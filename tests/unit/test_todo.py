"""Pruebas de la lista de tareas: reglas, repositorio y cog `lista`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs.todo import (
    SELECT_ID,
    DoneSelect,
    EditSelect,
    Lista,
    PageButton,
    PriorityPicker,
    build_board,
    form_values,
)
from bot.repositories.todo import TodoRepository
from bot.services.todo import (
    MAX_TASK_LENGTH,
    MAX_TASKS,
    PAGE_SIZE,
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


def test_sin_prioridad_no_hay_ninguna_y_alta_suelta_no_cuenta() -> None:
    """«alta» sin «prioridad» delante es parte de la tarea (una mesa alta)."""
    assert parse_task("comprar una mesa alta") == ("comprar una mesa alta", None)


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


async def test_editar_cambia_texto_y_prioridad_sin_perder_la_antiguedad(
    repo: TodoRepository,
) -> None:
    task = await repo.add_task(GUILD_ID, 2, "uno", Priority.BAJA)

    edited = await repo.update_task(GUILD_ID, task.id, "uno bien escrito", Priority.ALTA)

    assert edited == Task(task.id, GUILD_ID, 2, "uno bien escrito", Priority.ALTA, task.created_at)
    assert await repo.list_tasks(GUILD_ID) == [edited]
    # Ni una tachada ni una de otro servidor.
    assert await repo.update_task(GUILD_ID + 1, task.id, "x", Priority.BAJA) is None
    await repo.remove_tasks(GUILD_ID, [task.id])
    assert await repo.update_task(GUILD_ID, task.id, "x", Priority.BAJA) is None


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


def test_cada_pagina_de_la_lista_llena_con_textos_largos_cabe_en_un_embed() -> None:
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
    assert select.custom_id == f"{SELECT_ID}:0"
    assert len(select.options) == PAGE_SIZE
    assert select.max_values == PAGE_SIZE


def _arrows(view: discord.ui.View) -> list[tuple[str, bool]]:
    return [
        (item.item.custom_id, item.item.disabled)  # type: ignore[attr-defined]
        for item in view.children
        if isinstance(item, PageButton)
    ]


def test_la_lista_se_parte_en_paginas_de_25_con_flechas() -> None:
    tasks = [_task(i, Priority.MEDIA, i) for i in range(1, PAGE_SIZE + 6)]

    first, view = build_board(_guild(), tasks)
    assert view is not None
    assert len((first.description or "").splitlines()) == PAGE_SIZE
    assert "Página 1/2" in (first.footer.text or "")
    assert _arrows(view) == [("lista:pag:0", True), ("lista:pag:1", False)]

    # Una página que ya no existe (se tacharon tareas) enseña la última.
    last, view = build_board(_guild(), tasks, page=7)
    assert view is not None
    lines = (last.description or "").splitlines()
    assert len(lines) == 5
    assert lines[0].startswith(f"`{PAGE_SIZE + 1}.`")
    assert view.children[0].item.custom_id == f"{SELECT_ID}:1"  # type: ignore[attr-defined]
    assert _arrows(view) == [("lista:pag:0", False), ("lista:pag:1", True)]


def test_una_sola_pagina_no_lleva_flechas() -> None:
    embed, view = build_board(_guild(), [_task(1, Priority.MEDIA, 0)])
    assert view is not None
    assert _arrows(view) == []
    assert "Página" not in (embed.footer.text or "")


async def test_el_menu_de_las_listas_antiguas_sigue_valiendo() -> None:
    """Las listas publicadas antes de las páginas llevan `lista:hecha` a secas."""
    for custom_id, page in (("lista:hecha", 0), ("lista:hecha:3", 3)):
        match = DoneSelect.__discord_ui_compiled_template__.fullmatch(custom_id)
        assert match is not None
        select = discord.ui.Select(custom_id=custom_id)
        item = await DoneSelect.from_custom_id(MagicMock(), select, match)
        assert item.page == page


def test_ningun_componente_de_la_lista_repite_custom_id_ni_se_pasa_de_filas() -> None:
    """Discord rechaza entero (400) un mensaje con dos `custom_id` iguales o más de 5 filas."""
    tasks = [_task(i, Priority(i % 3), i) for i in range(1, 3 * PAGE_SIZE + 1)]
    for page in range(3):
        _, view = build_board(_guild(), tasks, page=page)
        assert view is not None
        items = [child.item for child in view.children]  # type: ignore[attr-defined]
        ids = [item.custom_id for item in items]
        assert len(ids) == len(set(ids)), ids
        rows: dict[int, int] = {}
        for item in items:
            width = 5 if isinstance(item, discord.ui.Select) else 1
            rows[item.row or 0] = rows.get(item.row or 0, 0) + width
        assert max(rows) <= 4 and max(rows.values()) <= 5, rows


def test_el_menu_editar_lleva_la_tarea_su_prioridad_y_su_autor() -> None:
    _, view = build_board(_guild(), [_task(7, Priority.BAJA, 0, author=3)], page=0)
    assert view is not None
    [menu] = [item for item in view.children if isinstance(item, EditSelect)]
    assert menu.item.custom_id == "lista:editar:0"
    assert menu.item.max_values == 1
    assert [(o.label, o.value) for o in menu.item.options] == [("t7", "7:2:3")]


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
    author.guild = ctx.guild
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


async def test_tachar_vuelve_a_la_misma_pagina(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    tasks = [await repo.add_task(GUILD_ID, 2, f"t{i}", Priority.MEDIA) for i in range(30)]
    interaction = _interaction(_member(2))

    await cog.complete_from_menu(interaction, [tasks[-1].id], page=1)

    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "Página 2/2" in (embed.footer.text or "")


async def test_las_flechas_cambian_de_pagina_en_el_mismo_mensaje(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    for i in range(30):
        await repo.add_task(GUILD_ID, 2, f"t{i}", Priority.MEDIA)
    events: list[str] = []
    interaction = fake_interaction(_member(2), events=events)
    interaction.guild = _guild()

    await cog.show_page(interaction, 1)

    assert events == ["response.defer", "edit_original_response"]
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "Página 2/2" in (embed.footer.text or "")


# -- Elegir prioridad -----------------------------------------------------------------


def _channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=600, channel=SimpleNamespace(id=20)))
    return channel


def _author() -> MagicMock:
    author = _member(2)
    author.guild = _guild()
    return author


async def test_lista_texto_sin_prioridad_la_pregunta_y_no_apunta_aun(
    repo: TodoRepository,
) -> None:
    cog = Lista(MagicMock(), repo)
    ctx = _ctx(_member(2))
    ctx.send = AsyncMock(return_value=MagicMock())

    await cog.lista_text.callback(cog, ctx, tarea="regar las plantas")

    assert await repo.list_tasks(GUILD_ID) == []
    ctx.message.delete.assert_awaited_once()
    picker = ctx.send.await_args.kwargs["view"]
    assert isinstance(picker, PriorityPicker)
    assert picker.message is ctx.send.return_value
    labels = [button.label for button in picker.children]  # type: ignore[attr-defined]
    assert labels == ["Alta", "Media", "Baja"]


async def test_elegir_prioridad_apunta_publica_y_borra_la_pregunta(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    author = _author()
    channel = _channel()
    picker = PriorityPicker(cog, author, channel, "regar las plantas")
    picker.message = MagicMock()
    picker.message.delete = AsyncMock()

    await picker.choose(Priority.BAJA, _interaction(author))

    [task] = await repo.list_tasks(GUILD_ID)
    assert (task.text, task.priority) == ("regar las plantas", Priority.BAJA)
    channel.send.assert_awaited_once()
    picker.message.delete.assert_awaited_once()
    # El fin del plazo después del clic no la apunta otra vez.
    await picker.on_timeout()
    assert len(await repo.list_tasks(GUILD_ID)) == 1


async def test_solo_quien_la_apunta_elige_la_prioridad(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    picker = PriorityPicker(cog, _author(), _channel(), "regar las plantas")
    other = _interaction(_member(3))

    await picker.choose(Priority.ALTA, other)

    assert other.response.send_message.await_args.kwargs["ephemeral"] is True
    assert await repo.list_tasks(GUILD_ID) == []
    assert not picker.done


async def test_sin_elegir_a_tiempo_se_apunta_como_media(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    author = _author()
    prompt = _interaction(author)
    await prompt.response.send_message("¿prioridad?")  # la pregunta privada de /lista
    picker = PriorityPicker(cog, author, _channel(), "regar las plantas", prompt=prompt)

    await picker.on_timeout()

    [task] = await repo.list_tasks(GUILD_ID)
    assert task.priority == Priority.MEDIA
    assert prompt.edit_original_response.await_args.kwargs["view"] is None


async def test_slash_sin_prioridad_la_pregunta_en_privado(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    interaction = _interaction(_member(2))

    await cog.lista.callback(cog, interaction, tarea="regar las plantas", prioridad=None)

    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], PriorityPicker)
    assert await repo.list_tasks(GUILD_ID) == []


async def test_slash_sin_tarea_acepta_antes_de_leer_la_lista(repo: TodoRepository) -> None:
    """Responder tras leer la base de datos pasaba de los 3 s de Discord en el NAS."""
    cog = Lista(MagicMock(), repo)
    events: list[str] = []
    interaction = fake_interaction(_member(2), events=events)
    interaction.guild = _guild()
    interaction.original_response = AsyncMock(
        return_value=SimpleNamespace(id=700, channel=SimpleNamespace(id=20))
    )

    await cog.lista.callback(cog, interaction, tarea=None, prioridad=None)

    assert events == ["response.defer", "edit_original_response"]
    assert await repo.get_board(GUILD_ID) == (20, 700)


# -- Editar -----------------------------------------------------------------------------


async def test_editar_abre_el_formulario_con_lo_que_tenia(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    task = await repo.add_task(GUILD_ID, 2, "regar", Priority.BAJA)
    events: list[str] = []
    interaction = fake_interaction(_member(2), events=events)
    interaction.guild = _guild()
    interaction.message.edit = AsyncMock()

    await cog.open_edit_form(interaction, task.id, "regar", Priority.BAJA, 2, page=0)

    # El formulario es la primera respuesta, sin esperar a la base de datos.
    assert events == ["response.send_modal"]
    form = interaction.response.send_modal.await_args.args[0]
    assert form.custom_id == f"lista:form:{task.id}:0"
    payload = form.to_dict()["components"]
    assert payload[0]["component"]["value"] == "regar"
    chosen = [o["value"] for o in payload[1]["component"]["options"] if o.get("default")]
    assert chosen == [str(Priority.BAJA.value)]
    # Y la lista se repone para soltar la tarea elegida en el menú.
    interaction.message.edit.assert_awaited_once()


async def test_editar_una_ajena_sin_ser_admin_no_abre_el_formulario(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    interaction = _interaction(_member(2))
    interaction.message.edit = AsyncMock()

    await cog.open_edit_form(interaction, 1, "ajena", Priority.MEDIA, 3, page=0)

    interaction.response.send_modal.assert_not_awaited()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_guardar_el_formulario_edita_la_lista_en_su_sitio(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    vieja = await repo.add_task(GUILD_ID, 2, "regar", Priority.BAJA)
    await repo.add_task(GUILD_ID, 3, "nueva", Priority.ALTA)
    events: list[str] = []
    interaction = fake_interaction(_member(2), events=events)
    interaction.guild = _guild()
    tracked = AsyncMock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("bot.cogs.todo.logros.track", tracked)
        await cog.save_edit(interaction, vieja.id, "regar las plantas", Priority.ALTA)

    assert events[0] == "response.defer"
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    lines = (embed.description or "").splitlines()
    assert "cambió 💤 «regar» por 🔥 «regar las plantas»" in lines[0]
    # Sube a alta y, por antigüedad, va delante de la otra alta.
    assert "regar las plantas" in lines[2] and "nueva" in lines[3]
    assert tracked.await_args.args[4].add == {"todo_edited": 1}


async def test_solo_cambiar_la_prioridad_lo_dice_asi(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    task = await repo.add_task(GUILD_ID, 2, "regar", Priority.ALTA)
    interaction = _interaction(_member(2))
    tracked = AsyncMock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("bot.cogs.todo.logros.track", tracked)
        await cog.save_edit(interaction, task.id, "regar", Priority.BAJA)

    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "pasó «regar» de 🔥 alta a 💤 baja" in (embed.description or "")
    assert tracked.await_args.args[4].add == {"todo_edited": 1, "todo_demoted": 1}


async def test_guardar_sin_cambios_no_toca_la_lista(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    task = await repo.add_task(GUILD_ID, 2, "regar", Priority.ALTA)
    interaction = _interaction(_member(2))

    await cog.save_edit(interaction, task.id, " regar ", Priority.ALTA)

    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize(
    ("author", "admin", "texto", "aviso"),
    [
        (3, False, "otra cosa", "Solo puede editar"),
        (3, True, "  ", "Escribe qué hay que hacer"),
    ],
)
async def test_guardar_lo_que_no_vale_se_avisa_en_privado(
    repo: TodoRepository, author: int, admin: bool, texto: str, aviso: str
) -> None:
    cog = Lista(MagicMock(), repo)
    task = await repo.add_task(GUILD_ID, author, "regar", Priority.MEDIA)
    interaction = _interaction(_member(2, admin=admin))

    await cog.save_edit(interaction, task.id, texto, Priority.ALTA)

    sent = interaction.followup.send.await_args
    assert aviso in sent.args[0] and sent.kwargs["ephemeral"] is True
    [kept] = await repo.list_tasks(GUILD_ID)
    assert (kept.text, kept.priority) == ("regar", Priority.MEDIA)


async def test_guardar_una_tarea_ya_tachada_avisa(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    interaction = _interaction(_member(2, admin=True))

    await cog.save_edit(interaction, 999, "regar", Priority.ALTA)

    assert "ya no está" in interaction.followup.send.await_args.args[0]
    assert await repo.list_tasks(GUILD_ID) == []


async def test_un_formulario_de_antes_de_reiniciar_se_guarda_igual(repo: TodoRepository) -> None:
    """Sin formulario vivo en discord.py, lo contesta `on_interaction` por su `custom_id`."""
    cog = Lista(MagicMock(), repo)
    task = await repo.add_task(GUILD_ID, 2, "regar", Priority.MEDIA)
    interaction = _interaction(_member(2))
    interaction.type = discord.InteractionType.modal_submit
    interaction.data = {
        "custom_id": f"lista:form:{task.id}:0",
        "components": [
            {"type": 18, "component": {"type": 4, "custom_id": "texto", "value": "regar ya"}},
            {"type": 18, "component": {"type": 3, "custom_id": "prioridad", "values": ["0"]}},
        ],
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("bot.cogs.todo.logros.track", AsyncMock())
        await cog.on_interaction(interaction)

    [edited] = await repo.list_tasks(GUILD_ID)
    assert (edited.text, edited.priority) == ("regar ya", Priority.ALTA)


async def test_otros_formularios_no_los_toca_la_lista(repo: TodoRepository) -> None:
    cog = Lista(MagicMock(), repo)
    interaction = _interaction(_member(2))
    interaction.type = discord.InteractionType.modal_submit
    interaction.data = {"custom_id": "rastro:pujar:3", "components": []}

    await cog.on_interaction(interaction)

    interaction.response.defer.assert_not_awaited()


def test_los_campos_del_formulario_se_leen_en_los_dos_formatos() -> None:
    """Con `Label` (el de ahora) y en filas, como mandaba Discord antes."""
    rows = {"components": [{"type": 1, "components": [{"custom_id": "texto", "value": "a"}]}]}
    select = {"custom_id": "prioridad", "values": ["2"]}
    labels = {"components": [{"type": 18, "component": select}]}
    assert form_values(rows) == {"texto": "a"}
    assert form_values(labels) == {"prioridad": "2"}
