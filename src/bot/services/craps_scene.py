"""Los dados dibujados con canvas en Chromium, y con Pillow si no hay navegador.

La escena (`assets/dados/escena.html`) pinta el tapete en perspectiva con su
pelusa y sus casillas, la pared de pirámides, los dados rojos translúcidos
con los puntos taladrados, las fichas, el disco del punto, el panel y los
carteles. Python decide todo lo que se ve (`bot.services.craps_render`,
`board_state` y `throw_states`): la escena solo pinta, y el dibujo de
Pillow (`CrapsRenderer`) lee los mismos datos.

**Si no hay navegador, se usa Pillow.** El primer fallo de Chromium se avisa
en el log y desde entonces cada imagen sale de `CrapsRenderer`
(`bot.services.browser_scene`). El juego nunca se queda sin imagen.

Coste de una tirada: ~50 fotogramas en ~1 s de navegador y ~0,4 s de montar
el GIF en un hilo.
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services.browser_scene import BrowserScene
from bot.services.craps_render import (
    CrapsRenderer,
    DieRest,
    H,
    Media,
    Table,
    W,
    board_state,
    encode,
    meta_state,
    throw_states,
)

SCENE = Path(__file__).resolve().parent.parent / "assets" / "dados" / "escena.html"
#: Fotogramas que se piden al navegador de una vez (cada uno vuelve como PNG en base64).
BATCH = 40


def _decode(url: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Monta los fotogramas pegando cada recuadro sobre el fotograma anterior.

    La escena devuelve el fotograma entero cuando cambia el marcador y, si no,
    solo el recuadro que ha cambiado (`x`, `y` y su PNG en `u`).
    """
    frames: list[Image.Image] = []
    for patch in patches:
        image = _decode(patch["u"])
        if image.size != (W, H):
            if not frames:
                raise ValueError("El primer fotograma de la escena tiene que ir entero.")
            frame = frames[-1].copy()
            frame.paste(image, (patch["x"], patch["y"]))
            image = frame
        frames.append(image)
    return frames


class CrapsScene:
    """Dibuja los dados en Chromium y, si no puede, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
    """

    def __init__(
        self, fallback: CrapsRenderer | None = None, *, executable_path: str | None = None
    ) -> None:
        self.fallback = fallback or CrapsRenderer()
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de los dados",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si el navegador falló y ya solo se dibuja con Pillow."""
        return self.browser.disabled

    async def close(self) -> None:
        """Cierra el navegador (al apagar el bot)."""
        await self.browser.close()

    async def _frames(self, states: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Recuadros de cada fotograma (ver `assemble`); `None` si no hay navegador."""

        async def work(page: Any) -> list[dict[str, Any]]:
            await page.evaluate("m => setup(m)", meta_state())
            patches: list[dict[str, Any]] = []
            for start in range(0, len(states), BATCH):
                patches += await page.evaluate(
                    "([s, first]) => renderFrames(s, first)", [states[start : start + BATCH], start]
                )
            return patches

        return await self.browser.run(work)

    async def board(self, table: Table, rest: tuple[DieRest, DieRest]) -> bytes:
        """PNG de la mesa quieta: al abrir, al poner Odds y al repintar."""
        patches = await self._frames([board_state(table, rest)])
        if patches is None:
            return await asyncio.to_thread(self.fallback.board, table, rest)
        return base64.b64decode(patches[0]["u"].split(",", 1)[1])

    async def throw(self, table: Table, *, seed: int) -> Media:
        """GIF de la última tirada de `table.game` y PNG del final."""
        states, rest = throw_states(table, seed=seed)
        patches = await self._frames(states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.throw, table, seed=seed)
        return await asyncio.to_thread(lambda: encode(assemble(patches), rest))
