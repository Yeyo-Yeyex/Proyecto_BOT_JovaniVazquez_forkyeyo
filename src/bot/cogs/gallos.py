"""Peleas de gallos: `gallos`, tu gallo contra los de la casa o contra el de otra persona.

- `gallos` (o `gallos 500`) abre tu reñidero, un panel que solo toca su dueño:
  la ficha de tu gallo (nombre, victorias, KO, rachas), un desplegable con los
  gallos de la casa (cada uno con su cuota, de ×1,12 a ×9,50) y los botones
  🐓 **¡A pelear!**, ½, ×2 y 💰 All-in para la apuesta, ✏️ **Nombre** para
  bautizar al gallo y 🎨 para cambiarle el aspecto (skins de la tienda; hay
  tres gratis).
- `gallos @alguien 500` le reta a un duelo de 500 Y$ por cabeza. El retado
  acepta o rechaza; quien reta puede retirarlo. Al aceptar se cobra a los dos y
  se abre la grada (`LOBBY_SECONDS`): el público apuesta por 🔴 uno u 🔵 otro a
  la porra, y cuando los dos dueños pulsan ✅ **¡Suelta el gallo!** (o se acaba
  el tiempo) empieza la pelea. El ganador se lleva el bote menos la comisión de
  la casa; la grada se reparte lo apostado entre los que acierten.

Cada pelea es un GIF en el reñidero (`bot.services.gallos_scene`, en Node con
canvas): los gallos entran, pelean por asaltos con sus picotazos, espolonazos
y patadas voladoras, y acaba por KO con estrellitas, porque uno sale por patas
o a los puntos. Reglas y cuotas en `bot.services.gallos`.

Dinero (`EconomyService`): juego, como el resto del casino (premios como
ganancia patrimonial de la base general, art. 33.1 LIRPF, y pérdidas que solo
compensan ganancias de juego, art. 33.5.d LIRPF): entra en la retención
diaria del casino y en la renta semanal. Contra la casa, `place_bet` al pelear
y `pay_winnings` con el premio (0 si pierde) antes de enseñar el GIF. En un
duelo, `place_bet` a los dos al aceptar y a cada apostante de grada al
apostar; al empezar la pelea se paga a todos con `pay_winnings` (0 a quien
pierde), antes del GIF: un apagado a mitad no deja a nadie sin cobrar. La
comisión del duelo y de la grada se destruye (es el margen de la casa, no un
impuesto). Si el bot se apaga con la grada abierta, se devuelve lo apostado.

Si `CASINO_CHANNEL_IDS` está configurado, solo se pelea en esos canales.
Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar
archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from fractions import Fraction
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.repositories.gallos import GallosRepository
from bot.services import skins
from bot.services.achievements import StatDelta, casino_stats, gallos_bet_stats, gallos_stats
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.gallos import (
    DUEL_CUT,
    GAME,
    HOUSE,
    NAME_MAX,
    POOL_CUT,
    RIVAL_BY_KEY,
    Corner,
    Fight,
    Finish,
    PoolBet,
    Rival,
    RoosterRecord,
    clean_name,
    draw_winner,
    duel_prize,
    format_odds,
    house_payout,
    narrate,
    pool_odds,
    pool_payouts,
    pool_totals,
    simulate,
)
from bot.services.gallos_render import Fighter, Media, intro_state, meta_state
from bot.services.gallos_scene import GallosScene
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

DEFAULT_STAKE = 100
#: El rival de la casa que sale elegido al abrir el panel (el del 50 %).
DEFAULT_RIVAL = "falcon"
VIEW_TIMEOUT = 300
#: Cuánto espera un reto a que lo acepten.
CHALLENGE_SECONDS = 120
#: Cuánto está abierta la grada antes de soltar los gallos.
LOBBY_SECONDS = 60
#: Desde aquí se anuncia en el canal una victoria contra la casa.
SHOUT_NET = 10_000
#: Margen tras el GIF antes de poner el PNG: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
GIF_NAME = "gallos.gif"
PNG_NAME = "gallos.png"
SKIN_GAME = "gallos"

COLOR_IDLE = discord.Color.from_rgb(214, 120, 40)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)
COLOR_DUEL = discord.Color.from_rgb(200, 30, 60)

WIN_LINES = (
    "¡Wepa!",
    "¡Kikirikí!",
    "¡Ese es mi gallo!",
    "¡Dale, campeón!",
    "¡Gallo con mayoría absoluta!",
    "¡Más gallo que Putin a caballo!",
    "¡El gallo sale en el NO-DO!",
    "¡Kikirikí, mi amor!",
    "¡Toma mango!",
)
LOSE_LINES = (
    "¡Ay, bendito!",
    "¡Caldo de gallina!",
    "¡Al puchero!",
    "¡Nooo!",
    "¡Gallo en pepitoria!",
    "¡El gallo dimite! Algo inédito.",
    "¡Más desplumado que un autónomo!",
    "¡Caldito para Waterloo!",
)
WAITING_LINES = (
    "🐓 Los gallos salen al ruedo…",
    "📣 ¡Hagan sus apuestas… ya no!",
    "🥁 Se calienta el reñidero…",
    "👀 Todo el barrio mirando…",
    "📣 Trump anuncia que su gallo es el más grande…",
    "🐓 Kim asegura que su gallo ya ganó ayer…",
    "☕ El barrio pide café y apuesta…",
    "🎙️ El NO-DO ya tiene escrito el resultado…",
    "🐎 Abascal llega a caballo a ver la pelea…",
)


def _percent(chance: Fraction) -> str:
    return f"{float(chance) * 100:.0f} %"


# -- Lo común a las dos mesas ------------------------------------------------------------


@dataclass(slots=True)
class Owner:
    """Un dueño y su gallo en una pelea."""

    member: discord.abc.User
    record: RoosterRecord
    skin: skins.Skin | None

    @property
    def skin_key(self) -> str:
        return self.skin.key if self.skin is not None else "serie"

    @property
    def paid_skin(self) -> bool:
        return self.skin is not None and not self.skin.free


def fighter_of(owner: Owner, odds: str = "") -> Fighter:
    """El gallo de un dueño tal como lo pinta la escena."""
    return Fighter(
        name=owner.record.name,
        tag=owner.member.display_name,
        skin=skins.params(SKIN_GAME, owner.skin),
        odds=odds,
    )


def fighter_of_rival(rival: Rival) -> Fighter:
    """Un gallo de la casa tal como lo pinta la escena."""
    look = dict(skins.GAMES[SKIN_GAME].defaults)
    look.update(rival.look)
    look["key"] = f"casa_{rival.key}"
    return Fighter(name=rival.name, tag=rival.origin, skin=look, odds=format_odds(rival.multiplier))


def record_line(record: RoosterRecord) -> str:
    """`12 peleas · 8 victorias (5 KO) · racha 3 · mejor 5`."""
    if not record.fights:
        return "Aún no ha pisado el reñidero."
    parts = [
        f"{record.fights} pelea{'s' if record.fights != 1 else ''}",
        f"{record.wins} victoria{'s' if record.wins != 1 else ''} ({record.kos} KO)",
    ]
    if record.streak:
        parts.append(f"🔥 racha {record.streak}")
    parts.append(f"mejor racha {record.best_streak}")
    return " · ".join(parts)


class NameModal(ui.Modal, title="✏️ Bautiza a tu gallo"):
    """Pide el nombre del gallo."""

    name: ui.TextInput = ui.TextInput(
        label="Nombre", placeholder="Kikirikí", max_length=NAME_MAX, min_length=1
    )

    def __init__(self, panel: HousePanel) -> None:
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida el nombre, lo guarda y repinta el panel."""
        try:
            name = clean_name(str(self.name.value))
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        # Guardarlo y repintar van a la base de datos y al pintor: se acepta antes.
        await ack(interaction)
        await self.panel.rename(interaction, name)


# -- Contra la casa ----------------------------------------------------------------------


class HousePanel(ui.View):
    """El reñidero de un miembro: su gallo contra los de la casa.

    No guarda dinero: se cobra y se paga siempre por la economía.
    """

    def __init__(self, cog: Gallos, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.rival = RIVAL_BY_KEY[DEFAULT_RIVAL]
        self.record = RoosterRecord(name="")
        self.balance = 0
        self.channel: object = None
        self.message: discord.Message | None = None
        self.result: str | None = None
        self._busy = False
        self._lock = asyncio.Lock()
        self._last_interaction: discord.Interaction | None = None

    # -- Texto ------------------------------------------------------------------------

    def description(self) -> str:
        """Texto del embed: tu gallo, el rival elegido y, si lo hay, el último resultado."""
        rival = self.rival
        lines = []
        if self.result:
            lines.append(self.result)
        lines.append(f"### 🐓 {self.record.name}")
        lines.append(f"-# {record_line(self.record)}")
        lines.append(f"### 🆚 {rival.label}")
        lines.append(f"*{rival.blurb}*")
        lines.append(
            f"Cuota **{format_odds(rival.multiplier)}** · le ganas el {_percent(rival.chance)} "
            f"de las veces · si ganas, cobras **{format_amount(house_payout(self.stake, rival))}**"
        )
        lines.append(
            f"-# Apuesta {format_amount(self.stake)} · Saldo {format_amount(self.balance)}"
        )
        if self.balance == 0:
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def embed(
        self, *, image: str = PNG_NAME, text: str | None = None, color: discord.Color | None = None
    ) -> discord.Embed:
        """Embed con la imagen adjunta (`attachment://`)."""
        embed = discord.Embed(
            title=f"🐓 Reñidero de {self.owner.display_name}",
            description=text if text is not None else self.description(),
            color=color or COLOR_IDLE,
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    # -- Componentes ------------------------------------------------------------------

    def rebuild(self, *, busy: bool = False) -> None:
        """Vuelve a montar el desplegable y los botones con el estado actual."""
        self.clear_items()
        select: ui.Select = ui.Select(
            placeholder="Elige rival",
            row=0,
            disabled=busy,
            custom_id=f"{GAME}:rival",
            options=[
                discord.SelectOption(
                    label=f"{r.name} · {format_odds(r.multiplier)}"[:100],
                    value=r.key,
                    emoji=r.emoji,
                    description=r.blurb[:100],
                    default=r.key == self.rival.key,
                )
                for r in HOUSE
            ],
        )
        select.callback = self._choose_rival  # type: ignore[method-assign]
        self.add_item(select)
        self._button(
            f"🐓 ¡A pelear! · {format_amount(self.stake)}",
            "fight",
            self._fight,
            style=discord.ButtonStyle.danger,
            row=1,
            disabled=busy,
        )
        self._button("½", "half", self._halve, row=1, disabled=busy)
        self._button("×2", "x2", self._double, row=1, disabled=busy)
        self._button("💰 All-in", "allin", self._all_in, row=1, disabled=busy)
        self._button("✏️ Nombre", "name", self._name, row=2, disabled=busy)
        if (button := tienda.skin_button(self.cog.bot, SKIN_GAME, row=2)) is not None:
            button.disabled = busy
            self.add_item(button)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label, style=style, disabled=disabled, custom_id=f"{GAME}:{custom_id}", row=row
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def disable_all(self) -> None:
        """Apaga todo (panel caducado o pelea en marcha)."""
        for item in self.children:
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño pelea con su gallo."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Este gallo es de {self.owner.display_name}. Saca el tuyo con `gallos`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: apaga los botones."""
        self.cog.panels.discard(self)
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el reñidero", exc_info=True)

    # -- Imagen -----------------------------------------------------------------------

    def _owner(self) -> Owner:
        return Owner(
            self.owner,
            self.record,
            tienda.skin_of(self.cog.bot, self.guild_id, self.owner.id, SKIN_GAME),
        )

    def _meta(self, seed: int) -> dict[str, Any]:
        rival = self.rival
        return meta_state(
            fighter_of(self._owner(), odds=format_amount(self.stake)),
            fighter_of_rival(rival),
            title="PELEA DE GALLOS",
            subtitle=f"Contra la casa · {format_odds(rival.multiplier)}",
            seed=seed,
        )

    async def poster(self) -> bytes:
        """PNG de la presentación: tu gallo frente al rival elegido."""
        return await self.cog.renderer.still(self._meta(0), intro_state())

    async def repaint(self, interaction: discord.Interaction) -> None:
        """Repinta la presentación, el texto y los botones."""
        png = await self.poster()
        self.rebuild()
        self._last_interaction = interaction
        await edit(
            interaction,
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )

    # -- Botones ----------------------------------------------------------------------

    async def _choose_rival(self, interaction: discord.Interaction) -> None:
        """Elige rival: cambia la presentación (dibujar es lento: se acepta antes)."""
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        if self._busy or not values or values[0] not in RIVAL_BY_KEY:
            return
        self.rival = RIVAL_BY_KEY[values[0]]
        self.result = None
        await self.repaint(interaction)

    async def _name(self, interaction: discord.Interaction) -> None:
        """✏️ Nombre: abre el formulario (tiene que ser la primera respuesta)."""
        if self._busy:
            await interaction.response.send_message("Espera a que acabe la pelea.", ephemeral=True)
            return
        await interaction.response.send_modal(NameModal(self))

    async def rename(self, interaction: discord.Interaction, name: str) -> None:
        """Guarda el nombre nuevo (ya validado) y repinta."""
        before = self.record.name
        self.record = await self.cog.repository.rename(self.guild_id, self.owner.id, name)
        await self.repaint(interaction)
        if name != before:
            await logros.track(
                self.cog.bot,
                self.guild_id,
                self.owner,
                self.channel,
                StatDelta(add={"gallos_renamed": 1}),
            )

    def _idle(self) -> bool:
        return not self._busy

    async def _restake(self, interaction: discord.Interaction, stake: int) -> None:
        self.stake = max(1, stake)
        self.result = None
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, embed=self.embed(), view=self)

    async def _halve(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self._idle():
            self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
            await self._restake(interaction, self.stake // 2)

    async def _double(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self._idle():
            self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
            await self._restake(interaction, min(self.stake * 2, max(1, self.balance)))

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self._idle():
            return
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        if self.balance <= 0:
            await notify(interaction, insufficient_text(0))
            return
        await self._restake(interaction, self.balance)

    async def _fight(self, interaction: discord.Interaction) -> None:
        """🐓 ¡A pelear!: cobra, decide, paga y enseña la pelea."""
        # Cobrar y dibujar van antes que nada: se acepta el clic ya.
        await ack(interaction)
        if self._busy:
            return
        self._busy = True
        self._last_interaction = interaction
        try:
            outcome = await self._play()
            if isinstance(outcome, str):
                await notify(interaction, outcome)
                return
            await self._show(interaction, outcome)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar una pelea de gallos", exc_info=True)
            return
        finally:
            self._busy = False
        await self._after(interaction, outcome)

    async def _play(self) -> HouseFight | str:
        """Cobra la apuesta, decide la pelea y paga, todo antes de enseñarla."""
        rival, stake = self.rival, self.stake
        async with self._lock:
            try:
                await self.cog.economy.place_bet(
                    self.guild_id, self.owner.id, game=GAME, stake=stake
                )
            except InsufficientFundsError as error:
                return insufficient_text(error.balance, stake)
            rng = self.cog.rng
            fight = simulate(draw_winner(rival.chance, rng), rng)
            won = fight.winner is Corner.RED
            payout = house_payout(stake, rival) if won else 0
            settlement = await self.cog.pay(self.guild_id, self.owner.id, payout)
            self.balance = (
                settlement.balance
                if settlement
                else await self.cog.economy.balance(self.guild_id, self.owner.id)
            )
            _before, self.record = await self.cog.repository.record_fight(
                self.guild_id,
                self.owner.id,
                won=won,
                finish=fight.finish,
                day=datetime.now(TIMEZONE).date(),
            )
            return HouseFight(
                rival=rival,
                stake=stake,
                fight=fight,
                payout=payout,
                settlement=settlement,
                seed=rng.randrange(1, 2**31),
                owner=self._owner(),
            )

    async def _show(self, interaction: discord.Interaction, game: HouseFight) -> None:
        """Enseña la pelea (con los botones apagados mientras se pinta) y el resultado."""
        self.disable_all()
        waiting = (
            f"# {random.choice(WAITING_LINES)}\n"
            f"**{game.owner.record.name}** 🆚 **{game.rival.label}** · "
            f"{format_amount(game.stake)} a {format_odds(game.rival.multiplier)}"
        )
        names = {Corner.RED: game.owner.record.name, Corner.BLUE: game.rival.name}

        async def show_waiting() -> None:
            try:
                await edit(interaction, embed=self.embed(text=waiting), view=self)
            except discord.HTTPException:
                logger.warning("No se pudo apagar el reñidero", exc_info=True)

        meta = self._meta(game.seed)
        _, media = await asyncio.gather(
            show_waiting(), self.cog.renderer.fight(meta, game.fight, names, seed=game.seed)
        )
        await edit(
            interaction,
            embed=self.embed(image=GIF_NAME, text=waiting),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=self,
        )
        await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        self.result = await self._result_text(game, names)
        self.rebuild()
        await edit(
            interaction,
            embed=self.embed(color=COLOR_WIN if game.won else COLOR_LOST),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _result_text(self, game: HouseFight, names: dict[Corner, str]) -> str:
        fight = game.fight
        lines = []
        if game.won:
            lines.append(f"# 🏆 {random.choice(WIN_LINES)} +{format_amount(game.net)}")
            lines.append(
                f"**{names[Corner.RED]}** gana a {game.rival.label} {fight.finish.label} y cobras "
                f"**{format_amount(game.payout)}**."
            )
        else:
            lines.append(f"# 💀 {random.choice(LOSE_LINES)} -{format_amount(game.stake)}")
            lines.append(f"{game.rival.label} gana {fight.finish.label}.")
        lines += narrate(fight, names, {Corner.BLUE: game.rival.signature})
        if game.settlement is not None and (tax := gambling_tax_line(game.settlement)):
            lines.append(tax)
        if hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=game.stake, net=game.net, balance_after=self.balance),
        ):
            lines.append(hint)
        return "\n".join(lines)

    async def _after(self, interaction: discord.Interaction, game: HouseFight) -> None:
        """Lo que va después de enseñar el resultado: renta, logros, estadísticas y anuncio."""
        await renta.remind(self.cog.bot, interaction)
        tax = game.settlement.tax_delta if game.settlement else 0
        delta = gallos_stats(
            fight=game.fight,
            corner=Corner.RED,
            rival=game.rival.key,
            chance=float(game.rival.chance),
            stake=game.stake,
            net=game.net,
            record=self.record,
            skin=game.owner.skin_key,
            paid_skin=game.owner.paid_skin,
            crowd_pool=0,
            rake=0,
            when=datetime.now(TIMEZONE),
        )
        delta.merge(
            casino_stats(stake=game.stake, net=game.net, balance_after=self.balance, tax_delta=tax)
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self.channel, delta, net=game.net
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=game.stake,
            net=game.net,
            balance_after=self.balance,
            tax=tax,
            details=(("ko", int(game.fight.finish is Finish.KO)), ("won", int(game.won))),
        )
        if game.won and game.net >= SHOUT_NET:
            await self.cog.shout(
                self.channel,
                f"📣 🐓 ¡**{game.owner.record.name}**, el gallo de {self.owner.mention}, le ha "
                f"ganado a {game.rival.label}! **+{format_amount(game.net)}**",
                self.owner,
            )


@dataclass(frozen=True, slots=True)
class HouseFight:
    """Una pelea contra la casa ya cobrada y pagada."""

    rival: Rival
    stake: int
    fight: Fight
    payout: int
    settlement: BetSettlement | None
    seed: int
    owner: Owner

    @property
    def won(self) -> bool:
        return self.fight.winner is Corner.RED

    @property
    def net(self) -> int:
        return self.payout - self.stake


# -- Duelos ------------------------------------------------------------------------------


class Phase(Enum):
    """Momento de un duelo."""

    CHALLENGE = "reto"
    LOBBY = "grada"
    FIGHT = "pelea"
    DONE = "fin"


@dataclass(slots=True)
class Spectator:
    """Un apostante de grada: su apuesta y la última interacción (para su resultado privado)."""

    member: discord.abc.User
    bet: PoolBet
    interaction: discord.Interaction | None = None
    settlement: BetSettlement | None = None
    payout: int = 0


class BetModal(ui.Modal, title="🎟️ Apuesta de grada"):
    """Pide cuánto apuesta el público por un gallo."""

    amount: ui.TextInput = ui.TextInput(
        label="Cantidad (500, 2k, all…)", placeholder="100", max_length=12
    )

    def __init__(self, duel: Duel, corner: Corner) -> None:
        super().__init__()
        self.duel = duel
        self.corner = corner

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Cobra la apuesta y la apunta en la grada."""
        # Leer el saldo y cobrar van a la base de datos: se acepta antes.
        await ack(interaction)
        await self.duel.place(interaction, self.corner, str(self.amount.value))


class DuelView(ui.View):
    """Los botones de un duelo; quién puede pulsar cada uno lo decide el duelo."""

    def __init__(self, duel: Duel) -> None:
        super().__init__(timeout=None)
        self.duel = duel

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int = 0,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label[:80], style=style, custom_id=f"{GAME}:{custom_id}", row=row
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def challenge(self) -> DuelView:
        """Botones del reto: aceptar, rechazar o retirarlo."""
        self.clear_items()
        self._button("✅ Aceptar", "accept", self.duel.accept, style=discord.ButtonStyle.success)
        self._button("❌ Rechazar", "reject", self.duel.reject, style=discord.ButtonStyle.danger)
        self._button("🗑️ Retirar", "withdraw", self.duel.withdraw)
        return self

    def lobby(self) -> DuelView:
        """Botones de la grada: apostar por cada gallo y soltar los gallos."""
        self.clear_items()
        duel = self.duel
        for corner in Corner:
            style = (
                discord.ButtonStyle.danger if corner is Corner.RED else discord.ButtonStyle.primary
            )
            self._button(
                f"{corner.emoji} Apostar por {duel.owner(corner).record.name}",
                f"bet_{corner.value}",
                duel.bet_red if corner is Corner.RED else duel.bet_blue,
                style=style,
            )
        self._button(
            f"✅ ¡Suelta el gallo! ({len(duel.ready)}/2)",
            "ready",
            duel.mark_ready,
            style=discord.ButtonStyle.success,
            row=1,
        )
        return self


class Duel:
    """Un duelo entre dos miembros, del reto al resultado, en un mismo mensaje.

    Guarda quién apuesta y cuánto solo para repartir: el dinero se cobra y se
    paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Gallos,
        *,
        guild_id: int,
        channel: object,
        red: Owner,
        blue: Owner,
        stake: int,
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.owners = {Corner.RED: red, Corner.BLUE: blue}
        self.stake = stake
        self.phase = Phase.CHALLENGE
        self.view = DuelView(self)
        self.message: discord.Message | None = None
        self.crowd: dict[int, Spectator] = {}
        self.ready: set[int] = set()
        self.interactions: dict[int, discord.Interaction] = {}
        self.settlements: dict[Corner, BetSettlement | None] = {}
        self.lobby_ends = 0.0
        self.started_at = 0.0
        self.go = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.fight: Fight | None = None
        self.seed = cog.rng.randrange(1, 2**31)
        self.prepared: asyncio.Task[Media] | None = None
        self._lock = asyncio.Lock()
        self.timer: asyncio.Task[None] | None = None

    def owner(self, corner: Corner) -> Owner:
        """El dueño de una esquina."""
        return self.owners[corner]

    def corner_of(self, user_id: int) -> Corner | None:
        """La esquina de un dueño, o `None` si no pelea."""
        for corner, owner in self.owners.items():
            if owner.member.id == user_id:
                return corner
        return None

    @property
    def members(self) -> list[discord.abc.User]:
        return [self.owners[c].member for c in Corner]

    # -- Texto e imagen -----------------------------------------------------------------

    def names(self) -> dict[Corner, str]:
        return {c: self.owners[c].record.name for c in Corner}

    def meta(self) -> dict[str, Any]:
        odds = pool_odds({k: s.bet for k, s in self.crowd.items()})
        return meta_state(
            fighter_of(self.owners[Corner.RED], odds=self._odds_text(odds[Corner.RED])),
            fighter_of(self.owners[Corner.BLUE], odds=self._odds_text(odds[Corner.BLUE])),
            title="DUELO DE GALLOS",
            subtitle=f"Bote {format_amount(duel_prize(self.stake))}",
            seed=self.seed,
        )

    @staticmethod
    def _odds_text(odds: Fraction | None) -> str:
        return format_odds(odds) if odds is not None else "sin apuestas"

    def _corner_line(self, corner: Corner) -> str:
        owner = self.owners[corner]
        return (
            f"{corner.emoji} **{owner.record.name}** de {owner.member.mention} · "
            f"{record_line(owner.record)}"
        )

    def embed(
        self, text: str, *, image: str = PNG_NAME, color: discord.Color = COLOR_DUEL
    ) -> discord.Embed:
        red, blue = self.members
        embed = discord.Embed(
            title=f"🐓 Duelo: {red.display_name} 🆚 {blue.display_name}",
            description=text,
            color=color,
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def challenge_text(self) -> str:
        red, blue = self.members
        return "\n".join(
            [
                f"# 🥊 {red.mention} reta a {blue.mention}",
                f"**{format_amount(self.stake)}** por cabeza · el ganador se lleva "
                f"**{format_amount(duel_prize(self.stake))}** "
                f"(la casa se queda el {int(DUEL_CUT * 100)} %).",
                self._corner_line(Corner.RED),
                self._corner_line(Corner.BLUE),
                f"-# {blue.display_name} tiene <t:{int(self.lobby_ends)}:R> para aceptar.",
            ]
        )

    def lobby_text(self) -> str:
        bets = {k: s.bet for k, s in self.crowd.items()}
        totals = pool_totals(bets)
        odds = pool_odds(bets)
        lines = [
            "# 🎟️ ¡Hagan sus apuestas!",
            f"Bote del duelo: **{format_amount(duel_prize(self.stake))}**.",
            self._corner_line(Corner.RED),
            self._corner_line(Corner.BLUE),
            "### Grada",
        ]
        for corner in Corner:
            backers = sum(1 for s in self.crowd.values() if s.bet.corner is corner)
            lines.append(
                f"{corner.emoji} {format_amount(totals[corner])} de {backers} "
                f"persona{'s' if backers != 1 else ''} · paga {self._odds_text(odds[corner])}"
            )
        lines.append(
            f"-# La grada se reparte lo apostado entre los que acierten, menos el "
            f"{int(POOL_CUT * 100)} % de la casa. Los gallos salen <t:{int(self.lobby_ends)}:R> o "
            "cuando sus dueños pulsen ✅."
        )
        return "\n".join(lines)

    async def poster(self) -> bytes:
        return await self.cog.renderer.still(self.meta(), intro_state())

    async def edit(self, **kwargs: Any) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar un duelo de gallos", exc_info=True)

    # -- Reto -------------------------------------------------------------------------

    async def open(self, send: Callable[..., Awaitable[discord.Message]]) -> None:
        """Manda el reto y espera a que lo acepten."""
        self.lobby_ends = self.cog.wall_clock() + CHALLENGE_SECONDS
        png = await self.poster()
        self.message = await send(
            content=self.owners[Corner.BLUE].member.mention,
            embed=self.embed(self.challenge_text()),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=self.view.challenge(),
            allowed_mentions=discord.AllowedMentions(users=[self.owners[Corner.BLUE].member]),
        )
        self.timer = asyncio.create_task(self._expire(), name="gallos-reto")

    async def _expire(self) -> None:
        await self.cog.sleep(CHALLENGE_SECONDS)
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                return
            self.phase = Phase.DONE
        self.cog.release(self)
        await self.edit(
            content=None,
            embed=self.embed("⌛ Nadie contestó al reto. Los gallos vuelven al corral."),
            view=None,
        )

    async def _only(self, interaction: discord.Interaction, corner: Corner, what: str) -> bool:
        if interaction.user.id == self.owners[corner].member.id:
            return True
        await interaction.response.send_message(what, ephemeral=True)
        return False

    async def accept(self, interaction: discord.Interaction) -> None:
        """✅ Aceptar: cobra a los dos y abre la grada."""
        if not await self._only(interaction, Corner.BLUE, "Este reto no es para ti."):
            return
        await ack(interaction)
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                return
            error = await self._charge_fighters()
            if error:
                await notify(interaction, error)
                return
            self.phase = Phase.LOBBY
            self.interactions[interaction.user.id] = interaction
            self.started_at = time.time()
            self.lobby_ends = self.cog.wall_clock() + LOBBY_SECONDS
            # La pelea se decide y se pinta ya, mientras se apuesta: el resultado no
            # depende de las apuestas (sale de su propio azar, 50 % cada uno) y no se
            # enseña ni se guarda en ningún sitio hasta que salen los gallos.
            rng = self.cog.rng
            self.fight = simulate(draw_winner(Fraction(1, 2), rng), rng)
            self.prepared = asyncio.create_task(
                self.cog.renderer.fight(self.meta(), self.fight, self.names(), seed=self.seed),
                name="gallos-dibujo",
            )
        if self.timer is not None:
            self.timer.cancel()
        await edit(
            interaction, content=None, embed=self.embed(self.lobby_text()), view=self.view.lobby()
        )
        self.task = asyncio.create_task(self.run(), name="gallos-duelo")
        await renta.remind(self.cog.bot, interaction)

    async def _charge_fighters(self) -> str | None:
        """Cobra la apuesta a los dos; si al segundo no le llega, devuelve la del primero."""
        charged: list[discord.abc.User] = []
        for corner in (Corner.BLUE, Corner.RED):
            member = self.owners[corner].member
            try:
                await self.cog.economy.place_bet(
                    self.guild_id, member.id, game=GAME, stake=self.stake
                )
            except InsufficientFundsError as error:
                for refunded in charged:
                    await self.cog.pay(self.guild_id, refunded.id, self.stake)
                if corner is Corner.BLUE:
                    return insufficient_text(error.balance, self.stake)
                return f"A {member.display_name} ya no le llega para el duelo."
            charged.append(member)
        return None

    async def reject(self, interaction: discord.Interaction) -> None:
        """❌ Rechazar: el reto se acaba sin mover dinero."""
        if not await self._only(interaction, Corner.BLUE, "Este reto no es para ti."):
            return
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                await interaction.response.defer()
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.cog.release(self)
        red, blue = self.members
        await edit(
            interaction,
            content=None,
            embed=self.embed(f"🐔 {blue.mention} rechaza el reto. Co-co-co…"),
            view=None,
        )
        await logros.track(
            self.cog.bot, self.guild_id, blue, self.channel, StatDelta(add={"gallos_rejects": 1})
        )
        await logros.track(
            self.cog.bot, self.guild_id, red, self.channel, StatDelta(add={"gallos_rejected": 1})
        )

    async def withdraw(self, interaction: discord.Interaction) -> None:
        """🗑️ Retirar: quien reta se echa atrás antes de que acepten."""
        if not await self._only(interaction, Corner.RED, "Solo quien reta puede retirarlo."):
            return
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                await interaction.response.defer()
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.cog.release(self)
        await edit(
            interaction,
            content=None,
            embed=self.embed("🗑️ Reto retirado. Aquí no ha pasado nada."),
            view=None,
        )

    # -- Grada ------------------------------------------------------------------------

    async def bet_red(self, interaction: discord.Interaction) -> None:
        await self._open_bet(interaction, Corner.RED)

    async def bet_blue(self, interaction: discord.Interaction) -> None:
        await self._open_bet(interaction, Corner.BLUE)

    async def _open_bet(self, interaction: discord.Interaction, corner: Corner) -> None:
        """Abre el formulario de la apuesta (las comprobaciones solo miran memoria)."""
        if self.phase is not Phase.LOBBY:
            await interaction.response.send_message("La grada ya está cerrada.", ephemeral=True)
            return
        if self.corner_of(interaction.user.id) is not None:
            await interaction.response.send_message(
                "Tú ya te juegas tu gallo: la grada es para el público.", ephemeral=True
            )
            return
        if interaction.user.id in self.crowd:
            await interaction.response.send_message(
                "Ya tienes tu apuesta en esta pelea.", ephemeral=True
            )
            return
        await interaction.response.send_modal(BetModal(self, corner))

    async def place(self, interaction: discord.Interaction, corner: Corner, text: str) -> None:
        """Cobra una apuesta de grada (el formulario ya está aceptado)."""
        user = interaction.user
        balance = await self.cog.economy.balance(self.guild_id, user.id)
        try:
            stake = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if stake <= 0 or stake > balance:
            await notify(interaction, insufficient_text(balance, stake))
            return
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                await notify(interaction, "La grada ya está cerrada.")
                return
            if user.id in self.crowd:
                await notify(interaction, "Ya tienes tu apuesta en esta pelea.")
                return
            try:
                await self.cog.economy.place_bet(self.guild_id, user.id, game=GAME, stake=stake)
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, stake))
                return
            self.crowd[user.id] = Spectator(user, PoolBet(corner, stake), interaction)
        await notify(
            interaction,
            f"🎟️ {format_amount(stake)} por {corner.emoji} **{self.owners[corner].record.name}**. "
            "Te digo cómo acaba en cuanto suelten los gallos.",
        )
        await self.edit(embed=self.embed(self.lobby_text()), view=self.view.lobby())
        await renta.remind(self.cog.bot, interaction)

    async def mark_ready(self, interaction: discord.Interaction) -> None:
        """✅ ¡Suelta el gallo!: cuando los dos dueños lo pulsan, empieza la pelea."""
        if self.corner_of(interaction.user.id) is None:
            await interaction.response.send_message(
                "Solo los dueños de los gallos pueden soltarlos.", ephemeral=True
            )
            return
        if self.phase is not Phase.LOBBY:
            await interaction.response.defer()
            return
        self.ready.add(interaction.user.id)
        self.interactions[interaction.user.id] = interaction
        await interaction.response.edit_message(view=self.view.lobby())
        if len(self.ready) == 2:
            self.go.set()
        await renta.remind(self.cog.bot, interaction)

    # -- Pelea ------------------------------------------------------------------------

    async def run(self) -> None:
        """Espera a que acabe la grada (o a los dos ✅) y pelea."""
        try:
            wait = max(0.0, self.lobby_ends - self.cog.wall_clock())
            sleeper = asyncio.ensure_future(self.cog.sleep(wait))
            waker = asyncio.ensure_future(self.go.wait())
            try:
                await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (sleeper, waker):
                    task.cancel()
                await asyncio.gather(sleeper, waker, return_exceptions=True)
            await self.brawl()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("El duelo de gallos ha fallado; se devuelve lo apostado")
            await self.shutdown()
        finally:
            self.cog.release(self)

    async def brawl(self) -> None:
        """Paga a todos y enseña la pelea (el GIF ya se pintó durante la grada)."""
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                return
            self.phase = Phase.FIGHT
            fight = self.fight
            assert fight is not None
            prize = duel_prize(self.stake)
            for corner in Corner:
                won = fight.winner is corner
                self.settlements[corner] = await self.cog.pay(
                    self.guild_id, self.owners[corner].member.id, prize if won else 0
                )
            payouts = pool_payouts({k: s.bet for k, s in self.crowd.items()}, fight.winner)
            for user_id, spectator in self.crowd.items():
                spectator.payout = payouts[user_id]
                spectator.settlement = await self.cog.pay(self.guild_id, user_id, spectator.payout)
            day = datetime.now(TIMEZONE).date()
            for corner in Corner:
                owner = self.owners[corner]
                _before, owner.record = await self.cog.repository.record_fight(
                    self.guild_id,
                    owner.member.id,
                    won=fight.winner is corner,
                    finish=fight.finish,
                    day=day,
                )
        names = self.names()
        waiting = f"# {random.choice(WAITING_LINES)}\n{self._corner_line(Corner.RED)}\n" + (
            self._corner_line(Corner.BLUE)
        )
        await self.edit(embed=self.embed(waiting), view=None)
        assert self.prepared is not None
        try:
            media = await self.prepared
        except Exception:
            logger.warning("No se pudo pintar el duelo por adelantado", exc_info=True)
            media = await self.cog.renderer.fight(self.meta(), fight, names, seed=self.seed)
        await self.edit(
            embed=self.embed(waiting, image=GIF_NAME),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
        )
        await self.cog.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        await self.edit(
            content=None,
            embed=self.embed(self.result_text(fight, names), color=COLOR_WIN),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
        )
        self.phase = Phase.DONE
        self.cog.release(self)
        await self.tell_everyone(fight)
        await self.track(fight)

    def result_text(self, fight: Fight, names: dict[Corner, str]) -> str:
        winner = self.owners[fight.winner]
        loser = self.owners[fight.loser]
        prize = duel_prize(self.stake)
        lines = [
            f"# 🏆 ¡Gana {fight.winner.emoji} {winner.record.name}!",
            f"El gallo de {winner.member.mention} tumba al de {loser.member.mention} "
            f"{fight.finish.label} y se lleva **{format_amount(prize)}** "
            f"(+{format_amount(prize - self.stake)}).",
            *narrate(fight, names),
        ]
        if self.crowd:
            winners = [s for s in self.crowd.values() if s.payout > s.bet.stake]
            refund = all(s.payout == s.bet.stake for s in self.crowd.values())
            if refund:
                lines.append("🎟️ Nadie apostó por el otro gallo: la grada recupera lo suyo.")
            elif winners:
                best = max(winners, key=lambda s: s.payout - s.bet.stake)
                lines.append(
                    f"🎟️ {len(winners)} de {len(self.crowd)} en la grada aciertan. El que más: "
                    f"{best.member.mention}, +{format_amount(best.payout - best.bet.stake)}."
                )
            else:
                lines.append("🎟️ Nadie en la grada lo vio venir.")
        return "\n".join(lines)

    async def tell_everyone(self, fight: Fight) -> None:
        """A cada uno, en privado, lo suyo: lo cobrado, Hacienda y su mascota."""
        for corner in Corner:
            owner = self.owners[corner]
            interaction = self.interactions.get(owner.member.id)
            settlement = self.settlements.get(corner)
            won = fight.winner is corner
            net = (duel_prize(self.stake) if won else 0) - self.stake
            text = (
                f"🏆 Tu gallo gana: +{format_amount(net)}"
                if won
                else f"💀 Tu gallo pierde: -{format_amount(self.stake)}"
            )
            await self._tell(interaction, owner.member.id, text, settlement, self.stake, net)
        for spectator in self.crowd.values():
            stake = spectator.bet.stake
            net = spectator.payout - stake
            if spectator.payout == stake:
                text = f"🎟️ Te devuelven tus {format_amount(stake)}."
            elif spectator.payout:
                text = (
                    f"🎟️ ¡Acertaste! Cobras {format_amount(spectator.payout)} "
                    f"(+{format_amount(net)})."
                )
            else:
                text = f"🎟️ Fallaste: -{format_amount(stake)}."
            await self._tell(
                spectator.interaction, spectator.member.id, text, spectator.settlement, stake, net
            )

    async def _tell(
        self,
        interaction: discord.Interaction | None,
        user_id: int,
        text: str,
        settlement: BetSettlement | None,
        stake: int,
        net: int,
    ) -> None:
        if interaction is None:
            return
        if settlement is not None and (tax := gambling_tax_line(settlement)):
            text += f"\n{tax}"
        balance = settlement.balance if settlement is not None else 0
        if hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            user_id,
            bet_moment(stake=stake, net=net, balance_after=balance),
        ):
            text += f"\n{hint}"
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException:
            logger.debug("No se pudo mandar el resultado privado de un duelo", exc_info=True)

    async def track(self, fight: Fight) -> None:
        """Logros y estadísticas del casino de cada uno, después de enseñar el resultado."""
        when = datetime.now(TIMEZONE)
        bets = {k: s.bet for k, s in self.crowd.items()}
        crowd_pool = sum(pool_totals(bets).values())
        refund = all(s.payout == s.bet.stake for s in self.crowd.values())
        prize = duel_prize(self.stake)
        for corner in Corner:
            owner = self.owners[corner]
            won = fight.winner is corner
            net = (prize if won else 0) - self.stake
            settlement = self.settlements.get(corner)
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            delta = gallos_stats(
                fight=fight,
                corner=corner,
                rival=None,
                chance=0.5,
                stake=self.stake,
                net=net,
                record=owner.record,
                skin=owner.skin_key,
                paid_skin=owner.paid_skin,
                crowd_pool=crowd_pool,
                # La comisión del bote sale a medias de los dos.
                rake=(2 * self.stake - prize) // 2,
                when=when,
            )
            delta.merge(
                casino_stats(stake=self.stake, net=net, balance_after=balance, tax_delta=tax)
            )
            await logros.casino_play(
                self.cog.bot, self.guild_id, owner.member, self.channel, delta, net=net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                owner.member,
                game=GAME,
                stake=self.stake,
                net=net,
                balance_after=balance,
                tax=tax,
                details=(
                    ("ko", int(fight.finish is Finish.KO)),
                    ("won", int(won)),
                    ("started", int(self.started_at)),
                ),
            )
        for spectator in self.crowd.values():
            stake = spectator.bet.stake
            net = spectator.payout - stake
            settlement = spectator.settlement
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            won = spectator.bet.corner is fight.winner
            odds_cents = spectator.payout * 100 // stake if won and not refund else 0
            delta = gallos_bet_stats(
                won=won,
                refunded=refund,
                odds_cents=odds_cents,
                rake=0 if refund else int(stake * POOL_CUT),
                when=when,
            )
            delta.merge(casino_stats(stake=stake, net=net, balance_after=balance, tax_delta=tax))
            await logros.casino_play(
                self.cog.bot, self.guild_id, spectator.member, self.channel, delta, net=net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                spectator.member,
                game=GAME,
                stake=stake,
                net=net,
                balance_after=balance,
                tax=tax,
                details=(("won", int(won)), ("started", int(self.started_at))),
            )

    async def shutdown(self) -> None:
        """Cierre ordenado: con la grada abierta se devuelve lo apostado a todos."""
        async with self._lock:
            if self.phase is Phase.LOBBY:
                for member in self.members:
                    await self.cog.pay(self.guild_id, member.id, self.stake)
                for user_id, spectator in self.crowd.items():
                    await self.cog.pay(self.guild_id, user_id, spectator.bet.stake)
                self.crowd.clear()
                text = "🛑 Duelo suspendido. Se devuelve lo apostado."
            elif self.phase is Phase.CHALLENGE:
                text = "🛑 Reto cancelado: el reñidero cierra."
            else:
                self.phase = Phase.DONE
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        if self.prepared is not None:
            self.prepared.cancel()
        await self.edit(content=None, embed=self.embed(text), view=None, attachments=[])


# -- Cog ---------------------------------------------------------------------------------


class Gallos(commands.Cog, name="Gallos"):
    """Peleas de gallos contra la casa y entre miembros, con los yapdollars de la economía."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: GallosRepository,
        casino_channel_ids: frozenset[int] = frozenset(),
        rng: random.Random | None = None,
        renderer: GallosScene | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.renderer = renderer or GallosScene()
        self.sleep = sleep
        self.wall_clock = wall_clock
        self.panels: set[HousePanel] = set()
        self.duels: set[Duel] = set()
        #: Quién está en un reto o un duelo (servidor, miembro): uno a la vez.
        self.busy: set[tuple[int, int]] = set()

    async def cog_unload(self) -> None:
        """Al apagar, devuelve lo apostado en las gradas abiertas y cierra el pintor."""
        for duel in list(self.duels):
            try:
                await duel.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar un duelo de gallos al apagar")
            if duel.task is not None:
                duel.task.cancel()
        self.duels.clear()
        for panel in list(self.panels):
            panel.stop()
        self.panels.clear()
        await self.renderer.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las fichas de los gallos del servidor que deja el bot."""
        await self.repository.delete_guild_data(guild.id)

    def release(self, duel: Duel) -> None:
        """Libera a los dos dueños de un duelo terminado."""
        self.duels.discard(duel)
        for member in duel.members:
            self.busy.discard((duel.guild_id, member.id))

    async def pay(self, guild_id: int, user_id: int, amount: int) -> BetSettlement | None:
        """Paga lo devuelto por una pelea (`pay_winnings`, 0 si pierde) y ajusta el IRPF.

        Tratamiento fiscal: juego (ver la docstring del módulo). `None` si el
        premio pasaría del saldo máximo y no se pudo pagar.
        """
        try:
            return await self.economy.pay_winnings(guild_id, user_id, game=GAME, amount=amount)
        except BalanceLimitError:
            logger.warning("Premio de gallos por encima del saldo máximo; no se paga.")
            return None

    async def shout(self, channel: object, text: str, user: discord.abc.User) -> None:
        """Anuncia en el canal las victorias gordas."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar una pelea de gallos", exc_info=True)

    async def _corner(self, guild_id: int, member: discord.abc.User) -> Owner:
        """El dueño con la ficha de su gallo y la skin que lleva."""
        await tienda.grant_free_skins(self.bot, guild_id, member.id, SKIN_GAME)
        record = await self.repository.get(guild_id, member.id)
        return Owner(member, record, tienda.skin_of(self.bot, guild_id, member.id, SKIN_GAME))

    async def gallo_embed(self, guild_id: int, member: discord.abc.User) -> discord.Embed:
        """La sección 🐓 Gallo de `perfil`: la ficha del gallo de un miembro."""
        record = await self.repository.get(guild_id, member.id)
        skin = tienda.skin_of(self.bot, guild_id, member.id, SKIN_GAME)
        ranking = await self.repository.ranking(guild_id, limit=100)
        place = next((i for i, (uid, _r) in enumerate(ranking, 1) if uid == member.id), None)
        lines = [
            f"### 🐓 {record.name}",
            f"Aspecto: {skin.label if skin else '🐓 Gallo colorado (de serie)'}",
            record_line(record),
        ]
        if record.fights:
            lines.append(
                f"{record.losses} derrota{'s' if record.losses != 1 else ''} · "
                f"{round(100 * record.wins / record.fights)} % de victorias"
            )
        if record.day_streak > 1:
            lines.append(f"📅 {record.day_streak} días seguidos peleando")
        if place is not None:
            lines.append(f"🏟️ Puesto {place} del reñidero del servidor")
        lines.append("-# `gallos` para pelear · ✏️ para bautizarlo · 🎨 para cambiarle el aspecto")
        return discord.Embed(
            title=f"🐓 El gallo de {member.display_name}",
            description="\n".join(lines),
            color=COLOR_IDLE,
        )

    # -- gallos -----------------------------------------------------------------------

    async def _gallos_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        rival: discord.abc.User | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Lógica compartida de `/gallos` y `.gallos`: abre el reñidero o reta a alguien.

        Returns:
            Si ha retado a alguien (para el aviso de la Renta del comando).
        """
        if guild is None:
            await send_error("Las peleas de gallos solo se hacen dentro de un servidor.")
            return False
        if error := casino_channel_error(self.casino_channel_ids, channel, "El reñidero"):
            await send_error(error)
            return False
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = (
                parse_amount(amount_text, balance)
                if amount_text
                else max(1, min(DEFAULT_STAKE, balance))
            )
        except ValueError as error:
            await send_error(str(error))
            return False
        if balance <= 0 or stake > balance:
            await send_error(insufficient_text(balance, stake))
            return False
        if rival is None:
            await self._open_panel(guild.id, channel, user, stake, balance, send)
            return False
        if rival.id == user.id:
            await send_error("No puedes retarte a ti mismo. Para eso está el espejo.")
            return False
        if rival.bot:
            await send_error("Los bots no tienen gallo. Pelea contra la casa con `gallos`.")
            return False
        for member in (user, rival):
            if (guild.id, member.id) in self.busy:
                await send_error(f"{member.display_name} ya está en un duelo de gallos.")
                return False
        red = await self._corner(guild.id, user)
        blue = await self._corner(guild.id, rival)
        duel = Duel(self, guild_id=guild.id, channel=channel, red=red, blue=blue, stake=stake)
        self.busy.update({(guild.id, user.id), (guild.id, rival.id)})
        self.duels.add(duel)
        try:
            await duel.open(send)
        except Exception:
            self.release(duel)
            raise
        await logros.track(
            self.bot, guild.id, user, channel, StatDelta(add={"gallos_challenges": 1})
        )
        return True

    async def _open_panel(
        self,
        guild_id: int,
        channel: object,
        user: discord.abc.User,
        stake: int,
        balance: int,
        send: Callable[..., Awaitable[discord.Message]],
    ) -> None:
        panel = HousePanel(self, guild_id=guild_id, owner=user, stake=stake)
        panel.channel = channel
        panel.balance = balance
        panel.record = (await self._corner(guild_id, user)).record
        png = await panel.poster()
        panel.rebuild()
        panel.message = await send(
            embed=panel.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=panel
        )
        self.panels.add(panel)

    @app_commands.command(
        name="gallos",
        description="Peleas de gallos: contra los de la casa o reta al gallo de alguien.",
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto 100)",
        rival="Persona a la que retar a un duelo (si no, peleas contra la casa)",
    )
    @app_commands.guild_only()
    async def gallos(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        rival: discord.Member | None = None,
    ) -> None:
        """Abre tu reñidero o reta a alguien.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. Contra
        la casa cobra al pelear; en un duelo, a los dos al aceptar.
        """
        responder = InteractionResponder(interaction)

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        challenged = await self._gallos_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            rival=rival,
            send=send,
            send_error=responder.send_error,
        )
        if challenged:
            # El que reta recibe aquí su resultado privado del duelo.
            for duel in self.duels:
                if duel.owner(Corner.RED).member.id == interaction.user.id:
                    duel.interactions[interaction.user.id] = interaction

    @commands.command(name="gallos")
    @commands.guild_only()
    async def gallos_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.gallos`, `.gallos 500`, `.gallos @alguien 500`.

        La cantidad y la mención van en cualquier orden.
        """
        responder = ContextResponder(ctx)
        rival = next((m for m in ctx.message.mentions if m.id != ctx.author.id), None)
        amounts = [a for a in args if not (a.startswith("<@") and a.endswith(">"))]
        if len(amounts) > 1:
            await responder.send_error("No entiendo tanto. Ejemplo: `.gallos @alguien 500`.")
            return

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._gallos_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amounts[0] if amounts else None,
            rival=rival,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y las fichas de los gallos del bot."""
    await bot.add_cog(
        Gallos(
            bot,
            economy=bot.economy,
            repository=bot.gallos,
            casino_channel_ids=bot.casino_channel_ids,
        )
    )
