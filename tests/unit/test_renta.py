"""Pruebas de la declaración de los lunes (`close_irpf_weeks`) y del cog de la Renta."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.renta import (
    DETAIL,
    SEALED,
    SUMMARY,
    Renta,
    RentaView,
    announcement_text,
    draft_text,
    notice_text,
)
from bot.repositories.economy import (
    CARRY_FINANCE,
    STATE_ACCOUNT_ID,
    EconomyRepository,
    IrpfReturn,
    IrpfRun,
    LedgerEntry,
)
from bot.services.economy import STARTING_BALANCE, Declaration, EconomyService, fiscal_week
from bot.services.levels import TIMEZONE
from bot.services.renta_report import detail_text, summary_text
from bot.services.taxes import (
    CLAIM_WEEKS,
    INCOME_PROPERTY,
    WeekIncome,
    gambling_day_tax,
    week_tax,
)

GUILD = 1
USER = 10


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def at(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now


def day(d: int, month: int = 10, hour: int = 18) -> datetime:
    """Octubre de 2026: los lunes son el 5, el 12, el 19 y el 26."""
    return datetime(2026, month, d, hour, tzinfo=TIMEZONE)


async def make_service(tmp_path: Path, clock: Clock) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    service = EconomyService(repository, clock=clock)
    # El servidor entra en la 8.0.0 en la semana en curso (lo hace el cog al arrancar).
    await service.close_irpf_weeks(GUILD)
    return service


async def put(service: EconomyService, user_id: int, amount: int, reason: str = "prueba") -> None:
    """Mueve dinero sin impuestos (para dar o quitar saldo en una prueba)."""
    await service.repository.apply(GUILD, user_id, [LedgerEntry(amount, reason)])


def add_debt(service: EconomyService, amount: int) -> None:
    """Le apunta al miembro una deuda con Hacienda."""
    with service.repository._transaction() as connection:  # noqa: SLF001
        connection.execute(
            "INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, ?)",
            (GUILD, USER, amount),
        )


async def close(service: EconomyService, clock: Clock, monday: int) -> IrpfReturn:
    """Cierra la semana anterior al lunes `monday` y devuelve la declaración de `USER`."""
    clock.at(day(monday, hour=0))
    closed = await service.close_irpf_weeks(GUILD)
    assert closed, "no se ha cerrado ninguna semana"
    (filed,) = [r for _week, run in closed for r in run.returns if r.user_id == USER]
    return filed


async def test_el_casino_retiene_el_19_y_el_lunes_devuelve_si_tu_tipo_es_menor(
    tmp_path: Path,
) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    win = await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    assert win.tax_delta == gambling_day_tax(35_000)

    filed = await close(service, clock, 12)

    assert filed.withheld == win.tax_delta
    assert filed.quota == week_tax(WeekIncome(gambling=35_000)).quota
    assert filed.result < 0  # 35.000 Y$ en una semana pagan menos del 19 %
    assert filed.refund == -filed.result
    assert await service.pending_declarations(GUILD, USER) == [
        Declaration(date(2026, 10, 5), filed.refund)
    ]


async def test_las_perdidas_de_otro_dia_compensan_y_se_devuelve_todo(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    win = await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    clock.at(day(7))
    await put(service, USER, 50_000)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=40_000, payout=0)

    filed = await close(service, clock, 12)

    assert filed.quota == 0  # la semana acaba en pérdidas: el juego cuenta 0
    assert filed.refund == win.tax_delta


async def test_si_te_retuvieron_de_menos_el_lunes_se_cobra_solo(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    # Un premio pequeño retiene poco; uno grande después sube el tipo de la semana.
    await service.pay_income(GUILD, USER, gross=20_000, concept="nivel:5")
    await service.pay_income(GUILD, USER, gross=900_000, concept="nivel:50")
    before = await service.balance(GUILD, USER)
    state_before = (await service.treasury(GUILD, since=0)).balance

    filed = await close(service, clock, 12)

    assert filed.result > 0 and filed.charged == filed.result and filed.debt == 0
    assert await service.balance(GUILD, USER) == before - filed.result
    assert (await service.treasury(GUILD, since=0)).balance == state_before + filed.result


async def test_sin_saldo_queda_como_deuda_y_se_cobra_el_lunes_siguiente(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.pay_income(GUILD, USER, gross=20_000, concept="nivel:5")
    await service.pay_income(GUILD, USER, gross=900_000, concept="nivel:50")
    await put(service, USER, -(await service.balance(GUILD, USER)), "prueba:gasto")

    filed = await close(service, clock, 12)
    assert filed.charged == 0 and filed.debt == filed.result > 0
    assert await service.tax_debt(GUILD, USER) == filed.debt

    clock.at(day(15))
    await put(service, USER, filed.debt * 2)
    clock.at(day(19, hour=0))
    await service.close_irpf_weeks(GUILD)

    assert await service.tax_debt(GUILD, USER) == 0
    assert await service.balance(GUILD, USER) == filed.debt


async def test_una_devolucion_paga_primero_lo_que_se_debe(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await put(service, STATE_ACCOUNT_ID, 10_000_000)
    add_debt(service, 1_000)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    await put(service, USER, -(await service.balance(GUILD, USER)))

    filed = await close(service, clock, 12)

    assert filed.result < 0
    assert filed.compensated == min(-filed.result, 1_000)
    assert filed.refund == -filed.result - filed.compensated
    assert await service.tax_debt(GUILD, USER) == 1_000 - filed.compensated
    # Lo compensado ya estaba en el Estado (retenido): Hacienda no lo cuenta otra vez.
    taxes = (await service.tax_breakdown(GUILD)).get(USER, {})
    assert taxes.get("deudas", 0) == 0


async def test_presentar_cobra_del_estado_una_sola_vez(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    filed = await close(service, clock, 12)
    before = await service.balance(GUILD, USER)
    state_before = (await service.treasury(GUILD, since=0)).balance

    claim = await service.claim_declarations(GUILD, USER)
    again = await service.claim_declarations(GUILD, USER)

    assert claim.refunded == filed.refund > 0
    assert claim.balance == before + claim.refunded
    assert (await service.treasury(GUILD, since=0)).balance == state_before - claim.refunded
    assert again.refunded == 0 and again.declarations == ()


async def test_una_devolucion_caduca_a_las_cuatro_semanas(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    await close(service, clock, 12)
    week = date(2026, 10, 5)

    clock.at(day(5, month=11))  # el lunes 2 de noviembre ya han pasado 4 semanas
    assert [d.week_start for d in await service.pending_declarations(GUILD, USER)] == [week]
    clock.at(day(9, month=11))
    assert CLAIM_WEEKS == 4
    assert await service.pending_declarations(GUILD, USER) == []


async def test_desplegar_no_deja_caducar_las_devoluciones_de_la_regla_vieja(
    tmp_path: Path,
) -> None:
    clock = Clock(day(10, month=8))
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    service = EconomyService(repository, clock=clock)
    # Agosto, con la regla vieja: gana un día y pierde casi todo otro; queda algo a devolver.
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    clock.at(day(11, month=8))
    await put(service, USER, 50_000)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=30_000, payout=0)
    # El servidor pasa a la 8.0.0 el 12 de octubre, mucho más de 4 semanas después.
    clock.at(day(14))
    await service.close_irpf_weeks(GUILD)

    pending = await service.pending_declarations(GUILD, USER)
    assert [d.week_start for d in pending] == [date(2026, 8, 10)]
    # Tiene las 4 semanas del plazo contadas desde el cambio.
    clock.at(day(4, month=11))
    assert await service.pending_declarations(GUILD, USER)
    clock.at(day(10, month=11))
    assert await service.pending_declarations(GUILD, USER) == []


async def test_si_el_bot_estuvo_apagado_el_plazo_cuenta_desde_el_cierre(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    # El bot vuelve el 9 de noviembre: la semana del 5 de octubre se cierra entonces.
    clock.at(day(9, month=11, hour=0))
    await service.close_irpf_weeks(GUILD)

    assert [d.week_start for d in await service.pending_declarations(GUILD, USER)] == [
        date(2026, 10, 5)
    ]
    clock.at(day(30, month=11))
    assert await service.pending_declarations(GUILD, USER)
    clock.at(day(7, month=12))
    assert await service.pending_declarations(GUILD, USER) == []


async def test_lo_que_el_estado_no_puede_devolver_sigue_pendiente(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    filed = await close(service, clock, 12)
    # El Estado se queda con 10 Y$ (lo ha gastado todo en premios de lotería).
    state = await service.balance(GUILD, STATE_ACCOUNT_ID)
    await put(service, STATE_ACCOUNT_ID, 10 - state)

    claim = await service.claim_declarations(GUILD, USER)

    assert claim.refunded == 10
    assert await service.pending_declarations(GUILD, USER) == [
        Declaration(date(2026, 10, 5), filed.refund - 10)
    ]
    await put(service, STATE_ACCOUNT_ID, 1_000_000)
    rest = await service.claim_declarations(GUILD, USER)
    assert rest.refunded == filed.refund - 10
    assert await service.pending_declarations(GUILD, USER) == []


async def test_cerrar_es_idempotente_y_recupera_semanas_perdidas(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=2_000)
    clock.at(day(13))
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=2_000)

    clock.at(day(20))  # el bot no cerró el 12: dos semanas de golpe, en orden
    closed = await service.close_irpf_weeks(GUILD)
    assert [week for week, _ in closed] == [date(2026, 10, 5), date(2026, 10, 12)]
    assert await service.close_irpf_weeks(GUILD) == []


async def test_las_semanas_de_antes_de_la_8_siguen_la_regla_vieja(tmp_path: Path) -> None:
    clock = Clock(day(5))
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    service = EconomyService(repository, clock=clock)
    win = await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    clock.at(day(7))
    await put(service, USER, 50_000)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=40_000, payout=0)
    # El servidor pasa a la 8.0.0 la semana siguiente: la del 5 es vieja.
    clock.at(day(12))
    assert await service.close_irpf_weeks(GUILD) == []

    pending = await service.pending_declarations(GUILD, USER)

    assert pending == [Declaration(date(2026, 10, 5), win.tax_delta)]


async def test_lo_que_no_cabe_de_intereses_pasa_a_la_semana_siguiente(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    repo = service.repository
    with repo._transaction() as connection:  # noqa: SLF001
        repo._record_in(  # noqa: SLF001
            connection, GUILD, USER, now=clock(), concept="pisos:renta", kind=INCOME_PROPERTY,
            gross=10_000, withheld=0, finance=16_000, costs=1_000, ref="piso", reduction=0.5,
        )  # fmt: skip
    await close(service, clock, 12)
    with repo._transaction() as connection:  # noqa: SLF001
        carried = connection.execute(
            "SELECT amount FROM economy_irpf_carry WHERE kind = ? AND ref = 'piso'",
            (CARRY_FINANCE,),
        ).fetchone()
    assert carried[0] == 6_000

    clock.at(day(14))
    week = fiscal_week(date(2026, 10, 14))
    with repo._transaction() as connection:  # noqa: SLF001
        repo._record_in(  # noqa: SLF001
            connection, GUILD, USER, now=clock(), concept="pisos:renta", kind=INCOME_PROPERTY,
            gross=10_000, withheld=0, finance=0, costs=0, ref="piso", reduction=0.5,
        )  # fmt: skip
        income, _ = repo._week_income_in(connection, GUILD, USER, week)  # noqa: SLF001
    (item,) = income.properties
    assert item.carry == 6_000
    assert item.settle().net == round((10_000 - 6_000) * 0.5)


async def test_el_aviso_no_dice_la_cifra_y_sale_una_vez(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    filed = await close(service, clock, 12)
    cog = Renta(MagicMock(), service)

    first = await cog.hint_for(GUILD, USER)
    second = await cog.hint_for(GUILD, USER)

    assert first is not None and "/renta" in first
    assert str(filed.refund) not in first.replace(".", "")
    # Hasta que se abre, no se adelanta que hay algo que presentar.
    assert second is None


async def test_el_sobre_se_abre_y_pasa_del_resumen_al_detalle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = MagicMock()
    monkeypatch.setattr("bot.cogs.renta.logros.note", note)
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.pay_income(GUILD, USER, gross=50_000, concept="nivel:10")
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    await close(service, clock, 12)
    cog = Renta(MagicMock(), service)
    guild = SimpleNamespace(id=GUILD)
    member = SimpleNamespace(id=USER)

    view = await cog._view(guild, member)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.mode == SEALED
    assert "Solo hay una forma de saberlo" in (view.embed().description or "")
    assert [item.custom_id for item in view.children] == ["renta:abrir"]  # type: ignore[attr-defined]

    interaction = SimpleNamespace(
        guild=guild,
        user=member,
        channel_id=5,
        response=SimpleNamespace(
            is_done=lambda: False, edit_message=AsyncMock(), defer=AsyncMock()
        ),
        edit_original_response=AsyncMock(),
    )
    await view._open(interaction)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.mode == SUMMARY
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None and statement.opened
    opened = note.call_args.args[3].add
    assert opened["renta_opened"] == 1
    assert opened["renta_opened_refund"] == int(statement.filed.result < 0)

    await view._detail(interaction)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.mode == DETAIL
    assert note.call_args.args[3].add == {"renta_detail": 1}
    text = view.embed().description or ""
    assert "paso a paso" in text and "Casino" in text and "Premios" in text
    await view._summary(interaction)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.mode == SUMMARY

    # Una vez abierta, `renta` va directa al resumen.
    again = await cog._view(guild, member)  # type: ignore[arg-type]  # noqa: SLF001
    assert again.mode == SUMMARY


async def test_el_detalle_rehace_la_misma_declaracion(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.pay_income(GUILD, USER, gross=50_000, concept="nivel:10")
    await service.pay_salary(GUILD, USER, gross=8_000, concept="pala:obra")
    filed = await close(service, clock, 12)

    statement = await service.irpf_statement(GUILD, USER)

    assert statement is not None and statement.detail is not None
    assert statement.detail.result == filed.result
    assert statement.detail.tax.quota == filed.quota
    assert "Trabajo" in detail_text(statement)
    assert "Declaración de la semana" in summary_text(statement)


def test_el_anuncio_del_lunes_no_dice_nombres_ni_cifras_de_nadie() -> None:
    run = IrpfRun(
        returns=(
            IrpfReturn(user_id=111, base=1, quota=1, deductions=0, withheld=0, result=500),
            IrpfReturn(user_id=222, base=1, quota=1, deductions=0, withheld=0, result=-300),
        )
    )
    text = announcement_text(date(2026, 10, 5), run)
    assert "111" not in text and "222" not in text
    assert "1 a pagar y 1 a devolver" in text


def test_el_aviso_de_la_declaracion_dice_la_semana_y_no_el_resultado() -> None:
    text = notice_text(date(2026, 10, 5))
    assert "05/10–11/10" in text and "/renta" in text


def test_borrador_con_varias_semanas() -> None:
    text = draft_text([Declaration(date(2026, 10, 5), 1_000), Declaration(date(2026, 10, 12), 500)])
    assert "1.500 Y$ a devolver" in text
    assert "05/10–11/10" in text


async def test_aviso_efimero_una_vez_por_campana_y_presentar_es_publico(
    tmp_path: Path,
) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    await close(service, clock, 12)
    await service.open_irpf_return(GUILD, USER, date(2026, 10, 5))
    cog = Renta(MagicMock(), service)
    channel = MagicMock(spec=["send"])
    channel.send = AsyncMock()
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=USER, display_name="Diego"),
        channel=channel,
        response=SimpleNamespace(is_done=lambda: True, edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    await cog.remind(interaction)  # type: ignore[arg-type]
    await cog.remind(interaction)  # type: ignore[arg-type]

    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    assert await cog.hint_for(GUILD, USER) is not None

    await cog.present(interaction)  # type: ignore[arg-type]

    assert "presenta la renta" in channel.send.await_args.args[0]
    assert await cog.hint_for(GUILD, USER) is None


def test_la_vista_sin_declaracion_explica_lo_que_pasara() -> None:
    view = RentaView(MagicMock(), USER, None, [], mode=SUMMARY)
    assert "Aún no tienes ninguna declaración" in (view.embed().description or "")
    assert view.children == []
