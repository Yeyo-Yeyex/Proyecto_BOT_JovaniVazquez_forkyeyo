"""Persistencia de la beernight: noches, sorbos, ajustes y mandamientos propios.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `beernight_nights`: una fila por noche. `ended_at` vacío = sigue abierta;
  como mucho hay una abierta por servidor.
- `beernight_participants`: quién estuvo en cada noche y desde cuándo.
- `beernight_sips`: cada sorbo apuntado, con su motivo (`Reason`), el
  mandamiento o evento y quién lo provocó. Es el histórico: de aquí salen el
  resumen de cada noche y el ranking de siempre.
- `beernight_custom`: mandamientos que propone la gente del servidor.
- `beernight_settings`: ajustes del servidor, en JSON (`Settings.to_json`).

El estado vivo de una noche (mandamientos activos, chivatazos pendientes)
está en memoria en el cog; si el bot se reinicia, la noche abierta se cierra
al arrancar con lo que ya estuviera apuntado.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.beernight import (
    MAX_CUSTOM,
    BeernightError,
    Reason,
    Settings,
    SipRecord,
    summarize,
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Night:
    """Una noche del histórico (o la que está en marcha)."""

    id: int
    guild_id: int
    host_id: int
    channel_id: int
    voice_channel_id: int | None
    started_at: float
    ended_at: float | None


@dataclass(frozen=True, slots=True)
class CustomMandate:
    """Un mandamiento propuesto por alguien del servidor."""

    id: int
    guild_id: int
    author_id: int
    text: str
    sips: int


@dataclass(slots=True)
class AllTime:
    """Ranking de siempre de un servidor.

    Attributes:
        sips: Sorbos de cada persona en todas las noches.
        nights: Noches en las que estuvo cada persona.
        mvps: Noches en las que cada persona fue la que más bebió.
        reports_ok: Chivatazos confirmados de cada persona.
        lies: Chivatazos falsos de cada persona.
        total_nights: Noches cerradas del servidor.
    """

    sips: Counter[int]
    nights: Counter[int]
    mvps: Counter[int]
    reports_ok: Counter[int]
    lies: Counter[int]
    total_nights: int


class BeernightRepository:
    """Acceso SQLite a la beernight.

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
                CREATE TABLE IF NOT EXISTS beernight_nights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    host_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    voice_channel_id INTEGER,
                    started_at REAL NOT NULL,
                    ended_at REAL
                );
                CREATE INDEX IF NOT EXISTS beernight_nights_guild
                    ON beernight_nights (guild_id, started_at);

                CREATE TABLE IF NOT EXISTS beernight_participants (
                    night_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at REAL NOT NULL,
                    PRIMARY KEY (night_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS beernight_sips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    night_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    sips INTEGER NOT NULL CHECK (sips >= 0),
                    forgiven INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    mandate TEXT,
                    by_user_id INTEGER,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS beernight_sips_night ON beernight_sips (night_id);
                CREATE INDEX IF NOT EXISTS beernight_sips_guild ON beernight_sips (guild_id);

                CREATE TABLE IF NOT EXISTS beernight_custom (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    sips INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS beernight_custom_guild ON beernight_custom (guild_id);

                CREATE TABLE IF NOT EXISTS beernight_settings (
                    guild_id INTEGER PRIMARY KEY,
                    data TEXT NOT NULL
                );
                """
            )

    # -- Ajustes --------------------------------------------------------------------

    async def get_settings(self, guild_id: int) -> Settings:
        """Ajustes del servidor, o los de por defecto si nunca se tocaron."""
        return await self._run(self._get_settings_sync, guild_id)

    def _get_settings_sync(self, guild_id: int) -> Settings:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM beernight_settings WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        if row is None:
            return Settings()
        try:
            data = json.loads(row["data"])
        except ValueError:
            return Settings()
        return Settings.from_json(data) if isinstance(data, dict) else Settings()

    async def save_settings(self, guild_id: int, settings: Settings) -> None:
        """Guarda los ajustes (ya validados) del servidor."""
        payload = json.dumps(settings.validated().to_json())
        await self._run(self._save_settings_sync, guild_id, payload)

    def _save_settings_sync(self, guild_id: int, payload: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO beernight_settings (guild_id, data) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET data = excluded.data
                """,
                (guild_id, payload),
            )

    # -- Mandamientos propios ----------------------------------------------------------

    @staticmethod
    def _custom(row: sqlite3.Row) -> CustomMandate:
        return CustomMandate(
            id=int(row["id"]),
            guild_id=int(row["guild_id"]),
            author_id=int(row["author_id"]),
            text=str(row["text"]),
            sips=int(row["sips"]),
        )

    async def list_custom(self, guild_id: int) -> list[CustomMandate]:
        """Mandamientos propios del servidor, del más antiguo al más nuevo."""
        return await self._run(self._list_custom_sync, guild_id)

    def _list_custom_sync(self, guild_id: int) -> list[CustomMandate]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM beernight_custom WHERE guild_id = ? ORDER BY id", (guild_id,)
            ).fetchall()
        return [self._custom(row) for row in rows]

    async def add_custom(
        self, guild_id: int, author_id: int, text: str, sips: int, now: float
    ) -> CustomMandate:
        """Apunta un mandamiento propio.

        Raises:
            BeernightError: Si el servidor ya tiene `MAX_CUSTOM`.
        """
        return await self._run(self._add_custom_sync, guild_id, author_id, text, sips, now)

    def _add_custom_sync(
        self, guild_id: int, author_id: int, text: str, sips: int, now: float
    ) -> CustomMandate:
        with self._connect() as connection:
            # Cuenta e inserta en la misma transacción para que dos altas a la
            # vez no pasen las dos del tope.
            connection.execute("BEGIN IMMEDIATE")
            (count,) = connection.execute(
                "SELECT COUNT(*) FROM beernight_custom WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            if count >= MAX_CUSTOM:
                raise BeernightError(
                    f"Ya hay {MAX_CUSTOM} mandamientos de la casa. Borra alguno antes."
                )
            cursor = connection.execute(
                "INSERT INTO beernight_custom (guild_id, author_id, text, sips, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (guild_id, author_id, text, sips, now),
            )
            assert cursor.lastrowid is not None
            return CustomMandate(cursor.lastrowid, guild_id, author_id, text, sips)

    async def get_custom(self, guild_id: int, custom_id: int) -> CustomMandate | None:
        """Un mandamiento propio del servidor, si existe."""
        return await self._run(self._get_custom_sync, guild_id, custom_id)

    def _get_custom_sync(self, guild_id: int, custom_id: int) -> CustomMandate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM beernight_custom WHERE guild_id = ? AND id = ?",
                (guild_id, custom_id),
            ).fetchone()
        return self._custom(row) if row else None

    async def delete_custom(self, guild_id: int, custom_id: int) -> bool:
        """Borra un mandamiento propio. Devuelve si existía."""
        return await self._run(self._delete_custom_sync, guild_id, custom_id)

    def _delete_custom_sync(self, guild_id: int, custom_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM beernight_custom WHERE guild_id = ? AND id = ?",
                (guild_id, custom_id),
            )
            return cursor.rowcount > 0

    # -- Noches -------------------------------------------------------------------------

    @staticmethod
    def _night(row: sqlite3.Row) -> Night:
        return Night(
            id=int(row["id"]),
            guild_id=int(row["guild_id"]),
            host_id=int(row["host_id"]),
            channel_id=int(row["channel_id"]),
            voice_channel_id=(
                int(row["voice_channel_id"]) if row["voice_channel_id"] is not None else None
            ),
            started_at=float(row["started_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
        )

    async def open_night(
        self,
        guild_id: int,
        host_id: int,
        channel_id: int,
        voice_channel_id: int | None,
        now: float,
    ) -> Night:
        """Abre una noche nueva.

        Raises:
            BeernightError: Si el servidor ya tiene una abierta.
        """
        return await self._run(
            self._open_night_sync, guild_id, host_id, channel_id, voice_channel_id, now
        )

    def _open_night_sync(
        self,
        guild_id: int,
        host_id: int,
        channel_id: int,
        voice_channel_id: int | None,
        now: float,
    ) -> Night:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            open_row = connection.execute(
                "SELECT id FROM beernight_nights WHERE guild_id = ? AND ended_at IS NULL",
                (guild_id,),
            ).fetchone()
            if open_row is not None:
                raise BeernightError("Ya hay una beernight en marcha en este servidor.")
            cursor = connection.execute(
                "INSERT INTO beernight_nights"
                " (guild_id, host_id, channel_id, voice_channel_id, started_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (guild_id, host_id, channel_id, voice_channel_id, now),
            )
            assert cursor.lastrowid is not None
            return Night(
                cursor.lastrowid, guild_id, host_id, channel_id, voice_channel_id, now, None
            )

    async def open_nights(self) -> list[Night]:
        """Noches que siguen abiertas (de todos los servidores)."""
        return await self._run(self._open_nights_sync)

    def _open_nights_sync(self) -> list[Night]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM beernight_nights WHERE ended_at IS NULL"
            ).fetchall()
        return [self._night(row) for row in rows]

    async def close_night(self, night_id: int, now: float) -> bool:
        """Cierra una noche. Devuelve `False` si ya estaba cerrada."""
        return await self._run(self._close_night_sync, night_id, now)

    def _close_night_sync(self, night_id: int, now: float) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE beernight_nights SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (now, night_id),
            )
            return cursor.rowcount > 0

    async def get_night(self, guild_id: int, night_id: int) -> Night | None:
        """Una noche del servidor, abierta o cerrada."""
        return await self._run(self._get_night_sync, guild_id, night_id)

    def _get_night_sync(self, guild_id: int, night_id: int) -> Night | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM beernight_nights WHERE guild_id = ? AND id = ?",
                (guild_id, night_id),
            ).fetchone()
        return self._night(row) if row else None

    async def recent_nights(self, guild_id: int, limit: int = 10) -> list[Night]:
        """Últimas noches cerradas del servidor, de la más nueva a la más vieja."""
        return await self._run(self._recent_nights_sync, guild_id, limit)

    def _recent_nights_sync(self, guild_id: int, limit: int) -> list[Night]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM beernight_nights WHERE guild_id = ? AND ended_at IS NOT NULL"
                " ORDER BY started_at DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()
        return [self._night(row) for row in rows]

    # -- Participantes ------------------------------------------------------------------

    async def add_participant(self, night_id: int, user_id: int, now: float) -> bool:
        """Apunta a alguien en la noche. Devuelve `False` si ya estaba."""
        return await self._run(self._add_participant_sync, night_id, user_id, now)

    def _add_participant_sync(self, night_id: int, user_id: int, now: float) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO beernight_participants (night_id, user_id, joined_at)"
                " VALUES (?, ?, ?)",
                (night_id, user_id, now),
            )
            return cursor.rowcount > 0

    async def participants(self, night_id: int) -> dict[int, float]:
        """Participantes de la noche con su hora de llegada."""
        return await self._run(self._participants_sync, night_id)

    def _participants_sync(self, night_id: int) -> dict[int, float]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, joined_at FROM beernight_participants WHERE night_id = ?",
                (night_id,),
            ).fetchall()
        return {int(row["user_id"]): float(row["joined_at"]) for row in rows}

    async def night_dates(self, guild_id: int, user_id: int) -> list[float]:
        """Inicio de cada noche cerrada en la que estuvo alguien (para las rachas)."""
        return await self._run(self._night_dates_sync, guild_id, user_id)

    def _night_dates_sync(self, guild_id: int, user_id: int) -> list[float]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT n.started_at FROM beernight_nights n"
                " JOIN beernight_participants p ON p.night_id = n.id"
                " WHERE n.guild_id = ? AND p.user_id = ? ORDER BY n.started_at",
                (guild_id, user_id),
            ).fetchall()
        return [float(row["started_at"]) for row in rows]

    # -- Sorbos -------------------------------------------------------------------------

    async def add_sips(self, night_id: int, guild_id: int, records: list[SipRecord]) -> None:
        """Apunta varios sorbos de golpe (un evento de todos, por ejemplo)."""
        if records:
            await self._run(self._add_sips_sync, night_id, guild_id, records)

    def _add_sips_sync(self, night_id: int, guild_id: int, records: list[SipRecord]) -> None:
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO beernight_sips (night_id, guild_id, user_id, sips, forgiven,"
                " reason, mandate, by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        night_id,
                        guild_id,
                        r.user_id,
                        r.sips,
                        r.forgiven,
                        r.reason.value,
                        r.mandate,
                        r.by_user_id,
                        r.created_at,
                    )
                    for r in records
                ],
            )

    async def sips_since(self, night_id: int, user_id: int, since: float) -> int:
        """Sorbos de alguien en la noche desde `since` (para el tope por hora)."""
        return await self._run(self._sips_since_sync, night_id, user_id, since)

    def _sips_since_sync(self, night_id: int, user_id: int, since: float) -> int:
        with self._connect() as connection:
            (total,) = connection.execute(
                "SELECT COALESCE(SUM(sips), 0) FROM beernight_sips"
                " WHERE night_id = ? AND user_id = ? AND created_at >= ?",
                (night_id, user_id, since),
            ).fetchone()
        return int(total)

    @staticmethod
    def _record(row: sqlite3.Row) -> SipRecord:
        return SipRecord(
            user_id=int(row["user_id"]),
            sips=int(row["sips"]),
            reason=Reason(row["reason"]),
            mandate=row["mandate"],
            by_user_id=int(row["by_user_id"]) if row["by_user_id"] is not None else None,
            created_at=float(row["created_at"]),
            forgiven=int(row["forgiven"]),
        )

    async def night_records(self, night_id: int) -> list[SipRecord]:
        """Todos los sorbos de una noche, en orden."""
        return await self._run(self._night_records_sync, night_id)

    def _night_records_sync(self, night_id: int) -> list[SipRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM beernight_sips WHERE night_id = ? ORDER BY id", (night_id,)
            ).fetchall()
        return [self._record(row) for row in rows]

    async def all_time(self, guild_id: int) -> AllTime:
        """Ranking de siempre del servidor, con las noches ya cerradas."""
        return await self._run(self._all_time_sync, guild_id)

    def _all_time_sync(self, guild_id: int) -> AllTime:
        with self._connect() as connection:
            nights = [
                int(row["id"])
                for row in connection.execute(
                    "SELECT id FROM beernight_nights WHERE guild_id = ? AND ended_at IS NOT NULL",
                    (guild_id,),
                ).fetchall()
            ]
            attendance: Counter[int] = Counter()
            for row in connection.execute(
                "SELECT p.user_id, COUNT(*) AS c FROM beernight_participants p"
                " JOIN beernight_nights n ON n.id = p.night_id"
                " WHERE n.guild_id = ? AND n.ended_at IS NOT NULL GROUP BY p.user_id",
                (guild_id,),
            ).fetchall():
                attendance[int(row["user_id"])] = int(row["c"])
            rows = connection.execute(
                "SELECT s.* FROM beernight_sips s JOIN beernight_nights n ON n.id = s.night_id"
                " WHERE n.guild_id = ? AND n.ended_at IS NOT NULL ORDER BY s.id",
                (guild_id,),
            ).fetchall()
        by_night: dict[int, list[SipRecord]] = {}
        for row in rows:
            by_night.setdefault(int(row["night_id"]), []).append(self._record(row))
        total = summarize(r for records in by_night.values() for r in records)
        mvps: Counter[int] = Counter()
        for records in by_night.values():
            if (mvp := summarize(records).mvp()) is not None:
                mvps[mvp] += 1
        return AllTime(
            sips=total.sips,
            nights=attendance,
            mvps=mvps,
            reports_ok=total.reports_ok,
            lies=total.lies,
            total_nights=len(nights),
        )

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo de la beernight de un servidor cuando el bot sale de él."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM beernight_participants WHERE night_id IN"
                " (SELECT id FROM beernight_nights WHERE guild_id = ?)",
                (guild_id,),
            )
            for table in (
                "beernight_sips",
                "beernight_nights",
                "beernight_custom",
                "beernight_settings",
            ):
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
