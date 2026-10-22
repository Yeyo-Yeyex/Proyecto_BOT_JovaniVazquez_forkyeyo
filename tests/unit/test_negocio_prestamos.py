"""Pruebas de los préstamos al negocio (9.7.0, `docs/negocios.md`, «Préstamos al negocio»).

Con la economía, `pisos` y los negocios de verdad sobre un SQLite temporal, como
`test_negocio_sl.py`: pedirlo de autónomo y de sociedad (a qué caja entra y qué mira el banco),
que su noche cobre las cuotas con los intereses como gasto y que las de `pisos` no, el impago
hasta el vencimiento, que pase a la sociedad al constituirla, que reste de su patrimonio y qué
queda al cerrar.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest
from test_negocio_service import (
    DAY,
    GUILD,
    USER,
    Clock,
    give,
    open_cafe,
    sin_azar_de_la_pala,  # noqa: F401  (fixture autouse: sin sorpresas en la `pala`)
    wallet,
)

from bot.repositories.business import BusinessRepository, companies_in
from bot.repositories.economy import EconomyRepository, LedgerEntry, company_account
from bot.repositories.housing import HousingRepository
from bot.repositories.housing_finance import LOAN_BUSINESS, LOAN_COMPANY, LOAN_PERSONAL
from bot.repositories.work import WorkRepository
from bot.services import business_sl as sl
from bot.services import loans as lending
from bot.services.business import nightly_bill
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.housing_market import HousingService
from bot.services.negocio import BusinessError, BusinessService
from bot.services.pala import WorkService
from bot.services.taxes import igic


def sql(tmp_path: Path, query: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(query, args).fetchall()


async def make(tmp_path: Path, clock: Clock) -> tuple[BusinessService, HousingService]:
    """El servicio de negocios con el banco de `pisos` (el de las pruebas de SL no lo lleva)."""
    database = tmp_path / "bot.db"
    economy_repo = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    housing = HousingRepository(database)
    await housing.initialize()
    work_repo = WorkRepository(database)
    await work_repo.initialize()
    work = WorkService(work_repo, economy, clock=clock, rng=random.Random(1), housing=housing)
    repository = BusinessRepository(database)
    await repository.initialize()
    service = BusinessService(
        repository, economy, work, clock=clock, rng=random.Random(1), housing=housing
    )
    work.business = service
    return service, HousingService(housing, economy, clock=clock)


def salary(tmp_path: Path, clock: Clock, net_per_day: int) -> None:
    """Nóminas de los últimos 30 días que suman `net_per_day` × 30 (lo que mira el banco)."""
    sql(
        tmp_path,
        "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
        "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, ?, ?)",
        GUILD, USER, net_per_day * 30, net_per_day * 30, clock.now - DAY * 2,
    )  # fmt: skip


async def sign(service: BusinessService, principal: int, months: int = 12):  # noqa: ANN201
    found = await service.loan_desk(GUILD, USER)
    assert found is not None
    _business, desk = found
    return await service.take_loan(GUILD, USER, principal=principal, months=months, rate=desk.rate)


async def empty(service: BusinessService, account: int = USER) -> None:
    balance = await service.economy.account_balance(GUILD, account)
    await service.economy.repository.apply(GUILD, account, [LedgerEntry(-balance, "prueba")])


async def starve(service: BusinessService, account: int = USER) -> None:
    """Deja en la caja lo justo para el local (y la gestoría): la cuota del banco no llega."""
    await empty(service, account)
    business = await service.business(GUILD, USER)
    assert business is not None
    need = nightly_bill(business).total + business.rent_debt
    if business.is_company:
        need += sl.BOOKKEEPING_NIGHT + igic(sl.BOOKKEEPING_NIGHT)
    await service.economy.repository.apply(GUILD, account, [LedgerEntry(need, "prueba")])


def loan_row(tmp_path: Path, loan_id: int) -> tuple:
    return sql(
        tmp_path,
        "SELECT kind, listing, status, balance, unpaid_count, unpaid_amount FROM housing_loans "
        "WHERE id = ?",
        loan_id,
    )[0]


# -- Reglas ------------------------------------------------------------------------------


def test_el_tipo_del_negocio_es_el_de_una_pyme() -> None:
    assert lending.business_rate(0.02, []) == pytest.approx(0.02 + lending.BUSINESS_SPREAD)
    assert lending.business_rate(0.02, []) < lending.loan_rate(0.02, [])
    assert lending.business_rate(1.0, []) == lending.MAX_RATE


def test_el_banco_mira_lo_que_deja_la_sociedad_sin_amortizaciones_ni_intereses() -> None:
    week = {"ventas": 100_000, "compras": -30_000, "amortizacion": -5_000, "intereses": -2_000}
    assert sl.ebitda(week) == 70_000
    assert sl.tax_base(week) == 63_000  # Sociedades sí los resta


# -- De autónomo -------------------------------------------------------------------------


async def test_un_autonomo_lo_firma_el_y_le_entra_en_su_monedero(tmp_path: Path) -> None:
    clock = Clock()
    service, housing = await make(tmp_path, clock)
    await open_cafe(service)
    with pytest.raises(BusinessError, match="no cabe"):
        await sign(service, 100_000)  # sin ingresos, al banco no le cabe nada
    salary(tmp_path, clock, 40_000)
    before = wallet(tmp_path, USER)[1]
    business, loan, offer, paid = await sign(service, 100_000)
    assert loan.kind == LOAN_BUSINESS and loan.business_id == business.id
    assert paid.balance == before + 100_000 - offer.fee == wallet(tmp_path, USER)[1]
    # La comisión es gasto de la actividad (en su IRPF) y sale en las cuentas.
    assert sql(
        tmp_path,
        "SELECT gross FROM economy_tax_records WHERE concept = 'negocio:prestamo:apertura'",
    ) == [(-offer.fee,)]
    assert business.week_totals["intereses"] == -offer.fee
    # No es un préstamo personal: no sale en ese mostrador, pero su cuota cuenta en el 35 %.
    desk, _income = await housing.loan_desk(GUILD, USER)
    assert desk.loans == () and desk.installments == offer.monthly


async def test_su_noche_cobra_la_cuota_y_la_de_pisos_no(tmp_path: Path) -> None:
    clock = Clock()
    service, housing = await make(tmp_path, clock)
    await open_cafe(service)
    salary(tmp_path, clock, 40_000)
    _business, loan, offer, _paid = await sign(service, 100_000)
    clock.now += DAY
    before = wallet(tmp_path, USER)[1]
    await housing.close_days(GUILD)  # `pisos` no la toca
    assert wallet(tmp_path, USER)[1] == before
    runs = await service.close_nights(GUILD)  # la noche del día de firmar: aún sin cuota
    assert runs[-1].loans == ()
    clock.now += DAY
    runs = await service.close_nights(GUILD)
    assert runs[-1].loans == ("pagada",)
    interest = round(100_000 * loan.rate / 12)
    assert sql(
        tmp_path,
        "SELECT SUM(gross) FROM economy_tax_records WHERE concept = 'negocio:prestamo:intereses'",
    ) == [(-interest,)]
    kind, _listing, status, balance, _count, _unpaid = loan_row(tmp_path, loan.id)
    left = 100_000 - (offer.payment - interest)
    assert (kind, status, balance) == (LOAN_BUSINESS, "activo", left)


async def test_sin_pagar_tres_noches_vence_y_lo_debe_el_dueno(tmp_path: Path) -> None:
    clock = Clock()
    service, housing = await make(tmp_path, clock)
    await open_cafe(service)
    salary(tmp_path, clock, 40_000)
    _business, loan, _offer, _paid = await sign(service, 100_000)
    await service.close_nights(GUILD)
    events = []
    for _ in range(1 + lending.DEFAULT_INSTALLMENTS):
        clock.now += DAY
        await starve(service)
        for run in await service.close_nights(GUILD):
            events += run.loans
    assert events == ["sin_pagar", "sin_pagar", "vencido"]
    kind, _listing, status, *_rest = loan_row(tmp_path, loan.id)
    assert (kind, status) == (LOAN_BUSINESS, "vencido")
    market = await housing.market(GUILD)
    assert market.bank_debts[USER] > 90_000 and market.in_asnef(USER)


async def test_amortizarlo_cuesta_la_compensacion_y_es_gasto(tmp_path: Path) -> None:
    clock = Clock()
    service, _housing = await make(tmp_path, clock)
    await open_cafe(service)
    salary(tmp_path, clock, 40_000)
    _business, loan, _offer, _paid = await sign(service, 100_000, months=24)
    before = wallet(tmp_path, USER)[1]
    business, closed, fee, paid = await service.repay_loan(GUILD, USER, loan.id)
    assert fee == lending.early_fee(100_000, 24) and closed.id == loan.id
    assert paid.balance == before - 100_000 - fee
    assert loan_row(tmp_path, loan.id)[2] == "pagado"
    assert business.week_totals["intereses"] == -(closed.fee + fee)
    with pytest.raises(BusinessError, match="cerrado"):
        await service.repay_loan(GUILD, USER, loan.id)


async def test_al_cerrar_el_negocio_del_autonomo_sigue_como_personal(tmp_path: Path) -> None:
    clock = Clock()
    service, housing = await make(tmp_path, clock)
    await open_cafe(service)
    salary(tmp_path, clock, 40_000)
    _business, loan, offer, _paid = await sign(service, 100_000)
    closing = await service.close(GUILD, USER)
    assert closing.bank_moved == 100_000 and closing.bank_paid == 0
    kind, listing, status, balance, *_rest = loan_row(tmp_path, loan.id)
    assert (kind, listing, status, balance) == (LOAN_PERSONAL, None, "activo", 100_000)
    clock.now += 2 * DAY
    before = wallet(tmp_path, USER)[1]
    await housing.close_days(GUILD)  # ahora la cobra `pisos`
    assert wallet(tmp_path, USER)[1] == before - offer.payment


# -- De sociedad -------------------------------------------------------------------------


async def company(tmp_path: Path, clock: Clock, week_profit: int = 700_000):  # noqa: ANN201
    """Una cafetería en sociedad con una semana pasada que deja `week_profit`."""
    service, housing = await make(tmp_path, clock)
    await open_cafe(service)
    await give(service, 2_000_000)
    business, _total = await service.constitute(GUILD, USER, 300_000)
    fresh = await service.business(GUILD, USER)
    assert fresh is not None
    fresh.last_week = {"ventas": week_profit + 100_000, "compras": -100_000}
    await service.repository.save(GUILD, fresh)
    return service, housing, business


async def test_una_sociedad_lo_firma_ella_y_le_entra_en_su_caja(tmp_path: Path) -> None:
    clock = Clock()
    service, housing, business = await company(tmp_path, clock)
    account = company_account(business.id)
    found = await service.loan_desk(GUILD, USER)
    assert found is not None
    _business, desk = found
    # Sin nómina del socio, el banco mira lo que deja la sociedad: el 35 % de 100.000 al día.
    assert desk.corporate and desk.room == lending.room_for(100_000, 0)
    assert "Sin nómina" not in " ".join(f.label for f in desk.factors)
    owner, cash = wallet(tmp_path, USER)[1], wallet(tmp_path, account)[1]
    before = await service.repository.read(lambda c: companies_in(c, GUILD))
    _business, loan, offer, paid = await sign(service, 300_000)
    assert loan.kind == LOAN_COMPANY
    assert paid.company == cash + 300_000 - offer.fee == wallet(tmp_path, account)[1]
    assert wallet(tmp_path, USER)[1] == owner
    # Es deuda de la sociedad: resta de su valor y no del patrimonio del socio.
    after = await service.repository.read(lambda c: companies_in(c, GUILD))
    assert after[USER] == before[USER] - offer.fee
    assert USER not in await housing.repository.member_debts(GUILD)
    view = await service.company_view(GUILD, USER)
    assert view is not None and view.bank == 300_000


async def test_su_caja_paga_las_cuotas_y_los_intereses_van_a_sociedades(tmp_path: Path) -> None:
    clock = Clock()
    service, _housing, business = await company(tmp_path, clock)
    account = company_account(business.id)
    _business, loan, _offer, _paid = await sign(service, 300_000)
    clock.now += DAY
    await service.close_nights(GUILD)  # la noche de firmar: aún sin cuota
    clock.now += DAY
    owner = wallet(tmp_path, USER)[1]
    runs = await service.close_nights(GUILD)
    assert runs[-1].loans == ("pagada",)
    assert wallet(tmp_path, USER)[1] == owner  # ni la cuota ni nada sale del socio
    fresh = await service.business(GUILD, USER)
    assert fresh is not None
    interest = round(300_000 * loan.rate / 12)
    assert fresh.week_totals["intereses"] == -(loan.fee + interest)
    assert "intereses" in sl.TAX_ITEMS
    assert wallet(tmp_path, account)[0] == wallet(tmp_path, account)[1]


async def test_al_constituirla_los_prestamos_del_autonomo_siguen_a_su_nombre(
    tmp_path: Path,
) -> None:
    """El dinero se lo quedó el autónomo: la deuda no pasa a la sociedad (no sería un
    dividendo sin impuestos)."""
    clock = Clock()
    service, housing = await make(tmp_path, clock)
    await open_cafe(service)
    salary(tmp_path, clock, 40_000)
    _business, loan, _offer, _paid = await sign(service, 100_000)
    await give(service, 2_000_000)
    business, _total = await service.constitute(GUILD, USER, 300_000)
    assert loan_row(tmp_path, loan.id)[:3] == (LOAN_PERSONAL, None, "activo")
    assert any("siguen a tu nombre" in line for line in business.news)
    view = await service.company_view(GUILD, USER)
    assert view is not None and view.bank == 0
    _business, own, _offer, _paid = await sign(service, 50_000)
    assert own.kind == LOAN_COMPANY
    pending = [p.kind for p in await housing_pending(housing)]
    assert "business_loan_guarantee" in pending and "loan" in pending


async def housing_pending(housing: HousingService) -> list:  # noqa: ANN401
    from bot.services.housing_view import pending_items

    market = await housing.market(GUILD)
    return pending_items(market, USER, [], housing.today())


async def test_liquidarla_paga_al_banco_y_el_resto_lo_asume_el_socio(tmp_path: Path) -> None:
    clock = Clock()
    service, _housing, business = await company(tmp_path, clock)
    account = company_account(business.id)
    _business, loan, _offer, _paid = await sign(service, 300_000)
    await empty(service, account)
    await service.contribute(GUILD, USER, 100_000)
    owner = wallet(tmp_path, USER)[1]
    closing = await service.close(GUILD, USER)
    # La caja (lo aportado y la fianza que vuelve) paga lo que puede; el resto, el socio.
    assert closing.bank_paid > 0 and closing.bank_paid + closing.bank_moved == 300_000
    assert closing.covered == 0  # lo del banco no entra en lo que responde por el capital
    assert wallet(tmp_path, USER)[1] == owner
    kind, listing, status, balance, *_rest = loan_row(tmp_path, loan.id)
    assert (kind, listing, status, balance) == (
        LOAN_PERSONAL, None, "activo", closing.bank_moved,
    )  # fmt: skip
    assert wallet(tmp_path, account)[1] == 0


async def test_si_vence_el_banco_reclama_al_socio_y_la_sociedad_se_lo_debe(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, housing, business = await company(tmp_path, clock)
    account = company_account(business.id)
    _business, loan, _offer, _paid = await sign(service, 300_000)
    await service.close_nights(GUILD)
    events = []
    for _ in range(1 + lending.DEFAULT_INSTALLMENTS):
        clock.now += DAY
        await starve(service, account)
        for run in await service.close_nights(GUILD):
            events += run.loans
    assert events[-1] == "vencido"
    market = await housing.market(GUILD)
    owed = market.bank_debts[USER]
    fresh = await service.business(GUILD, USER)
    assert fresh is not None and fresh.company["loan"] == owed
