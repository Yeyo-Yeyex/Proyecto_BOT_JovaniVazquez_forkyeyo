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
- Cerrar: la fianza vuelve, menos lo que se deba. Desde la fase 2, el stock y las mejoras se
  traspasan (con la clientela) o se liquidan a la baja (`closing_offer`).

Fase 2 (9.5.0): mejoras que se amortizan cada noche (`buy_equipment`), campañas de
publicidad (`start_campaign`), precio por producto (`set_price`), traslado a otro local o a
uno propio de `pisos` (`move`, `move_to_own`) y reseñas al acabar cada turno.
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
from typing import TYPE_CHECKING

from bot.repositories.business import BusinessRepository
from bot.repositories.housing import HousingError
from bot.services.agenda import Agenda
from bot.services.business import (
    EVICTION_NIGHTS,
    FLAT_RATE_DAYS,
    REPUTATION_MAX,
    Business,
    ClosingOffer,
    MoveQuote,
    NightlyBill,
    OpeningQuote,
    add_asset,
    add_reviews,
    amortize,
    clean_name,
    closing_offer,
    daily_pool,
    drift,
    forget_on_move,
    move_quote,
    nightly_bill,
    opening_quote,
    pool_factor,
    price_reputation,
    review_of,
    setup_asset,
    size_for,
    spoil,
    turn_clients,
    turn_reputation,
    wholesale,
)
from bot.services.business_catalog import (
    CAMPAIGN_BY_KEY,
    EMBLEMS,
    EQUIPMENT_BY_KEY,
    PALETTES,
    PRICE_BY_KEY,
    SECTOR_BY_KEY,
    SIZES,
)
from bot.services.business_games import (
    Client,
    TurnGame,
    TurnSummary,
    apply_loyalty,
    make_clients,
)
from bot.services.economy import (
    BusinessCost,
    BusinessMove,
    EconomyService,
    InsufficientFundsError,
)
from bot.services.housing_catalog import BARRIO_BY_KEY, Listing
from bot.services.levels import TIMEZONE, local_day
from bot.services.pala import WorkService
from bot.services.taxes import igic
from bot.services.telemetry import TimedLock
from bot.services.work import (
    BATTERY_MIN,
    FLAT_RATE_FEE,
    SELF_EMPLOYED_FEE,
    SHIFT_COST,
    TIRED,
    week_of,
)

if TYPE_CHECKING:
    from bot.repositories.housing import HousingRepository

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
        battery: Batería del dueño (es de la persona: la gasta aunque no tenga curro).
        now: Cuándo se hizo la foto.
    """

    business: Business
    agenda: Agenda
    bill: NightlyBill
    pool_left: float
    rivals: int
    battery: float
    now: float

    @property
    def blocker(self) -> str | None:
        """Lo que impide abrir un turno ahora, o `None`."""
        if not self.agenda.fits(1):
            return (
                "No te quedan franjas libres hoy: la jornada y las horas extra se las han comido."
            )
        if self.battery <= BATTERY_MIN:
            return "Estás tan reventado que te quedas dormido de pie. Descansa un poco."
        if not any(self.business.stock.get(p.key, 0) for p in self.business.sector_info.products):
            return "No tienes nada que vender: pasa por el 📦 almacén."
        if self.pool_left < 1:
            return "Hoy el barrio ya no da más de sí: todos tus clientes ya han comprado."
        return None


@dataclass(frozen=True, slots=True)
class TurnPlan:
    """El azar del próximo turno, sorteado antes de abrir (`BusinessService.plan`).

    Con él, el panel pinta el primer cliente antes de que se pulse 🔑 Abrir
    (`BusinessService.preview`) y `start_turn` saca la misma cola si nada ha cambiado.

    Attributes:
        luck: El redondeo de cuántos clientes vienen (`turn_clients`).
        seed: La semilla de la cola (`make_clients`).
    """

    luck: float
    seed: int


@dataclass(slots=True)
class Turn:
    """Un turno en marcha.

    Attributes:
        business: El negocio al empezar (con el turno ya apuntado).
        game: El minijuego.
        battery_before: Batería del dueño al empezar.
    """

    business: Business
    game: TurnGame
    battery_before: float

    @property
    def tired(self) -> bool:
        """Si empezó reventado."""
        return self.battery_before < TIRED


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Lo que dejó un turno.

    Attributes:
        business: El negocio después del turno.
        summary: Lo que pasó con cada cliente.
        withheld: IRPF a cuenta de las ventas.
        garnished: Lo embargado por deudas con Hacienda.
        balance: Saldo final del dueño.
        bank_garnished: Lo embargado por deudas con el banco (art. 607 LEC).
        stars_before: Estrellas al empezar.
        best: Si es su mejor turno en ventas.
        hour: Hora canaria a la que empezó.
        weekday: Día de la semana en que empezó (0 = lunes).
        day: Día (ISO, hora canaria) en que empezó.
        reviews: Las reseñas que dejaron sus clientes: `(quién, estrellas, frase)`.
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
    day: str = ""
    bank_garnished: int = 0
    reviews: tuple[tuple[str, int, int], ...] = ()

    @property
    def net(self) -> int:
        """Lo que entró en el bolsillo (ventas menos lo retenido y embargado)."""
        return self.summary.sales - self.withheld - self.garnished - self.bank_garnished


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
        evicted: Si el casero rescindió el contrato esta noche (o perdió su local propio).
        amortized: Lo amortizado esa noche.
    """

    user_id: int
    name: str
    day: date
    due: int
    unpaid: int
    spoiled: dict[str, int] = field(default_factory=dict)
    evicted: bool = False
    amortized: int = 0


@dataclass(frozen=True, slots=True)
class Closing:
    """Cómo acabó un negocio que se cierra.

    Attributes:
        business: El negocio cerrado.
        refund: Fianza que vuelve.
        forfeit: Fianza que se queda el casero.
        balance: Saldo final del dueño.
        traspaso: Si se traspasó (si no, se liquidó lo que quedaba).
        stock: Lo cobrado por el stock.
        proceeds: Lo cobrado por las mejoras, la obra y la clientela.
        gain_tax: IRPF de la ganancia patrimonial (19 %, base del ahorro).
        reason: `cierre`, `traspaso`, `desahucio` o `sin_local`.
    """

    business: Business
    refund: int
    forfeit: int
    balance: int
    traspaso: bool = False
    stock: int = 0
    proceeds: int = 0
    gain_tax: int = 0
    reason: str = "cierre"


@dataclass(frozen=True, slots=True)
class OwnLocal:
    """Un local o nave entera de alguien en `pisos`, para llevar ahí su negocio.

    Attributes:
        listing: El inmueble.
        status: Su estado en `pisos` (`vacio`, `negocio`…).
        size: El tamaño que tendría el negocio ahí (índice de `SIZES`), o `None` si no cabe.
        fits: Si sirve para su sector (local o nave) y está libre.
    """

    listing: Listing
    status: str
    size: int | None
    fits: bool


class BusinessService:
    """Casos de uso de los negocios.

    Args:
        repository: Persistencia de los negocios.
        economy: Única puerta al dinero.
        work: El curro del dueño (franjas y batería).
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
        rng: Azar de las colas de clientes; inyectable en pruebas.
        housing: Los inmuebles de `pisos`, para el local propio (fase 2). Sin ellos no hay
            traslado a un local tuyo.
    """

    def __init__(
        self,
        repository: BusinessRepository,
        economy: EconomyService,
        work: WorkService,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        housing: HousingRepository | None = None,
    ) -> None:
        self.repository = repository
        self.economy = economy
        self.work = work
        self.housing = housing
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
            pool_left=daily_pool(business.sector_info, business.barrio_info)
            * pool_factor(business, today)
            - used,
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
                business.add("apertura", -quote.license)
                setup_asset(business, quote.setup + quote.setup_igic)
                self.repository.save_in(connection, guild_id, business)
                move = BusinessMove(
                    user_id=user_id, payments=_opening_payments(quote), deposit=quote.deposit
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

    # -- Fase 2: mejoras, campañas y precios ---------------------------------------------

    async def buy_equipment(
        self, guild_id: int, user_id: int, key: str
    ) -> tuple[Business, int, int, int]:
        """Compra e instala una mejora del local.

        Tratamiento fiscal: compra con IGIC (al Estado); la base desaparece. No es gasto el
        día que se paga: base más IGIC (que el pequeño empresario no deduce) se amortizan
        cada noche (`business.amortize`, ver `EconomyService.business`).

        Returns:
            `(el negocio, base, IGIC, saldo final)`.

        Raises:
            BusinessError: Sin negocio, si no es para su sector, ya la tiene o no le llega.
        """
        item = EQUIPMENT_BY_KEY.get(key)
        if item is None:
            raise BusinessError("Esa mejora no existe.")
        base, tax = item.cost, igic(item.cost)
        async with self._lock(guild_id, user_id):
            today, week = self._today(self._clock())

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                if business.sector not in item.sectors:
                    raise BusinessError("Esa mejora no es para tu negocio.")
                if key in business.equipment:
                    raise BusinessError("Ya la tienes instalada.")
                business.roll_over(today, week)
                business.equipment = [*business.equipment, key]
                add_asset(business, key, base + tax, item.kind)
                self.repository.save_in(connection, guild_id, business)
                cost = BusinessCost(
                    "negocio:mejora", gone=base, taxes=(("igic", tax),), expense=False
                )
                return business, BusinessMove(user_id=user_id, payments=(cost,))

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(f"No te llega: son {base + tax} Y$ con el IGIC.") from error
            return business, base, tax, paid.balance

    async def start_campaign(
        self, guild_id: int, user_id: int, key: str
    ) -> tuple[Business, int, int, int]:
        """Lanza una campaña de publicidad: más clientes y más cupo unos días.

        Tratamiento fiscal: servicio con IGIC general (al Estado); la base desaparece. Es
        gasto entero de la actividad el día que se paga (art. 15.e LIS, por el art. 28.1
        LIRPF, y criterio de cobros y pagos).

        Returns:
            `(el negocio, base, IGIC, saldo final)`.

        Raises:
            BusinessError: Sin negocio, con otra campaña en marcha o si no le llega.
        """
        campaign = CAMPAIGN_BY_KEY.get(key)
        if campaign is None:
            raise BusinessError("Esa campaña no existe.")
        base, tax = campaign.cost, igic(campaign.cost)
        async with self._lock(guild_id, user_id):
            today, week = self._today(self._clock())

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                if business.campaign and business.campaign_until >= today.isoformat():
                    raise BusinessError("Ya tienes una campaña en marcha: espera a que acabe.")
                business.roll_over(today, week)
                business.campaign = key
                business.campaign_until = (today + timedelta(days=campaign.days - 1)).isoformat()
                business.add("marketing", -(base + tax))
                self.repository.save_in(connection, guild_id, business)
                cost = BusinessCost("negocio:publicidad", gone=base, taxes=(("igic", tax),))
                return business, BusinessMove(user_id=user_id, payments=(cost,))

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(f"No te llega: son {base + tax} Y$ con el IGIC.") from error
            return business, base, tax, paid.balance

    async def set_price(
        self, guild_id: int, user_id: int, product: str, level: str | None
    ) -> Business:
        """Pone el nivel de precios de un producto (`None`: el general). No mueve dinero.

        Raises:
            BusinessError: Sin negocio o con algo que no existe.
        """
        async with self._lock(guild_id, user_id):
            business = await self.business(guild_id, user_id)
            if business is None:
                raise BusinessError("No tienes negocio.")
            if business.sector_info.product(product) is None:
                raise BusinessError("Eso no se vende en tu negocio.")
            if level is not None and level not in PRICE_BY_KEY:
                raise BusinessError("Ese nivel de precios no existe.")
            prices = dict(business.prices)
            if level is None or level == business.price:
                prices.pop(product, None)
            else:
                prices[product] = level
            business.prices = prices
            return await self.repository.save(guild_id, business)

    # -- Fase 2: trasladarse ----------------------------------------------------------

    def move_quote(self, business: Business, barrio: str, size: int) -> MoveQuote:
        """Lo que cuesta trasladar `business` a un local alquilado.

        Raises:
            BusinessError: Si el barrio o el tamaño no existen.
        """
        if barrio not in BARRIO_BY_KEY or not 0 <= size < len(SIZES):
            raise BusinessError("Ese local no existe.")
        return move_quote(business, BARRIO_BY_KEY[barrio], size)

    async def own_locals(self, guild_id: int, user_id: int) -> list[OwnLocal]:
        """Los locales y naves enteros suyos en `pisos`, con si sirven para su negocio."""
        business = await self.repository.business(guild_id, user_id)
        if business is None or self.housing is None:
            return []
        housing = self.housing

        def read(connection: sqlite3.Connection) -> list[tuple[Listing, str]]:
            return housing.business_locals_in(connection, guild_id, user_id)

        sector = business.sector_info
        out = []
        for listing, status in await self.repository.read(read):
            size = size_for(sector, listing.m2) if listing.kind == sector.kind else None
            free = status == "vacio" or listing.key == business.own_listing
            out.append(OwnLocal(listing, status, size, size is not None and free))
        return out

    async def move(
        self, guild_id: int, user_id: int, barrio: str, size: int
    ) -> tuple[Business, MoveQuote, int]:
        """Traslada el negocio a un local alquilado de `barrio` y tamaño `size`.

        Tratamiento fiscal: como montarlo (`open`): la tasa de la comunicación previa del
        traslado (art. 34.2 de la Ley 7/2011 de Canarias), el acondicionamiento (se
        amortiza) y la fianza nueva. La del local de ahora vuelve, menos lo que se deba al
        casero; lo que no cubra se paga entero antes de irse. Si deja un local propio, ese
        vuelve a estar vacío en `pisos`. La obra del local que deja se queda allí.

        Returns:
            `(el negocio, lo que costó, saldo final)`.

        Raises:
            BusinessError: Sin negocio, si es el mismo local o si no le llega.
        """
        async with self._lock(guild_id, user_id):
            current = await self.business(guild_id, user_id)
            if current is None:
                raise BusinessError("No tienes negocio.")
            quote = self.move_quote(current, barrio, size)
            if quote.same_barrio and size == current.size and not current.own_listing:
                raise BusinessError("Ya estás en ese local.")
            return await self._move(guild_id, user_id, current, quote, barrio, None)

    async def move_to_own(
        self, guild_id: int, user_id: int, key: str
    ) -> tuple[Business, MoveQuote, int]:
        """Traslada el negocio a un local o nave entera suya de `pisos`: sin alquiler ni fianza.

        Tratamiento fiscal: la tasa del traslado y el acondicionamiento, como `move`. El
        local queda afecto a la actividad (`housing.OWN_BUSINESS`): no se alquila, no se
        vende y no imputa renta (art. 85.1 LIRPF). Sus recibos (IBI, comunidad, seguro) los
        sigue pagando `pisos` (simplificación: en la vida real serían gasto de la actividad).

        Raises:
            BusinessError: Sin negocio, si el local no sirve o si no le llega.
        """
        if self.housing is None:
            raise BusinessError("Ahora mismo no se puede mirar `pisos`.")
        async with self._lock(guild_id, user_id):
            current = await self.business(guild_id, user_id)
            if current is None:
                raise BusinessError("No tienes negocio.")
            if current.own_listing == key:
                raise BusinessError("Ya estás en ese local.")
            local = next(
                (o for o in await self.own_locals(guild_id, user_id) if o.listing.key == key), None
            )
            if local is None:
                raise BusinessError("Ese local no es entero tuyo.")
            if local.listing.kind != current.sector_info.kind:
                kind = "una nave" if current.sector_info.kind == "nave" else "un local"
                raise BusinessError(f"Tu negocio necesita {kind}.")
            if local.size is None:
                raise BusinessError("No cabe: el local es demasiado pequeño para tu negocio.")
            barrio = BARRIO_BY_KEY[local.listing.barrio]
            quote = move_quote(current, barrio, local.size, own=True)
            return await self._move(guild_id, user_id, current, quote, barrio.key, key)

    async def _move(
        self,
        guild_id: int,
        user_id: int,
        current: Business,
        quote: MoveQuote,
        barrio: str,
        own: str | None,
    ) -> tuple[Business, MoveQuote, int]:
        """Mueve el dinero y el negocio de un traslado (con el candado ya cogido)."""
        now = self._clock()
        today, week = self._today(now)
        housing = self.housing

        def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id)
            if business is None or business.id != current.id:
                raise BusinessError("Tu negocio ya no existe.")
            business.roll_over(today, week)
            forfeit = min(business.deposit, business.rent_debt)
            owed = business.rent_debt - forfeit
            if housing is not None and business.own_listing:
                housing.free_business_local_in(
                    connection, guild_id, user_id, business.own_listing, today, now
                )
            if own is not None:
                assert housing is not None
                try:
                    housing.occupy_business_local_in(connection, guild_id, user_id, own, today, now)
                except HousingError as error:
                    raise BusinessError(str(error)) from error
            if barrio != business.barrio:
                forget_on_move(business)
            opening = quote.opening
            refund = business.deposit - forfeit
            business.barrio, business.size, business.own_listing = barrio, quote.size, own or ""
            business.deposit, business.rent_debt, business.unpaid_nights = opening.deposit, 0, 0
            business.assets = [a for a in business.assets if a["key"] != "obra"]
            setup_asset(business, opening.setup + opening.setup_igic)
            business.add("apertura", -opening.license)
            if owed:
                business.add("local", -owed)
            business.tell(f"🚚 Te has trasladado a {BARRIO_BY_KEY[barrio].name}.")
            self.repository.save_in(connection, guild_id, business)
            payments = (
                *((BusinessCost("negocio:deuda_local", gone=owed),) if owed else ()),
                *_opening_payments(opening),
            )
            move = BusinessMove(
                user_id=user_id,
                refund=refund,
                forfeit=forfeit,
                payments=payments,
                deposit=opening.deposit,
            )
            return business, move

        try:
            business, paid = await self.economy.business(guild_id, prepare)
        except InsufficientFundsError as error:
            raise BusinessError(
                f"No te llega: el traslado cuesta {quote.total} Y$ (con la fianza nueva)."
            ) from error
        return business, quote, paid.balance

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

    def plan(self) -> TurnPlan:
        """Sortea el azar del próximo turno (lo gasta `start_turn`)."""
        return TurnPlan(self.rng.random(), self.rng.randrange(2**31))

    def preview(self, status: BusinessStatus, plan: TurnPlan) -> TurnGame | None:
        """El turno que saldría abriendo ahora con `plan`, sin guardar nada.

        `None` si ahora no se puede abrir. Es lo que pinta el panel antes del clic.
        """
        if status.blocker is not None:
            return None
        clients = self._queue(status, plan)
        if not clients:
            return None
        return TurnGame(status.business, clients, now=status.now, tired=status.battery < TIRED)

    @staticmethod
    def _queue(status: BusinessStatus, plan: TurnPlan) -> list[Client]:
        """La cola de un turno que empieza en el momento de `status`."""
        business = status.business
        hour = datetime.fromtimestamp(status.now, TIMEZONE).hour
        wanted = turn_clients(business, hour, plan.luck, local_day(status.now))
        count = min(wanted, math.floor(status.pool_left))
        return make_clients(business, count, plan.seed)

    async def start_turn(self, guild_id: int, user_id: int, plan: TurnPlan | None = None) -> Turn:
        """Abre un turno: gasta una franja y batería, y sortea la cola de clientes.

        Con `plan` (el que usó el panel para pintar el primer cliente) la cola sale de ese
        azar; sin él, se sortea ahora. No mueve dinero (se cobra al acabar, `finish_turn`).
        La franja y el cupo del barrio se apuntan ya: abrir es abrir, aunque luego no venga
        nadie más.

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
            clients = self._queue(status, plan or self.plan())
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
            game = TurnGame(business, clients, now=now, tired=before < TIRED)
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
            reviews: list[tuple[str, int, int]] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None or business.id != turn.business.id:
                    raise BusinessError("Tu negocio ya no existe: este turno no cuenta.")
                business.roll_over(today, week)
                for key, units in summary.units.items():
                    business.stock[key] = max(0, business.stock.get(key, 0) - units)
                change = turn_reputation(
                    business.sector_info,
                    summary.reputation,
                    summary.served,
                    price_reputation(business),
                )
                business.reputation = max(0.0, min(REPUTATION_MAX, business.reputation + change))
                business.regulars = apply_loyalty(business.regulars, summary.loyalty)
                business.sales_today += summary.sales
                business.clients_today += summary.served
                business.add("ventas", summary.sales)
                best.append(summary.sales > business.best_turn)
                business.best_turn = max(business.best_turn, summary.sales)
                # Reseñas con un azar propio del turno: no gastan el de las colas.
                rng = random.Random(f"{business.seed}:{game.started}")
                left = [r for o in summary.outcomes if (r := review_of(o, rng)) is not None]
                add_reviews(business, today, left)
                reviews.extend(left)
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
                day=started.date().isoformat(),
                bank_garnished=paid.bank_garnished,
                reviews=tuple(reviews),
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
        evict: list[str] = []

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
            if bill.fees:
                bills.append(BusinessCost("negocio:terraza", taxes=(("tasa", bill.fees),)))
            due = debt + bill.total
            spoiled = spoil(business)
            if business.last_open_day != day.isoformat():
                drift(business)
            business.billed_day = day.isoformat()
            amortized = amortize(business)
            if business.campaign and business.campaign_until <= day.isoformat():
                name = CAMPAIGN_BY_KEY[business.campaign].name.lower()
                business.tell(f"📣 Se acabó tu campaña: {name}.")
                business.campaign, business.campaign_until = "", ""
            lost_local = (
                bool(business.own_listing)
                and housing is not None
                and not housing.business_local_ok_in(
                    connection, guild_id, user_id, business.own_listing
                )
            )

            def settle(connection: sqlite3.Connection, unpaid: int) -> None:
                business.roll_over(day, week_of(day))
                business.add("local", -(due - unpaid))
                business.add("amortizacion", -amortized)
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
                gone = business.unpaid_nights >= EVICTION_NIGHTS or lost_local
                holder.append(
                    NightRun(user_id, business.name, day, due, unpaid, spoiled,
                             evicted=gone, amortized=amortized)
                )  # fmt: skip
                if gone:
                    evict.append("sin_local" if lost_local else "desahucio")

            return business, BusinessMove(
                user_id=user_id, bills=tuple(bills), settle=settle, amortization=amortized
            )

        housing = self.housing
        business, _paid = await self.economy.business(guild_id, prepare)
        if business is None:
            return None
        run = holder[0]
        if evict:
            await self._close(guild_id, user_id, business.id, now, reason=evict[0])
        return run

    # -- Cerrar -------------------------------------------------------------------------

    def offer(self, business: Business) -> ClosingOffer:
        """Lo que daría cerrar ahora: traspasándolo o liquidando lo que queda."""
        return closing_offer(business, self._clock())

    async def close(self, guild_id: int, user_id: int, *, traspaso: bool = False) -> Closing:
        """Cierra el negocio: traspasándolo o liquidando el stock y las mejoras.

        La fianza vuelve menos lo que se deba al casero. Tratamiento fiscal de lo cobrado en
        `EconomyService.business` («Traspasar»). La cuota de la semana ya pagada no se
        devuelve.

        Raises:
            BusinessError: Si no tiene negocio o nadie quiere traspasarlo.
        """
        async with self._lock(guild_id, user_id):
            business = await self.repository.business(guild_id, user_id)
            if business is None:
                raise BusinessError("No tienes negocio.")
            if traspaso and not self.offer(business).can_traspasar:
                raise BusinessError(
                    "Nadie quiere quedárselo: hace falta una semana abierto, 3 estrellas y "
                    "un local alquilado."
                )
            reason = "traspaso" if traspaso else "cierre"
            return await self._close(guild_id, user_id, business.id, self._clock(), reason=reason)

    async def _close(
        self, guild_id: int, user_id: int, business_id: int, now: float, *, reason: str
    ) -> Closing:
        """Borra el negocio y mueve la fianza y lo que se cobra (el candado lo tiene quien llama).

        Si no es un traspaso (cierre, desahucio, sin local), se liquida a la baja.
        """
        traspaso = reason == "traspaso"
        housing = self.housing
        today = local_day(now)

        def prepare(connection: sqlite3.Connection) -> tuple[Closing, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id)
            if business is None or business.id != business_id:
                raise BusinessError("No tienes negocio.")
            offer = closing_offer(business, now)
            forfeit = min(business.deposit, business.rent_debt)
            refund = business.deposit - forfeit
            if traspaso:
                stock, proceeds = offer.stock, offer.book + offer.goodwill
                gain = offer.goodwill
            else:
                stock, proceeds = offer.liquidation_stock, offer.liquidation_equipment
                gain = proceeds - offer.equipment_book
            if housing is not None and business.own_listing:
                housing.free_business_local_in(
                    connection, guild_id, user_id, business.own_listing, today, now
                )
            self.repository.close_in(connection, guild_id, business, now, reason)
            closing = Closing(
                business, refund, forfeit, 0, traspaso, stock, proceeds, reason=reason
            )
            move = BusinessMove(
                user_id=user_id,
                refund=refund,
                forfeit=forfeit,
                sales=stock,
                sold_cost=offer.stock,
                proceeds=proceeds,
                gain=gain,
            )
            return closing, move

        closing, paid = await self.economy.business(guild_id, prepare)
        return Closing(
            closing.business,
            closing.refund,
            closing.forfeit,
            paid.balance,
            closing.traspaso,
            closing.stock,
            closing.proceeds,
            paid.gain_tax,
            closing.reason,
        )

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


def _opening_payments(quote: OpeningQuote) -> tuple[BusinessCost, ...]:
    """Los pagos de abrir un local: la tasa (gasto) y el acondicionamiento (se amortiza)."""
    return (
        BusinessCost("negocio:apertura", taxes=(("tasa", quote.license),)),
        BusinessCost(
            "negocio:acondicionamiento",
            gone=quote.setup,
            taxes=(("igic", quote.setup_igic),),
            expense=False,
        ),
    )
