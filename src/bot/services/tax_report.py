"""La factura fiscal de cada miembro: todo lo que ha pagado, directo o indirecto.

`EconomyRepository.tax_breakdown` devuelve, por miembro, lo pagado en cada tipo
de impuesto. Este módulo dice qué es cada tipo, cómo se agrupa y cómo se
pinta en `hacienda`. No toca Discord ni la base de datos.

Grupos:

- **Directos:** los que paga quien gana o tiene el dinero (IRPF de cada renta,
  cotización del trabajador y cuota de autónomos, Patrimonio y gravamen de
  loterías).
- **Indirectos:** los que paga sin verlos (IGIC dentro del precio de la
  `tienda`, la Seguridad Social que la empresa ingresa por su nómina, que es coste
  de su puesto aunque no salga de su bolsillo, igual que en la vida real, y el
  IAJ que sale del bote de cada `porra`).
- **Otros pagos al Estado:** multas de la Inspección. No son impuestos, pero
  salen del bolsillo y van al Estado.
- **Devuelto:** lo que la renta semanal le devolvió (resta).
- **Fuera de España:** lo pagado en Hong Kong. No va a Perro Sanxe y no suma
  al total, pero se enseña aparte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import discord

from bot.services.economy import format_amount
from bot.services.taxes import SOLIDARITY_WEALTH_THRESHOLD, TAX_COLLECTOR


class Group(Enum):
    """Cómo se presenta cada tipo de pago."""

    DIRECT = "directos"
    INDIRECT = "indirectos"
    OTHER = "otros"
    REFUND = "devuelto"
    FOREIGN = "extranjero"


@dataclass(frozen=True, slots=True)
class TaxKind:
    """Un tipo de pago de la factura."""

    key: str
    emoji: str
    label: str
    short: str
    group: Group
    law: str


#: Tipos de pago, en el orden en que salen. Las claves son las de la consulta.
TAX_KINDS: tuple[TaxKind, ...] = (
    TaxKind("irpf_trabajo", "🪏", "IRPF de las nóminas", "IRPF nóm.", Group.DIRECT,
            "art. 17 LIRPF"),
    TaxKind("irpf_otros", "🎁", "IRPF de premios y otros ingresos", "IRPF otros", Group.DIRECT,
            "art. 99 LIRPF"),
    TaxKind("irpf_renta", "📑", "IRPF de la declaración del lunes", "IRPF renta",
            Group.DIRECT, "arts. 96 y 97 LIRPF"),
    TaxKind("irpf_casino", "🎰", "IRPF del casino", "IRPF casino", Group.DIRECT,
            "art. 33.5.d LIRPF"),
    TaxKind("irpf_ahorro", "🏦", "IRPF del ahorro (intereses y plusvalías)", "IRPF ahorro",
            Group.DIRECT, "arts. 25.2, 33 y 66 LIRPF"),
    TaxKind("irpf_alquiler", "🔑", "IRPF de los alquileres", "IRPF alq.", Group.DIRECT,
            "arts. 22 y 23 LIRPF"),
    TaxKind("ibi", "🏘️", "IBI de los pisos", "IBI", Group.DIRECT, "art. 60 RDL 2/2004"),
    TaxKind("ss_trabajador", "🧑‍🏭", "Seguridad Social del trabajador y autónomos", "SS",
            Group.DIRECT, "LGSS"),
    TaxKind("patrimonio", "🏰", "Impuesto sobre el Patrimonio", "Patrim.", Group.DIRECT,
            "Ley 19/1991"),
    TaxKind("deudas", "⚖️", "Deudas con Hacienda cobradas después", "Deudas", Group.DIRECT,
            "art. 169 LGT"),
    TaxKind("grandes_fortunas", "💎", "Impuesto de Solidaridad de las Grandes Fortunas",
            "Grandes F.", Group.DIRECT, "art. 3 Ley 38/2022"),
    TaxKind("loteria", "🎟️", "Gravamen especial de loterías", "Lotería", Group.DIRECT,
            "DA 33ª LIRPF"),
    TaxKind("igic", "🛒", "IGIC de las compras (y primas de seguros)", "IGIC", Group.INDIRECT,
            "Ley 4/2012 y art. 12 Ley 13/1996"),
    TaxKind("ss_empresa", "🏢", "Seguridad Social que paga la empresa", "SS empresa",
            Group.INDIRECT, "LGSS"),
    TaxKind("itp", "🧺", "Transmisiones Patrimoniales (Rastro)", "ITP", Group.INDIRECT,
            "art. 31.1.d DLeg 1/2009 de Canarias"),
    TaxKind("itp_pisos", "🏠", "Transmisiones Patrimoniales (pisos)", "ITP pisos",
            Group.INDIRECT, "art. 31 DLeg 1/2009 de Canarias"),
    TaxKind("iaj", "🎫", "Impuesto sobre Actividades de Juego (porras)", "IAJ",
            Group.INDIRECT, "art. 48 Ley 13/2011"),
    TaxKind("tasas", "🏖️", "Tasas de licencias vacacionales", "Tasas", Group.OTHER,
            "ley canaria de vivienda vacacional"),
    TaxKind("multas", "🚨", "Multas de la Inspección", "Multas", Group.OTHER, "LGT"),
    TaxKind("devuelto", "📬", "Devuelto en la renta", "Devuelto", Group.REFUND,
            "art. 103 LIRPF"),
    TaxKind("extranjero", "🇭🇰", "Impuestos y MPF en Hong Kong", "Hong Kong", Group.FOREIGN,
            "IRD de Hong Kong"),
)  # fmt: skip
KIND_BY_KEY: dict[str, TaxKind] = {kind.key: kind for kind in TAX_KINDS}


@dataclass(frozen=True, slots=True)
class TaxBill:
    """Lo pagado por un miembro (o por todo el servidor), por tipo.

    Attributes:
        user_id: Miembro, o 0 para el total del servidor.
        amounts: Importe por clave de `TAX_KINDS`. `devuelto` va en positivo.
    """

    user_id: int
    amounts: dict[str, int] = field(default_factory=dict)

    def group_total(self, group: Group) -> int:
        """Suma de los tipos de un grupo."""
        return sum(
            amount
            for key, amount in self.amounts.items()
            if key in KIND_BY_KEY and KIND_BY_KEY[key].group is group
        )

    @property
    def direct(self) -> int:
        """Impuestos directos."""
        return self.group_total(Group.DIRECT)

    @property
    def indirect(self) -> int:
        """Impuestos indirectos."""
        return self.group_total(Group.INDIRECT)

    @property
    def refunded(self) -> int:
        """Lo que le devolvieron (positivo)."""
        return self.group_total(Group.REFUND)

    @property
    def total(self) -> int:
        """Lo que se ha quedado el Estado: directos + indirectos + multas − devuelto."""
        return self.direct + self.indirect + self.group_total(Group.OTHER) - self.refunded

    @property
    def foreign(self) -> int:
        """Lo pagado fuera (no cuenta en `total`)."""
        return self.group_total(Group.FOREIGN)


def bills(breakdown: dict[int, dict[str, int]]) -> list[TaxBill]:
    """Facturas de cada miembro, de la que más paga a la que menos."""
    out = [TaxBill(user_id, dict(amounts)) for user_id, amounts in breakdown.items()]
    return sorted(out, key=lambda bill: (-bill.total, bill.user_id))


def server_bill(all_bills: list[TaxBill]) -> TaxBill:
    """Todas las facturas sumadas en una."""
    amounts: dict[str, int] = {}
    for bill in all_bills:
        for key, amount in bill.amounts.items():
            amounts[key] = amounts.get(key, 0) + amount
    return TaxBill(0, amounts)


def _sign(kind: TaxKind) -> str:
    """Las devoluciones salen con signo menos: restan de lo pagado."""
    return "−" if kind.group is Group.REFUND else ""


#: Impuestos que salen en la línea corta de cada miembro (los que más ha pagado).
COMPACT_KINDS = 5


def compact_line(bill: TaxBill) -> str:
    """`IRPF nóm. 1.200 · SS empresa 3.000 · IGIC 70`: los que más ha pagado.

    Solo lo que no es cero y como mucho `COMPACT_KINDS`, de más a menos, para que
    la lista de `hacienda` quepa en un embed aunque todos paguen de todo.
    """
    kinds = sorted(
        (k for k in TAX_KINDS if bill.amounts.get(k.key) and k.group is not Group.FOREIGN),
        key=lambda k: -abs(bill.amounts[k.key]),
    )
    parts = [
        f"{kind.short} {_sign(kind)}{format_amount(bill.amounts[kind.key]).removesuffix(' Y$')}"
        for kind in kinds[:COMPACT_KINDS]
    ]
    if len(kinds) > COMPACT_KINDS:
        parts.append(f"y {len(kinds) - COMPACT_KINDS} más")
    return " · ".join(parts) if parts else "Nada todavía"


def detail_lines(bill: TaxBill, group: Group) -> list[str]:
    """Una línea por tipo del grupo con algo pagado."""
    return [
        f"{kind.emoji} {kind.label}: **{_sign(kind)}{format_amount(bill.amounts[kind.key])}**"
        for kind in TAX_KINDS
        if kind.group is group and bill.amounts.get(kind.key)
    ]


def verdict(bill: TaxBill, share: float) -> str:
    """Una frase con guasa según lo que paga alguien.

    Args:
        share: Parte de lo recaudado en el servidor que ha pagado (0–1).
    """
    if bill.total <= 0:
        return "Ni un Y$ para Sanxe. O eres muy listo o no haces nada, papi."
    if share >= 0.5:
        return "Tú solo sostienes el Estado. El Falcon va con tu gasolina."
    if bill.indirect > bill.direct:
        return "Pagas más sin verlo que viéndolo. Así funciona el truco."
    if share >= 0.2:
        return "Contribuyente de los que Hacienda llama por su nombre."
    return "Pagas lo tuyo, como todo el mundo. Bueno, como casi todo el mundo."


# -- Embeds de `hacienda` ------------------------------------------------------------------

COLOR = discord.Color.from_rgb(170, 21, 27)
#: Miembros que salen con su desglose en la tarjeta pública (menos si no caben).
TOP_BILLS = 10
#: Límite de Discord para todo el texto de un embed.
EMBED_TOTAL_LIMIT = 6_000
#: Lo que se guarda para los nombres de los campos y el «y N más» de la lista.
FIELD_NAMES_RESERVE = 160
_MEDALS = ("🥇", "🥈", "🥉")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + " %"


def _chunks(lines: list[str], limit: int = 1_024) -> list[str]:
    """Junta líneas en bloques que quepan en un campo de embed."""
    out: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            out.append(current)
            current = line[:limit]
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def add_bill_fields(
    embed: discord.Embed, all_bills: list[TaxBill], server: TaxBill, names: dict[int, str]
) -> None:
    """Añade a la tarjeta de `hacienda` lo recaudado por impuesto y lo que paga cada uno."""
    by_kind = [
        f"{kind.emoji} {kind.label}: {_sign(kind)}{format_amount(server.amounts[kind.key])}"
        for kind in TAX_KINDS
        if server.amounts.get(kind.key)
    ]
    if by_kind:
        by_kind.append(
            f"**Neto para {TAX_COLLECTOR}: {format_amount(server.total)}** "
            f"(directos {format_amount(server.direct)} · indirectos "
            f"{format_amount(server.indirect)})"
        )
        for i, block in enumerate(_chunks(by_kind)):
            embed.add_field(name="Por impuesto" if i == 0 else "\u200b", value=block, inline=False)
    paying = [bill for bill in all_bills if bill.total > 0 or bill.amounts]
    if not paying:
        return
    lines = []
    for i, bill in enumerate(paying[:TOP_BILLS]):
        rank = _MEDALS[i] if i < len(_MEDALS) else f"`{i + 1}.`"
        lines.append(
            f"{rank} {names.get(bill.user_id, f'<@{bill.user_id}>')} · "
            f"**{format_amount(bill.total)}**\n-# {compact_line(bill)}"
        )
    # El embed entero no puede pasar de 6.000 caracteres: si no caben todos, se quitan
    # los últimos de la lista (los que menos pagan) y se dice cuántos faltan.
    budget = EMBED_TOTAL_LIMIT - len(embed) - FIELD_NAMES_RESERVE
    while len(lines) > 1 and len("\n".join(lines)) > budget:
        lines.pop()
    if len(paying) > len(lines):
        lines.append(
            f"-# Y {len(paying) - len(lines)} más. `hacienda @miembro` para ver a cualquiera."
        )
    for i, block in enumerate(_chunks(lines)):
        embed.add_field(
            name="Lo que paga cada uno (directo e indirecto)" if i == 0 else "\u200b",
            value=block,
            inline=False,
        )


#: Lo que pagan los pisos, en el orden del bloque 🏠: `(clave, emoji, nombre, norma)`.
HOUSING_TAXES: tuple[tuple[str, str, str, str], ...] = (
    ("itp", "🏠", "ITP al comprar", "art. 31 DLeg 1/2009 de Canarias"),
    ("ibi", "🏘️", "IBI", "art. 60 RDL 2/2004"),
    ("alquiler", "🔑", "IRPF de las rentas", "arts. 22 y 23 LIRPF"),
    ("ganancia", "📈", "IRPF de lo ganado al vender y de la SOCIMI", "arts. 33 y 66 LIRPF"),
    ("igic", "🏖️", "IGIC de las estancias vacacionales", "art. 51 Ley 4/2012"),
    ("igic_gastos", "🧾", "IGIC de la notaría y la agencia", "art. 51 Ley 4/2012"),
    ("licencia", "📜", "Tasas de licencias vacacionales", "ley canaria de vivienda vacacional"),
)


def solidarity_line(filed: int, people: int, paid: int) -> str:
    """La línea de las Grandes Fortunas de `hacienda`: se declaran aunque salgan a cero."""
    who = "fortuna" if people == 1 else "fortunas"
    return (
        f"{filed} declaraciones de {people} {who} de más de "
        f"{format_amount(SOLIDARITY_WEALTH_THRESHOLD)}: **{format_amount(paid)}**. Se "
        "descuenta el Patrimonio pagado y Canarias no lo bonifica, así que sale a cero "
        "· art. 3 Ley 38/2022"
    )


def housing_lines(amounts: dict[str, int]) -> list[str]:
    """Una línea por impuesto de los pisos que se haya pagado."""
    return [
        f"{emoji} {label}: {format_amount(amounts[key])} · {law}"
        for key, emoji, label, law in HOUSING_TAXES
        if amounts.get(key)
    ]


def add_housing_fields(
    embed: discord.Embed, housing: dict[int, dict[str, int]], names: dict[int, str]
) -> None:
    """Bloque 🏠 de `hacienda`: lo que pagan los pisos del servidor y cada casero.

    Args:
        housing: Lo de `EconomyService.housing_tax_breakdown`.
        names: Nombre de cada miembro.
    """
    if not housing:
        return
    totals: dict[str, int] = {}
    for amounts in housing.values():
        for key, value in amounts.items():
            totals[key] = totals.get(key, 0) + value
    total = sum(totals.values())
    embed.add_field(
        name=f"🏠 Lo que pagan los pisos · {format_amount(total)}",
        value="\n".join(housing_lines(totals)),
        inline=False,
    )
    ranked = sorted(housing.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    lines = []
    for i, (user_id, amounts) in enumerate(ranked[:TOP_BILLS]):
        rank = _MEDALS[i] if i < len(_MEDALS) else f"`{i + 1}.`"
        parts = " · ".join(
            f"{emoji} {format_amount(amounts[key])}"
            for key, emoji, _label, _law in HOUSING_TAXES
            if amounts.get(key)
        )
        lines.append(
            f"{rank} {names.get(user_id, f'<@{user_id}>')} · "
            f"**{format_amount(sum(amounts.values()))}**\n-# {parts}"
        )
    # Como en la lista de facturas: si no cabe en el embed, fuera los que menos pagan.
    budget = EMBED_TOTAL_LIMIT - len(embed) - FIELD_NAMES_RESERVE
    while len(lines) > 1 and len("\n".join(lines)) > budget:
        lines.pop()
    if len(ranked) > len(lines) and len(lines) < TOP_BILLS:
        lines.append(f"-# Y {len(ranked) - len(lines)} caseros más.")
    for i, block in enumerate(_chunks(lines)):
        embed.add_field(
            name="Los caseros que más pagan" if i == 0 else "​", value=block, inline=False
        )


def member_bill_embed(
    *,
    member_name: str,
    all_bills: list[TaxBill],
    year_bills: list[TaxBill],
    server: TaxBill,
    user_id: int,
    year: int,
) -> discord.Embed:
    """Factura fiscal completa de un miembro: todo lo que ha pagado y cómo."""
    bill = next((b for b in all_bills if b.user_id == user_id), TaxBill(user_id))
    this_year = next((b for b in year_bills if b.user_id == user_id), TaxBill(user_id))
    share = bill.total / server.total if server.total > 0 else 0.0
    rank = next((i for i, b in enumerate(all_bills, 1) if b.user_id == user_id), None)
    embed = discord.Embed(
        title=f"🧾 Factura fiscal de {member_name}",
        description=(
            f"Para {TAX_COLLECTOR}, desde siempre: **{format_amount(bill.total)}**\n"
            f"En {year}: {format_amount(this_year.total)}\n"
            f"Es el {_pct(share)} de todo lo recaudado"
            + (f" · puesto {rank} de {len(all_bills)}" if rank else "")
            + f"\n*{verdict(bill, share)}*"
        ),
        color=COLOR,
    )
    sections = (
        (Group.DIRECT, "Directos", bill.direct),
        (Group.INDIRECT, "Indirectos (sin verlos)", bill.indirect),
        (Group.OTHER, "Otros pagos al Estado", bill.group_total(Group.OTHER)),
        (Group.REFUND, "Te ha devuelto", bill.refunded),
        (Group.FOREIGN, "Fuera de España (no va a Sanxe)", bill.foreign),
    )
    for group, title, total in sections:
        lines = detail_lines(bill, group)
        if lines:
            embed.add_field(
                name=f"{title} · {format_amount(total)}", value="\n".join(lines), inline=False
            )
    if not bill.amounts:
        embed.add_field(
            name="Nada que declarar",
            value="Ni nóminas, ni premios, ni compras. Sanxe aún no sabe que existes.",
            inline=False,
        )
    embed.set_footer(
        text="La SS de la empresa es coste de tu puesto aunque no salga de tu bolsillo. "
        "El IGIC va dentro de cada precio."
    )
    return embed
