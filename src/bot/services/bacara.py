"""Reglas del bacará (Punto y Banca): zapato, manos, tercera carta, pagos y carretera.

Es el bacará de casino de toda la vida, el que en España se llama «Punto y
Banca». Nadie decide nada después de apostar: las cartas salen por una regla
fija, así que una mano se puede repartir (y dibujar) antes de que nadie apueste
sin cambiar ninguna probabilidad (ver `bot.cogs.bacara`).

**Valores.** Del as al 9 valen lo que dicen; el 10 y las figuras, 0. Una mano
vale la suma de sus cartas módulo 10: 7 + 8 = 15 vale 5. Gana la que más se
acerca a 9.

**Reparto.** Jugador, Banca, Jugador, Banca, todas boca arriba. Con 8 o 9 en las
dos primeras de cualquiera de los dos («natural») no se pide más. Si no:

- El Jugador pide tercera carta con 0-5 y se planta con 6-7.
- Si el Jugador se plantó, la Banca pide con 0-5, igual que él.
- Si el Jugador pidió, la Banca decide mirando su total y la tercera carta del
  Jugador (`banker_draws`): con 0-2 pide siempre, con 3 pide salvo que la carta
  sea un 8, con 4 si es de 2 a 7, con 5 si es de 4 a 7, con 6 si es 6 o 7 y con
  7 se planta.

**Pagos** (`payout`, lo devuelto con la apuesta incluida):

- Jugador: 1 a 1.
- Banca: 0,95 a 1. El 5 % es la comisión de la casa porque, por la regla de la
  tercera carta, la Banca gana algo más a menudo (45,9 % frente a 44,6 %). La
  comisión se redondea al Y$ más cercano (los medios, para la casa) y la Banca
  pide `BANKER_MIN_STAKE` (20 Y$), con lo que el 5 % siempre es al menos 1 Y$.
  Con la comisión redondeada a la baja y fichas de 1 Y$, la Banca cobraría 1 en
  vez de 1,95 y su ventaja pasaría del 1 % al 45 %.
- Empate: 8 a 1. Con empate, lo apostado a Jugador o a Banca se devuelve.

Ventaja de la casa con 8 barajas: 1,06 % en Banca, 1,24 % en Jugador y 14,36 %
en Empate (`docs/rtp_bacara.py` la calcula con estas mismas funciones).

**Zapato** (`Shoe`): 8 barajas, compartido por toda la mesa del canal. Al
abrirlo se quema la primera carta y tantas más como su valor (los 10 y las
figuras queman 10), como en un casino. La carta de corte va a `CUT_FROM_END`
cartas del final: cuando sale, esa es la última mano del zapato. El zapato da
para unas 80 manos.

**Carretera grande** (`big_road`): la cuadrícula que tienen todas las mesas de
bacará. Cada racha de Banca o de Jugador baja por una columna; al cambiar de
lado se empieza columna nueva. Los empates no ocupan casilla: se apuntan en la
del resultado anterior. Si una racha llega al fondo (6 filas), sigue hacia la
derecha por la última fila libre: la «cola del dragón». No sirve para predecir
nada (cada mano es independiente), y por eso mismo engancha.
"""

from __future__ import annotations

import random
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from bot.services.blackjack import Card

#: Clave del juego en el libro (`bacara:apuesta`), en `GAMES` y en las porras.
GAME = "bacara"
DECKS = 8
#: La carta de corte va a esta distancia del final del zapato.
CUT_FROM_END = 16
#: Comisión de la Banca, en tanto por cien de lo ganado.
COMMISSION_PERCENT = 5
#: Lo mínimo que se puede poner a Banca: con menos, el 5 % no llega a 1 Y$.
BANKER_MIN_STAKE = 20
#: El empate paga 8 a 1.
TIE_PAYS = 8
#: Filas de la carretera grande.
ROAD_ROWS = 6


class Side(Enum):
    """A qué se apuesta (y quién gana una mano)."""

    PLAYER = ("jugador", "Jugador", "🔵", "J")
    TIE = ("empate", "Empate", "🟢", "E")
    BANKER = ("banca", "Banca", "🔴", "B")

    def __init__(self, key: str, label: str, emoji: str, letter: str) -> None:
        self.key = key
        self.label = label
        self.emoji = emoji
        #: Letra de la carretera y de los textos compactos.
        self.letter = letter

    @property
    def pays(self) -> str:
        """Cómo paga, para los botones y la ayuda."""
        return {"jugador": "1:1", "empate": "8:1", "banca": "0,95:1"}[self.key]


SIDES: dict[str, Side] = {side.key: side for side in Side}


def card_points(card: Card) -> int:
    """Puntos de una carta en el bacará: as 1, del 2 al 9 su número, 10 y figuras 0."""
    return card.rank if card.rank < 10 else 0


def hand_points(cards: Sequence[Card]) -> int:
    """Puntos de una mano: la suma módulo 10."""
    return sum(card_points(c) for c in cards) % 10


def player_draws(total: int) -> bool:
    """Si el Jugador pide tercera carta con ese total en dos cartas (sin naturales)."""
    return total <= 5


def banker_draws(total: int, player_third: Card | None) -> bool:
    """Si la Banca pide tercera carta (sin naturales).

    Args:
        total: Puntos de la Banca con sus dos cartas.
        player_third: La tercera carta del Jugador, o `None` si se plantó.
    """
    if player_third is None:
        return total <= 5
    third = card_points(player_third)
    if total <= 2:
        return True
    if total == 3:
        return third != 8
    if total == 4:
        return 2 <= third <= 7
    if total == 5:
        return 4 <= third <= 7
    if total == 6:
        return third in (6, 7)
    return False


@dataclass(frozen=True, slots=True)
class Coup:
    """Una mano repartida.

    Attributes:
        player: Cartas del Jugador en el orden en que salieron (2 o 3).
        banker: Cartas de la Banca (2 o 3).
        number: Mano dentro de su zapato, desde 1.
        last: Ha salido la carta de corte: es la última mano del zapato.
        left: Cartas que quedan en el zapato tras repartirla.
    """

    player: tuple[Card, ...]
    banker: tuple[Card, ...]
    number: int = 1
    last: bool = False
    left: int = 0

    @property
    def player_total(self) -> int:
        """Puntos finales del Jugador."""
        return hand_points(self.player)

    @property
    def banker_total(self) -> int:
        """Puntos finales de la Banca."""
        return hand_points(self.banker)

    @property
    def winner(self) -> Side:
        """Quién gana: Jugador, Banca o empate."""
        p, b = self.player_total, self.banker_total
        if p == b:
            return Side.TIE
        return Side.PLAYER if p > b else Side.BANKER

    @property
    def natural(self) -> bool:
        """Si alguno de los dos sacó 8 o 9 con las dos primeras cartas."""
        return max(hand_points(self.player[:2]), hand_points(self.banker[:2])) >= 8

    def natural_of(self, side: Side) -> int | None:
        """El natural (8 o 9) de ese lado con sus dos primeras cartas, si lo tiene."""
        cards = self.player if side is Side.PLAYER else self.banker
        points = hand_points(cards[:2])
        return points if points >= 8 else None

    @property
    def cards(self) -> int:
        """Cartas repartidas en la mano (de 4 a 6)."""
        return len(self.player) + len(self.banker)

    @property
    def margin(self) -> int:
        """Diferencia de puntos entre los dos (0 si empatan)."""
        return abs(self.player_total - self.banker_total)

    @property
    def player_pair(self) -> bool:
        """Las dos primeras cartas del Jugador son del mismo valor (una pareja)."""
        return self.player[0].rank == self.player[1].rank

    @property
    def banker_pair(self) -> bool:
        """Las dos primeras cartas de la Banca son del mismo valor."""
        return self.banker[0].rank == self.banker[1].rank

    def comeback(self, side: Side) -> bool:
        """Si `side` gana tras ir perdiendo con las dos primeras cartas (la remontada)."""
        if side is Side.TIE or self.winner is not side:
            return False
        mine = self.player if side is Side.PLAYER else self.banker
        theirs = self.banker if side is Side.PLAYER else self.player
        return hand_points(mine[:2]) < hand_points(theirs[:2])


def deal(draw: Callable[[], Card]) -> tuple[tuple[Card, ...], tuple[Card, ...]]:
    """Reparte una mano con la regla de la tercera carta.

    Args:
        draw: Saca la siguiente carta del zapato.

    Returns:
        Las cartas del Jugador y de la Banca, en el orden en que salieron.
    """
    p1, b1, p2, b2 = draw(), draw(), draw(), draw()
    player = [p1, p2]
    banker = [b1, b2]
    p_total, b_total = hand_points(player), hand_points(banker)
    if p_total >= 8 or b_total >= 8:
        return tuple(player), tuple(banker)
    player_third: Card | None = None
    if player_draws(p_total):
        player_third = draw()
        player.append(player_third)
    if banker_draws(b_total, player_third):
        banker.append(draw())
    return tuple(player), tuple(banker)


def payout(side: Side, stake: int, winner: Side) -> int:
    """Lo que devuelve una apuesta a `side` (con lo apostado incluido); 0 si pierde."""
    if side is Side.TIE:
        return stake * (TIE_PAYS + 1) if winner is Side.TIE else 0
    if winner is Side.TIE:
        return stake
    if winner is not side:
        return 0
    if side is Side.BANKER:
        return 2 * stake - commission(stake)
    return 2 * stake


def commission(stake: int) -> int:
    """Lo que se queda la casa de una apuesta a Banca ganadora: el 5 %, al Y$ más cercano.

    Los medios van para la casa: 10 Y$ pagan 1 de comisión y 30 Y$ pagan 2.
    """
    return (stake * COMMISSION_PERCENT + 50) // 100


def new_cards(shuffle: Callable[[list[Card]], None] | None = None) -> list[Card]:
    """Las 8 barajas barajadas. Se reparten desde el final (`pop`).

    Args:
        shuffle: Baraja en sitio. Por defecto, con el azar del sistema operativo.
    """
    cards = [Card(rank, suit) for _ in range(DECKS) for suit in range(4) for rank in range(1, 14)]
    (shuffle or random.SystemRandom(secrets.randbits(64)).shuffle)(cards)
    return cards


@dataclass(slots=True)
class Shoe:
    """El zapato de una mesa: sus cartas, la carta de corte y lo quemado.

    Attributes:
        cards: Lo que queda, en orden de salida desde el final (`pop`).
        cut: Cuando quedan estas cartas o menos, ha salido la carta de corte.
        burned: Las cartas quemadas al abrirlo (la primera dice cuántas más).
        dealt: Manos repartidas.
        number: Número de zapato en la mesa, desde 1.
    """

    cards: list[Card]
    cut: int = CUT_FROM_END
    burned: tuple[Card, ...] = ()
    dealt: int = 0
    number: int = 1

    @classmethod
    def open(cls, shuffle: Callable[[list[Card]], None] | None = None, *, number: int = 1) -> Shoe:
        """Zapato nuevo: baraja y quema la primera carta y tantas más como su valor."""
        cards = new_cards(shuffle)
        first = cards.pop()
        extra = card_points(first) or 10
        burned = (first, *(cards.pop() for _ in range(extra)))
        return cls(cards=cards, burned=burned, number=number)

    @property
    def left(self) -> int:
        """Cartas que quedan."""
        return len(self.cards)

    @property
    def finished(self) -> bool:
        """Ha salido la carta de corte: no se reparten más manos."""
        return self.left <= self.cut

    def deal(self) -> Coup:
        """Reparte la mano siguiente.

        Raises:
            ValueError: Si el zapato ya está terminado.
        """
        if self.finished:
            raise ValueError("El zapato está terminado")
        player, banker = deal(self.cards.pop)
        self.dealt += 1
        return Coup(
            player=player,
            banker=banker,
            number=self.dealt,
            last=self.finished,
            left=self.left,
        )


# -- Carretera -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Result:
    """Lo que se apunta de cada mano en la carretera."""

    winner: Side
    player: int
    banker: int
    natural: bool = False
    player_pair: bool = False
    banker_pair: bool = False

    @classmethod
    def of(cls, coup: Coup) -> Result:
        """El resultado de una mano."""
        return cls(
            winner=coup.winner,
            player=coup.player_total,
            banker=coup.banker_total,
            natural=coup.natural,
            player_pair=coup.player_pair,
            banker_pair=coup.banker_pair,
        )


@dataclass(frozen=True, slots=True)
class RoadCell:
    """Una casilla de la carretera grande.

    Attributes:
        col: Columna en la cuadrícula (puede pasar de lo que se ve: se recorta al pintar).
        row: Fila, de 0 a `ROAD_ROWS - 1`.
        side: Banca o Jugador (Empate solo si la carretera empieza con empates).
        ties: Empates apuntados en esta casilla.
        index: Posición del resultado en la lista de resultados (para animar el último).
        natural: Si fue un natural.
    """

    col: int
    row: int
    side: Side
    ties: int = 0
    index: int = 0
    natural: bool = False


def big_road(results: Sequence[Result]) -> list[RoadCell]:
    """Las casillas de la carretera grande de un zapato (ver la docstring del módulo)."""
    cells: list[RoadCell] = []
    taken: set[tuple[int, int]] = set()
    leading_ties = 0
    streak_col = -1
    last_side: Side | None = None
    for index, result in enumerate(results):
        if result.winner is Side.TIE:
            if cells:
                prev = cells[-1]
                cells[-1] = RoadCell(
                    prev.col, prev.row, prev.side, prev.ties + 1, prev.index, prev.natural
                )
            else:
                leading_ties += 1
            continue
        if result.winner is last_side and cells:
            prev = cells[-1]
            col, row = prev.col, prev.row + 1
            tail = prev.col != streak_start(cells)
            if tail or row >= ROAD_ROWS or (col, row) in taken:
                # Cola del dragón: sigue a la derecha por la misma fila.
                col, row = prev.col + 1, prev.row
        else:
            streak_col += 1
            while (streak_col, 0) in taken:
                streak_col += 1
            col, row = streak_col, 0
        cell = RoadCell(col, row, result.winner, leading_ties, index, result.natural)
        leading_ties = 0
        cells.append(cell)
        taken.add((col, row))
        last_side = result.winner
    if leading_ties:
        cells.append(RoadCell(0, 0, Side.TIE, leading_ties, len(results) - 1))
    return cells


def streak_start(cells: Sequence[RoadCell]) -> int:
    """Columna en la que empezó la racha de la última casilla (su casilla de la fila 0)."""
    last = start = cells[-1]
    for cell in reversed(cells):
        if cell.side is not last.side:
            break
        start = cell
    return start.col


@dataclass(slots=True)
class RoadStats:
    """Recuento de un zapato para el marcador."""

    banker: int = 0
    player: int = 0
    tie: int = 0
    naturals: int = 0
    #: Racha en curso (sin contar empates) y de qué lado.
    streak: int = 0
    streak_side: Side | None = None
    #: Racha más larga del zapato y de qué lado.
    longest: int = 0
    longest_side: Side | None = None
    by_side: dict[Side, int] = field(default_factory=dict)

    @property
    def hands(self) -> int:
        """Manos jugadas en el zapato."""
        return self.banker + self.player + self.tie


def road_stats(results: Sequence[Result]) -> RoadStats:
    """Recuento, racha en curso y racha más larga de un zapato."""
    stats = RoadStats()
    for result in results:
        if result.natural:
            stats.naturals += 1
        if result.winner is Side.TIE:
            stats.tie += 1
            continue
        if result.winner is Side.BANKER:
            stats.banker += 1
        else:
            stats.player += 1
        if result.winner is stats.streak_side:
            stats.streak += 1
        else:
            stats.streak_side = result.winner
            stats.streak = 1
        if stats.streak > stats.longest:
            stats.longest = stats.streak
            stats.longest_side = stats.streak_side
    return stats


def chop_run(results: Sequence[Result]) -> int:
    """Manos seguidas al final del zapato que alternan Banca y Jugador («ping-pong»).

    Los empates no cortan ni cuentan.
    """
    sides = [r.winner for r in results if r.winner is not Side.TIE]
    run = 1 if sides else 0
    for prev, cur in zip(reversed(sides[:-1]), reversed(sides[1:]), strict=True):
        if prev is cur:
            break
        run += 1
    return run
