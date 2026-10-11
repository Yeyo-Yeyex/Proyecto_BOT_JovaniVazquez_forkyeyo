"""Estadísticas de logros de una mano de bacará (`baccarat_stats`)."""

from __future__ import annotations

from datetime import datetime

from bot.services.achievements import BACCARAT_DRAGON, baccarat_stats
from bot.services.bacara import BANKER_MIN_STAKE, Coup, Result, Side, payout
from bot.services.blackjack import Card
from bot.services.levels import TIMEZONE

NOON = datetime(2026, 3, 10, 18, tzinfo=TIMEZONE)


def c(rank: int) -> Card:
    return Card(rank, 0)


def stats(coup: Coup, bets: dict[Side, int], *, before=(), when=NOON, **kwargs):  # noqa: ANN001, ANN003, ANN201
    prize = sum(payout(side, stake, coup.winner) for side, stake in bets.items())
    return baccarat_stats(coup, bets=bets, prize=prize, before=list(before), when=when, **kwargs)


def results(*sides: Side) -> list[Result]:
    return [Result(side, 0, 0) for side in sides]


#: Banca natural 9 contra Jugador 3.
BANKER_NINE = Coup((c(1), c(2)), (c(4), c(5)), number=4)
#: Jugador remonta: 0 de salida, saca un 9 con la tercera; la Banca se queda en 3.
PLAYER_SQUEEZE = Coup((c(10), c(13), c(9)), (c(1), c(2)), number=4)
#: Empate a cero, seis cartas.
TIE_ZERO = Coup((c(10), c(13), c(10)), (c(12), c(11), c(10)), number=4)


def test_ganar_a_la_banca_con_natural_de_nueve() -> None:
    delta = stats(BANKER_NINE, {Side.BANKER: 100})
    add = delta.add
    assert add["bac_hands"] == 1 and add["bac_wins"] == 1
    assert add["bac_banker_wins"] == 1
    assert add["bac_naturals_won"] == 1 and add["bac_nine_wins"] == 1
    assert add["bac_commission"] == 5
    assert delta.peak["bac_win_max"] == 95
    assert "bac_losses" not in add


def test_remontada_con_apreton_hasta_el_nueve() -> None:
    add = stats(PLAYER_SQUEEZE, {Side.PLAYER: 100}).add
    assert add["bac_player_wins"] == 1
    assert add["bac_comebacks"] == 1
    assert add["bac_squeeze_nine"] == 1


def test_perder_por_un_punto() -> None:
    coup = Coup((c(3), c(4)), (c(2), c(4)), number=4)  # Jugador 7, Banca 6
    add = stats(coup, {Side.BANKER: 100}).add
    assert add["bac_losses"] == 1 and add["bac_lost_by_one"] == 1


def test_empate_a_cero_devuelve_y_cuenta_las_seis_cartas() -> None:
    add = stats(TIE_ZERO, {Side.BANKER: 100, Side.PLAYER: 100, Side.TIE: 20}).add
    assert add["bac_pushes"] == 1 and add["bac_tie_wins"] == 1
    assert add["bac_tie_zero"] == 1 and add["bac_six_cards"] == 1
    assert add["bac_hedge"] == 1 and add["bac_all_three"] == 1


def test_natural_de_ocho_contra_natural_de_nueve() -> None:
    coup = Coup((c(3), c(5)), (c(4), c(5)), number=4)
    add = stats(coup, {Side.PLAYER: 100}).add
    assert add["bac_natural_beaten"] == 1


def test_empate_de_naturales() -> None:
    coup = Coup((c(4), c(5)), (c(3), c(6)), number=4)
    assert stats(coup, {Side.TIE: 100}).add["bac_natural_tie"] == 1


def test_montar_y_cortar_al_dragon() -> None:
    before = results(*[Side.BANKER] * BACCARAT_DRAGON)
    ride = stats(BANKER_NINE, {Side.BANKER: 100}, before=before).add
    assert ride["bac_dragon_ride"] == 1 and "bac_dragon_slay" not in ride
    slay = stats(PLAYER_SQUEEZE, {Side.PLAYER: 100}, before=before).add
    assert slay["bac_dragon_slay"] == 1
    short = stats(BANKER_NINE, {Side.BANKER: 100}, before=before[1:]).add
    assert "bac_dragon_ride" not in short


def test_la_carretera_mas_larga_y_el_ping_pong() -> None:
    long_road = stats(BANKER_NINE, {Side.BANKER: 100}, before=results(*[Side.BANKER] * 7))
    assert long_road.peak["bac_road_longest"] == 8
    chop = results(*[Side.BANKER, Side.PLAYER] * 3)  # B J B J B J y ahora B
    assert stats(BANKER_NINE, {Side.BANKER: 100}, before=chop).add["bac_pingpong"] == 1


def test_mesa_zapato_y_fechas() -> None:
    first = Coup((c(1), c(2)), (c(4), c(5)), number=1, last=False)
    last = Coup((c(1), c(2)), (c(4), c(5)), number=80, last=True)
    when = datetime(2026, 12, 31, 3, tzinfo=TIMEZONE)
    delta = stats(first, {Side.BANKER: BANKER_MIN_STAKE}, when=when, players=4, shoe_hands=1)
    assert delta.add["bac_shoe_first"] == 1
    assert delta.add["bac_banker_min"] == 1
    assert delta.add["bac_night"] == 1 and delta.add["bac_newyear"] == 1
    assert delta.peak["bac_table_max"] == 4
    assert stats(last, {Side.BANKER: 100}).add["bac_shoe_last"] == 1
    pino = datetime(2026, 9, 8, 12, tzinfo=TIMEZONE)
    assert stats(first, {Side.BANKER: 100}, when=pino).add["bac_pino"] == 1


def test_perder_un_viernes_13() -> None:
    friday = datetime(2026, 11, 13, 20, tzinfo=TIMEZONE)
    assert stats(BANKER_NINE, {Side.PLAYER: 100}, when=friday).add["bac_friday13"] == 1
    assert "bac_friday13" not in stats(BANKER_NINE, {Side.BANKER: 100}, when=friday).add


def test_lo_social_lo_decide_la_mesa() -> None:
    add = stats(BANKER_NINE, {Side.BANKER: 100}, alone_win=True, herd_loss=False, starter=True).add
    assert add["bac_alone_win"] == 1 and add["bac_starter"] == 1
    assert "bac_herd_loss" not in add
