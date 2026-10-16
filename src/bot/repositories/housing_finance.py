"""Persistencia de lo que no es el mercado en `pisos`: hipotecas, VPO, vivienda habitual y más.

Funciones sueltas que reciben la conexión: corren dentro de la transacción que abre
`bot.repositories.housing` (y esta, dentro de la de `EconomyRepository.housing` cuando hay
dinero), así que lo de aquí y lo del mercado se guarda junto o no se guarda.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `housing_mortgages`: cada hipoteca (de un inmueble del mercado, `listing`, o de una
  VPO, `vpo:<clave>`), con lo pendiente, el tipo fijo al firmar, la cuota y lo que se
  debe de cuotas sin pagar.
- `housing_euribor`: el Euríbor simulado de cada lunes.
- `housing_bank_debts`: lo que se sigue debiendo al banco después de una ejecución
  hipotecaria (no hay dación en pago).
- `housing_vpo`: de quién es cada VPO y lo que debe de recibos.
- `housing_vpo_signups`, `housing_vpo_reservations` y `housing_vpo_draws`: quién está
  apuntado al sorteo, a quién le ha tocado y qué lunes se sorteó.
- `housing_homes`: dónde vive cada miembro (su piso, su VPO o el que alquila a otro
  miembro). Es su vivienda habitual.
- `housing_offers`: las ofertas del fondo buitre (fase 4).
- `housing_socimi`: quién ha montado su SOCIMI (fase 4).
- `housing_deposits`: la fianza (y la garantía adicional) que deja un miembro al
  alquilar el piso de otro, mientras espera en el depósito de fianzas, y lo que se
  queda el casero si lo desahucian debiendo meses (`forfeit`).

Solo IDs de miembros, claves del catálogo, importes y fechas.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from bot.services import housing as rules

#: Tablas de este módulo, para crearlas y para borrarlas al salir de un servidor.
TABLES = (
    "housing_mortgages",
    "housing_euribor",
    "housing_bank_debts",
    "housing_vpo",
    "housing_vpo_signups",
    "housing_vpo_reservations",
    "housing_vpo_draws",
    "housing_homes",
    "housing_offers",
    "housing_socimi",
    "housing_deposits",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS housing_mortgages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    principal INTEGER NOT NULL CHECK (principal > 0),
    balance INTEGER NOT NULL CHECK (balance >= 0),
    rate REAL NOT NULL,
    payment INTEGER NOT NULL CHECK (payment >= 0),
    months_left INTEGER NOT NULL,
    unpaid_count INTEGER NOT NULL DEFAULT 0,
    unpaid_amount INTEGER NOT NULL DEFAULT 0 CHECK (unpaid_amount >= 0),
    aval TEXT,
    status TEXT NOT NULL DEFAULT 'activa',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS housing_mortgages_member
    ON housing_mortgages (guild_id, user_id, status);

CREATE TABLE IF NOT EXISTS housing_euribor (
    guild_id INTEGER NOT NULL,
    week TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (guild_id, week)
);

CREATE TABLE IF NOT EXISTS housing_bank_debts (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_vpo (
    guild_id INTEGER NOT NULL,
    unit TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    paid INTEGER NOT NULL,
    since REAL NOT NULL,
    arrears INTEGER NOT NULL DEFAULT 0 CHECK (arrears >= 0),
    arrears_ibi INTEGER NOT NULL DEFAULT 0 CHECK (arrears_ibi >= 0),
    PRIMARY KEY (guild_id, unit)
);

CREATE TABLE IF NOT EXISTS housing_vpo_signups (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_vpo_reservations (
    guild_id INTEGER NOT NULL,
    unit TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    until TEXT NOT NULL,
    PRIMARY KEY (guild_id, unit)
);

CREATE TABLE IF NOT EXISTS housing_vpo_draws (
    guild_id INTEGER NOT NULL,
    week TEXT NOT NULL,
    PRIMARY KEY (guild_id, week)
);

CREATE TABLE IF NOT EXISTS housing_homes (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_offers (
    guild_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    price INTEGER NOT NULL,
    until TEXT NOT NULL,
    PRIMARY KEY (guild_id, listing, user_id)
);

CREATE TABLE IF NOT EXISTS housing_socimi (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_deposits (
    guild_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    forfeit INTEGER NOT NULL DEFAULT 0 CHECK (forfeit >= 0),
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, listing, user_id)
);
"""

#: Viviendas habituales: el piso propio, la VPO o el piso que se alquila a otro miembro.
HOME_OWN = "propia"
HOME_VPO = "vpo"
HOME_RENTED = "alquiler"


def vpo_ref(unit: str) -> str:
    """Clave de una VPO donde se guarda como inmueble (hipotecas, noticias)."""
    return f"vpo:{unit}"


@dataclass(frozen=True, slots=True)
class Mortgage:
    """Una hipoteca.

    Attributes:
        listing: El inmueble (o `vpo:<clave>`).
        principal: Lo prestado.
        balance: Lo que queda por devolver.
        rate: Tipo anual fijo.
        payment: Cuota de cada mes (un día del bot).
        months_left: Cuotas que quedan.
        unpaid_count: Cuotas seguidas sin pagar del todo.
        unpaid_amount: Lo que se debe de cuotas sin pagar.
        aval: `ico` si la avala el Estado (VPO), o `None`.
    """

    id: int
    user_id: int
    listing: str
    principal: int
    balance: int
    rate: float
    payment: int
    months_left: int
    unpaid_count: int
    unpaid_amount: int
    aval: str | None

    @property
    def owed(self) -> int:
        """Todo lo que hay que devolver para cancelarla."""
        return self.balance + self.unpaid_amount


def _mortgage(row: sqlite3.Row) -> Mortgage:
    return Mortgage(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        listing=str(row["listing"]),
        principal=int(row["principal"]),
        balance=int(row["balance"]),
        rate=float(row["rate"]),
        payment=int(row["payment"]),
        months_left=int(row["months_left"]),
        unpaid_count=int(row["unpaid_count"]),
        unpaid_amount=int(row["unpaid_amount"]),
        aval=row["aval"],
    )


def mortgages_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int | None = None
) -> list[Mortgage]:
    """Hipotecas activas del servidor (o de un miembro), de la más antigua a la más nueva."""
    if user_id is None:
        rows = connection.execute(
            "SELECT * FROM housing_mortgages WHERE guild_id = ? AND status = 'activa' ORDER BY id",
            (guild_id,),
        )
    else:
        rows = connection.execute(
            "SELECT * FROM housing_mortgages WHERE guild_id = ? AND user_id = ? "
            "AND status = 'activa' ORDER BY id",
            (guild_id, user_id),
        )
    return [_mortgage(r) for r in rows]


def add_mortgage_in(
    connection: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    listing: str,
    principal: int,
    rate: float,
    payment: int,
    now: float,
    aval: str | None = None,
) -> None:
    """Firma una hipoteca."""
    connection.execute(
        """
        INSERT INTO housing_mortgages
            (guild_id, user_id, listing, principal, balance, rate, payment, months_left, aval,
             created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, user_id, listing, principal, principal, rate, payment,
         rules.MORTGAGE_MONTHS, aval, now),
    )  # fmt: skip


def save_mortgage_in(
    connection: sqlite3.Connection,
    mortgage: Mortgage,
    *,
    balance: int,
    payment: int,
    months_left: int,
    unpaid_count: int,
    unpaid_amount: int,
    status: str = "activa",
) -> None:
    """Guarda cómo queda una hipoteca."""
    connection.execute(
        """
        UPDATE housing_mortgages SET balance = ?, payment = ?, months_left = ?,
            unpaid_count = ?, unpaid_amount = ?, status = ?
        WHERE id = ?
        """,
        (balance, payment, months_left, unpaid_count, unpaid_amount, status, mortgage.id),
    )


def close_mortgage_in(connection: sqlite3.Connection, mortgage_id: int, status: str) -> None:
    """Cierra una hipoteca (`pagada`, `cancelada` al vender o `ejecutada`)."""
    connection.execute(
        "UPDATE housing_mortgages SET balance = 0, unpaid_amount = 0, status = ? WHERE id = ?",
        (status, mortgage_id),
    )


def euribor_in(connection: sqlite3.Connection, guild_id: int) -> float:
    """El Euríbor de esta semana (el de partida si aún no se ha movido)."""
    row = connection.execute(
        "SELECT value FROM housing_euribor WHERE guild_id = ? ORDER BY week DESC LIMIT 1",
        (guild_id,),
    ).fetchone()
    return float(row["value"]) if row else rules.EURIBOR_START


def set_euribor_in(connection: sqlite3.Connection, guild_id: int, week: date, value: float) -> bool:
    """Apunta el Euríbor de una semana. `False` si ya estaba."""
    cursor = connection.execute(
        "INSERT OR IGNORE INTO housing_euribor (guild_id, week, value) VALUES (?, ?, ?)",
        (guild_id, week.isoformat(), value),
    )
    return cursor.rowcount > 0


def bank_debts_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, int]:
    """Lo que debe cada miembro al banco tras una ejecución."""
    return {
        int(r["user_id"]): int(r["amount"])
        for r in connection.execute(
            "SELECT user_id, amount FROM housing_bank_debts WHERE guild_id = ? AND amount > 0",
            (guild_id,),
        )
    }


def add_bank_debt_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, amount: int
) -> None:
    """Suma (o resta, con negativo) a la deuda con el banco, sin bajar de 0."""
    connection.execute(
        """
        INSERT INTO housing_bank_debts (guild_id, user_id, amount) VALUES (?, ?, MAX(0, ?))
        ON CONFLICT (guild_id, user_id) DO UPDATE SET amount = MAX(0, amount + excluded.amount)
        """,
        (guild_id, user_id, amount),
    )


# -- Vivienda habitual ------------------------------------------------------------------


def homes_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, tuple[str, str]]:
    """`{miembro: (tipo, referencia)}` de dónde vive cada uno."""
    return {
        int(r["user_id"]): (str(r["kind"]), str(r["ref"]))
        for r in connection.execute(
            "SELECT user_id, kind, ref FROM housing_homes WHERE guild_id = ?", (guild_id,)
        )
    }


def set_home_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, kind: str, ref: str, now: float
) -> None:
    """Apunta dónde vive alguien (sustituye lo que hubiera)."""
    connection.execute(
        """
        INSERT INTO housing_homes (guild_id, user_id, kind, ref, since) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, user_id) DO UPDATE SET
            kind = excluded.kind, ref = excluded.ref, since = excluded.since
        """,
        (guild_id, user_id, kind, ref, now),
    )


def clear_home_in(
    connection: sqlite3.Connection, guild_id: int, *, user_id: int | None = None,
    ref: str | None = None,
) -> None:  # fmt: skip
    """Borra la vivienda habitual de un miembro o la de quien viva en `ref`."""
    if user_id is not None:
        connection.execute(
            "DELETE FROM housing_homes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
    if ref is not None:
        connection.execute(
            "DELETE FROM housing_homes WHERE guild_id = ? AND ref = ?", (guild_id, ref)
        )


# -- VPO --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VpoOwner:
    """De quién es una VPO y lo que debe de recibos."""

    unit: str
    owner_id: int
    paid: int
    since: float
    arrears: int
    arrears_ibi: int


def vpo_in(connection: sqlite3.Connection, guild_id: int) -> dict[str, VpoOwner]:
    """Las VPO que tienen dueño."""
    return {
        str(r["unit"]): VpoOwner(
            str(r["unit"]),
            int(r["owner_id"]),
            int(r["paid"]),
            float(r["since"]),
            int(r["arrears"]),
            int(r["arrears_ibi"]),
        )  # fmt: skip
        for r in connection.execute("SELECT * FROM housing_vpo WHERE guild_id = ?", (guild_id,))
    }


def vpo_signups_in(connection: sqlite3.Connection, guild_id: int) -> list[int]:
    """Quién está apuntado al sorteo, por orden de inscripción."""
    return [
        int(r["user_id"])
        for r in connection.execute(
            "SELECT user_id FROM housing_vpo_signups WHERE guild_id = ? ORDER BY since, user_id",
            (guild_id,),
        )
    ]


def vpo_reservations_in(
    connection: sqlite3.Connection, guild_id: int
) -> dict[str, tuple[int, date]]:
    """`{vpo: (a quién le tocó, último día para comprarla)}`."""
    return {
        str(r["unit"]): (int(r["user_id"]), date.fromisoformat(r["until"]))
        for r in connection.execute(
            "SELECT unit, user_id, until FROM housing_vpo_reservations WHERE guild_id = ?",
            (guild_id,),
        )
    }


# -- Fondo buitre y SOCIMI (fase 4) -----------------------------------------------------


def offers_in(
    connection: sqlite3.Connection, guild_id: int
) -> dict[tuple[str, int], tuple[int, date]]:
    """`{(inmueble, miembro): (precio, último día)}` de las ofertas del fondo."""
    return {
        (str(r["listing"]), int(r["user_id"])): (int(r["price"]), date.fromisoformat(r["until"]))
        for r in connection.execute(
            "SELECT listing, user_id, price, until FROM housing_offers WHERE guild_id = ?",
            (guild_id,),
        )
    }


def socimi_in(connection: sqlite3.Connection, guild_id: int) -> set[int]:
    """Quién tiene su SOCIMI."""
    return {
        int(r["user_id"])
        for r in connection.execute(
            "SELECT user_id FROM housing_socimi WHERE guild_id = ?", (guild_id,)
        )
    }


@dataclass(frozen=True, slots=True)
class Deposit:
    """La fianza de un miembro de alquiler en el depósito de fianzas.

    Attributes:
        amount: Lo que dejó (fianza y garantía adicional).
        forfeit: Lo que se queda el casero por los meses que debía al desahuciarlo.
    """

    listing: str
    user_id: int
    amount: int
    forfeit: int


def deposits_in(connection: sqlite3.Connection, guild_id: int) -> list[Deposit]:
    """Las fianzas que esperan en el depósito."""
    return [
        Deposit(str(r["listing"]), int(r["user_id"]), int(r["amount"]), int(r["forfeit"]))
        for r in connection.execute(
            "SELECT listing, user_id, amount, forfeit FROM housing_deposits WHERE guild_id = ?",
            (guild_id,),
        )
    ]


def add_deposit_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, user_id: int, amount: int,
    now: float,
) -> None:  # fmt: skip
    """Apunta una fianza (se suma si quedaba otra del mismo piso sin devolver)."""
    connection.execute(
        """
        INSERT INTO housing_deposits (guild_id, listing, user_id, amount, since)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, listing, user_id) DO UPDATE SET amount = amount + excluded.amount
        """,
        (guild_id, listing, user_id, amount, now),
    )


def forfeit_deposit_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, user_id: int, owed: int
) -> None:
    """Marca lo que se queda el casero de una fianza (nunca más de lo que hay)."""
    connection.execute(
        """
        UPDATE housing_deposits SET forfeit = MIN(amount, forfeit + ?)
        WHERE guild_id = ? AND listing = ? AND user_id = ?
        """,
        (max(0, owed), guild_id, listing, user_id),
    )


def delete_deposit_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, user_id: int
) -> None:
    """Borra una fianza ya devuelta."""
    connection.execute(
        "DELETE FROM housing_deposits WHERE guild_id = ? AND listing = ? AND user_id = ?",
        (guild_id, listing, user_id),
    )
