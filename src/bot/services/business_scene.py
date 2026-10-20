"""Las pantallas de los negocios de `pala` con canvas: en Node y, si no, en Chromium.

La escena (`assets/negocio/escena.html`) pinta el inicio de `pala` (`dia`) y las pantallas
del negocio (montar, negocio, almacen, cuentas, cliente y cierre) a partir de lo que decide
Python (`bot.services.business_view`): ella no calcula nada. Cada estado es una pantalla
entera e independiente, así que el dibujo se reparte entre procesos sin que se note.

Se prueba, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`).
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.

No hay Pillow de reserva: si ninguno puede, `paint` devuelve `None` y el cog enseña el
panel en texto, como la `pala`. El primer fallo de cada uno se avisa en el log y desde
entonces se usa el siguiente.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png

SCENE = Path(__file__).resolve().parent.parent / "assets" / "negocio" / "escena.html"
W = 1000
H = 640

__all__ = ["H", "SCENE", "W", "BusinessScene"]

#: Nivel de compresión del PNG de Node (el mismo que `housing_scene`: el 6 tarda un 35 % más
#: y apenas ahorra peso).
PNG_LEVEL = 3


def _png(patch: dict[str, Any]) -> bytes:
    """El PNG de una pantalla: los píxeles de Node se comprimen con `PNG_LEVEL`."""
    if "image" not in patch:
        return patch_png(patch)
    buffer = io.BytesIO()
    patch["image"].save(buffer, format="PNG", compress_level=PNG_LEVEL)
    return buffer.getvalue()


class BusinessScene:
    """Dibuja las pantallas de los negocios con Node y, si no, con Chromium.

    Args:
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(self, *, executable_path: str | None = None, node: str | None = None) -> None:
        self.painter = NodeScene(SCENE, name="El pintor de los negocios", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de los negocios",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya no se dibuja nada (el panel sale en texto)."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes] | None:
        """Un PNG por pantalla, en el mismo orden; `None` si ni Node ni Chromium pueden."""
        if not states:
            return []
        meta = {"w": W, "h": H}
        patches = await self.painter.render(meta, states, gif=False)
        if patches is None:
            patches = await self.browser.render(meta, states)
        if patches is None:
            return None
        return [await asyncio.to_thread(_png, patch) for patch in patches]

    async def still(self, state: dict[str, Any]) -> bytes | None:
        """PNG de una sola pantalla; `None` si no se puede pintar."""
        pngs = await self.paint([state])
        return pngs[0] if pngs else None
