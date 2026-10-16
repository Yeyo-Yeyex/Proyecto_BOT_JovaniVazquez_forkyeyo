"""Pruebas de bot.services.allatu: premios, rondas, ofertas, regateo y final."""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from bot.services.allatu import (
    BOXES,
    HAGGLE_RAISE,
    OFFER_FACTORS,
    ROUND_ENDS,
    ROUNDS,
    RTP,
    TABLES,
    AllatuError,
    AllatuGame,
    Ending,
    Phase,
    Table,
    format_chance,
    format_multiplier,
    offer_mood,
    parse_table,
    tier,
)


def new_game(table: Table = Table.NORMAL, *, stake: int = 100, seed: int = 1) -> AllatuGame:
    return AllatuGame.new(stake, table, random.Random(seed))


def to_offer(game: AllatuGame) -> None:
    """Abre cajas (las primeras que se puedan) hasta que llama la banca."""
    while game.phase is Phase.OPEN:
        game.open(game.openable[0])


def to_final(game: AllatuGame) -> None:
    """Dice «no trato» a todo hasta el final a dos cajas."""
    while game.phase is not Phase.FINAL:
        if game.phase is Phase.OPEN:
            to_offer(game)
        else:
            game.no_deal()


@pytest.mark.parametrize("table", TABLES)
def test_cada_tabla_paga_de_media_el_99(table: Table) -> None:
    assert len(table.base) == BOXES - 1
    assert (sum(table.base) + table.gordo_mean) / BOXES == RTP


@pytest.mark.parametrize("table", TABLES)
def test_el_gordo_siempre_es_el_premio_mas_alto(table: Table) -> None:
    lowest_gordo = min(m for m, _ in table.gordo)
    assert max(table.base) < lowest_gordo
    assert sum(w for _, w in table.gordo) == 1_000


def test_el_gordo_se_sortea_con_sus_pesos() -> None:
    class Roll(random.Random):
        def __init__(self, value: int) -> None:
            super().__init__(0)
            self.value = value

        def randrange(self, *args: object, **kwargs: object) -> int:  # type: ignore[override]
            return self.value

    table = Table.INFIERNO
    edges = []
    acc = 0
    for mult, weight in table.gordo:
        edges.append((acc, mult))
        edges.append((acc + weight - 1, mult))
        acc += weight
    for roll, mult in edges:
        assert table.draw_gordo(Roll(roll)) == mult


def test_una_partida_reparte_los_premios_de_la_tabla_y_el_gordo() -> None:
    game = new_game(Table.PICANTE)
    assert sorted(game.values) == sorted([*Table.PICANTE.base, Fraction(game.gordo)])
    assert game.phase is Phase.PICK and game.gordo_alive
    assert len(game.haggle_luck) == len(ROUNDS)


def test_la_media_de_lo_que_queda_no_cambia_de_media_al_abrir_una_caja() -> None:
    """Es una martingala: por eso ninguna forma de aceptar ofertas pasa del 99 %."""
    game = new_game(Table.INFIERNO, seed=5)
    game.choose(3)
    for _ in range(4):
        game.open(game.openable[0])
    before = game.mean
    after = []
    # El jugador no sabe qué hay en su caja: cualquier caja cerrada podría ser la
    # suya y se abre cualquiera de las otras. De media, la media no se mueve.
    for own in game.closed:
        for box in game.closed:
            if box == own:
                continue
            trial = AllatuGame(
                stake=game.stake,
                table=game.table,
                gordo=game.gordo,
                values=game.values,
                haggle_luck=game.haggle_luck,
                own=own,
                opened=[*game.opened],
                phase=Phase.OPEN,
            )
            trial.open(box)
            after.append(trial.mean)
    assert sum(after, Fraction(0)) / len(after) == before


def test_la_oferta_no_mira_lo_que_hay_en_tu_caja() -> None:
    offers = set()
    for own in (0, 21):
        game = new_game(seed=9)
        game.choose(own)
        for box in range(1, 21):
            if game.phase is not Phase.OPEN:
                break
            game.open(box)
        assert game.phase is Phase.OFFER
        offers.add(game.offers[-1].amount)
    assert len(offers) == 1


def test_las_rondas_llaman_a_la_banca_tras_6_5_4_3_y_2_cajas() -> None:
    game = new_game()
    game.choose(0)
    calls = []
    while game.phase is not Phase.FINAL:
        if game.phase is Phase.OPEN:
            game.open(game.openable[0])
        else:
            calls.append(len(game.opened))
            game.no_deal()
    assert tuple(calls) == ROUND_ENDS
    assert [o.factor for o in game.offers] == list(OFFER_FACTORS)
    assert len(game.closed) == 2


def test_la_oferta_es_la_media_por_el_factor_hacia_abajo() -> None:
    game = new_game(stake=333)
    game.choose(5)
    to_offer(game)
    offer = game.offer
    assert offer is not None
    assert offer.amount == int(333 * OFFER_FACTORS[0] * game.mean)
    assert offer.amount <= 333 * game.mean


def test_trato_cobra_la_oferta_y_acaba() -> None:
    game = new_game()
    game.choose(0)
    to_offer(game)
    offer = game.offer
    assert offer is not None
    assert game.deal() == offer.amount
    assert game.phase is Phase.DONE and game.ending is Ending.DEAL
    assert offer.accepted and game.payout == offer.amount


def test_regatear_con_suerte_sube_un_10_y_ya_no_se_puede_otra_vez() -> None:
    game = new_game()
    game.haggle_luck = (True,) * len(ROUNDS)
    game.choose(0)
    to_offer(game)
    first = game.offer
    assert first is not None and game.can_haggle and game.haggle_result()
    assert game.haggle() is True
    second = game.offer
    assert second is not None and second.haggled
    assert second.factor == first.factor + HAGGLE_RAISE
    assert second.amount >= first.amount
    assert not game.can_haggle
    with pytest.raises(AllatuError):
        game.haggle()
    game.no_deal()
    to_offer(game)
    assert not game.can_haggle


def test_regatear_sin_suerte_cuelga_y_sigue_sin_oferta() -> None:
    game = new_game()
    game.haggle_luck = (False,) * len(ROUNDS)
    game.choose(0)
    to_offer(game)
    assert game.haggle() is False
    assert game.phase is Phase.OPEN
    assert game.offers[-1].hung_up


def test_la_ultima_oferta_ya_no_se_regatea() -> None:
    game = new_game()
    game.choose(0)
    while len(game.offers) < len(ROUNDS):
        if game.phase is Phase.OPEN:
            to_offer(game)
        else:
            game.no_deal()
    assert game.offer is not None and game.offer.factor == 1
    assert not game.can_haggle


@pytest.mark.parametrize("swap", [False, True])
def test_el_final_cobra_la_caja_que_te_quedas(swap: bool) -> None:
    game = new_game(seed=3)
    game.choose(7)
    to_final(game)
    other = game.other
    assert other is not None
    mine = other if swap else 7
    expected = int(game.stake * game.values[mine])
    assert game.finish(swap=swap) == expected
    assert game.own == mine
    assert game.ending is (Ending.SWAPPED if swap else Ending.KEPT)
    assert len(game.opened) == BOXES - 1


def test_no_se_abre_tu_caja_ni_una_abierta_ni_antes_de_elegir() -> None:
    game = new_game()
    with pytest.raises(AllatuError):
        game.open(0)
    game.choose(4)
    with pytest.raises(AllatuError):
        game.choose(5)
    with pytest.raises(AllatuError):
        game.open(4)
    game.open(1)
    with pytest.raises(AllatuError):
        game.open(1)
    with pytest.raises(AllatuError):
        game.deal()


def test_el_dado_nunca_apunta_a_tu_caja_ni_a_una_abierta() -> None:
    rng = random.Random(4)
    game = new_game()
    game.dice = 6
    game.choose(6)
    assert game.dice != 6
    for _ in range(5):
        game.open(game.dice)
        game.reroll_dice(rng)
        assert game.dice in game.openable


def test_cerrar_a_la_fuerza_acepta_la_oferta_o_la_que_tocaria() -> None:
    game = new_game()
    game.choose(0)
    game.open(1)
    payout = game.settle_now()
    assert game.ending is Ending.DEAL
    assert payout == int(game.stake * OFFER_FACTORS[0] * game.offers[-1].mean)
    final = new_game(seed=2)
    final.choose(0)
    to_final(final)
    assert final.settle_now() == int(final.stake * final.mean)


def test_formatos() -> None:
    assert format_multiplier(Fraction(3, 4)) == "×0,75"
    assert format_multiplier(Fraction(1, 2)) == "×0,5"
    assert format_multiplier(0) == "×0"
    assert format_multiplier(1000) == "×1.000"
    assert format_chance(Fraction(3, 1000)) == "0,3 %"
    assert format_chance(Fraction(76, 100)) == "76 %"
    assert parse_table("P") is Table.PICANTE
    with pytest.raises(ValueError):
        parse_table("fuego")
    assert tier(Fraction(250), 250) == "gordo"
    assert tier(Fraction(3, 2), 250) == "rojo"
    assert tier(Fraction(1, 2), 250) == "azul"
    assert tier(Fraction(0), 250) == "cero"


def test_el_humor_de_la_oferta_depende_de_la_apuesta() -> None:
    game = new_game(stake=100)
    game.choose(0)
    to_offer(game)
    offer = game.offer
    assert offer is not None
    offer.amount = 40
    assert offer_mood(game) == "rata"
    offer.amount = 100
    assert offer_mood(game) == "normal"
    offer.amount = 200
    assert offer_mood(game) == "generosa"
