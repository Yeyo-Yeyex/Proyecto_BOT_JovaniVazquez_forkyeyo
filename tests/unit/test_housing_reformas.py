"""Pruebas de la fase 4 de la ampliación de `pisos` (8.3.0).

Reformas (coste con IGIC, ICIO y licencia; suben renta y valor; la integral puede ser
rehabilitación y dar el 60 %), los imprevistos que decide el dueño (o la gestora a los dos
días) y el humor del inquilino.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.repositories.economy import STATE_ACCOUNT_ID
from bot.repositories.housing import HousingError
from bot.services import housing as rules
from bot.services.housing_catalog import LISTING_BY_KEY, REFORM_BY_KEY, REFORMS
from tests.unit.test_housing import (
    ANA,
    BAJO,
    BEA,
    GUILD,
    Clock,
    Scripted,
    at,
    give,
    housing_taxes,
    ledger_sum,
    make,
)

DAY = date(2026, 10, 14)
GARAJE = next(item for item in LISTING_BY_KEY.values() if item.kind == "garaje")


# -- Reglas --------------------------------------------------------------------------------


def test_una_reforma_paga_igic_icio_y_licencia() -> None:
    quote = rules.reform_quote(BAJO, REFORM_BY_KEY["cocina"])
    assert quote.cost == 90_000  # 9.000 € a 10 Y$ por euro
    assert quote.igic == round(quote.cost * rules.SERVICES_IGIC)
    assert quote.icio == round(quote.cost * rules.ICIO_RATE)
    assert quote.total == quote.cost + quote.igic + quote.icio + rules.WORKS_LICENSE_FEE
    building = next(i for i in LISTING_BY_KEY.values() if i.kind == "edificio")
    assert rules.reform_cost(building, REFORM_BY_KEY["cocina"]) == 8 * 90_000


def test_reformar_para_vender_pierde_y_las_mejoras_tienen_tope() -> None:
    for reform in REFORMS:
        if BAJO.kind not in reform.kinds:
            continue
        cost = rules.reform_cost(BAJO, reform)
        better = rules.improved(BAJO, [(reform, cost, DAY, False)])
        gain = rules.price(better) - rules.price(BAJO)
        assert gain < cost, reform.key  # el valor sube menos de lo que cuesta
        assert rules.reference_rent(better) > rules.reference_rent(BAJO)
    everything = rules.improved(
        BAJO, [(r, 10**9, DAY, False) for r in REFORMS if BAJO.kind in r.kinds]
    )
    assert everything.value_bonus == everything.rent_bonus == rules.REFORM_MAX_BONUS
    assert everything.energy == "B" and everything.breakdowns < 1 and everything.damp < 1
    # El catastro no ve las reformas.
    assert rules.cadastral_value(everything) == rules.cadastral_value(BAJO)


def test_la_integral_es_rehabilitacion_si_pasa_del_25_de_la_construccion() -> None:
    integral = REFORM_BY_KEY["integral"]
    cheap = min(LISTING_BY_KEY.values(), key=lambda i: rules.price(i) / max(i.m2, 1))
    dear = max(
        (i for i in LISTING_BY_KEY.values() if i.kind in integral.kinds),
        key=lambda i: rules.price(i) / max(i.m2, 1),
    )
    if cheap.kind in integral.kinds:
        assert rules.reform_quote(cheap, integral).rehab
    assert not rules.reform_quote(dear, integral).rehab
    assert not rules.reform_quote(BAJO, REFORM_BY_KEY["cocina"]).rehab


def test_el_60_vale_dos_anos_tras_la_rehabilitacion() -> None:
    rehab = replace(BAJO, rehab_until=DAY + timedelta(days=rules.REHAB_DAYS))
    assert (
        rules.contract_reduction(rehab, "referencia", index=1.0, rent=1, last_rent=1, day=DAY)
        == rules.REHAB_REDUCTION
    )
    assert (
        rules.contract_reduction(
            rehab, "referencia", index=1.0, rent=1, last_rent=1, day=DAY + timedelta(days=30)
        )
        == rules.RENT_REDUCTION
    )
    signed = rules.sign(rehab, rules.Lease(since=DAY), "abuela", DAY, 1.0)
    assert signed.reduction == rules.REHAB_REDUCTION


def test_en_obras_no_se_alquila_y_al_acabar_queda_vacio() -> None:
    lease = rules.Lease(status=rules.OBRAS, works_until=DAY + timedelta(days=3), since=DAY)
    during = rules.advance_day(BAJO, lease, DAY + timedelta(days=1), 1.0, Scripted(fill=0.0))
    assert during.lease.status == rules.OBRAS and during.rent == 0
    after = rules.advance_day(BAJO, lease, DAY + timedelta(days=3), 1.0, Scripted())
    assert after.lease.status == rules.VACANT
    assert any(e.key == "obra_terminada" for e in after.events)


def test_cada_forma_de_arreglar_un_imprevisto() -> None:
    rng = random.Random(1)
    assert rules.resolve("averia", None, 1_000, rng).pay == 1_000  # la gestora: técnico
    chapuza = rules.resolve("averia", "cunado", 1_000, Scripted([0.1]))
    assert chapuza.pay == 350 and chapuza.relapse and chapuza.mood < 0
    assert rules.resolve("averia", "espera", 1_000, rng).pay == 1_200
    parte = rules.resolve("humedades", "parte", 9_000, rng)
    assert parte.pay == rules.HOME_CLAIM_EXCESS and parte.claim
    assert rules.resolve("vecino", "mediacion", 0, rng).pay == 3_000
    assert rules.resolve("vecino", "hablar", 0, Scripted([0.1])).mood < 0
    assert rules.resolve("vecino", "hablar", 0, Scripted([0.9])).mood == 0


def test_el_humor_cambia_lo_que_se_queda_el_inquilino() -> None:
    assert rules.mood_factor(rules.MOOD_BASE) == 1.0
    assert rules.mood_factor(100) < 1.0 < rules.mood_factor(10)


# -- Con la base de datos --------------------------------------------------------------


async def test_reformar_cobra_entra_en_obras_y_al_acabar_vale_mas(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with pytest.raises(HousingError):
        await service.start_reform(GUILD, BEA, BAJO.key, "cocina")
    with pytest.raises(HousingError):
        await service.start_reform(GUILD, ANA, BAJO.key, "negocio")  # no es un local
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    before = await economy.balance(GUILD, ANA)
    held = (await service.market(GUILD)).holdings[BAJO.key][ANA]

    done, balance = await service.start_reform(GUILD, ANA, BAJO.key, "cocina")

    quote = done.quote
    assert balance == before - quote.total
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == quote.igic + quote.icio + quote.license
    assert housing_taxes(tmp_path, "icio") == quote.icio
    market = await service.market(GUILD)
    assert market.leases[BAJO.key].status == rules.OBRAS
    assert market.holdings[BAJO.key][ANA].paid == held.paid + quote.cost
    assert market.holdings[BAJO.key][ANA].cost == held.cost + quote.total
    assert market.listing(BAJO.key).rent_bonus == 0  # aún en obras
    with pytest.raises(HousingError):
        await service.start_reform(GUILD, ANA, BAJO.key, "cocina")
    for d in range(15, 21):
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    market = await service.market(GUILD)
    improved = market.listing(BAJO.key)
    assert improved.rent_bonus == REFORM_BY_KEY["cocina"].rent_bonus
    assert rules.price(improved) > rules.price(BAJO)
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)


async def test_un_imprevisto_espera_al_dueno_y_si_no_lo_arregla_la_gestora(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'funcionario', "
            "rent = 8000, until = '2027-06-01'"
        )
    # Solo una avería (el primer imprevisto) y nada más.
    service.rng = Scripted([0.0] + [0.99] * 40, fill=0.99)
    clock.now = at(15).timestamp()
    await service.close_days(GUILD)
    market = await service.market(GUILD)
    (decision,) = market.decisions[BAJO.key]
    assert decision.event == "averia"
    # Bea no decide: no es suyo.
    with pytest.raises(HousingError):
        await service.choose(GUILD, BEA, decision.id, "cunado")
    await service.choose(GUILD, ANA, decision.id, "cunado")
    # Seis imprevistos que no pasan, el inquilino paga y no se va, y la chapuza se rompe.
    service.rng = Scripted([0.99] * 8 + [0.1], fill=0.99)
    clock.now = at(16).timestamp()
    runs = await service.close_days(GUILD)
    assert any(choice == "cunado" for _o, _e, choice in runs[0].resolved)
    market = await service.market(GUILD)
    assert market.leases[BAJO.key].mood < rules.MOOD_BASE
    assert [d.event for d in market.decisions[BAJO.key]] == ["averia"]  # vuelve
    # Nadie decide: a los dos días lo arregla la gestora (técnico).
    for d in range(17, 20):
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    assert (await service.market(GUILD)).decisions.get(BAJO.key, ()) == ()
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)


async def test_un_garaje_no_admite_reformas_de_vivienda(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, GARAJE.key, 100)
    with pytest.raises(HousingError):
        await service.start_reform(GUILD, ANA, GARAJE.key, "pintura")
