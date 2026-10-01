"""Pruebas de bot.cogs.errors: mensajes claros en vez de fallos silenciosos."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from bot.cogs.errors import (
    GENERIC_ERROR,
    GUILD_ONLY_ERROR,
    Errors,
    describe_app_command_error,
    describe_command_error,
)


def test_un_mensaje_que_no_es_un_comando_no_recibe_respuesta() -> None:
    """Escribir `ºloquesea` sin que exista el comando no provoca ninguna respuesta."""
    assert describe_command_error(commands.CommandNotFound()) is None


def test_falta_un_argumento_se_indica_cual() -> None:
    """Se nombra el argumento que falta."""
    param = MagicMock()
    param.name = "consulta"

    message = describe_command_error(commands.MissingRequiredArgument(param))

    assert "consulta" in message


def test_un_argumento_invalido_da_un_mensaje_generico_de_uso() -> None:
    """Un argumento mal escrito remite a la ayuda."""
    assert "help" in describe_command_error(commands.BadArgument("x"))


def test_fuera_de_un_servidor_se_avisa() -> None:
    """Los comandos solo de servidor lo explican."""
    assert describe_command_error(commands.NoPrivateMessage()) == GUILD_ONLY_ERROR
    assert describe_app_command_error(app_commands.NoPrivateMessage()) == GUILD_ONLY_ERROR


def test_un_fallo_inesperado_no_revela_detalles_internos() -> None:
    """Los errores internos dan un mensaje genérico, sin traza ni texto de la excepción."""
    error = commands.CommandInvokeError(RuntimeError("secreto interno"))

    message = describe_command_error(error)

    assert message == GENERIC_ERROR
    assert "secreto" not in message


@pytest.mark.asyncio
async def test_on_command_error_responde_en_el_canal() -> None:
    """El listener envía el mensaje al canal donde se escribió el comando."""
    cog = Errors(MagicMock())
    ctx = MagicMock()
    ctx.send = AsyncMock()
    param = MagicMock()
    param.name = "valor"

    await cog.on_command_error(ctx, commands.MissingRequiredArgument(param))

    assert "valor" in ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_on_command_error_no_responde_si_el_comando_no_existe() -> None:
    """Un comando inexistente se ignora en silencio."""
    cog = Errors(MagicMock())
    ctx = MagicMock()
    ctx.send = AsyncMock()

    await cog.on_command_error(ctx, commands.CommandNotFound())

    ctx.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_command_error_tolera_no_poder_enviar_el_aviso() -> None:
    """Si ni siquiera se puede responder, el gestor no lanza otra excepción."""
    cog = Errors(MagicMock())
    ctx = MagicMock()
    ctx.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "sin permiso"))

    await cog.on_command_error(ctx, commands.BadArgument("x"))


@pytest.mark.asyncio
async def test_on_app_command_error_responde_de_forma_efimera() -> None:
    """Los errores de `/` se comunican en privado a quien los provocó."""
    cog = Errors(MagicMock())
    interaction = MagicMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()

    await cog.on_app_command_error(
        interaction, app_commands.CommandInvokeError(MagicMock(), RuntimeError("x"))
    )

    args, kwargs = interaction.response.send_message.await_args
    assert args[0] == GENERIC_ERROR
    assert kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_on_app_command_error_usa_followup_si_ya_se_respondio() -> None:
    """Si la interacción ya estaba diferida, el aviso va como mensaje de seguimiento."""
    cog = Errors(MagicMock())
    interaction = MagicMock()
    interaction.response.is_done = MagicMock(return_value=True)
    interaction.followup.send = AsyncMock()

    await cog.on_app_command_error(
        interaction, app_commands.CommandInvokeError(MagicMock(), RuntimeError("x"))
    )

    interaction.followup.send.assert_awaited_once()
