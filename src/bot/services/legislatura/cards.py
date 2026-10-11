"""La baraja española de 40 cartas de Legislatura y sus modificadores.

Cada carta tiene un número (1 a 7, sota 10, caballo 11 y rey 12) y un palo, y
puede llevar tres modificadores independientes, como en Balatro:

- **Etiqueta** (`Enhancement`): cambia cómo puntúa la carta (Enchufado,
  Mitin, Tránsfuga, Promesa electoral, Funcionario, Ladrillo, Lingote del
  Banco de España y Lotería de Navidad).
- **Edición** (`Edition`): un extra de presentación (Con fondos UE, Viral,
  Patrocinado). Offshore solo existe en asesores.
- **Sello** (`Seal`): un efecto al jugarla, descartarla o tenerla en la mano.

Las cartas tienen un `id` único dentro de la partida, porque las normativas y
los asesores pueden copiar, cambiar o destruir cartas concretas y la mano se
guarda como lista de ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Suit(StrEnum):
    """Palo de la baraja española."""

    OROS = "oros"
    COPAS = "copas"
    ESPADAS = "espadas"
    BASTOS = "bastos"

    @property
    def emoji(self) -> str:
        """Emoji corto para los textos (el dibujo lleva su propio palo)."""
        return {"oros": "🪙", "copas": "🏆", "espadas": "🗡️", "bastos": "🪵"}[self.value]


#: Números de la baraja de 40, en el orden que cuenta para las escaleras. El 7
#: va seguido de la sota, como en el tute y el chinchón.
RANKS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 10, 11, 12)
RANK_NAMES: dict[int, str] = {
    1: "As",
    2: "Dos",
    3: "Tres",
    4: "Cuatro",
    5: "Cinco",
    6: "Seis",
    7: "Siete",
    10: "Sota",
    11: "Caballo",
    12: "Rey",
}
#: Figuras (para los asesores que miran «cartas con cara»).
FACES: frozenset[int] = frozenset({10, 11, 12})


def rank_votes(rank: int) -> int:
    """Votos base que da una carta al puntuar: el as 11, las figuras 10, el resto su número."""
    if rank == 1:
        return 11
    if rank in FACES:
        return 10
    return rank


def rank_index(rank: int) -> int:
    """Posición del número en `RANKS` (0 para el as)."""
    return RANKS.index(rank)


class Enhancement(StrEnum):
    """Etiqueta de una carta (las mejoras de Balatro)."""

    ENCHUFADO = "enchufado"  # +30 votos al puntuar.
    MITIN = "mitin"  # +4 mult al puntuar.
    TRANSFUGA = "transfuga"  # Cuenta como cualquier palo.
    PROMESA = "promesa"  # ×2 mult al puntuar; 1 de cada 4 veces se rompe.
    FUNCIONARIO = "funcionario"  # ×1,5 mult si se queda en la mano: cobra por estar.
    LADRILLO = "ladrillo"  # +50 votos; sin número ni palo; siempre puntúa.
    LINGOTE = "lingote"  # +3 fondos si sigue en la mano al acabar la sesión.
    LOTERIA = "loteria"  # 1/5: +20 mult; 1/15: +20 fondos.


class Edition(StrEnum):
    """Edición de una carta o de un asesor."""

    FONDOS_UE = "fondos_ue"  # +50 votos.
    VIRAL = "viral"  # +10 mult.
    PATROCINADO = "patrocinado"  # ×1,5 mult.
    OFFSHORE = "offshore"  # Solo asesores: no ocupa hueco.


class Seal(StrEnum):
    """Sello de una carta."""

    BOE = "boe"  # +3 fondos al puntuar.
    VOTO_DELEGADO = "voto_delegado"  # Se activa dos veces.
    BRUSELAS = "bruselas"  # Si acaba la sesión en la mano, crea la encuesta de la última jugada.
    PARTIDO = "partido"  # Al descartarla, crea una normativa.


@dataclass(slots=True)
class Card:
    """Una carta concreta de la partida.

    Attributes:
        id: Único dentro de la partida; las listas de mano y mazo guardan ids.
        rank: Número (ver `RANKS`).
        suit: Palo.
        enhancement: Etiqueta, si tiene.
        edition: Edición, si tiene.
        seal: Sello, si tiene.
    """

    id: int
    rank: int
    suit: Suit
    enhancement: Enhancement | None = None
    edition: Edition | None = None
    seal: Seal | None = None

    @property
    def is_stone(self) -> bool:
        """Si es Ladrillo: no tiene número ni palo para formar jugadas."""
        return self.enhancement is Enhancement.LADRILLO

    @property
    def is_face(self) -> bool:
        """Si es figura (sota, caballo o rey)."""
        return not self.is_stone and self.rank in FACES

    def has_suit(self, suit: Suit) -> bool:
        """Si cuenta como `suit` (el Tránsfuga cuenta como todos; el Ladrillo, como ninguno)."""
        if self.is_stone:
            return False
        return self.enhancement is Enhancement.TRANSFUGA or self.suit is suit

    @property
    def label(self) -> str:
        """Nombre corto para textos: «Rey de oros», «Ladrillo»."""
        if self.is_stone:
            return "Ladrillo"
        return f"{RANK_NAMES[self.rank]} de {self.suit.value}"

    def to_dict(self) -> dict[str, object]:
        """Forma JSON compacta (sin claves vacías)."""
        data: dict[str, object] = {"i": self.id, "r": self.rank, "s": self.suit.value}
        if self.enhancement:
            data["e"] = self.enhancement.value
        if self.edition:
            data["d"] = self.edition.value
        if self.seal:
            data["l"] = self.seal.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Card:
        """Inversa de `to_dict`."""
        return cls(
            id=int(data["i"]),  # type: ignore[arg-type]
            rank=int(data["r"]),  # type: ignore[arg-type]
            suit=Suit(data["s"]),
            enhancement=Enhancement(data["e"]) if "e" in data else None,
            edition=Edition(data["d"]) if "d" in data else None,
            seal=Seal(data["l"]) if "l" in data else None,
        )


@dataclass(slots=True)
class DeckSpec:
    """Cómo es la baraja inicial de un gobierno.

    Attributes:
        suits: Palos que entran.
        ranks: Números que entran.
        extra: Cartas sueltas añadidas (número, palo, etiqueta).
    """

    suits: tuple[Suit, ...] = tuple(Suit)
    ranks: tuple[int, ...] = RANKS
    extra: tuple[tuple[int, Suit, Enhancement | None], ...] = field(default_factory=tuple)


def build_deck(spec: DeckSpec | None = None) -> list[Card]:
    """Baraja inicial con ids correlativos desde 1 (la de 40 si no se dice otra)."""
    spec = spec or DeckSpec()
    cards = [Card(0, rank, suit) for suit in spec.suits for rank in spec.ranks]
    cards += [Card(0, rank, suit, enhancement) for rank, suit, enhancement in spec.extra]
    for number, card in enumerate(cards, start=1):
        card.id = number
    return cards
