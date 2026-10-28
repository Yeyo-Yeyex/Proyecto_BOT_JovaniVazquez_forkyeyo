"""Dibujo del juego del pico (`picar`): de una partida jugada a GIF por partes.

La partida ya está jugada y pagada (`bot.services.picar.play`) y aquí solo se enseña.
La escena (`assets/picar/escena.html`) pinta en canvas y no decide nada: este módulo le
pasa el trozo de mundo que hace falta (`segment_meta`) y el estado de cada fotograma.
Se pinta con Node (`bot.services.node_scene`), si no con Chromium y, si tampoco, solo
se saca una imagen quieta con Pillow (`pillow_still`): el juego nunca se queda sin
imagen.

**Mundo.** La escena recibe las casillas tal como se generaron, el tick en que
desaparece cada una y los cambios de grieta. Así cada fotograma sale solo de su tick,
sin arrastrar nada del anterior, y el dibujo se reparte entre los procesos de Node.

**Tiempo.** La física va a 60 ticks por segundo y el GIF enseña uno de cada
`TICKS_PER_FRAME`, a 20 fotogramas por segundo: tiempo real.

**Cámara** (`camera_path`). Quieta mientras el pico está en una franja de la pantalla;
cuando sale, acelera, lo sigue y frena hasta dejarlo arriba. Con la cámara quieta el GIF
solo guarda lo que se mueve; con la cámara siguiendo al pico en cada fotograma pesaba
~800 KB por segundo, y así ~200-250 KB (medido el 28/10/2026 con hierro y netherite).
Antes se recolocaba acercándose un 30 % de lo que le faltaba en cada fotograma: el primero
saltaba 72 px de golpe, y en Discord se veían tirones.

**Guion** (`story`). Los fotogramas de la partida y, entre medias:

- Al cambiar de dimensión, el pico se para, la pantalla se oscurece, sale el rótulo
  («ENTRANDO AL NETHER») y se queda quieta `TRANSITION_HOLD_MS`. Ahí acaba un GIF: esa
  pausa tapa lo que tarda en subirse el siguiente, que empieza aclarando la pantalla.
- Al acabar, el pico se rompe en trocitos (o el dragón se desvanece) y sale el cartel
  del premio, quieto `FINAL_HOLD_MS`.

**Partes** (`PicarScene.parts`). Una partida larga no cabe en un GIF: Discord limita lo
que se sube y montar cientos de fotogramas a la vez no cabe en la memoria del NAS. Se
pinta por tramos de `CHUNK_FRAMES` y se monta con `bot.utils.gif.GifStream`, que solo
guarda los bytes ya codificados. Un GIF acaba en cada cambio de dimensión, cuando se
acerca al tope de peso (`budget`, con una pausa corta, `CUT_HOLD_MS`) y al final. El
primero es corto (`FIRST_PART_FRAMES`) para que el clic tenga respuesta enseguida, y cada
uno de los siguientes lleva los fotogramas que da tiempo a pintar y subir mientras se ve
el anterior (`next_part_frames`): con un segundo GIF de 400 fotogramas detrás de uno de 80,
el pico se quedaba quieto segundos mientras se pintaba y subía.

Las texturas (`assets/picar/texturas.png`, Pixel Perfection CE con licencia CC BY-SA 4.0,
ver `LICENCIA.md` allí) viajan en la meta como data URL; la escena solo las decodifica
cuando cambia su clave.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import io
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene
from bot.services.picar import OFFERS, SCALE, Outcome, format_mult
from bot.services.picar_physics import PICKS, Run
from bot.services.picar_world import (
    AIR,
    BLOCK_ID,
    BLOCKS,
    COLUMNS,
    DRAGON_ROW,
    LAYERS,
    Dimension,
    World,
    layer_at,
)
from bot.utils.gif import GifStream

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "picar"
SCENE = ASSETS / "escena.html"
FONT = Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"

#: Lado de un bloque en la escena (las texturas de 16, por tres). Con 6 columnas de 48 px
#: el GIF mide lo mismo de ancho que con las 9 de 32 de antes, y bloques y pico se ven un
#: 50 % más grandes, como en los juegos de este tipo.
BLOCK_PX = 48
WIDTH = COLUMNS * BLOCK_PX
HEIGHT = 512
#: Alto del marcador de arriba.
HUD_PX = 38
#: Ticks de física por fotograma del GIF y milisegundos de cada fotograma.
TICKS_PER_FRAME = 3
FRAME_MS = 50
#: Fotogramas que duran los efectos y los textos flotantes.
DEBRIS_FRAMES = 14
BOOM_FRAMES = 12
SPARK_FRAMES = 6
BURST_FRAMES = 14
TEXT_FRAMES = 24
_EFFECT_FRAMES = {
    "d": DEBRIS_FRAMES,
    "b": BOOM_FRAMES,
    "h": SPARK_FRAMES,
    "c": BURST_FRAMES,
    "m": BURST_FRAMES,
}
#: Velocidad máxima de la cámara y lo que acelera o frena, en píxeles por tick. Va el
#: doble de rápida que el pico a tope (7 bloques por segundo son 5,6 px por tick): cada
#: fotograma con la cámara en marcha pesa como uno entero, y más despacio se mueve en más
#: fotogramas. Así arranca con 12, 30 y 42 px en tres fotogramas y se mueve en un 5 % más
#: de fotogramas que la de antes (8 px y 0,6 eran 1,8 veces más: el diamante pasaba de
#: 22,6 a 34,7 MB).
CAMERA_MAX_SPEED = 14.0
CAMERA_ACCEL = 2.0
#: La cámara quiere al pico a esta altura de la pantalla.
CAMERA_AIM = 0.3
#: Márgenes que el pico no pasa nunca (arriba, bajo el marcador; abajo).
CAMERA_TOP_MARGIN = HUD_PX + 24
CAMERA_BOTTOM_MARGIN = 72
#: Franja de la pantalla (en píxeles desde arriba) en la que el pico no mueve la cámara.
CAMERA_BAND = (HUD_PX + 50, HEIGHT - 120)
#: Fila más alta que se ve al empezar (el cielo).
SKY_ROWS = 3

#: Cambio de dimensión: fotogramas oscureciendo, pausa con el rótulo y fotogramas
#: aclarando al empezar el GIF siguiente.
TRANSITION_FRAMES = 14
TRANSITION_HOLD_MS = 2_500
INTRO_FRAMES = 12
TRANSITION_DARK = 0.85
#: Final: el pico rompiéndose (o el dragón desvaneciéndose), el cartel entrando y la
#: pausa con el cartel.
SHATTER_FRAMES = 16
DRAGON_FRAMES = 30
RESULT_FRAMES = 8
FINAL_HOLD_MS = 4_000
#: Pausa al cortar un GIF que se pasaría de peso en mitad de una dimensión.
CUT_HOLD_MS = 900
#: Lo que tarda en verse el GIF siguiente: subirlo a Discord cuesta ~1,1 s fijos y
#: ~0,4 s por MB a partir del primero (informe del 14/10/2026), y el cliente tarda un
#: poco más en descargarlo. La pausa del final de cada GIF dura al menos eso, para que
#: el siguiente llegue mientras está quieta y no vuelva a empezar el anterior.
UPLOAD_FIXED_MS = 1_100
UPLOAD_PER_MB_MS = 400
CLIENT_MARGIN_MS = 700
#: Fotogramas que tarda en enseñar lo que vale un bloque misterioso.
MYSTERY_REVEAL_FRAMES = 8
#: Fotogramas de la lluvia de monedas del final (sale con cualquier premio).
COINS_FRAMES = 24
#: Fotogramas del primer GIF y de cada tramo que se pinta de una vez.
FIRST_PART_FRAMES = 80
CHUNK_FRAMES = 90
#: Margen sobre lo que pesa de media cada fotograma al prever si cabe un tramo más.
BUDGET_MARGIN = 1.4
#: Lo que se supone que cuesta pintar y montar un fotograma hasta medirlo en la tirada.
PAINT_MS_PER_FRAME = 20.0

#: Minerales de la capa honda, que se pintan sobre pizarra.
_DEEP_ORES = {"iron", "gold", "redstone", "lapis", "diamond", "emerald"}
_TEXT_LIFE = "#7dff8f"
_TEXT_CRAFT = "#7fe0ff"
_TEXT_MINERAL = "#ffd75e"
_TEXT_MULT = "#ff7df0"
#: Color de cada capa en el medidor de profundidad.
_GAUGE = {
    "soil": "#5aa63c",
    "caves": "#8d8d8d",
    "deep": "#4b5a7a",
    "nether": "#b8402f",
    "end": "#d9d39a",
}
#: Picos con brillo de encantamiento.
_GLINT = {"diamond", "netherite"}
#: Por encima de esta velocidad (píxeles por fotograma) el pico deja estela.
TRAIL_SPEED = 7
_BANNERS = {
    "nether": ("ENTRANDO AL NETHER", "#ff8a4a"),
    "end": ("ENTRANDO AL END", "#d9a7ff"),
}
_DIMENSION_LAYERS = {"nether", "end"}


@functools.cache
def assets() -> dict[str, Any]:
    """Atlas, dragón e índice de casillas, listos para la meta (una vez por proceso)."""
    atlas = (ASSETS / "texturas.png").read_bytes()
    dragon = (ASSETS / "dragon.png").read_bytes()
    return {
        "atlas": "data:image/png;base64," + base64.b64encode(atlas).decode(),
        "dragon": "data:image/png;base64," + base64.b64encode(dragon).decode(),
        "atlasKey": hashlib.sha1(atlas + dragon).hexdigest()[:12],
        "tiles": json.loads((ASSETS / "texturas.json").read_text(encoding="utf-8")),
    }


@functools.cache
def see_through() -> list[int]:
    """Casillas del atlas con algún píxel transparente (los cofres): debajo va la pared."""
    atlas = Image.open(ASSETS / "texturas.png").convert("RGBA")
    alpha = atlas.getchannel("A")
    return [
        index
        for index in range(atlas.width // 16)
        if alpha.crop((index * 16, 0, index * 16 + 16, 16)).getextrema()[0] < 255
        and not assets_key(index).startswith(("pick_", "crack_"))
    ]


def assets_key(index: int) -> str:
    """La clave de una casilla del atlas por su posición."""
    return next(key for key, i in assets()["tiles"].items() if i == index)


def tile_key(block: str, row: int) -> str:
    """Casilla del atlas con la que se pinta un bloque en una fila."""
    if block == "dirt" and row == 0:
        return "grass"
    if block in _DEEP_ORES and layer_at(row).key == "deep":
        return f"deep_{block}"
    return block


def background_key(row: int) -> str | None:
    """Casilla del fondo de una fila sin bloque; `None` en el vacío del End."""
    layer = layer_at(row)
    if layer.dimension is Dimension.END:
        return None
    return {"soil": "dirt", "caves": "stone", "deep": "deepslate", "nether": "netherrack"}[
        layer.key
    ]


# -- Lo que pasa en cada tick ----------------------------------------------------------------


@dataclass
class Timeline:
    """Lo que la escena necesita saber de la partida, tick a tick.

    `life[t]` y `units[t]` son la vida y el multiplicador (en `picar.SCALE`) al final del
    tick `t`; el índice 0 es el inicio. `effects` y `texts` son los efectos con su tick
    de inicio, `dimensions` los ticks en que el pico entra en el Nether y en el End.
    """

    run: Run
    pick: str
    life: list[int]
    units: list[int]
    gone: dict[tuple[int, int], int] = field(default_factory=dict)
    cracks: list[tuple[int, int, int, int]] = field(default_factory=list)
    effects: list[tuple[int, list[Any]]] = field(default_factory=list)
    texts: list[tuple[int, list[Any]]] = field(default_factory=list)
    dimensions: list[tuple[int, str]] = field(default_factory=list)


def timeline(run: Run, *, pick: str, values: dict[str, int]) -> Timeline:
    """Vida, multiplicador, roturas, grietas, efectos y textos de una partida grabada.

    Args:
        values: Lo que suma cada mineral, en `picar.SCALE` (los de la calibración).
    """
    stats = PICKS[pick]
    cost = OFFERS[pick].cost
    tl = Timeline(run, pick, [stats.life] + [0] * run.ticks, [0] * (run.ticks + 1))
    tiles = assets()["tiles"]
    damage: dict[tuple[int, int], int] = {}
    life, total, multiplier = stats.life, 0, 1
    events = iter(run.events)
    event = next(events, None)
    for tick in range(1, run.ticks + 1):
        while event is not None and event.tick == tick:
            e, cell = event, (event.row, event.col)
            cx, cy = (e.col + 0.5) * BLOCK_PX, (e.row + 0.5) * BLOCK_PX
            seed = e.tick * 977 + e.row * 31 + e.col
            if e.kind in ("hit", "break"):
                block = BLOCKS[BLOCK_ID[e.block]]
                life -= block.hardness
                damage[cell] = damage.get(cell, 0) + stats.power
                # Chispas donde el pico toca el bloque: el punto del bloque más cercano.
                px, py, _ = run.frames[tick - 1] if run.frames else (e.col, e.row, 0)
                hx = min(max(px, e.col), e.col + 1) * BLOCK_PX
                hy = min(max(py, e.row), e.row + 1) * BLOCK_PX
                tl.effects.append((tick, ["h", round(hx, 1), round(hy, 1), 0, 0, seed + 1]))
                if e.kind == "hit":
                    stage = min(9, damage[cell] * 10 // block.resistance)
                    tl.cracks.append((e.row, e.col, tick, stage))
            if e.kind in ("break", "blast"):
                tl.gone[cell] = tick
                tl.effects.append((tick, ["d", cx, cy, 0, tiles[tile_key(e.block, e.row)], seed]))
                value = values.get(e.block, 0)
                if value:
                    total += value
                    text = "+" + format_coins(cost * value * multiplier / SCALE)
                    tl.texts.append((tick, [cx, cy, text, 0, _TEXT_MINERAL, 14]))
            elif e.kind == "explode":
                tl.effects.append((tick, ["b", cx, cy, 0, e.amount, seed]))
            elif e.kind == "chest":
                life += e.amount
                tl.effects.append((tick, ["c", cx, cy, 0, _TEXT_LIFE, seed]))
                tl.texts.append((tick, [cx, cy, f"+{e.amount} vida", 0, _TEXT_LIFE, 15]))
            elif e.kind == "craft":
                life += e.amount
                tl.effects.append((tick, ["c", cx, cy, 0, _TEXT_CRAFT, seed]))
                tl.texts.append((tick, [cx, cy, "¡Vida a tope!", 0, _TEXT_CRAFT, 15]))
            elif e.kind == "mult":
                before, multiplier = multiplier, multiplier * e.amount
                tl.effects.append((tick, ["m", cx, cy, 0, _TEXT_MULT, seed]))
                reveal = tick
                if e.block == "mystery":
                    # El misterioso enseña primero la interrogación y luego lo que vale.
                    tl.texts.append((tick, [cx, cy, "?", 0, _TEXT_MULT, 34]))
                    reveal = tick + MYSTERY_REVEAL_FRAMES * TICKS_PER_FRAME
                if before > 1:
                    # Los multiplicadores se apilan a la vista: ×2 · ×3 = ×6.
                    text = f"×{before} · ×{e.amount} = ×{multiplier}"
                    tl.texts.append((reveal, [cx, cy, text, 0, _TEXT_MULT, 20]))
                else:
                    tl.texts.append((reveal, [cx, cy, f"¡TODO ×{e.amount}!", 0, _TEXT_MULT, 22]))
            elif e.kind == "layer" and e.layer in _DIMENSION_LAYERS:
                tl.dimensions.append((tick, e.layer))
            event = next(events, None)
        tl.life[tick] = life
        tl.units[tick] = total * multiplier
    return tl


def camera_path(run: Run) -> list[int]:
    """Parte de arriba de la pantalla (en píxeles de mundo) en cada tick.

    La cámara se queda quieta mientras el pico esté dentro de una franja
    (`CAMERA_BAND`). Mientras está fuera, apunta a dejarlo a `CAMERA_AIM` de la pantalla;
    cuando vuelve a la franja, el objetivo se queda fijo (si siguiera al pico, que no para
    de rebotar, no se pararía nunca). Hacia el objetivo acelera y frena como mucho
    `CAMERA_ACCEL` por tick, sin pasar de `CAMERA_MAX_SPEED`: nunca da un tirón. Quieta
    pesa mucho menos: el GIF solo guarda lo que cambia, y con la cámara en marcha cambia
    la imagen entera. Nunca deja que el pico pase de los márgenes, aunque caiga muy
    deprisa por un barranco.
    """
    cam = float(-SKY_ROWS * BLOCK_PX)
    path = [round(cam)]
    speed = 0.0
    goal: float | None = None
    low, high = CAMERA_BAND
    for _, y, _ in run.frames:
        py = y * BLOCK_PX
        if not cam + low <= py <= cam + high:
            goal = py - HEIGHT * CAMERA_AIM
        if goal is not None:
            gap = goal - cam
            # La velocidad con la que aún puede frenar a tiempo, hacia el objetivo.
            want = min(CAMERA_MAX_SPEED, sqrt(2 * CAMERA_ACCEL * abs(gap)))
            want = want if gap > 0 else -want
            speed += max(-CAMERA_ACCEL, min(CAMERA_ACCEL, want - speed))
            if abs(gap) <= abs(speed) and abs(speed) <= CAMERA_ACCEL * 2:
                cam, goal, speed = goal, None, 0.0
            else:
                cam += speed
        moved = min(max(cam, py - (HEIGHT - CAMERA_BOTTOM_MARGIN)), py - CAMERA_TOP_MARGIN)
        if moved != cam:
            # Empujada por el pico: va con él.
            speed, cam = moved - cam + speed, moved
        path.append(round(cam))
    return path


def format_coins(amount: float) -> str:
    """Y$ del marcador y de los textos: con un decimal por debajo de 10 («2,9 Y$»)."""
    if amount < 10:
        return f"{int(amount * 10) / 10:.1f} Y$".replace(".", ",")
    return f"{int(amount):,} Y$".replace(",", ".")


def depth_label(row: int) -> str:
    """Profundidad del marcador: la capa y la fila en negativo, como en el juego."""
    return f"{layer_at(max(0, row)).name} · capa {-max(0, row)}"


# -- Guion: los fotogramas, con transiciones y final ---------------------------------------


@dataclass
class Shot:
    """Un fotograma del guion: su estado para la escena y cuánto dura.

    `end` marca dónde acaba un GIF a la fuerza (tras la pausa de un cambio de dimensión).
    """

    state: dict[str, Any]
    ms: int = FRAME_MS
    end: bool = False


def _state(tl: Timeline, tick: int, camera: list[int], frame: int) -> dict[str, Any]:
    run = tl.run
    tick = min(max(tick, 0), run.ticks)
    x, y, angle = run.frames[tick - 1] if tick > 0 else (run.frames[0][0], -2.0, 0.0)
    effects = []
    for start, item in tl.effects:
        age = (tick - start) / TICKS_PER_FRAME
        if 0 <= age < _EFFECT_FRAMES[item[0]]:
            effects.append([item[0], item[1], item[2], round(age, 2), item[4], item[5]])
    texts = []
    for start, item in tl.texts:
        age = (tick - start) / TICKS_PER_FRAME
        if 0 <= age < TEXT_FRAMES:
            texts.append([item[0], item[1], item[2], round(age, 2), item[4], item[5]])
    trail = []
    for back in (1, 2):
        earlier = tick - back * TICKS_PER_FRAME
        if earlier < 1:
            break
        ex, ey, ea = run.frames[earlier - 1]
        later = run.frames[earlier + TICKS_PER_FRAME - 1]
        if abs(later[0] - ex) * BLOCK_PX + abs(later[1] - ey) * BLOCK_PX < TRAIL_SPEED:
            break
        trail.append([round(ex * BLOCK_PX, 2), round(ey * BLOCK_PX, 2), round(ea, 1)])
    return {
        "t": tick,
        "frame": frame,
        "cam": camera[tick],
        "x": round(x * BLOCK_PX, 2),
        "y": round(y * BLOCK_PX, 2),
        "a": round(angle, 1),
        "life": tl.life[tick],
        "max": tl.life[0],
        "depth": depth_label(int(y)),
        "row": max(0, int(y)),
        "trail": trail,
        "mult": format_mult(tl.units[tick]),
        "prize": format_coins(OFFERS[tl.pick].cost * tl.units[tick] / SCALE),
        "fx": effects,
        "txt": texts,
    }


def result_card(outcome: Outcome) -> dict[str, Any]:
    """Tarjeta del final: el premio, el multiplicador y hasta dónde llegó."""
    offer = OFFERS[outcome.pick]
    mult = format_mult(outcome.payout * SCALE // offer.cost)
    row = outcome.run.max_row
    info = f"Llegó a la capa {-row} · {layer_at(min(row, DRAGON_ROW - 1)).name}"
    extra = f"Pico de {offer.name.lower()} · {offer.cost:,} Y$".replace(",", ".")
    prize = f"+{outcome.payout:,} Y$".replace(",", ".")
    if outcome.dragon:
        return {
            "title": "¡DRAGÓN!",
            "sub": f"{prize} · {mult}",
            "colour": "#d9a7ff",
            "info": "Ha llegado al fondo del End",
            "extra": extra,
        }
    if outcome.payout == 0:
        return {
            "title": "PICO ROTO",
            "sub": "Sin premio",
            "colour": "#9f9ab8",
            "info": info,
            "extra": extra,
        }
    # Cualquier premio se celebra igual, también si no llega a lo que costó el pico (como
    # en las tragaperras): el título es lo cobrado y el neto lo dice el mensaje.
    return {"title": prize, "sub": mult, "colour": "#ffd75e", "info": info, "extra": extra}


def story(outcome: Outcome, values: dict[str, int]) -> tuple[list[Shot], Timeline]:
    """Todos los fotogramas de la tirada, con sus transiciones y su final."""
    tl = timeline(outcome.run, pick=outcome.pick, values=values)
    run = tl.run
    camera = camera_path(run)
    shots: list[Shot] = []
    changes = dict(tl.dimensions)
    ticks = list(range(0, run.ticks + 1, TICKS_PER_FRAME))
    if ticks[-1] != run.ticks:
        ticks.append(run.ticks)
    intro: tuple[int, str] | None = None
    previous = -1
    for tick in ticks:
        state = _state(tl, tick, camera, len(shots))
        if intro is not None:
            start, layer = intro
            k = (tick - start) / (INTRO_FRAMES * TICKS_PER_FRAME)
            if k < 1:
                title, colour = _BANNERS[layer]
                state["dark"] = round(TRANSITION_DARK * (1 - k), 3)
                state["banner"] = [title, "", round(1 - k, 3), colour]
            else:
                intro = None
        shots.append(Shot(state))
        crossed = [(t, layer) for t, layer in changes.items() if previous < t <= tick]
        previous = tick
        if crossed and tick < run.ticks:
            _, layer = crossed[-1]
            title, colour = _BANNERS[layer]
            for i in range(1, TRANSITION_FRAMES + 1):
                k = i / TRANSITION_FRAMES
                frozen = {**state, "dark": round(TRANSITION_DARK * k, 3), "fx": [], "txt": []}
                frozen["banner"] = [title, "", round(k, 3), colour]
                frozen["frame"] = len(shots)
                shots.append(Shot(frozen))
            shots[-1].ms = TRANSITION_HOLD_MS
            shots[-1].end = True
            intro = (tick, layer)

    last = shots[-1].state
    tiles = assets()["tiles"]
    if outcome.dragon:
        for i in range(1, DRAGON_FRAMES + 1):
            shots.append(Shot({**last, "slain": i, "frame": len(shots), "fx": [], "txt": []}))
    else:
        pick_tile = tiles[f"pick_{outcome.pick}"]
        seed = run.seed * 7 + 3
        for i in range(SHATTER_FRAMES):
            debris = [["d", last["x"], last["y"], i, pick_tile, seed]]
            shots.append(
                Shot({**last, "hidePick": True, "fx": debris, "txt": [], "frame": len(shots)})
            )
    final = {
        **shots[-1].state,
        "fx": [],
        "txt": [],
        "mult": format_mult(outcome.payout * SCALE // OFFERS[outcome.pick].cost),
        "prize": format_coins(outcome.payout),
    }
    card = result_card(outcome)
    frames = COINS_FRAMES if outcome.payout and not outcome.dragon else RESULT_FRAMES
    for i in range(1, frames + 1):
        alpha = round(min(1.0, i / RESULT_FRAMES), 3)
        state = {**final, "card": {**card, "alpha": alpha}}
        if frames == COINS_FRAMES:
            state["coins"] = [i, run.seed % 997]
        shots.append(Shot({**state, "frame": len(shots)}))
    shots[-1].ms = FINAL_HOLD_MS
    return shots, tl


# -- Meta de la escena -----------------------------------------------------------------------


def segment_meta(tl_run: Run, gone: dict, cracks: list, rows: range, *, pick: str) -> dict:
    """Meta de la escena para unos fotogramas que enseñan las filas `rows`."""
    art = assets()
    tiles = art["tiles"]
    world = World(tl_run.seed)
    grid, gone_rows, bg = [], [], []
    for row in rows:
        if row < 0 or row >= DRAGON_ROW:
            grid.append([-1] * COLUMNS)
            gone_rows.append([0] * COLUMNS)
            bg.append(-2)
            continue
        cells = world.row(row)
        grid.append([-1 if b == AIR else tiles[tile_key(BLOCKS[b].key, row)] for b in cells])
        gone_rows.append([gone.get((row, c), 0) for c in range(COLUMNS)])
        key = background_key(row)
        bg.append(-2 if key is None else tiles[key])
    return {
        "atlas": art["atlas"],
        "dragon": art["dragon"],
        "atlasKey": art["atlasKey"],
        "tiles": tiles,
        "pick": f"pick_{pick}",
        "top": rows.start,
        "grid": grid,
        "gone": gone_rows,
        "bg": bg,
        "cracks": [list(c) for c in cracks if rows.start <= c[0] < rows.stop],
        "dragonRow": DRAGON_ROW,
        "layers": [[layer.top, layer.key, _GAUGE[layer.key]] for layer in LAYERS],
        "shiny": sorted(
            {tiles[tile_key(b.key, row)] for b in BLOCKS if b.mineral for row in (5, 130)}
        ),
        "glint": pick in _GLINT,
        "seeThrough": see_through(),
    }


def _rows_for(states: list[dict[str, Any]]) -> range:
    top = min(s["cam"] for s in states) // BLOCK_PX - 1
    bottom = (max(s["cam"] for s in states) + HEIGHT) // BLOCK_PX + 2
    return range(top, bottom)


# -- Partes del GIF --------------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """Un GIF de la tirada.

    `ms` es lo que dura entero y `hold_ms` lo que dura su pausa final: el rótulo de una
    dimensión, el cartel del premio o, si `cut`, un corte a mitad de partida (por peso o
    para que el primero salga pronto), que es una pausa que no debería verse.
    """

    gif: bytes
    ms: int
    hold_ms: int
    last: bool
    frames: int
    cut: bool = False


def upload_ms(size: int) -> int:
    """Lo que se espera que tarde Discord en aceptar un archivo de `size` bytes."""
    megabytes = size / (1024 * 1024)
    return round(UPLOAD_FIXED_MS + UPLOAD_PER_MB_MS * max(0.0, megabytes - 1))


def swap_ms(size: int) -> int:
    """Milisegundos desde que se pide cambiar el GIF hasta que se ve el nuevo."""
    return upload_ms(size) + CLIENT_MARGIN_MS


def next_part_frames(part: Part, paint_ms: float) -> int:
    """Fotogramas que caben en el GIF siguiente sin que se quede esperando.

    El siguiente se pinta mientras se ve `part` y se sube antes de que acabe: tiene que
    estar pintado y subido en lo que dura `part` en movimiento (la pausa de un corte no
    cuenta: es justo lo que se quiere evitar; la del rótulo de una dimensión, sí). Pintar
    cuesta `paint_ms` por fotograma (medido en esta tirada) y subir, lo que pesan de media
    los de `part`. Así los GIF crecen: corto, más largo, más largo.
    """
    available = part.ms - (part.hold_ms if part.cut else 0) - UPLOAD_FIXED_MS - CLIENT_MARGIN_MS
    upload_per_frame = len(part.gif) / max(part.frames, 1) / (1024 * 1024) * UPLOAD_PER_MB_MS
    return max(CHUNK_FRAMES, int(available / (paint_ms + upload_per_frame)))


class PicarScene:
    """Dibuja las tiradas con Node, si no con Chromium; sin ninguno, solo la imagen final.

    Args:
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(self, *, executable_path: str | None = None, node: str | None = None) -> None:
        self.painter = NodeScene(SCENE, name="El pintor del pico", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador del pico",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya solo queda la imagen de Pillow."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def _paint(
        self, outcome: Outcome, tl: Timeline, states: list[dict[str, Any]]
    ) -> list[Image.Image] | None:
        meta = segment_meta(tl.run, tl.gone, tl.cracks, _rows_for(states), pick=outcome.pick)
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        if patches is None:
            return None
        if all("image" in p for p in patches):
            return [p["image"] for p in patches]
        return await asyncio.to_thread(browser_scene.assemble, patches, (WIDTH, HEIGHT))

    async def parts(
        self, outcome: Outcome, values: dict[str, int], *, budget: int
    ) -> AsyncIterator[Part]:
        """Los GIF de la tirada, uno detrás de otro, pintados según se piden.

        Quien llama enseña uno y, mientras se ve, pide el siguiente. Si no hay ni Node
        ni navegador no sale ninguno (queda `still`).

        Args:
            values: Lo que suma cada mineral (para los textos y el marcador).
            budget: Bytes que puede pesar cada GIF como mucho.
        """
        shots, tl = story(outcome, values)
        # Un primer GIF corto solo compensa si luego queda bastante partida.
        # Fotogramas que puede llevar el GIF en curso antes de cortarlo (sin contar el
        # peso). Un primer GIF corto solo compensa si luego queda bastante partida.
        cap = FIRST_PART_FRAMES if len(shots) > FIRST_PART_FRAMES * 3 // 2 else len(shots)
        paint_ms = PAINT_MS_PER_FRAME
        measured = False
        stream = GifStream((WIDTH, HEIGHT), loop=False)
        i = 0
        while i < len(shots):
            limit = max(1, min(CHUNK_FRAMES, cap - stream.frames))
            chunk: list[Shot] = []
            while i < len(shots) and len(chunk) < limit:
                chunk.append(shots[i])
                i += 1
                if chunk[-1].end:
                    break
            if stream.frames:
                per_frame = stream.size / stream.frames
                if stream.size + per_frame * len(chunk) * BUDGET_MARGIN > budget:
                    part = await self._finish(stream, CUT_HOLD_MS, last=False, pause=True)
                    yield part
                    cap = next_part_frames(part, paint_ms)
                    stream = GifStream((WIDTH, HEIGHT), loop=False)
            started = time.perf_counter()
            images = await self._paint(outcome, tl, [shot.state for shot in chunk])
            if images is None:
                return
            await asyncio.to_thread(stream.add, images, [shot.ms for shot in chunk])
            spent = (time.perf_counter() - started) * 1000 / len(chunk)
            paint_ms = (paint_ms + spent) / 2 if measured else spent
            measured = True
            if chunk[-1].end or i >= len(shots):
                part = await self._finish(stream, chunk[-1].ms, last=i >= len(shots))
                yield part
                cap = next_part_frames(part, paint_ms)
                stream = GifStream((WIDTH, HEIGHT), loop=False)
            elif stream.frames >= cap:
                part = await self._finish(stream, CUT_HOLD_MS, last=False, pause=True)
                yield part
                cap = next_part_frames(part, paint_ms)
                stream = GifStream((WIDTH, HEIGHT), loop=False)

    @staticmethod
    async def _finish(stream: GifStream, hold: int, *, last: bool, pause: bool = False) -> Part:
        """Cierra un GIF.

        Con `pause`, alarga el último fotograma `hold` (un corte a mitad de dimensión); si
        no, `hold` es lo que ya dura el último fotograma del guion. Si no es el último
        GIF, la pausa dura al menos lo que tarda en verse el siguiente (`swap_ms`).
        """
        assert stream.last is not None
        if pause:
            await asyncio.to_thread(stream.add, [stream.last], hold)
        if not last and (extra := swap_ms(stream.size) - hold) > 0:
            # El mismo fotograma otra vez: el GIF solo suma su tiempo al anterior.
            await asyncio.to_thread(stream.add, [stream.last], extra)
            hold += extra
        data = await asyncio.to_thread(stream.finish)
        return Part(data, stream.duration, hold, last, stream.frames, cut=pause)

    async def still(self, outcome: Outcome, values: dict[str, int]) -> bytes:
        """PNG del final de la tirada (lo que se queda en el mensaje al acabar el GIF)."""
        shots, tl = story(outcome, values)
        images = await self._paint(outcome, tl, [shots[-1].state])
        if images is None:
            return await asyncio.to_thread(pillow_still, outcome, shots[-1].state, tl)

        def encode() -> bytes:
            buffer = io.BytesIO()
            images[0].save(buffer, format="PNG", compress_level=6)
            return buffer.getvalue()

        return await asyncio.to_thread(encode)


def pillow_still(outcome: Outcome, state: dict[str, Any], tl: Timeline) -> bytes:
    """El final de la tirada dibujado con Pillow, sin escena (plan C).

    Pinta los bloques que se ven con las mismas texturas, el pico y el resultado en
    texto. Sin efectos ni grietas: solo hace falta si no hay Node ni navegador.
    """
    atlas = Image.open(ASSETS / "texturas.png").convert("RGBA")
    tiles = assets()["tiles"]

    def tile(key: str) -> Image.Image:
        i = tiles[key]
        return atlas.crop((i * 16, 0, i * 16 + 16, 16)).resize((BLOCK_PX, BLOCK_PX), 0)

    image = Image.new("RGBA", (WIDTH, HEIGHT), (13, 8, 20, 255))
    world = World(outcome.seed)
    cam = state["cam"]
    for row in range(cam // BLOCK_PX, (cam + HEIGHT) // BLOCK_PX + 1):
        sy = row * BLOCK_PX - cam
        for col in range(COLUMNS):
            x = col * BLOCK_PX
            if row < 0:
                image.paste((120, 180, 240, 255), (x, sy, x + BLOCK_PX, sy + BLOCK_PX))
                continue
            if row >= DRAGON_ROW:
                continue
            block = world.block(row, col)
            if block != AIR and tl.gone.get((row, col), 0) == 0:
                image.alpha_composite(tile(tile_key(BLOCKS[block].key, row)), (x, sy))
    if not state.get("hidePick"):
        pick = tile(f"pick_{outcome.pick}")
        half = BLOCK_PX // 2
        image.alpha_composite(pick, (int(state["x"]) - half, int(state["y"]) - cam - half))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, HUD_PX), fill=(10, 10, 16, 230))
    font = ImageFont.truetype(str(FONT), 14)
    card = result_card(outcome)
    draw.text((8, 4), f"{card['title']}  {card['sub']}", font=font, fill=(255, 215, 94))
    draw.text((8, 21), state["depth"], font=font, fill=(215, 215, 224))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue()
