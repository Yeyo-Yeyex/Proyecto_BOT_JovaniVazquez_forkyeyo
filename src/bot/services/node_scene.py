"""Escenas de canvas pintadas con Node y Skia, sin navegador.

Las escenas (`assets/*/escena.html`) solo usan canvas 2D, así que no necesitan
un Chromium entero: `assets/escena_node.cjs` carga su `<script>` en Node con
`@napi-rs/canvas` (Skia, el motor que pinta el canvas en Chrome) y devuelve
los recuadros en RGBA crudo por una tubería. Frente a `BrowserScene` se ahorra
comprimir cada fotograma a PNG en un hilo, pasarlo en base64 y descomprimirlo
en Python, y cada proceso ocupa bastante menos memoria que un Chromium.

Los procesos de Node se comparten entre todos los juegos (`_Pool`): cada uno
puede pintar cualquier escena, que carga la primera vez que se la piden.

- Siempre hay `MIN_WORKERS` abierto (desde que arranca el bot, `warm`): ningún
  dibujo empieza en frío.
- En cuanto un dibujo empieza, se abren los que le faltan hasta `MAX_WORKERS`
  (los hilos del NAS). Los de más se cierran tras `IDLE_SECONDS` sin usarse.
- Todos colaboran en cada dibujo (`NodeScene.render`): empieza con los libres,
  a partes iguales con los dibujos que ya están en marcha, y cada proceso que
  se abre o se queda libre a mitad se une y se lleva la mitad de lo que le
  queda al que más trabajo tiene pendiente. Cada tramo empieza con su propio
  `setup(meta)` y su primer fotograma sale entero, el mismo contrato que
  `BrowserScene.render`. Una escena que arrastra estado de un fotograma al
  siguiente (el polvo de los caballos) lo recrea con los fotogramas de antes
  (`warmup`).
- Los fotogramas se montan en un hilo según llegan y cada uno empieza a
  codificarse para el GIF (`bot.utils.gif.precode`) mientras Node pinta los
  siguientes. El bucle de eventos solo lee las tuberías.

`NodeScene.render` devuelve `None` si no hay Node, para que quien dibuja pase a
su plan B. Devuelve cada fotograma entero, ya abierto (`image`) en vez del PNG
(`u`); `assemble` los monta igual.

Hace falta `node` en el PATH y el paquete `@napi-rs/canvas` (`package.json` de
la raíz; en Docker, en `NODE_PATH`).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import queue
import shutil
import time
import weakref
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image

from bot.services.browser_scene import IDLE_SECONDS, Patch, png_bytes
from bot.services.telemetry import report_render
from bot.utils.gif import QUANTIZE_THREADS, precode

logger = logging.getLogger(__name__)

T = TypeVar("T")

RUNNER = Path(__file__).resolve().parent.parent / "assets" / "escena_node.cjs"
#: Procesos de Node abiertos como mucho: los hilos del NAS.
MAX_WORKERS = 4
#: Procesos que se quedan abiertos aunque no se usen, para no arrancar en frío.
MIN_WORKERS = 1
#: Lo que puede tardar Node en arrancar.
START_TIMEOUT = 15.0
#: Fotogramas por petición: cuanto menos, antes puede otro proceso llevarse el resto.
BATCH = 6
#: Fotogramas que se lleva como poco quien se une a un dibujo: cada tramo nuevo
#: cuesta un `setup` y un fotograma entero.
MIN_STEAL = 4


def patch_png(patch: Patch) -> bytes:
    """El PNG de un recuadro, venga de Chromium (ya en PNG, `u`) o de Node (`image`)."""
    if "image" not in patch:
        return png_bytes(patch)
    buffer = io.BytesIO()
    patch["image"].save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue()


def _open(patch: Patch) -> Image.Image:
    """La imagen de un recuadro de Node en RGBA crudo (`raw`)."""
    w, h = patch["size"]
    # El fondo de las escenas es opaco: el alfa sobra.
    return Image.frombuffer("RGBA", (w, h), patch["raw"], "raw", "RGBA", 0, 1).convert("RGB")


class NodeSceneError(RuntimeError):
    """La escena falló dentro de Node (el mensaje trae su traza)."""


class _Process:
    """Un `node escena_node.cjs` vivo, que pinta las escenas que le pidan."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.busy = False
        self.last_used = time.monotonic()

    @classmethod
    async def start(cls, node: str) -> _Process:
        proc = await asyncio.create_subprocess_exec(
            node,
            "--expose-gc",
            str(RUNNER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self = cls(proc)
        try:
            header = await asyncio.wait_for(self._header(), START_TIMEOUT)
        except BaseException:
            await self.close()
            raise
        if not header.get("ready"):
            raise NodeSceneError(f"Node no ha arrancado el pintor: {header}")
        return self

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def _header(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        line = await self.proc.stdout.readline()
        if not line:
            assert self.proc.stderr is not None
            error = (await self.proc.stderr.read()).decode(errors="replace").strip()
            raise NodeSceneError(f"Node se ha cerrado: {error or 'sin mensaje'}")
        return json.loads(line)

    async def call(
        self, request: dict[str, Any], *, decode: bool = True
    ) -> tuple[dict[str, Any], list[Patch]]:
        """Manda una petición y lee su respuesta (y los píxeles, si los hay).

        Con `decode=False` los recuadros llevan los píxeles crudos (`raw` y
        `size`) para abrirlos fuera del bucle de eventos (`_open`).
        """
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(request).encode() + b"\n")
        await self.proc.stdin.drain()
        header = await self._header()
        if not header.get("ok"):
            raise NodeSceneError(header.get("error", "error sin mensaje"))
        patches: list[Patch] = []
        for x, y, w, h in header.get("frames", []):
            raw = await self.proc.stdout.readexactly(w * h * 4)
            patch: Patch = {"x": x, "y": y, "size": (w, h), "raw": raw}
            patches.append({"x": x, "y": y, "image": _open(patch)} if decode else patch)
        return header, patches

    def kill(self) -> None:
        """Mata el proceso sin esperarlo (al cortar un dibujo a medias)."""
        if self.alive:
            self.proc.kill()

    async def close(self) -> None:
        self.kill()
        await self.proc.wait()


@dataclass(eq=False)
class _Draw:
    """Un dibujo en marcha: cuántos procesos le sirven y cuáles tiene.

    `more` dice si aún le queda trabajo que repartir con otro proceso.
    """

    want: int
    more: Callable[[], bool] = lambda: False
    workers: set[_Process] = field(default_factory=set)
    done: bool = False


class _Pool:
    """Los procesos de Node de un bucle de eventos, compartidos por todas las escenas.

    Args:
        node: Ejecutable de Node (si no, el `node` del PATH).
        idle_seconds: Cuánto se deja abierto un proceso de más sin usarlo.
    """

    def __init__(self, node: str | None, idle_seconds: float) -> None:
        self.node = node
        self.idle_seconds = idle_seconds
        self.workers: list[_Process] = []
        self.starting = 0
        #: Dibujos en marcha, para repartir los procesos libres a partes iguales.
        self.draws: list[_Draw] = []
        #: Escenas que lo usan; sin ninguna (y sin `warm`) se cierra.
        self.users: set[int] = set()
        self.kept = False
        self._changed = asyncio.Condition()
        self._reaper: asyncio.TimerHandle | None = None
        self._background: set[asyncio.Task[None]] = set()

    @property
    def drawing(self) -> int:
        """Dibujos en marcha."""
        return len(self.draws)

    def _idle(self) -> list[_Process]:
        return [worker for worker in self.workers if worker.alive and not worker.busy]

    def _share(self, draw: _Draw) -> int:
        """Los procesos que le tocan a `draw`: a partes iguales con los demás dibujos."""
        return max(1, min(draw.want, MAX_WORKERS // max(1, len(self.draws))))

    async def _spawn(self) -> _Process:
        self.starting += 1
        return await self._start()

    async def _start(self) -> _Process:
        """Arranca un proceso cuyo hueco ya está apuntado en `starting`."""
        try:
            node = self.node or shutil.which("node")
            if node is None:
                raise NodeSceneError("No hay `node` en el PATH")
            worker = await _Process.start(node)
        finally:
            self.starting -= 1
        self.workers.append(worker)
        return worker

    async def _spare(self) -> None:
        try:
            await self._start()
        except Exception:
            # El siguiente dibujo lo intenta otra vez y, si falla, pasa a su plan B.
            logger.debug("No se ha podido abrir el proceso de Node de reserva", exc_info=True)
            return
        async with self._changed:
            self._changed.notify_all()

    def _grow(self) -> None:
        """Abre los procesos que piden los dibujos en marcha (hasta `MAX_WORKERS`).

        El hueco se apunta ya y no al arrancar la tarea: si no, otro dibujo
        podría abrir uno más entretanto y pasar de `MAX_WORKERS`.
        """
        wanted = min(MAX_WORKERS, sum(draw.want for draw in self.draws))
        while len(self.workers) + self.starting < wanted:
            self.starting += 1
            self._in_background(self._spare())

    def _in_background(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def warm(self) -> None:
        """Deja abiertos los `MIN_WORKERS`; desde entonces el grupo no se cierra solo."""
        self.kept = True
        self.workers = [worker for worker in self.workers if worker.alive]
        while len(self.workers) < MIN_WORKERS:
            await self._spawn()

    async def start(self, draw: _Draw) -> list[_Process]:
        """Apunta `draw` y le da los libres que le tocan (al menos uno, esperando si hace falta).

        Abre en segundo plano los que faltan para que se le unan a mitad (`recruit`).
        """
        async with self._changed:
            self.draws.append(draw)
            try:
                while True:
                    self.workers = [worker for worker in self.workers if worker.alive]
                    idle = self._idle()
                    if idle:
                        break
                    if len(self.workers) + self.starting < MAX_WORKERS:
                        idle = [await self._spawn()]
                        break
                    await self._changed.wait()
            except BaseException:
                self.draws.remove(draw)
                self._changed.notify_all()
                raise
            taken = idle[: self._share(draw)]
            for worker in taken:
                worker.busy = True
            draw.workers.update(taken)
            self._grow()
            return taken

    async def recruit(self, draw: _Draw) -> _Process | None:
        """Otro proceso para `draw` en cuanto quede uno libre; `None` cuando `draw` acaba."""
        async with self._changed:
            while not draw.done:
                idle = self._idle()
                if idle and len(draw.workers) < self._share(draw) and draw.more():
                    worker = idle[0]
                    worker.busy = True
                    draw.workers.add(worker)
                    return worker
                await self._changed.wait()
            return None

    async def give_back(self, draw: _Draw, worker: _Process) -> None:
        """Devuelve al grupo un proceso que ya no tiene nada que pintar en `draw`."""
        async with self._changed:
            draw.workers.discard(worker)
            worker.busy = False
            worker.last_used = time.monotonic()
            self._changed.notify_all()
        self._schedule_reap()

    async def finish(self, draw: _Draw) -> None:
        """Da `draw` por terminado y devuelve los procesos que le queden."""
        async with self._changed:
            draw.done = True
            if draw in self.draws:
                self.draws.remove(draw)
            now = time.monotonic()
            for worker in draw.workers:
                worker.busy = False
                worker.last_used = now
            draw.workers.clear()
            self._changed.notify_all()
        self._schedule_reap()

    def drop(self, draw: _Draw) -> None:
        """Mata los procesos de `draw`, que pueden haber quedado con píxeles sin leer."""
        for worker in draw.workers:
            worker.kill()
            if worker in self.workers:
                self.workers.remove(worker)
            self._in_background(worker.close())
        draw.workers.clear()

    def _schedule_reap(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        loop = asyncio.get_running_loop()
        self._reaper = loop.call_later(self.idle_seconds, self._reap)

    def _reap(self) -> None:
        """Cierra los procesos libres de más que llevan `idle_seconds` sin pintar."""
        self._reaper = None
        limit = time.monotonic() - self.idle_seconds
        for worker in sorted(self._idle(), key=lambda worker: worker.last_used):
            if len(self.workers) <= MIN_WORKERS:
                break
            if worker.last_used <= limit:
                self.workers.remove(worker)
                self._in_background(worker.close())
        if len(self.workers) > MIN_WORKERS:
            self._schedule_reap()

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for task in tuple(self._background):
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        workers, self.workers = self.workers, []
        for worker in workers:
            await worker.close()


#: Un grupo por bucle de eventos y ejecutable de Node: los procesos de asyncio
#: están atados al bucle que los arrancó (las pruebas abren varios).
_pools: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str | None, _Pool]] = (
    weakref.WeakKeyDictionary()
)


def _pool(node: str | None, idle_seconds: float = IDLE_SECONDS) -> _Pool:
    by_node = _pools.setdefault(asyncio.get_running_loop(), {})
    if node not in by_node:
        by_node[node] = _Pool(node, idle_seconds)
    return by_node[node]


async def warm(node: str | None = None) -> None:
    """Abre ya el proceso de Node que siempre queda abierto (al arrancar el bot)."""
    try:
        await _pool(node).warm()
    except Exception:
        # Sin Node, cada escena lo descubre en su primer dibujo y pasa a su plan B.
        logger.warning("No se ha podido abrir Node al arrancar", exc_info=True)


async def shutdown() -> None:
    """Cierra todos los procesos de Node de este bucle (al apagar el bot)."""
    for pool in _pools.pop(asyncio.get_running_loop(), {}).values():
        await pool.close()


@dataclass(eq=False)
class _Span:
    """Un tramo seguido de fotogramas: `pos` es el primero aún sin pedir a Node."""

    pos: int
    end: int


def _steal(spans: list[_Span]) -> _Span | None:
    """La mitad final del tramo con más trabajo pendiente, si merece un proceso más."""
    if not spans:
        return None
    victim = max(spans, key=lambda span: span.end - span.pos)
    left = victim.end - victim.pos
    if left < 2 * MIN_STEAL:
        return None
    stolen = _Span(victim.pos + left // 2, victim.end)
    victim.end = stolen.pos
    spans.append(stolen)
    return stolen


class _Assembler:
    """Monta en un hilo los fotogramas enteros según llegan de los procesos, en orden.

    Cada fotograma empieza a codificarse para el GIF en cuanto se monta
    (`precode`), si `gif` lo pide: un dibujo de un solo fotograma es una imagen
    quieta y no lo necesita.
    """

    def __init__(self, count: int, *, gif: bool) -> None:
        self.count = count
        self.gif = gif
        self._queue: queue.SimpleQueue[tuple[int, Patch] | None] = queue.SimpleQueue()

    def add(self, index: int, patch: Patch) -> None:
        self._queue.put((index, patch))

    def close(self) -> None:
        self._queue.put(None)

    def run(self) -> list[Image.Image]:
        pool = ThreadPoolExecutor(max_workers=QUANTIZE_THREADS) if self.gif else None
        try:
            frames: list[Image.Image] = []
            waiting: dict[int, Patch] = {}
            while (item := self._queue.get()) is not None:
                waiting[item[0]] = item[1]
                while len(frames) in waiting:
                    patch = waiting.pop(len(frames))
                    image = _open(patch)
                    before = frames[-1] if frames else None
                    # Un recuadro que no es entero va encima del fotograma anterior.
                    if before is not None and image.size != before.size:
                        frame = before.copy()
                        frame.paste(image, (patch["x"], patch["y"]))
                        image = frame
                    if pool is not None:
                        precode(before, image, pool)
                    frames.append(image)
            return frames
        finally:
            # Lo que queda por codificar sigue en sus hilos; el GIF lo espera.
            if pool is not None:
                pool.shutdown(wait=False)


class NodeScene:
    """Una escena pintada por los procesos de Node compartidos; `None` en `render` si no hay Node.

    Args:
        scene: El HTML de la escena.
        name: Para los mensajes del log («el pintor de la moneda»).
        node: Ejecutable de Node (si no, el `node` del PATH).
        idle_seconds: Cuánto se dejan abiertos los procesos de más sin usarlos.
    """

    def __init__(
        self,
        scene: Path,
        *,
        name: str,
        node: str | None = None,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        self.scene = scene
        self.name = name
        self.node = node
        self.idle_seconds = idle_seconds
        self._pool: _Pool | None = None
        #: Tras un fallo no se reintenta en cada dibujo: se usa el plan B.
        self.disabled = False

    def _request(self, call: str, args: list[Any] | None = None, **extra: Any) -> dict[str, Any]:
        return {"scene": str(self.scene), "call": call, "args": args or [], **extra}

    def _use_pool(self) -> _Pool:
        if self._pool is None:
            self._pool = _pool(self.node, self.idle_seconds)
            self._pool.users.add(id(self))
        return self._pool

    async def close(self) -> None:
        """Deja de usar los procesos; si nadie más los usa (ni `warm`), se cierran."""
        pool, self._pool = self._pool, None
        if pool is None:
            return
        pool.users.discard(id(self))
        if not pool.users and not pool.kept:
            await pool.close()
            by_node = _pools.get(asyncio.get_running_loop(), {})
            if by_node.get(self.node) is pool:
                del by_node[self.node]

    async def evaluate(self, expression: str) -> Any:
        """Evalúa `expression` en la escena (para las pruebas); `None` si no hay Node."""

        async def work(worker: _Process) -> Any:
            header, _ = await worker.call(self._request(expression, eval=True))
            return header.get("value")

        return await self._run(work)

    async def render(
        self,
        meta: dict[str, Any],
        states: list[dict[str, Any]],
        *,
        procs: int = MAX_WORKERS,
        warmup: int = 0,
    ) -> list[Patch] | None:
        """Cada fotograma entero (`image`, ver `browser_scene.assemble`); `None` si no hay Node.

        El dibujo se reparte entre los procesos (`procs` como mucho), que se
        unen según quedan libres (ver el módulo). Cada tramo empieza con
        `setup(meta)` y `renderFrames(estados, primero)`; con `warmup`, esa
        primera llamada recibe además los `warmup` estados de antes del tramo,
        para que la escena recree lo que arrastra de un fotograma a otro.
        """
        if self.disabled:
            return None
        if not states:
            return []
        pool = self._use_pool()
        spans: list[_Span] = []
        draw = _Draw(
            max(1, min(procs, len(states))),
            more=lambda: any(span.end - span.pos >= 2 * MIN_STEAL for span in spans),
        )
        try:
            first = await pool.start(draw)
        except Exception:
            logger.warning("%s no está disponible; se usa el plan B", self.name, exc_info=True)
            self.disabled = True
            return None
        start = time.perf_counter()
        assembler = _Assembler(len(states), gif=len(states) > 1)
        assembling = asyncio.ensure_future(asyncio.to_thread(assembler.run))
        tasks: list[asyncio.Task[None]] = []

        async def paint(worker: _Process, span: _Span | None) -> None:
            fresh = False
            if span is None:
                # Quien se une a mitad carga la escena antes de llevarse trabajo: si
                # no, lo que se lleva esperaría a que la cargue en vez de pintarse ya.
                await worker.call(self._request("setup", [meta]))
                fresh = True
                span = _steal(spans)
            while span is not None:
                if not fresh:
                    await worker.call(self._request("setup", [meta]))
                fresh = False
                prior = states[max(0, span.pos - warmup) : span.pos]
                while span.pos < span.end:
                    begin = span.pos
                    # Se apunta antes de pedirlo: lo que queda es lo que se puede llevar otro.
                    span.pos = min(begin + BATCH, span.end)
                    args: list[Any] = [states[begin : span.pos], begin]
                    if prior:
                        args.append(prior)
                        prior = []
                    request = self._request("renderFrames", args)
                    _, patches = await worker.call(request, decode=False)
                    for offset, patch in enumerate(patches):
                        assembler.add(begin + offset, patch)
                span = _steal(spans)
            await pool.give_back(draw, worker)

        async def recruit() -> None:
            while (worker := await pool.recruit(draw)) is not None:
                tasks.append(asyncio.create_task(paint(worker, None)))

        size = -(-len(states) // len(first))
        for worker, begin in zip(first, range(0, len(states), size), strict=False):
            spans.append(_Span(begin, min(begin + size, len(states))))
            tasks.append(asyncio.create_task(paint(worker, spans[-1])))
        recruiter = asyncio.create_task(recruit())
        try:
            while running := [task for task in tasks if not task.done()]:
                await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in tasks:
                    if task.done():
                        task.result()
            recruiter.cancel()
            await pool.finish(draw)
            assembler.close()
            frames = await assembling
            if len(frames) != len(states):
                raise NodeSceneError(f"Faltan fotogramas: {len(frames)} de {len(states)}")
        except BaseException as error:
            # Cortado o fallido a mitad de una petición, un proceso puede tener
            # píxeles sin leer en la tubería y la siguiente leería basura. Se
            # matan (el grupo abre otros, ~0,2 s); si solo se ha cortado, la
            # escena no se apaga.
            recruiter.cancel()
            for task in tasks:
                task.cancel()
            assembler.close()
            pool.drop(draw)
            await asyncio.shield(pool.finish(draw))
            if not isinstance(error, Exception):
                raise
            logger.warning("%s no está disponible; se usa el plan B", self.name, exc_info=True)
            self.disabled = True
            return None
        report_render(self.name, (time.perf_counter() - start) * 1000)
        return [{"x": 0, "y": 0, "image": frame} for frame in frames]

    async def sequence(self, calls: list[tuple[str, list[Any]]]) -> list[list[Patch]] | None:
        """Hace `calls` (función de la escena y argumentos) en orden en un solo proceso.

        Para las escenas que no siguen `setup`/`renderFrames`. Devuelve, por
        llamada, las imágenes que devolvió (vacío si no devolvió ninguna);
        `None` si no hay Node.
        """

        async def work(worker: _Process) -> list[list[Patch]]:
            results = []
            for function, args in calls:
                _, patches = await worker.call(self._request(function, args))
                results.append(patches)
            return results

        return await self._run(work)

    async def _run(self, work: Callable[[_Process], Awaitable[T]]) -> T | None:
        """Hace `work` con un proceso del grupo; `None` (y plan B) si Node falla."""
        if self.disabled:
            return None
        pool = self._use_pool()
        draw = _Draw(1)
        try:
            workers = await pool.start(draw)
        except Exception:
            logger.warning("%s no está disponible; se usa el plan B", self.name, exc_info=True)
            self.disabled = True
            return None
        # Se mide con el proceso ya en la mano: lo que tarda pintar, no la cola.
        start = time.perf_counter()
        try:
            result = await work(workers[0])
        except BaseException as error:
            # Igual que en `render`: un proceso cortado a medias no se reutiliza.
            pool.drop(draw)
            await asyncio.shield(pool.finish(draw))
            if not isinstance(error, Exception):
                raise
            logger.warning("%s no está disponible; se usa el plan B", self.name, exc_info=True)
            self.disabled = True
            return None
        await pool.finish(draw)
        report_render(self.name, (time.perf_counter() - start) * 1000)
        return result
