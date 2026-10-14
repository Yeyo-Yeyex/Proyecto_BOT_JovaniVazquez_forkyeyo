"""Póker (Casino Hold'em) con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/poker/escena.html`) pinta el tapete, el mazo, las cartas (con
su vuelo, su giro y su brillo), las pastillas con lo que lleva cada mano, las
casillas de ANTE e IR con sus fichas, la tabla del ante y el cartel. Python
decide todo lo que se ve (`bot.services.poker_render`): la escena solo pinta, y
el dibujo de Pillow (`PokerRenderer`) lee los mismos datos.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`PokerRenderer`), si tampoco hay navegador: solo la imagen final,
   sin GIF.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.

**Una sola llamada al pintor para los dos finales.** `showdown` pide de una vez
el final si va y el final si se retira (el primer fotograma de cada uno sale
entero) y monta los dos GIF en un hilo. El cog lo pide mientras el jugador mira
el flop, así que al pulsar ya está hecho.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png
from bot.services.poker import Deal
from bot.services.poker_render import (
    H,
    Media,
    PokerRenderer,
    Showdown,
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


def build_showdown(
    patches: list[dict[str, Any]], states: list[dict[str, Any]], split: int
) -> Showdown:
    """Los dos GIF del final a partir de los recuadros de `ir + retirarse`.

    Args:
        patches: Los recuadros de los dos finales, uno detrás de otro.
        states: Los mismos estados (de ahí salen los tiempos de cada fotograma).
        split: Cuántos fotogramas tiene el final de ir.
    """
    frames = assemble(patches)
    with ThreadPoolExecutor(max_workers=2) as pool:
        call = pool.submit(encode, frames[:split], durations(states[:split]))
        fold = pool.submit(encode, frames[split:], durations(states[split:]))
        return Showdown(call=call.result(), fold=fold.result())


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

    async def board(self, *, skin: dict[str, object] | None = None) -> bytes:
        """PNG de la mesa vacía (al abrirla) con el tapete `skin`."""
        state = board_state()
        state["full"] = True
        patches = await self._frames({**meta_state(), "skin": skin}, [state])
        if patches is None:
            return await asyncio.to_thread(self.fallback.board)
        return patch_png(patches[0])

    async def deal(
        self, cards: Deal, *, seed: int = 0, skin: dict[str, object] | None = None
    ) -> Media:
        """El GIF del reparto hasta la mesa de decidir, y su PNG final.

        Args:
            cards: Las nueve cartas de la mano.
            seed: Semilla del vuelo de las cartas (misma semilla, mismo GIF).
            skin: El tapete del jugador (`skins.params` de su mesa); `None`, el de serie.
        """
        states = deal_states(cards, seed=seed)
        patches = await self._frames({**meta_state(), "skin": skin}, states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.deal, cards)
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations(states)))

    async def showdown(
        self, cards: Deal, *, seed: int = 0, skin: dict[str, object] | None = None
    ) -> Showdown:
        """Los GIF de los dos finales (ir y retirarse) con su PNG final.

        Args:
            cards: Las nueve cartas de la mano.
            seed: Semilla del vuelo y las chispas.
            skin: El tapete del jugador; `None`, el de serie.
        """
        call = showdown_states(cards, called=True, seed=seed)
        fold = showdown_states(cards, called=False, seed=seed)
        states = call + fold
        patches = await self._frames({**meta_state(), "skin": skin}, states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.showdown, cards)
        return await asyncio.to_thread(build_showdown, patches, states, len(call))
