"""Reglas de la ruleta americana: casillas, apuestas legales y pagos.

Lógica pura, sin Discord ni base de datos. El azar se inyecta para poder
probar resultados concretos; en producción se usa `secrets.SystemRandom`.

Representación: las casillas son enteros 0-36 y `DOUBLE_ZERO` (37) para el
`00`. Las apuestas son conjuntos de casillas; el pago sale del tamaño del
conjunto con la fórmula estándar `36 / n - 1` a uno, salvo la línea de cinco
números (0-00-1-2-3), que paga 6 a 1. Con 38 casillas, la casa gana el 5,26 %
de lo apostado en todas las apuestas excepto la de cinco números (7,89 %).
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass

DOUBLE_ZERO = 37
POCKETS: tuple[int, ...] = tuple(range(38))

#: Orden real de las casillas en una rueda americana, en sentido horario.
WHEEL_ORDER: tuple[int, ...] = (
    0, 28, 9, 26, 30, 11, 7, 20, 32, 17, 5, 22, 34, 15, 3, 24, 36, 13, 1,
    DOUBLE_ZERO, 27, 10, 25, 29, 12, 8, 19, 31, 18, 6, 21, 33, 16, 4, 23, 35, 14, 2,
)  # fmt: skip

RED_NUMBERS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})
BLACK_NUMBERS = frozenset(range(1, 37)) - RED_NUMBERS
ZEROS = frozenset({0, DOUBLE_ZERO})


def label(pocket: int) -> str:
    """Texto de una casilla: `00` para el doble cero, el número en el resto."""
    return "00" if pocket == DOUBLE_ZERO else str(pocket)


def color(pocket: int) -> str:
    """`"red"`, `"black"` o `"green"`."""
    if pocket in ZEROS:
        return "green"
    return "red" if pocket in RED_NUMBERS else "black"


COLOR_EMOJI = {"red": "🔴", "black": "⚫", "green": "🟢"}


def pretty(pocket: int) -> str:
    """Casilla con su color en emoji, p. ej. `🔴 17`."""
    return f"{COLOR_EMOJI[color(pocket)]} {label(pocket)}"


@dataclass(frozen=True, slots=True)
class Bet:
    """Una apuesta legal sobre la mesa.

    Attributes:
        key: Identificador estable (sirve para botones y para repetir).
        name: Nombre para mostrar.
        numbers: Casillas que ganan.
        payout: Pago "a uno": se gana `apuesta * payout` y se recupera la apuesta.
    """

    key: str
    name: str
    numbers: frozenset[int]
    payout: int

    def wins(self, pocket: int) -> bool:
        """Si la casilla hace ganar esta apuesta."""
        return pocket in self.numbers

    def total_return(self, stake: int, pocket: int) -> int:
        """Lo que se devuelve al jugador (apuesta incluida); 0 si pierde."""
        return stake * (self.payout + 1) if self.wins(pocket) else 0


def _outside(key: str, name: str, numbers: set[int] | frozenset[int]) -> Bet:
    frozen = frozenset(numbers)
    return Bet(key, name, frozen, 36 // len(frozen) - 1)


#: Apuestas exteriores, por clave. Los botones de la mesa usan estas claves.
OUTSIDE_BETS: dict[str, Bet] = {
    bet.key: bet
    for bet in (
        _outside("red", "🔴 Rojo", RED_NUMBERS),
        _outside("black", "⚫ Negro", BLACK_NUMBERS),
        _outside("even", "Par", {n for n in range(1, 37) if n % 2 == 0}),
        _outside("odd", "Impar", {n for n in range(1, 37) if n % 2 == 1}),
        _outside("low", "1-18", set(range(1, 19))),
        _outside("high", "19-36", set(range(19, 37))),
        _outside("dozen1", "1ª docena (1-12)", set(range(1, 13))),
        _outside("dozen2", "2ª docena (13-24)", set(range(13, 25))),
        _outside("dozen3", "3ª docena (25-36)", set(range(25, 37))),
        _outside("col1", "1ª columna", set(range(1, 37, 3))),
        _outside("col2", "2ª columna", set(range(2, 37, 3))),
        _outside("col3", "3ª columna", set(range(3, 37, 3))),
    )
}

#: Nombres que se pueden escribir en `.ruleta 100 <apuesta>`.
OUTSIDE_ALIASES: dict[str, str] = {
    "rojo": "red", "roja": "red", "red": "red", "r": "red",
    "negro": "black", "negra": "black", "black": "black", "n": "black",
    "par": "even", "pares": "even", "even": "even",
    "impar": "odd", "impares": "odd", "odd": "odd",
    "1-18": "low", "bajo": "low", "bajos": "low", "falta": "low", "low": "low",
    "19-36": "high", "alto": "high", "altos": "high", "pasa": "high", "high": "high",
    "1-12": "dozen1", "d1": "dozen1", "docena1": "dozen1",
    "13-24": "dozen2", "d2": "dozen2", "docena2": "dozen2",
    "25-36": "dozen3", "d3": "dozen3", "docena3": "dozen3",
    "c1": "col1", "col1": "col1", "columna1": "col1",
    "c2": "col2", "col2": "col2", "columna2": "col2",
    "c3": "col3", "col3": "col3", "columna3": "col3",
}  # fmt: skip


def _inside_shapes() -> dict[frozenset[int], str]:
    """Todas las combinaciones interiores legales del tapete americano.

    En el tapete, la fila `f` (1-12) contiene `3f-2, 3f-1, 3f`; los números
    adyacentes en horizontal difieren en 1 (sin cruzar fila) y en vertical
    en 3. Los ceros se sitúan sobre la primera fila: el 0 junto al 1 y al 2,
    y el 00 junto al 2 y al 3.
    """
    shapes: dict[frozenset[int], str] = {}
    z, zz = 0, DOUBLE_ZERO
    for n in POCKETS:
        shapes[frozenset({n})] = "Pleno"
    for n in range(1, 37):
        if n % 3 != 0:
            shapes[frozenset({n, n + 1})] = "Caballo"
        if n <= 33:
            shapes[frozenset({n, n + 3})] = "Caballo"
        if n % 3 == 1:
            shapes[frozenset({n, n + 1, n + 2})] = "Transversal"
            if n <= 31:
                shapes[frozenset(range(n, n + 6))] = "Seisena"
        if n % 3 != 0 and n <= 32:
            shapes[frozenset({n, n + 1, n + 3, n + 4})] = "Cuadro"
    for pair in ({z, zz}, {z, 1}, {z, 2}, {zz, 2}, {zz, 3}):
        shapes[frozenset(pair)] = "Caballo"
    for trio in ({z, 1, 2}, {z, zz, 2}, {zz, 2, 3}):
        shapes[frozenset(trio)] = "Trío"
    shapes[frozenset({z, zz, 1, 2, 3})] = "Línea de cinco"
    return shapes


INSIDE_SHAPES: dict[frozenset[int], str] = _inside_shapes()


def inside_bet(numbers: frozenset[int]) -> Bet:
    """Construye la apuesta interior sobre `numbers`.

    Raises:
        ValueError: Si los números no forman una apuesta legal en el tapete.
    """
    kind = INSIDE_SHAPES.get(numbers)
    if kind is None:
        raise ValueError(
            "Esa combinación no existe en el tapete. Vale un número (`17`), dos juntos "
            "(`17-20`), una fila (`13-14-15`), un cuadro (`17-18-20-21`), dos filas "
            "(`13-14-15-16-17-18`) o `0-00-1-2-3`."
        )
    payout = 6 if len(numbers) == 5 else 36 // len(numbers) - 1
    ordered = sorted(numbers, key=lambda p: (p != 0, p != DOUBLE_ZERO, p))
    joined = "-".join(label(p) for p in ordered)
    return Bet(f"in:{joined}", f"{kind} {joined}", numbers, payout)


_SEPARATORS = re.compile(r"[\s,\-–/]+")


def parse_bet(text: str) -> Bet:
    """Interpreta una apuesta escrita por el usuario.

    Acepta nombres de apuestas exteriores (`rojo`, `par`, `1-18`, `d2`,
    `c3`…) y listas de números separados por espacios, comas o guiones
    para las interiores (`17`, `0`, `00`, `17-20`, `13 14 15`).

    Raises:
        ValueError: Con un mensaje mostrable si la apuesta no es válida.
    """
    value = text.strip().lower()
    if not value:
        raise ValueError("Falta la apuesta.")
    if value in OUTSIDE_ALIASES:
        return OUTSIDE_BETS[OUTSIDE_ALIASES[value]]
    if not re.fullmatch(r"[\d\s,\-–/]+", value):
        raise ValueError(
            f"No conozco la apuesta `{text}`. Prueba `rojo`, `par`, `1-18`, `d2`, `c3` o números."
        )
    numbers: set[int] = set()
    for token in filter(None, _SEPARATORS.split(value)):
        if token == "00":
            numbers.add(DOUBLE_ZERO)
        elif len(token) <= 2 and int(token) <= 36:
            numbers.add(int(token))
        else:
            raise ValueError(f"`{token}` no está en la ruleta (van del 0 al 36, más el 00).")
    return inside_bet(frozenset(numbers))


def bet_from_key(key: str) -> Bet:
    """Recupera una apuesta a partir de su `key` (botones, repetir).

    Raises:
        ValueError: Si la clave no corresponde a ninguna apuesta legal.
    """
    if key in OUTSIDE_BETS:
        return OUTSIDE_BETS[key]
    if key.startswith("in:"):
        return parse_bet(key[3:])
    raise ValueError(f"Apuesta desconocida: {key}")


class Wheel:
    """Fuente de resultados de la ruleta.

    Args:
        randbelow: Función `n -> entero en [0, n)`. Por defecto usa el
            generador criptográfico del sistema, que no se puede predecir a
            partir de tiradas anteriores.
    """

    def __init__(self, randbelow: Callable[[int], int] = secrets.randbelow) -> None:
        self._randbelow = randbelow

    def spin(self) -> int:
        """Devuelve la casilla ganadora, con las 38 equiprobables."""
        return POCKETS[self._randbelow(len(POCKETS))]


#: Máximo de apuestas distintas en una misma tirada. Con 10 líneas el
#: resultado sigue cabiendo de sobra en un embed y se lee de un vistazo.
MAX_WAGERS = 10


@dataclass(frozen=True, slots=True)
class Wager:
    """Fichas puestas sobre una apuesta: qué y cuánto."""

    bet: Bet
    stake: int


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """Resultado de una tirada con una o varias apuestas a la vez.

    Attributes:
        pocket: Casilla ganadora.
        wagers: Apuestas jugadas, en el orden en que se pusieron.
        returns: Lo devuelto por cada apuesta (apuesta incluida; 0 si pierde),
            en el mismo orden que `wagers`.
    """

    pocket: int
    wagers: tuple[Wager, ...]
    returns: tuple[int, ...]

    @property
    def stake(self) -> int:
        """Total apostado en la tirada."""
        return sum(w.stake for w in self.wagers)

    @property
    def total_return(self) -> int:
        """Total devuelto al jugador."""
        return sum(self.returns)

    @property
    def net(self) -> int:
        """Ganancia (positiva) o pérdida (negativa) neta de la tirada."""
        return self.total_return - self.stake

    @property
    def won(self) -> bool:
        """Si la tirada deja al jugador con más de lo que apostó."""
        return self.net > 0

    @property
    def max_payout(self) -> int:
        """Pago "a uno" más alto entre las apuestas acertadas (0 si ninguna)."""
        return max(
            (w.bet.payout for w, r in zip(self.wagers, self.returns, strict=True) if r),
            default=0,
        )


def add_wager(wagers: Sequence[Wager], bet: Bet, stake: int) -> tuple[Wager, ...]:
    """Añade fichas a una apuesta, apilándolas si ya estaba en la mesa.

    Raises:
        ValueError: Si se supera `MAX_WAGERS` apuestas distintas.
    """
    if stake <= 0:
        raise ValueError("La ficha tiene que ser mayor que cero.")
    result = list(wagers)
    for index, wager in enumerate(result):
        if wager.bet == bet:
            result[index] = Wager(bet, wager.stake + stake)
            return tuple(result)
    if len(result) >= MAX_WAGERS:
        raise ValueError(f"Como mucho {MAX_WAGERS} apuestas distintas por tirada.")
    result.append(Wager(bet, stake))
    return tuple(result)


def play_round(wheel: Wheel, wagers: Sequence[Wager]) -> RoundOutcome:
    """Gira la rueda una vez y calcula el pago de cada apuesta.

    Raises:
        ValueError: Si no hay apuestas.
    """
    if not wagers:
        raise ValueError("No hay ninguna apuesta en la mesa.")
    pocket = wheel.spin()
    returns = tuple(w.bet.total_return(w.stake, pocket) for w in wagers)
    return RoundOutcome(pocket, tuple(wagers), returns)


def play(wheel: Wheel, bet: Bet, stake: int) -> RoundOutcome:
    """Atajo de `play_round` para una sola apuesta."""
    return play_round(wheel, [Wager(bet, stake)])


def parse_bets(text: str) -> list[Bet]:
    """Interpreta varias apuestas separadas por `+`: `rojo + 17 + d2`.

    Raises:
        ValueError: Si alguna no es válida, hay repetidas o son demasiadas.
    """
    bets = [parse_bet(part) for part in text.split("+")]
    if len({bet.key for bet in bets}) != len(bets):
        raise ValueError("Hay apuestas repetidas. Pon cada una una sola vez.")
    if len(bets) > MAX_WAGERS:
        raise ValueError(f"Como mucho {MAX_WAGERS} apuestas distintas por tirada.")
    return bets
