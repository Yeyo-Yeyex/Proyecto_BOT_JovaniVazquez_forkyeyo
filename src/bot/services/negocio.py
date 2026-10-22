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

Préstamos al negocio (9.7.0, `loan_desk`, `take_loan`, `repay_loan`): los pide el dueño en
`banco` (🏪 Negocio, también desde la portada). Entran en la caja del negocio y su noche cobra
las cuotas después del local (y de Sociedades): sus intereses y la comisión son gasto. Al
constituir la sociedad y al cerrar, lo que no pague la caja se lo queda su dueño como préstamo
personal. Reglas en `bot.services.loans` y `HousingRepository.take_business_loan_in`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bot.repositories.business import BusinessRepository
from bot.repositories.economy import BANK_LOAN_CONCEPT, company_account
from bot.repositories.housing import BusinessLoanDesk, HousingError
from bot.repositories.housing_finance import Loan, business_loans_in
from bot.services import business_sl as sl
from bot.services import loans as lending
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
    book_value,
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
    stock_value,
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
    BalanceLimitError,
    BusinessCost,
    BusinessMove,
    BusinessPaid,
    EconomyService,
    InsufficientFundsError,
)
from bot.services.housing_catalog import BARRIO_BY_KEY, Listing
from bot.services.housing_market import HousingService
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


def _account(business: Business) -> int | None:
    """La caja que paga y cobra por el negocio: la de su sociedad, o `None` (su dueño)."""
    return company_account(business.id) if business.is_company else None


def _broke(business: Business, total: int) -> str:
    """El aviso de que no llega, de quien sea la caja."""
    if business.is_company:
        return (
            f"La caja de la sociedad no llega: son {total} Y$. Aporta o préstale dinero desde 🏢 "
            "Sociedad."
        )
    return f"No te llega: son {total} Y$ con el IGIC."


@dataclass(frozen=True, slots=True)
class CompanyView:
    """Lo que enseña la pantalla 🏢 Sociedad.

    Attributes:
        business: El negocio.
        cash: Lo que hay en la caja de la sociedad (0 si es de autónomo).
        equity: Su patrimonio neto.
        dividend_room: Lo más que se puede repartir ahora.
        retention: La retención del sueldo de administrador ahora.
        blocked: Por qué no puede administrar una sociedad su dueño (incompatibilidad), o `None`.
        dissolution: Si está en causa de disolución por pérdidas.
        bank: Lo que debe al banco de sus préstamos.
    """

    business: Business
    cash: int
    equity: int
    dividend_room: int
    retention: float
    blocked: sl.Incompatibility | None
    dissolution: bool
    bank: int = 0


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
        corporate: El Impuesto sobre Sociedades de la semana que se cerró esa noche (fase 3):
            `{"base", "compensated", "tax", "rate", "reserve", "profit"}`, o `None`.
        refund: El reintegro de la pluriactividad que le llegó esa noche (fase 3).
        loans: Cómo fue la cuota de cada préstamo al negocio: `pagada`, `sin_pagar`,
            `devuelto` (la última) o `vencido` (`HousingRepository.settle_business_night_in`).
    """

    user_id: int
    name: str
    day: date
    due: int
    unpaid: int
    spoiled: dict[str, int] = field(default_factory=dict)
    evicted: bool = False
    amortized: int = 0
    corporate: dict[str, Any] | None = None
    refund: int = 0
    loans: tuple[str, ...] = ()


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
        company: Si era una sociedad (fase 3): lo de abajo es de su liquidación.
        liquidated: La cuota de liquidación que le llegó al socio.
        loan_repaid: Lo que se le devolvió de su préstamo.
        covered: Lo que pagó él de las deudas de la sociedad.
        derived_debt: Lo que se le queda como deuda con Hacienda (art. 43.1.b LGT).
        corporate_tax: El último Impuesto sobre Sociedades.
        bank_paid: Lo que pagó la caja de la sociedad de sus préstamos al liquidarla.
        bank_moved: Lo que queda de los préstamos del negocio, ahora personales de su dueño.
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
    company: bool = False
    liquidated: int = 0
    loan_repaid: int = 0
    covered: int = 0
    derived_debt: int = 0
    corporate_tax: int = 0
    bank_paid: int = 0
    bank_moved: int = 0


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
        #: Lo que ve el banco de los ingresos del dueño (los préstamos al negocio).
        self.lender = HousingService(housing, economy, clock=clock) if housing else None
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
                move = BusinessMove(user_id=user_id, payments=(cost,), account=_account(business))
                return business, move

            current = await self.repository.business(guild_id, user_id)
            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(_broke(current, base + tax) if current else "") from error
            return business, base, tax, paid.company if business.is_company else paid.balance

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
                move = BusinessMove(user_id=user_id, payments=(cost,), account=_account(business))
                return business, move

            current = await self.repository.business(guild_id, user_id)
            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(_broke(current, base + tax) if current else "") from error
            return business, base, tax, paid.company if business.is_company else paid.balance

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
            if current.is_company:
                raise BusinessError(
                    "Tu sociedad no puede usar un local tuyo sin pagarte un alquiler (operación "
                    "vinculada, art. 18 LIS): de momento, solo locales alquilados."
                )
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
                account=_account(business),
            )
            return business, move

        try:
            business, paid = await self.economy.business(guild_id, prepare)
        except InsufficientFundsError as error:
            raise BusinessError(
                _broke(current, quote.total)
                if current.is_company
                else f"No te llega: el traslado cuesta {quote.total} Y$ (con la fianza nueva)."
            ) from error
        return business, quote, paid.balance

    # -- Fase 3: la sociedad limitada ---------------------------------------------------

    async def incompatible(
        self, guild_id: int, user_id: int, sector: str
    ) -> sl.Incompatibility | None:
        """Lo que impide a un miembro administrar una sociedad por su curro (o `None`).

        De excedencia no hay incompatibilidad: no está en servicio activo.
        """
        contract = await self.work.repository.contract(guild_id, user_id)
        if contract is None or contract.on_excedencia:
            return None
        return sl.incompatibility(contract.job, contract.branch, sector)

    async def company_view(self, guild_id: int, user_id: int) -> CompanyView | None:
        """La foto de la sociedad (o de lo que costaría constituirla), o `None` sin negocio."""
        business = await self.business(guild_id, user_id)
        if business is None:
            return None
        blocked = await self.incompatible(guild_id, user_id, business.sector)
        if not business.is_company:
            return CompanyView(business, 0, 0, 0, sl.ADMIN_RETENTION, blocked, False)
        cash = await self.economy.account_balance(guild_id, company_account(business.id))
        bank = await self._bank_owed(guild_id, business.id)
        net = _equity(business, cash, bank)
        sales = business.last_week.get("ventas", business.week_totals.get("ventas", 0))
        return CompanyView(
            business,
            cash,
            net,
            sl.dividend_room(business.company, cash),
            sl.admin_retention(sales),
            blocked,
            sl.in_dissolution(business.company, net),
            bank,
        )

    async def _bank_owed(self, guild_id: int, business_id: int) -> int:
        """Lo que debe un negocio al banco de sus préstamos."""
        if self.housing is None:
            return 0
        return sum(loan.owed for loan in await self.housing.business_loans(guild_id, business_id))

    async def constitute(self, guild_id: int, user_id: int, capital: int) -> tuple[Business, int]:
        """Convierte el negocio de un autónomo en una sociedad limitada unipersonal.

        Tratamiento fiscal: la constitución está exenta del ITP-AJD (art. 45.I.B.11 del
        TRLITPAJD). El dueño mete en la caja nueva el capital y lo que cuestan la notaría y el
        registro con su IGIC (aportación de socio), y la sociedad los paga: son su primer
        gasto. El negocio (stock, mejoras, obra y fianza) pasa a la sociedad por lo que valía
        en sus cuentas: es la aportación de una rama de actividad, neutral en el IRPF (art. 87
        LIS) y no sujeta al IGIC (art. 9.1.º de la Ley 20/1991). Desde ahí, el negocio tributa
        en el Impuesto sobre Sociedades y su dueño, por lo que cobre de él. Sigue en el RETA
        como autónomo societario (art. 305.2.b LGSS), con la misma cuota.

        Returns:
            `(el negocio, lo que salió del monedero)`.

        Raises:
            BusinessError: Sin negocio, si ya es sociedad, si su curro no lo deja, si está en
                un local propio o si no le llega.
        """
        if capital not in sl.CAPITALS:
            raise BusinessError("Ese capital no está entre los que se pueden escriturar.")
        fees = sl.constitution_fees(capital)
        fees_igic = igic(fees)
        async with self._lock(guild_id, user_id):
            current = await self.business(guild_id, user_id)
            if current is None:
                raise BusinessError("No tienes negocio.")
            if current.is_company:
                raise BusinessError("Tu negocio ya es una sociedad limitada.")
            if current.own_listing:
                raise BusinessError(
                    "Tu negocio está en un local tuyo: la sociedad tendría que alquilártelo "
                    "(operación vinculada, art. 18 LIS). Trasládalo a uno alquilado antes."
                )
            if blocked := await self.incompatible(guild_id, user_id, current.sector):
                raise BusinessError(
                    f"No puedes administrar una sociedad: {blocked.text} ({blocked.law})."
                )
            now = self._clock()
            today, week = self._today(now)
            total = capital + fees + fees_igic
            housing = self.housing

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None or business.id != current.id or business.is_company:
                    raise BusinessError("Tu negocio ha cambiado: vuelve a intentarlo.")
                business.roll_over(today, week)
                business.form = "sl"
                business.company = sl.new_company(capital, total, now)
                # Lo de esta semana antes de constituirla es del autónomo: no entra en Sociedades.
                business.company["offset_week"] = week
                business.company["offset"] = dict(business.week_totals)
                business.add("constitucion", -(fees + fees_igic))
                business.tell(f"🏢 {business.name} ya es una sociedad limitada.")
                if (
                    housing is not None
                    and housing.business_loans_to_owner_in(connection, guild_id, business.id, now)[
                        1
                    ]
                ):
                    # Lo prestó el banco al autónomo y se lo quedó él: siguen a su nombre.
                    business.tell(
                        "🏦 Los préstamos de tu negocio siguen a tu nombre, como personales: la "
                        "sociedad empieza sin deudas con el banco."
                    )
                self.repository.save_in(connection, guild_id, business)
                cost = BusinessCost(
                    "sociedad:constitucion", gone=fees, taxes=(("igic", fees_igic),), expense=False
                )
                move = BusinessMove(
                    user_id=user_id,
                    account=company_account(business.id),
                    contribution=total,
                    payments=(cost,),
                )
                return business, move

            try:
                business, _paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(
                    f"No te llega: el capital y la notaría suman {total} Y$."
                ) from error
            return business, total

    async def _company_move(
        self,
        guild_id: int,
        user_id: int,
        change: Callable[[Business], BusinessMove],
        broke: str,
    ) -> tuple[Business, Any]:
        """Una operación entre el dueño y su sociedad (con el candado y la sociedad comprobada)."""
        async with self._lock(guild_id, user_id):
            today, week = self._today(self._clock())

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None or not business.is_company:
                    raise BusinessError("Tu negocio no es una sociedad.")
                business.roll_over(today, week)
                move = change(business)
                self.repository.save_in(connection, guild_id, business)
                return business, move

            try:
                return await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(broke) from error

    async def contribute(self, guild_id: int, user_id: int, amount: int) -> tuple[Business, Any]:
        """Mete dinero en la caja de la sociedad como aportación de socio (no se devuelve).

        Tratamiento fiscal: una aportación de socio (cuenta 118 del PGC) no es ingreso de la
        sociedad ni gasto del dueño; exenta de operaciones societarias (art. 45.I.B.11 del
        TRLITPAJD). Sube lo aportado, que se resta de lo que cobre al liquidarla.
        """
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")

        def change(business: Business) -> BusinessMove:
            business.company["contributed"] += amount
            return BusinessMove(user_id=user_id, account=_account(business), contribution=amount)

        return await self._company_move(guild_id, user_id, change, "No te llega para aportar eso.")

    async def lend(self, guild_id: int, user_id: int, amount: int) -> tuple[Business, Any]:
        """Le presta dinero a la sociedad (se le devuelve cuando haya en caja).

        Tratamiento fiscal: sin intereses. Simplificación: entre una sociedad y su socio, el
        art. 18 LIS pide valorarlo a precio de mercado. Si la sociedad se liquida, el préstamo
        del socio cobra el último (es subordinado, arts. 281 y 283 TRLC).
        """
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")

        def change(business: Business) -> BusinessMove:
            business.company["loan"] += amount
            return BusinessMove(user_id=user_id, account=_account(business), loan_in=amount)

        return await self._company_move(guild_id, user_id, change, "No te llega para prestar eso.")

    async def repay(self, guild_id: int, user_id: int, amount: int) -> tuple[Business, Any]:
        """La sociedad le devuelve al dueño parte de su préstamo. No es renta: es su dinero."""
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")

        def change(business: Business) -> BusinessMove:
            if amount > business.company["loan"]:
                raise BusinessError(f"Solo te debe {business.company['loan']} Y$ del préstamo.")
            business.company["loan"] -= amount
            return BusinessMove(user_id=user_id, account=_account(business), loan_back=amount)

        return await self._company_move(
            guild_id, user_id, change, "La caja de la sociedad no tiene tanto."
        )

    async def pay_salary(self, guild_id: int, user_id: int, amount: int) -> tuple[Business, Any]:
        """La sociedad le paga a su administrador un sueldo de `amount` (bruto).

        Tratamiento fiscal: en el IRPF, rendimiento del trabajo (art. 17.2.e LIRPF) con la
        retención del 35 %, o del 19 % si la sociedad factura menos de 100.000 € al año (art.
        101.2 LIRPF y art. 80.1.3.º RIRPF); entra en la Renta del lunes, y en el IMV y el banco
        como una nómina. En Sociedades es gasto deducible: no es una liberalidad (art. 15.e LIS)
        porque los estatutos tipo del juego lo prevén (art. 217 LSC). Sin cotización aparte: el
        administrador ya paga su cuota de autónomo societario.

        Raises:
            BusinessError: Si su curro no le deja administrar o la caja no llega.
        """
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")
        business = await self.business(guild_id, user_id)
        if business is not None and (
            blocked := await self.incompatible(guild_id, user_id, business.sector)
        ):
            raise BusinessError(
                f"No puedes cobrar de administrador: {blocked.text} ({blocked.law})."
            )

        def change(business: Business) -> BusinessMove:
            sales = business.last_week.get("ventas", business.week_totals.get("ventas", 0))
            business.add("sueldo", -amount)
            business.company["paid_out"] += amount
            return BusinessMove(
                user_id=user_id,
                account=_account(business),
                salary=amount,
                salary_rate=sl.admin_retention(sales),
            )

        return await self._company_move(
            guild_id, user_id, change, "La caja de la sociedad no tiene tanto."
        )

    async def pay_dividend(self, guild_id: int, user_id: int, amount: int) -> tuple[Business, Any]:
        """La sociedad reparte `amount` de dividendo a su socio único.

        Tratamiento fiscal: rendimiento del capital mobiliario (art. 25.1.a LIRPF) de la base
        del ahorro (art. 46), con la retención del 19 % (art. 101.4). Simplificación: como las
        plusvalías del Rastro, ese 19 % es lo que paga, sin la escala del ahorro. Solo de lo
        que queda disponible tras el Impuesto sobre Sociedades y la reserva legal, y si el
        patrimonio neto no baja del capital (art. 273 LSC).

        Raises:
            BusinessError: Si no hay tanto para repartir.
        """
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")
        view = await self.company_view(guild_id, user_id)
        if view is None or not view.business.is_company:
            raise BusinessError("Tu negocio no es una sociedad.")
        room = min(view.dividend_room, max(0, view.equity - view.business.company["capital"]))
        if amount > room:
            raise BusinessError(
                f"Solo puedes repartir {room} Y$: lo que quedó tras Sociedades y la reserva "
                "legal, sin dejar el patrimonio por debajo del capital."
            )

        def change(business: Business) -> BusinessMove:
            business.company["retained"] -= amount
            business.company["paid_out"] += amount
            return BusinessMove(user_id=user_id, account=_account(business), dividend=amount)

        return await self._company_move(
            guild_id, user_id, change, "La caja de la sociedad no tiene tanto."
        )

    # -- Préstamos al negocio (`banco`, 🏪 Negocio) -------------------------------------

    async def loan_desk(
        self, guild_id: int, user_id: int
    ) -> tuple[Business, BusinessLoanDesk] | None:
        """El negocio y lo que ve el banco para prestarle, o `None` sin negocio o sin banco."""
        business = await self.business(guild_id, user_id)
        if business is None or self.housing is None or self.lender is None:
            return None
        income, payroll = await self.lender.lender_view(guild_id, user_id)
        desk = await self.housing.business_desk(
            guild_id, user_id, business.id, income=income, payroll=payroll,
            company_income=_company_income(business),
        )  # fmt: skip
        return business, desk

    async def take_loan(
        self, guild_id: int, user_id: int, *, principal: int, months: int, rate: float
    ) -> tuple[Business, Loan, lending.Quote, BusinessPaid]:
        """Firma un préstamo al negocio: lo prestado entra en su caja y la comisión sale.

        Tratamiento fiscal: lo prestado no es renta (se devuelve). La comisión de apertura,
        como los intereses de cada noche, es gasto de la actividad (art. 28.1 LIRPF, que remite
        al art. 15 LIS) o de la sociedad; el límite del art. 16 LIS a los gastos financieros
        no se alcanza por debajo de un millón de euros al año. No es crédito al consumo (art. 2
        de la Ley 16/2011), así que no hay desistimiento. El banco no existe: lo prestado se
        crea y lo que cobra desaparece.

        Args:
            rate: El tipo que enseñaba la oferta (si ha cambiado, no se firma).

        Raises:
            BusinessError: Sin negocio, con deudas con Hacienda o si el banco no presta (ver
                `HousingRepository.take_business_loan_in`).
        """
        housing, lender = self.housing, self.lender
        if housing is None or lender is None:
            raise BusinessError("El banco está cerrado ahora mismo.")
        if await self.economy.tax_debt(guild_id, user_id) > 0:
            # Lo prestado se embargaría al entrar: el banco no presta a quien debe a Hacienda.
            raise BusinessError(
                "Debes dinero a Hacienda y tendrías la cuenta embargada: el banco no presta. "
                "Liquida la deuda en `renta`."
            )
        income, payroll = await lender.lender_view(guild_id, user_id)
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            signed: list[tuple[Loan, lending.Quote]] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                business.roll_over(today, week)
                loan, offer = housing.take_business_loan_in(
                    connection, guild_id, user_id, business.id, principal=principal,
                    months=months, rate=rate, income=income, payroll=payroll,
                    company_income=_company_income(business), day=today, now=now,
                )  # fmt: skip
                business.add("intereses", -offer.fee)
                self.repository.save_in(connection, guild_id, business)
                signed.append((loan, offer))
                move = BusinessMove(
                    user_id=user_id,
                    account=_account(business),
                    bank_loan=principal,
                    payments=(BusinessCost(f"{BANK_LOAN_CONCEPT}apertura", gone=offer.fee),),
                )
                return business, move

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except HousingError as error:
                raise BusinessError(str(error)) from error
            except BalanceLimitError as error:
                raise BusinessError("No cabe más dinero en la caja del negocio.") from error
            loan, offer = signed[0]
            return business, loan, offer, paid

    async def repay_loan(
        self, guild_id: int, user_id: int, loan_id: int
    ) -> tuple[Business, Loan, int, BusinessPaid]:
        """Devuelve entero un préstamo del negocio desde su caja, con la compensación.

        Tratamiento fiscal: lo devuelto no es gasto; la compensación por amortizar antes
        (`loans.early_fee`), sí, como los intereses.

        Returns:
            `(el negocio, el préstamo como estaba, la compensación, el dinero)`.

        Raises:
            BusinessError: Si no es de su negocio, ya está cerrado o la caja no llega.
        """
        housing = self.housing
        if housing is None:
            raise BusinessError("El banco está cerrado ahora mismo.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            closed: list[tuple[Loan, int]] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                business.roll_over(today, week)
                loan, fee = housing.repay_business_loan_in(
                    connection, guild_id, business.id, loan_id, now
                )
                business.add("intereses", -fee)
                self.repository.save_in(connection, guild_id, business)
                closed.append((loan, fee))
                move = BusinessMove(
                    user_id=user_id,
                    account=_account(business),
                    payments=(
                        BusinessCost(
                            f"{BANK_LOAN_CONCEPT}amortizacion", gone=loan.owed, expense=False
                        ),
                        BusinessCost(f"{BANK_LOAN_CONCEPT}compensacion", gone=fee),
                    ),
                )
                return business, move

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except HousingError as error:
                raise BusinessError(str(error)) from error
            except InsufficientFundsError as error:
                # `prepare` ya corrió (y se deshizo): dice cuánto era.
                loan, fee = closed[0]
                whose = "La caja de la sociedad no llega" if loan.corporate else "No te llega"
                raise BusinessError(f"{whose}: son {loan.owed + fee} Y$.") from error
            loan, fee = closed[0]
            return business, loan, fee, paid

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
                move = BusinessMove(user_id=user_id, payments=(cost,), account=_account(business))
                return business, move

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(_broke(current, base + tax)) from error
            return business, base, tax, paid.company if business.is_company else paid.balance

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
                    user_id=user_id,
                    sales=summary.sales,
                    sold_cost=summary.sold_cost,
                    account=_account(business),
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
                if day.weekday() == 6 and (
                    back := await self._pluriactivity(guild_id, user_id, day)
                ):
                    runs[-1] = replace(run, refund=back)
                day += timedelta(days=1)
            fresh = await self.repository.business(guild_id, user_id)
        if fresh is not None:
            await self._charge_fee(guild_id, fresh, now)
        return runs

    async def _pluriactivity(self, guild_id: int, user_id: int, sunday: date) -> int:
        """El reintegro de la semana que acaba en `sunday` a quien cotizó por los dos lados.

        Tratamiento fiscal: el art. 313 LGSS (con el RDL 3/2026) devuelve el 50 % de lo que
        lo cotizado por cuenta ajena y en el RETA pase de 17.323,68 € al año, con el tope del
        50 % de lo pagado al RETA. Lo paga el Estado. Simplificación: no se apunta como renta
        (en la vida real reduce el gasto de la cuota que se dedujo). Una vez por semana.
        """
        business = await self.repository.business(guild_id, user_id)
        week = week_of(sunday)
        if business is None or business.pluri_week >= week:
            return 0
        business.pluri_week = week
        await self.repository.save(guild_id, business)
        start = datetime.combine(date.fromisoformat(week), datetime.min.time(), TIMEZONE)
        end = start + timedelta(days=7)
        general, reta = await self.economy.contributions(
            guild_id, user_id, start.timestamp(), end.timestamp()
        )
        refund = sl.pluriactivity_refund(general, reta)
        if refund:
            await self.economy.refund_contributions(guild_id, user_id, amount=refund)
            fresh = await self.repository.business(guild_id, user_id)
            if fresh is not None:
                fresh.tell(
                    f"💶 La Seguridad Social te devuelve {refund} Y$: cotizaste de más por la "
                    "pluriactividad (art. 313 LGSS)."
                )
                await self.repository.save(guild_id, fresh)
        return refund

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
            company = business.is_company
            books = sl.BOOKKEEPING_NIGHT + igic(sl.BOOKKEEPING_NIGHT) if company else 0
            week_result: dict[str, Any] | None = None
            old_tax = business.company.get("tax_debt", 0) if company else 0
            if company:
                # Primero lo que se debe a Hacienda; la gestoría, con el local.
                if old_tax:
                    bills.insert(
                        0,
                        BusinessCost(
                            "sociedad:hacienda:deuda",
                            taxes=(("sociedades", old_tax),),
                            expense=False,
                        ),
                    )
                bills.append(
                    BusinessCost(
                        "sociedad:gestoria",
                        gone=sl.BOOKKEEPING_NIGHT,
                        taxes=(("igic", books - sl.BOOKKEEPING_NIGHT),),
                    )
                )
                if day.weekday() == 6:
                    # La última noche de la semana fiscal, el Impuesto sobre Sociedades, con lo
                    # de esta noche ya devengado (aunque no llegue para pagarlo).
                    week = week_of(day)
                    business.roll_over(day, week)
                    info = business.company
                    offset = info["offset"] if info["offset_week"] == week else None
                    base = (
                        sl.tax_base(business.week_totals, offset) - bill.total - amortized - books
                    )
                    week_result = sl.settle_week(
                        info, base, weekly_sales=business.week_totals.get("ventas", 0)
                    )
                    if week_result["tax"]:
                        bills.append(
                            BusinessCost(
                                "sociedad:hacienda:is",
                                taxes=(("sociedades", week_result["tax"]),),
                                expense=False,
                            )
                        )
            # Las cuotas de sus préstamos, lo último: el banco cobra después del local y de
            # Hacienda. Los intereses son gasto; la parte de capital, no.
            installments = (
                housing.business_night_in(connection, guild_id, business.id, day)
                if housing is not None
                else []
            )
            for item in installments:
                bills.append(BusinessCost(f"{BANK_LOAN_CONCEPT}intereses", gone=item.interest))
                bills.append(
                    BusinessCost(
                        f"{BANK_LOAN_CONCEPT}cuota", gone=item.due - item.interest, expense=False
                    )
                )
            loan_events: list[str] = []

            def settle(connection: sqlite3.Connection, unpaid: int) -> None:
                business.roll_over(day, week_of(day))
                if installments:
                    assert housing is not None
                    unpaid = self._settle_loans_in(
                        connection, guild_id, business, installments, unpaid, now, loan_events
                    )
                if company:
                    # Lo que no llegó: primero el impuesto de esta noche, luego lo del local y,
                    # si aún falta, lo que ya se le debía a Hacienda (las facturas van por orden).
                    tax = week_result["tax"] if week_result else 0
                    unpaid_tax = min(tax, unpaid)
                    rest = unpaid - unpaid_tax
                    unpaid_old = max(0, rest - (due + books))
                    unpaid = rest - unpaid_old
                    business.company["tax_debt"] = unpaid_old + unpaid_tax
                    # Sociedades va por devengo: el local y la gestoría cuentan esta noche.
                    business.add("local", -(due - debt))
                    business.add("gestoria", -books)
                    if tax:
                        business.add("sociedades", -tax)
                    if week_result is not None:
                        business.company["last"] = {"week": week_of(day), **week_result}
                else:
                    business.add("local", -(due - unpaid))
                business.add("amortizacion", -amortized)
                business.rent_debt = unpaid
                business.unpaid_nights = business.unpaid_nights + 1 if unpaid else 0
                if unpaid:
                    whose = "la caja de la sociedad" if company else "para el local"
                    business.tell(
                        f"🧾 Anoche no llegó {whose}: debes {unpaid} Y$ al casero "
                        f"({business.unpaid_nights} de {EVICTION_NIGHTS} noches)."
                    )
                if spoiled:
                    units = sum(spoiled.values())
                    business.tell(f"🗑️ Se te estropearon {units} unidades de lo perecedero.")
                self.repository.save_in(connection, guild_id, business)
                gone = business.unpaid_nights >= EVICTION_NIGHTS or lost_local
                holder.append(
                    NightRun(user_id, business.name, day, due, unpaid, spoiled,
                             evicted=gone, amortized=amortized, corporate=week_result,
                             loans=tuple(loan_events))
                )  # fmt: skip
                if gone:
                    evict.append("sin_local" if lost_local else "desahucio")

            return business, BusinessMove(
                user_id=user_id,
                bills=tuple(bills),
                settle=settle,
                amortization=amortized,
                account=_account(business),
            )

        housing = self.housing
        business, paid = await self.economy.business(guild_id, prepare)
        if business is None:
            return None
        run = holder[0]
        if evict:
            await self._close(guild_id, user_id, business.id, now, reason=evict[0])
        elif business.is_company:
            await self._watch_losses(guild_id, business, paid.company, day)
        return run

    def _settle_loans_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        business: Business,
        installments: list[Any],
        unpaid: int,
        now: float,
        events: list[str],
    ) -> int:
        """Guarda las cuotas del banco de una noche y devuelve lo que no llegó de lo demás.

        Las cuotas van al final de los recibos, así que lo que no llegó es suyo primero, del
        último préstamo al primero. Los intereses cuentan en las cuentas de la semana: los de
        una sociedad por devengo (aunque no lleguen); los de un autónomo, lo pagado (caja).
        """
        assert self.housing is not None
        missing: dict[int, int] = {}
        for item in reversed(installments):
            take = min(unpaid, item.due)
            unpaid -= take
            if take:
                missing[item.loan.id] = take
        for item in installments:
            lost = missing.get(item.loan.id, 0)
            unpaid_interest = max(0, lost - (item.due - item.interest))
            booked = item.interest if business.is_company else item.interest - unpaid_interest
            business.add("intereses", -booked)
        results = self.housing.settle_business_night_in(
            connection, guild_id, installments, missing, now
        )
        for what, loan, amount in results:
            events.append(what)
            if what == "sin_pagar":
                business.tell(
                    f"🏦 Anoche no llegó para la cuota del préstamo #{loan.id}: debes "
                    f"{missing[loan.id]} Y$ al banco ({loan.unpaid_count + 1} de "
                    f"{lending.DEFAULT_INSTALLMENTS} cuotas; con todas, vence entero)."
                )
            elif what == "devuelto":
                business.tell(f"🏦 Pagada la última cuota del préstamo #{loan.id}.")
            elif what == "vencido" and business.is_company:
                # El banco reclama al socio, que lo avalaba; la sociedad le debe lo que pague
                # por ella (acción de reembolso, art. 1838 CC): es su préstamo de socio.
                business.company["loan"] += amount
                business.tell(
                    f"🏦 El banco da por vencido el préstamo #{loan.id} y te reclama a ti, que lo "
                    f"avalabas: debes {amount} Y$ al banco. Tu sociedad te lo debe a ti."
                )
            elif what == "vencido":
                business.tell(
                    f"🏦 El banco da por vencido el préstamo #{loan.id}: {amount} Y$ pasan a "
                    "deuda tuya con el banco, que se cobra cada noche y se embarga."
                )
        return unpaid

    async def _watch_losses(self, guild_id: int, business: Business, cash: int, day: date) -> None:
        """Apunta si la sociedad está en causa de disolución por pérdidas (art. 363.1.e LSC).

        Con dos noches (meses) así sin remediarla, su administrador responde de las deudas al
        liquidarla (art. 367 LSC, `business_sl.liable_for_all`).
        """
        info = business.company
        bank = await self._bank_owed(guild_id, business.id)
        cause = sl.in_dissolution(info, _equity(business, cash, bank))
        since = info.get("cause_since", "")
        if cause == bool(since):
            return
        info["cause_since"] = day.isoformat() if cause else ""
        if cause:
            business.tell(
                "⚠️ Las pérdidas dejan a tu sociedad por debajo de la mitad de su capital: es "
                "causa de disolución. Aporta dinero en dos noches o responderás de sus deudas."
            )
        await self.repository.save(guild_id, business)

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
        #: `(lo que pagó la caja a sus préstamos, lo que se queda el dueño)`.
        bank: list[tuple[int, int]] = []

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
            if business.is_company:
                info = business.company
                # El último Impuesto sobre Sociedades: lo de la semana hasta hoy, con la venta.
                offset = info["offset"] if info["offset_week"] == business.week else None
                base = sl.tax_base(business.week_totals, offset) + stock + proceeds - offer.book
                sales = business.week_totals.get("ventas", 0) + stock
                result = sl.settle_week(info, base, weekly_sales=sales)
                hacienda = info["tax_debt"] + result["tax"]
                owed = business.rent_debt - forfeit
                bills = []
                if hacienda:
                    bills.append(
                        BusinessCost(
                            "sociedad:hacienda:cierre",
                            taxes=(("sociedades", hacienda),),
                            expense=False,
                        )
                    )
                if owed:
                    bills.append(BusinessCost("negocio:deuda_local", gone=owed, expense=False))
                # Luego el banco (sus préstamos vencen al disolverla): lo que la caja no llegue
                # a pagar lo asume su socio, que lo avalaba (`business_loans_to_owner_in`).
                loans = business_loans_in(connection, guild_id, business.id) if housing else []
                bank_owed = sum(loan.owed for loan in loans)
                if bank_owed:
                    bills.append(
                        BusinessCost(
                            f"{BANK_LOAN_CONCEPT}cancelacion", gone=bank_owed, expense=False
                        )
                    )

                def settle(connection: sqlite3.Connection, unpaid: int) -> None:
                    if housing is not None and bank_owed:
                        paid = bank_owed - min(unpaid, bank_owed)
                        bank.append(
                            housing.business_loans_to_owner_in(
                                connection, guild_id, business.id, now, paid
                            )
                        )

                limit = (
                    owed + hacienda
                    if sl.liable_for_all(info, today.isoformat())
                    else sl.guarantee(info)
                )
                closing = Closing(
                    business, refund, forfeit, 0, traspaso, stock, proceeds, reason=reason,
                    company=True, corporate_tax=result["tax"],
                )  # fmt: skip
                move = BusinessMove(
                    user_id=user_id,
                    account=_account(business),
                    refund=refund,
                    forfeit=forfeit,
                    sales=stock,
                    sold_cost=offer.stock,
                    proceeds=proceeds,
                    bills=tuple(bills),
                    liquidation=True,
                    liquidation_basis=info["contributed"],
                    liquidation_loan=info["loan"],
                    cover_limit=limit,
                    settle=settle,
                )
                return closing, move
            if housing is not None:
                # Los del autónomo ya eran suyos: siguen igual, ahora como personales.
                bank.append(
                    housing.business_loans_to_owner_in(connection, guild_id, business.id, now)
                )
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
        bank_paid, bank_moved = bank[0] if bank else (0, 0)
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
            closing.company,
            paid.liquidated,
            paid.loan_repaid,
            paid.covered,
            paid.derived_debt,
            closing.corporate_tax,
            bank_paid,
            bank_moved,
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
        if business.is_company:
            return 0, 0, fee  # el local lo paga la caja de la sociedad, no tu monedero
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


def _equity(business: Business, cash: int, bank: int = 0) -> int:
    """El patrimonio neto de la sociedad de un negocio con `cash` en caja y `bank` debido al
    banco."""
    assets = stock_value(business) + book_value(business)
    return sl.equity(business.company, cash, assets, business.rent_debt + bank)


def _company_income(business: Business) -> int | None:
    """Lo que deja su sociedad al día para el banco (`None` si es de un autónomo)."""
    if not business.is_company:
        return None
    return max(0, sl.ebitda(business.last_week)) // 7
