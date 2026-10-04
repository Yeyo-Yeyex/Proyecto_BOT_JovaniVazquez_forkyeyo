"""Pruebas de bot.services.slots_render: GIF de la tirada, PNG final y turbo."""

from __future__ import annotations

import io
import itertools

import pytest
from PIL import Image, ImageChops, ImageSequence

from bot.services.slots import REEL_STRIPS, spin_at
from bot.services.slots_render import (
    FRAME_MS,
    HEIGHT,
    OVERSHOOT,
    WIDTH,
    SlotsRenderer,
    reel_position,
)


@pytest.fixture(scope="module")
def renderer() -> SlotsRenderer:
    return SlotsRenderer()


def find(predicate):  # noqa: ANN001, ANN201
    for stops in itertools.product(*(range(len(s)) for s in REEL_STRIPS)):
        spin = spin_at(stops)
        if predicate(spin):
            return spin
    raise AssertionError("Ninguna tirada cumple la condición")


def test_el_rodillo_arranca_lejos_se_pasa_un_poco_y_para_en_su_sitio() -> None:
    assert reel_position(5, 0, 10, 10) == pytest.approx(15)
    assert reel_position(5, 7, 10, 10) == pytest.approx(5 - OVERSHOOT)
    assert reel_position(5, 10, 10, 10) == 5
    assert reel_position(5, 30, 10, 10) == 5


def test_el_gif_acaba_en_la_misma_imagen_que_el_png(renderer: SlotsRenderer) -> None:
    media = renderer.render(find(lambda s: s.pay_halves >= 4))

    gif = Image.open(io.BytesIO(media.gif))
    frames = [frame.convert("RGB") for frame in ImageSequence.Iterator(gif)]
    final = Image.open(io.BytesIO(media.png)).convert("RGB")

    assert gif.size == final.size == (WIDTH, HEIGHT)
    assert ImageChops.difference(frames[-1], final).getbbox() is None
    assert media.seconds == pytest.approx(FRAME_MS * (gif.n_frames - 1) / 1000, abs=0.5)


def test_la_anticipacion_alarga_la_animacion(renderer: SlotsRenderer) -> None:
    calm = renderer.render(find(lambda s: not s.anticipation and not s.pay_halves))
    tense = renderer.render(find(lambda s: s.anticipation and not s.pay_halves))
    assert tense.seconds > calm.seconds + 0.5


def test_el_turbo_no_hace_gif(renderer: SlotsRenderer) -> None:
    media = renderer.render(spin_at((0, 0, 0)), turbo=True)
    assert media.gif == b""
    assert media.seconds == 0
    assert media.png.startswith(b"\x89PNG")


def test_el_gif_no_se_dispara_de_tamano(renderer: SlotsRenderer) -> None:
    """El bot vive en un NAS: cada tirada debe pesar poco (la ruleta, ~50 KB)."""
    media = renderer.render(find(lambda s: s.is_jackpot))
    assert len(media.gif) < 250_000
    assert len(media.png) < 30_000


def test_la_imagen_parada_cambia_si_se_resalta_la_linea(renderer: SlotsRenderer) -> None:
    plain = renderer.still_png((1, 2, 3))
    lit = renderer.still_png((1, 2, 3), highlight=True)
    assert plain != lit
