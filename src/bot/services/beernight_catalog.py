"""Catálogo de serie de la beernight: mandamientos y eventos aleatorios.

Datos puros, sin Discord ni base de datos. Las reglas que los usan (qué
mandamientos están activos, a quién le toca un evento) viven en
`bot.services.beernight`.

- **Mandamientos** (`MANDATES`): normas que dicen quién bebe y cuánto
  («Quien diga «literal» bebe 1»). Los hace cumplir la gente: quien cae lo
  confiesa con 🍺 o alguien se chiva con 🚨 y otro lo confirma. Van por
  familias (`FAMILIES`) para que cada servidor apague las que no le hagan
  gracia. Las claves no se cambian nunca: el histórico guarda la clave de
  cada mandamiento incumplido.
- **Eventos** (`EVENTS`): los lanza el bot cada pocos minutos para que la
  noche no decaiga. Unos reparten sorbos solos, otros piden que alguien
  resuelva un duelo o un reto con botones, y los decretos meten un
  mandamiento temporal.

Las plantillas de los eventos usan `{who}`, `{a}`, `{b}`, `{n}` (sorbos) y
`{m}` (minutos de un decreto). `tests/unit/test_beernight.py` las arma todas
y falla si queda un hueco sin rellenar.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Family:
    """Tema de un grupo de mandamientos (se apagan y encienden juntos)."""

    key: str
    title: str


FAMILIES: tuple[Family, ...] = (
    Family("palabras", "🗣️ Palabras prohibidas"),
    Family("partida", "🎮 La partida"),
    Family("llamada", "🎙️ La llamada"),
    Family("espana", "🇪🇸 España y Hacienda"),
    Family("canarias", "🌋 Canarias"),
    Family("manias", "🤡 Manías y retos"),
    Family("movil", "📱 Móvil y vida real"),
)
FAMILY_BY_KEY: dict[str, Family] = {f.key: f for f in FAMILIES}
#: Familia de los mandamientos que propone la gente del servidor.
CUSTOM_FAMILY = Family("propios", "✍️ De la casa")
#: Familia de los mandamientos temporales que meten los decretos.
DECREE_FAMILY = Family("decreto", "📜 Decreto ley")


@dataclass(frozen=True, slots=True)
class Mandate:
    """Un mandamiento: quién bebe y cuántos sorbos.

    Attributes:
        key: Clave estable. Las de serie son palabras; las propias, `c<id>`;
            las de un decreto, `ev:<clave del evento>`.
        family: Clave de su familia.
        text: La norma, para leerla en voz alta.
        sips: Sorbos que bebe quien la incumple.
    """

    key: str
    family: str
    text: str
    sips: int = 1


def _family(family: str, rows: list[tuple[str, str, int]]) -> list[Mandate]:
    return [Mandate(key, family, text, sips) for key, text, sips in rows]


MANDATES: tuple[Mandate, ...] = tuple(
    _family("palabras", [
        ("literal", "Quien diga «literal».", 1),
        ("en_plan", "Quien diga «en plan».", 1),
        ("bro", "Quien diga «bro», «tío» o «tronco».", 1),
        ("o_sea", "Quien diga «o sea».", 1),
        ("basicamente", "Quien diga «básicamente».", 1),
        ("random", "Quien diga «random».", 1),
        ("cringe", "Quien diga «cringe», «basado» o «red flag».", 1),
        ("es_que", "Quien empiece una excusa con «es que…».", 1),
        ("vale_vale", "Quien diga «vale, vale» (dos veces seguidas).", 1),
        ("ahora_vuelvo", "Quien diga «ahora vuelvo».", 1),
        ("sueno", "Quien diga «tengo sueño».", 2),
        ("madrugo", "Quien diga «mañana madrugo».", 2),
        ("ultima", "Quien diga «una más y me voy».", 2),
        ("nombre_juego", "Quien diga el nombre del juego al que estáis jugando.", 1),
        ("palabrota_ingles", "Quien suelte una palabrota en inglés.", 1),
        ("perdon", "Quien pida perdón. Aquí nadie se disculpa.", 1),
        ("jaja_dicho", "Quien diga «jaja» en voz alta en vez de reírse.", 2),
        ("beber", "Quien diga «beber» o «bebo». Sí, esta también cuenta.", 1),
        ("mazo", "Quien diga «mazo» o «muy muy».", 1),
        ("te_lo_juro", "Quien diga «te lo juro» o «por mi madre».", 1),
        ("no_cap", "Quien diga «sin cap», «no cap» o «de verdad de la buena».", 1),
        ("tipico", "Quien diga «típico» o «clásico».", 1),
    ])
    + _family("partida", [
        ("primero_muere", "Quien muera el primero en la ronda.", 1),
        ("muerte_tonta", "Quien muera de la forma más tonta (caída, granada propia…).", 2),
        ("fuego_amigo", "Quien mate o perjudique a un compañero.", 2),
        ("ultimo", "Quien quede último.", 2),
        ("lag", "Quien culpe al lag.", 1),
        ("culpa_otro", "Quien culpe a otro de su muerte.", 1),
        ("hitbox", "Quien diga «estaba bugueado» o «eso es la hitbox».", 1),
        ("afk", "Quien se quede AFK en mitad de la partida.", 2),
        ("crash", "Quien se desconecte o se le cierre el juego.", 2),
        ("ragequit", "Quien abandone la partida de rabia.", 3),
        ("una_mas", "Quien pida la revancha justo después de perder.", 1),
        ("analista", "Quien explique una jugada que nadie le ha pedido.", 1),
        ("gg_temprano", "Quien diga «gg» antes de que acabe.", 2),
        ("vacio", "Quien se caiga al vacío o fuera del mapa.", 1),
        ("por_poco", "Quien pierda por un punto o por un segundo.", 1),
        ("mvp", "Quien sea el mejor de la ronda, por chulito.", 1),
        ("indeciso", "Quien tarde más de un minuto en elegir personaje o equipo.", 1),
        ("primer_minuto", "Quien muera en el primer minuto.", 2),
        ("grito", "Quien grite al morir.", 1),
        ("botin", "Quien vaya a por el botín y deje tirado a un compañero.", 1),
        ("tutorial", "Quien pregunte cómo se juega cuando ya ha empezado.", 1),
        ("menu", "Quien abra el menú o el mapa justo cuando le matan.", 1),
        ("chivo_pantalla", "Quien mire la pantalla de otro (o lo parezca).", 1),
    ])
    + _family("llamada", [
        ("micro_mute", "Quien hable con el micro silenciado.", 1),
        ("bano", "Quien se vaya al baño: bebe al volver.", 1),
        ("masticar", "Quien mastique pegado al micro.", 1),
        ("tarde", "Quien llegue tarde a la llamada.", 2),
        ("sale_entra", "Quien salga y vuelva a entrar en la llamada.", 1),
        ("eco", "Quien acople o haga eco.", 1),
        ("estornudo", "Quien estornude o tosa en el micro.", 1),
        ("fondo", "Quien tenga tele, música o familia de fondo.", 1),
        ("me_ois", "Quien pregunte «¿me oís?» o «¿se me escucha?».", 1),
        ("camara", "Quien encienda la cámara: bebe en directo.", 1),
        ("pantalla", "Quien comparta pantalla sin que nadie se lo pida.", 1),
        ("eructo", "Quien eructe en el micro.", 2),
        ("silencio", "Quien pida silencio.", 1),
        ("autorrisa", "Quien se ría de su propio chiste.", 1),
        ("chiste_malo", "Quien cuente un chiste y nadie se ría.", 2),
        ("anecdota_repe", "Quien cuente una anécdota que ya ha contado.", 1),
        ("me_voy", "Quien diga «me voy» y siga aquí diez minutos después.", 2),
        ("curro", "Quien hable de trabajo o de clase.", 1),
        ("bostezo", "Quien bostece en el micro.", 1),
        ("pisarse", "Quien hable a la vez que otro y no se calle.", 1),
        ("callado", "Quien lleve cinco minutos sin decir nada (se sospecha que duerme).", 1),
        ("susurro", "Quien susurre algo que nadie entiende.", 1),
    ])
    + _family("espana", [
        ("politica", "Quien saque la política.", 1),
        ("sanxe", "Quien diga «Pedro Sánchez» o «Perro Sanxe».", 2),
        ("hacienda", "Quien diga «Hacienda» o «la Renta».", 2),
        ("fango", "Quien diga «bulo» o «máquina del fango».", 2),
        ("alquiler", "Quien se queje del alquiler o del precio de la luz.", 1),
        ("paguita", "Quien diga «paguita» o «IMV».", 1),
        ("falcon", "Quien mencione el Falcon.", 2),
        ("dinero_real", "Quien hable de sueldos, facturas o nóminas de verdad.", 1),
        ("facha_rojo", "Quien llame a alguien «facha» o «rojo».", 1),
        ("futbol", "Quien hable de fútbol.", 1),
        ("mis_tiempos", "Quien diga «en mis tiempos» o «antes esto no pasaba».", 1),
        ("resistencia", "Quien diga «manual de resistencia»: bebe y resiste.", 3),
        ("madrid", "Quien hable de Madrid como si fuera el centro del mundo.", 1),
        ("tortilla", "Quien abra el debate de la tortilla con o sin cebolla.", 2),
        ("uco", "Quien mencione la UCO, Pegasus o un juzgado.", 2),
        ("cita_previa", "Quien se queje de la burocracia o de pedir cita previa.", 1),
        ("mercadona", "Quien diga «Mercadona».", 1),
        ("cripto", "Quien hable de cripto, bolsa o «invertir».", 2),
        ("yapdollars", "Quien hable del casino del bot o de los yapdollars.", 1),
        ("puente", "Quien hable de puentes, festivos o vacaciones.", 1),
        ("cotizar", "Quien diga «cotizar» o «la Seguridad Social».", 1),
        ("gobierno", "Quien diga «el Gobierno» o «el Congreso».", 1),
    ])
    + _family("canarias", [
        ("chacho", "Quien diga «chacho» o «mi niño».", 1),
        ("guagua", "Quien diga «autobús» en vez de «guagua».", 1),
        ("godo", "Quien diga «godo». Si lo es, bebe el doble en espíritu.", 1),
        ("papas", "Quien diga «patatas» en vez de «papas».", 1),
        ("gofio", "Quien mencione el gofio.", 2),
        ("calor", "Quien hable del calor, la calima o el tiempo.", 1),
        ("nos", "Quien diga «ños» o «ño».", 1),
        ("fuerte", "Quien diga «¡fuerte!» como exclamación.", 1),
        ("cholas", "Quien diga «chanclas» en vez de «cholas».", 1),
        ("millo", "Quien diga «maíz» en vez de «millo».", 1),
        ("peninsula", "Quien diga «la Península» o «peninsulares».", 1),
        ("hora_menos", "Quien diga «una hora menos en Canarias».", 2),
        ("teide", "Quien mencione el Teide o el Roque Nublo.", 1),
        ("mojo", "Quien diga «mojo».", 1),
        ("fos", "Quien diga «¡fos!».", 1),
        ("bochinche", "Quien diga «bochinche» o «enyesque».", 1),
        ("perenquen", "Quien diga «perenquén». Si alguien lo consigue, se merece la ronda.", 3),
        ("pibe", "Quien diga «pibe» o «piba».", 1),
        ("ustedes", "Quien diga «vosotros» (aquí se dice «ustedes»).", 1),
        ("canarion", "Quien saque la guerra entre Gran Canaria y Tenerife.", 2),
    ])
    + _family("manias", [
        ("salud", "Quien beba sin decir «¡salud!» antes.", 1),
        ("no_no", "Quien diga «no». Hoy solo se dice «negativo».", 1),
        ("numeros", "Quien diga un número en español: hoy van en inglés.", 1),
        ("excelencia", "Quien no llame «Excelencia» al anfitrión.", 1),
        ("tercera", "Quien diga «yo»: hoy se habla en tercera persona.", 1),
        ("apodos", "Quien llame a alguien por su nombre: hoy solo apodos.", 1),
        ("risa_mala", "Quien se ría de un chiste malo.", 1),
        ("wepa", "Quien acabe una frase sin decir «wepa».", 1),
        ("tacos", "Quien suelte un taco.", 1),
        ("cuantos", "Quien pregunte cuántos sorbos lleva.", 1),
        ("que_mandamientos", "Quien pregunte qué mandamientos hay activos.", 1),
        ("la_hora", "Quien pregunte la hora.", 1),
        ("por_favor", "Quien diga «por favor»: aquí se ordena.", 1),
        ("permiso_bano", "Quien vaya al baño sin pedir permiso al anfitrión.", 2),
        ("anglicismo", "Quien diga una palabra en inglés que tenga traducción.", 1),
        ("cantar", "Quien cante, aunque sea medio verso.", 1),
        ("imitar", "Quien imite a alguien de la llamada.", 1),
        ("que_que", "Quien diga «¿qué?» porque no ha oído.", 1),
        ("aburro", "Quien diga «me aburro».", 2),
        ("rima", "Quien rime sin querer.", 1),
        ("interrumpir", "Quien interrumpa al anfitrión.", 1),
        ("vaso_vacio", "Quien vacíe el vaso sin anunciarlo con un discurso.", 1),
        ("acento", "Quien hable con acento de otra parte sin que venga a cuento.", 1),
    ])
    + _family("movil", [
        ("mira_movil", "Quien mire el móvil en mitad de la partida.", 1),
        ("llamada_tel", "Quien reciba una llamada.", 1),
        ("notificacion", "Quien deje que suene una notificación por el micro.", 1),
        ("whatsapp", "Quien conteste un WhatsApp.", 1),
        ("redes", "Quien abra TikTok o Instagram.", 1),
        ("delivery", "Quien pida comida a domicilio: brinda con el repartidor.", 1),
        ("rellenar", "Quien se levante a rellenar el vaso: brinda al volver.", 1),
        ("ex", "Quien mencione a su ex.", 2),
        ("pareja", "Quien hable de su pareja.", 1),
        ("manana_importante", "Quien diga que mañana tiene algo importante.", 1),
        ("cansado", "Quien diga que está cansado.", 1),
        ("hielo", "Quien vaya a por hielo y tarde más de cinco minutos.", 1),
        ("derrame", "Quien derrame algo.", 2),
        ("no_borracho", "Quien diga que no va borracho.", 2),
        ("no_bebo_mas", "Quien diga «yo no bebo más».", 2),
        ("familia_llama", "Quien tenga a su madre o a su padre llamándole.", 1),
        ("foto_vaso", "Quien le haga una foto a su bebida.", 1),
        ("google", "Quien busque algo en Google para ganar una discusión.", 1),
        ("audio_wsp", "Quien ponga un audio de WhatsApp en la llamada.", 1),
        ("story", "Quien suba una story de la beernight.", 1),
        ("bateria", "Quien tenga el móvil por debajo del 10 %.", 1),
        ("snack", "Quien abra una bolsa de patatas (perdón, papas) sin compartir.", 1),
    ])
)  # fmt: skip
MANDATE_BY_KEY: dict[str, Mandate] = {m.key: m for m in MANDATES}


class EventKind(StrEnum):
    """Cómo se resuelve un evento aleatorio."""

    #: Reparte sorbos solo, a quien diga `Target`.
    DRINK = "drink"
    #: Dos personas se enfrentan; los demás pulsan quién ha perdido.
    DUEL = "duel"
    #: Una persona tiene que hacer algo; los demás dicen si lo ha cumplido.
    CHALLENGE = "challenge"
    #: Una persona reparte sorbos a quien elija.
    GIFT = "gift"
    #: Entra un mandamiento temporal durante unos minutos.
    DECREE = "decree"
    #: Cambian todos los mandamientos de golpe.
    RESHUFFLE = "reshuffle"


class Target(StrEnum):
    """A quién le toca un evento `DRINK`."""

    ONE = "one"
    PAIR = "pair"
    ALL = "all"
    ALL_BUT_ONE = "all_but_one"
    MOST = "most"
    LEAST = "least"
    HOST = "host"
    NEWEST = "newest"


@dataclass(frozen=True, slots=True)
class NightEvent:
    """Un evento aleatorio de la beernight.

    Attributes:
        key: Clave estable (sale en el histórico y en los logros).
        kind: Cómo se resuelve.
        text: Plantilla del anuncio (`{who}`, `{a}`, `{b}`, `{n}`, `{m}`).
        sips: Sorbos en juego.
        target: A quién le toca, en los eventos `DRINK`.
        rule: En un decreto, el texto del mandamiento temporal.
        minutes: En un decreto, cuánto dura.
    """

    key: str
    kind: EventKind
    text: str
    sips: int = 1
    target: Target = Target.ONE
    rule: str = ""
    minutes: int = 0

    @property
    def players_needed(self) -> int:
        """Gente presente que hace falta para que el evento tenga sentido."""
        if self.kind is EventKind.DUEL or self.target in (Target.PAIR, Target.ALL_BUT_ONE):
            return 2
        return 1


def _drink(target: Target, rows: list[tuple[str, str, int]]) -> list[NightEvent]:
    return [NightEvent(key, EventKind.DRINK, text, sips, target) for key, text, sips in rows]


def _kind(kind: EventKind, rows: list[tuple[str, str, int]]) -> list[NightEvent]:
    return [NightEvent(key, kind, text, sips) for key, text, sips in rows]


def _decrees(rows: list[tuple[str, str, str, int, int]]) -> list[NightEvent]:
    return [
        NightEvent(key, EventKind.DECREE, text, sips, rule=rule, minutes=minutes)
        for key, text, rule, sips, minutes in rows
    ]


EVENTS: tuple[NightEvent, ...] = tuple(
    _drink(Target.ONE, [
        ("diana", "🎯 Diana: {who} bebe {n}. No hay motivo. El motivo es que es {who}.", 1),
        ("bombo", "🎲 El bombo ha hablado: {who}, {n} sorbos.", 2),
        ("inspeccion", "🧾 Inspección sorpresa de Hacienda a {who}: {n} de recargo.", 2),
        ("notificacion_aeat", "📬 {who} tiene una notificación de la Agencia Tributaria. "
         "Bebe {n} por si acaso.", 1),
        ("uco_visita", "🚔 La UCO ha registrado el vaso de {who}: {n} sorbos para tapar pruebas.",
         2),
        ("falcon_billete", "✈️ {who} se ha colado en el Falcon. El billete se paga en sorbos: {n}.",
         2),
        ("loteria_no", "🎟️ A {who} no le ha tocado la lotería. Consuelo: {n}.", 1),
        ("cumple_falso", "🎂 Hoy es el cumpleaños de {who} (en algún universo). {n} por ello.", 1),
        ("pagafantas", "💸 {who} paga esta ronda: {n} sorbos.", 1),
        ("calima", "🌫️ Ha entrado calima en casa de {who}. Hidratación: {n}.", 1),
    ])
    + _drink(Target.ALL, [
        ("brindis_sanxe", "🍻 Brindis general: todos beben {n}. Por Perro Sanxe, que hoy no nos "
         "ha cobrado nada.", 1),
        ("brindis_canarias", "🌋 Por Canarias, que tiene una hora menos y aun así llega tarde: "
         "todos {n}.", 1),
        ("hidratacion", "💧 Pausa de hidratación oficial: todos beben {n} (lo que tengan en el "
         "vaso).", 1),
        ("huelga", "🪧 Huelga general convocada: nadie trabaja y todos beben {n}.", 1),
        ("decimotercera", "🎁 Ha llegado la paga extra: todos {n}.", 2),
        ("salud", "🥂 ¡Salud! Todos {n}. Quien no diga «salud» se lo ha ganado.", 1),
    ])
    + _drink(Target.ALL_BUT_ONE, [
        ("aforado", "👑 {who} está aforado esta ronda; los demás beben {n}.", 1),
        ("indulto", "📜 El Gobierno indulta a {who}. El resto paga el pato: {n}.", 1),
        ("conductor", "🚗 {who} es el conductor designado de este evento. Los demás {n}.", 1),
    ])
    + _drink(Target.MOST, [
        ("inercia", "🥴 {who} es quien más lleva y aun así bebe {n}. Por inercia.", 1),
        ("progresivo", "📈 Impuesto progresivo: quien más tiene, más paga. {who}, {n}.", 1),
    ])
    + _drink(Target.LEAST, [
        ("sereno", "🧐 {who} va demasiado sereno: {n} sorbos para ponerse al día.", 2),
        ("redistribucion", "⚖️ Redistribución de la riqueza: {who}, el más sobrio, bebe {n}.",
         2),
        ("imv_sorbos", "🧾 {who} cobra el IMV de sorbos: {n}, sin pedir cita previa.", 2),
    ])
    + _drink(Target.HOST, [
        ("anfitrion", "🎩 El anfitrión, {who}, bebe {n} por montar esto.", 1),
        ("excelencia", "🤵 Su Excelencia {who} tiene sed: {n}.", 1),
    ])
    + _drink(Target.NEWEST, [
        ("novato", "🚪 {who}, el último en llegar, paga la novatada: {n}.", 1),
        ("tarde", "⏰ Quien llega tarde no elige sitio: {who}, {n}.", 1),
    ])
    + _drink(Target.PAIR, [
        ("brindis_pareja", "🤝 {a} y {b} brindan entre sí: {n} cada uno.", 1),
        ("pacto", "📝 {a} y {b} firman un pacto de gobierno. Se sella con {n} cada uno.", 1),
        ("coalicion", "🏛️ Coalición inesperada: {a} y {b}, {n} cada uno.", 1),
    ])
    + _kind(EventKind.DUEL, [
        ("piedra_papel", "⚔️ Duelo: {a} contra {b}. Piedra, papel o tijera por voz. "
         "Quien pierda, {n}.", 2),
        ("trabalenguas", "👅 {a} contra {b}: «Tres tristes tigres tragaban trigo en un trigal». "
         "Quien se trabe, {n}.", 2),
        ("capital", "🧠 {a} contra {b}: el primero que diga la capital de Australia gana. "
         "Quien pierda, {n}.", 1),
        ("tararear", "🎶 {a} tararea una canción y {b} tiene diez segundos para adivinarla. "
         "Si la acierta bebe {a}; si no, {b}. {n} sorbos.", 2),
        ("wepa", "📢 {a} contra {b}: quien grite «¡WEPA!» con más sentimiento. Decide la "
         "llamada. Quien pierda, {n}.", 1),
        ("categorias", "🗂️ {a} contra {b}: marcas de cerveza por turnos. Quien se quede en "
         "blanco, {n}.", 2),
        ("ministros", "🏛️ {a} contra {b}: ministros del Gobierno por turnos. Quien falle o "
         "invente uno, {n}.", 2),
        ("pueblos", "🌋 {a} contra {b}: municipios de Canarias por turnos. Quien se quede "
         "en blanco, {n}.", 2),
        ("risa", "😐 {a} contra {b}: duelo de seriedad. El primero que se ría, {n}.", 2),
        ("mates", "🧮 {a} contra {b}: ¿cuánto es 17 por 6? El primero que lo diga bien gana. "
         "Quien pierda, {n}.", 1),
    ])
    + _kind(EventKind.CHALLENGE, [
        ("chiste", "🎤 {who} cuenta un chiste. Si nadie se ríe, {n} sorbos.", 2),
        ("canarismos", "🌋 {who} dice tres palabras canarias en diez segundos o bebe {n}.", 1),
        ("imitacion", "🎭 {who} imita a alguien de la llamada. Si no lo adivinan, {n}.", 2),
        ("abecedario", "🔤 {who} recita el abecedario al revés hasta la P o bebe {n}.", 2),
        ("discurso", "🎙️ {who} da un discurso de investidura de treinta segundos. Si convence, "
         "se libra; si no, {n}.", 2),
        ("verdad", "🫢 {who} cuenta algo que nadie de la llamada sepa o bebe {n}.", 2),
        ("cancion", "🎵 {who} canta el estribillo de la última canción que haya escuchado o "
         "bebe {n}.", 2),
        ("acento", "🗣️ {who} habla con acento argentino hasta el siguiente evento. Si se le "
         "escapa, {n}.", 1),
        ("cumplido", "💐 {who} le dice algo bonito a cada persona de la llamada o bebe {n}.", 1),
        ("ingles", "🇬🇧 {who} explica su día en inglés durante treinta segundos o bebe {n}.",
         1),
        ("ministerio", "🏛️ {who} se inventa un ministerio nuevo y lo defiende. Si la llamada "
         "no lo aprueba, {n}.", 1),
        ("refran", "📖 {who} dice un refrán entero sin equivocarse o bebe {n}.", 1),
    ])
    + _kind(EventKind.GIFT, [
        ("reparte", "🎁 {who} reparte {n} sorbos a quien quiera.", 2),
        ("ministro_hacienda", "🏛️ {who} es ministro de Hacienda por un minuto: recauda {n} "
         "sorbos de quien elija.", 2),
        ("dedo", "👉 {who} tiene el dedo de la justicia: {n} sorbos para quien señale.", 1),
        ("enchufe", "🔌 {who} coloca a un enchufado. El enchufado bebe {n}.", 2),
    ])
    + _decrees([
        ("decreto_yo", "📜 Decreto ley: durante {m} minutos, quien diga «yo» bebe {n}.",
         "Decreto: quien diga «yo».", 1, 10),
        ("decreto_susurro", "📜 Decreto ley: durante {m} minutos se habla susurrando. Quien "
         "suba la voz, {n}.", "Decreto: se habla susurrando.", 1, 5),
        ("decreto_wepa", "📜 Decreto boricua: durante {m} minutos cada frase acaba en «wepa». "
         "Quien se olvide, {n}.", "Decreto: cada frase acaba en «wepa».", 1, 10),
        ("decreto_usted", "📜 Decreto de cortesía: durante {m} minutos todos se tratan de "
         "usted. Quien tutee, {n}.", "Decreto: todos se tratan de usted.", 1, 10),
        ("decreto_risa", "📜 Estado de alarma: durante {m} minutos está prohibido reírse. "
         "Quien se ría, {n}.", "Decreto: prohibido reírse.", 1, 5),
        ("decreto_preguntas", "📜 Decreto socrático: durante {m} minutos solo se habla con "
         "preguntas. Quien afirme algo, {n}.", "Decreto: solo se habla con preguntas.", 1, 5),
        ("decreto_nombres", "📜 Decreto de anonimato: durante {m} minutos nadie dice nombres. "
         "Quien lo haga, {n}.", "Decreto: prohibido decir nombres.", 1, 10),
        ("decreto_ingles", "📜 Decreto bilingüe: durante {m} minutos se habla en inglés. "
         "Quien hable en español, {n}.", "Decreto: se habla en inglés.", 1, 5),
        ("decreto_rey", "📜 Real decreto: durante {m} minutos, antes de hablar hay que decir "
         "«con la venia». Quien no lo haga, {n}.", "Decreto: antes de hablar, «con la venia».",
         1, 10),
        ("decreto_tacos", "📜 Ley mordaza: durante {m} minutos, quien suelte un taco bebe {n}.",
         "Decreto: ley mordaza para los tacos.", 2, 10),
    ])
    + [
        NightEvent(
            "remodelacion",
            EventKind.RESHUFFLE,
            "🔄 Remodelación del Gobierno: cambian todos los mandamientos.",
            0,
        ),
        NightEvent(
            "mocion",
            EventKind.RESHUFFLE,
            "🗳️ Moción de censura aprobada: fuera los mandamientos de antes, entran otros.",
            0,
        ),
    ]
)  # fmt: skip
EVENT_BY_KEY: dict[str, NightEvent] = {e.key: e for e in EVENTS}
