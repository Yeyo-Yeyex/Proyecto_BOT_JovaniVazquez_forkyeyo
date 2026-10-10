"""GIF animados rápidos: cada fotograma guarda solo los píxeles que cambian.

Los juegos del casino mandan un GIF por jugada, así que escribirlo tiene que
ser barato. Pillow, con sus opciones por defecto, optimiza cada fotograma en
Python píxel a píxel (reordena la paleta y busca qué ha cambiado), y en el
pachinko eso eran ~750 ms de los ~870 ms de toda la tanda. Aquí ese trabajo
lo hace numpy:

1. Se compara cada fotograma con el anterior.
2. Los píxeles que no cambian se pintan con un índice reservado que el GIF
   declara transparente. Como no se borra lo anterior (`disposal=1`), se ve
   lo que había debajo.
3. Se le pasa a Pillow con `optimize=False`. Las zonas quietas son tiras
   largas del mismo índice, que el LZW comprime casi a cero.

La animación que se ve es idéntica a la de guardar los fotogramas tal cual.

Hay dos funciones, según cómo estén hechos los fotogramas:

- `shared_palette_gif`: fotogramas ya reducidos a una paleta común (modo
  `P`), como el pachinko, la tragaperras o la ruleta. Se conservan los
  índices tal cual.
- `local_palette_gif`: fotogramas a color completo con fotos o degradados,
  como los botes, la gallina o los caballos. Una paleta común los dejaría sin
  matices, así que cada fotograma lleva la suya, sacada solo de lo que cambia.

Por defecto el GIF se repite (los juegos acaban con un fotograma muy largo
para que no se note). Con `loop=False` no se escribe la extensión NETSCAPE y
se reproduce una sola vez en los clientes que lo respetan, como la ruleta.
"""

from __future__ import annotations

import io
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

#: Índice transparente de `local_palette_gif`: la paleta de cada fotograma usa los 255 primeros.
LOCAL_TRANSPARENT = 255
#: Colores de la paleta de cada fotograma en `local_palette_gif`.
LOCAL_COLORS = 255
#: Hilos que calculan las paletas de `local_palette_gif` a la vez.
QUANTIZE_THREADS = min(4, os.cpu_count() or 1)

Durations = int | Sequence[int]


def _save(
    images: Sequence[Image.Image],
    durations: Durations,
    *,
    loop: bool,
    transparency: int | None,
) -> bytes:
    """Escribe el GIF sin la optimización de Pillow (el trabajo ya está hecho)."""
    options: dict[str, object] = {}
    if loop:
        options["loop"] = 0
    if transparency is not None:
        options["transparency"] = transparency
    buffer = io.BytesIO()
    images[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=durations if isinstance(durations, int) else list(durations),
        disposal=1,
        optimize=False,
        **options,
    )
    return buffer.getvalue()


def _free_index(frames: Sequence[Image.Image], palette_size: int) -> int | None:
    """Un índice que no pinta ningún fotograma, para usarlo como transparente.

    Se prefiere uno de la paleta que no se use (así no crece el número de bits
    por píxel); si no hay, el primero que queda fuera de ella. `None` si la
    paleta ya tiene 256 colores y todos se usan.
    """
    used = np.zeros(256, dtype=bool)
    for frame in frames:
        used |= np.asarray(frame.histogram()[:256]) > 0
    for index in range(palette_size):
        if not used[index]:
            return index
    return palette_size if palette_size < 256 else None


def shared_palette_gif(
    frames: Sequence[Image.Image], durations: Durations, *, loop: bool = True
) -> bytes:
    """GIF a partir de fotogramas en modo `P` que comparten paleta.

    Args:
        frames: Los fotogramas, todos del mismo tamaño y con la misma paleta.
        durations: Milisegundos de cada fotograma, o uno para todos.
        loop: Si el GIF se repite.

    Returns:
        Los bytes del GIF.

    Raises:
        ValueError: Si no hay fotogramas, alguno no está en modo `P` o las
            paletas no coinciden (los índices no significarían lo mismo).
    """
    if not frames:
        raise ValueError("Hace falta al menos un fotograma.")
    palette = frames[0].getpalette() or []
    for frame in frames:
        if frame.mode != "P" or (frame.getpalette() or []) != palette:
            raise ValueError("Todos los fotogramas tienen que estar en modo P con la misma paleta.")

    transparent = _free_index(frames, len(palette) // 3)
    if transparent is None:
        # Sin hueco para el transparente: cada fotograma entero, sin la optimización lenta.
        return _save(frames, durations, loop=loop, transparency=None)

    # Si el transparente queda justo detrás de la paleta, se le hace hueco. No
    # se rellena hasta 256: una paleta más larga es más bits por píxel.
    full_palette = palette + [0] * max(0, (transparent + 1) * 3 - len(palette))
    first = frames[0].copy()
    first.putpalette(full_palette)
    images = [first]
    previous = np.asarray(frames[0])
    for frame in frames[1:]:
        current = np.asarray(frame)
        out = np.where(current == previous, transparent, current).astype(np.uint8)
        image = Image.fromarray(out, "P")
        image.putpalette(full_palette)
        images.append(image)
        previous = current
    return _save(images, durations, loop=loop, transparency=transparent)


def local_palette_gif(
    frames: Sequence[Image.Image], durations: Durations, *, loop: bool = True
) -> bytes:
    """GIF a partir de fotogramas a color, con una paleta propia en cada uno.

    La paleta de cada fotograma (tabla de color local del GIF) sale solo de
    la caja que cambia respecto al anterior, así que los 255 colores se
    gastan en lo que se mueve y no en el fondo, que ya está pintado.

    Args:
        frames: Los fotogramas, todos del mismo tamaño, en cualquier modo
            que se pueda pasar a RGB.
        durations: Milisegundos de cada fotograma, o uno para todos.
        loop: Si el GIF se repite.

    Returns:
        Los bytes del GIF.

    Raises:
        ValueError: Si no hay fotogramas.
    """
    if not frames:
        raise ValueError("Hace falta al menos un fotograma.")
    # Primera pasada, en orden: qué cambia en cada fotograma respecto al anterior.
    changes: list[tuple[np.ndarray, tuple[int, int, int, int], Image.Image]] = []
    previous: np.ndarray | None = None
    for frame in frames:
        rgb_image = frame.convert("RGB")
        rgb = np.asarray(rgb_image, dtype=np.uint8)
        # Cada píxel como un entero de 32 bits: comparar es una sola pasada.
        packed = np.frombuffer(rgb_image.tobytes("raw", "RGBX"), dtype=np.uint32)
        packed = packed.reshape(rgb.shape[:2])
        changed = np.ones(rgb.shape[:2], dtype=bool) if previous is None else packed != previous
        previous = packed
        ys, xs = np.nonzero(changed)
        if len(ys) == 0:
            ys, xs = np.array([0]), np.array([0])
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        region = Image.fromarray(rgb[box[1] : box[3], box[0] : box[2]], "RGB")
        changes.append((changed, box, region))

    def indexed(change: tuple[np.ndarray, tuple[int, int, int, int], Image.Image]) -> Image.Image:
        changed, box, region = change
        palette = region.quantize(colors=LOCAL_COLORS, method=Image.Quantize.FASTOCTREE)
        # Solo la caja: sin tramado, el índice de un píxel no depende de los
        # demás, y fuera de la caja todo es transparente.
        out = np.full(changed.shape, LOCAL_TRANSPARENT, dtype=np.uint8)
        inside = np.asarray(region.quantize(palette=palette, dither=Image.Dither.NONE))
        window = (slice(box[1], box[3]), slice(box[0], box[2]))
        out[window] = np.where(changed[window], inside, LOCAL_TRANSPARENT)
        image = Image.fromarray(out, "P")
        colors = (palette.getpalette() or [])[: LOCAL_COLORS * 3]
        image.putpalette(colors + [0] * (768 - len(colors)))
        return image

    # Segunda pasada, en paralelo: la paleta de cada fotograma no depende de las
    # demás y Pillow suelta el GIL al calcularla (con 4 hilos, ~3 veces más rápido).
    with ThreadPoolExecutor(max_workers=QUANTIZE_THREADS) as pool:
        images = list(pool.map(indexed, changes))
    return _save(images, durations, loop=loop, transparency=LOCAL_TRANSPARENT)
