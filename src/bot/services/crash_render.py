"""Gráfica del Crash: la curva del cohete con las retiradas y la explosión.

Se dibuja una sola vez por ronda, al explotar (~20 ms de CPU, ~15-25 KB de
PNG). Durante el vuelo el bot no manda imágenes: el multiplicador va en el
texto del mensaje, que cuesta mucho menos ancho de banda.

Lo que sale:

- La curva del multiplicador desde 1,00x hasta el punto de explosión, con
  relleno degradado debajo.
- Un punto blanco con el nombre y el multiplicador de cada jugador que se
  retiró a tiempo.
- Una estrella de explosión al final de la curva y el punto de explosión en
  grande arriba a la izquierda.

Los colores no cargan el significado solo: retirada = círculo con nombre;
explosión = estrella con picos. Se distinguen también con deuteranopia.
"""

from __future__ import annotations

import io
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.services.crash import format_multiplier, seconds_to

FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
)

WIDTH = 720
HEIGHT = 340
#: Se dibuja al doble y se reduce: curva y círculos sin dientes de sierra.
SCALE = 2
#: Márgenes del área de la gráfica.
LEFT, RIGHT, TOP, BOTTOM = 64, 40, 92, 40
#: Como mucho se rotulan tantas retiradas; el resto solo lleva su punto.
MAX_LABELS = 8

BACKGROUND = (24, 25, 28)
GRID = (44, 46, 52)
AXIS_TEXT = (120, 124, 134)
CURVE = (255, 196, 0)
CURVE_FILL = (255, 196, 0)
BOOM = (255, 128, 48)
TEXT = (238, 240, 244)
MUTED = (150, 154, 164)
LABEL_BG = (52, 55, 62)


@dataclass(frozen=True, slots=True)
class CashoutMark:
    """Una retirada que pintar: quién y en qué multiplicador (centésimas)."""

    name: str
    cents: int


class CrashRenderer:
    """Dibuja la gráfica final de una ronda; seguro para usar desde varios hilos."""

    def __init__(self, font_path: Path = FONT_PATH) -> None:
        self._font_path = font_path
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._lock = threading.Lock()

    def render(
        self, *, crash_cents: int, cashouts: Sequence[CashoutMark], subtitle: str = ""
    ) -> bytes:
        """PNG de la ronda terminada.

        Args:
            crash_cents: Punto de explosión.
            cashouts: Retiradas a tiempo.
            subtitle: Línea pequeña bajo el multiplicador (p. ej. `4 jugadores`).
        """
        s = SCALE
        image = Image.new("RGB", (WIDTH * s, HEIGHT * s), BACKGROUND)
        draw = ImageDraw.Draw(image, "RGBA")

        top_cents = max(200, math.ceil(crash_cents * 1.12))
        duration = max(seconds_to(crash_cents), 0.5)
        x0, x1 = LEFT * s, (WIDTH - RIGHT) * s
        y0, y1 = (HEIGHT - BOTTOM) * s, TOP * s

        def px(seconds: float) -> float:
            return x0 + (x1 - x0) * min(seconds / duration, 1.0)

        def py(cents: float) -> float:
            return y0 - (y0 - y1) * (cents - 100) / (top_cents - 100)

        self._grid(draw, top_cents, py, x0, x1)

        if crash_cents > 100:
            # Curva muestreada a intervalos de tiempo iguales: el multiplicador
            # en el paso i es crash^(i/steps), que es la misma exponencial.
            steps = 160
            growth = math.log(crash_cents / 100)
            points = [
                (px(duration * i / steps), py(100 * math.exp(growth * i / steps)))
                for i in range(steps + 1)
            ]
            fill = [*points, (points[-1][0], y0), (x0, y0)]
            draw.polygon(fill, fill=(*CURVE_FILL, 38))
            draw.line(points, fill=CURVE, width=5 * s, joint="curve")
            end = points[-1]
        else:
            # Explota en la rampa: la estrella sale en el arranque.
            end = (x0 + 24 * s, y0)
        self._burst(draw, end, 18 * s)
        self._cashouts(draw, cashouts, px, py)

        # Rótulo grande: el punto de explosión.
        big = self._font(44 * s)
        draw.text((24 * s, 14 * s), format_multiplier(crash_cents), font=big, fill=BOOM)
        width = draw.textlength(format_multiplier(crash_cents), font=big)
        label = "¡EXPLOTÓ EN LA RAMPA!" if crash_cents == 100 else "BOOM"
        draw.text((24 * s + width + 14 * s, 22 * s), label, font=self._font(18 * s), fill=TEXT)
        if subtitle:
            draw.text(
                (24 * s + width + 14 * s, 46 * s), subtitle, font=self._font(14 * s), fill=MUTED
            )

        final = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        final.quantize(colors=96, method=Image.Quantize.MEDIANCUT).save(
            buffer, format="PNG", optimize=True
        )
        return buffer.getvalue()

    # -- Piezas -----------------------------------------------------------------------

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        with self._lock:
            font = self._fonts.get(size)
            if font is None:
                font = ImageFont.truetype(str(self._font_path), size)
                self._fonts[size] = font
            return font

    def _grid(self, draw: ImageDraw.ImageDraw, top_cents: int, py, x0: float, x1: float) -> None:  # noqa: ANN001
        """Líneas horizontales en multiplicadores redondos (1x, 2x, 5x, 10x…)."""
        s = SCALE
        span = (top_cents - 100) / 100
        step = next(v for v in (0.25, 0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500) if span / v <= 5)
        font = self._font(12 * s)
        # 1x abajo y después los múltiplos redondos del paso (50x, 100x…), no 51x.
        values = [1.0]
        k = 1
        while k * step * 100 <= top_cents:
            if k * step > 1:
                values.append(k * step)
            k += 1
        for value in values:
            y = py(value * 100)
            draw.line((x0, y, x1, y), fill=GRID, width=s)
            text = f"{value:g}x".replace(".", ",")
            draw.text((x0 - 10 * s, y), text, font=font, fill=AXIS_TEXT, anchor="rm")

    def _burst(self, draw: ImageDraw.ImageDraw, center: tuple[float, float], radius: float) -> None:
        """Estrella de explosión de 10 picos."""
        cx, cy = center
        spikes = 10
        points = []
        for i in range(spikes * 2):
            r = radius if i % 2 == 0 else radius * 0.45
            angle = math.pi * i / spikes - math.pi / 2
            points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        draw.polygon(points, fill=BOOM)
        draw.ellipse(
            (cx - radius * 0.3, cy - radius * 0.3, cx + radius * 0.3, cy + radius * 0.3),
            fill=(255, 230, 160),
        )

    def _cashouts(
        self,
        draw: ImageDraw.ImageDraw,
        cashouts: Sequence[CashoutMark],
        px,  # noqa: ANN001
        py,  # noqa: ANN001
    ) -> None:
        """Un círculo blanco en la curva por retirada y su rótulo al lado.

        Los rótulos se apilan hacia arriba si se pisarían con el anterior.
        """
        s = SCALE
        font = self._font(13 * s)
        ordered = sorted(cashouts, key=lambda m: m.cents)
        taken: list[tuple[float, float, float, float]] = []
        for index, mark in enumerate(ordered):
            x, y = px(seconds_to(mark.cents)), py(mark.cents)
            r = 6 * s
            draw.ellipse((x - r, y - r, x + r, y + r), fill=TEXT, outline=BACKGROUND, width=2 * s)
            if index >= MAX_LABELS:
                continue
            text = f"{mark.name} {format_multiplier(mark.cents)}"
            w = draw.textlength(text, font=font)
            h = 18 * s
            # A la izquierda y por encima del punto: la curva sube hacia la derecha.
            box = [x - w - 18 * s, y - h - 10 * s, x - 8 * s, y - 10 * s]
            box[0] = max(box[0], 6 * s)
            box[2] = box[0] + w + 10 * s
            while any(_overlaps(box, other) for other in taken):
                box[1] -= h + 4 * s
                box[3] -= h + 4 * s
            taken.append(tuple(box))
            draw.rounded_rectangle(box, radius=6 * s, fill=(*LABEL_BG, 230))
            draw.text(
                (box[0] + 5 * s, (box[1] + box[3]) / 2), text, font=font, fill=TEXT, anchor="lm"
            )


def _overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
