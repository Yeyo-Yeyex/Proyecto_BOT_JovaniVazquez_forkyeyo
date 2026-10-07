"""El surtido de serie de El Colmado de Jovani.

Cada servidor arranca con estos artículos ya en el escaparate: la tienda los
mete la primera vez que alguien la abre (`Tienda.stock_up`) y apunta qué
claves ha metido ya (`shop_seeded`). A partir de ahí son artículos normales
del catálogo: los administradores los editan, rebajan, ocultan o retiran con
`catalogo`, y lo retirado no vuelve solo. Si una versión nueva del bot trae
artículos nuevos, entran en la siguiente apertura con la etiqueta 🆕.

Los roles no vienen de serie: dependen de los roles de cada servidor.

**Dos ejes, una pregunta cada uno.** Todo artículo se clasifica dos veces y
cada clasificación contesta a una sola pregunta:

- **Tipo** (`Kind`): ¿qué hace? Rol, potenciador de XP, objeto o mascota. Es
  la mecánica y da las pestañas del escaparate. Solo se crea un tipo nuevo
  cuando el artículo tiene estado o comportamiento que los demás no tienen.
- **Pasillo** (`AISLES`): ¿de qué va la broma? Es el tema o el origen (la
  Moncloa, Canarias, Hong Kong…), nunca la función. Un potenciador de
  Moncloa va en Moncloa, no en un pasillo «de potenciadores», porque eso ya
  lo dice la pestaña. Cada pasillo explica su tema en `Aisle.theme`.

Lo que antes resolvía un pasillo funcional sale ahora de los datos del
artículo y se enseña como etiqueta: si se usa (`use`), si se come (`food`,
para las mascotas), si es limitado (`stock`) o si pide nivel (`min_level`).

Las claves de pasillo no se cambian nunca (las cuentan los logros,
`shop_aisle_<clave>`). Un pasillo que se vacía va a `RETIRED_AISLES` con el
pasillo que hereda sus compras. Las reglas que tiene que cumplir el surtido
(tamaño de cada pasillo, que tenga algo que usar…) las comprueba
`tests/unit/test_shop_catalog.py`; la ficha de alta está en la Biblia.

**IGIC.** Cada artículo lleva el tipo que le tocaría en la vida real según la
Ley 4/2012 de Canarias (`bot.services.taxes.IGIC_RATES`): pan, huevos,
harina, fruta y libros al tipo cero; joyas, pieles y yates al 15 %; el coche
de menos de 11 CV al incrementado del 9,5 %; y casi todo lo demás al general
del 7 %. El oro de inversión va al cero porque está exento.

**Precios.** Escala general de la economía: 10 Y$ = 1 € y una tirada del
casino son 100 Y$ (Biblia, sección 4). Lo de comer y tirar cuesta unas
pocas tiradas; los caprichos, unos cuantos IMV; y el lujo pasa del mínimo
del Patrimonio (70.000 Y$), que es justo la gracia: lo que está en la vitrina
no lo ve el Patrimonio, que solo mira el saldo. Un sumidero de dinero, como
pide la Biblia para compensar el que se crea de la nada.

**Usos.** Los artículos con `use` se usan desde la `mochila`
(`bot.services.shop_uses`). Los que se gastan desaparecen al usarlos.

**Mascotas.** Salen de `bot.services.pets_catalog`: cada especie adoptable
es un artículo `Kind.PET` con la clave `mascota_<especie>` en el pasillo de
su tema. Las que aparecen solas también están, pero ocultas
(`visible=False`): así tienen un artículo al que apuntar en la mochila y un
valor para `patrimonio`, y nadie las puede comprar.

**Peña de la porra.** Lo del pasillo 🎫 sirve para las `porra`. Los prismáticos
y la libreta no se usan desde la mochila: con tenerlos, el cog de porras deja ver
quién apuesta qué y montar porras más largas (`bot.services.porras`, mismas
claves). No tocan el dinero de nadie.

**Herramientas de curro.** Las del pasillo 🛠️ Ferretería del curro no se usan
desde la mochila: con tenerlas, `pala` las aplica al minijuego del oficio
(`bot.services.work_tools`, con las mismas claves). Una por persona; cuestan
de 2 a 9 turnos del puesto 1, porque suben un poco la nota y con ella el
sueldo.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.pets_catalog import SPECIES, Species
from bot.services.shop import Kind
from bot.services.shop_uses import USES, Use


@dataclass(frozen=True, slots=True)
class Aisle:
    """Un pasillo del colmado.

    Attributes:
        key: Clave estable (la cuentan los logros); no se cambia nunca.
        theme: De qué va el pasillo. Decide qué entra y qué no.
    """

    key: str
    emoji: str
    name: str
    theme: str = ""

    @property
    def label(self) -> str:
        """`🌴 Canarias`."""
        return f"{self.emoji} {self.name}"


AISLES: tuple[Aisle, ...] = (
    Aisle("moncloa", "🌹", "La Moncloa", "El Gobierno, los partidos, la UCO y sus escándalos."),
    Aisle("ventanilla", "📎", "Ventanilla", "Burocracia, Hacienda, oposiciones y papeleo."),
    Aisle("canarias", "🌴", "Canarias", "Lo de las islas: gofio, guaguas, el Pino y el Teide."),
    Aisle(
        "espana",
        "🥘",
        "Typical Spanish",
        "La España de siempre: el bar, la madre, el tupper y el Seat Ibiza.",
    ),
    Aisle(
        "cotillon", "🎉", "Fiestas y verbenas", "Celebrar, regalar y lanzar cosas en las fiestas."
    ),
    Aisle(
        "amuletos",
        "🍀",
        "Supersticiones",
        "Suerte, fe y mal fario. No cambian ninguna probabilidad.",
    ),
    Aisle(
        "bazar", "🧰", "Bazar de todo a 100", "Cacharros del chino de abajo y trastos sin dueño."
    ),
    Aisle("boricua", "🇵🇷", "Rincón boricua", "Puerto Rico y las cosas de Jovani."),
    Aisle("hongkong", "🇭🇰", "Importación de Hong Kong", "Lo que trae Robuso en la maleta."),
    Aisle("lujo", "💎", "Vida de rico", "Joyas, yates y caprichos que no ve el Patrimonio."),
    Aisle("shitpost", "💀", "Lo que no debería venderse", "Memes, absurdos y estafas honestas."),
    Aisle(
        "animales", "🐾", "Tienda de animales", "Mascotas sin tema propio, su comida y sus cosas."
    ),
    Aisle(
        "curro",
        "🛠️",
        "Ferretería del curro",
        "El trabajo: lo que te llevas a `pala` para currar mejor.",
    ),
    Aisle(
        "porra",
        "🎫",
        "Peña de la porra",
        "El bar de las apuestas entre colegas: la porra, la quiniela y el árbitro.",
    ),
)
#: Pasillo de lo que crean los administradores con `catalogo`.
HOUSE_AISLE = Aisle("casa", "🏷️", "De la casa", "Lo que añaden los administradores.")
AISLE_BY_KEY: dict[str, Aisle] = {a.key: a for a in (*AISLES, HOUSE_AISLE)}
#: Pasillos que ya no existen y el que hereda sus compras para los logros
#: (`None` si sus artículos se repartieron y no hay un heredero claro).
#: Ultramarinos y la Farmacia de guardia eran pasillos de función (comida y
#: potenciadores), no de tema: se disolvieron en la auditoría de 2026.
RETIRED_AISLES: dict[str, str | None] = {"ultramarinos": "espana", "farmacia": None}
#: Mínimo de artículos a la venta en cada pasillo (si no llega, va dentro de otro).
MIN_AISLE_SIZE = 8


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Un artículo del surtido de serie.

    Attributes:
        key: Clave estable. Es lo que une el artículo de cada servidor con
            este surtido (`shop_items.catalog_key`); no se cambia nunca.
        multiplier: XP en tanto por cien (solo potenciadores).
        duration: Segundos que dura (solo potenciadores).
        use: Clave de `bot.services.shop_uses.USES` si se puede usar.
        food: Si se come: las mascotas lo aceptan en `mascota`.
        visible: Si se vende. Las mascotas que aparecen solas están ocultas.
    """

    key: str
    kind: Kind
    aisle: str
    emoji: str
    name: str
    price: int
    description: str
    igic: str = "general"
    multiplier: int | None = None
    duration: int | None = None
    stock: int | None = None
    per_user: int | None = None
    min_level: int = 0
    use: str | None = None
    food: bool = False
    visible: bool = True

    @property
    def usage(self) -> Use | None:
        """Cómo se usa, si se puede usar."""
        return USES.get(self.use) if self.use else None


H = 3600
D = 24 * H


def _t(
    key: str,
    aisle: str,
    emoji: str,
    name: str,
    price: int,
    description: str,
    *,
    igic: str = "general",
    stock: int | None = None,
    per_user: int | None = None,
    level: int = 0,
    use: str | None = None,
    food: bool = False,
) -> CatalogEntry:
    """Un objeto (coleccionable, usable o comestible)."""
    return CatalogEntry(
        key=key,
        kind=Kind.TROPHY,
        aisle=aisle,
        emoji=emoji,
        name=name,
        price=price,
        description=description,
        igic=igic,
        stock=stock,
        per_user=per_user,
        min_level=level,
        use=use,
        food=food,
    )


def _b(
    key: str,
    aisle: str,
    emoji: str,
    name: str,
    price: int,
    multiplier: int,
    duration: int,
    description: str,
    *,
    igic: str = "general",
    per_user: int | None = None,
    level: int = 0,
) -> CatalogEntry:
    """Un potenciador de XP."""
    return CatalogEntry(
        key=key,
        kind=Kind.BOOST,
        aisle=aisle,
        emoji=emoji,
        name=name,
        price=price,
        description=description,
        igic=igic,
        multiplier=multiplier,
        duration=duration,
        per_user=per_user,
        min_level=level,
    )


def _pet(species: Species) -> CatalogEntry:
    """El artículo de una especie de mascota (oculto si aparece sola)."""
    return CatalogEntry(
        key=species.catalog_key,
        kind=Kind.PET,
        aisle=species.aisle,
        emoji=species.emoji,
        name=species.name,
        price=species.price,
        description=species.description,
        stock=species.stock,
        # Una de cada especie por persona: se colecciona, no se acumula.
        per_user=1,
        min_level=species.level,
        visible=species.adoptable,
    )


# fmt: off
CATALOG: tuple[CatalogEntry, ...] = (
    # 🌹 La Moncloa -----------------------------------------------------------------------
    _t("manual_resistencia", "moncloa", "📕", "Manual de resistencia", 2_000,
       "Edición de bolsillo, firmada por quien lo escribiera de verdad. Es un libro: IGIC "
       "tipo cero. Hasta Perro Sanxe respeta la cultura cuando es la suya.", igic="cero"),
    _t("falcon", "moncloa", "✈️", "Falcon de Moncloa (tamaño real)", 50_000_000,
       "Para ir a un concierto, a una boda o a por pan. El combustible lo pone el "
       "contribuyente, que eres tú. Una sola unidad en todo el servidor.",
       igic="lujo", stock=1, level=50),
    _t("maqueta_falcon", "moncloa", "🛩️", "Maqueta del Falcon", 9_000,
       "Escala 1:72. Despega igual de a menudo que el de verdad, pero sin factura de "
       "queroseno."),
    _t("rosa_puno", "moncloa", "🌹", "Rosa del puño", 500,
       "Recién cortada en Ferraz. Pincha más de lo que parece."),
    _t("gafas_moncloa", "moncloa", "🕶️", "Gafas de sol del vídeo a cámara lenta", 3_500,
       "Las de bajar del avión mirando al horizonte. +100 de aura, -100 de "
       "credibilidad."),
    _t("carta_ciudadania", "moncloa", "✉️", "Carta a la ciudadanía", 1_200,
       "Cinco días de reflexión en una sola hoja. Trae un hueco en blanco para que "
       "escribas tu propio «he decidido seguir»."),
    _t("atril", "moncloa", "🎙️", "Atril de rueda de prensa", 7_000,
       "Con el folio ya puesto. Viene sin micrófono para los periodistas, de serie."),
    _t("lupa_uco", "moncloa", "🔍", "Lupa de la UCO", 4_000,
       "Aumenta 20 veces cualquier factura de mascarillas. Imprescindible en los "
       "registros de los martes."),
    _t("movil_pegasus", "moncloa", "📱", "Móvil con Pegasus de serie", 15_000,
       "Trae el espía preinstalado, como los de los ministros. La batería dura poco "
       "porque la está usando otro."),
    _t("escano", "moncloa", "🏛️", "Escaño del Congreso (de cartón)", 30_000,
       "Hay 350, como en el de verdad. Cada uno numerado. Votar no vota, pero aplaude.",
       stock=350, per_user=1),
    _t("factura_mascarillas", "moncloa", "🧾", "Factura de mascarillas", 6_660,
       "Original, con un sobrecoste del 400 % y una comisión para un primo de alguien. "
       "Precio: el número de la bestia, con un cero de comisión."),
    _t("peluche_sanxe", "moncloa", "🐶", "Peluche de Perro Sanxe", 2_500,
       "Achuchable y recaudatorio. Si lo dejas encima del sofá, a la mañana siguiente "
       "te falta un 19 % de los cojines."),
    _t("primera_piedra", "moncloa", "🏗️", "Primera piedra de una obra", 8_000,
       "La pusieron con banda de música y fotógrafos. La segunda piedra está prevista "
       "para la próxima legislatura. O la siguiente."),
    _t("boli_boe", "moncloa", "🖊️", "Bolígrafo del BOE", 900,
       "Con él se firman los decretos ley de los viernes por la tarde. La tinta se "
       "borra sola cuando cambia el Gobierno."),
    _t("rata_ferraz", "moncloa", "🐀", "Rata de Ferraz", 300,
       "Vive en las cañerías de la sede desde 1982. Ha visto cosas que no puede contar. "
       "Tampoco quiere."),
    _t("chivatazo_uco", "moncloa", "🚨", "Chivatazo a la UCO", 1_500,
       "Un número de teléfono y un nombre. La UCO hace el resto. A veces, a quien no "
       "era.", use="uco"),
    _t("licencia_pegasus", "moncloa", "🛰️", "Licencia de Pegasus", 2_500,
       "Un uso. Lee el último mensaje de quien quieras. Se lo inventa, claro, pero con "
       "mucha seguridad.", use="pegasus"),
    _t("indulto", "moncloa", "📜", "Indulto", 5_000,
       "Perdona lo imperdonable. Se publica en el BOE de madrugada para que no se entere "
       "nadie.", use="indulto"),
    _t("bulo", "moncloa", "🗞️", "Bulo de la máquina del fango", 300,
       "Un titular falso recién salido del horno. Úsalo con responsabilidad (no lo vas a "
       "hacer).", use="bulo"),
    _t("encuesta_cis", "moncloa", "📊", "Encuesta del CIS", 400,
       "La cocina es casera y el resultado siempre sale igual. Margen de error: el que "
       "haga falta.", use="cis"),
    _t("lote_mascarillas", "moncloa", "😷", "Lote de mascarillas", 1_200,
       "Mil unidades, sin homologar, a precio de oro. Véndeselas a quien quieras con un "
       "sobrecoste de escándalo.", use="mascarillas"),
    _t("rueda_prensa", "moncloa", "🎤", "Rueda de prensa sin preguntas", 700,
       "Convoca a los medios, lee un folio y vete sin mirar atrás.", use="rueda_prensa"),
    _b("turbo_moncloa", "moncloa", "🚀", "Turbo de Moncloa", 2_000, 200, H,
       "El mismo empujón que un decreto ley un viernes por la tarde: rápido, sin debate "
       "y con efecto inmediato."),
    _b("combustible_falcon", "moncloa", "🛫", "Combustible del Falcon", 9_000, 200, 6 * H,
       "Queroseno de primera para volar bajo el radar. Seis horas a doble de XP.",
       level=10),
    _b("fondos_ue", "moncloa", "🇪🇺", "Fondos europeos", 12_000, 150, D,
       "Llegan tarde, se reparten a dedo y nadie sabe en qué se gastan. Pero suman XP "
       "todo el día."),

    # 📎 Ventanilla -----------------------------------------------------------------------
    _t("burofax", "ventanilla", "📮", "Burofax", 400,
       "Para comunicar algo con valor legal, mala cara y acuse de recibo.", use="burofax"),
    _t("multa_dgt", "ventanilla", "🚓", "Talonario de multas de la DGT", 200,
       "Una hoja. Multa a quien quieras por lo que quieras. Recurrir no sirve de nada, "
       "como en la vida real.", use="multa"),
    _t("dni_falso", "ventanilla", "🪪", "DNI falso", 3_000,
       "Cámbiate el apodo del servidor. Nadie en la ventanilla levanta la vista.",
       use="dni_falso"),
    _t("modelo_100", "ventanilla", "📄", "Modelo 100 en blanco", 100,
       "Para que practiques. Hacienda ya tiene el tuyo relleno, con tus datos y con sus "
       "errores."),
    _t("fax_ss", "ventanilla", "📠", "Fax de la Seguridad Social", 2_000,
       "Sigue siendo el método oficial en algún ministerio. Hace un ruido que asusta a "
       "los becarios."),
    _t("dni_caducado", "ventanilla", "🆔", "DNI caducado", 150,
       "Caducó en 2021. La cita para renovarlo te la dan para 2027."),
    _t("cita_previa", "ventanilla", "🗓️", "Cita previa para dentro de 4 meses", 1_000,
       "Lo caro de la Administración no es el trámite: es la cita. Si llegas un minuto "
       "tarde, vuelta a empezar.", per_user=1),
    _t("certificado_padron", "ventanilla", "📑", "Certificado de empadronamiento", 80,
       "Prueba de que vives donde vives. Válido tres meses. Luego vives en otro sitio, "
       "legalmente hablando."),
    _t("numero_sepe", "ventanilla", "🎟️", "Turno 33 del SEPE", 33,
       "Van por el 2. Trae bocadillo."),
    _t("sello_oficial", "ventanilla", "🔏", "Sello oficial (no oficial)", 2_200,
       "Pone «COMPULSADO» en letras rojas. No compulsa nada, pero impone."),
    _t("asesor", "ventanilla", "💼", "Plaza de asesor sin funciones", 25_000,
       "Sueldo público, despacho propio y cero tareas. Requiere carnet del partido (no "
       "lo comprobamos). Veinte plazas.", stock=20, per_user=1),
    _b("oposicion", "ventanilla", "🧠", "Plaza de oposición aprobada", 60_000, 150, 7 * D,
       "Una semana entera a ×1,5. Plaza fija: solo una por persona, como en la vida "
       "real (y no en la vida real).", per_user=1, level=20),

    # 🌴 Canarias -------------------------------------------------------------------------
    _t("saco_gofio", "canarias", "🌾", "Saco de gofio de millo", 300,
       "Del molino de toda la vida. Pa'l escaldón, pa'l potaje o a cucharadas, como los "
       "valientes. Es harina: IGIC tipo cero.", igic="cero", food=True),
    _t("pella_gofio", "canarias", "🍙", "Pella de gofio", 60,
       "Amasada con agua, azúcar y cariño. También se puede lanzar. Sobre todo se puede "
       "lanzar.", igic="cero", use="gofio", food=True),
    _t("platano", "canarias", "🍌", "Plátano de Canarias", 80,
       "Con sus motitas, que es como se sabe que es de aquí. Te lo comes y la piel la "
       "dejas donde pise alguien.", igic="cero", use="platano", food=True),
    _t("queso_guia", "canarias", "🧀", "Queso de flor de Guía", 1_800,
       "Cuajado con flor de cardo, como se hace desde hace siglos en el norte de Gran "
       "Canaria. Para la vitrina o para un trozo a escondidas.", food=True),
    _t("vino_geria", "canarias", "🍷", "Vino de La Geria", 1_400,
       "Malvasía volcánica de Lanzarote, de viñas plantadas en hoyos de picón. Sabe a "
       "lava, en el buen sentido."),
    _t("timple", "canarias", "🎸", "Timple", 3_000,
       "Cinco cuerdas y todo el folclore canario. Se toca las veces que quieras, con un "
       "minuto de descanso para los vecinos.", use="timple"),
    _t("piedra_teide", "canarias", "🌋", "Piedra volcánica del Teide", 250,
       "Sacarla del Parque Nacional está prohibido. Esta la cogió un turista y nosotros "
       "solo la revendemos. Hecha la ley, hecha la trampa."),
    _t("frasco_calima", "canarias", "🌫️", "Frasco de calima", 150,
       "Aire de Canarias un día de calima: 100 % polvo del Sáhara, 0 % visibilidad. "
       "Ábrelo con mascarilla."),
    _t("bocina_guagua", "canarias", "🚌", "Bocina de guagua", 2_200,
       "Arrancada de una guagua que bajaba por la GC-1. Se pita las veces que quieras; "
       "la guagua, en cambio, no viene.", use="bocina"),
    _t("lagarto_hierro", "canarias", "🦎", "Lagarto gigante de El Hierro", 2_200,
       "De peluche. El de verdad está en peligro de extinción y no se vende, mi amor."),
    _t("miel_palma", "canarias", "🍯", "Miel de palma de La Gomera", 1_300,
       "Guarapo de palmera cocido a fuego lento. Para el queso asado, para el "
       "frangollo o para untar el micrófono de Jovani.", food=True),
    _t("mojo_picon", "canarias", "🌶️", "Tarro de mojo picón", 200,
       "Para las papas arrugadas o para untárselo a alguien en la cara. Pica de "
       "verdad.", use="mojo"),
    _t("bienmesabe", "canarias", "🍮", "Bienmesabe de Tejeda", 700,
       "Almendra, miel y huevo. Su nombre es una descripción técnica.", food=True),
    _t("vela_pino", "canarias", "🕯️", "Vela de la Virgen del Pino", 120,
       "De la basílica de Teror. Enciéndela y pide lo que quieras: salud, trabajo o que "
       "salga el rojo.", use="vela"),
    _t("invitacion_barraquito", "canarias", "☕", "Vale por un barraquito", 250,
       "Invita a alguien a un barraquito: leche condensada, Licor 43, café, leche, "
       "canela y limón, en capas.", use="barraquito"),
    _t("parcela_graciosa", "canarias", "🏝️", "Parcela en La Graciosa", 250_000,
       "Calles de arena, cero coches y vistas a Lanzarote. Solo hay diez y nadie sabe "
       "si se puede edificar. Spoiler: no.", stock=10, level=30),
    _t("palmera", "canarias", "🌴", "Palmera canaria en maceta", 1_900,
       "Phoenix canariensis, la de las fotos. En maceta, para el salón. Crece un "
       "centímetro por cada IMV cobrado."),
    _t("chalet_tafira", "canarias", "🏡", "Chalet en Tafira", 2_000_000,
       "Jardín, piscina y vistas a Las Palmas. Cinco unidades. El IBI no está incluido "
       "y el Ayuntamiento lo sabe.", stock=5, level=35),
    _b("barraquito_xp", "canarias", "🧉", "Barraquito doble", 600, 125, 2 * H,
       "Leche condensada, Licor 43, café y canela, en capas. Si lo remueves antes de la "
       "foto, Perro Sanxe te multa."),
    _b("gofio_platano", "canarias", "🥣", "Gofio con plátano y leche", 1_800, 140, 4 * H,
       "Desayuno de campeones canarios. Cuatro horas de XP extra y energía para subir "
       "el Roque Nublo."),

    # 🥘 Typical Spanish ------------------------------------------------------------------
    _t("huevo", "espana", "🥚", "Huevo de corral", 50,
       "La docena está a 3 €. Este vale 5 € porque es de gallina feliz y porque se puede "
       "lanzar. Huevo: IGIC tipo cero.", igic="cero", use="huevo", food=True),
    _t("tomate", "espana", "🍅", "Tomate de la Tomatina", 40,
       "Maduro, blandito y con muchas ganas de reventar en la cara de alguien.",
       igic="cero", use="tomate", food=True),
    _t("barra_pan", "espana", "🥖", "Barra de pan de ayer", 30,
       "Dura como una porra. El pan va al tipo cero del IGIC, así que esta arma blanca "
       "sale sin impuestos.", igic="cero", use="barra_pan", food=True),
    _t("padron", "espana", "🫑", "Pimiento de Padrón", 90,
       "Unos pican y otros no. Este puede que sí. Uno de cada cinco, más o menos.",
       igic="cero", use="padron", food=True),
    _t("paella", "espana", "🥘", "Paella del domingo", 1_500,
       "Para todo el canal, con socarrat. El chorizo está prohibido por la "
       "convención de Ginebra de la paella.", use="paella", food=True),
    _t("tortilla", "espana", "🥔", "Tortilla de patatas", 280,
       "¿Con o sin cebolla? Ábrela y lo sabrás. El canal lo sabrá. España lo sabrá.",
       use="tortilla", food=True),
    _t("tupper", "espana", "🍲", "Tupper de mamá", 200,
       "Lleno de lentejas y de expectativas. Devuélvelo, que tu madre lo está "
       "esperando desde 2019.", use="tupper", food=True),
    _t("aove", "espana", "🫒", "Aceite de oliva virgen extra (1 L)", 900,
       "Oro líquido. En 2024 le quitaron hasta el IVA en la península y aun así no había "
       "quien lo pagara. Alimento básico: tipo cero.", igic="cero", food=True),
    _t("fabada", "espana", "🥫", "Lata de fabada", 220,
       "Fabes, chorizo, morcilla y lacón. Para la vitrina, que en verano no se puede.", food=True),
    _t("brik_vino", "espana", "🧃", "Tetrabrik de vino", 120,
       "Cosecha: sí. Denominación de origen: la gasolinera. Ideal para el kalimotxo y "
       "para la autoestima."),
    _t("bocata_calamares", "espana", "🥪", "Bocadillo de calamares", 350,
       "Recién hecho en la Plaza Mayor. Lo de que sean anillas de cebolla es un bulo de "
       "la máquina del fango.", food=True),
    _t("chancla", "espana", "🩴", "Chancla de madre", 300,
       "Teledirigida, de goma maciza y con 40 años de experiencia. No ha fallado nunca.",
       use="chancla"),
    _t("cd_gasolinera", "espana", "💿", "CD de la gasolinera", 300,
       "Lo mejor de la ruta del bakalao en un CD-R que solo suena en el coche de tu tío. "
       "La pista 7 salta."),
    _t("seat_ibiza", "espana", "🚗", "Seat Ibiza tuneado (2003)", 120_000,
       "Alerón, llantas de 17 y un subwoofer que hace temblar la ITV. Menos de 11 CV: "
       "IGIC incrementado del 9,5 %.", igic="incrementado", level=10),
    _b("cortado", "espana", "☕", "Cortado de máquina", 150, 110, H,
       "El de la máquina del pasillo: sabe a vaso de plástico, pero espabila. +10 % de "
       "XP durante una hora."),
    _b("caramelo_miel", "espana", "🍬", "Caramelo de miel y limón", 300, 115, 2 * H,
       "Para la garganta de tanto escribir en el chat. Un empujoncito suave."),
    _b("churros", "espana", "🍫", "Chocolate con churros", 1_100, 130, 3 * H,
       "De madrugada, después de una noche larga. Recarga el cuerpo y el XP."),
    _b("anabolizantes", "espana", "🦾", "Batido de gimnasio de barrio", 6_000, 300, H,
       "Triple de XP durante una hora. El dueño del gimnasio dice que es proteína. El "
       "dueño del gimnasio miente.", level=15),
    _b("abono_cafe", "espana", "🗓️", "Abono mensual de café", 25_000, 110, 30 * D,
       "Un mes entero con un 10 % más de XP. Para los que viven en el chat."),

    # 🎉 Fiestas y verbenas ---------------------------------------------------------------
    _t("cava", "cotillon", "🍾", "Botella de cava", 650,
       "Para celebrar lo que sea, aunque no haya nada que celebrar. Cuidado con el "
       "tapón.", use="cava"),
    _t("tarta_nata", "cotillon", "🍰", "Tarta de nata", 450,
       "No es para comer. Bueno, puede ser para comer. Pero no es para comer.",
       use="tarta", food=True),
    _t("uvas", "cotillon", "🍇", "Doce uvas de Nochevieja", 120,
       "Peladas y sin pepitas, para no morir en el intento. Fruta: IGIC tipo cero.",
       igic="cero", use="uvas", food=True),
    _t("petardo", "cotillon", "🧨", "Petardo de Fallas", 100,
       "Mecha corta y mala leche. No lo enciendas cerca de la cara, que nos conocemos.",
       use="petardo"),
    _t("confeti", "cotillon", "🎊", "Bolsa de confeti", 60,
       "Para celebrar cualquier cosa. Lo vais a seguir encontrando en el sofá dentro de "
       "cinco años.", use="confeti"),
    _t("globo_agua", "cotillon", "🎈", "Globo de agua", 45,
       "Lleno hasta arriba. Que no se te reviente en la mano.", use="globo"),
    _t("ramo_flores", "cotillon", "💐", "Ramo de flores", 500,
       "Para alguien especial. O para alguien que te debe un Bizum, por si se ablanda.",
       use="ramo"),
    _t("vale_abrazo", "cotillon", "🤗", "Vale por un abrazo", 100,
       "Canjeable por un abrazo de los buenos. Sin IGIC de cariño, pero con el de "
       "siempre.", use="abrazo"),
    _t("carta_amor", "cotillon", "💌", "Carta de amor anónima", 300,
       "Le llega a quien tú quieras y no dice quién la manda. Solo lo sabes tú. Y el "
       "bot. Y la UCO.", use="carta"),
    _t("pinata_sanxe", "cotillon", "🪅", "Piñata de Perro Sanxe", 1_000,
       "Dentro hay caramelos y un 19 % de retención. Para la vitrina: si la rompes, "
       "Hacienda te cobra los caramelos."),
    _t("vuvuzela", "cotillon", "📯", "Vuvuzela", 1_000,
       "Recuerdo del Mundial de 2010. Se toca las veces que quieras, con un minuto de "
       "descanso para los tímpanos ajenos.", use="vuvuzela"),

    # 🍀 Supersticiones -------------------------------------------------------------------
    _t("galleta_suerte", "amuletos", "🥠", "Galleta de la suerte", 70,
       "Con mensaje dentro. Los mensajes los escribe un becario sin contrato.",
       use="galleta", food=True),
    _t("sal_gorda", "amuletos", "🧂", "Sal gorda", 25,
       "Para el pescado a la sal o para echársela a alguien por encima del hombro. Mal "
       "fario incluido.", use="sal"),
    _t("agua_bendita", "amuletos", "💦", "Agua bendita", 330,
       "Bendice a quien quieras. Si sale humo, avisa a un cura.", use="agua_bendita"),
    _t("bola8", "amuletos", "🎱", "Bola 8 mágica", 2_000,
       "Pregúntale lo que quieras y agítala. Acierta más que las encuestas. Usos "
       "ilimitados.", use="bola8"),
    _t("trebol", "amuletos", "🍀", "Trébol de cuatro hojas", 777,
       "No cambia ni una sola probabilidad del casino. Como los de verdad.", use="trebol"),
    _t("herradura", "amuletos", "🧲", "Herradura", 450,
       "De un caballo de la Feria de Abril. Cuélgala con las puntas hacia arriba, que si "
       "no se escapa la suerte (y la casa no devuelve)."),
    _t("calzoncillos_rojos", "amuletos", "🩲", "Ropa interior roja de Nochevieja", 1_231,
       "Estrenada el 31 de diciembre, como manda la tradición. Precio: 12/31. Es nueva, "
       "tranquilo.", use="calzoncillos"),
    _t("ojo_turco", "amuletos", "🧿", "Ojo turco", 300,
       "Protege del mal de ojo, de la envidia y de la sal gorda que te eche nadie."),
    _t("rosario", "amuletos", "📿", "Rosario de la abuela", 600,
       "Ha rezado por ti más veces de las que crees. Sobre todo cuando apuestas.", use="rosario"),
    _t("pata_conejo", "amuletos", "🐇", "Pata de conejo de peluche", 350,
       "De peluche, que el conejo de verdad la necesitaba más que tú."),
    _t("decimo_2019", "amuletos", "🎫", "Décimo de Navidad de 2019", 200,
       "No premiado. Lo guardaste por si acaso. Por si acaso, no."),
    _t("san_pancracio", "amuletos", "🙏", "Estampita de San Pancracio", 250,
       "Con perejil, que si no no funciona. Patrón de la salud y del trabajo; de la "
       "ruleta, no.", use="pancracio"),

    # 🧰 Bazar de todo a 100 --------------------------------------------------------------
    _t("extintor", "bazar", "🧯", "Extintor", 800,
       "Para apagar fuegos, polémicas y discusiones sobre la tortilla.", use="extintor"),
    _t("d20", "bazar", "🎲", "Dado de 20 caras", 1_200,
       "Para decidir cosas importantes, como si apostar al rojo o al negro. Un 1 es una "
       "pifia; un 20, gloria eterna.", use="d20"),
    _t("bumeran", "bazar", "🪃", "Bumerán", 400,
       "Lánzaselo a alguien y prepárate para lo que vuelva. Si vuelve.", use="bumeran"),
    _t("megafono", "bazar", "📢", "Megáfono", 1_000,
       "Grita lo que quieras en el canal, bien grande, una vez. Sin menciones, que nos "
       "conocemos.", use="megafono"),
    _t("caja_botin", "bazar", "🎁", "Caja botín del colmado", 2_500,
       "Dentro hay un coleccionable al azar. Puede ser un yate. Suele ser un calcetín. "
       "Como las de los videojuegos, pero con IGIC.", use="caja"),
    _t("papel_higienico", "bazar", "🧻", "Rollo de papel higiénico de 2020", 200,
       "Superviviente del acaparamiento de marzo de 2020. Vale más por lo que "
       "representa que por lo que hace."),
    _t("linterna_apagon", "bazar", "🔦", "Linterna del apagón", 1_000,
       "Del 28 de abril de 2025, cuando se fue la luz en toda la península. Canarias ni "
       "se enteró. La linterna tampoco sabe quién la apagó."),
    _t("calcetin", "bazar", "🧦", "Calcetín desparejado", 10,
       "Su pareja se perdió en la lavadora en 2017. Sigue esperando."),
    _t("pila_gastada", "bazar", "🪫", "Pila gastada", 15,
       "No sirve para nada, pero tirarla al contenedor de pilas da pereza. Quédatela."),
    _t("carrito", "bazar", "🛒", "Carrito del súper con una rueda loca", 750,
       "Siempre tira a la izquierda. Como algunos. O a la derecha. Depende del día."),
    _t("planta_plastico", "bazar", "🪴", "Planta de plástico de oficina", 400,
       "No hay que regarla y aun así se muere de pena cada lunes."),
    _t("paraguas_chino", "bazar", "☂️", "Paraguas de bazar", 150,
       "Aguanta una lluvia. Una. Luego se da la vuelta solo y se va volando a Fuerteventura."),
    _t("busca", "bazar", "📟", "Busca de 1998", 600,
       "El aparato que pitaba antes de los móviles. Alguien todavía le manda mensajes "
       "desde una cabina."),
    _b("lata_energetica", "bazar", "🥤", "Lata de bebida energética", 900, 150, H,
       "Taurina, cafeína y una advertencia en letra pequeña que nadie lee."),
    _b("bateria", "bazar", "🔋", "Batería externa", 5_000, 120, D,
       "Veinticuatro horas de carga lenta pero segura. Como la burocracia, pero a tu "
       "favor."),

    # 🇵🇷 Rincón boricua ------------------------------------------------------------------
    _t("bandera_pr", "boricua", "🇵🇷", "Bandera de Puerto Rico", 500,
       "Pa' colgarla en el balcón y que se entere el barrio entero. ¡Wepa!"),
    _t("micro_jovani", "boricua", "🎤", "Micro de Jovani", 7_770,
       "Sin cable, sin pilas y sin autotune. Bueno, con autotune. Edición numerada de "
       "50.", stock=50),
    _t("coquito", "boricua", "🥥", "Coquito", 450,
       "El ponche boricua de Navidad: coco, ron y canela. Se toma en vaso chiquito; "
       "Jovani lo toma en jarra."),
    _t("coqui", "boricua", "🐸", "Coquí de peluche", 600,
       "La ranita de Puerto Rico. Canta «co-quí» toda la noche, como tu vecino con el "
       "reguetón."),
    _t("perreo", "boricua", "💃", "Vale por un perreo", 350,
       "Saca a perrear a quien tú quieras. Si te dice que no, el vale se gasta igual.",
       use="perreo"),
    _t("altavoz", "boricua", "🔊", "Altavoz con reguetón", 2_500,
       "Dembow a todo volumen las veces que quieras, con un minuto de descanso para que "
       "no llamen a la policía.", use="reggaeton"),
    _t("pava", "boricua", "👒", "Pava jíbara", 900,
       "El sombrero de paja del campesino de Puerto Rico. Jovani se lo pone para los "
       "videoclips del campo."),
    _b("pina_colada", "boricua", "🍹", "Piña colada", 800, 130, 2 * H,
       "Inventada en San Juan, dicen. Dos horas de XP con sabor a playa boricua."),

    # 🇭🇰 Importación de Hong Kong --------------------------------------------------------
    _t("llamada_robuso", "hongkong", "📞", "Llamada a Robuso", 250,
       "Una llamada internacional a Hong Kong. Robuso contesta según la hora que sea "
       "allí, que es la hora de verdad.", use="robuso"),
    _t("dimsum", "hongkong", "🥟", "Cesta de dim sum", 380,
       "Har gow y siu mai recién hechos al vapor. Para compartir. O no.", use="dimsum", food=True),
    _t("sobre_rojo", "hongkong", "🧧", "Sobre rojo de Año Nuevo", 888,
       "Lai see para dar suerte. Va vacío: el dinero de dentro tributaría.",
       use="sobre_rojo"),
    _t("octopus", "hongkong", "💳", "Tarjeta Octopus", 500,
       "La de Hong Kong: vale para el metro, el 7-Eleven y para presumir de que Robuso te "
       "la trajo."),
    _t("dragon", "hongkong", "🐉", "Dragón de papel de Año Nuevo", 1_688,
       "Precio con tres ochos, que en cantonés suena a prosperidad. Un uno delante, que "
       "suena a Perro Sanxe."),
    _t("maneki", "hongkong", "🐈", "Gato de la suerte", 888,
       "El maneki-neko que saluda con la patita. Saluda a la suerte; la suerte no le "
       "devuelve el saludo."),
    _t("postal_victoria", "hongkong", "🌃", "Postal de Victoria Harbour", 120,
       "El skyline de noche, desde el Peak. Robuso jura que la foto la hizo él. No."),
    _b("te_hk", "hongkong", "🧋", "Té con leche de Hong Kong", 700, 130, 2 * H,
       "Colado con una media de tela, de verdad. Robuso jura que en Hong Kong se gana más "
       "XP con él."),

    # 💎 Vida de rico ---------------------------------------------------------------------
    _t("reloj_oro", "lujo", "⌚", "Reloj de oro de herencia", 180_000,
       "Herencia de un tío de Andorra. Da la hora y da explicaciones a Hacienda. Joyería: "
       "IGIC del 15 %.", igic="lujo", level=25),
    _t("anillo_diamantes", "lujo", "💍", "Anillo de diamantes", 350_000,
       "Para pedir matrimonio o para pedir un préstamo. Joyería: IGIC del 15 %.",
       igic="lujo", level=30),
    _t("yate", "lujo", "🛥️", "Yate en Puerto Rico de Gran Canaria", 4_500_000,
       "Puerto Rico, pero el de Gran Canaria. Jovani se confundió de isla al comprarlo y "
       "ahora lo revende. Tres unidades.", igic="lujo", stock=3, level=40),
    _t("platano_cinta", "lujo", "🍌", "Plátano pegado a la pared con cinta", 6_200_000,
       "Se vendió por 6,2 millones de dólares en 2024. Este es igual, pero el plátano es "
       "de Canarias. Obra de arte única.", igic="lujo", stock=1, level=45),
    _t("abrigo_vison", "lujo", "🧥", "Abrigo de visón sintético", 60_000,
       "Sintético, que estamos en 2026. Pero paga el IGIC de lujo como si fuera de "
       "verdad.", igic="lujo", level=15),
    _t("lingote", "lujo", "🪙", "Lingote de oro de 1 kg", 1_200_000,
       "El oro de inversión no paga IGIC: tipo cero. Y el Patrimonio de aquí solo mira "
       "tu saldo... de momento. Cincuenta lingotes.", igic="cero", stock=50, level=20),
    _t("diamante", "lujo", "💎", "Diamante sintético de laboratorio", 90_000,
       "Igual que uno de mina, pero sin conflictos. Brilla lo mismo y paga el mismo 15 % "
       "de IGIC.", igic="lujo", level=15),
    _t("champan", "lujo", "🥂", "Champán francés de los caros", 4_500,
       "Para brindar cuando ganes en la ruleta. Te durará años sin abrir.", use="champan"),
    _t("gemelos", "lujo", "🎖️", "Gemelos de oro con el escudo de Ferraz", 45_000,
       "Para las camisas de las cenas de gala. Joyería: IGIC del 15 %. Diez pares "
       "numerados.", igic="lujo", stock=10, level=15),

    # 💀 Lo que no debería venderse -------------------------------------------------------
    _t("aire_moncloa", "shitpost", "💨", "Aire embotellado de La Moncloa", 999,
       "Contiene: nada. Exactamente lo que parece. Con su IGIC sobre la nada, "
       "religiosamente pagado."),
    _t("nft_mono", "shitpost", "🖼️", "NFT de un mono aburrido", 69_420,
       "No tienes los derechos de la imagen. Ni la imagen. Tienes un recibo. Y un mono "
       "aburrido, que eres tú."),
    _t("piedra", "shitpost", "🪨", "Piedra", 1,
       "Es una piedra. 1 Y$. La cosa más honesta del colmado. Se puede lanzar las veces "
       "que quieras, porque nunca llega.", use="piedra"),
    _t("neurona", "shitpost", "🧠", "Neurona solitaria", 100,
       "La última que te queda a las 4 de la mañana. Trátala bien."),
    _t("pato_goma", "shitpost", "🦆", "Pato de goma de depurar código", 400,
       "Le explicas el fallo, él te mira, y de repente lo entiendes. Sin pato, el bot no "
       "existiría."),
    _t("accion_cripto", "shitpost", "📉", "Acción de una criptomoneda", 5,
       "Comprada en máximos, como manda la tradición. Su valor va camino de los 5 Y$ que "
       "cuesta."),
    _t("moai", "shitpost", "🗿", "Moái", 2_300,
       "🗿"),
    _t("troll", "shitpost", "🧌", "Troll de foro", 666,
       "Vive debajo de un puente y en los comentarios de cualquier noticia. Edición "
       "numerada para coleccionistas del mal.", stock=66),
    _t("patata_espana", "shitpost", "🥔", "Patata con forma de España", 1_492,
       "Encontrada en un huerto de Ciudad Real. No tiene Canarias, como los mapas del "
       "telediario. Ejemplar único.", stock=1),
    _t("usb_roto", "shitpost", "🔌", "Cable USB que no carga", 50,
       "Carga si lo pones en un ángulo exacto de 37 grados y no respiras."),
    _t("cubito", "shitpost", "🧊", "Cubito de hielo", 10,
       "Se derrite en media hora. Este no, porque es digital, pero imagínatelo. Cien "
       "unidades numeradas.", stock=100),
    _t("recibo_luz", "shitpost", "💡", "Recibo de la luz de enero", 2_300,
       "Lo has pagado dos veces: aquí y en casa. Enmarcado, para que veas a dónde va tu "
       "dinero."),
    _t("caca_oro", "shitpost", "💩", "Caca bañada en oro", 24_000,
       "Arte contemporáneo. Joyería, técnicamente: IGIC de lujo del 15 %. Perro Sanxe no "
       "le hace ascos a nada.", igic="lujo"),
    _t("cucaracha", "shitpost", "🪳", "Cucaracha voladora", 66,
       "Suéltala en el canal y mira el caos. Precio: dos patas por cada seis.",
       use="cucaracha"),
    _t("chiste_enmarcado", "shitpost", "🖼️", "Chiste de Jaimito enmarcado", 150,
       "El del profesor y la pregunta de la tabla del 7. Ya sabes cuál. No, ese no."),
    _t("nada", "shitpost", "⬛", "Nada", 500,
       "No es nada. No hace nada. Cuesta 500 Y$. Si lo compras, el problema no es la "
       "tienda."),

    # 🐾 Tienda de animales ---------------------------------------------------------------
    _t("pienso", "animales", "🥣", "Saco de pienso", 300,
       "Pienso completo para perros y gatos. Sabe a cartón, según quien lo ha probado. "
       "Que es más gente de la que crees.", food=True),
    _t("lata_atun", "animales", "🥫", "Latita de atún", 120,
       "Se abre y aparecen todos los gatos del barrio en tres segundos.", food=True),
    _t("alpiste", "animales", "🌾", "Bolsa de alpiste", 80,
       "Para canarios y demás pajaritos cantores. Cultivado en Lanzarote (no).", food=True),
    _t("hueso_jamon", "animales", "🦴", "Hueso de jamón", 150,
       "Del jamón de Navidad, rebañado hasta el último gramo. El perro no se queja.",
       food=True),
    _t("zanahoria", "animales", "🥕", "Manojo de zanahorias", 60,
       "Para conejos, caballos y tortugas. Verdura: IGIC tipo cero.", igic="cero",
       food=True),
    _t("pipas", "animales", "🌻", "Bolsa de pipas", 50,
       "Para hámsteres, loros y para ti en el banco del parque.", food=True),
    _t("chuche", "animales", "🍖", "Chuche de premio", 40,
       "La que se da cuando hace bien un truco. O cuando te mira así.", food=True),
    _t("pelota", "animales", "🎾", "Pelota de tenis babeada", 200,
       "Lánzasela a alguien del canal. Llega llena de babas, como debe ser. Usos "
       "ilimitados.", use="pelota"),
    _t("collar", "animales", "📛", "Collar con chapa grabada", 500,
       "Con nombre y teléfono, por si se escapa. El chip ya es obligatorio, pero la "
       "chapa queda más mona."),
    _t("rascador", "animales", "🗼", "Rascador de tres pisos", 2_500,
       "El gato lo ignorará y seguirá con el sofá. Es tradición."),
    _t("curso_tenencia", "animales", "🎓", "Curso de tenencia de perros", 800,
       "La Ley 7/2023 pide un curso para tener perro. Este es online, dura diez minutos "
       "y el examen se aprueba solo."),
    _t("seguro_rc_perro", "animales", "📋", "Seguro de responsabilidad civil canino", 1_500,
       "Lo pide la Ley 7/2023 para los perros. Cubre mordiscos y destrozos. Los seguros "
       "están exentos de IGIC (art. 10.1.16 de la Ley 20/1991).", igic="cero"),
    # 🎫 Peña de la porra -----------------------------------------------------------------
    _t("prismaticos_uco", "porra", "🔭", "Prismáticos de la UCO", 8_000,
       "Para ver desde la barra quién apuesta qué. En las `porra`, el botón 🔭 te enseña "
       "a cada apostante y lo que lleva.", per_user=1),
    _t("libreta_porra", "porra", "📓", "Libreta de la porra", 5_000,
       "Con las porras apuntadas a lápiz y un boli mordido. Te deja montar porras de "
       "hasta 10 jugadas en vez de 5.", per_user=1),
    _t("bufanda_pena", "porra", "🧣", "Bufanda de la peña", 1_500,
       "Para animar a quien protagoniza la porra. Abriga poco, pero da ánimos.",
       use="bufanda"),
    _t("silbato_arbitro", "porra", "🟨", "Silbato de árbitro", 900,
       "Pita a quien quieras. Tarjeta, penalti o VAR: tú decides, como en la Liga.",
       use="silbato"),
    _t("quiniela_enmarcada", "porra", "🖼️", "Quiniela de 14 enmarcada", 3_000,
       "Le faltó el pleno al quince. Sigue en la pared del bar desde el 98."),
    _t("boli_porra", "porra", "🖊️", "Boli de la porra del bar", 50,
       "Atado con cuerda al mostrador. Nadie sabe de quién es, pero todos lo han usado."),
    # 🛠️ Ferretería del curro (efectos en `bot.services.work_tools`) --------------------
    _t("reloj_fichar", "curro", "⌚", "Reloj de fichar", 3_000,
       "Un Casio de toda la vida, sincronizado con el registro de jornada. En `pala`: "
       "+10 % de tiempo en cualquier curro.", per_user=1),
    _t("casco_linterna", "curro", "⛑️", "Casco con linterna", 4_000,
       "Homologado y con luz para las zanjas. En `pala`, obra: +15 % de tiempo.",
       per_user=1),
    _t("chaleco_reflectante", "curro", "🦺", "Chaleco reflectante", 5_000,
       "Se te ve desde la caseta, así que el encargado te avisa antes de liarla. En "
       "`pala`, obra: un fallo gratis por turno.", per_user=1),
    _t("seguro_rc", "curro", "📄", "Seguro de responsabilidad civil", 8_000,
       "Para cuando la pala encuentra la fibra de todo el barrio. Los seguros están "
       "exentos de IGIC (art. 10.1.16 de la Ley 20/1991). En `pala`, cavar: romper "
       "algo no resta.", igic="cero", per_user=1),
    _t("zuecos", "curro", "👞", "Zuecos antideslizantes", 4_000,
       "Suela de cocina profesional: el suelo puede estar como una pista de patinaje. "
       "En `pala`, hostelería: +15 % de tiempo.", per_user=1),
    _t("libreta_comandas", "curro", "🗒️", "Libreta de comandas", 5_000,
       "Con boli atado con cuerda. En `pala`, hostelería: un fallo gratis por turno.",
       per_user=1),
    _t("hoja_reclamaciones", "curro", "📋", "Hoja de reclamaciones", 12_000,
       "Si el cliente sabe que la tienes, baja el tono. En `pala`, el chiringuito: "
       "tacha una respuesta mala en cada cliente.", per_user=1),
    _t("cubo_engrudo", "curro", "🪣", "Cubo de engrudo", 3_500,
       "Harina, agua y fe en el candidato. En `pala`, política: +15 % de tiempo.",
       per_user=1),
    _t("pinganillo", "curro", "🎧", "Pinganillo del portavoz", 6_000,
       "Te soplan qué votar y dónde pegar. En `pala`, política: un fallo gratis por "
       "turno.", per_user=1),
    _t("argumentario", "curro", "📒", "Argumentario de Ferraz", 15_000,
       "Respuestas para cualquier pregunta, sobre todo para las que no se responden. "
       "Es un libro: IGIC tipo cero. En `pala`, ruedas de prensa, comisiones y "
       "consejos: tacha una respuesta mala.", igic="cero", per_user=1),
    _t("fonendo", "curro", "🩺", "Fonendoscopio", 6_000,
       "De los buenos, con tu nombre grabado. En `pala`, sanidad: +15 % de tiempo.",
       per_user=1),
    _t("chuleta_triaje", "curro", "🧾", "Chuleta de bolsillo", 7_000,
       "Plastificada, con las constantes normales y las extensiones de la planta. En "
       "`pala`, sanidad: un fallo gratis por turno.", per_user=1),
    _t("vademecum", "curro", "📘", "Vademécum", 15_000,
       "Todos los medicamentos en papel biblia. Es un libro: IGIC tipo cero. En "
       "`pala`, el MIR y la consulta: tacha una respuesta mala.", igic="cero",
       per_user=1),
    _t("segunda_pantalla", "curro", "🖥️", "Segunda pantalla", 8_000,
       "En una el trabajo y en la otra Discord. En `pala`, oficina: +15 % de tiempo.",
       per_user=1),
    _t("tecla_deshacer", "curro", "⌨️", "Teclado con Ctrl+Z gigante", 4_000,
       "La tecla de deshacer ocupa medio teclado. Ojalá existiera en las reuniones. En "
       "`pala`, oficina: un fallo gratis por turno.", per_user=1),
    _t("ia_premium", "curro", "🤖", "Suscripción a la IA de moda", 15_000,
       "Responde con mucha seguridad, a veces bien. En `pala`, reuniones e inversores: "
       "tacha una respuesta mala.", per_user=1),
    _t("domino_bar", "porra", "🁫", "Fichas de dominó del bar", 600,
       "Las de la mesa del fondo, con la doble seis mordida. Se juega en silencio y se "
       "discute a gritos."),
    _t("cana_tapa", "porra", "🍺", "Caña con tapa de ensaladilla", 250,
       "Lo que se pide mientras se espera la jugada de otro. La tapa se la puedes dar a "
       "tu mascota.", food=True),
)
# fmt: on

#: Las mascotas van detrás, con su artículo generado desde `pets_catalog`.
CATALOG += tuple(_pet(species) for species in SPECIES)

CATALOG_BY_KEY: dict[str, CatalogEntry] = {entry.key: entry for entry in CATALOG}


def aisle_of(catalog_key: str | None) -> Aisle:
    """Pasillo de un artículo según su clave de serie; «De la casa» si no tiene."""
    entry = CATALOG_BY_KEY.get(catalog_key or "")
    return AISLE_BY_KEY[entry.aisle] if entry else HOUSE_AISLE


def use_of(catalog_key: str | None) -> Use | None:
    """Cómo se usa un artículo según su clave de serie, o `None`."""
    entry = CATALOG_BY_KEY.get(catalog_key or "")
    return entry.usage if entry else None


def food_of(catalog_key: str | None) -> bool:
    """Si un artículo se come (y se lo puedes dar a una mascota)."""
    entry = CATALOG_BY_KEY.get(catalog_key or "")
    return entry is not None and entry.food
