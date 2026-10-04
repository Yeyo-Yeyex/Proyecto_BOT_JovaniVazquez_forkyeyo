"""Persistencia de los cumpleaños.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `birthdays`: día y mes de cada miembro, por servidor. Sin año.
- `birthday_celebrations`: cumpleaños ya celebrados (anuncio y regalo), por
  año. Evita pagar dos veces si el bot se reinicia ese día.
- `birthday_greetings`: quién ha felicitado a quién cada año, para pagar
  una sola vez por persona.

Las reglas (qué fecha vale, cuánto se paga) viven en `bot.services.birthdays`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Birthday:
    """Cumpleaños guardado de un miembro."""

    user_id: int
    day: int
    month: int


class BirthdayRepository:
    """Acceso SQLite a los cumpleaños.

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
                CREATE TABLE IF NOT EXISTS birthdays (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31),
                    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS birthday_celebrations (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, year)
                );

                CREATE TABLE IF NOT EXISTS birthday_greetings (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    greeter_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, year, greeter_id)
                );
                """
            )

    async def set_birthday(
        self, guild_id: int, user_id: int, day: int, month: int, *, overwrite: bool
    ) -> bool:
        """Guarda el cumpleaños.

        Args:
            overwrite: Si es `False` y ya había uno guardado, no se cambia.

        Returns:
            `True` si se guardó; `False` si ya existía y no se podía cambiar.
        """
        return await self._run(self._set_birthday_sync, guild_id, user_id, day, month, overwrite)

    def _set_birthday_sync(
        self, guild_id: int, user_id: int, day: int, month: int, overwrite: bool
    ) -> bool:
        conflict = "DO UPDATE SET day = excluded.day, month = excluded.month"
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                INSERT INTO birthdays (guild_id, user_id, day, month) VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) {conflict if overwrite else "DO NOTHING"}
                """,
                (guild_id, user_id, day, month),
            )
            return cursor.rowcount > 0

    async def get_birthday(self, guild_id: int, user_id: int) -> Birthday | None:
        """Cumpleaños de un miembro, si lo ha puesto."""
        return await self._run(self._get_birthday_sync, guild_id, user_id)

    def _get_birthday_sync(self, guild_id: int, user_id: int) -> Birthday | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id, day, month FROM birthdays WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return (
                Birthday(int(row["user_id"]), int(row["day"]), int(row["month"])) if row else None
            )

    async def list_birthdays(self, guild_id: int) -> list[Birthday]:
        """Todos los cumpleaños de un servidor."""
        return await self._run(self._list_birthdays_sync, guild_id)

    def _list_birthdays_sync(self, guild_id: int) -> list[Birthday]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, day, month FROM birthdays WHERE guild_id = ?", (guild_id,)
            ).fetchall()
            return [Birthday(int(r["user_id"]), int(r["day"]), int(r["month"])) for r in rows]

    async def mark_celebrated(self, guild_id: int, user_id: int, year: int) -> bool:
        """Marca el cumpleaños de `year` como celebrado.

        Returns:
            `True` si no estaba celebrado (hay que anunciar y pagar ahora).
        """
        return await self._run(self._insert_once, "birthday_celebrations", guild_id, user_id, year)

    async def add_greeting(self, guild_id: int, user_id: int, year: int, greeter_id: int) -> bool:
        """Registra que `greeter_id` felicita a `user_id` en `year`.

        Returns:
            `True` si es la primera vez (hay que pagar ahora).
        """
        return await self._run(
            self._insert_once, "birthday_greetings", guild_id, user_id, year, greeter_id
        )

    def _insert_once(self, table: str, *values: int) -> bool:
        # `table` sale siempre de una cadena fija del código, nunca del usuario.
        placeholders = ", ".join("?" for _ in values)
        with self._connect() as connection:
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})", values
            )
            return cursor.rowcount > 0

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra los cumpleaños de un servidor cuando el bot sale de él."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            for table in ("birthdays", "birthday_celebrations", "birthday_greetings"):
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
