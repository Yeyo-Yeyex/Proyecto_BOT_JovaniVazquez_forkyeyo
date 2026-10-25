"""Pruebas de la 9.23.0 de `pisos`: la duración del contrato y recuperar el piso.

Elegir la duración y lo que cuesta firmar corto, no renovar (con la prórroga de la Ley de
Vivienda en zona tensionada), comprar las llaves a un inquilino del catálogo (por las buenas,
la justa, la generosa, su contraoferta y atrincherarse), comprárselas a un miembro con su
IRPF, la fama de casero y la necesidad del art. 9.3 LAU con su indemnización.
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
from bot.repositories.housing_finance import HOME_OWN
from bot.services import housing as rules
from bot.services.housing_catalog import LISTING_BY_KEY, RENT_LEVELS, ROOM_KIND
from bot.services.taxes import capital_gain_tax
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
REF = RENT_LEVELS["referencia"]
PISO = next(item for item in LISTING_BY_KEY.values() if item.kind == "piso2")


def rented(tenant: str = "funcionario", **changes: object) -> rules.Lease:
    lease = rules.Lease(
        status=rules.RENTED, level="referencia", tenant=tenant, rent=4_000,
        until=DAY + timedelta(days=48), since=DAY - timedelta(days=12),
        signed=DAY - timedelta(days=12),
    )  # fmt: skip
    return replace(lease, **changes)  # type: ignore[arg-type]


def sql(tmp_path: Path, query: str, *args: object) -> None:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(query, args)


# -- Duración del contrato ------------------------------------------------------------------


def test_la_duracion_se_elige_en_vivienda_y_local_pero_no_en_temporada_ni_habitaciones() -> None:
    assert rules.term_choices(PISO, REF) == rules.TERMS
    assert rules.contract_months(PISO, REF) == 60 == rules.contract_months(PISO, REF, 0)
    assert rules.contract_months(PISO, REF, 12) == 12
    assert not rules.term_choices(PISO, RENT_LEVELS["nomadas"])
    assert rules.contract_months(PISO, RENT_LEVELS["nomadas"], 24) == 8
    room = replace(PISO, kind=ROOM_KIND)
    assert not rules.term_choices(room, REF)
    assert rules.contract_months(room, REF, 48) == rules.ROOM_MONTHS


def test_firmar_corto_espanta_candidatos_y_atrae_impagos() -> None:
    find, risk = rules.term_factors(PISO, REF, 12)
    assert (find, risk) == pytest.approx((1 - rules.TERM_FIND_CUT, 1 + rules.TERM_RISK_UP))
    assert rules.term_factors(PISO, REF, 60) == (1.0, 1.0)
    assert rules.term_factors(PISO, REF, 0) == (1.0, 1.0)  # los de antes de la 9.23
    finds = [rules.term_factors(PISO, REF, t)[0] for t in rules.TERMS]
    assert finds == sorted(finds)


def test_el_contrato_dura_lo_elegido_y_renueva_por_lo_elegido() -> None:
    signed = rules.sign(PISO, rules.Lease(term=24), "funcionario", DAY, 1.0)
    assert signed.until == DAY + timedelta(days=24) and signed.signed == DAY
    end = signed.until
    renewed, event = rules.contract_end(PISO, replace(signed, term=36), end, 1.0, Scripted([0.0]),
                                        0.7)  # fmt: skip
    assert event.key == "renueva" and renewed.until == end + timedelta(days=36)


# -- No renovar ------------------------------------------------------------------------------


def test_no_renovar_le_hace_irse_al_acabar() -> None:
    lease = rented(leaving=rules.LEAVE_NO_RENEW)
    gone, event = rules.contract_end(PISO, lease, lease.until, 1.0, Scripted([0.0]), 1.0)
    assert event.key == "no_renovado" and event.tenant == "funcionario"
    assert gone.status == rules.VACANT and not gone.leaving and gone.signed is None


def test_en_zona_tensionada_pide_la_prorroga_una_vez() -> None:
    lease = rented(leaving=rules.LEAVE_NO_RENEW)
    tense = rules.TENSE_INDEX
    kept, event = rules.contract_end(PISO, lease, lease.until, tense, Scripted([0.0]), 1.0)
    assert event.key == "prorroga" and kept.leaving == rules.LEAVE_EXTENDED
    assert kept.until == lease.until + timedelta(days=rules.EXTENSION_DAYS)
    gone, event = rules.contract_end(PISO, kept, kept.until, tense, Scripted([0.0]), 1.0)
    assert event.key == "no_renovado" and gone.status == rules.VACANT
    # Sin zona tensionada, ni la pide.
    _gone, event = rules.contract_end(PISO, lease, lease.until, 1.0, Scripted([0.0]), 1.0)
    assert event.key == "no_renovado"


def test_los_contratos_de_antes_siguen_igual_con_el_mismo_azar() -> None:
    """Sin elegir nada, un contrato de la 9.22 gasta el mismo azar y renueva igual."""
    lease = rented(signed=None)
    kept, event = rules.contract_end(PISO, lease, lease.until, 1.2, Scripted([0.5]), 0.7)
    assert event.key == "renueva" and kept.until == lease.until + timedelta(days=60)
    gone, event = rules.contract_end(PISO, lease, lease.until, 1.2, Scripted([0.8]), 0.7)
    assert event.key == "se_va" and gone.status == rules.VACANT


# -- El precio de las llaves -----------------------------------------------------------------


def test_el_precio_justo_es_medio_mes_por_cada_ano_que_le_queda() -> None:
    assert rules.keys_fair(1_000, 48, 1.0, rules.MOOD_BASE, 0) == 2_000
    assert rules.keys_fair(1_000, 6, 1.0, rules.MOOD_BASE, 0) == 1_000  # un mes como mínimo
    # Quien se iría solo pide menos; quien no se movería, más (con tope).
    assert rules.keys_fair(1_000, 48, 3.0, rules.MOOD_BASE, 0) == 1_400
    assert rules.keys_fair(1_000, 48, 0.1, rules.MOOD_BASE, 0) == 3_000
    # Contento pide más; enfadado no pide menos.
    assert rules.keys_fair(1_000, 48, 1.0, 100, 0) == 2_600
    assert rules.keys_fair(1_000, 48, 1.0, 0, 0) == 2_000
    # La fama encarece; y si los alquileres han subido, pide la mitad de lo que le costaría.
    assert rules.keys_fair(1_000, 48, 1.0, rules.MOOD_BASE, 2) == 2_800
    assert rules.keys_fair(1_000, 48, 1.0, rules.MOOD_BASE, 0, market_rent=1_100) == 4_400


def test_la_justa_la_aceptan_6_de_10_y_la_generosa_9_de_10() -> None:
    assert rules.keys_odds(10_000, 10_000) == pytest.approx(0.6, abs=0.01)
    assert rules.keys_odds(15_000, 10_000) == pytest.approx(0.9, abs=0.01)
    assert rules.keys_odds(0, 10_000) == 0
    reserves = [rules.keys_reserve(10_000, str(i)) for i in range(4_000)]
    assert sum(r <= 10_000 for r in reserves) / 4_000 == pytest.approx(0.6, abs=0.03)
    assert sum(r <= 15_000 for r in reserves) / 4_000 == pytest.approx(0.9, abs=0.03)
    # Volver a preguntar no cambia lo que pide.
    assert rules.keys_reserve(10_000, "x") == rules.keys_reserve(10_000, "x")


def test_por_las_buenas_depende_de_las_ganas_de_irse_y_la_fama_lo_apaga() -> None:
    assert rules.free_odds(1.0, 48, 0) == pytest.approx(0.18)
    assert rules.free_odds(3.0, 2, 0) == rules.free_odds(9.0, 2, 0) == 0.35
    assert rules.free_odds(0.1, 60, 0) == pytest.approx(0.06)
    assert rules.free_odds(0.0, 60, 0) == 0.05
    assert rules.free_odds(3.0, 2, 1) == 0


def test_lo_que_contesta_el_inquilino() -> None:
    yes = rules.keys_answer(rules.KEYS_FREE, 2_000, 1_500, 0.3, "medio", Scripted([0.1]))
    assert yes.accepted and yes.paid == 0
    no = rules.keys_answer(rules.KEYS_FREE, 2_000, 1_500, 0.3, "medio", Scripted([0.5]))
    assert not no.accepted and no.ask == 1_500 and not no.trench
    rich = rules.keys_answer(rules.KEYS_RICH, 2_000, 2_900, 0.3, "alto", Scripted([0.0]))
    assert rich.accepted and rich.paid == 3_000
    # Uno de riesgo alto al que le ofreces poco puede atrincherarse; con la generosa, no.
    trench = rules.keys_answer(rules.KEYS_FAIR, 2_000, 2_500, 0.3, "alto", Scripted([0.0]))
    assert trench.trench and not trench.accepted and trench.ask == 0
    calm = rules.keys_answer(rules.KEYS_RICH, 2_000, 9_000, 0.3, "alto", Scripted([0.0]))
    assert not calm.trench and calm.ask == 9_000
    lease = rules.keys_refused(rented(), DAY, trench)
    assert lease.status == rules.DEFAULTING and lease.unpaid == 1
    assert lease.mood == rules.MOOD_BASE - rules.KEYS_MOOD_HIT
    assert lease.keys_wait == DAY + timedelta(days=rules.KEYS_WAIT_DAYS)


def test_quien_acepta_se_va_en_dos_dias_o_cuando_acabe_si_es_antes() -> None:
    lease = rules.leaves_in(rented(keys_ask=900), DAY, rules.LEAVE_KEYS, rules.KEYS_MOVE_DAYS)
    assert lease.until == DAY + timedelta(days=2) and lease.leaving == rules.LEAVE_KEYS
    assert lease.keys_ask == 0 and not rules.recoverable(lease)
    soon = rules.leaves_in(rented(until=DAY + timedelta(days=1)), DAY, rules.LEAVE_KEYS, 2)
    assert soon.until == DAY + timedelta(days=1)


def test_la_necesidad_pide_un_ano_de_contrato_y_que_sea_para_vivir() -> None:
    assert rules.need_problem(BAJO, rented(), DAY) is None
    assert "un año" in (rules.need_problem(BAJO, rented(signed=DAY), DAY) or "")
    assert rules.need_problem(BAJO, rented(level="nomadas"), DAY) is not None
    local = next(item for item in LISTING_BY_KEY.values() if item.kind == "local")
    assert rules.need_problem(local, rented(), DAY) is not None
    assert rules.need_fine(1_000, 48) == 4_000 and rules.need_fine(1_000, 2) == 1_000


# -- Con la base de datos --------------------------------------------------------------------


async def _rented(tmp_path: Path, rng: random.Random | None = None, **sets: object):
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, rng=rng)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    values = {
        "status": "alquilado", "tenant": "funcionario", "rent": 4_000, "until": "2026-12-01",
        "signed": "2026-09-01", **sets,
    }  # fmt: skip
    assigns = ", ".join(f"{k} = ?" for k in values)
    sql(tmp_path, f"UPDATE housing_leases SET {assigns}", *values.values())
    return clock, economy, service


async def _signed_so(tmp_path: Path, service, accepts: bool) -> rules.KeysQuote:
    """Busca un día de firma con el que lo que pide de verdad (fijo para cada contrato) quede
    por debajo de la oferta generosa (`accepts`) o por encima: así la prueba no es de azar."""
    for day in range(1, 29):
        sql(tmp_path, "UPDATE housing_leases SET signed = ?", f"2026-09-{day:02d}")
        quote = (await service.market(GUILD)).keys_quote(BAJO.key, at(14).date())
        assert quote is not None
        rich = rules.keys_offer_amount(rules.KEYS_RICH, quote.fair)
        if (quote.reserve <= rich) == accepts:
            return quote
    raise AssertionError("ningún día de firma sirve")


async def test_comprar_las_llaves_paga_y_suma_al_coste_del_piso(tmp_path: Path) -> None:
    _clock, economy, service = await _rented(tmp_path, rng=Scripted(fill=0.99))
    quote = await _signed_so(tmp_path, service, accepts=True)
    market = await service.market(GUILD)
    assert quote.fame == 0
    cost = market.holdings[BAJO.key][ANA].cost
    balance = await economy.balance(GUILD, ANA)

    deal, after = await service.offer_keys(GUILD, ANA, BAJO.key, rules.KEYS_RICH)

    price = rules.keys_offer_amount(rules.KEYS_RICH, quote.fair)
    assert deal.answer.accepted and deal.answer.paid == price
    assert after == balance - price == ledger_sum(tmp_path, ANA)
    market = await service.market(GUILD)
    assert market.holdings[BAJO.key][ANA].cost == cost + price  # una mejora
    assert market.fame(ANA, at(14).date()) == 1
    lease = market.leases[BAJO.key]
    assert lease.leaving == rules.LEAVE_KEYS and lease.until == at(16).date()
    assert not rules.recoverable(lease)


async def test_si_pide_mas_de_la_generosa_contraoferta_y_no_paga_nada(tmp_path: Path) -> None:
    _clock, economy, service = await _rented(tmp_path, rng=Scripted(fill=0.99))
    quote = await _signed_so(tmp_path, service, accepts=False)
    balance = await economy.balance(GUILD, ANA)
    deal, after = await service.offer_keys(GUILD, ANA, BAJO.key, rules.KEYS_RICH)
    assert not deal.answer.accepted and deal.answer.ask == quote.reserve and after == balance


async def test_si_dice_que_no_contraoferta_y_se_le_puede_pagar_o_dejar(
    tmp_path: Path,
) -> None:
    clock, economy, service = await _rented(tmp_path, rng=Scripted(fill=0.99))
    deal, _balance = await service.offer_keys(GUILD, ANA, BAJO.key, rules.KEYS_FREE)
    assert not deal.answer.accepted and deal.answer.ask > 0
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.keys_ask == deal.answer.ask and lease.keys_wait is not None
    with pytest.raises(HousingError, match="ni oír hablar"):
        await service.offer_keys(GUILD, ANA, BAJO.key, rules.KEYS_FAIR)
    await service.drop_keys_ask(GUILD, ANA, BAJO.key)
    with pytest.raises(HousingError):
        await service.accept_keys_ask(GUILD, ANA, BAJO.key)
    # Otra vez, y ahora se le paga lo que pide.
    clock.now = at(22).timestamp()
    deal, _ = await service.offer_keys(GUILD, ANA, BAJO.key, rules.KEYS_FREE)
    balance = await economy.balance(GUILD, ANA)
    deal, after = await service.accept_keys_ask(GUILD, ANA, BAJO.key)
    assert deal.answer.accepted and after == balance - deal.answer.paid
    news = await service.news(GUILD, (BAJO.key,), 10)
    assert {"rechaza_llaves", "llaves"} <= {n.kind for n in news}


async def test_sin_dinero_no_se_ofrece_nada(tmp_path: Path) -> None:
    _clock, economy, service = await _rented(tmp_path, rng=Scripted(fill=0.99))
    await _signed_so(tmp_path, service, accepts=True)
    await give(economy, ANA, -(await economy.balance(GUILD, ANA)))
    with pytest.raises(InsufficientFundsError):
        await service.offer_keys(GUILD, ANA, BAJO.key, rules.KEYS_RICH)  # aceptaría
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.leaving == "" and lease.status == rules.RENTED


async def test_no_renovar_y_echarse_atras(tmp_path: Path) -> None:
    _clock, _economy, service = await _rented(tmp_path)
    lease = await service.set_no_renew(GUILD, ANA, BAJO.key, True)
    assert lease.leaving == rules.LEAVE_NO_RENEW
    with pytest.raises(HousingError):
        await service.set_no_renew(GUILD, ANA, BAJO.key, True)
    lease = await service.set_no_renew(GUILD, ANA, BAJO.key, False)
    assert lease.leaving == ""
    with pytest.raises(HousingError):
        await service.set_no_renew(GUILD, BEA, BAJO.key, True)  # no es suyo


async def test_la_duracion_la_elige_quien_manda_y_solo_si_se_puede(tmp_path: Path) -> None:
    _clock, _economy, service = await _rented(tmp_path)
    lease = await service.set_term(GUILD, ANA, BAJO.key, 24)
    assert lease.term == 24
    with pytest.raises(HousingError):
        await service.set_term(GUILD, ANA, BAJO.key, 30)
    with pytest.raises(HousingError):
        await service.set_term(GUILD, BEA, BAJO.key, 12)
    await service.set_level(GUILD, ANA, BAJO.key, "nomadas")
    with pytest.raises(HousingError, match="no se elige"):
        await service.set_term(GUILD, ANA, BAJO.key, 12)


async def test_la_noche_echa_al_que_no_renueva_y_al_que_vendio_las_llaves(
    tmp_path: Path,
) -> None:
    clock, _economy, service = await _rented(
        tmp_path, rng=Scripted(fill=0.99), until="2026-10-14", leaving="llaves"
    )
    clock.now = at(15).timestamp()  # cierra el día 14, el último del contrato
    runs = await service.close_days(GUILD)
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.status == rules.VACANT and not lease.leaving
    assert "sale_llaves" in {n.kind for n in runs[0].news}


async def test_la_necesidad_le_muda_y_si_se_va_antes_paga(tmp_path: Path) -> None:
    clock, economy, service = await _rented(tmp_path, rng=Scripted(fill=0.99))
    lease, fine = await service.claim_need(GUILD, ANA, BAJO.key)
    assert lease.leaving == rules.LEAVE_NEED and lease.until == at(16).date()
    assert fine == rules.need_fine(4_000, (date(2026, 12, 1) - at(14).date()).days)
    with pytest.raises(HousingError):
        await service.claim_need(GUILD, ANA, BAJO.key)
    with pytest.raises(HousingError, match="para vivir"):
        await service.sell(GUILD, ANA, BAJO.key, 100)
    for d in (15, 16, 17):  # el día 16 se va y esa noche se muda Ana
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    market = await service.market(GUILD)
    assert market.homes[ANA] == (HOME_OWN, BAJO.key)
    assert market.leases[BAJO.key].status == rules.HABITUAL
    assert market.reclaims[BAJO.key].until == at(16).date() + timedelta(days=rules.NEED_STAY_DAYS)
    with pytest.raises(HousingError, match="indemnización"):
        await service.leave_home(GUILD, ANA)
    balance = await economy.balance(GUILD, ANA)
    paid, after = await service.leave_home_paying(GUILD, ANA)
    assert paid == fine and after == balance - fine == ledger_sum(tmp_path, ANA)
    market = await service.market(GUILD)
    assert ANA not in market.homes and BAJO.key not in market.reclaims


async def test_quien_cumple_los_doce_dias_se_va_gratis(tmp_path: Path) -> None:
    clock, _economy, service = await _rented(tmp_path, rng=Scripted(fill=0.99))
    await service.claim_need(GUILD, ANA, BAJO.key)
    for d in range(15, 30):
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    market = await service.market(GUILD)
    assert BAJO.key not in market.reclaims
    assert await service.leave_home(GUILD, ANA) == HOME_OWN


async def test_mientras_espera_no_se_va_a_vivir_a_otro_sitio(tmp_path: Path) -> None:
    _clock, economy, service = await _rented(tmp_path)
    other = next(
        item for item in LISTING_BY_KEY.values()
        if item.kind == "estudio" and item.key != BAJO.key
    )  # fmt: skip
    await service.buy(GUILD, ANA, other.key, 100)
    await service.claim_need(GUILD, ANA, BAJO.key)
    with pytest.raises(HousingError, match="espera a que se vaya"):
        await service.set_home(GUILD, ANA, other.key)
    assert await economy.balance(GUILD, ANA) > 0


async def test_comprarle_las_llaves_a_un_miembro_paga_su_irpf(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, rng=Scripted(fill=0.99))
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 1_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    ana, bea = await economy.balance(GUILD, ANA), await economy.balance(GUILD, BEA)

    deal = await service.member_keys(GUILD, ANA, BAJO.key, BEA, 20_000)

    tax = capital_gain_tax(20_000)
    assert deal.tax == tax and deal.lease.leaving == rules.LEAVE_KEYS
    assert await economy.balance(GUILD, ANA) == ana - 20_000
    assert await economy.balance(GUILD, BEA) == bea + 20_000 - tax
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == state + tax
    assert housing_taxes(tmp_path, "plusvalia") == tax
    with pytest.raises(HousingError):
        await service.member_keys(GUILD, ANA, BAJO.key, BEA, 1)  # ya se va
    for d in (15, 16, 17):
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    market = await service.market(GUILD)
    assert BEA not in market.homes and market.leases[BAJO.key].status == rules.VACANT


async def test_el_miembro_firma_lo_que_dure_el_contrato_elegido(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 1_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.set_term(GUILD, ANA, BAJO.key, 12)
    deal = await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    assert deal.lease.until == at(14).date() + timedelta(days=12)
    assert deal.lease.signed == at(14).date()


def test_la_ficha_avisa_de_que_se_va_y_cuando() -> None:
    from bot.repositories.housing import Holding, Market
    from bot.services import housing_view as view

    key = BAJO.key
    holding = Holding(key, ANA, 100, 1_000_000, 1_000_000, 0.0)
    for leaving, start in (
        (rules.LEAVE_NO_RENEW, "No renueva"), (rules.LEAVE_EXTENDED, "En prórroga"),
        (rules.LEAVE_KEYS, "Llaves pagadas"), (rules.LEAVE_NEED, "Lo pides para vivir"),
    ):  # fmt: skip
        market = Market(holdings={key: {ANA: holding}}, leases={key: rented(leaving=leaving)})
        state = view.listing_state(market, key, ANA, {ANA: "Ana"}, [], DAY)
        assert state["leaving"].startswith(start) and "01/12" in state["leaving"]
    market = Market(holdings={key: {ANA: holding}}, leases={key: rented()})
    assert view.listing_state(market, key, ANA, {ANA: "Ana"}, [], DAY)["leaving"] == ""
