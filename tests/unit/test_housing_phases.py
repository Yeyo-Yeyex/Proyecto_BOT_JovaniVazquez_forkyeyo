"""Pruebas de las fases 2 a 4 de `pisos`: hipoteca, VPO, vivienda habitual, miembros de
alquiler y bono, vacacional, gran tenedor, fondo buitre y SOCIMI."""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from bot.repositories.economy import (
    STATE_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.repositories.housing import HousingError, HousingRepository, assets_in
from bot.repositories.housing_finance import HOME_OWN, HOME_RENTED, HOME_VPO
from bot.services import housing as rules
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.housing_catalog import LISTING_BY_KEY, VPO_BY_KEY, VPO_UNITS
from bot.services.housing_market import HousingService
from bot.services.levels import TIMEZONE
from bot.services.work import Contract, battery_now

GUILD = 1
ANA = 10
BEA = 20
BAJO = LISTING_BY_KEY["schamann_bajo"]


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now

    def to(self, d: int, hour: int = 12) -> None:
        self.now = at(d, hour).timestamp()


class Quiet(random.Random):
    """Azar sin sorpresas: ningún imprevisto, nadie encuentra piso, el primero gana."""

    def random(self) -> float:
        return 0.999

    def choice(self, seq):  # noqa: ANN001, ANN201
        return seq[0]


def at(d: int, hour: int = 12) -> datetime:
    """Octubre de 2026: los lunes son 5, 12, 19 y 26."""
    return datetime(2026, 10, d, hour, tzinfo=TIMEZONE)


async def make(tmp_path: Path, clock: Clock) -> tuple[EconomyService, HousingService]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    housing_repo = HousingRepository(tmp_path / "bot.db")
    await housing_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    return economy, HousingService(housing_repo, economy, clock=clock, rng=Quiet())


async def give(economy: EconomyService, user_id: int, amount: int) -> None:
    await economy.repository.apply(GUILD, user_id, [LedgerEntry(amount, "prueba")])


def salary(tmp_path: Path, user_id: int, net_per_day: int, clock: Clock) -> None:
    """Nóminas de los últimos 30 días que suman `net_per_day` × 30 (lo que mira el banco)."""
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
            "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, ?, ?)",
            (GUILD, user_id, net_per_day * 30, net_per_day * 30, clock.now - 3600),
        )


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


def sql(tmp_path: Path, query: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(query, args).fetchall()


# -- Reglas ----------------------------------------------------------------------------


def test_la_cuota_francesa_devuelve_el_prestamo_en_360_meses() -> None:
    principal, rate = 347_200, 0.032
    payment = rules.installment(principal, rate)
    balance = principal
    for _ in range(rules.MORTGAGE_MONTHS):
        _interest, amortized = rules.mortgage_month(balance, rate, payment)
        balance -= amortized
    assert balance <= payment  # el redondeo hacia arriba la acaba un pelín antes
    assert rules.installment(0, rate) == 0
    assert rules.installment(36_000, 0.0) == 100


def test_la_cuota_de_un_bajo_o_una_vpo_cabe_en_el_sueldo_de_un_celador() -> None:
    celador = 6_000  # neto de cuatro turnos del puesto 1, a la escala de las nóminas
    payment = rules.installment(round(rules.price(BAJO) * rules.MORTGAGE_LTV), 0.032)
    assert rules.affordable(celador, payment)
    vpo = max(VPO_UNITS, key=lambda unit: unit.price)
    assert rules.affordable(celador, rules.installment(vpo.price, 0.032))
    assert celador <= rules.vpo_income_cap() and celador <= rules.bono_income_cap()


def test_el_banco_ejecuta_con_doce_cuotas_o_el_tres_por_ciento() -> None:
    assert not rules.must_execute(11, 100, 1_000_000)
    assert rules.must_execute(12, 100, 1_000_000)
    assert rules.must_execute(1, 30_000, 1_000_000)


def test_el_bono_es_250_euros_como_mucho_y_media_renta() -> None:
    assert rules.bono_amount(10_000) == rules.BONO_MAX == 2_500
    assert rules.bono_amount(3_000) == 1_500


def test_la_temporada_alta_canaria_es_el_invierno_y_el_igic_va_dentro() -> None:
    assert rules.season_factor(at(10).date()) < rules.season_factor(at(10).date().replace(month=12))
    assert rules.vacation_igic(10_700) == 700


def test_el_gran_tenedor_tiene_tope_en_zona_tensionada() -> None:
    assert rules.level_allowed("mercado", index=1.2, whole_homes=4, license=False)
    assert not rules.level_allowed("mercado", index=1.2, whole_homes=5, license=False)
    assert rules.level_allowed("mercado", index=1.05, whole_homes=9, license=False)
    assert not rules.level_allowed("vacacional", index=1.0, whole_homes=0, license=False)
    assert rules.level_allowed("vacacional", index=1.0, whole_homes=0, license=True)


def test_vivir_en_tu_casa_recarga_la_bateria_mas_rapido() -> None:
    contract = Contract(job="obra", level=1, battery=0.0, battery_at=0.0)
    at_home = Contract(job="obra", level=1, battery=0.0, battery_at=0.0, home_regen=1.2)
    assert battery_now(at_home, 3_600) == pytest.approx(battery_now(contract, 3_600) * 1.2)


# -- Hipoteca --------------------------------------------------------------------------


async def test_comprar_con_hipoteca_paga_la_entrada_y_firma_el_prestamo(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)

    trade = await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)

    deal = trade.deal
    assert deal.loan == round(deal.quote.price * rules.MORTGAGE_LTV)
    assert trade.balance == STARTING_BALANCE + 200_000 - (deal.quote.total - deal.loan)
    (mortgage,) = (await service.market(GUILD)).mortgages_of(ANA, BAJO.key)
    assert mortgage.balance == deal.loan and mortgage.payment == deal.payment
    assert mortgage.months_left == rules.MORTGAGE_MONTHS
    assert ledger_sum(tmp_path, ANA) == trade.balance


async def test_sin_nomina_el_banco_no_presta(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    with pytest.raises(HousingError, match="banco"):
        await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    assert (await service.market(GUILD)).holdings == {}


async def test_las_cuotas_se_cobran_y_sin_pagar_doce_llega_la_ejecucion(tmp_path: Path) -> None:
    clock = Clock(at(1))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)
    trade = await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    clock.to(2)
    await service.close_days(GUILD)
    (mortgage,) = (await service.market(GUILD)).mortgages_of(ANA)
    assert mortgage.balance < trade.deal.loan and mortgage.unpaid_count == 0
    # Se queda sin un duro: las cuotas siguientes no se pagan.
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])
    for d in range(3, 17):
        clock.to(d)
        await service.close_days(GUILD)

    market = await service.market(GUILD)
    assert market.mortgages_of(ANA) == []
    assert BAJO.key not in market.holdings
    assert market.bank_debts.get(ANA, 0) > 0
    assert sql(tmp_path, "SELECT status FROM housing_mortgages")[0][0] == "ejecutada"
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)
    # La deuda con el banco se cobra de lo que vaya entrando.
    debt = market.bank_debts[ANA]
    await give(economy, ANA, debt + 1_000)
    clock.to(17)
    await service.close_days(GUILD)
    assert (await service.market(GUILD)).bank_debts.get(ANA, 0) == 0


async def test_vender_con_hipoteca_la_cancela_con_lo_cobrado(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)
    await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    trade = await service.sell(GUILD, ANA, BAJO.key, 100)
    assert trade.deal.payoff > 0
    assert (await service.market(GUILD)).mortgages_of(ANA) == []
    assert ledger_sum(tmp_path, ANA) == trade.balance


async def test_amortizar_cancela_la_hipoteca_con_el_monedero(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 2_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    (mortgage,) = (await service.market(GUILD)).mortgages_of(ANA)
    paid, balance = await service.amortize(GUILD, ANA, mortgage.id)
    assert paid == mortgage.owed
    assert (await service.market(GUILD)).mortgages_of(ANA) == []
    assert ledger_sum(tmp_path, ANA) == balance


async def test_el_euribor_se_mueve_los_lunes(tmp_path: Path) -> None:
    clock = Clock(at(11))
    _economy, service = await make(tmp_path, clock)
    service.rng = random.Random(4)
    clock.to(13)  # cierra el lunes 12
    await service.close_days(GUILD)
    assert sql(tmp_path, "SELECT week FROM housing_euribor") == [("2026-10-12",)]


async def test_la_hipoteca_resta_en_el_patrimonio(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)
    trade = await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        net = assets_in(connection, GUILD)[ANA]
    fiscal = rules.fiscal_value(BAJO, 100, trade.deal.quote.price)
    assert net == fiscal - trade.deal.loan


# -- VPO y vivienda habitual -------------------------------------------------------------


async def test_la_vpo_se_sortea_se_compra_con_aval_y_se_devuelve_al_estado(tmp_path: Path) -> None:
    clock = Clock(at(11))
    economy, service = await make(tmp_path, clock)
    await give(economy, STATE_ACCOUNT_ID, 0)
    await service.vpo_signup(GUILD, ANA)
    clock.to(13)
    runs = await service.close_days(GUILD)  # el lunes 12 se sortea
    assert any(run.draws for run in runs)
    unit = (await service.market(GUILD)).reservation_of(ANA)
    assert unit == VPO_UNITS[0].key
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 50_000)
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)

    deal, balance = await service.vpo_buy(GUILD, ANA, unit)

    price = VPO_BY_KEY[unit].price
    assert deal.loan == price and balance == STARTING_BALANCE + 50_000 - deal.notary
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == price
    market = await service.market(GUILD)
    assert market.vpo_of(ANA) == unit and market.homes[ANA] == (HOME_VPO, unit)
    assert market.mortgages_of(ANA)[0].aval == "ico"
    with pytest.raises(HousingError):
        await service.vpo_signup(GUILD, ANA)  # ya tiene casa

    back, balance = await service.vpo_return(GUILD, ANA)
    assert back.payoff == deal.loan
    market = await service.market(GUILD)
    assert market.vpo_of(ANA) is None and ANA not in market.homes
    assert ledger_sum(tmp_path, ANA) == balance


async def test_sin_partida_el_estado_no_recompra_la_vpo(tmp_path: Path) -> None:
    clock = Clock(at(11))
    economy, service = await make(tmp_path, clock)
    await service.vpo_signup(GUILD, ANA)
    clock.to(13)
    await service.close_days(GUILD)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 50_000)
    unit = (await service.market(GUILD)).reservation_of(ANA)
    await service.vpo_buy(GUILD, ANA, unit)
    estado = await economy.state_balance(GUILD)
    await economy.repository.apply(GUILD, STATE_ACCOUNT_ID, [LedgerEntry(-estado, "prueba")])
    with pytest.raises(InsufficientFundsError):
        await service.vpo_return(GUILD, ANA)
    assert (await service.market(GUILD)).vpo_of(ANA) == unit


async def test_quien_gana_mucho_no_entra_en_el_sorteo(tmp_path: Path) -> None:
    clock = Clock(at(11))
    _economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, rules.vpo_income_cap() + 1, clock)
    with pytest.raises(HousingError, match="IPREM"):
        await service.vpo_signup(GUILD, ANA)


async def test_vivir_en_tu_piso_no_lo_alquila_y_paga_sus_recibos(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.set_home(GUILD, ANA, BAJO.key)
    market = await service.market(GUILD)
    assert market.leases[BAJO.key].status == rules.HABITUAL
    assert market.homes[ANA] == (HOME_OWN, BAJO.key)
    assert await service.repository.home_of(GUILD, ANA) == (HOME_OWN, BAJO.key)
    balance = await economy.balance(GUILD, ANA)
    clock.to(15)
    await service.close_days(GUILD)
    assert await economy.balance(GUILD, ANA) == balance - rules.monthly_costs(BAJO).total
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        assert assets_in(connection, GUILD).get(ANA, 0) == 0  # exenta hasta 3 millones
    assert await service.leave_home(GUILD, ANA) == HOME_OWN
    assert (await service.market(GUILD)).leases[BAJO.key].status == rules.VACANT


# -- Miembros de alquiler y bono --------------------------------------------------------


async def test_un_miembro_de_alquiler_paga_al_casero_y_cobra_el_bono(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 1_000_000)
    await give(economy, STATE_ACCOUNT_ID, 1_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    lease = await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    assert lease.member == BEA and lease.bono
    assert (await service.market(GUILD)).homes[BEA] == (HOME_RENTED, BAJO.key)
    ana = await economy.balance(GUILD, ANA)
    bea = await economy.balance(GUILD, BEA)
    state = await economy.balance(GUILD, STATE_ACCOUNT_ID)
    clock.to(15)

    (run,) = await service.close_days(GUILD)

    bono = rules.bono_amount(lease.rent)
    assert run.bono == {BEA: bono}
    assert await economy.balance(GUILD, ANA) > ana
    assert await economy.balance(GUILD, BEA) <= bea - lease.rent + bono
    assert await economy.balance(GUILD, STATE_ACCOUNT_ID) < state + 1  # pagó el bono
    for member in (ANA, BEA, STATE_ACCOUNT_ID):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)


async def test_sin_dinero_el_miembro_deja_de_pagar_y_sin_partida_no_hay_bono(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    balance = await economy.balance(GUILD, BEA)
    await economy.repository.apply(GUILD, BEA, [LedgerEntry(-balance, "prueba")])
    clock.to(15)
    (run,) = await service.close_days(GUILD)
    assert (await service.market(GUILD)).leases[BAJO.key].status == rules.DEFAULTING
    assert run.bono == {}
    # Con dinero y sin un duro en el Estado: paga, pero el bono no llega.
    await give(economy, BEA, 1_000_000)
    estado = await economy.state_balance(GUILD)
    await economy.repository.apply(GUILD, STATE_ACCOUNT_ID, [LedgerEntry(-estado, "prueba")])
    clock.to(16)
    (run,) = await service.close_days(GUILD)
    assert (await service.market(GUILD)).leases[BAJO.key].status == rules.RENTED
    assert run.bono_unpaid == [BEA]


# -- Vacacional, gran tenedor, fondo buitre y SOCIMI ------------------------------------


async def test_la_licencia_vacacional_es_una_por_barrio_y_su_tasa_va_al_estado(
    tmp_path: Path,
) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.buy(GUILD, ANA, "schamann_piso2", 100)
    with pytest.raises(HousingError, match="licencia"):
        await service.set_level(GUILD, ANA, BAJO.key, "vacacional")
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    fee, _balance = await service.buy_license(GUILD, ANA, BAJO.key)
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == fee == rules.LICENSE_FEE
    await service.set_level(GUILD, ANA, BAJO.key, "vacacional")
    with pytest.raises(HousingError, match="saturado"):
        await service.buy_license(GUILD, ANA, "schamann_piso2")
    breakdown = await economy.tax_breakdown(GUILD)
    assert breakdown[ANA]["tasas"] == fee


async def test_el_gran_tenedor_en_zona_tensionada_no_puede_pedir_de_mercado(
    tmp_path: Path,
) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 100_000_000)
    for key in ("taco_bajo", "taco_piso3", "ofra_bajo", "ofra_piso2", "telde_bajo"):
        await service.buy(GUILD, ANA, key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_index SET value = 1.2 WHERE barrio = 'taco'")
    with pytest.raises(HousingError, match="tensionada"):
        await service.set_level(GUILD, ANA, "taco_bajo", "mercado")
    await service.set_level(GUILD, ANA, "ofra_bajo", "mercado")  # Ofra no está tensionado


async def test_el_fondo_buitre_ofrece_un_15_por_ciento_mas_y_se_puede_aceptar(
    tmp_path: Path,
) -> None:
    clock = Clock(at(11))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 100_000_000)
    for key in ("taco_bajo", "ofra_bajo", "telde_bajo"):
        await service.buy(GUILD, ANA, key, 100)
    clock.to(13)
    (run, *_rest) = await service.close_days(GUILD)
    assert run.offers and run.offers[0][0] == ANA
    _member, key, price = run.offers[0]
    trade = await service.accept_offer(GUILD, ANA, key)
    assert trade.deal.vulture and trade.deal.quote.price == price
    assert key not in (await service.market(GUILD)).holdings


async def test_la_socimi_paga_el_19_del_dividendo_y_no_irpf_del_alquiler(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 100_000_000)
    for key in ("taco_bajo", "ofra_bajo", "telde_bajo"):
        await service.buy(GUILD, ANA, key, 100)
    with pytest.raises(HousingError):
        await service.found_socimi(GUILD, BEA)
    await service.found_socimi(GUILD, ANA)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', rent = 8000, "
            "until = '2027-01-01'"
        )
    clock.to(15)
    await service.close_days(GUILD)
    taxes = await economy.housing_tax_breakdown(GUILD)
    assert taxes[ANA]["ganancia"] > 0  # el 19 % del dividendo
    assert "alquiler" not in taxes[ANA]
