"""Pruebas de bot.services.mines: primera casilla segura, multiplicadores y retorno."""

from __future__ import annotations

import math
import random
from fractions import Fraction

import pytest

from bot.services.mines import (
    MAX_MINES,
    MAX_MULTIPLIER,
    MIN_MINES,
    TILES,
    MinesError,
    MinesGame,
    Status,
    check_mines,
    format_multiplier,
    milestone,
    multiplier,
    multiplier_cents,
    payout,
    risk_summary,
    survival,
)


def fixed(mines: set[int], stake: int = 100) -> MinesGame:
    """Partida con las minas ya colocadas (el primer clic debe ir fuera de ellas)."""
    return MinesGame(stake=stake, mines=len(mines), mine_tiles=frozenset(mines))


@pytest.mark.parametrize("mines", range(MIN_MINES, MAX_MINES + 1))
def test_desde_la_segunda_casilla_cobrar_devuelve_el_99_por_ciento(mines: int) -> None:
    # Valor esperado exacto de "destapar k y cobrar": P(llegar a k) × mult.
    assert multiplier(mines, 1) == 1
    previous = Fraction(1)
    for k in range(2, TILES - mines + 1):
        survive = Fraction(math.comb(24 - mines, k - 1), math.comb(24, k - 1))
        assert survival(mines, k) == survive
        mult = multiplier(mines, k)
        assert mult > previous or mult == MAX_MULTIPLIER
        if mult < MAX_MULTIPLIER:
            assert survive * mult == Fraction(99, 100)
        else:
            assert survive * mult <= Fraction(99, 100)
        previous = mult


def test_mas_minas_paga_mas_por_casilla() -> None:
    for k in (2, 3, 5):
        payouts = [multiplier(m, k) for m in range(MIN_MINES, TILES - k + 1)]
        assert payouts == sorted(payouts)
        assert len(set(payouts)) == len(payouts)


def test_multiplicadores_conocidos() -> None:
    assert multiplier_cents(2, 1) == 100
    assert multiplier_cents(2, 2) == 108  # 0,99 × 24/22
    assert multiplier_cents(23, 2) == 2_376  # 0,99 × 24
    assert multiplier(3, 0) == 1
    assert multiplier(10, 15) == MAX_MULTIPLIER
    assert payout(100, 23, 2) == 2_376
    assert format_multiplier(2_475) == "×24,75"


def test_las_minas_van_de_1_a_23() -> None:
    check_mines(1)
    check_mines(23)
    for bad in (0, 24, 25):
        with pytest.raises(ValueError):
            check_mines(bad)


@pytest.mark.parametrize("seed", range(40))
def test_la_primera_casilla_nunca_es_mina(seed: int) -> None:
    rng = random.Random(seed)
    game = MinesGame.new(100, MAX_MINES, rng)
    tile = rng.randrange(TILES)
    assert game.reveal(tile)
    assert tile not in game.mine_tiles
    assert len(game.mine_tiles) == MAX_MINES
    # Con 23 minas solo queda una buena: no se mueven tras el primer clic.
    placed = game.mine_tiles
    game.reveal(next(t for t in game.hidden if t not in placed) if game.hidden else tile)
    assert game.mine_tiles == placed


def test_new_no_coloca_minas_hasta_el_primer_clic() -> None:
    game = MinesGame.new(100, 5, random.Random(1))
    assert not game.mine_tiles
    assert game.safe_chance == 1
    with pytest.raises(ValueError):
        MinesGame.new(100, 24, random.Random(1))
    with pytest.raises(ValueError):
        MinesGame.new(0, 3, random.Random(1))


def test_destapar_seguras_sube_y_cobrar_paga() -> None:
    game = fixed({0, 1, 2})
    assert game.reveal(10)
    assert game.cashout_value == 100  # la primera devuelve la apuesta
    assert game.reveal(11)
    assert game.gems == 2
    assert game.next_cents == multiplier_cents(3, 3)
    assert game.next_value == payout(100, 3, 3)
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
    game = fixed(set(range(2, 25)))
    assert game.reveal(0)
    assert game.reveal(1)
    assert game.cleared and game.next_cents is None
    assert game.cash_out() == 2_376


def test_al_azar_elige_una_cerrada_y_lo_cuenta() -> None:
    game = fixed({0})
    rng = random.Random(3)
    for _ in range(5):
        tile = game.random_hidden(rng)
        assert tile not in game.revealed
        if not game.reveal(tile, random_pick=True):
            break
    assert game.random_picks >= 1


def test_frases_de_progreso() -> None:
    assert milestone(3, 23) == "🔥 ¡Tres limpias!"
    assert milestone(12, 23) == "🌓 ¡Medio tablero!"
    assert milestone(22, 23) == "😰 Solo queda una buena…"
    assert milestone(23, 23) == "🏁 ¡TABLERO LIMPIO!"
    assert milestone(4, 23) is None


def test_resumen_de_riesgo_cabe_en_el_menu() -> None:
    for mines in range(MIN_MINES, MAX_MINES + 1):
        text = risk_summary(mines)
        assert text.startswith("2ª ×") and "todo ×" in text
        assert len(text) <= 100  # límite de Discord para la descripción
