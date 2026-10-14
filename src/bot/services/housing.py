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

import random
from dataclasses import dataclass, replace
from datetime import date, timedelta

from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    KINDS,
    RENT_LEVELS,
    TENANTS,
    Listing,
    RentLevel,
)
from bot.services.taxes import YAPDOLLARS_PER_EURO, capital_gain_tax

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

#: Lo que sube el índice del barrio por cada inmueble entero que se compra, y lo
#: que baja por cada uno que se vende. Comprar encarece el barrio a los demás.
INDEX_BUY_STEP = 0.015
INDEX_SELL_STEP = 0.01
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
) -> SaleQuote:
    """Cuenta de una venta al índice de hoy.

    Args:
        shares: Participaciones que vende.
        owned: Participaciones que tiene (para repartir el coste y las deudas).
        cost: Lo que le costaron todas las que tiene.
        arrears: Deudas del inmueble entero.
        arrears_ibi: La parte de `arrears` que es IBI.
    """
    if not 1 <= shares <= owned <= SHARES:
        raise ValueError("No puedes vender más participaciones de las que tienes.")
    value = share_price(listing, index) * shares
    fee = round(value * SALE_FEE_RATE)
    debt = arrears * shares // SHARES
    debt_ibi = min(debt, arrears_ibi * shares // SHARES)
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


def index_after(index: float, shares: int, *, bought: bool) -> float:
    """Índice del barrio tras comprar o vender `shares` participaciones."""
    step = INDEX_BUY_STEP if bought else -INDEX_SELL_STEP
    moved = index * (1 + step * shares / SHARES)
    return min(INDEX_MAX, max(INDEX_MIN, round(moved, 4)))


def index_next_day(index: float) -> float:
    """El índice de mañana: vuelve poco a poco a 1 si nadie compra ni vende."""
    return round(index + (1.0 - index) * INDEX_REVERSION, 4)


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


_HOMES = frozenset({"estudio", "bajo", "piso2", "piso3", "atico", "chalet", "edificio"})
_BLOCKS = frozenset({"estudio", "bajo", "piso2", "piso3", "atico", "edificio"})

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


def _tenant_for(level: str, rng: random.Random) -> str:
    candidates = [t.key for t in TENANTS if level in t.levels]
    return rng.choice(candidates)


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

    rent = 0
    status = lease.status
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
        if rng.random() < level.find:
            lease = replace(
                lease,
                status=RENTED,
                tenant=_tenant_for(level.key, rng),
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
    imprevistos (del día y los atrasados). Lo que la renta no cubre queda como
    deuda del inmueble: se paga con la renta de los días siguientes o se descuenta
    al vender. Así nadie pone dinero de su bolsillo un día malo y la deuda no se
    olvida.
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
    """Reparte `amount` del inmueble entero entre las participaciones de cada uno.

    Las participaciones que no tiene nadie (siguen en venta) se quedan su parte, que
    no cobra nadie. Los céntimos que sobran del redondeo van a quien más tiene.

    Args:
        holders: Participaciones de cada miembro.
    """
    owned = sum(holders.values())
    if amount <= 0 or not owned:
        return {member: 0 for member in holders}
    parts = {member: amount * shares // SHARES for member, shares in holders.items()}
    leftover = amount * owned // SHARES - sum(parts.values())
    if leftover:
        top = max(holders, key=lambda member: (holders[member], -member))
        parts[top] += leftover
    return parts


def manager(holders: dict[int, int]) -> int | None:
    """Quién decide la renta: quien tiene la mayoría absoluta (art. 398 CC), o nadie."""
    for member, shares in holders.items():
        if shares * 2 > SHARES:
            return member
    return None
