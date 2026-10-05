"""Reglas de Minas: un tablero de 5×5 con minas escondidas.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.mines`) cobra al
empezar, paga al retirarse y pinta el tablero con botones.

El jugador elige cuántas minas hay (de 1 a 12) y va destapando casillas.
Cada casilla segura sube el multiplicador; puede cobrar cuando quiera, y si
pisa una mina lo pierde todo.

**La primera casilla siempre es segura**, como en el Buscaminas de Windows:
las minas se colocan, con el azar del sistema operativo, entre las otras 24
casillas justo después del primer clic, y a partir de ahí no se mueven.
Como esa casilla no tiene riesgo, paga ×1 (devuelve la apuesta).

**Por qué como mucho 12 minas.** Limpiar el tablero es acertar dónde están
las `m` minas entre las 24 casillas que quedan tras la segura: una
posibilidad entre C(24, m), y paga 0,99 × C(24, m). Esa cuenta es simétrica
(4 minas y 20 minas tienen la misma probabilidad y el mismo premio) y tiene
el máximo en 12. Con más de 12 el premio gordo vuelve a bajar, así que no
tendría sentido arriesgar más. Hasta 12, cada mina más sube el premio
máximo, y eso es lo que tienta a poner más.

Desde la segunda casilla, el multiplicador es el inverso de la probabilidad
de haber llegado hasta ahí, por el retorno al jugador. Tras `k` casillas
(contando la primera) con `m` minas:

    0,99 × C(24, k − 1) / C(24 − m, k − 1)

Así que cobrar en cualquier momento a partir de la segunda devuelve de media
el 99 % de lo apostado, se juegue como se juegue, y más minas = más riesgo =
más multiplicador por casilla. **No hay tope de multiplicador**: con 12 minas
limpiar el tablero paga ×2.677.114 (1 entre 2.704.156). Un tope igualaría el
premio gordo de varias opciones y haría que arriesgar más no compensara.
Se calcula con fracciones exactas para que el pago no dependa de redondeos
de coma flotante.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

SIZE = 5
TILES = SIZE * SIZE
MIN_MINES = 1
#: A partir de 12 el premio de limpiar el tablero baja (ver la docstring).
MAX_MINES = (TILES - 1) // 2
#: Con 2 minas la mitad de las partidas pasan de 7 casillas.
DEFAULT_MINES = 2
#: Retorno al jugador desde la segunda casilla: 99 %.
RTP = Fraction(99, 100)


def check_mines(mines: int) -> None:
    """Comprueba que el número de minas cabe en el tablero.

    Raises:
        ValueError: Con un mensaje mostrable si no está entre 1 y 12.
    """
    if not MIN_MINES <= mines <= MAX_MINES:
        raise ValueError(f"Las minas van de {MIN_MINES} a {MAX_MINES}.")


def survival(mines: int, revealed: int) -> Fraction:
    """Probabilidad de destapar `revealed` casillas buenas seguidas.

    La primera es segura, así que el riesgo empieza en la segunda.
    """
    if revealed <= 1:
        return Fraction(1)
    others = TILES - 1
    return Fraction(math.comb(others - mines, revealed - 1), math.comb(others, revealed - 1))


def multiplier(mines: int, revealed: int) -> Fraction:
    """Multiplicador exacto tras `revealed` casillas seguras (×1 con 0 o 1).

    Raises:
        ValueError: Si los números no caben en el tablero.
    """
    check_mines(mines)
    if not 0 <= revealed <= TILES - mines:
        raise ValueError("Más casillas seguras de las que hay.")
    if revealed <= 1:
        return Fraction(1)
    return RTP / survival(mines, revealed)


def multiplier_cents(mines: int, revealed: int) -> int:
    """El multiplicador en centésimas, redondeado hacia abajo (para enseñarlo)."""
    return math.floor(multiplier(mines, revealed) * 100)


def payout(stake: int, mines: int, revealed: int) -> int:
    """Lo que cobra quien se retira tras `revealed` casillas (apuesta incluida)."""
    return math.floor(stake * multiplier(mines, revealed))


def format_multiplier(cents: int) -> str:
    """`124` → `×1,24`; los miles llevan punto."""
    whole, frac = divmod(cents, 100)
    return "×" + f"{whole:,}".replace(",", ".") + f",{frac:02d}"


class Status(Enum):
    """Estado de una partida."""

    PLAYING = "playing"
    BUSTED = "busted"
    CASHED = "cashed"


class MinesError(Exception):
    """Acción no válida en la partida; el mensaje se puede enseñar al usuario."""


@dataclass(slots=True)
class MinesGame:
    """Una partida de Minas.

    Attributes:
        stake: Lo apostado (ya cobrado por la economía).
        mine_tiles: Posiciones de las minas (0–24, por filas). Vacío hasta el
            primer clic, que es cuando se colocan.
        revealed: Casillas seguras destapadas, en el orden en que se abrieron.
        exploded: La mina que se pisó, si se pisó alguna.
        random_picks: Cuántas casillas se abrieron con 🎲 (para los logros).
        rng: Azar con el que se colocan las minas al primer clic.
    """

    stake: int
    mines: int
    mine_tiles: frozenset[int] = frozenset()
    revealed: list[int] = field(default_factory=list)
    status: Status = Status.PLAYING
    exploded: int | None = None
    random_picks: int = 0
    rng: random.Random = field(default_factory=random.SystemRandom, repr=False)

    @classmethod
    def new(cls, stake: int, mines: int, rng: random.Random) -> MinesGame:
        """Empieza una partida; las minas se colocan en el primer clic.

        Raises:
            ValueError: Si la apuesta no es positiva o las minas no caben.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        check_mines(mines)
        return cls(stake=stake, mines=mines, rng=rng)

    # -- Estado -----------------------------------------------------------------------

    @property
    def playing(self) -> bool:
        """Si se puede seguir destapando."""
        return self.status is Status.PLAYING

    @property
    def gems(self) -> int:
        """Casillas seguras destapadas."""
        return len(self.revealed)

    @property
    def safe_total(self) -> int:
        """Casillas seguras del tablero."""
        return TILES - self.mines

    @property
    def cleared(self) -> bool:
        """Si ya no queda ninguna casilla segura por destapar."""
        return self.gems == self.safe_total

    @property
    def hidden(self) -> list[int]:
        """Casillas sin destapar."""
        opened = set(self.revealed)
        return [t for t in range(TILES) if t not in opened]

    @property
    def cents(self) -> int:
        """Multiplicador actual en centésimas."""
        return multiplier_cents(self.mines, self.gems)

    @property
    def next_cents(self) -> int | None:
        """Multiplicador si la siguiente casilla es segura; `None` si no quedan."""
        if self.cleared:
            return None
        return multiplier_cents(self.mines, self.gems + 1)

    @property
    def safe_chance(self) -> Fraction:
        """Probabilidad de que la siguiente casilla sea segura (1 en la primera)."""
        if self.gems == 0:
            return Fraction(1)
        hidden = TILES - self.gems
        return Fraction(self.safe_total - self.gems, hidden)

    @property
    def cashout_value(self) -> int:
        """Lo que se cobraría ahora mismo."""
        return payout(self.stake, self.mines, self.gems)

    @property
    def next_value(self) -> int | None:
        """Lo que se cobraría tras la siguiente casilla buena."""
        if self.cleared:
            return None
        return payout(self.stake, self.mines, self.gems + 1)

    @property
    def payout(self) -> int:
        """Lo cobrado al terminar: 0 si explotó."""
        if self.status is Status.CASHED:
            return self.cashout_value
        return 0

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la partida terminada."""
        return self.payout - self.stake

    # -- Acciones ---------------------------------------------------------------------

    def _place_mines(self, first: int) -> None:
        """Coloca las minas entre las casillas que no son la del primer clic."""
        others = [t for t in range(TILES) if t != first]
        self.mine_tiles = frozenset(self.rng.sample(others, self.mines))

    def reveal(self, tile: int, *, random_pick: bool = False) -> bool:
        """Destapa `tile`. Devuelve `True` si era segura.

        En el primer clic coloca las minas, así que siempre es segura. Si era
        la última casilla segura no cobra sola: el cog llama a `cash_out`
        justo después (`cleared`).

        Raises:
            MinesError: Si la partida terminó o la casilla ya estaba abierta.
        """
        if not self.playing:
            raise MinesError("La partida ya ha terminado.")
        if not 0 <= tile < TILES:
            raise MinesError("Esa casilla no existe.")
        if tile in self.revealed:
            raise MinesError("Esa casilla ya está destapada.")
        if not self.mine_tiles:
            self._place_mines(tile)
        if random_pick:
            self.random_picks += 1
        if tile in self.mine_tiles:
            self.status = Status.BUSTED
            self.exploded = tile
            return False
        self.revealed.append(tile)
        return True

    def cash_out(self) -> int:
        """Se retira y devuelve lo que cobra.

        Raises:
            MinesError: Si la partida terminó o no hay ninguna casilla abierta.
        """
        if not self.playing:
            raise MinesError("La partida ya ha terminado.")
        if not self.gems:
            raise MinesError("Destapa al menos una casilla antes de cobrar.")
        self.status = Status.CASHED
        return self.cashout_value

    def random_hidden(self, rng: random.Random) -> int:
        """Una casilla cerrada al azar (para 🎲).

        Raises:
            MinesError: Si la partida terminó.
        """
        if not self.playing:
            raise MinesError("La partida ya ha terminado.")
        return rng.choice(self.hidden)


# -- Mensajes de progreso -----------------------------------------------------------------


def milestone(gems: int, safe_total: int) -> str | None:
    """Frase para las casillas que merecen celebrarse; `None` para el resto.

    Se dicen al llegar, no después, para que avanzar se note casilla a casilla.
    """
    if gems == safe_total:
        return "🏁 ¡TABLERO LIMPIO!"
    if gems == safe_total - 1 and gems > 1:
        return "😰 Solo queda una buena…"
    if safe_total >= 6 and gems == (safe_total + 1) // 2:
        return "🌓 ¡Medio tablero!"
    return {
        3: "🔥 ¡Tres limpias!",
        5: "🔥🔥 ¡Racha de cinco!",
        7: "💪 ¡Siete, mi amor!",
        10: "🚀 ¡Diez casillas!",
        15: "👑 ¡Quince! Esto ya es leyenda.",
        20: "🤯 ¡VEINTE!",
    }.get(gems)


def risk_summary(mines: int) -> str:
    """Texto de una opción de minas en el menú: el premio de limpiar el tablero.

    Solo el premio gordo, sin decimales, porque es lo que crece con cada mina
    y lo que hace tentador subir el riesgo. Ejemplo con 4 minas:
    `Limpias el tablero: ×10.519`.
    """
    whole = format_multiplier(multiplier_cents(mines, TILES - mines)).split(",")[0]
    return f"Limpias el tablero: {whole}"
