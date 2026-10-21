"""El mostrador 💸 Anticipos de `banco`: el banco te adelanta las rentas de tus alquileres.

No es un cog: son la pantalla y los botones del mostrador, que pinta `BankPanel` (en
`bot.cogs.banco`) cuando está abierto, como `bot.cogs.banco_garantias`. Diseño, cifras y
normas en `docs/garantias.md` (parte 2); las reglas, en `bot.services.loans` (`advance_*`),
y la firma, en `HousingRepository.advance`.

- Lo que ve el banco (el tipo de hoy y lo que ya pagas en cuotas) y tus inmuebles: tu parte
  de la renta de cada uno (la cuota), los meses que le quedan al contrato y cuánto te adelanta,
  o por qué no (vacío, vacacional, tu casa, el inquilino debe, contrato corto…).
- Eliges inmueble y plazo (solo los que caben en el contrato) y 💸 Adelantar enseña la ficha:
  lo que recibes, la comisión, TIN, TAE, la cuota, el total y la letra pequeña. ✍️ Firmar o
  ↩️ Volver.
- Tus anticipos, con ↩️ Desistir (antes de la primera cuota, Ley 16/2011) y 🧾 Cancelar
  (amortizar con la compensación del art. 30), que usan lo mismo que los préstamos.
- `pisos` abre este mostrador con un inmueble elegido (💸 Adelantar rentas en 🧰 Gestionar,
  `banco.open_advances`).

Dinero: `EconomyService.housing` (fiscalidad en `HousingRepository.advance`). Firmar,
desistir y cancelar llaman a `renta.remind` (en `BankPanel.money`) y el resultado lleva
`renta.hint` con la mascota; los logros van en 🏦 Banco (`advance_*_stats`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.cogs import renta
from bot.repositories.economy import BalanceLimitError, InsufficientFundsError
from bot.repositories.housing import AdvanceDesk, HousingError, RentShare
from bot.repositories.housing_finance import Loan
from bot.services import loans as lending
from bot.services.achievements import StatDelta, advance_closed_stats, advance_signed_stats
from bot.services.economy import format_amount
from bot.services.pets import Event, Moment
from bot.utils.interactions import ack

if TYPE_CHECKING:
    from bot.cogs.banco import BankPanel

#: Clave del mostrador en `banco.TABS`.
TAB = "advances"
COLOR = discord.Color.from_rgb(170, 120, 40)
#: Plazo que sale elegido al abrir el mostrador (o el más largo que quepa en el contrato).
DEFAULT_TERM = max(lending.ADVANCE_TERMS)


def _pct(rate: float) -> str:
    """`7,25 %`."""
    return f"{rate * 100:.2f}".replace(".", ",") + " %"


def terms_for(found: RentShare) -> list[int]:
    """Los plazos que caben en lo que le queda al contrato."""
    return [t for t in lending.ADVANCE_TERMS if t <= found.months_left]


def share_line(found: RentShare, rate: float) -> str:
    """Una línea de un inmueble: la cuota, el contrato y cuánto adelanta, o por qué no."""
    share = "" if found.shares == 100 else f" ({found.shares} %)"
    line = f"**{found.title}**{share}\n> "
    if found.blocked is not None:
        return line + f"ahora no: {found.blocked}"
    term = max(terms_for(found))
    quote = lending.advance_quote(found.payment, term, rate)
    return line + (
        f"{format_amount(found.payment)} al mes · al contrato le quedan {found.months_left} "
        f"meses · a {term} te adelanta **{format_amount(quote.received)}**"
    )


def advance_line(loan: Loan, titles: dict[str, str]) -> str:
    """Una línea de un anticipo vivo."""
    title = titles.get(loan.listing or "", "un inmueble que ya no es tuyo")
    line = (
        f"**#{loan.id}** {title} · quedan {format_amount(loan.owed)} · "
        f"{format_amount(loan.payment)} al mes · {loan.months_left} cuotas · {_pct(loan.rate)}"
    )
    if loan.unpaid_count:
        line += (
            f"\n> ⚠️ Debes {format_amount(loan.unpaid_amount)} de {loan.unpaid_count} cuotas: "
            f"con {lending.DEFAULT_INSTALLMENTS} seguidas, vence entero"
        )
    return line


def offer_lines(quote: lending.AdvanceQuote, title: str) -> list[str]:
    """La ficha de una oferta de anticipo, con la letra pequeña."""
    return [
        f"> 🏠 Rentas de **{title}**",
        f"> 💶 Te adelanta **{format_amount(quote.principal)}** · recibes "
        f"**{format_amount(quote.received)}** (comisión de apertura {format_amount(quote.fee)})",
        f"> 📈 TIN fijo **{_pct(quote.rate)}** · TAE **{_pct(quote.tae)}**",
        f"> 📅 {quote.months} cuotas de **{format_amount(quote.payment)}** (tu parte de la renta "
        f"de hoy) · total {format_amount(quote.total)} · te cuesta {format_amount(quote.cost)}",
        "-# Un día del bot es un mes: el banco cobra la cuota cada noche desde mañana, después "
        "de la renta. Si el inquilino se va o no paga, la pagas tú; sin pagar, cuenta como un "
        f"préstamo (fichero de morosos con {lending.ASNEF_INSTALLMENTS} cuotas, vence con "
        f"{lending.DEFAULT_INSTALLMENTS}). Mientras esté vivo no puedes vender el inmueble. La "
        "renta sigue tributando cada noche en tu IRPF y el coste del anticipo no se deduce. "
        "Hasta la primera cuota puedes echarte atrás sin coste; cancelarlo antes cuesta el "
        f"{_pct(lending.EARLY_FEE_SHORT)} de lo que quede.",
    ]


async def screen(panel: BankPanel) -> discord.Embed:
    """El mostrador de anticipos del panel, leído de nuevo, con sus botones."""
    if panel.advance_offer is not None:
        quote, _key, title = panel.advance_offer
        panel.add_item(SignButton())
        panel.add_item(BackButton())
        return discord.Embed(
            title="💸 Banco · Tu anticipo",
            color=COLOR,
            description="\n".join(offer_lines(quote, title)),
        )
    desk = await panel.cog.housing.advance_desk(panel.guild_id, panel.owner.id)
    embed = discord.Embed(
        title="💸 Banco · Anticipos",
        color=COLOR,
        description=(
            "**Anticipo de rentas:** el banco te adelanta hoy 3, 6 o 12 meses de tu parte de "
            "la renta de un inmueble alquilado y se la cobra cada noche. No hace falta que "
            "quepa en tu 35 % (la paga el inquilino), pero cuenta para lo siguiente que pidas."
        ),
    )
    if desk.asnef:
        embed.description = (
            "📛 **Estás en el fichero de morosos (ASNEF).** El banco no adelanta rentas hasta "
            "que pagues lo que debes."
        )
    embed.add_field(
        name="📋 Lo que ve el banco",
        value=(
            f"Ya pagas **{format_amount(desk.installments)}** al mes en cuotas\nTipo de hoy: "
            f"**{_pct(desk.rate)} TIN** fijo (Euríbor {_pct(desk.euribor)} + "
            f"{_pct(lending.ADVANCE_SPREAD)})"
        ),
        inline=False,
    )
    ranked = sorted(desk.shares, key=lambda x: (x.blocked is not None, -x.payment))
    text = ""
    for line in (share_line(found, desk.rate) for found in ranked):
        if len(text) + len(line) + 1 > 1_000:
            text += "\n-# Y más: en el desplegable salen los 25 que más rentan."
            break
        text = f"{text}\n{line}" if text else line
    embed.add_field(
        name=f"🏠 Tus inmuebles ({len(desk.shares)})",
        value=text or "No tienes nada en `pisos`.",
        inline=False,
    )
    titles = {x.key: x.title for x in desk.shares}
    embed.add_field(
        name=f"🧾 Tus anticipos ({len(desk.advances)})",
        value="\n".join(advance_line(loan, titles) for loan in desk.advances)[:1_024] or "Ninguno.",
        inline=False,
    )
    embed.set_footer(
        text=(
            f"Comisión de apertura {_pct(lending.ADVANCE_FEE)} · uno por inmueble · no cuenta "
            f"en el tope de {lending.MAX_LOANS} préstamos"
        )
    )
    _items(panel, desk, ranked)
    return embed


def _items(panel: BankPanel, desk: AdvanceDesk, ranked: list[RentShare]) -> None:
    """Los desplegables de inmueble y plazo, 💸 Adelantar y tus anticipos."""
    usable = [x for x in ranked if x.blocked is None] if not desk.asnef else []
    if panel.advance_key not in {x.key for x in usable}:
        panel.advance_key = usable[0].key if usable else None
    chosen = next((x for x in usable if x.key == panel.advance_key), None)
    if chosen is not None:
        fits = terms_for(chosen)
        if panel.advance_months not in fits:
            panel.advance_months = max(fits)
        panel.add_item(ShareSelect(usable[:25], panel.advance_key))
        panel.add_item(TermSelect(fits, panel.advance_months))
    panel.add_item(AskButton(disabled=chosen is None))
    if desk.advances:
        ids = [loan.id for loan in desk.advances]
        if panel.selected_advance not in ids:
            panel.selected_advance = ids[0]
        loan = next(x for x in desk.advances if x.id == panel.selected_advance)
        fresh = loan.months_left == loan.months and not loan.paid and not loan.unpaid_count
        fee = lending.early_fee(loan.balance, loan.months_left)
        panel.add_item(DesistButton(loan.id, disabled=not fresh))
        panel.add_item(RepayButton(loan.id, loan.owed + fee))
        titles = {x.key: x.title for x in desk.shares}
        panel.add_item(AdvanceSelect(desk.advances, panel.selected_advance, titles))


async def sign(panel: BankPanel, interaction: discord.Interaction) -> tuple[str, StatDelta | None]:
    """Firma el anticipo de la ficha (ya con `ack`). Devuelve el resultado y sus logros."""
    assert panel.advance_offer is not None
    quote, key, title = panel.advance_offer
    panel.advance_offer = None
    cog, guild_id, user = panel.cog, panel.guild_id, interaction.user
    try:
        signed, balance = await cog.housing.advance(
            guild_id, user.id, key, months=quote.months, rate=quote.rate
        )
    except HousingError as error:
        return str(error), None
    except BalanceLimitError:
        return "No te cabe más dinero en la cuenta.", None
    lines = [
        f"✍️ Firmado el anticipo **#{signed.loan.id}** de las rentas de **{title}**: entran "
        f"**{format_amount(signed.quote.received)}** (te quedas con {format_amount(balance)}). "
        f"El banco se cobra {format_amount(signed.quote.payment)} cada noche durante "
        f"{signed.quote.months} noches, desde mañana.",
    ]
    if (hint := await renta.hint(cog.bot, guild_id, user.id, Moment(Event.MONEY))) is not None:
        lines.append(hint)
    desk = await cog.housing.advance_desk(guild_id, user.id)
    delta = advance_signed_stats(
        principal=signed.quote.principal, months=signed.quote.months,
        open_count=len(desk.advances),
    )  # fmt: skip
    return "\n".join(lines), delta


async def close(
    panel: BankPanel, interaction: discord.Interaction, loan_id: int, *, desist: bool
) -> tuple[str, StatDelta | None]:
    """Desiste de un anticipo o lo cancela (ya con `ack`). Devuelve el resultado y sus logros."""
    cog, guild_id, user = panel.cog, panel.guild_id, interaction.user
    action = cog.housing.desist_loan if desist else cog.housing.repay_loan
    try:
        closed, balance = await action(guild_id, user.id, loan_id)
    except HousingError as error:
        return str(error), None
    except InsufficientFundsError as error:
        return f"No te llega: tienes {format_amount(error.balance)}.", None
    if desist:
        text = (
            f"↩️ Te echas atrás del anticipo **#{loan_id}**: devuelves "
            f"{format_amount(closed.loan.principal)} y te devolvemos la comisión "
            f"({format_amount(closed.loan.fee)}). Las rentas vuelven a ser tuyas."
        )
    else:
        text = (
            f"🧾 Anticipo **#{loan_id}** cancelado: pagas {format_amount(closed.paid)}"
            + (f", con {format_amount(closed.fee)} de compensación" if closed.fee else "")
            + ". Las rentas vuelven a ser tuyas."
        )
    lines = [f"{text} Te quedas con {format_amount(balance)}."]
    if (hint := await renta.hint(cog.bot, guild_id, user.id, Moment(Event.MONEY))) is not None:
        lines.append(hint)
    return "\n".join(lines), advance_closed_stats(status=closed.status)


class ShareSelect(ui.Select["BankPanel"]):
    """Elige el inmueble cuyas rentas se adelantan."""

    def __init__(self, usable: list[RentShare], selected: str | None) -> None:
        super().__init__(
            placeholder="Elige el inmueble",
            options=[
                discord.SelectOption(
                    label=found.title[:100],
                    description=(
                        f"{format_amount(found.payment)} al mes · contrato: "
                        f"{found.months_left} meses"
                    )[:100],
                    value=found.key,
                    default=found.key == selected,
                )
                for found in usable
            ],
            custom_id="banco:anticipo",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.advance_key = self.values[0]
        await panel.refresh(interaction)


class TermSelect(ui.Select["BankPanel"]):
    """Cuántos meses se adelantan (solo los que caben en el contrato)."""

    def __init__(self, terms: list[int], months: int) -> None:
        super().__init__(
            placeholder="Meses",
            options=[
                discord.SelectOption(
                    label=f"{term} meses de renta ({term} noches)",
                    value=str(term),
                    default=term == months,
                )
                for term in terms
            ],
            custom_id="banco:anticipo_plazo",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.advance_months = int(self.values[0])
        await panel.refresh(interaction)


class AskButton(ui.Button["BankPanel"]):
    """💸 Adelantar: enseña la ficha (el importe sale de la cuota y el plazo)."""

    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="Adelantar", emoji="💸", style=discord.ButtonStyle.primary,
            custom_id="banco:anticipo_pedir", row=3, disabled=disabled,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        desk = await panel.cog.housing.advance_desk(panel.guild_id, panel.owner.id)
        found = next((x for x in desk.shares if x.key == panel.advance_key), None)
        if found is None or found.blocked is not None or desk.asnef:
            panel.notice = "Ese inmueble ya no vale para un anticipo."
        elif panel.advance_months not in terms_for(found):
            panel.notice = "Ese plazo ya no cabe en el contrato."
        else:
            quote = lending.advance_quote(found.payment, panel.advance_months, desk.rate)
            panel.advance_offer = (quote, found.key, found.title)
        await panel.refresh(interaction)


class SignButton(ui.Button["BankPanel"]):
    """✍️ Firmar el anticipo."""

    def __init__(self) -> None:
        super().__init__(
            label="Firmar", emoji="✍️", style=discord.ButtonStyle.success,
            custom_id="banco:anticipo_firmar", row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        if panel.advance_offer is None:
            await interaction.response.send_message("Esa oferta ya no vale.", ephemeral=True)
            return
        await panel.money(interaction, lambda: sign(panel, interaction))


class BackButton(ui.Button["BankPanel"]):
    """↩️ Volver al mostrador sin firmar."""

    def __init__(self) -> None:
        super().__init__(
            label="Volver", emoji="↩️", style=discord.ButtonStyle.secondary,
            custom_id="banco:anticipo_volver", row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.advance_offer = None
        await panel.refresh(interaction)


class DesistButton(ui.Button["BankPanel"]):
    """↩️ Desistir del anticipo elegido (solo antes de la primera cuota)."""

    def __init__(self, loan_id: int, *, disabled: bool) -> None:
        super().__init__(
            label=f"Desistir #{loan_id}", emoji="↩️", style=discord.ButtonStyle.secondary,
            custom_id="banco:anticipo_desistir", row=3, disabled=disabled,
        )  # fmt: skip
        self.loan_id = loan_id

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await panel.money(interaction, lambda: close(panel, interaction, self.loan_id, desist=True))


class RepayButton(ui.Button["BankPanel"]):
    """🧾 Cancelar el anticipo elegido: lo que queda más la compensación."""

    def __init__(self, loan_id: int, total: int) -> None:
        super().__init__(
            label=f"Cancelar #{loan_id} ({format_amount(total)})"[:80], emoji="🧾",
            style=discord.ButtonStyle.primary, custom_id="banco:anticipo_cancelar", row=3,
        )  # fmt: skip
        self.loan_id = loan_id

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await panel.money(
            interaction, lambda: close(panel, interaction, self.loan_id, desist=False)
        )


class AdvanceSelect(ui.Select["BankPanel"]):
    """Elige uno de tus anticipos para desistir o cancelarlo."""

    def __init__(self, advances: tuple[Loan, ...], selected: int | None, titles: dict) -> None:
        super().__init__(
            placeholder="Tus anticipos",
            options=[
                discord.SelectOption(
                    label=f"#{loan.id} · quedan {format_amount(loan.owed)}"[:100],
                    description=titles.get(loan.listing or "", loan.listing or "")[:100] or None,
                    value=str(loan.id),
                    default=loan.id == selected,
                )
                for loan in advances[:25]
            ],
            custom_id="banco:anticipo_lista",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.selected_advance = int(self.values[0])
        await panel.refresh(interaction)
