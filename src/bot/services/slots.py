"""Tragaperras: rodillos, tabla de pagos, giros gratis y máquina caliente.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.slots`) decide cuánto
se apuesta, cobra y paga con `EconomyService.play_slots` y pinta el resultado
con `bot.services.slots_render`.

La máquina tiene 3 rodillos y se ven 3 filas de cada uno (una cuadrícula de
3×3). Solo paga la fila del medio, la línea. Las filas de arriba y abajo
están para que se vea el símbolo que casi entra: el near-miss.

Cada rodillo es una tira fija de símbolos (`REEL_STRIPS`) y en cada tirada se
elige al azar dónde para cada uno, con la misma probabilidad para cada
posición. Las tiras no son iguales, y eso es lo que fija las probabilidades:
el primer rodillo lleva más 🃏 y 7️⃣ que el tercero, así que "7️⃣ 7️⃣ y el tercero
no" pasa mucho más que "7️⃣ 7️⃣ 7️⃣". Es el mismo truco de las máquinas reales.

Números de la tabla actual (calculados en `tests/unit/test_slots_service.py`):

- La línea devuelve ~83 % de lo apostado; con los giros gratis y la máquina
  caliente, ~91 %. El 3 % de cada apuesta va al bote común, que acaba
  saliendo entero en algún jackpot: en total vuelve ~94 % (la ruleta
  americana, 94,7 %).
- El 31 % de las tiradas paga algo, pero dos de cada tres de esas pagan menos
  de lo apostado (una 🍒 al principio devuelve la mitad). Es lo que más
  engancha de una tragaperras: la máquina lo celebra y aun así pierdes.
- Jackpot (🃏 🃏 🃏 en la línea): 1 de cada 14.400 tiradas.
- Giros gratis (3 🎟️ en cualquier fila): 1 de cada 133 tiradas.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

# -- Símbolos -----------------------------------------------------------------------

CHERRY = "C"
LEMON = "L"
GRAPE = "G"
BELL = "B"
DIAMOND = "D"
SEVEN = "7"
#: Comodín: sustituye a cualquier símbolo de la línea menos al 🎟️. Tres
#: seguidos son el jackpot.
WILD = "W"
#: Dispersión: cuenta en cualquier fila y no paga, da giros gratis.
SCATTER = "S"


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """Cómo se llama y cómo se pinta un símbolo."""

    key: str
    emoji: str
    name: str


SYMBOLS: dict[str, SymbolInfo] = {
    s.key: s
    for s in (
        SymbolInfo(CHERRY, "🍒", "Cereza"),
        SymbolInfo(LEMON, "🍋", "Limón"),
        SymbolInfo(GRAPE, "🍇", "Uvas"),
        SymbolInfo(BELL, "🔔", "Campana"),
        SymbolInfo(DIAMOND, "💎", "Diamante"),
        SymbolInfo(SEVEN, "7️⃣", "Siete"),
        SymbolInfo(WILD, "🃏", "Comodín"),
        SymbolInfo(SCATTER, "🎟️", "Giros gratis"),
    )
}

#: Tiras de los tres rodillos, en el orden en que pasan al girar. Se eligieron
#: barajando un recuento de símbolos con una semilla fija y comprobando que
#: dos 🎟️ no puedan verse a la vez en el mismo rodillo. Cambiarlas cambia todas
#: las probabilidades: hay que volver a pasar los tests del retorno.
REEL_STRIPS: tuple[str, str, str] = (
    "CLWBGCCLG7GB7BCBCDCGLSLLGGLCLWSD",
    "7GLLDCCSLBLSGGCBBLLBGGCLCD7WGC",
    "CBGGCDCLGGBDLGLGBSLGLLBLL7CCSW",
)

#: Tres iguales en la línea (con comodines): veces la apuesta.
THREE_OF_A_KIND: dict[str, int] = {
    CHERRY: 4,
    LEMON: 4,
    GRAPE: 10,
    BELL: 20,
    DIAMOND: 60,
    SEVEN: 200,
}
#: 🍒 🍒 en los dos primeros rodillos de la línea.
TWO_CHERRIES = 2
#: Una 🍒 en el primer rodillo devuelve la mitad de la apuesta. En medios,
#: para no usar decimales: `PAY_HALVES` guarda todos los premios ×2.
ONE_CHERRY_HALVES = 1

#: Símbolos que, si salen dos en la línea, hacen girar más el tercer rodillo.
HIGH_SYMBOLS = frozenset({SEVEN, DIAMOND, WILD})

#: Giros gratis por sacar 3 🎟️ (no se encadenan: en los giros gratis los 🎟️
#: no cuentan, para que una racha no se coma el retorno de la máquina).
FREE_SPINS = 5
#: Tiradas con premio que llenan la barra de la máquina caliente.
HEAT_MAX = 5
#: Multiplicador de la tirada caliente (solo la línea, no el bote).
HOT_MULTIPLIER = 2
#: Parte de cada apuesta que va al bote común, en tanto por ciento.
POT_SHARE_PERCENT = 3
#: Lo que pone la casa en el bote cuando se vacía (y al estrenarlo).
POT_SEED = 5_000


#: Veces la apuesta (lo cobrado entre lo apostado) a partir de las que una tirada
#: se celebra a lo grande. Las máquinas de verdad celebran «BIG WIN» con premios
#: modestos: cuanto más a menudo suena la fanfarria, más se recuerda haber ganado.
BIG_WIN = 5
MEGA_WIN = 15
EPIC_WIN = 50


class WinTier:
    """Nivel de celebración de un premio; texto estable para el dibujo y los logros."""

    BIG = "big"
    MEGA = "mega"
    EPIC = "epic"


def win_tier(won: int, stake: int) -> str | None:
    """Nivel de celebración de lo cobrado (línea más bote) frente a la apuesta.

    Args:
        won: Todo lo cobrado en la tirada.
        stake: Apuesta de la tirada; en un giro gratis, la que lo activó.

    Returns:
        `WinTier.EPIC`, `MEGA` o `BIG`, o `None` si no llega a ×`BIG_WIN`.
    """
    if stake <= 0 or won < BIG_WIN * stake:
        return None
    if won >= EPIC_WIN * stake:
        return WinTier.EPIC
    if won >= MEGA_WIN * stake:
        return WinTier.MEGA
    return WinTier.BIG


# -- Tirada -------------------------------------------------------------------------


class Kind:
    """Tipo de premio de la línea; texto estable para la presentación y los logros."""

    JACKPOT = "jackpot"
    THREE = "three"
    TWO_CHERRIES = "two_cherries"
    CHERRY = "cherry"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Spin:
    """Dónde han parado los rodillos y qué ha salido, sin dinero.

    Attributes:
        stops: Posición de la tira de cada rodillo que queda en la línea.
        grid: Filas visibles (arriba, línea, abajo); cada una con un
            símbolo por rodillo.
        kind: Tipo de premio de la línea (`Kind`).
        symbol: Símbolo que paga en un trío; `None` en el resto.
        pay_halves: Premio de la línea en medias apuestas (0 si no paga).
        scatters: 🎟️ visibles en toda la cuadrícula.
        near_miss: Por poco: los dos primeros de la línea iban a un premio
            gordo y el que faltaba ha quedado justo encima o debajo, o hay un
            trío gordo en una fila que no paga.
        anticipation: Los dos primeros rodillos prometen algo gordo (dos
            símbolos altos compatibles o dos 🎟️): el tercero gira más.
    """

    stops: tuple[int, int, int]
    grid: tuple[tuple[str, str, str], tuple[str, str, str], tuple[str, str, str]]
    kind: str
    symbol: str | None
    pay_halves: int
    scatters: int
    near_miss: bool
    anticipation: bool

    @property
    def line(self) -> tuple[str, str, str]:
        """Los tres símbolos de la línea de pago."""
        return self.grid[1]

    @property
    def is_jackpot(self) -> bool:
        """Si es 🃏 🃏 🃏 en la línea."""
        return self.kind == Kind.JACKPOT

    @property
    def triggers_free_spins(self) -> bool:
        """Si hay 3 🎟️ a la vista (uno por rodillo)."""
        return self.scatters >= 3

    def emoji_grid(self) -> str:
        """La cuadrícula en emojis, con flechas en la línea (para el modo turbo)."""
        rows = []
        for index, row in enumerate(self.grid):
            symbols = " ".join(SYMBOLS[s].emoji for s in row)
            rows.append(f"▶️ {symbols} ◀️" if index == 1 else f"⬛ {symbols} ⬛")
        return "\n".join(rows)


def _cell(reel: int, stop: int, offset: int) -> str:
    strip = REEL_STRIPS[reel]
    return strip[(stop + offset) % len(strip)]


def evaluate_line(line: tuple[str, str, str]) -> tuple[str, str | None, int]:
    """Premio de una línea: `(tipo, símbolo del trío, premio en medias apuestas)`.

    El comodín cuenta como cualquier símbolo menos el 🎟️, también como 🍒.
    """
    if line == (WILD, WILD, WILD):
        return Kind.JACKPOT, WILD, 0
    if SCATTER not in line:
        others = {s for s in line if s != WILD}
        if len(others) == 1:
            (symbol,) = others
            return Kind.THREE, symbol, THREE_OF_A_KIND[symbol] * 2
    first, second, _third = line
    if first in (CHERRY, WILD) and second in (CHERRY, WILD):
        return Kind.TWO_CHERRIES, None, TWO_CHERRIES * 2
    if first in (CHERRY, WILD):
        return Kind.CHERRY, None, ONE_CHERRY_HALVES
    return Kind.NONE, None, 0


def _high_target(first: str, second: str) -> str | None:
    """Símbolo que completaría un premio gordo con estos dos primeros, si lo hay."""
    if first not in HIGH_SYMBOLS or second not in HIGH_SYMBOLS:
        return None
    if first == second:
        return first
    if WILD in (first, second):
        return second if first == WILD else first
    return None


def _is_big_three(row: tuple[str, str, str]) -> bool:
    """Si la fila sería un jackpot o un trío de símbolos altos."""
    kind, symbol, _pay = evaluate_line(row)
    return kind == Kind.JACKPOT or (kind == Kind.THREE and symbol in HIGH_SYMBOLS)


def spin_at(stops: tuple[int, int, int], *, count_scatters: bool = True) -> Spin:
    """Resultado de parar los rodillos en `stops` (determinista; útil en pruebas).

    Args:
        count_scatters: `False` en los giros gratis, donde los 🎟️ no cuentan.
    """
    grid = tuple(tuple(_cell(reel, stops[reel], row - 1) for reel in range(3)) for row in range(3))
    line = grid[1]
    kind, symbol, pay_halves = evaluate_line(line)

    scatters = 0
    if count_scatters:
        scatters = sum(1 for reel in range(3) if any(row[reel] == SCATTER for row in grid))

    target = _high_target(line[0], line[1])
    third_column = (grid[0][2], grid[2][2])
    big_on_line = kind == Kind.JACKPOT or (kind == Kind.THREE and symbol in HIGH_SYMBOLS)
    near_miss = not big_on_line and (
        (target is not None and (target in third_column or WILD in third_column))
        # Un trío gordo en la fila de arriba o la de abajo, que no pagan.
        or any(_is_big_three(row) for row in (grid[0], grid[2]))
    )
    scatter_tease = count_scatters and all(
        any(row[reel] == SCATTER for row in grid) for reel in (0, 1)
    )
    return Spin(
        stops=stops,
        grid=grid,  # type: ignore[arg-type]
        kind=kind,
        symbol=symbol,
        pay_halves=pay_halves,
        scatters=scatters,
        near_miss=near_miss,
        anticipation=target is not None or scatter_tease,
    )


class SlotMachine:
    """Elige dónde paran los rodillos.

    Args:
        randbelow: Devuelve un entero en `[0, n)`; inyectable en pruebas.
    """

    def __init__(self, randbelow: Callable[[int], int] | None = None) -> None:
        self._randbelow = randbelow or random.SystemRandom().randrange

    def spin(self, *, free: bool = False) -> Spin:
        """Una tirada al azar. En los giros gratis (`free`) los 🎟️ no cuentan."""
        stops = tuple(self._randbelow(len(strip)) for strip in REEL_STRIPS)
        return spin_at(stops, count_scatters=not free)  # type: ignore[arg-type]


# -- Dinero de una tirada -----------------------------------------------------------


def line_payout(spin: Spin, stake: int, *, hot: bool = False) -> int:
    """Lo que devuelve la línea (apuesta incluida), sin el bote.

    Args:
        stake: Apuesta de la tirada; en los giros gratis, la que los activó.
        hot: Si la máquina está caliente: la línea paga `HOT_MULTIPLIER` veces.
    """
    multiplier = HOT_MULTIPLIER if hot else 1
    return stake * spin.pay_halves * multiplier // 2


def pot_share(stake: int) -> int:
    """Parte de una apuesta que va al bote común (redondeada hacia abajo)."""
    return stake * POT_SHARE_PERCENT // 100


def next_heat(heat: int, *, paid: bool, was_hot: bool) -> int:
    """Calor de la máquina tras una tirada.

    Cada tirada con premio suma uno, aunque sea el medio premio de una 🍒:
    la barra se llena a menudo y siempre parece que falta poco. La tirada
    caliente gasta la barra entera, pague o no.
    """
    if was_hot:
        return 0
    return min(HEAT_MAX, heat + 1) if paid else heat


def heat_bar(heat: int) -> str:
    """Barra de calor con formas (no solo colores): `🔥🔥🔥▫️▫️`."""
    heat = max(0, min(HEAT_MAX, heat))
    return "🔥" * heat + "▫️" * (HEAT_MAX - heat)


def paytable_lines() -> list[str]:
    """Tabla de premios para enseñar al jugador."""
    e = {key: info.emoji for key, info in SYMBOLS.items()}
    lines = [f"{e[WILD]} {e[WILD]} {e[WILD]} · **BOTE** entero"]
    for symbol, times in sorted(THREE_OF_A_KIND.items(), key=lambda item: -item[1]):
        lines.append(f"{e[symbol]} {e[symbol]} {e[symbol]} · ×{times}")
    lines.append(f"{e[CHERRY]} {e[CHERRY]} ❔ · ×{TWO_CHERRIES}")
    lines.append(f"{e[CHERRY]} ❔ ❔ · recuperas la mitad")
    lines.append(f"{e[SCATTER]} en los 3 rodillos (cualquier fila) · {FREE_SPINS} giros gratis")
    lines.append(f"{e[WILD]} sustituye a todo menos a {e[SCATTER]}")
    lines.append(f"🔥 Cada {HEAT_MAX} tiradas con premio, la siguiente paga ×{HOT_MULTIPLIER}")
    lines.append(f"💰 El {POT_SHARE_PERCENT} % de cada apuesta va al bote")
    return lines
