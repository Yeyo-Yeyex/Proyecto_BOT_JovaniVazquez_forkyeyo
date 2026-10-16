"""Catálogo de `pisos`: los barrios de Canarias, sus inmuebles e inquilinos.

Solo datos, sin reglas: lo que vale y renta cada cosa lo calcula
`bot.services.housing` a partir de lo de aquí. Cada barrio lleva su precio de
venta y de alquiler por metro cuadrado, aproximados a lo que pedían los
portales en 2026 (Gran Canaria y Tenerife), y cada inmueble su tipo, sus metros
y la descripción del anuncio.

Las claves (`Barrio.key`, `Listing.key`) no se cambian nunca: son lo que guarda
quién tiene qué en cada servidor y lo que cuentan los logros. Un inmueble nuevo
se añade al final de su barrio; retirar uno dejaría huérfanas las
participaciones de quien lo tuviera.

Las coordenadas del mapa (`x`, `y`, de 0 a 1 dentro del recuadro de cada isla)
las usa la escena (`assets/pisos/escena.html`) para poner las chinchetas.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Islas con barrios, en el orden en que salen en el mapa y en el desplegable.
ISLANDS: dict[str, str] = {"gc": "Gran Canaria", "tf": "Tenerife"}


@dataclass(frozen=True, slots=True)
class Kind:
    """Tipo de inmueble: cómo se pasa de los metros del barrio a precio y renta.

    Attributes:
        key: Clave estable.
        name: Nombre en la ficha.
        price_factor: Multiplica el precio por metro del barrio (un bajo interior
            vale menos que la media; un ático, más).
        rent_factor: Multiplica la renta por metro (lo pequeño renta más por
            metro, como en la vida real).
        fixed_m2: Si no es `None`, el precio y la renta salen de estos metros
            «equivalentes» y no de los del anuncio (garajes y trasteros se venden
            por plaza, no por metro).
        community: Comunidad (o mantenimiento) en euros al mes.
    """

    key: str
    name: str
    price_factor: float
    rent_factor: float
    fixed_m2: float | None
    community: int


KINDS: dict[str, Kind] = {
    k.key: k
    for k in (
        Kind("trastero", "Trastero", 1.0, 1.6, 3.5, 5),
        Kind("garaje", "Plaza de garaje", 1.0, 1.0, 9.0, 12),
        Kind("estudio", "Estudio", 0.9, 1.35, None, 35),
        Kind("bajo", "Bajo", 0.85, 1.3, None, 35),
        Kind("piso2", "Piso de 2 dormitorios", 1.0, 1.0, None, 55),
        Kind("piso3", "Piso de 3 dormitorios", 1.0, 0.95, None, 70),
        Kind("atico", "Ático", 1.3, 1.15, None, 110),
        Kind("chalet", "Chalet", 1.15, 0.85, None, 120),
        Kind("edificio", "Edificio entero", 1.0, 1.0, None, 250),
    )
}


@dataclass(frozen=True, slots=True)
class Barrio:
    """Un barrio con mercado propio.

    Attributes:
        key: Clave estable.
        name: Nombre corto (cabe en un botón).
        town: Municipio.
        island: Clave de `ISLANDS`.
        price_m2: Euros por metro cuadrado a la venta.
        rent_m2: Euros por metro cuadrado al mes en alquiler.
        blurb: Una línea de anuncio sobre el barrio.
        x: Posición en el mapa de su isla (0 izquierda, 1 derecha).
        y: Posición en el mapa de su isla (0 arriba, 1 abajo).
    """

    key: str
    name: str
    town: str
    island: str
    price_m2: int
    rent_m2: float
    blurb: str
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Listing:
    """Un inmueble concreto de un barrio (hay uno de cada en cada servidor).

    Attributes:
        key: Clave estable (`barrio_algo`).
        barrio: Clave del barrio.
        kind: Clave de `KINDS`.
        m2: Metros del anuncio.
        title: Titular del anuncio.
        blurb: Letra pequeña con sorna.
        floor: Planta («Bajo», «4.º sin ascensor»…).
        energy: Certificado energético (A a G).
    """

    key: str
    barrio: str
    kind: str
    m2: int
    title: str
    blurb: str
    floor: str
    energy: str


BARRIOS: tuple[Barrio, ...] = (
    # -- Gran Canaria -------------------------------------------------------------
    Barrio(
        "isleta",
        "La Isleta",
        "Las Palmas de Gran Canaria",
        "gc",
        2_400,
        12.5,
        "Barrio marinero de toda la vida. De momento.",
        0.80,
        0.07,
    ),
    Barrio(
        "canteras",
        "Las Canteras",
        "Las Palmas de Gran Canaria",
        "gc",
        3_900,
        17.5,
        "La playa urbana con más portátiles por metro cuadrado de Europa.",
        0.73,
        0.12,
    ),
    Barrio(
        "schamann",
        "Schamann",
        "Las Palmas de Gran Canaria",
        "gc",
        1_700,
        11.0,
        "Cuestas, guaguas y el último alquiler que paga un sueldo canario.",
        0.69,
        0.19,
    ),
    Barrio(
        "triana",
        "Triana",
        "Las Palmas de Gran Canaria",
        "gc",
        3_300,
        15.0,
        "Calle Mayor, tiendas de marca y balcones de los que nadie sale.",
        0.81,
        0.19,
    ),
    Barrio(
        "vegueta",
        "Vegueta",
        "Las Palmas de Gran Canaria",
        "gc",
        3_200,
        15.0,
        "Casco histórico de 1478. Las maletas con ruedas, de 2015.",
        0.83,
        0.25,
    ),
    Barrio(
        "tafira",
        "Tafira",
        "Las Palmas de Gran Canaria",
        "gc",
        2_900,
        12.0,
        "Chalets, jardines y vistas a la Caldera de Bandama. Aquí no llega la guagua.",
        0.66,
        0.29,
    ),
    Barrio(
        "telde",
        "San Gregorio",
        "Telde",
        "gc",
        1_900,
        10.5,
        "La segunda ciudad de la isla, a quince minutos de todo menos de un alquiler.",
        0.84,
        0.40,
    ),
    Barrio(
        "arucas",
        "Arucas",
        "Arucas",
        "gc",
        1_600,
        9.5,
        "Plataneras, ron y una catedral que no es catedral.",
        0.55,
        0.11,
    ),
    Barrio(
        "maspalomas",
        "Maspalomas",
        "San Bartolomé de Tirajana",
        "gc",
        4_200,
        18.0,
        "Dunas, bungalows de los 70 y alemanes que llegaron en 1972 y no se fueron.",
        0.55,
        0.91,
    ),
    Barrio(
        "mogan",
        "Puerto de Mogán",
        "Mogán",
        "gc",
        5_000,
        21.0,
        "La Venecia de Canarias, sin góndolas y con más yates que vecinos.",
        0.23,
        0.80,
    ),
    # -- Tenerife ------------------------------------------------------------------
    Barrio(
        "sanandres",
        "San Andrés",
        "Santa Cruz de Tenerife",
        "tf",
        2_100,
        11.0,
        "Las Teresitas a un paso y la arena traída del Sáhara en 1973.",
        0.93,
        0.10,
    ),
    Barrio(
        "laguna",
        "La Laguna",
        "San Cristóbal de La Laguna",
        "tf",
        2_700,
        13.5,
        "Patrimonio de la Humanidad, universitarios y niebla gratis todo el año.",
        0.79,
        0.15,
    ),
    Barrio(
        "santacruz",
        "Santa Cruz",
        "Santa Cruz de Tenerife",
        "tf",
        2_700,
        13.0,
        "La capital, el Carnaval y la calle Castillo con más inmobiliarias que bares.",
        0.87,
        0.21,
    ),
    Barrio(
        "ofra",
        "Ofra",
        "Santa Cruz de Tenerife",
        "tf",
        1_550,
        10.0,
        "El barrio de siempre del Hospital de la Candelaria. Bloques, vecinos y sombra.",
        0.82,
        0.26,
    ),
    Barrio(
        "taco",
        "Taco",
        "San Cristóbal de La Laguna",
        "tf",
        1_500,
        10.0,
        "Lo más barato del área metropolitana. Que no se entere el fondo.",
        0.77,
        0.30,
    ),
    Barrio(
        "puertocruz",
        "Puerto de la Cruz",
        "Puerto de la Cruz",
        "tf",
        2_600,
        12.5,
        "Turismo desde 1890: aquí se jubilaron los ingleses antes de que fuera moda.",
        0.49,
        0.30,
    ),
    Barrio(
        "orotava",
        "La Orotava",
        "La Orotava",
        "tf",
        2_000,
        10.5,
        "Balcones de tea, el valle y el Teide de fondo en todas las fotos.",
        0.56,
        0.37,
    ),
    Barrio(
        "medano",
        "El Médano",
        "Granadilla de Abona",
        "tf",
        3_300,
        15.0,
        "Kitesurf, viento y aviones de Tenerife Sur como despertador.",
        0.44,
        0.82,
    ),
    Barrio(
        "cristianos",
        "Los Cristianos",
        "Arona",
        "tf",
        3_900,
        17.0,
        "El ferry a La Gomera y el pueblo pesquero que fue y ya no es.",
        0.24,
        0.88,
    ),
    Barrio(
        "adeje",
        "Costa Adeje",
        "Adeje",
        "tf",
        4_900,
        20.0,
        "Resorts, villas con piscina infinita y camareros que viven en la furgoneta.",
        0.14,
        0.77,
    ),
)

BARRIO_BY_KEY: dict[str, Barrio] = {b.key: b for b in BARRIOS}


def _l(
    barrio: str, slug: str, kind: str, m2: int, title: str, blurb: str, floor: str, energy: str
) -> Listing:
    return Listing(f"{barrio}_{slug}", barrio, kind, m2, title, blurb, floor, energy)


LISTINGS: tuple[Listing, ...] = (
    # La Isleta
    _l(
        "isleta",
        "bajo",
        "bajo",
        32,
        "Bajo con olor a puchero",
        "Ideal inversores. El puchero de la vecina va incluido en el precio.",
        "Bajo",
        "F",
    ),
    _l(
        "isleta",
        "piso2",
        "piso2",
        62,
        "Piso marinero a dos calles de El Confital",
        "Reformado en 1994. Los azulejos del baño son vintage, no viejos.",
        "2.º sin ascensor",
        "E",
    ),
    _l(
        "isleta",
        "piso3",
        "piso3",
        85,
        "Piso familiar con vistas al Puerto",
        "Tres dormitorios: perfecto para alquilar por habitaciones a tres nómadas.",
        "5.º",
        "E",
    ),
    # Las Canteras
    _l(
        "canteras",
        "estudio",
        "estudio",
        28,
        "Estudio a 50 m de Las Canteras",
        "Wifi de fibra y una cama plegable. Los nómadas digitales lo pagan en una tarde.",
        "1.º",
        "E",
    ),
    _l(
        "canteras",
        "piso2",
        "piso2",
        65,
        "Piso en Guanarteme con vistas a La Cícer",
        "Se escuchan las olas. Y la terraza del bar de abajo hasta las dos.",
        "3.º",
        "D",
    ),
    _l(
        "canteras",
        "atico",
        "atico",
        110,
        "Ático en primera línea de Las Canteras",
        "Terraza de 40 m² mirando al Teide. Abstenerse quien tenga nómina canaria.",
        "Ático",
        "C",
    ),
    # Schamann
    _l(
        "schamann",
        "trastero",
        "trastero",
        6,
        "Trastero «apto para vivir»",
        "Sin ventana, sin cédula y sin vergüenza. Se alquila a quien lo necesite.",
        "Sótano",
        "G",
    ),
    _l(
        "schamann",
        "bajo",
        "bajo",
        30,
        "Bajo interior luminoso",
        "Luminoso: tiene una ventana al patio de luces. Para entrar a vivir tras una "
        "reforma integral.",
        "Bajo",
        "G",
    ),
    _l(
        "schamann",
        "piso2",
        "piso2",
        60,
        "Piso de dos dormitorios en lo alto de la cuesta",
        "Cuarta planta sin ascensor: el gimnasio va incluido.",
        "4.º sin ascensor",
        "F",
    ),
    # Triana
    _l(
        "triana",
        "garaje",
        "garaje",
        11,
        "Plaza de garaje en Triana",
        "Cabe un Smart o un Seat Ibiza aparcado con fe.",
        "Sótano -2",
        "—",
    ),
    _l(
        "triana",
        "piso3",
        "piso3",
        95,
        "Piso señorial junto a la calle Mayor",
        "Techos altos, suelo hidráulico y un portero que lo sabe todo.",
        "2.º",
        "E",
    ),
    _l(
        "triana",
        "atico",
        "atico",
        90,
        "Ático con terraza en Triana",
        "Perfecto para un Airbnb que el Ayuntamiento todavía no ha visto.",
        "Ático",
        "D",
    ),
    # Vegueta
    _l(
        "vegueta",
        "trastero",
        "trastero",
        5,
        "Trastero en una casa terrera del siglo XVI",
        "Lo más barato del casco histórico. Las cucarachas tienen más antigüedad que tú.",
        "Bajo",
        "G",
    ),
    _l(
        "vegueta",
        "piso2",
        "piso2",
        70,
        "Piso con balcón de tea frente a la Catedral",
        "Bien de Interés Cultural: no puedes cambiar ni la bombilla sin permiso.",
        "1.º",
        "F",
    ),
    _l(
        "vegueta",
        "edificio",
        "edificio",
        600,
        "Edificio entero de 8 viviendas",
        "Seis son pisos turísticos. Las otras dos, vecinos que no se van ni con agua caliente.",
        "Bajo a 3.º",
        "E",
    ),
    # Tafira
    _l(
        "tafira",
        "garaje",
        "garaje",
        14,
        "Plaza de garaje doble en Tafira",
        "Imprescindible: aquí sin coche no llegas ni a la panadería.",
        "Sótano",
        "—",
    ),
    _l(
        "tafira",
        "piso3",
        "piso3",
        110,
        "Piso con jardín comunitario",
        "Zona tranquila y residencial. Traducción: no hay nada abierto después de las ocho.",
        "1.º",
        "D",
    ),
    _l(
        "tafira",
        "chalet",
        "chalet",
        260,
        "Chalet con vistas a la Caldera de Bandama",
        "Jardín, barbacoa y un muro de piedra para que no se vea el barrio de al lado.",
        "Casa",
        "C",
    ),
    # San Gregorio (Telde)
    _l(
        "telde",
        "bajo",
        "bajo",
        35,
        "Bajo en San Gregorio",
        "A quince minutos del aeropuerto y de la capital. Sin tráfico, a las cuatro de la mañana.",
        "Bajo",
        "F",
    ),
    _l(
        "telde",
        "piso2",
        "piso2",
        70,
        "Piso de dos dormitorios en Telde",
        "Para entrar a vivir. Si la ITE lo permite.",
        "3.º",
        "E",
    ),
    _l(
        "telde",
        "piso3",
        "piso3",
        90,
        "Piso familiar en San Gregorio",
        "Colegio, centro de salud y una guagua que pasa cuando quiere.",
        "2.º",
        "E",
    ),
    # Arucas
    _l(
        "arucas",
        "trastero",
        "trastero",
        8,
        "Cuarto de aperos en una finca de plataneras",
        "Huele a plátano. Y a azufre. Mucho rústico, poco legal.",
        "Finca",
        "G",
    ),
    _l(
        "arucas",
        "piso2",
        "piso2",
        75,
        "Piso junto a la Iglesia de San Juan Bautista",
        "La «catedral» de Arucas que no es catedral. Tampoco el piso es tan grande.",
        "2.º",
        "E",
    ),
    _l(
        "arucas",
        "chalet",
        "chalet",
        220,
        "Casa canaria con patio y aljibe",
        "Patio con platanera, techos de tea y una cuadra que se vende como «loft».",
        "Casa",
        "E",
    ),
    # Maspalomas
    _l(
        "maspalomas",
        "estudio",
        "estudio",
        30,
        "Bungalow de los 70 en Playa del Inglés",
        "Piscina comunitaria con flotadores de 1983. Sin licencia vacacional (de momento).",
        "Bajo",
        "F",
    ),
    _l(
        "maspalomas",
        "piso2",
        "piso2",
        60,
        "Apartamento con piscina en el Campo Internacional",
        "Ideal jubilado nórdico. Ya hay tres esperando en la puerta.",
        "1.º",
        "D",
    ),
    _l(
        "maspalomas",
        "chalet",
        "chalet",
        200,
        "Villa junto a las dunas",
        "Las dunas están protegidas. La villa también, por una empresa de seguridad.",
        "Casa",
        "B",
    ),
    # Puerto de Mogán
    _l(
        "mogan",
        "garaje",
        "garaje",
        12,
        "Plaza de garaje frente al puerto",
        "Más cara que un piso en Ofra. Así está el mercado.",
        "Sótano",
        "—",
    ),
    _l(
        "mogan",
        "atico",
        "atico",
        95,
        "Ático frente a los canales",
        "La Venecia de Canarias: buganvillas, yates y un alquiler en libras.",
        "Ático",
        "C",
    ),
    _l(
        "mogan",
        "chalet",
        "chalet",
        280,
        "Villa con embarcadero propio",
        "Para el Falcon todavía no hay pista. Para el yate, sí.",
        "Casa",
        "A",
    ),
    # San Andrés
    _l(
        "sanandres",
        "bajo",
        "bajo",
        30,
        "Casita frente a Las Teresitas",
        "Arena del Sáhara, tabaibas y el pescado más fresco de Santa Cruz.",
        "Bajo",
        "F",
    ),
    _l(
        "sanandres",
        "piso2",
        "piso2",
        70,
        "Piso con vistas a Anaga",
        "La carretera a Santa Cruz se corta cuando llueve. O sea, nunca. O sea, siempre.",
        "2.º",
        "E",
    ),
    _l(
        "sanandres",
        "chalet",
        "chalet",
        180,
        "Casa en la ladera de San Andrés",
        "Vistas al mar y escaleras hasta la puerta: 142 escalones contados.",
        "Casa",
        "D",
    ),
    # La Laguna
    _l(
        "laguna",
        "estudio",
        "estudio",
        26,
        "Estudio para universitarios de la ULL",
        "Caben cinco estudiantes en 26 m² con litera triple. Precio por cabeza.",
        "1.º",
        "F",
    ),
    _l(
        "laguna",
        "piso2",
        "piso2",
        72,
        "Piso en la calle de La Carrera",
        "Patrimonio de la Humanidad, humedad incluida en el precio.",
        "2.º",
        "E",
    ),
    _l(
        "laguna",
        "piso3",
        "piso3",
        95,
        "Casa canaria en el casco",
        "Patio interior, balcones de madera y una licencia de obras que tarda tres años.",
        "Casa",
        "E",
    ),
    # Santa Cruz
    _l(
        "santacruz",
        "garaje",
        "garaje",
        12,
        "Plaza de garaje junto a la Plaza de España",
        "Para aparcar en Carnaval. El resto del año, para mirarla.",
        "Sótano -3",
        "—",
    ),
    _l(
        "santacruz",
        "piso3",
        "piso3",
        100,
        "Piso de tres dormitorios en el centro",
        "A dos pasos de la calle Castillo y a tres de no poder pagarlo.",
        "4.º",
        "D",
    ),
    _l(
        "santacruz",
        "edificio",
        "edificio",
        650,
        "Edificio de 8 viviendas en la calle Castillo",
        "Locales abajo, oficinas arriba y un fondo de inversión llamando a la puerta.",
        "Bajo a 4.º",
        "D",
    ),
    # Ofra
    _l(
        "ofra",
        "trastero",
        "trastero",
        6,
        "Trastero en un bloque de Ofra",
        "Seco, con luz y con un vecino que te ofrece alquilarte el suyo más barato.",
        "Sótano",
        "G",
    ),
    _l(
        "ofra",
        "bajo",
        "bajo",
        34,
        "Bajo junto al Hospital de la Candelaria",
        "Perfecto para un MIR: llegas a la guardia en zapatillas.",
        "Bajo",
        "F",
    ),
    _l(
        "ofra",
        "piso2",
        "piso2",
        68,
        "Piso de dos dormitorios en Ofra",
        "Bloque de los 70, ascensor de los 80 y vecinos de toda la vida.",
        "6.º",
        "E",
    ),
    # Taco
    _l(
        "taco",
        "trastero",
        "trastero",
        6,
        "Trastero en Taco",
        "Lo más barato de toda la isla de Tenerife. Que no se entere el fondo.",
        "Sótano",
        "G",
    ),
    _l(
        "taco",
        "bajo",
        "bajo",
        32,
        "Bajo en Taco con patio",
        "Patio de 6 m² donde caben una lavadora, un bardino y un sueño.",
        "Bajo",
        "F",
    ),
    _l(
        "taco",
        "piso3",
        "piso3",
        88,
        "Piso de tres dormitorios en Taco",
        "Tres dormitorios por lo que cuesta un estudio en La Laguna. De momento.",
        "3.º",
        "E",
    ),
    # Puerto de la Cruz
    _l(
        "puertocruz",
        "estudio",
        "estudio",
        30,
        "Apartamento junto al Lago Martiánez",
        "Lo diseñó César Manrique. El apartamento, no: el lago.",
        "2.º",
        "E",
    ),
    _l(
        "puertocruz",
        "piso2",
        "piso2",
        65,
        "Piso en el casco del Puerto",
        "Vecinos ingleses desde 1890: el té de las cinco es obligatorio.",
        "1.º",
        "E",
    ),
    _l(
        "puertocruz",
        "atico",
        "atico",
        85,
        "Ático con vistas al Teide y al mar",
        "Las dos cosas a la vez, si la calima te deja.",
        "Ático",
        "D",
    ),
    # La Orotava
    _l(
        "orotava",
        "garaje",
        "garaje",
        12,
        "Plaza de garaje en la villa",
        "Las calles empinadas son de 1500. Tu coche, no.",
        "Sótano",
        "—",
    ),
    _l(
        "orotava",
        "piso3",
        "piso3",
        105,
        "Piso con vistas al valle",
        "El valle más bonito del mundo según Humboldt, que no tuvo que pagarlo.",
        "2.º",
        "D",
    ),
    _l(
        "orotava",
        "chalet",
        "chalet",
        240,
        "Casa de balcones con patio de tea",
        "No es la Casa de los Balcones de verdad. Pero en la foto, sí.",
        "Casa",
        "E",
    ),
    # El Médano
    _l(
        "medano",
        "estudio",
        "estudio",
        28,
        "Estudio de surfero",
        "Huele a neopreno. El viento entra gratis, incluso con la ventana cerrada.",
        "1.º",
        "E",
    ),
    _l(
        "medano",
        "piso2",
        "piso2",
        66,
        "Piso frente a la Montaña Roja",
        "Kitesurf en la puerta y el aeropuerto a diez minutos: los aviones te despiertan solos.",
        "2.º",
        "D",
    ),
    _l(
        "medano",
        "piso3",
        "piso3",
        90,
        "Piso familiar en El Médano",
        "Tres dormitorios y un garaje lleno de tablas que no son tuyas.",
        "1.º",
        "D",
    ),
    # Los Cristianos
    _l(
        "cristianos",
        "estudio",
        "estudio",
        32,
        "Apartamento junto al puerto del ferry",
        "Si no te gusta el piso, el ferry a La Gomera sale cada hora.",
        "3.º",
        "E",
    ),
    _l(
        "cristianos",
        "piso2",
        "piso2",
        62,
        "Piso con piscina comunitaria",
        "Piscina, socorrista y un british breakfast en cada esquina.",
        "1.º",
        "D",
    ),
    _l(
        "cristianos",
        "atico",
        "atico",
        100,
        "Ático con vistas a La Gomera",
        "Terraza de 50 m². La Gomera sale gratis en todas las fotos.",
        "Ático",
        "C",
    ),
    # Costa Adeje
    _l(
        "adeje",
        "garaje",
        "garaje",
        14,
        "Plaza de garaje en una urbanización de lujo",
        "Vigilada 24 horas. Más que el piso de quien te sirve las copas.",
        "Sótano",
        "—",
    ),
    _l(
        "adeje",
        "atico",
        "atico",
        120,
        "Ático con jacuzzi en Costa Adeje",
        "Jacuzzi, solárium y un vecino ruso que paga en efectivo.",
        "Ático",
        "B",
    ),
    _l(
        "adeje",
        "chalet",
        "chalet",
        320,
        "Villa con piscina infinita",
        "Piscina infinita, como la lista de espera para una vivienda pública en Adeje.",
        "Casa",
        "A",
    ),
)

LISTING_BY_KEY: dict[str, Listing] = {item.key: item for item in LISTINGS}


def listings_of(barrio: str) -> tuple[Listing, ...]:
    """Los inmuebles de un barrio, en el orden del catálogo."""
    return tuple(item for item in LISTINGS if item.barrio == barrio)


@dataclass(frozen=True, slots=True)
class RentLevel:
    """Lo que pide el dueño por el alquiler, frente al precio de referencia del barrio.

    Attributes:
        key: Clave estable.
        name: Nombre en la ficha.
        factor: Multiplica la renta de referencia.
        find: Probabilidad diaria de encontrar inquilino si está vacío.
        default: Probabilidad diaria de que el inquilino deje de pagar.
        months: Días (meses del juego) que dura el contrato.
    """

    key: str
    name: str
    emoji: str
    factor: float
    find: float
    default: float
    months: int


#: De la más barata a la más cara. El contrato de vivienda dura 5 años (art. 9 LAU),
#: que a 1 día = 1 mes son 60 días; el de temporada de los nómadas, unos meses (art. 3 LAU).
RENT_LEVELS: dict[str, RentLevel] = {
    r.key: r
    for r in (
        RentLevel("social", "Asequible", "🤝", 0.8, 0.9, 0.002, 60),
        RentLevel("referencia", "De referencia", "📊", 1.0, 0.6, 0.004, 60),
        RentLevel("mercado", "De mercado", "📈", 1.25, 0.35, 0.006, 60),
        RentLevel("nomadas", "Para nómadas digitales", "💻", 1.6, 0.15, 0.001, 9),
        # Vivienda vacacional: solo con licencia (`housing.VACATION_LEVEL`). Cada «mes» es
        # una estancia corta, se cobra por adelantado (no hay impago) y la ocupación
        # depende de la temporada (`housing.season_factor`).
        RentLevel("vacacional", "Vacacional (con licencia)", "🏖️", 2.0, 0.6, 0.0, 1),
    )
}
DEFAULT_RENT_LEVEL = "referencia"


@dataclass(frozen=True, slots=True)
class Tenant:
    """Un inquilino ficticio: quién vive en tu piso.

    Attributes:
        key: Clave estable.
        name: Quién es.
        levels: Rentas que acepta (claves de `RENT_LEVELS`).
        line: Lo que dice al firmar.
    """

    key: str
    name: str
    emoji: str
    levels: tuple[str, ...]
    line: str


TENANTS: tuple[Tenant, ...] = (
    Tenant(
        "enfermeros",
        "Pareja de enfermeros",
        "🩺",
        ("social", "referencia", "mercado"),
        "Pagamos el 60 % del sueldo, pero por lo menos no vivimos con mis suegros.",
    ),
    Tenant(
        "familia",
        "Familia con dos niños",
        "👨‍👩‍👧‍👦",
        ("social", "referencia"),
        "¿El colegio está cerca? Da igual, firmamos ya antes de que lo coja otro.",
    ),
    Tenant(
        "opositor",
        "Opositor a notarías",
        "📚",
        ("social", "referencia"),
        "Pagan mis padres. Llevo siete años estudiando, el octavo es el bueno.",
    ),
    Tenant(
        "rider",
        "Rider con contrato",
        "🛵",
        ("social", "referencia"),
        "Con la Ley Rider ya tengo nómina. Bueno, media nómina.",
    ),
    Tenant(
        "profesora",
        "Profesora interina",
        "🍎",
        ("social", "referencia", "mercado"),
        "Este año me tocó esta isla. El que viene, ya veremos.",
    ),
    Tenant(
        "ingles",
        "Jubilado inglés",
        "🫖",
        ("referencia", "mercado"),
        "Lovely. I'll pay six months in advance, darling.",
    ),
    Tenant(
        "medico",
        "Médica residente",
        "🥼",
        ("referencia", "mercado"),
        "Con las guardias casi ni piso la casa, así que me vale cualquier cosa.",
    ),
    Tenant(
        "funcionario",
        "Funcionario del Cabildo",
        "🗂️",
        ("referencia", "mercado"),
        "Plaza fija, nómina fija y siesta fija. El inquilino perfecto.",
    ),
    Tenant(
        "aleman",
        "Teletrabajador alemán",
        "💻",
        ("mercado", "nomadas"),
        "In Berlin I pay double. Ist sehr billig! ¿Tiene fibra?",
    ),
    Tenant(
        "influencer",
        "Influencer de viajes",
        "🤳",
        ("nomadas",),
        "¿Me haces precio si te etiqueto en las stories?",
    ),
    Tenant(
        "erasmus",
        "Erasmus italiano",
        "🍕",
        ("mercado", "nomadas"),
        "Mamma mia, che sole. Pagan mis padres, tranquilo.",
    ),
    Tenant(
        "cripto",
        "Nómada cripto",
        "🪙",
        ("nomadas",),
        "¿Aceptas el pago en mi moneda? Mañana vale el doble, seguro.",
    ),
    Tenant(
        "britanicos",
        "Familia británica de vacaciones",
        "☀️",
        ("vacacional",),
        "Lovely! Where is the full English breakfast?",
    ),
    Tenant(
        "despedida",
        "Despedida de soltero alemana",
        "🍻",
        ("vacacional",),
        "Prost! ¿El balcón aguanta a ocho?",
    ),
    Tenant(
        "nordicos",
        "Pareja nórdica jubilada",
        "🧓",
        ("vacacional",),
        "Venimos de noviembre a marzo desde 1998. Nos quedamos con el piso.",
    ),
    Tenant(
        "surferos",
        "Grupo de surferos",
        "🏄",
        ("vacacional",),
        "¿Hay sitio para seis tablas en el salón?",
    ),
)

TENANT_BY_KEY: dict[str, Tenant] = {t.key: t for t in TENANTS}


@dataclass(frozen=True, slots=True)
class VpoUnit:
    """Una vivienda de protección oficial: se sortea, se compra a precio tasado y no se alquila.

    Attributes:
        key: Clave estable.
        name: Nombre de la promoción.
        town: Municipio.
        island: Clave de `ISLANDS`.
        m2: Metros.
        price: Precio máximo legal, en Y$ (no sigue al índice de ningún barrio).
        community: Comunidad en euros al mes.
        blurb: La letra pequeña.
    """

    key: str
    name: str
    town: str
    island: str
    m2: int
    price: int
    community: int
    blurb: str


#: Las VPO del servidor: una de cada. El precio está pensado para que la cuota de la
#: hipoteca (con el aval del Estado, al 100 %) quepa en el 35 % del sueldo de un celador.
VPO_UNITS: tuple[VpoUnit, ...] = (
    VpoUnit("rehoyas", "VPO en Las Rehoyas", "Las Palmas de Gran Canaria", "gc", 52, 420_000, 30,
            "Promoción pública de 1970 rehabilitada en 2019. El ascensor llegó en 2023."),
    VpoUnit("jinamar", "VPO en Jinámar", "Telde", "gc", 60, 450_000, 30,
            "El polígono más grande de Canarias. Vistas al mar y a la autopista."),
    VpoUnit("tamaraceite", "VPO en Tamaraceite", "Las Palmas de Gran Canaria", "gc", 65,
            480_000, 35,
            "Obra nueva del Cabildo. Entregada con seis años de retraso, como manda la tradición."),
    VpoUnit("anaza", "VPO en Añaza", "Santa Cruz de Tenerife", "tf", 55, 430_000, 30,
            "Barrio de los 90 junto al mar. La guagua pasa cuando le parece."),
    VpoUnit("gallega", "VPO en La Gallega", "Santa Cruz de Tenerife", "tf", 58, 440_000, 30,
            "Bloques de colores y una de las mejores vistas de Santa Cruz que nadie va a ver."),
    VpoUnit("cardonal", "VPO en El Cardonal", "San Cristóbal de La Laguna", "tf", 62, 460_000, 35,
            "Junto a la universidad: tu vecino es un opositor y el de enfrente, otro."),
)  # fmt: skip

VPO_BY_KEY: dict[str, VpoUnit] = {unit.key: unit for unit in VPO_UNITS}


# -- Obra nueva (fase 5) -------------------------------------------------------------------
#
# Cada lunes, quien está en la rama Ladrillo de `pala` levanta una promoción en un barrio
# que aún no tiene (`housing.new_promotion`). Los nombres, la letra pequeña y las plantas
# salen de aquí; el precio, de los metros y del barrio, como cualquier otro inmueble.

#: Nombres de las promociones, con la sorna de los folletos de la burbuja.
PROMOTION_NAMES: tuple[str, ...] = (
    "Residencial Brisas del Atlántico",
    "Residencial Mirador del Teide",
    "Torre Burbuja 2007",
    "Edificio Pelotazo",
    "Jardines de Moncloa",
    "Residencial Recalificado",
    "Las Terrazas del Falcon",
    "Marina Blackstone",
    "Villa Manual de Resistencia",
    "Mirador de Galapagar",
    "Bahía Next Generation",
    "Lomo Especulativo",
    "Atalaya del IBEX",
    "Costa Sobres de Génova",
    "Residencial Ladrillazo",
    "Edificio Vaso es un Vaso",
    "Las Dunas del Pelotazo",
    "Residencial Puerta Giratoria",
    "Altos de la Motosierra",
    "Residencial Atado y Bien Atado",
    "Torre Mesa Larguísima",
    "Residencial Make Canarias Great",
    "Edificio Waterloo",
    "Residencial Gofio Premium",
)

#: La letra pequeña de cada promoción.
PROMOTION_BLURBS: tuple[str, ...] = (
    "Sobre plano. Entrega prevista: cuando el Cabildo dé la licencia.",
    "Piscina comunitaria con vistas al mar (en el render).",
    "Domótica: la persiana sube con el móvil y el precio con el Euríbor.",
    "Zonas verdes de césped artificial y certificado A de la propia promotora.",
    "Llave en mano en 2029, en 2031 como muy tarde, palabra de promotor.",
    "Reserva con 6.000 € y el resto, a treinta años y un aval de tus padres.",
    "Vistas al mar garantizadas hasta que construyan delante.",
    "Recalificado en un pleno de madrugada. Todo legal, de momento.",
    "Gimnasio, coworking y conserje. El conserje es un holograma.",
    "Firmado por un arquitecto estrella que no ha pisado Canarias.",
    "Calidades premium: el folleto es de papel satinado.",
    "Últimas unidades desde el primer día. Siempre son las últimas.",
)

#: Plantas de la obra nueva por tipo de inmueble.
PROMOTION_FLOORS: dict[str, tuple[str, ...]] = {
    "piso2": ("1.º con ascensor", "3.º con ascensor", "5.º con ascensor"),
    "piso3": ("2.º con ascensor", "4.º con ascensor", "Bajo con jardín"),
    "atico": ("Ático con solárium", "Ático dúplex"),
}
