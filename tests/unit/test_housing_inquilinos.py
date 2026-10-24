"""Pruebas de la fase 2 de la ampliación de `pisos` (8.1.0).

Inquilinos con personalidad, elegir inquilino entre candidatos, el seguro de impago que
paga, el desahucio con abogado, el impuesto de primas y el IGIC de las reparaciones, las
deducciones canarias del casero (15 quater y 16) y las noticias de barrio.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.repositories.economy import STATE_ACCOUNT_ID, InsufficientFundsError
from bot.repositories.housing import HousingError
from bot.services import housing as rules
from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIO_NEWS,
    BARRIO_NEWS_BY_KEY,
    BUSINESS_KINDS,
    KINDS,
    LISTING_BY_KEY,
    RENT_LEVELS,
    ROOM_KIND,
    TENANT_BY_KEY,
    TENANTS,
)
from bot.services.taxes import (
    INSURANCE_DEDUCTION_MAX_EUR,
    LANDLORD_DEDUCTION_MAX_EUR,
    PropertyWeek,
    landlord_deduction,
    vacant_rental_deduction,
    weekly_yd,
)
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

LOCAL = next(item for item in LISTING_BY_KEY.values() if item.kind == "local")
DAY = date(2026, 10, 14)


def rented(tenant: str, **changes: object) -> rules.Lease:
    lease = rules.Lease(
        status=rules.RENTED, level="referencia", tenant=tenant, rent=4_000,
        until=DAY + timedelta(days=60), since=DAY,
    )  # fmt: skip
    return replace(lease, **changes)  # type: ignore[arg-type]


# -- Catálogo de inquilinos ---------------------------------------------------------------


def test_cada_inquilino_tiene_personalidad_completa() -> None:
    assert len(TENANTS) >= 55 and len(TENANT_BY_KEY) == len(TENANTS)
    people = [t for t in TENANTS if not t.kinds]
    assert len(people) >= 40 and len([t for t in TENANTS if t.kinds]) >= 16
    for tenant in TENANTS:
        assert tenant.line and tenant.emoji and tenant.levels, tenant.key
        assert tenant.risk > 0 and tenant.stay > 0 and tenant.wear > 0, tenant.key
        if tenant.levels != ("vacacional",):
            # Quien firma un contrato largo habla en cada momento y tiene rasgos.
            assert tenant.renew and tenant.leave and tenant.unpaid and tenant.complaint
            assert len(tenant.traits) >= 2, tenant.key
        person = not tenant.kinds or tenant.kinds == {ROOM_KIND}
        if person and tenant.levels != ("vacacional",):
            assert 18 <= tenant.age <= 90, tenant.key
        assert tenant.kinds <= BUSINESS_KINDS | {ROOM_KIND}, tenant.key


def test_una_habitacion_solo_la_alquila_quien_busca_habitacion() -> None:
    """Una familia no se mete en una habitación, y quien busca habitación no alquila un piso."""
    rooms = [t for t in TENANTS if t.kinds == {ROOM_KIND}]
    assert len(rooms) >= 10
    for level in ("social", "referencia", "mercado", "nomadas"):
        pool = rules.tenant_pool(level, ROOM_KIND)
        assert pool and set(pool) <= set(rooms), level
        assert not set(rules.tenant_pool(level, "piso2")) & set(rooms), level
    assert not rules.tenant_pool(rules.VACATION_LEVEL, ROOM_KIND)
    assert not TENANT_BY_KEY["familia"].rents("referencia", ROOM_KIND)


def test_la_gestora_deja_la_media_de_cada_grupo_como_estaba() -> None:
    """Elige al azar: la media de los factores de quien puede salir es exactamente 1."""
    for level in RENT_LEVELS:
        for kind in ("piso2", ROOM_KIND, *sorted(BUSINESS_KINDS)):
            pool = rules.tenant_pool(level, kind)
            if not pool:
                continue
            factors = [rules.tenant_factors(t.key, level, kind) for t in pool]
            n = len(factors)
            assert sum(f.risk for f in factors) / n == pytest.approx(1.0), (level, kind)
            assert sum(f.stay for f in factors) / n == pytest.approx(1.0), (level, kind)
            assert sum(f.wear for f in factors) / n == pytest.approx(1.0), (level, kind)


def test_hay_inquilinos_de_cada_riesgo_para_elegir() -> None:
    tiers = {
        rules.risk_tier(rules.tenant_factors(t.key, "referencia", "piso2"))
        for t in rules.tenant_pool("referencia", "piso2")
    }
    assert tiers == {"bajo", "medio", "alto"}
    assert rules.tenant_factors("no_existe", "referencia", "piso2") == rules.NEUTRAL
    assert rules.tenant_factors(None, "referencia", "piso2") == rules.NEUTRAL


def test_solo_los_de_18_a_35_son_jovenes() -> None:
    assert rules.is_young("medico")  # 28 años
    assert not rules.is_young("abuela")
    assert not rules.is_young("franquicia")  # un negocio no tiene edad
    assert not rules.is_young(None)


# -- Reducción del 70 % ----------------------------------------------------------------


def test_el_70_es_primer_alquiler_en_zona_tensionada_a_un_joven() -> None:
    tense = rules.TENSE_INDEX + 0.01
    young = rules.contract_reduction(
        BAJO, "referencia", index=tense, rent=900, last_rent=0, young=True
    )
    assert young == rules.YOUNG_RENT_REDUCTION
    # Con contrato anterior ya no es la primera vez; fuera de zona tensionada, tampoco.
    assert (
        rules.contract_reduction(
            BAJO, "referencia", index=tense, rent=990, last_rent=1_000, young=True
        )
        == rules.RENT_REDUCTION
    )
    assert (
        rules.contract_reduction(BAJO, "referencia", index=1.0, rent=900, last_rent=0, young=True)
        == rules.RENT_REDUCTION
    )
    # Bajar la renta más de un 5 % sigue dando el 90 %, que es mayor.
    assert (
        rules.contract_reduction(
            BAJO, "referencia", index=tense, rent=900, last_rent=1_000, young=True
        )
        == rules.TENSE_RENT_REDUCTION
    )
    assert (
        rules.contract_reduction(
            LOCAL, "referencia", index=tense, rent=900, last_rent=0, young=True
        )
        == 0.0
    )


def test_firmar_con_un_joven_en_zona_tensionada_fija_el_70() -> None:
    tense = rules.TENSE_INDEX + 0.01
    lease = rules.sign(BAJO, rules.Lease(since=DAY), "medico", DAY, tense)
    assert lease.status == rules.RENTED and lease.tenant == "medico"
    assert lease.reduction == rules.YOUNG_RENT_REDUCTION
    older = rules.sign(BAJO, rules.Lease(since=DAY), "abuela", DAY, tense)
    assert older.reduction == rules.RENT_REDUCTION


# -- Elegir inquilino ----------------------------------------------------------------


def test_si_elige_el_dueno_llega_un_candidato_y_el_piso_sigue_vacio() -> None:
    lease = rules.Lease(since=DAY, pick=rules.PICK_SELF)
    out = rules.advance_day(BAJO, lease, DAY, 1.0, Scripted(fill=0.0))
    assert out.lease.status == rules.VACANT
    arrivals = [e for e in out.events if e.key == "candidato"]
    assert len(arrivals) == 1 and arrivals[0].tenant in TENANT_BY_KEY
    assert TENANT_BY_KEY[arrivals[0].tenant].rents("referencia", BAJO.kind)
    agency = rules.advance_day(BAJO, rules.Lease(since=DAY), DAY, 1.0, Scripted(fill=0.0))
    assert agency.lease.status == rules.RENTED  # la gestora firma sola, como siempre


def test_un_inquilino_de_riesgo_alto_deja_de_pagar_mas() -> None:
    def defaults(tenant: str) -> int:
        rng = random.Random(7)
        count = 0
        for offset in range(4_000):
            lease = rented(tenant, until=None)  # sin fin de contrato: solo cuenta el impago
            out = rules.advance_day(BAJO, lease, DAY + timedelta(days=offset), 1.0, rng)
            count += any(e.key == "impago" for e in out.events)
        return count

    assert defaults("musico") > 2 * defaults("funcionario")


# -- Seguro de impago y desahucio ------------------------------------------------------


def test_el_seguro_paga_desde_el_segundo_mes_y_hasta_doce() -> None:
    lease = rented("funcionario", insured=True, status=rules.DEFAULTING, unpaid=1)
    assert rules.covered(BAJO, lease)
    assert rules.insure_month(BAJO, lease)[0] == 0  # el primer mes es la franquicia
    paid, after = rules.insure_month(BAJO, replace(lease, unpaid=2))
    assert paid == lease.rent and after.claims == 1
    assert rules.insure_month(BAJO, replace(lease, unpaid=20, claims=rules.COVER_MONTHS))[0] == 0
    trial = replace(lease, status=rules.EVICTING, unpaid=1)
    assert rules.insure_month(BAJO, trial)[0] == lease.rent  # durante el juicio, también
    assert rules.insure_month(BAJO, replace(lease, insured=False, unpaid=3))[0] == 0


def test_el_seguro_no_cubre_al_riesgo_alto_ni_lo_que_no_es_vivienda_habitual() -> None:
    risky = max(
        rules.tenant_pool("referencia", "piso2"),
        key=lambda t: rules.tenant_factors(t.key, "referencia", "piso2").risk,
    )
    assert not rules.insurable(BAJO, rented(risky.key))
    assert rules.insurable(BAJO, rented("funcionario"))
    assert not rules.insurable(BAJO, rented("aleman", level="nomadas"))
    assert not rules.insurable(LOCAL, rented("franquicia"))
    member = rules.Lease(status=rules.RENTED, member=ANA, rent=4_000, solvent=False)
    assert not rules.insurable(BAJO, member)
    assert rules.insurable(BAJO, replace(member, solvent=True))


def test_la_prima_sube_con_lo_que_se_impaga_y_solo_con_contrato() -> None:
    assert rules.premium_rate("referencia") == rules.PREMIUM_RATE
    assert rules.premium_rate("social") < rules.premium_rate("referencia")
    assert rules.premium_rate("mercado") > rules.premium_rate("referencia")
    lease = rented("funcionario", insured=True)
    assert rules.premium(BAJO, lease) == round(lease.rent * rules.PREMIUM_RATE)
    assert rules.premium(BAJO, replace(lease, insured=False)) == 0
    assert rules.premium(BAJO, rules.Lease(insured=True)) == 0  # vacío: sin contrato


def test_quien_se_pone_al_dia_le_devuelve_al_seguro_lo_que_adelanto() -> None:
    lease = rented("funcionario", insured=True, status=rules.DEFAULTING, unpaid=4, claims=3)
    # Seis imprevistos que no pasan y el inquilino se pone al día.
    out = rules.advance_day(BAJO, lease, DAY, 1.0, Scripted([0.99] * 6 + [0.0]))
    assert any(e.key == "paga" for e in out.events)
    assert out.rent == lease.rent * (4 + 1 - 3)
    assert out.lease.claims == 0


def test_el_abogado_del_desahucio_va_de_3000_a_15000() -> None:
    assert rules.eviction_fee(100) == rules.EVICTION_FEE_MIN
    assert rules.eviction_fee(4_000) == 4_000 * rules.EVICTION_FEE_MONTHS
    assert rules.eviction_fee(100_000) == rules.EVICTION_FEE_MAX


def test_la_renta_paga_primero_al_estado_y_lo_que_falta_se_debe() -> None:
    costs = rules.MonthlyCosts(community=100, insurance=50, ibi=30)
    full = rules.settle_day(1_000, costs, 0, 0, 0, taxes=20)
    assert (full.ibi, full.taxes, full.costs, full.net) == (30, 20, 150, 800)
    short = rules.settle_day(40, costs, 0, 0, 0, taxes=20)
    assert (short.ibi, short.taxes, short.taxes_left) == (30, 10, 10)
    assert short.arrears_ibi == 10 and short.arrears == 10 + 150


# -- Deducciones canarias del casero ---------------------------------------------------


def test_cada_inmueble_aplica_la_mayor_de_las_deducciones_del_casero() -> None:
    upkeep = PropertyWeek(ref="a", upkeep=1_000)
    insured = PropertyWeek(ref="a", upkeep=1_000, premium=400)
    assert landlord_deduction((upkeep,)) == round(1_000 * 0.10)
    assert landlord_deduction((insured,)) == round(400 * 0.75)  # la 15 quater, mayor
    bonus = PropertyWeek(ref="a", upkeep=1_000, premium=400, bonus=vacant_rental_deduction(100))
    assert landlord_deduction((bonus,)) == vacant_rental_deduction(100)
    capped = PropertyWeek(ref="a", upkeep=10**9, premium=10**9)
    assert landlord_deduction((capped,)) == max(
        round(weekly_yd(LANDLORD_DEDUCTION_MAX_EUR)), round(weekly_yd(INSURANCE_DEDUCTION_MAX_EUR))
    )
    assert vacant_rental_deduction(50) == round(vacant_rental_deduction(100) / 2)


# -- Noticias de barrio ----------------------------------------------------------------


def test_cada_noticia_puede_salir_en_algun_barrio_y_mes() -> None:
    for news in BARRIO_NEWS:
        assert news.title and news.blurb, news.key
        assert 0.97 <= news.index <= 1.03, news.key  # muy por debajo de la ida y vuelta
        assert any(
            rules.news_fits(news, barrio, date(2026, month, 1))
            for barrio in BARRIO_BY_KEY
            for month in range(1, 13)
        ), news.key


def test_las_noticias_del_lunes_respetan_el_tope_y_los_barrios_ocupados() -> None:
    always = Scripted(fill=0.0)
    out = rules.monday_news(DAY, set(), always)
    assert len(out) == rules.NEWS_MAX
    busy = {barrio for barrio, _key in out}
    again = rules.monday_news(DAY, busy, Scripted(fill=0.0))
    assert not busy & {barrio for barrio, _key in again}
    for barrio, key in out:
        assert rules.news_fits(BARRIO_NEWS_BY_KEY[key], barrio, DAY)
    assert rules.monday_news(DAY, set(), Scripted(fill=0.99)) == []


def test_una_noticia_cambia_lo_que_cuesta_encontrar_inquilino() -> None:
    level = RENT_LEVELS["referencia"]
    good = BARRIO_NEWS_BY_KEY["supermercado"]
    base = rules.find_chance(BAJO, level, DAY)
    assert rules.find_chance(BAJO, level, DAY, good) == pytest.approx(base * good.find)
    nomads = RENT_LEVELS["nomadas"]
    fibra = BARRIO_NEWS_BY_KEY["fibra"]
    assert rules.find_chance(BAJO, nomads, DAY, fibra) > rules.find_chance(BAJO, nomads, DAY)


# -- Con la base de datos --------------------------------------------------------------


async def test_elegir_inquilino_guarda_candidatos_y_se_firma_con_uno(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, rng=Scripted(fill=0.0))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with pytest.raises(HousingError):
        await service.set_pick(GUILD, BEA, BAJO.key, rules.PICK_SELF)  # no es suyo
    await service.set_pick(GUILD, ANA, BAJO.key, rules.PICK_SELF)
    clock.now = at(15).timestamp()

    runs = await service.close_days(GUILD)

    market = await service.market(GUILD)
    waiting = market.candidates_of(BAJO.key, date(2026, 10, 15))
    assert market.leases[BAJO.key].status == rules.VACANT
    assert len(waiting) == 1 and runs[0].candidates == [(BAJO.key, waiting[0])]
    signing = await service.sign_candidate(GUILD, ANA, BAJO.key, waiting[0])
    assert signing.lease.status == rules.RENTED and signing.tenant == waiting[0]
    assert signing.first and signing.risk in ("bajo", "medio", "alto")
    market = await service.market(GUILD)
    assert market.candidates_of(BAJO.key, date(2026, 10, 15)) == ()
    with pytest.raises(HousingError):
        await service.sign_candidate(GUILD, ANA, BAJO.key, waiting[0])


async def test_los_candidatos_se_van_a_los_tres_dias(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, rng=Scripted(fill=0.0))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.set_pick(GUILD, ANA, BAJO.key, rules.PICK_SELF)
    clock.now = at(15).timestamp()
    await service.close_days(GUILD)
    first = (await service.market(GUILD)).candidates_of(BAJO.key, date(2026, 10, 15))
    await service.discard_candidate(GUILD, ANA, BAJO.key, first[0])
    assert (await service.market(GUILD)).candidates_of(BAJO.key, date(2026, 10, 15)) == ()
    # Volver a la gestora borra los que esperan.
    clock.now = at(16).timestamp()
    await service.close_days(GUILD)
    assert (await service.market(GUILD)).candidates_of(BAJO.key, date(2026, 10, 16))
    await service.set_pick(GUILD, ANA, BAJO.key, rules.PICK_AGENCY)
    assert (await service.market(GUILD)).candidates.get(BAJO.key, ()) == ()


async def test_el_seguro_de_impago_cobra_prima_con_su_impuesto_y_paga(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.set_insurance(GUILD, ANA, BAJO.key, True)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'impago', tenant = 'funcionario', rent = 8000, "
            "unpaid = 1, until = '2027-01-01'"
        )
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    clock.now = at(15).timestamp()

    runs = await service.close_days(GUILD)

    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.claims == 1 and runs[0].insurer == {ANA: 8_000}
    costs = rules.monthly_costs(BAJO)
    premium = round(8_000 * rules.PREMIUM_RATE)
    ips = round(costs.insurance * rules.IPS_RATE) + round(premium * rules.IPS_RATE)
    assert housing_taxes(tmp_path, "ips") == ips
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state >= costs.ibi + ips
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)


async def test_desahuciar_cuesta_abogado_salvo_con_seguro(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'impago', tenant = 'funcionario', rent = 4000, "
            "unpaid = 3, until = '2027-01-01'"
        )
    balance = await economy.balance(GUILD, ANA)
    eviction, after = await service.evict(GUILD, ANA, BAJO.key)
    fee = rules.eviction_fee(4_000)
    assert (eviction.fee, eviction.insured) == (fee, False)
    assert eviction.fee_igic == round(fee * rules.SERVICES_IGIC)
    assert after == balance - fee - eviction.fee_igic
    assert housing_taxes(tmp_path, "igic_gastos") >= eviction.fee_igic
    assert ledger_sum(tmp_path, ANA) == after
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (costs,) = connection.execute(
            "SELECT COALESCE(SUM(costs), 0) FROM economy_tax_records WHERE user_id = ? AND ref = ?",
            (ANA, BAJO.key),
        ).fetchone()
    assert costs == fee + eviction.fee_igic  # gasto del inmueble deducible (art. 13.c RIRPF)


async def test_con_seguro_el_abogado_no_cuesta_y_sin_dinero_no_hay_juicio(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.set_insurance(GUILD, ANA, BAJO.key, True)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'impago', tenant = 'funcionario', rent = 4000, "
            "unpaid = 3, until = '2027-01-01'"
        )
    eviction, _balance = await service.evict(GUILD, ANA, BAJO.key)
    assert eviction.insured and eviction.fee == 0
    # Sin seguro y sin dinero, el juzgado no recibe la demanda.
    await service.set_insurance(GUILD, ANA, BAJO.key, False)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_leases SET status = 'impago', eviction_at = NULL")
    await give(economy, ANA, -(await economy.balance(GUILD, ANA)))
    with pytest.raises(InsufficientFundsError):
        await service.evict(GUILD, ANA, BAJO.key)
    assert (await service.market(GUILD)).leases[BAJO.key].status == rules.DEFAULTING


async def test_alquilar_una_vivienda_vacia_da_la_deduccion_del_16_una_vez(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, rng=Scripted(fill=0.0))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)  # se compra vacío: se alquila en 6 días
    clock.now = at(15).timestamp()
    await service.close_days(GUILD)  # la gestora firma (el azar siempre da 0)
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.status == rules.RENTED and lease.bonus
    clock.now = at(16).timestamp()
    service.rng = Scripted(fill=0.99)  # paga y no se rompe nada
    runs = await service.close_days(GUILD)  # la primera renta del contrato la aplica
    assert runs[0].bonus == {ANA: vacant_rental_deduction(100)}
    market = await service.market(GUILD)
    assert not market.leases[BAJO.key].bonus and BAJO.key in market.bonuses[ANA]
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (bonus,) = connection.execute(
            "SELECT SUM(bonus) FROM economy_tax_records WHERE user_id = ?", (ANA,)
        ).fetchone()
    assert bonus == vacant_rental_deduction(100)


async def test_las_noticias_del_lunes_se_guardan_y_mueven_poco_el_indice(tmp_path: Path) -> None:
    clock = Clock(at(19))  # lunes 19/10
    economy, service = await make(tmp_path, clock, rng=Scripted(fill=0.0))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    clock.now = at(20).timestamp()

    runs = await service.close_days(GUILD)

    assert len(runs[0].barrio_news) == rules.NEWS_MAX
    market = await service.market(GUILD)
    for barrio, key in runs[0].barrio_news:
        assert market.news_of(barrio, date(2026, 10, 19)).key == key
        assert market.news_of(barrio, date(2026, 10, 25)) is not None
        assert market.news_of(barrio, date(2026, 10, 26)) is None
        assert 0.97 <= market.index(barrio) <= 1.12


def test_los_tipos_nuevos_tienen_comunidad() -> None:
    # Una comprobación barata de que el catálogo de tipos no cambia con la fase 2.
    assert all(kind.community >= 0 for kind in KINDS.values())
