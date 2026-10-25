"""La lotería con canvas: rascas, décimos, boletos, sorteos y el cartel de la administración.

Una sola escena (`assets/loteria/escena.html`) con un `mode` por imagen; lo que se
ve lo decide `bot.services.lottery_render` y la escena solo pinta. Se prueba Node
con Skia (`bot.services.node_scene`) y, si falla, Chromium
(`bot.services.browser_scene`). No hay Pillow de reserva: si ninguno puede, los
métodos devuelven `None` y el cog sigue en texto, como antes de tener imágenes (el
rasca, con las casillas tapadas con spoilers).

Una sola escena y no una por imagen porque cada escena cargada ocupa ~50 MB en cada
proceso de Node que la pinta (CLAUDE.md, «Rendimiento en el NAS»).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.lottery_render import Media, encode
from bot.services.node_scene import NodeScene, patch_png

SCENE = Path(__file__).resolve().parent.parent / "assets" / "loteria" / "escena.html"

__all__ = ["SCENE", "LotteryScene"]


class LotteryScene:
    """Dibuja la lotería con Node y, si no, con Chromium.

    Args:
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(self, *, executable_path: str | None = None, node: str | None = None) -> None:
        self.painter = NodeScene(SCENE, name="El pintor de la lotería", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de la lotería",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya no se dibuja nada."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def still(self, state: dict[str, Any]) -> bytes | None:
        """PNG de una imagen quieta (décimo, boleto o cartel); `None` si no se puede.

        El estado lleva su `mode` y su tamaño (`w`, `h`): hace de `meta` y de fotograma.
        """
        meta = {"mode": state["mode"], "w": state["w"], "h": state["h"]}
        patches = await self.painter.render(meta, [state], gif=False)
        if patches is None:
            patches = await self.browser.render(meta, [state])
        if patches is None:
            return None
        return await asyncio.to_thread(patch_png, patches[0])

    async def animate(
        self, meta: dict[str, Any], states: list[dict[str, Any]], durations: Sequence[int]
    ) -> Media | None:
        """GIF y PNG final de un rasca o un sorteo; `None` si no se puede pintar."""
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        if patches is None:
            return None
        size = (meta["w"], meta["h"])
        return await asyncio.to_thread(
            lambda: encode(browser_scene.assemble(patches, size), durations)
        )
