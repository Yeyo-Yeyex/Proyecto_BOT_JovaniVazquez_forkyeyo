"""Pruebas de `allatu_stats` y del catálogo de logros de Allá tú."""

from __future__ import annotations

import random
from datetime import datetime
from fractions import Fraction

from bot.services.achievements import BY_ID, CATEGORY_BY_KEY, allatu_stats
from bot.services.allatu import ROUNDS, AllatuGame, Phase, Table

WHEN = datetime(2026, 10, 16, 18, 0)


def rigged(*, own_value: Fraction, other_value: Fraction, gordo: int = 10) -> AllatuGame:
    """Una partida con tu caja (la 0) y la última (la 21) con lo que diga la prueba."""
    rest = [Fraction(0)] * 20
    values = (own_value, *rest, other_value)
    game = AllatuGame(
        stake=100,
        table=Table.NORMAL,
        gordo=gordo,
        values=values,
        haggle_luck=(False,) * len(ROUNDS),
    )
    game.choose(0)
    return game


def play_to_final(game: AllatuGame) -> None:
    while game.phase is not Phase.FINAL:
        if game.phase is Phase.OPEN:
            game.open(game.openable[0])
        else:
            game.no_deal()


def test_la_categoria_y_los_logros_estan_en_el_catalogo() -> None:
    assert "allatu" in CATEGORY_BY_KEY
    ours = [a for a in BY_ID.values() if a.category == "allatu"]
    assert len(ours) >= 40
    secrets = [a for a in ours if a.secret]
    assert len(secrets) >= len(ours) // 7
    assert "casino_nineteen_games" in BY_ID


def test_un_trato_con_el_gordo_en_tu_caja() -> None:
    game = rigged(own_value=Fraction(10), other_value=Fraction(0))
    while game.phase is Phase.OPEN:
        game.open(game.openable[0])
    game.deal()
    delta = allatu_stats(game, when=WHEN)
    add = delta.add
    assert add["allatu_games"] == 1 and add["allatu_games_normal"] == 1
    assert add["allatu_deals"] == 1 and add["allatu_first_deal"] == 1
    assert add["allatu_own_gordo"] == 1 and add["allatu_deal_gordo"] == 1
    assert add["allatu_boxes"] == 6
    assert "allatu_finals" not in add
    assert delta.peak["allatu_offer_max"] == game.payout


def test_cambiar_y_dejar_el_gordo_en_la_otra() -> None:
    game = rigged(own_value=Fraction(10), other_value=Fraction(0))
    play_to_final(game)
    assert game.other == 21
    game.finish(swap=True)
    add = allatu_stats(game, when=WHEN).add
    assert add["allatu_finals"] == 1 and add["allatu_swaps"] == 1
    assert add["allatu_swap_gordo_lost"] == 1 and add["allatu_swap_bad"] == 1
    assert add["allatu_own_gordo"] == 1
    assert add["allatu_all_no"] == 1 and add["allatu_no_deals"] == len(ROUNDS)
    assert add["allatu_zero"] == 1
    # La otra caja se abre sola en el final: no cuenta como abierta por el jugador.
    assert add["allatu_boxes"] == 20


def test_quedarse_el_gordo_en_el_final() -> None:
    game = rigged(own_value=Fraction(10), other_value=Fraction(0))
    play_to_final(game)
    game.finish(swap=False)
    delta = allatu_stats(game, when=WHEN)
    assert delta.add["allatu_final_gordo"] == 1 and delta.add["allatu_keeps"] == 1
    assert delta.peak["allatu_mult_max"] == 1_000
    assert delta.peak["allatu_win_max"] == 900


def test_rechazar_mucho_y_acabar_perdiendo_es_allatu() -> None:
    game = rigged(own_value=Fraction(0), other_value=Fraction(10), gordo=10)
    play_to_final(game)
    # Con estos premios ninguna oferta llega a ×5: se fuerza una de ×6.
    game.offers[0].amount = 600
    game.finish(swap=False)
    assert allatu_stats(game, when=WHEN).add["allatu_greedy"] == 1


def test_la_racha_de_dias_y_las_fechas() -> None:
    game = rigged(own_value=Fraction(0), other_value=Fraction(1))
    play_to_final(game)
    game.finish(swap=False)
    friday13 = datetime(2026, 11, 13, 3, 0)
    delta = allatu_stats(game, when=friday13, day_start=740_000, day_last=740_006)
    assert delta.peak["allatu_streak"] == 7
    assert delta.add["allatu_friday13"] == 1 and delta.add["allatu_night"] == 1


def test_el_gordo_de_la_ruleta_cuenta_como_maximo() -> None:
    game = AllatuGame.new(100, Table.INFIERNO, random.Random(1))
    game.gordo = 1_000
    game.choose(0)
    game.open(1)
    game.settle_now()
    assert allatu_stats(game, when=WHEN).peak["allatu_gordo_max"] == 1_000


def with_values(values: list[Fraction], *, gordo: int = 10) -> AllatuGame:
    """Una partida Normal con los premios puestos a mano y tu caja, la 0."""
    game = AllatuGame(
        stake=100,
        table=Table.NORMAL,
        gordo=gordo,
        values=tuple(values),
        haggle_luck=(False,) * len(ROUNDS),
    )
    game.choose(0)
    return game


def test_aceptar_el_pesame_por_mas_de_lo_apostado() -> None:
    game = with_values([Fraction(2), Fraction(0), Fraction(10)] + [Fraction(2)] * 19)
    game.open(1)
    game.open(2)
    offer = game.offer
    assert offer is not None and offer.condolence and offer.amount == 150
    game.deal()
    delta = allatu_stats(game, when=WHEN)
    add = delta.add
    assert add["allatu_condolences"] == 1 and add["allatu_condolence_deals"] == 1
    assert add["allatu_condolence_rich"] == 1 and add["allatu_first_deal"] == 1
    assert add["allatu_comebacks"] == 1
    assert delta.peak["allatu_comeback_max"] == 150


def test_rechazar_el_pesame_y_ganar_sin_el_gordo() -> None:
    game = with_values([Fraction(3), Fraction(10)] + [Fraction(0)] * 20)
    game.open(1)
    assert game.offer is not None and game.offer.condolence
    play_to_final(game)
    game.finish(swap=False)
    delta = allatu_stats(game, when=WHEN)
    add = delta.add
    assert add["allatu_condolences"] == 1 and "allatu_condolence_deals" not in add
    # Las cinco de ronda y el pésame, todas rechazadas.
    assert add["allatu_all_no"] == 1 and add["allatu_no_deals"] == len(ROUNDS) + 1
    assert add["allatu_comebacks"] == 1
    assert delta.peak["allatu_comeback_max"] == 300


def test_aceptar_la_oferta_de_ronda_tras_el_pesame_no_es_la_primera() -> None:
    game = with_values([Fraction(1), Fraction(10)] + [Fraction(1)] * 20)
    game.open(1)
    game.no_deal()
    while game.phase is Phase.OPEN:
        game.open(game.openable[0])
    game.deal()
    add = allatu_stats(game, when=WHEN).add
    assert "allatu_first_deal" not in add and "allatu_condolence_deals" not in add


def test_perder_con_el_gordo_fuera_no_es_remontada() -> None:
    game = with_values([Fraction(0), Fraction(10)] + [Fraction(0)] * 20)
    game.open(1)
    game.deal()
    delta = allatu_stats(game, when=WHEN)
    assert "allatu_comebacks" not in delta.add
    assert "allatu_comeback_max" not in delta.peak
