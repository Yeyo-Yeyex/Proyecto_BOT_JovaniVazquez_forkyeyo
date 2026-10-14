"""Reglas del 4 en raya (`4enraya`): el tablero, Jovani (la máquina), los retos y la grada.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.enraya`) cobra, paga y
pinta (`bot.services.enraya_scene`); la ficha de cada miembro y sus retos del
día se guardan en `bot.repositories.enraya`.

**El juego.** Tablero de 7 columnas y 6 filas. Las rojas siempre salen; cada
turno se suelta una ficha en una columna que no esté llena y cae hasta abajo.
Gana quien junta cuatro en raya en horizontal, vertical o diagonal; si se
llena el tablero sin cuatro, son tablas.

**Dos maneras de jugar:**

- *Contra Jovani*, sin dinero: cuatro niveles (`Level`) que solo cambian
  cuánto piensa (`Level.depth`) y cuánto se despista (`Level.blunder`). Es un
  juego de habilidad: con cuotas fijas, quien juega bien sacaría dinero sin
  parar, así que contra la máquina no se apuesta. Lo que paga son los retos.
- *Duelo contra otra persona*, con apuesta opcional: los dos ponen lo mismo y
  el ganador se lleva el bote menos la comisión de la casa, la misma de los
  gallos (`gallos.DUEL_CUT`); en tablas cada uno recupera lo suyo. Mientras se
  juegan las primeras fichas (`GRADA_MOVES`), el público apuesta a la porra por
  una de las dos con las reglas de la grada de los gallos (`gallos.pool_payouts`).

**Retos del día y racha** (como el club del póker, `bot.services.poker_club`):
tres retos al día por miembro, uno fácil, uno medio y uno difícil, sorteados
con su servidor, su id y la fecha. Cada día seguido jugando sube un 10 % el
premio, hasta el doble.

**Equilibrio** (Biblia, «Equilibrio: medir en tiradas»). Los tres retos dan
750 Y$ brutos al día (la mitad del IMV máximo) y, con la racha a tope, 1.500
(un IMV), por jugar unas cuantas partidas y ganar alguna difícil: lo mismo que
el club del póker. Es dinero que se crea y paga IRPF como un premio
(`EconomyService.pay_income`); lo compensan, en parte, la comisión de los
duelos y de la grada, que se destruye.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from fractions import Fraction
from functools import cached_property

from bot.services.gallos import Corner, PoolBet, pool_odds, pool_payouts, pool_totals
from bot.services.poker_club import next_streak, streak_multiplier_pct

#: Clave del juego: motivo del libro, `casino_stats.GAMES` y porras.
GAME = "enraya"
COLS = 7
ROWS = 6
CELLS = COLS * ROWS
#: Fichas que hay en el tablero cuando cierra la grada de un duelo.
GRADA_MOVES = 6
#: Puntuación de una victoria en la búsqueda (se le resta la profundidad).
WIN_SCORE = 1_000_000


class Disc(Enum):
    """Color de una ficha. Las rojas siempre salen."""

    RED = 1
    YELLOW = 2

    @property
    def other(self) -> Disc:
        return Disc.YELLOW if self is Disc.RED else Disc.RED

    @property
    def emoji(self) -> str:
        return "🔴" if self is Disc.RED else "🟡"

    @property
    def label(self) -> str:
        return "rojas" if self is Disc.RED else "amarillas"

    @property
    def corner(self) -> Corner:
        """La esquina de la grada de los gallos que hace de este color (`pool_*`)."""
        return Corner.RED if self is Disc.RED else Corner.BLUE


#: Las cuatro direcciones de una raya: `(clave, paso en columnas, paso en filas)`.
DIRECTIONS: tuple[tuple[str, int, int], ...] = (
    ("h", 1, 0),
    ("v", 0, 1),
    ("d", 1, 1),
    ("a", 1, -1),
)
#: Nombre de cada dirección para los textos (diagonal y antidiagonal son «diagonal»).
DIRECTION_NAMES = {"h": "en horizontal", "v": "en vertical", "d": "en diagonal", "a": "en diagonal"}


@dataclass(frozen=True, slots=True)
class Move:
    """Una ficha ya colocada."""

    column: int
    row: int
    disc: Disc


@dataclass(frozen=True, slots=True)
class Line:
    """Una raya ganadora: su dirección y sus casillas `(columna, fila)`, 4 o más."""

    direction: str
    cells: tuple[tuple[int, int], ...]


class ColumnFullError(ValueError):
    """La columna está llena o no existe."""


@dataclass(frozen=True)
class Board:
    """Un tablero inmutable: la lista de jugadas, en orden.

    `play` devuelve uno nuevo. Las columnas y filas cuentan desde 0, la fila 0
    es la de abajo.
    """

    moves: tuple[Move, ...] = ()

    @cached_property
    def grid(self) -> tuple[tuple[Disc | None, ...], ...]:
        """`grid[columna][fila]`: la ficha que hay o `None`."""
        cells: list[list[Disc | None]] = [[None] * ROWS for _ in range(COLS)]
        for move in self.moves:
            cells[move.column][move.row] = move.disc
        return tuple(tuple(column) for column in cells)

    @cached_property
    def heights(self) -> tuple[int, ...]:
        """Fichas de cada columna."""
        counts = [0] * COLS
        for move in self.moves:
            counts[move.column] += 1
        return tuple(counts)

    @property
    def turn(self) -> Disc:
        """A quién le toca."""
        return Disc.RED if len(self.moves) % 2 == 0 else Disc.YELLOW

    @property
    def last(self) -> Move | None:
        return self.moves[-1] if self.moves else None

    def legal(self) -> list[int]:
        """Columnas en las que se puede soltar ficha."""
        return [c for c in range(COLS) if self.heights[c] < ROWS]

    def play(self, column: int) -> Board:
        """El tablero tras soltar la ficha de quien toca en `column`.

        Raises:
            ColumnFullError: Si la columna no existe o está llena.
        """
        if not 0 <= column < COLS or self.heights[column] >= ROWS:
            raise ColumnFullError(f"La columna {column + 1} no admite más fichas.")
        if self.finished:
            raise ColumnFullError("La partida ya ha terminado.")
        return Board((*self.moves, Move(column, self.heights[column], self.turn)))

    def at(self, column: int, row: int) -> Disc | None:
        if 0 <= column < COLS and 0 <= row < ROWS:
            return self.grid[column][row]
        return None

    def lines_through(self, column: int, row: int) -> list[Line]:
        """Las rayas de 4 o más que pasan por una casilla (de la ficha que hay en ella)."""
        disc = self.at(column, row)
        if disc is None:
            return []
        lines = []
        for key, dc, dr in DIRECTIONS:
            cells = [(column, row)]
            for sign in (1, -1):
                c, r = column + sign * dc, row + sign * dr
                while self.at(c, r) is disc:
                    cells.append((c, r))
                    c, r = c + sign * dc, r + sign * dr
            if len(cells) >= 4:
                lines.append(Line(key, tuple(sorted(cells))))
        return lines

    @cached_property
    def winning_lines(self) -> tuple[Line, ...]:
        """Las rayas que hizo la última ficha (vacío si no ganó)."""
        last = self.last
        return tuple(self.lines_through(last.column, last.row)) if last else ()

    @property
    def winner(self) -> Disc | None:
        """Quién ha ganado (la última ficha hizo raya), o `None`."""
        return self.last.disc if self.winning_lines and self.last else None

    @property
    def full(self) -> bool:
        return len(self.moves) == CELLS

    @property
    def finished(self) -> bool:
        """Si alguien ha ganado o el tablero está lleno (tablas)."""
        return self.winner is not None or self.full

    def winning_cells(self) -> list[tuple[int, int]]:
        """Las casillas de las rayas ganadoras, sin repetir."""
        return sorted({cell for line in self.winning_lines for cell in line.cells})

    def threats(self, disc: Disc) -> list[int]:
        """Columnas en las que `disc` ganaría soltando ficha ahora mismo."""
        found = []
        for column in self.legal():
            row = self.heights[column]
            probe = Board((*self.moves, Move(column, row, disc)))
            if probe.lines_through(column, row):
                found.append(column)
        return found

    def count(self, disc: Disc) -> int:
        """Fichas de `disc` en el tablero."""
        return sum(1 for move in self.moves if move.disc is disc)

    # -- Bitboard para Jovani ------------------------------------------------------------

    def bitboards(self) -> tuple[int, int]:
        """`(fichas de quien toca, todas las fichas)` en el formato de `_Search`."""
        mine = full = 0
        turn = self.turn
        for move in self.moves:
            bit = 1 << (move.column * (ROWS + 1) + move.row)
            full |= bit
            if move.disc is turn:
                mine |= bit
        return mine, full


# -- Jovani --------------------------------------------------------------------------------
#
# Búsqueda negamax con poda alfa-beta sobre bitboards (cada columna son 7 bits:
# 6 casillas y uno de tope), la técnica de los solvers de Conecta 4. La
# evaluación al llegar al fondo cuenta las casillas vacías que completarían una
# raya de cada uno (amenazas) y premia el centro. Entre jugadas igual de buenas
# elige al azar, para que no se pueda aprender una partida de memoria.

_H1 = ROWS + 1
_BOTTOM = sum(1 << (c * _H1) for c in range(COLS))
_BOARD = _BOTTOM * ((1 << ROWS) - 1)
_ORDER = (3, 2, 4, 1, 5, 0, 6)
_CENTER = sum(1 << (3 * _H1 + r) for r in range(ROWS))
# Tipos de valor guardado: exacto, cota inferior (hubo poda) o superior (nada mejoró alfa).
_EXACT, _LOWER, _UPPER = 0, 1, 2


def _column_mask(column: int) -> int:
    return ((1 << ROWS) - 1) << (column * _H1)


def _bottom_mask(column: int) -> int:
    return 1 << (column * _H1)


def _top_mask(column: int) -> int:
    return 1 << (ROWS - 1 + column * _H1)


def _winning_spots(position: int, mask: int) -> int:
    """Casillas vacías (alcanzables o no) que completarían una raya de `position`."""
    # Vertical.
    r = (position << 1) & (position << 2) & (position << 3)
    for shift in (_H1, _H1 - 1, _H1 + 1):
        # Horizontal y las dos diagonales: tres a un lado o dos y uno.
        p = (position << shift) & (position << 2 * shift)
        r |= p & (position << 3 * shift)
        r |= p & (position >> shift)
        p = (position >> shift) & (position >> 2 * shift)
        r |= p & (position << shift)
        r |= p & (position >> 3 * shift)
    return r & (_BOARD ^ mask)


def _popcount(value: int) -> int:
    return value.bit_count()


@dataclass(frozen=True, slots=True)
class Level:
    """Un nivel de Jovani.

    Attributes:
        key: Clave estable (estadísticas, logros, retos).
        name: Lo que se enseña.
        emoji: Junto al nombre.
        depth: Jugadas que piensa por delante.
        blunder: Probabilidad de soltar la ficha en una columna al azar.
        order: 0 el más fácil.
        blurb: Lo que dice el desplegable.
    """

    key: str
    name: str
    emoji: str
    depth: int
    blunder: float
    order: int
    blurb: str


LEVELS: tuple[Level, ...] = (
    Level("facil", "Jovani con resaca", "🥴", 2, 0.40, 0,
          "Fácil. Viene de la beernight y ve doble el tablero."),
    Level("normal", "Jovani de domingo", "☕", 4, 0.12, 1,
          "Normal. Ya se ha tomado el café, pero sin pulpa."),
    Level("dificil", "Jovani concentrado", "🧠", 6, 0.03, 2,
          "Difícil. Piensa seis fichas por delante, como Moncloa antes de unas generales."),
    Level("imposible", "Jovani con café de Puerto Rico", "🔥", 9, 0.0, 3,
          "Imposible. No se despista nunca. Ni el CIS acierta tanto."),
)  # fmt: skip
LEVEL_BY_KEY: dict[str, Level] = {level.key: level for level in LEVELS}
DEFAULT_LEVEL = "normal"


class _Search:
    """Una búsqueda de Jovani: negamax con alfa-beta y tabla de transposiciones."""

    def __init__(self, depth: int) -> None:
        self.depth = depth
        #: Posición → (profundidad, tipo de cota, valor) de lo ya buscado.
        self.table: dict[tuple[int, int], tuple[int, int, int]] = {}
        self.nodes = 0

    @staticmethod
    def _evaluate(mine: int, mask: int) -> int:
        theirs = mine ^ mask
        score = 4 * (
            _popcount(_winning_spots(mine, mask)) - _popcount(_winning_spots(theirs, mask))
        )
        return score + _popcount(mine & _CENTER) - _popcount(theirs & _CENTER)

    def negamax(self, mine: int, mask: int, depth: int, alpha: int, beta: int) -> int:
        """Lo bueno que es la posición para quien toca (`mine`), mirando `depth` jugadas."""
        self.nodes += 1
        moves = (mask + _BOTTOM) & _BOARD
        if not moves:
            return 0
        if _winning_spots(mine, mask) & moves:
            return WIN_SCORE - (self.depth - depth)
        if depth == 0:
            return self._evaluate(mine, mask)
        key = (mine, mask)
        cached = self.table.get(key)
        if cached is not None and cached[0] >= depth:
            _, flag, value = cached
            if flag == _EXACT:
                return value
            if flag == _LOWER:
                alpha = max(alpha, value)
            else:
                beta = min(beta, value)
            if alpha >= beta:
                return value
        start_alpha = alpha
        best = -WIN_SCORE * 2
        theirs = mine ^ mask
        # Si el rival amenaza ganar, solo vale taparle (con dos amenazas, pierde igual).
        forced = _winning_spots(theirs, mask) & moves
        for column in _ORDER:
            move = moves & _column_mask(column)
            if not move or (forced and not move & forced):
                continue
            score = -self.negamax(theirs, mask | move, depth - 1, -beta, -alpha)
            best = max(best, score)
            alpha = max(alpha, score)
            if alpha >= beta:
                break
        if best <= start_alpha:
            flag = _UPPER
        elif best >= beta:
            flag = _LOWER
        else:
            flag = _EXACT
        self.table[key] = (depth, flag, best)
        return best

    def scores(self, board: Board) -> dict[int, int]:
        """Puntuación de cada columna legal para quien toca en `board`."""
        mine, mask = board.bitboards()
        theirs = mine ^ mask
        result: dict[int, int] = {}
        for column in board.legal():
            move = ((mask + _bottom_mask(column)) & _column_mask(column)) & ~mask
            if _winning_spots(mine, mask) & move:
                result[column] = WIN_SCORE
                continue
            new_mask = mask | move
            result[column] = -self.negamax(
                theirs, new_mask, self.depth - 1, -WIN_SCORE * 2, WIN_SCORE * 2
            )
        return result


def jovani_move(board: Board, level: Level, rng: random.Random) -> int:
    """La columna que elige Jovani en `board` con su `level`.

    Con probabilidad `level.blunder` suelta en una columna al azar (con más
    peso en el centro, como haría una persona); si no, la mejor según la
    búsqueda y, entre las empatadas, una al azar.

    Raises:
        ColumnFullError: Si la partida ha terminado o no queda sitio.
    """
    legal = board.legal()
    if board.finished or not legal:
        raise ColumnFullError("No queda jugada posible.")
    if rng.random() < level.blunder:
        weights = [4 - abs(3 - column) for column in legal]
        return rng.choices(legal, weights=weights)[0]
    scores = _Search(level.depth).scores(board)
    best = max(scores.values())
    return rng.choice(sorted(c for c, s in scores.items() if s == best))


def plan_seed(turn_seed: int, column: int) -> random.Random:
    """El azar de la respuesta de Jovani si el miembro suelta en `column`.

    Sale de la semilla del turno, así que la respuesta pintada por adelantado y
    la que se calcula al pulsar son la misma.
    """
    return random.Random(turn_seed * 31 + column)


# -- Lo que pasó en una partida (para retos y logros) ---------------------------------------


class Ending(Enum):
    """Cómo acaba una partida para quien la juega."""

    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"
    RESIGN = "resign"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class GameEvent:
    """Una partida terminada, vista por uno de los dos jugadores.

    Attributes:
        ending: Cómo acaba para él (ganar, perder, tablas, rendirse, dormirse).
        disc: Su color.
        level: El nivel de Jovani, o `None` en un duelo.
        board: El tablero final.
        blocks: Rayas del rival que tapó (soltó ficha donde el rival ganaba).
        missed: Veces que pudo ganar en el acto y soltó en otra columna.
        stake: Lo que se jugó (0 contra Jovani o en un duelo amistoso).
    """

    ending: Ending
    disc: Disc
    level: Level | None
    board: Board
    blocks: int = 0
    missed: int = 0
    stake: int = 0

    @property
    def won(self) -> bool:
        return self.ending is Ending.WIN

    @property
    def lost(self) -> bool:
        return self.ending in (Ending.LOSS, Ending.RESIGN, Ending.TIMEOUT)

    @property
    def duel(self) -> bool:
        return self.level is None

    @property
    def my_moves(self) -> int:
        """Fichas que soltó."""
        return self.board.count(self.disc)

    @property
    def directions(self) -> frozenset[str]:
        """Direcciones de sus rayas ganadoras (vacío si no ganó)."""
        if not self.won:
            return frozenset()
        return frozenset(line.direction for line in self.board.winning_lines)

    @property
    def diagonal(self) -> bool:
        return bool(self.directions & {"d", "a"})

    def beat_at_least(self, order: int) -> bool:
        """Si ganó a Jovani en un nivel de al menos `order`."""
        return self.won and self.level is not None and self.level.order >= order


def blocks_and_misses(board: Board, disc: Disc) -> tuple[int, int]:
    """Cuántas rayas del rival tapó `disc` y cuántas victorias en el acto dejó pasar.

    Repasa la partida jugada a jugada: antes de cada ficha de `disc` mira
    dónde ganaba él y dónde ganaba el rival.
    """
    blocks = missed = 0
    position = Board()
    for move in board.moves:
        if move.disc is disc:
            mine = position.threats(disc)
            theirs = position.threats(disc.other)
            if mine and move.column not in mine:
                missed += 1
            if move.column in theirs and move.column not in mine:
                blocks += 1
        position = Board((*position.moves, move))
    return blocks, missed


# -- Retos del día ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Challenge:
    """Un reto del día.

    Attributes:
        key: Clave estable (se guarda con lo que lleva cada uno).
        text: Lo que se enseña, con `{goal}` para la meta.
        goal: Cuántas veces hay que cumplirlo.
        tier: 0 fácil, 1 medio, 2 difícil.
        count: Cuánto suma una partida (0 si no cuenta).
    """

    key: str
    text: str
    goal: int
    tier: int
    count: Callable[[GameEvent], int] = field(compare=False)

    @property
    def reward(self) -> int:
        """Premio bruto al cumplirlo, antes de la racha."""
        return TIER_REWARDS[self.tier]

    @property
    def label(self) -> str:
        """El texto con la meta puesta."""
        return self.text.format(goal=self.goal)


#: Premio bruto de cada dificultad (fácil, medio, difícil): lo mismo que el póker.
TIER_REWARDS = (100, 250, 400)
TIER_NAMES = ("Fácil", "Medio", "Difícil")


def _beat(order: int) -> Callable[[GameEvent], int]:
    return lambda e: int(e.beat_at_least(order))


def _serious_win(e: GameEvent) -> int:
    """Una victoria en duelo o contra Jovani en Normal o más."""
    return int(e.won and (e.duel or e.beat_at_least(1)))


CHALLENGES: tuple[Challenge, ...] = (
    # Fáciles: jugando un rato salen solos.
    Challenge("jugar_3", "Juega {goal} partidas", 3, 0, lambda e: 1),
    Challenge("ganar_2", "Gana {goal} partidas", 2, 0, lambda e: int(e.won)),
    Challenge("normal_1", "Gánale a Jovani en Normal o más", 1, 0, _beat(1)),
    Challenge("tapar_4", "Tapa {goal} rayas del rival", 4, 0, lambda e: e.blocks),
    Challenge("vertical_1", "Gana con una raya en vertical", 1, 0,
              lambda e: int("v" in e.directions)),
    # Medios: piden constancia o algo de cabeza.
    Challenge("serio_4", "Gana {goal} partidas en Normal o más (o en duelo)", 4, 1, _serious_win),
    Challenge("dificil_1", "Gánale a Jovani en Difícil o más", 1, 1, _beat(2)),
    Challenge("diagonal_2", "Gana {goal} veces con una raya en diagonal", 2, 1,
              lambda e: int(e.diagonal)),
    Challenge("rapido_1", "Gana soltando 7 fichas o menos (Normal o más, o en duelo)", 1, 1,
              lambda e: int(_serious_win(e) and e.my_moves <= 7)),
    Challenge("tapar_10", "Tapa {goal} rayas del rival", 10, 1, lambda e: e.blocks),
    Challenge("duelo_1", "Juega un duelo contra otra persona", 1, 1, lambda e: int(e.duel)),
    # Difíciles: un buen rato de tablero o una partida muy buena.
    Challenge("imposible_1", "Gánale a Jovani en Imposible", 1, 2, _beat(3)),
    Challenge("dificil_3", "Gánale {goal} veces a Jovani en Difícil o más", 3, 2, _beat(2)),
    Challenge("serio_8", "Gana {goal} partidas en Normal o más (o en duelo)", 8, 2, _serious_win),
    Challenge("duelo_gana_1", "Gana un duelo contra otra persona", 1, 2,
              lambda e: int(e.duel and e.won)),
    Challenge("diagonal_dificil_1", "Gana con una diagonal a Jovani en Difícil o más", 1, 2,
              lambda e: int(e.beat_at_least(2) and e.diagonal)),
)  # fmt: skip
CHALLENGE_BY_KEY: dict[str, Challenge] = {c.key: c for c in CHALLENGES}


def daily_challenges(guild_id: int, user_id: int, day: date) -> tuple[Challenge, ...]:
    """Los tres retos de hoy de un miembro: uno de cada dificultad.

    Se sortean con una semilla de su servidor, su id y la fecha (y el nombre del
    juego, para que no coincidan con los del póker), así que son los mismos todo
    el día y no hace falta guardarlos.
    """
    digest = hashlib.sha256(f"{GAME}:{guild_id}:{user_id}:{day.isoformat()}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    return tuple(rng.choice([c for c in CHALLENGES if c.tier == tier]) for tier in range(3))


def progress_for(challenges: tuple[Challenge, ...], event: GameEvent) -> dict[str, int]:
    """Lo que suma esta partida a cada reto (sin los que no suma nada)."""
    return {c.key: n for c in challenges if (n := c.count(event))}


def reward_for(challenge: Challenge, streak: int) -> int:
    """El premio bruto de un reto con la racha de hoy."""
    return challenge.reward * (100 + streak_multiplier_pct(streak)) // 100


# -- La ficha de cada miembro --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlayerRecord:
    """El historial de un miembro en el 4 en raya.

    Attributes:
        games: Partidas terminadas (contra Jovani y duelos).
        wins: Ganadas.
        losses: Perdidas (también rendirse o dormirse).
        draws: Tablas.
        streak: Victorias seguidas ahora mismo.
        best_streak: La mejor racha de victorias.
        day_streak: Días seguidos jugando.
        last_day: Último día que jugó.
        best_level: Nivel más alto al que le ha ganado a Jovani (`Level.order`, -1 ninguno).
    """

    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    streak: int = 0
    best_streak: int = 0
    day_streak: int = 0
    last_day: date | None = None
    best_level: int = -1


def after_game(record: PlayerRecord, event: GameEvent, day: date) -> PlayerRecord:
    """La ficha tras una partida."""
    streak = record.streak + 1 if event.won else 0 if event.lost else record.streak
    best_level = record.best_level
    if event.won and event.level is not None:
        best_level = max(best_level, event.level.order)
    return PlayerRecord(
        games=record.games + 1,
        wins=record.wins + int(event.won),
        losses=record.losses + int(event.lost),
        draws=record.draws + int(event.ending is Ending.DRAW),
        streak=streak,
        best_streak=max(record.best_streak, streak),
        day_streak=next_streak(record.day_streak, record.last_day, day),
        last_day=day,
        best_level=best_level,
    )


# -- La grada de los duelos ----------------------------------------------------------------


def grada_open(board: Board) -> bool:
    """Si el público aún puede apostar: hasta que haya `GRADA_MOVES` fichas."""
    return len(board.moves) < GRADA_MOVES and not board.finished


def grada_totals(bets: Mapping[int, PoolBet]) -> dict[Disc, int]:
    """Lo apostado a cada color (las apuestas usan las esquinas de los gallos)."""
    totals = pool_totals(bets)
    return {disc: totals[disc.corner] for disc in Disc}


def grada_odds(bets: Mapping[int, PoolBet]) -> dict[Disc, Fraction | None]:
    """Cuota a la porra de cada color si ganara ahora (`gallos.pool_odds`)."""
    odds = pool_odds(bets)
    return {disc: odds[disc.corner] for disc in Disc}


def grada_payouts(bets: Mapping[int, PoolBet], winner: Disc | None) -> dict[int, int]:
    """Lo que cobra cada apostante (apuesta incluida). En tablas se devuelve todo."""
    if winner is None:
        return {user: bet.stake for user, bet in bets.items()}
    return pool_payouts(bets, winner.corner)
