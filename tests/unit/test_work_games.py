"""Pruebas de los minijuegos de `pala` (`bot.services.work_games`)."""

from __future__ import annotations

import random

from bot.services.work import Mechanic
from bot.services.work_catalog import JOBS
from bot.services.work_games import GRACE_SECONDS, MiniGame, new_game

NOW = 1_000.0


def play_perfect(game: MiniGame, *, step: float = 0.5) -> float:
    """Juega sin fallar; devuelve la hora al terminar."""
    now = game.started_at
    while not game.finished(now):
        if game.showing:
            game.hide(now)
            continue
        current = game.current
        assert current is not None
        option = (
            current.answer[game.step] if game.mechanic is Mechanic.MEMORY else current.answer[0]
        )
        assert game.press(option, now)
        now += step
    return now


def all_positions():  # noqa: ANN201
    return [position for job in JOBS for position in job.positions]


def test_jugar_perfecto_saca_100_en_todos_los_puestos() -> None:
    for position in all_positions():
        game = new_game(position, random.Random(1), now=NOW)
        play_perfect(game)
        assert game.score() == 100, position.title
        assert game.broken == 0


def test_cada_ronda_cabe_en_los_botones_de_discord() -> None:
    for position in all_positions():
        game = new_game(position, random.Random(2), now=NOW)
        for round_ in game.rounds:
            assert 2 <= len(round_.options) <= 10
            assert all(0 < len(label) <= 80 for label in round_.options)
            assert all(0 <= i < len(round_.options) for i in round_.answer)


def test_cavar_en_una_tuberia_resta() -> None:
    position = JOBS[0].position(1)
    game = new_game(position, random.Random(3), now=NOW)
    current = game.current
    assert current is not None
    # Busca una ronda con un sitio peligroso para pulsarlo.
    while not current.penalty:
        game.press(current.answer[0], NOW)
        current = game.current
        assert current is not None
    before = game.points
    assert not game.press(next(iter(current.penalty)), NOW)
    assert game.points == before - 1 and game.broken == 1


def test_en_memoria_un_fallo_acaba_la_ronda() -> None:
    position = JOBS[1].position(2)  # camarero
    game = new_game(position, random.Random(4), now=NOW)
    game.hide(NOW)
    current = game.current
    assert current is not None
    wrong = next(i for i in range(len(current.options)) if i != current.answer[0])
    assert not game.press(wrong, NOW)
    assert game.index == 1 and game.showing  # siguiente ronda, enseñando la secuencia
    assert game.perfect_rounds == 0


def test_mientras_se_ensena_la_secuencia_no_se_puede_pulsar() -> None:
    game = new_game(JOBS[1].position(1), random.Random(5), now=NOW)
    assert game.showing
    assert not game.press(game.rounds[0].answer[0], NOW)
    assert game.points == 0


def test_acabar_antes_da_un_extra_pero_la_nota_no_pasa_de_100() -> None:
    game = new_game(JOBS[2].position(3), random.Random(6), now=NOW)  # rueda de prensa
    # Falla la primera y acierta el resto deprisa.
    first = game.current
    assert first is not None
    game.press((first.answer[0] + 1) % len(first.options), NOW)
    play_perfect(game, step=0.1)
    base = round(100 * game.points / game.max_points)
    assert base < game.score() <= 100


def test_fuera_de_tiempo_no_cuenta() -> None:
    game = new_game(JOBS[0].position(1), random.Random(7), now=NOW)
    late = game.deadline + GRACE_SECONDS + 1
    current = game.current
    assert current is not None
    assert not game.press(current.answer[0], late)
    assert game.finished(late) and game.points == 0


def test_reventado_tienes_menos_tiempo() -> None:
    position = JOBS[0].position(1)
    fresh = new_game(position, random.Random(8), now=NOW)
    tired = new_game(position, random.Random(8), now=NOW, tired=True)
    assert tired.seconds < fresh.seconds


def test_el_plano_de_cavar_cambia_cada_turno() -> None:
    position = JOBS[0].position(1)
    headers = {new_game(position, random.Random(seed), now=NOW).header for seed in range(10)}
    assert len(headers) > 1
