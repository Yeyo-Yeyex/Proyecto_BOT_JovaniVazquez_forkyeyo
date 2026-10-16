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

#: El carácter de cada barrio: decide cómo se pinta (cielo, suelo, fachadas, lo que hay
#: delante) y qué tipos de inmueble abundan en él.
THEMES: dict[str, str] = {
    "marinero": "Marinero",
    "historico": "Histórico",
    "centro": "Céntrico",
    "obrero": "Obrero",
    "playa": "De playa",
    "lujo": "De lujo",
    "rural": "Rural",
}

#: Cuánto buscan los nómadas digitales en cada tipo de barrio: multiplica su `find`. Quieren
#: playa, fibra y cafetería con leche de avena; en Taco o en una finca de Arucas, pocos.
NOMAD_DEMAND: dict[str, float] = {
    "playa": 1.0,
    "centro": 1.0,
    "historico": 0.8,
    "marinero": 0.8,
    "lujo": 0.6,
    "obrero": 0.5,
    "rural": 0.4,
}


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
        # Los de la ampliación (7.22): cada barrio tiene los suyos.
        Kind("local", "Local comercial", 0.75, 1.05, None, 40),
        Kind("nave", "Nave industrial", 0.45, 0.6, None, 60),
        # El amarre se mide por su lámina de agua (eslora × manga).
        Kind("amarre", "Amarre", 0.3, 0.4, None, 30),
        Kind("terrera", "Casa terrera", 0.95, 0.95, None, 20),
        Kind("cueva", "Casa cueva", 0.7, 1.0, None, 10),
        Kind("finca", "Finca rústica", 0.6, 0.6, None, 40),
        Kind("casona", "Casa señorial", 1.2, 0.8, None, 90),
        Kind("bungalow", "Bungalow", 0.9, 1.2, None, 60),
        Kind("duplex", "Dúplex", 1.1, 1.0, None, 90),
        Kind("loft", "Loft", 1.05, 1.2, None, 50),
    )
}

#: Los que no son vivienda ni trastero: los alquilan negocios, no personas
#: (`Tenant.business`).
BUSINESS_KINDS = frozenset({"local", "nave", "amarre"})


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
        theme: Clave de `THEMES`.
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
    theme: str


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
        "marinero",
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
        "playa",
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
        "obrero",
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
        "centro",
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
        "historico",
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
        "rural",
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
        "obrero",
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
        "rural",
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
        "playa",
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
        "lujo",
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
        "marinero",
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
        "historico",
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
        "centro",
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
        "obrero",
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
        "obrero",
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
        "playa",
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
        "historico",
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
        "playa",
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
        "playa",
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
        "lujo",
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
    _l(
        "isleta",
        "amarre",
        "amarre",
        30,
        "Amarre en el puerto pesquero",
        "El barco no va incluido. La concesión de Puertos del Estado caduca en 2031.",
        "Pantalán B",
        "—",
    ),
    _l(
        "isleta",
        "terrera",
        "terrera",
        70,
        "Casa terrera de pescadores",
        "Fachada azul, puerta verde y una azotea a la que un fondo ya le ha echado el ojo.",
        "Casa",
        "F",
    ),
    _l(
        "isleta",
        "local",
        "local",
        45,
        "Local de antigua pescadería",
        "Ideal para un brunch de aguacate a 14 €. El barrio todavía no lo sabe.",
        "Bajo",
        "F",
    ),
    _l(
        "isleta",
        "cuarto_redes",
        "trastero",
        6,
        "Cuarto de redes en la azotea",
        "Las redes del abuelo se quedan: el Ayuntamiento lo llama patrimonio etnográfico.",
        "Azotea",
        "G",
    ),
    _l(
        "isleta",
        "estudio",
        "estudio",
        30,
        "Estudio junto a La Puntilla",
        "Del Confital a la cama en tres minutos. De la cama al Euríbor, en uno.",
        "1.º",
        "E",
    ),
    _l(
        "isleta",
        "garaje",
        "garaje",
        12,
        "Plaza de garaje en La Isleta",
        "Aparcar en La Isleta es más difícil que pescar un mero. Esta plaza es el mero.",
        "Sótano",
        "—",
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
    _l(
        "canteras",
        "loft",
        "loft",
        70,
        "Loft en una antigua fábrica de Guanarteme",
        "Antes se hacía tabaco. Ahora se hace coworking, que echa más humo.",
        "1.º",
        "D",
    ),
    _l(
        "canteras",
        "local",
        "local",
        50,
        "Local en el paseo de Las Canteras",
        "Para tu heladería artesanal, la número catorce del paseo.",
        "Bajo",
        "E",
    ),
    _l(
        "canteras",
        "duplex",
        "duplex",
        110,
        "Dúplex con vistas a la playa",
        "Dos plantas: en la de abajo vives tú y en la de arriba, el banco.",
        "6.º y 7.º",
        "C",
    ),
    _l(
        "canteras",
        "garaje",
        "garaje",
        11,
        "Plaza de garaje a cien metros de la playa",
        "Vale más que el coche que aparcas y que el sueldo con que lo pagas.",
        "Sótano -1",
        "—",
    ),
    _l(
        "canteras",
        "piso3",
        "piso3",
        95,
        "Piso familiar en la calle Sagasta",
        "Tres dormitorios y un tendedero con vistas al mar que el anuncio llama «terraza».",
        "4.º",
        "D",
    ),
    _l(
        "canteras",
        "trastero",
        "trastero",
        4,
        "Trastero para tablas de surf",
        "Cabe una tabla y medio sueño de vivir de ella.",
        "Sótano",
        "G",
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
    _l(
        "schamann",
        "piso3",
        "piso3",
        82,
        "Piso de tres dormitorios con vistas a la ciudad",
        "Vistas a toda Las Palmas. Las escaleras, también: 98 escalones.",
        "5.º sin ascensor",
        "F",
    ),
    _l(
        "schamann",
        "local",
        "local",
        40,
        "Local de antiguo bazar",
        "Lo vendía todo a un euro. Ahora el alquiler sube un euro al día.",
        "Bajo",
        "G",
    ),
    _l(
        "schamann",
        "garaje",
        "garaje",
        10,
        "Plaza de garaje en cuesta",
        "Hasta el garaje está en cuesta. El freno de mano es tu mejor inquilino.",
        "Sótano",
        "—",
    ),
    _l(
        "schamann",
        "estudio",
        "estudio",
        25,
        "Estudio «reformado»",
        "Pintura blanca y una foto con gran angular. El anuncio lo llama loft.",
        "3.º",
        "F",
    ),
    _l(
        "schamann",
        "vpo_vieja",
        "piso2",
        58,
        "Antigua VPO descalificada",
        "Fue protección oficial en 1975. Ahora es VPO: Vivienda de Precio Obsceno.",
        "2.º",
        "E",
    ),
    _l(
        "schamann",
        "terrera",
        "terrera",
        65,
        "Casa terrera con azotea",
        "Azotea con vistas a La Isleta, bombona de butano y una escritura de 1958.",
        "Casa",
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
    _l(
        "triana",
        "local",
        "local",
        80,
        "Local en la calle Mayor de Triana",
        "Cierran las tiendas de toda la vida para abrir franquicias de toda la vida.",
        "Bajo",
        "D",
    ),
    _l(
        "triana",
        "loft",
        "loft",
        75,
        "Loft sobre una antigua tienda de tejidos",
        "Techos de cinco metros y una escalera de caracol que no pasó ninguna inspección.",
        "1.º",
        "D",
    ),
    _l(
        "triana",
        "estudio",
        "estudio",
        30,
        "Estudio junto al Gabinete Literario",
        "Rodeado de cultura. La cultura no paga el alquiler.",
        "2.º",
        "E",
    ),
    _l(
        "triana",
        "piso2",
        "piso2",
        70,
        "Piso modernista con miradores",
        "Fachada protegida. La cocina de 1987, también: la protege el casero.",
        "3.º",
        "E",
    ),
    _l(
        "triana",
        "duplex",
        "duplex",
        120,
        "Dúplex con azotea frente a San Telmo",
        "La estación de guaguas abajo: te despierta la primera Global de la mañana.",
        "Ático dúplex",
        "C",
    ),
    _l(
        "triana",
        "edificio",
        "edificio",
        500,
        "Edificio modernista de 6 viviendas",
        "Seis viviendas, un local y una SOCIMI que ya ha mandado tres cartas.",
        "Bajo a 3.º",
        "E",
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
    _l(
        "vegueta",
        "casona",
        "casona",
        300,
        "Casa señorial junto a la Casa de Colón",
        "Patio canario, escudo nobiliario y goteras con certificado de antigüedad.",
        "Casa",
        "D",
    ),
    _l(
        "vegueta",
        "terrera",
        "terrera",
        90,
        "Casa terrera en la calle de los Balcones",
        "Bien protegido: para cambiar una teja hace falta un informe arqueológico.",
        "Casa",
        "F",
    ),
    _l(
        "vegueta",
        "local",
        "local",
        55,
        "Local con bóveda de piedra",
        "Perfecto para un bar de vino natural. Ya hay once en la calle.",
        "Bajo",
        "F",
    ),
    _l(
        "vegueta",
        "estudio",
        "estudio",
        32,
        "Estudio en una casona dividida",
        "Antes era la despensa de un marqués. Ahora la despensa eres tú.",
        "1.º",
        "E",
    ),
    _l(
        "vegueta",
        "piso3",
        "piso3",
        110,
        "Piso con patio interior en el casco",
        "Tres dormitorios, un patio con helechos y un turista con mapa cada diez minutos.",
        "1.º",
        "E",
    ),
    _l(
        "vegueta",
        "garaje",
        "garaje",
        12,
        "Plaza de garaje en pleno casco antiguo",
        "El único hueco libre de Vegueta. El Cabildo lo vigila como a un yacimiento.",
        "Sótano",
        "—",
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
    _l(
        "tafira",
        "cueva",
        "cueva",
        80,
        "Casa cueva junto a la Caldera de Bandama",
        "Certificado A sin una placa solar: los antiguos canarios ya sabían de eficiencia.",
        "Cueva",
        "A",
    ),
    _l(
        "tafira",
        "finca",
        "finca",
        350,
        "Finca con viñedo del Monte Lentiscal",
        "Vino del Monte, lagar de piedra y una bodega que el banco tasa como trastero.",
        "Casa",
        "E",
    ),
    _l(
        "tafira",
        "terrera",
        "terrera",
        120,
        "Casa terrera con huerta",
        "Papas, coles y un pozo seco desde que llegó el campo de golf.",
        "Casa",
        "E",
    ),
    _l(
        "tafira",
        "duplex",
        "duplex",
        140,
        "Dúplex junto al campus",
        "A dos minutos de la universidad. Los estudiantes, a dos vidas de poder pagarlo.",
        "1.º y 2.º",
        "D",
    ),
    _l(
        "tafira",
        "bajo",
        "bajo",
        60,
        "Bajo con jardín en Tafira Alta",
        "Jardín privado, buganvillas y el silencio de quien llega a todo en coche.",
        "Bajo",
        "D",
    ),
    _l(
        "tafira",
        "chalet_suelo",
        "chalet",
        300,
        "Chalet en suelo rústico",
        "Construido en los 90, legalizado en los 2000 y en un juzgado desde los 2010.",
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
    _l(
        "telde",
        "nave",
        "nave",
        400,
        "Nave en el polígono de Salinetas",
        "Para tu tienda online de móviles de Temu o para el barco que nunca vas a sacar.",
        "Nave",
        "G",
    ),
    _l(
        "telde",
        "cueva",
        "cueva",
        60,
        "Cueva cerca de Cuatro Puertas",
        "Yacimiento aborigen a cien metros: los primeros inquilinos no pagaban IBI.",
        "Cueva",
        "A",
    ),
    _l(
        "telde",
        "local",
        "local",
        60,
        "Local en la zona comercial de San Gregorio",
        "Zona comercial abierta: abierta a que cierres en seis meses.",
        "Bajo",
        "E",
    ),
    _l(
        "telde",
        "terrera",
        "terrera",
        85,
        "Casa terrera en el barrio de San Juan",
        "Barrio de San Juan, siglo XV. La fontanería, del XVI.",
        "Casa",
        "F",
    ),
    _l(
        "telde",
        "trastero",
        "trastero",
        7,
        "Trastero en San Gregorio",
        "Guarda la bici, la tabla y la esperanza de comprar un piso algún día.",
        "Sótano",
        "G",
    ),
    _l(
        "telde",
        "garaje",
        "garaje",
        11,
        "Plaza de garaje junto a la GC-1",
        "A cinco minutos de la autopista: lo que tardas en llegar al atasco.",
        "Sótano",
        "—",
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
    _l(
        "arucas",
        "finca",
        "finca",
        400,
        "Finca de plataneras con casa",
        "Media hectárea de plátanos. La subvención de Bruselas da más que los plátanos.",
        "Casa",
        "F",
    ),
    _l(
        "arucas",
        "nave",
        "nave",
        350,
        "Almacén de empaquetado de plátanos",
        "Cuarenta años empaquetando plátanos. Ahora, sueños de loft para un fondo luxemburgués.",
        "Nave",
        "G",
    ),
    _l(
        "arucas",
        "terrera",
        "terrera",
        95,
        "Casa terrera de cantería azul",
        "Piedra azul de Arucas, la misma de la iglesia. El precio también es de iglesia.",
        "Casa",
        "E",
    ),
    _l(
        "arucas",
        "cueva",
        "cueva",
        55,
        "Cueva en la Montaña de Arucas",
        "Vistas a toda la costa norte, 21 °C todo el año y sin cobertura, que es un lujo.",
        "Cueva",
        "A",
    ),
    _l(
        "arucas",
        "casona",
        "casona",
        260,
        "Casona junto a la fábrica del ron",
        "Huele a ron añejo día y noche. El tasador subió el precio después de la visita.",
        "Casa",
        "D",
    ),
    _l(
        "arucas",
        "piso3",
        "piso3",
        92,
        "Piso de tres dormitorios en el casco",
        "El último casco del norte donde aún se paga una hipoteca con un sueldo.",
        "3.º",
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
    _l(
        "maspalomas",
        "bungalow",
        "bungalow",
        50,
        "Bungalow en el Campo Internacional",
        "Tumbona incluida. La toalla que te la reserva desde las siete, también.",
        "Bajo",
        "E",
    ),
    _l(
        "maspalomas",
        "local",
        "local",
        40,
        "Local en un centro comercial de los 80",
        "La mitad de los locales, cerrados. Los que quedan venden imanes de nevera.",
        "Bajo",
        "F",
    ),
    _l(
        "maspalomas",
        "atico",
        "atico",
        100,
        "Ático con vistas al faro",
        "El faro sale en la foto. El edificio de enfrente, que tapa el resto, no.",
        "Ático",
        "B",
    ),
    _l(
        "maspalomas",
        "duplex",
        "duplex",
        120,
        "Dúplex en Meloneras",
        "Golf, hotel de cinco estrellas y un portero que te pregunta si eres del personal.",
        "Bajo y 1.º",
        "B",
    ),
    _l(
        "maspalomas",
        "garaje",
        "garaje",
        12,
        "Plaza de garaje en Playa del Inglés",
        "En temporada alta se alquila por horas. Como todo lo demás.",
        "Sótano",
        "—",
    ),
    _l(
        "maspalomas",
        "complejo",
        "edificio",
        450,
        "Complejo de bungalows entero",
        "Diez bungalows, una piscina y la recepción de 1979. Cada año llama un fondo.",
        "Bajo y 1.º",
        "E",
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
    _l(
        "mogan",
        "amarre",
        "amarre",
        35,
        "Amarre en el puerto de Mogán",
        "Ocho años de lista de espera. Sale porque el dueño se ha comprado uno más grande.",
        "Pantalán A",
        "—",
    ),
    _l(
        "mogan",
        "amarre_vip",
        "amarre",
        150,
        "Amarre para megayate",
        "Para un yate de 25 metros. El tuyo es un kayak, pero el amarre impresiona igual.",
        "Pantalán VIP",
        "—",
    ),
    _l(
        "mogan",
        "duplex",
        "duplex",
        150,
        "Dúplex en las casitas del puerto",
        "Un vecino que viene diez días al año. Los otros 355, la persiana.",
        "Bajo y 1.º",
        "C",
    ),
    _l(
        "mogan",
        "bungalow",
        "bungalow",
        70,
        "Bungalow en Taurito",
        "Taurito: el lugar del mundo con más toallas por metro cuadrado.",
        "Bajo",
        "C",
    ),
    _l(
        "mogan",
        "local",
        "local",
        50,
        "Local en el paseo del puerto",
        "Para tu restaurante de pescado congelado con vistas al pescado fresco.",
        "Bajo",
        "D",
    ),
    _l(
        "mogan",
        "villa",
        "chalet",
        400,
        "Villa en la ladera de Mogán",
        "Ascensor panorámico, piscina en tres alturas y una licencia que nadie encuentra.",
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
    _l(
        "sanandres",
        "amarre",
        "amarre",
        30,
        "Amarre en el muelle de San Andrés",
        "Para la barquilla del domingo. El domingo que no llueve.",
        "Muelle",
        "—",
    ),
    _l(
        "sanandres",
        "terrera",
        "terrera",
        75,
        "Casa terrera del barrio pesquero",
        "Puerta a la calle, azotea al mar y pescado fresco si caes bien en el muelle.",
        "Casa",
        "F",
    ),
    _l(
        "sanandres",
        "local",
        "local",
        45,
        "Local para restaurante de pescado",
        "Tres restaurantes de pescado por cada vecino. Hay sitio para el cuarto.",
        "Bajo",
        "F",
    ),
    _l(
        "sanandres",
        "trastero",
        "trastero",
        6,
        "Cuarto de aperos de pesca",
        "Nasas, redes y un olor que se queda en la escritura.",
        "Bajo",
        "G",
    ),
    _l(
        "sanandres",
        "piso3",
        "piso3",
        85,
        "Piso familiar frente al castillo",
        "El castillo se cayó en 1898. El piso aguanta, que es lo que importa.",
        "2.º",
        "E",
    ),
    _l(
        "sanandres",
        "estudio",
        "estudio",
        28,
        "Estudio cerca de Las Teresitas",
        "Cinco minutos andando a la playa. Veinte buscando aparcamiento.",
        "1.º",
        "E",
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
    _l(
        "laguna",
        "casona",
        "casona",
        280,
        "Casa señorial en la calle San Agustín",
        "Escudo de armas, patio de columnas y una humedad que también es patrimonio.",
        "Casa",
        "E",
    ),
    _l(
        "laguna",
        "terrera",
        "terrera",
        80,
        "Casa terrera junto a la Catedral",
        "La Catedral tardó un siglo en repararse. Tu tejado va por el mismo camino.",
        "Casa",
        "F",
    ),
    _l(
        "laguna",
        "local",
        "local",
        50,
        "Local en la calle Herradores",
        "Peatonal, céntrica y con tres franquicias de café por manzana.",
        "Bajo",
        "E",
    ),
    _l(
        "laguna",
        "garaje",
        "garaje",
        12,
        "Plaza de garaje a las afueras del casco",
        "Al casco no se entra en coche: veinte minutos andando bajo la lluvia.",
        "Sótano",
        "—",
    ),
    _l(
        "laguna",
        "residencia",
        "edificio",
        520,
        "Edificio de pisos de estudiantes",
        "Doce habitaciones, doce estudiantes y una nevera con doce nombres escritos.",
        "Bajo a 3.º",
        "E",
    ),
    _l(
        "laguna",
        "duplex",
        "duplex",
        110,
        "Dúplex de obra nueva en La Cuesta",
        "Empezado en 2008 y entregado en 2015 por la crisis. La crisis venía incluida.",
        "3.º y 4.º",
        "C",
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
    _l(
        "santacruz",
        "local",
        "local",
        70,
        "Local en la calle Castillo",
        "Cada local de la calle es una tienda de recuerdos o una inmobiliaria. Elige.",
        "Bajo",
        "D",
    ),
    _l(
        "santacruz",
        "loft",
        "loft",
        80,
        "Loft junto al Auditorio",
        "Vistas al Auditorio de Calatrava, que se cae a trocitos en cuotas cómodas.",
        "2.º",
        "C",
    ),
    _l(
        "santacruz",
        "atico",
        "atico",
        95,
        "Ático con vistas al puerto",
        "Terraza con vistas a los cruceros. Los cruceristas, con vistas a tu terraza.",
        "Ático",
        "C",
    ),
    _l(
        "santacruz",
        "estudio",
        "estudio",
        30,
        "Estudio junto a la Rambla",
        "Encima de una oficina que cierra a las tres y de un bar que abre a las tres.",
        "1.º",
        "E",
    ),
    _l(
        "santacruz",
        "toscal",
        "piso2",
        70,
        "Piso en El Toscal",
        "Casas de colores, calles estrechas y un fondo comprándolas de una en una.",
        "3.º sin ascensor",
        "F",
    ),
    _l(
        "santacruz",
        "duplex",
        "duplex",
        130,
        "Dúplex en las Ramblas",
        "Ficus centenarios en la puerta: sombra, raíces en el garaje y cotorras de regalo.",
        "5.º y 6.º",
        "C",
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
    _l(
        "ofra",
        "piso3",
        "piso3",
        85,
        "Piso de tres dormitorios en un bloque de Ofra",
        "Ocho plantas, un ascensor y una junta que lleva desde 1994 votando cambiarlo.",
        "8.º",
        "E",
    ),
    _l(
        "ofra",
        "local",
        "local",
        45,
        "Local junto al centro de salud",
        "Farmacia, bar o casa de apuestas: las tres funcionan, por desgracia en ese orden.",
        "Bajo",
        "F",
    ),
    _l(
        "ofra",
        "garaje",
        "garaje",
        10,
        "Plaza de garaje en Ofra",
        "Plaza amplia. El coche del vecino ya lo ha comprobado.",
        "Sótano",
        "—",
    ),
    _l(
        "ofra",
        "estudio",
        "estudio",
        26,
        "Estudio para residentes del hospital",
        "A cinco minutos de la Candelaria. Las guardias, a precio de 2009.",
        "2.º",
        "E",
    ),
    _l(
        "ofra",
        "nave",
        "nave",
        300,
        "Nave en el polígono El Mayorazgo",
        "Polígono de toda la vida: talleres, almacenes y, desde hace poco, un crossfit.",
        "Nave",
        "G",
    ),
    _l(
        "ofra",
        "piso_fondo",
        "piso2",
        62,
        "Piso reformado por un fondo",
        "Dos semanas de obra: vinilo gris, cuadro de Ikea y renta un 40 % más alta.",
        "4.º",
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
    _l(
        "taco",
        "piso2",
        "piso2",
        60,
        "Piso de dos dormitorios en Taco",
        "El piso más barato del área metropolitana. Ya hay cola de fondos en el portal.",
        "3.º",
        "E",
    ),
    _l(
        "taco",
        "local",
        "local",
        40,
        "Local con la persiana bajada desde 2012",
        "El cartel de «Se alquila» ya tiene su propia antigüedad.",
        "Bajo",
        "G",
    ),
    _l(
        "taco",
        "nave",
        "nave",
        350,
        "Nave en el polígono de Los Majuelos",
        "Taller, almacén o centro logístico de una multinacional que tributa en Irlanda.",
        "Nave",
        "G",
    ),
    _l(
        "taco",
        "terrera",
        "terrera",
        70,
        "Casa terrera con las varillas al aire",
        "La segunda planta espera desde 1991, por si cambia el plan general.",
        "Casa",
        "G",
    ),
    _l(
        "taco",
        "garaje",
        "garaje",
        10,
        "Plaza de garaje en Taco",
        "Se paga casi sola alquilándola a quien trabaja en el polígono.",
        "Sótano",
        "—",
    ),
    _l(
        "taco",
        "estudio",
        "estudio",
        25,
        "Estudio en un bajo dividido",
        "Antes era un piso; ahora, tres estudios. La magia financiera de un tabique.",
        "Bajo",
        "F",
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
    _l(
        "puertocruz",
        "casona",
        "casona",
        240,
        "Casona canaria junto a la plaza del Charco",
        "Hotelito del siglo XIX convertido en casona. O al revés, según quién tase.",
        "Casa",
        "E",
    ),
    _l(
        "puertocruz",
        "bungalow",
        "bungalow",
        55,
        "Bungalow en un complejo de La Paz",
        "Piscina con trampolín de cuando eran legales y el Teide entre dos hoteles.",
        "Bajo",
        "E",
    ),
    _l(
        "puertocruz",
        "local",
        "local",
        45,
        "Local en la calle de San Telmo",
        "Paseo marítimo, mucho turista y ningún vecino que compre el pan.",
        "Bajo",
        "E",
    ),
    _l(
        "puertocruz",
        "terrera",
        "terrera",
        80,
        "Casa de pescadores en La Ranilla",
        "Casas de pescadores, murales en las paredes y un hostal boutique en cada esquina.",
        "Casa",
        "F",
    ),
    _l(
        "puertocruz",
        "duplex",
        "duplex",
        100,
        "Dúplex con vistas al Lago Martiánez",
        "Vistas a los lagos de Manrique y al hotel de 1971 que tapa la mitad.",
        "4.º y 5.º",
        "D",
    ),
    _l(
        "puertocruz",
        "garaje",
        "garaje",
        11,
        "Plaza de garaje en el centro",
        "Aparcar en el Puerto en verano es deporte olímpico. Esta plaza es la medalla.",
        "Sótano",
        "—",
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
    _l(
        "orotava",
        "casona",
        "casona",
        320,
        "Casona con patio de tea",
        "Alfombras de flores en el Corpus una semana al año y turistas el resto.",
        "Casa",
        "E",
    ),
    _l(
        "orotava",
        "finca",
        "finca",
        400,
        "Finca de viñedos en el valle",
        "Viña, lagar y bodega. La denominación de origen paga poco; el vino es tuyo.",
        "Casa",
        "E",
    ),
    _l(
        "orotava",
        "terrera",
        "terrera",
        85,
        "Casa terrera en la Villa de Abajo",
        "Calles empedradas de 1500 y una lavadora que llegó a la puerta en grúa.",
        "Casa",
        "F",
    ),
    _l(
        "orotava",
        "local",
        "local",
        45,
        "Local junto al Ayuntamiento",
        "A un paso del Ayuntamiento: ideal para una gestoría que sepa a quién llamar.",
        "Bajo",
        "F",
    ),
    _l(
        "orotava",
        "piso2",
        "piso2",
        70,
        "Piso con vistas al Teide",
        "El Teide desde la cocina. Desde el salón, el edificio que nunca debió construirse.",
        "3.º",
        "E",
    ),
    _l(
        "orotava",
        "bodeguita",
        "trastero",
        6,
        "Bodeguita en la planta baja",
        "Huele a vino de garrafa: el dueño anterior lo vendía por la ventana.",
        "Sótano",
        "G",
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
    _l(
        "medano",
        "bungalow",
        "bungalow",
        50,
        "Bungalow junto a La Tejita",
        "Al lado, un hotel a medio hacer que lleva veinte años en los juzgados.",
        "Bajo",
        "D",
    ),
    _l(
        "medano",
        "loft",
        "loft",
        65,
        "Loft de kitesurfista",
        "Taller de tablas abajo, cama arriba y el neopreno colgado en la ducha.",
        "1.º",
        "D",
    ),
    _l(
        "medano",
        "local",
        "local",
        45,
        "Local de escuela de surf",
        "La quinta escuela de surf de la calle. El viento da para todas.",
        "Bajo",
        "E",
    ),
    _l(
        "medano",
        "atico",
        "atico",
        85,
        "Ático sobre la plaza",
        "Terraza sobre la plaza y los aviones del Sur saludando cada tres minutos.",
        "Ático",
        "C",
    ),
    _l(
        "medano",
        "garaje",
        "garaje",
        12,
        "Plaza para furgoneta camperizada",
        "Para la furgoneta que sale en todas las fotos de Instagram de El Médano.",
        "Sótano",
        "—",
    ),
    _l(
        "medano",
        "trastero",
        "trastero",
        5,
        "Trastero para tablas de kite",
        "Huele a neopreno mojado. El precio, a burbuja.",
        "Sótano",
        "G",
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
    _l(
        "cristianos",
        "bungalow",
        "bungalow",
        55,
        "Bungalow en un complejo con piscina",
        "Animación a las once y un karaoke que acaba siempre con la misma canción.",
        "Bajo",
        "D",
    ),
    _l(
        "cristianos",
        "local",
        "local",
        50,
        "Local en el paseo marítimo",
        "Uno más para alquilar hamacas o excursiones a ver ballenas que no siempre salen.",
        "Bajo",
        "E",
    ),
    _l(
        "cristianos",
        "amarre",
        "amarre",
        35,
        "Amarre en el puerto de Los Cristianos",
        "Entre el ferry y los barcos de ver ballenas: aquí no para de moverse nada.",
        "Pantalán",
        "—",
    ),
    _l(
        "cristianos",
        "duplex",
        "duplex",
        110,
        "Dúplex en la zona alta",
        "Vistas al mar por encima de doce edificios que hace veinte años no estaban.",
        "2.º y 3.º",
        "D",
    ),
    _l(
        "cristianos",
        "piso3",
        "piso3",
        90,
        "Piso de tres dormitorios en el centro",
        "El casero anterior lo alquilaba por habitaciones. El fondo de ahora, por literas.",
        "2.º",
        "E",
    ),
    _l(
        "cristianos",
        "garaje",
        "garaje",
        12,
        "Plaza de garaje en el centro",
        "Hay más coches de alquiler que vecinos. Esta plaza es para uno de ellos.",
        "Sótano",
        "—",
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
        "Jacuzzi, solárium y un vecino con el yate a nombre de una sociedad de Delaware.",
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
    _l(
        "adeje",
        "duplex",
        "duplex",
        160,
        "Dúplex en una urbanización con golf",
        "Golf regado con agua desalada mientras el Cabildo pide ahorrar en la ducha.",
        "Bajo y 1.º",
        "A",
    ),
    _l(
        "adeje",
        "bungalow",
        "bungalow",
        70,
        "Bungalow de lujo en La Caleta",
        "Piscina privada y una limpiadora que vive a 40 km porque aquí no le llega.",
        "Bajo",
        "B",
    ),
    _l(
        "adeje",
        "amarre",
        "amarre",
        60,
        "Amarre en Puerto Colón",
        "Yates de alquiler, fiestas en barco y la tarifa de un piso por amarrar.",
        "Pantalán",
        "—",
    ),
    _l(
        "adeje",
        "local",
        "local",
        60,
        "Local en una galería de lujo",
        "Rodeado de joyerías. Las joyerías, rodeadas de vigilantes.",
        "Bajo",
        "C",
    ),
    _l(
        "adeje",
        "apartamentos",
        "edificio",
        500,
        "Edificio de apartamentos turísticos",
        "Cuarenta apartamentos turísticos en un municipio sin pisos para los profesores.",
        "Bajo a 4.º",
        "C",
    ),
    _l(
        "adeje",
        "atico_piscina",
        "atico",
        140,
        "Ático dúplex con piscina privada",
        "Piscina en la azotea: el agua la pagas tú y el sol lo pone el folleto del Cabildo.",
        "Ático",
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
#: que a 1 día = 1 mes son 60 días; el de temporada de los nómadas, medio año (art. 3 LAU).
#: Los nómadas pagan más pero se van antes y tardan en llegar, y en un barrio sin playa ni
#: fibra apenas aparecen (`NOMAD_DEMAND`): en el mejor barrio rinden como «de mercado» con
#: más vaivén, y en uno obrero o rural, menos que la de referencia (`docs/pisos_sim.py`).
RENT_LEVELS: dict[str, RentLevel] = {
    r.key: r
    for r in (
        RentLevel("social", "Asequible", "🤝", 0.8, 0.9, 0.002, 60),
        RentLevel("referencia", "De referencia", "📊", 1.0, 0.6, 0.004, 60),
        RentLevel("mercado", "De mercado", "📈", 1.25, 0.35, 0.006, 60),
        RentLevel("nomadas", "Para nómadas digitales", "💻", 1.5, 0.15, 0.001, 6),
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
        kinds: Si no está vacío, solo alquila estos tipos de inmueble. Los negocios
            (`BUSINESS_KINDS`) solo los alquila quien los lleva en `kinds`, y una persona
            (sin `kinds`) no se mete a vivir en un local, una nave o un amarre.
    """

    key: str
    name: str
    emoji: str
    levels: tuple[str, ...]
    line: str
    kinds: frozenset[str] = frozenset()

    def rents(self, level: str, kind: str) -> bool:
        """Si firmaría un contrato de `level` en un inmueble de tipo `kind`."""
        if level not in self.levels:
            return False
        if self.kinds:
            return kind in self.kinds
        return kind not in BUSINESS_KINDS


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
    # Negocios: alquilan locales, naves y amarres (7.22).
    Tenant(
        "franquicia",
        "Franquicia de cafeterías",
        "☕",
        ("referencia", "mercado"),
        "Abrimos el local trescientos de la isla. Pagamos puntual y cerramos sin avisar.",
        frozenset({"local"}),
    ),
    Tenant(
        "apuestas",
        "Casa de apuestas",
        "🎰",
        ("mercado", "nomadas"),
        "Pagamos el doble que cualquiera. No pregunte de dónde sale.",
        frozenset({"local"}),
    ),
    Tenant(
        "coworking",
        "Coworking con kombucha",
        "🧋",
        ("referencia", "mercado", "nomadas"),
        "Alquilamos mesas por horas a nómadas digitales. Como tú, pero con café.",
        frozenset({"local", "nave"}),
    ),
    Tenant(
        "ferreteria",
        "Ferretería de toda la vida",
        "🔩",
        ("social", "referencia"),
        "Llevamos sesenta años en el barrio. El contrato, que sea de los de antes.",
        frozenset({"local"}),
    ),
    Tenant(
        "taller",
        "Taller mecánico",
        "🔧",
        ("social", "referencia", "mercado"),
        "La ITV se la pasamos a cualquiera. Bueno, a casi cualquiera.",
        frozenset({"nave", "local"}),
    ),
    Tenant(
        "logistica",
        "Almacén de reparto de una multinacional",
        "📦",
        ("referencia", "mercado", "nomadas"),
        "Necesitamos la nave para ayer. Los impuestos los pagamos en Irlanda.",
        frozenset({"nave"}),
    ),
    Tenant(
        "pescador",
        "Pescador artesanal",
        "🎣",
        ("social", "referencia"),
        "La barquilla es de mi abuelo. La cuota de pesca, de Bruselas.",
        frozenset({"amarre"}),
    ),
    Tenant(
        "excursiones",
        "Excursiones para ver ballenas",
        "🐋",
        ("referencia", "mercado", "nomadas"),
        "Ballenas garantizadas o le devolvemos la ilusión.",
        frozenset({"amarre"}),
    ),
    Tenant(
        "yate_charter",
        "Yate de alquiler para despedidas",
        "🛥️",
        ("mercado", "nomadas"),
        "Fiesta en barco de diez a seis. El capitán es el único sobrio.",
        frozenset({"amarre"}),
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
