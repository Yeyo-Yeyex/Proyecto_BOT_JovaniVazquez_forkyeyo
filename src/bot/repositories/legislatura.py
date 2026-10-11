"""Persistencia de Legislatura: partidas, perfiles y la sesión del día.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `legislatura_runs`: una fila por partida. Mientras está activa guarda el
  estado entero en JSON (`Run.dumps`, unos pocos KB) y se reescribe tras cada
  acción, para que reiniciar el bot (o abandonar y volver) no cambie nada. Un
  índice único impide tener dos partidas activas a la vez por miembro. Al
  terminar se borra el estado y se quedan las cifras (resultado, premio y
  estadísticas), que es lo que necesitan los logros y las clasificaciones.
- `legislatura_profiles`: por miembro, la prima más alta desbloqueada, los
  gobiernos desbloqueados y los aspectos elegidos.
- `legislatura_daily`: el resultado de cada miembro en la sesión del día (una
  sola partida por día y servidor, con la misma semilla para todos).

Las reglas viven en `bot.services.legislatura`; el dinero, en la economía.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.legislatura.run import Run

T = TypeVar("T")

#: Estados de una partida en la tabla.
ACTIVE = "activa"
FINISHED = ("perdida", "ganada", "abandonada")


@dataclass(slots=True)
class Profile:
    """Lo que un miembro ha desbloqueado y elegido en Legislatura.

    Attributes:
        max_stake: Prima más alta a la que puede jugar.
        unlocks: Claves desbloqueadas (gobiernos y lo que venga).
        skins: Aspecto elegido por hueco (`cosmetics.resolve`).
    """

    max_stake: int = 1
    unlocks: set[str] = field(default_factory=set)
    skins: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DailyEntry:
    """Resultado de un miembro en la sesión del día."""

    user_id: int
    score: int
    periods: int


class LegislaturaRepository:
    """Acceso SQLite a partidas, perfiles y sesiones del día.

    Args:
        database_path: Ruta del archivo SQLite persistente.
        clock: Reloj (sustituible en pruebas).
    """

    def __init__(self, database_path: Path, clock: Callable[[], float] = time.time) -> None:
        self.database_path = database_path
        self._clock = clock

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
                CREATE TABLE IF NOT EXISTS legislatura_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    seed TEXT NOT NULL,
                    stake INTEGER NOT NULL,
                    government TEXT NOT NULL,
                    status TEXT NOT NULL,
                    entry INTEGER NOT NULL DEFAULT 0,
                    payout INTEGER NOT NULL DEFAULT 0,
                    periods INTEGER NOT NULL DEFAULT 0,
                    best_hand INTEGER NOT NULL DEFAULT 0,
                    state TEXT,
                    stats TEXT,
                    started REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS legislatura_one_active
                    ON legislatura_runs (guild_id, user_id) WHERE status = 'activa';
                CREATE TABLE IF NOT EXISTS legislatura_profiles (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    max_stake INTEGER NOT NULL DEFAULT 1,
                    unlocks TEXT NOT NULL DEFAULT '[]',
                    skins TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (guild_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS legislatura_daily (
                    guild_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    run_id INTEGER NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    periods INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, day, user_id)
                );
                """
            )

    # -- Partidas ---------------------------------------------------------------------------

    async def active(self, guild_id: int, user_id: int) -> tuple[int, Run] | None:
        """La partida activa de un miembro (id y estado), si tiene."""
        return await self._run(self._active_sync, guild_id, user_id)

    def _active_sync(self, guild_id: int, user_id: int) -> tuple[int, Run] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, state FROM legislatura_runs "
                "WHERE guild_id = ? AND user_id = ? AND status = ?",
                (guild_id, user_id, ACTIVE),
            ).fetchone()
        if row is None:
            return None
        return int(row["id"]), Run.loads(row["state"])

    async def create(self, guild_id: int, user_id: int, run: Run, *, entry: int) -> int | None:
        """Guarda una partida nueva como activa.

        Returns:
            El id de la partida, o `None` si el miembro ya tenía una activa
            (el índice único lo impide aunque lleguen dos clics a la vez).
        """
        return await self._run(self._create_sync, guild_id, user_id, run, entry)

    def _create_sync(self, guild_id: int, user_id: int, run: Run, entry: int) -> int | None:
        now = self._clock()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO legislatura_runs
                        (guild_id, user_id, mode, seed, stake, government, status, entry,
                         state, started, updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        user_id,
                        run.mode,
                        run.seed,
                        run.stake,
                        run.government,
                        ACTIVE,
                        entry,
                        run.dumps(),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            return None
        return int(cursor.lastrowid or 0)

    async def save(self, run_id: int, run: Run) -> None:
        """Reescribe el estado de una partida activa."""
        await self._run(self._save_sync, run_id, run)

    def _save_sync(self, run_id: int, run: Run) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE legislatura_runs SET state = ?, periods = ?, best_hand = ?, updated = ? "
                "WHERE id = ? AND status = ?",
                (
                    run.dumps(),
                    run.cleared_periods,
                    run.stats.get("mejor_jugada", 0),
                    self._clock(),
                    run_id,
                    ACTIVE,
                ),
            )

    async def finish(self, run_id: int, run: Run, *, status: str, payout: int) -> None:
        """Cierra una partida: borra el estado y deja resultado, premio y estadísticas.

        Raises:
            ValueError: Si `status` no es uno de `FINISHED`.
        """
        if status not in FINISHED:
            raise ValueError(f"Estado de cierre desconocido: {status}")
        await self._run(self._finish_sync, run_id, run, status, payout)

    def _finish_sync(self, run_id: int, run: Run, status: str, payout: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE legislatura_runs
                SET status = ?, payout = ?, periods = ?, best_hand = ?, state = NULL,
                    stats = ?, updated = ?
                WHERE id = ?
                """,
                (
                    status,
                    payout,
                    run.cleared_periods,
                    run.stats.get("mejor_jugada", 0),
                    json.dumps(run.stats, separators=(",", ":")),
                    self._clock(),
                    run_id,
                ),
            )

    # -- Perfiles -----------------------------------------------------------------------------

    async def profile(self, guild_id: int, user_id: int) -> Profile:
        """Perfil de un miembro (el de serie si no ha jugado nunca)."""
        return await self._run(self._profile_sync, guild_id, user_id)

    def _profile_sync(self, guild_id: int, user_id: int) -> Profile:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM legislatura_profiles WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        if row is None:
            return Profile()
        return Profile(
            max_stake=int(row["max_stake"]),
            unlocks=set(json.loads(row["unlocks"])),
            skins=dict(json.loads(row["skins"])),
        )

    async def save_profile(self, guild_id: int, user_id: int, profile: Profile) -> None:
        """Guarda el perfil entero."""
        await self._run(self._save_profile_sync, guild_id, user_id, profile)

    def _save_profile_sync(self, guild_id: int, user_id: int, profile: Profile) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO legislatura_profiles (guild_id, user_id, max_stake, unlocks, skins)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                    max_stake = excluded.max_stake,
                    unlocks = excluded.unlocks,
                    skins = excluded.skins
                """,
                (
                    guild_id,
                    user_id,
                    profile.max_stake,
                    json.dumps(sorted(profile.unlocks)),
                    json.dumps(profile.skins, sort_keys=True),
                ),
            )

    # -- Sesión del día -------------------------------------------------------------------------

    async def daily_run(self, guild_id: int, day: str, user_id: int) -> int | None:
        """Id de la partida del día de un miembro, si ya la ha empezado."""
        return await self._run(self._daily_run_sync, guild_id, day, user_id)

    def _daily_run_sync(self, guild_id: int, day: str, user_id: int) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM legislatura_daily "
                "WHERE guild_id = ? AND day = ? AND user_id = ?",
                (guild_id, day, user_id),
            ).fetchone()
        return int(row["run_id"]) if row else None

    async def record_daily(
        self, guild_id: int, day: str, user_id: int, run_id: int, *, score: int, periods: int
    ) -> None:
        """Apunta (o mejora) el resultado de la partida del día."""
        await self._run(self._record_daily_sync, guild_id, day, user_id, run_id, score, periods)

    def _record_daily_sync(
        self, guild_id: int, day: str, user_id: int, run_id: int, score: int, periods: int
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO legislatura_daily (guild_id, day, user_id, run_id, score, periods)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, day, user_id) DO UPDATE SET
                    score = max(score, excluded.score),
                    periods = max(periods, excluded.periods)
                """,
                (guild_id, day, user_id, run_id, score, periods),
            )

    async def daily_ranking(self, guild_id: int, day: str, limit: int = 10) -> list[DailyEntry]:
        """Clasificación del día: más periodos superados y, a igualdad, mejor jugada."""
        return await self._run(self._daily_ranking_sync, guild_id, day, limit)

    def _daily_ranking_sync(self, guild_id: int, day: str, limit: int) -> list[DailyEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, score, periods FROM legislatura_daily "
                "WHERE guild_id = ? AND day = ? ORDER BY periods DESC, score DESC LIMIT ?",
                (guild_id, day, limit),
            ).fetchall()
        return [DailyEntry(int(r["user_id"]), int(r["score"]), int(r["periods"])) for r in rows]

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo guardado de un servidor (al salir el bot de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            for table in ("legislatura_runs", "legislatura_profiles", "legislatura_daily"):
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
