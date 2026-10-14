"""Pintar antes del clic: lo que una mesa deja dibujado para cada botón de un turno.

Lo comparten los juegos de tablero (`4enraya`, `backgammon`): mientras el miembro
piensa, la mesa pinta en segundo plano el GIF de cada botón posible y, al
pulsar, solo queda subirlo («Pintar antes del clic» en CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

logger = logging.getLogger(__name__)


class Ahead:
    """Lo que una mesa pinta por adelantado para un turno: un dibujo por botón.

    Los dibujos van de uno en uno, en el orden pedido, y nunca se cortan a
    medias (`NodeScene` no lo admite): cada uno comprueba antes de empezar si
    el turno sigue siendo el mismo (`key`). Al pulsar, `take` da el dibujo si ya
    está o si se está pintando justo ese; si no, quien llama lo pinta en el
    momento.
    """

    def __init__(self, enabled: bool, *, name: str = "un juego") -> None:
        self.enabled = enabled
        #: Para el log y el nombre de la tarea («el 4 en raya»).
        self.name = name
        self.key: Hashable | None = None
        self.futures: dict[Hashable, asyncio.Future[Any]] = {}
        self.current: Hashable | None = None
        self.task: asyncio.Task[None] | None = None

    def plan(
        self,
        key: Hashable,
        order: list[Hashable],
        paint: Callable[[Hashable], Awaitable[Any]],
    ) -> None:
        """Empieza a pintar `order` para el turno `key`, si no lo está ya."""
        if not self.enabled or key == self.key:
            return
        loop = asyncio.get_running_loop()
        self.key = key
        self.futures = {item: loop.create_future() for item in order}
        previous = self.task
        self.task = asyncio.create_task(
            self._run(key, order, self.futures, paint, previous), name=f"adelanto de {self.name}"
        )

    async def _run(
        self,
        key: Hashable,
        order: list[Hashable],
        futures: dict[Hashable, asyncio.Future[Any]],
        paint: Callable[[Hashable], Awaitable[Any]],
        previous: asyncio.Task[None] | None,
    ) -> None:
        # Un plan detrás de otro: así un plan viejo nunca hace esperar dos dibujos.
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        try:
            for item in order:
                result = None
                if self.key == key:
                    self.current = item
                    try:
                        result = await paint(item)
                    except Exception:
                        logger.warning(
                            "No se pudo pintar por adelantado %s", self.name, exc_info=True
                        )
                    finally:
                        self.current = None
                if not futures[item].done():
                    futures[item].set_result(result)
        finally:
            for future in futures.values():
                if not future.done():
                    future.set_result(None)

    async def take(self, key: Hashable, item: Hashable) -> Any:
        """El dibujo de `item` si vale para `key` y está (o se está pintando); si no, `None`."""
        if self.key != key or item not in self.futures:
            return None
        future = self.futures[item]
        if future.done() or self.current == item:
            return await asyncio.shield(future)
        return None

    def drop(self) -> None:
        """El turno ha cambiado: lo que quede por pintar ya no sirve."""
        self.key = None
