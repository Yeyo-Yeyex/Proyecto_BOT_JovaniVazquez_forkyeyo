"""Efecto "magik": deformación de imágenes mediante *seam carving*.

El *seam carving* (reescalado consciente del contenido) no estira ni recorta
la imagen de forma uniforme. En cada paso calcula la "energía" de cada píxel
(cuánto contraste tiene respecto a sus vecinos), busca la **costura** (un
camino continuo de píxeles de arriba abajo) de menor energía total y la
elimina. Así se pierden primero las zonas planas y se conservan los bordes;
al repetirlo muchas veces y volver a ampliar la imagen, las formas se
derriten de forma característica. Es la técnica que usan los bots tipo
Dank Memer para su comando `magik`.

Este módulo es lógica pura: no depende de discord.py, no hace I/O de red y
es síncrono y bloqueante (CPU). Quien lo llame desde el event loop debe
hacerlo con `asyncio.to_thread` (ver Biblia.txt, norma de rendimiento).
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps

# Límites de seguridad: la imagen la controla el usuario, así que se rechaza
# lo que pueda agotar memoria o CPU antes de decodificarlo por completo.
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_INPUT_PIXELS = 25_000_000
MIN_SIDE_PIXELS = 16

# El seam carving es O(costuras × píxeles): se trabaja siempre sobre una
# versión reducida para que el comando responda en pocos segundos.
WORKING_MAX_SIDE = 320

# Fracción de ancho y alto que se conserva al eliminar costuras. Con 0.5 se
# quita la mitad en cada eje, y luego se amplía de vuelta al tamaño original.
KEEP_FRACTION = 0.5


class InvalidImageError(Exception):
    """El archivo no es una imagen legible o es demasiado pequeño."""


class ImageTooLargeError(Exception):
    """La imagen supera los límites de tamaño admitidos."""


def _energy(image: np.ndarray) -> np.ndarray:
    """Mapa de energía: magnitud del gradiente de la luminosidad.

    Los píxeles en bordes y detalles tienen energía alta; las zonas planas,
    baja. Es lo que decide qué costuras se sacrifican primero.
    """
    gray = image @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    return np.abs(np.gradient(gray, axis=1)) + np.abs(np.gradient(gray, axis=0))


def _find_vertical_seam(energy: np.ndarray) -> np.ndarray:
    """Encuentra la costura vertical de menor energía acumulada.

    Programación dinámica por filas, vectorizada por columnas: cada fila
    solo necesita la anterior, así que el bucle es de `alto` iteraciones
    numpy y no de `alto × ancho` en Python puro.

    Returns:
        Vector de `alto` posiciones: la columna de la costura en cada fila.
    """
    height, width = energy.shape
    cost = energy.astype(np.float32, copy=True)
    # Desplazamiento (-1, 0, +1) elegido en cada celda, para reconstruir el camino.
    step = np.zeros((height, width), dtype=np.int8)
    columns = np.arange(width)
    infinity = np.float32(np.inf)

    for row in range(1, height):
        previous = cost[row - 1]
        left = np.concatenate(([infinity], previous[:-1]))
        right = np.concatenate((previous[1:], [infinity]))
        candidates = np.stack((left, previous, right))
        choice = candidates.argmin(axis=0)
        cost[row] += candidates[choice, columns]
        step[row] = choice - 1

    seam = np.empty(height, dtype=np.intp)
    seam[-1] = int(cost[-1].argmin())
    for row in range(height - 1, 0, -1):
        seam[row - 1] = seam[row] + step[row, seam[row]]
    return seam


def _remove_vertical_seam(image: np.ndarray, seam: np.ndarray) -> np.ndarray:
    """Devuelve la imagen sin los píxeles de la costura (un píxel menos de ancho)."""
    height, width, channels = image.shape
    keep = np.ones((height, width), dtype=bool)
    keep[np.arange(height), seam] = False
    return image[keep].reshape(height, width - 1, channels)


def _carve_width(image: np.ndarray, target_width: int) -> np.ndarray:
    """Elimina costuras verticales hasta alcanzar `target_width` píxeles de ancho."""
    while image.shape[1] > target_width:
        seam = _find_vertical_seam(_energy(image))
        image = _remove_vertical_seam(image, seam)
    return image


def _load_rgb(data: bytes) -> Image.Image:
    """Decodifica los bytes como imagen RGB, validando tamaño y formato.

    Se comprueban las dimensiones declaradas en la cabecera *antes* de
    decodificar los píxeles, para no reservar memoria por una "bomba de
    descompresión". Las imágenes animadas se reducen a su primer fotograma.

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
        # En JPEG, decodifica directamente a una versión reducida (mucho más
        # rápido con fotos de varios megapíxeles); en otros formatos no hace nada.
        image.draft("RGB", (WORKING_MAX_SIDE * 2, WORKING_MAX_SIDE * 2))
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

    if image.mode in {"RGBA", "LA", "P"}:
        # Sobre fondo blanco: la transparencia no tiene "energía" útil y
        # dejaría costuras negras tras la conversión directa a RGB.
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def apply_magik(data: bytes) -> bytes:
    """Aplica el efecto magik a una imagen y devuelve el resultado como PNG.

    Reduce la imagen a un tamaño de trabajo, elimina costuras verticales y
    horizontales hasta conservar `KEEP_FRACTION` de cada eje y vuelve a
    ampliarla al tamaño de trabajo.

    Args:
        data: Contenido del archivo de imagen original (PNG, JPEG, GIF, ...).

    Returns:
        Bytes de la imagen distorsionada, en formato PNG.

    Raises:
        ImageTooLargeError: Si la imagen supera los límites de tamaño.
        InvalidImageError: Si no es una imagen válida.
    """
    image = _load_rgb(data)
    image.thumbnail((WORKING_MAX_SIDE, WORKING_MAX_SIDE), Image.Resampling.LANCZOS)
    width, height = image.size

    pixels = np.asarray(image, dtype=np.float32)
    target_width = max(2, int(width * KEEP_FRACTION))
    target_height = max(2, int(height * KEEP_FRACTION))

    pixels = _carve_width(pixels, target_width)
    # Las costuras horizontales son las verticales de la imagen traspuesta.
    pixels = _carve_width(pixels.transpose(1, 0, 2), target_height).transpose(1, 0, 2)

    carved = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), mode="RGB")
    result = carved.resize((width, height), Image.Resampling.BICUBIC)

    output = io.BytesIO()
    result.save(output, format="PNG", optimize=True)
    return output.getvalue()
