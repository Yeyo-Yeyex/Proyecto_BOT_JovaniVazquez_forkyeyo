"""Pruebas de las garantías del banco (`docs/garantias.md`): rehipotecar los pisos.

La economía y `pisos` son los reales sobre un SQLite temporal, como en las pruebas de los
préstamos (`test_loans.py`) y de las hipotecas (`test_housing_phases.py`).
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository, LedgerEntry
from bot.repositories.housing import HousingError, HousingRepository, assets_in
from bot.repositories.housing_finance import HOME_OWN, set_home_in
from bot.services import housing as rules
from bot.services import loans as lending
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.housing_catalog import LISTING_BY_KEY, LISTINGS
from bot.services.housing_market import HousingService
from bot.services.levels import TIMEZONE
from bot.services.taxes import igic

GUILD = 1
ANA = 10
BAJO = LISTING_BY_KEY["schamann_bajo"]
#: Lo que no es vivienda: presta el 60 %.
GARAJE = next(x for x in LISTINGS if x.kind not in rules.HOME_KINDS)


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


def sql(tmp_path: Path, query: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(query, args).fetchall()


def salary(tmp_path: Path, net_per_day: int, clock: Clock) -> None:
    """Nóminas de los últimos 30 días que suman `net_per_day` × 30 (lo que mira el banco)."""
    sql(
        tmp_path,
        "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
        "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, ?, ?)",
        GUILD, ANA, net_per_day * 30, net_per_day * 30, clock.now - 3_600,
    )  # fmt: skip


def ledger_sum(tmp_path: Path, user_id: int = ANA) -> int:
    (total,) = sql(
        tmp_path,
        "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
        GUILD, user_id,
    )[0]  # fmt: skip
    return int(total)


async def give(economy: EconomyService, amount: int) -> None:
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(amount, "prueba")])


async def owned(tmp_path: Path, clock: Clock, *, key: str = BAJO.key, cash: int = 3_000_000):
    """Ana con nómina y un inmueble comprado al contado."""
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 20_000, clock)
    await give(economy, cash)
    await service.buy(GUILD, ANA, key, 100)
    return economy, service


async def remortgage(service: HousingService, amount: int, *, key: str = BAJO.key, months=360):  # noqa: ANN001, ANN201
    desk, _income = await service.remortgage_desk(GUILD, ANA)
    return await service.remortgage(
        GUILD, ANA, key, principal=amount, months=months, rate=desk.rate
    )


# -- Reglas ----------------------------------------------------------------------------


def test_presta_el_80_de_la_vivienda_y_el_60_de_lo_demas_menos_lo_debido() -> None:
    assert lending.remortgage_room(1_000_000, 0, home=True) == 800_000
    assert lending.remortgage_room(1_000_000, 0, home=False) == 600_000
    assert lending.remortgage_room(1_000_000, 500_000, home=True) == 300_000
    assert lending.remortgage_room(1_000_000, 900_000, home=True) == 0


def test_la_tasacion_cuesta_el_02_con_minimo_y_su_igic() -> None:
    assert lending.appraisal_fee(100_000) == lending.APPRAISAL_MIN
    assert lending.appraisal_fee(5_000_000) == 10_000
    quote = lending.remortgage_quote(400_000, 360, 0.04, 5_000_000)
    assert quote.appraisal_igic == igic(10_000)
    assert quote.received == 400_000 - 2_000 - 10_000 - quote.appraisal_igic
    assert quote.tae > quote.rate


def test_lo_mas_que_se_puede_pedir_cabe_en_el_35_y_en_la_tasacion() -> None:
    most = lending.max_remortgage(10_000_000, 2_000, 0.04, 360)
    assert rules.installment(most, 0.04, 360) <= 2_000
    assert rules.installment(most + 1_000, 0.04, 360) > 2_000
    assert lending.max_remortgage(50_000, 100_000, 0.04, 360) == 50_000


# -- Firmar ----------------------------------------------------------------------------


async def test_rehipotecar_trae_lo_prestado_menos_comision_y_tasacion(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await owned(tmp_path, clock)
    before = await economy.balance(GUILD, ANA)
    state_before = await economy.balance(GUILD, STATE_ACCOUNT_ID)
    desk, _ = await service.remortgage_desk(GUILD, ANA)
    (found,) = desk.collaterals
    assert found.dwelling and found.owed == 0
    assert found.room == lending.remortgage_room(found.value, 0, home=True)
    signed, balance = await remortgage(service, 100_000)
    assert balance == before + signed.quote.received
    assert ledger_sum(tmp_path) == balance
    state = await economy.balance(GUILD, STATE_ACCOUNT_ID)
    assert state - state_before == signed.quote.appraisal_igic
    assert signed.mortgage.remortgage and signed.mortgage.listing == BAJO.key
    assert sql(tmp_path, "SELECT kind, months_left FROM housing_mortgages") == [("rehipoteca", 360)]
    desk, _ = await service.remortgage_desk(GUILD, ANA)
    assert desk.collaterals[0].owed == 100_000
    assert [m.id for m in desk.remortgages] == [signed.mortgage.id]
    assert desk.installments == signed.quote.payment


async def test_no_presta_mas_de_lo_que_deja_la_tasacion(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await owned(tmp_path, clock)
    desk, _ = await service.remortgage_desk(GUILD, ANA)
    with pytest.raises(HousingError, match="80 %"):
        await remortgage(service, desk.collaterals[0].room + 1)
    with pytest.raises(HousingError, match="desde"):
        await remortgage(service, lending.REMORTGAGE_MIN - 1)
    with pytest.raises(HousingError, match="plazo"):
        await remortgage(service, 50_000, months=100)


async def test_lo_que_no_es_vivienda_presta_el_60(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await owned(tmp_path, clock, key=GARAJE.key, cash=10_000_000)
    desk, _ = await service.remortgage_desk(GUILD, ANA)
    (found,) = desk.collaterals
    assert not found.dwelling
    assert found.room == int(found.value * lending.REMORTGAGE_LTV_OTHER)


async def test_comprado_con_hipoteca_no_queda_nada_que_rehipotecar(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 20_000, clock)
    await give(economy, 300_000)
    await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    desk, _ = await service.remortgage_desk(GUILD, ANA)
    # Comprar sube un poco el barrio, pero no da ni para el mínimo.
    assert desk.collaterals[0].room < lending.REMORTGAGE_MIN
    with pytest.raises(HousingError, match="como mucho"):
        await remortgage(service, lending.REMORTGAGE_MIN)


async def test_la_cuota_tiene_que_caber_en_el_35(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 100, clock)
    await give(economy, 3_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with pytest.raises(HousingError, match="35 %"):
        await remortgage(service, 200_000)


async def test_con_deuda_con_hacienda_o_en_asnef_no_rehipoteca(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await owned(tmp_path, clock)
    sql(tmp_path, "INSERT INTO housing_bank_debts (guild_id, user_id, amount) VALUES (?, ?, ?)",
        GUILD, ANA, 10_000)  # fmt: skip
    with pytest.raises(HousingError, match="ASNEF"):
        await remortgage(service, 50_000)
    sql(tmp_path, "DELETE FROM housing_bank_debts")
    sql(tmp_path, "INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, 300)",
        GUILD, ANA)  # fmt: skip
    with pytest.raises(HousingError, match="Hacienda"):
        await remortgage(service, 50_000)


async def test_si_el_euribor_ha_cambiado_no_se_firma(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await owned(tmp_path, clock)
    with pytest.raises(HousingError, match="Euríbor"):
        await service.remortgage(GUILD, ANA, BAJO.key, principal=50_000, months=360, rate=0.5)


# -- Cada noche, vender y ejecutar -------------------------------------------------------


async def test_los_intereses_de_la_rehipoteca_no_restan_de_la_renta(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await owned(tmp_path, clock)
    signed, _ = await remortgage(service, 200_000)
    clock.to(15)
    await service.close_days(GUILD)
    mortgage = (await service.market(GUILD)).mortgages_of(ANA)[0]
    assert mortgage.balance < signed.mortgage.balance  # la cuota se cobra
    rows = sql(tmp_path, "SELECT finance FROM economy_tax_records WHERE ref = ?", BAJO.key)
    assert rows and all(finance == 0 for (finance,) in rows)


async def test_vender_cancela_tambien_la_rehipoteca(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await owned(tmp_path, clock)
    await remortgage(service, 200_000)
    trade = await service.sell(GUILD, ANA, BAJO.key, 100)
    assert trade.deal.payoff == 200_000
    assert (await service.market(GUILD)).mortgages_of(ANA) == []
    assert ledger_sum(tmp_path) == trade.balance


async def test_dos_hipotecas_del_mismo_inmueble_se_ejecutan_juntas(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 20_000, clock)
    await give(economy, 400_000)
    await service.buy(GUILD, ANA, BAJO.key, 50, financed=True)
    await service.buy(GUILD, ANA, BAJO.key, 50, financed=True)
    first, second = (await service.market(GUILD)).mortgages_of(ANA)
    assert first.listing == second.listing == BAJO.key
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])
    closed = []
    for d in range(2, 20):
        clock.to(d)
        await service.close_days(GUILD)
        closed += (await service.close_auctions(GUILD)).results
    # Una sola subasta judicial para las dos, y las dos se cierran.
    assert [r.bank for r in closed] == [True]
    assert sql(tmp_path, "SELECT COUNT(*) FROM housing_auctions") == [(1,)]
    assert sql(tmp_path, "SELECT DISTINCT status FROM housing_mortgages") == [("ejecutada",)]
    market = await service.market(GUILD)
    assert market.mortgages_of(ANA) == [] and BAJO.key not in market.holdings
    assert market.bank_debts.get(ANA, 0) > 0
    assert ledger_sum(tmp_path) == await economy.balance(GUILD, ANA)


# -- Patrimonio --------------------------------------------------------------------------


def wealth(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        return assets_in(connection, GUILD).get(ANA, 0)


async def test_la_rehipoteca_resta_en_el_patrimonio(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await owned(tmp_path, clock)
    before = wealth(tmp_path)
    await remortgage(service, 200_000)
    assert wealth(tmp_path) == before - 200_000


async def test_en_la_vivienda_habitual_solo_resta_la_parte_que_no_esta_exenta(
    tmp_path: Path,
) -> None:
    clock = Clock(14)
    home = min(
        (x for x in LISTINGS
         if x.kind in rules.HOME_KINDS and rules.price(x) > 2 * rules.HOME_WEALTH_EXEMPT),
        key=rules.price,
    )  # fmt: skip
    _economy, service = await owned(tmp_path, clock, key=home.key, cash=rules.price(home) * 2)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        set_home_in(connection, GUILD, ANA, HOME_OWN, home.key, clock.now)
    before = wealth(tmp_path)
    (held,) = sql(tmp_path, "SELECT paid FROM housing_holdings WHERE user_id = ?", ANA)
    fiscal = rules.fiscal_value(home, 100, held[0])
    taxed = (fiscal - rules.HOME_WEALTH_EXEMPT) / fiscal
    await remortgage(service, 200_000, key=home.key)
    assert wealth(tmp_path) == before - int(200_000 * taxed)


async def test_perfil_y_fortunas_restan_lo_que_se_debe_al_banco(tmp_path: Path) -> None:
    from bot.services.net_worth import build

    clock = Clock(14)
    _economy, service = await owned(tmp_path, clock)
    await remortgage(service, 200_000)
    debts = await service.repository.member_debts(GUILD)
    assert debts == {ANA: 200_000}
    (rich, poor) = build({ANA: 300_000, 20: 250_000}, [], {}, {}, debts=debts)
    assert (poor.user_id, poor.total) == (ANA, 100_000) and poor.assets == 300_000
    assert rich.user_id == 20


# -- Avalista (9.3.0) --------------------------------------------------------------------

BEA = 20


def pay(tmp_path: Path, user_id: int, net_per_day: int, clock: Clock) -> None:
    """Nóminas de los últimos 30 días de cualquiera (lo que mira el banco)."""
    sql(
        tmp_path,
        "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
        "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, ?, ?)",
        GUILD, user_id, net_per_day * 30, net_per_day * 30, clock.now - 3_600,
    )  # fmt: skip


async def guaranteed_loan(service: HousingService, principal: int, guarantor: int = BEA):  # noqa: ANN201
    desk, _ = await service.loan_desk(GUILD, ANA)
    rate = lending.loan_rate(
        desk.euribor, lending.profile_factors(desk.profile, insured=False, guaranteed=True)
    )
    return await service.take_loan(
        GUILD, ANA, principal=principal, months=12, insured=False, rate=rate,
        guarantor=guarantor,
    )  # fmt: skip


async def test_con_avalista_presta_lo_que_solo_no_daba_y_baja_el_tipo(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await make(tmp_path, clock)
    pay(tmp_path, ANA, 1_000, clock)
    pay(tmp_path, BEA, 20_000, clock)
    desk, _ = await service.loan_desk(GUILD, ANA)
    rate = lending.loan_rate(desk.euribor, lending.profile_factors(desk.profile, insured=False))
    with pytest.raises(HousingError, match="35 %"):
        await service.take_loan(GUILD, ANA, principal=50_000, months=12, insured=False, rate=rate)
    signed, _balance = await guaranteed_loan(service, 50_000)
    assert signed.loan.aval == lending.guarantor_tag(BEA)
    assert any(f.label == "Con avalista" for f in signed.factors)
    market = await service.market(GUILD)
    # La cuota avalada cuenta en el 35 % del avalista (riesgo indirecto).
    assert market.installments_of(BEA) == signed.quote.monthly
    assert market.guaranteed_by(BEA) == [signed.loan]


async def test_quien_no_puede_avalar(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await make(tmp_path, clock)
    pay(tmp_path, ANA, 1_000, clock)
    pay(tmp_path, BEA, 50_000, clock)
    with pytest.raises(HousingError, match="ti mismo"):
        await guaranteed_loan(service, 20_000, guarantor=ANA)
    for _ in range(lending.MAX_GUARANTEES):
        await guaranteed_loan(service, 5_000)
    with pytest.raises(HousingError, match="avala"):
        await guaranteed_loan(service, 5_000)
    sql(tmp_path, "UPDATE housing_loans SET aval = NULL")
    sql(tmp_path, "INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, 300)",
        GUILD, BEA)  # fmt: skip
    with pytest.raises(HousingError, match="Hacienda"):
        await guaranteed_loan(service, 5_000)
    sql(tmp_path, "DELETE FROM economy_tax_debts")
    sql(tmp_path, "INSERT INTO housing_bank_debts (guild_id, user_id, amount) VALUES (?, ?, ?)",
        GUILD, BEA, 10_000)  # fmt: skip
    with pytest.raises(HousingError, match="ASNEF"):
        await guaranteed_loan(service, 5_000)


async def test_si_no_paga_el_banco_reclama_al_avalista_y_le_debes_a_el(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    pay(tmp_path, ANA, 1_000, clock)
    pay(tmp_path, BEA, 20_000, clock)
    await guaranteed_loan(service, 50_000)
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])
    claims = []
    for d in range(2, 3 + lending.DEFAULT_INSTALLMENTS):
        clock.to(d)
        for run in await service.close_days(GUILD):
            claims += run.guarantee_claims
    market = await service.market(GUILD)
    ((guarantor, debtor, owed),) = claims
    assert (guarantor, debtor) == (BEA, ANA) and owed > 0
    assert market.member_debts == {(BEA, ANA): owed}
    assert ANA not in market.bank_debts and market.in_asnef(ANA)
    assert market.loans_of(ANA) == [] and market.guaranteed_by(BEA) == []
    assert market.bank_debts[BEA] == owed
    # A la noche siguiente el banco ya cobra a Bea de lo que tenga.
    clock.to(3 + lending.DEFAULT_INSTALLMENTS)
    await service.close_days(GUILD)
    assert (await service.market(GUILD)).bank_debts[BEA] < owed
    assert ledger_sum(tmp_path, BEA) == await economy.balance(GUILD, BEA)


async def test_el_reembolso_se_cobra_cada_noche_y_llega_al_avalista(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await make(tmp_path, clock)
    sql(tmp_path, "INSERT INTO housing_member_debts VALUES (?, ?, ?, ?, ?)",
        GUILD, BEA, ANA, 30_000, clock.now)  # fmt: skip
    await give(economy, 10_000)
    ana_before = await economy.balance(GUILD, ANA)
    bea_before = await economy.balance(GUILD, BEA)
    clock.to(15)
    (run,) = await service.close_days(GUILD)
    assert run.reimbursements == [(BEA, ANA, ana_before)]
    assert await economy.balance(GUILD, ANA) == 0
    assert await economy.balance(GUILD, BEA) == bea_before + ana_before
    assert (await service.market(GUILD)).member_debts == {(BEA, ANA): 30_000 - ana_before}
    assert ledger_sum(tmp_path, BEA) == await economy.balance(GUILD, BEA)
    assert ledger_sum(tmp_path, ANA) == 0
    # Con todo devuelto, la fila se borra y sale del fichero.
    await give(economy, 1_000_000)
    clock.to(16)
    await service.close_days(GUILD)
    market = await service.market(GUILD)
    assert market.member_debts == {} and not market.in_asnef(ANA)


async def test_el_reembolso_se_embarga_de_la_nomina_tras_el_banco(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, _service = await make(tmp_path, clock)
    sql(tmp_path, "INSERT INTO housing_bank_debts (guild_id, user_id, amount) VALUES (?, ?, ?)",
        GUILD, ANA, 100)  # fmt: skip
    sql(tmp_path, "INSERT INTO housing_member_debts VALUES (?, ?, ?, ?, ?)",
        GUILD, BEA, ANA, 1_000_000, clock.now)  # fmt: skip
    bea_before = await economy.balance(GUILD, BEA)
    result = await economy.pay_salary(GUILD, ANA, gross=4 * lending.SMI_DAY, concept="pala:obra")
    taken = lending.wage_garnish(0, result.payslip.net)
    assert result.balance == STARTING_BALANCE + result.payslip.net - taken
    assert sql(tmp_path, "SELECT amount FROM housing_bank_debts") == [(0,)]
    bea_got = await economy.balance(GUILD, BEA) - bea_before
    assert bea_got == taken - 100
    assert sql(tmp_path, "SELECT amount FROM housing_member_debts") == [(1_000_000 - bea_got,)]


async def test_una_rehipoteca_avalada_que_no_cubre_la_subasta_la_paga_el_avalista(
    tmp_path: Path,
) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    pay(tmp_path, ANA, 20_000, clock)
    pay(tmp_path, BEA, 20_000, clock)
    await give(economy, 3_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    desk, _ = await service.remortgage_desk(GUILD, ANA)
    (found,) = desk.collaterals
    await service.remortgage(
        GUILD, ANA, BAJO.key, principal=found.room, months=360,
        rate=lending.remortgage_rate(desk.euribor, guaranteed=True), guarantor=BEA,
    )  # fmt: skip
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])
    claims = []
    for d in range(2, 25):
        clock.to(d)
        for run in await service.close_days(GUILD):
            claims += run.guarantee_claims
        claims += (await service.close_auctions(GUILD)).guarantee_claims
    market = await service.market(GUILD)
    assert BAJO.key not in market.holdings
    ((guarantor, debtor, short),) = claims
    assert (guarantor, debtor) == (BEA, ANA) and short > 0
    assert market.bank_debts.get(ANA, 0) == 0


async def test_lo_que_se_deben_los_miembros_cuenta_en_el_patrimonio(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await make(tmp_path, clock)
    sql(tmp_path, "INSERT INTO housing_member_debts VALUES (?, ?, ?, ?, ?)",
        GUILD, BEA, ANA, 30_000, clock.now)  # fmt: skip
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        found = assets_in(connection, GUILD)
    assert (found[ANA], found[BEA]) == (-30_000, 30_000)
    assert await service.repository.member_debts(GUILD) == {ANA: 30_000, BEA: -30_000}


# -- Anticipo de rentas (9.2.0) ------------------------------------------------------------

RENT = 8_000


def let(tmp_path: Path, key: str = BAJO.key, **changes: object) -> None:
    """Alquila el inmueble a una familia de la gestora (o lo deja como diga `changes`)."""
    values = {"status": "alquilado", "tenant": "familia", "rent": RENT, "until": "2027-01-01",
              "unpaid": 0, "level": "referencia", **changes}  # fmt: skip
    sets = ", ".join(f"{column} = ?" for column in values)
    sql(tmp_path, f"UPDATE housing_leases SET {sets} WHERE listing = ?", *values.values(), key)


async def rented(tmp_path: Path, clock: Clock, **changes: object):  # noqa: ANN201
    """Ana con nómina y el bajo alquilado, comprado al contado."""
    economy, service = await owned(tmp_path, clock)
    let(tmp_path, **changes)
    return economy, service


async def advance(service: HousingService, months: int = 12, key: str = BAJO.key):  # noqa: ANN201
    desk = await service.advance_desk(GUILD, ANA)
    return await service.advance(GUILD, ANA, key, months=months, rate=desk.rate)


def test_el_anticipo_es_el_valor_actual_de_las_cuotas_menos_la_comision() -> None:
    quote = lending.advance_quote(10_000, 12, 0.06)
    exact = 10_000 * (1 - (1 + 0.005) ** -12) / 0.005
    assert quote.principal == int(exact)
    assert quote.fee == -(-quote.principal // 100)
    assert quote.received == quote.principal - quote.fee
    assert quote.total == 120_000 and quote.cost == 120_000 - quote.received
    assert quote.tae > quote.rate
    assert lending.advance_quote(10_000, 3, 0.0).principal == 30_000


def test_las_cuotas_del_anticipo_lo_saldan_en_su_plazo() -> None:
    for months in lending.ADVANCE_TERMS:
        quote = lending.advance_quote(8_765, months, 0.0525)
        balance = quote.principal
        for _ in range(months):
            _interest, principal = rules.mortgage_month(balance, quote.rate, quote.payment)
            balance -= principal
        assert balance == 0


async def test_adelantar_trae_lo_adelantado_menos_la_comision(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await rented(tmp_path, clock)
    before = await economy.balance(GUILD, ANA)
    desk = await service.advance_desk(GUILD, ANA)
    (found,) = desk.shares
    assert found.blocked is None and found.payment == RENT and found.months_left > 12
    signed, balance = await advance(service)
    assert balance == before + signed.quote.received
    assert ledger_sum(tmp_path) == balance
    assert signed.quote.payment == RENT
    assert sql(tmp_path, "SELECT kind, listing, months, premium FROM housing_loans") == [
        ("rentas", BAJO.key, 12, 0)
    ]
    desk = await service.advance_desk(GUILD, ANA)
    assert [x.id for x in desk.advances] == [signed.loan.id]
    assert "ya tienes el anticipo" in (desk.shares[0].blocked or "")
    # No cuenta en el tope de préstamos personales, pero su cuota sí cuenta en el 35 %.
    loans, _income = await service.loan_desk(GUILD, ANA)
    assert loans.loans == () and loans.installments == RENT
    assert sql(tmp_path, "SELECT kind FROM housing_news WHERE kind = 'anticipo'") == [("anticipo",)]


async def test_la_cuota_es_tu_parte_de_la_renta(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await make(tmp_path, clock)
    await economy.repository.apply(GUILD, 20, [LedgerEntry(3_000_000, "prueba")])
    await give(economy, 3_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 75)
    await service.buy(GUILD, 20, BAJO.key, 25)
    let(tmp_path)
    (found,) = (await service.advance_desk(GUILD, ANA)).shares
    assert found.payment == RENT * 75 // 100


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ({"status": "vacio", "tenant": None, "rent": 0}, "no está alquilado"),
        ({"level": rules.VACATION_LEVEL, "license": 1}, "vacacional"),
        ({"until": "2026-10-16"}, "le quedan 2 meses"),
        ({"unpaid": 1}, "debe 1 meses"),
    ],
)
async def test_no_se_adelanta_lo_que_no_esta_alquilado_con_contrato(
    tmp_path: Path, changes: dict, why: str
) -> None:
    clock = Clock(14)
    _economy, service = await rented(tmp_path, clock, **changes)
    (found,) = (await service.advance_desk(GUILD, ANA)).shares
    assert why in (found.blocked or "")
    with pytest.raises(HousingError, match="no se puede"):
        await advance(service, months=3)


async def test_ni_la_casa_en_que_vives_ni_mas_meses_que_el_contrato(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await rented(tmp_path, clock, until="2026-10-20")
    with pytest.raises(HousingError, match="le quedan 6 meses"):
        await advance(service, months=12)
    await advance(service, months=6)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        set_home_in(connection, GUILD, ANA, HOME_OWN, BAJO.key, clock.now)
    (found,) = (await service.advance_desk(GUILD, ANA)).shares
    assert found.blocked == "vives en él"


async def test_en_asnef_con_hacienda_o_con_uno_vivo_no_se_adelanta(tmp_path: Path) -> None:
    clock = Clock(14)
    _economy, service = await rented(tmp_path, clock)
    sql(tmp_path, "INSERT INTO housing_bank_debts (guild_id, user_id, amount) VALUES (?, ?, ?)",
        GUILD, ANA, 10_000)  # fmt: skip
    with pytest.raises(HousingError, match="ASNEF"):
        await advance(service)
    sql(tmp_path, "DELETE FROM housing_bank_debts")
    sql(tmp_path, "INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, 300)",
        GUILD, ANA)  # fmt: skip
    with pytest.raises(HousingError, match="Hacienda"):
        await advance(service)
    sql(tmp_path, "DELETE FROM economy_tax_debts")
    await advance(service)
    with pytest.raises(HousingError, match="ya tienes el anticipo"):
        await advance(service)


async def test_desistir_lo_devuelve_y_la_comision_vuelve(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await rented(tmp_path, clock)
    before = await economy.balance(GUILD, ANA)
    signed, _ = await advance(service)
    closed, balance = await service.desist_loan(GUILD, ANA, signed.loan.id)
    assert closed.status == "desistido" and balance == before
    assert ledger_sum(tmp_path) == balance
    assert (await service.advance_desk(GUILD, ANA)).advances == ()


async def test_cada_noche_se_cobra_la_cuota_y_se_cancela_con_compensacion(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await rented(tmp_path, clock)
    signed, _ = await advance(service)
    clock.to(16)
    runs = await service.close_days(GUILD)
    assert runs and all(ANA not in run.advance_orphans for run in runs)
    (loan,) = (await service.advance_desk(GUILD, ANA)).advances
    assert loan.months_left == 11 and loan.balance < signed.loan.balance and not loan.unpaid_count
    with pytest.raises(HousingError, match="primera cuota"):
        await service.desist_loan(GUILD, ANA, loan.id)
    closed, balance = await service.repay_loan(GUILD, ANA, loan.id)
    assert closed.fee == lending.early_fee(loan.balance, loan.months_left)
    assert closed.paid == loan.owed + closed.fee
    assert ledger_sum(tmp_path) == balance == await economy.balance(GUILD, ANA)
    await service.sell(GUILD, ANA, BAJO.key, 100)  # cancelado, ya se puede vender


async def test_con_el_anticipo_vivo_no_se_vende_por_las_buenas(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await rented(tmp_path, clock)
    signed, _ = await advance(service)
    blocked = f"anticipo #{signed.loan.id}"
    with pytest.raises(HousingError, match=blocked):
        await service.sell(GUILD, ANA, BAJO.key, 100)
    await economy.repository.apply(GUILD, 20, [LedgerEntry(5_000_000, "prueba")])
    with pytest.raises(HousingError, match=blocked):
        await service.member_sale(GUILD, ANA, 20, BAJO.key, 100, 1_000_000)
    with pytest.raises(HousingError, match=blocked):
        await service.create_auction(GUILD, ANA, BAJO.key, 100, 1_000)
    # Una venta que no decide él (la junta, una división sin pujas) sí sigue, y el anticipo
    # se queda: lo sigue pagando él.
    run = service.repository.sell(
        GUILD, ANA, BAJO.key, 100, service.today(), clock.now, forced=True
    )
    await economy.housing(GUILD, run)
    market = await service.market(GUILD)
    assert BAJO.key not in market.holdings
    assert market.advance_of(ANA, BAJO.key) is not None


async def test_si_el_inquilino_se_va_la_cuota_la_pones_tu(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await rented(tmp_path, clock)
    signed, _ = await advance(service)
    let(tmp_path, status="okupa", tenant=None, rent=0)
    before = await economy.balance(GUILD, ANA)
    clock.to(16)
    runs = await service.close_days(GUILD)
    assert any(ANA in run.advance_orphans for run in runs)
    (loan,) = (await service.advance_desk(GUILD, ANA)).advances
    assert loan.months_left == 11 and not loan.unpaid_count
    assert await economy.balance(GUILD, ANA) <= before - signed.quote.payment
    assert ledger_sum(tmp_path) == await economy.balance(GUILD, ANA)


async def test_sin_dinero_la_cuota_del_anticipo_es_un_impago_del_prestamo(tmp_path: Path) -> None:
    clock = Clock(14)
    economy, service = await rented(tmp_path, clock)
    await advance(service)
    let(tmp_path, status="okupa", tenant=None, rent=0)
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])
    for d in range(16, 16 + lending.DEFAULT_INSTALLMENTS):
        clock.to(d)
        await service.close_days(GUILD)
        if d == 16 + lending.ASNEF_INSTALLMENTS - 1:
            assert (await service.advance_desk(GUILD, ANA)).asnef
    assert sql(tmp_path, "SELECT status FROM housing_loans") == [("vencido",)]
    assert (await service.market(GUILD)).bank_debts.get(ANA, 0) > 0
