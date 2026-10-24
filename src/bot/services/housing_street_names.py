"""Los datos de las calles de `pisos` (9.14.0): sus nombres y las frases de anuncio de las
unidades nuevas de sus edificios. La regla que los usa está en `bot.services.housing_streets`.

Cambiar una frase o el nombre de una calle no toca ninguna clave: solo se ve distinto.
"""

from __future__ import annotations

#: Las tres vías de cada barrio, `(nombre, qué es)`: la principal, una residencial y la del
#: puerto o el polígono (donde hay amarres o naves) u otra típica del barrio. Comprobadas en
#: OpenStreetMap (Nominatim y Overpass) el 24/10/2026; cuando el barrio no tiene puerto o
#: polígono propio, es el más cercano y la frase lo dice.
STREET_NAMES: dict[str, tuple[tuple[str, str], tuple[str, str], tuple[str, str]]] = {
    "isleta": (
        ("Calle Juan Rejón", "Calle principal de La Isleta, con comercios y bloques"),
        ("Calle Isaac Peral", "Calle residencial del casco de La Isleta"),
        ("Muelle Deportivo", "Muelle deportivo de Las Palmas, el más cercano a La Isleta"),
    ),
    "canteras": (
        ("Calle Luis Morote", "Eje comercial y de ocio a pocos pasos de la playa"),
        ("Calle Sagasta", "Calle residencial de bloques entre el puerto y Las Canteras"),
        ("Paseo de Las Canteras", "Paseo marítimo a lo largo de toda la playa"),
    ),
    "schamann": (
        ("Avenida de Escaleritas", "Avenida con comercios y bloques, en el límite de Schamann"),
        ("Calle Pintor Juan Ismael", "Calle residencial de bloques del barrio"),
        ("Calle Federico Viera", "Calle de bloques y viviendas del barrio"),
    ),
    "triana": (
        ("Calle Mayor de Triana", "Peatonal y comercial, de San Telmo a la plaza de Cairasco"),
        ("Calle Travieso", "Calle residencial del casco de Triana"),
        ("Calle Perojo", "Calle comercial junto a Triana, con edificios de principios del XX"),
    ),
    "vegueta": (
        ("Calle Mendizábal", "Calle principal de Vegueta, con comercios y casas históricas"),
        ("Calle Espíritu Santo", "Calle residencial junto a la catedral de Santa Ana"),
        ("Calle Doctor Chil", "Calle histórica junto a la Casa de Colón y el Museo Canario"),
    ),
    "tafira": (
        ("Carretera del Centro", "Eje de Tafira Baja (GC-110), con comercios y el campus"),
        ("Calle Patagonia", "Calle residencial de chalés y adosados de Tafira Baja"),
        ("Calle Saulo Torón", "Calle de casas y chalés de Tafira Baja"),
    ),
    "telde": (
        ("Calle Doctor Melián", "Calle comercial junto a San Gregorio, con bloques"),
        ("Calle Obispo Verdugo", "Calle residencial del casco de Telde, cerca de San Gregorio"),
        ("Calle Carpintero", "Calle del polígono de Salinetas, el más cercano a San Gregorio"),
    ),
    "arucas": (
        ("Calle León y Castillo", "Calle comercial del casco, junto a la iglesia de San Juan"),
        ("Calle Gourié", "Calle residencial del casco, junto a la plaza de la Constitución"),
        ("Calle Era de San Pedro", "Calle de la zona industrial, junto a la destilería del ron"),
    ),
    "maspalomas": (
        ("Avenida de Tirajana", "Eje comercial y turístico entre Playa del Inglés y Maspalomas"),
        ("Avenida de Italia", "Avenida de apartamentos y locales de Playa del Inglés"),
        ("Paseo Costa Canaria", "Paseo con apartamentos y hoteles hacia la Punta de Maspalomas"),
    ),
    "mogan": (
        ("Avenida de Las Artes", "Peatonal comercial del puerto, con tiendas y restaurantes"),
        ("Calle La Noria", "Calle residencial de casas blancas del pueblo marinero"),
        ("Paseo de Los Pescadores", "Paseo junto al puerto deportivo y sus pantalanes"),
    ),
    "sanandres": (
        ("Avenida Pedro Schwartz", "Calle peatonal y comercial del casco de San Andrés"),
        ("Calle de Nuestra Señora del Carmen", "Calle residencial del casco marinero"),
        ("Calle Dique", "Calle junto al dique y la dársena pesquera de San Andrés"),
    ),
    "laguna": (
        ("Calle San Agustín", "Calle histórica del casco, con comercios y casas señoriales"),
        ("Calle Obispo Rey Redondo", "Calle del centro histórico, junto a la catedral"),
        ("Calle Herradores", "Calle del casco antiguo de casas con balcones de madera"),
    ),
    "santacruz": (
        ("Calle del Castillo", "Peatonal y principal calle comercial de la capital"),
        ("Calle Suárez Guerra", "Calle del centro, de bloques y comercios"),
        ("Calle Valentín Sanz", "Calle céntrica entre El Toscal y la zona del puerto"),
    ),
    "ofra": (
        ("Calle Pedro Suárez Hernández", "Calle principal de Ofra, con comercios y bloques"),
        ("Calle Marcos Peraza y Vega", "Calle residencial de bloques de Ofra"),
        ("Calle Subida al Mayorazgo", "Calle del polígono de El Mayorazgo, junto a Ofra"),
    ),
    "taco": (
        ("Avenida de Taco", "Vía principal de Taco, con comercios y bloques"),
        ("Calle Padre Manjón", "Calle residencial de bloques de Taco"),
        ("Avenida Los Majuelos", "Avenida junto al polígono industrial de Los Majuelos"),
    ),
    "puertocruz": (
        ("Calle Quintana", "Calle comercial del casco antiguo del Puerto de la Cruz"),
        ("Calle Iriarte", "Calle residencial del casco, cerca del puerto pesquero"),
        ("Paseo de San Telmo", "Paseo marítimo junto a la ermita de San Telmo"),
    ),
    "orotava": (
        ("Calle Carrera del Escultor Estévez", "Calle principal del casco, con casas señoriales"),
        ("Calle Tomás Zerolo", "Calle del casco histórico, con balcones de madera"),
        ("Calle San Francisco", "Calle de casas tradicionales en el centro histórico"),
    ),
    "medano": (
        ("Avenida Príncipes de España", "Eje comercial del pueblo, con restaurantes y tiendas"),
        ("Avenida Chasna", "Avenida residencial de apartamentos y casas del pueblo"),
        ("Calle Pescadores", "Calle de casas bajas del núcleo pesquero, cerca de la playa"),
    ),
    "cristianos": (
        ("Avenida Suecia", "Calle comercial y turística de Los Cristianos"),
        ("Calle Valle Menéndez", "Calle de bloques y apartamentos del casco"),
        ("Paseo Rincón del Puerto", "Paseo junto al puerto de Los Cristianos y sus pantalanes"),
    ),
    "adeje": (
        ("Avenida de Bruselas", "Eje comercial y de ocio de Fañabé, con hoteles y restaurantes"),
        ("Calle Alemania", "Calle residencial de urbanizaciones de Costa Adeje"),
        ("Avenida de Colón", "Avenida de acceso a Puerto Colón, la marina de Costa Adeje"),
    ),
}

#: Las frases de anuncio de las unidades nuevas, por tipo (cada una toma una por su clave). La
#: mitad informan y la otra mitad tienen guasa, espaciadas.
UNIT_BLURBS: dict[str, tuple[str, ...]] = {
    "garaje": (
        "Plaza amplia, sin columnas que estorben al maniobrar.",
        "Cabe un coche normal. Un todoterreno, con fe y retrovisores plegados.",
        "Puerta automática con mando. El mando, de los de antes.",
        "En el barrio aparcar en la calle es deporte de riesgo: esto es el seguro.",
    ),
    "trastero": (
        "Trastero con puerta metálica y luz.",
        "Para la bici, la tabla y las cajas de la mudanza que nunca abriste.",
        "Seco y ventilado: nada de humedades en las cajas.",
        "Aquí cabe todo lo que no te atreves a tirar. Y la Thermomix.",
    ),
    "local": (
        "Local a pie de calle con escaparate y persiana.",
        "Salida de humos y baño adaptado: listo para abrir.",
        "Antes fue una zapatería, una tienda de móviles y un bazar. El siguiente puedes ser tú.",
        "Mucho paso de gente por delante a todas horas.",
    ),
    "bajo": (
        "Bajo con entrada propia y patio interior.",
        "Fresco en verano. En invierno, también.",
        "Sin escaleras: ideal para la abuela y para quien vuelve tarde.",
        "Ventanas a la calle; la vida del barrio, en directo.",
    ),
    "estudio": (
        "Estudio bien aprovechado, con cocina americana.",
        "Pequeño pero matón: todo a mano sin levantarte del sofá.",
        "Luminoso y fácil de alquilar.",
        "Para una persona y sus plantas. Las plantas, mejor pequeñas.",
    ),
    "piso2": (
        "Dos dormitorios, salón y cocina independiente.",
        "Ventanas a la calle y una vecina que lo sabe todo de todos.",
        "Buena distribución y armarios empotrados.",
        "Reformado hace poco: el baño ya no es de color salmón.",
    ),
    "piso3": (
        "Tres dormitorios, dos baños y terraza.",
        "Para familia, compañeros de piso o una colección de bicicletas.",
        "Exterior, con mucha luz por la tarde.",
        "Cocina grande, de las de comer en ella los domingos.",
    ),
    "atico": (
        "Ático con terraza y vistas despejadas.",
        "Arriba del todo: el ascensor te espera abajo cada mañana.",
        "Terraza con sitio para la barbacoa y la tendedera.",
        "Desde aquí se ve llegar la calima antes que en la tele.",
    ),
    "bungalow": (
        "Bungalow con terraza y piscina comunitaria.",
        "Una planta, jardín pequeño y tumbona de serie.",
        "A cinco minutos andando de la playa.",
        "La toalla en la tumbona a las siete: aquí se respeta la tradición.",
    ),
    "amarre": (
        "Amarre con agua y luz en el pantalán.",
        "Para una lancha o un velero mediano.",
        "Junto a la bocana: se sale rápido a navegar.",
        "El barco no está incluido. El olor a gasoil, sí.",
    ),
    "nave": (
        "Nave diáfana con muelle de carga.",
        "Techos altos y puerta para camiones.",
        "Oficina en entreplanta y aparcamiento propio.",
        "Cabe un almacén, un taller o la próxima gran startup del polígono.",
    ),
}
