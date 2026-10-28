"""GIF animados rápidos: cada fotograma guarda solo los píxeles que cambian.

Los juegos del casino mandan un GIF por jugada, así que escribirlo tiene que
ser barato. Pillow, con sus opciones por defecto, optimiza cada fotograma en
Python píxel a píxel (reordena la paleta y busca qué ha cambiado), y en el
pachinko eso eran ~750 ms de los ~870 ms de toda la tanda. Aquí ese trabajo
lo hace numpy:

1. Se compara cada fotograma con el anterior y se recorta la caja que cambia.
   Un fotograma que no cambia nada no se escribe: su tiempo se suma al anterior.
2. Dentro de la caja, los píxeles que no cambian se pintan con un índice
   reservado que el GIF declara transparente. Como no se borra lo anterior
   (`disposal=1`), se ve lo que había debajo.
3. Cada fotograma (paleta y LZW) se codifica en un hilo aparte y el GIF se
   escribe a mano juntando los trozos. Pillow suelta el GIL al cuantizar y al
   comprimir, así que con 4 hilos va 2-3 veces más rápido que pasarle la lista
   entera a `Image.save`, que además compara otra vez cada fotograma entero
   con el anterior para recortarlo.
4. En `local_palette_gif` cada fotograma solo depende del anterior, así que se
   puede empezar antes de tener el GIF entero: `precode` lo codifica mientras
   Node pinta los siguientes (`bot.services.node_scene`).

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

El formato sigue lo que escribe Pillow (GIF89a, tabla de color global con la
paleta del primer fotograma, LZW de 8 bits), para que lo abra cualquier visor
que ya abría los de antes.
"""

from __future__ import annotations

import io
import math
import os
import struct
import weakref
from collections.abc import Sequence
from concurrent.futures import Executor, Future, ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageFile

#: Índice transparente de `local_palette_gif`: la paleta de cada fotograma usa los 255 primeros.
LOCAL_TRANSPARENT = 255
#: Colores de la paleta de cada fotograma en `local_palette_gif`.
LOCAL_COLORS = 255
#: Hilos que codifican los fotogramas a la vez.
QUANTIZE_THREADS = min(4, os.cpu_count() or 1)
#: `disposal=1`: lo que pinta un fotograma se queda debajo del siguiente.
_KEEP = 1

Durations = int | Sequence[int]
Box = tuple[int, int, int, int]


def _durations(durations: Durations, count: int) -> list[int]:
    return [durations] * count if isinstance(durations, int) else list(durations)


def _changed_box(changed: np.ndarray) -> Box | None:
    """La caja (x0, y0, x1, y1) de los píxeles que cambian; `None` si no cambia ninguno."""
    rows = np.flatnonzero(changed.any(axis=1))
    if len(rows) == 0:
        return None
    cols = np.flatnonzero(changed.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _color_table(palette: Sequence[int]) -> tuple[int, bytes]:
    """El campo de tamaño de una tabla de color y sus bytes, rellenos a potencia de 2.

    Mismo cálculo que Pillow: 2 colores como mínimo y la potencia de 2 que los cubre.
    """
    colors = max(2, len(palette) // 3)
    size = max(0, math.ceil(math.log2(colors)) - 1)
    return size, bytes(palette).ljust(3 * 2 ** (size + 1), b"\0")


def _lzw(indices: np.ndarray) -> bytes:
    """Los datos LZW (8 bits, en sub-bloques) de unos índices, con el codificador de Pillow."""
    image = Image.fromarray(indices, "P")
    buffer = io.BytesIO()
    ImageFile._save(image, buffer, [ImageFile._Tile("gif", (0, 0, *image.size), 0, "P")])
    return buffer.getvalue()


def _frame(
    indices: np.ndarray,
    box: Box,
    ms: int,
    transparency: int | None,
    palette: Sequence[int] | None = None,
) -> bytes:
    """Un fotograma: control (tiempo, transparencia), descriptor, paleta local y LZW."""
    return _control(ms, transparency) + _image(indices, box, palette)


def _control(ms: int, transparency: int | None) -> bytes:
    """La extensión de control de un fotograma: su tiempo y su índice transparente."""
    flags = (_KEEP << 2) | (1 if transparency is not None else 0)
    return b"!\xf9\x04" + struct.pack("<BHBB", flags, ms // 10, transparency or 0, 0)


def _image(indices: np.ndarray, box: Box, palette: Sequence[int] | None) -> bytes:
    """Descriptor, paleta local (si la hay) y LZW de un fotograma, sin su tiempo."""
    x0, y0, x1, y1 = box
    descriptor = b"," + struct.pack("<HHHH", x0, y0, x1 - x0, y1 - y0)
    table = b""
    if palette is None:
        descriptor += b"\0"
    else:
        size, table = _color_table(palette)
        descriptor += bytes([0x80 | size])
    return descriptor + table + b"\x08" + _lzw(indices) + b"\0"


def _write(size: tuple[int, int], palette: Sequence[int], frames: list[bytes], loop: bool) -> bytes:
    """El GIF entero: cabecera, tabla global, repetición, fotogramas y cierre."""
    table_size, table = _color_table(palette)
    header = b"GIF89a" + struct.pack("<HHBBB", *size, 0x80 | table_size, 0, 0) + table
    if loop:
        header += b"!\xff\x0bNETSCAPE2.0\x03\x01\0\0\0"
    return b"".join([header, *frames, b";"])


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

    # Sin hueco para el transparente (los 256 colores en uso), la caja que cambia
    # va entera. Si el transparente queda justo detrás de la paleta, se le hace
    # hueco; no se rellena hasta 256: una paleta más larga es más bits por píxel.
    transparent = _free_index(frames, len(palette) // 3)
    if transparent is not None:
        palette = palette + [0] * max(0, (transparent + 1) * 3 - len(palette))

    # En orden: qué cambia en cada fotograma respecto al anterior.
    jobs: list[tuple[np.ndarray, Box]] = []
    ms: list[int] = []
    previous: np.ndarray | None = None
    for frame, duration in zip(frames, _durations(durations, len(frames)), strict=True):
        current = np.asarray(frame)
        if previous is None:
            jobs.append((current, (0, 0, *frame.size)))
        else:
            changed = current != previous
            box = _changed_box(changed)
            if box is None:
                ms[-1] += duration
                continue
            window = (slice(box[1], box[3]), slice(box[0], box[2]))
            crop = current[window]
            if transparent is not None:
                crop = np.where(changed[window], crop, transparent).astype(np.uint8)
            jobs.append((crop, box))
        ms.append(duration)
        previous = current

    # En paralelo: el LZW de cada fotograma no depende de los demás.
    with ThreadPoolExecutor(max_workers=QUANTIZE_THREADS) as pool:
        blocks = list(
            pool.map(lambda job, duration: _frame(job[0], job[1], duration, transparent), jobs, ms)
        )
    return _write(frames[0].size, palette, blocks, loop)


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
    # Primera pasada, en orden: qué cambia en cada fotograma respecto al anterior
    # (salvo los que `precode` ya empezó mientras se pintaban). Segunda, en
    # paralelo: la paleta y el LZW, que Pillow calcula soltando el GIL.
    with ThreadPoolExecutor(max_workers=QUANTIZE_THREADS) as pool:
        jobs: list[Future[_Coded]] = []
        before: Image.Image | None = None
        previous: np.ndarray | None = None
        for frame in frames:
            job = _precoded(before, frame)
            if job is not None:
                previous = None
            else:
                rgb = _rgb(frame)
                packed = _packed(rgb)
                if before is not None and previous is None:
                    previous = _packed(_rgb(before))
                job = _submit(pool, previous, packed, rgb)
                previous = packed
            jobs.append(job)
            before = frame
        coded = [job.result() for job in jobs]
    # El tiempo de un fotograma que no cambia nada se suma al anterior.
    blocks: list[tuple[int, bytes]] = []
    for part, duration in zip(coded, _durations(durations, len(frames)), strict=True):
        if part is None:
            blocks[-1] = (blocks[-1][0] + duration, blocks[-1][1])
        else:
            blocks.append((duration, part[0]))
    frames_bytes = [_control(ms, LOCAL_TRANSPARENT) + image for ms, image in blocks]
    first = coded[0]
    assert first is not None
    return _write(frames[0].size, first[1], frames_bytes, loop)


#: Lo que se guarda en un fotograma con `precode`: a qué fotograma sigue y su trabajo.
_PRECODE_ATTRIBUTE = "_gif_precode"


def precode(before: Image.Image | None, frame: Image.Image, pool: Executor) -> None:
    """Empieza ya en `pool` el trabajo de `frame` para un futuro `local_palette_gif`.

    Para codificar mientras se pintan los fotogramas siguientes (ver
    `bot.services.node_scene`). Solo se aprovecha si en el GIF `frame` va justo
    detrás de `before` (`None` si es el primero): el autobús, por ejemplo, monta
    dos GIF con un tramo común. Se guarda en el propio fotograma y se va con él.
    """
    previous = None if before is None else weakref.ref(before)
    setattr(frame, _PRECODE_ATTRIBUTE, (previous, pool.submit(_code, before, frame)))


def _precoded(before: Image.Image | None, frame: Image.Image) -> Future[_Coded] | None:
    """El trabajo que `precode` empezó para `frame` detrás de `before`, si lo hay."""
    saved = getattr(frame, _PRECODE_ATTRIBUTE, None)
    if saved is None:
        return None
    previous, job = saved
    follows = previous() if previous is not None else None
    return job if follows is before else None


#: Un fotograma codificado sin su tiempo (descriptor, paleta y LZW) y su paleta;
#: `None` si es igual que el anterior.
_Coded = tuple[bytes, list[int]] | None


def _rgb(image: Image.Image) -> Image.Image:
    return image if image.mode == "RGB" else image.convert("RGB")


def _packed(rgb: Image.Image) -> np.ndarray:
    """Cada píxel como un entero de 32 bits: comparar dos fotogramas es una sola pasada."""
    packed = np.frombuffer(rgb.tobytes("raw", "RGBX"), dtype=np.uint32)
    return packed.reshape(rgb.height, rgb.width)


def _code(before: Image.Image | None, frame: Image.Image) -> _Coded:
    """Lo que cambia `frame` respecto a `before`, entero (el trabajo de `precode`)."""
    rgb = _rgb(frame)
    previous = None if before is None else _packed(_rgb(before))
    return _submit(None, previous, _packed(rgb), rgb).result()


def _submit(
    pool: Executor | None, previous: np.ndarray | None, packed: np.ndarray, rgb: Image.Image
) -> Future[_Coded]:
    """Compara con el anterior aquí y deja la paleta y el LZW a `pool` (o los hace ya)."""
    if previous is None:
        window, box, region = None, (0, 0, *rgb.size), rgb
    else:
        changed = packed != previous
        found = _changed_box(changed)
        if found is None:
            done: Future[_Coded] = Future()
            done.set_result(None)
            return done
        box = found
        window = changed[box[1] : box[3], box[0] : box[2]]
        region = rgb.crop(box)
    if pool is not None:
        return pool.submit(_quantize, window, box, region)
    now: Future[_Coded] = Future()
    now.set_result(_quantize(window, box, region))
    return now


def _quantize(window: np.ndarray | None, box: Box, region: Image.Image) -> _Coded:
    """La paleta propia de lo que cambia y su LZW.

    Dentro de la caja, los píxeles que no cambian (`window` falso) van con el
    índice transparente. El primero (`window` es `None`) va entero y con la
    tabla global, como lo escribe Pillow.
    """
    palette = region.quantize(colors=LOCAL_COLORS, method=Image.Quantize.FASTOCTREE)
    # Sin tramado, el índice de un píxel no depende de los demás.
    indices = np.asarray(region.quantize(palette=palette, dither=Image.Dither.NONE))
    if window is not None:
        indices = np.where(window, indices, LOCAL_TRANSPARENT).astype(np.uint8)
    colors = (palette.getpalette() or [])[: LOCAL_COLORS * 3]
    colors += [0] * (768 - len(colors))
    return _image(indices, box, None if window is None else colors), colors


class GifStream:
    """GIF con paleta local en cada fotograma que se escribe por tramos.

    Hace lo mismo que `local_palette_gif`, pero sin tener todos los fotogramas a la
    vez: cada `add` codifica un tramo (en paralelo, aprovechando lo que `precode` ya
    empezó) y solo se guardan sus bytes y el último fotograma, que es contra el que se
    compara el primero del tramo siguiente. Para animaciones largas (el juego del
    pico): con `local_palette_gif`, cientos de fotogramas de 288 × 512 ocupaban cientos
    de MB a la vez. `size` dice cuánto pesa ya el GIF, para cortarlo antes de que pase
    del límite de Discord. Juntar tramos da los mismos bytes que pasar todos los
    fotogramas de una vez a `local_palette_gif`.
    """

    def __init__(self, size: tuple[int, int], *, loop: bool = True) -> None:
        self.dimensions = size
        self.loop = loop
        self.frames = 0
        self._last: Image.Image | None = None
        self._palette: list[int] | None = None
        self._blocks: list[tuple[int, bytes]] = []
        self._bytes = 0

    @property
    def size(self) -> int:
        """Bytes que ocupa ya el GIF (sin la cabecera ni el cierre, que son unos pocos)."""
        return self._bytes

    @property
    def duration(self) -> int:
        """Milisegundos que dura el GIF entero."""
        return sum(ms for ms, _ in self._blocks)

    @property
    def last(self) -> Image.Image | None:
        """El último fotograma añadido."""
        return self._last

    def add(self, frames: Sequence[Image.Image], durations: Durations) -> None:
        """Codifica un tramo de fotogramas detrás de los que ya hay."""
        if not frames:
            return
        with ThreadPoolExecutor(max_workers=QUANTIZE_THREADS) as pool:
            jobs: list[Future[_Coded]] = []
            before = self._last
            previous: np.ndarray | None = None
            for frame in frames:
                if frame.size != self.dimensions:
                    raise ValueError("Todos los fotogramas tienen que tener el tamaño del GIF.")
                job = _precoded(before, frame)
                if job is not None:
                    previous = None
                else:
                    rgb = _rgb(frame)
                    packed = _packed(rgb)
                    if before is not None and previous is None:
                        previous = _packed(_rgb(before))
                    job = _submit(pool, previous, packed, rgb)
                    previous = packed
                jobs.append(job)
                before = frame
            coded = [job.result() for job in jobs]
        for part, duration in zip(coded, _durations(durations, len(frames)), strict=True):
            if part is None:
                ms, image = self._blocks[-1]
                self._blocks[-1] = (ms + duration, image)
            else:
                if self._palette is None:
                    self._palette = part[1]
                self._blocks.append((duration, part[0]))
                self._bytes += len(part[0]) + 8
        self.frames += len(frames)
        self._last = frames[-1]

    def finish(self) -> bytes:
        """Los bytes del GIF.

        Raises:
            ValueError: Si no se ha añadido ningún fotograma.
        """
        if self._palette is None:
            raise ValueError("Hace falta al menos un fotograma.")
        blocks = [_control(ms, LOCAL_TRANSPARENT) + image for ms, image in self._blocks]
        return _write(self.dimensions, self._palette, blocks, self.loop)
