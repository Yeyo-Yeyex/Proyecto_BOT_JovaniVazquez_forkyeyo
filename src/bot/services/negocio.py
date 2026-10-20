"""Casos de uso de los negocios de `pala`: montar, comprar, abrir turnos, las noches y cerrar.

Junta las reglas (`bot.services.business`), los minijuegos (`bot.services.business_games`),
la persistencia (`bot.repositories.business`) y el dinero (`EconomyService.business`; la
cuota de autónomos, `charge_self_employed_fee`). Las franjas y la batería son de la persona:
las da `WorkService` (`agenda`, `battery`, `spend_battery`). No sabe nada de Discord: el cog
`bot.cogs.business` dibuja y llama a esto.

Todo el dinero (y su tratamiento fiscal) está documentado en `EconomyService.business`:

- Montar: tasa de la apertura y acondicionamiento (con IGIC), enteros o nada; la fianza al
  depósito de fianzas. La primera cuota de autónomo, con la tarifa plana.
- Comprar al mayorista: base e IGIC, enteros o nada.
- Un turno: sus ventas, con la retención a cuenta sobre el margen y el embargo.
- Cada noche (`close_nights`): el alquiler (con su IGIC y la retención del casero), los
  suministros y lo que se debía del local, hasta donde llegue. A las `EVICTION_NIGHTS`
  noches sin pagar, el casero rescinde: la fianza cubre la deuda y el resto vuelve.
- Cada semana: la cuota de autónomo, si no la ha pagado ya (una por persona).
- Cerrar: la fianza vuelve, menos lo que se deba.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from bot.repositories.business import BusinessRepository
from bot.services.agenda import Agenda
from bot.services.business import (
    EVICTION_NIGHTS,
    FLAT_RATE_DAYS,
    REPUTATION_MAX,
    Business,
    NightlyBill,
    OpeningQuote,
    clean_name,
    daily_pool,
    drift,
    nightly_bill,
    opening_quote,
    spoil,
    turn_clients,
    wholesale,
)
from bot.services.business_catalog import (
    EMBLEMS,
    PALETTES,
    PRICE_BY_KEY,
    SECTOR_BY_KEY,
)
from bot.services.business_games import TurnGame, TurnSummary, apply_loyalty, make_clients
from bot.services.economy import (
    BusinessCost,
    BusinessMove,
    EconomyService,
    InsufficientFundsError,
)
from bot.services.housing_catalog import BARRIO_BY_KEY
from bot.services.levels import TIMEZONE, local_day
from bot.services.pala import WorkService
from bot.services.telemetry import TimedLock
from bot.services.work import (
    BATTERY_MIN,
    FLAT_RATE_FEE,
    SELF_EMPLOYED_FEE,
    SHIFT_COST,
    TIRED,
    week_of,
)

logger = logging.getLogger(__name__)

#: Noches que se cierran como mucho de una vez (si el bot estuvo apagado).
CATCH_UP_NIGHTS = 7
#: Lotes que se pueden comprar de una vez.
MAX_LOTS = 20


class BusinessError(Exception):
    """Algo que impide la acción; el mensaje es para el usuario."""


@dataclass(frozen=True, slots=True)
class BusinessStatus:
    """Foto de un negocio para el panel.

    Attributes:
        business: El negocio, con los contadores de hoy al día.
        agenda: Las franjas de hoy de su dueño.
        bill: Lo que cuesta su noche.
        pool_left: Clientes que le quedan hoy al barrio para su sector.
        rivals: Otros negocios del mismo sector en su barrio.
        battery: Batería del dueño, si tiene curro.
        now: Cuándo se hizo la foto.
    """

    business: Business
    agenda: Agenda
    bill: NightlyBill
    pool_left: float
    rivals: int
    battery: float | None
    now: float

    @property
    def blocker(self) -> str | None:
        """Lo que impide abrir un turno ahora, o `None`."""
        if not self.agenda.fits(1):
            return (
                "No te quedan franjas libres hoy: la jornada y las horas extra se las han comido."
            )
        if self.battery is not None and self.battery <= BATTERY_MIN:
            return "Estás tan reventado que te quedas dormido de pie. Descansa un poco."
        if not any(self.business.stock.get(p.key, 0) for p in self.business.sector_info.products):
            return "No tienes nada que vender: pasa por el 📦 almacén."
        if self.pool_left < 1:
            return "Hoy el barrio ya no da más de sí: todos tus clientes ya han comprado."
        return None


@dataclass(slots=True)
class Turn:
    """Un turno en marcha.

    Attributes:
        business: El negocio al empezar (con el turno ya apuntado).
        game: El minijuego.
        battery_before: Batería del dueño al empezar (`None` sin curro).
    """

    business: Business
    game: TurnGame
    battery_before: float | None = None

    @property
    def tired(self) -> bool:
        """Si empezó reventado."""
        return self.battery_before is not None and self.battery_before < TIRED


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Lo que dejó un turno.

    Attributes:
        business: El negocio después del turno.
        summary: Lo que pasó con cada cliente.
        withheld: IRPF a cuenta de las ventas.
        garnished: Lo embargado por deudas con Hacienda.
        balance: Saldo final del dueño.
        stars_before: Estrellas al empezar.
        best: Si es su mejor turno en ventas.
        hour: Hora canaria a la que empezó.
        weekday: Día de la semana en que empezó (0 = lunes).
    """

    business: Business
    summary: TurnSummary
    withheld: int
    garnished: int
    balance: int
    stars_before: float
    best: bool
    hour: int
    weekday: int

    @property
    def net(self) -> int:
        """Lo que entró en el bolsillo (ventas menos lo retenido y embargado)."""
        return self.summary.sales - self.withheld - self.garnished


@dataclass(frozen=True, slots=True)
class NightRun:
    """Lo que pasó en la noche de un negocio.

    Attributes:
        user_id: Su dueño.
        name: Su nombre.
        day: La noche (el día que se cierra).
        due: Lo que había que pagar (con la deuda de antes).
        unpaid: Lo que no llegó.
        spoiled: Unidades perdidas por producto.
        evicted: Si el casero rescindió el contrato esta noche.
    """

    user_id: int
    name: str
    day: date
    due: int
    unpaid: int
    spoiled: dict[str, int] = field(default_factory=dict)
    evicted: bool = False


@dataclass(frozen=True, slots=True)
class Closing:
    """Cómo acabó un negocio que se cierra.

    Attributes:
        business: El negocio cerrado.
        refund: Fianza que vuelve.
        forfeit: Fianza que se queda el casero.
        balance: Saldo final del dueño.
    """

    business: Business
    refund: int
    forfeit: int
    balance: int


class BusinessService:
    """Casos de uso de los negocios.

    Args:
        repository: Persistencia de los negocios.
        economy: Única puerta al dinero.
        work: El curro del dueño (franjas y batería).
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
        rng: Azar de las colas de clientes; inyectable en pruebas.
    """

    def __init__(
        self,
        repository: BusinessRepository,
        economy: EconomyService,
        work: WorkService,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.repository = repository
        self.economy = economy
        self.work = work
        self._clock = clock
        self.rng = rng or random.Random()
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def now(self) -> float:
        """Hora actual según el reloj del servicio (epoch)."""
        return self._clock()

    def _lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            for other, value in list(self._locks.items()):
                if not value.locked():
                    del self._locks[other]
            lock = self._locks[key] = TimedLock("negocio:BusinessService._locks")
        return lock

    def _today(self, now: float) -> tuple[date, str]:
        today = local_day(now)
        return today, week_of(today)

    # -- Lo que pregunta la `pala` (`pala.BusinessLink`) --------------------------------

    async def franjas_today(self, guild_id: int, user_id: int) -> int:
        """Turnos de hoy en su negocio (0 si no tiene)."""
        business = await self.repository.business(guild_id, user_id)
        if business is None:
            return 0
        today = local_day(self._clock()).isoformat()
        return business.turns_today if business.day == today else 0

    async def last_turn_at(self, guild_id: int, user_id: int) -> float | None:
        """Cuándo abrió su negocio por última vez (`None` si no tiene o nunca abrió)."""
        business = await self.repository.business(guild_id, user_id)
        if business is None or not business.last_turn_at:
            return None
        return business.last_turn_at

    # -- Consulta -----------------------------------------------------------------------

    async def business(self, guild_id: int, user_id: int) -> Business | None:
        """El negocio de un miembro (con los contadores de hoy al día), o `None`."""
        business = await self.repository.business(guild_id, user_id)
        if business is not None:
            business.roll_over(*self._today(self._clock()))
        return business

    async def status(self, guild_id: int, user_id: int) -> BusinessStatus | None:
        """Foto del negocio de un miembro para el panel, o `None` si no tiene."""
        business = await self.business(guild_id, user_id)
        if business is None:
            return None
        now = self._clock()
        today = local_day(now)
        used = await self.repository.pool_used(guild_id, business.barrio, business.sector, today)
        others = await self.repository.businesses(guild_id)
        rivals = sum(
            1
            for other in others
            if other.id != business.id
            and other.barrio == business.barrio
            and other.sector == business.sector
        )
        return BusinessStatus(
            business=business,
            agenda=await self.work.agenda(guild_id, user_id),
            bill=nightly_bill(business),
            pool_left=daily_pool(business.sector_info, business.barrio_info) - used,
            rivals=rivals,
            battery=await self.work.battery(guild_id, user_id),
            now=now,
        )

    @staticmethod
    def quote(sector: str, barrio: str) -> OpeningQuote:
        """Lo que cuesta montar `sector` en `barrio`.

        Raises:
            BusinessError: Si el sector o el barrio no existen.
        """
        if sector not in SECTOR_BY_KEY or barrio not in BARRIO_BY_KEY:
            raise BusinessError("Ese negocio o ese barrio no existen.")
        return opening_quote(SECTOR_BY_KEY[sector], BARRIO_BY_KEY[barrio])

    async def closed(self, guild_id: int, user_id: int) -> list[sqlite3.Row]:
        """Los negocios que cerró un miembro."""
        return await self.repository.closed(guild_id, user_id)

    # -- Montar y retocar ---------------------------------------------------------------

    async def open(
        self,
        guild_id: int,
        user_id: int,
        *,
        sector: str,
        barrio: str,
        name: str,
        palette: int,
        emblem: int,
    ) -> tuple[Business, int]:
        """Monta un negocio: tasa, acondicionamiento y fianza, y la cuota de la semana.

        Returns:
            `(el negocio, cuota de autónomo cobrada)`.

        Raises:
            BusinessError: Si ya tiene uno, algo no existe o no le llega.
        """
        quote = self.quote(sector, barrio)
        try:
            name = clean_name(name)
        except ValueError as error:
            raise BusinessError(str(error)) from error
        if not 0 <= palette < len(PALETTES) or not 0 <= emblem < len(EMBLEMS):
            raise BusinessError("Ese rótulo no existe.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                if self.repository.business_in(connection, guild_id, user_id) is not None:
                    raise BusinessError("Ya tienes un negocio. De momento, uno por persona.")
                business = Business(
                    user_id=user_id,
                    sector=sector,
                    barrio=barrio,
                    name=name,
                    palette=palette,
                    emblem=emblem,
                    opened_at=now,
                    seed=self.rng.randrange(2**31),
                    deposit=quote.deposit,
                    # La primera noche que se cobra es la de hoy.
                    billed_day=(today - timedelta(days=1)).isoformat(),
                )
                business.roll_over(today, week)
                business.add("apertura", -(quote.license + quote.setup + quote.setup_igic))
                self.repository.save_in(connection, guild_id, business)
                move = BusinessMove(
                    user_id=user_id,
                    payments=(
                        BusinessCost("negocio:apertura", taxes=(("tasa", quote.license),)),
                        BusinessCost(
                            "negocio:acondicionamiento",
                            gone=quote.setup,
                            taxes=(("igic", quote.setup_igic),),
                        ),
                    ),
                    deposit=quote.deposit,
                )
                return business, move

            try:
                business, _paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(
                    f"No te llega: montarlo cuesta {quote.total} Y$ (con la fianza)."
                ) from error
            fee = await self._charge_fee(guild_id, business, now)
            return business, fee

    async def _charge_fee(self, guild_id: int, business: Business, now: float) -> int:
        """Cobra la cuota de autónomo de esta semana si aún no la ha pagado. Devuelve lo cobrado.

        Tratamiento fiscal: cotización al RETA (`charge_self_employed_fee`). La primera
        semana desde el alta, la tarifa plana (art. 38 ter de la Ley 20/2007). Una sola
        cuota por persona aunque también sea autónomo en la `pala`.
        """
        if await self.economy.self_employed_fee_paid(guild_id, business.user_id):
            return 0
        flat = now - business.opened_at < FLAT_RATE_DAYS * 86_400
        amount = FLAT_RATE_FEE if flat else SELF_EMPLOYED_FEE
        charged, _balance = await self.economy.charge_self_employed_fee(
            guild_id, business.user_id, amount=amount, concept="autonomos"
        )
        if charged:
            fresh = await self.repository.business(guild_id, business.user_id)
            if fresh is not None:
                fresh.roll_over(*self._today(now))
                fresh.add("cuota", -charged)
                await self.repository.save(guild_id, fresh)
        return charged

    async def restyle(
        self,
        guild_id: int,
        user_id: int,
        *,
        name: str | None = None,
        palette: int | None = None,
        emblem: int | None = None,
        price: str | None = None,
    ) -> Business:
        """Cambia el nombre, el rótulo o el nivel de precios. No mueve dinero.

        Raises:
            BusinessError: Sin negocio o con algo que no existe.
        """
        async with self._lock(guild_id, user_id):
            business = await self.business(guild_id, user_id)
            if business is None:
                raise BusinessError("No tienes negocio.")
            if name is not None:
                try:
                    business.name = clean_name(name)
                except ValueError as error:
                    raise BusinessError(str(error)) from error
            if palette is not None:
                if not 0 <= palette < len(PALETTES):
                    raise BusinessError("Esa paleta no existe.")
                business.palette = palette
            if emblem is not None:
                if not 0 <= emblem < len(EMBLEMS):
                    raise BusinessError("Ese emblema no existe.")
                business.emblem = emblem
            if price is not None:
                if price not in PRICE_BY_KEY:
                    raise BusinessError("Ese nivel de precios no existe.")
                business.price = price
            return await self.repository.save(guild_id, business)

    async def take_news(self, guild_id: int, user_id: int) -> list[str]:
        """Los avisos guardados (las noches) y los borra."""
        async with self._lock(guild_id, user_id):
            business = await self.repository.business(guild_id, user_id)
            if business is None or not business.news:
                return []
            news, business.news = business.news, []
            await self.repository.save(guild_id, business)
            return news

    # -- Comprar ------------------------------------------------------------------------

    async def buy(
        self, guild_id: int, user_id: int, product: str, lots: int
    ) -> tuple[Business, int, int, int]:
        """Compra `lots` lotes de `product` al mayorista.

        Returns:
            `(el negocio, base, IGIC, saldo final)`.

        Raises:
            BusinessError: Sin negocio, con algo que no vende o si no le llega.
        """
        if not 1 <= lots <= MAX_LOTS:
            raise BusinessError(f"Se compran de 1 a {MAX_LOTS} lotes de una vez.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            current = await self.repository.business(guild_id, user_id)
            if current is None:
                raise BusinessError("No tienes negocio.")
            item = current.sector_info.product(product)
            if item is None:
                raise BusinessError("Eso no se vende en tu negocio.")
            base, tax = wholesale(item, item.pack * lots)

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None or business.id != current.id:
                    raise BusinessError("Tu negocio ya no existe.")
                business.roll_over(today, week)
                business.stock[product] = business.stock.get(product, 0) + item.pack * lots
                business.add("compras", -(base + tax))
                self.repository.save_in(connection, guild_id, business)
                taxes = (("igic", tax),) if tax else ()
                cost = BusinessCost("negocio:compras", gone=base, taxes=taxes)
                return business, BusinessMove(user_id=user_id, payments=(cost,))

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(f"No te llega: son {base + tax} Y$ con el IGIC.") from error
            return business, base, tax, paid.balance

    # -- Un turno -----------------------------------------------------------------------

    async def start_turn(self, guild_id: int, user_id: int) -> Turn:
        """Abre un turno: gasta una franja y batería, y sortea la cola de clientes.

        No mueve dinero (se cobra al acabar, `finish_turn`). La franja y el cupo del barrio
        se apuntan ya: abrir es abrir, aunque luego no venga nadie más.

        Raises:
            BusinessError: Sin negocio o si algo impide abrir (`BusinessStatus.blocker`).
        """
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id)
            if status is None:
                raise BusinessError("No tienes negocio. Móntalo desde `pala`.")
            if (blocker := status.blocker) is not None:
                raise BusinessError(blocker)
            business, now = status.business, status.now
            hour = datetime.fromtimestamp(now, TIMEZONE).hour
            wanted = turn_clients(business, hour, self.rng)
            count = min(wanted, math.floor(status.pool_left))
            clients = make_clients(business, count, self.rng.randrange(2**31))
            if not clients:
                raise BusinessError("No tienes nada que vender: pasa por el 📦 almacén.")
            today = local_day(now)
            business.turns_today += 1
            business.last_turn_at = now
            if business.last_open_day != today.isoformat():
                yesterday = (today - timedelta(days=1)).isoformat()
                business.open_streak = (
                    business.open_streak + 1 if business.last_open_day == yesterday else 1
                )
                business.last_open_day = today.isoformat()
            await self.repository.begin_turn(guild_id, business, today, len(clients))
            before = await self.work.spend_battery(guild_id, user_id, SHIFT_COST)
            tired = before is not None and before < TIRED
            game = TurnGame(business, clients, now=now, tired=tired)
            return Turn(business=business, game=game, battery_before=before)

    async def finish_turn(self, guild_id: int, user_id: int, turn: Turn) -> TurnResult:
        """Cierra un turno: cobra las ventas y guarda el stock, la reputación y los habituales.

        Raises:
            BusinessError: Si el negocio ya no existe (cerró o lo desahuciaron a mitad).
        """
        async with self._lock(guild_id, user_id):
            now = self._clock()
            game = turn.game
            if not game.finished(now):
                game.finish(now)
            summary = game.summary()
            today, week = self._today(now)
            stars_before = turn.business.stars
            best: list[bool] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None or business.id != turn.business.id:
                    raise BusinessError("Tu negocio ya no existe: este turno no cuenta.")
                business.roll_over(today, week)
                for key, units in summary.units.items():
                    business.stock[key] = max(0, business.stock.get(key, 0) - units)
                bonus = PRICE_BY_KEY[business.price].reputation * summary.served
                business.reputation = max(
                    0.0, min(REPUTATION_MAX, business.reputation + summary.reputation + bonus)
                )
                business.regulars = apply_loyalty(business.regulars, summary.loyalty)
                business.sales_today += summary.sales
                business.clients_today += summary.served
                business.add("ventas", summary.sales)
                best.append(summary.sales > business.best_turn)
                business.best_turn = max(business.best_turn, summary.sales)
                self.repository.save_in(connection, guild_id, business)
                move = BusinessMove(
                    user_id=user_id, sales=summary.sales, sold_cost=summary.sold_cost
                )
                return business, move

            business, paid = await self.economy.business(guild_id, prepare)
            if paid.withheld:
                business.add("irpf", -paid.withheld)
                await self.repository.save(guild_id, business)
            started = datetime.fromtimestamp(game.started, TIMEZONE)
            return TurnResult(
                business=business,
                summary=summary,
                withheld=paid.withheld,
                garnished=paid.garnished,
                balance=paid.balance,
                stars_before=stars_before,
                best=bool(best and best[0]),
                hour=started.hour,
                weekday=started.weekday(),
            )

    # -- Las noches y la semana ---------------------------------------------------------

    async def close_nights(self, guild_id: int) -> list[NightRun]:
        """Cierra las noches que falten de cada negocio del servidor y cobra la cuota.

        Cada noche va en su propia transacción. Si un negocio falla, se sigue con el resto.
        """
        runs: list[NightRun] = []
        for business in await self.repository.businesses(guild_id):
            try:
                runs.extend(await self._close_business_nights(guild_id, business.user_id))
            except (OSError, sqlite3.Error):
                logger.exception("No se pudieron cerrar las noches del negocio %s", business.id)
        return runs

    async def _close_business_nights(self, guild_id: int, user_id: int) -> list[NightRun]:
        runs: list[NightRun] = []
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today = local_day(now)
            current = await self.repository.business(guild_id, user_id)
            if current is None:
                return runs
            last = (
                date.fromisoformat(current.billed_day)
                if current.billed_day
                else today - timedelta(days=1)
            )
            day = max(last + timedelta(days=1), today - timedelta(days=CATCH_UP_NIGHTS))
            while day < today:
                run = await self._close_night(guild_id, user_id, current.id, day, now)
                if run is None:
                    break
                runs.append(run)
                if run.evicted:
                    return runs
                day += timedelta(days=1)
            fresh = await self.repository.business(guild_id, user_id)
        if fresh is not None:
            await self._charge_fee(guild_id, fresh, now)
        return runs

    async def _close_night(
        self, guild_id: int, user_id: int, business_id: int, day: date, now: float
    ) -> NightRun | None:
        """Cobra una noche de un negocio y, si toca, lo desahucia. `None` si ya no existe."""
        holder: list[NightRun] = []
        evict: list[Business] = []

        def prepare(connection: sqlite3.Connection) -> tuple[Business | None, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id)
            if (
                business is None
                or business.id != business_id
                or business.billed_day >= day.isoformat()
            ):
                return None, BusinessMove(user_id=user_id)
            bill = nightly_bill(business)
            debt = business.rent_debt
            bills = []
            if debt:
                bills.append(BusinessCost("negocio:deuda_local", gone=debt))
            bills.append(
                BusinessCost(
                    "negocio:alquiler",
                    gone=bill.rent - bill.withholding,
                    taxes=(("igic", bill.rent_igic), ("retencion", bill.withholding)),
                )
            )
            bills.append(
                BusinessCost(
                    "negocio:suministros",
                    gone=bill.utilities,
                    taxes=(("igic", bill.utilities_igic),),
                )
            )
            due = debt + bill.total
            spoiled = spoil(business)
            if business.last_open_day != day.isoformat():
                drift(business)
            business.billed_day = day.isoformat()

            def settle(connection: sqlite3.Connection, unpaid: int) -> None:
                business.roll_over(day, week_of(day))
                business.add("local", -(due - unpaid))
                business.rent_debt = unpaid
                business.unpaid_nights = business.unpaid_nights + 1 if unpaid else 0
                if unpaid:
                    business.tell(
                        f"🧾 Anoche no llegó para el local: debes {unpaid} Y$ al casero "
                        f"({business.unpaid_nights} de {EVICTION_NIGHTS} noches)."
                    )
                if spoiled:
                    units = sum(spoiled.values())
                    business.tell(f"🗑️ Se te estropearon {units} unidades de lo perecedero.")
                self.repository.save_in(connection, guild_id, business)
                holder.append(
                    NightRun(user_id, business.name, day, due, unpaid, spoiled,
                             evicted=business.unpaid_nights >= EVICTION_NIGHTS)
                )  # fmt: skip
                if business.unpaid_nights >= EVICTION_NIGHTS:
                    evict.append(business)

            return business, BusinessMove(user_id=user_id, bills=tuple(bills), settle=settle)

        business, _paid = await self.economy.business(guild_id, prepare)
        if business is None:
            return None
        run = holder[0]
        if evict:
            await self._close(guild_id, user_id, evict[0].id, now, reason="desahucio")
        return run

    # -- Cerrar -------------------------------------------------------------------------

    async def close(self, guild_id: int, user_id: int) -> Closing:
        """Cierra el negocio: la fianza vuelve menos lo que se deba al casero.

        El stock se pierde (traspasarlo llegará con la fase 2). La cuota de la semana ya
        pagada no se devuelve.

        Raises:
            BusinessError: Si no tiene negocio.
        """
        async with self._lock(guild_id, user_id):
            business = await self.repository.business(guild_id, user_id)
            if business is None:
                raise BusinessError("No tienes negocio.")
            return await self._close(guild_id, user_id, business.id, self._clock(), reason="cierre")

    async def _close(
        self, guild_id: int, user_id: int, business_id: int, now: float, *, reason: str
    ) -> Closing:
        """Borra el negocio y mueve la fianza (sin coger el candado: lo tiene quien llama)."""

        def prepare(connection: sqlite3.Connection) -> tuple[Closing, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id)
            if business is None or business.id != business_id:
                raise BusinessError("No tienes negocio.")
            forfeit = min(business.deposit, business.rent_debt)
            refund = business.deposit - forfeit
            self.repository.close_in(connection, guild_id, business, now, reason)
            closing = Closing(business, refund, forfeit, 0)
            return closing, BusinessMove(user_id=user_id, refund=refund, forfeit=forfeit)

        closing, paid = await self.economy.business(guild_id, prepare)
        return Closing(closing.business, closing.refund, closing.forfeit, paid.balance)

    # -- Para `saldo` -------------------------------------------------------------------

    async def dues(self, guild_id: int, user_id: int) -> tuple[int, int, int]:
        """`(lo de esta noche, deuda del local, cuota de la semana por pagar)`. Solo lee."""
        business = await self.repository.business(guild_id, user_id)
        if business is None:
            return 0, 0, 0
        fee = 0
        if not await self.economy.self_employed_fee_paid(guild_id, user_id):
            flat = self._clock() - business.opened_at < FLAT_RATE_DAYS * 86_400
            fee = FLAT_RATE_FEE if flat else SELF_EMPLOYED_FEE
        return nightly_bill(business).total, business.rent_debt, fee
