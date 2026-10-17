"""Persistencia de `pisos`: quién tiene qué, qué pasa en cada inmueble y el índice de cada barrio.

Tablas del mercado (en el mismo archivo SQLite que el resto del bot):

- `housing_holdings`: las participaciones de cada miembro en cada inmueble, con lo
  que pagó (`paid`, sin impuestos: para el Patrimonio) y lo que le costó (`cost`,
  con ITP y notaría: para la ganancia al vender).
- `housing_leases`: el estado de cada inmueble con dueño (vacío, alquilado, impago,
  desahucio, okupas o vivienda habitual de su dueño), el nivel de renta, el contrato
  en vigor (con un inquilino ficticio o con un miembro, `member`, y si cobra el Bono
  Alquiler Joven), la licencia vacacional y la deuda del inmueble. Solo hay fila
  mientras alguien tenga alguna participación.
- `housing_index`: el índice de precios de cada barrio (1 si no hay fila).
- `housing_days`: días ya cerrados, para no cobrar dos veces el mismo mes.
- `housing_news`: lo que ha pasado en cada inmueble (y en las VPO, con `vpo:<clave>`),
  para el panel y los logros. Se guardan los últimos `NEWS_DAYS` días.
- `housing_income`: lo que cobró y pagó cada miembro cada día por cada inmueble,
  para el resumen del panel y lo que mira el banco. Se guardan `NEWS_DAYS` días.

Hipotecas, banco, Euríbor, VPO, vivienda habitual, ofertas del fondo buitre y SOCIMI
están en `bot.repositories.housing_finance`, con funciones que corren dentro de las
mismas transacciones.

El dinero no se toca aquí. Lo que mueve dinero (`buy`, `sell`, `close_day`,
`amortize`, `vpo_buy`, `vpo_return`, `buy_license`, `found_socimi`,
`rent_to_member`, que cobra la fianza) devuelve una función que corre dentro de la
transacción de `EconomyRepository.housing`. Lo que no mueve dinero (la renta que se
pide, desahucios, apuntarse al sorteo, vivir en un piso) va en su propia transacción.

La fianza de un miembro de alquiler se devuelve sola la noche después de que se vaya,
por el camino que sea (se muda, lo desahucian, venden el piso, lo ejecutan): el cierre
de cada día mira las fianzas cuyo inquilino ya no vive en el piso (`_deposits_in`).

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

from bot.repositories import housing_finance as finance
from bot.repositories import sqlite
from bot.repositories.economy import (
    HOUSING_DEPOSIT_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    HousingDebit,
    HousingMove,
    HousingPull,
    HousingRent,
    HousingTax,
    LedgerEntry,
)
from bot.repositories.housing_finance import Mortgage, Promotion, VpoOwner
from bot.services import housing as rules
from bot.services.economy import format_amount
from bot.services.housing_catalog import (
    BARRIOS,
    DEFAULT_RENT_LEVEL,
    LISTING_BY_KEY,
    RENT_LEVELS,
    VPO_BY_KEY,
    VPO_UNITS,
    Listing,
    listings_of,
)
from bot.services.levels import local_day
from bot.services.taxes import INCOME_BUSINESS, INCOME_PRIZE

T = TypeVar("T")

#: Días de noticias y de cobros que se guardan.
NEWS_DAYS = 35
#: Motivo del libro (y concepto del IRPF) de la comisión del promotor de la obra nueva.
PROMOTER_CONCEPT = "pisos:promotor"


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
    """Algo que pasó en un inmueble (o en una VPO, con `vpo:<clave>`).

    Attributes:
        kind: `compra`, `venta`, `firma`, `renueva`, `se_va`, `impago`, `paga`,
            `okupa`, `desahucio`, `juicio`, una clave de `housing.EVENTS` o lo de las
            fases 2 a 4 (`ejecucion`, `sorteo_vpo`, `compra_vpo`, `oferta_fondo`…).
        amount: Lo que costó o movió (0 si nada).
        user_id: El miembro del que va la noticia, si va de uno.
        tenant: El inquilino ficticio, si hay.
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
        mortgages: Hipotecas activas.
        vpo: De quién es cada VPO.
        signups: Apuntados al sorteo de VPO.
        reservations: `{vpo: (a quién le tocó, último día)}`.
        homes: `{miembro: (tipo, referencia)}` de su vivienda habitual.
        euribor: El Euríbor de esta semana.
        bank_debts: Lo que se debe al banco tras una ejecución.
        offers: `{(inmueble, miembro): (precio, último día)}` del fondo buitre.
        socimi: Quién tiene SOCIMI.
        promotions: La obra nueva del servidor (fase 5), por clave.
    """

    indexes: dict[str, float] = field(default_factory=dict)
    holdings: dict[str, dict[int, Holding]] = field(default_factory=dict)
    leases: dict[str, rules.Lease] = field(default_factory=dict)
    mortgages: tuple[Mortgage, ...] = ()
    vpo: dict[str, VpoOwner] = field(default_factory=dict)
    signups: tuple[int, ...] = ()
    reservations: dict[str, tuple[int, date]] = field(default_factory=dict)
    homes: dict[int, tuple[str, str]] = field(default_factory=dict)
    euribor: float = rules.EURIBOR_START
    bank_debts: dict[int, int] = field(default_factory=dict)
    offers: dict[tuple[str, int], tuple[int, date]] = field(default_factory=dict)
    socimi: frozenset[int] = frozenset()
    promotions: dict[str, Promotion] = field(default_factory=dict)

    def listing(self, key: str) -> Listing | None:
        """El inmueble de una clave: del catálogo de siempre o de la obra nueva del servidor."""
        listing = LISTING_BY_KEY.get(key)
        if listing is None and (promotion := self.promotions.get(key)) is not None:
            return promotion.listing
        return listing

    def keys(self) -> list[str]:
        """Las claves de todos los inmuebles del servidor: el catálogo y luego la obra nueva."""
        return [*LISTING_BY_KEY, *self.promotions]

    def listings_of(self, barrio: str) -> tuple[Listing, ...]:
        """Los inmuebles de un barrio: los nueve del catálogo y su obra nueva, si la tiene."""
        extra = tuple(p.listing for p in self.promotions.values() if p.listing.barrio == barrio)
        return (*listings_of(barrio), *extra)

    def index(self, barrio: str) -> float:
        """Índice de un barrio (1 si nunca se ha movido)."""
        return self.indexes.get(barrio, 1.0)

    def index_level(self, barrio: str) -> float:
        """El índice al que tiende un barrio con lo que tienen comprado los miembros."""
        return rules.ownership_level(
            (item, sum(self.shares_of(item.key).values())) for item in self.listings_of(barrio)
        )

    def shares_of(self, listing: str) -> dict[int, int]:
        """Participaciones de cada dueño de un inmueble."""
        return {m: h.shares for m, h in self.holdings.get(listing, {}).items()}

    def free_shares(self, listing: str) -> int:
        """Participaciones que siguen en venta."""
        return rules.SHARES - sum(self.shares_of(listing).values())

    def of_member(self, user_id: int) -> list[Holding]:
        """Lo que tiene un miembro, en el orden del catálogo."""
        out = []
        for key in self.keys():
            holding = self.holdings.get(key, {}).get(user_id)  # solo inmuebles que existen
            if holding is not None:
                out.append(holding)
        return out

    def value(self, holding: Holding) -> int:
        """Valor de mercado de unas participaciones (0 si el inmueble ya no existe)."""
        listing = self.listing(holding.listing)
        if listing is None:
            return 0
        return rules.price(listing, self.index(listing.barrio)) * holding.shares // rules.SHARES

    def mortgages_of(self, user_id: int, listing: str | None = None) -> list[Mortgage]:
        """Hipotecas activas de un miembro (de un inmueble, si se dice)."""
        return [
            m
            for m in self.mortgages
            if m.user_id == user_id and (listing is None or m.listing == listing)
        ]

    def whole_homes(self, user_id: int) -> int:
        """Viviendas que tiene enteras (para gran tenedor, fondo buitre y SOCIMI)."""
        return sum(
            1
            for h in self.of_member(user_id)
            if h.shares == rules.SHARES and self.kind_of(h.listing) in rules.HOME_KINDS
        )

    def owns_home(self, user_id: int) -> bool:
        """Si tiene alguna participación de una vivienda o una VPO (para el sorteo)."""
        if any(owner.owner_id == user_id for owner in self.vpo.values()):
            return True
        return any(self.kind_of(h.listing) in rules.HOME_KINDS for h in self.of_member(user_id))

    def vpo_of(self, user_id: int) -> str | None:
        """La VPO de un miembro, si tiene."""
        return next((u for u, owner in self.vpo.items() if owner.owner_id == user_id), None)

    def reservation_of(self, user_id: int) -> str | None:
        """La VPO que le ha tocado en el sorteo y aún puede comprar."""
        return next((u for u, (m, _until) in self.reservations.items() if m == user_id), None)

    def licensed_in(self, barrio: str) -> int:
        """Licencias vacacionales de un barrio."""
        return sum(
            1
            for key, lease in self.leases.items()
            if lease.license and (item := self.listing(key)) is not None and item.barrio == barrio
        )

    def kind_of(self, key: str) -> str:
        """El tipo de un inmueble del servidor (vacío si la clave ya no existe)."""
        listing = self.listing(key)
        return listing.kind if listing is not None else ""


@dataclass(frozen=True, slots=True)
class Purchase:
    """Una compra hecha.

    Attributes:
        quote: Las cuentas.
        shares: Participaciones que tiene ahora en total.
        index_before: Índice del barrio antes de comprar.
        index_after: Índice después (comprar lo sube).
        first: Si es lo primero que tiene en todo el mercado.
        loan: Lo que prestó el banco (0 al contado).
        payment: La cuota de esa hipoteca.
        rate: Su tipo fijo (Euríbor de la semana más el diferencial).
        aval: Si el banco solo la ha dado con el aval de tus padres.
        promoter: Si es obra nueva, quien la levantó.
        promoter_fee: Lo que cobró el promotor de esta compra (0 si compra el propio
            promotor, que no se paga comisión a sí mismo).
    """

    listing: str
    quote: rules.PurchaseQuote
    shares: int
    index_before: float
    index_after: float
    first: bool
    loan: int = 0
    payment: int = 0
    rate: float = 0.0
    aval: bool = False
    promoter: int | None = None
    promoter_fee: int = 0


@dataclass(frozen=True, slots=True)
class Sale:
    """Una venta hecha.

    Attributes:
        quote: Las cuentas.
        shares_left: Participaciones que le quedan.
        held_days: Días que tuvo el inmueble (desde la primera compra).
        payoff: Lo que se fue a cancelar su hipoteca.
        vulture: Si fue la oferta del fondo buitre.
    """

    listing: str
    quote: rules.SaleQuote
    shares_left: int
    held_days: int
    payoff: int = 0
    vulture: bool = False


@dataclass(frozen=True, slots=True)
class VpoDeal:
    """Una VPO comprada o devuelta.

    Attributes:
        unit: La VPO.
        price: Precio tasado.
        notary: Notaría (al comprar).
        loan: Hipoteca con aval del Estado (al comprar).
        payment: Su cuota.
        payoff: Lo que se fue a cancelar la hipoteca (al devolver).
        returned: Si es una devolución.
    """

    unit: str
    price: int
    notary: int = 0
    loan: int = 0
    payment: int = 0
    payoff: int = 0
    returned: bool = False


@dataclass(frozen=True, slots=True)
class TenantDeal:
    """Un alquiler firmado con un miembro.

    Attributes:
        lease: El contrato.
        deposit: Lo que dejó en el depósito de fianzas (fianza y garantía).
        guarantee: Si el casero le pidió la garantía adicional.
        aval: Si se libró de la garantía gracias al aval de sus padres.
    """

    lease: rules.Lease
    deposit: int
    guarantee: bool
    aval: bool = False


@dataclass(frozen=True, slots=True)
class MemberDay:
    """Lo que un miembro cobró y pagó un día por todos sus inmuebles (antes del IRPF)."""

    rent: int = 0
    ibi: int = 0
    costs: int = 0


@dataclass(slots=True)
class DayRun:
    """Un día cerrado en un servidor (se rellena mientras se cierra).

    Attributes:
        done_before: Ya estaba cerrado; no se ha tocado nada.
        news: Lo que ha pasado.
        members: Lo que cobró y pagó cada dueño.
        owners: Dueños de cada inmueble con noticias (para avisarles).
        executions: `(miembro, inmueble)` de cada ejecución hipotecaria.
        draws: `(miembro, vpo)` de cada sorteo ganado.
        offers: `(miembro, inmueble, precio)` de cada oferta del fondo.
        bono: Lo que el Estado pagó de Bono Alquiler Joven a cada miembro.
        bono_unpaid: Miembros a los que no se pudo pagar el bono (partida agotada).
        mortgage_unpaid: Miembros que no pudieron pagar entera la cuota.
        installments: Cuotas de hipoteca pagadas enteras por cada miembro.
        aval_executions: Miembros a los que han ejecutado una hipoteca avalada por sus
            padres (la familia de `pala` lo paga).
        promotions: `(promotor, inmueble)` de la obra nueva que ha salido hoy (los lunes).
        euribor: `(el de antes, el nuevo)` si hoy ha sido lunes y se ha movido el
            Euríbor (aunque se quede igual), para el anuncio del BCE.
    """

    day: date
    done_before: bool = False
    news: list[News] = field(default_factory=list)
    members: dict[int, MemberDay] = field(default_factory=dict)
    owners: dict[str, tuple[int, ...]] = field(default_factory=dict)
    executions: list[tuple[int, str]] = field(default_factory=list)
    draws: list[tuple[int, str]] = field(default_factory=list)
    offers: list[tuple[int, str, int]] = field(default_factory=list)
    bono: dict[int, int] = field(default_factory=dict)
    bono_unpaid: list[int] = field(default_factory=list)
    mortgage_unpaid: list[int] = field(default_factory=list)
    installments: dict[int, int] = field(default_factory=dict)
    aval_executions: list[int] = field(default_factory=list)
    promotions: list[tuple[int, str]] = field(default_factory=list)
    euribor: tuple[float, float] | None = None


def _date(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _listing(market: Market, key: str) -> Listing:
    listing = market.listing(key)
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
        member=row["member"],
        bono=bool(row["bono"]),
        license=bool(row["license"]),
        last_rent=int(row["last_rent"]),
        reduction=None if row["reduction"] is None else float(row["reduction"]),
    )


def _save_lease_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, lease: rules.Lease
) -> None:
    connection.execute(
        """
        INSERT INTO housing_leases
            (guild_id, listing, status, level, tenant, rent, until, since, unpaid,
             eviction_at, arrears, arrears_ibi, member, bono, license, last_rent, reduction)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, listing) DO UPDATE SET
            status = excluded.status, level = excluded.level, tenant = excluded.tenant,
            rent = excluded.rent, until = excluded.until, since = excluded.since,
            unpaid = excluded.unpaid, eviction_at = excluded.eviction_at,
            arrears = excluded.arrears, arrears_ibi = excluded.arrears_ibi,
            member = excluded.member, bono = excluded.bono, license = excluded.license,
            last_rent = excluded.last_rent, reduction = excluded.reduction
        """,
        (
            guild_id, listing, lease.status, lease.level, lease.tenant, lease.rent,
            _iso(lease.until), _iso(lease.since), lease.unpaid, _iso(lease.eviction_at),
            lease.arrears, lease.arrears_ibi, lease.member, int(lease.bono), int(lease.license),
            lease.last_rent, lease.reduction,
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
    return Market(
        indexes=indexes,
        holdings=holdings,
        leases=leases,
        mortgages=tuple(finance.mortgages_in(connection, guild_id)),
        vpo=finance.vpo_in(connection, guild_id),
        signups=tuple(finance.vpo_signups_in(connection, guild_id)),
        reservations=finance.vpo_reservations_in(connection, guild_id),
        homes=finance.homes_in(connection, guild_id),
        euribor=finance.euribor_in(connection, guild_id),
        bank_debts=finance.bank_debts_in(connection, guild_id),
        offers=finance.offers_in(connection, guild_id),
        socimi=frozenset(finance.socimi_in(connection, guild_id)),
        promotions=finance.promotions_in(connection, guild_id),
    )


def _set_index_in(connection: sqlite3.Connection, guild_id: int, barrio: str, value: float) -> None:
    connection.execute(
        """
        INSERT INTO housing_index (guild_id, barrio, value) VALUES (?, ?, ?)
        ON CONFLICT (guild_id, barrio) DO UPDATE SET value = excluded.value
        """,
        (guild_id, barrio, value),
    )


def _news_in(connection: sqlite3.Connection, guild_id: int, news: News, now: float) -> None:
    connection.execute(
        """
        INSERT INTO housing_news (guild_id, listing, day, kind, amount, user_id, tenant, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, news.listing, news.day.isoformat(), news.kind, news.amount, news.user_id,
         news.tenant, now),
    )  # fmt: skip


def assets_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, int]:
    """Patrimonio neto de los inmuebles de cada miembro (puede ser negativo), para el Patrimonio.

    Corre dentro de la transacción de `EconomyRepository.charge_wealth_tax`:

    - Cada inmueble a su valor fiscal (`housing.fiscal_value`, art. 10 de la Ley 19/1991).
    - La vivienda habitual está exenta hasta 3.000.000 Y$ (art. 4.Nueve): el piso en el
      que vive su dueño y la VPO (que nunca llega a ese valor: no cuenta).
    - Las deudas restan (art. 25): las hipotecas y lo que se debe al banco, salvo las
      de la vivienda habitual, que no son deducibles porque el bien está exento (art. 25.2).
    """
    homes = finance.homes_in(connection, guild_id)
    market = Market(promotions=finance.promotions_in(connection, guild_id))
    out: dict[int, int] = {}
    for r in connection.execute(
        "SELECT listing, user_id, shares, paid FROM housing_holdings WHERE guild_id = ?",
        (guild_id,),
    ):
        listing = market.listing(str(r["listing"]))
        if listing is None:
            continue
        member = int(r["user_id"])
        value = rules.fiscal_value(listing, int(r["shares"]), int(r["paid"]))
        if homes.get(member) == (finance.HOME_OWN, listing.key):
            value -= min(value, rules.HOME_WEALTH_EXEMPT)
        out[member] = out.get(member, 0) + value
    for mortgage in finance.mortgages_in(connection, guild_id):
        habitual = mortgage.listing.startswith("vpo:") or homes.get(mortgage.user_id) == (
            finance.HOME_OWN,
            mortgage.listing,
        )
        if not habitual:
            out[mortgage.user_id] = out.get(mortgage.user_id, 0) - mortgage.owed
    for member, debt in finance.bank_debts_in(connection, guild_id).items():
        out[member] = out.get(member, 0) - debt
    return out


def _vpo_eligible(market: Market, user_id: int) -> bool:
    """Si alguien puede optar a una VPO: sin vivienda propia ni VPO (vivir de alquiler vale)."""
    home = market.homes.get(user_id)
    return not market.owns_home(user_id) and (home is None or home[0] == finance.HOME_RENTED)


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

    def _write(self, operation: Callable[..., T], *args: object) -> T:
        """Corre `operation(conexión, *args)` en una transacción de escritura propia."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection, *args)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result
        finally:
            connection.close()

    async def initialize(self) -> None:
        """Crea las tablas si no existen y añade las columnas que falten."""
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
                    member INTEGER,
                    bono INTEGER NOT NULL DEFAULT 0,
                    license INTEGER NOT NULL DEFAULT 0,
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
                + finance.SCHEMA
            )
            # Bases de datos de la primera versión de `pisos`: sin miembros de alquiler,
            # bono ni licencias; y de antes de la 8.0.0, sin la renta del contrato anterior
            # ni la reducción del art. 23.2 de cada contrato (NULL: la general).
            columns = {row[1] for row in connection.execute("PRAGMA table_info(housing_leases)")}
            for name, ddl in (
                ("member", "member INTEGER"),
                ("bono", "bono INTEGER NOT NULL DEFAULT 0"),
                ("license", "license INTEGER NOT NULL DEFAULT 0"),
                ("last_rent", "last_rent INTEGER NOT NULL DEFAULT 0"),
                ("reduction", "reduction REAL"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE housing_leases ADD COLUMN {ddl}")
        finally:
            connection.close()

    # -- Lecturas ---------------------------------------------------------------------

    async def market(self, guild_id: int) -> Market:
        """Todo el mercado del servidor (200 inmuebles y 6 VPO como mucho: de una vez)."""
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
        """`(valor de mercado, patrimonio neto fiscal)` de los inmuebles de cada miembro."""
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
        for unit, owner in market.vpo.items():
            values[owner.owner_id] = values.get(owner.owner_id, 0) + VPO_BY_KEY[unit].price
        return values, fiscal

    async def home_of(self, guild_id: int, user_id: int) -> tuple[str, str] | None:
        """`(tipo, referencia)` de la vivienda habitual de alguien, o `None`."""
        return await self._run(self._home_of_sync, guild_id, user_id)

    def _home_of_sync(self, guild_id: int, user_id: int) -> tuple[str, str] | None:
        connection = self._connect()
        try:
            return finance.homes_in(connection, guild_id).get(user_id)
        finally:
            connection.close()

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

    # -- Comprar y vender ---------------------------------------------------------------

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
        financed: bool = False,
        income: int = 0,
        aval_income: int = 0,
    ) -> Callable[[sqlite3.Connection], tuple[Purchase, HousingMove]]:
        """Compra participaciones de un inmueble al índice de hoy, al contado o con hipoteca.

        Args:
            expected: Lo que decía la caja (lo que sale del bolsillo). Si ha subido
                entretanto, no se cobra.
            financed: Con hipoteca: el banco presta el 80 % del precio al Euríbor de la
                semana más el diferencial, a 360 meses.
            income: Ingresos diarios que mira el banco (`housing.daily_income`).
            aval_income: Lo que suma el aval de tus padres si lo tienes
                (`housing.parents_income`). Solo se usa si sin él no llega: entonces la
                hipoteca queda avalada.

        Raises (dentro de la transacción):
            HousingError: Si no quedan tantas libres, el número no vale o el banco no
                presta (la cuota pasa del 35 % de lo que ingresa).
            PriceChanged: Si lo que sale del bolsillo ya no es `expected`.
        """

        def run(connection: sqlite3.Connection) -> tuple[Purchase, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _listing(market, key)
            free = market.free_shares(key)
            if shares < 1:
                raise HousingError("Tienes que comprar al menos una participación.")
            if free <= 0:
                raise HousingError("Ya no queda ninguna participación libre de este inmueble.")
            if shares > free:
                raise HousingError(f"Solo quedan {free} participaciones libres.")
            index = market.index(listing.barrio)
            quote = rules.purchase_quote(listing, shares, index)
            loan = payment = 0
            rate = 0.0
            aval = False
            if financed:
                loan = round(quote.price * rules.MORTGAGE_LTV)
                rate = market.euribor + rules.MORTGAGE_SPREAD
                payment = rules.installment(loan, rate)
                current = sum(m.payment for m in market.mortgages_of(user_id))
                if not rules.affordable(income, current + payment):
                    aval = aval_income > 0 and rules.affordable(
                        income + aval_income, current + payment
                    )
                    if not aval:
                        tip = (
                            " Ni con el aval de tus padres."
                            if aval_income
                            else " Trabaja más, pide menos o pide a tus padres que te avalen "
                            "(un favor de `pala`)."
                        )
                        raise HousingError(
                            f"El banco te dice que no: la cuota ({format_amount(payment)} al "
                            f"mes) más las que ya pagas ({format_amount(current)}) pasa del 35 % "
                            f"de lo que ingresas ({format_amount(income)} al día, nóminas y el "
                            f"70 % de tus alquileres).{tip}"
                        )
                finance.add_mortgage_in(
                    connection, guild_id, user_id, key, loan, rate, payment, now,
                    aval=rules.PARENTS_AVAL if aval else None,
                )  # fmt: skip
            out_of_pocket = quote.total - loan
            # Si sale más barato que en la caja se cobra (mejor para quien compra);
            # si sale más caro, no.
            if expected is not None and out_of_pocket > expected:
                raise PriceChanged(out_of_pocket)
            first = not any(user_id in h for h in market.holdings.values())
            held = market.holdings.get(key, {}).get(user_id)
            promotion = market.promotions.get(key)
            promoter = promotion.promoter if promotion is not None else None
            fee = rules.promoter_fee(quote.price) if promoter not in (None, user_id) else 0
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
                    connection, guild_id, key, rules.Lease(level=DEFAULT_RENT_LEVEL, since=day)
                )
            after = rules.index_after(index, listing, shares, bought=True)
            _set_index_in(connection, guild_id, listing.barrio, after)
            _news_in(
                connection, guild_id, News(key, day, "compra", quote.price, user_id, None), now
            )
            if loan:
                _news_in(connection, guild_id, News(key, day, "hipoteca", loan, user_id, None), now)
            purchase = Purchase(
                listing=key,
                quote=quote,
                shares=(held.shares if held else 0) + shares,
                index_before=index,
                index_after=after,
                first=first,
                loan=loan,
                payment=payment,
                rate=rate,
                aval=aval,
                promoter=promoter,
                promoter_fee=fee,
            )
            # La comisión del promotor sale del precio, que si no desaparecería: no crea
            # dinero. Rendimiento de su actividad (art. 27 LIRPF), con retención a cuenta.
            fees = (
                (HousingRent(promoter, fee, kind=INCOME_BUSINESS, concept=PROMOTER_CONCEPT),)
                if promoter is not None and fee
                else ()
            )
            move = HousingMove(
                rents=fees,
                entries=(
                    (user_id, LedgerEntry(-(quote.price - loan), "pisos:compra")),
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
        vulture: bool = False,
    ) -> Callable[[sqlite3.Connection], tuple[Sale, HousingMove]]:
        """Vende participaciones al mercado al índice de hoy (o al fondo buitre).

        Lo cobrado cancela primero la parte de su hipoteca que corresponde a lo vendido
        (cancelación registral) y las deudas del piso. Si lo vende todo, deja de vivir
        en él; si había un miembro de alquiler y no queda ningún dueño, tiene que irse.

        Args:
            expected: Lo que decía la caja que cobraría. Si ha bajado, no se vende.
            vulture: Vender entero al fondo buitre al precio de su oferta.

        Raises (dentro de la transacción):
            HousingError: Si no tiene tantas, o si la oferta no existe o caducó.
            PriceChanged: Si lo que cobraría ya no es `expected`.
        """

        def run(connection: sqlite3.Connection) -> tuple[Sale, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _listing(market, key)
            held = market.holdings.get(key, {}).get(user_id)
            if held is None:
                raise HousingError("No tienes nada en este inmueble.")
            if not 1 <= shares <= held.shares:
                raise HousingError(f"Tienes {held.shares} participaciones: no puedes vender más.")
            lease = market.leases.get(key, rules.Lease())
            index = market.index(listing.barrio)
            agreed = None
            if vulture:
                offer = market.offers.get((key, user_id))
                if offer is None or offer[1] < day:
                    raise HousingError("El fondo ya no tiene ninguna oferta por este inmueble.")
                if shares != held.shares:
                    raise HousingError("El fondo lo quiere entero o nada.")
                agreed = offer[0]
            quote = rules.sale_quote(
                listing, shares, held.shares, held.cost, index=index,
                arrears=lease.arrears, arrears_ibi=lease.arrears_ibi,
                pool=sum(market.shares_of(key).values()), value=agreed,
            )  # fmt: skip
            mortgages = market.mortgages_of(user_id, key)
            payoff = sum(m.owed for m in mortgages) * shares // held.shares
            if expected is not None and quote.received - payoff < expected:
                raise PriceChanged(quote.received - payoff)
            left = held.shares - shares
            for mortgage in mortgages:
                if not left:
                    finance.close_mortgage_in(connection, mortgage.id, "cancelada")
                    continue
                finance.save_mortgage_in(
                    connection, mortgage,
                    balance=mortgage.balance - mortgage.balance * shares // held.shares,
                    payment=mortgage.payment - mortgage.payment * shares // held.shares,
                    months_left=mortgage.months_left,
                    unpaid_count=mortgage.unpaid_count,
                    unpaid_amount=mortgage.unpaid_amount
                    - mortgage.unpaid_amount * shares // held.shares,
                )  # fmt: skip
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
            lease = replace(
                lease,
                arrears=lease.arrears - quote.arrears,
                arrears_ibi=lease.arrears_ibi - quote.arrears_ibi,
            )
            if market.homes.get(user_id) == (finance.HOME_OWN, key):
                # Vender (aunque sea una parte) la casa en la que vives: te mudas.
                finance.clear_home_in(connection, guild_id, user_id=user_id)
                lease = replace(lease, status=rules.VACANT, since=day)
            others = sum(s for m, s in market.shares_of(key).items() if m != user_id)
            if left or others:
                _save_lease_in(connection, guild_id, key, lease)
            else:
                # Sin dueños, el inmueble vuelve al mercado sin deudas. Si vivía un
                # miembro de alquiler, el comprador (el mercado) no le renueva.
                if lease.member is not None:
                    finance.clear_home_in(connection, guild_id, user_id=lease.member)
                connection.execute(
                    "DELETE FROM housing_leases WHERE guild_id = ? AND listing = ?", (guild_id, key)
                )
            connection.execute(
                "DELETE FROM housing_offers WHERE guild_id = ? AND listing = ? AND user_id = ?",
                (guild_id, key, user_id),
            )
            _set_index_in(
                connection,
                guild_id,
                listing.barrio,
                rules.index_after(index, listing, shares, bought=False),
            )
            kind = "venta_fondo" if vulture else "venta"
            _news_in(connection, guild_id, News(key, day, kind, quote.price, user_id, None), now)
            held_days = max(0, int((now - held.since) // 86_400))
            entries: list[tuple[int, LedgerEntry]] = [
                (user_id, LedgerEntry(quote.price, "pisos:venta")),
                (user_id, LedgerEntry(-quote.fee, "pisos:agencia")),
                (user_id, LedgerEntry(-(quote.arrears - quote.arrears_ibi), "pisos:comunidad")),
                (user_id, LedgerEntry(-payoff, "pisos:hipoteca_cancelada")),
            ]
            taxes = (
                HousingTax(user_id, "ibi", quote.arrears_ibi, quote.arrears_ibi),
                HousingTax(user_id, "plusvalia", max(0, quote.gain), quote.gain_tax),
            )
            sale = Sale(key, quote, left, held_days, payoff=payoff, vulture=vulture)
            return sale, HousingMove(entries=tuple(entries), taxes=taxes)

        return run

    def amortize(
        self, guild_id: int, user_id: int, mortgage_id: int, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[int, HousingMove]]:
        """Cancela una hipoteca de golpe con el dinero del monedero.

        Sin comisión: la Ley 5/2019 la limita y aquí no se cobra (decisión del proyecto).

        Returns (dentro de la transacción):
            Lo pagado.
        """

        def run(connection: sqlite3.Connection) -> tuple[int, HousingMove]:
            mortgage = next(
                (m for m in finance.mortgages_in(connection, guild_id, user_id)
                 if m.id == mortgage_id),
                None,
            )  # fmt: skip
            if mortgage is None:
                raise HousingError("Esa hipoteca ya no existe.")
            finance.close_mortgage_in(connection, mortgage.id, "pagada")
            _news_in(
                connection, guild_id,
                News(mortgage.listing, day, "amortiza", mortgage.owed, user_id, None), now,
            )  # fmt: skip
            entry = LedgerEntry(-mortgage.owed, "pisos:amortizacion")
            return mortgage.owed, HousingMove(entries=((user_id, entry),))

        return run

    # -- Cada noche ---------------------------------------------------------------------

    def close_day(
        self,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        builders: tuple[int, ...] = (),
    ) -> Callable[[sqlite3.Connection], tuple[DayRun, HousingMove]]:
        """Cierra un día (un mes del juego) entero.

        Primero cobra la renta a los miembros de alquiler (entera o nada) y luego, sabiendo
        quién pagó: ejecuciones hipotecarias, rentas, bono, IGIC vacacional, SOCIMI,
        recibos de cada inmueble y de cada VPO, cuotas de las hipotecas y deudas con el
        banco. Los lunes, además, el Euríbor, el sorteo de VPO, las ofertas del fondo y la
        obra nueva. Es idempotente: si el día ya estaba cerrado, no mueve nada.

        Args:
            builders: Quienes han fichado esta semana en la rama Ladrillo de `pala`, del
                puesto más alto al más bajo: el lunes, uno de ellos (por turnos) levanta
                una promoción.
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
            result = DayRun(day)
            market = _market_in(connection, guild_id)
            for barrio in (b.key for b in BARRIOS):
                value, level = market.index(barrio), market.index_level(barrio)
                if barrio in market.indexes or value != level:
                    _set_index_in(connection, guild_id, barrio, rules.index_next_day(value, level))
            if day.weekday() == 0:
                self._monday_in(connection, guild_id, day, now, rng, market, result)
                if builders:
                    builder = builders[day.toordinal() // 7 % len(builders)]
                    self._promotion_in(connection, guild_id, day, now, rng, market, builder,
                                       result)  # fmt: skip
            pulls = tuple(
                HousingPull(lease.member, lease.rent, key, "pisos:alquiler", home=True)
                for key, lease in market.leases.items()
                if lease.member is not None
                and lease.status in (rules.RENTED, rules.DEFAULTING)
                and market.listing(key) is not None
            )

            def then(conn: sqlite3.Connection, paid: frozenset[str]) -> HousingMove:
                return self._close_rest_in(conn, guild_id, day, now, rng, paid, result)

            return result, HousingMove(pulls=pulls, then=then)

        return run

    def _monday_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        market: Market,
        result: DayRun,
    ) -> None:
        """Lo de cada lunes: Euríbor, sorteo de VPO y ofertas del fondo buitre."""
        euribor = rules.next_euribor(market.euribor, rng)
        if finance.set_euribor_in(connection, guild_id, day, euribor):
            result.euribor = (market.euribor, euribor)
        # VPO: caducan las reservas que nadie compró y se sortean las libres.
        for unit, (member, until) in market.reservations.items():
            if until < day:
                connection.execute(
                    "DELETE FROM housing_vpo_reservations WHERE guild_id = ? AND unit = ?",
                    (guild_id, unit),
                )
                item = News(finance.vpo_ref(unit), day, "vpo_caducada", 0, member, None)
                result.news.append(item)
                _news_in(connection, guild_id, item, now)
        drawn = connection.execute(
            "INSERT OR IGNORE INTO housing_vpo_draws (guild_id, week) VALUES (?, ?)",
            (guild_id, day.isoformat()),
        ).rowcount
        if drawn:
            reserved = finance.vpo_reservations_in(connection, guild_id)
            candidates = [
                m
                for m in market.signups
                if _vpo_eligible(market, m) and all(owner != m for owner, _u in reserved.values())
            ]
            for unit in VPO_UNITS:
                if unit.key in market.vpo or unit.key in reserved or not candidates:
                    continue
                winner = rng.choice(candidates)
                candidates.remove(winner)
                until = day + timedelta(days=7)
                connection.execute(
                    "INSERT INTO housing_vpo_reservations (guild_id, unit, user_id, until) "
                    "VALUES (?, ?, ?, ?)",
                    (guild_id, unit.key, winner, until.isoformat()),
                )
                connection.execute(
                    "DELETE FROM housing_vpo_signups WHERE guild_id = ? AND user_id = ?",
                    (guild_id, winner),
                )
                result.draws.append((winner, unit.key))
                item = News(finance.vpo_ref(unit.key), day, "sorteo_vpo", unit.price, winner, None)
                result.news.append(item)
                _news_in(connection, guild_id, item, now)
        # Fondo buitre: caducan las ofertas viejas y llega una nueva para quien tiene mucho.
        connection.execute(
            "DELETE FROM housing_offers WHERE guild_id = ? AND until < ?",
            (guild_id, day.isoformat()),
        )
        owners = {m for members in market.holdings.values() for m in members}
        for member in sorted(owners):
            wholes = [h for h in market.of_member(member) if h.shares == rules.SHARES]
            edificio = any(market.kind_of(h.listing) == "edificio" for h in wholes)
            if market.whole_homes(member) < rules.VULTURE_HOMES and not edificio:
                continue
            target = rng.choice(wholes)
            price = round(market.value(target) * (1 + rules.VULTURE_PREMIUM), -3)
            connection.execute(
                """
                INSERT INTO housing_offers (guild_id, listing, user_id, price, until)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, listing, user_id) DO UPDATE SET
                    price = excluded.price, until = excluded.until
                """,
                (guild_id, target.listing, member, price,
                 (day + timedelta(days=rules.VULTURE_DAYS)).isoformat()),
            )  # fmt: skip
            result.offers.append((member, target.listing, price))
            item = News(target.listing, day, "oferta_fondo", price, member, None)
            result.news.append(item)
            _news_in(connection, guild_id, item, now)

    @staticmethod
    def _promotion_in(
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        market: Market,
        builder: int,
        result: DayRun,
    ) -> None:
        """La obra nueva del lunes: una promoción en un barrio que aún no tiene ninguna.

        Cuando todos los barrios tienen la suya (20), no se construye más: es el tope de
        «Nada crece sin tope» de la obra nueva.
        """
        taken = {p.listing.barrio for p in market.promotions.values()}
        free = [b.key for b in BARRIOS if b.key not in taken]
        if not free:
            return
        listing = rules.new_promotion(len(market.promotions) + 1, free, rng)
        finance.add_promotion_in(connection, guild_id, Promotion(listing, builder, day), now)
        result.promotions.append((builder, listing.key))
        item = News(listing.key, day, "obra_nueva", rules.base_price(listing), builder, None)
        result.news.append(item)
        _news_in(connection, guild_id, item, now)

    def _execute_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        market: Market,
        mortgage: Mortgage,
        entries: list[tuple[int, LedgerEntry]],
        taxes: list[HousingTax],
        result: DayRun,
    ) -> None:
        """Ejecución hipotecaria: subasta al 70 %, el banco cobra y lo que falte se sigue debiendo.

        Lo que da la subasta paga primero las deudas del piso (el IBI, al Estado) y
        luego al banco; si sobra, para el dueño. No hay dación en pago: lo que no cubre
        la subasta queda como deuda con el banco y se cobra de lo que vaya entrando.
        """
        member = mortgage.user_id
        if mortgage.listing.startswith("vpo:"):
            unit = mortgage.listing.removeprefix("vpo:")
            owner = market.vpo.get(unit)
            proceeds = round(VPO_BY_KEY[unit].price * rules.AUCTION_SHARE) if owner else 0
            debt_other = debt_ibi = 0
            if owner is not None:
                debt_ibi, debt_other = owner.arrears_ibi, owner.arrears - owner.arrears_ibi
                connection.execute(
                    "DELETE FROM housing_vpo WHERE guild_id = ? AND unit = ?", (guild_id, unit)
                )
            finance.clear_home_in(connection, guild_id, user_id=member)
        else:
            held = market.holdings.get(mortgage.listing, {}).get(member)
            listing = market.listing(mortgage.listing)
            if held is None or listing is None:
                proceeds = debt_ibi = debt_other = 0
            else:
                lease = market.leases.get(mortgage.listing, rules.Lease())
                pool = sum(market.shares_of(mortgage.listing).values())
                index = market.index(listing.barrio)
                proceeds = round(market.value(held) * rules.AUCTION_SHARE)
                debt = lease.arrears * held.shares // pool
                debt_ibi = min(debt, lease.arrears_ibi * held.shares // pool)
                debt_other = debt - debt_ibi
                connection.execute(
                    "DELETE FROM housing_holdings "
                    "WHERE guild_id = ? AND listing = ? AND user_id = ?",
                    (guild_id, mortgage.listing, member),
                )
                lease = replace(
                    lease, arrears=lease.arrears - debt, arrears_ibi=lease.arrears_ibi - debt_ibi
                )
                if market.homes.get(member) == (finance.HOME_OWN, mortgage.listing):
                    finance.clear_home_in(connection, guild_id, user_id=member)
                    lease = replace(lease, status=rules.VACANT, since=day)
                if pool - held.shares > 0:
                    _save_lease_in(connection, guild_id, mortgage.listing, lease)
                else:
                    if lease.member is not None:
                        finance.clear_home_in(connection, guild_id, user_id=lease.member)
                    connection.execute(
                        "DELETE FROM housing_leases WHERE guild_id = ? AND listing = ?",
                        (guild_id, mortgage.listing),
                    )
                _set_index_in(
                    connection, guild_id, listing.barrio,
                    rules.index_after(index, listing, held.shares, bought=False),
                )  # fmt: skip
        debt_ibi = min(debt_ibi, proceeds)
        debt_other = min(debt_other, proceeds - debt_ibi)
        # Lo que sobre de la subasta se queda en su monedero.
        to_bank = min(mortgage.owed, proceeds - debt_ibi - debt_other)
        if proceeds:
            entries.append((member, LedgerEntry(proceeds, "pisos:subasta")))
            entries.append((member, LedgerEntry(-debt_other, "pisos:comunidad")))
            entries.append((member, LedgerEntry(-to_bank, "pisos:banco")))
            taxes.append(HousingTax(member, "ibi", debt_ibi, debt_ibi))
        finance.close_mortgage_in(connection, mortgage.id, "ejecutada")
        if mortgage.owed > to_bank:
            finance.add_bank_debt_in(connection, guild_id, member, mortgage.owed - to_bank)
        result.executions.append((member, mortgage.listing))
        if mortgage.aval == rules.PARENTS_AVAL:
            result.aval_executions.append(member)
        item = News(mortgage.listing, day, "ejecucion", proceeds, member, None)
        result.news.append(item)
        _news_in(connection, guild_id, item, now)

    def _close_rest_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        paid: frozenset[str],
        result: DayRun,
    ) -> HousingMove:
        """El cierre del día una vez se sabe qué miembros de alquiler pagaron."""
        rents: list[HousingRent] = []
        grants: list[HousingRent] = []
        entries: list[tuple[int, LedgerEntry]] = []
        taxes: list[HousingTax] = []
        debits: list[HousingDebit] = []
        imputations: list[tuple[int, str, int]] = []
        market = _market_in(connection, guild_id)
        # Ejecuciones hipotecarias: con lo que se debía al empezar el día.
        executed = set()
        for mortgage in market.mortgages:
            if rules.must_execute(
                mortgage.unpaid_count, mortgage.unpaid_amount, mortgage.principal
            ):
                self._execute_in(
                    connection, guild_id, day, now, market, mortgage, entries, taxes, result
                )
                executed.add(mortgage.id)
        if executed:
            market = _market_in(connection, guild_id)
        # Intereses de hoy de cada hipoteca: lo que se deduce de la renta (art. 23.1.a LIRPF).
        interest: dict[tuple[int, str], int] = {}
        schedule: dict[int, tuple[int, int]] = {}
        for mortgage in market.mortgages:
            pay = mortgage.payment if mortgage.months_left > 0 else 0
            owed_interest, principal = rules.mortgage_month(mortgage.balance, mortgage.rate, pay)
            schedule[mortgage.id] = (mortgage.balance - principal, pay)
            key = (mortgage.user_id, mortgage.listing)
            interest[key] = interest.get(key, 0) + owed_interest
            due = pay + mortgage.unpaid_amount
            if due:
                # La cuota de la hipoteca de la vivienda habitual cuenta para la deducción
                # canaria por inversión en vivienda habitual (art. 14 del DL 1/2009).
                home = market.homes.get(mortgage.user_id) == (finance.HOME_OWN, mortgage.listing)
                debits.append(HousingDebit(mortgage.user_id, f"hipoteca:{mortgage.id}", 0, due,
                                           "pisos:hipoteca", home=home))  # fmt: skip
        for member, amount in market.bank_debts.items():
            debits.append(HousingDebit(member, f"banco:{member}", 0, amount, "pisos:deuda_banco"))
        for unit, owner in market.vpo.items():
            spec = VPO_BY_KEY[unit]
            costs = rules.vpo_costs(spec.price, spec.community)
            debits.append(
                HousingDebit(
                    owner.owner_id, f"vpo:{unit}", costs.ibi + owner.arrears_ibi,
                    costs.community + owner.arrears - owner.arrears_ibi,
                )
            )  # fmt: skip
        self._deposits_in(connection, guild_id, day, now, market, rents, entries, result)
        for key in market.keys():
            lease = market.leases.get(key)
            if lease is None or not market.shares_of(key):
                continue
            self._close_listing_in(
                connection, guild_id, day, now, rng, market, key, lease, paid, interest,
                rents, grants, taxes, entries, debits, imputations, result,
            )  # fmt: skip
        horizon = (day - timedelta(days=NEWS_DAYS)).isoformat()
        connection.execute(
            "DELETE FROM housing_news WHERE guild_id = ? AND day < ?", (guild_id, horizon)
        )
        connection.execute(
            "DELETE FROM housing_income WHERE guild_id = ? AND day < ?", (guild_id, horizon)
        )
        mortgages = {m.id: m for m in market.mortgages}

        def settle(conn: sqlite3.Connection, unpaid: dict[str, tuple[int, int]]) -> None:
            for mortgage_id, (balance, pay) in schedule.items():
                mortgage = mortgages[mortgage_id]
                left = unpaid.get(f"hipoteca:{mortgage_id}", (0, 0))[1]
                months = max(0, mortgage.months_left - (1 if pay else 0))
                if left:
                    result.mortgage_unpaid.append(mortgage.user_id)
                elif pay:
                    member = mortgage.user_id
                    result.installments[member] = result.installments.get(member, 0) + 1
                if not balance and not left:
                    finance.close_mortgage_in(conn, mortgage_id, "pagada")
                    continue
                finance.save_mortgage_in(
                    conn, mortgage, balance=balance, payment=pay if months else 0,
                    months_left=months, unpaid_count=mortgage.unpaid_count + 1 if left else 0,
                    unpaid_amount=left,
                )  # fmt: skip
            for tag, (ibi, other) in unpaid.items():
                if tag.startswith("hipoteca:"):
                    continue
                if tag.startswith("banco:"):
                    member = int(tag.removeprefix("banco:"))
                    conn.execute(
                        "UPDATE housing_bank_debts SET amount = ? "
                        "WHERE guild_id = ? AND user_id = ?",
                        (other, guild_id, member),
                    )
                elif tag.startswith("vpo:"):
                    conn.execute(
                        "UPDATE housing_vpo SET arrears = ?, arrears_ibi = ? "
                        "WHERE guild_id = ? AND unit = ?",
                        (ibi + other, ibi, guild_id, tag.removeprefix("vpo:")),
                    )
                elif tag.startswith("bono:"):
                    member = market.leases[tag.removeprefix("bono:")].member
                    if member is not None and other:
                        result.bono.pop(member, None)
                        result.bono_unpaid.append(member)
                        item = News(tag.removeprefix("bono:"), day, "partida_agotada", other,
                                    member, None)  # fmt: skip
                        result.news.append(item)
                        _news_in(conn, guild_id, item, now)
                elif ibi or other:
                    conn.execute(
                        """
                        UPDATE housing_leases
                        SET arrears = arrears + ?, arrears_ibi = arrears_ibi + ?
                        WHERE guild_id = ? AND listing = ?
                        """,
                        (ibi + other, ibi, guild_id, tag),
                    )

        return HousingMove(
            rents=tuple(rents),
            grants=tuple(grants),
            entries=tuple(entries),
            taxes=tuple(taxes),
            debits=tuple(debits),
            settle=settle,
            imputations=tuple(imputations),
        )

    def _close_listing_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        market: Market,
        key: str,
        lease: rules.Lease,
        paid: frozenset[str],
        interest: dict[tuple[int, str], int],
        rents: list[HousingRent],
        grants: list[HousingRent],
        taxes: list[HousingTax],
        entries: list[tuple[int, LedgerEntry]],
        debits: list[HousingDebit],
        imputations: list[tuple[int, str, int]],
        result: DayRun,
    ) -> None:
        """Un mes de un inmueble con dueño: estado, renta, impuestos y recibos."""
        listing = _listing(market, key)
        index = market.index(listing.barrio)
        news: list[News] = []
        all_holders = market.shares_of(key)
        boss = rules.manager(all_holders)
        # La renta que se pide tiene que estar permitida: la vacacional pide licencia y
        # el gran tenedor tiene tope en zona tensionada (Ley 12/2023).
        if not rules.level_allowed(
            lease.level, index=index, whole_homes=market.whole_homes(boss) if boss else 0,
            license=lease.license,
        ):  # fmt: skip
            lease = replace(lease, level="referencia")
            news.append(News(key, day, "tope_ley", 0, boss, None))
        # Solo cobra y paga ese mes quien ya era dueño ese día.
        holders = {
            member: holding.shares
            for member, holding in market.holdings[key].items()
            if local_day(holding.since) <= day
        }
        if lease.member is not None:
            outcome = self._member_month(listing, lease, key, day, rng, paid, news)
            if outcome.lease.member is None:
                # Desahuciado: deja de vivir aquí y el casero se queda de la fianza lo que
                # debía (art. 36 LAU); el resto se le devuelve mañana.
                finance.clear_home_in(connection, guild_id, user_id=lease.member)
                finance.forfeit_deposit_in(
                    connection, guild_id, key, lease.member, lease.unpaid * lease.rent
                )
        else:
            outcome = rules.advance_day(listing, lease, day, index, rng)
        lease_after = outcome.lease
        owned = sum(holders.values())
        if not owned:
            _save_lease_in(connection, guild_id, key, lease_after)
            self._log_news(connection, guild_id, now, news + self._event_news(key, day, outcome),
                           result)  # fmt: skip
            return
        part = partial(rules.owned_share, owned=owned)
        ibi_month = outcome.costs.ibi
        empty_for = (day - lease_after.since).days if lease_after.since else 0
        if (
            lease_after.status == rules.VACANT
            and empty_for >= rules.EMPTY_DAYS
            and boss is not None
            and market.whole_homes(boss) >= rules.EMPTY_OWNER_HOMES
        ):
            ibi_month = round(ibi_month * rules.EMPTY_IBI_FACTOR)
            if day.weekday() == 0:
                news.append(News(key, day, "recargo_ibi", ibi_month, boss, None))
        costs_owned = rules.MonthlyCosts(
            part(outcome.costs.community), part(outcome.costs.insurance), part(ibi_month)
        )
        rent_owned = part(outcome.rent)
        igic = rules.vacation_igic(rent_owned) if lease_after.level == rules.VACATION_LEVEL else 0
        payout = rules.settle_day(
            rent_owned - igic, costs_owned, part(outcome.extra), lease.arrears, lease.arrears_ibi
        )
        _save_lease_in(connection, guild_id, key, replace(lease_after, arrears=0, arrears_ibi=0))
        owe_ibi = rules.split(payout.arrears_ibi, holders)
        owe_other = rules.split(payout.arrears - payout.arrears_ibi, holders)
        debits.extend(
            HousingDebit(member, key, owe_ibi[member], owe_other[member])
            for member in holders
            if owe_ibi[member] or owe_other[member]
        )
        events = self._event_news(key, day, outcome)
        if events:
            result.owners[key] = tuple(holders)
        self._log_news(connection, guild_id, now, news + events, result)
        if lease_after.member is not None and lease_after.bono and key in paid:
            amount = rules.bono_amount(lease_after.rent)
            grants.append(
                HousingRent(lease_after.member, amount, kind=INCOME_PRIZE, concept="pisos:bono",
                            tag=f"bono:{key}")
            )  # fmt: skip
            result.bono[lease_after.member] = amount
        nets = rules.split(payout.net, holders)
        ibis = rules.split(payout.ibi, holders)
        costs = rules.split(payout.costs, holders)
        igics = rules.split(igic, holders)
        # Para el IRPF, los gastos del mes que toca a cada uno, se paguen hoy o no (se
        # imputan al periodo en que se devengan, art. 14 LIRPF).
        month_ibi = rules.split(costs_owned.ibi, holders)
        community = rules.split(costs_owned.community, holders)
        insurance = rules.split(costs_owned.insurance, holders)
        repairs = rules.split(part(outcome.extra), holders)
        reduction = rules.lease_reduction(listing, lease_after)
        dwelling = reduction > 0
        cadastral = rules.cadastral_value(listing)
        for member, shares in holders.items():
            gross = nets[member] + ibis[member] + costs[member] + igics[member]
            if gross <= 0:
                # Un piso vacío que no es la casa de nadie imputa renta (art. 85 LIRPF).
                if lease_after.status == rules.VACANT:
                    imputations.append((member, key, rules.imputed_rent(listing, shares)))
                continue
            held = market.holdings[key][member]
            if member in market.socimi:
                # La SOCIMI tributa al 0 % y el dueño paga el 19 % del dividendo.
                deductible = (
                    ibis[member] + costs[member] + igics[member] + rules.amortization(held.paid)
                    + interest.get((member, key), 0)
                )  # fmt: skip
                rents.append(HousingRent(member, gross, kind=None))
                profit = max(0, gross - deductible)
                taxes.append(HousingTax(member, "socimi", profit, rules.socimi_tax(profit)))
            else:
                rents.append(
                    HousingRent(
                        member,
                        gross,
                        ref=key,
                        income=gross - igics[member],
                        finance=interest.get((member, key), 0) + repairs[member],
                        costs=(
                            month_ibi[member] + community[member] + insurance[member]
                            + rules.amortization(held.paid)
                        ),
                        upkeep=repairs[member] + insurance[member] if dwelling else 0,
                        reduction=reduction,
                    )
                )  # fmt: skip
            entries.append((member, LedgerEntry(-costs[member], "pisos:comunidad")))
            taxes.append(
                HousingTax(member, "ibi", cadastral * shares // rules.SHARES, ibis[member])
            )
            if igics[member]:
                taxes.append(HousingTax(member, "igic", gross - igics[member], igics[member]))
            before = result.members.get(member, MemberDay())
            result.members[member] = MemberDay(
                before.rent + gross, before.ibi + ibis[member], before.costs + costs[member]
            )
            connection.execute(
                """
                INSERT INTO housing_income (guild_id, user_id, listing, day, rent, ibi, costs)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id, listing, day) DO NOTHING
                """,
                (guild_id, member, key, day.isoformat(), gross, ibis[member], costs[member]),
            )

    @staticmethod
    def _member_month(
        listing: Listing,
        lease: rules.Lease,
        key: str,
        day: date,
        rng: random.Random,
        paid: frozenset[str],
        news: list[News],
    ) -> rules.DayOutcome:
        """Un mes con un miembro de alquiler: paga si le llega; si no, impago."""
        extra, events = rules.roll_events(listing, rng)
        member = lease.member
        rent = 0
        if lease.status == rules.EVICTING:
            if lease.eviction_at is not None and day >= lease.eviction_at:
                news.append(News(key, day, "desahucio", 0, member, None))
                lease = replace(lease, status=rules.VACANT, member=None, bono=False, rent=0,
                                last_rent=lease.rent,
                                until=None, since=day, unpaid=0, eviction_at=None)  # fmt: skip
        elif key in paid:
            rent = lease.rent
            if lease.status == rules.DEFAULTING:
                news.append(News(key, day, "paga", rent, member, None))
            lease = replace(lease, status=rules.RENTED, unpaid=0)
            if lease.until is not None and day >= lease.until:
                lease = replace(lease, until=day + timedelta(days=60))
                news.append(News(key, day, "renueva", 0, member, None))
        else:
            if lease.status == rules.RENTED:
                news.append(News(key, day, "impago", lease.rent, member, None))
            lease = replace(lease, status=rules.DEFAULTING, unpaid=lease.unpaid + 1)
        outcome = rules.DayOutcome(
            lease=lease, rent=rent, costs=rules.monthly_costs(listing), extra=extra, events=events
        )
        return outcome

    @staticmethod
    def _event_news(key: str, day: date, outcome: rules.DayOutcome) -> list[News]:
        tenant = outcome.lease.tenant
        return [News(key, day, e.key, e.cost, None, tenant) for e in outcome.events]

    @staticmethod
    def _log_news(
        connection: sqlite3.Connection, guild_id: int, now: float, items: list[News],
        result: DayRun,
    ) -> None:  # fmt: skip
        for item in items:
            result.news.append(item)
            _news_in(connection, guild_id, item, now)

    # -- VPO ----------------------------------------------------------------------------

    async def vpo_signup(self, guild_id: int, user_id: int, now: float, join: bool) -> None:
        """Se apunta (o se borra) del sorteo de VPO.

        Raises:
            HousingError: Si ya tiene vivienda, VPO o una reserva.
        """
        await self._run(self._write, self._vpo_signup_in, guild_id, user_id, now, join)

    @staticmethod
    def _vpo_signup_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, now: float, join: bool
    ) -> None:
        if not join:
            connection.execute(
                "DELETE FROM housing_vpo_signups WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            return
        market = _market_in(connection, guild_id)
        if not _vpo_eligible(market, user_id):
            raise HousingError("La VPO es para quien no tiene casa. Tú ya tienes dónde vivir.")
        if market.reservation_of(user_id):
            raise HousingError("Ya te ha tocado una: cómprala antes de que caduque.")
        connection.execute(
            "INSERT OR IGNORE INTO housing_vpo_signups (guild_id, user_id, since) VALUES (?, ?, ?)",
            (guild_id, user_id, now),
        )

    def vpo_buy(
        self, guild_id: int, user_id: int, unit: str, day: date, now: float, *, income: int
    ) -> Callable[[sqlite3.Connection], tuple[VpoDeal, HousingMove]]:
        """Compra la VPO que le ha tocado: precio tasado, hipoteca al 100 % con aval del Estado.

        La primera entrega de una VPO de promoción pública lleva IGIC al tipo cero (art. 52
        de la Ley 4/2012): solo se paga la notaría. El precio va a la cuenta del Estado
        (la promoción es pública) y lo presta el banco: es dinero nuevo que se destruye al
        pagar las cuotas.

        Raises (dentro de la transacción):
            HousingError: Si no le ha tocado, caducó, ya tiene casa o el banco no presta.
        """
        spec = VPO_BY_KEY.get(unit)

        def run(connection: sqlite3.Connection) -> tuple[VpoDeal, HousingMove]:
            if spec is None:
                raise HousingError("Esa VPO no existe.")
            market = _market_in(connection, guild_id)
            reservation = market.reservations.get(unit)
            if reservation is None or reservation[0] != user_id or reservation[1] < day:
                raise HousingError("Esa VPO no te ha tocado (o se te pasó el plazo).")
            if not _vpo_eligible(market, user_id):
                raise HousingError("La VPO es para quien no tiene casa.")
            home = market.homes.get(user_id)
            if home is not None and home[0] == finance.HOME_RENTED:
                # Vivía de alquiler en el piso de otro miembro: se muda a su VPO.
                lease = market.leases.get(home[1])
                if lease is not None:
                    _save_lease_in(
                        connection, guild_id, home[1],
                        replace(lease, status=rules.VACANT, member=None, bono=False, rent=0,
                                last_rent=lease.rent,
                                until=None, since=day, unpaid=0, eviction_at=None),
                    )  # fmt: skip
                    _news_in(connection, guild_id, News(home[1], day, "se_va", 0, user_id, None),
                             now)  # fmt: skip
            rate = market.euribor + rules.MORTGAGE_SPREAD
            loan = round(spec.price * rules.VPO_LTV)
            payment = rules.installment(loan, rate)
            current = sum(m.payment for m in market.mortgages_of(user_id))
            if not rules.affordable(income, current + payment):
                raise HousingError(
                    f"Ni con el aval del Estado: la cuota ({format_amount(payment)} al mes) pasa "
                    f"del 35 % de lo que ingresas ({format_amount(income)} al día). Ficha más."
                )
            notary = round(spec.price * rules.NOTARY_RATE)
            connection.execute(
                "INSERT INTO housing_vpo (guild_id, unit, owner_id, paid, since) "
                "VALUES (?, ?, ?, ?, ?)",
                (guild_id, unit, user_id, spec.price, now),
            )
            connection.execute(
                "DELETE FROM housing_vpo_reservations WHERE guild_id = ? AND unit = ?",
                (guild_id, unit),
            )
            finance.add_mortgage_in(
                connection, guild_id, user_id, finance.vpo_ref(unit), loan, rate, payment, now,
                aval="ico",
            )  # fmt: skip
            finance.set_home_in(connection, guild_id, user_id, finance.HOME_VPO, unit, now)
            _news_in(
                connection, guild_id,
                News(finance.vpo_ref(unit), day, "compra_vpo", spec.price, user_id, None), now,
            )  # fmt: skip
            deal = VpoDeal(unit, spec.price, notary=notary, loan=loan, payment=payment)
            return deal, HousingMove(
                entries=(
                    (user_id, LedgerEntry(-notary, "pisos:notaria")),
                    (STATE_ACCOUNT_ID, LedgerEntry(spec.price, "vpo:venta")),
                )
            )

        return run

    def vpo_return(
        self, guild_id: int, user_id: int, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[VpoDeal, HousingMove]]:
        """Devuelve la VPO: la Administración la recompra a precio tasado (derecho de tanteo).

        Lo paga la cuenta del Estado; con eso se cancela la hipoteca y las deudas de la
        VPO, y la VPO vuelve al sorteo.

        Raises (dentro de la transacción):
            HousingError: Si no tiene VPO.
            InsufficientFundsError: Si el Estado no tiene para recomprarla.
        """

        def run(connection: sqlite3.Connection) -> tuple[VpoDeal, HousingMove]:
            market = _market_in(connection, guild_id)
            unit = market.vpo_of(user_id)
            if unit is None:
                raise HousingError("No tienes ninguna VPO.")
            owner = market.vpo[unit]
            spec = VPO_BY_KEY[unit]
            mortgages = market.mortgages_of(user_id, finance.vpo_ref(unit))
            payoff = sum(m.owed for m in mortgages)
            for mortgage in mortgages:
                finance.close_mortgage_in(connection, mortgage.id, "cancelada")
            connection.execute(
                "DELETE FROM housing_vpo WHERE guild_id = ? AND unit = ?", (guild_id, unit)
            )
            finance.clear_home_in(connection, guild_id, user_id=user_id)
            _news_in(
                connection, guild_id,
                News(finance.vpo_ref(unit), day, "devuelve_vpo", spec.price, user_id, None), now,
            )  # fmt: skip
            other = owner.arrears - owner.arrears_ibi
            deal = VpoDeal(unit, spec.price, payoff=payoff, returned=True)
            return deal, HousingMove(
                entries=(
                    (STATE_ACCOUNT_ID, LedgerEntry(-spec.price, "vpo:recompra")),
                    (user_id, LedgerEntry(spec.price, "vpo:recompra")),
                    (user_id, LedgerEntry(-payoff, "pisos:hipoteca_cancelada")),
                    (user_id, LedgerEntry(-other, "pisos:comunidad")),
                ),
                taxes=(HousingTax(user_id, "ibi", owner.arrears_ibi, owner.arrears_ibi),),
            )

        return run

    # -- Vivir y alquilar a miembros ----------------------------------------------------

    async def set_home(self, guild_id: int, user_id: int, key: str, day: date, now: float) -> None:
        """Se muda a un piso suyo: deja de alquilarse y pasa a ser su vivienda habitual.

        Raises:
            HousingError: Si no es entero suyo, no es vivienda, está alquilado o ya vive
                en otro sitio.
        """
        await self._run(self._write, self._set_home_in, guild_id, user_id, key, day, now)

    @staticmethod
    def _set_home_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, day: date,
        now: float,
    ) -> None:  # fmt: skip
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        if market.shares_of(key).get(user_id, 0) != rules.SHARES:
            raise HousingError("Solo puedes vivir en un piso que sea entero tuyo.")
        if listing.kind not in rules.HOME_KINDS:
            raise HousingError("En un trastero o un garaje no se empadrona nadie. Todavía.")
        if user_id in market.homes:
            raise HousingError("Ya vives en otro sitio: múdate primero.")
        lease = market.leases[key]
        if lease.status != rules.VACANT:
            raise HousingError("Está ocupado: tiene que estar vacío para que te mudes.")
        _save_lease_in(connection, guild_id, key, replace(lease, status=rules.HABITUAL, since=day))
        finance.set_home_in(connection, guild_id, user_id, finance.HOME_OWN, key, now)
        _news_in(connection, guild_id, News(key, day, "se_muda", 0, user_id, None), now)

    async def leave_home(self, guild_id: int, user_id: int, day: date, now: float) -> str:
        """Deja la vivienda habitual (su piso o el que alquila a otro miembro).

        Returns:
            El tipo de vivienda que deja.

        Raises:
            HousingError: Si no vive en ningún sitio o vive en una VPO (se devuelve).
        """
        return await self._run(self._write, self._leave_home_in, guild_id, user_id, day, now)

    @staticmethod
    def _leave_home_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, day: date, now: float
    ) -> str:
        market = _market_in(connection, guild_id)
        home = market.homes.get(user_id)
        if home is None:
            raise HousingError("No vives en ningún piso del mercado: vives con tus padres.")
        kind, ref = home
        if kind == finance.HOME_VPO:
            raise HousingError("De la VPO no se sale así: hay que devolverla.")
        lease = market.leases.get(ref)
        if lease is not None:
            _save_lease_in(
                connection, guild_id, ref,
                replace(lease, status=rules.VACANT, member=None, bono=False, rent=0,
                                last_rent=lease.rent,
                        until=None, since=day, unpaid=0, eviction_at=None),
            )  # fmt: skip
        finance.clear_home_in(connection, guild_id, user_id=user_id)
        _news_in(connection, guild_id, News(ref, day, "se_va", 0, user_id, None), now)
        return kind

    def rent_to_member(
        self,
        guild_id: int,
        owner_id: int,
        key: str,
        member_id: int,
        day: date,
        now: float,
        *,
        bono: bool,
        income: int,
        aval: bool = False,
    ) -> Callable[[sqlite3.Connection], tuple[TenantDeal, HousingMove]]:
        """Alquila un piso a un miembro con la renta que pide el inmueble, 5 años (60 días).

        El miembro deja la fianza de un mes y, si su nómina no llega a 3 veces la renta y
        no le avalan sus padres, la garantía adicional de dos meses más
        (`housing.deposit_for`). Va al depósito de fianzas y vuelve cuando se va.

        Args:
            income: Neto medio de nómina al día del miembro (`work_income`).
            aval: Si sus padres le avalan (el favor de `pala`): no se pide la garantía.

        Raises (dentro de la transacción):
            HousingError: Si no lo decide quien lo ofrece, no está vacío, el miembro ya
                vive en otro sitio o es uno de los dueños.
            InsufficientFundsError: Si no le llega para la fianza. No se firma nada.
        """

        def run(connection: sqlite3.Connection) -> tuple[TenantDeal, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _listing(market, key)
            holders = market.shares_of(key)
            if rules.manager(holders) != owner_id:
                raise HousingError("Lo alquila quien tiene más de la mitad del inmueble.")
            if listing.kind not in rules.HOME_KINDS:
                raise HousingError("Eso no es una vivienda.")
            if member_id in holders:
                raise HousingError("No puedes alquilarle el piso a uno de sus dueños.")
            if member_id in market.homes:
                raise HousingError("Ya vive en otro sitio.")
            lease = market.leases[key]
            if lease.status != rules.VACANT:
                raise HousingError("Tiene que estar vacío para firmar con otro inquilino.")
            level = RENT_LEVELS[
                "referencia" if lease.level == rules.VACATION_LEVEL else lease.level
            ]
            rent = rules.asking_rent(listing, level, market.index(listing.barrio))
            short = rules.needs_guarantee(rent, income, aval=False)
            guarantee = short and not aval
            deposit = rules.deposit_for(rent, guarantee=guarantee)
            # Un miembro vive en él: es alquiler de vivienda aunque el dueño pidiera otra renta.
            reduction = rules.contract_reduction(
                listing, "referencia", index=market.index(listing.barrio), rent=rent,
                last_rent=lease.last_rent,
            )  # fmt: skip
            lease = replace(lease, status=rules.RENTED, tenant=None, member=member_id,
                            bono=bono, rent=rent, until=day + timedelta(days=60), since=day,
                            unpaid=0, reduction=reduction)  # fmt: skip
            _save_lease_in(connection, guild_id, key, lease)
            finance.set_home_in(connection, guild_id, member_id, finance.HOME_RENTED, key, now)
            finance.add_deposit_in(connection, guild_id, key, member_id, deposit, now)
            _news_in(connection, guild_id, News(key, day, "firma_miembro", rent, member_id,
                                                None), now)  # fmt: skip
            move = HousingMove(
                entries=(
                    (member_id, LedgerEntry(-deposit, "pisos:fianza")),
                    (HOUSING_DEPOSIT_ACCOUNT_ID, LedgerEntry(deposit, "pisos:fianza")),
                )
            )
            return TenantDeal(lease, deposit, guarantee, short and aval), move

        return run

    def _deposits_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        market: Market,
        rents: list[HousingRent],
        entries: list[tuple[int, LedgerEntry]],
        result: DayRun,
    ) -> None:
        """Devuelve las fianzas de quien ya no vive de alquiler en el piso (art. 36.4 LAU).

        Lo que se queda el casero por los meses que debía (`forfeit`) es renta del
        alquiler para los dueños de hoy, con su IRPF; si ya no tiene dueños, vuelve
        entera al inquilino. Sale todo del depósito de fianzas: no se crea dinero.
        """
        for deposit in finance.deposits_in(connection, guild_id):
            lease = market.leases.get(deposit.listing)
            if lease is not None and lease.member == deposit.user_id:
                continue
            holders = market.shares_of(deposit.listing)
            kept = deposit.forfeit if holders else 0
            # La renta que debía un miembro: vivienda, con la reducción general.
            for member, part in rules.split(kept, holders).items():
                if part > 0:
                    rents.append(
                        HousingRent(
                            member, part, ref=deposit.listing, reduction=rules.RENT_REDUCTION
                        )
                    )
            refund = deposit.amount - kept
            entries.append(
                (HOUSING_DEPOSIT_ACCOUNT_ID, LedgerEntry(-deposit.amount, "pisos:fianza"))
            )
            entries.append((deposit.user_id, LedgerEntry(refund, "pisos:fianza")))
            finance.delete_deposit_in(connection, guild_id, deposit.listing, deposit.user_id)
            items = []
            if refund:
                items.append(
                    News(deposit.listing, day, "fianza_devuelta", refund, deposit.user_id, None)
                )
            if kept:
                items.append(
                    News(deposit.listing, day, "fianza_incautada", kept, deposit.user_id, None)
                )
            self._log_news(connection, guild_id, now, items, result)

    # -- Fase 4: licencias, SOCIMI ------------------------------------------------------

    def buy_license(
        self, guild_id: int, user_id: int, key: str, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[int, HousingMove]]:
        """Pide la licencia de vivienda vacacional (una por barrio); la tasa va al Estado.

        Raises (dentro de la transacción):
            HousingError: Si no lo decide él, no es vivienda, vive alguien o el barrio ya
                no tiene plazas.
        """

        def run(connection: sqlite3.Connection) -> tuple[int, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _listing(market, key)
            if rules.manager(market.shares_of(key)) != user_id:
                raise HousingError("La licencia la pide quien tiene más de la mitad del inmueble.")
            if listing.kind not in rules.HOME_KINDS:
                raise HousingError("Un garaje vacacional no lo aprueba ni el Cabildo.")
            lease = market.leases[key]
            if lease.license:
                raise HousingError("Ya tiene licencia.")
            if lease.status == rules.HABITUAL or lease.member is not None:
                raise HousingError("Vive alguien: no se puede convertir en vacacional.")
            if market.licensed_in(listing.barrio) >= rules.LICENSE_PER_BARRIO:
                raise HousingError(
                    "El barrio ya no tiene plazas vacacionales: está saturado. Canarias tiene "
                    "un límite (y este barrio, también)."
                )
            _save_lease_in(connection, guild_id, key, replace(lease, license=True))
            _news_in(connection, guild_id, News(key, day, "licencia", rules.LICENSE_FEE,
                                                user_id, None), now)  # fmt: skip
            fee = rules.LICENSE_FEE
            return fee, HousingMove(taxes=(HousingTax(user_id, "licencia", fee, fee),))

        return run

    def found_socimi(
        self, guild_id: int, user_id: int, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[int, HousingMove]]:
        """Monta su SOCIMI: hace falta tener 3 inmuebles enteros y pagar la salida a bolsa.

        La salida (notaría, registro y el BME Growth) desaparece.

        Raises (dentro de la transacción):
            HousingError: Si ya la tiene o no tiene inmuebles suficientes.
        """

        def run(connection: sqlite3.Connection) -> tuple[int, HousingMove]:
            market = _market_in(connection, guild_id)
            if user_id in market.socimi:
                raise HousingError("Ya tienes tu SOCIMI.")
            if market.whole_homes(user_id) < rules.SOCIMI_HOMES:
                raise HousingError(
                    f"Para una SOCIMI hacen falta {rules.SOCIMI_HOMES} viviendas enteras. "
                    "Hacerse fondo cuesta."
                )
            connection.execute(
                "INSERT INTO housing_socimi (guild_id, user_id, since) VALUES (?, ?, ?)",
                (guild_id, user_id, now),
            )
            fee = rules.SOCIMI_FEE
            return fee, HousingMove(entries=((user_id, LedgerEntry(-fee, "pisos:socimi")),))

        return run

    # -- Lo que no mueve dinero -------------------------------------------------------

    async def set_level(self, guild_id: int, user_id: int, key: str, level: str) -> rules.Lease:
        """Cambia la renta que pide el inmueble (solo quien tiene la mayoría).

        Vale para el próximo contrato: a mitad de uno no se puede subir (art. 18 LAU).

        Raises:
            HousingError: Si no tiene la mayoría, el nivel no existe, pide licencia o el
                gran tenedor tiene tope en zona tensionada.
        """
        return await self._run(self._write, self._set_level_in, guild_id, user_id, key, level)

    @staticmethod
    def _set_level_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, level: str
    ) -> rules.Lease:
        if level not in RENT_LEVELS:
            raise HousingError("Esa renta no existe.")
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        if rules.manager(market.shares_of(key)) != user_id:
            raise HousingError(
                "La renta la decide quien tiene más de la mitad del inmueble "
                "(art. 398 del Código Civil)."
            )
        lease = market.leases[key]
        if level == rules.VACATION_LEVEL and not lease.license:
            raise HousingError("Sin licencia vacacional no se alquila a turistas. Pídela antes.")
        if not rules.level_allowed(
            level, index=market.index(listing.barrio), whole_homes=market.whole_homes(user_id),
            license=lease.license,
        ):  # fmt: skip
            raise HousingError(
                "Eres gran tenedor en una zona tensionada: la Ley de Vivienda no te deja pedir "
                "más que la renta de referencia (art. 17 de la Ley 12/2023)."
            )
        lease = replace(lease, level=level)
        _save_lease_in(connection, guild_id, key, lease)
        return lease

    async def evict(
        self, guild_id: int, user_id: int, key: str, day: date, now: float, rng: random.Random
    ) -> rules.Lease:
        """Pide el desahucio de quien no paga (también de un miembro) o de los okupas.

        Lo pide quien tiene la mayoría; si nadie la tiene, cualquier copropietario
        (art. 394 del Código Civil: cada uno puede defender la cosa común).

        Raises:
            HousingError: Si no le toca o no hay a quién desahuciar.
        """
        return await self._run(self._write, self._evict_in, guild_id, user_id, key, day, now, rng)

    @staticmethod
    def _evict_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, day: date,
        now: float, rng: random.Random,
    ) -> rules.Lease:  # fmt: skip
        market = _market_in(connection, guild_id)
        _listing(market, key)
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
        _news_in(connection, guild_id, News(key, day, "juicio", 0, lease.member or user_id,
                                            lease.tenant), now)  # fmt: skip
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
                *finance.TABLES,
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
    "VpoDeal",
    "assets_in",
]
