"""Pruebas de la nómina de `pala` en `bot.services.taxes`: Seguridad Social e IRPF."""

from __future__ import annotations

import pytest

from bot.services.taxes import (
    SS_EMPLOYER_RATE,
    SS_MAX_BASE_EUR,
    SS_WORKER_RATE,
    Pace,
    WeekIncome,
    compute_payslip,
    compute_pension_payslip,
    compute_self_employed_payslip,
    payroll_rates,
    solidarity_contribution,
    week_tax,
    weekly_yd,
    work_income_reduction,
)


def full_week(gross: int, shifts: int = 27) -> Pace:
    """Lo que mira la retención con `shifts` turnos de `gross` ya cobrados en la semana."""
    wages = gross * shifts
    return Pace(wages=wages, current=WeekIncome(work=wages, social=round(wages * SS_WORKER_RATE)))


def test_cotizacion_del_trabajador_es_la_de_2026() -> None:
    # 4,70 contingencias comunes + 1,55 desempleo + 0,10 formación + 0,15 MEI
    assert SS_WORKER_RATE == pytest.approx(0.065)
    # 23,60 + 5,50 + 0,20 + 0,60 + 0,75 + 1,5 de accidentes de trabajo
    assert SS_EMPLOYER_RATE == pytest.approx(0.3215)


def test_reduccion_por_rendimientos_del_trabajo_por_tramos() -> None:
    assert work_income_reduction(10_000) == 7_302
    assert work_income_reduction(14_852) == 7_302
    assert work_income_reduction(16_000) == pytest.approx(7_302 - 1.75 * (16_000 - 14_852))
    assert work_income_reduction(18_000) == pytest.approx(2_364.34 - 1.14 * (18_000 - 17_673.52))
    assert work_income_reduction(25_000) == 0


def test_la_solidaridad_solo_grava_lo_que_pasa_de_la_base_maxima() -> None:
    assert solidarity_contribution(SS_MAX_BASE_EUR) == 0
    over = SS_MAX_BASE_EUR * 1.05
    assert solidarity_contribution(over) == pytest.approx((over - SS_MAX_BASE_EUR) * 0.0115)


def test_por_debajo_de_la_base_maxima_se_cotiza_el_tipo_entero() -> None:
    rates = payroll_rates(30_000)
    assert rates.ss_worker == pytest.approx(SS_WORKER_RATE)
    assert not rates.over_max_base


def test_por_encima_de_la_base_maxima_el_tipo_efectivo_baja() -> None:
    rates = payroll_rates(200_000)
    assert rates.over_max_base
    assert rates.ss_worker < SS_WORKER_RATE
    assert rates.ss_employer < SS_EMPLOYER_RATE


def test_sueldos_bajos_no_pagan_irpf_gracias_a_la_reduccion() -> None:
    # 14.600 € al año: la reducción del art. 20 y el mínimo personal lo dejan a cero.
    wages = round(weekly_yd(14_600))
    income = WeekIncome(work=wages, social=round(wages * SS_WORKER_RATE))
    assert week_tax(income).quota == 0


def test_la_seguridad_social_proyecta_las_nominas_de_la_semana() -> None:
    """La base de cotización mira las nóminas de la semana (o las de la anterior, si más)."""
    gross = 30_000
    light = compute_payslip(gross, Pace())
    heavy = compute_payslip(gross, full_week(gross))
    remembered = compute_payslip(gross, Pace(previous_wages=gross * 28))
    assert light.rates.ss_worker == pytest.approx(SS_WORKER_RATE)
    assert heavy.rates.ss_worker < SS_WORKER_RATE
    assert remembered.ss_worker == heavy.ss_worker


def test_la_nomina_cuadra_bruto_menos_cotizacion_menos_irpf() -> None:
    slip = compute_payslip(10_000, full_week(10_000))
    assert slip.net == slip.gross - slip.ss_worker - slip.irpf
    assert slip.total_taxes == slip.ss_worker + slip.irpf + slip.ss_employer
    assert slip.employer_cost == slip.gross + slip.ss_employer
    assert slip.irpf > 0 and slip.ss_worker > 0


def test_la_nomina_retiene_con_el_tipo_de_la_semana_pasada_si_es_mayor() -> None:
    slip = compute_payslip(10_000, Pace(previous_rate=0.3))
    assert slip.rates.irpf == 0.3
    assert slip.irpf == round(10_000 * 0.3)


def test_cobrar_mas_nunca_deja_menos_neto() -> None:
    """El mito de «me suben de tramo y gano menos»: aquí tampoco pasa."""
    nets = [compute_payslip(gross, full_week(gross)).net for gross in range(500, 50_000, 500)]
    assert nets == sorted(nets)


def test_el_autonomo_no_cotiza_por_turno() -> None:
    slip = compute_self_employed_payslip(10_000, full_week(10_000))
    assert slip.ss_worker == 0 and slip.ss_employer == 0
    assert slip.net == 10_000 - slip.irpf


def test_la_pension_no_cotiza_y_retiene_como_trabajo() -> None:
    slip = compute_pension_payslip(20_000, full_week(20_000))
    assert slip.ss_worker == 0 and slip.ss_employer == 0
    assert slip.irpf > 0


def test_un_bruto_no_positivo_no_es_una_nomina() -> None:
    with pytest.raises(ValueError):
        compute_payslip(0, Pace())
