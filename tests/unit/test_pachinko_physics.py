"""Pruebas de bot.services.pachinko_physics: geometría, simulador y biblioteca de caídas."""

from __future__ import annotations

import json
import math
import random

import pytest

from bot.services import pachinko_physics as physics
from bot.services.pachinko import BOARDS, Board
from bot.services.pachinko_physics import (
    BALL_R,
    CX,
    DECORATION_CLEARANCE,
    DECORATIONS,
    ENTRY_Y,
    MAX_SECONDS,
    MIN_SECONDS,
    PIN_R,
    TRAJECTORIES_PER_POCKET,
    Body,
    Geometry,
    Segment,
    Start,
    Trajectory,
    advance,
    bake_library,
    decode_library,
    encode_library,
    geometry_for,
    library,
    simulate,
)

ALL_BOARDS = list(BOARDS.values())


def open_field(pins: tuple[tuple[float, float], ...] = ()) -> Geometry:
    """Campo ancho con paredes lejos, solo con los clavos que se digan (para probar la física)."""
    walls = (Segment(10, 100, 10, 500), Segment(330, 100, 330, 500))
    grid = physics._build_grid(pins, walls)
    return Geometry(10, 28, 24, 400.0, pins, walls, (), grid)


# -- Geometría ----------------------------------------------------------------------


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_hay_un_bolsillo_mas_que_filas_y_estan_centrados(board: Board) -> None:
    geometry = geometry_for(board)
    assert len(geometry.dividers) == board.rows
    centers = [geometry.pocket_x(k) for k in range(board.rows + 1)]
    assert centers == sorted(centers)
    assert (centers[0] + centers[-1]) / 2 == CX
    assert {round(b - a, 6) for a, b in zip(centers, centers[1:], strict=False)} == {geometry.dx}
    # Cada separador cae justo entre dos bolsillos.
    for k, divider in enumerate(geometry.dividers):
        assert divider.x0 == divider.x1 == (centers[k] + centers[k + 1]) / 2


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_los_clavos_estan_dentro_del_campo_y_dejan_pasar_a_la_bola(board: Board) -> None:
    geometry = geometry_for(board)
    gap = 2 * BALL_R + 2 * PIN_R
    for x, y in geometry.pins:
        assert geometry.left + gap < x < geometry.right - gap
        assert physics.PIN_TOP <= y < geometry.pocket_top - BALL_R
    for i, (x, y) in enumerate(geometry.pins):
        for x2, y2 in geometry.pins[i + 1 :]:
            assert math.hypot(x - x2, y - y2) > gap + 2


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_los_clavos_no_tapan_los_adornos(board: Board) -> None:
    for x, y in geometry_for(board).pins:
        for mx, my in DECORATIONS:
            assert math.hypot(x - mx, y - my) >= DECORATION_CLEARANCE


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_ultima_fila_de_clavos_cae_sobre_los_separadores(board: Board) -> None:
    geometry = geometry_for(board)
    last_y = max(y for _x, y in geometry.pins)
    last_row = sorted(x for x, y in geometry.pins if y == last_y)
    assert last_row == [d.x0 for d in geometry.dividers]


def test_cada_tablero_tiene_su_propio_campo_aunque_compartan_filas() -> None:
    assert geometry_for(BOARDS["clasica"]) == geometry_for(BOARDS["dragon"])
    assert geometry_for(BOARDS["clasica"]) != geometry_for(BOARDS["oni"])


# -- Simulador ----------------------------------------------------------------------


def test_sin_clavos_la_bola_cae_en_caida_libre(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(physics, "MIN_SECONDS", 0.0)
    geometry = open_field()
    fall = simulate(geometry, Start(170.0, 0.0))
    assert fall is not None
    assert fall.bounces == 0
    for index, (x, y) in enumerate(fall.points[:-1]):
        t = index * physics.FRAME_MS / 1000
        assert x == 170.0
        assert y == pytest.approx(ENTRY_Y + physics.GRAVITY * t * t / 2, abs=2.0)


def test_un_clavo_desvia_a_la_bola_y_cuenta_un_rebote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(physics, "MIN_SECONDS", 0.0)
    pin = (170.0, 260.0)
    right = simulate(open_field((pin,)), Start(171.0, 0.0))
    left = simulate(open_field((pin,)), Start(169.0, 0.0))
    assert right is not None and left is not None
    assert right.bounces == left.bounces == 1
    # Cae de un lado o del otro del clavo, según de qué lado le dio.
    assert right.points[-1][0] > 171.0
    assert left.points[-1][0] < 169.0


def test_la_bola_no_sale_del_campo_por_las_paredes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(physics, "MIN_SECONDS", 0.0)
    geometry = open_field()
    for vx in (-40.0, 40.0):
        fall = simulate(geometry, Start(18.0 if vx < 0 else 322.0, vx * 10))
        assert fall is not None
        assert all(10 + BALL_R <= x <= 330 - BALL_R for x, _y in fall.points)


def test_una_bola_que_se_queda_encajada_se_descarta() -> None:
    # Dos clavos casi juntos hacen de cuna: la bola se asienta encima y no avanza.
    cradle = ((CX - 4.0, 250.0), (CX + 4.0, 250.0))
    assert simulate(open_field(cradle), Start(float(CX), 0.0)) is None


def test_una_caida_demasiado_corta_se_descarta() -> None:
    assert (
        simulate(open_field(), Start(170.0, 0.0)) is None
    )  # caída libre: ~0,5 s, menos de lo admitido


def test_simular_dos_veces_lo_mismo_da_lo_mismo() -> None:
    geometry = geometry_for(BOARDS["clasica"])
    rng = random.Random(4)
    for _ in range(40):
        start = physics.random_start(geometry, rng)
        assert simulate(geometry, start) == simulate(geometry, start)


def test_los_puntos_estan_redondeados_a_una_decima() -> None:
    fall = library(BOARDS["oni"])[3][0]
    for x, y in fall.points:
        assert round(x * 10) == pytest.approx(x * 10)
        assert round(y * 10) == pytest.approx(y * 10)


# -- Varias bolas a la vez ------------------------------------------------------------


def free_field(monkeypatch: pytest.MonkeyPatch) -> Geometry:
    """Campo sin clavos y sin duración mínima: solo se ve lo que hacen las bolas entre sí."""
    monkeypatch.setattr(physics, "MIN_SECONDS", 0.0)
    return open_field()


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_una_bola_sola_cae_exactamente_como_en_la_biblioteca(board: Board) -> None:
    """Sin otra bola no hay choques: `advance` repite la cuenta de `simulate` bit a bit.

    Es lo que garantiza que una bola que sale cuando no queda ninguna en el aire
    (el peor caso de `pachinko_motion`) cae igual que su caída de la biblioteca.
    """
    geometry = geometry_for(board)
    for pocket, items in library(board).items():
        for saved in items:
            (entered,) = advance(geometry, [Body.spawn(0, pocket, saved.start, 0)], 0) or [None]
            assert entered is not None
            assert tuple(entered.points) == saved.points
            assert entered.bounces == saved.bounces
            assert entered.hits == 0


def test_dos_bolas_que_se_acercan_chocan_y_se_reparten_el_impulso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = free_field(monkeypatch)
    left = Body.spawn(0, geometry.pocket_at(126.0), Start(166.0, 100.0), 0)
    right = Body.spawn(1, geometry.pocket_at(214.0), Start(174.0, -100.0), 0)
    entered = advance(geometry, [left, right], 0)
    assert entered is not None and len(entered) == 2
    assert left.hits == right.hits == 1
    # Misma masa y acercamiento de 200 px/s: salen con el 80 % (restitución 0,8) en
    # sentido contrario, simétricas respecto al punto de choque.
    assert left.points[-1][0] < 166.0 and right.points[-1][0] > 174.0
    assert (left.points[-1][0] + right.points[-1][0]) / 2 == pytest.approx(170.0, abs=0.2)
    # Cada una sale a 200 × 0,8 / 2 = 80 px/s hacia su lado durante lo que dura la caída libre.
    fall_seconds = math.sqrt(2 * (geometry.end_y - ENTRY_Y) / physics.GRAVITY)
    assert left.points[-1][0] == pytest.approx(166.0 - 80.0 * fall_seconds, abs=2.0)


def test_dos_bolas_lejanas_no_chocan(monkeypatch: pytest.MonkeyPatch) -> None:
    geometry = free_field(monkeypatch)
    a = Body.spawn(0, geometry.pocket_at(166.0), Start(166.0, 0.0), 0)
    b = Body.spawn(1, geometry.pocket_at(240.0), Start(240.0, 0.0), 0)
    assert advance(geometry, [a, b], 0) is not None
    assert a.hits == b.hits == 0
    assert a.points[-1][0] == 166.0 and b.points[-1][0] == 240.0


def test_apoyarse_despacio_una_bola_en_otra_no_cuenta_como_choque(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se separan igual, pero un roce a menos de `BALL_HIT_MIN_SPEED` no suma."""
    geometry = free_field(monkeypatch)
    a = Body.spawn(0, geometry.pocket_at(166.0), Start(166.0, 10.0), 0)
    b = Body.spawn(1, geometry.pocket_at(174.0), Start(174.0, -10.0), 0)
    assert advance(geometry, [a, b], 0) is not None
    assert a.hits == b.hits == 0
    assert a.points[-1][0] < b.points[-1][0]


def test_el_sistema_se_descarta_si_una_bola_cae_en_otro_bolsillo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = free_field(monkeypatch)
    wrong = Body.spawn(0, geometry.pocket_at(166.0) + 1, Start(166.0, 0.0), 0)
    assert advance(geometry, [wrong], 0) is None


def test_se_puede_parar_a_mitad_para_sacar_una_instantanea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parar y seguir es lo mismo que correr de un tirón (la simulación es determinista)."""
    geometry = free_field(monkeypatch)
    start = Start(120.0, 40.0)
    target = geometry.pocket_at(120.0 + 40.0 * 0.5)
    whole = Body.spawn(0, target, start, 0)
    advance(geometry, [whole], 0)
    body = Body.spawn(0, target, start, 0)
    halves = [body]
    assert advance(geometry, halves, 0, stop=100) == []
    assert halves == [body] and body.y < geometry.end_y
    advance(geometry, halves, 100)
    assert body.points == whole.points


# -- Biblioteca ---------------------------------------------------------------------


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_biblioteca_tiene_las_caidas_justas_en_cada_bolsillo(board: Board) -> None:
    falls = library(board)
    assert sorted(falls) == list(range(board.rows + 1))
    assert all(len(items) == TRAJECTORIES_PER_POCKET for items in falls.values())
    for pocket, items in falls.items():
        assert len({fall.start for fall in items}) == len(items)
        assert all(fall.pocket == pocket for fall in items)


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_cada_caida_acaba_en_su_bolsillo_dentro_del_campo_y_dura_lo_justo(board: Board) -> None:
    geometry = geometry_for(board)
    for pocket, items in library(board).items():
        for fall in items:
            x, y = fall.points[-1]
            assert geometry.pocket_at(x) == pocket
            assert abs(x - geometry.pocket_x(pocket)) < geometry.dx / 2
            assert y >= geometry.pocket_top
            assert all(geometry.left < px < geometry.right for px, _py in fall.points)
            assert fall.points[0] == (fall.start.x0, ENTRY_Y)
            assert MIN_SECONDS - 0.05 <= fall.seconds <= MAX_SECONDS + 0.05
            assert fall.bounces >= 0


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_biblioteca_guardada_se_puede_reproducir(board: Board) -> None:
    """Si falla, hay que regenerar `trayectorias.json` con `docs/pachinko_trayectorias.py`.

    Vuelve a simular la condición inicial de cada caída guardada: da los mismos
    puntos, rebotes y bolsillo (la física es exacta en cualquier máquina).
    """
    geometry = geometry_for(board)
    for items in library(board).values():
        for saved in items:
            assert simulate(geometry, saved.start) == saved


def test_las_caidas_no_son_todas_iguales() -> None:
    """Una biblioteca útil tiene caídas distintas: rápidas, lentas, con más o menos golpes."""
    for board in ALL_BOARDS:
        falls = [fall for items in library(board).values() for fall in items]
        assert len({fall.bounces for fall in falls}) >= 8
        assert len({fall.frames for fall in falls}) >= 8


def test_hornear_con_la_misma_semilla_da_la_misma_biblioteca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(physics, "TRAJECTORIES_PER_POCKET", 2)
    board = BOARDS["sakura"]
    first = bake_library(board, random.Random("a"))
    assert first == bake_library(board, random.Random("a"))
    assert all(len(items) == 2 for items in first.values())
    assert first != bake_library(board, random.Random("b"))


def test_hornear_sin_presupuesto_falla_con_un_mensaje_claro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(physics, "BAKE_BUDGET", 3)
    with pytest.raises(RuntimeError, match="faltan"):
        bake_library(BOARDS["oni"], random.Random(1))


def test_codificar_y_decodificar_la_biblioteca_no_pierde_nada() -> None:
    board = BOARDS["clasica"]
    original = library(board)
    text = encode_library({board.key: original})
    assert decode_library(json.loads(text)[board.key]) == original


def test_un_tablero_sin_biblioteca_pide_regenerar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(physics, "_load_all", lambda: {})
    with pytest.raises(RuntimeError, match="pachinko_trayectorias"):
        library(BOARDS["oni"])


def test_el_archivo_de_trayectorias_no_pasa_de_un_mega_y_medio() -> None:
    assert physics.DATA_PATH.stat().st_size < 1_500_000


def test_las_cifras_que_usan_los_logros_son_las_de_la_biblioteca() -> None:
    """Los textos de los logros de rebotes y de duración dicen estas cifras."""
    from bot.services.achievements import (
        PACHINKO_CLEAN_BOUNCES,
        PACHINKO_SLOW_FRAMES,
        PACHINKO_SWIFT_FRAMES,
    )

    falls: list[Trajectory] = [
        fall for board in ALL_BOARDS for items in library(board).values() for fall in items
    ]
    assert max(f.bounces for f in falls) == 21  # «Récord del Congreso»
    assert max(f.frames for f in falls) == PACHINKO_SLOW_FRAMES + 1
    assert min(f.frames for f in falls) == PACHINKO_SWIFT_FRAMES - 1
    assert any(f.bounces <= PACHINKO_CLEAN_BOUNCES for f in falls)
    assert round((PACHINKO_SLOW_FRAMES - 1) * physics.FRAME_MS / 1000, 2) == 1.95
    assert round((PACHINKO_SWIFT_FRAMES - 1) * physics.FRAME_MS / 1000, 2) == 1.1
