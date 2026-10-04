"""Reglas de Minas: un tablero de 5×5 con minas escondidas.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.mines`) cobra al
empezar, paga al retirarse y pinta el tablero con botones.

El jugador elige cuántas minas hay (`MINE_CHOICES`) y va destapando
casillas. Cada casilla segura sube el multiplicador; puede cobrar cuando
quiera, y si pisa una mina lo pierde todo. Las minas se colocan al empezar
con el azar del sistema operativo, antes del primer clic, y no se mueven.

El multiplicador tras `k` casillas seguras con `m` minas es el inverso de
la probabilidad de haber llegado hasta ahí, por el retorno al jugador:

    0,99 × C(25, k) / C(25 − m, k)

Así que cobrar en cualquier momento devuelve de media el 99 % de lo
apostado, se juegue como se juegue. Se calcula con fracciones exactas
para que el pago no dependa de redondeos de coma flotante.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

SIZE = 5
TILES = SIZE * SIZE
#: Minas que se pueden elegir. El botón 💣 las recorre en este orden.
MINE_CHOICES: tuple[int, ...] = (1, 3, 5, 10, 15, 20, 24)
DEFAULT_MINES = 3
#: Retorno al jugador: 99 %.
RTP = Fraction(99, 100)
#: Tope del multiplicador (×10.000). Con muchas minas la fórmula da millones;
#: el tope casi no cambia el retorno (hace falta muchísima suerte para
#: llegar) y evita que una partida rompa la economía del servidor.
MAX_MULTIPLIER = Fraction(10_000)


def multiplier(mines: int, revealed: int) -> Fraction:
    """Multiplicador exacto tras `revealed` casillas seguras (1 si ninguna).

    Raises:
        ValueError: Si los números no caben en el tablero.
    """
    if not 1 <= mines < TILES:
        raise ValueError("Número de minas fuera de rango.")
    if not 0 <= revealed <= TILES - mines:
        raise ValueError("Más casillas seguras de las que hay.")
    if revealed == 0:
        return Fraction(1)
    fair = Fraction(math.comb(TILES, revealed), math.comb(TILES - mines, revealed))
    return min(RTP * fair, MAX_MULTIPLIER)


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


def next_mine_choice(mines: int) -> int:
    """La siguiente opción de minas tras `mines`, volviendo a la primera."""
    if mines not in MINE_CHOICES:
        return DEFAULT_MINES
    index = MINE_CHOICES.index(mines)
    return MINE_CHOICES[(index + 1) % len(MINE_CHOICES)]


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
        mine_tiles: Posiciones de las minas (0–24, por filas).
        revealed: Casillas seguras destapadas, en el orden en que se abrieron.
        exploded: La mina que se pisó, si se pisó alguna.
        random_picks: Cuántas casillas se abrieron con 🎲 (para los logros).
    """

    stake: int
    mines: int
    mine_tiles: frozenset[int]
    revealed: list[int] = field(default_factory=list)
    status: Status = Status.PLAYING
    exploded: int | None = None
    random_picks: int = 0

    @classmethod
    def new(cls, stake: int, mines: int, rng: random.Random) -> MinesGame:
        """Coloca las minas al azar y empieza la partida.

        Raises:
            ValueError: Si la apuesta no es positiva o las minas no son válidas.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        if mines not in MINE_CHOICES:
            raise ValueError("Número de minas no permitido.")
        return cls(stake=stake, mines=mines, mine_tiles=frozenset(rng.sample(range(TILES), mines)))

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
        """Probabilidad de que la siguiente casilla sea segura."""
        hidden = TILES - self.gems
        return Fraction(self.safe_total - self.gems, hidden)

    @property
    def cashout_value(self) -> int:
        """Lo que se cobraría ahora mismo."""
        return payout(self.stake, self.mines, self.gems)

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

    def reveal(self, tile: int, *, random_pick: bool = False) -> bool:
        """Destapa `tile`. Devuelve `True` si era segura.

        Si era la última casilla segura no cobra sola: el cog llama a
        `cash_out` justo después (`cleared`).

        Raises:
            MinesError: Si la partida terminó o la casilla ya estaba abierta.
        """
        if not self.playing:
            raise MinesError("La partida ya ha terminado.")
        if not 0 <= tile < TILES:
            raise MinesError("Esa casilla no existe.")
        if tile in self.revealed:
            raise MinesError("Esa casilla ya está destapada.")
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
