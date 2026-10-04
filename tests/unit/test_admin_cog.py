"""Pruebas de bot.cogs.admin: autorización y efectos de los comandos."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from bot.cogs.admin import BOT_FORBIDDEN, SAY_MENTIONS, Admin
from bot.cogs.welcome import Welcome
from bot.repositories.welcome import WelcomeRepository, WelcomeSettings


def make_member(member_id: int, position: int, *, admin: bool = False) -> MagicMock:
    """Miembro de prueba con rol de la posición indicada."""
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.top_role = SimpleNamespace(position=position)
    member.guild_permissions = discord.Permissions(administrator=admin)
    member.mention = f"<@{member_id}>"
    member.display_name = f"m{member_id}"
    member.__str__.return_value = f"m{member_id}"
    member.kick = AsyncMock()
    member.ban = AsyncMock()
    member.timeout = AsyncMock()
    member.edit = AsyncMock()
    return member


@pytest.fixture
def guild() -> SimpleNamespace:
    """Servidor con dueño 1 y el bot (id 3) con un rol alto."""
    return SimpleNamespace(id=10, owner_id=1, me=make_member(3, 10))


def make_ctx(guild: SimpleNamespace, author: MagicMock) -> MagicMock:
    """Contexto de texto de prueba."""
    ctx = MagicMock()
    ctx.guild = guild
    ctx.author = author
    ctx.channel = MagicMock()
    ctx.send = AsyncMock()
    ctx.message.delete = AsyncMock()
    return ctx


def last_message(ctx: MagicMock) -> str:
    """Texto del último mensaje enviado por el bot en el contexto."""
    return ctx.send.await_args.args[0]


@pytest.mark.asyncio
async def test_cog_check_rechaza_a_quien_no_es_admin(guild: SimpleNamespace) -> None:
    """Un miembro sin Administrador recibe MissingPermissions."""
    cog = Admin(MagicMock())
    ctx = make_ctx(guild, make_member(2, 5, admin=False))

    with pytest.raises(commands.MissingPermissions):
        await cog.cog_check(ctx)


@pytest.mark.asyncio
async def test_cog_check_rechaza_mensajes_directos() -> None:
    """Fuera de un servidor no hay administradores."""
    cog = Admin(MagicMock())
    ctx = make_ctx(None, MagicMock())  # type: ignore[arg-type]
    ctx.guild = None

    with pytest.raises(commands.NoPrivateMessage):
        await cog.cog_check(ctx)


@pytest.mark.asyncio
async def test_cog_check_acepta_a_un_admin(guild: SimpleNamespace) -> None:
    """Con Administrador, el comando sigue."""
    cog = Admin(MagicMock())
    assert await cog.cog_check(make_ctx(guild, make_member(2, 5, admin=True)))


@pytest.mark.asyncio
async def test_interaction_check_rechaza_a_quien_no_es_admin(guild: SimpleNamespace) -> None:
    """La versión `/` comprueba lo mismo en el servidor, no solo en el menú."""
    cog = Admin(MagicMock())
    interaction = MagicMock()
    interaction.guild = guild
    interaction.user = make_member(2, 5, admin=False)

    with pytest.raises(app_commands.MissingPermissions):
        await cog.interaction_check(interaction)


@pytest.mark.asyncio
async def test_kick_expulsa_con_motivo_y_autor_en_la_auditoria(guild: SimpleNamespace) -> None:
    """El motivo del registro de auditoría incluye quién lo pidió."""
    cog = Admin(MagicMock())
    target = make_member(4, 1)
    target.guild = guild
    ctx = make_ctx(guild, make_member(2, 5, admin=True))

    await cog.kick_text.callback(cog, ctx, target, motivo="spam")

    target.kick.assert_awaited_once_with(reason="spam (por m2)")
    assert "expulsado" in last_message(ctx)


@pytest.mark.asyncio
async def test_kick_no_toca_a_alguien_con_rol_superior(guild: SimpleNamespace) -> None:
    """La jerarquía se comprueba antes de llamar a Discord."""
    cog = Admin(MagicMock())
    target = make_member(4, 7)
    target.guild = guild
    ctx = make_ctx(guild, make_member(2, 5, admin=True))

    await cog.kick_text.callback(cog, ctx, target)

    target.kick.assert_not_awaited()
    assert "tuyo" in last_message(ctx)


@pytest.mark.asyncio
async def test_ban_sin_permiso_del_bot_lo_explica(guild: SimpleNamespace) -> None:
    """Un 403 de Discord se convierte en un mensaje claro."""
    cog = Admin(MagicMock())
    target = make_member(4, 1)
    target.guild = guild
    target.ban.side_effect = discord.Forbidden(MagicMock(status=403), "no")
    ctx = make_ctx(guild, make_member(2, 5, admin=True))

    await cog.ban_text.callback(cog, ctx, target)

    assert last_message(ctx) == BOT_FORBIDDEN


@pytest.mark.asyncio
async def test_mute_aplica_la_duracion_interpretada(guild: SimpleNamespace) -> None:
    """`.mute @x 1h30m` aísla 90 minutos."""
    cog = Admin(MagicMock())
    target = make_member(4, 1)
    target.guild = guild
    ctx = make_ctx(guild, make_member(2, 5, admin=True))

    await cog.mute_text.callback(cog, ctx, target, "1h30m")

    assert target.timeout.await_args.args[0] == timedelta(minutes=90)
    assert "1h 30m" in last_message(ctx)


@pytest.mark.asyncio
async def test_mute_con_duracion_invalida_no_llama_a_discord(guild: SimpleNamespace) -> None:
    """Una duración mal escrita se explica sin aislar a nadie."""
    cog = Admin(MagicMock())
    target = make_member(4, 1)
    target.guild = guild
    ctx = make_ctx(guild, make_member(2, 5, admin=True))

    await cog.mute_text.callback(cog, ctx, target, "mucho")

    target.timeout.assert_not_awaited()
    assert "Duración" in last_message(ctx)


@pytest.mark.asyncio
async def test_purge_text_rechaza_cantidades_fuera_de_rango(guild: SimpleNamespace) -> None:
    """Más de 100 mensajes no se aceptan."""
    cog = Admin(MagicMock())
    ctx = make_ctx(guild, make_member(2, 5, admin=True))

    await cog.purge_text.callback(cog, ctx, 500)

    assert "entre 1 y 100" in last_message(ctx)


@pytest.mark.asyncio
async def test_purge_por_miembro_borra_solo_sus_mensajes_hasta_la_cantidad() -> None:
    """Con miembro, se revisan más mensajes pero solo se borran N suyos."""
    cog = Admin(MagicMock())
    target = make_member(4, 1)
    messages = [SimpleNamespace(author=SimpleNamespace(id=i % 2 + 4)) for i in range(10)]
    channel = MagicMock(spec=discord.TextChannel)

    async def fake_purge(*, limit, check, **_kwargs):
        return [m for m in messages[:limit] if check(m)]

    channel.purge = fake_purge

    count = await cog._purge(channel, 3, target, "r")

    assert count == 3


@pytest.mark.asyncio
async def test_say_text_no_permite_mencionar_a_everyone(guild: SimpleNamespace) -> None:
    """El bot repite el texto, pero sin pings masivos ni a roles."""
    cog = Admin(MagicMock())
    ctx = make_ctx(guild, make_member(2, 5, admin=True))

    await cog.say_text.callback(cog, ctx, texto="@everyone hola")

    ctx.message.delete.assert_awaited_once()
    assert ctx.send.await_args.kwargs["allowed_mentions"] is SAY_MENTIONS
    assert SAY_MENTIONS.everyone is False
    assert SAY_MENTIONS.roles is False


@pytest.mark.asyncio
async def test_lock_quita_escribir_a_everyone_y_unlock_lo_restaura(
    guild: SimpleNamespace,
) -> None:
    """`lock` pone `send_messages=False`; `unlock` borra la sobrescritura vacía."""
    cog = Admin(MagicMock())
    everyone = object()
    guild.default_role = everyone
    channel = MagicMock(spec=discord.TextChannel)
    overwrite = discord.PermissionOverwrite()
    channel.overwrites_for = MagicMock(side_effect=lambda _role: overwrite)
    channel.set_permissions = AsyncMock()
    ctx = make_ctx(guild, make_member(2, 5, admin=True))
    ctx.channel = channel

    await cog.lock_text.callback(cog, ctx)
    locked = channel.set_permissions.await_args.kwargs["overwrite"]
    assert locked.send_messages is False

    overwrite = locked
    await cog.unlock_text.callback(cog, ctx)
    assert channel.set_permissions.await_args.kwargs["overwrite"] is None
    assert "desbloqueado" in last_message(ctx)


@pytest.mark.asyncio
async def test_role_alterna_y_respeta_la_jerarquia(guild: SimpleNamespace) -> None:
    """Da el rol si no lo tiene y no deja dar uno superior al del admin."""
    cog = Admin(MagicMock())
    ctx = make_ctx(guild, make_member(2, 5, admin=True))
    target = make_member(4, 1)
    target.guild = guild
    target.roles = []
    target.add_roles = AsyncMock()

    low = MagicMock(spec=discord.Role)
    low.is_default.return_value = False
    low.managed = False
    low.__ge__ = lambda _self, _other: False
    low.mention = "@bajo"

    await cog.role_text.callback(cog, ctx, target, rol=low)
    target.add_roles.assert_awaited_once()

    high = MagicMock(spec=discord.Role)
    high.is_default.return_value = False
    high.managed = False
    high.__ge__ = lambda _self, _other: True
    target.add_roles.reset_mock()
    await cog.role_text.callback(cog, ctx, target, rol=high)
    target.add_roles.assert_not_awaited()
    assert "tuyo" in last_message(ctx)


# -- bienv ------------------------------------------------------------------------


async def make_bienv(tmp_path: Path, guild: SimpleNamespace) -> tuple[Admin, WelcomeRepository]:
    """Admin con la bienvenida real (repositorio en disco) y #chat-general."""
    repository = WelcomeRepository(tmp_path / "bot.db")
    await repository.initialize()
    chat = MagicMock(spec=discord.TextChannel)
    chat.name = "chat-general"
    chat.mention = "#chat-general"
    chat.permissions_for = lambda _member: discord.Permissions(send_messages=True)
    guild.text_channels = [chat]
    guild.get_channel = lambda _id: None
    bot = MagicMock()
    bot.welcome = repository
    welcome = Welcome(bot, repository)
    bot.get_cog = lambda name: welcome if name == "Welcome" else None
    return Admin(bot), repository


async def test_bienv_guarda_un_gif_valido(guild: SimpleNamespace, tmp_path: Path) -> None:
    cog, repository = await make_bienv(tmp_path, guild)
    ctx = make_ctx(guild, make_member(1, 20, admin=True))
    gif = "https://media.tenor.com/x/kratos.gif"

    await cog.bienv_text.callback(cog, ctx, None, gif=gif)

    assert (await repository.settings(guild.id)).gif_url == gif
    assert "Bienvenida actualizada" in last_message(ctx)
    assert ctx.send.await_args.kwargs["embed"].image.url == gif


async def test_bienv_rechaza_un_adjunto_de_discord(guild: SimpleNamespace, tmp_path: Path) -> None:
    cog, repository = await make_bienv(tmp_path, guild)
    ctx = make_ctx(guild, make_member(1, 20, admin=True))

    await cog.bienv_text.callback(
        cog, ctx, None, gif="https://cdn.discordapp.com/attachments/1/2/a.gif"
    )

    assert "caducan" in last_message(ctx)
    assert (await repository.settings(guild.id)).gif_url is None


async def test_bienv_quitar_vuelve_al_video(guild: SimpleNamespace, tmp_path: Path) -> None:
    cog, repository = await make_bienv(tmp_path, guild)
    await repository.save_settings(guild.id, WelcomeSettings(gif_url="https://x.com/a.gif"))
    ctx = make_ctx(guild, make_member(1, 20, admin=True))

    await cog.bienv_text.callback(cog, ctx, None, gif="quitar")

    assert (await repository.settings(guild.id)).gif_url is None
    assert "vídeo de Kratos" in last_message(ctx)


async def test_bienv_sin_argumentos_solo_muestra(guild: SimpleNamespace, tmp_path: Path) -> None:
    cog, _repository = await make_bienv(tmp_path, guild)
    ctx = make_ctx(guild, make_member(1, 20, admin=True))

    await cog.bienv_text.callback(cog, ctx, None, gif="")

    assert last_message(ctx).startswith("👋 Bienvenida actual.")
