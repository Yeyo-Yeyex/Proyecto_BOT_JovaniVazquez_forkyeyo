"""Botes: tres máquinas de monedas y recogida (`volcan`, `olimpo`, `filon`).

Las tres comparten reglas y números (`bot.services.hold_win`) y cambian de
dibujos, efectos y textos. Cada jugador abre su propia máquina, un mensaje con
botones que solo él puede pulsar.

**Juego base.** Botones:

- 🎰 **Tirar**: cobra, gira y paga los ways y la recogida.
- 🔁 **Auto ×10**: diez tiradas sin animación y un resumen. Para si sale un
  bonus, si no llega el dinero o si la banca no puede pagar.
- ⚡ **Turbo**: sin GIF, solo la imagen final (más rápido y casi sin datos).
- **½**, **×2**, 💰 **All-in** cambian la apuesta; 📋 **Premios**, la tabla.

**Bonus.** Cuando un maletín se llena, la máquina pasa al bonus: 🎰 **Girar**
juega una tirada (el contador vuelve a 3 cada vez que cae algo) y ⏩ **Auto**
juega todas las que queden seguidas. Si la máquina se cierra o el bot se apaga
con un bonus a medias, se juega solo hasta el final y se paga: nadie pierde
un bonus.

**Dinero e impuestos.** Cada tirada base es una apuesta del casino
(`EconomyService.settle_bet`) y el bonus se paga al acabar con
`EconomyService.pay_winnings`. Tratamiento fiscal: juego, como la ruleta y la
tragaperras. Los premios de máquinas y casinos son ganancia patrimonial de la
base general (art. 33.1 de la Ley 35/2006, del IRPF) y las pérdidas solo
compensan ganancias de juego del mismo periodo (art. 33.5.d): por eso todo
entra en la retención diaria del casino y en la renta semanal. Los botes
también, porque el gravamen especial del 20 % (disposición adicional 33ª) es
solo para loterías del Estado, ONCE y Cruz Roja. El bonus no cobra apuesta: lo
apostado ya se cobró en las tiradas que llenaron el maletín.

Si `CASINO_CHANNEL_IDS` está configurado, las máquinas solo se abren en esos
canales. Permisos del bot en el canal: enviar mensajes, insertar enlaces
(embeds) y adjuntar archivos.
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
from bot.services.achievements import (
    StatDelta,
    casino_stats,
    hold_win_bonus_stats,
    hold_win_stats,
)
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.hold_win import (
    CASE_SIZE,
    COIN_VALUES,
    GRAND,
    GRAND_BONUS_ODDS,
    MAJOR_BASE,
    MAJOR_COINS,
    MAJOR_MAX,
    MIN_STAKE,
    MINI_BASE,
    MINI_COINS,
    MINI_MAX,
    RESET_VALUE,
    THEMES,
    WAYS_PAYS,
    BaseSpin,
    BonusGame,
    BonusKind,
    BonusResult,
    BonusStep,
    Meters,
    Theme,
    Tier,
    Trigger,
    fill_cases,
    format_multiplier,
    reset_won_jackpots,
    spin_base,
    to_amount,
)
from bot.services.hold_win_render import Banner, BonusPanel, HoldWinRenderer, Media, Panel
from bot.services.levels import TIMEZONE
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.hold_win import HoldWinRepository

logger = logging.getLogger(__name__)

#: Apuesta por defecto al abrir la máquina sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la máquina se cierra.
MACHINE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en empezar el GIF.
REVEAL_MARGIN_SECONDS = 0.4
#: Pausa entre tiradas del ⏩ Auto del bonus en turbo (sin GIF que esperar).
BONUS_AUTO_PAUSE = 1.0
#: Segundos que se ve la tirada que llena el maletín antes de la entrada al bonus.
BONUS_INTRO_PAUSE = 1.5
AUTO_SPINS = 10
#: A partir de cuántas veces la apuesta se anuncia el premio en el canal.
SHOUT_MULTIPLIER = 50
#: Cartel de premio grande, mega y épico (veces la apuesta).
BIG_WIN, MEGA_WIN, EPIC_WIN = 10, 25, 50

GIF_NAME = "botes.gif"
PNG_NAME = "botes.png"

COLOR_IDLE = discord.Color.from_rgb(43, 45, 49)
COLOR_SPIN = discord.Color.from_rgb(222, 178, 70)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_BONUS = discord.Color.from_rgb(170, 60, 255)

TIER_SHAPES = {Tier.GREEN: "🟢 verde", Tier.BLUE: "🔷 azul", Tier.RED: "⭐ roja"}
BONUS_TIER = {BonusKind.GREEN: Tier.GREEN, BonusKind.BLUE: Tier.BLUE, BonusKind.RED: Tier.RED}

# -- Textos en el tono de Jovani Vázquez ---------------------------------------------------

WIN_TITLES = {1: "¡GRAN PREMIO!", 2: "¡MEGA PREMIO!", 3: "¡PREMIO ÉPICO!"}
WIN_LINES = ("¡Wepa!", "¡Eso es, mi amor!", "¡Cobras!", "¡Tilín, tilín!", "¡Acho, qué bueno!")
COLLECT_LINES = {
    "volcan": ("🌋 ¡El volcán escupe dinero!", "🌋 ¡Erupción de monedas!"),
    "olimpo": ("⚡ ¡Zeus te lo recoge todo!", "⚡ ¡Rayazo, mi amor!"),
    "filon": ("🛒 ¡La vagoneta va llena!", "🛒 ¡Todo pa' la vagoneta!"),
}
NEAR_MISS_LINES = (
    "Tanta moneda y sin nadie que la recoja… ¡Ay, bendito!",
    "Las monedas ahí tiradas. Al maletín van, mi amor.",
)
LOSS_LINES = ("La próxima es la buena.", "Nada, mi amor. Otra.", "Calentando…")
BONUS_INTRO = {
    "volcan": "La montaña ruge. Caen monedas y se quedan pegadas a la roca.",
    "olimpo": "Zeus se asoma entre las nubes. Lo que caiga, se queda.",
    "filon": "Se abre una veta nueva. Lo que caiga, se queda en la pared.",
}


def win_tier(points: int) -> int:
    """0 normal, 1 grande, 2 mega o 3 épico, según las veces la apuesta."""
    times = points / 100
    if times >= EPIC_WIN:
        return 3
    if times >= MEGA_WIN:
        return 2
    if times >= BIG_WIN:
        return 1
    return 0


def bonus_name(theme: Theme, kind: str) -> str:
    """`Bonus Lava` o el nombre del gran bonus (`¡ERUPCIÓN!`)."""
    name = theme.bonus_names[kind]
    return name if kind == BonusKind.GRAND else f"Bonus {name}"


@dataclass(frozen=True, slots=True)
class BasePlay:
    """Una tirada base ya cobrada y pagada.

    Attributes:
        payout: Lo cobrado en Y$ (ways más recogida).
        meters: Maletines y botes tras la tirada.
        trigger: Bonus que se dispara, si alguno.
        bonus: Partida de bonus ya empezada, si se dispara.
    """

    spin: BaseSpin
    stake: int
    payout: int
    settlement: BetSettlement
    meters: Meters
    trigger: Trigger | None
    bonus: BonusGame | None
    media: Media
    session_spins: int

    @property
    def net(self) -> int:
        """Ganancia (positiva) o pérdida (negativa)."""
        return self.payout - self.stake

    @property
    def balance(self) -> int:
        """Saldo tras la tirada, con el IRPF ajustado."""
        return self.settlement.balance


@dataclass(frozen=True, slots=True)
class BonusPayout:
    """Un bonus terminado y pagado."""

    result: BonusResult
    stake: int
    amount: int
    settlement: BetSettlement
    kind: str
    steps: tuple[BonusStep, ...]


def base_panel(meters: Meters, stake: int) -> Panel:
    """Botes y maletines del juego base, en Y$ a la apuesta actual."""
    return Panel(
        mini=to_amount(meters.mini, stake),
        major=to_amount(meters.major, stake),
        grand=to_amount(GRAND, stake),
        cases=tuple((meters.cases[t].coins, CASE_SIZE[t]) for t in Tier),
    )


def bonus_panel(theme: Theme, game: BonusGame) -> BonusPanel:
    """Marcadores del bonus."""
    return BonusPanel(
        name=bonus_name(theme, game.kind),
        mini=to_amount(game.mini, game.stake),
        major=to_amount(game.major, game.stake),
        grand=to_amount(GRAND, game.stake),
        respins_left=game.respins_left,
        reset_value=game.reset_value,
        multiplier=game.multiplier,
        coins=game.coins,
        won=game.jackpots(),
        maximized=game.maximized,
    )


def base_banner(spin: BaseSpin, payout: int) -> Banner | None:
    """Cartel de la tirada: solo si paga."""
    if payout <= 0:
        return None
    tier = win_tier(spin.points)
    return Banner(WIN_TITLES.get(tier), f"+{format_amount(payout)}", tier)


def base_text(theme: Theme, play: BasePlay, rng: random.Random | None = None) -> str:
    """Bloque grande con lo que ha pasado en una tirada base."""
    rng = rng or random.Random()
    spin = play.spin
    lines: list[str] = []
    tier = win_tier(spin.points)
    if tier:
        lines.append(f"# 💥 {WIN_TITLES[tier]}")
        lines.append(f"## +{format_amount(play.payout)}")
    elif play.net > 0:
        lines.append(f"# {rng.choice(WIN_LINES)} +{format_amount(play.net)}")
    elif play.payout > 0:
        lines.append(f"## Cobras {format_amount(play.payout)}")
    elif spin.near_miss:
        lines.append(f"## {rng.choice(NEAR_MISS_LINES)}")
    else:
        lines.append(f"## -{format_amount(play.stake)} · {rng.choice(LOSS_LINES)}")

    if spin.collectors:
        times = " ×2" if spin.collectors == 2 else ""
        lines.append(
            f"{rng.choice(COLLECT_LINES[theme.key])} {len(spin.coins)} monedas{times}: "
            f"+{format_amount(to_amount(spin.collect_points, play.stake))}"
        )
    best = max(spin.wins, key=lambda w: w.points, default=None)
    if best is not None:
        emoji = theme.symbols[best.symbol][0]
        ways = f" · {best.ways} ways" if best.ways > 1 else ""
        lines.append(
            f"{emoji * best.reels} +{format_amount(to_amount(best.points, play.stake))}{ways}"
        )
    chips = spin.chips
    if chips["mini"] or chips["major"]:
        parts = [f"{n} {name.upper()}" for name, n in chips.items() if n]
        lines.append(f"🎰 Ficha de bote: {' y '.join(parts)} suben.")
    if play.trigger is not None:
        name = bonus_name(theme, play.trigger.kind)
        lines.append(f"### 🎁 ¡{name.strip('¡!')}! Se llena el maletín.")
        if play.trigger.upgraded:
            lines.append("💫 ¡Y sube al gran bonus, con monedas de los tres colores!")
    return "\n".join(lines)


def bonus_text(theme: Theme, game: BonusGame, step: BonusStep | None) -> str:
    """Texto del bonus tras una tirada (o al empezar)."""
    lines = [f"# 🎁 {bonus_name(theme, game.kind)}"]
    if step is None:
        lines.append(BONUS_INTRO[theme.key])
        lines.append(
            f"Quedan **{game.respins_left}** tiradas y vuelven a {game.reset_value} cada vez "
            "que cae algo."
        )
    else:
        if step.landings:
            coins = sum(1 for landing in step.landings if landing.cell.is_coin)
            parts = []
            if coins:
                parts.append("una moneda" if coins == 1 else f"{coins} monedas")
            if step.multiplier_added:
                parts.append(f"ticket +×{step.multiplier_added}")
            if step.extra:
                parts.append(f"+{step.extra} tirada extra")
            if step.instant != 10:
                factor = f"{step.instant / 10:.1f}".rstrip("0").rstrip(".").replace(".", ",")
                parts.append(f"¡las monedas ×{factor}!")
            if step.maximized:
                parts.append("¡botes al máximo!")
            lines.append(f"## ¡Cae {', '.join(parts)}! Vuelve a {game.respins_left}.")
        else:
            left = game.respins_left
            if left == 1:
                lines.append("## Nada… ¡queda la última!")
            elif left:
                lines.append(f"## Nada… quedan {left}.")
            else:
                lines.append("## Se acabó.")
    current = to_amount(game.coin_points, game.stake) * game.multiplier
    lines.append(
        f"Monedas: **{game.coins}/20** · en la mesa {format_amount(current)}"
        + (f" (×{game.multiplier})" if game.multiplier > 1 else "")
    )
    won = game.jackpots()
    if won:
        lines.append("🏆 Asegurado: " + " + ".join(name.upper() for name in won))
    if game.coins < MINI_COINS:
        lines.append(f"-# {MINI_COINS - game.coins} para el MINI")
    elif game.coins < MAJOR_COINS:
        lines.append(f"-# {MAJOR_COINS - game.coins} para el MAJOR")
    elif game.coins < 20:
        lines.append(f"-# ¡{20 - game.coins} para el GRAND!")
    return "\n".join(lines)


def bonus_end_text(theme: Theme, payout: BonusPayout) -> str:
    """Resumen del bonus pagado."""
    result = payout.result
    tier = win_tier(result.points)
    title = WIN_TITLES.get(tier, "¡Bonus cobrado!")
    lines = [f"# 🎁 {title}", f"## +{format_amount(payout.amount)}"]
    coins = to_amount(result.coin_points, payout.stake)
    detail = f"{result.coins} monedas · {format_amount(coins)}"
    if result.multiplier > 1:
        detail += f" ×{result.multiplier}"
    lines.append(detail)
    for name, value in zip(result.jackpots, result.jackpot_values, strict=True):
        lines.append(f"### 🏆 ¡{name.upper()}! +{format_amount(to_amount(value, payout.stake))}")
    if result.coins == 19:
        lines.append("¡Una moneda para el GRAND! ¡Ay, bendito!")
    return "\n".join(lines)


def paytable_embed(theme: Theme) -> discord.Embed:
    """Tabla de premios y reglas de una máquina (se manda en privado)."""
    lines = ["**Ways** (de izquierda a derecha, cualquier fila; 3, 4 y 5 rodillos):"]
    for index in reversed(range(len(theme.symbols))):
        emoji, _name = theme.symbols[index]
        pays = " / ".join(format_multiplier(p) for p in WAYS_PAYS[index])
        lines.append(f"{emoji} {pays} por way")
    lines.append(f"{theme.wild[0]} comodín en los rodillos 2 a 4 (no sustituye monedas).")
    lines.append("")
    lines.append("**Monedas** (llevan el premio escrito):")
    for tier in Tier:
        values = [v for v, _w in COIN_VALUES[tier]]
        lines.append(
            f"{TIER_SHAPES[tier]}: {format_multiplier(min(values))} a "
            f"{format_multiplier(max(values))} la apuesta"
        )
    lines.append(
        f"{theme.collect[0]} en el rodillo 1 o el 5 recoge todas las monedas; en los dos, el doble."
    )
    lines.append("")
    lines.append("**Maletines**: cada moneda que cae entra en el de su color, se cobre o no.")
    lines.append(
        " · ".join(f"{TIER_SHAPES[t].split()[0]} {CASE_SIZE[t]}" for t in Tier)
        + f" monedas. Lleno = bonus. 1 de cada {GRAND_BONUS_ODDS} sube al gran bonus."
    )
    lines.append("")
    lines.append(
        f"**Bonus**: {RESET_VALUE} tiradas; cada vez que cae algo vuelven a {RESET_VALUE}. "
        "Las monedas se quedan. 🎟️ suma al multiplicador, 🔁 +1 tirada, 💥 multiplica las "
        "monedas, 🏆 sube los botes al máximo y ❓ es una sorpresa."
    )
    lines.append(
        f"**Botes**: MINI con {MINI_COINS} monedas ({format_multiplier(MINI_BASE)} a "
        f"{format_multiplier(MINI_MAX)}), MAJOR con {MAJOR_COINS} "
        f"({format_multiplier(MAJOR_BASE)} a {format_multiplier(MAJOR_MAX)}) y GRAND con la "
        f"pantalla llena ({format_multiplier(GRAND)}). Las fichas del juego base suben el MINI "
        "y el MAJOR."
    )
    lines.append(f"-# Apuesta mínima: {format_amount(MIN_STAKE)}. Retorno medio ~94 %.")
    return discord.Embed(
        title=f"📋 {theme.emoji} {theme.title}: premios y reglas",
        description="\n".join(lines),
        color=COLOR_WIN,
    )


def parse_stake(amount_text: str | None, balance: int) -> int:
    """Apuesta del comando: la indicada (`500`, `2k`, `all`) o la de por defecto.

    Raises:
        ValueError: Con un mensaje mostrable si no se entiende o no llega al mínimo.
    """
    if not amount_text:
        return max(MIN_STAKE, min(DEFAULT_STAKE, balance))
    stake = parse_amount(amount_text, balance)
    if stake < MIN_STAKE:
        raise ValueError(f"La apuesta mínima es {format_amount(MIN_STAKE)}.")
    return stake


def tax_note(settlements: list[BetSettlement]) -> str | None:
    """Línea de IRPF de una jugada o de varias (suma de ajustes)."""
    if not settlements:
        return None
    if len(settlements) == 1:
        return gambling_tax_line(settlements[0])
    delta = sum(s.tax_delta for s in settlements)
    last = settlements[-1]
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


EditFn = Callable[..., Awaitable[Any]]


# -- Máquina ------------------------------------------------------------------------


class HoldWinView(discord.ui.View):
    """Máquina de un jugador: un mensaje con botones.

    Guarda la apuesta, el turbo y el bonus en curso. No guarda dinero ni
    maletines: se leen y se cambian siempre a través del cog.
    """

    def __init__(
        self, cog: HoldWin, *, theme: Theme, guild_id: int, owner: discord.abc.User, stake: int
    ) -> None:
        super().__init__(timeout=MACHINE_TIMEOUT)
        self.cog = cog
        self.theme = theme
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.turbo = cog.turbo_default(guild_id, owner.id)
        self.session_spins = 0
        self.bonus: BonusGame | None = None
        self.last_text: str | None = None
        self.last_won: bool | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        self._build_buttons()

    # -- Botones --------------------------------------------------------------------

    def _add(
        self,
        label: str,
        row: int,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        custom_id: str,
    ) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=style, row=row, custom_id=f"{self.theme.key}:{custom_id}"
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
        self.half_button = self._add("½", 1, self._halve, custom_id="half")
        self.double_button = self._add("×2", 1, self._double_stake, custom_id="x2")
        self.allin_button = self._add("💰 All-in", 1, self._all_in, custom_id="allin")
        self._add("📋 Premios", 1, self._paytable, custom_id="paytable")
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        """Activa o desactiva los botones y pone al día sus etiquetas según el modo."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = not enabled
        if self.bonus is not None:
            self.spin_button.label = f"🎰 Girar ({self.bonus.respins_left})"
            self.spin_button.style = discord.ButtonStyle.primary
            self.auto_button.label = "⏩ Auto bonus"
            for button in (self.half_button, self.double_button, self.allin_button):
                button.disabled = True
        else:
            self.spin_button.label = f"🎰 Tirar · {format_amount(self.stake)}"
            self.spin_button.style = discord.ButtonStyle.success
            self.auto_button.label = f"🔁 Auto ×{AUTO_SPINS}"
        self.turbo_button.label = "⚡ Turbo: sí" if self.turbo else "⚡ Turbo"
        self.turbo_button.style = (
            discord.ButtonStyle.success if self.turbo else discord.ButtonStyle.secondary
        )

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega; al resto se le invita a abrir la suya."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta máquina es de {self.owner.display_name}. Abre la tuya con `{self.theme.key}`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Cierra la máquina; si queda un bonus, se juega hasta el final y se paga."""
        self.cog.machines.discard(self)
        payout = await self.finish_bonus_unattended()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        kwargs: dict[str, Any] = {"view": self}
        if payout is not None:
            text = bonus_end_text(self.theme, payout) + "\n-# Bonus jugado al cerrar la máquina."
            kwargs["embed"] = await self.current_embed(text=text, won=True)
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la máquina de botes", exc_info=True)

    async def finish_bonus_unattended(self) -> BonusPayout | None:
        """Juega y paga el bonus pendiente sin enseñarlo (cierre o apagado)."""
        if self.bonus is None:
            return None
        game, self.bonus = self.bonus, None
        try:
            game.play_out(self.cog.rng)
            payout = await self.cog.pay_bonus(self.guild_id, self.owner.id, self.theme, game)
        except Exception:
            logger.exception("No se pudo pagar un bonus al cerrar la máquina")
            return None
        await self._track_bonus(payout)
        return payout

    # -- Embeds ---------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(
        self,
        *,
        balance: int | None = None,
        text: str | None = None,
        won: bool | None = None,
        image: str = PNG_NAME,
    ) -> discord.Embed:
        """Embed de la máquina con el saldo actual."""
        if balance is None:
            balance = await self.balance()
        if text is None:
            text = self.last_text
        if text is None:
            text = (
                f"Pulsa 🎰 **Tirar**. Los símbolos pagan por **ways** y "
                f"{self.theme.collect[0]} recoge las monedas.\n"
                "Cada moneda llena su **maletín**: lleno, **bonus**. "
                "📋 **Premios** para las reglas."
            )
        if self.bonus is None and balance < self.stake:
            text += "\n\n**No te llega para esta apuesta.** Bájala o usa `imv`."
        if won is None:
            won = self.last_won
        if self.bonus is not None:
            color = COLOR_BONUS
        elif won is None:
            color = COLOR_IDLE
        else:
            color = COLOR_WIN if won else COLOR_LOSS
        embed = discord.Embed(
            title=f"{self.theme.emoji} {self.theme.title}", description=text, color=color
        )
        embed.add_field(name="Saldo", value=format_amount(balance))
        if self.bonus is not None:
            embed.add_field(name="Apuesta del bonus", value=format_amount(self.bonus.stake))
        else:
            embed.add_field(name="Apuesta", value=format_amount(self.stake))
        embed.set_image(url=f"attachment://{image}")
        footer = f"Máquina de {self.owner.display_name}"
        if self.turbo:
            footer += " · ⚡ Turbo"
        embed.set_footer(text=footer)
        return embed

    # -- Juego base -----------------------------------------------------------------

    async def _guarded_play(
        self, interaction: discord.Interaction, *, turbo: bool, render: bool = True
    ) -> BasePlay | None:
        """Una tirada base con los errores de saldo convertidos en avisos privados."""
        try:
            play = await self.cog.play_base(
                self.guild_id,
                self.owner.id,
                self.theme,
                stake=self.stake,
                turbo=turbo,
                render=render,
                session_spins=self.session_spins + 1,
            )
        except InsufficientFundsError as error:
            await _private(interaction, insufficient_text(error.balance, self.stake))
            return None
        except BalanceLimitError:
            await _private(interaction, "La banca no puede pagar tanto. Baja la apuesta.")
            return None
        self.session_spins += 1
        if play.bonus is not None:
            self.bonus = play.bonus
        return play

    async def _spin(self, interaction: discord.Interaction) -> None:
        """🎰: en el juego base, una tirada; en el bonus, una tirada del bonus."""
        if self._busy:
            await interaction.response.defer()
            return
        if self.bonus is not None:
            await self._bonus_spin(interaction)
            return
        self._busy = True
        play: BasePlay | None = None
        try:
            play = await self._guarded_play(interaction, turbo=self.turbo)
            if play is None:
                return
            self._last_interaction = interaction
            await self.show_base(
                play,
                first_edit=interaction.response.edit_message,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        await self._track_base(play)
        await self.cog.shout_base(self.theme, play, self.owner, self._channel())

    def _channel(self) -> object:
        return getattr(self.message, "channel", None)

    async def show_base(self, play: BasePlay, *, first_edit: EditFn, final_edit: EditFn) -> None:
        """Enseña la tirada (GIF y PNG, o solo PNG en turbo) y, si toca, la entrada al bonus."""
        if play.media.gif:
            self._set_enabled(False)
            await first_edit(
                embed=self._spinning_embed(),
                attachments=[discord.File(io.BytesIO(play.media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(play.media.seconds + REVEAL_MARGIN_SECONDS)
            edit = final_edit
        else:
            edit = first_edit
        text = base_text(self.theme, play)
        if note := tax_note([play.settlement]):
            text += f"\n{note}"
        if hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id):
            text += f"\n{hint}"
        self.last_text = text
        self.last_won = play.payout > 0
        png = play.media.png
        if self.bonus is not None:
            # La tirada que llena el maletín enseña ya la entrada al bonus.
            await edit(
                embed=await self.current_embed(balance=play.balance, text=text),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            await asyncio.sleep(BONUS_INTRO_PAUSE)
            await self._show_bonus_intro(final_edit)
            return
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(balance=play.balance, text=text),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )

    def _spinning_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"{self.theme.emoji} {self.theme.title}",
            description=f"# 🌀 ¡Girando!\n{format_amount(self.stake)}",
            color=COLOR_SPIN,
        )
        embed.set_image(url=f"attachment://{GIF_NAME}")
        embed.set_footer(text=f"Máquina de {self.owner.display_name}")
        return embed

    async def _show_bonus_intro(self, edit: EditFn) -> None:
        assert self.bonus is not None
        game = self.bonus
        png = await asyncio.to_thread(
            self.cog.renderer.bonus_still,
            self.theme.key,
            game.board,
            stake=game.stake,
            panel=bonus_panel(self.theme, game),
            banner=Banner(bonus_name(self.theme, game.kind), f"a {format_amount(game.stake)}", 2),
        )
        self.last_text = bonus_text(self.theme, game, None)
        self.last_won = None
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )

    async def _track_base(self, play: BasePlay | None) -> None:
        """Logros de la tirada, después de enseñarla."""
        if play is None:
            return
        delta = hold_win_stats(
            play.spin,
            theme=self.theme.key,
            stake=play.stake,
            payout=play.payout,
            trigger=play.trigger,
            turbo=not play.media.gif,
            session_spins=play.session_spins,
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
            self.cog.bot, self.guild_id, self.owner, self._channel(), delta, net=play.net
        )

    async def _auto(self, interaction: discord.Interaction) -> None:
        """🔁 en el juego base: diez tiradas. ⏩ en el bonus: todas las que queden."""
        if self._busy:
            await interaction.response.defer()
            return
        if self.bonus is not None:
            await self._bonus_auto(interaction)
            return
        self._busy = True
        plays: list[BasePlay] = []
        stopped: str | None = None
        try:
            await interaction.response.defer()
            self._last_interaction = interaction
            for _ in range(AUTO_SPINS):
                try:
                    play = await self.cog.play_base(
                        self.guild_id,
                        self.owner.id,
                        self.theme,
                        stake=self.stake,
                        turbo=True,
                        render=False,
                        session_spins=self.session_spins + 1,
                    )
                except InsufficientFundsError:
                    stopped = "Parado: no te llega para otra tirada."
                    break
                except BalanceLimitError:
                    stopped = "Parado: la banca no puede pagar tanto."
                    break
                self.session_spins += 1
                plays.append(play)
                if play.bonus is not None:
                    self.bonus = play.bonus
                    stopped = "Parado: ¡se ha llenado un maletín!"
                    break
            if not plays:
                await interaction.followup.send(
                    insufficient_text(await self.balance(), self.stake), ephemeral=True
                )
                return
            last = plays[-1]
            png = await asyncio.to_thread(
                self.cog.renderer.base_still,
                self.theme.key,
                last.spin,
                stake=last.stake,
                panel=base_panel(last.meters, last.stake),
                highlight=last.payout > 0,
            )
            text = self.auto_text(plays, stopped)
            if note := tax_note([p.settlement for p in plays]):
                text += f"\n{note}"
            if hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id):
                text += f"\n{hint}"
            self.last_text = text
            self.last_won = sum(p.net for p in plays) > 0
            self._set_enabled(True)
            if self.bonus is not None:
                self._set_enabled(False)
            await interaction.edit_original_response(
                embed=await self.current_embed(balance=last.balance, text=text),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            if self.bonus is not None:
                await asyncio.sleep(BONUS_INTRO_PAUSE)
                await self._show_bonus_intro(interaction.edit_original_response)
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        for play in plays:
            await self._track_base(play)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self._channel(),
            StatDelta(add={"botes_auto": 1}),
        )
        for play in plays:
            await self.cog.shout_base(self.theme, play, self.owner, self._channel())

    def auto_text(self, plays: list[BasePlay], stopped: str | None) -> str:
        """Resumen de una ronda de Auto."""
        net = sum(p.net for p in plays)
        sign = "+" if net > 0 else "-" if net < 0 else "±"
        paid = sum(1 for p in plays if p.payout > 0)
        collects = sum(1 for p in plays if p.spin.collectors)
        lines = [
            f"# 🔁 {len(plays)} tiradas · {sign}{format_amount(abs(net))}",
            f"{paid} con premio · {collects} recogidas · apostado "
            f"{format_amount(sum(p.stake for p in plays))}",
        ]
        best = max(plays, key=lambda p: p.payout)
        if best.payout > 0:
            lines.append(f"Mejor tirada: +{format_amount(best.payout)}")
        if stopped:
            lines.append(f"-# {stopped}")
        return "\n".join(lines)

    # -- Bonus ----------------------------------------------------------------------

    async def _bonus_spin(self, interaction: discord.Interaction) -> None:
        """Una tirada del bonus, con su animación."""
        assert self.bonus is not None
        self._busy = True
        payout: BonusPayout | None = None
        try:
            self._last_interaction = interaction
            payout = await self._bonus_step(
                first_edit=interaction.response.edit_message,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        if payout is not None:
            await renta.remind(self.cog.bot, interaction)
            await self._track_bonus(payout)
            await self.cog.shout_bonus(self.theme, payout, self.owner, self._channel())

    async def _bonus_auto(self, interaction: discord.Interaction) -> None:
        """Juega el bonus entero, tirada a tirada, editando el mensaje."""
        self._busy = True
        payout: BonusPayout | None = None
        try:
            await interaction.response.defer()
            self._last_interaction = interaction
            while self.bonus is not None:
                payout = await self._bonus_step(
                    first_edit=interaction.edit_original_response,
                    final_edit=interaction.edit_original_response,
                )
                if self.bonus is not None and self.turbo:
                    await asyncio.sleep(BONUS_AUTO_PAUSE)
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        if payout is not None:
            await self._track_bonus(payout)
            await self.cog.shout_bonus(self.theme, payout, self.owner, self._channel())

    async def _bonus_step(self, *, first_edit: EditFn, final_edit: EditFn) -> BonusPayout | None:
        """Juega, dibuja y enseña una tirada del bonus; si termina, lo paga.

        El bonus se paga antes de enseñar el último fotograma: si Discord
        falla al editar, el dinero ya está en su sitio.
        """
        game = self.bonus
        assert game is not None
        before = list(game.board)
        panel_before = bonus_panel(self.theme, game)
        step = game.step(self.cog.rng)
        payout: BonusPayout | None = None
        banner: Banner | None = None
        if game.finished:
            self.bonus = None
            payout = await self.cog.pay_bonus(self.guild_id, self.owner.id, self.theme, game)
            banner = Banner(
                WIN_TITLES.get(win_tier(payout.result.points), "¡BONUS COBRADO!"),
                f"+{format_amount(payout.amount)}",
                max(1, win_tier(payout.result.points)),
            )
        media = await asyncio.to_thread(
            self.cog.renderer.render_bonus_step,
            self.theme.key,
            before,
            step,
            stake=game.stake,
            panel_before=panel_before,
            panel_after=bonus_panel(self.theme, game),
            banner=banner,
            turbo=self.turbo,
        )
        if payout is None:
            text = bonus_text(self.theme, game, step)
            won: bool | None = None
            balance = None
        else:
            text = bonus_end_text(self.theme, payout)
            if note := tax_note([payout.settlement]):
                text += f"\n{note}"
            if hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id):
                text += f"\n{hint}"
            won = True
            balance = payout.settlement.balance
        self.last_text = text
        self.last_won = won
        if media.gif:
            self._set_enabled(False)
            embed = await self.current_embed(
                text=f"# 🎁 {bonus_name(self.theme, game.kind)}\n🌀 Girando…", image=GIF_NAME
            )
            await first_edit(
                embed=embed,
                attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
            edit = final_edit
        else:
            edit = first_edit
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(balance=balance, text=text, won=won),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )
        return payout

    async def _track_bonus(self, payout: BonusPayout) -> None:
        delta = hold_win_bonus_stats(payout.result, payout.steps, amount=payout.amount)
        delta.merge(
            casino_stats(
                stake=0,
                net=payout.amount,
                balance_after=payout.settlement.balance,
                tax_delta=payout.settlement.tax_delta,
            )
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self._channel(), delta, net=payout.amount
        )

    # -- Ajustes --------------------------------------------------------------------

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
            await _private(interaction, insufficient_text(balance, MIN_STAKE))
            return
        self.stake = balance
        await self._refresh(interaction, balance)

    async def _paytable(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=paytable_embed(self.theme), ephemeral=True)


async def _private(interaction: discord.Interaction, text: str) -> None:
    """Aviso que solo ve quien pulsa, responda ya o no a la interacción."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


# -- Cog ----------------------------------------------------------------------------


class HoldWin(commands.Cog, name="Botes"):
    """Las máquinas de Botes: Volcán, Olimpo y Filón."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: HoldWinRepository,
        renderer: HoldWinRenderer | None = None,
        rng: random.Random | None = None,
        casino_channel_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.renderer = renderer or HoldWinRenderer()
        self.rng = rng or random.SystemRandom()
        self.casino_channel_ids = casino_channel_ids
        self._turbo: dict[tuple[int, int], bool] = {}
        # Un cerrojo por jugador y máquina: leer, cambiar y guardar los
        # maletines no puede mezclarse con otra tirada del mismo jugador.
        self._locks: dict[tuple[int, int, str], asyncio.Lock] = {}
        self.machines: set[HoldWinView] = set()
        self._warm_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        """Prepara fondos, símbolos y paletas en segundo plano."""

        def warm() -> None:
            spin = spin_base(random.Random(0))
            for key in THEMES:
                self.renderer.base_still(
                    key, spin, stake=DEFAULT_STAKE, panel=base_panel(Meters(), DEFAULT_STAKE)
                )

        self._warm_task = asyncio.create_task(asyncio.to_thread(warm), name="botes-warm-up")

    async def cog_unload(self) -> None:
        """Al apagar, juega y paga los bonus a medias."""
        if self._warm_task is not None:
            self._warm_task.cancel()
        for view in list(self.machines):
            try:
                await view.finish_bonus_unattended()
            except Exception:
                logger.exception("No se pudo pagar un bonus al apagar")
            view.stop()
        self.machines.clear()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los maletines y botes del servidor que deja el bot."""
        await self.repository.delete_guild_data(guild.id)

    def turbo_default(self, guild_id: int, user_id: int) -> bool:
        """Si el miembro dejó el turbo puesto la última vez."""
        return self._turbo.get((guild_id, user_id), False)

    def set_turbo_default(self, guild_id: int, user_id: int, turbo: bool) -> None:
        """Recuerda el turbo para la próxima máquina del miembro."""
        self._turbo[(guild_id, user_id)] = turbo

    def _lock(self, guild_id: int, user_id: int, theme: str) -> asyncio.Lock:
        key = (guild_id, user_id, theme)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def meters(self, guild_id: int, user_id: int, theme: str) -> Meters:
        """Maletines y botes guardados de un jugador en una máquina."""
        return await self.repository.load(guild_id, user_id, theme)

    # -- Dinero ---------------------------------------------------------------------

    async def play_base(
        self,
        guild_id: int,
        user_id: int,
        theme: Theme,
        *,
        stake: int,
        turbo: bool,
        render: bool = True,
        session_spins: int = 1,
    ) -> BasePlay:
        """Juega una tirada base: cobra, paga, llena maletines y, si toca, empieza el bonus.

        La rejilla se decide antes de cobrar, pero los maletines solo cambian
        si el cobro sale bien.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        if stake < MIN_STAKE:
            raise ValueError(f"La apuesta mínima es {MIN_STAKE}.")
        spin = spin_base(self.rng)
        payout = to_amount(spin.points, stake)
        async with self._lock(guild_id, user_id, theme.key):
            settlement = await self.economy.settle_bet(
                guild_id, user_id, game=theme.key, stake=stake, payout=payout
            )
            meters = await self.repository.load(guild_id, user_id, theme.key)
            trigger = fill_cases(meters, spin, stake, self.rng)
            await self.repository.save(guild_id, user_id, theme.key, meters)
        bonus = None
        if trigger is not None:
            tiers = set(Tier) if trigger.kind == BonusKind.GRAND else {BONUS_TIER[trigger.kind]}
            seeds = [i for i, cell in spin.coins if cell.tier in tiers]
            bonus = BonusGame.start(
                trigger, mini=meters.mini, major=meters.major, rng=self.rng, seed_cells=seeds
            )
        if render:
            media = await asyncio.to_thread(
                self.renderer.render_base,
                theme.key,
                spin,
                stake=stake,
                panel=base_panel(meters, stake),
                banner=base_banner(spin, payout),
                turbo=turbo,
            )
        else:
            media = Media(gif=b"", png=b"", seconds=0.0)
        return BasePlay(
            spin=spin,
            stake=stake,
            payout=payout,
            settlement=settlement,
            meters=meters,
            trigger=trigger,
            bonus=bonus,
            media=media,
            session_spins=session_spins,
        )

    async def pay_bonus(
        self, guild_id: int, user_id: int, theme: Theme, game: BonusGame
    ) -> BonusPayout:
        """Paga un bonus terminado y devuelve a su base los botes ganados.

        Tratamiento fiscal: juego (`pay_winnings`), ver el docstring del módulo.
        """
        result = game.result()
        amount = to_amount(result.points, game.stake)
        async with self._lock(guild_id, user_id, theme.key):
            settlement = await self.economy.pay_winnings(
                guild_id, user_id, game=theme.key, amount=amount
            )
            if result.jackpots:
                meters = await self.repository.load(guild_id, user_id, theme.key)
                reset_won_jackpots(meters, result.jackpots)
                await self.repository.save(guild_id, user_id, theme.key, meters)
        return BonusPayout(
            result=result,
            stake=game.stake,
            amount=amount,
            settlement=settlement,
            kind=game.kind,
            steps=tuple(game.history),
        )

    # -- Anuncios -------------------------------------------------------------------

    async def _shout(self, channel: object, user: discord.abc.User, text: str) -> None:
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio de botes", exc_info=True)

    async def shout_base(
        self, theme: Theme, play: BasePlay, user: discord.abc.User, channel: object
    ) -> None:
        """Anuncia en el canal las tiradas base enormes."""
        if play.stake and play.payout >= SHOUT_MULTIPLIER * play.stake:
            await self._shout(
                channel,
                user,
                f"📣 {theme.emoji} ¡{user.mention} acaba de ganar "
                f"**{format_amount(play.payout)}** en el {theme.title}!",
            )

    async def shout_bonus(
        self, theme: Theme, payout: BonusPayout, user: discord.abc.User, channel: object
    ) -> None:
        """Anuncia los botes MAJOR y GRAND y los bonus enormes."""
        jackpots = payout.result.jackpots
        if "grand" in jackpots:
            text = (
                f"📣 🏆🏆🏆 ¡{user.mention} ha llenado la pantalla del {theme.title} y se lleva "
                f"el **GRAND**! **{format_amount(payout.amount)}** en total. {TAX_COLLECTOR} "
                "ya está afilando los dientes."
            )
        elif "major" in jackpots:
            text = (
                f"📣 🏆 ¡{user.mention} saca el **MAJOR** en el {theme.title}! "
                f"**{format_amount(payout.amount)}** del bonus."
            )
        elif payout.amount >= SHOUT_MULTIPLIER * payout.stake:
            text = (
                f"📣 {theme.emoji} ¡{user.mention} revienta el bonus del {theme.title}: "
                f"**{format_amount(payout.amount)}**!"
            )
        else:
            return
        await self._shout(channel, user, text)

    # -- Comandos -------------------------------------------------------------------

    async def _open(
        self,
        theme: Theme,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/x` y `.x`: abre la máquina."""
        if guild is None:
            await send_error(f"El {theme.title} solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, f"El {theme.title}"):
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
        view = HoldWinView(self, theme=theme, guild_id=guild.id, owner=user, stake=stake)
        meters = await self.meters(guild.id, user.id, theme.key)
        png = await asyncio.to_thread(
            self.renderer.base_still,
            theme.key,
            spin_base(self.rng),
            stake=stake,
            panel=base_panel(meters, stake),
        )
        view.message = await send(
            embed=await view.current_embed(balance=balance),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=view,
        )
        self.machines.add(view)

    async def _slash(
        self, interaction: discord.Interaction, theme_key: str, cantidad: str | None
    ) -> None:
        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._open(
            THEMES[theme_key],
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )

    async def _text(self, ctx: commands.Context, theme_key: str, cantidad: str | None) -> None:
        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._open(
            THEMES[theme_key],
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )

    @app_commands.command(name="volcan", description="Volcán: monedas, recogida y bonus de lava.")
    @app_commands.describe(cantidad="Apuesta por tirada: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def volcan(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Abre la máquina del Volcán. Mueve yapdollars a través de la economía."""
        await self._slash(interaction, "volcan", cantidad)

    @commands.command(name="volcan")
    @commands.guild_only()
    async def volcan_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.volcan` o `.volcan 500`."""
        await self._text(ctx, "volcan", cantidad)

    @app_commands.command(name="olimpo", description="Olimpo: rayos de Zeus, monedas y botes.")
    @app_commands.describe(cantidad="Apuesta por tirada: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def olimpo(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Abre la máquina del Olimpo. Mueve yapdollars a través de la economía."""
        await self._slash(interaction, "olimpo", cantidad)

    @commands.command(name="olimpo")
    @commands.guild_only()
    async def olimpo_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.olimpo` o `.olimpo 500`."""
        await self._text(ctx, "olimpo", cantidad)

    @app_commands.command(name="filon", description="Filón: la mina de monedas y vagonetas.")
    @app_commands.describe(cantidad="Apuesta por tirada: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def filon(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Abre la máquina del Filón. Mueve yapdollars a través de la economía."""
        await self._slash(interaction, "filon", cantidad)

    @commands.command(name="filon")
    @commands.guild_only()
    async def filon_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.filon` o `.filon 500`."""
        await self._text(ctx, "filon", cantidad)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el repositorio compartidos del bot."""
    await bot.add_cog(
        HoldWin(
            bot,
            economy=bot.economy,
            repository=bot.hold_win,
            casino_channel_ids=bot.casino_channel_ids,
        )
    )
