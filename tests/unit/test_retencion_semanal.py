"""Una sola renta y una sola semana para todas las retenciones.

La nómina de `pala` proyecta con toda la renta sujeta de los últimos 7 días
(premios, casino en positivo y nóminas), igual que los premios y el casino.
Antes solo miraba las nóminas: quien ganaba eventos al 30 % trabajaba al 0 %.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from bot.repositories.economy import EconomyRepository
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.taxes import (
    PROJECTION_WINDOW_SECONDS,
    compute_hk_payslip,
    fiscal_to_wage,
    wage_to_fiscal,
)

GUILD = 1
ANA = 10
BEA = 20
NOW = datetime(2026, 10, 6, 18, tzinfo=TIMEZONE).timestamp()
#: Bruto de un turno del puesto 1: solo, no llega al mínimo.
TURNO = 1_700
#: Un premio gordo de la semana (escala general).
PREMIO = 30_000


class Clock:
    """Reloj controlable."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def economy_with(tmp_path: Path) -> tuple[EconomyService, Clock]:
    repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repo.initialize()
    clock = Clock()
    return EconomyService(repo, clock=clock), clock


def test_la_ventana_es_la_semana_de_la_renta() -> None:
    assert PROJECTION_WINDOW_SECONDS == 7 * 24 * 3600


def test_pasar_de_escala_y_volver_apenas_pierde() -> None:
    assert fiscal_to_wage(170) == 1_700
    assert abs(fiscal_to_wage(wage_to_fiscal(1_755)) - 1_755) <= 5


async def test_un_turno_suelto_sin_mas_renta_no_paga_irpf(tmp_path: Path) -> None:
    economy, _ = await economy_with(tmp_path)

    salary = await economy.pay_salary(GUILD, BEA, gross=TURNO, concept="pala:obra")

    assert salary.payslip.irpf == 0


async def test_la_nomina_suma_los_premios_de_la_semana(tmp_path: Path) -> None:
    economy, _ = await economy_with(tmp_path)
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    state_before = await economy.balance(GUILD, STATE_ACCOUNT_ID)

    salary = await economy.pay_salary(GUILD, ANA, gross=TURNO, concept="pala:obra")
    alone = await economy.pay_salary(GUILD, BEA, gross=TURNO, concept="pala:obra")

    slip = salary.payslip
    assert slip.irpf > 0
    assert alone.payslip.irpf == 0
    # El Estado recibe la retención y las dos cotizaciones, ni más ni menos.
    state_after = await economy.balance(GUILD, STATE_ACCOUNT_ID)
    bea = alone.payslip
    assert state_after - state_before == slip.total_taxes + bea.total_taxes


async def test_el_casino_en_positivo_tambien_cuenta(tmp_path: Path) -> None:
    economy, _ = await economy_with(tmp_path)
    await economy.grant(GUILD, ANA, amount=10_000, reason="prueba")
    await economy.settle_bet(GUILD, ANA, game="slots", stake=1_000, payout=1_000 + PREMIO)

    salary = await economy.pay_salary(GUILD, ANA, gross=TURNO, concept="pala:obra")

    assert salary.payslip.irpf > 0


async def test_lo_de_hace_mas_de_una_semana_ya_no_cuenta(tmp_path: Path) -> None:
    economy, clock = await economy_with(tmp_path)
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    clock.now += PROJECTION_WINDOW_SECONDS + 60

    salary = await economy.pay_salary(GUILD, ANA, gross=TURNO, concept="pala:obra")

    assert salary.payslip.irpf == 0


async def test_autonomo_y_beckham_tambien_suman_toda_la_renta(tmp_path: Path) -> None:
    economy, _ = await economy_with(tmp_path)
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    alone = await economy.pay_salary(
        GUILD, BEA, gross=TURNO, concept="pala:chiringuito", self_employed=True
    )

    salary = await economy.pay_salary(
        GUILD, ANA, gross=TURNO, concept="pala:chiringuito", self_employed=True
    )

    assert salary.payslip.irpf > alone.payslip.irpf


async def test_los_premios_siguen_viendo_la_nomina(tmp_path: Path) -> None:
    economy, _ = await economy_with(tmp_path)
    for _ in range(20):
        await economy.pay_salary(GUILD, ANA, gross=25_000, concept="pala:politica")

    worker = await economy.pay_income(GUILD, ANA, gross=1_000, concept="nivel")
    idle = await economy.pay_income(GUILD, BEA, gross=1_000, concept="nivel")

    assert worker.tax > idle.tax


def test_hong_kong_residente_suma_la_renta_espanola() -> None:
    sin = compute_hk_payslip(TURNO * 10, recent_hk=0, resident=True, exempt_left=0)
    con = compute_hk_payslip(
        TURNO * 10, recent_hk=0, resident=True, exempt_left=0, recent_es=fiscal_to_wage(PREMIO)
    )

    assert con.irpf > sin.irpf
    # Hong Kong solo mira lo de Hong Kong.
    assert con.hk_tax == sin.hk_tax
