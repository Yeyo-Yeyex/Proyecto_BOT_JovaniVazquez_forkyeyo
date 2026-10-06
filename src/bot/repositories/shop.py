"""Persistencia de la tienda: catálogo, ventas e inventario.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `shop_items`: el catálogo de cada servidor, tal y como lo configuran los
  administradores con `catalogo`.
- `shop_purchases`: una fila por venta, con su número de factura (correlativo
  por servidor), lo cobrado y si se devolvió.
- `shop_inventory`: lo que tiene cada miembro. Guarda una copia del nombre,
  el emoji y la clave de serie para que un objeto siga en la mochila (y se
  pueda usar) aunque el artículo salga del catálogo. Los alquileres y
  potenciadores llevan `expires_at`; los objetos gastados pasan a `expired`.
- `shop_seeded`: qué artículos del surtido de serie
  (`bot.services.shop_catalog`) se han metido ya en cada servidor, para no
  volver a meter lo que un administrador haya retirado.

El dinero no se toca aquí: la venta se apunta con `reserve`, que corre dentro
de la transacción de `EconomyRepository.purchase`, así que el cobro, el IGIC,
la unidad vendida y la factura se guardan juntos o no se guarda nada.

Solo se guardan IDs, nombres de artículos y marcas de tiempo. Todo se borra
cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from bot.services.shop import (
    BEST_SELLER_MIN,
    Kind,
    Quote,
    Sale,
    ShopError,
    ShopItem,
    boost_window,
    ineligibility,
    quote,
    rental_end,
)

if TYPE_CHECKING:
    from bot.services.shop_catalog import CatalogEntry

T = TypeVar("T")

#: Columnas que `update_item` deja cambiar; el resto (id, tipo, ventas) es fijo.
EDITABLE_FIELDS = frozenset(
    {
        "name",
        "emoji",
        "description",
        "price",
        "igic",
        "duration",
        "multiplier",
        "stock",
        "per_user",
        "min_level",
        "discount",
        "discount_until",
        "visible",
    }
)


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """Algo que tiene un miembro.

    Attributes:
        item_id: Artículo de origen (puede que ya no esté en el catálogo).
        serial: Número de serie si era de edición limitada.
        edition: Unidades de esa edición.
        starts_at: Desde cuándo vale (potenciadores en cola).
        expires_at: Hasta cuándo; `None` para siempre.
        equipped: Si el rol está puesto (solo roles para siempre).
        catalog_key: Clave del surtido de serie del artículo de origen. Se
            copia al comprar para que un objeto se pueda seguir usando aunque
            el artículo salga del catálogo.
    """

    id: int
    item_id: int
    kind: Kind
    name: str
    emoji: str
    role_id: int | None
    serial: int | None
    edition: int | None
    multiplier: int | None
    starts_at: float
    expires_at: float | None
    equipped: bool
    catalog_key: str | None = None


@dataclass(frozen=True, slots=True)
class ShopStats:
    """Resumen de ventas de un servidor para la trastienda."""

    sales: int
    revenue: int
    tax: int


def _item(row: sqlite3.Row) -> ShopItem:
    return ShopItem(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        kind=Kind(row["kind"]),
        name=row["name"],
        emoji=row["emoji"],
        description=row["description"],
        price=int(row["price"]),
        igic_key=row["igic"],
        role_id=row["role_id"],
        duration=row["duration"],
        multiplier=row["multiplier"],
        stock=row["stock"],
        sold=int(row["sold"]),
        per_user=row["per_user"],
        min_level=int(row["min_level"]),
        discount=int(row["discount"]),
        discount_until=row["discount_until"],
        visible=bool(row["visible"]),
        created_at=float(row["created_at"]),
        catalog_key=row["catalog_key"],
    )


def _entry(row: sqlite3.Row) -> InventoryEntry:
    return InventoryEntry(
        id=int(row["id"]),
        item_id=int(row["item_id"]),
        kind=Kind(row["kind"]),
        name=row["name"],
        emoji=row["emoji"],
        role_id=row["role_id"],
        serial=row["serial"],
        edition=row["edition"],
        multiplier=row["multiplier"],
        starts_at=float(row["starts_at"]),
        expires_at=row["expires_at"],
        equipped=bool(row["equipped"]),
        catalog_key=row["catalog_key"],
    )


# Orden del escaparate: roles, potenciadores y coleccionables; dentro, de barato a caro.
_ITEM_ORDER = """
    ORDER BY CASE kind WHEN 'rol' THEN 0 WHEN 'xp' THEN 1 ELSE 2 END, price, id
"""


class ShopRepository:
    """Acceso SQLite al catálogo y al inventario de la tienda.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
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
                CREATE TABLE IF NOT EXISTS shop_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('rol', 'xp', 'objeto')),
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    price INTEGER NOT NULL CHECK (price > 0),
                    igic TEXT NOT NULL,
                    role_id INTEGER,
                    duration INTEGER,
                    multiplier INTEGER,
                    stock INTEGER,
                    sold INTEGER NOT NULL DEFAULT 0 CHECK (sold >= 0),
                    per_user INTEGER,
                    min_level INTEGER NOT NULL DEFAULT 0,
                    discount INTEGER NOT NULL DEFAULT 0,
                    discount_until REAL,
                    visible INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS shop_items_guild ON shop_items (guild_id);

                CREATE TABLE IF NOT EXISTS shop_purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    invoice INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    base INTEGER NOT NULL,
                    tax INTEGER NOT NULL,
                    discount INTEGER NOT NULL,
                    igic TEXT NOT NULL,
                    inventory_id INTEGER NOT NULL,
                    -- Fin del alquiler antes de esta compra, si la compra lo renovó.
                    previous_expires_at REAL,
                    renewal INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    refunded INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (guild_id, invoice)
                );

                CREATE INDEX IF NOT EXISTS shop_purchases_member
                    ON shop_purchases (guild_id, user_id, item_id);

                CREATE TABLE IF NOT EXISTS shop_inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    role_id INTEGER,
                    serial INTEGER,
                    edition INTEGER,
                    multiplier INTEGER,
                    starts_at REAL NOT NULL,
                    expires_at REAL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'expired', 'refunded')),
                    equipped INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS shop_inventory_member
                    ON shop_inventory (guild_id, user_id, status);

                CREATE TABLE IF NOT EXISTS shop_seeded (
                    guild_id INTEGER NOT NULL,
                    catalog_key TEXT NOT NULL,
                    PRIMARY KEY (guild_id, catalog_key)
                );
                """
            )
            # Bases de datos de antes del surtido de serie: la columna se añade
            # vacía (los artículos de entonces son todos «de la casa»).
            for table in ("shop_items", "shop_inventory"):
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if "catalog_key" not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN catalog_key TEXT")
            connection.commit()
        finally:
            connection.close()

    # -- Catálogo --------------------------------------------------------------------

    async def create_item(
        self,
        guild_id: int,
        *,
        kind: Kind,
        name: str,
        emoji: str,
        description: str,
        price: int,
        igic: str,
        now: float,
        role_id: int | None = None,
        duration: int | None = None,
        multiplier: int | None = None,
        stock: int | None = None,
    ) -> ShopItem:
        """Añade un artículo al catálogo y lo devuelve."""
        return await self._run(
            self._create_sync,
            guild_id,
            kind,
            name,
            emoji,
            description,
            price,
            igic,
            now,
            role_id,
            duration,
            multiplier,
            stock,
        )

    def _create_sync(
        self,
        guild_id: int,
        kind: Kind,
        name: str,
        emoji: str,
        description: str,
        price: int,
        igic: str,
        now: float,
        role_id: int | None,
        duration: int | None,
        multiplier: int | None,
        stock: int | None,
    ) -> ShopItem:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO shop_items
                    (guild_id, kind, name, emoji, description, price, igic, role_id,
                     duration, multiplier, stock, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    kind.value,
                    name,
                    emoji,
                    description,
                    price,
                    igic,
                    role_id,
                    duration,
                    multiplier,
                    stock,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM shop_items WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        connection.close()
        return _item(row)

    async def item(self, guild_id: int, item_id: int) -> ShopItem | None:
        """Un artículo del servidor, o `None` si no existe."""
        return await self._run(self._item_sync, guild_id, item_id)

    def _item_sync(self, guild_id: int, item_id: int) -> ShopItem | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
            ).fetchone()
        finally:
            connection.close()
        return _item(row) if row else None

    async def items(self, guild_id: int, *, include_hidden: bool = False) -> list[ShopItem]:
        """Catálogo del servidor en el orden del escaparate."""
        return await self._run(self._items_sync, guild_id, include_hidden)

    def _items_sync(self, guild_id: int, include_hidden: bool) -> list[ShopItem]:
        where = "" if include_hidden else " AND visible = 1"
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM shop_items WHERE guild_id = ?{where} {_ITEM_ORDER}", (guild_id,)
            ).fetchall()
        finally:
            connection.close()
        return [_item(row) for row in rows]

    async def update_item(self, guild_id: int, item_id: int, **fields: object) -> ShopItem:
        """Cambia campos de un artículo y lo devuelve actualizado.

        Raises:
            ShopError: Si el artículo no existe o si se dejan menos
                existencias de las ya vendidas.
            ValueError: Si se pide cambiar un campo que no es editable.
        """
        unknown = set(fields) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"Campos no editables: {sorted(unknown)}")
        return await self._run(self._update_sync, guild_id, item_id, fields)

    def _update_sync(self, guild_id: int, item_id: int, fields: dict[str, object]) -> ShopItem:
        connection = self._connect()
        try:
            with connection:
                row = connection.execute(
                    "SELECT * FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
                ).fetchone()
                if row is None:
                    raise ShopError("Ese artículo ya no existe.")
                stock = fields.get("stock", row["stock"])
                if isinstance(stock, int) and stock < int(row["sold"]):
                    raise ShopError(
                        f"Ya se han vendido {row['sold']}: no puedes dejar menos existencias."
                    )
                if fields:
                    # Los nombres de columna salen de EDITABLE_FIELDS, nunca del usuario.
                    assignments = ", ".join(f"{column} = ?" for column in fields)
                    connection.execute(
                        f"UPDATE shop_items SET {assignments} WHERE guild_id = ? AND id = ?",
                        (*fields.values(), guild_id, item_id),
                    )
                row = connection.execute(
                    "SELECT * FROM shop_items WHERE id = ?", (item_id,)
                ).fetchone()
        finally:
            connection.close()
        return _item(row)

    async def delete_item(self, guild_id: int, item_id: int) -> bool:
        """Saca un artículo del catálogo. Lo ya comprado se queda en las mochilas."""
        return await self._run(self._delete_item_sync, guild_id, item_id)

    def _delete_item_sync(self, guild_id: int, item_id: int) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
                )
        finally:
            connection.close()
        return cursor.rowcount > 0

    async def best_seller(self, guild_id: int) -> int | None:
        """El artículo visible más vendido, si ha vendido lo bastante para presumir."""
        return await self._run(self._best_seller_sync, guild_id)

    def _best_seller_sync(self, guild_id: int) -> int | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT id FROM shop_items
                WHERE guild_id = ? AND visible = 1 AND sold >= ?
                ORDER BY sold DESC, id ASC LIMIT 1
                """,
                (guild_id, BEST_SELLER_MIN),
            ).fetchone()
        finally:
            connection.close()
        return int(row["id"]) if row else None

    async def stats(self, guild_id: int) -> ShopStats:
        """Ventas, facturación (base) e IGIC de la tienda del servidor."""
        return await self._run(self._stats_sync, guild_id)

    def _stats_sync(self, guild_id: int) -> ShopStats:
        connection = self._connect()
        try:
            sales, revenue, tax = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(base), 0), COALESCE(SUM(tax), 0)
                FROM shop_purchases WHERE guild_id = ? AND refunded = 0
                """,
                (guild_id,),
            ).fetchone()
        finally:
            connection.close()
        return ShopStats(int(sales), int(revenue), int(tax))

    # -- Venta -----------------------------------------------------------------------

    def reserve(
        self,
        guild_id: int,
        user_id: int,
        item_id: int,
        *,
        expected: Quote,
        level: int,
        now: float,
    ) -> Callable[[sqlite3.Connection], tuple[ShopItem, Sale]]:
        """Prepara el apunte de una venta para `EconomyRepository.purchase`.

        Dentro de la transacción del cobro vuelve a leer el artículo y lo
        comprueba todo otra vez: entre que se abre la caja y se paga, un
        administrador puede cambiar el precio o se puede agotar.

        Args:
            expected: Precio que vio el comprador en la caja.
            level: Nivel actual del comprador.

        Returns:
            Una función que recibe la conexión de la transacción y devuelve
            `(artículo, venta)`, o lanza `ShopError` y se deshace todo.
        """

        def run(connection: sqlite3.Connection) -> tuple[ShopItem, Sale]:
            return self._reserve_in(connection, guild_id, user_id, item_id, expected, level, now)

        return run

    def _reserve_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        item_id: int,
        expected: Quote,
        level: int,
        now: float,
    ) -> tuple[ShopItem, Sale]:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
        ).fetchone()
        if row is None:
            raise ShopError("Ese artículo ya no existe.")
        item = _item(row)
        current = quote(item, now)
        if (current.base, current.tax) != (expected.base, expected.tax):
            raise ShopError(
                "El precio ha cambiado mientras mirabas. Vuelve a abrir la caja para ver el nuevo."
            )
        (bought,) = connection.execute(
            """
            SELECT COUNT(*) FROM shop_purchases
            WHERE guild_id = ? AND user_id = ? AND item_id = ? AND refunded = 0
            """,
            (guild_id, user_id, item_id),
        ).fetchone()
        owns_role = False
        if item.kind is Kind.ROLE:
            owns_role = (
                connection.execute(
                    """
                    SELECT 1 FROM shop_inventory
                    WHERE guild_id = ? AND user_id = ? AND role_id = ? AND status = 'active'
                      AND expires_at IS NULL
                    """,
                    (guild_id, user_id, item.role_id),
                ).fetchone()
                is not None
            )
        reason = ineligibility(item, now=now, level=level, bought=int(bought), owns_role=owns_role)
        if reason is not None:
            raise ShopError(reason)

        serial = item.sold + 1 if item.stock is not None else None
        connection.execute("UPDATE shop_items SET sold = sold + 1 WHERE id = ?", (item.id,))
        (invoice,) = connection.execute(
            "SELECT COALESCE(MAX(invoice), 0) + 1 FROM shop_purchases WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()

        starts_at, expires_at = now, None
        inventory_id: int | None = None
        previous_end: float | None = None
        if item.kind is Kind.ROLE and item.duration is not None:
            rental = connection.execute(
                """
                SELECT id, expires_at FROM shop_inventory
                WHERE guild_id = ? AND user_id = ? AND role_id = ? AND status = 'active'
                  AND expires_at IS NOT NULL
                ORDER BY expires_at DESC LIMIT 1
                """,
                (guild_id, user_id, item.role_id),
            ).fetchone()
            if rental is not None:
                inventory_id = int(rental["id"])
                previous_end = float(rental["expires_at"])
                expires_at = rental_end(now, previous_end, item.duration)
                connection.execute(
                    "UPDATE shop_inventory SET expires_at = ? WHERE id = ?",
                    (expires_at, inventory_id),
                )
            else:
                expires_at = now + item.duration
        elif item.kind is Kind.BOOST:
            (queue_end,) = connection.execute(
                """
                SELECT MAX(expires_at) FROM shop_inventory
                WHERE guild_id = ? AND user_id = ? AND kind = 'xp' AND status = 'active'
                  AND expires_at > ?
                """,
                (guild_id, user_id, now),
            ).fetchone()
            starts_at, expires_at = boost_window(now, queue_end, item.duration or 3600)

        if inventory_id is None:
            cursor = connection.execute(
                """
                INSERT INTO shop_inventory
                    (guild_id, user_id, item_id, kind, name, emoji, role_id, serial, edition,
                     multiplier, starts_at, expires_at, catalog_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    item.id,
                    item.kind.value,
                    item.name,
                    item.emoji,
                    item.role_id,
                    serial,
                    item.stock if serial is not None else None,
                    item.multiplier,
                    starts_at,
                    expires_at,
                    item.catalog_key,
                ),
            )
            inventory_id = int(cursor.lastrowid or 0)

        cursor = connection.execute(
            """
            INSERT INTO shop_purchases
                (guild_id, invoice, user_id, item_id, item_name, kind, base, tax, discount, igic,
                 inventory_id, previous_expires_at, renewal, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                invoice,
                user_id,
                item.id,
                item.name,
                item.kind.value,
                current.base,
                current.tax,
                current.discount,
                current.rate.key,
                inventory_id,
                previous_end,
                int(previous_end is not None),
                now,
            ),
        )
        (collection,) = connection.execute(
            """
            SELECT COUNT(DISTINCT item_id) FROM shop_inventory
            WHERE guild_id = ? AND user_id = ? AND kind = 'objeto' AND status = 'active'
            """,
            (guild_id, user_id),
        ).fetchone()
        sale = Sale(
            purchase_id=int(cursor.lastrowid or 0),
            invoice=int(invoice),
            serial=serial,
            edition=item.stock if serial is not None else None,
            starts_at=starts_at,
            expires_at=expires_at,
            renewed=previous_end is not None,
            last_unit=item.stock is not None and serial == item.stock,
            collection=int(collection),
        )
        return item, sale

    def release(self, purchase_id: int) -> Callable[[sqlite3.Connection], None]:
        """Prepara la anulación de una venta para `EconomyRepository.refund_purchase`.

        Marca la venta como devuelta, repone la unidad y quita lo entregado
        (o devuelve el alquiler a como estaba si la venta lo renovó).
        """

        def run(connection: sqlite3.Connection) -> None:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM shop_purchases WHERE id = ? AND refunded = 0", (purchase_id,)
            ).fetchone()
            if row is None:
                raise ShopError("Esa compra ya estaba devuelta.")
            connection.execute(
                "UPDATE shop_purchases SET refunded = 1 WHERE id = ?", (purchase_id,)
            )
            connection.execute(
                "UPDATE shop_items SET sold = MAX(sold - 1, 0) WHERE id = ?", (row["item_id"],)
            )
            if row["renewal"]:
                connection.execute(
                    "UPDATE shop_inventory SET expires_at = ? WHERE id = ?",
                    (row["previous_expires_at"], row["inventory_id"]),
                )
            else:
                connection.execute(
                    "UPDATE shop_inventory SET status = 'refunded' WHERE id = ?",
                    (row["inventory_id"],),
                )

        return run

    # -- Inventario ------------------------------------------------------------------

    async def inventory(self, guild_id: int, user_id: int, now: float) -> list[InventoryEntry]:
        """Lo que tiene un miembro y sigue en vigor, de lo más nuevo a lo más viejo."""
        return await self._run(self._inventory_sync, guild_id, user_id, now)

    def _inventory_sync(self, guild_id: int, user_id: int, now: float) -> list[InventoryEntry]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM shop_inventory
                WHERE guild_id = ? AND user_id = ? AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY id DESC
                """,
                (guild_id, user_id, now),
            ).fetchall()
        finally:
            connection.close()
        return [_entry(row) for row in rows]

    async def set_equipped(
        self, guild_id: int, user_id: int, entry_id: int, equipped: bool
    ) -> InventoryEntry | None:
        """Marca un rol para siempre como puesto o quitado. `None` si no es suyo."""
        return await self._run(self._set_equipped_sync, guild_id, user_id, entry_id, equipped)

    def _set_equipped_sync(
        self, guild_id: int, user_id: int, entry_id: int, equipped: bool
    ) -> InventoryEntry | None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE shop_inventory SET equipped = ?
                    WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'active'
                    """,
                    (int(equipped), entry_id, guild_id, user_id),
                )
                row = connection.execute(
                    """
                    SELECT * FROM shop_inventory
                    WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'active'
                    """,
                    (entry_id, guild_id, user_id),
                ).fetchone()
        finally:
            connection.close()
        return _entry(row) if row else None

    async def active_boosts(self, now: float) -> list[tuple[int, int, float, float, int]]:
        """Potenciadores que aún no han acabado: `(servidor, miembro, inicio, fin, %)`."""
        return await self._run(self._active_boosts_sync, now)

    def _active_boosts_sync(self, now: float) -> list[tuple[int, int, float, float, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT guild_id, user_id, starts_at, expires_at, multiplier FROM shop_inventory
                WHERE kind = 'xp' AND status = 'active' AND expires_at > ?
                """,
                (now,),
            ).fetchall()
        finally:
            connection.close()
        return [
            (
                int(r["guild_id"]),
                int(r["user_id"]),
                float(r["starts_at"]),
                float(r["expires_at"]),
                int(r["multiplier"] or 100),
            )
            for r in rows
        ]

    async def due_rentals(self, now: float) -> list[tuple[int, int, int, int | None]]:
        """Roles alquilados que ya han vencido: `(fila, servidor, miembro, rol)`."""
        return await self._run(self._due_rentals_sync, now)

    def _due_rentals_sync(self, now: float) -> list[tuple[int, int, int, int | None]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT id, guild_id, user_id, role_id FROM shop_inventory
                WHERE kind = 'rol' AND status = 'active' AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (now,),
            ).fetchall()
        finally:
            connection.close()
        return [(int(r["id"]), int(r["guild_id"]), int(r["user_id"]), r["role_id"]) for r in rows]

    async def mark_expired(self, entry_ids: list[int]) -> None:
        """Da por vencidas filas del inventario (alquileres que ya se han quitado)."""
        if entry_ids:
            await self._run(self._mark_expired_sync, entry_ids)

    def _mark_expired_sync(self, entry_ids: list[int]) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.executemany(
                    "UPDATE shop_inventory SET status = 'expired' WHERE id = ?",
                    [(entry_id,) for entry_id in entry_ids],
                )
        finally:
            connection.close()

    # -- Surtido de serie ------------------------------------------------------------

    async def stock_catalog(
        self, guild_id: int, entries: Sequence[CatalogEntry], now: float, *, restock: bool = False
    ) -> int:
        """Mete en el catálogo del servidor los artículos de serie que falten.

        Sin `restock`, solo mete los que nunca se han metido en ese servidor:
        lo que un administrador haya retirado no vuelve. Con `restock` (botón
        «Reponer surtido» de la trastienda) vuelve a meter también los
        retirados, pero nunca duplica uno que siga en el catálogo, aunque esté
        oculto.

        Returns:
            Cuántos artículos ha añadido.
        """
        return await self._run(self._stock_sync, guild_id, list(entries), now, restock)

    def _stock_sync(
        self, guild_id: int, entries: list[CatalogEntry], now: float, restock: bool
    ) -> int:
        connection = self._connect()
        try:
            with connection:
                seeded = {
                    row[0]
                    for row in connection.execute(
                        "SELECT catalog_key FROM shop_seeded WHERE guild_id = ?", (guild_id,)
                    )
                }
                present = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT catalog_key FROM shop_items
                        WHERE guild_id = ? AND catalog_key IS NOT NULL
                        """,
                        (guild_id,),
                    )
                }
                skip = present if restock else seeded | present
                added = 0
                for entry in entries:
                    if entry.key in skip:
                        continue
                    connection.execute(
                        """
                        INSERT INTO shop_items
                            (guild_id, kind, name, emoji, description, price, igic, duration,
                             multiplier, stock, per_user, min_level, created_at, catalog_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            guild_id,
                            entry.kind.value,
                            entry.name,
                            entry.emoji,
                            entry.description,
                            entry.price,
                            entry.igic,
                            entry.duration,
                            entry.multiplier,
                            entry.stock,
                            entry.per_user,
                            entry.min_level,
                            now,
                            entry.key,
                        ),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO shop_seeded (guild_id, catalog_key) VALUES (?, ?)",
                        (guild_id, entry.key),
                    )
                    added += 1
        finally:
            connection.close()
        return added

    # -- Usar objetos ----------------------------------------------------------------

    async def consume(self, guild_id: int, user_id: int, entry_id: int) -> bool:
        """Gasta un objeto de la mochila. `False` si ya no estaba (doble clic, otra pestaña)."""
        return await self._run(self._consume_sync, guild_id, user_id, entry_id)

    def _consume_sync(self, guild_id: int, user_id: int, entry_id: int) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE shop_inventory SET status = 'expired'
                    WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'active'
                      AND kind = 'objeto'
                    """,
                    (entry_id, guild_id, user_id),
                )
        finally:
            connection.close()
        return cursor.rowcount > 0

    async def restore(self, entry_id: int) -> None:
        """Devuelve a la mochila un objeto gastado (si el uso no se pudo completar)."""
        await self._run(self._restore_sync, entry_id)

    def _restore_sync(self, entry_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE shop_inventory SET status = 'active'
                    WHERE id = ? AND status = 'expired'
                    """,
                    (entry_id,),
                )
        finally:
            connection.close()

    async def grant(
        self, guild_id: int, user_id: int, item: ShopItem, now: float
    ) -> tuple[InventoryEntry, bool]:
        """Mete un objeto en la mochila sin venta (premio de la caja botín).

        No cuenta como unidad vendida ni lleva número de serie: la caja solo
        da artículos de existencias ilimitadas.

        Returns:
            `(entrada, repe)`: `repe` si ya tenía uno igual.
        """
        return await self._run(self._grant_sync, guild_id, user_id, item, now)

    def _grant_sync(
        self, guild_id: int, user_id: int, item: ShopItem, now: float
    ) -> tuple[InventoryEntry, bool]:
        connection = self._connect()
        try:
            with connection:
                dupe = (
                    connection.execute(
                        """
                        SELECT 1 FROM shop_inventory
                        WHERE guild_id = ? AND user_id = ? AND item_id = ? AND status = 'active'
                        """,
                        (guild_id, user_id, item.id),
                    ).fetchone()
                    is not None
                )
                cursor = connection.execute(
                    """
                    INSERT INTO shop_inventory
                        (guild_id, user_id, item_id, kind, name, emoji, multiplier, starts_at,
                         catalog_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        user_id,
                        item.id,
                        item.kind.value,
                        item.name,
                        item.emoji,
                        item.multiplier,
                        now,
                        item.catalog_key,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM shop_inventory WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
        finally:
            connection.close()
        return _entry(row), dupe

    async def collection_size(self, guild_id: int, user_id: int) -> int:
        """Objetos distintos que tiene un miembro (para los logros de colección)."""
        return await self._run(self._collection_sync, guild_id, user_id)

    def _collection_sync(self, guild_id: int, user_id: int) -> int:
        connection = self._connect()
        try:
            (count,) = connection.execute(
                """
                SELECT COUNT(DISTINCT item_id) FROM shop_inventory
                WHERE guild_id = ? AND user_id = ? AND kind = 'objeto' AND status = 'active'
                """,
                (guild_id, user_id),
            ).fetchone()
        finally:
            connection.close()
        return int(count)

    # -- Limpieza --------------------------------------------------------------------

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra el catálogo, las ventas, los inventarios y el surtido de un servidor."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                for table in ("shop_items", "shop_purchases", "shop_inventory", "shop_seeded"):
                    # `table` sale de una tupla fija, nunca de entrada del usuario.
                    connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        finally:
            connection.close()
