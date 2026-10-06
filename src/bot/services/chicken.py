"""Reglas del Pollo (estilo Chicken Road): cruzar carriles sin que te atropellen.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.chicken`) cobra al
empezar, paga al retirarse y pinta la carretera (`bot.services.chicken_render`).

El pollo sale de la acera y cruza una autopista carril a carril. Cada carril
cruzado sube el multiplicador; el jugador puede cobrar cuando quiera, y si
le atropellan lo pierde todo. Si llega a la meta, cobra solo el premio gordo.

**Dificultades.** Cada una tiene una probabilidad fija de atropello por
carril y un número de carriles:

| Dificultad | Atropello | Carriles | Meta     |
|------------|-----------|----------|----------|
| Fácil      | 4 %       | 24       | ×2,63    |
| Media      | 12 %      | 22       | ×16,48   |
| Difícil    | 20 %      | 20       | ×85,86   |
| Hardcore   | 40 %      | 15       | ×2.105,5 |

**Multiplicador.** Tras cruzar `k` carriles con atropello `p`:

    0,99 / (1 − p)^k

Es el inverso de la probabilidad de haber llegado, por el retorno al jugador
(99 %, como Minas y el Crash). Cobrar en cualquier carril devuelve de media
el 99 % de lo apostado, se juegue como se juegue. Se calcula con fracciones
exactas para que el pago no dependa de redondeos de coma flotante.

**El carril del atropello se sortea al empezar** (`draw_hit_lane`): se tira
el dado carril a carril antes del primer paso y se guarda el primero que
atropella. Es la misma probabilidad que tirar en cada paso, y permite
enseñar al cobrar dónde estaba el coche («cobraste en el 6 y el coche venía
en el 9»), que es lo que hace pulsar 🔁.

**Autocobro.** El jugador fija un multiplicador objetivo y el pollo cruza
solo hasta alcanzarlo (o hasta que le atropellen), en un único GIF.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

#: Retorno al jugador: 99 %, ventaja de la casa 1 %.
RTP = Fraction(99, 100)


@dataclass(frozen=True, slots=True)
class Difficulty:
    """Una dificultad: cuánto atropella cada carril y cuántos hay.

    Attributes:
        key: Lo que se escribe en el comando (`facil`, `media`…).
        name: Nombre visible.
        road: Qué carretera es (para el texto y el rótulo de la imagen).
        emoji: Forma que la distingue en el menú.
        hit: Probabilidad de atropello en cada carril.
        lanes: Carriles hasta la meta.
    """

    key: str
    name: str
    road: str
    emoji: str
    hit: Fraction
    lanes: int


DIFFICULTIES: tuple[Difficulty, ...] = (
    Difficulty("facil", "Fácil", "Calle de urbanización", "🟢", Fraction(1, 25), 24),
    Difficulty("media", "Media", "Carretera nacional", "🔷", Fraction(3, 25), 22),
    Difficulty("dificil", "Difícil", "Autovía en hora punta", "🔶", Fraction(1, 5), 20),
    Difficulty("hardcore", "Hardcore", "Autopista en operación salida", "💀", Fraction(2, 5), 15),
)
DIFFICULTY_BY_KEY: dict[str, Difficulty] = {d.key: d for d in DIFFICULTIES}
#: Alias que se aceptan al escribir la dificultad.
_ALIASES = {
    "fácil": "facil",
    "f": "facil",
    "easy": "facil",
    "m": "media",
    "medio": "media",
    "normal": "media",
    "difícil": "dificil",
    "d": "dificil",
    "hard": "dificil",
    "h": "hardcore",
    "extremo": "hardcore",
    "extrema": "hardcore",
}
DEFAULT_DIFFICULTY = "media"

#: Objetivos de autocobro que ofrece el menú (centésimas). Solo se enseñan
#: los que la dificultad puede alcanzar antes de la meta.
AUTO_TARGETS: tuple[int, ...] = (
    110, 125, 150, 200, 300, 500, 1_000, 2_500, 5_000, 10_000, 50_000, 100_000,
)  # fmt: skip


def parse_difficulty(text: str) -> Difficulty:
    """`dificil`, `difícil`, `d`, `hard` → la dificultad Difícil.

    Raises:
        ValueError: Con un mensaje mostrable si no es ninguna.
    """
    key = text.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in DIFFICULTY_BY_KEY:
        names = ", ".join(f"`{d.key}`" for d in DIFFICULTIES)
        raise ValueError(f"Las dificultades son {names}.")
    return DIFFICULTY_BY_KEY[key]


def survival(difficulty: Difficulty, lanes: int) -> Fraction:
    """Probabilidad de cruzar `lanes` carriles seguidos sin que te atropellen."""
    return (1 - difficulty.hit) ** lanes


def multiplier(difficulty: Difficulty, lanes: int) -> Fraction:
    """Multiplicador exacto tras cruzar `lanes` carriles (×1 en la acera).

    Raises:
        ValueError: Si `lanes` no está entre 0 y los carriles de la dificultad.
    """
    if not 0 <= lanes <= difficulty.lanes:
        raise ValueError("Esos carriles no existen.")
    if lanes == 0:
        return Fraction(1)
    return RTP / survival(difficulty, lanes)


def multiplier_cents(difficulty: Difficulty, lanes: int) -> int:
    """El multiplicador en centésimas, redondeado hacia abajo (para enseñarlo)."""
    return math.floor(multiplier(difficulty, lanes) * 100)


def payout(stake: int, difficulty: Difficulty, lanes: int) -> int:
    """Lo que cobra quien se retira tras `lanes` carriles (apuesta incluida)."""
    return math.floor(stake * multiplier(difficulty, lanes))


def format_multiplier(cents: int) -> str:
    """`124` → `×1,24`; los miles llevan punto."""
    whole, frac = divmod(cents, 100)
    return "×" + f"{whole:,}".replace(",", ".") + f",{frac:02d}"


def short_multiplier(cents: int) -> str:
    """Versión corta para las alcantarillas: `×1,24`, `×16,6`, `×2.106`."""
    if cents >= 100_000:
        return "×" + f"{cents // 100:,}".replace(",", ".")
    if cents >= 1_000:
        return f"×{cents / 100:.1f}".replace(".", ",")
    return format_multiplier(cents)


def lanes_for_target(difficulty: Difficulty, target_cents: int) -> int:
    """Carriles que hay que cruzar para llegar a `target_cents` (o la meta)."""
    for lanes in range(1, difficulty.lanes + 1):
        if multiplier_cents(difficulty, lanes) >= target_cents:
            return lanes
    return difficulty.lanes


def auto_targets(difficulty: Difficulty) -> list[int]:
    """Objetivos de autocobro con sentido en esta dificultad (por debajo de la meta)."""
    top = multiplier_cents(difficulty, difficulty.lanes)
    return [t for t in AUTO_TARGETS if t < top]


def draw_hit_lane(difficulty: Difficulty, rng: random.Random) -> int | None:
    """Sortea el primer carril que atropella (1 = el primero); `None` si ninguno.

    Se tira carril a carril, igual que si se sorteara al pisarlo.
    """
    for lane in range(1, difficulty.lanes + 1):
        if rng.random() < difficulty.hit:
            return lane
    return None


class Status(Enum):
    """Estado de una partida."""

    PLAYING = "playing"
    SPLAT = "splat"
    CASHED = "cashed"


class ChickenError(Exception):
    """Acción no válida en la partida; el mensaje se puede enseñar al usuario."""


@dataclass(slots=True)
class ChickenGame:
    """Una partida del Pollo.

    Attributes:
        stake: Lo apostado (ya cobrado por la economía).
        difficulty: Dificultad de la partida.
        hit_lane: Carril donde atropellan (1…lanes), o `None` si la
            carretera está libre hasta la meta. Se sortea al empezar y no se
            enseña hasta acabar.
        crossed: Carriles cruzados.
        auto_lanes: Carriles cruzados con autocobro (para los logros).
        auto_target: Objetivo del último autocobro (centésimas), si hubo.
    """

    stake: int
    difficulty: Difficulty
    hit_lane: int | None
    crossed: int = 0
    status: Status = Status.PLAYING
    auto_lanes: int = 0
    auto_target: int | None = None

    @classmethod
    def new(cls, stake: int, difficulty: Difficulty, rng: random.Random) -> ChickenGame:
        """Empieza una partida y sortea dónde atropellan.

        Raises:
            ValueError: Si la apuesta no es positiva.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        return cls(stake=stake, difficulty=difficulty, hit_lane=draw_hit_lane(difficulty, rng))

    # -- Estado -----------------------------------------------------------------------

    @property
    def playing(self) -> bool:
        """Si se puede seguir cruzando."""
        return self.status is Status.PLAYING

    @property
    def lanes(self) -> int:
        """Carriles hasta la meta."""
        return self.difficulty.lanes

    @property
    def finished_road(self) -> bool:
        """Si llegó a la meta."""
        return self.crossed == self.lanes

    @property
    def cents(self) -> int:
        """Multiplicador actual en centésimas."""
        return multiplier_cents(self.difficulty, self.crossed)

    @property
    def next_cents(self) -> int | None:
        """Multiplicador del siguiente carril; `None` si ya está en la meta."""
        if self.finished_road:
            return None
        return multiplier_cents(self.difficulty, self.crossed + 1)

    @property
    def safe_chance(self) -> Fraction:
        """Probabilidad de cruzar el siguiente carril."""
        return 1 - self.difficulty.hit

    @property
    def cashout_value(self) -> int:
        """Lo que se cobraría ahora mismo."""
        return payout(self.stake, self.difficulty, self.crossed)

    @property
    def next_value(self) -> int | None:
        """Lo que se cobraría tras el siguiente carril."""
        if self.finished_road:
            return None
        return payout(self.stake, self.difficulty, self.crossed + 1)

    @property
    def payout(self) -> int:
        """Lo cobrado al terminar: 0 si le atropellaron."""
        return self.cashout_value if self.status is Status.CASHED else 0

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la partida terminada."""
        return self.payout - self.stake

    @property
    def free_lanes_left(self) -> int | None:
        """Tras cobrar: carriles libres que quedaban antes del coche.

        `None` si la carretera estaba libre hasta la meta. Sirve para el
        «te habrías llevado ×N» al cobrar.
        """
        if self.hit_lane is None:
            return None
        return max(0, self.hit_lane - self.crossed - 1)

    @property
    def missed_cents(self) -> int:
        """Tras cobrar: el mejor multiplicador al que se podía haber llegado."""
        best = self.lanes if self.hit_lane is None else self.hit_lane - 1
        return multiplier_cents(self.difficulty, max(best, self.crossed))

    # -- Acciones ---------------------------------------------------------------------

    def cross(self, *, auto: bool = False) -> bool:
        """Cruza el siguiente carril. Devuelve `True` si sobrevive.

        Si era el último carril no cobra solo: el cog llama a `cash_out`
        justo después (`finished_road`).

        Raises:
            ChickenError: Si la partida terminó.
        """
        if not self.playing or self.finished_road:
            raise ChickenError("La partida ya ha terminado.")
        lane = self.crossed + 1
        if auto:
            self.auto_lanes += 1
        if lane == self.hit_lane:
            self.status = Status.SPLAT
            return False
        self.crossed = lane
        return True

    def cross_until(self, target_cents: int) -> int:
        """Autocobro: cruza hasta llegar a `target_cents`, la meta o el coche.

        No cobra: el cog llama a `cash_out` si sigue vivo. Devuelve los
        carriles que ha intentado cruzar (el del atropello incluido).

        Raises:
            ChickenError: Si la partida terminó o el objetivo ya está alcanzado.
        """
        if not self.playing or self.finished_road:
            raise ChickenError("La partida ya ha terminado.")
        if self.cents >= target_cents:
            raise ChickenError("Ya has pasado ese multiplicador.")
        self.auto_target = target_cents
        hops = 0
        while self.playing and not self.finished_road and self.cents < target_cents:
            hops += 1
            self.cross(auto=True)
        return hops

    def cash_out(self) -> int:
        """Se retira y devuelve lo que cobra.

        Raises:
            ChickenError: Si la partida terminó o aún está en la acera.
        """
        if not self.playing:
            raise ChickenError("La partida ya ha terminado.")
        if not self.crossed:
            raise ChickenError("Cruza al menos un carril antes de cobrar.")
        self.status = Status.CASHED
        return self.cashout_value


# -- Textos de progreso ---------------------------------------------------------------


def milestone(crossed: int, lanes: int) -> str | None:
    """Frase para los carriles que merecen celebrarse; `None` para el resto."""
    if crossed == lanes:
        return "🏁 ¡HA CRUZADO LA AUTOPISTA ENTERA!"
    if crossed == lanes - 1:
        return "😰 Un carril más y es leyenda…"
    if lanes >= 8 and crossed == lanes // 2:
        return "🌓 ¡Media carretera!"
    return {
        3: "🔥 ¡Tres carriles!",
        5: "🔥🔥 ¡Cinco y sin un rasguño!",
        7: "💪 ¡Siete, mi amor!",
        10: "🚀 ¡Diez carriles! La DGT no se lo cree.",
        15: "👑 ¡Quince! Esto es un pollo de leyenda.",
        20: "🤯 ¡VEINTE!",
    }.get(crossed)


def difficulty_summary(difficulty: Difficulty) -> str:
    """Texto de una dificultad en el menú: riesgo y premio de la meta.

    Ejemplo: `40 % por carril · 15 carriles · meta ×2.106`.
    """
    top = multiplier_cents(difficulty, difficulty.lanes)
    whole = format_multiplier(top).split(",")[0]
    return (
        f"{round(float(difficulty.hit) * 100)} % por carril · "
        f"{difficulty.lanes} carriles · meta {whole}"
    )
