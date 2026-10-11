"""Persistencia de la telemetría interna (contadores, usuarios del día y muestras del sistema).

Tablas (en el mismo archivo SQLite del bot):

- `telemetry_counts`: un contador por (día, hora, servidor, tipo, nombre). La
  hora `-1` significa «día entero»: son las filas horarias viejas ya plegadas.
- `telemetry_users`: qué usuarios distintos hicieron algo cada día. Es el único
  dato por persona, por eso se borra pronto (`USER_RETENTION_DAYS`).
- `telemetry_system`: muestras de memoria, retraso del bucle y navegadores.

Retención (Biblia: «no guardar datos personales innecesarios»):

- horas sueltas: `HOURLY_RETENTION_DAYS` (90); después se pliegan a un día;
- contadores diarios: `COUNTS_RETENTION_DAYS` (400);
- usuarios: `USER_RETENTION_DAYS` (90);
- muestras del sistema: `SYSTEM_RETENTION_DAYS` (14).

Guarda solo cantidades, nunca contenido de mensajes. Las tablas de otros
repositorios (`casino_plays`, `economy_wallets`, `economy_ledger`) solo se
leen, para el informe; nunca se escriben. Todas las cuentas del informe son
agregados de SQL, salvo los usuarios, que se cargan para seudonimizarlos.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.levels import TIMEZONE
from bot.services.telemetry import Batch, ReportData

T = TypeVar("T")

HOURLY_RETENTION_DAYS = 90
"""Días que se guarda el desglose por hora; antes de eso se pliega en una fila por día."""

COUNTS_RETENTION_DAYS = 400
"""Días que se guardan los contadores (ya plegados) antes de borrarlos."""

USER_RETENTION_DAYS = 90
"""Días que se guardan los usuarios distintos por día (dato personal: retención corta)."""

SYSTEM_RETENTION_DAYS = 14
"""Días que se guardan las muestras del sistema."""

WHOLE_DAY = -1
"""Valor de `hour` de las filas que resumen un día entero."""

_COUNTER_COLUMNS = (
    "events",
    "errors",
    "ms_count",
    "ms_total",
    "ms_max",
    "ms_lt500",
    "ms_lt1000",
    "ms_lt3000",
    "ms_ge3000",
)
_COUNT_KEY = "day, hour, guild_id, kind, name"

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS telemetry_counts (
        day TEXT NOT NULL,
        hour INTEGER NOT NULL,
        guild_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        events INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        ms_count INTEGER NOT NULL DEFAULT 0,
        ms_total INTEGER NOT NULL DEFAULT 0,
        ms_max INTEGER NOT NULL DEFAULT 0,
        ms_lt500 INTEGER NOT NULL DEFAULT 0,
        ms_lt1000 INTEGER NOT NULL DEFAULT 0,
        ms_lt3000 INTEGER NOT NULL DEFAULT 0,
        ms_ge3000 INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, hour, guild_id, kind, name)
    ) WITHOUT ROWID;

    CREATE TABLE IF NOT EXISTS telemetry_users (
        day TEXT NOT NULL,
        guild_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        PRIMARY KEY (day, guild_id, kind, name, user_id)
    ) WITHOUT ROWID;

    CREATE TABLE IF NOT EXISTS telemetry_system (
        at REAL NOT NULL,
        day TEXT NOT NULL,
        memory_mb REAL,
        rss_mb REAL NOT NULL,
        loop_lag_ms REAL NOT NULL,
        gateway_ms REAL,
        chromium INTEGER NOT NULL,
        node INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS telemetry_system_at ON telemetry_system (at);
"""

# Suma de todas las columnas y MAX para ms_max: sirve para el upsert y para el pliegue.
_SUM_SET = ", ".join(
    f"{c} = MAX({c}, excluded.{c})" if c == "ms_max" else f"{c} = {c} + excluded.{c}"
    for c in _COUNTER_COLUMNS
)


class TelemetryRepository:
    """Acceso SQLite a la telemetría.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        # Si nadie llamó a `initialize` (pruebas que cierran el bot sin arrancarlo,
        # con flush al cerrar), `write` crea las tablas la primera vez.
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen (idempotente)."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(_SCHEMA)
        self._ready = True

    async def write(self, batch: Batch) -> None:
        """Escribe un lote en una sola transacción.

        Los contadores se suman a los que ya hubiera (upsert), los usuarios se
        ignoran si ya estaban y las muestras se añaden. Crea las tablas si hace falta.
        """
        await self._run(self._write_sync, batch)

    def _write_sync(self, batch: Batch) -> None:
        if not self._ready:
            self._initialize_sync()
        columns = ", ".join(_COUNTER_COLUMNS)
        marks = ", ".join("?" for _ in range(5 + len(_COUNTER_COLUMNS)))
        upsert = (
            f"INSERT INTO telemetry_counts ({_COUNT_KEY}, {columns}) VALUES ({marks}) "
            f"ON CONFLICT({_COUNT_KEY}) DO UPDATE SET {_SUM_SET}"
        )
        counters = [
            (
                key.day,
                key.hour,
                key.guild_id,
                key.kind,
                key.name,
                *(getattr(value, column) for column in _COUNTER_COLUMNS),
            )
            for key, value in batch.counters.items()
        ]
        users = [(u.day, u.guild_id, u.kind, u.name, u.user_id) for u in batch.users]
        samples = [
            (
                s.at,
                s.day,
                s.memory_mb,
                s.rss_mb,
                s.loop_lag_ms,
                s.gateway_ms,
                s.chromium,
                s.node,
            )
            for s in batch.samples
        ]
        with closing(self._connect()) as connection, connection:
            connection.executemany(upsert, counters)
            connection.executemany(
                "INSERT OR IGNORE INTO telemetry_users (day, guild_id, kind, name, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                users,
            )
            connection.executemany(
                "INSERT INTO telemetry_system "
                "(at, day, memory_mb, rss_mb, loop_lag_ms, gateway_ms, chromium, node) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                samples,
            )

    async def prune(self, *, now: float) -> None:
        """Aplica la retención: pliega horas viejas y borra lo que pasa de plazo."""
        await self._run(self._prune_sync, now)

    def _prune_sync(self, now: float) -> None:
        if not self._ready:
            self._initialize_sync()
        today = datetime.fromtimestamp(now, TIMEZONE).date()
        fold_before = (today - timedelta(days=HOURLY_RETENTION_DAYS)).isoformat()
        counts_before = (today - timedelta(days=COUNTS_RETENTION_DAYS)).isoformat()
        users_before = (today - timedelta(days=USER_RETENTION_DAYS)).isoformat()
        system_before = now - SYSTEM_RETENTION_DAYS * 86400
        columns = ", ".join(_COUNTER_COLUMNS)
        folded = ", ".join(f"MAX({c})" if c == "ms_max" else f"SUM({c})" for c in _COUNTER_COLUMNS)
        with closing(self._connect()) as connection, connection:
            # Pliegue: cada hora vieja se suma a su fila de día entero (`hour = -1`),
            # que puede existir ya si el pliegue se hizo a medias otro día.
            connection.execute(
                f"INSERT INTO telemetry_counts (day, hour, guild_id, kind, name, {columns}) "
                f"SELECT day, {WHOLE_DAY}, guild_id, kind, name, {folded} "
                "FROM telemetry_counts WHERE hour >= 0 AND day < ? "
                "GROUP BY day, guild_id, kind, name "
                f"ON CONFLICT({_COUNT_KEY}) DO UPDATE SET {_SUM_SET}",
                (fold_before,),
            )
            connection.execute(
                "DELETE FROM telemetry_counts WHERE hour >= 0 AND day < ?", (fold_before,)
            )
            connection.execute("DELETE FROM telemetry_counts WHERE day < ?", (counts_before,))
            connection.execute("DELETE FROM telemetry_users WHERE day < ?", (users_before,))
            connection.execute("DELETE FROM telemetry_system WHERE at < ?", (system_before,))

    async def report_data(self, *, guild_id: int, since_day: str, since_ts: float) -> ReportData:
        """Agrega en SQL lo que necesita `build_report` para un servidor desde `since_day`."""
        return await self._run(self._report_data_sync, guild_id, since_day, since_ts)

    @staticmethod
    def _has_table(connection: sqlite3.Connection, name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return row is not None

    def _report_data_sync(self, guild_id: int, since_day: str, since_ts: float) -> ReportData:
        # Los contadores del servidor 0 (avisos del log, dibujos) no son de ningún
        # servidor y salen en el informe de todos.
        if not self._ready:
            self._initialize_sync()
        data = ReportData()
        with closing(self._connect()) as connection:
            data.usage = [
                dict(row)
                for row in connection.execute(
                    "SELECT kind, name, SUM(events) AS events, SUM(errors) AS errors, "
                    "SUM(ms_count) AS ms_count, SUM(ms_total) AS ms_total, "
                    "MAX(ms_max) AS ms_max, SUM(ms_lt500) AS lt500, SUM(ms_lt1000) AS lt1000, "
                    "SUM(ms_lt3000) AS lt3000, SUM(ms_ge3000) AS ge3000 "
                    "FROM telemetry_counts WHERE guild_id IN (?, 0) AND day >= ? "
                    "GROUP BY kind, name",
                    (guild_id, since_day),
                )
            ]
            data.daily = [
                (r["day"], r["kind"], r["events"], r["errors"])
                for r in connection.execute(
                    "SELECT day, kind, SUM(events) AS events, SUM(errors) AS errors "
                    "FROM telemetry_counts WHERE guild_id IN (?, 0) AND day >= ? "
                    "GROUP BY day, kind",
                    (guild_id, since_day),
                )
            ]
            data.hourly = [
                (r["hour"], r["kind"], r["events"])
                for r in connection.execute(
                    "SELECT hour, kind, SUM(events) AS events FROM telemetry_counts "
                    "WHERE guild_id IN (?, 0) AND day >= ? AND hour >= 0 GROUP BY hour, kind",
                    (guild_id, since_day),
                )
            ]
            data.users = [
                (r["day"], r["kind"], r["name"], r["user_id"])
                for r in connection.execute(
                    "SELECT day, kind, name, user_id FROM telemetry_users "
                    "WHERE guild_id = ? AND day >= ?",
                    (guild_id, since_day),
                )
            ]
            data.system = [
                dict(row)
                for row in connection.execute(
                    "SELECT day, MAX(memory_mb) AS memory_max, AVG(memory_mb) AS memory_avg, "
                    "MAX(rss_mb) AS rss_max, MAX(loop_lag_ms) AS lag_max, "
                    "AVG(gateway_ms) AS gateway_avg, MAX(chromium) AS chromium_max, "
                    "MAX(node) AS node_max FROM telemetry_system WHERE day >= ? GROUP BY day",
                    (since_day,),
                )
            ]
            if self._has_table(connection, "casino_plays"):
                data.casino = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT game, COALESCE(SUM(stake > 0), 0) AS plays, "
                        "COALESCE(SUM(stake = 0), 0) AS free, "
                        "COUNT(DISTINCT user_id) AS players, "
                        "COALESCE(SUM(stake), 0) AS wagered, COALESCE(SUM(payout), 0) AS paid "
                        "FROM casino_plays WHERE guild_id = ? AND created_at >= ? "
                        "GROUP BY game",
                        (guild_id, since_ts),
                    )
                ]
            if self._has_table(connection, "economy_wallets"):
                for row in connection.execute(
                    "SELECT user_id, balance FROM economy_wallets WHERE guild_id = ?",
                    (guild_id,),
                ):
                    if row["user_id"] > 0:
                        data.balances.append(int(row["balance"]))
                    else:
                        data.system_accounts[int(row["user_id"])] = int(row["balance"])
            if self._has_table(connection, "economy_ledger"):
                data.ledger = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT reason, COUNT(*) AS moves, "
                        "COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END), 0) AS inflow, "
                        "COALESCE(SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END), 0) AS outflow "
                        "FROM economy_ledger WHERE guild_id = ? AND created_at >= ? "
                        "AND user_id > 0 GROUP BY reason",
                        (guild_id, since_ts),
                    )
                ]
        return data
