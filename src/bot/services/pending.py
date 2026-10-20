"""Lo pendiente de un miembro, para `saldo`: lo que va a pagar, lo que puede cobrar y lo retenido.

Cada cog dueño de los datos (Renta, pisos, trabajo, loterías, Rastro) da sus avisos con
una función puente que devuelve `Pending`; `saldo` los junta, los ordena por fecha y los
pinta por secciones con `pending_fields`. Aquí solo vive el aviso y cómo se enseña: qué
está pendiente lo decide cada cog.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

#: Lo que cabe en el valor de un campo de un embed de Discord.
FIELD_LIMIT = 1_024


class Side(StrEnum):
    """Hacia dónde va el dinero de un aviso."""

    PAY = "pay"
    COLLECT = "collect"
    HELD = "held"


#: Título de cada sección, en el orden en que salen.
SECTION_TITLES: dict[Side, str] = {
    Side.PAY: "⏳ Por pagar",
    Side.COLLECT: "📥 Por cobrar",
    Side.HELD: "🔒 Retenido",
}


@dataclass(frozen=True, slots=True)
class Pending:
    """Una cosa pendiente.

    Attributes:
        side: Si se paga, se cobra o está retenida.
        kind: Clave estable de qué es (`renta_debt`, `mortgage`…): la usan los logros.
        emoji: El icono de la línea.
        text: La línea ya escrita, sin la fecha.
        amount: Los Y$ en juego (0 si no se sabe o no aplica).
        when: Cuándo vence o se cobra (epoch), o `None` si no tiene fecha. Se añade a la
            línea como fecha relativa de Discord y ordena la sección.
    """

    side: Side
    kind: str
    emoji: str
    text: str
    amount: int = 0
    when: float | None = None

    def line(self) -> str:
        """La línea tal y como sale en `saldo`."""
        when = f" · <t:{int(self.when)}:R>" if self.when is not None else ""
        return f"{self.emoji} {self.text}{when}"


def pending_fields(items: Iterable[Pending]) -> list[tuple[str, str]]:
    """`(título, texto)` de cada sección con algo, en orden y lo que vence antes, primero.

    Si una sección no cabe en un campo del embed, se corta con «y N más».
    """
    items = list(items)
    fields = []
    for side, title in SECTION_TITLES.items():
        lines = [
            item.line()
            for item in sorted(
                (i for i in items if i.side == side),
                key=lambda i: (i.when is None, i.when or 0.0),
            )
        ]
        if not lines:
            continue
        shown: list[str] = []
        used = 0
        for index, line in enumerate(lines):
            # Se deja sitio para la cola «Y N más» por si la siguiente ya no cabe.
            if used + len(line) + 1 > FIELD_LIMIT - 20:
                shown.append(f"-# Y {len(lines) - index} más.")
                break
            shown.append(line)
            used += len(line) + 1
        fields.append((title, "\n".join(shown)))
    return fields
