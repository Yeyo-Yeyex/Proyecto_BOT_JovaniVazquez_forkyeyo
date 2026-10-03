"""Utilidades de dibujo compartidas por los efectos: plantillas, fuentes y texto.

Las funciones de texto reproducen las de `imgen`, el generador de imágenes de
Dank Memer (licencia MIT), adaptadas a Pillow moderno: `font.getsize` y
`draw.textsize` ya no existen y se sustituyen por medidas con `getbbox`.

Diferencia deliberada con el original: no se dibujan emojis como imágenes
(eso exigía ~60 MB de PNG). Los emojis personalizados de Discord se escriben
como `:nombre:` y los emojis Unicode se eliminan, porque las fuentes de las
plantillas no tienen esos glifos y saldrían como cuadros vacíos.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from bot.services.memes.registry import MemeInputError

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "memes"

# Las plantillas se guardan en PNG, JPEG o GIF según qué pese menos; el
# código las pide sin extensión para no depender de esa decisión.
_ASSET_EXTENSIONS = (".png", ".jpg", ".gif", ".mp4")

_CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")
# Bloques Unicode de emojis, selectores de variante y unión de anchura cero.
_UNICODE_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0000fe00-\U0000fe0f\U0000200d\U0001f1e6-\U0001f1ff]"
)

# Lienzo mínimo reutilizable para medir texto sin crear uno en cada llamada.
_MEASURE = ImageDraw.Draw(Image.new("L", (1, 1)))


@lru_cache(maxsize=256)
def asset_path(stem: str) -> Path:
    """Ruta de la plantilla `stem` (p. ej. `"abandon/abandon"`), sin importar su extensión.

    Raises:
        FileNotFoundError: Si no existe; es un fallo del código, no del usuario.
    """
    for extension in _ASSET_EXTENSIONS:
        path = ASSETS_DIR / f"{stem}{extension}"
        if path.is_file():
            return path
    raise FileNotFoundError(f"Falta la plantilla {stem}")


def asset(stem: str) -> Image.Image:
    """Abre una plantilla estática ya cargada en memoria (el archivo queda cerrado)."""
    with Image.open(asset_path(stem)) as image:
        image.load()
        return image.copy()


def frames(stem: str) -> list[Image.Image]:
    """Todos los fotogramas de una plantilla animada, en RGBA."""
    result = []
    with Image.open(asset_path(stem)) as image:
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            result.append(image.convert("RGBA"))
    return result


@lru_cache(maxsize=64)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Fuente de `assets/memes/fonts` (con extensión, p. ej. `"verdana.ttf"`)."""
    return ImageFont.truetype(str(ASSETS_DIR / "fonts" / name), size)


def clean_text(text: str) -> str:
    """Adapta texto de Discord a lo que las fuentes de las plantillas pueden dibujar."""
    text = _CUSTOM_EMOJI.sub(r":\1:", text)
    text = _UNICODE_EMOJI.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def text_size(text: str, typeface: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """Ancho y alto que ocupa `text` dibujado desde (0, 0), como el antiguo `textsize`."""
    if not text:
        return 0, 0
    _, _, right, bottom = _MEASURE.multiline_textbbox((0, 0), text, font=typeface)
    return int(right), int(bottom)


def wrap(typeface: ImageFont.FreeTypeFont, text: str, line_width: int) -> str:
    """Parte el texto en líneas que no superen `line_width` píxeles (por palabras)."""
    lines: list[str] = []
    line: list[str] = []
    for word in text.split():
        candidate = " ".join([*line, word])
        if text_size(candidate, typeface)[0] > line_width and line:
            lines.append(" ".join(line))
            line = [word]
        else:
            line.append(word)
    if line:
        lines.append(" ".join(line))
    return "\n".join(lines).strip()


def auto_text_size(
    text: str,
    typeface: ImageFont.FreeTypeFont,
    desired_width: int,
    fallback_size: int = 25,
    font_scalar: float = 1,
) -> tuple[ImageFont.FreeTypeFont, str]:
    """Busca un tamaño de letra con el que el texto ocupe justo `desired_width`.

    Prueba tamaños de 20 a 39 (escalados por `font_scalar`) y devuelve la
    fuente y el texto partido en líneas. Si ninguno encaja, usa
    `fallback_size`.
    """
    for size in range(20, 40):
        candidate = typeface.font_variant(size=math.floor(size * font_scalar))
        if text_size(text, candidate)[0] >= desired_width:
            wrapped = wrap(candidate, text, desired_width)
            width = max(text_size(line, candidate)[0] for line in wrapped.splitlines())
            if abs(desired_width - width) <= 10:
                return candidate, wrapped
    fallback = typeface.font_variant(size=fallback_size)
    return fallback, wrap(fallback, text, desired_width)


def draw_text(
    image: Image.Image,
    position: tuple[float, float],
    text: str,
    typeface: ImageFont.FreeTypeFont,
    fill: object = "black",
) -> None:
    """Dibuja texto (multilínea) sobre `image`, como `render_text_with_emoji` sin emojis."""
    ImageDraw.Draw(image).multiline_text(
        (int(position[0]), int(position[1])), text, font=typeface, fill=fill
    )


def paste(base: Image.Image, overlay: Image.Image, position: tuple[int, int]) -> None:
    """Pega `overlay` sobre `base` usando su propia transparencia como máscara."""
    mask = overlay if overlay.mode in {"RGBA", "LA"} else None
    base.paste(overlay, position, mask)


def circle_mask(size: tuple[int, int]) -> Image.Image:
    """Máscara circular suavizada (se dibuja al triple y se reduce)."""
    big = Image.new("L", (size[0] * 3, size[1] * 3), 0)
    ImageDraw.Draw(big).ellipse((0, 0, *big.size), fill=255)
    return big.resize(size, Image.Resampling.LANCZOS)


def skew(
    image: Image.Image,
    target: list[tuple[float, float]],
    source: list[tuple[float, float]] | None = None,
    resolution: int = 1024,
) -> Image.Image:
    """Proyecta la imagen sobre el cuadrilátero `target` (esquinas en sentido horario).

    Resuelve la transformación de perspectiva que lleva las cuatro esquinas
    de origen a las de destino; Pillow necesita la inversa (de destino a
    origen), por eso los papeles de `source` y `target` se intercambian.
    """
    if source is None:
        source = [(0, 0), (image.width, 0), (image.width, image.height), (0, image.height)]
    matrix = []
    for (sx, sy), (tx, ty) in zip(source, target, strict=True):
        matrix.append([tx, ty, 1, 0, 0, 0, -sx * tx, -sx * ty])
        matrix.append([0, 0, 0, tx, ty, 1, -sy * tx, -sy * ty])
    coefficients = np.linalg.lstsq(
        np.array(matrix, dtype=float), np.array(source, dtype=float).reshape(8), rcond=None
    )[0]
    return image.transform(
        (resolution, resolution),
        Image.Transform.PERSPECTIVE,
        tuple(coefficients),
        Image.Resampling.BICUBIC,
    )


def _fitted_lines(text: str, typeface: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Líneas que caben en `width`; las palabras demasiado largas se cortan por letras."""
    result: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = word if not current else f"{current} {word}"
            if text_size(candidate, typeface)[0] <= width:
                current = candidate
                continue
            if current:
                result.append(current)
                current = ""
            for char in word:
                if current and text_size(current + char, typeface)[0] > width:
                    result.append(current)
                    current = ""
                current += char
        result.append(current)
    return result


def draw_fitted_text(
    image: Image.Image,
    text: str,
    box: tuple[int, int, int, int],
    font_name: str,
    maximum: int,
    *,
    minimum: int = 12,
    fill: object = "black",
    max_lines: int | None = None,
) -> None:
    """Dibuja el texto centrado en `box`, con el mayor tamaño que quepa.

    Raises:
        MemeInputError: Si ni con `minimum` puntos cabe el texto.
    """
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    draw = ImageDraw.Draw(image)
    for size in range(maximum, minimum - 1, -1):
        typeface = font(font_name, size)
        lines = _fitted_lines(text, typeface, width)
        ascent, descent = typeface.getmetrics()
        line_height = ascent + descent
        if max_lines is not None and len(lines) > max_lines:
            continue
        if len(lines) * line_height > height:
            continue
        y = top + (height - len(lines) * line_height) / 2
        for line in lines:
            line_left, _, line_right, _ = draw.textbbox((0, 0), line or " ", font=typeface)
            x = left + (width - (line_right - line_left)) / 2 - line_left
            draw.text((x, y), line, font=typeface, fill=fill)
            y += line_height
        return
    raise MemeInputError("El texto es demasiado largo para esta plantilla.")


def render_caption(
    text: str,
    font_name: str,
    size: int,
    color: object,
    *,
    width: int | None = None,
    background: object | None = None,
    stroke_width: int = 0,
) -> Image.Image:
    """Texto sobre una capa propia, para superponerlo a vídeos o GIF.

    Si se da `width`, el texto se parte por letras hasta ese ancho y la capa
    lo ocupa entero; si no, la capa mide lo justo.
    """
    typeface = font(font_name, size)

    def bounds(value: str) -> tuple[float, float, float, float]:
        return _MEASURE.textbbox((0, 0), value or " ", font=typeface, stroke_width=stroke_width)

    if width is None:
        lines = text.split("\n")
    else:
        lines = []
        for paragraph in text.split("\n"):
            current = ""
            for char in paragraph:
                candidate = current + char
                left, _, right, _ = bounds(candidate)
                if current and right - left > width:
                    split = current.rfind(" ")
                    if split > 0:
                        lines.append(current[:split])
                        current = current[split + 1 :] + char
                    else:
                        lines.append(current)
                        current = char
                else:
                    current = candidate
            lines.append(current)

    ascent, descent = typeface.getmetrics()
    line_height = ascent + descent
    if width is None:
        width = max(1, *(int(bounds(line)[2] - min(0, bounds(line)[0])) for line in lines))
    layer = Image.new(
        "RGBA", (max(1, width), max(1, line_height * len(lines))), background or (0, 0, 0, 0)
    )
    draw = ImageDraw.Draw(layer)
    for index, line in enumerate(lines):
        left = bounds(line)[0]
        draw.text(
            (max(0, -left), index * line_height),
            line,
            font=typeface,
            fill=color,
            stroke_width=stroke_width,
            stroke_fill=color,
        )
    return layer
