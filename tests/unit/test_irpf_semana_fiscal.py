"""El año fiscal es la semana: la renta de las retenciones se reinicia cada lunes.

Y la regularización única que devuelve lo retenido de más cuando la renta
arrastraba los últimos 7 días.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from bot.cogs.renta import regularization_text
from bot.repositories.economy import EconomyRepository, LedgerEntry
from bot.services.economy import (
    STARTING_BALANCE,
    STATE_ACCOUNT_ID,
    EconomyService,
    fiscal_window,
)
from bot.services.levels import TIMEZONE
from bot.services.taxes import (
    PROJECTION_WINDOW_SECONDS,
    compute_payslip,
    compute_withholding,
    fiscal_to_wage,
    gambling_day_tax,
)

GUILD = 1
ANA = 10
BEA = 20
DOMINGO = datetime(2026, 10, 4, 20, tzinfo=TIMEZONE).timestamp()
LUNES = datetime(2026, 10, 5, 10, tzinfo=TIMEZONE).timestamp()
PREMIO = 30_000
OLD_WINDOW = PROJECTION_WINDOW_SECONDS


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def setup(tmp_path: Path) -> tuple[EconomyService, EconomyRepository, Clock]:
    repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repo.initialize()
    clock = Clock(DOMINGO)
    return EconomyService(repo, clock=clock), repo, clock


async def old_income(repo: EconomyRepository, user: int, gross: int, now: float) -> int:
    """Cobra un premio como antes: con la renta de los últimos 7 días."""
    tax, _ = await repo.credit_income(
        GUILD,
        user,
        gross=gross,
        concept="logro",
        now=now,
        withhold=lambda g, r: compute_withholding(g, r).tax,
        window_seconds=OLD_WINDOW,
    )
    return tax


def test_la_ventana_empieza_el_lunes_a_medianoche() -> None:
    assert fiscal_window(LUNES) == 10 * 3600
    assert fiscal_window(DOMINGO) == 6 * 86_400 + 20 * 3600


async def test_lo_del_domingo_no_cuenta_el_lunes(tmp_path: Path) -> None:
    economy, _, clock = await setup(tmp_path)
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    clock.now = LUNES

    ana = await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    bea = await economy.pay_income(GUILD, BEA, gross=PREMIO, concept="logro")

    assert ana.tax == bea.tax


async def test_dentro_de_la_semana_la_retencion_sube(tmp_path: Path) -> None:
    economy, _, clock = await setup(tmp_path)
    clock.now = LUNES
    first = await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    clock.now += 86_400

    second = await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")

    assert second.tax > first.tax


async def test_regulariza_los_premios_cobrados_de_mas(tmp_path: Path) -> None:
    economy, repo, clock = await setup(tmp_path)
    await old_income(repo, ANA, PREMIO, DOMINGO)
    overcharged = await old_income(repo, ANA, PREMIO, LUNES)
    clock.now = LUNES + 3600
    fair = compute_withholding(PREMIO, 0).tax
    balance = await economy.balance(GUILD, ANA)
    state = await economy.balance(GUILD, STATE_ACCOUNT_ID)

    paid = await economy.regularize_irpf(GUILD)

    assert overcharged > fair
    assert paid == [(ANA, overcharged - fair)]
    assert await economy.balance(GUILD, ANA) == balance + overcharged - fair
    assert await economy.balance(GUILD, STATE_ACCOUNT_ID) == state - (overcharged - fair)
    # Una sola vez.
    assert await economy.regularize_irpf(GUILD) is None


async def test_regulariza_la_nomina(tmp_path: Path) -> None:
    economy, repo, clock = await setup(tmp_path)
    await old_income(repo, ANA, PREMIO, DOMINGO)

    def payslip_for(amount: int, recent: int):
        return compute_payslip(amount, fiscal_to_wage(recent))

    slip, _ = await repo.credit_salary(
        GUILD,
        ANA,
        gross=1_700,
        concept="pala:obra",
        now=LUNES,
        payslip_for=payslip_for,
        window_seconds=OLD_WINDOW,
    )
    clock.now = LUNES + 3600

    paid = await economy.regularize_irpf(GUILD)

    assert slip.irpf > 0
    assert paid == [(ANA, slip.irpf - compute_payslip(1_700, 0).irpf)]


async def test_regulariza_el_casino_de_la_semana_sin_declarar(tmp_path: Path) -> None:
    economy, repo, clock = await setup(tmp_path)
    await old_income(repo, ANA, PREMIO, DOMINGO)
    await repo.apply(GUILD, ANA, [LedgerEntry(10_000, "prueba")])
    settled = await repo.settle_gamble(
        GUILD,
        ANA,
        [LedgerEntry(-1_000, "slots:apuesta"), LedgerEntry(1_000 + PREMIO, "slots:premio")],
        day="2026-10-05",
        now=LUNES,
        adjust_tax=True,
        day_tax=gambling_day_tax,
        window_seconds=OLD_WINDOW,
    )
    clock.now = LUNES + 3600

    paid = await economy.regularize_irpf(GUILD)

    fair = gambling_day_tax(PREMIO, 0)
    assert settled.day_withheld > fair
    assert paid == [(ANA, settled.day_withheld - fair)]


async def test_no_devuelve_nada_si_no_se_cobro_de_mas(tmp_path: Path) -> None:
    economy, _, clock = await setup(tmp_path)
    clock.now = LUNES
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")

    assert await economy.regularize_irpf(GUILD) == []


def test_el_anuncio_nombra_y_resume() -> None:
    paid = [(i, 100) for i in range(20)]

    text = regularization_text(paid, {0: "Ana"})

    assert "2.000" in text
    assert "Ana: +" in text
    assert "Y 5 más." in text
