"""Pruebas de la sección de nivel de `perfil` y de `/ranking` (también `.ranking`)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.message_stats import MessageStats
from bot.services.levels import MemberActivity, local_day


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
async def test_seccion_de_nivel_ensena_nivel_xp_y_racha() -> None:
    """La sección 📊 Nivel de `perfil` lee XP y racha del miembro indicado."""
    today = local_day(time.time()).isoformat()
    repository = make_seeded_repository(
        member_activity=AsyncMock(
            return_value=MemberActivity(total_xp=250, last_active_day=today, streak_days=4)
        )
    )
    cog = MessageStats(MagicMock(), repository)
    otro = make_member("Otra persona", member_id=77)

    embed = await cog.level_embed(SimpleNamespace(id=1), otro)

    repository.member_activity.assert_awaited_once_with(1, 77)
    assert "Otra persona" in embed.title
    assert "Nivel 1" in embed.description
    assert "50/200 XP" in embed.description
    assert "**4** días seguidos" in embed.fields[0].value


@pytest.mark.asyncio
async def test_seccion_de_nivel_avisa_si_los_niveles_no_estan_inicializados() -> None:
    """Sin historial de niveles, la sección lo dice y explica cómo encenderlos."""
    repository = make_seeded_repository()
    repository.level_settings = AsyncMock(return_value=None)
    cog = MessageStats(MagicMock(), repository)

    embed = await cog.level_embed(SimpleNamespace(id=1), make_member())

    assert "apagados" in embed.description
    assert "/niveles" in embed.description


@pytest.mark.asyncio
async def test_ranking_text_valida_el_rango_de_pagina_antes_de_consultar() -> None:
    """`.ranking` rechaza páginas fuera de rango sin llegar a consultar el repositorio."""
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
    """`.ranking` muestra un aviso de progreso y lo sustituye por el embed final."""
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
    """`/ranking` diferido resuelve nombres y edita la respuesta original con el embed."""
    repository = make_seeded_repository()
    cog = MessageStats(MagicMock(), repository)
    cog._resolve_display_name = AsyncMock(side_effect=["Primero", "Segundo"])
    interaction = make_interaction(member=make_member())

    await cog.ranking.callback(cog, interaction, 1)

    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_awaited_once()
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert embed.title == "🏆 Ranking de niveles"
