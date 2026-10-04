"""Animación del pachinko: un GIF por tanda y una imagen fija final.

La máquina imita las de los salones japoneses: un mueble con bombillas que
persiguen, un rótulo de neón, una pantalla con el sorteo, un campo de clavos
con dos molinillos y los bolsillos abajo. Una tanda cuesta ~0,3 s de CPU
(fuera del event loop) y unos cientos de KB de GIF. El modo turbo solo manda
la imagen final.

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
    FEVER_BALLS,
    MAX_HOLD,
    POCKETS,
    ROWS,
    START_POCKET,
    Ball,
    Draw,
    Kind,
    Volley,
    pocket_label,
)

FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
)

# -- Geometría ----------------------------------------------------------------------

WIDTH = 340
HEIGHT = 500
CX = WIDTH // 2
#: Separación horizontal entre clavos y entre bolsillos.
DX = 28
#: Altura de la primera fila de clavos y distancia entre filas.
PIN_TOP = 190
DY = 18
PIN_R = 2
BALL_R = 5
#: Donde aparece cada bola, encima del primer clavo.
ENTRY_Y = 168
POCKET_TOP = PIN_TOP + DY * ROWS + 4
POCKET_H = 36
LABEL_Y = POCKET_TOP + POCKET_H + 3
TRAY_TOP = LABEL_Y + 18
LCD_BOX = (70, 64, 270, 148)
HOLD_Y = 162
WINDMILLS = ((44, 232), (296, 232))
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
BALL_FRAMES = ENTRY_FRAMES + ROWS * ROW_FRAMES + EXIT_FRAMES
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
CABINET = (34, 16, 64)
CABINET_EDGE = (205, 205, 225)
BOARD = (22, 12, 44)
GOLD = (255, 200, 60)
MAGENTA = (255, 60, 200)
CYAN = (60, 230, 255)
YELLOW = (255, 240, 90)
WHITE = (255, 255, 255)
BLUE = (70, 120, 255)
PIN = (215, 215, 235)
LCD_BG = (8, 18, 62)
LCD_REACH = (88, 0, 92)
LCD_ATARI = (120, 78, 0)
BULB_OFF = (88, 66, 40)
BULB_ON = (255, 232, 120)
OUT_FILL = (52, 50, 66)
RAINBOW = (MAGENTA, YELLOW, CYAN, WHITE, GOLD, BLUE)
PALETTE_COLORS = 64

#: Relleno, forma y color de la etiqueta de cada valor de bolsillo.
POCKET_STYLE: dict[int, tuple[tuple[int, int, int], str]] = {
    10: (GOLD, "star"),
    3: (CYAN, "diamond"),
    1: (BLUE, "circle"),
    0: (OUT_FILL, "cross"),
}
START_STYLE = (MAGENTA, "tulip")


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


def pin_x(row: int, rights: int) -> float:
    """Horizontal del clavo que golpea una bola en `row` tras `rights` derechas."""
    return CX + (rights - row / 2) * DX


def pocket_x(pocket: int) -> float:
    """Centro del bolsillo `pocket`."""
    return CX + (pocket - ROWS / 2) * DX


def ball_position(ball: Ball, frame: float) -> tuple[float, float] | None:
    """Dónde está una bola `frame` fotogramas después de lanzarla.

    Devuelve `None` antes de lanzarla y después de entrar en el bolsillo.
    Entre dos filas avanza en línea recta con un saltito (`HOP`), que es lo
    que hace que parezca que rebota en el clavo.
    """
    if frame < 0 or frame >= BALL_FRAMES:
        return None
    if frame < ENTRY_FRAMES:
        t = frame / ENTRY_FRAMES
        return CX, ENTRY_Y + (PIN_TOP - BALL_R - ENTRY_Y) * t * t
    frame -= ENTRY_FRAMES
    if frame < ROWS * ROW_FRAMES:
        row, sub = divmod(frame, ROW_FRAMES)
        row = int(row)
        t = sub / ROW_FRAMES
        rights = sum(ball.path[:row])
        x0 = pin_x(row, rights)
        x1 = x0 + (DX / 2 if ball.path[row] else -DX / 2)
        y0 = PIN_TOP + row * DY - BALL_R
        y1 = y0 + DY
        return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t - HOP * math.sin(math.pi * t)
    t = (frame - ROWS * ROW_FRAMES) / EXIT_FRAMES
    x = pocket_x(ball.pocket)
    y0 = PIN_TOP + ROWS * DY - BALL_R
    return x, y0 + (POCKET_TOP + 10 - y0) * t


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
    landings = tuple(index * LAUNCH_GAP + BALL_FRAMES for index in range(len(volley.balls)))
    start_landings = [
        landing
        for ball, landing in zip(volley.balls, landings, strict=True)
        if ball.pocket == START_POCKET
    ]
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


class PachinkoRenderer:
    """Dibuja las tandas del pachinko con piezas precalculadas.

    Es seguro llamarlo desde varios hilos: las piezas se preparan una vez con
    un cerrojo y después solo se leen (las cachés de textos se rellenan bajo el
    mismo cerrojo).
    """

    def __init__(self, font_path: Path = FONT_PATH) -> None:
        self._font_path = font_path
        self._lock = threading.RLock()
        self._ready = False
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._texts: dict[tuple, Image.Image] = {}

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
        fill: tuple[int, int, int],
        stroke: tuple[int, int, int] = (0, 0, 0),
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

    def _prepare(self) -> None:
        with self._lock:
            if self._ready:
                return
            self._bulbs = _bulb_positions()
            self._lcd_bulbs = _lcd_bulb_positions()
            self._board = self._draw_board(lit=False)
            self._board_lit = self._draw_board(lit=True)
            self._ball = self._ball_sprite()
            self._windmills = [self._windmill_sprite(angle) for angle in (0, 22, 45, 67)]
            sample = Image.new("RGB", (WIDTH * 2, HEIGHT))
            sample.paste(self._board, (0, 0))
            sample.paste(self._board_lit, (WIDTH, 0))
            # Los colores de los carteles y de la pantalla también tienen que
            # estar en la paleta aunque no salgan en el tablero.
            swatch = ImageDraw.Draw(sample)
            for i, color in enumerate((*RAINBOW, LCD_REACH, LCD_ATARI, BULB_ON, BULB_OFF)):
                swatch.rectangle((i * 8, 0, i * 8 + 7, 7), fill=color)
            self._palette = sample.quantize(colors=PALETTE_COLORS, method=Image.Quantize.MEDIANCUT)
            self._ready = True

    def _draw_board(self, *, lit: bool) -> Image.Image:
        """Mueble, rótulo, marco de la pantalla, clavos, molinillos fijos y bolsillos.

        Con `lit`, todos los bolsillos están encendidos; de ahí se recorta el
        que destella cuando entra una bola.
        """
        image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, WIDTH - 2, HEIGHT - 2), radius=18, fill=CABINET)
        draw.rounded_rectangle(
            (1, 1, WIDTH - 2, HEIGHT - 2), radius=18, outline=CABINET_EDGE, width=3
        )
        draw.rounded_rectangle((16, 54, WIDTH - 17, TRAY_TOP - 2), radius=12, fill=BOARD)
        draw.rounded_rectangle((16, 54, WIDTH - 17, TRAY_TOP - 2), radius=12, outline=GOLD, width=2)

        # Rótulo de neón: el color de contorno hace de halo.
        small = self._text("JOVANI VÁZQUEZ", 11, YELLOW, MAGENTA, 1)
        image.paste(small, (CX - small.width // 2, 12), small)
        for side in (-1, 1):
            sx = CX + side * (small.width // 2 + 12)
            draw.polygon(_star(sx, 18, 6, 2.5), fill=YELLOW)
        big = self._text("PACHINKO", 26, WHITE, MAGENTA, 3)
        image.paste(big, (CX - big.width // 2, 24), big)

        # Marco de la pantalla.
        x0, y0, x1, y1 = LCD_BOX
        draw.rounded_rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), radius=10, fill=GOLD)
        draw.rounded_rectangle((x0 - 3, y0 - 3, x1 + 3, y1 + 3), radius=8, fill=(30, 30, 40))
        draw.rectangle(LCD_BOX, fill=LCD_BG)

        # Campo de clavos: el triángulo por el que bajan las bolas y clavos de
        # adorno alrededor, como en un tablero de verdad.
        for row in range(ROWS):
            y = PIN_TOP + row * DY
            for j in range(row + 1):
                x = pin_x(row, j)
                draw.ellipse((x - PIN_R, y - PIN_R, x + PIN_R, y + PIN_R), fill=PIN)
            half = (row / 2 + 1) * DX
            offset = DX / 2 if row % 2 else 0
            x = 26 + offset
            while x < WIDTH - 26:
                inside = abs(x - CX) < half
                near_mill = any(math.hypot(x - mx, y - my) < 24 for mx, my in WINDMILLS)
                if not inside and not near_mill:
                    draw.ellipse((x - PIN_R, y - PIN_R, x + PIN_R, y + PIN_R), fill=PIN)
                x += DX
        for mx, my in WINDMILLS:
            draw.ellipse((mx - 18, my - 18, mx + 18, my + 18), outline=GOLD, width=2)

        # Bolsillos con su forma y, debajo, su valor.
        for pocket in range(ROWS + 1):
            self._draw_pocket(image, draw, pocket, lit=lit)
        draw.text((CX, TRAY_TOP + 16), "BOLAS", font=self._font(10), fill=GOLD, anchor="mm")
        return image

    def _draw_pocket(
        self, image: Image.Image, draw: ImageDraw.ImageDraw, pocket: int, *, lit: bool
    ) -> None:
        if pocket == START_POCKET:
            fill, shape = START_STYLE
        else:
            fill, shape = POCKET_STYLE[POCKETS[pocket]]
        x = pocket_x(pocket)
        box = (x - DX / 2 + 2, POCKET_TOP, x + DX / 2 - 2, POCKET_TOP + POCKET_H)
        body = WHITE if lit else fill
        draw.rounded_rectangle(box, radius=5, fill=body, outline=GOLD if lit else PIN, width=1)
        mark = fill if lit else BOARD
        cy = POCKET_TOP + POCKET_H * 0.62
        if shape == "star":
            draw.polygon(_star(x, cy, 8, 3.5), fill=mark)
        elif shape == "diamond":
            draw.polygon([(x, cy - 8), (x + 6, cy), (x, cy + 8), (x - 6, cy)], fill=mark)
        elif shape == "circle":
            draw.ellipse((x - 5, cy - 5, x + 5, cy + 5), fill=mark)
        elif shape == "cross":
            draw.line((x - 5, cy - 5, x + 5, cy + 5), fill=PIN, width=2)
            draw.line((x - 5, cy + 5, x + 5, cy - 5), fill=PIN, width=2)
        else:  # tulipán de la ranura START
            draw.polygon(
                [(x - 8, cy - 8), (x - 4, cy - 2), (x, cy - 9), (x + 4, cy - 2), (x + 8, cy - 8),
                 (x + 6, cy + 5), (x - 6, cy + 5)],
                fill=mark,
            )  # fmt: skip
        label_color = MAGENTA if pocket == START_POCKET else (WHITE if POCKETS[pocket] else PIN)
        label = self._text(pocket_label(pocket), 10, label_color, (0, 0, 0), 1)
        image.paste(label, (round(x - label.width / 2), LABEL_Y), label)

    def _ball_sprite(self) -> Image.Image:
        size = BALL_R * 2 + 1
        sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        draw.ellipse((0, 0, size - 1, size - 1), fill=(175, 180, 195), outline=(90, 90, 110))
        draw.ellipse((2, 2, 5, 5), fill=WHITE)
        return sprite

    def _windmill_sprite(self, angle: float) -> Image.Image:
        """Molinillo de cuatro aspas girado `angle` grados."""
        size = 33
        sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        c = size / 2
        for blade in range(4):
            a = math.radians(angle + blade * 90)
            tip = (c + 14 * math.cos(a), c + 14 * math.sin(a))
            side = (c + 9 * math.cos(a + 0.5), c + 9 * math.sin(a + 0.5))
            draw.polygon([(c, c), side, tip], fill=CYAN if blade % 2 else MAGENTA)
        draw.ellipse((c - 3, c - 3, c + 3, c + 3), fill=GOLD)
        return sprite

    # -- Fotogramas -----------------------------------------------------------------

    def _paste(self, frame: Image.Image, sprite: Image.Image, cx: float, cy: float) -> None:
        frame.paste(sprite, (round(cx - sprite.width / 2), round(cy - sprite.height / 2)), sprite)

    def _bulbs_layer(self, draw: ImageDraw.ImageDraw, frame: int, mode: str) -> None:
        """Bombillas del borde: persiguen, corren en un reach y se vuelven locas en un atari."""
        for i, (x, y) in enumerate(self._bulbs + self._lcd_bulbs):
            if mode == "atari":
                color = RAINBOW[(i + frame) % len(RAINBOW)]
            elif mode == "reach":
                color = MAGENTA if (i + frame) % 2 == 0 else BULB_ON
            else:
                color = BULB_ON if (i + frame // 2) % 4 == 0 else BULB_OFF
            draw.ellipse((x - BULB_R, y - BULB_R, x + BULB_R, y + BULB_R), fill=color)

    def _digit(self, value: int, color: tuple[int, int, int], blur: bool = False) -> Image.Image:
        sprite = self._text(str(value), 46, color, (0, 0, 0), 3)
        if blur:
            # Número girando: se dibuja estirado y más bajo, sin calcular un desenfoque.
            return sprite.resize((sprite.width, sprite.height + 14))
        return sprite

    def _lcd(
        self,
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
            draw.rectangle(LCD_BOX, fill=LCD_BG)
            for cx in centers:
                self._paste(frame_image, self._digit(7, (90, 110, 170)), cx, mid_y)
            tag = self._text("¡DALE!", 12, YELLOW, (0, 0, 0), 2)
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
            bg = LCD_BG
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
                banner = self._text(f"FEVER +{FEVER_BALLS}", 18, YELLOW, MAGENTA, 3)
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

    def _tray(self, frame_image: Image.Image, balls: int, *, final: bool = False) -> None:
        text = f"+{balls}" if final else str(balls)
        color = GOLD if balls >= BALLS else WHITE
        sprite = self._text(text, 16, color, (0, 0, 0), 2)
        frame_image.paste(sprite, (CX + 26, TRAY_TOP + 16 - sprite.height // 2), sprite)

    def _frame(
        self, volley: Volley, timeline: Timeline, frame: int, rng: random.Random
    ) -> Image.Image:
        image = self._board.copy()
        draw = ImageDraw.Draw(image)

        # Bolsillos que destellan porque acaba de entrar una bola.
        for ball, landing in zip(volley.balls, timeline.landings, strict=True):
            if landing <= frame < landing + POCKET_FLASH and (frame - landing) % 2 == 0:
                x = pocket_x(ball.pocket)
                box = (
                    round(x - DX / 2),
                    POCKET_TOP - 1,
                    round(x + DX / 2),
                    POCKET_TOP + POCKET_H + 1,
                )
                image.paste(self._board_lit.crop(box), box[:2])

        mill = self._windmills[(frame // 2) % len(self._windmills)]
        for mx, my in WINDMILLS:
            self._paste(image, mill, mx, my)

        for index, ball in enumerate(volley.balls):
            position = ball_position(ball, frame - index * LAUNCH_GAP)
            if position is not None:
                self._paste(image, self._ball, *position)

        slot = timeline.slot_at(frame)
        mode = self._lcd(image, frame, slot, rng)
        if mode == "atari":
            # Lluvia de bolas de la compuerta abierta.
            for _ in range(10):
                x = rng.randint(30, WIDTH - 30)
                y = rng.randint(LCD_BOX[3] + 10, POCKET_TOP - 6)
                self._paste(image, self._ball, x, y)
        self._bulbs_layer(draw, frame, mode)
        self._hold_dots(draw, timeline.held(frame))

        balls = sum(
            ball.returned
            for ball, landing in zip(volley.balls, timeline.landings, strict=True)
            if landing <= frame
        )
        balls += FEVER_BALLS * sum(s.jackpots_at(frame) for s in timeline.slots)
        self._tray(image, balls)
        return image

    def _final(self, volley: Volley, timeline: Timeline) -> Image.Image:
        """Imagen final: bolas contadas en cada bolsillo y la pantalla en el último sorteo."""
        image = self._board.copy()
        draw = ImageDraw.Draw(image)
        for mx, my in WINDMILLS:
            self._paste(image, self._windmills[0], mx, my)
        for pocket, count in enumerate(volley.pocket_counts()):
            if not count:
                continue
            x = pocket_x(pocket)
            y = POCKET_TOP + 9
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=WHITE, outline=BOARD)
            badge = self._text(str(count), 11, BOARD, WHITE, 0)
            self._paste(image, badge, x, y)
        best = volley.best
        slot = None
        if timeline.slots:
            slot = next((s for s in timeline.slots if s.draw is best), timeline.slots[-1])
        mode = self._lcd(image, timeline.frames, slot, random.Random(0), final=True)
        self._bulbs_layer(draw, 0, "atari" if mode == "atari" else "idle")
        self._hold_dots(draw, 0)
        self._tray(image, volley.total_balls, final=True)
        return image

    def _quantize(self, image: Image.Image) -> Image.Image:
        return image.quantize(palette=self._palette, dither=Image.Dither.NONE)

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def idle_png(self) -> bytes:
        """La máquina parada, al abrirla."""
        self._prepare()
        image = self._board.copy()
        for mx, my in WINDMILLS:
            self._paste(image, self._windmills[0], mx, my)
        self._lcd(image, 0, None, random.Random(0))
        draw = ImageDraw.Draw(image)
        self._bulbs_layer(draw, 0, "idle")
        self._hold_dots(draw, 0)
        self._tray(image, 0)
        return self._png(self._quantize(image))

    def still_png(self, volley: Volley) -> bytes:
        """PNG del final de una tanda, sin animación (Ráfaga)."""
        self._prepare()
        return self._png(self._quantize(self._final(volley, build_timeline(volley))))

    def render(self, volley: Volley, *, turbo: bool = False) -> PachinkoMedia:
        """Animación y PNG final de una tanda.

        Args:
            turbo: Solo el PNG final, sin GIF (más rápido y casi sin datos).
        """
        self._prepare()
        timeline = build_timeline(volley)
        final = self._quantize(self._final(volley, timeline))
        if turbo:
            return PachinkoMedia(gif=b"", png=self._png(final), seconds=0.0)
        # Los números que pasan girando son de adorno; con una semilla fija
        # por tanda, la misma tanda se dibuja siempre igual.
        rng = random.Random(hash(tuple(ball.path for ball in volley.balls)))
        frames = [
            self._quantize(self._frame(volley, timeline, index, rng))
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
