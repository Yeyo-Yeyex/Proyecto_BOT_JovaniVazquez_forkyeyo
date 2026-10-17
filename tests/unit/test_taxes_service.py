"""Pruebas del IRPF de `bot.services.taxes`: la vara única, la semana y la declaración."""

from __future__ import annotations

import pytest

from bot.services.taxes import (
    DONATION_FULL_LIMIT,
    GAMBLING_EXEMPT,
    GAMBLING_WITHHOLDING_RATE,
    HOME_DEDUCTION_BASE_EUR,
    HOME_DEDUCTION_QUOTA_SHARE,
    HOME_DEDUCTION_RATES,
    IRPF_YAPDOLLARS_PER_EURO,
    LANDLORD_DEDUCTION_MAX_EUR,
    LANDLORD_DEDUCTION_RATE,
    STATE_BRACKETS,
    STATE_PERSONAL_MINIMUM,
    TENANT_DEDUCTION_MAX_EUR,
    TENANT_DEDUCTION_RATE,
    TENANT_INCOME_MAX_EUR,
    WORK_REDUCTION_OTHER_INCOME_EUR,
    Pace,
    PropertyWeek,
    WeekIncome,
    annual_eur,
    annual_tax,
    apply_scale,
    compute_withholding,
    format_rate,
    gambling_day_tax,
    home_deduction,
    landlord_deduction,
    legacy_weekly_refund,
    property_withholding,
    regional_tax,
    stable_rate,
    tenant_deduction,
    week_return,
    week_tax,
    weekly_yd,
)


def yd(euros: float) -> int:
    """Euros de un año → Y$ de una semana, redondeado (para escribir casos en euros)."""
    return round(weekly_yd(euros))


def test_escala_estatal_por_tramos() -> None:
    # 12.450 × 9,5 % + 7.750 × 12 % + 9.800 × 15 %
    assert apply_scale(30_000, STATE_BRACKETS) == pytest.approx(3_582.75)


def test_cuota_anual_estatal_mas_canarias_descontando_minimos() -> None:
    # Estatal: 3.582,75 − 527,25 (mínimo 5.550) = 3.055,50
    # Canarias: 3.370,75 − 504,54 (mínimo 5.606) = 2.866,21
    assert annual_tax(30_000) == pytest.approx(5_921.71)
    assert regional_tax(30_000) == pytest.approx(2_866.21)


def test_la_semana_es_el_ano_con_la_vara_del_irpf() -> None:
    assert annual_eur(IRPF_YAPDOLLARS_PER_EURO) == pytest.approx(52)
    assert weekly_yd(annual_eur(12_345)) == pytest.approx(12_345)


def test_por_debajo_del_minimo_personal_no_se_paga() -> None:
    tax = week_tax(WeekIncome(prizes=yd(STATE_PERSONAL_MINIMUM - 100)))
    assert tax.quota == 0
    assert tax.rate == 0


def test_el_tipo_sube_con_la_renta() -> None:
    rates = [week_tax(WeekIncome(prizes=yd(e))).rate for e in (10_000, 30_000, 60_000, 200_000)]
    assert rates == sorted(rates)
    assert 0 < rates[-1] < 0.5


def test_una_sola_vara_el_mismo_dinero_paga_lo_mismo_venga_de_donde_venga() -> None:
    """Un premio, una ganancia del casino o una actividad: misma base, misma cuota."""
    amount = yd(40_000)
    quotas = {
        week_tax(WeekIncome(prizes=amount)).quota,
        week_tax(WeekIncome(gambling=amount)).quota,
        week_tax(WeekIncome(business=amount)).quota,
    }
    assert len(quotas) == 1


def test_trabajo_seguridad_social_gastos_y_reduccion_del_articulo_20() -> None:
    gross, social = yd(14_000), yd(14_000 * 0.065)
    tax = week_tax(WeekIncome(work=gross, social=social))
    # Neto 13.090 €: la reducción (7.302 €) y los 2.000 € de gastos lo dejan en el mínimo.
    assert tax.expenses == yd(2_000)
    assert tax.reduction == pytest.approx(yd(7_302), abs=2)
    assert tax.quota == 0


def test_sin_reduccion_del_articulo_20_si_las_otras_rentas_pasan_de_6500() -> None:
    work = WeekIncome(work=yd(14_000), social=yd(910))
    extra = yd(WORK_REDUCTION_OTHER_INCOME_EUR + 1_000)
    assert week_tax(work.plus(prizes=extra)).reduction == 0
    assert week_tax(work.plus(prizes=yd(1_000))).reduction > 0


def test_intereses_y_reparaciones_no_pasan_de_lo_que_renta_el_piso() -> None:
    item = PropertyWeek("piso", income=10_000, finance=15_000, costs=0, reduction=0.5)
    settled = item.settle()
    assert settled.net == 0
    assert settled.finance_left == 5_000


def test_lo_arrastrado_se_usa_antes_que_lo_de_esta_semana() -> None:
    item = PropertyWeek("piso", income=10_000, finance=6_000, carry=7_000)
    settled = item.settle()
    assert settled.carry_used == 7_000
    assert settled.finance_left == 3_000
    assert settled.net == 0


def test_la_reduccion_solo_va_sobre_el_neto_positivo() -> None:
    positive = PropertyWeek("a", income=10_000, costs=2_000, reduction=0.5).settle()
    negative = PropertyWeek("b", income=10_000, costs=12_000, reduction=0.5).settle()
    assert positive.net == 4_000
    assert negative.net == -2_000


def test_el_negativo_del_piso_rebaja_el_irpf_de_la_nomina() -> None:
    """Desgravar la hipoteca: el resto de gastos puede dejar el piso en negativo (art. 48)."""
    nomina = WeekIncome(work=yd(60_000), social=yd(3_900))
    piso = PropertyWeek("piso", income=yd(5_000), finance=yd(5_000), costs=yd(4_000))
    assert week_tax(nomina.plus(prop=piso)).quota < week_tax(nomina).quota


def test_las_perdidas_del_juego_no_rebajan_nada_mas() -> None:
    nomina = WeekIncome(work=yd(60_000), social=yd(3_900))
    assert week_tax(nomina.plus(gambling=-yd(20_000))).quota == week_tax(nomina).quota
    assert week_tax(nomina.plus(gambling=yd(5_000))).quota > week_tax(nomina).quota


def test_la_base_negativa_pasa_a_las_semanas_siguientes() -> None:
    bad = week_tax(WeekIncome(properties=(PropertyWeek("x", income=1_000, costs=9_000),)))
    assert bad.base == 0
    assert bad.negative == 8_000
    later = week_tax(WeekIncome(prizes=yd(30_000), negative=bad.negative))
    assert later.negative_used == 8_000
    assert later.base == yd(30_000) - 8_000


def test_la_imputacion_suma_a_la_base_pero_no_retiene() -> None:
    tax = week_tax(WeekIncome(prizes=yd(20_000), imputed=yd(5_000)))
    assert tax.base == pytest.approx(yd(25_000), abs=1)
    assert tax.contributions == yd(20_000)


def test_la_ley_beckham_va_aparte_de_la_escala() -> None:
    tax = week_tax(WeekIncome(beckham=yd(100_000)))
    assert tax.beckham == pytest.approx(yd(24_000), abs=2)
    assert tax.base == 0


def test_el_tipo_estable_es_el_de_la_semana_pasada_o_el_de_esta_si_es_mayor() -> None:
    pace = Pace(previous_rate=0.20)
    assert stable_rate(pace, WeekIncome(prizes=yd(10_000))) == 0.20
    big = WeekIncome(prizes=yd(300_000))
    assert stable_rate(pace, big) == week_tax(big).rate > 0.20


def test_el_tipo_de_la_semana_nunca_baja_al_cobrar_mas() -> None:
    income, rates = WeekIncome(), []
    for _ in range(40):
        income = income.plus(work=yd(5_000), social=yd(325))
        rates.append(stable_rate(Pace(), income))
    assert rates == sorted(rates)


def test_un_premio_retiene_con_el_tipo_estable() -> None:
    pace = Pace(previous_rate=0.25, current=WeekIncome(work=yd(30_000), social=yd(1_950)))
    held = compute_withholding(10_000, pace)
    assert held.rate == stable_rate(pace, pace.current.plus(prizes=10_000))
    assert held.tax == round(10_000 * held.rate)
    assert held.net == 10_000 - held.tax


def test_el_casino_retiene_el_19_por_ciento_de_lo_ganado_en_el_dia() -> None:
    big = GAMBLING_EXEMPT + 1
    assert gambling_day_tax(big) == round(big * GAMBLING_WITHHOLDING_RATE)
    assert gambling_day_tax(0) == gambling_day_tax(-500) == 0


def test_los_dias_que_se_gana_poco_no_retienen_como_los_premios_de_300_euros() -> None:
    # Art. 75.3.f RIRPF: sin retención hasta 300 € (con la vara del IRPF); si pasa,
    # retiene todo, no solo lo que pasa.
    assert GAMBLING_EXEMPT == round(weekly_yd(300))
    assert gambling_day_tax(GAMBLING_EXEMPT) == 0
    assert gambling_day_tax(GAMBLING_EXEMPT + 1) == round((GAMBLING_EXEMPT + 1) * 0.19)


def test_la_renta_del_piso_retiene_sobre_su_neto_reducido() -> None:
    item = PropertyWeek("piso", income=10_000, finance=1_000, costs=1_000, reduction=0.5)
    held = property_withholding(item, Pace(previous_rate=0.3))
    assert held.tax == round(4_000 * held.rate)


def test_la_declaracion_sale_a_pagar_si_se_retuvo_de_menos() -> None:
    income = WeekIncome(prizes=yd(50_000))
    filed = week_return(income, withheld=0)
    assert filed.result == week_tax(income).quota > 0


def test_la_declaracion_sale_a_devolver_si_se_retuvo_de_mas() -> None:
    income = WeekIncome(gambling=yd(8_000))
    filed = week_return(income, withheld=gambling_day_tax(yd(8_000)))
    assert filed.result < 0


def test_donativos_80_y_40_con_el_limite_del_10_por_ciento_de_la_base() -> None:
    income = WeekIncome(prizes=yd(60_000))
    base = week_tax(income).base
    gift = DONATION_FULL_LIMIT * 3
    filed = week_return(income, withheld=0, donated=gift)
    assert filed.donations == round(DONATION_FULL_LIMIT * 0.8 + DONATION_FULL_LIMIT * 2 * 0.4)
    huge = week_return(income, withheld=0, donated=base)
    eligible = int(base * 0.10)
    assert huge.donations == round(
        DONATION_FULL_LIMIT * 0.8 + (eligible - DONATION_FULL_LIMIT) * 0.4
    )


def test_deduccion_canaria_por_vivienda_habitual_por_tramos_y_con_tope() -> None:
    low_rate = HOME_DEDUCTION_RATES[0][1]
    cap = yd(HOME_DEDUCTION_BASE_EUR)
    assert home_deduction(cap * 3, income=yd(20_000), regional=10**9) == round(cap * low_rate)
    mid = HOME_DEDUCTION_RATES[1][1]
    assert home_deduction(cap, income=yd(30_000), regional=10**9) == round(cap * mid)
    assert home_deduction(cap, income=yd(60_000), regional=10**9) == 0
    # Con las de rehabilitación, no pasa del 15 % de la cuota autonómica.
    assert home_deduction(cap, income=yd(20_000), regional=1_000) == round(
        1_000 * HOME_DEDUCTION_QUOTA_SHARE
    )


def test_deduccion_canaria_del_inquilino() -> None:
    income = yd(20_000)
    rent = yd(6_000)
    assert tenant_deduction(rent, income=income) == min(
        round(rent * TENANT_DEDUCTION_RATE), yd(TENANT_DEDUCTION_MAX_EUR)
    )
    # El alquiler tiene que pasar del 10 % de la renta y la renta no puede ser alta.
    assert tenant_deduction(round(income * 0.05), income=income) == 0
    assert tenant_deduction(rent, income=yd(TENANT_INCOME_MAX_EUR + 1_000)) == 0


def test_deduccion_canaria_del_casero_con_tope_por_inmueble() -> None:
    cap = yd(LANDLORD_DEDUCTION_MAX_EUR)
    small = PropertyWeek("a", income=10_000, upkeep=1_000)
    big = PropertyWeek("b", income=10_000, upkeep=cap * 100)
    assert landlord_deduction((small, big)) == round(1_000 * LANDLORD_DEDUCTION_RATE) + cap


def test_las_deducciones_canarias_no_pasan_de_la_cuota_autonomica() -> None:
    income = WeekIncome(prizes=yd(9_000))  # cuota pequeña
    filed = week_return(income, withheld=0, rent_paid=yd(9_000))
    assert filed.home + filed.rental <= filed.tax.regional


def test_la_doble_imposicion_no_pasa_de_lo_que_pagaria_aqui() -> None:
    income = WeekIncome(work=yd(80_000))
    filed = week_return(income, withheld=0, foreign_credit=10**9, foreign_income=yd(80_000))
    assert filed.foreign == min(round(yd(80_000) * filed.tax.rate), filed.tax.quota)
    assert filed.due >= 0


def test_la_renta_se_guarda_y_se_recupera_igual() -> None:
    income = WeekIncome(
        work=1, social=2, business=3, prizes=4, gambling=-5, image=6, beckham=7, imputed=8,
        negative=9, properties=(PropertyWeek("x", 10, 11, 12, 0.9, 13, 14),),
    )  # fmt: skip
    assert WeekIncome.from_dict(income.to_dict()) == income


def test_las_semanas_de_antes_de_la_8_se_cierran_con_su_regla() -> None:
    assert legacy_weekly_refund(net=-5_000, withheld=700, other_recent_income=0) == 700
    assert legacy_weekly_refund(net=35_000, withheld=0, other_recent_income=0) == 0


def test_formato_del_tipo() -> None:
    assert format_rate(0.2158) == "21,58 %"
