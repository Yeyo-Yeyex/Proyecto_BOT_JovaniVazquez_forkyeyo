"""El banco: `banco`, un panel con dos mostradores (préstamos y cuentas).

`/banco [mostrador]` (y `.banco`) abre tu panel. Solo lo maneja quien lo abre; con `/` es
privado y con `.` se ve en el canal.

- 🏦 **Préstamos** (reglas en `bot.services.loans`, persistencia y cuotas con las hipotecas
  en `pisos`): lo que ve el banco de ti (ingresos, lo que ya pagas y lo que cabe en el
  35 %), tu tipo explicado punto a punto, lo más que te presta a cada plazo y tus
  préstamos. Eliges plazo y seguro, pides con 💳 (un formulario con el importe), miras la
  ficha (TIN, TAE, comisión, total) y firmas con ✍️. Desde tus préstamos se amortiza o se
  desiste (hasta la primera cuota).
- 📒 **Cuentas**: lo que ha cobrado la tienda (su caja, lo que paga el Monte), lo que ha
  prestado y cobrado el banco y lo que ha ganado el casino, desde el primer día.

El Monte de Piedad se mudó en la 9.0.0 al panel de `vender` (`bot.cogs.rastro`): «empeñar
en `vender`, pignorar en `banco`». Las cuentas siguen enseñando sus cifras
(`MonteRepository.stats`), y `.banco monte` abre los préstamos con un aviso.

Dinero: los préstamos van por `EconomyService.housing` (fiscalidad en su docstring). Cada
acción con dinero llama a `renta.remind` y su resultado lleva `renta.hint` con la mascota.
Lo pendiente sale en `saldo`: las cuotas, con las de `pisos`.

Logros: los préstamos y las cuentas en 🏦 Banco (`loan_*_stats`, `bank_panel_stats`). No
necesita permisos especiales ni intents.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.repositories.economy import (
    POKER_POT_ACCOUNT_ID,
    SHOP_ACCOUNT_ID,
    SLOTS_POT_ACCOUNT_ID,
    BalanceLimitError,
    InsufficientFundsError,
)
from bot.repositories.housing import HousingError, HousingRepository, LoanDesk
from bot.repositories.housing_finance import Loan
from bot.repositories.monte import MonteRepository
from bot.services import loans as lending
from bot.services.achievements import (
    StatDelta,
    bank_panel_stats,
    loan_closed_stats,
    loan_signed_stats,
)
from bot.services.economy import EconomyService, format_amount, parse_amount
from bot.services.housing_market import HousingService
from bot.services.pets import Event, Moment
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(32, 92, 160)
COLOR_BOOKS = discord.Color.from_rgb(90, 90, 110)
#: Lo que sigue respondiendo un panel.
PANEL_TIMEOUT = 900
#: Plazo que sale elegido al abrir el mostrador de préstamos.
DEFAULT_TERM = 48
#: Mostradores del panel: `(clave, etiqueta)`.
TABS = (("loans", "🏦 Préstamos"), ("books", "📒 Cuentas"))
#: Aviso de `.banco monte`, que abría el mostrador que se mudó a `vender`.
MONTE_MOVED = "El Monte de Piedad se ha mudado a `vender`."


#: Lo que devuelve una acción con dinero: el texto del resultado y sus logros (o `None`).
Outcome = tuple[str, StatDelta | None]


def _pct(rate: float) -> str:
    """`7,25 %`."""
    return f"{rate * 100:.2f}".replace(".", ",") + " %"


def _points(points: float) -> str:
    """`−0,5` o `+1,5` puntos del tipo."""
    sign = "−" if points < 0 else "+"
    return f"{sign}{abs(points) * 100:.2f}".rstrip("0").rstrip(",.").replace(".", ",")


def loan_line(loan: Loan) -> str:
    """Una línea de un préstamo activo."""
    insured = f" · 🛡️ {loan.covers_left} coberturas" if loan.insured else ""
    line = (
        f"**#{loan.id}** · quedan {format_amount(loan.balance)} de "
        f"{format_amount(loan.principal)} · {format_amount(loan.monthly)} al mes · "
        f"{loan.months_left} cuotas · {_pct(loan.rate)}{insured}"
    )
    if loan.unpaid_count:
        line += (
            f"\n> ⚠️ Debes {format_amount(loan.unpaid_amount)} de {loan.unpaid_count} cuotas: con "
            f"{lending.DEFAULT_INSTALLMENTS} seguidas, vence entero"
        )
    return line


def quote_lines(quote: lending.Quote) -> list[str]:
    """La ficha de una oferta de préstamo, como la información normalizada de la ley."""
    lines = [
        f"> 💶 Importe: **{format_amount(quote.principal)}** · recibes "
        f"{format_amount(quote.received)} (comisión de apertura {format_amount(quote.fee)})",
        f"> 📈 TIN **{_pct(quote.rate)}** · TAE **{_pct(quote.tae)}**",
        f"> 📅 {quote.months} cuotas de {format_amount(quote.payment)}",
    ]
    if quote.premium:
        lines.append(
            f"> 🛡️ Seguro de protección de pagos: {format_amount(quote.premium)} con cada cuota "
            f"más {format_amount(quote.premium_tax)} de IPS; paga hasta "
            f"{lending.INSURANCE_COVERS} cuotas que no puedas pagar"
        )
    lines += [
        f"> 💸 Cada mes: **{format_amount(quote.monthly)}** · total "
        f"{format_amount(quote.total)} · te cuesta {format_amount(quote.cost)}",
        "-# Un día del bot es un mes: la primera cuota se cobra la noche siguiente a la de "
        "hoy y hasta entonces puedes echarte atrás sin coste. Amortizar antes cuesta el 1 % "
        "(o el 0,5 % si queda menos de un año).",
    ]
    return lines


class Banco(commands.Cog):
    """Préstamos y cuentas del servidor."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        monte: MonteRepository,
        housing: HousingRepository,
        *,
        service: HousingService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.monte = monte
        self.housing = service or HousingService(housing, economy)
        self.clock = clock
        #: `(servidor, miembro)` con una operación de dinero en marcha: una a la vez.
        self.working: set[tuple[int, int]] = set()

    # -- Pantallas ---------------------------------------------------------------------

    async def loans_embed(
        self, guild_id: int, user_id: int, *, months: int, insured: bool
    ) -> tuple[discord.Embed, LoanDesk, int]:
        """El mostrador de préstamos: lo que ve el banco, tu tipo y tus préstamos.

        Returns:
            `(embed, lo que ve el banco, lo que te cabe al mes)`.
        """
        desk, income = await self.housing.loan_desk(guild_id, user_id)
        room = lending.room_for(income, desk.installments)
        factors = lending.profile_factors(desk.profile, insured=insured)
        rate = lending.loan_rate(desk.euribor, factors)
        embed = discord.Embed(title="🏦 Banco · Préstamos", color=COLOR)
        warnings = []
        if desk.asnef:
            warnings.append(
                "📛 **Estás en el fichero de morosos (ASNEF).** Ni préstamos ni hipotecas "
                "hasta que pagues las cuotas atrasadas y la deuda con el banco."
            )
        if desk.bank_debt:
            warnings.append(
                f"⚖️ Debes **{format_amount(desk.bank_debt)}** al banco: se cobra cada noche "
                "y se embarga de tus nóminas por tramos del salario mínimo."
            )
        embed.description = "\n".join(
            warnings
            or [
                "Préstamos personales para lo que quieras, también la entrada de un piso. "
                "Todas tus cuotas juntas (hipotecas y préstamos) no pueden pasar del 35 % de "
                "lo que ingresas."
            ]
        )
        embed.add_field(
            name="📋 Lo que ve el banco",
            value=(
                f"Ingresas **{format_amount(income)}** al día (nóminas de 30 días y el 70 % de "
                f"tus alquileres)\nYa pagas **{format_amount(desk.installments)}** al mes en "
                f"cuotas\nTe caben **{format_amount(room)}** al mes más"
            ),
            inline=False,
        )
        lines = [
            f"**{_pct(rate)} TIN** = Euríbor {_pct(desk.euribor)} + "
            f"{_pct(lending.LOAN_SPREAD)} de un cliente nuevo"
        ]
        lines += [f"{_points(f.points)} · {f.label}" for f in factors]
        if rate in (lending.MIN_RATE, lending.MAX_RATE):
            lines.append("-# El tipo no baja ni sube de ahí, haga lo que haga tu perfil.")
        embed.add_field(name="📈 Tu tipo hoy", value="\n".join(lines), inline=False)
        rows = []
        for term in lending.TERMS:
            most = lending.max_principal(room, rate, term, insured=insured)
            mark = "▶ " if term == months else ""
            rows.append(f"{mark}{term} meses: hasta **{format_amount(most)}**")
        embed.add_field(
            name="💳 Lo más que te presta" + (" (con seguro)" if insured else ""),
            value="\n".join(rows),
            inline=False,
        )
        embed.add_field(
            name=f"🧾 Tus préstamos ({len(desk.loans)}/{lending.MAX_LOANS})",
            value="\n".join(loan_line(loan) for loan in desk.loans)[:1024] or "Ninguno.",
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Comisión de apertura {_pct(lending.OPENING_FEE)} · seguro: −1 punto, prima "
                f"del {round(lending.INSURANCE_SHARE * 100)} % de la cuota · de "
                f"{format_amount(lending.MIN_PRINCIPAL)} a {format_amount(lending.MAX_PRINCIPAL)}"
            )
        )
        return embed, desk, room

    async def books_embed(self, guild_id: int) -> discord.Embed:
        """Las cuentas del servidor: la tienda, el banco y el casino, desde el primer día."""
        week_ago = self.clock() - 7 * 86_400
        till_reasons = (
            "tienda:venta",
            "tienda:devolucion",
            "monte:compra",
            "monte:prestamo",
            "monte:cobro",
        )
        shop = await self.economy.ledger_summary(
            guild_id, account=SHOP_ACCOUNT_ID, reasons=till_reasons
        )
        shop_week = await self.economy.ledger_summary(
            guild_id, account=SHOP_ACCOUNT_ID, reasons=("tienda:venta",), since=week_ago
        )
        balances = await self.economy.system_balances(
            guild_id, [SHOP_ACCOUNT_ID, SLOTS_POT_ACCOUNT_ID, POKER_POT_ACCOUNT_ID]
        )
        monte = await self.monte.stats(guild_id)
        bank = await self.housing.repository.bank_book(guild_id)
        member_reasons = (
            "pisos:hipoteca",
            "banco:cuota",
            "embargo:banco",
            "pisos:deuda_banco",
        )
        members = await self.economy.ledger_summary(guild_id, account=None, reasons=member_reasons)
        rows, withheld, refunded_day, refunded_renta, _first = await self.economy.casino_ledger(
            guild_id, None
        )

        def total(found: dict[str, tuple[int, int]], reason: str) -> int:
            return found.get(reason, (0, 0))[1]

        def count(found: dict[str, tuple[int, int]], reason: str) -> int:
            return found.get(reason, (0, 0))[0]

        embed = discord.Embed(
            title="📒 Las cuentas del servidor",
            color=COLOR_BOOKS,
            description="Desde el primer día. Lo que va a Hacienda sale en `hacienda`.",
        )
        embed.add_field(
            name=f"🏪 Tienda · caja {format_amount(balances[SHOP_ACCOUNT_ID])}",
            value=(
                f"Cobrado en ventas: **{format_amount(total(shop, 'tienda:venta'))}** "
                f"({count(shop, 'tienda:venta')} ventas, sin IGIC) · últimos 7 días "
                f"{format_amount(total(shop_week, 'tienda:venta'))}\n"
                f"Devoluciones: {format_amount(-total(shop, 'tienda:devolucion'))}\n"
                f"🏷️ El Monte ha comprado {monte.sales} cosas por "
                f"{format_amount(monte.bought)}, presta {format_amount(monte.lent)} en "
                f"{monte.pawned} papeletas, ha cobrado {format_amount(monte.interest)} de "
                f"intereses y se ha quedado {monte.forfeited} cosas por las que prestó "
                f"{format_amount(monte.forfeited_lent)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🏦 Banco",
            value=(
                f"🏠 {bank.mortgages} hipotecas · quedan {format_amount(bank.mortgage_balance)} · "
                f"cobrado en cuotas {format_amount(-total(members, 'pisos:hipoteca'))}\n"
                f"💳 {bank.loans} préstamos · quedan {format_amount(bank.loan_balance)} · "
                f"prestado desde el primer día {format_amount(bank.lent)}\n"
                f"Cobrado en cuotas de préstamos {format_amount(-total(members, 'banco:cuota'))}"
                f": intereses {format_amount(bank.loan_interest)}, primas de seguro "
                f"{format_amount(bank.premiums)}; comisiones {format_amount(bank.loan_fees)}\n"
                f"⚠️ Cuotas sin pagar {format_amount(bank.unpaid)} · deuda con el banco "
                f"{format_amount(bank.bank_debts)} · embargado de nóminas "
                f"{format_amount(-total(members, 'embargo:banco'))} · cobrado de deudas "
                f"{format_amount(-total(members, 'pisos:deuda_banco'))}\n"
                f"📛 {bank.asnef} en el fichero de morosos · {bank.defaults} impagos (préstamos "
                "vencidos e hipotecas ejecutadas)\n"
                "-# El banco no existe: lo que presta se crea y lo que cobra desaparece."
            ),
            inline=False,
        )
        staked = -sum(t for reason, _n, t in rows if reason.endswith(":apuesta"))
        paid = sum(t for reason, _n, t in rows if not reason.endswith(":apuesta"))
        plays = sum(n for reason, n, _t in rows if reason.endswith(":apuesta"))
        house = staked - paid
        tax = withheld - refunded_day - refunded_renta
        embed.add_field(
            name="🎰 Casino",
            value=(
                f"Apostado **{format_amount(staked)}** en {plays} jugadas · pagado "
                f"{format_amount(paid)}\n"
                + (
                    f"La casa gana **{format_amount(house)}**"
                    if house >= 0
                    else f"La casa pierde **{format_amount(-house)}**"
                )
                + f" · IRPF del juego neto para Hacienda {format_amount(tax)}\n"
                f"Botes: tragaperras {format_amount(balances[SLOTS_POT_ACCOUNT_ID])} · póker "
                f"{format_amount(balances[POKER_POT_ACCOUNT_ID])}\n"
                "-# Por juego, en `apuestas` (📒 Libro)."
            ),
            inline=False,
        )
        return embed

    # -- Préstamos ---------------------------------------------------------------------

    async def sign(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        quote: lending.Quote,
        insured: bool,
    ) -> Outcome:
        """Firma la oferta que se enseñó (ya con `ack`). Devuelve el resultado y sus logros."""
        user = interaction.user
        try:
            signed, balance = await self.housing.take_loan(
                guild_id, user.id, principal=quote.principal, months=quote.months,
                insured=insured, rate=quote.rate,
            )  # fmt: skip
        except HousingError as error:
            return str(error), None
        except BalanceLimitError:
            return "No te cabe más dinero en la cuenta.", None
        lines = [
            f"✍️ Firmado el préstamo **#{signed.loan.id}**: entran "
            f"**{format_amount(signed.quote.received)}** (te quedas con "
            f"{format_amount(balance)}). Pagarás {format_amount(signed.quote.monthly)} cada "
            f"noche durante {signed.quote.months} noches, desde mañana.",
        ]
        if (hint := await renta.hint(self.bot, guild_id, user.id, Moment(Event.MONEY))) is not None:
            lines.append(hint)
        desk, _income = await self.housing.loan_desk(guild_id, user.id)
        delta = loan_signed_stats(
            principal=signed.quote.principal, months=signed.quote.months, insured=insured,
            discount=desk.euribor + lending.LOAN_SPREAD - signed.quote.rate,
            open_loans=len(desk.loans),
        )  # fmt: skip
        return "\n".join(lines), delta

    async def close_loan(
        self, interaction: discord.Interaction, guild_id: int, loan_id: int, *, desist: bool
    ) -> Outcome:
        """Amortiza un préstamo o desiste (ya con `ack`). Devuelve el resultado y sus logros."""
        user = interaction.user
        action = self.housing.desist_loan if desist else self.housing.repay_loan
        try:
            closed, balance = await action(guild_id, user.id, loan_id)
        except HousingError as error:
            return str(error), None
        except InsufficientFundsError as error:
            return f"No te llega: tienes {format_amount(error.balance)}.", None
        if desist:
            text = (
                f"↩️ Te echas atrás del préstamo **#{loan_id}**: devuelves "
                f"{format_amount(closed.loan.principal)} y te devolvemos la comisión "
                f"({format_amount(closed.loan.fee)})."
            )
        else:
            text = (
                f"🧾 Préstamo **#{loan_id}** amortizado: pagas {format_amount(closed.paid)}"
                + (f", con {format_amount(closed.fee)} de compensación" if closed.fee else "")
                + "."
            )
        lines = [f"{text} Te quedas con {format_amount(balance)}."]
        if (hint := await renta.hint(self.bot, guild_id, user.id, Moment(Event.MONEY))) is not None:
            lines.append(hint)
        return "\n".join(lines), loan_closed_stats(status=closed.status)

    # -- Comandos ----------------------------------------------------------------------

    @app_commands.command(name="banco", description="Préstamos y las cuentas del servidor.")
    @app_commands.describe(mostrador="Por dónde abrir el banco (por defecto, préstamos).")
    @app_commands.choices(
        mostrador=[app_commands.Choice(name=label, value=key) for key, label in TABS]
    )
    @app_commands.guild_only()
    async def banco(
        self, interaction: discord.Interaction, mostrador: app_commands.Choice[str] | None = None
    ) -> None:
        """Abre tu panel del banco, privado. Leer tus préstamos puede tardar: se difiere."""
        assert interaction.guild is not None  # guild_only
        await interaction.response.defer(ephemeral=True, thinking=True)
        panel = BankPanel(
            self, interaction.guild.id, interaction.user, mostrador.value if mostrador else "loans"
        )
        try:
            kwargs = await panel.screen()
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo abrir el banco en %s", interaction.guild.id)
            await interaction.followup.send("El banco está cerrado ahora mismo.", ephemeral=True)
            return
        panel.message = await interaction.followup.send(ephemeral=True, wait=True, **kwargs)
        await panel.track_open(interaction.channel)

    @commands.command(name="banco")
    @commands.guild_only()
    async def banco_text(self, ctx: commands.Context, mostrador: str | None = None) -> None:
        """Versión de texto (`.banco [préstamos|cuentas]`): el panel sale en el canal.

        `.banco monte` abre los préstamos con el aviso de que el Monte está en `vender`.
        """
        assert ctx.guild is not None
        keys = {"prestamos": "loans", "préstamos": "loans", "cuentas": "books"}
        wanted = (mostrador or "").casefold()
        panel = BankPanel(self, ctx.guild.id, ctx.author, keys.get(wanted, "loans"))
        if wanted == "monte":
            panel.notice = MONTE_MOVED
        try:
            kwargs = await panel.screen()
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo abrir el banco en %s", ctx.guild.id)
            await ctx.reply("El banco está cerrado ahora mismo.", mention_author=False)
            return
        panel.message = await ctx.send(**kwargs)
        await panel.track_open(ctx.channel)


class AmountForm(ui.Modal):
    """💳 Cuánto se pide (por defecto, lo más que presta al plazo elegido)."""

    def __init__(self, panel: BankPanel, most: int) -> None:
        super().__init__(title=f"💳 Préstamo a {panel.months} meses"[:45])
        self.panel = panel
        self.most = most
        self.amount: ui.TextInput = ui.TextInput(
            label=f"Importe (hasta {format_amount(most)})"[:45],
            default=str(most),
            max_length=20,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.offer(interaction, self.amount.value, self.most)


class BankPanel(ui.View):
    """El panel de `banco` de un miembro: dos mostradores y sus botones."""

    def __init__(
        self, cog: Banco, guild_id: int, owner: discord.abc.User, tab: str = "loans"
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.tab = tab
        self.months = DEFAULT_TERM
        self.insured = False
        #: Préstamo elegido en la lista.
        self.selected_loan: int | None = None
        #: La oferta enseñada, mientras se decide si se firma.
        self.quote: lending.Quote | None = None
        #: Lo que se acaba de hacer, encima de la pantalla.
        self.notice: str | None = None
        self.message: discord.Message | None = None
        self.asnef = False
        #: Lo más que presta al plazo y seguro elegidos, de la última lectura.
        self.most: int | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Este banco no es tuyo: abre el tuyo con `banco`.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            pass

    async def track_open(self, channel: object) -> None:
        """Logros de abrir el banco (en el fichero de morosos) o de mirar las cuentas."""
        if self.asnef or self.tab == "books":
            await logros.track(
                self.cog.bot, self.guild_id, self.owner, channel,
                bank_panel_stats(asnef=self.asnef, books=self.tab == "books"),
            )  # fmt: skip

    # -- Pantallas ---------------------------------------------------------------------

    async def screen(self) -> dict[str, object]:
        """El embed y los botones del mostrador abierto, leídos de nuevo."""
        self.clear_items()
        for key, label in TABS:
            self.add_item(TabButton(key, label, active=key == self.tab))
        content = self.notice
        self.notice = None
        if self.tab == "books":
            embed = await self.cog.books_embed(self.guild_id)
        elif self.quote is not None:
            embed = discord.Embed(
                title="🏦 Banco · Tu oferta",
                color=COLOR,
                description="\n".join(quote_lines(self.quote)),
            )
            self.add_item(SignButton())
            self.add_item(BackButton())
        else:
            embed, desk, room = await self.cog.loans_embed(
                self.guild_id, self.owner.id, months=self.months, insured=self.insured
            )
            self.asnef = desk.asnef
            self._loan_items(desk, room)
        return {
            "content": content,
            "embed": embed,
            "view": self,
            "allowed_mentions": discord.AllowedMentions.none(),
        }

    def _loan_items(self, desk: LoanDesk, room: int) -> None:
        rate = lending.loan_rate(
            desk.euribor, lending.profile_factors(desk.profile, insured=self.insured)
        )
        self.most = lending.max_principal(room, rate, self.months, insured=self.insured)
        self.add_item(TermSelect(self.months))
        closed = (
            desk.asnef or self.most < lending.MIN_PRINCIPAL or len(desk.loans) >= lending.MAX_LOANS
        )
        self.add_item(InsuranceButton(self.insured))
        self.add_item(AskButton(disabled=closed))
        if desk.loans:
            self.add_item(LoanSelect(desk.loans, self.selected_loan))
            loan = next((x for x in desk.loans if x.id == self.selected_loan), None)
            if loan is not None:
                fee = lending.early_fee(loan.balance, loan.months_left)
                self.add_item(RepayButton(loan.id, loan.owed + fee))
                fresh = loan.months_left == loan.months and not loan.paid and not loan.unpaid_count
                self.add_item(DesistButton(loan.id, disabled=not fresh))

    async def refresh(self, interaction: discord.Interaction) -> None:
        """Vuelve a pintar el panel (ya con `ack`)."""
        try:
            kwargs = await self.screen()
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer el banco de %s", self.owner.id)
            await notify(interaction, "El banco no responde ahora mismo. Prueba en un rato.")
            return
        await edit(interaction, **kwargs)

    # -- Acciones con dinero -----------------------------------------------------------

    async def money(
        self, interaction: discord.Interaction, run: Callable[[], Awaitable[Outcome]]
    ) -> None:
        """Hace una operación de dinero (una a la vez por miembro) y repinta con el resultado.

        Los logros y el aviso de la Renta van después de contestar.
        """
        key = (self.guild_id, self.owner.id)
        if key in self.cog.working:
            await interaction.response.send_message(
                "Un momento, que el banco está con lo anterior.", ephemeral=True
            )
            return
        self.cog.working.add(key)
        await ack(interaction)
        try:
            self.notice, delta = await run()
        finally:
            self.cog.working.discard(key)
        await self.refresh(interaction)
        await renta.remind(self.cog.bot, interaction)
        if delta is not None:
            await logros.track(self.cog.bot, self.guild_id, self.owner, interaction.channel, delta)

    async def offer(self, interaction: discord.Interaction, text: str, most: int) -> None:
        """Enseña la ficha de lo pedido en el formulario."""
        await ack(interaction)
        try:
            amount = parse_amount(text, most)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if amount > most:
            await notify(interaction, f"Lo más que te presta a ese plazo es {format_amount(most)}.")
            return
        if amount < lending.MIN_PRINCIPAL:
            await notify(
                interaction, f"El banco presta desde {format_amount(lending.MIN_PRINCIPAL)}."
            )
            return
        desk, _income = await self.cog.housing.loan_desk(self.guild_id, self.owner.id)
        rate = lending.loan_rate(
            desk.euribor, lending.profile_factors(desk.profile, insured=self.insured)
        )
        self.quote = lending.quote(amount, self.months, rate, insured=self.insured)
        await self.refresh(interaction)


class TabButton(ui.Button["BankPanel"]):
    """Cambia de mostrador."""

    def __init__(self, key: str, label: str, *, active: bool) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary,
            custom_id=f"banco:tab:{key}",
            row=0,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.tab = self.key
        panel.quote = None
        await panel.refresh(interaction)
        if self.key == "books":
            await panel.track_open(interaction.channel)


class TermSelect(ui.Select["BankPanel"]):
    """Plazo del préstamo que se va a pedir."""

    def __init__(self, months: int) -> None:
        super().__init__(
            placeholder="Plazo",
            options=[
                discord.SelectOption(
                    label=f"{term} meses ({term} días)", value=str(term), default=term == months
                )
                for term in lending.TERMS
            ],  # fmt: skip
            custom_id="banco:plazo",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.months = int(self.values[0])
        await panel.refresh(interaction)


class InsuranceButton(ui.Button["BankPanel"]):
    """Con o sin el seguro de protección de pagos."""

    def __init__(self, insured: bool) -> None:
        super().__init__(
            label=f"Seguro: {'sí' if insured else 'no'}",
            emoji="🛡️",
            style=discord.ButtonStyle.success if insured else discord.ButtonStyle.secondary,
            custom_id="banco:seguro",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.insured = not panel.insured
        await panel.refresh(interaction)


class AskButton(ui.Button["BankPanel"]):
    """💳 Pedir: abre el formulario con el importe."""

    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="Pedir préstamo",
            emoji="💳",
            style=discord.ButtonStyle.primary,
            custom_id="banco:pedir",
            row=2,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        # El formulario tiene que ser la primera respuesta: lo más que presta se calcula
        # con lo que ya se leyó al pintar el mostrador (lo comprueba otra vez al firmar).
        most = panel.most
        if most is None or most < lending.MIN_PRINCIPAL:
            await interaction.response.send_message(
                "Vuelve a abrir el mostrador de préstamos.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AmountForm(panel, most))


class SignButton(ui.Button["BankPanel"]):
    """✍️ Firmar la oferta."""

    def __init__(self) -> None:
        super().__init__(
            label="Firmar", emoji="✍️", style=discord.ButtonStyle.success,
            custom_id="banco:firmar", row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None and panel.quote is not None
        quote, insured = panel.quote, panel.insured
        panel.quote = None
        await panel.money(
            interaction, lambda: panel.cog.sign(interaction, panel.guild_id, quote, insured)
        )


class BackButton(ui.Button["BankPanel"]):
    """↩️ Volver al mostrador sin firmar."""

    def __init__(self) -> None:
        super().__init__(
            label="Volver", emoji="↩️", style=discord.ButtonStyle.secondary,
            custom_id="banco:volver", row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.quote = None
        await panel.refresh(interaction)


class LoanSelect(ui.Select["BankPanel"]):
    """Elige uno de tus préstamos para amortizarlo o desistir."""

    def __init__(self, loans: tuple[Loan, ...], selected: int | None) -> None:
        super().__init__(
            placeholder="Tus préstamos",
            options=[
                discord.SelectOption(
                    label=f"#{loan.id} · quedan {format_amount(loan.balance)}"[:100],
                    description=f"{loan.months_left} cuotas de {format_amount(loan.monthly)}"[:100],
                    value=str(loan.id),
                    default=loan.id == selected,
                )
                for loan in loans
            ],
            custom_id="banco:prestamo",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.selected_loan = int(self.values[0])
        await panel.refresh(interaction)


class RepayButton(ui.Button["BankPanel"]):
    """🧾 Amortizar el préstamo elegido entero."""

    def __init__(self, loan_id: int, total: int) -> None:
        super().__init__(
            label=f"Amortizar #{loan_id} ({format_amount(total)})"[:80],
            emoji="🧾",
            style=discord.ButtonStyle.primary,
            custom_id="banco:amortizar",
            row=4,
        )
        self.loan_id = loan_id

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        panel.selected_loan = None
        await panel.money(
            interaction,
            lambda: panel.cog.close_loan(interaction, panel.guild_id, self.loan_id, desist=False),
        )


class DesistButton(ui.Button["BankPanel"]):
    """↩️ Desistir del préstamo elegido (solo antes de la primera cuota)."""

    def __init__(self, loan_id: int, *, disabled: bool) -> None:
        super().__init__(
            label=f"Desistir #{loan_id}",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id="banco:desistir",
            row=4,
            disabled=disabled,
        )
        self.loan_id = loan_id

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        panel.selected_loan = None
        await panel.money(
            interaction,
            lambda: panel.cog.close_loan(interaction, panel.guild_id, self.loan_id, desist=True),
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, el Monte (sus cifras en las cuentas) y el banco de
    `pisos`."""
    await bot.add_cog(Banco(bot, bot.economy, bot.monte, bot.housing))
