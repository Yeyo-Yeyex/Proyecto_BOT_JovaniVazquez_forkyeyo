"""Patrimonio de cada miembro: todo lo que tiene, sumado y valorado.

Lo usan `patrimonio [miembro]` (el de uno, con el detalle) y `fortunas` (la
lista de todos). No mueve dinero ni toca Discord: recibe lo que leen los
repositorios y devuelve números y textos.

Qué cuenta como activo y cómo se valora:

- **Efectivo:** el saldo del monedero.
- **Bienes de la `tienda`** en vigor: lo pagado sin IGIC, con las
  renovaciones. Lo que caduca (roles alquilados, potenciadores) vale la parte
  de vida que le queda; lo regalado o sacado de una caja vale el precio actual.
  Es el valor de adquisición, como el art. 18 de la Ley 19/1991 para los bienes
  muebles sin cotización (simplificado: no hay depreciación por uso).
- **Boletos de lotería** de sorteos que aún no se han celebrado, a su coste.
- **Derechos de cobro:** lo que la renta semanal le tiene que devolver y aún
  no ha presentado.

No hay deudas personales en el bot (las multas se cobran al momento), así que
el patrimonio neto es la suma de los activos. El Impuesto sobre el Patrimonio
que cobra `bot.cogs.patrimonio` sigue mirando solo el efectivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.services.economy import format_amount
from bot.services.shop import Kind


@dataclass(frozen=True, slots=True)
class Holding:
    """Un bien de la tienda con su valor."""

    name: str
    emoji: str
    kind: str
    value: int
    expires_at: float | None


@dataclass(frozen=True, slots=True)
class NetWorth:
    """Todo lo que tiene un miembro.

    Attributes:
        user_id: Miembro.
        cash: Saldo del monedero.
        goods: Bienes de la tienda en vigor, del más valioso al menos.
        lottery: Coste de sus boletos para sorteos pendientes.
        renta: Lo que la renta le debe y no ha cobrado.
    """

    user_id: int
    cash: int = 0
    goods: tuple[Holding, ...] = field(default_factory=tuple)
    lottery: int = 0
    renta: int = 0

    @property
    def goods_value(self) -> int:
        """Valor de todos los bienes."""
        return sum(item.value for item in self.goods)

    @property
    def total(self) -> int:
        """Patrimonio neto."""
        return self.cash + self.goods_value + self.lottery + self.renta

    @property
    def illiquid_share(self) -> float:
        """Parte del patrimonio que no es efectivo (0–1)."""
        return 1 - self.cash / self.total if self.total > 0 else 0.0


def build(
    balances: dict[int, int],
    holdings: list[tuple[int, str, str, str, int, float | None]],
    lottery: dict[int, int],
    renta: dict[int, int],
) -> list[NetWorth]:
    """Junta las piezas en un `NetWorth` por miembro, del más rico al menos.

    Args:
        balances: Saldo por miembro.
        holdings: Filas de `ShopRepository.holdings`.
        lottery: Coste de boletos pendientes por miembro.
        renta: Devolución pendiente por miembro.
    """
    goods: dict[int, list[Holding]] = {}
    for user_id, name, emoji, kind, value, expires in holdings:
        goods.setdefault(user_id, []).append(Holding(name, emoji, kind, value, expires))
    users = set(balances) | set(goods) | set(lottery) | set(renta)
    out = [
        NetWorth(
            user_id=user_id,
            cash=balances.get(user_id, 0),
            goods=tuple(sorted(goods.get(user_id, []), key=lambda h: (-h.value, h.name))),
            lottery=lottery.get(user_id, 0),
            renta=renta.get(user_id, 0),
        )
        for user_id in users
    ]
    return sorted(out, key=lambda w: (-w.total, w.user_id))


def gini(values: list[int]) -> float:
    """Coeficiente de Gini (0 = todos igual, 1 = uno lo tiene todo)."""
    data = sorted(max(0, v) for v in values)
    total = sum(data)
    n = len(data)
    if n < 2 or total == 0:
        return 0.0
    weighted = sum((i + 1) * v for i, v in enumerate(data))
    return (2 * weighted) / (n * total) - (n + 1) / n


def top_share(values: list[int], fraction: float) -> float:
    """Parte del total que tiene el `fraction` más rico (al menos una persona)."""
    data = sorted((max(0, v) for v in values), reverse=True)
    total = sum(data)
    if not data or total == 0:
        return 0.0
    count = max(1, round(len(data) * fraction))
    return sum(data[:count]) / total


KIND_LABELS = {
    Kind.ROLE.value: "Rol",
    Kind.BOOST.value: "Potenciador",
    Kind.TROPHY.value: "Objeto",
    Kind.PET.value: "Mascota",
}


def holding_line(item: Holding) -> str:
    """`🎩 Chistera · 1.200 Y$ (objeto)`."""
    kind = KIND_LABELS.get(item.kind, item.kind)
    when = " · caduca" if item.expires_at is not None else ""
    return f"{item.emoji} {item.name} · {format_amount(item.value)} ({kind.lower()}{when})"


def verdict(worth: NetWorth, rank: int, total_people: int) -> str:
    """Frase con guasa según lo que tiene alguien."""
    if worth.total <= 0:
        return "Patrimonio cero. Ni Sanxe se molesta en mirarte, papi."
    if rank == 1 and total_people > 1:
        return "El Amancio Ortega del servidor. Sanxe ya ha preguntado por ti."
    if worth.illiquid_share >= 0.5:
        return "Rico en cosas, pobre en efectivo. Muy español."
    if rank == total_people and total_people > 2:
        return "La base de la pirámide. Alguien tiene que sostenerla."
    return "Ni rico ni pobre: clase media según el CIS."
