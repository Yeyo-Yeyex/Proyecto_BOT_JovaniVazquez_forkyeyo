"""Pruebas de la fase 3 de los negocios: la sociedad limitada (`docs/negocios.md`).

Con la base de datos de verdad, como `test_negocio_service.py`, del que se toman las piezas:
constituirla, que la caja sea suya (ventas, compras y local), el sueldo de administrador y
los dividendos con su IRPF, el Impuesto sobre Sociedades de cada domingo, liquidarla con sus
acreedores por orden, la causa de disolución y la pluriactividad.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_negocio_service import (
    DAY,
    GUILD,
    USER,
    Clock,
    give,
    make,
    open_cafe,
    play,
    sin_azar_de_la_pala,  # noqa: F401  (fixture autouse: sin sorpresas en la `pala`)
    wallet,
)

from bot.repositories.economy import STATE_ACCOUNT_ID, company_account
from bot.services import business_sl as sl
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.negocio import BusinessError
from bot.services.taxes import capital_gain_tax, igic


def sql(tmp_path: Path, query: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(query, args).fetchall()


async def company_cafe(tmp_path: Path, clock: Clock, capital: int = 300_000):  # noqa: ANN201
    service, work = await make(tmp_path, clock)
    await open_cafe(service)
    await give(service, 2_000_000)
    business, _total = await service.constitute(GUILD, USER, capital)
    return service, work, business


# -- Reglas ------------------------------------------------------------------------------


def test_sociedades_de_una_microempresa_va_por_tramos() -> None:
    small = sl.weekly_yd(40_000)  # 40.000 € de base al año: todo al 19 %
    assert sl.corporate_tax(round(small), weekly_sales=1)[1] == pytest.approx(0.19)
    big = round(sl.weekly_yd(100_000))  # 50.000 € al 19 % y 50.000 al 21 %
    assert sl.corporate_tax(big, weekly_sales=1)[1] == pytest.approx(0.20, abs=1e-4)
    huge = round(sl.weekly_yd(2_000_000))  # factura más de un millón: el general
    assert sl.corporate_tax(1_000, weekly_sales=huge)[1] == pytest.approx(0.25)


def test_las_perdidas_se_compensan_y_la_reserva_legal_se_queda_su_parte() -> None:
    company = sl.new_company(100, 10_000, 0)  # capital de 1 €: el 20 % a reserva
    assert sl.settle_week(company, -50_000, 10)["tax"] == 0 and company["losses"] == 50_000
    week = sl.settle_week(company, 150_000, 10)
    assert week["compensated"] == 50_000 and company["losses"] == 0
    assert week["tax"] == round(100_000 * 0.19)
    assert week["reserve"] == round(week["profit"] * 0.2)
    # Lo disponible lleva también la pérdida de la semana anterior.
    assert company["retained"] == -50_000 + week["profit"] - week["reserve"]
    full = sl.new_company(1_000_000, 1_000_000, 0)  # 10 % hasta el 20 % del capital
    assert sl.legal_reserve(full, 100_000) == 10_000
    full["reserve"] = 195_000
    assert sl.legal_reserve(full, 100_000) == 5_000


def test_los_oficios_incompatibles() -> None:
    assert sl.incompatibility("politica", "moncloa", "tienda") is not None
    assert sl.incompatibility("funcionariado", "alta", "tienda") is not None
    assert sl.incompatibility("funcionariado", "", "tienda") is None
    assert sl.incompatibility("hosteleria", "hotel", "cafeteria") is not None
    assert sl.incompatibility("hosteleria", "hotel", "taller") is None
    assert sl.incompatibility(None, "", "cafeteria") is None


def test_la_pluriactividad_devuelve_la_mitad_del_exceso_con_su_tope() -> None:
    limit = sl.weekly_yd(sl.PLURIACTIVITY_LIMIT_EUR)
    assert sl.pluriactivity_refund(round(limit) - 20_000, 10_000) == 0
    assert sl.pluriactivity_refund(round(limit), 10_000) == 5_000  # tope: la mitad del RETA
    assert sl.pluriactivity_refund(round(limit) - 8_000, 10_000) == 1_000


# -- Constituirla -------------------------------------------------------------------------


async def test_constituirla_mete_el_capital_en_su_caja_y_paga_la_notaria(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    await give(service, 2_000_000)
    _total, before = wallet(tmp_path, USER)
    state_before = wallet(tmp_path, STATE_ACCOUNT_ID)[1]
    business, total = await service.constitute(GUILD, USER, 300_000)
    fees = sl.constitution_fees(300_000)
    assert total == 300_000 + fees + igic(fees)
    assert business.is_company and business.company["capital"] == 300_000
    assert wallet(tmp_path, USER)[1] == before - total
    assert wallet(tmp_path, company_account(business.id)) == (300_000, 300_000)
    assert wallet(tmp_path, STATE_ACCOUNT_ID)[1] - state_before == igic(fees)
    with pytest.raises(BusinessError, match="ya es una sociedad"):
        await service.constitute(GUILD, USER, 300_000)


async def test_un_curro_incompatible_no_deja_constituirla(tmp_path: Path) -> None:
    clock = Clock()
    service, work = await make(tmp_path, clock)
    await work.hire(GUILD, USER, "politica")
    await open_cafe(service)
    await give(service, 2_000_000)
    with pytest.raises(BusinessError, match="Ley 3/2015"):
        await service.constitute(GUILD, USER, 300_000)


# -- La caja es suya ---------------------------------------------------------------------


async def test_las_ventas_y_las_compras_son_de_la_caja_sin_irpf(tmp_path: Path) -> None:
    clock = Clock()
    service, _work, business = await company_cafe(tmp_path, clock)
    account = company_account(business.id)
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await service.buy(GUILD, USER, product.key, 2)
    _total, owner = wallet(tmp_path, USER)
    cash = wallet(tmp_path, account)[1]
    turn = await service.start_turn(GUILD, USER)
    clock.now = play(turn.game, clock.now)
    result = await service.finish_turn(GUILD, USER, turn)
    assert result.withheld == 0 and result.summary.sales > 0
    assert wallet(tmp_path, USER)[1] == owner  # al monedero del dueño no le llega nada
    assert wallet(tmp_path, account)[1] == cash + result.summary.sales
    records = sql(
        tmp_path, "SELECT COUNT(*) FROM economy_tax_records WHERE concept = 'negocio:ventas'"
    )[0][0]
    assert records == 0
    # Con la caja vacía, la mejora no se compra aunque el dueño tenga dinero.
    await service.pay_salary(GUILD, USER, wallet(tmp_path, account)[1])
    with pytest.raises(BusinessError, match="caja de la sociedad"):
        await service.buy_equipment(GUILD, USER, "cafetera2")
    await service.contribute(GUILD, USER, 100_000)
    await service.buy_equipment(GUILD, USER, "cafetera2")


async def test_el_sueldo_de_administrador_retiene_y_cuenta_como_nomina(tmp_path: Path) -> None:
    clock = Clock()
    service, _work, business = await company_cafe(tmp_path, clock)
    _total, before = wallet(tmp_path, USER)
    _business, paid = await service.pay_salary(GUILD, USER, 100_000)
    assert paid.salary_tax == 19_000  # factura menos de 100.000 € al año: el 19 %
    assert wallet(tmp_path, USER)[1] == before + 100_000 - 19_000
    assert wallet(tmp_path, company_account(business.id))[1] == 200_000
    row = sql(
        tmp_path,
        "SELECT gross, irpf, net FROM economy_payroll WHERE concept = 'sociedad:sueldo'",
    )[0]
    assert row == (100_000, 19_000, 81_000)
    kind = sql(tmp_path, "SELECT kind FROM economy_tax_records WHERE concept = 'sociedad:sueldo'")[
        0
    ][0]
    assert kind == "trabajo"
    assert await service.economy.work_week_net(GUILD, USER) > 0  # el IMV lo ve
    with pytest.raises(BusinessError, match="caja de la sociedad"):
        await service.pay_salary(GUILD, USER, 10_000_000)


async def test_prestar_y_devolver(tmp_path: Path) -> None:
    clock = Clock()
    service, _work, business = await company_cafe(tmp_path, clock)
    await service.lend(GUILD, USER, 50_000)
    view = await service.company_view(GUILD, USER)
    assert view is not None and view.business.company["loan"] == 50_000
    with pytest.raises(BusinessError, match="Solo te debe"):
        await service.repay(GUILD, USER, 60_000)
    await service.repay(GUILD, USER, 50_000)
    assert wallet(tmp_path, company_account(business.id))[1] == 300_000


# -- El Impuesto sobre Sociedades y los dividendos ---------------------------------------


async def test_cada_domingo_paga_sociedades_y_deja_repartir_lo_que_queda(tmp_path: Path) -> None:
    clock = Clock()
    service, _work, business = await company_cafe(tmp_path, clock)
    account = company_account(business.id)
    # Una semana con un beneficio de 200.000 Y$ (lo de antes de constituirla no cuenta).
    fresh = await service.business(GUILD, USER)
    assert fresh is not None
    fresh.add("ventas", 200_000)
    await service.repository.save(GUILD, fresh)
    await service.contribute(GUILD, USER, 200_000)  # para que la caja tenga con qué pagar
    with pytest.raises(BusinessError, match="Solo puedes repartir 0"):
        await service.pay_dividend(GUILD, USER, 1_000)
    clock.now += 6 * DAY  # del martes al lunes: se cierra la noche del domingo
    runs = await service.close_nights(GUILD)
    week = next(r.corporate for r in runs if r.corporate is not None)
    assert week["base"] < 200_000 and week["tax"] > 0
    taxes = sql(tmp_path, "SELECT SUM(tax) FROM economy_business_tax WHERE kind = 'sociedades'")[0][
        0
    ]
    assert taxes == week["tax"]
    view = await service.company_view(GUILD, USER)
    assert view is not None and view.dividend_room > 0
    _total, before = wallet(tmp_path, USER)
    _business, paid = await service.pay_dividend(GUILD, USER, 50_000)
    assert paid.dividend_tax == capital_gain_tax(50_000)
    assert wallet(tmp_path, USER)[1] == before + 50_000 - paid.dividend_tax
    assert wallet(tmp_path, account)[1] == paid.company


# -- Liquidarla ------------------------------------------------------------------------


async def test_liquidarla_devuelve_el_prestamo_y_la_cuota_con_su_irpf(tmp_path: Path) -> None:
    clock = Clock()
    service, _work, business = await company_cafe(tmp_path, clock)
    await service.lend(GUILD, USER, 40_000)
    await service.contribute(GUILD, USER, 60_000)
    _total, before = wallet(tmp_path, USER)
    closing = await service.close(GUILD, USER)
    assert closing.company and closing.loan_repaid == 40_000
    assert closing.liquidated > 0
    basis = business.company["contributed"] + 60_000
    assert closing.gain_tax == capital_gain_tax(closing.liquidated - basis)
    assert wallet(tmp_path, company_account(business.id))[1] == 0
    after = wallet(tmp_path, USER)[1]
    assert after == (before + closing.refund * 0 + 40_000 + closing.liquidated - closing.gain_tax)


async def test_si_se_hunde_el_socio_responde_hasta_los_3000_euros(tmp_path: Path) -> None:
    clock = Clock()
    service, _work, business = await company_cafe(tmp_path, clock, capital=100)
    # La caja casi vacía y una deuda con el casero mucho mayor que la fianza.
    fresh = await service.business(GUILD, USER)
    assert fresh is not None
    fresh.rent_debt = fresh.deposit + 1_000_000
    await service.repository.save(GUILD, fresh)
    _total, before = wallet(tmp_path, USER)
    closing = await service.close(GUILD, USER)
    assert closing.covered == sl.FULL_CAPITAL - 100  # art. 4.1 LSC
    assert wallet(tmp_path, USER)[1] == before - closing.covered


async def test_las_perdidas_que_no_se_remedian_hacen_responder_de_todo(tmp_path: Path) -> None:
    clock = Clock()
    service, _work, business = await company_cafe(tmp_path, clock, capital=1_000_000)
    # Se gasta casi toda la caja: el patrimonio neto baja de la mitad del capital.
    await service.pay_salary(GUILD, USER, 900_000)
    clock.now += DAY
    await service.close_nights(GUILD)
    fresh = await service.business(GUILD, USER)
    assert fresh is not None and fresh.company["cause_since"]
    clock.now += 2 * DAY
    await service.close_nights(GUILD)
    fresh = await service.business(GUILD, USER)
    assert fresh is not None
    fresh.rent_debt = fresh.deposit + 2_000_000
    await service.repository.save(GUILD, fresh)
    closing = await service.close(GUILD, USER)
    assert closing.covered > sl.guarantee(fresh.company)  # responde de más que la garantía


# -- Lo demás --------------------------------------------------------------------------


async def test_el_local_de_una_sociedad_no_sale_en_el_saldo_del_dueno(tmp_path: Path) -> None:
    clock = Clock()
    service, _work, _business = await company_cafe(tmp_path, clock)
    tonight, debt, _fee = await service.dues(GUILD, USER)
    assert tonight == debt == 0


async def test_la_pluriactividad_devuelve_cuotas_el_domingo(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    # Una nómina con mucha cotización esta semana: lo de los dos lados pasa del límite.
    sql(
        tmp_path,
        "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
        "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', 400000, 30000, "
        "90000, 0, 370000, ?)",
        GUILD, USER, clock.now,
    )  # fmt: skip
    _total, before = wallet(tmp_path, USER)
    clock.now += 6 * DAY
    await service.close_nights(GUILD)
    refunds = sql(
        tmp_path,
        "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE user_id = ? "
        "AND reason = 'seguridad_social:reintegro'",
        USER,
    )[0][0]
    assert refunds > 0
    assert wallet(tmp_path, USER)[1] >= before - 100_000  # cobra el reintegro (y paga noches)
