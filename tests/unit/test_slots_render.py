"""Pruebas de bot.services.slots_render: GIF de la tirada, PNG final y turbo."""

from __future__ import annotations

import io
import itertools

import pytest
from PIL import Image, ImageChops, ImageSequence

from bot.services.slots import BIG_WIN, EPIC_WIN, MEGA_WIN, REEL_STRIPS, WinTier, spin_at
from bot.services.slots_render import (
    FLASH_FRAMES,
    FLASHES,
    FRAME_MS,
    HEIGHT,
    LIGHTS_H,
    LIGHTS_W,
    LIGHTS_X,
    LIGHTS_Y,
    OVERSHOOT,
    STOP_FRAMES,
    WIDTH,
    SlotsRenderer,
    reel_position,
    rollup_frames,
    rollup_values,
)

STAKE = 100


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


def frames_of(media) -> list[Image.Image]:  # noqa: ANN001
    gif = Image.open(io.BytesIO(media.gif))
    return [frame.convert("RGB") for frame in ImageSequence.Iterator(gif)]


def same_image(a: Image.Image, b: Image.Image) -> bool:
    return ImageChops.difference(a, b).getbbox() is None


def test_la_cuenta_arranca_suave_y_acaba_justo_en_lo_cobrado() -> None:
    values = rollup_values(12_345, 20)
    assert values[-1] == 12_345
    assert values == sorted(values)
    steps = [b - a for a, b in itertools.pairwise([0, *values])]
    # Acelera y frena: los saltos del medio son mayores que los de los extremos.
    assert steps[len(steps) // 2] > steps[0]
    assert steps[len(steps) // 2] > steps[-1]


def test_sin_premio_no_hay_cuenta_y_la_animacion_no_cambia(renderer: SlotsRenderer) -> None:
    spin = find(lambda s: s.pay_halves >= 4 and not s.anticipation and not s.is_jackpot)
    plain = renderer.render(spin)
    explicit = renderer.render(spin, won=0, stake=STAKE)

    expected = STOP_FRAMES[2] + 1 + FLASHES * FLASH_FRAMES * 2 + 1
    assert plain.gif == explicit.gif
    assert plain.png == explicit.png
    assert Image.open(io.BytesIO(plain.gif)).n_frames == expected
    assert plain.seconds == pytest.approx(FRAME_MS * (expected - 1) / 1000)


def test_la_cuenta_dura_mas_cuanto_mas_alto_es_el_nivel(renderer: SlotsRenderer) -> None:
    spin = find(lambda s: s.pay_halves >= 4 and not s.anticipation and not s.is_jackpot)
    base = renderer.render(spin).seconds
    small = renderer.render(spin, won=STAKE // 2, stake=STAKE).seconds
    big = renderer.render(spin, won=BIG_WIN * STAKE, stake=STAKE).seconds
    mega = renderer.render(spin, won=MEGA_WIN * STAKE, stake=STAKE).seconds
    epic = renderer.render(spin, won=EPIC_WIN * STAKE, stake=STAKE).seconds

    assert base < small < big < mega < epic
    assert small - base == pytest.approx(FRAME_MS * rollup_frames(None) / 1000)
    assert epic - base == pytest.approx(FRAME_MS * rollup_frames(WinTier.EPIC) / 1000)
    assert rollup_frames(None) * FRAME_MS == pytest.approx(600, abs=FRAME_MS)
    assert rollup_frames(WinTier.EPIC) * FRAME_MS == pytest.approx(2500, abs=FRAME_MS)


@pytest.mark.parametrize("multiple", [0, 1, BIG_WIN, MEGA_WIN, EPIC_WIN])
def test_con_y_sin_premio_el_ultimo_fotograma_es_el_png(
    renderer: SlotsRenderer, multiple: int
) -> None:
    media = renderer.render(find(lambda s: s.pay_halves >= 4), won=multiple * STAKE, stake=STAKE)
    final = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert final.size == (WIDTH, HEIGHT)
    assert same_image(frames_of(media)[-1], final)


def test_la_cuenta_pasa_por_importes_distintos_antes_del_final(renderer: SlotsRenderer) -> None:
    media = renderer.render(find(lambda s: s.pay_halves >= 4), won=MEGA_WIN * STAKE, stake=STAKE)
    tail = frames_of(media)[-rollup_frames(WinTier.MEGA) - 1 :]
    assert not same_image(tail[0], tail[-1])
    assert not same_image(tail[len(tail) // 2], tail[-1])


def test_el_turbo_con_premio_ensena_el_importe(renderer: SlotsRenderer) -> None:
    spin = find(lambda s: s.pay_halves >= 4)
    plain = renderer.render(spin, turbo=True)
    small = renderer.render(spin, turbo=True, won=STAKE, stake=STAKE)
    other = renderer.render(spin, turbo=True, won=STAKE * 2, stake=STAKE)
    epic = renderer.render(spin, turbo=True, won=EPIC_WIN * STAKE, stake=STAKE)

    assert small.gif == epic.gif == b""
    assert small.seconds == epic.seconds == 0
    assert len({plain.png, small.png, other.png, epic.png}) == 4


def test_cada_nivel_tiene_su_cartel(renderer: SlotsRenderer) -> None:
    """Mismo importe en el marcador; solo cambia el nivel (por la apuesta)."""
    spin = find(lambda s: s.pay_halves >= 4)
    won = EPIC_WIN * STAKE
    pngs = {
        renderer.render(spin, turbo=True, won=won, stake=stake).png
        for stake in (won, won // BIG_WIN, won // MEGA_WIN, won // EPIC_WIN)
    }
    assert len(pngs) == 4


def test_el_gif_epico_del_peor_caso_no_se_dispara(renderer: SlotsRenderer) -> None:
    """Jackpot con anticipación, cuenta ÉPICA, luces y un importe de siete cifras."""
    spin = find(lambda s: s.is_jackpot)
    assert spin.anticipation
    media = renderer.render(spin, won=1_234_567, stake=STAKE)
    assert len(media.gif) < 300_000
    assert len(media.png) < 30_000


# -- Luces de los laterales ----------------------------------------------------------


def lights(image: Image.Image) -> tuple[bytes, bytes]:
    """Las dos columnas de luces (izquierda y derecha) de un fotograma."""
    top, bottom = LIGHTS_Y, LIGHTS_Y + LIGHTS_H
    left = image.crop((LIGHTS_X, top, LIGHTS_X + LIGHTS_W, bottom))
    right_x = WIDTH - LIGHTS_X - LIGHTS_W
    right = image.crop((right_x, top, right_x + LIGHTS_W, bottom))
    return left.tobytes(), right.tobytes()


def test_las_luces_cambian_entre_fotogramas_mientras_gira(renderer: SlotsRenderer) -> None:
    spin = find(lambda s: not s.anticipation and not s.pay_halves and not s.is_jackpot)
    spinning = frames_of(renderer.render(spin))[: STOP_FRAMES[2]]
    looks = [lights(frame) for frame in spinning]

    assert all(left == right for left, right in looks)
    assert len(set(looks)) >= 4
    # La persecución avanza: dos fotogramas seguidos de aspecto distinto los hay
    # a lo largo de todo el giro, no solo al principio.
    changes = [k for k in range(1, len(looks)) if looks[k] != looks[k - 1]]
    assert changes[0] <= 2
    assert changes[-1] >= len(looks) - 3


@pytest.mark.parametrize("multiple", [0, EPIC_WIN])
def test_el_ultimo_fotograma_lleva_las_mismas_luces_que_el_png(
    renderer: SlotsRenderer, multiple: int
) -> None:
    spin = find(lambda s: s.pay_halves >= 4 and not s.is_jackpot)
    media = renderer.render(spin, won=multiple * STAKE, stake=STAKE)
    final = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert same_image(frames_of(media)[-1], final)

    still = Image.open(io.BytesIO(renderer.still_png(spin.stops))).convert("RGB")
    # Sin premio, luces tranquilas como en la imagen parada; con premio, otras.
    assert (lights(final) == lights(still)) is (multiple == 0)


def test_la_imagen_parada_lleva_las_luces_tranquilas(renderer: SlotsRenderer) -> None:
    still = Image.open(io.BytesIO(renderer.still_png((1, 2, 3)))).convert("RGB")
    left, right = lights(still)
    assert left == right
    # Encendidas y apagadas: la columna no es de un solo color.
    column = still.crop((LIGHTS_X, LIGHTS_Y, LIGHTS_X + LIGHTS_W, LIGHTS_Y + LIGHTS_H))
    assert len(column.getcolors(4096) or []) > 3


def test_con_anticipacion_las_luces_son_otras(renderer: SlotsRenderer) -> None:
    calm = find(lambda s: not s.anticipation and not s.pay_halves and not s.is_jackpot)
    tense = find(lambda s: s.anticipation and not s.pay_halves and not s.is_jackpot)
    calm_looks = {lights(frame) for frame in frames_of(renderer.render(calm))}
    tense_frames = frames_of(renderer.render(tense))
    # Mientras el tercer rodillo frena despacio, ninguna luz es de las normales.
    waiting = {lights(frame) for frame in tense_frames[STOP_FRAMES[1] : STOP_FRAMES[2]]}
    assert waiting
    assert not waiting & calm_looks
    # Y cambian en cada fotograma: van más rápido que la persecución normal.
    looks = [lights(frame) for frame in tense_frames[STOP_FRAMES[1] : STOP_FRAMES[2]]]
    assert all(a != b for a, b in itertools.pairwise(looks))


def test_las_luces_parpadean_mas_rapido_cuanto_mas_alto_es_el_nivel(
    renderer: SlotsRenderer,
) -> None:
    spin = find(lambda s: s.pay_halves >= 4 and not s.anticipation and not s.is_jackpot)

    def blink_rate(multiple: int, tier: str | None) -> float:
        media = renderer.render(spin, won=multiple * STAKE // 2, stake=STAKE)
        counting = frames_of(media)[-rollup_frames(tier) - 1 : -1]
        looks = [lights(frame) for frame in counting]
        return sum(a != b for a, b in itertools.pairwise(looks)) / len(looks)

    small = blink_rate(1, None)
    big = blink_rate(BIG_WIN * 2, WinTier.BIG)
    mega = blink_rate(MEGA_WIN * 2, WinTier.MEGA)
    epic = blink_rate(EPIC_WIN * 2, WinTier.EPIC)
    assert 0 < small < big < mega <= epic
