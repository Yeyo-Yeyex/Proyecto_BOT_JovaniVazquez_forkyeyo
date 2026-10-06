"""Pruebas de la cuenta remunerada: intereses diarios por tramos y liquidación del ahorro."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bot.cogs.intereses import Intereses, day_stats, notice_lines, savings_stats, tiers_label
from bot.repositories.economy import (
    STATE_ACCOUNT_ID,
    EconomyRepository,
    InterestNotice,
    LedgerEntry,
)
from bot.services.donations import BY_KEY
from bot.services.economy import (
    INTEREST_CATCH_UP_DAYS,
    STARTING_BALANCE,
    EconomyService,
    day_bounds,
    treasury_embed,
)
from bot.services.interest import (
    INTEREST_DAILY_MAX,
    INTEREST_TIERS,
    INTEREST_TOP,
    INTEREST_WITHHOLDING,
    SPIN_PRICE,
    DayFacts,
    DayOutcome,
    InterestPayment,
    LedgerRow,
    Streaks,
    analyze_day,
    interest_for,
    savings_settlement,
    settle_day,
    withholding,
)
from bot.services.levels import TIMEZONE
from bot.services.taxes import savings_marginal_rate, savings_tax

GUILD = 1
SAVER = 20
LAZY = 30


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def at(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now


def at(d: int, hour: int = 18, minute: int = 0) -> datetime:
    """Septiembre de 2026 (ya pasado, como el libro en producción): lunes 7, 14, 21 y 28."""
    return datetime(2026, 9, d, hour, minute, tzinfo=TIMEZONE)


async def make_service(tmp_path: Path, clock: Clock) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=clock)


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


def last_id(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (value,) = connection.execute("SELECT COALESCE(MAX(id), 0) FROM economy_ledger").fetchone()
    return int(value)


async def dated(tmp_path: Path, when: datetime, action: Awaitable[object]) -> None:
    """Ejecuta `action` y pone la fecha `when` a los movimientos que deja en el libro.

    El libro usa la hora real de SQLite; las pruebas la cambian para colocar
    cada movimiento en el día que toca.
    """
    before = last_id(tmp_path)
    await action
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE economy_ledger SET created_at = ? WHERE id > ?", (when.timestamp(), before)
        )


async def move(
    service: EconomyService,
    tmp_path: Path,
    user_id: int,
    amount: int,
    when: datetime,
    reason: str = "prueba",
) -> None:
    await dated(
        tmp_path, when, service.repository.apply(GUILD, user_id, [LedgerEntry(amount, reason)])
    )


async def open_with(service: EconomyService, tmp_path: Path, user_id: int, balance: int) -> None:
    """Abre un monedero el día 1 con `balance` exactos."""
    await dated(tmp_path, at(1, hour=9), service.balance(GUILD, user_id))
    await move(service, tmp_path, user_id, balance - STARTING_BALANCE, at(1, hour=9))


# -- Reglas ----------------------------------------------------------------------------


def test_los_tramos_pagan_cada_parte_del_saldo_a_su_tipo() -> None:
    assert INTEREST_TIERS == ((4_000, 0.025), (20_000, 0.0125), (70_000, 0.004))
    assert interest_for(2_000) == 50
    assert interest_for(4_000) == 100
    assert interest_for(20_000) == 100 + 200
    assert interest_for(INTEREST_TOP) == INTEREST_DAILY_MAX == 500
    assert interest_for(10 * INTEREST_TOP) == INTEREST_DAILY_MAX
    assert interest_for(0) == interest_for(-5) == 0


def test_cada_pago_retiene_el_19() -> None:
    assert INTEREST_WITHHOLDING == 0.19
    assert withholding(500) == 95
    assert withholding(0) == 0


def test_el_saldo_medio_pesa_el_tiempo() -> None:
    start, end = day_bounds(date(2026, 9, 15))
    noon = start + (end - start) / 2
    facts = analyze_day(0, [LedgerRow(noon, 8_000, 8_000, "prueba")], start, end)
    assert facts.average == 4_000
    assert facts.minimum == 0 and facts.close == 8_000
    # Meterlo un minuto antes de medianoche casi no cuenta.
    late = analyze_day(0, [LedgerRow(end - 60, 70_000, 70_000, "prueba")], start, end)
    assert interest_for(late.average) <= 2


def test_el_dia_del_cambio_de_hora_dura_25_horas() -> None:
    start, end = day_bounds(date(2026, 10, 25))
    assert end - start == 25 * 3600
    assert start == datetime(2026, 10, 25, tzinfo=TIMEZONE).timestamp()


def test_el_analisis_distingue_lo_automatico_de_lo_que_hace_uno() -> None:
    start, end = day_bounds(date(2026, 9, 15))
    rows = [
        LedgerRow(start + 600, 405, 5_405, "intereses"),
        LedgerRow(start + 600, -95, 5_310, "irpf:intereses"),
        LedgerRow(start + 3_600, 300, 5_610, "imv"),
        LedgerRow(start + 7_200, -400, 5_210, "ruleta:apuesta"),
        LedgerRow(start + 7_200, 0, 5_210, "ruleta:premio"),
        LedgerRow(start + 9_000, 1_600, 6_810, "pala:obra"),
        LedgerRow(start + 9_500, -2_000, 4_810, "bizum:enviado"),
    ]
    facts = analyze_day(5_000, rows, start, end)
    assert facts.active and facts.spent and facts.payroll and facts.bizum_out
    assert facts.casino_net == -400
    assert facts.interest_in == 310
    assert facts.imv == 300
    quiet = analyze_day(5_000, rows[:2], start, end)
    assert not quiet.active and not quiet.spent


def test_las_rachas_de_no_hacer_nada_solo_siguen_si_se_cobra() -> None:
    rich = DayFacts(average=INTEREST_TOP, minimum=INTEREST_TOP, close=INTEREST_TOP)
    outcome = settle_day(SAVER, rich, Streaks(capped=2, floor=4, resist=1, still=6, ant=3))
    assert outcome.payment == InterestPayment(SAVER, INTEREST_TOP, 500, 95)
    assert outcome.streaks == Streaks(capped=3, floor=5, resist=2, still=7, ant=4)
    broke = settle_day(SAVER, DayFacts(average=0, minimum=0, close=0), outcome.streaks)
    assert broke.payment.gross == 0
    assert broke.streaks == Streaks()


def test_la_semana_se_liquida_con_la_escala_del_ahorro() -> None:
    week_max = INTEREST_DAILY_MAX * 7
    # 3.500 Y$ a la semana son 18.200 € al año: 6.000 al 19 % y el resto al 21 %.
    assert savings_tax(week_max) == round((6_000 * 0.19 + 12_200 * 0.21) / 52 * 10)
    assert savings_marginal_rate(week_max) == 0.21
    assert savings_marginal_rate(700) == 0.19
    withheld = withholding(INTEREST_DAILY_MAX) * 7
    settlement = savings_settlement(SAVER, week_max, withheld, balance=100_000)
    assert settlement.charged == savings_tax(week_max) - withheld > 0
    # Nunca más de lo que hay en el monedero, y nunca a devolver.
    assert savings_settlement(SAVER, week_max, withheld, balance=10).charged == 10
    assert savings_settlement(SAVER, 700, 999, balance=100).charged == 0


# -- Base de datos -----------------------------------------------------------------------


async def test_paga_el_dia_sobre_el_saldo_medio_y_el_estado_cobra(tmp_path: Path) -> None:
    clock = Clock(at(16, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, SAVER, 2_000)
    # El martes 15 a mediodía mete 8.000: 2.000 medio día y 10.000 el otro medio.
    await move(service, tmp_path, SAVER, 8_000, at(15, hour=12))

    result = await service.pay_interest(GUILD)
    again = await service.pay_interest(GUILD)

    gross = interest_for(6_000)
    tax = withholding(gross)
    [(day, run)] = result.days
    assert day == date(2026, 9, 15)
    assert [o.payment for o in run.outcomes] == [InterestPayment(SAVER, 6_000, gross, tax)]
    assert again.days == ()
    assert await service.balance(GUILD, SAVER) == 10_000 + gross - tax
    assert ledger_sum(tmp_path, SAVER) == 10_000 + gross - tax
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == tax
    treasury = await service.treasury(GUILD, since=0)
    assert treasury.collected_total == tax


async def test_cobran_tambien_los_que_no_hacen_nada(tmp_path: Path) -> None:
    clock = Clock(at(16, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, LAZY, 4_000)

    result = await service.pay_interest(GUILD)

    [(_day, run)] = result.days
    [outcome] = run.outcomes
    assert outcome.payment.gross == 100
    assert not outcome.facts.active


async def test_ni_el_estado_ni_las_ongs_cobran_intereses(tmp_path: Path) -> None:
    clock = Clock(at(16, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, SAVER, 99_000)
    await dated(
        tmp_path,
        at(1, hour=10),
        service.donate(
            GUILD, SAVER, ong_key="mares", ong_account=BY_KEY["mares"].account_id, amount=50_000
        ),
    )
    await move(service, tmp_path, STATE_ACCOUNT_ID, 1, at(1, hour=11))

    result = await service.pay_interest(GUILD)

    assert [o.payment.user_id for _d, run in result.days for o in run.outcomes] == [SAVER]


async def test_el_dinero_metido_en_una_partida_no_cuenta(tmp_path: Path) -> None:
    clock = Clock(at(16, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, SAVER, 8_000)
    # Apuesta 4.000 al blackjack a las 00:00 y la partida sigue abierta todo el día.
    await dated(
        tmp_path, at(15, hour=0), service.place_bet(GUILD, SAVER, game="blackjack", stake=4_000)
    )

    result = await service.pay_interest(GUILD)

    assert result.days[0][1].outcomes[0].payment.average == 4_000


async def test_si_el_bot_estuvo_caido_paga_los_dias_que_faltan(tmp_path: Path) -> None:
    clock = Clock(at(16, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, SAVER, 4_000)
    await service.pay_interest(GUILD)
    clock.at(at(26, hour=9))  # diez días después

    result = await service.pay_interest(GUILD)

    days = [day for day, _run in result.days]
    assert len(days) == INTEREST_CATCH_UP_DAYS
    assert days[-1] == date(2026, 9, 25)


async def test_el_lunes_se_liquida_la_semana_y_sanxe_cobra_la_diferencia(tmp_path: Path) -> None:
    clock = Clock(at(15, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, SAVER, 200_000)
    await service.pay_interest(GUILD)  # el lunes 14
    for d in range(16, 21):
        clock.at(at(d, hour=0, minute=10))
        result = await service.pay_interest(GUILD)
        assert result.savings is None
    clock.at(at(21, hour=0, minute=10))
    state_before = ledger_sum(tmp_path, STATE_ACCOUNT_ID)

    # El lunes 21 paga el domingo y, en la misma pasada, liquida la semana.
    result = await service.pay_interest(GUILD)

    assert [day for day, _run in result.days] == [date(2026, 9, 20)]
    sunday_tax = withholding(INTEREST_DAILY_MAX)
    assert result.savings is not None
    week, run = result.savings
    assert week == date(2026, 9, 14)
    [settlement] = run.settlements
    week_gross = INTEREST_DAILY_MAX * 7
    assert settlement.gross == week_gross
    assert settlement.rate == 0.21
    assert settlement.charged == savings_tax(week_gross) - withholding(INTEREST_DAILY_MAX) * 7
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == state_before + sunday_tax + settlement.charged
    assert ledger_sum(tmp_path, SAVER) == await service.balance(GUILD, SAVER)
    assert (await service.pay_interest(GUILD)).savings is None
    last = await service.last_savings(GUILD, SAVER)
    assert last is not None and last[4] == 21 and last[5] == settlement.charged


async def test_el_aviso_sale_una_vez(tmp_path: Path) -> None:
    clock = Clock(at(16, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, SAVER, 4_000)
    await service.pay_interest(GUILD)

    notice = await service.take_interest_notice(GUILD, SAVER)
    again = await service.take_interest_notice(GUILD, SAVER)

    assert notice == InterestNotice(days=1, gross=100, tax=19, first_day="2026-09-15")
    assert again == InterestNotice()


async def test_saldo_proyecta_hoy_y_recuerda_lo_de_ayer(tmp_path: Path) -> None:
    clock = Clock(at(16, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, SAVER, 4_000)
    await dated(tmp_path, at(16, hour=0, minute=10), service.pay_interest(GUILD))
    # A mediodía tiene 4.081 (4.000 + 81 netos de ayer) y mete 20.000 más.
    clock.at(at(16, hour=12))
    await move(service, tmp_path, SAVER, 20_000, at(16, hour=12))

    preview = await service.interest_preview(GUILD, SAVER)

    assert preview.yesterday == (100, 19)
    assert abs(preview.average - (4_081 + 24_081) // 2) <= 2
    assert preview.gross == interest_for(preview.average)
    assert preview.to_top == INTEREST_TOP - preview.average


async def test_hacienda_cabe_en_un_campo_de_discord() -> None:
    from bot.repositories.economy import Treasury

    embed = treasury_embed(Treasury(0, 0, 0, ()), year=2026, names={})
    assert all(len(field.value or "") <= 1024 for field in embed.fields)
    assert "Intereses" in (embed.fields[-1].value or "")


# -- Avisos y logros ---------------------------------------------------------------------


def outcome(facts: DayFacts, streaks: Streaks | None = None) -> DayOutcome:
    gross = interest_for(facts.average)
    return DayOutcome(
        InterestPayment(SAVER, facts.average, gross, withholding(gross)),
        facts,
        streaks or Streaks(),
    )


def test_los_logros_del_dia_salen_de_lo_que_ha_pasado() -> None:
    capped = day_stats(outcome(DayFacts(INTEREST_TOP, INTEREST_TOP, INTEREST_TOP), Streaks(3)))
    assert capped.add == {
        "interest_earned": 405,
        "interest_days": 1,
        "interest_tax": 95,
        "interest_capped": 1,
    }
    assert capped.peak["interest_capped_streak"] == 3
    assert day_stats(outcome(DayFacts(0, 0, 0, active=True))).add == {"interest_zero": 1}
    assert day_stats(outcome(DayFacts(120, 0, 0))).add["interest_rounding"] == 1
    cigarra = day_stats(outcome(DayFacts(500, 0, SPIN_PRICE - 1, active=True, payroll=True)))
    assert cigarra.add["interest_grasshopper"] == 1
    fundido = day_stats(outcome(DayFacts(500, 0, 0, interest_in=81, casino_net=-100)))
    assert fundido.add["interest_gambled"] == 1
    rentista = day_stats(outcome(DayFacts(500, 0, 0, interest_in=405, imv=300)))
    assert rentista.add["interest_beats_imv"] == 1
    trampa = day_stats(outcome(DayFacts(500, 0, INTEREST_TIERS[0][0] - 1, bizum_out=True)))
    assert trampa.add["interest_bizum_trick"] == 1


def test_la_liquidacion_apunta_el_tramo_y_lo_cobrado() -> None:
    settlement = savings_settlement(SAVER, INTEREST_DAILY_MAX * 7, 665, balance=10**6)
    stats = savings_stats(settlement)
    assert stats.peak == {"savings_rate_max": 21}
    assert stats.add == {"interest_tax": settlement.charged}


def test_el_aviso_dice_ayer_o_mientras_no_mirabas() -> None:
    today = date(2026, 9, 16)
    [ayer] = notice_lines(InterestNotice(1, 100, 19, "2026-09-15"), today)
    assert "Ayer el banco te pagó 81" in ayer and "Perro Sanxe se llevó 19" in ayer
    [fuera] = notice_lines(InterestNotice(9, 900, 171, "2026-09-07"), today)
    assert "Mientras no mirabas" in fuera and "9 días" in fuera
    [liquidacion] = notice_lines(InterestNotice(savings=(("2026-09-07", 41, 21),)), today)
    assert "tramo del 21 %" in liquidacion
    assert notice_lines(InterestNotice(), today) == []
    assert tiers_label().startswith("2,5 % hasta 4.000")


async def test_volver_tras_una_semana_tiene_logro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(at(16, hour=0, minute=10))
    service = await make_service(tmp_path, clock)
    await open_with(service, tmp_path, LAZY, 4_000)
    monkeypatch.setattr("bot.cogs.intereses.time.time", clock)
    note = MagicMock()
    monkeypatch.setattr("bot.cogs.intereses.logros.note", note)
    guild = SimpleNamespace(id=GUILD, text_channels=[], system_channel=None)
    bot = MagicMock()
    bot.get_guild.return_value = guild
    cog = Intereses(bot, service)
    for d in range(16, 25):
        clock.at(at(d, hour=0, minute=10))
        await cog.run_guild(guild)  # type: ignore[arg-type]

    line = await cog.hint_for(GUILD, LAZY)
    again = await cog.hint_for(GUILD, LAZY)

    assert line is not None and "Mientras no mirabas" in line
    assert again is None
    stats = [call.args[3] for call in note.call_args_list]
    assert any(s.add.get("interest_comeback") for s in stats)
    assert max(s.peak.get("interest_still_streak", 0) for s in stats) >= 7
