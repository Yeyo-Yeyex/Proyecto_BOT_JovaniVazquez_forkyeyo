"""Pruebas de bot.cogs.general: `/latencia` y el comando de ayuda (`/ayuda`)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands
from interaction_fakes import fake_interaction

from bot.cogs.general import ADMIN_CATEGORY, HOME, General, HelpView, build_help_pages


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
    """El comando /latencia debe responder una sola vez, en efímero, con la latencia del bot."""
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
    """`.latencia` comparte la lógica de `/latencia` a través de `ContextResponder`."""
    fake_bot = MagicMock()
    fake_bot.latency = 0.05

    cog = General(fake_bot)
    ctx = make_context()

    await cog.ping_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    message = ctx.send.await_args.args[0]
    assert "50 ms" in message


@pytest.mark.asyncio
async def test_help_responde_con_el_menu_de_forma_efimera() -> None:
    """`/ayuda` envía la portada con el menú de categorías, solo para quien la pide."""
    real_bot = commands.Bot(command_prefix=".", intents=discord.Intents.none(), help_command=None)
    cog = General(real_bot)
    await real_bot.add_cog(cog)
    interaction = make_interaction()
    interaction.user = SimpleNamespace(id=7)
    interaction.original_response = AsyncMock()

    await cog.help_command.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].title == "📖 Comandos"
    view = kwargs["view"]
    assert isinstance(view, HelpView)
    assert view.owner_id == 7
    assert view.message is interaction.original_response.return_value


@pytest.mark.asyncio
async def test_help_text_se_llama_igual_que_el_slash_y_sin_alias() -> None:
    """`.ayuda` usa el mismo nombre que `/ayuda`, sin alias en otro idioma."""
    assert General.help_command_text.name == "ayuda"
    assert not General.help_command_text.aliases

    real_bot = commands.Bot(command_prefix=".", intents=discord.Intents.none(), help_command=None)
    cog = General(real_bot)
    await real_bot.add_cog(cog)
    ctx = make_context()
    ctx.author = SimpleNamespace(id=7)

    await cog.help_command_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.kwargs["embed"].title == "📖 Comandos"
    assert isinstance(ctx.send.await_args.kwargs["view"], HelpView)


async def make_bot_with_commands() -> commands.Bot:
    """Bot real con los comandos de `General` (slash y texto con el mismo nombre)."""
    bot = commands.Bot(command_prefix=".", intents=discord.Intents.none(), help_command=None)
    await bot.add_cog(General(bot))
    return bot


@pytest.mark.asyncio
async def test_build_help_pages_portada_y_una_pagina_por_categoria() -> None:
    """La portada resume las categorías y cada página lista sus comandos con descripción."""
    bot = await make_bot_with_commands()

    pages = build_help_pages(bot)

    assert [page.title for page in pages] == [HOME, "⚙️ General"]
    assert "⚙️ General · 2 comandos" in pages[0].embed.description
    general = pages[1].embed.description.splitlines()
    assert general == [
        "`ayuda` — Todos los comandos, por categorías.",
        "`latencia` — Comprueba que el bot responde.",
    ]


class FakeAdminCog(commands.Cog, name="Admin"):
    """Cog mínimo con el nombre del de administración, para probar la ayuda."""

    @commands.command(name="echar")
    async def kick(self, ctx: commands.Context) -> None:
        """Comando de prueba."""


@pytest.mark.asyncio
async def test_build_help_pages_solo_ensena_admin_a_administradores() -> None:
    """La página Admin aparece con `include_admin=True` y al final."""
    bot = await make_bot_with_commands()
    await bot.add_cog(FakeAdminCog())

    normal = build_help_pages(bot)
    admin = build_help_pages(bot, include_admin=True)

    assert all(page.title != ADMIN_CATEGORY for page in normal)
    assert admin[-1].title == ADMIN_CATEGORY
    assert admin[-1].embed.description == "`echar` — Comando de prueba."


@pytest.mark.asyncio
async def test_help_de_un_administrador_incluye_la_categoria_admin() -> None:
    """`/ayuda` decide si enseña la categoría Admin según los permisos de quien la pide."""
    bot = await make_bot_with_commands()
    await bot.add_cog(FakeAdminCog())
    cog = bot.get_cog("General")
    interaction = make_interaction()
    interaction.original_response = AsyncMock()
    admin = MagicMock(spec=discord.Member)
    admin.guild_permissions = discord.Permissions(administrator=True)
    interaction.user = admin

    await cog.help_command.callback(cog, interaction)

    view = interaction.response.send_message.call_args.kwargs["view"]
    assert list(view.pages)[-1] == ADMIN_CATEGORY


@pytest.mark.asyncio
async def test_el_menu_cambia_de_pagina_en_el_mismo_mensaje() -> None:
    """Elegir una categoría edita el mensaje con su página y la marca en el menú."""
    bot = await make_bot_with_commands()
    view = HelpView(build_help_pages(bot), owner_id=7)
    interaction = fake_interaction(SimpleNamespace(id=7))

    await view.show(interaction, "⚙️ General")

    kwargs = interaction.response.edit_message.await_args.kwargs
    assert kwargs["embed"].title == "⚙️ General"
    select = view.children[0]
    assert [o.value for o in select.options if o.default] == ["⚙️ General"]


@pytest.mark.asyncio
async def test_el_menu_solo_lo_maneja_quien_lo_abrio() -> None:
    """Otro miembro recibe un aviso privado y la página no cambia."""
    bot = await make_bot_with_commands()
    view = HelpView(build_help_pages(bot), owner_id=7)
    interaction = fake_interaction(SimpleNamespace(id=8))

    assert await view.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once()
    assert view.key == HOME


def test_chunk_names_parte_los_nombres_sin_pasar_del_limite_de_un_campo() -> None:
    """Una lista larga de nombres se reparte en varios campos de como mucho 1024 caracteres."""
    from bot.cogs.general import FIELD_LIMIT, _chunk_names

    names = [f"efecto{i:03d}" for i in range(200)]

    chunks = _chunk_names(names)

    assert len(chunks) > 1
    assert all(len(chunk) <= FIELD_LIMIT for chunk in chunks)
    assert " · ".join(chunks).split(" · ") == names
