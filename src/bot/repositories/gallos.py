"""Persistencia de las peleas de gallos: la ficha del gallo de cada miembro.

Tabla `gallos` (en el mismo archivo SQLite que el resto del bot): por servidor
y miembro, el nombre de su gallo y su historial (`RoosterRecord`: peleas,
victorias, KO, rachas y el último día que peleó). Nada de esto toca las
probabilidades: es la gracia de tener un gallo con historia.

El aspecto del gallo no se guarda aquí, es una skin de la tienda
(`bot.services.skins`). El dinero tampoco: pasa por la economía. Solo se
guardan IDs; todo se borra cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.gallos import Finish, RoosterRecord, after_fight, default_name

T = TypeVar("T")


def _record(row: sqlite3.Row) -> RoosterRecord:
    last_day = str(row["last_day"] or "")
    return RoosterRecord(
        name=str(row["name"]),
        fights=int(row["fights"]),
        wins=int(row["wins"]),
        kos=int(row["kos"]),
        streak=int(row["streak"]),
        best_streak=int(row["best_streak"]),
        day_streak=int(row["day_streak"]),
        last_day=date.fromisoformat(last_day) if last_day else None,
    )


class GallosRepository:
    """Acceso SQLite a la ficha de los gallos.

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
                CREATE TABLE IF NOT EXISTS gallos (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    fights INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    kos INTEGER NOT NULL DEFAULT 0,
                    streak INTEGER NOT NULL DEFAULT 0,
                    best_streak INTEGER NOT NULL DEFAULT 0,
                    day_streak INTEGER NOT NULL DEFAULT 0,
                    last_day TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )

    async def get(self, guild_id: int, user_id: int) -> RoosterRecord:
        """La ficha del gallo de un miembro (la de un gallo nuevo si nunca ha peleado)."""
        return await self._run(self._get_sync, guild_id, user_id)

    def _get_sync(self, guild_id: int, user_id: int) -> RoosterRecord:
        with self._connect() as connection:
            return self._get_in(connection, guild_id, user_id)

    @staticmethod
    def _get_in(connection: sqlite3.Connection, guild_id: int, user_id: int) -> RoosterRecord:
        row = connection.execute(
            "SELECT * FROM gallos WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        return _record(row) if row is not None else RoosterRecord(name=default_name(user_id))

    async def record_fight(
        self, guild_id: int, user_id: int, *, won: bool, finish: Finish, day: date
    ) -> tuple[RoosterRecord, RoosterRecord]:
        """Apunta una pelea en la ficha del gallo.

        Returns:
            La ficha de antes y la de después.
        """
        return await self._run(self._record_fight_sync, guild_id, user_id, won, finish, day)

    def _record_fight_sync(
        self, guild_id: int, user_id: int, won: bool, finish: Finish, day: date
    ) -> tuple[RoosterRecord, RoosterRecord]:
        with self._connect() as connection:
            before = self._get_in(connection, guild_id, user_id)
            after = after_fight(before, won=won, finish=finish, day=day)
            self._save_in(connection, guild_id, user_id, after)
        return before, after

    async def rename(self, guild_id: int, user_id: int, name: str) -> RoosterRecord:
        """Le cambia el nombre al gallo (ya validado con `gallos.clean_name`)."""
        return await self._run(self._rename_sync, guild_id, user_id, name)

    def _rename_sync(self, guild_id: int, user_id: int, name: str) -> RoosterRecord:
        with self._connect() as connection:
            record = self._get_in(connection, guild_id, user_id)
            renamed = replace(record, name=name)
            self._save_in(connection, guild_id, user_id, renamed)
        return renamed

    @staticmethod
    def _save_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, record: RoosterRecord
    ) -> None:
        connection.execute(
            """
            INSERT INTO gallos
                (guild_id, user_id, name, fights, wins, kos, streak, best_streak,
                 day_streak, last_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                name = excluded.name, fights = excluded.fights, wins = excluded.wins,
                kos = excluded.kos, streak = excluded.streak,
                best_streak = excluded.best_streak, day_streak = excluded.day_streak,
                last_day = excluded.last_day
            """,
            (
                guild_id,
                user_id,
                record.name,
                record.fights,
                record.wins,
                record.kos,
                record.streak,
                record.best_streak,
                record.day_streak,
                record.last_day.isoformat() if record.last_day else None,
            ),
        )

    async def ranking(self, guild_id: int, limit: int = 10) -> list[tuple[int, RoosterRecord]]:
        """Los gallos con más victorias del servidor: `(user_id, ficha)`."""
        return await self._run(self._ranking_sync, guild_id, limit)

    def _ranking_sync(self, guild_id: int, limit: int) -> list[tuple[int, RoosterRecord]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM gallos WHERE guild_id = ? AND fights > 0
                ORDER BY wins DESC, best_streak DESC, fights ASC LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
        return [(int(row["user_id"]), _record(row)) for row in rows]

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo guardado de un servidor (al salir el bot de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM gallos WHERE guild_id = ?", (guild_id,))
