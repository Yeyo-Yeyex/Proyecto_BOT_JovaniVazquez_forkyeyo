"""Pruebas de bot.services.craps: las reglas puras de los dados (`dados`, craps).

Los dados se fuerzan con un `random.Random` falso (`craps_fakes.Scripted`) o pasando
la tirada a `CrapsGame.roll`, nunca con estadística.
"""

from __future__ import annotations

import random
from dataclasses import replace
from fractions import Fraction

import pytest
from craps_fakes import Scripted

from bot.services.craps import (
    BAR,
    CRAPS,
    NATURALS,
    ODDS_MAX,
    POINTS,
    TRUE_ODDS,
    Bet,
    CrapsError,
    CrapsGame,
    Hand,
    Roll,
    Status,
    format_odds,
    house_edge,
    milestone,
    odds_profit,
    parse_bet,
    point_chance,
    throw,
    total_chance,
)

PASS, DONT = Bet.PASS, Bet.DONT


def dice_for(total: int) -> tuple[int, int]:
    """Dos dados que suman `total` (dobles solo con 2 y 12)."""
    low = max(1, total - 6)
    return low, total - low


def play(bet: Bet, *totals: int, stake: int = 100) -> CrapsGame:
    """Partida con una tirada por cada total de `totals`."""
    game = CrapsGame.new(stake, bet)
    for total in totals:
        game.roll(dice_for(total))
    return game


# -- Probabilidades ---------------------------------------------------------------------


def test_total_chance_cuenta_las_combinaciones_de_dos_dados() -> None:
    assert total_chance(2) == Fraction(1, 36)
    assert total_chance(7) == Fraction(1, 6)
    assert total_chance(12) == Fraction(1, 36)
    assert sum(total_chance(t) for t in range(2, 13)) == 1
    assert total_chance(1) == 0 and total_chance(13) == 0


def test_point_chance_es_hacer_el_punto_antes_que_el_siete() -> None:
    assert point_chance(4) == Fraction(1, 3)
    assert point_chance(5) == Fraction(2, 5)
    assert point_chance(6) == Fraction(5, 11)
    assert point_chance(8) == point_chance(6)


def test_throw_tira_dos_dados_de_seis_caras() -> None:
    assert throw(Scripted((3, 5))) == (3, 5)
    for seed in range(20):
        a, b = throw(random.Random(seed))
        assert 1 <= a <= 6 and 1 <= b <= 6


# -- La ventaja de la casa --------------------------------------------------------------


def test_la_ventaja_de_pase_es_7_entre_495_exacto() -> None:
    assert house_edge(PASS) == Fraction(7, 495)
    assert float(house_edge(PASS)) == pytest.approx(0.01414, abs=1e-5)


def test_la_ventaja_de_no_pase_es_3_entre_220_exacto() -> None:
    assert house_edge(DONT) == Fraction(3, 220)
    assert float(house_edge(DONT)) == pytest.approx(0.01364, abs=1e-5)


@pytest.mark.parametrize("bet", list(Bet))
@pytest.mark.parametrize("point", POINTS)
def test_las_odds_devuelven_exactamente_lo_apostado_de_media(bet: Bet, point: int) -> None:
    """Esperanza 0 con `Fraction`; 60 es múltiplo de todos los denominadores (sin redondeo)."""
    amount = 60
    p = point_chance(point)
    profit = Fraction(odds_profit(bet, point, amount))
    if bet is PASS:
        expected = p * profit - (1 - p) * amount
    else:
        expected = (1 - p) * profit - p * amount
    assert expected == 0


# -- Salida con Pase --------------------------------------------------------------------


@pytest.mark.parametrize("total", sorted(NATURALS))
def test_pase_gana_en_la_salida_con_7_u_11(total: int) -> None:
    game = play(PASS, total)
    assert game.status is Status.WON and not game.playing
    assert game.point is None
    assert game.payout == 200 and game.net == 100


@pytest.mark.parametrize("total", sorted(CRAPS))
def test_pase_pierde_en_la_salida_con_2_3_o_12(total: int) -> None:
    game = play(PASS, total)
    assert game.status is Status.LOST and not game.playing
    assert game.payout == 0 and game.net == -100


@pytest.mark.parametrize("point", POINTS)
def test_pase_pone_el_punto_con_4_5_6_8_9_o_10(point: int) -> None:
    game = play(PASS, point)
    assert game.status is Status.POINT and game.playing
    assert game.point == point
    assert game.last is not None and game.last.come_out


# -- Salida con No pase -----------------------------------------------------------------


@pytest.mark.parametrize("total", [2, 3])
def test_no_pase_gana_en_la_salida_con_2_o_3(total: int) -> None:
    game = play(DONT, total)
    assert game.status is Status.WON
    assert game.payout == 200 and game.net == 100


@pytest.mark.parametrize("total", sorted(NATURALS))
def test_no_pase_pierde_en_la_salida_con_7_u_11(total: int) -> None:
    game = play(DONT, total)
    assert game.status is Status.LOST
    assert game.payout == 0 and game.net == -100


def test_la_barra_del_12_empata_con_no_pase_y_devuelve_la_apuesta() -> None:
    assert BAR == 12
    game = play(DONT, BAR, stake=250)
    assert game.status is Status.PUSH and not game.playing
    assert game.payout == 250 and game.net == 0


def test_el_12_sigue_perdiendo_con_pase() -> None:
    assert play(PASS, BAR).status is Status.LOST


@pytest.mark.parametrize("point", POINTS)
def test_no_pase_tambien_pone_el_punto(point: int) -> None:
    game = play(DONT, point)
    assert game.status is Status.POINT and game.point == point


# -- El punto ---------------------------------------------------------------------------


@pytest.mark.parametrize("point", POINTS)
def test_pase_gana_al_hacer_el_punto(point: int) -> None:
    game = play(PASS, point, 11, 2, 3)
    assert game.status is Status.POINT  # 11, 2 y 3 no deciden con el punto puesto
    roll = game.roll(dice_for(point))
    assert roll.made and not roll.seven_out
    assert game.status is Status.WON
    assert game.payout == 200 and game.point == point  # el punto se queda al terminar


@pytest.mark.parametrize("bet", list(Bet))
def test_el_siete_fuera_pierde_pase_y_gana_no_pase(bet: Bet) -> None:
    game = play(bet, 8, 7)
    roll = game.last
    assert roll is not None and roll.seven_out and not roll.made
    assert game.status is (Status.LOST if bet is PASS else Status.WON)
    assert game.payout == (0 if bet is PASS else 200)


def test_no_pase_pierde_cuando_se_hace_el_punto() -> None:
    game = play(DONT, 9, 9)
    assert game.status is Status.LOST and game.net == -100


@pytest.mark.parametrize("bet", list(Bet))
def test_las_tiradas_que_no_deciden_dejan_el_punto_puesto(bet: Bet) -> None:
    game = play(bet, 6, 2, 3, 4, 11, 12, 10)
    assert game.status is Status.POINT and game.point == 6
    assert len(game.rolls) == 7
    assert all(r.point == 6 for r in game.rolls[1:])


def test_roll_recuerda_el_punto_que_habia_antes_de_tirar() -> None:
    game = play(PASS, 5, 9)
    assert [r.point for r in game.rolls] == [None, 5]


def test_roll_describe_la_tirada() -> None:
    hard = Roll((3, 3), 6)
    assert hard.total == 6 and hard.doubles and hard.made and hard.hard
    assert not hard.seven_out and not hard.come_out
    easy = Roll((1, 5), 6)
    assert easy.made and not easy.hard and not easy.doubles
    assert Roll((3, 4), 6).seven_out
    assert not Roll((3, 4)).seven_out  # en la salida un 7 es un natural, no siete fuera
    assert Roll((3, 3)).come_out and not Roll((3, 3)).made and not Roll((3, 3)).hard


# -- Errores ----------------------------------------------------------------------------


@pytest.mark.parametrize("stake", [0, -1])
def test_una_partida_necesita_apuesta_positiva(stake: int) -> None:
    with pytest.raises(ValueError, match="positiva"):
        CrapsGame.new(stake, PASS)


@pytest.mark.parametrize("dice", [(0, 3), (3, 7), (7, 1), (-1, 2)])
def test_unos_dados_que_no_existen_se_rechazan_sin_apuntarse(dice: tuple[int, int]) -> None:
    game = CrapsGame.new(100, PASS)
    with pytest.raises(CrapsError, match="no existen"):
        game.roll(dice)
    assert game.rolls == [] and game.playing


def test_no_se_tira_en_una_partida_terminada() -> None:
    game = play(PASS, 7)
    with pytest.raises(CrapsError, match="terminado"):
        game.roll((1, 2))
    with pytest.raises(CrapsError, match="terminado"):
        game.play(Scripted((1, 2)))
    assert len(game.rolls) == 1


def test_play_tira_con_el_azar_dado() -> None:
    game = CrapsGame.new(100, PASS)
    roll = game.play(Scripted((2, 2)))
    assert roll.dice == (2, 2) and game.point == 4


# -- Odds -------------------------------------------------------------------------------


def test_las_odds_solo_se_ponen_con_el_punto_puesto() -> None:
    game = CrapsGame.new(100, PASS)
    with pytest.raises(CrapsError, match="punto"):
        game.add_odds(100)
    assert game.odds_room == 0
    done = play(PASS, 7)
    with pytest.raises(CrapsError, match="punto"):
        done.add_odds(100)
    assert done.odds == 0 and done.odds_room == 0


@pytest.mark.parametrize("amount", [0, -10])
def test_las_odds_han_de_ser_positivas(amount: int) -> None:
    game = play(PASS, 6)
    with pytest.raises(CrapsError, match="Pon algo"):
        game.add_odds(amount)
    assert game.odds == 0


def test_las_odds_llegan_como_mucho_a_odds_max_veces_la_apuesta() -> None:
    game = play(PASS, 6, stake=100)
    assert game.odds_room == ODDS_MAX * 100
    with pytest.raises(CrapsError, match=str(ODDS_MAX)):
        game.add_odds(ODDS_MAX * 100 + 1)
    assert game.odds == 0
    assert game.add_odds(ODDS_MAX * 100) == ODDS_MAX * 100
    assert game.odds_full and game.odds_room == 0
    with pytest.raises(CrapsError):
        game.add_odds(1)


def test_las_odds_se_pueden_poner_a_trozos_y_suman() -> None:
    game = play(PASS, 5, stake=100)
    assert game.add_odds(100) == 100
    assert game.odds_room == (ODDS_MAX - 1) * 100 and not game.odds_full
    assert game.add_odds(50) == 150
    assert game.wagered == 250


def test_sin_odds_odds_full_es_falso() -> None:
    assert not play(PASS, 5).odds_full


@pytest.mark.parametrize(("point", "ratio"), sorted(TRUE_ODDS.items()))
def test_pase_cobra_las_odds_con_la_cuota_de_true_odds(point: int, ratio: tuple[int, int]) -> None:
    a, b = ratio
    amount = 60  # divisible por todos los denominadores: sin redondeo
    assert odds_profit(PASS, point, amount) * b == amount * a


def test_los_pagos_de_las_odds_son_2_a_1_3_a_2_y_6_a_5() -> None:
    assert [odds_profit(PASS, p, 100) for p in (4, 10)] == [200, 200]
    assert [odds_profit(PASS, p, 100) for p in (5, 9)] == [150, 150]
    assert [odds_profit(PASS, p, 100) for p in (6, 8)] == [120, 120]


@pytest.mark.parametrize(("point", "ratio"), sorted(TRUE_ODDS.items()))
def test_no_pase_cobra_las_odds_al_reves(point: int, ratio: tuple[int, int]) -> None:
    a, b = ratio
    amount = 60
    assert odds_profit(DONT, point, amount) * a == amount * b


def test_los_pagos_inversos_de_no_pase_son_1_a_2_2_a_3_y_5_a_6() -> None:
    assert odds_profit(DONT, 4, 100) == 50
    assert odds_profit(DONT, 9, 90) == 60
    assert odds_profit(DONT, 6, 120) == 100


def test_el_premio_de_las_odds_se_redondea_hacia_abajo() -> None:
    assert odds_profit(PASS, 5, 101) == 151  # 151,5
    assert odds_profit(PASS, 6, 103) == 123  # 123,6
    assert odds_profit(DONT, 6, 103) == 85  # 85,83
    assert odds_profit(DONT, 4, 101) == 50  # 50,5
    assert odds_profit(PASS, 6, 1) == 1 and odds_profit(DONT, 6, 1) == 0


def test_format_odds_da_la_cuota_y_la_invierte_para_no_pase() -> None:
    assert format_odds(PASS, 6) == "6:5"
    assert format_odds(DONT, 6) == "5:6"
    assert format_odds(PASS, 4) == "2:1"
    assert format_odds(DONT, 10) == "1:2"
    assert format_odds(PASS, 9) == "3:2"


# -- Pagos y neto con Odds --------------------------------------------------------------


def test_pase_con_odds_ganadoras_cobra_apuesta_mas_odds_con_su_premio() -> None:
    game = play(PASS, 4, stake=100)
    game.add_odds(200)
    assert game.potential() == 2 * 100 + 200 + odds_profit(PASS, 4, 200)
    game.roll((2, 2))
    assert game.status is Status.WON
    assert game.wagered == 300
    assert game.payout == 200 + 200 + odds_profit(PASS, 4, 200)
    assert game.net == game.payout - 300


def test_no_pase_con_odds_ganadoras_cobra_la_cuota_inversa() -> None:
    game = play(DONT, 4, stake=100)
    game.add_odds(200)
    game.roll((3, 4))
    assert game.status is Status.WON
    assert game.payout == 200 + 200 + odds_profit(DONT, 4, 200)
    assert game.net == game.payout - 300


def test_con_odds_perdedoras_se_pierde_todo_lo_puesto() -> None:
    game = play(PASS, 8, stake=100)
    game.add_odds(300)
    game.roll((6, 1))
    assert game.status is Status.LOST
    assert game.payout == 0 and game.wagered == 400 and game.net == -400


def test_potential_sin_odds_es_el_doble_de_la_apuesta() -> None:
    assert CrapsGame.new(70, PASS).potential() == 140
    assert play(PASS, 9, stake=70).potential() == 140


def test_el_neto_es_lo_cobrado_menos_lo_puesto_en_cada_final() -> None:
    won = play(PASS, 7)
    assert won.net == won.payout - won.wagered > 0
    lost = play(PASS, 2)
    assert lost.net == -lost.wagered
    assert play(DONT, 12).net == 0


def test_una_partida_en_juego_no_ha_pagado_nada() -> None:
    game = play(PASS, 6)
    assert game.payout == 0 and game.status is Status.POINT
    assert game.wagered == 100


# -- parse_bet --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "bet"),
    [
        ("pase", PASS),
        ("  PASE ", PASS),
        ("pass", PASS),
        ("p", PASS),
        ("si", PASS),
        ("sí", PASS),
        ("nopase", DONT),
        ("no pase", DONT),
        ("No-Pase", DONT),
        ("no_pase", DONT),
        ("no", DONT),
        ("dont", DONT),
        ("np", DONT),
        ("contra", DONT),
    ],
)
def test_parse_bet_entiende_los_alias(text: str, bet: Bet) -> None:
    assert parse_bet(text) is bet


@pytest.mark.parametrize("text", ["", "casa", "500", "paso"])
def test_parse_bet_rechaza_lo_que_no_es_una_apuesta(text: str) -> None:
    with pytest.raises(ValueError, match="pase"):
        parse_bet(text)


def test_las_apuestas_se_dicen_con_su_emoji() -> None:
    assert PASS.label == "Pase" and PASS.emoji == "✅" and PASS.key == "pase"
    assert DONT.label == "No pase" and DONT.emoji == "🚫" and DONT.key == "nopase"


# -- La mano ----------------------------------------------------------------------------


def observe_all(hand: Hand, *rolls: Roll) -> None:
    for roll in rolls:
        hand.observe(roll)


def test_una_mano_nueva_esta_vacia() -> None:
    hand = Hand()
    assert hand.rolls == 0 and hand.points == [] and not hand.seven_out
    assert hand.distinct_points == 0 and hand.repeat_max == 0


def test_la_mano_cuenta_las_tiradas_y_los_puntos_hechos_en_orden() -> None:
    hand = Hand()
    observe_all(
        hand,
        Roll((2, 2)),  # salida: pone el 4
        Roll((1, 5), 4),
        Roll((2, 2), 4),  # hace el 4
        Roll((3, 3)),  # salida: pone el 6
        Roll((1, 5), 6),  # hace el 6
        Roll((6, 2)),  # salida: pone el 8
        Roll((4, 4), 8),  # hace el 8
        Roll((2, 2)),
        Roll((2, 2), 4),  # hace el 4 otra vez
    )
    assert hand.rolls == 9
    assert hand.points == [4, 6, 8, 4]
    assert hand.distinct_points == 3
    assert not hand.seven_out


def test_los_naturales_y_los_puntos_puestos_no_cuentan_como_puntos_hechos() -> None:
    hand = Hand()
    observe_all(hand, Roll((3, 4)), Roll((5, 6)), Roll((2, 3)))
    assert hand.points == [] and hand.rolls == 3


def test_el_siete_fuera_cierra_la_mano() -> None:
    hand = Hand()
    observe_all(hand, Roll((2, 3)), Roll((3, 4), 5))
    assert hand.seven_out and hand.rolls == 2


def test_un_siete_en_la_salida_no_cierra_la_mano() -> None:
    hand = Hand()
    hand.observe(Roll((3, 4)))
    assert not hand.seven_out


def test_la_mano_no_admite_tiradas_tras_el_siete_fuera() -> None:
    hand = Hand()
    hand.observe(Roll((3, 4), 5))
    with pytest.raises(CrapsError, match="mano"):
        hand.observe(Roll((1, 1)))
    assert hand.rolls == 1


def test_repeat_max_guarda_la_racha_mas_larga_del_mismo_total() -> None:
    hand = Hand()
    observe_all(hand, Roll((3, 4)), Roll((2, 5)), Roll((6, 1)))
    assert hand.repeat == 3 and hand.repeat_max == 3
    observe_all(hand, Roll((1, 1)), Roll((6, 6)), Roll((6, 6)))
    assert hand.repeat == 2 and hand.repeat_max == 3  # el récord no baja
    assert hand.last_total == 12


def test_repeat_max_con_totales_siempre_distintos_es_uno() -> None:
    hand = Hand()
    observe_all(hand, Roll((1, 1)), Roll((1, 2)), Roll((1, 3)))
    assert hand.repeat_max == 1


# -- milestone --------------------------------------------------------------------------


def test_milestone_celebra_solo_las_manos_calientes() -> None:
    for points in (2, 3, 5, 7, 10):
        assert milestone(points)
    for points in (0, 1, 4, 6, 8, 9, 11):
        assert milestone(points) is None
    assert "DIEZ PUNTOS" in (milestone(10) or "")


# -- La partida entera contra la ventaja de la casa -------------------------------------


def expected_net(bet: Bet, *, stake: int, odds: int = 0) -> Fraction:
    """Ganancia neta esperada de la partida, enumerando los dados con `CrapsGame` de verdad.

    La salida recorre las 36 combinaciones; con un punto puesto, la partida se decide con
    probabilidad `point_chance` por el punto y el resto por el siete (lo demás no decide).
    """
    total = Fraction(0)
    for a in range(1, 7):
        for b in range(1, 7):
            game = CrapsGame.new(stake, bet)
            game.roll((a, b))
            if game.playing:
                point = game.point
                assert point is not None
                if odds:
                    game.add_odds(odds)
                made = replace(game, rolls=list(game.rolls))
                made.roll(dice_for(point))
                seven = replace(game, rolls=list(game.rolls))
                seven.roll((3, 4))
                chance = point_chance(point)
                total += Fraction(1, 36) * (chance * made.net + (1 - chance) * seven.net)
            else:
                total += Fraction(1, 36) * game.net
    return total


@pytest.mark.parametrize("bet", list(Bet))
def test_jugar_la_partida_entera_pierde_la_ventaja_de_la_casa_de_media(bet: Bet) -> None:
    stake = 100
    assert expected_net(bet, stake=stake) == -house_edge(bet) * stake


@pytest.mark.parametrize("bet", list(Bet))
def test_las_odds_no_cambian_la_esperanza_de_la_partida(bet: Bet) -> None:
    """60 de apuesta y 120 de odds: múltiplos de todos los denominadores, sin redondeo."""
    assert expected_net(bet, stake=60, odds=120) == expected_net(bet, stake=60)
