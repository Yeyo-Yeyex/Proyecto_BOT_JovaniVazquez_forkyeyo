"""Pruebas de los comandos secretos (`enchufe`, `amanece`) con el bot real.

Viven en el cog `Admin` pero tocan la `pala` y el negocio, que son del `BotClient`: se
prueban con el cog cargado como en producción. Lo que importa es el secreto: solo
Yeyo, y con cualquier otro el bot no hace nada a la vista.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from bot.app import BotClient
from bot.cogs.admin import AMANECE_USAGE, ENCHUFE_USAGE, SECRET_OWNER
from bot.cogs.deploy import DEPLOYERS
from bot.services.business import Business
from bot.services.levels import local_day
from bot.services.work import BATTERY_MAX
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


async def secret(
    client: BotClient, name: str, text: str, user_id: int = SECRET_OWNER, *, guild: bool = True
) -> MagicMock:
    """Ejecuta `.<name> <text>` como `user_id` y devuelve el `ctx` para mirarlo."""
    admin = client.get_cog("Admin")
    command = client.get_command(name)
    assert command is not None
    ctx = MagicMock()
    ctx.bot = client
    ctx.guild = SimpleNamespace(id=GUILD_ID) if guild else None
    ctx.author = member(user_id)
    ctx.command = command
    ctx.send = AsyncMock()
    ctx.message.delete = AsyncMock()
    assert await admin.cog_check(ctx)  # type: ignore[union-attr]
    await command.callback(admin, ctx, texto=text)  # type: ignore[arg-type]
    return ctx


async def enchufe(client: BotClient, text: str, *args: int, **kwargs: bool) -> MagicMock:
    return await secret(client, "enchufe", text, *args, **kwargs)


async def amanece(client: BotClient, text: str, *args: int, **kwargs: bool) -> MagicMock:
    return await secret(client, "amanece", text, *args, **kwargs)


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
        dani = next(i for i in DEPLOYERS if i != SECRET_OWNER)
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
        for name in ("enchufe", "amanece"):
            command = client.get_command(name)
            assert command is not None and command.hidden and command.extras["secret"]
            assert client.tree.get_command(name) is None, "sin `/`"
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


async def test_amanece_vacia_las_franjas_de_hoy_del_curro_y_del_negocio(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        await client.work.hire(GUILD_ID, GUILLE, "politica")
        today = local_day(time.time()).isoformat()
        await client.negocio.repository.save(
            GUILD_ID,
            Business(GUILLE, "tienda", "vegueta", "Ultramarinos", day=today, turns_today=3),
        )
        status = await client.work.status(GUILD_ID, GUILLE)
        assert status is not None
        status.contract.shifts_today, status.contract.franjas = 4, {"extra": 1}
        status.contract.battery = 3.0
        await client.work.repository.save_contract(GUILD_ID, GUILLE, status.contract)
        assert (await client.work.status(GUILD_ID, GUILLE)).agenda.free == 0  # type: ignore[union-attr]

        ctx = await amanece(client, f"<@{GUILLE}>")
        ctx.message.delete.assert_awaited_once()
        assert "franjas de hoy a cero" in whisper(ctx)
        status = await client.work.status(GUILD_ID, GUILLE)
        assert status is not None
        assert status.agenda.free == 8 - status.contract.jornada
        assert status.battery == BATTERY_MAX
        assert await client.negocio.franjas_today(GUILD_ID, GUILLE) == 0

        # Con cualquier otro, nada; sin mención, el suyo; una mención rara, el uso.
        await client.work.spend_battery(GUILD_ID, GUILLE, 50)
        for ctx in (await amanece(client, "", GUILLE), await amanece(client, "", guild=False)):
            ctx.send.assert_not_awaited()
            ctx.author.send.assert_not_awaited()
            ctx.message.delete.assert_not_awaited()
        assert await client.work.battery(GUILD_ID, GUILLE) < BATTERY_MAX
        assert "no tiene curro" in whisper(await amanece(client, ""))
        assert whisper(await amanece(client, "nadie")) == AMANECE_USAGE
    finally:
        await client.close()
