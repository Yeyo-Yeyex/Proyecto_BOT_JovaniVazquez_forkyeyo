"""Muebles del vacacional: objetos de la tienda que suben la nota de las reseñas en `pisos`.

Se compran en el pasillo 🛋️ Muebles del vacacional de la `tienda` (`bot.services.shop_catalog`,
con la misma clave) y no se usan desde la mochila: con tenerlos basta, como las herramientas
de `pala`. Cada noche, `pisos` pregunta a la tienda qué tiene quien decide cada vivienda
vacacional (`bot.cogs.shop.owned_keys`) y `furniture_stars` junta lo que suben la nota de la
reseña de esa estancia (`housing.review_stars`).

No mueven dinero: suben la nota, y la nota sube la ocupación y, con «Superanfitrión», el
precio. El precio del mueble es el sumidero que lo compensa.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Furniture:
    """Un mueble del vacacional.

    Attributes:
        key: Clave del artículo en `bot.services.shop_catalog.CATALOG`.
        emoji: El mismo emoji que el artículo.
        name: Nombre corto para la ficha.
        stars: Lo que sube la nota de cada reseña.
        themes: Tipos de barrio donde sube además `bonus` (la barbacoa en el campo).
        guests: Huéspedes con los que sube además `bonus` (la cuna con las familias).
        bonus: Lo que sube de más en su barrio o con sus huéspedes.
        nomads: Si además atrae nómadas digitales (la fibra).
    """

    key: str
    emoji: str
    name: str
    stars: float
    themes: frozenset[str] = frozenset()
    guests: frozenset[str] = frozenset()
    bonus: float = 0.0
    nomads: bool = False


FURNITURE: tuple[Furniture, ...] = (
    Furniture("mueble_smart_tv", "📺", "Smart TV", 0.1),
    Furniture("mueble_aire", "❄️", "Aire acondicionado", 0.2),
    Furniture("mueble_cafetera", "☕", "Cafetera de cápsulas", 0.1),
    Furniture("mueble_colchon", "🛏️", "Colchón viscoelástico", 0.2),
    Furniture("mueble_fibra", "📶", "Fibra de 1 Gb", 0.1, nomads=True),
    Furniture("mueble_playa", "⛱️", "Kit de playa", 0.05, themes=frozenset({"playa"}), bonus=0.2),
    Furniture(
        "mueble_cuna", "🍼", "Cuna y trona", 0.05,
        guests=frozenset({"britanicos", "numerosa"}), bonus=0.25,
    ),
    Furniture(
        "mueble_barbacoa", "🍖", "Barbacoa", 0.05, themes=frozenset({"rural", "lujo"}), bonus=0.2
    ),
    Furniture("mueble_cerradura", "🔐", "Cerradura inteligente", 0.1),
    Furniture("mueble_juegos", "🎲", "Juegos de mesa", 0.05),
)  # fmt: skip

FURNITURE_KEYS: frozenset[str] = frozenset(f.key for f in FURNITURE)
#: Lo más que suben los muebles la nota de una reseña.
MAX_FURNITURE_STARS = 0.8
#: Lo que la fibra multiplica la probabilidad de encontrar nómadas.
NOMAD_BOOST = 1.15


def furniture_stars(owned: Collection[str], theme: str, guest: str | None) -> float:
    """Lo que suben la nota los muebles que tiene alguien, en un barrio y con un huésped."""
    total = 0.0
    for item in FURNITURE:
        if item.key not in owned:
            continue
        total += item.stars
        if theme in item.themes or (guest is not None and guest in item.guests):
            total += item.bonus
    return min(MAX_FURNITURE_STARS, total)


def nomad_boost(owned: Collection[str]) -> float:
    """Lo que multiplica la demanda de nómadas: la fibra la sube."""
    return NOMAD_BOOST if any(f.nomads and f.key in owned for f in FURNITURE) else 1.0
