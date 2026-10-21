"""Avalistas de `banco` (9.3.0): pedir a otro miembro que avale un préstamo o una rehipoteca.

No es un cog: son las piezas que usan los mostradores de 🏦 Préstamos y 🔐 Garantías
(`BankPanel`, en `bot.cogs.banco`). Diseño y normas en `docs/garantias.md`, parte 3; las
reglas, en `bot.services.loans` (`GUARANTOR_*`) y `repositories.housing.guarantor_problem`.

- 🤝 **Avalista** abre un desplegable de miembros. Elegido uno, el panel enseña lo que le cabe
  en su 35 % (que se suma a lo tuyo) o por qué no vale, y el tipo baja medio punto.
- En la ficha, ✍️ **Pedir el aval** publica la petición en el canal mencionando al avalista
  (`GuaranteeView`): ✍️ Avalar y ✖️ No solo para él, ↩️ Retirar para quien pide. Caduca a
  los `GUARANTEE_SECONDS`. Al avalar se firma, comprobándolo todo otra vez.
- Si el avalado no paga, el banco reclama al avalista y el avalado le debe lo pagado
  (`HousingRepository`, `_settle_loan_in` y `_execute_in`; se cobra cada noche y se embarga).

Avalar no mueve dinero del avalista: firmar lo mueve para quien pide, que recibe el aviso de
la Renta con su próximo botón (el clic es del avalista). Logros en 🏦 Banco
(`guarantee_*_stats`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.repositories.economy import BalanceLimitError
from bot.repositories.housing import HousingError
from bot.services import loans as lending
from bot.services.achievements import (
    StatDelta,
    guarantee_refused_stats,
    guarantee_signed_stats,
    loan_signed_stats,
    remortgage_signed_stats,
)
from bot.services.economy import format_amount
from bot.services.pets import Event, Moment
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.cogs.banco import BankPanel

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(120, 90, 160)
COLOR_DONE = discord.Color.from_rgb(87, 242, 135)
#: Lo que se contesta a un clic en una petición ya cerrada.
CLOSED = "Esta petición ya está cerrada."


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


async def load(panel: BankPanel) -> None:
    """Lee lo que le cabe al avalista elegido y si vale (antes de pintar el mostrador)."""
    if panel.guarantor is None:
        panel.guarantor_room, panel.guarantor_problem = 0, None
        return
    room, problem = await panel.cog.housing.guarantor_room(
        panel.guild_id, panel.owner.id, panel.guarantor.id
    )
    panel.guarantor_room, panel.guarantor_problem = room, problem


def extra_room(panel: BankPanel) -> int:
    """Lo que suma el avalista al hueco de cada mes (0 sin avalista o si no vale)."""
    if panel.guarantor is None or panel.guarantor_problem is not None:
        return 0
    return panel.guarantor_room


def guaranteed(panel: BankPanel) -> bool:
    """Si la oferta va con un avalista que vale."""
    return panel.guarantor is not None and panel.guarantor_problem is None


def field(panel: BankPanel) -> tuple[str, str] | None:
    """`(nombre, valor)` del campo del avalista en el mostrador, o `None` sin avalista."""
    if panel.guarantor is None:
        return None
    who = panel.guarantor.mention
    if panel.guarantor_problem is not None:
        return "🤝 Avalista", f"{who} no vale: {panel.guarantor_problem}"
    return (
        "🤝 Avalista",
        f"{who} · le caben **{format_amount(panel.guarantor_room)}** al mes en su 35 %, que se "
        f"suman a lo tuyo · el tipo baja {lending.GUARANTOR_DISCOUNT * 100:.1f}".replace(".", ",")
        + " puntos. Tendrá que aceptarlo: si dejas de pagar, el banco le reclama a él y tú le "
        "debes lo que pague.",
    )


def add_items(panel: BankPanel, *, button_row: int, select_row: int) -> None:
    """El botón 🤝 y, si se está eligiendo, el desplegable de miembros."""
    panel.add_item(GuarantorButton(panel.guarantor, row=button_row))
    if panel.picking_guarantor:
        panel.add_item(GuarantorSelect(row=select_row))


class GuarantorButton(ui.Button["BankPanel"]):
    """🤝 Avalista: elegir uno (o quitarlo si ya hay)."""

    def __init__(self, guarantor: discord.abc.User | None, *, row: int) -> None:
        label = f"Avalista: {guarantor.display_name}" if guarantor is not None else "Avalista"
        super().__init__(
            label=label[:80], emoji="🤝",
            style=discord.ButtonStyle.success if guarantor else discord.ButtonStyle.secondary,
            custom_id="banco:avalista", row=row,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        if panel.guarantor is not None:
            panel.guarantor = None
            panel.picking_guarantor = False
        else:
            panel.picking_guarantor = not panel.picking_guarantor
        await panel.refresh(interaction)


class GuarantorSelect(ui.UserSelect["BankPanel"]):
    """¿Quién te avala?"""

    def __init__(self, *, row: int) -> None:
        super().__init__(placeholder="¿Quién te avala?", custom_id="banco:avalista_elige", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        chosen = self.values[0]
        if chosen.bot:
            await interaction.response.send_message(
                "Un bot no avala a nadie: no tiene nómina.", ephemeral=True
            )
            return
        await ack(interaction)
        panel.guarantor = chosen
        panel.picking_guarantor = False
        await panel.refresh(interaction)


# -- La petición ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Request:
    """Lo que se pide avalar: un préstamo (`quote`, `insured`) o una rehipoteca.

    Attributes:
        loan: La oferta del préstamo, o `None` si es una rehipoteca.
        insured: Si el préstamo lleva el seguro.
        remortgage: La oferta de la rehipoteca, o `None`.
        key: El inmueble de la rehipoteca.
        title: Su titular.
        room: Lo que dejaba la tasación (para los logros).
    """

    loan: lending.Quote | None = None
    insured: bool = False
    remortgage: lending.RemortgageQuote | None = None
    key: str = ""
    title: str = ""
    room: int = 0

    def summary(self) -> str:
        """Lo que se pide, en una línea."""
        if self.loan is not None:
            quote = self.loan
            return (
                f"un préstamo de **{format_amount(quote.principal)}** a {quote.months} meses "
                f"({format_amount(quote.monthly)} al mes)"
            )
        assert self.remortgage is not None
        quote = self.remortgage
        return (
            f"una rehipoteca de **{format_amount(quote.principal)}** sobre **{self.title}** a "
            f"{quote.months} meses ({format_amount(quote.payment)} al mes)"
        )

    @property
    def owed(self) -> int:
        """Lo que se pide prestado (lo que respondería el avalista)."""
        quote = self.loan or self.remortgage
        assert quote is not None
        return quote.principal


async def ask(panel: BankPanel, interaction: discord.Interaction, request: Request) -> None:
    """Publica la petición de aval en el canal (ya con `ack`) y repinta el panel."""
    guarantor = panel.guarantor
    assert guarantor is not None
    view = GuaranteeView(panel, request, guarantor)
    embed = discord.Embed(
        title="🤝 Petición de aval",
        color=COLOR,
        description=(
            f"**{_name(panel.owner)}** pide a {guarantor.mention} que le avale "
            f"{request.summary()}.\n> Si deja de pagar, el banco te reclamará lo que quede y "
            "te lo cobrará como una deuda tuya (fichero de morosos y embargo incluidos); "
            f"{_name(panel.owner)} te deberá lo que pagues. Mientras dure, la cuota cuenta en "
            f"tu 35 %.\n-# Caduca <t:{int(panel.cog.clock()) + lending.GUARANTEE_SECONDS}:R>."
        ),
    )
    try:
        view.message = await interaction.followup.send(
            guarantor.mention,
            embed=embed,
            view=view,
            wait=True,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[guarantor]
            ),
        )
    except discord.HTTPException:
        logger.warning("No se pudo publicar la petición de aval", exc_info=True)
        await notify(interaction, "No he podido publicar la petición aquí. Prueba en otro canal.")
        return
    panel.notice = f"🤝 Petición de aval enviada a {guarantor.mention}: tiene que aceptarla."
    panel.quote = None
    panel.remortgage_offer = None
    await panel.refresh(interaction)


class GuaranteeView(ui.View):
    """✍️ Avalar y ✖️ No para el avalista; ↩️ Retirar para quien pide."""

    def __init__(self, panel: BankPanel, request: Request, guarantor: discord.abc.User) -> None:
        super().__init__(timeout=lending.GUARANTEE_SECONDS)
        self.cog = panel.cog
        self.guild_id = panel.guild_id
        self.borrower = panel.owner
        self.guarantor = guarantor
        self.request = request
        self.message: discord.Message | None = None
        self.done = False

    async def _close(self, interaction: discord.Interaction, text: str, *, ok: bool) -> None:
        self.done = True
        self.stop()
        embed = discord.Embed(description=text, color=COLOR_DONE if ok else COLOR)
        await edit(interaction, content=None, embed=embed, view=None)

    @ui.button(label="Avalar", emoji="✍️", style=discord.ButtonStyle.success, custom_id="aval:si")
    async def accept(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.guarantor.id:
            await interaction.response.send_message("Esto lo decide el avalista.", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message(
                "Esta petición ya está cerrada.", ephemeral=True
            )
            return
        self.done = True
        await ack(interaction)
        text, delta = await self._sign()
        await self._close(interaction, text, ok=delta is not None)
        if delta is not None:
            await logros.track(
                self.cog.bot, self.guild_id, self.borrower, interaction.channel, delta
            )
            await logros.track(
                self.cog.bot, self.guild_id, self.guarantor, interaction.channel,
                guarantee_signed_stats(given=True, open_count=await self._open_count()),
            )  # fmt: skip

    async def _open_count(self) -> int:
        market = await self.cog.housing.market(self.guild_id)
        return len(market.guaranteed_by(self.guarantor.id))

    async def _sign(self) -> tuple[str, StatDelta | None]:
        """Firma lo pedido con el aval. Devuelve el resultado y los logros de quien lo pide
        (`None` si no se firmó)."""
        request, borrower, guarantor = self.request, self.borrower, self.guarantor
        housing = self.cog.housing
        try:
            if request.loan is not None:
                quote = request.loan
                signed, balance = await housing.take_loan(
                    self.guild_id, borrower.id, principal=quote.principal, months=quote.months,
                    insured=request.insured, rate=quote.rate, guarantor=guarantor.id,
                )  # fmt: skip
                what = f"el préstamo **#{signed.loan.id}**"
                received, monthly = signed.quote.received, signed.quote.monthly
                desk, _ = await housing.loan_desk(self.guild_id, borrower.id)
                delta = loan_signed_stats(
                    principal=quote.principal, months=quote.months, insured=request.insured,
                    discount=desk.euribor + lending.LOAN_SPREAD - quote.rate,
                    open_loans=len(desk.loans),
                )  # fmt: skip
            else:
                assert request.remortgage is not None
                quote = request.remortgage
                done, balance = await housing.remortgage(
                    self.guild_id, borrower.id, request.key, principal=quote.principal,
                    months=quote.months, rate=quote.rate, guarantor=guarantor.id,
                )  # fmt: skip
                what = f"la rehipoteca **#{done.mortgage.id}** sobre **{request.title}**"
                received, monthly = done.quote.received, done.quote.payment
                desk = await housing.repository.remortgage_desk(self.guild_id, borrower.id)
                found = next((c for c in desk.collaterals if c.key == request.key), None)
                delta = remortgage_signed_stats(
                    principal=quote.principal, months=quote.months, room=request.room,
                    habitual=found is not None and found.habitual,
                    dwelling=found is None or found.dwelling, open_count=len(desk.remortgages),
                )  # fmt: skip
        except HousingError as error:
            return f"✖️ No se ha podido firmar: {error}", None
        except BalanceLimitError:
            return f"✖️ No se ha podido firmar: a {_name(borrower)} no le cabe más dinero.", None
        lines = [
            f"🤝 **{_name(guarantor)}** avala a **{_name(borrower)}**: firmado {what}. Entran "
            f"**{format_amount(received)}** (se queda con {format_amount(balance)}) y paga "
            f"{format_amount(monthly)} cada noche.",
        ]
        hint = await renta.hint(self.cog.bot, self.guild_id, borrower.id, Moment(Event.MONEY))
        if hint is not None:
            lines.append(hint)
        delta.add["guarantees_received"] = 1
        return "\n".join(lines), delta

    @ui.button(label="No", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="aval:no")
    async def refuse(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.guarantor.id:
            await interaction.response.send_message("Esto lo decide el avalista.", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message(
                "Esta petición ya está cerrada.", ephemeral=True
            )
            return
        await self._close(
            interaction, f"✖️ **{_name(self.guarantor)}** no avala a **{_name(self.borrower)}**.",
            ok=False,
        )  # fmt: skip
        await logros.track(
            self.cog.bot, self.guild_id, self.guarantor, interaction.channel,
            guarantee_refused_stats(),
        )  # fmt: skip

    @ui.button(
        label="Retirar", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="aval:retirar"
    )
    async def withdraw(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.borrower.id:
            await interaction.response.send_message("Solo la retira quien la pide.", ephemeral=True)
            return
        if self.done:
            await interaction.response.send_message(
                "Esta petición ya está cerrada.", ephemeral=True
            )
            return
        await self._close(
            interaction, f"↩️ **{_name(self.borrower)}** retira la petición de aval.", ok=False
        )

    async def on_timeout(self) -> None:
        if self.done or self.message is None:
            return
        self.done = True
        try:
            await self.message.edit(
                content=None,
                embed=discord.Embed(description="⌛ La petición de aval ha caducado.", color=COLOR),
                view=None,
            )
        except discord.HTTPException:
            pass
