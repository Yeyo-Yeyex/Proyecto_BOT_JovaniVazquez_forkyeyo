"""Persistencia del Monte de Piedad (`banco`): papeletas de empeño y lo que se le ha vendido.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `monte_tickets`: cada papeleta, con el objeto (copia de nombre y emoji para enseñarlo),
  lo que se pagó por él en la tienda, lo prestado, lo que hay que devolver y hasta cuándo.
  Se abre (`open`) y acaba desempeñada (`redeemed`) o perdida (`forfeited`).
- `monte_sales`: cada venta al Monte, con lo pagado en la tienda y lo cobrado.

Lo empeñado y lo vendido queda en `shop_inventory` a nombre de `PAWN_ACCOUNT_ID`, como lo
que está en un puesto del Rastro: no se usa, no se luce y no cuenta en el patrimonio de
nadie. Lo que se pierde se queda ahí.

El dinero no se toca aquí. Lo que mueve dinero (`sell`, `pawn`, `redeem`) devuelve una
función que corre dentro de la transacción de `EconomyRepository.monte`: el objeto, la
papeleta y el dinero se guardan juntos o no se guarda nada. Perder una papeleta vencida
no mueve dinero (`forfeit_due`).

Solo se guardan IDs, nombres de artículos, importes y marcas de tiempo. Todo se borra
cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.repositories.economy import PAWN_ACCOUNT_ID, MonteMove
from bot.repositories.shop import InventoryEntry, _entry, move_entry_in
from bot.services.monte import MAX_TICKETS, PAWN_SECONDS, MonteError, Offer, offer, refusal
from bot.services.shop import Kind

T = TypeVar("T")

#: Lo que se pagó en la tienda por cada unidad (sin IGIC, con su rebaja) y si su artículo
#: es de edición limitada. Lo comprado en el Rastro conserva la venta original de la
#: tienda, sea de quien sea: el Monte devuelve lo que se gastó en la tienda, no lo que se
#: pagaron los miembros entre ellos.
_APPRAISAL_SQL = """
    SELECT i.*,
        (SELECT SUM(p.base) FROM shop_purchases p
         WHERE p.guild_id = i.guild_id AND p.inventory_id = i.id AND p.refunded = 0) AS paid,
        (SELECT it.stock FROM shop_items it
         WHERE it.guild_id = i.guild_id AND it.id = i.item_id) AS stock
    FROM shop_inventory i
"""


@dataclass(frozen=True, slots=True)
class Appraisal:
    """Un objeto de la mochila y lo que da el Monte por él.

    Attributes:
        entry: El objeto.
        offer: Lo que paga y presta, si lo acepta.
        refusal: Por qué no lo acepta, si no lo acepta.
    """

    entry: InventoryEntry
    offer: Offer | None
    refusal: str | None


@dataclass(frozen=True, slots=True)
class Ticket:
    """Una papeleta de empeño.

    Attributes:
        paid: Lo que se pagó por el objeto en la tienda.
        loan: Lo prestado.
        repay: Lo que hay que devolver para desempeñarlo.
        due_at: Hasta cuándo se puede desempeñar.
        status: `open`, `redeemed` o `forfeited`.
    """

    id: int
    guild_id: int
    user_id: int
    entry_id: int
    item_id: int
    name: str
    emoji: str
    catalog_key: str | None
    paid: int
    loan: int
    repay: int
    created_at: float
    due_at: float
    status: str

    @property
    def label(self) -> str:
        """`🎻 Guitarra española`."""
        return f"{self.emoji} {self.name}"


@dataclass(frozen=True, slots=True)
class MonteSale:
    """Una venta al Monte ya hecha.

    Attributes:
        paid: Lo que se pagó por el objeto en la tienda.
        amount: Lo que ha pagado el Monte (antes del embargo, si lo hay).
    """

    user_id: int
    name: str
    emoji: str
    catalog_key: str | None
    paid: int
    amount: int

    @property
    def label(self) -> str:
        """`🎻 Guitarra española`."""
        return f"{self.emoji} {self.name}"


@dataclass(frozen=True, slots=True)
class MonteStats:
    """Lo que ha hecho el Monte en un servidor, para las cuentas de `banco`.

    Attributes:
        sales: Objetos que ha comprado.
        bought: Lo que ha pagado por ellos.
        pawned: Papeletas abiertas.
        lent: Lo prestado que sigue pendiente.
        redeemed: Papeletas desempeñadas.
        interest: Intereses cobrados al desempeñar.
        forfeited: Papeletas perdidas.
        forfeited_lent: Lo prestado por ellas, que no vuelve.
    """

    sales: int = 0
    bought: int = 0
    pawned: int = 0
    lent: int = 0
    redeemed: int = 0
    interest: int = 0
    forfeited: int = 0
    forfeited_lent: int = 0


def _ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        entry_id=int(row["entry_id"]),
        item_id=int(row["item_id"]),
        name=row["name"],
        emoji=row["emoji"],
        catalog_key=row["catalog_key"],
        paid=int(row["paid"]),
        loan=int(row["loan"]),
        repay=int(row["repay"]),
        created_at=float(row["created_at"]),
        due_at=float(row["due_at"]),
        status=row["status"],
    )


def _appraisal(row: sqlite3.Row) -> Appraisal:
    paid = int(row["paid"] or 0)
    entry = _entry(row)
    reason = refusal(
        entry.kind,
        entry.catalog_key,
        numbered=entry.serial is not None or row["stock"] is not None,
        paid=paid,
    )
    return Appraisal(entry, None if reason else offer(paid), reason)


class MonteRepository:
    """Acceso SQLite al Monte de Piedad; comparte archivo con la tienda y la economía."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas del Monte si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS monte_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    entry_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    catalog_key TEXT,
                    paid INTEGER NOT NULL CHECK (paid > 0),
                    loan INTEGER NOT NULL CHECK (loan >= 0),
                    repay INTEGER NOT NULL CHECK (repay >= loan),
                    created_at REAL NOT NULL,
                    due_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'redeemed', 'forfeited')),
                    closed_at REAL
                );

                CREATE INDEX IF NOT EXISTS monte_tickets_open
                    ON monte_tickets (status, guild_id);

                CREATE TABLE IF NOT EXISTS monte_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    entry_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    catalog_key TEXT,
                    paid INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
        finally:
            connection.close()

    # -- Lecturas --------------------------------------------------------------------

    async def appraisals(self, guild_id: int, user_id: int, now: float) -> list[Appraisal]:
        """Los coleccionables y tapetes de la mochila con lo que da el Monte por cada uno.

        Por orden de nombre: primero los que acepta, luego los que no.
        """
        return await self._run(self._appraisals_sync, guild_id, user_id, now)

    def _appraisals_sync(self, guild_id: int, user_id: int, now: float) -> list[Appraisal]:
        connection = self._connect()
        try:
            rows = connection.execute(
                _APPRAISAL_SQL
                + """
                WHERE i.guild_id = ? AND i.user_id = ? AND i.kind = ? AND i.status = 'active'
                  AND (i.expires_at IS NULL OR i.expires_at > ?)
                """,
                (guild_id, user_id, Kind.TROPHY.value, now),
            ).fetchall()
        finally:
            connection.close()
        found = [_appraisal(row) for row in rows]
        return sorted(found, key=lambda a: (a.offer is None, a.entry.name.casefold(), a.entry.id))

    async def open_tickets(self) -> list[Ticket]:
        """Papeletas abiertas de todos los servidores (para la caché del cog)."""
        return await self._run(self._open_tickets_sync)

    def _open_tickets_sync(self) -> list[Ticket]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM monte_tickets WHERE status = 'open' ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        return [_ticket(row) for row in rows]

    async def stats(self, guild_id: int) -> MonteStats:
        """Lo que ha hecho el Monte en un servidor desde el primer día."""
        return await self._run(self._stats_sync, guild_id)

    def _stats_sync(self, guild_id: int) -> MonteStats:
        connection = self._connect()
        try:
            sales, bought = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM monte_sales WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            totals = {
                str(status): (int(count), int(lent or 0), int(due or 0))
                for status, count, lent, due in connection.execute(
                    """
                    SELECT status, COUNT(*), SUM(loan), SUM(repay) FROM monte_tickets
                    WHERE guild_id = ? GROUP BY status
                    """,
                    (guild_id,),
                )
            }
        finally:
            connection.close()
        pawned, lent, _ = totals.get("open", (0, 0, 0))
        redeemed, redeemed_lent, redeemed_due = totals.get("redeemed", (0, 0, 0))
        forfeited, forfeited_lent, _ = totals.get("forfeited", (0, 0, 0))
        return MonteStats(
            sales=int(sales),
            bought=int(bought),
            pawned=pawned,
            lent=lent,
            redeemed=redeemed,
            interest=redeemed_due - redeemed_lent,
            forfeited=forfeited,
            forfeited_lent=forfeited_lent,
        )

    # -- Lo que mueve dinero (corre en `EconomyRepository.monte`) ----------------------

    def sell(
        self, guild_id: int, user_id: int, entry_id: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[MonteSale, MonteMove]]:
        """Vende un objeto al Monte: pasa a guardarlo el Monte y la caja paga `SELL_SHARE`.

        Raises (al correr):
            MonteError: Si ya no es suyo o el Monte no lo acepta.
        """

        def run(connection: sqlite3.Connection) -> tuple[MonteSale, MonteMove]:
            row, deal = _acceptable_in(connection, guild_id, user_id, entry_id)
            _hand_over_in(connection, guild_id, user_id, entry_id)
            connection.execute(
                """
                INSERT INTO monte_sales
                    (guild_id, user_id, entry_id, item_id, name, emoji, catalog_key, paid,
                     amount, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id, user_id, entry_id, row["item_id"], row["name"], row["emoji"],
                    row["catalog_key"], deal.paid, deal.sale, now,
                ),
            )  # fmt: skip
            sale = MonteSale(
                user_id=user_id,
                name=row["name"],
                emoji=row["emoji"],
                catalog_key=row["catalog_key"],
                paid=deal.paid,
                amount=deal.sale,
            )
            move = MonteMove(user_id, deal.sale, "monte:venta", "monte:compra", garnish=True)
            return sale, move

        return run

    def pawn(
        self, guild_id: int, user_id: int, entry_id: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[Ticket, MonteMove]]:
        """Empeña un objeto: lo guarda el Monte, la caja presta `PAWN_SHARE` y abre papeleta.

        Raises (al correr):
            MonteError: Si ya no es suyo, el Monte no lo acepta o ya tiene `MAX_TICKETS`.
        """

        def run(connection: sqlite3.Connection) -> tuple[Ticket, MonteMove]:
            (open_count,) = connection.execute(
                """
                SELECT COUNT(*) FROM monte_tickets
                WHERE guild_id = ? AND user_id = ? AND status = 'open'
                """,
                (guild_id, user_id),
            ).fetchone()
            if open_count >= MAX_TICKETS:
                raise MonteError(
                    f"Ya tienes {MAX_TICKETS} papeletas abiertas. Desempeña algo antes de "
                    "traer más."
                )
            row, deal = _acceptable_in(connection, guild_id, user_id, entry_id)
            _hand_over_in(connection, guild_id, user_id, entry_id)
            cursor = connection.execute(
                """
                INSERT INTO monte_tickets
                    (guild_id, user_id, entry_id, item_id, name, emoji, catalog_key, paid,
                     loan, repay, created_at, due_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id, user_id, entry_id, row["item_id"], row["name"], row["emoji"],
                    row["catalog_key"], deal.paid, deal.loan, deal.repay, now,
                    now + PAWN_SECONDS,
                ),
            )  # fmt: skip
            ticket = _ticket_in(connection, int(cursor.lastrowid or 0))
            return ticket, MonteMove(user_id, deal.loan, "monte:empeno", "monte:prestamo")

        return run

    def redeem(
        self, guild_id: int, user_id: int, ticket_id: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[Ticket, MonteMove]]:
        """Desempeña una papeleta: devuelve el préstamo con su interés y el objeto vuelve.

        Raises (al correr):
            MonteError: Si la papeleta no es suya, ya está cerrada o ha vencido.
        """

        def run(connection: sqlite3.Connection) -> tuple[Ticket, MonteMove]:
            row = connection.execute(
                "SELECT * FROM monte_tickets WHERE id = ? AND guild_id = ?",
                (ticket_id, guild_id),
            ).fetchone()
            if row is None or int(row["user_id"]) != user_id:
                raise MonteError("Esa papeleta no es tuya.")
            ticket = _ticket(row)
            if ticket.status != "open" or now >= ticket.due_at:
                raise MonteError("Esa papeleta ya no está abierta: el objeto es del Monte.")
            if (
                move_entry_in(connection, guild_id, ticket.entry_id, PAWN_ACCOUNT_ID, user_id)
                is None
            ):
                raise MonteError("El objeto de esa papeleta se ha perdido por el camino.")
            connection.execute(
                "UPDATE monte_tickets SET status = 'redeemed', closed_at = ? WHERE id = ?",
                (now, ticket.id),
            )
            closed = _ticket_in(connection, ticket.id)
            return closed, MonteMove(user_id, -ticket.repay, "monte:desempeno", "monte:cobro")

        return run

    # -- Sin dinero ------------------------------------------------------------------

    async def forfeit_due(self, now: float) -> list[Ticket]:
        """Cierra las papeletas vencidas: el objeto se queda en el Monte. Devuelve cuáles."""
        return await self._run(self._forfeit_due_sync, now)

    def _forfeit_due_sync(self, now: float) -> list[Ticket]:
        connection = self._connect()
        try:
            with connection:
                rows = connection.execute(
                    "SELECT * FROM monte_tickets WHERE status = 'open' AND due_at <= ? ORDER BY id",
                    (now,),
                ).fetchall()
                for row in rows:
                    connection.execute(
                        "UPDATE monte_tickets SET status = 'forfeited', closed_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
                return [_ticket_in(connection, int(row["id"])) for row in rows]
        finally:
            connection.close()

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra las papeletas y ventas del Monte de un servidor."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                for table in ("monte_tickets", "monte_sales"):
                    # `table` sale de una tupla fija, nunca de entrada del usuario.
                    connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        finally:
            connection.close()


# -- Piezas que corren dentro de una transacción abierta ---------------------------------


def _ticket_in(connection: sqlite3.Connection, ticket_id: int) -> Ticket:
    row = connection.execute("SELECT * FROM monte_tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _ticket(row)


def _acceptable_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, entry_id: int
) -> tuple[sqlite3.Row, Offer]:
    """El objeto de la mochila y lo que da el Monte, si es de `user_id` y lo acepta."""
    row = connection.execute(
        _APPRAISAL_SQL + " WHERE i.id = ? AND i.guild_id = ? AND i.user_id = ? "
        "AND i.status = 'active'",
        (entry_id, guild_id, user_id),
    ).fetchone()
    if row is None:
        raise MonteError("Ese objeto ya no está en tu mochila.")
    found = _appraisal(row)
    if found.offer is None:
        raise MonteError(found.refusal or "El Monte no acepta eso.")
    return row, found.offer


def _hand_over_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, entry_id: int
) -> None:
    if move_entry_in(connection, guild_id, entry_id, user_id, PAWN_ACCOUNT_ID) is None:
        raise MonteError("Ese objeto ya no está en tu mochila.")
