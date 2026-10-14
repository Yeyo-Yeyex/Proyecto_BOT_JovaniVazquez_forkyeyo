"""Reglas de la timba: Texas Hold'em sin límite entre jugadores. Lógica pura, sin Discord ni dinero.

El cog (`bot.cogs.timba`) lleva la mesa del canal, aparta el dinero de cada uno al
empezar la mano y le devuelve lo que no gastó más lo que gane. Aquí solo se juega:
ciegas, turnos, apuestas, botes secundarios y enfrentamiento.

**Una mano** (`TimbaHand`):

1. El botón va rotando. Pone la ciega pequeña el siguiente al botón y la grande
   el de después; a dos, el botón pone la pequeña y habla primero antes del flop.
2. Se reparten dos cartas a cada uno (`hole`) y cinco comunitarias que se van
   enseñando: flop, turn y river.
3. En cada ronda de apuestas habla cada uno por orden: **pasar** si no hay nada
   que igualar, **igualar**, **subir** (como mínimo lo que subió el último, y
   nunca menos de la ciega grande) o **retirarse**. Se puede ir con todo
   (*all-in*) aunque no llegue a una subida entera; una subida corta no vuelve a
   abrir la ronda a quien ya había hablado (solo puede igualar o retirarse).
4. Si solo queda uno, se lleva el bote sin enseñar. Si los que quedan ya no
   pueden apostar (todos con todo dentro menos uno), se enseñan las comunitarias
   que falten. En el enfrentamiento cada uno juega su mejor mano de cinco
   (`poker.evaluate`) y cada bote se lo llevan los mejores de entre quienes
   pusieron hasta él (`pots`); las fichas que no se puedan partir van al primero
   tras el botón.

**Comisión** (`rake`): de cada bote que se disputa y llega al flop, la casa se
queda el `RAKE_PCT` %, con un tope de `RAKE_CAP_BB` ciegas grandes («sin flop no
hay comisión», como en las salas de verdad). El cog la manda al Estado como
Impuesto sobre Actividades de Juego.

**Las cartas se sortean al empezar** (`deal`): las comunitarias ya están en el
mazo aunque no se vean, igual que en la mesa de verdad.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from bot.services.blackjack import Card
from bot.services.poker import HandValue, evaluate, full_deck

#: Clave del juego en el libro (`timba:apuesta`), en `casino_stats.GAMES` y en las porras.
GAME = "timba"
#: Asientos de la mesa.
MAX_SEATS = 6
#: Lo máximo que uno se juega en una mano, en ciegas grandes.
MAX_BUY_BB = 100
#: Lo mínimo que hay que tener para sentarse, en ciegas grandes.
MIN_BUY_BB = 20
#: Comisión de la casa por bote disputado que llega al flop, y su tope en ciegas grandes.
RAKE_PCT = 5
RAKE_CAP_BB = 3


class Street(Enum):
    """En qué ronda va la mano. El valor es cuántas comunitarias se ven."""

    PREFLOP = 0
    FLOP = 3
    TURN = 4
    RIVER = 5
    SHOWDOWN = 6

    @property
    def label(self) -> str:
        return {0: "Antes del flop", 3: "Flop", 4: "Turn", 5: "River", 6: "Enfrentamiento"}[
            self.value
        ]


class Act(Enum):
    """Lo que puede hacer el que habla."""

    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    RAISE = "raise"

    @property
    def label(self) -> str:
        return {"fold": "se retira", "check": "pasa", "call": "iguala", "raise": "sube"}[self.value]


class TimbaError(Exception):
    """Acción no válida (no es su turno, subida demasiado pequeña…); se puede enseñar."""


@dataclass(slots=True)
class Player:
    """Un jugador en la mano.

    Attributes:
        user_id: Quién es.
        stack: Lo que le queda por apostar en la mano.
        street: Lo que lleva puesto en la ronda en curso.
        total: Lo que lleva puesto en toda la mano.
        folded: Si se ha retirado.
        hole: Sus dos cartas.
    """

    user_id: int
    stack: int
    hole: tuple[Card, Card]
    street: int = 0
    total: int = 0
    folded: bool = False
    timeouts: int = 0

    @property
    def all_in(self) -> bool:
        """Si ya no le queda nada que apostar (y sigue en la mano)."""
        return not self.folded and self.stack == 0

    @property
    def active(self) -> bool:
        """Si todavía puede hablar: ni retirado ni con todo dentro."""
        return not self.folded and self.stack > 0


@dataclass(frozen=True, slots=True)
class Options:
    """Lo que puede hacer el que habla.

    Attributes:
        to_call: Lo que le falta para igualar (0: puede pasar).
        min_raise: A cuánto como mínimo puede subir en la ronda (total suyo en la ronda).
        max_raise: A cuánto como mucho (todo lo que tiene).
        can_raise: Si puede subir (no, si una subida corta no le reabre la ronda o
            no le llega para más que igualar).
    """

    to_call: int
    min_raise: int
    max_raise: int
    can_raise: bool


@dataclass(frozen=True, slots=True)
class Event:
    """Una acción de la mano, para el texto de la mesa y los logros."""

    user_id: int
    act: Act
    amount: int
    street: Street
    all_in: bool = False


@dataclass(frozen=True, slots=True)
class Pot:
    """Un bote (el principal o uno secundario) y quién se lo lleva.

    Attributes:
        amount: Lo que hay, ya sin la comisión.
        eligible: Quiénes pueden ganarlo (pusieron hasta él y no se retiraron).
        winners: Quiénes se lo llevan.
        rake: La comisión que se le quitó.
    """

    amount: int
    eligible: tuple[int, ...]
    winners: tuple[int, ...]
    rake: int = 0


@dataclass(frozen=True, slots=True)
class Result:
    """Cómo acaba la mano.

    Attributes:
        won: Lo que se lleva cada uno de los botes (0 si nada).
        pots: Los botes, del principal a los secundarios.
        showdown: Si hubo enfrentamiento (si no, ganó el último que quedaba).
        values: La mejor mano de cada uno de los que llegaron al enfrentamiento.
        rake: Comisión total.
    """

    won: dict[int, int]
    pots: tuple[Pot, ...]
    showdown: bool
    values: dict[int, HandValue]
    rake: int

    def rake_of(self, user_id: int) -> int:
        """La parte de la comisión que sale de lo que gana `user_id`."""
        share = 0
        for pot in self.pots:
            if pot.rake and user_id in pot.winners:
                share += pot.rake // len(pot.winners)
        return share


def rake_for(pot: int, big_blind: int) -> int:
    """La comisión de un bote disputado que ha llegado al flop."""
    return min(pot * RAKE_PCT // 100, RAKE_CAP_BB * big_blind)


@dataclass(slots=True)
class TimbaHand:
    """Una mano de Texas Hold'em sin límite.

    Se crea con `TimbaHand.deal` (que pone las ciegas) y avanza con `act`.

    Attributes:
        players: Los jugadores, en orden de asiento.
        button: Índice del botón en `players`.
        small_blind, big_blind: Las ciegas.
        board: Las cinco comunitarias (se ven según `street`).
    """

    players: list[Player]
    button: int
    small_blind: int
    big_blind: int
    board: tuple[Card, ...]
    street: Street = Street.PREFLOP
    current_bet: int = 0
    min_raise: int = 0
    to_act: int | None = None
    pending: set[int] = field(default_factory=set)
    no_reraise: set[int] = field(default_factory=set)
    events: list[Event] = field(default_factory=list)
    result: Result | None = None
    saw_flop: bool = False

    # -- Reparto ---------------------------------------------------------------------------

    @classmethod
    def deal(
        cls,
        stacks: Sequence[tuple[int, int]],
        *,
        button: int,
        small_blind: int,
        big_blind: int,
        rng: random.Random,
    ) -> TimbaHand:
        """Reparte una mano, pone las ciegas y deja hablando al primero.

        Args:
            stacks: `(user_id, lo que se juega)` de cada jugador, en orden de asiento.
            button: Índice del botón.

        Raises:
            ValueError: Con menos de dos jugadores, más de `MAX_SEATS`, ciegas
                incoherentes o alguien sin nada que jugarse.
        """
        if not 2 <= len(stacks) <= MAX_SEATS:
            raise ValueError("La timba se juega de dos a seis.")
        if not 0 < small_blind <= big_blind:
            raise ValueError("Ciegas incoherentes.")
        if any(stack <= 0 for _, stack in stacks):
            raise ValueError("Todos tienen que jugarse algo.")
        cards = rng.sample(full_deck(), 2 * len(stacks) + 5)
        players = [
            Player(user_id=uid, stack=stack, hole=(cards[2 * i], cards[2 * i + 1]))
            for i, (uid, stack) in enumerate(stacks)
        ]
        hand = cls(
            players=players,
            button=button % len(players),
            small_blind=small_blind,
            big_blind=big_blind,
            board=tuple(cards[2 * len(stacks) :]),
        )
        hand._post_blinds()
        return hand

    def _next(self, index: int) -> int:
        return (index + 1) % len(self.players)

    def _post(self, index: int, amount: int) -> None:
        player = self.players[index]
        paid = min(amount, player.stack)
        player.stack -= paid
        player.street += paid
        player.total += paid

    def _post_blinds(self) -> None:
        heads_up = len(self.players) == 2
        sb = self.button if heads_up else self._next(self.button)
        bb = self._next(sb)
        self._post(sb, self.small_blind)
        self._post(bb, self.big_blind)
        self.current_bet = self.big_blind
        self.min_raise = self.big_blind
        self.pending = {i for i, p in enumerate(self.players) if p.active}
        self.to_act = self._first_pending(self._next(bb))
        self._settle_if_done()

    # -- Consultas -------------------------------------------------------------------------

    @property
    def finished(self) -> bool:
        """Si la mano ya ha acabado."""
        return self.result is not None

    @property
    def visible_board(self) -> tuple[Card, ...]:
        """Las comunitarias que se ven ya."""
        return self.board[: min(self.street.value, 5)]

    @property
    def pot(self) -> int:
        """Todo lo puesto en la mano."""
        return sum(p.total for p in self.players)

    @property
    def current(self) -> Player | None:
        """El que habla, o `None` si no habla nadie (mano acabada)."""
        return None if self.to_act is None else self.players[self.to_act]

    def index_of(self, user_id: int) -> int:
        """El asiento de `user_id` en la mano.

        Raises:
            TimbaError: Si no juega esta mano.
        """
        for i, player in enumerate(self.players):
            if player.user_id == user_id:
                return i
        raise TimbaError("No juegas esta mano.")

    def options(self) -> Options:
        """Lo que puede hacer el que habla.

        Raises:
            TimbaError: Si la mano ya ha acabado.
        """
        player = self.current
        if player is None or self.to_act is None:
            raise TimbaError("La mano ya ha acabado.")
        to_call = min(self.current_bet - player.street, player.stack)
        max_raise = player.street + player.stack
        min_raise = min(self.current_bet + self.min_raise, max_raise)
        can_raise = (
            self.to_act not in self.no_reraise
            and max_raise > self.current_bet
            and self._others_can_respond()
        )
        return Options(to_call, min_raise, max_raise, can_raise)

    def _others_can_respond(self) -> bool:
        """Si queda alguien más que pueda igualar una subida (si no, subir no tiene sentido)."""
        return any(p.active for i, p in enumerate(self.players) if i != self.to_act)

    # -- Acciones --------------------------------------------------------------------------

    def act(self, user_id: int, act: Act, amount: int = 0) -> Event:
        """`user_id` hace `act`; con `Act.RAISE`, `amount` es a cuánto sube (su total en la ronda).

        Raises:
            TimbaError: Si no es su turno o la acción no vale ahora.
        """
        if self.finished or self.to_act is None:
            raise TimbaError("La mano ya ha acabado.")
        index = self.to_act
        player = self.players[index]
        if player.user_id != user_id:
            raise TimbaError("No es tu turno.")
        opts = self.options()
        if act is Act.CHECK and opts.to_call:
            raise TimbaError("No puedes pasar: hay que igualar o retirarse.")
        if act is Act.CALL and not opts.to_call:
            act = Act.CHECK
        event_amount = 0
        if act is Act.FOLD:
            player.folded = True
        elif act is Act.CALL:
            self._post(index, opts.to_call)
            event_amount = opts.to_call
        elif act is Act.RAISE:
            if not opts.can_raise:
                raise TimbaError("Ahora no puedes subir: solo igualar o retirarte.")
            target = min(amount, opts.max_raise)
            if target < opts.min_raise:
                raise TimbaError(f"La subida mínima es a {opts.min_raise}.")
            raise_by = target - self.current_bet
            self._post(index, target - player.street)
            full = raise_by >= self.min_raise
            already = set(range(len(self.players))) - self.pending - {index}
            if full:
                self.min_raise = raise_by
                self.no_reraise = set()
            else:
                # Subida corta (all-in): quien ya había hablado solo puede igualar.
                self.no_reraise |= already
            self.current_bet = target
            self.pending = {i for i, p in enumerate(self.players) if p.active and i != index}
            event_amount = target
        event = Event(user_id, act, event_amount, self.street, all_in=player.all_in)
        self.events.append(event)
        self.pending.discard(index)
        self._advance(index)
        return event

    def timeout(self) -> Event:
        """El que habla se ha dormido: pasa si puede y, si no, se retira."""
        player = self.current
        if player is None:
            raise TimbaError("La mano ya ha acabado.")
        player.timeouts += 1
        act = Act.CHECK if not self.options().to_call else Act.FOLD
        return self.act(player.user_id, act)

    # -- Avance ----------------------------------------------------------------------------

    def _first_pending(self, start: int) -> int | None:
        index = start
        for _ in range(len(self.players)):
            if index in self.pending and self.players[index].active:
                return index
            index = self._next(index)
        return None

    def _advance(self, index: int) -> None:
        self.to_act = self._first_pending(self._next(index))
        self._settle_if_done()

    def _settle_if_done(self) -> None:
        """Pasa de ronda, enseña lo que falte o acaba la mano, según quién quede."""
        alive = [p for p in self.players if not p.folded]
        if len(alive) == 1:
            self._finish(showdown=False)
            return
        if self.to_act is not None:
            return
        # La ronda está cerrada. Si ya no puede apostar más que uno, se enseña todo.
        if sum(p.active for p in alive) <= 1 or self.street is Street.RIVER:
            self.street = Street.RIVER
            self.saw_flop = True
            self._finish(showdown=True)
            return
        self.street = {
            Street.PREFLOP: Street.FLOP,
            Street.FLOP: Street.TURN,
            Street.TURN: Street.RIVER,
        }[self.street]
        self.saw_flop = True
        for p in self.players:
            p.street = 0
        self.current_bet = 0
        self.min_raise = self.big_blind
        self.no_reraise = set()
        self.pending = {i for i, p in enumerate(self.players) if p.active}
        self.to_act = self._first_pending(self._next(self.button))
        if self.to_act is None:
            self._settle_if_done()

    def _finish(self, *, showdown: bool) -> None:
        self.to_act = None
        self.pending = set()
        values = {p.user_id: evaluate((*p.hole, *self.board)) for p in self.players if not p.folded}
        pots = build_pots(self.players)
        won: dict[int, int] = {p.user_id: 0 for p in self.players}
        settled: list[Pot] = []
        order = [self.players[(self.button + 1 + k) % len(self.players)].user_id
                 for k in range(len(self.players))]  # fmt: skip
        total_rake = 0
        for amount, eligible in pots:
            if len(eligible) == 1:
                winners: tuple[int, ...] = eligible
            else:
                best = max(values[uid] for uid in eligible)
                winners = tuple(uid for uid in order if uid in eligible and values[uid] == best)
            rake = rake_for(amount, self.big_blind) if len(eligible) > 1 and self.saw_flop else 0
            total_rake += rake
            net = amount - rake
            share, odd = divmod(net, len(winners))
            for k, uid in enumerate(winners):
                won[uid] += share + (1 if k < odd else 0)
            settled.append(Pot(net, eligible, winners, rake))
        if showdown:
            self.street = Street.SHOWDOWN
        self.result = Result(
            won=won,
            pots=tuple(settled),
            showdown=showdown,
            values=values if showdown else {},
            rake=total_rake,
        )


def build_pots(players: Sequence[Player]) -> list[tuple[int, tuple[int, ...]]]:
    """Los botes por niveles de lo puesto: `(cantidad, quiénes pueden ganarlo)`.

    Cada nivel de lo que puso alguien que sigue en la mano abre un bote nuevo; lo
    de los retirados entra en los botes hasta donde llegó, pero no pueden ganarlos.
    Un bote con un solo aspirante es lo que nadie igualó: se le devuelve.
    """
    levels = sorted({p.total for p in players if not p.folded and p.total > 0})
    pots: list[tuple[int, tuple[int, ...]]] = []
    floor = 0
    for level in levels:
        amount = sum(max(0, min(p.total, level) - floor) for p in players)
        eligible = tuple(p.user_id for p in players if not p.folded and p.total >= level)
        if amount:
            pots.append((amount, eligible))
        floor = level
    # Lo que pusieron los retirados por encima del último nivel de los que siguen.
    leftover = sum(max(0, p.total - floor) for p in players)
    if leftover and pots:
        amount, eligible = pots[-1]
        pots[-1] = (amount + leftover, eligible)
    return pots
