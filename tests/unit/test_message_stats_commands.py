"""Pruebas de `/level`, `/top` y sus equivalentes de texto (`.level`, `.top`)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.message_stats import MessageStats


def make_seeded_repository(**overrides: object) -> MagicMock:
    """Crea un repositorio de prueba con los niveles ya inicializados."""
    repository = MagicMock()
    repository.level_settings = AsyncMock(return_value=SimpleNamespace(historical_seeded=True))
    repository.member_xp = AsyncMock(return_value=250)
    repository.level_leaderboard = AsyncMock(return_value=[(10, 250), (20, 90)])
    for name, value in overrides.items():
        setattr(repository, name, value)
    return repository


def make_interaction(*, guild_id: int = 1, member: object | None = None) -> MagicMock:
    """Crea una interacción de prueba con respuesta, defer y edición simuladas."""
    interaction = MagicMock()
    interaction.guild = SimpleNamespace(id=guild_id, name="Servidor de prueba", icon=None)
    interaction.user = member
    interaction.channel = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


def make_context(*, guild_id: int = 1, member: object | None = None) -> MagicMock:
    """Crea un `commands.Context` de prueba para invocar comandos de texto (`....`)."""
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=guild_id, name="Servidor de prueba", icon=None)
    ctx.author = member
    ctx.channel = MagicMock()
    progress_message = MagicMock()
    progress_message.edit = AsyncMock()
    ctx.send = AsyncMock(return_value=progress_message)
    return ctx


def make_member(display_name: str = "Miembro de prueba", member_id: int = 99) -> MagicMock:
    """Crea un `discord.Member` de prueba con un nombre e id concretos."""
    member = MagicMock(spec=discord.Member)
    member.display_name = display_name
    member.id = member_id
    return member


@pytest.mark.asyncio
async def test_nivel_text_usa_al_autor_cuando_no_se_indica_miembro() -> None:
    """`.level` sin argumentos consulta el nivel de quien lo invoca."""
    repository = make_seeded_repository()
    cog = MessageStats(MagicMock(), repository)
    autor = make_member("Autor del mensaje", member_id=42)
    ctx = make_context(member=autor)

    await cog.level_text.callback(cog, ctx, None)

    repository.member_xp.assert_awaited_once_with(1, 42)
    ctx.send.assert_awaited_once()
    message = ctx.send.await_args.args[0]
    assert "Autor del mensaje" in message


@pytest.mark.asyncio
async def test_nivel_text_admite_consultar_a_otro_miembro() -> None:
    """`.level @otro` consulta el nivel del miembro indicado, no del autor."""
    repository = make_seeded_repository()
    cog = MessageStats(MagicMock(), repository)
    autor = make_member("Autor", member_id=1)
    otro = make_member("Otra persona", member_id=77)
    ctx = make_context(member=autor)

    await cog.level_text.callback(cog, ctx, otro)

    repository.member_xp.assert_awaited_once_with(1, 77)
    message = ctx.send.await_args.args[0]
    assert "Otra persona" in message


@pytest.mark.asyncio
async def test_nivel_avisa_si_los_niveles_no_estan_inicializados() -> None:
    """`/level` informa con claridad cuando el servidor aún no tiene historial de niveles."""
    repository = make_seeded_repository()
    repository.level_settings = AsyncMock(return_value=None)
    cog = MessageStats(MagicMock(), repository)
    interaction = make_interaction(member=make_member())

    await cog.level.callback(cog, interaction, None)

    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "no están inicializados" in message


@pytest.mark.asyncio
async def test_ranking_text_valida_el_rango_de_pagina_antes_de_consultar() -> None:
    """`.top` rechaza páginas fuera de rango sin llegar a consultar el repositorio."""
    repository = make_seeded_repository()
    cog = MessageStats(MagicMock(), repository)
    ctx = make_context(member=make_member())

    await cog.ranking_text.callback(cog, ctx, 999)

    repository.level_leaderboard.assert_not_awaited()
    ctx.send.assert_awaited_once()
    message = ctx.send.await_args.args[0]
    assert "entre 1 y 100" in message


@pytest.mark.asyncio
async def test_ranking_text_edita_el_mensaje_de_progreso_con_el_embed_final() -> None:
    """`.top` muestra un aviso de progreso y lo sustituye por el embed final."""
    repository = make_seeded_repository()
    cog = MessageStats(MagicMock(), repository)
    cog._resolve_display_name = AsyncMock(side_effect=["Primero", "Segundo"])
    ctx = make_context(member=make_member())

    await cog.ranking_text.callback(cog, ctx, 1)

    ctx.send.assert_awaited_once()
    progress_message = ctx.send.await_args_list[0].args[0]
    assert progress_message
    sent_message = ctx.send.return_value
    sent_message.edit.assert_awaited_once()
    embed = sent_message.edit.await_args.kwargs["embed"]
    assert embed.title == "🏆 Ranking de niveles"


@pytest.mark.asyncio
async def test_ranking_edita_la_respuesta_diferida_con_el_embed_final() -> None:
    """`/top` diferido resuelve nombres y edita la respuesta original con el embed."""
    repository = make_seeded_repository()
    cog = MessageStats(MagicMock(), repository)
    cog._resolve_display_name = AsyncMock(side_effect=["Primero", "Segundo"])
    interaction = make_interaction(member=make_member())

    await cog.ranking.callback(cog, interaction, 1)

    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_awaited_once()
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert embed.title == "🏆 Ranking de niveles"
