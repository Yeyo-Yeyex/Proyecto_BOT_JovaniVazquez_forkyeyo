"""La escena de la lotería (`assets/loteria/escena.html`) y su clase (`bot.services.lottery_scene`).

Por ahora solo pinta el rasca; el resto de modos (sorteo, décimo, boleto y cartel) entran con sus
pruebas. Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la raíz); la
comparación con Chromium, también si no hay navegador. Sin ninguno de los dos, la clase devuelve
`None` y el cog sigue en texto.
"""

from __future__ import annotations

import asyncio
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
from bot.services.lottery_render import SCRATCH_SIZE
from bot.services.lottery_scene import SCENE, LotteryScene
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "muestras_loteria.py"
SIZE = SCRATCH_SIZE
#: Lo que más puede pesar un rasca (el GIF sube a Discord con cada tirada).
MAX_GIF_BYTES = 600 * 1024


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("muestras_loteria", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


muestras = _load()
SCRATCHES = {
    name: animation
    for name, animation in muestras.animations().items()
    if name.startswith("rasca_")
}


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def last_frame(gif: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(gif))
    image.seek(image.n_frames - 1)
    return image.convert("RGB")


def test_la_escena_deja_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]
    assert "Math.random(" not in html


def test_las_muestras_cubren_cada_rasca_con_y_sin_premio() -> None:
    assert len(SCRATCHES) == 6
    assert {meta["game"] for meta, _, _ in SCRATCHES.values()} == {"x10", "7ymedia"}
    tones = {states[-1]["banner"]["tone"] for _, states, _ in SCRATCHES.values()}
    assert tones == {"win", "lose"}
    for meta, states, _ in SCRATCHES.values():
        assert (meta["w"], meta["h"]) == SIZE
        assert len(meta["cells"]) == 9 and len(meta["top"]) == 4
        assert all(len(state["p"]) == 9 for state in states)


async def test_sin_node_ni_chromium_no_se_pinta() -> None:
    scene = LotteryScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    meta, states, durations = SCRATCHES["rasca_x10_premio"]
    try:
        assert await scene.animate(meta, states, durations) is None
        assert await scene.still(muestras.stills()["cartel"]) is None
    finally:
        await scene.close()
    assert scene.disabled


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> LotteryScene:
    """La lotería con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = LotteryScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
@pytest.mark.parametrize("name", sorted(SCRATCHES))
async def test_con_node_cada_rasca_es_un_gif_que_acaba_en_su_png(
    scene: LotteryScene, name: str
) -> None:
    meta, states, durations = SCRATCHES[name]
    media = await scene.animate(meta, states, durations)
    assert media is not None and not scene.painter.disabled
    assert media.seconds > 1.5
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == SIZE and gif.n_frames > 10
    assert len(media.gif) < MAX_GIF_BYTES
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert png.size == SIZE
    # El último fotograma del GIF es el PNG que queda (salvo la paleta del GIF).
    assert difference(last_frame(media.gif), png) < 4


@needs_node
@module_loop
async def test_con_node_cada_resultado_se_ve_distinto_y_pinta_lo_mismo_dos_veces(
    scene: LotteryScene,
) -> None:
    results = {}
    for name in ("rasca_x10_premio", "rasca_x10_nada", "rasca_7m_premio"):
        meta, states, durations = SCRATCHES[name]
        media = await scene.animate(meta, states, durations)
        assert media is not None
        results[name] = media
    pngs = [media.png for media in results.values()]
    assert len(set(pngs)) == len(pngs)
    meta, states, durations = SCRATCHES["rasca_x10_premio"]
    again = await scene.animate(meta, states, durations)
    assert again is not None
    # Pintar antes del clic y al pulsar tienen que dar los mismos bytes.
    assert again.gif == results["rasca_x10_premio"].gif
    assert again.png == results["rasca_x10_premio"].png


@needs_node
@module_loop
async def test_con_node_solo_viaja_lo_que_cambia(scene: LotteryScene) -> None:
    meta, states, _ = SCRATCHES["rasca_x10_premio"]
    result = await scene.painter.sequence([("setup", [meta]), ("renderFrames", [states, 0])])
    assert result is not None
    patches = result[1]
    assert len(patches) == len(states)
    sizes = [patch["image"].size for patch in patches]
    assert sizes[0] == SIZE and (patches[0]["x"], patches[0]["y"]) == (0, 0)
    sent = sum(w * h for w, h in sizes)
    assert sent < len(states) * SIZE[0] * SIZE[1] * 0.4
    # Mientras se rasca, cada fotograma es poco más que una casilla con su moneda.
    scratching = sizes[2 : len(states) - 10]
    assert max(w * h for w, h in scratching) < SIZE[0] * SIZE[1] * 0.5


@needs_node
@module_loop
async def test_repartido_pinta_lo_mismo_que_de_un_tiron(scene: LotteryScene) -> None:
    painter = scene.painter
    original = painter._request
    calls: list[str] = []

    def spy(call: str, args: list | None = None, **extra: object) -> dict:
        calls.append(call)
        return original(call, args, **extra)

    painter._request = spy  # type: ignore[method-assign]
    try:
        for name in ("rasca_x10_premio", "rasca_7m_nada"):
            meta, states, _ = SCRATCHES[name]
            calls.clear()
            split = await painter.render(meta, states)
            setups = calls.count("setup")
            whole = await painter.render(meta, states, procs=1)
            assert split is not None and whole is not None
            assert setups > 1  # de verdad se repartió
            a = browser_scene.assemble(split, SIZE)
            b = browser_scene.assemble(whole, SIZE)
            assert [x.tobytes() for x in a] == [y.tobytes() for y in b]
    finally:
        painter._request = original  # type: ignore[method-assign]


@needs_node
@module_loop
async def test_un_modo_o_un_estado_que_no_valen_desactivan_el_pintor() -> None:
    meta, states, durations = SCRATCHES["rasca_x10_premio"]
    for bad_meta, bad_states in (
        ({**meta, "mode": "no_existe"}, states),
        (meta, [{"p": []}] * len(states)),
    ):
        other = LotteryScene(executable_path=NO_CHROMIUM)
        try:
            assert await other.animate(bad_meta, bad_states, durations) is None
        finally:
            await other.close()
        assert other.painter.disabled and other.disabled


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_el_rasca() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de la lotería")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    try:
        for name in ("rasca_x10_premio", "rasca_7m_nada"):
            meta, all_states, _ = SCRATCHES[name]
            states = [*all_states[::5], all_states[-1]]
            from_browser = await browser.render(meta, states)
            if from_browser is None:
                pytest.skip("Chromium no arranca en esta máquina")
            from_node = await painter.render(meta, states)
            assert from_node is not None
            a, b = await asyncio.gather(
                asyncio.to_thread(browser_scene.assemble, from_browser, SIZE),
                asyncio.to_thread(browser_scene.assemble, from_node, SIZE),
            )
            assert len(a) == len(b) == len(states)
            for x, y in zip(a, b, strict=True):
                assert difference(x, y) < 4
    finally:
        await browser.close()
        await painter.close()
