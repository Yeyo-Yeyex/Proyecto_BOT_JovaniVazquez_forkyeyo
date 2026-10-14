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
- Si un dibujo se queda con todos los libres, se abre otro de reserva para el
  siguiente, hasta `MAX_WORKERS` (los hilos del NAS). Los de más se cierran
  tras `IDLE_SECONDS` sin usarse.
- Cada dibujo se reparte entre los procesos libres, a partes iguales con los
  dibujos que ya están en marcha: quien juega solo pinta con 4 y, con tres
  jugando a la vez, cada uno tiene el suyo. Cada proceso pinta un tramo
  seguido de estados tras su propio `setup(meta)` y su primer fotograma sale
  entero, el mismo contrato que `BrowserScene.render`.

`NodeScene.render` devuelve `None` si no hay Node, para que quien dibuja pase a
su plan B. Los recuadros que devuelve llevan la imagen ya abierta (`image`) en
vez del PNG (`u`); `assemble` los monta igual.

Hace falta `node` en el PATH y el paquete `@napi-rs/canvas` (`package.json` de
la raíz; en Docker, en `NODE_PATH`).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import time
import weakref
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image

from bot.services.browser_scene import BATCH, IDLE_SECONDS, Patch, png_bytes
from bot.services.telemetry import report_render

logger = logging.getLogger(__name__)

T = TypeVar("T")

RUNNER = Path(__file__).resolve().parent.parent / "assets" / "escena_node.cjs"
#: Procesos de Node abiertos como mucho: los hilos del NAS.
MAX_WORKERS = 4
#: Procesos que se quedan abiertos aunque no se usen, para no arrancar en frío.
MIN_WORKERS = 1
#: Lo que puede tardar Node en arrancar.
START_TIMEOUT = 15.0


def patch_png(patch: Patch) -> bytes:
    """El PNG de un recuadro, venga de Chromium (ya en PNG, `u`) o de Node (`image`)."""
    if "image" not in patch:
        return png_bytes(patch)
    buffer = io.BytesIO()
    patch["image"].save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue()


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

    async def call(self, request: dict[str, Any]) -> tuple[dict[str, Any], list[Patch]]:
        """Manda una petición y lee su respuesta (y los píxeles, si los hay)."""
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(request).encode() + b"\n")
        await self.proc.stdin.drain()
        header = await self._header()
        if not header.get("ok"):
            raise NodeSceneError(header.get("error", "error sin mensaje"))
        patches: list[Patch] = []
        for x, y, w, h in header.get("frames", []):
            raw = await self.proc.stdout.readexactly(w * h * 4)
            # El fondo de las escenas es opaco: el alfa sobra.
            image = Image.frombuffer("RGBA", (w, h), raw, "raw", "RGBA", 0, 1).convert("RGB")
            patches.append({"x": x, "y": y, "image": image})
        return header, patches

    def kill(self) -> None:
        """Mata el proceso sin esperarlo (al cortar un dibujo a medias)."""
        if self.alive:
            self.proc.kill()

    async def close(self) -> None:
        self.kill()
        await self.proc.wait()


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
        self.drawing = 0
        #: Escenas que lo usan; sin ninguna (y sin `warm`) se cierra.
        self.users: set[int] = set()
        self.kept = False
        self._changed = asyncio.Condition()
        self._reaper: asyncio.TimerHandle | None = None
        self._background: set[asyncio.Task[None]] = set()

    def _idle(self) -> list[_Process]:
        return [worker for worker in self.workers if worker.alive and not worker.busy]

    async def _spawn(self) -> _Process:
        node = self.node or shutil.which("node")
        if node is None:
            raise NodeSceneError("No hay `node` en el PATH")
        self.starting += 1
        try:
            worker = await _Process.start(node)
        finally:
            self.starting -= 1
        self.workers.append(worker)
        return worker

    async def _spare(self) -> None:
        try:
            await self._spawn()
        except Exception:
            # El siguiente dibujo lo intenta otra vez y, si falla, pasa a su plan B.
            logger.debug("No se ha podido abrir el proceso de Node de reserva", exc_info=True)
            return
        async with self._changed:
            self._changed.notify_all()

    def _ensure_spare(self) -> None:
        """Abre otro proceso si no queda ninguno libre (hasta `MAX_WORKERS`)."""
        if self._idle() or len(self.workers) + self.starting >= MAX_WORKERS:
            return
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

    async def acquire(self, wanted: int) -> list[_Process]:
        """Hasta `wanted` procesos libres (al menos uno), esperando si no queda ninguno."""
        async with self._changed:
            while True:
                self.workers = [worker for worker in self.workers if worker.alive]
                idle = self._idle()
                if idle:
                    break
                if len(self.workers) + self.starting < MAX_WORKERS:
                    idle = [await self._spawn()]
                    break
                await self._changed.wait()
            share = max(1, MAX_WORKERS // (self.drawing + 1))
            taken = idle[: max(1, min(wanted, share))]
            for worker in taken:
                worker.busy = True
            self.drawing += 1
            self._ensure_spare()
            return taken

    async def release(self, workers: list[_Process]) -> None:
        """Devuelve los procesos de un dibujo terminado (los que sigan vivos) al grupo."""
        async with self._changed:
            now = time.monotonic()
            for worker in workers:
                worker.busy = False
                worker.last_used = now
            self.drawing -= 1
            self._changed.notify_all()
        self._schedule_reap()

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

    def discard(self, workers: list[_Process]) -> None:
        """Mata procesos que pueden haber quedado con píxeles sin leer en la tubería."""
        for worker in workers:
            worker.kill()
            if worker in self.workers:
                self.workers.remove(worker)
            self._in_background(worker.close())

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

        async def work(workers: list[_Process]) -> Any:
            header, _ = await workers[0].call(self._request(expression, eval=True))
            return header.get("value")

        return await self._run(1, work)

    async def render(
        self, meta: dict[str, Any], states: list[dict[str, Any]], *, procs: int = MAX_WORKERS
    ) -> list[Patch] | None:
        """Recuadros de cada fotograma (ver `browser_scene.assemble`); `None` si no hay Node.

        Cada proceso que toca pinta un tramo seguido de estados tras su propio
        `setup(meta)`, y su primer fotograma sale entero. `procs` limita cuántos
        procesos usa como mucho.
        """

        async def one(worker: _Process, offset: int, chunk: list[dict[str, Any]]) -> list[Patch]:
            await worker.call(self._request("setup", [meta]))
            patches: list[Patch] = []
            for start in range(0, len(chunk), BATCH):
                _, part = await worker.call(
                    self._request("renderFrames", [chunk[start : start + BATCH], offset + start])
                )
                patches += part
            return patches

        async def work(workers: list[_Process]) -> list[Patch]:
            size = -(-len(states) // len(workers))
            chunks = [(i, states[i : i + size]) for i in range(0, len(states), size)]
            parts = await asyncio.gather(
                *(
                    one(worker, offset, chunk)
                    for worker, (offset, chunk) in zip(workers, chunks, strict=False)
                )
            )
            return [patch for part in parts for patch in part]

        return await self._run(max(1, min(procs, len(states))), work)

    async def sequence(self, calls: list[tuple[str, list[Any]]]) -> list[list[Patch]] | None:
        """Hace `calls` (función de la escena y argumentos) en orden en un solo proceso.

        Para las escenas que no siguen `setup`/`renderFrames` o que guardan estado
        de un fotograma al siguiente (el polvo de los caballos), y no se pueden
        repartir entre procesos. Devuelve, por llamada, las imágenes que devolvió
        (vacío si no devolvió ninguna); `None` si no hay Node.
        """

        async def work(workers: list[_Process]) -> list[list[Patch]]:
            results = []
            for function, args in calls:
                _, patches = await workers[0].call(self._request(function, args))
                results.append(patches)
            return results

        return await self._run(1, work)

    async def _run(self, wanted: int, work: Callable[[list[_Process]], Awaitable[T]]) -> T | None:
        if self.disabled:
            return None
        if self._pool is None:
            self._pool = _pool(self.node, self.idle_seconds)
            self._pool.users.add(id(self))
        pool = self._pool
        try:
            workers = await pool.acquire(wanted)
        except Exception:
            logger.warning("%s no está disponible; se usa el plan B", self.name, exc_info=True)
            self.disabled = True
            return None
        # Se mide con los procesos ya en la mano: lo que tarda pintar, no la cola.
        start = time.perf_counter()
        try:
            result = await work(workers)
        except asyncio.CancelledError:
            # Cortado a mitad de una petición, el proceso puede tener píxeles sin
            # leer en la tubería y la siguiente leería basura. Se matan (el
            # grupo abre otros, ~0,2 s) y la escena no se apaga.
            pool.discard(workers)
            await asyncio.shield(pool.release([]))
            raise
        except Exception:
            logger.warning("%s no está disponible; se usa el plan B", self.name, exc_info=True)
            self.disabled = True
            pool.discard(workers)
            await pool.release([])
            return None
        await pool.release(workers)
        report_render(self.name, (time.perf_counter() - start) * 1000)
        return result
