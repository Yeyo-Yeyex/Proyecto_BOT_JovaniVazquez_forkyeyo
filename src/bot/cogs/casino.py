"""Casino y economía: `ruleta`, `saldo` y `daily`.

Todo el dinero se mueve con `EconomyService` (`bot.economy`), que es la
misma economía que usará cualquier juego o sistema futuro. Este cog solo
traduce botones y comandos a llamadas al servicio y pinta el resultado.

La ruleta es americana (0 y 00), individual e instantánea: cada jugador abre
su propia mesa, un mensaje con botones que solo él puede pulsar. Cada clic
en una apuesta cobra, gira y paga en el acto. Si `CASINO_CHANNEL_IDS` está
configurado, la ruleta solo se abre en esos canales.

Permisos que necesita el bot en el canal: enviar mensajes, insertar enlaces
(embeds) y adjuntar archivos (el GIF de la rueda).
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.economy import (
    CURRENCY_EMOJI,
    CURRENCY_NAME,
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    daily_amount,
    format_amount,
    parse_amount,
)
from bot.services.roulette import (
    COLOR_EMOJI,
    OUTSIDE_BETS,
    POCKETS,
    Bet,
    SpinOutcome,
    Wheel,
    color,
    label,
    parse_bet,
    play,
    pretty,
)
from bot.services.roulette_render import SPIN_SECONDS, SpinMedia, WheelRenderer
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


def result_text(outcome: SpinOutcome, rng: random.Random | None = None) -> str:
    """Bloque grande con el número y lo ganado o perdido."""
    rng = rng or random.Random()
    lines = [f"# {pretty(outcome.pocket)}"]
    if outcome.won:
        if outcome.bet.payout >= 8:
            lines.append(f"## {rng.choice(BIG_WIN_LINES)} +{format_amount(outcome.net)}")
        else:
            lines.append(f"### {rng.choice(WIN_LINES)} +{format_amount(outcome.net)}")
        lines.append(f"{outcome.bet.name} paga {outcome.bet.payout}:1")
    else:
        lines.append(f"### -{format_amount(outcome.stake)} · {rng.choice(LOSS_LINES)}")
        lines.append(f"Ibas a {outcome.bet.name}")
    return "\n".join(lines)


def table_embed(
    *,
    owner: str,
    balance: int,
    stake: int,
    history: Iterable[int],
    outcome: SpinOutcome | None = None,
    streak: int = 0,
    text: str | None = None,
) -> discord.Embed:
    """Embed de la mesa en reposo: tras abrirla o tras una tirada.

    Args:
        text: Texto ya calculado del resultado. Se pasa aparte para que no
            cambie la frase aleatoria al tocar la ficha después de una tirada.
    """
    if outcome is None:
        description = (
            "Pulsa una apuesta y la rueda gira al momento.\n"
            "🎯 **Números** para plenos, caballos, cuadros…"
        )
        embed_color = COLOR_IDLE
    else:
        description = text or result_text(outcome)
        embed_color = COLOR_WIN if outcome.won else COLOR_LOSS
    if balance == 0:
        description += "\n\n**Estás a cero.** `daily` te recarga."
    embed = discord.Embed(title="🎰 Ruleta americana", description=description, color=embed_color)
    embed.add_field(name="Saldo", value=format_amount(balance))
    embed.add_field(name="Ficha", value=format_amount(stake))
    embed.add_field(name="Racha", value=f"🔥 {streak}" if streak >= 2 else "—")
    embed.set_image(url=f"attachment://{PNG_NAME}")
    embed.set_footer(text=f"Mesa de {owner} · {history_line(history)}")
    return embed


def spinning_embed(*, owner: str, bet: Bet, stake: int, history: Iterable[int]) -> discord.Embed:
    """Embed mientras la bola gira: solo dice a qué se ha apostado."""
    embed = discord.Embed(
        title="🎰 Ruleta americana",
        description=f"# 🌀 Girando…\n**{bet.name}** · {format_amount(stake)}",
        color=COLOR_SPIN,
    )
    embed.set_image(url=f"attachment://{GIF_NAME}")
    embed.set_footer(text=f"Mesa de {owner} · {history_line(history)}")
    return embed


def insufficient_text(balance: int) -> str:
    """Aviso cuando la ficha supera el saldo."""
    if balance == 0:
        return "Estás a cero. Usa `daily` para recargar."
    return f"No te llega: tienes {format_amount(balance)}. Baja la ficha o pulsa 💰 All-in."


def parse_command_args(
    amount_text: str | None, bet_text: str | None, balance: int
) -> tuple[int, Bet | None]:
    """Interpreta `ruleta [cantidad] [apuesta]`.

    Si el primer argumento no es una cantidad pero sí una apuesta
    (`.ruleta rojo`), se juega con la ficha por defecto.

    Raises:
        ValueError: Con un mensaje mostrable si algo no se entiende.
    """
    default_stake = max(1, min(DEFAULT_STAKE, balance))
    if not amount_text:
        return default_stake, parse_bet(bet_text) if bet_text else None
    try:
        stake = parse_amount(amount_text, balance)
    except ValueError:
        if bet_text:
            raise
        try:
            return default_stake, parse_bet(amount_text)
        except ValueError:
            raise ValueError(
                f"No entiendo `{amount_text}`. Ejemplos: `ruleta 500`, `ruleta all rojo`, "
                "`ruleta 50 17`."
            ) from None
    return stake, parse_bet(bet_text) if bet_text else None


# -- Mesa ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpinResult:
    """Tirada ya cobrada y pagada, lista para mostrarse."""

    outcome: SpinOutcome
    balance: int
    media: SpinMedia


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
        """Valida la apuesta y, si es legal, gira con ella."""
        try:
            bet = parse_bet(self.numbers.value)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.table.play(interaction, bet)


class RouletteTable(discord.ui.View):
    """Mesa de ruleta de un jugador: un mensaje con botones.

    Solo su dueño puede pulsarla. Guarda la ficha actual, la última apuesta
    (para repetir/doblar) y la racha de aciertos. No guarda dinero: el saldo
    se lee y se cambia siempre a través de la economía.
    """

    def __init__(self, cog: Casino, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=TABLE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.last_bet: Bet | None = None
        self.last_outcome: SpinOutcome | None = None
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
            await self.play(interaction, bet)

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
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = not enabled
        if enabled and self.last_bet is None:
            self.repeat_button.disabled = True
            self.double_button.disabled = True

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

    async def current_embed(self) -> discord.Embed:
        """Embed de reposo con el saldo actual."""
        balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        return table_embed(
            owner=self.owner.display_name,
            balance=balance,
            stake=self.stake,
            history=self.cog.history(self.guild_id),
            outcome=self.last_outcome,
            streak=self.streak,
            text=self.last_text,
        )

    async def play(self, interaction: discord.Interaction, bet: Bet) -> None:
        """Cobra, gira y paga `bet` con la ficha actual, editando la mesa."""
        if self._busy:
            # Doble clic mientras gira: se ignora sin mostrar error.
            await interaction.response.defer()
            return
        self._busy = True
        try:
            try:
                result = await self.cog.spin(self.guild_id, self.owner.id, bet, self.stake)
            except InsufficientFundsError as error:
                await interaction.response.send_message(
                    insufficient_text(error.balance), ephemeral=True
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

    async def show_spin(
        self, result: SpinResult, *, first_edit: EditFn, final_edit: EditFn
    ) -> None:
        """Muestra el GIF del giro y, al acabar, el resultado con su PNG final.

        El dinero ya está cobrado y pagado cuando se llama: si Discord falla
        al editar, el saldo sigue siendo correcto.
        """
        outcome = result.outcome
        self.last_bet = outcome.bet
        self._set_enabled(False)
        history = list(self.cog.history(self.guild_id))
        await first_edit(
            embed=spinning_embed(
                owner=self.owner.display_name,
                bet=outcome.bet,
                stake=outcome.stake,
                history=history,
            ),
            attachments=[discord.File(io.BytesIO(result.media.gif), filename=GIF_NAME)],
            view=self,
        )
        await asyncio.sleep(SPIN_SECONDS + REVEAL_MARGIN_SECONDS)

        self.cog.record(self.guild_id, outcome.pocket)
        self.streak = self.streak + 1 if outcome.won else 0
        self.last_outcome = outcome
        self.last_text = result_text(outcome)
        self._set_enabled(True)
        await final_edit(
            embed=table_embed(
                owner=self.owner.display_name,
                balance=result.balance,
                stake=self.stake,
                history=self.cog.history(self.guild_id),
                outcome=outcome,
                streak=self.streak,
                text=self.last_text,
            ),
            attachments=[discord.File(io.BytesIO(result.media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """Actualiza la mesa tras cambiar la ficha, sin tocar la imagen."""
        await interaction.response.edit_message(embed=await self.current_embed(), view=self)
        self._last_interaction = interaction

    async def _open_numbers(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NumberBetModal(self))

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        # Si el doble no cabe, se queda en todo el saldo: es lo que se busca.
        self.stake = max(1, min(self.stake * 2, balance))
        await self._refresh(interaction)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        if balance == 0:
            await interaction.response.send_message(insufficient_text(0), ephemeral=True)
            return
        self.stake = balance
        await self._refresh(interaction)

    async def _repeat(self, interaction: discord.Interaction) -> None:
        if self.last_bet is not None:
            await self.play(interaction, self.last_bet)

    async def _double_and_repeat(self, interaction: discord.Interaction) -> None:
        if self.last_bet is None:
            return
        balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        if self.stake * 2 > balance:
            await interaction.response.send_message(
                f"Para doblar necesitas {format_amount(self.stake * 2)} y tienes "
                f"{format_amount(balance)}.",
                ephemeral=True,
            )
            return
        self.stake *= 2
        await self.play(interaction, self.last_bet)


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

    async def spin(self, guild_id: int, user_id: int, bet: Bet, stake: int) -> SpinResult:
        """Juega una tirada: decide el número y mueve el dinero de forma atómica.

        El número se decide antes de cobrar, pero solo se muestra si el cobro
        sale bien; así no se puede "ver" el resultado sin pagarlo.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la ficha.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        outcome = play(self.wheel, bet, stake)
        balance = await self.economy.settle_bet(
            guild_id, user_id, game=GAME, stake=stake, payout=outcome.total_return
        )
        media = await asyncio.to_thread(self.renderer.media, outcome.pocket)
        return SpinResult(outcome=outcome, balance=balance, media=media)

    def _casino_channel_error(self, channel: object) -> str | None:
        if not self.casino_channel_ids:
            return None
        channel_id = getattr(channel, "id", None)
        if channel_id in self.casino_channel_ids:
            return None
        allowed = " ".join(f"<#{cid}>" for cid in sorted(self.casino_channel_ids))
        return f"La ruleta se juega en {allowed}."

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
            stake, bet = parse_command_args(amount_text, bet_text, balance)
        except ValueError as error:
            await send_error(str(error))
            return
        if stake > balance:
            await send_error(insufficient_text(balance))
            return

        table = RouletteTable(self, guild_id=guild.id, owner=user, stake=stake)
        if bet is None:
            png = await asyncio.to_thread(self.renderer.idle_png)
            table.message = await send(
                embed=await table.current_embed(),
                file=discord.File(io.BytesIO(png), filename=PNG_NAME),
                view=table,
            )
            return

        # Apuesta escrita en el propio comando: la mesa nace ya girando.
        try:
            result = await self.spin(guild.id, user.id, bet, stake)
        except InsufficientFundsError as error:
            await send_error(insufficient_text(error.balance))
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
        cantidad="Ficha: 500, 2k, all… (por defecto 100)",
        apuesta="Opcional, gira ya: rojo, par, 1-18, d2, c3, 17, 17-20…",
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

    @commands.command(name="ruleta")
    @commands.guild_only()
    async def ruleta_text(
        self, ctx: commands.Context, cantidad: str | None = None, *, apuesta: str | None = None
    ) -> None:
        """Versión de texto: `.ruleta`, `.ruleta 500`, `.ruleta all rojo`, `.ruleta 50 17-20`."""

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
        embed = discord.Embed(
            description=(
                f"{CURRENCY_EMOJI} **{who.display_name}** tiene **{format_amount(balance)}**"
            ),
            color=COLOR_WIN,
        )
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

    # -- daily ----------------------------------------------------------------------

    async def _daily_impl(self, responder: CommandResponder, user: discord.abc.User) -> None:
        if responder.guild is None:
            await responder.send_error("La economía solo funciona dentro de un servidor.")
            return
        result = await self.economy.claim_daily(responder.guild.id, user.id)
        next_at = f"<t:{int(result.next_claim_at)}:R>"
        if not result.claimed:
            await responder.send_error(f"Ya cobraste hoy. Vuelve {next_at}.")
            return
        streak = (
            f"🔥 Racha de {result.streak} días" if result.streak >= 2 else "Primer día de racha"
        )
        embed = discord.Embed(
            description=(
                f"# {CURRENCY_EMOJI} +{format_amount(result.amount)}\n"
                f"{streak} · Saldo: **{format_amount(result.balance)}**\n"
                f"Vuelve {next_at} y cobras {format_amount(daily_amount(result.streak + 1))}. "
                f"Si pasan más de 48 h, la racha se pierde."
            ),
            color=COLOR_WIN,
        )
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        await responder.send(embed=embed)

    @app_commands.command(name="daily", description=f"Cobra tus {CURRENCY_NAME} diarios.")
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction) -> None:
        """Cobra la recompensa diaria; cada día seguido paga más (hasta un tope)."""
        await self._daily_impl(InteractionResponder(interaction), interaction.user)

    @commands.command(name="daily")
    @commands.guild_only()
    async def daily_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.daily`) de `/daily`."""
        await self._daily_impl(ContextResponder(ctx), ctx.author)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra la economía del servidor cuando el bot deja de pertenecer a él."""
        await self.economy.delete_guild_data(guild.id)
        self._history.pop(guild.id, None)
        logger.info("Se eliminó la economía del servidor %s", guild.id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Casino(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
