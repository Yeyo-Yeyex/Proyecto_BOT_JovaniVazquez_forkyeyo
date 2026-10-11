"""Bacará con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/bacara/escena.html`) pinta el tapete, el zapato, las cartas
(con su vuelo, su giro y el apretón de la tercera), las fichas de los totales,
los carteles, las chispas y la carretera grande. Python decide todo lo que se ve
(`bot.services.bacara_render`, `board_state` y `coup_states`): la escena solo
pinta, y el dibujo de Pillow (`BacaraRenderer`) lee los mismos datos.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`BacaraRenderer`), si tampoco hay navegador: solo la imagen final,
   sin GIF.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.

Una mano son 60-105 fotogramas, casi todos recuadros pequeños (una carta que
vuela, una ficha que cambia). El cog la pinta mientras la mesa está abierta a
apuestas, así que lo normal es que el GIF ya esté hecho al cerrarla.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.bacara import Coup, Result
from bot.services.bacara_render import (
    BacaraRenderer,
    H,
    Header,
    Media,
    W,
    board_state,
    coup_states,
    durations,
    encode,
    meta_state,
)
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png

SCENE = Path(__file__).resolve().parent.parent / "assets" / "bacara" / "escena.html"


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


class BacaraScene:
    """Dibuja el bacará con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: BacaraRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or BacaraRenderer()
        self.painter = NodeScene(SCENE, name="El pintor del bacará", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador del bacará",
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

    async def board(
        self, results: Sequence[Result], header: Header, coup: Coup | None = None
    ) -> bytes:
        """PNG de la mesa quieta: vacía al abrir o con la última mano mientras se apuesta."""
        state = board_state(results, header, coup)
        state["full"] = True
        patches = await self._frames(meta_state(), [state])
        if patches is None:
            return await asyncio.to_thread(self.fallback.board, results, header, coup)
        return patch_png(patches[0])

    async def coup(
        self,
        coup: Coup,
        before: Sequence[Result],
        header: Header,
        *,
        burned: int = 0,
        seed: int = 0,
    ) -> Media:
        """El GIF de una mano y el PNG de cómo queda la mesa.

        Args:
            coup: La mano.
            before: Resultados del zapato antes de ella.
            header: El rótulo con esta mano ya contada.
            burned: Cartas quemadas, si es la primera del zapato.
            seed: Semilla de las chispas y el temblor del apretón.
        """
        states = coup_states(coup, before, header, burned=burned, seed=seed)
        patches = await self._frames(meta_state(), states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.coup, coup, before, header)
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations(states)))
