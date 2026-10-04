"""Pachinko: `pachinko`, una máquina japonesa de bolas con botones.

Cada jugador abre su propia máquina, un mensaje con botones que solo él puede
pulsar. Las reglas (clavos, bolsillos, sorteo, reach y rush) viven en
`bot.services.pachinko`; el dibujo, en `bot.services.pachinko_render`; el
dinero, en `EconomyService.settle_bet`. Este cog solo une las piezas y pinta.

Botones:

- 🎯 **Lanzar**: cobra la tanda, lanza las 10 bolas y paga lo que devuelvan.
- 🔁 **Ráfaga ×5**: cinco tandas seguidas con un solo resumen y una sola imagen.
- ⚡ **Turbo**: sin animación, solo la imagen final (más rápido y casi sin datos).
- **½**, **×2**, 💰 **All-in**: cambian la apuesta. 📋 **Premios**: la tabla.

La animación es un GIF que se monta en cada tanda (~0,5 s de CPU fuera del
event loop y 130-330 KB). Al acabar se cambia por el PNG final, como en la
tragaperras. El turbo se recuerda por miembro en memoria hasta reiniciar.

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
from bot.services.achievements import StatDelta, casino_stats, pachinko_stats
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pachinko import (
    BALLS,
    FEVER_BALLS,
    MIN_STAKE,
    Kind,
    PachinkoMachine,
    Volley,
    hold_bar,
    payout,
    paytable_lines,
)
from bot.services.pachinko_render import PachinkoMedia, PachinkoRenderer
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "pachinko"
#: Apuesta por defecto al abrir la máquina sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la máquina se cierra.
MACHINE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en empezar el GIF.
REVEAL_MARGIN_SECONDS = 0.4
#: Tandas de la Ráfaga.
BURST_VOLLEYS = 5
#: A partir de cuántas veces la apuesta se anuncia el premio en el canal.
SHOUT_MULTIPLIER = 20
#: Premios gordos encadenados que se anuncian en el canal aunque paguen poco.
SHOUT_RUSH = 5

GIF_NAME = "pachinko.gif"
PNG_NAME = "pachinko.png"

COLOR_IDLE = discord.Color.from_rgb(34, 16, 64)
COLOR_SPIN = discord.Color.from_rgb(255, 60, 200)
COLOR_WIN = discord.Color.from_rgb(255, 200, 60)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)

# Textos en el tono de Jovani Vázquez: alegre, exagerado y cariñoso.
SUPER_LINES = ("7️⃣7️⃣7️⃣ ¡SUPER RUSH, MI AMOR!", "7️⃣7️⃣7️⃣ ¡SE ROMPIÓ LA MÁQUINA!", "7️⃣7️⃣7️⃣ ¡JOVANAZO JAPONÉS!")
RUSH_LINES = ("🔥 ¡RUSH! ¡Esto no para!", "🔥 ¡RUSH, bendito!", "🔥 ¡Encadenando, wepa!")
ATARI_LINES = ("🎉 ¡ATARI!", "🎉 ¡ATARI, papi!", "🎉 ¡Se abrió la compuerta!")
WIN_LINES = ("¡Wepa!", "¡Eso es!", "¡Cobras, mi amor!", "¡Lluvia de bolas!")
SMALL_WIN_LINES = ("🎯 ¡Premio!", "🎯 ¡Algo cae!", "🎯 ¡Tilín, tilín!")
REACH_LINES = (
    "👀 ¡REACH!… y nada. Por un pelo.",
    "👀 ¡Ay, bendito! El del centro se pasó.",
    "👀 Casi, casi… la próxima es la buena.",
)
LOSS_LINES = (
    "Las bolas se fueron por el desagüe.",
    "Nada, mi amor. Otra.",
    "La máquina se hace la difícil.",
    "Calentando la compuerta…",
)


# -- Presentación -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PachinkoPlay:
    """Una tanda ya cobrada y pagada, lista para enseñarse.

    Attributes:
        won: Lo que ha devuelto la tanda (apuesta incluida).
        session_volleys: Tandas en esta máquina, contando esta.
    """

    volley: Volley
    stake: int
    won: int
    settlement: BetSettlement
    media: PachinkoMedia
    session_volleys: int

    @property
    def net(self) -> int:
        """Ganancia (positiva) o pérdida (negativa) de la tanda."""
        return self.won - self.stake

    @property
    def balance(self) -> int:
        """Saldo tras la tanda, con el IRPF ya ajustado."""
        return self.settlement.balance


def _digits(volley: Volley) -> str | None:
    best = volley.best
    if best is None:
        return None
    return " ".join(str(d) for d in best.digits)


def result_text(play: PachinkoPlay, rng: random.Random | None = None) -> str:
    """Bloque grande con lo que ha pasado en la tanda.

    Como en la tragaperras, una tanda que devuelve algo se celebra aunque
    devuelva menos de lo apostado: lo cobrado es real y el saldo lo dice todo.
    """
    rng = rng or random.Random()
    volley = play.volley
    best = volley.best
    lines: list[str] = []
    if best is not None:
        if best.kind == Kind.SUPER:
            lines.append(f"# {rng.choice(SUPER_LINES)}")
        elif best.kind == Kind.RUSH:
            lines.append(f"# {rng.choice(RUSH_LINES)}")
        else:
            lines.append(f"# {rng.choice(ATARI_LINES)}")
        lines.append(f"## {_digits(volley)} · +{format_amount(play.net)}")
        if volley.jackpots > 1:
            lines.append(f"**×{volley.jackpots}** premios gordos encadenados.")
    elif play.net > 0:
        lines.append(f"# {rng.choice(WIN_LINES)} +{format_amount(play.net)}")
    elif play.won > 0:
        lines.append(f"## {rng.choice(SMALL_WIN_LINES)} Cobras {format_amount(play.won)}")
    elif any(d.reach for d in volley.draws):
        lines.append(f"## {rng.choice(REACH_LINES)}")
        lines.append(f"-{format_amount(play.stake)}")
    else:
        lines.append(f"## -{format_amount(play.stake)} · {rng.choice(LOSS_LINES)}")

    if best is None and any(d.reach for d in volley.draws) and play.won > 0:
        lines.append(rng.choice(REACH_LINES))
    detail = f"🎯 {volley.total_balls} bolas de vuelta · 🌀 {volley.starts} en START"
    if volley.draws:
        detail += f" · {len(volley.draws)} sorteo{'s' if len(volley.draws) > 1 else ''}"
    lines.append(detail)
    if volley.wasted:
        lines.append(
            f"Reserva llena: {volley.wasted} bola{'s' if volley.wasted > 1 else ''} al limbo."
        )
    if volley.corners:
        lines.append("⭐ ¡Bola en la esquina!")
    return "\n".join(lines)


def burst_text(plays: list[PachinkoPlay], stopped: str | None = None) -> str:
    """Resumen de una Ráfaga: cuántas tandas, neto, ataris y la mejor."""
    net = sum(p.net for p in plays)
    sign = "+" if net > 0 else "-" if net < 0 else "±"
    jackpots = sum(p.volley.jackpots for p in plays)
    lines = [
        f"# 🔁 {len(plays)} tandas · {sign}{format_amount(abs(net))}",
        f"Apostado {format_amount(sum(p.stake for p in plays))} · "
        f"{sum(p.volley.total_balls for p in plays)} bolas de vuelta",
    ]
    if jackpots:
        best = max(plays, key=lambda p: p.volley.jackpots)
        lines.append(f"## 🎉 {_digits(best.volley)} · ×{jackpots} premios gordos")
    if stopped:
        lines.append(f"-# {stopped}")
    return "\n".join(lines)


def tax_note(plays: list[PachinkoPlay]) -> str | None:
    """Línea de IRPF de una tanda o de una Ráfaga (suma de ajustes)."""
    if not plays:
        return None
    if len(plays) == 1:
        return gambling_tax_line(plays[0].settlement)
    delta = sum(p.settlement.tax_delta for p in plays)
    if delta > 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(delta)} de IRPF. "
            f"Hoy vas {format_amount(plays[-1].settlement.day_net)} arriba."
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
    turbo: bool,
    text: str | None = None,
    won: bool | None = None,
) -> discord.Embed:
    """Embed de la máquina parada: al abrirla o tras una tanda."""
    if text is None:
        description = (
            f"Pulsa 🎯 **Lanzar**: {BALLS} bolas bajan por los clavos.\n"
            "Las que entran en **START** juegan en la pantalla: tres iguales es "
            f"**ATARI** (+{FEVER_BALLS} bolas) y los impares traen **RUSH**."
        )
    else:
        description = text
    if balance < MIN_STAKE:
        description += "\n\n**No te llega para una tanda.** `imv` te recarga."
    color = COLOR_IDLE if won is None else COLOR_WIN if won else COLOR_LOSS
    embed = discord.Embed(title="🌸 Pachinko", description=description, color=color)
    embed.add_field(name="Saldo", value=format_amount(balance))
    embed.add_field(name="Apuesta", value=f"{format_amount(stake)} ({BALLS} bolas)")
    embed.add_field(name="Bola", value=format_amount(stake // BALLS))
    embed.set_image(url=f"attachment://{PNG_NAME}")
    footer = f"Máquina de {owner}"
    if turbo:
        footer += " · ⚡ Turbo"
    embed.set_footer(text=footer)
    return embed


def launching_embed(*, owner: str, stake: int, held: int) -> discord.Embed:
    """Embed mientras caen las bolas."""
    embed = discord.Embed(
        title="🌸 Pachinko",
        description=f"# 🎯 ¡Bolas fuera!\n{format_amount(stake)} · reserva {hold_bar(held)}",
        color=COLOR_SPIN,
    )
    embed.set_image(url=f"attachment://{GIF_NAME}")
    embed.set_footer(text=f"Máquina de {owner}")
    return embed


def paytable_embed() -> discord.Embed:
    """Tabla de premios (se manda en privado)."""
    return discord.Embed(
        title="📋 Premios del pachinko",
        description="\n".join(paytable_lines()),
        color=COLOR_WIN,
    )


def parse_stake(amount_text: str | None, balance: int) -> int:
    """Apuesta del comando: la indicada (`500`, `2k`, `all`) o la de por defecto.

    Raises:
        ValueError: Con un mensaje mostrable si no se entiende o no llega al
            mínimo (una bola tiene que valer al menos 1 Y$).
    """
    if not amount_text:
        return max(MIN_STAKE, min(DEFAULT_STAKE, balance))
    stake = parse_amount(amount_text, balance)
    if stake < MIN_STAKE:
        raise ValueError(f"La tanda mínima es {format_amount(MIN_STAKE)}: una bola por yapdollar.")
    return stake


EditFn = Callable[..., Awaitable[Any]]


# -- Máquina ------------------------------------------------------------------------


class PachinkoView(discord.ui.View):
    """Máquina de un jugador: un mensaje con botones.

    Guarda la apuesta, el modo turbo y las tandas de la sesión. No guarda
    dinero: el saldo se lee y se cambia siempre a través de la economía.
    """

    def __init__(
        self, cog: Pachinko, *, guild_id: int, owner: discord.abc.User, stake: int
    ) -> None:
        super().__init__(timeout=MACHINE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.turbo = cog.turbo_default(guild_id, owner.id)
        self.session_volleys = 0
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
        self.launch_button = self._add("🎯 Lanzar", 0, self._launch, style=green, custom_id="go")
        self._add(f"🔁 Ráfaga ×{BURST_VOLLEYS}", 0, self._burst, style=blue, custom_id="burst")
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
        self.launch_button.label = f"🎯 Lanzar · {format_amount(self.stake)}"
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
            f"Esta máquina es de {self.owner.display_name}. Abre la tuya con `pachinko`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Desactiva los botones al cerrar la máquina por inactividad."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el pachinko", exc_info=True)

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño de la máquina."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(
        self, *, balance: int | None = None, text: str | None = None
    ) -> discord.Embed:
        """Embed de reposo con el saldo actual."""
        if balance is None:
            balance = await self.balance()
        return machine_embed(
            owner=self.owner.display_name,
            balance=balance,
            stake=self.stake,
            turbo=self.turbo,
            text=text if text is not None else self.last_text,
            won=self.last_won,
        )

    async def _play_one(self, *, turbo: bool, render: bool = True) -> PachinkoPlay:
        """Una tanda con la apuesta actual.

        Raises:
            InsufficientFundsError, BalanceLimitError: Como `Pachinko.play`.
        """
        play = await self.cog.play(
            self.guild_id,
            self.owner.id,
            stake=self.stake,
            turbo=turbo,
            render=render,
            session_volleys=self.session_volleys + 1,
        )
        self.session_volleys += 1
        return play

    async def _launch(self, interaction: discord.Interaction) -> None:
        """Respuesta a 🎯 Lanzar: cobra, lanza, enseña y paga."""
        if self._busy:
            # Doble clic mientras caen las bolas: se ignora sin mostrar error.
            await interaction.response.defer()
            return
        self._busy = True
        try:
            try:
                play = await self._play_one(turbo=self.turbo)
            except InsufficientFundsError as error:
                await interaction.response.send_message(
                    insufficient_text(error.balance, self.stake), ephemeral=True
                )
                return
            except BalanceLimitError:
                await interaction.response.send_message(
                    "La banca no puede pagar tanto. Baja la apuesta.", ephemeral=True
                )
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

    async def show(self, play: PachinkoPlay, *, first_edit: EditFn, final_edit: EditFn) -> None:
        """Enseña la tanda: el GIF y después el PNG final (o solo el PNG en turbo).

        El dinero ya está cobrado y pagado: si Discord falla al editar, el
        saldo sigue siendo correcto.
        """
        if play.media.gif:
            self._set_enabled(False)
            await first_edit(
                embed=launching_embed(
                    owner=self.owner.display_name, stake=play.stake, held=len(play.volley.draws)
                ),
                attachments=[discord.File(io.BytesIO(play.media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(play.media.seconds + REVEAL_MARGIN_SECONDS)
            edit = final_edit
        else:
            edit = first_edit

        text = result_text(play)
        if note := tax_note([play]):
            text += f"\n{note}"
        if renta_hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id):
            text += f"\n{renta_hint}"
        self.last_text = text
        self.last_won = play.won > 0
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(balance=play.balance),
            attachments=[discord.File(io.BytesIO(play.media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _track(self, play: PachinkoPlay) -> None:
        """Logros de la tanda, después de enseñarla (antes destriparía el resultado)."""
        delta = pachinko_stats(
            play.volley,
            stake=play.stake,
            won=play.won,
            turbo=not play.media.gif,
            session_volleys=play.session_volleys,
            when=datetime.now(TIMEZONE),
        )
        delta.merge(
            casino_stats(
                stake=play.stake,
                net=play.net,
                balance_after=play.balance,
                tax_delta=play.settlement.tax_delta,
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

    async def _burst(self, interaction: discord.Interaction) -> None:
        """Cinco tandas seguidas sin animación y un solo resumen.

        Para antes si se acaba el dinero o sale un atari (para que se vea).
        """
        if self._busy:
            await interaction.response.defer()
            return
        self._busy = True
        plays: list[PachinkoPlay] = []
        stopped: str | None = None
        try:
            await interaction.response.defer()
            self._last_interaction = interaction
            for _ in range(BURST_VOLLEYS):
                try:
                    plays.append(await self._play_one(turbo=True, render=False))
                except InsufficientFundsError:
                    stopped = "Parado: no te llega para otra tanda."
                    break
                except BalanceLimitError:
                    stopped = "Parado: la banca no puede pagar tanto."
                    break
                if plays[-1].volley.jackpots:
                    stopped = "Parado: ¡ATARI!"
                    break
            if not plays:
                await interaction.followup.send(
                    insufficient_text(await self.balance(), self.stake), ephemeral=True
                )
                return
            last = plays[-1]
            png = await asyncio.to_thread(self.cog.renderer.still_png, last.volley)
            text = burst_text(plays, stopped)
            if note := tax_note(plays):
                text += f"\n{note}"
            if renta_hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id):
                text += f"\n{renta_hint}"
            self.last_text = text
            self.last_won = sum(p.net for p in plays) > 0
            self._set_enabled(True)
            await interaction.edit_original_response(
                embed=await self.current_embed(balance=last.balance),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        channel = getattr(self.message, "channel", None)
        for play in plays:
            await self._track(play)
        await logros.track(
            self.cog.bot, self.guild_id, self.owner, channel, StatDelta(add={"pachinko_burst": 1})
        )
        for play in plays:
            await self.cog.shout(play, self.owner, channel)

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
        self.stake = max(MIN_STAKE, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        balance = await self.balance()
        # Si el doble no cabe, se queda en todo el saldo: es lo que se busca.
        self.stake = max(MIN_STAKE, min(self.stake * 2, balance))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        balance = await self.balance()
        if balance < MIN_STAKE:
            await interaction.response.send_message(
                insufficient_text(balance, MIN_STAKE), ephemeral=True
            )
            return
        self.stake = balance
        await self._refresh(interaction, balance)

    async def _paytable(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=paytable_embed(), ephemeral=True)


# -- Cog ----------------------------------------------------------------------------


class Pachinko(commands.Cog, name="Pachinko"):
    """El pachinko del casino: una máquina propia por jugador."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: PachinkoRenderer | None = None,
        machine: PachinkoMachine | None = None,
        casino_channel_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.renderer = renderer or PachinkoRenderer()
        self.machine = machine or PachinkoMachine()
        self.casino_channel_ids = casino_channel_ids
        # Turbo por (servidor, miembro). Crece como mucho hasta el número de
        # miembros que han jugado; se pierde al reiniciar.
        self._turbo: dict[tuple[int, int], bool] = {}
        self._warm_task: asyncio.Task[bytes] | None = None

    async def cog_load(self) -> None:
        """Prepara las piezas del dibujo en segundo plano."""
        self._warm_task = asyncio.create_task(
            asyncio.to_thread(self.renderer.idle_png), name="pachinko-warm-up"
        )

    async def cog_unload(self) -> None:
        """Cancela la preparación si el bot se apaga antes de acabarla."""
        if self._warm_task is not None:
            self._warm_task.cancel()

    def turbo_default(self, guild_id: int, user_id: int) -> bool:
        """Si el miembro dejó el turbo puesto la última vez."""
        return self._turbo.get((guild_id, user_id), False)

    def set_turbo_default(self, guild_id: int, user_id: int, turbo: bool) -> None:
        """Recuerda el turbo para la próxima máquina del miembro."""
        self._turbo[(guild_id, user_id)] = turbo

    async def play(
        self,
        guild_id: int,
        user_id: int,
        *,
        stake: int,
        turbo: bool,
        render: bool = True,
        session_volleys: int = 1,
    ) -> PachinkoPlay:
        """Juega una tanda: decide las bolas y el sorteo y mueve el dinero de una vez.

        Fiscalmente es juego, como la ruleta y la tragaperras: `settle_bet`
        cobra la apuesta, paga el premio y ajusta la retención diaria del
        IRPF, porque la ganancia es una ganancia patrimonial de la base general
        (art. 33.1 LIRPF) y las pérdidas del mismo día compensan (art. 33.5.d
        LIRPF). Lo retenido va a la cuenta del Estado en la misma transacción.

        Raises:
            ValueError: Si la apuesta no llega a `MIN_STAKE`.
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        if stake < MIN_STAKE:
            raise ValueError(f"La tanda mínima es {MIN_STAKE}.")
        volley = self.machine.launch()
        won = payout(volley, stake)
        settlement = await self.economy.settle_bet(
            guild_id, user_id, game=GAME, stake=stake, payout=won
        )
        if render:
            media = await asyncio.to_thread(self.renderer.render, volley, turbo=turbo)
        else:
            media = PachinkoMedia(gif=b"", png=b"", seconds=0.0)
        return PachinkoPlay(
            volley=volley,
            stake=stake,
            won=won,
            settlement=settlement,
            media=media,
            session_volleys=session_volleys,
        )

    async def shout(self, play: PachinkoPlay, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los rush largos y los premios enormes, para que se vea."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        volley = play.volley
        best = volley.best
        big = play.stake and play.net >= SHOUT_MULTIPLIER * play.stake
        if best is not None and (best.kind == Kind.SUPER or volley.jackpots >= SHOUT_RUSH or big):
            name = (
                "SUPER RUSH"
                if best.kind == Kind.SUPER
                else "RUSH"
                if volley.jackpots > 1
                else "ATARI"
            )
            text = (
                f"📣 🌸 {_digits(volley)} ¡{user.mention} ha sacado **{name} ×{volley.jackpots}** "
                f"en el pachinko! Se lleva **{format_amount(play.net)}**. "
                f"{TAX_COLLECTOR} ya está contando bolas."
            )
        elif big:
            amount = format_amount(play.net)
            text = f"📣 🌸 ¡{user.mention} acaba de ganar **{amount}** en el pachinko!"
        else:
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio del pachinko", exc_info=True)

    # -- pachinko -------------------------------------------------------------------

    async def _pachinko_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/pachinko` y `.pachinko`: abre la máquina."""
        if guild is None:
            await send_error("El pachinko solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El pachinko"):
            await send_error(error)
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = parse_stake(amount_text, balance)
        except ValueError as error:
            await send_error(str(error))
            return
        if stake > balance:
            await send_error(insufficient_text(balance, stake))
            return

        view = PachinkoView(self, guild_id=guild.id, owner=user, stake=stake)
        png = await asyncio.to_thread(self.renderer.idle_png)
        view.message = await send(
            embed=await view.current_embed(balance=balance),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=view,
        )

    @app_commands.command(name="pachinko", description="Pachinko japonés con reach y rush.")
    @app_commands.describe(cantidad="Apuesta por tanda de 10 bolas: 500, 2k, all… (100)")
    @app_commands.guild_only()
    async def pachinko(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Abre una máquina de pachinko con botones.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. Las
        tandas mueven yapdollars a través de la economía del bot.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._pachinko_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )

    @commands.command(name="pachinko")
    @commands.guild_only()
    async def pachinko_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.pachinko` o `.pachinko 500`."""

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._pachinko_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Pachinko(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
