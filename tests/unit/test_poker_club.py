"""Pruebas del club del póker: mesa compartida, 💎 Bote, rango, retos, racha y su dinero.

Cubre la lógica pura (`bot.services.poker` y `bot.services.poker_club`), el
repositorio del club (`bot.repositories.poker`) y el bote en la economía
(`EconomyService.pay_poker`) sobre un SQLite temporal.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import POKER_POT_ACCOUNT_ID, EconomyRepository
from bot.repositories.poker import PokerRepository
from bot.services import poker_club as club
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.poker import (
    BONUS_SHARE,
    BONUS_STAKE,
    MAX_SEATS,
    POT_SEED,
    Outcome,
    Rank,
    bonus,
    deal_table,
)

GUILD = 1
USER = 10
DAY = date(2026, 10, 14)


def event(**kwargs: object) -> club.HandEvent:
    base: dict[str, object] = {
        "ante": 100,
        "called": True,
        "outcome": Outcome.WIN,
        "rank": Rank.PAIR,
        "net": 300,
    }
    base.update(kwargs)
    return club.HandEvent(**base)  # type: ignore[arg-type]


# -- Mesa compartida y bote ------------------------------------------------------------


def test_la_mesa_reparte_de_una_baraja_sin_repetir_y_cada_asiento_es_una_mano() -> None:
    table = deal_table(random.Random(3))
    cards = [*table.dealer, *table.board, *(c for seat in table.seats for c in seat)]
    assert len(table.seats) == MAX_SEATS
    assert len(set(cards)) == len(cards)
    hand = table.seat(2)
    assert hand.player == table.seats[2] and hand.board == table.board
    assert hand.dealer == table.dealer


def test_la_mesa_no_admite_asientos_de_mas() -> None:
    with pytest.raises(ValueError):
        deal_table(random.Random(1), MAX_SEATS + 1)


def test_el_bote_paga_fijo_hasta_el_poker_y_se_lleva_el_bote_comun_con_escaleras_de_color() -> None:
    assert bonus(Rank.PAIR).won is False
    assert bonus(Rank.FLUSH).fixed == 4 * BONUS_STAKE
    assert bonus(Rank.QUADS).fixed == 50 * BONUS_STAKE
    assert bonus(Rank.STRAIGHT_FLUSH).pot_permille == 100
    assert bonus(Rank.ROYAL_FLUSH).pot_permille == 1000


# -- Rango, XP, retos y racha ----------------------------------------------------------


def test_el_rango_sube_con_la_xp_y_abre_las_mesas_de_ante_alto() -> None:
    assert club.rank_of(0).level == 1
    assert club.rank_of(club.RANKS[4].xp).level == 5
    assert club.rank_of(10**9).level == 10
    assert club.next_rank(10**9) is None
    assert club.min_rank_for(100).level == 1
    assert club.min_rank_for(1_000_000).max_ante is None


def test_los_rangos_van_en_orden_y_el_tope_de_ante_nunca_baja() -> None:
    xps = [r.xp for r in club.RANKS]
    assert xps == sorted(xps) and len(set(xps)) == len(xps)
    caps = [r.max_ante if r.max_ante is not None else 10**18 for r in club.RANKS]
    assert caps == sorted(caps)


def test_un_ante_pequeno_no_da_xp_ni_cuenta_en_los_retos() -> None:
    small = event(ante=club.XP_MIN_ANTE - 1)
    assert club.xp_for(small) == 0
    retos = club.daily_challenges(GUILD, USER, DAY)
    assert club.progress_for(retos, small) == {}
    assert club.xp_for(event()) == 2
    assert club.xp_for(event(net=-300)) == 1


def test_los_retos_son_tres_uno_por_dificultad_y_los_mismos_todo_el_dia() -> None:
    retos = club.daily_challenges(GUILD, USER, DAY)
    assert [c.tier for c in retos] == [0, 1, 2]
    assert retos == club.daily_challenges(GUILD, USER, DAY)
    days = {club.daily_challenges(GUILD, USER, DAY + timedelta(days=d)) for d in range(30)}
    assert len(days) > 1, "cambian de un día a otro"


def test_cada_reto_cuenta_algo_con_alguna_mano() -> None:
    hands = [
        event(),
        event(outcome=Outcome.NO_QUALIFY),
        event(rank=Rank.ROYAL_FLUSH, others=2, bonus=True, bonus_won=True),
        event(called=False, outcome=Outcome.LOSE, net=-100),
    ]
    for challenge in club.CHALLENGES:
        assert any(challenge.count(h) for h in hands), challenge.key
        assert "{goal}" not in challenge.label


def test_la_racha_sigue_si_jugo_ayer_y_vuelve_a_uno_si_no() -> None:
    assert club.next_streak(4, DAY - timedelta(days=1), DAY) == 5
    assert club.next_streak(4, DAY, DAY) == 4
    assert club.next_streak(4, DAY - timedelta(days=2), DAY) == 1
    assert club.next_streak(0, None, DAY) == 1


def test_la_racha_sube_el_premio_hasta_el_doble() -> None:
    reto = club.CHALLENGES[0]
    assert club.reward_for(reto, 1) == reto.reward
    assert club.reward_for(reto, 3) == reto.reward * 120 // 100
    assert club.reward_for(reto, 100) == reto.reward * 2


# -- Repositorio -----------------------------------------------------------------------


async def make_repo(tmp_path: Path) -> PokerRepository:
    repo = PokerRepository(tmp_path / "bot.db")
    await repo.initialize()
    return repo


async def test_apuntar_manos_suma_xp_racha_y_cumple_retos_una_sola_vez(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    goals = {"ganar_4": 2}
    first = await repo.record_hand(
        GUILD, USER, day=DAY, xp=2, counts=True, progress={"ganar_4": 1}, goals=goals
    )
    assert first.after.xp == 2 and first.after.streak == 1 and first.completed == ()
    second = await repo.record_hand(
        GUILD, USER, day=DAY, xp=2, counts=True, progress={"ganar_4": 1}, goals=goals
    )
    assert second.completed == ("ganar_4",)
    third = await repo.record_hand(
        GUILD, USER, day=DAY, xp=1, counts=True, progress={"ganar_4": 1}, goals=goals
    )
    assert third.completed == (), "un reto cumplido no se vuelve a cobrar"
    assert third.progress == {"ganar_4": 3}
    tomorrow = await repo.record_hand(
        GUILD, USER, day=DAY + timedelta(days=1), xp=1, counts=True, progress={}, goals={}
    )
    assert tomorrow.after.streak == 2 and tomorrow.after.xp == 6
    assert await repo.daily(GUILD, USER, DAY) == {}, "lo de ayer se borra"


async def test_una_mano_que_no_cuenta_no_toca_la_ficha(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    record = await repo.record_hand(GUILD, USER, day=DAY, xp=0, counts=False, progress={}, goals={})
    assert record.after == record.before == await repo.player(GUILD, USER)


async def test_el_bote_de_cada_uno_se_recuerda_y_se_olvida_con_el_servidor(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.set_bonus(GUILD, USER, True)
    await repo.record_hand(GUILD, USER, day=DAY, xp=1, counts=True, progress={}, goals={})
    assert (await repo.player(GUILD, USER)).bonus is True
    await repo.forget_guild(GUILD)
    assert (await repo.player(GUILD, USER)).xp == 0


# -- El bote en la economía ------------------------------------------------------------


async def make_economy(tmp_path: Path) -> EconomyService:
    repository = EconomyRepository(tmp_path / "eco.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository)


def pot_ledger(tmp_path: Path) -> list[tuple[str, int]]:
    connection = sqlite_module.connect(tmp_path / "eco.db")
    try:
        rows = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE user_id = ? ORDER BY id",
            (POKER_POT_ACCOUNT_ID,),
        ).fetchall()
    finally:
        connection.close()
    return [(r[0], r[1]) for r in rows]


async def test_el_bote_crece_con_cada_apuesta_y_la_escalera_real_se_lo_lleva(
    tmp_path: Path,
) -> None:
    eco = await make_economy(tmp_path)
    await eco.place_bet(GUILD, USER, game="poker", stake=BONUS_STAKE)
    await eco.pay_poker(GUILD, USER, game="poker", payout=0, share=BONUS_SHARE, seed=POT_SEED)
    assert await eco.poker_pot(GUILD, seed=POT_SEED) == POT_SEED + BONUS_SHARE

    await eco.place_bet(GUILD, USER, game="poker", stake=BONUS_STAKE)
    before = await eco.balance(GUILD, USER)
    paid = await eco.pay_poker(
        GUILD, USER, game="poker", payout=0, share=BONUS_SHARE, pot_permille=1000, seed=POT_SEED
    )
    assert paid.pot_won == POT_SEED + 2 * BONUS_SHARE
    assert paid.pot == POT_SEED, "la casa lo vuelve a sembrar"
    tax = await eco.state_balance(GUILD)
    assert paid.bet.balance == before + paid.pot_won - tax
    assert pot_ledger(tmp_path) == [
        ("poker:semilla", POT_SEED),
        ("poker:aporte", BONUS_SHARE),
        ("poker:aporte", BONUS_SHARE),
        ("poker:bote", -(POT_SEED + 2 * BONUS_SHARE)),
        ("poker:semilla", POT_SEED),
    ]


async def test_la_escalera_de_color_se_lleva_el_diez_por_ciento(tmp_path: Path) -> None:
    eco = await make_economy(tmp_path)
    await eco.place_bet(GUILD, USER, game="poker", stake=BONUS_STAKE)
    paid = await eco.pay_poker(
        GUILD, USER, game="poker", payout=0, share=BONUS_SHARE, pot_permille=100, seed=POT_SEED
    )
    assert paid.pot_won == (POT_SEED + BONUS_SHARE) // 10
    assert paid.pot == POT_SEED + BONUS_SHARE - paid.pot_won
