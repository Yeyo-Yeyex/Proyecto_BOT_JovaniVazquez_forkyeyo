"""Pruebas del cálculo de IRPF de `bot.services.taxes`."""

from __future__ import annotations

import pytest

from bot.services.taxes import (
    STATE_BRACKETS,
    annual_tax,
    apply_scale,
    compute_withholding,
    format_rate,
    withholding_rate,
)


def test_escala_estatal_por_tramos() -> None:
    # 12.450 × 9,5 % + 7.750 × 12 % + 9.800 × 15 %
    assert apply_scale(30_000, STATE_BRACKETS) == pytest.approx(3_582.75)


def test_cuota_anual_estatal_mas_canarias_descontando_minimos() -> None:
    # Estatal: 3.582,75 − 527,25 (mínimo 5.550) = 3.055,50
    # Canarias: 3.370,75 − 504,54 (mínimo 5.606) = 2.866,21
    assert annual_tax(30_000) == pytest.approx(5_921.71)


def test_por_debajo_del_minimo_personal_no_se_paga() -> None:
    assert annual_tax(5_000) == 0
    assert withholding_rate(50_000) == 0  # 5.000 € anuales


def test_el_tipo_sube_con_la_renta() -> None:
    rates = [withholding_rate(euros * 10) for euros in (10_000, 30_000, 60_000, 200_000)]
    assert rates == sorted(rates)
    assert rates[-1] < 0.5


def test_retencion_proyecta_los_ultimos_30_dias() -> None:
    nuevo = compute_withholding(1_000, recent_income=0)
    habitual = compute_withholding(1_000, recent_income=29_000)

    assert nuevo.tax == 0
    assert habitual.tax > 0
    assert habitual.net == 1_000 - habitual.tax
    # 30.000 Y$ en 30 días → 365.000 Y$/año → 36.500 €
    assert habitual.rate == withholding_rate(365_000)


def test_formato_del_tipo() -> None:
    assert format_rate(0.2158) == "21,58 %"
