"""Lectura validada de imágenes enviadas por usuarios.

Toda imagen que llega de Discord (un adjunto o un avatar) es entrada no
confiable: puede ser enorme, estar corrupta o ser una "bomba de
descompresión" (una cabecera que declara millones de píxeles). Este módulo
concentra las comprobaciones para que `magik` y los efectos de `memes`
apliquen exactamente los mismos límites.

Es lógica pura y bloqueante: quien lo use desde el event loop debe hacerlo
dentro de `asyncio.to_thread`.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

# Límites de seguridad, comprobados antes de decodificar los píxeles.
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_INPUT_PIXELS = 25_000_000
MIN_SIDE_PIXELS = 16


class InvalidImageError(Exception):
    """El archivo no es una imagen legible o es demasiado pequeño."""


class ImageTooLargeError(Exception):
    """La imagen supera los límites de tamaño admitidos."""


def open_image(data: bytes, *, draft_side: int | None = None) -> Image.Image:
    """Decodifica `data` como imagen ya cargada en memoria y con la rotación EXIF aplicada.

    Las dimensiones declaradas en la cabecera se comprueban *antes* de
    decodificar, para no reservar memoria por una imagen trampa. Las
    imágenes animadas se reducen a su primer fotograma. El modo de color
    se conserva (RGB, RGBA, P...); convertirlo es cosa de quien llama.

    Args:
        data: Bytes del archivo original.
        draft_side: Si se indica, los JPEG se decodifican directamente a un
            tamaño aproximado de `2 × draft_side`, mucho más rápido con fotos
            de varios megapíxeles. No afecta a otros formatos.

    Raises:
        ImageTooLargeError: Si los bytes o los píxeles superan los límites.
        InvalidImageError: Si no es una imagen válida o es demasiado pequeña.
    """
    if len(data) > MAX_INPUT_BYTES:
        raise ImageTooLargeError("El archivo supera el tamaño máximo permitido.")

    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width * height > MAX_INPUT_PIXELS:
            raise ImageTooLargeError("La imagen tiene demasiados píxeles.")
        if min(width, height) < MIN_SIDE_PIXELS:
            raise InvalidImageError("La imagen es demasiado pequeña.")
        if draft_side is not None:
            image.draft("RGB", (draft_side * 2, draft_side * 2))
        image.load()
        # Las fotos de móvil guardan su giro en los metadatos EXIF: sin esto
        # el resultado saldría tumbado respecto a como se ve la foto original.
        image = ImageOps.exif_transpose(image)
    except (ImageTooLargeError, InvalidImageError):
        raise
    except Image.DecompressionBombError as error:
        raise ImageTooLargeError("La imagen tiene demasiados píxeles.") from error
    except (OSError, ValueError, SyntaxError) as error:
        raise InvalidImageError("No se pudo leer el archivo como imagen.") from error
    return image


def flatten_on_white(image: Image.Image) -> Image.Image:
    """Devuelve la imagen en RGB, con la transparencia sobre fondo blanco.

    Convertir RGBA a RGB directamente deja negras las zonas transparentes.
    """
    if image.mode in {"RGBA", "LA", "P"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")
