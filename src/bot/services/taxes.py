"""Hacienda del bot: IRPF a la española sobre los ingresos en yapdollars.

Primer bloque del sistema de impuestos. De momento solo calcula la retención
de IRPF que se aplica a los premios por subir de nivel (el
IMV está exento, como el real, por el art. 7.y LIRPF) y sobre la ganancia
neta diaria del casino (`gambling_day_tax`). Lo retenido va a la
cuenta del Estado (`STATE_ACCOUNT_ID` en `bot.repositories.economy`).

Está pensado para crecer: la tabla `economy_tax_records` guarda cada
ingreso con su retención, así que más adelante se puede hacer la declaración
anual (cuota real del ejercicio frente a lo ya retenido) sin migrar datos.

Cómo se calcula, imitando el sistema real:

1. Los yapdollars se convierten a euros con `YAPDOLLARS_PER_EURO`. Con 10 Y$
   por euro, un ingreso diario del orden del IMV a racha máxima (1.500 Y$)
   equivaldría a unos 55.000 € al año, así que las rentas del juego caen en
   los tramos donde la escala real tiene sentido. Ajustar este valor cuando
   haya más fuentes de ingresos sujetos.
2. Se proyecta la renta anual a partir de lo cobrado en los últimos 30 días
   (incluido el cobro actual). Es la misma idea que las retenciones de una
   nómina: Hacienda estima tu renta del año y te retiene a cuenta.
3. A esa base se le aplican la escala estatal (art. 63.1 de la Ley 35/2006
   del IRPF) y la autonómica de Canarias (Ley 9/2025 de Presupuestos de
   Canarias para 2026), descontando en cada una el mínimo personal (art. 57
   LIRPF: 5.550 €; art. 18 quater del Decreto Legislativo 1/2009 de
   Canarias: 5.606 €), tal y como hace el art. 56 LIRPF.
4. Tipo de retención = cuota / base, redondeado a dos decimales como pide el
   art. 86 del Reglamento del IRPF. Se aplica al cobro y se redondea a Y$.

Simplificaciones conocidas: no hay reducción por rendimientos del trabajo,
ni mínimos familiares, ni límite excluyente de retención (art. 81 RIRPF).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Tipo de cambio de juego. Ver punto 1 del docstring del módulo.
YAPDOLLARS_PER_EURO = 10

#: Ventana que se usa para proyectar la renta anual.
PROJECTION_WINDOW_SECONDS = 30 * 24 * 3600
_DAYS_PER_YEAR = 365
_WINDOW_DAYS = 30

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


def annual_tax(base_eur: float) -> float:
    """Cuota íntegra anual (estatal + Canarias) de una base liquidable en euros.

    El mínimo personal no reduce la base: se calcula la escala sobre él y se
    resta, que es lo que dispone el art. 56 LIRPF. Por eso nunca sale negativa.
    """
    base = max(0.0, base_eur)
    state = apply_scale(base, STATE_BRACKETS) - apply_scale(
        min(base, STATE_PERSONAL_MINIMUM), STATE_BRACKETS
    )
    regional = apply_scale(base, CANARIAS_BRACKETS) - apply_scale(
        min(base, CANARIAS_PERSONAL_MINIMUM), CANARIAS_BRACKETS
    )
    return state + regional


def withholding_rate(projected_annual_yd: int) -> float:
    """Tipo de retención (0–1, dos decimales en %) para una renta anual en Y$."""
    base_eur = projected_annual_yd / YAPDOLLARS_PER_EURO
    if base_eur <= 0:
        return 0.0
    return round(annual_tax(base_eur) / base_eur * 100, 2) / 100


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


def compute_withholding(gross: int, recent_income: int) -> Withholding:
    """Retención de un cobro, dados los ingresos de los últimos 30 días.

    Args:
        gross: Cantidad bruta del cobro, en Y$.
        recent_income: Ingresos brutos sujetos a IRPF de los últimos 30
            días, sin contar este cobro.
    """
    if gross <= 0:
        return Withholding(gross=max(0, gross), tax=0, rate=0.0)
    projected = (recent_income + gross) * _DAYS_PER_YEAR // _WINDOW_DAYS
    rate = withholding_rate(projected)
    return Withholding(gross=gross, tax=round(gross * rate), rate=rate)


def gambling_day_tax(net_gain: int, other_recent_income: int) -> int:
    """Retención total que corresponde a la ganancia neta del casino de un día.

    Las ganancias de juego tributan en la base general y las pérdidas solo
    compensan ganancias de juego (art. 33.5.d LIRPF). En la ley la
    compensación es por año; aquí es por día, para que perder una tarde no
    arrastre semanas.

    Args:
        net_gain: Premios menos apuestas del día, ya sin negativos.
        other_recent_income: Renta sujeta del resto de los últimos 30 días.
    """
    if net_gain <= 0:
        return 0
    return compute_withholding(net_gain, other_recent_income).tax


#: Declaraciones pendientes que se guardan como máximo. Cuando sale a devolver
#: una semana más, la más antigua caduca y el dinero se queda en el Estado.
MAX_PENDING_DECLARATIONS = 2


def weekly_refund(net: int, withheld: int, other_recent_income: int) -> int:
    """Lo que sale a devolver en la declaración semanal del casino.

    Durante la semana se retiene día a día (`gambling_day_tax`). Al cerrarla,
    las pérdidas de un día compensan las ganancias de otro: se calcula la
    retención que tocaría sobre el neto de la semana y se devuelve lo que se
    retuvo de más. Nunca sale a pagar.
    """
    return max(0, withheld - gambling_day_tax(max(net, 0), other_recent_income))


def format_rate(rate: float) -> str:
    """`0.1934` → `19,34 %`."""
    return f"{rate * 100:.2f}".replace(".", ",") + " %"


# -- Impuesto sobre el Patrimonio -------------------------------------------------------
#
# Grava lo que se tiene, no lo que se gana: castiga el dinero quieto y por eso
# empuja a gastarlo. Canarias no tiene tarifa propia ni bonificación general,
# así que se aplica la escala estatal del art. 30 de la Ley 19/1991 con el
# mínimo exento de 700.000 € (art. 28 de la Ley 19/1991 y art. 29 del Decreto
# Legislativo 1/2009 de Canarias).
#
# Escala del juego: el mínimo exento real (700.000 €) equivale a 70.000 Y$, y
# todos los tramos se escalan con el mismo factor (`WEALTH_SCALE`, 1 Y$ = 10 €
# en este impuesto). Los tipos son los reales. Es otra escala que la del IRPF
# (10 Y$ = 1 €) a propósito: con la del IRPF el mínimo serían 7 millones y no lo
# pagaría nadie.
#
# El ejercicio dura una semana, como la renta del casino: cada lunes se cobra
# la cuota anual completa sobre el saldo de ese momento. No se aplica el límite
# conjunto con el IRPF del art. 31 de la Ley 19/1991.

#: Mínimo exento en yapdollars.
WEALTH_MINIMUM = 70_000
#: Mínimo exento real, en euros.
WEALTH_MINIMUM_EUR = 700_000.0
#: Yapdollars por euro en este impuesto.
WEALTH_SCALE = WEALTH_MINIMUM / WEALTH_MINIMUM_EUR

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


def wealth_tax(balance: int) -> int:
    """Cuota semanal del Impuesto sobre el Patrimonio para un saldo, en Y$.

    La base liquidable es lo que pasa del mínimo exento; se pasa a euros con
    `WEALTH_SCALE`, se le aplica la escala real y se vuelve a yapdollars.
    """
    base = balance - WEALTH_MINIMUM
    if base <= 0:
        return 0
    return round(apply_scale(base / WEALTH_SCALE, WEALTH_BRACKETS) * WEALTH_SCALE)


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


# -- Deducción por donativos ---------------------------------------------------------
#
# Art. 19.1 de la Ley 49/2002 (redacción del Real Decreto-ley 6/2023): se
# deduce de la cuota del IRPF el 80 % de los primeros 250 € donados y el 40 %
# del resto. La base de la deducción no puede pasar del 10 % de la base
# liquidable (art. 69.1 LIRPF) y la deducción no puede dejar la cuota en
# negativo: si no has pagado IRPF, no recuperas nada. No se aplica el 45 % por
# donar tres años seguidos a la misma entidad.
#
# En el bot la cuota es el IRPF retenido en la semana, y la deducción sale a
# devolver en la renta del lunes junto con lo del casino.

DONATION_FULL_RATE = 0.80
DONATION_FULL_LIMIT = 250 * YAPDOLLARS_PER_EURO
DONATION_REST_RATE = 0.40
DONATION_BASE_LIMIT = 0.10


def donation_deduction(donated: int, base: int, tax_paid: int) -> int:
    """Lo que se recupera en la renta por los donativos de una semana.

    Args:
        donated: Donado en la semana.
        base: Renta sujeta de la semana (ingresos brutos más ganancia neta
            del casino), para el límite del 10 %.
        tax_paid: IRPF pagado en la semana y no devuelto por otra vía.
    """
    eligible = min(donated, int(base * DONATION_BASE_LIMIT))
    if eligible <= 0 or tax_paid <= 0:
        return 0
    first = min(eligible, DONATION_FULL_LIMIT)
    deduction = first * DONATION_FULL_RATE + (eligible - first) * DONATION_REST_RATE
    return min(round(deduction), tax_paid)


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
# 4. IRPF con la escala de siempre (`annual_tax`), pero sobre el rendimiento
#    neto: bruto − cotizaciones del trabajador − 2.000 € de otros gastos
#    (art. 19.2.f LIRPF) − la reducción por obtención de rendimientos del
#    trabajo (art. 20 LIRPF, redacción de la Ley 7/2024).
#
# Como en `compute_withholding`, la renta anual se proyecta con lo cobrado en
# los últimos 30 días y el tipo de cada concepto se aplica al bruto del turno.

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
#: Otros gastos deducibles de los rendimientos del trabajo (art. 19.2.f LIRPF).
WORK_OTHER_EXPENSES_EUR = 2_000.0

#: Escala de las nóminas: 100 Y$ por euro, diez veces la del resto del bot.
#:
#: El resto de la economía (casino, IMV, loterías) usa `YAPDOLLARS_PER_EURO` (10
#: Y$ por euro), y a esa escala el IMV a racha máxima ya equivale a 55.000 € al
#: año. Para que trabajar compense frente al IMV, un turno del puesto más bajo
#: tiene que pagar más que un IMV entero (unos 1.700 Y$); a la escala general
#: eso serían 233.000 € al año a jornada completa y un celador pagaría un 40 %
#: de IRPF. A 100 Y$ por euro, un turno de celador son 17 € (un turno son unas
#: dos horas: el salario mínimo por hora) y la jornada completa, unos 25.000 € al
#: año, con la retención de un sueldo así. Todo lo que es renta del trabajo
#: (nómina, Seguridad Social, IRPF de la nómina, Hong Kong, Ley Beckham,
#: exenciones) se mide en esta escala. Lo que pasa de una escala a otra (la
#: renta que ven el casino y la declaración, y lo que el trabajo quita del IMV)
#: se convierte con `wage_to_fiscal`.
WAGE_YAPDOLLARS_PER_EURO = 100


def wage_to_fiscal(amount: int) -> int:
    """Pasa Y$ de nómina (100 Y$/€) a la escala general del bot (10 Y$/€)."""
    return round(amount * YAPDOLLARS_PER_EURO / WAGE_YAPDOLLARS_PER_EURO)


def work_income_reduction(net_eur: float) -> float:
    """Reducción por obtención de rendimientos del trabajo (art. 20 LIRPF), en euros.

    Redacción de la Ley 7/2024: 7.302 € hasta 14.852 € de rendimiento neto;
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
    """Tipos efectivos de una nómina (fracciones del bruto), para una renta anual.

    Attributes:
        ss_worker: Seguridad Social del trabajador, solidaridad incluida.
        ss_employer: Seguridad Social de la empresa, solidaridad incluida.
        irpf: Retención de IRPF, redondeada a dos decimales en % (art. 86 RIRPF).
        over_max_base: Si la renta pasa de la base máxima de cotización (y,
            por tanto, paga cotización de solidaridad).
    """

    ss_worker: float
    ss_employer: float
    irpf: float
    over_max_base: bool

    @property
    def solidarity(self) -> bool:
        """Si la nómina lleva cotización de solidaridad."""
        return self.over_max_base


def payroll_rates(annual_yd: int) -> PayrollRates:
    """Tipos efectivos de cotización e IRPF para un sueldo anual en Y$ de nómina."""
    annual = annual_yd / WAGE_YAPDOLLARS_PER_EURO
    if annual <= 0:
        return PayrollRates(SS_WORKER_RATE, SS_EMPLOYER_RATE, 0.0, False)
    base = min(annual, SS_MAX_BASE_EUR)
    solidarity = solidarity_contribution(annual)
    worker = base * SS_WORKER_RATE + solidarity * SOLIDARITY_WORKER_SHARE
    employer = base * SS_EMPLOYER_RATE + solidarity * (1 - SOLIDARITY_WORKER_SHARE)
    net = max(0.0, annual - worker - WORK_OTHER_EXPENSES_EUR)
    taxable = max(0.0, net - work_income_reduction(net))
    irpf = round(annual_tax(taxable) / annual * 100, 2) / 100
    return PayrollRates(
        ss_worker=worker / annual,
        ss_employer=employer / annual,
        irpf=irpf,
        over_max_base=annual > SS_MAX_BASE_EUR,
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
    def fiscal_gross(self) -> int:
        """El bruto en la escala general (lo que cuenta como renta fuera de la nómina)."""
        return wage_to_fiscal(self.gross)

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


def beckham_rate(annual_yd: int) -> float:
    """Tipo medio de la Ley Beckham para un sueldo anual en Y$ de nómina."""
    annual = annual_yd / WAGE_YAPDOLLARS_PER_EURO
    if annual <= BECKHAM_LIMIT_EUR:
        return BECKHAM_RATE
    tax = BECKHAM_LIMIT_EUR * BECKHAM_RATE + (annual - BECKHAM_LIMIT_EUR) * BECKHAM_TOP_RATE
    return tax / annual


def compute_payslip(gross: int, recent_income: int, *, beckham: bool = False) -> Payslip:
    """Nómina de un turno dados los ingresos sujetos de los últimos 30 días.

    Args:
        gross: Bruto del turno, en Y$; positivo.
        recent_income: Bruto de nómina de los últimos 30 días sin este turno.
            Como en la vida real, quien paga retiene según el sueldo que paga
            (art. 82 RIRPF), no según lo que ganas en el casino.
        beckham: Si tributa por la Ley Beckham (24 % fijo en vez de la escala).
            La Seguridad Social no cambia.
    """
    if gross <= 0:
        raise ValueError("El bruto de una nómina debe ser positivo.")
    projected = (recent_income + gross) * _DAYS_PER_YEAR // _WINDOW_DAYS
    rates = payroll_rates(projected)
    if beckham:
        rates = PayrollRates(
            rates.ss_worker, rates.ss_employer, beckham_rate(projected), rates.over_max_base
        )
    ss_worker = round(gross * rates.ss_worker)
    irpf = min(round(gross * rates.irpf), gross - ss_worker)
    return Payslip(
        gross=gross,
        ss_worker=ss_worker,
        irpf=irpf,
        ss_employer=round(gross * rates.ss_employer),
        rates=rates,
    )


def compute_self_employed_payslip(gross: int, recent_income: int) -> Payslip:
    """«Nómina» de un autónomo: sin cotización por turno, IRPF por la escala.

    Los autónomos no cotizan por cada ingreso: pagan su cuota aparte (ver
    `EconomyService.charge_self_employed_fee`). El IRPF es la escala sobre lo
    proyectado, sin la reducción del art. 20 LIRPF (que es solo para
    rendimientos del trabajo), en la escala de las nóminas.
    """
    if gross <= 0:
        raise ValueError("El bruto de una nómina debe ser positivo.")
    projected = (recent_income + gross) * _DAYS_PER_YEAR // _WINDOW_DAYS
    annual = projected / WAGE_YAPDOLLARS_PER_EURO
    rate = round(annual_tax(annual) / annual * 100, 2) / 100 if annual else 0.0
    return Payslip(
        gross=gross,
        ss_worker=0,
        irpf=round(gross * rate),
        ss_employer=0,
        rates=PayrollRates(0.0, 0.0, rate, False),
    )


def compute_beckham_payslip(gross: int, recent_income: int) -> Payslip:
    """`compute_payslip` con la Ley Beckham (para pasarla como función)."""
    return compute_payslip(gross, recent_income, beckham=True)


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
# El tipo de cambio es de juego: 9 HK$ por euro.

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
#: Exención del art. 7.p LIRPF, en Y$ de nómina por día (60.100 € al año).
EXEMPT_7P_PER_DAY = round(60_100 * WAGE_YAPDOLLARS_PER_EURO / 365)


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
    """

    gross: int
    mpf_worker: int
    mpf_employer: int
    hk_tax: int
    exempt: int
    irpf: int
    double_tax_relief: int
    resident: bool

    @property
    def net(self) -> int:
        """Lo que llega al bolsillo."""
        return self.gross - self.mpf_worker - self.hk_tax - self.irpf

    @property
    def fiscal_taxable(self) -> int:
        """Lo sujeto en España, en la escala general (0 si no es residente)."""
        return wage_to_fiscal(self.gross - self.exempt) if self.resident else 0

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
) -> ForeignPayslip:
    """Nómina de un turno trabajado desde Hong Kong (en Y$ de nómina).

    Args:
        gross: Bruto del turno.
        recent_hk: Bruto cobrado en Hong Kong los últimos 30 días (para
            proyectar el salaries tax y, si sigue siendo residente, el IRPF).
        resident: Si sigue siendo residente fiscal en España.
        exempt_left: Exención del art. 7.p que queda hoy.
    """
    if gross <= 0:
        raise ValueError("El bruto de una nómina debe ser positivo.")
    annual_yd = (recent_hk + gross) * _DAYS_PER_YEAR / _WINDOW_DAYS
    annual_hkd = annual_yd / WAGE_YAPDOLLARS_PER_EURO * HKD_PER_EUR
    mpf_rate = min(MPF_RATE, MPF_MAX_HKD / annual_hkd) if annual_hkd else MPF_RATE
    hk_rate = hk_salaries_tax(annual_hkd) / annual_hkd if annual_hkd else 0.0
    mpf = round(gross * mpf_rate)
    hk_tax = round(gross * hk_rate)
    exempt = irpf = relief = 0
    if resident:
        exempt = min(gross, max(0, exempt_left))
        taxable = gross - exempt
        if taxable > 0:
            projected = (recent_hk + gross) * _DAYS_PER_YEAR // _WINDOW_DAYS
            # Sin cotización española: el rendimiento neto es el bruto menos los
            # otros gastos, con la reducción del art. 20 LIRPF.
            annual = projected / WAGE_YAPDOLLARS_PER_EURO
            net = max(0.0, annual - WORK_OTHER_EXPENSES_EUR)
            base = max(0.0, net - work_income_reduction(net))
            rate = round(annual_tax(base) / annual * 100, 2) / 100
            gross_irpf = round(taxable * rate)
            relief = min(gross_irpf, round(hk_tax * taxable / gross))
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
    )
