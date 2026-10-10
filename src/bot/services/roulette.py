"""Reglas de la ruleta americana: casillas, apuestas legales y pagos.

Lógica pura, sin Discord ni base de datos. El azar se inyecta para poder
probar resultados concretos; en producción se usa `secrets.SystemRandom`.

Representación: las casillas son enteros 0-36 y `DOUBLE_ZERO` (37) para el
`00`. Las apuestas son conjuntos de casillas; el pago sale del tamaño del
conjunto con la fórmula estándar `36 / n - 1` a uno, salvo la línea de cinco
números (0-00-1-2-3), que paga 6 a 1, y el pleno. Con 38 casillas, la casa
gana el 5,26 % de lo apostado en todas las apuestas excepto la de cinco
números (7,89 %) y el pleno (5,5 %, ver abajo).

**Los rayos.** Copia de la Lightning Roulette de los casinos en línea: en
cada tirada, con las apuestas ya cerradas, caen de 1 a 5 rayos sobre números
al azar y cada uno lleva un multiplicador de 50 a 500. Un pleno que acierta un
número con rayo cobra ese multiplicador; sin rayo, cobra 29 a 1 en vez de 35.
Lo que se quita a todos los plenos se reparte en los pocos que tienen rayo:
la ventaja de la casa apenas cambia (`straight_return`), pero la varianza se
dispara. Es el gancho de la tragaperras metido en la ruleta: cada tirada
puede ser la de 500x y el ojo se va solo a los números con rayo.

**El casi.** `near_miss` dice si la bola ha caído a una o dos casillas de un
pleno perdido. La escena hace que la bola pase por él antes de caer en el
suyo y el resultado lo grita («¡POR UNA CASILLA!»). En las tragaperras, los
casi activan el mismo circuito de recompensa que un premio y alargan el juego
(Clark et al., Neuron, 2009). Es solo dibujo: el número ya está decidido.

**Calientes y fríos.** `hot_cold` saca del historial del servidor los
números que más salen y los que llevan más sin salir. Los casinos los ponen
en el marcador aunque cada tirada sea independiente: invitan a la falacia
del jugador («al 17 le toca»).
"""

from __future__ import annotations

import re
import secrets
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

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


#: Pago "a uno" de un pleno sin rayo (35 en la ruleta de siempre).
STRAIGHT_PAYOUT = 29

#: Cuántos rayos caen en una tirada: `(rayos, peso sobre 100)`.
LIGHTNING_COUNTS: tuple[tuple[int, int], ...] = ((1, 50), (2, 30), (3, 15), (4, 4), (5, 1))
#: Multiplicador de cada rayo (pago "a uno" del pleno): `(multiplicador, peso sobre 100)`.
#: Calibrado para que el pleno devuelva un poco menos que el resto de apuestas
#: (`straight_return`, 94,5 % frente a 94,7 %); una prueba lo comprueba.
LIGHTNING_MULTIPLIERS: tuple[tuple[int, int], ...] = (
    (50, 30), (100, 25), (150, 15), (200, 12), (300, 10), (500, 8),
)  # fmt: skip
#: Distancia en la rueda (en casillas) a la que una caída cuenta como «casi».
NEAR_MISS_DISTANCE = 2


def _pick(table: Sequence[tuple[int, int]], roll: int) -> int:
    """Valor de `table` (pares valor/peso sobre 100) que cae con `roll` en [0, 100)."""
    for value, weight in table:
        if roll < weight:
            return value
        roll -= weight
    raise ValueError("Los pesos tienen que sumar 100.")


def straight_return() -> Fraction:
    """Lo que devuelve de media un pleno por cada Y$ apostado, rayos incluidos.

    Un número concreto lleva rayo con probabilidad `E[rayos] / 38`. Si sale y
    tiene rayo cobra `m + 1`; si no tiene, `STRAIGHT_PAYOUT + 1`.
    """
    expected_count = Fraction(sum(n * w for n, w in LIGHTNING_COUNTS), 100)
    expected_mult = Fraction(sum(m * w for m, w in LIGHTNING_MULTIPLIERS), 100)
    p_lucky = expected_count / len(POCKETS)
    hit = (1 - p_lucky) * (STRAIGHT_PAYOUT + 1) + p_lucky * (expected_mult + 1)
    return hit / len(POCKETS)


def wheel_distance(a: int, b: int) -> int:
    """Casillas que separan `a` y `b` en la rueda, por el lado más corto."""
    diff = abs(WHEEL_ORDER.index(a) - WHEEL_ORDER.index(b))
    return min(diff, len(WHEEL_ORDER) - diff)


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

    @property
    def straight(self) -> bool:
        """Si es un pleno (un solo número), la única apuesta que cobra rayos."""
        return len(self.numbers) == 1

    def payout_for(self, pocket: int, lucky: Mapping[int, int] | None = None) -> int:
        """Pago "a uno" si sale `pocket`: el del rayo en un pleno con rayo; 0 si pierde."""
        if not self.wins(pocket):
            return 0
        if self.straight and lucky and pocket in lucky:
            return lucky[pocket]
        return self.payout

    def total_return(self, stake: int, pocket: int, lucky: Mapping[int, int] | None = None) -> int:
        """Lo que se devuelve al jugador (apuesta incluida); 0 si pierde."""
        payout = self.payout_for(pocket, lucky)
        return stake * (payout + 1) if payout else 0


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
    if len(numbers) == 1:
        payout = STRAIGHT_PAYOUT
    else:
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
        lightning: Si caen rayos. Las pruebas que miran un pago concreto los apagan.
    """

    def __init__(
        self, randbelow: Callable[[int], int] = secrets.randbelow, *, lightning: bool = True
    ) -> None:
        self._randbelow = randbelow
        self.lightning = lightning

    def spin(self) -> int:
        """Devuelve la casilla ganadora, con las 38 equiprobables."""
        return POCKETS[self._randbelow(len(POCKETS))]

    def strike(self) -> dict[int, int]:
        """Rayos de la tirada: `{casilla: multiplicador}`, en el orden en que caen.

        Independiente del número que sale: se sortean aparte, sin repetir casilla.
        """
        if not self.lightning:
            return {}
        count = _pick(LIGHTNING_COUNTS, self._randbelow(100))
        remaining = list(POCKETS)
        lucky: dict[int, int] = {}
        for _ in range(count):
            pocket = remaining.pop(self._randbelow(len(remaining)))
            lucky[pocket] = _pick(LIGHTNING_MULTIPLIERS, self._randbelow(100))
        return lucky


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
        lucky: Rayos de la tirada, `{casilla: multiplicador}`.
    """

    pocket: int
    wagers: tuple[Wager, ...]
    returns: tuple[int, ...]
    lucky: Mapping[int, int] = field(default_factory=dict)

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
        """Pago "a uno" más alto entre las apuestas acertadas, rayos incluidos (0 si ninguna)."""
        return max((w.bet.payout_for(self.pocket, self.lucky) for w in self.wagers), default=0)

    @property
    def lucky_hit(self) -> int:
        """Multiplicador cobrado por un pleno con rayo; 0 si ninguno."""
        if self.pocket not in self.lucky:
            return 0
        if any(w.bet.straight and w.bet.wins(self.pocket) for w in self.wagers):
            return self.lucky[self.pocket]
        return 0

    @property
    def lucky_covered(self) -> bool:
        """Si algún pleno de la tirada llevaba rayo (haya salido o no)."""
        return any(w.bet.straight and w.bet.numbers & self.lucky.keys() for w in self.wagers)

    @property
    def near_miss(self) -> int | None:
        """Pleno perdido a `NEAR_MISS_DISTANCE` casillas o menos de la bola; el más cercano.

        Es la casilla por la que la escena hace pasar la bola antes de caer.
        """
        lost = [
            number
            for w in self.wagers
            if w.bet.straight and not w.bet.wins(self.pocket)
            for number in w.bet.numbers
        ]
        close = [(wheel_distance(n, self.pocket), n) for n in lost]
        close = [pair for pair in close if pair[0] <= NEAR_MISS_DISTANCE]
        return min(close)[1] if close else None


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
    lucky = wheel.strike()
    returns = tuple(w.bet.total_return(w.stake, pocket, lucky) for w in wagers)
    return RoundOutcome(pocket, tuple(wagers), returns, lucky)


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


#: Números calientes y fríos que enseña el marcador.
HOT_COLD_COUNT = 3


def hot_cold(
    history: Sequence[int], count: int = HOT_COLD_COUNT
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Números calientes y fríos de un historial (el más reciente primero).

    Returns:
        `(calientes, fríos)`. Calientes: `(casilla, veces)` de los que más han
        salido, desempatando por el más reciente. Fríos: `(casilla, tiradas sin
        salir)` de los que llevan más sin salir; uno que no ha salido nunca
        cuenta todo el historial. Vacíos si no hay historial.
    """
    if not history:
        return [], []
    counts = Counter(history)
    first_seen = {pocket: history.index(pocket) for pocket in counts}
    hot = sorted(counts.items(), key=lambda item: (-item[1], first_seen[item[0]]))[:count]
    gaps = [(pocket, first_seen.get(pocket, len(history))) for pocket in WHEEL_ORDER]
    cold = sorted(gaps, key=lambda item: -item[1])[:count]
    return hot, cold
