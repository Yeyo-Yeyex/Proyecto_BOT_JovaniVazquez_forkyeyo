"""Pruebas de la fase 6 de la ampliación de `pisos` (8.5.0).

La junta de copropietarios (propuestas, votos por participaciones, el silencio como sí y las
mayorías del Código Civil y la LPH), el fondo de reserva y la acción de división.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from bot.repositories.housing import HousingError
from bot.repositories.housing_junta import APPROVED, FAILED, REJECTED
from bot.services import housing as rules
from bot.services.housing_catalog import KINDS, REFORM_BY_KEY
from tests.unit.test_housing import (
    ANA,
    BAJO,
    BEA,
    GUILD,
    Clock,
    Scripted,
    at,
    give,
    ledger_sum,
    make,
)

CARLA = 30
DAY_SECONDS = 24 * 3600


async def _shared(tmp_path: Path, ana: int = 60, bea: int = 40):
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    for member in (ANA, BEA, CARLA):
        await give(economy, member, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, ana)
    await service.buy(GUILD, BEA, BAJO.key, bea)
    return clock, economy, service


# -- Reglas --------------------------------------------------------------------------------


def test_las_mayorias_de_la_junta() -> None:
    assert rules.passes(rules.MAJORITY, 100, 40)
    assert not rules.passes(rules.MAJORITY, 100, 50)  # empate: no hay mayoría
    assert rules.passes(rules.THREE_FIFTHS, 100, 40)
    assert not rules.passes(rules.THREE_FIFTHS, 100, 41)
    assert rules.passes(rules.UNANIMITY, 100, 0)
    assert not rules.passes(rules.UNANIMITY, 100, 1)


# -- Con la base de datos --------------------------------------------------------------


async def test_la_junta_es_para_inmuebles_con_varios_duenos(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with pytest.raises(HousingError, match="varios dueños"):
        await service.propose(GUILD, ANA, BAJO.key, "renta", "mercado")


async def test_con_todos_los_votos_se_cierra_en_el_momento(tmp_path: Path) -> None:
    _clock, _economy, service = await _shared(tmp_path)
    vote = await service.propose(GUILD, BEA, BAJO.key, "renta", "mercado")
    assert vote.ballots == {BEA: True}
    with pytest.raises(HousingError, match="abierta"):
        await service.propose(GUILD, ANA, BAJO.key, "renta", "social")
    with pytest.raises(HousingError, match="dueños"):
        await service.vote(GUILD, CARLA, vote.id, True)
    result = await service.vote(GUILD, ANA, vote.id, False)
    # Ana tiene el 60 % y dice que no: no hay mayoría.
    assert result.vote.status == REJECTED and set(result.owners) == {ANA, BEA}
    assert (await service.market(GUILD)).leases[BAJO.key].level != "mercado"
    vote = await service.propose(GUILD, ANA, BAJO.key, "renta", "mercado")
    result = await service.vote(GUILD, BEA, vote.id, False)
    assert result.vote.status == APPROVED
    assert (await service.market(GUILD)).leases[BAJO.key].level == "mercado"


async def test_quien_no_vota_cuenta_a_favor_y_la_junta_elige_quien_administra(
    tmp_path: Path,
) -> None:
    clock, _economy, service = await _shared(tmp_path, ana=40, bea=60)
    assert (await service.market(GUILD)).manager(BAJO.key) == BEA
    vote = await service.propose(GUILD, ANA, BAJO.key, "administra", str(ANA))
    assert await service.close_votes(GUILD) == []  # aún no ha acabado
    clock.now = at(14).timestamp() + DAY_SECONDS + 1
    (result,) = await service.close_votes(GUILD)
    assert result.vote.id == vote.id and result.vote.status == APPROVED
    market = await service.market(GUILD)
    assert market.manager(BAJO.key) == ANA  # Bea no votó: su silencio vale como sí
    await service.set_level(GUILD, ANA, BAJO.key, "social")
    with pytest.raises(HousingError):
        await service.set_level(GUILD, BEA, BAJO.key, "mercado")


async def test_una_reforma_pide_tres_quintos_y_la_pagan_todos(tmp_path: Path) -> None:
    _clock, _economy, service = await _shared(tmp_path, ana=50, bea=50)
    vote = await service.propose(GUILD, ANA, BAJO.key, "reforma", "pintura")
    result = await service.vote(GUILD, BEA, vote.id, False)
    assert result.vote.status == REJECTED  # 50 % no llega a 3/5


async def test_una_reforma_aprobada_la_pagan_por_participaciones(tmp_path: Path) -> None:
    _clock, economy, service = await _shared(tmp_path)
    before = {m: await economy.balance(GUILD, m) for m in (ANA, BEA)}
    held = (await service.market(GUILD)).holdings[BAJO.key]
    vote = await service.propose(GUILD, ANA, BAJO.key, "reforma", "pintura")
    result = await service.vote(GUILD, BEA, vote.id, False)  # 60 % sí: justo 3/5
    assert result.vote.status == APPROVED
    quote = rules.reform_quote(BAJO, REFORM_BY_KEY["pintura"])
    paid = {m: before[m] - await economy.balance(GUILD, m) for m in (ANA, BEA)}
    assert sum(paid.values()) == quote.total
    assert paid[ANA] > paid[BEA]
    market = await service.market(GUILD)
    assert market.leases[BAJO.key].status == rules.OBRAS
    assert (
        market.holdings[BAJO.key][ANA].paid
        == held[ANA].paid + rules.split(quote.cost, {ANA: 60, BEA: 40})[ANA]
    )
    for member in (ANA, BEA):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)


async def test_si_a_un_dueno_no_le_llega_la_reforma_falla(tmp_path: Path) -> None:
    _clock, economy, service = await _shared(tmp_path)
    await give(economy, BEA, -(await economy.balance(GUILD, BEA)))
    vote = await service.propose(GUILD, ANA, BAJO.key, "reforma", "integral")
    result = await service.vote(GUILD, BEA, vote.id, True)
    assert result.vote.status == FAILED and "no le llega" in result.vote.note
    market = await service.market(GUILD)
    assert market.leases[BAJO.key].status != rules.OBRAS
    assert "integral" not in market.reforms.get(BAJO.key, {})


async def test_vender_entero_pide_unanimidad(tmp_path: Path) -> None:
    clock, economy, service = await _shared(tmp_path)
    vote = await service.propose(GUILD, ANA, BAJO.key, "venta")
    result = await service.vote(GUILD, BEA, vote.id, False)
    assert result.vote.status == REJECTED
    await service.propose(GUILD, ANA, BAJO.key, "venta")
    clock.now = at(14).timestamp() + DAY_SECONDS + 1
    (result,) = await service.close_votes(GUILD)
    assert result.vote.status == APPROVED
    assert BAJO.key not in (await service.market(GUILD)).holdings
    for member in (ANA, BEA):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)


async def test_la_derrama_llena_el_fondo_y_lo_que_no_se_paga_es_deuda_del_piso(
    tmp_path: Path,
) -> None:
    _clock, economy, service = await _shared(tmp_path)
    await give(economy, BEA, -(await economy.balance(GUILD, BEA)))
    ana = await economy.balance(GUILD, ANA)
    vote = await service.propose(GUILD, ANA, BAJO.key, "derrama", amount=10_000)
    result = await service.vote(GUILD, BEA, vote.id, True)
    assert result.vote.status == APPROVED
    market = await service.market(GUILD)
    assert market.reserves[BAJO.key] == 10_000
    assert ana - await economy.balance(GUILD, ANA) == 6_000
    assert market.leases[BAJO.key].arrears == 4_000  # lo de Bea, a deber
    with pytest.raises(HousingError, match="derrama va de"):
        await service.propose(GUILD, ANA, BAJO.key, "derrama", amount=1)


async def test_el_fondo_de_reserva_paga_la_derrama(tmp_path: Path) -> None:
    clock, _economy, service = await _shared(tmp_path)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO housing_reserve (guild_id, listing, balance) VALUES (?, ?, 1000000)",
            (GUILD, BAJO.key),
        )
    service.rng = Scripted([0.99, 0.99, 0.0])  # una derrama y nada más
    clock.now = at(15).timestamp()
    (run,) = await service.close_days(GUILD)
    derrama = next(n for n in run.news if n.kind == "derrama")
    covered = next(n for n in run.news if n.kind == "fondo_reserva")
    assert covered.amount == derrama.amount
    saved = round(KINDS[BAJO.kind].community * 10 * rules.RESERVE_SHARE)  # 10 Y$ por euro
    market = await service.market(GUILD)
    assert market.reserves[BAJO.key] == pytest.approx(1_000_000 - derrama.amount + saved, abs=2)


async def test_la_accion_de_division_vende_entero_al_que_mas_puja(tmp_path: Path) -> None:
    clock, economy, service = await _shared(tmp_path)
    with pytest.raises(HousingError):
        await service.divide(GUILD, CARLA, BAJO.key)
    auction = await service.divide(GUILD, BEA, BAJO.key)
    assert auction.division and auction.shares == 100
    with pytest.raises(HousingError, match="división"):
        await service.sell(GUILD, ANA, BAJO.key, 60)
    before = {m: await economy.balance(GUILD, m) for m in (ANA, BEA)}
    await service.bid(GUILD, CARLA, auction.id, auction.min_bid)
    clock.now = at(14).timestamp() + DAY_SECONDS + 1
    run = await service.close_auctions(GUILD)
    assert run.results[0].winner == CARLA
    market = await service.market(GUILD)
    assert market.shares_of(BAJO.key) == {CARLA: 100}
    for member in (ANA, BEA):
        assert await economy.balance(GUILD, member) > before[member]
    for member in (ANA, BEA, CARLA):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)


async def test_en_la_division_un_dueno_puja_y_paga_solo_lo_de_los_demas(tmp_path: Path) -> None:
    clock, economy, service = await _shared(tmp_path)
    auction = await service.divide(GUILD, ANA, BAJO.key)
    bea = await economy.balance(GUILD, BEA)
    bid = await service.bid(GUILD, BEA, auction.id, auction.min_bid)
    # Bea deja en el depósito el 60 % de la puja (lo de Ana) con su ITP y su notaría.
    assert bid.escrow < auction.min_bid
    clock.now = at(14).timestamp() + DAY_SECONDS + 1
    await service.close_auctions(GUILD)
    assert (await service.market(GUILD)).shares_of(BAJO.key) == {BEA: 100}
    assert bea - await economy.balance(GUILD, BEA) == bid.escrow
    for member in (ANA, BEA):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)


async def test_sin_pujas_la_division_vende_al_mercado(tmp_path: Path) -> None:
    clock, economy, service = await _shared(tmp_path)
    await service.divide(GUILD, ANA, BAJO.key)
    clock.now = at(14).timestamp() + DAY_SECONDS + 1
    run = await service.close_auctions(GUILD)
    assert run.results[0].winner is None
    assert BAJO.key not in (await service.market(GUILD)).holdings
    assert any(n.kind == "division_mercado" for n in run.news)


async def test_la_licencia_por_la_junta_la_pagan_todos(tmp_path: Path) -> None:
    _clock, economy, service = await _shared(tmp_path)
    service.rng = random.Random(1)  # los vecinos del bloque dicen que sí (0,13)
    before = {m: await economy.balance(GUILD, m) for m in (ANA, BEA)}
    vote = await service.propose(GUILD, BEA, BAJO.key, "licencia")
    result = await service.vote(GUILD, ANA, vote.id, True)
    assert result.vote.status == APPROVED
    assert (await service.market(GUILD)).leases[BAJO.key].license
    paid = {m: before[m] - await economy.balance(GUILD, m) for m in (ANA, BEA)}
    assert paid == rules.split(rules.LICENSE_FEE, {ANA: 60, BEA: 40})
