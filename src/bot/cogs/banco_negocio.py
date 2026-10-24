"""El mostrador 🏪 Negocio de `banco`: préstamos a nombre de tu negocio de `pala`.

No es un cog: son la pantalla y los botones del mostrador, que pinta `BankPanel` (en
`bot.cogs.banco`) cuando está abierto, como `bot.cogs.banco_anticipos`. Diseño y normas en
`docs/negocios.md` («Préstamos al negocio»); las reglas, en `bot.services.loans`
(`business_rate`, `MAX_BUSINESS_LOANS`) y `HousingRepository.take_business_loan_in`, y el
dinero, en `BusinessService.take_loan` y `repay_loan`.

- Sin negocio, dice dónde se monta.
- Lo que ve el banco: de un autónomo, lo que ingresas (con lo que deja el negocio) y lo que
  te cabe en el 35 %; de una sociedad, lo que deja ella la semana pasada más lo tuyo, que la
  avalas. Tu tipo (Euríbor + el diferencial de una pyme + tu perfil), lo más que presta a cada
  plazo y los préstamos del negocio.
- Eliges plazo, 💳 Pedir abre un formulario con el importe y enseña la ficha (TIN, TAE,
  comisión, cuota, total y la letra pequeña); ✍️ Firmar o ↩️ Volver. Uno de la lista se
  amortiza con 🧾 (con la compensación). No hay desistimiento: no es crédito al consumo.
- `pala` lo abre desde la portada del negocio (🏦 Banco, `banco.open_business`).

Dinero: `EconomyService.business` (fiscalidad en `BusinessService.take_loan`). Firmar y
amortizar llaman a `renta.remind` (en `BankPanel.money`) y el resultado lleva `renta.hint`
con la mascota; los logros van en 🏪 Negocio (`business_loan_*_stats`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.cogs import renta
from bot.repositories.housing import BusinessLoanDesk
from bot.repositories.housing_finance import Loan
from bot.services import loans as lending
from bot.services.achievements import (
    StatDelta,
    business_loan_closed_stats,
    business_loan_signed_stats,
)
from bot.services.business import Business
from bot.services.economy import format_amount, parse_amount
from bot.services.negocio import BusinessError
from bot.services.pets import Event, Moment
from bot.utils.interactions import ack, notify

if TYPE_CHECKING:
    from bot.cogs.banco import BankPanel

#: Clave del mostrador en `banco.TABS`.
TAB = "business"
COLOR = discord.Color.from_rgb(40, 130, 90)
#: Plazo que sale elegido al abrir el mostrador.
DEFAULT_TERM = 48


def _pct(rate: float) -> str:
    """`7,25 %`."""
    return f"{rate * 100:.2f}".replace(".", ",") + " %"


def _points(points: float) -> str:
    """`−0,5` o `+1,5` puntos del tipo."""
    sign = "−" if points < 0 else "+"
    return f"{sign}{abs(points) * 100:.2f}".rstrip("0").rstrip(",.").replace(".", ",")


def title_of(business: Business) -> str:
    """El nombre con el que firma: el del negocio, con «S.L.» si es de una sociedad."""
    return f"{business.name}, S.L." if business.is_company else business.name


def loan_line(loan: Loan) -> str:
    """Una línea de un préstamo del negocio."""
    line = (
        f"**#{loan.id}** · quedan {format_amount(loan.balance)} de "
        f"{format_amount(loan.principal)} · {format_amount(loan.monthly)} al mes · "
        f"{loan.months_left} cuotas · {_pct(loan.rate)}"
    )
    if loan.unpaid_count:
        line += (
            f"\n> ⚠️ Debe {format_amount(loan.unpaid_amount)} de {loan.unpaid_count} cuotas: con "
            f"{lending.DEFAULT_INSTALLMENTS} seguidas, vence entero"
        )
    return line


def offer_lines(quote: lending.Quote, business: Business) -> list[str]:
    """La ficha de una oferta, con la letra pequeña."""
    where = (
        f"A nombre de **{title_of(business)}**: entra en su caja y lo paga ella"
        if business.is_company
        else f"Para **{title_of(business)}**, a tu nombre: como autónomo, el negocio no tiene "
        "caja propia y entra en tu monedero"
    )
    who = (
        f"Si la sociedad no paga {lending.DEFAULT_INSTALLMENTS} cuotas, el banco te lo reclama a "
        "ti, que la avalas, y ella pasa a debértelo."
        if business.is_company
        else f"Con {lending.DEFAULT_INSTALLMENTS} cuotas sin pagar vence entero y lo debes tú."
    )
    return [
        f"> 🏪 {where}",
        f"> 💶 Importe: **{format_amount(quote.principal)}** · recibe "
        f"{format_amount(quote.received)} (comisión de apertura {format_amount(quote.fee)})",
        f"> 📈 TIN fijo **{_pct(quote.rate)}** · TAE **{_pct(quote.tae)}**",
        f"> 📅 {quote.months} cuotas de **{format_amount(quote.payment)}** · total "
        f"{format_amount(quote.total)} · cuesta {format_amount(quote.cost)}",
        "-# Un día del bot es un mes: la primera cuota se cobra la noche siguiente a la de hoy, "
        "en la noche del negocio y después del local. Los intereses y la comisión son gasto del "
        "negocio. No es crédito al consumo: no hay desistimiento ni seguro de pagos. Amortizar "
        "antes cuesta el 1 % (o el 0,5 % si queda menos de un año). Con "
        f"{lending.ASNEF_INSTALLMENTS} cuotas sin pagar entras en el fichero de morosos. {who}",
    ]


async def _desk(panel: BankPanel) -> tuple[Business, BusinessLoanDesk] | None:
    service = panel.cog.negocio
    if service is None:
        return None
    found = await service.loan_desk(panel.guild_id, panel.owner.id, panel.business_id)
    if found is None and panel.business_id is not None:
        # Ese local ya cerró: el primero que quede.
        panel.business_id = None
        found = await service.loan_desk(panel.guild_id, panel.owner.id)
    return found


async def screen(panel: BankPanel) -> discord.Embed:
    """El mostrador del negocio del panel, leído de nuevo, con sus botones."""
    found = await _desk(panel)
    if found is None:
        panel.business_most = None
        return discord.Embed(
            title="🏪 Banco · Negocio",
            color=COLOR,
            description=(
                "Aquí se piden préstamos a nombre de tu negocio de `pala`, y no tienes ninguno. "
                "Se monta desde el inicio de `pala` (🏪 Montar negocio)."
            ),
        )
    business, desk = found
    panel.business_id = business.id
    if panel.business_offer is not None:
        panel.add_item(SignButton())
        panel.add_item(BackButton())
        return discord.Embed(
            title="🏪 Banco · Tu oferta",
            color=COLOR,
            description="\n".join(offer_lines(panel.business_offer, business)),
        )
    embed = discord.Embed(title=f"🏪 Banco · {title_of(business)}", color=COLOR)
    warnings = []
    if desk.owner.asnef:
        warnings.append(
            "📛 **Estás en el fichero de morosos (ASNEF).** El banco no presta a tu negocio "
            "hasta que pagues lo que debes."
        )
    if desk.owner.bank_debt:
        warnings.append(
            f"⚖️ Debes **{format_amount(desk.owner.bank_debt)}** al banco: se cobra cada noche "
            "y se embarga de lo que cobres."
        )
    if business.is_company:
        intro = (
            "Préstamos a nombre de tu sociedad: entran en su caja y los paga ella cada noche, "
            "después del local y de Hacienda. El banco pide tu aval de socio. Sus intereses son "
            "gasto en Sociedades."
        )
    else:
        intro = (
            "Préstamos para tu negocio. Como autónomo lo firmas tú: entra en tu monedero, "
            "respondes con todo lo tuyo y su cuota cuenta en tu 35 %. Sus intereses son gasto "
            "del negocio en tu IRPF."
        )
    embed.description = "\n".join(warnings or [intro])
    if desk.corporate:
        seen = (
            f"Tu sociedad dejó **{format_amount(desk.company_income or 0)}** al día la semana "
            "pasada (antes de impuestos, amortizaciones e intereses)\n"
            f"Tú ingresas {format_amount(max(0, desk.income))} al día y la avalas\n"
        )
    else:
        seen = (
            f"Ingresas **{format_amount(desk.income)}** al día (nóminas y lo que deja el negocio "
            "en 30 días, y el 70 % de tus alquileres)\n"
        )
    embed.add_field(
        name="📋 Lo que ve el banco",
        value=(
            f"{seen}Ya pagas **{format_amount(desk.owner.installments)}** al mes en cuotas\n"
            f"Caben **{format_amount(desk.room)}** al mes más (el 35 %)"
        ),
        inline=False,
    )
    lines = [
        f"**{_pct(desk.rate)} TIN** = Euríbor {_pct(desk.owner.euribor)} + "
        f"{_pct(lending.BUSINESS_SPREAD)} de una pyme"
    ]
    lines += [f"{_points(f.points)} · {f.label}" for f in desk.factors]
    embed.add_field(name="📈 El tipo hoy", value="\n".join(lines), inline=False)
    rows = []
    for term in lending.TERMS:
        mark = "▶ " if term == panel.business_months else ""
        rows.append(f"{mark}{term} meses: hasta **{format_amount(desk.most(term))}**")
    embed.add_field(name="💳 Lo más que presta", value="\n".join(rows), inline=False)
    embed.add_field(
        name=f"🧾 Préstamos del negocio ({len(desk.loans)}/{lending.MAX_BUSINESS_LOANS})",
        value="\n".join(loan_line(loan) for loan in desk.loans)[:1_024] or "Ninguno.",
        inline=False,
    )
    embed.set_footer(
        text=(
            f"Comisión de apertura {_pct(lending.OPENING_FEE)} · de "
            f"{format_amount(lending.MIN_PRINCIPAL)} a {format_amount(lending.MAX_PRINCIPAL)} · no "
            f"cuentan en el tope de {lending.MAX_LOANS} préstamos personales"
        )
    )
    panel.asnef = desk.owner.asnef
    _items(panel, desk)
    service = panel.cog.negocio
    owned = await service.owned(panel.guild_id, panel.owner.id) if service is not None else []
    if len(owned) > 1:
        # Una cadena: cada local tiene sus préstamos (dos como mucho).
        ids = [b.id for b in owned]
        nxt = owned[(ids.index(business.id) + 1) % len(owned)] if business.id in ids else owned[0]
        panel.add_item(NextLocalButton(nxt.id, nxt.name))
    return embed


def _items(panel: BankPanel, desk: BusinessLoanDesk) -> None:
    """El plazo, 💳 Pedir y los préstamos del negocio."""
    panel.business_most = desk.most(panel.business_months)
    closed = (
        desk.owner.asnef
        or panel.business_most < lending.MIN_PRINCIPAL
        or len(desk.loans) >= lending.MAX_BUSINESS_LOANS
    )
    panel.add_item(TermSelect(panel.business_months))
    panel.add_item(AskButton(disabled=closed))
    if desk.loans:
        ids = [loan.id for loan in desk.loans]
        if panel.selected_business_loan not in ids:
            panel.selected_business_loan = ids[0]
        loan = next(x for x in desk.loans if x.id == panel.selected_business_loan)
        fee = lending.early_fee(loan.balance, loan.months_left)
        panel.add_item(LoanSelect(desk.loans, panel.selected_business_loan))
        panel.add_item(RepayButton(loan.id, loan.owed + fee))


async def offer(panel: BankPanel, interaction: discord.Interaction, text: str, most: int) -> None:
    """Enseña la ficha de lo pedido en el formulario."""
    await ack(interaction)
    try:
        amount = parse_amount(text, most)
    except ValueError as error:
        await notify(interaction, str(error))
        return
    if amount > most:
        await notify(interaction, f"Lo más que presta a ese plazo es {format_amount(most)}.")
        return
    if amount < lending.MIN_PRINCIPAL:
        await notify(interaction, f"El banco presta desde {format_amount(lending.MIN_PRINCIPAL)}.")
        return
    found = await _desk(panel)
    if found is None:
        await notify(interaction, "Ya no tienes negocio.")
        return
    _business, desk = found
    panel.business_offer = lending.quote(amount, panel.business_months, desk.rate, insured=False)
    await panel.refresh(interaction)


async def sign(panel: BankPanel, interaction: discord.Interaction) -> tuple[str, StatDelta | None]:
    """Firma la oferta de la ficha (ya con `ack`). Devuelve el resultado y sus logros."""
    quote = panel.business_offer
    panel.business_offer = None
    service = panel.cog.negocio
    if quote is None or service is None:
        return "Esa oferta ya no vale.", None
    guild_id, user = panel.guild_id, interaction.user
    try:
        business, loan, signed, paid = await service.take_loan(
            guild_id, user.id, principal=quote.principal, months=quote.months, rate=quote.rate,
            biz=panel.business_id,
        )  # fmt: skip
    except BusinessError as error:
        return str(error), None
    where = (
        f"en la caja de la sociedad (queda {format_amount(paid.company)})"
        if business.is_company
        else f"en tu monedero (te quedas con {format_amount(paid.balance)})"
    )
    lines = [
        f"✍️ Firmado el préstamo **#{loan.id}** a nombre de **{title_of(business)}**: entran "
        f"**{format_amount(signed.received)}** {where}. El negocio paga "
        f"{format_amount(signed.monthly)} cada noche durante {signed.months} noches, desde "
        "mañana.",
    ]
    hint = await renta.hint(panel.cog.bot, guild_id, user.id, Moment(Event.MONEY))
    if hint is not None:
        lines.append(hint)
    found = await _desk(panel)
    delta = business_loan_signed_stats(
        principal=signed.principal, months=signed.months, corporate=business.is_company,
        open_loans=len(found[1].loans) if found else 1,
    )  # fmt: skip
    return "\n".join(lines), delta


async def repay(
    panel: BankPanel, interaction: discord.Interaction, loan_id: int
) -> tuple[str, StatDelta | None]:
    """Amortiza un préstamo del negocio (ya con `ack`). Devuelve el resultado y sus logros."""
    service = panel.cog.negocio
    if service is None:
        return "El banco está cerrado ahora mismo.", None
    guild_id, user = panel.guild_id, interaction.user
    try:
        business, loan, fee, paid = await service.repay_loan(
            guild_id, user.id, loan_id, biz=panel.business_id
        )
    except BusinessError as error:
        return str(error), None
    left = (
        f"En la caja de la sociedad quedan {format_amount(paid.company)}."
        if business.is_company
        else f"Te quedas con {format_amount(paid.balance)}."
    )
    lines = [
        f"🧾 Préstamo **#{loan_id}** del negocio amortizado: paga "
        f"{format_amount(loan.owed + fee)}"
        + (f", con {format_amount(fee)} de compensación" if fee else "")
        + f". {left}"
    ]
    hint = await renta.hint(panel.cog.bot, guild_id, user.id, Moment(Event.MONEY))
    if hint is not None:
        lines.append(hint)
    return "\n".join(lines), business_loan_closed_stats()


class AmountForm(ui.Modal):
    """💳 Cuánto se pide (por defecto, lo más que presta al plazo elegido)."""

    def __init__(self, panel: BankPanel, most: int) -> None:
        super().__init__(title=f"💳 Préstamo al negocio a {panel.business_months} meses"[:45])
        self.panel = panel
        self.most = most
        self.amount: ui.TextInput = ui.TextInput(
            label=f"Importe (hasta {format_amount(most)})"[:45], default=str(most), max_length=20
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await offer(self.panel, interaction, self.amount.value, self.most)


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
            ],
            custom_id="banco:negocio_plazo",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.business_months = int(self.values[0])
        await panel.refresh(interaction)


class AskButton(ui.Button["BankPanel"]):
    """💳 Pedir: abre el formulario con el importe."""

    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="Pedir préstamo", emoji="💳", style=discord.ButtonStyle.primary,
            custom_id="banco:negocio_pedir", row=2, disabled=disabled,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        # El formulario tiene que ser la primera respuesta: lo más que presta se calcula con
        # lo leído al pintar el mostrador (se comprueba otra vez al firmar).
        most = panel.business_most
        if most is None or most < lending.MIN_PRINCIPAL:
            await interaction.response.send_message(
                "Vuelve a abrir el mostrador del negocio.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AmountForm(panel, most))


class NextLocalButton(ui.Button["BankPanel"]):
    """🏪 Pasa al siguiente local de la cadena (cada uno pide sus préstamos)."""

    def __init__(self, business_id: int, name: str) -> None:
        super().__init__(
            label=f"Ir a {name}"[:80], emoji="🏪", style=discord.ButtonStyle.secondary,
            custom_id="banco:negocio_local", row=2,
        )  # fmt: skip
        self.business_id = business_id

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.business_id = self.business_id
        panel.business_offer = None
        panel.selected_business_loan = None
        await panel.refresh(interaction)


class SignButton(ui.Button["BankPanel"]):
    """✍️ Firmar la oferta."""

    def __init__(self) -> None:
        super().__init__(
            label="Firmar", emoji="✍️", style=discord.ButtonStyle.success,
            custom_id="banco:negocio_firmar", row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        if panel.business_offer is None:
            await interaction.response.send_message("Esa oferta ya no vale.", ephemeral=True)
            return
        await panel.money(interaction, lambda: sign(panel, interaction))


class BackButton(ui.Button["BankPanel"]):
    """↩️ Volver al mostrador sin firmar."""

    def __init__(self) -> None:
        super().__init__(
            label="Volver", emoji="↩️", style=discord.ButtonStyle.secondary,
            custom_id="banco:negocio_volver", row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.business_offer = None
        await panel.refresh(interaction)


class LoanSelect(ui.Select["BankPanel"]):
    """Elige uno de los préstamos del negocio para amortizarlo."""

    def __init__(self, loans: tuple[Loan, ...], selected: int | None) -> None:
        super().__init__(
            placeholder="Préstamos del negocio",
            options=[
                discord.SelectOption(
                    label=f"#{loan.id} · quedan {format_amount(loan.owed)}"[:100],
                    description=f"{loan.months_left} cuotas de {format_amount(loan.monthly)}"[:100],
                    value=str(loan.id),
                    default=loan.id == selected,
                )
                for loan in loans
            ],
            custom_id="banco:negocio_lista",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.selected_business_loan = int(self.values[0])
        await panel.refresh(interaction)


class RepayButton(ui.Button["BankPanel"]):
    """🧾 Amortizar entero el préstamo elegido, desde la caja del negocio."""

    def __init__(self, loan_id: int, total: int) -> None:
        super().__init__(
            label=f"Amortizar #{loan_id} ({format_amount(total)})"[:80], emoji="🧾",
            style=discord.ButtonStyle.primary, custom_id="banco:negocio_amortizar", row=4,
        )  # fmt: skip
        self.loan_id = loan_id

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        panel.selected_business_loan = None
        await panel.money(interaction, lambda: repay(panel, interaction, self.loan_id))
