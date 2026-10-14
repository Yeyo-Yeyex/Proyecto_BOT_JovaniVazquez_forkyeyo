"""Pruebas de bot.services.enraya_render: fotogramas de cada jugada y dibujo de reserva con Pillow.

Python decide lo que se ve en cada fotograma (la caída con botes, el brillo y
el confeti, el cartel); aquí se comprueban esos estados sin dibujar nada, y el
dibujo de Pillow solo se mira por fuera (tamaño, que el GIF se juega una vez).
La escena de canvas tiene sus propias pruebas (`test_enraya_scene.py`).
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops

from bot.services.enraya import ROWS, Board, Disc
from bot.services.enraya_render import (
    BANNER_BOTTOM,
    BANNER_MIDDLE,
    BANNER_TOP,
    FINAL_FRAME_MS,
    GEOMETRY,
    HOVER_MS,
    THINK_MS,
    WIN_FRAMES,
    EnrayaRenderer,
    H,
    Media,
    Player,
    W,
    banner,
    banner_y,
    board_state,
    canvas_text,
    meta_state,
    move_steps,
)

HORIZONTAL = [0, 0, 1, 1, 2, 2, 3]
#: Las rojas ganan en vertical en las filas 2 a 5 de la columna 0 (arriba del todo).
VERTICAL_TOP = [0, 0, 0, 1, 0, 2, 0, 1, 0]
#: Las rojas ganan en vertical en las filas 0 a 3 de la columna 0 (abajo del todo).
VERTICAL_BOTTOM = [0, 1, 0, 1, 0, 1, 0]


def played(columns: list[int]) -> Board:
    board = Board()
    for column in columns:
        board = board.play(column)
    return board


def falling_heights(steps: list[tuple[dict, int]]) -> list[float]:
    return [state["falling"]["y"] for state, _ms in steps if state["falling"] is not None]


# -- Una ficha ---------------------------------------------------------------------------


def test_una_ficha_empieza_flotando_sobre_el_tablero() -> None:
    before = played([3])
    move = before.play(3).last
    assert move is not None
    steps, _final = move_steps(before, [move])
    first, ms = steps[0]
    assert ms == HOVER_MS
    assert first["falling"] == {"col": 3, "y": float(ROWS), "disc": move.disc.value}
    assert first["grid"][3][1] == 0  # la ficha aún no está en el tablero
    assert first["turn"] == move.disc.value


def test_la_ficha_cae_con_gravedad_y_acaba_en_su_fila() -> None:
    before = played([3, 3])
    move = before.play(3).last
    assert move is not None and move.row == 2
    steps, _final = move_steps(before, [move])
    heights = falling_heights(steps)
    assert heights[0] == ROWS and heights[-1] == move.row
    # Cae más deprisa cada fotograma hasta el primer golpe (el último trozo es solo el resto).
    first_hit = heights.index(float(move.row))
    fall = heights[: first_hit + 1]
    drops = [a - b for a, b in zip(fall, fall[1:], strict=False)][:-1]
    assert drops == sorted(drops) and all(d > 0 for d in drops)
    # Y bota dos veces: vuelve a subir y a tocar suelo.
    after_hit = heights[first_hit:]
    assert max(after_hit) > move.row
    assert after_hit.count(float(move.row)) == 3
    assert all(h >= move.row for h in heights)
    assert all(h <= ROWS for h in heights)


def test_el_estado_final_de_una_ficha_tiene_la_jugada_en_el_tablero() -> None:
    before = played([3])
    move = before.play(4).last
    assert move is not None
    _steps, final = move_steps(before, [move])
    assert final["grid"][4][0] == move.disc.value
    assert final["last"] == [move.column, move.row]
    assert final["falling"] is None
    assert final["turn"] == move.disc.other.value
    assert final["win"] is None and final["confetti"] is None and final["banner"] is None


def test_los_fotogramas_de_la_caida_no_traen_la_ficha_ya_puesta() -> None:
    before = Board()
    move = before.play(2).last
    assert move is not None
    steps, _final = move_steps(before, [move])
    for state, _ms in steps:
        assert state["grid"][2][0] == 0
        assert state["last"] is None


# -- Dos fichas (la tuya y la de Jovani) -------------------------------------------------


def test_dos_fichas_llevan_una_pausa_con_el_tablero_quieto_entre_medias() -> None:
    before = Board()
    mine = before.play(3)
    both = mine.play(4)
    steps, final = move_steps(before, [mine.last, both.last])
    pauses = [(state, ms) for state, ms in steps if ms == THINK_MS]
    assert len(pauses) == 1
    pause, _ms = pauses[0]
    assert pause["falling"] is None
    assert pause["grid"][3][0] == Disc.RED.value and pause["grid"][4][0] == 0
    assert pause["turn"] == Disc.YELLOW.value
    # La segunda ficha vuelve a empezar flotando y acaba en su fila.
    heights = falling_heights(steps)
    assert heights.count(float(ROWS)) == 2
    assert final["grid"][3][0] == Disc.RED.value and final["grid"][4][0] == Disc.YELLOW.value
    assert final["last"] == [4, 0]
    assert final["turn"] == Disc.RED.value


def test_dos_fichas_apilan_la_segunda_encima_de_la_primera() -> None:
    before = Board()
    mine = before.play(3)
    both = mine.play(3)
    _steps, final = move_steps(before, [mine.last, both.last])
    assert final["grid"][3][:2] == [Disc.RED.value, Disc.YELLOW.value]
    assert final["last"] == [3, 1]


# -- Final con raya ----------------------------------------------------------------------


def winning_steps():
    before = played(HORIZONTAL[:-1])
    after = before.play(3)
    end = banner("¡GANAS!", "Le ganas a Jovani", kind="win")
    steps, final = move_steps(before, [after.last], end=end)
    return after, steps, final


def test_al_ganar_siguen_el_brillo_y_el_confeti() -> None:
    after, steps, final = winning_steps()
    shining = [state for state, _ms in steps if state["win"] is not None]
    assert len(shining) == WIN_FRAMES
    assert all(state["confetti"] is not None for state in shining)
    assert {tuple(cell) for cell in shining[0]["win"]["cells"]} == set(after.winning_cells())
    assert shining[0]["win"]["disc"] == Disc.RED.value
    progress = [state["win"]["t"] for state in shining]
    assert progress == sorted(progress) and progress[-1] == 1.0
    # Antes de ganar, la caída no brilla.
    assert steps[0][0]["win"] is None and steps[0][0]["confetti"] is None


def test_el_cartel_del_final_aparece_poco_a_poco_hasta_ser_opaco() -> None:
    _after, steps, final = winning_steps()
    shown = [state["banner"] for state, _ms in steps if state["banner"] is not None]
    alphas = [b["a"] for b in shown]
    assert alphas == sorted(alphas)
    assert 0 < alphas[0] < 1 and alphas[-1] == 1.0
    assert {b["title"] for b in shown} == {"¡GANAS!"}
    # El primer fotograma del brillo aún no enseña el cartel.
    first_shine = next(state for state, _ms in steps if state["win"] is not None)
    assert first_shine["banner"] is None
    assert final["banner"]["a"] == 1.0 and final["win"]["t"] == 1.0
    assert final["turn"] == 0


def test_el_final_sin_raya_solo_trae_el_cartel() -> None:
    before = played([0, 1])
    move = before.play(2).last
    assert move is not None
    end = banner("TABLAS", "Tablero lleno", kind="draw")
    steps, final = move_steps(before, [move], end=end)
    banners = [state["banner"] for state, _ms in steps if state["banner"] is not None]
    assert [b["a"] for b in banners] == sorted(b["a"] for b in banners)
    assert banners[-1]["a"] == 1.0
    assert all(state["win"] is None and state["confetti"] is None for state, _ms in steps)
    assert final["banner"]["kind"] == "draw" and final["turn"] == 0


def test_un_final_que_no_es_un_cartel_conocido_se_rechaza() -> None:
    with pytest.raises(AssertionError):
        banner("¿?", kind="otro")


# -- Dónde va el cartel ------------------------------------------------------------------


def test_sin_raya_el_cartel_va_en_medio() -> None:
    assert banner_y(played([0, 1, 2])) == BANNER_MIDDLE


def test_con_la_raya_abajo_el_cartel_sube() -> None:
    board = played(VERTICAL_BOTTOM)
    assert board.winner is not None
    assert banner_y(board) == BANNER_TOP


def test_con_la_raya_arriba_el_cartel_baja() -> None:
    board = played(VERTICAL_TOP)
    assert board.winner is Disc.RED
    assert min(row for _col, row in board.winning_cells()) >= 2
    assert banner_y(board) == BANNER_BOTTOM


def test_el_cartel_de_cada_fotograma_lleva_su_altura() -> None:
    state = board_state(played(VERTICAL_TOP), end=banner("X", kind="win"))
    assert state["banner"]["y"] == BANNER_BOTTOM
    _after, _steps, final = winning_steps()
    assert final["banner"]["y"] == banner_y(played(HORIZONTAL))


# -- Textos ------------------------------------------------------------------------------


def test_canvas_text_quita_los_emojis_y_deja_los_acentos() -> None:
    assert canvas_text("Peña 🔴 Ñandú ✨ ¡Qué día!") == "Peña Ñandú ¡Qué día!"
    assert canvas_text("José 😀🎉") == "José"
    assert canvas_text("ñ á é ü") == "ñ á é ü"


def test_canvas_text_junta_las_vocales_con_acento_suelto() -> None:
    assert canvas_text("café") == "café"


def test_canvas_text_nunca_devuelve_un_nombre_vacio() -> None:
    assert canvas_text("🔴🟡") == "?"
    assert canvas_text("   ") == "?"
    assert canvas_text("") == "?"


def test_canvas_text_junta_los_espacios() -> None:
    assert canvas_text("  Diego \n  Torres ") == "Diego Torres"


def test_meta_state_limpia_los_nombres_y_lleva_la_geometria() -> None:
    meta = meta_state(
        Player("Diego 🔴", "Debuta hoy ✨"),
        Player("Jovani"),
        title="4 EN RAYA",
        subtitle="Contra Jovani",
        seed=42,
    )
    assert meta["red"] == {"name": "Diego", "tag": "Debuta hoy"}
    assert meta["yellow"] == {"name": "Jovani", "tag": "?"}
    assert (meta["title"], meta["subtitle"], meta["seed"]) == ("4 EN RAYA", "Contra Jovani", 42)
    assert meta["geom"] == GEOMETRY and meta["geom"] is not GEOMETRY


# -- Dibujo de reserva con Pillow ----------------------------------------------------------

META = meta_state(
    Player("Diego", "Debuta hoy"),
    Player("Jovani", "Jovani de domingo"),
    title="4 EN RAYA",
    subtitle="Contra Jovani de domingo",
    seed=3,
)


def png_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    assert image.format == "PNG"
    return image


def test_el_gif_de_pillow_se_juega_una_vez_y_mide_720_por_480() -> None:
    before = played(HORIZONTAL[:-1])
    after = before.play(3)
    steps, final = move_steps(before, [after.last], end=banner("¡GANAS!", "Bien", kind="win"))
    media = EnrayaRenderer().animate(META, steps, final)
    assert isinstance(media, Media)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H) == (720, 480)
    assert "loop" not in gif.info
    assert gif.n_frames > 1
    gif.seek(gif.n_frames - 1)
    assert gif.info["duration"] >= FINAL_FRAME_MS
    assert media.seconds == pytest.approx(sum(ms for _state, ms in steps) / 1000)
    assert media.seconds > 0


def test_el_png_del_final_es_el_ultimo_fotograma() -> None:
    before = played(HORIZONTAL[:-1])
    after = before.play(3)
    steps, final = move_steps(before, [after.last], end=banner("¡GANAS!", "Bien", kind="win"))
    renderer = EnrayaRenderer()
    media = renderer.animate(META, steps, final)
    png = png_image(media.png)
    assert png.size == (W, H)
    assert ImageChops.difference(png.convert("RGB"), renderer.frame(META, final)).getbbox() is None


def test_still_devuelve_un_png_del_tablero_quieto() -> None:
    data = EnrayaRenderer().still(META, board_state(Board(), turn=Disc.RED))
    assert png_image(data).size == (W, H)


def test_el_dibujo_cambia_con_la_ficha_y_con_el_cartel() -> None:
    renderer = EnrayaRenderer()
    empty = renderer.frame(META, board_state(Board(), turn=Disc.RED))
    with_disc = renderer.frame(META, board_state(played([3]), turn=Disc.YELLOW))
    assert ImageChops.difference(empty, with_disc).getbbox() is not None
    plain = renderer.frame(META, board_state(played(HORIZONTAL)))
    banner_state = board_state(played(HORIZONTAL), end={**banner("¡GANAS!", "Bien"), "a": 1.0})
    with_banner = renderer.frame(META, banner_state)
    assert ImageChops.difference(plain, with_banner).getbbox() is not None
    # Un cartel casi transparente no se pinta en la reserva.
    faint = board_state(played(HORIZONTAL), end={**banner("¡GANAS!", "Bien"), "a": 0.1})
    assert ImageChops.difference(plain, renderer.frame(META, faint)).getbbox() is None
