"""Pruebas de la declaración de los lunes (`close_irpf_weeks`) y del cog de la Renta."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction
from render_fakes import FakeRentaScene

from bot.cogs.renta import (
    ADVICE,
    CONCEPTS,
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
    PAYMENT_PAID,
    PAYMENT_PENDING,
    PAYMENT_SPLIT,
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
    garnish,
    second_installment,
    week_tax,
)

GUILD = 1
USER = 10


@pytest.fixture(autouse=True)
def _sin_dibujo(monkeypatch: pytest.MonkeyPatch) -> None:
    """La carta no se pinta de verdad: el cog usa el doble (un PNG de relleno)."""
    monkeypatch.setattr("bot.services.renta_scene.RentaScene", FakeRentaScene)


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


async def payable(service: EconomyService, clock: Clock) -> IrpfReturn:
    """Cierra una semana que sale a pagar (la del 5 de octubre) y devuelve la declaración."""
    # Un premio pequeño retiene poco; uno grande después sube el tipo de la semana.
    await service.pay_income(GUILD, USER, gross=20_000, concept="nivel:5")
    await service.pay_income(GUILD, USER, gross=900_000, concept="nivel:50")
    filed = await close(service, clock, 12)
    assert filed.result > 0
    return filed


WEEK = date(2026, 10, 5)


async def test_si_te_retuvieron_de_menos_queda_pendiente_y_se_paga_entera(
    tmp_path: Path,
) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    filed = await payable(service, clock)
    before = await service.balance(GUILD, USER)
    state_before = (await service.treasury(GUILD, since=0)).balance
    # El lunes no se cobra nada: se elige cómo pagarla al abrirla.
    assert filed.payment == PAYMENT_PENDING and filed.charged == 0 and filed.debt == 0
    assert filed.pay_due == day(13, hour=0).timestamp()

    clock.at(day(12, hour=10))
    payment = await service.pay_irpf_return(GUILD, USER, WEEK, split=False)

    assert payment is not None and not payment.automatic
    assert (payment.charged, payment.debt, payment.second) == (filed.result, 0, 0)
    assert await service.balance(GUILD, USER) == payment.balance == before - filed.result
    assert (await service.treasury(GUILD, since=0)).balance == state_before + filed.result
    assert await service.pay_irpf_return(GUILD, USER, WEEK, split=True) is None
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None
    assert statement.filed.payment == PAYMENT_PAID and statement.filed.charged == filed.result
    assert (await service.tax_breakdown(GUILD))[USER]["irpf_renta"] == filed.result


async def test_fraccionada_paga_el_60_y_el_40_al_acabar_el_jueves(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    filed = await payable(service, clock)
    before = await service.balance(GUILD, USER)
    second = second_installment(filed.result)
    assert second == filed.result - round(filed.result * 0.6)

    clock.at(day(12, hour=10))
    cog = Renta(MagicMock(), service)
    payment = await service.pay_irpf_return(GUILD, USER, WEEK, split=True)
    assert payment is not None
    assert (payment.charged, payment.second) == (filed.result - second, second)
    assert payment.second_due == day(16, hour=0).timestamp()
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None and statement.filed.payment == PAYMENT_SPLIT
    assert "El jueves se cobran" in summary_text(statement)

    clock.at(day(15, hour=23))  # el jueves por la noche aún no toca
    assert await service.settle_irpf_payments(GUILD) == ()
    clock.at(day(16, hour=0))
    (paid,) = await service.settle_irpf_payments(GUILD)

    assert paid.automatic and (paid.charged, paid.debt) == (second, 0)
    assert await service.balance(GUILD, USER) == before - filed.result
    assert await service.settle_irpf_payments(GUILD) == ()
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None and statement.filed.payment == PAYMENT_PAID
    assert statement.filed.charged == filed.result and statement.filed.second_paid == second
    assert "dos plazos" in summary_text(statement)
    # Se avisa una vez, en la siguiente acción con dinero.
    text = await cog.hint_for(GUILD, USER)
    assert text is not None and "segundo plazo" in text
    assert "segundo plazo" not in (await cog.hint_for(GUILD, USER) or "")


async def test_si_no_eliges_se_cobra_entera_al_acabar_el_lunes(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    filed = await payable(service, clock)
    before = await service.balance(GUILD, USER)

    clock.at(day(12, hour=23))
    assert await service.settle_irpf_payments(GUILD) == ()
    clock.at(day(13, hour=0))
    (payment,) = await service.settle_irpf_payments(GUILD)

    assert payment.automatic and (payment.charged, payment.second) == (filed.result, 0)
    assert await service.balance(GUILD, USER) == before - filed.result
    assert await service.pay_irpf_return(GUILD, USER, WEEK, split=True) is None


async def test_sin_saldo_queda_como_deuda_y_se_cobra_el_lunes_siguiente(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    filed = await payable(service, clock)
    await put(service, USER, -(await service.balance(GUILD, USER)), "prueba:gasto")

    payment = await service.pay_irpf_return(GUILD, USER, WEEK, split=False)
    assert payment is not None and (payment.charged, payment.debt) == (0, filed.result)
    assert await service.tax_debt(GUILD, USER) == filed.result
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None and statement.filed.debt == filed.result
    filed = statement.filed

    clock.at(day(15))
    await put(service, USER, filed.debt * 2)
    clock.at(day(19, hour=0))
    await service.close_irpf_weeks(GUILD)

    assert await service.tax_debt(GUILD, USER) == 0
    assert await service.balance(GUILD, USER) == filed.debt


async def test_sin_saldo_el_jueves_el_40_queda_como_deuda(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    filed = await payable(service, clock)
    await service.pay_irpf_return(GUILD, USER, WEEK, split=True)
    await put(service, USER, -(await service.balance(GUILD, USER)), "prueba:gasto")

    clock.at(day(16, hour=0))
    (second,) = await service.settle_irpf_payments(GUILD)

    assert (second.charged, second.debt) == (0, second_installment(filed.result))
    assert await service.tax_debt(GUILD, USER) == second.debt
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None and statement.filed.debt == second.debt
    assert "quedaron como deuda" in summary_text(statement)


async def test_una_devolucion_paga_primero_lo_que_se_debe(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await put(service, STATE_ACCOUNT_ID, 10_000_000)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    # La deuda llega después de ganar: si no, el embargo del premio ya la habría pagado.
    add_debt(service, 1_000)
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


async def test_desplegar_antes_del_arranque_deja_la_semana_con_la_regla_vieja(
    tmp_path: Path,
) -> None:
    clock = Clock(day(14))
    repository = EconomyRepository(
        tmp_path / "bot.db", starting_balance=STARTING_BALANCE, irpf_start="2026-10-19"
    )
    await repository.initialize()
    service = EconomyService(repository, clock=clock)
    # El bot nuevo entra el miércoles 14; la Renta nueva arranca el lunes 19.
    assert await service.close_irpf_weeks(GUILD) == []
    win = await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    clock.at(day(15))
    await put(service, USER, 50_000)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=30_000, payout=0)

    # El lunes 19, la semana del 12 no se cierra con las reglas nuevas: va con la vieja.
    clock.at(day(19, hour=0))
    closed = await service.close_irpf_weeks(GUILD)
    assert all(run.legacy or not run.returns for _week, run in closed)
    pending = await service.pending_declarations(GUILD, USER)
    assert [d.week_start for d in pending] == [date(2026, 10, 12)]
    assert 0 < pending[0].refund <= win.tax_delta

    # La primera semana con las reglas nuevas es la del 19.
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    filed = await close(service, clock, 26)
    assert filed.quota == week_tax(WeekIncome(gambling=35_000)).quota


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
    member = SimpleNamespace(id=USER, display_name="Diego")

    view = await cog._view(guild, member)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.mode == SEALED
    assert "Solo hay una forma de saberlo" in view.text()
    assert ids(view) == ["renta:abrir"]
    assert view.png is None and view.drawing is not None  # la carta se pinta con el sobre cerrado

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
    assert view.png == b"PNG"
    (card,) = interaction.response.edit_message.await_args.kwargs["attachments"]
    assert card.filename == "renta.png"
    statement = await service.irpf_statement(GUILD, USER)
    assert statement is not None and statement.opened
    opened = note.call_args.args[3].add
    assert opened["renta_opened"] == 1
    assert opened["renta_opened_refund"] == int(statement.filed.result < 0)

    assert ids(view)[:4] == ["renta:resumen", "renta:desglose", "renta:detalle", "renta:consejo"]

    await view._tab(DETAIL)(interaction)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.mode == DETAIL
    assert note.call_args.args[3].add == {"renta_detail": 1}
    text = view.text()
    assert "paso a paso" in text and "Casino" in text and "Premios" in text
    # Cambiar de pestaña no vuelve a subir la carta.
    assert "attachments" not in interaction.response.edit_message.await_args.kwargs

    await view._tab(CONCEPTS)(interaction)  # type: ignore[arg-type]  # noqa: SLF001
    assert note.call_args.args[3].add == {"renta_breakdown": 1}
    assert "Por conceptos" in view.text() and "Premios y logros" in view.text()

    await view._tab(ADVICE)(interaction)  # type: ignore[arg-type]  # noqa: SLF001
    assert note.call_args.args[3].add == {"renta_advice": 1}
    assert "consejo" in view.text() and "renta:donar" in ids(view)

    await view._tab(SUMMARY)(interaction)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.mode == SUMMARY and "renta:donar" not in ids(view)

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
    assert "Aún no tienes ninguna declaración" in view.text()
    assert ids(view) == [] and view.files() == []


# -- Embargo del 30 % y deuda (8.7.0) ----------------------------------------------------

DEBT = 1_000_000
OTHER = 20


def debt_payments(service: EconomyService) -> int:
    """Todo lo cobrado de deudas con Hacienda (lo que `hacienda` cuenta como «deudas»)."""
    with service.repository._transaction() as connection:  # noqa: SLF001
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM economy_debt_payments WHERE user_id = ?",
            (USER,),
        ).fetchone()
    return int(total)


async def test_quien_debe_paga_el_30_de_lo_que_cobra(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    before = await service.balance(GUILD, USER)
    add_debt(service, DEBT)
    state_before = (await service.treasury(GUILD, since=0)).balance

    income = await service.pay_income(GUILD, USER, gross=50_000, concept="logro:prueba")
    salary = await service.pay_salary(GUILD, USER, gross=8_000, concept="pala:obra")

    net = (50_000 - income.tax) + salary.payslip.net
    taken = garnish(50_000 - income.tax) + garnish(salary.payslip.net)
    assert taken > 0
    assert await service.tax_debt(GUILD, USER) == DEBT - taken
    assert await service.balance(GUILD, USER) == salary.balance == before + net - taken
    state = (await service.treasury(GUILD, since=0)).balance
    assert (
        state - state_before
        == income.tax
        + salary.payslip.irpf
        + salary.payslip.ss_worker
        + (salary.payslip.ss_employer)
        + taken
    )
    assert debt_payments(service) == taken


async def test_sin_deuda_no_se_embarga_nada(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    before = await service.balance(GUILD, USER)

    income = await service.pay_income(GUILD, USER, gross=50_000, concept="logro:prueba")

    assert income.balance == before + 50_000 - income.tax
    assert debt_payments(service) == 0


async def test_en_el_casino_solo_se_embarga_la_ganancia(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await put(service, USER, 100_000)
    add_debt(service, DEBT)
    before = await service.balance(GUILD, USER)

    await service.place_bet(GUILD, USER, game="blackjack", stake=10_000)
    push = await service.pay_winnings(GUILD, USER, game="blackjack", amount=10_000)
    # La apuesta que vuelve no es dinero nuevo.
    assert push.balance == before and await service.tax_debt(GUILD, USER) == DEBT

    win = await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)

    gained = 35_000 - win.tax_delta
    assert await service.tax_debt(GUILD, USER) == DEBT - garnish(gained)
    assert win.balance == await service.balance(GUILD, USER) == before + gained - garnish(gained)


async def test_el_embargo_no_pasa_de_la_deuda(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    add_debt(service, 100)
    before = await service.balance(GUILD, USER)

    income = await service.pay_income(GUILD, USER, gross=50_000, concept="logro:prueba")

    assert await service.tax_debt(GUILD, USER) == 0
    assert income.balance == before + 50_000 - income.tax - 100


async def test_el_bizum_de_otro_se_embarga_y_el_de_mama_no(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await put(service, OTHER, 10_000)
    add_debt(service, DEBT)

    receipt = await service.bizum(GUILD, OTHER, USER, amount=1_000)
    assert await service.tax_debt(GUILD, USER) == DEBT - garnish(1_000)
    assert receipt.receiver_balance == await service.balance(GUILD, USER)

    await service.mom_bizum(GUILD, USER, amount=1_000)
    assert await service.tax_debt(GUILD, USER) == DEBT - garnish(1_000)


async def test_liquidar_la_deuda_paga_lo_que_llega(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    add_debt(service, 5_000)
    await put(service, USER, 3_000 - await service.balance(GUILD, USER))

    assert await service.pay_tax_debt(GUILD, USER) == (3_000, 2_000, 0)
    await put(service, USER, 10_000)
    assert await service.pay_tax_debt(GUILD, USER) == (2_000, 0, 8_000)
    assert await service.pay_tax_debt(GUILD, USER) == (0, 0, 8_000)
    assert (await service.tax_breakdown(GUILD))[USER]["deudas"] == 5_000


async def test_el_embargo_se_avisa_una_vez(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    note = MagicMock()
    monkeypatch.setattr("bot.cogs.renta.logros.note", note)
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    cog = Renta(MagicMock(), service)
    add_debt(service, DEBT)
    clock.now += 60

    income = await service.pay_income(GUILD, USER, gross=50_000, concept="logro:prueba")
    text = await cog.hint_for(GUILD, USER)

    taken = garnish(50_000 - income.tax)
    assert text is not None and "embargado" in text and "aún debes" in text
    assert note.call_args.args[3].add == {"debt_garnished": taken}
    assert await cog.hint_for(GUILD, USER) is None


def members() -> tuple[SimpleNamespace, SimpleNamespace]:
    return SimpleNamespace(id=GUILD), SimpleNamespace(id=USER, display_name="Diego")


def ids(view: RentaView) -> list[str]:
    """Los `custom_id` de los botones del panel, en orden."""
    return [b.custom_id for b in view.walk_children() if isinstance(b, discord.ui.Button)]  # type: ignore[misc]


async def test_al_abrirla_se_paga_entera_o_fraccionada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = MagicMock()
    monkeypatch.setattr("bot.cogs.renta.logros.note", note)
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    filed = await payable(service, clock)
    cog = Renta(MagicMock(), service)
    guild, member = members()

    view = await cog._view(guild, member)  # type: ignore[arg-type]  # noqa: SLF001
    assert ids(view) == ["renta:abrir"]  # el sobre no delata que sale a pagar
    assert "hasta el final del lunes" in view.text()
    interaction = fake_interaction(member)
    interaction.guild, interaction.channel_id = guild, 5
    await view._open(interaction)  # type: ignore[arg-type]  # noqa: SLF001
    tabs = ["renta:resumen", "renta:desglose", "renta:detalle", "renta:consejo"]
    assert ids(view) == [*tabs, "renta:pagar", "renta:fraccionar"]
    assert "fraccionarla" in view.text()

    interaction = fake_interaction(member)
    interaction.guild, interaction.channel_id = guild, 5
    await view._pay_split(interaction)  # type: ignore[arg-type]  # noqa: SLF001

    interaction.response.defer.assert_awaited_once()
    edited = interaction.edit_original_response.await_args.kwargs
    fresh = edited["view"]
    assert ids(fresh) == tabs
    assert "Fraccionada" in fresh.text()
    # El sello pasa de «PENDIENTE» a «FRACCIONADA»: la carta es otra y se vuelve a subir.
    assert fresh.seal == "FRACCIONADA" and len(edited["attachments"]) == 1
    added = note.call_args.args[3].add
    assert added == {
        "renta_paid": filed.result - second_installment(filed.result),
        "renta_split": 1,
    }


async def test_liquidar_la_deuda_desde_la_renta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bot.cogs.renta.logros.note", MagicMock())
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    cog = Renta(MagicMock(), service)
    guild, member = members()
    add_debt(service, 500)
    clock.now += 60

    view = await cog._view(guild, member)  # type: ignore[arg-type]  # noqa: SLF001
    assert ids(view) == ["renta:liquidar"]
    assert "Debes 500 Y$ a Hacienda" in view.text()
    interaction = fake_interaction(member)
    interaction.guild, interaction.channel_id = guild, 5
    await view._settle_debt(interaction)  # type: ignore[arg-type]  # noqa: SLF001

    assert await service.tax_debt(GUILD, USER) == 0
    fresh = interaction.edit_original_response.await_args.kwargs["view"]
    assert ids(fresh) == [] and "Deuda liquidada" in fresh.text()
    # Lo pagado a mano no se avisa como un embargo.
    assert await cog.hint_for(GUILD, USER) is None


# -- Lo pendiente para `saldo` -------------------------------------------------------------


def add_return(service: EconomyService, *, opened: bool, payment: str, second: int = 0) -> None:
    """Le apunta al miembro una declaración de la semana del 5 que sale a pagar 500 Y$."""
    with service.repository._transaction() as connection:  # noqa: SLF001
        connection.execute(
            "INSERT INTO economy_irpf_returns (guild_id, user_id, week_start, base, quota, "
            "deductions, withheld, result, created_at, opened, payment, pay_due, second, "
            "second_due) VALUES (?, ?, '2026-10-05', 0, 0, 0, 0, 500, 0, ?, ?, 7e8, ?, 8e8)",
            (GUILD, USER, int(opened), payment, second),
        )


async def test_saldo_no_destapa_la_renta_sin_abrir(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    filed = await close(service, clock, 12)
    cog = Renta(MagicMock(), service)

    (sealed,) = await cog.pending_for(GUILD, USER)

    # Ni la cifra ni la devolución: solo que está sin abrir y el plazo, que vale para todos.
    assert sealed.kind == "renta_sealed" and sealed.amount == 0
    assert str(filed.refund) not in sealed.text
    assert sealed.when == day(13, hour=0).timestamp()


async def test_saldo_ensena_la_devolucion_con_lo_que_queda_para_que_caduque(
    tmp_path: Path,
) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    filed = await close(service, clock, 12)
    await service.repository.open_irpf_return(GUILD, USER, "2026-10-05")
    cog = Renta(MagicMock(), service)

    (refund,) = await cog.pending_for(GUILD, USER)

    assert refund.kind == "renta_refund" and refund.amount == filed.refund
    # Caduca el lunes en que ya han pasado CLAIM_WEEKS semanas desde el cierre (el 9 de nov.).
    assert refund.when == day(9, month=11, hour=0).timestamp()


async def test_saldo_ensena_la_renta_a_pagar_el_segundo_plazo_y_la_deuda(tmp_path: Path) -> None:
    service = await make_service(tmp_path, Clock(day(13)))
    cog = Renta(MagicMock(), service)
    add_return(service, opened=True, payment=PAYMENT_PENDING)
    add_debt(service, 300)

    kinds = {i.kind: i for i in await cog.pending_for(GUILD, USER)}
    assert kinds["renta_due"].amount == 500 and kinds["renta_due"].when == 7e8
    assert kinds["renta_debt"].amount == 300

    with service.repository._transaction() as connection:  # noqa: SLF001
        connection.execute("DELETE FROM economy_irpf_returns")
    add_return(service, opened=True, payment=PAYMENT_SPLIT, second=200)
    second = next(i for i in await cog.pending_for(GUILD, USER) if i.kind == "renta_second")
    assert second.amount == 200 and second.when == 8e8


# -- El panel nuevo: justificante al presentar y donar desde el consejo (9.24.0) -------------


async def test_presentar_desde_el_panel_deja_el_justificante_con_su_sello(
    tmp_path: Path,
) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    await close(service, clock, 12)
    await service.open_irpf_return(GUILD, USER, WEEK)
    cog = Renta(MagicMock(), service)
    guild, member = members()
    view = await cog._view(guild, member)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.seal == "" and "PENDIENTE" in view.card_key and "renta:presentar" in ids(view)
    interaction = fake_interaction(member)
    interaction.guild, interaction.channel_id = guild, 5
    interaction.channel = SimpleNamespace(send=AsyncMock())

    await view._present(interaction)  # type: ignore[arg-type]  # noqa: SLF001

    interaction.response.defer.assert_awaited_once()
    edited = interaction.edit_original_response.await_args.kwargs
    fresh = edited["view"]
    assert fresh.seal == "PRESENTADA" and len(edited["attachments"]) == 1
    assert "Declaración presentada" in fresh.text() and "Ya la presentaste" in fresh.text()
    assert "renta:presentar" not in ids(fresh)
    assert "presenta la renta" in interaction.channel.send.await_args.args[0]


async def test_donar_desde_el_consejo_dona_como_donar_y_lo_apunta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.cogs.donations import Donaciones

    note = MagicMock()
    monkeypatch.setattr("bot.cogs.renta.logros.note", note)
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.pay_salary(GUILD, USER, gross=200_000, concept="pala:obra")
    await close(service, clock, 12)
    await service.open_irpf_return(GUILD, USER, WEEK)
    bot = MagicMock()
    cog = Renta(bot, service)
    cogs = {"Renta": cog, "Donaciones": Donaciones(bot, service)}
    bot.get_cog = cogs.get
    guild, member = members()
    view = await cog._view(guild, member, mode=ADVICE)  # type: ignore[arg-type]  # noqa: SLF001
    assert view.advice is not None and view.advice.more > 0

    # El botón abre el formulario con la cantidad que más rinde ya escrita.
    click = fake_interaction(member)
    await view._donate(click)  # type: ignore[arg-type]  # noqa: SLF001
    form = click.response.send_modal.await_args.args[0]
    assert form.amount.default == str(view.advice.more)

    before = await service.balance(GUILD, USER)
    submit = fake_interaction(member)
    submit.guild, submit.channel_id = guild, 5
    await cog.donate(submit, "hambre", str(view.advice.more), suggested=view.advice.more)  # type: ignore[arg-type]

    submit.response.defer.assert_awaited_once()
    assert await service.balance(GUILD, USER) == before - view.advice.more
    # Sale en el canal como con `/donar`.
    posted = submit.followup.send.await_args.kwargs
    assert posted["ephemeral"] is False and "dona" in posted["embed"].description
    assert note.call_args.args[3].add == {"renta_advice_donated": 1, "renta_advice_exact": 1}


async def test_donar_sin_saldo_no_apunta_nada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.cogs.donations import Donaciones

    note = MagicMock()
    monkeypatch.setattr("bot.cogs.renta.logros.note", note)
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    bot = MagicMock()
    cog = Renta(bot, service)
    bot.get_cog = {"Renta": cog, "Donaciones": Donaciones(bot, service)}.get
    guild, member = members()
    submit = fake_interaction(member)
    submit.guild, submit.channel_id = guild, 5

    await cog.donate(submit, "hambre", str(10 * STARTING_BALANCE), suggested=0)  # type: ignore[arg-type]

    assert submit.followup.send.await_args.kwargs["ephemeral"] is True
    note.assert_not_called()
