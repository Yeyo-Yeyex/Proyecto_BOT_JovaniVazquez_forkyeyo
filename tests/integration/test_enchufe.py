"""Pruebas de `enchufe` con el bot real (`BotClient` y `load_extension`).

`enchufe` vive en el cog `Admin` pero sube de puesto en `pala`, que es del `BotClient`:
se prueba con el cog cargado como en producción. Lo que importa es el secreto: solo
Yeyo, y con cualquier otro el bot no hace nada a la vista.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from bot.app import BotClient
from bot.cogs.admin import ENCHUFE_OWNER, ENCHUFE_USAGE
from bot.cogs.deploy import DEPLOYERS
from bot.services.work_catalog import JOB_BY_KEY

GUILD_ID = 10
GUILLE = 77
POLITICA = JOB_BY_KEY["politica"]


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
    await client.economy.repository.initialize()
    await client.housing.initialize()
    await client.work.repository.initialize()
    await client.negocio.repository.initialize()
    await client.moderation.initialize()
    await client.load_extension("bot.cogs.admin")
    return client


def member(user_id: int) -> MagicMock:
    person = MagicMock(spec=discord.Member)
    person.id = user_id
    person.bot = False
    person.display_name = f"Miembro {user_id}"
    person.send = AsyncMock()
    return person


@pytest.fixture(autouse=True)
def guille_por_mencion(monkeypatch: pytest.MonkeyPatch) -> None:
    """`<@77>` es Guille; lo demás no es nadie."""

    async def convert(self: object, ctx: object, text: str) -> MagicMock:
        if text != f"<@{GUILLE}>":
            raise commands.MemberNotFound(text)
        return member(GUILLE)

    monkeypatch.setattr(commands.MemberConverter, "convert", convert)


async def enchufe(
    client: BotClient, text: str, user_id: int = ENCHUFE_OWNER, *, guild: bool = True
) -> MagicMock:
    """Ejecuta `.enchufe <text>` como `user_id` y devuelve el `ctx` para mirarlo."""
    admin = client.get_cog("Admin")
    ctx = MagicMock()
    ctx.bot = client
    ctx.guild = SimpleNamespace(id=GUILD_ID) if guild else None
    ctx.author = member(user_id)
    ctx.command = client.get_command("enchufe")
    ctx.send = AsyncMock()
    ctx.message.delete = AsyncMock()
    assert await admin.cog_check(ctx)  # type: ignore[union-attr]
    await admin.enchufe.callback(admin, ctx, texto=text)  # type: ignore[union-attr]
    return ctx


def whisper(ctx: MagicMock) -> str:
    """Lo que el bot le ha dicho por privado; nunca nada en el canal."""
    ctx.send.assert_not_awaited()
    return ctx.author.send.await_args.args[0]


async def level(client: BotClient) -> int:
    status = await client.work.status(GUILD_ID, GUILLE)
    assert status is not None
    return status.contract.level


async def test_yeyo_sube_a_guille_por_privado_y_borra_su_mensaje(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        await client.work.hire(GUILD_ID, GUILLE, "politica")

        ctx = await enchufe(client, f"<@{GUILLE}>")
        ctx.message.delete.assert_awaited_once()
        assert "sube 1 puesto:" in whisper(ctx)
        assert await level(client) == 2

        ctx = await enchufe(client, f"<@{GUILLE}> 99")
        assert "Moncloa" in whisper(ctx) and "Bruselas" in whisper(ctx)
        assert await level(client) == POLITICA.trunk_top

        ctx = await enchufe(client, f"<@{GUILLE}> 99 moncloa")
        assert await level(client) == POLITICA.top
        ctx = await enchufe(client, f"<@{GUILLE}>")
        assert "lo más alto" in whisper(ctx)
    finally:
        await client.close()


async def test_con_cualquier_otro_no_pasa_nada_a_la_vista(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        await client.work.hire(GUILD_ID, GUILLE, "politica")
        dani = next(i for i in DEPLOYERS if i != ENCHUFE_OWNER)
        for ctx in (
            await enchufe(client, f"<@{GUILLE}>", dani),
            await enchufe(client, f"<@{GUILLE}>", GUILLE),
            await enchufe(client, "", GUILLE),
            await enchufe(client, f"<@{GUILLE}>", guild=False),
        ):
            ctx.send.assert_not_awaited()
            ctx.author.send.assert_not_awaited()
            ctx.message.delete.assert_not_awaited()
        assert await level(client) == 1
        command = client.get_command("enchufe")
        assert command is not None and command.hidden and command.extras["secret"]
        assert client.tree.get_command("enchufe") is None, "sin `/`"
    finally:
        await client.close()


async def test_un_uso_malo_solo_lo_ve_yeyo(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        assert whisper(await enchufe(client, "")) == ENCHUFE_USAGE
        assert whisper(await enchufe(client, "nadie 2")) == ENCHUFE_USAGE
        assert "No tiene curro" in whisper(await enchufe(client, f"<@{GUILLE}>"))
    finally:
        await client.close()
