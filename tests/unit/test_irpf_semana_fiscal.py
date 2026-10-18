"""El año fiscal es la semana: de lunes a lunes y con el tipo estable.

La renta de la semana se reinicia cada lunes, pero el tipo con el que se retiene no
empieza de cero: es el de la semana anterior (la empresa retiene con el sueldo previsto,
arts. 82 a 87 RIRPF) y sube si esta semana se gana más deprisa.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from bot.repositories.economy import EconomyRepository
from bot.services.economy import STARTING_BALANCE, EconomyService, fiscal_week
from bot.services.levels import TIMEZONE

GUILD = 1
ANA = 10
BEA = 20
DOMINGO = datetime(2026, 10, 4, 20, tzinfo=TIMEZONE).timestamp()
LUNES = datetime(2026, 10, 5, 10, tzinfo=TIMEZONE).timestamp()
PREMIO = 300_000


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def setup(tmp_path: Path) -> tuple[EconomyService, Clock]:
    repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repo.initialize()
    clock = Clock(DOMINGO)
    return EconomyService(repo, clock=clock), clock


def test_la_semana_va_de_lunes_a_lunes_en_hora_canaria() -> None:
    week = fiscal_week(date(2026, 10, 7))
    assert week.monday == "2026-10-05"
    assert week.start == datetime(2026, 10, 5, tzinfo=TIMEZONE).timestamp()
    assert week.end == datetime(2026, 10, 12, tzinfo=TIMEZONE).timestamp()
    assert week.next_monday == "2026-10-12" and week.previous_monday == "2026-09-28"


def test_la_semana_del_cambio_de_hora_dura_una_hora_mas() -> None:
    week = fiscal_week(date(2026, 10, 25))  # el domingo 25 se atrasa la hora
    assert week.end - week.start == 7 * 86_400 + 3_600


async def test_el_lunes_se_retiene_con_el_tipo_de_la_semana_pasada(tmp_path: Path) -> None:
    economy, clock = await setup(tmp_path)
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    clock.now = LUNES

    ana = await economy.pay_income(GUILD, ANA, gross=10_000, concept="logro")
    bea = await economy.pay_income(GUILD, BEA, gross=10_000, concept="logro")

    assert bea.tax == 0  # 10.000 Y$ sueltos no llegan al mínimo
    assert ana.tax > 0  # pero Ana venía de una semana gorda


async def test_dentro_de_la_semana_la_retencion_sube(tmp_path: Path) -> None:
    economy, clock = await setup(tmp_path)
    clock.now = LUNES
    first = await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    clock.now += 86_400

    second = await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")

    assert second.rate > first.rate


async def test_lo_de_hace_dos_semanas_ya_no_cuenta(tmp_path: Path) -> None:
    economy, clock = await setup(tmp_path)
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    clock.now = LUNES + 7 * 86_400

    late = await economy.pay_income(GUILD, ANA, gross=10_000, concept="logro")

    assert late.tax == 0


async def test_cerrada_la_semana_el_suelo_es_el_tipo_de_su_declaracion(tmp_path: Path) -> None:
    economy, clock = await setup(tmp_path)
    clock.now = LUNES
    await economy.close_irpf_weeks(GUILD)  # el servidor entra en la 8.0.0 esta semana
    await economy.pay_income(GUILD, ANA, gross=PREMIO, concept="logro")
    clock.now = LUNES + 7 * 86_400
    ((_week, run),) = await economy.close_irpf_weeks(GUILD)
    (filed,) = run.returns

    after = await economy.pay_income(GUILD, ANA, gross=10_000, concept="logro")

    assert after.rate == filed.rate > 0
