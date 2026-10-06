"""Persistencia del trabajo (`pala`): contratos, historial, formación, turnos y canales.

El dinero no pasa por aquí (va por `bot.services.economy`); aquí solo se
guarda el estado laboral. Tablas:

- `work_contracts`: el contrato en curso de cada miembro (`Contract`). El
  progreso de las tareas va en JSON porque cambia con el catálogo.
- `work_history`: el puesto en el que se quedó cada miembro en cada oficio y
  el más alto al que llegó. Al volver a un oficio se entra en ese puesto.
- `work_training`: formaciones compradas (valen para siempre).
- `work_shifts`: un registro corto de turnos (puntuación, tipo, dinero) para
  contar días en el puesto, el sueldo medio de la baja y los logros. Se
  borra lo que pase de `SHIFT_RETENTION_SECONDS`.
- `work_settings`: canales donde se permite `pala` (vacío = cualquiera).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, fields
from pathlib import Path
from typing import TypeVar

from bot.services.work import Contract, ShiftRecord

T = TypeVar("T")

#: Turnos más antiguos que esto se borran (los ascensos piden como mucho 5 días).
SHIFT_RETENTION_SECONDS = 35 * 86_400

_CONTRACT_FIELDS = tuple(f.name for f in fields(Contract))


class WorkRepository:
    """Acceso SQLite al trabajo; comparte archivo con el resto del bot.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS work_contracts (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS work_history (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    job TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    max_level INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, job)
                );

                CREATE TABLE IF NOT EXISTS work_training (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    training TEXT NOT NULL,
                    bought_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, training)
                );

                CREATE TABLE IF NOT EXISTS work_shifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    job TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    score INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    gross INTEGER NOT NULL,
                    net INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS work_shifts_member
                    ON work_shifts (guild_id, user_id, created_at);

                CREATE TABLE IF NOT EXISTS work_settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_ids TEXT NOT NULL DEFAULT '[]'
                );
                """
            )
        finally:
            connection.close()

    # -- Contratos -----------------------------------------------------------------------

    async def contract(self, guild_id: int, user_id: int) -> Contract | None:
        """Contrato en curso del miembro, o `None` si no trabaja."""
        return await self._run(self._contract_sync, guild_id, user_id)

    def _contract_sync(self, guild_id: int, user_id: int) -> Contract | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT data FROM work_contracts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        data = json.loads(row["data"])
        # Campos que no conoce esta versión se ignoran; los nuevos toman su valor
        # por defecto. Así añadir un campo a `Contract` no necesita migración.
        return Contract(**{k: v for k, v in data.items() if k in _CONTRACT_FIELDS})

    async def save_contract(self, guild_id: int, user_id: int, contract: Contract) -> None:
        """Guarda el contrato y actualiza el historial del oficio."""
        await self._run(self._save_contract_sync, guild_id, user_id, contract)

    def _save_contract_sync(self, guild_id: int, user_id: int, contract: Contract) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO work_contracts (guild_id, user_id, data) VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET data = excluded.data
                """,
                (guild_id, user_id, json.dumps(asdict(contract))),
            )
            connection.execute(
                """
                INSERT INTO work_history (guild_id, user_id, job, level, max_level)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, job) DO UPDATE SET
                    level = excluded.level,
                    max_level = MAX(max_level, excluded.level)
                """,
                (guild_id, user_id, contract.job, contract.level, contract.level),
            )
            connection.execute("COMMIT")
        finally:
            connection.close()

    async def history(self, guild_id: int, user_id: int) -> dict[str, tuple[int, int]]:
        """`{oficio: (puesto en el que se quedó, puesto máximo)}`."""
        return await self._run(self._history_sync, guild_id, user_id)

    def _history_sync(self, guild_id: int, user_id: int) -> dict[str, tuple[int, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT job, level, max_level FROM work_history WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchall()
            return {r["job"]: (int(r["level"]), int(r["max_level"])) for r in rows}
        finally:
            connection.close()

    # -- Formación -----------------------------------------------------------------------

    async def trainings(self, guild_id: int, user_id: int) -> set[str]:
        """Formaciones que tiene el miembro."""
        return await self._run(self._trainings_sync, guild_id, user_id)

    def _trainings_sync(self, guild_id: int, user_id: int) -> set[str]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT training FROM work_training WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchall()
            return {r["training"] for r in rows}
        finally:
            connection.close()

    @staticmethod
    def reserve_training(
        guild_id: int, user_id: int, training: str, now: float
    ) -> Callable[[sqlite3.Connection], None]:
        """Apunte de una formación para `EconomyService.purchase` (misma transacción).

        Raises:
            ValueError: Si ya la tenía; la compra no se cobra.
        """

        def reserve(connection: sqlite3.Connection) -> None:
            try:
                connection.execute(
                    """
                    INSERT INTO work_training (guild_id, user_id, training, bought_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (guild_id, user_id, training, now),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("Ya tienes esa formación.") from error

        return reserve

    # -- Turnos --------------------------------------------------------------------------

    async def add_shift(self, guild_id: int, user_id: int, record: ShiftRecord) -> None:
        """Apunta un turno y borra los que ya no hacen falta."""
        await self._run(self._add_shift_sync, guild_id, user_id, record)

    def _add_shift_sync(self, guild_id: int, user_id: int, record: ShiftRecord) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO work_shifts (guild_id, user_id, job, level, day, created_at,
                                         score, kind, gross, net)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    record.job,
                    record.level,
                    record.day,
                    record.created_at,
                    record.score,
                    record.kind,
                    record.gross,
                    record.net,
                ),
            )
            connection.execute(
                "DELETE FROM work_shifts WHERE guild_id = ? AND user_id = ? AND created_at < ?",
                (guild_id, user_id, record.created_at - SHIFT_RETENTION_SECONDS),
            )
            connection.execute("COMMIT")
        finally:
            connection.close()

    async def days_in_position(
        self, guild_id: int, user_id: int, job: str, level: int, since: float
    ) -> int:
        """Días distintos con algún turno en ese puesto desde `since`."""
        return await self._run(self._days_sync, guild_id, user_id, job, level, since)

    def _days_sync(self, guild_id: int, user_id: int, job: str, level: int, since: float) -> int:
        connection = self._connect()
        try:
            (days,) = connection.execute(
                """
                SELECT COUNT(DISTINCT day) FROM work_shifts
                WHERE guild_id = ? AND user_id = ? AND job = ? AND level = ? AND created_at >= ?
                """,
                (guild_id, user_id, job, level, since),
            ).fetchone()
            return int(days)
        finally:
            connection.close()

    async def gross_since(self, guild_id: int, user_id: int, since: float) -> int:
        """Bruto declarado de los turnos desde `since` (para la baja)."""
        return await self._run(self._gross_since_sync, guild_id, user_id, since)

    def _gross_since_sync(self, guild_id: int, user_id: int, since: float) -> int:
        connection = self._connect()
        try:
            (gross,) = connection.execute(
                """
                SELECT COALESCE(SUM(gross), 0) FROM work_shifts
                WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND kind != 'negro'
                """,
                (guild_id, user_id, since),
            ).fetchone()
            return int(gross)
        finally:
            connection.close()

    # -- Ajustes -------------------------------------------------------------------------

    async def channels(self, guild_id: int) -> frozenset[int]:
        """Canales donde se permite `pala` (vacío = cualquiera)."""
        return await self._run(self._channels_sync, guild_id)

    def _channels_sync(self, guild_id: int) -> frozenset[int]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT channel_ids FROM work_settings WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            return frozenset(int(c) for c in json.loads(row["channel_ids"])) if row else frozenset()
        finally:
            connection.close()

    async def set_channels(self, guild_id: int, channel_ids: frozenset[int]) -> None:
        """Cambia los canales permitidos (vacío = cualquiera)."""
        await self._run(self._set_channels_sync, guild_id, channel_ids)

    def _set_channels_sync(self, guild_id: int, channel_ids: frozenset[int]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO work_settings (guild_id, channel_ids) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET channel_ids = excluded.channel_ids
                """,
                (guild_id, json.dumps(sorted(channel_ids))),
            )
        finally:
            connection.close()

    # -- Limpieza ------------------------------------------------------------------------

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo el trabajo de un servidor cuando el bot sale de él."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table in (
                "work_contracts",
                "work_history",
                "work_training",
                "work_shifts",
                "work_settings",
            ):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            connection.execute("COMMIT")
        finally:
            connection.close()
