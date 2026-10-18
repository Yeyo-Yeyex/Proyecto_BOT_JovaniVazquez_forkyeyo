"""Persistencia de la junta de copropietarios de `pisos`: votaciones, administradores y fondo.

Funciones sueltas que reciben la conexión, como las de `bot.repositories.housing_trade`:
corren dentro de la transacción que abre `bot.repositories.housing`.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `housing_votes`: cada propuesta de la junta de un inmueble con varios dueños miembros
  (`housing.MOTIONS`), con lo que se propone (`value` o `amount`), cuándo acaba y cómo acabó.
- `housing_ballots`: el voto de cada dueño (quien no vota cuenta a favor).
- `housing_admins`: quien administra cada inmueble por acuerdo de la junta (si no hay, la
  mayoría absoluta, `housing.manager`).
- `housing_reserve`: el fondo de reserva de cada inmueble (art. 9.1.f LPH). Se queda con el
  inmueble aunque cambie de dueños: es de la comunidad.

Solo IDs de miembros, claves del catálogo, importes y fechas.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

#: Tablas de este módulo, para crearlas y para borrarlas al salir de un servidor.
TABLES = ("housing_votes", "housing_ballots", "housing_admins", "housing_reserve")

#: Estados de una votación.
OPEN = "abierta"
APPROVED = "aprobada"
REJECTED = "rechazada"
FAILED = "fallida"

SCHEMA = """
CREATE TABLE IF NOT EXISTS housing_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    proposer INTEGER NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    amount INTEGER NOT NULL DEFAULT 0,
    ends_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'abierta',
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS housing_votes_open ON housing_votes (guild_id, status);

CREATE TABLE IF NOT EXISTS housing_ballots (
    guild_id INTEGER NOT NULL,
    vote_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    yes INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (vote_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_admins (
    guild_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, listing)
);

CREATE TABLE IF NOT EXISTS housing_reserve (
    guild_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    PRIMARY KEY (guild_id, listing)
);
"""


@dataclass(frozen=True, slots=True)
class Vote:
    """Una propuesta de la junta.

    Attributes:
        id: Su número.
        listing: El inmueble.
        proposer: Quién la propone (vota a favor).
        kind: Qué se vota (`housing.Motion.key`).
        value: Lo propuesto (la renta, `1`/`0`, el modo, el miembro, la reforma…).
        amount: La derrama, en Y$ (solo `derrama`).
        ends_at: Cuándo se cierra (epoch).
        ballots: `{miembro: sí}` de quienes han votado.
        status: `abierta`, `aprobada`, `rechazada` o `fallida` (aprobada pero no se pudo).
        note: Por qué falló, si falló.
    """

    id: int
    listing: str
    proposer: int
    kind: str
    value: str
    amount: int
    ends_at: float
    ballots: dict[int, bool] = field(default_factory=dict)
    status: str = OPEN
    note: str = ""


def votes_in(connection: sqlite3.Connection, guild_id: int) -> tuple[Vote, ...]:
    """Las votaciones abiertas de un servidor, de la que acaba antes a la que acaba después."""
    rows = connection.execute(
        "SELECT * FROM housing_votes WHERE guild_id = ? AND status = ? ORDER BY ends_at, id",
        (guild_id, OPEN),
    ).fetchall()
    ballots: dict[int, dict[int, bool]] = {}
    if rows:
        marks = ",".join("?" * len(rows))
        for b in connection.execute(
            f"SELECT vote_id, user_id, yes FROM housing_ballots WHERE vote_id IN ({marks})",
            tuple(int(r["id"]) for r in rows),
        ):
            ballots.setdefault(int(b["vote_id"]), {})[int(b["user_id"])] = bool(b["yes"])
    return tuple(
        Vote(
            int(r["id"]),
            str(r["listing"]),
            int(r["proposer"]),
            str(r["kind"]),
            str(r["value"]),
            int(r["amount"]),
            float(r["ends_at"]),
            ballots.get(int(r["id"]), {}),
        )  # fmt: skip
        for r in rows
    )


def add_vote_in(
    connection: sqlite3.Connection,
    guild_id: int,
    *,
    listing: str,
    proposer: int,
    kind: str,
    value: str,
    amount: int,
    ends_at: float,
    now: float,
) -> int:
    """Abre una votación con el sí de quien la propone. Devuelve su número."""
    cursor = connection.execute(
        """
        INSERT INTO housing_votes
            (guild_id, listing, proposer, kind, value, amount, ends_at, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, listing, proposer, kind, value, amount, ends_at, OPEN, now),
    )
    number = int(cursor.lastrowid or 0)
    ballot_in(connection, guild_id, number, proposer, True, now)
    return number


def ballot_in(
    connection: sqlite3.Connection, guild_id: int, vote_id: int, user_id: int, yes: bool,
    now: float,
) -> None:  # fmt: skip
    """Apunta (o cambia) el voto de un dueño."""
    connection.execute(
        """
        INSERT INTO housing_ballots (guild_id, vote_id, user_id, yes, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (vote_id, user_id) DO UPDATE SET yes = excluded.yes,
            created_at = excluded.created_at
        """,
        (guild_id, vote_id, user_id, int(yes), now),
    )


def close_vote_in(
    connection: sqlite3.Connection, vote_id: int, status: str, note: str = ""
) -> None:
    """Cierra una votación con su resultado."""
    connection.execute(
        "UPDATE housing_votes SET status = ?, note = ? WHERE id = ?", (status, note, vote_id)
    )


def admins_in(connection: sqlite3.Connection, guild_id: int) -> dict[str, int]:
    """`{inmueble: miembro}` de quien administra por acuerdo de la junta."""
    return {
        str(r["listing"]): int(r["user_id"])
        for r in connection.execute(
            "SELECT listing, user_id FROM housing_admins WHERE guild_id = ?", (guild_id,)
        )
    }


def set_admin_in(connection: sqlite3.Connection, guild_id: int, listing: str, user_id: int) -> None:
    """Pone a alguien a administrar un inmueble."""
    connection.execute(
        """
        INSERT INTO housing_admins (guild_id, listing, user_id) VALUES (?, ?, ?)
        ON CONFLICT (guild_id, listing) DO UPDATE SET user_id = excluded.user_id
        """,
        (guild_id, listing, user_id),
    )


def reserves_in(connection: sqlite3.Connection, guild_id: int) -> dict[str, int]:
    """El fondo de reserva de cada inmueble que tiene algo."""
    return {
        str(r["listing"]): int(r["balance"])
        for r in connection.execute(
            "SELECT listing, balance FROM housing_reserve WHERE guild_id = ? AND balance > 0",
            (guild_id,),
        )
    }


def add_reserve_in(connection: sqlite3.Connection, guild_id: int, listing: str, delta: int) -> None:
    """Suma (o resta, si `delta` es negativo) al fondo de reserva de un inmueble."""
    connection.execute(
        """
        INSERT INTO housing_reserve (guild_id, listing, balance) VALUES (?, ?, MAX(0, ?))
        ON CONFLICT (guild_id, listing) DO UPDATE SET balance = MAX(0, balance + ?)
        """,
        (guild_id, listing, delta, delta),
    )
