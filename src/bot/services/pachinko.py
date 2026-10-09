"""Pachinko: tableros de clavos, bolsillos, sorteo digital, reach y rush.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.pachinko`) cobra y paga
con `EconomyService.settle_bet` y pinta el resultado con
`bot.services.pachinko_render`.

Cómo funciona una tanda (cada vez que se pulsa 🎯 Lanzar):

1. Caen `BALLS` bolas. Cada una rebota en las filas de clavos del tablero y
   en cada clavo va a la izquierda o a la derecha a cara o cruz. El bolsillo
   final es el número de veces que ha ido a la derecha (un tablero de
   Galton): los del centro se llenan mucho y los de las esquinas casi nunca.
2. Cada bolsillo devuelve bolas (`Board.pockets`). El del centro es la
   ranura **START**: no devuelve nada, pero cada bola que entra gana una
   tirada del sorteo de la pantalla. Como en las máquinas reales, se guardan
   como mucho `MAX_HOLD` tiradas en reserva (保留, *horyū*); las bolas que
   entran con la reserva llena se pierden.
3. El sorteo saca tres números del 1 al 9. Tres iguales es **ATARI**
   (大当り, premio gordo): se abre la compuerta y entran `Board.fever_balls`
   bolas. Si el número es impar, el atari trae un **RUSH** (確変, *kakuhen*):
   se encadenan más premios mientras una moneda trucada siga saliendo cara.
   Con el 7 es el **SUPER RUSH**, con más probabilidad de seguir y más tope.
4. Cuando el sorteo no toca, a veces enseña un **REACH** (dos números iguales
   y el del centro girando): es solo espectáculo, no cambia la probabilidad.

Todo lo que paga se cuenta en bolas. Una bola vale `apuesta / BALLS`.

La caída que se ve en el GIF no decide nada: sale de la biblioteca de
`bot.services.pachinko_physics` (caídas con gravedad y rebotes simuladas de
antemano), de las que cada bola usa una que acaba en el bolsillo ya sorteado.

Hay cuatro tableros (`BOARDS`). Todos devuelven lo mismo de media, entre el
94 y el 95 % (la ruleta americana, 94,7 %), y lo que cambia es el riesgo:
cuánto sale de los bolsillos (poco a poco) y cuánto de los ataris (de golpe).
Números calculados con fracciones exactas en `tests/unit/test_pachinko_service.py`:

| Tablero | Filas | Retorno | De los bolsillos | Un atari cada | Atari medio |
|---|---|---|---|---|---|
| 🌸 Sakura | 8 | 95,2 % | 66,4 % | 7 tandas | ×1,8 |
| 🏮 Clásica | 10 | 94,6 % | 57,6 % | 17 tandas | ×6,3 |
| 🐉 Dragón | 10 | 94,4 % | 26,4 % | 22 tandas | ×14,4 |
| 👹 Oni | 12 | 94,3 % | 17,2 % | 39 tandas | ×29,6 |

("Atari medio" es lo que paga de media cada atari, en veces la apuesta.)
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import comb

from bot.services.pachinko_physics import TRAJECTORIES_PER_POCKET

#: Bolas por tanda; lo apostado se reparte entre ellas.
BALLS = 10
#: Tiradas del sorteo que se pueden guardar a la vez.
MAX_HOLD = 4
#: Apuesta mínima: una bola tiene que valer al menos 1 Y$.
MIN_STAKE = BALLS
#: Número del super rush.
SUPER_DIGIT = 7


# -- Tableros -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Board:
    """Un tablero (mapa) de pachinko: su forma, lo que paga y cómo se llama.

    Attributes:
        key: Identificador estable; se guarda en logros y se escribe en
            `.pachinko 500 oni`.
        risk: Texto corto del riesgo para el menú (`"riesgo bajo"`).
        rows: Filas de clavos (par, para que haya un bolsillo en el centro).
        pockets: Bolas que devuelve cada bolsillo, de izquierda a derecha. El
            del centro (START) no devuelve nada: paga con tiradas del sorteo.
        atari_chance: Probabilidad de atari por tirada: `(num, den)`.
        fever_balls: Bolas por premio gordo (un atari o cada vuelta de rush).
        rush: Rush de los impares menos el 7: `(num, den, vueltas extra
            como mucho)`. Sigue con probabilidad `num/den`.
        super_rush: Igual, para el 7.
        fake_reach: Probabilidad de enseñar un reach cuando no toca.
    """

    key: str
    name: str
    emoji: str
    risk: str
    blurb: str
    rows: int
    pockets: tuple[int, ...]
    atari_chance: tuple[int, int]
    fever_balls: int
    rush: tuple[int, int, int]
    super_rush: tuple[int, int, int]
    fake_reach: tuple[int, int] = (1, 6)

    @property
    def start_pocket(self) -> int:
        """Bolsillo de la ranura START (el del centro)."""
        return self.rows // 2

    @property
    def title(self) -> str:
        """`🌸 Sakura`."""
        return f"{self.emoji} {self.name}"

    def pocket_label(self, pocket: int) -> str:
        """Texto del bolsillo para el tablero y la tabla de premios."""
        if pocket == self.start_pocket:
            return "START"
        value = self.pockets[pocket]
        return f"×{value}" if value else "OUT"


SAKURA = Board(
    key="sakura",
    name="Sakura",
    emoji="🌸",
    risk="riesgo bajo",
    blurb="8 filas. Los bolsillos pagan a menudo y los ataris caen cada pocas tandas.",
    rows=8,
    pockets=(5, 3, 2, 0, 0, 0, 2, 3, 5),
    atari_chance=(1, 16),
    fever_balls=11,
    rush=(1, 2, 4),
    super_rush=(2, 3, 6),
)
CLASSIC = Board(
    key="clasica",
    name="Clásica",
    emoji="🏮",
    risk="riesgo medio",
    blurb="10 filas. La de siempre: un poco de bolsillos y un poco de compuerta.",
    rows=10,
    pockets=(10, 3, 1, 0, 1, 0, 1, 0, 1, 3, 10),
    atari_chance=(1, 40),
    fever_balls=30,
    rush=(3, 5, 9),
    super_rush=(4, 5, 14),
)
DRAGON = Board(
    key="dragon",
    name="Dragón",
    emoji="🐉",
    risk="riesgo alto",
    blurb="10 filas. Casi todo OUT, esquinas de ×50 y ataris que pagan el doble.",
    rows=10,
    pockets=(50, 4, 1, 0, 0, 0, 0, 0, 1, 4, 50),
    atari_chance=(1, 50),
    fever_balls=69,
    rush=(3, 5, 9),
    super_rush=(4, 5, 14),
)
ONI = Board(
    key="oni",
    name="Oni",
    emoji="👹",
    risk="riesgo extremo",
    blurb="12 filas. Los bolsillos no dan nada; un atari paga ×30 de media y un "
    "super rush puede encadenar 25.",
    rows=12,
    pockets=(100, 10, 2, 0, 0, 0, 0, 0, 0, 0, 2, 10, 100),
    atari_chance=(1, 84),
    fever_balls=100,
    rush=(3, 4, 14),
    super_rush=(6, 7, 24),
)

#: Tableros en el orden del menú, de menos a más riesgo.
BOARDS: dict[str, Board] = {b.key: b for b in (SAKURA, CLASSIC, DRAGON, ONI)}
DEFAULT_BOARD = CLASSIC.key
#: Mayor racha posible en cualquier tablero (para logros y textos).
MAX_JACKPOTS = max(b.super_rush[2] + 1 for b in BOARDS.values())

_ALIASES = {"clasico": "clasica", "classic": "clasica", "dragon": "dragon", "dragón": "dragon"}


def find_board(text: str) -> Board | None:
    """Tablero por su nombre, sin tildes ni mayúsculas (`Dragón`, `oni`…)."""
    key = text.strip().lower()
    key = _ALIASES.get(key, key).replace("á", "a").replace("ó", "o")
    return BOARDS.get(key)


# -- Bolas, sorteo y tanda ----------------------------------------------------------


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
        trajectory: Qué caída de la biblioteca de su bolsillo se ve
            (`pachinko_physics.library`); solo afecta al dibujo, nunca al pago.
    """

    path: tuple[int, ...]
    trajectory: int = 0

    @property
    def pocket(self) -> int:
        """Bolsillo en el que cae: las veces que ha ido a la derecha."""
        return sum(self.path)


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
    """Una tanda entera en un tablero: las bolas, el sorteo y lo que devuelve.

    Attributes:
        board: Tablero en el que se ha jugado.
        balls: Las `BALLS` bolas, en el orden en que se lanzan.
        draws: Tiradas del sorteo, en orden (las de la reserva).
        wasted: Bolas que entraron en START con la reserva llena.
    """

    board: Board
    balls: tuple[Ball, ...]
    draws: tuple[Draw, ...]
    wasted: int

    def returned(self, ball: Ball) -> int:
        """Bolas que devuelve el bolsillo de `ball` en este tablero."""
        return self.board.pockets[ball.pocket]

    @property
    def pocket_balls(self) -> int:
        """Bolas devueltas por los bolsillos."""
        return sum(self.returned(ball) for ball in self.balls)

    @property
    def jackpots(self) -> int:
        """Premios gordos de toda la tanda."""
        return sum(draw.jackpots for draw in self.draws)

    @property
    def fever_balls(self) -> int:
        """Bolas que ha pagado la compuerta en los ataris."""
        return self.jackpots * self.board.fever_balls

    @property
    def total_balls(self) -> int:
        """Todas las bolas devueltas."""
        return self.pocket_balls + self.fever_balls

    @property
    def starts(self) -> int:
        """Bolas que entraron por START (con la reserva llena o no)."""
        return sum(1 for ball in self.balls if ball.pocket == self.board.start_pocket)

    @property
    def best(self) -> Draw | None:
        """La tirada que más paga, si alguna paga."""
        paying = [draw for draw in self.draws if draw.atari]
        return max(paying, key=lambda draw: draw.jackpots) if paying else None

    @property
    def corners(self) -> int:
        """Bolas en los bolsillos de las esquinas (los que más pagan)."""
        return sum(1 for ball in self.balls if ball.pocket in (0, self.board.rows))

    def pocket_counts(self) -> list[int]:
        """Cuántas bolas han caído en cada bolsillo."""
        counts = [0] * (self.board.rows + 1)
        for ball in self.balls:
            counts[ball.pocket] += 1
        return counts


def payout(volley: Volley, stake: int) -> int:
    """Lo que devuelve una tanda en yapdollars (apuesta incluida).

    Se calcula sobre el total de bolas y se redondea hacia abajo una sola vez,
    así no se pierden céntimos por bola.
    """
    return stake * volley.total_balls // BALLS


Randbelow = Callable[[int], int]


def _chance(randbelow: Randbelow, chance: tuple[int, int]) -> bool:
    numerator, denominator = chance
    return randbelow(denominator) < numerator


def draw_lottery(board: Board, randbelow: Randbelow) -> Draw:
    """Una tirada del sorteo en `board`.

    Primero se decide si toca y después cómo se enseña: los números de un
    fallo y el reach falso no influyen en el resultado.
    """
    if _chance(randbelow, board.atari_chance):
        digit = randbelow(9) + 1
        if digit % 2 == 0:
            return Draw((digit, digit, digit), Kind.ATARI, True, 1)
        numerator, denominator, cap = board.super_rush if digit == SUPER_DIGIT else board.rush
        jackpots = 1
        while jackpots <= cap and _chance(randbelow, (numerator, denominator)):
            jackpots += 1
        kind = Kind.SUPER if digit == SUPER_DIGIT else Kind.RUSH
        return Draw((digit, digit, digit), kind, True, jackpots)

    side = randbelow(9) + 1
    if _chance(randbelow, board.fake_reach):
        # Reach falso: el del centro para justo al lado, el casi más cruel.
        center = side % 9 + 1 if randbelow(2) else (side - 2) % 9 + 1
        return Draw((side, center, side), Kind.MISS, True, 0)
    # Sin reach: el de la derecha nunca coincide con el de la izquierda.
    right = (side + randbelow(8)) % 9 + 1
    center = randbelow(9) + 1
    return Draw((side, center, right), Kind.MISS, False, 0)


def build_volley(board: Board, balls: Sequence[Ball], draw: Callable[[], Draw]) -> Volley:
    """Monta la tanda: reparte las bolas de START entre la reserva y el sorteo.

    Caben `MAX_HOLD` tiradas por tanda (lo que se ve en la pantalla), así que
    con cinco o más bolas en START se pierden las que sobran.

    Raises:
        ValueError: Si alguna bola no tiene una fila por cada fila del tablero.
    """
    if any(len(ball.path) != board.rows for ball in balls):
        raise ValueError("Cada bola necesita un rebote por fila del tablero.")
    starts = sum(1 for ball in balls if ball.pocket == board.start_pocket)
    held = min(starts, MAX_HOLD)
    draws = tuple(draw() for _ in range(held))
    return Volley(board=board, balls=tuple(balls), draws=draws, wasted=starts - held)


class PachinkoMachine:
    """Lanza tandas al azar.

    Args:
        randbelow: Devuelve un entero en `[0, n)`; inyectable en pruebas. Decide
            todo lo que cuenta: tablero, rebotes y sorteo.
        pick_trajectory: Lo mismo, solo para elegir la caída que se ve de cada
            bola. Va aparte para que forzar el azar del juego en las pruebas no
            dependa de cuántas caídas hay y para que el dinero no la use nunca.
    """

    def __init__(
        self, randbelow: Randbelow | None = None, pick_trajectory: Randbelow | None = None
    ) -> None:
        self._randbelow = randbelow or random.SystemRandom().randrange
        self._pick_trajectory = pick_trajectory or random.SystemRandom().randrange

    def random_board(self) -> Board:
        """Un tablero al azar (la opción 🎲 del menú)."""
        boards = list(BOARDS.values())
        return boards[self._randbelow(len(boards))]

    def ball(self, board: Board) -> Ball:
        """Una bola con un rebote al azar por fila y una caída de la biblioteca."""
        path = tuple(self._randbelow(2) for _ in range(board.rows))
        return Ball(path, self._pick_trajectory(TRAJECTORIES_PER_POCKET))

    def launch(self, board: Board) -> Volley:
        """Una tanda completa en `board`."""
        balls = [self.ball(board) for _ in range(BALLS)]
        return build_volley(board, balls, lambda: draw_lottery(board, self._randbelow))


# -- Números exactos ----------------------------------------------------------------


def pocket_probability(board: Board, pocket: int) -> Fraction:
    """Probabilidad de que una bola caiga en `pocket`: C(filas, k) / 2^filas."""
    return Fraction(comb(board.rows, pocket), 2**board.rows)


def _rush_expectation(numerator: int, denominator: int, cap: int) -> Fraction:
    """Premios gordos esperados de un rush: 1 + p + p² + … + p^tope."""
    p = Fraction(numerator, denominator)
    return sum((p**i for i in range(cap + 1)), Fraction(0))


def jackpots_per_atari(board: Board) -> Fraction:
    """Premios gordos de media por cada atari."""
    ninth = Fraction(1, 9)
    evens = 4 * ninth
    rushes = 4 * ninth * _rush_expectation(*board.rush)
    supers = ninth * _rush_expectation(*board.super_rush)
    return evens + rushes + supers


def _held_distribution(board: Board) -> list[Fraction]:
    """Probabilidad de tener 0..MAX_HOLD tiradas del sorteo en una tanda."""
    s = pocket_probability(board, board.start_pocket)
    held = [Fraction(0)] * (MAX_HOLD + 1)
    for n in range(BALLS + 1):
        held[min(n, MAX_HOLD)] += comb(BALLS, n) * s**n * (1 - s) ** (BALLS - n)
    return held


def pocket_return(board: Board) -> Fraction:
    """Parte de lo apostado que devuelven los bolsillos."""
    return sum(
        (pocket_probability(board, k) * board.pockets[k] for k in range(board.rows + 1)),
        Fraction(0),
    )


def lottery_return(board: Board) -> Fraction:
    """Parte de lo apostado que devuelven los ataris."""
    held = _held_distribution(board)
    expected_draws = sum((n * p for n, p in enumerate(held)), Fraction(0))
    q = Fraction(*board.atari_chance)
    return expected_draws * q * jackpots_per_atari(board) * board.fever_balls / BALLS


def expected_return(board: Board) -> Fraction:
    """Retorno total, sin contar el redondeo de céntimos."""
    return pocket_return(board) + lottery_return(board)


def atari_volley_chance(board: Board) -> Fraction:
    """Probabilidad de que una tanda tenga al menos un atari."""
    miss = 1 - Fraction(*board.atari_chance)
    held = _held_distribution(board)
    return 1 - sum((p * miss**n for n, p in enumerate(held)), Fraction(0))


def atari_value(board: Board) -> Fraction:
    """Lo que paga de media un atari, en veces la apuesta."""
    return jackpots_per_atari(board) * board.fever_balls / BALLS


# -- Presentación -------------------------------------------------------------------


def decimal(value: float, places: int = 1) -> str:
    """Número con coma decimal, como se escribe en español: `94,3`."""
    return f"{value:.{places}f}".replace(".", ",")


def hold_bar(held: int) -> str:
    """Reserva con formas, no solo colores: `●●●○`."""
    held = max(0, min(MAX_HOLD, held))
    return "●" * held + "○" * (MAX_HOLD - held)


def paytable_lines(board: Board) -> list[str]:
    """Tabla de premios de un tablero para enseñar al jugador."""
    pockets = sorted({v for v in board.pockets if v}, reverse=True)
    values = " · ".join(f"×{v}" for v in pockets)
    rush_cap = board.rush[2] + 1
    super_cap = board.super_rush[2] + 1
    every = round(1 / float(atari_volley_chance(board)))
    return [
        f"**{board.title}** · {board.risk}. {board.blurb}",
        f"🎯 Cada tanda lanza **{BALLS} bolas**. Una bola vale la apuesta entre {BALLS}.",
        f"⬇️ Bolsillos, de las esquinas hacia dentro: {values} bolas; el resto, OUT.",
        f"🌀 **START** (el centro) · una tirada en la pantalla. Reserva: {MAX_HOLD}.",
        f"🎰 Tres iguales · **ATARI** · +{board.fever_balls} bolas por premio "
        f"(uno cada ~{every} tandas)",
        f"🔥 Impar · **RUSH** · {board.rush[0]}/{board.rush[1]} de encadenar otro, "
        f"hasta {rush_cap}",
        f"7️⃣ El 7 · **SUPER RUSH** · {board.super_rush[0]}/{board.super_rush[1]} de "
        f"seguir, hasta {super_cap}",
        "👀 El **REACH** es espectáculo: no cambia lo que toca.",
        f"📊 Devuelve el {decimal(float(expected_return(board)) * 100)} % de media, como "
        "los otros tableros: cambia el riesgo, no la casa.",
    ]
