"""Dibujo de las imágenes del aspecto de Jovani: avatar, banner y su texto.

Son funciones síncronas y deterministas (mismas entradas, mismos bytes) que usan
Pillow y bloquean: se llaman siempre dentro de `asyncio.to_thread`.

Discord coloca el avatar encima de la esquina inferior izquierda del banner, así que
el texto va alineado a la derecha y dentro del `TEXT_AREA` derecho de la imagen. La
fuente (`MontserratBold.ttf`) no tiene emojis: `printable` quita lo que no puede pintar
para que no salgan cuadraditos. El PNG usa `compress_level=6`, no `optimize=True`
(en el NAS `optimize` multiplica por seis el tiempo de guardado).
"""

from __future__ import annotations

import io
import unicodedata
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import bot.services
from bot.services.aspecto import Look
from bot.services.image_input import open_image

AVATAR_SIDE = 512
BANNER_SIZE = (1200, 480)

FONT_PATH = (
    Path(bot.services.__file__).parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
)

#: Fracción derecha del banner donde puede ir el texto (el avatar tapa el resto).
TEXT_AREA = 0.62
#: Margen entre el texto y el borde derecho, en píxeles.
MARGIN = 48
#: Opacidad (0-255) del velo oscuro sobre una imagen de fondo.
VEIL_ALPHA = 140
#: Máximo de líneas de datos bajo el título.
MAX_LINES = 3

_TITLE_SIZE = 88
_VERSION_SIZE = 34
_LINE_SIZE = 38
_MIN_SIZE = 18
_ELLIPSIS = "…"
_WHITE = (255, 255, 255)
_SHADOW = (0, 0, 0)


@lru_cache(maxsize=64)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


@lru_cache(maxsize=1)
def _missing_glyph() -> bytes:
    """Cómo pinta la fuente un carácter que no tiene (el recuadro de «no encontrado»)."""
    return bytes(_font(64).getmask("\U0010ffff"))


@lru_cache(maxsize=4096)
def _paintable(char: str) -> bool:
    if char.isspace():
        return True
    if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn", "Mn", "Me"}:
        return False
    return bytes(_font(64).getmask(char)) != _missing_glyph()


def printable(text: str) -> str:
    """Quita lo que la fuente no puede pintar (emojis, símbolos raros) y limpia espacios."""
    kept = "".join(char for char in unicodedata.normalize("NFC", text) if _paintable(char))
    return " ".join(kept.split())


def _square_crop(image: Image.Image) -> Image.Image:
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    return image.crop((left, top, left + side, top + side))


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Recorta en el centro a la proporción de `size` y lo escala a `size`."""
    width, height = size
    if image.width * height > image.height * width:
        new_width = max(1, round(image.height * width / height))
        left = (image.width - new_width) // 2
        image = image.crop((left, 0, left + new_width, image.height))
    else:
        new_height = max(1, round(image.width * height / width))
        top = (image.height - new_height) // 2
        image = image.crop((0, top, image.width, top + new_height))
    return image.resize(size, Image.Resampling.LANCZOS)


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue()


def prepare_avatar(data: bytes) -> bytes:
    """Recorte cuadrado centrado, a `AVATAR_SIDE` de lado, en PNG.

    Raises:
        InvalidImageError: Si `data` no es una imagen legible.
        ImageTooLargeError: Si supera los límites de `open_image`.
    """
    image = open_image(data).convert("RGBA")
    image = _square_crop(image).resize((AVATAR_SIDE, AVATAR_SIDE), Image.Resampling.LANCZOS)
    return _png(image)


def prepare_banner(data: bytes) -> bytes:
    """Recorte centrado a la proporción de `BANNER_SIZE`, a ese tamaño, en PNG.

    Raises:
        InvalidImageError: Si `data` no es una imagen legible.
        ImageTooLargeError: Si supera los límites de `open_image`.
    """
    image = open_image(data).convert("RGBA")
    return _png(_cover(image, BANNER_SIZE).convert("RGB"))


def _gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = BANNER_SIZE
    column = Image.new("RGB", (1, height))
    column.putdata(
        [
            tuple(round(a + (b - a) * y / (height - 1)) for a, b in zip(top, bottom, strict=True))
            for y in range(height)
        ]
    )
    return column.resize(BANNER_SIZE, Image.Resampling.NEAREST)


def _width(text: str, font: ImageFont.FreeTypeFont, stroke: int) -> int:
    left, _, right, _ = font.getbbox(text, anchor="rt", stroke_width=stroke)
    return int(right - left)


def _fit(text: str, size: int, max_width: int, stroke: int) -> tuple[str, ImageFont.FreeTypeFont]:
    """Reduce el tamaño hasta que `text` quepa y, si no, lo recorta con «…»."""
    for current in range(size, _MIN_SIZE - 1, -2):
        font = _font(current)
        if _width(text, font, stroke) <= max_width:
            return text, font
    font = _font(_MIN_SIZE)
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _width(text[:middle].rstrip() + _ELLIPSIS, font, stroke) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + _ELLIPSIS, font


def shared_size(texts: Sequence[str], size: int, max_width: int) -> int:
    """Tamaño común para varias líneas: el de la que más haya que reducir.

    Si cada línea se redujera por su cuenta, las cortas se verían más grandes que las
    largas y el bloque de datos quedaría desigual.
    """
    stroke = max(2, size // 14)
    return min((int(_fit(text, size, max_width, stroke)[1].size) for text in texts), default=size)


def draw_banner(
    look: Look,
    base: bytes | None,
    title: str,
    lines: Sequence[str],
    version: str,
) -> bytes:
    """Dibuja el banner de un look, de `BANNER_SIZE`.

    Args:
        look: Look del que salen los colores del degradado.
        base: Imagen de fondo ya pasada por `prepare_banner`, o `None` para el degradado.
        title: Título grande.
        lines: Líneas de datos; se usan como mucho `MAX_LINES`.
        version: Versión del bot; si va vacía no se pinta.

    Returns:
        El PNG del banner.
    """
    if base is None:
        canvas = _gradient(look.top, look.bottom).convert("RGBA")
    else:
        with Image.open(io.BytesIO(base)) as source:
            background = _cover(source.convert("RGBA"), BANNER_SIZE)
        canvas = Image.alpha_composite(
            background, Image.new("RGBA", BANNER_SIZE, (0, 0, 0, VEIL_ALPHA))
        )

    width, height = BANNER_SIZE
    right = width - MARGIN
    max_width = right - round(width * (1 - TEXT_AREA))

    entries: list[tuple[str, int]] = []
    clean_title = printable(title)
    if clean_title:
        entries.append((clean_title, _TITLE_SIZE))
    if version:
        entries.append((f"v{printable(version)}", _VERSION_SIZE))
    data = [clean for line in lines[:MAX_LINES] if (clean := printable(line))]
    line_size = shared_size(data, _LINE_SIZE, max_width)
    entries += [(text, line_size) for text in data]

    draw = ImageDraw.Draw(canvas)
    fitted = []
    for text, size in entries:
        stroke = max(2, size // 14)
        shown, font = _fit(text, size, max_width, stroke)
        fitted.append((shown, font, stroke))

    gap = 14
    heights = [sum(font.getmetrics()) + 2 * stroke for _, font, stroke in fitted]
    total = sum(heights) + gap * max(0, len(fitted) - 1)
    y = max(24, (height - total) // 2)
    for (shown, font, stroke), block in zip(fitted, heights, strict=True):
        draw.text(
            (right, y + stroke),
            shown,
            font=font,
            fill=_WHITE,
            anchor="rt",
            stroke_width=stroke,
            stroke_fill=_SHADOW,
        )
        y += block + gap
    return _png(canvas.convert("RGB"))
