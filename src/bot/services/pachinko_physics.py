"""Física del pachinko: la geometría del campo de clavos y la caída de las bolas.

Lógica pura, sin Pillow ni Discord. Hace dos cosas:

1. `geometry_for(board)` coloca los clavos, las paredes y los separadores de
   los bolsillos en las coordenadas de la imagen (340×500). El dibujo
   (`bot.services.pachinko_render`) pinta exactamente esos clavos, así que lo
   que se ve es lo que la bola golpea.
2. `simulate` deja caer una bola con gravedad y rebotes (bola contra clavo y
   bola contra pared) y devuelve su `Trajectory`: dónde está en cada
   fotograma del GIF, en qué bolsillo acaba y cuántos clavos ha golpeado.

**La lógica decide y la física obedece.** El bolsillo de cada bola lo sortea
`bot.services.pachinko` (cara o cruz por fila), así que el retorno de la
máquina sigue demostrado con fracciones exactas. La física no lo cambia: de
antemano se simulan muchas bolas (`bake_library`) y se guarda una biblioteca
de caídas reales por tablero y bolsillo (`assets/pachinko/trayectorias.json`).
En cada tanda, cada bola usa una de la biblioteca que acaba en su bolsillo. Ninguna
trayectoria está trucada: todas son una simulación completa desde su
condición inicial, sin empujones.

Reproducibilidad: una caída queda fijada por `Start` (`x0`, `vx0`) y el bucle
solo usa `+ - * /` y `math.sqrt`, que dan el mismo resultado en cualquier
máquina (IEEE 754 exacto). Sin azar ni numpy dentro del bucle. Por eso una
prueba puede volver a simular las condiciones guardadas y comprobar que salen
los mismos puntos. **Si se cambia la geometría o una constante física hay que
regenerar el archivo** con `python docs/pachinko_trayectorias.py`; la prueba
`test_la_biblioteca_guardada_se_puede_reproducir` falla si se olvida.

Este módulo también es el dueño de las constantes de geometría de la imagen
(`WIDTH`, `PIN_TOP`, `LCD_BOX`…), para que dibujo y física nunca discrepen.
"""

from __future__ import annotations

import functools
import json
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.services.pachinko import Board

# -- Geometría de la imagen ---------------------------------------------------------

WIDTH = 340
HEIGHT = 500
CX = WIDTH // 2
#: Altura de la primera fila de clavos.
PIN_TOP = 190
#: Espacio vertical para las filas de clavos y ancho para los bolsillos.
PIN_SPACE = 192
POCKET_SPACE = 304
#: Separación máxima entre bolsillos (en horizontal) y entre filas (en vertical).
MAX_DX = 28
MAX_DY = 24
PIN_R = 2
BALL_R = 5
#: Altura a la que aparece cada bola, justo debajo de la reserva de la pantalla.
ENTRY_Y = 170
POCKET_H = 36
LCD_BOX = (70, 64, 270, 148)
HOLD_Y = 162
DECORATIONS = ((44, 232), (296, 232))
#: Los clavos no se acercan a un adorno más de esto (centro a centro).
DECORATION_CLEARANCE = 26

#: Milisegundos entre dos fotogramas del GIF; cada punto de `Trajectory.points`
#: es la bola en uno de ellos.
FRAME_MS = 50

# -- Física -------------------------------------------------------------------------

#: Pasos de simulación por segundo (480 = 24 pasos por fotograma de 50 ms).
SIM_HZ = 480
STEPS_PER_FRAME = FRAME_MS * SIM_HZ // 1000
DT = 1.0 / SIM_HZ
#: Gravedad en píxeles por segundo al cuadrado (la imagen mide 500 px).
GRAVITY = 2000.0
#: Parte de la velocidad contra el obstáculo que se conserva al chocar.
PIN_RESTITUTION = 0.6
WALL_RESTITUTION = 0.5
#: Parte de la velocidad a lo largo del obstáculo que se pierde en cada choque.
FRICTION = 0.04
#: Grosor de las paredes y separadores (radio de la cápsula que choca con la bola).
SEGMENT_R = 1.0
#: Un golpe cuenta como rebote si la bola llega al clavo a más de esta velocidad
#: (px/s); así rozar o rodar por un clavo no suma.
BOUNCE_MIN_SPEED = 90.0
#: Cuánto baja el centro de la bola por debajo del borde de los bolsillos para
#: darla por dentro (los separadores ya la han encerrado en su bolsillo).
POCKET_DEPTH = 8.0
#: Duración admitida de una caída, en segundos.
MIN_SECONDS = 1.0
MAX_SECONDS = 2.0
#: Si en este tiempo (s) la bola se mueve menos de `STUCK_DISTANCE` px, está atascada.
STUCK_SECONDS = 0.25
STUCK_DISTANCE = 4.0
#: Altura del tope de los separadores sobre el borde de los bolsillos.
DIVIDER_RISE = 10.0
#: Hasta dónde bajan los separadores y las paredes bajo el borde de los bolsillos.
DIVIDER_DROP = 24.0
#: Filas de clavos como mucho menos una: con más, la bola tarda demasiado en bajar.
MAX_PIN_ROWS = 10
#: Los clavos de la fila de abajo quedan a esta distancia sobre los bolsillos.
LAST_ROW_GAP = 22.0
#: Altura de las paredes laterales: arrancan justo debajo de la pantalla.
WALL_TOP = ENTRY_Y - 14
#: Franja de entrada: la bola aparece lejos de las paredes, pero cubre casi todo el ancho.
ENTRY_MARGIN = BALL_R + 4
#: Velocidad horizontal inicial máxima de la bola (px/s).
ENTRY_SPEED = 40
#: Tamaño de las celdas de la rejilla espacial (px).
CELL = 16

#: Caídas guardadas por bolsillo y tablero.
TRAJECTORIES_PER_POCKET = 24
#: Intentos máximos de `bake_library` por tablero antes de rendirse.
BAKE_BUDGET = 200_000

DATA_PATH = Path(__file__).resolve().parent.parent / "assets" / "pachinko" / "trayectorias.json"


# -- Geometría por tablero ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Segment:
    """Un segmento (pared o separador) de `(x0, y0)` a `(x1, y1)`."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True, slots=True)
class Geometry:
    """El campo de clavos de un tablero, en coordenadas de la imagen.

    Attributes:
        rows: Filas de `Board` (los bolsillos son `rows + 1`).
        dx: Ancho de cada bolsillo.
        dy: Distancia entre filas del tablero (fija la altura de los bolsillos).
        pocket_top: Borde de arriba de los bolsillos.
        pins: Centro de cada clavo (todos de radio `PIN_R`), en una rejilla al
            tresbolillo: las filas pares caen sobre los separadores y las
            impares sobre los centros de los bolsillos.
        walls: Paredes laterales que encierran el campo.
        dividers: Separadores cortos entre bolsillos.
        grid: Rejilla espacial que usa el simulador (no cuenta para `==`).
    """

    rows: int
    dx: float
    dy: float
    pocket_top: float
    pins: tuple[tuple[float, float], ...]
    walls: tuple[Segment, ...]
    dividers: tuple[Segment, ...]
    grid: dict = field(compare=False, hash=False, repr=False, default_factory=dict)

    @property
    def left(self) -> float:
        """Borde izquierdo del campo (donde está la pared)."""
        return CX - (self.rows + 1) * self.dx / 2

    @property
    def right(self) -> float:
        """Borde derecho del campo."""
        return CX + (self.rows + 1) * self.dx / 2

    def pocket_x(self, pocket: int) -> float:
        """Centro del bolsillo `pocket`."""
        return CX + (pocket - self.rows / 2) * self.dx

    def pocket_at(self, x: float) -> int:
        """Bolsillo que hay bajo la horizontal `x` (limitado a los de los extremos)."""
        return min(self.rows, max(0, int((x - self.left) // self.dx)))

    @property
    def end_y(self) -> float:
        """Altura a la que una bola se da por metida en su bolsillo."""
        return self.pocket_top + POCKET_DEPTH


def pocket_pitch(rows: int) -> float:
    """Ancho de cada bolsillo (y paso de clavos) para un tablero de `rows` filas."""
    return min(MAX_DX, POCKET_SPACE // (rows + 1))


def row_pitch(rows: int) -> float:
    """Distancia entre filas del tablero; con ella se calcula dónde empiezan los bolsillos."""
    return min(MAX_DY, PIN_SPACE // rows)


def _cells(x0: float, y0: float, x1: float, y1: float, margin: float) -> Iterator[tuple[int, int]]:
    """Celdas de la rejilla que toca la caja `(x0, y0)-(x1, y1)` ensanchada `margin`."""
    for cx in range(int((min(x0, x1) - margin) // CELL), int((max(x0, x1) + margin) // CELL) + 1):
        for cy in range(
            int((min(y0, y1) - margin) // CELL), int((max(y0, y1) + margin) // CELL) + 1
        ):
            yield cx, cy


def _build_grid(
    pins: tuple[tuple[float, float], ...], segments: tuple[Segment, ...]
) -> dict[tuple[int, int], tuple[list[tuple[float, float]], list[Segment]]]:
    """Rejilla espacial: cada celda guarda solo los obstáculos que la bola puede tocar en ella.

    Se ensancha cada obstáculo con el alcance de la bola, así basta mirar la
    celda del centro de la bola en cada paso, en vez de todos los clavos.
    """
    grid: dict[tuple[int, int], tuple[list[tuple[float, float]], list[Segment]]] = {}
    for x, y in pins:
        for cell in _cells(x, y, x, y, BALL_R + PIN_R + 1):
            grid.setdefault(cell, ([], []))[0].append((x, y))
    for seg in segments:
        for cell in _cells(seg.x0, seg.y0, seg.x1, seg.y1, BALL_R + SEGMENT_R + 1):
            grid.setdefault(cell, ([], []))[1].append(seg)
    return grid


@functools.cache
def geometry_for(board: Board) -> Geometry:
    """El campo de `board`.

    Los clavos forman una rejilla al tresbolillo de `rows + 1` filas que cubre
    todo el ancho de los bolsillos. La separación entre clavos es la de los
    bolsillos, así que las filas pares (la última también) caen justo sobre los
    separadores y la bola entra limpia en un bolsillo. En las filas impares no
    se pone el clavo pegado a la pared: entre él y la pared no cabría la bola y
    se quedaría encajada. Tampoco hay clavos cerca de los adornos de los lados.
    """
    rows = board.rows
    dx = pocket_pitch(rows)
    dy = row_pitch(rows)
    pocket_top = PIN_TOP + dy * rows + 4
    left = CX - (rows + 1) * dx / 2
    right = CX + (rows + 1) * dx / 2
    last_row = min(rows, MAX_PIN_ROWS)
    step = (pocket_top - LAST_ROW_GAP - PIN_TOP) / last_row
    pins: list[tuple[float, float]] = []
    for row in range(last_row + 1):
        y = PIN_TOP + row * step
        if row % 2 == 0:
            xs = [left + j * dx for j in range(1, rows + 1)]
        else:
            xs = [left + (j + 0.5) * dx for j in range(1, rows)]
        for x in xs:
            near = any(
                (x - mx) * (x - mx) + (y - my) * (y - my)
                < DECORATION_CLEARANCE * DECORATION_CLEARANCE
                for mx, my in DECORATIONS
            )
            if not near:
                pins.append((x, y))
    floor = pocket_top + DIVIDER_DROP
    walls = (
        Segment(left, WALL_TOP, left, floor),
        Segment(right, WALL_TOP, right, floor),
    )
    dividers = tuple(
        Segment(left + j * dx, pocket_top - DIVIDER_RISE, left + j * dx, floor)
        for j in range(1, rows + 1)
    )
    grid = _build_grid(tuple(pins), walls + dividers)
    return Geometry(rows, dx, dy, pocket_top, tuple(pins), walls, dividers, grid)


# -- Simulador ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Start:
    """Condición inicial de una caída: horizontal y velocidad horizontal de la entrada.

    Attributes:
        x0: Horizontal de la bola al aparecer (a `ENTRY_Y`), con décimas de píxel.
        vx0: Velocidad horizontal inicial en px/s (entera).
    """

    x0: float
    vx0: float


@dataclass(frozen=True, slots=True)
class Trajectory:
    """Una caída simulada: de la entrada al bolsillo.

    Attributes:
        pocket: Bolsillo en el que acaba.
        start: Condición inicial; volver a simular `start` da los mismos `points`.
        points: Posición `(x, y)` de la bola en cada fotograma del GIF (cada
            `FRAME_MS`), redondeada a 0,1 px. El último es la bola ya dentro del
            bolsillo.
        bounces: Golpes contra clavos a más de `BOUNCE_MIN_SPEED`.
    """

    pocket: int
    start: Start
    points: tuple[tuple[float, float], ...]
    bounces: int

    @property
    def frames(self) -> int:
        """Fotogramas que la bola está a la vista."""
        return len(self.points)

    @property
    def seconds(self) -> float:
        """Lo que dura la caída."""
        return (len(self.points) - 1) * FRAME_MS / 1000


def simulate(geometry: Geometry, start: Start) -> Trajectory | None:
    """Deja caer una bola desde `start` y devuelve su caída, o `None` si no vale.

    Euler semiimplícito a `SIM_HZ` pasos por segundo: primero la velocidad y
    después la posición. En cada paso se resuelven los choques de la bola con
    los clavos (círculo contra círculo) y con las paredes y separadores
    (círculo contra cápsula): se saca la bola del obstáculo y se refleja la
    velocidad con el coeficiente de restitución, perdiendo algo a lo largo de
    la superficie (rozamiento).

    Es determinista: no usa azar y solo `+ - * /` y `sqrt`.

    Returns:
        La caída, o `None` si la bola se atasca, se sale del campo o tarda
        menos de `MIN_SECONDS` o más de `MAX_SECONDS` en entrar en un bolsillo.
    """
    grid = geometry.grid
    left, right = geometry.left, geometry.right
    end_y = geometry.end_y
    x, y = start.x0, float(ENTRY_Y)
    vx, vy = start.vx0, 0.0
    pin_reach = BALL_R + PIN_R
    pin_reach2 = pin_reach * pin_reach
    seg_reach = BALL_R + SEGMENT_R
    seg_reach2 = seg_reach * seg_reach
    gravity_step = GRAVITY * DT
    max_steps = int(MAX_SECONDS * SIM_HZ) + STEPS_PER_FRAME
    stuck_steps = int(STUCK_SECONDS * SIM_HZ)
    anchor_x, anchor_y = x, y
    bounces = 0
    points = [(round(x * 10) / 10, round(y * 10) / 10)]
    for step in range(1, max_steps + 1):
        vy += gravity_step
        x += vx * DT
        y += vy * DT
        cell = grid.get((int(x // CELL), int(y // CELL)))
        if cell is not None:
            for px, py in cell[0]:
                ex = x - px
                ey = y - py
                d2 = ex * ex + ey * ey
                if d2 >= pin_reach2:
                    continue
                d = sqrt(d2)
                if d == 0.0:
                    nx, ny, d = 0.0, -1.0, 0.0
                else:
                    nx, ny = ex / d, ey / d
                push = pin_reach - d
                x += nx * push
                y += ny * push
                vn = vx * nx + vy * ny
                if vn < 0.0:
                    if -vn > BOUNCE_MIN_SPEED:
                        bounces += 1
                    # Se invierte lo que entraba al clavo (con pérdida) y se
                    # frena un poco lo que resbala por su superficie.
                    tx, ty = vx - vn * nx, vy - vn * ny
                    keep = 1.0 - FRICTION
                    vx = tx * keep - vn * PIN_RESTITUTION * nx
                    vy = ty * keep - vn * PIN_RESTITUTION * ny
            for seg in cell[1]:
                sx = seg.x1 - seg.x0
                sy = seg.y1 - seg.y0
                t = ((x - seg.x0) * sx + (y - seg.y0) * sy) / (sx * sx + sy * sy)
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                ex = x - (seg.x0 + t * sx)
                ey = y - (seg.y0 + t * sy)
                d2 = ex * ex + ey * ey
                if d2 >= seg_reach2:
                    continue
                d = sqrt(d2)
                if d == 0.0:
                    nx, ny, d = 0.0, -1.0, 0.0
                else:
                    nx, ny = ex / d, ey / d
                push = seg_reach - d
                x += nx * push
                y += ny * push
                vn = vx * nx + vy * ny
                if vn < 0.0:
                    tx, ty = vx - vn * nx, vy - vn * ny
                    keep = 1.0 - FRICTION
                    vx = tx * keep - vn * WALL_RESTITUTION * nx
                    vy = ty * keep - vn * WALL_RESTITUTION * ny
        if step % STEPS_PER_FRAME == 0:
            points.append((round(x * 10) / 10, round(y * 10) / 10))
        if y >= end_y:
            seconds = step * DT
            if not MIN_SECONDS <= seconds <= MAX_SECONDS or not left < x < right:
                return None
            if step % STEPS_PER_FRAME:
                # La caída acaba entre dos fotogramas: el último punto es la
                # bola ya dentro del bolsillo.
                points.append((round(x * 10) / 10, round(y * 10) / 10))
            return Trajectory(geometry.pocket_at(x), start, tuple(points), bounces)
        if step % stuck_steps == 0:
            moved = (x - anchor_x) * (x - anchor_x) + (y - anchor_y) * (y - anchor_y)
            if moved < STUCK_DISTANCE * STUCK_DISTANCE:
                return None
            anchor_x, anchor_y = x, y
    return None


# -- Biblioteca ---------------------------------------------------------------------


def random_start(geometry: Geometry, rng: random.Random) -> Start:
    """Condición inicial al azar: `x0` en casi todo el ancho y `vx0` pequeña."""
    low = geometry.left + ENTRY_MARGIN
    high = geometry.right - ENTRY_MARGIN
    return Start(
        round(rng.uniform(low, high) * 10) / 10, float(rng.randint(-ENTRY_SPEED, ENTRY_SPEED))
    )


def bake_library(board: Board, rng: random.Random) -> dict[int, list[Trajectory]]:
    """Simula caídas al azar hasta tener `TRAJECTORIES_PER_POCKET` por bolsillo.

    Args:
        board: Tablero.
        rng: Generador de las condiciones iniciales (con una semilla fija sale
            siempre la misma biblioteca).

    Raises:
        RuntimeError: Si tras `BAKE_BUDGET` intentos algún bolsillo no llega a
            la cifra (señal de que la geometría no deja llegar a él).
    """
    geometry = geometry_for(board)
    found: dict[int, list[Trajectory]] = {pocket: [] for pocket in range(board.rows + 1)}
    seen: set[Start] = set()
    for _ in range(BAKE_BUDGET):
        start = random_start(geometry, rng)
        if start in seen:
            continue
        seen.add(start)
        trajectory = simulate(geometry, start)
        if trajectory is None:
            continue
        bucket = found[trajectory.pocket]
        if len(bucket) < TRAJECTORIES_PER_POCKET:
            bucket.append(trajectory)
            if all(len(b) >= TRAJECTORIES_PER_POCKET for b in found.values()):
                return found
    missing = {p: len(b) for p, b in found.items() if len(b) < TRAJECTORIES_PER_POCKET}
    raise RuntimeError(
        f"El tablero {board.key} no llega a {TRAJECTORIES_PER_POCKET} caídas "
        f"por bolsillo tras {BAKE_BUDGET} intentos; faltan: {missing}"
    )


def encode_library(libraries: dict[str, dict[int, list[Trajectory]]]) -> str:
    """JSON compacto de las bibliotecas de varios tableros (`trayectorias.json`).

    Una caída es una lista de enteros: `[x0 en décimas, vx0, rebotes, x, y, x, y…]`
    con las posiciones en décimas de píxel; los bolsillos van en orden.
    """
    data = {
        key: [
            [
                [round(t.start.x0 * 10), round(t.start.vx0), t.bounces]
                + [round(c * 10) for point in t.points for c in point]
                for t in library[pocket]
            ]
            for pocket in sorted(library)
        ]
        for key, library in libraries.items()
    }
    return json.dumps(data, separators=(",", ":"))


def decode_library(rows: list[list[list[int]]]) -> dict[int, list[Trajectory]]:
    """Inversa de `encode_library` para un tablero."""
    library: dict[int, list[Trajectory]] = {}
    for pocket, entries in enumerate(rows):
        trajectories = []
        for entry in entries:
            coords = entry[3:]
            points = tuple((coords[i] / 10, coords[i + 1] / 10) for i in range(0, len(coords), 2))
            trajectories.append(
                Trajectory(pocket, Start(entry[0] / 10, float(entry[1])), points, entry[2])
            )
        library[pocket] = trajectories
    return library


@functools.cache
def _load_all() -> dict[str, dict[int, list[Trajectory]]]:
    """Lee `trayectorias.json` entero (una vez)."""
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return {key: decode_library(rows) for key, rows in raw.items()}


def library(board: Board) -> dict[int, list[Trajectory]]:
    """Las caídas guardadas de `board`: `TRAJECTORIES_PER_POCKET` por bolsillo.

    Raises:
        RuntimeError: Si el archivo no tiene el tablero (hay que regenerarlo con
            `docs/pachinko_trayectorias.py`).
    """
    libraries = _load_all()
    if board.key not in libraries:
        raise RuntimeError(
            f"Faltan las trayectorias del tablero {board.key}: "
            "ejecuta docs/pachinko_trayectorias.py"
        )
    return libraries[board.key]


def trajectory_of(board: Board, pocket: int, index: int) -> Trajectory:
    """La caída número `index` hacia el bolsillo `pocket` de `board`."""
    return library(board)[pocket][index]
