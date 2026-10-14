"""Póker (Casino Hold'em) con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/poker/escena.html`) pinta el tapete, el mazo, las cartas (con
su vuelo, su giro y su brillo), los asientos de la mesa del canal con sus
pastillas, fichas y resultados, la banca, las tablas del ante y del 💎 Bote y el
cartel. Python decide todo lo que se ve (`bot.services.poker_render`): la escena
solo pinta, y el dibujo de Pillow (`PokerRenderer`) lee los mismos datos.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`PokerRenderer`), si tampoco hay navegador: solo la imagen final,
   sin GIF.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png
from bot.services.poker import TableDeal
from bot.services.poker_render import (
    H,
    Media,
    PokerRenderer,
    SeatView,
    W,
    board_state,
    deal_states,
    durations,
    encode,
    meta_state,
    showdown_states,
)

SCENE = Path(__file__).resolve().parent.parent / "assets" / "poker" / "escena.html"


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


class PokerScene:
    """Dibuja el póker con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: PokerRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or PokerRenderer()
        self.painter = NodeScene(SCENE, name="El pintor del póker", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador del póker",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya solo se dibuja con Pillow."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def _frames(
        self, meta: dict[str, Any], states: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """Recuadros de cada fotograma (ver `assemble`); `None` si no hay Node ni navegador."""
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def board(self, *, pot: int = 0, skin: dict[str, object] | None = None) -> bytes:
        """PNG de la mesa vacía (al abrirla) con el tapete `skin`."""
        state = board_state(pot)
        state["full"] = True
        patches = await self._frames({**meta_state(), "skin": skin}, [state])
        if patches is None:
            return await asyncio.to_thread(self.fallback.board, pot)
        return await asyncio.to_thread(patch_png, patches[0])

    async def timba(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        """PNG de la mesa de la timba (`poker_render.timba_meta` y `timba_state`)."""
        patches = await self._frames({**meta}, [state])
        if patches is None:
            return await asyncio.to_thread(self.fallback.timba, meta, state)
        return await asyncio.to_thread(patch_png, patches[0])

    async def deal(
        self,
        table: TableDeal,
        views: Sequence[SeatView],
        *,
        pot: int = 0,
        seed: int = 0,
        skin: dict[str, object] | None = None,
    ) -> Media:
        """El GIF del reparto hasta la mesa de decidir, y su PNG final.

        Args:
            table: Las cartas de la mano.
            views: Los asientos ocupados, en orden.
            pot: Lo que lleva el 💎 Bote.
            seed: Semilla del vuelo de las cartas (misma semilla, mismo GIF).
            skin: El tapete de quien abrió la mesa; `None`, el de serie.
        """
        states = deal_states(table, views, pot=pot, seed=seed)
        patches = await self._frames({**meta_state(), "skin": skin}, states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.deal, table, views, pot=pot)
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations(states)))

    async def showdown(
        self,
        table: TableDeal,
        views: Sequence[SeatView],
        called: Sequence[bool],
        *,
        pot: int = 0,
        seed: int = 0,
        skin: dict[str, object] | None = None,
    ) -> Media:
        """El GIF del final con lo que ha decidido cada asiento, y su PNG final."""
        states = showdown_states(table, views, called, pot=pot, seed=seed)
        patches = await self._frames({**meta_state(), "skin": skin}, states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.showdown, table, views, called, pot=pot)
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations(states)))
