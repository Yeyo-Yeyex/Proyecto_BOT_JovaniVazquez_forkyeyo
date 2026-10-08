"""Persistencia de las máquinas de Botes (de momento, `volcan`).

Tabla (en el mismo archivo SQLite que el resto del bot):

- `hold_win_meters`: por servidor, miembro y máquina, las monedas de cada
  maletín con la suma de sus apuestas y el valor actual de los botes Mini y
  Major (en puntos, centésimas de la apuesta). El Grand es fijo y no se guarda.

Se guarda para que un maletín a medias siga ahí mañana: es lo que hace volver.
Las reglas viven en `bot.services.hold_win`; el dinero, en la economía.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.hold_win import Case, Meters, Tier

T = TypeVar("T")

_COLUMNS = {Tier.GREEN: "green", Tier.BLUE: "blue", Tier.RED: "red"}


class HoldWinRepository:
    """Acceso SQLite a los maletines y botes de cada jugador.

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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS hold_win_meters (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    theme TEXT NOT NULL,
                    green INTEGER NOT NULL DEFAULT 0,
                    green_stake INTEGER NOT NULL DEFAULT 0,
                    blue INTEGER NOT NULL DEFAULT 0,
                    blue_stake INTEGER NOT NULL DEFAULT 0,
                    red INTEGER NOT NULL DEFAULT 0,
                    red_stake INTEGER NOT NULL DEFAULT 0,
                    mini INTEGER NOT NULL,
                    major INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, theme)
                );
                """
            )

    async def load(self, guild_id: int, user_id: int, theme: str) -> Meters:
        """Maletines y botes de un jugador en una máquina (vacíos si es nuevo)."""
        return await self._run(self._load_sync, guild_id, user_id, theme)

    def _load_sync(self, guild_id: int, user_id: int, theme: str) -> Meters:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hold_win_meters WHERE guild_id = ? AND user_id = ? AND theme = ?",
                (guild_id, user_id, theme),
            ).fetchone()
        meters = Meters()
        if row is None:
            return meters
        for tier, column in _COLUMNS.items():
            meters.cases[tier] = Case(tier, int(row[column]), int(row[f"{column}_stake"]))
        meters.mini = int(row["mini"])
        meters.major = int(row["major"])
        return meters

    async def save(self, guild_id: int, user_id: int, theme: str, meters: Meters) -> None:
        """Guarda los maletines y botes de un jugador en una máquina."""
        await self._run(self._save_sync, guild_id, user_id, theme, meters)

    def _save_sync(self, guild_id: int, user_id: int, theme: str, meters: Meters) -> None:
        values = []
        for tier in _COLUMNS:
            case = meters.cases[tier]
            values += [case.coins, case.stake_sum]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hold_win_meters
                    (guild_id, user_id, theme, green, green_stake, blue, blue_stake,
                     red, red_stake, mini, major)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id, theme) DO UPDATE SET
                    green = excluded.green, green_stake = excluded.green_stake,
                    blue = excluded.blue, blue_stake = excluded.blue_stake,
                    red = excluded.red, red_stake = excluded.red_stake,
                    mini = excluded.mini, major = excluded.major
                """,
                (guild_id, user_id, theme, *values, meters.mini, meters.major),
            )

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo guardado de un servidor (al salir el bot de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM hold_win_meters WHERE guild_id = ?", (guild_id,))
