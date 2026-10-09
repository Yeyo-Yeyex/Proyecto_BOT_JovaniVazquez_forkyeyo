"""Animación del pachinko: un GIF por tanda y una imagen fija final.

La máquina imita las de los salones japoneses: un mueble con bombillas que
persiguen, un rótulo de neón, una pantalla con el sorteo, un campo de clavos
con dos adornos que se mueven y los bolsillos abajo.

**Las piezas se pintan una vez, fuera del bot.** El mueble con su rótulo, el
campo de clavos y los bolsillos (fondo), los bolsillos iluminados, la bola
metálica, los cuatro pasos de cada adorno, las bombillas con halo, las
pantallas de cada modo, los puntos de la reserva y la chapa del contador salen
de `assets/pachinko/escena.html` (canvas, con degradados y brillos) en un
Chromium, con `docs/pachinko_piezas.py`, y se guardan como PNG en
`assets/pachinko/<tablero>/`. El bot no abre ningún navegador: carga esos PNG
(`bot.services.pachinko_pieces`) y monta cada fotograma pegándolos, y encima
dibuja con Pillow lo que cambia (números, contador, carteles). Si falta alguno
o no se puede abrir, esa pieza se dibuja con Pillow como antes y se avisa una
vez en el log. `piece_spec` es lo único que alimenta la escena (geometría de
la física, colores de `THEMES`) y `piece_problems` dice si los PNG siguen al
día.

La paleta del GIF (`PALETTE_COLORS`) tiene colores exactos para lo pequeño y
saturado (bolsillos, números, carteles, bombillas) y reparte el resto entre el
fondo y lo que se mueve; con degradados, una paleta repartida por superficie
lavaba los bolsillos. Los degradados grandes son del fondo, que no cambia, y
lo que se mueve (bombillas, adornos, pantallas) lleva el borde limpio y pocos
tonos para que el GIF se comprima. Una tanda cuesta ~0,2-0,25 s de CPU (fuera
del event loop) y unos 300-350 KB de GIF, que escribe
`bot.utils.gif.shared_palette_gif`. Medido con 40 tandas por tablero: mediana
de 0,20 a 0,26 s (el 10 % más largo, hasta ~0,37 s) y de 300 a 350 KB (hasta
~570 KB); con el dibujo solo con Pillow eran 0,21-0,25 s y 215-240 KB. El modo
turbo solo manda la imagen final. El movimiento de las bolas (`motion_for`, con
sus choques) se calcula aparte y cuesta ~75-83 ms más (máximo ~200 ms); no
cambia el GIF: con 40 tandas por tablero, antes y después, 0,21-0,24 s de
mediana y 300-340 KB.

Cada tablero de `bot.services.pachinko.BOARDS` tiene su tema (`THEMES`):
colores, rótulo y adorno (molinillos, flores de cerezo, perlas de dragón o
llamas). El campo de clavos (`Layout`) es el de `bot.services.pachinko_physics`:
lo que se dibuja es exactamente lo que golpean las bolas.

La animación sigue la línea de tiempo de una máquina real:

1. Las bolas se lanzan de una en una y caen con física real (gravedad,
   rebotes en los clavos y las paredes y choques de unas con otras). El
   movimiento de la tanda entera lo busca `bot.services.pachinko_motion`
   (`motion_for`) apoyándose en las caídas simuladas de antemano
   (`pachinko_physics.library`): las diez bolas, chocando de verdad, acaban
   cada una en el bolsillo que ha sorteado `bot.services.pachinko`; la física
   no decide nada, solo cómo se ve. Cada caída dura lo suyo, así que las
   bolas no llegan en el orden en que salen.
2. Cuando una cae en START, la reserva (los puntos bajo la pantalla) gana una
   tirada, y la pantalla la juega en cuanto queda libre, mientras siguen
   cayendo bolas. Las tiradas se asignan por orden de llegada.
3. En un reach, el número del centro tarda más y aparece el cartel. En un
   atari, la pantalla se pone dorada, las bombillas se vuelven locas, llueven
   bolas y el contador del rush va subiendo.

El último fotograma se queda quieto mucho tiempo y es idéntico al PNG final:
el bot cambia el GIF por el PNG al acabar, como en la tragaperras.

Los bolsillos se distinguen por la forma (estrella, rombo, círculo, aspa y
tulipán) y por el texto, no solo por el color: se leen también con
deuteranopia.
"""

from __future__ import annotations

import io
import json
import math
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from bot.services.pachinko import (
    BALLS,
    BOARDS,
    MAX_HOLD,
    Board,
    Draw,
    Kind,
    Volley,
)
from bot.services.pachinko_motion import VolleyMotion, motion_for
from bot.services.pachinko_physics import (
    BALL_R,
    CX,
    DECORATIONS,
    FRAME_MS,
    HEIGHT,
    HOLD_Y,
    LCD_BOX,
    PIN_R,
    POCKET_H,
    WIDTH,
    Geometry,
    geometry_for,
    library,
)
from bot.services.pachinko_pieces import (
    BACKGROUND_FILE,
    BADGE_CELL,
    BADGE_FILE,
    BALL_CELL,
    BALL_FILE,
    BULB_CELL,
    BULBS_FILE,
    DECORATION_CELL,
    DECORATION_STEPS,
    HOLD_CELL,
    HOLD_FILE,
    LIT_FILE,
    LIT_PAD,
    MANIFEST,
    PIECES_DIR,
    SCREEN_KEYS,
    decoration_file,
    expected_sizes,
    lit_cell_size,
    load_pieces,
    piece_hash,
    screen_file,
    screen_size,
)
from bot.utils.gif import shared_palette_gif

FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
)

# -- Geometría ----------------------------------------------------------------------
# Las medidas del campo (WIDTH, PIN_TOP, LCD_BOX, DECORATIONS…) viven en
# `bot.services.pachinko_physics`, que las comparte con la física.

BULB_SPACING = 20
BULB_R = 3

# -- Tiempos (en fotogramas de `FRAME_MS`) -----------------------------------------
# Cuándo sale cada bola (`LAUNCH_GAP`) lo decide `bot.services.pachinko_motion`.

#: Fotogramas que un bolsillo se queda iluminado al recibir una bola.
POCKET_FLASH = 4
#: Paradas de los números del sorteo desde que empieza la tirada.
LEFT_STOP = 5
RIGHT_STOP = 8
CENTER_STOP = 10
REACH_STOP = 22
SUPER_REACH_STOP = 30
#: Fotogramas que el número del centro va frenando antes de parar en un reach.
SLOWDOWN = 6
RESULT_HOLD = 4
ATARI_FRAMES = 12
RUSH_STEP = 4
FINAL_FRAME_MS = 60_000

# -- Colores ------------------------------------------------------------------------

BACKGROUND = (43, 45, 49)  # gris de los embeds en el tema oscuro
CABINET_EDGE = (205, 205, 225)
GOLD = (255, 200, 60)
MAGENTA = (255, 60, 200)
CYAN = (60, 230, 255)
YELLOW = (255, 240, 90)
WHITE = (255, 255, 255)
BLUE = (70, 120, 255)
PIN = (215, 215, 235)
LCD_REACH = (88, 0, 92)
LCD_ATARI = (120, 78, 0)
#: Segundo color de la pantalla, que alterna con el primero cada dos fotogramas.
LCD_REACH_ALT = (60, 0, 70)
LCD_ATARI_ALT = (160, 110, 0)
LCD_BEZEL = (30, 30, 40)
OUT_FILL = (52, 50, 66)
RAINBOW = (MAGENTA, YELLOW, CYAN, WHITE, GOLD, BLUE)
#: Colores de los adornos que no salen de `THEMES`.
PETAL = (255, 170, 215)
PEARL = (20, 60, 70)
#: Casquillo oscuro de las bombillas (también va pintado en los sprites).
SOCKET = (16, 12, 24)
FLAME = (255, 110, 20)
#: Colores del GIF: unos exactos, y el resto repartido entre el fondo y lo que se mueve.
PALETTE_COLORS = 70
#: Colores más frecuentes de la fila de bolsillos que se guardan exactos.
POCKET_COLORS = 14

#: Relleno y forma de los bolsillos que pagan, del que más paga al que menos.
#: Las formas son distintas para que se distingan sin depender del color.
POCKET_RANKS: tuple[tuple[tuple[int, int, int], str], ...] = (
    (GOLD, "star"),
    (CYAN, "diamond"),
    (BLUE, "circle"),
)
OUT_STYLE = (OUT_FILL, "cross")
START_STYLE = (MAGENTA, "tulip")

RGB = tuple[int, int, int]

TITLE_SMALL = "JOVANI VÁZQUEZ"
TRAY_TITLE = "BOLAS"
#: Radio del aro que rodea a cada adorno del campo.
DECORATION_RING_R = 18


@dataclass(frozen=True, slots=True)
class Theme:
    """Aspecto de un tablero.

    Attributes:
        title: Rótulo grande de neón.
        cabinet: Color del mueble.
        board: Fondo del campo de clavos.
        accent: Marcos dorados del campo y de la pantalla.
        halo: Contorno del rótulo (hace de brillo de neón).
        lcd: Fondo de la pantalla en reposo.
        bulb_on, bulb_off: Bombillas encendidas y apagadas.
        decoration: Adorno animado de los lados: `"windmill"`, `"flower"`,
            `"pearl"` o `"flame"`.
    """

    title: str
    cabinet: RGB
    board: RGB
    accent: RGB
    halo: RGB
    lcd: RGB
    bulb_on: RGB
    bulb_off: RGB
    decoration: str


THEMES: dict[str, Theme] = {
    "sakura": Theme(
        "SAKURA", (74, 22, 60), (36, 10, 34), (255, 150, 205), (200, 40, 140),
        (48, 10, 56), (255, 210, 235), (96, 50, 80), "flower",
    ),
    "clasica": Theme(
        "PACHINKO", (34, 16, 64), (22, 12, 44), GOLD, MAGENTA,
        (8, 18, 62), (255, 232, 120), (88, 66, 40), "windmill",
    ),
    "dragon": Theme(
        "DRAGÓN", (8, 46, 56), (4, 24, 32), (90, 230, 210), (20, 120, 160),
        (2, 30, 50), (150, 255, 235), (30, 80, 80), "pearl",
    ),
    "oni": Theme(
        "ONI", (66, 16, 8), (28, 6, 4), (255, 130, 30), (190, 30, 20),
        (40, 6, 4), (255, 190, 80), (90, 40, 20), "flame",
    ),
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Layout:
    """Dónde va cada cosa en la imagen de un tablero.

    La geometría del campo (clavos, paredes, separadores, centro de cada
    bolsillo) es la de `bot.services.pachinko_physics`: lo que se dibuja es lo
    que la bola golpea. Aquí se añade lo que solo es dibujo (textos, bandeja).

    Attributes:
        board: El tablero.
        geometry: Su campo de clavos.
    """

    board: Board
    geometry: Geometry

    @property
    def rows(self) -> int:
        """Filas del tablero (los bolsillos son `rows + 1`)."""
        return self.geometry.rows

    @property
    def dx(self) -> float:
        """Ancho de cada bolsillo."""
        return self.geometry.dx

    @property
    def dy(self) -> float:
        """Distancia entre filas del tablero (fija dónde empiezan los bolsillos)."""
        return self.geometry.dy

    @property
    def pocket_top(self) -> float:
        """Borde de arriba de los bolsillos."""
        return self.geometry.pocket_top

    @property
    def label_y(self) -> float:
        """Altura de los textos de los bolsillos."""
        return self.pocket_top + POCKET_H + 3

    @property
    def tray_top(self) -> float:
        """Borde de arriba de la bandeja con el contador de bolas."""
        return self.label_y + 18

    def pocket_x(self, pocket: int) -> float:
        """Centro del bolsillo `pocket`."""
        return self.geometry.pocket_x(pocket)


def layout_for(board: Board) -> Layout:
    """Disposición de `board`: su campo de clavos y lo que cuelga de él."""
    return Layout(board, geometry_for(board))


@dataclass(frozen=True, slots=True)
class PachinkoMedia:
    """Imágenes de una tanda.

    Attributes:
        gif: Animación; vacía en modo turbo.
        png: Imagen fija final, igual que el último fotograma del GIF.
        seconds: Lo que dura la animación hasta el último fotograma.
    """

    gif: bytes
    png: bytes
    seconds: float


# -- Línea de tiempo ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DrawSlot:
    """Cuándo se juega una tirada del sorteo en la pantalla.

    Attributes:
        start: Fotograma en que empieza a girar.
        center_stop: Fotograma en que para el número del centro.
        end: Primer fotograma libre para la siguiente tirada.
        queued: Fotograma en que entró en la reserva (la bola llegó a START).
    """

    draw: Draw
    queued: int
    start: int
    center_stop: int
    end: int

    @property
    def rush_start(self) -> int:
        """Fotograma en que empieza el contador del rush (tras el cartel de atari)."""
        return self.center_stop + ATARI_FRAMES

    def jackpots_at(self, frame: int) -> int:
        """Premios gordos ya cobrados en `frame`.

        El primero llega con el cartel de atari y cada vuelta del rush,
        `RUSH_STEP` fotogramas después de la anterior.
        """
        if not self.draw.atari or frame < self.center_stop:
            return 0
        if frame < self.rush_start:
            return 1
        return min(self.draw.jackpots, 1 + (frame - self.rush_start) // RUSH_STEP)


def draw_length(draw: Draw) -> tuple[int, int]:
    """`(parada del centro, duración total)` de una tirada, desde su inicio."""
    if draw.kind == Kind.SUPER:
        center = SUPER_REACH_STOP
    elif draw.reach:
        center = REACH_STOP
    else:
        center = CENTER_STOP
    if draw.atari:
        total = center + ATARI_FRAMES + RUSH_STEP * draw.jackpots + RESULT_HOLD
    else:
        total = center + RESULT_HOLD
    return center, total


@dataclass(frozen=True, slots=True)
class Timeline:
    """Todo lo que pasa en la animación de una tanda, en fotogramas.

    Attributes:
        landings: Fotograma en que cada bola entra en su bolsillo.
        slots: Tiradas del sorteo programadas.
        frames: Fotogramas animados (sin contar el final).
    """

    landings: tuple[int, ...]
    slots: tuple[DrawSlot, ...]
    frames: int
    wasted_at: tuple[int, ...] = field(default=())

    def held(self, frame: int) -> int:
        """Tiradas esperando en la reserva en `frame`."""
        return sum(1 for s in self.slots if s.queued <= frame < s.start)

    def slot_at(self, frame: int) -> DrawSlot | None:
        """La tirada que enseña la pantalla en `frame` (la última empezada)."""
        current = None
        for slot in self.slots:
            if slot.start <= frame:
                current = slot
        return current


def build_timeline(volley: Volley, motion: VolleyMotion | None = None) -> Timeline:
    """Programa la animación: cuándo cae cada bola y cuándo gira la pantalla.

    Cada bola sale y entra cuando dice su movimiento (`pachinko_motion`: las
    bolas chocan entre sí y alguna sale con retraso), así que no llegan en el
    orden en que se lanzan. Las tiradas entran en la reserva por orden de
    llegada a START y la pantalla las juega en orden, en cuanto acaba la
    anterior. Las bolas de START que no cupieron en la reserva
    (`Volley.wasted`) son las últimas en llegar. El pago no depende de este
    orden (`build_volley` cuenta bolas), pero la pantalla tiene que ser
    coherente con lo que se ve.

    Args:
        motion: El movimiento de la tanda; si no se da, se calcula (o sale de
            la caché de `motion_for`).
    """
    motion = motion or motion_for(volley)
    landings = tuple(ball.landing for ball in motion.balls)
    start = volley.board.start_pocket
    pairs = zip(volley.balls, landings, strict=True)
    start_landings = sorted(landing for ball, landing in pairs if ball.pocket == start)
    queued = start_landings[: len(volley.draws)]
    wasted = tuple(start_landings[len(volley.draws) :])
    slots = []
    free_at = 0
    for draw, queued_at in zip(volley.draws, queued, strict=True):
        start = max(queued_at, free_at)
        center, total = draw_length(draw)
        slots.append(DrawSlot(draw, queued_at, start, start + center, start + total))
        free_at = start + total
    last_landing = max(landings, default=0)
    frames = max(last_landing + POCKET_FLASH, free_at)
    return Timeline(landings, tuple(slots), frames, wasted)


# -- Dibujo -------------------------------------------------------------------------


def _star(cx: float, cy: float, outer: float, inner: float) -> list[tuple[float, float]]:
    points = []
    for i in range(10):
        radius = outer if i % 2 == 0 else inner
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def _bulb_positions() -> list[tuple[int, int]]:
    """Bombillas repartidas por el borde del mueble, en el sentido del reloj."""
    inset = 7
    left, top, right, bottom = inset, inset, WIDTH - inset - 1, HEIGHT - inset - 1
    points: list[tuple[int, int]] = []
    perimeter = 2 * (right - left) + 2 * (bottom - top)
    count = perimeter // BULB_SPACING
    for i in range(count):
        d = i * perimeter / count
        if d < right - left:
            points.append((round(left + d), top))
            continue
        d -= right - left
        if d < bottom - top:
            points.append((right, round(top + d)))
            continue
        d -= bottom - top
        if d < right - left:
            points.append((round(right - d), bottom))
            continue
        d -= right - left
        points.append((left, round(bottom - d)))
    return points


def _lcd_bulb_positions() -> list[tuple[int, int]]:
    """Bombillas del marco dorado de la pantalla, por arriba y por los lados."""
    x0, y0, x1, y1 = LCD_BOX
    points = [(x, y0 - 5) for x in range(x0 + 6, x1 - 2, 14)]
    points += [(x1 + 5, y) for y in range(y0 + 8, y1 - 2, 14)]
    points += [(x0 - 5, y) for y in range(y1 - 8, y0 + 2, -14)]
    return points


def bulb_palette(theme: Theme) -> list[tuple[RGB, bool]]:
    """Colores distintos que puede tener una bombilla y si dan halo, en orden.

    Son los del reposo (apagada y encendida), el del reach y los del atari. Es
    el orden de las celdas de `bombillas.png`.
    """
    colors: dict[RGB, bool] = {}
    for color, glows in ((theme.bulb_off, False), (theme.bulb_on, True)):
        colors.setdefault(color, glows)
    for color in (MAGENTA, *RAINBOW):
        colors.setdefault(color, True)
    return list(colors.items())


def pocket_style(board: Board, paying: list[int], pocket: int) -> tuple[RGB, str]:
    """Relleno y forma del bolsillo `pocket`; `paying` son sus valores de mayor a menor."""
    value = board.pockets[pocket]
    if pocket == board.start_pocket:
        return START_STYLE
    if value:
        return POCKET_RANKS[min(paying.index(value), len(POCKET_RANKS) - 1)]
    return OUT_STYLE


def piece_spec(board: Board) -> dict:
    """Todo lo que `escena.html` necesita para pintar las piezas de `board`.

    Es la única fuente de las cifras: la geometría es la de la física, los
    colores son los de `THEMES` y las constantes de este módulo, y las medidas
    de los recortes son las de `bot.services.pachinko_pieces`. La escena no
    tiene cifras propias; `docs/pachinko_piezas.py` guarda un resumen de este
    diccionario para saber si los PNG están al día.
    """
    theme = THEMES[board.key]
    layout = layout_for(board)
    geometry = layout.geometry
    paying = sorted({v for v in board.pockets if v}, reverse=True)
    pockets = []
    for pocket in range(board.rows + 1):
        fill, shape = pocket_style(board, paying, pocket)
        has_label = bool(board.pockets[pocket]) or pocket == board.start_pocket
        pockets.append(
            {
                "x": layout.pocket_x(pocket),
                "fill": fill,
                "shape": shape,
                "label": board.pocket_label(pocket) if has_label else None,
                "label_color": MAGENTA if pocket == board.start_pocket else WHITE,
                "label_size": 10 if layout.dx >= 26 else 9,
            }
        )
    segments = lambda items: [[s.x0, s.y0, s.x1, s.y1] for s in items]  # noqa: E731
    return {
        "board": board.key,
        "size": [WIDTH, HEIGHT],
        "cx": CX,
        "colors": {
            "background": BACKGROUND,
            "cabinet_edge": CABINET_EDGE,
            "gold": GOLD,
            "magenta": MAGENTA,
            "cyan": CYAN,
            "yellow": YELLOW,
            "white": WHITE,
            "blue": BLUE,
            "pin": PIN,
            "lcd_bezel": LCD_BEZEL,
            "petal": PETAL,
            "pearl": PEARL,
            "flame": FLAME,
            "socket": SOCKET,
        },
        "theme": {
            "title": theme.title,
            "cabinet": theme.cabinet,
            "board": theme.board,
            "accent": theme.accent,
            "halo": theme.halo,
            "lcd": theme.lcd,
            "bulb_on": theme.bulb_on,
            "bulb_off": theme.bulb_off,
            "decoration": theme.decoration,
        },
        "title": {
            "small": {"text": TITLE_SMALL, "size": 11, "top": 12, "stroke": 1},
            "big": {"text": theme.title, "size": 26, "top": 24, "stroke": 3},
            "star_y": 18,
            "star_r": 6,
            "star_inner": 2.5,
            "star_gap": 12,
        },
        "field_box": list(field_box(layout)),
        "lcd_box": list(LCD_BOX),
        "pocket_top": layout.pocket_top,
        "pocket_h": POCKET_H,
        "label_y": layout.label_y,
        "tray_top": layout.tray_top,
        "tray_text": {"text": TRAY_TITLE, "size": 10},
        "dx": layout.dx,
        "pockets": pockets,
        "pins": [list(pin) for pin in geometry.pins],
        "pin_r": PIN_R,
        "walls": segments(geometry.walls),
        "dividers": segments(geometry.dividers),
        "decorations": [list(d) for d in DECORATIONS],
        "ring_r": DECORATION_RING_R,
        "decoration_steps": DECORATION_STEPS,
        "bulbs": [list(b) for b in _bulb_positions() + _lcd_bulb_positions()],
        "bulb_r": BULB_R,
        "bulb_colors": [{"color": color, "lit": lit} for color, lit in bulb_palette(theme)],
        "ball_r": BALL_R,
        "cells": {
            "ball": BALL_CELL,
            "decoration": DECORATION_CELL,
            "bulb": BULB_CELL,
            "hold": HOLD_CELL,
            "badge": BADGE_CELL,
            "screen": list(screen_size()),
        },
        "lit_cell": list(lit_cell_size(layout.dx)),
        "lit_pad": LIT_PAD,
        "screens": {
            "reposo": theme.lcd,
            "reach_a": LCD_REACH,
            "reach_b": LCD_REACH_ALT,
            "atari_a": LCD_ATARI,
            "atari_b": LCD_ATARI_ALT,
        },
    }


def piece_problems(board: Board, directory: Path = PIECES_DIR) -> list[str]:
    """Lo que falla en los PNG guardados de `board` (vacía si están al día).

    Mira que estén todos, que midan lo que toca y que el resumen de `piezas.json`
    sea el de la geometría, los temas y la escena de ahora. Lo usan
    `docs/pachinko_piezas.py --comprobar` y las pruebas; no necesita navegador.
    """
    theme = THEMES[board.key]
    layout = layout_for(board)
    folder = directory / board.key
    problems = []
    try:
        saved = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))["hash"]
    except (OSError, ValueError, KeyError):
        problems.append(f"falta {MANIFEST}")
    else:
        if saved != piece_hash(piece_spec(board)):
            problems.append(
                "la geometría, los temas o la escena han cambiado desde que se pintaron"
            )
    sizes = expected_sizes(len(bulb_palette(theme)), board.rows + 1, layout.dx)
    for name, size in sizes.items():
        try:
            with Image.open(folder / name) as image:
                if image.size != size:
                    problems.append(f"{name} mide {image.size} y tendría que medir {size}")
        except OSError:
            problems.append(f"falta {name}")
    return problems


def field_box(layout: Layout) -> tuple[int, int, int, float]:
    """Caja del campo de clavos (esquinas incluidas), de la pantalla a la bandeja."""
    return 16, 54, WIDTH - 17, layout.tray_top - 2


@dataclass(slots=True)
class _Assets:
    """Piezas precalculadas de un tablero.

    Attributes:
        base: Fondo estático (RGB).
        lit_strip: Bolsillos iluminados (RGBA, una celda por bolsillo) de los
            PNG, o `None` si se dibujan con Pillow.
        lit_full: Imagen entera con todos los bolsillos iluminados, solo cuando
            no hay `lit_strip` (respaldo con Pillow).
        screens: Fondos de la pantalla por modo (RGB); los que falten se
            pintan lisos.
        bulbs: Sprite de cada color de bombilla; `None` si se dibujan con Pillow.
        hold: Punto de la reserva vacío y lleno; `None` si se dibujan con Pillow.
        badge: Chapa del contador del final; `None` si se dibuja con Pillow.
    """

    board: Board
    theme: Theme
    layout: Layout
    base: Image.Image
    lit_strip: Image.Image | None
    lit_full: Image.Image | None
    ball: Image.Image
    decorations: list[Image.Image]
    screens: dict[str, Image.Image]
    bulbs: dict[RGB, Image.Image] | None
    hold: tuple[Image.Image, Image.Image] | None
    badge: Image.Image | None
    palette: Image.Image


class PachinkoRenderer:
    """Dibuja las tandas del pachinko con piezas precalculadas por tablero.

    Las piezas son los PNG de `assets/pachinko/<tablero>/` (pintados con canvas,
    ver `bot.services.pachinko_pieces`); la que falte se dibuja con Pillow.
    Es seguro llamarlo desde varios hilos: las piezas de cada tablero se
    preparan una vez con un cerrojo y después solo se leen (la caché de
    textos se rellena bajo el mismo cerrojo).

    Args:
        font_path: Tipografía de los textos.
        pieces_dir: Carpeta con las de cada tablero (cambia en las pruebas).
    """

    def __init__(self, font_path: Path = FONT_PATH, pieces_dir: Path = PIECES_DIR) -> None:
        self._font_path = font_path
        self._pieces_dir = pieces_dir
        self._lock = threading.RLock()
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._texts: dict[tuple, Image.Image] = {}
        self._assets: dict[str, _Assets] = {}
        self._bulbs = _bulb_positions() + _lcd_bulb_positions()

    # -- Piezas ---------------------------------------------------------------------

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        font = self._fonts.get(size)
        if font is None:
            font = ImageFont.truetype(str(self._font_path), size)
            self._fonts[size] = font
        return font

    def _text(
        self,
        text: str,
        size: int,
        fill: RGB,
        stroke: RGB = (0, 0, 0),
        stroke_width: int = 2,
    ) -> Image.Image:
        """Texto con contorno en RGBA, guardado en caché (hay pocos distintos)."""
        key = (text, size, fill, stroke, stroke_width)
        with self._lock:
            cached = self._texts.get(key)
            if cached is not None:
                return cached
            font = self._font(size)
            left, top, right, bottom = font.getbbox(text, stroke_width=stroke_width)
            image = Image.new("RGBA", (right - left + 2, bottom - top + 2), (0, 0, 0, 0))
            ImageDraw.Draw(image).text(
                (1 - left, 1 - top),
                text,
                font=font,
                fill=fill,
                stroke_width=stroke_width,
                stroke_fill=stroke,
            )
            self._texts[key] = image
            return image

    def assets(self, board: Board) -> _Assets:
        """Piezas de `board`, preparadas la primera vez (~0,2 s de CPU).

        Carga los PNG de la carpeta del tablero; cada pieza que falte se
        dibuja con Pillow (el juego nunca se queda sin imagen).
        """
        with self._lock:
            cached = self._assets.get(board.key)
            if cached is not None:
                return cached
            theme = THEMES[board.key]
            layout = layout_for(board)
            pieces = load_pieces(
                board.key,
                len(bulb_palette(theme)),
                board.rows + 1,
                layout.dx,
                self._pieces_dir,
            )
            assets = self._assemble(board, theme, layout, pieces)
            self._assets[board.key] = assets
            return assets

    def _assemble(
        self, board: Board, theme: Theme, layout: Layout, pieces: dict[str, Image.Image]
    ) -> _Assets:
        """Reúne las piezas de un tablero: los PNG que hay y, de lo que falte, Pillow."""
        background = pieces.get(BACKGROUND_FILE)
        base = (
            background.convert("RGB")
            if background is not None
            else self._draw_board(board, theme, layout, lit=False)
        )
        lit_strip = pieces.get(LIT_FILE)
        lit_full = None if lit_strip else self._draw_board(board, theme, layout, lit=True)
        ball = pieces.get(BALL_FILE) or self._ball_sprite()
        decorations = [
            pieces.get(decoration_file(step)) or self._decoration(theme, step)
            for step in range(DECORATION_STEPS)
        ]
        screens = {
            key: pieces[screen_file(key)].convert("RGB")
            for key in SCREEN_KEYS
            if screen_file(key) in pieces
        }
        atlas = pieces.get(BULBS_FILE)
        bulbs = None
        if atlas is not None:
            bulbs = {
                color: atlas.crop((i * BULB_CELL, 0, (i + 1) * BULB_CELL, BULB_CELL))
                for i, (color, _) in enumerate(bulb_palette(theme))
            }
        pair = pieces.get(HOLD_FILE)
        hold = None
        if pair is not None:
            hold = (
                pair.crop((0, 0, HOLD_CELL, HOLD_CELL)),
                pair.crop((HOLD_CELL, 0, 2 * HOLD_CELL, HOLD_CELL)),
            )
        assets = _Assets(
            board, theme, layout, base, lit_strip, lit_full, ball, decorations, screens,
            bulbs, hold, pieces.get(BADGE_FILE), palette=Image.new("P", (1, 1)),
        )  # fmt: skip
        assets.palette = self._palette(assets)
        return assets

    def _palette(self, assets: _Assets) -> Image.Image:
        """Paleta común del GIF, sacada de fotogramas de muestra de cada modo.

        Se mide sobre lo que de verdad sale (fondo con las bombillas del
        reposo, del reach y del atari, la pantalla de cada modo y los bolsillos
        iluminados) y no solo sobre el fondo: así los halos y los degradados no
        se quedan sin sus colores.
        """
        theme = assets.theme
        shots = []
        for mode, screen in (("idle", "reposo"), ("reach", "reach_a"), ("atari", "atari_a")):
            image = assets.base.copy()
            self._decorate(assets, image, len(shots))
            sprite = assets.screens.get(screen)
            if sprite is not None:
                image.paste(sprite, LCD_BOX[:2])
            if mode == "idle":
                for i in range(6):
                    self._paste(image, assets.ball, 60 + i * 41, 250 + (i % 3) * 30)
            self._bulbs_layer(image, ImageDraw.Draw(image), assets, 0, mode)
            self._hold_dots(image, ImageDraw.Draw(image), assets, 2)
            shots.append(image)
        flashed = assets.base.copy()
        for pocket in range(assets.board.rows + 1):
            self._flash(assets, flashed, pocket)
        shots.append(flashed)
        # Tres partes de paleta. Primero los colores que tienen que salir exactos
        # (los de los carteles, los números, las pantallas, las bombillas y los
        # bolsillos: pocos y pequeños, un degradado los lavaría hacia el morado
        # del mueble). Después una parte para el fondo (casi toda la imagen, con
        # los degradados) y otra para lo que cambia (halos, bolas, adornos).
        base = np.asarray(assets.base)
        top = round(assets.layout.pocket_top)
        pocket_row = Image.fromarray(base[top : top + POCKET_H + 1], "RGB")
        frequent = sorted(pocket_row.getcolors(10_000) or [], reverse=True)[:POCKET_COLORS]
        exact = [
            *RAINBOW, GOLD, YELLOW, WHITE, (0, 0, 0), PIN, OUT_FILL, LCD_REACH, LCD_ATARI,
            LCD_REACH_ALT, LCD_ATARI_ALT, (90, 110, 170), theme.bulb_on, theme.bulb_off,
            theme.lcd, theme.board, theme.cabinet, theme.accent, theme.halo,
            *(color for _, color in frequent),
        ]  # fmt: skip
        exact = list(dict.fromkeys(exact))
        rest = max(2, PALETTE_COLORS - len(exact))
        moving = [np.asarray(shot)[(np.asarray(shot) != base).any(axis=2)] for shot in shots]
        colors = np.unique(np.concatenate(moving), axis=0)
        moving_image = Image.fromarray(colors.reshape(-1, 1, 3), "RGB")
        parts = (
            (assets.base.quantize(colors=rest // 2, method=Image.Quantize.MEDIANCUT), rest // 2),
            (moving_image.quantize(colors=rest - rest // 2, method=Image.Quantize.MEDIANCUT),
             rest - rest // 2),
        )  # fmt: skip
        entries = [channel for color in exact for channel in color]
        for part, count in parts:
            raw = part.getpalette() or []
            used = len(part.getcolors() or [])
            entries.extend(raw[: min(count, used) * 3])
        palette = Image.new("P", (1, 1))
        palette.putpalette(entries)
        return palette

    def _draw_board(self, board: Board, theme: Theme, layout: Layout, *, lit: bool) -> Image.Image:
        """Mueble, rótulo, marco de la pantalla, clavos y bolsillos.

        Con `lit`, todos los bolsillos están encendidos; de ahí se recorta el
        que destella cuando entra una bola.
        """
        image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(image)
        field = field_box(layout)
        draw.rounded_rectangle((1, 1, WIDTH - 2, HEIGHT - 2), radius=18, fill=theme.cabinet)
        draw.rounded_rectangle(
            (1, 1, WIDTH - 2, HEIGHT - 2), radius=18, outline=CABINET_EDGE, width=3
        )
        draw.rounded_rectangle(field, radius=12, fill=theme.board)
        draw.rounded_rectangle(field, radius=12, outline=theme.accent, width=2)

        # Rótulo de neón: el color de contorno hace de halo.
        small = self._text(TITLE_SMALL, 11, YELLOW, theme.halo, 1)
        image.paste(small, (CX - small.width // 2, 12), small)
        for side in (-1, 1):
            sx = CX + side * (small.width // 2 + 12)
            draw.polygon(_star(sx, 18, 6, 2.5), fill=YELLOW)
        big = self._text(theme.title, 26, WHITE, theme.halo, 3)
        image.paste(big, (CX - big.width // 2, 24), big)

        # Marco de la pantalla.
        x0, y0, x1, y1 = LCD_BOX
        draw.rounded_rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), radius=10, fill=theme.accent)
        draw.rounded_rectangle((x0 - 3, y0 - 3, x1 + 3, y1 + 3), radius=8, fill=LCD_BEZEL)
        draw.rectangle(LCD_BOX, fill=theme.lcd)

        # Campo de clavos: los mismos que golpea la física, con las paredes y los
        # separadores de los bolsillos.
        for wall in layout.geometry.walls:
            draw.line((wall.x0, wall.y0, wall.x1, wall.y1), fill=theme.accent, width=2)
        for x, y in layout.geometry.pins:
            draw.ellipse((x - PIN_R, y - PIN_R, x + PIN_R, y + PIN_R), fill=PIN)
        for divider in layout.geometry.dividers:
            draw.line((divider.x0, divider.y0, divider.x1, divider.y1), fill=PIN, width=2)
        for mx, my in DECORATIONS:
            ring = DECORATION_RING_R
            box = (mx - ring, my - ring, mx + ring, my + ring)
            draw.ellipse(box, outline=theme.accent, width=2)

        # Bolsillos con su forma y, debajo, su valor.
        paying = sorted({v for v in board.pockets if v}, reverse=True)
        for pocket in range(board.rows + 1):
            self._draw_pocket(image, draw, board, theme, layout, paying, pocket, lit=lit)
        draw.text(
            (CX, layout.tray_top + 16),
            TRAY_TITLE,
            font=self._font(10),
            fill=theme.accent,
            anchor="mm",
        )
        return image

    def _draw_pocket(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        board: Board,
        theme: Theme,
        layout: Layout,
        paying: list[int],
        pocket: int,
        *,
        lit: bool,
    ) -> None:
        value = board.pockets[pocket]
        fill, shape = pocket_style(board, paying, pocket)
        x = layout.pocket_x(pocket)
        half = layout.dx / 2
        top = layout.pocket_top
        box = (x - half + 2, top, x + half - 2, top + POCKET_H)
        body = WHITE if lit else fill
        draw.rounded_rectangle(
            box, radius=5, fill=body, outline=theme.accent if lit else PIN, width=1
        )
        mark = fill if lit else theme.board
        cy = top + POCKET_H * 0.62
        size = min(8, half - 4)
        if shape == "star":
            draw.polygon(_star(x, cy, size, size * 0.45), fill=mark)
        elif shape == "diamond":
            draw.polygon(
                [(x, cy - size), (x + size * 0.75, cy), (x, cy + size), (x - size * 0.75, cy)],
                fill=mark,
            )
        elif shape == "circle":
            r = size * 0.65
            draw.ellipse((x - r, cy - r, x + r, cy + r), fill=mark)
        elif shape == "cross":
            r = size * 0.65
            draw.line((x - r, cy - r, x + r, cy + r), fill=PIN, width=2)
            draw.line((x - r, cy + r, x + r, cy - r), fill=PIN, width=2)
        else:  # tulipán de la ranura START
            k = size / 8
            draw.polygon(
                [(x - 8 * k, cy - 8 * k), (x - 4 * k, cy - 2 * k), (x, cy - 9 * k),
                 (x + 4 * k, cy - 2 * k), (x + 8 * k, cy - 8 * k), (x + 6 * k, cy + 5 * k),
                 (x - 6 * k, cy + 5 * k)],
                fill=mark,
            )  # fmt: skip
        if not value and pocket != board.start_pocket:
            # OUT no lleva texto: el aspa ya lo dice y así START tiene sitio.
            return
        label_color = MAGENTA if pocket == board.start_pocket else WHITE
        font_size = 10 if layout.dx >= 26 else 9
        label = self._text(board.pocket_label(pocket), font_size, label_color, (0, 0, 0), 1)
        image.paste(label, (round(x - label.width / 2), round(layout.label_y)), label)

    def _ball_sprite(self) -> Image.Image:
        """La bola con Pillow (respaldo de `bola.png`)."""
        size = BALL_R * 2 + 1
        sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        draw.ellipse((0, 0, size - 1, size - 1), fill=(175, 180, 195), outline=(90, 90, 110))
        draw.ellipse((2, 2, 5, 5), fill=WHITE)
        return sprite

    def _decoration(self, theme: Theme, step: int) -> Image.Image:
        """Adorno de los lados en el paso `step` (0-3) con Pillow (respaldo de `adorno_N.png`)."""
        size = 33
        sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        c = size / 2
        if theme.decoration == "flower":
            # Flor de cerezo de cinco pétalos que gira despacio.
            for petal in range(5):
                a = math.radians(step * 18 + petal * 72)
                px, py = c + 9 * math.cos(a), c + 9 * math.sin(a)
                draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=PETAL)
                draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=WHITE)
            draw.ellipse((c - 4, c - 4, c + 4, c + 4), fill=YELLOW)
        elif theme.decoration == "pearl":
            # Perla del dragón con tres comas (tomoe) que dan vueltas.
            draw.ellipse((c - 14, c - 14, c + 14, c + 14), fill=PEARL, outline=theme.accent)
            for comma in range(3):
                a = math.radians(step * 30 + comma * 120)
                hx, hy = c + 7 * math.cos(a), c + 7 * math.sin(a)
                draw.ellipse((hx - 4, hy - 4, hx + 4, hy + 4), fill=GOLD)
                tail = (c + 11 * math.cos(a + 0.9), c + 11 * math.sin(a + 0.9))
                draw.polygon([(hx, hy - 3), tail, (hx, hy + 3)], fill=GOLD)
            draw.ellipse((c - 3, c - 3, c + 3, c + 3), fill=WHITE)
        elif theme.decoration == "flame":
            # Llama que parpadea: cambia de altura y de inclinación.
            height = (14, 16, 13, 15)[step]
            lean = (-2, 1, 2, -1)[step]
            draw.polygon(
                [(c - 10, c + 12), (c + lean - 3, c - height), (c + 10, c + 12)],
                fill=FLAME,
            )
            draw.polygon(
                [(c - 6, c + 12), (c + lean, c - height + 7), (c + 6, c + 12)],
                fill=YELLOW,
            )
            draw.ellipse((c - 3, c + 6, c + 3, c + 12), fill=WHITE)
        else:
            # Molinillo de cuatro aspas.
            for blade in range(4):
                a = math.radians(step * 22.5 + blade * 90)
                tip = (c + 14 * math.cos(a), c + 14 * math.sin(a))
                side = (c + 9 * math.cos(a + 0.5), c + 9 * math.sin(a + 0.5))
                draw.polygon([(c, c), side, tip], fill=CYAN if blade % 2 else MAGENTA)
            draw.ellipse((c - 3, c - 3, c + 3, c + 3), fill=GOLD)
        return sprite

    # -- Fotogramas -----------------------------------------------------------------

    def _paste(self, frame: Image.Image, sprite: Image.Image, cx: float, cy: float) -> None:
        frame.paste(sprite, (round(cx - sprite.width / 2), round(cy - sprite.height / 2)), sprite)

    def _bulbs_layer(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        assets: _Assets,
        frame: int,
        mode: str,
    ) -> None:
        """Bombillas del borde: persiguen, corren en un reach y se vuelven locas en un atari."""
        theme = assets.theme
        sprites = assets.bulbs
        reach = BULB_CELL // 2
        for i, (x, y) in enumerate(self._bulbs):
            if mode == "atari":
                color = RAINBOW[(i + frame) % len(RAINBOW)]
            elif mode == "reach":
                color = MAGENTA if (i + frame) % 2 == 0 else theme.bulb_on
            else:
                color = theme.bulb_on if (i + frame // 2) % 4 == 0 else theme.bulb_off
            if sprites is None:
                draw.ellipse((x - BULB_R, y - BULB_R, x + BULB_R, y + BULB_R), fill=color)
            else:
                sprite = sprites[color]
                image.paste(sprite, (x - reach, y - reach), sprite)

    def _flash(self, assets: _Assets, image: Image.Image, pocket: int) -> None:
        """Enciende el bolsillo `pocket`: se pega su celda iluminada (con su resplandor)."""
        layout = assets.layout
        x = layout.pocket_x(pocket)
        if assets.lit_strip is None:
            # Respaldo con Pillow: el recorte del bolsillo de la imagen iluminada.
            box = (
                round(x - layout.dx / 2),
                round(layout.pocket_top) - 1,
                round(x + layout.dx / 2),
                round(layout.pocket_top + POCKET_H) + 1,
            )
            assert assets.lit_full is not None
            image.paste(assets.lit_full.crop(box), box[:2])
            return
        cell_w, cell_h = lit_cell_size(layout.dx)
        cell = assets.lit_strip.crop((pocket * cell_w, 0, (pocket + 1) * cell_w, cell_h))
        image.paste(cell, (round(x) - (cell_w - 1) // 2, round(layout.pocket_top) - LIT_PAD), cell)

    def _digit(self, value: int, color: RGB, blur: bool = False) -> Image.Image:
        sprite = self._text(str(value), 46, color, (0, 0, 0), 3)
        if blur:
            # Número girando: se dibuja estirado y más bajo, sin calcular un desenfoque.
            return sprite.resize((sprite.width, sprite.height + 14))
        return sprite

    def _lcd(
        self,
        assets: _Assets,
        frame_image: Image.Image,
        frame: int,
        slot: DrawSlot | None,
        rng: random.Random,
        *,
        final: bool = False,
    ) -> str:
        """Dibuja la pantalla y devuelve el modo de las bombillas."""
        draw = ImageDraw.Draw(frame_image)
        x0, y0, x1, y1 = LCD_BOX
        centers = (x0 + 38, CX, x1 - 38)
        mid_y = (y0 + y1) // 2 + 2
        if slot is None:
            self._screen(assets, frame_image, draw, "reposo", assets.theme.lcd)
            for cx in centers:
                self._paste(frame_image, self._digit(7, (90, 110, 170)), cx, mid_y)
            tag = self._text(f"¡DALE! · {assets.board.risk.upper()}", 12, YELLOW, (0, 0, 0), 2)
            self._paste(frame_image, tag, CX, y1 - 9)
            return "idle"

        d = slot.draw
        t = frame - slot.start
        atari_on = d.atari and frame >= slot.center_stop
        reach_on = d.reach and t >= RIGHT_STOP and frame < slot.center_stop
        if atari_on:
            first = (frame // 2) % 2 or final
            key, bg = ("atari_a", LCD_ATARI) if first else ("atari_b", LCD_ATARI_ALT)
        elif reach_on:
            key, bg = ("reach_a", LCD_REACH) if (frame // 2) % 2 else ("reach_b", LCD_REACH_ALT)
        else:
            key, bg = "reposo", assets.theme.lcd
        self._screen(assets, frame_image, draw, key, bg)
        digit_color = GOLD if atari_on else WHITE
        stops = (LEFT_STOP, slot.center_stop - slot.start, RIGHT_STOP)
        order = (0, 2, 1)  # paran izquierda, derecha y, por último, el centro
        for reel in order:
            stop = stops[reel]
            cx = centers[reel]
            target = d.digits[reel]
            if t >= stop or final:
                self._paste(frame_image, self._digit(target, digit_color), cx, mid_y)
            elif reel == 1 and d.reach and stop - t <= SLOWDOWN:
                # Frenando: pasan los números uno a uno hasta el bueno.
                shown = (target - (stop - t) - 1) % 9 + 1
                self._paste(frame_image, self._digit(shown, YELLOW), cx, mid_y)
            else:
                self._paste(frame_image, self._digit(rng.randint(1, 9), CYAN, True), cx, mid_y)

        if reach_on and (frame % 3) != 2:
            text = "SUPER REACH!" if d.kind == Kind.SUPER else "REACH!"
            banner = self._text(text, 20, YELLOW, MAGENTA, 3)
            self._paste(frame_image, banner, CX, y0 + 12)
        if atari_on:
            jackpots = slot.jackpots_at(frame) if not final else d.jackpots
            if frame < slot.rush_start and not final:
                color = RAINBOW[frame % len(RAINBOW)]
                banner = self._text("¡ATARI!", 30, color, (0, 0, 0), 4)
                self._paste(frame_image, banner, CX, y0 + 18)
            elif d.kind == Kind.ATARI:
                fever = assets.board.fever_balls
                banner = self._text(f"FEVER +{fever}", 18, YELLOW, MAGENTA, 3)
                self._paste(frame_image, banner, CX, y0 + 12)
            else:
                name = "SUPER RUSH" if d.kind == Kind.SUPER else "RUSH"
                banner = self._text(f"{name} ×{jackpots}", 18, YELLOW, MAGENTA, 3)
                self._paste(frame_image, banner, CX, y0 + 12)
            return "atari"
        return "reach" if reach_on else "idle"

    def _screen(
        self,
        assets: _Assets,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        key: str,
        color: RGB,
    ) -> None:
        """Fondo de la pantalla: el PNG de su modo o, si no hay, un rectángulo liso."""
        sprite = assets.screens.get(key)
        if sprite is None:
            draw.rectangle(LCD_BOX, fill=color)
        else:
            image.paste(sprite, LCD_BOX[:2])

    def _hold_dots(
        self, image: Image.Image, draw: ImageDraw.ImageDraw, assets: _Assets, held: int
    ) -> None:
        """Reserva: puntos llenos (tiradas pendientes) y huecos (vacíos)."""
        for i in range(MAX_HOLD):
            x = CX + (i - (MAX_HOLD - 1) / 2) * 16
            if assets.hold is not None:
                dot = assets.hold[1 if i < held else 0]
                self._paste(image, dot, x, HOLD_Y)
                continue
            box = (x - 5, HOLD_Y - 5, x + 5, HOLD_Y + 5)
            if i < held:
                draw.ellipse(box, fill=MAGENTA, outline=WHITE)
            else:
                draw.ellipse(box, outline=PIN, width=1)

    def _tray(
        self, layout: Layout, frame_image: Image.Image, balls: int, *, final: bool = False
    ) -> None:
        text = f"+{balls}" if final else str(balls)
        color = GOLD if balls >= BALLS else WHITE
        sprite = self._text(text, 16, color, (0, 0, 0), 2)
        y = round(layout.tray_top + 16 - sprite.height / 2)
        frame_image.paste(sprite, (CX + 26, y), sprite)

    def _decorate(self, assets: _Assets, image: Image.Image, step: int) -> None:
        sprite = assets.decorations[step % len(assets.decorations)]
        for mx, my in DECORATIONS:
            self._paste(image, sprite, mx, my)

    def _frame(
        self,
        assets: _Assets,
        volley: Volley,
        motion: VolleyMotion,
        timeline: Timeline,
        frame: int,
        rng: random.Random,
    ) -> Image.Image:
        layout = assets.layout
        image = assets.base.copy()
        draw = ImageDraw.Draw(image)

        # Bolsillos que destellan porque acaba de entrar una bola.
        for ball, landing in zip(volley.balls, timeline.landings, strict=True):
            if landing <= frame < landing + POCKET_FLASH and (frame - landing) % 2 == 0:
                self._flash(assets, image, ball.pocket)

        self._decorate(assets, image, frame // 2)

        for ball in motion.balls:
            position = ball.position(frame)
            if position is not None:
                self._paste(image, assets.ball, *position)

        slot = timeline.slot_at(frame)
        mode = self._lcd(assets, image, frame, slot, rng)
        if mode == "atari":
            # Lluvia de bolas de la compuerta abierta.
            for _ in range(10):
                x = rng.randint(30, WIDTH - 30)
                y = rng.randint(LCD_BOX[3] + 10, round(layout.pocket_top) - 6)
                self._paste(image, assets.ball, x, y)
        self._bulbs_layer(image, draw, assets, frame, mode)
        self._hold_dots(image, draw, assets, timeline.held(frame))

        balls = sum(
            volley.returned(ball)
            for ball, landing in zip(volley.balls, timeline.landings, strict=True)
            if landing <= frame
        )
        balls += volley.board.fever_balls * sum(s.jackpots_at(frame) for s in timeline.slots)
        self._tray(layout, image, balls)
        return image

    def _final(self, assets: _Assets, volley: Volley, timeline: Timeline) -> Image.Image:
        """Imagen final: bolas contadas en cada bolsillo y la pantalla en el mejor sorteo."""
        layout = assets.layout
        image = assets.base.copy()
        draw = ImageDraw.Draw(image)
        self._decorate(assets, image, 0)
        for pocket, count in enumerate(volley.pocket_counts()):
            if not count:
                continue
            x = layout.pocket_x(pocket)
            y = layout.pocket_top + 9
            r = min(8, layout.dx / 2 - 2)
            if assets.badge is not None:
                self._paste(image, assets.badge, x, y)
            else:
                draw.ellipse((x - r, y - r, x + r, y + r), fill=WHITE, outline=assets.theme.board)
            badge = self._text(str(count), 11, assets.theme.board, WHITE, 0)
            self._paste(image, badge, x, y)
        best = volley.best
        slot = None
        if timeline.slots:
            slot = next((s for s in timeline.slots if s.draw is best), timeline.slots[-1])
        mode = self._lcd(assets, image, timeline.frames, slot, random.Random(0), final=True)
        self._bulbs_layer(image, draw, assets, 0, "atari" if mode == "atari" else "idle")
        self._hold_dots(image, draw, assets, 0)
        self._tray(layout, image, volley.total_balls, final=True)
        return image

    @staticmethod
    def _quantize(assets: _Assets, image: Image.Image) -> Image.Image:
        return image.quantize(palette=assets.palette, dither=Image.Dither.NONE)

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def idle_png(self, board: Board) -> bytes:
        """La máquina parada, al abrirla o al cambiar de tablero."""
        assets = self.assets(board)
        image = assets.base.copy()
        self._decorate(assets, image, 0)
        self._lcd(assets, image, 0, None, random.Random(0))
        draw = ImageDraw.Draw(image)
        self._bulbs_layer(image, draw, assets, 0, "idle")
        self._hold_dots(image, draw, assets, 0)
        self._tray(assets.layout, image, 0)
        return self._png(self._quantize(assets, image))

    def still_png(self, volley: Volley, motion: VolleyMotion | None = None) -> bytes:
        """PNG del final de una tanda, sin animación (Ráfaga)."""
        assets = self.assets(volley.board)
        final = self._final(assets, volley, build_timeline(volley, motion))
        return self._png(self._quantize(assets, final))

    def warm_up(self) -> None:
        """Prepara las piezas de todos los tableros (al cargar el cog)."""
        for board in BOARDS.values():
            library(board)
            self.assets(board)

    def render(
        self, volley: Volley, *, turbo: bool = False, motion: VolleyMotion | None = None
    ) -> PachinkoMedia:
        """Animación y PNG final de una tanda.

        Args:
            turbo: Solo el PNG final, sin GIF (más rápido y casi sin datos).
            motion: Su movimiento (`motion_for`); si no se da, se calcula.
        """
        assets = self.assets(volley.board)
        motion = motion or motion_for(volley)
        timeline = build_timeline(volley, motion)
        final = self._quantize(assets, self._final(assets, volley, timeline))
        if turbo:
            return PachinkoMedia(gif=b"", png=self._png(final), seconds=0.0)
        # Los números que pasan girando son de adorno; con una semilla fija
        # por tanda, la misma tanda se dibuja siempre igual.
        rng = random.Random(hash(tuple((ball.path, ball.trajectory) for ball in volley.balls)))
        frames = [
            self._quantize(assets, self._frame(assets, volley, motion, timeline, index, rng))
            for index in range(timeline.frames)
        ]
        frames.append(final)
        durations = [FRAME_MS] * (len(frames) - 1) + [FINAL_FRAME_MS]
        seconds = FRAME_MS * (len(frames) - 1) / 1000
        gif = shared_palette_gif(frames, durations)
        return PachinkoMedia(gif=gif, png=self._png(final), seconds=seconds)
