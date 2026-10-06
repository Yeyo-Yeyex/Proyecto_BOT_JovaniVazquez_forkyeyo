"""Casino y economía: `ruleta`, `saldo`, `imv` (la recompensa diaria) y `hacienda`.

Todo el dinero se mueve con `EconomyService` (`bot.economy`), que es la
misma economía que usará cualquier juego o sistema futuro. Este cog solo
traduce botones y comandos a llamadas al servicio y pinta el resultado.

La ruleta es americana (0 y 00) e individual: cada jugador abre su propia
mesa, un mensaje con botones que solo él puede pulsar. Tiene dos modos:

- **Rápido** (por defecto): cada clic en una apuesta cobra, gira y paga.
- **Varias apuestas**: cada clic pone una ficha en la mesa y 🎰 Girar las
  juega todas en la misma tirada.

Si `CASINO_CHANNEL_IDS` está configurado, la ruleta solo se abre en esos
canales. Permisos que necesita el bot en el canal: enviar mensajes,
insertar enlaces (embeds) y adjuntar archivos (el GIF de la rueda).
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.services.achievements import StatDelta, casino_stats, hacienda_stats, roulette_stats
from bot.services.economy import (
    CURRENCY_EMOJI,
    CURRENCY_NAME,
    IMV_FLOOR_SHARE,
    IMV_WORK_EXEMPT,
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    daily_amount,
    format_amount,
    gambling_tax_line,
    is_all_in,
    parse_amount,
    treasury_embed,
)
from bot.services.levels import TIMEZONE
from bot.services.roulette import (
    COLOR_EMOJI,
    OUTSIDE_BETS,
    POCKETS,
    Bet,
    RoundOutcome,
    Wager,
    Wheel,
    add_wager,
    color,
    label,
    parse_bet,
    parse_bets,
    play_round,
    pretty,
)
from bot.services.roulette_render import SPIN_SECONDS, SpinMedia, WheelRenderer
from bot.services.tax_report import add_bill_fields, member_bill_embed, server_bill
from bot.services.tax_report import bills as tax_bills
from bot.services.taxes import TAX_COLLECTOR, WEALTH_MINIMUM, wealth_tax
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "ruleta"
#: Ficha por defecto al abrir una mesa sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la mesa se cierra.
TABLE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en descargar el GIF y
#: empezar a reproducirlo; sin margen se cortaría el final del frenazo.
REVEAL_MARGIN_SECONDS = 0.4
#: Últimos números que se muestran en la mesa, por servidor.
HISTORY_SIZE = 12

GIF_NAME = "ruleta.gif"


PNG_NAME = "ruleta.png"

COLOR_IDLE = discord.Color.from_rgb(43, 45, 49)
COLOR_SPIN = discord.Color.from_rgb(222, 178, 70)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)

WIN_LINES = ("¡Ganas", "¡Toma ya!", "¡Cobras", "¡Dentro!", "¡Bingo!")
BIG_WIN_LINES = ("💥 ¡PLENAZO!", "💥 ¡REVIENTAS LA BANCA!", "💥 ¡LOCURA!")
LOSS_LINES = ("Casi.", "La próxima es la buena.", "La bola no quiso.", "Uf, por poco.")


# -- Presentación -------------------------------------------------------------------


def history_line(history: Iterable[int]) -> str:
    """Últimos resultados con su color, el más reciente primero."""
    items = [f"{COLOR_EMOJI[color(p)]}{label(p)}" for p in history]
    return "Últimos: " + " · ".join(items) if items else "Aún no ha salido ningún número."


def wagers_total(wagers: Sequence[Wager]) -> int:
    """Suma de las fichas de varias apuestas."""
    return sum(w.stake for w in wagers)


def wagers_lines(wagers: Sequence[Wager]) -> str:
    """Una línea por apuesta: `🔴 Rojo · 100 Y$`."""
    return "\n".join(f"{w.bet.name} · {format_amount(w.stake)}" for w in wagers)


def result_text(outcome: RoundOutcome, rng: random.Random | None = None) -> str:
    """Bloque grande con el número y lo ganado o perdido.

    Con una sola apuesta es una frase; con varias, además, una línea por
    apuesta marcando cuál ha entrado.
    """
    rng = rng or random.Random()
    lines = [f"# {pretty(outcome.pocket)}"]
    if outcome.won:
        if outcome.max_payout >= 8:
            lines.append(f"## {rng.choice(BIG_WIN_LINES)} +{format_amount(outcome.net)}")
        else:
            lines.append(f"### {rng.choice(WIN_LINES)} +{format_amount(outcome.net)}")
    elif outcome.total_return:
        # Ha acertado algo, pero menos de lo apostado en total.
        lines.append(
            f"### -{format_amount(-outcome.net)} · Recuperas "
            f"{format_amount(outcome.total_return)} de {format_amount(outcome.stake)}"
        )
    elif outcome.net == 0:
        lines.append("### Te quedas igual.")
    else:
        lines.append(f"### -{format_amount(outcome.stake)} · {rng.choice(LOSS_LINES)}")

    if len(outcome.wagers) == 1:
        (wager,) = outcome.wagers
        if outcome.won:
            lines.append(f"{wager.bet.name} paga {wager.bet.payout}:1")
        else:
            lines.append(f"Ibas a {wager.bet.name}")
        return "\n".join(lines)

    for wager, returned in zip(outcome.wagers, outcome.returns, strict=True):
        if returned:
            gain = returned - wager.stake
            lines.append(
                f"✅ {wager.bet.name} · {format_amount(wager.stake)} → +{format_amount(gain)}"
            )
        else:
            lines.append(f"❌ {wager.bet.name} · {format_amount(wager.stake)}")
    return "\n".join(lines)


def table_embed(
    *,
    owner: str,
    balance: int,
    stake: int,
    history: Iterable[int],
    outcome: RoundOutcome | None = None,
    streak: int = 0,
    text: str | None = None,
    multi: bool = False,
    slip: Sequence[Wager] = (),
) -> discord.Embed:
    """Embed de la mesa en reposo: tras abrirla, tras una tirada o al poner fichas.

    Args:
        text: Texto ya calculado del resultado. Se pasa aparte para que no
            cambie la frase aleatoria al tocar la ficha después de una tirada.
        multi: Si la mesa está en modo varias apuestas.
        slip: Fichas puestas y aún no jugadas (modo varias apuestas).
    """
    if outcome is not None:
        description = text or result_text(outcome)
        embed_color = COLOR_WIN if outcome.won else COLOR_LOSS
    elif multi:
        description = "Cada apuesta que pulses pone una ficha. Luego, 🎰 **Girar**."
        embed_color = COLOR_IDLE
    else:
        description = (
            "Pulsa una apuesta y la rueda gira al momento.\n"
            "🎯 **Números** para plenos, caballos, cuadros…\n"
            "🧩 **Varias** para jugar varias apuestas en la misma tirada."
        )
        embed_color = COLOR_IDLE
    if multi and slip:
        description += (
            f"\n\n**🧩 En la mesa** ({format_amount(wagers_total(slip))})\n{wagers_lines(slip)}"
        )
    elif multi and outcome is not None:
        description += "\n\n🧩 Pon fichas para la siguiente tirada o pulsa 🔁 Repetir."
    if balance == 0:
        description += "\n\n**Estás a cero.** `imv` te recarga."
    embed = discord.Embed(title="🎰 Ruleta americana", description=description, color=embed_color)
    embed.add_field(name="Saldo", value=format_amount(balance))
    embed.add_field(name="Ficha", value=format_amount(stake))
    embed.add_field(name="Racha", value=f"🔥 {streak}" if streak >= 2 else "—")
    embed.set_image(url=f"attachment://{PNG_NAME}")
    embed.set_footer(text=f"Mesa de {owner} · {history_line(history)}")
    return embed


def spinning_embed(*, owner: str, wagers: Sequence[Wager], history: Iterable[int]) -> discord.Embed:
    """Embed mientras la bola gira: solo dice a qué se ha apostado."""
    if len(wagers) == 1:
        bets = f"**{wagers[0].bet.name}** · {format_amount(wagers[0].stake)}"
    else:
        bets = f"{wagers_lines(wagers)}\n**Total** · {format_amount(wagers_total(wagers))}"
    embed = discord.Embed(
        title="🎰 Ruleta americana",
        description=f"# 🌀 Girando…\n{bets}",
        color=COLOR_SPIN,
    )
    embed.set_image(url=f"attachment://{GIF_NAME}")
    embed.set_footer(text=f"Mesa de {owner} · {history_line(history)}")
    return embed


def insufficient_text(balance: int, needed: int | None = None) -> str:
    """Aviso cuando lo apostado supera el saldo."""
    if balance == 0:
        return "Estás a cero. Usa `imv` para recargar."
    if needed is not None:
        return f"Necesitas {format_amount(needed)} y tienes {format_amount(balance)}."
    return f"No te llega: tienes {format_amount(balance)}. Baja la ficha o pulsa 💰 All-in."


def casino_channel_error(
    channel_ids: frozenset[int], channel: object, game: str = "El casino"
) -> str | None:
    """Mensaje de error si `channel` no es un canal de casino; `None` si vale.

    Lo comparten todos los juegos: con `CASINO_CHANNEL_IDS` vacío se puede
    jugar en cualquier canal.
    """
    if not channel_ids or getattr(channel, "id", None) in channel_ids:
        return None
    allowed = " ".join(f"<#{cid}>" for cid in sorted(channel_ids))
    return f"{game} se juega en {allowed}."


def parse_command_args(
    amount_text: str | None, bet_text: str | None, balance: int
) -> tuple[int, list[Bet]]:
    """Interpreta `ruleta [cantidad] [apuesta + apuesta + …]`.

    La cantidad es la ficha de cada apuesta. Con `all` y varias apuestas, el
    saldo se reparte a partes iguales. Si el primer argumento no es una
    cantidad pero sí una apuesta (`.ruleta rojo + 17`), se juega con la
    ficha por defecto.

    Returns:
        `(ficha, apuestas)`; la lista está vacía si solo se abre la mesa.

    Raises:
        ValueError: Con un mensaje mostrable si algo no se entiende.
    """
    default_stake = max(1, min(DEFAULT_STAKE, balance))
    if not amount_text:
        return default_stake, parse_bets(bet_text) if bet_text else []
    try:
        stake = parse_amount(amount_text, balance)
    except ValueError:
        whole = f"{amount_text} {bet_text or ''}".strip()
        try:
            return default_stake, parse_bets(whole)
        except ValueError:
            if bet_text:
                raise
            raise ValueError(
                f"No entiendo `{amount_text}`. Ejemplos: `ruleta 500`, `ruleta all rojo`, "
                "`ruleta 50 17 + rojo`."
            ) from None
    bets = parse_bets(bet_text) if bet_text else []
    if len(bets) > 1 and is_all_in(amount_text):
        stake = balance // len(bets)
        if stake == 0:
            raise ValueError(insufficient_text(balance, len(bets)))
    return stake, bets


# -- Mesa ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpinResult:
    """Tirada ya cobrada y pagada, lista para mostrarse."""

    outcome: RoundOutcome
    balance: int
    media: SpinMedia
    #: Línea de IRPF de esta tirada (retención o devolución), si la hay.
    tax_note: str | None = None
    #: IRPF retenido (positivo) o devuelto (negativo) en esta tirada.
    tax_delta: int = 0


EditFn = Callable[..., Awaitable[Any]]


class NumberBetModal(discord.ui.Modal, title="🎯 Apuesta a números"):
    """Formulario para las apuestas interiores (plenos, caballos, cuadros…)."""

    numbers: discord.ui.TextInput = discord.ui.TextInput(
        label="Números",
        placeholder="17 · 0-00 · 17-20 · 13-14-15 · 17-18-20-21",
        max_length=40,
    )

    def __init__(self, table: RouletteTable) -> None:
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida la apuesta y la juega (o la pone en la mesa en modo varias)."""
        try:
            bet = parse_bet(self.numbers.value)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.table.choose(interaction, bet)


class RouletteTable(discord.ui.View):
    """Mesa de ruleta de un jugador: un mensaje con botones.

    Solo su dueño puede pulsarla. Guarda la ficha actual, el modo (rápido o
    varias apuestas), las fichas puestas sin jugar, la última tirada (para
    repetir/doblar) y la racha. No guarda dinero: el saldo se lee y se
    cambia siempre a través de la economía, y las fichas puestas no se
    cobran hasta pulsar Girar.
    """

    def __init__(self, cog: Casino, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=TABLE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.multi = False
        self.slip: tuple[Wager, ...] = ()
        self.last_wagers: tuple[Wager, ...] = ()
        self.last_outcome: RoundOutcome | None = None
        self.last_text: str | None = None
        self.streak = 0
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

    def _bet_button(self, key: str, text: str, row: int, style: discord.ButtonStyle) -> None:
        bet = OUTSIDE_BETS[key] if key in OUTSIDE_BETS else parse_bet(key)

        async def callback(interaction: discord.Interaction) -> None:
            await self.choose(interaction, bet)

        self._add(text, row, callback, style=style, custom_id=f"bet:{bet.key}")

    def _build_buttons(self) -> None:
        gray, red = discord.ButtonStyle.secondary, discord.ButtonStyle.danger
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        self._bet_button("red", "🔴 Rojo", 0, red)
        self._bet_button("black", "⚫ Negro", 0, gray)
        self._bet_button("even", "Par", 0, gray)
        self._bet_button("odd", "Impar", 0, gray)
        self._add("🎯 Números", 0, self._open_numbers, style=blue, custom_id="numbers")
        self._bet_button("low", "1-18", 1, gray)
        self._bet_button("high", "19-36", 1, gray)
        self._bet_button("dozen1", "1-12", 1, gray)
        self._bet_button("dozen2", "13-24", 1, gray)
        self._bet_button("dozen3", "25-36", 1, gray)
        self._bet_button("col1", "1ª col", 2, gray)
        self._bet_button("col2", "2ª col", 2, gray)
        self._bet_button("col3", "3ª col", 2, gray)
        self._bet_button("0", "🟢 0", 2, green)
        self._bet_button("00", "🟢 00", 2, green)
        self._add("½", 3, self._halve, custom_id="half")
        self._add("×2", 3, self._double_stake, custom_id="x2")
        self._add("💰 All-in", 3, self._all_in, custom_id="allin")
        self.repeat_button = self._add(
            "🔁 Repetir", 3, self._repeat, style=blue, custom_id="repeat"
        )
        self.double_button = self._add(
            "⏫ Doblar", 3, self._double_and_repeat, style=blue, custom_id="double"
        )
        self.mode_button = self._add("🧩 Varias", 4, self._toggle_mode, custom_id="mode")
        self.spin_button = self._add("🎰 Girar", 4, self._spin_slip, style=green, custom_id="spin")
        self.clear_button = self._add("🗑️ Quitar fichas", 4, self._clear_slip, custom_id="clear")
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        """Activa o desactiva los botones según el estado de la mesa."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = not enabled
        self.mode_button.label = "🧩 Varias: sí" if self.multi else "🧩 Varias"
        self.mode_button.style = (
            discord.ButtonStyle.success if self.multi else discord.ButtonStyle.secondary
        )
        if not enabled:
            return
        if not self.last_wagers:
            self.repeat_button.disabled = True
            self.double_button.disabled = True
        self.spin_button.disabled = not (self.multi and self.slip)
        self.clear_button.disabled = not self.slip

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa; al resto se le invita a abrir la suya."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta mesa es de {self.owner.display_name}. Abre la tuya con `ruleta`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Desactiva los botones cuando la mesa lleva un rato sin usarse."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        # El token de la última interacción que editó la mesa vale 15 min y la
        # mesa caduca a los 3 sin uso: sirve aunque se abriera con `/` hace rato.
        # Si no hubo ninguna, la mesa es reciente y vale su propio mensaje.
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa de ruleta", exc_info=True)

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño de la mesa."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(self, balance: int | None = None) -> discord.Embed:
        """Embed de reposo con el saldo actual."""
        if balance is None:
            balance = await self.balance()
        return table_embed(
            owner=self.owner.display_name,
            balance=balance,
            stake=self.stake,
            history=self.cog.history(self.guild_id),
            outcome=self.last_outcome,
            streak=self.streak,
            text=self.last_text,
            multi=self.multi,
            slip=self.slip,
        )

    async def choose(self, interaction: discord.Interaction, bet: Bet) -> None:
        """Respuesta a pulsar una apuesta: girar ya o poner la ficha, según el modo."""
        if not self.multi:
            await self.play(interaction, (Wager(bet, self.stake),))
            return
        if self._busy:
            await interaction.response.defer()
            return
        balance = await self.balance()
        needed = wagers_total(self.slip) + self.stake
        if needed > balance:
            await interaction.response.send_message(
                insufficient_text(balance, needed), ephemeral=True
            )
            return
        try:
            self.slip = add_wager(self.slip, bet, self.stake)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self._refresh(interaction, balance)

    async def play(self, interaction: discord.Interaction, wagers: Sequence[Wager]) -> None:
        """Cobra, gira y paga las apuestas, editando la mesa."""
        if self._busy:
            # Doble clic mientras gira: se ignora sin mostrar error.
            await interaction.response.defer()
            return
        self._busy = True
        try:
            try:
                result = await self.cog.spin(self.guild_id, self.owner.id, wagers)
            except InsufficientFundsError as error:
                await interaction.response.send_message(
                    insufficient_text(error.balance, wagers_total(wagers)), ephemeral=True
                )
                return
            except BalanceLimitError:
                await interaction.response.send_message(
                    "La banca no puede pagar tanto. Baja la ficha.", ephemeral=True
                )
                return
            self._last_interaction = interaction
            await self.show_spin(
                result,
                first_edit=interaction.response.edit_message,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)

    async def show_spin(
        self, result: SpinResult, *, first_edit: EditFn, final_edit: EditFn
    ) -> None:
        """Muestra el GIF del giro y, al acabar, el resultado con su PNG final.

        El dinero ya está cobrado y pagado cuando se llama: si Discord falla
        al editar, el saldo sigue siendo correcto.
        """
        outcome = result.outcome
        self.last_wagers = outcome.wagers
        self.slip = ()
        self._set_enabled(False)
        await first_edit(
            embed=spinning_embed(
                owner=self.owner.display_name,
                wagers=outcome.wagers,
                history=self.cog.history(self.guild_id),
            ),
            attachments=[discord.File(io.BytesIO(result.media.gif), filename=GIF_NAME)],
            view=self,
        )
        await asyncio.sleep(SPIN_SECONDS + REVEAL_MARGIN_SECONDS)

        self.cog.record(self.guild_id, outcome.pocket)
        previous_pocket = self.last_outcome.pocket if self.last_outcome is not None else None
        self.streak = self.streak + 1 if outcome.won else 0
        self.last_outcome = outcome
        self.last_text = result_text(outcome)
        if result.tax_note:
            self.last_text += f"\n{result.tax_note}"
        if renta_hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id):
            self.last_text += f"\n{renta_hint}"
        self._set_enabled(True)
        await final_edit(
            embed=await self.current_embed(result.balance),
            attachments=[discord.File(io.BytesIO(result.media.png), filename=PNG_NAME)],
            view=self,
        )
        # Después de enseñar el número: un aviso de logro antes destriparía la tirada.
        delta = roulette_stats(outcome, table_streak=self.streak, previous_pocket=previous_pocket)
        delta.merge(
            casino_stats(
                stake=outcome.stake,
                net=outcome.net,
                balance_after=result.balance,
                tax_delta=result.tax_delta,
            )
        )
        await logros.casino_play(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            delta,
            net=outcome.net,
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=outcome.stake,
            net=outcome.net,
            balance_after=result.balance,
            tax=result.tax_delta,
        )

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        """Actualiza la mesa (ficha, modo, fichas puestas) sin tocar la imagen."""
        self._set_enabled(True)
        await interaction.response.edit_message(embed=await self.current_embed(balance), view=self)
        self._last_interaction = interaction

    async def _open_numbers(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NumberBetModal(self))

    def _free_balance(self, balance: int) -> int:
        """Saldo que queda sin comprometer por las fichas ya puestas."""
        return max(0, balance - wagers_total(self.slip))

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        balance = await self.balance()
        # Si el doble no cabe, se queda en lo que queda libre: es lo que se busca.
        self.stake = max(1, min(self.stake * 2, self._free_balance(balance)))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        balance = await self.balance()
        free = self._free_balance(balance)
        if free == 0:
            await interaction.response.send_message(insufficient_text(free), ephemeral=True)
            return
        # En modo varias, all-in es "todo lo que queda" para la siguiente ficha.
        self.stake = free
        await self._refresh(interaction, balance)

    async def _repeat(self, interaction: discord.Interaction) -> None:
        if self.last_wagers:
            await self.play(interaction, self.last_wagers)

    async def _double_and_repeat(self, interaction: discord.Interaction) -> None:
        if not self.last_wagers:
            return
        doubled = tuple(Wager(w.bet, w.stake * 2) for w in self.last_wagers)
        balance = await self.balance()
        if wagers_total(doubled) > balance:
            await interaction.response.send_message(
                "No te llega para doblar. " + insufficient_text(balance, wagers_total(doubled)),
                ephemeral=True,
            )
            return
        self.stake *= 2
        await self.play(interaction, doubled)

    async def _toggle_mode(self, interaction: discord.Interaction) -> None:
        self.multi = not self.multi
        self.slip = ()
        await self._refresh(interaction)

    async def _spin_slip(self, interaction: discord.Interaction) -> None:
        if self.slip:
            await self.play(interaction, self.slip)

    async def _clear_slip(self, interaction: discord.Interaction) -> None:
        self.slip = ()
        await self._refresh(interaction)


# -- Cog ----------------------------------------------------------------------------


class Casino(commands.Cog):
    """Comandos del casino y de la economía de yapdollars."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: WheelRenderer | None = None,
        wheel: Wheel | None = None,
        casino_channel_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.renderer = renderer or WheelRenderer()
        self.wheel = wheel or Wheel()
        self.casino_channel_ids = casino_channel_ids
        # Una cola corta por servidor: el tamaño total está acotado por el
        # número de servidores del bot.
        self._history: dict[int, deque[int]] = {}
        self._warm_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        """Prepara en segundo plano las 38 animaciones (~5 s de CPU una vez).

        Así ninguna tirada espera a que se dibuje su GIF.
        """
        self._warm_task = asyncio.create_task(self._warm_up(), name="ruleta-warm-up")

    async def cog_unload(self) -> None:
        """Cancela el precalculado si aún no ha terminado."""
        if self._warm_task is not None:
            self._warm_task.cancel()

    async def _warm_up(self) -> None:
        # Una casilla por llamada al hilo: así cancelar la tarea (al apagar el
        # bot) para el trabajo en ~0,1 s en vez de esperar a las 38.
        try:
            await asyncio.to_thread(self.renderer.idle_png)
            for pocket in POCKETS:
                await asyncio.to_thread(self.renderer.media, pocket)
            logger.info("Animaciones de la ruleta listas.")
        except asyncio.CancelledError:
            raise
        except Exception:
            # No es grave: cada animación se dibujará al usarse por primera vez.
            logger.exception("No se pudieron precalcular las animaciones de la ruleta")

    # -- Estado compartido ----------------------------------------------------------

    def history(self, guild_id: int) -> list[int]:
        """Últimos números del servidor, el más reciente primero."""
        return list(self._history.get(guild_id, ()))

    def record(self, guild_id: int, pocket: int) -> None:
        """Añade un número al historial del servidor."""
        self._history.setdefault(guild_id, deque(maxlen=HISTORY_SIZE)).appendleft(pocket)

    async def spin(self, guild_id: int, user_id: int, wagers: Sequence[Wager]) -> SpinResult:
        """Juega una tirada: decide el número y mueve el dinero de forma atómica.

        Todas las apuestas se cobran y se pagan en una sola operación. El
        número se decide antes de cobrar, pero solo se muestra si el cobro
        sale bien; así no se puede "ver" el resultado sin pagarlo.

        Raises:
            InsufficientFundsError: Si el saldo no cubre el total apostado.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        outcome = play_round(self.wheel, wagers)
        settlement = await self.economy.settle_bet(
            guild_id, user_id, game=GAME, stake=outcome.stake, payout=outcome.total_return
        )
        media = await asyncio.to_thread(self.renderer.media, outcome.pocket)
        return SpinResult(
            outcome=outcome,
            balance=settlement.balance,
            media=media,
            tax_note=gambling_tax_line(settlement),
            tax_delta=settlement.tax_delta,
        )

    def _casino_channel_error(self, channel: object) -> str | None:
        return casino_channel_error(self.casino_channel_ids, channel, "La ruleta")

    # -- ruleta ---------------------------------------------------------------------

    async def _ruleta_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        bet_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/ruleta` y `.ruleta`.

        Args:
            send: Envía el mensaje de la mesa y devuelve el mensaje creado.
            send_error: Responde con un error (efímero cuando es posible).
        """
        if guild is None:
            await send_error("La ruleta solo se juega dentro de un servidor.")
            return
        if (error := self._casino_channel_error(channel)) is not None:
            await send_error(error)
            return

        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake, bets = parse_command_args(amount_text, bet_text, balance)
        except ValueError as error:
            await send_error(str(error))
            return
        needed = stake * max(1, len(bets))
        if needed > balance:
            await send_error(insufficient_text(balance, needed if len(bets) > 1 else None))
            return

        table = RouletteTable(self, guild_id=guild.id, owner=user, stake=stake)
        if not bets:
            png = await asyncio.to_thread(self.renderer.idle_png)
            table.message = await send(
                embed=await table.current_embed(balance),
                file=discord.File(io.BytesIO(png), filename=PNG_NAME),
                view=table,
            )
            return

        # Apuestas escritas en el propio comando: la mesa nace ya girando.
        # Con varias, la mesa queda en modo varias para seguir igual.
        table.multi = len(bets) > 1
        wagers = tuple(Wager(bet, stake) for bet in bets)
        try:
            result = await self.spin(guild.id, user.id, wagers)
        except InsufficientFundsError as error:
            await send_error(insufficient_text(error.balance, wagers_total(wagers)))
            return
        except BalanceLimitError:
            await send_error("La banca no puede pagar tanto. Baja la ficha.")
            return

        async def first_edit(**kwargs: Any) -> None:
            attachments = kwargs.pop("attachments")
            table.message = await send(files=attachments, **kwargs)

        async def final_edit(**kwargs: Any) -> None:
            assert table.message is not None
            await table.message.edit(**kwargs)

        await table.show_spin(result, first_edit=first_edit, final_edit=final_edit)

    @app_commands.command(name="ruleta", description="Ruleta americana con tus yapdollars.")
    @app_commands.describe(
        cantidad="Ficha por apuesta: 500, 2k, all… (por defecto 100)",
        apuesta="Opcional, gira ya: rojo, 17, d2, 17-20… Varias con +: rojo + 17",
    )
    @app_commands.guild_only()
    async def ruleta(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        apuesta: str | None = None,
    ) -> None:
        """Abre una mesa de ruleta con botones (o gira directamente con `apuesta`).

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado.
        Mueve yapdollars del usuario a través de la economía del bot.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._ruleta_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            bet_text=apuesta,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="ruleta")
    @commands.guild_only()
    async def ruleta_text(
        self, ctx: commands.Context, cantidad: str | None = None, *, apuesta: str | None = None
    ) -> None:
        """Versión de texto: `.ruleta`, `.ruleta 500`, `.ruleta all rojo`, `.ruleta 50 17 + rojo`.

        Varias apuestas se separan con `+`; la cantidad es la ficha de cada una.
        """

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._ruleta_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            bet_text=apuesta,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )

    # -- saldo ----------------------------------------------------------------------

    async def _saldo_impl(
        self, responder: CommandResponder, user: discord.abc.User, target: discord.abc.User | None
    ) -> None:
        if responder.guild is None:
            await responder.send_error("La economía solo funciona dentro de un servidor.")
            return
        who = target or user
        if who.bot:
            await responder.send_error("Los bots no tienen monedero.")
            return
        balance = await self.economy.balance(responder.guild.id, who.id)
        description = f"{CURRENCY_EMOJI} **{who.display_name}** tiene **{format_amount(balance)}**"
        # El Patrimonio se cobra el lunes sobre el saldo de ese momento: avisarlo
        # aquí es lo que empuja a gastar antes.
        if wealth := wealth_tax(balance):
            description += (
                f"\n-# 🐶 Si el lunes sigue ahí, {TAX_COLLECTOR} se lleva "
                f"{format_amount(wealth)} de Patrimonio (lo que pase de "
                f"{format_amount(WEALTH_MINIMUM)})."
            )
        # La cuenta remunerada: enseñar lo de ayer y lo que va saliendo hoy es lo que
        # hace que se note y que dé pena fundírselo todo.
        preview = await self.economy.interest_preview(responder.guild.id, who.id)
        if preview.yesterday is not None:
            gross, tax = preview.yesterday
            description += (
                f"\n-# 🏦 Ayer cobró {format_amount(gross - tax)} de intereses "
                f"({TAX_COLLECTOR} se llevó {format_amount(tax)})."
            )
        if preview.gross:
            top = (
                f" Le faltan {format_amount(preview.to_top)} de media para el máximo."
                if preview.to_top
                else " Ya cobra el máximo: lo que pase de ahí, ni un Y$."
            )
            description += (
                f"\n-# 🏦 Saldo medio de hoy: {format_amount(preview.average)}. Si no lo toca, "
                f"mañana cobra {format_amount(preview.net)} netos.{top}"
            )
        embed = discord.Embed(description=description, color=COLOR_WIN)
        await responder.send(embed=embed)

    @app_commands.command(name="saldo", description=f"Muestra tus {CURRENCY_NAME} o los de otro.")
    @app_commands.describe(miembro="De quién ver el saldo (por defecto, tú)")
    @app_commands.guild_only()
    async def saldo(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Muestra el saldo de yapdollars; abre el monedero si es la primera vez."""
        await self._saldo_impl(InteractionResponder(interaction), interaction.user, miembro)

    @commands.command(name="saldo")
    @commands.guild_only()
    async def saldo_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.saldo [@miembro]`) de `/saldo`."""
        await self._saldo_impl(ContextResponder(ctx), ctx.author, miembro)

    # -- IMV (recompensa diaria) ----------------------------------------------------

    async def _daily_impl(self, responder: CommandResponder, user: discord.abc.User) -> None:
        """Cobra el IMV, lo enseña y lo cuenta para los logros de Economía."""
        if responder.guild is None:
            await responder.send_error("La economía solo funciona dentro de un servidor.")
            return
        result = await self.economy.claim_daily(responder.guild.id, user.id)
        next_at = f"<t:{int(result.next_claim_at)}:R>"
        if result.abroad:
            await responder.send_error(
                "🇭🇰 Vives en Hong Kong, mi amor: el IMV es para quien reside en España "
                "(arts. 10 y 36.e de la Ley 19/2021). Vuelve con `pala` y hablamos."
            )
            await logros.track(
                self.bot,
                responder.guild.id,
                user,
                responder.channel,
                StatDelta(add={"imv_abroad": 1}),
            )
            return
        if result.suspended_until:
            await responder.send_error(
                f"🕵️ La Inspección de Trabajo te pilló cobrando en negro y te ha suspendido "
                f"el IMV. Vuelve {next_at}, mi amor, y esta vez declara."
            )
            return
        if not result.claimed:
            await responder.send_error(f"Ya cobraste el IMV hoy. Vuelve {next_at}.")
            return
        streak = (
            f"🔥 Racha de {result.streak} días" if result.streak >= 2 else "Primer día de racha"
        )
        work = ""
        if result.reduction:
            work = (
                f"\n🪏 Esta semana has cobrado {format_amount(result.work_net)} netos "
                f"trabajando: te quitan {format_amount(result.reduction)} de los "
                f"{format_amount(result.full_amount)} que tocaban. Los primeros "
                f"{format_amount(IMV_WORK_EXEMPT)} a la semana no cuentan y del resto, "
                "solo la mitad (RD 789/2022)."
            )
        elif result.work_net:
            work = "\n🪏 Trabajas y cobras el IMV entero: aún no pasas del mínimo exento."
        embed = discord.Embed(
            description=(
                f"# {CURRENCY_EMOJI} +{format_amount(result.amount)}\n"
                f"{streak} · Saldo: **{format_amount(result.balance)}**{work}\n"
                f"Vuelve {next_at} y cobras {format_amount(daily_amount(result.streak + 1))}"
                f"{' (menos lo que trabajes)' if result.work_net else ''}. "
                f"Si pasan más de 48 h, la racha se pierde.\n"
                f"-# 🐶 {TAX_COLLECTOR} no puede tocarlo: el IMV está exento de IRPF "
                "(art. 7.y LIRPF)."
            ),
            color=COLOR_WIN,
        )
        embed.set_author(name=f"IMV de {user.display_name}", icon_url=user.display_avatar.url)
        await responder.send(embed=embed)
        delta = StatDelta(
            add={"imv_claims": 1},
            peak={"imv_streak_max": result.streak, "balance_max": result.balance},
        )
        if result.work_net:
            delta.add["imv_with_salary"] = 1
        if result.reduction and result.amount <= round(result.full_amount * IMV_FLOOR_SHARE):
            delta.add["imv_floor"] = 1
        await logros.track(self.bot, responder.guild.id, user, responder.channel, delta)

    @app_commands.command(
        name="imv", description=f"Cobra tu Ingreso Mínimo Vital diario en {CURRENCY_NAME}."
    )
    @app_commands.guild_only()
    async def imv(self, interaction: discord.Interaction) -> None:
        """Cobra el IMV; cada día seguido paga más (hasta un tope). Exento de IRPF."""
        await self._daily_impl(InteractionResponder(interaction), interaction.user)

    @commands.command(name="imv")
    @commands.guild_only()
    async def imv_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.imv`) de `/imv`."""
        await self._daily_impl(ContextResponder(ctx), ctx.author)

    # -- Hacienda ------------------------------------------------------------------

    async def _hacienda_impl(
        self, responder: CommandResponder, member: discord.abc.User | None = None
    ) -> None:
        """Sin miembro: la cuenta del Estado y lo que paga cada uno. Con miembro: su factura."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("La economía solo funciona dentro de un servidor.")
            return
        now = datetime.now(TIMEZONE)
        year_start = datetime(now.year, 1, 1, tzinfo=TIMEZONE).timestamp()
        all_bills = tax_bills(await self.economy.tax_breakdown(guild.id))
        server = server_bill(all_bills)
        names = {bill.user_id: self._display_name(guild, bill.user_id) for bill in all_bills}
        if member is None:
            treasury = await self.economy.treasury(guild.id, since=year_start)
            embed = treasury_embed(replace(treasury, top_contributors=()), year=now.year, names={})
            # Las cifras primero; la explicación de qué se cobra, al final.
            explained = [(f.name, f.value) for f in embed.fields]
            embed.clear_fields()
            add_bill_fields(embed, all_bills, server, names)
            for name, value in explained:
                embed.add_field(name=name, value=value, inline=False)
        else:
            year_bills = tax_bills(await self.economy.tax_breakdown(guild.id, since=year_start))
            embed = member_bill_embed(
                member_name=discord.utils.escape_markdown(member.display_name),
                all_bills=all_bills,
                year_bills=year_bills,
                server=server,
                user_id=member.id,
                year=now.year,
            )
        await responder.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        author = getattr(responder, "member", None)
        if author is not None and not author.bot:
            mine = next((b for b in all_bills if b.user_id == author.id), None)
            share = (mine.total / server.total) if mine and server.total > 0 else 0.0
            await logros.track(
                self.bot,
                guild.id,
                author,
                responder.channel,
                hacienda_stats(
                    own=member is not None and member.id == author.id,
                    snooping=member is not None and member.id != author.id,
                    share=share,
                    indirect_over_direct=bool(mine and mine.indirect > mine.direct > 0),
                ),
            )

    @staticmethod
    def _display_name(guild: discord.Guild, user_id: int) -> str:
        found = guild.get_member(user_id)
        if found is None:
            return f"<@{user_id}>"
        return discord.utils.escape_markdown(found.display_name)

    @app_commands.command(
        name="hacienda",
        description="Cuánto ha recaudado el Estado y todo lo que paga cada uno (o tu factura).",
    )
    @app_commands.describe(miembro="De quién ver la factura fiscal completa (opcional).")
    @app_commands.guild_only()
    async def hacienda(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """La cuenta del Estado con lo que paga cada miembro, o la factura de `miembro`."""
        await self._hacienda_impl(InteractionResponder(interaction), miembro)

    @commands.command(name="hacienda")
    @commands.guild_only()
    async def hacienda_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.hacienda [miembro]`) de `/hacienda`."""
        await self._hacienda_impl(ContextResponder(ctx), miembro)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra la economía del servidor cuando el bot deja de pertenecer a él."""
        await self.economy.delete_guild_data(guild.id)
        self._history.pop(guild.id, None)
        logger.info("Se eliminó la economía del servidor %s", guild.id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Casino(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
