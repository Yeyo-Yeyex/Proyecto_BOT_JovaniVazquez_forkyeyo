"""Tragaperras: `slots`, una máquina de 3 rodillos con botones y bote común.

Cada jugador abre su propia máquina, un mensaje con botones que solo él puede
pulsar. Las reglas (rodillos, premios, giros gratis, máquina caliente) viven
en `bot.services.slots`; el dibujo, en `bot.services.slots_render`; el dinero,
en `EconomyService.play_slots`. Este cog solo une las piezas y pinta.

Botones:

- 🎰 **Tirar**: cobra, gira y paga. Con giros gratis pendientes, juega uno.
- 🔁 **Ráfaga ×10**: diez tiradas seguidas sin animación, con un solo resumen y
  una sola imagen.
- ▶️ **Auto**: tiradas normales encadenadas, cada una con su animación y su imagen
  final, como si el dueño pulsara 🎰 Tirar una y otra vez. Mientras corre, el
  botón es ⏹️ **Parar** y es lo único que se puede pulsar. Para sola si no llega
  el saldo, si sale un premio gordo (el bote o ×50 la apuesta), si las pérdidas
  netas de la sesión llegan a 10 veces la apuesta o al llegar a 25 tiradas. El
  bucle y sus reglas están en `bot.services.autoplay`.
- ⚡ **Turbo**: sin animación, solo la imagen final (más rápido y casi sin datos).
- **½**, **×2**, 💰 **All-in**: cambian la apuesta. 📋 **Premios**: la tabla.
- 🔁 **Re-girar el 3º**: sale tras un casi-premio del tercer rodillo. Cobra
  `respin_price` (lo que vale de media el re-giro entre 0,995) y vuelve a girar
  solo ese rodillo. Se encadena mientras siga quedándose a uno.
- 🔴 **Rojo** / ⚫ **Negro**: doble o nada con lo que acaba de cobrar, a cara o
  cruz, hasta `DOUBLE_MAX` veces seguidas. Elegir color no cambia nada: es la
  ilusión de control de las máquinas de bar.
- 🎁 **Giro del día**: un giro gratis al día cuya apuesta sube con la racha de
  días seguidos (`daily_stake`).

El embed enseña lo que paga cada combinación a la apuesta actual, como el
cartel de una máquina de bar; el bote con su tope («cae antes de 50.000») y
las tiradas que lleva el servidor sin bote; y los premios cobrados en la
sesión, en bruto. El neto de verdad sale en el ticket al cerrar la máquina.

La animación es un GIF que se monta en cada tirada (~0,1 s de CPU fuera del
event loop y ~150 KB). Al acabar se cambia por el PNG final, como en la ruleta.

El calor de la máquina (`bot.services.slots.next_heat`) se guarda por
miembro en la base de datos (`SlotsRepository`) y se enfría un punto cada diez
minutos sin jugar (`decayed_heat`): si te vas, lo pierdes.

Si `CASINO_CHANNEL_IDS` está configurado, la máquina solo se abre en esos
canales. Permisos que necesita el bot en el canal: enviar mensajes, insertar
enlaces (embeds) y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import (
    StatDelta,
    casino_stats,
    slots_autoplay_stats,
    slots_cooled_stats,
    slots_double_stats,
    slots_respin_stats,
    slots_stats,
    slots_ticket_stats,
)
from bot.services.autoplay import (
    AUTOPLAY_MAX,
    AUTOPLAY_MIN_GAP,
    AutoplayOutcome,
    AutoplaySession,
    AutoplayStop,
    SpinResult,
    StopReason,
)
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
from bot.services.pets import bet_moment
from bot.services.slots import (
    DOUBLE_MAX,
    FREE_SPINS,
    HEAT_DECAY_SECONDS,
    HEAT_MAX,
    HOT_MULTIPLIER,
    POT_CAP,
    POT_SEED,
    SCATTER,
    SYMBOLS,
    Kind,
    SlotMachine,
    Spin,
    WinTier,
    daily_stake,
    decayed_heat,
    heat_bar,
    line_payout,
    next_daily_streak,
    next_heat,
    paytable_lines,
    pot_share,
    prize_table,
    respin_price,
    win_tier,
)
from bot.services.slots_render import SlotsMedia, SlotsRenderer
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.slots import SlotsRepository

logger = logging.getLogger(__name__)

GAME = "tragaperras"
#: Apuesta por defecto al abrir la máquina sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la máquina se cierra.
MACHINE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en empezar el GIF.
REVEAL_MARGIN_SECONDS = 0.4
#: Tiradas de Ráfaga.
BURST_SPINS = 10
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
JACKPOT_LINES = ("🃏🃏🃏 ¡JOVANAZO!", "🃏🃏🃏 ¡EL BOTE ES TUYO!", "🃏🃏🃏 ¡WEPAAA!")
MYSTERY_LINES = ("💰 ¡HA CAÍDO EL BOTE!", "💰 ¡LE TOCABA!", "💰 ¡BOTE MISTERIOSO!")
TIER_LINES = {
    WinTier.EPIC: ("🌋 ¡ÉPICO!", "🌋 ¡REVIENTAS LA MÁQUINA!", "🌋 ¡ESTO NO SE VE NUNCA!"),
    WinTier.MEGA: ("💥 ¡MEGAPREMIO!", "💥 ¡QUÉ LOCURA!", "💥 ¡AY, BENDITO, QUÉ PREMIO!"),
    WinTier.BIG: ("✨ ¡GRAN PREMIO!", "✨ ¡TOMA YA!", "✨ ¡ESO ES CALIDAD!"),
}
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
DOUBLE_WIN_LINES = ("¡Dentro!", "¡Lo sabía!", "¡Olfato de casino!", "¡Tienes el don!")
DOUBLE_LOSS_LINES = (
    "La casa agradece tu aportación.",
    "Era el otro color, obviamente.",
    "Como un rescate bancario, pero al revés.",
)
TICKET_LINES = (
    "Gracias por su visita. Vuelva pronto.",
    "Hacienda ya tiene una copia.",
    "Conserve este ticket para su psicólogo.",
)
#: Colores del doble o nada. Da igual cuál se elija: es cara o cruz.
RED = "red"
BLACK = "black"
COLOR_NAMES = {RED: "🔴 Rojo", BLACK: "⚫ Negro"}


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
    #: Lo que ha costado si no es la apuesta: el precio de un re-giro.
    cost: int | None = None
    #: En un re-giro, cuántos van seguidos sobre la misma tirada (contando este).
    respin_chain: int = 0
    daily: bool = False
    daily_streak: int = 0

    @property
    def jackpot(self) -> int:
        """Lo que se ha llevado del bote."""
        return self.settlement.jackpot

    @property
    def mystery(self) -> bool:
        """Si el bote ha caído solo, por el tope oculto."""
        return bool(self.jackpot) and getattr(self.settlement, "mystery", False)

    @property
    def respin(self) -> bool:
        """Si es un re-giro del tercer rodillo."""
        return self.cost is not None

    @property
    def paid_stake(self) -> int:
        """Lo que ha salido del bolsillo: nada en un giro gratis, el precio en un re-giro."""
        if self.cost is not None:
            return self.cost
        return 0 if self.free or self.daily else self.stake

    @property
    def tier(self) -> str | None:
        """Nivel de celebración (`WinTier`) de lo cobrado frente a la apuesta."""
        return win_tier(self.won, self.stake)

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

    @property
    def big_prize(self) -> bool:
        """Premio gordo: el bote o ×`SHOUT_MULTIPLIER` la apuesta (los que se anuncian)."""
        return self.jackpot > 0 or (self.stake > 0 and self.net >= SHOUT_MULTIPLIER * self.stake)


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
        lines.append(f"# {rng.choice(MYSTERY_LINES if play.mystery else JACKPOT_LINES)}")
        lines.append(f"## +{format_amount(play.jackpot)} del bote")
        if play.mystery and (drought := getattr(play.settlement, "drought", 0)):
            lines.append(f"Llevaba {drought:,} tiradas sin caer.".replace(",", "."))
        if play.payout:
            lines.append(f"Y además la línea paga {format_amount(play.payout)}.")
    elif (tier := play.tier) is not None:
        lines.append(f"# {rng.choice(TIER_LINES[tier])}")
        lines.append(f"## +{format_amount(play.net)}")
    elif play.net > 0:
        lines.append(f"# {rng.choice(WIN_LINES)} +{format_amount(play.net)}")
    elif play.payout > 0:
        lines.append(f"## {rng.choice(SMALL_WIN_LINES)} Cobras {format_amount(play.payout)}")
    elif spin.near_miss:
        lines.append(f"## {rng.choice(NEAR_MISS_LINES)}")
        if spin.teaser is not None:
            emoji = SYMBOLS[spin.teaser].emoji
            lines.append(f"El {emoji} se ha quedado a una casilla de la línea.")
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
    if play.respin:
        lines.append(f"🔁 Re-giro del tercer rodillo por {format_amount(play.paid_stake)}.")
    if play.daily:
        days = "1 día" if play.daily_streak == 1 else f"{play.daily_streak} días"
        lines.append(f"🎁 Giro del día · racha de {days}.")
    if play.free:
        lines.append("🎟️ Giro gratis.")
    if spin.triggers_free_spins:
        lines.append(f"### 🎟️🎟️🎟️ ¡GIROS GRATIS! +{FREE_SPINS}")
    elif spin.scatters == 2:
        lines.append(f"{SYMBOLS[SCATTER].emoji}{SYMBOLS[SCATTER].emoji} Te faltó una entrada…")
    return "\n".join(lines)


def auto_text(plays: list[SlotsPlay], stopped: str | None = None) -> str:
    """Resumen de una Ráfaga: cuántas, cuántas con premio, neto y la mejor."""
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
        lines.append(f"# 🃏🃏🃏 ¡JOVANAZO! +{format_amount(sum(p.jackpot for p in plays))}")
    if any(p.spin.triggers_free_spins for p in plays):
        lines.append("🎟️ ¡Han salido giros gratis!")
    if stopped:
        lines.append(f"-# {stopped}")
    return "\n".join(lines)


def tax_note(delta: int, plays: list[SlotsPlay]) -> str | None:
    """Línea de IRPF de una tirada o de una Ráfaga (suma de ajustes)."""
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
    spins_since: int = 0,
    session_gross: int = 0,
    session_prizes: int = 0,
) -> discord.Embed:
    """Embed de la máquina parada: al abrirla o tras una tirada.

    Args:
        spins_since: Tiradas pagadas del servidor desde el último bote.
        session_gross: Premios cobrados en esta máquina, en bruto: sin restar
            lo apostado. Es lo que enseña una máquina de verdad.
        session_prizes: Cuántas tiradas de la sesión han cobrado algo.
    """
    if text is None:
        description = (
            "Pulsa 🎰 **Tirar**. Paga la fila del medio.\n"
            "📋 **Premios** para ver la tabla. 🃏 🃏 🃏 se lleva el **bote**."
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
    pot_lines = [f"**{format_amount(pot)}**", f"-# Cae antes de {format_amount(POT_CAP)}"]
    if spins_since:
        pot_lines.append(f"-# {spins_since:,} tiradas sin bote".replace(",", "."))
    embed.add_field(name="💰 Bote", value="\n".join(pot_lines))
    left, right = prize_columns(stake)
    embed.add_field(name=f"Premios a {format_amount(stake)}", value=left)
    embed.add_field(name="\u200b", value=right)
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
    if session_gross:
        embed.add_field(
            name="🎫 Premios cobrados",
            value=f"**{format_amount(session_gross)}** en {session_prizes} premios",
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


def prize_columns(stake: int) -> tuple[str, str]:
    """El cartel de premios a la apuesta `stake`, en dos columnas como en la máquina del bar."""
    rows = [
        f"{combo} **{'BOTE' if amount is None else format_amount(amount)}**"
        for combo, amount in prize_table(stake)
    ]
    half = (len(rows) + 1) // 2
    return "\n".join(rows[:half]), "\n".join(rows[half:])


def ticket_text(
    *, spins: int, staked: int, gross: int, tax: int = 0, rng: random.Random | None = None
) -> str:
    """El ticket de la sesión al cerrar la máquina: aquí sí sale el neto, con el IRPF.

    Args:
        tax: IRPF del juego retenido en la sesión (negativo si se devolvió).
    """
    rng = rng or random.Random()
    net = gross - staked - tax
    sign = "+" if net > 0 else "-" if net < 0 else "±"
    return "\n".join(
        (
            "## 🧾 Ticket de la sesión",
            f"Tiradas: {spins} · Apostado: {format_amount(staked)}",
            f"Premios cobrados: {format_amount(gross)}",
            *(
                [f"IRPF ({TAX_COLLECTOR}): {'-' if tax > 0 else '+'}{format_amount(abs(tax))}"]
                if tax
                else []
            ),
            f"**Neto: {sign}{format_amount(abs(net))}**",
            f"-# {rng.choice(TICKET_LINES)}",
        )
    )


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

    `_busy` vale mientras hay una acción en curso (una tirada, una Ráfaga o todo
    un ▶️ Auto): un segundo clic de dos botones a la vez no puede cobrar dos
    veces. `autoplay` es la sesión de ▶️ Auto en marcha, si la hay.
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
        # Ticket de la sesión: lo apostado y lo cobrado en bruto (con re-giros y dobles).
        self.session_staked = 0
        self.session_gross = 0
        self.session_prizes = 0
        # IRPF del juego retenido (o devuelto, en negativo) en la sesión.
        self.session_tax = 0
        # Lo que se puede hacer con la última tirada: re-girar el tercer
        # rodillo (`respin_offer`: la tirada, su apuesta, el precio y los
        # re-giros encadenados) o doblar lo cobrado (`double_offer`: cuánto y
        # cuántos dobles ganados lleva).
        self.respin_offer: tuple[Spin, int, int, int] | None = None
        self.double_offer: tuple[int, int] | None = None
        # Racha con la que se cobraría hoy el giro del día; 0 si no hay.
        self.daily_streak = 0
        self.cooled = 0
        self.last_text: str | None = None
        self.last_won: bool | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        self.autoplay: AutoplaySession | None = None
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
        self.burst_button = self._add(
            f"🔁 Ráfaga ×{BURST_SPINS}", 0, self._burst, style=blue, custom_id="auto"
        )
        self.autoplay_button = self._add(
            "▶️ Auto", 0, self._autoplay_click, style=blue, custom_id="autoplay"
        )
        self.turbo_button = self._add("⚡ Turbo", 0, self._toggle_turbo, custom_id="turbo")
        self._add("½", 1, self._halve, custom_id="half")
        self._add("×2", 1, self._double_stake, custom_id="x2")
        self._add("💰 All-in", 1, self._all_in, custom_id="allin")
        self._add("📋 Premios", 1, self._paytable, custom_id="paytable")
        red = discord.ButtonStyle.danger
        self.respin_button = self._add(
            "🔁 Re-girar", 2, self._respin, style=red, custom_id="respin"
        )
        self.red_button = self._add(
            COLOR_NAMES[RED], 2, self._double_red, style=red, custom_id="red"
        )
        self.black_button = self._add(COLOR_NAMES[BLACK], 2, self._double_black, custom_id="black")
        self.daily_button = self._add(
            "🎁 Giro del día", 2, self._daily, style=green, custom_id="daily"
        )
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        """Activa o desactiva los botones y pone al día sus etiquetas."""
        running = self.autoplay is not None
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                # Con ▶️ Auto en marcha solo queda el botón de Parar.
                item.disabled = running or not enabled
        stopping = running and self.autoplay is not None and self.autoplay.stop_requested
        if running:
            self.autoplay_button.disabled = stopping
            self.autoplay_button.label = "⏹️ Parando…" if stopping else "⏹️ Parar"
            self.autoplay_button.style = discord.ButtonStyle.danger
        else:
            self.autoplay_button.label = "▶️ Auto"
            self.autoplay_button.style = discord.ButtonStyle.primary
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
        # La fila de abajo solo enseña lo que se puede hacer ahora mismo.
        extras = {
            self.respin_button: self.respin_offer is not None and not running,
            self.red_button: self.double_offer is not None and not running,
            self.black_button: self.double_offer is not None and not running,
            self.daily_button: self.daily_streak > 0 and not running,
        }
        for button, visible in extras.items():
            if visible and button not in self.children:
                self.add_item(button)
            elif not visible and button in self.children:
                self.remove_item(button)
        if self.respin_offer is not None:
            price = self.respin_offer[2]
            self.respin_button.label = f"🔁 Re-girar el 3º · {format_amount(price)}"
        if self.double_offer is not None:
            amount = format_amount(self.double_offer[0])
            self.red_button.label = f"{COLOR_NAMES[RED]} · {amount}"
            self.black_button.label = f"{COLOR_NAMES[BLACK]} · {amount}"
        if self.daily_streak:
            stake = format_amount(daily_stake(self.daily_streak))
            self.daily_button.label = f"🎁 Giro del día · {stake}"

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
        await self.close_autoplay()
        plays = await self.flush_free_spins()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        kwargs: dict[str, Any] = {"view": self}
        summary = ""
        if plays:
            summary = auto_text(plays, "Giros gratis jugados al cerrar la máquina.") + "\n\n"
        if self.session_spins:
            summary += ticket_text(
                spins=self.session_spins,
                staked=self.session_staked,
                gross=self.session_gross,
                tax=self.session_tax,
            )
        if summary:
            kwargs["embed"] = await self.current_embed(text=summary)
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la tragaperras", exc_info=True)
        if self.session_spins:
            await logros.track(
                self.cog.bot,
                self.guild_id,
                self.owner,
                getattr(self.message, "channel", None),
                slots_ticket_stats(
                    spins=self.session_spins,
                    gross=self.session_gross,
                    staked=self.session_staked,
                    net=self.session_gross - self.session_staked - self.session_tax,
                ),
            )

    async def close_autoplay(self) -> None:
        """Detiene un ▶️ Auto en marcha: acaba la tirada en curso y cierra la tarea."""
        session = self.autoplay
        if session is None:
            return
        await session.close()
        if self.autoplay is session:
            # La tarea se canceló antes de poder cerrarse sola: deja la vista limpia.
            self.autoplay = None
            self.timeout = MACHINE_TIMEOUT
            self._busy = False
            self._set_enabled(True)

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
        spins_since = await self.cog.spins_since(self.guild_id)
        if text is None:
            text = self.last_text
        if self.cooled:
            note = f"🧊 La máquina se ha enfriado mientras no estabas: -{self.cooled} 🔥"
            text = f"{text}\n{note}" if text else note
        return machine_embed(
            owner=self.owner.display_name,
            balance=balance,
            stake=self.stake,
            pot=pot,
            heat=self.cog.heat(self.guild_id, self.owner.id),
            free_spins=self.free_spins,
            free_stake=self.free_stake,
            turbo=self.turbo,
            text=text,
            won=self.last_won,
            last_jackpot=await self.cog.last_jackpot_text(self.guild_id),
            spins_since=spins_since,
            session_gross=self.session_gross,
            session_prizes=self.session_prizes,
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
        self._account(play)
        return play

    def _account(self, play: SlotsPlay) -> None:
        """Suma la tirada al ticket de la sesión y retira las ofertas de la anterior."""
        self.session_staked += play.paid_stake
        self.session_gross += play.won
        self.session_prizes += play.won > 0
        self.session_tax += play.settlement.bet.tax_delta
        self.cooled = 0
        self.respin_offer = None
        self.double_offer = None

    def _offer(self, play: SlotsPlay) -> None:
        """Lo que se puede hacer tras una tirada a mano: re-girar si roza, doblar si cobra."""
        if play.spin.teaser is not None:
            price = respin_price(play.spin, play.stake, play.settlement.pot)
            self.respin_offer = (play.spin, play.stake, price, play.respin_chain + 1)
        if play.won > 0:
            self.double_offer = (play.won, 0)

    async def _guarded_play(
        self, interaction: discord.Interaction, *, turbo: bool, render: bool = True
    ) -> SlotsPlay | None:
        """`_play_one` con los errores de saldo convertidos en avisos privados."""
        try:
            return await self._play_one(turbo=turbo, render=render)
        except InsufficientFundsError as error:
            await notify(interaction, insufficient_text(error.balance, self.stake))
        except BalanceLimitError:
            await notify(interaction, "La banca no puede pagar tanto. Baja la apuesta.")
        return None

    async def play(self, interaction: discord.Interaction) -> None:
        """Respuesta a 🎰 Tirar: cobra, gira, enseña y paga."""
        if self._busy:
            # Doble clic mientras gira: se ignora sin mostrar error.
            await ack(interaction)
            return
        self._busy = True
        try:
            # Cobrar y dibujar va a la base de datos y tarda: se acepta el clic antes.
            await ack(interaction)
            play = await self._guarded_play(interaction, turbo=self.turbo)
            if play is None:
                return
            self._offer(play)
            self._last_interaction = interaction
            await self.show(
                play,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        await self._after_play(interaction, play)

    async def show(
        self,
        play: SlotsPlay,
        *,
        first_edit: EditFn,
        final_edit: EditFn,
        note: str | None = None,
    ) -> None:
        """Enseña la tirada: el GIF y después el PNG final (o solo el PNG en turbo).

        El dinero ya está cobrado y pagado: si Discord falla al editar, el
        saldo sigue siendo correcto.

        Args:
            note: Línea pequeña bajo el resultado (el contador de ▶️ Auto).
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
        if tax_line := tax_note(play.settlement.bet.tax_delta, [play]):
            text += f"\n{tax_line}"
        if renta_hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=play.paid_stake, net=play.net, balance_after=play.balance),
        ):
            text += f"\n{renta_hint}"
        if note:
            text += f"\n{note}"
        self.last_text = text
        self.last_won = play.won > 0
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(balance=play.balance, pot=play.settlement.pot),
            attachments=[discord.File(io.BytesIO(play.media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _track(self, play: SlotsPlay, *, autoplay: bool = False) -> None:
        """Logros de la tirada, después de enseñarla (antes destriparía el resultado)."""
        if play.respin:
            delta = slots_respin_stats(
                price=play.paid_stake,
                payout=play.payout,
                jackpot=play.jackpot,
                chain=play.respin_chain,
            )
        else:
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
                autoplay=autoplay,
                tier=play.tier,
                mystery=play.mystery,
                drought=play.settlement.drought,
                daily=play.daily,
                daily_streak=play.daily_streak,
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
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=play.paid_stake,
            net=play.net,
            balance_after=play.balance,
            tax=play.settlement.bet.tax_delta,
        )

    async def _spin(self, interaction: discord.Interaction) -> None:
        await self.play(interaction)

    async def _after_play(self, interaction: discord.Interaction, play: SlotsPlay) -> None:
        """Lo que no hace falta para enseñar la tirada: Renta, logros y anuncio."""
        await renta.remind(self.cog.bot, interaction)
        await self._track(play)
        await self.cog.shout(play, self.owner, getattr(self.message, "channel", None))

    async def _respin(self, interaction: discord.Interaction) -> None:
        """🔁 Re-girar el 3º: cobra el precio y vuelve a girar solo el tercer rodillo.

        El precio se recalcula al pulsar con el bote de ese momento: si otro
        jugador lo ha hecho crecer y el re-giro sale más caro que lo que dice
        el botón, no se cobra y se avisa del precio nuevo.
        """
        offer = self.respin_offer
        if self._busy or offer is None:
            await ack(interaction)
            return
        self._busy = True
        play: SlotsPlay | None = None
        try:
            await ack(interaction)
            spin, stake, shown, chain = offer
            price = respin_price(spin, stake, await self.cog.pot(self.guild_id))
            if price > shown:
                self.respin_offer = (spin, stake, price, chain)
                self._set_enabled(True)
                await edit(interaction, view=self)
                await notify(
                    interaction,
                    f"El bote ha subido y el re-giro ahora cuesta {format_amount(price)}.",
                )
                return
            try:
                play = await self.cog.respin(
                    self.guild_id,
                    self.owner.id,
                    spin=spin,
                    stake=stake,
                    price=price,
                    turbo=self.turbo,
                    session_spins=self.session_spins + 1,
                    chain=chain,
                )
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, price))
                return
            except BalanceLimitError:
                await notify(interaction, "La banca no puede pagar tanto. Baja la apuesta.")
                return
            self.session_spins += 1
            self._account(play)
            self._offer(play)
            self._last_interaction = interaction
            await self.show(
                play,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        if play is not None:
            await self._after_play(interaction, play)

    async def _double_red(self, interaction: discord.Interaction) -> None:
        await self._double(interaction, RED)

    async def _double_black(self, interaction: discord.Interaction) -> None:
        await self._double(interaction, BLACK)

    async def _double(self, interaction: discord.Interaction, color: str) -> None:
        """🔴/⚫ Doble o nada con lo último cobrado: cara o cruz, el color da igual.

        Es una apuesta del casino como otra (`settle_bet`, juego en el IRPF):
        lo cobrado ya está en el saldo y se vuelve a apostar.
        """
        offer = self.double_offer
        if self._busy or offer is None:
            await ack(interaction)
            return
        self._busy = True
        bet = None
        try:
            await ack(interaction)
            amount, chain = offer
            won = self.cog.coin()
            try:
                bet = await self.cog.economy.settle_bet(
                    self.guild_id,
                    self.owner.id,
                    game=GAME,
                    stake=amount,
                    payout=2 * amount if won else 0,
                )
            except InsufficientFundsError as error:
                self.double_offer = None
                self._set_enabled(True)
                await edit(interaction, view=self)
                await notify(interaction, insufficient_text(error.balance, amount))
                return
            except BalanceLimitError:
                await notify(interaction, "La banca no puede pagar tanto.")
                return
            chain = chain + 1 if won else chain
            self.session_staked += amount
            self.session_gross += 2 * amount if won else 0
            self.session_tax += bet.tax_delta
            self.respin_offer = None
            self.double_offer = (2 * amount, chain) if won and chain < DOUBLE_MAX else None
            shown = color if won else (BLACK if color == RED else RED)
            if won:
                lines = [
                    f"# {COLOR_NAMES[shown]} · {random.choice(DOUBLE_WIN_LINES)}",
                    f"## +{format_amount(amount)} · llevas {format_amount(2 * amount)}",
                ]
                if self.double_offer is None:
                    lines.append(f"Tope de {DOUBLE_MAX} dobles seguidos: te lo quedas.")
            else:
                lines = [
                    f"# {COLOR_NAMES[shown]}",
                    f"## -{format_amount(amount)} · {random.choice(DOUBLE_LOSS_LINES)}",
                ]
            if tax_line := gambling_tax_line(bet):
                lines.append(tax_line)
            self.last_text = "\n".join(lines)
            self.last_won = won
            self._set_enabled(True)
            await edit(interaction, embed=await self.current_embed(balance=bet.balance), view=self)
            self._last_interaction = interaction
        finally:
            self._busy = False
        if bet is None:
            return
        net = amount if won else -amount
        await renta.remind(self.cog.bot, interaction)
        delta = slots_double_stats(amount=amount, won=won, chain=chain)
        delta.merge(
            casino_stats(stake=amount, net=net, balance_after=bet.balance, tax_delta=bet.tax_delta)
        )
        channel = getattr(self.message, "channel", None)
        await logros.casino_play(self.cog.bot, self.guild_id, self.owner, channel, delta, net=net)
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=amount,
            net=net,
            balance_after=bet.balance,
            tax=bet.tax_delta,
        )

    async def _daily(self, interaction: discord.Interaction) -> None:
        """🎁 Giro del día: gratis, a una apuesta que sube con la racha de días seguidos.

        Lo cobrado es juego, como un giro gratis. Apuntarlo es atómico: dos
        clics (o dos máquinas abiertas) no dan dos giros.
        """
        if self._busy or not self.daily_streak:
            await ack(interaction)
            return
        self._busy = True
        play: SlotsPlay | None = None
        try:
            await ack(interaction)
            streak = await self.cog.claim_daily(self.guild_id, self.owner.id)
            self.daily_streak = 0
            if streak is None:
                self._set_enabled(True)
                await edit(interaction, view=self)
                await notify(interaction, "Ya has cobrado el giro de hoy. Vuelve mañana.")
                return
            try:
                play = await self.cog.play(
                    self.guild_id,
                    self.owner.id,
                    stake=daily_stake(streak),
                    free=False,
                    turbo=self.turbo,
                    session_spins=self.session_spins + 1,
                    daily_streak=streak,
                )
            except BalanceLimitError:
                await notify(interaction, "La banca no puede pagar tanto.")
                return
            self.session_spins += 1
            if play.spin.triggers_free_spins:
                self.free_spins += FREE_SPINS
                self.free_stake = play.stake
            self._account(play)
            self._offer(play)
            self._last_interaction = interaction
            await self.show(
                play,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        if play is not None:
            await self._after_play(interaction, play)

    async def _burst(self, interaction: discord.Interaction) -> None:
        """🔁 Ráfaga: diez tiradas seguidas sin animación y un solo resumen.

        Para antes si se acaba el dinero o sale el bote. Los giros gratis que
        salgan por el camino se juegan dentro de la misma ronda.
        """
        if self._busy:
            await ack(interaction)
            return
        self._busy = True
        plays: list[SlotsPlay] = []
        stopped: str | None = None
        try:
            await ack(interaction)
            self._last_interaction = interaction
            for _ in range(BURST_SPINS):
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
                await notify(interaction, insufficient_text(await self.balance(), self.stake))
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
            if renta_hint := await renta.hint(
                self.cog.bot,
                self.guild_id,
                self.owner.id,
                bet_moment(
                    stake=sum(p.paid_stake for p in plays),
                    net=sum(p.net for p in plays),
                    balance_after=plays[-1].balance,
                ),
            ):
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

    # -- ▶️ Auto ---------------------------------------------------------------------

    def _message_edit(self, interaction: discord.Interaction) -> EditFn:
        """Cómo editar el mensaje de la máquina sin el token de la interacción.

        El token caduca a los 15 minutos y un ▶️ Auto largo puede pasarse. El mensaje
        de un slash command es un `InteractionMessage`, cuyo `edit` usa ese token: se
        edita entonces por el canal. Un mensaje normal (`.tragas`) ya edita con el
        token del bot. Sin mensaje guardado, se cae a la respuesta de la interacción.
        """
        message = self.message
        if message is None:
            return interaction.edit_original_response
        if isinstance(message, discord.InteractionMessage):
            get_partial = getattr(message.channel, "get_partial_message", None)
            if get_partial is not None:
                return get_partial(message.id).edit
        return message.edit

    async def _autoplay_click(self, interaction: discord.Interaction) -> None:
        """▶️ Auto empieza una sesión; con la sesión en marcha, el mismo botón es ⏹️ Parar."""
        session = self.autoplay
        if session is not None:
            await self._stop_autoplay(interaction, session)
            return
        if self._busy:
            # Doble clic o una tirada en curso: se acepta el clic y se ignora.
            await ack(interaction)
            return
        self._busy = True
        started = False
        try:
            await ack(interaction)
            self._last_interaction = interaction
            session = AutoplaySession(
                stake=self.stake, max_spins=AUTOPLAY_MAX, min_gap=AUTOPLAY_MIN_GAP
            )
            self.autoplay = session
            # Sin clics durante la sesión, la vista caducaría a los 3 minutos.
            self.timeout = None
            edit_fn = self._message_edit(interaction)

            async def step(number: int) -> SpinResult:
                return await self._autoplay_step(interaction, session, number, edit_fn)

            async def finish(outcome: AutoplayOutcome) -> None:
                await self._autoplay_finish(interaction, session, outcome, edit_fn)

            session.start(
                step,
                on_finish=finish,
                name=f"slots-autoplay-{self.guild_id}-{self.owner.id}",
            )
            started = True
        finally:
            if not started:
                self.autoplay = None
                self.timeout = MACHINE_TIMEOUT
                self._busy = False

    async def _stop_autoplay(
        self, interaction: discord.Interaction, session: AutoplaySession
    ) -> None:
        """⏹️ Parar: marca el flag y contesta ya, sin base de datos (solo memoria)."""
        if not session.armed:
            # El mensaje aún no enseña el botón de Parar: es el doble clic de ▶️ Auto.
            await ack(interaction)
            return
        session.request_stop()
        self._set_enabled(True)
        await interaction.response.edit_message(view=self)

    async def _autoplay_step(
        self,
        interaction: discord.Interaction,
        session: AutoplaySession,
        number: int,
        edit_fn: EditFn,
    ) -> SpinResult:
        """Una tirada de ▶️ Auto: igual que 🎰 Tirar, pero editando sin token.

        Raises:
            AutoplayStop: Si no llega el saldo o la banca no puede pagar (antes de cobrar).
        """
        try:
            play = await self._play_one(turbo=self.turbo)
        except InsufficientFundsError:
            raise AutoplayStop(StopReason.NO_FUNDS) from None
        except BalanceLimitError:
            raise AutoplayStop(StopReason.BANK_LIMIT) from None

        async def first_edit(**kwargs: Any) -> None:
            await edit_fn(**kwargs)
            session.armed = True

        sign = "+" if session.net + play.net > 0 else "-" if session.net + play.net < 0 else "±"
        note = (
            f"-# ▶️ Auto · tirada {number}/{session.max_spins} · "
            f"neto {sign}{format_amount(abs(session.net + play.net))}"
        )
        stop: StopReason | None = None
        try:
            await self.show(play, first_edit=first_edit, final_edit=edit_fn, note=note)
        except discord.HTTPException:
            # Mensaje borrado o sin permisos: seguir jugando a ciegas gastaría dinero.
            logger.warning("No se pudo editar la tragaperras en pleno Auto", exc_info=True)
            stop = StopReason.CLOSED
        edited_at = time.monotonic()
        channel = getattr(self.message, "channel", None)
        await self._track(play, autoplay=True)
        await self.cog.shout(play, self.owner, channel)
        if number == 1:
            await renta.remind(self.cog.bot, interaction)
        return SpinResult(net=play.net, big_prize=play.big_prize, edited_at=edited_at, stop=stop)

    async def _autoplay_finish(
        self,
        interaction: discord.Interaction,
        session: AutoplaySession,
        outcome: AutoplayOutcome,
        edit_fn: EditFn,
    ) -> None:
        """Cierra la sesión: devuelve los botones, enseña el resumen y apunta los logros."""
        try:
            self.autoplay = None
            self.timeout = MACHINE_TIMEOUT
            self._set_enabled(True)
            try:
                if outcome.spins == 0:
                    if outcome.reason is StopReason.NO_FUNDS:
                        await notify(
                            interaction, insufficient_text(await self.balance(), self.stake)
                        )
                    elif outcome.reason is not StopReason.CLOSED:
                        await notify(interaction, outcome.reason_text())
                    if session.armed:
                        await edit_fn(embed=await self.current_embed(), view=self)
                else:
                    # El resumen va encima de la última tirada para que se siga viendo.
                    text = outcome.summary()
                    if self.last_text:
                        text += f"\n\n{self.last_text}"
                    self.last_text = text
                    self.last_won = outcome.net > 0
                    await edit_fn(embed=await self.current_embed(), view=self)
            except discord.HTTPException:
                logger.debug("No se pudo cerrar el resumen del Auto", exc_info=True)
        finally:
            self._busy = False
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            slots_autoplay_stats(spins=outcome.spins, net=outcome.net, reason=outcome.reason),
        )

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        """Actualiza la máquina (apuesta, turbo) sin tocar la imagen.

        El embed lee saldo y bote de la base de datos: se acepta el clic antes.
        """
        await ack(interaction)
        self._set_enabled(True)
        await edit(interaction, embed=await self.current_embed(balance=balance), view=self)
        self._last_interaction = interaction

    async def _toggle_turbo(self, interaction: discord.Interaction) -> None:
        self.turbo = not self.turbo
        self.cog.set_turbo_default(self.guild_id, self.owner.id, self.turbo)
        await self._refresh(interaction)

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        # Si el doble no cabe, se queda en todo el saldo: es lo que se busca.
        self.stake = max(1, min(self.stake * 2, balance))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        if balance == 0:
            await notify(interaction, insufficient_text(0))
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
        repository: SlotsRepository | None = None,
        clock: Callable[[], float] = time.time,
        coin: Callable[[], bool] | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        # Calor y giro diario guardados; `None` en pruebas que no los usan.
        self.repository = repository
        self.renderer = renderer or SlotsRenderer()
        self.machine = machine or SlotMachine()
        self.casino_channel_ids = casino_channel_ids
        self.clock = clock
        # Cara o cruz del doble o nada; inyectable en pruebas.
        self.coin = coin or (lambda: random.SystemRandom().random() < 0.5)
        # Calor (con la hora en que se tocó por última vez) y turbo por
        # (servidor, miembro). Crecen como mucho hasta el número de miembros
        # que han jugado. El calor se guarda también en `repository`.
        self._heat: dict[tuple[int, int], int] = {}
        self._heat_at: dict[tuple[int, int], float] = {}
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
                await view.close_autoplay()
                await view.flush_free_spins()
            except Exception:
                logger.exception("No se pudieron jugar los giros gratis al apagar")
            view.stop()
        self.machines.clear()

    # -- Estado por miembro ----------------------------------------------------------

    def heat(self, guild_id: int, user_id: int) -> int:
        """Calor de la máquina de un miembro (sin aplicar el enfriado pendiente)."""
        return self._heat.get((guild_id, user_id), 0)

    async def load_heat(self, guild_id: int, user_id: int) -> None:
        """Trae el calor guardado a memoria la primera vez que el miembro abre la máquina."""
        key = (guild_id, user_id)
        if key in self._heat or self.repository is None:
            return
        saved = await self.repository.load_heat(guild_id, user_id)
        if saved is not None:
            self._heat[key], self._heat_at[key] = saved

    def cool_down(self, guild_id: int, user_id: int) -> int:
        """Aplica el enfriado por el tiempo sin jugar y devuelve los puntos perdidos."""
        key = (guild_id, user_id)
        heat = self._heat.get(key, 0)
        since = self._heat_at.get(key)
        if not heat or since is None:
            return 0
        cooled = decayed_heat(heat, self.clock() - since)
        if cooled == heat:
            return 0
        self._heat[key] = cooled
        # El resto del intervalo empezado sigue contando para el siguiente punto.
        self._heat_at[key] = since + (heat - cooled) * HEAT_DECAY_SECONDS
        return heat - cooled

    async def _save_heat(self, guild_id: int, user_id: int) -> None:
        key = (guild_id, user_id)
        self._heat_at[key] = self.clock()
        if self.repository is not None:
            await self.repository.save_heat(
                guild_id, user_id, self._heat.get(key, 0), self._heat_at[key]
            )

    async def daily_status(self, guild_id: int, user_id: int) -> tuple[bool, int]:
        """`(si puede cobrar hoy el giro del día, racha que tendría al cobrarlo)`."""
        if self.repository is None:
            return False, 0
        today = datetime.now(TIMEZONE).date()
        saved = await self.repository.daily(guild_id, user_id)
        if saved is None:
            return True, 1
        last_day, streak = date.fromisoformat(saved[0]), saved[1]
        if last_day == today:
            return False, streak
        return True, next_daily_streak(last_day, today, streak)

    async def claim_daily(self, guild_id: int, user_id: int) -> int | None:
        """Apunta el giro del día; devuelve la racha, o `None` si ya se había cobrado."""
        ready, streak = await self.daily_status(guild_id, user_id)
        if not ready or self.repository is None:
            return None
        today = datetime.now(TIMEZONE).date().isoformat()
        if not await self.repository.claim_daily(guild_id, user_id, today=today, streak=streak):
            return None
        return streak

    def turbo_default(self, guild_id: int, user_id: int) -> bool:
        """Si el miembro dejó el turbo puesto la última vez."""
        return self._turbo.get((guild_id, user_id), False)

    def set_turbo_default(self, guild_id: int, user_id: int, turbo: bool) -> None:
        """Recuerda el turbo para la próxima máquina del miembro."""
        self._turbo[(guild_id, user_id)] = turbo

    async def pot(self, guild_id: int) -> int:
        """Bote común del servidor."""
        return await self.economy.slots_pot(guild_id, seed=POT_SEED)

    async def spins_since(self, guild_id: int) -> int:
        """Tiradas pagadas del servidor desde el último bote."""
        return await self.economy.slots_spins_since(guild_id)

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
        daily_streak: int = 0,
    ) -> SlotsPlay:
        """Juega una tirada: decide los rodillos y mueve el dinero de forma atómica.

        Los rodillos se deciden antes de cobrar, pero solo se enseñan si el
        cobro sale bien. El calor solo cambia si la tirada se ha pagado.

        Args:
            stake: Apuesta; en un giro gratis, la que lo activó (no se cobra).
            render: `False` para no dibujar nada (Auto y giros al cerrar).
            daily_streak: Si es el giro del día, la racha (no se cobra, pero
                los 🎟️ cuentan); 0 en el resto.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        key = (guild_id, user_id)
        daily = daily_streak > 0
        charged = not free and not daily
        await self.load_heat(guild_id, user_id)
        self.cool_down(guild_id, user_id)
        spin = self.machine.spin(free=free)
        hot = self.heat(guild_id, user_id) >= HEAT_MAX
        payout = line_payout(spin, stake, hot=hot)
        settlement = await self.economy.play_slots(
            guild_id,
            user_id,
            game=GAME,
            stake=stake if charged else 0,
            payout=payout,
            share=pot_share(stake) if charged else 0,
            jackpot=spin.is_jackpot,
            seed=POT_SEED,
            cap=POT_CAP,
        )
        heat = next_heat(
            self.heat(guild_id, user_id), paid=payout > 0 or spin.is_jackpot, was_hot=hot
        )
        self._heat[key] = heat
        await self._save_heat(guild_id, user_id)
        media = await self._media(
            spin, turbo=turbo, render=render, won=payout + settlement.jackpot, stake=stake
        )
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
            daily=daily,
            daily_streak=daily_streak,
        )

    async def _media(
        self, spin: Spin, *, turbo: bool, render: bool, won: int, stake: int
    ) -> SlotsMedia:
        """Animación e imagen final de una tirada (nada si `render` es falso)."""
        if not render:
            return SlotsMedia(gif=b"", png=b"", seconds=0.0)
        return await asyncio.to_thread(
            self.renderer.render, spin, turbo=turbo, won=won, stake=stake
        )

    async def respin(
        self,
        guild_id: int,
        user_id: int,
        *,
        spin: Spin,
        stake: int,
        price: int,
        turbo: bool,
        session_spins: int,
        chain: int = 1,
    ) -> SlotsPlay:
        """Re-gira el tercer rodillo de `spin` cobrando `price`.

        Es una apuesta más de la tragaperras (juego en el IRPF, como la
        tirada): paga la línea a la apuesta original y, con 🃏 🃏 🃏, el bote.
        No aporta al bote, no calienta la máquina y no da giros gratis.

        Raises:
            InsufficientFundsError: Si el saldo no cubre el precio.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        again = self.machine.respin(spin)
        payout = line_payout(again, stake)
        settlement = await self.economy.play_slots(
            guild_id,
            user_id,
            game=GAME,
            stake=price,
            payout=payout,
            share=0,
            jackpot=again.is_jackpot,
            seed=POT_SEED,
            cap=POT_CAP,
        )
        media = await self._media(
            again, turbo=turbo, render=True, won=payout + settlement.jackpot, stake=stake
        )
        return SlotsPlay(
            spin=again,
            stake=stake,
            free=False,
            hot=False,
            payout=payout,
            settlement=settlement,
            media=media,
            heat=self.heat(guild_id, user_id),
            session_spins=session_spins,
            cost=price,
            respin_chain=chain,
        )

    async def shout(self, play: SlotsPlay, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal el bote y los premios enormes, para que se vea."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        if play.mystery:
            drought = getattr(play.settlement, "drought", 0)
            text = (
                f"📣 💰 ¡Ha caído el bote misterioso! {user.mention} se lleva "
                f"**{format_amount(play.jackpot)}**"
                + (f" tras {drought:,} tiradas sin bote".replace(",", ".") if drought else "")
                + f". {TAX_COLLECTOR} ya se está relamiendo."
            )
        elif play.jackpot:
            text = (
                f"📣 🃏🃏🃏 ¡{user.mention} ha sacado el **JOVANAZO**! Se lleva "
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
        """Lógica compartida entre `/tragas` y `.tragas`: abre la máquina."""
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
        await self.load_heat(guild.id, user.id)
        view.cooled = self.cool_down(guild.id, user.id)
        ready, streak = await self.daily_status(guild.id, user.id)
        view.daily_streak = streak if ready else 0
        view._set_enabled(True)
        stops = self.machine.spin().stops
        png = await asyncio.to_thread(self.renderer.still_png, stops)
        view.message = await send(
            embed=await view.current_embed(balance=balance),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=view,
        )
        self.machines.add(view)
        if view.cooled:
            await logros.track(
                self.bot, guild.id, user, channel, slots_cooled_stats(lost=view.cooled)
            )

    @app_commands.command(name="tragas", description="Tragaperras con bote común, a botones.")
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

    @commands.command(name="tragas")
    @commands.guild_only()
    async def slots_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.tragas` o `.tragas 500`."""

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
    """Registra el cog con la economía y el repositorio compartidos del bot."""
    await bot.add_cog(
        Slots(
            bot,
            economy=bot.economy,
            casino_channel_ids=bot.casino_channel_ids,
            repository=bot.slots_repository,
        )
    )
