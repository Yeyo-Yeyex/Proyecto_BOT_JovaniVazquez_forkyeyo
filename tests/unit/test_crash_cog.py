"""Pruebas de bot.cogs.crash: la mesa compartida, el vuelo y el dinero.

Se usa la economía real sobre un SQLite temporal, un reloj falso (las esperas
lo adelantan al instante), un punto de explosión fijado a mano y un
renderizador falso. El bucle de rondas no se arranca solo: cada prueba mueve
las fases a mano (`fly`, `finish`…).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs.crash import Crash, CrashTable, Phase, trail
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.services.crash import multiplier_at, seconds_to
from bot.services.economy import STARTING_BALANCE, EconomyService

GUILD_ID = 1
CHANNEL_ID = 555
ANA, LEO = 10, 11


class FakeClock:
    """Reloj que solo avanza cuando el bucle "duerme"."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> float:
        return 1_800_000_000 + self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def render(self, **kwargs) -> bytes:  # noqa: ANN003
        self.calls.append(kwargs)
        return b"PNG"


def make_user(user_id: int, name: str = "Ana") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.bot = False
    return user


def make_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL_ID
    message = MagicMock()
    message.id = 999
    message.edit = AsyncMock()
    channel.last_message_id = message.id
    channel.send = AsyncMock(return_value=message)
    channel.test_message = message
    return channel


def make_interaction(user_id: int, name: str = "Ana") -> MagicMock:
    return fake_interaction(make_user(user_id, name))


async def make_cog(tmp_path: Path, crash_cents: int = 300) -> tuple[Crash, FakeClock]:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    clock = FakeClock()
    cog = Crash(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        clock=clock.monotonic,
        wall_clock=clock.wall,
        sleep=clock.sleep,
    )
    cog.new_crash_point = lambda: crash_cents  # type: ignore[method-assign]
    cog.start = MagicMock()  # type: ignore[method-assign]
    return cog, clock


async def open_table(cog: Crash, user_id: int = ANA, amount: str = "500", auto=None):
    channel = make_channel()
    send = AsyncMock(return_value=channel.test_message)
    errors = AsyncMock()
    await cog._crash_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(user_id),
        amount_text=amount,
        auto_text=auto,
        send=send,
        confirm=AsyncMock(),
        send_error=errors,
    )
    errors.assert_not_awaited()
    return cog.tables[CHANNEL_ID], channel


def state_balance(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, STATE_ACCOUNT_ID),
        ).fetchone()
    return int(row[0]) if row else 0


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


# -- Presentación ---------------------------------------------------------------------


def test_la_estela_sube() -> None:
    line = trail([100, 120, 150, 200])
    assert line.endswith("🚀")
    assert line[0] == "▁" and line[3] == "█"


def test_split_args_admite_cualquier_orden() -> None:
    assert Crash.split_args("500", "2x") == ("500", "2x")
    assert Crash.split_args("2x", "500") == ("500", "2x")
    assert Crash.split_args("no", None) == (None, "no")
    assert Crash.split_args(None, None) == (None, None)


# -- Abrir y entrar -------------------------------------------------------------------


async def test_crash_abre_la_mesa_y_cobra(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table, _ = await open_table(cog)
    assert table.phase is Phase.LOBBY
    assert table.round.seats[ANA].stake == 500
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 500
    cog.start.assert_called_once_with(table)  # type: ignore[attr-defined]


async def test_entrar_con_el_boton_usa_la_ficha_y_no_dos_veces(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table, _ = await open_table(cog)
    leo = make_interaction(LEO, "Leo")
    await table.join_button(leo)
    leo.response.defer.assert_awaited_once()
    leo.edit_original_response.assert_awaited_once()
    assert table.round.seats[LEO].stake == 100
    again = make_interaction(LEO, "Leo")
    await table.join_button(again)
    again.response.send_message.assert_awaited_once()
    assert await cog.economy.balance(GUILD_ID, LEO) == STARTING_BALANCE - 100


async def test_entrar_sin_saldo_avisa_y_no_se_sienta(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table, _ = await open_table(cog)
    cog.set_ficha(GUILD_ID, LEO, STARTING_BALANCE + 1)
    leo = make_interaction(LEO, "Leo")
    await table.join_button(leo)
    assert LEO not in table.round.seats
    assert leo.followup.send.await_args.kwargs["ephemeral"] is True


async def test_crash_con_otra_mesa_embarcando_entra_en_ella(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table, channel = await open_table(cog)
    confirm = AsyncMock()
    await cog._crash_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(LEO, "Leo"),
        amount_text="200",
        auto_text="2x",
        send=AsyncMock(),
        confirm=confirm,
        send_error=AsyncMock(),
    )
    assert cog.tables[CHANNEL_ID] is table
    assert table.round.seats[LEO].stake == 200
    assert table.round.seats[LEO].auto_cents == 200
    confirm.assert_awaited_once()


async def test_crash_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({1234})
    errors = AsyncMock()
    await cog._crash_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=make_channel(),
        user=make_user(ANA),
        amount_text="100",
        auto_text=None,
        send=AsyncMock(),
        confirm=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert not cog.tables


async def test_los_botones_de_ficha_la_cambian_en_privado(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table, _ = await open_table(cog)
    leo = make_interaction(LEO, "Leo")
    await table.double(leo)
    assert cog.ficha(GUILD_ID, LEO) == 200
    await table.halve(make_interaction(LEO, "Leo"))
    assert cog.ficha(GUILD_ID, LEO) == 100
    await table.all_in(make_interaction(LEO, "Leo"))
    assert cog.ficha(GUILD_ID, LEO) == STARTING_BALANCE
    assert leo.followup.send.await_args.kwargs["ephemeral"] is True


async def test_auto_desde_el_formulario_cambia_el_asiento(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table, _ = await open_table(cog)
    await table.set_auto(make_interaction(ANA), "1,5")
    assert table.round.seats[ANA].auto_cents == 150
    await table.set_auto(make_interaction(ANA), "")
    assert table.round.seats[ANA].auto_cents is None
    bad = make_interaction(ANA)
    await table.set_auto(bad, "abc")
    bad.response.send_message.assert_awaited_once()


# -- Vuelo ----------------------------------------------------------------------------


async def test_retirarse_a_mano_paga_el_multiplicador_del_momento(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path, crash_cents=500)
    table, _ = await open_table(cog)
    table.phase = Phase.FLYING
    table.launched_at = clock.now
    clock.now += seconds_to(200) + 0.01
    cents = multiplier_at(clock.now)
    interaction = make_interaction(ANA)
    await table.cash_out(interaction)
    paid = 500 * cents // 100
    seat = table.round.seats[ANA]
    assert seat.cashed_cents == cents and seat.payout == paid
    tax = table.settlements[ANA].tax_delta
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 500 + paid - tax
    assert ledger_sum(tmp_path, ANA) == await cog.economy.balance(GUILD_ID, ANA)
    assert state_balance(tmp_path) == tax
    interaction.edit_original_response.assert_awaited_once()
    interaction.followup.send.assert_awaited()


async def test_retirarse_tras_la_explosion_es_tarde(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path, crash_cents=150)
    table, _ = await open_table(cog)
    table.phase = Phase.FLYING
    table.launched_at = clock.now
    clock.now += seconds_to(150) + 0.01
    interaction = make_interaction(ANA)
    await table.cash_out(interaction)
    assert table.round.seats[ANA].cashed_cents is None
    assert "Tarde" in interaction.response.send_message.await_args.args[0]


async def test_quien_no_juega_no_puede_retirarse(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path, crash_cents=500)
    table, _ = await open_table(cog)
    table.phase = Phase.FLYING
    table.launched_at = clock.now
    clock.now += 1
    interaction = make_interaction(LEO, "Leo")
    await table.cash_out(interaction)
    interaction.response.send_message.assert_awaited_once()


async def test_el_vuelo_paga_el_auto_retiro_exacto_y_explota(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path, crash_cents=400)
    table, _ = await open_table(cog, auto="2x")
    leo = make_interaction(LEO, "Leo")
    await table.join_button(leo)  # sin auto: se queda dentro y explota
    await table.fly()
    assert clock.now >= seconds_to(400)
    assert table.round.seats[ANA].cashed_cents == 200
    assert table.round.seats[ANA].by_auto
    assert table.round.seats[LEO].cashed_cents is None
    await table.finish()
    assert table.history[0] == 400
    assert table.phase is Phase.LOBBY and not table.round.seats
    assert table.round_no == 2
    ana = await cog.economy.balance(GUILD_ID, ANA)
    leo_balance = await cog.economy.balance(GUILD_ID, LEO)
    assert leo_balance == STARTING_BALANCE - 100
    assert ana <= STARTING_BALANCE + 500 and ana >= STARTING_BALANCE
    assert ledger_sum(tmp_path, ANA) == ana
    assert cog.renderer.calls[0]["crash_cents"] == 400  # type: ignore[attr-defined]
    assert "Explotó" in (table.last_result or "")


async def test_si_no_queda_nadie_dentro_explota_ya(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path, crash_cents=50_000)
    table, _ = await open_table(cog, auto="1,5x")
    await table.fly()
    assert table.round.seats[ANA].cashed_cents == 150
    # No espera los ~40 s hasta 500x: corta en cuanto cobra el último.
    assert clock.now < seconds_to(160)


async def test_el_vuelo_edita_como_mucho_una_vez_por_segundo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, crash_cents=1_000)
    table, channel = await open_table(cog)
    table.message = channel.test_message
    await table.fly()
    await table.finish()
    edits = channel.test_message.edit.await_count
    # Despegue + una por segundo (~15 s hasta 10x) + embarque siguiente.
    assert edits <= 2 + int(seconds_to(1_000)) + 1


async def test_embarque_vacio_cierra_la_mesa(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table, channel = await open_table(cog)
    table.message = channel.test_message
    table.round.seats.clear()
    await table.run()
    assert table.phase is Phase.CLOSED
    assert CHANNEL_ID not in cog.tables


async def test_crash_en_pleno_vuelo_entra_en_la_siguiente(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, crash_cents=200)
    table, channel = await open_table(cog)
    table.phase = Phase.FLYING
    confirm = AsyncMock()
    await cog._crash_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(LEO, "Leo"),
        amount_text="300",
        auto_text=None,
        send=AsyncMock(),
        confirm=confirm,
        send_error=AsyncMock(),
    )
    assert LEO in table.queued and LEO not in table.round.seats
    assert await cog.economy.balance(GUILD_ID, LEO) == STARTING_BALANCE
    table.phase = Phase.LOBBY
    await table.fly()
    await table.finish()
    assert table.round.seats[LEO].stake == 300
    assert await cog.economy.balance(GUILD_ID, LEO) == STARTING_BALANCE - 300


async def test_mesa_enterrada_se_manda_abajo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, crash_cents=200)
    table, channel = await open_table(cog)
    old = channel.test_message
    table.message = old
    channel.last_message_id = 12345  # alguien ha escrito después
    new = MagicMock(id=1000, edit=AsyncMock())
    channel.send = AsyncMock(return_value=new)
    await table.fly()
    await table.finish()
    channel.send.assert_awaited_once()
    assert table.message is new
    assert old.edit.await_args.kwargs == {"view": None}


# -- Apagado --------------------------------------------------------------------------


async def test_apagar_en_embarque_devuelve_lo_apostado(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await open_table(cog)
    await cog.cog_unload()
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    assert not cog.tables


async def test_apagar_en_vuelo_retira_a_todos_en_el_momento(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path, crash_cents=1_000)
    table, _ = await open_table(cog, amount="100")
    table.phase = Phase.FLYING
    table.launched_at = clock.now
    clock.now += seconds_to(300) + 0.01
    await cog.cog_unload()
    seat = table.round  # la ronda se vacía al cerrar
    assert not seat.seats
    balance = await cog.economy.balance(GUILD_ID, ANA)
    assert balance >= STARTING_BALANCE + 100  # 3x menos IRPF, como mínimo +100
    assert ledger_sum(tmp_path, ANA) == balance


@pytest.mark.parametrize("phase", [Phase.LOBBY, Phase.FLYING])
async def test_las_vistas_tienen_los_botones_de_su_fase(tmp_path: Path, phase: Phase) -> None:
    cog, _ = await make_cog(tmp_path)
    table = CrashTable(cog, guild_id=GUILD_ID, channel=make_channel())
    labels = [item.label for item in table.view(phase).children]  # type: ignore[attr-defined]
    if phase is Phase.LOBBY:
        assert labels[0] == "🚀 Entrar" and "🎯 Auto" in labels
    else:
        assert labels == ["💸 Retirar"]


async def test_bucle_completo_juega_una_ronda_y_cierra(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, crash_cents=250)
    table, channel = await open_table(cog, auto="2x")
    table.message = channel.test_message
    await table.run()
    assert table.phase is Phase.CLOSED
    assert list(table.history) == [250]
    assert table.round_no == 2
    balance = await cog.economy.balance(GUILD_ID, ANA)
    assert STARTING_BALANCE < balance <= STARTING_BALANCE + 500
    final = channel.test_message.edit.await_args.kwargs
    assert final["view"] is None and "cerrada" in final["embed"].description
