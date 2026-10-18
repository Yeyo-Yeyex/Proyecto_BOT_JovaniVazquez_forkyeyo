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
from datetime import date

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
        rent_bonus: Lo que suben la renta sus reformas en este servidor (0 en el catálogo).
        value_bonus: Lo que suben su valor (0 en el catálogo).
        breakdowns: Lo que multiplican sus reformas las averías y las cerraduras.
        damp: Lo que multiplican las humedades.
        rehab_until: Último día en que un contrato nuevo tiene la reducción del 60 % por
            vivienda rehabilitada (`None` si no se ha rehabilitado).
    """

    key: str
    barrio: str
    kind: str
    m2: int
    title: str
    blurb: str
    floor: str
    energy: str
    rent_bonus: float = 0.0
    value_bonus: float = 0.0
    breakdowns: float = 1.0
    damp: float = 1.0
    rehab_until: date | None = None


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
#: que a 1 día = 1 mes son 60 días; el de temporada de los nómadas, unos meses (art. 3 LAU).
#: Los nómadas pagan más pero se van antes y tardan en llegar, y en un barrio sin playa ni
#: fibra apenas aparecen (`NOMAD_DEMAND`). Cobrado de media frente a la renta de
#: referencia (`docs/pisos_sim.py --niveles`): referencia 0,92, mercado 1,11 y nómadas
#: 1,23 en la playa o el centro, 1,18 en uno histórico o marinero, 1,07 en el de lujo,
#: 1,01 en el obrero y 0,94 en el rural. Hasta la 7.22 daban 1,28 en cualquier barrio.
RENT_LEVELS: dict[str, RentLevel] = {
    r.key: r
    for r in (
        RentLevel("social", "Asequible", "🤝", 0.8, 0.9, 0.002, 60),
        RentLevel("referencia", "De referencia", "📊", 1.0, 0.6, 0.004, 60),
        RentLevel("mercado", "De mercado", "📈", 1.25, 0.35, 0.006, 60),
        RentLevel("nomadas", "Para nómadas digitales", "💻", 1.6, 0.15, 0.001, 8),
        # Vivienda vacacional: solo con licencia (`housing.VACATION_LEVEL`). Cada «mes» es
        # una estancia corta, se cobra por adelantado (no hay impago) y la ocupación
        # depende de la temporada (`housing.season_factor`).
        RentLevel("vacacional", "Vacacional (con licencia)", "🏖️", 2.0, 0.6, 0.0, 1),
    )
}
DEFAULT_RENT_LEVEL = "referencia"


@dataclass(frozen=True, slots=True)
class Tenant:
    """Un inquilino ficticio: quién vive en tu piso (o qué negocio ocupa tu local).

    Los multiplicadores son relativos: la gestora elige al azar entre los que encajan y
    `bot.services.housing.tenant_factors` los divide por la media de los que podían
    salir con esa renta y ese tipo de inmueble. Así la media de la gestora deja el
    impago, las marchas y las averías como estaban: quien elige al inquilino gana algo.

    Attributes:
        key: Clave estable.
        name: Quién es.
        levels: Rentas que acepta (claves de `RENT_LEVELS`).
        line: Lo que dice al firmar.
        kinds: Si no está vacío, solo alquila estos tipos de inmueble. Los negocios
            (`BUSINESS_KINDS`) solo los alquila quien los lleva en `kinds`, y una persona
            (sin `kinds`) no se mete a vivir en un local, una nave o un amarre.
        age: Años (0 en un negocio o un grupo de turistas). De 18 a 35 da la reducción
            del 70 % en zona tensionada si el piso se alquila por primera vez (art. 23.2
            LIRPF).
        risk: Lo que multiplica la probabilidad de dejar de pagar.
        stay: Lo que multiplica la de irse antes y la de no renovar.
        wear: Lo que multiplica las averías y las humedades (niños, perro, fiestas).
        traits: Rasgos que se ven en su tarjeta de candidato.
        renew: Lo que dice al renovar.
        leave: Lo que dice al irse.
        unpaid: Lo que dice cuando deja de pagar.
        complaint: Lo que dice cuando algo se rompe.
    """

    key: str
    name: str
    emoji: str
    levels: tuple[str, ...]
    line: str
    kinds: frozenset[str] = frozenset()
    age: int = 0
    risk: float = 1.0
    stay: float = 1.0
    wear: float = 1.0
    traits: tuple[str, ...] = ()
    renew: str = ""
    leave: str = ""
    unpaid: str = ""
    complaint: str = ""

    def rents(self, level: str, kind: str) -> bool:
        """Si firmaría un contrato de `level` en un inmueble de tipo `kind`."""
        if level not in self.levels:
            return False
        if self.kinds:
            return kind in self.kinds
        return kind not in BUSINESS_KINDS


_SOCIAL_REF = ("social", "referencia")
_LOW_MID = ("social", "referencia", "mercado")
_MID = ("referencia", "mercado")
_NOMAD = ("mercado", "nomadas")

TENANTS: tuple[Tenant, ...] = (
    # -- Personas: vivienda habitual ----------------------------------------------------
    Tenant(
        "enfermeros", "Pareja de enfermeros", "🩺", _LOW_MID,
        "Pagamos el 60 % del sueldo, pero por lo menos no vivimos con mis suegros.",
        age=31, risk=0.6, stay=0.9, wear=0.9, traits=("Dos nóminas", "Turnos de noche"),
        renew="Renovamos. Mudarse con turnos de noche no lo aguanta nadie.",
        leave="Nos han dado plaza en otra isla. Gracias por todo.",
        unpaid="Nos han pagado tarde las guardias. En cuanto entren, te pagamos.",
        complaint="Se ha roto el termo justo al salir de una guardia de doce horas.",
    ),
    Tenant(
        "familia", "Familia con dos niños", "👨‍👩‍👧‍👦", _SOCIAL_REF,
        "¿El colegio está cerca? Da igual, firmamos ya antes de que lo coja otro.",
        age=38, risk=1.0, stay=0.6, wear=1.5, traits=("Dos niños", "Buscan colegio cerca"),
        renew="Los niños ya tienen amigos en el barrio: nos quedamos.",
        leave="Viene el tercero y aquí ya no cabemos.",
        unpaid="Septiembre: libros, uniformes y la renta. Algo no llega.",
        complaint="Los niños han descubierto que el bidé hace de fuente.",
    ),
    Tenant(
        "opositor", "Opositor a notarías", "📚", _SOCIAL_REF,
        "Pagan mis padres. Llevo siete años estudiando, el octavo es el bueno.",
        age=29, risk=0.8, stay=0.7, wear=0.6, traits=("Pagan sus padres", "Siempre en casa"),
        renew="Me quedo: aquí he memorizado medio Código Civil.",
        leave="¡He aprobado! Me voy a una notaría en Soria.",
        unpaid="Mis padres dicen que este mes me lo pague el temario.",
        complaint="El vecino taladra justo cuando repaso Hipotecaria.",
    ),
    Tenant(
        "rider", "Rider con contrato", "🛵", _SOCIAL_REF,
        "Con la Ley Rider ya tengo nómina. Bueno, media nómina.",
        age=26, risk=1.6, stay=1.3, wear=1.0, traits=("Contrato temporal", "Media jornada"),
        renew="Renuevo. Total, el reparto no me deja tiempo ni para mudarme.",
        leave="Me voy a repartir a Madrid, que allí piden más.",
        unpaid="La aplicación me ha bajado las horas. Te pago cuando haya más pedidos.",
        complaint="Se ha roto la cerradura y tengo la moto dentro del salón.",
    ),
    Tenant(
        "profesora", "Profesora interina", "🍎", _LOW_MID,
        "Este año me tocó esta isla. El que viene, ya veremos.",
        age=33, risk=0.8, stay=1.5, wear=0.8, traits=("Plaza en el aire", "Nómina fija"),
        renew="Me han vuelto a dar esta isla. Renuevo antes de que cambien de idea.",
        leave="Han salido las listas: me voy a Fuerteventura.",
        unpaid="Las interinas cobramos tarde en septiembre. Ya llegará.",
        complaint="Corrigiendo exámenes se me ha ido la luz. Otra vez.",
    ),
    Tenant(
        "ingles", "Jubilado inglés", "🫖", _MID,
        "Vine una semana en 1994 y aquí sigo. Pago seis meses por adelantado.",
        age=71, risk=0.3, stay=0.5, wear=0.6, traits=("Paga por adelantado", "Pensión fija"),
        renew="Otros cinco años, por favor. Aquí no llueve nunca.",
        leave="Me vuelvo con mi hija: los nietos me reclaman.",
        unpaid="El banco de allí ha bloqueado la transferencia. Ya lo arreglo.",
        complaint="La tetera eléctrica ha hecho saltar los plomos.",
    ),
    Tenant(
        "medico", "Médica residente", "🥼", _MID,
        "Con las guardias casi ni piso la casa, así que me vale cualquier cosa.",
        age=28, risk=0.5, stay=1.2, wear=0.6, traits=("Nómina fija", "Guardias"),
        renew="Me quedan dos años de residencia: renuevo.",
        leave="Acabo la residencia y me voy de adjunta a otro hospital.",
        unpaid="Me he despistado entre guardia y guardia. Mañana te pago.",
        complaint="El aire acondicionado gotea encima de la cama.",
    ),
    Tenant(
        "funcionario", "Funcionario del Cabildo", "🗂️", _MID,
        "Plaza fija, nómina fija y siesta fija. El inquilino perfecto.",
        age=45, risk=0.3, stay=0.6, wear=0.8, traits=("Plaza fija", "Nómina fija"),
        renew="Renuevo, por supuesto. Por triplicado.",
        leave="Me ha salido el traslado a la otra isla.",
        unpaid="Ha habido un retraso administrativo. Ya sabe cómo va esto.",
        complaint="He presentado una queja por registro: la persiana no baja.",
    ),
    Tenant(
        "cajera", "Cajera del supermercado", "🛒", _LOW_MID,
        "Contrato indefinido y turno de mañana. Mejor que eso, solo el de tarde.",
        age=32, risk=0.6, stay=0.8, wear=0.9, traits=("Contrato indefinido", "Nómina fija"),
        renew="Renuevo. El súper está a cinco minutos andando.",
        leave="Me voy a vivir con mi pareja. Te dejo el piso limpio.",
        unpaid="Me han cambiado el día de cobro. La semana que viene te pago.",
        complaint="Ha salido agua debajo del fregadero.",
    ),
    Tenant(
        "guagua", "Conductor de guagua", "🚌", _SOCIAL_REF,
        "Treinta años en la misma línea. Puntual con la renta, más que la guagua.",
        age=47, risk=0.4, stay=0.6, wear=0.8, traits=("Plaza fija", "Madruga"),
        renew="Renuevo. La parada está en la puerta: no me muevo de aquí.",
        leave="Me jubilo y me vuelvo al pueblo.",
        unpaid="Huelga de transporte: este mes cobramos menos.",
        complaint="El termo hace un ruido como el motor de la 12.",
    ),
    Tenant(
        "abuela", "Abuela con su pensión", "👵", _SOCIAL_REF,
        "Llevo cincuenta años en el barrio. Al casero le hago potaje los domingos.",
        age=79, risk=0.3, stay=0.4, wear=0.5, traits=("Pensión fija", "No se mueve del barrio"),
        renew="¿Irme yo? De aquí no me saca nadie.",
        leave="Me voy a vivir con mi hija. Le dejo la llave a la vecina.",
        unpaid="Con lo que ha subido la luz, este mes no llego.",
        complaint="La lavadora hace un ruido que no es normal.",
    ),
    Tenant(
        "estudiantes", "Tres estudiantes compartiendo piso", "🎓", _LOW_MID,
        "Somos tres y pagamos a medias. La limpieza, ya veremos.",
        age=21, risk=1.2, stay=1.5, wear=1.7, traits=("Pagan sus padres", "Fiestas"),
        renew="Nos quedamos otro curso. Bueno, dos, que hay asignaturas pendientes.",
        leave="Acabamos la carrera: cada uno se vuelve a su casa.",
        unpaid="Uno de los tres se ha ido sin pagar su parte.",
        complaint="Se ha roto la puerta del baño. No preguntes cómo.",
    ),
    Tenant(
        "camarero", "Camarero fijo discontinuo", "🍽️", _SOCIAL_REF,
        "En temporada alta, ningún problema. En temporada baja, hablamos.",
        age=27, risk=1.5, stay=1.3, wear=1.0, traits=("Fijo discontinuo", "Trabaja de noche"),
        renew="Renuevo. El hotel me vuelve a llamar en noviembre.",
        leave="Me han ofrecido la temporada en otra isla.",
        unpaid="Temporada baja: el hotel ha cerrado hasta noviembre.",
        complaint="Llego a las dos de la mañana y no hay agua caliente.",
    ),
    Tenant(
        "policia", "Policía local recién destinado", "👮", _MID,
        "Me han destinado aquí dos años. Luego, concurso de traslados.",
        age=28, risk=0.4, stay=1.6, wear=0.8, traits=("Plaza fija", "Destino temporal"),
        renew="Sigo destinado aquí. Renuevo.",
        leave="Ha salido el concurso de traslados: vuelvo a mi isla.",
        unpaid="Se me ha pasado. Ahora mismo lo pago, que no quiero líos.",
        complaint="Han forzado la cerradura del portal. Lo he denunciado yo mismo.",
    ),
    Tenant(
        "bombera", "Bombera del consorcio", "🚒", _MID,
        "Si se quema algo, lo apago yo. Si se rompe, te aviso.",
        age=36, risk=0.4, stay=0.7, wear=0.7, traits=("Plaza fija", "Turnos de 24 horas"),
        renew="Renuevo. El parque está a diez minutos.",
        leave="Me voy a una casa con jardín para el perro.",
        unpaid="Un despiste con la transferencia. Ya está hecha.",
        complaint="El enchufe de la cocina echa chispas. Por suerte, entiendo de esto.",
    ),
    Tenant(
        "madre", "Madre con un hijo", "🧒", _SOCIAL_REF,
        "Me hace falta un sitio cerca del colegio y del trabajo. Esto es las dos cosas.",
        age=34, risk=1.0, stay=0.7, wear=1.2, traits=("Una nómina", "Un niño"),
        renew="El niño está contento en el colegio: renovamos.",
        leave="Me han ofrecido un trabajo mejor en el sur.",
        unpaid="El padre no ha pagado la pensión este mes. En cuanto pague, te pago.",
        complaint="Se ha roto la cisterna y no para de correr el agua.",
    ),
    Tenant(
        "divorciado", "Recién divorciado", "💔", _LOW_MID,
        "Me ha tocado el sofá y el perro. El piso, a ella.",
        age=46, risk=1.3, stay=1.2, wear=1.2, traits=("Pensión de alimentos", "Con perro"),
        renew="Renuevo. Ya me he acostumbrado a cocinar solo.",
        leave="Hemos vuelto. Lo sé, lo sé.",
        unpaid="Entre el abogado y la pensión de los niños, este mes no llego.",
        complaint="El perro ha mordido la puerta. Lo pago yo, tranquilo.",
    ),
    Tenant(
        "musico", "Músico de orquesta de verbenas", "🎺", _SOCIAL_REF,
        "En verano no paro: fiestas del Pino, del Carmen y de lo que sea. En invierno, ensayo.",
        age=41, risk=1.8, stay=1.0, wear=1.1, traits=("Autónomo", "Ensaya en casa"),
        renew="Renuevo. Los vecinos ya se saben el repertorio.",
        leave="Nos han contratado para una gira por Venezuela.",
        unpaid="En invierno no hay verbenas. En junio te lo pago todo.",
        complaint="Se ha ido la luz en mitad del ensayo.",
    ),
    Tenant(
        "graduados", "Pareja de recién graduados", "🥂", _SOCIAL_REF,
        "Primer piso sin compañeros. La nevera, por fin, es nuestra.",
        age=25, risk=1.3, stay=1.2, wear=0.9,
        traits=("Contrato en prácticas", "Dos sueldos pequeños"),
        renew="Nos han hecho fijos a los dos: renovamos.",
        leave="Nos vamos a Dublín, que allí pagan más.",
        unpaid="Se han acabado las prácticas y el contrato nuevo no ha llegado.",
        complaint="El horno no calienta. Lo hemos descubierto el día de la cena con los suegros.",
    ),
    Tenant(
        "arquitectos", "Pareja de arquitectos", "📐", _MID,
        "¿Esa pared es de carga? Lo digo por nada.",
        age=34, risk=1.1, stay=0.9, wear=0.8, traits=("Autónomos", "Teletrabajan"),
        renew="Renovamos. Ya hemos hecho los planos de cómo quedaría reformado.",
        leave="Nos vamos a una casa que hemos diseñado nosotros. Tardaremos diez años.",
        unpaid="El cliente no nos ha pagado el proyecto. Ya cobraremos.",
        complaint="Hay humedades en el techo. Es la cubierta, te lo digo yo.",
    ),
    Tenant(
        "futbolista", "Futbolista del filial", "⚽", _MID,
        "Si subo al primer equipo, te compro el piso. Si no, me ceden a otro club.",
        age=20, risk=0.9, stay=1.6, wear=1.2, traits=("Contrato federativo", "Lo paga el club"),
        renew="Me han renovado en el club. Y yo te renuevo a ti.",
        leave="Me ceden a un equipo de Segunda.",
        unpaid="El club paga tarde este mes. Cosas del fútbol.",
        complaint="La tele se ha caído celebrando un gol. Un gol mío, eso sí.",
    ),
    Tenant(
        "abogada", "Abogada de un bufete", "⚖️", ("mercado",),
        "He leído el contrato tres veces. La cláusula siete, mejor la hablamos.",
        age=37, risk=0.5, stay=0.9, wear=0.7, traits=("Nómina alta", "Lee la letra pequeña"),
        renew="Renuevo, con la cláusula siete corregida.",
        leave="Me he comprado un ático. Con hipoteca, como todo el mundo.",
        unpaid="No pago hasta que arregles la avería. Te lo mando por burofax.",
        complaint="Te mando por burofax la lista de desperfectos.",
    ),
    Tenant(
        "piloto", "Piloto de Binter", "✈️", ("mercado",),
        "Duermo entre islas tres noches de cada cuatro. El piso estará impecable.",
        age=39, risk=0.3, stay=0.8, wear=0.6, traits=("Nómina alta", "Casi nunca está"),
        renew="Renuevo. Aterrizo, duermo y despego: no hay mejor inquilino.",
        leave="Me han ascendido a comandante en otra base.",
        unpaid="Se me ha pasado entre vuelo y vuelo. Pago al aterrizar.",
        complaint="Vuelvo de tres días fuera y la nevera se ha apagado.",
    ),
    Tenant(
        "directivo", "Directivo trasladado de Madrid", "💼", ("mercado",),
        "Lo paga la empresa. Dos años de destino y vuelta a la Castellana.",
        age=52, risk=0.2, stay=1.4, wear=0.7, traits=("Lo paga la empresa", "Destino temporal"),
        renew="La empresa me alarga el destino: renuevo.",
        leave="Fin del destino. Vuelvo a Madrid con muy buen color.",
        unpaid="Recursos humanos ha cambiado de proveedor. Ya está solucionado.",
        complaint="Mi asistente te llamará por lo del aire acondicionado.",
    ),
    Tenant(
        "ingeniera", "Ingeniera de una startup", "👩‍💻", _NOMAD,
        "Si sale bien la ronda de financiación, te pago un año por adelantado.",
        age=30, risk=0.7, stay=1.4, wear=0.7, traits=("Teletrabaja", "Sueldo en acciones"),
        renew="Ha salido la ronda: renuevo.",
        leave="Nos ha comprado una empresa de Silicon Valley. Me mudo.",
        unpaid="La startup ha cerrado. Me han pagado en acciones que no valen nada.",
        complaint="Se ha caído la fibra en plena reunión con los inversores.",
    ),
    # -- Personas: temporada (nómadas digitales) ---------------------------------------
    Tenant(
        "aleman", "Teletrabajador alemán", "💻", _NOMAD,
        "En Berlín pago el doble por la mitad de sol. ¿La fibra llega bien al balcón?",
        age=34, risk=0.7, stay=1.2, wear=0.7, traits=("Teletrabaja", "Empresa extranjera"),
        renew="Me quedo otra temporada. En casa ya están a dos grados.",
        leave="Se acaba el invierno: vuelvo a casa hasta noviembre.",
        unpaid="Mi banco no reconoce la cuenta. Ya lo arreglo.",
        complaint="El wifi se corta cada vez que llueve. Aquí no llueve y se corta igual.",
    ),
    Tenant(
        "influencer", "Influencer de viajes", "🤳", ("nomadas",),
        "¿Me haces precio si te etiqueto en las stories?",
        age=24, risk=1.4, stay=1.4, wear=1.1, traits=("Ingresos variables", "Graba en casa"),
        renew="Me quedo: el piso tiene muy buena luz para los vídeos.",
        leave="Me ha salido una colaboración en Bali.",
        unpaid="La marca no me ha pagado la campaña. Te pago en visibilidad.",
        complaint="Se ha roto la persiana y no puedo grabar a contraluz.",
    ),
    Tenant(
        "erasmus", "Erasmus italiano", "🍕", _NOMAD,
        "Vengo de Bolonia para un cuatrimestre. Pagan mis padres, tranquilo.",
        age=22, risk=1.1, stay=1.3, wear=1.4, traits=("Pagan sus padres", "Fiestas"),
        renew="Alargo la beca un cuatrimestre más.",
        leave="Se acaba el Erasmus. Me llevo el gofio en la maleta.",
        unpaid="Mis padres están de viaje y no miran el móvil.",
        complaint="Hicimos una cena y la vitrocerámica no ha sobrevivido.",
    ),
    Tenant(
        "cripto", "Nómada cripto", "🪙", ("nomadas",),
        "¿Aceptas el pago en mi moneda? Mañana vale el doble, seguro.",
        age=30, risk=1.9, stay=1.2, wear=1.0, traits=("Ingresos variables", "Teletrabaja"),
        renew="Renuevo. Y te pago en euros, que la moneda ha bajado un poco.",
        leave="Me voy a Dubái. Allí no se pagan impuestos, dicen.",
        unpaid="Mi moneda ha caído un 90 %. Pero va a volver a subir.",
        complaint="Se ha ido la luz y se me han apagado los ordenadores de minar.",
    ),
    Tenant(
        "disenadora", "Diseñadora freelance", "🎨", _NOMAD,
        "Trabajo desde cualquier sitio con enchufe y buena luz. Esto tiene las dos.",
        age=29, risk=1.2, stay=1.3, wear=0.8, traits=("Autónoma", "Teletrabaja"),
        renew="Me quedo otra temporada: aquí me salen mejor los encargos.",
        leave="Me voy a Lisboa a probar otro invierno.",
        unpaid="Tengo tres facturas sin cobrar. Cuando paguen ellos, pago yo.",
        complaint="La silla del escritorio se ha roto. Bueno, esa era mía.",
    ),
    Tenant(
        "desarrollador", "Desarrollador de videojuegos", "🎮", _NOMAD,
        "Necesito fibra simétrica. Lo demás me da igual: casi no salgo.",
        age=27, risk=1.0, stay=1.1, wear=0.8, traits=("Teletrabaja", "Siempre en casa"),
        renew="Renuevo. Me queda mucho para terminar el juego.",
        leave="Hemos sacado el juego y me voy a celebrarlo a Japón.",
        unpaid="Han retrasado la salida del juego y no cobro hasta entonces.",
        complaint="Se ha cortado la fibra en mitad de una partida clasificatoria.",
    ),
    Tenant(
        "furgoneta", "Pareja de viajeros en furgoneta", "🚐", ("nomadas",),
        "Se nos ha roto la furgoneta en Mogán, así que nos quedamos unos meses.",
        age=31, risk=1.5, stay=1.6, wear=0.9, traits=("Ingresos variables", "Con perro"),
        renew="El taller dice que la furgoneta tardará otra temporada.",
        leave="Ya está arreglada la furgoneta. ¡Nos vamos!",
        unpaid="Hemos gastado lo del alquiler en el motor nuevo.",
        complaint="El perro ha descubierto que la puerta de la terraza se abre sola.",
    ),
    Tenant(
        "escritor", "Escritor buscando inspiración", "✍️", _NOMAD,
        "Vengo a terminar la novela. Llevo cuatro años con el primer capítulo.",
        age=55, risk=1.0, stay=1.0, wear=0.6, traits=("Ingresos variables", "Siempre en casa"),
        renew="Me quedo. Ya voy por el segundo capítulo.",
        leave="He terminado la novela. Sales en el capítulo doce.",
        unpaid="La editorial no ha pagado el adelanto.",
        complaint="El vecino del tercero ensaya con la batería y no me deja escribir.",
    ),
    # -- Turistas (vivienda vacacional) -------------------------------------------------
    Tenant(
        "britanicos", "Familia británica de vacaciones", "☀️", ("vacacional",),
        "¿La piscina tiene sombra? Los niños ya se han quemado en el aeropuerto.",
        wear=1.3,
    ),
    Tenant(
        "despedida", "Despedida de soltero", "🍻", ("vacacional",),
        "Somos ocho y un novio. ¿El balcón aguanta?",
        wear=2.0,
    ),
    Tenant(
        "nordicos", "Pareja nórdica jubilada", "🧓", ("vacacional",),
        "Venimos de noviembre a marzo desde 1998. Nos quedamos con el piso.",
        wear=0.5,
    ),
    Tenant(
        "surferos", "Grupo de surferos", "🏄", ("vacacional",),
        "¿Hay sitio para seis tablas en el salón?",
        wear=1.2,
    ),
    Tenant(
        "senderistas", "Senderistas del Teide", "🥾", ("vacacional",),
        "Salimos a las cinco de la mañana y volvemos reventados: ducha y cama.",
        wear=0.7,
    ),
    Tenant(
        "luna_miel", "Pareja de luna de miel", "💍", ("vacacional",),
        "¿Hay vistas al mar? Se lo hemos prometido a su madre.",
        wear=0.6,
    ),
    Tenant(
        "ciclistas", "Equipo ciclista de pretemporada", "🚴", ("vacacional",),
        "Venimos a entrenar en invierno, como los profesionales. ¿Dónde dejamos las bicis?",
        wear=1.1,
    ),
    Tenant(
        "buceo", "Grupo de buceo", "🤿", ("vacacional",),
        "Los trajes de neopreno se secan en el balcón, si no le importa.",
        wear=1.0,
    ),
    Tenant(
        "amigas", "Viaje de amigas", "👯", ("vacacional",),
        "Cinco amigas, siete días y un plan: no tener ningún plan.",
        wear=1.0,
    ),
    Tenant(
        "numerosa", "Familia numerosa de vacaciones", "🏖️", ("vacacional",),
        "Somos cinco y la abuela. ¿El sofá cama es de verdad?",
        wear=1.5,
    ),
    # -- Negocios: locales, naves y amarres (7.22 y fase 2 de la ampliación) -------------
    Tenant(
        "franquicia", "Franquicia de cafeterías", "☕", _MID,
        "Abrimos el local trescientos de la isla. Pagamos puntual y cerramos sin avisar.",
        frozenset({"local"}), risk=0.5, stay=1.3, wear=0.9, traits=("Cadena", "Paga puntual"),
        renew="Renovamos el local. El de enfrente lo cerramos.",
        leave="La central ha decidido cerrar el local. Sin explicaciones.",
        unpaid="Los pagos salen de la central y este mes van con retraso.",
        complaint="La cafetera industrial ha reventado la instalación.",
    ),
    Tenant(
        "apuestas", "Casa de apuestas", "🎰", _NOMAD,
        "Pagamos el doble que cualquiera. No pregunte de dónde sale.",
        frozenset({"local"}), risk=0.7, stay=1.4, wear=1.1, traits=("Paga bien", "Mala fama"),
        renew="Renovamos, aunque el Ayuntamiento quiera echarnos.",
        leave="Nueva ordenanza: no podemos estar a menos de 500 metros del colegio.",
        unpaid="La casa siempre gana, pero este mes ha perdido.",
        complaint="Un cliente se ha enfadado con la máquina y la máquina ha perdido.",
    ),
    Tenant(
        "coworking", "Coworking con kombucha", "🧋", ("referencia", "mercado", "nomadas"),
        "Alquilamos mesas por horas a nómadas digitales. Como tú, pero con café.",
        frozenset({"local", "nave"}), risk=1.2, stay=1.2, wear=0.9,
        traits=("Startup", "Ingresos variables"),
        renew="Renovamos: tenemos lista de espera para las mesas de la ventana.",
        leave="Pivotamos: ahora somos una plataforma, sin local.",
        unpaid="Estamos esperando la siguiente ronda de financiación.",
        complaint="El aire acondicionado no puede con cuarenta portátiles.",
    ),
    Tenant(
        "ferreteria", "Ferretería de toda la vida", "🔩", _SOCIAL_REF,
        "Llevamos sesenta años en el barrio. El contrato, que sea de los de antes.",
        frozenset({"local"}), risk=0.6, stay=0.5, wear=0.8,
        traits=("Negocio familiar", "Sesenta años en el barrio"),
        renew="Renovamos. Mi padre ya renovaba con el tuyo.",
        leave="Nos jubilamos. Con nosotros cierra la última ferretería del barrio.",
        unpaid="Con las grandes superficies cerca, este mes se ha vendido poco.",
        complaint="La persiana metálica se ha atascado a media altura.",
    ),
    Tenant(
        "taller", "Taller mecánico", "🔧", _LOW_MID,
        "La ITV se la pasamos a cualquiera. Bueno, a casi cualquiera.",
        frozenset({"nave", "local"}), risk=1.0, stay=0.8, wear=1.6,
        traits=("Autónomo", "Mucho trajín"),
        renew="Renovamos. Ya tenemos clientes de tres generaciones.",
        leave="Nos mudamos al polígono, que allí cabe la grúa.",
        unpaid="Me deben tres reparaciones. Cuando las cobre, pago.",
        complaint="Hay una mancha de aceite en el suelo. Lleva ahí desde 1990.",
    ),
    Tenant(
        "logistica", "Almacén de reparto de una multinacional", "📦",
        ("referencia", "mercado", "nomadas"),
        "Necesitamos la nave para ayer. Los impuestos los pagamos en Irlanda.",
        frozenset({"nave"}), risk=0.4, stay=1.3, wear=1.3,
        traits=("Multinacional", "Mucho tráfico de furgonetas"),
        renew="Renovamos. Nos sale más barato que construir.",
        leave="Hemos automatizado el reparto: nos vamos a una nave más grande.",
        unpaid="El pago está en revisión en la sede europea.",
        complaint="Una carretilla ha chocado con una columna. Mandamos un perito.",
    ),
    Tenant(
        "pescador", "Pescador artesanal", "🎣", _SOCIAL_REF,
        "La barquilla es de mi abuelo. La cuota de pesca, de Bruselas.",
        frozenset({"amarre"}), risk=1.2, stay=0.5, wear=0.9,
        traits=("Autónomo", "Toda la vida en el muelle"),
        renew="Renuevo. Sin amarre no hay pesca.",
        leave="Vendo la barquilla. Los nietos no quieren saber nada del mar.",
        unpaid="Han cerrado la pesquería del atún: este mes no hay ingresos.",
        complaint="Se ha soltado una defensa y la barquilla ha rozado el muelle.",
    ),
    Tenant(
        "excursiones", "Excursiones para ver ballenas", "🐋",
        ("referencia", "mercado", "nomadas"),
        "Ballenas garantizadas o le devolvemos la ilusión.",
        frozenset({"amarre"}), risk=0.9, stay=1.0, wear=1.0, traits=("Turismo", "Temporada"),
        renew="Renovamos el amarre: las ballenas siguen viniendo.",
        leave="Nos vamos a otro puerto con más cruceros.",
        unpaid="Temporada baja: ni turistas ni ballenas.",
        complaint="Un turista ha tirado el móvil al agua y ha atascado la bomba de achique.",
    ),
    Tenant(
        "yate_charter", "Yate de alquiler para despedidas", "🛥️", _NOMAD,
        "Fiesta en barco de diez a seis. El capitán es el único sobrio.",
        frozenset({"amarre"}), risk=1.3, stay=1.2, wear=1.7, traits=("Turismo", "Fiestas"),
        renew="Renovamos. Este verano tenemos todos los sábados reservados.",
        leave="Nos llevamos el barco a Ibiza.",
        unpaid="Nos han cancelado tres despedidas seguidas.",
        complaint="La fiesta del sábado ha dejado el pantalán hecho un desastre.",
    ),
    Tenant(
        "bar", "Bar de toda la vida", "🍺", _SOCIAL_REF,
        "Café, barraquito y el partido. Abrimos a las siete, como siempre.",
        frozenset({"local"}), risk=1.0, stay=0.6, wear=1.3,
        traits=("Negocio familiar", "Abre a las siete"),
        renew="Renovamos. Aquí se han visto todas las finales desde 1982.",
        leave="Nos jubilamos. El último barraquito, por cuenta de la casa.",
        unpaid="Han abierto una franquicia enfrente y ha bajado la caja.",
        complaint="Se ha roto la cámara frigorífica en pleno partido.",
    ),
    Tenant(
        "peluqueria", "Peluquería de barrio", "💇", _LOW_MID,
        "Aquí una se entera de todo lo que pasa en el barrio, y además se corta el pelo.",
        frozenset({"local"}), risk=0.8, stay=0.7, wear=1.0, traits=("Autónoma", "Clientela fija"),
        renew="Renuevo. Las clientas no me dejarían irme.",
        leave="Me voy a un local más grande, con dos sillones más.",
        unpaid="Agosto: medio barrio de vacaciones y el otro medio, sin dinero.",
        complaint="El calentador no da para tantos lavados.",
    ),
    Tenant(
        "academia", "Academia de inglés", "🗣️", _MID,
        "Clases de repaso por las tardes y preparación de exámenes en verano.",
        frozenset({"local"}), risk=0.7, stay=0.9, wear=1.1,
        traits=("Pyme", "Niños por las tardes"),
        renew="Renovamos. Se nos han apuntado todos los del colegio de al lado.",
        leave="Nos pasamos a dar las clases por internet.",
        unpaid="Los padres pagan el curso en septiembre. Hasta entonces, vamos justos.",
        complaint="Los niños han atascado el váter con una pelota de goma.",
    ),
    Tenant(
        "souvenirs", "Tienda de recuerdos", "🧲", _NOMAD,
        "Imanes del Teide hechos muy lejos del Teide.",
        frozenset({"local"}), risk=1.1, stay=1.1, wear=0.8, traits=("Turismo", "Temporada"),
        renew="Renovamos. Los cruceros han vuelto a parar aquí.",
        leave="Han cambiado la ruta de los cruceros y aquí ya no pasa nadie.",
        unpaid="Sin cruceros no se vende ni un imán.",
        complaint="Ha entrado agua por el escaparate con la última tormenta.",
    ),
    Tenant(
        "dentista", "Clínica dental de una cadena", "🦷", ("mercado",),
        "Primera visita gratis. La segunda, ya veremos.",
        frozenset({"local"}), risk=1.4, stay=1.3, wear=0.9,
        traits=("Cadena", "Financia los tratamientos"),
        renew="Renovamos. Tenemos la agenda llena hasta el año que viene.",
        leave="La cadena ha quebrado y los clientes se han quedado sin sus implantes.",
        unpaid="La financiera que cobraba los tratamientos ha cerrado.",
        complaint="El compresor de la clínica ha hecho saltar la luz de todo el edificio.",
    ),
    Tenant(
        "bodega", "Bodega de vino de la tierra", "🍷", _LOW_MID,
        "Malvasía de la isla. La crianza, en tu nave.",
        frozenset({"nave"}), risk=0.8, stay=0.6, wear=1.0,
        traits=("Negocio familiar", "Vendimia en agosto"),
        renew="Renovamos. La nave tiene la temperatura perfecta para las barricas.",
        leave="Nos vamos a una nave junto a las viñas.",
        unpaid="Mala cosecha por la calima: este año vendemos poco.",
        complaint="Se ha roto la máquina de frío y las barricas sufren.",
    ),
    Tenant(
        "vela", "Escuela de vela", "⛵", _MID,
        "Cursos para niños en verano y regatas los domingos.",
        frozenset({"amarre"}), risk=0.6, stay=0.7, wear=1.1,
        traits=("Club deportivo", "Temporada"),
        renew="Renovamos el amarre. Este año tenemos el doble de alumnos.",
        leave="El club ha conseguido un pantalán propio.",
        unpaid="Esperamos la subvención del Cabildo, que llega tarde.",
        complaint="Un optimist ha chocado con el pantalán. El niño está bien.",
    ),
)  # fmt: skip

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


# -- Noticias de barrio (fase 2 de la ampliación) -------------------------------------------
#
# Cada lunes algún barrio sale en las noticias (`housing.monday_news`). Durante 7 días la
# noticia cambia lo que pasa allí: lo que cuesta encontrar inquilino, los nómadas y los
# turistas, el impago, las averías, las quejas del vecino o los okupas. Alguna mueve el
# índice del barrio un 1-2 % de una vez: muy por debajo de lo que cuesta comprar y vender
# (~11 %), así que no se puede especular con ellas (y no se saben hasta que salen).


@dataclass(frozen=True, slots=True)
class BarrioNews:
    """Una noticia que puede salir en un barrio.

    Attributes:
        key: Clave estable.
        title: El titular, en claro.
        blurb: Una línea más (a veces con guasa).
        themes: Tipos de barrio donde puede salir (vacío: en todos).
        islands: Islas donde puede salir (vacío: en las dos).
        months: Meses en que puede salir (vacío: todo el año).
        find: Multiplica la probabilidad de encontrar inquilino (y candidatos).
        nomad: Multiplica, además, la de los nómadas digitales.
        vacation: Multiplica, además, la de los turistas.
        default: Multiplica la de dejar de pagar.
        repairs: Multiplica averías, humedades y cerraduras.
        neighbour: Multiplica las quejas del vecino.
        squat: Multiplica la entrada de okupas.
        index: Lo que multiplica el índice del barrio, una vez, el día que sale.
        weight: Lo probable que es frente a las demás.
    """

    key: str
    title: str
    blurb: str
    themes: frozenset[str] = frozenset()
    islands: frozenset[str] = frozenset()
    months: frozenset[int] = frozenset()
    find: float = 1.0
    nomad: float = 1.0
    vacation: float = 1.0
    default: float = 1.0
    repairs: float = 1.0
    neighbour: float = 1.0
    squat: float = 1.0
    index: float = 1.0
    weight: float = 1.0


#: Sin límite (cualquier barrio, isla o mes).
_ANY: frozenset = frozenset()
_SUMMER = frozenset({6, 7, 8, 9})
_WINTER = frozenset({11, 12, 1, 2, 3})
_COAST = frozenset({"playa", "marinero"})
_TOURIST = frozenset({"playa", "lujo", "centro", "historico", "marinero"})

BARRIO_NEWS: tuple[BarrioNews, ...] = (
    BarrioNews(
        "supermercado", "Abre un supermercado nuevo en el barrio",
        "Con parking y todo. Media isla viene a comprar aquí.",
        find=1.15,
    ),
    BarrioNews(
        "centro_salud", "Inauguran un centro de salud",
        "Por fin no hay que coger la guagua para ir al médico.",
        frozenset({"obrero", "rural", "centro", "marinero"}), find=1.1, index=1.01,
    ),
    BarrioNews(
        "sin_medicos", "El centro de salud cierra por las tardes",
        "Faltan médicos: la cola empieza a las siete de la mañana.",
        find=0.9,
    ),
    BarrioNews(
        "metroguagua", "Obras de la MetroGuagua en la avenida",
        "Dos carriles cortados y un ruido de taladro que no para.",
        frozenset({"centro", "obrero", "playa"}), frozenset({"gc"}), find=0.85, neighbour=1.5,
    ),
    BarrioNews(
        "tranvia", "Amplían la línea del tranvía",
        "La parada nueva está a cinco minutos andando.",
        frozenset({"centro", "historico", "obrero"}), frozenset({"tf"}), find=1.15, index=1.02,
    ),
    BarrioNews(
        "fibra", "Llega la fibra de 1 Gb al barrio",
        "Los nómadas digitales ya lo saben. Ellos siempre lo saben antes.",
        nomad=1.3,
    ),
    BarrioNews(
        "rodaje", "Rodaje de una serie en las calles del barrio",
        "Han cortado la calle tres días para grabar una escena de cuatro segundos.",
        frozenset({"historico", "centro", "playa", "marinero"}), nomad=1.2, vacation=1.2,
        neighbour=1.3,
    ),
    BarrioNews(
        "festival", "Festival de música el fin de semana",
        "Tres escenarios y cuarenta mil personas. Los vecinos, despiertos hasta las seis.",
        frozenset({"playa", "centro"}), _ANY, _SUMMER, vacation=1.3, neighbour=1.8,
    ),
    BarrioNews(
        "botellon", "Botellón todos los viernes en la plaza",
        "La policía pasa, mira y se va.",
        find=0.9, neighbour=2.0,
    ),
    BarrioNews(
        "fabrica", "Cierra la fábrica de toda la vida",
        "Doscientos despidos. En el bar de la esquina no se habla de otra cosa.",
        frozenset({"obrero"}), default=1.5, find=0.85, index=0.98,
    ),
    BarrioNews(
        "logistica", "Abre un centro logístico en el polígono",
        "Trescientos empleos nuevos, casi todos de madrugada.",
        frozenset({"obrero"}), find=1.15, default=0.9,
    ),
    BarrioNews(
        "tiburon", "Bandera roja: avistan un tiburón cerca de la orilla",
        "Era un tronco. Pero la bandera roja se ha quedado toda la semana.",
        _COAST, vacation=0.7,
    ),
    BarrioNews(
        "ballena", "Una ballena se deja ver frente a la costa",
        "Media isla está en el muelle con el móvil en alto.",
        _COAST, vacation=1.2,
    ),
    BarrioNews(
        "calima", "Calima: polvo en suspensión y cuarenta grados",
        "Los aires acondicionados no dan abasto y las terrazas, color naranja.",
        repairs=1.4, vacation=0.8,
    ),
    BarrioNews(
        "carnaval", "Carnaval: el barrio no duerme",
        "Murgas, comparsas y una ciudad entera disfrazada.",
        frozenset({"centro", "historico", "marinero"}), _ANY, frozenset({2, 3}),
        vacation=1.5, neighbour=2.0,
    ),
    BarrioNews(
        "romeria", "Romería del barrio",
        "Carretas, trajes típicos y papas con mojo para todos.",
        frozenset({"rural", "historico"}), vacation=1.1, neighbour=1.3,
    ),
    BarrioNews(
        "bandera_azul", "La playa recibe la bandera azul",
        "Agua limpia, socorristas y duchas que funcionan.",
        frozenset({"playa"}), vacation=1.25, index=1.01,
    ),
    BarrioNews(
        "vertido", "Vertido de aguas residuales en la playa",
        "Prohibido el baño hasta nuevo aviso. Huele hasta en la avenida.",
        _COAST, vacation=0.6, find=0.95,
    ),
    BarrioNews(
        "hotel_lujo", "Abre un hotel de cinco estrellas",
        "Con spa, helipuerto y una suite que cuesta lo que tu alquiler de un año.",
        frozenset({"playa", "lujo"}), vacation=0.85, find=1.05, index=1.02,
    ),
    BarrioNews(
        "cruceros", "Llegan más cruceros al puerto",
        "Seis mil turistas bajan a la vez y suben a las cinco.",
        frozenset({"marinero", "centro"}), vacation=1.15,
    ),
    BarrioNews(
        "peatonal", "Peatonalizan la calle principal",
        "Más terrazas, menos coches y el precio del metro cuadrado, para arriba.",
        frozenset({"centro", "historico"}), find=1.1, index=1.02,
    ),
    BarrioNews(
        "colegio", "Abre un colegio nuevo",
        "Las familias del barrio de al lado ya buscan piso aquí.",
        frozenset({"obrero", "rural", "centro"}), find=1.15,
    ),
    BarrioNews(
        "residencia", "Abre una residencia de estudiantes",
        "Trescientos estudiantes más. Los bares, encantados.",
        frozenset({"historico", "centro"}), find=1.1, nomad=1.1, neighbour=1.2,
    ),
    BarrioNews(
        "robos", "Ola de robos en los portales",
        "La comunidad de propietarios se plantea poner cámaras.",
        find=0.85, repairs=1.3, squat=1.3,
    ),
    BarrioNews(
        "apagon", "Apagón de una noche entera",
        "Se fundieron neveras, routers y la paciencia de todo el barrio.",
        repairs=1.3,
    ),
    BarrioNews(
        "sequia", "Restricciones de agua por la sequía",
        "Cortes de agua de diez a seis. Las piscinas, vacías.",
        frozenset({"rural", "lujo"}), find=0.9, vacation=0.9,
    ),
    BarrioNews(
        "mercadillo", "Mercadillo del agricultor los sábados",
        "Papas, quesos y mojo de la zona. Los domingos, sin pan.",
        frozenset({"rural", "historico"}), find=1.05, vacation=1.1,
    ),
    BarrioNews(
        "cafeteria", "Abre la tercera cafetería de especialidad del barrio",
        "El café cuesta cuatro euros y el wifi va muy rápido.",
        frozenset({"centro", "marinero", "historico"}), nomad=1.25, find=1.05, index=1.02,
    ),
    BarrioNews(
        "pintadas", "Pintadas contra el turismo en las fachadas",
        "«Tu paraíso, nuestra miseria», en tres idiomas.",
        _TOURIST, vacation=0.85,
    ),
    BarrioNews(
        "triatlon", "Pasa el triatlón por el barrio",
        "Calles cortadas, bicis de cinco mil euros y gente en mallas a las siete.",
        frozenset({"playa", "lujo"}), vacation=1.15, neighbour=1.2,
    ),
    BarrioNews(
        "tormenta", "Tormenta: la lluvia de un mes en una semana",
        "Goteras, garajes inundados y el termo de la azotea, en la calle de abajo.",
        _ANY, _ANY, frozenset({10, 11, 12, 1, 2}), repairs=1.6,
    ),
    BarrioNews(
        "parque", "Inauguran un parque con zona para perros",
        "Columpios nuevos, árboles nuevos y un pipicán con césped artificial.",
        find=1.1,
    ),
    BarrioNews(
        "zona_azul", "Zona azul en todo el barrio",
        "Aparcar cuesta dos euros la hora. Los vecinos, indignados.",
        frozenset({"centro", "playa"}), find=0.95,
    ),
    BarrioNews(
        "fiestas", "Fiestas patronales con verbena hasta las seis",
        "La orquesta toca lo mismo que el año pasado, y se baila igual.",
        _ANY, _ANY, _SUMMER, neighbour=1.8, vacation=1.1,
    ),
    BarrioNews(
        "incendio", "Incendio forestal en el monte cercano",
        "Controlado, pero el humo llega a todas las casas.",
        frozenset({"rural"}), _ANY, _SUMMER, find=0.85, vacation=0.6,
    ),
    BarrioNews(
        "nomadas", "El barrio se pone de moda entre los nómadas digitales",
        "Sale en un vídeo con dos millones de visitas. El alquiler, también de moda.",
        frozenset({"playa", "centro", "marinero"}), nomad=1.4, index=1.01,
    ),
    BarrioNews(
        "zanja", "El Ayuntamiento levanta la calle por tercera vez este año",
        "Primero el agua, luego la luz y ahora la fibra. La misma zanja, tres veces.",
        find=0.9, neighbour=1.4,
    ),
    BarrioNews(
        "famoso", "Un futbolista famoso se compra un chalet en el barrio",
        "Los vecinos ya saben a qué hora sale a correr.",
        frozenset({"lujo", "playa"}), index=1.02, vacation=1.1,
    ),
    BarrioNews(
        "atun", "Vuelve el atún al muelle",
        "Buena temporada: los barcos vuelven llenos y en la cofradía se respira.",
        frozenset({"marinero"}), default=0.85, find=1.05,
    ),
    BarrioNews(
        "okupas_tele", "La tele dice que hay okupas en el barrio",
        "Un reportaje de cinco minutos. En la calle no ha visto nadie a ninguno.",
        find=0.95, squat=1.3, weight=0.6,
    ),
)  # fmt: skip

BARRIO_NEWS_BY_KEY: dict[str, BarrioNews] = {n.key: n for n in BARRIO_NEWS}


# -- Reformas (fase 4 de la ampliación) -----------------------------------------------------
#
# Las hace el dueño de un inmueble entero, con él vacío (`housing.start_reform`). Mientras
# duran, está «En obras». Al acabar suben la renta y el valor del inmueble en ese servidor (el
# valor, menos de lo que cuestan: reformar para vender pierde dinero) y bajan las averías.

#: Viviendas de un edificio entero: las reformas de precio fijo (una cocina) cuentan ocho.
BUILDING_UNITS = 8


@dataclass(frozen=True, slots=True)
class Reform:
    """Una reforma que se le puede hacer a un inmueble.

    Attributes:
        key: Clave estable.
        name: Nombre en el botón y en la pantalla.
        emoji: Su icono.
        kinds: Tipos de inmueble en que se puede hacer.
        per_m2: Coste en euros por metro cuadrado.
        fixed: Coste fijo en euros (por vivienda: un edificio cuenta `BUILDING_UNITS`).
        days: Días de obras (meses del juego).
        rent_bonus: Lo que sube la renta (0,05 = 5 %).
        breakdowns: Lo que multiplica las averías y las cerraduras.
        damp: Lo que multiplica las humedades.
        energy: Certificado energético que deja (vacío: no lo cambia).
        rehab: Si puede ser rehabilitación a efectos del IRPF (art. 41.1 del Reglamento).
        blurb: Qué incluye, en una línea.
    """

    key: str
    name: str
    emoji: str
    kinds: frozenset[str]
    per_m2: float
    fixed: float
    days: int
    rent_bonus: float
    breakdowns: float = 1.0
    damp: float = 1.0
    energy: str = ""
    rehab: bool = False
    blurb: str = ""


_DWELLINGS = frozenset(
    {"estudio", "bajo", "piso2", "piso3", "atico", "chalet", "edificio", "terrera", "cueva"}
    | {"finca", "casona", "bungalow", "duplex", "loft"}
)

REFORMS: tuple[Reform, ...] = (
    Reform("pintura", "Pintura y arreglos", "🎨", _DWELLINGS, 20, 0, 2, 0.03,
           blurb="Paredes, puertas y enchufes: lo que se ve en la primera visita."),
    Reform("cocina", "Cocina nueva", "🍳", _DWELLINGS, 0, 9_000, 5, 0.05, breakdowns=0.8,
           blurb="Muebles, encimera y electrodomésticos nuevos."),
    Reform("bano", "Baño nuevo", "🚿", _DWELLINGS, 0, 6_000, 4, 0.04, damp=0.5,
           blurb="Plato de ducha, alicatado y fontanería nueva: adiós a las humedades."),
    Reform("energia", "Eficiencia energética", "☀️", _DWELLINGS, 120, 0, 7, 0.04,
           breakdowns=0.9, energy="B",
           blurb="Ventanas, aislamiento y aerotermia: el certificado sube a la B."),
    Reform("integral", "Reforma integral", "🏗️", _DWELLINGS, 700, 0, 20, 0.12,
           breakdowns=0.4, damp=0.4, rehab=True,
           blurb="Estructura, fachada, cubierta e instalaciones: el piso como nuevo."),
    Reform("negocio", "Acondicionar el local", "🛠️", frozenset({"local", "nave"}), 150, 0, 7,
           0.08, breakdowns=0.8, blurb="Salida de humos, baños adaptados y escaparate."),
)  # fmt: skip

REFORM_BY_KEY: dict[str, Reform] = {r.key: r for r in REFORMS}
