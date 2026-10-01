"""Pruebas de bot.cogs.general: `/ping` y el comando de ayuda (`/ayuda`)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from bot.cogs.general import General, build_help_embed


def make_interaction() -> MagicMock:
    """Crea una interacción de prueba con respuesta simulada."""
    interaction = MagicMock()
    interaction.guild = SimpleNamespace(id=1)
    interaction.user = None
    interaction.channel = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    return interaction


def make_context() -> MagicMock:
    """Crea un `commands.Context` de prueba para invocar comandos de texto (`º...`)."""
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=1)
    ctx.author = None
    ctx.channel = MagicMock()
    ctx.send = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_ping_responde_de_forma_efimera_con_la_latencia() -> None:
    """El comando /ping debe responder una sola vez, en efímero, con la latencia del bot."""
    fake_bot = MagicMock()
    fake_bot.latency = 0.123  # segundos -> se espera 123 ms redondeados

    cog = General(fake_bot)

    interaction = make_interaction()

    await cog.ping.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    assert kwargs["ephemeral"] is True
    message = interaction.response.send_message.call_args.kwargs["content"]
    assert "123 ms" in message


@pytest.mark.asyncio
async def test_ping_text_responde_con_la_misma_latencia_que_la_version_slash() -> None:
    """`ºping` comparte la lógica de `/ping` a través de `ContextResponder`."""
    fake_bot = MagicMock()
    fake_bot.latency = 0.05

    cog = General(fake_bot)
    ctx = make_context()

    await cog.ping_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    message = ctx.send.await_args.args[0]
    assert "50 ms" in message


@pytest.mark.asyncio
async def test_ayuda_responde_con_un_embed_de_forma_efimera() -> None:
    """`/ayuda` construye y envía el embed de ayuda de forma efímera."""
    real_bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    cog = General(real_bot)
    await real_bot.add_cog(cog)
    interaction = make_interaction()

    await cog.help_command.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"] is not None


@pytest.mark.asyncio
async def test_ayuda_text_usa_el_alias_help() -> None:
    """`ºhelp` es un alias de `ºayuda` y también envía el embed."""
    assert "help" in General.help_command_text.aliases

    real_bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    cog = General(real_bot)
    await real_bot.add_cog(cog)
    ctx = make_context()

    await cog.help_command_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.kwargs["embed"] is not None


def test_build_help_embed_lista_comandos_de_aplicacion_y_de_texto() -> None:
    """El embed de ayuda incluye tanto comandos slash como comandos de texto."""
    real_bot = commands.Bot(
        command_prefix=("!", "º"),
        intents=discord.Intents.none(),
        help_command=None,
    )

    @real_bot.command(name="ejemplo")
    async def _ejemplo(ctx: commands.Context) -> None:
        """Comando de texto de ejemplo."""

    embed = build_help_embed(real_bot)

    assert "º" in embed.description
    assert any("ejemplo" in (field.value or "") for field in embed.fields)
