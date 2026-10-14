"""Persistencia del Rastro: puestos, pujas y ventas de segunda mano entre miembros.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `market_listings`: cada puesto, con el objeto (copia de nombre, emoji y número de
  serie para enseñarlo), el precio de salida, la puja más alta (quién, cuánto, con
  qué valor de referencia y qué ITP retiene) y cuándo se adjudica. Mientras está
  abierto, el objeto está en `shop_inventory` a nombre de `MARKET_ACCOUNT_ID`.
- `market_bids`: cada puja, para los logros y para contar si hubo subasta.
- `market_sales`: cada venta cerrada con sus cuentas. `cost` es lo que le costó a
  quien compra, y sirve para su plusvalía si lo vuelve a vender.

El dinero no se toca aquí. Lo que mueve dinero (`bid`, `sell`, `close`) devuelve una
función que corre dentro de la transacción de `EconomyRepository.market`: el objeto,
el puesto y el dinero se guardan juntos o no se guarda nada. Abrir y retirar un
puesto no mueven dinero y van en su propia transacción.

Solo se guardan IDs, nombres de artículos, importes y marcas de tiempo. Todo se
borra cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.repositories.economy import MARKET_ACCOUNT_ID, MarketMove, MarketPayment
from bot.repositories.shop import move_entry_in
from bot.services.rastro import (
    AUCTION_SECONDS,
    LISTING_SECONDS,
    MAX_LISTINGS,
    Deal,
    MarketError,
    escrow,
    min_bid,
    untradeable,
)
from bot.services.shop import Kind, money

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Listing:
    """Un puesto del Rastro.

    Attributes:
        start_price: Precio de salida; la primera puja tiene que llegar.
        close_at: Cuándo se adjudica (`None` hasta la primera puja).
        expires_at: Cuándo se cierra si nadie puja.
        top_bidder: Quien va ganando, o `None`.
        top_bid: Su puja.
        top_escrow: Lo que tiene retenido en el depósito (puja más ITP).
        top_reference: Valor de referencia con el que se calculó su ITP.
        bids: Pujas recibidas.
    """

    id: int
    guild_id: int
    seller_id: int
    entry_id: int
    item_id: int
    name: str
    emoji: str
    serial: int | None
    edition: int | None
    catalog_key: str | None
    start_price: int
    channel_id: int | None
    created_at: float
    expires_at: float
    close_at: float | None
    top_bidder: int | None
    top_bid: int | None
    top_escrow: int | None
    top_reference: int | None
    bids: int
    status: str

    @property
    def label(self) -> str:
        """`🏰 Marquesado de Mogán nº 3/10`."""
        text = f"{self.emoji} {self.name}"
        if self.serial is not None:
            text += f" nº {self.serial}/{self.edition}" if self.edition else f" nº {self.serial}"
        return text

    @property
    def next_bid(self) -> int:
        """Lo mínimo que hay que pujar ahora."""
        return min_bid(self.start_price, self.top_bid)

    @property
    def deadline(self) -> float:
        """Cuándo deja de admitir pujas: la adjudicación o la caducidad del puesto."""
        return self.close_at if self.close_at is not None else self.expires_at


@dataclass(frozen=True, slots=True)
class Bid:
    """Lo que ha pasado al pujar.

    Attributes:
        listing: El puesto ya con la puja nueva.
        outbid: Quien iba ganando y ha sido superado, o `None`.
        outbid_amount: Su puja.
        itp: ITP que se le retiene a quien puja (lo paga si gana).
    """

    listing: Listing
    outbid: int | None
    outbid_amount: int | None
    itp: int


@dataclass(frozen=True, slots=True)
class Trade:
    """Una venta cerrada: directa o adjudicada en subasta.

    Attributes:
        listing: El puesto, si venía de una subasta.
        void: Si la subasta no se pudo adjudicar (se devolvió todo).
        expired: Si el puesto se cerró sin pujas (el objeto vuelve a su dueño).
    """

    seller_id: int
    buyer_id: int | None
    name: str
    emoji: str
    serial: int | None
    edition: int | None
    catalog_key: str | None
    deal: Deal | None
    listing: Listing | None = None
    void: bool = False
    expired: bool = False


def _listing(row: sqlite3.Row) -> Listing:
    return Listing(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        seller_id=int(row["seller_id"]),
        entry_id=int(row["entry_id"]),
        item_id=int(row["item_id"]),
        name=row["name"],
        emoji=row["emoji"],
        serial=row["serial"],
        edition=row["edition"],
        catalog_key=row["catalog_key"],
        start_price=int(row["start_price"]),
        channel_id=row["channel_id"],
        created_at=float(row["created_at"]),
        expires_at=float(row["expires_at"]),
        close_at=row["close_at"],
        top_bidder=row["top_bidder"],
        top_bid=row["top_bid"],
        top_escrow=row["top_escrow"],
        top_reference=row["top_reference"],
        bids=int(row["bids"]),
        status=row["status"],
    )


class MarketRepository:
    """Acceso SQLite al Rastro; comparte archivo con la tienda y la economía."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas del Rastro si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    seller_id INTEGER NOT NULL,
                    entry_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    serial INTEGER,
                    edition INTEGER,
                    catalog_key TEXT,
                    start_price INTEGER NOT NULL CHECK (start_price > 0),
                    channel_id INTEGER,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    close_at REAL,
                    top_bidder INTEGER,
                    top_bid INTEGER,
                    top_escrow INTEGER,
                    top_reference INTEGER,
                    bids INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'sold', 'cancelled', 'expired', 'void')),
                    closed_at REAL
                );

                CREATE INDEX IF NOT EXISTS market_listings_open
                    ON market_listings (status, guild_id);

                CREATE TABLE IF NOT EXISTS market_bids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    listing_id INTEGER NOT NULL,
                    bidder_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    entry_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    seller_id INTEGER NOT NULL,
                    buyer_id INTEGER NOT NULL,
                    price INTEGER NOT NULL CHECK (price >= 0),
                    reference INTEGER NOT NULL,
                    itp INTEGER NOT NULL,
                    gain_tax INTEGER NOT NULL,
                    cost INTEGER NOT NULL,
                    listing_id INTEGER,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS market_sales_entry
                    ON market_sales (guild_id, entry_id, buyer_id);
                """
            )
        finally:
            connection.close()

    # -- Lecturas --------------------------------------------------------------------

    async def open_listings(self) -> list[Listing]:
        """Puestos abiertos de todos los servidores (para la caché del cog)."""
        return await self._run(self._open_sync)

    def _open_sync(self) -> list[Listing]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM market_listings WHERE status = 'open' ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        return [_listing(row) for row in rows]

    async def reference(self, guild_id: int, item_id: int) -> tuple[int, int, int | None]:
        """`(valor de referencia, nivel mínimo, límite por persona)` de un artículo."""
        return await self._run(self._reference_sync, guild_id, item_id)

    def _reference_sync(self, guild_id: int, item_id: int) -> tuple[int, int, int | None]:
        connection = self._connect()
        try:
            return _item_rules_in(connection, guild_id, item_id)
        finally:
            connection.close()

    async def appraise(
        self, guild_id: int, item_id: int, entry_id: int, owner: int
    ) -> tuple[int, int, int | None, int]:
        """Tasación para enseñar una oferta antes de cerrarla.

        Returns:
            `(valor de referencia, nivel mínimo, límite por persona, lo que le costó
            a `owner`)`. Al aceptar se vuelve a calcular dentro de la transacción.
        """
        return await self._run(self._appraise_sync, guild_id, item_id, entry_id, owner)

    def _appraise_sync(
        self, guild_id: int, item_id: int, entry_id: int, owner: int
    ) -> tuple[int, int, int | None, int]:
        connection = self._connect()
        try:
            reference, level, per_user = _item_rules_in(connection, guild_id, item_id)
            return reference, level, per_user, _cost_in(connection, guild_id, entry_id, owner)
        finally:
            connection.close()

    # -- Puestos (sin dinero) ----------------------------------------------------------

    async def open_stall(
        self,
        guild_id: int,
        seller_id: int,
        entry_id: int,
        *,
        start_price: int,
        channel_id: int | None,
        now: float,
    ) -> Listing:
        """Pone un objeto de la mochila en un puesto; el objeto pasa a guardarlo el Rastro.

        Raises:
            MarketError: Si no es suyo, no se vende o ya tiene `MAX_LISTINGS` puestos.
        """
        return await self._run(
            self._open_stall_sync, guild_id, seller_id, entry_id, start_price, channel_id, now
        )

    def _open_stall_sync(
        self,
        guild_id: int,
        seller_id: int,
        entry_id: int,
        start_price: int,
        channel_id: int | None,
        now: float,
    ) -> Listing:
        if start_price <= 0:
            raise MarketError("En el Rastro hay que poner precio de salida: al menos 1 Y$.")
        connection = self._connect()
        try:
            with connection:
                (open_count,) = connection.execute(
                    """
                    SELECT COUNT(*) FROM market_listings
                    WHERE guild_id = ? AND seller_id = ? AND status = 'open'
                    """,
                    (guild_id, seller_id),
                ).fetchone()
                if open_count >= MAX_LISTINGS:
                    raise MarketError(
                        f"Ya tienes {MAX_LISTINGS} puestos en el Rastro. "
                        "Esto no es El Corte Inglés."
                    )
                entry = _owned_in(connection, guild_id, seller_id, entry_id)
                moved = move_entry_in(connection, guild_id, entry_id, seller_id, MARKET_ACCOUNT_ID)
                assert moved is not None  # `_owned_in` ya ha visto que es suyo
                cursor = connection.execute(
                    """
                    INSERT INTO market_listings
                        (guild_id, seller_id, entry_id, item_id, name, emoji, serial, edition,
                         catalog_key, start_price, channel_id, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        seller_id,
                        entry_id,
                        entry["item_id"],
                        entry["name"],
                        entry["emoji"],
                        entry["serial"],
                        entry["edition"],
                        entry["catalog_key"],
                        start_price,
                        channel_id,
                        now,
                        now + LISTING_SECONDS,
                    ),
                )
                return _listing_in(connection, int(cursor.lastrowid or 0))
        finally:
            connection.close()

    async def withdraw(self, guild_id: int, listing_id: int, seller_id: int, now: float) -> Listing:
        """Retira un puesto sin pujas; el objeto vuelve a la mochila de su dueño.

        Raises:
            MarketError: Si no es suyo, ya está cerrado o ya tiene pujas.
        """
        return await self._run(self._withdraw_sync, guild_id, listing_id, seller_id, now)

    def _withdraw_sync(self, guild_id: int, listing_id: int, seller_id: int, now: float) -> Listing:
        connection = self._connect()
        try:
            with connection:
                listing = _open_listing_in(connection, guild_id, listing_id)
                if listing.seller_id != seller_id:
                    raise MarketError("Ese puesto no es tuyo.")
                if listing.top_bidder is not None:
                    raise MarketError(
                        "Ya hay pujas: lo prometido es deuda. Esto no es una promesa electoral."
                    )
                _return_to_seller_in(connection, listing, "cancelled", now)
                return _listing_in(connection, listing.id)
        finally:
            connection.close()

    # -- Lo que mueve dinero (corre en `EconomyRepository.market`) ---------------------

    def bid(
        self, guild_id: int, listing_id: int, bidder_id: int, amount: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[Bid, MarketMove]]:
        """Puja `amount` por un puesto. Lo pujado y su ITP van al depósito.

        Raises (al correr):
            MarketError: Puesto cerrado, puja corta, es el vendedor, ya va ganando o
                se pasaría del límite por persona del artículo.
        """

        def run(connection: sqlite3.Connection) -> tuple[Bid, MarketMove]:
            listing = _open_listing_in(connection, guild_id, listing_id)
            if now >= listing.deadline:
                raise MarketError("Ese puesto ya ha cerrado. ¡Adjudicado a otro, mi amor!")
            if bidder_id == listing.seller_id:
                raise MarketError(
                    "Pujar por lo tuyo para inflar el precio es de la Gürtel. Aquí no."
                )
            if bidder_id == listing.top_bidder:
                raise MarketError("Ya vas ganando: no te pujes a ti mismo, que no es el CIS.")
            if amount < listing.next_bid:
                raise MarketError(f"La puja mínima ahora es de {money(listing.next_bid)} Y$.")
            reference, _level, per_user = _item_rules_in(connection, guild_id, listing.item_id)
            if per_user is not None:
                held = _held_in(connection, guild_id, bidder_id, listing.item_id)
                (winning,) = connection.execute(
                    """
                    SELECT COUNT(*) FROM market_listings
                    WHERE guild_id = ? AND status = 'open' AND top_bidder = ? AND item_id = ?
                    """,
                    (guild_id, bidder_id, listing.item_id),
                ).fetchone()
                if held + winning >= per_user:
                    raise MarketError(
                        "Ya tienes (o vas ganando) todos los que se pueden tener de eso."
                    )
            held_back = escrow(amount, reference)
            connection.execute(
                """
                UPDATE market_listings
                SET top_bidder = ?, top_bid = ?, top_escrow = ?, top_reference = ?,
                    bids = bids + 1, close_at = ?
                WHERE id = ?
                """,
                (bidder_id, amount, held_back, reference, now + AUCTION_SECONDS, listing.id),
            )
            connection.execute(
                """
                INSERT INTO market_bids (guild_id, listing_id, bidder_id, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, listing.id, bidder_id, amount, now),
            )
            refunds = (
                ((listing.top_bidder, listing.top_escrow or 0),)
                if listing.top_bidder is not None
                else ()
            )
            result = Bid(
                listing=_listing_in(connection, listing.id),
                outbid=listing.top_bidder,
                outbid_amount=listing.top_bid,
                itp=held_back - amount,
            )
            return result, MarketMove(hold=(bidder_id, held_back), refunds=refunds)

        return run

    def sell(
        self, guild_id: int, seller_id: int, buyer_id: int, entry_id: int, price: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[Trade, MarketMove]]:
        """Venta directa: el objeto pasa a `buyer_id` y paga precio e ITP de su monedero.

        Raises (al correr):
            MarketError: Si el objeto ya no es de quien vende, no se vende o quien
                compra ya tiene todos los que puede tener.
        """

        def run(connection: sqlite3.Connection) -> tuple[Trade, MarketMove]:
            if buyer_id == seller_id or buyer_id <= 0:
                raise MarketError("Venderte algo a ti mismo es blanquear, no comerciar.")
            entry = _owned_in(connection, guild_id, seller_id, entry_id)
            reference, _level, per_user = _item_rules_in(connection, guild_id, entry["item_id"])
            if (
                per_user is not None
                and _held_in(connection, guild_id, buyer_id, entry["item_id"]) >= per_user
            ):
                raise MarketError("Esa persona ya tiene todos los que se pueden tener de eso.")
            deal = Deal(price, reference, _cost_in(connection, guild_id, entry_id, seller_id))
            return _hand_over_in(
                connection, guild_id, entry, seller_id, seller_id, buyer_id, deal, None, now
            )

        return run

    def close(
        self, guild_id: int, listing_id: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[Trade, MarketMove]]:
        """Cierra un puesto que ha llegado a su hora: adjudica, o devuelve el objeto.

        Sin pujas, el objeto vuelve a su dueño. Con pujas, pasa a quien gana y su
        puja sale del depósito; si ya no puede tenerlo (límite por persona), la
        subasta queda desierta y se le devuelve todo.

        Raises (al correr):
            MarketError: Si el puesto ya no está abierto o aún no es su hora.
        """

        def run(connection: sqlite3.Connection) -> tuple[Trade, MarketMove]:
            listing = _open_listing_in(connection, guild_id, listing_id)
            if now < listing.deadline:
                raise MarketError("Aún no es la hora de cerrar ese puesto.")
            base = {
                "seller_id": listing.seller_id,
                "name": listing.name,
                "emoji": listing.emoji,
                "serial": listing.serial,
                "edition": listing.edition,
                "catalog_key": listing.catalog_key,
            }
            buyer = listing.top_bidder
            if buyer is None:
                _return_to_seller_in(connection, listing, "expired", now)
                closed = _listing_in(connection, listing.id)
                return Trade(buyer_id=None, deal=None, listing=closed, expired=True, **base), (
                    MarketMove()
                )
            _reference, _level, per_user = _item_rules_in(connection, guild_id, listing.item_id)
            if (
                per_user is not None
                and _held_in(connection, guild_id, buyer, listing.item_id) >= per_user
            ):
                _return_to_seller_in(connection, listing, "void", now)
                closed = _listing_in(connection, listing.id)
                refund = ((buyer, listing.top_escrow or 0),)
                return Trade(buyer_id=buyer, deal=None, listing=closed, void=True, **base), (
                    MarketMove(refunds=refund)
                )
            entry = connection.execute(
                "SELECT * FROM shop_inventory WHERE id = ?", (listing.entry_id,)
            ).fetchone()
            deal = Deal(
                listing.top_bid or 0,
                listing.top_reference or 0,
                _cost_in(connection, guild_id, listing.entry_id, listing.seller_id),
            )
            connection.execute(
                "UPDATE market_listings SET status = 'sold', closed_at = ? WHERE id = ?",
                (now, listing.id),
            )
            return _hand_over_in(
                connection,
                guild_id,
                entry,
                MARKET_ACCOUNT_ID,
                listing.seller_id,
                buyer,
                deal,
                _listing_in(connection, listing.id),
                now,
            )

        return run

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra los puestos, pujas y ventas de un servidor."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                for table in ("market_listings", "market_bids", "market_sales"):
                    # `table` sale de una tupla fija, nunca de entrada del usuario.
                    connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        finally:
            connection.close()


# -- Piezas que corren dentro de una transacción abierta ---------------------------------


def _listing_in(connection: sqlite3.Connection, listing_id: int) -> Listing:
    row = connection.execute("SELECT * FROM market_listings WHERE id = ?", (listing_id,)).fetchone()
    return _listing(row)


def _open_listing_in(connection: sqlite3.Connection, guild_id: int, listing_id: int) -> Listing:
    row = connection.execute(
        "SELECT * FROM market_listings WHERE id = ? AND guild_id = ?", (listing_id, guild_id)
    ).fetchone()
    if row is None or row["status"] != "open":
        raise MarketError("Ese puesto ya no está en el Rastro.")
    return _listing(row)


def _owned_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, entry_id: int
) -> sqlite3.Row:
    """El objeto de la mochila, si es de `user_id`, está en vigor y se puede vender."""
    row = connection.execute(
        """
        SELECT * FROM shop_inventory
        WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'active'
        """,
        (entry_id, guild_id, user_id),
    ).fetchone()
    if row is None:
        raise MarketError("Ese objeto ya no está en tu mochila.")
    if (reason := untradeable(Kind(row["kind"]), row["catalog_key"])) is not None:
        raise MarketError(reason)
    return row


def _item_rules_in(
    connection: sqlite3.Connection, guild_id: int, item_id: int
) -> tuple[int, int, int | None]:
    """`(valor de referencia, nivel mínimo, límite por persona)` del artículo de origen.

    El valor de referencia es el precio de catálogo, sin IGIC ni rebaja. Si el
    artículo ya no existe, vale 0 y no hay requisitos: solo cuenta el precio.
    """
    row = connection.execute(
        "SELECT price, min_level, per_user FROM shop_items WHERE guild_id = ? AND id = ?",
        (guild_id, item_id),
    ).fetchone()
    if row is None:
        return 0, 0, None
    return int(row["price"]), int(row["min_level"]), row["per_user"]


def _held_in(connection: sqlite3.Connection, guild_id: int, user_id: int, item_id: int) -> int:
    """Unidades en vigor de un artículo en la mochila de un miembro."""
    (held,) = connection.execute(
        """
        SELECT COUNT(*) FROM shop_inventory
        WHERE guild_id = ? AND user_id = ? AND item_id = ? AND status = 'active'
        """,
        (guild_id, user_id, item_id),
    ).fetchone()
    return int(held)


def _cost_in(connection: sqlite3.Connection, guild_id: int, entry_id: int, owner: int) -> int:
    """Lo que le costó un objeto a su dueño, para la plusvalía.

    Si lo compró en el Rastro, lo que apuntó esa venta; si no, lo pagado en la
    tienda sin IGIC; si salió de una caja o fue un regalo, 0.
    """
    row = connection.execute(
        """
        SELECT cost FROM market_sales WHERE guild_id = ? AND entry_id = ? AND buyer_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (guild_id, entry_id, owner),
    ).fetchone()
    if row is not None:
        return int(row["cost"])
    (paid,) = connection.execute(
        """
        SELECT COALESCE(SUM(base), 0) FROM shop_purchases
        WHERE guild_id = ? AND inventory_id = ? AND user_id = ? AND refunded = 0
        """,
        (guild_id, entry_id, owner),
    ).fetchone()
    return int(paid)


def _return_to_seller_in(
    connection: sqlite3.Connection, listing: Listing, status: str, now: float
) -> None:
    moved = move_entry_in(
        connection, listing.guild_id, listing.entry_id, MARKET_ACCOUNT_ID, listing.seller_id
    )
    if moved is None:
        raise MarketError("El objeto de ese puesto se ha perdido por el camino.")
    connection.execute(
        "UPDATE market_listings SET status = ?, closed_at = ? WHERE id = ?",
        (status, now, listing.id),
    )


def _hand_over_in(
    connection: sqlite3.Connection,
    guild_id: int,
    entry: sqlite3.Row,
    holder: int,
    seller_id: int,
    buyer_id: int,
    deal: Deal,
    listing: Listing | None,
    now: float,
) -> tuple[Trade, MarketMove]:
    """Pasa el objeto a quien compra, apunta la venta y dice qué dinero se mueve."""
    if move_entry_in(connection, guild_id, int(entry["id"]), holder, buyer_id) is None:
        raise MarketError("Ese objeto ya no está en la mochila de quien lo vende.")
    connection.execute(
        """
        INSERT INTO market_sales
            (guild_id, entry_id, item_id, seller_id, buyer_id, price, reference, itp,
             gain_tax, cost, listing_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            int(entry["id"]),
            int(entry["item_id"]),
            seller_id,
            buyer_id,
            deal.price,
            deal.reference,
            deal.itp,
            deal.gain_tax,
            deal.buyer_cost,
            listing.id if listing else None,
            now,
        ),
    )
    trade = Trade(
        seller_id=seller_id,
        buyer_id=buyer_id,
        name=entry["name"],
        emoji=entry["emoji"],
        serial=entry["serial"],
        edition=entry["edition"],
        catalog_key=entry["catalog_key"],
        deal=deal,
        listing=listing,
    )
    payment = MarketPayment(
        buyer_id=buyer_id,
        seller_id=seller_id,
        price=deal.price,
        itp_base=deal.itp_base,
        itp=deal.itp,
        gain=deal.gain,
        gain_tax=deal.gain_tax,
        escrowed=listing is not None,
    )
    return trade, MarketMove(payment=payment)
