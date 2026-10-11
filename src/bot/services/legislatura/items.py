"""Tipos comunes del catálogo de Legislatura: bloques, categorías, rarezas y fichas.

Separado de `catalog` para que los módulos de efectos (`advisors`,
`consumables`) puedan usarlos sin importarse en círculo con el registro.

**Categorías** (las de Balatro, con su nombre en Legislatura):

| Balatro | Legislatura | Qué es |
|---|---|---|
| Comodín | Asesor | Efecto permanente en la mesa (5 huecos) |
| Planeta | Encuesta | Sube de nivel una combinación |
| Tarot | Normativa | Cambia cartas concretas |
| Espectral | Cloaca | Muy fuerte, con un coste |
| Vale | Tratado | Mejora permanente de la partida |
| Sobre | Sobre | Abre varias y eliges |
| Etiqueta (Tag) | Abstención | Premio por saltarse una sesión |
| Jefe | Moción de censura | Regla mala en la tercera sesión del periodo |
| Baraja | Gobierno | Cómo empieza la partida |
| Apuesta (Stake) | Prima de riesgo | Dificultad, se desbloquea ganando |

**Bloques.** Todo lo que tiene sátira es de un bloque y la regla del proyecto
es que haya de todos y que se crucen. `catalog` comprueba en sus pruebas que
ningún bloque se quede sin asesores.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from bot.services.legislatura.hands import HandType

if TYPE_CHECKING:
    from bot.services.legislatura.cards import Card
    from bot.services.legislatura.effects import Effect
    from bot.services.legislatura.run import Run


class GameError(Exception):
    """Jugada no permitida. El mensaje es para el usuario, tal cual."""


class Bloc(StrEnum):
    """De quién va la broma."""

    ESPANA = "espana"
    UE = "ue"
    EEUU = "eeuu"
    CHINA = "china"
    ISRAEL = "israel"
    NEUTRO = "neutro"

    @property
    def emoji(self) -> str:
        """Bandera o símbolo del bloque."""
        return {
            "espana": "🇪🇸",
            "ue": "🇪🇺",
            "eeuu": "🇺🇸",
            "china": "🇨🇳",
            "israel": "🇮🇱",
            "neutro": "🏛️",
        }[self.value]


class Category(StrEnum):
    """Qué tipo de cosa es. El valor se guarda: no cambiarlo."""

    ASESOR = "asesor"
    ENCUESTA = "encuesta"
    NORMATIVA = "normativa"
    CLOACA = "cloaca"
    TRATADO = "tratado"
    SOBRE = "sobre"

    @property
    def consumable(self) -> bool:
        """Si va a los huecos de consumibles y se gasta al usarlo."""
        return self in (Category.ENCUESTA, Category.NORMATIVA, Category.CLOACA)


class Rarity(StrEnum):
    """Rareza de un asesor. Fija su probabilidad en la tienda."""

    COMUN = "comun"
    POCO_COMUN = "poco_comun"
    RARO = "raro"
    LEGENDARIO = "legendario"


#: Peso de cada rareza al sacar un asesor en la tienda (los legendarios solo
#: salen de cloacas, como en Balatro).
RARITY_WEIGHTS: dict[Rarity, float] = {
    Rarity.COMUN: 70,
    Rarity.POCO_COMUN: 25,
    Rarity.RARO: 5,
    Rarity.LEGENDARIO: 0,
}


@dataclass(frozen=True, slots=True)
class Usable:
    """Cómo se usa un consumible.

    Attributes:
        min_targets: Cartas de la mano que hay que elegir como mínimo.
        max_targets: Como máximo (0: no se eligen cartas).
        apply: Función que lo aplica y devuelve el texto para el usuario. Puede
            lanzar `GameError` si no se puede usar ahora.
    """

    min_targets: int
    max_targets: int
    apply: Callable[[Run, list[Card]], str]


@dataclass(frozen=True, slots=True)
class PackSpec:
    """Lo que trae un sobre.

    Attributes:
        category: De qué categoría son las opciones (`None`: cartas de la baraja).
        size: Cuántas opciones enseña.
        picks: Cuántas se pueden quedar.
    """

    category: Category | None
    size: int
    picks: int


@dataclass(frozen=True, slots=True)
class ItemDef:
    """Una cosa del catálogo de la partida.

    Attributes:
        key: Clave estable (se guarda en partidas y logros): no se cambia nunca.
        category: Qué es.
        name: Nombre visible.
        emoji: Emoji de los textos (el dibujo puede tener su propio arte).
        bloc: De quién va la broma.
        cost: Precio en fondos de la partida (no en yapdollars).
        text: Qué hace, tal cual se enseña.
        rarity: Solo asesores.
        effect: Solo asesores: sus ganchos.
        usable: Solo consumibles (encuestas, normativas, cloacas).
        hand: Solo encuestas: qué combinación sube.
        pack: Solo sobres.
        redeem: Solo tratados: se aplica una vez al comprarlo.
        visible: Si sale en la tienda (los legendarios no).
    """

    key: str
    category: Category
    name: str
    emoji: str
    bloc: Bloc
    cost: int
    text: str
    rarity: Rarity = Rarity.COMUN
    effect: Effect | None = None
    usable: Usable | None = None
    hand: HandType | None = None
    pack: PackSpec | None = None
    redeem: Callable[[Run], None] | None = None
    visible: bool = True
