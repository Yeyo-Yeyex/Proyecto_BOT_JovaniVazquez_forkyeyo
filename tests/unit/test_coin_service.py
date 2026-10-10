"""Pruebas de bot.services.coin: las reglas puras de Cara o cruz (`moneda`).

El azar se fuerza con un `random.Random` falso (`coin_fakes.Scripted`) que devuelve los
números que hacen salir los resultados pedidos, nunca con estadística.
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from coin_fakes import Scripted

from bot.services.coin import (
    EDGE_CHANCE,
    MAX_FLIPS,
    CoinError,
    CoinGame,
    Flip,
    Outcome,
    Side,
    Status,
    format_multiplier,
    milestone,
    multiplier,
    parse_side,
    toss,
    win_chance,
)

CARA, CRUZ, EDGE = Outcome.CARA, Outcome.CRUZ, Outcome.EDGE


def play(*outcomes: Outcome, stake: int = 100) -> CoinGame:
    """Partida nueva con los lanzamientos de `outcomes` (el primero es el `upcoming`)."""
    return CoinGame.new(stake, Scripted(*outcomes))


# -- El canto y la probabilidad ---------------------------------------------------------


def test_el_canto_sale_con_edge_chance_y_acertar_es_49_5() -> None:
    assert EDGE_CHANCE == Fraction(1, 100)
    assert win_chance() == Fraction(99, 200)
    assert float(win_chance()) == pytest.approx(0.495)


def test_toss_cae_de_canto_justo_por_debajo_de_edge_chance() -> None:
    edge = float(EDGE_CHANCE)
    just_below = random.Random()
    just_below.random = lambda: edge - 1e-9  # type: ignore[method-assign]
    assert toss(just_below) is EDGE
    at_edge = random.Random()
    at_edge.random = lambda: edge  # type: ignore[method-assign]
    assert toss(at_edge) is not EDGE


@pytest.mark.parametrize(
    ("rolls", "expected"),
    [
        ([0.0], EDGE),
        ([0.0099], EDGE),
        ([0.5, 0.0], CARA),
        ([0.5, 0.4999], CARA),
        ([0.5, 0.5], CRUZ),
        ([0.5, 0.9999], CRUZ),
    ],
)
def test_toss_reparte_cara_y_cruz_a_partes_iguales_fuera_del_canto(
    rolls: list[float], expected: Outcome
) -> None:
    rng = random.Random()
    queue = list(rolls)
    rng.random = lambda: queue.pop(0)  # type: ignore[method-assign]
    assert toss(rng) is expected
    assert not queue  # el canto no gasta el segundo número


def test_los_resultados_se_dicen_con_su_emoji() -> None:
    assert CARA.side is Side.CARA and CRUZ.side is Side.CRUZ and EDGE.side is None
    assert CARA.label == "👑 Cara"
    assert CRUZ.label == "✈️ Cruz"
    assert EDGE.label == "🪙 De canto"
    assert Side.CARA.other is Side.CRUZ and Side.CRUZ.other is Side.CARA


# -- Multiplicadores --------------------------------------------------------------------


@pytest.mark.parametrize("wins", range(MAX_FLIPS + 1))
def test_el_multiplicador_es_dos_elevado_a_los_aciertos(wins: int) -> None:
    assert multiplier(wins) == 2**wins


@pytest.mark.parametrize("wins", [-1, MAX_FLIPS + 1])
def test_una_racha_que_no_existe_no_tiene_multiplicador(wins: int) -> None:
    with pytest.raises(ValueError, match="racha"):
        multiplier(wins)


def test_el_multiplicador_se_escribe_con_punto_de_miles() -> None:
    assert format_multiplier(2) == "×2"
    assert format_multiplier(1_024) == "×1.024"


# -- Partida: acertar, fallar y el canto ------------------------------------------------


def test_una_partida_necesita_apuesta_positiva() -> None:
    for stake in (0, -5):
        with pytest.raises(ValueError, match="positiva"):
            CoinGame.new(stake, Scripted(CARA))


def test_acertar_dobla_lo_que_hay_en_juego() -> None:
    game = play(CARA, CRUZ, CARA, stake=100)
    assert game.playing and game.wins == 0 and game.pot == 100
    flip = game.flip(Side.CARA, Scripted(CRUZ))
    assert flip == Flip(Side.CARA, CARA) and flip.won
    assert game.playing and game.wins == 1 and game.multiplier == 2 and game.pot == 200
    game.flip(Side.CRUZ, Scripted(CARA))
    assert game.wins == 2 and game.pot == 400 and game.last is not None and game.last.won


def test_fallar_pierde_todo_y_no_paga_nada() -> None:
    game = play(CRUZ, CARA)
    flip = game.flip(Side.CARA, Scripted(CARA))
    assert not flip.won
    assert game.status is Status.LOST and not game.playing
    assert game.payout == 0 and game.net == -100


def test_fallar_tras_una_racha_pierde_la_apuesta_y_el_bote_era_mayor() -> None:
    game = play(CARA, CARA, CRUZ, stake=50)
    game.flip(Side.CARA, Scripted(CRUZ))
    game.flip(Side.CARA, Scripted(CARA))
    assert game.status is Status.LOST
    assert game.wins == 1 and game.pot == 100  # lo que se iba a llevar
    assert game.payout == 0 and game.net == -50


def test_el_canto_pierde_aunque_se_pidiera_bien() -> None:
    for pick in Side:
        game = play(EDGE, CARA)
        flip = game.flip(pick, Scripted(CARA))
        assert flip.outcome is EDGE and not flip.won
        assert game.status is Status.EDGE
        assert game.payout == 0 and game.net == -100


def test_el_canto_tras_aciertos_pierde_solo_la_apuesta_pero_dice_cuanto_habia() -> None:
    rng = Scripted(CARA, CARA, EDGE, CARA)
    game = CoinGame.new(10, rng)
    game.flip(Side.CARA, rng)
    game.flip(Side.CARA, rng)
    assert game.wins == 2 and game.upcoming is EDGE
    game.flip(Side.CARA, rng)
    assert game.status is Status.EDGE
    assert game.wins == 2 and game.pot == 40
    assert game.payout == 0 and game.net == -10


def test_el_siguiente_lanzamiento_se_sortea_por_adelantado_y_es_el_que_sale() -> None:
    rng = Scripted(CRUZ, CARA, EDGE)
    game = CoinGame.new(100, rng)
    assert game.upcoming is CRUZ
    first = game.flip(Side.CRUZ, rng)
    assert first.outcome is CRUZ and game.upcoming is CARA
    second = game.flip(Side.CARA, rng)
    assert second.outcome is CARA and game.upcoming is EDGE
    assert [f.outcome for f in game.flips] == [CRUZ, CARA]


def test_flip_lanza_con_el_upcoming_fijado_a_mano() -> None:
    game = play(CARA)
    game.upcoming = CRUZ
    assert game.flip(Side.CRUZ, Scripted(CARA)).outcome is CRUZ


# -- Cobrar -----------------------------------------------------------------------------


def test_cobrar_paga_la_apuesta_por_el_multiplicador() -> None:
    game = play(CARA, CRUZ, CARA, stake=100)
    game.flip(Side.CARA, Scripted(CRUZ))
    game.flip(Side.CRUZ, Scripted(CARA))
    assert game.cash_out() == 400
    assert game.status is Status.CASHED and not game.playing
    assert game.payout == 400 and game.net == 300


def test_no_se_puede_cobrar_sin_aciertos() -> None:
    game = play(CARA)
    with pytest.raises(CoinError, match="Acierta"):
        game.cash_out()
    assert game.playing


def test_no_se_puede_cobrar_ni_lanzar_tras_terminar() -> None:
    lost = play(CARA, CARA)
    lost.flip(Side.CRUZ, Scripted(CARA))
    with pytest.raises(CoinError):
        lost.cash_out()
    with pytest.raises(CoinError):
        lost.flip(Side.CARA, Scripted(CARA))

    cashed = play(CARA, CARA)
    cashed.flip(Side.CARA, Scripted(CARA))
    cashed.cash_out()
    with pytest.raises(CoinError):
        cashed.cash_out()
    with pytest.raises(CoinError):
        cashed.flip(Side.CARA, Scripted(CARA))


def test_una_partida_sin_cobrar_no_ha_pagado_nada() -> None:
    game = play(CARA, CARA)
    game.flip(Side.CARA, Scripted(CARA))
    assert game.playing and game.pot == 200
    assert game.payout == 0


# -- El tope ----------------------------------------------------------------------------


def maxed_game() -> CoinGame:
    rng = Scripted(*[CARA] * (MAX_FLIPS + 1))
    game = CoinGame.new(100, rng)
    for _ in range(MAX_FLIPS):
        game.flip(Side.CARA, rng)
    return game


def test_a_los_diez_aciertos_queda_en_el_tope_y_ya_no_se_puede_lanzar() -> None:
    game = maxed_game()
    assert game.maxed and game.playing and game.wins == MAX_FLIPS
    assert game.multiplier == 1_024 and game.pot == 102_400
    with pytest.raises(CoinError):
        game.flip(Side.CARA, Scripted(CARA))


def test_en_el_tope_se_cobra_el_premio_gordo() -> None:
    game = maxed_game()
    assert game.cash_out() == 102_400
    assert game.payout == 102_400 and game.net == 102_300


# -- parse_side y milestone -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "side"),
    [
        ("cara", Side.CARA),
        ("  CARA ", Side.CARA),
        ("c", Side.CARA),
        ("heads", Side.CARA),
        ("rey", Side.CARA),
        ("corona", Side.CARA),
        ("cruz", Side.CRUZ),
        ("X", Side.CRUZ),
        ("tails", Side.CRUZ),
        ("Falcon", Side.CRUZ),
    ],
)
def test_parse_side_entiende_los_alias(text: str, side: Side) -> None:
    assert parse_side(text) is side


@pytest.mark.parametrize("text", ["", "canto", "a", "100"])
def test_parse_side_rechaza_lo_que_no_es_un_lado(text: str) -> None:
    with pytest.raises(ValueError, match="cara"):
        parse_side(text)


def test_milestone_celebra_solo_las_rachas_gordas() -> None:
    for wins in (3, 5, 7, 8, 9, MAX_FLIPS):
        assert milestone(wins)
    for wins in (0, 1, 2, 4, 6, 11):
        assert milestone(wins) is None
    assert "LA MONEDA DE ORO" in (milestone(MAX_FLIPS) or "")
