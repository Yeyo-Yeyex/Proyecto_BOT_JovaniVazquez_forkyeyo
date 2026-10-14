"""Pruebas de bot.services.poker (reglas del Casino Hold'em) y de `poker_stats` (logros).

Las cartas se escriben como texto (`"Ah"` es el as de corazones) para leer las
manos de un vistazo. Los palos van como en `blackjack.SUITS`: s ♠, h ♥, d ♦, c ♣.
"""

from __future__ import annotations

import random
from datetime import datetime

import pytest

from bot.services.achievements import poker_stats
from bot.services.blackjack import Card
from bot.services.levels import TIMEZONE
from bot.services.poker import (
    CALL_FACTOR,
    Deal,
    Outcome,
    PokerError,
    PokerHand,
    Rank,
    deal,
    evaluate,
    payout,
    qualifies,
    showdown,
)
from bot.services.poker_render import H, PokerRenderer, W, deal_states, showdown_states

RANKS = {"A": 1, "T": 10, "J": 11, "Q": 12, "K": 13, **{str(n): n for n in range(2, 10)}}
SUITS = {"s": 0, "h": 1, "d": 2, "c": 3}
NOON = datetime(2026, 3, 10, 18, tzinfo=TIMEZONE)


def cards(text: str) -> tuple[Card, ...]:
    return tuple(Card(RANKS[c[0]], SUITS[c[1]]) for c in text.split())


def table(player: str, dealer: str, board: str) -> Deal:
    p, d, b = cards(player), cards(dealer), cards(board)
    return Deal(player=(p[0], p[1]), dealer=(d[0], d[1]), board=(b[0], b[1], b[2], b[3], b[4]))


def played(cards_: Deal, *, called: bool, ante: int = 100) -> PokerHand:
    hand = PokerHand.new(ante, random.Random(1), cards=cards_)
    if called:
        hand.call()
    else:
        hand.fold()
    return hand


# -- Valor de las manos ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("hand", "rank", "label"),
    [
        ("As Ks Qs Js Ts 2d 3c", Rank.ROYAL_FLUSH, "Escalera real"),
        ("9h 8h 7h 6h 5h Ad Ac", Rank.STRAIGHT_FLUSH, "Escalera de color al 9"),
        ("7s 7h 7d 7c Ks 2d 3c", Rank.QUADS, "Póker de sietes"),
        ("Ks Kh Kd 2c 2s 9d 3c", Rank.FULL_HOUSE, "Full de reyes y doses"),
        ("As 9s 7s 4s 2s Kd Qc", Rank.FLUSH, "Color al A"),
        ("As 2d 3c 4h 5s Kd 9c", Rank.STRAIGHT, "Escalera al 5"),
        ("Qs Qh Qd 9c 4s 2d 7c", Rank.TRIPS, "Trío de damas"),
        ("Js Jh 4d 4c As 2d 7c", Rank.TWO_PAIR, "Doble pareja de jotas y cuatros"),
        ("Ts Th 4d 8c As 2d 7c", Rank.PAIR, "Pareja de dieces"),
        ("As Jh 4d 8c 9s 2d 7c", Rank.HIGH_CARD, "Carta alta: A"),
    ],
)
def test_cada_categoria_se_reconoce_con_siete_cartas(hand: str, rank: Rank, label: str) -> None:
    value = evaluate(cards(hand))
    assert value.rank is rank
    assert value.label == label


def test_la_rueda_pierde_contra_la_escalera_al_seis() -> None:
    assert evaluate(cards("As 2d 3c 4h 5s")) < evaluate(cards("2d 3c 4h 5s 6s"))


def test_el_kicker_desempata_y_las_mismas_alturas_empatan() -> None:
    assert evaluate(cards("As Ah Kd 7c 2s")) > evaluate(cards("Ad Ac Qd 7h 2h"))
    assert evaluate(cards("As Ah Kd 7c 2s")) == evaluate(cards("Ad Ac Kh 7h 2h"))


def test_hacen_falta_cinco_cartas() -> None:
    with pytest.raises(ValueError):
        evaluate(cards("As Ah Kd 7c"))


def test_la_banca_juega_con_pareja_de_cuatros_o_mejor() -> None:
    assert not qualifies(evaluate(cards("As Kh 9d 7c 2s")))
    assert not qualifies(evaluate(cards("3s 3h 9d 7c As")))
    assert qualifies(evaluate(cards("4s 4h 9d 7c As")))
    assert qualifies(evaluate(cards("2s 2h 9d 9c As")))


# -- Resultados y pagos ----------------------------------------------------------------

#: Tablero sin nada: ni color, ni escalera, ni parejas.
DRY = "Kd 9c 6h 3s 2c"


def test_la_banca_no_juega_el_ante_cobra_y_la_de_ir_se_devuelve() -> None:
    hand = table("Ah Qs", "Jd 8s", DRY)
    assert showdown(hand) is Outcome.NO_QUALIFY
    # Ante 1 a 1 (carta alta) y la de ir de vuelta.
    assert payout(hand, 100, called=True) == 2 * 100 + CALL_FACTOR * 100


def test_ganar_paga_el_ante_por_la_tabla_y_la_de_ir_1_a_1() -> None:
    hand = table("Kh Ks", "9d 9s", DRY)  # trío de reyes contra trío de nueves
    assert showdown(hand) is Outcome.WIN
    assert payout(hand, 100, called=True) == 2 * 100 + 2 * CALL_FACTOR * 100


def test_un_full_cobra_el_ante_3_a_1() -> None:
    hand = table("Kh Ks", "9d 4s", "Kd 9c 9h 3s 2c")  # full de reyes contra full de nueves
    assert showdown(hand) is Outcome.WIN
    assert payout(hand, 100, called=True) == 4 * 100 + 2 * CALL_FACTOR * 100


def test_empatar_devuelve_todo_y_perder_no_devuelve_nada() -> None:
    assert showdown(table("4h 5s", "4d 5c", "Ad Kc Kh Qs Qc")) is Outcome.TIE
    assert payout(table("4h 5s", "4d 5c", "Ad Kc Kh Qs Qc"), 100, called=True) == 300
    lose = table("7h 8s", "Kc Ks", DRY)
    assert showdown(lose) is Outcome.LOSE
    assert payout(lose, 100, called=True) == 0


def test_retirarse_pierde_el_ante_y_se_ve_lo_que_habria_pasado() -> None:
    hand = played(table("Ah Qs", "Jd 8s", DRY), called=False)
    assert hand.outcome is Outcome.FOLD
    assert (hand.staked, hand.payout, hand.net) == (100, 0, -100)
    assert hand.would_have is Outcome.NO_QUALIFY


def test_ir_cuesta_el_doble_y_no_se_decide_dos_veces() -> None:
    hand = played(table("Kh Ks", "9d 9s", DRY), called=True)
    assert hand.staked == 100 * (1 + CALL_FACTOR)
    assert hand.net == 2 * 100 + 2 * CALL_FACTOR * 100 - hand.staked
    with pytest.raises(PokerError):
        hand.fold()


def test_el_ante_debe_ser_positivo() -> None:
    with pytest.raises(ValueError):
        PokerHand.new(0, random.Random(1))


def test_el_reparto_no_repite_cartas() -> None:
    for seed in range(50):
        d = deal(random.Random(seed))
        dealt = (*d.player, *d.dealer, *d.board)
        assert len(set(dealt)) == 9


# -- Logros: poker_stats ---------------------------------------------------------------


def test_ir_y_ganar_con_full_suma_la_mano_ligada_y_el_maximo() -> None:
    hand = played(table("Kh Ks", "9d 4s", "Kd 9c 9h 3s 2c"), called=True)
    delta = poker_stats(hand, when=NOON)
    assert delta.add["poker_games"] == delta.add["poker_calls"] == 1
    assert delta.add["poker_wins"] == 1
    assert delta.add["poker_made_full_house"] == 1
    assert delta.peak["poker_win_max"] == hand.net
    assert "poker_folds" not in delta.add


def test_retirarse_con_una_mano_ganadora_es_de_cobardes() -> None:
    hand = played(table("Kh Ks", "9d 4s", "Kd 9c 9h 3s 2c"), called=False)
    delta = poker_stats(hand, when=NOON)
    assert delta.add["poker_folds"] == 1
    assert delta.add["poker_fold_would_win"] == 1
    assert delta.add["poker_fold_monster"] == 1
    assert not any(k.startswith("poker_made_") for k in delta.add), "retirado no liga nada"


def test_perder_con_color_es_un_bad_beat_y_por_la_misma_jugada_es_por_el_kicker() -> None:
    beat = played(table("Ah 2h", "Kc Kd", "Kh 9h 6h Ks 3c"), called=True)  # color contra póker
    delta = poker_stats(beat, when=NOON)
    assert delta.add["poker_losses"] == 1
    assert delta.add["poker_bad_beat"] == 1
    assert delta.add["poker_dealer_monster"] == 1
    kicker = played(table("Ah 7s", "Ad Qc", "As 9c 6h 4s 2c"), called=True)  # ases con kicker
    assert poker_stats(kicker, when=NOON).add["poker_kicker"] == 1


def test_ir_con_carta_alta_y_que_la_banca_no_juegue_es_un_farol() -> None:
    hand = played(table("Ah Qs", "Jd 8s", DRY), called=True)
    delta = poker_stats(hand, when=NOON)
    assert delta.add["poker_bluff"] == 1
    assert delta.add["poker_no_qualify"] == 1
    assert delta.add["poker_air_call_wins"] == 1


def test_las_fechas_del_poker() -> None:
    hand = played(table("7h 8s", "Kc Ks", DRY), called=True)
    friday13 = datetime(2026, 2, 13, 3, tzinfo=TIMEZONE)
    delta = poker_stats(hand, when=friday13)
    assert delta.add["poker_friday13"] == delta.add["poker_night"] == 1
    reyes = poker_stats(hand, when=datetime(2027, 1, 6, 12, tzinfo=TIMEZONE))
    assert reyes.add["poker_reyes"] == 1


# -- Dibujo ----------------------------------------------------------------------------


def test_el_reparto_acaba_en_la_mesa_de_decidir_con_lo_que_lleva() -> None:
    hand = table("Kh Ks", "9d 4s", DRY)
    states = deal_states(hand, seed=1)
    assert states[-1]["player"]["text"] == "TRÍO"  # KK con el Kd del flop


def test_los_dos_finales_cambian_el_cartel() -> None:
    hand = table("Kh Ks", "9d 4s", DRY)
    call = showdown_states(hand, called=True, seed=1)[-1]["banner"]
    fold = showdown_states(hand, called=False, seed=1)[-1]["banner"]
    assert call["title"] != fold["title"]


def test_pillow_pinta_la_mesa_a_su_tamano() -> None:
    from io import BytesIO

    from PIL import Image

    media = PokerRenderer().showdown(table("Kh Ks", "9d 4s", DRY))
    for png in (media.call.png, media.fold.png):
        assert Image.open(BytesIO(png)).size == (W, H)
