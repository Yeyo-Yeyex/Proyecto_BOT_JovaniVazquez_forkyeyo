"""Pruebas de `craps_stats` (bot.services.achievements): los contadores de una partida de dados.

Cada prueba monta una partida terminada con el azar de guion (`craps_fakes`) y mira qué
contadores (`add`) y máximos (`peak`) salen. La hora se fuerza con `when`. Al final, lo
propio de los logros de la categoría `"dice"` (las pruebas globales ya miran el resto).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import pytest
from craps_fakes import game_after, table_for

from bot.services.achievements import AVAILABLE, craps_stats
from bot.services.craps import ODDS_MAX, POINTS, Bet, CrapsGame, Hand, Roll

PASS, DONT = Bet.PASS, Bet.DONT
NOON = datetime(2026, 3, 4, 12, 0)

NATURAL = (3, 4)
ELEVEN = (5, 6)
SNAKE_EYES = (1, 1)
BOXCARS = (6, 6)
THREE = (1, 2)
SIX = (1, 5)
SIX_HARD = (3, 3)
NOTHING = (6, 5)


def stats(game: CrapsGame, hand: Hand | None = None, when: datetime = NOON) -> dict[str, int]:
    """Contadores sumados de la partida (los máximos, aparte, en `peaks`)."""
    return craps_stats(game, hand or table_for(game).hand, when=when).add


def peaks(game: CrapsGame, hand: Hand | None = None, when: datetime = NOON) -> dict[str, int]:
    return craps_stats(game, hand or table_for(game).hand, when=when).peak


def hand_of(*rolls: Roll) -> Hand:
    hand = Hand()
    for roll in rolls:
        hand.observe(roll)
    return hand


# -- Lo común de toda partida -----------------------------------------------------------


def test_toda_partida_cuenta_una_partida_sus_tiradas_y_sus_maximos() -> None:
    game = game_after(SIX, NOTHING, (2, 4))
    assert stats(game)["dice_games"] == 1
    assert stats(game)["dice_rolls"] == 3
    peak = peaks(game)
    assert peak["dice_game_rolls_max"] == 3
    assert peak["dice_hand_points_max"] == 1
    assert peak["dice_hand_rolls_max"] == 3
    assert peak["dice_fire_max"] == 1
    assert peak["dice_repeat_max"] == 1


def test_los_maximos_de_la_mano_vienen_de_la_mano_y_no_de_la_partida() -> None:
    game = game_after(NATURAL)
    hand = hand_of(
        Roll((2, 2)), Roll((2, 2), 4), Roll((3, 3)), Roll((3, 3), 6), Roll((3, 3)), Roll(NATURAL)
    )
    peak = peaks(game, hand)
    assert peak["dice_game_rolls_max"] == 1
    assert peak["dice_hand_points_max"] == 2
    assert peak["dice_hand_rolls_max"] == 6
    assert peak["dice_fire_max"] == 2
    assert peak["dice_repeat_max"] == hand.repeat_max == 3  # 4, 4, 6, 6, 6, 7


def test_fuego_cuenta_puntos_distintos_aunque_se_repitan() -> None:
    hand = hand_of(
        Roll((2, 2)), Roll((2, 2), 4), Roll((2, 2)), Roll((2, 2), 4), Roll((1, 5)),
        Roll((1, 5), 6),
    )  # fmt: skip
    peak = peaks(game_after(NATURAL), hand)
    assert peak["dice_hand_points_max"] == 3 and peak["dice_fire_max"] == 2


def test_no_se_apuntan_contadores_a_cero() -> None:
    add = stats(game_after(NATURAL))
    for absent in (
        "dice_losses",
        "dice_seven_outs",
        "dice_points_set",
        "dice_points_made",
        "dice_odds_games",
        "dice_dont_bar",
        "dice_night",
        "dice_hard",
    ):
        assert absent not in add


def test_pase_y_no_pase_se_cuentan_aparte() -> None:
    assert stats(game_after(NATURAL))["dice_pass_games"] == 1
    assert "dice_dont_games" not in stats(game_after(NATURAL))
    assert stats(game_after(NATURAL, bet=DONT))["dice_dont_games"] == 1
    assert "dice_pass_games" not in stats(game_after(NATURAL, bet=DONT))


def test_cada_total_que_sale_se_cuenta_tantas_veces_como_salga() -> None:
    add = stats(game_after(SIX, NOTHING, NOTHING, NATURAL))
    assert add["dice_total_6"] == 1
    assert add["dice_total_11"] == 2
    assert add["dice_total_7"] == 1
    assert "dice_total_2" not in add


def test_ojos_de_serpiente_y_doble_seis_se_cuentan_en_cualquier_tirada() -> None:
    add = stats(game_after(SIX, SNAKE_EYES, BOXCARS, SNAKE_EYES, NATURAL))
    assert add["dice_snake_eyes"] == 2
    assert add["dice_boxcars"] == 1
    # 1-2 y 2-1 suman 3 pero no son dos unos; 5-1 y 3-3 suman 6 y no son dobles seises.
    other = stats(game_after(THREE))
    assert "dice_snake_eyes" not in other and "dice_boxcars" not in other


# -- La salida --------------------------------------------------------------------------


@pytest.mark.parametrize("roll", [NATURAL, ELEVEN])
def test_los_naturales_cuentan_en_la_salida(roll: tuple[int, int]) -> None:
    add = stats(game_after(roll))
    assert add["dice_naturals"] == 1
    assert "dice_craps_rolls" not in add


@pytest.mark.parametrize("roll", [SNAKE_EYES, THREE, BOXCARS])
def test_las_pifias_cuentan_en_la_salida(roll: tuple[int, int]) -> None:
    add = stats(game_after(roll))
    assert add["dice_craps_rolls"] == 1
    assert "dice_naturals" not in add


def test_un_siete_despues_del_punto_no_es_un_natural() -> None:
    add = stats(game_after(SIX, NATURAL))
    assert "dice_naturals" not in add
    assert add["dice_seven_outs"] == 1


def test_un_once_en_la_salida_cuenta_natural_y_once_pero_no_el_siete() -> None:
    add = stats(game_after(ELEVEN))
    assert add["dice_elevens"] == 1 and add["dice_naturals"] == 1
    assert "dice_elevens" not in stats(game_after(NATURAL))
    assert "dice_elevens" not in stats(game_after(SIX, ELEVEN, NATURAL))


def test_las_pifias_cuentan_igual_con_no_pase() -> None:
    assert stats(game_after(SNAKE_EYES, bet=DONT))["dice_craps_rolls"] == 1


# -- El punto ---------------------------------------------------------------------------


def test_poner_el_punto_cuenta_aunque_luego_no_se_haga() -> None:
    add = stats(game_after(SIX, NATURAL))
    assert add["dice_points_set"] == 1
    assert "dice_points_made" not in add


def test_hacer_el_punto_cuenta_el_punto_y_cual_era() -> None:
    add = stats(game_after(SIX, (2, 4)))
    assert add["dice_points_made"] == 1 and add["dice_point_made_6"] == 1
    assert "dice_hard" not in add
    assert not any(k.startswith("dice_hard_") for k in add)


@pytest.mark.parametrize("point", [4, 6, 8, 10])
def test_hacer_el_punto_con_dobles_cuenta_por_las_malas_y_cual(point: int) -> None:
    half = point // 2
    add = stats(game_after((half, half), (half, half)))
    assert add["dice_hard"] == 1 and add[f"dice_hard_{point}"] == 1
    assert add[f"dice_point_made_{point}"] == 1


def test_el_punto_hecho_a_secas_no_es_por_las_malas_aunque_el_anterior_lo_fuera() -> None:
    add = stats(game_after(SIX_HARD, NOTHING, (2, 4)))
    assert "dice_hard" not in add and add["dice_point_made_6"] == 1


@pytest.mark.parametrize("point", POINTS)
def test_cada_punto_hecho_tiene_su_contador(point: int) -> None:
    low = max(1, point - 6)
    add = stats(game_after((low, point - low), (point - low, low)))
    assert add[f"dice_point_made_{point}"] == 1


def test_con_no_pase_hacer_el_punto_tambien_se_cuenta() -> None:
    add = stats(game_after(SIX, (2, 4), bet=DONT))
    assert add["dice_points_made"] == 1
    assert "dice_wins" not in add and add["dice_losses"] == 1


# -- Siete fuera ------------------------------------------------------------------------


def test_siete_fuera_cuenta_y_el_inmediato_es_el_exprés() -> None:
    quick = stats(game_after(SIX, NATURAL))
    assert quick["dice_seven_outs"] == 1 and quick["dice_seven_first"] == 1
    slow = stats(game_after(SIX, NOTHING, NATURAL))
    assert slow["dice_seven_outs"] == 1 and "dice_seven_first" not in slow


def test_siete_fuera_con_no_pase_gana_y_cuenta_el_siete() -> None:
    add = stats(game_after(SIX, NATURAL, bet=DONT))
    assert add["dice_seven_outs"] == 1
    assert add["dice_wins"] == 1 and add["dice_dont_wins"] == 1 and add["dice_dont_seven"] == 1


def test_no_pase_que_gana_por_pifia_no_cuenta_el_siete() -> None:
    add = stats(game_after(SNAKE_EYES, bet=DONT))
    assert add["dice_dont_wins"] == 1
    assert "dice_dont_seven" not in add


# -- Ganar, perder, empatar -------------------------------------------------------------


def test_ganar_con_pase_cuenta_victoria_ganancia_y_maximo() -> None:
    game = game_after(NATURAL, stake=250)
    add, peak = stats(game), peaks(game)
    assert add["dice_wins"] == 1 and add["dice_pass_wins"] == 1
    assert "dice_dont_wins" not in add and "dice_losses" not in add
    assert add["dice_profit"] == 250 and peak["dice_win_max"] == 250


def test_ganar_con_odds_cuenta_la_ganancia_neta_con_ellas() -> None:
    game = game_after(SIX, (2, 4), odds=200)
    assert stats(game)["dice_profit"] == game.net > game.stake
    assert peaks(game)["dice_win_max"] == game.net


def test_perder_cuenta_derrota_y_no_suma_ganancia() -> None:
    game = game_after(SNAKE_EYES)
    add, peak = stats(game), peaks(game)
    assert add["dice_losses"] == 1
    assert "dice_wins" not in add and "dice_profit" not in add and "dice_win_max" not in peak


def test_empatar_con_la_barra_cuenta_la_barra_y_no_gana_ni_pierde() -> None:
    add = stats(game_after(BOXCARS, bet=DONT))
    assert add["dice_dont_bar"] == 1
    assert "dice_wins" not in add and "dice_losses" not in add
    assert add["dice_craps_rolls"] == 1  # el 12 sigue siendo una pifia de salida


def test_doble_seis_y_para_casa_es_perder_con_pase_en_la_salida() -> None:
    assert stats(game_after(BOXCARS))["dice_boxcars_lost"] == 1
    assert "dice_boxcars_lost" not in stats(game_after(BOXCARS, bet=DONT))
    assert "dice_boxcars_lost" not in stats(game_after(SNAKE_EYES))
    assert "dice_boxcars_lost" not in stats(game_after(SIX, BOXCARS, NATURAL))


def test_partida_larga_perdida_son_diez_tiradas_o_mas() -> None:
    nine = stats(game_after(SIX, *[NOTHING] * 7, NATURAL))
    assert peaks(game_after(SIX, *[NOTHING] * 7, NATURAL))["dice_game_rolls_max"] == 9
    assert "dice_long_lost" not in nine
    ten = game_after(SIX, *[NOTHING] * 8, NATURAL)
    assert stats(ten)["dice_long_lost"] == 1
    assert peaks(ten)["dice_game_rolls_max"] == 10


def test_una_partida_larga_ganada_no_es_tanto_para_nada() -> None:
    game = game_after(SIX, *[NOTHING] * 9, (2, 4))
    assert "dice_long_lost" not in stats(game) and stats(game)["dice_wins"] == 1


def test_cobrar_exactamente_777_es_ganar_con_ese_cobro() -> None:
    # Pase al 5 con 231 en Odds: 200 + 231 + 346 de premio = 777.
    game = game_after((1, 4), (2, 3), odds=231)
    assert game.payout == 777
    assert stats(game)["dice_cash_777"] == 1
    other = game_after((1, 4), (2, 3), odds=230)
    assert other.payout != 777 and "dice_cash_777" not in stats(other)


# -- Odds -------------------------------------------------------------------------------


def test_poner_odds_cuenta_la_partida_con_odds() -> None:
    add = stats(game_after(SIX, (2, 4), odds=100))
    assert add["dice_odds_games"] == 1
    assert "dice_odds_full" not in add
    assert "dice_odds_games" not in stats(game_after(SIX, (2, 4)))


def test_odds_al_tope_ganadoras_y_perdedoras() -> None:
    won = stats(game_after(SIX, (2, 4), stake=100, odds=ODDS_MAX * 100))
    assert won["dice_odds_full"] == 1 and won["dice_odds_full_won"] == 1
    assert "dice_odds_full_lost" not in won
    lost = stats(game_after(SIX, NATURAL, stake=100, odds=ODDS_MAX * 100))
    assert lost["dice_odds_full"] == 1 and lost["dice_odds_full_lost"] == 1
    assert "dice_odds_full_won" not in lost


def test_odds_por_debajo_del_tope_no_cuentan_como_al_tope() -> None:
    add = stats(game_after(SIX, NATURAL, stake=100, odds=ODDS_MAX * 100 - 1))
    assert "dice_odds_full" not in add and "dice_odds_full_lost" not in add


# -- Fechas -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "night"), [(1, False), (2, True), (5, True), (6, False), (12, False)]
)
def test_la_noche_es_de_las_dos_a_las_seis(hour: int, night: bool) -> None:
    add = stats(game_after(NATURAL), when=datetime(2026, 3, 4, hour, 30))
    assert ("dice_night" in add) is night


def test_dias_con_logro_propio() -> None:
    game = game_after(NATURAL)
    for stat, month, day in (
        ("dice_canarias", 5, 30),
        ("dice_nochevieja", 12, 31),
        ("dice_inocentes", 12, 28),
    ):
        assert stats(game, when=datetime(2026, month, day, 12))[stat] == 1
        assert stat not in stats(game, when=datetime(2026, month, day - 1, 12))
        assert stat not in stats(game, when=NOON)


def test_viernes_13_es_perder_dinero_ese_dia() -> None:
    friday = datetime(2026, 2, 13, 12)
    assert friday.weekday() == 4
    assert stats(game_after(SNAKE_EYES), when=friday)["dice_friday13"] == 1
    assert "dice_friday13" not in stats(game_after(NATURAL), when=friday)
    assert "dice_friday13" not in stats(game_after(BOXCARS, bet=DONT), when=friday)  # empate
    thursday = datetime(2026, 2, 12, 12)
    assert "dice_friday13" not in stats(game_after(SNAKE_EYES), when=thursday)
    friday_14 = datetime(2026, 3, 6, 12)
    assert friday_14.weekday() == 4 and friday_14.day != 13
    assert "dice_friday13" not in stats(game_after(SNAKE_EYES), when=friday_14)


# -- Los logros de la categoría "dice" --------------------------------------------------

DICE = [a for a in AVAILABLE if a.category == "dice"]


def test_hay_logros_de_dados_y_con_el_prefijo_de_la_familia() -> None:
    assert len(DICE) >= 40
    assert all(a.id.startswith("dados") for a in DICE)
    assert all(a.stat.startswith("dice_") for a in DICE)


def test_los_nombres_de_los_logros_de_dados_no_se_repiten() -> None:
    names = [a.name for a in DICE]
    assert len(names) == len(set(names))
    ids = [a.id for a in DICE]
    assert len(ids) == len(set(ids))


def test_las_metas_de_cada_contador_de_dados_crecen_con_la_rareza_sin_bajar() -> None:
    by_stat: dict[str, list] = defaultdict(list)
    for achievement in DICE:
        if len(achievement.conditions) == 1:
            by_stat[achievement.stat].append(achievement)
    scale = ["Común", "Raro", "Épico", "Legendario", "Mítico"]
    for stat, tiers in by_stat.items():
        goals = [a.goal for a in tiers]
        assert goals == sorted(goals) and len(set(goals)) == len(goals), stat
        levels = [scale.index(a.rarity.label) for a in tiers]
        assert levels == sorted(levels), f"{stat}: la rareza baja al subir la meta"


def battery() -> set[str]:
    """Los contadores que sacan `craps_stats` en un surtido de partidas que los cubre."""
    keys: set[str] = set()
    games: list[CrapsGame] = []
    for bet in Bet:
        for first in ((a, b) for a in range(1, 7) for b in range(1, 7)):
            games.append(game_after(first, bet=bet))
            if first[0] + first[1] in POINTS:
                total = first[0] + first[1]
                low = total // 2 if total % 2 == 0 else max(1, total - 6)
                made = (low, total - low)
                games.append(game_after(first, made, bet=bet, odds=300))
                games.append(game_after(first, NATURAL, bet=bet, odds=100))
                games.append(game_after(first, *[NOTHING] * 8, NATURAL, bet=bet))
    games.append(game_after((1, 4), (2, 3), odds=231))  # cobra 777
    for game in games:
        keys |= set(stats(game))
        keys |= set(peaks(game))
        for when in (
            datetime(2026, 1, 3, 3),
            datetime(2026, 5, 30, 12),
            datetime(2026, 12, 31, 12),
            datetime(2026, 12, 28, 12),
            datetime(2026, 2, 13, 12),
        ):
            keys |= set(stats(game, when=when))
    return keys


def test_cada_condicion_de_los_logros_de_dados_la_alimenta_craps_stats() -> None:
    produced = battery()
    missing = {stat for a in DICE for stat, _goal in a.conditions if stat not in produced}
    assert not missing, f"Contadores que ningún caso de craps_stats produce: {sorted(missing)}"
