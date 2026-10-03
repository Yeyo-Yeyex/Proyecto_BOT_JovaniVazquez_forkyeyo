"""Persistencia de la economía: monederos, libro de movimientos y recompensas.

Toda la economía del bot (casino, recompensas diarias y lo que venga después)
usa una única moneda, los yapdollars, y pasa por este repositorio. Las reglas
de negocio (cuánto se paga, cuándo) viven en `bot.services.economy`; aquí solo
se garantiza que cada movimiento sea atómico y quede registrado.

Modelo de datos:

- `economy_wallets`: saldo actual por servidor y usuario. Es una caché del
  libro: siempre coincide con la suma de sus movimientos.
- `economy_ledger`: una fila por movimiento (positivo o negativo) con su
  motivo y el saldo resultante. Permite auditar y revertir errores.
- `economy_daily`: última recompensa diaria reclamada y racha actual.

Los saldos son enteros y nunca negativos. Cada operación abre su propia
transacción `BEGIN IMMEDIATE`, así dos botones pulsados a la vez no pueden
gastar el mismo dinero dos veces.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

# Límite de seguridad muy por debajo del máximo de INTEGER en SQLite (2^63).
# Ningún uso normal se acerca; existe para que una racha absurda no desborde.
MAX_BALANCE = 10**15


class InsufficientFundsError(Exception):
    """El movimiento dejaría el saldo en negativo.

    Attributes:
        balance: Saldo disponible en el momento del intento.
    """

    def __init__(self, balance: int) -> None:
        super().__init__(f"Saldo insuficiente: {balance}")
        self.balance = balance


class BalanceLimitError(Exception):
    """El movimiento superaría `MAX_BALANCE`."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Un movimiento a aplicar sobre un monedero.

    Attributes:
        delta: Cantidad a sumar (positiva) o restar (negativa).
        reason: Motivo corto y estable, p. ej. `"ruleta:apuesta"`.
    """

    delta: int
    reason: str


@dataclass(frozen=True, slots=True)
class DailyClaim:
    """Estado de la recompensa diaria de un usuario."""

    last_claimed_at: float
    streak: int


class EconomyRepository:
    """Acceso SQLite a la economía; comparte archivo con el resto del bot.

    Args:
        database_path: Ruta del archivo SQLite persistente.
        starting_balance: Saldo con el que se abre un monedero nuevo. Se
            registra como un movimiento `"bienvenida"` para que el libro cuadre.
    """

    def __init__(self, database_path: Path, *, starting_balance: int) -> None:
        if starting_balance < 0:
            raise ValueError("El saldo inicial no puede ser negativo.")
        self.database_path = database_path
        self.starting_balance = starting_balance

    def _connect(self) -> sqlite3.Connection:
        """Abre una conexión en modo autocommit; las transacciones son explícitas."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Abre una conexión con transacción de escritura y la cierra siempre.

        `BEGIN IMMEDIATE` toma el bloqueo de escritura al empezar, antes de
        leer el saldo: dos operaciones simultáneas sobre el mismo monedero se
        serializan y la segunda ve el saldo ya actualizado por la primera.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        finally:
            connection.close()

    async def initialize(self) -> None:
        """Crea las tablas de la economía si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS economy_wallets (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    balance INTEGER NOT NULL CHECK (balance >= 0),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS economy_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    delta INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    -- julianday en vez de unixepoch(): SQLite de Debian bookworm es 3.40.
                    created_at REAL NOT NULL
                        DEFAULT ((julianday('now') - 2440587.5) * 86400.0)
                );

                CREATE INDEX IF NOT EXISTS economy_ledger_member
                    ON economy_ledger (guild_id, user_id, id);

                CREATE TABLE IF NOT EXISTS economy_daily (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_claimed_at REAL NOT NULL,
                    streak INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )
        finally:
            connection.close()

    # -- Monederos -----------------------------------------------------------------

    def _ensure_wallet(self, connection: sqlite3.Connection, guild_id: int, user_id: int) -> int:
        """Devuelve el saldo, abriendo el monedero con el saldo inicial si no existe.

        Debe llamarse dentro de una transacción ya abierta.
        """
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        if row is not None:
            return int(row["balance"])
        connection.execute(
            "INSERT INTO economy_wallets (guild_id, user_id, balance) VALUES (?, ?, ?)",
            (guild_id, user_id, self.starting_balance),
        )
        if self.starting_balance:
            self._log(
                connection,
                guild_id,
                user_id,
                self.starting_balance,
                self.starting_balance,
                "bienvenida",
            )
        return self.starting_balance

    @staticmethod
    def _log(
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        delta: int,
        balance_after: int,
        reason: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO economy_ledger (guild_id, user_id, delta, balance_after, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, delta, balance_after, reason),
        )

    async def balance(self, guild_id: int, user_id: int) -> int:
        """Devuelve el saldo del usuario, abriendo su monedero si es la primera vez."""
        return await self._run(self._balance_sync, guild_id, user_id)

    def _balance_sync(self, guild_id: int, user_id: int) -> int:
        with self._transaction() as connection:
            return self._ensure_wallet(connection, guild_id, user_id)

    async def apply(
        self,
        guild_id: int,
        user_id: int,
        entries: Sequence[LedgerEntry],
        *,
        required_balance: int = 0,
    ) -> int:
        """Aplica varios movimientos como una sola operación atómica.

        Los movimientos se aplican en orden y ninguno puede dejar el saldo
        en negativo: así una apuesta (`-100`) seguida de su premio (`+200`)
        exige tener los 100 antes de cobrar. O se aplican todos o ninguno.

        Args:
            guild_id: Servidor del monedero.
            user_id: Dueño del monedero.
            entries: Movimientos a aplicar, en orden.
            required_balance: Saldo mínimo exigido antes de empezar, para
                operaciones cuyo coste no es un movimiento directo.

        Returns:
            El saldo final.

        Raises:
            InsufficientFundsError: Si algún paso deja el saldo por debajo de 0
                o no se alcanza `required_balance`. No se aplica nada.
            BalanceLimitError: Si el saldo superaría `MAX_BALANCE`.
        """
        return await self._run(
            self._apply_sync, guild_id, user_id, tuple(entries), required_balance
        )

    def _apply_sync(
        self,
        guild_id: int,
        user_id: int,
        entries: tuple[LedgerEntry, ...],
        required_balance: int,
    ) -> int:
        with self._transaction() as connection:
            return self._apply_in_transaction(
                connection, guild_id, user_id, entries, required_balance
            )

    def _apply_in_transaction(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        entries: Sequence[LedgerEntry],
        required_balance: int = 0,
    ) -> int:
        initial = self._ensure_wallet(connection, guild_id, user_id)
        if initial < required_balance:
            raise InsufficientFundsError(initial)
        balance = initial
        for entry in entries:
            balance += entry.delta
            if balance < 0:
                raise InsufficientFundsError(initial)
            if balance > MAX_BALANCE:
                raise BalanceLimitError
            self._log(connection, guild_id, user_id, entry.delta, balance, entry.reason)
        connection.execute(
            "UPDATE economy_wallets SET balance = ? WHERE guild_id = ? AND user_id = ?",
            (balance, guild_id, user_id),
        )
        return balance

    # -- Recompensa diaria ---------------------------------------------------------

    async def claim_daily(
        self,
        guild_id: int,
        user_id: int,
        *,
        now: float,
        decide: Callable[[DailyClaim | None], tuple[int, int] | None],
    ) -> tuple[int, int] | None:
        """Cobra la recompensa diaria de forma atómica.

        La regla (espera, racha y cantidad) la pone el servicio a través de
        `decide`: recibe el estado anterior y devuelve `(cantidad, racha)` o
        `None` si todavía no toca. Se evalúa dentro de la transacción para
        que dos `.daily` simultáneos no cobren dos veces.

        Returns:
            `(cantidad, saldo_final)` si se cobró; `None` si `decide` lo rechazó.
        """
        return await self._run(self._claim_daily_sync, guild_id, user_id, now, decide)

    def _claim_daily_sync(
        self,
        guild_id: int,
        user_id: int,
        now: float,
        decide: Callable[[DailyClaim | None], tuple[int, int] | None],
    ) -> tuple[int, int] | None:
        with self._transaction() as connection:
            decision = decide(self._daily_state_in(connection, guild_id, user_id))
            if decision is None:
                return None
            amount, streak = decision
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(amount, "daily"),)
            )
            connection.execute(
                """
                INSERT INTO economy_daily (guild_id, user_id, last_claimed_at, streak)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    last_claimed_at = excluded.last_claimed_at,
                    streak = excluded.streak
                """,
                (guild_id, user_id, now, streak),
            )
            return amount, balance

    async def daily_state(self, guild_id: int, user_id: int) -> DailyClaim | None:
        """Devuelve la última recompensa diaria reclamada, si existe."""
        return await self._run(self._daily_state_sync, guild_id, user_id)

    def _daily_state_sync(self, guild_id: int, user_id: int) -> DailyClaim | None:
        connection = self._connect()
        try:
            return self._daily_state_in(connection, guild_id, user_id)
        finally:
            connection.close()

    @staticmethod
    def _daily_state_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int
    ) -> DailyClaim | None:
        row = connection.execute(
            """
            SELECT last_claimed_at, streak FROM economy_daily
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return DailyClaim(last_claimed_at=float(row["last_claimed_at"]), streak=int(row["streak"]))

    # -- Limpieza ------------------------------------------------------------------

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra toda la economía de un servidor cuando el bot sale de él."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._transaction() as connection:
            for table in ("economy_wallets", "economy_ledger", "economy_daily"):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
