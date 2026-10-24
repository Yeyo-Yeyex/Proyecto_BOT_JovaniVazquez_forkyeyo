"""Textos de la declaración de la Renta (`renta`): el sobre, el resumen y el detalle.

Lógica pura, sin Discord: el cog los mete en un embed. Los números salen de la
declaración rehecha con sus entradas (`EconomyService.irpf_statement`), así que cuadran
al Y$ con lo que se cobró o se devolvió.

Hay dos modos, para no saturar a nadie:

- **Resumen:** las cuentas en cinco líneas (base, cuota, deducciones, retenido y
  resultado).
- **Detalle:** el porqué de cada línea, paso a paso y con su artículo: el trabajo (gastos
  y reducción), cada inmueble (tope de intereses, arrastre y reducción), el resto de
  rentas, la integración en la base, las dos escalas, las deducciones y lo retenido por
  fuente.
"""

from __future__ import annotations

from bot.repositories.economy import PAYMENT_PENDING, PAYMENT_SPLIT
from bot.services.economy import IrpfStatement, format_amount, week_label
from bot.services.taxes import (
    BECKHAM_LIMIT_EUR,
    INCOME_BECKHAM,
    INCOME_BUSINESS,
    INCOME_FOREIGN,
    INCOME_IMAGE,
    INCOME_PENSION,
    INCOME_PRIZE,
    INCOME_PROPERTY,
    INCOME_WORK,
    TAX_COLLECTOR,
    WORK_REDUCTION_OTHER_INCOME_EUR,
    format_rate,
    second_installment,
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


def result_line(statement: IrpfStatement) -> str:
    """El resultado en una línea, con lo que se hizo con él."""
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
            line += f" Quedan {_money(filed.refund)}: preséntala para cobrarlos."
        return line
    return "😐 **Sale a cero.** Ni tú le debes nada ni él a ti."


def summary_text(statement: IrpfStatement) -> str:
    """El resumen: las cuentas en pocas líneas."""
    filed = statement.filed
    lines = [
        f"📑 **Declaración de la semana {week_label(statement.week)}**",
        f"Base liquidable: **{_money(filed.base)}**",
        f"Cuota íntegra (Estado + Canarias): {_money(filed.quota)}",
    ]
    if filed.deductions:
        lines.append(f"Deducciones: {_minus(filed.deductions)}")
    lines.append(f"Retenido durante la semana: {_minus(filed.withheld)}")
    lines.append(result_line(statement))
    lines.append(
        f"-# Tu tipo medio fue el {format_rate(filed.rate)}: es el mínimo con el que te "
        "retienen esta semana."
    )
    return "\n".join(lines)


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


def detail_text(statement: IrpfStatement, *, rate: float | None = None) -> str:
    """El detalle: el porqué de cada cifra, paso a paso y con sus artículos.

    Args:
        rate: Tipo con el que te retienen ahora (se añade al final si se da).
    """
    sections = [
        [f"🔍 **Declaración de la semana {week_label(statement.week)}, paso a paso**"],
        _work_lines(statement),
        _property_lines(statement),
        _other_lines(statement),
        _base_lines(statement),
        _deduction_lines(statement),
        _withheld_lines(statement),
        [result_line(statement)],
    ]
    if rate is not None:
        sections.append([f"-# Esta semana te retienen al {format_rate(rate)}."])
    return "\n\n".join("\n".join(lines) for lines in sections if lines)
