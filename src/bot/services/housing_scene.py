"""El panel de `pisos` con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/pisos/escena.html`) pinta las doce pantallas del panel (mapa,
barrio, ficha, cartera, caja, vpo, gestion, candidatos, subastas, reformas, imprevistos y resenas)
a partir de lo que decide `bot.services.housing_view`: ella no calcula nada. Cada
estado es una pantalla entera e independiente, así que el dibujo se reparte entre procesos
sin que se note.

Se prueba, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`).
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`bot.services.housing_render`), si tampoco hay navegador.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente: el panel nunca se queda sin imagen.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from bot.services.browser_scene import BrowserScene
from bot.services.housing_render import SCREEN_H, H, HousingRenderer, W
from bot.services.node_scene import NodeScene, patch_png

SCENE = Path(__file__).resolve().parent.parent / "assets" / "pisos" / "escena.html"

__all__ = ["H", "SCENE", "SCREEN_H", "W", "HousingScene"]

#: Nivel de compresión del PNG de Node. Con las ilustraciones, el 6 de `patch_png` tarda un 35 %
#: más que el 3 (~24 frente a ~17 ms por pantalla) y solo ahorra un 3-8 % de peso (~260 KB).
PNG_LEVEL = 3


def _png(patch: dict[str, Any]) -> bytes:
    """El PNG de una pantalla: los píxeles de Node se comprimen con `PNG_LEVEL`."""
    if "image" not in patch:
        return patch_png(patch)
    buffer = io.BytesIO()
    patch["image"].save(buffer, format="PNG", compress_level=PNG_LEVEL)
    return buffer.getvalue()


class HousingScene:
    """Dibuja las pantallas de `pisos` con Node, si no con Chromium y, si no, con Pillow.

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: HousingRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or HousingRenderer()
        self.painter = NodeScene(SCENE, name="El pintor de los pisos", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de los pisos",
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

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes]:
        """Un PNG por pantalla (`housing_view`), en el mismo orden."""
        if not states:
            return []
        meta = {"w": W, "h": H}
        patches = await self.painter.render(meta, states, gif=False)
        if patches is None:
            patches = await self.browser.render(meta, states)
        if patches is None:
            return [await asyncio.to_thread(self.fallback.paint, state) for state in states]
        return [await asyncio.to_thread(_png, patch) for patch in patches]

    async def still(self, state: dict[str, Any]) -> bytes:
        """PNG de una sola pantalla."""
        return (await self.paint([state]))[0]
