"""Reglas del bacará: valores, tercera carta, pagos, zapato y carretera grande."""

from __future__ import annotations

import pytest

from bot.services import bacara as bc
from bot.services.bacara import Coup, Result, Shoe, Side
from bot.services.blackjack import Card


def c(rank: int, suit: int = 0) -> Card:
    return Card(rank, suit)


def scripted(*ranks: int):
    """Un `draw` que saca las cartas en el orden dado."""
    cards = [c(r) for r in ranks]
    return lambda: cards.pop(0)


def test_valores_de_las_cartas_y_de_la_mano() -> None:
    assert [bc.card_points(c(r)) for r in range(1, 14)] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 0, 0, 0]
    assert bc.hand_points([c(7), c(8)]) == 5
    assert bc.hand_points([c(13), c(12), c(10)]) == 0
    assert bc.hand_points([c(9), c(9), c(9)]) == 7


# Tabla de la Banca de los reglamentos: fila = total de la Banca (0-7), columna =
# tercera carta del Jugador (0-9). «P» pide, «.» se planta.
BANKER_TABLE = {
    0: "PPPPPPPPPP",
    1: "PPPPPPPPPP",
    2: "PPPPPPPPPP",
    3: "PPPPPPPP.P",
    4: "..PPPPPP..",
    5: "....PPPP..",
    6: "......PP..",
    7: "..........",
}


@pytest.mark.parametrize("total", range(8))
def test_tabla_de_la_tercera_carta_de_la_banca(total: int) -> None:
    for third in range(10):
        card = c(third or 10)
        assert bc.banker_draws(total, card) is (BANKER_TABLE[total][third] == "P"), (total, third)


def test_banca_sin_tercera_del_jugador_pide_con_cinco_o_menos() -> None:
    assert [bc.banker_draws(t, None) for t in range(8)] == [True] * 6 + [False] * 2


def test_jugador_pide_con_cinco_o_menos() -> None:
    assert [bc.player_draws(t) for t in range(8)] == [True] * 6 + [False] * 2


def test_reparto_alterno_y_natural_planta_a_los_dos() -> None:
    # P: 4+4 = 8 (natural); B: 1+2 = 3. Nadie pide aunque la Banca tenga 3.
    player, banker = bc.deal(scripted(4, 1, 4, 2, 9, 9))
    assert [x.rank for x in player] == [4, 4]
    assert [x.rank for x in banker] == [1, 2]


def test_reparto_con_tercera_carta_para_los_dos() -> None:
    # P: 2+3 = 5, pide un 4 → 9. B: 10+3 = 3, con un 4 del Jugador pide.
    player, banker = bc.deal(scripted(2, 10, 3, 3, 4, 6))
    assert [x.rank for x in player] == [2, 3, 4]
    assert [x.rank for x in banker] == [10, 3, 6]
    coup = Coup(player, banker)
    assert (coup.player_total, coup.banker_total, coup.winner) == (9, 9, Side.TIE)


def test_banca_con_tres_no_pide_si_el_jugador_saca_un_ocho() -> None:
    player, banker = bc.deal(scripted(1, 1, 1, 2, 8, 9))
    assert len(player) == 3 and len(banker) == 2


def test_pagos() -> None:
    assert bc.payout(Side.PLAYER, 100, Side.PLAYER) == 200
    assert bc.payout(Side.PLAYER, 100, Side.BANKER) == 0
    assert bc.payout(Side.PLAYER, 100, Side.TIE) == 100
    assert bc.payout(Side.BANKER, 100, Side.BANKER) == 195
    assert bc.payout(Side.BANKER, 100, Side.TIE) == 100
    assert bc.payout(Side.TIE, 100, Side.TIE) == 900
    assert bc.payout(Side.TIE, 100, Side.BANKER) == 0


def test_comision_al_y_mas_cercano_y_los_medios_para_la_casa() -> None:
    assert bc.commission(bc.BANKER_MIN_STAKE) == 1
    assert bc.commission(10) == 1
    assert bc.commission(30) == 2
    assert bc.commission(21) == 1
    assert bc.commission(1_000) == 50
    assert bc.payout(Side.BANKER, 30, Side.BANKER) == 58


def test_zapato_quema_y_se_acaba_en_la_carta_de_corte() -> None:
    def no_shuffle(cards: list[Card]) -> None:
        cards.sort(
            key=lambda x: (x.rank, x.suit)
        )  # la última carta (la primera en salir) es un rey

    shoe = Shoe.open(no_shuffle)
    # La primera es un rey: vale 0, así que quema 10 más.
    assert len(shoe.burned) == 11
    assert shoe.left == 52 * bc.DECKS - 11
    coups = []
    while not shoe.finished:
        coups.append(shoe.deal())
    assert coups[-1].last and not any(x.last for x in coups[:-1])
    assert [x.number for x in coups] == list(range(1, len(coups) + 1))
    assert coups[-1].left == shoe.left <= bc.CUT_FROM_END
    with pytest.raises(ValueError):
        shoe.deal()


def test_un_zapato_da_para_unas_ochenta_manos() -> None:
    shoe = Shoe.open()
    while not shoe.finished:
        shoe.deal()
    assert 70 <= shoe.dealt <= 95


def r(*letters: str) -> list[Result]:
    side = {"B": Side.BANKER, "J": Side.PLAYER, "E": Side.TIE}
    return [Result(side[x], 0, 0) for x in letters]


def cells(results: list[Result]) -> list[tuple[int, int, str, int]]:
    return [(x.col, x.row, x.side.letter, x.ties) for x in bc.big_road(results)]


def test_carretera_rachas_en_columnas_y_empates_en_la_casilla_anterior() -> None:
    assert cells(r("B", "B", "J", "E", "E", "B")) == [
        (0, 0, "B", 0),
        (0, 1, "B", 0),
        (1, 0, "J", 2),
        (2, 0, "B", 0),
    ]


def test_carretera_empates_del_principio_van_a_la_primera_casilla() -> None:
    assert cells(r("E")) == [(0, 0, "E", 1)]
    assert cells(r("E", "E", "J")) == [(0, 0, "J", 2)]


def test_carretera_cola_del_dragon() -> None:
    road = cells(r(*"BBBBBBBB", "J"))
    assert road[:6] == [(0, i, "B", 0) for i in range(6)]
    # Al llegar al fondo, sigue a la derecha por la última fila.
    assert road[6:8] == [(1, 5, "B", 0), (2, 5, "B", 0)]
    # La racha siguiente empieza en la columna de al lado de donde empezó esta.
    assert road[8] == (1, 0, "J", 0)


def test_carretera_cola_que_choca_con_otra() -> None:
    # Una racha de 6 en la columna 0 y una de 6 en la 1: la segunda no cabe al
    # fondo porque la cola de la primera ocupa (1, 5), así que gira antes.
    road = cells(r(*"BBBBBBB", *"JJJJJJ"))
    assert (1, 5, "B", 0) in road
    jugador = [x for x in road if x[2] == "J"]
    assert jugador[:5] == [(1, i, "J", 0) for i in range(5)]
    assert jugador[5] == (2, 4, "J", 0)


def test_recuento_y_rachas_del_zapato() -> None:
    stats = bc.road_stats(r("B", "B", "E", "B", "J", "J"))
    assert (stats.banker, stats.player, stats.tie, stats.hands) == (3, 2, 1, 6)
    assert (stats.streak, stats.streak_side) == (2, Side.PLAYER)
    assert (stats.longest, stats.longest_side) == (3, Side.BANKER)


def test_ping_pong_al_final_del_zapato() -> None:
    assert bc.chop_run(r("B", "B", "J", "B", "E", "J")) == 4
    assert bc.chop_run(r("B")) == 1
    assert bc.chop_run([]) == 0


def test_propiedades_de_la_mano() -> None:
    coup = Coup((c(5), c(5), c(9)), (c(3), c(13)))
    assert coup.player_pair and not coup.banker_pair
    assert coup.player_total == 9 and coup.banker_total == 3
    assert coup.winner is Side.PLAYER
    assert coup.comeback(Side.PLAYER)  # iba 0 contra 3 y remonta
    assert not coup.natural
    assert coup.margin == 6 and coup.cards == 5
    natural = Coup((c(4), c(5)), (c(1), c(1)))
    assert natural.natural and natural.natural_of(Side.PLAYER) == 9
    assert natural.natural_of(Side.BANKER) is None
