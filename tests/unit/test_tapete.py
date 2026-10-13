"""El tapete común de las mesas con fieltro (`assets/tapete.js`, juego `tapete` de las skins)."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from test_node_scene import needs_node

from bot.services import skin_images, skins
from bot.services.coin import Side
from bot.services.coin_scene import CoinScene

ASSETS = Path(skins.__file__).resolve().parent.parent / "assets"
FELT_SCENES = ("ruleta", "autobus", "bacara", "dados", "moneda", "pollo")


def test_cada_mesa_con_fieltro_carga_el_tapete_y_lo_pinta() -> None:
    for game in FELT_SCENES:
        html = (ASSETS / game / "escena.html").read_text(encoding="utf-8")
        assert '<script src="../tapete.js"></script>' in html, game
        assert "Tapete.pattern(" in html, game


def test_cada_mesa_espera_a_la_imagen_propia_antes_de_pintar() -> None:
    """La imagen se carga de forma asíncrona: sin esperarla, saldría el fieltro liso."""
    for game in FELT_SCENES:
        html = (ASSETS / game / "escena.html").read_text(encoding="utf-8")
        assert "window.setup = async (meta) => {\n  await Tapete.ready(meta?.skin);" in html, game


def test_cada_dibujo_de_las_skins_existe_en_el_tapete() -> None:
    js = (ASSETS / "tapete.js").read_text(encoding="utf-8")
    match = re.search(r"const PATTERNS = \{([^}]*)\}", js)
    assert match is not None
    patterns = {name.strip() for name in match.group(1).split(",")}
    for skin in skins.skins_of(skins.TAPETE):
        params = skins.params(skins.TAPETE, skin)
        assert params["pattern"] in patterns | {""}, skin.key


@needs_node
async def test_la_skin_cambia_la_mesa_y_la_de_serie_no() -> None:
    scene = CoinScene(executable_path="/no/existe/chromium")

    async def board(skin: dict[str, object] | None) -> Image.Image:
        png = await scene.board(None, stake=100, face=Side.CARA, skin=skin)
        return Image.open(io.BytesIO(png)).convert("RGB")

    try:
        plain = await board(None)
        serie = await board(skins.params(skins.TAPETE))
        gold = await board(skins.params(skins.TAPETE, skins.SKIN_BY_KEY["tapete_oro"]))
        again = await board(None)
    finally:
        await scene.close()
    # La de serie es la mesa de siempre, con su verde, no el de la ruleta.
    assert ImageChops.difference(plain, serie).getbbox() is None
    assert ImageChops.difference(plain, gold).getbbox() is not None
    # Y al quitarse la skin, el fondo se repinta.
    assert ImageChops.difference(plain, again).getbbox() is None


@pytest.mark.parametrize("key", [s.key for s in skins.skins_of(skins.TAPETE)])
def test_los_tapetes_valen_para_todas_las_mesas(key: str) -> None:
    skin = skins.SKIN_BY_KEY[key]
    assert skin.game == skins.TAPETE
    assert len(skins.params(skins.TAPETE, skin)["felt"]) == 3


@needs_node
async def test_la_imagen_propia_se_pinta_y_cambiarla_repinta_la_mesa() -> None:
    scene = CoinScene(executable_path="/no/existe/chromium")
    propio = skins.SKIN_BY_KEY["tapete_propio"]

    def picture(color: str) -> skin_images.SkinImage:
        buffer = io.BytesIO()
        Image.new("RGB", (320, 180), color).save(buffer, format="PNG")
        return skin_images.prepare(buffer.getvalue())

    async def board(image: skin_images.SkinImage | None) -> Image.Image:
        skin = skins.params(skins.TAPETE, propio, image)
        png = await scene.board(None, stake=100, face=Side.CARA, skin=skin)
        return Image.open(io.BytesIO(png)).convert("RGB")

    try:
        bare = await board(None)
        red = await board(picture("red"))
        blue = await board(picture("blue"))
    finally:
        await scene.close()
    # Sin imagen, fieltro liso; con imagen, se ve, y otra imagen repinta el fondo.
    corner = (40, 330)
    assert bare.getpixel(corner)[1] > bare.getpixel(corner)[0]
    assert red.getpixel(corner)[0] > red.getpixel(corner)[2]
    assert blue.getpixel(corner)[2] > blue.getpixel(corner)[0]
