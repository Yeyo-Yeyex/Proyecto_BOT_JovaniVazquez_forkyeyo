"""Pruebas del desglose por conceptos, el consejo de los donativos, los sellos y la carta de
la Renta (`bot.services.renta_report`)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from test_renta import GUILD, USER, Clock, close, day, make_service

from bot.repositories.economy import PAYMENT_PAID, PAYMENT_PENDING, PAYMENT_SPLIT, IrpfReturn
from bot.services.donations import BY_KEY
from bot.services.economy import EconomyService, IrpfStatement, monday_epoch
from bot.services.renta_report import (
    CARD_ROWS,
    EXPIRED,
    FILED,
    OWED,
    PAID,
    SPLIT,
    advice_text,
    card_state,
    concepts,
    concepts_text,
    donation_advice,
    result_line,
    seal_for,
)
from bot.services.taxes import (
    CLAIM_WEEKS,
    DONATION_BASE_LIMIT,
    DONATION_FULL_LIMIT,
    DONATION_FULL_RATE,
    INCOME_BUSINESS,
    INCOME_IMAGE,
    INCOME_PRIZE,
    INCOME_WORK,
    PropertyWeek,
    WeekIncome,
    week_return,
)

WEEK = date(2026, 10, 5)
#: Un sueldo semanal que paga IRPF de sobra para que los donativos desgraven enteros.
SALARY = 200_000


def statement_of(income: WeekIncome, withheld_by: dict[str, int], **kwargs: int) -> IrpfStatement:
    """Una declaración hecha a mano con `week_return`, sin base de datos."""
    arguments = {"withheld": sum(withheld_by.values()), **kwargs}
    detail = week_return(income, **arguments)
    filed = IrpfReturn(
        user_id=USER,
        base=detail.tax.base,
        quota=detail.tax.quota,
        deductions=detail.deductions,
        withheld=detail.withheld,
        result=detail.result,
        rate=detail.tax.rate,
    )
    return IrpfStatement(
        week=WEEK,
        filed=filed,
        detail=detail,
        income=income,
        withheld_by=withheld_by,
        opened=True,
        arguments=arguments,
    )


def balances(rows: tuple, statement: IrpfStatement) -> tuple[int, int]:
    """`(Σ cuota de los conceptos, Σ cuota − deducciones − Σ retenido)`."""
    quota = sum(c.quota for c in rows)
    assert statement.detail is not None
    return quota, quota - statement.detail.deductions - sum(c.withheld for c in rows)


# -- Por conceptos ---------------------------------------------------------------------------


async def test_los_conceptos_reparten_la_cuota_y_dan_el_resultado(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.pay_salary(GUILD, USER, gross=SALARY, concept="pala:obra")
    await service.pay_income(GUILD, USER, gross=50_000, concept="nivel:10")
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    ong = BY_KEY["hambre"]
    await service.donate(GUILD, USER, ong_key=ong.key, ong_account=ong.account_id, amount=500)
    filed = await close(service, clock, 12)

    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None and statement.detail is not None
    rows = concepts(statement)

    assert {c.key for c in rows} == {"trabajo", "premios", "casino"}
    assert balances(rows, statement) == (statement.detail.tax.quota, filed.result)
    assert all(c.quota > 0 for c in rows)
    text = concepts_text(statement)
    assert "Trabajo y pensiones" in text and "Deducciones" in text and "de cuota" in text


def test_un_piso_en_negativo_rebaja_la_cuota_y_la_cuenta_sigue_cuadrando() -> None:
    income = WeekIncome(
        work=SALARY,
        properties=(PropertyWeek(ref="piso", income=2_000, costs=30_000),),
        prizes=5_000,
    )
    statement = statement_of(income, {INCOME_WORK: 20_000, INCOME_PRIZE: 1_000, "raro": 300})
    rows = concepts(statement)
    piso = next(c for c in rows if c.key == "alquileres")

    assert piso.base < 0 and piso.quota < 0
    assert next(c for c in rows if c.key == "otros").withheld == 300
    assert balances(rows, statement) == (statement.filed.quota, statement.filed.result)
    assert "Te ahorra" in concepts_text(statement)


def test_una_declaracion_sin_entradas_no_tiene_conceptos() -> None:
    statement = statement_of(WeekIncome(work=SALARY), {INCOME_WORK: 1_000})
    old = IrpfStatement(
        week=WEEK,
        filed=statement.filed,
        detail=None,
        income=WeekIncome(),
        withheld_by={},
        opened=True,
    )
    assert concepts(old) == ()
    assert donation_advice(old) is None
    assert "solo tiene el resumen" in concepts_text(old)


# -- El consejo de los donativos --------------------------------------------------------------


async def _closed_with_salary(tmp_path: Path, donated: int = 0) -> tuple[EconomyService, int]:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.pay_salary(GUILD, USER, gross=SALARY, concept="pala:obra")
    if donated:
        ong = BY_KEY["hambre"]
        await service.donate(
            GUILD, USER, ong_key=ong.key, ong_account=ong.account_id, amount=donated
        )
    filed = await close(service, clock, 12)
    return service, filed.result


async def test_el_consejo_dice_lo_que_pasa_de_verdad_si_donas_eso(tmp_path: Path) -> None:
    service, result = await _closed_with_salary(tmp_path / "sin")
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None and statement.detail is not None
    advice = donation_advice(statement)

    assert advice is not None and advice.donated == 0
    ceiling = int(statement.detail.tax.base * DONATION_BASE_LIMIT)
    assert advice.more == min(DONATION_FULL_LIMIT, ceiling) > 0
    assert advice.saved == round(advice.more * DONATION_FULL_RATE)
    # Quien dona eso mismo esa semana paga justo lo que dice el consejo.
    _other, donated_result = await _closed_with_salary(tmp_path / "con", advice.more)
    assert result - donated_result == advice.saved
    text = advice_text(statement, advice)
    assert "no donaste nada" in text and "te habría costado de verdad" in text


async def test_si_ya_donaste_el_tramo_del_80_no_te_pide_mas(tmp_path: Path) -> None:
    service, _result = await _closed_with_salary(tmp_path, DONATION_FULL_LIMIT)
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None
    advice = donation_advice(statement)

    assert advice is not None and advice.more == 0 and advice.deducted > 0
    text = advice_text(statement, advice)
    assert "ya aprovechaste" in text and "40 %" in text


async def test_sin_irpf_que_rebajar_donar_no_devuelve_nada(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.pay_income(GUILD, USER, gross=1_000, concept="nivel:2")
    await close(service, clock, 12)
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None
    advice = donation_advice(statement)

    assert advice is not None and advice.more == 0 and advice.saved == 0
    assert "no te habría devuelto nada" in advice_text(statement, advice)


# -- Sellos ------------------------------------------------------------------------------------


def _with(**changes: object) -> IrpfStatement:
    """Una declaración sin entradas con los campos de `IrpfReturn` que se pidan."""
    values: dict[str, object] = {
        "user_id": USER,
        "base": 1,
        "quota": 1,
        "deductions": 0,
        "withheld": 0,
        "result": 0,
    }
    values.update(changes)
    return IrpfStatement(
        week=WEEK,
        filed=IrpfReturn(**values),  # type: ignore[arg-type]
        detail=None,
        income=WeekIncome(),
        withheld_by={},
        opened=True,
    )


def test_el_sello_dice_lo_que_ya_se_hizo_con_la_declaracion() -> None:
    soon = monday_epoch(WEEK + timedelta(weeks=1))
    late = monday_epoch(WEEK + timedelta(weeks=1 + CLAIM_WEEKS))
    pay = {"result": 500}
    assert seal_for(_with(**pay, payment=PAYMENT_PENDING), [], soon) == ""
    assert seal_for(_with(**pay, payment=PAYMENT_PAID, charged=500), [], soon) == PAID
    assert seal_for(_with(**pay, payment=PAYMENT_SPLIT, second=200), [], soon) == SPLIT
    assert seal_for(_with(**pay, payment=PAYMENT_SPLIT, second_at=9.0), [], soon) == PAID
    assert seal_for(_with(**pay, payment=PAYMENT_PAID, debt=100), [], soon) == OWED
    # Las de antes de la 8.7.0 se cobraban solas.
    assert seal_for(_with(**pay), [], soon) == PAID

    back = _with(result=-300, refund=300)
    assert seal_for(back, [WEEK], soon) == ""
    assert seal_for(back, [], soon) == FILED
    assert seal_for(back, [], late) == EXPIRED
    assert "Ya la presentaste" in result_line(back, FILED)
    assert "preséntala" in result_line(back)
    assert seal_for(_with(result=-300, compensated=300), [], soon) == ""


# -- La carta ----------------------------------------------------------------------------------


def test_la_carta_junta_lo_que_no_cabe_y_dice_el_resultado() -> None:
    income = WeekIncome(
        work=SALARY, business=20_000, prizes=10_000, gambling=8_000, image=4_000, imputed=1_000
    )
    withheld = {INCOME_WORK: 10_000, INCOME_BUSINESS: 500, INCOME_PRIZE: 100, INCOME_IMAGE: 960}
    statement = statement_of(income, withheld)
    state = card_state(statement, "Diego", FILED)

    rows = state["rows"]
    assert isinstance(rows, list) and len(rows) == CARD_ROWS
    assert rows[-1]["name"] == "Otros (3)"
    assert state["tone"] == "pay" and state["verdict"] == "A PAGAR"
    assert state["seal"] == FILED and state["name"] == "Diego"
    assert state["earned"] == SALARY + 20_000 + 10_000 + 8_000 + 4_000 + 1_000
    assert 0 < int(state["per100"]) < 100  # type: ignore[call-overload]

    refund = card_state(statement_of(WeekIncome(work=SALARY), {INCOME_WORK: 90_000}), "X")
    assert refund["tone"] == "refund" and refund["verdict"] == "A DEVOLVER"
