"""Reglas de los préstamos personales del banco (`banco`), sin Discord ni base de datos.

El banco es el de las hipotecas de `pisos` (no existe: lo prestado se crea de la nada y las
cuotas desaparecen) y mide igual: un día del bot es un mes, la cuota se cobra en el cierre
de cada noche de `pisos` y **todas las cuotas juntas (hipotecas y préstamos) no pueden
pasar del 35 % de lo que ingresas** (`housing.affordable`, con las nóminas de 30 días y el
70 % de las rentas). Así un préstamo sirve para la entrada de un piso, pero su cuota cuenta
para la hipoteca, y al revés.

**El tipo** es fijo al firmar: el Euríbor de la semana más un diferencial que depende de tu
perfil (`profile_factors`), y la oferta explica cada punto: préstamos devueltos sin fallar,
inmuebles a tu nombre, la hipoteca al día, impagos en el historial, cobrar sin nómina…
Pagar bien te hace cliente preferente.

**La letra pequeña** (`Quote`): comisión de apertura (`OPENING_FEE`, desaparece), la TAE
con todo dentro y el tipo bonificado si contratas el seguro de protección de pagos
(`INSURANCE_DISCOUNT`): una prima con cada cuota más su impuesto (IPS), y a cambio paga
hasta `INSURANCE_COVERS` cuotas que no puedas cubrir. La TAE dice si compensa.

**Echarse atrás y amortizar** (Ley 16/2011): hasta que se cobra la primera cuota (la
noche siguiente a firmar, `first_day`) se puede desistir devolviendo lo prestado, y la
comisión vuelve (los 14 días del art. 28, a escala del bot). Amortizar antes cuesta la
compensación del art. 30: el 1 % de lo que se devuelve si queda más de un año (12 cuotas)
y el 0,5 % si queda menos.

**No pagar no sale gratis:**

- Una cuota que no llega se vuelve a pedir cada noche. Con `ASNEF_INSTALLMENTS` seguidas
  sin pagar, o con deuda con el banco, entras en el fichero de morosos (`in_asnef`): ni
  préstamos ni hipotecas hasta que pagues. Sales en cuanto pagas (art. 20 de la Ley
  Orgánica 3/2018: solo mientras dura el impago, con 50 € de mínimo).
- Con `DEFAULT_INSTALLMENTS` seguidas, el banco da el préstamo por vencido: todo lo que
  queda pasa a deuda con el banco (la misma que deja una ejecución hipotecaria), que se
  cobra cada noche del monedero y **se embarga de las nóminas y pensiones por tramos del
  salario mínimo, como el art. 607 LEC** (`wage_garnish`). El IMV no se embarga.

Con deuda con Hacienda, el banco no presta (`EconomyService.housing` embargaría lo prestado).

**Rehipotecar** (🔐 Garantías, `remortgage_quote`): una hipoteca nueva sobre participaciones
que ya se tienen, para sacar dinero de lo que valen. Presta sobre lo que valen hoy en el
mercado (la tasación, que paga quien pide) hasta el 80 % si es vivienda y el 60 % si no, menos
lo que ya se debe de ese inmueble. Diseño entero en `docs/garantias.md`.

**Anticipo de rentas** (💸 Anticipos, `advance_quote`): el banco te adelanta hoy 3, 6 o 12
meses de tu parte de la renta de un inmueble alquilado (el valor actual de esas cuotas al
Euríbor + 3 %, menos un 1 % de apertura) y se los cobra cada noche como un préstamo. No mira
el 35 % al firmar (lo paga el inquilino), pero la cuota cuenta después. Uno por inmueble, no
cuenta en `MAX_LOANS` y, si el inquilino no paga, la pones tú.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bot.services.housing import DTI_MAX, IPS_RATE, MORTGAGE_SPREAD, installment
from bot.services.taxes import WAGE_YAPDOLLARS_PER_EURO, igic

#: Diferencial sobre el Euríbor de un cliente sin historial (los préstamos personales van
#: del 6 al 9 % en 2026).
LOAN_SPREAD = 0.05
#: El tipo nunca baja de aquí ni pasa de aquí, haga lo que haga el perfil.
MIN_RATE = 0.03
MAX_RATE = 0.15
#: Plazos que se ofrecen, en meses (días del bot).
TERMS = (12, 24, 48, 96)
#: Lo menos y lo más que presta en un préstamo (75.000 € a la escala general de la tienda,
#: 10 Y$ = 1 €, como un préstamo personal grande). Lo que de verdad limita es el 35 %.
MIN_PRINCIPAL = 1_000
MAX_PRINCIPAL = 750_000
#: Préstamos personales abiertos a la vez por persona (los anticipos de rentas van aparte).
MAX_LOANS = 3
#: Comisión de apertura, sobre lo prestado.
OPENING_FEE = 0.01
#: Prima del seguro de protección de pagos, sobre cada cuota.
INSURANCE_SHARE = 0.06
#: Lo que baja el tipo con el seguro.
INSURANCE_DISCOUNT = 0.01
#: Cuotas que paga el seguro si no llegas, en toda la vida del préstamo.
INSURANCE_COVERS = 3
#: Cuotas seguidas sin pagar para entrar en el fichero de morosos.
ASNEF_INSTALLMENTS = 2
#: Deuda con el banco a partir de la que se entra en el fichero (50 € a la escala general:
#: la disposición adicional sexta de la Ley Orgánica 3/2018).
ASNEF_MIN_DEBT = 500
#: Cuotas seguidas sin pagar para el vencimiento anticipado.
DEFAULT_INSTALLMENTS = 3
#: Compensación por amortizar antes (art. 30 de la Ley 16/2011): más de un año o menos.
EARLY_FEE_LONG = 0.01
EARLY_FEE_SHORT = 0.005
EARLY_FEE_MONTHS = 12
#: Salario mínimo de 2025 (1.184 € en 14 pagas, RD 87/2025), al día, a la escala de las
#: nóminas de `pala` (100 Y$ = 1 €): un día de trabajo del bot se paga como un día de verdad.
SMI_EUR_YEAR = 1_184 * 14
SMI_DAY = round(SMI_EUR_YEAR * WAGE_YAPDOLLARS_PER_EURO / 365)
#: Escala del art. 607.2 LEC sobre lo que pasa del salario mínimo: (hasta cuántos SMI, %).
GARNISH_SCALE: tuple[tuple[int, float], ...] = (
    (1, 0.0),
    (2, 0.30),
    (3, 0.50),
    (4, 0.60),
    (5, 0.75),
)
GARNISH_TOP = 0.90


#: Lo que dice el banco a quien está en el fichero de morosos (préstamos e hipotecas).
ASNEF_REFUSAL = (
    "Estás en el fichero de morosos (ASNEF): el banco no te presta hasta que pagues lo que "
    "debes. Míralo en `banco`."
)


@dataclass(frozen=True, slots=True)
class Profile:
    """Lo que mira el banco de un miembro para ponerle el tipo.

    Attributes:
        paid_loans: Préstamos que ha devuelto enteros (también los amortizados antes).
        defaults: Préstamos vencidos e hipotecas ejecutadas en su historial.
        owns_property: Si tiene algún inmueble o una VPO a su nombre.
        mortgage_current: Si tiene hipoteca y la paga al día.
        late: Si va con retraso en alguna cuota (sin llegar al fichero de morosos).
        payroll: Si cobra nómina (o pensión); si no, sus ingresos son solo rentas.
    """

    paid_loans: int = 0
    defaults: int = 0
    owns_property: bool = False
    mortgage_current: bool = False
    late: bool = False
    payroll: bool = True


@dataclass(frozen=True, slots=True)
class Factor:
    """Un punto del tipo, explicado: `("Has devuelto 2 préstamos sin fallar", -0.01)`."""

    label: str
    points: float


def profile_factors(profile: Profile, *, insured: bool, guaranteed: bool = False) -> list[Factor]:
    """Lo que sube o baja el diferencial de un miembro, en el orden en que se enseña.

    Args:
        guaranteed: Si lo avala otro miembro (`GUARANTOR_DISCOUNT`).
    """
    out = []
    if profile.paid_loans:
        n = profile.paid_loans
        out.append(
            Factor(
                f"Has devuelto {n} {'préstamo' if n == 1 else 'préstamos'} sin fallar",
                -0.005 * min(n, 3),
            )
        )
    if profile.mortgage_current:
        out.append(Factor("Pagas la hipoteca al día", -0.005))
    if profile.owns_property:
        out.append(Factor("Tienes inmuebles a tu nombre", -0.005))
    if profile.defaults:
        n = profile.defaults
        out.append(
            Factor(f"{n} {'impago' if n == 1 else 'impagos'} en tu historial", 0.015 * min(n, 3))
        )
    if profile.late:
        out.append(Factor("Vas con retraso en alguna cuota", 0.01))
    if not profile.payroll:
        out.append(Factor("Sin nómina: solo rentas", 0.01))
    if insured:
        out.append(Factor("Con el seguro de protección de pagos", -INSURANCE_DISCOUNT))
    if guaranteed:
        out.append(Factor("Con avalista", -GUARANTOR_DISCOUNT))
    return out


def loan_rate(euribor: float, factors: list[Factor]) -> float:
    """Tipo nominal anual (TIN) fijo: Euríbor + diferencial + perfil, entre los topes."""
    rate = euribor + LOAN_SPREAD + sum(f.points for f in factors)
    return round(min(MAX_RATE, max(MIN_RATE, rate)), 4)


@dataclass(frozen=True, slots=True)
class Quote:
    """La oferta de un préstamo, como la ficha de información normalizada.

    Attributes:
        principal: Lo que se presta.
        months: Plazo en meses (días del bot).
        rate: TIN anual.
        payment: Cuota de cada mes, sin seguro.
        fee: Comisión de apertura (se descuenta al firmar).
        premium: Prima del seguro con cada cuota (0 sin seguro).
        premium_tax: IPS de esa prima.
    """

    principal: int
    months: int
    rate: float
    payment: int
    fee: int
    premium: int = 0
    premium_tax: int = 0

    @property
    def monthly(self) -> int:
        """Lo que sale cada mes: cuota, prima y su impuesto."""
        return self.payment + self.premium + self.premium_tax

    @property
    def received(self) -> int:
        """Lo que llega al monedero al firmar."""
        return self.principal - self.fee

    @property
    def total(self) -> int:
        """Lo que se paga en total si se lleva hasta el final."""
        return self.monthly * self.months

    @property
    def cost(self) -> int:
        """Lo que cuesta el préstamo: todo lo que se paga de más sobre lo recibido."""
        return self.total - self.received

    @property
    def tae(self) -> float:
        """Tasa anual equivalente con comisión, prima e impuesto dentro."""
        return annual_equivalent(self.received, self.monthly, self.months)


def quote(principal: int, months: int, rate: float, *, insured: bool) -> Quote:
    """La oferta de prestar `principal` a `months` meses al tipo `rate`."""
    payment = installment(principal, rate, months)
    premium = round(payment * INSURANCE_SHARE) if insured else 0
    return Quote(
        principal=principal,
        months=months,
        rate=rate,
        payment=payment,
        fee=math.ceil(principal * OPENING_FEE),
        premium=premium,
        premium_tax=round(premium * IPS_RATE),
    )


def annual_equivalent(received: int, monthly: int, months: int) -> float:
    """TAE: el tipo anual efectivo con el que lo recibido vale lo mismo que lo pagado.

    Busca por bisección el tipo mensual `r` con `received = Σ monthly / (1 + r)^k` y lo
    pasa a año (`(1 + r)^12 - 1`), como el anexo I de la Ley 16/2011.
    """
    if received <= 0 or monthly * months <= received:
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(100):
        mid = (low + high) / 2
        value = monthly * (1 - (1 + mid) ** -months) / mid
        if value > received:
            low = mid
        else:
            high = mid
    return (1 + low) ** 12 - 1


def max_principal(room: int, rate: float, months: int, *, insured: bool) -> int:
    """Lo más que se puede pedir para que lo de cada mes quepa en `room`.

    Va de mayor a menor desde la fórmula de la cuota: la prima y su impuesto suben lo de
    cada mes y el redondeo de la cuota puede pasarse un Y$.
    """
    if room <= 0:
        return 0
    share = 1 + (INSURANCE_SHARE * (1 + IPS_RATE) if insured else 0)
    monthly_rate = rate / 12
    factor = months if monthly_rate <= 0 else (1 - (1 + monthly_rate) ** -months) / monthly_rate
    guess = min(MAX_PRINCIPAL, int(room / share * factor))
    while guess > 0 and quote(guess, months, rate, insured=insured).monthly > room:
        guess -= max(1, guess // 1_000)
    return max(0, guess)


def room_for(income: int, installments: int) -> int:
    """Lo que cabe aún cada mes en el 35 % de lo que ingresas, con las cuotas que ya pagas."""
    return max(0, math.floor(income * DTI_MAX) - installments)


def early_fee(amount: int, months_left: int) -> int:
    """Compensación por devolver `amount` antes de tiempo (art. 30 de la Ley 16/2011)."""
    share = EARLY_FEE_LONG if months_left > EARLY_FEE_MONTHS else EARLY_FEE_SHORT
    return math.ceil(max(0, amount) * share)


def in_asnef(unpaid_counts: list[int], bank_debt: int) -> bool:
    """Si alguien está en el fichero de morosos: cuotas seguidas sin pagar o deuda con el banco.

    Args:
        unpaid_counts: Cuotas seguidas sin pagar de cada hipoteca y préstamo.
        bank_debt: Lo que debe al banco tras una ejecución o un vencimiento.
    """
    return bank_debt >= ASNEF_MIN_DEBT or any(n >= ASNEF_INSTALLMENTS for n in unpaid_counts)


def _scaled(net: int) -> float:
    """Lo embargable de `net` cobrado en un día, por la escala del art. 607.2 LEC."""
    taken = 0.0
    floor = 0
    for multiple, share in GARNISH_SCALE:
        top = multiple * SMI_DAY
        taken += max(0, min(net, top) - floor) * share
        floor = top
    return taken + max(0, net - floor) * GARNISH_TOP


def wage_garnish(earlier_today: int, net: int) -> int:
    """Lo que se embarga de una nómina para la deuda con el banco (art. 607 LEC).

    La escala va sobre todo lo cobrado en el día, en tramos del salario mínimo diario: lo
    que no llega al SMI no se toca y del resto se embarga el 30, 50, 60, 75 o 90 %. Esta
    nómina paga lo que sube el embargo del día con ella (redondeo a favor del miembro).

    Args:
        earlier_today: Neto de las nóminas y pensiones ya cobradas hoy.
        net: Neto de esta.
    """
    before = max(0, earlier_today)
    return max(0, int(_scaled(before + max(0, net))) - int(_scaled(before)))


# -- Rehipotecar ---------------------------------------------------------------------------

#: Lo más que presta sobre lo tasado: el 80 % si es vivienda y el 60 % si no (locales,
#: garajes, trasteros, naves), los límites de los préstamos que respaldan los bonos
#: garantizados (art. 23.2 del RDL 24/2021; antes, art. 5 del RD 716/2009). De ahí se resta lo
#: que ya se debe de ese inmueble.
REMORTGAGE_LTV_HOME = 0.8
REMORTGAGE_LTV_OTHER = 0.6
#: Diferencial sobre el Euríbor: medio punto más que la hipoteca de compra (va detrás en el
#: rango y no compra nada). Fijo al firmar, como las demás hipotecas.
REMORTGAGE_SPREAD = MORTGAGE_SPREAD + 0.005
#: Plazos que se ofrecen, en meses (días del bot): 10, 20 y 30 años.
REMORTGAGE_TERMS = (120, 240, 360)
#: Lo menos que presta (1.000 € a la escala general).
REMORTGAGE_MIN = 10_000
#: Comisión de apertura, sobre lo prestado (art. 14.4 de la Ley 5/2019: una sola vez).
#: Desaparece. Es un préstamo hipotecario: va por la Ley 5/2019 y no por la de crédito al
#: consumo (art. 3.a de la Ley 16/2011), así que no hay desistimiento.
REMORTGAGE_FEE = 0.005
#: La tasación la paga quien pide (art. 14.1.e.i de la Ley 5/2019): el 0,2 % de lo tasado,
#: con un mínimo de 250 € a la escala general (las tasadoras cobran de 250 a 600 € por una
#: vivienda). Es un servicio profesional: lleva el IGIC general, que va al Estado. Notaría,
#: registro y gestoría (art. 14.1.e.ii-iii) y el AJD (art. 29 del RDL 1/1993, tras el RDL
#: 17/2018) los paga el banco, que no existe.
APPRAISAL_RATE = 0.002
APPRAISAL_MIN = 2_500


def remortgage_rate(euribor: float, *, guaranteed: bool = False) -> float:
    """Tipo de una rehipoteca: Euríbor más el diferencial (medio punto menos con avalista)."""
    return round(euribor + REMORTGAGE_SPREAD - (GUARANTOR_DISCOUNT if guaranteed else 0), 4)


def remortgage_room(value: int, owed: int, *, home: bool) -> int:
    """Lo más que presta sobre unas participaciones que valen `value` y ya deben `owed`."""
    share = REMORTGAGE_LTV_HOME if home else REMORTGAGE_LTV_OTHER
    return max(0, math.floor(value * share) - owed)


def appraisal_fee(value: int) -> int:
    """Lo que cuesta tasar algo que vale `value` (sin su IGIC)."""
    return max(APPRAISAL_MIN, math.ceil(max(0, value) * APPRAISAL_RATE))


@dataclass(frozen=True, slots=True)
class RemortgageQuote:
    """La oferta de una rehipoteca, como la ficha europea de información normalizada.

    Attributes:
        principal: Lo que se presta.
        months: Plazo en meses (días del bot).
        rate: TIN anual fijo.
        payment: Cuota de cada mes.
        fee: Comisión de apertura (desaparece).
        appraisal: La tasación (desaparece).
        appraisal_igic: Su IGIC (al Estado).
    """

    principal: int
    months: int
    rate: float
    payment: int
    fee: int
    appraisal: int
    appraisal_igic: int

    @property
    def received(self) -> int:
        """Lo que llega al monedero al firmar: lo prestado menos comisión y tasación."""
        return self.principal - self.fee - self.appraisal - self.appraisal_igic

    @property
    def total(self) -> int:
        """Lo que se paga en cuotas si se lleva hasta el final."""
        return self.payment * self.months

    @property
    def cost(self) -> int:
        """Lo que cuesta: todo lo que se paga de más sobre lo recibido."""
        return self.total - self.received

    @property
    def tae(self) -> float:
        """TAE con la comisión y la tasación dentro."""
        return annual_equivalent(self.received, self.payment, self.months)


def remortgage_quote(principal: int, months: int, rate: float, value: int) -> RemortgageQuote:
    """La oferta de prestar `principal` a `months` meses sobre algo tasado en `value`."""
    appraisal = appraisal_fee(value)
    return RemortgageQuote(
        principal=principal,
        months=months,
        rate=rate,
        payment=installment(principal, rate, months),
        fee=math.ceil(principal * REMORTGAGE_FEE),
        appraisal=appraisal,
        appraisal_igic=igic(appraisal),
    )


def max_remortgage(ltv_room: int, dti_room: int, rate: float, months: int) -> int:
    """Lo más que se puede pedir: lo que deja la tasación y que la cuota quepa en el 35 %."""
    if ltv_room <= 0 or dti_room <= 0:
        return 0
    monthly_rate = rate / 12
    factor = months if monthly_rate <= 0 else (1 - (1 + monthly_rate) ** -months) / monthly_rate
    guess = min(ltv_room, int(dti_room * factor))
    while guess > 0 and installment(guess, rate, months) > dti_room:
        guess -= max(1, guess // 1_000)
    return max(0, guess)


# -- Avalista ------------------------------------------------------------------------------

#: Cómo se apunta en el `aval` de un préstamo o una hipoteca que lo avala un miembro.
GUARANTOR_PREFIX = "miembro:"
#: Lo que baja el tipo con un avalista: el banco comparte el riesgo.
GUARANTOR_DISCOUNT = 0.005
#: Avales vivos a la vez por miembro (lo que deja ver la CIRBE a cualquier banco).
MAX_GUARANTEES = 2
#: Lo que espera la petición de aval a que el avalista conteste.
GUARANTEE_SECONDS = 600


def guarantor_tag(user_id: int) -> str:
    """El `aval` de algo que avala el miembro `user_id`."""
    return f"{GUARANTOR_PREFIX}{user_id}"


def guarantor_of(aval: str | None) -> int | None:
    """Quién avala algo (su `aval`), o `None` si no lo avala un miembro."""
    if aval is None or not aval.startswith(GUARANTOR_PREFIX):
        return None
    return int(aval.removeprefix(GUARANTOR_PREFIX))


def guaranteed_room(own: int, guarantor: int) -> int:
    """Lo que cabe al mes con un avalista: lo tuyo en el 35 % más lo suyo en su 35 %."""
    return max(0, own) + max(0, guarantor)


# -- Anticipo de rentas --------------------------------------------------------------------

#: Meses de renta que adelanta el banco (días del bot).
ADVANCE_TERMS = (3, 6, 12)
#: Diferencial sobre el Euríbor: tres puntos, porque cobra de una renta que puede faltar
#: (en España lo hacen empresas de factoring de alquileres, más caras que una hipoteca).
ADVANCE_SPREAD = 0.03
#: Comisión de apertura, sobre lo adelantado. Desaparece, como la de los préstamos.
ADVANCE_FEE = 0.01


@dataclass(frozen=True, slots=True)
class AdvanceQuote:
    """La oferta de un anticipo de rentas: el banco compra `months` cuotas de tu renta.

    Attributes:
        payment: La cuota: tu parte de la renta de hoy, que el banco se cobra cada noche.
        months: Cuántas cuotas (meses, días del bot).
        rate: TIN anual fijo con el que se descuentan.
        principal: Lo que adelanta: el valor actual de las cuotas al tipo mensual `rate / 12`,
            redondeado hacia abajo (así la última cuota siempre salda lo que queda).
        fee: Comisión de apertura (desaparece).
    """

    payment: int
    months: int
    rate: float
    principal: int
    fee: int

    @property
    def received(self) -> int:
        """Lo que llega al monedero al firmar: lo adelantado menos la comisión."""
        return self.principal - self.fee

    @property
    def total(self) -> int:
        """Lo que se paga en cuotas si se lleva hasta el final."""
        return self.payment * self.months

    @property
    def cost(self) -> int:
        """Lo que cuesta: las rentas que cedes menos lo que recibes hoy."""
        return self.total - self.received

    @property
    def tae(self) -> float:
        """TAE con la comisión dentro."""
        return annual_equivalent(self.received, self.payment, self.months)


def advance_quote(payment: int, months: int, rate: float) -> AdvanceQuote:
    """La oferta de adelantar `months` cuotas de `payment` al tipo `rate`."""
    monthly_rate = rate / 12
    factor = months if monthly_rate <= 0 else (1 - (1 + monthly_rate) ** -months) / monthly_rate
    principal = math.floor(max(0, payment) * factor)
    return AdvanceQuote(
        payment=payment,
        months=months,
        rate=rate,
        principal=principal,
        fee=math.ceil(principal * ADVANCE_FEE),
    )
