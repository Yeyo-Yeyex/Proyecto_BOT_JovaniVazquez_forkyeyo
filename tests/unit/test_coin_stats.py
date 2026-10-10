"""Pruebas de `coin_stats` (bot.services.achievements): los contadores de una partida de moneda.

Cada prueba monta una partida terminada con el azar de guion (`coin_fakes`) y mira qué
contadores (`add`) y máximos (`peak`) salen. La hora se fuerza con `when`.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from coin_fakes import game_after

from bot.services.achievements import coin_stats
from bot.services.coin import MAX_FLIPS, CoinGame, Outcome, Side

CARA, CRUZ, EDGE = Outcome.CARA, Outcome.CRUZ, Outcome.EDGE
NOON = datetime(2026, 3, 4, 12, 0)


def cashed(
    picks: list[Side], outcomes: list[Outcome], *, stake: int = 100, upcoming: Outcome = CARA
) -> CoinGame:
    game = game_after(picks, outcomes, stake=stake, upcoming=upcoming)
    game.cash_out()
    return game


def stats(game: CoinGame, when: datetime = NOON) -> dict[str, int]:
    """Contadores sumados de la partida (los máximos, aparte, en `peaks`)."""
    return coin_stats(game, when=when).add


def peaks(game: CoinGame, when: datetime = NOON) -> dict[str, int]:
    return coin_stats(game, when=when).peak


def test_toda_partida_cuenta_una_partida_sus_lanzamientos_y_su_racha() -> None:
    game = cashed([Side.CARA, Side.CRUZ], [CARA, CRUZ])
    assert stats(game)["coin_games"] == 1
    assert stats(game)["coin_flips"] == 2
    assert peaks(game)["coin_streak_max"] == 2


def test_los_aciertos_se_cuentan_por_lado_pedido() -> None:
    game = cashed([Side.CARA, Side.CRUZ, Side.CRUZ], [CARA, CRUZ, CRUZ])
    add = stats(game)
    assert add["coin_wins"] == 3
    assert add["coin_wins_cara"] == 1
    assert add["coin_wins_cruz"] == 2


def test_no_se_apuntan_contadores_a_cero() -> None:
    add = stats(game_after([Side.CARA], [CRUZ]))
    for absent in ("coin_wins", "coin_wins_cara", "coin_cashouts", "coin_edges", "coin_gallina"):
        assert absent not in add


@pytest.mark.parametrize(
    ("side", "stat", "other"),
    [
        (Side.CARA, "coin_loyal_cara", "coin_loyal_cruz"),
        (Side.CRUZ, "coin_loyal_cruz", "coin_loyal_cara"),
    ],
)
def test_leal_a_un_lado_con_cinco_aciertos_pidiendo_siempre_lo_mismo(
    side: Side, stat: str, other: str
) -> None:
    outcome = CARA if side is Side.CARA else CRUZ
    add = stats(cashed([side] * 5, [outcome] * 5))
    assert add[stat] == 1
    assert other not in add


def test_con_cuatro_aciertos_iguales_aun_no_es_leal() -> None:
    add = stats(cashed([Side.CARA] * 4, [CARA] * 4))
    assert "coin_loyal_cara" not in add and "coin_loyal_cruz" not in add


def test_no_es_leal_si_cambia_de_lado_aunque_acierte_cinco() -> None:
    picks = [Side.CARA, Side.CARA, Side.CRUZ, Side.CARA, Side.CARA]
    outcomes = [CARA, CARA, CRUZ, CARA, CARA]
    add = stats(cashed(picks, outcomes))
    assert "coin_loyal_cara" not in add


def test_chaquetero_es_alternar_de_lado_en_cuatro_aciertos() -> None:
    picks = [Side.CARA, Side.CRUZ, Side.CARA, Side.CRUZ]
    add = stats(cashed(picks, [CARA, CRUZ, CARA, CRUZ]))
    assert add["coin_flipflop"] == 1


def test_no_es_chaquetero_con_tres_aciertos_ni_repitiendo_lado() -> None:
    assert "coin_flipflop" not in stats(
        cashed([Side.CARA, Side.CRUZ, Side.CARA], [CARA, CRUZ, CARA])
    )
    assert "coin_flipflop" not in stats(
        cashed([Side.CARA, Side.CRUZ, Side.CRUZ, Side.CARA], [CARA, CRUZ, CRUZ, CARA])
    )


# -- Cobrar -----------------------------------------------------------------------------


def test_cobrar_cuenta_el_cobro_el_multiplicador_y_la_ganancia() -> None:
    game = cashed([Side.CARA, Side.CARA, Side.CARA], [CARA, CARA, CARA], stake=100)
    add, peak = stats(game), peaks(game)
    assert add["coin_cashouts"] == 1
    assert peak["coin_cash_mult_max"] == 8
    assert peak["coin_win_max"] == 700
    assert "coin_gallina" not in add and "coin_finish" not in add


def test_gallina_es_cobrar_con_un_solo_acierto() -> None:
    assert stats(cashed([Side.CARA], [CARA]))["coin_gallina"] == 1
    assert "coin_gallina" not in stats(cashed([Side.CARA, Side.CARA], [CARA, CARA]))


def test_la_siguiente_habria_sido_de_canto_solo_al_cobrar_a_medias() -> None:
    game = cashed([Side.CARA], [CARA], upcoming=EDGE)
    assert stats(game)["coin_next_edge"] == 1
    assert "coin_next_edge" not in stats(cashed([Side.CARA], [CARA], upcoming=CARA))
    assert "coin_next_edge" not in stats(cashed([Side.CARA], [CARA], upcoming=CRUZ))


def test_en_el_tope_no_cuenta_el_canto_siguiente_pero_si_el_final() -> None:
    game = cashed([Side.CARA] * MAX_FLIPS, [CARA] * MAX_FLIPS, upcoming=EDGE)
    add = stats(game)
    assert add["coin_finish"] == 1
    assert "coin_next_edge" not in add
    assert peaks(game)["coin_cash_mult_max"] == 1_024


def test_cobrar_666_es_cobrar_exactamente_666() -> None:
    assert stats(cashed([Side.CARA], [CARA], stake=333))["coin_cash_666"] == 1
    assert "coin_cash_666" not in stats(cashed([Side.CARA], [CARA], stake=334))


# -- Perder y canto ---------------------------------------------------------------------


def test_fallar_a_la_primera_cuenta_derrota_y_primer_fallo() -> None:
    game = game_after([Side.CARA], [CRUZ])
    add = stats(game)
    assert add["coin_losses"] == 1 and add["coin_first_fail"] == 1
    assert "coin_lost_big" not in add
    assert "coin_cashouts" not in add and "coin_win_max" not in peaks(game)


def test_perder_con_x16_o_mas_en_juego_es_perder_a_lo_grande() -> None:
    outcomes = [CARA] * 4 + [CRUZ]
    game = game_after([Side.CARA] * 5, outcomes)
    add = stats(game)
    assert add["coin_losses"] == 1 and add["coin_lost_big"] == 1
    assert "coin_first_fail" not in add
    outcomes = [CARA] * 3 + [CRUZ]
    assert "coin_lost_big" not in stats(game_after([Side.CARA] * 4, outcomes))


def test_el_canto_a_la_primera_cuenta_primer_canto_y_lo_perdido() -> None:
    game = game_after([Side.CARA], [EDGE])
    add = stats(game)
    assert add["coin_edges"] == 1 and add["coin_first_edge"] == 1
    assert add["coin_edge_lost"] == 100
    assert "coin_edge_big" not in add and "coin_losses" not in add


def test_el_canto_con_x8_o_mas_es_un_canto_gordo_y_cuenta_lo_que_habia() -> None:
    game = game_after([Side.CARA] * 4, [CARA, CARA, CARA, EDGE], stake=10)
    add = stats(game)
    assert add["coin_edges"] == 1 and add["coin_edge_big"] == 1
    assert add["coin_edge_lost"] == 80
    assert "coin_first_edge" not in add
    assert peaks(game)["coin_streak_max"] == 3


# -- Fechas -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "night"), [(1, False), (2, True), (5, True), (6, False), (12, False)]
)
def test_la_noche_es_de_las_dos_a_las_seis(hour: int, night: bool) -> None:
    add = stats(game_after([Side.CARA], [CRUZ]), datetime(2026, 3, 4, hour, 30))
    assert ("coin_night" in add) is night


def test_hispanidad_y_nochevieja_son_su_dia() -> None:
    game = game_after([Side.CARA], [CRUZ])
    assert stats(game, datetime(2026, 10, 12, 12))["coin_hispanidad"] == 1
    assert "coin_hispanidad" not in stats(game, datetime(2026, 10, 13, 12))
    assert stats(game, datetime(2026, 12, 31, 23))["coin_nochevieja"] == 1
    assert "coin_nochevieja" not in stats(game, datetime(2026, 12, 30, 23))


def test_viernes_13_pierde_solo_si_se_pierde_dinero() -> None:
    friday = datetime(2026, 2, 13, 12)
    assert friday.weekday() == 4
    assert stats(game_after([Side.CARA], [CRUZ]), friday)["coin_friday13"] == 1
    assert stats(game_after([Side.CARA], [EDGE]), friday)["coin_friday13"] == 1
    assert "coin_friday13" not in stats(cashed([Side.CARA], [CARA]), friday)
    # Un 13 que no es viernes no cuenta.
    monday = datetime(2026, 4, 13, 12)
    assert monday.weekday() == 0
    assert "coin_friday13" not in stats(game_after([Side.CARA], [CRUZ]), monday)
