"""Pruebas de bot.services.pachinko: bolsillos, reserva, sorteo y retorno exacto."""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from fractions import Fraction

import pytest

from bot.services.pachinko import (
    ATARI_CHANCE,
    BALLS,
    FEVER_BALLS,
    MAX_HOLD,
    POCKETS,
    ROWS,
    RUSH,
    START_POCKET,
    SUPER_RUSH,
    Ball,
    Draw,
    Kind,
    PachinkoMachine,
    atari_volley_chance,
    build_volley,
    draw_lottery,
    expected_return,
    hold_bar,
    jackpots_per_atari,
    lottery_return,
    payout,
    paytable_lines,
    pocket_label,
    pocket_probability,
    pocket_return,
)


def ball_in(pocket: int) -> Ball:
    """Bola que va a la derecha `pocket` veces y acaba en ese bolsillo."""
    return Ball((1,) * pocket + (0,) * (ROWS - pocket))


def scripted(values: Iterable[int]):  # noqa: ANN201
    """`randbelow` que devuelve los valores dados, en orden."""
    iterator = iter(values)

    def randbelow(n: int) -> int:
        value = next(iterator)
        assert 0 <= value < n
        return value

    return randbelow


MISS = Draw((1, 2, 3), Kind.MISS, False, 0)


def test_el_bolsillo_es_el_numero_de_rebotes_a_la_derecha() -> None:
    assert ball_in(0).pocket == 0
    assert Ball((0, 1) * 5).pocket == 5
    assert ball_in(ROWS).returned == POCKETS[ROWS]


def test_los_bolsillos_son_simetricos_y_start_no_devuelve_nada() -> None:
    assert POCKETS == tuple(reversed(POCKETS))
    assert len(POCKETS) == ROWS + 1
    assert POCKETS[START_POCKET] == 0
    assert pocket_label(START_POCKET) == "START"
    assert pocket_label(0) == "×10"
    assert pocket_label(3) == "OUT"


def test_las_probabilidades_de_los_bolsillos_suman_uno() -> None:
    assert sum(pocket_probability(k) for k in range(ROWS + 1)) == 1
    assert pocket_probability(START_POCKET) == Fraction(252, 1024)


def test_la_maquina_lanza_diez_bolas_con_un_rebote_por_fila() -> None:
    volley = PachinkoMachine().launch()
    assert len(volley.balls) == BALLS
    assert all(len(ball.path) == ROWS for ball in volley.balls)
    assert len(volley.draws) == min(volley.starts, MAX_HOLD)


def test_la_reserva_guarda_cuatro_y_las_demas_se_pierden() -> None:
    balls = [ball_in(START_POCKET)] * 6 + [ball_in(0)] * 4
    volley = build_volley(balls, lambda: MISS)
    assert len(volley.draws) == MAX_HOLD
    assert volley.wasted == 2
    assert volley.starts == 6
    assert volley.corners == 4
    assert volley.pocket_balls == 4 * POCKETS[0]


def test_atari_par_paga_un_premio() -> None:
    # Toca (0 < 1), número 4 (3 + 1).
    draw = draw_lottery(scripted([0, 3]))
    assert draw == Draw((4, 4, 4), Kind.ATARI, True, 1)


def test_rush_encadena_mientras_sale_cara_y_respeta_el_tope() -> None:
    # Toca, número 5, sigue dos veces y para.
    draw = draw_lottery(scripted([0, 4, 0, 0, 4]))
    assert (draw.kind, draw.jackpots) == (Kind.RUSH, 3)
    # Siempre sigue: se queda en el tope.
    capped = draw_lottery(scripted([0, 4] + [0] * 50))
    assert capped.jackpots == RUSH[2] + 1


def test_el_siete_es_el_super_rush() -> None:
    draw = draw_lottery(scripted([0, 6] + [0] * 50))
    assert draw.kind == Kind.SUPER
    assert draw.digits == (7, 7, 7)
    assert draw.jackpots == SUPER_RUSH[2] + 1


def test_reach_falso_para_justo_al_lado() -> None:
    # No toca (39), lados 9, reach (0 < 1), centro hacia arriba.
    draw = draw_lottery(scripted([39, 8, 0, 1]))
    assert draw.reach and not draw.atari
    assert draw.digits == (9, 1, 9)


def test_un_fallo_sin_reach_nunca_parece_reach() -> None:
    for left, offset in itertools.product(range(9), range(8)):
        draw = draw_lottery(scripted([39, left, 5, offset, 0]))
        assert draw.digits[0] != draw.digits[2]
        assert not draw.reach


def test_el_pago_se_redondea_una_sola_vez() -> None:
    volley = build_volley([ball_in(1)] + [ball_in(3)] * 9, lambda: MISS)
    assert volley.total_balls == 3
    assert payout(volley, 105) == 31  # 105 × 3 / 10 = 31,5


def test_los_premios_gordos_suman_bolas_de_la_compuerta() -> None:
    rush = Draw((5, 5, 5), Kind.RUSH, True, 3)
    volley = build_volley([ball_in(START_POCKET)] + [ball_in(3)] * 9, lambda: rush)
    assert volley.fever_balls == 3 * FEVER_BALLS
    assert volley.best is rush
    assert payout(volley, 100) == 100 * 3 * FEVER_BALLS // BALLS


def test_las_cifras_de_la_documentacion_son_las_reales() -> None:
    assert round(float(pocket_return()), 3) == 0.576
    assert round(float(lottery_return()), 3) == 0.370
    assert round(float(jackpots_per_atari()), 1) == 2.1
    assert round(1 / float(atari_volley_chance())) == 17


def test_el_retorno_total_es_el_de_un_casino_de_verdad() -> None:
    """Entre el 93 y el 96 %: como la ruleta americana (94,7 %)."""
    assert Fraction(93, 100) < expected_return() < Fraction(96, 100)


def test_la_simulacion_se_acerca_al_retorno_exacto() -> None:
    """Comprueba que la máquina de verdad sigue las mismas reglas que las cuentas."""
    import random

    rng = random.Random(1234)
    machine = PachinkoMachine(lambda n: rng.randrange(n))
    volleys = 40_000
    total = sum(machine.launch().total_balls for _ in range(volleys))
    assert total / (volleys * BALLS) == pytest.approx(float(expected_return()), abs=0.03)


def test_la_probabilidad_de_atari_es_la_anunciada() -> None:
    assert ATARI_CHANCE == (1, 40)


def test_la_reserva_usa_formas() -> None:
    assert hold_bar(3) == "●●●○"
    assert hold_bar(9) == "●●●●"


def test_la_tabla_de_premios_explica_reach_y_rush() -> None:
    text = "\n".join(paytable_lines())
    assert "ATARI" in text and "RUSH" in text and "REACH" in text and "START" in text
