"""Pruebas de bot.services.craps_render: la tirada de los dados y lo que se ve en cada fotograma.

Casi todo es matemática pura (cámara, giros, física, estados), sin dibujar. Solo las
pruebas de `paint`, `encode` y `CrapsRenderer` generan imágenes de verdad.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable

import pytest
from craps_fakes import game_after, table_for
from PIL import Image

from bot.services.craps import (
    POINTS,
    TOTAL_NAMES,
    Bet,
    CrapsGame,
    Hand,
    Roll,
    format_odds,
)
from bot.services.craps_render import (
    BOX_X,
    BOX_Y,
    CHIP_COLORS,
    FACES,
    FINAL_FRAME_MS,
    FRAME_MS,
    HALF,
    OPENING_REST,
    PUCK_OFF,
    REST_GAP,
    REST_X,
    REST_Y,
    REVEAL_FRAMES,
    TABLE_NEAR,
    WALL_HEIGHT,
    WALL_Y,
    CrapsRenderer,
    DieRest,
    H,
    W,
    banner_for,
    board_state,
    chip_color,
    chip_stack,
    die_geometry,
    encode,
    face_up,
    floor_rows,
    floor_y,
    meta_state,
    paint,
    panel_state,
    project,
    puck,
    resting_height,
    roll_kind,
    rotation,
    simulate,
    throw_seconds,
    throw_states,
    value_up,
    wall_rows,
    wall_z,
)

PASS, DONT = Bet.PASS, Bet.DONT
COMBINATIONS = [(a, b) for a in range(1, 7) for b in range(1, 7)]


def hand_of(game: CrapsGame) -> Hand:
    """La mano que ha visto las tiradas de `game`."""
    return table_for(game).hand


def shown(amount: int) -> str:
    """Cómo se escribe una cantidad en el panel (`1.500 Y$`)."""
    return f"{amount:,}".replace(",", ".") + " Y$"


# -- Cámara -----------------------------------------------------------------------------


@pytest.mark.parametrize("y", [TABLE_NEAR, 0.0, 100.0, 200.0, WALL_Y])
def test_floor_y_deshace_la_proyeccion_del_tapete(y: float) -> None:
    assert floor_y(project(0, y, 0)[1]) == pytest.approx(y, abs=1e-6)


@pytest.mark.parametrize("z", [0.0, 15.0, WALL_HEIGHT])
def test_wall_z_deshace_la_proyeccion_de_la_pared(z: float) -> None:
    assert wall_z(project(0, WALL_Y, z)[1]) == pytest.approx(z, abs=1e-6)


def test_lo_que_esta_mas_lejos_se_ve_mas_arriba_y_mas_pequeno() -> None:
    near_x, near_y, near_depth = project(100, 0, 0)
    far_x, far_y, far_depth = project(100, WALL_Y, 0)
    assert far_y < near_y and far_depth > near_depth
    assert abs(far_x - project(0, WALL_Y, 0)[0]) < abs(near_x - project(0, 0, 0)[0])


def test_algo_en_el_aire_se_ve_mas_arriba_que_en_el_tapete() -> None:
    assert project(0, 100, 50)[1] < project(0, 100, 0)[1]


def test_con_la_camara_centrada_la_x_cero_cae_en_el_centro_de_la_vista() -> None:
    assert project(0, 100, 0)[0] == project(0, 250, 40)[0]


def test_floor_rows_recorre_el_tapete_de_arriba_a_abajo_con_bordes_ordenados() -> None:
    rows = floor_rows()
    assert rows[0][0] < rows[-1][0] and rows[-1][0] < H
    ys = [r[0] for r in rows]
    assert ys == sorted(ys)
    for _, y0, y1, left, right in rows:
        assert TABLE_NEAR <= y0 <= y1 <= WALL_Y
        assert left < right
    # Más cerca, el tapete se ve más ancho.
    assert rows[-1][4] - rows[-1][3] > rows[0][4] - rows[0][3]


def test_wall_rows_cubre_la_pared_y_sus_alturas_estan_dentro() -> None:
    rows = wall_rows()
    assert rows
    for _, z0, z1, left, right in rows:
        assert 0 <= z0 <= z1 <= WALL_HEIGHT
        assert left < right


def test_la_escala_de_las_filas_da_mas_filas() -> None:
    assert len(floor_rows(2)) > len(floor_rows(1))


# -- Giros ------------------------------------------------------------------------------


def test_un_giro_nulo_o_sin_eje_es_la_identidad() -> None:
    identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    assert rotation((0, 0, 1), 0.0) == identity
    assert rotation((0, 0, 0), 1.0) == identity


def test_un_cuarto_de_vuelta_sobre_la_vertical_lleva_x_a_y() -> None:
    m = rotation((0, 0, 1), math.pi / 2)
    assert m[0] == pytest.approx((0, -1, 0), abs=1e-9)
    assert m[1] == pytest.approx((1, 0, 0), abs=1e-9)
    assert m[2] == pytest.approx((0, 0, 1), abs=1e-9)


def test_el_eje_no_hace_falta_normalizado() -> None:
    a = rotation((0, 0, 5), 0.7)
    b = rotation((0, 0, 1), 0.7)
    for row_a, row_b in zip(a, b, strict=True):
        assert row_a == pytest.approx(row_b)


def test_las_caras_opuestas_suman_siete_y_miran_al_reves() -> None:
    for value, (normal, _, _) in FACES.items():
        opposite = FACES[7 - value][0]
        assert tuple(-c for c in normal) == opposite


@pytest.mark.parametrize("value", range(1, 7))
@pytest.mark.parametrize("yaw", [0.0, 0.7, -2.0, math.pi])
def test_face_up_deja_arriba_el_valor_pedido(value: int, yaw: float) -> None:
    assert value_up(face_up(value, yaw)) == value


@pytest.mark.parametrize("value", range(1, 7))
def test_un_dado_quieto_apoya_una_cara_entera_en_el_tapete(value: int) -> None:
    assert resting_height(face_up(value, 0.3)) == pytest.approx(HALF)


def test_un_dado_apoyado_en_una_arista_queda_mas_alto_que_plano() -> None:
    tilted = rotation((1, 0, 0), math.pi / 4)
    assert resting_height(tilted) == pytest.approx(HALF * math.sqrt(2))
    assert resting_height(tilted) > resting_height(face_up(1, 0.0))


# -- die_geometry -----------------------------------------------------------------------


def test_un_dado_quieto_con_el_1_arriba_ensena_el_1_y_no_el_6() -> None:
    geometry = die_geometry(0.0, 150.0, 0.0, face_up(1, 0.4))
    visible = {face["v"] for face in geometry["faces"]}
    assert 1 in visible
    assert 6 not in visible  # la de abajo mira al tapete
    assert 1 <= len(visible) <= 3  # un cubo enseña como mucho tres caras


@pytest.mark.parametrize("value", range(1, 7))
def test_el_valor_de_arriba_siempre_es_una_cara_visible_y_la_opuesta_no(value: int) -> None:
    visible = {f["v"] for f in die_geometry(0.0, 150.0, 0.0, face_up(value, 0.2))["faces"]}
    assert value in visible and 7 - value not in visible


def test_la_geometria_trae_silueta_sombra_y_caras_con_cuatro_esquinas() -> None:
    geometry = die_geometry(10.0, 120.0, 0.0, face_up(3, 0.0))
    assert len(geometry["hull"]) >= 3
    assert all(len(face["q"]) == 4 for face in geometry["faces"])
    assert all(0 <= face["l"] <= 1 for face in geometry["faces"])
    assert set(geometry["shadow"]) == {"x", "y", "rx", "ry", "a"}
    assert geometry["depth"] > 0


def test_en_el_aire_el_dado_sube_en_pantalla_y_su_sombra_se_aclara() -> None:
    m = face_up(2, 0.0)
    low = die_geometry(0.0, 150.0, 0.0, m)
    high = die_geometry(0.0, 150.0, 100.0, m)
    assert high["y"] < low["y"]
    assert high["shadow"]["a"] < low["shadow"]["a"]
    assert high["shadow"]["y"] == low["shadow"]["y"]  # la sombra se queda en el tapete


def test_un_dado_mas_lejos_tiene_mas_profundidad() -> None:
    m = face_up(4, 0.0)
    assert die_geometry(0, 220.0, 0, m)["depth"] > die_geometry(0, 100.0, 0, m)["depth"]


def test_dierest_geometry_es_la_geometria_del_dado_quieto() -> None:
    rest = DieRest(12.0, 140.0, 0.5, 5)
    assert rest.geometry() == die_geometry(12.0, 140.0, 0.0, face_up(5, 0.5))


def test_los_dados_de_la_mesa_recien_abierta_son_un_3_y_un_4() -> None:
    assert [r.value for r in OPENING_REST] == [3, 4]


# -- simulate ---------------------------------------------------------------------------


@pytest.mark.parametrize("dice", COMBINATIONS)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_simulate_deja_arriba_el_valor_pedido_en_las_36_combinaciones(
    dice: tuple[int, int], seed: int
) -> None:
    tracks, rests = simulate(dice, seed)
    assert [value_up(track.spin[-1]) for track in tracks] == list(dice)
    assert [rest.value for rest in rests] == list(dice)
    assert [value_up(face_up(r.value, r.yaw)) for r in rests] == list(dice)


@pytest.mark.parametrize("seed", range(12))
def test_los_dados_quietos_quedan_dentro_de_la_mesa_visible_y_separados(seed: int) -> None:
    dice = COMBINATIONS[seed * 3 % len(COMBINATIONS)]
    _, rests = simulate(dice, seed)
    for rest in rests:
        assert REST_X[0] <= rest.x <= REST_X[1]
        assert REST_Y[0] <= rest.y <= REST_Y[1]
    a, b = rests
    assert math.dist((a.x, a.y), (b.x, b.y)) >= REST_GAP


@pytest.mark.parametrize("seed", range(6))
def test_ambos_recorridos_tienen_los_mismos_fotogramas(seed: int) -> None:
    tracks, _ = simulate((3, 5), seed)
    a, b = tracks
    frames = len(a.x)
    assert frames > 1
    for track in tracks:
        assert len(track.x) == len(track.y) == len(track.lift) == len(track.spin) == frames
    assert len(b.x) == frames


def test_los_dados_acaban_quietos_en_el_tapete() -> None:
    tracks, rests = simulate((2, 6), 4)
    for track, rest in zip(tracks, rests, strict=True):
        assert track.lift[-1] == 0.0
        assert (track.x[-1], track.y[-1]) == (rest.x, rest.y)
        assert track.x[-1] == track.x[-2] and track.y[-1] == track.y[-2]


def test_los_dados_entran_por_abajo_y_vuelan_antes_de_parar() -> None:
    tracks, _ = simulate((4, 4), 7)
    for track in tracks:
        assert track.y[0] < track.y[-1]
        assert max(track.lift) > 0
        assert all(lift >= 0 for lift in track.lift)


def test_la_misma_semilla_da_la_misma_tirada_y_otra_semilla_otra_distinta() -> None:
    assert simulate((3, 6), 99) == simulate((3, 6), 99)
    assert simulate((3, 6), 99) != simulate((3, 6), 100)


def test_el_valor_de_los_dados_no_cambia_el_recorrido_de_la_semilla() -> None:
    a, _ = simulate((1, 1), 5)
    b, _ = simulate((6, 6), 5)
    assert [t.x for t in a] == [t.x for t in b]
    assert [t.y for t in a] == [t.y for t in b]


# -- roll_kind y carteles ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("roll", "bet", "kind"),
    [
        (Roll((3, 4)), PASS, "win"),
        (Roll((5, 6)), DONT, "lose"),
        (Roll((1, 1)), PASS, "lose"),
        (Roll((1, 2)), DONT, "win"),
        (Roll((6, 6)), PASS, "lose"),
        (Roll((6, 6)), DONT, "push"),
        (Roll((1, 5)), PASS, "point"),
        (Roll((2, 2)), DONT, "point"),
        (Roll((2, 2), 4), PASS, "win"),
        (Roll((2, 2), 4), DONT, "lose"),
        (Roll((3, 4), 4), PASS, "lose"),
        (Roll((3, 4), 4), DONT, "win"),
        (Roll((6, 5), 4), PASS, "none"),
        (Roll((6, 6), 4), DONT, "none"),
    ],
)
def test_roll_kind_dice_como_se_pinta_cada_tirada_en_el_historial(
    roll: Roll, bet: Bet, kind: str
) -> None:
    assert roll_kind(roll, bet) == kind


def test_sin_tiradas_no_hay_cartel() -> None:
    assert banner_for(CrapsGame.new(100, PASS), Hand()) is None


def test_cartel_de_punto_dice_cual_es() -> None:
    game = game_after((1, 5))
    banner = banner_for(game, hand_of(game))
    assert banner is not None and banner["kind"] == "point"
    assert banner["title"] == "PUNTO: 6"
    assert "6" in banner["sub"] and "7" in banner["sub"]


def test_una_tirada_que_no_decide_nada_no_lleva_cartel() -> None:
    game = game_after((1, 5), (6, 5))
    assert banner_for(game, hand_of(game)) is None


def test_cartel_de_natural_con_pase_gana() -> None:
    game = game_after((3, 4))
    banner = banner_for(game, hand_of(game))
    assert banner is not None and banner["kind"] == "win" and banner["title"] == "¡NATURAL!"
    assert TOTAL_NAMES[7].upper() in banner["sub"]
    assert f"+{shown(game.net)}" in banner["sub"]


def test_cartel_de_natural_con_no_pase_pierde() -> None:
    game = game_after((5, 6), bet=DONT)
    banner = banner_for(game, hand_of(game))
    assert banner is not None and banner["kind"] == "lose" and banner["title"] == "¡NATURAL!"
    assert f"-{shown(game.wagered)}" in banner["sub"]


def test_cartel_de_pifia_segun_la_apuesta() -> None:
    lost = game_after((1, 1))
    banner = banner_for(lost, hand_of(lost))
    assert banner is not None and banner["kind"] == "lose" and banner["title"] == "¡PIFIA!"
    won = game_after((1, 1), bet=DONT)
    banner = banner_for(won, hand_of(won))
    assert banner is not None and banner["kind"] == "win"
    assert banner["title"] == "¡PIFIA DEL TIRADOR!"


def test_cartel_de_la_barra_con_no_pase() -> None:
    game = game_after((6, 6), bet=DONT)
    banner = banner_for(game, hand_of(game))
    assert banner is not None and banner["kind"] == "push" and banner["title"] == "¡BARRA!"


def test_el_12_con_pase_es_una_pifia_no_una_barra() -> None:
    game = game_after((6, 6))
    banner = banner_for(game, hand_of(game))
    assert banner is not None and banner["title"] == "¡PIFIA!"


def test_cartel_de_punto_hecho_con_pase_y_con_no_pase() -> None:
    won = game_after((1, 5), (2, 4))
    banner = banner_for(won, hand_of(won))
    assert banner is not None and banner["kind"] == "win" and banner["title"] == "¡PUNTO HECHO!"
    lost = game_after((1, 5), (2, 4), bet=DONT)
    banner = banner_for(lost, hand_of(lost))
    assert banner is not None and banner["kind"] == "lose" and banner["title"] == "¡PUNTO HECHO!"


def test_cartel_de_siete_fuera_gana_no_pase_y_pierde_pase() -> None:
    lost = game_after((1, 5), (3, 4))
    banner = banner_for(lost, hand_of(lost))
    assert banner is not None and banner["kind"] == "lose" and banner["title"] == "¡SIETE FUERA!"
    won = game_after((1, 5), (3, 4), bet=DONT)
    banner = banner_for(won, hand_of(won))
    assert banner is not None and banner["kind"] == "win" and banner["title"] == "¡SIETE FUERA!"


def test_cartel_de_mano_caliente_con_tres_puntos_en_la_mano() -> None:
    game = game_after((1, 5), (2, 4))
    cold = banner_for(game, Hand(points=[6, 6]))
    assert cold is not None and cold["kind"] == "win"
    hot = banner_for(game, Hand(points=[4, 5, 6]))
    assert hot is not None and hot["kind"] == "fire" and hot["title"] == "¡MANO CALIENTE!"


def test_el_cartel_de_las_odds_cuenta_la_ganancia_neta_con_ellas() -> None:
    game = game_after((2, 2), (2, 2), odds=200)
    banner = banner_for(game, hand_of(game))
    assert banner is not None
    assert f"+{shown(game.net)}" in banner["sub"] and game.net > game.stake


# -- Fichas y disco ---------------------------------------------------------------------


def test_chip_color_sube_con_la_cantidad_segun_la_tabla() -> None:
    assert chip_color(1) == CHIP_COLORS[0][1]
    for (limit, _), (_, color) in zip(CHIP_COLORS, CHIP_COLORS[1:], strict=False):
        assert chip_color(limit) == color
        assert chip_color(limit - 1) != color
    assert chip_color(10**12) == CHIP_COLORS[-1][1]


def test_chip_stack_es_mas_alto_cuantas_mas_cifras_y_con_tope() -> None:
    heights = [chip_stack(10**k, 0, 100)["n"] for k in range(1, 12)]
    assert heights == sorted(heights)
    assert chip_stack(1, 0, 100)["n"] == 1
    assert chip_stack(10**12, 0, 100)["n"] == 6


def test_chip_stack_escribe_la_cantidad_corta_y_la_opacidad() -> None:
    assert chip_stack(500, 0, 100)["t"] == "500"
    assert chip_stack(1_500, 0, 100)["t"] == "1,5k"
    assert chip_stack(2_000_000, 0, 100)["t"] == "2M"
    assert chip_stack(100, 0, 100)["a"] == 1.0
    assert chip_stack(100, 0, 100, alpha=0.55)["a"] == 0.55


def test_chip_stack_se_pinta_en_pantalla_donde_cae_el_punto_del_tapete() -> None:
    chip = chip_stack(100, -150.0, 17.0)
    sx, sy, _ = project(-150.0, 17.0, 0)
    assert chip["x"] == pytest.approx(sx, abs=0.01) and chip["y"] == pytest.approx(sy, abs=0.01)
    assert chip["r"] > 0


@pytest.mark.parametrize("point", POINTS)
def test_el_disco_va_on_sobre_la_casilla_del_punto(point: int) -> None:
    disc = puck(point)
    assert disc["on"] is True
    sx, sy, _ = project(BOX_X[point], sum(BOX_Y) / 2, 0)
    assert disc["x"] == pytest.approx(sx, abs=0.01) and disc["y"] == pytest.approx(sy, abs=0.01)


def test_el_disco_va_off_en_su_esquina_sin_punto() -> None:
    disc = puck(None)
    assert disc["on"] is False
    sx, sy, _ = project(*PUCK_OFF, 0)
    assert disc["x"] == pytest.approx(sx, abs=0.01) and disc["y"] == pytest.approx(sy, abs=0.01)


def test_las_casillas_de_los_puntos_van_ordenadas_de_izquierda_a_derecha() -> None:
    xs = [puck(p)["x"] for p in POINTS]
    assert xs == sorted(xs) and len(set(xs)) == len(POINTS)


# -- El panel ---------------------------------------------------------------------------


def test_panel_de_la_mesa_sin_partida() -> None:
    panel = panel_state(table_for(None, stake=1_500, bet=DONT))
    assert panel["status"] == "idle"
    assert panel["bet"] == "NO PASE" and panel["betKey"] == "nopase"
    assert panel["stake"] == shown(1_500)
    assert panel["odds"] is None and panel["pays"] is None and panel["point"] is None
    assert panel["history"] == []
    assert panel["hand"] == "0 tiradas · 0 puntos"


def test_panel_con_el_punto_puesto_ensena_punto_odds_y_lo_que_pagan() -> None:
    game = game_after((1, 5), odds=200)
    panel = panel_state(table_for(game))
    assert panel["status"] == "point" and panel["point"] == 6
    assert panel["odds"] == shown(200)
    assert panel["pays"] == format_odds(PASS, 6)
    assert panel["bet"] == "PASE" and panel["stake"] == shown(game.stake)
    assert panel["hand"] == "1 tirada · 0 puntos"


def test_panel_de_la_salida_decidida_sin_punto_no_ensena_cuotas() -> None:
    panel = panel_state(table_for(game_after((3, 4))))
    assert panel["status"] == "won" and panel["point"] is None and panel["pays"] is None


@pytest.mark.parametrize(
    ("rolls", "bet", "status"),
    [
        (((3, 4),), PASS, "won"),
        (((1, 1),), PASS, "lost"),
        (((6, 6),), DONT, "push"),
        (((1, 5), (3, 4)), PASS, "lost"),
        (((1, 5), (3, 4)), DONT, "won"),
    ],
)
def test_panel_de_una_partida_terminada_dice_como_acabo(
    rolls: tuple[tuple[int, int], ...], bet: Bet, status: str
) -> None:
    assert panel_state(table_for(game_after(*rolls, bet=bet)))["status"] == status


def test_panel_de_una_partida_ganada_con_punto_sigue_ensenando_el_punto_y_la_cuota() -> None:
    panel = panel_state(table_for(game_after((1, 5), (2, 4))))
    assert panel["status"] == "won"
    assert panel["point"] is None  # el disco ya no marca nada
    assert panel["pays"] == format_odds(PASS, 6)
    assert panel["hand"] == "2 tiradas · 1 punto"


def test_panel_guarda_las_ultimas_seis_tiradas() -> None:
    game = game_after((1, 5), *[(6, 5)] * 8)
    history = panel_state(table_for(game))["history"]
    assert len(history) == 6
    assert history[-1] == {"d": [6, 5], "t": 11, "k": "none"}


def test_panel_del_historial_trae_dados_total_y_clase() -> None:
    panel = panel_state(table_for(game_after((1, 5), (3, 4))))
    assert panel["history"] == [
        {"d": [1, 5], "t": 6, "k": "point"},
        {"d": [3, 4], "t": 7, "k": "lose"},
    ]


def test_panel_antes_de_la_tirada_no_adelanta_el_resultado() -> None:
    game = game_after((1, 5), (3, 4))
    table = table_for(game)
    before = panel_state(table, upto=1)
    assert before["status"] == "point" and before["point"] == 6
    assert before["hand"] == "1 tirada · 0 puntos"
    assert [r["t"] for r in before["history"]] == [6]  # sin el 7 que va a salir
    first = panel_state(table_for(game_after((3, 4))), upto=0)
    assert first["status"] == "comeout" and first["point"] is None
    assert first["history"] == [] and first["hand"] == "0 tiradas · 0 puntos"


def test_panel_antes_de_hacer_el_punto_aun_no_cuenta_ese_punto() -> None:
    game = game_after((1, 5), (2, 4))
    table = table_for(game)
    assert panel_state(table)["hand"] == "2 tiradas · 1 punto"
    assert panel_state(table, upto=1)["hand"] == "1 tirada · 0 puntos"


# -- board_state y throw_states ---------------------------------------------------------


def test_board_state_de_la_mesa_recien_abierta_ensena_la_pista_y_las_fichas_tenues() -> None:
    state = board_state(table_for(None, stake=500), OPENING_REST)
    assert state["hint"] is True and state["banner"] is None and state["total"] is None
    assert state["puck"]["on"] is False
    assert state["panel"]["status"] == "idle"
    assert len(state["chips"]) == 1 and state["chips"][0]["a"] < 1
    assert len(state["dice"]) == 2


def test_board_state_ordena_los_dados_del_fondo_al_frente() -> None:
    depths = [d["depth"] for d in board_state(table_for(None), OPENING_REST)["dice"]]
    assert depths == sorted(depths, reverse=True)


def test_board_state_con_el_punto_puesto_enciende_el_disco_y_no_ensena_la_pista() -> None:
    game = game_after((1, 5), odds=100)
    _, rests = simulate(game.last.dice, 1)  # type: ignore[union-attr]
    state = board_state(table_for(game), rests)
    assert state["puck"]["on"] is True
    assert state["puck"] == puck(6)
    assert state["hint"] is False
    assert state["total"] is not None and state["total"]["t"] == 6
    assert len(state["chips"]) == 2  # la apuesta y las odds


def test_board_state_al_decidirse_apaga_el_disco_y_vuelve_la_pista() -> None:
    game = game_after((1, 5), (3, 4))
    _, rests = simulate((3, 4), 1)
    state = board_state(table_for(game), rests)
    assert state["puck"]["on"] is False
    assert state["hint"] is True
    assert state["banner"] is not None and state["banner"]["title"] == "¡SIETE FUERA!"
    assert state["total"] is not None and state["total"]["t"] == 7


def test_el_total_de_la_mesa_quieta_es_la_suma_de_los_dados_y_su_clase() -> None:
    game = game_after((3, 4))
    _, rests = simulate((3, 4), 2)
    total = board_state(table_for(game), rests)["total"]
    assert total is not None and total["t"] == 7 and total["k"] == "win"


def test_meta_state_trae_las_filas_y_las_seis_casillas() -> None:
    meta = meta_state()
    assert len(meta["floor"]) == len(floor_rows()) and len(meta["wall"]) == len(wall_rows())
    assert set(meta["table"]["boxes"]) == {str(p) for p in POINTS}
    assert meta["table"]["boxes"] == {str(p): BOX_X[p] for p in POINTS}


def flight_of(states: list[dict]) -> list[dict]:
    """Los fotogramas con los dados rodando (antes del revelado)."""
    return states[:-REVEAL_FRAMES]


@pytest.mark.parametrize(
    "rolls",
    [((3, 4),), ((1, 5),), ((1, 5), (3, 4)), ((1, 5), (2, 4)), ((6, 6),)],
    ids=["natural", "punto", "siete-fuera", "punto-hecho", "doble-seis"],
)
def test_mientras_ruedan_los_dados_no_hay_cartel_ni_total(
    rolls: tuple[tuple[int, int], ...],
) -> None:
    game = game_after(*rolls, bet=DONT if rolls == ((6, 6),) else PASS)
    table = table_for(game)
    states, _ = throw_states(table, seed=3)
    before = panel_state(table, upto=len(game.rolls) - 1)
    flight = flight_of(states)
    assert len(flight) > 5
    for state in flight:
        assert state["banner"] is None and state["bannerA"] == 0.0
        assert state["total"] is None
        assert state["panel"] == before
        assert state["puck"] == puck(game.last.point)  # type: ignore[union-attr]
        assert state["hint"] is False and state["glow"] == []


def test_el_panel_de_antes_no_dice_el_resultado_de_la_tirada() -> None:
    game = game_after((1, 5), (3, 4))
    states, _ = throw_states(table_for(game), seed=3)
    assert states[0]["panel"]["status"] == "point"
    assert states[-1]["panel"]["status"] == "lost"
    assert states[0]["panel"]["history"][-1]["t"] == 6


def test_las_fichas_no_se_mueven_mientras_ruedan_los_dados() -> None:
    game = game_after((1, 5), (3, 4), odds=100)
    states, _ = throw_states(table_for(game), seed=3)
    flight = flight_of(states)
    assert all(state["chips"] == flight[0]["chips"] for state in flight)
    assert len(flight[0]["chips"]) == 2


def test_hay_impacto_contra_la_pared_en_algun_fotograma() -> None:
    states, _ = throw_states(table_for(game_after((3, 4))), seed=3)
    impacts = [s["impact"] for s in states if s["impact"]]
    assert impacts and all(0 < i["a"] <= 1 for i in impacts)


def test_el_final_de_la_tirada_enseña_el_cartel_y_el_total() -> None:
    game = game_after((1, 5))
    table = table_for(game)
    states, rests = throw_states(table, seed=5)
    last = states[-1]
    assert last["banner"] == banner_for(game, table.hand)
    assert last["banner"]["title"] == "PUNTO: 6"  # type: ignore[index]
    assert last["bannerA"] == 1.0
    assert last["total"]["t"] == 6 and last["total"]["a"] == 1.0
    assert last["panel"]["status"] == "point"
    assert states[len(states) - REVEAL_FRAMES]["bannerA"] > 0
    assert [r.value for r in rests] == [1, 5]


def test_el_cartel_aparece_poco_a_poco_en_el_revelado() -> None:
    states, _ = throw_states(table_for(game_after((3, 4))), seed=5)
    reveal = states[-REVEAL_FRAMES:]
    alphas = [s["bannerA"] for s in reveal]
    assert alphas == sorted(alphas) and alphas[0] < 1 and alphas[-1] == 1.0
    assert all(s["banner"] is not None for s in reveal)


def test_una_tirada_sin_cartel_tampoco_lo_ensena_al_final() -> None:
    game = game_after((1, 5), (6, 5))
    states, _ = throw_states(table_for(game), seed=5)
    assert states[-1]["banner"] is None and states[-1]["bannerA"] == 0.0
    assert states[-1]["total"]["t"] == 11  # type: ignore[index]


@pytest.mark.parametrize(
    ("rolls", "bet", "kind", "title"),
    [
        (((3, 4),), PASS, "win", "¡NATURAL!"),
        (((1, 5), (3, 4)), PASS, "lose", "¡SIETE FUERA!"),
        (((1, 5), (2, 4)), PASS, "win", "¡PUNTO HECHO!"),
        (((6, 6),), DONT, "push", "¡BARRA!"),
        (((1, 5),), PASS, "point", "PUNTO: 6"),
    ],
)
def test_al_final_de_la_tirada_el_cartel_corresponde(
    rolls: tuple[tuple[int, int], ...], bet: Bet, kind: str, title: str
) -> None:
    states, _ = throw_states(table_for(game_after(*rolls, bet=bet)), seed=2)
    banner = states[-1]["banner"]
    assert banner is not None and banner["kind"] == kind and banner["title"] == title


def test_el_disco_pasa_de_off_a_on_al_poner_el_punto() -> None:
    states, _ = throw_states(table_for(game_after((1, 5))), seed=2)
    assert states[0]["puck"]["on"] is False
    assert states[-1]["puck"] == puck(6) and states[-1]["puck"]["on"] is True
    middle = states[-REVEAL_FRAMES]["puck"]
    assert middle["x"] != states[0]["puck"]["x"]


def test_el_disco_pasa_de_on_a_off_al_decidirse() -> None:
    states, _ = throw_states(table_for(game_after((1, 5), (3, 4))), seed=2)
    assert states[0]["puck"] == puck(6)
    assert states[-1]["puck"] == puck(None) and states[-1]["puck"]["on"] is False


def test_el_disco_se_queda_on_si_la_tirada_no_decide() -> None:
    states, _ = throw_states(table_for(game_after((1, 5), (6, 5))), seed=2)
    assert all(s["puck"] == puck(6) for s in states)


def test_las_fichas_de_una_partida_perdida_se_van_y_las_de_una_ganada_llegan() -> None:
    lost, _ = throw_states(table_for(game_after((1, 1))), seed=2)
    assert lost[-1]["chips"][0]["a"] == 0.0 or not lost[-1]["chips"][0]["a"] > 0.05
    won, _ = throw_states(table_for(game_after((3, 4))), seed=2)
    assert len(won[-1]["chips"]) == 2  # la apuesta y el premio que llega de la banca
    assert won[-1]["chips"][1]["a"] == 1.0
    push, _ = throw_states(table_for(game_after((6, 6), bet=DONT)), seed=2)
    assert len(push[-1]["chips"]) == 1 and push[-1]["chips"][0]["a"] == 1.0


def test_throw_states_tiene_tantos_fotogramas_como_el_vuelo_mas_el_revelado() -> None:
    game = game_after((3, 4))
    states, _ = throw_states(table_for(game), seed=8)
    tracks, _ = simulate((3, 4), 8)
    assert len(states) == len(tracks[0].x) + REVEAL_FRAMES
    assert throw_seconds(states) == pytest.approx(FRAME_MS * (len(states) - 1) / 1000)


def test_la_misma_semilla_dibuja_la_misma_tirada() -> None:
    table = table_for(game_after((3, 4)))
    assert throw_states(table, seed=21) == throw_states(table, seed=21)
    assert throw_states(table, seed=21)[0] != throw_states(table, seed=22)[0]


def test_los_dados_del_ultimo_vuelo_aterrizan_donde_la_mesa_quieta() -> None:
    table = table_for(game_after((2, 5)))
    states, rests = throw_states(table, seed=6)
    landed = flight_of(states)[-1]["dice"]
    still = board_state(table, rests)["dice"]
    for a, b in zip(landed, still, strict=True):
        assert (a["x"], a["y"]) == pytest.approx((b["x"], b["y"]), abs=0.05)
        assert {f["v"] for f in a["faces"]} == {f["v"] for f in b["faces"]}


# -- Dibujo con Pillow ------------------------------------------------------------------


def png_size(data: bytes) -> tuple[str | None, tuple[int, int]]:
    image = Image.open(io.BytesIO(data))
    return image.format, image.size


def test_paint_da_una_imagen_de_640_por_360() -> None:
    image = paint(board_state(table_for(None), OPENING_REST))
    assert image.size == (W, H) and image.mode == "RGB"
    assert len(image.getcolors(1 << 20) or []) > 100


def test_paint_pinta_todos_los_carteles_sin_fallar() -> None:
    for rolls, bet in (
        (((1, 5),), PASS),
        (((3, 4),), PASS),
        (((5, 6),), DONT),
        (((6, 6),), DONT),
        (((1, 5), (3, 4)), PASS),
        (((1, 5), (2, 4)), DONT),
    ):
        game = game_after(*rolls, bet=bet, odds=100)
        _, rests = simulate(game.last.dice, 1)  # type: ignore[union-attr]
        assert paint(board_state(table_for(game), rests)).size == (W, H)


def test_el_dibujo_de_pillow_de_la_mesa_es_un_png_de_640_por_360() -> None:
    renderer = CrapsRenderer()
    assert png_size(renderer.board(table_for(None), OPENING_REST)) == ("PNG", (W, H))
    game = game_after((1, 5), odds=100)
    _, rests = simulate((1, 5), 1)
    assert png_size(renderer.board(table_for(game), rests)) == ("PNG", (W, H))


def test_el_png_de_la_mesa_con_el_punto_puesto_difiere_del_de_la_mesa_vacia() -> None:
    renderer = CrapsRenderer()
    _, rests = simulate((1, 5), 1)
    assert renderer.board(table_for(None), rests) != renderer.board(
        table_for(game_after((1, 5))), rests
    )


@pytest.mark.parametrize(
    "make_game",
    [
        lambda: game_after((1, 5)),
        lambda: game_after((1, 5), (3, 4), odds=100),
        lambda: game_after((6, 6), bet=DONT),
    ],
    ids=["punto", "siete-fuera-con-odds", "barra"],
)
def test_el_dibujo_de_pillow_de_la_tirada_es_un_gif_con_su_png_final(
    make_game: Callable[[], CrapsGame],
) -> None:
    table = table_for(make_game())
    media = CrapsRenderer().throw(table, seed=11)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H)
    states, rest = throw_states(table, seed=11)
    assert media.rest == rest
    assert 1 < gif.n_frames <= len(states)
    total = 0
    for index in range(gif.n_frames):
        gif.seek(index)
        total += gif.info["duration"]
    # Pillow funde los fotogramas idénticos seguidos, sumando su duración.
    assert total == FRAME_MS * (len(states) - 1) + FINAL_FRAME_MS
    assert media.seconds == pytest.approx(throw_seconds(states))
    assert png_size(media.png) == ("PNG", (W, H))


def test_encode_deja_el_ultimo_fotograma_quieto_y_el_png_es_ese_fotograma() -> None:
    frames = [paint(board_state(table_for(None, stake=s), OPENING_REST)) for s in (100, 5_000)]
    media = encode(frames, OPENING_REST)
    gif = Image.open(io.BytesIO(media.gif))
    gif.seek(gif.n_frames - 1)
    assert gif.info["duration"] == FINAL_FRAME_MS
    assert media.seconds == pytest.approx(FRAME_MS / 1000)
    assert media.rest == OPENING_REST
    assert Image.open(io.BytesIO(media.png)).convert("RGB").tobytes() == frames[-1].tobytes()
