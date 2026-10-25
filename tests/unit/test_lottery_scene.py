"""La escena de la lotería (`assets/loteria/escena.html`) y su clase (`bot.services.lottery_scene`).

Por ahora pinta el rasca y los sorteos; el resto de modos (décimo, boleto y cartel) entran con sus
pruebas. Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la raíz); la
comparación con Chromium, también si no hay navegador. Sin ninguno de los dos, la clase devuelve
`None` y el cog sigue en texto.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.lottery_render import DRAW_SIZE, SCRATCH_SIZE
from bot.services.lottery_scene import SCENE, LotteryScene
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "muestras_loteria.py"
#: Lo que más puede pesar un rasca y un sorteo (el GIF sube a Discord con cada uno).
MAX_SCRATCH_BYTES = 600 * 1024
MAX_DRAW_BYTES = 1536 * 1024


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("muestras_loteria", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


muestras = _load()
ANIMATIONS = muestras.animations()
SCRATCHES = {name: a for name, a in ANIMATIONS.items() if name.startswith("rasca_")}
DRAWS = {name: a for name, a in ANIMATIONS.items() if name.startswith("sorteo_")}


def size_of(meta: dict[str, Any]) -> tuple[int, int]:
    return meta["w"], meta["h"]


def max_bytes(name: str) -> int:
    return MAX_SCRATCH_BYTES if name.startswith("rasca_") else MAX_DRAW_BYTES


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


def test_las_muestras_cubren_cada_rasca_y_cada_sorteo() -> None:
    assert len(SCRATCHES) == 6
    assert {meta["game"] for meta, _, _ in SCRATCHES.values()} == {"x10", "7ymedia"}
    assert {states[-1]["banner"]["tone"] for _, states, _ in SCRATCHES.values()} == {"win", "lose"}
    for meta, states, _ in SCRATCHES.values():
        assert size_of(meta) == SCRATCH_SIZE
        assert len(meta["cells"]) == 9 and len(meta["top"]) == 4
        assert all(len(state["p"]) == 9 for state in states)
    # El rasca sin premio con dos casillas del premio más alto brilla en otro tono.
    tones = {name: states[-1]["glowTone"] for name, (_, states, _) in SCRATCHES.items()}
    assert tones["rasca_x10_casi"] == "near" and tones["rasca_x10_premio"] == "win"
    assert len(DRAWS) == 7
    assert {meta["kind"] for meta, _, _ in DRAWS.values()} == {"lotto", "gordo", "euro", "nacional"}
    assert {states[-1]["banner"]["tone"] for _, states, _ in DRAWS.values()} == {"win", "carry"}
    for meta, _, _ in DRAWS.values():
        assert size_of(meta) == DRAW_SIZE
    assert {len(meta["rows"]) for meta, _, _ in DRAWS.values() if meta["kind"] == "nacional"} == {
        2,
        3,
    }


async def test_sin_node_ni_chromium_no_se_pinta() -> None:
    scene = LotteryScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        for name in ("rasca_x10_premio", "sorteo_primitiva_bote", "sorteo_navidad"):
            meta, states, durations = ANIMATIONS[name]
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


async def final_picture(scene: LotteryScene, meta: dict, state: dict) -> Image.Image:
    """El último fotograma de una muestra, pintado solo, como una imagen entera."""
    patches = await scene.painter.render(meta, [state], gif=False)
    assert patches is not None
    return patches[0]["image"].convert("RGB")


@needs_node
@module_loop
@pytest.mark.parametrize("name", sorted(ANIMATIONS))
async def test_con_node_cada_animacion_es_un_gif_que_acaba_en_su_png(
    scene: LotteryScene, name: str
) -> None:
    meta, states, durations = ANIMATIONS[name]
    media = await scene.animate(meta, states, durations)
    assert media is not None and not scene.painter.disabled
    assert media.seconds > 1.5
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == size_of(meta) and gif.n_frames > 10
    assert len(media.gif) < max_bytes(name)
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert png.size == size_of(meta)
    # El último fotograma del GIF es el PNG que queda (salvo la paleta del GIF).
    assert difference(last_frame(media.gif), png) < 4


@needs_node
@module_loop
async def test_con_node_cada_resultado_se_ve_distinto_y_pinta_lo_mismo_dos_veces(
    scene: LotteryScene,
) -> None:
    names = (
        "rasca_x10_premio",
        "rasca_x10_nada",
        "rasca_7m_premio",
        "sorteo_gordo",
        "sorteo_jueves",
    )
    results = {}
    for name in names:
        meta, states, durations = ANIMATIONS[name]
        media = await scene.animate(meta, states, durations)
        assert media is not None
        results[name] = media
    pngs = [media.png for media in results.values()]
    assert len(set(pngs)) == len(pngs)
    for name in ("rasca_x10_premio", "sorteo_primitiva_bote"):
        meta, states, durations = ANIMATIONS[name]
        first = results.get(name) or await scene.animate(meta, states, durations)
        again = await scene.animate(meta, states, durations)
        assert first is not None and again is not None
        # Pintar antes del clic y al pulsar tienen que dar los mismos bytes.
        assert again.gif == first.gif and again.png == first.png


@needs_node
@module_loop
@pytest.mark.parametrize("name", ["rasca_x10_premio", "sorteo_primitiva_bote", "sorteo_navidad"])
async def test_con_node_solo_viaja_lo_que_cambia(scene: LotteryScene, name: str) -> None:
    meta, states, _ = ANIMATIONS[name]
    result = await scene.painter.sequence([("setup", [meta]), ("renderFrames", [states, 0])])
    assert result is not None
    patches = result[1]
    assert len(patches) == len(states)
    w, h = size_of(meta)
    sizes = [patch["image"].size for patch in patches]
    assert sizes[0] == (w, h) and (patches[0]["x"], patches[0]["y"]) == (0, 0)
    assert sum(pw * ph for pw, ph in sizes) < len(states) * w * h * 0.5


@needs_node
@module_loop
@pytest.mark.parametrize(
    "name", ["rasca_x10_premio", "rasca_7m_nada", "sorteo_primitiva_bote", "sorteo_navidad"]
)
async def test_repartido_pinta_lo_mismo_que_de_un_tiron(scene: LotteryScene, name: str) -> None:
    painter = scene.painter
    original = painter._request
    calls: list[str] = []

    def spy(call: str, args: list | None = None, **extra: object) -> dict:
        calls.append(call)
        return original(call, args, **extra)

    meta, states, _ = ANIMATIONS[name]
    painter._request = spy  # type: ignore[method-assign]
    try:
        split = await painter.render(meta, states)
        setups = calls.count("setup")
        whole = await painter.render(meta, states, procs=1)
    finally:
        painter._request = original  # type: ignore[method-assign]
    assert split is not None and whole is not None
    assert setups > 1  # de verdad se repartió
    a = browser_scene.assemble(split, size_of(meta))
    b = browser_scene.assemble(whole, size_of(meta))
    assert [x.tobytes() for x in a] == [y.tobytes() for y in b]


@needs_node
@module_loop
async def test_el_casi_y_el_premio_pintan_halos_distintos(scene: LotteryScene) -> None:
    meta, states, _ = SCRATCHES["rasca_x10_casi"]
    near = states[-1]
    assert near["glowTone"] == "near" and len(near["glow"]) == 2
    win = {**near, "glowTone": "win"}
    near_image = await final_picture(scene, meta, near)
    win_image = await final_picture(scene, meta, win)
    assert near_image.tobytes() != win_image.tobytes()
    # El aro de la casilla que brilla (en su borde de arriba): rojo en el casi, dorado en el premio.
    cell = near["glow"][0]
    x, y = 266 + cell % 3 * 120 + 12, 25 + cell // 3 * 120 - 1
    r, g, _ = near_image.getpixel((x, y))
    assert r > 200 and g < 100
    r, g, _ = win_image.getpixel((x, y))
    assert r > 200 and g > 170


@needs_node
@module_loop
@pytest.mark.parametrize(
    ("name", "from_y", "x0"),
    [
        ("rasca_x10_casi", 0, 266),
        ("rasca_7m_nada", 0, 266),
        ("sorteo_primitiva_bote", 299, 0),
        ("sorteo_bonoloto", 299, 0),
        ("sorteo_jueves", 155, 0),
        ("sorteo_navidad", 155, 0),
    ],
)
async def test_el_cartel_no_tapa_la_combinacion(
    scene: LotteryScene, name: str, from_y: int, x0: int
) -> None:
    """Con y sin cartel, la rejilla (rasca) o la combinación (sorteo) salen igual.

    Se empieza unos píxeles más allá del recuadro del cartel: el filtro al reducir se asoma uno.
    """
    meta, states, _ = ANIMATIONS[name]
    final = states[-1]
    assert final["banner"] is not None
    bare = {**final, "banner": None, "bannerA": 0.0}
    with_banner = await final_picture(scene, meta, final)
    without = await final_picture(scene, meta, bare)
    w, h = size_of(meta)
    area = (x0, from_y, w, h)
    assert with_banner.crop(area).tobytes() == without.crop(area).tobytes()
    assert with_banner.tobytes() != without.tobytes()


#: Datos raros (textos vacíos, `null` donde se espera texto, índices fuera de rango, una fila de
#: más): la escena tiene que pintarlos, porque una excepción apaga el pintor hasta reiniciar.
POOL = {
    "mode": "sorteo",
    "kind": "lotto",
    "w": 720,
    "h": 405,
    "game": "primitiva",
    "title": "LA PRIMITIVA",
    "date": "hoy",
    "seed": 5,
    "slots": [{"kind": "main"}] * 6 + [{"kind": "comp", "label": "C"}],
}
NACIONAL = {
    "mode": "sorteo",
    "kind": "nacional",
    "w": 720,
    "h": 405,
    "game": "navidad",
    "title": "NAVIDAD",
    "date": "hoy",
    "seed": 5,
    "rows": [{"label": "A", "amount": "1 Y$"}] * 3,
}
RASCA = {
    "mode": "rasca",
    "w": 640,
    "h": 400,
    "game": "x10",
    "title": "T",
    "price": "2 Y$",
    "top": ["1 Y$"] * 4,
    "cells": ["1 Y$"] * 9,
    "serial": "1",
    "seed": 3,
}
BIG_BANNER = {
    "tone": "win",
    "title": "🎉 ¡BOTE! 🎉" * 6,
    "amount": "9" * 40 + " Y$",
    "sub": "a · " * 30,
}
WEIRD: dict[str, tuple[dict, list]] = {
    "bote vacío": (POOL, [{}, None, 5, "x", []]),
    "bote sin datos": (
        {
            "mode": "sorteo",
            "game": "nuevo",
            "title": None,
            "date": None,
            "seed": None,
            "slots": None,
        },
        [{"shown": [None, "1", 2]}],
    ),
    "bote con huecos raros": (
        {**POOL, "slots": [None, {"kind": "raro"}, {"kind": "star", "label": None}, {}]},
        [{"shown": ["01", 2, None, "", {"a": 1}, "99", "x", "y", "z"], "sorted": True}],
    ),
    "bote con bola rara": (
        POOL,
        [
            {"fly": {"slot": 99, "v": "1", "t": 0.5}},
            {"fly": {"slot": "x", "v": None, "t": "a"}},
            {"fly": {"slot": 3, "v": None, "t": 7}},
            {"fly": 3},
            {"fly": {"slot": -1, "v": "2", "t": -2}},
        ],
    ),
    "bote con cartel raro": (
        POOL,
        [
            {
                "banner": {"tone": "otro", "title": None, "amount": None, "sub": None},
                "bannerA": 0.5,
            },
            {"banner": {}, "bannerA": "x"},
            {"banner": "texto", "bannerA": 1},
            {"banner": BIG_BANNER, "bannerA": 3},
            {"banner": {**BIG_BANNER, "tone": "carry"}, "bannerA": 0.2},
            {"banner": {"tone": "lose", "title": None}, "bannerA": 0.7, "spin": None},
        ],
    ),
    "nacional vacío": (NACIONAL, [{}, None, {"digits": None, "reintegros": None, "fly": None}]),
    "nacional sin filas": ({**NACIONAL, "rows": None}, [{"digits": [["1"]]}]),
    "nacional con una fila": (
        {**NACIONAL, "rows": [{"label": None, "amount": None}]},
        [
            {
                "digits": [[1, 2, None, "", "5", "6", "7"], "raro", None],
                "reintegros": [1, None, "2", "3", "4", "5"],
            }
        ],
    ),
    "nacional con bola rara": (
        NACIONAL,
        [
            {"fly": {"row": 5, "col": 0, "v": "1", "t": 0.5}},
            {"fly": {"row": 0, "col": 9, "v": "1", "t": 0.5}},
            {"fly": {"row": 1.5, "col": "x", "v": None, "t": None}},
            {"fly": {"row": 2, "col": 4, "v": "8", "t": 9}},
        ],
    ),
    "nacional con cartel": (
        NACIONAL,
        [
            {"banner": BIG_BANNER, "bannerA": 0.4},
            {
                "banner": {
                    "tone": "lose",
                    "title": "SIN PREMIOS",
                    "amount": "",
                    "sub": "En el servidor",
                },
                "bannerA": 1,
            },
        ],
    ),
    "tamaño raro": ({**POOL, "w": None, "h": "x"}, [{}]),
    "rasca vacío": (RASCA, [{}, None, {"p": None}, {"p": [2, -1, "x", None, 0.5]}]),
    "rasca con brillo raro": (
        RASCA,
        [
            {
                "p": [1] * 9,
                "active": 99,
                "glow": [0, 99, -1, "a", None, 2.5],
                "glowA": 5,
                "glowTone": "rara",
                "banner": {"tone": "win"},
                "bannerA": 2,
            },
            {
                "p": [0.5] * 9,
                "active": "3",
                "banner": {"tone": "lose", "title": None, "note": None},
                "bannerA": 1,
            },
        ],
    ),
    "rasca sin textos": (
        {"mode": "rasca", "game": "otro", "cells": None, "top": None, "title": None, "price": None},
        [{}],
    ),
    "rasca con pocas casillas": (
        {**RASCA, "cells": ["1 Y$", None, 7]},
        [{"p": [0.3] * 9, "active": 1}],
    ),
}


@needs_node
@module_loop
@pytest.mark.parametrize("name", sorted(WEIRD))
async def test_con_node_los_datos_raros_se_pintan_sin_apagar_el_pintor(name: str) -> None:
    meta, states = WEIRD[name]
    other = LotteryScene(executable_path=NO_CHROMIUM)
    try:
        patches = await other.painter.render(copy.deepcopy(meta), copy.deepcopy(states), procs=1)
    finally:
        await other.close()
    assert patches is not None and len(patches) == len(states)
    assert not other.painter.disabled


@needs_node
@module_loop
async def test_un_modo_que_no_existe_desactiva_el_pintor() -> None:
    other = LotteryScene(executable_path=NO_CHROMIUM)
    meta, states, durations = ANIMATIONS["rasca_x10_premio"]
    try:
        assert await other.animate({**meta, "mode": "no_existe"}, states, durations) is None
    finally:
        await other.close()
    assert other.painter.disabled and other.disabled


@pytest.fixture
def chromium() -> BrowserScene:
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    return BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )


@needs_node
@module_loop
@pytest.mark.parametrize(
    "name", ["rasca_x10_premio", "rasca_7m_nada", "sorteo_euromillones", "sorteo_nino"]
)
async def test_node_y_chromium_pintan_lo_mismo(chromium: BrowserScene, name: str) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    painter = NodeScene(SCENE, name="El pintor de la lotería")
    meta, all_states, _ = ANIMATIONS[name]
    states = [*all_states[::6], all_states[-1]]
    size = size_of(meta)
    try:
        from_browser = await chromium.render(meta, states)
        if from_browser is None:
            pytest.skip("Chromium no arranca en esta máquina")
        from_node = await painter.render(meta, states)
        assert from_node is not None
        a, b = await asyncio.gather(
            asyncio.to_thread(browser_scene.assemble, from_browser, size),
            asyncio.to_thread(browser_scene.assemble, from_node, size),
        )
    finally:
        await chromium.close()
        await painter.close()
    assert len(a) == len(b) == len(states)
    for x, y in zip(a, b, strict=True):
        assert difference(x, y) < 4


@needs_node
@module_loop
@pytest.mark.parametrize("name", sorted(WEIRD))
async def test_con_chromium_los_datos_raros_tambien_se_pintan(
    chromium: BrowserScene, name: str
) -> None:
    meta, states = WEIRD[name]
    try:
        patches = await chromium.render(copy.deepcopy(meta), copy.deepcopy(states))
    finally:
        await chromium.close()
    if patches is None:
        pytest.fail("Chromium no pinta datos raros")
    assert len(patches) == len(states) and not chromium.disabled
