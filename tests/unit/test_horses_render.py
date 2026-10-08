"""Pruebas de bot.services.horses_render: la parrilla, la carrera en GIF y el boleto."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from bot.services import horses as h
from bot.services.horses import SEGMENTS, STABLE_BY_KEY, BetKind, Going, Pick, RaceCard, RaceResult
from bot.services.horses_render import GATE_FRAMES, H, HorseRenderer, W

SIX = ("falcon", "manual", "paguita", "gofio", "fango", "uco")


def card_of(**kw) -> RaceCard:  # noqa: ANN003
    return RaceCard(
        horses=tuple(STABLE_BY_KEY[k] for k in SIX), going=Going.SECO, distance=1_200, **kw
    )


def photo_result() -> RaceResult:
    """El 3 gana al 0 por centésimas: foto-finish; el 1 tropieza y llueve."""
    order = (3, 0, 1, 2, 4, 5)
    gaps = (0.0, 0.01, 1.0, 1.5, 3.0, 4.0)
    times = [0.0] * 6
    for place, index in enumerate(order):
        times[index] = 75.0 + gaps[place]
    splits = tuple(tuple(t * s / SEGMENTS for s in range(SEGMENTS + 1)) for t in times)
    return RaceResult(
        order=order, times=tuple(times), splits=splits, stumbles={1: 3}, bolted={5: 4}, rained=True
    )


def test_la_parrilla_es_un_png_con_una_fila_por_caballo() -> None:
    card = card_of()
    odds = h.estimate(card, np.random.default_rng(1), trials=2_000)
    png = HorseRenderer().card(card, odds, {}, tip=h.Tip(horse=2, confidence=97), pot=5_000)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.width == W
    assert image.height > 6 * 40


def test_la_carrera_es_un_gif_que_acaba_en_el_podio() -> None:
    card = card_of(rain_chance=0.5)
    media = HorseRenderer().race(card, photo_result())
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF"
    assert gif.size == (W, H)
    assert gif.n_frames > GATE_FRAMES
    assert media.seconds > 5
    # Un GIF que Discord sirve sin problemas.
    assert len(media.gif) < 8 * 1024 * 1024
    final = Image.open(io.BytesIO(media.png))
    assert final.size == (W, H)


def test_una_carrera_real_se_dibuja_entera() -> None:
    rng = np.random.default_rng(4)
    card = h.new_card(rng, {}, now=0, grand_prix=True)
    media = HorseRenderer().race(card, h.run_race(card, rng))
    assert Image.open(io.BytesIO(media.gif)).n_frames > GATE_FRAMES


def test_el_boleto_lleva_premiado_solo_si_cobra() -> None:
    renderer = HorseRenderer()
    pick = Pick(BetKind.TRIFECTA, (0, 1, 2))
    kwargs = {
        "race": "Premio Puerta del Sol",
        "player": "Diego",
        "pick": pick,
        "names": ["Falcon Presidencial", "Manual de Resistencia", "La Paguita"],
        "stake": 500,
        "odds": 12_345,
    }
    plain = Image.open(io.BytesIO(renderer.ticket(**kwargs))).convert("RGB")
    won = Image.open(io.BytesIO(renderer.ticket(**kwargs, won=True, prize=61_725))).convert("RGB")
    assert plain.size == won.size
    # El sello rojo cambia la esquina de abajo a la derecha.
    corner = (won.width - 200, won.height - 90, won.width - 10, won.height - 10)
    assert plain.crop(corner).tobytes() != won.crop(corner).tobytes()
