"""Reglas de la sociedad limitada de un negocio de `pala` (fase 3, `docs/negocios.md`).

Lógica pura, sin Discord ni base de datos. El negocio de un autónomo pasa a ser de una
sociedad limitada unipersonal de la que su dueño es socio único y administrador: la
sociedad tiene caja propia (`economy.company_account`), paga el Impuesto sobre Sociedades
cada semana (el año fiscal del juego) y su dueño cobra de ella con un sueldo de
administrador o con dividendos. Si se hunde, solo pierde lo que hay dentro.

Escalas: las cifras de dinero de los negocios van a la de las nóminas (100 Y$ = 1 €); los
tramos y umbrales anuales en euros se pasan a una semana con la vara del IRPF
(`taxes.annual_eur`), como el resto de la renta. El estado de la sociedad va en
`Business.company` (JSON); su forma, en `Business.form` (`"sl"`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from bot.services.taxes import annual_eur, weekly_yd

#: Capitales que se pueden escribir al constituirla, en Y$ (a 100 Y$ el euro): 1 €, el
#: mínimo desde la Ley 18/2022 (art. 4.1 LSC), 3.000 €, el de antes, y 10.000 €.
CAPITALS: tuple[int, ...] = (100, 300_000, 1_000_000)
#: Por debajo de este capital (3.000 €), art. 4.1 LSC: la reserva legal es del 20 % del
#: beneficio hasta que capital y reserva sumen esto, y en la liquidación el socio responde
#: de lo que falte hasta esto si la sociedad no llega para pagar sus deudas.
FULL_CAPITAL = 300_000
#: Reserva legal ordinaria (art. 274 LSC): el 10 % del beneficio hasta el 20 % del capital.
LEGAL_RESERVE_SHARE = 0.10
LEGAL_RESERVE_CAP = 0.20
#: La del capital por debajo de 3.000 € (art. 4.2 LSC).
LOW_CAPITAL_RESERVE_SHARE = 0.20
#: Notaría y registro de la constitución telemática (art. 5 del RDL 13/2010), base sin IGIC:
#: 60 € y 40 € con estatutos tipo y capital de hasta 3.100 €; si no, 150 € y 100 €.
CONSTITUTION_FEES_SMALL = 10_000
CONSTITUTION_FEES = 25_000
SMALL_CONSTITUTION_CAPITAL = 310_000
#: La gestoría que lleva la contabilidad, las cuentas anuales y el Impuesto sobre Sociedades:
#: cada noche, como un mes del alquiler (base sin IGIC).
BOOKKEEPING_NIGHT = 1_000

#: Impuesto sobre Sociedades (Ley 27/2014, art. 29.1, con la disposición transitoria 44.ª
#: que añadió la Ley 7/2024 para 2026). Microempresa (cifra de negocios de menos de un
#: millón): el 19 % hasta 50.000 € de base y el 21 % el resto. El general, el 25 %. El 15 % de
#: las entidades de nueva creación no vale aquí: el negocio ya lo llevaba como autónomo su
#: socio único (art. 29.1.b LIS).
MICRO_BRACKETS: tuple[tuple[float, float], ...] = ((0.0, 0.19), (50_000.0, 0.21))
MICRO_TURNOVER_EUR = 1_000_000.0
GENERAL_RATE = 0.25
#: Partidas de las cuentas de la semana (`Business.week_totals`) que entran en la base del
#: Impuesto sobre Sociedades. No entran la cuota de autónomo (la paga el dueño), el IRPF, los
#: dividendos ni el propio impuesto.
TAX_ITEMS: tuple[str, ...] = (
    "ventas", "compras", "local", "marketing", "amortizacion", "apertura", "sueldo",
    "gestoria", "constitucion", "traspaso",
)  # fmt: skip

#: Retención del sueldo de administrador (art. 101.2 LIRPF): el 35 %, o el 19 % si la
#: sociedad factura menos de 100.000 € al año.
ADMIN_RETENTION = 0.35
ADMIN_RETENTION_SMALL = 0.19
SMALL_TURNOVER_EUR = 100_000.0

#: Pluriactividad (art. 313 LGSS, con el RDL 3/2026): quien cotiza a la vez por cuenta
#: ajena y en el RETA recupera el 50 % de lo que pase de este total del año, con el tope del
#: 50 % de lo pagado al RETA.
PLURIACTIVITY_LIMIT_EUR = 17_323.68
PLURIACTIVITY_REFUND = 0.5


@dataclass(frozen=True, slots=True)
class Incompatibility:
    """Un oficio de la `pala` que no deja ser administrador de una sociedad.

    Attributes:
        job: Clave del oficio.
        branch: Rama (`None` = todo el oficio).
        sectors: Solo con estos sectores (`None` = con todos): la concurrencia desleal.
        law: La norma.
        text: Por qué, en una línea.
    """

    job: str
    branch: str | None
    sectors: frozenset[str] | None
    law: str
    text: str


INCOMPATIBILITIES: tuple[Incompatibility, ...] = (
    Incompatibility(
        "politica", None, None, "art. 13 de la Ley 3/2015",
        "Un alto cargo tiene dedicación exclusiva: no administra empresas.",
    ),
    Incompatibility(
        "funcionariado", "alta", None, "art. 16.1 de la Ley 53/1984",
        "Con el complemento específico de la Alta Administración no se reconoce "
        "compatibilidad para una actividad privada.",
    ),
    Incompatibility(
        "sanidad", "publica", None, "art. 16 de la Ley 53/1984",
        "En la sanidad pública con dedicación exclusiva no se compatibiliza una empresa.",
    ),
    Incompatibility(
        "oficina", "unicornio", None, "art. 21.1 ET",
        "Tu contrato del unicornio tiene pacto de plena dedicación.",
    ),
    Incompatibility(
        "hosteleria", None, frozenset({"cafeteria", "heladeria"}), "art. 21.1 ET",
        "Sería concurrencia desleal con tu empresa de hostelería.",
    ),
)  # fmt: skip


def constitution_fees(capital: int) -> int:
    """Notaría y registro de la constitución (base sin IGIC) según el capital."""
    return CONSTITUTION_FEES_SMALL if capital <= SMALL_CONSTITUTION_CAPITAL else CONSTITUTION_FEES


def incompatibility(job: str | None, branch: str, sector: str) -> Incompatibility | None:
    """Lo que impide a quien trabaja de `job` administrar una sociedad de `sector`."""
    if job is None:
        return None
    for rule in INCOMPATIBILITIES:
        if rule.job != job or (rule.branch is not None and rule.branch != branch):
            continue
        if rule.sectors is None or sector in rule.sectors:
            return rule
    return None


def new_company(capital: int, contributed: int, now: float) -> dict[str, Any]:
    """El estado de una sociedad recién constituida (`Business.company`)."""
    return {
        "capital": capital,
        "reserve": 0,
        "retained": 0,
        "loan": 0,
        "contributed": contributed,
        "since": now,
        "losses": 0,
        "tax_debt": 0,
        "last": None,
        "offset_week": "",
        "offset": {},
        "cause_since": "",
        "paid_out": 0,
    }


def tax_base(totals: dict[str, int], offset: dict[str, int] | None = None) -> int:
    """La base de la semana: las partidas que cuentan, menos lo de antes de constituirla."""
    offset = offset or {}
    return sum(totals.get(k, 0) - offset.get(k, 0) for k in TAX_ITEMS)


def turnover_eur(weekly_sales: int) -> float:
    """La cifra de negocios del año, en euros, de lo vendido en una semana."""
    return annual_eur(weekly_sales)


def corporate_tax(base: int, *, weekly_sales: int) -> tuple[int, float]:
    """`(cuota, tipo medio)` del Impuesto sobre Sociedades de una semana con base `base`.

    Args:
        weekly_sales: Lo vendido en la semana (decide si es microempresa).
    """
    if base <= 0:
        return 0, 0.0
    if turnover_eur(weekly_sales) < MICRO_TURNOVER_EUR:
        annual = annual_eur(base)
        quota = sum(
            (min(annual, end) - start) * r
            for (start, r), end in zip(
                MICRO_BRACKETS, [*(b[0] for b in MICRO_BRACKETS[1:]), float("inf")], strict=True
            )
            if annual > start
        )
        rate = quota / annual
    else:
        rate = GENERAL_RATE
    return round(base * rate), rate


def settle_week(company: dict[str, Any], base: int, weekly_sales: int) -> dict[str, Any]:
    """Cierra el Impuesto sobre Sociedades de una semana: compensa pérdidas, liquida y reparte.

    Las bases negativas se compensan con las positivas siguientes (art. 26 LIS; sin el límite
    del 70 %, que empieza en el millón de euros). Del beneficio tras el impuesto, la reserva
    legal (`legal_reserve`) y el resto queda disponible para dividendos. Cambia `company` y
    devuelve lo de la semana: `{"base", "compensated", "tax", "rate", "reserve", "profit"}`.
    """
    compensated = 0
    if base < 0:
        company["losses"] += -base
        taxable = 0
    else:
        compensated = min(base, company["losses"])
        company["losses"] -= compensated
        taxable = base - compensated
    tax, rate = corporate_tax(taxable, weekly_sales=weekly_sales)
    profit = base - tax
    reserve = legal_reserve(company, profit) if profit > 0 else 0
    company["reserve"] += reserve
    company["retained"] += profit - reserve
    return {
        "base": base,
        "compensated": compensated,
        "tax": tax,
        "rate": rate,
        "reserve": reserve,
        "profit": profit,
    }


def legal_reserve(company: dict[str, Any], profit: int) -> int:
    """Lo que va a la reserva legal de un beneficio (arts. 4.2 y 274 LSC)."""
    capital, reserve = company["capital"], company["reserve"]
    if capital < FULL_CAPITAL:
        return max(
            0, min(round(profit * LOW_CAPITAL_RESERVE_SHARE), FULL_CAPITAL - capital - reserve)
        )
    return max(
        0, min(round(profit * LEGAL_RESERVE_SHARE), round(capital * LEGAL_RESERVE_CAP) - reserve)
    )


def dividend_room(company: dict[str, Any], cash: int) -> int:
    """Lo más que se puede repartir: lo disponible, lo que hay en caja y sin deuda con Hacienda.

    `retained` lleva los resultados tras el impuesto y la reserva legal, también los negativos
    (las pérdidas de semanas anteriores se comen primero lo disponible, art. 273.2 LSC). Las
    bases negativas (`losses`) son solo del impuesto: no se restan otra vez.
    """
    if company["tax_debt"] > 0:
        return 0
    return max(0, min(cash, company["retained"]))


def admin_retention(weekly_sales: int) -> float:
    """La retención del sueldo de administrador según lo que factura la sociedad."""
    return (
        ADMIN_RETENTION_SMALL
        if turnover_eur(weekly_sales) < SMALL_TURNOVER_EUR
        else ADMIN_RETENTION
    )


def pluriactivity_refund(general: int, self_employed: int) -> int:
    """Lo que la Seguridad Social devuelve de una semana a quien cotiza por los dos lados.

    Args:
        general: Lo cotizado por cuenta ajena (trabajador y empresa) en la semana.
        self_employed: Lo pagado al RETA en la semana.
    """
    if general <= 0 or self_employed <= 0:
        return 0
    excess = general + self_employed - weekly_yd(PLURIACTIVITY_LIMIT_EUR)
    if excess <= 0:
        return 0
    return round(min(excess * PLURIACTIVITY_REFUND, self_employed * PLURIACTIVITY_REFUND))


def guarantee(company: dict[str, Any]) -> int:
    """Lo más que responde el socio si la sociedad no llega para sus deudas (art. 4.1 LSC)."""
    return max(0, FULL_CAPITAL - company["capital"])


#: Noches (meses del juego) que tiene el administrador para remediar la causa de disolución
#: por pérdidas antes de responder él de las deudas (art. 367.1 LSC: dos meses).
DISSOLUTION_GRACE_NIGHTS = 2


def equity(company: dict[str, Any], cash: int, assets: int, debts: int) -> int:
    """El patrimonio neto: la caja, el stock y lo que queda por amortizar, menos las deudas.

    El préstamo del socio es deuda de la sociedad (no es un préstamo participativo).
    """
    return cash + assets - debts - company["loan"] - company["tax_debt"]


def in_dissolution(company: dict[str, Any], net: int) -> bool:
    """Si las pérdidas dejan el patrimonio neto por debajo de la mitad del capital (art.
    363.1.e LSC)."""
    return net < company["capital"] / 2


def liable_for_all(company: dict[str, Any], today: str) -> bool:
    """Si el socio responde de todas las deudas al liquidar: lleva más de dos meses (noches)
    en causa de disolución sin remediarla (art. 367 LSC)."""
    since = company.get("cause_since") or ""
    if not since:
        return False
    return (date.fromisoformat(today) - date.fromisoformat(since)).days >= DISSOLUTION_GRACE_NIGHTS
