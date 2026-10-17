"""Persistencia del trabajo (`pala`): contratos, historial, formación, turnos y canales.

El dinero no pasa por aquí (va por `bot.services.economy`); aquí solo se
guarda el estado laboral. Tablas:

- `work_contracts`: el contrato en curso de cada miembro (`Contract`). El
  progreso de las tareas va en JSON porque cambia con el catálogo.
- `work_history`: el puesto (y la rama) en el que se quedó cada miembro en
  cada oficio y el más alto al que llegó. Al volver a un oficio se entra en ese
  puesto.
- `work_retirements`: las ramas de las que se jubiló cada miembro (para siempre), con la
  calidad de lo cotizado en cada una (`quality`, de 0 a 1) y si es de antes de la 8.0.0
  (`legacy`: su pensión de entonces es el suelo).
- `work_pension_claims`: el último día que cobró la pensión, para cobrarla una
  vez al día.
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
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.work import Career, Contract, ShiftRecord

T = TypeVar("T")

#: Turnos más antiguos que esto se borran. Los días en el puesto se cuentan con ellos: los
#: puestos de rama piden hasta 21 días distintos, y quien no ficha a diario tarda más.
SHIFT_RETENTION_SECONDS = 120 * 86_400

_CONTRACT_FIELDS = tuple(f.name for f in fields(Contract))


@dataclass(frozen=True, slots=True)
class RetiredBranch:
    """Una rama de la que se jubiló un miembro.

    Attributes:
        quality: Calidad de lo cotizado en ella (`work.contribution_quality`).
        legacy: Si se jubiló antes de la 8.0.0: su pensión de entonces es el suelo.
        retired_at: Cuándo (epoch).
    """

    job: str
    branch: str
    quality: float
    legacy: bool
    retired_at: float


class WorkRepository:
    """Acceso SQLite al trabajo; comparte archivo con el resto del bot.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path, isolation_level=None)

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

                CREATE TABLE IF NOT EXISTS work_retirements (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    job TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    retired_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, job, branch)
                );

                CREATE TABLE IF NOT EXISTS work_pension_claims (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )
            # Bases de datos de antes de las ramas: todo el mundo estaba en el tronco.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(work_history)")}
            if "branch" not in columns:
                connection.execute(
                    "ALTER TABLE work_history ADD COLUMN branch TEXT NOT NULL DEFAULT ''"
                )
            # Jubilaciones de antes de la 8.0.0: calidad entera (no hay historial) y suelo.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(work_retirements)")}
            if "quality" not in columns:
                connection.execute(
                    "ALTER TABLE work_retirements ADD COLUMN quality REAL NOT NULL DEFAULT 1"
                )
            if "legacy" not in columns:
                connection.execute(
                    "ALTER TABLE work_retirements ADD COLUMN legacy INTEGER NOT NULL DEFAULT 1"
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
        # Un contrato guardado antes de la 8.0.0 conserva los días de antes en su puesto.
        data.setdefault("legacy_days", True)
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
            self._write_contract(connection, guild_id, user_id, contract)
            connection.execute("COMMIT")
        finally:
            connection.close()

    @staticmethod
    def _write_contract(
        connection: sqlite3.Connection, guild_id: int, user_id: int, contract: Contract
    ) -> None:
        """Escribe el contrato y su historial dentro de la transacción abierta."""
        connection.execute(
            """
            INSERT INTO work_contracts (guild_id, user_id, data) VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET data = excluded.data
            """,
            (guild_id, user_id, json.dumps(asdict(contract))),
        )
        connection.execute(
            """
            INSERT INTO work_history (guild_id, user_id, job, level, max_level, branch)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, job) DO UPDATE SET
                level = excluded.level,
                max_level = MAX(max_level, excluded.level),
                branch = excluded.branch
            """,
            (guild_id, user_id, contract.job, contract.level, contract.level, contract.branch),
        )

    async def history(self, guild_id: int, user_id: int) -> dict[str, Career]:
        """`{oficio: Career(puesto en el que se quedó, puesto máximo, rama)}`."""
        return await self._run(self._history_sync, guild_id, user_id)

    def _history_sync(self, guild_id: int, user_id: int) -> dict[str, Career]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT job, level, max_level, branch FROM work_history
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchall()
            return {
                r["job"]: Career(int(r["level"]), int(r["max_level"]), r["branch"]) for r in rows
            }
        finally:
            connection.close()

    # -- Jubilación ----------------------------------------------------------------------

    async def retirements(self, guild_id: int, user_id: int) -> frozenset[tuple[str, str]]:
        """Ramas de las que se jubiló: `{(oficio, rama)}`."""
        return await self._run(self._retirements_sync, guild_id, user_id)

    def _retirements_sync(self, guild_id: int, user_id: int) -> frozenset[tuple[str, str]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT job, branch FROM work_retirements WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchall()
            return frozenset((r["job"], r["branch"]) for r in rows)
        finally:
            connection.close()

    async def retired_branches(self, guild_id: int, user_id: int) -> list[RetiredBranch]:
        """Las ramas de las que se jubiló, con su calidad, por orden de jubilación."""
        return await self._run(self._retired_branches_sync, guild_id, user_id)

    def _retired_branches_sync(self, guild_id: int, user_id: int) -> list[RetiredBranch]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT job, branch, quality, legacy, retired_at FROM work_retirements
                WHERE guild_id = ? AND user_id = ? ORDER BY retired_at, rowid
                """,
                (guild_id, user_id),
            ).fetchall()
            return [
                RetiredBranch(
                    r["job"], r["branch"], float(r["quality"]), bool(r["legacy"]), r["retired_at"]
                )
                for r in rows
            ]
        finally:
            connection.close()

    async def retire(
        self,
        guild_id: int,
        user_id: int,
        branch: str,
        contract: Contract,
        now: float,
        quality: float = 1.0,
    ) -> None:
        """Apunta la jubilación de la rama `branch` del oficio del contrato y guarda
        el contrato nuevo (ya en la otra rama), todo en la misma transacción.

        Args:
            quality: Calidad de lo cotizado en la rama (`work.contribution_quality`).

        Raises:
            ValueError: Si ya estaba jubilado de esa rama; no se guarda nada.
        """
        await self._run(self._retire_sync, guild_id, user_id, branch, contract, now, quality)

    def _retire_sync(
        self,
        guild_id: int,
        user_id: int,
        branch: str,
        contract: Contract,
        now: float,
        quality: float,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO work_retirements
                        (guild_id, user_id, job, branch, retired_at, quality, legacy)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (guild_id, user_id, contract.job, branch, now, quality),
                )
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                raise ValueError("Ya estás jubilado de esa rama.") from error
            self._write_contract(connection, guild_id, user_id, contract)
            connection.execute("COMMIT")
        finally:
            connection.close()

    async def claim_pension_day(self, guild_id: int, user_id: int, day: str) -> str | None:
        """Apunta que hoy (`day`) cobra la pensión, si no la había cobrado ya.

        Returns:
            El día del cobro anterior (`""` si es el primero), para deshacerlo si
            el pago falla, o `None` si hoy ya la había cobrado.
        """
        return await self._run(self._claim_pension_sync, guild_id, user_id, day)

    def _claim_pension_sync(self, guild_id: int, user_id: int, day: str) -> str | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT day FROM work_pension_claims WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            previous = row["day"] if row else ""
            if previous == day:
                connection.execute("ROLLBACK")
                return None
            connection.execute(
                """
                INSERT INTO work_pension_claims (guild_id, user_id, day) VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET day = excluded.day
                """,
                (guild_id, user_id, day),
            )
            connection.execute("COMMIT")
            return previous
        finally:
            connection.close()

    async def pension_day(self, guild_id: int, user_id: int) -> str:
        """Último día (ISO) en que cobró la pensión (`""` si nunca)."""
        return await self._run(self._pension_day_sync, guild_id, user_id)

    def _pension_day_sync(self, guild_id: int, user_id: int) -> str:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT day FROM work_pension_claims WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return row["day"] if row else ""
        finally:
            connection.close()

    async def restore_pension_day(self, guild_id: int, user_id: int, day: str) -> None:
        """Deshace un cobro apuntado con `claim_pension_day` (si el pago falló)."""
        await self._run(self._restore_pension_sync, guild_id, user_id, day)

    def _restore_pension_sync(self, guild_id: int, user_id: int, day: str) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE work_pension_claims SET day = ? WHERE guild_id = ? AND user_id = ?",
                (day, guild_id, user_id),
            )
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

    async def last_shift_at(self, guild_id: int, user_id: int) -> float | None:
        """Cuándo fichó por última vez (epoch), en cualquier oficio; `None` si no consta."""
        return await self._run(self._last_shift_sync, guild_id, user_id)

    def _last_shift_sync(self, guild_id: int, user_id: int) -> float | None:
        connection = self._connect()
        try:
            (last,) = connection.execute(
                "SELECT MAX(created_at) FROM work_shifts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return float(last) if last is not None else None
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

    async def branch_workers(
        self, guild_id: int, job: str, branch: str, since: float
    ) -> tuple[int, ...]:
        """Quienes han fichado desde `since` en una rama de un oficio, del puesto más alto.

        La rama es la de su contrato en ese oficio (`work_history`); el puesto, el más
        alto con el que ficharon. A igual puesto, el que antes llegó a la base de datos.
        """
        return await self._run(self._branch_workers_sync, guild_id, job, branch, since)

    def _branch_workers_sync(
        self, guild_id: int, job: str, branch: str, since: float
    ) -> tuple[int, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT s.user_id, MAX(s.level) AS top FROM work_shifts s
                JOIN work_history h
                    ON h.guild_id = s.guild_id AND h.user_id = s.user_id AND h.job = s.job
                WHERE s.guild_id = ? AND s.job = ? AND h.branch = ? AND s.created_at > ?
                GROUP BY s.user_id
                ORDER BY top DESC, s.user_id
                """,
                (guild_id, job, branch, since),
            ).fetchall()
        finally:
            connection.close()
        return tuple(int(row["user_id"]) for row in rows)

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
                "work_retirements",
                "work_pension_claims",
            ):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            connection.execute("COMMIT")
        finally:
            connection.close()
