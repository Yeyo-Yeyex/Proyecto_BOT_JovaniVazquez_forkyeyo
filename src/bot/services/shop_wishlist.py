"""Lista de deseos de la tienda: cuándo avisar y qué decir, sin Discord ni base de datos.

Cada deseo guarda lo que ya se sabía de su artículo la última vez que se miró (`seen`,
unas marcas cortas). Solo se avisa de lo que cambia, para no repetir el mismo aviso
cada vez que se mira:

- 🏷️ `sale`: empieza una rebaja (o cambia la que había).
- 🗓️ `season`: empieza la temporada de un artículo de temporada.
- 📦 `restock`: vuelve a haber unidades de algo que estaba agotado.
- 💰 `money`: ya te llega el dinero. Solo una vez por deseo: un saldo que sube y
  baja con el casino no puede mandar un aviso en cada tirada.

Al apuntar un deseo, su estado de ese momento entra como ya visto: no se avisa de
lo que ya se veía en la ficha. Los avisos los manda el cog por mensaje privado
(`Tienda.whisper`), juntos en un solo mensaje por persona y servidor.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bot.services.economy import format_amount
from bot.services.shop import ShopItem, quote

#: Deseos por persona: los que caben en un desplegable de Discord (25).
MAX_WISHES = 25


@dataclass(frozen=True, slots=True)
class Wish:
    """Un artículo en la lista de deseos de un miembro.

    Attributes:
        seen: Marcas de lo que ya se sabía del artículo (`wish_state`).
    """

    guild_id: int
    user_id: int
    item_id: int
    created_at: float
    seen: frozenset[str]


def parse_seen(text: str | None) -> frozenset[str]:
    """Las marcas guardadas (`a,b,c`) como conjunto."""
    return frozenset(part for part in (text or "").split(",") if part)


def dump_seen(seen: Iterable[str]) -> str:
    """Las marcas como texto para guardarlas, siempre en el mismo orden."""
    return ",".join(sorted(seen))


def wish_state(
    item: ShopItem, *, now: float, balance: int, seasonal: bool, selling: bool
) -> frozenset[str]:
    """Lo que se sabe ahora de un artículo deseado, en marcas.

    Args:
        seasonal: Si solo se vende en su temporada.
        selling: Si se vende ahora (fuera de temporada, no).
    """
    marks: set[str] = set()
    if item.on_sale(now):
        until = int(item.discount_until) if item.discount_until is not None else 0
        marks.add(f"sale:{item.discount}:{until}")
    if seasonal and selling:
        marks.add("season")
    if item.sold_out:
        marks.add("soldout")
    elif selling and item.visible and balance >= quote(item, now).total:
        marks.add("money")
    return frozenset(marks)


def wish_news(old: frozenset[str], new: frozenset[str]) -> list[str]:
    """De qué hay que avisar al pasar de `old` a `new`: `sale`, `season`, `restock`, `money`."""
    news = []
    if any(mark.startswith("sale:") for mark in new - old):
        news.append("sale")
    if "season" in new - old:
        news.append("season")
    if "soldout" in old and "soldout" not in new:
        news.append("restock")
    if "money" in new - old:
        news.append("money")
    return news


def stored_seen(old: frozenset[str], new: frozenset[str]) -> frozenset[str]:
    """Las marcas que se guardan: las de ahora más `money`, que no se olvida."""
    return new | (old & {"money"})


def news_line(kind: str, item: ShopItem, now: float, *, season: str | None = None) -> str:
    """Una línea del aviso por mensaje privado."""
    label = f"{item.emoji} **{item.name}**"
    price = quote(item, now)
    if kind == "sale":
        until = (
            f" hasta <t:{int(item.discount_until)}:R>" if item.discount_until is not None else ""
        )
        return (
            f"🏷️ {label} está rebajado un {price.discount_pct} %: "
            f"{format_amount(price.base)} sin IGIC (antes {format_amount(price.price)}){until}."
        )
    if kind == "season":
        when = f": es temporada de {season}" if season else ""
        return f"🗓️ {label} vuelve a estar a la venta{when}."
    if kind == "restock":
        left = f": quedan {item.remaining}" if item.remaining is not None else ""
        return f"📦 {label} se ha repuesto{left}."
    level = f" (hace falta nivel {item.min_level})" if item.min_level else ""
    return f"💰 Ya te llega para {label}: {format_amount(price.total)} con IGIC{level}."


def news_message(guild_name: str, lines: list[str]) -> str:
    """El mensaje privado con todos los avisos de un servidor."""
    return "\n".join(
        [
            f"⭐ **Tu lista de deseos** en **{guild_name}**:",
            *(f"- {line}" for line in lines),
            "-# Lo compras en la `tienda`, sección ⭐ Mi lista. Si no quieres estos avisos, "
            "pulsa 🔕 en esa misma sección.",
        ]
    )


def stall_message(
    guild_name: str, item_label: str, *, seller: str, price: int, closes_at: float
) -> str:
    """El mensaje privado de que alguien vende en el Rastro algo de tu lista."""
    return (
        f"🧺 En **{guild_name}**, {seller} vende en el Rastro {item_label}, que está en tu "
        f"lista de deseos. Precio de salida: **{format_amount(price)}** (más el ITP).\n"
        f"-# Puja con `rastro` antes de <t:{int(closes_at)}:R>. Si no quieres estos avisos, "
        "pulsa 🔕 en la sección ⭐ Mi lista de la `tienda`."
    )
