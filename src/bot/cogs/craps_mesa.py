"""Mesa compartida de los dados: `dados mesa`, una por canal, con tirador y tiradas comunes.

`/dados mesa:Compartida` o `.dados mesa` abre la mesa del canal (o la baja al
final del canal si ya estaba abierta más arriba). Es el craps de siempre
(`bot.services.craps`), pero todos los del canal juegan las mismas tiradas:

1. **Salida.** Cualquiera apuesta a ✅ Pase o 🚫 No pase con su ficha (🪙 Ficha la
   cambia, ½ y ×2 también; 🔁 Repetir pone lo de la ronda anterior y te marca
   listo). Con la primera apuesta empieza la cuenta atrás (`BET_SECONDS`); si
   todos los que han apostado están listos, se tira ya.
2. **El tirador** es el primero que apostó en la mesa. Tira él con 🎲 Tirar (solo
   él) o, si no lo hace a tiempo, el bot tira solo al acabar la cuenta atrás. Con
   un siete fuera pasa al siguiente que está apostando, por orden de llegada. Los
   puntos de su mano son los de la mano caliente de los logros.
3. **Con el punto puesto**, cada jugador con apuesta puede poner ➕ Odds (hasta
   ×`ODDS_MAX` su apuesta) entre tiradas; Pase y No pase no se retiran. Quien llega
   con el punto puesto espera a la siguiente salida. Se tira con 🎲 Tirar, cuando
   todos están listos o al acabar `POINT_SECONDS`.

**Pintar antes de apostar.** Una tirada no depende de las apuestas: los dados de
la próxima se sortean al abrir cada ronda y tirada (sin enseñarlos) y Node pinta su
GIF en segundo plano. Por eso el dibujo no lleva importes ni fichas: es la línea de
Pase del tirador (`bot.services.craps.table_line`) y lo que gana cada uno va en el
texto. El tapete es el de quien abre la mesa.

**Dinero** (`EconomyService`), por jugador y como en la mesa individual: la apuesta
se cobra al tirar la salida (`place_bet`; quien ya no llega se queda fuera de la
ronda) y las Odds al ponerlas; se paga al decidirse la partida (`pay_winnings`, con
0 si pierde), que es cuando se ajusta el IRPF del día. Tratamiento fiscal: juego,
como el resto del casino (premios como ganancia patrimonial, art. 33.1 LIRPF,
y pérdidas que solo compensan ganancias de juego, art. 33.5.d LIRPF). Sin
comisiones: el margen es el de las reglas. Si la mesa se cierra o el bot se apaga
con el punto puesto, el bot tira solo hasta decidir y paga: nadie pierde el dinero
colgado. Con la mesa en la salida no hay nada cobrado y no hay nada que devolver.

La mesa vive en memoria, una por canal: sin apuestas durante `IDLE_SECONDS` se
cierra. Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar
archivos (y añadir reacciones para confirmar `.dados mesa 500 pase`).
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import random
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import shop as tienda
from bot.cogs.bacara import short_name, tax_summary
from bot.cogs.casino import insufficient_text
from bot.services.achievements import casino_stats, craps_stats, craps_table_stats
from bot.services.craps import (
    BAR,
    CRAPS,
    NATURALS,
    ODDS_MAX,
    Bet,
    CrapsGame,
    Hand,
    Roll,
    Status,
    format_odds,
    milestone,
    next_shooter,
    table_line,
    throw,
)
from bot.services.craps_render import OPENING_REST, DieRest, Media
from bot.services.craps_render import Table as Drawing
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    InsufficientFundsError,
    format_amount,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.utils.interactions import ack, notify

if TYPE_CHECKING:
    from bot.cogs.craps import Craps

logger = logging.getLogger(__name__)

GAME = "dados"
DEFAULT_STAKE = 100
#: Cuenta atrás desde la primera apuesta de la salida. Si todos están listos, se tira antes.
BET_SECONDS = 20
#: Espera entre tiradas con el punto puesto, para poner Odds. También se corta con 🎲 Tirar.
POINT_SECONDS = 15
#: Sin ninguna apuesta durante este tiempo, la mesa se cierra.
IDLE_SECONDS = 5 * 60
#: Margen tras el GIF antes de enseñar el resultado (lo que tarda en llegar y empezar).
REVEAL_MARGIN_SECONDS = 1.0
#: Apuestas y resultados que se listan por nombre; el resto se resume.
LIST_LIMIT = 10
#: Personas cuya mascota puede hablar en el resultado (para no llenar la mesa).
HINTS_LIMIT = 4
#: Fichas y últimas apuestas que se recuerdan; las más viejas se olvidan.
MAX_REMEMBERED = 500
#: Quienes se recuerdan por orden de llegada para repartir el cubilete.
ARRIVAL_LIMIT = 64
GIF_NAME = "dados.gif"
PNG_NAME = "dados.png"
#: Rótulo del panel del dibujo: no depende de quién apueste.
CROWD = ("PASE", "DEL TIRADOR")
NO_MENTIONS = discord.AllowedMentions.none()

COLOR_OPEN = discord.Color.from_rgb(88, 101, 242)
COLOR_POINT = discord.Color.from_rgb(255, 196, 0)
COLOR_WON = discord.Color.from_rgb(60, 200, 110)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)

# Frases de Jovani para la mesa compartida (las de la individual están en `bot.cogs.craps`).
NATURAL_LINES = (
    "Natural en la salida: Pase cobra y No pase se va a quejar a Bruselas.",
    "Siete u once de entrada: los de Pase brindan con ron y los de No pase piden amnistía.",
    "Natural, y la oposición de No pase se queda con el discurso y sin las fichas.",
    "¡Toma mangó! Natural: Pase se lo lleva todo antes del café.",
    "Natural por mayoría absoluta: Pase gobierna sin necesitar a Junts.",
    "Natural: el Falcon despega con los de Pase a bordo y los de No pase, en tierra.",
)
CRAPS_LINES = (
    "Pifia en la salida: No pase cobra y el 12 devuelve la ficha como quien devuelve un favor.",
    "Craps de salida: cae Pase como un Gobierno sin Presupuestos y a No pase le sale la jugada.",
    "Pifia: Pase pierde y No pase brinda con cava catalán, que es de Sant Sadurní.",
    "Salida de pifia: Pase en la UCI y No pase pidiendo la cuenta con cara de póker.",
)
POINT_LINES = (
    "Hay punto: o se repite o llega el siete, que son dos formas de que alguien llore.",
    "Punto puesto: Pase quiere repetirlo y No pase reza por un siete, como en el Gobierno.",
    "El disco se enciende y la mesa se parte en dos como el Congreso.",
    "Se pone el punto. Las Odds pagan lo justo y sin comisión: lo mejor del bar.",
)
POINT_MADE_LINES = (
    "¡Punto hecho! Pase cobra y No pase pide un recuento a la Junta Electoral.",
    "El punto vuelve a salir: Pase celebra y No pase estudia recurrir al Constitucional.",
    "Punto hecho, y el cubilete sigue con su dueño. Cuñado, ¿qué dices ahora?",
    "¡Punto! Los de Pase, como en una verbena: a bailar.",
)
SEVEN_OUT_LINES = (
    "¡Siete fuera! Cobra No pase y el cubilete cambia de manos, como la Moncloa cada cuatro años.",
    "Siete fuera: se acaba la mano, Pase llora y el siguiente tirador ya se frota las manos.",
    "El siete aparece sin avisar, como una inspección de Hacienda: cae Pase.",
    "Siete fuera: Pase a Waterloo y No pase al bar a invitar. Quiero café, pero con hielo.",
)
NOTHING_LINES = (
    "No decide nada: otra tirada y que siga el baile.",
    "Ni punto ni siete: la mesa aguanta la respiración, como en una moción de censura.",
    "Sigue sin decidirse. Si te atreves, ➕ Odds.",
    "Tirada de trámite, como un pleno de diputación: nada.",
)
SHOOTER_LINES = (
    "{who} coge el cubilete. Que Dios y el CIS le acompañen.",
    "El cubilete es de {who}: mano firme y sin Pegasus.",
    "{who} toma los dados, como quien toma posesión: con foto y sin programa.",
    "Turno de {who} con el cubilete. Que la fuerza del tapete le acompañe.",
)
CLOSE_LINES = (
    "Nadie apuesta: el crupier apaga las luces. `dados mesa` la vuelve a abrir.",
    "Mesa vacía, como un pleno en agosto. `dados mesa` la reabre.",
    "Se cierra la mesa por falta de quórum. `dados mesa` convoca otra.",
)


class MesaError(Exception):
    """Una apuesta que no se puede hacer ahora (tirando, sin punto, fuera de ronda…)."""


class Phase(Enum):
    """Momento de la mesa."""

    OPEN = "open"
    ROLLING = "rolling"
    POINT = "point"
    CLOSED = "closed"


def remember(store: dict[Any, Any], key: object, value: object) -> None:
    """Guarda `value` en un diccionario acotado: pasado `MAX_REMEMBERED`, olvida lo más viejo."""
    store.pop(key, None)
    store[key] = value
    while len(store) > MAX_REMEMBERED:
        del store[next(iter(store))]


def _dropped_text(names: list[str]) -> str:
    return f"{', '.join(names)} no llega a la ficha y se queda fuera de la ronda."


def roll_text(roll: Roll) -> str:
    """`**6** (3-3)`: el total y los dados."""
    return f"**{roll.total}** ({roll.dice[0]}-{roll.dice[1]})"


@dataclass(slots=True, eq=False)
class Seat:
    """Lo que alguien lleva apostado en la ronda.

    Attributes:
        bet: Pase o No pase.
        stake: Lo apostado (se cobra al tirar la salida).
        ready: Si ha pulsado 🃏 Listo o 🔁 Repetir.
        game: Su partida, desde que se cobra la salida.
        started_at: Cuándo se cobró (las porras no cuentan las anteriores a su cierre).
    """

    user: discord.abc.User
    bet: Bet
    stake: int
    ready: bool = False
    game: CrapsGame | None = None
    started_at: float = 0.0
    settlement: BetSettlement | None = None
    hint: str | None = None

    @property
    def net(self) -> int:
        """Ganado menos apostado, cuando su partida ha terminado."""
        game = self.game
        return game.net if game is not None and not game.playing else 0

    def bets_text(self) -> str:
        """`✅ Pase 100 + Odds 200`."""
        game = self.game
        text = (
            f"{self.bet.emoji} {self.bet.label} {format_amount(game.stake if game else self.stake)}"
        )
        if game is not None and game.odds:
            text += f" + Odds {format_amount(game.odds)}"
        return text


@dataclass(slots=True)
class RoundInfo:
    """Una ronda decidida: lo que hace falta para pagar su rastro (logros, porras, anuncio)."""

    seats: list[Seat]
    shooter: discord.abc.User | None
    shooter_bet: Bet | None
    hand: Hand
    line: CrapsGame


def seat_line(seat: Seat) -> str:
    """`✅ **Ana** · ✅ Pase 100 → **+98**`."""
    name = short_name(seat.user.display_name)
    if seat.net > 0:
        return f"✅ **{name}** · {seat.bets_text()} → **+{format_amount(seat.net)}**"
    if seat.net < 0:
        return f"❌ **{name}** · {seat.bets_text()} → -{format_amount(-seat.net)}"
    return f"↩️ **{name}** · {seat.bets_text()} → te lo devuelven"


class FichaModal(discord.ui.Modal, title="🪙 Tu ficha"):
    """Pide la cantidad que apuestas en cada ronda de la mesa."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="Cantidad (500, 2k, mitad, all…)", placeholder="100", max_length=12
    )

    def __init__(self, table: CrapsTable) -> None:
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guarda la ficha (leer el saldo va a la base de datos: se acepta antes)."""
        await ack(interaction)
        balance = await self.table.cog.economy.balance(self.table.guild_id, interaction.user.id)
        try:
            stake = parse_amount(str(self.amount.value), balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        await self.table.set_stake(interaction, stake)


class MesaView(discord.ui.View):
    """Botones de la mesa; los pulsa cualquiera. `mode`: `open`, `point` o `closed` (apagados)."""

    def __init__(self, table: CrapsTable, mode: str) -> None:
        super().__init__(timeout=None)
        self.table = table
        point = mode == "point"
        if not point:
            self._add(
                "Pase", "✅", discord.ButtonStyle.success, table.bet_callback(Bet.PASS), 0, "pase"
            )
            self._add(
                "No pase",
                "🚫",
                discord.ButtonStyle.danger,
                table.bet_callback(Bet.DONT),
                0,
                "nopase",
            )
        else:
            self._add("Odds", "➕", discord.ButtonStyle.success, table.odds, 0, "odds")
        self._add("Tirar", "🎲", discord.ButtonStyle.primary, table.shoot, 0, "tirar")
        if point:
            self._add("Listo", "🃏", discord.ButtonStyle.secondary, table.mark_ready, 0, "listo")
        else:
            self._add("Ficha", "🪙", discord.ButtonStyle.secondary, table.ask_stake, 1, "ficha")
            self._add("½", "", discord.ButtonStyle.secondary, table.halve, 1, "half")
            self._add("×2", "", discord.ButtonStyle.secondary, table.double, 1, "x2")
            self._add("Repetir", "🔁", discord.ButtonStyle.secondary, table.repeat, 1, "repetir")
            self._add("Listo", "🃏", discord.ButtonStyle.secondary, table.mark_ready, 1, "listo")
        # El 🎨: el tapete de cada uno, que se ve en las mesas que abra.
        if (paint := tienda.skin_button(table.cog.bot, GAME, row=1 if point else 2)) is not None:
            self.add_item(paint)
        if mode == "closed":
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

    def _add(
        self,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        row: int,
        custom_id: str,
    ) -> None:
        button: discord.ui.Button = discord.ui.Button(
            label=label,
            emoji=emoji or None,
            style=style,
            row=row,
            custom_id=f"{GAME}:mesa:{custom_id}",
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)


class CrapsTable:
    """La mesa compartida de un canal: sus apuestas, su mensaje y su bucle de tiradas.

    El dinero nunca vive aquí: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Craps,
        *,
        guild_id: int,
        channel: discord.abc.Messageable,
        png: bytes,
        skin: dict[str, object] | None = None,
        skin_label: str | None = None,
    ) -> None:
        self.cog = cog
        #: El tapete de quien abre la mesa (es compartida): vale hasta que se cierra.
        self.skin = skin
        self.skin_label = skin_label
        self.guild_id = guild_id
        self.channel = channel
        #: La imagen de la mesa quieta (el final de la última tirada o la mesa vacía).
        self.png = png
        self.rest: tuple[DieRest, DieRest] = OPENING_REST
        self.message: discord.Message | None = None
        self.phase = Phase.OPEN
        self.seats: dict[int, Seat] = {}
        self.deadline: float | None = None
        self.opened_at = cog.wall_clock()
        #: Línea de Pase del tirador, la mano y el historial de la mano (se dibujan).
        self.line: CrapsGame | None = None
        self.hand = Hand()
        self.history: list[tuple[Roll, Bet]] = []
        self.shooter: discord.abc.User | None = None
        #: Quienes han apostado en la mesa, por orden de llegada.
        self.arrival: list[int] = []
        #: Lo que dice la mesa de la última tirada (frases, resultados, Hacienda).
        self.report: list[str] = []
        #: Dados de la próxima tirada y semilla de su vuelo, sorteados por adelantado.
        self._dice: tuple[int, int] | None = None
        self._seed: int | None = None
        self._plan: tuple[tuple[Any, ...], asyncio.Future[Media | None]] | None = None
        self._painting: asyncio.Task[None] | None = None
        self.task: asyncio.Task[None] | None = None
        self._lock = TimedLock("craps_mesa:CrapsTable._lock")
        self._edit_task: asyncio.Task[None] | None = None
        self._dirty = False
        #: Se activan con la primera apuesta y cuando se debe tirar ya.
        self.first_bet = asyncio.Event()
        self.go = asyncio.Event()
        self.open_view = MesaView(self, "open")
        self.point_view = MesaView(self, "point")
        #: Los mismos botones apagados, mientras vuelan los dados. Uno por mesa:
        #: discord.py guarda cada vista sin tiempo límite hasta que se para.
        self.closed_view = MesaView(self, "closed")
        self.stopping = False
        self.sleeping = False

    # -- Vista -----------------------------------------------------------------------------

    def views(self) -> tuple[MesaView, MesaView, MesaView]:
        """Las tres vistas de la mesa."""
        return self.open_view, self.point_view, self.closed_view

    def view_now(self) -> MesaView:
        """Los botones que toca según la fase."""
        return self.point_view if self.phase is Phase.POINT else self.open_view

    def drawing(self, *, line: CrapsGame | None = None) -> Drawing:
        """Lo que se dibuja: la línea del tirador, su mano y el historial (sin importes)."""
        return Drawing(
            line if line is not None else self.line,
            self.hand,
            1,
            Bet.PASS,
            tuple(self.history),
            crowd=CROWD,
        )

    def shooter_line(self) -> str:
        """Quién lleva los dados."""
        if self.shooter is None:
            return "🎲 Sin tirador: el primero que apueste coge el cubilete."
        hand = ""
        if self.hand.rolls and not self.hand.seven_out:
            points = len(self.hand.points)
            hand = (
                f" · Mano: {self.hand.rolls} tirada{'s' if self.hand.rolls != 1 else ''}, "
                f"{points} punto{'s' if points != 1 else ''}"
            )
        return f"🎲 Tirador: **{short_name(self.shooter.display_name)}**{hand}"

    def bets_block(self) -> str:
        """Lista de apuestas de la ronda en curso, con tope de líneas."""
        seats = list(self.seats.values())
        lines = [
            f"{'✅' if s.ready else '🎟️'} {short_name(s.user.display_name)} · {s.bets_text()}"
            for s in seats[:LIST_LIMIT]
        ]
        if len(seats) > LIST_LIMIT:
            lines.append(f"… y {len(seats) - LIST_LIMIT} más")
        return "\n".join(lines)

    def _footer(self) -> str:
        return (
            f"Pase y No pase pagan 1:1 · Odds hasta ×{ODDS_MAX} sin ventaja para {TAX_COLLECTOR}"
            + (f" · Tapete: {self.skin_label}" if self.skin_label else "")
        )

    def open_embed(self) -> discord.Embed:
        """La salida: lo de la ronda anterior, el tirador y las apuestas de esta."""
        lines = list(self.report)
        if lines:
            lines.append("")
        lines.append(self.shooter_line())
        if self.seats:
            assert self.deadline is not None
            ready = sum(s.ready for s in self.seats.values())
            lines.append(f"### 🎲 Se tira <t:{math.ceil(self.deadline)}:R>")
            lines.append(
                f"✅ Listos {ready}/{len(self.seats)}: si estáis todos, se tira ya. "
                "El tirador lanza con 🎲 Tirar."
            )
        else:
            closes = math.ceil(self.opened_at + IDLE_SECONDS)
            lines.append(
                "Pulsa ✅ Pase o 🚫 No pase para apostar tu ficha. Sin apuestas, la mesa se "
                f"cierra <t:{closes}:R>."
            )
        embed = discord.Embed(
            title="🎲 Dados · mesa compartida",
            description="\n".join(lines)[:4_000],
            color=self._result_color(),
        )
        if self.seats:
            total = sum(s.stake for s in self.seats.values())
            embed.add_field(
                name=f"Apuestas ({len(self.seats)}) · {format_amount(total)}",
                value=self.bets_block()[:1_024],
                inline=False,
            )
        embed.set_image(url=f"attachment://{PNG_NAME}")
        embed.set_footer(text=self._footer())
        return embed

    def point_embed(self) -> discord.Embed:
        """Con el punto puesto: la última tirada, quién juega y la cuenta atrás."""
        line = self.line
        assert line is not None and line.point is not None
        point = line.point
        lines = [f"# 🎯 Punto: {point}", *self.report, ""]
        lines.append(self.shooter_line())
        lines.append(
            f"Pase quiere repetir el **{point}** antes que un 7; No pase, el 7. "
            f"➕ Odds pagan {format_odds(Bet.PASS, point)} a Pase y {format_odds(Bet.DONT, point)} "
            "a No pase."
        )
        if self.deadline is not None:
            ready = sum(s.ready for s in self.seats.values())
            lines.append(f"### 🎲 Se tira <t:{math.ceil(self.deadline)}:R>")
            lines.append(f"✅ Listos {ready}/{len(self.seats)}.")
        embed = discord.Embed(
            title="🎲 Dados · mesa compartida",
            description="\n".join(lines)[:4_000],
            color=COLOR_POINT,
        )
        if self.seats:
            embed.add_field(name=f"Jugadores ({len(self.seats)})",
                            value=self.bets_block()[:1_024], inline=False)  # fmt: skip
        embed.set_image(url=f"attachment://{PNG_NAME}")
        embed.set_footer(text=self._footer())
        return embed

    def rolling_embed(self) -> discord.Embed:
        """Los dados vuelan: sin destripar nada."""
        shooter = short_name(self.shooter.display_name) if self.shooter else "la mesa"
        embed = discord.Embed(
            title="🎲 ¡No va más!",
            description=f"Tira **{shooter}**. {len(self.seats)} en juego.",
            color=COLOR_OPEN,
        )
        embed.set_image(url=f"attachment://{GIF_NAME}")
        return embed

    def _result_color(self) -> discord.Color:
        line = self.line
        if not self.report or line is None or line.playing:
            return COLOR_OPEN
        return COLOR_WON if line.status is Status.WON else COLOR_LOST

    def embed(self) -> discord.Embed:
        """El mensaje de la mesa según la fase."""
        return self.point_embed() if self.phase is Phase.POINT else self.open_embed()

    def table_file(self) -> discord.File:
        """La imagen de la mesa quieta para adjuntarla."""
        return discord.File(io.BytesIO(self.png), filename=PNG_NAME)

    async def edit(self, **kwargs: Any) -> None:
        """Edita el mensaje de la mesa; un fallo de Discord no rompe nada."""
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar la mesa de dados", exc_info=True)

    def edit_soon(self) -> None:
        """Repinta la mesa sin esperar; agrupa los cambios que llegan seguidos.

        Los botones nunca editan el mensaje ellos mismos: si un clic tardío lo
        hiciera mientras vuelan los dados, taparía el GIF. Solo se repinta con la
        mesa en la salida o con el punto puesto.
        """
        self._dirty = True
        if self._edit_task is None or self._edit_task.done():
            self._edit_task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        while self._dirty and self.phase in (Phase.OPEN, Phase.POINT):
            self._dirty = False
            await self.edit(embed=self.embed(), view=self.view_now())

    # -- Apuestas --------------------------------------------------------------------------

    def place(self, user: discord.abc.User, bet: Bet, stake: int) -> Seat:
        """Apunta la apuesta de la salida (se cobra al tirar). Sustituye la que hubiera.

        Raises:
            MesaError: Si los dados ya vuelan o hay un punto puesto.
        """
        if stake < 1:
            raise MesaError("La ficha tiene que ser de al menos 1 Y$.")
        if self.phase is not Phase.OPEN:
            raise MesaError("No se apuesta ahora: espera a la siguiente salida.")
        seat = self.seats.get(user.id)
        if seat is None:
            seat = Seat(user=user, bet=bet, stake=stake)
            self.seats[user.id] = seat
        else:
            seat.bet, seat.stake, seat.ready = bet, stake, False
        if user.id not in self.arrival:
            self.arrival.append(user.id)
            if len(self.arrival) > ARRIVAL_LIMIT:
                gone = next(
                    (u for u in self.arrival if u not in self.seats and not self.is_shooter(u)),
                    None,
                )
                if gone is not None:
                    self.arrival.remove(gone)
        if self.shooter is None:
            self.shooter = user
        if self.deadline is None:
            self.deadline = self.cog.wall_clock() + BET_SECONDS
            self.first_bet.set()
        return seat

    def is_shooter(self, user_id: int) -> bool:
        """Si `user_id` lleva los dados."""
        return self.shooter is not None and self.shooter.id == user_id

    def _ready(self, seat: Seat) -> None:
        """Marca listo a alguien; si ya lo están todos, se tira."""
        seat.ready = True
        if all(s.ready for s in self.seats.values()):
            self.go.set()

    async def _check_balance(self, interaction: discord.Interaction, needed: int) -> bool:
        balance = await self.cog.economy.balance(self.guild_id, interaction.user.id)
        if balance <= 0 or needed > balance:
            await notify(interaction, insufficient_text(balance, needed))
            return False
        return True

    def _closed_text(self) -> str:
        if self.phase is Phase.POINT:
            return "Hay un punto puesto: espera a la siguiente salida para apostar."
        return "¡No va más! Espera a la siguiente salida."

    def bet_callback(self, bet: Bet) -> Callable[[discord.Interaction], Awaitable[None]]:
        """El callback del botón de Pase o No pase: apunta tu ficha."""

        async def callback(interaction: discord.Interaction) -> None:
            if self.phase is not Phase.OPEN:
                await interaction.response.send_message(self._closed_text(), ephemeral=True)
                return
            stake = self.cog.ficha(self.guild_id, interaction.user.id)
            # El saldo va a la base de datos: se acepta el clic antes.
            await ack(interaction)
            if not await self._check_balance(interaction, stake):
                return
            problem = None
            async with self._lock:
                try:
                    self.place(interaction.user, bet, stake)
                except MesaError as error:
                    problem = str(error)
            if problem:
                await notify(interaction, problem)
                return
            self.edit_soon()
            await renta.remind(self.cog.bot, interaction)

        return callback

    async def set_stake(self, interaction: discord.Interaction, stake: int) -> None:
        """Cambia la ficha (y la apuesta de esta ronda, si ya estabas dentro)."""
        stake = max(1, stake)
        self.cog.set_ficha(self.guild_id, interaction.user.id, stake)
        seat = self.seats.get(interaction.user.id)
        if seat is not None and self.phase is Phase.OPEN and seat.stake != stake:
            seat.stake, seat.ready = stake, False
            self.edit_soon()
        await notify(interaction, f"🪙 Tu ficha: **{format_amount(stake)}** por ronda.")

    async def bet_now(self, user: discord.abc.User, bet: Bet, stake: int) -> None:
        """Apunta una apuesta desde un comando (`place` con el candado de la mesa).

        Raises:
            MesaError: Si los dados vuelan o hay un punto puesto.
        """
        async with self._lock:
            self.place(user, bet, stake)

    async def ask_stake(self, interaction: discord.Interaction) -> None:
        """🪙 Ficha: abre el formulario (tiene que ser la primera respuesta)."""
        await interaction.response.send_modal(FichaModal(self))

    async def halve(self, interaction: discord.Interaction) -> None:
        """½: la mitad de la ficha. Solo mira memoria: contesta directamente."""
        current = self.cog.ficha(self.guild_id, interaction.user.id)
        await self.set_stake(interaction, current // 2)

    async def double(self, interaction: discord.Interaction) -> None:
        """×2: el doble de la ficha, hasta donde llegue el saldo."""
        await ack(interaction)
        balance = await self.cog.economy.balance(self.guild_id, interaction.user.id)
        current = self.cog.ficha(self.guild_id, interaction.user.id)
        await self.set_stake(interaction, min(current * 2, max(1, balance)))

    async def repeat(self, interaction: discord.Interaction) -> None:
        """🔁 Repetir: lo mismo que en tu ronda anterior, y listo."""
        if self.phase is not Phase.OPEN:
            await interaction.response.send_message(self._closed_text(), ephemeral=True)
            return
        seat = self.seats.get(interaction.user.id)
        if seat is not None:
            # Ya has apostado en esta ronda: Repetir hace de Listo.
            await interaction.response.defer()
            self._ready(seat)
            self.edit_soon()
            return
        last = self.cog.last_bets.get((self.guild_id, interaction.user.id))
        if not last:
            await interaction.response.send_message(
                "No tienes ronda anterior que repetir: pulsa ✅ Pase o 🚫 No pase.", ephemeral=True
            )
            return
        bet, stake = last
        await ack(interaction)
        if not await self._check_balance(interaction, stake):
            return
        problem = None
        async with self._lock:
            try:
                self._ready(self.place(interaction.user, bet, stake))
            except MesaError as error:
                problem = str(error)
        if problem:
            await notify(interaction, problem)
            return
        self.edit_soon()
        await renta.remind(self.cog.bot, interaction)

    async def mark_ready(self, interaction: discord.Interaction) -> None:
        """🃏 Listo: te marca listo; si lo están todos, se tira.

        Solo mira y cambia memoria, así que contesta directamente.
        """
        seat = self.seats.get(interaction.user.id)
        if self.phase not in (Phase.OPEN, Phase.POINT) or self.go.is_set():
            await interaction.response.send_message("Ya vuelan los dados.", ephemeral=True)
            return
        if seat is None:
            await interaction.response.send_message(
                "Primero apuesta: pulsa ✅ Pase o 🚫 No pase.", ephemeral=True
            )
            return
        await interaction.response.defer()
        self._ready(seat)
        self.edit_soon()

    async def shoot(self, interaction: discord.Interaction) -> None:
        """🎲 Tirar: solo el tirador; tira ya sin esperar a la cuenta atrás.

        Solo mira memoria, así que contesta directamente.
        """
        if self.phase not in (Phase.OPEN, Phase.POINT) or self.go.is_set():
            await interaction.response.send_message("Ya vuelan los dados.", ephemeral=True)
            return
        if self.shooter is None or not self.seats:
            await interaction.response.send_message(
                "Todavía no hay apuestas: el primero que apueste coge el cubilete.",
                ephemeral=True,
            )
            return
        if not self.is_shooter(interaction.user.id):
            await interaction.response.send_message(
                f"Los dados son de {self.shooter.display_name}.", ephemeral=True
            )
            return
        await interaction.response.defer()
        self.go.set()

    async def odds(self, interaction: discord.Interaction) -> None:
        """➕ Odds: cobra y apunta otra apuesta igual (hasta ×`ODDS_MAX`) con el punto puesto."""
        if self.phase is not Phase.POINT:
            await interaction.response.send_message(
                "Las Odds se ponen con el punto puesto.", ephemeral=True
            )
            return
        seat = self.seats.get(interaction.user.id)
        if seat is None or seat.game is None:
            await interaction.response.send_message(
                "No tienes apuesta en esta ronda: espera a la siguiente salida.", ephemeral=True
            )
            return
        await ack(interaction)
        problem = None
        async with self._lock:
            game = seat.game
            amount = 0
            if self.phase is not Phase.POINT or game is None or game.status is not Status.POINT:
                problem = "¡No va más! Vuelan los dados."
            elif (amount := min(game.stake, game.odds_room)) <= 0:
                problem = f"Tus Odds ya están al tope (×{ODDS_MAX})."
            else:
                try:
                    await self.cog.economy.place_bet(
                        self.guild_id, interaction.user.id, game=GAME, stake=amount
                    )
                except InsufficientFundsError as error:
                    problem = insufficient_text(error.balance, amount)
                else:
                    game.add_odds(amount)
        if problem:
            await notify(interaction, problem)
            return
        self.edit_soon()
        await renta.remind(self.cog.bot, interaction)

    # -- Ciclo -----------------------------------------------------------------------------

    async def nap(self, seconds: float, *, wake: asyncio.Event | None = None) -> None:
        """Espera del bucle; el único sitio donde el apagado lo puede cortar.

        Con `wake`, la espera acaba antes si ese evento se activa.
        """
        if self.stopping:
            raise asyncio.CancelledError
        self.sleeping = True
        try:
            if wake is None:
                await self.cog.sleep(seconds)
                return
            waiters = {
                asyncio.ensure_future(self.cog.sleep(seconds)),
                asyncio.ensure_future(wake.wait()),
            }
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
        finally:
            self.sleeping = False

    def open_round(self, *, prepare: bool = True) -> None:
        """Abre la salida de una ronda nueva y, con `prepare`, empieza a pintar la tirada."""
        self.seats = {}
        self.deadline = None
        self.first_bet.clear()
        self.go.clear()
        self.opened_at = self.cog.wall_clock()
        self.phase = Phase.OPEN
        if prepare:
            self.prepare()

    async def run(self) -> None:
        """Tiradas y más tiradas hasta que nadie apuesta en `IDLE_SECONDS`."""
        try:
            while True:
                if self.phase is Phase.OPEN:
                    await self.nap(IDLE_SECONDS, wake=self.first_bet)
                    if not self.seats:
                        await self.close()
                        return
                assert self.deadline is not None
                await self.nap(max(0.0, self.deadline - self.cog.wall_clock()), wake=self.go)
                await self.roll()
        except asyncio.CancelledError:
            if not self.stopping:
                raise
        except Exception:
            logger.exception("La mesa de dados ha fallado; se decide la partida y se paga")
            await self.shutdown()
        finally:
            self.cog.release(self)

    # -- GIF por adelantado ------------------------------------------------------------------

    def _plan_key(self) -> tuple[Any, ...]:
        """Lo que decide el GIF de la próxima tirada: no depende de las apuestas."""
        line = self.line
        if line is not None and line.playing:
            match: tuple[Any, ...] = (id(line), len(line.rolls))
        else:
            match = (None, self.hand.seven_out)
        return (*match, len(self.history), self._dice, self._seed)

    def _predict(self) -> Drawing:
        """La mesa tal como quedará con los dados ya sorteados (sobre copias)."""
        assert self._dice is not None
        line, hand, history = self.line, deepcopy(self.hand), list(self.history)
        if line is None or not line.playing:
            line = table_line()
            if hand.seven_out:
                hand, history = Hand(), []
        else:
            line = deepcopy(line)
        roll = line.roll(self._dice)
        hand.observe(roll)
        history.append((roll, Bet.PASS))
        return Drawing(line, hand, 1, Bet.PASS, tuple(history), crowd=CROWD)

    def prepare(self) -> None:
        """Sortea los dados de la próxima tirada y pinta su GIF en segundo plano.

        Es justo: la tirada no depende de las apuestas y no se enseña ni se guarda
        en ningún sitio visible hasta que se tira. Se llama al abrir la mesa y en
        cuanto sale el GIF de una tirada. Los dibujos van de uno en uno y nunca se
        cancelan a medias: el que aún no ha empezado no se pinta si la mesa cambió.
        """
        if not self.cog.ahead or self.phase is Phase.CLOSED or self.stopping:
            return
        if self._dice is None:
            self._dice = throw(self.cog.rng)
        if self._seed is None:
            self._seed = self.cog.rng.randrange(1, 2**31)
        key = self._plan_key()
        if self._plan is not None and self._plan[0] == key:
            return
        future: asyncio.Future[Media | None] = asyncio.get_running_loop().create_future()
        self._plan = (key, future)
        previous = self._painting
        self._painting = asyncio.create_task(
            self._paint_ahead(key, future, previous), name="dados-mesa-dibujo"
        )

    async def _paint_ahead(
        self,
        key: tuple[Any, ...],
        future: asyncio.Future[Media | None],
        previous: asyncio.Task[None] | None,
    ) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        media: Media | None = None
        if self._plan_key() == key and self.phase is not Phase.CLOSED:
            try:
                media = await self.cog.renderer.throw(self._predict(), seed=key[-1], skin=self.skin)
            except Exception:
                logger.warning("No se pudieron pintar por adelantado los dados", exc_info=True)
        if not future.done():
            future.set_result(media)

    def _take_plan(self) -> asyncio.Future[Media | None] | None:
        """El GIF por adelantado, si vale para la mesa tal como está."""
        plan, self._plan = self._plan, None
        if plan is None or plan[0] != self._plan_key():
            return None
        return plan[1]

    # -- Tirada ----------------------------------------------------------------------------

    def _apply(self, dice: tuple[int, int]) -> Roll:
        """Aplica una tirada a la línea, a la mano y a la partida de cada jugador."""
        line = self.line
        if line is None or not line.playing:
            line = self.line = table_line()
            if self.hand.seven_out:
                self.hand, self.history = Hand(), []
        roll = line.roll(dice)
        self.hand.observe(roll)
        self.history.append((roll, Bet.PASS))
        for seat in self.seats.values():
            if seat.game is not None and seat.game.playing:
                seat.game.roll(dice)
        return roll

    async def _start_round(self) -> list[str]:
        """Cobra la salida a cada jugador y abre su partida. Quien no llega se queda fuera.

        Returns:
            Los nombres de quienes se quedaron fuera.
        """
        dropped: list[str] = []
        charged: list[Seat] = []
        try:
            for user_id, seat in list(self.seats.items()):
                try:
                    await self.cog.economy.place_bet(
                        self.guild_id, user_id, game=GAME, stake=seat.stake
                    )
                except InsufficientFundsError:
                    dropped.append(short_name(seat.user.display_name))
                    del self.seats[user_id]
                    continue
                seat.game = CrapsGame.new(seat.stake, seat.bet)
                seat.started_at = self.cog.wall_clock()
                charged.append(seat)
        except Exception:
            # Un fallo a medias no deja a nadie sin su dinero: se devuelve lo cobrado.
            for seat in charged:
                try:
                    await self.cog.economy.pay_winnings(
                        self.guild_id, seat.user.id, game=GAME, amount=seat.stake
                    )
                except BalanceLimitError:
                    logger.warning("No se pudo devolver una apuesta de la mesa de dados.")
                seat.game = None
            raise
        return dropped

    async def _pay(self, seats: list[Seat]) -> None:
        """Paga a cada uno lo que le devuelve su partida (0 si pierde).

        Un fallo con un asiento (la base de datos ocupada) no deja sin cobrar a los
        demás: se reintenta una vez y, si vuelve a fallar, queda en el log.
        """
        for seat in seats:
            game = seat.game
            assert game is not None
            seat.settlement = None
            for attempt in range(2):
                try:
                    seat.settlement = await self.cog.economy.pay_winnings(
                        self.guild_id, seat.user.id, game=GAME, amount=game.payout
                    )
                    break
                except BalanceLimitError:
                    logger.warning("Premio de los dados por encima del saldo máximo; no se paga.")
                    break
                except Exception:
                    if attempt:
                        logger.exception(
                            "Impago en la mesa de dados: %s Y$ a %s", game.payout, seat.user.id
                        )
                    else:
                        await asyncio.sleep(0.5)

    def _round_info(self) -> RoundInfo:
        assert self.line is not None
        shooter_seat = self.seats.get(self.shooter.id) if self.shooter else None
        return RoundInfo(
            seats=list(self.seats.values()),
            shooter=self.shooter,
            shooter_bet=shooter_seat.bet if shooter_seat else None,
            hand=deepcopy(self.hand),
            line=self.line,
        )

    def roll_lines(self, roll: Roll, line: CrapsGame, info: RoundInfo | None) -> list[str]:
        """Lo que cuenta la mesa de la tirada: frase, resultados de la ronda y mano caliente."""
        head = f"Sale {roll_text(roll)}"
        out: list[str] = []
        if line.playing and roll.come_out:
            out.append(f"{head} y es el punto. {random.choice(POINT_LINES)}")
        elif line.playing:
            out.append(f"{head}. {random.choice(NOTHING_LINES)}")
        else:
            if roll.come_out and roll.total in NATURALS:
                phrase = random.choice(NATURAL_LINES)
            elif roll.come_out and roll.total in CRAPS:
                phrase = random.choice(CRAPS_LINES)
            elif roll.made:
                phrase = random.choice(POINT_MADE_LINES) + (" ¡Por las malas!" if roll.hard else "")
            else:
                phrase = random.choice(SEVEN_OUT_LINES)
            if roll.come_out and roll.total == BAR:
                phrase += " (El 12 empata con No pase.)"
            out.append(f"### {head}")
            out.append(phrase)
        if roll.made and (cheer := milestone(len(self.hand.points))):
            out.append(cheer)
        if info is not None:
            seats = sorted(info.seats, key=lambda s: -s.net)
            for seat in seats[:LIST_LIMIT]:
                out.append(seat_line(seat))
                if seat.hint:
                    out.append(seat.hint)
            if len(seats) > LIST_LIMIT:
                out.append(f"… y {len(seats) - LIST_LIMIT} más")
            if note := tax_summary(seats):
                out.append(note)
        return out

    async def _media(
        self,
        ready: asyncio.Future[Media | None] | None,
        drawing: Drawing,
        seed: int,
    ) -> Media:
        """El GIF de la tirada: el pintado por adelantado o, si no, uno nuevo.

        Mientras se pinta uno nuevo la mesa ya enseña la jugada con los botones
        apagados (`closed_view`), sin delatar nada.
        """

        async def paint() -> Media:
            media = await ready if ready is not None else None
            if media is None:
                media = await self.cog.renderer.throw(drawing, seed=seed, skin=self.skin)
            return media

        if ready is not None and ready.done() and ready.result() is not None:
            return await paint()
        _, media = await asyncio.gather(
            self.edit(embed=self.rolling_embed(), view=self.closed_view), paint()
        )
        return media

    async def roll(self) -> None:
        """Una tirada para toda la mesa: cobra la salida, tira, paga y enseña el resultado."""
        async with self._lock:
            opening = self.phase is Phase.OPEN
            self.phase = Phase.ROLLING
            dropped = await self._start_round() if opening else []
            if not self.seats:
                self.report = [_dropped_text(dropped)] if dropped else []
                self.open_round(prepare=False)
                await self.edit(embed=self.embed(), view=self.open_view)
                return
            ready = self._take_plan()
            dice = self._dice if self._dice is not None else throw(self.cog.rng)
            seed = self._seed if self._seed is not None else self.cog.rng.randrange(1, 2**31)
            self._dice = self._seed = None
            roll = self._apply(dice)
            line = self.line
            assert line is not None
            drawing = self.drawing()
            info: RoundInfo | None = None
            if not line.playing:
                info = self._round_info()
                await self._pay(info.seats)
            self.report = [_dropped_text(dropped)] if dropped else []
        if self._edit_task is not None:
            await asyncio.gather(self._edit_task, return_exceptions=True)
        media = await self._media(ready, drawing, seed)
        self.rest, self.png = media.rest, media.png
        await self.edit(
            embed=self.rolling_embed(),
            view=self.closed_view,
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
        )
        # Los dados siguientes se sortean ya y se pintan mientras se ve esta tirada.
        self.prepare()
        await self.nap(media.seconds + REVEAL_MARGIN_SECONDS)
        if info is not None:
            await self._hints(info)
        extra = self.report
        self.report = [*extra, *self.roll_lines(roll, line, info)]
        previous = self.shooter
        if info is not None:
            self._finish_round(roll, info)
        else:
            self.phase = Phase.POINT
            self.deadline = self.cog.wall_clock() + POINT_SECONDS
            self.go.clear()
            for seat in self.seats.values():
                seat.ready = False
        if self.shooter is not previous and self.shooter is not None:
            who = short_name(self.shooter.display_name)
            self.report.append(f"🎲 {random.choice(SHOOTER_LINES).format(who=who)}")
        await self.edit(
            embed=self.embed(),
            view=self.view_now(),
            attachments=[self.table_file()],
        )
        if info is not None:
            await self._track(info)

    def _finish_round(self, roll: Roll, info: RoundInfo) -> None:
        """Ronda decidida: pasa el cubilete si hubo siete fuera y abre la salida siguiente."""
        for seat in info.seats:
            remember(self.cog.last_bets, (self.guild_id, seat.user.id), (seat.bet, seat.stake))
        if roll.seven_out and self.shooter is not None:
            nxt = next_shooter(self.arrival, self.shooter.id, {s.user.id for s in info.seats})
            if nxt is not None and nxt != self.shooter.id:
                self.shooter = next(s.user for s in info.seats if s.user.id == nxt)
        self.open_round(prepare=False)

    async def _hints(self, info: RoundInfo) -> None:
        """La Renta y las mascotas hablan en el resultado de los primeros de la lista."""
        for seat in sorted(info.seats, key=lambda s: -s.net)[:HINTS_LIMIT]:
            game = seat.game
            assert game is not None
            balance = seat.settlement.balance if seat.settlement else 0
            seat.hint = await renta.hint(
                self.cog.bot,
                self.guild_id,
                seat.user.id,
                bet_moment(stake=game.wagered, net=game.net, balance_after=balance),
            )

    async def _track(self, info: RoundInfo) -> None:
        """Logros, estadísticas del casino y porras de cada jugador, tras enseñar la ronda."""
        when = datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE)
        seats = info.seats
        for seat in seats:
            game = seat.game
            assert game is not None
            shooter = info.shooter is not None and info.shooter.id == seat.user.id
            others = [s for s in seats if s is not seat]
            delta = craps_stats(game, info.hand if shooter else Hand(), when=when)
            delta.merge(
                craps_table_stats(
                    game,
                    players=len(seats),
                    shooter=shooter,
                    shooter_bet=info.shooter_bet,
                    hand_points=len(info.hand.points),
                    others_won=sum(1 for o in others if o.net > 0),
                    others_lost=sum(1 for o in others if o.net < 0),
                )
            )
            settlement = seat.settlement
            balance = (
                settlement.balance
                if settlement
                else await self.cog.economy.balance(self.guild_id, seat.user.id)
            )
            tax = settlement.tax_delta if settlement else 0
            delta.merge(
                casino_stats(stake=game.wagered, net=game.net, balance_after=balance, tax_delta=tax)
            )
            await logros.casino_play(
                self.cog.bot, self.guild_id, seat.user, self.channel, delta, net=game.net
            )
            last = game.last
            assert last is not None
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                seat.user,
                game=GAME,
                stake=game.wagered,
                net=game.net,
                balance_after=balance,
                tax=tax,
                details=(
                    ("made", int(last.made)),
                    ("sevenout", int(last.seven_out)),
                    ("rolls", len(game.rolls)),
                    ("started", int(seat.started_at)),
                ),
            )
        if info.shooter is not None:
            await self.cog.shout(info.line, info.hand, info.shooter, self.channel)

    # -- Cierre ----------------------------------------------------------------------------

    async def settle_now(self) -> RoundInfo | None:
        """Con el punto puesto, tira sin animación hasta decidir, paga y apunta el resultado.

        Returns:
            La ronda decidida, o `None` si no había una partida a medias.
        """
        line = self.line
        if line is None or not line.playing:
            return None
        while line.playing:
            roll = self._apply(throw(self.cog.rng))
        info = self._round_info()
        await self._pay(info.seats)
        last = line.last
        assert last is not None
        self.rest = (
            replace(self.rest[0], value=last.dice[0]),
            replace(self.rest[1], value=last.dice[1]),
        )
        self.report = self.roll_lines(roll, line, info)
        return info

    async def close(self) -> None:
        """Mesa sin apuestas: se cierra y el mensaje pierde los botones."""
        self.phase = Phase.CLOSED
        self.cancel_painting()
        embed = self.open_embed()
        embed.title = "🎲 Dados · mesa cerrada"
        embed.description = ("\n".join(self.report) + "\n\n" + random.choice(CLOSE_LINES)).strip()
        embed.clear_fields()
        embed.set_footer(text=None)
        await self.edit(embed=embed, view=None)
        self._stop_views()

    async def shutdown(self) -> None:
        """Cierre ordenado. Con el punto puesto, el bot decide la partida y paga a todos.

        En la salida no hay nada cobrado y no hay nada que devolver.
        """
        info: RoundInfo | None = None
        async with self._lock:
            try:
                info = await self.settle_now()
            except Exception:
                logger.exception("No se pudo decidir la partida de la mesa de dados al cerrar")
            self.phase = Phase.CLOSED
        self.cancel_painting()
        embed = discord.Embed(
            title="🎲 Dados · mesa cerrada",
            description="\n".join(
                [*self.report, "", "El casino cierra: se ha decidido la partida."]
            )
            if info is not None
            else "El casino cierra. Cuando vuelva, `dados mesa` abre otra.",
            color=COLOR_OPEN,
        )
        kwargs: dict[str, Any] = {"embed": embed, "view": None}
        if info is not None:
            try:
                png = await self.cog.renderer.board(self.drawing(), self.rest, skin=self.skin)
                kwargs["attachments"] = [discord.File(io.BytesIO(png), filename=PNG_NAME)]
                embed.set_image(url=f"attachment://{PNG_NAME}")
            except Exception:
                logger.warning("No se pudo pintar el final de la mesa de dados", exc_info=True)
        await self.edit(**kwargs)
        self._stop_views()
        if info is not None:
            await self._track(info)

    def cancel_painting(self) -> None:
        if self._plan is not None and not self._plan[1].done():
            self._plan[1].set_result(None)
        self._plan = None

    def _stop_views(self) -> None:
        for view in self.views():
            view.stop()

    async def move(self) -> None:
        """Vuelve a publicar la mesa al final del canal y borra la de arriba.

        Raises:
            MesaError: Si vuelan los dados (no se puede mover a mitad de tirada).
        """
        async with self._lock:
            if self.phase not in (Phase.OPEN, Phase.POINT):
                raise MesaError("Vuelan los dados. En cuanto acabe la tirada, la tienes abajo.")
            old = self.message
            self.message = await self.channel.send(  # type: ignore[attr-defined]
                embed=self.embed(),
                view=self.view_now(),
                file=self.table_file(),
                allowed_mentions=NO_MENTIONS,
            )
        if old is not None:
            try:
                await old.delete()
            except discord.HTTPException:
                logger.debug("No se pudo borrar la mesa de dados vieja", exc_info=True)
