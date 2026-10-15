"""Reglas de la Torre del dragón: nueve pisos, un dragón (o varios) en cada uno.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.tower`) cobra al
empezar, paga al acabar y pinta la torre con texto y botones.

La torre tiene `FLOORS` (9) pisos. En cada piso hay `width` puertas y
`dragons` de ellas esconden un dragón 🐉; las demás, un huevo 🥚. El jugador
elige una puerta por piso, de abajo arriba: con huevo sube de piso y sube el
multiplicador; con dragón lo pierde todo. Puede cobrar cuando quiera (tras
al menos un piso) y coronar el piso 9 cobra solo.

Cinco dificultades (`Difficulty`), que cambian puertas y dragones:

    Fácil    4 puertas, 1 dragón  (la de por defecto)
    Normal   3 puertas, 1 dragón
    Difícil  2 puertas, 1 dragón
    Experto  3 puertas, 2 dragones
    Maestro  4 puertas, 3 dragones

Los dragones de cada piso se sortean al empezar, con el azar del sistema
operativo, y no se mueven: al acabar se enseña la torre entera.

Tras `k` pisos superados con `N` puertas y `D` dragones el multiplicador es el
inverso de la probabilidad de haber llegado hasta ahí, por el retorno al
jugador:

    0,99 / ((N − D) / N) ** k

Así que cobrar en cualquier momento devuelve de media el 99 % de lo apostado,
se juegue como se juegue. **No hay tope de multiplicador**: coronar Maestro
paga ×259.522 (1 entre 262.144). Se calcula con fracciones exactas para que el
pago no dependa de redondeos de coma flotante.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

from bot.services.mines import format_multiplier

#: Pisos de la torre.
FLOORS = 9
#: Retorno al jugador al cobrar en cualquier piso: 99 %.
RTP = Fraction(99, 100)


class Difficulty(Enum):
    """Una dificultad: cuántas puertas tiene cada piso y cuántas esconden un dragón.

    El valor del enum es la clave estable (minúscula y sin tilde), la que se
    escribe en el comando y la que usan las estadísticas de logros.

    Attributes:
        label: Nombre visible.
        emoji: Forma que la distingue en el menú.
        width: Puertas por piso.
        dragons: Puertas con dragón en cada piso.
    """

    EASY = ("facil", "Fácil", "🥚", 4, 1)
    NORMAL = ("normal", "Normal", "🐣", 3, 1)
    HARD = ("dificil", "Difícil", "🔥", 2, 1)
    EXPERT = ("experto", "Experto", "🐲", 3, 2)
    MASTER = ("maestro", "Maestro", "👑", 4, 3)

    label: str
    emoji: str
    width: int
    dragons: int

    def __new__(cls, key: str, label: str, emoji: str, width: int, dragons: int) -> Difficulty:
        obj = object.__new__(cls)
        obj._value_ = key
        obj.label = label
        obj.emoji = emoji
        obj.width = width
        obj.dragons = dragons
        return obj

    @property
    def key(self) -> str:
        """Clave estable de la dificultad (`facil`, `maestro`…)."""
        return str(self.value)

    @property
    def safe_chance(self) -> Fraction:
        """Probabilidad de que una puerta cualquiera de un piso sea un huevo."""
        return Fraction(self.width - self.dragons, self.width)


DIFFICULTIES: tuple[Difficulty, ...] = tuple(Difficulty)
DIFFICULTY_BY_KEY: dict[str, Difficulty] = {d.key: d for d in DIFFICULTIES}
#: La que sale si nadie elige: es la que pidió el servidor.
DEFAULT_DIFFICULTY = Difficulty.EASY
#: Cómo se acepta escribir una dificultad además de su clave.
_ALIASES = {
    "fácil": "facil",
    "f": "facil",
    "easy": "facil",
    "n": "normal",
    "d": "dificil",
    "difícil": "dificil",
    "hard": "dificil",
    "e": "experto",
    "expert": "experto",
    "m": "maestro",
    "master": "maestro",
}


def parse_difficulty(text: str) -> Difficulty:
    """`dificil`, `Difícil`, `d`… → la dificultad correspondiente.

    Raises:
        ValueError: Con un mensaje mostrable si no es ninguna.
    """
    key = text.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in DIFFICULTY_BY_KEY:
        names = ", ".join(f"`{d.key}`" for d in DIFFICULTIES)
        raise ValueError(f"La dificultad es una de estas: {names}.")
    return DIFFICULTY_BY_KEY[key]


def survival(difficulty: Difficulty, floors: int) -> Fraction:
    """Probabilidad de superar `floors` pisos seguidos."""
    return difficulty.safe_chance**floors


def multiplier(difficulty: Difficulty, floors: int) -> Fraction:
    """Multiplicador exacto tras `floors` pisos superados (×1 con 0).

    Raises:
        ValueError: Si `floors` no cabe en la torre.
    """
    if not 0 <= floors <= FLOORS:
        raise ValueError("Ese piso no existe.")
    if floors == 0:
        return Fraction(1)
    return RTP / survival(difficulty, floors)


def multiplier_cents(difficulty: Difficulty, floors: int) -> int:
    """El multiplicador en centésimas, redondeado hacia abajo (para enseñarlo)."""
    return math.floor(multiplier(difficulty, floors) * 100)


def payout(stake: int, difficulty: Difficulty, floors: int) -> int:
    """Lo que cobra quien se retira tras `floors` pisos (apuesta incluida)."""
    return math.floor(stake * multiplier(difficulty, floors))


def plural_dragons(count: int) -> str:
    """`1` → `1 dragón`; `3` → `3 dragones`."""
    return f"{count} dragón" if count == 1 else f"{count} dragones"


class Status(Enum):
    """Estado de una partida."""

    PLAYING = "playing"
    BURNED = "burned"
    CASHED = "cashed"


class TowerError(Exception):
    """Acción no válida en la partida; el mensaje se puede enseñar al usuario."""


@dataclass(slots=True)
class TowerGame:
    """Una partida de la Torre del dragón.

    Attributes:
        stake: Lo apostado (ya cobrado por la economía).
        difficulty: Puertas y dragones de cada piso.
        dragons: Puertas con dragón de cada piso, de abajo arriba. Se sortean
            al empezar y no se mueven.
        picks: Puerta elegida en cada piso superado, de abajo arriba.
        burned: La puerta con dragón que se pisó, si se pisó alguna.
        random_picks: Cuántas puertas se eligieron con 🎲 (para los logros).
    """

    stake: int
    difficulty: Difficulty
    dragons: tuple[frozenset[int], ...]
    picks: list[int] = field(default_factory=list)
    status: Status = Status.PLAYING
    burned: int | None = None
    random_picks: int = 0

    @classmethod
    def new(cls, stake: int, difficulty: Difficulty, rng: random.Random) -> TowerGame:
        """Empieza una partida sorteando los dragones de los nueve pisos.

        Raises:
            ValueError: Si la apuesta no es positiva.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        dragons = tuple(
            frozenset(rng.sample(range(difficulty.width), difficulty.dragons))
            for _ in range(FLOORS)
        )
        return cls(stake=stake, difficulty=difficulty, dragons=dragons)

    # -- Estado -----------------------------------------------------------------------

    @property
    def playing(self) -> bool:
        """Si se puede seguir subiendo."""
        return self.status is Status.PLAYING

    @property
    def floor(self) -> int:
        """Pisos superados (0 al empezar, 9 al coronar)."""
        return len(self.picks)

    @property
    def crowned(self) -> bool:
        """Si ya se ha superado el último piso."""
        return self.floor == FLOORS

    @property
    def cents(self) -> int:
        """Multiplicador actual en centésimas."""
        return multiplier_cents(self.difficulty, self.floor)

    @property
    def next_cents(self) -> int | None:
        """Multiplicador si el siguiente piso se supera; `None` si ya se coronó."""
        if self.crowned:
            return None
        return multiplier_cents(self.difficulty, self.floor + 1)

    @property
    def safe_chance(self) -> Fraction:
        """Probabilidad de que la puerta del siguiente piso sea un huevo."""
        return self.difficulty.safe_chance

    @property
    def cashout_value(self) -> int:
        """Lo que se cobraría ahora mismo."""
        return payout(self.stake, self.difficulty, self.floor)

    @property
    def next_value(self) -> int | None:
        """Lo que se cobraría tras superar el siguiente piso."""
        if self.crowned:
            return None
        return payout(self.stake, self.difficulty, self.floor + 1)

    @property
    def payout(self) -> int:
        """Lo cobrado al terminar: 0 si lo quemó un dragón."""
        if self.status is Status.CASHED:
            return self.cashout_value
        return 0

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la partida terminada."""
        return self.payout - self.stake

    # -- Acciones ---------------------------------------------------------------------

    def climb(self, door: int, *, random_pick: bool = False) -> bool:
        """Elige una puerta del piso actual. Devuelve `True` si era un huevo.

        Si era el último piso no cobra solo: el cog llama a `cash_out` justo
        después (`crowned`).

        Raises:
            TowerError: Si la partida terminó o la puerta no existe.
        """
        if not self.playing:
            raise TowerError("La partida ya ha terminado.")
        if not 0 <= door < self.difficulty.width:
            raise TowerError("Esa puerta no existe.")
        if random_pick:
            self.random_picks += 1
        if door in self.dragons[self.floor]:
            self.status = Status.BURNED
            self.burned = door
            return False
        self.picks.append(door)
        return True

    def cash_out(self) -> int:
        """Se retira y devuelve lo que cobra.

        Raises:
            TowerError: Si la partida terminó o no se ha subido ningún piso.
        """
        if not self.playing:
            raise TowerError("La partida ya ha terminado.")
        if not self.floor:
            raise TowerError("Sube al menos un piso antes de cobrar.")
        self.status = Status.CASHED
        return self.cashout_value

    def random_door(self, rng: random.Random) -> int:
        """Una puerta del piso actual al azar (para 🎲).

        Raises:
            TowerError: Si la partida terminó.
        """
        if not self.playing:
            raise TowerError("La partida ya ha terminado.")
        return rng.randrange(self.difficulty.width)


# -- Mensajes de progreso -----------------------------------------------------------------

#: Frases al llegar a un piso que merece celebrarse; se elige una al azar.
MILESTONES: dict[int, tuple[str, ...]] = {
    3: (
        "🔥 Tres pisos: subes más rápido que el alquiler en Madrid.",
        "🔥 Tres pisos sin quemarte: Ayuso lo vendería como un hito de gestión.",
        "🔥 Tres pisos y todavía no te ha mordido ni Hacienda.",
    ),
    5: (
        "🏯 ¡Mitad de torre! La de Babel se quedó en obras y tú vas por la mitad.",
        "🏯 ¡Cinco pisos! Daenerys quemó ciudades enteras con menos mérito.",
        "🏯 ¡Cinco pisos! Juego de Tronos acabó peor que esto.",
    ),
    7: (
        "💪 ¡Siete pisos! Ya se ve el Teide desde aquí.",
        "💪 ¡Siete pisos! El Dragón de Xi tomaría nota.",
        "💪 ¡Siete pisos! Más alto que los fondos Next Generation… y de momento llegan.",
    ),
    8: (
        "😰 Solo queda el último piso… y ahí duerme el dragón gordo.",
        "😰 Un piso para la cima. Respira, que el dragón rojo de Gales también se cansa.",
        "😰 Ocho pisos y el último da vértigo, como una moción de censura.",
    ),
    FLOORS: (
        "🏁 ¡CIMA! Torre conquistada.",
        "🏁 ¡CIMA! Ni el dragón rojo de Gales te tose.",
        "🏁 ¡CIMA! Cobras sin pasar por el despacho de Moncloa.",
    ),
}


def milestone(floor: int, rng: random.Random | None = None) -> str | None:
    """Frase para los pisos que merecen celebrarse; `None` para el resto.

    Se dicen al llegar, no después, para que subir se note piso a piso.
    """
    lines = MILESTONES.get(floor)
    if not lines:
        return None
    return (rng or random).choice(lines)


def risk_summary(difficulty: Difficulty) -> str:
    """Texto de una dificultad en el menú: puertas, dragones y el premio de coronar.

    Ejemplo en Difícil: `2 puertas, 1 dragón · Coronar: ×506`.
    """
    whole = format_multiplier(multiplier_cents(difficulty, FLOORS)).split(",")[0]
    return f"{difficulty.width} puertas, {plural_dragons(difficulty.dragons)} · Coronar: {whole}"
