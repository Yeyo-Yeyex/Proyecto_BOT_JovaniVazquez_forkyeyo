"""Pruebas de `dinero` con el bot real (`BotClient` y `load_extension`).

`dinero` vive en el cog `Admin` pero escribe en la economía, que es del `BotClient`: se
prueba con el cog cargado como en producción, no montado a mano.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.app import BotClient
from bot.cogs.admin import MAX_DINERO
from bot.cogs.deploy import DEPLOYERS

GUILD_ID = 10
#: Solo Yeyo y Dani dan dinero, como con `crear`.
DEPLOYER_ID = min(DEPLOYERS)
GUILLE = 77


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
    await client.economy.repository.initialize()
    await client.moderation.initialize()
    await client.load_extension("bot.cogs.admin")
    return client


def member(user_id: int, *, bot: bool = False) -> MagicMock:
    person = MagicMock(spec=discord.Member)
    person.id = user_id
    person.bot = bot
    person.mention = f"<@{user_id}>"
    return person


async def dinero(
    client: BotClient, amount: int, to: MagicMock | None = None, user_id: int = DEPLOYER_ID
) -> str:
    """Ejecuta `.dinero <amount> [@to]` como `user_id` y devuelve lo que contesta."""
    admin = client.get_cog("Admin")
    ctx = MagicMock()
    ctx.bot = client
    ctx.guild = SimpleNamespace(id=GUILD_ID)
    ctx.author = member(user_id)
    ctx.command = client.get_command("dinero")
    ctx.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await admin.dinero_text.callback(admin, ctx, amount, to)  # type: ignore[union-attr]
    progress = ctx.send.return_value
    if progress.edit.await_count:
        return progress.edit.await_args.kwargs["content"]
    return ctx.send.await_args.args[0]


async def balance(client: BotClient, user_id: int) -> int:
    return await client.economy.balance(GUILD_ID, user_id)


async def test_dinero_lo_da_a_un_miembro_o_a_quien_lo_pide(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        before = await balance(client, GUILLE)
        answer = await dinero(client, 5000, member(GUILLE))
        assert "5.000 Y$" in answer and f"<@{GUILLE}>" in answer
        assert await balance(client, GUILLE) == before + 5000
        mine = await balance(client, DEPLOYER_ID)
        assert f"<@{DEPLOYER_ID}>" in await dinero(client, 1200)
        assert await balance(client, DEPLOYER_ID) == mine + 1200
        moves = await client.economy.ledger_summary(
            GUILD_ID, account=None, reasons=["admin:dinero"]
        )
        assert moves == {"admin:dinero": (2, 6200)}, "queda en el libro con su motivo"
    finally:
        await client.close()


async def test_solo_yeyo_y_dani_y_con_cantidades_que_tengan_sentido(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        before = await balance(client, GUILLE)
        other_admin = max(DEPLOYERS) + 1
        assert "Solo Yeyo y Dani" in await dinero(client, 5000, member(GUILLE), other_admin)
        assert "va de 1 a" in await dinero(client, 0, member(GUILLE))
        assert "va de 1 a" in await dinero(client, MAX_DINERO + 1, member(GUILLE))
        assert "bots" in await dinero(client, 5000, member(99, bot=True))
        assert await balance(client, GUILLE) == before
    finally:
        await client.close()
