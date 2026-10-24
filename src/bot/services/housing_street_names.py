"""Los datos de las calles de `pisos` (9.14.0): sus nombres y las frases de anuncio de las
unidades nuevas de sus edificios. La regla que los usa está en `bot.services.housing_streets`.

Cambiar una frase o el nombre de una calle no toca ninguna clave: solo se ve distinto.
"""

from __future__ import annotations

#: Las tres vías de cada barrio, `(nombre, qué es)`: la principal, una residencial y la del
#: puerto o el polígono (donde hay amarres o naves) u otra típica del barrio.
STREET_NAMES: dict[str, tuple[tuple[str, str], tuple[str, str], tuple[str, str]]] = {
    "isleta": (
        ("Calle Eufemiano Jurado", "Eje del barrio, con comercios y bloques de pescadores"),
        ("Calle Alfredo L. Jones", "Calle residencial entre el barrio y la zona portuaria"),
        ("Muelle Deportivo", "Dársena deportiva de Las Palmas, con sus pantalanes"),
    ),
    "canteras": (
        ("Calle Luis Morote", "Eje comercial y de ocio a pocos pasos de la playa"),
        ("Calle Sagasta", "Calle residencial de bloques entre Alcaravaneras y Las Canteras"),
        ("Paseo de Las Canteras", "Paseo marítimo a lo largo de toda la playa"),
    ),
    "schamann": (
        ("Avenida Pintor Felo Monzón", "Avenida con comercios y bloques de viviendas"),
        ("Calle Pintor Juan Ismael", "Calle residencial de bloques del barrio"),
        ("Calle Doctor Juan Domínguez Pérez", "Calle de bloques junto a Escaleritas"),
    ),
    "triana": (
        ("Calle Mayor de Triana", "Peatonal y comercial, de San Telmo a la plaza de Cairasco"),
        ("Calle Travieso", "Calle residencial del casco de Triana"),
        ("Calle Perojo", "Calle comercial con edificios de la época inglesa"),
    ),
    "vegueta": (
        ("Calle Mendizábal", "Calle principal de Vegueta, con bares y casas históricas"),
        ("Calle Espíritu Santo", "Calle residencial junto a la catedral de Santa Ana"),
        ("Calle Doctor Chil", "Calle histórica junto a la Casa de Colón y el Museo Canario"),
    ),
    "tafira": (
        ("Carretera de Tafira", "Vía principal de Tafira, con comercios y chalés"),
        ("Camino Viejo de Tafira", "Calle residencial de chalés y fincas"),
        ("Camino de la Calzada", "Vía de casas y fincas junto al Jardín Canario"),
    ),
    "telde": (
        ("Calle San Gregorio", "Calle principal del barrio, junto a la iglesia de San Gregorio"),
        ("Calle Doctor Rafael Cabrera", "Calle residencial del casco de Telde"),
        ("Calle Salinetas", "Calle del polígono industrial de Salinetas"),
    ),
    "arucas": (
        ("Calle León y Castillo", "Calle comercial del casco, junto a la iglesia de San Juan"),
        ("Calle Gourié", "Calle residencial junto a la plaza de la Constitución"),
        ("Calle Industrial", "Calle del polígono industrial de Arucas"),
    ),
    "maspalomas": (
        ("Avenida de Tirajana", "Eje comercial y turístico de Maspalomas"),
        ("Avenida de Italia", "Avenida de apartamentos y locales de Playa del Inglés"),
        ("Paseo Costa Canaria", "Paseo con apartamentos y hoteles junto al faro"),
    ),
    "mogan": (
        ("Avenida de Mogán", "Vía de acceso al puerto, con comercios y restaurantes"),
        ("Calle Playa de Mogán", "Calle de casas blancas con flores, junto a la playa"),
        ("Muelle de Puerto de Mogán", "Puerto deportivo con pantalanes y casitas"),
    ),
    "sanandres": (
        ("Calle Antonio Domínguez Alfonso", "Calle principal del pueblo, con comercios y bares"),
        ("Calle El Roque", "Calle residencial del casco marinero"),
        ("Muelle de Ribera", "Muelle de Santa Cruz, el puerto más cercano a San Andrés"),
    ),
    "laguna": (
        ("Calle San Agustín", "Calle histórica del casco, con casas señoriales"),
        ("Calle Obispo Rey Redondo", "Calle del centro histórico, junto a la catedral"),
        ("Calle Herradores", "Calle del casco antiguo con balcones de madera"),
    ),
    "santacruz": (
        ("Calle del Castillo", "Peatonal y principal calle comercial de la capital"),
        ("Calle Suárez Guerra", "Calle del centro, de bloques y comercios"),
        ("Calle Valentín Sanz", "Calle céntrica cerca del puerto, con edificios históricos"),
    ),
    "ofra": (
        ("Avenida Tres de Mayo", "Avenida con comercios y bloques que une Ofra con el centro"),
        ("Calle Doctor Allart", "Calle residencial de bloques de Ofra"),
        ("Calle Heliodoro Rodríguez López", "Calle del polígono Costa Sur, cercano a Ofra"),
    ),
    "taco": (
        ("Avenida de Taco", "Vía principal del barrio, con comercios y bloques"),
        ("Calle Pedro Suárez Hernández", "Calle residencial de bloques del barrio"),
        ("Calle Industrial de Taco", "Calle del polígono industrial de Taco"),
    ),
    "puertocruz": (
        ("Calle Quintana", "Calle comercial del casco, de la plaza del Charco a la Iglesia"),
        ("Calle Iriarte", "Calle residencial del centro, con casas tradicionales"),
        ("Paseo de San Telmo", "Paseo marítimo junto a la ermita de San Telmo"),
    ),
    "orotava": (
        ("Calle Carrera del Escultor Estévez", "Calle principal del casco, con casas señoriales"),
        ("Calle Tomás Zerolo", "Calle del casco histórico, con balcones de madera"),
        ("Calle San Francisco", "Calle de casas tradicionales en el centro histórico"),
    ),
    "medano": (
        ("Avenida Príncipes de España", "Eje comercial del pueblo, con restaurantes y tiendas"),
        ("Calle Chasna", "Calle residencial del casco de El Médano"),
        ("Calle Los Pescadores", "Calle de casas bajas cerca de la playa"),
    ),
    "cristianos": (
        ("Avenida Suecia", "Calle comercial y turística de Los Cristianos"),
        ("Calle Valle Menéndez", "Calle de bloques y apartamentos del casco"),
        ("Muelle de Los Cristianos", "Puerto con pantalanes deportivos y terminal de ferris"),
    ),
    "adeje": (
        ("Avenida de Bruselas", "Eje comercial y de ocio de Fañabé, con hoteles"),
        ("Avenida de Londres", "Avenida de apartamentos y urbanizaciones de lujo"),
        ("Muelle de Puerto Colón", "Marina de Costa Adeje, con pantalanes y yates"),
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
