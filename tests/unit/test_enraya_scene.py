"""Pruebas de bot.services.enraya_scene: el 4 en raya con canvas (Node, Chromium o Pillow).

Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la
raíz). La comparación con Chromium se salta también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.enraya import Board, Disc
from bot.services.enraya_render import H, Player, W, banner, board_state, meta_state, move_steps
from bot.services.enraya_scene import SCENE, EnrayaScene
from bot.services.node_scene import patch_png

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
SIZE = (W, H)
META = meta_state(
    Player("Diego", "Fichas rojas"),
    Player("Jovani el implacable de Moncloa", "Nivel difícil"),
    title="CUATRO EN RAYA",
    subtitle="Mesa de Yapland",
    seed=7,
)


def played(columns: list[int]) -> Board:
    board = Board()
    for column in columns:
        board = board.play(column)
    return board


#: Las rojas están a una ficha de la diagonal (0,0)-(3,3): les falta la columna 3.
BEFORE_WIN = played([0, 1, 1, 2, 3, 2, 2, 3, 3, 6])


def png_image(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == SIZE
    return image.convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def winning_move() -> tuple[list, dict]:
    move = BEFORE_WIN.play(3).last
    assert move is not None
    end = banner("¡GANAS!", "Cuatro en raya en diagonal", kind="win")
    steps, final = move_steps(BEFORE_WIN, [move], end=end)
    return steps, final


# -- Reserva con Pillow ------------------------------------------------------------------


async def test_sin_node_ni_chromium_el_cuatro_en_raya_cae_a_pillow() -> None:
    scene = EnrayaScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    steps, final = winning_move()
    try:
        still = await scene.still(META, board_state(Board(), turn=Disc.RED))
        media = await scene.animate(META, steps, final)
    finally:
        await scene.close()
    assert scene.disabled
    png_image(still)
    png_image(media.png)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == SIZE


# -- Node ---------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> EnrayaScene:
    """Una escena con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = EnrayaScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_el_tablero_vacio_es_un_png_y_no_esta_vacio(scene: EnrayaScene) -> None:
    png = await scene.still(META, board_state(Board(), turn=Disc.RED))
    assert not scene.painter.disabled
    assert len(png_image(png).getcolors(1 << 20) or []) > 1000


@needs_node
@module_loop
async def test_con_node_una_jugada_ganadora_es_un_gif_con_su_png_final(scene: EnrayaScene) -> None:
    steps, final = winning_move()
    media = await scene.animate(META, steps, final)
    assert not scene.painter.disabled
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == SIZE and gif.n_frames > 40
    gif.seek(gif.n_frames - 1)
    assert difference(gif.convert("RGB"), png_image(media.png)) < 12
    assert media.seconds == sum(ms for _state, ms in steps) / 1000


@needs_node
@module_loop
async def test_con_node_mientras_cae_la_ficha_solo_se_devuelve_su_franja(
    scene: EnrayaScene,
) -> None:
    """Los fotogramas de la caída son recuadros pequeños y, pegados, igual que enteros."""
    move = BEFORE_WIN.play(3).last
    assert move is not None
    steps, final = move_steps(BEFORE_WIN, [move])
    states = [state for state, _ms in steps] + [final]
    # Lo que devuelve la escena, sin montar (`render` ya da los fotogramas enteros).
    results = await scene.painter.sequence([("setup", [META]), ("renderFrames", [states, 0])])
    assert results is not None
    patches = results[1]
    assert len(patches) == len(states)
    sizes = [patch["image"].size for patch in patches]
    assert sizes[0] == SIZE and sizes[-1] == SIZE  # el primero y el que cambia el tablero
    falling = sizes[1:-1]
    assert falling and all(w * h < W * H / 4 for w, h in falling)
    frames = await asyncio.to_thread(browser_scene.assemble, patches, SIZE)
    rendered = await scene.painter.render(META, states)
    assert rendered is not None
    for mine, theirs in zip(frames, rendered, strict=True):
        assert mine.tobytes() == theirs["image"].tobytes()
    for index in (2, len(states) // 2, len(states) - 2):
        whole = png_image(await scene.still(META, states[index]))
        assert difference(frames[index].convert("RGB"), whole) < 0.5, index


@needs_node
@module_loop
async def test_con_node_cada_estado_final_se_pinta_distinto(scene: EnrayaScene) -> None:
    """Turnos, carteles de cada clase, brillo y confeti: sin error y cada uno diferente."""
    final = BEFORE_WIN.play(3)
    states = [
        board_state(BEFORE_WIN, turn=Disc.RED),
        board_state(BEFORE_WIN, turn=Disc.YELLOW),
        board_state(BEFORE_WIN),
        board_state(final, win_t=0.5, confetti_t=0.5, end=banner("¡GANAS!", "Cuatro", kind="win")),
        board_state(final, win_t=1.0),
    ]
    states += [
        board_state(BEFORE_WIN, end=banner("TÍTULO", "Sub", kind=k, a=1.0))
        for k in ("win", "lose", "draw", "time")
    ]
    patches = await scene.painter.render(META, states)
    assert patches is not None and len(patches) == len(states)
    images = [png_image(patch_png(patch)) for patch in patches]
    for first, other in zip(images, images[1:], strict=False):
        assert difference(first, other) > 0.05


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo(scene: EnrayaScene) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    steps, final = winning_move()
    states = [state for state, _ms in steps] + [final]
    try:
        from_browser = await browser.render(META, states)
    finally:
        await browser.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    from_node = await scene.painter.render(META, states)
    assert from_node is not None
    a, b = await asyncio.gather(
        asyncio.to_thread(browser_scene.assemble, from_browser, SIZE),
        asyncio.to_thread(browser_scene.assemble, from_node, SIZE),
    )
    for x, y in zip(a, b, strict=True):
        assert difference(x.convert("RGB"), y.convert("RGB")) < 4
