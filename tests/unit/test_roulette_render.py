"""Pruebas del render de la ruleta: tamaño, número de fotogramas y empalmes."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops

from bot.services.roulette import DOUBLE_ZERO
from bot.services.roulette_render import FRAME_COUNT, SIZE, WheelRenderer


@pytest.fixture(scope="module")
def renderer() -> WheelRenderer:
    return WheelRenderer()


def frames(gif: bytes) -> list[Image.Image]:
    image = Image.open(io.BytesIO(gif))
    result = []
    for index in range(image.n_frames):
        image.seek(index)
        result.append(image.convert("RGB"))
    return result


@pytest.mark.parametrize("pocket", [0, 17, DOUBLE_ZERO])
def test_el_gif_termina_exactamente_en_el_png_final(renderer: WheelRenderer, pocket: int) -> None:
    """El cambio de GIF a PNG al revelar el resultado no debe notarse."""
    media = renderer.media(pocket)
    last = frames(media.gif)[-1]
    png = Image.open(io.BytesIO(media.png)).convert("RGB")

    assert ImageChops.difference(last, png).getbbox() is None


def test_el_gif_empieza_en_la_rueda_en_reposo_salvo_la_bola(renderer: WheelRenderer) -> None:
    first = frames(renderer.media(17).gif)[0]
    idle = Image.open(io.BytesIO(renderer.idle_png())).convert("RGB")

    left, top, right, bottom = ImageChops.difference(first, idle).getbbox()
    assert right - left < SIZE * 0.1 and bottom - top < SIZE * 0.1


def test_el_gif_es_ligero_y_no_se_repite(renderer: WheelRenderer) -> None:
    media = renderer.media(5)
    image = Image.open(io.BytesIO(media.gif))

    assert image.n_frames == FRAME_COUNT
    assert image.size == (SIZE, SIZE)
    assert "loop" not in image.info  # sin NETSCAPE: se reproduce una vez
    assert len(media.gif) < 120_000


def test_la_cache_devuelve_el_mismo_render(renderer: WheelRenderer) -> None:
    assert renderer.media(9) is renderer.media(9)
