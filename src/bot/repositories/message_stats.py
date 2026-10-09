"""Persistencia asíncrona de recuentos de mensajes y trabajos de importación.

Se guarda únicamente el número agregado de mensajes por servidor y usuario,
más el estado necesario para reanudar una importación. Nunca se persiste el
contenido, los adjuntos ni los metadatos de los mensajes individuales.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.levels import HISTORICAL_XP_PER_MESSAGE, MemberActivity

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ImportStatus:
    """Resumen persistido del último trabajo de importación de un servidor."""

    status: str
    total_channels: int
    scanned_channels: int
    failed_channels: int
    cutoff_id: int
    messages_scanned: int
    messages_counted: int


@dataclass(frozen=True, slots=True)
class LevelSettings:
    """Configuración persistida del sistema de niveles de un servidor."""

    enabled: bool
    cooldown_seconds: int
    announce_channel_id: int | None
    historical_seeded: bool


@dataclass(frozen=True, slots=True)
class LevelAward:
    """Experiencia concedida a un miembro (por mensaje, voz o reacción)."""

    previous_xp: int
    total_xp: int
    cooldown_seconds: int
    announce_channel_id: int | None
    #: Días seguidos con actividad tras esta concesión.
    streak_days: int = 0


class MessageStatsRepository:
    """Lee y escribe estadísticas agregadas en una base de datos SQLite.

    Args:
        database_path: Ruta del archivo SQLite que persistirá entre reinicios.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        """Abre una conexión configurada para transacciones seguras."""
        connection = sqlite.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas e índices si la base de datos aún no existe."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS member_message_counts (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    historical_count INTEGER NOT NULL DEFAULT 0,
                    live_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS message_imports (
                    guild_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,
                    cutoff_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    total_channels INTEGER NOT NULL DEFAULT 0,
                    scanned_channels INTEGER NOT NULL DEFAULT 0,
                    failed_channels INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS message_import_channels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    messages_scanned INTEGER NOT NULL DEFAULT 0,
                    messages_counted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, channel_id),
                    FOREIGN KEY (guild_id) REFERENCES message_imports(guild_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS guild_level_settings (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 60,
                    announce_channel_id INTEGER,
                    historical_seeded INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS member_levels (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    total_xp INTEGER NOT NULL DEFAULT 0,
                    last_awarded_at REAL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )
            level_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(member_levels)").fetchall()
            }
            # Columnas añadidas con la racha, el bonus diario y el XP por
            # reacciones; las bases de datos antiguas se amplían en el sitio.
            for column, definition in (
                ("last_active_day", "TEXT"),
                ("streak_days", "INTEGER NOT NULL DEFAULT 0"),
                ("reaction_day", "TEXT"),
                ("reaction_xp", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in level_columns:
                    # `column` y `definition` salen de una tupla fija del código.
                    connection.execute(
                        f"ALTER TABLE member_levels ADD COLUMN {column} {definition}"
                    )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(message_import_channels)"
                ).fetchall()
            }
            if "messages_scanned" not in columns:
                connection.execute(
                    """
                    ALTER TABLE message_import_channels
                    ADD COLUMN messages_scanned INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "messages_counted" not in columns:
                connection.execute(
                    """
                    ALTER TABLE message_import_channels
                    ADD COLUMN messages_counted INTEGER NOT NULL DEFAULT 0
                    """
                )

    async def recover_interrupted_imports(self) -> None:
        """Marca las importaciones que quedaron activas al apagarse el proceso."""
        await self._run(self._recover_interrupted_imports_sync)

    def _recover_interrupted_imports_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE message_imports
                SET status = 'interrupted', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                """
            )

    async def start_import(
        self,
        guild_id: int,
        channel_ids: list[int],
        cutoff_id: int,
    ) -> bool:
        """Crea o reanuda una importación; devuelve False si ya terminó o se ejecuta.

        Los canales completados nunca se vuelven a sumar al reanudar. Si un
        canal falla, se marca pendiente en la siguiente reanudación para que
        una falta temporal de permisos no deje la importación bloqueada.
        """
        return await self._run(self._start_import_sync, guild_id, channel_ids, cutoff_id)

    def _start_import_sync(self, guild_id: int, channel_ids: list[int], cutoff_id: int) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status FROM message_imports WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            if existing is not None and existing["status"] in {"running", "completed"}:
                connection.rollback()
                return False

            if existing is None:
                connection.execute(
                    """
                    INSERT INTO message_imports (guild_id, status, cutoff_id)
                    VALUES (?, 'running', ?)
                    """,
                    (guild_id, cutoff_id),
                )
                # Antes del corte no se puede separar lo que ya se contó en
                # vivo de lo que se importará. Se reinicia ese agregado solo
                # al iniciar la importación histórica por primera vez.
                connection.execute(
                    "UPDATE member_message_counts SET live_count = 0 WHERE guild_id = ?",
                    (guild_id,),
                )
            else:
                connection.execute(
                    """
                    UPDATE message_imports
                    SET status = 'running', updated_at = CURRENT_TIMESTAMP
                    WHERE guild_id = ?
                    """,
                    (guild_id,),
                )
                connection.execute(
                    """
                    UPDATE message_import_channels
                    SET status = 'pending',
                        error = NULL,
                        messages_scanned = 0,
                        messages_counted = 0
                    WHERE guild_id = ? AND status = 'failed'
                    """,
                    (guild_id,),
                )

            connection.executemany(
                """
                INSERT OR IGNORE INTO message_import_channels
                    (guild_id, channel_id, status)
                VALUES (?, ?, 'pending')
                """,
                [(guild_id, channel_id) for channel_id in set(channel_ids)],
            )
            connection.execute(
                """
                UPDATE message_imports
                SET total_channels = (
                    SELECT COUNT(*) FROM message_import_channels WHERE guild_id = ?
                ),
                failed_channels = (
                    SELECT COUNT(*) FROM message_import_channels
                    WHERE guild_id = ? AND status = 'failed'
                ),
                updated_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                """,
                (guild_id, guild_id, guild_id),
            )
            connection.commit()
            return True

    async def pending_channel_ids(self, guild_id: int) -> list[int]:
        """Devuelve los canales aún no importados."""
        result = await self._run(self._pending_channel_ids_sync, guild_id)
        return result  # type: ignore[return-value]

    def _pending_channel_ids_sync(self, guild_id: int) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT channel_id FROM message_import_channels
                WHERE guild_id = ? AND status = 'pending'
                ORDER BY channel_id
                """,
                (guild_id,),
            ).fetchall()
            return [int(row["channel_id"]) for row in rows]

    async def update_channel_progress(
        self, guild_id: int, channel_id: int, messages_scanned: int, messages_counted: int
    ) -> None:
        """Actualiza contadores absolutos de progreso sin tocar los agregados finales."""
        await self._run(
            self._update_channel_progress_sync,
            guild_id,
            channel_id,
            messages_scanned,
            messages_counted,
        )

    def _update_channel_progress_sync(
        self, guild_id: int, channel_id: int, messages_scanned: int, messages_counted: int
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE message_import_channels
                SET messages_scanned = ?, messages_counted = ?
                WHERE guild_id = ? AND channel_id = ? AND status = 'pending'
                """,
                (messages_scanned, messages_counted, guild_id, channel_id),
            )

    async def save_channel_counts(
        self, guild_id: int, channel_id: int, counts: dict[int, int]
    ) -> None:
        """Suma de forma atómica los autores de un canal y marca el canal completo."""
        await self._run(self._save_channel_counts_sync, guild_id, channel_id, counts)

    def _save_channel_counts_sync(
        self, guild_id: int, channel_id: int, counts: dict[int, int]
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                """
                SELECT status FROM message_import_channels
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            ).fetchone()
            if state is None or state["status"] == "scanned":
                connection.rollback()
                return

            connection.executemany(
                """
                INSERT INTO member_message_counts
                    (guild_id, user_id, historical_count, live_count)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    historical_count = historical_count + excluded.historical_count
                """,
                [(guild_id, user_id, count) for user_id, count in counts.items() if count > 0],
            )
            # Si el historial ya se convirtió en XP (niveles activados tras una
            # importación parcial), los canales que se importan después suman
            # su XP aquí mismo; si no, se quedarían fuera para siempre.
            seeded = connection.execute(
                "SELECT historical_seeded FROM guild_level_settings WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            if seeded is not None and seeded["historical_seeded"]:
                connection.executemany(
                    """
                    INSERT INTO member_levels (guild_id, user_id, total_xp)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        total_xp = total_xp + excluded.total_xp
                    """,
                    [
                        (guild_id, user_id, count * HISTORICAL_XP_PER_MESSAGE)
                        for user_id, count in counts.items()
                        if count > 0
                    ],
                )
            connection.execute(
                """
                UPDATE message_import_channels
                SET status = 'scanned', error = NULL
                WHERE guild_id = ? AND channel_id = ?
                """,
                (guild_id, channel_id),
            )
            connection.execute(
                """
                UPDATE message_imports
                SET scanned_channels = (
                    SELECT COUNT(*) FROM message_import_channels
                    WHERE guild_id = ? AND status = 'scanned'
                ), updated_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                """,
                (guild_id, guild_id),
            )
            connection.commit()

    async def mark_channel_failed(self, guild_id: int, channel_id: int, error: str) -> None:
        """Marca un canal no legible sin abortar el resto del servidor."""
        await self._run(self._mark_channel_failed_sync, guild_id, channel_id, error)

    def _mark_channel_failed_sync(self, guild_id: int, channel_id: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE message_import_channels
                SET status = 'failed', error = ?
                WHERE guild_id = ? AND channel_id = ?
                """,
                (error[:300], guild_id, channel_id),
            )
            connection.execute(
                """
                UPDATE message_imports
                SET failed_channels = (
                    SELECT COUNT(*) FROM message_import_channels
                    WHERE guild_id = ? AND status = 'failed'
                ), updated_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                """,
                (guild_id, guild_id),
            )

    async def finish_import(self, guild_id: int) -> None:
        """Cierra el trabajo como completo o parcial, según sus canales."""
        await self._run(self._finish_import_sync, guild_id)

    def _finish_import_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE message_imports
                SET status = CASE
                    WHEN EXISTS (
                        SELECT 1 FROM message_import_channels
                        WHERE guild_id = ? AND status != 'scanned'
                    ) THEN 'partial'
                    ELSE 'completed'
                END,
                failed_channels = (
                    SELECT COUNT(*) FROM message_import_channels
                    WHERE guild_id = ? AND status = 'failed'
                ),
                updated_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                """,
                (guild_id, guild_id, guild_id),
            )

    async def mark_import_interrupted(self, guild_id: int) -> None:
        """Deja el trabajo reanudable cuando el almacenamiento falla."""
        await self._run(self._mark_import_interrupted_sync, guild_id)

    def _mark_import_interrupted_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE message_imports
                SET status = 'interrupted', updated_at = CURRENT_TIMESTAMP
                WHERE guild_id = ? AND status = 'running'
                """,
                (guild_id,),
            )

    async def import_status(self, guild_id: int) -> ImportStatus | None:
        """Devuelve el estado de importación más reciente del servidor."""
        result = await self._run(self._import_status_sync, guild_id)
        return result  # type: ignore[return-value]

    def _import_status_sync(self, guild_id: int) -> ImportStatus | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, total_channels, scanned_channels, failed_channels, cutoff_id,
                    (SELECT COALESCE(SUM(messages_scanned), 0)
                     FROM message_import_channels WHERE guild_id = ?) AS messages_scanned,
                    (SELECT COALESCE(SUM(messages_counted), 0)
                     FROM message_import_channels WHERE guild_id = ?) AS messages_counted
                FROM message_imports WHERE guild_id = ?
                """,
                (guild_id, guild_id, guild_id),
            ).fetchone()
            if row is None:
                return None
            return ImportStatus(
                status=row["status"],
                total_channels=row["total_channels"],
                scanned_channels=row["scanned_channels"],
                failed_channels=row["failed_channels"],
                cutoff_id=row["cutoff_id"],
                messages_scanned=row["messages_scanned"],
                messages_counted=row["messages_counted"],
            )

    async def record_live_message(self, guild_id: int, user_id: int, message_id: int) -> None:
        """Cuenta un mensaje nuevo sin mezclarlo con el periodo histórico."""
        await self._run(self._record_live_message_sync, guild_id, user_id, message_id)

    def _record_live_message_sync(self, guild_id: int, user_id: int, message_id: int) -> None:
        with self._connect() as connection:
            state = connection.execute(
                """
                SELECT status, cutoff_id FROM message_imports WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
            if (
                state is not None
                and state["status"] == "running"
                and message_id < state["cutoff_id"]
            ):
                return
            connection.execute(
                """
                INSERT INTO member_message_counts (guild_id, user_id, live_count)
                VALUES (?, ?, 1)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    live_count = live_count + 1
                """,
                (guild_id, user_id),
            )

    async def message_count(self, guild_id: int, user_id: int) -> int:
        """Devuelve los mensajes históricos más los registrados en vivo."""
        result = await self._run(self._message_count_sync, guild_id, user_id)
        return int(result)

    def _message_count_sync(self, guild_id: int, user_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT historical_count + live_count AS total
                FROM member_message_counts
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
            return int(row["total"]) if row else 0

    async def top_message_counts(
        self, guild_id: int, limit: int, offset: int
    ) -> list[tuple[int, int]]:
        """Devuelve los totales del servidor ordenados de forma estable."""
        result = await self._run(self._top_message_counts_sync, guild_id, limit, offset)
        return result  # type: ignore[return-value]

    def _top_message_counts_sync(
        self, guild_id: int, limit: int, offset: int
    ) -> list[tuple[int, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, historical_count + live_count AS total
                FROM member_message_counts
                WHERE guild_id = ?
                ORDER BY total DESC, user_id ASC
                LIMIT ? OFFSET ?
                """,
                (guild_id, limit, offset),
            ).fetchall()
            return [(int(row["user_id"]), int(row["total"])) for row in rows]

    async def delete_guild_data(self, guild_id: int) -> None:
        """Elimina el recuento y las importaciones cuando el bot deja un servidor."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM message_imports WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM member_message_counts WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM guild_level_settings WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM member_levels WHERE guild_id = ?", (guild_id,))

    async def enable_levels(
        self, guild_id: int, historical_xp_per_message: int
    ) -> tuple[bool, int]:
        """Activa niveles y convierte el historial a XP una sola vez.

        La activación se permite tras una importación histórica terminada,
        completa o parcial (con canales que no se pudieron leer).
        Cada mensaje histórico equivale al valor medio de la XP por mensaje.

        Returns:
            Una tupla con (activado, número de perfiles inicializados).
        """
        if historical_xp_per_message <= 0:
            raise ValueError("La XP histórica por mensaje debe ser positiva.")
        return await self._run(self._enable_levels_sync, guild_id, historical_xp_per_message)

    def _enable_levels_sync(
        self, guild_id: int, historical_xp_per_message: int
    ) -> tuple[bool, int]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            status = connection.execute(
                "SELECT status FROM message_imports WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            # Una importación parcial también vale: basta un canal sin permiso
            # de leer el historial para que no sea completa, y eso no puede
            # dejar los niveles apagados para siempre. Los canales que falten
            # se suman después con su XP (`_save_channel_counts_sync`).
            if status is None or status["status"] not in {"completed", "partial"}:
                connection.rollback()
                return False, 0

            connection.execute(
                "INSERT OR IGNORE INTO guild_level_settings (guild_id) VALUES (?)",
                (guild_id,),
            )
            settings = connection.execute(
                """
                SELECT historical_seeded FROM guild_level_settings WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
            seeded_profiles = 0
            if not settings["historical_seeded"]:
                historical_counts = connection.execute(
                    """
                    SELECT user_id, historical_count + live_count AS message_count
                    FROM member_message_counts
                    WHERE guild_id = ? AND historical_count + live_count > 0
                    """,
                    (guild_id,),
                ).fetchall()
                connection.executemany(
                    """
                    INSERT INTO member_levels (guild_id, user_id, total_xp)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        total_xp = total_xp + excluded.total_xp
                    """,
                    [
                        (
                            guild_id,
                            row["user_id"],
                            row["message_count"] * historical_xp_per_message,
                        )
                        for row in historical_counts
                    ],
                )
                seeded_profiles = len(historical_counts)
                connection.execute(
                    """
                    UPDATE guild_level_settings
                    SET historical_seeded = 1 WHERE guild_id = ?
                    """,
                    (guild_id,),
                )

            connection.execute(
                "UPDATE guild_level_settings SET enabled = 1 WHERE guild_id = ?",
                (guild_id,),
            )
            connection.commit()
            return True, seeded_profiles

    async def disable_levels(self, guild_id: int) -> None:
        """Desactiva la concesión de experiencia sin borrar el progreso guardado."""
        await self._run(self._disable_levels_sync, guild_id)

    def _disable_levels_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE guild_level_settings SET enabled = 0 WHERE guild_id = ?
                """,
                (guild_id,),
            )

    async def level_settings(self, guild_id: int) -> LevelSettings | None:
        """Devuelve la configuración de niveles del servidor."""
        return await self._run(self._level_settings_sync, guild_id)

    def _level_settings_sync(self, guild_id: int) -> LevelSettings | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT enabled, cooldown_seconds, announce_channel_id, historical_seeded
                FROM guild_level_settings WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
            if row is None:
                return None
            return LevelSettings(
                enabled=bool(row["enabled"]),
                cooldown_seconds=int(row["cooldown_seconds"]),
                announce_channel_id=row["announce_channel_id"],
                historical_seeded=bool(row["historical_seeded"]),
            )

    async def set_level_announce_channel(self, guild_id: int, channel_id: int | None) -> None:
        """Establece o elimina el canal de anuncios de subida de nivel."""
        await self._run(self._set_level_announce_channel_sync, guild_id, channel_id)

    def _set_level_announce_channel_sync(self, guild_id: int, channel_id: int | None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO guild_level_settings (guild_id) VALUES (?)",
                (guild_id,),
            )
            connection.execute(
                """
                UPDATE guild_level_settings
                SET announce_channel_id = ? WHERE guild_id = ?
                """,
                (channel_id, guild_id),
            )

    async def set_level_cooldown(self, guild_id: int, cooldown_seconds: int) -> None:
        """Configura el cooldown por servidor dentro del rango permitido."""
        if not 10 <= cooldown_seconds <= 3600:
            raise ValueError("El cooldown debe estar entre 10 y 3600 segundos.")
        await self._run(self._set_level_cooldown_sync, guild_id, cooldown_seconds)

    def _set_level_cooldown_sync(self, guild_id: int, cooldown_seconds: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO guild_level_settings (guild_id) VALUES (?)",
                (guild_id,),
            )
            connection.execute(
                """
                UPDATE guild_level_settings SET cooldown_seconds = ?
                WHERE guild_id = ?
                """,
                (cooldown_seconds, guild_id),
            )

    async def grant_activity(
        self,
        guild_id: int,
        user_ids: Sequence[int],
        decide: Callable[[int, MemberActivity, int], MemberActivity | None],
    ) -> dict[int, LevelAward]:
        """Aplica una regla de XP a uno o varios miembros en una sola transacción.

        La regla la pone `bot.services.levels`: `decide` recibe el id del
        miembro, su estado actual y el enfriamiento del servidor, y devuelve el
        estado nuevo o `None` si no toca dar nada. Se evalúa dentro de la
        transacción para que dos eventos simultáneos no se pisen. Se agrupan
        varios miembros (p. ej. todo un canal de voz) para escribir una sola
        vez por minuto.

        Returns:
            Resultado por miembro al que se le dio XP. Vacío si los niveles
            del servidor están desactivados.
        """
        return await self._run(self._grant_activity_sync, guild_id, tuple(user_ids), decide)

    def _grant_activity_sync(
        self,
        guild_id: int,
        user_ids: tuple[int, ...],
        decide: Callable[[int, MemberActivity, int], MemberActivity | None],
    ) -> dict[int, LevelAward]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            settings = connection.execute(
                """
                SELECT enabled, cooldown_seconds, announce_channel_id
                FROM guild_level_settings WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
            if settings is None or not settings["enabled"]:
                connection.rollback()
                return {}

            cooldown = int(settings["cooldown_seconds"])
            awards: dict[int, LevelAward] = {}
            for user_id in user_ids:
                row = connection.execute(
                    """
                    SELECT total_xp, last_awarded_at, last_active_day, streak_days,
                           reaction_day, reaction_xp
                    FROM member_levels WHERE guild_id = ? AND user_id = ?
                    """,
                    (guild_id, user_id),
                ).fetchone()
                previous = (
                    MemberActivity(
                        total_xp=int(row["total_xp"]),
                        last_awarded_at=row["last_awarded_at"],
                        last_active_day=row["last_active_day"],
                        streak_days=int(row["streak_days"]),
                        reaction_day=row["reaction_day"],
                        reaction_xp=int(row["reaction_xp"]),
                    )
                    if row is not None
                    else MemberActivity()
                )
                updated = decide(user_id, previous, cooldown)
                if updated is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO member_levels (
                        guild_id, user_id, total_xp, last_awarded_at, last_active_day,
                        streak_days, reaction_day, reaction_xp
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        total_xp = excluded.total_xp,
                        last_awarded_at = excluded.last_awarded_at,
                        last_active_day = excluded.last_active_day,
                        streak_days = excluded.streak_days,
                        reaction_day = excluded.reaction_day,
                        reaction_xp = excluded.reaction_xp
                    """,
                    (
                        guild_id,
                        user_id,
                        updated.total_xp,
                        updated.last_awarded_at,
                        updated.last_active_day,
                        updated.streak_days,
                        updated.reaction_day,
                        updated.reaction_xp,
                    ),
                )
                awards[user_id] = LevelAward(
                    previous_xp=previous.total_xp,
                    total_xp=updated.total_xp,
                    cooldown_seconds=cooldown,
                    announce_channel_id=settings["announce_channel_id"],
                    streak_days=updated.streak_days,
                )
            connection.commit()
            return awards

    async def enabled_guild_ids(self) -> set[int]:
        """Servidores con el sistema de niveles activo."""
        return await self._run(self._enabled_guild_ids_sync)

    def _enabled_guild_ids_sync(self) -> set[int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT guild_id FROM guild_level_settings WHERE enabled = 1"
            ).fetchall()
            return {int(row["guild_id"]) for row in rows}

    async def member_xp(self, guild_id: int, user_id: int) -> int:
        """Devuelve XP acumulada por un miembro; los perfiles ausentes empiezan en cero."""
        result = await self._run(self._member_xp_sync, guild_id, user_id)
        return int(result)

    def _member_xp_sync(self, guild_id: int, user_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT total_xp FROM member_levels WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return int(row["total_xp"]) if row else 0

    async def member_activity(self, guild_id: int, user_id: int) -> MemberActivity:
        """XP y racha de un miembro; los perfiles ausentes empiezan en cero."""
        return await self._run(self._member_activity_sync, guild_id, user_id)

    def _member_activity_sync(self, guild_id: int, user_id: int) -> MemberActivity:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT total_xp, last_awarded_at, last_active_day, streak_days,
                       reaction_day, reaction_xp
                FROM member_levels WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
        if row is None:
            return MemberActivity()
        return MemberActivity(
            total_xp=int(row["total_xp"]),
            last_awarded_at=row["last_awarded_at"],
            last_active_day=row["last_active_day"],
            streak_days=int(row["streak_days"]),
            reaction_day=row["reaction_day"],
            reaction_xp=int(row["reaction_xp"]),
        )

    async def level_leaderboard(
        self, guild_id: int, limit: int, offset: int
    ) -> list[tuple[int, int]]:
        """Devuelve XP por miembro de un servidor, ordenado de forma estable."""
        return await self._run(self._level_leaderboard_sync, guild_id, limit, offset)

    def _level_leaderboard_sync(
        self, guild_id: int, limit: int, offset: int
    ) -> list[tuple[int, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, total_xp FROM member_levels
                WHERE guild_id = ?
                ORDER BY total_xp DESC, user_id ASC
                LIMIT ? OFFSET ?
                """,
                (guild_id, limit, offset),
            ).fetchall()
            return [(int(row["user_id"]), int(row["total_xp"])) for row in rows]

    async def member_count_in_guild(self, guild_id: int) -> int:
        """Devuelve cuántos perfiles de nivel existen en el servidor."""
        return await self._run(self._member_count_in_guild_sync, guild_id)

    def _member_count_in_guild_sync(self, guild_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM member_levels WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            return int(row["total"])
