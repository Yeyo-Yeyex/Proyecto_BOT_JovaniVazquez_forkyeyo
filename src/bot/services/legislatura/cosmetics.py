"""Aspectos de Legislatura: tapetes, dorsos y arte alternativo de asesores.

Se compran con yapdollars en la `tienda` del bot y **no cambian las reglas**:
son el sumidero de dinero del juego y lo que se presume en el canal. Funcionan
como las herramientas de `pala` (`bot.services.work_tools`): con tener el
artículo basta. El cog pregunta qué claves tiene el miembro
(`bot.cogs.shop.owned_keys`), le deja elegir uno por hueco y guarda la elección
en su perfil de Legislatura; si deja de tenerlo (lo vende o lo regala), se
vuelve al de serie (`resolve`).

**Alta en la tienda.** Cada aspecto generará su artículo solo, como las
mascotas (`shop_catalog._pet`): misma clave, el pasillo de su tema (`aisle`) y
`Kind.TROPHY`. No se dan de alta todavía, porque saldrían a la venta antes de
que exista el juego; se hace en la fase de la tienda, siguiendo la ficha de alta
de la Biblia (precio en tiradas, IGIC real, nivel mínimo si cuestan 100.000 Y$ o
más). Los precios de aquí son una propuesta.

Los aspectos caros son la otra mitad de la economía del juego: Legislatura
reparte dinero de habilidad y estos objetos lo recogen, junto con el Falcon y el
resto de lujos del colmado.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum

from bot.services.legislatura.items import Bloc


class SkinSlot(StrEnum):
    """Dónde se ve un aspecto. El valor se guarda en el perfil."""

    TAPETE = "tapete"
    DORSO = "dorso"
    ASESOR = "asesor"


@dataclass(frozen=True, slots=True)
class Skin:
    """Un aspecto.

    Attributes:
        key: Clave del artículo de la tienda (y del aspecto): no se cambia nunca.
        slot: Dónde se ve.
        name: Nombre visible.
        emoji: Emoji del artículo.
        bloc: De quién va la broma (la regla de sátira vale también aquí).
        aisle: Pasillo de la tienda por su tema.
        price: Propuesta de precio en Y$ (escala general).
        felt: Colores del tapete (fondo, borde) en RGB; solo tapetes.
        target: Asesor al que viste; solo arte de asesores.
        default: Si es el de serie (gratis, sin artículo en la tienda).
        pattern: Dibujo que la escena pinta encima del tapete (una bandera, p. ej.).
    """

    key: str
    slot: SkinSlot
    name: str
    emoji: str
    bloc: Bloc
    aisle: str = "moncloa"
    price: int = 0
    felt: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None
    target: str | None = None
    default: bool = False
    pattern: str | None = None


SKINS: tuple[Skin, ...] = (
    Skin(
        "tapete_casino",
        SkinSlot.TAPETE,
        "Tapete del Casino del Estado",
        "🟩",
        Bloc.ESPANA,
        felt=((42, 138, 87), (6, 40, 26)),
        default=True,
    ),
    Skin(
        "tapete_congreso",
        SkinSlot.TAPETE,
        "Escaño del Congreso",
        "🟥",
        Bloc.ESPANA,
        price=120_000,
        felt=((150, 30, 40), (40, 6, 10)),
    ),
    Skin("dorso_clasico", SkinSlot.DORSO, "Dorso de Fournier", "🂠", Bloc.ESPANA, default=True),
    Skin(
        "tapete_bruselas",
        SkinSlot.TAPETE,
        "Moqueta del Berlaymont",
        "🟦",
        Bloc.UE,
        aisle="ventanilla",
        price=150_000,
        felt=((18, 40, 110), (10, 26, 74)),
    ),
    Skin(
        "tapete_despacho_oval",
        SkinSlot.TAPETE,
        "Alfombra del Despacho Oval",
        "🟨",
        Bloc.EEUU,
        aisle="lujo",
        price=400_000,
        felt=((196, 170, 96), (140, 118, 60)),
    ),
    Skin(
        "tapete_bandera_israel",
        SkinSlot.TAPETE,
        "Bandera de Israel",
        "🇮🇱",
        Bloc.ISRAEL,
        aisle="lujo",
        price=400_000,
        felt=((246, 248, 252), (196, 206, 222)),
        pattern="bandera_israel",
    ),
    Skin(
        "tapete_gran_salon",
        SkinSlot.TAPETE,
        "Gran Salón del Pueblo",
        "🟥",
        Bloc.CHINA,
        aisle="hongkong",
        price=400_000,
        felt=((150, 18, 24), (196, 160, 40)),
    ),
    Skin(
        "tapete_falcon",
        SkinSlot.TAPETE,
        "Tapicería del Falcon",
        "✈️",
        Bloc.ESPANA,
        aisle="moncloa",
        price=2_500_000,
        felt=((222, 214, 196), (120, 110, 92)),
    ),
    Skin(
        "dorso_boe",
        SkinSlot.DORSO,
        "Dorso del BOE",
        "📰",
        Bloc.ESPANA,
        aisle="ventanilla",
        price=80_000,
    ),
    Skin(
        "dorso_euro",
        SkinSlot.DORSO,
        "Dorso de billete de 500 €",
        "💶",
        Bloc.UE,
        aisle="lujo",
        price=1_000_000,
    ),
    Skin(
        "arte_perro_sanxe_gafas",
        SkinSlot.ASESOR,
        "Perro Sanxe con gafas de sol",
        "🕶️",
        Bloc.ESPANA,
        aisle="moncloa",
        price=750_000,
        target="perro_sanxe",
    ),
)

SKIN_BY_KEY: dict[str, Skin] = {s.key: s for s in SKINS}
DEFAULTS: dict[SkinSlot, Skin] = {s.slot: s for s in SKINS if s.default}


def available(owned: Collection[str]) -> list[Skin]:
    """Aspectos que puede elegir un miembro: los de serie y los que tiene comprados."""
    return [s for s in SKINS if s.default or s.key in owned]


def resolve(choice: Mapping[str, str], owned: Collection[str]) -> dict[str, Skin]:
    """Aspecto que se ve en cada hueco según lo elegido y lo que aún tiene.

    Args:
        choice: Lo guardado en el perfil: hueco (o `asesor:<clave>`) → clave del aspecto.
        owned: Claves de artículos que tiene ahora.

    Returns:
        Hueco → aspecto. Lo que ya no tiene vuelve al de serie; el arte de un
        asesor sin elegir no aparece (se dibuja el de siempre).
    """
    result: dict[str, Skin] = {slot.value: skin for slot, skin in DEFAULTS.items()}
    for where, key in choice.items():
        skin = SKIN_BY_KEY.get(key)
        if skin is None or not (skin.default or skin.key in owned):
            continue
        if skin.slot is SkinSlot.ASESOR:
            if where == f"asesor:{skin.target}":
                result[where] = skin
        elif where == skin.slot.value:
            result[where] = skin
    return result
