"""Persistencia de las loterías: sorteos, boletos, botes y rascas.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `lottery_draws`: un sorteo de un juego en un servidor. Se crea con la
  primera apuesta; un sorteo sin apuestas no existe y no se celebra. Guarda
  lo recaudado, la combinación ganadora y el reparto, en JSON.
- `lottery_tickets`: una fila por apuesta (o por compra de décimos de un
  mismo número), con lo que ha cobrado y el gravamen.
- `lottery_pots`: el bote acumulado de cada juego de bote por servidor.
- `lottery_scratches`: cada rasca, con su premio.

El dinero no se toca aquí: las funciones que devuelven un `hook` corren dentro
de la transacción de `EconomyRepository.lottery`, así que el cobro y los
boletos (o el cierre del sorteo y sus premios) se guardan juntos o nada.

Solo se guardan IDs, apuestas e importes. Todo se borra cuando el bot sale
del servidor.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.services.lottery import MAX_PER_DRAW, LotteryError

T = TypeVar("T")


class DrawChanged(Exception):
    """El sorteo ya estaba cerrado o entró una apuesta mientras se repartía."""


@dataclass(frozen=True, slots=True)
class Draw:
    """Un sorteo con apuestas.

    Attributes:
        status: `"open"` hasta que se celebra, luego `"drawn"`.
        sales: Recaudación en Y$.
        tickets: Filas de boletos (apuestas o compras de décimos).
        channel_id: Canal donde se compró por última vez: ahí se anuncia.
        result: Combinación ganadora (JSON ya leído), si se ha celebrado.
        summary: Reparto (JSON ya leído), si se ha celebrado.
    """

    id: int
    guild_id: int
    game: str
    draw_at: float
    status: str
    sales: int
    tickets: int
    channel_id: int | None
    result: dict | None = None
    summary: dict | None = None


@dataclass(frozen=True, slots=True)
class Ticket:
    """Una apuesta o una compra de décimos de un número.

    Attributes:
        pick: Apuesta codificada (`Pick.encode`).
        quantity: Décimos (1 en los juegos de bote).
        prize: Premio bruto, una vez celebrado.
        tax: Gravamen especial pagado.
        detail: Qué ha ganado, en texto.
    """

    id: int
    draw_id: int
    user_id: int
    pick: str
    quantity: int
    cost: int
    prize: int = 0
    tax: int = 0
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TicketRecord:
    """Un boleto con su juego y su sorteo, para el historial de un miembro."""

    game: str
    draw_at: float
    status: str
    pick: str
    quantity: int
    cost: int
    prize: int
    tax: int
    detail: str | None


@dataclass(frozen=True, slots=True)
class Totals:
    """Lo vendido y lo repartido en loterías en un servidor."""

    sold: int
    paid: int
    winners: int


def _draw(row: sqlite3.Row) -> Draw:
    return Draw(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        game=str(row["game"]),
        draw_at=float(row["draw_at"]),
        status=str(row["status"]),
        sales=int(row["sales"]),
        tickets=int(row["tickets"]),
        channel_id=int(row["channel_id"]) if row["channel_id"] is not None else None,
        result=json.loads(row["result"]) if row["result"] else None,
        summary=json.loads(row["summary"]) if row["summary"] else None,
    )


class LotteryRepository:
    """Acceso SQLite a las loterías.

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

    def _read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
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
                CREATE TABLE IF NOT EXISTS lottery_draws (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    draw_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'drawn')),
                    sales INTEGER NOT NULL DEFAULT 0,
                    tickets INTEGER NOT NULL DEFAULT 0,
                    channel_id INTEGER,
                    result TEXT,
                    summary TEXT,
                    drawn_at REAL,
                    UNIQUE (guild_id, game, draw_at)
                );

                CREATE INDEX IF NOT EXISTS lottery_draws_due ON lottery_draws (status, draw_at);

                CREATE TABLE IF NOT EXISTS lottery_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draw_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    pick TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    cost INTEGER NOT NULL,
                    prize INTEGER NOT NULL DEFAULT 0,
                    tax INTEGER NOT NULL DEFAULT 0,
                    detail TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS lottery_tickets_draw ON lottery_tickets (draw_id);
                CREATE INDEX IF NOT EXISTS lottery_tickets_member
                    ON lottery_tickets (guild_id, user_id, id);

                CREATE TABLE IF NOT EXISTS lottery_pots (
                    guild_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    carry INTEGER NOT NULL DEFAULT 0 CHECK (carry >= 0),
                    PRIMARY KEY (guild_id, game)
                );

                CREATE TABLE IF NOT EXISTS lottery_scratches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    cost INTEGER NOT NULL,
                    prize INTEGER NOT NULL,
                    tax INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
        finally:
            connection.close()

    # -- Compras (corren dentro de la transacción del cobro) ---------------------------

    def reserve(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str,
        draw_at: float,
        picks: Sequence[tuple[str, int]],
        price: int,
        channel_id: int | None,
        now: float,
    ) -> Callable[[sqlite3.Connection], tuple[int, int]]:
        """Apunta apuestas en el sorteo, creándolo si es la primera.

        Args:
            picks: `(apuesta codificada, décimos)`.
            price: Precio de un décimo o apuesta.

        Returns:
            Función para `EconomyRepository.lottery` que devuelve
            `(id del sorteo, apuestas o décimos del miembro en él tras comprar)`
            o lanza `LotteryError` (y no se cobra nada).
        """

        def run(connection: sqlite3.Connection) -> tuple[int, int]:
            if now >= draw_at:
                raise LotteryError("Ese sorteo ya está cerrado. Mira el siguiente.")
            connection.execute(
                "INSERT OR IGNORE INTO lottery_draws (guild_id, game, draw_at) VALUES (?, ?, ?)",
                (guild_id, game, draw_at),
            )
            row = connection.execute(
                "SELECT id, status FROM lottery_draws WHERE guild_id = ? AND game = ? "
                "AND draw_at = ?",
                (guild_id, game, draw_at),
            ).fetchone()
            if row["status"] != "open":
                raise LotteryError("Ese sorteo ya está cerrado. Mira el siguiente.")
            draw_id = int(row["id"])
            (owned,) = connection.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM lottery_tickets "
                "WHERE draw_id = ? AND user_id = ?",
                (draw_id, user_id),
            ).fetchone()
            wanted = sum(quantity for _, quantity in picks)
            if owned + wanted > MAX_PER_DRAW:
                raise LotteryError(
                    f"Máximo {MAX_PER_DRAW} por persona y sorteo, y ya llevas {owned}."
                )
            connection.executemany(
                """
                INSERT INTO lottery_tickets
                    (draw_id, guild_id, user_id, pick, quantity, cost, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (draw_id, guild_id, user_id, pick, quantity, price * quantity, now)
                    for pick, quantity in picks
                ],
            )
            connection.execute(
                "UPDATE lottery_draws SET sales = sales + ?, tickets = tickets + ?, "
                "channel_id = COALESCE(?, channel_id) WHERE id = ?",
                (price * wanted, len(picks), channel_id, draw_id),
            )
            return draw_id, int(owned) + wanted

        return run

    def record_scratch(
        self, guild_id: int, user_id: int, *, game: str, cost: int, prize: int, tax: int, now: float
    ) -> Callable[[sqlite3.Connection], int]:
        """Apunta un rasca; devuelve cuántos ha rascado ya el miembro."""

        def run(connection: sqlite3.Connection) -> int:
            connection.execute(
                """
                INSERT INTO lottery_scratches
                    (guild_id, user_id, game, cost, prize, tax, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, game, cost, prize, tax, now),
            )
            (count,) = connection.execute(
                "SELECT COUNT(*) FROM lottery_scratches WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return int(count)

        return run

    # -- Sorteos ------------------------------------------------------------------------

    async def due_draws(self, now: float) -> list[Draw]:
        """Sorteos abiertos cuya hora ya ha llegado, de todos los servidores."""

        def read(connection: sqlite3.Connection) -> list[Draw]:
            rows = connection.execute(
                "SELECT * FROM lottery_draws WHERE status = 'open' AND draw_at <= ? "
                "ORDER BY draw_at, id",
                (now,),
            ).fetchall()
            return [_draw(r) for r in rows]

        return await self._run(self._read, read)

    async def tickets(self, draw_id: int) -> list[Ticket]:
        """Boletos de un sorteo."""

        def read(connection: sqlite3.Connection) -> list[Ticket]:
            rows = connection.execute(
                "SELECT id, draw_id, user_id, pick, quantity, cost, prize, tax, detail "
                "FROM lottery_tickets WHERE draw_id = ? ORDER BY id",
                (draw_id,),
            ).fetchall()
            return [Ticket(**dict(r)) for r in rows]

        return await self._run(self._read, read)

    async def carry(self, guild_id: int, game: str) -> int:
        """Bote acumulado de un juego en el servidor."""

        def read(connection: sqlite3.Connection) -> int:
            row = connection.execute(
                "SELECT carry FROM lottery_pots WHERE guild_id = ? AND game = ?",
                (guild_id, game),
            ).fetchone()
            return int(row["carry"]) if row else 0

        return await self._run(self._read, read)

    def close(
        self,
        draw: Draw,
        *,
        result: Mapping,
        summary: Mapping,
        prizes: Mapping[int, tuple[int, int, str]],
        carry: int | None,
        now: float,
    ) -> Callable[[sqlite3.Connection], None]:
        """Cierra un sorteo con su resultado y sus premios.

        Args:
            prizes: `id de boleto → (premio, gravamen, detalle)`.
            carry: Bote que pasa al siguiente sorteo (`None` en la Nacional).

        Returns:
            Función para `EconomyRepository.lottery`. Lanza `DrawChanged` si el
            sorteo ya se cerró o si tiene boletos que no se han repartido.
        """

        def run(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT status, tickets FROM lottery_draws WHERE id = ?", (draw.id,)
            ).fetchone()
            if row is None or row["status"] != "open" or int(row["tickets"]) != draw.tickets:
                raise DrawChanged(draw.id)
            connection.execute(
                "UPDATE lottery_draws SET status = 'drawn', result = ?, summary = ?, "
                "drawn_at = ? WHERE id = ?",
                (json.dumps(result), json.dumps(summary), now, draw.id),
            )
            connection.executemany(
                "UPDATE lottery_tickets SET prize = ?, tax = ?, detail = ? WHERE id = ?",
                [(prize, tax, detail, ticket) for ticket, (prize, tax, detail) in prizes.items()],
            )
            if carry is not None:
                connection.execute(
                    """
                    INSERT INTO lottery_pots (guild_id, game, carry) VALUES (?, ?, ?)
                    ON CONFLICT (guild_id, game) DO UPDATE SET carry = excluded.carry
                    """,
                    (draw.guild_id, draw.game, carry),
                )

        return run

    # -- Consultas para el panel --------------------------------------------------------

    async def open_draws(self, guild_id: int) -> dict[tuple[str, float], Draw]:
        """Sorteos abiertos del servidor por `(juego, hora)`."""

        def read(connection: sqlite3.Connection) -> dict[tuple[str, float], Draw]:
            rows = connection.execute(
                "SELECT * FROM lottery_draws WHERE guild_id = ? AND status = 'open'",
                (guild_id,),
            ).fetchall()
            return {(str(r["game"]), float(r["draw_at"])): _draw(r) for r in rows}

        return await self._run(self._read, read)

    async def open_stakes(self, guild_id: int) -> dict[int, int]:
        """Lo pagado por cada miembro en boletos de sorteos aún sin celebrar (`patrimonio`)."""

        def read(connection: sqlite3.Connection) -> dict[int, int]:
            rows = connection.execute(
                """
                SELECT t.user_id, SUM(t.cost) FROM lottery_tickets t
                JOIN lottery_draws d ON d.id = t.draw_id
                WHERE d.guild_id = ? AND d.status = 'open'
                GROUP BY t.user_id
                """,
                (guild_id,),
            ).fetchall()
            return {int(user_id): int(total or 0) for user_id, total in rows}

        return await self._run(self._read, read)

    async def pots(self, guild_id: int) -> dict[str, int]:
        """Bote acumulado de cada juego en el servidor."""

        def read(connection: sqlite3.Connection) -> dict[str, int]:
            rows = connection.execute(
                "SELECT game, carry FROM lottery_pots WHERE guild_id = ?", (guild_id,)
            ).fetchall()
            return {str(r["game"]): int(r["carry"]) for r in rows}

        return await self._run(self._read, read)

    async def last_draw(self, guild_id: int, game: str) -> Draw | None:
        """Último sorteo celebrado de un juego en el servidor."""

        def read(connection: sqlite3.Connection) -> Draw | None:
            row = connection.execute(
                "SELECT * FROM lottery_draws WHERE guild_id = ? AND game = ? "
                "AND status = 'drawn' ORDER BY draw_at DESC LIMIT 1",
                (guild_id, game),
            ).fetchone()
            return _draw(row) if row else None

        return await self._run(self._read, read)

    async def member_tickets(
        self, guild_id: int, user_id: int, *, game: str, draw_at: float
    ) -> list[Ticket]:
        """Boletos de un miembro en un sorteo concreto."""

        def read(connection: sqlite3.Connection) -> list[Ticket]:
            rows = connection.execute(
                """
                SELECT t.id, t.draw_id, t.user_id, t.pick, t.quantity, t.cost, t.prize,
                       t.tax, t.detail
                FROM lottery_tickets t JOIN lottery_draws d ON d.id = t.draw_id
                WHERE d.guild_id = ? AND d.game = ? AND d.draw_at = ? AND t.user_id = ?
                ORDER BY t.id
                """,
                (guild_id, game, draw_at, user_id),
            ).fetchall()
            return [Ticket(**dict(r)) for r in rows]

        return await self._run(self._read, read)

    async def history(self, guild_id: int, user_id: int, *, limit: int) -> list[TicketRecord]:
        """Boletos recientes de un miembro: primero los pendientes, luego los últimos."""

        def read(connection: sqlite3.Connection) -> list[TicketRecord]:
            rows = connection.execute(
                """
                SELECT d.game, d.draw_at, d.status, t.pick, t.quantity, t.cost, t.prize,
                       t.tax, t.detail
                FROM lottery_tickets t JOIN lottery_draws d ON d.id = t.draw_id
                WHERE t.guild_id = ? AND t.user_id = ?
                ORDER BY d.status = 'drawn', d.draw_at DESC, t.id DESC
                LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
            return [TicketRecord(**dict(r)) for r in rows]

        return await self._run(self._read, read)

    async def totals(self, guild_id: int) -> Totals:
        """Lo vendido y repartido en premios, sorteos y rascas juntos."""

        def read(connection: sqlite3.Connection) -> Totals:
            sold, paid, winners = connection.execute(
                """
                SELECT COALESCE(SUM(cost), 0), COALESCE(SUM(prize), 0),
                       COALESCE(SUM(prize > 0), 0)
                FROM (
                    SELECT cost, prize FROM lottery_tickets WHERE guild_id = :guild
                    UNION ALL
                    SELECT cost, prize FROM lottery_scratches WHERE guild_id = :guild
                )
                """,
                {"guild": guild_id},
            ).fetchone()
            return Totals(int(sold), int(paid), int(winners))

        return await self._run(self._read, read)

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra las loterías del servidor que el bot abandona."""

        def write(connection: sqlite3.Connection) -> None:
            with connection:
                for table in (
                    "lottery_tickets",
                    "lottery_draws",
                    "lottery_pots",
                    "lottery_scratches",
                ):
                    # `table` sale de una tupla fija, nunca de entrada del usuario.
                    connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))

        await self._run(self._read, write)
