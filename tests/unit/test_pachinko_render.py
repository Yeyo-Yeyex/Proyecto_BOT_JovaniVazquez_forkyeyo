"""Pruebas de bot.services.pachinko_render: línea de tiempo y tamaño del GIF."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from bot.services.pachinko import ROWS, START_POCKET, Ball, Draw, Kind, build_volley
from bot.services.pachinko_render import (
    BALL_FRAMES,
    CENTER_STOP,
    LAUNCH_GAP,
    REACH_STOP,
    SUPER_REACH_STOP,
    PachinkoRenderer,
    ball_position,
    build_timeline,
    draw_length,
    pocket_x,
)


def ball_in(pocket: int) -> Ball:
    return Ball((1,) * pocket + (0,) * (ROWS - pocket))


MISS = Draw((1, 2, 3), Kind.MISS, False, 0)
REACH = Draw((4, 5, 4), Kind.MISS, True, 0)
RUSH = Draw((5, 5, 5), Kind.RUSH, True, 4)


@pytest.fixture(scope="module")
def renderer() -> PachinkoRenderer:
    return PachinkoRenderer()


def test_la_bola_acaba_en_su_bolsillo_y_desaparece() -> None:
    ball = ball_in(2)
    assert ball_position(ball, -1) is None
    x, _y = ball_position(ball, BALL_FRAMES - 0.01)
    assert x == pytest.approx(pocket_x(2))
    assert ball_position(ball, BALL_FRAMES) is None


def test_el_reach_y_el_super_reach_alargan_la_tirada() -> None:
    assert draw_length(MISS)[0] == CENTER_STOP
    assert draw_length(REACH)[0] == REACH_STOP
    assert draw_length(Draw((7, 7, 7), Kind.SUPER, True, 1))[0] == SUPER_REACH_STOP
    assert draw_length(RUSH)[1] > draw_length(Draw((4, 4, 4), Kind.ATARI, True, 1))[1]


def test_la_pantalla_juega_la_reserva_en_orden_sin_solaparse() -> None:
    balls = [ball_in(START_POCKET)] * 3 + [ball_in(0)] * 7
    draws = iter([REACH, MISS, RUSH])
    volley = build_volley(balls, lambda: next(draws))
    timeline = build_timeline(volley)
    first, second, third = timeline.slots
    assert first.start == first.queued == BALL_FRAMES
    assert second.queued == BALL_FRAMES + LAUNCH_GAP
    assert second.start == first.end  # esperó en la reserva
    assert third.start == second.end
    assert timeline.held(second.queued) == 1
    assert timeline.frames == third.end


def test_el_contador_del_rush_llega_a_todos_los_premios() -> None:
    volley = build_volley([ball_in(START_POCKET)] + [ball_in(3)] * 9, lambda: RUSH)
    slot = build_timeline(volley).slots[0]
    assert slot.jackpots_at(slot.center_stop - 1) == 0
    assert slot.jackpots_at(slot.center_stop) == 1
    assert slot.jackpots_at(slot.end - 1) == RUSH.jackpots


def test_el_gif_acaba_en_la_misma_imagen_que_el_png(renderer: PachinkoRenderer) -> None:
    volley = build_volley([ball_in(START_POCKET)] * 2 + [ball_in(1)] * 8, lambda: REACH)
    media = renderer.render(volley)
    gif = Image.open(io.BytesIO(media.gif))
    gif.seek(gif.n_frames - 1)
    last = gif.convert("RGB")
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert last.tobytes() == png.tobytes()
    assert media.seconds > 0


def test_el_turbo_no_hace_gif(renderer: PachinkoRenderer) -> None:
    volley = build_volley([ball_in(3)] * 10, lambda: MISS)
    media = renderer.render(volley, turbo=True)
    assert media.gif == b""
    assert media.png.startswith(b"\x89PNG")


def test_el_gif_no_se_dispara_de_tamano(renderer: PachinkoRenderer) -> None:
    """Un rush con la reserva llena es de lo más largo que puede salir."""
    draws = iter([RUSH, REACH, REACH, Draw((7, 7, 7), Kind.SUPER, True, 6)])
    volley = build_volley([ball_in(START_POCKET)] * 4 + [ball_in(0)] * 6, lambda: next(draws))
    media = renderer.render(volley)
    assert len(media.gif) < 900_000


def test_la_maquina_parada_es_un_png(renderer: PachinkoRenderer) -> None:
    assert renderer.idle_png().startswith(b"\x89PNG")
    volley = build_volley([ball_in(3)] * 10, lambda: MISS)
    assert renderer.still_png(volley).startswith(b"\x89PNG")
