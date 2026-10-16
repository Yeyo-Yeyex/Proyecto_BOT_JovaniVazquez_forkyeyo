"""Las máquinas de Botes con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/botes/escena.html`) pinta encima del fondo de cada máquina (el de
Pillow, `HoldWinRenderer.scene_backdrop`, pintado una vez) los rodillos, las placas de los
botes, los maletines, las luces del marco, los efectos de recogida, el marcador y los
carteles (estos dos, comunes con la clásica: `assets/tragas.js`). Python decide todo lo que
se ve, fotograma a fotograma, y la escena solo pinta: el ritmo vive aquí.

**El ritmo de una tirada** (`base_states`) depende de lo que hay en juego, como en la clásica:

1. Sin nada que prometa (~1,5 s): un tirón hacia arriba, los cinco rodillos a toda
   velocidad y paradas de izquierda a derecha, un poco movidas en cada tirada. Cada moneda
   que cae vuela a su maletín en cuanto para su rodillo, y la barra sube: el progreso se ve
   en todas las tiradas sin alargarlas (vuela mientras giran los demás). Un maletín a punto
   de llenarse late.
2. **Rescate** (`Tease.kind == "rescue"`): monedas que suman ×1 o más en los rodillos 1 a 4
   y ningún recogedor en el 1. El quinto sigue a tope con las luces rojas y frena despacio:
   ¿llega el recogedor? Con ×3 o más, más largo y el resto de la máquina se apaga. Si no
   llega, a veces se ve pasar uno justo antes de parar.
3. **Doble** (`"double"`, `BaseSpin.anticipation`): recogedor en el 1 y monedas a la vista;
   el quinto puede traer el segundo, que lo dobla. Luces ámbar en estrella.
4. **Camino del cinco** (`"ways"`): el símbolo más alto en los cuatro primeros rodillos.
   Luces azules redondas.
5. Si paga: las casillas que pagan se iluminan, el marcador suma cada premio por ways y
   luego la recogida moneda a moneda, con el efecto de la máquina (lava, oro o rayos) del
   recogedor a cada moneda; con dos recogedores, el segundo pase dobla. Al cruzar ×10, ×25
   y ×50 sale el cartel y sube de nivel (GRAN PREMIO, MEGAPREMIO, ÉPICO).

**El bonus** (`bonus_step_states`): las monedas se quedan y cada casilla vacía es un rodillo
pequeño que para de una en una. En la última tirada todo va más despacio y en rojo. Si
faltan una o dos monedas para el MINI, el MAJOR o el GRAND, las últimas casillas frenan
despacio con el resto apagado. Los misteriosos tiemblan y se giran; los tickets vuelan al
multiplicador; el ×1,5 sube el valor de cada moneda delante de los ojos. Al acabar, el
recuento: moneda a moneda al marcador, luego el multiplicador y los botes, con el cartel
subiendo de nivel. La entrada al bonus (`bonus_intro_states`) vacía la rejilla, deja las
monedas del bonus y enseña su nombre.

El último fotograma de cada GIF es el PNG que se queda en el mensaje y sale de la misma
función que el dibujo quieto (`still_state`, `bonus_still_state`, `bonus_end_state`).

Todo es determinista: lo que parece azar (las paradas movidas, los símbolos que pasan
girando, las monedas que caen) sale de la propia jugada, así que pintar antes del clic y al
pulsar dan los mismos bytes (`base_key`, `bonus_key`).

La misma escena se pinta de tres maneras, de mejor a peor: Node con Skia, Chromium y el
`HoldWinRenderer` de Pillow de antes (sin el ritmo nuevo). El primer fallo de cada una se
avisa en el log y desde entonces se usa la siguiente: la máquina nunca se queda sin imagen.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import io
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.economy import format_amount
from bot.services.hold_win import (
    CASE_SIZE,
    CELLS,
    GRAND_COINS,
    MAJOR_COINS,
    MINI_COINS,
    POINTS_PER_STAKE,
    REELS,
    ROWS,
    BaseSpin,
    BonusGame,
    BonusResult,
    BonusStep,
    Cell,
    Kind,
    Tier,
    cell_index,
    evaluate_ways,
    random_filler,
    to_amount,
    win_tier,
)
from bot.services.hold_win_render import (
    ASSETS,
    CELL,
    FOOTER_H,
    FOOTER_Y,
    GRID_Y,
    HEADER_H,
    HEADER_Y,
    HEIGHT,
    MARGIN,
    STYLES,
    WIDTH,
    Banner,
    BonusPanel,
    HoldWinRenderer,
    Media,
    Panel,
    _bonus_filler,
    _shown,
    cell_tile_png,
    cell_xy,
    short_amount,
)
from bot.services.node_scene import NodeScene, patch_png
from bot.services.slots_scene import (
    BANNER_POP,
    BRAKE_POWER,
    EPIC_RAYS,
    RAY_SPIN,
    RAY_STEP_MS,
    SLOW_BOUNCE_MS,
    SLOW_OVERSHOOT,
    SLOW_POWER,
    SPEED,
    Track,
    _round10,
    _seed,
    _smooth,
)
from bot.utils.gif import local_palette_gif

SCENE = ASSETS / "escena.html"

# -- Medidas: las del dibujo de Pillow (el fondo se reusa tal cual); la escena usa las mismas --

#: Tamaño de la imagen que se manda. La escena pinta a WIDTH × HEIGHT (382 × 469, la medida
#: lógica) a la escala `2 × OUT_W / WIDTH` y reduce a la mitad justa: 408 de ancho, como la
#: clásica.
OUT_W = 408
OUT_H = round(HEIGHT * OUT_W / WIDTH)
#: Bombillas del marco de la rejilla (la 0, arriba a la izquierda; siguen en el sentido del
#: reloj). La escena las reparte por el marco de cada máquina.
BULBS = 40
#: Placas de los botes (arriba) y maletines (abajo): tres columnas de `PANEL_W`.
PANEL_W = (WIDTH - 2 * MARGIN - 16) // 3
PANEL_X = tuple(MARGIN + i * (PANEL_W + 8) for i in range(3))
#: Marcador del premio: sobre los maletines (o el pie del bonus) mientras cuenta.
METER_BOX = (36, FOOTER_Y - 7, WIDTH - 72, 60)
#: Dónde se ve el multiplicador del bonus y el primer rombo de las tiradas (centros).
MULT_XY = (MARGIN + 164, FOOTER_Y + FOOTER_H / 2)
DIAMOND_X0, DIAMOND_PITCH = MARGIN + 20, 20

# -- Ritmo (milisegundos; múltiplos de 10, la unidad del GIF) -------------------------------------

FRAME_MS = 50
CRUISE_FRAME_MS = 70
SLOW_FRAME_MS = 120
SLOW_STEP_CELLS = 0.3
FINAL_FRAME_MS = 60_000
#: Cuándo toca fondo cada rodillo sin nada en juego, y cuánto se mueve de una tirada a otra.
LAND_MS = (600, 780, 960, 1140, 1360)
JITTER_MS = 30
FLASH_MS = 180
#: Lo que tarda una moneda en volar a su maletín (o una ficha a su placa).
TOKEN_MS = 300
TOKEN_DELAY_MS = 60
#: Un maletín late si le faltan estas monedas o menos (o la décima parte, si es más).
NEAR_CASE = 3
PULSE_MS = 360


@dataclass(frozen=True, slots=True)
class TeaseRule:
    """Una espera del quinto rodillo: cuánto sigue a tope, cuánto frena y cómo se ve."""

    cruise: int
    slow: int
    color: str
    shape: str
    dim: float = 0.0


GOLD = "#deb246"
AMBER = "#ffd65c"
BLUE = "#60c4ff"
RED = "#ec382c"
WHITE = "#ffffff"

TEASES: dict[str, TeaseRule] = {
    "ways": TeaseRule(400, 1000, BLUE, "round"),
    "double": TeaseRule(500, 1100, AMBER, "star"),
    "rescue": TeaseRule(700, 1300, RED, "diamond"),
    "rescue_big": TeaseRule(1000, 1700, RED, "diamond", dim=0.6),
}
#: Puntos de las monedas a la vista (sin el quinto rodillo) que hacen esperar al rescate.
RESCUE_POINTS = 100
BIG_RESCUE_POINTS = 300
#: Símbolo que hace esperar al camino del cinco (el más alto).
WAYS_SYMBOL = 5
#: Si el rescate o el doble no llegan, se ve pasar un recogedor así de a menudo.
MISS_SHOW = 0.6
DIM_MS = 300

# -- Premio ----------------------------------------------------------------------------------------

BLINK_MS = 100
#: Cada premio por ways: lo que tarda en sumarse al marcador.
WAYS_STEP_MS = 280
MAX_WAYS_STEPS = 3
#: Recogida: fotogramas de cada moneda (y su duración) mientras quedan pocas o muchas.
COLLECT_MS = (60, 40)
COLLECT_SLOW_COINS = 6
ROLLUP_FRAME_MS = 70
SETTLE_MS = 420
TIER_NAMES = {1: "big", 2: "mega", 3: "epic"}
COINS = {1: 0, 2: 7, 3: 14}
SPARKLES = 9
COIN_SPEED = (0.22, 0.42)

# -- Bonus -----------------------------------------------------------------------------------------

#: Primera parada y separación entre casillas (más en la última tirada).
BONUS_LAND_MS = 520
BONUS_STAGGER_MS = 55
LAST_STAGGER_MS = 85
BONUS_JITTER_MS = 20
#: Persecución de un bote: lo que sigue a tope y frena cada casilla que lo decide.
CHASE_CRUISE_MS = 500
CHASE_SLOW_MS = 1200
CHASE_DIM = 0.55
POP = (1.3, 1.12, 1.0)
#: Monedas del bonus que dan cada bote.
GOALS = (MINI_COINS, MAJOR_COINS, GRAND_COINS)


# -- Luces -----------------------------------------------------------------------------------------

Bulbs = dict[str, Any]


def _bulbs(shape: str, lit: Sequence[str | None]) -> Bulbs:
    return {"shape": shape, "lit": list(lit)}


CALM = _bulbs("round", [GOLD if b % 2 == 0 else None for b in range(BULBS)])
DARK = _bulbs("round", [None] * BULBS)
WIN_SHAPE = {0: "round", 1: "star", 2: "star", 3: "burst"}
WIN_COLORS = {0: (AMBER,), 1: (AMBER,), 2: (AMBER, WHITE), 3: (AMBER, WHITE)}
WIN_BLINK_MS = {0: 240, 1: 180, 2: 120, 3: 120}


def chase_bulbs(t: float) -> Bulbs:
    """Persecución mientras giran: una de cada tres avanza, en grupos ámbar y azul."""
    step = int(t // 90)
    lit = []
    for bulb in range(BULBS):
        offset = bulb - step
        color = AMBER if (offset // 3) % 2 == 0 else BLUE
        lit.append(color if offset % 3 == 0 else None)
    return _bulbs("round", lit)


def tension_bulbs(t: float, color: str = RED, shape: str = "diamond") -> Bulbs:
    """Tensión: dos de cada cuatro, el doble de rápido."""
    step = int(t // 50)
    return _bulbs(shape, [color if (b - step) % 4 < 2 else None for b in range(BULBS)])


def win_bulbs(tier: int, color: int = 0) -> Bulbs:
    colors = WIN_COLORS[tier]
    return _bulbs(WIN_SHAPE[tier], [colors[color % len(colors)]] * BULBS)


def blink_bulbs(tier: int, t: float) -> Bulbs:
    half = WIN_BLINK_MS[tier]
    if int(t // half) % 2:
        return DARK
    return win_bulbs(tier, int(t // (2 * half)))


# -- Casillas --------------------------------------------------------------------------------------


def cell_key(cell: Cell | None, stake: int) -> str:
    """Lo que la escena sabe pintar de una casilla, en una cadena corta.

    `p0`…`p5` símbolos, `w` comodín, `c` recogedor, `mini`/`major` fichas de bote,
    `g|texto`, `b|texto`, `r|texto` monedas verde, azul y roja con su valor en Y$,
    `t|+×3` ticket, `x|+1` tirada extra, `i|×1,5` multiplicador inmediato, `X|MAX`
    maximizador, `?r`/`?b` misteriosos y `""` casilla vacía.
    """
    if cell is None:
        return ""
    kind = cell.kind
    if kind == Kind.PAY:
        return f"p{cell.symbol}"
    if kind == Kind.WILD:
        return "w"
    if kind == Kind.COLLECT:
        return "c"
    if kind == Kind.CHIP:
        return str(cell.symbol)
    if kind == Kind.COIN:
        assert cell.tier is not None
        return f"{'gbr'[cell.tier]}|{short_amount(to_amount(cell.value, stake))}"
    if kind == Kind.TICKET:
        return f"t|+×{cell.value}"
    if kind == Kind.EXTRA:
        return "x|+1"
    if kind == Kind.INSTANT:
        return f"i|×{_factor(cell.value)}"
    if kind == Kind.MAXIMIZER:
        return "X|MAX"
    if kind == Kind.MYSTERY_RED:
        return "?r"
    if kind == Kind.MYSTERY_BLUE:
        return "?b"
    raise ValueError(f"Casilla sin dibujo: {kind}")


def _factor(tenths: int) -> str:
    return f"{tenths / 10:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def _center(index: int) -> tuple[float, float]:
    x, y = cell_xy(index)
    return x + CELL / 2, y + CELL / 2


def _case_xy(tier: int) -> tuple[float, float]:
    """Centro del icono de un maletín (adonde vuelan las monedas)."""
    return PANEL_X[tier] + 21, FOOTER_Y + 23


def _plaque_xy(index: int) -> tuple[float, float]:
    return PANEL_X[index] + PANEL_W / 2, HEADER_Y + HEADER_H / 2


def _meter_xy() -> tuple[float, float]:
    x, y, w, h = METER_BOX
    return x + w / 2, y + h / 2


def _fly(
    a: tuple[float, float], b: tuple[float, float], u: float, *, lift: float = 30
) -> tuple[float, float]:
    """Punto de un vuelo de `a` a `b` en arco, con `u` de 0 a 1 (suave al salir y al llegar)."""
    e = _smooth(u)
    return (
        round(a[0] + (b[0] - a[0]) * e, 1),
        round(a[1] + (b[1] - a[1]) * e - lift * 4 * e * (1 - e), 1),
    )


def _grid_seed(*parts: object) -> random.Random:
    return _seed("botes", *parts)


# -- Paneles ---------------------------------------------------------------------------------------


def _plaques(
    values: Sequence[int],
    *,
    glow: Sequence[float] = (0.0, 0.0, 0.0),
    won: Sequence[bool] = (False, False, False),
    maximized: bool = False,
) -> dict[str, Any]:
    labels = ("MINI", "MAJOR", "GRAND")
    return {
        "plaques": [
            [label, f"{short_amount(value)} Y$", round(g, 3), bool(w)]
            for label, value, g, w in zip(labels, values, glow, won, strict=True)
        ],
        "max": maximized,
    }


def _header(panel: Panel | BonusPanel, **extra: Any) -> dict[str, Any]:
    if isinstance(panel, BonusPanel):
        extra.setdefault("won", tuple(name in panel.won for name in ("mini", "major", "grand")))
        extra.setdefault("maximized", panel.maximized)
    return _plaques((panel.mini, panel.major, panel.grand), **extra)


def _cases(
    counts: Sequence[int], *, full: Sequence[bool] = (False, False, False), t: float = 0.0
) -> dict[str, Any]:
    """Pie del juego base: los tres maletines. Los que están a punto de llenarse laten."""
    beat = int(t // (PULSE_MS / 2)) % 2 == 0
    cases = []
    for tier, (count, is_full) in enumerate(zip(counts, full, strict=True)):
        size = CASE_SIZE[Tier(tier)]
        shown = min(count, size)
        near = size - shown <= max(NEAR_CASE, size // 10)
        pulse = (1.0 if beat else 0.4) if (near or is_full) else 0.0
        text = "¡LLENO!" if is_full else f"{shown}/{size}"
        cases.append([round(shown / size, 4), text, pulse, bool(is_full)])
    return {"cases": cases}


def _bonus_footer(
    panel: BonusPanel,
    *,
    left: int | None = None,
    reset: int | None = None,
    refill: float = 0.0,
    mult: int | None = None,
    mult_pop: float = 1.0,
    coins: int | None = None,
    tense: float = 0.0,
) -> dict[str, Any]:
    """Pie del bonus: rombos de las tiradas, multiplicador y monedas hacia los botes."""
    return {
        "bonus": {
            "left": panel.respins_left if left is None else left,
            "reset": panel.reset_value if reset is None else reset,
            "refill": round(refill, 3),
            "mult": f"×{panel.multiplier if mult is None else mult}",
            "multPop": round(mult_pop, 3),
            "coins": panel.coins if coins is None else coins,
            "tense": round(tense, 3),
        }
    }


def _meter(points: int, final: int, stake: int, *, label: str, glow: float = 0.0, alpha=1.0):
    # `fit` es el importe final: el marcador elige la letra con él y no cambia de tamaño.
    return {
        "label": label,
        "text": format_amount(to_amount(points, stake)),
        "fit": format_amount(to_amount(final, stake)),
        "glow": round(glow, 3),
        "alpha": round(alpha, 3),
    }


def _empty_state(**extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "reels": None,
        "blur": [0.0] * REELS,
        "flash": [0.0] * REELS,
        "board": None,
        "spin": [],
        "fade": [],
        "pop": [],
        "lit": [],
        "flip": [],
        "shake": [],
        "shade": 0.0,
        "dim": 0.0,
        "keep": [],
        "focus": None,
        "aura": [],
        "glow": 0.0,
        "glowColor": RED,
        "bulbs": CALM,
        "beam": None,
        "fly": [],
        "header": None,
        "footer": None,
        "meter": None,
        "banner": None,
        "coins": [],
        "sparkles": [],
    }
    state.update(extra)
    return state


# -- Juego base: qué espera promete --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tease:
    """La espera del quinto rodillo (`None`, `"ways"`, `"double"`, `"rescue"`, `"rescue_big"`)
    y las casillas de los cuatro primeros que la provocan (laten mientras espera)."""

    kind: str | None
    cells: tuple[int, ...] = ()

    @property
    def collect(self) -> bool:
        """Si espera a un recogedor (rescate o doble)."""
        return self.kind in ("rescue", "rescue_big", "double")


def _first_reels(index: int) -> bool:
    return index < cell_index(REELS - 1, 0)


def tease_of(spin: BaseSpin) -> Tease:
    """Qué espera el quinto rodillo, de más a menos importante."""
    grid = spin.grid
    first = [cell_index(0, r) for r in range(ROWS) if grid[cell_index(0, r)].kind == Kind.COLLECT]
    coins = [(i, c) for i, c in spin.coins if _first_reels(i)]
    points = sum(c.value for _i, c in coins)
    if not first and len(coins) >= 2 and points >= RESCUE_POINTS:
        kind = "rescue_big" if points >= BIG_RESCUE_POINTS else "rescue"
        return Tease(kind, tuple(i for i, _c in coins))
    if spin.anticipation:
        return Tease("double", tuple(first + [i for i, _c in coins]))
    # El quinto rodillo tapado: ¿el símbolo más alto ya llena los cuatro primeros?
    blank = Cell(Kind.CHIP, symbol="mini")
    covered = [cell if _first_reels(i) else blank for i, cell in enumerate(grid)]
    for win in evaluate_ways(covered):
        if win.symbol == WAYS_SYMBOL and win.reels == REELS - 1:
            return Tease("ways", tuple(sorted(win.cells)))
    return Tease(None)


def tease_hit(spin: BaseSpin, tease: Tease) -> bool:
    """Si la espera acaba bien: llega el recogedor, o el cinco de fila."""
    if tease.collect:
        return any(spin.grid[cell_index(REELS - 1, r)].kind == Kind.COLLECT for r in range(ROWS))
    if tease.kind == "ways":
        return any(w.symbol == WAYS_SYMBOL and w.reels == REELS for w in spin.wins)
    return False


def tracks_for(spin: BaseSpin, tease: Tease) -> list[Track]:
    """El viaje de cada rodillo (`stop` 1: la fila 0 enseña `strip[1]`)."""
    rng = _grid_seed("land", spin.grid)
    lands = [_round10(ms + rng.randint(-JITTER_MS, JITTER_MS)) for ms in LAND_MS]
    tracks = [Track(1, lands[r]) for r in range(REELS - 1)]
    if tease.kind is None:
        tracks.append(Track(1, lands[-1]))
    else:
        rule = TEASES[tease.kind]
        tracks.append(
            Track(
                1,
                lands[-2] + rule.cruise + rule.slow,
                brake=rule.slow,
                power=SLOW_POWER,
                overshoot=SLOW_OVERSHOOT,
                bounce=SLOW_BOUNCE_MS,
            )
        )
    return tracks


def frame_times(tracks: Sequence[Track]) -> list[int]:
    """Cuándo empieza cada fotograma; el último, con todo parado.

    Como en la clásica: a 20 por segundo, a `CRUISE_FRAME_MS` mientras todos los que giran van
    a tope y, mientras alguno frena despacio, cada fotograma dura lo que tarda en avanzar
    `SLOW_STEP_CELLS` (cámara lenta con fotogramas largos, no con más fotogramas).
    """
    end = _round10(max(track.end for track in tracks))
    slow = [track for track in tracks if track.moving and track.power != BRAKE_POWER]
    times: list[int] = []
    t = 0
    while t < end:
        times.append(t)
        step = FRAME_MS
        spinning = [track for track in tracks if track.moving and t < track.land]
        if spinning and all(track.speed(t) >= SPEED for track in spinning):
            brake = min(track.braking_from for track in spinning)
            step = max(FRAME_MS, min(CRUISE_FRAME_MS, _round10(brake - t)))
        braking = [track for track in slow if track.braking_from <= t < track.end]
        if braking and len(braking) == len([s for s in spinning if s in slow]) == len(spinning):
            speed = max(track.speed(t) for track in braking)
            step = SLOW_FRAME_MS if speed <= 0 else SLOW_STEP_CELLS / speed
            step = min(SLOW_FRAME_MS, max(FRAME_MS, _round10(step)))
        t += step
    if times and end - times[-1] < FRAME_MS // 2 and len(times) > 1:
        times.pop()
    times.append(end)
    return times


def _strip_length(track: Track) -> int:
    return math.ceil(track.position(WIND_PEAK)) + ROWS + 2


#: Momento del tirón en que el rodillo está más arriba (el de más posición).
WIND_PEAK = 75


def base_strips(theme: str, spin: BaseSpin, tease: Tease, tracks: Sequence[Track], stake: int):
    """Las tiras de cada rodillo: `[asomo, las 4 de la tirada, lo que pasa girando…]`.

    La fila `r` enseña `strip[floor(p) + r]` (los índices bajos entran por arriba). Si el
    rescate o el doble no llegan, a veces el recogedor pasa por el quinto justo antes de
    parar (en `strip[ROWS + 1]`, la casilla que sale por abajo la última).
    """
    rng = _grid_seed("strips", theme, spin.grid)
    strips: list[list[str]] = []
    for reel, track in enumerate(tracks):
        finals = [spin.grid[cell_index(reel, row)] for row in range(ROWS)]
        fillers = [random_filler(rng, reel) for _ in range(_strip_length(track) - ROWS)]
        cells = [fillers[0], *finals, *fillers[1:]]
        strips.append([cell_key(cell, stake) for cell in cells])
    if tease.kind is not None and not tease_hit(spin, tease) and rng.random() < MISS_SHOW:
        passing = Cell(Kind.COLLECT) if tease.collect else Cell(Kind.PAY, symbol=WAYS_SYMBOL)
        strips[-1][ROWS + 1] = cell_key(passing, stake)
    return strips



def shows_slip(theme: str, spin: BaseSpin) -> bool:
    """Si el rescate o el doble no llegan y se ve pasar el recogedor de largo (para los logros).

    No depende de la apuesta: las tiras salen siempre de la misma semilla.
    """
    tease = tease_of(spin)
    if not tease.collect or tease_hit(spin, tease):
        return False
    strips = base_strips(theme, spin, tease, tracks_for(spin, tease), POINTS_PER_STAKE)
    return strips[-1][ROWS + 1] == "c"

# -- Juego base: los fotogramas --------------------------------------------------------------------


def filled_cases(before: Panel, spin: BaseSpin) -> tuple[bool, bool, bool]:
    """Los maletines que se llenan con esta tirada (se ven «¡LLENO!» al acabar)."""
    by_tier = spin.coins_by_tier()
    return tuple(  # type: ignore[return-value]
        before.cases[t][0] + by_tier[Tier(t)] >= CASE_SIZE[Tier(t)] for t in range(3)
    )


def win_cells(spin: BaseSpin) -> list[int]:
    """Las casillas que pagan: las de los ways y, si se recoge, recogedores y monedas."""
    cells: set[int] = set()
    for win in spin.wins:
        cells |= win.cells
    if spin.collectors:
        cells |= {i for i, _c in spin.coins}
        cells |= set(spin.collector_cells)
    return sorted(cells)


def still_state(
    spin: BaseSpin,
    *,
    stake: int,
    panel: Panel,
    highlight: bool | None = None,
    full: Sequence[bool] = (False, False, False),
) -> dict[str, Any]:
    """La máquina parada en `spin`: el último fotograma de la tirada y el PNG.

    Args:
        panel: Botes y maletines que se ven (tras la tirada).
        highlight: Si se iluminan las casillas que pagan; por defecto, si algo paga.
        full: Maletines que se acaban de llenar: se ven llenos con «¡LLENO!».
    """
    cells = win_cells(spin)
    if highlight is None:
        highlight = bool(cells)
    counts = [CASE_SIZE[Tier(t)] if full[t] else panel.cases[t][0] for t in range(3)]
    state = _empty_state(
        board=[cell_key(cell, stake) for cell in spin.grid],
        header=_header(panel),
        footer=_cases(counts, full=full),
        bulbs=win_bulbs(0) if highlight and cells else CALM,
    )
    if highlight and cells:
        state.update(lit=[[i, 1.0] for i in cells], shade=0.45, keep=cells)
    return state


def _token_frames(
    spin: BaseSpin, tracks: Sequence[Track], before: Panel, after: Panel
) -> list[tuple[float, int, str, tuple[float, float], tuple[float, float]]]:
    """Cada moneda (y ficha) que vuela: `(sale, a dónde, icono, desde, hasta)`."""
    flights = []
    for index, cell in enumerate(spin.grid):
        reel = index // ROWS
        start = tracks[reel].land + TOKEN_DELAY_MS
        if cell.is_coin:
            assert cell.tier is not None
            tier = int(cell.tier)
            flights.append((start, tier, "gbr"[tier], _center(index), _case_xy(tier)))
        elif cell.kind == Kind.CHIP:
            target = 0 if cell.symbol == "mini" else 1
            flights.append(
                (start, 3 + target, str(cell.symbol), _center(index), _plaque_xy(target))
            )
    return flights


def _spin_frames(
    theme: str, spin: BaseSpin, *, stake: int, before: Panel, after: Panel
) -> tuple[list[dict[str, Any]], list[Track], Tease]:
    tease = tease_of(spin)
    tracks = tracks_for(spin, tease)
    times = frame_times(tracks)
    flights = _token_frames(spin, tracks, before, after)
    # Las monedas del quinto rodillo aún vuelan cuando para: se espera a que lleguen.
    landed_all = max(start + TOKEN_MS for start, *_rest in flights) if flights else 0
    while times[-1] < landed_all:
        times.append(times[-1] + FRAME_MS)
    last = tracks[-1]
    rule = TEASES.get(tease.kind) if tease.kind else None
    tease_from = tracks[-2].land
    full = filled_cases(before, spin)
    chips = spin.chips
    states = []
    for index, t in enumerate(times):
        teasing = rule is not None and tease_from <= t < last.land
        beat = int((t - tease_from) // (PULSE_MS / 2)) % 2 == 0
        arrived = [0, 0, 0, 0, 0]
        fly = []
        for start, target, icon, a, b in flights:
            u = (t - start) / TOKEN_MS
            if u >= 1:
                arrived[target] += 1
            elif u >= 0:
                x, y = _fly(a, b, u)
                fly.append([x, y, icon, round(0.62 - 0.22 * u, 3), 1.0])
        counts = [before.cases[k][0] + arrived[k] for k in range(3)]
        shown_full = [full[k] and counts[k] >= CASE_SIZE[Tier(k)] for k in range(3)]
        # Las placas cambian cuando llega la última ficha de su bote.
        mini = after.mini if chips["mini"] and arrived[3] >= chips["mini"] else before.mini
        major = after.major if chips["major"] and arrived[4] >= chips["major"] else before.major
        chip_glow = [
            1.0
            if chips[name] and 0 < arrived[3 + k] and t - _last_arrival(flights, 3 + k) < 200
            else 0.0
            for k, name in enumerate(("mini", "major"))
        ]
        dim = 0.0
        if teasing and rule.dim:
            dim = rule.dim * min(1.0, (t - tease_from) / DIM_MS)
        state = _empty_state(
            reels=[round(track.position(t), 4) for track in tracks],
            blur=[round(min(1.0, track.speed(t) / SPEED), 3) for track in tracks],
            flash=[
                round(max(0.0, 1 - (t - track.land) / FLASH_MS), 3) if t >= track.land else 0.0
                for track in tracks
            ],
            bulbs=tension_bulbs(t, rule.color, rule.shape) if teasing else chase_bulbs(t),
            glow=(1.0 if beat else 0.55) if teasing else 0.0,
            glowColor=rule.color if rule else RED,
            focus=REELS - 1 if teasing else None,
            dim=round(dim, 3),
            keep=list(tease.cells) if teasing else [],
            lit=[[i, 1.0 if beat else 0.5] for i in tease.cells] if teasing else [],
            fly=fly,
            header=_plaques((mini, major, before.grand), glow=(*chip_glow, 0.0)),
            footer=_cases(counts, full=shown_full, t=t),
        )
        if index + 1 < len(times):
            state["ms"] = times[index + 1] - t
        states.append(state)
    return states, tracks, tease


def _last_arrival(flights: Sequence[tuple], target: int) -> float:
    ends = [start + TOKEN_MS for start, tgt, *_rest in flights if tgt == target]
    return max(ends) if ends else 0.0


def _coins_rain(rng: random.Random, count: int) -> list[tuple[float, float, float, float]]:
    return [
        (
            rng.uniform(MARGIN, WIDTH - MARGIN),
            rng.uniform(-HEIGHT, 0),
            rng.uniform(*COIN_SPEED),
            rng.uniform(0, 4),
        )
        for _ in range(count)
    ]


@dataclass(slots=True)
class _Count:
    """La cuenta de un premio: añade fotogramas con el marcador, el cartel y la lluvia."""

    stake: int
    final: int
    label: str
    seed: random.Random
    base: dict[str, Any]
    states: list[dict[str, Any]] = field(default_factory=list)
    points: int = 0
    t: int = 0
    tier_since: dict[int, int] = field(default_factory=dict)
    rain: list[tuple[float, float, float, float]] = field(default_factory=list)
    sparkles: list[tuple[float, float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rain = _coins_rain(self.seed, COINS[3])
        self.sparkles = [
            (
                self.seed.uniform(MARGIN, WIDTH - MARGIN),
                self.seed.uniform(GRID_Y, FOOTER_Y),
                self.seed.uniform(0, 1),
            )
            for _ in range(SPARKLES)
        ]

    def add(self, ms: int, *, glow: float = 0.0, settle: float | None = None, **extra: Any):
        tier = win_tier(self.points)
        state = {**self.base, **extra}
        state["meter"] = _meter(self.points, self.final, self.stake, label=self.label, glow=glow)
        state["bulbs"] = blink_bulbs(tier, self.t)
        if tier:
            index = len(self.states)
            self.tier_since.setdefault(tier, index)
            age = index - self.tier_since[tier]
            spin = RAY_SPIN * (self.t // RAY_STEP_MS * RAY_STEP_MS)
            if settle is not None:
                # Los rayos acaban en una posición igual a la de la imagen quieta.
                end = math.ceil(spin / (360 / EPIC_RAYS)) * (360 / EPIC_RAYS)
                spin += (end - spin) * _smooth(settle)
            state["banner"] = {
                "tier": TIER_NAMES[tier],
                "scale": BANNER_POP[age] if age < len(BANNER_POP) else 1.0,
                "spin": round(spin % 360, 2),
                "flash": int(self.t // 180) % 2,
            }
            since = age * ROLLUP_FRAME_MS
            fade = 1.0 if settle is None else 1 - settle
            state["coins"] = [
                [
                    round(x, 1),
                    round((y0 + v * since) % (HEIGHT + 60) - 30, 1),
                    int(phase + since / 70) % 4,
                    round(fade, 3),
                ]
                for x, y0, v, phase in self.rain[: COINS[tier]]
            ]
            if tier == 3:
                state["sparkles"] = [
                    [
                        round(x, 1),
                        round(y, 1),
                        round(abs(math.sin(math.pi * (p + self.t / 500))) * 2) / 2,
                    ]
                    for x, y, p in self.sparkles
                ]
        state["ms"] = ms
        self.states.append(state)
        self.t += ms
        return state

    def roll(self, to: int, frames: int, **extra: Any) -> None:
        """Sube el marcador hasta `to` en `frames` fotogramas."""
        low = self.points
        for k in range(1, frames + 1):
            self.points = round(low + (to - low) * _smooth(k / frames))
            self.add(ROLLUP_FRAME_MS, glow=1 - k / frames, **extra)

    def settle(self, **extra: Any) -> None:
        """El cartel y las monedas se asientan (solo con cartel)."""
        if not win_tier(self.points):
            for _ in range(3):
                self.add(100, **extra)
            return
        count = round(SETTLE_MS / ROLLUP_FRAME_MS)
        for k in range(1, count + 1):
            self.add(ROLLUP_FRAME_MS, settle=k / count, **extra)


def _base_celebration(
    theme: str,
    spin: BaseSpin,
    *,
    stake: int,
    before: Panel,
    after: Panel,
    tease: Tease,
) -> list[dict[str, Any]]:
    full = filled_cases(before, spin)
    counts = [CASE_SIZE[Tier(t)] if full[t] else after.cases[t][0] for t in range(3)]
    cells = win_cells(spin)
    base = _empty_state(
        board=[cell_key(cell, stake) for cell in spin.grid],
        header=_header(after),
        footer=_cases(counts, full=full),
        shade=0.45,
        keep=cells,
        lit=[[i, 1.0] for i in cells],
    )
    states: list[dict[str, Any]] = []
    if tease.kind is not None and tease_hit(spin, tease):
        # ¡Llega! El quinto rodillo destella con el color de su espera.
        for k in range(4):
            states.append(
                {
                    **base,
                    "focus": REELS - 1,
                    "glow": 1.0 if k % 2 == 0 else 0.6,
                    "glowColor": GOLD,
                    "flash": [0.0] * (REELS - 1) + [1.0 if k % 2 == 0 else 0.4],
                    "bulbs": win_bulbs(1, k),
                    "ms": 80,
                }
            )
    for k in range(4):
        on = k % 2 == 0
        states.append(
            {
                **base,
                "lit": [[i, 1.0 if on else 0.35] for i in cells],
                "bulbs": win_bulbs(0) if on else DARK,
                "ms": BLINK_MS,
            }
        )
    if spin.points <= 0:
        return states
    count = _Count(stake, spin.points, "PREMIO", _grid_seed("coins", theme, spin.grid, stake), base)
    # Premios por ways, el más gordo primero; si hay muchos, los últimos juntos.
    wins = sorted(spin.wins, key=lambda w: -w.points)
    groups = wins[: MAX_WAYS_STEPS - 1] + (
        [wins[MAX_WAYS_STEPS - 1 :]] if wins[MAX_WAYS_STEPS - 1 :] else []
    )
    for group in groups:
        items = group if isinstance(group, list) else [group]
        lit = sorted(set().union(*(w.cells for w in items)))
        frames = max(2, WAYS_STEP_MS // ROLLUP_FRAME_MS)
        count.roll(
            count.points + sum(w.points for w in items),
            frames,
            lit=[[i, 1.0] for i in lit],
            keep=lit,
        )
    if spin.collectors:
        coins = [i for i, _c in spin.coins]
        values = dict(spin.coins)
        sources = spin.collector_cells
        hot: list[int] = []
        for n, index in enumerate(coins):
            ms = COLLECT_MS[0] if n < COLLECT_SLOW_COINS else COLLECT_MS[1]
            done = [[j, 1.0] for j in hot]
            if n < COLLECT_SLOW_COINS:
                count.add(
                    ms, beam={"from": sources[0], "to": [*done, [index, 0.5]], "hot": hot[-1:]}
                )
            count.points += values[index].value
            hot.append(index)
            count.add(
                ms,
                glow=1.0,
                beam={"from": sources[0], "to": [*done, [index, 1.0]], "hot": [index]},
            )
        if spin.collectors == 2:
            every = [[j, 1.0] for j in coins]
            count.add(
                ROLLUP_FRAME_MS,
                beam={"from": sources[-1], "to": [[j, 0.5] for j in coins], "hot": []},
            )
            count.roll(
                count.points + spin.coin_points,
                5,
                beam={"from": sources[-1], "to": every, "hot": coins},
            )
    count.points = spin.points
    count.settle()
    states += count.states
    # El marcador y el cartel se van: el último fotograma es la máquina quieta.
    for alpha in (0.6, 0.25):
        fading = dict(states[-1])
        if fading["meter"]:
            fading["meter"] = {**fading["meter"], "alpha": alpha}
        if fading["banner"]:
            fading["banner"] = {**fading["banner"], "scale": round(alpha, 3)}
        fading["coins"] = []
        fading["sparkles"] = []
        fading["beam"] = None
        fading["ms"] = 60
        states.append(fading)
    return states


def base_states(
    theme: str, spin: BaseSpin, *, stake: int, before: Panel, after: Panel
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Los fotogramas de una tirada base y lo que la escena necesita de ella (las tiras).

    Args:
        before, after: Botes y maletines antes y después de la tirada (a la apuesta).
    """
    states, tracks, tease = _spin_frames(theme, spin, stake=stake, before=before, after=after)
    states[-1]["ms"] = FRAME_MS
    if win_cells(spin):
        states += _base_celebration(
            theme, spin, stake=stake, before=before, after=after, tease=tease
        )
    states.append(still_state(spin, stake=stake, panel=after, full=filled_cases(before, spin)))
    return states, {"strips": base_strips(theme, spin, tease, tracks, stake)}


# -- Bonus -----------------------------------------------------------------------------------------


def bonus_still_state(board: Sequence[Cell | None], *, stake: int, panel: BonusPanel) -> dict:
    """El bonus parado entre tiradas (y el último fotograma de cada tirada que no acaba)."""
    return _empty_state(
        board=[cell_key(cell, stake) for cell in board],
        header=_header(panel),
        footer=_bonus_footer(panel),
        lit=[[i, 0.6] for i, cell in enumerate(board) if cell is not None and cell.is_coin],
    )


@dataclass(frozen=True, slots=True)
class BonusFinish:
    """Un bonus que acaba en esta tirada: lo que paga, para el recuento."""

    result: BonusResult
    amount: int


def bonus_end_state(
    board: Sequence[Cell | None], *, stake: int, panel: BonusPanel, finish: BonusFinish
) -> dict:
    """El bonus cobrado: el marcador con el total y el cartel de su nivel."""
    state = bonus_still_state(board, stake=stake, panel=panel)
    points = finish.result.points
    tier = win_tier(points)
    state.update(
        meter=_meter(points, points, stake, label="BONUS"),
        banner={
            "tier": TIER_NAMES.get(tier, "big"),
            "scale": 1.0,
            "spin": 0.0,
            "flash": 0,
            **({} if tier else {"text": "¡BONUS COBRADO!"}),
        },
        bulbs=win_bulbs(tier),
    )
    return state


def _next_goal(coins: int) -> int | None:
    return next((goal for goal in GOALS if coins < goal), None)


def _bonus_order(empty: Sequence[int]) -> list[int]:
    """Orden en que paran las casillas vacías: columna a columna, de arriba abajo."""
    return sorted(empty)


def _chase_cells(order: Sequence[int], landed: dict[int, Any], coins_before: int) -> list[int]:
    """Las últimas casillas que deciden un bote: frenan despacio.

    Se mira justo antes de que pare cada una de las tres últimas: si con lo que queda por parar
    aún se puede llegar al siguiente bote y faltan una o dos monedas, esa casilla se hace esperar.
    """
    count = coins_before
    chase = []
    for k, index in enumerate(order):
        remaining = len(order) - k
        goal = _next_goal(count)
        if remaining <= 3 and goal is not None and 1 <= goal - count <= min(2, remaining):
            chase.append(index)
        landing = landed.get(index)
        if landing is not None and landing.cell.is_coin and not landing.mystery:
            count += 1
    return chase


def _bonus_tracks(
    order: Sequence[int], chase: Sequence[int], *, last_spin: bool, seed: random.Random
) -> dict[int, Track]:
    stagger = LAST_STAGGER_MS if last_spin else BONUS_STAGGER_MS
    tracks: dict[int, Track] = {}
    t = BONUS_LAND_MS
    for k, index in enumerate(order):
        jitter = seed.randint(-BONUS_JITTER_MS, BONUS_JITTER_MS)
        if index in chase:
            t += CHASE_CRUISE_MS + CHASE_SLOW_MS
            tracks[index] = Track(
                1,
                _round10(t),
                brake=CHASE_SLOW_MS,
                power=SLOW_POWER,
                overshoot=SLOW_OVERSHOOT,
                bounce=SLOW_BOUNCE_MS,
            )
        else:
            tracks[index] = Track(1, _round10(t + jitter if k else t))
            t += stagger
    return tracks


def _plaques_won(coins: int) -> tuple[bool, bool, bool]:
    return tuple(coins >= goal for goal in GOALS)  # type: ignore[return-value]


def bonus_step_states(
    before: Sequence[Cell | None],
    step: BonusStep,
    *,
    stake: int,
    panel_before: BonusPanel,
    panel_after: BonusPanel,
    finish: BonusFinish | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Los fotogramas de una tirada del bonus y las tiras de sus casillas vacías.

    Args:
        before: La rejilla antes de la tirada (con los modificadores de la anterior).
        finish: Si el bonus acaba en esta tirada, lo que paga: sale el recuento.
    """
    seed = _seed("bonus", tuple(before), step.landings, stake)
    board = [cell if cell is not None and cell.is_coin else None for cell in before]
    landed = {landing.index: landing for landing in step.landings}
    empty = [i for i, cell in enumerate(board) if cell is None]
    order = _bonus_order(empty)
    last_spin = panel_before.respins_left == 1
    chase = _chase_cells(order, landed, panel_before.coins)
    tracks = _bonus_tracks(order, chase, last_spin=last_spin, seed=seed)
    strips: dict[str, list[str]] = {}
    for index, track in tracks.items():
        final = cell_key(_shown(landed[index]), stake) if index in landed else ""
        length = _strip_length(track)
        fillers = [cell_key(_bonus_filler(seed, stake), stake) for _ in range(length - 1)]
        strips[str(index)] = [fillers[0], final, *fillers[1:]]
    keys = [cell_key(cell, stake) for cell in board]
    states: list[dict[str, Any]] = []
    coins_lit = [[i, 0.6] for i, cell in enumerate(board) if cell is not None]

    def frame(ms: int, **extra: Any) -> dict[str, Any]:
        state = _empty_state(board=list(keys), header=_header(panel_before), lit=list(coins_lit))
        state.update(extra)
        state["ms"] = ms
        states.append(state)
        return state

    # Los modificadores de la tirada anterior se van.
    old = [i for i, cell in enumerate(before) if cell is not None and not cell.is_coin]
    if old:
        for alpha in (0.6, 0.25):
            fading = list(keys)
            for i in old:
                fading[i] = cell_key(before[i], stake)
            frame(
                60, board=fading, fade=[[i, alpha] for i in old], footer=_bonus_footer(panel_before)
            )
    # Giran las vacías y paran de una en una.
    times = frame_times(list(tracks.values())) if tracks else [0, FRAME_MS]
    reset_now = panel_before.reset_value
    coins = panel_before.coins
    first_land = min((tracks[i].land for i in landed), default=None)
    chase_from = min((tracks[i].braking_from for i in chase), default=None)
    for n, t in enumerate(times):
        counted = coins + sum(
            1
            for i, landing in landed.items()
            if landing.cell.is_coin and not landing.mystery and t >= tracks[i].land
        )
        refilled = first_land is not None and t >= first_land
        refill = max(0.0, 1 - (t - first_land) / 300) if refilled else 0.0
        spin = []
        pop = []
        for index, track in tracks.items():
            spin.append(
                [
                    index,
                    round(track.position(t), 4),
                    round(min(1.0, track.speed(t) / SPEED), 3),
                    round(max(0.0, 1 - (t - track.land) / FLASH_MS), 3) if t >= track.land else 0.0,
                ]
            )
            if index in landed and t >= track.land:
                age = int((t - track.land) // 60)
                if age < len(POP):
                    pop.append([index, POP[age]])
        chasing = (
            chase_from is not None and t >= chase_from and any(t < tracks[i].end for i in chase)
        )
        beat = int(t // (PULSE_MS / 2)) % 2 == 0
        goal = _next_goal(counted)
        tense = 1.0 if (last_spin and beat) else (0.5 if last_spin else 0.0)
        glow_plaque = [0.0, 0.0, 0.0]
        if chasing and goal is not None:
            glow_plaque[GOALS.index(goal)] = 1.0 if beat else 0.4
        open_chase = [i for i in chase if t < tracks[i].end]
        state = frame(
            FRAME_MS,
            spin=spin,
            pop=pop,
            bulbs=tension_bulbs(t, RED) if (last_spin or chasing) else chase_bulbs(t),
            aura=open_chase if chasing else [],
            glow=(1.0 if beat else 0.55) if chasing else 0.0,
            glowColor=RED,
            dim=CHASE_DIM if chasing and len(open_chase) == 1 else 0.0,
            keep=open_chase if chasing else [],
            header=_header(panel_before, glow=glow_plaque, won=_plaques_won(counted)),
            footer=_bonus_footer(
                panel_before,
                left=reset_now if refilled else panel_before.respins_left - 1,
                refill=refill,
                coins=counted,
                tense=tense,
            ),
        )
        if n + 1 < len(times):
            state["ms"] = times[n + 1] - t
    # Lo que ha caído ya se queda en la rejilla.
    for index, landing in landed.items():
        keys[index] = cell_key(_shown(landing), stake)
    coins_now = coins + sum(1 for x in landed.values() if x.cell.is_coin and not x.mystery)
    left_now = reset_now if landed else panel_before.respins_left - 1
    mult = panel_before.multiplier
    reset = reset_now
    plaques = (panel_before.mini, panel_before.major, panel_before.grand)
    maxed = panel_before.maximized

    def after_spin(ms: int, **extra: Any) -> dict[str, Any]:
        extra.setdefault("header", _plaques(plaques, won=_plaques_won(coins_now), maximized=maxed))
        extra.setdefault(
            "footer",
            _bonus_footer(panel_before, left=left_now, reset=reset, mult=mult, coins=coins_now),
        )
        extra.setdefault(
            "lit", [[i, 0.6] for i, key in enumerate(keys) if key[:2] in ("g|", "b|", "r|")]
        )
        return frame(ms, **extra)

    # Los misteriosos tiemblan y se giran.
    mysteries = [landing for landing in step.landings if landing.mystery]
    if mysteries:
        for k, angle in enumerate((-8, 8, -5)):
            after_spin(
                70,
                shake=[[m.index, angle] for m in mysteries],
                bulbs=tension_bulbs(k * 70, BLUE, "diamond"),
            )
        for u in (0.25, 0.5, 0.75, 1.0):
            after_spin(
                60,
                flip=[
                    [m.index, u, cell_key(Cell(m.mystery), stake), cell_key(m.cell, stake)]
                    for m in mysteries
                ],
            )
        for m in mysteries:
            keys[m.index] = cell_key(m.cell, stake)
            if m.cell.is_coin:
                coins_now += 1
        for scale in POP[1:]:
            after_spin(60, pop=[[m.index, scale] for m in mysteries])
    # Tickets al multiplicador, tiradas extra a los rombos.
    tickets = [x for x in step.landings if x.cell.kind == Kind.TICKET]
    for landing in tickets:
        a = _center(landing.index)
        for u in (0.33, 0.66):
            x, y = _fly(a, MULT_XY, u, lift=40)
            after_spin(60, fly=[[x, y, "t", 0.7, 1.0]], lit=[[landing.index, 1.0]])
        mult += landing.cell.value
        for scale in (1.4, 1.15, 1.0):
            after_spin(
                70,
                footer=_bonus_footer(
                    panel_before,
                    left=left_now,
                    reset=reset,
                    mult=mult,
                    mult_pop=scale,
                    coins=coins_now,
                ),
            )
    extras = [x for x in step.landings if x.cell.kind == Kind.EXTRA]
    for landing in extras:
        a = _center(landing.index)
        target = (DIAMOND_X0 + reset * DIAMOND_PITCH, FOOTER_Y + FOOTER_H / 2)
        for u in (0.33, 0.66):
            x, y = _fly(a, target, u, lift=40)
            after_spin(60, fly=[[x, y, "x", 0.7, 1.0]])
        reset = min(reset + 1, panel_after.reset_value)
        left_now = reset
        for refill in (1.0, 0.5):
            after_spin(
                70,
                footer=_bonus_footer(
                    panel_before,
                    left=left_now,
                    reset=reset,
                    refill=refill,
                    mult=mult,
                    coins=coins_now,
                ),
            )
    # El multiplicador inmediato sube el valor de cada moneda a la vista.
    if step.instant != 10:
        coin_cells = [i for i, cell in enumerate(step.board) if cell is not None and cell.is_coin]
        old_values = {
            i: (before[i] if before[i] is not None and before[i].is_coin else landed[i].cell)
            for i in coin_cells
        }
        for k in range(1, 6):
            u = _smooth(k / 5)
            for i in coin_cells:
                cell = old_values[i]
                value = round(cell.value + (step.board[i].value - cell.value) * u)
                keys[i] = cell_key(Cell(Kind.COIN, tier=cell.tier, value=value), stake)
            after_spin(
                70, lit=[[i, 1.0 if k % 2 else 0.6] for i in coin_cells], bulbs=win_bulbs(1, k)
            )
    # El maximizador lleva el MINI y el MAJOR a su tope.
    if step.maximized:
        start = plaques
        for k in range(1, 5):
            u = _smooth(k / 4)
            plaques = (
                round(start[0] + (panel_after.mini - start[0]) * u),
                round(start[1] + (panel_after.major - start[1]) * u),
                start[2],
            )
            after_spin(
                70,
                header=_plaques(
                    plaques, glow=(1.0, 1.0, 0.0), won=_plaques_won(coins_now), maximized=True
                ),
            )
        maxed = True
    if finish is None:
        states.append(bonus_still_state(step.board, stake=stake, panel=panel_after))
        return states, {"cells": strips}
    states += _recount(step.board, stake=stake, panel=panel_after, finish=finish, keys=keys)
    states.append(bonus_end_state(step.board, stake=stake, panel=panel_after, finish=finish))
    return states, {"cells": strips}


def _recount(
    board: Sequence[Cell | None],
    *,
    stake: int,
    panel: BonusPanel,
    finish: BonusFinish,
    keys: Sequence[str],
) -> list[dict[str, Any]]:
    """El recuento del bonus: cada moneda al marcador, el multiplicador y los botes."""
    result = finish.result
    base = _empty_state(board=list(keys), header=_header(panel), footer=_bonus_footer(panel))
    count = _Count(stake, result.points, "BONUS", _seed("recount", tuple(board), stake), base)
    coins = [i for i, cell in enumerate(board) if cell is not None and cell.is_coin]
    meter = _meter_xy()
    done: list[int] = []
    for n, index in enumerate(coins):
        ms = COLLECT_MS[0] if n < COLLECT_SLOW_COINS else COLLECT_MS[1]
        x, y = _fly(_center(index), meter, 0.5, lift=20)
        cell = board[index]
        assert cell is not None
        icon = "gbr"[int(cell.tier)] if cell.tier is not None else "g"
        count.add(
            ms,
            lit=[[index, 1.0]] + [[j, 0.25] for j in done],
            fly=[[x, y, icon, 0.6, 1.0]],
        )
        count.points += cell.value
        done.append(index)
        count.add(ms, glow=1.0, lit=[[j, 0.25] for j in done])
    dim_all = [[j, 0.25] for j in done]
    if result.multiplier > 1:
        for u in (0.33, 0.66):
            x, y = _fly(MULT_XY, meter, u, lift=10)
            count.add(
                70,
                lit=dim_all,
                fly=[[x, y, "t", 0.8, 1.0]],
                footer=_bonus_footer(panel, mult_pop=1.3),
            )
        count.roll(result.coin_points * result.multiplier, 6, lit=dim_all)
    for name, value in zip(result.jackpots, result.jackpot_values, strict=True):
        k = ("mini", "major", "grand").index(name)
        glow = [0.0, 0.0, 0.0]
        glow[k] = 1.0
        header = _header(panel, glow=glow)
        for u in (0.25, 0.5, 0.75):
            x, y = _fly(_plaque_xy(k), meter, u, lift=-30)
            count.add(70, lit=dim_all, header=header, fly=[[x, y, name, 0.8, 1.0]])
        count.roll(count.points + value, 5, lit=dim_all, header=header)
    count.points = result.points
    count.settle(lit=dim_all)
    return count.states


def bonus_intro_states(
    spin: BaseSpin,
    *,
    base_stake: int,
    base_panel: Panel,
    full: Sequence[bool],
    game: BonusGame,
    panel: BonusPanel,
    name: str,
) -> list[dict[str, Any]]:
    """La entrada al bonus: los maletines llenos destellan, la rejilla se vacía, caen las
    monedas del bonus y sale su nombre. Empieza donde acaba la tirada (su imagen quieta)."""
    start = still_state(spin, stake=base_stake, panel=base_panel, highlight=False, full=full)
    counts = [CASE_SIZE[Tier(t)] if full[t] else base_panel.cases[t][0] for t in range(3)]
    states: list[dict[str, Any]] = []
    base_keys = [cell_key(cell, base_stake) for cell in spin.grid]
    for k in range(6):
        states.append(
            {
                **start,
                "reels": None,
                "board": base_keys,
                "footer": _cases(counts, full=full, t=k * 180),
                "bulbs": win_bulbs(2, k) if k % 2 == 0 else DARK,
                "ms": 90,
            }
        )
    for alpha in (0.75, 0.5, 0.25):
        states.append(
            {
                **states[-1],
                "fade": [[i, alpha] for i in range(CELLS)],
                "bulbs": CALM,
                "ms": 60,
            }
        )
    keys = [""] * CELLS
    seeded = [i for i, cell in enumerate(game.board) if cell is not None]
    for n in range(len(seeded) + 1):
        shown = seeded[:n]
        for i in shown:
            keys[i] = cell_key(game.board[i], game.stake)
        states.append(
            _empty_state(
                board=list(keys),
                header=_header(panel),
                footer=_bonus_footer(panel, left=min(n, panel.respins_left)),
                pop=[[seeded[n - 1], POP[0]]] if n else [],
                lit=[[i, 0.6] for i in shown],
                ms=80,
            )
        )
    for scale in BANNER_POP:
        state = bonus_intro_still(game, panel=panel, name=name)
        state["banner"] = {**state["banner"], "scale": scale}
        state["ms"] = 60
        states.append(state)
    for k in range(5):
        state = bonus_intro_still(game, panel=panel, name=name)
        state["banner"] = {**state["banner"], "flash": k % 2}
        state["bulbs"] = win_bulbs(2, k)
        state["ms"] = 90
        states.append(state)
    states.append(bonus_intro_still(game, panel=panel, name=name))
    return states


def bonus_intro_still(game: BonusGame, *, panel: BonusPanel, name: str) -> dict[str, Any]:
    """El bonus a punto de empezar, con su nombre: lo que se ve hasta el primer 🎰 Girar."""
    state = bonus_still_state(game.board, stake=game.stake, panel=panel)
    state["banner"] = {"tier": "mega", "scale": 1.0, "spin": 0.0, "flash": 0, "text": name}
    return state


# -- Lo que se pinta y cuándo vale lo ya pintado ---------------------------------------------------


def base_key(
    theme: str, spin: BaseSpin, *, stake: int, before: Panel, after: Panel, turbo: bool
) -> tuple:
    """Lo que decide la imagen de una tirada base: si coincide, vale lo pintado por adelantado."""
    return ("base", theme, spin.grid, stake, before, after, turbo)


def bonus_key(
    theme: str,
    before: Sequence[Cell | None],
    step: BonusStep,
    *,
    stake: int,
    panel_before: BonusPanel,
    panel_after: BonusPanel,
    finish: BonusFinish | None,
    turbo: bool,
) -> tuple:
    """Lo mismo para una tirada del bonus."""
    return ("bonus", theme, tuple(before), step, stake, panel_before, panel_after, finish, turbo)


def _style(theme: str) -> dict[str, Any]:
    style = STYLES[theme]

    def hexa(color: tuple[int, int, int]) -> str:
        return "#" + "".join(f"{c:02x}" for c in color)

    return {
        "frame": hexa(style.frame),
        "frameDark": hexa(style.frame_dark),
        "glow": hexa(style.glow),
        "title": hexa(style.title),
        "titleGlow": hexa(style.title_glow),
        "plaques": [[hexa(rim), hexa(face)] for rim, face in style.plaques],
        "effect": style.effect,
    }


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


SYMBOL_NAMES = ("pay0", "pay1", "pay2", "pay3", "pay4", "pay5", "wild", "collect")
MODIFIER_NAMES = ("ticket", "extra", "instant", "max", "mystery")


@functools.cache
def meta_state(theme: str) -> dict[str, Any]:
    """Lo que la escena necesita de cada máquina: fondo, casillas, símbolos y colores.

    Pinta el fondo con Pillow y lee los PNG del disco: la primera vez, desde `asyncio.to_thread`.
    """
    keys = dict(zip(SYMBOL_NAMES, ("p0", "p1", "p2", "p3", "p4", "p5", "w", "c"), strict=True))
    symbols = {
        keys[name]: _data_url((ASSETS / f"{theme}_{name}.png").read_bytes())
        for name in SYMBOL_NAMES
    }
    symbols.update(
        {name: _data_url((ASSETS / f"{name}.png").read_bytes()) for name in MODIFIER_NAMES}
    )
    return {
        "theme": theme,
        "backdrop": _data_url(HoldWinRenderer().scene_backdrop(theme)),
        "tiles": {
            "full": _data_url(cell_tile_png(theme, empty=False)),
            "empty": _data_url(cell_tile_png(theme, empty=True)),
        },
        "symbols": symbols,
        "symbolsKey": f"{theme}-v1",
        "style": _style(theme),
        "goals": list(GOALS),
        "bulbs": BULBS,
        "pointsPerStake": POINTS_PER_STAKE,
        "out": [OUT_W, OUT_H],
    }


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    return browser_scene.assemble(patches, (OUT_W, OUT_H))


def durations(states: Sequence[dict[str, Any]]) -> list[int]:
    """Milisegundos de cada fotograma; el último se queda quieto un minuto."""
    return [int(s["ms"]) for s in states[:-1]] + [FINAL_FRAME_MS]


def encode(frames: Sequence[Image.Image], frame_ms: Sequence[int]) -> Media:
    """GIF (que se repite: el cog lo cambia por el PNG al acabar) y PNG del último fotograma."""
    gif = local_palette_gif(list(frames), list(frame_ms), loop=True)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=sum(frame_ms[:-1]) / 1000)


class HoldWinScene:
    """Dibuja las máquinas de Botes con Node, si no con Chromium y, si no, con Pillow.

    Args:
        fallback: El `HoldWinRenderer` de reserva.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: HoldWinRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or HoldWinRenderer()
        self.painter = NodeScene(SCENE, name="El pintor de los Botes", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de los Botes",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya solo se dibuja con Pillow."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def _frames(
        self, theme: str, states: list[dict[str, Any]], extra: dict[str, Any] | None = None
    ) -> list[dict[str, Any]] | None:
        meta = await asyncio.to_thread(meta_state, theme)
        meta = {**meta, "strips": None, "cells": None, **(extra or {})}
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def _png(self, theme: str, state: dict[str, Any]) -> bytes | None:
        patches = await self._frames(theme, [state])
        if patches is None:
            return None
        return await asyncio.to_thread(patch_png, patches[0])

    async def _media(
        self, theme: str, states: list[dict[str, Any]], extra: dict[str, Any] | None = None
    ) -> Media | None:
        patches = await self._frames(theme, states, extra)
        if patches is None:
            return None
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations(states)))

    async def base(
        self,
        theme: str,
        spin: BaseSpin,
        *,
        stake: int,
        before: Panel,
        after: Panel,
        banner: Banner | None = None,
        turbo: bool = False,
    ) -> Media:
        """El GIF de una tirada base y su PNG final (solo el PNG en turbo).

        Args:
            before, after: Botes y maletines antes y después de la tirada.
            banner: Cartel del dibujo de Pillow de reserva.
        """
        if turbo:
            state = still_state(spin, stake=stake, panel=after, full=filled_cases(before, spin))
            png = await self._png(theme, state)
            if png is not None:
                return Media(gif=b"", png=png, seconds=0.0)
        else:
            states, extra = base_states(theme, spin, stake=stake, before=before, after=after)
            media = await self._media(theme, states, extra)
            if media is not None:
                return media
        return await asyncio.to_thread(
            self.fallback.render_base,
            theme,
            spin,
            stake=stake,
            panel=after,
            banner=banner,
            turbo=turbo,
        )

    async def still(
        self, theme: str, spin: BaseSpin, *, stake: int, panel: Panel, highlight: bool = False
    ) -> bytes:
        """La máquina parada (al abrirla y tras un Auto)."""
        png = await self._png(
            theme, still_state(spin, stake=stake, panel=panel, highlight=highlight)
        )
        if png is not None:
            return png
        return await asyncio.to_thread(
            self.fallback.base_still, theme, spin, stake=stake, panel=panel, highlight=highlight
        )

    async def bonus_intro(
        self,
        theme: str,
        spin: BaseSpin,
        *,
        base_stake: int,
        before: Panel,
        after: Panel,
        game: BonusGame,
        panel: BonusPanel,
        name: str,
        banner: Banner,
        turbo: bool = False,
    ) -> Media:
        """La entrada al bonus (GIF y PNG con el nombre; solo el PNG en turbo).

        Args:
            before, after: Botes y maletines de la tirada que lo dispara.
            banner: Cartel del dibujo de Pillow de reserva.
        """
        if turbo:
            png = await self._png(theme, bonus_intro_still(game, panel=panel, name=name))
            if png is not None:
                return Media(gif=b"", png=png, seconds=0.0)
        else:
            states = bonus_intro_states(
                spin,
                base_stake=base_stake,
                base_panel=after,
                full=filled_cases(before, spin),
                game=game,
                panel=panel,
                name=name,
            )
            media = await self._media(theme, states)
            if media is not None:
                return media
        png = await asyncio.to_thread(
            self.fallback.bonus_still,
            theme,
            game.board,
            stake=game.stake,
            panel=panel,
            banner=banner,
        )
        return Media(gif=b"", png=png, seconds=0.0)

    async def bonus_step(
        self,
        theme: str,
        before: Sequence[Cell | None],
        step: BonusStep,
        *,
        stake: int,
        panel_before: BonusPanel,
        panel_after: BonusPanel,
        finish: BonusFinish | None = None,
        banner: Banner | None = None,
        turbo: bool = False,
    ) -> Media:
        """Una tirada del bonus (GIF y PNG; solo el PNG en turbo).

        Args:
            finish: Si el bonus acaba aquí, lo que paga (sale el recuento).
            banner: Cartel del dibujo de Pillow de reserva.
        """
        if turbo:
            if finish is None:
                state = bonus_still_state(step.board, stake=stake, panel=panel_after)
            else:
                state = bonus_end_state(step.board, stake=stake, panel=panel_after, finish=finish)
            png = await self._png(theme, state)
            if png is not None:
                return Media(gif=b"", png=png, seconds=0.0)
        else:
            states, extra = bonus_step_states(
                before,
                step,
                stake=stake,
                panel_before=panel_before,
                panel_after=panel_after,
                finish=finish,
            )
            media = await self._media(theme, states, extra)
            if media is not None:
                return media
        return await asyncio.to_thread(
            self.fallback.render_bonus_step,
            theme,
            before,
            step,
            stake=stake,
            panel_before=panel_before,
            panel_after=panel_after,
            banner=banner,
            turbo=turbo,
        )
