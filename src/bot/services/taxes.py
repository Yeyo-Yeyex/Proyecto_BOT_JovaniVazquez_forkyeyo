"""Hacienda del bot: IRPF a la española sobre los ingresos en yapdollars.

Desde la 8.0.0 el IRPF mide toda la renta con **una sola vara**: todo ingreso
sujeto, venga de una nómina, de un piso, del casino o de un premio, se pasa a
euros con el mismo tipo de cambio, `IRPF_YAPDOLLARS_PER_EURO` (`W`), y paga la
escala real. Antes había tres (las nóminas a 100 Y$ por euro, el resto a 10 y
la renta de los pisos a un treintavo), así que el mismo dinero tributaba
distinto según de dónde viniera.

Cómo funciona, imitando el sistema real:

1. **El año fiscal del bot es la semana.** La renta de una semana se pasa a
   euros de un año con `annual_eur` (× 52 / `W`), se le aplica la escala estatal
   (art. 63.1 de la Ley 35/2006 del IRPF) y la de Canarias (Ley 9/2025), con el
   mínimo personal (art. 57 LIRPF y art. 18 quater del Decreto Legislativo
   1/2009) restado como manda el art. 56, y la cuota vuelve a Y$ de la semana con
   `weekly_yd`. Cualquier cifra anual en euros de la ley (los 2.000 € de gastos
   del trabajo, la base máxima de cotización, el límite de los donativos) se
   pasa igual: la semana es el año.
2. **La renta se guarda por clases** (`WeekIncome`): trabajo (con su Seguridad
   Social), actividad, inmuebles (uno por uno, art. 23 LIRPF), premios, juego,
   capital mobiliario y la Ley Beckham. `week_tax` las integra como el art. 48:
   el trabajo con los gastos del art. 19.2.f y la reducción del art. 20, los
   inmuebles con el tope de intereses y reparaciones y la reducción del art.
   23.2, el juego sin que sus pérdidas toquen nada más (art. 33.5.d) y el negativo
   de los pisos rebajando el resto de la base.
3. **Tipo estable** (`stable_rate`). En la vida real la empresa retiene con el
   sueldo previsto del año (arts. 82 a 87 del Reglamento del IRPF). Aquí cada
   cobro retiene con el tipo que dio la semana anterior y, si lo que va de esta
   ya da uno mayor, con ese: el tipo sube si se gana más deprisa y nunca baja a
   mitad de semana.
4. **Declaración de los lunes** (`week_return`). Cada lunes se calcula la cuota
   exacta de la semana cerrada, se le restan las deducciones (donativos, la
   canaria por inversión en vivienda habitual y la doble imposición del art. 80)
   y lo retenido. Si sale a pagar, se cobra sola; si sale a devolver, se presenta
   con el botón de la Renta.

La base del ahorro (los intereses) va aparte, con su escala y la misma vara
(`savings_tax`). La lotería y el Patrimonio usan la escala de los precios
(`YAPDOLLARS_PER_EURO`, 10 Y$ por euro), porque gravan premios y bienes que
cuestan lo que cuestan en la tienda y en `pisos`.

Simplificaciones conocidas: no hay mínimos familiares ni tributación conjunta,
y el Beckham paga su tipo fijo solo por el trabajo.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

#: Tipo de cambio de los precios del juego (tienda, pisos, Patrimonio, loterías).
YAPDOLLARS_PER_EURO = 10

#: Vara única del IRPF (`W`): yapdollars por euro de toda la renta sujeta, de la base
#: general y de la del ahorro. Fija cómo se siente el impuesto: con 250, la jornada
#: completa del puesto 1 de `pala` no paga IRPF y la de lo más alto de una rama paga
#: alrededor de un 35-42 % (calibrado con `docs/economia_sim.py`).
IRPF_YAPDOLLARS_PER_EURO = 250

#: Semanas de un año: la renta de una semana × 52 es la del año (la semana es el año).
WEEKS_PER_YEAR = 52

#: Ventana de lo cobrado en Hong Kong para proyectar su salaries tax, que tiene su
#: propio año fiscal y no se reinicia los lunes.
PROJECTION_WINDOW_SECONDS = 7 * 24 * 3600
_DAYS_PER_YEAR = 365


def annual_eur(weekly: float) -> float:
    """Pasa Y$ de una semana a euros de un año con la vara del IRPF."""
    return weekly * WEEKS_PER_YEAR / IRPF_YAPDOLLARS_PER_EURO


def weekly_yd(annual: float) -> float:
    """Pasa euros de un año a Y$ de una semana con la vara del IRPF."""
    return annual * IRPF_YAPDOLLARS_PER_EURO / WEEKS_PER_YEAR


#: Escala estatal, art. 63.1 LIRPF: (desde €, tipo marginal).
STATE_BRACKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.095),
    (12_450.0, 0.12),
    (20_200.0, 0.15),
    (35_200.0, 0.185),
    (60_000.0, 0.225),
    (300_000.0, 0.245),
)

#: Escala autonómica de Canarias (Ley 9/2025, efectos desde 1-1-2025).
CANARIAS_BRACKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.09),
    (13_748.0, 0.115),
    (19_422.0, 0.14),
    (35_924.0, 0.185),
    (57_566.0, 0.235),
    (93_268.0, 0.25),
    (123_745.0, 0.26),
)

STATE_PERSONAL_MINIMUM = 5_550.0
CANARIAS_PERSONAL_MINIMUM = 5_606.0

#: Quién se lleva el dinero en los mensajes del bot.
TAX_COLLECTOR = "Perro Sanxe"


def apply_scale(base: float, brackets: tuple[tuple[float, float], ...]) -> float:
    """Cuota de una escala progresiva por tramos sobre `base` euros."""
    tax = 0.0
    for index, (start, rate) in enumerate(brackets):
        if base <= start:
            break
        end = brackets[index + 1][0] if index + 1 < len(brackets) else base
        tax += (min(base, end) - start) * rate
    return tax


def regional_tax(base_eur: float) -> float:
    """Cuota íntegra autonómica (Canarias) de una base liquidable en euros."""
    base = max(0.0, base_eur)
    return apply_scale(base, CANARIAS_BRACKETS) - apply_scale(
        min(base, CANARIAS_PERSONAL_MINIMUM), CANARIAS_BRACKETS
    )


def annual_tax(base_eur: float) -> float:
    """Cuota íntegra anual (estatal + Canarias) de una base liquidable en euros.

    El mínimo personal no reduce la base: se calcula la escala sobre él y se
    resta, que es lo que dispone el art. 56 LIRPF. Por eso nunca sale negativa.
    """
    base = max(0.0, base_eur)
    state = apply_scale(base, STATE_BRACKETS) - apply_scale(
        min(base, STATE_PERSONAL_MINIMUM), STATE_BRACKETS
    )
    return state + regional_tax(base)


def format_rate(rate: float) -> str:
    """`0.1934` → `19,34 %`."""
    return f"{rate * 100:.2f}".replace(".", ",") + " %"


# -- La renta de la semana, por clases --------------------------------------------------
#
# Cada ingreso sujeto se apunta en `economy_tax_records` con su clase (`kind`) y su
# íntegro en Y$, sin conversiones: la vara se aplica aquí, al integrar la semana.

#: Rendimientos del trabajo (art. 17.1 LIRPF): las nóminas de `pala`.
INCOME_WORK = "trabajo"
#: Trabajo hecho en el extranjero: la parte de Hong Kong que tributa en España.
INCOME_FOREIGN = "extranjero"
#: Pensiones (art. 17.2.a.1ª LIRPF): trabajo sin cotización.
INCOME_PENSION = "pension"
#: Actividades económicas (art. 27 LIRPF): autónomos y la comisión del promotor.
INCOME_BUSINESS = "actividad"
#: Capital inmobiliario (art. 22 LIRPF): las rentas de `pisos`, inmueble por inmueble.
INCOME_PROPERTY = "inmueble"
#: Ganancias patrimoniales sin transmisión (art. 33.1 LIRPF): premios, logros, el bono.
INCOME_PRIZE = "premio"
#: Capital mobiliario de la base general (art. 25.4.d LIRPF): derechos de imagen.
INCOME_IMAGE = "mobiliario"
#: Trabajo bajo la Ley Beckham (art. 93 LIRPF): tipo fijo, fuera de la escala.
INCOME_BECKHAM = "beckham"
#: Imputación de rentas inmobiliarias (art. 85 LIRPF): los pisos vacíos que no son la
#: vivienda habitual. No retienen: se liquidan en la declaración.
INCOME_IMPUTED = "imputacion"

#: Imputación de rentas del art. 85 LIRPF: el 1,1 % anual del valor catastral (el de los
#: municipios con valores revisados; el general es el 2 %).
IMPUTED_RATE = 0.011

#: Gastos que el art. 19.2.f LIRPF deja restar a cualquier rendimiento del trabajo.
WORK_OTHER_EXPENSES_EUR = 2_000.0
#: Rentas distintas del trabajo por encima de las cuales no hay reducción del art. 20.
WORK_REDUCTION_OTHER_INCOME_EUR = 6_500.0


def work_income_reduction(net_eur: float) -> float:
    """Reducción por obtención de rendimientos del trabajo (art. 20 LIRPF), en euros.

    `net_eur` es el íntegro menos la Seguridad Social (los gastos del art. 19.2.a a e,
    sin los 2.000 € de la letra f). Redacción del RDL 4/2024: 7.302 € hasta 14.852 €;
    de ahí baja 1,75 € por euro hasta 17.673,52 €, luego 1,14 € por euro
    hasta 19.747,5 € y a partir de ahí es cero.
    """
    if net_eur <= 14_852:
        return 7_302.0
    if net_eur <= 17_673.52:
        return max(0.0, 7_302 - 1.75 * (net_eur - 14_852))
    if net_eur <= 19_747.5:
        return max(0.0, 2_364.34 - 1.14 * (net_eur - 17_673.52))
    return 0.0


@dataclass(frozen=True, slots=True)
class PropertySettlement:
    """Cómo queda un inmueble en una semana (`PropertyWeek.settle`).

    Attributes:
        net: Rendimiento neto reducido (negativo si los gastos pasan de la renta).
        carry_used: Intereses y reparaciones de semanas anteriores deducidos ahora.
        finance_left: Intereses y reparaciones de esta semana que no caben y pasan
            a las cuatro siguientes.
    """

    net: int
    carry_used: int
    finance_left: int


@dataclass(frozen=True, slots=True)
class PropertyWeek:
    """Un inmueble alquilado, para un copropietario, en una semana (art. 23 LIRPF).

    Attributes:
        ref: El inmueble (`Listing.key`).
        income: Rentas íntegras cobradas.
        finance: Intereses de la hipoteca y gastos de reparación y conservación. Juntos
            no pueden pasar de lo que renta el inmueble (art. 23.1.a); lo que sobra se
            deduce en las cuatro semanas siguientes con el mismo tope.
        costs: El resto de gastos deducibles (IBI, comunidad, seguro y amortización):
            pueden dejar el rendimiento en negativo, y ese negativo rebaja el resto de
            la base general (art. 48). Solo de los días alquilados.
        reduction: Reducción del art. 23.2 sobre el rendimiento neto positivo. Solo la
            tiene el alquiler de vivienda: 50 %, o 90 % en zona tensionada si el contrato
            nuevo rebaja la renta más de un 5 %.
        carry: Intereses y reparaciones de las cuatro semanas anteriores que no cupieron.
        upkeep: Reparaciones y seguro de una vivienda alquilada (ya dentro de `finance` y
            `costs`): la base de la deducción canaria del casero (art. 15 ter del Decreto
            Legislativo 1/2009).
    """

    ref: str
    income: int = 0
    finance: int = 0
    costs: int = 0
    reduction: float = 0.0
    carry: int = 0
    upkeep: int = 0

    def merged(self, other: PropertyWeek) -> PropertyWeek:
        """Suma dos tramos del mismo inmueble; la reducción, ponderada por la renta."""
        income = self.income + other.income
        if income > 0:
            reduction = (self.reduction * self.income + other.reduction * other.income) / income
        else:
            reduction = max(self.reduction, other.reduction)
        return PropertyWeek(
            ref=self.ref,
            income=income,
            finance=self.finance + other.finance,
            costs=self.costs + other.costs,
            reduction=reduction,
            carry=max(self.carry, other.carry),
            upkeep=self.upkeep + other.upkeep,
        )

    def settle(self) -> PropertySettlement:
        """Rendimiento neto reducido, con el tope y el arrastre del art. 23.1.a.

        Primero se deduce lo arrastrado (lo más viejo caduca antes) y luego lo de esta
        semana, sin pasar entre los dos de la renta del inmueble.
        """
        room = max(0, self.income)
        carry_used = min(max(0, self.carry), room)
        finance_now = min(max(0, self.finance), room - carry_used)
        net = self.income - carry_used - finance_now - max(0, self.costs)
        if net > 0:
            net = round(net * (1 - self.reduction))
        return PropertySettlement(
            net=net, carry_used=carry_used, finance_left=max(0, self.finance) - finance_now
        )


def _with_property(
    properties: tuple[PropertyWeek, ...], item: PropertyWeek
) -> tuple[PropertyWeek, ...]:
    for index, current in enumerate(properties):
        if current.ref == item.ref:
            return properties[:index] + (current.merged(item),) + properties[index + 1 :]
    return (*properties, item)


@dataclass(frozen=True, slots=True)
class WeekIncome:
    """La renta sujeta a la base general de un miembro en una semana, en Y$.

    Attributes:
        work: Íntegros del trabajo y de las pensiones.
        social: Seguridad Social del trabajador de esas nóminas (gasto deducible).
        business: Rendimiento de actividades económicas, ya sin la cuota de autónomo.
        properties: Cada inmueble alquilado (`PropertyWeek`).
        other: Rendimientos que ya llegan netos (las filas de antes de la 8.0.0).
        prizes: Ganancias patrimoniales sin transmisión (premios, logros, el bono).
        gambling: Neto del juego de la semana: los días con pérdidas compensan a los
            que ganan, y si sale negativo no rebaja nada más (art. 33.5.d LIRPF).
        image: Capital mobiliario de la base general (derechos de imagen).
        beckham: Trabajo por la Ley Beckham: su tipo fijo, fuera de la escala.
        imputed: Imputación de rentas de los pisos vacíos (art. 85 LIRPF).
        negative: Base liquidable general negativa de las cuatro semanas anteriores
            que aún se puede compensar (art. 50 LIRPF).
    """

    work: int = 0
    social: int = 0
    business: int = 0
    properties: tuple[PropertyWeek, ...] = ()
    other: int = 0
    prizes: int = 0
    gambling: int = 0
    image: int = 0
    beckham: int = 0
    imputed: int = 0
    negative: int = 0

    def plus(self, prop: PropertyWeek | None = None, **amounts: int) -> WeekIncome:
        """La misma renta con `amounts` sumados (por campo) y, si hay, otro tramo de piso."""
        changes: dict[str, object] = {
            name: getattr(self, name) + value for name, value in amounts.items()
        }
        if prop is not None:
            changes["properties"] = _with_property(self.properties, prop)
        return replace(self, **changes)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        """La renta como diccionario (para guardarla en JSON con su declaración)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> WeekIncome:
        """La renta guardada con `to_dict`; ignora campos que ya no existan."""
        names = {f.name for f in fields(cls)} - {"properties"}
        known = {f.name for f in fields(PropertyWeek)}
        properties = tuple(
            PropertyWeek(**{k: v for k, v in item.items() if k in known})  # type: ignore[arg-type]
            for item in data.get("properties", ())  # type: ignore[union-attr]
        )
        values = {k: v for k, v in data.items() if k in names}
        return cls(properties=properties, **values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class WeekTax:
    """La cuota de una semana, en Y$ (`week_tax`).

    Attributes:
        work: Rendimiento neto reducido del trabajo.
        properties: Suma de los rendimientos netos reducidos de los inmuebles.
        rest: Actividad, capital mobiliario, imputaciones y rendimientos que ya llegaban
            netos.
        gains: Premios y juego en positivo.
        base: Base liquidable general (nunca negativa).
        negative_used: Base negativa de semanas anteriores compensada ahora.
        negative: Base negativa de esta semana, que pasa a las cuatro siguientes.
        quota: Cuota íntegra (estatal + autonómica, más la de la Ley Beckham).
        regional: La parte autonómica de la cuota (límite de las deducciones canarias).
        contributions: Lo que retiene: íntegros del trabajo, actividad, premios, juego
            positivo, capital mobiliario y la parte positiva de cada inmueble.
        settlements: `(inmueble, arrastre usado, sobrante que se arrastra)`.
        expenses: Los 2.000 € de otros gastos del trabajo aplicados (art. 19.2.f).
        reduction: La reducción por rendimientos del trabajo aplicada (art. 20).
        state: La parte estatal de la cuota (escala del art. 63).
        beckham: La parte de la cuota de la Ley Beckham (art. 93).
        nets: `(inmueble, rendimiento neto reducido)` de cada inmueble.
    """

    work: int
    properties: int
    rest: int
    gains: int
    base: int
    negative_used: int
    negative: int
    quota: int
    regional: int
    contributions: int
    settlements: tuple[tuple[str, int, int], ...] = ()
    expenses: int = 0
    reduction: int = 0
    state: int = 0
    beckham: int = 0
    nets: tuple[tuple[str, int], ...] = ()

    @property
    def rate(self) -> float:
        """Tipo de la semana (0–1, dos decimales en %, como el art. 86 RIRPF)."""
        if self.contributions <= 0 or self.quota <= 0:
            return 0.0
        return round(self.quota / self.contributions * 100, 2) / 100


def week_tax(income: WeekIncome) -> WeekTax:
    """Integra una semana como el art. 48 LIRPF y calcula su cuota íntegra.

    Trabajo: íntegros menos Seguridad Social y los 2.000 € del art. 19.2.f, y la
    reducción del art. 20 si las demás rentas no pasan de 6.500 €; nada lo deja en
    negativo.
    Inmuebles: cada uno con su tope y su reducción (`PropertyWeek.settle`). El saldo de
    los rendimientos puede ser negativo y rebaja las ganancias; si la base sale negativa,
    pasa a las cuatro semanas siguientes (art. 50).
    """
    settled = [(p.ref, p.settle()) for p in income.properties]
    properties = sum(s.net for _, s in settled)
    positive_properties = sum(max(0, s.net) for _, s in settled)
    gambling = max(0, income.gambling)
    rest = income.business + income.other + income.image + income.imputed
    gains = income.prizes + gambling
    net = max(0.0, annual_eur(income.work) - annual_eur(income.social))
    # El tramo de la reducción del art. 20 se mira sin los 2.000 € de la letra f.
    reduction = 0.0
    if annual_eur(rest + positive_properties + gains) <= WORK_REDUCTION_OTHER_INCOME_EUR:
        reduction = work_income_reduction(net)
    expenses = min(WORK_OTHER_EXPENSES_EUR, net)
    net -= expenses
    reduction = min(reduction, net)
    net -= reduction
    base = net + annual_eur(rest + properties + gains)
    negative = negative_used = 0
    if base < 0:
        negative = round(weekly_yd(-base))
        base = 0.0
    else:
        used = min(base, annual_eur(max(0, income.negative)))
        negative_used = round(weekly_yd(used))
        base -= used
    beckham = annual_eur(income.beckham)
    beckham_quota = beckham_rate(beckham) * beckham if beckham > 0 else 0.0
    quota = annual_tax(base) + beckham_quota
    return WeekTax(
        work=round(weekly_yd(net)),
        properties=properties,
        rest=rest,
        gains=gains,
        base=round(weekly_yd(base)),
        negative_used=negative_used,
        negative=negative,
        quota=round(weekly_yd(quota)),
        regional=round(weekly_yd(regional_tax(base))),
        # La imputación no retiene (no hay quien pague): no entra en lo que retiene.
        contributions=(
            income.work + rest - income.imputed + positive_properties + gains + income.beckham
        ),
        settlements=tuple((ref, s.carry_used, s.finance_left) for ref, s in settled),
        expenses=round(weekly_yd(expenses)),
        reduction=round(weekly_yd(reduction)),
        state=round(weekly_yd(annual_tax(base) - regional_tax(base))),
        beckham=round(weekly_yd(beckham_quota)),
        nets=tuple((ref, s.net) for ref, s in settled),
    )


# -- Retenciones: el tipo estable ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pace:
    """Lo que mira la retención de un cobro.

    Attributes:
        previous_rate: Tipo de la semana anterior (`WeekTax.rate`): el suelo de esta.
        previous_wages: Nóminas de la semana anterior, para la Seguridad Social.
        wages: Nóminas de esta semana hasta ahora, sin el cobro.
        current: Toda la renta de esta semana hasta ahora, sin el cobro.
    """

    previous_rate: float = 0.0
    previous_wages: int = 0
    wages: int = 0
    current: WeekIncome = WeekIncome()


def stable_rate(pace: Pace, income: WeekIncome) -> float:
    """Tipo con el que retiene un cobro: el de la semana anterior o, si ya es mayor, el de esta.

    `income` es la renta de esta semana con el cobro dentro. Como la escala es progresiva,
    el tipo de esta semana solo crece al sumar renta: nunca baja a mitad de semana.
    """
    return max(pace.previous_rate, week_tax(income).rate)


@dataclass(frozen=True, slots=True)
class Withholding:
    """Retención calculada sobre un cobro.

    Attributes:
        gross: Cantidad bruta cobrada, en Y$.
        tax: Lo que se queda Hacienda, en Y$.
        rate: Tipo de retención aplicado (0–1).
    """

    gross: int
    tax: int
    rate: float

    @property
    def net(self) -> int:
        """Lo que llega al bolsillo."""
        return self.gross - self.tax


def compute_withholding(gross: int, pace: Pace) -> Withholding:
    """Retención de un premio (ganancia patrimonial de la base general) con el tipo estable.

    En la vida real los premios en metálico retienen un 19 % fijo (art. 101.7 LIRPF); en
    el bot retienen con el tipo de cada uno, para que la declaración del lunes no dé
    sustos (decisión del proyecto).
    """
    if gross <= 0:
        return Withholding(gross=max(0, gross), tax=0, rate=0.0)
    rate = stable_rate(pace, pace.current.plus(prizes=gross))
    return Withholding(gross=gross, tax=round(gross * rate), rate=rate)


#: Retención de lo ganado en el casino: el 19 % fijo, como la de los premios de juegos
#: y concursos (art. 101.7 LIRPF). La declaración del lunes lo ajusta al tipo de cada uno:
#: quien gana mucho paga allí lo que falte y quien gana poco recupera lo de más.
GAMBLING_WITHHOLDING_RATE = 0.19


def gambling_day_tax(net_gain: int) -> int:
    """Retención total que corresponde a la ganancia neta del casino de un día.

    Las ganancias de juego tributan en la base general y las pérdidas solo compensan
    ganancias de juego (art. 33.5.d LIRPF): un día con pérdidas no retiene, y la
    declaración del lunes junta los días de la semana y ajusta lo retenido.

    Args:
        net_gain: Premios menos apuestas del día, ya sin negativos.
    """
    return round(max(0, net_gain) * GAMBLING_WITHHOLDING_RATE)


def property_withholding(item: PropertyWeek, pace: Pace) -> Withholding:
    """Retención a cuenta de un cobro de renta de un inmueble.

    Retiene sobre el rendimiento neto reducido de este cobro (sin el arrastre, que se
    aplica en la declaración) con el tipo estable de toda la renta de la semana.
    """
    net = item.settle().net
    rate = stable_rate(pace, pace.current.plus(prop=item))
    return Withholding(gross=item.income, tax=round(max(0, net) * rate), rate=rate)


# -- La declaración de los lunes -------------------------------------------------------

#: Semanas que hay para presentar una declaración que sale a devolver: el plazo de cuatro
#: años para pedir una devolución (art. 66 de la Ley 58/2003) son cuatro semanas. La de
#: una semana se puede presentar las cuatro siguientes; después caduca y el dinero se
#: queda en el Estado.
CLAIM_WEEKS = 4

# Deducción por donativos: art. 19.1 de la Ley 49/2002 (redacción del Real
# Decreto-ley 6/2023). Se deduce de la cuota el 80 % de los primeros 250 € donados y el
# 40 % del resto. La base de la deducción no pasa del 10 % de la base liquidable (art.
# 69.1 LIRPF) y la deducción no puede dejar la cuota en negativo: si no has pagado
# IRPF, no recuperas nada. No se aplica el 45 % de quien dona a la misma entidad tres
# años seguidos. Los 250 € son de un año: en Y$, los de una semana con la vara del IRPF.

DONATION_FULL_RATE = 0.80
DONATION_FULL_LIMIT = round(weekly_yd(250))
DONATION_REST_RATE = 0.40
DONATION_BASE_LIMIT = 0.10


def donation_deduction(donated: int, base: int, tax_paid: int) -> int:
    """Lo que se deduce de la cuota por los donativos de una semana.

    Args:
        donated: Donado en la semana.
        base: Base liquidable de la semana, para el límite del 10 %.
        tax_paid: Cuota de la que se deduce (no puede quedar negativa).
    """
    eligible = min(donated, int(base * DONATION_BASE_LIMIT))
    if eligible <= 0 or tax_paid <= 0:
        return 0
    first = min(eligible, DONATION_FULL_LIMIT)
    deduction = first * DONATION_FULL_RATE + (eligible - first) * DONATION_REST_RATE
    return min(round(deduction), tax_paid)


# Deducción autonómica de Canarias por inversión en vivienda habitual (Decreto
# Legislativo 1/2009): un porcentaje de lo pagado en la semana por comprar la vivienda
# habitual (entrada, cuotas de la hipoteca y gastos), con una base máxima anual y solo
# por debajo de un nivel de renta (base general + ahorro). Con las de rehabilitación,
# no pasa del 15 % de la cuota íntegra autonómica. La estatal se suprimió en 2013.

#: `(base general + ahorro anual por debajo de, porcentaje)`.
HOME_DEDUCTION_RATES: tuple[tuple[float, float], ...] = ((26_035.0, 0.05), (46_455.0, 0.035))
#: Base máxima anual de la deducción, en euros.
HOME_DEDUCTION_BASE_EUR = 6_000.0
#: Tope de las deducciones canarias de vivienda sobre la cuota íntegra autonómica.
HOME_DEDUCTION_QUOTA_SHARE = 0.15


def home_deduction(invested: int, *, income: int, regional: int) -> int:
    """Deducción canaria por lo invertido en la vivienda habitual en una semana.

    Args:
        invested: Lo pagado en la semana por la vivienda habitual (Y$).
        income: Base imponible general más la del ahorro de la semana (Y$).
        regional: Cuota íntegra autonómica de la semana (Y$), para su tope.
    """
    if invested <= 0 or regional <= 0:
        return 0
    yearly = annual_eur(income)
    rate = next((r for limit, r in HOME_DEDUCTION_RATES if yearly < limit), 0.0)
    base = min(invested, round(weekly_yd(HOME_DEDUCTION_BASE_EUR)))
    return min(round(base * rate), round(regional * HOME_DEDUCTION_QUOTA_SHARE))


# Deducciones canarias del alquiler (Decreto Legislativo 1/2009, campaña 2025):
#
# - Inquilino (art. 15): el 24 % de lo pagado por el alquiler de la vivienda habitual,
#   hasta 740 € al año, si la renta (base general + ahorro) no pasa de 46.455 € y el
#   alquiler se lleva más del 10 % de ella. El bot no sabe edades: sin los 760 € de los
#   menores de 40 y los mayores de 75.
# - Casero (art. 15 ter): el 10 % de lo que se gasta en una vivienda alquilada en
#   reparaciones, conservación y seguros, hasta 150 € por inmueble y año.
#
# Todas las autonómicas juntas no pasan de la cuota íntegra autonómica (art. 18).

TENANT_DEDUCTION_RATE = 0.24
TENANT_DEDUCTION_MAX_EUR = 740.0
TENANT_INCOME_MAX_EUR = 46_455.0
TENANT_RENT_SHARE = 0.10
LANDLORD_DEDUCTION_RATE = 0.10
LANDLORD_DEDUCTION_MAX_EUR = 150.0


def tenant_deduction(rent_paid: int, *, income: int) -> int:
    """Deducción canaria del inquilino por el alquiler de una semana (art. 15).

    Args:
        rent_paid: Lo pagado en la semana por el alquiler de la vivienda habitual.
        income: Base imponible general más la del ahorro de la semana.
    """
    if rent_paid <= 0 or annual_eur(income) > TENANT_INCOME_MAX_EUR:
        return 0
    if rent_paid <= income * TENANT_RENT_SHARE:
        return 0
    return min(round(rent_paid * TENANT_DEDUCTION_RATE), round(weekly_yd(TENANT_DEDUCTION_MAX_EUR)))


def landlord_deduction(properties: tuple[PropertyWeek, ...]) -> int:
    """Deducción canaria del casero por reparaciones y seguros de una semana (art. 15 ter)."""
    cap = round(weekly_yd(LANDLORD_DEDUCTION_MAX_EUR))
    return sum(min(round(p.upkeep * LANDLORD_DEDUCTION_RATE), cap) for p in properties)


@dataclass(frozen=True, slots=True)
class WeekReturn:
    """La declaración de una semana (`week_return`), en Y$.

    Attributes:
        tax: La cuota íntegra y cómo se ha integrado la renta.
        donations: Deducción por donativos.
        home: Deducción canaria por inversión en vivienda habitual.
        foreign: Deducción por doble imposición internacional (art. 80 LIRPF).
        withheld: Lo retenido durante la semana.
        rental: Deducciones canarias del alquiler (inquilino y casero).
    """

    tax: WeekTax
    donations: int
    home: int
    foreign: int
    withheld: int
    rental: int = 0

    @property
    def deductions(self) -> int:
        """Todas las deducciones de la cuota."""
        return self.donations + self.home + self.foreign + self.rental

    @property
    def due(self) -> int:
        """Cuota líquida: la íntegra menos las deducciones."""
        return max(0, self.tax.quota - self.deductions)

    @property
    def result(self) -> int:
        """Lo que sale: positivo, a pagar; negativo, a devolver."""
        return self.due - self.withheld


def week_return(
    income: WeekIncome,
    *,
    withheld: int,
    donated: int = 0,
    home_invested: int = 0,
    foreign_credit: int = 0,
    foreign_income: int = 0,
    savings: int = 0,
    rent_paid: int = 0,
) -> WeekReturn:
    """La declaración de una semana cerrada.

    Args:
        income: Toda la renta de la semana, con los arrastres de las anteriores.
        withheld: Retenciones de la semana (ingresos y días de casino).
        donated: Donado a ONGs en la semana.
        home_invested: Pagado en la semana por la vivienda habitual.
        foreign_credit: Impuesto pagado fuera por la parte de la renta que tributa aquí.
        foreign_income: Esa renta de fuera: la deducción del art. 80 no pasa de lo que
            pagaría aquí con el tipo medio.
        savings: Base del ahorro de la semana (los intereses), para el nivel de renta
            de las deducciones canarias.
        rent_paid: Lo pagado en la semana por el alquiler de la vivienda habitual.
    """
    tax = week_tax(income)
    total = tax.base + max(0, savings)
    foreign = min(max(0, foreign_credit), round(max(0, foreign_income) * tax.rate), tax.quota)
    # Las autonómicas, juntas, no pasan de la cuota autonómica (art. 18).
    home = home_deduction(home_invested, income=total, regional=tax.regional)
    rental = tenant_deduction(rent_paid, income=total) + landlord_deduction(income.properties)
    regional = min(home + rental, tax.regional, tax.quota - foreign)
    home = min(home, regional)
    rental = regional - home
    donations = donation_deduction(donated, tax.base, tax.quota - foreign - regional)
    return WeekReturn(
        tax=tax,
        donations=donations,
        home=home,
        foreign=foreign,
        withheld=max(0, withheld),
        rental=rental,
    )


# -- Las semanas de antes de la 8.0.0 ----------------------------------------------------
#
# Hasta la 8.0.0 cada retención proyectaba con la renta desde el lunes × 365/7 a 10 Y$
# por euro y la declaración solo devolvía lo del casino y los donativos. Las semanas de
# antes del cambio se cierran con esa regla (`legacy_weekly_refund`): nadie paga de más
# ni de menos por una semana que ya había empezado con otras reglas.


def legacy_gambling_day_tax(net_gain: int, other_recent_income: int) -> int:
    """Retención de un día de casino con la regla de antes de la 8.0.0."""
    if net_gain <= 0:
        return 0
    base_eur = (other_recent_income + net_gain) * _DAYS_PER_YEAR // 7 / YAPDOLLARS_PER_EURO
    rate = round(annual_tax(base_eur) / base_eur * 100, 2) / 100 if base_eur > 0 else 0.0
    return round(net_gain * rate)


def legacy_weekly_refund(net: int, withheld: int, other_recent_income: int) -> int:
    """Lo que sale a devolver del casino en una semana de antes de la 8.0.0 (nunca a pagar)."""
    return max(0, withheld - legacy_gambling_day_tax(max(net, 0), other_recent_income))


# -- Impuesto sobre el Patrimonio -------------------------------------------------------
#
# Grava lo que se tiene, no lo que se gana. Canarias no tiene tarifa propia ni
# bonificación general, así que se aplica la escala estatal del art. 30 de la Ley
# 19/1991 con el mínimo exento de 700.000 € (art. 28 de la Ley 19/1991 y art. 29
# del Decreto Legislativo 1/2009 de Canarias).
#
# Escala: la general, 10 Y$ = 1 € (`YAPDOLLARS_PER_EURO`), así que el mínimo exento
# son 7.000.000 Y$. Hasta que llegaron los `pisos` el impuesto usaba 1 Y$ = 10 €
# (mínimo de 70.000 Y$) porque nadie podía juntar 7 millones; con inmuebles a
# precio real, esa escala valoraba un piso como si costara cien veces más y se
# comía su renta (y ponía un techo al ahorro que hacía imposible comprar lo más
# caro de la tienda). Los tipos son los reales.
#
# La base es el patrimonio neto: el efectivo, los inmuebles a su valor fiscal
# (`housing.fiscal_value`, art. 10 de la Ley 19/1991) y los objetos de la tienda
# (coleccionables y títulos: joyas, coches, cuadros) a lo que costaron sin IGIC
# (arts. 18 y 19). Roles, potenciadores y mascotas no cuentan: son derechos que
# caducan o ajuar doméstico, exento por el art. 4.Cuatro. Se aplica el límite
# conjunto con el IRPF del art. 31 (`wealth_quote`) y, por encima de 3.000.000 € de
# base, se declara el Impuesto de Solidaridad de las Grandes Fortunas.
#
# El ejercicio dura una semana, como el del IRPF: cada lunes se cobra la cuota anual
# completa sobre lo que se tiene en ese momento. Se paga con el efectivo; lo que no
# llega queda como deuda, que se cobra del monedero el lunes siguiente y se embarga
# de lo que cobre por sus pisos (art. 169 de la Ley 58/2003).

#: Mínimo exento real, en euros.
WEALTH_MINIMUM_EUR = 700_000.0
#: Yapdollars por euro en este impuesto: la escala general.
WEALTH_SCALE = YAPDOLLARS_PER_EURO
#: Mínimo exento en yapdollars.
WEALTH_MINIMUM = round(WEALTH_MINIMUM_EUR * WEALTH_SCALE)

#: Escala estatal, art. 30 de la Ley 19/1991: (desde €, tipo marginal).
WEALTH_BRACKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.002),
    (167_129.45, 0.003),
    (334_252.88, 0.005),
    (668_499.75, 0.009),
    (1_336_999.51, 0.013),
    (2_673_999.01, 0.017),
    (5_347_998.03, 0.021),
    (10_695_996.06, 0.035),
)


def wealth_tax(net_worth: int) -> int:
    """Cuota semanal del Impuesto sobre el Patrimonio para un patrimonio, en Y$.

    La base liquidable es lo que pasa del mínimo exento; se pasa a euros con
    `WEALTH_SCALE`, se le aplica la escala real y se vuelve a yapdollars.

    Args:
        net_worth: Efectivo más inmuebles a su valor fiscal.
    """
    base = net_worth - WEALTH_MINIMUM
    if base <= 0:
        return 0
    return round(apply_scale(base / WEALTH_SCALE, WEALTH_BRACKETS) * WEALTH_SCALE)


#: Límite conjunto con el IRPF (art. 31 de la Ley 19/1991): la cuota del Patrimonio y las
#: del IRPF juntas no pueden pasar del 60 % de la suma de las bases imponibles del IRPF.
#: Si se pasan, el Patrimonio baja, como mucho un 80 %, como en la ley. Todo con las cifras
#: de la misma semana (la semana es el año). No cuenta la parte del Patrimonio de lo que
#: no puede dar rentas (los objetos de la tienda).
WEALTH_JOINT_LIMIT = 0.60
WEALTH_MAX_REDUCTION = 0.80

#: Impuesto Temporal de Solidaridad de las Grandes Fortunas (art. 3 de la Ley 38/2022):
#: mismo patrimonio neto y mismo mínimo exento que el Patrimonio, pero la escala empieza
#: al 0 % hasta 3.000.000 € de base. Se descuenta el Patrimonio pagado y aplica el mismo
#: límite conjunto. Canarias no bonifica el Patrimonio, así que sale a cero: se declara y
#: no se paga. Escala: (desde €, tipo marginal).
SOLIDARITY_WEALTH_BRACKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (3_000_000.0, 0.017),
    (5_347_998.03, 0.021),
    (10_695_996.06, 0.035),
)
#: Patrimonio neto desde el que se declara: el mínimo exento más los 3.000.000 € al 0 %
#: (37.000.000 Y$ a la escala general).
SOLIDARITY_WEALTH_THRESHOLD = round(
    (WEALTH_MINIMUM_EUR + SOLIDARITY_WEALTH_BRACKETS[1][0]) * WEALTH_SCALE
)


@dataclass(frozen=True, slots=True)
class WealthQuote:
    """La cuenta del Patrimonio de un lunes.

    Attributes:
        full: Cuota íntegra (`wealth_tax`).
        due: Lo que se paga tras el límite conjunto con el IRPF.
        solidarity: Lo que se paga de Grandes Fortunas (cero en Canarias).
        subject: Si pasa del umbral de Grandes Fortunas y tiene que declararlo.
    """

    full: int
    due: int
    solidarity: int = 0
    subject: bool = False

    @property
    def reduced(self) -> int:
        """Lo que le ahorra el límite conjunto."""
        return self.full - self.due


def wealth_quote(
    net_worth: int, *, unproductive: int = 0, irpf_base: int = 0, irpf_quota: int = 0
) -> WealthQuote:
    """Patrimonio con el límite conjunto del art. 31 y las Grandes Fortunas.

    Args:
        net_worth: Base: efectivo, pisos y objetos.
        unproductive: La parte de la base que no da rentas (objetos de la tienda): su
            parte de la cuota no se rebaja con el límite conjunto.
        irpf_base: Suma de las bases imponibles del IRPF de la semana (general y ahorro).
        irpf_quota: Suma de sus cuotas íntegras.
    """
    full = wealth_tax(net_worth)
    due = full
    if full > 0:
        share = 1 - min(1.0, max(0, unproductive) / net_worth) if net_worth > 0 else 0.0
        productive = round(full * share)
        excess = max(0, irpf_quota) + productive - round(max(0, irpf_base) * WEALTH_JOINT_LIMIT)
        if excess > 0:
            due = full - min(excess, productive, round(full * WEALTH_MAX_REDUCTION))
    subject = net_worth > SOLIDARITY_WEALTH_THRESHOLD
    solidarity = 0
    if subject and full > 0:
        base = (net_worth - WEALTH_MINIMUM) / WEALTH_SCALE
        raw = round(apply_scale(base, SOLIDARITY_WEALTH_BRACKETS) * WEALTH_SCALE)
        # El mismo límite conjunto, en la misma proporción, y se descuenta el Patrimonio.
        solidarity = max(0, raw * due // full - due)
    return WealthQuote(full=full, due=due, solidarity=solidarity, subject=subject)


# -- IGIC: el impuesto al consumo ---------------------------------------------------
#
# En Canarias no hay IVA: las compras pagan el Impuesto General Indirecto
# Canario. Los tipos están en la Ley 4/2012 de Canarias, que los sacó del
# antiguo art. 27 de la Ley 20/1991: el general del 7 % (art. 51), frente al
# 21 % del IVA peninsular, el cero (art. 52), el reducido del 3 % (art. 54) y
# los incrementados del 9,5 % (art. 59.3) y del 15 % (arts. 56.1 y 59.4; era
# el 13,5 % hasta 2020), que es el de las joyas y otros lujos. La tienda deja
# elegir el tipo de cada artículo (`IGIC_RATES`); por defecto, el general.
#
# Los descuentos que se aplican en el momento de la venta no forman parte de
# la base imponible (art. 22 de la Ley 20/1991): con una rebaja, el IGIC se
# calcula sobre el precio rebajado.
#
# Los donativos no lo pagan: el IGIC grava entregas y servicios a título
# oneroso (art. 4 de la Ley 20/1991) y un donativo no tiene contraprestación.


@dataclass(frozen=True, slots=True)
class IgicRate:
    """Un tipo del IGIC.

    Attributes:
        key: Identificador estable; es lo que se guarda en cada artículo.
        label: Nombre corto para la interfaz.
        rate: Tipo (0–1).
        law: Artículo que lo fija.
        example: Qué paga ese tipo en la vida real, para elegir con criterio.
    """

    key: str
    label: str
    rate: float
    law: str
    example: str


IGIC_RATES: tuple[IgicRate, ...] = (
    IgicRate("cero", "Tipo cero", 0.0, "art. 52 de la Ley 4/2012", "pan, leche, libros"),
    IgicRate("reducido", "Reducido", 0.03, "art. 54 de la Ley 4/2012", "industria y transporte"),
    IgicRate("general", "General", 0.07, "art. 51 de la Ley 4/2012", "casi todo"),
    IgicRate(
        "incrementado", "Incrementado", 0.095, "art. 59.3 de la Ley 4/2012", "coches de hasta 11 CV"
    ),
    IgicRate("lujo", "Lujo", 0.15, "arts. 56.1 y 59.4 de la Ley 4/2012", "joyas y yates"),
)
IGIC_BY_KEY: dict[str, IgicRate] = {r.key: r for r in IGIC_RATES}
#: Tipo por defecto de cualquier compra.
IGIC_DEFAULT = IGIC_BY_KEY["general"]
#: Tipo general del IGIC.
IGIC_GENERAL_RATE = IGIC_DEFAULT.rate


def igic(base: int, rate: float = IGIC_GENERAL_RATE) -> int:
    """IGIC de una compra con base imponible `base` Y$, redondeado al Y$."""
    if base <= 0:
        return 0
    return round(base * rate)


# -- Gravamen especial sobre premios de lotería -----------------------------------------
#
# Disposición adicional 33ª de la Ley 35/2006 del IRPF: los premios de las
# loterías y apuestas de la SELAE, de la ONCE, de la Cruz Roja y de los
# organismos equivalentes de la UE (Euromillones) no van a la base general.
# Tributan aparte, con un gravamen especial del 20 % sobre lo que pase de
# 40.000 € por décimo, fracción, cupón o apuesta. Lo retiene quien paga el
# premio (aquí, el Estado) y es definitivo: no entra en la renta semanal.
#
# Los premios de loterías no son ganancia de juego del casino: no compensan
# pérdidas ni retienen por días (`gambling_day_tax` es para el casino).

LOTTERY_EXEMPT_EUR = 40_000
#: Parte exenta de cada décimo o apuesta, en yapdollars.
LOTTERY_EXEMPT = LOTTERY_EXEMPT_EUR * YAPDOLLARS_PER_EURO
LOTTERY_RATE = 0.20


def lottery_tax(prize: int) -> int:
    """Gravamen especial de UN décimo, apuesta o rasca premiado.

    Args:
        prize: Premio bruto de esa unidad (si un décimo cobra varios premios,
            la suma de todos, que es como se aplica la exención).
    """
    return round(max(0, prize - LOTTERY_EXEMPT) * LOTTERY_RATE)


# -- Nómina: Seguridad Social e IRPF del trabajo ------------------------------------------
#
# Los sueldos de `pala` no son un ingreso cualquiera: son rendimientos del
# trabajo (art. 17.1 LIRPF) y llevan cotización a la Seguridad Social. Por eso
# no usan `compute_withholding` tal cual, sino una nómina completa:
#
# 1. Seguridad Social del trabajador, tipos de 2026 del régimen general:
#    contingencias comunes 4,70 %, desempleo 1,55 %, formación profesional
#    0,10 % y MEI 0,15 %. Solo sobre la base hasta la base máxima (5.101,20 €
#    al mes).
# 2. Seguridad Social de la empresa: 23,60 + 5,50 (desempleo) + 0,20 (FOGASA)
#    + 0,60 (formación) + 0,75 (MEI) = 30,65 %, más un 1,5 % de accidentes de
#    trabajo, que en la realidad depende de la actividad. La empresa no existe
#    en el juego: lo paga «ella» y entra en el Estado igualmente (dinero nuevo,
#    a propósito: engorda los botes de la lotería).
# 3. Cotización adicional de solidaridad (art. 19 bis LGSS, tipos de 2026 del
#    RDL 3/2026) por lo que pase de la base máxima: 1,15 % hasta un 10 % más,
#    1,25 % hasta un 50 % más y 1,46 % por encima. El trabajador paga el 16,6 %
#    y la empresa el resto.
# 4. IRPF con el tipo estable de toda la renta del miembro (`stable_rate`); los
#    gastos del art. 19.2.f y la reducción del art. 20 entran en `week_tax`.
#
# La base de cotización del año se proyecta con las nóminas de la semana (las de la
# anterior si eran más), pasadas a euros con la vara del IRPF.

#: Cotización del trabajador (fracción del bruto).
SS_WORKER_RATE = 0.0470 + 0.0155 + 0.0010 + 0.0015
#: Cotización de la empresa, accidentes de trabajo incluidos.
SS_EMPLOYER_RATE = 0.2360 + 0.0550 + 0.0020 + 0.0060 + 0.0075 + 0.015
#: Base máxima de cotización de 2026, en euros al año.
SS_MAX_BASE_EUR = 5_101.20 * 12
#: Tramos de la cotización de solidaridad: (hasta × base máxima, tipo).
SOLIDARITY_BRACKETS: tuple[tuple[float, float], ...] = (
    (1.10, 0.0115),
    (1.50, 0.0125),
    (float("inf"), 0.0146),
)
#: Parte de la cotización de solidaridad que paga el trabajador.
SOLIDARITY_WORKER_SHARE = 0.166

#: Escala de las cifras de juego del trabajo: 100 Y$ por euro. Ya no es una escala
#: fiscal (el IRPF usa `IRPF_YAPDOLLARS_PER_EURO`): solo sirve para escribir en euros las
#: cifras que se inventa el juego y que no salen de ninguna ley, como el precio de las
#: formaciones, la cuota de autónomo, el billete a Hong Kong o lo que mira el banco de
#: `pisos`. Un turno de celador son 17 € a esta escala.
WAGE_YAPDOLLARS_PER_EURO = 100


def solidarity_contribution(annual_eur: float) -> float:
    """Cotización de solidaridad anual total (empresa + trabajador), en euros."""
    total = 0.0
    floor = SS_MAX_BASE_EUR
    for multiple, rate in SOLIDARITY_BRACKETS:
        ceiling = SS_MAX_BASE_EUR * multiple
        if annual_eur > floor:
            total += (min(annual_eur, ceiling) - floor) * rate
        floor = ceiling
    return total


@dataclass(frozen=True, slots=True)
class PayrollRates:
    """Tipos efectivos de una nómina (fracciones del bruto).

    Attributes:
        ss_worker: Seguridad Social del trabajador, solidaridad incluida.
        ss_employer: Seguridad Social de la empresa, solidaridad incluida.
        irpf: Retención de IRPF: el tipo estable de la semana.
        over_max_base: Si las nóminas del año pasan de la base máxima de cotización
            (y, por tanto, pagan cotización de solidaridad).
    """

    ss_worker: float
    ss_employer: float
    irpf: float
    over_max_base: bool

    @property
    def solidarity(self) -> bool:
        """Si la nómina lleva cotización de solidaridad."""
        return self.over_max_base


def payroll_rates(annual_eur: float, irpf: float = 0.0) -> PayrollRates:
    """Tipos de cotización para unas nóminas de `annual_eur` euros al año."""
    if annual_eur <= 0:
        return PayrollRates(SS_WORKER_RATE, SS_EMPLOYER_RATE, irpf, False)
    base = min(annual_eur, SS_MAX_BASE_EUR)
    solidarity = solidarity_contribution(annual_eur)
    worker = base * SS_WORKER_RATE + solidarity * SOLIDARITY_WORKER_SHARE
    employer = base * SS_EMPLOYER_RATE + solidarity * (1 - SOLIDARITY_WORKER_SHARE)
    return PayrollRates(
        ss_worker=worker / annual_eur,
        ss_employer=employer / annual_eur,
        irpf=irpf,
        over_max_base=annual_eur > SS_MAX_BASE_EUR,
    )


@dataclass(frozen=True, slots=True)
class Payslip:
    """Nómina de un turno, en Y$.

    Attributes:
        gross: Salario bruto.
        ss_worker: Cotización del trabajador (sale del bruto).
        irpf: Retención de IRPF (sale del bruto).
        ss_employer: Cotización de la empresa (no sale del bruto: la paga
            ella, pero va igual al Estado).
        rates: Tipos aplicados.
    """

    gross: int
    ss_worker: int
    irpf: int
    ss_employer: int
    rates: PayrollRates

    @property
    def net(self) -> int:
        """Lo que llega al bolsillo."""
        return self.gross - self.ss_worker - self.irpf

    @property
    def employer_cost(self) -> int:
        """Lo que le cuesta el turno a la empresa."""
        return self.gross + self.ss_employer

    @property
    def total_taxes(self) -> int:
        """Todo lo que acaba en el Estado por esta nómina."""
        return self.ss_worker + self.irpf + self.ss_employer


#: Régimen de impatriados («Ley Beckham», art. 93 LIRPF, redacción de la Ley
#: 28/2022): los rendimientos del trabajo tributan al 24 % hasta 600.000 € y al
#: 47 % a partir de ahí, en vez de por la escala.
BECKHAM_RATE = 0.24
BECKHAM_TOP_RATE = 0.47
BECKHAM_LIMIT_EUR = 600_000.0


def beckham_rate(annual_eur: float) -> float:
    """Tipo medio de la Ley Beckham para un sueldo de `annual_eur` euros al año."""
    if annual_eur <= BECKHAM_LIMIT_EUR:
        return BECKHAM_RATE
    tax = BECKHAM_LIMIT_EUR * BECKHAM_RATE + (annual_eur - BECKHAM_LIMIT_EUR) * BECKHAM_TOP_RATE
    return tax / annual_eur


def compute_payslip(gross: int, pace: Pace, *, beckham: bool = False) -> Payslip:
    """Nómina de un turno con el tipo estable de la semana.

    Args:
        gross: Bruto del turno, en Y$; positivo.
        pace: Tipo de la semana anterior y renta de esta, sin el turno. En la vida
            real la empresa retiene solo según el sueldo que paga (art. 82 RIRPF);
            aquí la retención ya mira toda la renta del miembro (decisión del
            proyecto, ver la Biblia) y la declaración del lunes ajusta el resto.
        beckham: Si tributa por la Ley Beckham (24 % fijo en vez de la escala).
            La Seguridad Social no cambia.
    """
    if gross <= 0:
        raise ValueError("El bruto de una nómina debe ser positivo.")
    wages = max(pace.previous_wages, pace.wages + gross)
    rates = payroll_rates(annual_eur(wages))
    ss_worker = round(gross * rates.ss_worker)
    income = (
        pace.current.plus(beckham=gross)
        if beckham
        else pace.current.plus(work=gross, social=ss_worker)
    )
    rate = stable_rate(pace, income)
    return Payslip(
        gross=gross,
        ss_worker=ss_worker,
        irpf=min(round(gross * rate), gross - ss_worker),
        ss_employer=round(gross * rates.ss_employer),
        rates=replace(rates, irpf=rate),
    )


def compute_self_employed_payslip(gross: int, pace: Pace) -> Payslip:
    """«Nómina» de un autónomo: sin cotización por turno y con el tipo estable.

    Los autónomos no cotizan por cada ingreso: pagan su cuota aparte (ver
    `EconomyService.charge_self_employed_fee`), que la declaración resta como gasto.
    Su rendimiento es de actividad económica (art. 27 LIRPF): sin la reducción del
    art. 20, que es solo para el trabajo.
    """
    if gross <= 0:
        raise ValueError("El bruto de una nómina debe ser positivo.")
    rate = stable_rate(pace, pace.current.plus(business=gross))
    return Payslip(
        gross=gross,
        ss_worker=0,
        irpf=min(round(gross * rate), gross),
        ss_employer=0,
        rates=PayrollRates(0.0, 0.0, rate, False),
    )


def compute_pension_payslip(gross: int, pace: Pace) -> Payslip:
    """«Nómina» de una pensión de jubilación: IRPF sin cotización.

    Las pensiones son rendimientos del trabajo (art. 17.2.a.1ª LIRPF): los gastos del
    art. 19.2.f y la reducción del art. 20 cuentan como en cualquier sueldo, pero el
    pensionista ya no cotiza y no hay empresa que cotice por él.
    """
    if gross <= 0:
        raise ValueError("El bruto de una nómina debe ser positivo.")
    rate = stable_rate(pace, pace.current.plus(work=gross))
    return Payslip(
        gross=gross,
        ss_worker=0,
        irpf=min(round(gross * rate), gross),
        ss_employer=0,
        rates=PayrollRates(0.0, 0.0, rate, False),
    )


def compute_beckham_payslip(gross: int, pace: Pace) -> Payslip:
    """`compute_payslip` con la Ley Beckham (para pasarla como función)."""
    return compute_payslip(gross, pace, beckham=True)


# -- Trabajar desde Hong Kong ------------------------------------------------------------
#
# Quien se va a Hong Kong con `pala` cobra allí, y allí paga:
#
# - **MPF** (Mandatory Provident Fund): el 5 % del sueldo el trabajador y otro
#   5 % la empresa, con un tope de ingresos de HK$30.000 al mes (como mucho
#   HK$18.000 al año cada uno).
# - **Salaries tax** (año 2025/26): escala del 2, 6, 10 y 14 % en tramos de
#   HK$50.000 y 17 % por encima, sobre lo que pasa de la deducción personal
#   (HK$132.000) y del MPF; o el tipo estándar (15 % hasta HK$5 millones, 16 %
#   después) sobre todo, si sale menos. Se paga lo menor.
#
# Y en España:
#
# - Mientras sigue siendo residente fiscal (`work.residence_phase`), tributa
#   por todo lo que gana en el mundo, pero el art. 7.p LIRPF deja exentos hasta
#   60.100 € al año por trabajos hechos en el extranjero para una empresa no
#   residente, si allí hay un impuesto parecido y no es un paraíso fiscal (Hong
#   Kong no está en la lista de la Orden HFP/115/2023). Sobre lo que pasa de la
#   exención se aplica el IRPF y se descuenta lo pagado en Hong Kong por esa
#   parte (deducción por doble imposición internacional, art. 80 LIRPF).
# - Cuando deja de ser residente, España ya no le cobra nada por ese sueldo.
#
# Los euros salen con la vara del IRPF y los dólares de Hong Kong, a un cambio de
# juego: 9 HK$ por euro.

HKD_PER_EUR = 9.0
MPF_RATE = 0.05
MPF_MAX_HKD = 18_000.0
HK_ALLOWANCE_HKD = 132_000.0
HK_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 0.02),
    (50_000.0, 0.06),
    (100_000.0, 0.10),
    (150_000.0, 0.14),
    (200_000.0, 0.17),
)
HK_STANDARD_RATE = 0.15
HK_STANDARD_TOP_RATE = 0.16
HK_STANDARD_LIMIT_HKD = 5_000_000.0
#: Exención del art. 7.p LIRPF, en Y$ por día (60.100 € al año con la vara del IRPF).
EXEMPT_7P_PER_DAY = round(60_100 * IRPF_YAPDOLLARS_PER_EURO / _DAYS_PER_YEAR)


def hk_salaries_tax(annual_hkd: float) -> float:
    """Salaries tax anual de Hong Kong (lo menor de escala y tipo estándar), en HK$."""
    mpf = min(annual_hkd * MPF_RATE, MPF_MAX_HKD)
    progressive = apply_scale(max(0.0, annual_hkd - mpf - HK_ALLOWANCE_HKD), HK_BANDS)
    base = max(0.0, annual_hkd - mpf)
    standard = (
        min(base, HK_STANDARD_LIMIT_HKD) * HK_STANDARD_RATE
        + max(0.0, base - HK_STANDARD_LIMIT_HKD) * HK_STANDARD_TOP_RATE
    )
    return min(progressive, standard)


@dataclass(frozen=True, slots=True)
class ForeignPayslip:
    """Nómina de un turno en Hong Kong, en Y$.

    Attributes:
        mpf_worker: MPF del trabajador (sale del bruto).
        mpf_employer: MPF de la empresa (no sale del bruto).
        hk_tax: Salaries tax de Hong Kong.
        exempt: Parte exenta en España por el art. 7.p LIRPF.
        irpf: IRPF español después de la deducción por doble imposición.
        double_tax_relief: Deducción por doble imposición (art. 80 LIRPF).
        resident: Si sigue siendo residente fiscal en España.
        credit: Salaries tax que corresponde a la parte que tributa en España: lo que
            la declaración puede deducir por doble imposición.
    """

    gross: int
    mpf_worker: int
    mpf_employer: int
    hk_tax: int
    exempt: int
    irpf: int
    double_tax_relief: int
    resident: bool
    credit: int = 0

    @property
    def net(self) -> int:
        """Lo que llega al bolsillo."""
        return self.gross - self.mpf_worker - self.hk_tax - self.irpf

    @property
    def taxable(self) -> int:
        """Lo que tributa en España (0 si ya no es residente)."""
        return self.gross - self.exempt if self.resident else 0

    @property
    def foreign(self) -> int:
        """Todo lo que se queda Hong Kong (MPF de los dos y salaries tax)."""
        return self.mpf_worker + self.mpf_employer + self.hk_tax


def compute_hk_payslip(
    gross: int,
    *,
    recent_hk: int,
    resident: bool,
    exempt_left: int,
    pace: Pace | None = None,
) -> ForeignPayslip:
    """Nómina de un turno trabajado desde Hong Kong.

    Args:
        gross: Bruto del turno.
        recent_hk: Bruto cobrado en Hong Kong los últimos 7 días (para
            proyectar el salaries tax, que solo ve lo de Hong Kong).
        resident: Si sigue siendo residente fiscal en España.
        exempt_left: Exención del art. 7.p que queda hoy.
        pace: Renta española de la semana, para el tipo estable del residente,
            que tributa por su renta mundial (art. 2 LIRPF).
    """
    if gross <= 0:
        raise ValueError("El bruto de una nómina debe ser positivo.")
    annual_yd = (recent_hk + gross) * _DAYS_PER_YEAR / 7
    annual_hkd = annual_yd / IRPF_YAPDOLLARS_PER_EURO * HKD_PER_EUR
    mpf_rate = min(MPF_RATE, MPF_MAX_HKD / annual_hkd) if annual_hkd else MPF_RATE
    hk_rate = hk_salaries_tax(annual_hkd) / annual_hkd if annual_hkd else 0.0
    mpf = round(gross * mpf_rate)
    hk_tax = round(gross * hk_rate)
    exempt = irpf = relief = credit = 0
    if resident:
        exempt = min(gross, max(0, exempt_left))
        taxable = gross - exempt
        if taxable > 0:
            current = pace or Pace()
            # Sin cotización española: los gastos y la reducción del trabajo van en
            # `week_tax`, como en cualquier sueldo.
            rate = stable_rate(current, current.current.plus(work=taxable))
            gross_irpf = round(taxable * rate)
            credit = round(hk_tax * taxable / gross)
            relief = min(gross_irpf, credit)
            irpf = gross_irpf - relief
    return ForeignPayslip(
        gross=gross,
        mpf_worker=mpf,
        mpf_employer=mpf,
        hk_tax=hk_tax,
        exempt=exempt,
        irpf=min(irpf, gross - mpf - hk_tax),
        double_tax_relief=relief,
        resident=resident,
        credit=credit,
    )


# -- Intereses de la cuenta: base del ahorro ---------------------------------------------
#
# El efectivo del monedero da intereses cada día, por tramos (ver
# `bot.services.interest`). Ese dinero es rendimiento del capital mobiliario
# (art. 25.2 LIRPF) y tributa en la base del ahorro, no en la general: escala de
# los arts. 66.1 y 76 LIRPF (redacción de la Ley 7/2024, desde 2025), con la parte
# estatal y la autonómica iguales, así que el tipo total va del 19 % al 30 %.
#
# Como en la vida real, cada pago lleva una retención fija del 19 % (art. 101.4
# LIRPF) y la diferencia con la escala se ajusta después. En el bot ese ajuste es
# semanal: cada lunes se liquida la semana con `savings_tax`, con la misma vara que
# la base general (`annual_eur`). Con el tope diario de los intereses, una semana entera
# no pasa de ~730 € al año: siempre en el primer tramo, así que la liquidación sale a
# cero y lo retenido es lo que se paga. Es la única renta que no se suma a las demás:
# la ley separa la base del ahorro de la general (arts. 45 a 49 LIRPF).

#: Escala del ahorro, estatal + autonómica (arts. 66.1 y 76 LIRPF): (desde €, tipo).
SAVINGS_BRACKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.19),
    (6_000.0, 0.21),
    (50_000.0, 0.23),
    (200_000.0, 0.27),
    (300_000.0, 0.30),
)


def savings_tax(weekly_gross: int) -> int:
    """Cuota de la base del ahorro sobre los intereses de una semana, en Y$.

    Args:
        weekly_gross: Intereses brutos de la semana.
    """
    if weekly_gross <= 0:
        return 0
    annual = annual_eur(weekly_gross)
    rate = apply_scale(annual, SAVINGS_BRACKETS) / annual
    return round(weekly_gross * rate)


def savings_marginal_rate(weekly_gross: int) -> float:
    """Tipo del tramo más alto de la escala del ahorro al que llega una semana."""
    annual = annual_eur(max(0, weekly_gross))
    rate = SAVINGS_BRACKETS[0][1]
    for start, bracket_rate in SAVINGS_BRACKETS:
        if annual > start:
            rate = bracket_rate
    return rate


# -- Porras: Impuesto sobre Actividades de Juego y derechos de imagen ---------------------
#
# Las porras (`bot.services.porras`) son apuestas cruzadas: los miembros apuestan
# entre ellos sobre las jugadas de otro y el bot solo hace de operador (art. 3.c
# de la Ley 13/2011, de regulación del juego). El operador cobra una comisión
# sobre lo apostado y sus ingresos netos (lo jugado menos los premios) pagan el
# Impuesto sobre Actividades de Juego al 20 % (art. 48.7 de la Ley 13/2011, en la
# redacción de la Ley 6/2018). Aquí el operador no existe (como la empresa de las
# nóminas): la comisión entera, el 10 % de cada apuesta, va al Estado como IAJ.
# Decisión del proyecto, para que la casa no acumule dinero que nadie usa.
#
# El protagonista de la porra cobra un 2 % del bote por prestar su imagen. Es un
# rendimiento del capital mobiliario por la cesión del derecho de imagen (art.
# 25.4.d LIRPF) y retiene un tipo fijo del 24 % (art. 101 LIRPF), no el tipo
# estable de los premios: por eso no usa `compute_withholding`. En la declaración
# entra en la base general como cualquier otra renta.

#: Comisión de las porras que va al Estado como IAJ, en tanto por uno.
GAMING_TAX_RATE = 0.10
#: Retención fija de los derechos de imagen (art. 101 LIRPF).
IMAGE_RIGHTS_WITHHOLDING = 0.24


def gaming_tax(stake: int) -> int:
    """IAJ que paga una apuesta de una porra resuelta: el 10 %, redondeado a la baja.

    Se redondea a la baja para que la suma de las de todos nunca pase del bote.
    """
    return max(0, stake) * round(GAMING_TAX_RATE * 100) // 100


def image_rights_withholding(gross: int) -> int:
    """Retención del 24 % sobre los derechos de imagen de una porra."""
    return round(max(0, gross) * IMAGE_RIGHTS_WITHHOLDING)


# -- Rastro: ventas de segunda mano entre miembros (`bot.services.rastro`) ------------------
#
# Una venta entre particulares no lleva IGIC (quien vende no es empresario, art. 4
# de la Ley 20/1991): paga el Impuesto sobre Transmisiones Patrimoniales Onerosas,
# a cargo de quien compra (art. 8.a del Real Decreto Legislativo 1/1993). En
# Canarias, los bienes muebles van al 5,5 % (art. 31.1.d del Decreto Legislativo
# 1/2009). La base es el valor real del bien (art. 10.1 RDL 1/1993), que Hacienda
# comprueba con sus propias tablas de precios (art. 57.1.b de la Ley 58/2003, como
# los precios medios de los coches de segunda mano): por eso la base es el mayor
# entre lo pactado y el valor de referencia, y vender a 1 Y$ no la baja.
#
# Si se vende por debajo del valor, la diferencia es una donación encubierta.
# Está exenta de Donaciones por decisión del proyecto, igual que el Bizum (ver
# `bot.services.bizum`).
#
# Quien vende por más de lo que le costó tiene una ganancia patrimonial (art. 33.1
# LIRPF) de la base del ahorro (art. 46 LIRPF), que el bot cobra en el momento al
# primer tipo de la escala del ahorro, el 19 % (arts. 66.1 y 76 LIRPF), sin
# liquidación posterior.

#: ITP de los bienes muebles en Canarias, en tanto por uno.
ITP_RATE = 0.055
#: Primer tramo de la escala del ahorro, que paga la plusvalía de una venta.
CAPITAL_GAIN_RATE = 0.19


def itp(base: int) -> int:
    """ITP de una venta del Rastro sobre `base` Y$ (ya el mayor entre precio y valor)."""
    return round(max(0, base) * ITP_RATE)


def capital_gain_tax(gain: int) -> int:
    """IRPF de la plusvalía de quien vende: el 19 % de la ganancia, si la hay.

    Se redondea a la baja: nunca puede pasar de lo que cobra quien vende.
    """
    return max(0, gain) * round(CAPITAL_GAIN_RATE * 100) // 100
