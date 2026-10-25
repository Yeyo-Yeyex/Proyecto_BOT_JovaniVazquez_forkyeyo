"""La carta de `/renta` con canvas: la declaración de una semana en una imagen.

La escena (`assets/renta/escena.html`) pinta lo que decide Python (`renta_report.card_state`):
ella no calcula nada. Mide `W` × `H`. Se prueba Node con Skia (`bot.services.node_scene`) y, si
falla, Chromium (`bot.services.browser_scene`). No hay Pillow de reserva: si ninguno puede,
`still` devuelve `None` y el cog enseña la declaración en texto.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png

SCENE = Path(__file__).resolve().parent.parent / "assets" / "renta" / "escena.html"
W = 960
H = 560

__all__ = ["H", "SCENE", "W", "RentaScene"]

PNG_LEVEL = 3


def _png(patch: dict[str, Any]) -> bytes:
    if "image" not in patch:
        return patch_png(patch)
    buffer = io.BytesIO()
    patch["image"].save(buffer, format="PNG", compress_level=PNG_LEVEL)
    return buffer.getvalue()


class RentaScene:
    """Dibuja la carta de la Renta con Node y, si no, con Chromium.

    Args:
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(self, *, executable_path: str | None = None, node: str | None = None) -> None:
        self.painter = NodeScene(SCENE, name="El pintor de la Renta", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de la Renta",
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

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes] | None:
        """Un PNG por estado, en el mismo orden; `None` si ni Node ni Chromium pueden."""
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
        """PNG de una carta; `None` si no se puede pintar."""
        pngs = await self.paint([state])
        return pngs[0] if pngs else None
