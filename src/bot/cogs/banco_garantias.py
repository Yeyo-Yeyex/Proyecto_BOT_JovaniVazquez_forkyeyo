"""El mostrador 🔐 Garantías de `banco`: rehipotecar lo que ya tienes en `pisos`.

No es un cog: son la pantalla y los botones del mostrador, que pinta `BankPanel` (en
`bot.cogs.banco`) cuando está abierto. Diseño entero, cifras y normas en
`docs/garantias.md`; las reglas, en `bot.services.loans` (`remortgage_*`), y la firma, en
`HousingRepository.remortgage`.

- Lo que ve el banco de ti (lo que ingresas, lo que ya pagas y lo que te cabe en el 35 %), el
  tipo de hoy y tus inmuebles: lo que vale cada uno (la tasación), lo que ya debes de él y lo
  más que te presta (el 80 % si es vivienda, el 60 % si no, menos lo debido).
- Eliges inmueble y plazo, pides con 🏦 (un formulario con el importe), miras la ficha
  (tasación con su IGIC, comisión, lo que recibes, TIN, TAE, cuota y total) y firmas con ✍️.
- Tus rehipotecas, con lo que queda. Se amortizan como cualquier hipoteca, desde la ficha del
  inmueble en `pisos`, que además tiene 🏦 Rehipotecar para abrir este mostrador con el
  inmueble ya elegido (`banco.open_guarantees`).

Dinero: `EconomyService.housing` (tratamiento fiscal en su docstring). Firmar llama a
`renta.remind` y el resultado lleva `renta.hint` con la mascota; los logros van en 🏦 Banco
(`remortgage_signed_stats`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.cogs import renta
from bot.repositories.economy import BalanceLimitError
from bot.repositories.housing import Collateral, HousingError, RemortgageDesk
from bot.services import loans as lending
from bot.services.achievements import StatDelta, remortgage_signed_stats
from bot.services.economy import format_amount, parse_amount
from bot.services.pets import Event, Moment
from bot.utils.interactions import ack, notify

if TYPE_CHECKING:
    from bot.cogs.banco import BankPanel

#: Clave del mostrador en `banco.TABS`.
TAB = "guarantees"
COLOR = discord.Color.from_rgb(70, 120, 90)
#: Plazo que sale elegido al abrir el mostrador.
DEFAULT_TERM = max(lending.REMORTGAGE_TERMS)


def _pct(rate: float) -> str:
    """`7,25 %`."""
    return f"{rate * 100:.2f}".replace(".", ",") + " %"


def collateral_line(found: Collateral) -> str:
    """Una línea de un inmueble tuyo: lo que vale, lo que debes y lo que te presta."""
    share = "" if found.shares == 100 else f" ({found.shares} %)"
    home = " · 🏠 vives aquí" if found.habitual else ""
    line = (
        f"**{found.title}**{share}{home}\n> Vale {format_amount(found.value)} · debes "
        f"{format_amount(found.owed)} · "
    )
    if found.blocked is not None:
        return line + f"ahora no: {found.blocked}"
    if found.room < lending.REMORTGAGE_MIN:
        return line + "no queda margen"
    return line + f"te presta hasta **{format_amount(found.room)}**"


def remortgage_lines(quote: lending.RemortgageQuote, title: str) -> list[str]:
    """La ficha de una oferta de rehipoteca, como la información normalizada de la ley."""
    return [
        f"> 🏠 Sobre **{title}**",
        f"> 💶 Importe: **{format_amount(quote.principal)}** · recibes "
        f"**{format_amount(quote.received)}**",
        f"> 🧾 Tasación {format_amount(quote.appraisal)} + {format_amount(quote.appraisal_igic)} "
        f"de IGIC · comisión de apertura {format_amount(quote.fee)}",
        f"> 📈 TIN fijo **{_pct(quote.rate)}** · TAE **{_pct(quote.tae)}**",
        f"> 📅 {quote.months} cuotas de **{format_amount(quote.payment)}** · total "
        f"{format_amount(quote.total)} · te cuesta {format_amount(quote.cost)}",
        "-# Un día del bot es un mes: la primera cuota se cobra esta noche. Es una hipoteca: "
        "no hay desistimiento, se amortiza sin comisión desde `pisos` y, con 12 cuotas o el 3 % "
        "del capital sin pagar, el banco saca tu parte a subasta y cobra todas tus hipotecas de "
        "ese inmueble. Sus intereses no se restan de la renta del piso en el IRPF.",
    ]


async def screen(panel: BankPanel) -> discord.Embed:
    """El mostrador de garantías del panel, leído de nuevo, con sus botones."""
    cog = panel.cog
    if panel.remortgage_offer is not None:
        quote, _key, title = panel.remortgage_offer
        panel.add_item(SignButton())
        panel.add_item(BackButton())
        return discord.Embed(
            title="🔐 Banco · Tu rehipoteca",
            color=COLOR,
            description="\n".join(remortgage_lines(quote, title)),
        )
    desk, income = await cog.housing.remortgage_desk(panel.guild_id, panel.owner.id)
    room = lending.room_for(income, desk.installments)
    embed = discord.Embed(
        title="🔐 Banco · Garantías",
        color=COLOR,
        description=(
            "**Rehipotecar:** una hipoteca nueva sobre lo que ya tienes en `pisos`. El banco "
            "lo tasa a lo que vale hoy y presta hasta el 80 % si es vivienda y el 60 % si no, "
            "menos lo que ya debes de él. La cuota cuenta en tu 35 %."
        ),
    )
    if desk.asnef:
        embed.description = (
            "📛 **Estás en el fichero de morosos (ASNEF).** El banco no rehipoteca hasta que "
            "pagues lo que debes."
        )
    embed.add_field(
        name="📋 Lo que ve el banco",
        value=(
            f"Ingresas **{format_amount(income)}** al día · ya pagas "
            f"**{format_amount(desk.installments)}** al mes en cuotas · te caben "
            f"**{format_amount(room)}** al mes más\nTipo de hoy: **{_pct(desk.rate)} TIN** "
            f"fijo (Euríbor {_pct(desk.euribor)} + {_pct(lending.REMORTGAGE_SPREAD)})"
        ),
        inline=False,
    )
    ranked = sorted(desk.collaterals, key=lambda c: (c.blocked is not None, -c.room))
    lines = [collateral_line(found) for found in ranked]
    text = ""
    for line in lines:
        if len(text) + len(line) + 1 > 1_000:
            text += "\n-# Y más: en el desplegable salen los 25 con más margen."
            break
        text = f"{text}\n{line}" if text else line
    embed.add_field(
        name=f"🏠 Tus inmuebles ({len(desk.collaterals)})",
        value=text or "No tienes nada en `pisos` que rehipotecar.",
        inline=False,
    )
    titles = {c.key: c.title for c in desk.collaterals}
    embed.add_field(
        name=f"🧾 Tus rehipotecas ({len(desk.remortgages)})",
        value="\n".join(
            f"**#{m.id}** {titles.get(m.listing, m.listing)} · quedan {format_amount(m.owed)} · "
            f"{format_amount(m.payment)} al mes · {m.months_left} cuotas · {_pct(m.rate)}"
            for m in desk.remortgages
        )[:1_024]
        or "Ninguna.",
        inline=False,
    )
    embed.set_footer(
        text=(
            f"Tasación: el {_pct(lending.APPRAISAL_RATE)} de lo que vale (mínimo "
            f"{format_amount(lending.APPRAISAL_MIN)}) más IGIC · comisión de apertura "
            f"{_pct(lending.REMORTGAGE_FEE)} · desde {format_amount(lending.REMORTGAGE_MIN)}"
        )
    )
    _items(panel, desk, ranked, room)
    return embed


def _items(panel: BankPanel, desk: RemortgageDesk, ranked: list[Collateral], room: int) -> None:
    """El desplegable de inmuebles, el de plazos y el botón de pedir."""
    usable = [c for c in ranked if c.blocked is None and c.room >= lending.REMORTGAGE_MIN]
    if panel.collateral not in {c.key for c in usable}:
        panel.collateral = usable[0].key if usable else None
    chosen = next((c for c in usable if c.key == panel.collateral), None)
    panel.remortgage_most = (
        lending.max_remortgage(chosen.room, room, desk.rate, panel.remortgage_months)
        if chosen is not None and not desk.asnef
        else 0
    )
    panel.remortgage_room = chosen.room if chosen is not None else 0
    if usable:
        panel.add_item(CollateralSelect(usable[:25], panel.collateral))
    panel.add_item(TermSelect(panel.remortgage_months))
    panel.add_item(AskButton(disabled=panel.remortgage_most < lending.REMORTGAGE_MIN))


async def sign(panel: BankPanel, interaction: discord.Interaction) -> tuple[str, StatDelta | None]:
    """Firma la rehipoteca de la ficha (ya con `ack`). Devuelve el resultado y sus logros."""
    assert panel.remortgage_offer is not None
    quote, key, title = panel.remortgage_offer
    room = panel.remortgage_room
    panel.remortgage_offer = None
    cog, guild_id = panel.cog, panel.guild_id
    user = interaction.user
    try:
        signed, balance = await cog.housing.remortgage(
            guild_id, user.id, key, principal=quote.principal, months=quote.months,
            rate=quote.rate,
        )  # fmt: skip
    except HousingError as error:
        return str(error), None
    except BalanceLimitError:
        return "No te cabe más dinero en la cuenta.", None
    lines = [
        f"✍️ Firmada la rehipoteca **#{signed.mortgage.id}** sobre **{title}**: entran "
        f"**{format_amount(signed.quote.received)}** (te quedas con {format_amount(balance)}). "
        f"Pagarás {format_amount(signed.quote.payment)} cada noche durante "
        f"{signed.quote.months} noches.",
    ]
    if (hint := await renta.hint(cog.bot, guild_id, user.id, Moment(Event.MONEY))) is not None:
        lines.append(hint)
    desk = await cog.housing.repository.remortgage_desk(guild_id, user.id)
    found = next((c for c in desk.collaterals if c.key == key), None)
    delta = remortgage_signed_stats(
        principal=signed.quote.principal, months=signed.quote.months, room=room,
        habitual=found is not None and found.habitual,
        dwelling=found is None or found.dwelling, open_count=len(desk.remortgages),
    )  # fmt: skip
    return "\n".join(lines), delta


class AmountForm(ui.Modal):
    """🏦 Cuánto se pide (por defecto, lo más que presta)."""

    def __init__(self, panel: BankPanel, most: int) -> None:
        super().__init__(title=f"🏦 Rehipoteca a {panel.remortgage_months} meses"[:45])
        self.panel = panel
        self.most = most
        self.amount: ui.TextInput = ui.TextInput(
            label=f"Importe (hasta {format_amount(most)})"[:45], default=str(most), max_length=20
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        panel = self.panel
        await ack(interaction)
        try:
            amount = parse_amount(self.amount.value, self.most)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if amount > self.most:
            await notify(
                interaction,
                f"Lo más que te presta ahí a ese plazo es {format_amount(self.most)}: lo que "
                "deja la tasación y lo que cabe en tu 35 %.",
            )
            return
        if amount < lending.REMORTGAGE_MIN:
            await notify(
                interaction,
                f"El banco rehipoteca desde {format_amount(lending.REMORTGAGE_MIN)}.",
            )
            return
        desk = await panel.cog.housing.repository.remortgage_desk(panel.guild_id, panel.owner.id)
        found = next((c for c in desk.collaterals if c.key == panel.collateral), None)
        if found is None:
            await notify(interaction, "Ya no tienes nada en ese inmueble.")
            return
        quote = lending.remortgage_quote(amount, panel.remortgage_months, desk.rate, found.value)
        panel.remortgage_offer = (quote, found.key, found.title)
        await panel.refresh(interaction)


class CollateralSelect(ui.Select["BankPanel"]):
    """Elige el inmueble que se rehipoteca."""

    def __init__(self, usable: list[Collateral], selected: str | None) -> None:
        super().__init__(
            placeholder="Elige el inmueble",
            options=[
                discord.SelectOption(
                    label=found.title[:100],
                    description=(
                        f"Vale {format_amount(found.value)} · presta hasta "
                        f"{format_amount(found.room)}"
                    )[:100],
                    value=found.key,
                    default=found.key == selected,
                )
                for found in usable
            ],
            custom_id="banco:garantia",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.collateral = self.values[0]
        await panel.refresh(interaction)


class TermSelect(ui.Select["BankPanel"]):
    """Plazo de la rehipoteca."""

    def __init__(self, months: int) -> None:
        super().__init__(
            placeholder="Plazo",
            options=[
                discord.SelectOption(
                    label=f"{term} meses ({term // 12} años, {term} días)", value=str(term),
                    default=term == months,
                )
                for term in lending.REMORTGAGE_TERMS
            ],  # fmt: skip
            custom_id="banco:garantia_plazo",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.remortgage_months = int(self.values[0])
        await panel.refresh(interaction)


class AskButton(ui.Button["BankPanel"]):
    """🏦 Rehipotecar: abre el formulario con el importe."""

    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="Rehipotecar", emoji="🏦", style=discord.ButtonStyle.primary,
            custom_id="banco:rehipotecar", row=3, disabled=disabled,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        # El formulario tiene que ser la primera respuesta: lo más que presta se calculó al
        # pintar el mostrador (se comprueba otra vez al firmar).
        most = panel.remortgage_most
        if most < lending.REMORTGAGE_MIN or panel.collateral is None:
            await interaction.response.send_message(
                "Vuelve a abrir el mostrador de garantías.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AmountForm(panel, most))


class SignButton(ui.Button["BankPanel"]):
    """✍️ Firmar la rehipoteca."""

    def __init__(self) -> None:
        super().__init__(
            label="Firmar", emoji="✍️", style=discord.ButtonStyle.success,
            custom_id="banco:garantia_firmar", row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        if panel.remortgage_offer is None:
            await interaction.response.send_message("Esa oferta ya no vale.", ephemeral=True)
            return
        await panel.money(interaction, lambda: sign(panel, interaction))


class BackButton(ui.Button["BankPanel"]):
    """↩️ Volver al mostrador sin firmar."""

    def __init__(self) -> None:
        super().__init__(
            label="Volver", emoji="↩️", style=discord.ButtonStyle.secondary,
            custom_id="banco:garantia_volver", row=1,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.remortgage_offer = None
        await panel.refresh(interaction)
