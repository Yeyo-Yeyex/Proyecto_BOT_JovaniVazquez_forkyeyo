"""Animación del pachinko: un GIF por tanda y una imagen fija final.

La máquina imita las de los salones japoneses: un mueble con bombillas que
persiguen, un rótulo de neón, una pantalla con el sorteo, un campo de clavos
con dos adornos que se mueven y los bolsillos abajo. Una tanda cuesta ~0,5 s
de CPU (fuera del event loop) y unos cientos de KB de GIF. El modo turbo solo
manda la imagen final.

Cada tablero de `bot.services.pachinko.BOARDS` tiene su tema (`THEMES`):
colores, rótulo y adorno (molinillos, flores de cerezo, perlas de dragón o
llamas). La geometría (`Layout`) sale de sus filas: con más filas, los
clavos se juntan para que todo quepa en la misma imagen.

La animación sigue la línea de tiempo de una máquina real:

1. Las bolas se lanzan de una en una y bajan rebotando por los clavos, cada
   una por el camino que ha decidido `bot.services.pachinko`.
2. Cuando una cae en START, la reserva (los puntos bajo la pantalla) gana una
   tirada, y la pantalla la juega en cuanto queda libre, mientras siguen
   cayendo bolas.
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
import math
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.services.pachinko import (
    BALLS,
    BOARDS,
    MAX_HOLD,
    Ball,
    Board,
    Draw,
    Kind,
    Volley,
)

FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
)

# -- Geometría ----------------------------------------------------------------------

WIDTH = 340
HEIGHT = 500
CX = WIDTH // 2
#: Altura de la primera fila de clavos.
PIN_TOP = 190
#: Espacio vertical para las filas de clavos y ancho para los bolsillos.
PIN_SPACE = 192
POCKET_SPACE = 304
#: Separación máxima entre clavos (en horizontal y en vertical).
MAX_DX = 28
MAX_DY = 24
PIN_R = 2
BALL_R = 5
#: Donde aparece cada bola, encima del primer clavo.
ENTRY_Y = 168
POCKET_H = 36
LCD_BOX = (70, 64, 270, 148)
HOLD_Y = 162
DECORATIONS = ((44, 232), (296, 232))
BULB_SPACING = 20
BULB_R = 3

# -- Tiempos (en fotogramas) --------------------------------------------------------

FRAME_MS = 50
#: Fotogramas entre una bola y la siguiente.
LAUNCH_GAP = 3
#: Fotogramas de la entrada hasta el primer clavo, por fila y hasta el bolsillo.
ENTRY_FRAMES = 2
ROW_FRAMES = 2
EXIT_FRAMES = 2
#: Altura del saltito al rebotar en cada clavo, en píxeles.
HOP = 5
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
OUT_FILL = (52, 50, 66)
RAINBOW = (MAGENTA, YELLOW, CYAN, WHITE, GOLD, BLUE)
PALETTE_COLORS = 64

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
    """Geometría de un tablero según sus filas.

    Attributes:
        rows: Filas de clavos.
        dx: Separación horizontal entre clavos y entre bolsillos.
        dy: Distancia vertical entre filas.
    """

    rows: int
    dx: float
    dy: float

    @property
    def pocket_top(self) -> float:
        """Borde de arriba de los bolsillos."""
        return PIN_TOP + self.dy * self.rows + 4

    @property
    def label_y(self) -> float:
        """Altura de los textos de los bolsillos."""
        return self.pocket_top + POCKET_H + 3

    @property
    def tray_top(self) -> float:
        """Borde de arriba de la bandeja con el contador de bolas."""
        return self.label_y + 18

    @property
    def ball_frames(self) -> int:
        """Fotogramas desde que se lanza una bola hasta que entra en su bolsillo."""
        return ENTRY_FRAMES + self.rows * ROW_FRAMES + EXIT_FRAMES

    def pin_x(self, row: int, rights: int) -> float:
        """Horizontal del clavo que golpea una bola en `row` tras `rights` derechas."""
        return CX + (rights - row / 2) * self.dx

    def pocket_x(self, pocket: int) -> float:
        """Centro del bolsillo `pocket`."""
        return CX + (pocket - self.rows / 2) * self.dx

    def ball_position(self, ball: Ball, frame: float) -> tuple[float, float] | None:
        """Dónde está una bola `frame` fotogramas después de lanzarla.

        Devuelve `None` antes de lanzarla y después de entrar en el bolsillo.
        Entre dos filas avanza en línea recta con un saltito (`HOP`), que es lo
        que hace que parezca que rebota en el clavo.
        """
        if frame < 0 or frame >= self.ball_frames:
            return None
        if frame < ENTRY_FRAMES:
            t = frame / ENTRY_FRAMES
            return CX, ENTRY_Y + (PIN_TOP - BALL_R - ENTRY_Y) * t * t
        frame -= ENTRY_FRAMES
        if frame < self.rows * ROW_FRAMES:
            row, sub = divmod(frame, ROW_FRAMES)
            row = int(row)
            t = sub / ROW_FRAMES
            rights = sum(ball.path[:row])
            x0 = self.pin_x(row, rights)
            x1 = x0 + (self.dx / 2 if ball.path[row] else -self.dx / 2)
            y0 = PIN_TOP + row * self.dy - BALL_R
            y1 = y0 + self.dy
            return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t - HOP * math.sin(math.pi * t)
        t = (frame - self.rows * ROW_FRAMES) / EXIT_FRAMES
        x = self.pocket_x(ball.pocket)
        y0 = PIN_TOP + self.rows * self.dy - BALL_R
        return x, y0 + (self.pocket_top + 10 - y0) * t


def layout_for(board: Board) -> Layout:
    """Geometría de `board`: los clavos se juntan si hay muchas filas."""
    dx = min(MAX_DX, POCKET_SPACE // (board.rows + 1))
    dy = min(MAX_DY, PIN_SPACE // board.rows)
    return Layout(board.rows, dx, dy)


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


def build_timeline(volley: Volley) -> Timeline:
    """Programa la animación: cuándo cae cada bola y cuándo gira la pantalla.

    Las tiradas entran en la reserva cuando su bola llega a START y la
    pantalla las juega en orden, en cuanto acaba la anterior. Las bolas de
    START que no cupieron en la reserva (`Volley.wasted`) son las últimas en
    llegar.
    """
    ball_frames = layout_for(volley.board).ball_frames
    landings = tuple(index * LAUNCH_GAP + ball_frames for index in range(len(volley.balls)))
    start = volley.board.start_pocket
    pairs = zip(volley.balls, landings, strict=True)
    start_landings = [landing for ball, landing in pairs if ball.pocket == start]
    queued = start_landings[: len(volley.draws)]
    wasted = tuple(start_landings[len(volley.draws) :])
    slots = []
    free_at = 0
    for draw, queued_at in zip(volley.draws, queued, strict=True):
        start = max(queued_at, free_at)
        center, total = draw_length(draw)
        slots.append(DrawSlot(draw, queued_at, start, start + center, start + total))
        free_at = start + total
    last_landing = landings[-1] if landings else 0
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


@dataclass(slots=True)
class _Assets:
    """Piezas precalculadas de un tablero."""

    board: Board
    theme: Theme
    layout: Layout
    base: Image.Image
    lit: Image.Image
    decorations: list[Image.Image]
    palette: Image.Image


class PachinkoRenderer:
    """Dibuja las tandas del pachinko con piezas precalculadas por tablero.

    Es seguro llamarlo desde varios hilos: las piezas de cada tablero se
    preparan una vez con un cerrojo y después solo se leen (la caché de
    textos se rellena bajo el mismo cerrojo).
    """

    def __init__(self, font_path: Path = FONT_PATH) -> None:
        self._font_path = font_path
        self._lock = threading.RLock()
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._texts: dict[tuple, Image.Image] = {}
        self._assets: dict[str, _Assets] = {}
        self._bulbs = _bulb_positions() + _lcd_bulb_positions()
        self._ball = self._ball_sprite()

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
        """Piezas de `board`, preparadas la primera vez (~0,05 s de CPU)."""
        with self._lock:
            cached = self._assets.get(board.key)
            if cached is not None:
                return cached
            theme = THEMES[board.key]
            layout = layout_for(board)
            base = self._draw_board(board, theme, layout, lit=False)
            lit = self._draw_board(board, theme, layout, lit=True)
            decorations = [self._decoration(theme, step) for step in range(4)]
            sample = Image.new("RGB", (WIDTH * 2, HEIGHT))
            sample.paste(base, (0, 0))
            sample.paste(lit, (WIDTH, 0))
            # Los colores de los carteles y de la pantalla también tienen que
            # estar en la paleta aunque no salgan en el tablero.
            swatch = ImageDraw.Draw(sample)
            extra = (*RAINBOW, LCD_REACH, LCD_ATARI, theme.bulb_on, theme.bulb_off, theme.lcd)
            for i, color in enumerate(extra):
                swatch.rectangle((i * 8, 0, i * 8 + 7, 7), fill=color)
            for i, sprite in enumerate(decorations):
                sample.paste(sprite, (i * 34, 10), sprite)
            palette = sample.quantize(colors=PALETTE_COLORS, method=Image.Quantize.MEDIANCUT)
            assets = _Assets(board, theme, layout, base, lit, decorations, palette)
            self._assets[board.key] = assets
            return assets

    def _draw_board(self, board: Board, theme: Theme, layout: Layout, *, lit: bool) -> Image.Image:
        """Mueble, rótulo, marco de la pantalla, clavos y bolsillos.

        Con `lit`, todos los bolsillos están encendidos; de ahí se recorta el
        que destella cuando entra una bola.
        """
        image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(image)
        field_box = (16, 54, WIDTH - 17, layout.tray_top - 2)
        draw.rounded_rectangle((1, 1, WIDTH - 2, HEIGHT - 2), radius=18, fill=theme.cabinet)
        draw.rounded_rectangle(
            (1, 1, WIDTH - 2, HEIGHT - 2), radius=18, outline=CABINET_EDGE, width=3
        )
        draw.rounded_rectangle(field_box, radius=12, fill=theme.board)
        draw.rounded_rectangle(field_box, radius=12, outline=theme.accent, width=2)

        # Rótulo de neón: el color de contorno hace de halo.
        small = self._text("JOVANI VÁZQUEZ", 11, YELLOW, theme.halo, 1)
        image.paste(small, (CX - small.width // 2, 12), small)
        for side in (-1, 1):
            sx = CX + side * (small.width // 2 + 12)
            draw.polygon(_star(sx, 18, 6, 2.5), fill=YELLOW)
        big = self._text(theme.title, 26, WHITE, theme.halo, 3)
        image.paste(big, (CX - big.width // 2, 24), big)

        # Marco de la pantalla.
        x0, y0, x1, y1 = LCD_BOX
        draw.rounded_rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), radius=10, fill=theme.accent)
        draw.rounded_rectangle((x0 - 3, y0 - 3, x1 + 3, y1 + 3), radius=8, fill=(30, 30, 40))
        draw.rectangle(LCD_BOX, fill=theme.lcd)

        # Campo de clavos: el triángulo por el que bajan las bolas y clavos de
        # adorno alrededor, como en un tablero de verdad.
        dx = layout.dx
        for row in range(layout.rows):
            y = PIN_TOP + row * layout.dy
            for j in range(row + 1):
                x = layout.pin_x(row, j)
                draw.ellipse((x - PIN_R, y - PIN_R, x + PIN_R, y + PIN_R), fill=PIN)
            half = (row / 2 + 1) * dx
            x = 26 + (dx / 2 if row % 2 else 0)
            while x < WIDTH - 26:
                inside = abs(x - CX) < half
                near_deco = any(math.hypot(x - mx, y - my) < 24 for mx, my in DECORATIONS)
                if not inside and not near_deco:
                    draw.ellipse((x - PIN_R, y - PIN_R, x + PIN_R, y + PIN_R), fill=PIN)
                x += dx
        for mx, my in DECORATIONS:
            draw.ellipse((mx - 18, my - 18, mx + 18, my + 18), outline=theme.accent, width=2)

        # Bolsillos con su forma y, debajo, su valor.
        paying = sorted({v for v in board.pockets if v}, reverse=True)
        for pocket in range(board.rows + 1):
            self._draw_pocket(image, draw, board, theme, layout, paying, pocket, lit=lit)
        draw.text(
            (CX, layout.tray_top + 16),
            "BOLAS",
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
        if pocket == board.start_pocket:
            fill, shape = START_STYLE
        elif value:
            fill, shape = POCKET_RANKS[min(paying.index(value), len(POCKET_RANKS) - 1)]
        else:
            fill, shape = OUT_STYLE
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
        size = BALL_R * 2 + 1
        sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        draw.ellipse((0, 0, size - 1, size - 1), fill=(175, 180, 195), outline=(90, 90, 110))
        draw.ellipse((2, 2, 5, 5), fill=WHITE)
        return sprite

    def _decoration(self, theme: Theme, step: int) -> Image.Image:
        """Adorno de los lados en el paso `step` (0-3) de su animación."""
        size = 33
        sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        c = size / 2
        if theme.decoration == "flower":
            # Flor de cerezo de cinco pétalos que gira despacio.
            for petal in range(5):
                a = math.radians(step * 18 + petal * 72)
                px, py = c + 9 * math.cos(a), c + 9 * math.sin(a)
                draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=(255, 170, 215))
                draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=WHITE)
            draw.ellipse((c - 4, c - 4, c + 4, c + 4), fill=YELLOW)
        elif theme.decoration == "pearl":
            # Perla del dragón con tres comas (tomoe) que dan vueltas.
            draw.ellipse((c - 14, c - 14, c + 14, c + 14), fill=(20, 60, 70), outline=theme.accent)
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
                fill=(255, 110, 20),
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

    def _bulbs_layer(self, draw: ImageDraw.ImageDraw, theme: Theme, frame: int, mode: str) -> None:
        """Bombillas del borde: persiguen, corren en un reach y se vuelven locas en un atari."""
        for i, (x, y) in enumerate(self._bulbs):
            if mode == "atari":
                color = RAINBOW[(i + frame) % len(RAINBOW)]
            elif mode == "reach":
                color = MAGENTA if (i + frame) % 2 == 0 else theme.bulb_on
            else:
                color = theme.bulb_on if (i + frame // 2) % 4 == 0 else theme.bulb_off
            draw.ellipse((x - BULB_R, y - BULB_R, x + BULB_R, y + BULB_R), fill=color)

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
            draw.rectangle(LCD_BOX, fill=assets.theme.lcd)
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
            bg = LCD_ATARI if (frame // 2) % 2 or final else (160, 110, 0)
        elif reach_on:
            bg = LCD_REACH if (frame // 2) % 2 else (60, 0, 70)
        else:
            bg = assets.theme.lcd
        draw.rectangle(LCD_BOX, fill=bg)
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

    def _hold_dots(self, draw: ImageDraw.ImageDraw, held: int) -> None:
        """Reserva: puntos llenos (tiradas pendientes) y huecos (vacíos)."""
        for i in range(MAX_HOLD):
            x = CX + (i - (MAX_HOLD - 1) / 2) * 16
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
                x = layout.pocket_x(ball.pocket)
                box = (
                    round(x - layout.dx / 2),
                    round(layout.pocket_top) - 1,
                    round(x + layout.dx / 2),
                    round(layout.pocket_top + POCKET_H) + 1,
                )
                image.paste(assets.lit.crop(box), box[:2])

        self._decorate(assets, image, frame // 2)

        for index, ball in enumerate(volley.balls):
            position = layout.ball_position(ball, frame - index * LAUNCH_GAP)
            if position is not None:
                self._paste(image, self._ball, *position)

        slot = timeline.slot_at(frame)
        mode = self._lcd(assets, image, frame, slot, rng)
        if mode == "atari":
            # Lluvia de bolas de la compuerta abierta.
            for _ in range(10):
                x = rng.randint(30, WIDTH - 30)
                y = rng.randint(LCD_BOX[3] + 10, round(layout.pocket_top) - 6)
                self._paste(image, self._ball, x, y)
        self._bulbs_layer(draw, assets.theme, frame, mode)
        self._hold_dots(draw, timeline.held(frame))

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
            draw.ellipse((x - r, y - r, x + r, y + r), fill=WHITE, outline=assets.theme.board)
            badge = self._text(str(count), 11, assets.theme.board, WHITE, 0)
            self._paste(image, badge, x, y)
        best = volley.best
        slot = None
        if timeline.slots:
            slot = next((s for s in timeline.slots if s.draw is best), timeline.slots[-1])
        mode = self._lcd(assets, image, timeline.frames, slot, random.Random(0), final=True)
        self._bulbs_layer(draw, assets.theme, 0, "atari" if mode == "atari" else "idle")
        self._hold_dots(draw, 0)
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
        self._bulbs_layer(draw, assets.theme, 0, "idle")
        self._hold_dots(draw, 0)
        self._tray(assets.layout, image, 0)
        return self._png(self._quantize(assets, image))

    def still_png(self, volley: Volley) -> bytes:
        """PNG del final de una tanda, sin animación (Ráfaga)."""
        assets = self.assets(volley.board)
        final = self._final(assets, volley, build_timeline(volley))
        return self._png(self._quantize(assets, final))

    def warm_up(self) -> None:
        """Prepara las piezas de todos los tableros (al cargar el cog)."""
        for board in BOARDS.values():
            self.assets(board)

    def render(self, volley: Volley, *, turbo: bool = False) -> PachinkoMedia:
        """Animación y PNG final de una tanda.

        Args:
            turbo: Solo el PNG final, sin GIF (más rápido y casi sin datos).
        """
        assets = self.assets(volley.board)
        timeline = build_timeline(volley)
        final = self._quantize(assets, self._final(assets, volley, timeline))
        if turbo:
            return PachinkoMedia(gif=b"", png=self._png(final), seconds=0.0)
        # Los números que pasan girando son de adorno; con una semilla fija
        # por tanda, la misma tanda se dibuja siempre igual.
        rng = random.Random(hash(tuple(ball.path for ball in volley.balls)))
        frames = [
            self._quantize(assets, self._frame(assets, volley, timeline, index, rng))
            for index in range(timeline.frames)
        ]
        frames.append(final)
        buffer = io.BytesIO()
        durations = [FRAME_MS] * (len(frames) - 1) + [FINAL_FRAME_MS]
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            disposal=1,
        )
        seconds = FRAME_MS * (len(frames) - 1) / 1000
        return PachinkoMedia(gif=buffer.getvalue(), png=self._png(final), seconds=seconds)
