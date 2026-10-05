"""Botes: tragaperras de 5×4 con monedas, recogida, maletines y bonus de reinicio.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.hold_win`) cobra, paga y
guarda; el dibujo vive en `bot.services.hold_win_render`. Las tres máquinas
(`volcan`, `olimpo` y `filon`) comparten estas reglas y estos números: solo
cambian los dibujos y los textos (`THEMES`). Así un solo juego de pruebas
garantiza el retorno de las tres.

**Unidades.** Todos los valores de premio van en *puntos*: centésimas de la
apuesta de la tirada (100 puntos = ×1 la apuesta). Así una moneda de ×0,3 o un
Mini de ×1,5 son enteros, y el Y$ se calcula una sola vez al final
(`to_amount`), redondeando hacia abajo.

Una tirada del juego base tiene dos capas que pagan por separado:

1. **Ways.** Cada símbolo normal paga si aparece en los rodillos 1, 2 y 3
   seguidos (o más), en cualquier fila. Paga su premio por cada combinación
   posible: dos 🍍 en el rodillo 1, una en el 2 y una en el 3 son 2×1×1 = 2
   ways. El comodín cuenta como cualquier símbolo normal en los rodillos 2 a 4.
2. **Monedas y recogida.** Las monedas (verde, azul y roja) llevan su valor
   escrito y no forman combinaciones. Si el recogedor sale en el rodillo 1 o en
   el 5, se cobran todas las monedas de la pantalla; si sale en los dos, el
   doble.

Además, cada moneda que cae, se cobre o no, entra en el **maletín** de su color.
El maletín lleno dispara el **bonus** de ese color (`BonusGame`). El progreso
es real y se guarda: no hay barras de adorno. Para que nadie llene un maletín
a 10 Y$ y lo dispare a 10.000 Y$, el bonus se juega a la apuesta media de las
monedas que lo llenaron (`Case.average_stake`).

En el bonus solo caen monedas y modificadores. Quedan 3 tiradas y cada vez que
cae algo vuelve a 3. Con 10, 15 o 20 monedas se ganan los botes Mini, Major y
Grand. Las fichas de bote del juego base suben el Mini y el Major de cada
jugador; el Grand es fijo, ×1.000.

Números de las reglas actuales (simulados con `simulate` en varios millones de
tiradas; `tests/unit/test_hold_win_service.py` comprueba una versión corta):

- Retorno total ~94 %, como el resto del casino: ~42 % de los ways, ~22 % de
  la recogida y ~30 % de los bonus.
- Un bonus cada ~58 tiradas: el verde cada ~120, el azul cada ~250, el rojo
  cada ~500 y el gran bonus cada ~350.
- El bonus medio paga ~×17 la apuesta: ~×7 el verde, ~×19 el azul, ~×44 el
  rojo y ~×26 el gran bonus. El Grand (pantalla llena) sale una vez cada
  ~200.000 tiradas.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

# -- Unidades -----------------------------------------------------------------------

#: Puntos que vale la apuesta entera: los premios van en centésimas de apuesta.
POINTS_PER_STAKE = 100

#: Apuesta mínima. Con menos, una moneda verde de ×0,1 redondearía a 0 Y$.
MIN_STAKE = 10

REELS = 5
ROWS = 4
CELLS = REELS * ROWS


def to_amount(points: int, stake: int) -> int:
    """Puntos de premio a yapdollars para una apuesta, redondeando hacia abajo."""
    return stake * points // POINTS_PER_STAKE


def format_multiplier(points: int) -> str:
    """`250` → `×2,5`; `1000` → `×10`."""
    whole, rest = divmod(points, POINTS_PER_STAKE)
    if rest == 0:
        return f"×{whole}"
    text = f"{points / POINTS_PER_STAKE:.2f}".rstrip("0")
    return "×" + text.replace(".", ",")


def cell_index(reel: int, row: int) -> int:
    """Posición de una casilla en la rejilla plana (por rodillos)."""
    return reel * ROWS + row


# -- Celdas -------------------------------------------------------------------------


class Tier(IntEnum):
    """Color de una moneda. Cada uno tiene su forma (no solo su color)."""

    GREEN = 0
    BLUE = 1
    RED = 2


TIER_NAMES = {Tier.GREEN: "verde", Tier.BLUE: "azul", Tier.RED: "roja"}
#: Emoji de cada color en los textos. Se distinguen por la forma: ● ⬢ ★.
TIER_EMOJI = {Tier.GREEN: "🟢", Tier.BLUE: "🔷", Tier.RED: "⭐"}


class Kind:
    """Tipo de casilla; texto estable para la presentación y los logros."""

    PAY = "pay"
    WILD = "wild"
    COIN = "coin"
    COLLECT = "collect"
    CHIP = "chip"
    # Solo en el bonus:
    TICKET = "ticket"
    EXTRA = "extra"
    INSTANT = "instant"
    MAXIMIZER = "max"
    MYSTERY_RED = "mystery_red"
    MYSTERY_BLUE = "mystery_blue"


#: Modificadores del bonus: se aplican al caer y desaparecen en la tirada siguiente.
MODIFIERS = frozenset({Kind.TICKET, Kind.EXTRA, Kind.INSTANT, Kind.MAXIMIZER})


@dataclass(frozen=True, slots=True)
class Cell:
    """Lo que hay en una casilla.

    Attributes:
        kind: Tipo (`Kind`).
        symbol: Índice del símbolo normal (0 el más bajo) si `kind` es `PAY`;
            `"mini"`/`"major"` en una ficha de bote.
        tier: Color de una moneda.
        value: Puntos de una moneda; `+N` del ticket (en enteros) o el factor
            del multiplicador inmediato (en décimas: 15 = ×1,5).
    """

    kind: str
    symbol: int | str | None = None
    tier: Tier | None = None
    value: int = 0

    @property
    def is_coin(self) -> bool:
        """Si es una moneda (lo único que se queda fijo en el bonus)."""
        return self.kind == Kind.COIN


# -- Tabla de pagos -----------------------------------------------------------------

#: Símbolos normales, de menos a más valor. Cada máquina los dibuja a su manera.
PAY_SYMBOLS = 6

#: Puntos por way de cada símbolo con 3, 4 y 5 rodillos seguidos.
WAYS_PAYS: tuple[tuple[int, int, int], ...] = (
    (3, 6, 15),
    (3, 7, 20),
    (4, 10, 25),
    (6, 13, 40),
    (10, 25, 65),
    (16, 40, 130),
)

#: Valores posibles de cada moneda (en puntos) y su peso. Verde ×0,1–×0,5,
#: azul ×0,6–×1,5, roja ×2–×5 (las del original: ×1–×5, ×6–×15 y ×20–×50 de
#: una apuesta de línea que es la décima parte de la total).
COIN_VALUES: dict[Tier, tuple[tuple[int, int], ...]] = {
    Tier.GREEN: ((10, 40), (20, 28), (30, 17), (40, 10), (50, 5)),
    Tier.BLUE: ((60, 35), (80, 28), (100, 20), (120, 11), (150, 6)),
    Tier.RED: ((200, 40), (250, 25), (300, 18), (400, 11), (500, 6)),
}

# -- Botes (en puntos) --------------------------------------------------------------

MINI_BASE, MINI_MAX, MINI_CHIP = 100, 250, 5
MAJOR_BASE, MAJOR_MAX, MAJOR_CHIP = 500, 1_000, 10
#: El Grand es fijo y no crece con fichas: ×1.000 la apuesta. Es distinto del
#: bote común de `tragas`, que sí acumula.
GRAND = 100_000

#: Monedas en el bonus que dan cada bote. Se ganan todos los alcanzados.
MINI_COINS, MAJOR_COINS, GRAND_COINS = 10, 15, 20

# -- Rodillos del juego base ---------------------------------------------------------

#: Peso de cada tipo de casilla en cada rodillo; cada casilla se elige por
#: separado. El recogedor solo sale en los rodillos 1 y 5 y el comodín solo
#: en los del medio. Cambiar un peso cambia el retorno: hay que volver a
#: pasar `simulate` y las pruebas.
_EDGE = {Kind.COIN: 62, Kind.COLLECT: 48, Kind.CHIP: 10}
_MIDDLE = {Kind.COIN: 62, Kind.WILD: 34, Kind.CHIP: 10}
#: Pesos de los símbolos normales (de menos a más valor), comunes a todos.
_PAY_WEIGHTS = (175, 160, 145, 125, 105, 84)

#: Color de las monedas del juego base.
BASE_TIER_WEIGHTS = ((Tier.GREEN, 78), (Tier.BLUE, 18), (Tier.RED, 4))
#: Fichas de bote del juego base: Mini o Major.
CHIP_WEIGHTS = (("mini", 70), ("major", 30))

# -- Maletines ----------------------------------------------------------------------

#: Monedas que caben en el maletín de cada color.
CASE_SIZE = {Tier.GREEN: 108, Tier.BLUE: 50, Tier.RED: 22}
#: Uno de cada tantos bonus sube al gran bonus, con monedas de los tres colores.
GRAND_BONUS_ODDS = 6

# -- Bonus --------------------------------------------------------------------------

#: Tiradas a las que vuelve el contador cuando cae algo.
RESET_VALUE = 3
#: Tope del contador tras las tiradas extra.
MAX_RESET_VALUE = 6


class BonusKind:
    """Qué bonus se juega: el de un color o el gran bonus con los tres."""

    GREEN = "green"
    BLUE = "blue"
    RED = "red"
    GRAND = "grand"

    ALL = (GREEN, BLUE, RED, GRAND)


BONUS_OF_TIER = {Tier.GREEN: BonusKind.GREEN, Tier.BLUE: BonusKind.BLUE, Tier.RED: BonusKind.RED}


@dataclass(frozen=True, slots=True)
class BonusRules:
    """Números de un tipo de bonus.

    Attributes:
        tiers: Colores de las monedas que caen y su peso.
        land: Probabilidad, en milésimas, de que algo caiga en cada casilla
            vacía en cada tirada.
        start: Monedas con las que empieza y su peso.
    """

    tiers: tuple[tuple[Tier, int], ...]
    land: int
    start: tuple[tuple[int, int], ...]


BONUS_RULES: dict[str, BonusRules] = {
    BonusKind.GREEN: BonusRules(((Tier.GREEN, 1),), 45, ((4, 50), (5, 35), (6, 15))),
    BonusKind.BLUE: BonusRules(((Tier.BLUE, 1),), 40, ((4, 55), (5, 35), (6, 10))),
    BonusKind.RED: BonusRules(((Tier.RED, 1),), 34, ((3, 50), (4, 35), (5, 15))),
    BonusKind.GRAND: BonusRules(
        ((Tier.GREEN, 45), (Tier.BLUE, 35), (Tier.RED, 20)), 48, ((6, 50), (7, 35), (8, 15))
    ),
}

#: Qué cae en una casilla del bonus.
BONUS_LAND_WEIGHTS = (
    (Kind.COIN, 790),
    (Kind.TICKET, 55),
    (Kind.EXTRA, 30),
    (Kind.INSTANT, 30),
    (Kind.MAXIMIZER, 10),
    (Kind.MYSTERY_RED, 45),
    (Kind.MYSTERY_BLUE, 40),
)
#: El misterioso rojo puede ser un ticket, una tirada extra o una moneda.
MYSTERY_RED_WEIGHTS = ((Kind.TICKET, 35), (Kind.EXTRA, 25), (Kind.COIN, 40))
#: El azul, un multiplicador inmediato, un maximizador o una moneda.
MYSTERY_BLUE_WEIGHTS = ((Kind.INSTANT, 35), (Kind.MAXIMIZER, 15), (Kind.COIN, 50))
#: Ticket de multiplicador: suma +1 a +5 al multiplicador del bonus.
TICKET_WEIGHTS = ((1, 40), (2, 25), (3, 18), (4, 11), (5, 6))
#: Multiplicador inmediato, en décimas: ×1,5, ×2, ×3 o ×5.
INSTANT_WEIGHTS = ((15, 45), (20, 30), (30, 18), (50, 7))


def _pick(rng: random.Random, table: Sequence[tuple[object, int]]) -> object:
    total = sum(weight for _item, weight in table)
    roll = rng.randrange(total)
    for item, weight in table:
        roll -= weight
        if roll < 0:
            return item
    raise AssertionError("tabla de pesos vacía")


def _reel_table(reel: int) -> tuple[tuple[Cell | str, int], ...]:
    specials = _EDGE if reel in (0, REELS - 1) else _MIDDLE
    table: list[tuple[Cell | str, int]] = [
        (Cell(Kind.PAY, symbol=index), weight) for index, weight in enumerate(_PAY_WEIGHTS)
    ]
    table.extend((kind, weight) for kind, weight in specials.items())
    return tuple(table)


_REEL_TABLES = tuple(_reel_table(reel) for reel in range(REELS))


def coin(rng: random.Random, tier: Tier) -> Cell:
    """Una moneda al azar del color indicado."""
    value = _pick(rng, COIN_VALUES[tier])
    return Cell(Kind.COIN, tier=tier, value=int(value))  # type: ignore[arg-type]


def _base_cell(rng: random.Random, reel: int) -> Cell:
    choice = _pick(rng, _REEL_TABLES[reel])
    if isinstance(choice, Cell):
        return choice
    if choice == Kind.COIN:
        return coin(rng, _pick(rng, BASE_TIER_WEIGHTS))  # type: ignore[arg-type]
    if choice == Kind.CHIP:
        return Cell(Kind.CHIP, symbol=str(_pick(rng, CHIP_WEIGHTS)))
    return Cell(str(choice))


# -- Juego base ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WayWin:
    """Premio de un símbolo normal por ways.

    Attributes:
        symbol: Índice del símbolo.
        reels: Rodillos seguidos desde el primero (3, 4 o 5).
        ways: Combinaciones que pagan.
        points: Premio total en puntos (`ways` × el premio por way).
        cells: Casillas que forman el premio (para resaltarlas).
    """

    symbol: int
    reels: int
    ways: int
    points: int
    cells: frozenset[int]


@dataclass(frozen=True, slots=True)
class BaseSpin:
    """Una tirada del juego base, sin dinero.

    Attributes:
        grid: Las 20 casillas, por rodillos (`cell_index`).
        wins: Premios por ways.
        collectors: Rodillos con recogedor (0, 1 o 2).
    """

    grid: tuple[Cell, ...]
    wins: tuple[WayWin, ...]
    collectors: int

    @property
    def ways_points(self) -> int:
        """Lo que pagan los ways, en puntos."""
        return sum(win.points for win in self.wins)

    @property
    def coins(self) -> list[tuple[int, Cell]]:
        """Monedas a la vista con su casilla."""
        return [(i, c) for i, c in enumerate(self.grid) if c.is_coin]

    @property
    def coin_points(self) -> int:
        """Suma del valor de las monedas a la vista."""
        return sum(c.value for _i, c in self.coins)

    @property
    def collect_points(self) -> int:
        """Lo que paga la recogida: todas las monedas por cada recogedor."""
        return self.coin_points * self.collectors

    @property
    def points(self) -> int:
        """Premio total de la tirada, en puntos."""
        return self.ways_points + self.collect_points

    def coins_by_tier(self) -> dict[Tier, int]:
        """Cuántas monedas de cada color han caído (lo que entra en los maletines)."""
        counts = {tier: 0 for tier in Tier}
        for _i, cell in self.coins:
            assert cell.tier is not None
            counts[cell.tier] += 1
        return counts

    @property
    def chips(self) -> dict[str, int]:
        """Fichas de bote a la vista: `{"mini": n, "major": n}`."""
        counts = {"mini": 0, "major": 0}
        for cell in self.grid:
            if cell.kind == Kind.CHIP:
                counts[str(cell.symbol)] += 1
        return counts

    @property
    def collector_cells(self) -> list[int]:
        """Casillas con recogedor."""
        return [i for i, c in enumerate(self.grid) if c.kind == Kind.COLLECT]

    @property
    def anticipation(self) -> bool:
        """Recogedor en el rodillo 1, monedas a la vista y el 5 por parar: gira más."""
        first = any(self.grid[cell_index(0, r)].kind == Kind.COLLECT for r in range(ROWS))
        coins_before_last = sum(
            1 for i, c in self.coins if i < cell_index(REELS - 1, 0)
        )  # monedas que ya se ven cuando falta el último rodillo
        return first and coins_before_last >= 2

    @property
    def near_miss(self) -> bool:
        """Mucho dinero en monedas a la vista y ningún recogedor."""
        return self.collectors == 0 and self.coin_points >= 100

    @property
    def wild_win(self) -> bool:
        """Si algún premio por ways lleva comodín."""
        return any(self.grid[i].kind == Kind.WILD for win in self.wins for i in win.cells)


def evaluate_ways(grid: Sequence[Cell]) -> tuple[WayWin, ...]:
    """Premios por ways de una rejilla (de izquierda a derecha, desde el rodillo 1)."""
    wins: list[WayWin] = []
    for symbol in range(PAY_SYMBOLS):
        ways = 1
        cells: set[int] = set()
        reels = 0
        for reel in range(REELS):
            column = [
                cell_index(reel, row)
                for row in range(ROWS)
                if grid[cell_index(reel, row)].kind == Kind.WILD
                or (
                    grid[cell_index(reel, row)].kind == Kind.PAY
                    and grid[cell_index(reel, row)].symbol == symbol
                )
            ]
            if not column:
                break
            # Un rodillo solo de comodines no empieza un premio.
            if reel == 0 and all(grid[i].kind == Kind.WILD for i in column):
                break
            ways *= len(column)
            cells.update(column)
            reels += 1
        if reels >= 3:
            points = WAYS_PAYS[symbol][reels - 3] * ways
            wins.append(WayWin(symbol, reels, ways, points, frozenset(cells)))
    return tuple(wins)


def evaluate_base(grid: Sequence[Cell]) -> BaseSpin:
    """Resultado de una rejilla del juego base (determinista; útil en pruebas)."""
    collectors = sum(
        1
        for reel in (0, REELS - 1)
        if any(grid[cell_index(reel, r)].kind == Kind.COLLECT for r in range(ROWS))
    )
    return BaseSpin(grid=tuple(grid), wins=evaluate_ways(grid), collectors=collectors)


def spin_base(rng: random.Random) -> BaseSpin:
    """Una tirada al azar del juego base."""
    grid = [_base_cell(rng, index // ROWS) for index in range(CELLS)]
    return evaluate_base(grid)


def random_filler(rng: random.Random, reel: int) -> Cell:
    """Una casilla cualquiera de un rodillo, para dibujarla pasando al girar."""
    return _base_cell(rng, reel)


# -- Maletines y botes ---------------------------------------------------------------


@dataclass(slots=True)
class Case:
    """Maletín de un color: monedas que lleva y la suma de sus apuestas."""

    tier: Tier
    coins: int = 0
    stake_sum: int = 0

    @property
    def size(self) -> int:
        """Monedas que caben."""
        return CASE_SIZE[self.tier]

    @property
    def full(self) -> bool:
        """Si ya dispara su bonus."""
        return self.coins >= self.size

    def average_stake(self) -> int:
        """Apuesta media de las monedas del maletín: la del bonus que dispara."""
        return max(MIN_STAKE, self.stake_sum // self.coins) if self.coins else MIN_STAKE

    def add(self, coins: int, stake: int) -> None:
        """Mete monedas caídas con una apuesta."""
        self.coins += coins
        self.stake_sum += coins * stake

    def empty(self) -> int:
        """Vacía el maletín al disparar el bonus; devuelve la apuesta del bonus.

        Lo que sobra de un maletín lleno pasa al siguiente con la apuesta media.
        """
        stake = self.average_stake()
        overflow = max(0, self.coins - self.size)
        self.coins = overflow
        self.stake_sum = overflow * stake
        return stake


@dataclass(slots=True)
class Meters:
    """Estado guardado de un jugador en una máquina: maletines y botes.

    Attributes:
        mini, major: Valor actual de cada bote, en puntos.
    """

    cases: dict[Tier, Case] = field(default_factory=lambda: {t: Case(t) for t in Tier})
    mini: int = MINI_BASE
    major: int = MAJOR_BASE

    def add_chips(self, chips: dict[str, int]) -> None:
        """Suma las fichas de bote del juego base, sin pasar del máximo."""
        self.mini = min(MINI_MAX, self.mini + MINI_CHIP * chips.get("mini", 0))
        self.major = min(MAJOR_MAX, self.major + MAJOR_CHIP * chips.get("major", 0))


@dataclass(frozen=True, slots=True)
class Trigger:
    """Un bonus que se dispara: de qué tipo y a qué apuesta se juega."""

    kind: str
    stake: int
    upgraded: bool


def fill_cases(meters: Meters, spin: BaseSpin, stake: int, rng: random.Random) -> Trigger | None:
    """Mete las monedas de la tirada en los maletines y dice si sale un bonus.

    Si se llenan dos o más maletines a la vez, o uno y sale la tirada de
    `GRAND_BONUS_ODDS`, es el gran bonus, con la apuesta media de los llenos.
    Modifica `meters`.
    """
    meters.add_chips(spin.chips)
    for tier, count in spin.coins_by_tier().items():
        if count:
            meters.cases[tier].add(count, stake)
    full = [case for case in meters.cases.values() if case.full]
    if not full:
        return None
    stakes = [case.empty() for case in full]
    average = max(MIN_STAKE, sum(stakes) // len(stakes))
    if len(full) > 1:
        return Trigger(BonusKind.GRAND, average, upgraded=False)
    if rng.randrange(GRAND_BONUS_ODDS) == 0:
        return Trigger(BonusKind.GRAND, average, upgraded=True)
    return Trigger(BONUS_OF_TIER[full[0].tier], average, upgraded=False)


# -- Bonus --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Landing:
    """Algo que cae en una casilla en una tirada del bonus.

    Attributes:
        index: Casilla.
        cell: Lo que queda (tras revelar el misterioso).
        mystery: `Kind.MYSTERY_RED`/`MYSTERY_BLUE` si salió de un misterioso.
    """

    index: int
    cell: Cell
    mystery: str | None = None


@dataclass(frozen=True, slots=True)
class BonusStep:
    """Una tirada del bonus ya resuelta.

    Attributes:
        landings: Lo que ha caído.
        multiplier_added: Lo que suman los tickets de esta tirada.
        instant: Factor (en décimas) aplicado a las monedas; 10 si ninguno.
        extra: Tiradas extra ganadas (suben el valor de reinicio).
        maximized: Si un maximizador ha subido los botes al máximo.
        board: Rejilla tras la tirada (monedas y los modificadores recién caídos).
        respins_left: Contador tras la tirada.
    """

    landings: tuple[Landing, ...]
    multiplier_added: int
    instant: int
    extra: int
    maximized: bool
    board: tuple[Cell | None, ...]
    respins_left: int


@dataclass(frozen=True, slots=True)
class BonusResult:
    """Lo que paga un bonus terminado (en puntos de la apuesta del bonus)."""

    coin_points: int
    multiplier: int
    jackpots: tuple[str, ...]
    jackpot_values: tuple[int, ...]
    coins: int
    spins: int

    @property
    def jackpot_points(self) -> int:
        """Lo que suman los botes ganados."""
        return sum(self.jackpot_values)

    @property
    def points(self) -> int:
        """Total: monedas por el multiplicador más los botes (que no se multiplican)."""
        return self.coin_points * self.multiplier + self.jackpot_points


@dataclass(slots=True)
class BonusGame:
    """Partida de bonus en curso.

    Args:
        kind: Tipo de bonus (`BonusKind`).
        stake: Apuesta a la que se juega.
        mini, major: Valor de los botes del jugador al empezar, en puntos.
    """

    kind: str
    stake: int
    mini: int
    major: int
    board: list[Cell | None] = field(default_factory=lambda: [None] * CELLS)
    respins_left: int = RESET_VALUE
    reset_value: int = RESET_VALUE
    multiplier: int = 1
    spins: int = 0
    maximized: bool = False
    history: list[BonusStep] = field(default_factory=list)

    @classmethod
    def start(
        cls,
        trigger: Trigger,
        *,
        mini: int,
        major: int,
        rng: random.Random,
        seed_cells: Iterable[int] = (),
    ) -> BonusGame:
        """Empieza un bonus con sus monedas iniciales.

        Args:
            seed_cells: Casillas preferidas para las monedas iniciales (las
                de la tirada que lo disparó); el resto, al azar.
        """
        game = cls(kind=trigger.kind, stake=trigger.stake, mini=mini, major=major)
        rules = BONUS_RULES[trigger.kind]
        count = int(_pick(rng, rules.start))  # type: ignore[arg-type]
        preferred = [i for i in dict.fromkeys(seed_cells) if 0 <= i < CELLS]
        free = [i for i in range(CELLS) if i not in preferred]
        rng.shuffle(free)
        for index in (preferred + free)[:count]:
            game.board[index] = coin(rng, _pick(rng, rules.tiers))  # type: ignore[arg-type]
        return game

    @property
    def coins(self) -> int:
        """Monedas fijas en la rejilla."""
        return sum(1 for cell in self.board if cell is not None and cell.is_coin)

    @property
    def coin_points(self) -> int:
        """Suma de las monedas fijas."""
        return sum(cell.value for cell in self.board if cell is not None and cell.is_coin)

    @property
    def finished(self) -> bool:
        """Sin tiradas o con la rejilla llena de monedas."""
        return self.respins_left <= 0 or self.coins >= CELLS

    def jackpots(self) -> tuple[str, ...]:
        """Botes alcanzados con las monedas actuales."""
        won = []
        if self.coins >= MINI_COINS:
            won.append("mini")
        if self.coins >= MAJOR_COINS:
            won.append("major")
        if self.coins >= GRAND_COINS:
            won.append("grand")
        return tuple(won)

    def jackpot_value(self, name: str) -> int:
        """Valor de un bote en este bonus, en puntos."""
        return {"mini": self.mini, "major": self.major, "grand": GRAND}[name]

    def _land(self, rng: random.Random) -> tuple[Cell, str | None]:
        rules = BONUS_RULES[self.kind]
        kind = _pick(rng, BONUS_LAND_WEIGHTS)
        mystery = None
        if kind == Kind.MYSTERY_RED:
            mystery, kind = Kind.MYSTERY_RED, _pick(rng, MYSTERY_RED_WEIGHTS)
        elif kind == Kind.MYSTERY_BLUE:
            mystery, kind = Kind.MYSTERY_BLUE, _pick(rng, MYSTERY_BLUE_WEIGHTS)
        if kind == Kind.COIN:
            return coin(rng, _pick(rng, rules.tiers)), mystery  # type: ignore[arg-type]
        if kind == Kind.TICKET:
            return Cell(Kind.TICKET, value=int(_pick(rng, TICKET_WEIGHTS))), mystery  # type: ignore[arg-type]
        if kind == Kind.INSTANT:
            return Cell(Kind.INSTANT, value=int(_pick(rng, INSTANT_WEIGHTS))), mystery  # type: ignore[arg-type]
        return Cell(str(kind)), mystery

    def step(self, rng: random.Random) -> BonusStep:
        """Juega una tirada del bonus.

        Los modificadores de la tirada anterior desaparecen; en cada casilla
        vacía puede caer algo. Si cae cualquier cosa, el contador vuelve al
        valor de reinicio.

        Raises:
            RuntimeError: Si el bonus ya ha terminado.
        """
        if self.finished:
            raise RuntimeError("El bonus ya ha terminado.")
        for index, cell in enumerate(self.board):
            if cell is not None and not cell.is_coin:
                self.board[index] = None
        land = BONUS_RULES[self.kind].land
        landings: list[Landing] = []
        for index in range(CELLS):
            if self.board[index] is None and rng.random() * 1000 < land:
                cell, mystery = self._land(rng)
                self.board[index] = cell
                landings.append(Landing(index, cell, mystery))

        added = sum(x.cell.value for x in landings if x.cell.kind == Kind.TICKET)
        extra = sum(1 for x in landings if x.cell.kind == Kind.EXTRA)
        maximized = any(x.cell.kind == Kind.MAXIMIZER for x in landings)
        instant = 10
        for landing in landings:
            if landing.cell.kind == Kind.INSTANT:
                instant = instant * landing.cell.value // 10
        if instant != 10:
            for index, cell in enumerate(self.board):
                if cell is not None and cell.is_coin:
                    self.board[index] = Cell(
                        Kind.COIN, tier=cell.tier, value=cell.value * instant // 10
                    )
        self.multiplier += added
        self.reset_value = min(MAX_RESET_VALUE, self.reset_value + extra)
        if maximized:
            self.mini, self.major, self.maximized = MINI_MAX, MAJOR_MAX, True
        self.respins_left = self.reset_value if landings else self.respins_left - 1
        self.spins += 1
        step = BonusStep(
            landings=tuple(landings),
            multiplier_added=added,
            instant=instant,
            extra=extra,
            maximized=maximized,
            board=tuple(self.board),
            respins_left=self.respins_left,
        )
        self.history.append(step)
        return step

    def result(self) -> BonusResult:
        """Premio del bonus con lo que hay ahora en la rejilla."""
        jackpots = self.jackpots()
        return BonusResult(
            coin_points=self.coin_points,
            multiplier=self.multiplier,
            jackpots=jackpots,
            jackpot_values=tuple(self.jackpot_value(name) for name in jackpots),
            coins=self.coins,
            spins=self.spins,
        )

    def play_out(self, rng: random.Random) -> BonusResult:
        """Juega hasta el final de golpe (al cerrar la máquina o en pruebas)."""
        while not self.finished:
            self.step(rng)
        return self.result()


def reset_won_jackpots(meters: Meters, jackpots: Iterable[str]) -> None:
    """Devuelve a su valor inicial los botes que se acaban de ganar."""
    for name in jackpots:
        if name == "mini":
            meters.mini = MINI_BASE
        elif name == "major":
            meters.major = MAJOR_BASE


# -- Simulación ---------------------------------------------------------------------


@dataclass(slots=True)
class Simulation:
    """Resultado de `simulate`, todo en puntos por tirada pagada."""

    spins: int = 0
    ways: int = 0
    collect: int = 0
    bonus: int = 0
    bonuses: dict[str, int] = field(default_factory=lambda: {k: 0 for k in BonusKind.ALL})
    grands: int = 0

    @property
    def rtp(self) -> float:
        """Retorno total (1,0 = se devuelve todo lo apostado)."""
        return (self.ways + self.collect + self.bonus) / (self.spins * POINTS_PER_STAKE)

    def part(self, name: str) -> float:
        """Retorno de una parte: `"ways"`, `"collect"` o `"bonus"`."""
        return getattr(self, name) / (self.spins * POINTS_PER_STAKE)

    @property
    def bonus_every(self) -> float:
        """Cada cuántas tiradas sale un bonus."""
        total = sum(self.bonuses.values())
        return self.spins / total if total else float("inf")


def simulate(spins: int, rng: random.Random) -> Simulation:
    """Juega `spins` tiradas a apuesta fija y suma lo que devuelve cada parte.

    Los bonus se juegan a su apuesta (la media de sus maletines, que aquí es la
    misma) y los botes se ganan y se reinician como en el juego real.
    """
    stake = POINTS_PER_STAKE
    meters = Meters()
    sim = Simulation()
    for _ in range(spins):
        spin = spin_base(rng)
        sim.spins += 1
        sim.ways += spin.ways_points
        sim.collect += spin.collect_points
        trigger = fill_cases(meters, spin, stake, rng)
        if trigger is None:
            continue
        sim.bonuses[trigger.kind] += 1
        game = BonusGame.start(trigger, mini=meters.mini, major=meters.major, rng=rng)
        result = game.play_out(rng)
        reset_won_jackpots(meters, result.jackpots)
        sim.bonus += result.points
        sim.grands += "grand" in result.jackpots
    return sim


# -- Máquinas -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Theme:
    """Una máquina: mismas reglas, otros dibujos y otros nombres.

    Attributes:
        key: Clave estable (también el nombre del comando y el prefijo de los
            dibujos en `assets/botes`).
        title: Nombre de la máquina.
        symbols: Emoji y nombre de los símbolos normales, de menos a más valor.
        wild, collect: Emoji y nombre del comodín y del recogedor.
        bonus_names: Nombre de cada bonus (`BonusKind`).
    """

    key: str
    title: str
    emoji: str
    symbols: tuple[tuple[str, str], ...]
    wild: tuple[str, str]
    collect: tuple[str, str]
    bonus_names: dict[str, str]


THEMES: dict[str, Theme] = {
    theme.key: theme
    for theme in (
        Theme(
            key="volcan",
            title="Volcán",
            emoji="🌋",
            symbols=(
                ("🍌", "Plátano"),
                ("🥥", "Coco"),
                ("💚", "Olivino"),
                ("🖤", "Obsidiana"),
                ("🦎", "Lagarto tizón"),
                ("🦜", "Guacamayo"),
            ),
            wild=("🔥", "Fuego"),
            collect=("🌋", "Volcán"),
            bonus_names={
                BonusKind.GREEN: "Ceniza",
                BonusKind.BLUE: "Magma",
                BonusKind.RED: "Lava",
                BonusKind.GRAND: "¡ERUPCIÓN!",
            },
        ),
        Theme(
            key="olimpo",
            title="Olimpo",
            emoji="⚡",
            symbols=(
                ("🦉", "Tetradracma"),
                ("🥣", "Kílix"),
                ("🏺", "Ánfora"),
                ("⚱️", "Crátera"),
                ("🪖", "Casco corintio"),
                ("🪙", "Estátera de oro"),
            ),
            wild=("🗿", "Zeus"),
            collect=("⚡", "Rayo de Zeus"),
            bonus_names={
                BonusKind.GREEN: "Hermes",
                BonusKind.BLUE: "Poseidón",
                BonusKind.RED: "Ares",
                BonusKind.GRAND: "¡FURIA DE ZEUS!",
            },
        ),
        Theme(
            key="filon",
            title="Filón",
            emoji="⛏️",
            symbols=(
                ("🔦", "Linterna"),
                ("🪏", "Pala"),
                ("🪨", "Mena de oro"),
                ("🥈", "Lingote de plata"),
                ("🥇", "Lingote de oro"),
                ("💎", "Diamante"),
            ),
            wild=("⛏️", "Pico"),
            collect=("🛒", "Vagoneta"),
            bonus_names={
                BonusKind.GREEN: "Cobre",
                BonusKind.BLUE: "Plata",
                BonusKind.RED: "Oro",
                BonusKind.GRAND: "¡VETA MADRE!",
            },
        ),
    )
}
