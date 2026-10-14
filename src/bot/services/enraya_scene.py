"""El 4 en raya con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/enraya/escena.html`) pinta el salón (fondo con focos), las
fichas de los dos jugadores con su nombre, el tablero azul con sus agujeros,
las fichas brillantes con relieve, la que cae por detrás del tablero, el aro de
la última jugada, el brillo de la raya ganadora, el confeti y los carteles.
Python decide todo lo que se ve (`bot.services.enraya_render`): la escena solo
pinta.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`EnrayaRenderer`), si tampoco hay navegador.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.enraya_render import H, W, EnrayaRenderer, Media, encode
from bot.services.node_scene import NodeScene, patch_png

SCENE = Path(__file__).resolve().parent.parent / "assets" / "enraya" / "escena.html"


class EnrayaScene:
    """Dibuja el 4 en raya con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: EnrayaRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or EnrayaRenderer()
        self.painter = NodeScene(SCENE, name="El pintor del 4 en raya", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador del 4 en raya",
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

    async def _patches(
        self, meta: dict[str, Any], states: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def still(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        """PNG de un estado quieto (la mesa al abrir, al dormirse alguien)."""
        patches = await self._patches(meta, [state])
        if patches is None:
            return await asyncio.to_thread(self.fallback.still, meta, state)
        return await asyncio.to_thread(patch_png, patches[0])

    async def animate(
        self, meta: dict[str, Any], steps: list[tuple[dict, int]], final: dict[str, Any]
    ) -> Media:
        """GIF de una jugada (`enraya_render.move_steps`) y PNG del final."""
        states = [state for state, _ms in steps]
        states.append(final)
        patches = await self._patches(meta, states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.animate, meta, steps, final)

        def build() -> Media:
            frames: list[Image.Image] = browser_scene.assemble(patches, (W, H))
            return encode(frames, [ms for _state, ms in steps])

        return await asyncio.to_thread(build)
