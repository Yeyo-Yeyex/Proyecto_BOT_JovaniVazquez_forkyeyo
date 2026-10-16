"""La tragaperras clásica con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/slots/escena.html`) pinta el mueble, los tres rodillos, las luces de
los lados, el marcador del premio, los carteles, las monedas y el doble o nada. Python
decide todo lo que se ve, fotograma a fotograma, y la escena solo pinta: el ritmo vive
aquí.

**El ritmo de una tirada** (`spin_states`) depende de lo que hay en juego, como en las
máquinas de verdad: las tiradas sin nada son cortas y el drama llega solo a veces.

1. Sin nada que prometa (~1,5 s): un tirón hacia arriba, los rodillos a toda velocidad y
   paradas de izquierda a derecha, cada una con su rebote y un destello. Las paradas se
   mueven un poco en cada tirada (`JITTER_MS`): no suena a metrónomo.
2. Si los dos primeros prometen algo gordo (`Spin.anticipation`), el tercero sigue a tope
   con las luces de tensión y luego frena despacio hasta que se ven pasar los símbolos uno
   a uno por la línea (el «slow roll»): el casi-premio se ve rozar la línea. La cámara
   lenta son fotogramas más largos, no más fotogramas.
3. Camino del bote (🃏 🃏 en la línea): más largo, y el resto de la máquina se apaga.
4. Dos 🎟️: la misma espera, con las luces en azul.
5. Si la tirada paga, la línea parpadea con sus símbolos y el marcador cuenta por tramos:
   hasta ×5 la apuesta, hasta ×15, hasta ×50 y hasta el final, frenando en cada umbral.
   Al cruzarlo el cartel sube de nivel (GRAN PREMIO, MEGAPREMIO, ÉPICO), como el «Big
   Win → Mega Win» de las máquinas modernas. Un premio menor que la apuesta cuenta
   deprisa.

**El doble o nada** (`double_states`) es una carta boca abajo que tiembla, se levanta, se
gira y enseña un palo rojo (♥ ♦) o negro (♠ ♣). Arriba quedan las últimas cartas.

El último fotograma de cada GIF es el PNG que se queda en el mensaje y sale de la misma
función que el dibujo quieto (`still_state`, `double_still`).

Todo es determinista: lo que parece azar (las paradas un poco movidas, las monedas, el
palo de la carta) sale de la propia tirada. Pintar antes del clic y pintar al pulsar dan
los mismos bytes, y `media_key` dice cuándo una imagen ya pintada vale para una tirada.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los fotogramas en
   RGBA crudo.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`SlotsRenderer`, el dibujo de antes, más pequeño y sin el ritmo nuevo), si
   tampoco hay navegador. El doble o nada se queda entonces sin imagen.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el siguiente. La
máquina nunca se queda sin imagen.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import io
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.economy import format_amount
from bot.services.node_scene import NodeScene, patch_png
from bot.services.slots import (
    BIG_WIN,
    EPIC_WIN,
    MEGA_WIN,
    REEL_STRIPS,
    SCATTER,
    WILD,
    Kind,
    Spin,
    WinTier,
    high_target,
    win_tier,
)
from bot.services.slots_render import SYMBOL_FILES, SlotsMedia, SlotsRenderer
from bot.utils.gif import local_palette_gif

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "slots"
SCENE = ASSETS / "escena.html"

# -- Medidas: las usa también la escena; si cambian aquí, cambian allí -----------------------

#: Celda de un símbolo dentro del rodillo y lado del símbolo, en píxeles.
CELL_W, CELL_H = 128, 116
SYMBOL = 100
ROWS = 3
GAP = 16
MARGIN = 32
#: Columna de cada lado para las luces.
SIDE = 32
LEFT = SIDE + MARGIN
WINDOW_H = CELL_H * ROWS
W = LEFT * 2 + CELL_W * 3 + GAP * 2
H = MARGIN * 2 + WINDOW_H
#: Tamaño de la imagen que se manda: la escena pinta a W × H (al doble) y reduce a 3/4.
#: Es lo que más mueve el peso del GIF, porque los rodillos cambian enteros en cada
#: fotograma: a 544 × 412, una tirada sin premio pesaba ~920 KB y un ÉPICO ~3,3 MB; a
#: 408 × 309, ~620 KB y ~2,1 MB (la de Pillow de antes, 115 y 260). En Discord se ve
#: igual de nítida. Menos colores o menos fotogramas por segundo ahorraban mucho menos.
OUT_SCALE = 0.75
OUT_W, OUT_H = round(W * OUT_SCALE), round(H * OUT_SCALE)
#: Bombillas de cada columna de luces (la 0 es la de abajo).
BULBS = 12

# -- Ritmo de la tirada (milisegundos; múltiplos de 10, la unidad del GIF) -----------------

#: Fotograma normal: 20 por segundo bastan con los rodillos desenfocados.
FRAME_MS = 50
#: Con todos los que giran a toda velocidad no se ve forma, solo estela: menos fotogramas.
CRUISE_FRAME_MS = 70
#: Fotograma más largo del frenazo lento.
SLOW_FRAME_MS = 120
#: Lo que se quiere que avance el tercer rodillo en cada fotograma del frenazo lento.
SLOW_STEP_CELLS = 0.3
FINAL_FRAME_MS = 60_000
#: Tirón hacia arriba antes de arrancar (lo que suben los símbolos, en celdas).
WIND_MS = 150
WIND_CELLS = 0.22
#: Arranque hasta la velocidad máxima, en celdas por milisegundo (~18 por segundo).
ACCEL_MS = 160
SPEED = 0.018
#: Cuándo toca fondo cada rodillo, sin anticipación: el segundo 300 ms después del
#: primero y el tercero algo más tarde, para que la última parada se espere.
LAND_MS = (760, 1060, 1400)
#: Cuánto se mueve cada parada arriba o abajo de una tirada a otra.
JITTER_MS = 40
#: Frenazo normal: duración y curva (2: frena de golpe al final).
BRAKE_MS = 260
BRAKE_POWER = 2.0
#: Se pasa de la parada y vuelve: el rebote.
OVERSHOOT = 0.16
BOUNCE_MS = 140
#: Anticipación: el tercero sigue a tope tras parar el segundo y luego frena despacio.
TEASE_CRUISE_MS = 700
SLOW_ROLL_MS = 1500
#: Camino del bote: más espera y más frenazo.
JACKPOT_CRUISE_MS = 1200
JACKPOT_SLOW_MS = 1900
SLOW_POWER = 2.5
SLOW_OVERSHOOT = 0.06
SLOW_BOUNCE_MS = 160
#: Destello de la ventana de un rodillo al tocar fondo.
FLASH_MS = 180
#: Re-giro: solo gira el tercero.
RESPIN_LAND_MS = 900
#: Luces: la persecución sube una bombilla cada `CHASE_MS`; la de tensión, cada `TENSION_MS`.
CHASE_MS = 90
TENSION_MS = 50
#: Latido de la ventana del tercero y de los símbolos que prometen: a saltos (encendido y
#: medio), no en una onda continua. Cada cambio vuelve a escribir esas zonas en el GIF.
PULSE_MS = 360
#: Oscuridad del resto de la máquina camino del bote, y lo que tarda en apagarse.
JACKPOT_DIM = 0.62
DIM_MS = 300

# -- Premio ----------------------------------------------------------------------------------

#: Parpadeo de la línea al pagar: cada mitad, y cuántos (el bote, el doble).
BLINK_MS = 100
BLINKS = 3
#: Cuenta del marcador: un fotograma cada `ROLLUP_FRAME_MS`; el primer tramo dura
#: `FIRST_BAND_MS` (o `SMALL_ROLLUP_MS` si se cobra menos de lo apostado) y cada tramo de
#: nivel, `BAND_MS`.
ROLLUP_FRAME_MS = 80
FIRST_BAND_MS = 600
SMALL_ROLLUP_MS = 420
BAND_MS = 860
#: Tras la cuenta, el cartel y las monedas se asientan.
SETTLE_MS = 420
#: Escalas de los primeros fotogramas de un cartel: entra creciendo y se pasa un poco.
BANNER_POP = (0.5, 0.86, 1.1, 1.0)
#: Rayos del cartel ÉPICO: cuántos (el giro se cierra en un múltiplo de 360/RAYS) y cuánto
#: giran por milisegundo.
EPIC_RAYS = 18
RAY_SPIN = 0.06
#: Los rayos giran a saltos: girarlos en cada fotograma reescribe el cartel entero.
RAY_STEP_MS = 160
#: Parpadeo de las luces durante la cuenta, por nivel (`None`: premio sin nivel).
WIN_BLINK_MS: dict[str | None, int] = {
    None: 240,
    WinTier.BIG: 180,
    WinTier.MEGA: 120,
    WinTier.EPIC: 120,
}
#: Monedas que caen con cada nivel y destellos del ÉPICO.
COINS = {WinTier.BIG: 0, WinTier.MEGA: 7, WinTier.EPIC: 14}
SPARKLES = 9
COIN_SPEED = (0.22, 0.42)  # píxeles por milisegundo

# -- Doble o nada ----------------------------------------------------------------------------

DOUBLE_FADE_FRAMES = 5
DOUBLE_SHAKE_FRAMES = 8
DOUBLE_FLIP_FRAMES = 8
DOUBLE_HISTORY = 5
RED_SUITS = ("H", "D")
BLACK_SUITS = ("S", "C")

# -- Luces -----------------------------------------------------------------------------------

GOLD = "#deb246"
AMBER = "#ffd65c"
BLUE = "#60c4ff"
RED = "#ec382c"
WHITE = "#ffffff"

Bulbs = dict[str, Any]


def _bulbs(shape: str, lit: Sequence[str | None]) -> Bulbs:
    return {"shape": shape, "lit": list(lit)}


#: Máquina parada sin premio: una sí y una no, fijas.
CALM = _bulbs("round", [GOLD if b % 2 == 0 else None for b in range(BULBS)])
DARK = _bulbs("round", [None] * BULBS)
#: Forma de las bombillas en el premio: también se distingue sin ver colores.
WIN_SHAPE: dict[str | None, str] = {
    None: "round",
    WinTier.BIG: "star",
    WinTier.MEGA: "star",
    WinTier.EPIC: "burst",
}
WIN_COLORS: dict[str | None, tuple[str, ...]] = {
    None: (AMBER,),
    WinTier.BIG: (AMBER,),
    WinTier.MEGA: (AMBER, WHITE),
    WinTier.EPIC: (AMBER, WHITE),
}


def chase_bulbs(t: float) -> Bulbs:
    """Persecución mientras giran: una de cada tres sube, en grupos ámbar y azul."""
    step = int(t // CHASE_MS)
    lit = []
    for bulb in range(BULBS):
        offset = bulb - step
        color = AMBER if (offset // 3) % 2 == 0 else BLUE
        lit.append(color if offset % 3 == 0 else None)
    return _bulbs("round", lit)


def tension_bulbs(t: float, color: str = RED) -> Bulbs:
    """Tensión: dos de cada cuatro, rombos, el doble de rápido."""
    step = int(t // TENSION_MS)
    return _bulbs("diamond", [color if (b - step) % 4 < 2 else None for b in range(BULBS)])


def win_bulbs(tier: str | None, color: int = 0) -> Bulbs:
    """Todas encendidas con la forma del nivel del premio."""
    colors = WIN_COLORS[tier]
    return _bulbs(WIN_SHAPE[tier], [colors[color % len(colors)]] * BULBS)


def blink_bulbs(tier: str | None, t: float) -> Bulbs:
    """Todas a la vez, encendidas y apagadas; más deprisa cuanto más nivel."""
    half = WIN_BLINK_MS[tier]
    if int(t // half) % 2:
        return DARK
    return win_bulbs(tier, int(t // (2 * half)))


def flip_bulbs(t: float) -> Bulbs:
    """Doble o nada: rojas en las pares o en las impares, saltando como la carta."""
    side = int(t // 120) % 2
    return _bulbs("diamond", [RED if b % 2 == side else None for b in range(BULBS)])


# -- Rodillos --------------------------------------------------------------------------------


def _smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


@dataclass(frozen=True, slots=True)
class Track:
    """El viaje de un rodillo: arranca, va a tope, frena y rebota en su parada.

    Las posiciones son de la tira (como `Spin.stops`): la celda de la línea es la de la
    posición entera, y los símbolos bajan, así que la posición decrece hasta la parada.

    Attributes:
        stop: Parada final.
        land: Milisegundo en que toca fondo (pasado de la parada por `overshoot`).
        brake: Lo que dura el frenazo y su curva (`power`): más, más lento al final.
        moving: `False` en los rodillos que no giran (el re-giro).
    """

    stop: int
    land: float
    brake: float = BRAKE_MS
    power: float = BRAKE_POWER
    overshoot: float = OVERSHOOT
    bounce: float = BOUNCE_MS
    moving: bool = True

    @property
    def end(self) -> float:
        """Cuándo se queda quieto en su parada."""
        return self.land + self.bounce if self.moving else 0.0

    @property
    def braking_from(self) -> float:
        return self.land - self.brake

    def _distances(self) -> tuple[float, float, float]:
        accel = SPEED * ACCEL_MS / 2
        cruise = SPEED * (self.braking_from - WIND_MS - ACCEL_MS)
        brake = SPEED * self.brake / self.power
        return accel, cruise, brake

    def _travelled(self, t: float) -> float:
        accel, cruise, brake = self._distances()
        if t <= WIND_MS:
            return 0.0
        if t <= WIND_MS + ACCEL_MS:
            tau = t - WIND_MS
            return SPEED * tau * tau / (2 * ACCEL_MS)
        if t <= self.braking_from:
            return accel + SPEED * (t - WIND_MS - ACCEL_MS)
        if t <= self.land:
            u = (t - self.braking_from) / self.brake
            return accel + cruise + brake * (1 - (1 - u) ** self.power)
        return accel + cruise + brake

    def position(self, t: float) -> float:
        """Posición de la tira en el milisegundo `t`."""
        if not self.moving or t >= self.end:
            return float(self.stop)
        target = self.stop - self.overshoot
        total = sum(self._distances())
        if t < WIND_MS:
            return target + total + WIND_CELLS * math.sin(math.pi * t / WIND_MS)
        if t <= self.land:
            return target + total - self._travelled(t)
        return self.stop - self.overshoot * (1 - _smooth((t - self.land) / self.bounce))

    def speed(self, t: float) -> float:
        """Celdas por milisegundo en `t` (para el desenfoque y el frenazo lento)."""
        if not self.moving or t <= WIND_MS or t >= self.land:
            return 0.0
        if t <= WIND_MS + ACCEL_MS:
            return SPEED * (t - WIND_MS) / ACCEL_MS
        if t <= self.braking_from:
            return SPEED
        u = (t - self.braking_from) / self.brake
        return SPEED * (1 - u) ** (self.power - 1)


def _seed(*parts: object) -> random.Random:
    """Azar repetible: el mismo para la misma tirada (para pintar por adelantado)."""
    return random.Random(repr(parts))


def _round10(ms: float) -> int:
    return int(round(ms / 10.0)) * 10


@dataclass(frozen=True, slots=True)
class Tease:
    """Qué espera promete la tirada: `None`, `"line"`, `"jackpot"` o `"scatter"`."""

    kind: str | None
    cells: tuple[tuple[int, int], ...] = ()


def tease_of(spin: Spin) -> Tease:
    """La espera de la tirada y los símbolos de los dos primeros rodillos que la provocan."""
    if not spin.anticipation:
        return Tease(None)
    first, second, _ = spin.line
    target = high_target(first, second)
    if target is not None:
        return Tease("jackpot" if target == WILD else "line", ((0, 1), (1, 1)))
    cells = tuple(
        (reel, row) for reel in (0, 1) for row in range(ROWS) if spin.grid[row][reel] == SCATTER
    )
    return Tease("scatter", cells)


def tracks_for(spin: Spin, *, respin: bool = False) -> tuple[Track, Track, Track]:
    """El viaje de cada rodillo para `spin` (con sus paradas un poco movidas)."""
    rng = _seed("land", spin.stops, respin)
    tease = tease_of(spin).kind
    if respin:
        land = RESPIN_LAND_MS
        before = WIND_MS + ACCEL_MS
    else:
        lands = [_round10(ms + rng.randint(-JITTER_MS, JITTER_MS)) for ms in LAND_MS]
        before = lands[1]
    if tease is None:
        third = Track(spin.stops[2], land if respin else lands[2])
    else:
        cruise, slow = (
            (JACKPOT_CRUISE_MS, JACKPOT_SLOW_MS)
            if tease == "jackpot"
            else (TEASE_CRUISE_MS, SLOW_ROLL_MS)
        )
        third = Track(
            spin.stops[2],
            before + cruise + slow,
            brake=slow,
            power=SLOW_POWER,
            overshoot=SLOW_OVERSHOOT,
            bounce=SLOW_BOUNCE_MS,
        )
    if respin:
        return (
            Track(spin.stops[0], 0, moving=False),
            Track(spin.stops[1], 0, moving=False),
            third,
        )
    return Track(spin.stops[0], lands[0]), Track(spin.stops[1], lands[1]), third


def frame_times(tracks: Sequence[Track]) -> list[int]:
    """Cuándo empieza cada fotograma del giro; el último, con todo parado.

    A 20 por segundo mientras algo va deprisa, y a `CRUISE_FRAME_MS` mientras todos los
    que giran van a tope (solo se ve la estela). En el frenazo lento del tercero, cada
    fotograma dura lo que tarda en avanzar `SLOW_STEP_CELLS` (hasta `SLOW_FRAME_MS`).
    """
    end = _round10(max(track.end for track in tracks))
    slow = tracks[2].power != BRAKE_POWER
    times: list[int] = []
    t = 0
    while t < end:
        times.append(t)
        step = FRAME_MS
        spinning = [track for track in tracks if track.moving and t < track.land]
        if spinning and all(track.speed(t) >= SPEED for track in spinning):
            # Sin pasarse del momento en que alguno empieza a frenar.
            brake = min(track.braking_from for track in spinning)
            step = max(FRAME_MS, min(CRUISE_FRAME_MS, _round10(brake - t)))
        if slow and t >= tracks[2].braking_from:
            speed = tracks[2].speed(t)
            step = SLOW_FRAME_MS if speed <= 0 else SLOW_STEP_CELLS / speed
            step = min(SLOW_FRAME_MS, max(FRAME_MS, _round10(step)))
        t += step
    if times and end - times[-1] < FRAME_MS // 2 and len(times) > 1:
        times.pop()  # un fotograma de 10-20 ms no se vería: se junta con el anterior
    times.append(end)
    return times


# -- Estados: lo que lee la escena -----------------------------------------------------------


def win_cells(spin: Spin) -> list[list[int]]:
    """Casillas `[rodillo, fila]` que se iluminan: las de la línea que pagan y los 🎟️."""
    cells: list[list[int]] = []
    if spin.kind in (Kind.JACKPOT, Kind.THREE):
        cells = [[0, 1], [1, 1], [2, 1]]
    elif spin.kind == Kind.TWO_CHERRIES:
        cells = [[0, 1], [1, 1]]
    elif spin.kind == Kind.CHERRY:
        cells = [[0, 1]]
    if spin.triggers_free_spins:
        cells += [
            [reel, row]
            for reel in range(3)
            for row in range(ROWS)
            if spin.grid[row][reel] == SCATTER and [reel, row] not in cells
        ]
    return cells


def _line_pays(spin: Spin) -> bool:
    return spin.pay_halves > 0 or spin.is_jackpot


def _base(stops: Sequence[float], **extra: Any) -> dict[str, Any]:
    """Un estado con los rodillos en `stops` y todo lo demás apagado."""
    state: dict[str, Any] = {
        "reels": [float(s) for s in stops],
        "blur": [0.0, 0.0, 0.0],
        "flash": [0.0, 0.0, 0.0],
        "bulbs": CALM,
        "glow": 0.0,
        "glowColor": RED,
        "dim": 0.0,
        "line": 0.0,
        "hits": [],
        "pulse": 0.0,
        "banner": None,
        "meter": None,
        "coins": [],
        "sparkles": [],
        "gamble": None,
    }
    state.update(extra)
    return state


def _meter(amount: int, won: int, glow: float = 0.0) -> dict[str, Any]:
    # `fit` es el importe final: el marcador elige el tamaño de letra con él y no
    # cambia de tamaño mientras cuenta.
    return {"text": format_amount(amount), "fit": format_amount(won), "glow": round(glow, 3)}


def still_state(spin: Spin, *, won: int = 0, stake: int = 0, highlight: bool | None = None) -> dict:
    """La máquina parada en `spin`: el último fotograma de la tirada y el PNG.

    Args:
        won: Lo cobrado; si es más que cero, sale el marcador (y el cartel de su nivel).
        stake: La apuesta, para el nivel del premio.
        highlight: Si se ilumina lo que paga. Por defecto, si la línea paga o hay giros
            gratis. `False` para la máquina al abrirla.
    """
    if highlight is None:
        highlight = _line_pays(spin) or spin.triggers_free_spins
    tier = win_tier(won, stake) if won > 0 else None
    state = _base(spin.stops)
    if highlight:
        state.update(
            line=1.0 if _line_pays(spin) else 0.0,
            hits=win_cells(spin),
            pulse=1.0,
            bulbs=win_bulbs(tier) if won > 0 else CALM,
        )
    if won > 0:
        state["meter"] = _meter(won, won)
        state["bulbs"] = win_bulbs(tier)
        if tier is not None:
            state["banner"] = {"tier": tier, "scale": 1.0, "spin": 0.0, "flash": 0}
    return state


def _spin_frames(spin: Spin, *, respin: bool) -> list[dict[str, Any]]:
    """Los fotogramas del giro, del arranque a los tres rodillos parados."""
    tracks = tracks_for(spin, respin=respin)
    tease = tease_of(spin)
    times = frame_times(tracks)
    third = tracks[2]
    tease_from = WIND_MS if respin else tracks[1].land
    states = []
    for index, t in enumerate(times):
        teasing = tease.kind is not None and tease_from <= t < third.land
        if teasing:
            color = BLUE if tease.kind == "scatter" else RED
            bulbs = tension_bulbs(t, color)
        else:
            bulbs = chase_bulbs(t)
        beat = int((t - tease_from) // (PULSE_MS / 2)) % 2 == 0
        dim = 0.0
        if teasing and tease.kind == "jackpot":
            dim = JACKPOT_DIM * min(1.0, (t - tease_from) / DIM_MS)
        state = _base(
            [track.position(t) for track in tracks],
            blur=[round(min(1.0, track.speed(t) / SPEED), 3) for track in tracks],
            flash=[
                round(max(0.0, 1 - (t - track.land) / FLASH_MS), 3)
                if track.moving and t >= track.land
                else 0.0
                for track in tracks
            ],
            bulbs=bulbs,
            glow=(1.0 if beat else 0.55) if teasing else 0.0,
            glowColor=BLUE if tease.kind == "scatter" else RED,
            dim=round(dim, 3),
            hits=[list(cell) for cell in tease.cells] if teasing else [],
            pulse=(1.0 if beat else 0.5) if teasing else 0.0,
        )
        state["reels"] = [round(p, 4) for p in state["reels"]]
        if index + 1 < len(times):
            state["ms"] = times[index + 1] - t
        states.append(state)
    return states


def _bands(won: int, stake: int) -> list[tuple[int, int, int]]:
    """Tramos de la cuenta: `(desde, hasta, milisegundos)`, frenando en cada umbral."""
    marks = [m * stake for m in (BIG_WIN, MEGA_WIN, EPIC_WIN) if 0 < m * stake < won]
    points = [0, *marks, won]
    first = SMALL_ROLLUP_MS if won < stake else FIRST_BAND_MS
    return [
        (points[k], points[k + 1], first if k == 0 else BAND_MS) for k in range(len(points) - 1)
    ]


def _coins(rng: random.Random, count: int) -> list[tuple[float, float, float, float]]:
    """Monedas: `(x, y de salida, velocidad, fase del giro)`, siempre las mismas."""
    return [
        (
            rng.uniform(LEFT, W - LEFT),
            rng.uniform(-H, 0),
            rng.uniform(*COIN_SPEED),
            rng.uniform(0, 4),
        )
        for _ in range(count)
    ]


def _celebration(spin: Spin, won: int, stake: int, start: int) -> list[dict[str, Any]]:
    """La cuenta del marcador con los carteles que suben de nivel, y el asiento final."""
    final_tier = win_tier(won, stake)
    rng = _seed("coins", spin.stops, won, stake)
    coins = _coins(rng, COINS[WinTier.EPIC])
    sparkles = [
        (rng.uniform(LEFT, W - LEFT), rng.uniform(MARGIN, H - MARGIN), rng.uniform(0, 1))
        for _ in range(SPARKLES)
    ]
    frames: list[tuple[int, int, float]] = []  # (importe, ms, brillo del marcador)
    for low, high, ms in _bands(won, stake):
        count = max(2, round(ms / ROLLUP_FRAME_MS))
        for k in range(1, count + 1):
            u = k / count
            frames.append((round(low + (high - low) * _smooth(u)), ROLLUP_FRAME_MS, 1 - u))
    if final_tier is not None:
        settle = round(SETTLE_MS / ROLLUP_FRAME_MS)
        frames += [(won, ROLLUP_FRAME_MS, 0.0)] * settle

    states = []
    tier_since: dict[str | None, int] = {}
    rays_end = 0.0
    t = 0
    count_end = len(frames) - (round(SETTLE_MS / ROLLUP_FRAME_MS) if final_tier else 0)
    for index, (amount, ms, shine) in enumerate(frames):
        tier = win_tier(amount, stake)
        if tier is not None:
            tier_since.setdefault(tier, index)
        state = _base(
            spin.stops,
            line=1.0 if _line_pays(spin) else 0.0,
            hits=win_cells(spin),
            pulse=1.0,
            # Las luces suben de nivel con el cartel.
            bulbs=blink_bulbs(tier, start + t),
            meter=_meter(amount, won, glow=round(shine * 2) / 2 if index < count_end else 0.0),
        )
        if tier is not None:
            age = index - tier_since[tier]
            spin_deg = RAY_SPIN * (t // RAY_STEP_MS * RAY_STEP_MS)
            if index >= count_end:
                # Los rayos acaban en una posición igual a la de la imagen quieta.
                if not rays_end:
                    rays_end = math.ceil(spin_deg / (360 / EPIC_RAYS)) * (360 / EPIC_RAYS)
                settle_u = (index - count_end + 1) / (len(frames) - count_end)
                spin_deg = spin_deg + (rays_end - spin_deg) * _smooth(settle_u)
            state["banner"] = {
                "tier": tier,
                "scale": BANNER_POP[age] if age < len(BANNER_POP) else 1.0,
                "spin": round(spin_deg % 360, 2),
                "flash": int(t // 180) % 2,
            }
            coin_count = COINS[tier]
            fade = 1.0
            if index >= count_end:
                fade = 1 - (index - count_end + 1) / (len(frames) - count_end)
            since = (index - tier_since[tier]) * ROLLUP_FRAME_MS
            state["coins"] = [
                [
                    round(x, 1),
                    round((y0 + v * since) % (H + 60) - 30, 1),
                    int(phase + since / 70) % 4,
                    round(fade, 3),
                ]
                for x, y0, v, phase in coins[:coin_count]
            ]
            if tier == WinTier.EPIC:
                state["sparkles"] = [
                    [
                        round(x, 1),
                        round(y, 1),
                        round(abs(math.sin(math.pi * (p + t / 500))) * 2) / 2,
                    ]
                    for x, y, p in sparkles
                ]
        state["ms"] = ms
        states.append(state)
        t += ms
    return states


def spin_states(
    spin: Spin, *, won: int = 0, stake: int = 0, respin: bool = False
) -> list[dict[str, Any]]:
    """Todos los fotogramas de una tirada, con su duración (`ms`); el último es el quieto.

    Args:
        won: Lo cobrado (línea más bote): si es más que cero, la cuenta del marcador.
        stake: La apuesta, para los tramos de la cuenta y los carteles.
        respin: Re-giro del tercer rodillo: los otros dos no se mueven.
    """
    states = _spin_frames(spin, respin=respin)
    # Los tres rodillos ya parados se ven un instante antes de lo que venga.
    states[-1]["ms"] = FRAME_MS
    if _line_pays(spin) or spin.triggers_free_spins:
        blinks = BLINKS * (2 if spin.is_jackpot else 1)
        for k in range(blinks * 2):
            on = k % 2 == 0
            blink = _base(
                spin.stops,
                line=1.0 if on and _line_pays(spin) else 0.0,
                hits=win_cells(spin),
                pulse=1.0 if on else 0.3,
                bulbs=win_bulbs(None) if on else DARK,
            )
            blink["ms"] = BLINK_MS
            states.append(blink)
    if won > 0:
        start = sum(s["ms"] for s in states)
        states += _celebration(spin, won, stake, start)
    states.append(still_state(spin, won=won, stake=stake))
    return states


def durations(states: Sequence[dict[str, Any]]) -> list[int]:
    """Milisegundos de cada fotograma; el último se queda quieto un minuto."""
    return [int(s["ms"]) for s in states[:-1]] + [FINAL_FRAME_MS]


# -- Doble o nada ----------------------------------------------------------------------------


def double_suit(*, amount: int, pick: str, won: bool, history: Sequence[str]) -> str:
    """El palo de la carta: del color elegido si gana y del otro si pierde."""
    red = (pick == "red") == won
    return _seed("suit", amount, pick, won, tuple(history)).choice(
        RED_SUITS if red else BLACK_SUITS
    )


def _gamble(
    *,
    amount: int,
    pick: str,
    history: Sequence[str],
    mix: float = 1.0,
    card: dict[str, Any] | None = None,
    stamp: dict[str, Any] | None = None,
    label: str = "EN JUEGO",
    text: str | None = None,
    tone: str = "gold",
    glow: float = 0.0,
) -> dict[str, Any]:
    return {
        "mix": round(mix, 3),
        "pick": pick,
        "prize": format_amount(2 * amount),
        "label": label,
        "text": text or format_amount(amount),
        "tone": tone,
        "glow": round(glow, 3),
        "card": card
        or {"flip": 0.0, "suit": None, "rot": 0.0, "lift": 0.0, "glow": 0.0, "dim": 0.0},
        "stamp": stamp,
        "history": list(history)[-DOUBLE_HISTORY:],
    }


def double_still(
    *,
    stops: Sequence[int],
    amount: int,
    pick: str,
    won: bool,
    history: Sequence[str],
) -> dict[str, Any]:
    """La carta ya girada: el último fotograma del doble o nada y su PNG.

    Args:
        history: Los palos de antes de esta carta; esta se añade al final.
    """
    suit = double_suit(amount=amount, pick=pick, won=won, history=history)
    card = {
        "flip": 1.0,
        "suit": suit,
        "rot": 0.0,
        "lift": 0.0,
        "glow": 1.0 if won else 0.0,
        "dim": 0.0 if won else 0.55,
    }
    stamp = {"text": "¡DOBLE!" if won else "NADA", "kind": "win" if won else "lose", "scale": 1.0}
    gamble = _gamble(
        amount=amount,
        pick=pick,
        history=[*history, suit],
        card=card,
        stamp=stamp,
        label="¡GANAS!" if won else "PIERDES",
        text=format_amount(2 * amount) if won else f"-{format_amount(amount)}",
        tone="win" if won else "lose",
    )
    return _base(stops, gamble=gamble, bulbs=win_bulbs(WinTier.BIG) if won else DARK)


def double_states(
    *,
    stops: Sequence[int],
    amount: int,
    pick: str,
    won: bool,
    history: Sequence[str],
    fresh: bool,
) -> list[dict[str, Any]]:
    """Los fotogramas de un doble o nada: la carta tiembla, se gira y paga o no.

    Args:
        stops: Los rodillos de la tirada que se dobla (se ven debajo al empezar).
        amount: Lo que se juega.
        pick: `"red"` o `"black"`, lo que pulsó el jugador.
        won: Si gana (sale su color).
        history: Palos de las cartas anteriores, la más reciente al final.
        fresh: Si es el primero de la racha: el panel entra fundiéndose sobre los rodillos.
    """
    suit = double_suit(amount=amount, pick=pick, won=won, history=history)
    states: list[dict[str, Any]] = []
    t = 0

    def add(ms: int, **gamble: Any) -> None:
        nonlocal t
        bulbs = gamble.pop("bulbs", None) or flip_bulbs(t)
        state = _base(
            stops, bulbs=bulbs, gamble=_gamble(amount=amount, pick=pick, history=history, **gamble)
        )
        state["ms"] = ms
        states.append(state)
        t += ms

    if fresh:
        for k in range(1, DOUBLE_FADE_FRAMES + 1):
            add(60, mix=k / DOUBLE_FADE_FRAMES, bulbs=CALM)
    for k in range(DOUBLE_SHAKE_FRAMES):
        swing = math.sin(k * 1.9) * (2.0 + 2.5 * k / DOUBLE_SHAKE_FRAMES)
        add(
            80,
            card={
                "flip": 0.0,
                "suit": None,
                "rot": round(swing, 2),
                "lift": 0.0,
                "glow": 0.0,
                "dim": 0.0,
            },
        )
    for k in range(1, DOUBLE_FLIP_FRAMES + 1):
        u = k / DOUBLE_FLIP_FRAMES
        add(
            50,
            card={
                "flip": round(u, 3),
                "suit": suit,
                "rot": 0.0,
                "lift": round(14 * math.sin(math.pi * u), 2),
                "glow": 0.0,
                "dim": 0.0,
            },
        )
    states[-1]["ms"] = 220  # la carta ya girada: un instante antes de saber
    result_card = {"flip": 1.0, "suit": suit, "rot": 0.0, "lift": 0.0}
    if won:
        for k, scale in enumerate(BANNER_POP):
            add(
                60,
                card={**result_card, "glow": 1.0, "dim": 0.0},
                stamp={"text": "¡DOBLE!", "kind": "win", "scale": scale},
                bulbs=blink_bulbs(WinTier.BIG, k * 60),
            )
        steps = 10
        for k in range(1, steps + 1):
            u = _smooth(k / steps)
            add(
                60,
                card={**result_card, "glow": 1.0, "dim": 0.0},
                stamp={"text": "¡DOBLE!", "kind": "win", "scale": 1.0},
                label="¡GANAS!",
                text=format_amount(round(amount + amount * u)),
                tone="win",
                glow=1 - k / steps,
                bulbs=blink_bulbs(WinTier.BIG, (k + 4) * 60),
            )
    else:
        for k, scale in enumerate(BANNER_POP):
            add(
                70,
                card={**result_card, "glow": 0.0, "dim": round(0.55 * (k + 1) / 4, 3)},
                stamp={"text": "NADA", "kind": "lose", "scale": scale},
                label="PIERDES",
                text=f"-{format_amount(amount)}",
                tone="lose",
                bulbs=DARK if k % 2 else flip_bulbs(t),
            )
    states.append(double_still(stops=stops, amount=amount, pick=pick, won=won, history=history))
    return states


# -- Lo que se pinta y cuándo vale lo ya pintado ----------------------------------------------


def media_key(spin: Spin, *, won: int, stake: int, turbo: bool, respin: bool = False) -> tuple:
    """Lo que decide la imagen de una tirada: si coincide, vale lo pintado por adelantado.

    Sin premio, la apuesta no cambia nada de lo que se ve; con premio, sí (los tramos de la
    cuenta y el nivel del cartel).
    """
    return ("spin", spin.stops, respin, turbo, won, stake if won > 0 else 0)


@functools.cache
def meta_state() -> dict[str, Any]:
    """Lo que la escena necesita una vez: las tiras de los rodillos y los símbolos.

    Lee los PNG del disco: la primera vez se llama desde `asyncio.to_thread`.
    """
    symbols = {
        key: "data:image/png;base64,"
        + base64.b64encode((ASSETS / name).read_bytes()).decode("ascii")
        for key, name in SYMBOL_FILES.items()
    }
    return {
        "strips": list(REEL_STRIPS),
        "symbols": symbols,
        "symbolsKey": "200px-v1",
        "out": [OUT_W, OUT_H],
    }


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (OUT_W, OUT_H))


def encode(frames: Sequence[Image.Image], frame_ms: Sequence[int]) -> SlotsMedia:
    """GIF (que se repite: el cog lo cambia por el PNG al acabar) y PNG del último fotograma."""
    gif = local_palette_gif(list(frames), list(frame_ms), loop=True)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return SlotsMedia(gif=gif, png=png.getvalue(), seconds=sum(frame_ms[:-1]) / 1000)


class SlotsScene:
    """Dibuja la tragaperras con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El `SlotsRenderer` de reserva.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: SlotsRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or SlotsRenderer()
        self.painter = NodeScene(SCENE, name="El pintor de la tragaperras", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de la tragaperras",
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

    async def _frames(self, states: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Recuadros de cada fotograma; `None` si no hay Node ni navegador."""
        meta = await asyncio.to_thread(meta_state)
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def _png(self, state: dict[str, Any]) -> bytes | None:
        patches = await self._frames([state])
        if patches is None:
            return None
        return await asyncio.to_thread(patch_png, patches[0])

    async def spin(
        self,
        spin: Spin,
        *,
        turbo: bool = False,
        won: int = 0,
        stake: int = 0,
        respin: bool = False,
    ) -> SlotsMedia:
        """El GIF de una tirada y su PNG final (solo el PNG en turbo).

        Args:
            won: Todo lo cobrado (línea más bote), para el marcador.
            stake: Apuesta de la tirada (en un giro gratis, la que lo activó).
            respin: Re-giro del tercer rodillo.
        """
        if turbo:
            png = await self._png(still_state(spin, won=won, stake=stake))
            if png is not None:
                return SlotsMedia(gif=b"", png=png, seconds=0.0)
        else:
            states = spin_states(spin, won=won, stake=stake, respin=respin)
            patches = await self._frames(states)
            if patches is not None:
                return await asyncio.to_thread(lambda: encode(assemble(patches), durations(states)))
        return await asyncio.to_thread(
            self.fallback.render, spin, turbo=turbo, won=won, stake=stake
        )

    async def still(self, spin: Spin, *, highlight: bool | None = None) -> bytes:
        """La máquina parada en `spin`, sin marcador (al abrirla y tras una Ráfaga)."""
        png = await self._png(still_state(spin, highlight=highlight))
        if png is not None:
            return png
        lit = bool(highlight) if highlight is not None else _line_pays(spin)
        return await asyncio.to_thread(self.fallback.still_png, spin.stops, highlight=lit)

    async def double(
        self,
        *,
        stops: Sequence[int],
        amount: int,
        pick: str,
        won: bool,
        history: Sequence[str],
        fresh: bool,
    ) -> SlotsMedia | None:
        """El GIF del doble o nada (`double_states`); `None` si solo queda Pillow."""
        states = double_states(
            stops=stops, amount=amount, pick=pick, won=won, history=history, fresh=fresh
        )
        patches = await self._frames(states)
        if patches is None:
            return None
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations(states)))
