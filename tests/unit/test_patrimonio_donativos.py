"""Pruebas del Impuesto sobre el Patrimonio, el IGIC y los donativos deducibles."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import donations as donations_cog
from bot.cogs.donations import Donaciones
from bot.cogs.patrimonio import Patrimonio
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository, LedgerEntry
from bot.services.donations import BY_KEY, ONGS, find_ong
from bot.services.economy import STARTING_BALANCE, Declaration, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.taxes import (
    DONATION_FULL_LIMIT,
    WEALTH_MINIMUM,
    apply_scale,
    donation_deduction,
    igic,
    wealth_tax,
)

GUILD = 1
USER = 10
RICH = 20


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def at(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now


def day(d: int, hour: int = 18) -> datetime:
    """Octubre de 2026: los lunes son 5, 12 y 19."""
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


# -- Reglas ----------------------------------------------------------------------------


def test_patrimonio_exento_hasta_el_minimo() -> None:
    assert wealth_tax(WEALTH_MINIMUM) == 0
    assert wealth_tax(WEALTH_MINIMUM + 1) == 0  # 10 € de base: 0,02 € de cuota
    assert wealth_tax(0) == 0


def test_patrimonio_usa_la_escala_real_escalada() -> None:
    """200.000 Y$ dejan 130.000 Y$ de base = 1,3 M€ con la escala del art. 30."""
    expected_eur = (
        167_129.45 * 0.002
        + (334_252.88 - 167_129.45) * 0.003
        + (668_499.75 - 334_252.88) * 0.005
        + (1_300_000 - 668_499.75) * 0.009
    )
    assert wealth_tax(200_000) == round(expected_eur / 10)


def test_patrimonio_es_progresivo() -> None:
    rates = [wealth_tax(b) / b for b in (100_000, 500_000, 2_000_000, 20_000_000)]
    assert rates == sorted(rates)
    assert wealth_tax(20_000_000) < 20_000_000 * 0.035


def test_igic_es_el_siete_por_ciento() -> None:
    assert igic(1_000) == 70
    assert igic(0) == 0


def test_deduccion_80_y_40_con_los_limites() -> None:
    # 80 % de los primeros 2.500 Y$ y 40 % del resto.
    assert donation_deduction(5_000, 1_000_000, 10_000) == 2_000 + 1_000
    # No pasa del 10 % de la base…
    assert donation_deduction(5_000, 10_000, 10_000) == 800
    # …ni del IRPF pagado.
    assert donation_deduction(5_000, 1_000_000, 300) == 300
    # Sin IRPF no se recupera nada.
    assert donation_deduction(5_000, 1_000_000, 0) == 0
    assert DONATION_FULL_LIMIT == 2_500


def test_las_ongs_tienen_cuentas_negativas_y_distintas() -> None:
    accounts = [ong.account_id for ong in ONGS]
    assert len(ONGS) == 4
    assert all(a < 0 for a in accounts)
    assert len(set(accounts)) == 4
    assert find_ong("mares") is BY_KEY["mares"]
    assert find_ong("AYUDA") is BY_KEY["ayudante"]
    assert find_ong("cruz roja") is None


def test_apply_scale_no_cambia() -> None:
    """La escala del Patrimonio reutiliza la función del IRPF."""
    assert apply_scale(0, ((0.0, 0.1),)) == 0


# -- Patrimonio en la base de datos ----------------------------------------------------


async def test_la_primera_semana_solo_activa_y_la_siguiente_cobra(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    await service.repository.apply(GUILD, RICH, [_entry(199_000)])
    await service.balance(GUILD, USER)  # 1.000 Y$: no paga

    week, first = await service.charge_wealth_tax(GUILD)
    assert first.activation and not first.charges
    assert week == date(2026, 9, 28)

    clock.at(day(12, hour=0))
    week, run = await service.charge_wealth_tax(GUILD)
    again = await service.charge_wealth_tax(GUILD)

    assert week == date(2026, 10, 5)
    assert [(c.user_id, c.balance, c.tax) for c in run.charges] == [
        (RICH, 200_000, wealth_tax(200_000))
    ]
    assert again[1].done_before
    assert await service.balance(GUILD, RICH) == 200_000 - wealth_tax(200_000)
    assert ledger_sum(tmp_path, RICH) == await service.balance(GUILD, RICH)
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == wealth_tax(200_000)
    treasury = await service.treasury(GUILD, since=0)
    assert treasury.collected_total == wealth_tax(200_000)
    assert treasury.top_contributors == ((RICH, wealth_tax(200_000)),)


async def test_ni_el_estado_ni_las_ongs_pagan_patrimonio(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    await service.repository.apply(GUILD, RICH, [_entry(500_000)])
    await service.donate(
        GUILD, RICH, ong_key="mares", ong_account=BY_KEY["mares"].account_id, amount=300_000
    )
    await service.charge_wealth_tax(GUILD)
    clock.at(day(12))

    _, run = await service.charge_wealth_tax(GUILD)

    assert [c.user_id for c in run.charges] == [RICH]


def _entry(amount: int) -> LedgerEntry:
    return LedgerEntry(amount, "prueba")


# -- Donativos -------------------------------------------------------------------------


async def test_donar_pasa_el_dinero_a_la_ong(tmp_path: Path) -> None:
    service = await make_service(tmp_path, Clock(day(6)))
    ong = BY_KEY["hambre"]

    receipt = await service.donate(
        GUILD, USER, ong_key=ong.key, ong_account=ong.account_id, amount=400
    )

    assert receipt.balance == STARTING_BALANCE - 400
    assert ledger_sum(tmp_path, ong.account_id) == 400
    assert ledger_sum(tmp_path, USER) == receipt.balance
    assert (await service.ong_totals(GUILD))["hambre"] == (400, 1)


async def test_donativo_desgrava_en_la_renta_si_se_pago_irpf(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    income = await service.pay_income(GUILD, USER, gross=50_000, concept="nivel:50")
    assert income.tax > 0
    ong = BY_KEY["ayudante"]
    await service.donate(GUILD, USER, ong_key=ong.key, ong_account=ong.account_id, amount=3_000)

    clock.at(day(12, hour=9))
    pending = await service.pending_declarations(GUILD, USER)
    expected = donation_deduction(3_000, 50_000, income.tax)
    assert pending == [Declaration(date(2026, 10, 5), expected)]

    state_before = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    claim = await service.claim_declarations(GUILD, USER)
    assert claim.refunded == expected
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == state_before - expected
    assert ledger_sum(tmp_path, USER) == claim.balance


async def test_donativo_sin_irpf_no_desgrava(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    ong = BY_KEY["desmontar"]
    await service.donate(GUILD, USER, ong_key=ong.key, ong_account=ong.account_id, amount=500)

    clock.at(day(12, hour=9))

    assert await service.pending_declarations(GUILD, USER) == []


# -- Cogs ------------------------------------------------------------------------------


def make_ctx(user_id: int = USER) -> MagicMock:
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=GUILD)
    ctx.author = SimpleNamespace(id=user_id, bot=False, display_name="Diego")
    ctx.channel = MagicMock()
    ctx.send = AsyncMock()
    return ctx


async def test_donar_sin_argumentos_ensena_las_ongs(tmp_path: Path) -> None:
    cog = Donaciones(MagicMock(), await make_service(tmp_path, Clock(day(6))))
    ctx = make_ctx()

    await cog.donar_text.callback(cog, ctx)

    embed = ctx.send.await_args.kwargs["embed"]
    assert len(embed.fields) == 4
    assert len(embed) <= 6000


async def test_donar_por_texto_dona_y_cuenta_para_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = AsyncMock()
    monkeypatch.setattr(donations_cog.logros, "track", track)
    monkeypatch.setattr(donations_cog.renta, "hint", AsyncMock(return_value=None))
    service = await make_service(tmp_path, Clock(day(6)))
    cog = Donaciones(MagicMock(), service)
    ctx = make_ctx()

    await cog.donar_text.callback(cog, ctx, "mares", "mitad")

    assert await service.balance(GUILD, USER) == STARTING_BALANCE // 2
    text = ctx.send.await_args.kwargs["embed"].description
    assert "Mares Limpios" in text
    delta = track.await_args.args[4]
    assert delta.add == {"donated": STARTING_BALANCE // 2}
    assert delta.peak == {"ongs_supported": 1}


async def test_donar_mas_de_lo_que_tienes_no_mueve_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path, Clock(day(6)))
    cog = Donaciones(MagicMock(), service)
    ctx = make_ctx()

    await cog.donar_text.callback(cog, ctx, "hambre", "5k")

    assert "No te llega" in ctx.send.await_args.args[0]
    assert await service.balance(GUILD, USER) == STARTING_BALANCE


async def test_el_patrimonio_se_anuncia_y_suma_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    await service.repository.apply(GUILD, RICH, [_entry(299_000)])
    await service.charge_wealth_tax(GUILD)  # activación
    clock.at(day(12))
    monkeypatch.setattr("bot.cogs.patrimonio.time.time", clock)
    note = MagicMock()
    monkeypatch.setattr("bot.cogs.patrimonio.logros.note", note)
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.id = 5
    channel.send = AsyncMock()
    guild = SimpleNamespace(
        id=GUILD,
        text_channels=[channel],
        system_channel=None,
        get_member=lambda _id: SimpleNamespace(display_name="Ricachón"),
    )
    cog = Patrimonio(MagicMock(), service)

    await cog.run_guild(guild)  # type: ignore[arg-type]
    await cog.run_guild(guild)  # type: ignore[arg-type]

    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert "Ricachón" in (embed.description or "")
    delta = note.call_args.args[3]
    assert delta.add == {"wealth_tax_paid": wealth_tax(300_000), "wealth_tax_weeks": 1}
