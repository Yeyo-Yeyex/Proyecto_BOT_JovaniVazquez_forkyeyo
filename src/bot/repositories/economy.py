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
- `economy_lottery_tax`: el gravamen especial de cada premio de lotería
  (disposición adicional 33ª LIRPF), para que `hacienda` lo cuente.
- `economy_public_debt`: deuda pública. Si el Estado tiene que pagar un
  premio de lotería y no le llega el saldo, emite deuda por lo que falta: el
  dinero se crea, queda apuntado aquí y `hacienda` lo enseña.
- `economy_donations`: donativos a las ONGs. Cada ONG tiene su monedero con
  un `user_id` negativo (ver `bot.services.donations`), así el dinero donado
  no desaparece: se queda en la ONG, que es lo que ella quería.
- `economy_bizums`: cada Bizum entre miembros (quién, a quién, cuánto y
  cuándo), sin el concepto. Sirve para sumar lo enviado en el día.
- `economy_payroll`: cada nómina de `pala` con su desglose (bruto,
  cotizaciones del trabajador y de la empresa, IRPF y neto). Las
  cotizaciones cuentan como recaudación en `hacienda`, a nombre de quien
  cobró la nómina; el neto de la última semana reduce el IMV.
- `economy_sanctions`: multas y regularizaciones (p. ej. la Inspección de
  Trabajo que pilla un turno en negro). Van al Estado y cuentan en `hacienda`.
- `economy_imv_suspensions`: hasta cuándo no se puede cobrar el IMV de un
  miembro (sanción por cobrar en negro).
- `economy_residence`: quién vive (y trabaja) fuera de España y desde cuándo.
  Mientras tanto no cobra el IMV. Las nóminas de fuera llevan su país en
  `economy_payroll.country` y lo que se queda el otro país va a su cuenta
  (`HK_ACCOUNT_ID`), no al Estado.
- `economy_interest_days` y `economy_interest`: días en los que ya se pagaron
  los intereses de la cuenta y lo que cobró cada uno (saldo medio, bruto y
  retención del 19 %), con una marca de si ya se le ha avisado.
- `economy_interest_streaks`: rachas de días seguidos de cada miembro para los
  logros de la cuenta (cobrar el máximo, no bajar de un saldo, no tocar nada).
- `economy_interest_weeks` y `economy_interest_savings`: semanas ya liquidadas
  con la escala del ahorro y lo que pagó cada uno de más. Retenciones y
  liquidaciones cuentan en `hacienda`.
- `economy_wallets` con `user_id = PORRA_ACCOUNT_ID`: el depósito de las
  porras. Guarda lo apostado hasta que la porra se resuelve o se anula; al
  liquidar se vacía entero (premios, IAJ, derechos de imagen o devoluciones).
- `economy_porra_bets`: cada apuesta a una porra (quién, a qué opción y cuánto).
  Es la verdad del dinero de cada porra: el reparto se calcula con estas filas.
- `economy_porra_settled`: porras ya liquidadas, para no pagar dos veces.
- `economy_gaming_tax`: el Impuesto sobre Actividades de Juego de cada porra,
  a nombre de cada apostante (art. 48 de la Ley 13/2011), para `hacienda`.

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
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from bot.services.interest import DayOutcome, LedgerRow, SavingsSettlement, Streaks
    from bot.services.taxes import ForeignPayslip, Payslip

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

#: `user_id` de la hacienda de Hong Kong: recibe el salaries tax y el MPF de quien
#: trabaja allí con `pala`. No es el Estado español: no cuenta en `hacienda`.
HK_ACCOUNT_ID = -300

#: `user_id` del depósito de las porras: lo apostado espera aquí a que la porra
#: se resuelva, así no desaparece del libro mientras tanto.
PORRA_ACCOUNT_ID = -400


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


class PorraClosedError(Exception):
    """La porra ya está liquidada: no admite apuestas ni otro pago."""


class PorraSideError(Exception):
    """El miembro ya apostó a otra opción de la misma porra.

    Attributes:
        outcome: La opción a la que apostó.
    """

    def __init__(self, outcome: int) -> None:
        super().__init__(f"Ya apostó a la opción {outcome}")
        self.outcome = outcome


class PorraCapError(Exception):
    """La apuesta pasaría el tope del bote.

    Attributes:
        room: Lo que aún cabe en el bote.
    """

    def __init__(self, room: int) -> None:
        super().__init__(f"Solo caben {room}")
        self.room = room


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
        debt: Deuda pública emitida para pagar premios de lotería.
    """

    balance: int
    collected_total: int
    collected_since: int
    top_contributors: tuple[tuple[int, int], ...]
    debt: int = 0


@dataclass(frozen=True, slots=True)
class LotteryPayout:
    """Un premio de lotería a pagar desde la cuenta del Estado.

    Attributes:
        user_id: Quién cobra.
        gross: Premio bruto.
        tax: Gravamen especial que se queda el Estado (ver `taxes.lottery_tax`).
        concept: Motivo corto para el libro (`"primitiva"`, `"x10"`…).
    """

    user_id: int
    gross: int
    tax: int
    concept: str


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
class InterestRun:
    """Resultado de pagar los intereses de un día en un servidor.

    Attributes:
        done_before: El día ya estaba pagado; no se ha tocado nada.
        outcomes: Un resultado por monedero de miembro, haya cobrado o no (las
            rachas y algunos logros también miran a quien no cobra).
    """

    done_before: bool = False
    outcomes: tuple[DayOutcome, ...] = ()


@dataclass(frozen=True, slots=True)
class SavingsRun:
    """Resultado de liquidar el ahorro de una semana en un servidor."""

    done_before: bool = False
    settlements: tuple[SavingsSettlement, ...] = ()


@dataclass(frozen=True, slots=True)
class InterestNotice:
    """Intereses y liquidaciones que un miembro aún no ha visto.

    Attributes:
        days: Días cobrados sin avisar.
        gross: Bruto de esos días.
        tax: Retención de esos días.
        first_day: El más antiguo (ISO), para saber cuánto tiempo ha estado fuera.
        savings: Liquidaciones del ahorro sin avisar: `(lunes ISO, cobrado, tipo %)`.
    """

    days: int = 0
    gross: int = 0
    tax: int = 0
    first_day: str | None = None
    savings: tuple[tuple[str, int, int], ...] = ()


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


@dataclass(frozen=True, slots=True)
class PorraBetReceipt:
    """Resultado de apostar a una porra.

    Attributes:
        balance: Saldo de quien apuesta, tras apostar.
        stake: Lo que lleva apostado en esa porra, esta apuesta incluida.
        pool: Bote de la porra tras la apuesta.
    """

    balance: int
    stake: int
    pool: int


@dataclass(frozen=True, slots=True)
class PorraPayment:
    """Lo que ha movido la liquidación de una porra.

    Attributes:
        bets: Resultado de cada apostante (saldo e IRPF del día), por miembro.
        image_tax: Retención de los derechos de imagen (0 si no hubo).
        subject_balance: Saldo del protagonista tras cobrarlos, o `None`.
    """

    bets: dict[int, BetSettlement]
    image_tax: int = 0
    subject_balance: int | None = None


@dataclass(frozen=True, slots=True)
class BizumReceipt:
    """Resultado de un Bizum entre dos miembros.

    Attributes:
        sender_balance: Saldo de quien envía, tras enviar.
        receiver_balance: Saldo de quien recibe, tras recibir.
        sent_today: Lo enviado hoy por quien envía, este Bizum incluido.
    """

    sender_balance: int
    receiver_balance: int
    sent_today: int


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

                CREATE TABLE IF NOT EXISTS economy_bizums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_bizums_sender
                    ON economy_bizums (guild_id, sender_id, created_at);

                CREATE TABLE IF NOT EXISTS economy_consumption_tax (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    base INTEGER NOT NULL,
                    tax INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_lottery_tax (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    gross INTEGER NOT NULL,
                    tax INTEGER NOT NULL CHECK (tax > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_public_debt (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    concept TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_payroll (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    gross INTEGER NOT NULL CHECK (gross >= 0),
                    ss_worker INTEGER NOT NULL CHECK (ss_worker >= 0),
                    ss_employer INTEGER NOT NULL CHECK (ss_employer >= 0),
                    irpf INTEGER NOT NULL CHECK (irpf >= 0),
                    net INTEGER NOT NULL,
                    country TEXT NOT NULL DEFAULT 'es',
                    foreign_tax INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_payroll_member
                    ON economy_payroll (guild_id, user_id, created_at);

                CREATE TABLE IF NOT EXISTS economy_sanctions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_residence (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    country TEXT NOT NULL,
                    since REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS economy_imv_suspensions (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    until REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS economy_ledger_time
                    ON economy_ledger (guild_id, created_at);

                CREATE TABLE IF NOT EXISTS economy_interest_days (
                    guild_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, day)
                );

                CREATE TABLE IF NOT EXISTS economy_interest (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    average INTEGER NOT NULL CHECK (average >= 0),
                    gross INTEGER NOT NULL CHECK (gross > 0),
                    tax INTEGER NOT NULL CHECK (tax >= 0),
                    created_at REAL NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, day)
                );

                CREATE TABLE IF NOT EXISTS economy_interest_streaks (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    capped INTEGER NOT NULL DEFAULT 0,
                    floor INTEGER NOT NULL DEFAULT 0,
                    resist INTEGER NOT NULL DEFAULT 0,
                    still INTEGER NOT NULL DEFAULT 0,
                    ant INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS economy_interest_weeks (
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_interest_savings (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    gross INTEGER NOT NULL,
                    withheld INTEGER NOT NULL,
                    quota INTEGER NOT NULL,
                    rate INTEGER NOT NULL,
                    charged INTEGER NOT NULL CHECK (charged >= 0),
                    created_at REAL NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_porra_bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    porra_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    outcome INTEGER NOT NULL CHECK (outcome >= 0),
                    stake INTEGER NOT NULL CHECK (stake > 0),
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_porra_bets_porra
                    ON economy_porra_bets (guild_id, porra_id);

                CREATE TABLE IF NOT EXISTS economy_porra_settled (
                    guild_id INTEGER NOT NULL,
                    porra_id INTEGER NOT NULL,
                    refund INTEGER NOT NULL,
                    settled_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, porra_id)
                );

                CREATE TABLE IF NOT EXISTS economy_gaming_tax (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    porra_id INTEGER NOT NULL,
                    tax INTEGER NOT NULL CHECK (tax >= 0),
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

    # -- Porras --------------------------------------------------------------------

    @staticmethod
    def _porra_settled_in(connection: sqlite3.Connection, guild_id: int, porra_id: int) -> bool:
        row = connection.execute(
            "SELECT 1 FROM economy_porra_settled WHERE guild_id = ? AND porra_id = ?",
            (guild_id, porra_id),
        ).fetchone()
        return row is not None

    @staticmethod
    def _porra_rows_in(
        connection: sqlite3.Connection, guild_id: int, porra_id: int
    ) -> list[tuple[int, int, int]]:
        return [
            (int(user_id), int(outcome), int(stake))
            for user_id, outcome, stake in connection.execute(
                """
                SELECT user_id, outcome, stake FROM economy_porra_bets
                WHERE guild_id = ? AND porra_id = ? ORDER BY id
                """,
                (guild_id, porra_id),
            )
        ]

    async def porra_bet(
        self,
        guild_id: int,
        porra_id: int,
        user_id: int,
        *,
        outcome: int,
        stake: int,
        cap: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
    ) -> PorraBetReceipt:
        """Cobra una apuesta a una porra y la deja en el depósito, todo atómico.

        Es una apuesta del casino que se resuelve después (como `place_bet`):
        resta en el día del jugador pero no ajusta su IRPF hasta que se liquide.

        Args:
            outcome: Opción a la que apuesta.
            cap: Tope del bote de la porra.

        Raises:
            PorraClosedError: Si la porra ya está liquidada.
            PorraSideError: Si ya apostó a otra opción de esta porra.
            PorraCapError: Si no cabe en el bote.
            InsufficientFundsError: Si no le llega.
        """
        return await self._run(
            self._porra_bet_sync,
            guild_id,
            porra_id,
            user_id,
            outcome,
            stake,
            cap,
            game,
            day,
            now,
            day_tax,
            window_seconds,
        )

    def _porra_bet_sync(
        self,
        guild_id: int,
        porra_id: int,
        user_id: int,
        outcome: int,
        stake: int,
        cap: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
    ) -> PorraBetReceipt:
        if stake <= 0 or outcome < 0:
            raise ValueError("Apuesta de porra inválida.")
        with self._transaction() as connection:
            if self._porra_settled_in(connection, guild_id, porra_id):
                raise PorraClosedError
            rows = self._porra_rows_in(connection, guild_id, porra_id)
            mine = [row for row in rows if row[0] == user_id]
            if mine and mine[0][1] != outcome:
                raise PorraSideError(mine[0][1])
            pool = sum(row[2] for row in rows)
            if pool + stake > cap:
                raise PorraCapError(max(0, cap - pool))
            bet = self._settle_gamble_in(
                connection,
                guild_id,
                user_id,
                (LedgerEntry(-stake, f"{game}:apuesta"),),
                day=day,
                now=now,
                adjust_tax=False,
                day_tax=day_tax,
                window_seconds=window_seconds,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
                VALUES (?, ?, 0)
                """,
                (guild_id, PORRA_ACCOUNT_ID),
            )
            self._apply_in_transaction(
                connection, guild_id, PORRA_ACCOUNT_ID, (LedgerEntry(stake, f"{game}:deposito"),)
            )
            connection.execute(
                """
                INSERT INTO economy_porra_bets
                    (guild_id, porra_id, user_id, outcome, stake, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, porra_id, user_id, outcome, stake, now),
            )
            return PorraBetReceipt(
                balance=bet.balance,
                stake=sum(row[2] for row in mine) + stake,
                pool=pool + stake,
            )

    async def porra_bets(self, guild_id: int, porra_id: int) -> list[tuple[int, int, int]]:
        """Apuestas de una porra como filas `(miembro, opción, apuesta)`, en orden."""
        return await self._run(self._porra_bets_sync, guild_id, porra_id)

    def _porra_bets_sync(self, guild_id: int, porra_id: int) -> list[tuple[int, int, int]]:
        connection = self._connect()
        try:
            return self._porra_rows_in(connection, guild_id, porra_id)
        finally:
            connection.close()

    async def porra_settled(self, guild_id: int, porra_id: int) -> bool:
        """Si la porra ya está liquidada."""
        return await self._run(self._porra_settled_sync, guild_id, porra_id)

    def _porra_settled_sync(self, guild_id: int, porra_id: int) -> bool:
        connection = self._connect()
        try:
            return self._porra_settled_in(connection, guild_id, porra_id)
        finally:
            connection.close()

    async def settle_porra(
        self,
        guild_id: int,
        porra_id: int,
        *,
        payouts: dict[int, int],
        taxes: dict[int, int],
        refund: bool,
        image: int,
        subject_id: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
        image_withhold: Callable[[int, int], int],
    ) -> PorraPayment:
        """Vacía el depósito de una porra: premios, IAJ, imagen o devoluciones.

        Todo en una transacción: o cobra todo el mundo o nadie. Comprueba que el
        reparto suma justo lo apostado y que cada apostante sale en `payouts`.

        Args:
            payouts: Lo que vuelve a cada apostante (0 a quien falla).
            taxes: IAJ de cada apostante; va al Estado.
            refund: Si es una devolución (anulada o sin aciertos). Entonces se
                deshace la apuesta (`<juego>:apuesta` en positivo) en vez de
                pagar un premio.
            image: Derechos de imagen brutos del protagonista.
            image_withhold: Retención de los derechos de imagen, con la firma de
                `_credit_income_in`.

        Raises:
            PorraClosedError: Si ya estaba liquidada. No se mueve nada.
            ValueError: Si el reparto no cuadra con lo apostado.
        """
        return await self._run(
            self._settle_porra_sync,
            guild_id,
            porra_id,
            dict(payouts),
            dict(taxes),
            refund,
            image,
            subject_id,
            game,
            day,
            now,
            day_tax,
            window_seconds,
            image_withhold,
        )

    def _settle_porra_sync(
        self,
        guild_id: int,
        porra_id: int,
        payouts: dict[int, int],
        taxes: dict[int, int],
        refund: bool,
        image: int,
        subject_id: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int, int], int],
        window_seconds: float,
        image_withhold: Callable[[int, int], int],
    ) -> PorraPayment:
        with self._transaction() as connection:
            if self._porra_settled_in(connection, guild_id, porra_id):
                raise PorraClosedError
            rows = self._porra_rows_in(connection, guild_id, porra_id)
            pool = sum(row[2] for row in rows)
            bettors = {row[0] for row in rows}
            if set(payouts) != bettors or not set(taxes) <= bettors:
                raise ValueError("El reparto no tiene a los mismos apostantes que el libro.")
            if min([*payouts.values(), *taxes.values(), image, 0]) < 0:
                raise ValueError("El reparto tiene cantidades negativas.")
            if sum(payouts.values()) + sum(taxes.values()) + image != pool:
                raise ValueError("El reparto no suma lo apostado.")
            if pool:
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    PORRA_ACCOUNT_ID,
                    (LedgerEntry(-pool, f"{game}:deposito"),),
                )
            results: dict[int, BetSettlement] = {}
            for user_id in sorted(bettors):
                amount = payouts[user_id]
                reason = f"{game}:apuesta" if refund else f"{game}:premio"
                entries = (LedgerEntry(amount, reason),) if amount else ()
                results[user_id] = self._settle_gamble_in(
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
            tax_total = sum(taxes.values())
            if tax_total:
                self._credit_state_in(connection, guild_id, tax_total, f"iaj:{game}")
                connection.executemany(
                    """
                    INSERT INTO economy_gaming_tax (guild_id, user_id, porra_id, tax, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (guild_id, user_id, porra_id, tax, now)
                        for user_id, tax in sorted(taxes.items())
                        if tax
                    ],
                )
            image_tax, subject_balance = 0, None
            if image:
                image_tax, subject_balance = self._credit_income_in(
                    connection,
                    guild_id,
                    subject_id,
                    gross=image,
                    concept=f"{game}:imagen",
                    now=now,
                    withhold=image_withhold,
                    window_seconds=window_seconds,
                )
            connection.execute(
                """
                INSERT INTO economy_porra_settled (guild_id, porra_id, refund, settled_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, porra_id, int(refund), now),
            )
            return PorraPayment(bets=results, image_tax=image_tax, subject_balance=subject_balance)

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
                7 días previos al cierre)` y devuelve lo que sale a devolver
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

    async def casino_ledger(
        self, guild_id: int, user_id: int | None
    ) -> tuple[list[tuple[str, int, int]], int, int, int, float | None]:
        """Lo que dice el libro del casino desde el primer día (para `apuestas`).

        Solo lee. Mira los movimientos de los miembros (no los del Estado ni los
        de los botes) cuyo motivo acaba en `:apuesta`, `:premio` o `:bote`, y
        los del IRPF del juego y la renta.

        Args:
            user_id: Un miembro, o `None` para todo el servidor.

        Returns:
            `(filas, retenido, devuelto en el día, devuelto en la renta, primer
            movimiento)`, donde cada fila es `(motivo, movimientos, suma)`.
        """
        return await self._run(self._casino_ledger_sync, guild_id, user_id)

    def _casino_ledger_sync(
        self, guild_id: int, user_id: int | None
    ) -> tuple[list[tuple[str, int, int]], int, int, int, float | None]:
        # Los miembros tienen ids de Discord (positivos); el Estado es 0 y las
        # cuentas de la casa son negativas.
        where = "guild_id = ? AND user_id > 0"
        args: tuple[object, ...] = (guild_id,)
        if user_id is not None:
            where += " AND user_id = ?"
            args = (guild_id, user_id)
        connection = self._connect()
        try:
            rows = [
                (str(reason), int(count), int(total or 0))
                for reason, count, total in connection.execute(
                    f"""
                    SELECT reason, COUNT(*), SUM(delta) FROM economy_ledger
                    WHERE {where} AND (reason LIKE '%:apuesta' OR reason LIKE '%:premio'
                        OR reason LIKE '%:bote')
                    GROUP BY reason
                    """,
                    args,
                )
            ]
            sums = dict(
                connection.execute(
                    f"""
                    SELECT reason, SUM(delta) FROM economy_ledger
                    WHERE {where} AND reason IN
                        ('irpf:juego', 'devolucion:irpf:juego', 'devolucion:renta')
                    GROUP BY reason
                    """,
                    args,
                ).fetchall()
            )
            (first_at,) = connection.execute(
                f"SELECT MIN(created_at) FROM economy_ledger WHERE {where} "
                "AND reason LIKE '%:apuesta'",
                args,
            ).fetchone()
        finally:
            connection.close()
        return (
            rows,
            -int(sums.get("irpf:juego") or 0),
            int(sums.get("devolucion:irpf:juego") or 0),
            int(sums.get("devolucion:renta") or 0),
            float(first_at) if first_at is not None else None,
        )

    async def member_balances(self, guild_id: int) -> dict[int, int]:
        """Saldo de cada miembro con monedero (sin el Estado ni las cuentas de la casa)."""
        return await self._run(self._member_balances_sync, guild_id)

    def _member_balances_sync(self, guild_id: int) -> dict[int, int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT user_id, balance FROM economy_wallets WHERE guild_id = ? AND user_id > 0",
                (guild_id,),
            ).fetchall()
        finally:
            connection.close()
        return {int(user_id): int(balance) for user_id, balance in rows}

    async def tax_breakdown(
        self, guild_id: int, *, since: float | None = None
    ) -> dict[int, dict[str, int]]:
        """Todo lo que cada miembro ha pagado al Estado, impuesto a impuesto.

        Solo lee. Las claves son las de `bot.services.tax_report.TAX_KINDS`:
        IRPF del trabajo y de otros ingresos, del casino y del ahorro,
        Seguridad Social del trabajador (con la cuota de autónomos) y de la
        empresa, IGIC (las devoluciones restan), Patrimonio, gravamen de
        loterías, IAJ de las porras, multas, lo devuelto en la renta (positivo;
        resta) y lo pagado en Hong Kong (que no va al Estado).

        Args:
            since: Epoch desde el que contar, o `None` para todo.
        """
        return await self._run(self._tax_breakdown_sync, guild_id, since)

    def _tax_breakdown_sync(self, guild_id: int, since: float | None) -> dict[int, dict[str, int]]:
        # Una sola vista con (miembro, tipo, importe, momento). El IRPF de las
        # nóminas está en `economy_tax_records` y en `economy_payroll`: se
        # cuenta por la nómina y se resta del resto de rentas para no duplicarlo.
        parts = """
            SELECT user_id, 'irpf_trabajo' AS kind, irpf AS amount, created_at AS at
            FROM economy_payroll WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_otros', -irpf, created_at
            FROM economy_payroll WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_otros', withheld, created_at
            FROM economy_tax_records WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_casino', withheld, updated_at
            FROM economy_gambling_days WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_ahorro', tax, created_at
            FROM economy_interest WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_ahorro', charged, created_at
            FROM economy_interest_savings WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'ss_trabajador', ss_worker, created_at
            FROM economy_payroll WHERE guild_id = :guild AND country = 'es'
            UNION ALL
            SELECT user_id, 'ss_empresa', ss_employer, created_at
            FROM economy_payroll WHERE guild_id = :guild AND country = 'es'
            UNION ALL
            SELECT user_id, 'extranjero', foreign_tax + ss_worker + ss_employer, created_at
            FROM economy_payroll WHERE guild_id = :guild AND country != 'es'
            UNION ALL
            SELECT user_id, 'igic', tax, created_at
            FROM economy_consumption_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'patrimonio', tax, created_at
            FROM economy_wealth_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'loteria', tax, created_at
            FROM economy_lottery_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'iaj', tax, created_at
            FROM economy_gaming_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'multas', amount, created_at
            FROM economy_sanctions WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'devuelto', delta, created_at
            FROM economy_ledger WHERE guild_id = :guild AND reason = 'devolucion:renta'
        """
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT user_id, kind, SUM(amount) FROM ({parts})
                WHERE user_id > 0 AND (:since IS NULL OR at >= :since)
                GROUP BY user_id, kind
                """,
                {"guild": guild_id, "since": since},
            ).fetchall()
        finally:
            connection.close()
        out: dict[int, dict[str, int]] = {}
        for user_id, kind, total in rows:
            if total:
                out.setdefault(int(user_id), {})[str(kind)] = int(total)
        return out

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
            # Retenciones de ingresos y del casino, Patrimonio, IGIC, gravamen de
            # loterías, Seguridad Social de las nóminas (la de la empresa se apunta a
            # quien cobra), multas e IRPF de los intereses, en una sola vista.
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
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_lottery_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_gaming_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, ss_worker + ss_employer AS withheld, created_at AS at
                FROM economy_payroll WHERE guild_id = :guild AND country = 'es'
                UNION ALL
                SELECT user_id, amount AS withheld, created_at AS at FROM economy_sanctions
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_interest
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, charged AS withheld, created_at AS at
                FROM economy_interest_savings WHERE guild_id = :guild
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
            (debt,) = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM economy_public_debt WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            return Treasury(
                balance=int(row["balance"]) if row else 0,
                collected_total=int(total),
                collected_since=int(recent),
                top_contributors=tuple((int(r["user_id"]), int(r["paid"])) for r in contributors),
                debt=int(debt),
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

    # -- Intereses de la cuenta ---------------------------------------------------------

    @staticmethod
    def _day_rows_in(
        connection: sqlite3.Connection,
        guild_id: int,
        start: float,
        end: float,
        user_id: int | None = None,
    ) -> tuple[dict[int, int], dict[int, list[LedgerRow]]]:
        """Saldo al empezar el día y movimientos del día, por miembro.

        Todo sale del libro, no de fotos guardadas: no hace falta una tarea a
        medianoche y, si el bot estuvo caído, el saldo medio sigue siendo exacto.
        El saldo de apertura es el saldo de ahora menos lo que se ha movido desde
        `start`; un monedero que aún no existía empieza en 0.

        Args:
            user_id: Solo ese miembro; `None` para todos los del servidor.
        """
        from bot.services.interest import LedgerRow

        member = "AND user_id = :user" if user_id is not None else "AND user_id > 0"
        params = {"guild": guild_id, "start": start, "end": end, "user": user_id}
        balances = {
            int(row["user_id"]): int(row["balance"])
            for row in connection.execute(
                f"SELECT user_id, balance FROM economy_wallets WHERE guild_id = :guild {member}",
                params,
            )
        }
        moved = {
            int(row["user_id"]): int(row["moved"])
            for row in connection.execute(
                f"""
                SELECT user_id, SUM(delta) AS moved FROM economy_ledger
                WHERE guild_id = :guild AND created_at >= :start {member}
                GROUP BY user_id
                """,
                params,
            )
        }
        openings = {uid: balance - moved.get(uid, 0) for uid, balance in balances.items()}
        rows: dict[int, list[LedgerRow]] = {}
        for row in connection.execute(
            f"""
            SELECT user_id, created_at, delta, balance_after, reason FROM economy_ledger
            WHERE guild_id = :guild AND created_at >= :start AND created_at < :end {member}
            ORDER BY id
            """,
            params,
        ):
            rows.setdefault(int(row["user_id"]), []).append(
                LedgerRow(
                    float(row["created_at"]),
                    int(row["delta"]),
                    int(row["balance_after"]),
                    str(row["reason"]),
                )
            )
        return openings, rows

    async def interest_day_rows(
        self, guild_id: int, user_id: int, start: float
    ) -> tuple[int, list[LedgerRow]]:
        """Saldo de un miembro al empezar el día `start` y sus movimientos desde entonces."""
        return await self._run(self._interest_day_rows_sync, guild_id, user_id, start)

    def _interest_day_rows_sync(
        self, guild_id: int, user_id: int, start: float
    ) -> tuple[int, list[LedgerRow]]:
        connection = self._connect()
        try:
            openings, rows = self._day_rows_in(
                connection, guild_id, start, float("inf"), user_id=user_id
            )
            return openings.get(user_id, 0), rows.get(user_id, [])
        finally:
            connection.close()

    async def last_interest_day(self, guild_id: int) -> str | None:
        """Último día (ISO) cuyos intereses ya se pagaron en el servidor."""
        return await self._run(self._last_interest_day_sync, guild_id)

    def _last_interest_day_sync(self, guild_id: int) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT MAX(day) AS day FROM economy_interest_days WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            return str(row["day"]) if row and row["day"] else None
        finally:
            connection.close()

    async def pay_interest_day(
        self,
        guild_id: int,
        *,
        day: str,
        start: float,
        end: float,
        now: float,
        settle: Callable[[int, int, list[LedgerRow], Streaks], DayOutcome],
    ) -> InterestRun:
        """Paga los intereses de `day` a todos los miembros del servidor, una vez.

        Todo va en una transacción: o cobran todos o nadie, y el día queda
        marcado. Cada cobro deja en el libro el bruto (`intereses`) y la
        retención (`irpf:intereses`), que entra en la cuenta del Estado. Las
        cuentas especiales (Estado, ONGs, bote, tienda, con `user_id` <= 0) no
        cobran.

        Args:
            day: Día ISO que se paga.
            start: Su medianoche inicial (epoch).
            end: Su medianoche final.
            settle: Recibe `(miembro, saldo inicial, movimientos, rachas)` y
                devuelve el resultado (regla en `bot.services.interest`).
        """
        return await self._run(self._pay_interest_day_sync, guild_id, day, start, end, now, settle)

    def _pay_interest_day_sync(
        self,
        guild_id: int,
        day: str,
        start: float,
        end: float,
        now: float,
        settle: Callable[[int, int, list[LedgerRow], Streaks], DayOutcome],
    ) -> InterestRun:
        from bot.services.interest import Streaks

        with self._transaction() as connection:
            done = connection.execute(
                "SELECT 1 FROM economy_interest_days WHERE guild_id = ? AND day = ?",
                (guild_id, day),
            ).fetchone()
            if done is not None:
                return InterestRun(done_before=True)
            connection.execute(
                "INSERT INTO economy_interest_days (guild_id, day, processed_at) VALUES (?, ?, ?)",
                (guild_id, day, now),
            )
            openings, rows = self._day_rows_in(connection, guild_id, start, end)
            streaks = {
                int(row["user_id"]): Streaks(
                    int(row["capped"]),
                    int(row["floor"]),
                    int(row["resist"]),
                    int(row["still"]),
                    int(row["ant"]),
                )
                for row in connection.execute(
                    "SELECT * FROM economy_interest_streaks WHERE guild_id = ?", (guild_id,)
                )
            }
            outcomes = []
            for user_id in sorted(openings):
                outcome = settle(
                    user_id,
                    openings[user_id],
                    rows.get(user_id, []),
                    streaks.get(user_id, Streaks()),
                )
                payment = outcome.payment
                if payment.gross > 0:
                    entries = [LedgerEntry(payment.gross, "intereses")]
                    if payment.tax:
                        entries.append(LedgerEntry(-payment.tax, "irpf:intereses"))
                    self._apply_in_transaction(connection, guild_id, user_id, entries)
                    if payment.tax:
                        self._credit_state_in(connection, guild_id, payment.tax, "irpf:intereses")
                    connection.execute(
                        """
                        INSERT INTO economy_interest
                            (guild_id, user_id, day, average, gross, tax, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, user_id, day, payment.average, payment.gross, payment.tax, now),
                    )
                new = outcome.streaks
                connection.execute(
                    """
                    INSERT INTO economy_interest_streaks
                        (guild_id, user_id, capped, floor, resist, still, ant)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id) DO UPDATE SET
                        capped = excluded.capped, floor = excluded.floor,
                        resist = excluded.resist, still = excluded.still, ant = excluded.ant
                    """,
                    (guild_id, user_id, new.capped, new.floor, new.resist, new.still, new.ant),
                )
                outcomes.append(outcome)
            return InterestRun(outcomes=tuple(outcomes))

    async def settle_savings(
        self,
        guild_id: int,
        *,
        week: str,
        first_day: str,
        last_day: str,
        now: float,
        settle: Callable[[int, int, int, int], SavingsSettlement],
    ) -> SavingsRun:
        """Liquida con la escala del ahorro los intereses de una semana, una vez.

        Suma lo cobrado de `first_day` a `last_day` (ISO, ambos incluidos) por
        cada miembro y cobra la diferencia entre la cuota y lo ya retenido
        (`irpf:ahorro`), que va al Estado. Todo en una transacción.

        Args:
            week: Lunes ISO de la semana.
            settle: Recibe `(miembro, bruto, retenido, saldo)` y devuelve la
                liquidación (regla en `bot.services.interest.savings_settlement`).
        """
        return await self._run(
            self._settle_savings_sync, guild_id, week, first_day, last_day, now, settle
        )

    def _settle_savings_sync(
        self,
        guild_id: int,
        week: str,
        first_day: str,
        last_day: str,
        now: float,
        settle: Callable[[int, int, int, int], SavingsSettlement],
    ) -> SavingsRun:
        with self._transaction() as connection:
            done = connection.execute(
                "SELECT 1 FROM economy_interest_weeks WHERE guild_id = ? AND week_start = ?",
                (guild_id, week),
            ).fetchone()
            if done is not None:
                return SavingsRun(done_before=True)
            connection.execute(
                "INSERT INTO economy_interest_weeks (guild_id, week_start, processed_at) "
                "VALUES (?, ?, ?)",
                (guild_id, week, now),
            )
            totals = connection.execute(
                """
                SELECT i.user_id, SUM(i.gross) AS gross, SUM(i.tax) AS tax, w.balance
                FROM economy_interest i
                JOIN economy_wallets w ON w.guild_id = i.guild_id AND w.user_id = i.user_id
                WHERE i.guild_id = ? AND i.day BETWEEN ? AND ?
                GROUP BY i.user_id
                """,
                (guild_id, first_day, last_day),
            ).fetchall()
            settlements = []
            for row in totals:
                user_id = int(row["user_id"])
                result = settle(user_id, int(row["gross"]), int(row["tax"]), int(row["balance"]))
                if result.charged:
                    self._apply_in_transaction(
                        connection,
                        guild_id,
                        user_id,
                        (LedgerEntry(-result.charged, "irpf:ahorro"),),
                    )
                    self._credit_state_in(connection, guild_id, result.charged, "irpf:ahorro")
                connection.execute(
                    """
                    INSERT INTO economy_interest_savings
                        (guild_id, user_id, week_start, gross, withheld, quota, rate, charged,
                         created_at, notified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        user_id,
                        week,
                        result.gross,
                        result.withheld,
                        result.quota,
                        round(result.rate * 100),
                        result.charged,
                        now,
                        # Sin nada que cobrar no hay nada que contar.
                        0 if result.charged else 1,
                    ),
                )
                settlements.append(result)
            return SavingsRun(settlements=tuple(settlements))

    async def last_interest(self, guild_id: int, user_id: int) -> tuple[str, int, int] | None:
        """Último cobro de intereses de un miembro: `(día ISO, bruto, retención)`."""
        return await self._run(self._last_interest_sync, guild_id, user_id)

    def _last_interest_sync(self, guild_id: int, user_id: int) -> tuple[str, int, int] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT day, gross, tax FROM economy_interest
                WHERE guild_id = ? AND user_id = ? ORDER BY day DESC LIMIT 1
                """,
                (guild_id, user_id),
            ).fetchone()
            return (str(row["day"]), int(row["gross"]), int(row["tax"])) if row else None
        finally:
            connection.close()

    async def take_interest_notice(self, guild_id: int, user_id: int) -> InterestNotice:
        """Lo que el miembro aún no ha visto de la cuenta, y lo marca como visto."""
        return await self._run(self._take_interest_notice_sync, guild_id, user_id)

    def _take_interest_notice_sync(self, guild_id: int, user_id: int) -> InterestNotice:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS days, COALESCE(SUM(gross), 0) AS gross,
                       COALESCE(SUM(tax), 0) AS tax, MIN(day) AS first_day
                FROM economy_interest WHERE guild_id = ? AND user_id = ? AND notified = 0
                """,
                (guild_id, user_id),
            ).fetchone()
            savings = connection.execute(
                """
                SELECT week_start, charged, rate FROM economy_interest_savings
                WHERE guild_id = ? AND user_id = ? AND notified = 0 ORDER BY week_start
                """,
                (guild_id, user_id),
            ).fetchall()
            for table in ("economy_interest", "economy_interest_savings"):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(
                    f"UPDATE {table} SET notified = 1 "
                    "WHERE guild_id = ? AND user_id = ? AND notified = 0",
                    (guild_id, user_id),
                )
            return InterestNotice(
                days=int(row["days"]),
                gross=int(row["gross"]),
                tax=int(row["tax"]),
                first_day=row["first_day"],
                savings=tuple(
                    (str(s["week_start"]), int(s["charged"]), int(s["rate"])) for s in savings
                ),
            )

    async def last_savings(
        self, guild_id: int, user_id: int
    ) -> tuple[str, int, int, int, int, int] | None:
        """Última liquidación del ahorro: `(lunes, bruto, retenido, cuota, tipo %, cobrado)`."""
        return await self._run(self._last_savings_sync, guild_id, user_id)

    def _last_savings_sync(
        self, guild_id: int, user_id: int
    ) -> tuple[str, int, int, int, int, int] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT week_start, gross, withheld, quota, rate, charged
                FROM economy_interest_savings
                WHERE guild_id = ? AND user_id = ? ORDER BY week_start DESC LIMIT 1
                """,
                (guild_id, user_id),
            ).fetchone()
            if row is None:
                return None
            return (
                str(row["week_start"]),
                int(row["gross"]),
                int(row["withheld"]),
                int(row["quota"]),
                int(row["rate"]),
                int(row["charged"]),
            )
        finally:
            connection.close()

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

    # -- Bizum entre miembros ----------------------------------------------------------

    async def bizum(
        self,
        guild_id: int,
        sender_id: int,
        receiver_id: int,
        *,
        amount: int,
        now: float,
        day_start: float,
    ) -> BizumReceipt:
        """Pasa `amount` de un miembro a otro y apunta el Bizum.

        Sin impuestos: el dinero sale entero de uno y llega entero al otro
        (ver `bot.services.bizum`). Los dos monederos se tocan en la misma
        transacción, así que no hay un momento en que el dinero no esté en
        ninguno de los dos.

        Args:
            day_start: Inicio (epoch) del día local, para sumar lo enviado hoy.

        Raises:
            InsufficientFundsError: Si a quien envía no le llega. No se mueve nada.
            BalanceLimitError: Si quien recibe superaría el saldo máximo.
        """
        return await self._run(
            self._bizum_sync, guild_id, sender_id, receiver_id, amount, now, day_start
        )

    def _bizum_sync(
        self,
        guild_id: int,
        sender_id: int,
        receiver_id: int,
        amount: int,
        now: float,
        day_start: float,
    ) -> BizumReceipt:
        if amount <= 0:
            raise ValueError("Un Bizum tiene que ser positivo.")
        # Solo entre miembros: el Estado, las ONGs, el bote y la caja de la tienda
        # tienen id 0 o negativo y no pueden mandar ni recibir Bizums.
        if sender_id <= 0 or receiver_id <= 0 or sender_id == receiver_id:
            raise ValueError("Un Bizum va de un miembro a otro distinto.")
        with self._transaction() as connection:
            sender_balance = self._apply_in_transaction(
                connection, guild_id, sender_id, (LedgerEntry(-amount, "bizum:enviado"),)
            )
            receiver_balance = self._apply_in_transaction(
                connection, guild_id, receiver_id, (LedgerEntry(amount, "bizum:recibido"),)
            )
            connection.execute(
                """
                INSERT INTO economy_bizums (guild_id, sender_id, receiver_id, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, sender_id, receiver_id, amount, now),
            )
            (sent_today,) = connection.execute(
                """
                SELECT COALESCE(SUM(amount), 0) FROM economy_bizums
                WHERE guild_id = ? AND sender_id = ? AND created_at >= ?
                """,
                (guild_id, sender_id, day_start),
            ).fetchone()
            return BizumReceipt(sender_balance, receiver_balance, int(sent_today))

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

    # -- Loterías ----------------------------------------------------------------------

    async def state_balance(self, guild_id: int) -> int:
        """Saldo de la cuenta del Estado (0 si aún no existe)."""
        return await self._run(self._state_balance_sync, guild_id)

    def _state_balance_sync(self, guild_id: int) -> int:
        connection = self._connect()
        try:
            return self._state_balance_in(connection, guild_id)
        finally:
            connection.close()

    async def lottery(
        self,
        guild_id: int,
        *,
        charges: Sequence[tuple[int, int, str]],
        payouts: Sequence[LotteryPayout],
        now: float,
        hook: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, dict[int, int]]:
        """Cobra boletos y paga premios de lotería con la cuenta del Estado de banca.

        Todo va en una transacción: `hook` (lo pone el repositorio de
        loterías: apunta boletos o cierra un sorteo) corre primero y, si lanza,
        no se mueve nada.

        Args:
            charges: `(user_id, importe, motivo)` de cada compra. El dinero va
                entero al Estado.
            payouts: Premios. El Estado paga el bruto y se queda el gravamen.
                Si no le llega el saldo, emite deuda pública por lo que falta.

        Returns:
            `(lo que devuelva hook, saldo final de cada miembro tocado)`.

        Raises:
            InsufficientFundsError: Si a alguien no le llega para su compra.
        """
        return await self._run(
            self._lottery_sync, guild_id, tuple(charges), tuple(payouts), now, hook
        )

    def _lottery_sync(
        self,
        guild_id: int,
        charges: tuple[tuple[int, int, str], ...],
        payouts: tuple[LotteryPayout, ...],
        now: float,
        hook: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, dict[int, int]]:
        if any(cost <= 0 for _, cost, _ in charges):
            raise ValueError("Un boleto cuesta algo.")
        if any(p.gross <= 0 or not 0 <= p.tax <= p.gross for p in payouts):
            raise ValueError("Premio o gravamen fuera de rango.")
        balances: dict[int, int] = {}
        with self._transaction() as connection:
            result = hook(connection)
            for user_id, cost, reason in charges:
                balances[user_id] = self._apply_in_transaction(
                    connection, guild_id, user_id, (LedgerEntry(-cost, reason),)
                )
                self._credit_state_in(connection, guild_id, cost, "loteria:venta")
            for payout in payouts:
                self._open_empty_wallet(connection, guild_id, STATE_ACCOUNT_ID)
                shortfall = payout.gross - self._state_balance_in(connection, guild_id)
                if shortfall > 0:
                    self._credit_state_in(connection, guild_id, shortfall, "deuda:emision")
                    connection.execute(
                        "INSERT INTO economy_public_debt (guild_id, amount, concept, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (guild_id, shortfall, payout.concept, now),
                    )
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    STATE_ACCOUNT_ID,
                    (LedgerEntry(-payout.gross, f"loteria:premio:{payout.concept}"),),
                )
                entries = [LedgerEntry(payout.gross, f"loteria:{payout.concept}")]
                if payout.tax:
                    entries.append(LedgerEntry(-payout.tax, "gravamen:loteria"))
                balances[payout.user_id] = self._apply_in_transaction(
                    connection, guild_id, payout.user_id, entries
                )
                if payout.tax:
                    self._credit_state_in(connection, guild_id, payout.tax, "gravamen:loteria")
                    connection.execute(
                        """
                        INSERT INTO economy_lottery_tax
                            (guild_id, user_id, concept, gross, tax, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, payout.user_id, payout.concept, payout.gross, payout.tax, now),
                    )
            return result, balances

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

    # -- Nóminas, dinero en negro y sanciones ----------------------------------------

    async def credit_salary(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        now: float,
        payslip_for: Callable[[int, int], Payslip],
        window_seconds: float,
    ) -> tuple[Payslip, int]:
        """Paga una nómina: bruto, cotización, IRPF y lo de la empresa, todo junto.

        En el libro del trabajador quedan tres movimientos: el bruto
        (`concept`), la Seguridad Social (`ss:concept`) y la retención
        (`irpf:concept`). Al Estado le entran la retención, la cotización del
        trabajador y la de la empresa; esta última es dinero nuevo, porque la
        empresa no existe. El desglose queda en `economy_payroll` y el bruto, en
        la escala general (`Payslip.fiscal_gross`), en `economy_tax_records`,
        que es la renta que ven el casino y la declaración.

        Args:
            payslip_for: Recibe `(bruto, renta sujeta de la ventana)` y
                devuelve la nómina. La renta es toda la del miembro (nóminas,
                premios, casino en positivo), la misma que ven los premios y el
                casino (`_recent_taxable_in`), en la escala general; pasarla a
                la de nóminas es cosa de quien la recibe. La regla vive en
                `bot.services.taxes`.
            window_seconds: Ventana de la renta que se pasa a `payslip_for`.

        Returns:
            `(nómina, saldo_final)`.
        """
        return await self._run(
            self._credit_salary_sync,
            guild_id,
            user_id,
            gross,
            concept,
            now,
            payslip_for,
            window_seconds,
        )

    def _credit_salary_sync(
        self,
        guild_id: int,
        user_id: int,
        gross: int,
        concept: str,
        now: float,
        payslip_for: Callable[[int, int], Payslip],
        window_seconds: float,
    ) -> tuple[Payslip, int]:
        with self._transaction() as connection:
            recent = self._recent_taxable_in(connection, guild_id, user_id, now - window_seconds)
            slip = payslip_for(gross, recent)
            entries = [LedgerEntry(slip.gross, concept)]
            if slip.ss_worker:
                entries.append(LedgerEntry(-slip.ss_worker, f"ss:{concept}"))
            if slip.irpf:
                entries.append(LedgerEntry(-slip.irpf, f"irpf:{concept}"))
            balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
            if slip.irpf:
                self._credit_state_in(connection, guild_id, slip.irpf, f"irpf:{concept}")
            if slip.ss_worker:
                self._credit_state_in(connection, guild_id, slip.ss_worker, f"ss:{concept}")
            if slip.ss_employer:
                self._credit_state_in(
                    connection, guild_id, slip.ss_employer, f"ss_empresa:{concept}"
                )
            connection.execute(
                """
                INSERT INTO economy_tax_records
                    (guild_id, user_id, created_at, concept, gross, withheld)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, now, concept, slip.fiscal_gross, slip.irpf),
            )
            connection.execute(
                """
                INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker,
                                             ss_employer, irpf, net, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    concept,
                    slip.gross,
                    slip.ss_worker,
                    slip.ss_employer,
                    slip.irpf,
                    slip.net,
                    now,
                ),
            )
            return slip, balance

    async def charge_contribution(
        self, guild_id: int, user_id: int, *, amount: int, concept: str, now: float
    ) -> tuple[int, int]:
        """Cobra una cotización sin nómina (la cuota de autónomos) y la manda al Estado.

        Queda en `economy_payroll` como una fila sin bruto y con neto negativo:
        así cuenta como Seguridad Social en `hacienda` y resta del neto semanal
        que mira el IMV. Si no llega el saldo, se cobra lo que haya.

        Returns:
            `(cobrado, saldo_final)`.
        """
        return await self._run(
            self._charge_contribution_sync, guild_id, user_id, amount, concept, now
        )

    def _charge_contribution_sync(
        self, guild_id: int, user_id: int, amount: int, concept: str, now: float
    ) -> tuple[int, int]:
        with self._transaction() as connection:
            balance = self._ensure_wallet(connection, guild_id, user_id)
            charged = min(balance, amount)
            if charged <= 0:
                return 0, balance
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-charged, f"ss:{concept}"),)
            )
            self._credit_state_in(connection, guild_id, charged, f"ss:{concept}")
            connection.execute(
                """
                INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker,
                                             ss_employer, irpf, net, created_at)
                VALUES (?, ?, ?, 0, ?, 0, 0, ?, ?)
                """,
                (guild_id, user_id, concept, charged, -charged, now),
            )
            return charged, balance

    async def work_net(self, guild_id: int, user_id: int, since: float) -> int:
        """Neto cobrado en nóminas desde `since` (lo cobrado en negro no cuenta)."""
        return await self._run(self._work_net_sync, guild_id, user_id, since)

    def _work_net_sync(self, guild_id: int, user_id: int, since: float) -> int:
        connection = self._connect()
        try:
            (net,) = connection.execute(
                """
                SELECT COALESCE(SUM(net), 0) FROM economy_payroll
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND country = 'es'
                """,
                (guild_id, user_id, since),
            ).fetchone()
            return int(net)
        finally:
            connection.close()

    async def payroll_totals(
        self, guild_id: int, user_id: int, since: float
    ) -> tuple[int, int, int, int]:
        """`(bruto, Seguridad Social total, IRPF, neto)` de las nóminas desde `since`."""
        return await self._run(self._payroll_totals_sync, guild_id, user_id, since)

    def _payroll_totals_sync(
        self, guild_id: int, user_id: int, since: float
    ) -> tuple[int, int, int, int]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(gross), 0), COALESCE(SUM(ss_worker + ss_employer), 0),
                       COALESCE(SUM(irpf), 0), COALESCE(SUM(net), 0)
                FROM economy_payroll
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND country = 'es'
                """,
                (guild_id, user_id, since),
            ).fetchone()
            return int(row[0]), int(row[1]), int(row[2]), int(row[3])
        finally:
            connection.close()

    async def other_taxes(self, guild_id: int, user_id: int, since: float) -> int:
        """IGIC, Patrimonio e IRPF de los intereses desde `since` (logro «Socio de Hacienda»)."""
        return await self._run(self._other_taxes_sync, guild_id, user_id, since)

    def _other_taxes_sync(self, guild_id: int, user_id: int, since: float) -> int:
        connection = self._connect()
        try:
            (igic,) = connection.execute(
                """
                SELECT COALESCE(SUM(tax), 0) FROM economy_consumption_tax
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            (wealth,) = connection.execute(
                """
                SELECT COALESCE(SUM(tax), 0) FROM economy_wealth_tax
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            (interest,) = connection.execute(
                """
                SELECT COALESCE(SUM(tax), 0) FROM economy_interest
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            (savings,) = connection.execute(
                """
                SELECT COALESCE(SUM(charged), 0) FROM economy_interest_savings
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            return int(igic) + int(wealth) + int(interest) + int(savings)
        finally:
            connection.close()

    async def sanction(
        self, guild_id: int, user_id: int, *, amount: int, concept: str, now: float
    ) -> tuple[int, int]:
        """Cobra una multa al Estado; si no llega el saldo, se queda con lo que haya.

        Returns:
            `(cobrado, saldo_final)`.
        """
        return await self._run(self._sanction_sync, guild_id, user_id, amount, concept, now)

    def _sanction_sync(
        self, guild_id: int, user_id: int, amount: int, concept: str, now: float
    ) -> tuple[int, int]:
        with self._transaction() as connection:
            balance = self._ensure_wallet(connection, guild_id, user_id)
            charged = min(balance, amount)
            if charged <= 0:
                return 0, balance
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-charged, f"multa:{concept}"),)
            )
            self._credit_state_in(connection, guild_id, charged, f"multa:{concept}")
            connection.execute(
                """
                INSERT INTO economy_sanctions (guild_id, user_id, concept, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, concept, charged, now),
            )
            return charged, balance

    async def suspend_imv(self, guild_id: int, user_id: int, until: float) -> None:
        """Deja sin IMV a un miembro hasta `until` (alarga, nunca acorta)."""
        await self._run(self._suspend_imv_sync, guild_id, user_id, until)

    def _suspend_imv_sync(self, guild_id: int, user_id: int, until: float) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO economy_imv_suspensions (guild_id, user_id, until) VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET until = MAX(until, excluded.until)
                """,
                (guild_id, user_id, until),
            )

    async def imv_suspended_until(self, guild_id: int, user_id: int) -> float:
        """Hasta cuándo está suspendido el IMV (0 si no lo está)."""
        return await self._run(self._imv_suspended_sync, guild_id, user_id)

    def _imv_suspended_sync(self, guild_id: int, user_id: int) -> float:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT until FROM economy_imv_suspensions WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return float(row["until"]) if row else 0.0
        finally:
            connection.close()

    # -- Trabajar fuera ------------------------------------------------------------------

    async def credit_foreign_salary(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        country: str,
        account_id: int,
        now: float,
        payslip_for: Callable[[int, int, int], ForeignPayslip],
        window_seconds: float,
    ) -> tuple[ForeignPayslip, int]:
        """Paga una nómina cobrada fuera de España.

        En el libro: el bruto, la cotización del otro país (`mpf:concept`), su
        impuesto (`impuesto_ext:concept`) y, si hay, el IRPF español. Lo del otro
        país (las dos cotizaciones y el impuesto) va a `account_id`; el IRPF, al
        Estado. La parte sujeta en España queda en `economy_tax_records`.

        Args:
            payslip_for: Recibe `(bruto, bruto de fuera de la ventana, renta
                sujeta en España de la ventana en la escala general)` y devuelve
                la nómina.

        Returns:
            `(nómina, saldo_final)`.
        """
        return await self._run(
            self._credit_foreign_sync,
            guild_id,
            user_id,
            gross,
            concept,
            country,
            account_id,
            now,
            payslip_for,
            window_seconds,
        )

    def _credit_foreign_sync(
        self,
        guild_id: int,
        user_id: int,
        gross: int,
        concept: str,
        country: str,
        account_id: int,
        now: float,
        payslip_for: Callable[[int, int, int], ForeignPayslip],
        window_seconds: float,
    ) -> tuple[ForeignPayslip, int]:
        with self._transaction() as connection:
            since = now - window_seconds
            (recent_foreign,) = connection.execute(
                """
                SELECT COALESCE(SUM(gross), 0) FROM economy_payroll
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND country = ?
                """,
                (guild_id, user_id, since, country),
            ).fetchone()
            recent_es = self._recent_taxable_in(connection, guild_id, user_id, since)
            slip = payslip_for(gross, int(recent_foreign), recent_es)
            entries = [LedgerEntry(slip.gross, concept)]
            if slip.mpf_worker:
                entries.append(LedgerEntry(-slip.mpf_worker, f"mpf:{concept}"))
            if slip.hk_tax:
                entries.append(LedgerEntry(-slip.hk_tax, f"impuesto_ext:{concept}"))
            if slip.irpf:
                entries.append(LedgerEntry(-slip.irpf, f"irpf:{concept}"))
            balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
            if slip.foreign:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
                    VALUES (?, ?, 0)
                    """,
                    (guild_id, account_id),
                )
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    account_id,
                    (LedgerEntry(slip.foreign, f"impuesto_ext:{concept}"),),
                )
            if slip.irpf:
                self._credit_state_in(connection, guild_id, slip.irpf, f"irpf:{concept}")
            taxable = slip.fiscal_taxable
            if taxable > 0:
                connection.execute(
                    """
                    INSERT INTO economy_tax_records
                        (guild_id, user_id, created_at, concept, gross, withheld)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, now, concept, taxable, slip.irpf),
                )
            connection.execute(
                """
                INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker,
                                             ss_employer, irpf, net, country, foreign_tax,
                                             created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    concept,
                    slip.gross,
                    slip.mpf_worker,
                    slip.mpf_employer,
                    slip.irpf,
                    slip.net,
                    country,
                    slip.hk_tax,
                    now,
                ),
            )
            return slip, balance

    async def foreign_taxes(self, guild_id: int, user_id: int, country: str) -> int:
        """Todo lo que se ha quedado otro país (impuesto y cotizaciones) de un miembro."""
        return await self._run(self._foreign_taxes_sync, guild_id, user_id, country)

    def _foreign_taxes_sync(self, guild_id: int, user_id: int, country: str) -> int:
        connection = self._connect()
        try:
            (total,) = connection.execute(
                """
                SELECT COALESCE(SUM(foreign_tax + ss_worker + ss_employer), 0)
                FROM economy_payroll WHERE guild_id = ? AND user_id = ? AND country = ?
                """,
                (guild_id, user_id, country),
            ).fetchone()
            return int(total)
        finally:
            connection.close()

    async def set_residence(
        self, guild_id: int, user_id: int, country: str | None, now: float
    ) -> None:
        """Apunta que vive fuera (`country`) o que ha vuelto (`None`)."""
        await self._run(self._set_residence_sync, guild_id, user_id, country, now)

    def _set_residence_sync(
        self, guild_id: int, user_id: int, country: str | None, now: float
    ) -> None:
        with self._transaction() as connection:
            if country is None:
                connection.execute(
                    "DELETE FROM economy_residence WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
                return
            connection.execute(
                """
                INSERT INTO economy_residence (guild_id, user_id, country, since)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    country = excluded.country, since = excluded.since
                """,
                (guild_id, user_id, country, now),
            )

    async def residence(self, guild_id: int, user_id: int) -> str | None:
        """País donde vive, o `None` si vive en España."""
        return await self._run(self._residence_sync, guild_id, user_id)

    def _residence_sync(self, guild_id: int, user_id: int) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT country FROM economy_residence WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return str(row["country"]) if row else None
        finally:
            connection.close()

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
                "economy_bizums",
                "economy_slots_jackpots",
                "economy_consumption_tax",
                "economy_lottery_tax",
                "economy_public_debt",
                "economy_payroll",
                "economy_sanctions",
                "economy_imv_suspensions",
                "economy_residence",
                "economy_interest_days",
                "economy_interest",
                "economy_interest_streaks",
                "economy_interest_weeks",
                "economy_interest_savings",
                "economy_porra_bets",
                "economy_porra_settled",
                "economy_gaming_tax",
            ):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
