"""La carta de `/renta` (`assets/renta/escena.html`) y su clase (`bot.services.renta_scene`).

Sin Node ni Chromium, `still` devuelve `None` y el cog enseña la declaración en texto.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.renta_scene import SCENE, H, RentaScene, W

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
META = {"w": W, "h": H}
SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "muestras_renta.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("muestras_renta", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


samples = _load().samples


def png_image(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    return image.convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def test_las_muestras_cubren_cada_resultado_y_cada_sello() -> None:
    states = samples().values()
    assert {s["tone"] for s in states} == {"pay", "refund", "zero"}
    assert {s["seal"] for s in states} >= {"", "PENDIENTE", "PAGADA", "PRESENTADA", "CON DEUDA"}
    assert any(not s["rows"] for s in states)


async def test_sin_node_ni_chromium_no_se_pinta() -> None:
    scene = RentaScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        assert await scene.paint([]) == []
        assert await scene.still(samples()["a_pagar_pendiente"]) is None
    finally:
        await scene.close()
    assert scene.disabled


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> RentaScene:
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = RentaScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_cada_muestra_es_un_png_del_tamano_y_distinta(scene: RentaScene) -> None:
    states = list(samples().values())
    pngs = await scene.paint(states)
    assert pngs is not None and not scene.painter.disabled and len(pngs) == len(states)
    images = [png_image(png) for png in pngs]
    for image in images:
        assert len(image.getcolors(1 << 20) or []) > 1000
    for i, first in enumerate(images):
        for other in images[i + 1 :]:
            assert difference(first, other) > 0.05
    assert (await scene.still(states[0])) == pngs[0]


@needs_node
@module_loop
async def test_con_node_un_estado_incompleto_desactiva_el_pintor() -> None:
    other = RentaScene(executable_path=NO_CHROMIUM)
    try:
        assert await other.paint([{"nada": 1}]) is None
    finally:
        await other.close()
    assert other.painter.disabled and other.disabled


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo(scene: RentaScene) -> None:
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    states = list(samples().values())
    try:
        from_browser = await browser.render(META, states)
    finally:
        await browser.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    from_node = await scene.painter.render(META, states, gif=False)
    assert from_node is not None
    for name, patch_b, patch_n in zip(samples(), from_browser, from_node, strict=True):
        x, y = (browser_scene.assemble([p], (W, H))[0].convert("RGB") for p in (patch_b, patch_n))
        assert difference(x, y) < 4, name
