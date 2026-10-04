"""Pruebas de bot.services.mines: multiplicadores, retorno y partida."""

from __future__ import annotations

import math
import random
from fractions import Fraction

import pytest

from bot.services.mines import (
    MAX_MULTIPLIER,
    MINE_CHOICES,
    TILES,
    MinesError,
    MinesGame,
    Status,
    format_multiplier,
    multiplier,
    multiplier_cents,
    next_mine_choice,
    payout,
)


def fixed(mines: set[int], stake: int = 100) -> MinesGame:
    return MinesGame(stake=stake, mines=len(mines), mine_tiles=frozenset(mines))


@pytest.mark.parametrize("mines", MINE_CHOICES)
def test_cobrar_pronto_paga_menos_y_nunca_se_gana_a_la_larga(mines: int) -> None:
    # Valor esperado exacto de "destapar k y cobrar": P(sobrevivir k) × mult.
    previous_mult, previous_expected = Fraction(0), Fraction(0)
    for k in range(1, TILES - mines + 1):
        survive = Fraction(math.comb(TILES - mines, k), math.comb(TILES, k))
        mult = multiplier(mines, k)
        expected = survive * mult
        assert expected < 1
        assert mult >= previous_mult
        if 1 < mult < MAX_MULTIPLIER:
            assert expected == 1 - Fraction(13, 100) * survive
            # Fuera del suelo de ×1, cuanto más se arriesga, mejor retorno.
            assert expected >= previous_expected
            previous_expected = expected
        previous_mult = mult


def test_multiplicadores_conocidos() -> None:
    assert multiplier_cents(1, 1) == 100  # 25/24 − 0,13 < 1: se queda en ×1
    assert multiplier_cents(3, 1) == 100  # 25/22 − 0,13 = 1,006
    assert multiplier_cents(3, 2) == 116
    assert multiplier_cents(3, 10) == 492
    assert multiplier_cents(24, 1) == 2_487
    assert multiplier(3, 0) == 1
    assert multiplier(10, 15) == MAX_MULTIPLIER
    assert payout(100, 24, 1) == 2_487
    assert format_multiplier(2_475) == "×24,75"


def test_el_boton_de_minas_recorre_las_opciones() -> None:
    assert next_mine_choice(3) == 5
    assert next_mine_choice(24) == 1
    assert next_mine_choice(7) == 3


def test_new_coloca_las_minas_pedidas() -> None:
    game = MinesGame.new(100, 5, random.Random(1))
    assert len(game.mine_tiles) == 5
    assert all(0 <= t < TILES for t in game.mine_tiles)
    with pytest.raises(ValueError):
        MinesGame.new(100, 7, random.Random(1))
    with pytest.raises(ValueError):
        MinesGame.new(0, 3, random.Random(1))


def test_destapar_seguras_sube_y_cobrar_paga() -> None:
    game = fixed({0, 1, 2})
    assert game.reveal(10)
    assert game.reveal(11)
    assert game.gems == 2
    assert game.next_cents == multiplier_cents(3, 3)
    assert game.safe_chance == Fraction(20, 23)
    paid = game.cash_out()
    assert paid == payout(100, 3, 2)
    assert game.status is Status.CASHED
    assert game.net == paid - 100


def test_pisar_una_mina_lo_pierde_todo() -> None:
    game = fixed({0, 1, 2})
    game.reveal(10)
    assert not game.reveal(1)
    assert game.status is Status.BUSTED
    assert game.exploded == 1
    assert game.payout == 0 and game.net == -100
    with pytest.raises(MinesError):
        game.reveal(12)


def test_no_se_puede_cobrar_sin_destapar_ni_repetir_casilla() -> None:
    game = fixed({0})
    with pytest.raises(MinesError):
        game.cash_out()
    game.reveal(5)
    with pytest.raises(MinesError):
        game.reveal(5)


def test_limpiar_el_tablero() -> None:
    game = fixed(set(range(1, 25)))
    assert game.reveal(0)
    assert game.cleared and game.next_cents is None
    assert game.cash_out() == 2_487


def test_al_azar_elige_una_cerrada_y_lo_cuenta() -> None:
    game = fixed({0})
    rng = random.Random(3)
    for _ in range(5):
        tile = game.random_hidden(rng)
        assert tile not in game.revealed
        if not game.reveal(tile, random_pick=True):
            break
    assert game.random_picks >= 1
