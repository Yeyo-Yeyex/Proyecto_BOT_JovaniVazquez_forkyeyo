"""Pruebas de la puesta en marcha de los niveles: comando `niveles` e importación.

Cubren lo que hace falta para que los niveles funcionen en un servidor nuevo:
importar el historial, encenderlos (también tras una importación parcial),
reanudar una importación que cortó un reinicio y el canal de anuncios.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.cogs.admin import NIVELES_USAGE, Admin
from bot.cogs.message_stats import MessageStats
from bot.repositories.message_stats import LevelAward, MessageStatsRepository
from bot.services.levels import HISTORICAL_XP_PER_MESSAGE

GUILD = 10


def user_message(author_id: int) -> SimpleNamespace:
    """Mensaje de una persona, como los que devuelve `channel.history`."""
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id, bot=False),
        webhook_id=None,
        is_system=lambda: False,
    )


class FakeChannel:
    """Canal de texto falso con historial y `send`."""

    def __init__(self, channel_id: int, authors: list[int], *, readable: bool = True) -> None:
        self.id = channel_id
        self.authors = authors
        self.readable = readable
        self.send = AsyncMock()
        self.mention = f"<#{channel_id}>"

    def history(self, **_: object) -> AsyncIterator[SimpleNamespace]:
        """Historial asíncrono; si no es legible, falla como Discord sin permiso."""

        async def iterate() -> AsyncIterator[SimpleNamespace]:
            if not self.readable:
                raise discord.Forbidden(MagicMock(status=403), "sin permiso")
            for author_id in self.authors:
                yield user_message(author_id)

        return iterate()


def fake_guild(*channels: FakeChannel) -> SimpleNamespace:
    """Servidor con los canales dados y sin hilos."""
    by_id = {channel.id: channel for channel in channels}
    return SimpleNamespace(
        id=GUILD,
        text_channels=list(channels),
        threads=[],
        channels=[],
        get_channel=by_id.get,
        me=MagicMock(),
    )


async def make_stats(tmp_path: Path) -> tuple[MessageStats, MessageStatsRepository]:
    repository = MessageStatsRepository(tmp_path / "bot.db")
    await repository.initialize()
    return MessageStats(MagicMock(), repository), repository


async def wait_import(cog: MessageStats) -> None:
    """Espera a que termine la tarea de importación en segundo plano."""
    task = cog._scan_tasks.get(GUILD)
    if task is not None:
        await task


# -- Repositorio ----------------------------------------------------------------------


async def test_una_importacion_parcial_permite_encender_los_niveles(tmp_path: Path) -> None:
    _cog, repository = await make_stats(tmp_path)
    assert await repository.start_import(GUILD, [20, 21], cutoff_id=500)
    await repository.save_channel_counts(GUILD, 20, {30: 3})
    await repository.mark_channel_failed(GUILD, 21, "Forbidden")
    await repository.finish_import(GUILD)

    assert await repository.enable_levels(GUILD, historical_xp_per_message=20) == (True, 1)
    assert await repository.member_xp(GUILD, 30) == 60


async def test_un_canal_importado_despues_de_encender_suma_su_xp(tmp_path: Path) -> None:
    _cog, repository = await make_stats(tmp_path)
    assert await repository.start_import(GUILD, [20, 21], cutoff_id=500)
    await repository.save_channel_counts(GUILD, 20, {30: 3})
    await repository.mark_channel_failed(GUILD, 21, "Forbidden")
    await repository.finish_import(GUILD)
    await repository.enable_levels(GUILD, historical_xp_per_message=HISTORICAL_XP_PER_MESSAGE)

    # Se arregla el permiso y se reimporta el canal que faltaba.
    assert await repository.start_import(GUILD, [20, 21], cutoff_id=600)
    await repository.save_channel_counts(GUILD, 21, {30: 2, 31: 1})
    await repository.finish_import(GUILD)

    assert await repository.member_xp(GUILD, 30) == 5 * HISTORICAL_XP_PER_MESSAGE
    assert await repository.member_xp(GUILD, 31) == HISTORICAL_XP_PER_MESSAGE


# -- Cog de niveles -------------------------------------------------------------------


async def test_importar_enciende_los_niveles_y_lo_anuncia(tmp_path: Path) -> None:
    cog, repository = await make_stats(tmp_path)
    general = FakeChannel(20, [30, 30, 31])
    guild = fake_guild(general)

    text = await cog.start_import(guild, general)  # type: ignore[arg-type]
    await wait_import(cog)

    assert "Importación iniciada" in text
    settings = await repository.level_settings(GUILD)
    assert settings is not None and settings.enabled and settings.historical_seeded
    assert await repository.member_xp(GUILD, 30) == 2 * HISTORICAL_XP_PER_MESSAGE
    general.send.assert_awaited_once()
    assert "3" in general.send.await_args.args[0]


async def test_importacion_parcial_tambien_enciende_y_avisa_del_canal(tmp_path: Path) -> None:
    cog, repository = await make_stats(tmp_path)
    general = FakeChannel(20, [30])
    privado = FakeChannel(21, [31], readable=False)
    guild = fake_guild(general, privado)

    await cog.start_import(guild, general)  # type: ignore[arg-type]
    await wait_import(cog)

    settings = await repository.level_settings(GUILD)
    assert settings is not None and settings.enabled
    assert "Leer el historial de mensajes" in general.send.await_args.args[0]


async def test_una_importacion_cortada_por_un_reinicio_se_reanuda(tmp_path: Path) -> None:
    cog, repository = await make_stats(tmp_path)
    # La base de datos dice "running", pero en este proceso no hay tarea viva.
    assert await repository.start_import(GUILD, [20], cutoff_id=500)
    general = FakeChannel(20, [30])

    text = await cog.start_import(fake_guild(general), general)  # type: ignore[arg-type]
    await wait_import(cog)

    assert "reanudada" in text
    assert await repository.member_xp(GUILD, 30) == HISTORICAL_XP_PER_MESSAGE


async def test_activar_sin_importar_explica_el_siguiente_paso(tmp_path: Path) -> None:
    cog, repository = await make_stats(tmp_path)

    text = await cog.activate(fake_guild())  # type: ignore[arg-type]

    assert "importar" in text
    assert await repository.level_settings(GUILD) is None


async def test_la_subida_de_nivel_se_anuncia_en_el_canal_configurado(tmp_path: Path) -> None:
    cog, _repository = await make_stats(tmp_path)
    anuncios = MagicMock(spec=discord.TextChannel)
    guild = SimpleNamespace(id=GUILD, get_channel={77: anuncios}.get)
    origin = FakeChannel(20, [])
    award = LevelAward(previous_xp=0, total_xp=10, cooldown_seconds=60, announce_channel_id=77)

    assert cog._announce_target(guild, award, origin) is anuncios  # type: ignore[arg-type]
    gone = LevelAward(previous_xp=0, total_xp=10, cooldown_seconds=60, announce_channel_id=99)
    assert cog._announce_target(guild, gone, origin) is origin  # type: ignore[arg-type]


# -- Comando `niveles` ----------------------------------------------------------------


def make_admin(stats: MessageStats) -> Admin:
    bot = MagicMock()
    bot.get_cog = lambda name: stats if name == "MessageStats" else None
    return Admin(bot)


def make_ctx(guild: SimpleNamespace) -> MagicMock:
    ctx = MagicMock()
    ctx.guild = guild
    ctx.author = MagicMock(spec=discord.Member)
    ctx.channel = FakeChannel(20, [])
    ctx.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return ctx


async def test_niveles_sin_argumentos_ensena_el_estado(tmp_path: Path) -> None:
    stats, _repository = await make_stats(tmp_path)
    admin = make_admin(stats)
    ctx = make_ctx(fake_guild())

    await admin.niveles_text.callback(admin, ctx)

    text = ctx.send.await_args.args[0]
    assert "apagados" in text
    assert "importar" in text


async def test_niveles_rechaza_un_cooldown_fuera_de_rango(tmp_path: Path) -> None:
    stats, repository = await make_stats(tmp_path)
    admin = make_admin(stats)
    ctx = make_ctx(fake_guild())

    await admin.niveles_text.callback(admin, ctx, "5")

    assert ctx.send.await_args.args[0] == NIVELES_USAGE
    assert await repository.level_settings(GUILD) is None


async def test_niveles_guarda_cooldown_y_apaga(tmp_path: Path) -> None:
    stats, repository = await make_stats(tmp_path)
    admin = make_admin(stats)
    ctx = make_ctx(fake_guild())

    await admin.niveles_text.callback(admin, ctx, "desactivar", "120")

    settings = await repository.level_settings(GUILD)
    assert settings is not None
    assert settings.cooldown_seconds == 120
    assert not settings.enabled
    assert "apagados" in ctx.send.await_args.args[0]


async def test_niveles_importar_desde_texto_enciende_al_acabar(tmp_path: Path) -> None:
    stats, repository = await make_stats(tmp_path)
    admin = make_admin(stats)
    general = FakeChannel(20, [30])
    ctx = make_ctx(fake_guild(general))

    await admin.niveles_text.callback(admin, ctx, "importar")
    await wait_import(stats)

    settings = await repository.level_settings(GUILD)
    assert settings is not None and settings.enabled
    assert await repository.member_xp(GUILD, 30) == HISTORICAL_XP_PER_MESSAGE
