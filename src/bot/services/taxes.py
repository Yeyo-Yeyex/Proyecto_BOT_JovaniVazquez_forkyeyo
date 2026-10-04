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
