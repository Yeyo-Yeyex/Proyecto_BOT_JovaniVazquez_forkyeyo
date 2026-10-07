"""Persistencia de las porras: qué porras hay y en qué punto están.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `porras`: una fila por porra, con quién la monta, sobre quién, el juego, la
  propuesta, las jugadas, la apuesta mínima, el estado y, al acabar, la opción
  ganadora o por qué se anuló.
- `porra_streaks`: porras acertadas seguidas por miembro, para los logros (se
  guardan porque las porras son pocas y lentas: una racha en memoria la cortaría
  cualquier reinicio).

El dinero no está aquí: las apuestas y el depósito viven en la economía
(`economy_porra_bets`), que es la que manda. Si el bot se cae a mitad, al volver
las porras sin terminar se anulan y se devuelve lo apostado (`bot.cogs.porras`).
Las reglas, en `bot.services.porras`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from bot.services.porras import Porra, Status, VoidReason

T = TypeVar("T")


class PorraRepository:
    """Acceso SQLite a las porras.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Conexión con transacción que se confirma al salir y se cierra siempre."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                CREATE TABLE IF NOT EXISTS porras (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    opener_id INTEGER NOT NULL,
                    subject_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    proposition TEXT NOT NULL,
                    plays INTEGER NOT NULL CHECK (plays > 0),
                    stake INTEGER NOT NULL CHECK (stake > 0),
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    locked_at REAL,
                    finished_at REAL,
                    outcome INTEGER,
                    void_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS porras_status ON porras (status);

                CREATE TABLE IF NOT EXISTS porra_streaks (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    streak INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )

    async def create(self, porra: Porra) -> int:
        """Guarda una porra nueva y devuelve su id (también lo pone en `porra.id`)."""
        porra.id = await self._run(self._create_sync, porra)
        return porra.id

    def _create_sync(self, porra: Porra) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO porras (guild_id, channel_id, message_id, opener_id, subject_id,
                    game, proposition, plays, stake, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    porra.guild_id,
                    porra.channel_id,
                    porra.message_id,
                    porra.opener_id,
                    porra.subject_id,
                    porra.game,
                    porra.proposition,
                    porra.plays,
                    porra.stake,
                    porra.status.value,
                    porra.created_at,
                ),
            )
            return int(cursor.lastrowid or 0)

    async def save(self, porra: Porra, *, finished_at: float | None = None) -> None:
        """Guarda el estado, el mensaje, el cierre y el desenlace de una porra."""
        await self._run(self._save_sync, porra, finished_at)

    def _save_sync(self, porra: Porra, finished_at: float | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE porras SET message_id = ?, status = ?, locked_at = ?, outcome = ?,
                    void_reason = ?, finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (
                    porra.message_id,
                    porra.status.value,
                    porra.locked_at,
                    porra.outcome,
                    porra.void_reason.value if porra.void_reason else None,
                    finished_at,
                    porra.id,
                ),
            )

    async def unfinished(self) -> list[Porra]:
        """Porras que no están ni resueltas ni anuladas (para recuperarlas al arrancar)."""
        return await self._run(self._unfinished_sync)

    def _unfinished_sync(self) -> list[Porra]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM porras WHERE status NOT IN (?, ?) ORDER BY id",
                (Status.RESOLVED.value, Status.VOID.value),
            ).fetchall()
        return [_porra(row) for row in rows]

    async def bump_streak(self, guild_id: int, user_id: int, *, won: bool) -> int:
        """Suma una porra acertada a la racha (o la corta) y devuelve la racha nueva."""
        return await self._run(self._bump_streak_sync, guild_id, user_id, won)

    def _bump_streak_sync(self, guild_id: int, user_id: int, won: bool) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT streak FROM porra_streaks WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            streak = (int(row["streak"]) if row else 0) + 1 if won else 0
            connection.execute(
                """
                INSERT INTO porra_streaks (guild_id, user_id, streak) VALUES (?, ?, ?)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET streak = excluded.streak
                """,
                (guild_id, user_id, streak),
            )
            return streak

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra las porras de un servidor (al salir el bot de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM porras WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM porra_streaks WHERE guild_id = ?", (guild_id,))


def _porra(row: sqlite3.Row) -> Porra:
    return Porra(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        channel_id=int(row["channel_id"]),
        opener_id=int(row["opener_id"]),
        subject_id=int(row["subject_id"]),
        game=str(row["game"]),
        proposition=str(row["proposition"]),
        plays=int(row["plays"]),
        stake=int(row["stake"]),
        status=Status(row["status"]),
        created_at=float(row["created_at"]),
        locked_at=float(row["locked_at"]) if row["locked_at"] is not None else None,
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
        outcome=int(row["outcome"]) if row["outcome"] is not None else None,
        void_reason=VoidReason(row["void_reason"]) if row["void_reason"] else None,
    )
