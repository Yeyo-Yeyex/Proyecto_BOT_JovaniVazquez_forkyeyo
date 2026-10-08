"""Pruebas de la lista de tareas: reglas, repositorio y cog `lista`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs.todo import SELECT_ID, Lista, build_board
from bot.repositories.todo import TodoRepository
from bot.services.todo import (
    MAX_TASK_LENGTH,
    MAX_TASKS,
    Priority,
    Task,
    TaskError,
    can_complete,
    parse_task,
    sort_tasks,
)

GUILD_ID = 1

# -- Reglas ---------------------------------------------------------------------------


def test_la_prioridad_se_lee_del_final_del_texto() -> None:
    assert parse_task("comprar pan prioridad alta") == ("comprar pan", Priority.ALTA)


def test_la_prioridad_se_lee_en_cualquier_sitio_y_sin_mayusculas() -> None:
    assert parse_task("PRIORIDAD Baja regar las plantas") == ("regar las plantas", Priority.BAJA)
    assert parse_task("arreglar prio: alta el purge") == ("arreglar el purge", Priority.ALTA)


def test_sin_prioridad_es_media_y_alta_suelta_no_cuenta() -> None:
    """«alta» sin «prioridad» delante es parte de la tarea (una mesa alta)."""
    assert parse_task("comprar una mesa alta") == ("comprar una mesa alta", Priority.MEDIA)


def test_la_prioridad_del_desplegable_manda_sobre_el_texto() -> None:
    """Incluye alta, que vale 0: no debe tratarse como «sin prioridad»."""
    assert parse_task("x prioridad baja", Priority.ALTA) == ("x", Priority.ALTA)


def test_una_tarea_vacia_o_larga_no_se_apunta() -> None:
    with pytest.raises(TaskError):
        parse_task("   prioridad alta  ")
    with pytest.raises(TaskError):
        parse_task("a" * (MAX_TASK_LENGTH + 1))


def _task(task_id: int, priority: Priority, created: float, author: int = 2) -> Task:
    return Task(task_id, GUILD_ID, author, f"t{task_id}", priority, created)


def test_se_ordena_por_prioridad_y_luego_por_antiguedad() -> None:
    tasks = [
        _task(1, Priority.BAJA, 1),
        _task(2, Priority.ALTA, 5),
        _task(3, Priority.MEDIA, 2),
        _task(4, Priority.ALTA, 3),
    ]
    assert [t.id for t in sort_tasks(tasks)] == [4, 2, 3, 1]


def test_tacha_quien_la_apunto_o_un_admin() -> None:
    task = _task(1, Priority.MEDIA, 0, author=7)
    assert can_complete(task, 7, is_admin=False)
    assert can_complete(task, 8, is_admin=True)
    assert not can_complete(task, 8, is_admin=False)


# -- Repositorio ------------------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path: Path) -> TodoRepository:
    repository = TodoRepository(tmp_path / "bot.sqlite3")
    await repository.initialize()
    return repository


async def test_el_repositorio_apunta_lista_y_tacha(repo: TodoRepository) -> None:
    a = await repo.add_task(GUILD_ID, 2, "uno", Priority.BAJA)
    b = await repo.add_task(GUILD_ID, 3, "dos", Priority.ALTA)
    await repo.add_task(GUILD_ID + 1, 2, "de otro servidor", Priority.ALTA)

    assert {t.id for t in await repo.list_tasks(GUILD_ID)} == {a.id, b.id}
    assert await repo.remove_tasks(GUILD_ID, [a.id, 999]) == [a.id]
    # Tachar dos veces la misma no la cuenta otra vez.
    assert await repo.remove_tasks(GUILD_ID, [a.id]) == []
    assert [t.text for t in await repo.list_tasks(GUILD_ID)] == ["dos"]


async def test_no_se_pasa_del_tope_de_tareas(repo: TodoRepository) -> None:
    for i in range(MAX_TASKS):
        await repo.add_task(GUILD_ID, 2, f"t{i}", Priority.MEDIA)
    with pytest.raises(TaskError):
        await repo.add_task(GUILD_ID, 2, "una más", Priority.MEDIA)


async def test_el_repositorio_recuerda_el_ultimo_mensaje_de_la_lista(repo: TodoRepository) -> None:
    assert await repo.get_board(GUILD_ID) is None
    await repo.set_board(GUILD_ID, 10, 100)
    await repo.set_board(GUILD_ID, 11, 101)
    assert await repo.get_board(GUILD_ID) == (11, 101)


# -- Embed ------------------------------------------------------------------------------


def _guild() -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    guild.get_member = MagicMock(return_value=None)
    return guild


def test_la_lista_llena_con_textos_largos_cabe_en_un_embed() -> None:
    tasks = [
        Task(i, GUILD_ID, 2, "x" * MAX_TASK_LENGTH, Priority(i % 3), i) for i in range(MAX_TASKS)
    ]
    titles = ", ".join(["«" + "y" * MAX_TASK_LENGTH + "»"] * 3)
    note = f"✅ **alguien** tachó {titles} y 9 más."
    embed, view = build_board(_guild(), tasks, note)
    assert len(embed.description or "") <= 4096
    assert len(embed) <= 6000
    assert view is not None
    select = view.children[0].item  # type: ignore[attr-defined]
    assert select.custom_id == SELECT_ID
    assert len(select.options) == MAX_TASKS
    assert select.max_values == MAX_TASKS


def test_la_lista_vacia_no_lleva_menu() -> None:
    embed, view = build_board(_guild(), [])
    assert view is None
    assert "Nada pendiente" in (embed.description or "")


# -- Cog --------------------------------------------------------------------------------


def _member(user_id: int, *, admin: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = False
    member.display_name = f"m{user_id}"
    member.guild_permissions = discord.Permissions(administrator=admin)
    return member


def _ctx(author: MagicMock, sent_id: int = 500) -> MagicMock:
    ctx = MagicMock()
    ctx.guild = _guild()
    ctx.author = author
    ctx.message.delete = AsyncMock()
    sent = SimpleNamespace(id=sent_id, channel=SimpleNamespace(id=20))
    ctx.channel.send = AsyncMock(return_value=sent)
    ctx.send = AsyncMock()
    return ctx


async def test_lista_texto_apunta_borra_la_orden_y_publica_la_lista_ordenada(
    repo: TodoRepository,
) -> None:
    cog = Lista(MagicMock(), repo)
    await repo.add_task(GUILD_ID, 2, "vieja", Priority.BAJA)
    ctx = _ctx(_member(2))

    await cog.lista_text.callback(cog, ctx, tarea="arreglar el purge prioridad alta")

    ctx.message.delete.assert_awaited_once()
    embed = ctx.channel.send.await_args.kwargs["embed"]
    lines = (embed.description or "").splitlines()
    assert "apuntó" in lines[0]
    # La alta sale antes que la baja, aunque se apuntó después.
    assert lines[2].endswith("· <@2>") and "arreglar el purge" in lines[2]
    assert "vieja" in lines[3]
    assert await repo.get_board(GUILD_ID) == (20, 500)


async def test_publicar_otra_lista_borra_la_anterior(repo: TodoRepository) -> None:
    bot = MagicMock()
    old_channel = MagicMock(spec=discord.TextChannel)
    old_message = MagicMock()
    old_message.delete = AsyncMock()
    old_channel.get_partial_message = MagicMock(return_value=old_message)
    bot.get_channel = MagicMock(return_value=old_channel)
    cog = Lista(bot, repo)
    await repo.set_board(GUILD_ID, 20, 400)

    await cog.lista_text.callback(cog, _ctx(_member(2), sent_id=401), tarea=None)

    old_channel.get_partial_message.assert_called_once_with(400)
    old_message.delete.assert_awaited_once()
    assert await repo.get_board(GUILD_ID) == (20, 401)


async def test_una_tarea_invalida_se_explica_y_no_se_apunta(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    ctx = _ctx(_member(2))

    await cog.lista_text.callback(cog, ctx, tarea="prioridad alta")

    assert "Uso:" in ctx.send.await_args.args[0]
    ctx.channel.send.assert_not_awaited()
    assert await repo.list_tasks(GUILD_ID) == []


def _interaction(user: MagicMock) -> MagicMock:
    interaction = fake_interaction(user)
    interaction.guild = _guild()
    return interaction


async def test_el_menu_tacha_solo_las_propias_si_no_eres_admin(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    mine = await repo.add_task(GUILD_ID, 2, "mía", Priority.MEDIA)
    other = await repo.add_task(GUILD_ID, 3, "ajena", Priority.MEDIA)
    interaction = _interaction(_member(2))

    await cog.complete_from_menu(interaction, [mine.id, other.id])

    assert [t.text for t in await repo.list_tasks(GUILD_ID)] == ["ajena"]
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "tachó «mía»" in (embed.description or "")
    assert "1 no eran suyas" in (embed.description or "")


async def test_el_menu_no_deja_tachar_ajenas_sin_ser_admin(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    other = await repo.add_task(GUILD_ID, 3, "ajena", Priority.MEDIA)
    interaction = _interaction(_member(2))

    await cog.complete_from_menu(interaction, [other.id])

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    interaction.edit_original_response.assert_not_awaited()
    assert len(await repo.list_tasks(GUILD_ID)) == 1


async def test_un_admin_tacha_cualquiera_y_la_lista_se_queda_sin_menu(
    repo: TodoRepository,
) -> None:
    cog = Lista(MagicMock(), repo)
    other = await repo.add_task(GUILD_ID, 3, "ajena", Priority.MEDIA)
    interaction = _interaction(_member(9, admin=True))

    await cog.complete_from_menu(interaction, [other.id])

    assert await repo.list_tasks(GUILD_ID) == []
    assert interaction.edit_original_response.await_args.kwargs["view"] is None
