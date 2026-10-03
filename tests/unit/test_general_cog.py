"""Pruebas de bot.cogs.general: `/ping` y el comando de ayuda (`/help`)."""

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
    """Crea un `commands.Context` de prueba para invocar comandos de texto (`....`)."""
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
    """`.ping` comparte la lógica de `/ping` a través de `ContextResponder`."""
    fake_bot = MagicMock()
    fake_bot.latency = 0.05

    cog = General(fake_bot)
    ctx = make_context()

    await cog.ping_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    message = ctx.send.await_args.args[0]
    assert "50 ms" in message


@pytest.mark.asyncio
async def test_help_responde_con_un_embed_de_forma_efimera() -> None:
    """`/help` construye y envía el embed de ayuda de forma efímera."""
    real_bot = commands.Bot(command_prefix=".", intents=discord.Intents.none(), help_command=None)
    cog = General(real_bot)
    await real_bot.add_cog(cog)
    interaction = make_interaction()

    await cog.help_command.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"] is not None


@pytest.mark.asyncio
async def test_help_text_se_llama_igual_que_el_slash_y_sin_alias() -> None:
    """`.help` usa el mismo nombre que `/help`, sin alias en otro idioma."""
    assert General.help_command_text.name == "help"
    assert not General.help_command_text.aliases

    real_bot = commands.Bot(command_prefix=".", intents=discord.Intents.none(), help_command=None)
    cog = General(real_bot)
    await real_bot.add_cog(cog)
    ctx = make_context()

    await cog.help_command_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.kwargs["embed"] is not None


async def make_bot_with_commands() -> commands.Bot:
    """Bot real con un comando slash y su gemelo de texto, en cogs distintos."""
    bot = commands.Bot(command_prefix=".", intents=discord.Intents.none(), help_command=None)
    await bot.add_cog(General(bot))
    return bot


@pytest.mark.asyncio
async def test_build_help_embed_muestra_cada_comando_una_sola_vez_sin_prefijos() -> None:
    """Como `/x` y `.x` son iguales, la ayuda lista cada nombre una sola vez."""
    bot = await make_bot_with_commands()

    embed = build_help_embed(bot)

    text = "\n".join(field.value for field in embed.fields)
    assert text.count("**ping**") == 1
    assert text.count("**help**") == 1
    assert "/ping" not in text
    assert ".ping" not in text
    assert "alias" not in text.lower()


@pytest.mark.asyncio
async def test_build_help_embed_agrupa_por_categoria_con_descripcion_corta() -> None:
    """La ayuda agrupa por categoría y cada línea es nombre + descripción breve."""
    bot = await make_bot_with_commands()

    embed = build_help_embed(bot)

    assert [field.name for field in embed.fields] == ["⚙️ General"]
    lines = embed.fields[0].value.splitlines()
    assert len(lines) == 2
    assert all(len(line) <= 80 for line in lines)


def test_chunk_names_parte_los_nombres_sin_pasar_del_limite_de_un_campo() -> None:
    """Una lista larga de nombres se reparte en varios campos de como mucho 1024 caracteres."""
    from bot.cogs.general import FIELD_LIMIT, _chunk_names

    names = [f"efecto{i:03d}" for i in range(200)]

    chunks = _chunk_names(names)

    assert len(chunks) > 1
    assert all(len(chunk) <= FIELD_LIMIT for chunk in chunks)
    assert " · ".join(chunks).split(" · ") == names
