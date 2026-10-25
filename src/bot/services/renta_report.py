"""Textos y carta de la declaración de la Renta (`renta`).

Lógica pura, sin Discord: el cog lo monta en su panel. Los números salen de la
declaración rehecha con sus entradas (`EconomyService.irpf_statement`), así que cuadran
al Y$ con lo que se cobró o se devolvió.

La declaración se enseña en pestañas, para no saturar a nadie:

- **Resumen:** el resultado y lo que se hace con él; si se puede dibujar, encima va la
  carta (`card_state`, pintada por `bot.services.renta_scene`).
- **Desglose:** cada fuente de renta (trabajo, alquileres, casino…) con lo que entró, la
  parte de la cuota que le toca, lo que se le retuvo y si faltó o sobró (`concepts`).
- **Paso a paso:** el porqué de cada línea con su artículo: el trabajo (gastos y
  reducción), cada inmueble (tope de intereses, arrastre y reducción), el resto de
  rentas, la integración en la base, las dos escalas, las deducciones y lo retenido por
  fuente.
- **Consejo:** cuánto habría rendido donar esa semana (`donation_advice`), rehecho con
  `taxes.week_return`, no estimado.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta

from bot.repositories.economy import PAYMENT_PENDING, PAYMENT_SPLIT
from bot.services.donations import deduction_rule_text
from bot.services.economy import IrpfStatement, format_amount, monday_epoch, week_label
from bot.services.taxes import (
    BECKHAM_LIMIT_EUR,
    CLAIM_WEEKS,
    DONATION_BASE_LIMIT,
    DONATION_FULL_LIMIT,
    DONATION_FULL_RATE,
    INCOME_BECKHAM,
    INCOME_BUSINESS,
    INCOME_FOREIGN,
    INCOME_IMAGE,
    INCOME_IMPUTED,
    INCOME_PENSION,
    INCOME_PRIZE,
    INCOME_PROPERTY,
    INCOME_WORK,
    TAX_COLLECTOR,
    WORK_REDUCTION_OTHER_INCOME_EUR,
    format_rate,
    second_installment,
    week_return,
    weekly_yd,
)

#: Cómo se llama cada fuente de lo retenido.
SOURCE_LABELS: dict[str, str] = {
    INCOME_WORK: "Nóminas",
    INCOME_FOREIGN: "Hong Kong (la parte española)",
    INCOME_PENSION: "Pensiones",
    INCOME_BUSINESS: "Actividad (autónomo, promotor)",
    INCOME_PROPERTY: "Alquileres",
    INCOME_PRIZE: "Premios, niveles y logros",
    INCOME_IMAGE: "Derechos de imagen (24 % fijo)",
    INCOME_BECKHAM: "Ley Beckham (24 % fijo)",
    "juego": "Casino (19 % de lo ganado cada día)",
    "antes": "Cobros de antes de la 8.0.0",
}
#: Inmuebles que se enseñan uno a uno en el detalle; el resto se suman.
PROPERTIES_SHOWN = 6


def _money(amount: int) -> str:
    return format_amount(amount)


def _minus(amount: int) -> str:
    return f"−{format_amount(amount)}"


def _when(epoch: float) -> str:
    """Un momento como fecha de Discord relativa (`<t:…:R>`): cada uno la ve en su hora."""
    return f"<t:{int(epoch)}:R>"


def _law(annual: float) -> str:
    """Una cifra anual en euros de la ley, en Y$ de una semana con la vara del IRPF."""
    return format_amount(round(weekly_yd(annual)))


def envelope_text(statement: IrpfStatement) -> str:
    """El sobre cerrado: ya está la declaración, pero aún no se sabe qué sale."""
    return (
        f"📑 **{TAX_COLLECTOR} ya ha hecho tus cuentas de la semana "
        f"{week_label(statement.week)}.**\n"
        "¿Te toca pagar o te devuelve? Solo hay una forma de saberlo.\n"
        "-# Si sale a pagar, tienes hasta el final del lunes para pagarla entera o en dos "
        "plazos (el 60 % ahora y el 40 % el jueves)."
    )


#: Sellos de la carta: lo que ya se ha hecho con la declaración (`seal_for`).
PAID = "PAGADA"
SPLIT = "FRACCIONADA"
OWED = "CON DEUDA"
FILED = "PRESENTADA"
EXPIRED = "CADUCADA"


def seal_for(statement: IrpfStatement, pending: Collection[date], now: float) -> str:
    """El sello de una declaración: lo que ya se hizo con ella, o `""` si falta decidir.

    Args:
        pending: Lunes de las declaraciones aún por presentar
            (`EconomyService.pending_declarations`): la fila de la declaración no sabe si
            se presentó.
        now: Ahora (epoch), para saber si una devolución sin presentar ya caducó.
    """
    filed = statement.filed
    if filed.result > 0:
        if filed.payment == PAYMENT_PENDING:
            return ""
        if filed.debt:
            return OWED
        if filed.payment == PAYMENT_SPLIT and not filed.second_at:
            return SPLIT
        return PAID
    if filed.refund <= 0 or statement.week in pending:
        return ""
    if now >= monday_epoch(statement.week + timedelta(weeks=1 + CLAIM_WEEKS)):
        return EXPIRED
    return FILED


def result_line(statement: IrpfStatement, seal: str = "") -> str:
    """El resultado en una línea, con lo que se hizo con él.

    Args:
        seal: El de `seal_for`: dice si una devolución ya se presentó o caducó.
    """
    filed = statement.filed
    if filed.result > 0:
        line = f"💸 **Te sale a pagar {_money(filed.result)}.**"
        if filed.payment == PAYMENT_PENDING:
            second = second_installment(filed.result)
            return line + (
                f" Tienes hasta el final del lunes ({_when(filed.pay_due)}) para pagarla "
                f"entera o fraccionarla: {_money(filed.result - second)} ahora y "
                f"{_money(second)} el jueves, sin intereses ni recargo. Si no eliges, se "
                "cobra entera."
            )
        if filed.payment == PAYMENT_SPLIT:
            line += f" La has fraccionado: se cobraron {_money(filed.charged)}"
            if filed.debt:
                line += f" y {_money(filed.debt)} quedaron como deuda"
            return line + (
                f". El jueves se cobran los {_money(filed.second)} que faltan "
                f"({_when(filed.second_due)})."
            )
        if filed.debt:
            return line + (
                f" Se cobraron {_money(filed.charged)} y {_money(filed.debt)} quedaron como "
                f"deuda: {TAX_COLLECTOR} se queda el 30 % de lo que cobres hasta saldarla, o "
                "la liquidas aquí."
            )
        if filed.second:
            return line + f" Pagada en dos plazos: {TAX_COLLECTOR} ya lo tiene todo."
        return line + f" {TAX_COLLECTOR} ya se lo ha cobrado."
    if filed.result < 0:
        line = f"🎉 **Te sale a devolver {_money(-filed.result)}.**"
        if filed.compensated:
            line += f" {_money(filed.compensated)} pagan lo que debías a Hacienda."
        if filed.refund:
            if seal == FILED:
                line += f" Ya la presentaste: {_money(filed.refund)} cobrados."
            elif seal == EXPIRED:
                line += (
                    f" No la presentaste a tiempo: {_money(filed.refund)} se los quedó "
                    f"{TAX_COLLECTOR}."
                )
            else:
                line += f" Quedan {_money(filed.refund)}: preséntala para cobrarlos."
        return line
    return "😐 **Sale a cero.** Ni tú le debes nada ni él a ti."


def summary_text(statement: IrpfStatement, seal: str = "") -> str:
    """El resumen: las cuentas en pocas líneas (sin carta dibujada)."""
    filed = statement.filed
    lines = [
        f"📑 **Declaración de la semana {week_label(statement.week)}**",
        f"Base liquidable: **{_money(filed.base)}**",
        f"Cuota íntegra (Estado + Canarias): {_money(filed.quota)}",
    ]
    if filed.deductions:
        lines.append(f"Deducciones: {_minus(filed.deductions)}")
    lines.append(f"Retenido durante la semana: {_minus(filed.withheld)}")
    lines.append(result_line(statement, seal))
    lines.append(rate_note(statement))
    return "\n".join(lines)


def rate_note(statement: IrpfStatement) -> str:
    """Línea pequeña del tipo medio de la semana."""
    return (
        f"-# Tu tipo medio fue el {format_rate(statement.filed.rate)}: es el mínimo con el "
        "que te retienen esta semana."
    )


def _work_lines(statement: IrpfStatement) -> list[str]:
    income = statement.income
    detail = statement.detail
    if detail is None or not income.work:
        return []
    tax = detail.tax
    lines = [
        "**🪏 Trabajo** (art. 17 LIRPF)",
        f"Nóminas, pensiones y lo de fuera: {_money(income.work)}",
    ]
    if income.social:
        lines.append(f"− Seguridad Social (art. 19.2.a): {_minus(income.social)}")
    if tax.expenses:
        lines.append(f"− Otros gastos (art. 19.2.f): {_minus(tax.expenses)}")
    if tax.reduction:
        lines.append(f"− Reducción por rendimientos del trabajo (art. 20): {_minus(tax.reduction)}")
    elif tax.rest + max(0, tax.properties) + tax.gains > 0:
        lines.append(
            f"-# Sin reducción del art. 20: o ganas demasiado o tus otras rentas pasan de "
            f"{_law(WORK_REDUCTION_OTHER_INCOME_EUR)} en la semana."
        )
    lines.append(f"= Rendimiento neto reducido: **{_money(tax.work)}**")
    return lines


def _property_lines(statement: IrpfStatement) -> list[str]:
    detail = statement.detail
    if detail is None or not statement.income.properties:
        return []
    tax = detail.tax
    nets = dict(tax.nets)
    settled = {ref: (used, left) for ref, used, left in tax.settlements}
    lines = ["**🏠 Alquileres** (arts. 22 y 23 LIRPF)"]
    items = sorted(statement.income.properties, key=lambda p: -p.income)
    for item in items[:PROPERTIES_SHOWN]:
        used, left = settled.get(item.ref, (0, 0))
        text = (
            f"`{item.ref}`: renta {_money(item.income)}, intereses y reparaciones "
            f"{_minus(item.finance - left + used)}, resto de gastos {_minus(item.costs)}"
        )
        if item.reduction and nets.get(item.ref, 0) > 0:
            text += f", reducción del {round(item.reduction * 100)} %"
        lines.append(f"- {text} → **{_money(nets.get(item.ref, 0))}**")
        if left:
            lines.append(f"-# {_money(left)} de intereses y reparaciones pasan a otras semanas.")
        if used:
            lines.append(f"-# Incluye {_money(used)} arrastrados de semanas anteriores.")
    if len(items) > PROPERTIES_SHOWN:
        rest = sum(nets.get(p.ref, 0) for p in items[PROPERTIES_SHOWN:])
        lines.append(f"- Y {len(items) - PROPERTIES_SHOWN} más: {_money(rest)}")
    lines.append(
        "-# Intereses y reparaciones no pasan de lo que renta cada piso; el resto de gastos "
        "sí puede dejarlo en negativo, y ese negativo rebaja el resto de tu base (art. 48)."
    )
    lines.append(f"= Total alquileres: **{_money(tax.properties)}**")
    return lines


def _other_lines(statement: IrpfStatement) -> list[str]:
    income = statement.income
    lines = []
    if income.business:
        lines.append(
            f"**🧾 Actividad** (art. 27): {_money(income.business)} (ya sin la cuota de autónomo)"
        )
    if income.image:
        lines.append(f"**📸 Derechos de imagen** (art. 25.4.d): {_money(income.image)}")
    if income.imputed:
        lines.append(
            f"**🏚️ Pisos vacíos** (art. 85, 1,1 % del catastral al año): {_money(income.imputed)}"
        )
    if income.other:
        lines.append(f"**📦 Rentas de antes de la 8.0.0** (ya netas): {_money(income.other)}")
    if income.prizes:
        lines.append(f"**🎁 Premios, niveles y logros** (art. 33.1): {_money(income.prizes)}")
    if income.gambling:
        if income.gambling > 0:
            lines.append(
                f"**🎰 Casino**, neto de la semana (art. 33.5.d): {_money(income.gambling)}"
            )
        else:
            lines.append(
                f"**🎰 Casino**: perdiste {_money(-income.gambling)} en la semana. Las pérdidas "
                "del juego solo compensan ganancias del juego (art. 33.5.d): cuenta 0."
            )
    if income.beckham:
        lines.append(f"**⚽ Ley Beckham** (art. 93, aparte de la escala): {_money(income.beckham)}")
    return lines


def _base_lines(statement: IrpfStatement) -> list[str]:
    detail = statement.detail
    if detail is None:
        return []
    tax = detail.tax
    lines = ["**📐 Base y cuota**"]
    if tax.negative_used:
        lines.append(
            f"Bases negativas de semanas anteriores (art. 50): {_minus(tax.negative_used)}"
        )
    lines.append(f"Base liquidable general: **{_money(tax.base)}**")
    if tax.negative:
        lines.append(
            f"-# Te sale negativa: {_money(tax.negative)} pasan a las 4 semanas siguientes."
        )
    lines.append(f"Escala estatal (art. 63): {_money(tax.state)}")
    lines.append(f"Escala de Canarias (Ley 9/2025): {_money(tax.regional)}")
    if tax.beckham:
        lines.append(f"Ley Beckham (24 % hasta {_law(BECKHAM_LIMIT_EUR)}): {_money(tax.beckham)}")
    lines.append(f"= Cuota íntegra: **{_money(tax.quota)}**")
    lines.append("-# El mínimo personal ya va restado en cada escala (art. 56).")
    return lines


def _deduction_lines(statement: IrpfStatement) -> list[str]:
    detail = statement.detail
    if detail is None or not detail.deductions:
        return []
    lines = ["**🧮 Deducciones**"]
    if detail.donations:
        lines.append(f"Donativos (art. 19 de la Ley 49/2002): {_minus(detail.donations)}")
    if detail.home:
        lines.append(f"Vivienda habitual (Canarias, art. 14 del DL 1/2009): {_minus(detail.home)}")
    if detail.rental:
        lines.append(f"Alquiler (Canarias, arts. 15 y 15 ter): {_minus(detail.rental)}")
    if detail.foreign:
        lines.append(f"Doble imposición internacional (art. 80): {_minus(detail.foreign)}")
    return lines


def _withheld_lines(statement: IrpfStatement) -> list[str]:
    if not statement.withheld_by:
        return []
    lines = ["**🐶 Lo que te retuvieron**"]
    for source, amount in sorted(statement.withheld_by.items(), key=lambda item: -item[1]):
        lines.append(f"{SOURCE_LABELS.get(source, source)}: {_minus(amount)}")
    return lines


def detail_text(statement: IrpfStatement, *, rate: float | None = None, seal: str = "") -> str:
    """El detalle: el porqué de cada cifra, paso a paso y con sus artículos.

    Args:
        rate: Tipo con el que te retienen ahora (se añade al final si se da).
        seal: El de `seal_for`, para el resultado.
    """
    sections = [
        [f"🔍 **Declaración de la semana {week_label(statement.week)}, paso a paso**"],
        _work_lines(statement),
        _property_lines(statement),
        _other_lines(statement),
        _base_lines(statement),
        _deduction_lines(statement),
        _withheld_lines(statement),
        [result_line(statement, seal)],
    ]
    if rate is not None:
        sections.append([f"-# Esta semana te retienen al {format_rate(rate)}."])
    return "\n\n".join("\n".join(lines) for lines in sections if lines)


# -- Por conceptos (pestaña Desglose y la tabla de la carta) ------------------------------


@dataclass(frozen=True, slots=True)
class Concept:
    """Una fuente de renta de la declaración (`concepts`), en Y$.

    Attributes:
        key: Clave estable (`trabajo`, `alquileres`…): su color en la carta.
        emoji: Su icono en el texto.
        name: Su nombre.
        earned: Lo que entró: los íntegros (del casino, el neto de la semana).
        base: Lo que aporta a la base liquidable (negativo si la rebaja).
        quota: La parte de la cuota que le toca (negativa si la rebaja).
        withheld: Lo que se le retuvo durante la semana.
    """

    key: str
    emoji: str
    name: str
    earned: int
    base: int
    quota: int
    withheld: int

    @property
    def gap(self) -> int:
        """Lo que faltó por retener (positivo) o lo que sobró (negativo)."""
        return self.quota - self.withheld


#: `(clave, emoji, nombre, fuentes de lo retenido)`, en el orden del desglose.
CONCEPTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("trabajo", "🪏", "Trabajo y pensiones", (INCOME_WORK, INCOME_PENSION, INCOME_FOREIGN)),
    ("alquileres", "🏠", "Alquileres", (INCOME_PROPERTY,)),
    ("actividad", "🧾", "Actividad", (INCOME_BUSINESS,)),
    ("premios", "🎁", "Premios y logros", (INCOME_PRIZE,)),
    ("casino", "🎰", "Casino", ("juego",)),
    ("imagen", "📸", "Derechos de imagen", (INCOME_IMAGE,)),
    ("vacios", "🏚️", "Pisos vacíos", (INCOME_IMPUTED,)),
    ("antes", "📦", "Rentas de antes de la 8.0.0", ("antes",)),
    ("beckham", "⚽", "Ley Beckham", (INCOME_BECKHAM,)),
)


def _shares(total: int, weights: list[int]) -> list[int]:
    """Reparte `total` en proporción a `weights` (pueden ser negativos), sumando `total`."""
    whole = sum(weights)
    if total <= 0 or whole <= 0:
        return [0] * len(weights)
    shares = [round(total * weight / whole) for weight in weights]
    # Lo que se pierde al redondear se lo lleva la fuente que más aporta.
    top = max(range(len(weights)), key=lambda i: weights[i])
    shares[top] += total - sum(shares)
    return shares


def concepts(statement: IrpfStatement) -> tuple[Concept, ...]:
    """La declaración por fuentes de renta, de modo que sus cuentas den el resultado.

    La cuota de la escala se reparte en proporción a lo que cada fuente aporta a la base,
    es decir, al tipo medio: la escala es progresiva y no hay un reparto exacto, pero así
    las partes suman la cuota al Y$ y `Σ cuota − deducciones − Σ retenido` es el
    resultado. Una fuente que rebaja la base (un piso en negativo, las bases negativas de
    antes) se lleva una parte negativa: lo que te ahorra. La Ley Beckham va fuera de la
    escala, con su propia cuota. Lo retenido que no es de ninguna fuente conocida sale
    aparte, para que la cuenta cuadre.

    Returns:
        Las fuentes con algo que contar; vacío si la declaración no guardó sus entradas.
    """
    detail = statement.detail
    if detail is None:
        return ()
    income, tax = statement.income, detail.tax
    amounts = {
        "trabajo": (income.work, tax.work),
        "alquileres": (sum(p.income for p in income.properties), tax.properties),
        "actividad": (income.business, income.business),
        "premios": (income.prizes, income.prizes),
        "casino": (income.gambling, max(0, income.gambling)),
        "imagen": (income.image, income.image),
        "vacios": (income.imputed, income.imputed),
        "antes": (income.other, income.other),
        "beckham": (income.beckham, 0),
    }
    rows: list[tuple[str, str, str, int, int, int]] = []
    known: set[str] = set()
    for key, emoji, name, sources in CONCEPTS:
        earned, base = amounts[key]
        withheld = sum(statement.withheld_by.get(source, 0) for source in sources)
        known.update(sources)
        if earned or base or withheld:
            rows.append((key, emoji, name, earned, base, withheld))
    if tax.negative_used:
        rows.append(("arrastre", "↩️", "Bases negativas de antes", 0, -tax.negative_used, 0))
    if other := sum(v for k, v in statement.withheld_by.items() if k not in known):
        rows.append(("otros", "📎", "Otras retenciones", 0, 0, other))
    shares = _shares(tax.quota - tax.beckham, [base for *_, base, _w in rows])
    return tuple(
        Concept(key, emoji, name, earned, base, tax.beckham if key == "beckham" else share, held)
        for (key, emoji, name, earned, base, held), share in zip(rows, shares, strict=True)
    )


def _gap_text(gap: int) -> str:
    """Lo que faltó (positivo) o sobró (negativo) por retener, en palabras."""
    if gap > 0:
        return f"faltaron {_money(gap)}"
    return f"sobraron {_money(-gap)}" if gap < 0 else "cuadra"


def _concept_lines(concept: Concept) -> list[str]:
    head = f"{concept.emoji} **{concept.name}**"
    if concept.earned < 0:
        head += f" · perdiste {_money(-concept.earned)}, que no rebajan nada más"
    elif concept.earned:
        head += f" · entraron {_money(concept.earned)}"
    bits = []
    if concept.quota > 0:
        bits.append(f"le tocan {_money(concept.quota)} de cuota")
    elif concept.quota < 0:
        bits.append(f"te ahorra {_money(-concept.quota)} de cuota")
    if concept.withheld:
        bits.append(f"te retuvieron {_money(concept.withheld)}")
    if not bits:
        return [head]
    text = " y ".join(bits)
    # Sin retenciones, lo que rebaja la cuota no «sobra»: solo ahorra.
    text += f": {_gap_text(concept.gap)}." if concept.withheld or concept.quota > 0 else "."
    return [head, f"-# {text[0].upper()}{text[1:]}"]


def concepts_text(statement: IrpfStatement, seal: str = "") -> str:
    """La pestaña Desglose: cada fuente con su parte de la cuota, y la cuenta que da el resultado.

    Args:
        seal: El de `seal_for`, para el resultado.
    """
    rows = concepts(statement)
    if not rows:
        return (
            "🧩 Esta declaración es de antes de que se guardara de dónde sale cada cifra: "
            "solo tiene el resumen.\n\n" + result_line(statement, seal)
        )
    lines = [f"🧩 **Por conceptos · semana {week_label(statement.week)}**"]
    for concept in rows:
        lines += _concept_lines(concept)
    detail = statement.detail
    if detail is not None and detail.deductions:
        lines.append(f"🧮 **Deducciones** · te rebajan {_money(detail.deductions)} de la cuota")
    lines.append(
        "-# La cuota de la escala se reparte entre las fuentes según lo que cada una suma a la "
        "base: la escala es progresiva, así que es una cuenta orientativa que cuadra al Y$."
    )
    lines.append(result_line(statement, seal))
    return "\n".join(lines)


# -- El consejo de los donativos ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DonationAdvice:
    """Lo que habría rendido donar en la semana declarada (`donation_advice`), en Y$.

    Attributes:
        donated: Lo donado esa semana.
        deducted: Lo que se dedujo por ello.
        more: Lo que habría convenido donar de más: hasta agotar el tramo del 80 % sin
            pasar del 10 % de la base ni de la cuota que quedaba (0 si nada).
        saved: Cuánto habría bajado el resultado donando `more` más, rehecho con
            `taxes.week_return` (no estimado).
        ceiling: Lo máximo donado que desgrava en la semana (el 10 % de la base).
        quota_left: Cuota que aún podían rebajar los donativos.
    """

    donated: int
    deducted: int
    more: int
    saved: int
    ceiling: int
    quota_left: int


def donation_advice(statement: IrpfStatement) -> DonationAdvice | None:
    """Cuánto le habría convenido donar en la semana declarada, o `None` sin entradas.

    Los donativos rebajan la cuota (art. 19 de la Ley 49/2002): el 80 % de los primeros
    `DONATION_FULL_LIMIT` y el 40 % del resto, hasta el 10 % de la base y nunca más de la
    cuota que quede tras las demás deducciones (`taxes.donation_deduction`). El consejo es
    lo que quedaba hasta agotar el tramo del 80 %, que es donde donar sale más barato.
    """
    detail = statement.detail
    if detail is None:
        return None
    arguments = statement.arguments
    donated = arguments.get("donated", 0)
    tax = detail.tax
    quota_left = tax.quota - detail.foreign - detail.home - detail.rental - detail.donations
    ceiling = int(tax.base * DONATION_BASE_LIMIT)
    more = max(0, min(DONATION_FULL_LIMIT, ceiling) - donated)
    more = min(more, int(quota_left / DONATION_FULL_RATE))
    saved = 0
    if more > 0:
        again = week_return(statement.income, **{**arguments, "donated": donated + more})
        saved = detail.result - again.result
    return DonationAdvice(
        donated=donated,
        deducted=detail.donations,
        more=more,
        saved=saved,
        ceiling=ceiling,
        quota_left=quota_left,
    )


def advice_text(statement: IrpfStatement, advice: DonationAdvice) -> str:
    """La pestaña Consejo: si donar habría salido barato esa semana y cuánto."""
    week = week_label(statement.week)
    lines = [f"🤝 **El consejo de {TAX_COLLECTOR}**"]
    gave = f"donaste {_money(advice.donated)}" if advice.donated else "no donaste nada"
    if advice.more:
        lines.append(
            f"La semana {week} {gave}. Si hubieras donado **{_money(advice.more)}** más, te "
            f"habría devuelto **{_money(advice.saved)}**: te habría costado de verdad "
            f"{_money(advice.more - advice.saved)}."
        )
    elif advice.deducted:
        lines.append(
            f"La semana {week} {gave} y te dedujeron {_money(advice.deducted)}: ya "
            "aprovechaste el tramo que más devuelve."
        )
        if advice.quota_left > 0 and advice.ceiling > advice.donated:
            lines.append(
                f"-# Lo que dones de más vuelve al 40 %, hasta {_money(advice.ceiling)} donados "
                "en la semana."
            )
    else:
        lines.append(
            f"La semana {week} donar no te habría devuelto nada: los donativos rebajan el IRPF "
            "que pagas y no había cuota que rebajar."
        )
    lines.append(f"-# Desgrava el {deduction_rule_text()}.")
    lines.append(
        "📅 Lo que dones hasta el domingo cuenta en la Renta del lunes: si esta semana ganas "
        "parecido, te sale igual de barato."
    )
    lines.append(
        "-# Donar no da dinero: como mucho recuperas el 80 %. El resto se lo queda la ONG."
    )
    return "\n".join(lines)


# -- La carta dibujada --------------------------------------------------------------------

#: Color de cada concepto en la carta.
CONCEPT_COLORS: dict[str, str] = {
    "trabajo": "#2f6fd6",
    "alquileres": "#d9822b",
    "actividad": "#8e5bd6",
    "premios": "#c99a06",
    "casino": "#1f9d8a",
    "imagen": "#d6457f",
    "vacios": "#7d8a96",
    "antes": "#9aa5ae",
    "beckham": "#2e9e4f",
    "arrastre": "#4b5b6b",
    "otros": "#7d8a96",
}
#: Filas de la tabla de la carta; el resto se junta en «Otros».
CARD_ROWS = 4


def _signed(amount: int) -> str:
    return f"−{format_amount(-amount)}" if amount < 0 else format_amount(amount)


def _card_row(name: str, key: str, earned: int, quota: int, withheld: int) -> dict[str, str]:
    gap = quota - withheld
    return {
        "name": name,
        "color": CONCEPT_COLORS.get(key, CONCEPT_COLORS["otros"]),
        "earned": _signed(earned) if earned else "—",
        "quota": _signed(quota),
        "withheld": format_amount(withheld),
        "gap": _gap_text(gap),
        "tone": "pay" if gap > 0 else "refund" if gap < 0 else "even",
    }


def card_state(statement: IrpfStatement, name: str, seal: str = "") -> dict[str, object]:
    """La carta de la declaración para la escena (`renta_scene`), todo ya en texto.

    Dos barras: de lo que ganaste a tu base y cómo se paga la cuota (lo retenido y las
    deducciones frente a la cuota: lo que falta sale a pagar y lo que sobra, a devolver);
    el sello con el resultado, el tipo medio y la tabla por conceptos (`concepts`).

    Args:
        name: Nombre de su dueño, tal cual (la escena quita lo que no sabe pintar).
        seal: El de `seal_for`: lo que ya se hizo con ella, en un sello encima.
    """
    filed = statement.filed
    rows = concepts(statement)
    general = [c for c in rows if c.key != "beckham"]
    earned = sum(max(0, c.earned) for c in general) if rows else filed.base
    everything = earned + sum(max(0, c.earned) for c in rows if c.key == "beckham")
    deductions = statement.detail.deductions if statement.detail else filed.deductions
    due = max(0, filed.quota - deductions)
    result = filed.result
    tone = "pay" if result > 0 else "refund" if result < 0 else "zero"
    table = [_card_row(c.name, c.key, c.earned, c.quota, c.withheld) for c in rows]
    if len(table) > CARD_ROWS:
        rest = rows[CARD_ROWS - 1 :]
        table = table[: CARD_ROWS - 1] + [
            _card_row(
                f"Otros ({len(rest)})",
                "otros",
                sum(c.earned for c in rest),
                sum(c.quota for c in rest),
                sum(c.withheld for c in rest),
            )
        ]
    return {
        "week": f"Semana {week_label(statement.week)}",
        "name": name,
        "earned": earned,
        "base": filed.base,
        "quota": filed.quota,
        "deductions": deductions,
        "withheld": filed.withheld,
        "labels": {
            "earned": format_amount(earned),
            "base": format_amount(filed.base),
            "cut": format_amount(max(0, earned - filed.base)),
            "quota": format_amount(filed.quota),
            "deductions": format_amount(deductions),
            "withheld": format_amount(filed.withheld),
        },
        "tone": tone,
        "verdict": {"pay": "A PAGAR", "refund": "A DEVOLVER", "zero": "A CERO"}[tone],
        "amount": format_amount(abs(result)),
        "rate": format_rate(filed.rate),
        "per100": round(100 * due / everything) if everything > 0 else -1,
        "seal": seal,
        "rows": table,
    }
