"""Persistencia del club del póker: XP, racha, el 💎 Bote de cada uno y los retos del día.

Dos tablas en el mismo archivo SQLite que el resto del bot:

- `poker_players`: por servidor y miembro, la XP del rango, la racha de días
  jugando, el último día que jugó una mano que cuenta y si juega el 💎 Bote
  (se recuerda de una mano a otra).
- `poker_daily`: lo que lleva cada miembro de cada reto del día y si ya lo
  cumplió (y cobró). Las filas de días pasados no se usan; se borran al apuntar
  una mano de un día nuevo, así la tabla no crece.

Qué retos tiene cada uno no se guarda: sale de `poker_club.daily_challenges`.
El dinero tampoco: pasa por la economía. Solo se guardan IDs; todo se borra
cuando el bot sale del servidor (`forget_guild`).
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
from bot.services.poker_club import next_streak

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PokerPlayer:
    """La ficha de un miembro en el club.

    Attributes:
        xp: Experiencia acumulada (da el rango).
        streak: Días seguidos jugando.
        last_day: Último día que jugó una mano que cuenta.
        bonus: Si juega el 💎 Bote en cada mano.
    """

    xp: int = 0
    streak: int = 0
    last_day: date | None = None
    bonus: bool = False


@dataclass(frozen=True, slots=True)
class HandRecord:
    """Lo que cambió al apuntar una mano.

    Attributes:
        before: La ficha de antes.
        after: La de después.
        completed: Retos que se acaban de cumplir con esta mano.
        progress: Lo que lleva de cada reto de hoy, tras la mano.
    """

    before: PokerPlayer
    after: PokerPlayer
    completed: tuple[str, ...]
    progress: dict[str, int]


def _player(row: sqlite3.Row | None) -> PokerPlayer:
    if row is None:
        return PokerPlayer()
    last = str(row["last_day"] or "")
    return PokerPlayer(
        xp=int(row["xp"]),
        streak=int(row["streak"]),
        last_day=date.fromisoformat(last) if last else None,
        bonus=bool(row["bonus"]),
    )


class PokerRepository:
    """Acceso SQLite al club del póker.

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
                CREATE TABLE IF NOT EXISTS poker_players (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    xp INTEGER NOT NULL DEFAULT 0,
                    streak INTEGER NOT NULL DEFAULT 0,
                    last_day TEXT,
                    bonus INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS poker_daily (
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

    async def player(self, guild_id: int, user_id: int) -> PokerPlayer:
        """La ficha de un miembro (vacía si nunca ha jugado)."""
        return await self._run(self._player_sync, guild_id, user_id)

    def _player_sync(self, guild_id: int, user_id: int) -> PokerPlayer:
        with self._connect() as connection:
            return self._player_in(connection, guild_id, user_id)

    @staticmethod
    def _player_in(connection: sqlite3.Connection, guild_id: int, user_id: int) -> PokerPlayer:
        row = connection.execute(
            "SELECT * FROM poker_players WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        return _player(row)

    async def set_bonus(self, guild_id: int, user_id: int, on: bool) -> None:
        """Recuerda si el miembro juega el 💎 Bote."""
        await self._run(self._set_bonus_sync, guild_id, user_id, on)

    def _set_bonus_sync(self, guild_id: int, user_id: int, on: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO poker_players (guild_id, user_id, bonus) VALUES (?, ?, ?)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET bonus = excluded.bonus
                """,
                (guild_id, user_id, int(on)),
            )

    async def daily(self, guild_id: int, user_id: int, day: date) -> dict[str, tuple[int, bool]]:
        """Lo que lleva de cada reto de `day`: clave → (progreso, cumplido)."""
        return await self._run(self._daily_sync, guild_id, user_id, day)

    def _daily_sync(self, guild_id: int, user_id: int, day: date) -> dict[str, tuple[int, bool]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT challenge, progress, done FROM poker_daily "
                "WHERE guild_id = ? AND user_id = ? AND day = ?",
                (guild_id, user_id, day.isoformat()),
            ).fetchall()
        return {str(r["challenge"]): (int(r["progress"]), bool(r["done"])) for r in rows}

    async def record_hand(
        self,
        guild_id: int,
        user_id: int,
        *,
        day: date,
        xp: int,
        counts: bool,
        progress: Mapping[str, int],
        goals: Mapping[str, int],
    ) -> HandRecord:
        """Apunta una mano: XP, racha y retos del día, todo en una transacción.

        Args:
            day: El día local de la mano.
            xp: La XP que da.
            counts: Si cuenta para la racha (ante de al menos una tirada).
            progress: Lo que suma a cada reto de hoy.
            goals: La meta de cada reto de hoy, para saber cuáles se cumplen.

        Returns:
            La ficha de antes y la de después, los retos recién cumplidos y lo que
            lleva de cada reto.
        """
        return await self._run(
            self._record_hand_sync, guild_id, user_id, day, xp, counts, dict(progress), dict(goals)
        )

    def _record_hand_sync(
        self,
        guild_id: int,
        user_id: int,
        day: date,
        xp: int,
        counts: bool,
        progress: dict[str, int],
        goals: dict[str, int],
    ) -> HandRecord:
        today = day.isoformat()
        with self._connect() as connection:
            before = self._player_in(connection, guild_id, user_id)
            after = before
            if counts:
                after = PokerPlayer(
                    xp=before.xp + xp,
                    streak=next_streak(before.streak, before.last_day, day),
                    last_day=day,
                    bonus=before.bonus,
                )
                if before.last_day != day:
                    # Día nuevo: lo de días pasados ya no sirve.
                    connection.execute(
                        "DELETE FROM poker_daily WHERE guild_id = ? AND user_id = ? AND day < ?",
                        (guild_id, user_id, today),
                    )
                connection.execute(
                    """
                    INSERT INTO poker_players (guild_id, user_id, xp, streak, last_day, bonus)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id) DO UPDATE SET
                        xp = excluded.xp, streak = excluded.streak, last_day = excluded.last_day
                    """,
                    (guild_id, user_id, after.xp, after.streak, today, int(after.bonus)),
                )
            completed: list[str] = []
            for key, amount in progress.items():
                row = connection.execute(
                    "SELECT progress, done FROM poker_daily "
                    "WHERE guild_id = ? AND user_id = ? AND day = ? AND challenge = ?",
                    (guild_id, user_id, today, key),
                ).fetchone()
                current, done = (int(row["progress"]), bool(row["done"])) if row else (0, False)
                current += amount
                newly = not done and current >= goals.get(key, current + 1)
                connection.execute(
                    """
                    INSERT INTO poker_daily (guild_id, user_id, day, challenge, progress, done)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id, day, challenge) DO UPDATE SET
                        progress = excluded.progress, done = excluded.done
                    """,
                    (guild_id, user_id, today, key, current, int(done or newly)),
                )
                if newly:
                    completed.append(key)
            rows = connection.execute(
                "SELECT challenge, progress FROM poker_daily "
                "WHERE guild_id = ? AND user_id = ? AND day = ?",
                (guild_id, user_id, today),
            ).fetchall()
        return HandRecord(
            before=before,
            after=after,
            completed=tuple(completed),
            progress={str(r["challenge"]): int(r["progress"]) for r in rows},
        )

    async def forget_guild(self, guild_id: int) -> None:
        """Borra todo lo del club de un servidor (el bot ha salido de él)."""
        await self._run(self._forget_guild_sync, guild_id)

    def _forget_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM poker_players WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM poker_daily WHERE guild_id = ?", (guild_id,))
