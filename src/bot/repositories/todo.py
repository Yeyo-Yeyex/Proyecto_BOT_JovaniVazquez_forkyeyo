"""Persistencia de la lista de tareas (`lista`).

Tablas (en el mismo archivo SQLite que el resto del bot):

- `todo_tasks`: tareas pendientes de cada servidor. Al tacharlas se borran:
  la lista es de cosas por hacer, no un archivo.
- `todo_boards`: último mensaje del bot con la lista en cada servidor, para
  borrarlo al publicar uno nuevo y que solo haya una lista a la vista.

Las reglas (prioridades, límites, orden) viven en `bot.services.todo`.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from bot.services.todo import MAX_TASKS, Priority, Task, TaskError

T = TypeVar("T")


class TodoRepository:
    """Acceso SQLite a la lista de tareas.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

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
                CREATE TABLE IF NOT EXISTS todo_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 2),
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS todo_tasks_guild ON todo_tasks (guild_id);

                CREATE TABLE IF NOT EXISTS todo_boards (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL
                );
                """
            )

    @staticmethod
    def _task(row: sqlite3.Row) -> Task:
        return Task(
            id=int(row["id"]),
            guild_id=int(row["guild_id"]),
            author_id=int(row["author_id"]),
            text=str(row["text"]),
            priority=Priority(int(row["priority"])),
            created_at=float(row["created_at"]),
        )

    async def add_task(self, guild_id: int, author_id: int, text: str, priority: Priority) -> Task:
        """Apunta una tarea.

        Raises:
            TaskError: Si el servidor ya tiene `MAX_TASKS` pendientes.
        """
        return await self._run(self._add_task_sync, guild_id, author_id, text, priority)

    def _add_task_sync(self, guild_id: int, author_id: int, text: str, priority: Priority) -> Task:
        now = time.time()
        with self._connect() as connection:
            # Cuenta e inserta en la misma transacción para que dos `lista` a la
            # vez no pasen las dos del tope.
            connection.execute("BEGIN IMMEDIATE")
            (count,) = connection.execute(
                "SELECT COUNT(*) FROM todo_tasks WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            if count >= MAX_TASKS:
                raise TaskError(
                    f"La lista ya tiene {MAX_TASKS} tareas. Tacha alguna antes de apuntar más."
                )
            cursor = connection.execute(
                "INSERT INTO todo_tasks (guild_id, author_id, text, priority, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (guild_id, author_id, text, int(priority), now),
            )
            assert cursor.lastrowid is not None
            return Task(cursor.lastrowid, guild_id, author_id, text, priority, now)

    async def list_tasks(self, guild_id: int) -> list[Task]:
        """Tareas pendientes del servidor, sin ordenar (ver `services.todo.sort_tasks`)."""
        return await self._run(self._list_tasks_sync, guild_id)

    def _list_tasks_sync(self, guild_id: int) -> list[Task]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM todo_tasks WHERE guild_id = ?", (guild_id,)
            ).fetchall()
            return [self._task(row) for row in rows]

    async def get_tasks(self, guild_id: int, task_ids: list[int]) -> list[Task]:
        """Las tareas pedidas que sigan pendientes en el servidor."""
        return await self._run(self._get_tasks_sync, guild_id, task_ids)

    def _get_tasks_sync(self, guild_id: int, task_ids: list[int]) -> list[Task]:
        if not task_ids:
            return []
        placeholders = ", ".join("?" for _ in task_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM todo_tasks WHERE guild_id = ? AND id IN ({placeholders})",
                (guild_id, *task_ids),
            ).fetchall()
            return [self._task(row) for row in rows]

    async def remove_tasks(self, guild_id: int, task_ids: list[int]) -> list[int]:
        """Tacha (borra) las tareas indicadas.

        Returns:
            Los ids que de verdad se borraron; si otro las tachó antes, no salen.
        """
        return await self._run(self._remove_tasks_sync, guild_id, task_ids)

    def _remove_tasks_sync(self, guild_id: int, task_ids: list[int]) -> list[int]:
        removed = []
        with self._connect() as connection:
            for task_id in task_ids:
                cursor = connection.execute(
                    "DELETE FROM todo_tasks WHERE guild_id = ? AND id = ?", (guild_id, task_id)
                )
                if cursor.rowcount:
                    removed.append(task_id)
        return removed

    async def get_board(self, guild_id: int) -> tuple[int, int] | None:
        """`(canal, mensaje)` de la última lista publicada, si la hay."""
        return await self._run(self._get_board_sync, guild_id)

    def _get_board_sync(self, guild_id: int) -> tuple[int, int] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT channel_id, message_id FROM todo_boards WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            return (int(row["channel_id"]), int(row["message_id"])) if row else None

    async def set_board(self, guild_id: int, channel_id: int, message_id: int) -> None:
        """Recuerda el mensaje con la lista recién publicada."""
        await self._run(self._set_board_sync, guild_id, channel_id, message_id)

    def _set_board_sync(self, guild_id: int, channel_id: int, message_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO todo_boards (guild_id, channel_id, message_id) VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id, message_id = excluded.message_id
                """,
                (guild_id, channel_id, message_id),
            )

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra la lista de un servidor cuando el bot sale de él."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            for table in ("todo_tasks", "todo_boards"):
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
