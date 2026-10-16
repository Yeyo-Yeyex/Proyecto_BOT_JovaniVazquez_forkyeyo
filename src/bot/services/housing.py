"""Reglas de `pisos`: lo que vale un inmueble, lo que renta y lo que cuesta tenerlo.

Lógica pura, sin Discord ni base de datos. Los datos (barrios, inmuebles,
inquilinos) están en `bot.services.housing_catalog`; el dinero lo mueve
`EconomyService.housing` y lo que pasa cada día lo guarda
`bot.repositories.housing`.

**Escalas.**

- Precios a la escala general, 10 Y$ = 1 € (`taxes.YAPDOLLARS_PER_EURO`), como la
  tienda: un piso cuesta lo que pide el portal multiplicado por diez.
- **Un día del bot es un mes del contrato:** cada noche el inquilino paga el mes y
  se paga el mes de comunidad, seguro e IBI. Así un piso se paga solo en unos
  meses de verdad y no en veinte años.
- El IRPF del bot proyecta la renta de la semana × 52 (`taxes.compute_withholding`),
  así que una semana de alquileres (siete «meses») no se le puede pasar tal cual: se
  le pasa lo que daría un año real con `rent_to_fiscal`, igual que la nómina de
  `pala` pasa por `wage_to_fiscal` (Biblia, «Equilibrio»).

**Participaciones.** Cada inmueble se divide en `SHARES` participaciones: quien
tiene las 100 tiene el piso entero; quien compra 3, el 3 % de todo (lo que vale,
lo que renta y lo que cuesta). Es la copropiedad del art. 392 del Código Civil,
con los mismos impuestos que un piso entero. Decide la renta quien tiene la
mayoría (art. 398 CC); si nadie la tiene, la gestora pide la de referencia.

**Impuestos y gastos** (cada uno con su norma, en el comentario de su constante):
ITP al comprar, notaría y comisión de la agencia (no son impuestos: el dinero
desaparece), IBI cada mes, IRPF de la renta y de la ganancia al vender. El
Impuesto sobre el Patrimonio cuenta los inmuebles a su valor fiscal
(`fiscal_value`).

**De dónde sale el dinero.** Los inquilinos y los compradores son ficticios: la
renta y lo cobrado al vender se crean de la nada, y lo pagado al comprar y los
gastos desaparecen. El tope que pide la Biblia («Nada crece sin tope») es que solo
hay un inmueble de cada en el servidor (60 en total): la renta del servidor no
puede crecer sin fin aunque se reinvierta.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import date, timedelta

from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    KINDS,
    LISTING_BY_KEY,
    LISTINGS,
    NOMAD_DEMAND,
    PROMOTION_BLURBS,
    PROMOTION_FLOORS,
    PROMOTION_NAMES,
    RENT_LEVELS,
    TENANTS,
    Listing,
    RentLevel,
)
from bot.services.taxes import WAGE_YAPDOLLARS_PER_EURO, YAPDOLLARS_PER_EURO, capital_gain_tax

#: Participaciones en que se divide cada inmueble.
SHARES = 100

#: ITP de los inmuebles en Canarias: tipo general del 6,5 % (art. 31 del Decreto
#: Legislativo 1/2009). Lo paga quien compra (art. 8.a del RDL 1/1993). El tipo
#: reducido de la vivienda habitual (5 %) no aplica: aquí se compra para alquilar.
ITP_RATE = 0.065
#: Notaría, registro y gestoría, más o menos un 1,5 % del precio. No es un
#: impuesto: desaparece (los aranceles del RD 1426/1989 y del RD 1427/1989).
NOTARY_RATE = 0.015
#: Comisión de la agencia al vender, a cargo de quien vende: un 3 %.
SALE_FEE_RATE = 0.03
#: Valor catastral: la mitad del valor de mercado (coeficiente de referencia al
#: mercado RM de 0,5, Orden EHA/1213/2005). No sigue al índice del barrio: el
#: catastro va siempre años por detrás.
CADASTRAL_SHARE = 0.5
#: Tipo del IBI urbano. La ley deja a cada ayuntamiento entre el 0,4 % y el 1,1 %
#: (art. 72.1 del RDL 2/2004); se usa uno intermedio para todos.
IBI_RATE = 0.0055
#: Seguro del hogar con impago: un 3 % de la renta de referencia al mes.
INSURANCE_SHARE = 0.03
#: Amortización deducible: el 3 % anual del valor de la construcción (art. 14.2.a
#: del RIRPF), que se toma como el 60 % de lo pagado (el resto es suelo).
AMORTIZATION_RATE = 0.03
CONSTRUCTION_SHARE = 0.6
#: Reducción del rendimiento neto del alquiler de vivienda: el 50 % general
#: (art. 23.2 LIRPF, redacción de la Ley 12/2023). Las del 60 %, 70 % y 90 % piden
#: rebajar la renta en zona tensionada, alquilar a jóvenes o rehabilitar.
RENT_REDUCTION = 0.5

#: Lo que se mueve el índice del barrio si se compra (o se vende) todo el barrio de
#: golpe. Cada operación lo mueve en proporción a lo que pesa en el barrio
#: (`index_after`): comprar el trastero apenas lo toca y comprar la villa, mucho.
#: Comprar encarece el barrio a los demás. Como es menor que lo que cuesta una ida y
#: vuelta (ITP, notaría y agencia, ~11 %), inflarlo comprando y vendiendo nunca sale a
#: cuenta.
INDEX_STEP = 0.015
#: Cada día el índice recorre esta parte de lo que le falta para volver a 1.
INDEX_REVERSION = 0.01
INDEX_MIN = 0.7
INDEX_MAX = 2.5

#: Probabilidad diaria de que un inquilino se vaya antes de tiempo.
EARLY_LEAVE = 0.004
#: Al acabar el contrato, probabilidad de que lo renueve con la misma renta.
RENEWAL = 0.7
#: Probabilidad diaria de que quien no paga se ponga al día solo.
PAY_BACK = 0.04
#: Probabilidad diaria de que entren okupas en un piso vacío desde hace una semana.
SQUAT = 0.003
SQUAT_AFTER_DAYS = 7
#: Días que tarda el juzgado: desahucio por impago y por ocupación.
EVICTION_DAYS = (10, 20)
SQUAT_EVICTION_DAYS = (15, 30)

#: Estados de un inmueble con dueño.
VACANT = "vacio"
RENTED = "alquilado"
DEFAULTING = "impago"
EVICTING = "desahucio"
SQUATTED = "okupa"


def _euros(amount: float) -> int:
    """Euros a Y$, a la escala general."""
    return round(amount * YAPDOLLARS_PER_EURO)


def _m2(listing: Listing) -> float:
    kind = KINDS[listing.kind]
    return kind.fixed_m2 if kind.fixed_m2 is not None else listing.m2


def base_price(listing: Listing) -> int:
    """Precio del inmueble entero con el índice del barrio a 1, en Y$ (a miles)."""
    barrio = BARRIO_BY_KEY[listing.barrio]
    kind = KINDS[listing.kind]
    euros = _m2(listing) * barrio.price_m2 * kind.price_factor
    return int(round(_euros(euros), -3))


def price(listing: Listing, index: float = 1.0) -> int:
    """Lo que vale hoy el inmueble entero, con el índice de su barrio."""
    return int(round(base_price(listing) * index, -3))


def share_price(listing: Listing, index: float = 1.0) -> int:
    """Lo que vale una participación (1/`SHARES`)."""
    return price(listing, index) // SHARES


def reference_rent(listing: Listing, index: float = 1.0) -> int:
    """Renta de referencia de un mes (un día del bot), en Y$ (a decenas)."""
    barrio = BARRIO_BY_KEY[listing.barrio]
    kind = KINDS[listing.kind]
    euros = _m2(listing) * barrio.rent_m2 * kind.rent_factor * index
    return int(round(_euros(euros), -1))


def asking_rent(listing: Listing, level: RentLevel, index: float = 1.0) -> int:
    """Lo que pide el dueño al mes con su nivel de renta."""
    return int(round(reference_rent(listing, index) * level.factor, -1))


def cadastral_value(listing: Listing) -> int:
    """Valor catastral del inmueble entero (no sigue al índice)."""
    return round(base_price(listing) * CADASTRAL_SHARE)


@dataclass(frozen=True, slots=True)
class MonthlyCosts:
    """Lo que cuesta tener el inmueble entero un mes (un día del bot), en Y$.

    Attributes:
        community: Comunidad o mantenimiento. Desaparece.
        insurance: Seguro del hogar con impago. Desaparece.
        ibi: IBI del mes. Va al Estado (en el juego no hay ayuntamientos).
    """

    community: int
    insurance: int
    ibi: int

    @property
    def total(self) -> int:
        """Todo junto."""
        return self.community + self.insurance + self.ibi


def monthly_costs(listing: Listing) -> MonthlyCosts:
    """Gastos de un mes del inmueble entero."""
    return MonthlyCosts(
        community=_euros(KINDS[listing.kind].community),
        insurance=round(reference_rent(listing) * INSURANCE_SHARE),
        ibi=round(cadastral_value(listing) * IBI_RATE / 12),
    )


def fiscal_value(listing: Listing, shares: int, paid: int) -> int:
    """Valor de unas participaciones para el Impuesto sobre el Patrimonio.

    El mayor entre el valor catastral y el precio de adquisición (art. 10.1 de la
    Ley 19/1991). Como el catastral es la mitad del mercado, casi siempre manda lo
    pagado.

    Args:
        shares: Participaciones que tiene.
        paid: Lo que pagó por ellas, sin impuestos ni gastos.
    """
    return max(cadastral_value(listing) * shares // SHARES, paid)


def rent_to_fiscal(amount: int) -> int:
    """Pasa renta de alquiler (un día = un mes) a la escala de la retención.

    La retención proyecta la renta de la semana a un año multiplicando por 365/7
    (`taxes.compute_withholding`). Un día de alquiler es un mes, así que un año
    real de alquiler son 12 días: se le pasa `amount × 12 / 365` para que la
    proyección dé la renta real del año (Biblia, «Equilibrio», escalas propias).
    """
    return max(0, amount) * 12 // 365


def taxable_rent(rent: int, deductible: int) -> int:
    """Rendimiento neto reducido de un mes de alquiler, en Y$.

    Ingresos menos gastos deducibles (comunidad, seguro, IBI, reparaciones y
    amortización, arts. 23.1 LIRPF y 13 y 14 RIRPF), con la reducción del 50 %
    (art. 23.2 LIRPF). Un rendimiento negativo no se compensa aquí: cuenta como 0.
    """
    return round(max(0, rent - deductible) * (1 - RENT_REDUCTION))


def amortization(paid: int) -> int:
    """Amortización de un mes sobre lo pagado por unas participaciones."""
    return round(paid * CONSTRUCTION_SHARE * AMORTIZATION_RATE / 12)


@dataclass(frozen=True, slots=True)
class PurchaseQuote:
    """Lo que cuesta comprar unas participaciones.

    Attributes:
        shares: Cuántas.
        price: Su valor de mercado.
        itp: ITP (al Estado).
        notary: Notaría, registro y gestoría (desaparece).
    """

    shares: int
    price: int
    itp: int
    notary: int

    @property
    def total(self) -> int:
        """Todo lo que sale del bolsillo."""
        return self.price + self.itp + self.notary


def purchase_quote(listing: Listing, shares: int, index: float = 1.0) -> PurchaseQuote:
    """Cuenta de una compra al índice de hoy (el índice sube después de comprar)."""
    if not 1 <= shares <= SHARES:
        raise ValueError("Las participaciones van de 1 a 100.")
    value = share_price(listing, index) * shares
    return PurchaseQuote(
        shares=shares,
        price=value,
        itp=round(value * ITP_RATE),
        notary=round(value * NOTARY_RATE),
    )


@dataclass(frozen=True, slots=True)
class SaleQuote:
    """Lo que se cobra al vender unas participaciones al mercado.

    Attributes:
        shares: Cuántas.
        price: Su valor de mercado.
        fee: Comisión de la agencia (desaparece).
        arrears: Deudas del inmueble que le tocan (comunidad e IBI sin pagar):
            se descuentan del precio, porque la deuda va con el piso (art. 9.1.e
            de la Ley de Propiedad Horizontal).
        arrears_ibi: La parte de `arrears` que es IBI (va al Estado).
        cost: Lo que le costaron (precio, ITP y gastos: art. 35.1 LIRPF).
        gain: Ganancia patrimonial (puede ser negativa).
        gain_tax: IRPF de la ganancia, base del ahorro al 19 % (`capital_gain_tax`).
    """

    shares: int
    price: int
    fee: int
    arrears: int
    arrears_ibi: int
    cost: int
    gain: int
    gain_tax: int

    @property
    def received(self) -> int:
        """Lo que llega al bolsillo."""
        return max(0, self.price - self.fee - self.arrears - self.gain_tax)


def sale_quote(
    listing: Listing,
    shares: int,
    owned: int,
    cost: int,
    *,
    index: float = 1.0,
    arrears: int = 0,
    arrears_ibi: int = 0,
    pool: int | None = None,
    value: int | None = None,
) -> SaleQuote:
    """Cuenta de una venta al índice de hoy.

    Args:
        shares: Participaciones que vende.
        owned: Participaciones que tiene (para repartir lo que le costaron).
        cost: Lo que le costaron todas las que tiene.
        arrears: Deudas del inmueble (las de las participaciones que tienen dueño).
        arrears_ibi: La parte de `arrears` que es IBI.
        pool: Participaciones que tienen todos los dueños juntos (`owned` si no se
            dice). La deuda se reparte entre ellas: quien lo tiene todo y vende de una
            en una paga lo mismo que vendiéndolo de golpe.
        value: Precio pactado de las participaciones (la oferta del fondo buitre); si
            no se dice, el de mercado al índice de hoy.
    """
    if not 1 <= shares <= owned <= SHARES:
        raise ValueError("No puedes vender más participaciones de las que tienes.")
    pool = max(owned, pool or owned)
    value = share_price(listing, index) * shares if value is None else value
    fee = round(value * SALE_FEE_RATE)
    debt = arrears * shares // pool
    debt_ibi = min(debt, arrears_ibi * shares // pool)
    debt = min(debt, value - fee)
    debt_ibi = min(debt_ibi, debt)
    part_cost = cost * shares // owned
    gain = value - fee - debt - part_cost
    return SaleQuote(
        shares=shares,
        price=value,
        fee=fee,
        arrears=debt,
        arrears_ibi=debt_ibi,
        cost=part_cost,
        gain=gain,
        gain_tax=min(capital_gain_tax(gain), value - fee - debt),
    )


def barrio_weight(listing: Listing) -> float:
    """Lo que pesa un inmueble en su barrio: su precio entre el de todos los del barrio.

    La obra nueva cuenta con los tres del catálogo y ella misma (cada barrio tiene una como
    mucho), así que nunca pesa más que todo el barrio.
    """
    total = sum(base_price(item) for item in LISTINGS if item.barrio == listing.barrio)
    if listing.key not in LISTING_BY_KEY:
        total += base_price(listing)
    return base_price(listing) / total


def index_after(index: float, listing: Listing, shares: int, *, bought: bool) -> float:
    """Índice del barrio tras comprar o vender `shares` participaciones de `listing`.

    Comprar multiplica por `1 + INDEX_STEP × peso × shares/100` y vender divide por lo
    mismo: una compra y su venta dejan el índice exactamente igual. Sin redondear,
    para que el redondeo no lo suba un poco en cada vuelta.
    """
    factor = 1 + INDEX_STEP * barrio_weight(listing) * shares / SHARES
    moved = index * factor if bought else index / factor
    return min(INDEX_MAX, max(INDEX_MIN, moved))


def index_next_day(index: float) -> float:
    """El índice de mañana: vuelve poco a poco a 1 si nadie compra ni vende."""
    return index + (1.0 - index) * INDEX_REVERSION


# -- El día a día de un inmueble --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lease:
    """Lo que pasa en un inmueble con dueño.

    Attributes:
        status: `VACANT`, `RENTED`, `DEFAULTING`, `EVICTING` o `SQUATTED`.
        level: Clave de `RENT_LEVELS` que pide el dueño (vale para el próximo
            contrato: a mitad de uno no se puede subir, art. 18 LAU).
        tenant: Clave del inquilino (si hay).
        rent: Renta del contrato en vigor, Y$ al mes.
        until: Último día del contrato.
        since: Desde qué día está en este estado.
        unpaid: Meses que debe el inquilino.
        eviction_at: Día de la vista del desahucio.
        arrears: Gastos sin pagar del inmueble entero (comunidad, seguro e IBI).
        arrears_ibi: La parte de `arrears` que es IBI.
        member: El miembro que vive de alquiler, si el inquilino es del servidor.
        bono: Si ese miembro cobra el Bono Alquiler Joven.
        license: Si tiene licencia de vivienda vacacional.
    """

    status: str = VACANT
    level: str = "referencia"
    tenant: str | None = None
    rent: int = 0
    until: date | None = None
    since: date | None = None
    unpaid: int = 0
    eviction_at: date | None = None
    arrears: int = 0
    arrears_ibi: int = 0
    member: int | None = None
    bono: bool = False
    license: bool = False


@dataclass(frozen=True, slots=True)
class Event:
    """Un imprevisto del día.

    Attributes:
        key: Clave estable (para las noticias y los logros).
        cost: Lo que cuesta al inmueble entero, en Y$ (0 si no cuesta nada).
    """

    key: str
    cost: int = 0


@dataclass(frozen=True, slots=True)
class EventKind:
    """Un tipo de imprevisto, con su probabilidad diaria y su coste.

    Attributes:
        key: Clave estable.
        chance: Probabilidad diaria.
        community_months: El coste en meses de comunidad (la derrama, la ITE).
        euros: El coste en euros, `(mínimo, máximo)` (las averías).
        kinds: Tipos de inmueble a los que les puede pasar (vacío: a todos).
    """

    key: str
    chance: float
    community_months: int = 0
    euros: tuple[int, int] = (0, 0)
    kinds: frozenset[str] = frozenset()


#: Lo que tiene instalaciones que se rompen (las viviendas y los negocios con techo).
_HOMES = frozenset(
    {"estudio", "bajo", "piso2", "piso3", "atico", "chalet", "edificio", "terrera", "cueva"}
    | {"finca", "casona", "bungalow", "duplex", "loft", "local", "nave"}
)
#: Lo que está en una comunidad de propietarios (derramas, ITE y vecinos).
_BLOCKS = frozenset(
    {"estudio", "bajo", "piso2", "piso3", "atico", "edificio", "duplex", "loft", "local"}
    | {"bungalow"}
)

#: Imprevistos (los textos están en el cog). Una avería cada tres meses del juego
#: y una derrama al año, más o menos.
EVENTS: tuple[EventKind, ...] = (
    EventKind("averia", 0.010, euros=(120, 450), kinds=_HOMES),
    EventKind("humedades", 0.004, euros=(300, 900), kinds=_HOMES),
    EventKind("derrama", 0.004, community_months=15, kinds=_BLOCKS),
    EventKind("ite", 0.002, community_months=30, kinds=_BLOCKS),
    EventKind("vecino", 0.010, kinds=_BLOCKS),
    EventKind("cerradura", 0.004, euros=(80, 200)),
)


@dataclass(frozen=True, slots=True)
class DayOutcome:
    """Lo que pasa un día en un inmueble.

    Attributes:
        lease: Cómo queda.
        rent: Renta cobrada del inmueble entero (0 si no paga nadie).
        costs: Gastos del mes del inmueble entero (sin los imprevistos).
        extra: Coste de los imprevistos del día.
        events: Imprevistos y cambios de estado del día, para las noticias
            (`firma`, `renueva`, `se_va`, `impago`, `paga`, `okupa`, `desahucio`
            y las claves de `EVENTS`).
    """

    lease: Lease
    rent: int
    costs: MonthlyCosts
    extra: int
    events: tuple[Event, ...]


def _tenant_for(level: str, kind: str, rng: random.Random) -> str:
    candidates = [t.key for t in TENANTS if t.rents(level, kind)]
    return rng.choice(candidates)


def find_chance(listing: Listing, level: RentLevel, day: date) -> float:
    """Probabilidad de que un inmueble vacío encuentre inquilino hoy.

    La vacacional depende de la temporada (`season_factor`) y los nómadas, del tipo de
    barrio (`NOMAD_DEMAND`): buscan playa y fibra, no un polígono.
    """
    find = level.find
    if level.key == VACATION_LEVEL:
        find *= season_factor(day)
    elif level.key == NOMAD_LEVEL:
        find *= NOMAD_DEMAND[BARRIO_BY_KEY[listing.barrio].theme]
    return find


def advance_day(
    listing: Listing, lease: Lease, day: date, index: float, rng: random.Random
) -> DayOutcome:
    """Un mes de la vida de un inmueble con dueño (un día del bot).

    Orden: imprevistos, desahucio que llega a su fecha, el inquilino paga o no,
    se va, renueva o deja de pagar, y si está vacío, se busca inquilino (o entran
    okupas). La renta solo se cobra si hay inquilino al día al empezar.

    Args:
        lease: Cómo estaba ayer.
        day: El día que se cierra.
        index: Índice del barrio (para la renta de un contrato nuevo).
        rng: Azar inyectable.
    """
    level = RENT_LEVELS.get(lease.level, RENT_LEVELS["referencia"])
    extra, rolled = roll_events(listing, rng)
    events = list(rolled)

    rent = 0
    status = lease.status
    if status == HABITUAL or lease.member is not None:
        # Vive su dueño, o un miembro de alquiler: no hay inquilinos ficticios que entren,
        # se vayan o dejen de pagar (lo del miembro lo decide su monedero).
        return DayOutcome(lease=lease, rent=0, costs=monthly_costs(listing), extra=extra,
                          events=tuple(events))  # fmt: skip
    if status == EVICTING and lease.eviction_at is not None and day >= lease.eviction_at:
        lease = replace(
            lease,
            status=VACANT,
            tenant=None,
            rent=0,
            until=None,
            since=day,
            unpaid=0,
            eviction_at=None,
        )
        events.append(Event("desahucio"))
    elif status == RENTED:
        rent = lease.rent
        if lease.until is not None and day >= lease.until:
            if rng.random() < RENEWAL:
                lease = replace(lease, until=day + timedelta(days=level.months))
                events.append(Event("renueva"))
            else:
                lease = replace(lease, status=VACANT, tenant=None, rent=0, until=None, since=day)
                events.append(Event("se_va"))
        elif rng.random() < level.default:
            lease = replace(lease, status=DEFAULTING, since=day, unpaid=1)
            rent = 0
            events.append(Event("impago"))
        elif rng.random() < EARLY_LEAVE:
            lease = replace(lease, status=VACANT, tenant=None, rent=0, until=None, since=day)
            events.append(Event("se_va"))
    elif status == DEFAULTING:
        if rng.random() < PAY_BACK:
            rent = lease.rent * (lease.unpaid + 1)
            lease = replace(lease, status=RENTED, unpaid=0, since=day)
            events.append(Event("paga"))
        else:
            lease = replace(lease, unpaid=lease.unpaid + 1)
    elif status == VACANT:
        vacant_for = (day - lease.since).days if lease.since is not None else 0
        if rng.random() < find_chance(listing, level, day):
            lease = replace(
                lease,
                status=RENTED,
                tenant=_tenant_for(level.key, listing.kind, rng),
                rent=asking_rent(listing, level, index),
                until=day + timedelta(days=level.months),
                since=day,
                unpaid=0,
            )
            events.append(Event("firma"))
        elif vacant_for >= SQUAT_AFTER_DAYS and rng.random() < SQUAT:
            lease = replace(lease, status=SQUATTED, tenant=None, rent=0, since=day)
            events.append(Event("okupa"))
    return DayOutcome(
        lease=lease, rent=rent, costs=monthly_costs(listing), extra=extra, events=tuple(events)
    )


def roll_events(listing: Listing, rng: random.Random) -> tuple[int, tuple[Event, ...]]:
    """Los imprevistos de un mes: `(lo que cuestan, cuáles)`."""
    events: list[Event] = []
    extra = 0
    for kind in EVENTS:
        if kind.kinds and listing.kind not in kind.kinds:
            continue
        if rng.random() >= kind.chance:
            continue
        cost = _euros(KINDS[listing.kind].community * kind.community_months)
        if kind.euros[1]:
            cost += _euros(rng.randint(*kind.euros))
        extra += cost
        events.append(Event(kind.key, cost))
    return extra, tuple(events)


def start_eviction(lease: Lease, day: date, rng: random.Random) -> Lease:
    """Pide el desahucio: el juzgado da fecha para la vista.

    Raises:
        ValueError: Si nadie debe nada ni hay okupas.
    """
    if lease.status == DEFAULTING:
        low, high = EVICTION_DAYS
    elif lease.status == SQUATTED:
        low, high = SQUAT_EVICTION_DAYS
    else:
        raise ValueError("Solo se desahucia a quien no paga o a los okupas.")
    return replace(
        lease, status=EVICTING, since=day, eviction_at=day + timedelta(days=rng.randint(low, high))
    )


@dataclass(frozen=True, slots=True)
class Payout:
    """Cómo se reparte un día entre lo que se debe y lo que cobran los dueños.

    Todo es del inmueble entero; el reparto entre participaciones lo hace
    `split`.

    Attributes:
        ibi: IBI pagado hoy (del mes y del atrasado). Va al Estado.
        costs: Comunidad, seguro e imprevistos pagados hoy. Desaparecen.
        net: Lo que queda para los dueños.
        arrears: Deuda que queda.
        arrears_ibi: La parte de `arrears` que es IBI.
    """

    ibi: int
    costs: int
    net: int
    arrears: int
    arrears_ibi: int


def settle_day(
    rent: int, costs: MonthlyCosts, extra: int, arrears: int, arrears_ibi: int
) -> Payout:
    """Paga con la renta del día lo que se debe, por orden, y deja el resto a deber.

    Primero el IBI (del mes y el atrasado), luego la comunidad, el seguro y los
    imprevistos (del día y los atrasados). Lo que la renta no cubre se intenta cobrar
    del monedero de los dueños (`HousingDebit`, un recibo domiciliado) y lo que no
    llegue queda como deuda del inmueble: se paga después o se descuenta al vender.
    """
    owed_ibi = costs.ibi + arrears_ibi
    owed_other = costs.community + costs.insurance + extra + (arrears - arrears_ibi)
    pay_ibi = min(rent, owed_ibi)
    pay_other = min(rent - pay_ibi, owed_other)
    left_ibi = owed_ibi - pay_ibi
    left_other = owed_other - pay_other
    return Payout(
        ibi=pay_ibi,
        costs=pay_other,
        net=rent - pay_ibi - pay_other,
        arrears=left_ibi + left_other,
        arrears_ibi=left_ibi,
    )


def split(amount: int, holders: dict[int, int]) -> dict[int, int]:
    """Reparte `amount` entero entre los dueños, según sus participaciones.

    `amount` ya es solo de las participaciones que tienen dueño (`owned_share`); los
    céntimos que sobran del redondeo van a quien más tiene.

    Args:
        holders: Participaciones de cada miembro.
    """
    owned = sum(holders.values())
    if amount <= 0 or not owned:
        return {member: 0 for member in holders}
    parts = {member: amount * shares // owned for member, shares in holders.items()}
    leftover = amount - sum(parts.values())
    if leftover:
        top = max(holders, key=lambda member: (holders[member], -member))
        parts[top] += leftover
    return parts


def owned_share(amount: int, owned: int) -> int:
    """La parte de una cifra del inmueble entero que toca a las participaciones con dueño.

    Las que siguen en venta no cobran renta ni pagan gastos: son de la promotora.
    """
    return amount * owned // SHARES


def manager(holders: dict[int, int]) -> int | None:
    """Quién decide la renta: quien tiene la mayoría absoluta (art. 398 CC), o nadie."""
    for member, shares in holders.items():
        if shares * 2 > SHARES:
            return member
    return None


# -- Fase 2: hipoteca, VPO y vivienda habitual ------------------------------------------

#: Estado de un inmueble en el que vive su dueño: no se alquila, paga sus recibos.
HABITUAL = "habitual"
#: Tipos de inmueble que son vivienda (un trastero o un garaje no lo son).
HOME_KINDS = frozenset(
    {"estudio", "bajo", "piso2", "piso3", "atico", "chalet", "edificio", "terrera", "cueva"}
    | {"finca", "casona", "bungalow", "duplex", "loft"}
)

#: Lo que presta el banco sobre el precio (sin ITP ni notaría): el 80 %. Con el aval del
#: Estado (la línea de avales del ICO para la primera vivienda de jóvenes y familias con
#: menores, de 2023), el 100 % en la VPO.
MORTGAGE_LTV = 0.8
VPO_LTV = 1.0
#: Diferencial sobre el Euríbor: el tipo es fijo al firmar (Euríbor del día + 1 %).
MORTGAGE_SPREAD = 0.01
#: 30 años: 360 meses, que a un día = un mes son 360 días.
MORTGAGE_MONTHS = 360
#: La cuota no puede pasar de esta parte de los ingresos (lo que piden los bancos).
DTI_MAX = 0.35
#: Parte de las rentas de alquiler que el banco cuenta como ingreso.
RENT_INCOME_SHARE = 0.7
#: Días de ingresos que mira el banco (las nóminas de un mes de verdad).
INCOME_DAYS = 30
#: Euríbor simulado: empieza en el real de 2026 y se mueve un cuarto de punto los lunes.
EURIBOR_START = 0.022
EURIBOR_STEP = 0.0025
EURIBOR_MIN = 0.0
EURIBOR_MAX = 0.05
#: Ejecución hipotecaria (art. 693.2 LEC, redacción de la Ley 5/2019): impagos de 12
#: cuotas o del 3 % del capital en la primera mitad del préstamo.
EXECUTION_INSTALLMENTS = 12
EXECUTION_SHARE = 0.03
#: Sin pujas, el banco se adjudica el inmueble por el 70 % de su valor (art. 671 LEC).
AUCTION_SHARE = 0.7
#: Vivienda habitual exenta en el Patrimonio hasta 300.000 € (art. 4.Nueve.c de la Ley
#: 19/1991), a 10 Y$ por euro.
HOME_WEALTH_EXEMPT = 3_000_000
#: Emanciparse: la batería de `pala` recarga un 20 % más rápido en tu casa.
HOME_REGEN = 1.2
#: El aval de tus padres (favor `aval` de `pala`): el banco suma a tus ingresos la mitad
#: de la pensión de tu padre, la media de jubilación (unos 1.500 € al mes), porque la otra
#: mitad la necesitan para vivir. Va a la escala de las nóminas (`WAGE_YAPDOLLARS_PER_EURO`),
#: como lo que mira el banco. La hipoteca queda avalada (`PARENTS_AVAL`): si la ejecutan,
#: tus padres pierden el piso del pueblo y la familia de `pala` se hunde.
PARENTS_PENSION_EUR = 1_500
PARENTS_SHARE = 0.5
PARENTS_AVAL = "padres"
#: IPREM en euros al año (600 € × 14 pagas, el de 2025 prorrogado). La VPO pide no
#: pasar de 3,5 veces el IPREM de ingresos.
IPREM_EUR_YEAR = 8_400
VPO_INCOME_IPREM = 3.5


def installment(principal: int, annual_rate: float, months: int = MORTGAGE_MONTHS) -> int:
    """Cuota mensual (un día del bot) de un préstamo francés, en Y$."""
    if principal <= 0:
        return 0
    rate = annual_rate / 12
    if rate <= 0:
        return -(-principal // months)
    return math.ceil(principal * rate / (1 - (1 + rate) ** -months))


def mortgage_month(balance: int, annual_rate: float, payment: int) -> tuple[int, int]:
    """`(intereses, amortización)` de una cuota: lo que no son intereses baja el capital."""
    interest = round(balance * annual_rate / 12)
    return interest, max(0, min(balance, payment - interest))


def next_euribor(value: float, rng: random.Random) -> float:
    """Euríbor del lunes siguiente: sube, baja o se queda un cuarto de punto."""
    moved = value + rng.choice((-EURIBOR_STEP, 0.0, EURIBOR_STEP))
    return round(min(EURIBOR_MAX, max(EURIBOR_MIN, moved)), 4)


#: Lo que dice el BCE desde Fráncfort cuando el Euríbor sube, baja o se queda quieto. El
#: Euríbor no lo fija el BCE (es lo que se prestan los bancos), pero lo sigue de cerca:
#: aquí cada lunes sale una frase de su presidencia con el movimiento.
ECB_UP: tuple[str, ...] = (
    "Lagarde sube tipos «para domar la inflación»; tu cuota no tiene quién la dome",
    "Los halcones de Fráncfort ganan la votación: la paloma acaba en paella",
    "El BCE endurece la política monetaria. Tu nevera ya la había endurecido antes",
    "«La inflación es transitoria», dijeron. Lo transitorio era tu poder adquisitivo",
    "Fráncfort sube un cuarto de punto y el Bundesbank lo celebra con salchichas",
    "Subida de tipos: los bancos aplauden, los hipotecados rezan, Sánchez culpa a Putin",
    "Lagarde avisa: «Haremos lo que haga falta». Esta vez, lo que haga falta es cobrarte",
    "El BCE sube tipos y Feijóo dice que con él no pasaría. Con él gobierna Fráncfort igual",
)
ECB_DOWN: tuple[str, ...] = (
    "Lagarde baja tipos: los fondos buitre ya están mirando tu barrio",
    "Whatever it takes, versión rebajas: el BCE abarata el dinero (para quien ya lo tiene)",
    "Fráncfort baja un cuarto de punto. El precio de los pisos lo sube medio",
    "El BCE recorta tipos y las inmobiliarias reimprimen los carteles con otro cero",
    "Bajada de tipos: Bruselas lo celebra, Alemania protesta y tu casero sube la renta",
    "Dinero más barato: lo justo para que el ático suba otro 10 %",
    "Lagarde abre el grifo. El agua llega antes a Blackstone que a tu cuenta",
    "El BCE baja tipos y el Gobierno se lo apunta en el Manual de resistencia",
)
ECB_HOLD: tuple[str, ...] = (
    "El BCE mantiene los tipos «atado y bien atado» hasta nueva orden",
    "Lagarde no toca nada: reunión de seis horas para decir que todo sigue igual",
    "Tipos congelados. Lo único que se mueve en Fráncfort es el canapé",
    "Fráncfort espera «a tener más datos». Los datos esperan a tener más Fráncfort",
    "El BCE no mueve ficha: ni halcones ni palomas, gaviotas de Las Canteras",
    "Política monetaria en pausa, como la reforma de la financiación autonómica",
    "Tipos quietos: el BCE vigila la inflación desde una mesa larguísima, como Putin",
)


def ecb_phrase(before: float, after: float, week: date) -> str:
    """La frase del BCE de un lunes: según el Euríbor suba, baje o se quede quieto.

    Sale siempre la misma para la misma semana y el mismo movimiento (la elige el
    número del lunes), así que no hace falta guardarla para enseñarla otra vez.
    """
    if after > before:
        pool = ECB_UP
    elif after < before:
        pool = ECB_DOWN
    else:
        pool = ECB_HOLD
    return pool[week.toordinal() // 7 % len(pool)]


def vpo_income_cap() -> int:
    """Neto diario de nómina máximo para la VPO, en Y$ de nómina (escala de `pala`)."""
    return round(IPREM_EUR_YEAR * VPO_INCOME_IPREM * WAGE_YAPDOLLARS_PER_EURO / 365)


def parents_income() -> int:
    """Lo que suma al día el aval de tus padres a lo que mira el banco, en Y$ de nómina."""
    return round(PARENTS_PENSION_EUR * PARENTS_SHARE * WAGE_YAPDOLLARS_PER_EURO / INCOME_DAYS)


def daily_income(work_net: int, rent: int) -> int:
    """Lo que el banco cree que ganas al día: nóminas de 30 días y el 70 % de las rentas."""
    return (work_net + round(rent * RENT_INCOME_SHARE)) // INCOME_DAYS


def affordable(income: int, installments: int) -> bool:
    """Si unas cuotas (al día) caben en el 35 % de los ingresos diarios."""
    return installments <= income * DTI_MAX


def must_execute(unpaid_count: int, unpaid_amount: int, principal: int) -> bool:
    """Si el banco ya puede ejecutar la hipoteca (Ley 5/2019)."""
    return unpaid_count >= EXECUTION_INSTALLMENTS or (
        unpaid_amount > 0 and unpaid_amount >= principal * EXECUTION_SHARE
    )


def vpo_costs(unit_price: int, community_eur: int) -> MonthlyCosts:
    """Recibos de un mes de una VPO: comunidad e IBI (sin seguro con impago: no se alquila)."""
    return MonthlyCosts(
        community=_euros(community_eur),
        insurance=0,
        ibi=round(unit_price * CADASTRAL_SHARE * IBI_RATE / 12),
    )


# -- Fase 3: miembros de alquiler y Bono Alquiler Joven ---------------------------------

#: Bono Alquiler Joven: 250 € al mes (RD 42/2022), como mucho la mitad de la renta. Lo paga
#: la cuenta del Estado mientras le quede dinero: si no, «partida agotada».
BONO_EUR = 250
BONO_MAX = BONO_EUR * YAPDOLLARS_PER_EURO
BONO_SHARE = 0.5
#: Ingresos máximos para el bono: 3 veces el IPREM (RD 42/2022, art. 4).
BONO_INCOME_IPREM = 3.0


#: Sobreesfuerzo: el Banco de España habla de sobreesfuerzo cuando el alquiler se come
#: más del 30 % de lo que se gana.
OVERBURDEN = 0.30
#: Lo que piden los caseros para alquilar: una nómina de 3 veces la renta.
INCOME_RENT_MULTIPLE = 3
#: Fianza de un mes de renta en la vivienda (art. 36.1 LAU) y, si la nómina no llega a
#: 3 veces la renta, la garantía adicional de dos meses más, el máximo que permite el
#: art. 36.5 LAU. Con el aval de tus padres (`pala`) no se pide la garantía.
DEPOSIT_MONTHS = 1
GUARANTEE_MONTHS = 2


def effort(rent: int, daily_net: int) -> float | None:
    """Parte del sueldo que se va en el alquiler: la renta de un mes (un día) entre el neto de
    nómina de un día (`work_income`). `None` si no cobra nómina."""
    if daily_net <= 0:
        return None
    return rent / daily_net


def needs_guarantee(rent: int, daily_net: int, *, aval: bool) -> bool:
    """Si el casero pide la garantía adicional: nómina por debajo de 3 veces la renta y sin aval."""
    return not aval and daily_net < rent * INCOME_RENT_MULTIPLE


def deposit_for(rent: int, *, guarantee: bool) -> int:
    """Lo que se deja al firmar: la fianza y, si se pide, la garantía adicional."""
    return rent * (DEPOSIT_MONTHS + (GUARANTEE_MONTHS if guarantee else 0))


def bono_amount(rent: int) -> int:
    """Lo que paga el bono un mes: 250 € como mucho y nunca más de la mitad de la renta."""
    return max(0, min(BONO_MAX, round(rent * BONO_SHARE)))


def bono_income_cap() -> int:
    """Neto diario de nómina máximo para el bono, en Y$ de nómina (escala de `pala`)."""
    return round(IPREM_EUR_YEAR * BONO_INCOME_IPREM * WAGE_YAPDOLLARS_PER_EURO / 365)


# -- Fase 4: vacacional, gran tenedor, fondo buitre y SOCIMI ----------------------------

#: Nivel de renta de la vivienda vacacional (solo con licencia).
VACATION_LEVEL = "vacacional"
#: Nivel de renta de los nómadas digitales (su demanda depende del barrio).
NOMAD_LEVEL = "nomadas"
#: Lo que cuesta la licencia (proyecto, cédula y tasas), en Y$. La tasa va al Estado.
LICENSE_FEE = 30_000
#: Plazas vacacionales por barrio: la ley canaria limita las viviendas vacacionales por
#: zona; aquí, una por barrio. Si está cogida, no hay licencia.
LICENSE_PER_BARRIO = 1
#: IGIC de las estancias vacacionales: el tipo general (art. 51 de la Ley 4/2012). Va
#: dentro de lo que paga el turista: el dueño se queda el resto y lo ingresa en el Estado.
VACATION_IGIC = 0.07
#: Gran tenedor en zona tensionada: 5 viviendas o más (art. 3.k de la Ley 12/2023).
BIG_HOLDER_HOMES = 5
#: Un barrio está tensionado cuando su índice pasa de este valor (los precios subieron
#: un 10 %): allí el gran tenedor no puede pedir más que la renta de referencia (art. 17.7).
TENSE_INDEX = 1.10
#: Niveles que el gran tenedor no puede pedir en zona tensionada.
CAPPED_LEVELS = frozenset({"mercado", "nomadas", VACATION_LEVEL})
#: Recargo del IBI de la vivienda vacía más de 2 años (24 días) de quien tiene 4 o más
#: (art. 72.4 del RDL 2/2004, recargo de hasta el 150 %).
EMPTY_DAYS = 24
EMPTY_OWNER_HOMES = 4
EMPTY_IBI_FACTOR = 2.5
#: El fondo buitre ofrece un 15 % sobre el valor de mercado por un inmueble entero de quien
#: tiene 3 enteros o un edificio, cada lunes; la oferta dura una semana.
VULTURE_HOMES = 3
VULTURE_PREMIUM = 0.15
VULTURE_DAYS = 7
#: SOCIMI: hace falta tener 3 inmuebles enteros y pagar la salida al BME Growth. Sus
#: rentas no pagan IRPF del alquiler sino el 19 % del dividendo (base del ahorro, art. 66
#: LIRPF): la sociedad tributa al 0 % (art. 9 de la Ley 11/2009).
SOCIMI_HOMES = 3
SOCIMI_FEE = 100_000
SOCIMI_RATE = 0.19


def season_factor(day: date) -> float:
    """Ocupación vacacional: en Canarias la temporada alta es el invierno (de noviembre a marzo)."""
    return 1.0 if day.month in (11, 12, 1, 2, 3) else 0.4


def vacation_igic(rent: int) -> int:
    """IGIC que va dentro de una estancia vacacional que cuesta `rent` con todo."""
    return rent - round(rent / (1 + VACATION_IGIC))


def tense(index: float) -> bool:
    """Si un barrio está en zona tensionada."""
    return index >= TENSE_INDEX


def big_holder(whole_homes: int) -> bool:
    """Si alguien es gran tenedor (en zona tensionada)."""
    return whole_homes >= BIG_HOLDER_HOMES


def level_allowed(level: str, *, index: float, whole_homes: int, license: bool) -> bool:
    """Si se puede pedir una renta: la vacacional pide licencia y el gran tenedor tiene tope."""
    if level == VACATION_LEVEL and not license:
        return False
    return not (level in CAPPED_LEVELS and tense(index) and big_holder(whole_homes))


def socimi_tax(profit: int) -> int:
    """El 19 % del dividendo que reparte la SOCIMI de un mes de alquiler."""
    return round(max(0, profit) * SOCIMI_RATE)


# -- Fase 5: obra nueva de la rama Ladrillo --------------------------------------------

#: Las claves de la obra nueva: `obra_1`, `obra_2`… (ningún barrio se llama así).
PROMOTION_PREFIX = "obra_"
#: Tipos de la obra nueva y sus metros, `(tipo, mínimo, máximo)`.
PROMOTION_KINDS: tuple[tuple[str, int, int], ...] = (
    ("piso2", 70, 90),
    ("piso3", 90, 120),
    ("atico", 85, 115),
)
#: Comisión del promotor: de cada participación de su promoción que se compra, el 5 % del
#: precio va a quien la levantó en vez de desaparecer (el margen de la promotora). Es
#: rendimiento de una actividad económica (art. 27 LIRPF, el promotor de `pala` es
#: autónomo) y se retiene a cuenta como el resto de la renta general.
PROMOTER_FEE = 0.05
#: Días hacia atrás en que hay que haber fichado en la rama Ladrillo para levantar la
#: promoción del lunes.
BUILDER_DAYS = 7


def is_promotion(key: str) -> bool:
    """Si una clave es de la obra nueva."""
    return key.startswith(PROMOTION_PREFIX)


def new_promotion(number: int, free_barrios: list[str], rng: random.Random) -> Listing:
    """La promoción número `number` del servidor, en uno de los barrios que no tienen.

    Es un inmueble como los del catálogo: su precio y su renta salen de sus metros y del
    barrio. Certificado A: es obra nueva (lo único nuevo de la burbuja).

    Raises:
        ValueError: Si no queda ningún barrio libre (el tope: una por barrio).
    """
    if not free_barrios:
        raise ValueError("Ya hay obra nueva en todos los barrios.")
    barrio = rng.choice(sorted(free_barrios))
    kind, low, high = rng.choice(PROMOTION_KINDS)
    return Listing(
        key=f"{PROMOTION_PREFIX}{number}",
        barrio=barrio,
        kind=kind,
        m2=rng.randint(low, high),
        title=f"Obra nueva: {rng.choice(PROMOTION_NAMES)}",
        blurb=rng.choice(PROMOTION_BLURBS),
        floor=rng.choice(PROMOTION_FLOORS[kind]),
        energy="A",
    )


def promoter_fee(price: int) -> int:
    """Lo que se lleva el promotor de una compra en su promoción."""
    return round(max(0, price) * PROMOTER_FEE)
