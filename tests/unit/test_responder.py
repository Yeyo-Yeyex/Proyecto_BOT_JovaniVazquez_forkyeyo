"""Pruebas de bot.utils.responder: envío de archivos con `finish`."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.utils.responder import ContextResponder, InteractionResponder


def make_file() -> discord.File:
    """Archivo de prueba en memoria."""
    return discord.File(io.BytesIO(b"datos"), filename="prueba.png")


@pytest.mark.asyncio
async def test_interaction_finish_adjunta_el_archivo_a_la_respuesta_original() -> None:
    """`/comando`: el archivo sustituye al aviso de progreso vía `attachments`."""
    interaction = MagicMock()
    interaction.guild = SimpleNamespace(id=1)
    interaction.edit_original_response = AsyncMock()
    file = make_file()

    await InteractionResponder(interaction).finish(file=file)

    kwargs = interaction.edit_original_response.await_args.kwargs
    assert kwargs["attachments"] == [file]


@pytest.mark.asyncio
async def test_interaction_finish_sin_archivo_no_toca_los_adjuntos() -> None:
    """Sin `file`, no se envía `attachments` (no se borran adjuntos existentes)."""
    interaction = MagicMock()
    interaction.edit_original_response = AsyncMock()

    await InteractionResponder(interaction).finish("hola")

    assert "attachments" not in interaction.edit_original_response.await_args.kwargs


@pytest.mark.asyncio
async def test_context_finish_con_archivo_envia_mensaje_nuevo_y_borra_el_progreso() -> None:
    """`.comando`: el archivo va en un mensaje nuevo y el aviso de progreso se borra."""
    progress = MagicMock()
    progress.delete = AsyncMock()
    ctx = MagicMock()
    ctx.send = AsyncMock(side_effect=[progress, MagicMock()])
    responder = ContextResponder(ctx)
    file = make_file()

    await responder.start_progress("⏳")
    await responder.finish(file=file)

    assert ctx.send.await_args_list[1].kwargs["file"] is file
    progress.delete.assert_awaited_once()
    progress.edit.assert_not_called()


@pytest.mark.asyncio
async def test_context_finish_con_archivo_tolera_no_poder_borrar_el_progreso() -> None:
    """Si no se puede borrar el aviso (sin permiso), el resultado ya enviado no falla."""
    progress = MagicMock()
    progress.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "sin permiso"))
    ctx = MagicMock()
    ctx.send = AsyncMock(side_effect=[progress, MagicMock()])
    responder = ContextResponder(ctx)

    await responder.start_progress("⏳")
    await responder.finish(file=make_file())

    progress.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_finish_sin_archivo_sigue_editando_el_progreso() -> None:
    """Sin archivo, el resultado sustituye al aviso editándolo (comportamiento previo)."""
    progress = MagicMock()
    progress.edit = AsyncMock()
    ctx = MagicMock()
    ctx.send = AsyncMock(return_value=progress)
    responder = ContextResponder(ctx)

    await responder.start_progress("⏳")
    await responder.finish("listo")

    assert progress.edit.await_args.kwargs["content"] == "listo"


@pytest.mark.asyncio
async def test_context_finish_sin_progreso_envia_el_archivo_como_mensaje_nuevo() -> None:
    """Sin aviso previo, el archivo se envía directamente."""
    ctx = MagicMock()
    ctx.send = AsyncMock()
    file = make_file()

    await ContextResponder(ctx).finish(file=file)

    assert ctx.send.await_args.kwargs["file"] is file
