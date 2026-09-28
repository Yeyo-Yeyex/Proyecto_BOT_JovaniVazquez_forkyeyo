"""Pruebas de persistencia y reanudación de los recuentos de mensajes."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bot.cogs.message_stats import MessageStats
from bot.repositories.message_stats import MessageStatsRepository


class FakeHistoryChannel:
    """Canal falso cuyo historial permite probar el escaneo sin conectar a Discord."""

    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self.messages = messages

    def history(self, **_: object) -> AsyncIterator[SimpleNamespace]:
        """Expone los mensajes de prueba con la interfaz asíncrona de discord.py."""

        async def iterate() -> AsyncIterator[SimpleNamespace]:
            for message in self.messages:
                yield message

        return iterate()


@pytest.mark.asyncio
async def test_escaneo_omite_bots_y_persiste_solo_recuentos(tmp_path: Path) -> None:
    """El importador cuenta usuarios normales y omite mensajes de bots."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()
    assert await repository.start_import(10, [20], cutoff_id=500)

    messages = [
        SimpleNamespace(
            author=SimpleNamespace(id=30, bot=False),
            webhook_id=None,
            is_system=lambda: False,
        ),
        SimpleNamespace(
            author=SimpleNamespace(id=31, bot=True),
            webhook_id=None,
            is_system=lambda: False,
        ),
        SimpleNamespace(
            author=SimpleNamespace(id=30, bot=False),
            webhook_id=None,
            is_system=lambda: False,
        ),
    ]
    cog = MessageStats(MagicMock(), repository)

    await cog._run_import(
        SimpleNamespace(id=10),
        {20: FakeHistoryChannel(messages)},
    )

    assert await repository.message_count(10, 30) == 2
    assert await repository.message_count(10, 31) == 0
    status = await repository.import_status(10)
    assert status is not None
    assert status.status == "completed"


@pytest.mark.asyncio
async def test_importacion_guarda_agregados_y_cuenta_mensajes_en_vivo(
    tmp_path: Path,
) -> None:
    """El total suma historial y mensajes posteriores al corte sin duplicar canales."""
    repository = MessageStatsRepository(tmp_path / "data" / "stats.sqlite3")
    await repository.initialize()
    assert await repository.start_import(10, [20], cutoff_id=500)

    await repository.record_live_message(10, 30, message_id=499)
    await repository.record_live_message(10, 30, message_id=501)
    await repository.record_live_message(10, 30, message_id=502)
    await repository.update_channel_progress(10, 20, 6, 6)
    await repository.save_channel_counts(10, 20, {30: 4, 31: 2})
    await repository.save_channel_counts(10, 20, {30: 4})
    await repository.finish_import(10)

    assert await repository.message_count(10, 30) == 6
    assert await repository.message_count(10, 31) == 2
    assert await repository.message_count(11, 30) == 0
    status = await repository.import_status(10)
    assert status is not None
    assert status.status == "completed"
    assert status.scanned_channels == 1
    assert status.messages_scanned == 6
    assert status.messages_counted == 6


@pytest.mark.asyncio
async def test_importacion_interrumpida_se_reanuda_sin_recontar_canales(
    tmp_path: Path,
) -> None:
    """Al reanudar, los canales completados se conservan y solo quedan pendientes."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()
    assert await repository.start_import(10, [20, 21], cutoff_id=500)
    await repository.save_channel_counts(10, 20, {30: 3})
    await repository.recover_interrupted_imports()

    status = await repository.import_status(10)
    assert status is not None
    assert status.status == "interrupted"
    assert await repository.start_import(10, [20, 21], cutoff_id=600)
    assert await repository.pending_channel_ids(10) == [21]

    await repository.save_channel_counts(10, 21, {30: 2})
    await repository.finish_import(10)

    assert await repository.message_count(10, 30) == 5
    status = await repository.import_status(10)
    assert status is not None
    assert status.status == "completed"
    assert status.cutoff_id == 500


@pytest.mark.asyncio
async def test_progreso_se_actualiza_sin_acumular_reintentos(tmp_path: Path) -> None:
    """El progreso representa el último punto del escaneo, no suma escrituras repetidas."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()
    assert await repository.start_import(10, [20], cutoff_id=500)

    await repository.update_channel_progress(10, 20, 100, 80)
    await repository.update_channel_progress(10, 20, 150, 120)

    status = await repository.import_status(10)
    assert status is not None
    assert status.messages_scanned == 150
    assert status.messages_counted == 120


@pytest.mark.asyncio
async def test_initialize_migra_base_existente_agregando_columnas_de_progreso(
    tmp_path: Path,
) -> None:
    """La base SQLite previa recibe las columnas nuevas sin perder su estado."""
    database_path = tmp_path / "stats.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE message_imports (
                guild_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                cutoff_id INTEGER NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                total_channels INTEGER NOT NULL DEFAULT 0,
                scanned_channels INTEGER NOT NULL DEFAULT 0,
                failed_channels INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE message_import_channels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                PRIMARY KEY (guild_id, channel_id)
            );
            INSERT INTO message_imports (guild_id, status, cutoff_id)
            VALUES (10, 'interrupted', 500);
            INSERT INTO message_import_channels (guild_id, channel_id)
            VALUES (10, 20);
            """
        )

    repository = MessageStatsRepository(database_path)
    await repository.initialize()
    status = await repository.import_status(10)

    assert status is not None
    assert status.status == "interrupted"
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(message_import_channels)")
        }
    assert {"messages_scanned", "messages_counted"} <= columns


@pytest.mark.asyncio
async def test_importacion_parcial_puede_reintentar_canales_fallidos(
    tmp_path: Path,
) -> None:
    """Un canal fallido queda visible y pendiente para un intento posterior."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()
    assert await repository.start_import(10, [20], cutoff_id=500)
    await repository.mark_channel_failed(10, 20, "Forbidden")
    await repository.finish_import(10)

    status = await repository.import_status(10)
    assert status is not None
    assert status.status == "partial"
    assert status.failed_channels == 1

    assert await repository.start_import(10, [20], cutoff_id=600)
    assert await repository.pending_channel_ids(10) == [20]
    await repository.save_channel_counts(10, 20, {30: 1})
    await repository.finish_import(10)

    status = await repository.import_status(10)
    assert status is not None
    assert status.status == "completed"
    assert await repository.message_count(10, 30) == 1


@pytest.mark.asyncio
async def test_eliminar_datos_de_servidor_no_afecta_a_otros(tmp_path: Path) -> None:
    """La eliminación por servidor respeta aislamiento entre servidores."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()
    await repository.record_live_message(10, 30, message_id=1)
    await repository.record_live_message(11, 30, message_id=2)

    await repository.delete_guild_data(10)

    assert await repository.message_count(10, 30) == 0
    assert await repository.message_count(11, 30) == 1


@pytest.mark.asyncio
async def test_activar_niveles_convierte_historial_una_sola_vez(tmp_path: Path) -> None:
    """La activación concede XP histórica una vez y exige importación completa."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()
    assert await repository.start_import(10, [20], cutoff_id=500)
    await repository.save_channel_counts(10, 20, {30: 10, 31: 4})
    await repository.record_live_message(10, 30, message_id=501)
    await repository.finish_import(10)

    assert await repository.enable_levels(11, historical_xp_per_message=20) == (False, 0)
    assert await repository.enable_levels(10, historical_xp_per_message=20) == (True, 2)
    assert await repository.member_xp(10, 30) == 220
    assert await repository.member_xp(10, 31) == 80

    await repository.disable_levels(10)
    assert await repository.enable_levels(10, historical_xp_per_message=20) == (True, 0)
    assert await repository.member_xp(10, 30) == 220
    settings = await repository.level_settings(10)
    assert settings is not None
    assert settings.enabled is True
    assert settings.historical_seeded is True


@pytest.mark.asyncio
async def test_xp_obedece_enfriamiento_y_aislamiento_por_servidor(
    tmp_path: Path,
) -> None:
    """El XP solo se concede a niveles activos y respeta cooldown por miembro."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()
    assert await repository.start_import(10, [20], cutoff_id=500)
    await repository.save_channel_counts(10, 20, {30: 1})
    await repository.finish_import(10)
    await repository.enable_levels(10, historical_xp_per_message=20)

    assert await repository.award_message_xp(11, 30, 20, now=100) is None
    first = await repository.award_message_xp(10, 30, 20, now=100)
    assert first is not None
    assert first.previous_xp == 20
    assert first.total_xp == 40
    assert await repository.award_message_xp(10, 30, 20, now=159) is None
    second = await repository.award_message_xp(10, 30, 20, now=160)
    assert second is not None
    assert second.total_xp == 60


@pytest.mark.asyncio
async def test_canal_de_anuncios_y_ranking_son_por_servidor(tmp_path: Path) -> None:
    """La configuración y clasificación no filtran XP entre servidores."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()
    for guild_id in (10, 11):
        assert await repository.start_import(guild_id, [guild_id + 20], cutoff_id=500)
        await repository.save_channel_counts(guild_id, guild_id + 20, {30: 1})
        await repository.finish_import(guild_id)
        await repository.enable_levels(guild_id, historical_xp_per_message=20)

    await repository.set_level_announce_channel(10, 99)
    await repository.set_level_cooldown(10, 120)
    await repository.award_message_xp(10, 30, 20, now=100)

    settings = await repository.level_settings(10)
    assert settings is not None
    assert settings.announce_channel_id == 99
    assert settings.cooldown_seconds == 120
    assert await repository.level_leaderboard(10, 10, 0) == [(30, 40)]
    assert await repository.level_leaderboard(11, 10, 0) == [(30, 20)]


@pytest.mark.asyncio
async def test_cooldown_rechaza_valores_fuera_del_rango(tmp_path: Path) -> None:
    """La persistencia no admite cooldown cero o excesivamente largo."""
    repository = MessageStatsRepository(tmp_path / "stats.sqlite3")
    await repository.initialize()

    with pytest.raises(ValueError, match="entre 10 y 3600"):
        await repository.set_level_cooldown(10, 0)
    with pytest.raises(ValueError, match="entre 10 y 3600"):
        await repository.set_level_cooldown(10, 3601)
