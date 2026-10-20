"""El banco: `banco`, un panel con tres mostradores (préstamos, Monte de Piedad y cuentas).

`/banco [mostrador]` (y `.banco`) abre tu panel. Solo lo maneja quien lo abre; con `/` es
privado y con `.` se ve en el canal.

- 🏦 **Préstamos** (reglas en `bot.services.loans`, persistencia y cuotas con las hipotecas
  en `pisos`): lo que ve el banco de ti (ingresos, lo que ya pagas y lo que cabe en el
  35 %), tu tipo explicado punto a punto, lo más que te presta a cada plazo y tus
  préstamos. Eliges plazo y seguro, pides con 💳 (un formulario con el importe), miras la
  ficha (TIN, TAE, comisión, total) y firmas con ✍️. Desde tus préstamos se amortiza o se
  desiste (hasta la primera cuota).
- 🏷️ **Monte de Piedad** (`bot.services.monte`, `bot.repositories.monte`): lo que acepta
  de tu mochila y lo que da por cada cosa. Eliges un objeto y lo vendes (💰) o lo empeñas
  (🏷️); tus papeletas se desempeñan con su botón. Una tarea mira cada minuto las
  papeletas vencidas y el objeto se queda en el Monte.
- 📒 **Cuentas**: lo que ha cobrado la tienda (su caja, lo que paga el Monte), lo que ha
  prestado y cobrado el banco y lo que ha ganado el casino, desde el primer día.

Dinero: vender, empeñar y desempeñar van por `EconomyService.monte` y los préstamos por
`EconomyService.housing` (fiscalidad en sus docstrings). Cada acción con dinero llama a
`renta.remind` y su resultado lleva `renta.hint` con la mascota. Lo pendiente sale en
`saldo`: las papeletas, con `pending` de este módulo; las cuotas, con las de `pisos`.

Logros: el Monte en 🧺 Rastro y los préstamos y las cuentas en 🏦 Banco (`monte_*_stats`,
`loan_*_stats`, `bank_panel_stats`). No necesita permisos especiales ni intents.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.cogs import shop as tienda
from bot.repositories.economy import (
    POKER_POT_ACCOUNT_ID,
    SHOP_ACCOUNT_ID,
    SLOTS_POT_ACCOUNT_ID,
    BalanceLimitError,
    InsufficientFundsError,
    TillEmptyError,
)
from bot.repositories.housing import HousingError, HousingRepository, LoanDesk
from bot.repositories.housing_finance import Loan
from bot.repositories.monte import Appraisal, MonteRepository, Ticket
from bot.services import loans as lending
from bot.services.achievements import (
    StatDelta,
    bank_panel_stats,
    loan_closed_stats,
    loan_signed_stats,
    monte_forfeit_stats,
    monte_pawn_stats,
    monte_redeem_stats,
    monte_sale_stats,
)
from bot.services.economy import EconomyService, format_amount, parse_amount
from bot.services.housing_market import HousingService
from bot.services.monte import (
    MAX_TICKETS,
    MONTE_NAME,
    PAWN_FEE,
    PAWN_SECONDS,
    PAWN_SHARE,
    SELL_SHARE,
    MonteError,
)
from bot.services.pending import Pending, Side
from bot.services.pets import Event, Moment
from bot.services.shop_catalog import is_title
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(32, 92, 160)
COLOR_MONTE = discord.Color.from_rgb(150, 110, 40)
COLOR_BOOKS = discord.Color.from_rgb(90, 90, 110)
#: Cada cuánto se miran las papeletas vencidas.
FORFEIT_EVERY = 60
#: Lo que sigue respondiendo un panel.
PANEL_TIMEOUT = 900
#: Plazo que sale elegido al abrir el mostrador de préstamos.
DEFAULT_TERM = 48
#: Mostradores del panel: `(clave, etiqueta)`.
TABS = (("loans", "🏦 Préstamos"), ("monte", f"🏷️ {MONTE_NAME}"), ("books", "📒 Cuentas"))


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


def ticket_line(ticket: Ticket) -> str:
    """Una línea de una papeleta abierta."""
    return (
        f"**#{ticket.id}** {ticket.label} · devuelves **{format_amount(ticket.repay)}** · "
        f"vence <t:{int(ticket.due_at)}:R>"
    )


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
    """Préstamos, Monte de Piedad y cuentas del servidor."""

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
        #: Papeletas abiertas de todos los servidores, por id (para `saldo` y la tarea).
        self.tickets: dict[int, Ticket] = {}
        #: `(servidor, miembro)` con una operación de dinero en marcha: una a la vez.
        self.working: set[tuple[int, int]] = set()

    async def cog_load(self) -> None:
        """Crea las tablas del Monte, carga las papeletas y arranca la tarea."""
        await self.monte.initialize()
        self.tickets = {t.id: t for t in await self.monte.open_tickets()}
        self._forfeiter.start()

    async def cog_unload(self) -> None:
        """Para la tarea de las papeletas vencidas."""
        self._forfeiter.cancel()

    # -- Papeletas vencidas ------------------------------------------------------------

    @tasks.loop(seconds=FORFEIT_EVERY)
    async def _forfeiter(self) -> None:
        await self.forfeit_due()

    @_forfeiter.before_loop
    async def _before_forfeiter(self) -> None:
        await self.bot.wait_until_ready()

    async def forfeit_due(self) -> list[Ticket]:
        """Cierra las papeletas vencidas (el objeto se queda en el Monte). Nunca lanza."""
        try:
            lost = await self.monte.forfeit_due(self.clock())
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron cerrar las papeletas vencidas del Monte")
            return []
        for ticket in lost:
            self.tickets.pop(ticket.id, None)
            logros.note(self.bot, ticket.guild_id, ticket.user_id, monte_forfeit_stats())
        return lost

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

    async def monte_embed(
        self, guild_id: int, user_id: int, selected: int | None
    ) -> tuple[discord.Embed, list[Appraisal], list[Ticket]]:
        """El mostrador del Monte: tus papeletas y lo que da por tu mochila."""
        found = await self.monte.appraisals(guild_id, user_id, self.clock())
        tickets = sorted(
            (t for t in self.tickets.values() if t.guild_id == guild_id and t.user_id == user_id),
            key=lambda t: t.due_at,
        )
        till = (await self.economy.system_balances(guild_id, [SHOP_ACCOUNT_ID]))[SHOP_ACCOUNT_ID]
        accepted = [a for a in found if a.offer is not None]
        embed = discord.Embed(
            title=f"🏷️ {MONTE_NAME}",
            color=COLOR_MONTE,
            description=(
                f"Compra al **{round(SELL_SHARE * 100)} %** de lo que pagaste en la tienda (sin "
                f"IGIC) y presta al **{round(PAWN_SHARE * 100)} %**: devuelves el préstamo más "
                f"un {round(PAWN_FEE * 100)} % en {PAWN_SECONDS // 86_400} días y lo recuperas; "
                "si no, se lo queda el Monte. Paga con la caja de la tienda "
                f"({format_amount(till)}). No acepta lo que no salió de la tienda ni lo "
                "numerado: eso va al Rastro."
            ),
        )
        embed.add_field(
            name=f"🎟️ Tus papeletas ({len(tickets)}/{MAX_TICKETS})",
            value="\n".join(ticket_line(t) for t in tickets)[:1024] or "Ninguna.",
            inline=False,
        )
        refused = len(found) - len(accepted)
        value = f"El Monte acepta **{len(accepted)}** cosas de tu mochila."
        if refused:
            value += f" Otras {refused} no (cajas, regalos, premios o numeradas)."
        if len(accepted) > 25:
            value += " En la lista salen las 25 primeras por orden de nombre."
        choice = next((a for a in accepted if a.entry.id == selected), None)
        if choice is not None and choice.offer is not None:
            deal = choice.offer
            value += (
                f"\n\n**{choice.entry.emoji} {choice.entry.name}** (te costó "
                f"{format_amount(deal.paid)}):\n> 💰 Te lo compra por "
                f"**{format_amount(deal.sale)}**\n> 🏷️ Te presta **{format_amount(deal.loan)}** "
                "y devuelves "
                f"**{format_amount(deal.repay)}**"
            )
        embed.add_field(name="🎒 Tu mochila", value=value, inline=False)
        return embed, accepted, tickets

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

    # -- Monte -------------------------------------------------------------------------

    async def sell(self, interaction: discord.Interaction, guild_id: int, entry_id: int) -> Outcome:
        """Vende un objeto al Monte (ya con `ack`). Devuelve el resultado y sus logros."""
        user = interaction.user
        try:
            sale, balance = await self.economy.monte(
                guild_id, self.monte.sell(guild_id, user.id, entry_id, self.clock())
            )
        except MonteError as error:
            return str(error), None
        except TillEmptyError:
            return "La caja de la tienda no llega para pagarte eso ahora mismo.", None
        except BalanceLimitError:
            return "No te cabe más dinero en la cuenta.", None
        lines = [
            f"💰 El Monte te compra **{sale.label}** por **{format_amount(sale.amount)}**. "
            f"Te quedas con {format_amount(balance)}.",
        ]
        if (hint := await renta.hint(self.bot, guild_id, user.id, Moment(Event.TRADE))) is not None:
            lines.append(hint)
        await tienda.refresh_worn(self.bot, guild_id, user.id)
        return "\n".join(lines), monte_sale_stats(
            amount=sale.amount, title=is_title(sale.catalog_key)
        )

    async def pawn(self, interaction: discord.Interaction, guild_id: int, entry_id: int) -> Outcome:
        """Empeña un objeto (ya con `ack`). Devuelve el resultado y sus logros."""
        user = interaction.user
        try:
            ticket, balance = await self.economy.monte(
                guild_id, self.monte.pawn(guild_id, user.id, entry_id, self.clock())
            )
        except MonteError as error:
            return str(error), None
        except TillEmptyError:
            return "La caja de la tienda no llega para prestarte eso ahora mismo.", None
        except BalanceLimitError:
            return "No te cabe más dinero en la cuenta.", None
        self.tickets[ticket.id] = ticket
        lines = [
            f"🏷️ Empeñas **{ticket.label}**: te prestan **{format_amount(ticket.loan)}** "
            f"(te quedas con {format_amount(balance)}). Devuelve "
            f"**{format_amount(ticket.repay)}** antes de <t:{int(ticket.due_at)}:f> para "
            "recuperarlo; si no, se lo queda el Monte.",
        ]
        if (hint := await renta.hint(self.bot, guild_id, user.id, Moment(Event.TRADE))) is not None:
            lines.append(hint)
        await tienda.refresh_worn(self.bot, guild_id, user.id)
        open_tickets = sum(
            1 for t in self.tickets.values() if t.guild_id == guild_id and t.user_id == user.id
        )
        return "\n".join(lines), monte_pawn_stats(loan=ticket.loan, open_tickets=open_tickets)

    async def redeem(
        self, interaction: discord.Interaction, guild_id: int, ticket_id: int
    ) -> Outcome:
        """Desempeña una papeleta (ya con `ack`). Devuelve el resultado y sus logros."""
        user = interaction.user
        now = self.clock()
        try:
            ticket, balance = await self.economy.monte(
                guild_id, self.monte.redeem(guild_id, user.id, ticket_id, now)
            )
        except MonteError as error:
            # Vencida o perdida: la memoria se pone al día (solo con las papeletas propias).
            if (ticket := self.tickets.get(ticket_id)) is not None and ticket.user_id == user.id:
                self.tickets.pop(ticket_id, None)
            return str(error), None
        except InsufficientFundsError as error:
            return (
                f"No te llega: tienes {format_amount(error.balance)} y para desempeñarlo hace "
                "falta lo prestado más el interés."
            ), None
        self.tickets.pop(ticket.id, None)
        lines = [
            f"🎟️ Recuperas **{ticket.label}** por **{format_amount(ticket.repay)}**. Te "
            f"quedas con {format_amount(balance)}.",
        ]
        if (hint := await renta.hint(self.bot, guild_id, user.id, Moment(Event.TRADE))) is not None:
            lines.append(hint)
        return "\n".join(lines), monte_redeem_stats(seconds_left=ticket.due_at - now)

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

    @app_commands.command(
        name="banco", description="Préstamos, el Monte de Piedad y las cuentas del servidor."
    )
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
        """Versión de texto (`.banco [préstamos|monte|cuentas]`): el panel sale en el canal."""
        assert ctx.guild is not None
        keys = {"prestamos": "loans", "préstamos": "loans", "monte": "monte", "cuentas": "books"}
        tab = keys.get((mostrador or "").casefold(), "loans")
        panel = BankPanel(self, ctx.guild.id, ctx.author, tab)
        try:
            kwargs = await panel.screen()
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo abrir el banco en %s", ctx.guild.id)
            await ctx.reply("El banco está cerrado ahora mismo.", mention_author=False)
            return
        panel.message = await ctx.send(**kwargs)
        await panel.track_open(ctx.channel)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra el Monte del servidor que deja el bot (los préstamos los borra `pisos`)."""
        for ticket_id in [i for i, t in self.tickets.items() if t.guild_id == guild.id]:
            self.tickets.pop(ticket_id, None)
        await self.monte.delete_guild_data(guild.id)


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
    """El panel de `banco` de un miembro: tres mostradores y sus botones."""

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
        #: Objeto elegido en el Monte y préstamo elegido en la lista.
        self.selected_entry: int | None = None
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
        if self.tab == "monte":
            embed, accepted, tickets = await self.cog.monte_embed(
                self.guild_id, self.owner.id, self.selected_entry
            )
            self._monte_items(accepted, tickets)
        elif self.tab == "books":
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

    def _monte_items(self, accepted: list[Appraisal], tickets: list[Ticket]) -> None:
        if accepted:
            self.add_item(ItemSelect(accepted[:25], self.selected_entry))
        choice = next((a for a in accepted if a.entry.id == self.selected_entry), None)
        offer = choice.offer if choice is not None else None
        self.add_item(SellButton(offer.sale if offer else None))
        self.add_item(PawnButton(offer.loan if offer else None, len(tickets) >= MAX_TICKETS))
        for ticket in tickets[:MAX_TICKETS]:
            self.add_item(RedeemButton(ticket.id, ticket.repay))

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


class ItemSelect(ui.Select["BankPanel"]):
    """Elige un objeto de la mochila para el Monte."""

    def __init__(self, accepted: list[Appraisal], selected: int | None) -> None:
        options = []
        for found in accepted:
            assert found.offer is not None
            entry = found.entry
            options.append(
                discord.SelectOption(
                    label=f"{entry.emoji} {entry.name}"[:100],
                    description=(
                        f"Vender {format_amount(found.offer.sale)} · empeñar "
                        f"{format_amount(found.offer.loan)}"
                    )[:100],
                    value=str(entry.id),
                    default=entry.id == selected,
                )
            )
        super().__init__(
            placeholder="Elige algo de tu mochila", options=options, custom_id="banco:objeto",
            row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.selected_entry = int(self.values[0])
        await panel.refresh(interaction)


class SellButton(ui.Button["BankPanel"]):
    """💰 Vender al Monte lo elegido."""

    def __init__(self, amount: int | None) -> None:
        label = f"Vender por {format_amount(amount)}" if amount is not None else "Vender"
        super().__init__(
            label=label[:80], emoji="💰", style=discord.ButtonStyle.danger,
            custom_id="banco:vender", row=2, disabled=amount is None,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None and panel.selected_entry is not None
        entry, panel.selected_entry = panel.selected_entry, None
        await panel.money(interaction, lambda: panel.cog.sell(interaction, panel.guild_id, entry))


class PawnButton(ui.Button["BankPanel"]):
    """🏷️ Empeñar lo elegido."""

    def __init__(self, amount: int | None, full: bool) -> None:
        label = f"Empeñar por {format_amount(amount)}" if amount is not None else "Empeñar"
        super().__init__(
            label=label[:80], emoji="🏷️", style=discord.ButtonStyle.primary,
            custom_id="banco:empenar", row=2, disabled=amount is None or full,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None and panel.selected_entry is not None
        entry, panel.selected_entry = panel.selected_entry, None
        await panel.money(interaction, lambda: panel.cog.pawn(interaction, panel.guild_id, entry))


class RedeemButton(ui.Button["BankPanel"]):
    """🎟️ Desempeñar una papeleta."""

    def __init__(self, ticket_id: int, repay: int) -> None:
        super().__init__(
            label=f"#{ticket_id} ({format_amount(repay)})"[:80],
            emoji="🎟️",
            style=discord.ButtonStyle.success,
            custom_id=f"banco:desempenar:{ticket_id}",
            row=3,
        )
        self.ticket_id = ticket_id

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await panel.money(
            interaction, lambda: panel.cog.redeem(interaction, panel.guild_id, self.ticket_id)
        )


# -- Funciones puente ---------------------------------------------------------------


def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Las papeletas abiertas de alguien en el Monte (para `saldo`), o `[]`.

    Sale de las papeletas que el cog tiene en memoria: no toca la base de datos.
    """
    cog = find_cog(bot, Banco)
    if cog is None:
        return []
    return [
        Pending(
            Side.PAY,
            "monte_ticket",
            "🏷️",
            f"Papeleta del Monte ({ticket.label}): devuelve {format_amount(ticket.repay)} "
            "para recuperarlo",
            ticket.repay,
            ticket.due_at,
        )  # fmt: skip
        for ticket in cog.tickets.values()
        if ticket.guild_id == guild_id and ticket.user_id == user_id
    ]


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, el Monte y el banco de `pisos`."""
    await bot.add_cog(Banco(bot, bot.economy, bot.monte, bot.housing))
