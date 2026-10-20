"""Reglas de los negocios de `pala`: el local, la demanda, la reputación y las cuentas.

Lógica pura, sin Discord ni base de datos. El catálogo está en
`bot.services.business_catalog`, los minijuegos en `bot.services.business_games`, la
persistencia en `bot.repositories.business` y los casos de uso (y el dinero) en
`bot.services.negocio`. El diseño entero, con sus fases, en `docs/negocios.md`.

En resumen:

- **Un negocio por miembro** (fase 1), de autónomo: no tiene caja propia, todo sale y
  entra del monedero de su dueño («respondes con lo tuyo»).
- **El local** se alquila en un barrio de `pisos`: su alquiler de cada noche sale de los
  metros del sector, el `rent_m2` del barrio y el `rent_factor` del tipo de inmueble, a la
  misma escala que la renta de un local en `pisos` (`rent`). La fianza son dos
  mensualidades (art. 36.1 LAU; un día del bot es un mes del contrato, como en `pisos`).
- **La demanda es artificial y limitada** (`turn_clients`, `daily_pool`): los clientes de
  un turno dependen del sector, el tema del barrio, la hora canaria, las estrellas y el
  precio; el barrio tiene un cupo diario por sector que comparten todos los negocios de
  ese sector en él.
- **Reputación** de 0 a 100 (`stars`, de 1 a 5): la mueven los clientes de cada turno
  (`REPUTATION`) y se olvida un poco cada noche sin abrir (`REPUTATION_DRIFT`).
- **Stock** por producto: lo perecedero se pierde en parte cada noche (`spoil`).
- **Cada noche** se pagan el alquiler y los suministros (`nightly_bill`); lo que no llega
  queda como deuda del local y, a las `EVICTION_NIGHTS` noches sin pagar, el casero
  rescinde el contrato.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from datetime import date

from bot.services.business_catalog import (
    EMBLEMS,
    PALETTES,
    PRICE_BY_KEY,
    REGULAR_NAMES,
    REGULARS_PER_BUSINESS,
    SECTOR_BY_KEY,
    Product,
    Sector,
)
from bot.services.housing_catalog import BARRIO_BY_KEY, KINDS, Barrio
from bot.services.taxes import IGIC_BY_KEY, IGIC_GENERAL_RATE, YAPDOLLARS_PER_EURO, igic

# -- Constantes ----------------------------------------------------------------------

#: Largo máximo del nombre de un negocio.
NAME_MAX = 24
#: Avisos que se guardan para enseñar en la portada.
NEWS_MAX = 5
#: Reputación al abrir y sus límites.
REPUTATION_START = 50.0
REPUTATION_MAX = 100.0
#: Lo que se acerca la reputación a `REPUTATION_START` cada noche sin abrir (si está por
#: encima): la clientela olvida.
REPUTATION_DRIFT = 1.0
#: Turnos de clientes base que da de sí un barrio al día por sector (el cupo de la
#: demanda): ni con el mejor negocio se saca más de un barrio.
DAILY_POOL_TURNS = 8
#: Noches sin pagar el alquiler tras las que el casero rescinde el contrato.
EVICTION_NIGHTS = 3
#: Fianza del local en mensualidades (art. 36.1 de la Ley 29/1994, uso distinto de vivienda).
DEPOSIT_MONTHS = 2
#: Retención del IRPF del arrendador de un local (art. 100 RIRPF): la ingresa el inquilino.
RENT_WITHHOLDING = 0.19
#: Días desde el alta con la tarifa plana de autónomos (art. 38 ter de la Ley 20/2007, «12
#: meses»: la semana es el año fiscal).
FLAT_RATE_DAYS = 7
#: Probabilidad de que un cliente sea uno de tus habituales.
REGULAR_CHANCE = 0.3
#: Fidelidad máxima de un habitual y desde la que pide «lo de siempre» sin decir qué.
LOYALTY_MAX = 10
USUAL_LOYALTY = 3

#: Lo que mueve la reputación cada cosa que pasa con un cliente (`business_games`).
REPUTATION: dict[str, float] = {
    "fast": 0.4,
    "ok": 0.3,
    "slow": 0.1,
    "wrong": -1.0,
    "left": -0.6,
    "short": -0.6,
    "over": 0.1,
    "caught": 0.6,
    "missed_thief": 0.0,
    "false_accuse": -3.0,
    "bad_diagnosis": -1.5,
    "rejected": -0.1,
    "no_part": -0.3,
    "cheap_quote": 0.2,
}


def stars(reputation: float) -> float:
    """Estrellas de 1 a 5, de media en media, de una reputación de 0 a 100."""
    return round((1 + 4 * max(0.0, min(REPUTATION_MAX, reputation)) / REPUTATION_MAX) * 2) / 2


def reputation_factor(reputation: float) -> float:
    """Lo que multiplica la clientela la reputación: 1 con 3 estrellas, 4/3 con 5."""
    return 0.5 + stars(reputation) / 6


# -- El negocio ------------------------------------------------------------------------


@dataclass(slots=True)
class Business:
    """Un negocio (una fila de `business`; casi todo va en JSON).

    Attributes:
        id: Número del negocio (lo pone la base de datos; 0 si aún no está guardado).
        user_id: Su dueño.
        sector: Clave de `SECTORS`.
        barrio: Clave de los barrios de `pisos`.
        name: Nombre (ya limpio, `clean_name`).
        palette: Índice de `PALETTES`.
        emblem: Índice de `EMBLEMS`.
        form: Forma jurídica: `autonomo` (la fase 3 traerá `sl`).
        opened_at: Cuándo se montó (epoch).
        seed: Semilla de sus habituales y de lo que se dibuja.
        price: Clave de `PRICE_LEVELS`.
        reputation: De 0 a 100.
        stock: Unidades por producto.
        regulars: Fidelidad de cada habitual, por su nombre.
        day: Día (ISO) de los contadores de hoy.
        turns_today: Turnos abiertos hoy (las franjas del negocio).
        sales_today: Ventas de hoy (con propinas).
        clients_today: Clientes atendidos hoy.
        last_turn_at: Cuándo abrió por última vez (epoch; 0 si nunca).
        open_streak: Días seguidos abriendo, hasta `last_open_day`.
        last_open_day: Último día (ISO) en que abrió.
        week: Lunes ISO de `week_totals`.
        week_totals: Cuentas de esta semana por partida (`ventas`, `compras`, ...).
        last_week: Las de la semana anterior.
        rent_debt: Alquiler y suministros que se deben al casero.
        unpaid_nights: Noches seguidas sin pagar entero.
        billed_day: Último día (ISO) cuya noche ya se cobró.
        deposit: Fianza que espera en el depósito de fianzas.
        news: Lo que ha pasado desde la última vez que se abrió el panel (las noches).
        best_turn: Las mejores ventas de un turno.
    """

    user_id: int
    sector: str
    barrio: str
    name: str
    palette: int = 0
    emblem: int = 0
    form: str = "autonomo"
    opened_at: float = 0.0
    seed: int = 0
    price: str = "normal"
    reputation: float = REPUTATION_START
    stock: dict[str, int] = field(default_factory=dict)
    regulars: dict[str, int] = field(default_factory=dict)
    day: str = ""
    turns_today: int = 0
    sales_today: int = 0
    clients_today: int = 0
    last_turn_at: float = 0.0
    open_streak: int = 0
    last_open_day: str = ""
    week: str = ""
    week_totals: dict[str, int] = field(default_factory=dict)
    last_week: dict[str, int] = field(default_factory=dict)
    rent_debt: int = 0
    unpaid_nights: int = 0
    billed_day: str = ""
    deposit: int = 0
    news: list[str] = field(default_factory=list)
    best_turn: int = 0
    id: int = 0

    @property
    def sector_info(self) -> Sector:
        """Su sector del catálogo."""
        return SECTOR_BY_KEY[self.sector]

    @property
    def barrio_info(self) -> Barrio:
        """Su barrio de `pisos`."""
        return BARRIO_BY_KEY[self.barrio]

    @property
    def stars(self) -> float:
        """Sus estrellas (1-5)."""
        return stars(self.reputation)

    @property
    def colors(self) -> tuple[str, str]:
        """`(principal, acento)` de su rótulo."""
        _name, main, accent = PALETTES[self.palette % len(PALETTES)]
        return main, accent

    @property
    def emblem_key(self) -> str:
        """Clave del emblema del rótulo (la dibuja la escena)."""
        return EMBLEMS[self.emblem % len(EMBLEMS)][0]

    def roll_over(self, today: date, week: str) -> None:
        """Pone a cero los contadores del día y pasa la semana si han cambiado.

        Idempotente: se puede llamar varias veces el mismo día.
        """
        if self.day != today.isoformat():
            self.day = today.isoformat()
            self.turns_today = self.sales_today = self.clients_today = 0
        if self.week != week:
            if self.week:
                self.last_week = dict(self.week_totals)
            self.week = week
            self.week_totals = {}

    def tell(self, line: str) -> None:
        """Apunta un aviso para la próxima vez que abra el panel (los cinco últimos)."""
        self.news = [*self.news, line][-NEWS_MAX:]

    def add(self, item: str, amount: int) -> None:
        """Suma `amount` a una partida de las cuentas de la semana (positivo o negativo)."""
        if amount:
            self.week_totals[item] = self.week_totals.get(item, 0) + amount


def clean_name(text: str) -> str:
    """El nombre de un negocio, limpio: una línea, sin menciones, enlaces ni marcas.

    Raises:
        ValueError: Si queda vacío.
    """
    # Lo que viene de un formulario es de quien lo escribe: fuera saltos, @, <#…> y
    # caracteres de formato de Markdown que romperían el panel o mencionarían a alguien.
    text = re.sub(r"<[@#][!&]?\d+>|<a?:\w+:\d+>", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\r\n\t@<>*_`~|#\\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:NAME_MAX].strip()
    if not text:
        raise ValueError("El negocio necesita un nombre.")
    return text


def regular_names(seed: int) -> tuple[str, ...]:
    """Los cinco habituales de un negocio, siempre los mismos para su semilla."""
    rng = random.Random(seed)
    return tuple(rng.sample(REGULAR_NAMES, REGULARS_PER_BUSINESS))


def usual_order(seed: int, regular: str, sector: Sector) -> dict[str, int]:
    """Lo que pide siempre un habitual (en el taller, la avería que le repite el coche)."""
    rng = random.Random(f"{seed}:{regular}")
    if sector.game == "diagnostico":
        return {rng.choice(sector.products).key: 1}
    low, high = sector.order
    count = rng.randint(low, max(low, (low + high) // 2))
    order: dict[str, int] = {}
    for _ in range(count):
        key = rng.choices(sector.products, weights=[p.weight for p in sector.products])[0].key
        order[key] = order.get(key, 0) + 1
    return order


# -- El local y lo que cuesta ----------------------------------------------------------


def rent(sector: Sector, barrio: Barrio) -> int:
    """Alquiler del local de cada noche (un mes del contrato), base sin IGIC, en Y$.

    Como `housing.reference_rent` de un local o una nave de `pisos` con los metros del
    sector: alquilar el local o tenerlo alquilado sale a lo mismo.
    """
    euros = sector.m2 * barrio.rent_m2 * KINDS[sector.kind].rent_factor
    return int(round(euros * YAPDOLLARS_PER_EURO, -1))


def deposit(sector: Sector, barrio: Barrio) -> int:
    """Fianza del local: `DEPOSIT_MONTHS` mensualidades."""
    return DEPOSIT_MONTHS * rent(sector, barrio)


@dataclass(frozen=True, slots=True)
class OpeningQuote:
    """Lo que cuesta montar un negocio.

    Attributes:
        license: Tasa de la apertura (al Estado: no hay ayuntamientos).
        deposit: Fianza del local (al depósito de fianzas; vuelve al cerrar).
        setup: Acondicionamiento del local, base.
        setup_igic: Su IGIC.
        rent: Alquiler de cada noche, base.
    """

    license: int
    deposit: int
    setup: int
    setup_igic: int
    rent: int

    @property
    def total(self) -> int:
        """Lo que sale del monedero al montarlo."""
        return self.license + self.deposit + self.setup + self.setup_igic


def opening_quote(sector: Sector, barrio: Barrio) -> OpeningQuote:
    """Lo que cuesta montar `sector` en `barrio`."""
    return OpeningQuote(
        license=sector.license,
        deposit=deposit(sector, barrio),
        setup=sector.setup,
        setup_igic=igic(sector.setup),
        rent=rent(sector, barrio),
    )


@dataclass(frozen=True, slots=True)
class NightlyBill:
    """El recibo de una noche: alquiler y suministros.

    Attributes:
        rent: Alquiler, base.
        rent_igic: IGIC del alquiler (arrendamiento de local, tipo general).
        withholding: Retención del IRPF del arrendador (art. 100 RIRPF), sale de la base:
            el inquilino la ingresa en el Estado y el resto se lo lleva el casero.
        utilities: Suministros, base.
        utilities_igic: Su IGIC.
    """

    rent: int
    rent_igic: int
    withholding: int
    utilities: int
    utilities_igic: int

    @property
    def total(self) -> int:
        """Lo que cuesta la noche entera."""
        return self.rent + self.rent_igic + self.utilities + self.utilities_igic

    @property
    def taxes(self) -> int:
        """Lo que va al Estado (IGIC y retención)."""
        return self.rent_igic + self.withholding + self.utilities_igic


def nightly_bill(business: Business) -> NightlyBill:
    """Lo que cuesta una noche del local de `business`."""
    sector, barrio = business.sector_info, business.barrio_info
    base = rent(sector, barrio)
    return NightlyBill(
        rent=base,
        rent_igic=igic(base),
        withholding=round(base * RENT_WITHHOLDING),
        utilities=sector.utilities,
        utilities_igic=igic(sector.utilities),
    )


def wholesale(product: Product, units: int) -> tuple[int, int]:
    """`(base, IGIC)` de comprar `units` unidades al mayorista."""
    base = product.cost * units
    return base, igic(base, IGIC_BY_KEY.get(product.igic, IGIC_BY_KEY["general"]).rate)


def sale_price(product: Product, price_level: str) -> int:
    """Precio de venta de una unidad con el nivel de precios del negocio (a decenas)."""
    return int(round(product.price * PRICE_BY_KEY[price_level].ticket, -1))


def spoil(business: Business) -> dict[str, int]:
    """Quita del stock lo que se pierde esta noche y devuelve las unidades perdidas."""
    lost: dict[str, int] = {}
    for product in business.sector_info.products:
        units = business.stock.get(product.key, 0)
        gone = math.ceil(units * product.spoil) if product.spoil and units else 0
        if gone:
            business.stock[product.key] = units - gone
            lost[product.key] = gone
    return lost


def drift(business: Business) -> None:
    """La clientela olvida un poco si no abriste (solo por encima de la reputación inicial)."""
    if business.reputation > REPUTATION_START:
        business.reputation = max(REPUTATION_START, business.reputation - REPUTATION_DRIFT)


# -- La demanda ----------------------------------------------------------------------


def daily_pool(sector: Sector, barrio: Barrio) -> float:
    """Clientes que da de sí un barrio al día para un sector (entre todos sus negocios)."""
    return DAILY_POOL_TURNS * sector.clients * sector.fit.get(barrio.theme, 1.0)


def turn_clients(business: Business, hour: int, rng: random.Random) -> int:
    """Clientes que vendrían a un turno a esta hora (sin mirar el cupo del barrio).

    `base × tema del barrio × hora × reputación × precio`, redondeado con un poco de azar
    para que dos turnos iguales no salgan clavados. Al menos uno.
    """
    sector, barrio = business.sector_info, business.barrio_info
    level = PRICE_BY_KEY[business.price]
    price = level.luxury_clients if barrio.theme == "lujo" else level.clients
    expected = (
        sector.clients
        * sector.fit.get(barrio.theme, 1.0)
        * sector.curve[hour % 24]
        * reputation_factor(business.reputation)
        * price
    )
    return max(1, math.floor(expected + rng.random()))


def stock_value(business: Business) -> int:
    """Lo que costó al mayorista el stock que hay (sin IGIC)."""
    return sum(p.cost * business.stock.get(p.key, 0) for p in business.sector_info.products)


#: IGIC general, para los textos.
IGIC_RATE = IGIC_GENERAL_RATE
