"""Persistencia de la última actividad de cada miembro (`actividad`).

Tabla `member_last_seen` (en el mismo archivo SQLite que el resto del bot): una
fila por servidor y miembro con la última vez que escribió (y en qué canal),
la última vez que entró, salió o cambió de canal de voz (y cuál) y la última
vez que se le vio conectado. Solo marcas de tiempo e IDs de canal: nunca el
contenido de los mensajes.

El cog `Actividad` junta los cambios en memoria y los vuelca aquí cada minuto
(`save`), para no escribir en la base por cada mensaje o cambio de estado.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite

T = TypeVar("T")

#: Columnas que se pueden apuntar; `save` solo acepta estas claves.
FIELDS = ("message_at", "message_channel", "voice_at", "voice_channel", "online_at")


@dataclass(frozen=True, slots=True)
class LastSeen:
    """Última actividad conocida de un miembro; `None` si nunca se vio."""

    message_at: float | None = None
    message_channel: int | None = None
    voice_at: float | None = None
    voice_channel: int | None = None
    online_at: float | None = None


class ActivityRepository:
    """Acceso SQLite a la última actividad de los miembros.

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
        """Crea la tabla si no existe."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS member_last_seen (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    message_at REAL,
                    message_channel INTEGER,
                    voice_at REAL,
                    voice_channel INTEGER,
                    online_at REAL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )

    async def save(self, changes: Mapping[tuple[int, int], Mapping[str, float | int]]) -> None:
        """Apunta los cambios `{(servidor, miembro): {columna: valor}}` en una transacción.

        Las columnas que no vienen se quedan como estaban.

        Raises:
            KeyError: Si alguna columna no está en `FIELDS`.
        """
        rows = []
        for (guild_id, user_id), fields in changes.items():
            unknown = set(fields) - set(FIELDS)
            if unknown:
                raise KeyError(f"Columnas desconocidas: {sorted(unknown)}")
            rows.append((guild_id, user_id, *(fields.get(name) for name in FIELDS)))
        await self._run(self._save_sync, rows)

    def _save_sync(self, rows: list[tuple[object, ...]]) -> None:
        updates = ", ".join(f"{name} = COALESCE(excluded.{name}, {name})" for name in FIELDS)
        with self._connect() as connection:
            connection.executemany(
                f"""
                INSERT INTO member_last_seen (guild_id, user_id, {", ".join(FIELDS)})
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET {updates}
                """,
                rows,
            )

    async def guild(self, guild_id: int) -> dict[int, LastSeen]:
        """Última actividad de cada miembro del servidor que se haya visto alguna vez."""
        return await self._run(self._guild_sync, guild_id)

    def _guild_sync(self, guild_id: int) -> dict[int, LastSeen]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT user_id, {', '.join(FIELDS)} FROM member_last_seen WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()
        return {
            int(row["user_id"]): LastSeen(**{name: row[name] for name in FIELDS}) for row in rows
        }
