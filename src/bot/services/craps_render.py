"""Dados: la tirada, lo que se ve en cada fotograma y el dibujo con Pillow.

La imagen buena la pinta Chromium con canvas (`bot.services.craps_scene` y
`assets/dados/escena.html`). Este módulo decide **qué** se ve y lo pinta
también con Pillow, más sencillo, para cuando no hay navegador. Las dos
versiones leen los mismos diccionarios (`meta_state`, `board_state`,
`throw_states`): los dados están en el mismo sitio y caen igual en las dos.

**La cámara.** La mesa se ve desde delante y desde arriba, en perspectiva
(`project`). El mundo va en unidades de mesa: `x` a la derecha, `y` hacia el
fondo y `z` hacia arriba, con el tapete en `z = 0` y la pared de pirámides
en `y = WALL_Y`. El tapete se dibuja visto desde arriba y se pasa a la
cámara fila a fila (`floor_rows`): con la cámara centrada y sin girar, las
líneas horizontales del tapete siguen horizontales en pantalla, así que
cada fila de la pantalla es una fila del tapete estirada.

**La tirada** (`simulate`). Los dos dados entran por abajo a la izquierda,
vuelan hacia el fondo, chocan contra la pared, rebotan en el tapete y ruedan
hasta pararse. Es una física sencilla (gravedad, rebotes que pierden fuerza,
rozamiento) con su semilla, así que la misma tirada se dibuja igual. El giro
se integra con la velocidad: en el aire dan vueltas y en el tapete ruedan
(el ángulo es la distancia entre el medio lado). Para que caigan en la cara
que toca, toda la orientación se corrige por la derecha con un giro fijo
(`R(t) · D`): eso no cambia cómo gira el dado, solo qué cara queda arriba
al final.

**El resultado no se ve antes de tiempo.** Hasta que los dados se paran, el
panel enseña lo de antes de tirar. Luego aparecen el total, el cartel y el
movimiento de las fichas (las que gana llegan desde la banca; las que
pierde se las lleva Perro Sanxe) y el disco del punto se mueve.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from bot.services.craps import (
    BAR,
    CRAPS,
    NATURALS,
    POINTS,
    TOTAL_NAMES,
    Bet,
    CrapsGame,
    Hand,
    Roll,
    Status,
    format_odds,
    milestone,
)
from bot.utils.gif import local_palette_gif

W, H = 640, 360
#: Panel de la derecha (en píxeles de pantalla).
PANEL_X, PANEL_W = 452, 176

# -- Cámara -----------------------------------------------------------------------------------

CAM_Y, CAM_Z = -100.0, 560.0
PITCH = math.radians(64)
FOCAL = 620.0
#: Punto de la pantalla hacia el que mira la cámara.
VIEW_X, VIEW_Y = 225.0, 165.0
_COS, _SIN = math.cos(PITCH), math.sin(PITCH)

#: Tapete: de `-TABLE_HALF` a `TABLE_HALF` en x y de `TABLE_NEAR` a `WALL_Y` en y.
TABLE_HALF = 230.0
TABLE_NEAR = -40.0
WALL_Y = 300.0
WALL_HEIGHT = 60.0
#: Píxeles de la textura del tapete por unidad de mesa.
TEXTURE_SCALE = 3

#: Medio lado de un dado.
HALF = 20.0


def project(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Punto del mundo → `(x, y)` de pantalla y profundidad (distancia a lo largo de la vista)."""
    dy, dz = y - CAM_Y, z - CAM_Z
    depth = dy * _COS - dz * _SIN
    up = dy * _SIN + dz * _COS
    return VIEW_X + FOCAL * x / depth, VIEW_Y - FOCAL * up / depth, depth


def floor_y(screen_y: float) -> float:
    """El `y` del tapete que cae en la fila `screen_y` de la pantalla."""
    s = (VIEW_Y - screen_y) / FOCAL
    return CAM_Y + CAM_Z * (_COS + s * _SIN) / (_SIN - s * _COS)


def wall_z(screen_y: float) -> float:
    """La altura de la pared que cae en la fila `screen_y` de la pantalla."""
    s = (VIEW_Y - screen_y) / FOCAL
    dy = WALL_Y - CAM_Y
    return CAM_Z + dy * (s * _COS - _SIN) / (_COS + s * _SIN)


@cache
def floor_rows(scale: int = 2) -> tuple[tuple[float, float, float, float, float], ...]:
    """Filas del tapete en pantalla: `(y, y0, y1, izquierda, derecha)`.

    `y` es la fila de pantalla (en píxeles, de `1/scale` en `1/scale`), `y0`
    e `y1` el trozo del tapete (en unidades de mesa) que cae en ella e
    `izquierda` y `derecha` dónde quedan los bordes del tapete. Lo usan las
    dos versiones del dibujo para pasar el tapete a la cámara.
    """
    top = project(0, WALL_Y, 0)[1]
    rows = []
    step = 1 / scale
    k = math.floor(top * scale) / scale
    while k < H:
        y0 = max(TABLE_NEAR, min(WALL_Y, floor_y(k + step)))
        y1 = max(TABLE_NEAR, min(WALL_Y, floor_y(k)))
        depth = project(0, (y0 + y1) / 2, 0)[2]
        half = FOCAL * TABLE_HALF / depth
        rows.append((k, y0, y1, VIEW_X - half, VIEW_X + half))
        k += step
    return tuple(rows)


@cache
def wall_rows(scale: int = 2) -> tuple[tuple[float, float, float, float, float], ...]:
    """Filas de la pared de pirámides, como `floor_rows` (con `z` en vez de `y`)."""
    bottom = project(0, WALL_Y, 0)[1]
    top = project(0, WALL_Y, WALL_HEIGHT)[1]
    rows = []
    step = 1 / scale
    k = math.floor(top * scale) / scale
    while k < bottom:
        z0 = max(0.0, min(WALL_HEIGHT, wall_z(k + step)))
        z1 = max(0.0, min(WALL_HEIGHT, wall_z(k)))
        depth = project(0, WALL_Y, (z0 + z1) / 2)[2]
        half = FOCAL * TABLE_HALF / depth
        rows.append((k, z0, z1, VIEW_X - half, VIEW_X + half))
        k += step
    return tuple(rows)


# -- Giros --------------------------------------------------------------------------------------

Vec = tuple[float, float, float]
Mat = tuple[Vec, Vec, Vec]

IDENTITY: Mat = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _mul(a: Mat, b: Mat) -> Mat:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def _apply(m: Mat, v: Vec) -> Vec:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _transpose(m: Mat) -> Mat:
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]


def rotation(axis: Vec, angle: float) -> Mat:
    """Giro de `angle` radianes alrededor de `axis` (Rodrigues)."""
    n = math.sqrt(sum(c * c for c in axis))
    if n < 1e-9 or abs(angle) < 1e-12:
        return IDENTITY
    x, y, z = (c / n for c in axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


# -- El dado ------------------------------------------------------------------------------------

#: Cada cara: normal y ejes de la textura (derecha, abajo), en el sistema del dado.
#: Las opuestas suman 7, como en un dado de verdad.
FACES: dict[int, tuple[Vec, Vec, Vec]] = {
    1: ((0, 0, 1), (1, 0, 0), (0, -1, 0)),
    6: ((0, 0, -1), (1, 0, 0), (0, 1, 0)),
    2: ((0, -1, 0), (1, 0, 0), (0, 0, -1)),
    5: ((0, 1, 0), (-1, 0, 0), (0, 0, -1)),
    3: ((1, 0, 0), (0, 1, 0), (0, 0, -1)),
    4: ((-1, 0, 0), (0, -1, 0), (0, 0, -1)),
}
CORNERS: tuple[Vec, ...] = tuple(
    (sx * HALF, sy * HALF, sz * HALF) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)
)
#: De dónde viene la luz (arriba, a la izquierda y hacia la cámara).
LIGHT: Vec = (-0.45, -0.55, 0.7)
_LIGHT_N = math.sqrt(sum(c * c for c in LIGHT))
LIGHT = (LIGHT[0] / _LIGHT_N, LIGHT[1] / _LIGHT_N, LIGHT[2] / _LIGHT_N)


def face_up(value: int, yaw: float) -> Mat:
    """Orientación del dado quieto con `value` arriba, girado `yaw` sobre la vertical."""
    normal = FACES[value][0]
    up = (0.0, 0.0, 1.0)
    dot = normal[2]
    if dot > 0.999:
        base = IDENTITY
    elif dot < -0.999:
        base = rotation((1.0, 0.0, 0.0), math.pi)
    else:
        axis = (normal[1] * up[2] - normal[2] * up[1], normal[2] * up[0] - normal[0] * up[2], 0.0)
        base = rotation(axis, math.acos(dot))
    return _mul(rotation(up, yaw), base)


def value_up(m: Mat) -> int:
    """La cara que mira más hacia arriba con la orientación `m`."""
    return max(FACES, key=lambda v: _apply(m, FACES[v][0])[2])


def resting_height(m: Mat) -> float:
    """Lo que sube el centro sobre el punto más bajo del dado con la orientación `m`."""
    return -min(_apply(m, c)[2] for c in CORNERS)


def die_geometry(x: float, y: float, lift: float, m: Mat) -> dict[str, Any]:
    """Lo que hay que pintar de un dado: caras visibles, silueta, sombra y profundidad.

    Args:
        x, y: Dónde está sobre el tapete.
        lift: Altura de su punto más bajo sobre el tapete.
        m: Orientación.

    Returns:
        `faces`: lista de `{v, q, l}` (valor, las cuatro esquinas en pantalla
        empezando por arriba a la izquierda de la textura, y luz de 0 a 1);
        `hull`: la silueta; `shadow`: elipse de la sombra; `depth`: para
        ordenar los dados; `x`, `y`: el centro en pantalla.
    """
    cz = lift + resting_height(m)
    center = (x, y, cz)
    cam = (0.0, CAM_Y, CAM_Z)
    faces = []
    points = []
    for value, (normal, right, down) in FACES.items():
        n = _apply(m, normal)
        mid = (x + n[0] * HALF, y + n[1] * HALF, cz + n[2] * HALF)
        to_cam = (cam[0] - mid[0], cam[1] - mid[1], cam[2] - mid[2])
        if n[0] * to_cam[0] + n[1] * to_cam[1] + n[2] * to_cam[2] <= 0:
            continue
        r, d = _apply(m, right), _apply(m, down)
        quad = []
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            px = mid[0] + (r[0] * su + d[0] * sv) * HALF
            py = mid[1] + (r[1] * su + d[1] * sv) * HALF
            pz = mid[2] + (r[2] * su + d[2] * sv) * HALF
            sx, sy, _ = project(px, py, pz)
            quad.append((round(sx, 2), round(sy, 2)))
            points.append((sx, sy))
        light = max(0.0, n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2])
        faces.append({"v": value, "q": quad, "l": round(0.35 + 0.65 * light, 3)})
    sx, sy, depth = project(*center)
    gx, gy, _ = project(x, y, 0)
    gx2, _, _ = project(x + HALF * 1.5, y, 0)
    _, gy2, _ = project(x, y + HALF * 1.5, 0)
    k = min(1.0, lift / 120)
    rx = abs(gx2 - gx) * (1 + 0.5 * k)
    return {
        "x": round(sx, 2),
        "y": round(sy, 2),
        "depth": round(depth, 2),
        "faces": faces,
        "hull": _hull(points),
        "shadow": {
            "x": round(gx, 2),
            "y": round(gy, 2),
            "rx": round(rx, 2),
            "ry": round(max(2.0, abs(gy - gy2) * (1 + 0.5 * k)), 2),
            "a": round(0.5 * (1 - 0.7 * k), 3),
        },
    }


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Envolvente convexa (cadena monótona), para pintar la silueta del dado."""
    pts = sorted(set((round(x, 2), round(y, 2)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# -- La tirada ----------------------------------------------------------------------------------

FRAME_MS = 40
#: El último fotograma se queda quieto: el PNG llega antes de que se note el bucle.
FINAL_FRAME_MS = 60_000
#: Subpasos de la física por fotograma (rebotes más finos).
SUBSTEPS = 4
GRAVITY = 1500.0
#: Lo que conserva la velocidad vertical al rebotar en el tapete y la de fondo al chocar.
FLOOR_BOUNCE, WALL_BOUNCE = 0.42, 0.42
#: Frenada al rodar sobre el tapete (unidades/s²) y lo que se pierde en cada bote.
ROLL_FRICTION, BOUNCE_FRICTION = 380.0, 0.82
#: Por debajo de esta velocidad, el dado en el tapete se para.
REST_SPEED = 14.0
MAX_FRAMES = 80
#: Fotogramas quietos al final de la tirada antes del cartel.
HOLD_FRAMES = 3
REVEAL_FRAMES = 12
#: Dónde pueden quedarse quietos los dados (para que se vean y no los tape el panel).
REST_X = (-185.0, 175.0)
REST_Y = (95.0, 232.0)
#: Distancia mínima entre los dos dados quietos.
REST_GAP = HALF * 3.0


@dataclass(frozen=True, slots=True)
class DieRest:
    """Dónde se quedó un dado: posición sobre el tapete, giro y valor."""

    x: float
    y: float
    yaw: float
    value: int

    def geometry(self) -> dict[str, Any]:
        """Lo que se pinta de él quieto (`die_geometry`)."""
        return die_geometry(self.x, self.y, 0.0, face_up(self.value, self.yaw))


#: Dados de la mesa antes de la primera tirada: un 3 y un 4.
OPENING_REST = (DieRest(-40.0, 150.0, 0.35, 3), DieRest(35.0, 168.0, -0.2, 4))


@dataclass(frozen=True, slots=True)
class Track:
    """Recorrido de un dado: dónde está y cómo está girado en cada fotograma."""

    x: list[float]
    y: list[float]
    lift: list[float]
    spin: list[Mat]
    #: Fotograma en que choca contra la pared (`None` si no llega).
    wall_hit: int | None


def _fly(rng: random.Random, start: Vec, velocity: Vec) -> tuple[Track, Mat]:
    """Física de un dado sin corregir la cara final.

    Returns:
        El recorrido y la orientación con la que acaba.
    """
    x, y, z = start
    vx, vy, vz = velocity
    m = face_up(rng.randint(1, 6), rng.uniform(0, 2 * math.pi))
    # En el aire da vueltas sobre un eje cualquiera; en el tapete, rueda.
    spin_axis: Vec = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-0.4, 0.4))
    spin_speed = rng.uniform(14, 20)
    xs, ys, lifts, spins = [], [], [], []
    wall_hit = None
    dt = FRAME_MS / 1000 / SUBSTEPS
    resting = 0
    for frame in range(MAX_FRAMES):
        for _ in range(SUBSTEPS):
            if resting:
                break
            vz -= GRAVITY * dt
            x, y, z = x + vx * dt, y + vy * dt, z + vz * dt
            on_floor = z <= 0
            if on_floor:
                z = 0.0
                if vz < -60:
                    vz = -vz * FLOOR_BOUNCE
                    vx *= BOUNCE_FRICTION
                    vy *= BOUNCE_FRICTION
                else:
                    vz = 0.0
            if y > WALL_Y - HALF * 1.4 and vy > 0:
                y = WALL_Y - HALF * 1.4
                vy = -vy * WALL_BOUNCE
                vx *= 0.9
                vz += rng.uniform(60, 160)
                spin_axis = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-0.5, 0.5))
                if wall_hit is None:
                    wall_hit = frame
            for bound, sign in ((-TABLE_HALF + HALF * 1.4, -1), (TABLE_HALF - HALF * 1.4, 1)):
                if (x - bound) * sign > 0 and vx * sign > 0:
                    x = bound
                    vx = -vx * WALL_BOUNCE
            speed = math.hypot(vx, vy)
            if on_floor and z == 0 and vz == 0:
                slow = max(0.0, speed - ROLL_FRICTION * dt)
                if speed > 0:
                    vx, vy = vx * slow / speed, vy * slow / speed
                speed = slow
                if speed < REST_SPEED:
                    vx = vy = 0.0
                    resting = 1
                # Rueda: el eje es perpendicular al avance y el ángulo, distancia / medio lado.
                m = _mul(rotation((-vy, vx, 0.0), speed * dt / HALF), m)
            else:
                m = _mul(rotation(spin_axis, spin_speed * dt), m)
        xs.append(x)
        ys.append(y)
        lifts.append(max(0.0, z))
        spins.append(m)
        if resting:
            resting += 1
            if resting > HOLD_FRAMES:
                break
    return Track(xs, ys, lifts, spins, wall_hit), m


def _launch(rng: random.Random, index: int) -> tuple[Vec, Vec]:
    """Desde dónde y con qué fuerza se tira cada dado (el segundo, un poco al lado)."""
    x = rng.uniform(-200, -150) + 26 * index
    y = rng.uniform(-60, -40) - 12 * index
    z = rng.uniform(70, 100) + 8 * index
    vx = rng.uniform(80, 220)
    vy = rng.uniform(560, 680)
    vz = rng.uniform(140, 260)
    return (x, y, z), (vx, vy, vz)


def simulate(dice: tuple[int, int], seed: int) -> tuple[list[Track], tuple[DieRest, DieRest]]:
    """Tira los dos dados con su semilla para que caigan en `dice`.

    Prueba lanzamientos hasta que los dos se paran donde se ven y sin
    pisarse; luego corrige cada orientación (`R(t) · D`) para que el valor
    de arriba sea el que toca.

    Returns:
        Los dos recorridos (con el mismo número de fotogramas) y dónde se
        quedan.
    """
    rng = random.Random(seed)
    best: tuple[list[tuple[Track, Mat]], float] | None = None
    for _ in range(40):
        flights = [_fly(rng, *_launch(rng, i)) for i in range(2)]
        (a, _), (b, _) = flights
        ends = [(t.x[-1], t.y[-1]) for t in (a, b)]
        gap = math.dist(*ends)
        inside = all(REST_X[0] <= x <= REST_X[1] and REST_Y[0] <= y <= REST_Y[1] for x, y in ends)
        score = gap if inside else gap - 1_000
        if best is None or score > best[1]:
            best = (flights, score)
        if inside and gap >= REST_GAP:
            break
    assert best is not None
    flights = best[0]
    frames = max(len(t.x) for t, _ in flights)
    tracks: list[Track] = []
    rests: list[DieRest] = []
    for (track, end), value in zip(flights, dice, strict=True):
        yaw = rng.uniform(-0.6, 0.6)
        target = face_up(value, math.atan2(end[1][0], end[0][0]) + yaw * 0.25)
        # Corrección por la derecha: misma forma de girar, otra cara al final.
        fix = _mul(_transpose(end), target)
        spins = [_mul(m, fix) for m in track.spin]
        pad = frames - len(track.x)
        tracks.append(
            Track(
                track.x + [track.x[-1]] * pad,
                track.y + [track.y[-1]] * pad,
                track.lift + [0.0] * pad,
                spins + [spins[-1]] * pad,
                track.wall_hit,
            )
        )
        yaw_end = math.atan2(target[1][0], target[0][0])
        rests.append(DieRest(track.x[-1], track.y[-1], yaw_end, value))
    return tracks, (rests[0], rests[1])


# -- Fichas, disco y tapete ---------------------------------------------------------------------

#: Casillas de los puntos: centro en x de cada una y fila (en unidades de mesa).
BOX_X: dict[int, float] = {p: -175.0 + 70.0 * i for i, p in enumerate(POINTS)}
BOX_Y = (242.0, 292.0)
#: Dónde descansa el disco apagado (OFF).
PUCK_OFF = (-205.0, 215.0)
#: Bandas de Pase y No pase (y de las fichas).
PASS_Y = (4.0, 30.0)
DONT_Y = (34.0, 56.0)
#: Banca: de donde salen y adonde van las fichas.
BANK = (0.0, 292.0)
#: Colores de las fichas según lo que valen.
CHIP_COLORS = (
    (100, "#e8e4da"),
    (1_000, "#d23a3a"),
    (10_000, "#2f9e5b"),
    (100_000, "#222831"),
    (1_000_000, "#7b4bd6"),
)


def _amount(value: int) -> str:
    return f"{value:,}".replace(",", ".") + " Y$"


def _plural(n: int, word: str) -> str:
    """`1 tirada`, `3 tiradas`."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _short(value: int) -> str:
    """`1500` → `1,5k`, `2000000` → `2M`: lo que cabe en una ficha."""
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= size:
            text = f"{value / size:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            return text + suffix
    return str(value)


def chip_color(amount: int) -> str:
    """Color de la ficha de arriba de un montón de `amount`."""
    for limit, color in CHIP_COLORS:
        if amount < limit:
            return color
    return CHIP_COLORS[-1][1]


def chip_stack(amount: int, x: float, y: float, alpha: float = 1.0) -> dict[str, Any]:
    """Un montón de fichas en el tapete, ya en pantalla.

    Cuantas más cifras, más alto el montón (de 1 a 6 fichas).
    """
    sx, sy, _ = project(x, y, 0)
    rx = abs(project(x + 13, y, 0)[0] - sx)
    return {
        "x": round(sx, 2),
        "y": round(sy, 2),
        "r": round(rx, 2),
        "n": max(1, min(6, len(str(max(1, amount))) - 1)),
        "c": chip_color(amount),
        "t": _short(amount),
        "a": round(alpha, 3),
    }


def _bet_spot(bet: Bet) -> tuple[float, float]:
    band = PASS_Y if bet is Bet.PASS else DONT_Y
    return -150.0, (band[0] + band[1]) / 2


def puck(point: int | None) -> dict[str, Any]:
    """El disco: encendido sobre la casilla del punto o apagado en su esquina."""
    if point is None:
        x, y = PUCK_OFF
    else:
        x, y = BOX_X[point], (BOX_Y[0] + BOX_Y[1]) / 2
    sx, sy, _ = project(x, y, 0)
    return {
        "x": round(sx, 2),
        "y": round(sy, 2),
        "r": round(abs(project(x + 15, y, 0)[0] - sx), 2),
        "on": point is not None,
    }


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def _slide(a: dict[str, Any], b: dict[str, Any], t: float) -> dict[str, Any]:
    """Mitad de camino entre dos posiciones del disco."""
    k = _ease(t)
    out = dict(b)
    for key in ("x", "y", "r"):
        out[key] = round(a[key] + (b[key] - a[key]) * k, 2)
    out["on"] = b["on"] if t > 0.5 else a["on"]
    return out


# -- Lo que se ve: panel, historial, cartel -----------------------------------------------------


def meta_state() -> dict[str, Any]:
    """Lo fijo de la escena: las filas del tapete y la pared y dónde van las casillas."""
    boxes = []
    for p in POINTS:
        x0, y0, _ = project(BOX_X[p] - 33, BOX_Y[0], 0)
        x1, y1, _ = project(BOX_X[p] + 33, BOX_Y[1], 0)
        boxes.append({"p": p, "x0": round(x0, 2), "x1": round(x1, 2)})
    return {
        "floor": [[round(v, 3) for v in row] for row in floor_rows()],
        "wall": [[round(v, 3) for v in row] for row in wall_rows()],
        "table": {
            "half": TABLE_HALF,
            "near": TABLE_NEAR,
            "far": WALL_Y,
            "wall": WALL_HEIGHT,
            "scale": TEXTURE_SCALE,
            "boxes": {str(p): BOX_X[p] for p in POINTS},
            "boxY": BOX_Y,
            "pass": PASS_Y,
            "dont": DONT_Y,
            "off": PUCK_OFF,
        },
    }


def roll_kind(roll: Roll, bet: Bet) -> str:
    """Cómo se pinta una tirada en el historial.

    `win` o `lose` si decidió la partida (según la apuesta), `push` en la
    barra, `point` si puso el punto y `none` si no decidió nada.
    """
    total = roll.total
    passing = bet is Bet.PASS
    if roll.come_out:
        if total in NATURALS:
            return "win" if passing else "lose"
        if total == BAR and not passing:
            return "push"
        if total in CRAPS:
            return "lose" if passing else "win"
        return "point"
    if roll.made:
        return "win" if passing else "lose"
    if roll.seven_out:
        return "lose" if passing else "win"
    return "none"


@dataclass(frozen=True, slots=True)
class Table:
    """Lo que enseña el panel: la partida (o la última), la mano y la apuesta de la mesa.

    Attributes:
        game: La partida en curso o la última; `None` antes de la primera.
        hand: La mano del tirador.
        stake: La apuesta de la mesa (la de la partida si hay una en curso).
        bet: A qué apuesta la mesa.
        history: Últimas tiradas de la mano, como `(tirada, apuesta)`.
    """

    game: CrapsGame | None
    hand: Hand
    stake: int
    bet: Bet
    history: tuple[tuple[Roll, Bet], ...] = ()


def panel_state(table: Table, *, upto: int | None = None) -> dict[str, Any]:
    """El panel de la derecha. Con `upto`, como estaba antes de la tirada `upto`.

    Mientras ruedan los dados, el panel enseña lo de antes: el resultado no
    se adelanta.
    """
    game = table.game
    history = table.history if upto is None else table.history[: len(table.history) - 1]
    if game is not None and upto is not None:
        point = game.rolls[upto].point if upto < len(game.rolls) else game.point
        odds = game.odds
        status = "point" if point is not None else "comeout"
    elif game is not None:
        point = game.point if game.playing else None
        odds = game.odds
        status = {
            Status.COME_OUT: "comeout",
            Status.POINT: "point",
            Status.WON: "won",
            Status.LOST: "lost",
            Status.PUSH: "push",
        }[game.status]
    else:
        point, odds, status = None, 0, "idle"
    bet = game.bet if game is not None else table.bet
    stake = game.stake if game is not None else table.stake
    hand_rolls = table.hand.rolls - (1 if upto is not None else 0)
    hand_points = len(table.hand.points)
    if upto is not None and game is not None and game.rolls[upto].made:
        hand_points -= 1
    shown_point = point if status in ("point", "comeout") else (game.point if game else None)
    return {
        "bet": bet.label.upper(),
        "betKey": bet.key,
        "stake": _amount(stake),
        "odds": _amount(odds) if odds else None,
        "pays": format_odds(bet, shown_point) if shown_point in POINTS else None,
        "point": point,
        "status": status,
        "hand": f"{_plural(max(0, hand_rolls), 'tirada')} · {_plural(hand_points, 'punto')}",
        "history": [
            {"d": list(r.dice), "t": r.total, "k": roll_kind(r, b)} for r, b in history[-6:]
        ],
    }


def banner_for(game: CrapsGame, hand: Hand) -> dict[str, str] | None:
    """El cartel de la última tirada: qué salió y qué supone.

    `kind` decide el color: `win`, `lose`, `push`, `point` o `fire` (mano
    caliente). Las tiradas que no deciden nada no llevan cartel.
    """
    roll = game.last
    if roll is None:
        return None
    total = roll.total
    name = TOTAL_NAMES[total].upper()
    if game.status is Status.POINT and roll.come_out:
        return {
            "kind": "point",
            "title": f"PUNTO: {total}",
            "sub": f"Ahora hay que repetir el {total} antes que el 7",
        }
    if game.status is Status.PUSH:
        return {"kind": "push", "title": "¡BARRA!", "sub": "El 12 empata con No pase"}
    if game.status is Status.WON:
        if roll.made and len(hand.points) >= 3:
            title = "¡MANO CALIENTE!"
        elif roll.made:
            title = "¡PUNTO HECHO!"
        elif roll.seven_out:
            title = "¡SIETE FUERA!"
        elif roll.come_out and total in NATURALS:
            title = "¡NATURAL!"
        else:
            title = "¡PIFIA DEL TIRADOR!"
        return {
            "kind": "fire" if title == "¡MANO CALIENTE!" else "win",
            "title": title,
            "sub": f"{name} · +{_amount(game.net)}",
        }
    if game.status is Status.LOST:
        if roll.seven_out:
            title = "¡SIETE FUERA!"
        elif roll.made:
            title = "¡PUNTO HECHO!"
        elif total in NATURALS:
            title = "¡NATURAL!"
        else:
            title = "¡PIFIA!"
        return {"kind": "lose", "title": title, "sub": f"{name} · -{_amount(game.wagered)}"}
    return None


def _chips(game: CrapsGame | None, table: Table, *, settled: bool, t: float) -> list[dict]:
    """Las fichas del tapete. Con `settled`, mueve las que gana o pierde (`t` de 0 a 1)."""
    if game is None:
        return [chip_stack(table.stake, *_bet_spot(table.bet), alpha=0.55)]
    bx, by = _bet_spot(game.bet)
    ox = bx + 36
    chips = []
    stays = not settled or game.status in (Status.WON, Status.PUSH)
    if stays:
        chips.append(chip_stack(game.stake, bx, by))
        if game.odds:
            chips.append(chip_stack(game.odds, ox, by))
    if settled and game.status is Status.WON:
        k = _ease(t)
        prize = game.payout - game.wagered
        tx = ox + 36 if game.odds else ox
        px = BANK[0] + (tx - BANK[0]) * k
        py = BANK[1] + (by - BANK[1]) * k
        chips.append(chip_stack(prize, px, py, alpha=min(1.0, t * 3)))
    if settled and game.status is Status.LOST:
        k = _ease(t)
        for amount, x0 in ((game.stake, bx), (game.odds, ox)):
            if amount:
                x = x0 + (BANK[0] - x0) * k
                y = by + (BANK[1] - by) * k
                chips.append(chip_stack(amount, x, y, alpha=max(0.0, 1 - t * 1.2)))
    return chips


def board_state(table: Table, rest: tuple[DieRest, DieRest]) -> dict[str, Any]:
    """El fotograma quieto: dados donde quedaron, fichas, disco y cartel si lo hay."""
    game = table.game
    settled = game is not None and not game.playing
    banner = banner_for(game, table.hand) if game is not None else None
    point = game.point if game is not None and game.playing else None
    return {
        "dice": sorted((r.geometry() for r in rest), key=lambda d: -d["depth"]),
        "panel": panel_state(table),
        "puck": puck(point),
        "chips": _chips(game, table, settled=settled, t=1.0),
        "banner": banner,
        "bannerA": 1.0 if banner else 0.0,
        "total": _total_label(rest, game) if game is not None and game.rolls else None,
        "impact": None,
        "hint": game is None or not game.rolls or not game.playing,
        "glow": [],
    }


def _total_label(rest: tuple[DieRest, DieRest], game: CrapsGame | None, a: float = 1.0) -> dict:
    """El total grande encima de los dados."""
    # A la derecha del dado más a la derecha, a media altura: el cartel va arriba.
    right = max(rest, key=lambda r: r.x)
    sx, sy, _ = project(right.x + HALF * 3.2, right.y, HALF)
    total = rest[0].value + rest[1].value
    kind = "none"
    if game is not None and game.last is not None:
        kind = roll_kind(game.last, game.bet)
    return {"x": round(sx, 2), "y": round(sy, 2), "t": total, "k": kind, "a": round(a, 3)}


def throw_states(
    table: Table, *, seed: int
) -> tuple[list[dict[str, Any]], tuple[DieRest, DieRest]]:
    """Fotogramas de la última tirada de `table.game` (ya resuelta), revelado incluido.

    Returns:
        Los fotogramas y dónde quedan los dados (para los PNG siguientes).
    """
    game = table.game
    assert game is not None and game.last is not None
    roll = game.last
    index = len(game.rolls) - 1
    tracks, rest = simulate(roll.dice, seed)
    before_panel = panel_state(table, upto=index)
    before_puck = puck(roll.point)
    # Durante la tirada, las fichas están como antes: puestas y sin mover.
    flying_chips = _chips(game, table, settled=False, t=0.0)
    states = []
    frames = len(tracks[0].x)
    for f in range(frames):
        dice = [die_geometry(t.x[f], t.y[f], t.lift[f], t.spin[f]) for t in tracks]
        impact = None
        for t in tracks:
            if t.wall_hit is not None and 0 <= f - t.wall_hit < 5:
                ix, iy, _ = project(t.x[t.wall_hit], WALL_Y, HALF)
                impact = {
                    "x": round(ix, 2),
                    "y": round(iy, 2),
                    "a": round(1 - (f - t.wall_hit) / 5, 3),
                }
        states.append(
            {
                "dice": sorted(dice, key=lambda d: -d["depth"]),
                "panel": before_panel,
                "puck": before_puck,
                "chips": flying_chips,
                "banner": None,
                "bannerA": 0.0,
                "total": None,
                "impact": impact,
                "hint": False,
                "glow": [],
            }
        )
    final = board_state(table, rest)
    settled = not game.playing
    end_puck = final["puck"]
    for k in range(1, REVEAL_FRAMES + 1):
        t = k / REVEAL_FRAMES
        state = dict(final)
        state["bannerA"] = min(1.0, k / (REVEAL_FRAMES - 4)) if final["banner"] else 0.0
        state["total"] = _total_label(rest, game, a=min(1.0, k / 3))
        state["puck"] = _slide(before_puck, end_puck, min(1.0, t * 1.4))
        state["chips"] = _chips(game, table, settled=settled, t=t)
        state["glow"] = [_glow(r, 1 - k / 7) for r in rest] if k < 7 else []
        states.append(state)
    return states, rest


def _glow(rest: DieRest, a: float) -> dict[str, Any]:
    sx, sy, _ = project(rest.x, rest.y, HALF)
    return {"x": round(sx, 2), "y": round(sy, 2), "a": round(max(0.0, a), 3)}


def throw_seconds(states: list[dict[str, Any]]) -> float:
    """Lo que dura el GIF hasta el último fotograma."""
    return FRAME_MS * (len(states) - 1) / 1000


def cheer(hand: Hand) -> str | None:
    """Frase de la mano caliente (si la hay) para el texto del embed."""
    return milestone(len(hand.points))


# -- Dibujo con Pillow (sin navegador) ----------------------------------------------------------

S = 2  # se dibuja al doble y se reduce: bordes suaves
ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"
FELT = (22, 104, 64)
FELT_DARK = (10, 58, 35)
LINE = (236, 226, 190)
DIE_RED = (196, 32, 40)
DIE_EDGE = (110, 12, 18)
BANNER_COLORS = {
    "win": (60, 200, 110),
    "lose": (230, 70, 70),
    "push": (120, 150, 190),
    "point": (255, 196, 0),
    "fire": (255, 120, 30),
}
KIND_COLORS = {
    "win": (60, 200, 110),
    "lose": (230, 70, 70),
    "push": (120, 150, 190),
    "point": (255, 196, 0),
    "none": (150, 165, 158),
}
#: Dónde va cada punto de un dado, en la textura de 0 a 1.
PIPS: dict[int, tuple[tuple[float, float], ...]] = {
    1: ((0.5, 0.5),),
    2: ((0.27, 0.27), (0.73, 0.73)),
    3: ((0.27, 0.27), (0.5, 0.5), (0.73, 0.73)),
    4: ((0.27, 0.27), (0.73, 0.27), (0.27, 0.73), (0.73, 0.73)),
    5: ((0.27, 0.27), (0.73, 0.27), (0.5, 0.5), (0.27, 0.73), (0.73, 0.73)),
    6: ((0.27, 0.25), (0.73, 0.25), (0.27, 0.5), (0.73, 0.5), (0.27, 0.75), (0.73, 0.75)),
}


@cache
def _font(size: int, *, title: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TITLE_FONT if title else TEXT_FONT), size * S)


def _hex(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


@cache
def _felt_texture() -> Image.Image:
    """El tapete visto desde arriba, con sus casillas y bandas."""
    k = TEXTURE_SCALE
    width = int(2 * TABLE_HALF * k)
    height = int((WALL_Y - TABLE_NEAR) * k)
    img = Image.new("RGB", (width, height), FELT)
    d = ImageDraw.Draw(img)

    def tx(x: float) -> float:
        return (x + TABLE_HALF) * k

    def ty(y: float) -> float:
        return (WALL_Y - y) * k

    for p in POINTS:
        x0, x1 = tx(BOX_X[p] - 33), tx(BOX_X[p] + 33)
        d.rectangle((x0, ty(BOX_Y[1]), x1, ty(BOX_Y[0])), outline=LINE, width=2 * k)
        label = {6: "SEIS", 9: "NUEVE"}.get(p, str(p))
        d.text(((x0 + x1) / 2, ty(sum(BOX_Y) / 2)), label, font=_font(16, title=True), fill=LINE,
               anchor="mm")  # fmt: skip
    for band, label in ((PASS_Y, "PASE"), (DONT_Y, "NO PASE · BARRA 12")):
        d.rectangle((tx(-215), ty(band[1]), tx(215), ty(band[0])), outline=LINE, width=2 * k)
        d.text((tx(40), ty(sum(band) / 2)), label, font=_font(13, title=True), fill=LINE,
               anchor="mm")  # fmt: skip
    d.text((tx(0), ty(80)), "CASINO DEL ESTADO", font=_font(20, title=True),
           fill=(36, 128, 82), anchor="mm")  # fmt: skip
    return img


@cache
def _background() -> Image.Image:
    """Fondo, pared y tapete en perspectiva, al doble de tamaño."""
    img = Image.new("RGB", (W * S, H * S), (14, 10, 8))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W * S, int(project(0, WALL_Y, WALL_HEIGHT)[1] * S)), fill=(40, 22, 14))
    tex = _felt_texture()
    k = TEXTURE_SCALE
    for sy, y0, y1, left, right in floor_rows(S):
        t0 = int((WALL_Y - y1) * k)
        t1 = max(t0 + 1, int((WALL_Y - y0) * k))
        width = int((right - left) * S)
        if width <= 0:
            continue
        row = tex.crop((0, t0, tex.width, t1)).resize((width, 1), Image.Resampling.BILINEAR)
        img.paste(row, (int(left * S), int(sy * S)))
    for sy, z0, z1, left, right in wall_rows(S):
        shade = 70 + int(60 * (z0 / WALL_HEIGHT))
        stripe = (shade, 24, 26) if int(z1 / 10) % 2 else (shade - 20, 18, 20)
        d.line((int(left * S), int(sy * S), int(right * S), int(sy * S)), fill=stripe)
    d.text((22 * S, 10 * S), "DADOS", font=_font(26, title=True), fill=(255, 214, 90))
    d.rounded_rectangle(
        (PANEL_X * S, 10 * S, (PANEL_X + PANEL_W) * S, 350 * S),
        radius=14 * S,
        fill=(8, 22, 15),
        outline=(80, 70, 40),
    )
    return img


def _paint_die(img: Image.Image, die: dict[str, Any]) -> None:
    d = ImageDraw.Draw(img)
    hull = [(x * S, y * S) for x, y in die["hull"]]
    if len(hull) >= 3:
        d.polygon(hull, fill=DIE_EDGE)
    for face in die["faces"]:
        quad = [(x * S, y * S) for x, y in face["q"]]
        light = face["l"]
        color = tuple(int(c * light) for c in DIE_RED)
        d.polygon(quad, fill=color, outline=DIE_EDGE)
        (x0, y0), (x1, y1), _, (x3, y3) = quad
        ux, uy = x1 - x0, y1 - y0
        vx, vy = x3 - x0, y3 - y0
        pip = tuple(int(255 * min(1.0, 0.45 + light * 0.6)) for _ in range(3))
        for px, py in PIPS[face["v"]]:
            points = []
            for a in range(10):
                ang = 2 * math.pi * a / 10
                u = px + 0.1 * math.cos(ang)
                v = py + 0.1 * math.sin(ang)
                points.append((x0 + ux * u + vx * v, y0 + uy * u + vy * v))
            d.polygon(points, fill=pip)


def _paint_shadow(img: Image.Image, shadow: dict[str, Any]) -> None:
    blur = 4 * S
    x, y, rx, ry = (shadow[k] * S for k in ("x", "y", "rx", "ry"))
    left, top = int(x - rx) - 3 * blur, int(y - ry) - 3 * blur
    mask = Image.new("L", (int(2 * rx) + 6 * blur, int(2 * ry) + 6 * blur), 0)
    cx, cy = x - left, y - top
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=int(255 * shadow["a"]))
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    img.paste((0, 0, 0, 255), (left, top, left + mask.width, top + mask.height), mask=mask)


def _paint_chip(d: ImageDraw.ImageDraw, chip: dict[str, Any]) -> None:
    if chip["a"] <= 0.05:
        return
    x, y, r = chip["x"] * S, chip["y"] * S, chip["r"] * S
    ry = r * 0.45
    color = _hex(chip["c"])
    for i in range(chip["n"]):
        cy = y - i * 4 * S
        d.ellipse((x - r, cy - ry, x + r, cy + ry), fill=color, outline=(20, 20, 20))
    top = y - (chip["n"] - 1) * 4 * S
    ink = (30, 30, 30) if chip["c"] == "#e8e4da" else (255, 255, 255)
    d.text((x, top), chip["t"], font=_font(9), fill=ink, anchor="mm")


def _paint_puck(d: ImageDraw.ImageDraw, p: dict[str, Any]) -> None:
    x, y, r = p["x"] * S, p["y"] * S, p["r"] * S
    ry = r * 0.5
    fill = (245, 245, 245) if p["on"] else (25, 25, 25)
    ink = (20, 20, 20) if p["on"] else (240, 240, 240)
    d.ellipse((x - r, y - ry, x + r, y + ry), fill=fill, outline=(120, 120, 120), width=S)
    d.text((x, y), "ON" if p["on"] else "OFF", font=_font(8), fill=ink, anchor="mm")


def _paint_panel(d: ImageDraw.ImageDraw, panel: dict[str, Any]) -> None:
    x0 = (PANEL_X + 14) * S
    x1 = (PANEL_X + PANEL_W - 14) * S
    d.text((x0, 26 * S), panel["bet"], font=_font(16, title=True), fill=(255, 225, 140))
    d.text((x1, 30 * S), panel["stake"], font=_font(11), fill=(240, 240, 230), anchor="ra")
    if panel["odds"]:
        d.text((x0, 52 * S), "ODDS", font=_font(11), fill=(200, 220, 210))
        d.text((x1, 52 * S), panel["odds"], font=_font(11), fill=(240, 240, 230), anchor="ra")
    point = panel["point"]
    d.text((x0, 80 * S), "PUNTO", font=_font(11), fill=(200, 220, 210))
    d.text((x1, 74 * S), str(point) if point else "—", font=_font(30, title=True),
           fill=(255, 214, 90), anchor="ra")  # fmt: skip
    if panel["pays"]:
        d.text((x0, 104 * S), f"Odds pagan {panel['pays']}", font=_font(10), fill=(200, 220, 210))
    d.text((x0, 128 * S), panel["hand"].upper(), font=_font(9), fill=(200, 220, 210))
    for i, roll in enumerate(reversed(panel["history"])):
        y = (162 + i * 30) * S
        for j, value in enumerate(roll["d"]):
            bx = x0 + j * 24 * S
            d.rounded_rectangle((bx, y - 10 * S, bx + 20 * S, y + 10 * S), radius=4 * S,
                                fill=DIE_RED)  # fmt: skip
            for px, py in PIPS[value]:
                cx, cy = bx + px * 20 * S, y - 10 * S + py * 20 * S
                d.ellipse((cx - 2 * S, cy - 2 * S, cx + 2 * S, cy + 2 * S), fill=(255, 255, 255))
        color = KIND_COLORS[roll["k"]]
        d.text((x1, y), str(roll["t"]), font=_font(18, title=True), fill=color, anchor="rm")


def _paint_banner(img: Image.Image, banner: dict[str, str], alpha: float) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    color = BANNER_COLORS[banner["kind"]]
    cx, cy = 225 * S, 72 * S
    d.rounded_rectangle((cx - 180 * S, cy - 34 * S, cx + 180 * S, cy + 38 * S), radius=16 * S,
                        fill=(10, 14, 12, 215), outline=color, width=3 * S)  # fmt: skip
    d.text((cx, cy - 8 * S), banner["title"], font=_font(28, title=True), fill=color, anchor="mm")
    d.text((cx, cy + 22 * S), banner["sub"], font=_font(12), fill=(235, 238, 240), anchor="mm")
    if alpha < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    img.alpha_composite(layer)


def _paint_total(d: ImageDraw.ImageDraw, total: dict[str, Any]) -> None:
    color = KIND_COLORS[total["k"]]
    x, y = total["x"] * S, total["y"] * S
    d.text((x, y), str(total["t"]), font=_font(34, title=True), fill=color, anchor="mm",
           stroke_width=3 * S, stroke_fill=(10, 10, 10))  # fmt: skip


def paint(state: dict[str, Any]) -> Image.Image:
    """Un fotograma con Pillow, a 640×360."""
    img = _background().convert("RGBA")
    d = ImageDraw.Draw(img)
    _paint_puck(d, state["puck"])
    for chip in state["chips"]:
        _paint_chip(d, chip)
    if state["hint"] and not state["banner"]:
        d.text((225 * S, 72 * S), "¿PASE O NO PASE?", font=_font(28, title=True),
               fill=(255, 240, 200), anchor="mm", stroke_width=3 * S,
               stroke_fill=(6, 24, 15))  # fmt: skip
    for die in state["dice"]:
        _paint_shadow(img, die["shadow"])
    for die in state["dice"]:
        _paint_die(img, die)
    d = ImageDraw.Draw(img)
    _paint_panel(d, state["panel"])
    if state["total"] and state["total"]["a"] > 0.3:
        _paint_total(d, state["total"])
    if state["banner"] and state["bannerA"] > 0:
        _paint_banner(img, state["banner"], state["bannerA"])
    return img.convert("RGB").reduce(S)


@dataclass(frozen=True, slots=True)
class Media:
    """Lo que el cog sube: el GIF de la tirada, el PNG final, cuánto dura y dónde quedan."""

    gif: bytes
    png: bytes
    seconds: float
    rest: tuple[DieRest, DieRest]


def encode(frames: list[Image.Image], rest: tuple[DieRest, DieRest]) -> Media:
    """GIF (con el último fotograma quieto) y PNG del último fotograma."""
    durations = [FRAME_MS] * (len(frames) - 1) + [FINAL_FRAME_MS]
    gif = local_palette_gif(frames, durations)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", optimize=True)
    return Media(
        gif=gif, png=png.getvalue(), seconds=FRAME_MS * (len(frames) - 1) / 1000, rest=rest
    )


class CrapsRenderer:
    """Dibujo de reserva con Pillow: el mismo contenido que la escena, más sencillo."""

    def board(self, table: Table, rest: tuple[DieRest, DieRest]) -> bytes:
        """PNG de la mesa quieta."""
        img = paint(board_state(table, rest))
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()

    def throw(self, table: Table, *, seed: int) -> Media:
        """GIF de la última tirada de `table.game` y PNG del final."""
        states, rest = throw_states(table, seed=seed)
        return encode([paint(s) for s in states], rest)
