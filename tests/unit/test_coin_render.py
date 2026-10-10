"""Pruebas de bot.services.coin_render: el vuelo de la moneda y lo que se ve en cada fotograma.

Casi todo es matemática pura (poses, geometría, estados), sin dibujar. Solo las
pruebas de `CoinRenderer` y de `assemble` generan imágenes de verdad.
"""

from __future__ import annotations

import base64
import io
import math
from collections.abc import Callable

import pytest
from coin_fakes import game_after
from PIL import Image

from bot.services.coin import MAX_FLIPS, CoinGame, Outcome, Side, Status
from bot.services.coin_render import (
    ELEVATION,
    FINAL_FRAME_MS,
    FLIGHT_FRAMES,
    FRAME_MS,
    RADIUS,
    REST_FRAMES,
    REVEAL_FRAMES,
    TABLE_Y,
    THICKNESS,
    CoinRenderer,
    H,
    W,
    banner_for,
    board_state,
    coin_geometry,
    flight_frames,
    meta_state,
    rest_spin,
    toss_poses,
    toss_seconds,
    toss_states,
)
from bot.services.coin_scene import assemble

CARA, CRUZ, EDGE = Outcome.CARA, Outcome.CRUZ, Outcome.EDGE
TWO_PI = 2 * math.pi


def lost_game() -> CoinGame:
    """Un acierto y luego fallo: pidió cara, salió cruz."""
    return game_after([Side.CARA, Side.CARA], [CARA, CRUZ])


def cashed_game() -> CoinGame:
    game = game_after([Side.CARA, Side.CRUZ], [CARA, CRUZ], stake=100)
    game.cash_out()
    return game


def edge_game() -> CoinGame:
    return game_after([Side.CARA], [EDGE])


def maxed_cashed_game() -> CoinGame:
    game = game_after([Side.CARA] * MAX_FLIPS, [CARA] * MAX_FLIPS)
    game.cash_out()
    return game


def angle_of(spin: float) -> float:
    return spin % TWO_PI


def near(angle: float, target: float) -> bool:
    """Si `angle` es `target` módulo una vuelta."""
    return math.isclose(math.remainder(angle - target, TWO_PI), 0.0, abs_tol=1e-6)


# -- toss_poses -------------------------------------------------------------------------


@pytest.mark.parametrize("start", list(Side))
@pytest.mark.parametrize("seed", range(8))
def test_el_lanzamiento_acaba_en_reposo_con_la_cara_que_salio(start: Side, seed: int) -> None:
    for outcome, expected in ((CARA, 0.0), (CRUZ, math.pi)):
        last = toss_poses(start, outcome, tension=0, seed=seed)[-1]
        assert last.lift == 0.0
        assert near(last.spin, expected)


@pytest.mark.parametrize("seed", range(12))
def test_el_canto_acaba_de_pie_a_mas_o_menos_medio_pi(seed: int) -> None:
    last = toss_poses(Side.CARA, EDGE, tension=0, seed=seed)[-1]
    assert last.lift == 0.0
    assert near(last.spin, math.pi / 2) or near(last.spin, -math.pi / 2)


def test_el_canto_cae_de_pie_mirando_con_las_dos_caras_segun_la_semilla() -> None:
    standing = set()
    for seed in range(40):
        last = toss_poses(Side.CARA, EDGE, tension=0, seed=seed)[-1]
        standing.add("cara" if near(last.spin, math.pi / 2) else "cruz")
    assert standing == {"cara", "cruz"}


def test_empieza_en_reposo_con_la_cara_de_partida() -> None:
    poses = toss_poses(Side.CRUZ, CARA, tension=0, seed=1)
    for pose in poses[:REST_FRAMES]:
        assert pose.lift == 0.0 and pose.spin == pytest.approx(math.pi)


def test_con_mas_aciertos_el_vuelo_es_mas_largo() -> None:
    lengths = [len(toss_poses(Side.CARA, CARA, tension=t, seed=3)) for t in (0, 3, 6, 9)]
    assert lengths == sorted(lengths) and len(set(lengths)) == len(lengths)
    assert flight_frames(0) == FLIGHT_FRAMES
    assert flight_frames(5) > flight_frames(0)
    # Pasados los nueve aciertos ya no se alarga más.
    assert flight_frames(MAX_FLIPS) == flight_frames(MAX_FLIPS - 1)


def test_el_mismo_lanzamiento_con_la_misma_semilla_se_dibuja_igual() -> None:
    a = toss_poses(Side.CARA, CRUZ, tension=2, seed=99)
    assert a == toss_poses(Side.CARA, CRUZ, tension=2, seed=99)
    assert a != toss_poses(Side.CARA, CRUZ, tension=2, seed=100)


def test_el_canto_tarda_mas_en_asentarse_que_una_cara() -> None:
    edge = toss_poses(Side.CARA, EDGE, tension=0, seed=1)
    cara = toss_poses(Side.CARA, CARA, tension=0, seed=1)
    assert len(edge) > len(cara)


def test_la_moneda_vuela_y_vuelve_a_la_mesa() -> None:
    poses = toss_poses(Side.CARA, CARA, tension=0, seed=1)
    assert max(p.lift for p in poses) > 100
    assert all(p.lift >= 0 for p in poses)


def test_rest_spin_de_cada_cara() -> None:
    assert rest_spin(Side.CARA) == rest_spin(CARA) == 0.0
    assert rest_spin(Side.CRUZ) == rest_spin(CRUZ) == math.pi
    assert rest_spin(EDGE) == pytest.approx(math.pi / 2)


# -- coin_geometry ----------------------------------------------------------------------


def test_tumbada_con_cada_cara_arriba_se_ve_esa_cara_achatada() -> None:
    cara = coin_geometry(0.0, rest_spin(Side.CARA))
    cruz = coin_geometry(0.0, rest_spin(Side.CRUZ))
    assert cara["face"] == "cara" and cruz["face"] == "cruz"
    for geo in (cara, cruz):
        assert geo["rx"] == pytest.approx(RADIUS)
        assert geo["ry"] == pytest.approx(RADIUS * math.sin(ELEVATION))
        assert geo["ry"] < geo["rx"]


def test_de_pie_se_ve_casi_redonda_y_con_la_cara_del_lado_que_mira() -> None:
    mira_cara = coin_geometry(0.0, math.pi / 2)
    mira_cruz = coin_geometry(0.0, -math.pi / 2)
    assert mira_cara["face"] == "cara"
    assert mira_cruz["face"] == "cruz"
    for geo in (mira_cara, mira_cruz):
        assert geo["ry"] == pytest.approx(RADIUS * math.cos(ELEVATION))
        assert geo["ry"] > 0.8 * geo["rx"]


@pytest.mark.parametrize("spin", [math.pi / 2, -math.pi / 2, 3 * math.pi / 2])
def test_de_pie_el_borde_inferior_toca_el_tapete(spin: float) -> None:
    geo = coin_geometry(0.0, spin)
    # La línea media del canto (entre las dos caras) apoya justo en el tapete...
    middle = (geo["y"] + geo["back"]) / 2
    assert middle + geo["ry"] == pytest.approx(TABLE_Y, abs=1e-6)
    # ...y ninguna de las dos caras se hunde en él más que medio grosor.
    for y in (geo["y"], geo["back"]):
        assert abs(y + geo["ry"] - TABLE_Y) <= THICKNESS / 2


def test_en_el_aire_la_moneda_crece_y_su_sombra_se_aclara() -> None:
    ground = coin_geometry(0.0, 0.0)
    high = coin_geometry(150.0, 0.0)
    assert high["rx"] > ground["rx"]
    assert high["y"] < ground["y"]
    assert high["shadow"]["a"] < ground["shadow"]["a"]
    assert high["shadow"]["rx"] > ground["shadow"]["rx"]


def test_la_cara_que_se_ve_cambia_al_girar_media_vuelta() -> None:
    faces = {coin_geometry(0.0, math.pi * k / 10)["face"] for k in range(21)}
    assert faces == {"cara", "cruz"}


# -- toss_states y board_state ----------------------------------------------------------


def test_el_vuelo_no_revela_el_resultado_antes_de_parar() -> None:
    game = lost_game()
    states = toss_states(game, start=Side.CARA, seed=5)
    flight = states[:-REVEAL_FRAMES]
    assert flight
    for state in flight:
        assert state["ladder"] == {"level": 1, "state": "flying"}
        assert state["banner"] is None and state["bannerA"] == 0.0
        assert state["history"] == [{"o": "cara", "won": True}]  # sin el último lanzamiento
        assert state["pick"] == "cara"
        assert state["hint"] is False


def test_el_primer_lanzamiento_empieza_con_la_escalera_a_cero_y_sin_historial() -> None:
    game = game_after([Side.CRUZ], [CARA])
    states = toss_states(game, start=Side.CARA, seed=1)
    assert states[0]["ladder"] == {"level": 0, "state": "flying"}
    assert states[0]["history"] == []


def test_el_ultimo_estado_es_la_mesa_quieta_con_el_cartel() -> None:
    for game in (lost_game(), edge_game(), game_after([Side.CARA], [CARA])):
        states = toss_states(game, start=Side.CRUZ, seed=7)
        assert game.last is not None
        board = board_state(game, face=game.last.outcome)
        assert states[-1] == dict(board, bannerA=1.0, pick=game.last.pick.key)
        assert states[-1]["banner"] == banner_for(game)
        # Los fotogramas del revelado enseñan ya el final, con el cartel apareciendo.
        reveal = states[-REVEAL_FRAMES:]
        assert all(s["history"] == board["history"] for s in reveal)
        assert [s["bannerA"] for s in reveal] == sorted(s["bannerA"] for s in reveal)
        assert reveal[0]["bannerA"] < 1.0


def test_la_moneda_del_ultimo_vuelo_aterriza_donde_la_mesa_quieta() -> None:
    game = lost_game()
    states = toss_states(game, start=Side.CARA, seed=7)
    landed = states[-REVEAL_FRAMES - 1]["coin"]
    rest = states[-1]["coin"]
    for key in ("x", "y", "back", "rx", "ry"):
        assert landed[key] == pytest.approx(rest[key], abs=1e-6)
    assert landed["face"] == rest["face"] == "cruz"


def test_toss_seconds_cuenta_los_fotogramas_menos_el_ultimo() -> None:
    states = toss_states(lost_game(), start=Side.CARA, seed=1)
    assert toss_seconds(states) == pytest.approx(0.04 * (len(states) - 1))


def test_board_state_de_la_mesa_recien_abierta_ensena_la_pista() -> None:
    state = board_state(None, face=Side.CARA)
    assert state["hint"] is True and state["banner"] is None
    assert state["ladder"] == {"level": 0, "state": "play"}
    assert state["coin"]["face"] == "cara"


def test_board_state_de_una_racha_a_medias_ensena_el_nivel_y_el_historial() -> None:
    game = game_after([Side.CARA, Side.CRUZ], [CARA, CRUZ])
    state = board_state(game, face=CRUZ)
    assert state["ladder"] == {"level": 2, "state": "play"}
    assert [h["o"] for h in state["history"]] == ["cara", "cruz"]
    assert state["hint"] is False
    assert state["coin"]["face"] == "cruz"


@pytest.mark.parametrize(
    ("game", "state"),
    [(lost_game(), "lost"), (edge_game(), "lost"), (cashed_game(), "cash")],
)
def test_board_state_marca_la_escalera_segun_como_acabo(game: CoinGame, state: str) -> None:
    assert game.last is not None
    assert board_state(game, face=game.last.outcome)["ladder"]["state"] == state


def test_meta_state_trae_la_escalera_de_diez_peldanos() -> None:
    meta = meta_state(1_000)
    assert meta["stake"] == "1.000 Y$"
    assert len(meta["ladder"]) == MAX_FLIPS
    assert meta["ladder"][0] == {"m": "×2", "v": "2.000 Y$"}
    assert meta["ladder"][-1] == {"m": "×1.024", "v": "1.024.000 Y$"}


# -- banner_for -------------------------------------------------------------------------


def test_sin_lanzamientos_no_hay_cartel() -> None:
    assert banner_for(game_after([], [CARA])) is None


def test_cartel_de_acierto_con_la_partida_en_marcha() -> None:
    game = game_after([Side.CARA, Side.CRUZ], [CARA, CRUZ])
    assert game.status is Status.PLAYING
    assert banner_for(game) == {"kind": "win", "title": "¡CRUZ!", "sub": "×4 · 400 Y$"}


def test_cartel_de_fallo_dice_lo_que_se_pidio_y_lo_que_se_iba_a_llevar() -> None:
    assert banner_for(lost_game()) == {
        "kind": "lose",
        "title": "¡CRUZ!",
        "sub": "Pediste cara · adiós a 200 Y$",
    }


def test_cartel_de_canto() -> None:
    assert banner_for(edge_game()) == {
        "kind": "edge",
        "title": "¡DE CANTO!",
        "sub": "Perro Sanxe se la queda",
    }


def test_cartel_de_cobro() -> None:
    assert banner_for(cashed_game()) == {
        "kind": "cash",
        "title": "¡COBRADO!",
        "sub": "+300 Y$ en ×4",
    }


def test_cartel_de_la_moneda_de_oro() -> None:
    assert banner_for(maxed_cashed_game()) == {
        "kind": "gold",
        "title": "¡MONEDA DE ORO!",
        "sub": "Diez seguidas · +102.300 Y$",
    }


# -- CoinRenderer (Pillow) y assemble ---------------------------------------------------


def png_size(data: bytes) -> tuple[str | None, tuple[int, int]]:
    image = Image.open(io.BytesIO(data))
    return image.format, image.size


def test_el_dibujo_de_pillow_de_la_mesa_es_un_png_de_640_por_360() -> None:
    renderer = CoinRenderer()
    assert png_size(renderer.board(None, stake=100, face=Side.CARA)) == ("PNG", (W, H))
    assert png_size(renderer.board(lost_game(), stake=100, face=CRUZ)) == ("PNG", (W, H))
    assert png_size(renderer.board(edge_game(), stake=100, face=EDGE)) == ("PNG", (W, H))


@pytest.mark.parametrize("make_game", [lost_game, edge_game])
def test_el_dibujo_de_pillow_del_lanzamiento_es_un_gif_con_su_png_final(
    make_game: Callable[[], CoinGame],
) -> None:
    game = make_game()
    media = CoinRenderer().toss(game, start=Side.CARA, seed=11)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H)
    states = toss_states(game, start=Side.CARA, seed=11)
    # Pillow funde los fotogramas idénticos seguidos (el reposo), sumando su duración.
    assert len(states) // 2 < gif.n_frames <= len(states)
    total = 0
    for index in range(gif.n_frames):
        gif.seek(index)
        total += gif.info["duration"]
    assert total == FRAME_MS * (len(states) - 1) + FINAL_FRAME_MS
    assert media.seconds == pytest.approx(toss_seconds(states))
    assert png_size(media.png) == ("PNG", (W, H))


def data_url(image: Image.Image) -> str:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_assemble_pega_cada_recuadro_sobre_el_fotograma_anterior() -> None:
    red = Image.new("RGB", (W, H), (255, 0, 0))
    blue = Image.new("RGB", (10, 6), (0, 0, 255))
    green = Image.new("RGB", (4, 4), (0, 255, 0))
    frames = assemble(
        [
            {"u": data_url(red)},
            {"u": data_url(blue), "x": 5, "y": 7},
            {"u": data_url(green), "x": 100, "y": 200},
        ]
    )
    assert len(frames) == 3 and all(f.size == (W, H) for f in frames)
    assert frames[0].getpixel((5, 7)) == (255, 0, 0)  # el primero no se toca
    assert frames[1].getpixel((5, 7)) == (0, 0, 255)
    assert frames[1].getpixel((14, 12)) == (0, 0, 255)
    assert frames[1].getpixel((15, 13)) == (255, 0, 0)
    # El tercero conserva el azul del segundo y añade su recuadro verde.
    assert frames[2].getpixel((5, 7)) == (0, 0, 255)
    assert frames[2].getpixel((101, 201)) == (0, 255, 0)
    assert frames[1].getpixel((101, 201)) == (255, 0, 0)


def test_assemble_acepta_un_fotograma_entero_en_mitad() -> None:
    red = Image.new("RGB", (W, H), (255, 0, 0))
    white = Image.new("RGB", (W, H), (255, 255, 255))
    frames = assemble([{"u": data_url(red)}, {"u": data_url(white)}])
    assert frames[1].getpixel((0, 0)) == (255, 255, 255)


def test_assemble_falla_si_el_primer_fotograma_no_viene_entero() -> None:
    patch = Image.new("RGB", (10, 10), (0, 0, 255))
    with pytest.raises(ValueError, match="primer fotograma"):
        assemble([{"u": data_url(patch), "x": 0, "y": 0}])
