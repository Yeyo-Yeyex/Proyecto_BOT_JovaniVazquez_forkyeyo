"""Pruebas de bot.services.pachinko_motion: el movimiento de una tanda con choques entre bolas.

La prueba clave es que el dinero no depende de la física: con las bolas chocando
de verdad, cada una acaba en el bolsillo que dijo el sorteo. El resto comprueba
que el movimiento es una simulación de verdad (se puede repetir desde cero),
determinista, que la caché está acotada y que el caso de retraso, que garantiza
que la búsqueda termina, funciona.
"""

from __future__ import annotations

import random

import pytest

from bot.services import pachinko_motion as motion_module
from bot.services.pachinko import (
    BOARDS,
    CLASSIC,
    Ball,
    Board,
    Draw,
    Kind,
    PachinkoMachine,
    build_volley,
)
from bot.services.pachinko_motion import (
    LAUNCH_GAP,
    MOTION_CACHE,
    BallMotion,
    VolleyMotion,
    motion_for,
)
from bot.services.pachinko_physics import (
    ENTRY_Y,
    MAX_SECONDS,
    MIN_SECONDS,
    STEPS_PER_FRAME,
    Body,
    advance,
    geometry_for,
    library,
)

ALL_BOARDS = list(BOARDS.values())
MISS = Draw((1, 2, 3), Kind.MISS, False, 0)

#: Tandas al azar por tablero. Cada una cuesta ~0,08 s; con 60 por tablero la prueba
#: tarda unos 20 s en total y cubre 2.400 bolas chocando.
VOLLEYS_PER_BOARD = 60

#: Una tanda de la Clásica con varios choques (bolas 4 y 5 se tocan cinco veces): el
#: camino de cada bola y la caída preferida de la biblioteca.
CRASHING = [
    ((0, 1, 1, 0, 0, 0, 0, 0, 0, 0), 12),
    ((0, 1, 1, 1, 1, 1, 0, 1, 0, 1), 7),
    ((0, 1, 1, 1, 1, 0, 1, 0, 0, 1), 19),
    ((1, 0, 0, 1, 0, 0, 1, 0, 0, 0), 17),
    ((0, 1, 0, 0, 1, 1, 1, 1, 1, 0), 5),
    ((0, 1, 1, 1, 0, 1, 0, 1, 1, 0), 16),
    ((0, 1, 0, 0, 1, 1, 0, 1, 0, 1), 8),
    ((1, 1, 1, 0, 1, 0, 0, 0, 0, 0), 9),
    ((0, 0, 1, 1, 0, 1, 0, 0, 1, 1), 13),
    ((1, 0, 0, 1, 0, 0, 0, 0, 1, 0), 16),
]


def random_volley(board: Board, seed: int):  # noqa: ANN201
    """Una tanda al azar con semilla fija, como la lanza el juego."""
    rng = random.Random(f"{board.key}-{seed}")
    return PachinkoMachine(rng.randrange, rng.randrange).launch(board)


def crashing_volley():  # noqa: ANN201
    balls = [Ball(path, trajectory) for path, trajectory in CRASHING]
    return build_volley(CLASSIC, balls, lambda: MISS)


def replay(volley, motion: VolleyMotion) -> list[Body]:  # noqa: ANN001
    """Vuelve a simular la tanda desde cero con las salidas y los lanzamientos del movimiento.

    No usa la búsqueda: solo `advance`, metiendo cada bola en su fotograma. Si el
    movimiento es una simulación de verdad, sale exactamente lo mismo.
    """
    geometry = geometry_for(volley.board)
    bodies: list[Body] = []
    done: dict[int, Body] = {}
    step = 0
    for ident, ball in sorted(enumerate(motion.balls), key=lambda item: item[1].launch):
        launch_step = ball.launch * STEPS_PER_FRAME
        if launch_step > step:
            entered = advance(geometry, bodies, step, launch_step)
            assert entered is not None
            done.update((body.ident, body) for body in entered)
            step = launch_step
        bodies.append(Body.spawn(ident, ball.pocket, ball.start, launch_step))
    entered = advance(geometry, bodies, step)
    assert entered is not None
    done.update((body.ident, body) for body in entered)
    return [done[ident] for ident in range(len(motion.balls))]


# -- El dinero no depende de la física ---------------------------------------------------


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_con_choques_cada_bola_acaba_en_el_bolsillo_sorteado(board: Board) -> None:
    """La prueba clave: el bolsillo sale del sorteo y la física obedece, choque o no choque."""
    geometry = geometry_for(board)
    collisions = 0
    for seed in range(VOLLEYS_PER_BOARD):
        volley = random_volley(board, seed)
        motion = motion_for(volley)
        assert len(motion.balls) == len(volley.balls)
        for ball, move in zip(volley.balls, motion.balls, strict=True):
            assert move.pocket == ball.pocket
            x, y = move.points[-1]
            assert geometry.pocket_at(x) == ball.pocket
            assert y >= geometry.pocket_top
            assert all(geometry.left < px < geometry.right for px, _py in move.points)
            assert MIN_SECONDS - 0.05 <= (move.frames - 1) * 0.05 <= MAX_SECONDS + 0.05
        collisions += motion.collisions
    assert collisions > 0  # en 60 tandas hay choques de verdad


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_el_movimiento_se_puede_repetir_desde_cero_con_el_simulador(board: Board) -> None:
    """No es un empujón: volver a simular lanzamientos y salidas da los mismos puntos y cuentas."""
    for seed in range(5):
        volley = random_volley(board, seed)
        motion = motion_for(volley)
        for move, body in zip(motion.balls, replay(volley, motion), strict=True):
            assert tuple(body.points) == move.points
            assert body.bounces == move.bounces
            assert body.hits == move.collisions


def test_una_tanda_con_choques_sigue_siendo_una_simulacion_completa() -> None:
    volley = crashing_volley()
    motion = motion_for(volley)
    assert motion.collisions > 0
    for move, body in zip(motion.balls, replay(volley, motion), strict=True):
        assert tuple(body.points) == move.points


# -- Choques de verdad ---------------------------------------------------------------------


def test_hay_choques_y_las_bolas_que_chocan_no_caen_como_en_la_biblioteca() -> None:
    volley = crashing_volley()
    motion = motion_for(volley)
    assert motion.collisions >= 3
    assert motion.collisions == sum(move.collisions for move in motion.balls) // 2
    crashed = [move for move in motion.balls if move.collisions]
    assert crashed
    for ball, move in zip(volley.balls, motion.balls, strict=True):
        alone = library(CLASSIC)[ball.pocket][ball.trajectory]
        if move.collisions:
            assert move.points != alone.points


def test_una_bola_que_no_toca_a_nadie_cae_como_su_salida_en_la_biblioteca() -> None:
    """Si nadie la roza, la bola sigue exactamente la caída guardada de su salida."""
    volley = crashing_volley()
    motion = motion_for(volley)
    lonely = 0
    for move in motion.balls:
        if move.collisions == 0:
            saved = next(fall for fall in library(CLASSIC)[move.pocket] if fall.start == move.start)
            assert move.points == saved.points
            assert move.bounces == saved.bounces
            lonely += 1
    assert lonely >= 1


# -- Lanzamientos --------------------------------------------------------------------------


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_se_lanza_en_fotogramas_enteros_y_en_orden(board: Board) -> None:
    for seed in range(10):
        motion = motion_for(random_volley(board, seed))
        assert motion.balls[0].launch == 0
        for before, after in zip(motion.balls, motion.balls[1:], strict=False):
            assert isinstance(after.launch, int)
            assert after.launch >= before.launch + LAUNCH_GAP
            # Un retraso desplaza a las siguientes: nunca se pierde el hueco entre bolas.
            assert after.launch == before.launch + LAUNCH_GAP + after.delay
        for move in motion.balls:
            assert move.landing == move.launch + len(move.points)
            assert move.points[0] == (move.start.x0, ENTRY_Y)


def test_las_bolas_sin_retraso_salen_cada_hueco_normal() -> None:
    motion = motion_for(crashing_volley())
    if not motion.delayed:
        assert [move.launch for move in motion.balls] == [i * LAUNCH_GAP for i in range(10)]


# -- Determinismo y caché -------------------------------------------------------------------


def test_la_misma_tanda_da_siempre_el_mismo_movimiento() -> None:
    volley = crashing_volley()
    first = motion_for(volley)
    motion_module._plan.cache_clear()
    again = motion_for(volley)
    assert again == first
    assert again is not first  # recalculado, no sacado de la caché


def test_pedir_dos_veces_la_misma_tanda_usa_la_cache() -> None:
    motion_module._plan.cache_clear()
    volley = crashing_volley()
    assert motion_for(volley) is motion_for(volley)
    info = motion_module._plan.cache_info()
    assert (info.hits, info.misses) == (1, 1)


def test_el_sorteo_no_cambia_el_movimiento_de_las_bolas() -> None:
    """Solo cuentan el tablero y las bolas: dos sorteos distintos comparten el cálculo."""
    balls = [Ball(path, trajectory) for path, trajectory in CRASHING]
    miss = build_volley(CLASSIC, balls, lambda: MISS)
    atari = build_volley(CLASSIC, balls, lambda: Draw((5, 5, 5), Kind.RUSH, True, 4))
    assert motion_for(miss) is motion_for(atari)


def test_la_cache_esta_acotada(monkeypatch: pytest.MonkeyPatch) -> None:
    info = motion_module._plan.cache_info()
    assert info.maxsize == MOTION_CACHE <= 64
    # Con una búsqueda de mentira (la real tarda ~0,1 s) se llena de sobra la caché.
    calls = []

    def fake_plan(board: Board, picks: tuple) -> VolleyMotion:
        calls.append(picks)
        return VolleyMotion(())

    cached = motion_module.functools.lru_cache(maxsize=MOTION_CACHE)(fake_plan)
    monkeypatch.setattr(motion_module, "_plan", cached)
    for index in range(MOTION_CACHE * 2):
        volley = build_volley(
            CLASSIC, [Ball((0,) * 10, index % 24), Ball((1,) * 10, index)], lambda: MISS
        )
        motion_for(volley)
    assert cached.cache_info().currsize == MOTION_CACHE


# -- El peor caso: esperar a que no quede ninguna bola en el aire -------------------------------


@pytest.mark.parametrize("tries", [0, 1])
def test_si_ninguna_salida_sirve_la_bola_espera_y_acaba_en_su_bolsillo(
    monkeypatch: pytest.MonkeyPatch, tries: int
) -> None:
    """Sin candidatas (`MAX_TRIES` a 0) o con una sola, la búsqueda termina siempre retrasando."""
    monkeypatch.setattr(motion_module, "MAX_TRIES", tries)
    motion_module._plan.cache_clear()
    volley = crashing_volley()
    motion = motion_for(volley)
    assert motion.delayed >= 1
    for ball, move in zip(volley.balls, motion.balls, strict=True):
        assert move.pocket == ball.pocket
        assert geometry_for(CLASSIC).pocket_at(move.points[-1][0]) == ball.pocket
    # Y sigue siendo una simulación completa.
    for move, body in zip(motion.balls, replay(volley, motion), strict=True):
        assert tuple(body.points) == move.points
    motion_module._plan.cache_clear()


def test_con_cero_intentos_cada_bola_sale_con_el_aire_vacio_y_cae_como_en_la_biblioteca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El argumento de que la búsqueda termina: sola en el aire, su caída es la guardada."""
    monkeypatch.setattr(motion_module, "MAX_TRIES", 0)
    motion_module._plan.cache_clear()
    volley = crashing_volley()
    motion = motion_for(volley)
    for ball, move in zip(volley.balls, motion.balls, strict=True):
        saved = library(CLASSIC)[ball.pocket][ball.trajectory]
        assert move.points == saved.points
        assert move.bounces == saved.bounces
        assert move.collisions == 0
    for before, after in zip(motion.balls, motion.balls[1:], strict=False):
        assert after.launch >= before.landing - 1  # espera a que la anterior haya entrado
    motion_module._plan.cache_clear()


def test_una_sola_bola_cae_como_su_caida_de_la_biblioteca() -> None:
    ball = Ball((1, 0) * 5, 3)
    motion = motion_for(build_volley(CLASSIC, [ball], lambda: MISS))
    saved = library(CLASSIC)[ball.pocket][3]
    (move,) = motion.balls
    assert isinstance(move, BallMotion)
    assert (move.points, move.bounces, move.collisions) == (saved.points, saved.bounces, 0)
    assert move.launch == 0 and move.delay == 0 and move.rejected == 0
