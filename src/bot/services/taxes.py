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
