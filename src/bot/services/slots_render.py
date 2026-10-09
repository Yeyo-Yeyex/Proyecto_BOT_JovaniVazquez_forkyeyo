"""Animación de la tragaperras: un GIF por tirada y una imagen fija final.

La ruleta tiene 38 resultados y prerrenderiza un GIF por casilla. Aquí hay
unas 29.000 combinaciones de rodillos, así que no se puede: el GIF se monta
en cada tirada. Para que sea barato, todo lo caro se hace una sola vez al
crear el renderizador:

- Cada símbolo se dibuja ya cuantizado a una paleta fija común, en versión
  nítida y en versión desenfocada (la que se ve con el rodillo a toda
  velocidad). Montar un fotograma es pegar recortes de índices de paleta, sin
  volver a calcular colores.
- El marco de la máquina (fondo, ventanas y flechas de la línea) también
  está ya cuantizado.

Una tirada cuesta ~0,05 s de CPU y ~110 KB de GIF, que escribe
`bot.utils.gif.shared_palette_gif`. El modo turbo se salta el GIF y manda
solo la imagen final (~15 KB).

La animación:

1. Los tres rodillos arrancan a la vez y paran de izquierda a derecha, con
   un pequeño rebote al frenar.
2. Si los dos primeros prometen algo gordo (`Spin.anticipation`), el
   tercero sigue girando más tiempo, frena despacio y su ventana se ilumina.
3. Si la línea paga, parpadea un marco sobre ella.

El último fotograma se queda quieto mucho tiempo y es idéntico al PNG final:
el bot cambia el GIF por el PNG al acabar, así el mensaje deja de animarse
aunque el cliente repita el GIF en bucle (lo mismo que hace la ruleta).

Los símbolos son imágenes de `assets/slots` (ver su `LICENSE.txt`). Se
distinguen por la forma y no solo por el color, también con deuteranopia.
"""

from __future__ import annotations

import io
import math
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from bot.services.slots import (
    BELL,
    CHERRY,
    DIAMOND,
    GRAPE,
    LEMON,
    REEL_STRIPS,
    SCATTER,
    SEVEN,
    WILD,
    Spin,
)
from bot.utils.gif import shared_palette_gif

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "slots"
SYMBOL_FILES = {
    CHERRY: "cherry.png",
    LEMON: "lemon.png",
    GRAPE: "grape.png",
    BELL: "bell.png",
    DIAMOND: "diamond.png",
    SEVEN: "seven.png",
    WILD: "wild.png",
    SCATTER: "scatter.png",
}

#: Celda de un símbolo dentro del rodillo, en píxeles.
CELL_W = 64
CELL_H = 58
#: Lado del símbolo dentro de la celda.
SYMBOL = 50
ROWS = 3
GAP = 8
MARGIN = 16
WINDOW_H = CELL_H * ROWS
WIDTH = MARGIN * 2 + CELL_W * 3 + GAP * 2
HEIGHT = MARGIN * 2 + WINDOW_H

FRAME_MS = 70
#: Fotograma en el que para cada rodillo (normal y con anticipación).
STOP_FRAMES = (10, 14, 18)
ANTICIPATION_STOP = 32
#: Celdas que recorre cada rodillo antes de parar: el tercero, más.
TRAVEL_CELLS = (10, 13, 16)
ANTICIPATION_TRAVEL = 24
#: Lo que se pasa de largo antes del rebote, en celdas.
OVERSHOOT = 0.18
BOUNCE_FRAMES = 3
#: Radio del desenfoque de los símbolos con el rodillo a toda velocidad.
BLUR_PIXELS = 5
#: Colores de la paleta común. Menos colores, GIF más pequeño.
PALETTE_COLORS = 64
#: Por encima de esta velocidad (celdas por fotograma) se pinta desenfocado.
BLUR_SPEED = 0.45
#: Parpadeos del marco de la línea cuando paga (cada uno, encendido y apagado).
FLASHES = 3
FLASH_FRAMES = 2
FINAL_FRAME_MS = 60_000

BACKGROUND = (43, 45, 49)  # gris de los embeds en el tema oscuro
CABINET = (28, 29, 33)
REEL_BG = (236, 233, 224)
REEL_SHADE = (205, 200, 188)
GOLD = (222, 178, 70)
HIGHLIGHT = (255, 214, 92)
GLOW = (255, 245, 200)
DARK_LINE = (60, 60, 66)


@dataclass(frozen=True, slots=True)
class SlotsMedia:
    """Imágenes de una tirada.

    Attributes:
        gif: Animación del giro; vacía en modo turbo.
        png: Imagen fija final, igual que el último fotograma del GIF.
        seconds: Lo que dura la animación hasta el último fotograma.
    """

    gif: bytes
    png: bytes
    seconds: float


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def reel_position(stop: int, frame: int, stop_frame: int, travel: float) -> float:
    """Posición (en celdas de la tira) de un rodillo en un fotograma.

    Los símbolos bajan: la posición decrece hasta `stop`, se pasa un poco
    (`OVERSHOOT`) y vuelve en `BOUNCE_FRAMES` fotogramas.
    """
    main_end = stop_frame - BOUNCE_FRAMES
    if frame >= stop_frame:
        return float(stop)
    if frame >= main_end:
        t = (frame - main_end) / BOUNCE_FRAMES
        return stop - OVERSHOOT * (1 - t)
    t = frame / main_end
    return stop + (travel + OVERSHOOT) * (1 - _ease_out(t)) - OVERSHOOT


class SlotsRenderer:
    """Dibuja las tiradas de la tragaperras con piezas precalculadas.

    Es seguro llamarlo desde varios hilos: las piezas se preparan una vez con
    un cerrojo y después solo se leen.
    """

    def __init__(self, assets: Path = ASSETS) -> None:
        self._assets = assets
        self._lock = threading.Lock()
        self._ready = False

    # -- Piezas ---------------------------------------------------------------------

    def _prepare(self) -> None:
        with self._lock:
            if self._ready:
                return
            symbols = {key: self._load(name) for key, name in SYMBOL_FILES.items()}
            sharp = {key: self._cell(img) for key, img in symbols.items()}
            blurred = {key: _vertical_blur(cell, BLUR_PIXELS) for key, cell in sharp.items()}
            frame = self._frame()
            glow = self._frame(glow=True)
            highlight = self._highlight_layer()

            # La paleta sale de todas las piezas juntas: así cada una cabe sin
            # perder colores y todas comparten índices.
            sample = Image.new("RGB", (WIDTH + CELL_W * 2, max(HEIGHT * 2, CELL_H * 8)))
            sample.paste(frame, (0, 0))
            sample.paste(glow, (0, HEIGHT))
            sample.paste(highlight.convert("RGB"), (0, 0), highlight)
            for index, key in enumerate(SYMBOL_FILES):
                sample.paste(sharp[key], (WIDTH, index * CELL_H))
                sample.paste(blurred[key], (WIDTH + CELL_W, index * CELL_H))
            palette = sample.quantize(colors=PALETTE_COLORS, method=Image.Quantize.MEDIANCUT)

            def q(image: Image.Image) -> Image.Image:
                return image.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)

            self._palette = palette
            self._sharp = {key: q(cell) for key, cell in sharp.items()}
            self._blurred = {key: q(cell) for key, cell in blurred.items()}
            self._frame_p = q(frame)
            self._glow_p = q(glow)
            self._highlight = highlight
            self._ready = True

    def _load(self, name: str) -> Image.Image:
        image = Image.open(self._assets / name).convert("RGBA")
        if image.size != (SYMBOL, SYMBOL):
            image = image.resize((SYMBOL, SYMBOL), Image.Resampling.LANCZOS)
        return image

    @staticmethod
    def _cell(symbol: Image.Image) -> Image.Image:
        """Celda del rodillo con su símbolo centrado y una línea de separación."""
        cell = Image.new("RGB", (CELL_W, CELL_H), REEL_BG)
        draw = ImageDraw.Draw(cell)
        draw.line((0, CELL_H - 1, CELL_W, CELL_H - 1), fill=REEL_SHADE)
        cell.paste(symbol, ((CELL_W - SYMBOL) // 2, (CELL_H - SYMBOL) // 2), symbol)
        return cell

    @staticmethod
    def reel_x(reel: int) -> int:
        """Borde izquierdo de la ventana de un rodillo."""
        return MARGIN + reel * (CELL_W + GAP)

    def _frame(self, *, glow: bool = False) -> Image.Image:
        """Marco de la máquina: fondo, ventanas y flechas de la línea.

        Con `glow`, la ventana del tercer rodillo está iluminada (anticipación).
        """
        image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, WIDTH - 3, HEIGHT - 3), radius=14, fill=CABINET)
        draw.rounded_rectangle((2, 2, WIDTH - 3, HEIGHT - 3), radius=14, outline=GOLD, width=3)
        for reel in range(3):
            x = self.reel_x(reel)
            box = (x - 3, MARGIN - 3, x + CELL_W + 2, MARGIN + WINDOW_H + 2)
            color = GLOW if glow and reel == 2 else DARK_LINE
            draw.rectangle(box, outline=color, width=3)
        # Flechas de la línea de pago, fuera de las ventanas.
        mid = MARGIN + CELL_H * 1.5
        size = 7
        draw.polygon([(3, mid - size), (3 + size + 2, mid), (3, mid + size)], fill=GOLD)
        right = WIDTH - 4
        draw.polygon([(right, mid - size), (right - size - 2, mid), (right, mid + size)], fill=GOLD)
        return image

    def _highlight_layer(self) -> Image.Image:
        """Marco dorado sobre la línea de pago (capa con transparencia)."""
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        top = MARGIN + CELL_H
        draw.rounded_rectangle(
            (MARGIN - 6, top - 2, WIDTH - MARGIN + 5, top + CELL_H + 1),
            radius=8,
            outline=HIGHLIGHT,
            width=4,
        )
        return layer

    # -- Fotogramas -----------------------------------------------------------------

    def _reel_column(self, reel: int, position: float, blurred: bool) -> Image.Image:
        """Ventana de un rodillo con la tira en `position` (celda en la línea)."""
        strip = REEL_STRIPS[reel]
        cells = self._blurred if blurred else self._sharp
        column = Image.new("P", (CELL_W, WINDOW_H))
        base = math.floor(position)
        shift = (position - base) * CELL_H
        # La celda de la línea es la fila 1; arriba van las anteriores de la tira.
        for row in range(-1, ROWS + 1):
            symbol = strip[(base + row - 1) % len(strip)]
            y = round(row * CELL_H - shift)
            if -CELL_H < y < WINDOW_H:
                column.paste(cells[symbol], (0, y))
        return column

    def _compose(
        self, positions: tuple[float, ...], speeds: tuple[float, ...], *, glow: bool
    ) -> Image.Image:
        frame = (self._glow_p if glow else self._frame_p).copy()
        for reel, (position, speed) in enumerate(zip(positions, speeds, strict=True)):
            column = self._reel_column(reel, position, speed > BLUR_SPEED)
            frame.paste(column, (self.reel_x(reel), MARGIN))
        return frame

    def _with_highlight(self, frame: Image.Image) -> Image.Image:
        rgb = frame.convert("RGB")
        rgb.paste(self._highlight.convert("RGB"), (0, 0), self._highlight)
        return rgb.quantize(palette=self._palette, dither=Image.Dither.NONE)

    def still(self, stops: tuple[int, int, int], *, highlight: bool = False) -> Image.Image:
        """Fotograma con los rodillos parados en `stops`."""
        self._prepare()
        frame = self._compose(tuple(float(s) for s in stops), (0.0, 0.0, 0.0), glow=False)
        return self._with_highlight(frame) if highlight else frame

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def still_png(self, stops: tuple[int, int, int], *, highlight: bool = False) -> bytes:
        """PNG de la máquina parada (al abrirla o en modo turbo)."""
        return self._png(self.still(stops, highlight=highlight))

    def render(self, spin: Spin, *, turbo: bool = False) -> SlotsMedia:
        """Animación y PNG final de una tirada.

        Args:
            turbo: Solo el PNG final, sin GIF (más rápido y casi sin datos).
        """
        self._prepare()
        wins = spin.pay_halves > 0 or spin.is_jackpot
        final = self.still(spin.stops, highlight=wins)
        if turbo:
            return SlotsMedia(gif=b"", png=self._png(final), seconds=0.0)

        stop_frames = list(STOP_FRAMES)
        travels = list(TRAVEL_CELLS)
        if spin.anticipation:
            stop_frames[2] = ANTICIPATION_STOP
            travels[2] = ANTICIPATION_TRAVEL
        last = stop_frames[2]

        frames: list[Image.Image] = []
        previous = tuple(
            reel_position(spin.stops[r], 0, stop_frames[r], travels[r]) for r in range(3)
        )
        for index in range(last + 1):
            positions = tuple(
                reel_position(spin.stops[r], index, stop_frames[r], travels[r]) for r in range(3)
            )
            speeds = tuple(abs(a - b) for a, b in zip(positions, previous, strict=True))
            previous = positions
            # La ventana del tercero se ilumina mientras se espera que pare.
            glow = spin.anticipation and stop_frames[1] <= index < last and index % 4 < 2
            frames.append(self._compose(positions, speeds, glow=glow))

        if wins:
            flashes = FLASHES * (2 if spin.is_jackpot else 1)
            plain = frames[-1]
            for _ in range(flashes):
                frames.extend([final] * FLASH_FRAMES)
                frames.extend([plain] * FLASH_FRAMES)
        frames.append(final)

        durations = [FRAME_MS] * (len(frames) - 1) + [FINAL_FRAME_MS]
        seconds = FRAME_MS * (len(frames) - 1) / 1000
        gif = shared_palette_gif(frames, durations)
        return SlotsMedia(gif=gif, png=self._png(final), seconds=seconds)


def _vertical_blur(image: Image.Image, radius: int) -> Image.Image:
    """Desenfoque solo vertical: el de un rodillo girando a toda velocidad.

    Media de `2·radius + 1` píxeles en vertical. Pillow no trae filtros
    direccionales de este tamaño, así que se hace con numpy (ya es dependencia).
    """
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    padded = np.pad(pixels, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    summed = np.cumsum(padded, axis=0)
    summed = np.concatenate([np.zeros_like(summed[:1]), summed], axis=0)
    window = 2 * radius + 1
    blurred = (summed[window:] - summed[:-window]) / window
    return Image.fromarray(blurred.clip(0, 255).astype(np.uint8), "RGB")
