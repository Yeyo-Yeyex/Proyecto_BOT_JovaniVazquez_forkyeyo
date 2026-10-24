"""Las calles de cada barrio de `pisos`, sus edificios y las unidades de cada edificio (9.14.0).

Cada barrio tiene 3 calles con 3 edificios, y **cada edificio es el de uno de los 9 inmuebles
destacados del barrio** (la tarjeta N vive en el edificio N): un piso del 2.º, en un bloque con
su local en el bajo y su garaje en el sótano; un chalé, en su casa; un garaje, en un
aparcamiento; un amarre, en un pantalán. Alrededor del destacado salen sus vecinos: unidades
nuevas, sin dueño, que se compran y se alquilan como cualquier inmueble. Plan entero en
`docs/pisos-calles.md`.

Nada de lo que ya existía cambia: los destacados conservan su clave, sus metros y su planta, y
las unidades nuevas tienen claves nuevas (`<edificio>_<hueco>`, p. ej. `triana_e3_2b`). El
catálogo sale de una regla fija (el destacado y el tema del barrio), sin azar del momento: una
prueba fija su huella (`fingerprint`) para que no cambie sin querer, porque cambiarlo dejaría
participaciones y contratos apuntando a unidades que ya no son las mismas.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from bot.services.housing_catalog import BARRIO_BY_KEY, BARRIOS, Listing, listings_of
from bot.services.housing_street_names import STREET_NAMES, UNIT_BLURBS

#: Los tipos de edificio, por lo que es su destacado.
BLOQUE, APARCAMIENTO, GUARDAMUEBLES, CASA = "bloque", "aparcamiento", "guardamuebles", "casa"
COMPLEJO, PANTALAN, POLIGONO, EDIFICIO = "complejo", "pantalan", "poligono", "edificio"
BUILDING_NAMES = {
    BLOQUE: "Bloque de pisos",
    APARCAMIENTO: "Aparcamiento",
    GUARDAMUEBLES: "Guardamuebles",
    CASA: "Casa",
    COMPLEJO: "Complejo de bungalows",
    PANTALAN: "Pantalán",
    POLIGONO: "Naves del polígono",
    EDIFICIO: "Edificio entero",
}
_BUILDING_OF_KIND = {
    "garaje": APARCAMIENTO,
    "trastero": GUARDAMUEBLES,
    "chalet": CASA,
    "terrera": CASA,
    "cueva": CASA,
    "finca": CASA,
    "casona": CASA,
    "bungalow": COMPLEJO,
    "amarre": PANTALAN,
    "nave": POLIGONO,
    "edificio": EDIFICIO,
}
#: Los que van en la tercera calle del barrio (la del puerto o el polígono).
_SPECIAL = frozenset({PANTALAN, POLIGONO})


@dataclass(frozen=True, slots=True)
class Theme:
    """Cómo son los bloques de un tipo de barrio.

    Attributes:
        floors: Plantas sobre la calle, con el bajo (de las que sale el ático, si lo hay).
        per_floor: Viviendas por planta.
        locales: Locales en el bajo (0: el bajo es una vivienda).
        garajes: Plazas de garaje en el sótano.
        trasteros: Trasteros en el sótano.
        attic: Si la planta de arriba es un ático.
        m2: Metros de una vivienda de las que se inventan, si el destacado no es vivienda.
    """

    floors: int
    per_floor: int
    locales: int
    garajes: int
    trasteros: int
    attic: bool
    m2: int


THEMES: dict[str, Theme] = {
    "centro": Theme(4, 2, 1, 2, 1, True, 85),
    "historico": Theme(3, 1, 1, 0, 1, False, 95),
    "obrero": Theme(5, 2, 1, 1, 1, False, 70),
    "playa": Theme(4, 2, 1, 1, 1, True, 60),
    "marinero": Theme(3, 1, 1, 0, 1, False, 65),
    "lujo": Theme(3, 1, 1, 2, 0, True, 120),
    "rural": Theme(2, 1, 0, 0, 1, False, 90),
}
#: Plazas por planta de un aparcamiento, trasteros de un guardamuebles, bungalows de un
#: complejo, amarres de un pantalán y naves de un polígono (con el destacado).
PLAZAS = 5
TRASTEROS = 5
BUNGALOWS = 4
AMARRES = 4
NAVES = 2

_HOMES = frozenset({"estudio", "bajo", "piso2", "piso3", "atico", "loft", "duplex"})
#: Lo que piden de menos las plazas y los trasteros nuevos frente a los sueltos del catálogo:
#: los alquilan sobre todo los vecinos, que pagan menos. Sin esto, cientos de ellos a un 9-12 %
#: adelantaban el Falcon de la simulación por debajo del día 350.
BASEMENT_RENT = -0.25


@dataclass(frozen=True, slots=True)
class Street:
    """Una calle de un barrio.

    Attributes:
        key: `<barrio>_c<n>` (n de 1 a 3).
        barrio: Clave del barrio.
        name: Como en la placa («Calle Mayor de Triana»).
        blurb: Qué es o qué hay, en una línea.
    """

    key: str
    barrio: str
    name: str
    blurb: str


@dataclass(frozen=True, slots=True)
class Unit:
    """Un hueco de un edificio: la unidad que lo ocupa y dónde está.

    Attributes:
        key: Clave del inmueble (el destacado conserva la suya).
        level: Planta: -1 el sótano, 0 el bajo, 1 la primera… (para el corte del edificio).
        label: Cómo se dice («2.º B», «Plaza 3», «Local 1»).
    """

    key: str
    level: int
    label: str


@dataclass(frozen=True, slots=True)
class Building:
    """Un edificio de una calle, con sus unidades de abajo arriba.

    Attributes:
        key: `<barrio>_e<n>`: el edificio de la tarjeta N del barrio.
        barrio: Clave del barrio.
        street: Clave de su calle.
        number: Número de portal.
        kind: `BLOQUE`, `APARCAMIENTO`, `GUARDAMUEBLES`, `CASA`, `COMPLEJO`, `PANTALAN`,
            `POLIGONO` o `EDIFICIO`.
        featured: Clave del destacado (el inmueble del catálogo que vive aquí).
        floors: Plantas sobre la calle, con el bajo (1 en una casa; 0 en lo que no tiene).
        units: Sus unidades, de abajo arriba y de izquierda a derecha.
    """

    key: str
    barrio: str
    street: str
    number: int
    kind: str
    featured: str
    floors: int
    units: tuple[Unit, ...]

    @property
    def name(self) -> str:
        """«Bloque de pisos», «Casa», «Pantalán»…"""
        return BUILDING_NAMES[self.kind]


def _seed(*parts: object) -> int:
    """Un número fijo para cada cosa (no `hash`: cambia entre arranques de Python)."""
    text = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(text).digest()[:4], "big")


def _vary(base: int, *parts: object, spread: float = 0.15) -> int:
    """`base` ± `spread`, fijo para `parts`."""
    step = (_seed(*parts) % 1001) / 1000 * 2 - 1
    return max(1, round(base * (1 + spread * step)))


def _home_kind(m2: int) -> str:
    return "estudio" if m2 < 40 else "piso2" if m2 < 76 else "piso3"


def _floor_of(listing: Listing) -> tuple[int, int]:
    """`(planta, plantas que ocupa)` de una vivienda o local; el ático se pone luego arriba."""
    text = listing.floor
    if text.startswith("Ático"):
        return -2, 1  # arriba del todo
    if text.startswith("Bajo y"):
        return 0, 2
    numbers = [int(n) for n in re.findall(r"(\d+)\.º", text)]
    if not numbers:
        return (0, 1) if text.startswith("Bajo") else (1, 1)  # «Casa»: un piso de una casa
    return numbers[0], len(numbers)


def _unit(
    building: str, barrio: str, slot: str, kind: str, m2: int, label: str, street: Street,
    number: int, energy: str,
) -> Listing:  # fmt: skip
    """Una unidad nueva de un edificio, con su clave `<edificio>_<hueco>`."""
    blurbs = UNIT_BLURBS[kind]
    return Listing(
        key=f"{building}_{slot}",
        barrio=barrio,
        kind=kind,
        m2=m2,
        title=f"{label} · {street.name}, {number}",
        blurb=blurbs[_seed(building, slot) % len(blurbs)],
        floor=label,
        energy=energy,
        rent_bonus=BASEMENT_RENT if kind in ("garaje", "trastero") else 0.0,
    )


def _bloque(
    key: str, featured: Listing, street: Street, number: int, theme: Theme
) -> tuple[int, list[tuple[Unit, Listing | None]]]:
    """Las unidades de un bloque alrededor de su destacado (que conserva su sitio y su clave)."""
    floor, span = _floor_of(featured) if featured.kind != "local" else (0, 1)
    attic = theme.attic or floor == -2
    floors = max(theme.floors, floor + span + (1 if attic else 0), 2 if attic else 1)
    top = floors - 1 if attic else None  # la planta del ático
    # Los vecinos se parecen al destacado si es un piso; si es un estudio, un bajo o un ático,
    # son pisos del tamaño del barrio (un bloque entero de estudios adelantaba el Falcon).
    alike = featured.kind in ("piso2", "piso3", "loft", "duplex")
    base_m2 = featured.m2 if alike else theme.m2
    out: list[tuple[Unit, Listing | None]] = []
    barrio = featured.barrio

    def new(slot: str, level: int, kind: str, m2: int, label: str) -> None:
        listing = _unit(key, barrio, slot, kind, m2, label, street, number, featured.energy)
        out.append((Unit(listing.key, level, label), listing))

    for n in range(1, theme.garajes + 1):
        new(f"g{n}", -1, "garaje", 12, f"Plaza {n}")
    for n in range(1, theme.trasteros + 1):
        new(f"t{n}", -1, "trastero", _vary(5, key, "t", n, spread=0.3), f"Trastero {n}")
    # El bajo: sus locales (o una vivienda en los barrios sin comercio), con el destacado si
    # vive ahí.
    ground = max(theme.locales, 1)
    for n in range(1, ground + 1):
        if n == 1 and floor == 0:
            label = "Local 1" if featured.kind == "local" else featured.floor
            out.append((Unit(featured.key, 0, label), None))
            continue
        if theme.locales:
            new(f"b{n}", 0, "local", _vary(55, key, "b", n, spread=0.3), f"Local {n}")
        else:
            new(f"b{n}", 0, "bajo", _vary(base_m2, key, "b", n), "Bajo")
    # Las plantas, con el destacado en la suya (un dúplex ocupa también la de encima).
    busy = {(floor + i, 0) for i in range(span)} if floor > 0 else set()
    for level in range(1, floors):
        if level == top:
            break
        for n in range(theme.per_floor):
            letter = "ABCD"[n]
            if (level, n) in busy:
                if level == floor:
                    # Como sus vecinos («2.º A»), salvo un dúplex, que dice sus dos plantas.
                    label = featured.floor if span > 1 else f"{level}.º {letter}"
                    out.append((Unit(featured.key, level, label), None))
                continue
            m2 = _vary(base_m2, key, level, n)
            new(f"{level}{letter.lower()}", level, _home_kind(m2), m2, f"{level}.º {letter}")
    if top is not None:
        if floor == -2:
            out.append((Unit(featured.key, top, featured.floor), None))
        else:
            new("at", top, "atico", _vary(round(base_m2 * 1.1), key, "at"), "Ático")
    return floors, out


def _group(
    key: str, featured: Listing, street: Street, number: int, kind: str
) -> tuple[int, list[tuple[Unit, Listing | None]]]:
    """Un aparcamiento, un guardamuebles, un complejo, un pantalán o un polígono.

    El destacado es el primero de su grupo («Nave 1») y los nuevos siguen la cuenta.
    """
    first = {GUARDAMUEBLES: "Trastero", COMPLEJO: "Bungalow", PANTALAN: "Amarre", POLIGONO: "Nave"}
    out: list[tuple[Unit, Listing | None]] = [
        (Unit(featured.key, 0, f"{first.get(kind, featured.floor)} 1"), None)
    ]
    barrio = featured.barrio

    def new(slot: str, level: int, unit_kind: str, m2: int, label: str) -> None:
        listing = _unit(key, barrio, slot, unit_kind, m2, label, street, number, featured.energy)
        out.append((Unit(listing.key, level, label), listing))

    if kind == APARCAMIENTO:
        levels = max(1, max((int(n) for n in re.findall(r"-(\d)", featured.floor)), default=1))
        out = [(Unit(featured.key, -levels, f"Plaza {levels}{PLAZAS:02d}"), None)]
        for level in range(1, levels + 1):
            for n in range(1, PLAZAS + (0 if level == levels else 1)):
                new(f"p{level}{n}", -level, "garaje", 12, f"Plaza {level}{n:02d}")
        return 0, sorted(out, key=lambda pair: (pair[0].level, pair[0].label))
    if kind == GUARDAMUEBLES:
        for n in range(1, TRASTEROS):
            new(f"t{n}", 0, "trastero", _vary(6, key, n, spread=0.4), f"Trastero {n + 1}")
        return 1, out
    if kind == COMPLEJO:
        for n in range(1, BUNGALOWS):
            new(f"u{n}", 0, "bungalow", _vary(featured.m2, key, n), f"Bungalow {n + 1}")
        return 1, out
    if kind == PANTALAN:
        for n in range(1, AMARRES + 1):
            new(f"a{n}", 0, "amarre", _vary(featured.m2, key, n, spread=0.5), f"Amarre {n + 1}")
        return 0, out
    for n in range(1, NAVES):
        new(f"n{n}", 0, "nave", _vary(featured.m2, key, n, spread=0.25), f"Nave {n + 1}")
    return 1, out


def _barrio(barrio: str) -> tuple[list[Street], list[Building], list[Listing]]:
    """Las calles, los edificios y las unidades nuevas de un barrio."""
    names = STREET_NAMES[barrio]
    streets = [
        Street(f"{barrio}_c{n}", barrio, name, blurb)
        for n, (name, blurb) in enumerate(names, start=1)
    ]
    featured = list(listings_of(barrio))
    kinds = [_BUILDING_OF_KIND.get(item.kind, BLOQUE) for item in featured]
    # Los del puerto y el polígono, en la tercera calle; los demás, por orden del catálogo.
    order = sorted(range(len(featured)), key=lambda i: (kinds[i] in _SPECIAL, i))
    theme = THEMES[BARRIO_BY_KEY[barrio].theme]
    buildings, units = [], []
    for place, index in enumerate(order):
        item, kind = featured[index], kinds[index]
        key = f"{barrio}_e{index + 1}"
        street = streets[place // 3]
        number = 2 + (place % 3) * 12 + _seed(key) % 9
        if kind == BLOQUE:
            floors, pairs = _bloque(key, item, street, number, theme)
        elif kind in (CASA, EDIFICIO):
            floors, pairs = 1, [(Unit(item.key, 0, item.floor), None)]
        else:
            floors, pairs = _group(key, item, street, number, kind)
        buildings.append(
            Building(
                key,
                barrio,
                street.key,
                number,
                kind,
                item.key,
                floors,
                tuple(unit for unit, _listing in pairs),
            )  # fmt: skip
        )
        units += [listing for _unit_, listing in pairs if listing is not None]
    buildings.sort(key=lambda b: int(b.key.rsplit("_e", 1)[1]))
    return streets, buildings, units


def _catalog() -> tuple[tuple[Street, ...], tuple[Building, ...], tuple[Listing, ...]]:
    streets: list[Street] = []
    buildings: list[Building] = []
    units: list[Listing] = []
    for barrio in BARRIOS:
        s, b, u = _barrio(barrio.key)
        streets += s
        buildings += b
        units += u
    return tuple(streets), tuple(buildings), tuple(units)


STREETS, BUILDINGS, STREET_UNITS = _catalog()
STREET_BY_KEY: dict[str, Street] = {s.key: s for s in STREETS}
BUILDING_BY_KEY: dict[str, Building] = {b.key: b for b in BUILDINGS}
STREET_UNIT_BY_KEY: dict[str, Listing] = {u.key: u for u in STREET_UNITS}
#: El edificio de cada unidad (los destacados incluidos).
_BUILDING_OF: dict[str, str] = {u.key: b.key for b in BUILDINGS for u in b.units}


def building_of(key: str) -> Building | None:
    """El edificio en que está un inmueble (o una planta o habitación suya), si está en uno."""
    while key:
        found = _BUILDING_OF.get(key)
        if found is not None:
            return BUILDING_BY_KEY[found]
        head, sep, _tail = key.rpartition("~")
        key = head if sep else ""
    return None


def streets_of(barrio: str) -> tuple[Street, ...]:
    """Las tres calles de un barrio."""
    return tuple(s for s in STREETS if s.barrio == barrio)


def buildings_of(street: str) -> tuple[Building, ...]:
    """Los edificios de una calle, por su número."""
    return tuple(sorted((b for b in BUILDINGS if b.street == street), key=lambda b: b.number))


def units_of_barrio(barrio: str) -> tuple[Listing, ...]:
    """Las unidades nuevas de las calles de un barrio (sin los destacados)."""
    return tuple(u for u in STREET_UNITS if u.barrio == barrio)


def fingerprint() -> str:
    """La huella del catálogo: cambia si cambia cualquier clave, tipo, metro o planta."""
    rows = sorted(
        (b.key, b.street, b.kind, b.number, u.key, u.level, u.label)
        for b in BUILDINGS
        for u in b.units
    )
    rows += sorted((u.key, u.kind, u.m2, u.floor) for u in STREET_UNITS)  # type: ignore[misc]
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]
