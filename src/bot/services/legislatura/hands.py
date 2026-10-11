"""Combinaciones de Legislatura: qué jugada forman unas cartas y cuáles puntúan.

Son las de Balatro con dos cambios por la baraja española:

- **Escaleras de 10 números:** 1-2-3-4-5-6-7-sota-caballo-rey, con la sota
  pegada al 7 como en el chinchón. El as vale abajo (as-2-3-4-5) y arriba
  (7-sota-caballo-rey-as).
- **Las cuarenta:** caballo y rey del mismo palo, como en el tute. Es una
  jugada de dos cartas, más fuerte que la doble pareja, que obliga a decidir
  si se juega ya o se busca algo mejor.

Cada jugada tiene votos y mult de base y sube de nivel con las encuestas
(`HandInfo.at_level`). Las cartas Ladrillo no forman jugada pero siempre
puntúan; el Tránsfuga cuenta como cualquier palo.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from bot.services.legislatura.cards import RANKS, Card, Suit, rank_index


class HandType(StrEnum):
    """Combinación de cartas. El valor se guarda (niveles y estadísticas): no cambiarlo."""

    CARTA_ALTA = "carta_alta"
    PAREJA = "pareja"
    DOBLE_PAREJA = "doble_pareja"
    LAS_CUARENTA = "las_cuarenta"
    TRIO = "trio"
    ESCALERA = "escalera"
    COLOR = "color"
    FULL = "full"
    POKER = "poker"
    ESCALERA_COLOR = "escalera_color"
    REPOKER = "repoker"
    FULL_COLOR = "full_color"
    REPOKER_COLOR = "repoker_color"


@dataclass(frozen=True, slots=True)
class HandInfo:
    """Valores de una combinación.

    Attributes:
        name: Nombre visible.
        votes: Votos de base a nivel 1.
        mult: Mult de base a nivel 1.
        votes_step: Votos que suma cada nivel por encima del 1.
        mult_step: Mult que suma cada nivel por encima del 1.
        secret: Si no se enseña en la tabla hasta jugarla una vez (las que
            solo salen con cartas copiadas o tránsfugas).
    """

    name: str
    votes: int
    mult: int
    votes_step: int
    mult_step: int
    secret: bool = False

    def at_level(self, level: int) -> tuple[int, int]:
        """Votos y mult de base a ese nivel (mínimo 1)."""
        extra = max(level, 1) - 1
        return self.votes + self.votes_step * extra, self.mult + self.mult_step * extra


#: Tabla de combinaciones. Las cifras son las de Balatro salvo las cuarenta
#: (nueva) y se recalibran con el simulador antes de abrir el juego.
HANDS: dict[HandType, HandInfo] = {
    HandType.CARTA_ALTA: HandInfo("Carta alta", 5, 1, 10, 1),
    HandType.PAREJA: HandInfo("Pareja", 10, 2, 15, 1),
    HandType.DOBLE_PAREJA: HandInfo("Doble pareja", 20, 2, 20, 1),
    HandType.LAS_CUARENTA: HandInfo("Las cuarenta", 20, 3, 20, 1),
    HandType.TRIO: HandInfo("Trío", 30, 3, 20, 2),
    HandType.ESCALERA: HandInfo("Escalera", 30, 4, 30, 3),
    HandType.COLOR: HandInfo("Color", 35, 4, 15, 2),
    HandType.FULL: HandInfo("Full", 40, 4, 25, 2),
    HandType.POKER: HandInfo("Póker", 60, 7, 30, 3),
    HandType.ESCALERA_COLOR: HandInfo("Escalera de color", 100, 8, 40, 4),
    HandType.REPOKER: HandInfo("Repóker", 120, 12, 35, 3, secret=True),
    HandType.FULL_COLOR: HandInfo("Full de color", 140, 14, 40, 4, secret=True),
    HandType.REPOKER_COLOR: HandInfo("Repóker de color", 160, 16, 50, 3, secret=True),
}

#: Orden de prioridad, de la mejor a la peor: se elige la primera que se cumpla.
PRIORITY: tuple[HandType, ...] = (
    HandType.REPOKER_COLOR,
    HandType.FULL_COLOR,
    HandType.REPOKER,
    HandType.ESCALERA_COLOR,
    HandType.POKER,
    HandType.FULL,
    HandType.COLOR,
    HandType.ESCALERA,
    HandType.TRIO,
    HandType.LAS_CUARENTA,
    HandType.DOBLE_PAREJA,
    HandType.PAREJA,
    HandType.CARTA_ALTA,
)

#: Cartas que se pueden jugar a la vez.
MAX_PLAY = 5


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Resultado de mirar una jugada.

    Attributes:
        hand: La combinación que cuenta (la de más prioridad).
        scoring: Ids de las cartas que puntúan, en el orden en que se jugaron.
        contains: Todas las combinaciones que hay dentro (un full contiene
            trío, pareja y doble pareja): lo miran asesores del tipo «si la
            jugada contiene una pareja».
    """

    hand: HandType
    scoring: tuple[int, ...]
    contains: frozenset[HandType]


@dataclass(frozen=True, slots=True)
class Rules:
    """Reglas que cambian algunos asesores al mirar una jugada.

    Attributes:
        short: Color y escalera con 4 cartas en vez de 5.
        gaps: La escalera admite saltarse un número entre cartas.
        all_score: Todas las cartas jugadas puntúan, formen jugada o no.
    """

    short: bool = False
    gaps: bool = False
    all_score: bool = False


def _flush(cards: Sequence[Card], need: int) -> list[Card] | None:
    """Las cartas de un color si hay al menos `need` de un mismo palo."""
    best: list[Card] | None = None
    for suit in Suit:
        same = [c for c in cards if c.has_suit(suit)]
        if len(same) >= need and (best is None or len(same) > len(best)):
            best = same
    return best


def _straight(cards: Sequence[Card], need: int, gaps: bool) -> list[Card] | None:
    """Las cartas de una escalera de `need` números seguidos, si la hay."""
    by_index: dict[int, Card] = {}
    for card in cards:
        index = rank_index(card.rank)
        by_index.setdefault(index, card)
        if card.rank == 1:
            by_index.setdefault(len(RANKS), card)  # El as también cierra por arriba.
    indexes = sorted(by_index)
    step = 2 if gaps else 1
    best: list[int] = []
    run: list[int] = []
    for index in indexes:
        if run and 0 < index - run[-1] <= step:
            run.append(index)
        else:
            run = [index]
        if len(run) >= need and len(run) > len(best):
            best = list(run)
    if len(best) < need:
        return None
    chosen = {by_index[i].id for i in best}
    return [c for c in cards if c.id in chosen]


def evaluate(cards: Sequence[Card], rules: Rules | None = None) -> Evaluation:
    """Mira qué jugada forman `cards` (de 1 a `MAX_PLAY` cartas).

    Raises:
        ValueError: Si no hay cartas o hay más de `MAX_PLAY`.
    """
    if not 1 <= len(cards) <= MAX_PLAY:
        raise ValueError(f"Se juegan de 1 a {MAX_PLAY} cartas.")
    rules = rules or Rules()
    need = 4 if rules.short else 5
    ranked = [c for c in cards if not c.is_stone]
    counts = Counter(c.rank for c in ranked)
    groups = sorted(counts.values(), reverse=True)
    flush = _flush(ranked, need)
    straight = _straight(ranked, need, rules.gaps)

    def of_rank(*sizes: int) -> list[Card]:
        """Cartas de los números que se repiten al menos tantas veces."""
        wanted = {rank for rank, n in counts.items() if n >= min(sizes)}
        return [c for c in ranked if c.rank in wanted]

    found: dict[HandType, list[Card]] = {}
    if groups and groups[0] >= 5:
        five = of_rank(5)
        found[HandType.REPOKER] = five
        if flush and len(flush) >= 5 and {c.id for c in five} <= {c.id for c in flush}:
            found[HandType.REPOKER_COLOR] = five
    if len(groups) >= 2 and groups[0] >= 3 and groups[1] >= 2:
        full = of_rank(2)
        found[HandType.FULL] = full
        if flush and {c.id for c in full} <= {c.id for c in flush}:
            found[HandType.FULL_COLOR] = full
    if groups and groups[0] >= 4:
        found[HandType.POKER] = of_rank(4)
    if flush:
        found[HandType.COLOR] = flush
    if straight:
        found[HandType.ESCALERA] = straight
        if flush and {c.id for c in straight} <= {c.id for c in flush}:
            found[HandType.ESCALERA_COLOR] = straight
    if groups and groups[0] >= 3:
        found[HandType.TRIO] = of_rank(3)
    if len([n for n in groups if n >= 2]) >= 2:
        found[HandType.DOBLE_PAREJA] = of_rank(2)
    if groups and groups[0] >= 2:
        found[HandType.PAREJA] = of_rank(2)
    for suit in Suit:
        horse = next((c for c in ranked if c.rank == 11 and c.has_suit(suit)), None)
        king = next((c for c in ranked if c.rank == 12 and c.has_suit(suit)), None)
        if horse and king:
            found[HandType.LAS_CUARENTA] = [horse, king]
            break
    if ranked:
        top = max(ranked, key=lambda c: (c.rank == 1, rank_index(c.rank)))
        found[HandType.CARTA_ALTA] = [top]
    else:
        found[HandType.CARTA_ALTA] = []

    hand = next(h for h in PRIORITY if h in found)
    scoring_ids = {c.id for c in found[hand]}
    scoring_ids |= {c.id for c in cards if c.is_stone}
    if rules.all_score:
        scoring_ids = {c.id for c in cards}
    scoring = tuple(c.id for c in cards if c.id in scoring_ids)
    return Evaluation(hand=hand, scoring=scoring, contains=frozenset(found))
