"""Las ONGs del bot y los donativos deducibles.

Las ocho ONGs son de broma: dicen servir a una causa y hacen justo lo
contrario, o se gastan lo donado en conseguir más dinero. Donar es un gasto
de verdad (el dinero se queda en la cuenta de la ONG, no vuelve a nadie), pero
desgrava: el art. 19.1 de la Ley 49/2002 permite deducir de la cuota del IRPF
el 80 % de los primeros 250 € y el 40 % del resto. La regla está en
`bot.services.taxes.donation_deduction` y se aplica en la declaración del lunes.
Es el truco de siempre: donar para pagar menos a Perro Sanxe.

Tratamiento fiscal del donativo: no paga IGIC (no hay contraprestación, art. 4
de la Ley 20/1991) ni Donaciones (quien recibe es una entidad, y el ISD solo
grava a personas físicas: art. 3 de la Ley 29/1987).

Cada ONG tiene un monedero con `user_id` negativo en `economy_wallets`. Los
ids no se cambian nunca: son los que guardan el dinero.

Además de desgravar, donar mucho a una ONG te hace mecenas suyo (`PATRON_LEVELS`, por
lo donado a esa ONG desde siempre) y cada viernes a las 20:00 cada ONG sortea un regalo
entre quienes le donaron en los siete días anteriores (`raffle_window`, `draw_winner`):
un boleto por donar y más cuanto más alto es tu nivel de mecenas. Al subir de nivel, la
ONG también te regala algo. El premio suele ser un objeto barato de la tienda; el
exclusivo de la ONG (`ONG_GIFTS`) sale pocas veces (`RAFFLE_EXCLUSIVE`) y seguro al
llegar a Oro y a Patrono. Los exclusivos no se venden, y ningún regalo mueve dinero
(como las cajas por jugar).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.services.taxes import (
    DONATION_BASE_LIMIT,
    DONATION_FULL_LIMIT,
    DONATION_FULL_RATE,
    DONATION_REST_RATE,
)


@dataclass(frozen=True, slots=True)
class Ong:
    """Una ONG del catálogo.

    Attributes:
        key: Identificador estable; es lo que se guarda en cada donativo.
        account_id: `user_id` (negativo) de su monedero.
        mission: Lo que dice que hace.
        reality: Lo que hace de verdad.
        spending: En qué se gasta un donativo; se elige uno al azar.
    """

    key: str
    account_id: int
    name: str
    emoji: str
    mission: str
    reality: str
    spending: tuple[str, ...]


ONGS: tuple[Ong, ...] = (
    Ong(
        key="desmontar",
        account_id=-1,
        name="Fundación Desmontando España",
        emoji="🧨",
        mission="Fomentar la convivencia y el diálogo entre todos los españoles.",
        reality=(
            "Desmontar España pieza a pieza. De momento solo ha conseguido desmontar "
            "la cuenta corriente de sus donantes."
        ),
        spending=(
            "un congreso en Bruselas sobre cómo organizar más congresos en Bruselas",
            "un estudio de 300 páginas que concluye que hace falta otro estudio",
            "las dietas de un viaje en primera para debatir sobre la desigualdad",
            "un mapa nuevo de España sin España",
            "una mesa de diálogo en Waterloo, con Puigdemont por videollamada",
            "el relator internacional que relata otra cosa",
            "un estudio de Tezanos que dice que España ya está desmontada al 87 %",
            "las pegatinas de «España nos roba» para el maletero del Peugeot",
            "un traductor de catalán a catalán para la tribuna del Congreso",
        ),
    ),
    Ong(
        key="ayudante",
        account_id=-2,
        name="Asociación Ayuda al Ayudante",
        emoji="🤝",
        mission="Ayudar a las personas que más lo necesitan.",
        reality="Ayuda a quien más lo necesita: su junta directiva.",
        spending=(
            "el coche de empresa del vicepresidente segundo",
            "una cena de gala para recaudar fondos para la próxima cena de gala",
            "la subida de sueldo del director de transparencia",
            "un curso de liderazgo en Marbella para el comité de ética",
            "la asesoría de un sobrino que no sabe lo que es una factura",
            "el regalo de puros y champán para el patrono de honor, como los de Bibi",
            "un máster convalidado al presidente para que firme mejor los cheques",
            "un despacho con vistas para el asesor del asesor",
            "la mascarilla de oro de la última campaña, comprada a un amigo de Koldo",
        ),
    ),
    Ong(
        key="mares",
        account_id=-3,
        name="Mares Limpios Sociedad Limitada",
        emoji="🛥️",
        mission="Proteger los océanos y la fauna marina.",
        reality=(
            "Supervisa el océano desde un yate de 40 metros que gasta más gasóleo "
            "que un pueblo entero."
        ),
        spending=(
            "gasóleo para el yate de supervisión",
            "una campaña con influencers en las Maldivas",
            "pajitas de papel para la fiesta en el yate",
            "el segundo yate, por si el primero se ensucia",
            "un vuelo en jet privado a Davos para hablar del clima",
            "el tapón pegado a la botella de champán de la cubierta",
            "un documental en el que el director sale buceando con una tortuga de plástico",
            "la limpieza de una playa en las Maldivas para la foto de Instagram",
            "una boya con forma de Falcon para el puerto deportivo",
        ),
    ),
    Ong(
        key="hambre",
        account_id=-4,
        name="Fundación Cubiertos de Plata contra el Hambre",
        emoji="🍽️",
        mission="Acabar con el hambre en el mundo.",
        reality=(
            "Acaba con el hambre de su patronato en un banquete anual con langosta "
            "y siete tenedores por persona."
        ),
        spending=(
            "langosta para el banquete solidario",
            "un sumiller para maridar la rueda de prensa",
            "manteles de hilo bordados con el logo de la fundación",
            "el café de especialidad del patronato (con mango, por supuesto)",
            "un menú degustación de 14 pases para debatir sobre la escasez",
            "jamón de bellota para la gala benéfica, cortado por un maestro cortador",
            "el desayuno con huevo y jugo de china de el bueno de la junta directiva",
            "una cena con Milei para pedirle que pase la motosierra por el hambre",
            "el catering del congreso contra el despilfarro alimentario",
        ),
    ),
    Ong(
        key="gamers",
        account_id=-5,
        name="Gamers Sin Fronteras",
        emoji="🎮",
        mission="Llevar videojuegos a los niños que no tienen consola.",
        reality=(
            "Los niños siguen sin consola; el presidente tiene tres y una silla gaming con masaje."
        ),
        spending=(
            "una silla gaming con luces RGB para la sede",
            "el pase de batalla de toda la junta directiva",
            "cajas de botín hasta que salió la skin dorada",
            "una PS5 Pro para probar el material antes de donarlo",
            "el Game Pass de la familia del tesorero",
            "un torneo benéfico de FIFA que ganó el presidente",
            "la reserva de GTA VI, que llegará cuando llegue",
            "un directo de 24 horas para recaudar para el siguiente directo",
            "un ratón de 300 € para el community manager",
        ),
    ),
    Ong(
        key="influencers",
        account_id=-6,
        name="Protectora de Influencers Abandonados",
        emoji="🤳",
        mission="Rescatar a los influencers que se han quedado sin seguidores.",
        reality="Los rescata en Dubái, con tu donativo y sin declarar nada en España.",
        spending=(
            "un viaje a Dubái para grabar un vídeo sobre la humildad",
            "un aro de luz para cada influencer rescatado",
            "seguidores comprados para que nadie se sienta solo",
            "un retiro espiritual en Bali con una foto cada diez minutos",
            "la mudanza a Andorra de un streamer en apuros",
            "un curso de cómo pedir perdón llorando en un vídeo",
            "el brunch de aguacate para la foto, que nadie se comió",
            "el sorteo de un iPhone que nunca le llegó a nadie",
            "un Lamborghini alquilado por horas para la miniatura",
        ),
    ),
    Ong(
        key="platano",
        account_id=-7,
        name="Plataforma Salvemos el Plátano Canario",
        emoji="🍌",
        mission="Defender el plátano de Canarias frente a la banana.",
        reality="En las cenas de la junta sirven banana de Ecuador porque sale más barata.",
        spending=(
            "una cata de bananas para conocer al enemigo",
            "un estudio que demuestra que las motitas son buenas",
            "un anuncio en la Península que nadie entendió",
            "un congreso en Madrid sobre el plátano sin un solo canario invitado",
            "un camión de bananas de Ecuador para el desayuno del congreso",
            "una estatua de un plátano gigante en una rotonda de Tenerife",
            "papas arrugadas con mojo para la junta, que eso también es Canarias",
            "la guagua de la junta hasta el Teide, para inspirarse",
            "un bocadillo de plátano con chorizo para la foto del presidente",
        ),
    ),
    Ong(
        key="jovani",
        account_id=-8,
        name="Fundación Amigos del Bot Jovani",
        emoji="🤖",
        mission="Mantener vivo al bot del servidor.",
        reality=(
            "El bot sigue en el mismo NAS; el dinero se va en memoria para que Chromium "
            "pinte ruletas."
        ),
        spending=(
            "un módulo de memoria para que Chromium pinte otra ruleta",
            "un ventilador para el NAS, que ya suena como un avión",
            "créditos de IA para escribir estas frases",
            "una camiseta de Jovani firmada por nadie",
            "un SAI por si se va la luz en mitad de una tirada",
            "el café de quien revisa los cambios a las tres de la mañana",
            "un disco duro nuevo, que el viejo hace ruidos raros",
            "un curso de Python para el administrador del servidor",
            "una entrada para un concierto de Jovani, aunque el bot no tenga piernas",
        ),
    ),
)
BY_KEY: dict[str, Ong] = {ong.key: ong for ong in ONGS}


def find_ong(text: str) -> Ong | None:
    """Busca una ONG por su clave o por una palabra de su nombre (sin mayúsculas)."""
    wanted = text.strip().lower()
    if not wanted:
        return None
    if wanted in BY_KEY:
        return BY_KEY[wanted]
    for ong in ONGS:
        if wanted in ong.name.lower():
            return ong
    return None


def pick_spending(ong: Ong, rng: random.Random | None = None) -> str:
    """En qué se ha gastado la ONG tu donativo."""
    return (rng or random).choice(ong.spending)


def deduction_rule_text() -> str:
    """La regla de la deducción en una línea, para enseñarla al donar."""
    first = f"{DONATION_FULL_LIMIT:,}".replace(",", ".")
    return (
        f"{DONATION_FULL_RATE:.0%} de los primeros {first} Y$ donados en la semana y "
        f"{DONATION_REST_RATE:.0%} del resto, hasta el {DONATION_BASE_LIMIT:.0%} de lo que "
        "ganes esa semana y nunca más del IRPF que hayas pagado"
    ).replace("%", " %")


# -- Mecenas y sorteos ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatronLevel:
    """Un nivel de mecenas de una ONG.

    Attributes:
        threshold: Lo donado a esa ONG, desde siempre, para llegar a él.
        tickets: Boletos de más en el sorteo semanal de esa ONG.
        exclusive: Probabilidad de que el regalo por llegar sea uno exclusivo de la ONG
            (si no, algo barato de la tienda).
    """

    name: str
    emoji: str
    threshold: int
    tickets: int
    exclusive: float


#: Probabilidad de que el premio del sorteo sea un regalo exclusivo de la ONG; si no, es
#: un objeto de la tienda de `GIFT_CHEAP_MAX` Y$ o menos.
RAFFLE_EXCLUSIVE = 0.15
GIFT_CHEAP_MAX = 500
PATRON_LEVELS: tuple[PatronLevel, ...] = (
    PatronLevel("Bronce", "🥉", 5_000, 1, RAFFLE_EXCLUSIVE),
    PatronLevel("Plata", "🥈", 25_000, 2, RAFFLE_EXCLUSIVE),
    PatronLevel("Oro", "🥇", 100_000, 4, 1.0),
    PatronLevel("Patrono", "🏛️", 500_000, 7, 1.0),
)


#: El sorteo: los viernes (`weekday` 4) a las 20:00, hora de Canarias. El lunes ya
#: tiene la Renta y el Patrimonio.
RAFFLE_WEEKDAY = 4
RAFFLE_HOUR = 20


def raffle_window(now: datetime) -> tuple[datetime, datetime]:
    """El último sorteo ya llegado: `(desde, hasta)`, siete días que acaban en él.

    `now` lleva zona horaria; las dos fechas salen en la misma.
    """
    days = (now.weekday() - RAFFLE_WEEKDAY) % 7
    end = (now - timedelta(days=days)).replace(hour=RAFFLE_HOUR, minute=0, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=7)
    return end - timedelta(days=7), end


def patron_level(total: int) -> PatronLevel | None:
    """El nivel de mecenas con `total` donado a una ONG (`None` por debajo de Bronce)."""
    return next((lvl for lvl in reversed(PATRON_LEVELS) if total >= lvl.threshold), None)


def next_patron_level(total: int) -> PatronLevel | None:
    """El siguiente nivel por alcanzar (`None` si ya es Patrono)."""
    return next((lvl for lvl in PATRON_LEVELS if total < lvl.threshold), None)


def raffle_tickets(total: int) -> int:
    """Boletos en el sorteo de una ONG: uno por donar esa semana y los de su nivel."""
    level = patron_level(total)
    return 1 + (level.tickets if level else 0)


def draw_winner(entries: dict[int, tuple[int, int]], rng: random.Random | None = None) -> int:
    """Saca el ganador del sorteo de una ONG.

    Args:
        entries: `{user_id: (donado esa semana, donado a la ONG desde siempre)}`.
    """
    users = sorted(entries)
    weights = [raffle_tickets(entries[user][1]) for user in users]
    return (rng or random).choices(users, weights)[0]


# -- Regalos exclusivos ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OngGift:
    """Un regalo que solo da una ONG: un objeto de la tienda que no se vende.

    Es un artículo oculto del surtido de serie (`shop_catalog`, como las piezas de las
    cajas), con su precio como valor. La ONG lo regala en su sorteo semanal y al subir de
    nivel de mecenas.

    Attributes:
        ong: Clave de la ONG que lo regala.
        key: Clave dentro de la ONG; el artículo es `ong_<ong>_<key>`.
        aisle: Pasillo de la tienda por su tema.
    """

    ong: str
    key: str
    emoji: str
    name: str
    price: int
    description: str
    aisle: str

    @property
    def catalog_key(self) -> str:
        """Clave del artículo en la tienda."""
        return f"ong_{self.ong}_{self.key}"


def _gifts(ong: str, aisle: str, *items: tuple[str, str, str, int, str]) -> tuple[OngGift, ...]:
    return tuple(
        OngGift(ong, key, emoji, name, price, text, aisle)
        for key, emoji, name, price, text in items
    )


ONG_GIFTS: tuple[OngGift, ...] = (
    *_gifts(
        "desmontar",
        "moncloa",
        (
            "pin",
            "📍",
            "Pin de la mesa de diálogo",
            80,
            "Regalo de la Fundación Desmontando España por dialogar con la cartera.",
        ),
        (
            "mapa",
            "🗺️",
            "Mapa de España por piezas",
            150,
            "Un puzle de 17 piezas que no encajan. Regalo de la Fundación.",
        ),
        (
            "acta",
            "📜",
            "Acta de una reunión en Waterloo",
            220,
            "Tres folios, dos firmas y una foto de grupo. No dice nada.",
        ),
        (
            "taza",
            "☕",
            "Taza «Yo también desmonté España»",
            60,
            "La taza de merchandising de la Fundación. No se vende en ningún sitio.",
        ),
        (
            "relator",
            "🎙️",
            "Micrófono del relator",
            300,
            "El micrófono con el que el relator internacional relata otra cosa.",
        ),
    ),
    *_gifts(
        "ayudante",
        "ventanilla",
        (
            "lanyard",
            "🪪",
            "Acreditación de voluntario VIP",
            90,
            "Da acceso a la cena de gala, pero solo a la zona de pie.",
        ),
        (
            "boli",
            "🖊️",
            "Bolígrafo de firmar cheques",
            120,
            "El boli oficial de la Asociación. Escribe mejor las cifras con muchos ceros.",
        ),
        (
            "memoria",
            "📘",
            "Memoria anual de transparencia",
            70,
            "Doscientas páginas a color y ninguna cifra. Regalo de la Asociación.",
        ),
        (
            "diploma",
            "🎓",
            "Diploma de ayudante del ayudante",
            180,
            "Certifica que has ayudado a quien ayuda. Va enmarcado.",
        ),
        (
            "llavero",
            "🔑",
            "Llavero del coche de empresa",
            250,
            "Del coche no, solo el llavero. El coche es del vicepresidente segundo.",
        ),
    ),
    *_gifts(
        "mares",
        "lujo",
        (
            "pajita",
            "🥤",
            "Pajita de papel de la fiesta del yate",
            50,
            "Se deshizo en el primer sorbo, como manda la tradición.",
        ),
        (
            "gorra",
            "🧢",
            "Gorra de capitán de Mares Limpios",
            160,
            "La que se pone la junta para salir en las fotos del yate.",
        ),
        (
            "tortuga",
            "🐢",
            "Tortuga de plástico del documental",
            200,
            "La protagonista del documental. Flota que da gusto.",
        ),
        (
            "boya",
            "🛟",
            "Salvavidas con el logo de la S. L.",
            140,
            "No se ha usado nunca: nadie se ha bajado del yate.",
        ),
        (
            "tapon",
            "🍾",
            "Tapón pegado de champán",
            90,
            "Del champán de cubierta. Sigue pegado a la botella, que no te dan.",
        ),
    ),
    *_gifts(
        "hambre",
        "espana",
        (
            "tenedor",
            "🍴",
            "Tenedor de plata número siete",
            180,
            "El séptimo tenedor del banquete solidario. Nadie sabía para qué era.",
        ),
        (
            "servilleta",
            "🧻",
            "Servilleta bordada de la gala",
            70,
            "Con el logo de la Fundación Cubiertos de Plata en hilo de oro.",
        ),
        (
            "menu",
            "📋",
            "Menú degustación de 14 pases",
            110,
            "La carta del banquete contra el hambre, firmada por el sumiller.",
        ),
        (
            "babero",
            "🦞",
            "Babero para la langosta",
            90,
            "Imprescindible para luchar contra el hambre sin mancharse.",
        ),
        (
            "jamon",
            "🍖",
            "Loncha de jamón de bellota envasada",
            150,
            "Cortada por un maestro cortador en la gala benéfica. Al vacío.",
        ),
    ),
    *_gifts(
        "gamers",
        "bazar",
        (
            "mando",
            "🎮",
            "Mando con el drift de fábrica",
            120,
            "Regalo de Gamers Sin Fronteras. El muñeco se mueve solo hacia la izquierda.",
        ),
        (
            "codigo",
            "🔢",
            "Código de una skin caducada",
            60,
            "Caducó ayer. Lo bonito es la intención.",
        ),
        ("rgb", "🌈", "Tira de LED RGB de la sede", 140, "Da +10 de FPS según la junta directiva."),
        (
            "alfombrilla",
            "🖱️",
            "Alfombrilla XXL del torneo benéfico",
            100,
            "La del torneo de FIFA que ganó el presidente.",
        ),
        (
            "silla",
            "💺",
            "Ruedecita de la silla gaming",
            80,
            "Una de las cinco ruedas de la silla del presidente. Las otras cuatro, ni lo sueñes.",
        ),
    ),
    *_gifts(
        "influencers",
        "shitpost",
        (
            "aro",
            "💡",
            "Aro de luz de segunda mano",
            130,
            "Del último influencer rescatado. Alumbra la cara y oculta las ojeras.",
        ),
        (
            "codigo",
            "🏷️",
            "Código de descuento del 5 %",
            50,
            "Solo vale en la tienda de un influencer que ya cerró.",
        ),
        ("selfie", "🤳", "Palo selfie de Dubái", 110, "Viajó a Dubái tres veces. Tú no."),
        (
            "guion",
            "📝",
            "Guion del vídeo de disculpas",
            90,
            "Con las pausas para llorar marcadas en rojo.",
        ),
        (
            "seguidores",
            "👥",
            "Pack de 100 seguidores bot",
            160,
            "Te siguen, no te comentan y no te abandonan nunca.",
        ),
    ),
    *_gifts(
        "platano",
        "canarias",
        (
            "pegatina",
            "🍌",
            "Pegatina del plátano con motitas",
            50,
            "Regalo de la Plataforma. Las motitas son buenas, lo dice un estudio.",
        ),
        (
            "pina",
            "🍌",
            "Piña de plátanos de la cata",
            140,
            "Se la quitaron a la cata de bananas. Al menos estos son de aquí.",
        ),
        (
            "camiseta",
            "👕",
            "Camiseta «Banana no, plátano sí»",
            120,
            "Talla única, que en Canarias es la XL.",
        ),
        (
            "estatua",
            "🗿",
            "Plátano de la rotonda en miniatura",
            260,
            "La rotonda de Tenerife en tamaño mesita de noche.",
        ),
        (
            "bono",
            "🚌",
            "Bono de guagua al Teide de la junta",
            90,
            "Le quedan dos viajes. La junta solo hizo el de ida.",
        ),
    ),
    *_gifts(
        "jovani",
        "boricua",
        (
            "ram",
            "🧠",
            "Módulo de memoria del NAS",
            200,
            "Se lo quitaron al NAS para dártelo. Chromium ya lo echa de menos.",
        ),
        (
            "ventilador",
            "🌀",
            "Ventilador jubilado del NAS",
            110,
            "Sonaba como un avión. Ahora suena como un avión en tu mochila.",
        ),
        (
            "pegatina",
            "🤖",
            "Pegatina «Yo mantengo al bot»",
            50,
            "Regalo de la Fundación Amigos del Bot Jovani por pagar la luz del servidor.",
        ),
        (
            "cable",
            "🔌",
            "Cable de red del armario",
            70,
            "Si el bot se cae un día, ya sabes por qué.",
        ),
        (
            "entrada",
            "🎟️",
            "Entrada sin usar de un concierto",
            300,
            "La del bot, que al final no fue: no tiene piernas.",
        ),
    ),
)
GIFTS_BY_ONG: dict[str, tuple[OngGift, ...]] = {
    ong.key: tuple(g for g in ONG_GIFTS if g.ong == ong.key) for ong in ONGS
}
