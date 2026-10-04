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
- `economy_daily`: último IMV (la recompensa diaria, exenta de IRPF)
  reclamado y racha actual.
- `economy_wallets` con `user_id = STATE_ACCOUNT_ID`: la cuenta del Estado,
  donde acaba todo lo recaudado. Empieza en 0, no con el saldo de bienvenida.
- `economy_gambling_days`: resultado neto del casino por usuario y día
  (premios menos apuestas) y lo retenido sobre él. La retención siempre
  corresponde a la ganancia neta del día: si después se pierde, se devuelve.
- `economy_declarations`: declaración semanal del casino por usuario y
  semana (lunes ISO): lo que sale a devolver y si está pendiente,
  presentada, caducada o sin nada que devolver (`none`).
- `economy_tax_records`: un registro por ingreso sujeto a IRPF, con lo
  retenido. Sirve para proyectar la renta anual (ver `bot.services.taxes`)
  y, en el futuro, para la declaración anual.
- `economy_wealth_weeks` y `economy_wealth_tax`: semanas en las que ya se
  cobró el Impuesto sobre el Patrimonio y lo que pagó cada uno.
- `economy_wallets` con `user_id = SLOTS_POT_ACCOUNT_ID`: el bote común de la
  tragaperras. Crece con una parte de cada apuesta y se lo lleva entero
  quien saque el jackpot; la casa lo vuelve a sembrar al vaciarse.
- `economy_slots_jackpots`: cada jackpot de la tragaperras (quién, cuánto y
  cuándo), para enseñar el último en la máquina.
- `economy_wallets` con `user_id = SHOP_ACCOUNT_ID`: la caja de la tienda, que
  se queda con la base imponible de lo que se vende.
- `economy_consumption_tax`: el IGIC de cada compra de la tienda (y, con signo
  negativo, el de cada devolución), para que `hacienda` lo cuente.
- `economy_donations`: donativos a las ONGs. Cada ONG tiene su monedero con
  un `user_id` negativo (ver `bot.services.donations`), así el dinero donado
  no desaparece: se queda en la ONG, que es lo que ella quería.

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

#: `user_id` de la cuenta del Estado en `economy_wallets`. Ningún usuario de
#: Discord tiene id 0, así que no choca con nadie. Recibe todo lo que se
#: recauda; qué se hace con ese dinero está por decidir.
STATE_ACCOUNT_ID = 0

#: `user_id` del bote de la tragaperras. Negativo, como las ONGs (que usan
#: -1, -2…), para que no pague Patrimonio ni salga en las cuentas de miembros.
SLOTS_POT_ACCOUNT_ID = -100

#: `user_id` de la caja de la tienda: recibe la base imponible de cada compra
#: (el IGIC va al Estado). Así lo gastado no desaparece del libro.
SHOP_ACCOUNT_ID = -200


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


@dataclass(frozen=True, slots=True)
class BetSettlement:
    """Resultado de mover dinero de una apuesta, con el IRPF del día.

    Attributes:
        balance: Saldo final del jugador.
        tax_delta: Retención aplicada ahora; negativa si es una devolución.
        day_net: Resultado neto del casino hoy (premios menos apuestas).
        day_withheld: Total retenido hoy tras este movimiento.
    """

    balance: int
    tax_delta: int = 0
    day_net: int = 0
    day_withheld: int = 0


@dataclass(frozen=True, slots=True)
class SlotsSettlement:
    """Resultado de mover el dinero de una tirada de tragaperras.

    Attributes:
        bet: Saldo e IRPF del jugador, como en cualquier apuesta.
        jackpot: Lo que se ha llevado del bote (0 si no hay jackpot).
        pot: Bote tras la tirada (ya resembrado si se vació).
    """

    bet: BetSettlement
    jackpot: int
    pot: int


@dataclass(frozen=True, slots=True)
class JackpotRecord:
    """Un jackpot de la tragaperras."""

    user_id: int
    amount: int
    won_at: float


@dataclass(frozen=True, slots=True)
class Treasury:
    """Resumen de la cuenta del Estado de un servidor.

    Attributes:
        balance: Saldo actual de la cuenta.
        collected_total: Todo lo recaudado desde siempre.
        collected_since: Lo recaudado desde el instante pedido (p. ej. el año).
        top_contributors: `(user_id, total retenido)` de quienes más han
            pagado, de más a menos.
    """

    balance: int
    collected_total: int
    collected_since: int
    top_contributors: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class WealthCharge:
    """Lo que pagó un miembro de Impuesto sobre el Patrimonio en una semana."""

    user_id: int
    balance: int
    tax: int


@dataclass(frozen=True, slots=True)
class WealthRun:
    """Resultado de pasar el Patrimonio por un servidor.

    Attributes:
        done_before: La semana ya estaba cobrada; no se ha tocado nada.
        activation: Era la primera vez en este servidor: se marca la semana
            sin cobrar, para que nadie pague por sorpresa al desplegar.
        charges: Quién ha pagado, de más a menos.
    """

    done_before: bool = False
    activation: bool = False
    charges: tuple[WealthCharge, ...] = ()


@dataclass(frozen=True, slots=True)
class DonationReceipt:
    """Resultado de un donativo.

    Attributes:
        balance: Saldo de quien dona tras donar.
        ongs_supported: A cuántas ONGs distintas ha donado ya.
        donated_week: Lo que lleva donado esta semana.
    """

    balance: int
    ongs_supported: int
    donated_week: int


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

                CREATE TABLE IF NOT EXISTS economy_tax_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    concept TEXT NOT NULL,
                    gross INTEGER NOT NULL,
                    withheld INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_tax_records_member
                    ON economy_tax_records (guild_id, user_id, created_at);

                CREATE TABLE IF NOT EXISTS economy_gambling_days (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    net INTEGER NOT NULL DEFAULT 0,
                    withheld INTEGER NOT NULL DEFAULT 0 CHECK (withheld >= 0),
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, day)
                );

                CREATE TABLE IF NOT EXISTS economy_slots_jackpots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    won_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_wealth_weeks (
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_wealth_tax (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    balance INTEGER NOT NULL,
                    tax INTEGER NOT NULL CHECK (tax > 0),
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    ong TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    week_start TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_donations_member
                    ON economy_donations (guild_id, user_id, week_start);

                CREATE TABLE IF NOT EXISTS economy_consumption_tax (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    base INTEGER NOT NULL,
                    tax INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_declarations (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    refund INTEGER NOT NULL CHECK (refund >= 0),
                    status TEXT NOT NULL
                        CHECK (status IN ('pending', 'claimed', 'expired', 'none')),
                    claimed_at REAL,
                    PRIMARY KEY (guild_id, user_id, week_start)
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

    # -- Ingresos con retención -----------------------------------------------------

    def _credit_income_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        now: float,
        withhold: Callable[[int, int], int],
        window_seconds: float,
    ) -> tuple[int, int]:
        """Ingresa `gross`, retiene lo que diga `withhold` y lo registra.

        En el libro quedan dos movimientos, como en una nómina: el bruto
        (`concept`) y la retención (`irpf:concept`). La retención entra en la
        cuenta del Estado en la misma transacción.

        Args:
            withhold: Recibe `(bruto, ingresos de la ventana)` y devuelve la
                retención en Y$. La regla vive en `bot.services.taxes`.
            window_seconds: Ventana hacia atrás cuyos ingresos se pasan a
                `withhold`.

        Returns:
            `(retención, saldo_final)`.
        """
        recent = self._recent_taxable_in(connection, guild_id, user_id, now - window_seconds)
        tax = max(0, min(gross, withhold(gross, recent)))
        entries = [LedgerEntry(gross, concept)]
        if tax:
            entries.append(LedgerEntry(-tax, f"irpf:{concept}"))
        balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
        if tax:
            self._credit_state_in(connection, guild_id, tax, f"irpf:{concept}")
        connection.execute(
            """
            INSERT INTO economy_tax_records
                (guild_id, user_id, created_at, concept, gross, withheld)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, now, concept, gross, tax),
        )
        return tax, balance

    async def credit_income(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        now: float,
        withhold: Callable[[int, int], int],
        window_seconds: float,
    ) -> tuple[int, int]:
        """Versión atómica e independiente de `_credit_income_in`.

        Returns:
            `(retención, saldo_final)`.
        """
        return await self._run(
            self._credit_income_sync,
            guild_id,
            user_id,
            gross,
            concept,
            now,
            withhold,
            window_seconds,
        )

    def _credit_income_sync(
        self,
        guild_id: int,
        user_id: int,
        gross: int,
        concept: str,
        now: float,
        withhold: Callable[[int, int], int],
        window_seconds: float,
    ) -> tuple[int, int]:
        with self._transaction() as connection:
            return self._credit_income_in(
                connection,
                guild_id,
                user_id,
                gross=gross,
                concept=concept,
                now=now,
                withhold=withhold,
                window_seconds=window_seconds,
            )

    @staticmethod
    def _recent_taxable_in(
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        since: float,
        *,
        exclude_day: str | None = None,
    ) -> int:
        """Renta sujeta desde `since`: ingresos con retención más días de casino en positivo."""
        (income,) = connection.execute(
            """
            SELECT COALESCE(SUM(gross), 0) FROM economy_tax_records
            WHERE guild_id = ? AND user_id = ? AND created_at > ?
            """,
            (guild_id, user_id, since),
        ).fetchone()
        (gambling,) = connection.execute(
            """
            SELECT COALESCE(SUM(MAX(net, 0)), 0) FROM economy_gambling_days
            WHERE guild_id = ? AND user_id = ? AND updated_at > ? AND day != ?
            """,
            (guild_id, user_id, since, exclude_day or ""),
        ).fetchone()
        return int(income) + int(gambling)

    # -- Casino con IRPF -----------------------------------------------------------

    async def settle_gamble(
        self,
        guild_id: int,
        user_id: int,
        entries: Sequence[LedgerEntry],
        *,
        day: str,
        now: float,
        adjust_tax: bool,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
    ) -> BetSettlement:
        """Mueve el dinero de una apuesta y ajusta el IRPF del día, todo atómico.

        El resultado neto del día cambia en la suma de `entries`. Si
        `adjust_tax`, la retención del día se recalcula para que sea siempre
        la que corresponde a la ganancia neta: se cobra la diferencia o, si
        las pérdidas la han reducido, se devuelve desde la cuenta del Estado.

        Args:
            day: Día (ISO, hora canaria) al que se imputa el movimiento.
            adjust_tax: `False` para cobrar una apuesta cuyo resultado llega
                después (blackjack): la retención se ajusta al pagar.
            day_tax: Recibe `(ganancia neta del día ≥ 0, renta de los otros
                días de la ventana)` y devuelve la retención total del día.

        Raises:
            InsufficientFundsError: Si el jugador no cubre la apuesta.
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        return await self._run(
            self._settle_gamble_sync,
            guild_id,
            user_id,
            tuple(entries),
            day,
            now,
            adjust_tax,
            day_tax,
            window_seconds,
        )

    def _settle_gamble_sync(
        self,
        guild_id: int,
        user_id: int,
        entries: tuple[LedgerEntry, ...],
        day: str,
        now: float,
        adjust_tax: bool,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
    ) -> BetSettlement:
        with self._transaction() as connection:
            return self._settle_gamble_in(
                connection,
                guild_id,
                user_id,
                entries,
                day=day,
                now=now,
                adjust_tax=adjust_tax,
                day_tax=day_tax,
                window_seconds=window_seconds,
            )

    def _settle_gamble_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        entries: Sequence[LedgerEntry],
        *,
        day: str,
        now: float,
        adjust_tax: bool,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
    ) -> BetSettlement:
        """Cuerpo de `settle_gamble` dentro de una transacción ya abierta."""
        balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
        connection.execute(
            """
            INSERT INTO economy_gambling_days (guild_id, user_id, day, net, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, day) DO UPDATE SET
                net = net + excluded.net, updated_at = excluded.updated_at
            """,
            (guild_id, user_id, day, sum(entry.delta for entry in entries), now),
        )
        row = connection.execute(
            """
            SELECT net, withheld FROM economy_gambling_days
            WHERE guild_id = ? AND user_id = ? AND day = ?
            """,
            (guild_id, user_id, day),
        ).fetchone()
        net, withheld = int(row["net"]), int(row["withheld"])
        if not adjust_tax:
            return BetSettlement(balance, 0, net, withheld)

        others = self._recent_taxable_in(
            connection, guild_id, user_id, now - window_seconds, exclude_day=day
        )
        delta = max(0, day_tax(max(net, 0), others)) - withheld
        if delta > 0:
            # Nunca deja al jugador en negativo; lo que falte se cobra en
            # el siguiente ajuste, porque el objetivo se recalcula siempre.
            delta = min(delta, balance)
            if delta:
                balance = self._apply_in_transaction(
                    connection, guild_id, user_id, (LedgerEntry(-delta, "irpf:juego"),)
                )
                self._credit_state_in(connection, guild_id, delta, "irpf:juego")
        elif delta < 0:
            refund = min(-delta, self._state_balance_in(connection, guild_id))
            delta = -refund
            if refund:
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    STATE_ACCOUNT_ID,
                    (LedgerEntry(-refund, "devolucion:irpf:juego"),),
                )
                balance = self._apply_in_transaction(
                    connection,
                    guild_id,
                    user_id,
                    (LedgerEntry(refund, "devolucion:irpf:juego"),),
                )
        withheld += delta
        connection.execute(
            """
            UPDATE economy_gambling_days SET withheld = ?
            WHERE guild_id = ? AND user_id = ? AND day = ?
            """,
            (withheld, guild_id, user_id, day),
        )
        return BetSettlement(balance, delta, net, withheld)

    # -- Tragaperras -----------------------------------------------------------------

    def _pot_in(self, connection: sqlite3.Connection, guild_id: int, seed: int) -> int:
        """Saldo del bote; lo crea con `seed` la primera vez."""
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, SLOTS_POT_ACCOUNT_ID),
        ).fetchone()
        if row is not None:
            return int(row["balance"])
        connection.execute(
            "INSERT INTO economy_wallets (guild_id, user_id, balance) VALUES (?, ?, 0)",
            (guild_id, SLOTS_POT_ACCOUNT_ID),
        )
        if not seed:
            return 0
        return self._apply_in_transaction(
            connection, guild_id, SLOTS_POT_ACCOUNT_ID, (LedgerEntry(seed, "bote:semilla"),)
        )

    async def slots_pot(self, guild_id: int, *, seed: int) -> int:
        """Bote actual de la tragaperras; lo siembra con `seed` si no existía."""
        return await self._run(self._slots_pot_sync, guild_id, seed)

    def _slots_pot_sync(self, guild_id: int, seed: int) -> int:
        with self._transaction() as connection:
            return self._pot_in(connection, guild_id, seed)

    async def last_jackpot(self, guild_id: int) -> JackpotRecord | None:
        """Último jackpot del servidor, si ha habido alguno."""
        return await self._run(self._last_jackpot_sync, guild_id)

    def _last_jackpot_sync(self, guild_id: int) -> JackpotRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT user_id, amount, won_at FROM economy_slots_jackpots
                WHERE guild_id = ? ORDER BY id DESC LIMIT 1
                """,
                (guild_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return JackpotRecord(int(row["user_id"]), int(row["amount"]), float(row["won_at"]))

    async def settle_slots(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str,
        stake: int,
        payout: int,
        share: int,
        jackpot: bool,
        seed: int,
        day: str,
        now: float,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
    ) -> SlotsSettlement:
        """Cobra una tirada, paga la línea y mueve el bote, todo en una transacción.

        Para el jugador es una apuesta más (`settle_gamble`): la apuesta, el
        premio y, si hay jackpot, el bote entran en su neto de juego del día
        y en su IRPF. Del dinero apostado, `share` pasa al bote; el resto se
        lo queda la casa. Con jackpot, el jugador se lleva el bote entero
        (con la parte de esta misma tirada) y la casa lo vuelve a sembrar.

        Args:
            stake: Apuesta cobrada; 0 en un giro gratis.
            payout: Lo que devuelve la línea, apuesta incluida.
            share: Parte de `stake` que va al bote.
            seed: Lo que pone la casa en un bote nuevo o recién vaciado.

        Raises:
            InsufficientFundsError: Si el jugador no cubre la apuesta. No se
                mueve nada, tampoco el bote.
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        return await self._run(
            self._settle_slots_sync,
            guild_id,
            user_id,
            game,
            stake,
            payout,
            share,
            jackpot,
            seed,
            day,
            now,
            day_tax,
            window_seconds,
        )

    def _settle_slots_sync(
        self,
        guild_id: int,
        user_id: int,
        game: str,
        stake: int,
        payout: int,
        share: int,
        jackpot: bool,
        seed: int,
        day: str,
        now: float,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
    ) -> SlotsSettlement:
        if stake < 0 or payout < 0 or not 0 <= share <= stake:
            raise ValueError("Movimiento de tragaperras inválido.")
        with self._transaction() as connection:
            pot = self._pot_in(connection, guild_id, seed)
            won = pot + share if jackpot else 0
            entries = []
            if stake:
                entries.append(LedgerEntry(-stake, f"{game}:apuesta"))
            if payout:
                entries.append(LedgerEntry(payout, f"{game}:premio"))
            if won:
                entries.append(LedgerEntry(won, f"{game}:bote"))
            bet = self._settle_gamble_in(
                connection,
                guild_id,
                user_id,
                entries,
                day=day,
                now=now,
                adjust_tax=True,
                day_tax=day_tax,
                window_seconds=window_seconds,
            )
            pot_moves = []
            if share:
                pot_moves.append(LedgerEntry(share, f"{game}:aporte"))
            if won:
                pot_moves.append(LedgerEntry(-won, f"{game}:bote"))
                if seed:
                    pot_moves.append(LedgerEntry(seed, "bote:semilla"))
                connection.execute(
                    """
                    INSERT INTO economy_slots_jackpots (guild_id, user_id, amount, won_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (guild_id, user_id, won, now),
                )
            if pot_moves:
                pot = self._apply_in_transaction(
                    connection, guild_id, SLOTS_POT_ACCOUNT_ID, pot_moves
                )
            return SlotsSettlement(bet=bet, jackpot=won, pot=pot)

    # -- Declaración semanal -------------------------------------------------------

    def _sync_declarations_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        *,
        current_week: str,
        week_end: Callable[[str], float],
        refund_for: Callable[[int, int, int], int],
        window_seconds: float,
        keep: int,
        deduction_for: Callable[[int, int, int], int] | None = None,
    ) -> None:
        """Crea las declaraciones de semanas cerradas que falten y caduca las viejas.

        Se calcula al vuelo cuando el usuario juega o pregunta, así no hace
        falta ninguna tarea que recorra a todo el mundo cada lunes. Hay
        declaración en las semanas con casino o con donativos.

        Args:
            current_week: Lunes (ISO) de la semana en curso; solo se declaran
                las anteriores.
            week_end: Fin (epoch) de una semana dado su lunes ISO.
            refund_for: Recibe `(neto, retenido, renta de otros ingresos en los
                30 días previos al cierre)` y devuelve lo que sale a devolver
                del casino.
            keep: Cuántas declaraciones pendientes se guardan como máximo; las
                más antiguas caducan y el dinero se queda en el Estado.
            deduction_for: Recibe `(donado, base de la semana, IRPF pagado y no
                devuelto)` y devuelve la deducción por donativos. `None`, sin
                deducción.
        """
        # `date(day, '-6 days', 'weekday 1')` es el lunes de la semana de `day`.
        weeks = connection.execute(
            """
            SELECT week FROM (
                SELECT date(day, '-6 days', 'weekday 1') AS week FROM economy_gambling_days
                WHERE guild_id = :guild AND user_id = :user AND day < :current
                UNION
                SELECT week_start AS week FROM economy_donations
                WHERE guild_id = :guild AND user_id = :user AND week_start < :current
            )
            WHERE week NOT IN (
                SELECT week_start FROM economy_declarations
                WHERE guild_id = :guild AND user_id = :user
            )
            ORDER BY week
            """,
            {"guild": guild_id, "user": user_id, "current": current_week},
        ).fetchall()
        for (week,) in weeks:
            end = week_end(week)
            start = end - 7 * 86_400
            net, withheld = connection.execute(
                """
                SELECT COALESCE(SUM(net), 0), COALESCE(SUM(withheld), 0)
                FROM economy_gambling_days
                WHERE guild_id = ? AND user_id = ? AND date(day, '-6 days', 'weekday 1') = ?
                """,
                (guild_id, user_id, week),
            ).fetchone()
            (others,) = connection.execute(
                """
                SELECT COALESCE(SUM(gross), 0) FROM economy_tax_records
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND created_at <= ?
                """,
                (guild_id, user_id, end - window_seconds, end),
            ).fetchone()
            refund = max(0, refund_for(int(net), int(withheld), int(others)))
            if deduction_for is not None:
                refund += self._donation_refund_in(
                    connection,
                    guild_id,
                    user_id,
                    week=week,
                    start=start,
                    end=end,
                    gambling_net=int(net),
                    gambling_withheld=int(withheld) - refund,
                    deduction_for=deduction_for,
                )
            connection.execute(
                """
                INSERT INTO economy_declarations (guild_id, user_id, week_start, refund, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, week, refund, "pending" if refund else "none"),
            )
        connection.execute(
            """
            UPDATE economy_declarations SET status = 'expired'
            WHERE guild_id = ? AND user_id = ? AND status = 'pending' AND week_start NOT IN (
                SELECT week_start FROM economy_declarations
                WHERE guild_id = ? AND user_id = ? AND status = 'pending'
                ORDER BY week_start DESC LIMIT ?
            )
            """,
            (guild_id, user_id, guild_id, user_id, keep),
        )

    @staticmethod
    def _donation_refund_in(
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        *,
        week: str,
        start: float,
        end: float,
        gambling_net: int,
        gambling_withheld: int,
        deduction_for: Callable[[int, int, int], int],
    ) -> int:
        """Deducción por los donativos de `week`, limitada por el IRPF de esa semana.

        La cuota es lo retenido en la semana (ingresos más casino, ya sin lo que
        devuelve el casino) y la base, la renta sujeta de la semana.
        """
        (donated,) = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) FROM economy_donations
            WHERE guild_id = ? AND user_id = ? AND week_start = ?
            """,
            (guild_id, user_id, week),
        ).fetchone()
        if not donated:
            return 0
        gross, income_withheld = connection.execute(
            """
            SELECT COALESCE(SUM(gross), 0), COALESCE(SUM(withheld), 0) FROM economy_tax_records
            WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND created_at < ?
            """,
            (guild_id, user_id, start, end),
        ).fetchone()
        base = int(gross) + max(gambling_net, 0)
        tax_paid = int(income_withheld) + max(gambling_withheld, 0)
        return max(0, deduction_for(int(donated), base, tax_paid))

    @staticmethod
    def _pending_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int
    ) -> list[tuple[str, int]]:
        rows = connection.execute(
            """
            SELECT week_start, refund FROM economy_declarations
            WHERE guild_id = ? AND user_id = ? AND status = 'pending'
            ORDER BY week_start
            """,
            (guild_id, user_id),
        ).fetchall()
        return [(str(r["week_start"]), int(r["refund"])) for r in rows]

    async def pending_declarations(
        self, guild_id: int, user_id: int, **rules: object
    ) -> list[tuple[str, int]]:
        """Declaraciones pendientes `(lunes ISO, a devolver)`, de la más antigua a la última.

        `rules` son los argumentos con nombre de `_sync_declarations_in`.
        """
        return await self._run(self._pending_sync, guild_id, user_id, rules)

    def _pending_sync(
        self, guild_id: int, user_id: int, rules: dict[str, object]
    ) -> list[tuple[str, int]]:
        with self._transaction() as connection:
            self._sync_declarations_in(connection, guild_id, user_id, **rules)  # type: ignore[arg-type]
            return self._pending_in(connection, guild_id, user_id)

    async def claim_declarations(
        self, guild_id: int, user_id: int, *, now: float, **rules: object
    ) -> tuple[list[tuple[str, int]], int, int]:
        """Presenta todas las declaraciones pendientes y paga la devolución.

        El dinero sale de la cuenta del Estado, que es donde está lo retenido.

        Returns:
            `(semanas presentadas, total devuelto, saldo final)`.
        """
        return await self._run(self._claim_sync, guild_id, user_id, now, rules)

    def _claim_sync(
        self, guild_id: int, user_id: int, now: float, rules: dict[str, object]
    ) -> tuple[list[tuple[str, int]], int, int]:
        with self._transaction() as connection:
            self._sync_declarations_in(connection, guild_id, user_id, **rules)  # type: ignore[arg-type]
            pending = self._pending_in(connection, guild_id, user_id)
            total = min(
                sum(refund for _, refund in pending), self._state_balance_in(connection, guild_id)
            )
            connection.execute(
                """
                UPDATE economy_declarations SET status = 'claimed', claimed_at = ?
                WHERE guild_id = ? AND user_id = ? AND status = 'pending'
                """,
                (now, guild_id, user_id),
            )
            if total:
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    STATE_ACCOUNT_ID,
                    (LedgerEntry(-total, "devolucion:renta"),),
                )
                balance = self._apply_in_transaction(
                    connection, guild_id, user_id, (LedgerEntry(total, "devolucion:renta"),)
                )
            else:
                balance = self._ensure_wallet(connection, guild_id, user_id)
            return pending, total, balance

    @staticmethod
    def _state_balance_in(connection: sqlite3.Connection, guild_id: int) -> int:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, STATE_ACCOUNT_ID),
        ).fetchone()
        return int(row["balance"]) if row else 0

    def _credit_state_in(
        self, connection: sqlite3.Connection, guild_id: int, amount: int, reason: str
    ) -> None:
        """Ingresa `amount` en la cuenta del Estado (se abre a 0 si no existía)."""
        connection.execute(
            """
            INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
            VALUES (?, ?, 0)
            """,
            (guild_id, STATE_ACCOUNT_ID),
        )
        self._apply_in_transaction(
            connection, guild_id, STATE_ACCOUNT_ID, (LedgerEntry(amount, reason),)
        )

    async def treasury(self, guild_id: int, since: float, top: int) -> Treasury:
        """Saldo y recaudación de la cuenta del Estado del servidor."""
        return await self._run(self._treasury_sync, guild_id, since, top)

    def _treasury_sync(self, guild_id: int, since: float, top: int) -> Treasury:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
                (guild_id, STATE_ACCOUNT_ID),
            ).fetchone()
            # Retenciones de ingresos y del casino, Patrimonio e IGIC, en una sola vista.
            taxes = """
                SELECT user_id, withheld, created_at AS at FROM economy_tax_records
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, withheld, updated_at AS at FROM economy_gambling_days
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_wealth_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_consumption_tax
                WHERE guild_id = :guild
            """
            total, recent = connection.execute(
                f"""
                SELECT COALESCE(SUM(withheld), 0),
                       COALESCE(SUM(CASE WHEN at >= :since THEN withheld END), 0)
                FROM ({taxes})
                """,
                {"guild": guild_id, "since": since},
            ).fetchone()
            contributors = connection.execute(
                f"""
                SELECT user_id, SUM(withheld) AS paid FROM ({taxes})
                GROUP BY user_id HAVING paid > 0
                ORDER BY paid DESC, user_id ASC LIMIT :top
                """,
                {"guild": guild_id, "top": top},
            ).fetchall()
            return Treasury(
                balance=int(row["balance"]) if row else 0,
                collected_total=int(total),
                collected_since=int(recent),
                top_contributors=tuple((int(r["user_id"]), int(r["paid"])) for r in contributors),
            )
        finally:
            connection.close()

    # -- Impuesto sobre el Patrimonio -------------------------------------------------

    async def charge_wealth_tax(
        self,
        guild_id: int,
        *,
        week: str,
        now: float,
        tax_for: Callable[[int], int],
    ) -> WealthRun:
        """Cobra el Patrimonio de `week` a todos los miembros del servidor, una vez.

        Todo va en una transacción: o pagan todos o nadie, y la semana queda
        marcada. Lo cobrado va a la cuenta del Estado. Las cuentas especiales
        (Estado y ONGs, con `user_id` <= 0) no pagan.

        Args:
            week: Lunes ISO de la semana que se cierra.
            tax_for: Cuota en Y$ para un saldo (regla en `bot.services.taxes`).
        """
        return await self._run(self._charge_wealth_sync, guild_id, week, now, tax_for)

    def _charge_wealth_sync(
        self, guild_id: int, week: str, now: float, tax_for: Callable[[int], int]
    ) -> WealthRun:
        with self._transaction() as connection:
            done = connection.execute(
                "SELECT 1 FROM economy_wealth_weeks WHERE guild_id = ? AND week_start = ?",
                (guild_id, week),
            ).fetchone()
            if done is not None:
                return WealthRun(done_before=True)
            (previous,) = connection.execute(
                "SELECT COUNT(*) FROM economy_wealth_weeks WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            connection.execute(
                "INSERT INTO economy_wealth_weeks (guild_id, week_start, processed_at) "
                "VALUES (?, ?, ?)",
                (guild_id, week, now),
            )
            if not previous:
                return WealthRun(activation=True)
            wallets = connection.execute(
                """
                SELECT user_id, balance FROM economy_wallets
                WHERE guild_id = ? AND user_id > 0 AND balance > 0
                """,
                (guild_id,),
            ).fetchall()
            charges = []
            for row in wallets:
                user_id, balance = int(row["user_id"]), int(row["balance"])
                tax = min(balance, max(0, tax_for(balance)))
                if not tax:
                    continue
                self._apply_in_transaction(
                    connection, guild_id, user_id, (LedgerEntry(-tax, "patrimonio"),)
                )
                self._credit_state_in(connection, guild_id, tax, "patrimonio")
                connection.execute(
                    """
                    INSERT INTO economy_wealth_tax
                        (guild_id, user_id, week_start, balance, tax, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, week, balance, tax, now),
                )
                charges.append(WealthCharge(user_id, balance, tax))
            charges.sort(key=lambda c: (-c.tax, c.user_id))
            return WealthRun(charges=tuple(charges))

    # -- Donativos ---------------------------------------------------------------------

    async def donate(
        self,
        guild_id: int,
        user_id: int,
        *,
        ong: str,
        ong_account: int,
        amount: int,
        week: str,
        now: float,
    ) -> DonationReceipt:
        """Pasa `amount` del miembro a la cuenta de la ONG y apunta el donativo.

        Raises:
            InsufficientFundsError: Si no tiene tanto. No se mueve nada.
        """
        return await self._run(
            self._donate_sync, guild_id, user_id, ong, ong_account, amount, week, now
        )

    def _donate_sync(
        self,
        guild_id: int,
        user_id: int,
        ong: str,
        ong_account: int,
        amount: int,
        week: str,
        now: float,
    ) -> DonationReceipt:
        if ong_account >= 0:
            raise ValueError("Las cuentas de ONG tienen id negativo.")
        with self._transaction() as connection:
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-amount, f"donativo:{ong}"),)
            )
            connection.execute(
                "INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance) "
                "VALUES (?, ?, 0)",
                (guild_id, ong_account),
            )
            self._apply_in_transaction(
                connection, guild_id, ong_account, (LedgerEntry(amount, "donativo"),)
            )
            connection.execute(
                """
                INSERT INTO economy_donations
                    (guild_id, user_id, ong, amount, week_start, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, ong, amount, week, now),
            )
            ongs, week_total = connection.execute(
                """
                SELECT COUNT(DISTINCT ong),
                       COALESCE(SUM(CASE WHEN week_start = ? THEN amount END), 0)
                FROM economy_donations WHERE guild_id = ? AND user_id = ?
                """,
                (week, guild_id, user_id),
            ).fetchone()
            return DonationReceipt(balance, int(ongs), int(week_total))

    # -- Compras de la tienda --------------------------------------------------------

    def _open_empty_wallet(
        self, connection: sqlite3.Connection, guild_id: int, account_id: int
    ) -> None:
        """Abre a 0 una cuenta que no es de un miembro (sin saldo de bienvenida)."""
        connection.execute(
            "INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance) VALUES (?, ?, 0)",
            (guild_id, account_id),
        )

    async def purchase(
        self,
        guild_id: int,
        user_id: int,
        *,
        base: int,
        tax: int,
        concept: str,
        now: float,
        reserve: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, int]:
        """Cobra una compra: la base a la caja de la tienda y el IGIC al Estado.

        `reserve` lo pone la tienda y corre dentro de la misma transacción,
        antes del cobro: comprueba existencias y límites y apunta la venta. Si
        lanza, o si al miembro no le llega, no se mueve nada.

        Returns:
            `(lo que devuelva reserve, saldo final)`.

        Raises:
            InsufficientFundsError: Si no le llega el saldo.
        """
        return await self._run(
            self._purchase_sync, guild_id, user_id, base, tax, concept, now, reserve
        )

    def _purchase_sync(
        self,
        guild_id: int,
        user_id: int,
        base: int,
        tax: int,
        concept: str,
        now: float,
        reserve: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, int]:
        if base <= 0 or tax < 0:
            raise ValueError("Una compra necesita base positiva e IGIC no negativo.")
        with self._transaction() as connection:
            result = reserve(connection)
            entries = [LedgerEntry(-base, f"tienda:{concept}")]
            if tax:
                entries.append(LedgerEntry(-tax, "tienda:igic"))
            balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
            self._open_empty_wallet(connection, guild_id, SHOP_ACCOUNT_ID)
            self._apply_in_transaction(
                connection, guild_id, SHOP_ACCOUNT_ID, (LedgerEntry(base, "tienda:venta"),)
            )
            if tax:
                self._credit_state_in(connection, guild_id, tax, "igic:tienda")
            connection.execute(
                """
                INSERT INTO economy_consumption_tax
                    (guild_id, user_id, concept, base, tax, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, concept, base, tax, now),
            )
            return result, balance

    async def refund_purchase(
        self,
        guild_id: int,
        user_id: int,
        *,
        base: int,
        tax: int,
        concept: str,
        now: float,
        release: Callable[[sqlite3.Connection], None],
    ) -> int:
        """Deshace una compra: la caja devuelve la base y el Estado el IGIC.

        `release` lo pone la tienda (marca la venta como devuelta y repone la
        unidad) y corre en la misma transacción.

        Returns:
            El saldo final del miembro.
        """
        return await self._run(
            self._refund_purchase_sync, guild_id, user_id, base, tax, concept, now, release
        )

    def _refund_purchase_sync(
        self,
        guild_id: int,
        user_id: int,
        base: int,
        tax: int,
        concept: str,
        now: float,
        release: Callable[[sqlite3.Connection], None],
    ) -> int:
        with self._transaction() as connection:
            release(connection)
            self._open_empty_wallet(connection, guild_id, SHOP_ACCOUNT_ID)
            self._apply_in_transaction(
                connection, guild_id, SHOP_ACCOUNT_ID, (LedgerEntry(-base, "tienda:devolucion"),)
            )
            entries = [LedgerEntry(base, f"devolucion:{concept}")]
            if tax:
                self._apply_in_transaction(
                    connection, guild_id, STATE_ACCOUNT_ID, (LedgerEntry(-tax, "igic:devolucion"),)
                )
                entries.append(LedgerEntry(tax, "devolucion:igic"))
                # Factura rectificativa: el IGIC devuelto resta de lo recaudado.
                connection.execute(
                    """
                    INSERT INTO economy_consumption_tax
                        (guild_id, user_id, concept, base, tax, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, concept, -base, -tax, now),
                )
            return self._apply_in_transaction(connection, guild_id, user_id, entries)

    async def ong_totals(self, guild_id: int) -> dict[str, tuple[int, int]]:
        """Por ONG: `(total recaudado, donantes distintos)` en el servidor."""
        return await self._run(self._ong_totals_sync, guild_id)

    def _ong_totals_sync(self, guild_id: int) -> dict[str, tuple[int, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT ong, SUM(amount) AS total, COUNT(DISTINCT user_id) AS donors
                FROM economy_donations WHERE guild_id = ? GROUP BY ong
                """,
                (guild_id,),
            ).fetchall()
            return {str(r["ong"]): (int(r["total"]), int(r["donors"])) for r in rows}
        finally:
            connection.close()

    # -- IMV (recompensa diaria) ----------------------------------------------------

    async def claim_daily(
        self,
        guild_id: int,
        user_id: int,
        *,
        now: float,
        decide: Callable[[DailyClaim | None], tuple[int, int] | None],
    ) -> tuple[int, int] | None:
        """Cobra el IMV de forma atómica. Está exento de IRPF (art. 7.y LIRPF).

        La regla (espera, racha y cantidad) la pone el servicio a través de
        `decide`: recibe el estado anterior y devuelve `(cantidad, racha)` o
        `None` si todavía no toca. Se evalúa dentro de la transacción para
        que dos `.daily` simultáneos no cobren dos veces.

        Al estar exento no deja registro en `economy_tax_records`: no cuenta
        para la renta con la que se calculan las retenciones.

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
                connection, guild_id, user_id, (LedgerEntry(amount, "imv"),)
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
            for table in (
                "economy_wallets",
                "economy_ledger",
                "economy_daily",
                "economy_tax_records",
                "economy_gambling_days",
                "economy_declarations",
                "economy_wealth_weeks",
                "economy_wealth_tax",
                "economy_donations",
                "economy_slots_jackpots",
                "economy_consumption_tax",
            ):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
