"""Logros: el catálogo, las rarezas y las reglas para desbloquearlos.

Lógica pura, sin Discord ni base de datos. Un logro se desbloquea cuando las
estadísticas de un miembro llegan a una meta (`Achievement.conditions`). Las
estadísticas son contadores con nombre (`messages`, `voice_minutes`,
`roulette_spins`…) que se guardan por servidor y miembro. Hay dos tipos:

- **Sumas** (`add`): se acumulan. Mensajes, minutos en voz, tiradas…
- **Máximos** (`peak`): se quedan con el mayor valor visto. Nivel, mayor
  premio de una jugada, racha más larga…

Quien juega o habla no toca esto directamente: los cogs calculan qué ha
pasado con las funciones de este módulo (`message_stats`, `roulette_stats`,
`blackjack_stats`, `casino_stats`) y se lo pasan al cog de logros.

Para añadir un logro basta con una línea en el catálogo (`_build_catalog`).
Si usa una estadística nueva, el juego que la produce tiene que sumarla.
Los `id` no se cambian nunca: son lo que se guarda en la base de datos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from bot.services.blackjack import BlackjackGame, Result, hand_total, is_blackjack
from bot.services.roulette import DOUBLE_ZERO, ZEROS, RoundOutcome

# -- Rarezas ---------------------------------------------------------------------------


class Rarity(Enum):
    """Rareza de un logro: decide su premio, sus puntos y cómo se pinta.

    Los emojis son de formas distintas, no solo de colores, para que se
    distingan también con daltonismo.
    """

    COMMON = ("Común", "▫️", 50, 10, (170, 170, 170))
    RARE = ("Raro", "🔹", 200, 25, (70, 140, 255))
    EPIC = ("Épico", "💠", 750, 50, (160, 90, 255))
    LEGENDARY = ("Legendario", "🌟", 2_500, 100, (255, 190, 40))
    MYTHIC = ("Mítico", "👑", 10_000, 250, (255, 255, 255))

    def __init__(
        self, label: str, emoji: str, reward: int, points: int, rgb: tuple[int, int, int]
    ) -> None:
        self.label = label
        self.emoji = emoji
        #: Yapdollars brutos al desbloquearlo (pagan IRPF, ver el cog).
        self.reward = reward
        #: Puntos para el ranking de logros.
        self.points = points
        self.rgb = rgb


# -- Categorías ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Category:
    """Grupo de logros que se muestra junto en `logros`.

    Attributes:
        upcoming: El juego aún no existe. Sus logros se enseñan como
            "próximamente", no cuentan para el total ni se pueden desbloquear.
    """

    key: str
    title: str
    upcoming: bool = False


CATEGORIES: tuple[Category, ...] = (
    Category("chat", "💬 Chat"),
    Category("time", "🗓️ Horarios y fechas"),
    Category("voice", "🎙️ Voz"),
    Category("social", "❤️ Social"),
    Category("levels", "📈 Niveles"),
    Category("roulette", "🎡 Ruleta"),
    Category("blackjack", "🃏 Blackjack"),
    Category("casino", "💰 Casino"),
    Category("slots", "🎰 Tragaperras", upcoming=True),
    Category("economy", "🏛️ Economía y Hacienda"),
    Category("meta", "🏆 Coleccionista"),
)
CATEGORY_BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}


# -- Logros ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Achievement:
    """Un logro del catálogo.

    Attributes:
        id: Identificador estable; es lo que se guarda al desbloquearlo.
        conditions: Pares `(estadística, meta)`; hacen falta todos.
        unit: Cómo se muestra el progreso (`"min"` pasa minutos a horas,
            `"money"` formatea yapdollars).
        secret: Se muestra como `???` hasta que alguien lo desbloquea.
    """

    id: str
    name: str
    description: str
    category: str
    rarity: Rarity
    conditions: tuple[tuple[str, int], ...]
    unit: str = ""
    secret: bool = False

    @property
    def stat(self) -> str:
        """Estadística principal (la que mide el progreso)."""
        return self.conditions[0][0]

    @property
    def goal(self) -> int:
        """Meta de la estadística principal."""
        return self.conditions[0][1]

    @property
    def upcoming(self) -> bool:
        """Si pertenece a un juego que aún no existe."""
        return CATEGORY_BY_KEY[self.category].upcoming


#: Estadística virtual con el número de logros normales desbloqueados (los de
#: Coleccionista no cuentan, para que no se desbloqueen unos a otros).
UNLOCKED_STAT = "achievements"
#: Estadística virtual: mensajes contados en vivo más los del historial importado.
MESSAGES_TOTAL_STAT = "messages_total"

_R = Rarity
C, R, E, L, M = _R.COMMON, _R.RARE, _R.EPIC, _R.LEGENDARY, _R.MYTHIC


def _tiers(category: str, stat: str, rows: Iterable[tuple], *, unit: str = "") -> list[Achievement]:
    """Crea los logros escalonados de una estadística.

    Cada fila es `(meta, id, nombre, descripción, rareza)` y, opcionalmente,
    `True` al final si es secreto. Van de menor a mayor meta.
    """
    built = []
    for row in rows:
        goal, achievement_id, name, description, rarity, *rest = row
        built.append(
            Achievement(
                id=achievement_id,
                name=name,
                description=description,
                category=category,
                rarity=rarity,
                conditions=((stat, goal),),
                unit=unit,
                secret=bool(rest and rest[0]),
            )
        )
    return built


def _build_catalog() -> tuple[Achievement, ...]:
    a: list[Achievement] = []

    # 💬 Chat -------------------------------------------------------------------------
    a += _tiers("chat", MESSAGES_TOTAL_STAT, [
        (1, "chat_1", "Rompiendo el hielo", "Escribe tu primer mensaje.", C),
        (100, "chat_100", "Ya se te oye", "Escribe 100 mensajes.", C),
        (500, "chat_500", "Tertuliano", "Escribe 500 mensajes.", C),
        (1_000, "chat_1k", "Cotorra", "Escribe 1.000 mensajes.", R),
        (5_000, "chat_5k", "Sin filtro", "Escribe 5.000 mensajes.", R),
        (10_000, "chat_10k", "Yapper certificado", "Escribe 10.000 mensajes.", E),
        (25_000, "chat_25k", "Teclado en llamas", "Escribe 25.000 mensajes.", E),
        (50_000, "chat_50k", "¿Tú no trabajas?", "Escribe 50.000 mensajes.", L),
        (100_000, "chat_100k", "Ruido de fondo", "Escribe 100.000 mensajes.", L),
        (250_000, "chat_250k", "Yapper supremo", "Escribe 250.000 mensajes.", M),
    ])  # fmt: skip
    a += _tiers("chat", "msg_replies", [
        (50, "reply_50", "Conversador", "Responde a 50 mensajes.", C),
        (500, "reply_500", "Hilo infinito", "Responde a 500 mensajes.", R),
        (5_000, "reply_5k", "Tertulia de bar", "Responde a 5.000 mensajes.", E),
    ])  # fmt: skip
    a += _tiers("chat", "msg_questions", [
        (50, "ask_50", "Curioso", "Haz 50 preguntas (mensajes que acaban en ?).", C),
        (500, "ask_500", "Pregúntale a Google", "Haz 500 preguntas.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_mentions", [
        (50, "ping_50", "Oye, tú", "Menciona a alguien en 50 mensajes.", C),
        (500, "ping_500", "Pesado", "Menciona a alguien en 500 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_attachments", [
        (10, "pic_10", "Paparazzi", "Sube 10 archivos o imágenes.", C),
        (100, "pic_100", "Galería de arte", "Sube 100 archivos o imágenes.", R),
        (1_000, "pic_1k", "Archivo nacional", "Sube 1.000 archivos o imágenes.", E),
    ])  # fmt: skip
    a += _tiers("chat", "msg_links", [
        (25, "link_25", "Cartero", "Comparte 25 enlaces.", C),
        (250, "link_250", "Agregador de noticias", "Comparte 250 enlaces.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_stickers", [
        (10, "sticker_10", "Pegatinero", "Manda 10 stickers.", C),
        (100, "sticker_100", "Álbum completo", "Manda 100 stickers.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_long", [
        (1, "long_1", "Me explayo", "Escribe un mensaje de 600 caracteres o más.", C),
        (25, "long_25", "Escribes biblias", "Escribe 25 mensajes de 600 caracteres o más.", R),
        (100, "long_100", "Premio Planeta", "Escribe 100 mensajes de 600 caracteres o más.", E),
    ])  # fmt: skip
    a += _tiers("chat", "msg_short", [
        (100, "short_100", "Monosílabo", "Manda 100 mensajes de 3 caracteres o menos.", C),
        (1_000, "short_1k", "k.", "Manda 1.000 mensajes de 3 caracteres o menos.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_caps", [
        (10, "caps_10", "NO GRITES", "Escribe 10 mensajes TODO EN MAYÚSCULAS.", C),
        (100, "caps_100", "BLOQ MAYÚS ROTO", "Escribe 100 mensajes TODO EN MAYÚSCULAS.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_xd", [
        (100, "xd_100", "xd", "Escribe «xd» en 100 mensajes.", C),
        (1_000, "xd_1k", "XDDDDDD", "Escribe «xd» en 1.000 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_laughs", [
        (100, "laugh_100", "Jajajaja", "Ríete (jaja, jsjs, lol…) en 100 mensajes.", C),
        (1_000, "laugh_1k", "Risa enlatada", "Ríete en 1.000 mensajes.", R),
    ])  # fmt: skip

    # 🗓️ Horarios y fechas ----------------------------------------------------------------
    a += _tiers("time", "msg_night", [
        (10, "night_10", "Búho", "Escribe 10 mensajes entre las 2:00 y las 6:00.", C),
        (100, "night_100", "Insomne", "Escribe 100 mensajes entre las 2:00 y las 6:00.", R),
        (1_000, "night_1k", "Vampiro", "Escribe 1.000 mensajes entre las 2:00 y las 6:00.", E),
    ])  # fmt: skip
    a += _tiers("time", "msg_morning", [
        (10, "morning_10", "Madrugador", "Escribe 10 mensajes entre las 6:00 y las 8:00.", C),
        (100, "morning_100", "Al que madruga…", "Escribe 100 mensajes antes de las 8:00.", R),
        (1_000, "morning_1k", "Gallo del corral", "Escribe 1.000 mensajes antes de las 8:00.", E),
    ])  # fmt: skip
    a += _tiers("time", "msg_happy_hour", [
        (50, "happy_50", "Hora feliz", "Escribe 50 mensajes durante la hora feliz.", C),
        (500, "happy_500", "Cazador de horas felices", "Escribe 500 mensajes en la hora feliz.", R),
    ])  # fmt: skip
    a += _tiers(
        "time", "msg_leet", [(1, "leet", "1337", "Escribe un mensaje a las 13:37.", R, True)]
    )
    a += _tiers("time", "msg_new_year", [
        (1, "new_year", "Campanadas", "Escribe en la primera hora del año.", E, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_halloween", [
        (1, "halloween", "Truco o trato", "Escribe el 31 de octubre.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_christmas", [
        (1, "christmas", "Espíritu navideño", "Escribe en Nochebuena o en Navidad.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_canarias", [
        (1, "canarias", "¡Viva Canarias!", "Escribe el 30 de mayo, Día de Canarias.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_own_birthday", [
        (1, "own_bday", "Felicidades a mí", "Escribe el día de tu cumpleaños.", C, True),
    ])  # fmt: skip

    # 🎙️ Voz ------------------------------------------------------------------------------
    a += _tiers("voice", "voice_minutes", [
        (60, "voice_1h", "¿Se me oye?", "Pasa 1 hora en llamada con más gente.", C),
        (600, "voice_10h", "Tertulia", "Pasa 10 horas en llamada.", C),
        (3_000, "voice_50h", "Locutor", "Pasa 50 horas en llamada.", R),
        (6_000, "voice_100h", "Podcaster", "Pasa 100 horas en llamada.", R),
        (15_000, "voice_250h", "Vives en la llamada", "Pasa 250 horas en llamada.", E),
        (30_000, "voice_500h", "Okupa del canal", "Pasa 500 horas en llamada.", L),
        (60_000, "voice_1000h", "Mil horas", "Pasa 1.000 horas en llamada.", M),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_session_max", [
        (180, "session_3h", "Maratón", "Aguanta 3 horas seguidas en llamada.", R),
        (480, "session_8h", "Jornada completa", "Aguanta 8 horas seguidas en llamada.", E),
        (720, "session_12h", "Ultramaratón", "Aguanta 12 horas seguidas en llamada.", L),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_night", [
        (60, "vnight_1h", "Trasnochador", "Pasa 1 hora en llamada entre las 2:00 y las 6:00.", C),
        (600, "vnight_10h", "Turno de noche", "Pasa 10 horas en llamada de madrugada.", R),
        (3_000, "vnight_50h", "Guardia nocturna", "Pasa 50 horas en llamada de madrugada.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_stream", [
        (60, "stream_1h", "En directo", "Comparte pantalla durante 1 hora.", C),
        (600, "stream_10h", "Streamer", "Comparte pantalla durante 10 horas.", R),
        (3_000, "stream_50h", "Streamer de barrio", "Comparte pantalla durante 50 horas.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_video", [
        (30, "cam_30m", "Dando la cara", "Pon la cámara durante 30 minutos.", C),
        (600, "cam_10h", "Influencer", "Pon la cámara durante 10 horas.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_muted", [
        (120, "mute_2h", "Oyente", "Pasa 2 horas en llamada con el micro silenciado.", C),
        (1_200, "mute_20h", "El mimo", "Pasa 20 horas en llamada sin abrir el micro.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_crowd_max", [
        (5, "crowd_5", "Fiesta", "Coincide en una llamada con 5 personas o más.", C),
        (10, "crowd_10", "Multitud", "Coincide en una llamada con 10 personas o más.", R),
    ])  # fmt: skip
    a += _tiers("voice", "voice_alone", [
        (60, "alone_1h", "Hablando solo", "Pasa 1 hora solo en un canal de voz.", C, True),
        (600, "alone_10h", "Forever alone", "Pasa 10 horas solo en un canal de voz.", R, True),
    ], unit="min")  # fmt: skip

    # ❤️ Social ---------------------------------------------------------------------------
    a += _tiers("social", "reactions_given", [
        (50, "react_50", "Me gusta", "Reacciona a 50 mensajes de otros.", C),
        (500, "react_500", "Dedo rápido", "Reacciona a 500 mensajes de otros.", R),
        (5_000, "react_5k", "Reaccionador compulsivo", "Reacciona a 5.000 mensajes.", E),
    ])  # fmt: skip
    a += _tiers("social", "reactions_received", [
        (50, "liked_50", "Gustas", "Recibe 50 reacciones de otras personas.", C),
        (500, "liked_500", "Carismático", "Recibe 500 reacciones.", R),
        (5_000, "liked_5k", "Ídolo de masas", "Recibe 5.000 reacciones.", E),
    ])  # fmt: skip
    a += _tiers("social", "reactions_on_message_max", [
        (5, "viral_5", "Viral", "Que 5 personas reaccionen al mismo mensaje tuyo.", R),
        (10, "viral_10", "Trending topic", "Que 10 personas reaccionen al mismo mensaje.", E),
    ])  # fmt: skip
    a += _tiers("social", "greetings_sent", [
        (1, "greet_1", "Buen amigo", "Felicita a alguien por su cumpleaños.", C),
        (10, "greet_10", "Alma de la fiesta", "Felicita 10 cumpleaños.", R),
        (50, "greet_50", "Tarta para todos", "Felicita 50 cumpleaños.", E),
    ])  # fmt: skip
    a += _tiers("social", "greetings_received", [
        (5, "greeted_5", "Querido", "Recibe 5 felicitaciones de cumpleaños.", C),
        (20, "greeted_20", "Popular", "Recibe 20 felicitaciones de cumpleaños.", R),
    ])  # fmt: skip
    a += _tiers("social", "msg_bot_call", [
        (1, "bot_call", "¿Me llamabas?", "Menciona al bot o di su nombre.", C, True),
    ])  # fmt: skip
    a += _tiers("social", "msg_sanxe", [
        (1, "sanxe", "Invocación", "Nombra a Perro Sanxe en el chat.", C, True),
    ])  # fmt: skip

    # 📈 Niveles --------------------------------------------------------------------------
    a += _tiers("levels", "level_max", [
        (5, "level_5", "Novato", "Llega al nivel 5.", C),
        (10, "level_10", "De la casa", "Llega al nivel 10.", C),
        (20, "level_20", "Veterano", "Llega al nivel 20.", R),
        (30, "level_30", "Pilar del servidor", "Llega al nivel 30.", R),
        (50, "level_50", "Leyenda local", "Llega al nivel 50.", E),
        (75, "level_75", "Semidiós", "Llega al nivel 75.", L),
        (100, "level_100", "Nivel 100", "Llega al nivel 100.", M),
    ])  # fmt: skip
    a += _tiers("levels", "activity_streak_max", [
        (7, "streak_7", "Una semana sin faltar", "Habla 7 días seguidos.", C),
        (30, "streak_30", "Un mes sin faltar", "Habla 30 días seguidos.", R),
        (100, "streak_100", "Cien días", "Habla 100 días seguidos.", E),
        (365, "streak_365", "Un año entero", "Habla 365 días seguidos.", L),
    ])  # fmt: skip

    # 🎡 Ruleta ---------------------------------------------------------------------------
    a += _tiers("roulette", "roulette_spins", [
        (1, "rl_1", "Hagan juego", "Juega tu primera tirada de ruleta.", C),
        (50, "rl_50", "Cliente habitual", "Juega 50 tiradas de ruleta.", C),
        (250, "rl_250", "Crupier honorario", "Juega 250 tiradas de ruleta.", R),
        (1_000, "rl_1k", "La bola es mi amiga", "Juega 1.000 tiradas de ruleta.", E),
        (5_000, "rl_5k", "Vives en la mesa", "Juega 5.000 tiradas de ruleta.", L),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_wins", [
        (10, "rlw_10", "Primeras victorias", "Gana 10 tiradas de ruleta.", C),
        (100, "rlw_100", "Buen ojo", "Gana 100 tiradas de ruleta.", R),
        (1_000, "rlw_1k", "Rey de la ruleta", "Gana 1.000 tiradas de ruleta.", E),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_straight_wins", [
        (1, "pleno_1", "¡Pleno!", "Acierta un número suelto.", R),
        (10, "pleno_10", "Francotirador", "Acierta 10 plenos.", E),
        (50, "pleno_50", "Vidente", "Acierta 50 plenos.", L),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_green_wins", [
        (1, "green", "Verde esperanza", "Gana apostando al 0 o al 00.", E),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_double_zero_wins", [
        (1, "double_zero", "Doble cero", "Acierta un pleno al 00.", E, True),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_color_wins", [
        (50, "color_50", "Rojo o negro", "Gana 50 apuestas a color.", C),
        (500, "color_500", "Ajedrecista", "Gana 500 apuestas a color.", R),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_wagers_max", [
        (5, "wagers_5", "Pintor de mesa", "Juega 5 apuestas en una sola tirada.", C),
        (10, "wagers_10", "Alfombra de fichas", "Juega 10 apuestas en una sola tirada.", R),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_streak_max", [
        (3, "rlstreak_3", "Racha", "Gana 3 tiradas seguidas en la misma mesa.", C),
        (5, "rlstreak_5", "En llamas", "Gana 5 tiradas seguidas en la misma mesa.", R),
        (8, "rlstreak_8", "Imparable", "Gana 8 tiradas seguidas en la misma mesa.", E),
        (12, "rlstreak_12", "¿Trampas?", "Gana 12 tiradas seguidas en la misma mesa.", L),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_repeat_pocket", [
        (1, "deja_vu", "Déjà vu", "Que salga el mismo número dos veces seguidas.", R, True),
    ])  # fmt: skip

    # 🃏 Blackjack ------------------------------------------------------------------------
    a += _tiers("blackjack", "bj_hands", [
        (1, "bj_1", "Primera mano", "Juega tu primera mano de blackjack.", C),
        (50, "bj_50", "Jugador de cartas", "Juega 50 manos de blackjack.", C),
        (250, "bj_250", "Tahúr", "Juega 250 manos de blackjack.", R),
        (1_000, "bj_1k", "Mesa reservada", "Juega 1.000 manos de blackjack.", E),
        (5_000, "bj_5k", "Dueño del casino", "Juega 5.000 manos de blackjack.", L),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_wins", [
        (10, "bjw_10", "Le pillas el truco", "Gana 10 manos de blackjack.", C),
        (100, "bjw_100", "Ganador nato", "Gana 100 manos de blackjack.", R),
        (1_000, "bjw_1k", "La banca te teme", "Gana 1.000 manos de blackjack.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_naturals", [
        (1, "natural_1", "¡Blackjack!", "Saca un blackjack (as y figura de salida).", C),
        (10, "natural_10", "As en la manga", "Saca 10 blackjacks.", R),
        (50, "natural_50", "Contador de cartas", "Saca 50 blackjacks.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_double_wins", [
        (1, "dbl_1", "Doble o nada", "Gana una mano después de doblar.", C),
        (25, "dbl_25", "Sangre fría", "Gana 25 manos doblando.", R),
    ])  # fmt: skip
    a += _tiers(
        "blackjack", "bj_splits", [(1, "split_1", "Divide y vencerás", "Separa una pareja.", C)]
    )
    a += _tiers("blackjack", "bj_split_sweeps", [
        (1, "split_sweep", "Doble victoria", "Gana las dos manos después de separar.", R),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_busts", [
        (10, "bust_10", "Me pasé", "Pásate de 21 diez veces.", C),
        (100, "bust_100", "Avaricioso", "Pásate de 21 cien veces.", R),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_21_multi", [
        (1, "21_1", "Veintiuno", "Suma 21 con tres cartas o más.", C),
        (25, "21_25", "Matemático", "Suma 21 con tres cartas o más 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_cards_max", [
        (5, "cards_5", "Cinco cartas", "Acaba una mano con 5 cartas sin pasarte.", R),
        (6, "cards_6", "Castillo de naipes", "Acaba una mano con 6 cartas sin pasarte.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_pushes", [(10, "push_10", "Tablas", "Empata 10 manos.", C)])
    a += _tiers("blackjack", "bj_dealer_busts", [
        (25, "dbust_25", "La banca revienta", "Gana 25 manos porque la banca se pasa.", C),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_dealer_naturals", [
        (5, "dnat_5", "La banca siempre gana", "Que la banca saque blackjack 5 veces.", C),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_bad_beat", [
        (1, "bad_beat", "Por los pelos", "Pierde con 20 contra 21 de la banca.", C, True),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_kamikaze", [
        (1, "kamikaze", "Kamikaze con suerte", "Pide carta con 17 o más y no te pases.", E, True),
    ])  # fmt: skip

    # 💰 Casino ---------------------------------------------------------------------------
    a += _tiers("casino", "casino_wagered", [
        (10_000, "wager_10k", "Apostador", "Apuesta 10.000 Y$ en total.", C),
        (100_000, "wager_100k", "Gran apostador", "Apuesta 100.000 Y$ en total.", R),
        (1_000_000, "wager_1m", "Ballena", "Apuesta 1.000.000 Y$ en total.", E),
        (10_000_000, "wager_10m", "Jeque del casino", "Apuesta 10.000.000 Y$ en total.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("casino", "casino_win_max", [
        (1_000, "bigwin_1k", "Buen golpe", "Gana 1.000 Y$ netos en una jugada.", C),
        (10_000, "bigwin_10k", "Pelotazo", "Gana 10.000 Y$ netos en una jugada.", R),
        (100_000, "bigwin_100k", "Bote", "Gana 100.000 Y$ netos en una jugada.", E),
        (1_000_000, "bigwin_1m", "Rompebancas", "Gana 1.000.000 Y$ netos en una jugada.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("casino", "casino_loss_max", [
        (1_000, "bigloss_1k", "Duele", "Pierde 1.000 Y$ en una jugada.", C),
        (10_000, "bigloss_10k", "Eso ha dolido", "Pierde 10.000 Y$ en una jugada.", R),
        (100_000, "bigloss_100k", "Ruina", "Pierde 100.000 Y$ en una jugada.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("casino", "casino_all_in", [
        (1, "allin_1", "All-in", "Apuesta todo tu saldo.", C),
        (10, "allin_10", "Sin miedo", "Ve all-in 10 veces.", R),
        (50, "allin_50", "YOLO", "Ve all-in 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("casino", "casino_all_in_wins", [
        (1, "allinw_1", "Todo o nada", "Gana un all-in.", R),
        (10, "allinw_10", "Nervios de acero", "Gana 10 all-in.", E),
    ])  # fmt: skip
    a += _tiers("casino", "casino_broke", [
        (1, "broke_1", "Arruinado", "Quédate a cero en el casino.", C),
        (10, "broke_10", "Cliente del IMV", "Quédate a cero 10 veces.", R),
    ])  # fmt: skip
    a += _tiers("casino", "casino_win_streak_max", [
        (5, "wstreak_5", "Viento a favor", "Gana 5 jugadas seguidas en el casino.", R),
        (10, "wstreak_10", "Tocado por los dioses", "Gana 10 jugadas seguidas.", L),
    ])  # fmt: skip
    a += _tiers("casino", "casino_loss_streak_max", [
        (5, "lstreak_5", "Mala racha", "Pierde 5 jugadas seguidas.", C),
        (10, "lstreak_10", "Gafe", "Pierde 10 jugadas seguidas.", R),
        (20, "lstreak_20", "Maldito", "Pierde 20 jugadas seguidas.", E),
    ])  # fmt: skip
    a += _tiers("casino", "casino_bet_666", [
        (1, "bet_666", "Apuesta diabólica", "Juega exactamente 666 Y$ de una vez.", R, True),
    ])  # fmt: skip
    a += _tiers("casino", "casino_bet_42", [
        (1, "bet_42", "La respuesta", "Juega exactamente 42 Y$ de una vez.", R, True),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="versatile",
            name="Polivalente",
            description="Juega a la ruleta y al blackjack.",
            category="casino",
            rarity=C,
            conditions=(("roulette_spins", 1), ("bj_hands", 1)),
        )
    )

    # 🎰 Tragaperras (próximamente) -------------------------------------------------------
    a += _tiers("slots", "slots_spins", [
        (1, "slots_1", "Tirar de la palanca", "Juega tu primera tirada en la tragaperras.", C),
        (100, "slots_100", "Enganchado", "Juega 100 tiradas en la tragaperras.", C),
        (1_000, "slots_1k", "Zombi de la máquina", "Juega 1.000 tiradas.", E),
        (10_000, "slots_10k", "La máquina te conoce", "Juega 10.000 tiradas.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_wins", [
        (10, "slotsw_10", "Tilín tilín", "Gana 10 tiradas en la tragaperras.", C),
        (100, "slotsw_100", "Luces y campanas", "Gana 100 tiradas en la tragaperras.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_jackpots", [
        (1, "jackpot_1", "¡JACKPOT!", "Saca el premio gordo de la tragaperras.", E),
        (5, "jackpot_5", "Rey del jackpot", "Saca el premio gordo 5 veces.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_win_max", [
        (10_000, "slots_rain", "Lluvia de monedas", "Gana 10.000 Y$ en una tirada.", R),
    ], unit="money")  # fmt: skip

    # 🏛️ Economía y Hacienda -------------------------------------------------------------
    a += _tiers("economy", "balance_max", [
        (10_000, "rich_10k", "Clase media", "Ten 10.000 Y$ a la vez.", C),
        (100_000, "rich_100k", "Acomodado", "Ten 100.000 Y$ a la vez.", R),
        (1_000_000, "rich_1m", "Millonario", "Ten 1.000.000 Y$ a la vez.", E),
        (10_000_000, "rich_10m", "Multimillonario", "Ten 10.000.000 Y$ a la vez.", L),
        (100_000_000, "rich_100m", "Paraíso fiscal", "Ten 100.000.000 Y$ a la vez.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "imv_claims", [
        (1, "imv_1", "Paguita", "Cobra el IMV por primera vez.", C),
        (30, "imv_30", "Subsidiado", "Cobra el IMV 30 veces.", R),
        (100, "imv_100", "Abonado al IMV", "Cobra el IMV 100 veces.", E),
        (365, "imv_365", "Un año de paguita", "Cobra el IMV 365 veces.", L),
    ])  # fmt: skip
    a += _tiers("economy", "imv_streak_max", [
        (7, "imvs_7", "Constancia", "Cobra el IMV 7 días seguidos.", C),
        (30, "imvs_30", "Disciplina", "Cobra el IMV 30 días seguidos.", R),
        (100, "imvs_100", "Inquebrantable", "Cobra el IMV 100 días seguidos.", E),
    ])  # fmt: skip
    a += _tiers("economy", "tax_paid", [
        (1_000, "tax_1k", "Contribuyente", "Paga 1.000 Y$ de IRPF.", C),
        (10_000, "tax_10k", "Patriota fiscal", "Paga 10.000 Y$ de IRPF.", R),
        (100_000, "tax_100k", "Favorito de Perro Sanxe", "Paga 100.000 Y$ de IRPF.", E),
        (1_000_000, "tax_1m", "Mecenas del Estado", "Paga 1.000.000 Y$ de IRPF.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "tax_refunds", [
        (1, "refund_day", "Desgravación", "Recupera IRPF del casino perdiendo el mismo día.", C),
    ])  # fmt: skip
    a += _tiers("economy", "renta_filed", [
        (1, "renta_1", "Declarante", "Presenta la renta.", C),
        (10, "renta_10", "Asesor fiscal", "Presenta la renta 10 veces.", R),
    ])  # fmt: skip
    a += _tiers("economy", "renta_refunded", [
        (10_000, "renta_10k", "Devolución gorda", "Recupera 10.000 Y$ con la renta.", R),
        (100_000, "renta_100k", "Hacienda somos todos", "Recupera 100.000 Y$ con la renta.", E),
    ], unit="money")  # fmt: skip

    # 🏆 Coleccionista --------------------------------------------------------------------
    a += _tiers("meta", UNLOCKED_STAT, [
        (10, "meta_10", "Cazador de logros", "Desbloquea 10 logros.", C),
        (25, "meta_25", "Coleccionista", "Desbloquea 25 logros.", R),
        (50, "meta_50", "Vitrina llena", "Desbloquea 50 logros.", E),
        (100, "meta_100", "Museo", "Desbloquea 100 logros.", L),
    ])  # fmt: skip
    countable = sum(
        1 for x in a if x.category != "meta" and not CATEGORY_BY_KEY[x.category].upcoming
    )
    a += _tiers("meta", UNLOCKED_STAT, [
        (countable, "completionist", "Completista", "Desbloquea todos los logros.", M),
    ])  # fmt: skip
    return tuple(a)


CATALOG: tuple[Achievement, ...] = _build_catalog()
BY_ID: dict[str, Achievement] = {a.id: a for a in CATALOG}
#: Logros que se pueden conseguir hoy (sin los de juegos que aún no existen).
AVAILABLE: tuple[Achievement, ...] = tuple(a for a in CATALOG if not a.upcoming)
_META = tuple(a for a in AVAILABLE if a.category == "meta")
_NORMAL = tuple(a for a in AVAILABLE if a.category != "meta")


# -- Evaluación ------------------------------------------------------------------------


@dataclass(slots=True)
class StatDelta:
    """Cambios en las estadísticas de un miembro.

    Attributes:
        add: Cuánto sumar a cada contador.
        peak: Valor visto para cada máximo; se guarda si supera al anterior.
    """

    add: dict[str, int] = field(default_factory=dict)
    peak: dict[str, int] = field(default_factory=dict)

    def merge(self, other: StatDelta) -> None:
        """Acumula `other` en este cambio."""
        for stat, value in other.add.items():
            self.add[stat] = self.add.get(stat, 0) + value
        for stat, value in other.peak.items():
            self.peak[stat] = max(self.peak.get(stat, value), value)

    def __bool__(self) -> bool:
        return bool(self.add or self.peak)


def with_derived(stats: Mapping[str, int]) -> dict[str, int]:
    """Añade las estadísticas calculadas a partir de otras.

    `messages_total` suma los mensajes contados por los logros y los que ya
    tenía el miembro en el historial importado antes de que existieran.
    """
    full = dict(stats)
    full[MESSAGES_TOTAL_STAT] = full.get("messages", 0) + full.get("messages_imported", 0)
    return full


def _met(achievement: Achievement, stats: Mapping[str, int]) -> bool:
    return all(stats.get(stat, 0) >= goal for stat, goal in achievement.conditions)


def newly_unlocked(stats: Mapping[str, int], unlocked: Iterable[str]) -> list[str]:
    """Logros que se cumplen con `stats` y aún no estaban en `unlocked`.

    Primero los normales y después los de Coleccionista, contando ya los
    que se acaban de conseguir.
    """
    have = set(unlocked)
    full = with_derived(stats)
    new = [a.id for a in _NORMAL if a.id not in have and _met(a, full)]
    full[UNLOCKED_STAT] = sum(1 for a in _NORMAL if a.id in have) + len(new)
    new += [a.id for a in _META if a.id not in have and _met(a, full)]
    return new


def progress(achievement: Achievement, stats: Mapping[str, int]) -> tuple[int, int]:
    """`(actual, meta)` de la estadística principal, con el actual sin pasarse."""
    full = with_derived(stats)
    if achievement.category == "meta":
        full[UNLOCKED_STAT] = stats.get(UNLOCKED_STAT, 0)
    return min(full.get(achievement.stat, 0), achievement.goal), achievement.goal


def points(achievement_ids: Iterable[str]) -> int:
    """Puntos de ranking de una colección de logros (ignora ids desconocidos)."""
    return sum(BY_ID[i].rarity.points for i in achievement_ids if i in BY_ID)


def total_reward(achievement_ids: Iterable[str]) -> int:
    """Yapdollars brutos que se pagan por estos logros."""
    return sum(BY_ID[i].rarity.reward for i in achievement_ids if i in BY_ID)


# -- Qué cuenta cada cosa --------------------------------------------------------------

#: Mensaje "largo" a partir de estos caracteres.
LONG_MESSAGE = 600
#: Mensaje "corto" hasta estos caracteres.
SHORT_MESSAGE = 3
#: Letras mínimas para que un mensaje en mayúsculas cuente como grito.
CAPS_MIN_LETTERS = 8

_LINK = re.compile(r"https?://", re.IGNORECASE)
_XD = re.compile(r"(?<![a-z])x+d+(?![a-z])", re.IGNORECASE)
_WORD = re.compile(r"[a-záéíóúüñ]+")
#: jaja, jajaj, jejeje, jsjsjs, jajsja… (al menos dos jotas).
_LAUGH_WORD = re.compile(r"(?:j+[aeis]+){2,}j*|j+[aeis]+j+")
_LAUGH_WORDS = frozenset({"lol", "lmao", "lmfao"})


def is_laugh(text: str) -> bool:
    """Si el mensaje contiene una risa escrita."""
    return any(
        word in _LAUGH_WORDS or _LAUGH_WORD.fullmatch(word) for word in _WORD.findall(text.lower())
    )


def is_night(hour: int) -> bool:
    """De 2:00 a 5:59, hora canaria."""
    return 2 <= hour < 6


def message_stats(
    content: str,
    *,
    when: datetime,
    attachments: int = 0,
    stickers: int = 0,
    is_reply: bool = False,
    mentions_others: bool = False,
    mentions_bot: bool = False,
    happy_hour: bool = False,
    own_birthday: bool = False,
) -> dict[str, int]:
    """Contadores que suma un mensaje. El contenido se mira y se olvida.

    Args:
        content: Texto del mensaje; no se guarda en ningún sitio.
        when: Hora local (Atlantic/Canary) del mensaje.
    """
    stats = {"messages": 1}
    text = content.strip()
    lowered = text.lower()

    def bump(stat: str, condition: bool) -> None:
        if condition:
            stats[stat] = 1

    letters = [ch for ch in text if ch.isalpha()]
    bump("msg_night", is_night(when.hour))
    bump("msg_morning", 6 <= when.hour < 8)
    bump("msg_long", len(text) >= LONG_MESSAGE)
    bump("msg_short", 0 < len(text) <= SHORT_MESSAGE)
    bump("msg_caps", len(letters) >= CAPS_MIN_LETTERS and all(ch.isupper() for ch in letters))
    bump("msg_questions", text.endswith("?"))
    bump("msg_links", bool(_LINK.search(text)))
    bump("msg_xd", bool(_XD.search(text)))
    bump("msg_laughs", is_laugh(text))
    bump("msg_attachments", attachments > 0)
    bump("msg_stickers", stickers > 0)
    bump("msg_replies", is_reply)
    bump("msg_mentions", mentions_others)
    bump("msg_happy_hour", happy_hour)
    bump("msg_leet", when.hour == 13 and when.minute == 37)
    bump("msg_new_year", when.month == 1 and when.day == 1 and when.hour == 0)
    bump("msg_halloween", when.month == 10 and when.day == 31)
    bump("msg_christmas", when.month == 12 and when.day in (24, 25))
    bump("msg_canarias", when.month == 5 and when.day == 30)
    bump("msg_own_birthday", own_birthday)
    bump("msg_bot_call", mentions_bot or "jovani" in lowered)
    bump("msg_sanxe", "sanxe" in lowered)
    # Los adjuntos y stickers cuentan uno por mensaje, no uno por archivo:
    # subir 10 imágenes de golpe no debería valer 10 veces más.
    return stats


def casino_stats(*, stake: int, net: int, balance_after: int, tax_delta: int = 0) -> StatDelta:
    """Lo que cuenta cualquier jugada de casino, sea del juego que sea.

    Args:
        stake: Total apostado en la jugada (dobles y separaciones incluidos).
        net: Ganancia (positiva) o pérdida (negativa) neta.
        balance_after: Saldo tras cobrar el premio.
        tax_delta: IRPF retenido (positivo) o devuelto (negativo) en la jugada.
    """
    balance_before = balance_after - net
    all_in = stake >= balance_before > 0
    delta = StatDelta(
        add={"casino_wagered": stake},
        peak={"balance_max": balance_after},
    )
    if net > 0:
        delta.peak["casino_win_max"] = net
    elif net < 0:
        delta.peak["casino_loss_max"] = -net
    if all_in:
        delta.add["casino_all_in"] = 1
        if net > 0:
            delta.add["casino_all_in_wins"] = 1
    if balance_after == 0:
        delta.add["casino_broke"] = 1
    if stake == 666:
        delta.add["casino_bet_666"] = 1
    if stake == 42:
        delta.add["casino_bet_42"] = 1
    if tax_delta > 0:
        delta.add["tax_paid"] = tax_delta
    elif tax_delta < 0:
        delta.add["tax_refunds"] = 1
    return delta


def roulette_stats(
    outcome: RoundOutcome, *, table_streak: int, previous_pocket: int | None
) -> StatDelta:
    """Contadores de una tirada de ruleta (sin lo común del casino).

    Args:
        table_streak: Tiradas ganadas seguidas en la mesa, contando esta.
        previous_pocket: Número de la tirada anterior en la misma mesa.
    """
    delta = StatDelta(
        add={"roulette_spins": 1},
        peak={"roulette_wagers_max": len(outcome.wagers), "roulette_streak_max": table_streak},
    )
    won = [w for w, r in zip(outcome.wagers, outcome.returns, strict=True) if r]
    if outcome.won:
        delta.add["roulette_wins"] = 1
    straight = [w for w in won if len(w.bet.numbers) == 1]
    if straight:
        delta.add["roulette_straight_wins"] = len(straight)
    if any(w.bet.numbers <= ZEROS for w in won):
        delta.add["roulette_green_wins"] = 1
    if outcome.pocket == DOUBLE_ZERO and any(w.bet.numbers == {DOUBLE_ZERO} for w in straight):
        delta.add["roulette_double_zero_wins"] = 1
    colors = sum(1 for w in won if w.bet.key in ("red", "black"))
    if colors:
        delta.add["roulette_color_wins"] = colors
    if previous_pocket is not None and previous_pocket == outcome.pocket:
        delta.add["roulette_repeat_pocket"] = 1
    return delta


def blackjack_stats(game: BlackjackGame) -> StatDelta:
    """Contadores de una mano de blackjack ya pagada (sin lo común del casino)."""
    delta = StatDelta(add={"bj_hands": 1})
    add = delta.add

    def bump(stat: str, amount: int = 1) -> None:
        if amount:
            add[stat] = add.get(stat, 0) + amount

    results = [hand.result for hand in game.hands]
    dealer_total = game.dealer_total
    if game.net > 0:
        bump("bj_wins")
    bump("bj_naturals", results.count(Result.BLACKJACK))
    bump("bj_double_wins", sum(1 for h in game.hands if h.doubled and h.result is Result.WIN))
    if len(game.hands) > 1:
        bump("bj_splits")
        if all(r is Result.WIN for r in results):
            bump("bj_split_sweeps")
    bump("bj_busts", results.count(Result.BUST))
    bump("bj_pushes", results.count(Result.PUSH))
    bump("bj_21_multi", sum(1 for h in game.hands if h.total == 21 and len(h.cards) >= 3))
    if dealer_total > 21 and Result.WIN in results:
        bump("bj_dealer_busts")
    if is_blackjack(game.dealer):
        bump("bj_dealer_naturals")
    if any(h.result is Result.LOSE and h.total == 20 and dealer_total == 21 for h in game.hands):
        bump("bj_bad_beat")
    if any(_kamikaze(h.cards, h.doubled, h.busted) for h in game.hands):
        bump("bj_kamikaze")
    standing = [len(h.cards) for h in game.hands if not h.busted]
    if standing:
        delta.peak["bj_cards_max"] = max(standing)
    return delta


def _kamikaze(cards: list, doubled: bool, busted: bool) -> bool:
    """Pidió carta con un 17 duro o más y no se pasó.

    Con el 17 "blando" (as que vale 11) pedir no tiene riesgo, así que no cuenta.
    """
    if doubled or busted or len(cards) < 3:
        return False
    total, soft = hand_total(cards[:-1])
    return total >= 17 and not soft
