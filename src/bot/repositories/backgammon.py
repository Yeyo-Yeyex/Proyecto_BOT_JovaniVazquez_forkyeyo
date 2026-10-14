"""Persistencia del backgammon: la ficha de cada miembro y lo que lleva de los retos del día.

Dos tablas en el mismo archivo SQLite que el resto del bot:

- `backgammon_players`: por servidor y miembro, su historial (`PlayerRecord`:
  partidas, victorias, derrotas, gammons y backgammons ganados, rachas, días
  seguidos y el nivel más alto de Jovani al que ha ganado).
- `backgammon_daily`: lo que lleva cada miembro de cada reto del día y si ya lo
  cumplió (y cobró). Las filas de días pasados no se usan; se borran al apuntar
  una partida de un día nuevo, así la tabla no crece.

Qué retos tiene cada uno no se guarda: sale de `backgammon.daily_challenges`. El
dinero tampoco: pasa por la economía. Solo se guardan IDs; todo se borra cuando
el bot sale del servidor (`forget_guild`).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.backgammon import GameEvent, PlayerRecord, after_game

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class GameRecord:
    """Lo que cambió al apuntar una partida.

    Attributes:
        before: La ficha de antes.
        after: La de después.
        completed: Retos que se acaban de cumplir con esta partida.
        progress: Lo que lleva de cada reto de hoy, tras la partida.
    """

    before: PlayerRecord
    after: PlayerRecord
    completed: tuple[str, ...]
    progress: Mapping[str, int]


def _record(row: sqlite3.Row | None) -> PlayerRecord:
    if row is None:
        return PlayerRecord()
    last_day = str(row["last_day"] or "")
    return PlayerRecord(
        games=int(row["games"]),
        wins=int(row["wins"]),
        losses=int(row["losses"]),
        gammons=int(row["gammons"]),
        backgammons=int(row["backgammons"]),
        streak=int(row["streak"]),
        best_streak=int(row["best_streak"]),
        day_streak=int(row["day_streak"]),
        last_day=date.fromisoformat(last_day) if last_day else None,
        best_level=int(row["best_level"]),
    )


class BackgammonRepository:
    """Acceso SQLite a las fichas y los retos del backgammon.

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backgammon_players (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    games INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    gammons INTEGER NOT NULL DEFAULT 0,
                    backgammons INTEGER NOT NULL DEFAULT 0,
                    streak INTEGER NOT NULL DEFAULT 0,
                    best_streak INTEGER NOT NULL DEFAULT 0,
                    day_streak INTEGER NOT NULL DEFAULT 0,
                    last_day TEXT,
                    best_level INTEGER NOT NULL DEFAULT -1,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backgammon_daily (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    challenge TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, day, challenge)
                )
                """
            )

    async def get(self, guild_id: int, user_id: int) -> PlayerRecord:
        """La ficha de un miembro (vacía si nunca ha jugado)."""
        return await self._run(self._get_sync, guild_id, user_id)

    def _get_sync(self, guild_id: int, user_id: int) -> PlayerRecord:
        with self._connect() as connection:
            return self._get_in(connection, guild_id, user_id)

    @staticmethod
    def _get_in(connection: sqlite3.Connection, guild_id: int, user_id: int) -> PlayerRecord:
        row = connection.execute(
            "SELECT * FROM backgammon_players WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return _record(row)

    async def daily(self, guild_id: int, user_id: int, day: date) -> dict[str, tuple[int, bool]]:
        """Lo que lleva de cada reto de `day`: clave → (progreso, cumplido)."""
        return await self._run(self._daily_sync, guild_id, user_id, day)

    def _daily_sync(self, guild_id: int, user_id: int, day: date) -> dict[str, tuple[int, bool]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT challenge, progress, done FROM backgammon_daily "
                "WHERE guild_id = ? AND user_id = ? AND day = ?",
                (guild_id, user_id, day.isoformat()),
            ).fetchall()
        return {str(r["challenge"]): (int(r["progress"]), bool(r["done"])) for r in rows}

    async def record_game(
        self,
        guild_id: int,
        user_id: int,
        event: GameEvent,
        *,
        day: date,
        progress: Mapping[str, int],
        goals: Mapping[str, int],
    ) -> GameRecord:
        """Apunta una partida: ficha, racha y retos del día, todo en una transacción.

        Args:
            event: La partida, vista por este miembro.
            day: El día local de la partida.
            progress: Lo que suma a cada reto de hoy.
            goals: La meta de cada reto de hoy, para saber cuáles se cumplen.

        Returns:
            La ficha de antes y la de después, los retos recién cumplidos y lo que
            lleva de cada reto.
        """
        return await self._run(
            self._record_game_sync, guild_id, user_id, event, day, dict(progress), dict(goals)
        )

    def _record_game_sync(
        self,
        guild_id: int,
        user_id: int,
        event: GameEvent,
        day: date,
        progress: dict[str, int],
        goals: dict[str, int],
    ) -> GameRecord:
        today = day.isoformat()
        with self._connect() as connection:
            before = self._get_in(connection, guild_id, user_id)
            after = after_game(before, event, day)
            if before.last_day != day:
                # Día nuevo: lo de días pasados ya no sirve.
                connection.execute(
                    "DELETE FROM backgammon_daily WHERE guild_id = ? AND user_id = ? AND day < ?",
                    (guild_id, user_id, today),
                )
            connection.execute(
                """
                INSERT INTO backgammon_players
                    (guild_id, user_id, games, wins, losses, gammons, backgammons, streak,
                     best_streak, day_streak, last_day, best_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                    games = excluded.games, wins = excluded.wins, losses = excluded.losses,
                    gammons = excluded.gammons, backgammons = excluded.backgammons,
                    streak = excluded.streak,
                    best_streak = excluded.best_streak, day_streak = excluded.day_streak,
                    last_day = excluded.last_day, best_level = excluded.best_level
                """,
                (
                    guild_id,
                    user_id,
                    after.games,
                    after.wins,
                    after.losses,
                    after.gammons,
                    after.backgammons,
                    after.streak,
                    after.best_streak,
                    after.day_streak,
                    today,
                    after.best_level,
                ),
            )
            completed: list[str] = []
            for key, amount in progress.items():
                row = connection.execute(
                    "SELECT progress, done FROM backgammon_daily "
                    "WHERE guild_id = ? AND user_id = ? AND day = ? AND challenge = ?",
                    (guild_id, user_id, today, key),
                ).fetchone()
                current, done = (int(row["progress"]), bool(row["done"])) if row else (0, False)
                current += amount
                newly = not done and current >= goals.get(key, current + 1)
                connection.execute(
                    """
                    INSERT INTO backgammon_daily (guild_id, user_id, day, challenge, progress, done)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id, day, challenge) DO UPDATE SET
                        progress = excluded.progress, done = excluded.done
                    """,
                    (guild_id, user_id, today, key, current, int(done or newly)),
                )
                if newly:
                    completed.append(key)
            rows = connection.execute(
                "SELECT challenge, progress FROM backgammon_daily "
                "WHERE guild_id = ? AND user_id = ? AND day = ?",
                (guild_id, user_id, today),
            ).fetchall()
        return GameRecord(
            before=before,
            after=after,
            completed=tuple(completed),
            progress={str(r["challenge"]): int(r["progress"]) for r in rows},
        )

    async def ranking(self, guild_id: int, limit: int = 10) -> list[tuple[int, PlayerRecord]]:
        """Quién más ha ganado en el servidor: `(user_id, ficha)`."""
        return await self._run(self._ranking_sync, guild_id, limit)

    def _ranking_sync(self, guild_id: int, limit: int) -> list[tuple[int, PlayerRecord]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM backgammon_players WHERE guild_id = ? AND games > 0
                ORDER BY wins DESC, gammons DESC, best_level DESC, best_streak DESC, games ASC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
        return [(int(row["user_id"]), _record(row)) for row in rows]

    async def forget_guild(self, guild_id: int) -> None:
        """Borra todo lo del backgammon de un servidor (el bot ha salido de él)."""
        await self._run(self._forget_guild_sync, guild_id)

    def _forget_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM backgammon_players WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM backgammon_daily WHERE guild_id = ?", (guild_id,))
