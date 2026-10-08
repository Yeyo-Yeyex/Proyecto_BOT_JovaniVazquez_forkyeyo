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
from interaction_fakes import fake_interaction

from bot.cogs import work as work_cog
from bot.cogs.work import PalaPanel, Work, game_id, parse_game_id
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
    interaction = fake_interaction(make_user(user_id))
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


async def test_sin_curro_el_panel_ofrece_los_cinco_oficios(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, errors = await open_panel(cog)
    errors.assert_not_awaited()
    assert panel is not None
    (select,) = [i for i in panel.walk_children() if isinstance(i, ui.Select)]
    assert {o.value for o in select.options} == {
        "obra",
        "hosteleria",
        "politica",
        "sanidad",
        "oficina",
    }
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
        await panel.game_click(make_interaction(), game.index, current.answer[0])
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
    await panel.game_click(make_interaction(), panel.shift.game.index, "ver")
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


async def level_up(cog: Work, job: str, level: int) -> None:
    status = await cog.service.status(GUILD, OWNER)
    assert status is not None and status.contract.job == job
    status.contract.level = level
    await cog.service.repository.save_contract(GUILD, OWNER, status.contract)


async def test_sanidad_tiene_boton_de_guardia_y_la_guardia_se_juega(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "sanidad")
    assert not any(b.label == "🚑 Guardia" for b in buttons(panel))  # celador
    await level_up(cog, "sanidad", 3)
    await panel.refresh(make_interaction())
    assert any(b.label == "🚑 Guardia" for b in buttons(panel))
    check_limits(panel)
    await panel._clock_in_guard(make_interaction())
    assert panel.shift is not None
    await panel._finish(None)
    assert "saliente" in texts(panel)
    blocked = make_interaction()
    await panel._clock_in(blocked)
    assert "saliente" in blocked.followup.send.await_args.args[0]


async def test_oficina_teletrabajo_y_hong_kong_desde_el_panel(tmp_path: Path) -> None:
    from bot.repositories.economy import LedgerEntry

    cog = await make_cog(tmp_path)
    await cog.service.economy.repository.apply(GUILD, OWNER, [LedgerEntry(50_000, "prueba")])
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "oficina")
    labels = [b.label or "" for b in buttons(panel)]
    assert "🏠 Teletrabajo" in labels
    assert not any("Hong Kong" in label for label in labels)  # becario
    await level_up(cog, "oficina", 3)
    await panel.refresh(make_interaction())
    assert any("Irse a Hong Kong" in (b.label or "") for b in buttons(panel))
    check_limits(panel)
    await panel._go_abroad(make_interaction())
    assert "Te vas a Hong Kong" in texts(panel)
    assert any("Volver a casa" in (b.label or "") for b in buttons(panel))
    # Viviendo fuera no se cambia de oficio desde el panel.
    assert not [
        i
        for i in panel.walk_children()
        if isinstance(i, ui.Select) and "curro" in (i.placeholder or "")
    ]
    await panel._clock_in(make_interaction())
    await panel._finish(None)
    assert "Payslip" in texts(panel) and "Salaries tax" in texts(panel)
    await panel.refresh(make_interaction())
    await panel._go_home(make_interaction())
    assert "Vuelves a casa" in texts(panel)


def custom_ids(panel: PalaPanel) -> dict[str, str]:
    """`etiqueta → custom_id` de los botones que se ven ahora mismo."""
    return {b.label or "": b.custom_id or "" for b in buttons(panel)}


def component_interaction(custom_id: str, user_id: int = OWNER) -> MagicMock:
    interaction = make_interaction(user_id)
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": custom_id, "component_type": 2}
    return interaction


def test_el_custom_id_del_minijuego_va_y_vuelve() -> None:
    assert parse_game_id(game_id(3, 7, 2)) == (3, 7, 2)
    assert parse_game_id(game_id(3, 0, "ver")) == (3, 0, "ver")
    assert parse_game_id("pala:3:7:otra") is None
    assert parse_game_id("pala:x:7:1") is None
    assert parse_game_id("tienda:1") is None


async def test_los_clics_del_minijuego_llegan_por_el_listener(tmp_path: Path) -> None:
    """Los botones del minijuego no dependen del registro de la vista de discord.py."""
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    current = game.current
    assert current is not None
    right = game_id(panel.token, game.index, current.answer[0])
    assert right in custom_ids(panel).values()
    # La vista no lo atiende (ni siquiera para su dueño): lo hace el listener.
    click = component_interaction(right)
    assert not await panel.interaction_check(click)
    click.response.send_message.assert_not_awaited()
    await cog.on_interaction(click)
    assert game.index == 1 and game.correct == 1
    click.response.edit_message.assert_awaited_once()
    await panel._finish(None)


async def test_un_clic_de_una_ronda_ya_pasada_se_acepta_sin_contar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    current = game.current
    assert current is not None
    await panel.game_click(make_interaction(), 0, current.answer[0])
    late = make_interaction()
    await panel.game_click(late, 0, current.answer[0])
    late.response.defer.assert_awaited_once()
    late.response.edit_message.assert_not_awaited()
    assert game.index == 1 and game.presses == 1 and game.stale == 1
    await panel._finish(None)


async def test_una_rafaga_en_la_memoria_cuenta_todas_las_pulsaciones(tmp_path: Path) -> None:
    """Clics seguidos con los botones de antes de redibujar: cuentan todos, en orden."""
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "politica")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert "Memoriza la secuencia" in texts(panel)
    await cog.on_interaction(component_interaction(custom_ids(panel)["✅ Memorizado"]))
    current = game.current
    assert current is not None and not game.showing
    ids = custom_ids(panel)
    sequence = [ids[current.options[i]] for i in current.answer]
    clicks = [component_interaction(custom_id) for custom_id in sequence]
    await asyncio.gather(*(cog.on_interaction(click) for click in clicks))
    assert game.perfect_rounds == 1 and game.index == 1
    for click in clicks:
        responses = click.response.edit_message.await_count + click.response.defer.await_count
        assert responses == 1
    # Solo el último redibuja; y la ronda nueva enseña cómo acabó la anterior.
    assert clicks[-1].response.edit_message.await_count == 1
    assert "Ronda anterior:** ✅" in texts(panel)
    await panel._finish(None)


async def test_un_fallo_en_la_memoria_se_ve_en_la_ronda_siguiente(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "politica")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    await panel.game_click(make_interaction(), 0, "ver")
    current = game.current
    assert current is not None
    wrong = next(i for i in range(len(current.options)) if i != current.answer[0])
    await panel.game_click(make_interaction(), 0, wrong)
    assert game.showing and game.index == 1
    assert "Fallaste en el 1.º: tocaba" in texts(panel)
    assert current.options[current.answer[0]] in texts(panel)
    await panel._finish(None)


async def test_otro_miembro_no_puede_jugar_tu_turno(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    current = panel.shift.game.current
    assert current is not None
    intruder = component_interaction(game_id(panel.token, 0, current.answer[0]), user_id=77)
    await cog.on_interaction(intruder)
    intruder.response.send_message.assert_awaited_once()
    assert panel.shift.game.presses == 0
    await panel._finish(None)


async def test_un_panel_caducado_avisa_al_pulsar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    click = component_interaction(game_id(999, 0, 1))
    await cog.on_interaction(click)
    assert "caducó" in click.response.send_message.await_args.args[0]


async def test_las_herramientas_de_la_mochila_ayudan_en_el_turno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = AsyncMock(return_value=frozenset({"chaleco_reflectante", "reloj_fichar", "huevo"}))
    monkeypatch.setattr(work_cog.tienda, "owned_keys", owned)
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert game.retries == 1
    assert set(game.tools) == {"chaleco_reflectante", "reloj_fichar"}
    assert "🛠️" in texts(panel) and "fallo gratis" in texts(panel)
    current = game.current
    assert current is not None
    wrong = next(i for i in range(len(current.options)) if i not in current.answer)
    await panel.game_click(make_interaction(), 0, wrong)
    assert game.index == 0 and game.saved == 1
    assert "tu herramienta te salva" in texts(panel)
    assert "fallo gratis gastado" in texts(panel)
    await panel._finish(None)
    assert panel._tools_owned == 2
