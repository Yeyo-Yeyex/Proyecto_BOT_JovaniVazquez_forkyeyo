"""Pruebas de bot.services.chicken_render: GIF y PNG del Pollo."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from bot.services.chicken import DIFFICULTIES, DIFFICULTY_BY_KEY, ChickenGame, Status
from bot.services.chicken_render import (
    FINAL_FRAME_MS,
    VEHICLES,
    ChickenRenderer,
    H,
    W,
    tension_seconds,
    vehicle_for,
)

MEDIA = DIFFICULTY_BY_KEY["media"]


@pytest.fixture(scope="module")
def renderer() -> ChickenRenderer:
    return ChickenRenderer()


def game(*, crossed: int, hit: int | None, status: Status = Status.PLAYING) -> ChickenGame:
    g = ChickenGame(stake=500, difficulty=MEDIA, hit_lane=hit)
    g.crossed = crossed
    g.status = status
    return g


def frames_of(gif: bytes) -> tuple[list[Image.Image], list[int]]:
    image = Image.open(io.BytesIO(gif))
    frames, durations = [], []
    try:
        while True:
            frames.append(image.convert("RGB").copy())
            durations.append(image.info["duration"])
            image.seek(image.tell() + 1)
    except EOFError:
        pass
    return frames, durations


@pytest.mark.parametrize("difficulty", DIFFICULTIES, ids=lambda d: d.key)
def test_la_acera_de_cada_dificultad_es_un_png(renderer: ChickenRenderer, difficulty) -> None:  # noqa: ANN001
    png = renderer.start(difficulty, stake=100, seed=3)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)


def test_un_paso_es_un_gif_cuyo_final_es_el_png(renderer: ChickenRenderer) -> None:
    g = game(crossed=3, hit=9)
    media = renderer.hops(g, start=2, seed=7)
    frames, durations = frames_of(media.gif)
    assert len(frames) > 10
    assert durations[-1] == FINAL_FRAME_MS
    assert media.seconds == pytest.approx(sum(durations[:-1]) / 1000)
    # La espera de tensión ocupa buena parte del GIF.
    assert media.seconds >= tension_seconds(g.cents)
    board = Image.open(io.BytesIO(renderer.board(g, seed=7))).convert("RGB")
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert png.tobytes() == board.tobytes()
    # El GIF guarda solo lo que cambia: no pesa como los fotogramas enteros.
    assert len(media.gif) < 600_000


def test_atropello_y_autocobro(renderer: ChickenRenderer) -> None:
    splat = renderer.hops(game(crossed=2, hit=3, status=Status.SPLAT), start=2, seed=1)
    assert splat.gif and splat.png
    auto = game(crossed=0, hit=None)
    auto.cross_until(500)
    auto.cash_out()
    media = renderer.hops(auto, start=0, seed=1)
    frames, _durations = frames_of(media.gif)
    # Dos fotogramas por carril de la carrera más la tensión del último.
    assert len(frames) < 2 * auto.crossed + 40


def test_cobrado_y_meta(renderer: ChickenRenderer) -> None:
    cashed = game(crossed=4, hit=6, status=Status.CASHED)
    assert renderer.board(cashed, seed=2, note="¡Cobrado!")
    finished = game(crossed=MEDIA.lanes, hit=None, status=Status.CASHED)
    assert renderer.board(finished, seed=2, note="¡Meta!")


def test_sin_saltos_no_hay_gif(renderer: ChickenRenderer) -> None:
    with pytest.raises(ValueError):
        renderer.hops(game(crossed=2, hit=None), start=2, seed=1)


def test_la_tension_crece_con_lo_que_hay_en_juego() -> None:
    values = [tension_seconds(c) for c in (110, 200, 1_000, 10_000, 200_000)]
    assert values == sorted(values)
    assert values[0] >= 0.7 and values[-1] == 2.6


def test_el_vehiculo_de_cada_carril_es_fijo_por_partida() -> None:
    names = {v[1] for v in VEHICLES}
    assert vehicle_for(5, 3) == vehicle_for(5, 3)
    assert {vehicle_for(5, lane) for lane in range(1, 60)} <= names
