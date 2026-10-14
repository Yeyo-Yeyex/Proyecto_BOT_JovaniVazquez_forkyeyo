"""Reglas del Casino Hold'em: póker Texas Hold'em contra la banca, con una sola decisión.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.poker`) cobra el ante al
repartir, cobra la apuesta de ir si se va, paga al final y pinta la mesa
(`bot.services.poker_scene`).

**Cómo se juega.** Es el Casino Hold'em de los casinos de verdad:

1. Se pone el **ante** y se reparten dos cartas al jugador, dos boca abajo a la
   banca y el flop (tres comunitarias) boca arriba.
2. Con sus dos cartas y el flop a la vista, el jugador decide:
   - **Ir** (`call`): pone el doble del ante y se ve el final.
   - **Retirarse** (`fold`): pierde el ante y ya.
3. Si va, salen el turn y el river, la banca enseña sus cartas y cada uno juega
   su mejor mano de cinco con sus dos cartas y las cinco comunitarias.

**La banca juega** («califica») con pareja de cuatros o mejor (`qualifies`). Si
no juega, el ante cobra según la tabla y la apuesta de ir se devuelve, gane
quien gane. Si juega, se comparan las manos: si gana el jugador, el ante cobra
según la tabla y la de ir paga 1 a 1; si empatan, se devuelve todo; si gana la
banca, se pierde todo.

**Tabla del ante** (`ANTE_PAYS`, lo que se gana además del ante): escalera real
100 a 1, escalera de color 20 a 1, póker 10 a 1, full 3 a 1, color 2 a 1 y
escalera o menos 1 a 1.

Ventaja de la casa: 2,16 % del ante con la estrategia óptima (ir con casi todo:
se va en un 82 % de las manos), la cifra que se publica para estas reglas y esta
tabla. Es más que el 1 % de los juegos de cobrar a tiempo porque aquí hay una
decisión de verdad: retirarse con lo que no toca le gana algo a ir siempre (ir
siempre cuesta un 8 %). `docs/rtp_poker.py` lo comprueba con estas reglas: con
una estrategia aprendida, algo peor que la óptima, sale un 2,6 % ± 0,2 %.

**Una baraja sin reponer.** Las nueve cartas de la mano (dos del jugador, dos de
la banca y cinco comunitarias) salen de una baraja de 52 barajada (`deal`). Se
sortean al repartir, antes de decidir: lo que sale no depende de lo que se
elija, igual que en la mesa de verdad, donde el turn y el river ya están en el
mazo. Así el cog dibuja el final de las dos opciones mientras el jugador piensa.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from functools import total_ordering
from itertools import combinations

from bot.services.blackjack import Card

#: Clave del juego en el libro (`poker:apuesta`), en `casino_stats.GAMES` y en las porras.
GAME = "poker"
#: La apuesta de ir es el doble del ante.
CALL_FACTOR = 2
#: Cartas de una mano: dos del jugador, dos de la banca y cinco comunitarias.
DEAL_SIZE = 9
#: La banca juega con pareja de este valor o mejor (pareja de cuatros).
QUALIFY_PAIR = 4


def height(card: Card) -> int:
    """Altura de la carta para comparar: del 2 al 14 (el as es la más alta)."""
    return 14 if card.rank == 1 else card.rank


class Rank(Enum):
    """Categorías de mano, de peor a mejor. El valor ordena; `label` es lo que se enseña."""

    HIGH_CARD = (0, "Carta alta")
    PAIR = (1, "Pareja")
    TWO_PAIR = (2, "Doble pareja")
    TRIPS = (3, "Trío")
    STRAIGHT = (4, "Escalera")
    FLUSH = (5, "Color")
    FULL_HOUSE = (6, "Full")
    QUADS = (7, "Póker")
    STRAIGHT_FLUSH = (8, "Escalera de color")
    ROYAL_FLUSH = (9, "Escalera real")

    def __init__(self, order: int, label: str) -> None:
        self.order = order
        self.label = label

    @property
    def key(self) -> str:
        """Clave corta y estable (para estadísticas): `high_card`, `royal_flush`…"""
        return self.name.lower()


#: Lo que gana el ante, además de devolverse, cuando cobra según la tabla.
ANTE_PAYS: dict[Rank, int] = {
    Rank.ROYAL_FLUSH: 100,
    Rank.STRAIGHT_FLUSH: 20,
    Rank.QUADS: 10,
    Rank.FULL_HOUSE: 3,
    Rank.FLUSH: 2,
}


def ante_pays(rank: Rank) -> int:
    """Cuántas veces el ante se gana con esta mano: 1 de escalera para abajo."""
    return ANTE_PAYS.get(rank, 1)


#: Cómo se nombra cada altura en plural (`Pareja de ases`) y en corto (`Escalera al K`).
HEIGHT_NAMES = {14: "ases", 13: "reyes", 12: "damas", 11: "jotas", 10: "dieces", 9: "nueves",
                8: "ochos", 7: "sietes", 6: "seises", 5: "cincos", 4: "cuatros", 3: "treses",
                2: "doses"}  # fmt: skip
HEIGHT_LABELS = {14: "A", 13: "K", 12: "Q", 11: "J", **{h: str(h) for h in range(2, 11)}}


@total_ordering
@dataclass(frozen=True, slots=True)
class HandValue:
    """Lo que vale una mano de cinco cartas; se comparan con `<` y `==`.

    Attributes:
        order: La categoría (`Rank.order`), lo primero que se compara.
        tiebreak: Alturas que desempatan, de más a menos importante.
        rank: La categoría.
        cards: Las cinco cartas, de la que más pesa a la que menos (no cuentan
            al comparar: dos manos con las mismas alturas empatan).
    """

    order: int
    tiebreak: tuple[int, ...]
    rank: Rank
    cards: tuple[Card, ...]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HandValue):
            return NotImplemented
        return (self.order, self.tiebreak) == (other.order, other.tiebreak)

    def __lt__(self, other: HandValue) -> bool:
        return (self.order, self.tiebreak) < (other.order, other.tiebreak)

    def __hash__(self) -> int:
        return hash((self.order, self.tiebreak))

    @property
    def label(self) -> str:
        """`Pareja de ases`, `Full de reyes y doses`, `Escalera al 9`…"""
        top = self.tiebreak[0]
        match self.rank:
            case Rank.HIGH_CARD:
                return f"Carta alta: {HEIGHT_LABELS[top]}"
            case Rank.PAIR | Rank.TRIPS | Rank.QUADS:
                return f"{self.rank.label} de {HEIGHT_NAMES[top]}"
            case Rank.TWO_PAIR | Rank.FULL_HOUSE:
                second = HEIGHT_NAMES[self.tiebreak[1]]
                return f"{self.rank.label} de {HEIGHT_NAMES[top]} y {second}"
            case Rank.STRAIGHT | Rank.STRAIGHT_FLUSH | Rank.FLUSH:
                return f"{self.rank.label} al {HEIGHT_LABELS[top]}"
        return self.rank.label


def _five(cards: Sequence[Card]) -> HandValue:
    """El valor de exactamente cinco cartas."""
    heights = sorted((height(c) for c in cards), reverse=True)
    counts = Counter(heights)
    # Por grupos: primero los más repetidos y, a igualdad, los más altos.
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    flush = len({c.suit for c in cards}) == 1
    distinct = sorted(counts, reverse=True)
    straight_high = 0
    if len(distinct) == 5:
        if distinct[0] - distinct[4] == 4:
            straight_high = distinct[0]
        elif distinct == [14, 5, 4, 3, 2]:  # la rueda: el as cuenta como 1
            straight_high = 5
    if straight_high and flush:
        rank = Rank.ROYAL_FLUSH if straight_high == 14 else Rank.STRAIGHT_FLUSH
        tiebreak: tuple[int, ...] = (straight_high,)
    elif groups[0][1] == 4:
        rank, tiebreak = Rank.QUADS, (groups[0][0], groups[1][0])
    elif groups[0][1] == 3 and groups[1][1] == 2:
        rank, tiebreak = Rank.FULL_HOUSE, (groups[0][0], groups[1][0])
    elif flush:
        rank, tiebreak = Rank.FLUSH, tuple(heights)
    elif straight_high:
        rank, tiebreak = Rank.STRAIGHT, (straight_high,)
    elif groups[0][1] == 3:
        rank, tiebreak = Rank.TRIPS, tuple(h for h, _ in groups)
    elif groups[0][1] == 2 and groups[1][1] == 2:
        rank, tiebreak = Rank.TWO_PAIR, tuple(h for h, _ in groups)
    elif groups[0][1] == 2:
        rank, tiebreak = Rank.PAIR, tuple(h for h, _ in groups)
    else:
        rank, tiebreak = Rank.HIGH_CARD, tuple(heights)
    # Las cartas en el orden en que pesan: grupos primero; en la rueda, el as al final.
    weight = {h: (n, h) for h, n in counts.items()}
    if straight_high == 5:
        weight[14] = (1, 1)
    ordered = tuple(sorted(cards, key=lambda c: weight[height(c)], reverse=True))
    return HandValue(rank.order, tiebreak, rank, ordered)


def evaluate(cards: Sequence[Card]) -> HandValue:
    """La mejor mano de cinco que se puede hacer con 5, 6 o 7 cartas.

    Raises:
        ValueError: Con menos de cinco cartas.
    """
    if len(cards) < 5:
        raise ValueError("Hacen falta al menos cinco cartas.")
    return max(_five(combo) for combo in combinations(cards, 5))


def qualifies(value: HandValue) -> bool:
    """Si la banca juega: pareja de cuatros o mejor."""
    if value.rank is Rank.HIGH_CARD:
        return False
    return value.rank is not Rank.PAIR or value.tiebreak[0] >= QUALIFY_PAIR


def full_deck() -> list[Card]:
    """Las 52 cartas de una baraja francesa."""
    return [Card(rank, suit) for suit in range(4) for rank in range(1, 14)]


@dataclass(frozen=True, slots=True)
class Deal:
    """Las nueve cartas de una mano, sorteadas al repartir.

    Attributes:
        player: Las dos del jugador.
        dealer: Las dos de la banca (boca abajo hasta el final).
        board: Las cinco comunitarias: flop (las tres primeras), turn y river.
    """

    player: tuple[Card, Card]
    dealer: tuple[Card, Card]
    board: tuple[Card, Card, Card, Card, Card]

    @property
    def flop(self) -> tuple[Card, ...]:
        """Las tres comunitarias que se ven al decidir."""
        return self.board[:3]

    @property
    def player_value(self) -> HandValue:
        """La mejor mano del jugador con las siete cartas."""
        return evaluate((*self.player, *self.board))

    @property
    def dealer_value(self) -> HandValue:
        """La mejor mano de la banca con las siete cartas."""
        return evaluate((*self.dealer, *self.board))

    @property
    def flop_value(self) -> HandValue:
        """Lo que lleva el jugador al decidir: sus dos cartas y el flop."""
        return evaluate((*self.player, *self.flop))


def deal(rng: random.Random) -> Deal:
    """Baraja y reparte una mano: jugador, banca y comunitarias, sin repetir carta."""
    cards = rng.sample(full_deck(), DEAL_SIZE)
    return Deal(
        player=(cards[0], cards[1]),
        dealer=(cards[2], cards[3]),
        board=(cards[4], cards[5], cards[6], cards[7], cards[8]),
    )


class Outcome(Enum):
    """Cómo acaba una mano. El valor es la clave para estadísticas y porras."""

    FOLD = "fold"
    NO_QUALIFY = "no_qualify"
    WIN = "win"
    TIE = "tie"
    LOSE = "lose"


class PokerError(Exception):
    """Acción no válida en la mano; el mensaje se puede enseñar al usuario."""


@dataclass(slots=True)
class PokerHand:
    """Una mano de Casino Hold'em.

    Attributes:
        ante: El ante (ya cobrado por la economía al repartir).
        cards: Las nueve cartas, sorteadas al repartir.
        called: `True` si fue, `False` si se retiró, `None` mientras decide.
    """

    ante: int
    cards: Deal
    called: bool | None = None

    @classmethod
    def new(cls, ante: int, rng: random.Random, *, cards: Deal | None = None) -> PokerHand:
        """Reparte una mano con su ante.

        Args:
            cards: Las cartas ya sorteadas (`deal`), si se sortearon antes para
                dibujar la animación por adelantado; si no, se sortean aquí.

        Raises:
            ValueError: Si el ante no es positivo.
        """
        if ante <= 0:
            raise ValueError("El ante debe ser positivo.")
        return cls(ante=ante, cards=cards if cards is not None else deal(rng))

    @property
    def deciding(self) -> bool:
        """Si aún falta decidir entre ir y retirarse."""
        return self.called is None

    @property
    def call_stake(self) -> int:
        """Lo que cuesta ir: el doble del ante."""
        return self.ante * CALL_FACTOR

    @property
    def staked(self) -> int:
        """Lo apostado en total: el ante y, si fue, la apuesta de ir."""
        return self.ante + (self.call_stake if self.called else 0)

    def call(self) -> None:
        """Va: pone el doble del ante y se ve el final.

        Raises:
            PokerError: Si ya había decidido.
        """
        if not self.deciding:
            raise PokerError("Esta mano ya está decidida.")
        self.called = True

    def fold(self) -> None:
        """Se retira y pierde el ante.

        Raises:
            PokerError: Si ya había decidido.
        """
        if not self.deciding:
            raise PokerError("Esta mano ya está decidida.")
        self.called = False

    @property
    def outcome(self) -> Outcome:
        """Cómo acaba (o acabaría, si fuera) la mano.

        Raises:
            PokerError: Si aún no ha decidido.
        """
        if self.called is None:
            raise PokerError("Aún no has decidido.")
        if not self.called:
            return Outcome.FOLD
        return showdown(self.cards)

    @property
    def payout(self) -> int:
        """Lo devuelto al terminar, apuestas incluidas (0 si se retiró o perdió)."""
        return payout(self.cards, self.ante, called=bool(self.called)) if not self.deciding else 0

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la mano terminada."""
        return self.payout - self.staked

    @property
    def would_have(self) -> Outcome:
        """Lo que habría pasado yendo (para retirarse y mirar, y los logros)."""
        return showdown(self.cards)


def showdown(cards: Deal) -> Outcome:
    """El resultado si el jugador va: la banca no juega, gana, empata o pierde."""
    dealer = cards.dealer_value
    if not qualifies(dealer):
        return Outcome.NO_QUALIFY
    player = cards.player_value
    if player > dealer:
        return Outcome.WIN
    if player == dealer:
        return Outcome.TIE
    return Outcome.LOSE


def payout(cards: Deal, ante: int, *, called: bool) -> int:
    """Lo que se devuelve (apuestas incluidas) con estas cartas y este ante.

    Si la banca no juega, el ante cobra por la tabla y la de ir se devuelve; si
    gana el jugador, el ante cobra por la tabla y la de ir paga 1 a 1; si
    empatan, se devuelve todo.
    """
    if not called:
        return 0
    call = ante * CALL_FACTOR
    ante_won = ante * (1 + ante_pays(cards.player_value.rank))
    match showdown(cards):
        case Outcome.NO_QUALIFY:
            return ante_won + call
        case Outcome.WIN:
            return ante_won + 2 * call
        case Outcome.TIE:
            return ante + call
    return 0


def paytable() -> list[tuple[str, str]]:
    """La tabla del ante para la ayuda: (mano, cuánto paga)."""
    rows = [(rank.label, f"{pays} a 1") for rank, pays in ANTE_PAYS.items()]
    rows.append(("Escalera o menos", "1 a 1"))
    return rows
