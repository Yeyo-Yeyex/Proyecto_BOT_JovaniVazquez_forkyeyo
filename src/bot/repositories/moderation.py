"""Persistencia del historial de sanciones y de las notas de moderador (`admin`).

Tablas (en el mismo archivo SQLite que el resto del bot):

- `mod_actions`: cada sanción que pasa por el bot (`callar`, `hablar`, `echar`,
  `banear`, `indultar`, desde su comando o desde el panel `admin`): a quién, quién,
  el motivo y, si es un aislamiento, cuánto dura. Lo que se haga a mano desde
  Discord no pasa por aquí.
- `mod_notes`: notas privadas de los administradores sobre un miembro. Solo se
  enseñan en el panel `admin`, que solo abren administradores.
- `blocked_commands`: los comandos que un administrador ha bloqueado con `bloquear`
  (un fallo o un exploit por arreglar). Sobreviven a los reinicios hasta que se
  desbloquean.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Sanction:
    """Una sanción del historial."""

    id: int
    user_id: int
    actor_id: int
    kind: str
    reason: str | None
    seconds: int | None
    created_at: float


@dataclass(frozen=True, slots=True)
class Note:
    """Una nota de moderador."""

    id: int
    user_id: int
    author_id: int
    text: str
    created_at: float


class ModerationRepository:
    """Acceso SQLite al historial de sanciones y a las notas de moderador.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mod_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    actor_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    reason TEXT,
                    seconds INTEGER,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mod_actions_member
                    ON mod_actions (guild_id, user_id);

                CREATE TABLE IF NOT EXISTS mod_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mod_notes_member ON mod_notes (guild_id, user_id);

                CREATE TABLE IF NOT EXISTS blocked_commands (
                    guild_id INTEGER NOT NULL,
                    command TEXT NOT NULL,
                    PRIMARY KEY (guild_id, command)
                );
                """
            )

    async def record(
        self,
        guild_id: int,
        user_id: int,
        actor_id: int,
        kind: str,
        reason: str | None = None,
        seconds: int | None = None,
    ) -> None:
        """Apunta una sanción en el historial."""
        await self._run(
            self._record_sync, guild_id, user_id, actor_id, kind, reason, seconds, time.time()
        )

    def _record_sync(self, *values: object) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mod_actions
                    (guild_id, user_id, actor_id, kind, reason, seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    async def sanctions(
        self, guild_id: int, user_id: int | None = None, *, limit: int = 10
    ) -> list[Sanction]:
        """Últimas sanciones de un miembro (o de todo el servidor), la más nueva antes."""
        return await self._run(self._sanctions_sync, guild_id, user_id, limit)

    def _sanctions_sync(self, guild_id: int, user_id: int | None, limit: int) -> list[Sanction]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, actor_id, kind, reason, seconds, created_at
                FROM mod_actions
                WHERE guild_id = ? AND (? IS NULL OR user_id = ?)
                ORDER BY id DESC LIMIT ?
                """,
                (guild_id, user_id, user_id, limit),
            ).fetchall()
        return [Sanction(**dict(row)) for row in rows]

    async def sanction_counts(self, guild_id: int, user_id: int) -> dict[str, int]:
        """Cuántas sanciones de cada tipo tiene un miembro."""
        return await self._run(self._sanction_counts_sync, guild_id, user_id)

    def _sanction_counts_sync(self, guild_id: int, user_id: int) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT kind, COUNT(*) AS total FROM mod_actions
                WHERE guild_id = ? AND user_id = ? GROUP BY kind
                """,
                (guild_id, user_id),
            ).fetchall()
        return {str(row["kind"]): int(row["total"]) for row in rows}

    async def add_note(self, guild_id: int, user_id: int, author_id: int, text: str) -> None:
        """Apunta una nota de moderador sobre un miembro."""
        await self._run(self._add_note_sync, guild_id, user_id, author_id, text, time.time())

    def _add_note_sync(self, *values: object) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mod_notes (guild_id, user_id, author_id, text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                values,
            )

    async def notes(self, guild_id: int, user_id: int, *, limit: int = 10) -> list[Note]:
        """Últimas notas sobre un miembro, la más nueva antes."""
        return await self._run(self._notes_sync, guild_id, user_id, limit)

    def _notes_sync(self, guild_id: int, user_id: int, limit: int) -> list[Note]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, author_id, text, created_at FROM mod_notes
                WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
        return [Note(**dict(row)) for row in rows]

    async def delete_note(self, guild_id: int, note_id: int) -> bool:
        """Borra una nota del servidor; `False` si no existía."""
        return await self._run(self._delete_note_sync, guild_id, note_id)

    def _delete_note_sync(self, guild_id: int, note_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM mod_notes WHERE guild_id = ? AND id = ?", (guild_id, note_id)
            )
            return cursor.rowcount > 0

    async def blocked_commands(self) -> dict[int, set[str]]:
        """Los comandos bloqueados de cada servidor (se leen una vez, al arrancar)."""
        return await self._run(self._blocked_commands_sync)

    def _blocked_commands_sync(self) -> dict[int, set[str]]:
        blocked: dict[int, set[str]] = {}
        with self._connect() as connection:
            for row in connection.execute("SELECT guild_id, command FROM blocked_commands"):
                blocked.setdefault(int(row["guild_id"]), set()).add(str(row["command"]))
        return blocked

    async def set_blocked(self, guild_id: int, command: str, blocked: bool) -> None:
        """Bloquea (`blocked=True`) o desbloquea un comando en el servidor."""
        await self._run(self._set_blocked_sync, guild_id, command, blocked)

    def _set_blocked_sync(self, guild_id: int, command: str, blocked: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO blocked_commands (guild_id, command) VALUES (?, ?)"
                if blocked
                else "DELETE FROM blocked_commands WHERE guild_id = ? AND command = ?",
                (guild_id, command),
            )
