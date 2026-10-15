"""Pruebas de `war_stats` y del catálogo de logros de la Guerra."""

from __future__ import annotations

from bot.services.achievements import (
    AVAILABLE,
    BJ_HIGH_STAKE,
    BY_ID,
    CATEGORY_BY_KEY,
    Rarity,
    war_stats,
)
from bot.services.blackjack import Card
from bot.services.war import WarGame


def c(rank: int) -> Card:
    return Card(rank, 0)


def hand(*cards: Card, stake: int = 100, side: int = 0, choice: str | None = None) -> WarGame:
    """Mano con el zapato preparado; `choice` es `war` o `surrender` si hay empate."""
    war = WarGame(stake=stake, side=side, shoe=[c(7)] * 10 + list(reversed(cards)))
    war.deal()
    if choice == "war":
        war.go_to_war()
    elif choice == "surrender":
        war.surrender()
    return war


def test_una_mano_ganada_cuenta_mano_victoria_y_rango() -> None:
    delta = war_stats(hand(c(1), c(4)), win_run=3)
    assert delta.add["war_hands"] == 1 and delta.add["war_wins"] == 1
    assert delta.add["war_win_rank_14"] == 1
    assert delta.peak["war_win_streak_max"] == 3 and delta.peak["war_win_max"] == 100
    assert not {"war_ties", "war_wars", "war_surrenders"} & set(delta.add)


def test_perder_con_un_rey_contra_un_as_es_el_secreto_del_rey() -> None:
    delta = war_stats(hand(c(13), c(1)), lose_run=2)
    assert delta.add["war_king_lost"] == 1
    assert delta.peak["war_lose_streak_max"] == 2
    assert "war_wins" not in delta.add and "war_win_max" not in delta.peak
    assert "war_king_lost" not in war_stats(hand(c(12), c(1))).add


def test_el_empate_cuenta_el_rango_y_la_racha_de_empates() -> None:
    delta = war_stats(hand(c(2), c(2), choice="surrender"), tie_run=2)
    assert delta.add["war_ties"] == 1 and delta.add["war_tie_rank_2"] == 1
    assert delta.add["war_tie_two"] == 1 and delta.peak["war_tie_streak_max"] == 2
    assert delta.add["war_surrenders"] == 1


def test_rendirse_con_ases_y_con_una_ficha_de_uno() -> None:
    aces = war_stats(hand(c(1), c(1), choice="surrender"))
    assert aces.add["war_surrender_ace"] == 1 and aces.add["war_ace_ace"] == 1
    one = war_stats(hand(c(5), c(5), stake=1, choice="surrender"))
    assert one.add["war_surrender_one"] == 1
    assert "war_surrender_one" not in aces.add


def test_la_guerra_ganada_empatada_y_perdida() -> None:
    won = war_stats(hand(c(9), c(9), c(2), c(3), c(4), c(12), c(5), choice="war"))
    assert won.add["war_wars"] == 1 and won.add["war_war_wins"] == 1 and won.add["war_wins"] == 1
    assert "war_war_ties" not in won.add
    tied = war_stats(hand(c(9), c(9), c(2), c(3), c(4), c(8), c(8), choice="war"))
    assert tied.add["war_war_wins"] == 1 and tied.add["war_war_ties"] == 1
    lost = war_stats(hand(c(9), c(9), c(2), c(3), c(4), c(5), c(12), choice="war"))
    assert lost.add["war_war_losses"] == 1
    assert "war_wins" not in lost.add and "war_war_wins" not in lost.add


def test_ganar_con_un_dos_solo_se_puede_empatando_a_doses_en_la_guerra() -> None:
    two = war_stats(hand(c(9), c(9), c(2), c(3), c(4), c(2), c(2), choice="war"))
    assert two.add["war_win_two"] == 1
    other = war_stats(hand(c(9), c(9), c(2), c(3), c(4), c(3), c(3), choice="war"))
    assert "war_win_two" not in other.add


def test_la_lateral_cuenta_apuestas_aciertos_y_la_triple_corona() -> None:
    miss = war_stats(hand(c(10), c(4), side=100))
    assert miss.add["war_side_bets"] == 1 and "war_side_hits" not in miss.add
    hit = war_stats(hand(c(9), c(9), side=100, choice="surrender"))
    assert hit.add["war_side_hits"] == 1 and "war_triple" not in hit.add
    triple = war_stats(hand(c(9), c(9), c(2), c(3), c(4), c(12), c(5), side=100, choice="war"))
    assert triple.add["war_side_hits"] == 1 and triple.add["war_triple"] == 1
    assert "war_side_bets" not in war_stats(hand(c(10), c(4))).add


def test_los_cobros_exactos_secretos() -> None:
    # Ganar a la primera devuelve el doble; ganar la guerra, el triple.
    assert war_stats(hand(c(10), c(4), stake=333)).add["war_cash_666"] == 1
    war = hand(c(9), c(9), c(2), c(3), c(4), c(12), c(5), stake=259, choice="war")
    assert war_stats(war).add["war_cash_777"] == 1
    assert (
        war_stats(hand(c(9), c(9), c(2), c(3), c(4), c(12), c(5), stake=23, choice="war")).add[
            "war_cash_69"
        ]
        == 1
    )
    assert not {"war_cash_777", "war_cash_666", "war_cash_69"} & set(
        war_stats(hand(c(10), c(4))).add
    )


def test_una_ficha_alta_cuenta_como_presupuesto_de_defensa() -> None:
    assert war_stats(hand(c(10), c(4), stake=BJ_HIGH_STAKE)).add["war_max_stake"] == 1
    assert "war_max_stake" not in war_stats(hand(c(10), c(4), stake=BJ_HIGH_STAKE - 1)).add


def test_la_coleccion_del_mazo_pide_ganar_con_cada_rango_del_3_al_as() -> None:
    wins = BY_ID["war_all_wins"].conditions
    assert {stat for stat, _ in wins} == {f"war_win_rank_{n}" for n in range(3, 15)}
    ties = BY_ID["war_all_ties"].conditions
    assert {stat for stat, _ in ties} == {f"war_tie_rank_{n}" for n in range(2, 15)}


def test_la_categoria_y_el_reparto_de_logros() -> None:
    assert CATEGORY_BY_KEY["war"].group == "casino_group"
    war = [a for a in AVAILABLE if a.category == "war"]
    assert len(war) > 40
    secrets = [a for a in war if a.secret]
    assert len(war) / 8 <= len(secrets) <= len(war) / 4  # más o menos uno de cada seis
    assert {a.rarity for a in war} == set(Rarity)


def test_lo_que_se_decide_a_proposito_no_pasa_de_raro() -> None:
    """Rendirse, la lateral y ficha de 1 son decisiones: Común o Raro aunque cuesten."""
    for achievement_id in ("war_surr_1", "war_side_1", "war_surr_one", "war_war_1"):
        assert BY_ID[achievement_id].rarity in (Rarity.COMMON, Rarity.RARE)
