"""Tragaperras: `slots`, una máquina de 3 rodillos con botones y bote común.

Cada jugador abre su propia máquina, un mensaje con botones que solo él puede
pulsar. Las reglas (rodillos, premios, giros gratis, máquina caliente) viven
en `bot.services.slots`; el dibujo, en `bot.services.slots_render`; el dinero,
en `EconomyService.play_slots`. Este cog solo une las piezas y pinta.

Botones:

- 🎰 **Tirar**: cobra, gira y paga. Con giros gratis pendientes, juega uno.
- 🔁 **Auto ×10**: diez tiradas seguidas con un solo resumen y una sola imagen.
- ⚡ **Turbo**: sin animación, solo la imagen final (más rápido y casi sin datos).
- **½**, **×2**, 💰 **All-in**: cambian la apuesta. 📋 **Premios**: la tabla.

La animación es un GIF que se monta en cada tirada (~0,1 s de CPU fuera del
event loop y ~150 KB). Al acabar se cambia por el PNG final, como en la ruleta.

El calor de la máquina (`bot.services.slots.next_heat`) se guarda por
miembro en memoria: cerrar la máquina no lo borra, reiniciar el bot sí.

Si `CASINO_CHANNEL_IDS` está configurado, la máquina solo se abre en esos
canales. Permisos que necesita el bot en el canal: enviar mensajes, insertar
enlaces (embeds) y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import StatDelta, casino_stats, slots_stats
from bot.services.economy import (
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    SlotsSettlement,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.slots import (
    FREE_SPINS,
    HEAT_MAX,
    HOT_MULTIPLIER,
    POT_SEED,
    SCATTER,
    SYMBOLS,
    Kind,
    SlotMachine,
    Spin,
    heat_bar,
    line_payout,
    next_heat,
    paytable_lines,
    pot_share,
)
from bot.services.slots_render import SlotsMedia, SlotsRenderer
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "tragaperras"
#: Apuesta por defecto al abrir la máquina sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la máquina se cierra.
MACHINE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en empezar el GIF.
REVEAL_MARGIN_SECONDS = 0.4
#: Tiradas de Auto.
AUTO_SPINS = 10
#: A partir de cuántas veces la apuesta se anuncia el premio en el canal.
SHOUT_MULTIPLIER = 50

GIF_NAME = "tragaperras.gif"
PNG_NAME = "tragaperras.png"

COLOR_IDLE = discord.Color.from_rgb(43, 45, 49)
COLOR_SPIN = discord.Color.from_rgb(222, 178, 70)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_HOT = discord.Color.from_rgb(255, 110, 40)

# Textos en el tono de Jovani Vázquez: alegre, exagerado y cariñoso.
JACKPOT_LINES = ("🎤🎤🎤 ¡JOVANAZO!", "🎤🎤🎤 ¡EL BOTE ES TUYO!", "🎤🎤🎤 ¡WEPAAA!")
BIG_WIN_LINES = ("💥 ¡QUÉ LOCURA!", "💥 ¡REVIENTAS LA MÁQUINA!", "💥 ¡AY, BENDITO, QUÉ PREMIO!")
WIN_LINES = ("¡Wepa!", "¡Eso es!", "¡Cobras, mi amor!", "¡Tilín, tilín!", "¡Acho, qué bueno!")
SMALL_WIN_LINES = ("🍒 ¡Premio!", "🍒 ¡Algo cae!", "🍒 ¡Tilín!")
NEAR_MISS_LINES = (
    "¡Ay, bendito! Por un pelo.",
    "¡Uff! Lo tenías ahí.",
    "Casi, casi… la próxima es la buena.",
)
LOSS_LINES = (
    "La próxima es la buena.",
    "Nada, mi amor. Otra.",
    "La máquina se hace la difícil.",
    "Calentando motores…",
)


# -- Presentación -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SlotsPlay:
    """Una tirada ya cobrada y pagada, lista para enseñarse.

    Attributes:
        stake: Apuesta de la tirada (la que activó los giros gratis si `free`).
        payout: Lo que ha devuelto la línea.
        heat: Calor de la máquina tras la tirada.
        session_spins: Tiradas en esta máquina, contando esta.
    """

    spin: Spin
    stake: int
    free: bool
    hot: bool
    payout: int
    settlement: SlotsSettlement
    media: SlotsMedia
    heat: int
    session_spins: int

    @property
    def jackpot(self) -> int:
        """Lo que se ha llevado del bote."""
        return self.settlement.jackpot

    @property
    def paid_stake(self) -> int:
        """Lo que ha salido del bolsillo: nada en un giro gratis."""
        return 0 if self.free else self.stake

    @property
    def won(self) -> int:
        """Todo lo cobrado: línea más bote."""
        return self.payout + self.jackpot

    @property
    def net(self) -> int:
        """Ganancia (positiva) o pérdida (negativa) de la tirada."""
        return self.won - self.paid_stake

    @property
    def balance(self) -> int:
        """Saldo tras la tirada, con el IRPF ya ajustado."""
        return self.settlement.bet.balance


def result_text(play: SlotsPlay, rng: random.Random | None = None) -> str:
    """Bloque grande con lo que ha pasado en la tirada.

    Cuando la línea devuelve menos de lo apostado, se celebra el premio
    ("¡Premio! Cobras 50 Y$") y no se dice que en realidad se pierde: es lo
    que hacen las máquinas de verdad. Lo cobrado es real; el saldo lo dice todo.
    """
    rng = rng or random.Random()
    spin = play.spin
    lines: list[str] = []
    if play.jackpot:
        lines.append(f"# {rng.choice(JACKPOT_LINES)}")
        lines.append(f"## +{format_amount(play.jackpot)} del bote")
        if play.payout:
            lines.append(f"Y además la línea paga {format_amount(play.payout)}.")
    elif play.stake and play.payout >= 20 * play.stake:
        lines.append(f"# {rng.choice(BIG_WIN_LINES)}")
        lines.append(f"## +{format_amount(play.net)}")
    elif play.net > 0:
        lines.append(f"# {rng.choice(WIN_LINES)} +{format_amount(play.net)}")
    elif play.payout > 0:
        lines.append(f"## {rng.choice(SMALL_WIN_LINES)} Cobras {format_amount(play.payout)}")
    elif spin.near_miss:
        lines.append(f"## {rng.choice(NEAR_MISS_LINES)}")
        if play.paid_stake:
            lines.append(f"-{format_amount(play.paid_stake)}")
    elif play.paid_stake:
        lines.append(f"## -{format_amount(play.paid_stake)} · {rng.choice(LOSS_LINES)}")
    else:
        lines.append("## Nada en este giro gratis.")

    if spin.kind == Kind.THREE and spin.symbol is not None and not play.jackpot:
        emoji = SYMBOLS[spin.symbol].emoji
        lines.append(f"{emoji} {emoji} {emoji}")
    if play.hot:
        lines.append(f"🔥 Tirada caliente: la línea paga ×{HOT_MULTIPLIER}.")
    if play.free:
        lines.append("🎟️ Giro gratis.")
    if spin.triggers_free_spins:
        lines.append(f"### 🎟️🎟️🎟️ ¡GIROS GRATIS! +{FREE_SPINS}")
    elif spin.scatters == 2:
        lines.append(f"{SYMBOLS[SCATTER].emoji}{SYMBOLS[SCATTER].emoji} Te faltó una entrada…")
    return "\n".join(lines)


def auto_text(plays: list[SlotsPlay], stopped: str | None = None) -> str:
    """Resumen de una ronda de Auto: cuántas, cuántas con premio, neto y la mejor."""
    net = sum(p.net for p in plays)
    paid = sum(1 for p in plays if p.won > 0)
    best = max(plays, key=lambda p: p.net)
    sign = "+" if net > 0 else "-" if net < 0 else "±"
    lines = [
        f"# 🔁 {len(plays)} tiradas · {sign}{format_amount(abs(net))}",
        f"{paid} con premio · apostado {format_amount(sum(p.paid_stake for p in plays))}",
    ]
    if best.net > 0:
        line = " ".join(SYMBOLS[s].emoji for s in best.spin.line)
        lines.append(f"Mejor: {line} +{format_amount(best.net)}")
    if any(p.jackpot for p in plays):
        lines.append(f"# 🎤🎤🎤 ¡JOVANAZO! +{format_amount(sum(p.jackpot for p in plays))}")
    if any(p.spin.triggers_free_spins for p in plays):
        lines.append("🎟️ ¡Han salido giros gratis!")
    if stopped:
        lines.append(f"-# {stopped}")
    return "\n".join(lines)


def tax_note(delta: int, plays: list[SlotsPlay]) -> str | None:
    """Línea de IRPF de una tirada o de una ronda de Auto (suma de ajustes)."""
    if not plays:
        return None
    if len(plays) == 1:
        return gambling_tax_line(plays[0].settlement.bet)
    last = plays[-1].settlement.bet
    if delta > 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(delta)} de IRPF. "
            f"Hoy vas {format_amount(last.day_net)} arriba."
        )
    if delta < 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} te devuelve {format_amount(-delta)}: "
            "tus pérdidas de hoy compensan lo que habías ganado."
        )
    return None


def machine_embed(
    *,
    owner: str,
    balance: int,
    stake: int,
    pot: int,
    heat: int,
    free_spins: int,
    free_stake: int,
    turbo: bool,
    text: str | None = None,
    won: bool | None = None,
    last_jackpot: str | None = None,
) -> discord.Embed:
    """Embed de la máquina parada: al abrirla o tras una tirada."""
    if text is None:
        description = (
            "Pulsa 🎰 **Tirar**. Paga la fila del medio.\n"
            "📋 **Premios** para ver la tabla. 🎤 🎤 🎤 se lleva el **bote**."
        )
    else:
        description = text
    if balance == 0 and not free_spins:
        description += "\n\n**Estás a cero.** `imv` te recarga."
    hot = heat >= HEAT_MAX
    if won is None:
        color = COLOR_HOT if hot else COLOR_IDLE
    else:
        color = COLOR_WIN if won else COLOR_HOT if hot else COLOR_LOSS
    embed = discord.Embed(title="🎰 Tragaperras", description=description, color=color)
    embed.add_field(name="Saldo", value=format_amount(balance))
    embed.add_field(name="Apuesta", value=format_amount(stake))
    embed.add_field(name="💰 Bote", value=f"**{format_amount(pot)}**")
    if hot:
        embed.add_field(
            name="🔥 ¡Máquina caliente!",
            value=f"La siguiente tirada paga ×{HOT_MULTIPLIER}.",
            inline=False,
        )
    else:
        embed.add_field(name="Calor", value=heat_bar(heat), inline=False)
    if free_spins:
        embed.add_field(
            name="🎟️ Giros gratis",
            value=f"Te quedan **{free_spins}** a {format_amount(free_stake)}.",
            inline=False,
        )
    embed.set_image(url=f"attachment://{PNG_NAME}")
    footer = f"Máquina de {owner}"
    if turbo:
        footer += " · ⚡ Turbo"
    if last_jackpot:
        footer += f" · Último bote: {last_jackpot}"
    embed.set_footer(text=footer)
    return embed


def spinning_embed(*, owner: str, stake: int, free: bool, hot: bool, pot: int) -> discord.Embed:
    """Embed mientras giran los rodillos."""
    lines = ["# 🌀 ¡Girando!"]
    lines.append(f"🎟️ Giro gratis a {format_amount(stake)}" if free else format_amount(stake))
    if hot:
        lines.append(f"🔥 Tirada caliente: ×{HOT_MULTIPLIER}")
    embed = discord.Embed(title="🎰 Tragaperras", description="\n".join(lines), color=COLOR_SPIN)
    embed.add_field(name="💰 Bote", value=f"**{format_amount(pot)}**")
    embed.set_image(url=f"attachment://{GIF_NAME}")
    embed.set_footer(text=f"Máquina de {owner}")
    return embed


def paytable_embed() -> discord.Embed:
    """Tabla de premios (se manda en privado)."""
    return discord.Embed(
        title="📋 Premios de la tragaperras",
        description="Solo paga la fila del medio.\n\n" + "\n".join(paytable_lines()),
        color=COLOR_WIN,
    )


def parse_stake(amount_text: str | None, balance: int) -> int:
    """Apuesta del comando: la indicada (`500`, `2k`, `all`) o la de por defecto.

    Raises:
        ValueError: Con un mensaje mostrable si no se entiende.
    """
    if not amount_text:
        return max(1, min(DEFAULT_STAKE, balance))
    return parse_amount(amount_text, balance)


EditFn = Callable[..., Awaitable[Any]]


# -- Máquina ------------------------------------------------------------------------


class SlotMachineView(discord.ui.View):
    """Máquina de un jugador: un mensaje con botones.

    Guarda la apuesta, el modo turbo, los giros gratis pendientes y las
    tiradas de la sesión. No guarda dinero: el saldo y el bote se leen y se
    cambian siempre a través de la economía.
    """

    def __init__(self, cog: Slots, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=MACHINE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.turbo = cog.turbo_default(guild_id, owner.id)
        self.free_spins = 0
        self.free_stake = 0
        self.session_spins = 0
        self.last_text: str | None = None
        self.last_won: bool | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        self._build_buttons()

    # -- Construcción ---------------------------------------------------------------

    def _add(
        self,
        label_text: str,
        row: int,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        custom_id: str,
    ) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(
            label=label_text, style=style, row=row, custom_id=f"{GAME}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)
        return button

    def _build_buttons(self) -> None:
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        self.spin_button = self._add("🎰 Tirar", 0, self._spin, style=green, custom_id="spin")
        self.auto_button = self._add(
            f"🔁 Auto ×{AUTO_SPINS}", 0, self._auto, style=blue, custom_id="auto"
        )
        self.turbo_button = self._add("⚡ Turbo", 0, self._toggle_turbo, custom_id="turbo")
        self._add("½", 1, self._halve, custom_id="half")
        self._add("×2", 1, self._double_stake, custom_id="x2")
        self._add("💰 All-in", 1, self._all_in, custom_id="allin")
        self._add("📋 Premios", 1, self._paytable, custom_id="paytable")
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        """Activa o desactiva los botones y pone al día sus etiquetas."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = not enabled
        if self.free_spins:
            self.spin_button.label = f"🎟️ Giro gratis ({self.free_spins})"
            self.spin_button.style = discord.ButtonStyle.primary
        else:
            self.spin_button.label = f"🎰 Tirar · {format_amount(self.stake)}"
            self.spin_button.style = discord.ButtonStyle.success
        self.turbo_button.label = "⚡ Turbo: sí" if self.turbo else "⚡ Turbo"
        self.turbo_button.style = (
            discord.ButtonStyle.success if self.turbo else discord.ButtonStyle.secondary
        )

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su máquina; al resto se le invita a abrir la suya."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta máquina es de {self.owner.display_name}. Abre la tuya con `slots`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Cierra la máquina y juega los giros gratis que se hayan quedado sin usar."""
        self.cog.machines.discard(self)
        plays = await self.flush_free_spins()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        kwargs: dict[str, Any] = {"view": self}
        if plays:
            summary = auto_text(plays, "Giros gratis jugados al cerrar la máquina.")
            kwargs["embed"] = await self.current_embed(text=summary)
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la tragaperras", exc_info=True)

    async def flush_free_spins(self) -> list[SlotsPlay]:
        """Juega los giros gratis pendientes sin animación; nadie pierde lo ganado.

        Lo usan el cierre por inactividad y el apagado del bot.
        """
        plays: list[SlotsPlay] = []
        while self.free_spins:
            try:
                plays.append(await self._play_one(turbo=True, render=False))
            except Exception:
                logger.exception("No se pudo jugar un giro gratis al cerrar la máquina")
                break
        for play in plays:
            await self._track(play)
        return plays

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño de la máquina."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(
        self, *, balance: int | None = None, pot: int | None = None, text: str | None = None
    ) -> discord.Embed:
        """Embed de reposo con el saldo y el bote actuales."""
        if balance is None:
            balance = await self.balance()
        if pot is None:
            pot = await self.cog.pot(self.guild_id)
        return machine_embed(
            owner=self.owner.display_name,
            balance=balance,
            stake=self.stake,
            pot=pot,
            heat=self.cog.heat(self.guild_id, self.owner.id),
            free_spins=self.free_spins,
            free_stake=self.free_stake,
            turbo=self.turbo,
            text=text if text is not None else self.last_text,
            won=self.last_won,
            last_jackpot=await self.cog.last_jackpot_text(self.guild_id),
        )

    async def _play_one(self, *, turbo: bool, render: bool = True) -> SlotsPlay:
        """Una tirada: gratis si quedan giros gratis, pagada si no.

        Raises:
            InsufficientFundsError, BalanceLimitError: Como `Slots.play`.
        """
        free = self.free_spins > 0
        stake = self.free_stake if free else self.stake
        play = await self.cog.play(
            self.guild_id,
            self.owner.id,
            stake=stake,
            free=free,
            turbo=turbo,
            render=render,
            session_spins=self.session_spins + 1,
        )
        self.session_spins += 1
        if free:
            self.free_spins -= 1
        if play.spin.triggers_free_spins:
            self.free_spins += FREE_SPINS
            self.free_stake = stake
        return play

    async def _guarded_play(
        self, interaction: discord.Interaction, *, turbo: bool, render: bool = True
    ) -> SlotsPlay | None:
        """`_play_one` con los errores de saldo convertidos en avisos privados."""
        try:
            return await self._play_one(turbo=turbo, render=render)
        except InsufficientFundsError as error:
            await interaction.response.send_message(
                insufficient_text(error.balance, self.stake), ephemeral=True
            )
        except BalanceLimitError:
            await interaction.response.send_message(
                "La banca no puede pagar tanto. Baja la apuesta.", ephemeral=True
            )
        return None

    async def play(self, interaction: discord.Interaction) -> None:
        """Respuesta a 🎰 Tirar: cobra, gira, enseña y paga."""
        if self._busy:
            # Doble clic mientras gira: se ignora sin mostrar error.
            await interaction.response.defer()
            return
        self._busy = True
        try:
            play = await self._guarded_play(interaction, turbo=self.turbo)
            if play is None:
                return
            self._last_interaction = interaction
            await self.show(
                play,
                first_edit=interaction.response.edit_message,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        await self._track(play)
        await self.cog.shout(play, self.owner, getattr(self.message, "channel", None))

    async def show(self, play: SlotsPlay, *, first_edit: EditFn, final_edit: EditFn) -> None:
        """Enseña la tirada: el GIF y después el PNG final (o solo el PNG en turbo).

        El dinero ya está cobrado y pagado: si Discord falla al editar, el
        saldo sigue siendo correcto.
        """
        if play.media.gif:
            self._set_enabled(False)
            await first_edit(
                embed=spinning_embed(
                    owner=self.owner.display_name,
                    stake=play.stake,
                    free=play.free,
                    hot=play.hot,
                    pot=play.settlement.pot,
                ),
                attachments=[discord.File(io.BytesIO(play.media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(play.media.seconds + REVEAL_MARGIN_SECONDS)
            edit = final_edit
        else:
            edit = first_edit

        text = result_text(play)
        if note := tax_note(play.settlement.bet.tax_delta, [play]):
            text += f"\n{note}"
        if renta_hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id):
            text += f"\n{renta_hint}"
        self.last_text = text
        self.last_won = play.won > 0
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(balance=play.balance, pot=play.settlement.pot),
            attachments=[discord.File(io.BytesIO(play.media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _track(self, play: SlotsPlay) -> None:
        """Logros de la tirada, después de enseñarla (antes destriparía el resultado)."""
        delta = slots_stats(
            play.spin,
            stake=play.stake,
            payout=play.payout,
            jackpot=play.jackpot,
            free=play.free,
            hot=play.hot,
            turbo=not play.media.gif,
            session_spins=play.session_spins,
            when=datetime.now(TIMEZONE),
        )
        delta.merge(
            casino_stats(
                stake=play.paid_stake,
                net=play.net,
                balance_after=play.balance,
                tax_delta=play.settlement.bet.tax_delta,
            )
        )
        await logros.casino_play(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            delta,
            net=play.net,
        )

    async def _spin(self, interaction: discord.Interaction) -> None:
        await self.play(interaction)

    async def _auto(self, interaction: discord.Interaction) -> None:
        """Diez tiradas seguidas sin animación y un solo resumen.

        Para antes si se acaba el dinero o sale el bote. Los giros gratis que
        salgan por el camino se juegan dentro de la misma ronda.
        """
        if self._busy:
            await interaction.response.defer()
            return
        self._busy = True
        plays: list[SlotsPlay] = []
        stopped: str | None = None
        try:
            await interaction.response.defer()
            self._last_interaction = interaction
            for _ in range(AUTO_SPINS):
                try:
                    plays.append(await self._play_one(turbo=True, render=False))
                except InsufficientFundsError:
                    stopped = "Parado: no te llega para otra tirada."
                    break
                except BalanceLimitError:
                    stopped = "Parado: la banca no puede pagar tanto."
                    break
                if plays[-1].jackpot:
                    stopped = "Parado: ¡ha salido el bote!"
                    break
            if not plays:
                await interaction.followup.send(
                    insufficient_text(await self.balance(), self.stake), ephemeral=True
                )
                return
            last = plays[-1]
            png = await asyncio.to_thread(
                self.cog.renderer.still_png,
                last.spin.stops,
                highlight=last.spin.pay_halves > 0 or last.spin.is_jackpot,
            )
            text = auto_text(plays, stopped)
            if note := tax_note(sum(p.settlement.bet.tax_delta for p in plays), plays):
                text += f"\n{note}"
            if renta_hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id):
                text += f"\n{renta_hint}"
            self.last_text = text
            self.last_won = sum(p.net for p in plays) > 0
            self._set_enabled(True)
            await interaction.edit_original_response(
                embed=await self.current_embed(balance=last.balance, pot=last.settlement.pot),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        for play in plays:
            await self._track(play)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            StatDelta(add={"slots_auto": 1}),
        )
        for play in plays:
            await self.cog.shout(play, self.owner, getattr(self.message, "channel", None))

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        """Actualiza la máquina (apuesta, turbo) sin tocar la imagen."""
        self._set_enabled(True)
        await interaction.response.edit_message(
            embed=await self.current_embed(balance=balance), view=self
        )
        self._last_interaction = interaction

    async def _toggle_turbo(self, interaction: discord.Interaction) -> None:
        self.turbo = not self.turbo
        self.cog.set_turbo_default(self.guild_id, self.owner.id, self.turbo)
        await self._refresh(interaction)

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        balance = await self.balance()
        # Si el doble no cabe, se queda en todo el saldo: es lo que se busca.
        self.stake = max(1, min(self.stake * 2, balance))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        balance = await self.balance()
        if balance == 0:
            await interaction.response.send_message(insufficient_text(0), ephemeral=True)
            return
        self.stake = balance
        await self._refresh(interaction, balance)

    async def _paytable(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=paytable_embed(), ephemeral=True)


# -- Cog ----------------------------------------------------------------------------


class Slots(commands.Cog, name="Tragaperras"):
    """La tragaperras del casino, con bote común por servidor."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: SlotsRenderer | None = None,
        machine: SlotMachine | None = None,
        casino_channel_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.renderer = renderer or SlotsRenderer()
        self.machine = machine or SlotMachine()
        self.casino_channel_ids = casino_channel_ids
        # Calor y turbo por (servidor, miembro). Crecen como mucho hasta el
        # número de miembros que han jugado; se pierden al reiniciar.
        self._heat: dict[tuple[int, int], int] = {}
        self._turbo: dict[tuple[int, int], bool] = {}
        # Máquinas abiertas: para jugar sus giros gratis si el bot se apaga.
        self.machines: set[SlotMachineView] = set()
        self._warm_task: asyncio.Task[bytes] | None = None

    async def cog_load(self) -> None:
        """Prepara las piezas del dibujo en segundo plano (~0,05 s de CPU)."""
        self._warm_task = asyncio.create_task(
            asyncio.to_thread(self.renderer.still_png, (0, 0, 0)), name="slots-warm-up"
        )

    async def cog_unload(self) -> None:
        """Al apagar, juega los giros gratis pendientes para no quedarse con ellos."""
        if self._warm_task is not None:
            self._warm_task.cancel()
        for view in list(self.machines):
            try:
                await view.flush_free_spins()
            except Exception:
                logger.exception("No se pudieron jugar los giros gratis al apagar")
            view.stop()
        self.machines.clear()

    # -- Estado por miembro ----------------------------------------------------------

    def heat(self, guild_id: int, user_id: int) -> int:
        """Calor de la máquina de un miembro."""
        return self._heat.get((guild_id, user_id), 0)

    def turbo_default(self, guild_id: int, user_id: int) -> bool:
        """Si el miembro dejó el turbo puesto la última vez."""
        return self._turbo.get((guild_id, user_id), False)

    def set_turbo_default(self, guild_id: int, user_id: int, turbo: bool) -> None:
        """Recuerda el turbo para la próxima máquina del miembro."""
        self._turbo[(guild_id, user_id)] = turbo

    async def pot(self, guild_id: int) -> int:
        """Bote común del servidor."""
        return await self.economy.slots_pot(guild_id, seed=POT_SEED)

    async def last_jackpot_text(self, guild_id: int) -> str | None:
        """`Nombre, 85.000 Y$` del último jackpot del servidor, si lo hay."""
        record = await self.economy.last_jackpot(guild_id)
        if record is None:
            return None
        guild = self.bot.get_guild(guild_id) if hasattr(self.bot, "get_guild") else None
        member = guild.get_member(record.user_id) if isinstance(guild, discord.Guild) else None
        name = member.display_name if member is not None else "alguien"
        return f"{name}, {format_amount(record.amount)}"

    # -- Tirada ---------------------------------------------------------------------

    async def play(
        self,
        guild_id: int,
        user_id: int,
        *,
        stake: int,
        free: bool,
        turbo: bool,
        render: bool = True,
        session_spins: int = 1,
    ) -> SlotsPlay:
        """Juega una tirada: decide los rodillos y mueve el dinero de forma atómica.

        Los rodillos se deciden antes de cobrar, pero solo se enseñan si el
        cobro sale bien. El calor solo cambia si la tirada se ha pagado.

        Args:
            stake: Apuesta; en un giro gratis, la que lo activó (no se cobra).
            render: `False` para no dibujar nada (Auto y giros al cerrar).

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        key = (guild_id, user_id)
        spin = self.machine.spin(free=free)
        hot = self.heat(guild_id, user_id) >= HEAT_MAX
        payout = line_payout(spin, stake, hot=hot)
        settlement = await self.economy.play_slots(
            guild_id,
            user_id,
            game=GAME,
            stake=0 if free else stake,
            payout=payout,
            share=0 if free else pot_share(stake),
            jackpot=spin.is_jackpot,
            seed=POT_SEED,
        )
        heat = next_heat(
            self.heat(guild_id, user_id), paid=payout > 0 or spin.is_jackpot, was_hot=hot
        )
        self._heat[key] = heat
        if render:
            media = await asyncio.to_thread(self.renderer.render, spin, turbo=turbo)
        else:
            media = SlotsMedia(gif=b"", png=b"", seconds=0.0)
        return SlotsPlay(
            spin=spin,
            stake=stake,
            free=free,
            hot=hot,
            payout=payout,
            settlement=settlement,
            media=media,
            heat=heat,
            session_spins=session_spins,
        )

    async def shout(self, play: SlotsPlay, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal el bote y los premios enormes, para que se vea."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        if play.jackpot:
            text = (
                f"📣 🎤🎤🎤 ¡{user.mention} ha sacado el **JOVANAZO**! Se lleva "
                f"**{format_amount(play.jackpot)}** del bote. {TAX_COLLECTOR} ya se está "
                "relamiendo."
            )
        elif play.stake and play.net >= SHOUT_MULTIPLIER * play.stake:
            line = " ".join(SYMBOLS[s].emoji for s in play.spin.line)
            text = f"📣 {line} ¡{user.mention} acaba de ganar **{format_amount(play.net)}**!"
        else:
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio de la tragaperras", exc_info=True)

    # -- slots ----------------------------------------------------------------------

    async def _slots_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/slots` y `.slots`: abre la máquina."""
        if guild is None:
            await send_error("La tragaperras solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "La tragaperras"):
            await send_error(error)
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = parse_stake(amount_text, balance)
        except ValueError as error:
            await send_error(str(error))
            return
        if stake > balance:
            await send_error(insufficient_text(balance))
            return

        view = SlotMachineView(self, guild_id=guild.id, owner=user, stake=stake)
        stops = self.machine.spin().stops
        png = await asyncio.to_thread(self.renderer.still_png, stops)
        view.message = await send(
            embed=await view.current_embed(balance=balance),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=view,
        )
        self.machines.add(view)

    @app_commands.command(name="slots", description="Tragaperras con bote común, a botones.")
    @app_commands.describe(cantidad="Apuesta por tirada: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def slots(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Abre una tragaperras con botones.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. Las
        tiradas mueven yapdollars a través de la economía del bot.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._slots_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )

    @commands.command(name="slots")
    @commands.guild_only()
    async def slots_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.slots` o `.slots 500`."""

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._slots_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Slots(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
