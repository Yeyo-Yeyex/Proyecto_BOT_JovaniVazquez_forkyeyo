"""Persistencia de `pisos`: quién tiene qué, qué pasa en cada inmueble y el índice de cada barrio.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `housing_holdings`: las participaciones de cada miembro en cada inmueble, con lo
  que pagó (`paid`, sin impuestos: para el Patrimonio) y lo que le costó (`cost`,
  con ITP y notaría: para la ganancia al vender).
- `housing_leases`: el estado de cada inmueble con dueño (vacío, alquilado, impago,
  desahucio u okupas), el nivel de renta que pide, el contrato en vigor y la deuda
  del inmueble (comunidad, seguro e IBI sin pagar). Solo hay fila mientras alguien
  tenga alguna participación.
- `housing_index`: el índice de precios de cada barrio (1 si no hay fila).
- `housing_days`: días ya cerrados, para no cobrar dos veces el mismo mes.
- `housing_news`: lo que ha pasado en cada inmueble (compras, ventas, inquilinos,
  averías…), para el panel y los logros. Se guardan los últimos `NEWS_DAYS` días.
- `housing_income`: lo que cobró y pagó cada miembro cada día por cada inmueble,
  para el resumen del panel. Se guardan los últimos `NEWS_DAYS` días.

El dinero no se toca aquí. Lo que mueve dinero (`buy`, `sell`, `close_day`) devuelve
una función que corre dentro de la transacción de `EconomyRepository.housing`: las
participaciones, el inmueble y el dinero se guardan juntos o no se guarda nada.
Cambiar la renta y pedir un desahucio no mueven dinero y van en su propia
transacción.

Solo se guardan IDs de miembros, claves del catálogo, importes y fechas. Todo se
borra cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.repositories.economy import (
    HousingDebit,
    HousingMove,
    HousingRent,
    HousingTax,
    LedgerEntry,
)
from bot.services import housing as rules
from bot.services.housing_catalog import (
    DEFAULT_RENT_LEVEL,
    LISTING_BY_KEY,
    RENT_LEVELS,
    Listing,
)
from bot.services.levels import local_day

T = TypeVar("T")

#: Días de noticias y de cobros que se guardan.
NEWS_DAYS = 35


class HousingError(ValueError):
    """Una operación de `pisos` que no se puede hacer; el mensaje es para el miembro."""


class PriceChanged(HousingError):
    """El precio ha cambiado desde que se enseñó la caja: no se cobra nada.

    Attributes:
        amount: Lo que costaría (o se cobraría) ahora.
    """

    def __init__(self, amount: int) -> None:
        super().__init__("El precio ha cambiado mientras mirabas la caja.")
        self.amount = amount


@dataclass(frozen=True, slots=True)
class Holding:
    """Las participaciones de un miembro en un inmueble.

    Attributes:
        paid: Lo que pagó por ellas, sin impuestos ni gastos.
        cost: Lo que le costaron, con ITP y notaría.
        since: Cuándo compró la primera (epoch).
    """

    listing: str
    user_id: int
    shares: int
    paid: int
    cost: int
    since: float


@dataclass(frozen=True, slots=True)
class News:
    """Algo que pasó en un inmueble.

    Attributes:
        kind: `compra`, `venta`, `firma`, `renueva`, `se_va`, `impago`, `paga`,
            `okupa`, `desahucio`, `juicio` o una clave de `housing.EVENTS`.
        amount: Lo que costó o movió (0 si nada).
        user_id: Quien compró o vendió, si fue eso.
        tenant: El inquilino, si hay.
    """

    listing: str
    day: date
    kind: str
    amount: int
    user_id: int | None
    tenant: str | None


@dataclass(frozen=True, slots=True)
class Market:
    """Todo el mercado de un servidor en un momento.

    Attributes:
        indexes: Índice de cada barrio que se ha movido (los demás, 1).
        holdings: Participaciones de cada inmueble, por miembro.
        leases: Estado de cada inmueble con dueño.
    """

    indexes: dict[str, float] = field(default_factory=dict)
    holdings: dict[str, dict[int, Holding]] = field(default_factory=dict)
    leases: dict[str, rules.Lease] = field(default_factory=dict)

    def index(self, barrio: str) -> float:
        """Índice de un barrio (1 si nunca se ha movido)."""
        return self.indexes.get(barrio, 1.0)

    def shares_of(self, listing: str) -> dict[int, int]:
        """Participaciones de cada dueño de un inmueble."""
        return {m: h.shares for m, h in self.holdings.get(listing, {}).items()}

    def free_shares(self, listing: str) -> int:
        """Participaciones que siguen en venta."""
        return rules.SHARES - sum(self.shares_of(listing).values())

    def of_member(self, user_id: int) -> list[Holding]:
        """Lo que tiene un miembro, en el orden del catálogo."""
        out = []
        for key in LISTING_BY_KEY:
            holding = self.holdings.get(key, {}).get(user_id)  # solo inmuebles que existen
            if holding is not None:
                out.append(holding)
        return out

    def value(self, holding: Holding) -> int:
        """Valor de mercado de unas participaciones (0 si el inmueble ya no existe)."""
        listing = LISTING_BY_KEY.get(holding.listing)
        if listing is None:
            return 0
        return rules.price(listing, self.index(listing.barrio)) * holding.shares // rules.SHARES


@dataclass(frozen=True, slots=True)
class Purchase:
    """Una compra hecha.

    Attributes:
        quote: Las cuentas.
        shares: Participaciones que tiene ahora en total.
        index_before: Índice del barrio antes de comprar.
        index_after: Índice después (comprar lo sube).
        first: Si es lo primero que tiene en todo el mercado.
    """

    listing: str
    quote: rules.PurchaseQuote
    shares: int
    index_before: float
    index_after: float
    first: bool


@dataclass(frozen=True, slots=True)
class Sale:
    """Una venta hecha.

    Attributes:
        quote: Las cuentas.
        shares_left: Participaciones que le quedan.
        held_days: Días que tuvo el inmueble (desde la primera compra).
    """

    listing: str
    quote: rules.SaleQuote
    shares_left: int
    held_days: int


@dataclass(frozen=True, slots=True)
class MemberDay:
    """Lo que un miembro cobró y pagó un día por todos sus inmuebles (antes del IRPF)."""

    rent: int = 0
    ibi: int = 0
    costs: int = 0


@dataclass(frozen=True, slots=True)
class DayRun:
    """Un día cerrado en un servidor.

    Attributes:
        done_before: Ya estaba cerrado; no se ha tocado nada.
        news: Lo que ha pasado.
        members: Lo que cobró y pagó cada dueño.
        owners: Dueños de cada inmueble con noticias (para avisarles).
    """

    day: date
    done_before: bool = False
    news: tuple[News, ...] = ()
    members: dict[int, MemberDay] = field(default_factory=dict)
    owners: dict[str, tuple[int, ...]] = field(default_factory=dict)


def _date(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _listing(key: str) -> Listing:
    listing = LISTING_BY_KEY.get(key)
    if listing is None:
        raise HousingError("Ese inmueble no está en el mercado.")
    return listing


def _lease_from(row: sqlite3.Row) -> rules.Lease:
    return rules.Lease(
        status=str(row["status"]),
        level=str(row["level"]),
        tenant=row["tenant"],
        rent=int(row["rent"]),
        until=_date(row["until"]),
        since=_date(row["since"]),
        unpaid=int(row["unpaid"]),
        eviction_at=_date(row["eviction_at"]),
        arrears=int(row["arrears"]),
        arrears_ibi=int(row["arrears_ibi"]),
    )


def _save_lease_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, lease: rules.Lease
) -> None:
    connection.execute(
        """
        INSERT INTO housing_leases
            (guild_id, listing, status, level, tenant, rent, until, since, unpaid,
             eviction_at, arrears, arrears_ibi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, listing) DO UPDATE SET
            status = excluded.status, level = excluded.level, tenant = excluded.tenant,
            rent = excluded.rent, until = excluded.until, since = excluded.since,
            unpaid = excluded.unpaid, eviction_at = excluded.eviction_at,
            arrears = excluded.arrears, arrears_ibi = excluded.arrears_ibi
        """,
        (
            guild_id, listing, lease.status, lease.level, lease.tenant, lease.rent,
            _iso(lease.until), _iso(lease.since), lease.unpaid, _iso(lease.eviction_at),
            lease.arrears, lease.arrears_ibi,
        ),
    )  # fmt: skip


def _market_in(connection: sqlite3.Connection, guild_id: int) -> Market:
    indexes = {
        str(r["barrio"]): float(r["value"])
        for r in connection.execute(
            "SELECT barrio, value FROM housing_index WHERE guild_id = ?", (guild_id,)
        )
    }
    holdings: dict[str, dict[int, Holding]] = {}
    for r in connection.execute(
        "SELECT listing, user_id, shares, paid, cost, since FROM housing_holdings "
        "WHERE guild_id = ?",
        (guild_id,),
    ):
        holding = Holding(
            str(r["listing"]), int(r["user_id"]), int(r["shares"]), int(r["paid"]),
            int(r["cost"]), float(r["since"]),
        )  # fmt: skip
        holdings.setdefault(holding.listing, {})[holding.user_id] = holding
    leases = {
        str(r["listing"]): _lease_from(r)
        for r in connection.execute("SELECT * FROM housing_leases WHERE guild_id = ?", (guild_id,))
    }
    return Market(indexes=indexes, holdings=holdings, leases=leases)


def _set_index_in(connection: sqlite3.Connection, guild_id: int, barrio: str, value: float) -> None:
    connection.execute(
        """
        INSERT INTO housing_index (guild_id, barrio, value) VALUES (?, ?, ?)
        ON CONFLICT (guild_id, barrio) DO UPDATE SET value = excluded.value
        """,
        (guild_id, barrio, value),
    )


def _news_in(
    connection: sqlite3.Connection,
    guild_id: int,
    news: News,
    now: float,
) -> None:
    connection.execute(
        """
        INSERT INTO housing_news (guild_id, listing, day, kind, amount, user_id, tenant, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, news.listing, news.day.isoformat(), news.kind, news.amount, news.user_id,
         news.tenant, now),
    )  # fmt: skip


def assets_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, int]:
    """Valor fiscal de los inmuebles de cada miembro, para el Patrimonio.

    Corre dentro de la transacción de `EconomyRepository.charge_wealth_tax`
    (`housing.fiscal_value`: el mayor entre catastral y lo pagado).
    """
    out: dict[int, int] = {}
    for r in connection.execute(
        "SELECT listing, user_id, shares, paid FROM housing_holdings WHERE guild_id = ?",
        (guild_id,),
    ):
        listing = LISTING_BY_KEY.get(str(r["listing"]))
        if listing is None:
            continue
        member = int(r["user_id"])
        out[member] = out.get(member, 0) + rules.fiscal_value(
            listing, int(r["shares"]), int(r["paid"])
        )
    return out


class HousingRepository:
    """Acceso SQLite a `pisos`.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path, isolation_level=None)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS housing_holdings (
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    shares INTEGER NOT NULL CHECK (shares > 0 AND shares <= 100),
                    paid INTEGER NOT NULL CHECK (paid >= 0),
                    cost INTEGER NOT NULL CHECK (cost >= 0),
                    since REAL NOT NULL,
                    PRIMARY KEY (guild_id, listing, user_id)
                );

                CREATE TABLE IF NOT EXISTS housing_leases (
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    status TEXT NOT NULL,
                    level TEXT NOT NULL,
                    tenant TEXT,
                    rent INTEGER NOT NULL DEFAULT 0,
                    until TEXT,
                    since TEXT,
                    unpaid INTEGER NOT NULL DEFAULT 0,
                    eviction_at TEXT,
                    arrears INTEGER NOT NULL DEFAULT 0 CHECK (arrears >= 0),
                    arrears_ibi INTEGER NOT NULL DEFAULT 0 CHECK (arrears_ibi >= 0),
                    PRIMARY KEY (guild_id, listing)
                );

                CREATE TABLE IF NOT EXISTS housing_index (
                    guild_id INTEGER NOT NULL,
                    barrio TEXT NOT NULL,
                    value REAL NOT NULL,
                    PRIMARY KEY (guild_id, barrio)
                );

                CREATE TABLE IF NOT EXISTS housing_days (
                    guild_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, day)
                );

                CREATE TABLE IF NOT EXISTS housing_news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    day TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0,
                    user_id INTEGER,
                    tenant TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS housing_news_listing
                    ON housing_news (guild_id, listing, id);

                CREATE TABLE IF NOT EXISTS housing_income (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    day TEXT NOT NULL,
                    rent INTEGER NOT NULL,
                    ibi INTEGER NOT NULL,
                    costs INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, listing, day)
                );
                """
            )
        finally:
            connection.close()

    # -- Lecturas ---------------------------------------------------------------------

    async def market(self, guild_id: int) -> Market:
        """Todo el mercado del servidor (60 inmuebles como mucho: se lee de una vez)."""
        return await self._run(self._market_sync, guild_id)

    def _market_sync(self, guild_id: int) -> Market:
        connection = self._connect()
        try:
            return _market_in(connection, guild_id)
        finally:
            connection.close()

    async def news(self, guild_id: int, listings: tuple[str, ...], limit: int = 8) -> list[News]:
        """Lo último que ha pasado en unos inmuebles, de lo más nuevo a lo más viejo."""
        return await self._run(self._news_sync, guild_id, listings, limit)

    def _news_sync(self, guild_id: int, listings: tuple[str, ...], limit: int) -> list[News]:
        if not listings:
            return []
        marks = ", ".join("?" * len(listings))
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT listing, day, kind, amount, user_id, tenant FROM housing_news
                WHERE guild_id = ? AND listing IN ({marks})
                ORDER BY id DESC LIMIT ?
                """,  # solo marcadores `?`, nunca texto del usuario
                (guild_id, *listings, limit),
            ).fetchall()
        finally:
            connection.close()
        return [
            News(str(r["listing"]), date.fromisoformat(r["day"]), str(r["kind"]),
                 int(r["amount"]), r["user_id"], r["tenant"])
            for r in rows
        ]  # fmt: skip

    async def income(self, guild_id: int, user_id: int, since: date) -> MemberDay:
        """Lo que un miembro ha cobrado y pagado desde `since` (incluido)."""
        return await self._run(self._income_sync, guild_id, user_id, since)

    def _income_sync(self, guild_id: int, user_id: int, since: date) -> MemberDay:
        connection = self._connect()
        try:
            rent, ibi, costs = connection.execute(
                """
                SELECT COALESCE(SUM(rent), 0), COALESCE(SUM(ibi), 0), COALESCE(SUM(costs), 0)
                FROM housing_income WHERE guild_id = ? AND user_id = ? AND day >= ?
                """,
                (guild_id, user_id, since.isoformat()),
            ).fetchone()
        finally:
            connection.close()
        return MemberDay(int(rent), int(ibi), int(costs))

    async def member_values(self, guild_id: int) -> tuple[dict[int, int], dict[int, int]]:
        """`(valor de mercado, valor fiscal)` de los inmuebles de cada miembro."""
        return await self._run(self._member_values_sync, guild_id)

    def _member_values_sync(self, guild_id: int) -> tuple[dict[int, int], dict[int, int]]:
        connection = self._connect()
        try:
            market = _market_in(connection, guild_id)
            fiscal = assets_in(connection, guild_id)
        finally:
            connection.close()
        values: dict[int, int] = {}
        for members in market.holdings.values():
            for member, holding in members.items():
                values[member] = values.get(member, 0) + market.value(holding)
        return values, fiscal

    async def last_day(self, guild_id: int) -> date | None:
        """Último día cerrado en el servidor, o `None` si nunca se cerró ninguno."""
        return await self._run(self._last_day_sync, guild_id)

    def _last_day_sync(self, guild_id: int) -> date | None:
        connection = self._connect()
        try:
            (day,) = connection.execute(
                "SELECT MAX(day) FROM housing_days WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        finally:
            connection.close()
        return _date(day)

    # -- Lo que mueve dinero (corre dentro de `EconomyRepository.housing`) ------------

    def buy(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        shares: int,
        day: date,
        now: float,
        *,
        expected: int | None = None,
    ) -> Callable[[sqlite3.Connection], tuple[Purchase, HousingMove]]:
        """Compra participaciones de un inmueble al índice de hoy.

        Args:
            expected: Lo que decía la caja (precio, ITP y notaría). Si alguien ha
                movido el índice del barrio entretanto, no se cobra.

        Raises (dentro de la transacción):
            HousingError: Si no quedan tantas libres o el número no vale.
            PriceChanged: Si el total ya no es `expected`.
        """
        listing = _listing(key)

        def run(connection: sqlite3.Connection) -> tuple[Purchase, HousingMove]:
            market = _market_in(connection, guild_id)
            free = market.free_shares(key)
            if shares < 1:
                raise HousingError("Tienes que comprar al menos una participación.")
            if free <= 0:
                raise HousingError("Ya no queda ninguna participación libre de este inmueble.")
            if shares > free:
                raise HousingError(f"Solo quedan {free} participaciones libres.")
            index = market.index(listing.barrio)
            quote = rules.purchase_quote(listing, shares, index)
            # Si sale más barato que en la caja se cobra (mejor para quien compra);
            # si sale más caro, no.
            if expected is not None and quote.total > expected:
                raise PriceChanged(quote.total)
            first = not any(user_id in h for h in market.holdings.values())
            held = market.holdings.get(key, {}).get(user_id)
            connection.execute(
                """
                INSERT INTO housing_holdings (guild_id, listing, user_id, shares, paid, cost, since)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, listing, user_id) DO UPDATE SET
                    shares = shares + excluded.shares, paid = paid + excluded.paid,
                    cost = cost + excluded.cost
                """,
                (guild_id, key, user_id, shares, quote.price, quote.total, now),
            )
            if key not in market.leases:
                _save_lease_in(
                    connection, guild_id, key,
                    rules.Lease(level=DEFAULT_RENT_LEVEL, since=day),
                )  # fmt: skip
            after = rules.index_after(index, listing, shares, bought=True)
            _set_index_in(connection, guild_id, listing.barrio, after)
            bought = News(key, day, "compra", quote.price, user_id, None)
            _news_in(connection, guild_id, bought, now)
            purchase = Purchase(
                listing=key,
                quote=quote,
                shares=(held.shares if held else 0) + shares,
                index_before=index,
                index_after=after,
                first=first,
            )
            move = HousingMove(
                entries=(
                    (user_id, LedgerEntry(-quote.price, "pisos:compra")),
                    (user_id, LedgerEntry(-quote.notary, "pisos:notaria")),
                ),
                taxes=(HousingTax(user_id, "itp", quote.price, quote.itp),),
            )
            return purchase, move

        return run

    def sell(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        shares: int,
        day: date,
        now: float,
        *,
        expected: int | None = None,
    ) -> Callable[[sqlite3.Connection], tuple[Sale, HousingMove]]:
        """Vende participaciones al mercado al índice de hoy.

        Args:
            expected: Lo que decía la caja que cobraría. Si ha cambiado (el índice,
                las deudas del piso), no se vende.

        Raises (dentro de la transacción):
            HousingError: Si no tiene tantas.
            PriceChanged: Si lo que cobraría ya no es `expected`.
        """
        listing = _listing(key)

        def run(connection: sqlite3.Connection) -> tuple[Sale, HousingMove]:
            market = _market_in(connection, guild_id)
            held = market.holdings.get(key, {}).get(user_id)
            if held is None:
                raise HousingError("No tienes nada en este inmueble.")
            if not 1 <= shares <= held.shares:
                raise HousingError(f"Tienes {held.shares} participaciones: no puedes vender más.")
            lease = market.leases.get(key, rules.Lease())
            index = market.index(listing.barrio)
            quote = rules.sale_quote(
                listing, shares, held.shares, held.cost, index=index,
                arrears=lease.arrears, arrears_ibi=lease.arrears_ibi,
                pool=sum(market.shares_of(key).values()),
            )  # fmt: skip
            if expected is not None and quote.received < expected:
                raise PriceChanged(quote.received)
            left = held.shares - shares
            if left:
                connection.execute(
                    """
                    UPDATE housing_holdings SET shares = ?, paid = ?, cost = ?
                    WHERE guild_id = ? AND listing = ? AND user_id = ?
                    """,
                    (left, held.paid - held.paid * shares // held.shares, held.cost - quote.cost,
                     guild_id, key, user_id),
                )  # fmt: skip
            else:
                connection.execute(
                    "DELETE FROM housing_holdings "
                    "WHERE guild_id = ? AND listing = ? AND user_id = ?",
                    (guild_id, key, user_id),
                )
            others = sum(s for m, s in market.shares_of(key).items() if m != user_id)
            if left or others:
                _save_lease_in(
                    connection, guild_id, key,
                    replace(lease, arrears=lease.arrears - quote.arrears,
                            arrears_ibi=lease.arrears_ibi - quote.arrears_ibi),
                )  # fmt: skip
            else:
                # Sin dueños, el inmueble vuelve al mercado con su inquilino y sin deudas.
                connection.execute(
                    "DELETE FROM housing_leases WHERE guild_id = ? AND listing = ?", (guild_id, key)
                )
            _set_index_in(
                connection,
                guild_id,
                listing.barrio,
                rules.index_after(index, listing, shares, bought=False),
            )
            _news_in(connection, guild_id, News(key, day, "venta", quote.price, user_id, None), now)
            held_days = max(0, int((now - held.since) // 86_400))
            entries: list[tuple[int, LedgerEntry]] = [
                (user_id, LedgerEntry(quote.price, "pisos:venta")),
                (user_id, LedgerEntry(-quote.fee, "pisos:agencia")),
                (user_id, LedgerEntry(-(quote.arrears - quote.arrears_ibi), "pisos:comunidad")),
            ]
            taxes = (
                HousingTax(user_id, "ibi", quote.arrears_ibi, quote.arrears_ibi),
                HousingTax(user_id, "plusvalia", max(0, quote.gain), quote.gain_tax),
            )
            return Sale(key, quote, left, held_days), HousingMove(
                entries=tuple(entries), taxes=taxes
            )

        return run

    def close_day(
        self, guild_id: int, day: date, now: float, rng: random.Random
    ) -> Callable[[sqlite3.Connection], tuple[DayRun, HousingMove]]:
        """Cierra un día (un mes del juego): rentas, gastos, imprevistos e índices.

        Es idempotente: si el día ya estaba cerrado, no mueve nada.
        """

        def run(connection: sqlite3.Connection) -> tuple[DayRun, HousingMove]:
            done = connection.execute(
                "SELECT 1 FROM housing_days WHERE guild_id = ? AND day = ?",
                (guild_id, day.isoformat()),
            ).fetchone()
            if done is not None:
                return DayRun(day, done_before=True), HousingMove()
            connection.execute(
                "INSERT INTO housing_days (guild_id, day, processed_at) VALUES (?, ?, ?)",
                (guild_id, day.isoformat(), now),
            )
            market = _market_in(connection, guild_id)
            for barrio, value in market.indexes.items():
                _set_index_in(connection, guild_id, barrio, rules.index_next_day(value))
            news: list[News] = []
            members: dict[int, MemberDay] = {}
            owners: dict[str, tuple[int, ...]] = {}
            rents: list[HousingRent] = []
            entries: list[tuple[int, LedgerEntry]] = []
            taxes: list[HousingTax] = []
            debits: list[HousingDebit] = []
            for key in LISTING_BY_KEY:
                lease = market.leases.get(key)
                if lease is None or not market.shares_of(key):
                    continue
                # Solo cobra y paga ese mes quien ya era dueño ese día: comprar entre la
                # medianoche y el cierre no da derecho a la renta del día anterior.
                holders = {
                    member: holding.shares
                    for member, holding in market.holdings[key].items()
                    if local_day(holding.since) <= day
                }
                listing = LISTING_BY_KEY[key]
                outcome = rules.advance_day(listing, lease, day, market.index(listing.barrio), rng)
                owned = sum(holders.values())
                if not owned:
                    _save_lease_in(connection, guild_id, key, outcome.lease)
                    continue
                part = partial(rules.owned_share, owned=owned)
                costs_owned = rules.MonthlyCosts(
                    part(outcome.costs.community), part(outcome.costs.insurance),
                    part(outcome.costs.ibi),
                )  # fmt: skip
                payout = rules.settle_day(
                    part(outcome.rent), costs_owned, part(outcome.extra), lease.arrears,
                    lease.arrears_ibi,
                )  # fmt: skip
                # Lo que la renta no cubre se intenta cobrar del monedero de los dueños
                # (recibo domiciliado); lo que no llegue vuelve como deuda en `settle`.
                _save_lease_in(
                    connection, guild_id, key, replace(outcome.lease, arrears=0, arrears_ibi=0)
                )
                owe_ibi = rules.split(payout.arrears_ibi, holders)
                owe_other = rules.split(payout.arrears - payout.arrears_ibi, holders)
                debits.extend(
                    HousingDebit(member, key, owe_ibi[member], owe_other[member])
                    for member in holders
                    if owe_ibi[member] or owe_other[member]
                )
                for event in outcome.events:
                    item = News(key, day, event.key, event.cost, None, outcome.lease.tenant
                                or lease.tenant)  # fmt: skip
                    news.append(item)
                    _news_in(connection, guild_id, item, now)
                if outcome.events:
                    owners[key] = tuple(holders)
                nets = rules.split(payout.net, holders)
                ibis = rules.split(payout.ibi, holders)
                costs = rules.split(payout.costs, holders)
                cadastral = rules.cadastral_value(listing)
                for member, shares in holders.items():
                    gross = nets[member] + ibis[member] + costs[member]
                    if gross <= 0:
                        continue
                    paid = market.holdings[key][member].paid
                    deductible = ibis[member] + costs[member] + rules.amortization(paid)
                    taxable = rules.taxable_rent(gross, deductible)
                    rents.append(HousingRent(member, gross, taxable, rules.rent_to_fiscal(taxable)))
                    entries.append((member, LedgerEntry(-costs[member], "pisos:comunidad")))
                    taxes.append(
                        HousingTax(member, "ibi", cadastral * shares // rules.SHARES, ibis[member])
                    )
                    before = members.get(member, MemberDay())
                    members[member] = MemberDay(
                        before.rent + gross, before.ibi + ibis[member], before.costs + costs[member]
                    )
                    connection.execute(
                        """
                        INSERT INTO housing_income
                            (guild_id, user_id, listing, day, rent, ibi, costs)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, member, key, day.isoformat(), gross, ibis[member],
                         costs[member]),
                    )  # fmt: skip
            horizon = (day - timedelta(days=NEWS_DAYS)).isoformat()
            connection.execute(
                "DELETE FROM housing_news WHERE guild_id = ? AND day < ?", (guild_id, horizon)
            )
            connection.execute(
                "DELETE FROM housing_income WHERE guild_id = ? AND day < ?", (guild_id, horizon)
            )

            def settle(conn: sqlite3.Connection, unpaid: dict[str, tuple[int, int]]) -> None:
                for tag, (ibi, other) in unpaid.items():
                    if ibi or other:
                        conn.execute(
                            """
                            UPDATE housing_leases
                            SET arrears = arrears + ?, arrears_ibi = arrears_ibi + ?
                            WHERE guild_id = ? AND listing = ?
                            """,
                            (ibi + other, ibi, guild_id, tag),
                        )

            run_ = DayRun(day, news=tuple(news), members=members, owners=owners)
            return run_, HousingMove(
                rents=tuple(rents),
                entries=tuple(entries),
                taxes=tuple(taxes),
                debits=tuple(debits),
                settle=settle,
            )

        return run

    # -- Lo que no mueve dinero -------------------------------------------------------

    async def set_level(self, guild_id: int, user_id: int, key: str, level: str) -> rules.Lease:
        """Cambia la renta que pide el inmueble (solo quien tiene la mayoría).

        Vale para el próximo contrato: a mitad de uno no se puede subir (art. 18 LAU).

        Raises:
            HousingError: Si no tiene la mayoría o el nivel no existe.
        """
        return await self._run(self._set_level_sync, guild_id, user_id, key, level)

    def _set_level_sync(self, guild_id: int, user_id: int, key: str, level: str) -> rules.Lease:
        _listing(key)
        if level not in RENT_LEVELS:
            raise HousingError("Esa renta no existe.")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                market = _market_in(connection, guild_id)
                if rules.manager(market.shares_of(key)) != user_id:
                    raise HousingError(
                        "La renta la decide quien tiene más de la mitad del inmueble "
                        "(art. 398 del Código Civil)."
                    )
                lease = replace(market.leases[key], level=level)
                _save_lease_in(connection, guild_id, key, lease)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        finally:
            connection.close()
        return lease

    async def evict(
        self, guild_id: int, user_id: int, key: str, day: date, now: float, rng: random.Random
    ) -> rules.Lease:
        """Pide el desahucio de quien no paga o de los okupas.

        Lo pide quien tiene la mayoría; si nadie la tiene, cualquier copropietario
        (art. 394 del Código Civil: cada uno puede defender la cosa común).

        Raises:
            HousingError: Si no le toca o no hay a quién desahuciar.
        """
        return await self._run(self._evict_sync, guild_id, user_id, key, day, now, rng)

    def _evict_sync(
        self, guild_id: int, user_id: int, key: str, day: date, now: float, rng: random.Random
    ) -> rules.Lease:
        _listing(key)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                market = _market_in(connection, guild_id)
                holders = market.shares_of(key)
                boss = rules.manager(holders)
                if user_id not in holders or (boss is not None and boss != user_id):
                    raise HousingError("El desahucio lo pide quien tiene la mayoría del inmueble.")
                lease = market.leases[key]
                try:
                    lease = rules.start_eviction(lease, day, rng)
                except ValueError as error:
                    raise HousingError(str(error)) from error
                _save_lease_in(connection, guild_id, key, lease)
                _news_in(
                    connection, guild_id, News(key, day, "juicio", 0, user_id, lease.tenant), now
                )
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        finally:
            connection.close()
        return lease

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo de un servidor (al salir el bot)."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            for table in (
                "housing_holdings",
                "housing_leases",
                "housing_index",
                "housing_days",
                "housing_news",
                "housing_income",
            ):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        finally:
            connection.close()


__all__ = [
    "DayRun",
    "Holding",
    "HousingError",
    "HousingRepository",
    "Market",
    "MemberDay",
    "News",
    "PriceChanged",
    "Purchase",
    "Sale",
    "assets_in",
]
