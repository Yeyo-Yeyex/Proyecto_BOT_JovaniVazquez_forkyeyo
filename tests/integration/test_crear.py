"""Pruebas de `crear` con el bot real (`BotClient` y `load_extension`).

`crear` vive en el cog `Admin` pero escribe en la mochila de la tienda, que es del
`BotClient`: se prueba con el cog cargado como en producción, no montado a mano.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.app import BotClient
from bot.cogs.admin import pick_object
from bot.cogs.deploy import DEPLOYERS
from bot.services.shop import Kind
from bot.services.shop_catalog import CATALOG

GUILD_ID = 10
NOW = 1_700_000_000.0
#: Solo Yeyo y Dani crean objetos, como con `bloquear`.
DEPLOYER_ID = min(DEPLOYERS)
GUILLE = 77


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
    await client.moderation.initialize()
    await client.shop.initialize()
    await client.shop.stock_catalog(GUILD_ID, CATALOG, NOW)
    await client.load_extension("bot.cogs.admin")
    return client


def member(user_id: int) -> MagicMock:
    person = MagicMock(spec=discord.Member)
    person.id = user_id
    person.bot = False
    person.mention = f"<@{user_id}>"
    return person


async def crear(client: BotClient, text: str, user_id: int = DEPLOYER_ID) -> str:
    """Ejecuta `.crear @Guille <text>` como `user_id` y devuelve lo que contesta."""
    admin = client.get_cog("Admin")
    ctx = MagicMock()
    ctx.bot = client
    ctx.guild = SimpleNamespace(id=GUILD_ID)
    ctx.author = member(user_id)
    ctx.command = client.get_command("crear")
    ctx.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await admin.crear_text.callback(admin, ctx, member(GUILLE), objeto=text)  # type: ignore[union-attr]
    progress = ctx.send.return_value
    if progress.edit.await_count:
        return progress.edit.await_args.kwargs["content"]
    return ctx.send.await_args.args[0]


async def test_crear_devuelve_la_patata_unica_sin_gastar_existencias(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        patata = await client.shop.item_by_key(GUILD_ID, "patata_espana")
        assert patata is not None

        answer = await crear(client, "Patata con forma de España")

        assert "Patata con forma de España" in answer and f"<@{GUILLE}>" in answer
        (entry,) = await client.shop.inventory(GUILD_ID, GUILLE, NOW + 1)
        assert entry.catalog_key == "patata_espana" and entry.serial is None
        after = await client.shop.item(GUILD_ID, patata.id)
        assert after is not None and after.sold == patata.sold, "no gasta existencias"
        assert "Ya tenía otro" in await crear(client, "patata_espana")
    finally:
        await client.close()


async def test_solo_yeyo_y_dani_pueden_crear(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        other_admin = max(DEPLOYERS) + 1
        assert "Solo Yeyo y Dani" in await crear(client, "Piedra", other_admin)
        assert await client.shop.inventory(GUILD_ID, GUILLE, NOW + 1) == []
    finally:
        await client.close()


async def test_crear_no_adivina_ni_da_roles_potenciadores_o_mascotas(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        assert "Hay varios" in await crear(client, "patata")  # y la tortilla de patatas
        assert "No hay ningún objeto «nada que exista»" in await crear(client, "nada que exista")
        assert "No hay ningún objeto" in await crear(client, "mascota_gato")
        items = await client.shop.items(GUILD_ID, include_hidden=True)
        others = [i for i in items if i.kind is not Kind.TROPHY]
        objects = [i for i in items if i.kind is Kind.TROPHY]
        assert others, "el surtido trae mascotas con las que comprobarlo"
        assert all(isinstance(pick_object(objects, str(i.id)), str) for i in others)
        assert await client.shop.inventory(GUILD_ID, GUILLE, NOW + 1) == []
    finally:
        await client.close()
