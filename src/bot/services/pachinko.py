"""Pachinko: tablero de clavos, bolsillos, sorteo digital, reach y rush.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.pachinko`) cobra y paga
con `EconomyService.settle_bet` y pinta el resultado con
`bot.services.pachinko_render`.

Cómo funciona una tanda (cada vez que se pulsa 🎯 Lanzar):

1. Caen `BALLS` bolas. Cada una rebota en `ROWS` filas de clavos y en cada
   clavo va a la izquierda o a la derecha a cara o cruz. El bolsillo final es
   el número de veces que ha ido a la derecha (un tablero de Galton): los del
   centro se llenan mucho y los de las esquinas casi nunca.
2. Cada bolsillo devuelve bolas (`POCKETS`). El del centro es la ranura
   **START**: no devuelve nada, pero cada bola que entra gana una tirada del
   sorteo de la pantalla. Como en las máquinas reales, se guardan como mucho
   `MAX_HOLD` tiradas en reserva (保留, *horyū*); las bolas que entran con la
   reserva llena se pierden.
3. El sorteo saca tres números del 1 al 9. Tres iguales es **ATARI**
   (大当り, premio gordo): se abre la compuerta y entran `FEVER_BALLS` bolas.
   Si el número es impar, el atari trae un **RUSH** (確変, *kakuhen*): se
   encadenan más ataris mientras una moneda trucada siga saliendo cara. Con el
   7 es el **SUPER RUSH**, con más probabilidad de seguir y más tope.
4. Cuando el sorteo no toca, a veces enseña un **REACH** (dos números iguales
   y el del centro girando): es solo espectáculo, no cambia la probabilidad.

Todo lo que paga se cuenta en bolas. Una bola vale `apuesta / BALLS`.

Números de la tabla actual (calculados en `tests/unit/test_pachinko_service.py`):

- Devuelve un 94,6 % de lo apostado (la ruleta americana, 94,7 %): 57,6 %
  sale de los bolsillos y 37,0 %, de los ataris.
- Un atari cada ~17 tandas. Cada atari paga de media 2,1 premios gordos.
- En el 99,9 % de las tandas cae algo en un bolsillo, pero solo una de cada
  diez devuelve lo apostado o más: la máquina tintinea y aun así pierdes.
  El dinero de verdad está en los ataris.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import comb

# -- Tablero ------------------------------------------------------------------------

#: Filas de clavos. Con 10 filas hay 11 bolsillos.
ROWS = 10
#: Bolas por tanda; lo apostado se reparte entre ellas.
BALLS = 10
#: Bolsillo de la ranura START (el del centro).
START_POCKET = ROWS // 2
#: Bolas que devuelve cada bolsillo, de izquierda a derecha. El START no
#: devuelve nada: paga con tiradas del sorteo.
POCKETS: tuple[int, ...] = (10, 3, 1, 0, 1, 0, 1, 0, 1, 3, 10)
#: Tiradas del sorteo que se pueden guardar a la vez.
MAX_HOLD = 4
#: Apuesta mínima: una bola tiene que valer al menos 1 Y$.
MIN_STAKE = BALLS

# -- Sorteo -------------------------------------------------------------------------

#: Probabilidad de atari por tirada del sorteo: `(numerador, denominador)`.
ATARI_CHANCE = (1, 40)
#: Bolas que paga cada premio gordo (un atari o cada vuelta de un rush).
FEVER_BALLS = 30
#: Probabilidad de que el rush encadene otro premio y vueltas extra como
#: mucho: `(numerador, denominador, tope)`. El rush normal sale con los
#: impares menos el 7; el super rush, con el 7. Los pares pagan un solo premio.
RUSH = (3, 5, 9)
SUPER_RUSH = (4, 5, 14)
SUPER_DIGIT = 7
#: Probabilidad de enseñar un reach falso cuando el sorteo no toca.
FAKE_REACH_CHANCE = (1, 6)


class Kind:
    """Tipo de tirada del sorteo; texto estable para la presentación y los logros."""

    MISS = "miss"
    #: Atari con número par: un premio gordo.
    ATARI = "atari"
    #: Atari impar (menos el 7): rush.
    RUSH = "rush"
    #: Atari con el 7: super rush.
    SUPER = "super"


@dataclass(frozen=True, slots=True)
class Ball:
    """Una bola: hacia dónde rebota en cada fila de clavos.

    Attributes:
        path: Un 0 (izquierda) o un 1 (derecha) por fila.
    """

    path: tuple[int, ...]

    @property
    def pocket(self) -> int:
        """Bolsillo en el que cae: las veces que ha ido a la derecha."""
        return sum(self.path)

    @property
    def returned(self) -> int:
        """Bolas que devuelve su bolsillo."""
        return POCKETS[self.pocket]


@dataclass(frozen=True, slots=True)
class Draw:
    """Una tirada del sorteo de la pantalla.

    Attributes:
        digits: Los tres números, de izquierda a derecha.
        kind: `Kind`.
        reach: Si los dos de los lados coinciden (siempre en un atari).
        jackpots: Premios gordos que paga: 0 si no toca, 1 en un atari par y
            1 o más en un rush.
    """

    digits: tuple[int, int, int]
    kind: str
    reach: bool
    jackpots: int

    @property
    def atari(self) -> bool:
        """Si ha tocado."""
        return self.jackpots > 0


@dataclass(frozen=True, slots=True)
class Volley:
    """Una tanda entera: las bolas, el sorteo y lo que devuelve, sin dinero.

    Attributes:
        balls: Las `BALLS` bolas, en el orden en que se lanzan.
        draws: Tiradas del sorteo, en orden (las de la reserva).
        wasted: Bolas que entraron en START con la reserva llena.
    """

    balls: tuple[Ball, ...]
    draws: tuple[Draw, ...]
    wasted: int

    @property
    def pocket_balls(self) -> int:
        """Bolas devueltas por los bolsillos."""
        return sum(ball.returned for ball in self.balls)

    @property
    def jackpots(self) -> int:
        """Premios gordos de toda la tanda."""
        return sum(draw.jackpots for draw in self.draws)

    @property
    def fever_balls(self) -> int:
        """Bolas que ha pagado la compuerta en los ataris."""
        return self.jackpots * FEVER_BALLS

    @property
    def total_balls(self) -> int:
        """Todas las bolas devueltas."""
        return self.pocket_balls + self.fever_balls

    @property
    def starts(self) -> int:
        """Bolas que entraron por START (con la reserva llena o no)."""
        return sum(1 for ball in self.balls if ball.pocket == START_POCKET)

    @property
    def best(self) -> Draw | None:
        """La tirada que más paga, si alguna paga."""
        paying = [draw for draw in self.draws if draw.atari]
        return max(paying, key=lambda draw: draw.jackpots) if paying else None

    @property
    def corners(self) -> int:
        """Bolas en los bolsillos de las esquinas (los que más pagan)."""
        return sum(1 for ball in self.balls if ball.pocket in (0, ROWS))

    def pocket_counts(self) -> list[int]:
        """Cuántas bolas han caído en cada bolsillo."""
        counts = [0] * (ROWS + 1)
        for ball in self.balls:
            counts[ball.pocket] += 1
        return counts


def payout(volley: Volley, stake: int) -> int:
    """Lo que devuelve una tanda en yapdollars (apuesta incluida).

    Se calcula sobre el total de bolas y se redondea hacia abajo una sola vez,
    así no se pierden céntimos por bola.
    """
    return stake * volley.total_balls // BALLS


# -- Sorteo y tanda al azar ---------------------------------------------------------


Randbelow = Callable[[int], int]


def _chance(randbelow: Randbelow, chance: tuple[int, int]) -> bool:
    numerator, denominator = chance
    return randbelow(denominator) < numerator


def draw_lottery(randbelow: Randbelow) -> Draw:
    """Una tirada del sorteo.

    Primero se decide si toca y después cómo se enseña: los números de un
    fallo y el reach falso no influyen en el resultado.
    """
    if _chance(randbelow, ATARI_CHANCE):
        digit = randbelow(9) + 1
        if digit % 2 == 0:
            return Draw((digit, digit, digit), Kind.ATARI, True, 1)
        numerator, denominator, cap = SUPER_RUSH if digit == SUPER_DIGIT else RUSH
        jackpots = 1
        while jackpots <= cap and _chance(randbelow, (numerator, denominator)):
            jackpots += 1
        kind = Kind.SUPER if digit == SUPER_DIGIT else Kind.RUSH
        return Draw((digit, digit, digit), kind, True, jackpots)

    side = randbelow(9) + 1
    if _chance(randbelow, FAKE_REACH_CHANCE):
        # Reach falso: el del centro para justo al lado, el casi más cruel.
        center = side % 9 + 1 if randbelow(2) else (side - 2) % 9 + 1
        return Draw((side, center, side), Kind.MISS, True, 0)
    # Sin reach: el de la derecha nunca coincide con el de la izquierda.
    right = (side + randbelow(8)) % 9 + 1
    center = randbelow(9) + 1
    return Draw((side, center, right), Kind.MISS, False, 0)


def build_volley(balls: Sequence[Ball], draw: Callable[[], Draw]) -> Volley:
    """Monta la tanda: reparte las bolas de START entre la reserva y el sorteo.

    La reserva se va gastando mientras caen bolas: cada bola que entra en
    START con hueco en la reserva gana una tirada. Aquí se simplifica a que
    caben `MAX_HOLD` por tanda (lo que se ve en la pantalla), así que con
    cinco o más bolas en START se pierden las que sobran.
    """
    starts = sum(1 for ball in balls if ball.pocket == START_POCKET)
    held = min(starts, MAX_HOLD)
    draws = tuple(draw() for _ in range(held))
    return Volley(balls=tuple(balls), draws=draws, wasted=starts - held)


class PachinkoMachine:
    """Lanza tandas al azar.

    Args:
        randbelow: Devuelve un entero en `[0, n)`; inyectable en pruebas.
    """

    def __init__(self, randbelow: Randbelow | None = None) -> None:
        self._randbelow = randbelow or random.SystemRandom().randrange

    def ball(self) -> Ball:
        """Una bola con un rebote al azar por fila."""
        return Ball(tuple(self._randbelow(2) for _ in range(ROWS)))

    def launch(self) -> Volley:
        """Una tanda completa."""
        balls = [self.ball() for _ in range(BALLS)]
        return build_volley(balls, lambda: draw_lottery(self._randbelow))


# -- Números exactos ----------------------------------------------------------------


def pocket_probability(pocket: int) -> Fraction:
    """Probabilidad de que una bola caiga en `pocket`: C(ROWS, k) / 2^ROWS."""
    return Fraction(comb(ROWS, pocket), 2**ROWS)


def _rush_expectation(numerator: int, denominator: int, cap: int) -> Fraction:
    """Premios gordos esperados de un rush: 1 + p + p² + … + p^tope."""
    p = Fraction(numerator, denominator)
    return sum((p**i for i in range(cap + 1)), Fraction(0))


def jackpots_per_atari() -> Fraction:
    """Premios gordos de media por cada atari."""
    ninth = Fraction(1, 9)
    evens = 4 * ninth
    rushes = 4 * ninth * _rush_expectation(*RUSH)
    supers = ninth * _rush_expectation(*SUPER_RUSH)
    return evens + rushes + supers


def _held_distribution() -> list[Fraction]:
    """Probabilidad de tener 0..MAX_HOLD tiradas del sorteo en una tanda."""
    s = pocket_probability(START_POCKET)
    held = [Fraction(0)] * (MAX_HOLD + 1)
    for n in range(BALLS + 1):
        held[min(n, MAX_HOLD)] += comb(BALLS, n) * s**n * (1 - s) ** (BALLS - n)
    return held


def pocket_return() -> Fraction:
    """Parte de lo apostado que devuelven los bolsillos."""
    return sum((pocket_probability(k) * POCKETS[k] for k in range(ROWS + 1)), Fraction(0))


def lottery_return() -> Fraction:
    """Parte de lo apostado que devuelven los ataris."""
    expected_draws = sum((n * p for n, p in enumerate(_held_distribution())), Fraction(0))
    q = Fraction(*ATARI_CHANCE)
    return expected_draws * q * jackpots_per_atari() * FEVER_BALLS / BALLS


def expected_return() -> Fraction:
    """Retorno total, sin contar el redondeo de céntimos."""
    return pocket_return() + lottery_return()


def atari_volley_chance() -> Fraction:
    """Probabilidad de que una tanda tenga al menos un atari."""
    miss = 1 - Fraction(*ATARI_CHANCE)
    return 1 - sum((p * miss**n for n, p in enumerate(_held_distribution())), Fraction(0))


# -- Presentación -------------------------------------------------------------------


def pocket_label(pocket: int) -> str:
    """Texto del bolsillo para el tablero y la tabla de premios."""
    if pocket == START_POCKET:
        return "START"
    value = POCKETS[pocket]
    return f"×{value}" if value else "OUT"


def hold_bar(held: int) -> str:
    """Reserva con formas, no solo colores: `●●●○`."""
    held = max(0, min(MAX_HOLD, held))
    return "●" * held + "○" * (MAX_HOLD - held)


def paytable_lines() -> list[str]:
    """Tabla de premios para enseñar al jugador."""
    corner, side, small = POCKETS[0], POCKETS[1], POCKETS[2]
    rush_cap = RUSH[2] + 1
    super_cap = SUPER_RUSH[2] + 1
    return [
        f"🎯 Cada tanda lanza **{BALLS} bolas**. Una bola vale la apuesta entre {BALLS}.",
        f"⬇️ Esquinas · ×{corner} bolas · lados · ×{side} · el resto · ×{small} o nada",
        f"🌀 **START** (el centro) · una tirada en la pantalla. Reserva: {MAX_HOLD}.",
        f"🎰 Tres iguales · **ATARI** · +{FEVER_BALLS} bolas por premio",
        f"🔥 Impar · **RUSH** · encadena premios ({RUSH[0]}/{RUSH[1]} de seguir, hasta {rush_cap})",
        f"7️⃣ El 7 · **SUPER RUSH** · {SUPER_RUSH[0]}/{SUPER_RUSH[1]} de seguir, hasta {super_cap}",
        "👀 El **REACH** es espectáculo: no cambia lo que toca.",
    ]
