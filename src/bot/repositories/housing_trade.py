"""Persistencia de la compraventa entre miembros de `pisos`: subastas y pujas.

Funciones sueltas que reciben la conexión, como las de `bot.repositories.housing_finance`:
corren dentro de la transacción que abre `bot.repositories.housing` (y esta, dentro de la de
`EconomyRepository.housing` cuando hay dinero).

Tablas (en el mismo archivo SQLite que el resto del bot):

- `housing_auctions`: cada subasta, voluntaria (la pide un dueño) o judicial (la de una
  hipoteca ejecutada, con su `mortgage_id`), con la mejor puja, quién la hizo y lo que
  espera de él en el depósito de las subastas (`escrow`: la puja y lo que pagará de ITP y
  notaría si gana).
- `housing_bids`: cada puja, para los logros y para contar cuántas hubo.

La venta directa a otro miembro no guarda nada aquí: la oferta vive en el mensaje de
Discord y, al aceptarse, la hace `HousingRepository.member_sale` en una sola transacción.

Solo IDs de miembros, claves del catálogo, importes y fechas.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: Tablas de este módulo, para crearlas y para borrarlas al salir de un servidor.
TABLES = ("housing_auctions", "housing_bids")

#: Estados de una subasta.
OPEN = "abierta"
SOLD = "adjudicada"
DESERTED = "desierta"
CANCELLED = "cancelada"

SCHEMA = """
CREATE TABLE IF NOT EXISTS housing_auctions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    seller INTEGER NOT NULL,
    shares INTEGER NOT NULL CHECK (shares > 0 AND shares <= 100),
    min_bid INTEGER NOT NULL CHECK (min_bid > 0),
    ends_at REAL NOT NULL,
    judicial INTEGER NOT NULL DEFAULT 0,
    mortgage_id INTEGER,
    best_bid INTEGER NOT NULL DEFAULT 0,
    best_bidder INTEGER,
    escrow INTEGER NOT NULL DEFAULT 0,
    bids INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'abierta',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS housing_auctions_open ON housing_auctions (guild_id, status);

CREATE TABLE IF NOT EXISTS housing_bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    auction_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    created_at REAL NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Auction:
    """Una subasta de participaciones de un inmueble.

    Attributes:
        id: Su número.
        listing: El inmueble.
        seller: De quién son las participaciones (en una judicial, el ejecutado).
        shares: Cuántas se subastan.
        min_bid: Puja mínima.
        ends_at: Cuándo acaba (epoch); se alarga si alguien puja en la última hora.
        judicial: Si es la subasta de una hipoteca ejecutada.
        mortgage_id: La hipoteca, si es judicial.
        best_bid: La mejor puja (0 si no hay).
        best_bidder: Quién la hizo.
        escrow: Lo que espera de esa persona en el depósito (la puja, el ITP y la notaría).
        bids: Cuántas pujas lleva.
        created_at: Cuándo empezó.
    """

    id: int
    listing: str
    seller: int
    shares: int
    min_bid: int
    ends_at: float
    judicial: bool
    mortgage_id: int | None
    best_bid: int
    best_bidder: int | None
    escrow: int
    bids: int
    created_at: float


def _auction_from(row: sqlite3.Row) -> Auction:
    return Auction(
        id=int(row["id"]),
        listing=str(row["listing"]),
        seller=int(row["seller"]),
        shares=int(row["shares"]),
        min_bid=int(row["min_bid"]),
        ends_at=float(row["ends_at"]),
        judicial=bool(row["judicial"]),
        mortgage_id=None if row["mortgage_id"] is None else int(row["mortgage_id"]),
        best_bid=int(row["best_bid"]),
        best_bidder=None if row["best_bidder"] is None else int(row["best_bidder"]),
        escrow=int(row["escrow"]),
        bids=int(row["bids"]),
        created_at=float(row["created_at"]),
    )


def auctions_in(connection: sqlite3.Connection, guild_id: int) -> tuple[Auction, ...]:
    """Las subastas abiertas de un servidor, de la que acaba antes a la que acaba después."""
    rows = connection.execute(
        "SELECT * FROM housing_auctions WHERE guild_id = ? AND status = ? ORDER BY ends_at, id",
        (guild_id, OPEN),
    ).fetchall()
    return tuple(_auction_from(r) for r in rows)


def add_auction_in(
    connection: sqlite3.Connection,
    guild_id: int,
    *,
    listing: str,
    seller: int,
    shares: int,
    min_bid: int,
    ends_at: float,
    now: float,
    mortgage_id: int | None = None,
) -> int:
    """Abre una subasta (judicial si lleva `mortgage_id`). Devuelve su número."""
    cursor = connection.execute(
        """
        INSERT INTO housing_auctions
            (guild_id, listing, seller, shares, min_bid, ends_at, judicial, mortgage_id,
             status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, listing, seller, shares, min_bid, ends_at, int(mortgage_id is not None),
         mortgage_id, OPEN, now),
    )  # fmt: skip
    return int(cursor.lastrowid or 0)


def save_bid_in(
    connection: sqlite3.Connection,
    guild_id: int,
    auction: Auction,
    user_id: int,
    amount: int,
    escrow: int,
    ends_at: float,
    now: float,
) -> None:
    """Apunta una puja: pasa a ser la mejor y la subasta se alarga si toca."""
    connection.execute(
        """
        UPDATE housing_auctions
        SET best_bid = ?, best_bidder = ?, escrow = ?, bids = bids + 1, ends_at = ?
        WHERE id = ?
        """,
        (amount, user_id, escrow, ends_at, auction.id),
    )
    connection.execute(
        "INSERT INTO housing_bids (guild_id, auction_id, user_id, amount, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (guild_id, auction.id, user_id, amount, now),
    )


def close_auction_in(connection: sqlite3.Connection, auction_id: int, status: str) -> None:
    """Cierra una subasta (adjudicada, desierta o cancelada)."""
    connection.execute("UPDATE housing_auctions SET status = ? WHERE id = ?", (status, auction_id))
