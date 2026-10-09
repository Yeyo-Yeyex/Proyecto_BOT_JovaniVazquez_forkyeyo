"""Lo que hacen los objetos de la tienda al usarlos desde la mochila.

Sin Discord ni base de datos: aquí solo se decide qué pasa y qué se cuenta.
El cog (`bot.cogs.shop`) se encarga de gastar la unidad, enseñar el
resultado en el canal y apuntar los logros.

Cada objeto usable del surtido (`bot.services.shop_catalog`) apunta a un
`Use` por su clave. Hay tres formas de usarlo (`Target`):

- **Sin más** (`NONE`): petardos, la galleta de la suerte, la bola 8.
- **A alguien** (`MEMBER`): huevos, burofax, el chivatazo a la UCO. Se elige
  a quién con un desplegable de miembros; si te eliges a ti o al bot, hay
  frases propias.
- **Con texto** (`TEXT`): el megáfono, que pide qué gritar en un formulario.

Los gastables (`consumes=True`) se acaban al usarlos; los demás (la bola 8,
el timple, la vuvuzela) se usan las veces que quieras, con una espera entre
usos (`cooldown`) para que no se conviertan en spam.

Ningún uso mueve dinero: lo que cuesta es comprarlos (con IGIC, en la caja).
Así no hacen falta reglas fiscales nuevas (Biblia, sección 4). La caja botín
tampoco: da un objeto del catálogo, sin valor de reventa, y no Y$.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from bot.services.taxes import TAX_COLLECTOR


class Target(StrEnum):
    """A quién o a qué se aplica un uso."""

    NONE = "nada"
    MEMBER = "miembro"
    TEXT = "texto"


@dataclass(frozen=True, slots=True)
class Outcome:
    """Un desenlace posible de un uso.

    Attributes:
        weight: Peso relativo frente a los demás desenlaces del mismo uso.
        text: Plantilla con `{who}`, `{target}`, `{item}` y los huecos de
            `Use.pools`.
        flag: Marca para los logros (`"hit"`, `"miss"`, `"backfire"`…).
    """

    weight: int
    text: str
    flag: str = ""


@dataclass(frozen=True, slots=True)
class Use:
    """Cómo se usa un objeto.

    Attributes:
        key: Clave estable (se guarda en los contadores de logros).
        verb: Lo que se hace, para el desplegable de la mochila ("Lanzar").
        target: Si pide a alguien o un texto.
        consumes: Si se gasta al usarlo.
        cooldown: Segundos de espera entre usos (solo los que no se gastan).
        outcomes: Desenlaces posibles, elegidos al azar por su peso.
        pools: Huecos extra de las plantillas: `{"multa": ("frase", …)}`.
        special: Usos con lógica propia (`d20`, `padron`, `robuso`, `mystery`,
            `megaphone`); sus desenlaces se arman en código.
        on_self: Frase si te eliges a ti mismo (solo `Target.MEMBER`).
    """

    key: str
    verb: str
    target: Target = Target.NONE
    consumes: bool = True
    cooldown: int = 0
    outcomes: tuple[Outcome, ...] = ()
    pools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    special: str = ""
    on_self: str = ""

    @property
    def summary(self) -> str:
        """Etiqueta corta para la ficha del escaparate."""
        how = {
            Target.NONE: self.verb,
            Target.MEMBER: f"{self.verb} a alguien",
            Target.TEXT: f"{self.verb} lo que quieras",
        }[self.target]
        return f"🫳 {how} · " + ("un solo uso" if self.consumes else "usos ilimitados")


@dataclass(frozen=True, slots=True)
class UseResult:
    """Lo que ha pasado al usar algo.

    Attributes:
        text: Mensaje público para el canal.
        flags: Marcas para los logros.
    """

    text: str
    flags: frozenset[str] = frozenset()


#: Frase cuando alguien usa un objeto contra el propio bot.
BOT_LINES: tuple[str, ...] = (
    "{who} intenta usar {item} contra Jovani. Jovani lo esquiva con un paso de perreo "
    "y le guiña un ojo. Al bot no, mi amor.",
    "{who} apunta a Jovani con {item}. Jovani se agacha, sigue cantando y ni se despeina.",
    "{who} va a por Jovani con {item}. Rebota en el autotune y vuelve a su dueño. 🎤",
)
#: Frase por defecto si te eliges a ti mismo.
SELF_LINE = "{who} se aplica {item} a sí mismo. Nadie entiende nada, pero el colmado no devuelve."

HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def _o(weight: int, text: str, flag: str = "") -> Outcome:
    return Outcome(weight, text, flag)


# -- Catálogo de usos ------------------------------------------------------------------

_USES: tuple[Use, ...] = (
    # 🥘 Typical Spanish (la comida de siempre) -----------------------------------------
    Use(
        "huevo",
        "Lanzar",
        Target.MEMBER,
        outcomes=(
            _o(
                55,
                "🥚 {who} le estampa un huevo a {target} en toda la frente. ¡Plaf! "
                "Clara por aquí, yema por allá.",
                "hit",
            ),
            _o(
                20,
                "🥚 {who} lanza un huevo a {target}... y falla. Se lo come la fachada de "
                "Ferraz, que ya está acostumbrada.",
                "miss",
            ),
            _o(
                12,
                "🥚 {who} lanza un huevo a {target}, rebota en una farola y le da a {who}. "
                "Karma de corral.",
                "backfire",
            ),
            _o(
                8,
                "🥚 El huevo de {who} vuela hacia {target} y le cae a {collector}, que "
                "pasaba por ahí cobrando. Multa de 600 € por ensuciar la vía pública. "
                "Es broma. Por ahora.",
                "collector",
            ),
            _o(
                5,
                "🥚 {who} lanza un huevo a {target}, que lo caza al vuelo, lo casca y se "
                "hace una tortilla francesa. Respeto.",
                "caught",
            ),
        ),
    ),
    Use(
        "tomate",
        "Lanzar",
        Target.MEMBER,
        outcomes=(
            _o(
                60,
                "🍅 ¡Tomatina! {who} le revienta un tomate a {target}. Queda como un "
                "gazpacho con patas.",
                "hit",
            ),
            _o(
                25,
                "🍅 {who} lanza un tomate a {target}, que lo esquiva con un quiebro de "
                "torero. Ole.",
                "miss",
            ),
            _o(
                15,
                "🍅 {who} aprieta tanto el tomate antes de lanzarlo que se pone perdido "
                "él solo. {target} se ríe desde lejos.",
                "backfire",
            ),
        ),
    ),
    Use(
        "barra_pan",
        "Atizar",
        Target.MEMBER,
        outcomes=(
            _o(
                50,
                "🥖 {who} le atiza a {target} con una barra de pan de ayer. Dura como "
                "una porra. {target} ve las estrellas y las migas.",
                "hit",
            ),
            _o(
                30,
                "🥖 {who} intenta darle a {target} con la barra, pero es de gasolinera y "
                "se dobla como un churro. Cero daños, mucha vergüenza.",
                "miss",
            ),
            _o(
                20,
                "🥖 {who} va a pegarle a {target} con la barra y se la come por el "
                "camino. El hambre es el hambre.",
                "eaten",
            ),
        ),
    ),
    Use(
        "padron",
        "Comerte uno",
        special="padron",
    ),
    Use(
        "paella",
        "Invitar al canal",
        outcomes=(
            _o(
                50,
                "🥘 {who} saca una paella del domingo para todo el canal. Con socarrat. "
                "Quien diga que lleva chorizo, al calabozo.",
            ),
            _o(
                30,
                "🥘 {who} invita a paella. Alguien pregunta si lleva guisantes y se "
                "monta un debate de tres horas. Se enfría.",
            ),
            _o(
                20,
                "🥘 {who} reparte paella. {collector} se sirve primero, como siempre: "
                "un 21 %... ah, no, que aquí es IGIC. Un 7 %.",
            ),
        ),
    ),
    Use(
        "cava",
        "Descorchar",
        outcomes=(
            _o(
                60,
                "🍾 {who} descorcha una botella de cava. ¡Pum! Chinchín para todo el canal.",
                "pop",
            ),
            _o(
                25,
                "🍾 {who} descorcha el cava y el tapón le da a la lámpara. Se ha ido la "
                "luz en todo el servidor. Otro apagón.",
                "lamp",
            ),
            _o(
                15,
                "🍾 {who} agita el cava como en la Fórmula 1 y lo pone todo perdido. "
                "Campeón del mundo de nada.",
            ),
        ),
    ),
    Use("galleta", "Abrir", special="fortune"),
    Use(
        "tarta",
        "Tartazo",
        Target.MEMBER,
        outcomes=(
            _o(
                65,
                "🍰 ¡Tartazo! {who} le planta una tarta de nata en la cara a {target}. "
                "Momento de cine mudo.",
                "hit",
            ),
            _o(
                20,
                "🍰 {who} va a darle el tartazo a {target}, resbala con la nata y se la "
                "come él. Literalmente.",
                "backfire",
            ),
            _o(
                15,
                "🍰 {target} esquiva el tartazo de {who} y se la come entera. Cero "
                "arrepentimiento.",
                "miss",
            ),
        ),
    ),
    Use(
        "tortilla",
        "Abrir el debate",
        outcomes=(
            _o(
                50,
                "🥔 {who} saca una tortilla CON cebolla. El canal se divide en dos "
                "bandos. Esto acaba en el Congreso.",
                "con",
            ),
            _o(
                50,
                "🥔 {who} saca una tortilla SIN cebolla. Media España le retira el "
                "saludo. La otra media le pide matrimonio.",
                "sin",
            ),
        ),
    ),
    Use(
        "tupper",
        "Devolver",
        Target.MEMBER,
        outcomes=(
            _o(
                70,
                "🍲 {who} le devuelve el tupper a {target}. Limpio y con la tapa. "
                "Las madres de España lloran de emoción.",
                "returned",
            ),
            _o(
                30,
                "🍲 {who} le devuelve el tupper a {target}... sin la tapa. Se rompe una "
                "amistad de veinte años.",
                "lid",
            ),
        ),
        on_self="🍲 {who} se devuelve el tupper a sí mismo. Era suyo desde el principio. "
        "Jugada maestra.",
    ),
    Use(
        "sal",
        "Echar sal",
        Target.MEMBER,
        outcomes=(
            _o(
                60,
                "🧂 {who} le echa sal gorda a {target} por encima del hombro. Mal fario "
                "garantizado durante siete años, o hasta la próxima ruleta.",
                "hit",
            ),
            _o(
                40,
                "🧂 {who} le echa sal a {target}, pero {target} lleva un ojo turco. "
                "Se anula. Empate técnico de supersticiones.",
                "blocked",
            ),
        ),
    ),
    Use(
        "uvas",
        "Comértelas",
        outcomes=(
            _o(
                55,
                "🍇 {who} se come las doce uvas al ritmo de las campanadas. Año nuevo, "
                "ruina nueva.",
                "ok",
            ),
            _o(
                30,
                "🍇 {who} se atraganta en la séptima uva. Le quedan cinco y un año de "
                "mala suerte. Las de la ruleta no cuentan, que esas ya venían mal.",
                "choke",
            ),
            _o(
                15,
                "🍇 {who} se come las uvas con los cuartos en vez de con las "
                "campanadas. Clásico. Feliz año dos veces.",
                "quarters",
            ),
        ),
    ),
    # 🌴 Canarias ---------------------------------------------------------------------------
    Use(
        "gofio",
        "Lanzar",
        Target.MEMBER,
        outcomes=(
            _o(
                60,
                "🌾 {who} le lanza una pella de gofio a {target}. ¡Fos! Queda como una "
                "croqueta empanada.",
                "hit",
            ),
            _o(
                25,
                "🌾 {who} lanza la pella de gofio, pero se deshace en el aire. Nube de "
                "gofio en todo el canal. Huele a casa de abuela.",
                "miss",
            ),
            _o(
                15,
                "🌾 {target} coge la pella de gofio al vuelo y se la come con plátano. "
                "Chacho, ¡qué arte!",
                "caught",
            ),
        ),
    ),
    Use(
        "mojo",
        "Untar",
        Target.MEMBER,
        outcomes=(
            _o(
                70,
                "🌶️ {who} le unta mojo picón a {target}. Pica más que la factura de la luz.",
                "hit",
            ),
            _o(
                30,
                "🌶️ {who} va a untarle mojo a {target}, pero se lo echa a unas papas "
                "arrugadas por el camino. Prioridades.",
                "eaten",
            ),
        ),
    ),
    Use(
        "platano",
        "Tirar la piel",
        Target.MEMBER,
        outcomes=(
            _o(
                55,
                "🍌 {who} deja una piel de plátano de Canarias delante de {target}. "
                "Resbalón olímpico. Un 9,8 del jurado.",
                "hit",
            ),
            _o(
                30,
                "🍌 {target} ve la piel de plátano de {who} y la esquiva. Lleva toda la "
                "vida en Canarias: sabe dónde pisa.",
                "miss",
            ),
            _o(
                15,
                "🍌 {who} se resbala con su propia piel de plátano. Ironía con "
                "denominación de origen.",
                "backfire",
            ),
        ),
    ),
    Use(
        "vela",
        "Encender",
        outcomes=(
            _o(
                40,
                "🕯️ {who} le enciende una vela a la Virgen del Pino. Pide salud, "
                "trabajo y que Hacienda se olvide de su nombre.",
            ),
            _o(
                30,
                "🕯️ {who} enciende una vela y pide que salga el rojo. Sale el 0. "
                "La Virgen está a otras cosas.",
            ),
            _o(
                30,
                "🕯️ {who} enciende una vela por la paz del servidor. Dura tres "
                "minutos. Récord histórico.",
            ),
        ),
    ),
    Use(
        "barraquito",
        "Invitar",
        Target.MEMBER,
        outcomes=(
            _o(
                70,
                "☕ {who} invita a {target} a un barraquito. Leche condensada, Licor 43, "
                "café y canela. Amistad sellada.",
                "shared",
            ),
            _o(
                30,
                "☕ {who} invita a {target} a un barraquito, pero el camarero lo remueve "
                "antes de la foto. Tragedia en tres capas.",
                "ruined",
            ),
        ),
        on_self="☕ {who} se invita a sí mismo a un barraquito. Autocuidado canario.",
    ),
    Use(
        "timple",
        "Tocar",
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(40, "🎸 {who} se arranca con el timple: una isa que hace llorar a medio canal."),
            _o(30, "🎸 {who} toca el timple. Suena a romería de Teror y a resaca del Pino."),
            _o(
                30,
                "🎸 {who} intenta tocar el timple y se le salta una cuerda. Folía "
                "interrumpida por motivos técnicos.",
            ),
        ),
    ),
    # 🎉 Fiestas y verbenas --------------------------------------------------------------
    Use(
        "petardo",
        "Encender",
        outcomes=(
            _o(
                50,
                "🧨 {who} tira un petardo en mitad del canal. ¡PUM! Tres perros ladran "
                "y un vecino llama a la policía.",
                "boom",
            ),
            _o(
                30,
                "🧨 {who} enciende el petardo y... nada. Está mojado. Se acerca a mirar. Error.",
                "dud",
            ),
            _o(
                20,
                "🧨 {who} tira un petardo de Fallas. Lo oyen hasta en Valencia, que "
                "contestan con una mascletà.",
                "boom",
            ),
        ),
    ),
    Use(
        "confeti",
        "Lanzar",
        outcomes=(
            _o(
                70,
                "🎉 {who} lanza confeti por todo el canal. Lo vais a encontrar en el "
                "sofá hasta 2031.",
            ),
            _o(30, "🎉 {who} lanza confeti para celebrar... nada. Pero con mucho entusiasmo."),
        ),
    ),
    Use(
        "globo",
        "Lanzar",
        Target.MEMBER,
        outcomes=(
            _o(
                60,
                "🎈 {who} le revienta un globo de agua a {target}. Empapado como en "
                "agosto en la Feria de Málaga.",
                "hit",
            ),
            _o(
                25,
                "🎈 El globo de {who} revienta en su mano antes de lanzarlo. Mojado y humillado.",
                "backfire",
            ),
            _o(
                15,
                "🎈 {who} lanza el globo a {target} y rebota sin romperse. Globo de "
                "calidad alemana.",
                "miss",
            ),
        ),
    ),
    Use(
        "ramo",
        "Regalar",
        Target.MEMBER,
        outcomes=(
            _o(
                70,
                "💐 {who} le regala un ramo de flores a {target}. Qué detalle, mi amor. "
                "Esto se merece un perreo lento.",
                "love",
            ),
            _o(
                30,
                "💐 {who} le regala flores a {target}, que es alérgico. Estornudo en "
                "Dolby Surround.",
                "allergy",
            ),
        ),
        on_self="💐 {who} se regala un ramo de flores a sí mismo. Quiérete, que nadie lo "
        "va a hacer por ti.",
    ),
    Use(
        "abrazo",
        "Dar",
        Target.MEMBER,
        outcomes=(
            _o(
                80,
                "🤗 {who} le da un abrazo a {target}. De los de verdad, con palmadita "
                "en la espalda.",
                "hug",
            ),
            _o(
                20,
                "🤗 {who} le da un abrazo a {target} tan fuerte que le cruje la "
                "espalda. Gratis el quiropráctico.",
                "crack",
            ),
        ),
        on_self="🤗 {who} se abraza a sí mismo. Está bien. Todo va a estar bien.",
    ),
    Use(
        "carta",
        "Enviar",
        Target.MEMBER,
        outcomes=(
            _o(
                25,
                "💌 {target}, alguien de este servidor te ha mandado una carta anónima: "
                "«Me gusta cómo pierdes en la ruleta. Con dignidad.»",
            ),
            _o(
                25,
                "💌 {target}, carta anónima para ti: «Cada vez que dices "
                '"una última tirada" me enamoro un poco más.»',
            ),
            _o(
                25,
                "💌 {target}, te ha llegado una carta anónima: «Eres el IMV de mi "
                "vida: poquito, pero todos los días.»",
            ),
            _o(
                25,
                "💌 {target}, una carta sin remite: «Si fueras un impuesto, serías el "
                "IGIC: más llevadero que los de la península.»",
            ),
        ),
    ),
    Use(
        "chancla",
        "Lanzar",
        Target.MEMBER,
        outcomes=(
            _o(
                70,
                "🩴 {who} lanza la chancla de madre a {target}. Trayectoria "
                "teledirigida, impacto garantizado. Ninguna madre ha fallado jamás.",
                "hit",
            ),
            _o(
                30,
                "🩴 {who} lanza la chancla a {target} y falla. Es la primera vez en la "
                "historia. La chancla vuelve sola a su dueño.",
                "miss",
            ),
        ),
    ),
    Use(
        "bumeran",
        "Lanzar",
        Target.MEMBER,
        outcomes=(
            _o(
                50,
                "🪃 {who} le lanza un bumerán a {target}. Le da, vuelve y le da a {who}. "
                "Dos por uno.",
                "both",
            ),
            _o(30, "🪃 {who} lanza el bumerán a {target}. No vuelve. Era un palo.", "stick"),
            _o(
                20,
                "🪃 El bumerán de {who} se pierde en el horizonte. Lo encuentra Robuso "
                "en Hong Kong tres días después.",
                "lost",
            ),
        ),
    ),
    Use(
        "cucaracha",
        "Soltar",
        outcomes=(
            _o(
                50,
                "🪳 {who} suelta una cucaracha voladora en el canal. Gritos, sillas por "
                "los aires y alguien se sube a la mesa.",
                "panic",
            ),
            _o(
                30,
                "🪳 {who} suelta una cucaracha. Se posa en el micro de Jovani y se pone "
                "a cantar. Ahora es parte del grupo.",
                "singer",
            ),
            _o(
                20,
                "🪳 {who} suelta una cucaracha, que vuela directa a su cara. La "
                "naturaleza es justa.",
                "backfire",
            ),
        ),
    ),
    Use(
        "extintor",
        "Apagar el chat",
        outcomes=(
            _o(
                60,
                "🧯 {who} vacía un extintor en el canal. Se acabaron los fuegos, las "
                "polémicas y la visibilidad.",
            ),
            _o(
                40,
                "🧯 {who} saca el extintor para apagar la discusión. Funciona durante "
                "doce segundos. Luego alguien dice «pues la tortilla sin cebolla...».",
            ),
        ),
    ),
    Use(
        "agua_bendita",
        "Bendecir",
        Target.MEMBER,
        outcomes=(
            _o(
                60,
                "💦 {who} rocía a {target} con agua bendita. Quedan perdonados sus "
                "pecados, menos los de la ruleta.",
                "bless",
            ),
            _o(40, "💦 {who} bendice a {target}. Sale humo. Habrá que hablar de esto.", "smoke"),
        ),
    ),
    # 🧰 Bazar ---------------------------------------------------------------------------
    Use(
        "vuvuzela",
        "Tocar",
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(70, "📯 {who} toca la vuvuzela. BZZZZZZZZZZZZZZZZZZZZZ. Sudáfrica 2010 ha vuelto."),
            _o(
                30,
                "📯 {who} sopla la vuvuzela con tanta fuerza que se le salen las "
                "lentillas. BZZZ... bzz... bz.",
            ),
        ),
    ),
    Use("bola8", "Agitar", consumes=False, cooldown=20, special="eightball"),
    Use("d20", "Tirar", consumes=False, cooldown=20, special="d20"),
    Use("megafono", "Gritar", Target.TEXT, special="megaphone"),
    Use("caja", "Abrir", special="mystery"),
    Use(
        "bocina",
        "Pitar",
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(
                50,
                "🚌 {who} pita como una guagua de Global bajando por la GC-1. "
                "¡PIIIII! Todos a un lado.",
            ),
            _o(
                50,
                "🚌 {who} toca la bocina de la guagua. La guagua pasa de largo igual. "
                "La siguiente, en 40 minutos.",
            ),
        ),
    ),
    # 🍀 Amuletos ------------------------------------------------------------------------
    # 📎 Ventanilla ----------------------------------------------------------------------
    Use(
        "multa",
        "Multar",
        Target.MEMBER,
        outcomes=(
            _o(
                100,
                "🚓 {who} le pone una multa de la DGT a {target}: {multa}. "
                "{importe} € y {puntos}. Recurra si quiere: no sirve de nada.",
            ),
        ),
        pools={
            "multa": (
                "circular a 121 por la GC-1",
                "aparcar en doble fila delante del casino",
                "mirar el móvil mientras perdía en la ruleta",
                "llevar el perro de copiloto sin cinturón",
                "adelantar a una guagua por la derecha",
                "pitar a un ministro en la Castellana",
                "conducir con chancletas y con prisas",
                "no usar el intermitente desde 2019",
                "tunear un Seat Ibiza sin homologar el alerón",
            ),
            "importe": ("100", "200", "500", "600", "1.000"),
            "puntos": ("sin puntos", "2 puntos", "4 puntos", "6 puntos"),
        },
        on_self="🚓 {who} se multa a sí mismo. Perro Sanxe aplaude con las orejas: "
        "por fin alguien que le ahorra trabajo.",
    ),
    Use(
        "burofax",
        "Enviar",
        Target.MEMBER,
        outcomes=(
            _o(
                100,
                "📮 BUROFAX para {target} de parte de {who}: «Por la presente le "
                "comunico {motivo}. Sin otro particular, reciba un cordial saludo.»",
            ),
        ),
        pools={
            "motivo": (
                "que me debe 3 tiradas de ruleta desde el martes",
                "que mi tupper sigue en su casa y lo quiero de vuelta",
                "mi más enérgica protesta por su forma de jugar al blackjack",
                "que ha sido usted despedido de este servidor (es broma, por ahora)",
                "que su tortilla llevaba cebolla y no me lo dijo",
                "que reclamo la custodia compartida del meme de ayer",
                "mi intención de demandarle por daños morales tras su último audio",
            ),
        },
        on_self="📮 {who} se manda un burofax a sí mismo. Le llega a los nueve días "
        "hábiles. Lo firma. Se notifica. Burocracia perfecta.",
    ),
    Use(
        "dni_falso",
        "Cambiarte el nombre",
        Target.TEXT,
        special="nickname",
    ),
    # 🌹 La Moncloa ----------------------------------------------------------------------
    Use(
        "uco",
        "Dar el chivatazo",
        Target.MEMBER,
        outcomes=(
            _o(
                85,
                "🚨 {who} da un chivatazo a la UCO. Registran la casa de {target} y "
                "encuentran: {hallazgo}. Diligencias abiertas.",
                "raid",
            ),
            _o(
                15,
                "🚨 {who} llama a la UCO para denunciar a {target}, pero la UCO ya "
                "estaba investigando a {who}. Sorpresa.",
                "backfire",
            ),
        ),
        pools={
            "hallazgo": (
                "1.000 mascarillas con sobrecoste",
                "un sobre con «gastos de representación»",
                "una agenda con iniciales que no quieren decir nada",
                "un pendrive con la contabilidad B del servidor",
                "tres tuppers que no son suyos",
                "una fontanera escondida en el armario",
                "una plaza de asesor sin funciones",
                "una foto firmada de Perro Sanxe",
            ),
        },
        on_self="🚨 {who} se denuncia a sí mismo a la UCO. Le dan un pin de "
        "colaborador ejemplar. Y le registran la casa, por si acaso.",
    ),
    Use(
        "pegasus",
        "Espiar",
        Target.MEMBER,
        outcomes=(
            _o(
                85,
                "📱 {who} activa Pegasus en el móvil de {target}. Último mensaje "
                "interceptado: «{mensaje}». Información clasificada.",
                "spy",
            ),
            _o(
                15,
                "📱 {who} intenta espiar a {target} con Pegasus, pero el móvil de "
                "{target} ya estaba pinchado por otros tres ministerios. Cola de "
                "espera.",
                "busy",
            ),
        ),
        pools={
            "mensaje": (
                "mamá, ¿qué hay de comer?",
                "una última tirada y lo dejo",
                "no le digas a nadie que me gusta el reguetón",
                "¿alguien sabe hacer la renta?",
                "ya voy, ya voy, estoy saliendo (está en pijama)",
                "jajajaja",
                "¿esto lo ve alguien más?",
                "mañana empiezo la dieta",
            ),
        },
        on_self="📱 {who} se espía a sí mismo con Pegasus. Descubre que tiene 4.000 "
        "fotos de memes. Nada que no supiera.",
    ),
    Use(
        "indulto",
        "Indultar",
        Target.MEMBER,
        outcomes=(
            _o(
                100,
                "📜 {who} concede un indulto a {target} por {delito}. Publicado en "
                "el BOE un martes a las 23:58, para que no se entere nadie.",
            ),
        ),
        pools={
            "delito": (
                "perder 20.000 Y$ en la ruleta sin pestañear",
                "poner reguetón en el canal de voz a las 4 de la mañana",
                "no devolver el tupper",
                "hacer spam de pegatinas",
                "decir que el gofio está sobrevalorado",
                "irse a Hong Kong sin avisar",
            ),
        },
        on_self="📜 {who} se indulta a sí mismo. Un clásico de la casa. El BOE ni pestañea.",
    ),
    Use(
        "bulo",
        "Publicar",
        outcomes=(
            _o(
                100,
                "🗞️ ÚLTIMA HORA, según fuentes de la máquina del fango: «{titular}». "
                "Difundido por {who}. Verificado por nadie.",
            ),
        ),
        pools={
            "titular": (
                "Jovani Vázquez será el próximo ministro de Hacienda",
                "el gofio cura la resaca, según un estudio de la Universidad de Teror",
                "la ruleta del casino está trucada a favor del rojo (falso: está a favor "
                "de la banca)",
                "Robuso ha comprado medio Hong Kong con yapdollars",
                "Perro Sanxe estudia un impuesto a los memes",
                "el IMV pasará a pagarse en plátanos de Canarias",
                "el Falcon ya tiene programa de puntos",
                "confirmado: la tortilla con cebolla es inconstitucional",
            ),
        },
    ),
    Use("cis", "Publicar", special="cis"),
    Use(
        "mascarillas",
        "Vender",
        Target.MEMBER,
        outcomes=(
            _o(
                80,
                "😷 {who} le vende a {target} un lote de mascarillas con un sobrecoste "
                "del {pct} %. Comisión para un intermediario que no conoce nadie.",
                "deal",
            ),
            _o(
                20,
                "😷 {who} intenta colocarle mascarillas a {target}, pero {target} ya "
                "tenía un primo en el ministerio que se las vendía más caras.",
                "outbid",
            ),
        ),
    ),
    Use(
        "rueda_prensa",
        "Convocar",
        outcomes=(
            _o(
                50,
                "🎤 {who} convoca una rueda de prensa. Sin preguntas. Lee un folio, "
                "dice «gracias» y se va. Periodismo de alto nivel.",
            ),
            _o(
                30,
                "🎤 {who} da una rueda de prensa por plasma. La conexión se cae a los "
                "dos minutos. Nadie lo nota.",
            ),
            _o(
                20,
                "🎤 {who} da una rueda de prensa y acepta UNA pregunta. Responde a "
                "otra. Ovación en la sala.",
            ),
        ),
    ),
    # 🇵🇷 Rincón boricua -------------------------------------------------------------------
    Use(
        "perreo",
        "Sacar a perrear",
        Target.MEMBER,
        outcomes=(
            _o(
                70,
                "💃 {who} saca a perrear a {target}. Hasta abajo, mi amor. ¡Wepa! "
                "Jovani lo aprueba.",
                "yes",
            ),
            _o(
                30,
                "💃 {who} saca a perrear a {target}, que le deja con la mano tendida. "
                "Calabaza nivel Puerto Rico.",
                "no",
            ),
        ),
        on_self="💃 {who} perrea solo en mitad del canal. Nadie le saca, pero nadie le "
        "para. Libertad.",
    ),
    Use(
        "reggaeton",
        "Poner",
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(
                40,
                "🔊 {who} pone reguetón a todo volumen. Dembow en el canal, mi amor. "
                "Los vecinos tocan el techo con la escoba.",
            ),
            _o(30, "🔊 {who} pone un temazo de Jovani. Tres personas lloran. Una de emoción."),
            _o(
                30, "🔊 {who} enciende el altavoz y suena una sevillana por error. Se perrea igual."
            ),
        ),
    ),
    # 🇭🇰 Importación de Hong Kong --------------------------------------------------------
    Use("robuso", "Llamar", special="robuso"),
    Use(
        "dimsum",
        "Compartir",
        Target.MEMBER,
        outcomes=(
            _o(
                70,
                "🥟 {who} comparte una cesta de dim sum con {target}. Har gow, siu mai "
                "y una amistad que cruza continentes.",
                "shared",
            ),
            _o(
                30,
                "🥟 {who} le ofrece dim sum a {target} y se lo come todo antes de que "
                "llegue. Robuso lo vio venir desde Hong Kong.",
                "eaten",
            ),
        ),
    ),
    Use(
        "sobre_rojo",
        "Regalar",
        Target.MEMBER,
        outcomes=(
            _o(
                70,
                "🧧 {who} le da un sobre rojo a {target} por el Año Nuevo chino. Dentro: "
                "buena suerte. Dinero, no; que eso tributa.",
                "luck",
            ),
            _o(
                30,
                "🧧 {who} le da un sobre rojo a {target}. Dentro hay una nota: "
                "«Debes 3 Y$ a Robuso». Tradición moderna.",
                "debt",
            ),
        ),
    ),
    # 💀 Lo que no debería venderse --------------------------------------------------------
    Use(
        "piedra",
        "Lanzar",
        Target.MEMBER,
        consumes=False,
        cooldown=120,
        outcomes=(
            _o(
                60,
                "🪨 {who} le lanza la piedra a {target}. No le da, porque la piedra "
                "está en una mochila de Discord. Pero la intención es lo que cuenta.",
                "miss",
            ),
            _o(
                40,
                "🪨 {who} mira a {target} y le enseña la piedra. Solo eso. Mucho mensaje.",
                "stare",
            ),
        ),
        on_self="🪨 {who} mira su piedra. La piedra le mira. Un vínculo así no se compra "
        "con dinero. Bueno, sí: por 1 Y$.",
    ),
    # 🍀 Supersticiones: dan una frase, nunca una probabilidad ----------------------------
    Use(
        "trebol",
        "Frotar",
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(
                45,
                "🍀 {who} frota el trébol de cuatro hojas antes de jugar. La ruleta ni se "
                "inmuta: las probabilidades vienen en el código, no en el trébol.",
            ),
            _o(
                35,
                "🍀 {who} frota el trébol tan fuerte que se le cae una hoja. Ahora es un "
                "trébol normal. La suerte, también.",
                "broken",
            ),
            _o(
                20,
                "🍀 {who} frota el trébol y {collector} aparece a cobrar el 19 % de la "
                "suerte. Retención a cuenta de la buena fortuna.",
            ),
        ),
    ),
    Use(
        "rosario",
        "Rezar",
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(
                50,
                "📿 {who} reza el rosario de la abuela entre tirada y tirada. La abuela, "
                "desde arriba, pide que deje de apostar.",
            ),
            _o(
                30,
                "📿 {who} reza un misterio doloroso. Muy adecuado para el saldo que tiene.",
            ),
            _o(
                20,
                "📿 {who} se lía con las cuentas del rosario y acaba rezando la tabla del "
                "7. Le sale mejor que la renta.",
            ),
        ),
    ),
    Use(
        "calzoncillos",
        "Ponértelos",
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(
                50,
                "🩲 {who} se pone la ropa interior roja de Nochevieja. Si no es 31 de "
                "diciembre, la tradición no garantiza nada. Si lo es, tampoco.",
            ),
            _o(
                30,
                "🩲 {who} se pone la ropa interior roja del revés. Según la abuela, eso "
                "da el doble de suerte. Según la ciencia, no.",
            ),
            _o(
                20,
                "🩲 {who} enseña la ropa interior roja a todo el canal. Nadie lo había "
                "pedido, mi amor.",
                "flash",
            ),
        ),
    ),
    Use(
        "pancracio",
        "Pedir",
        consumes=False,
        cooldown=60,
        pools={
            "deseo": (
                "trabajo",
                "salud",
                "que salga el rojo",
                "una cita previa en el SEPE",
                "que Perro Sanxe se olvide de su renta",
                "un piso de alquiler por menos de 1.000 €",
            ),
        },
        outcomes=(
            _o(
                60,
                "🙏 {who} le pone perejil a San Pancracio y le pide {deseo}. El santo "
                "toma nota y le da número: van por el 2.",
            ),
            _o(
                40,
                "🙏 {who} le pide {deseo} a San Pancracio sin perejil. El santo no "
                "trabaja sin perejil. Ni los domingos.",
                "no_parsley",
            ),
        ),
    ),
    # 💎 Vida de rico ----------------------------------------------------------------------
    Use(
        "champan",
        "Descorchar",
        outcomes=(
            _o(
                60,
                "🥂 {who} descorcha el champán francés de los caros. Burbujas finas, "
                "brindis largo y una factura con el 15 % de IGIC de lujo para "
                "{collector}.",
                "pop",
            ),
            _o(
                25,
                "🥂 {who} abre el champán y lo sirve en vaso de plástico. El sumiller "
                "llora en francés.",
            ),
            _o(
                15,
                "🥂 {who} descorcha el champán y resulta que era cava. Nadie lo nota. "
                "Nadie lo nota nunca.",
                "fake",
            ),
        ),
    ),
    # 🐾 Tienda de animales ----------------------------------------------------------------
    Use(
        "pelota",
        "Lanzar",
        Target.MEMBER,
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(
                50,
                "🎾 {who} le lanza la pelota de tenis babeada a {target}. Llega llena de "
                "babas. Era de esperar.",
                "hit",
            ),
            _o(
                30,
                "🎾 {who} lanza la pelota hacia {target} y medio barrio de perros sale "
                "corriendo detrás. {target} acaba en el suelo.",
                "dogs",
            ),
            _o(
                20,
                "🎾 {target} caza al vuelo la pelota que le lanza {who} y se la queda. "
                "Ahora la pelota tiene dos dueños y ninguno quiere tocarla.",
                "caught",
            ),
        ),
        on_self="🎾 {who} se lanza la pelota a sí mismo y va a buscarla. Alguien tenía "
        "que hacerlo si no hay perro.",
    ),
    # 🎫 Peña de la porra --------------------------------------------------------------
    Use(
        "bufanda",
        "Ondear",
        Target.MEMBER,
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(
                55,
                "🧣 {who} ondea la bufanda de la peña por {target}: «¡Sí se puede, sí se "
                "puede!». Medio bar se une al cántico.",
                "cheer",
            ),
            _o(
                30,
                "🧣 {who} le pone la bufanda a {target} para la suerte. Huele a puro y a "
                "Liga del 98, pero da calorcito.",
                "cheer",
            ),
            _o(
                15,
                "🧣 {who} ondea la bufanda con tanto ímpetu que le da en la cara a {target}. "
                "Ánimo con daños colaterales.",
                "backfire",
            ),
        ),
        on_self="🧣 {who} se anima a sí mismo con la bufanda. La peña es él solo, pero "
        "la peña nunca falla.",
    ),
    Use(
        "silbato",
        "Pitar",
        Target.MEMBER,
        consumes=False,
        cooldown=60,
        outcomes=(
            _o(
                40,
                "🟨 ¡Priiii! {who} le saca amarilla a {target} por protestar. {target} "
                "pide el VAR, pero el VAR está tomando café.",
                "yellow",
            ),
            _o(
                25,
                "🟨 ¡Priiii, priiii! {who} le saca la roja directa a {target}. Al vestuario, "
                "y sin ducha.",
                "red",
            ),
            _o(
                20,
                "🟨 {who} pita penalti a favor de {target}. Nadie sabe de qué, pero el "
                "árbitro siempre tiene razón.",
                "penalty",
            ),
            _o(
                15,
                "🟨 {who} va a pitarle a {target} y aparece {collector} pidiendo la factura "
                "del silbato. Los árbitros también hacen la declaración.",
                "collector",
            ),
        ),
        on_self="🟨 {who} se pita a sí mismo fuera de juego. Honestidad arbitral, eso "
        "sí que no se ve en la Liga.",
    ),
)

USES: dict[str, Use] = {use.key: use for use in _USES}
#: Usos que necesitan la base de datos o Discord y los resuelve el cog.
COG_SPECIALS = frozenset({"mystery", "megaphone", "nickname"})

# -- Textos de los usos especiales -----------------------------------------------------

FORTUNES: tuple[str, ...] = (
    "Un dinero inesperado llegará a tu vida. Perro Sanxe ya lo sabe.",
    "La próxima tirada es la buena. (La galleta no se hace responsable).",
    "Alguien de este servidor piensa en ti. Probablemente para pedirte un Bizum.",
    "Hoy no es tu día. Mañana tampoco, pero tendrás IMV.",
    "Quien guarda, halla. Quien juega, paga IGIC.",
    "Tu número de la suerte es el 17. El de la ruleta, el 0.",
    "Un viaje largo te espera. Probablemente a Hong Kong, a ver a Robuso.",
    "Sé como el gofio: humilde, versátil y de aquí.",
    "La paciencia es la madre de la ciencia. La impaciencia, del Crash.",
    "Hacienda somos todos, pero unos más que otros.",
    "No dejes para mañana lo que puedes declarar hoy.",
    "La suerte sonríe a los valientes. A los que se plantan con 12, no.",
    "Un desconocido te devolverá un tupper. Créetelo.",
    "Tu futuro es brillante, como el Falcon recién encerado.",
    "Esta galleta ha sido fabricada en una nave sin licencia. Disfrútala.",
    "Errar es humano. Echarle la culpa a la máquina del fango, más.",
)

EIGHTBALL: tuple[tuple[str, str], ...] = (
    ("Sí, mi amor. Clarísimo.", "yes"),
    ("Sin ninguna duda. Lo pone el BOE.", "yes"),
    ("Las encuestas del CIS dicen que sí.", "yes"),
    ("Pregúntale a Perro Sanxe, que es el que manda.", "maybe"),
    ("Ahora no, estoy cobrando el IMV.", "maybe"),
    ("Respuesta poco clara. Vuelve a agitar, pero paga IGIC.", "maybe"),
    ("Depende de cómo salga la ruleta.", "maybe"),
    ("Robuso dice que sí, pero en Hong Kong son las 4 de la mañana.", "maybe"),
    ("No cuentes con ello.", "no"),
    ("Mis fuentes (la UCO) dicen que no.", "no"),
    ("Ni de broma. Ni en Hong Kong.", "no"),
    ("No. Y no insistas, que te veo venir.", "no"),
)

CIS_PARTIES: tuple[str, ...] = (
    "el partido del gofio",
    "los del reguetón",
    "los de la tortilla sin cebolla",
    "el bloque de Hong Kong",
    "la abstención por pereza",
)


def _pick(rng: random.Random, outcomes: tuple[Outcome, ...]) -> Outcome:
    return rng.choices(outcomes, weights=[o.weight for o in outcomes], k=1)[0]


def _fill(template: str, values: dict[str, str]) -> str:
    return template.format(**values)


def resolve(
    use: Use,
    *,
    who: str,
    item: str,
    rng: random.Random,
    now: float,
    target: str | None = None,
    target_is_self: bool = False,
    target_is_bot: bool = False,
) -> UseResult:
    """Decide qué pasa al usar un objeto y arma el mensaje público.

    Args:
        who: Nombre de quien lo usa, ya en negrita.
        item: Emoji y nombre del objeto, ya formateados.
        rng: Azar inyectable (las pruebas pasan una semilla).
        now: Momento del uso (epoch); lo usa la llamada a Robuso.
        target: Mención del objetivo, si el uso lo pide.
        target_is_self: Si el objetivo es quien lo usa.
        target_is_bot: Si el objetivo es un bot.
    """
    if use.special in COG_SPECIALS:
        raise ValueError(f"El uso {use.key} se resuelve en el cog, no aquí.")
    values: dict[str, str] = {
        "who": who,
        "item": item,
        "target": target or "",
        "collector": TAX_COLLECTOR,
    }
    for name, options in use.pools.items():
        values[name] = rng.choice(options)
    values["pct"] = str(rng.choice((150, 200, 300, 400, 666, 1_000)))

    if use.target is Target.MEMBER and target_is_bot:
        return UseResult(_fill(rng.choice(BOT_LINES), values), frozenset({"bot"}))
    if use.target is Target.MEMBER and target_is_self:
        return UseResult(_fill(use.on_self or SELF_LINE, values), frozenset({"self"}))

    if use.special == "fortune":
        return UseResult(f"🥠 {who} abre una galleta de la suerte: *«{rng.choice(FORTUNES)}»*")
    if use.special == "eightball":
        answer, flag = rng.choice(EIGHTBALL)
        return UseResult(f"🎱 {who} agita la bola 8 mágica... **{answer}**", frozenset({flag}))
    if use.special == "d20":
        return _d20(who, rng.randint(1, 20))
    if use.special == "padron":
        return _padron(who, hot=rng.random() < 0.2)
    if use.special == "robuso":
        return _robuso(who, now)
    if use.special == "cis":
        return _cis(who, rng)

    outcome = _pick(rng, use.outcomes)
    flags = frozenset({outcome.flag}) if outcome.flag else frozenset()
    return UseResult(_fill(outcome.text, values), flags)


def _d20(who: str, roll: int) -> UseResult:
    if roll == 20:
        return UseResult(
            f"🎲 {who} tira el d20... **¡20 NATURAL!** Crítico. El servidor entero se "
            "pone en pie. Jovani suelta un «¡wepa!» que se oye en Puerto Rico.",
            frozenset({"nat20"}),
        )
    if roll == 1:
        return UseResult(
            f"🎲 {who} tira el d20... **1**. Pifia. Se le cae el dado debajo del sofá "
            "y, al agacharse, se da con la mesa.",
            frozenset({"nat1"}),
        )
    comment = (
        "Fallo." if roll < 6 else "Meh." if roll < 11 else "No está mal." if roll < 16 else "¡Bien!"
    )
    return UseResult(f"🎲 {who} tira el d20... **{roll}**. {comment}")


def _padron(who: str, *, hot: bool) -> UseResult:
    if hot:
        return UseResult(
            f"🌶️ {who} se come un pimiento de Padrón... **¡Y PICA!** Le salen lágrimas, "
            "humo por las orejas y una disculpa a medio servidor. Unos pican y otros no.",
            frozenset({"hot"}),
        )
    return UseResult(
        f"🌶️ {who} se come un pimiento de Padrón. No pica. Decepción y alivio a partes iguales."
    )


def _robuso(who: str, now: float) -> UseResult:
    """Llamada a Robuso con la hora real de Hong Kong."""
    local = datetime.fromtimestamp(now, HONG_KONG)
    clock = f"{local:%H:%M}"
    hour = local.hour
    if hour < 7:
        text = (
            f"📞 {who} llama a Robuso a Hong Kong. Allí son las **{clock}**. "
            "Robuso contesta en cantonés con palabras que no salen en el diccionario "
            "y cuelga. Normal."
        )
        return UseResult(text, frozenset({"asleep"}))
    if hour < 10:
        text = (
            f"📞 {who} llama a Robuso. En Hong Kong son las **{clock}** y está "
            "desayunando congee en un cha chaan teng. Manda saludos y una foto del té con "
            "leche."
        )
    elif hour < 19:
        text = (
            f"📞 {who} llama a Robuso. En Hong Kong son las **{clock}**: está "
            "currando y dice que le llames luego, que su jefe le está mirando. Cuelga "
            "susurrando «te quiero, cabrón»."
        )
    else:
        text = (
            f"📞 {who} llama a Robuso. En Hong Kong son las **{clock}** y está de fiesta "
            "en Lan Kwai Fong. Solo se oye música, gritos y algo que suena a karaoke de "
            "Jovani."
        )
    return UseResult(text, frozenset({"awake"}))


def _cis(who: str, rng: random.Random) -> UseResult:
    """Encuesta del CIS en la que siempre gana el mismo."""
    rival = rng.choice(CIS_PARTIES)
    winner = rng.randint(43, 61) + rng.randint(0, 9) / 10
    loser = rng.randint(4, 15) + rng.randint(0, 9) / 10
    rest = round(100 - winner - loser, 1)
    fmt = lambda n: f"{n:.1f}".replace(".", ",")  # noqa: E731
    return UseResult(
        f"📊 {who} publica una encuesta del CIS. Estimación de voto: **{TAX_COLLECTOR} "
        f"{fmt(winner)} %**, {rival} {fmt(loser)} %, resto {fmt(rest)} %. Margen de error: "
        "el que haga falta."
    )


# -- Caja botín, megáfono y apodo ------------------------------------------------------

#: Precio de catálogo a partir del cual un premio de la caja botín es el gordo.
MYSTERY_JACKPOT = 100_000
#: Cuánto pesa el precio al sortear la caja: con 0,4 un objeto 100 veces más caro
#: sale unas 6 veces menos. Con el surtido de serie, la caja (2.500 Y$) devuelve
#: de media un 70 % de su precio en objetos y da el gordo una vez de cada 300.
#: Que pierda dinero es la gracia: como las de los videojuegos.
MYSTERY_EXPONENT = 0.4


def mystery_weight(price: int) -> float:
    """Peso de un premio en la caja botín: lo caro sale menos."""
    return 1 / max(1, price) ** MYSTERY_EXPONENT


def mystery_expected(prices: list[int]) -> float:
    """Valor esperado (a precio de catálogo) de abrir una caja con estos premios."""
    weights = [mystery_weight(p) for p in prices]
    total = sum(weights)
    return sum(p * w for p, w in zip(prices, weights, strict=True)) / total if total else 0.0


def pick_mystery(prices: list[int], rng: random.Random) -> int:
    """Índice del premio que toca en la caja botín."""
    weights = [mystery_weight(p) for p in prices]
    return rng.choices(range(len(prices)), weights=weights, k=1)[0]


def mystery_text(who: str, prize: str, *, price: int, dupe: bool) -> UseResult:
    """Mensaje de la caja botín abierta."""
    flags = {"jackpot"} if price >= MYSTERY_JACKPOT else set()
    if dupe:
        flags.add("dupe")
    if price >= MYSTERY_JACKPOT:
        lead = f"🎁 {who} abre la caja botín y... **¡EL GORDO!** Le toca {prize}. ¡WEPA!"
    elif price <= 50:
        lead = f"🎁 {who} abre la caja botín con mucha ilusión. Le toca... {prize}. Ya."
    else:
        lead = f"🎁 {who} abre la caja botín. Le toca {prize}."
    tail = " Repe, mi amor." if dupe else ""
    return UseResult(
        f"{lead}{tail}\n-# El anteproyecto de ley de cajas botín de 2022 sigue en un cajón: "
        f"{TAX_COLLECTOR} todavía no sabe cómo cobrarlas.",
        frozenset(flags),
    )


MAX_SHOUT = 150
MAX_NICK = 32
_SPACES = re.compile(r"\s+")


def clean_shout(text: str, limit: int = MAX_SHOUT) -> str:
    """Una sola línea, sin espacios de más y recortada a `limit` caracteres.

    Raises:
        ValueError: Si queda vacío.
    """
    cleaned = _SPACES.sub(" ", text or "").strip()
    if not cleaned:
        raise ValueError("Escribe algo, mi amor. El megáfono no grita silencios.")
    return cleaned[:limit]


def megaphone_text(who: str, shout: str) -> UseResult:
    """Mensaje del megáfono; `shout` ya viene limpio y sin markdown."""
    return UseResult(f"📢 {who} coge el megáfono:\n## {shout}")


def nickname_text(who: str, old: str, new: str) -> UseResult:
    """Mensaje del DNI falso."""
    return UseResult(
        f"🪪 {who} enseña un DNI falso en la ventanilla. A partir de ahora es **{new}**. "
        f"El funcionario ni levanta la vista.\n-# Antes: {old}",
        frozenset({"nickname"}),
    )
