"""Pruebas para resolver nombres legibles de miembros en el ranking."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.message_stats import MessageStats, build_ranking_embed


@pytest.mark.asyncio
async def test_level_up_announcement_uses_message_channel() -> None:
    """El aviso de nivel se publica en el canal recibido del evento de mensaje."""
    channel = MagicMock()
    channel.send = AsyncMock()
    member = SimpleNamespace(id=200, display_name="Nombre Visible")
    guild = SimpleNamespace(id=100)
    cog = MessageStats(MagicMock(), MagicMock())

    await cog._announce_level_up(guild, channel, member, level=2)

    channel.send.assert_awaited_once()
    args, kwargs = channel.send.await_args
    assert args == ("¡Nombre Visible ha alcanzado el nivel **2**!",)
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


@pytest.mark.asyncio
async def test_on_message_pasa_el_canal_que_otorgo_el_nivel() -> None:
    """El evento de mensaje envía al anuncio el canal exacto que concedió XP."""
    repository = MagicMock()
    repository.grant_activity = AsyncMock(
        return_value={
            200: SimpleNamespace(
                previous_xp=90, total_xp=110, streak_days=1, announce_channel_id=None
            )
        }
    )
    cog = MessageStats(MagicMock(), repository)
    cog._announce_level_up = AsyncMock()
    guild = SimpleNamespace(id=100)
    channel = MagicMock()
    member = SimpleNamespace(id=200, bot=False, display_name="Nombre")
    message = SimpleNamespace(
        guild=guild,
        channel=channel,
        author=member,
        webhook_id=None,
        is_system=lambda: False,
    )

    await cog.on_message(message)

    cog._announce_level_up.assert_awaited_once_with(guild, channel, member, 1, reward=None)


@pytest.mark.asyncio
async def test_resolve_display_name_consulta_miembros_que_no_estan_en_cache() -> None:
    """Un miembro fuera de caché se resuelve por API en lugar de mostrar su ID."""
    bot = MagicMock()
    bot.fetch_user = AsyncMock()
    repository = MagicMock()
    cog = MessageStats(bot, repository)
    guild = MagicMock()
    guild.id = 100
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=SimpleNamespace(display_name="Nombre Visible"))

    display_name = await cog._resolve_display_name(guild, user_id=200)

    assert display_name == "Nombre Visible"
    guild.fetch_member.assert_awaited_once_with(200)
    bot.fetch_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_display_name_usa_nombre_de_cache_sin_consulta_api() -> None:
    """Los miembros en caché usan su apodo actual y evitan llamadas de red."""
    bot = MagicMock()
    bot.fetch_user = AsyncMock()
    cog = MessageStats(bot, MagicMock())
    guild = MagicMock()
    guild.get_member.return_value = SimpleNamespace(display_name="Apodo del servidor")
    guild.fetch_member = AsyncMock()

    display_name = await cog._resolve_display_name(guild, user_id=200)

    assert display_name == "Apodo del servidor"
    guild.fetch_member.assert_not_awaited()
    bot.fetch_user.assert_not_awaited()


def test_build_ranking_embed_presenta_podio_progreso_y_pagina() -> None:
    """El ranking muestra nombres legibles, medallas y una clasificación visual."""
    guild = SimpleNamespace(name="Servidor de prueba", icon=None)
    embed = build_ranking_embed(
        guild,
        page=2,
        entries=[
            (11, "Primera persona", 250),
            (12, "Segunda persona", 180),
            (13, "Tercera persona", 90),
            (14, "Cuarta persona", 40),
        ],
    )

    assert embed.title == "🏆 Ranking de niveles"
    assert embed.author.name == "Servidor de prueba"
    assert [field.name for field in embed.fields[:3]] == [
        "🥇 Primera persona",
        "🥈 Segunda persona",
        "🥉 Tercera persona",
    ]
    assert "`███████░░░`" in embed.fields[0].value
    assert embed.fields[3].name == "Clasificación"
    assert "Cuarta persona" in embed.fields[3].value
    assert embed.footer.text.startswith("Página 2")
    assert "Usuario 14" not in str(embed.to_dict())
