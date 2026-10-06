"""Pruebas de `bot.cogs.work`: el panel de `pala`, el minijuego con botones y los canales.

Usan el servicio real sobre un SQLite temporal; Discord se sustituye por
dobles (`MagicMock`). `roll` se fija para que no haya accidentes ni eventos.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui

from bot.cogs.work import PalaPanel, Work
from bot.repositories.economy import EconomyRepository
from bot.repositories.work import WorkRepository
from bot.services import pala
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import local_day
from bot.services.pala import WorkService
from bot.services.work import LEGAL_EXTRAS_PER_WEEK, ORDINARY_SHIFTS, Mechanic

GUILD = 1
OWNER = 10


@pytest.fixture(autouse=True)
def no_dice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


def make_user(user_id: int = OWNER) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER) -> MagicMock:
    interaction = MagicMock()
    interaction.user = make_user(user_id)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.edit = AsyncMock()
    return interaction


async def make_cog(tmp_path: Path) -> Work:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    repository = WorkRepository(tmp_path / "bot.db")
    await repository.initialize()
    service = WorkService(repository, EconomyService(economy_repo), rng=random.Random(5))
    bot = MagicMock()
    bot.birthdays = None
    return Work(bot, service)


async def open_panel(cog: Work, channel_id: int = 99) -> tuple[PalaPanel | None, AsyncMock]:
    send = AsyncMock(return_value=MagicMock())
    errors = AsyncMock()
    await cog._open(
        guild=MagicMock(id=GUILD),
        channel=MagicMock(id=channel_id, parent_id=None),
        user=make_user(),
        send=send,
        send_error=errors,
    )
    panel = next(iter(cog.panels), None)
    return panel, errors


def buttons(panel: PalaPanel) -> list[ui.Button]:
    return [item for item in panel.walk_children() if isinstance(item, ui.Button)]


def texts(panel: PalaPanel) -> str:
    return "\n".join(
        item.content for item in panel.walk_children() if isinstance(item, ui.TextDisplay)
    )


def check_limits(panel: PalaPanel) -> None:
    """Lo que Discord rechazaría al enviar: demasiados componentes o texto."""
    assert panel.total_children_count <= 40
    assert len(texts(panel)) <= 4000
    for item in panel.walk_children():
        if isinstance(item, ui.ActionRow):
            assert len(item.children) <= 5


async def test_sin_curro_el_panel_ofrece_los_tres_oficios(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, errors = await open_panel(cog)
    errors.assert_not_awaited()
    assert panel is not None
    (select,) = [i for i in panel.walk_children() if isinstance(i, ui.Select)]
    assert {o.value for o in select.options} == {"obra", "hosteleria", "politica"}
    assert "Coge la pala" in texts(panel)
    check_limits(panel)


async def test_firmar_y_fichar_un_turno_entero_con_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    assert any(b.label == "⛏️ Fichar" for b in buttons(panel))
    check_limits(panel)

    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert game.mechanic is Mechanic.DIG
    check_limits(panel)
    while panel.shift is not None:
        current = game.current
        assert current is not None
        await panel._option(current.answer[0])(make_interaction())
        if panel.shift is not None:
            check_limits(panel)
    assert "Nómina" in texts(panel) and "100/100" in texts(panel)
    check_limits(panel)
    balance = await cog.service.economy.balance(GUILD, OWNER)
    assert balance > STARTING_BALANCE
    await asyncio.sleep(0)  # deja que el temporizador cancelado se cierre
    assert not cog.timers


async def test_la_memoria_ensena_y_luego_oculta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "hosteleria")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None and panel.shift.game.showing
    assert [b.label for b in buttons(panel)] == ["✅ Memorizado"]
    await panel._hide(make_interaction())
    assert not panel.shift.game.showing
    assert len(buttons(panel)) >= 5
    check_limits(panel)
    await panel._finish(None)


async def test_si_se_acaba_el_tiempo_el_turno_se_cobra_solo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "politica")
    await panel._clock_in(make_interaction())
    await panel.on_timeout()
    assert panel.shift is None
    assert "Turno ordinario" in texts(panel)
    status = await cog.service.status(GUILD, OWNER)
    assert status is not None and status.contract.shifts_today == 1


async def test_pasado_el_limite_legal_el_jefe_ofrece_el_b(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    status = await cog.service.status(GUILD, OWNER)
    assert status is not None
    contract = status.contract
    contract.shifts_today = ORDINARY_SHIFTS
    contract.extras_week = LEGAL_EXTRAS_PER_WEEK
    contract.shift_day = local_day(cog.service.now()).isoformat()
    await cog.service.repository.save_contract(GUILD, OWNER, contract)
    await panel._clock_in(make_interaction())
    assert panel.shift is None
    assert any("en B" in (b.label or "") for b in buttons(panel))
    await panel._clock_in_black(make_interaction())
    assert panel.shift is not None
    await panel._finish(None)
    assert "En B" in texts(panel)


async def test_la_pala_solo_se_coge_en_los_canales_del_tajo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.service.toggle_channel(GUILD, 5)
    panel, errors = await open_panel(cog, channel_id=6)
    assert panel is None
    errors.assert_awaited_once()
    assert "<#5>" in errors.await_args.args[0]
    panel, errors = await open_panel(cog, channel_id=5)
    assert panel is not None
    errors.assert_not_awaited()


async def test_otro_miembro_no_puede_tocar_tu_pala(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    intruder = make_interaction(user_id=77)
    assert not await panel.interaction_check(intruder)
    intruder.response.send_message.assert_awaited_once()


async def test_la_maquina_de_cafe_recarga_y_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._coffee(make_interaction(), "barraquito")
    assert "barraquito" in texts(panel)
    assert await cog.service.economy.balance(GUILD, OWNER) < STARTING_BALANCE


async def test_tajo_anade_quita_y_abre_todos_los_canales(tmp_path: Path) -> None:
    from bot.cogs.admin import Admin

    cog = await make_cog(tmp_path)
    bot = MagicMock()
    bot.work = cog.service
    admin = Admin(bot)
    responder = MagicMock()
    responder.guild = MagicMock(id=GUILD)
    responder.send = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 5
    channel.mention = "<#5>"

    await admin._tajo_impl(responder, channel, False)
    assert await cog.service.channels(GUILD) == frozenset({5})
    assert "añadido" in responder.send.await_args.args[0]
    await admin._tajo_impl(responder, channel, False)
    assert await cog.service.channels(GUILD) == frozenset()
    await cog.service.toggle_channel(GUILD, 7)
    await admin._tajo_impl(responder, None, True)
    assert await cog.service.channels(GUILD) == frozenset()
    assert "cualquier canal" in responder.send.await_args.args[0]


async def test_no_se_puede_fichar_en_dos_paneles_a_la_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    first, _ = await open_panel(cog)
    assert first is not None
    await first._hire(make_interaction(), "obra")
    second, _ = await open_panel(cog)
    second = next(p for p in cog.panels if p is not first)
    await first._clock_in(make_interaction())
    blocked = make_interaction()
    await second._clock_in(blocked)
    assert second.shift is None
    blocked.response.send_message.assert_awaited_once()
    await first._finish(None)
    assert not cog.working
