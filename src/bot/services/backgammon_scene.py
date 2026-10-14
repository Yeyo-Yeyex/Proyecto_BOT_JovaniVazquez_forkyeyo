"""El backgammon con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/backgammon/escena.html`) pinta la mesa de nogal con su lámpara,
el tablero con marco de madera, tapete (con las skins de `assets/tapete.js`),
puntos de marfil y burdeos, barra con bisagras y bandeja forrada, las fichas
con volumen, los dados de marfil o de ébano, el cubo de doblar, las tarjetas de
los jugadores, el confeti y los carteles. Python decide todo lo que se ve
(`bot.services.backgammon_render`): la escena solo pinta.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`BackgammonRenderer`), si tampoco hay navegador.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen. Pillow no conoce las skins: con
él sale el tapete de serie.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.backgammon_render import BackgammonRenderer, H, Media, W, encode
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png

SCENE = Path(__file__).resolve().parent.parent / "assets" / "backgammon" / "escena.html"


class BackgammonScene:
    """Dibuja el backgammon con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: BackgammonRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or BackgammonRenderer()
        self.painter = NodeScene(SCENE, name="El pintor del backgammon", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador del backgammon",
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
        """GIF de una animación (`backgammon_render.move_steps`, `chain`...) y PNG del final."""
        states = [state for state, _ms in steps]
        states.append(final)
        patches = await self._patches(meta, states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.animate, meta, steps, final)

        def build() -> Media:
            frames: list[Image.Image] = browser_scene.assemble(patches, (W, H))
            return encode(frames, [ms for _state, ms in steps])

        return await asyncio.to_thread(build)
