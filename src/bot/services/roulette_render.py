"""Animación de la ruleta: un GIF de giro y una imagen fija por casilla.

Para que el giro sea instantáneo y gaste poco en el NAS, no se dibuja nada
por tirada. Hay 38 resultados posibles, así que se renderiza una animación
por casilla una sola vez y se guarda en memoria (38 GIF de ~50 KB). Cada
tirada solo elige la animación de la casilla ganadora.

La rueda se queda quieta y lo que se mueve es la bola: da vueltas, frena,
rebota y cae en su casilla. Así cada fotograma solo cambia unos pocos
píxeles y el GIF pesa ~50 KB en vez de ~800 KB con la rueda girando, y los
números se leen mientras la bola pasa por encima.

Cada animación empieza en la misma imagen que `idle_png` (la rueda vacía) y
termina exactamente en su PNG final. El bot muestra la rueda vacía, el GIF y,
cuando este acaba, cambia el adjunto por el PNG: los cambios no se notan y
el mensaje deja de animarse aunque algún cliente repita los GIF en bucle.

Colores: el verde de los ceros tira a turquesa para distinguirse del rojo
también con deuteranopia, y cada casilla lleva su número escrito.
"""

from __future__ import annotations

import io
import math
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.services.roulette import WHEEL_ORDER, color, label
from bot.utils.gif import shared_palette_gif

FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
)

#: Lado de la imagen final en píxeles. Discord muestra las imágenes de embed
#: a ~300-400 px; más grande solo engorda el archivo.
SIZE = 300
#: Las capas se dibujan al doble y se reducen, para tener bordes suaves.
SUPERSAMPLE = 2

FRAME_MS = 50
FRAME_COUNT = 40
#: El último fotograma se queda quieto mucho tiempo por si el cliente repite.
FINAL_FRAME_MS = 60_000
#: Lo que dura el giro; el bot espera esto antes de revelar el resultado.
SPIN_SECONDS = FRAME_MS * (FRAME_COUNT - 1) / 1000

#: Vueltas completas de la bola antes de caer.
BALL_LAPS = 3
#: Fracción de la animación a partir de la cual la bola empieza a caer.
DROP_START = 0.55

BACKGROUND = (43, 45, 49)  # gris del fondo de los embeds en el tema oscuro
POCKET_COLORS = {
    "red": (200, 40, 45),
    "black": (24, 24, 27),
    "green": (0, 150, 136),
}
RIM_DARK = (70, 42, 28)
RIM_LIGHT = (120, 78, 48)
GOLD = (222, 178, 70)
BALL = (245, 245, 245)
HIGHLIGHT = (255, 214, 92)

# Radios en fracción del lado de la imagen.
R_RIM = 0.47
R_OUT = 0.43
R_IN = 0.30
R_HUB = 0.19
R_TRACK = 0.455  # pista exterior por la que corre la bola
R_REST = 0.335  # donde reposa la bola: parte interior de la casilla, sin tapar el número

_STEP = 360 / len(WHEEL_ORDER)


@dataclass(frozen=True, slots=True)
class SpinMedia:
    """Archivos ya codificados para una casilla."""

    gif: bytes
    png: bytes


def _ease_out(t: float) -> float:
    """Desaceleración cúbica: rápido al principio, frena al final."""
    return 1 - (1 - t) ** 3


def _polar(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    """Punto a `degrees` grados en sentido horario desde arriba."""
    radians = math.radians(degrees - 90)
    return cx + radius * math.cos(radians), cy + radius * math.sin(radians)


def ball_path(pocket_index: int, frame: int) -> tuple[float, float]:
    """Ángulo (grados) y radio (fracción) de la bola en un fotograma.

    La bola recorre `BALL_LAPS` vueltas por la pista con desaceleración y,
    desde `DROP_START`, cae hacia la casilla con un par de rebotes que se
    amortiguan, hasta quedar quieta en su centro en el último fotograma.
    """
    t = frame / (FRAME_COUNT - 1)
    target = BALL_LAPS * 360 + pocket_index * _STEP
    angle = target * _ease_out(t)
    drop = max(0.0, (t - DROP_START) / (1 - DROP_START))
    bounce = abs(math.sin(drop * 3 * math.pi)) * 0.03 * (1 - drop)
    radius = R_TRACK - (R_TRACK - R_REST) * _ease_out(drop) + bounce
    return angle, radius


class WheelRenderer:
    """Dibuja y cachea las animaciones de la rueda.

    Es seguro llamarlo desde varios hilos: cada casilla se renderiza una sola
    vez aunque dos tiradas la pidan a la vez. La caché tiene como mucho 38
    entradas (una por casilla), así que su tamaño está acotado.
    """

    def __init__(self, font_path: Path = FONT_PATH) -> None:
        self._font_path = font_path
        self._cache: dict[int, SpinMedia] = {}
        self._lock = threading.Lock()
        self._wheel: Image.Image | None = None
        self._ball: Image.Image | None = None
        self._palette: Image.Image | None = None
        self._idle_png: bytes | None = None

    # -- API ------------------------------------------------------------------------

    def media(self, pocket: int) -> SpinMedia:
        """GIF y PNG final de la casilla; se renderizan la primera vez (bloqueante)."""
        cached = self._cache.get(pocket)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._cache.get(pocket)
            if cached is None:
                cached = self._render(pocket)
                self._cache[pocket] = cached
            return cached

    def idle_png(self) -> bytes:
        """Rueda en reposo, sin bola: la imagen de una mesa recién abierta."""
        with self._lock:
            if self._idle_png is None:
                frame = self._quantize([self._compose(None, None)])[0]
                buffer = io.BytesIO()
                frame.save(buffer, format="PNG", optimize=True)
                self._idle_png = buffer.getvalue()
            return self._idle_png

    # -- Capas ----------------------------------------------------------------------

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self._font_path), size * SUPERSAMPLE)

    @staticmethod
    def _downscale(image: Image.Image) -> Image.Image:
        return image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)

    @staticmethod
    def _pocket_polygon(c: float, s: float, index: int) -> list[tuple[float, float]]:
        start = index * _STEP - _STEP / 2
        end = start + _STEP
        return [
            _polar(c, c, s * R_OUT, start),
            _polar(c, c, s * R_OUT, end),
            _polar(c, c, s * R_IN, end),
            _polar(c, c, s * R_IN, start),
        ]

    def _wheel_layer(self) -> Image.Image:
        """Rueda completa sobre el fondo, ya al tamaño final."""
        if self._wheel is not None:
            return self._wheel
        s = SIZE * SUPERSAMPLE
        c = s / 2
        image = Image.new("RGBA", (s, s), BACKGROUND + (255,))
        draw = ImageDraw.Draw(image)

        def circle(radius: float, **kwargs: object) -> None:
            r = s * radius
            draw.ellipse((c - r, c - r, c + r, c + r), **kwargs)

        circle(R_RIM, fill=RIM_DARK)
        circle(R_OUT + 0.01, fill=GOLD)

        font = self._font(13)
        for index, pocket in enumerate(WHEEL_ORDER):
            draw.polygon(self._pocket_polygon(c, s, index), fill=POCKET_COLORS[color(pocket)])
            start = index * _STEP - _STEP / 2
            # Separadores dorados entre casillas, como en una rueda real.
            draw.line(
                [_polar(c, c, s * R_IN, start), _polar(c, c, s * R_OUT, start)],
                fill=GOLD,
                width=SUPERSAMPLE,
            )
            # Número girado en dirección radial, en la parte exterior de la casilla.
            tile = Image.new("RGBA", (font.size * 3, font.size * 2), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text(
                (tile.width / 2, tile.height / 2),
                label(pocket),
                font=font,
                fill="white",
                anchor="mm",
            )
            tile = tile.rotate(-index * _STEP, resample=Image.Resampling.BICUBIC, expand=True)
            tx, ty = _polar(c, c, s * (R_OUT + R_IN) / 2 + s * 0.035, index * _STEP)
            image.alpha_composite(tile, (round(tx - tile.width / 2), round(ty - tile.height / 2)))

        circle(R_IN, fill=RIM_LIGHT, outline=GOLD, width=2 * SUPERSAMPLE)
        circle(R_HUB, fill=RIM_DARK, outline=GOLD, width=2 * SUPERSAMPLE)
        for spoke in range(4):
            draw.line(
                [
                    _polar(c, c, s * R_HUB * 0.3, 45 + spoke * 90),
                    _polar(c, c, s * R_IN, 45 + spoke * 90),
                ],
                fill=GOLD,
                width=3 * SUPERSAMPLE,
            )
        circle(R_HUB * 0.35, fill=GOLD)
        self._wheel = self._downscale(image)
        return self._wheel

    def _ball_sprite(self) -> Image.Image:
        """Bola suavizada, dibujada una vez y pegada en cada fotograma."""
        if self._ball is None:
            d = round(SIZE * 0.05) * 4
            sprite = Image.new("RGBA", (d, d), (0, 0, 0, 0))
            ImageDraw.Draw(sprite).ellipse(
                (2, 2, d - 3, d - 3), fill=BALL, outline=(140, 140, 140), width=4
            )
            self._ball = sprite.resize((d // 4, d // 4), Image.Resampling.LANCZOS)
        return self._ball

    def _result_layer(self, pocket: int) -> Image.Image:
        """Casilla ganadora resaltada y su número grande, derecho, en el centro."""
        s = SIZE * SUPERSAMPLE
        c = s / 2
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        index = WHEEL_ORDER.index(pocket)
        draw.polygon(self._pocket_polygon(c, s, index), outline=HIGHLIGHT, width=4 * SUPERSAMPLE)
        r_badge = s * 0.17
        draw.ellipse(
            (c - r_badge, c - r_badge, c + r_badge, c + r_badge),
            fill=POCKET_COLORS[color(pocket)],
            outline=HIGHLIGHT,
            width=3 * SUPERSAMPLE,
        )
        draw.text((c, c), label(pocket), font=self._font(40), fill="white", anchor="mm")
        return self._downscale(layer)

    # -- Composición ----------------------------------------------------------------

    def _compose(
        self,
        ball: tuple[float, float] | None,
        result: Image.Image | None,
    ) -> Image.Image:
        """Un fotograma RGB: rueda, resultado opcional y bola opcional."""
        frame = self._wheel_layer().copy()
        if result is not None:
            frame.alpha_composite(result)
        if ball is not None:
            angle, radius = ball
            sprite = self._ball_sprite()
            bx, by = _polar(SIZE / 2, SIZE / 2, radius * SIZE, angle)
            frame.alpha_composite(
                sprite, (round(bx - sprite.width / 2), round(by - sprite.height / 2))
            )
        return frame.convert("RGB")

    def _quantize(self, frames: list[Image.Image]) -> list[Image.Image]:
        """Pasa los fotogramas a una paleta común.

        Una sola paleta para todo (sacada de la rueda con el resultado más
        vistoso) evita que los colores parpadeen entre fotogramas o entre la
        última imagen del GIF y el PNG final.
        """
        if self._palette is None:
            sample = self._compose((0.0, R_REST), self._result_layer(1))
            self._palette = sample.quantize(colors=96, method=Image.Quantize.MEDIANCUT)
        return [f.quantize(palette=self._palette, dither=Image.Dither.NONE) for f in frames]

    def _render(self, pocket: int) -> SpinMedia:
        index = WHEEL_ORDER.index(pocket)
        result = self._result_layer(pocket)
        frames = [
            self._compose(ball_path(index, i), result if i == FRAME_COUNT - 1 else None)
            for i in range(FRAME_COUNT)
        ]
        quantized = self._quantize(frames)

        # Sin repetir: el GIF se reproduce una sola vez en los clientes que lo respetan.
        gif = shared_palette_gif(
            quantized, [FRAME_MS] * (FRAME_COUNT - 1) + [FINAL_FRAME_MS], loop=False
        )
        png = io.BytesIO()
        quantized[-1].save(png, format="PNG", optimize=True)
        return SpinMedia(gif=gif, png=png.getvalue())
