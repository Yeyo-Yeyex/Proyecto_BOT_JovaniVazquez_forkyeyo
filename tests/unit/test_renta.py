"""Pruebas de la declaración semanal del casino y su cog."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.cogs.renta import Renta, draft_text
from bot.repositories.economy import EconomyRepository
from bot.services.economy import STARTING_BALANCE, Declaration, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.taxes import gambling_day_tax, weekly_refund

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
    """Octubre de 2026: el lunes 5 empieza una semana."""
    return datetime(2026, month, d, hour, tzinfo=TIMEZONE)


async def make_service(tmp_path: Path, clock: Clock) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=clock)


def test_devolucion_semanal_compensa_dias() -> None:
    win_day = gambling_day_tax(35_000, 0)
    assert weekly_refund(net=10_000, withheld=win_day, other_recent_income=0) == (
        win_day - gambling_day_tax(10_000, 0)
    )
    assert weekly_refund(net=-5_000, withheld=win_day, other_recent_income=0) == win_day
    assert weekly_refund(net=35_000, withheld=win_day, other_recent_income=0) == 0


async def test_perder_otro_dia_de_la_semana_sale_a_devolver(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    win = await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    clock.at(day(7))
    await service.settle_bet(GUILD, USER, game="ruleta", stake=25_000, payout=0)

    assert await service.pending_declarations(GUILD, USER) == []  # la semana sigue abierta

    clock.at(day(12, hour=9))
    pending = await service.pending_declarations(GUILD, USER)

    assert pending == [Declaration(date(2026, 10, 5), win.tax_delta - gambling_day_tax(10_000, 0))]


async def test_presentar_cobra_del_estado_una_sola_vez(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
    clock.at(day(7))
    after_loss = await service.settle_bet(GUILD, USER, game="ruleta", stake=25_000, payout=0)
    clock.at(day(12))
    state_before = (await service.treasury(GUILD, since=0)).balance

    claim = await service.claim_declarations(GUILD, USER)
    again = await service.claim_declarations(GUILD, USER)

    assert claim.refunded > 0
    assert claim.balance == after_loss.balance + claim.refunded
    assert (await service.treasury(GUILD, since=0)).balance == state_before - claim.refunded
    assert again.refunded == 0 and again.declarations == ()


async def test_solo_se_guardan_las_dos_ultimas_semanas(tmp_path: Path) -> None:
    clock = Clock(day(5))
    service = await make_service(tmp_path, clock)
    # Tres semanas seguidas: gana un día y pierde otro, así cada una sale a devolver.
    for monday in (5, 12, 19):
        clock.at(day(monday))
        await service.settle_bet(GUILD, USER, game="ruleta", stake=1_000, payout=36_000)
        clock.at(day(monday + 2))
        await service.settle_bet(GUILD, USER, game="ruleta", stake=20_000, payout=0)
    clock.at(day(26))

    pending = await service.pending_declarations(GUILD, USER)

    assert [d.week_start for d in pending] == [date(2026, 10, 12), date(2026, 10, 19)]
    # Siguen ahí aunque pase mucho tiempo sin jugar.
    clock.at(day(20, month=12))
    assert len(await service.pending_declarations(GUILD, USER)) == 2


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
    clock.at(day(7))
    await service.settle_bet(GUILD, USER, game="ruleta", stake=25_000, payout=0)
    clock.at(day(12))
    cog = Renta(MagicMock(), service)
    channel = MagicMock(spec=["send"])
    channel.send = AsyncMock()
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=USER, display_name="Diego"),
        channel=channel,
        response=SimpleNamespace(is_done=lambda: True, edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.remind(interaction)  # type: ignore[arg-type]
    await cog.remind(interaction)  # type: ignore[arg-type]

    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    assert await cog.hint_for(GUILD, USER) is not None

    await cog.present(interaction)  # type: ignore[arg-type]

    assert "presenta la renta" in channel.send.await_args.args[0]
    assert await cog.hint_for(GUILD, USER) is None
