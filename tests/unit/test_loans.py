"""Pruebas de los préstamos personales del banco: reglas, el 35 % con las hipotecas, las
cuotas de cada noche, el seguro, el vencimiento, el fichero de morosos y el embargo de nómina.

La economía y `pisos` son los reales sobre un SQLite temporal, como en las pruebas de las
hipotecas (`test_housing_phases.py`).
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from bot.repositories.economy import EconomyRepository, InsufficientFundsError, LedgerEntry
from bot.repositories.housing import HousingError, HousingRepository, assets_in
from bot.services import housing as rules
from bot.services import loans as lending
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.housing_catalog import LISTING_BY_KEY
from bot.services.housing_market import HousingService
from bot.services.levels import TIMEZONE

GUILD = 1
ANA = 10
BAJO = LISTING_BY_KEY["schamann_bajo"]


class Clock:
    def __init__(self, d: int) -> None:
        self.to(d)

    def __call__(self) -> float:
        return self.now

    def to(self, d: int, hour: int = 12) -> None:
        self.now = datetime(2026, 10, d, hour, tzinfo=TIMEZONE).timestamp()


class Quiet(random.Random):
    """Sin imprevistos ni sorpresas en los cierres de `pisos`."""

    def random(self) -> float:
        return 0.999

    def choice(self, seq):  # noqa: ANN001, ANN201
        return seq[0]


async def make(tmp_path: Path, clock: Clock) -> tuple[EconomyService, HousingService]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    housing_repo = HousingRepository(tmp_path / "bot.db")
    await housing_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    return economy, HousingService(housing_repo, economy, clock=clock, rng=Quiet())


def salary(tmp_path: Path, net_per_day: int, clock: Clock) -> None:
    """Nóminas de los últimos 30 días que suman `net_per_day` × 30 (lo que mira el banco)."""
    sql(
        tmp_path,
        "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
        "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, ?, ?)",
        GUILD, ANA, net_per_day * 30, net_per_day * 30, clock.now - 86_400 * 2,
    )  # fmt: skip


def sql(tmp_path: Path, query: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(query, args).fetchall()


def ledger_sum(tmp_path: Path) -> int:
    (total,) = sql(
        tmp_path,
        "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
        GUILD, ANA,
    )[0]  # fmt: skip
    return int(total)


async def empty(economy: EconomyService) -> None:
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])


async def sign(
    service: HousingService, principal: int = 20_000, months: int = 12, insured: bool = False
):  # noqa: ANN201
    desk, _income = await service.loan_desk(GUILD, ANA)
    factors = lending.profile_factors(desk.profile, insured=insured)
    rate = lending.loan_rate(desk.euribor, factors)
    return await service.take_loan(
        GUILD, ANA, principal=principal, months=months, insured=insured, rate=rate
    )


# -- Reglas ----------------------------------------------------------------------------


def test_la_tae_cuenta_la_comision_y_el_seguro() -> None:
    plain = lending.quote(100_000, 48, 0.07, insured=False)
    insured = lending.quote(100_000, 48, 0.06, insured=True)
    assert plain.received == 100_000 - plain.fee
    assert plain.tae > 0.07  # la comisión sube la TAE por encima del TIN
    assert insured.premium == round(insured.payment * lending.INSURANCE_SHARE)
    assert insured.premium_tax == round(insured.premium * rules.IPS_RATE)
    assert insured.monthly == insured.payment + insured.premium + insured.premium_tax
    assert insured.tae > insured.rate


def test_lo_mas_que_se_puede_pedir_cabe_en_lo_que_queda_del_35() -> None:
    room = lending.room_for(10_000, 1_000)
    assert room == int(10_000 * rules.DTI_MAX) - 1_000
    for insured in (False, True):
        most = lending.max_principal(room, 0.07, 48, insured=insured)
        assert lending.quote(most, 48, 0.07, insured=insured).monthly <= room
        assert lending.quote(most + 1_000, 48, 0.07, insured=insured).monthly > room
    assert lending.max_principal(0, 0.07, 48, insured=False) == 0


def test_el_perfil_baja_o_sube_el_tipo_entre_los_topes() -> None:
    good = lending.Profile(paid_loans=5, owns_property=True, mortgage_current=True)
    bad = lending.Profile(defaults=9, late=True, payroll=False)
    base = lending.loan_rate(0.02, [])
    assert lending.loan_rate(0.02, lending.profile_factors(good, insured=True)) < base
    assert lending.loan_rate(0.02, lending.profile_factors(bad, insured=False)) > base
    assert lending.loan_rate(-1.0, []) == lending.MIN_RATE
    assert lending.loan_rate(1.0, []) == lending.MAX_RATE


def test_amortizar_antes_cuesta_el_1_o_el_05_por_ciento() -> None:
    assert lending.early_fee(10_000, lending.EARLY_FEE_MONTHS + 1) == 100
    assert lending.early_fee(10_000, lending.EARLY_FEE_MONTHS) == 50


def test_el_embargo_de_nomina_va_por_tramos_del_smi_del_dia() -> None:
    smi = lending.SMI_DAY
    assert lending.wage_garnish(0, smi) == 0  # el salario mínimo no se toca
    assert lending.wage_garnish(0, 2 * smi) == int(smi * 0.30)
    assert lending.wage_garnish(0, 3 * smi) == int(smi * 0.30 + smi * 0.50)
    # Partido en varias nóminas del mismo día, se embarga lo mismo (salvo redondeo).
    whole = lending.wage_garnish(0, 7 * smi)
    parts = sum(lending.wage_garnish(k * smi, smi) for k in range(7))
    assert abs(whole - parts) <= 7


def test_el_fichero_de_morosos_mira_cuotas_seguidas_y_deuda() -> None:
    assert not lending.in_asnef([1, 0], 0)
    assert lending.in_asnef([lending.ASNEF_INSTALLMENTS], 0)
    assert lending.in_asnef([], lending.ASNEF_MIN_DEBT)
    assert not lending.in_asnef([], lending.ASNEF_MIN_DEBT - 1)


# -- Con dinero de verdad --------------------------------------------------------------


async def test_firmar_trae_lo_prestado_menos_la_comision(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, balance = await sign(service, 20_000)
    assert balance == STARTING_BALANCE + 20_000 - signed.quote.fee
    assert ledger_sum(tmp_path) == balance
    desk, _ = await service.loan_desk(GUILD, ANA)
    assert desk.installments == signed.quote.monthly
    assert [loan.id for loan in desk.loans] == [signed.loan.id]


async def test_sin_nomina_ni_rentas_el_banco_no_presta(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(1))
    with pytest.raises(HousingError, match="35 %"):
        await sign(service)


async def test_con_deuda_con_hacienda_el_banco_no_presta(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    sql(
        tmp_path, "INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, 300)",
        GUILD, ANA,
    )  # fmt: skip
    with pytest.raises(HousingError, match="Hacienda"):
        await sign(service)


async def test_si_el_tipo_ha_cambiado_no_se_firma(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    with pytest.raises(HousingError, match="tipo"):
        await service.take_loan(GUILD, ANA, principal=20_000, months=12, insured=False, rate=0.5)


async def test_la_cuota_del_prestamo_cuenta_para_la_hipoteca(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 3_000, clock)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(10_000_000, "prueba")])
    desk, income = await service.loan_desk(GUILD, ANA)
    rate = lending.loan_rate(desk.euribor, lending.profile_factors(desk.profile, insured=False))
    room = lending.room_for(income, desk.installments)
    most = lending.max_principal(room, rate, 12, insured=False)
    await service.take_loan(GUILD, ANA, principal=most, months=12, insured=False, rate=rate)
    with pytest.raises(HousingError, match="35 %"):
        await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)


async def test_la_primera_cuota_llega_la_noche_siguiente_y_se_puede_desistir(
    tmp_path: Path,
) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await sign(service, 20_000)
    clock.to(2)
    await service.close_days(GUILD)  # cierra el día 1: aún sin cuota
    (loan,) = (await service.market(GUILD)).loans_of(ANA)
    assert loan.months_left == loan.months and loan.balance == 20_000
    closed, balance = await service.desist_loan(GUILD, ANA, loan.id)
    assert closed.status == "desistido" and closed.paid == 20_000 - signed.quote.fee
    assert balance == STARTING_BALANCE  # como si no lo hubiera pedido
    assert (await service.market(GUILD)).loans_of(ANA) == []


async def test_tras_la_primera_cuota_ya_no_se_desiste_pero_se_amortiza(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await sign(service, 20_000, months=24)
    for d in (2, 3):
        clock.to(d)
        await service.close_days(GUILD)
    (loan,) = (await service.market(GUILD)).loans_of(ANA)
    assert loan.months_left == 23 and loan.balance < 20_000 and loan.paid == signed.quote.monthly
    with pytest.raises(HousingError, match="primera cuota"):
        await service.desist_loan(GUILD, ANA, loan.id)
    closed, _ = await service.repay_loan(GUILD, ANA, loan.id)
    assert closed.fee == lending.early_fee(loan.balance, loan.months_left)
    assert closed.paid == loan.owed + closed.fee
    assert (await service.market(GUILD)).loans_of(ANA) == []
    assert ledger_sum(tmp_path) == await economy.balance(GUILD, ANA)


async def test_sin_dinero_para_amortizar_no_se_mueve_nada(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await sign(service)
    await empty(economy)
    with pytest.raises(InsufficientFundsError):
        await service.repay_loan(GUILD, ANA, signed.loan.id)
    assert len((await service.market(GUILD)).loans_of(ANA)) == 1


async def test_el_seguro_paga_las_cuotas_que_no_llegan(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    await sign(service, insured=True)
    await empty(economy)
    covered = []
    for d in range(2, 2 + lending.INSURANCE_COVERS + 1):
        clock.to(d)
        for run in await service.close_days(GUILD):
            covered += run.loan_covered
    (loan,) = (await service.market(GUILD)).loans_of(ANA)
    assert covered == [ANA] * lending.INSURANCE_COVERS
    assert loan.covers_left == 0 and loan.unpaid_count == 0


async def test_sin_pagar_entra_en_el_fichero_y_al_final_vence(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await sign(service, 20_000)
    await empty(economy)
    defaults = []
    for d in range(2, 3 + lending.DEFAULT_INSTALLMENTS):
        clock.to(d)
        for run in await service.close_days(GUILD):
            defaults += run.loan_defaults
        market = await service.market(GUILD)
        if d == 2 + lending.ASNEF_INSTALLMENTS:
            assert market.in_asnef(ANA)
            with pytest.raises(HousingError, match="ASNEF"):
                await sign(service)
    market = await service.market(GUILD)
    assert market.loans_of(ANA) == []
    assert [member for member, _ in defaults] == [ANA]
    assert market.bank_debts[ANA] == defaults[0][1] > 0
    assert sql(tmp_path, "SELECT status FROM housing_loans")[0][0] == "vencido"
    assert market.in_asnef(ANA)
    desk, _ = await service.loan_desk(GUILD, ANA)
    assert desk.asnef and desk.profile.defaults == 1


async def test_la_deuda_con_el_banco_se_embarga_de_la_nomina(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    sql(
        tmp_path, "INSERT INTO housing_bank_debts (guild_id, user_id, amount) VALUES (?, ?, ?)",
        GUILD, ANA, 1_000_000,
    )  # fmt: skip
    gross = 4 * lending.SMI_DAY
    result = await economy.pay_salary(GUILD, ANA, gross=gross, concept="pala:obra")
    net = result.payslip.net
    taken = lending.wage_garnish(0, net)
    assert taken > 0
    assert result.balance == STARTING_BALANCE + net - taken
    assert sql(tmp_path, "SELECT amount FROM housing_bank_debts")[0][0] == 1_000_000 - taken
    assert ledger_sum(tmp_path) == result.balance


async def test_los_prestamos_restan_en_el_patrimonio(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    await sign(service, 20_000)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        assert assets_in(connection, GUILD)[ANA] == -20_000


async def test_el_libro_del_banco_cuenta_lo_prestado_y_los_intereses(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await sign(service, 20_000, insured=True)
    for d in (2, 3):
        clock.to(d)
        await service.close_days(GUILD)
    book = await service.repository.bank_book(GUILD)
    assert book.loans == 1 and book.lent == 20_000
    assert book.loan_fees == signed.quote.fee
    assert book.loan_interest > 0 and book.premiums == signed.quote.premium
