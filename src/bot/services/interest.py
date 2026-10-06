"""Cuenta remunerada: intereses diarios sobre el efectivo del monedero, por tramos.

Reglas (lógica pura, sin Discord ni base de datos):

- **Cuándo:** cada día, para todos a la vez, sobre el día anterior (de
  medianoche a medianoche, hora canaria). Lo paga `bot.cogs.intereses`. Cobran
  también los que no hacen nada (decisión del proyecto): el dinero que duerme
  también trabaja.
- **Sobre qué:** el saldo medio del día, ponderado por el tiempo: cada saldo
  cuenta los segundos que estuvo en el monedero. Meter un pastizal a las 23:59
  y sacarlo a las 00:01 no da casi nada. El dinero metido en una partida
  (apuestas en curso del blackjack, las minas…) ya ha salido del monedero, así
  que no cuenta: solo el que está quieto de verdad.
- **Tipo, por tramos** (`INTEREST_TIERS`), como la letra pequeña de las cuentas
  de bienvenida: el 2,5 % diario solo hasta 4.000 Y$, el 1,25 % de ahí a 20.000
  y el 0,4 % hasta 70.000. Por encima no se paga nada: el tramo remunerado
  acaba justo donde empieza el Patrimonio. Como mucho, `INTEREST_DAILY_MAX`
  (500 Y$) brutos al día, un tercio del IMV máximo. Norma del proyecto: nada que
  dependa del saldo crece sin tope (Biblia, "Equilibrio").
- **Impuestos:** rendimiento del capital mobiliario (art. 25.2 LIRPF). Cada
  pago lleva la retención fija del 19 % (art. 101.4 LIRPF y art. 90.1 RIRPF) y
  cada lunes la semana se liquida con la escala del ahorro
  (`bot.services.taxes.savings_tax`, arts. 66.1 y 76 LIRPF): lo que falte para
  la cuota se cobra entonces (`savings_settlement`).
- **De dónde sale el dinero:** de la nada. El banco no existe en el juego: es un
  grifo pequeño, como la cotización de la empresa en `pala`. No sale de la
  cuenta del Estado, que entonces vaciaría la lotería. Lo compensan la
  retención, la liquidación del ahorro y el Patrimonio.
- **Repartir el dinero entre amigos** para exprimir el primer tramo está
  permitido (los bancos de verdad tienen el mismo agujero). Tiene logro.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from bot.services.taxes import savings_marginal_rate, savings_tax

#: Tramos de la cuenta: `(hasta qué saldo medio, interés diario)`. Cada tipo se
#: aplica solo a la parte del saldo que cae en su tramo, como el IRPF.
INTEREST_TIERS: tuple[tuple[int, float], ...] = (
    (4_000, 0.025),
    (20_000, 0.0125),
    (70_000, 0.004),
)
#: Saldo medio a partir del cual ya no se cobra más.
INTEREST_TOP = INTEREST_TIERS[-1][0]
#: Retención fija sobre cada pago (art. 101.4 LIRPF).
INTEREST_WITHHOLDING = 0.19

#: Una tirada del casino (Biblia, "Equilibrio"): la vara de medir de los logros.
SPIN_PRICE = 100
#: Tipo efectivo de retención por encima del cual el redondeo al Y$ ha jugado a
#: favor de Hacienda (en pagos de 3 Y$, 1 de retención es un 33 %).
ROUNDING_RATE = 0.25

#: Motivos del libro que no son algo que haya hecho el miembro: los cobros y
#: cargos automáticos. Sirven para saber si alguien "no ha hecho nada" un día.
AUTOMATIC_REASONS = frozenset(
    {"intereses", "irpf:intereses", "irpf:ahorro", "patrimonio", "bienvenida"}
)
#: Sufijos de los movimientos del casino (`{juego}:apuesta`, `{juego}:premio`…).
_CASINO_SUFFIXES = (":apuesta", ":premio", ":bote", ":aporte")


def interest_for(average: int) -> int:
    """Intereses brutos de un día para un saldo medio, redondeados al Y$."""
    remaining = max(0, average)
    total = 0.0
    floor = 0
    for ceiling, rate in INTEREST_TIERS:
        portion = min(remaining, ceiling - floor)
        if portion <= 0:
            break
        total += portion * rate
        remaining -= portion
        floor = ceiling
    return round(total)


#: Lo máximo que se cobra en un día (500 Y$ con los tramos actuales).
INTEREST_DAILY_MAX = interest_for(INTEREST_TOP)


def withholding(gross: int) -> int:
    """Retención del 19 % sobre un pago de intereses, redondeada al Y$."""
    return round(max(0, gross) * INTEREST_WITHHOLDING)


def tier_rate(average: int) -> float:
    """Tipo diario del tramo en el que cae el siguiente Y$ (0 por encima del tope)."""
    for ceiling, rate in INTEREST_TIERS:
        if average < ceiling:
            return rate
    return 0.0


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """Un movimiento del libro, lo justo para analizar el día.

    Attributes:
        at: Instante (epoch).
        delta: Cuánto entra (positivo) o sale (negativo).
        balance_after: Saldo después del movimiento.
        reason: Motivo estable del movimiento (`"ruleta:apuesta"`, `"imv"`…).
    """

    at: float
    delta: int
    balance_after: int
    reason: str


@dataclass(frozen=True, slots=True)
class DayFacts:
    """Lo que pasó en un monedero durante un día, sacado del libro.

    Attributes:
        average: Saldo medio ponderado por el tiempo.
        minimum: El saldo más bajo que tuvo.
        close: Saldo a medianoche.
        active: Hizo algo él (algún movimiento que no es automático).
        spent: Le salió dinero por algo suyo (no cuentan impuestos automáticos).
        payroll: Cobró alguna nómina de `pala`.
        bizum_out: Mandó algún Bizum.
        casino_net: Premios menos apuestas del casino.
        interest_in: Intereses netos que le entraron ese día (los del día anterior).
        imv: IMV cobrado ese día.
    """

    average: int
    minimum: int
    close: int
    active: bool = False
    spent: bool = False
    payroll: bool = False
    bizum_out: bool = False
    casino_net: int = 0
    interest_in: int = 0
    imv: int = 0


def analyze_day(opening: int, rows: Sequence[LedgerRow], start: float, end: float) -> DayFacts:
    """Resume un día de un monedero.

    Args:
        opening: Saldo al empezar el día (0 si el monedero aún no existía).
        rows: Movimientos del día, en orden.
        start: Medianoche al empezar (epoch).
        end: Medianoche al acabar. No siempre son 86.400 s: los días del cambio
            de hora duran 23 o 25 horas.
    """
    length = max(1.0, end - start)
    weighted = 0.0
    balance, since = max(0, opening), start
    minimum = balance
    active = spent = payroll = bizum_out = False
    casino_net = interest_in = imv = 0
    for row in rows:
        at = min(max(row.at, start), end)
        weighted += balance * (at - since)
        balance, since = max(0, row.balance_after), at
        minimum = min(minimum, balance)
        automatic = row.reason in AUTOMATIC_REASONS
        active = active or not automatic
        spent = spent or (row.delta < 0 and not automatic)
        payroll = payroll or (row.delta > 0 and row.reason.startswith("pala:"))
        bizum_out = bizum_out or row.reason == "bizum:enviado"
        if row.reason.endswith(_CASINO_SUFFIXES):
            casino_net += row.delta
        elif row.reason in ("intereses", "irpf:intereses"):
            interest_in += row.delta
        elif row.reason == "imv":
            imv += row.delta
    weighted += balance * (end - since)
    return DayFacts(
        average=int(weighted // length),
        minimum=minimum,
        close=balance,
        active=active,
        spent=spent,
        payroll=payroll,
        bizum_out=bizum_out,
        casino_net=casino_net,
        interest_in=interest_in,
        imv=imv,
    )


def project_today(
    opening: int, rows: Sequence[LedgerRow], start: float, end: float, now: float
) -> int:
    """Saldo medio de hoy si el dinero se queda como está hasta medianoche.

    Lo que ya ha pasado cuenta con su tiempo; el saldo de ahora rellena lo que
    queda de día.
    """
    past = [row for row in rows if row.at <= now]
    return analyze_day(opening, past, start, end).average


@dataclass(frozen=True, slots=True)
class Streaks:
    """Rachas de días seguidos que alimentan los logros de la cuenta.

    Attributes:
        capped: Días cobrando el máximo diario.
        floor: Días sin bajar de 4.000 (el primer tramo lleno).
        resist: Días sin bajar de 20.000.
        still: Días cobrando sin mover ni un Y$.
        ant: Días cobrando sin gastar nada.
    """

    capped: int = 0
    floor: int = 0
    resist: int = 0
    still: int = 0
    ant: int = 0


#: Saldo que no hay que perder de vista para «Manual de resistencia».
RESIST_BALANCE = INTEREST_TIERS[1][0]


@dataclass(frozen=True, slots=True)
class InterestPayment:
    """Intereses de un miembro por un día, en Y$.

    Attributes:
        user_id: Quién cobra.
        average: Saldo medio del día.
        gross: Intereses brutos.
        tax: Retención del 19 %, que va al Estado.
    """

    user_id: int
    average: int
    gross: int
    tax: int

    @property
    def net(self) -> int:
        """Lo que llega al bolsillo."""
        return self.gross - self.tax


@dataclass(frozen=True, slots=True)
class DayOutcome:
    """Resultado de un día para un miembro: lo que cobra y cómo quedan sus rachas."""

    payment: InterestPayment
    facts: DayFacts
    streaks: Streaks


def settle_day(user_id: int, facts: DayFacts, previous: Streaks) -> DayOutcome:
    """Calcula el pago de un día y actualiza las rachas.

    Las rachas de no hacer nada (`still`, `ant`) solo siguen si ese día se cobra:
    no tener un duro y no tocarlo no tiene mérito.
    """
    gross = interest_for(facts.average)
    payment = InterestPayment(user_id, facts.average, gross, withholding(gross))
    earning = gross > 0
    streaks = Streaks(
        capped=previous.capped + 1 if gross >= INTEREST_DAILY_MAX else 0,
        floor=previous.floor + 1 if facts.minimum >= INTEREST_TIERS[0][0] else 0,
        resist=previous.resist + 1 if facts.minimum >= RESIST_BALANCE else 0,
        still=previous.still + 1 if earning and not facts.active else 0,
        ant=previous.ant + 1 if earning and not facts.spent else 0,
    )
    return DayOutcome(payment, facts, streaks)


@dataclass(frozen=True, slots=True)
class SavingsSettlement:
    """Liquidación semanal de la base del ahorro de un miembro, en Y$.

    Attributes:
        user_id: Quién liquida.
        gross: Intereses brutos de la semana.
        withheld: Lo ya retenido al 19 % en cada pago.
        quota: Cuota con la escala del ahorro.
        rate: Tipo marginal alcanzado (0,19, 0,21…).
        charged: Lo que se cobra ahora (la diferencia, sin pasar del saldo).
    """

    user_id: int
    gross: int
    withheld: int
    quota: int
    rate: float
    charged: int


def savings_settlement(user_id: int, gross: int, withheld: int, balance: int) -> SavingsSettlement:
    """Liquida una semana de intereses con la escala del ahorro.

    Si por redondeos lo retenido supera la cuota, no se devuelve nada: la
    diferencia es de uno o dos Y$ y la Renta del casino no se mezcla con esto.
    """
    quota = savings_tax(gross)
    charged = min(max(0, balance), max(0, quota - withheld))
    return SavingsSettlement(user_id, gross, withheld, quota, savings_marginal_rate(gross), charged)
