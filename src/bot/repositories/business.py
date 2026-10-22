"""Persistencia de los negocios de `pala`: el negocio de cada miembro, el cupo de la demanda
y los negocios cerrados.

El dinero no pasa por aquí: va por `bot.services.economy` (`EconomyService.business`). Las
funciones `*_in` reciben la conexión de esa misma transacción, así el negocio y su dinero
se guardan juntos o no se guarda nada. Tablas:

- `business`: el negocio abierto de cada miembro (`bot.services.business.Business`). Casi
  todo va en JSON (`data`), así un campo nuevo no necesita migración. La clave lleva un
  `id` propio para que un miembro pueda tener varios en la fase 5.
- `business_demand`: clientes que ya se han llevado los negocios de un sector en un barrio
  un día (el cupo de la demanda, `business.daily_pool`). Se borra lo de hace más de
  `DEMAND_RETENTION_DAYS`.
- `business_closed`: los negocios cerrados (sector, barrio, nombre, cuándo y por qué), para
  la vida laboral y los logros.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, fields
from datetime import date, timedelta
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.repositories.economy import company_account
from bot.repositories.housing_finance import LOAN_COMPANY
from bot.services.business import Business, book_value, stock_value

T = TypeVar("T")

#: Días que se guarda el cupo de la demanda.
DEMAND_RETENTION_DAYS = 3

_FIELDS = frozenset(f.name for f in fields(Business))


def _load(row: sqlite3.Row) -> Business:
    data = json.loads(row["data"])
    # Campos que no conoce esta versión se ignoran; los nuevos toman su valor por defecto.
    business = Business(**{k: v for k, v in data.items() if k in _FIELDS and k != "id"})
    business.id = int(row["id"])
    business.user_id = int(row["user_id"])
    return business


class BusinessRepository:
    """Acceso SQLite a los negocios; comparte archivo con el resto del bot.

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

    async def read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Una lectura con una conexión propia (para mirar otras tablas, como las de `pisos`)."""
        return await self._run(self._read_sync, operation)

    def _read_sync(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        connection = self._connect()
        try:
            return operation(connection)
        finally:
            connection.close()

    async def initialize(self) -> None:
        """Crea las tablas si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS business (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS business_member ON business (guild_id, user_id);

                CREATE TABLE IF NOT EXISTS business_demand (
                    guild_id INTEGER NOT NULL,
                    barrio TEXT NOT NULL,
                    sector TEXT NOT NULL,
                    day TEXT NOT NULL,
                    used REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, barrio, sector, day)
                );

                CREATE TABLE IF NOT EXISTS business_closed (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    sector TEXT NOT NULL,
                    barrio TEXT NOT NULL,
                    name TEXT NOT NULL,
                    opened_at REAL NOT NULL,
                    closed_at REAL NOT NULL,
                    reason TEXT NOT NULL
                );
                """
            )
        finally:
            connection.close()

    # -- Dentro de una transacción ----------------------------------------------------

    @staticmethod
    def business_in(connection: sqlite3.Connection, guild_id: int, user_id: int) -> Business | None:
        """El negocio de un miembro, leído en la conexión de la transacción."""
        row = connection.execute(
            "SELECT id, user_id, data FROM business WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id LIMIT 1",
            (guild_id, user_id),
        ).fetchone()
        return _load(row) if row is not None else None

    @staticmethod
    def save_in(connection: sqlite3.Connection, guild_id: int, business: Business) -> Business:
        """Guarda el negocio (lo crea si `id` es 0) y lo devuelve con su `id`."""
        data = asdict(business)
        data.pop("id")
        text = json.dumps(data, ensure_ascii=False)
        if business.id:
            connection.execute("UPDATE business SET data = ? WHERE id = ?", (text, business.id))
        else:
            cursor = connection.execute(
                "INSERT INTO business (guild_id, user_id, data) VALUES (?, ?, ?)",
                (guild_id, business.user_id, text),
            )
            business.id = int(cursor.lastrowid or 0)
        return business

    @staticmethod
    def close_in(
        connection: sqlite3.Connection, guild_id: int, business: Business, now: float, reason: str
    ) -> None:
        """Borra un negocio abierto y lo apunta en `business_closed`."""
        connection.execute("DELETE FROM business WHERE id = ?", (business.id,))
        connection.execute(
            """
            INSERT INTO business_closed
                (guild_id, user_id, sector, barrio, name, opened_at, closed_at, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id, business.user_id, business.sector, business.barrio, business.name,
                business.opened_at, now, reason,
            ),
        )  # fmt: skip

    @staticmethod
    def pool_used_in(
        connection: sqlite3.Connection, guild_id: int, barrio: str, sector: str, day: date
    ) -> float:
        """Clientes que ya se han llevado hoy los negocios de `sector` en `barrio`."""
        row = connection.execute(
            "SELECT used FROM business_demand "
            "WHERE guild_id = ? AND barrio = ? AND sector = ? AND day = ?",
            (guild_id, barrio, sector, day.isoformat()),
        ).fetchone()
        return float(row["used"]) if row is not None else 0.0

    @staticmethod
    def take_pool_in(
        connection: sqlite3.Connection,
        guild_id: int,
        barrio: str,
        sector: str,
        day: date,
        clients: int,
    ) -> None:
        """Apunta `clients` más en el cupo de hoy y borra lo de días viejos."""
        connection.execute(
            """
            INSERT INTO business_demand (guild_id, barrio, sector, day, used)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, barrio, sector, day) DO UPDATE SET used = used + excluded.used
            """,
            (guild_id, barrio, sector, day.isoformat(), clients),
        )
        old = (day - timedelta(days=DEMAND_RETENTION_DAYS)).isoformat()
        connection.execute(
            "DELETE FROM business_demand WHERE guild_id = ? AND day < ?", (guild_id, old)
        )

    # -- Lecturas ---------------------------------------------------------------------

    async def business(self, guild_id: int, user_id: int) -> Business | None:
        """El negocio abierto de un miembro, o `None`."""
        return await self._run(self._business_sync, guild_id, user_id)

    def _business_sync(self, guild_id: int, user_id: int) -> Business | None:
        connection = self._connect()
        try:
            return self.business_in(connection, guild_id, user_id)
        finally:
            connection.close()

    async def businesses(self, guild_id: int) -> list[Business]:
        """Todos los negocios abiertos de un servidor."""
        return await self._run(self._businesses_sync, guild_id)

    def _businesses_sync(self, guild_id: int) -> list[Business]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT id, user_id, data FROM business WHERE guild_id = ? ORDER BY id",
                (guild_id,),
            ).fetchall()
        finally:
            connection.close()
        return [_load(row) for row in rows]

    async def closed(self, guild_id: int, user_id: int) -> list[sqlite3.Row]:
        """Los negocios que cerró un miembro, del más viejo al más nuevo."""
        return await self._run(self._closed_sync, guild_id, user_id)

    def _closed_sync(self, guild_id: int, user_id: int) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT sector, barrio, name, opened_at, closed_at, reason FROM business_closed "
                "WHERE guild_id = ? AND user_id = ? ORDER BY closed_at",
                (guild_id, user_id),
            ).fetchall()
        finally:
            connection.close()

    async def pool_used(self, guild_id: int, barrio: str, sector: str, day: date) -> float:
        """Clientes que ya se han llevado hoy los negocios de `sector` en `barrio`."""
        return await self._run(self._pool_used_sync, guild_id, barrio, sector, day)

    def _pool_used_sync(self, guild_id: int, barrio: str, sector: str, day: date) -> float:
        connection = self._connect()
        try:
            return self.pool_used_in(connection, guild_id, barrio, sector, day)
        finally:
            connection.close()

    async def save(self, guild_id: int, business: Business) -> Business:
        """Guarda un negocio sin mover dinero (el precio, el rótulo)."""
        return await self._run(self._save_sync, guild_id, business)

    def _save_sync(self, guild_id: int, business: Business) -> Business:
        connection = self._connect()
        try:
            return self.save_in(connection, guild_id, business)
        finally:
            connection.close()

    async def begin_turn(self, guild_id: int, business: Business, day: date, clients: int) -> None:
        """Guarda el turno que empieza y se lleva `clients` del cupo del barrio, a la vez."""
        await self._run(self._begin_turn_sync, guild_id, business, day, clients)

    def _begin_turn_sync(self, guild_id: int, business: Business, day: date, clients: int) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self.save_in(connection, guild_id, business)
                self.take_pool_in(
                    connection, guild_id, business.barrio, business.sector, day, clients
                )
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        finally:
            connection.close()

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra los negocios de un servidor (el bot ha salido de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table in ("business", "business_demand", "business_closed"):
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))  # noqa: S608
            connection.execute("COMMIT")
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


def companies_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, int]:
    """El valor de la sociedad de cada dueño, para el Patrimonio (fase 3 de los negocios).

    Es el valor teórico de sus participaciones (art. 16.Uno de la Ley 19/1991): la caja, el
    stock y lo que queda por amortizar, menos lo que debe la sociedad al casero, a Hacienda, a
    su plantilla y al banco (sus préstamos, que su dueño solo avala). Lo que le debe al socio no se resta: es
    un crédito del propio dueño y se anulan.
    """
    out: dict[int, int] = {}
    tables = {
        str(r[0])
        for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('business', 'housing_loans')"
        )
    }
    if "business" not in tables:  # sin el cog de los negocios (pruebas que no lo montan)
        return out
    bank: dict[str, int] = {}
    if "housing_loans" in tables:
        for listing, owed in connection.execute(
            "SELECT listing, SUM(balance + unpaid_amount) FROM housing_loans "
            "WHERE guild_id = ? AND status = 'activo' AND kind = ? GROUP BY listing",
            (guild_id, LOAN_COMPANY),
        ):
            bank[str(listing)] = int(owed)
    for row in connection.execute("SELECT * FROM business WHERE guild_id = ?", (guild_id,)):
        business = _load(row)
        if not business.is_company:
            continue
        cash = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, company_account(business.id)),
        ).fetchone()
        value = (
            (int(cash[0]) if cash else 0)
            + stock_value(business)
            + book_value(business)
            - business.rent_debt
            - business.company.get("tax_debt", 0)
            - business.wage_debt
            - bank.get(str(business.id), 0)
        )
        out[business.user_id] = out.get(business.user_id, 0) + value
    return out
