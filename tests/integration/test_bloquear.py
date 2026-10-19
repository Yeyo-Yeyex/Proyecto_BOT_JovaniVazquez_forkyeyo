"""Pruebas de `bloquear` con el bot real (`BotClient` y `load_extension`).

El bloqueo cruza cogs: lo guarda `Admin`, lo contesta `Errors` en los comandos `.` y
para los botones de otro cog envolviendo el reparto de clics de discord.py. Por eso se
prueba con los cogs cargados como en producción, no montados a mano.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from discord.ext import commands
from interaction_fakes import fake_interaction

from bot.app import BotClient
from bot.cogs.errors import BLOCKED_ERROR, describe_command_error

GUILD_ID = 10
ADMIN_ID = 1


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
    await client.economy.repository.initialize()
    await client.moderation.initialize()
    for extension in ("bot.cogs.errors", "bot.cogs.blackjack", "bot.cogs.mines", "bot.cogs.admin"):
        await client.load_extension(extension)
    return client


def admin_ctx(client: BotClient) -> MagicMock:
    """Contexto de `.bloquear` escrito por un administrador."""
    author = MagicMock(spec=discord.Member)
    author.id = ADMIN_ID
    author.guild_permissions = discord.Permissions(administrator=True)
    ctx = MagicMock()
    ctx.bot = client
    ctx.guild = SimpleNamespace(id=GUILD_ID)
    ctx.author = author
    ctx.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return ctx


async def bloquear(client: BotClient, word: str) -> str:
    """Ejecuta `.bloquear <word>` y devuelve lo que contesta."""
    admin = client.get_cog("Admin")
    ctx = admin_ctx(client)
    await admin.bloquear_text.callback(admin, ctx, word)  # type: ignore[union-attr]
    progress = ctx.send.return_value
    if progress.edit.await_count:
        return progress.edit.await_args.kwargs["content"]
    return ctx.send.await_args.args[0]


def click(client: BotClient) -> MagicMock:
    interaction = fake_interaction(MagicMock(id=ADMIN_ID))
    interaction.client = client
    interaction.guild_id = GUILD_ID
    return interaction


async def test_bloquear_para_texto_slash_y_botones_y_sobrevive_al_reinicio(
    tmp_path: Path,
) -> None:
    client = await load_bot(tmp_path)
    try:
        assert "bloqueado" in await bloquear(client, "minas")
        admin = client.get_cog("Admin")
        assert admin.blocked[GUILD_ID] == {"minas"}  # type: ignore[union-attr]

        # `.minas`: el control global lanza y `Errors` lo cuenta en claro.
        ctx = MagicMock(guild=SimpleNamespace(id=GUILD_ID), command=client.get_command("minas"))
        with pytest.raises(commands.DisabledCommand) as raised:
            await client.can_run(ctx)
        assert describe_command_error(raised.value) == BLOCKED_ERROR
        ctx.command = client.get_command("blackjack")
        assert await client.can_run(ctx)

        # `/minas`: el árbol contesta en privado y no lo ejecuta.
        slash = click(client)
        slash.type = discord.InteractionType.application_command
        slash.data = {"name": "minas"}
        assert not await client.tree.interaction_check(slash)
        slash.response.send_message.assert_awaited_once_with(BLOCKED_ERROR, ephemeral=True)

        # Un botón de un tablero de Minas ya abierto no hace nada.
        mines = client.get_cog("Minas")
        board_class = sys.modules[type(mines).__module__].MinesBoard  # el que cargó el bot
        board = board_class(
            mines, guild_id=GUILD_ID, owner=MagicMock(id=ADMIN_ID), stake=100, mines=2
        )
        board.rebuild()
        button = next(i for i in board.walk_children() if isinstance(i, ui.Button))
        button.callback = AsyncMock()  # type: ignore[method-assign]
        pressed = click(client)
        task = board._dispatch_item(button, pressed)
        assert task is not None
        await task
        button.callback.assert_not_awaited()
        pressed.response.send_message.assert_awaited_once_with(BLOCKED_ERROR, ephemeral=True)

        # Tras reiniciar, sigue bloqueado: se lee de la base.
        await client.reload_extension("bot.cogs.admin")
        assert client.get_cog("Admin").blocked[GUILD_ID] == {"minas"}  # type: ignore[union-attr]

        assert "vuelve a funcionar" in await bloquear(client, "minas")
        assert await client.can_run(ctx)
        board.stop()
    finally:
        await client.close()


async def test_siguen_los_botones_de_otros_archivos_y_se_paran_formularios_y_dinamicos(
    tmp_path: Path,
) -> None:
    client = await load_bot(tmp_path)
    try:
        await bloquear(client, "minas")

        view = ui.View()
        button: ui.Button = ui.Button(label="otro")
        button.callback = AsyncMock()  # type: ignore[method-assign]
        view.add_item(button)
        task = view._dispatch_item(button, click(client))
        assert task is not None
        await task
        button.callback.assert_awaited_once()
        view.stop()

        # Un formulario del archivo de Minas tampoco se envía.
        form_class = type("Form", (ui.Modal,), {"__module__": "bot.cogs.mines"}, title="x")
        form = form_class()
        form.on_submit = AsyncMock()  # type: ignore[method-assign]
        submitted = click(client)
        await form._scheduled_task(submitted, [], {})
        form.on_submit.assert_not_awaited()
        submitted.response.send_message.assert_awaited_once_with(BLOCKED_ERROR, ephemeral=True)

        # Ni un botón dinámico (los del Rastro o `lista`) de ese archivo.
        dynamic = type("Dynamic", (), {"__module__": "bot.cogs.mines"})
        dynamic.from_custom_id = AsyncMock()  # type: ignore[attr-defined]
        pressed = click(client)
        store = client._connection._view_store
        await store.schedule_dynamic_item_call(2, dynamic, pressed, "x", MagicMock())
        dynamic.from_custom_id.assert_not_awaited()  # type: ignore[attr-defined]
        pressed.response.send_message.assert_awaited_once_with(BLOCKED_ERROR, ephemeral=True)
    finally:
        await client.close()


async def test_bloquear_entiende_atajos_y_no_bloquea_lo_que_no_debe(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        assert "`blackjack` bloqueado" in await bloquear(client, ".bj")
        assert "no se puede bloquear" in await bloquear(client, "bloquear")
        assert "No hay ningún comando `nada`" in await bloquear(client, "nada")
        assert "`blackjack`" in await bloquear(client, "")
    finally:
        await client.close()
