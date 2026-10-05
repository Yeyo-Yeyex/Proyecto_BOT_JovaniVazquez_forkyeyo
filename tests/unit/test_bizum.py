"""Bizum entre miembros: el dinero pasa entero, cuadra el libro y salen los logros."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import bizum as bizum_cog
from bot.cogs.bizum import Bizum
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository, LedgerEntry
from bot.services.achievements import BY_ID, bizum_received_stats, bizum_stats, newly_unlocked
from bot.services.bizum import (
    CONCEPT_MAX,
    MAX_DAILY,
    MAX_OPERATION,
    MIN_AMOUNT,
    check_limits,
    clean_concept,
)
from bot.services.economy import STARTING_BALANCE, EconomyService, InsufficientFundsError
from bot.services.levels import TIMEZONE

GUILD = 1
DIEGO = 10
ANA = 20


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def at(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now


def day(d: int, hour: int = 18) -> datetime:
    return datetime(2026, 10, d, hour, tzinfo=TIMEZONE)


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


async def give(service: EconomyService, user_id: int, amount: int) -> None:
    await service.repository.apply(GUILD, user_id, [LedgerEntry(amount, "test")])


# -- Reglas ----------------------------------------------------------------------------


def test_los_limites_son_los_de_bizum_al_cambio_del_juego() -> None:
    # 0,50 €, 1.000 € y 2.000 € con 10 Y$ por euro.
    assert (MIN_AMOUNT, MAX_OPERATION, MAX_DAILY) == (5, 10_000, 20_000)


def test_pasarse_por_operacion_y_por_dia_se_detecta_por_separado() -> None:
    assert not check_limits(MAX_OPERATION, MAX_OPERATION).any
    assert check_limits(MAX_OPERATION + 1, MAX_OPERATION + 1).over_operation
    justo = check_limits(MAX_OPERATION, MAX_DAILY + 1)
    assert justo.over_daily and not justo.over_operation


def test_el_concepto_se_recorta_y_queda_en_una_linea() -> None:
    assert clean_concept("  la pizza\n de ayer ") == "la pizza de ayer"
    assert clean_concept("   ") is None
    assert len(clean_concept("x" * 200)) == CONCEPT_MAX


# -- Servicio --------------------------------------------------------------------------


async def test_el_bizum_pasa_entero_y_el_estado_no_cobra_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path, Clock(day(6)))

    receipt = await service.bizum(GUILD, DIEGO, ANA, amount=300)

    assert receipt.sender_balance == STARTING_BALANCE - 300
    assert receipt.receiver_balance == STARTING_BALANCE + 300
    assert ledger_sum(tmp_path, DIEGO) == await service.balance(GUILD, DIEGO)
    assert ledger_sum(tmp_path, ANA) == await service.balance(GUILD, ANA)
    assert await service.state_balance(GUILD) == 0


async def test_sin_saldo_no_se_mueve_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path, Clock(day(6)))

    with pytest.raises(InsufficientFundsError):
        await service.bizum(GUILD, DIEGO, ANA, amount=STARTING_BALANCE + 1)

    assert await service.balance(GUILD, DIEGO) == STARTING_BALANCE
    assert await service.balance(GUILD, ANA) == STARTING_BALANCE


async def test_menos_del_minimo_o_a_uno_mismo_no_vale(tmp_path: Path) -> None:
    service = await make_service(tmp_path, Clock(day(6)))

    with pytest.raises(ValueError, match="menos de"):
        await service.bizum(GUILD, DIEGO, ANA, amount=MIN_AMOUNT - 1)
    with pytest.raises(ValueError, match="ti mismo"):
        await service.bizum(GUILD, DIEGO, DIEGO, amount=100)
    with pytest.raises(ValueError):
        await service.repository.bizum(
            GUILD, DIEGO, STATE_ACCOUNT_ID, amount=100, now=0.0, day_start=0.0
        )


async def test_lo_enviado_hoy_se_suma_y_empieza_de_cero_al_dia_siguiente(tmp_path: Path) -> None:
    clock = Clock(day(6, 10))
    service = await make_service(tmp_path, clock)
    await give(service, DIEGO, 100_000)

    await service.bizum(GUILD, DIEGO, ANA, amount=MAX_OPERATION)
    clock.at(day(6, 23))
    receipt = await service.bizum(GUILD, DIEGO, ANA, amount=MAX_OPERATION + 1)
    assert receipt.sent_today == 2 * MAX_OPERATION + 1

    clock.at(day(7, 0))
    receipt = await service.bizum(GUILD, DIEGO, ANA, amount=500)
    assert receipt.sent_today == 500


# -- Logros ----------------------------------------------------------------------------


def test_a_espaldas_de_sanchez_salta_al_pasar_el_maximo_por_operacion() -> None:
    espaldas = BY_ID["bizum_espaldas"]
    assert espaldas.name == "A espaldas de Sánchez"
    assert espaldas.story

    justo = bizum_stats(amount=MAX_OPERATION, sent_today=MAX_OPERATION, balance_after=1)
    assert "bizum_espaldas" not in newly_unlocked(justo.peak | justo.add, [])
    pasado = bizum_stats(amount=MAX_OPERATION + 1, sent_today=MAX_OPERATION + 1, balance_after=1)
    assert "bizum_espaldas" in newly_unlocked(pasado.peak | pasado.add, [])


def test_bizum_stats_cuenta_minimo_maximo_justo_y_quedarse_a_cero() -> None:
    assert bizum_stats(amount=MIN_AMOUNT, sent_today=5, balance_after=10).add["bizum_min"] == 1
    assert bizum_stats(amount=MAX_OPERATION, sent_today=1, balance_after=10).add["bizum_full"] == 1
    assert bizum_stats(amount=50, sent_today=50, balance_after=0).add["bizum_broke"] == 1
    received = bizum_received_stats(amount=50, balance_after=1_050)
    assert received.add == {"bizum_received_count": 1, "bizum_received": 50}
    assert received.peak == {"balance_max": 1_050}


# -- Cog -------------------------------------------------------------------------------


def member(user_id: int, name: str, *, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, bot=bot, display_name=name, mention=f"<@{user_id}>")


def make_ctx(author: SimpleNamespace) -> MagicMock:
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=GUILD)
    ctx.author = author
    ctx.channel = MagicMock()
    ctx.send = AsyncMock()
    return ctx


@pytest.fixture
def track(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(bizum_cog.logros, "track", mock)
    monkeypatch.setattr(bizum_cog.renta, "hint", AsyncMock(return_value=None))
    return mock


async def test_bizum_por_texto_avisa_a_quien_recibe_y_apunta_logros_de_los_dos(
    tmp_path: Path, track: AsyncMock
) -> None:
    service = await make_service(tmp_path, Clock(day(6)))
    cog = Bizum(MagicMock(), service)
    ctx = make_ctx(member(DIEGO, "Diego"))
    ana = member(ANA, "Ana")

    await cog.bizum_text.callback(cog, ctx, ana, "200", concepto="las papas")

    assert await service.balance(GUILD, ANA) == STARTING_BALANCE + 200
    kwargs = ctx.send.await_args.kwargs
    assert ctx.send.await_args.args[0] == ana.mention
    assert kwargs["allowed_mentions"].users == [ana]
    text = kwargs["embed"].description
    assert "las papas" in text and "exentos de Donaciones" in text
    assert "🤫" not in text
    (sender_call, receiver_call) = track.await_args_list
    assert sender_call.args[2].id == DIEGO and sender_call.args[4].add["bizum_sent"] == 200
    assert receiver_call.args[2] is ana and receiver_call.args[4].add["bizum_received"] == 200


async def test_pasarse_del_maximo_se_deja_pero_se_dice(tmp_path: Path, track: AsyncMock) -> None:
    service = await make_service(tmp_path, Clock(day(6)))
    await give(service, DIEGO, 50_000)
    cog = Bizum(MagicMock(), service)
    ctx = make_ctx(member(DIEGO, "Diego"))

    await cog.bizum_text.callback(cog, ctx, member(ANA, "Ana"), "15k")

    assert await service.balance(GUILD, ANA) == STARTING_BALANCE + 15_000
    assert "pasa del máximo" in ctx.send.await_args.kwargs["embed"].description
    assert track.await_args_list[0].args[4].peak["bizum_max"] == 15_000


async def test_a_un_bot_o_sin_saldo_no_se_manda_nada(tmp_path: Path, track: AsyncMock) -> None:
    service = await make_service(tmp_path, Clock(day(6)))
    cog = Bizum(MagicMock(), service)
    ctx = make_ctx(member(DIEGO, "Diego"))

    await cog.bizum_text.callback(cog, ctx, member(99, "Jovani", bot=True), "100")
    assert "bots" in ctx.send.await_args.args[0]
    await cog.bizum_text.callback(cog, ctx, member(ANA, "Ana"), "5k")
    assert "No te llega" in ctx.send.await_args.args[0]

    assert await service.balance(GUILD, DIEGO) == STARTING_BALANCE
    track.assert_not_awaited()


async def test_slash_lleva_el_aviso_de_la_renta(
    tmp_path: Path, track: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    remind = AsyncMock()
    monkeypatch.setattr(bizum_cog.renta, "remind", remind)
    cog = Bizum(MagicMock(), await make_service(tmp_path, Clock(day(6))))
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = SimpleNamespace(id=GUILD)
    interaction.user = member(DIEGO, "Diego")
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()

    await cog.bizum.callback(cog, interaction, member(ANA, "Ana"), "100")

    remind.assert_awaited_once()
