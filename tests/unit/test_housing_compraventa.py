"""Pruebas de la fase 3 de la ampliación de `pisos` (8.2.0).

Vender participaciones a otro miembro (ITP sobre el valor de referencia, ganancia sobre el
de mercado), subastas entre miembros con su depósito y las subastas judiciales de las
hipotecas ejecutadas (lo adjudicado no se vende al mercado en 30 días).
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from bot.repositories.economy import (
    HOUSING_AUCTION_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.repositories.housing import HousingError
from bot.repositories.housing_finance import HOME_OWN
from bot.services import housing as rules
from bot.services.housing_catalog import LISTING_BY_KEY
from tests.unit.test_housing_phases import (
    ANA,
    BAJO,
    BEA,
    GUILD,
    Clock,
    at,
    give,
    ledger_sum,
    make,
    salary,
    sql,
)

CAROL = 30
LOCAL = next(item for item in LISTING_BY_KEY.values() if item.kind == "local")


def housing_tax(tmp_path: Path, user_id: int, kind: str) -> int:
    (total,) = sql(
        tmp_path,
        "SELECT COALESCE(SUM(tax), 0) FROM economy_housing_tax WHERE user_id = ? AND kind = ?",
        user_id,
        kind,
    )[0]
    return int(total)


# -- Reglas --------------------------------------------------------------------------------


def test_el_valor_de_referencia_es_el_90_del_de_mercado() -> None:
    value = rules.share_price(BAJO) * 40
    assert rules.reference_value(BAJO, 40) == round(value * rules.REFERENCE_SHARE)


def test_vender_barato_no_baja_el_itp_ni_la_ganancia() -> None:
    value = rules.share_price(BAJO) * 100
    cheap = rules.member_sale_quote(BAJO, 100, 100, value, 1_000)
    # El ITP va sobre el valor de referencia y la ganancia, sobre el de mercado.
    assert cheap.itp == round(cheap.reference * rules.ITP_RATE)
    assert cheap.gain == value - value and cheap.gift == value - 1_000
    dear = rules.member_sale_quote(BAJO, 100, 100, value, value * 2)
    assert dear.itp == round(value * 2 * rules.ITP_RATE)
    assert dear.gain == value and dear.gain_tax > 0
    # En una subasta el precio es el de mercado.
    auctioned = rules.member_sale_quote(BAJO, 100, 100, value, value // 2, market_value_gain=False)
    assert auctioned.gain == value // 2 - value
    judicial = rules.member_sale_quote(
        BAJO, 100, 100, value, value, itp_rate=rules.AUCTION_ITP_RATE
    )
    assert judicial.itp == round(value * rules.AUCTION_ITP_RATE)
    with pytest.raises(ValueError):
        rules.member_sale_quote(BAJO, 101, 100, value, value)
    with pytest.raises(ValueError):
        rules.member_sale_quote(BAJO, 10, 100, value, 0)


def test_cada_puja_sube_un_uno_por_ciento_y_la_ultima_hora_alarga() -> None:
    assert rules.next_bid(0, 5_000) == 5_000
    assert rules.next_bid(10_000, 5_000) == 10_100
    assert rules.next_bid(10, 1) == 11  # al menos 1 Y$ más
    assert rules.auction_end(10_000.0, 1_000.0) == 10_000.0
    assert rules.auction_end(10_000.0, 9_500.0) == 9_500.0 + rules.AUCTION_EXTEND_SECONDS


def test_la_subasta_judicial_sigue_al_70_60_y_50_de_la_lec() -> None:
    value, owed = 100_000, 80_000
    assert rules.judicial_ok(70_000, value, owed, habitual=True)
    assert not rules.judicial_ok(49_000, value, owed, habitual=False)
    assert rules.judicial_ok(55_000, value, owed, habitual=False)
    assert not rules.judicial_ok(65_000, value, owed, habitual=True)  # no cubre la deuda
    assert rules.judicial_ok(65_000, value, 60_000, habitual=True)
    assert not rules.judicial_ok(58_000, value, 50_000, habitual=True)  # por debajo del 60 %


# -- Vender a un miembro -------------------------------------------------------------------


async def test_vender_a_un_miembro_mueve_el_dinero_y_los_impuestos(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    index = (await service.market(GUILD)).index(BAJO.barrio)
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    before = {m: await economy.balance(GUILD, m) for m in (ANA, BEA)}

    sale, balance = await service.member_sale(GUILD, ANA, BEA, BAJO.key, 40, 100_000)

    quote = sale.quote
    assert balance == before[BEA] - quote.buyer_total
    assert await economy.balance(GUILD, ANA) == before[ANA] + 100_000 - quote.gain_tax
    market = await service.market(GUILD)
    assert market.shares_of(BAJO.key) == {ANA: 60, BEA: 40}
    assert market.index(BAJO.barrio) == pytest.approx(index)  # no entra ni sale nada del mercado
    assert housing_tax(tmp_path, BEA, "itp") == quote.itp
    assert quote.itp == round(max(100_000, quote.reference) * rules.ITP_RATE)
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == (
        quote.itp + quote.notary_igic + quote.gain_tax
    )
    for member in (ANA, BEA):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)
    with pytest.raises(HousingError):
        await service.member_sale(GUILD, ANA, ANA, BAJO.key, 10, 1_000)


async def test_el_comprador_puede_pagar_con_hipoteca(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, BEA, 6_000, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 300_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    price = rules.price(BAJO)
    sale, _ = await service.member_sale(GUILD, ANA, BEA, BAJO.key, 100, price, financed=True)
    assert sale.loan == round(price * rules.MORTGAGE_LTV)
    assert (await service.market(GUILD)).mortgages_of(BEA)
    assert ledger_sum(tmp_path, BEA) == await economy.balance(GUILD, BEA)


async def test_el_inquilino_que_compra_su_piso_se_queda_a_vivir(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    sale, _ = await service.member_sale(GUILD, ANA, BEA, BAJO.key, 100, rules.price(BAJO))
    assert sale.moved_in
    market = await service.market(GUILD)
    assert market.homes[BEA] == (HOME_OWN, BAJO.key)
    assert market.leases[BAJO.key].status == rules.HABITUAL
    assert market.leases[BAJO.key].member is None


# -- Subastas entre miembros -------------------------------------------------------------


async def test_una_subasta_se_puja_devuelve_al_superado_y_se_adjudica(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    for member in (ANA, BEA, CAROL):
        await give(economy, member, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    auction = await service.create_auction(GUILD, ANA, BAJO.key, 50, 100_000)
    with pytest.raises(HousingError):
        await service.sell(GUILD, ANA, BAJO.key, 10)  # en subasta, no se vende
    with pytest.raises(HousingError):
        await service.bid(GUILD, ANA, auction.id, 200_000)  # en la suya no puja
    with pytest.raises(HousingError):
        await service.bid(GUILD, BEA, auction.id, 99_000)  # por debajo de la mínima
    bea_before = await economy.balance(GUILD, BEA)
    first = await service.bid(GUILD, BEA, auction.id, 120_000)
    assert await economy.balance(GUILD, BEA) == bea_before - first.escrow
    assert ledger_sum(tmp_path, HOUSING_AUCTION_ACCOUNT_ID) == first.escrow
    # A la última hora, Carol puja y la subasta se alarga.
    clock.now = auction.ends_at - 600
    second = await service.bid(GUILD, CAROL, auction.id, 130_000)
    assert second.outbid == BEA and second.extended
    assert await economy.balance(GUILD, BEA) == bea_before  # se le devuelve todo
    assert ledger_sum(tmp_path, HOUSING_AUCTION_ACCOUNT_ID) == second.escrow

    clock.now = second.auction.ends_at + 1
    run = await service.close_auctions(GUILD)

    (result,) = run.results
    assert result.winner == CAROL and result.price == 130_000
    market = await service.market(GUILD)
    assert market.shares_of(BAJO.key) == {ANA: 50, CAROL: 50}
    assert ledger_sum(tmp_path, HOUSING_AUCTION_ACCOUNT_ID) == 0
    assert housing_tax(tmp_path, CAROL, "itp") > 0
    for member in (ANA, BEA, CAROL):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)
    assert market.auctions == ()


async def test_una_subasta_sin_pujas_no_vende_nada(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    auction = await service.create_auction(GUILD, ANA, BAJO.key, 100, 10**9)
    clock.now = auction.ends_at + 1
    run = await service.close_auctions(GUILD)
    assert run.results[0].winner is None and not run.results[0].bank
    assert (await service.market(GUILD)).shares_of(BAJO.key) == {ANA: 100}


async def test_sin_dinero_para_el_deposito_no_hay_puja(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    auction = await service.create_auction(GUILD, ANA, BAJO.key, 100, 1_000)
    with pytest.raises(InsufficientFundsError):
        await service.bid(GUILD, BEA, auction.id, 10**8)
    assert (await service.market(GUILD)).auctions[0].best_bidder is None


# -- Subastas judiciales -------------------------------------------------------------------


async def foreclosed(tmp_path: Path):  # noqa: ANN201
    """Ana compra con hipoteca y deja de pagar hasta que el banco ejecuta: sale a subasta."""
    clock = Clock(at(1))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)
    await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])
    for d in range(2, 17):
        clock.to(d)
        runs = await service.close_days(GUILD)
        if any(run.auctions for run in runs):
            break
    (auction,) = (await service.market(GUILD)).auctions
    assert auction.judicial and auction.seller == ANA
    return clock, economy, service, auction


async def test_la_ejecucion_sale_a_subasta_y_el_ganador_no_revende_en_30_dias(
    tmp_path: Path,
) -> None:
    clock, economy, service, auction = await foreclosed(tmp_path)
    await give(economy, BEA, 10_000_000)
    value = rules.price(BAJO, (await service.market(GUILD)).index(BAJO.barrio))
    bid = round(value * 0.8)
    await service.bid(GUILD, BEA, auction.id, bid)
    clock.now = auction.ends_at + 1

    run = await service.close_auctions(GUILD)

    assert run.results[0].winner == BEA and run.executions == [(ANA, BAJO.key)]
    market = await service.market(GUILD)
    assert market.shares_of(BAJO.key) == {BEA: 100}
    assert market.mortgages_of(ANA) == []
    assert housing_tax(tmp_path, BEA, "itp") >= round(bid * rules.AUCTION_ITP_RATE)
    holding = market.holdings[BAJO.key][BEA]
    assert holding.locked_until == run.news[0].day + timedelta(days=rules.AUCTION_LOCK_DAYS)
    with pytest.raises(HousingError, match="subasta judicial"):
        await service.sell(GUILD, BEA, BAJO.key, 100)
    await give(economy, CAROL, 10_000_000)
    await service.member_sale(GUILD, BEA, CAROL, BAJO.key, 10, 10_000)  # a un miembro, sí
    for member in (ANA, BEA, CAROL):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)
    assert ledger_sum(tmp_path, HOUSING_AUCTION_ACCOUNT_ID) == 0


async def test_una_puja_baja_por_la_casa_del_ejecutado_no_vale(tmp_path: Path) -> None:
    clock, economy, service, auction = await foreclosed(tmp_path)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO housing_homes (guild_id, user_id, kind, ref, since) "
            "VALUES (?, ?, ?, ?, 0)",
            (GUILD, ANA, HOME_OWN, BAJO.key),
        )
    await give(economy, BEA, 10_000_000)
    await service.bid(GUILD, BEA, auction.id, auction.min_bid)  # el 50 %: no cubre la deuda
    before = await economy.balance(GUILD, BEA)
    clock.now = auction.ends_at + 1
    run = await service.close_auctions(GUILD)
    assert run.results[0].bank and run.results[0].winner is None
    assert await economy.balance(GUILD, BEA) > before  # se le devuelve el depósito
    assert BAJO.key not in (await service.market(GUILD)).holdings  # el banco, al 70 %


async def test_pagar_toda_la_deuda_antes_del_remate_anula_la_subasta(tmp_path: Path) -> None:
    clock, economy, service, auction = await foreclosed(tmp_path)
    await give(economy, BEA, 10_000_000)
    await service.bid(GUILD, BEA, auction.id, auction.min_bid * 2)
    await give(economy, ANA, 10_000_000)
    (mortgage,) = (await service.market(GUILD)).mortgages_of(ANA)
    await service.amortize(GUILD, ANA, mortgage.id)
    market = await service.market(GUILD)
    assert market.auctions == () and market.shares_of(BAJO.key) == {ANA: 100}
    assert ledger_sum(tmp_path, HOUSING_AUCTION_ACCOUNT_ID) == 0
    assert ledger_sum(tmp_path, BEA) == await economy.balance(GUILD, BEA)
    clock.now = auction.ends_at + 1
    assert (await service.close_auctions(GUILD)).results == []
