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
`blackjack_stats`, `slots_stats`, `hold_win_stats`, `hold_win_bonus_stats`,
`crash_stats`, `mines_stats`, `pachinko_stats`,
`casino_stats`, `shop_stats`, `bizum_stats`) y se
lo pasan al cog de logros.

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
from typing import TYPE_CHECKING

from bot.services.bizum import MAX_DAILY as BIZUM_MAX_DAILY
from bot.services.bizum import MAX_OPERATION as BIZUM_MAX_OPERATION
from bot.services.bizum import MIN_AMOUNT as BIZUM_MIN_AMOUNT
from bot.services.blackjack import BlackjackGame, Result, hand_total, is_blackjack
from bot.services.crash import Seat as CrashSeat
from bot.services.hold_win import BaseSpin as HoldWinSpin
from bot.services.hold_win import BonusResult as HoldWinBonusResult
from bot.services.hold_win import BonusStep as HoldWinStep
from bot.services.hold_win import Trigger as HoldWinTrigger
from bot.services.interest import (
    INTEREST_DAILY_MAX,
    INTEREST_TIERS,
    INTEREST_TOP,
    RESIST_BALANCE,
)
from bot.services.lottery import MAX_PER_DRAW as MAX_LOTTERY_PER_DRAW
from bot.services.mines import MAX_MINES as MINES_MAX
from bot.services.mines import MinesGame
from bot.services.mines import Status as MinesStatus
from bot.services.pachinko import BOARDS as PACHINKO_BOARDS
from bot.services.pachinko import Kind as PachinkoKind
from bot.services.pachinko import Volley as PachinkoVolley
from bot.services.roulette import DOUBLE_ZERO, ZEROS, RoundOutcome
from bot.services.slots import WILD as SLOT_WILD
from bot.services.slots import Kind as SlotKind
from bot.services.slots import Spin
from bot.services.slots import pot_share as slots_pot_share

if TYPE_CHECKING:
    from bot.services.pala import ShiftOutcome

from bot.services.taxes import (
    LOTTERY_EXEMPT,
    STATE_PERSONAL_MINIMUM,
    TAX_COLLECTOR,
    YAPDOLLARS_PER_EURO,
)

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
    Category("todo", "📝 Lista"),
    Category("levels", "📈 Niveles"),
    Category("roulette", "🎡 Ruleta"),
    Category("blackjack", "🃏 Blackjack"),
    Category("casino", "💰 Casino"),
    Category("slots", "🎰 Tragaperras"),
    Category("botes", "🌋 Botes"),
    Category("crash", "🚀 Crash"),
    Category("mines", "💣 Minas"),
    Category("pachinko", "🌸 Pachinko"),
    Category("lottery", "🎟️ Loterías"),
    Category("shop", "🛍️ Tienda"),
    # Bizum y la cuenta remunerada: el menú de `logros` ya va por 25 opciones, el
    # máximo de Discord, así que lo del banco comparte categoría.
    Category("bizum", "🏦 Banco: Bizum y cuenta"),
    Category("economy", "🏛️ Economía y Hacienda"),
    Category("work", "🪏 Trabajo"),
    Category("jobs", "👷 Oficios"),
    Category("sanidad", "🏥 Sanidad"),
    Category("oficina", "💻 Oficina"),
    Category("hongkong", "🇭🇰 Hong Kong"),
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
        story: Texto largo que acompaña al aviso de desbloqueo. Convierte el
            logro en un gancho de "la primera vez que…": como cada logro se
            desbloquea una sola vez por miembro, el texto sale una sola vez.
    """

    id: str
    name: str
    description: str
    category: str
    rarity: Rarity
    conditions: tuple[tuple[str, int], ...]
    unit: str = ""
    secret: bool = False
    story: str | None = None

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


#: Renta anual en Y$ a partir de la cual empieza la retención: el mínimo
#: personal estatal (art. 57 LIRPF) al cambio del juego. El autonómico de
#: Canarias es algo mayor, así que la primera mordida siempre es la estatal.
FIRST_TAX_YEARLY = int(STATE_PERSONAL_MINIMUM * YAPDOLLARS_PER_EURO)
#: Lo mismo en la ventana de 30 días que usa la retención (`compute_withholding`).
FIRST_TAX_MONTHLY = FIRST_TAX_YEARLY * 30 // 365


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


#: Discurso del logro `tax_first`: el aviso de la primera retención. No se
#: dice al entrar al servidor; salta la primera vez que alguien gana lo
#: bastante para pagar, venga de donde venga el dinero (casino, niveles,
#: premios de logros), porque todos esos caminos suman `tax_paid`.
FIRST_TAX_STORY = (
    f"🐶 **¡Ay, bendito! {TAX_COLLECTOR} te encontró.** Hasta hoy cobrabas limpito "
    f"porque no llegabas al mínimo personal: {_thousands(FIRST_TAX_YEARLY)} Y$ al año, "
    f"unos {_thousands(FIRST_TAX_MONTHLY)} Y$ cada 30 días. Te pasaste, mi amor, y "
    "desde hoy cada premio, cada nivel y cada pelotazo del casino pasa antes por su "
    "cartera. Cuanto más ganas, más se lleva.\n"
    "Lo que el casino te retenga de más te lo devuelve en la renta del lunes, si te "
    "acuerdas de presentarla (`renta`). Bienvenido a España: aquí hasta el café paga."
)


#: Discurso del logro `gravamen`: el primer premio de lotería que paga impuestos.
LOTTERY_TAX_STORY = (
    f"🐶 **{TAX_COLLECTOR} también juega a la lotería, pero sin comprar décimo.** Los "
    f"premios hasta {_thousands(LOTTERY_EXEMPT)} Y$ por décimo o apuesta están exentos; "
    "de ahí para arriba se queda el 20 %, y te lo quita antes de pagarte "
    "(disposición adicional 33ª de la Ley del IRPF). No va a la renta ni se "
    "devuelve: es definitivo. Con lo que te queda, mi amor, ya puedes invitar."
)


#: Discurso del logro `bizum_espaldas`: el primer Bizum por encima del máximo.
BIZUM_LIMIT_STORY = (
    f"🤫 **Eso no lo ha visto nadie, ¿verdad, mi amor?** En España un Bizum no pasa de "
    f"1.000 € por operación ({_thousands(BIZUM_MAX_OPERATION)} Y$) ni de 2.000 € al día; "
    "el banco te lo para en seco. Aquí te ha colado porque Perro Sanxe estaba mirando "
    "despegar el Falcon. Si alguien pregunta, era para el cumple de tu prima."
)


#: Discurso del logro `paguita`: rechazar un ascenso.
DECLINE_STORY = (
    "🪑 **Has rechazado un ascenso.** En la vida real hay gente que lo hace a propósito: "
    "si cobrar más te quita una ayuda entera, acabas igual o peor. Es la «trampa de la "
    "pobreza», y por eso existe el incentivo al empleo del IMV (RD 789/2022): lo que "
    "ganas trabajando solo te quita una parte. Aquí, cada Y$ de más te quita medio de "
    "IMV, así que ascender siempre compensa. Pero tú sabrás, mi amor."
)

#: Discurso del logro `ochenta_horas`: pasar del límite legal de horas extra.
OVERTIME_STORY = (
    "⏰ **El art. 35.2 del Estatuto de los Trabajadores dice que las horas extra no "
    "pueden pasar de 80 al año.** Aquí son dos turnos extra por semana. Tú ya vas por "
    "encima, así que tu jefe te las ofrece en B: sin IRPF, sin cotizar y sin que lo vea "
    "el IMV. Si viene la Inspección, devuelves todo con un 20 % de recargo. Wepa."
)

#: Discurso del logro `primera_nomina`.
FIRST_PAYSLIP_STORY = (
    "📄 **Tu primera nómina.** Arriba, el bruto. Luego te quitan la Seguridad Social "
    "(6,5 %: pensiones, paro, formación y el MEI) y la retención de IRPF, que sale de "
    "proyectar lo que cobras al año. Abajo, lo que te llega. Y en letra pequeña, lo que "
    "paga la empresa por ti: otro 32 % que no ves nunca. Todo eso, para Perro Sanxe."
)

#: Discurso del logro `tramo`.
BRACKET_STORY = (
    "📊 **«Me suben de tramo y gano menos».** Mentira, y de las gordas. El IRPF es "
    "progresivo por tramos: solo lo que pasa de cada escalón paga el tipo nuevo, no todo "
    "el sueldo. Ganar un yapdólar más nunca te deja con menos neto. Lo que sí pasa es que "
    "la retención sube, y eso duele igual. Bienvenido a la clase media alta."
)

#: Discurso del logro `socio_hacienda`.
PARTNER_STORY = (
    "🤝 **Esta semana has pagado más impuestos que lo que te ha llegado.** Cuenta la "
    "Seguridad Social tuya y la de la empresa, el IRPF, el IGIC de lo que compras y el "
    "Patrimonio. Es la cuenta que hace la gente cuando dice que trabaja medio año para "
    "Hacienda, y aquí te ha salido más de medio. Perro Sanxe te considera de la familia."
)


#: Discurso del logro `guardia_1`: la primera guardia.
GUARD_STORY = (
    "🚑 **Tu primera guardia.** Las guardias no son horas extra: en el Estatuto Marco "
    "(Ley 55/2003) son jornada complementaria, así que no tienen tope ni se pagan en B. Te "
    "pagan 1,6 veces la base por el doble de trabajo: la hora de guardia sale más barata "
    "que la normal, como en muchos hospitales de verdad. Pero en sanidad la barra solo sube "
    "con guardias. Bienvenido, y ahora a dormir: estás saliente."
)

#: Discurso del logro `no_residente`: dejar de ser residente fiscal en España.
NONRESIDENT_STORY = (
    "✈️ **Ya no eres residente fiscal en España.** Quien pasa más de 183 días del año "
    "fuera deja de serlo (art. 9.1.a LIRPF), salvo que su familia o sus intereses "
    "económicos sigan aquí (art. 9.1.b). Desde hoy tu sueldo de Hong Kong solo paga allí. "
    "Perro Sanxe mira tu nómina como quien mira un barco que se va. Ojo: si sigues "
    "comprando en el chiringuito, a lo mejor te escribe."
)

#: Discurso del logro `beckham`.
BECKHAM_STORY = (
    "⚽ **Ley Beckham.** El régimen de impatriados (art. 93 LIRPF) es para quien se muda a "
    "España tras 5 años sin residir aquí: durante el año de la llegada y los 5 siguientes, "
    "su sueldo tributa al 24 % fijo hasta 600.000 € en vez de por la escala. Se llama así "
    "porque llegó con Beckham al Real Madrid en 2003. Tú no juegas al fútbol, pero "
    "tributas como si sí."
)


#: Discurso de «Sanxe cobra antes que tú»: la primera retención de los intereses.
INTEREST_TAX_STORY = (
    "El banco te ha pagado intereses y, antes de que los vieras, Perro Sanxe ya se había "
    "quedado el 19 %. Es la retención a cuenta de los rendimientos del capital mobiliario "
    "(art. 101.4 LIRPF): el banco se la quita y se la manda a Hacienda por ti. Cada lunes la "
    "semana se liquida con la escala del ahorro y, si te toca más, te cobra la diferencia. "
    "Devolver, no te devuelve nada: el 19 % es el tramo más bajo."
)

#: Discurso de «Me suben de tramo del ahorro»: el mito del tramo, versión ahorro.
SAVINGS_BRACKET_STORY = (
    "Tus intereses de la semana, proyectados a un año, pasan de 6.000 € y entras en el tramo "
    "del 21 % de la base del ahorro (arts. 66.1 y 76 LIRPF). Tranquilo: el 21 % solo se aplica "
    "a lo que pasa de ese límite, el resto sigue al 19 %. Subir de tramo nunca te deja con "
    "menos dinero. Eso sí, Sanxe te lo cobra el lunes sin preguntar."
)


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
    a += _tiers("social", "welcomes_given", [
        (1, "welcome_1", "Comité de bienvenida", "Dale la bienvenida a alguien nuevo.", C),
        (5, "welcome_5", "Relaciones públicas", "Da la bienvenida a 5 personas.", R),
        (20, "welcome_20", "Portero de discoteca", "Da la bienvenida a 20 personas.", E),
    ])  # fmt: skip
    a += _tiers("social", "welcomes_fast", [
        (1, "welcome_fast", "Más rápido que Hacienda",
         "Da la bienvenida a alguien en su primer minuto en el servidor.", R, True),
    ])  # fmt: skip
    a += _tiers("social", "msg_bot_call", [
        (1, "bot_call", "¿Me llamabas?", "Menciona al bot o di su nombre.", C, True),
    ])  # fmt: skip
    a += _tiers("social", "msg_sanxe", [
        (1, "sanxe", "Invocación", "Nombra a Perro Sanxe en el chat.", C, True),
    ])  # fmt: skip

    # 📝 Lista (cogs/todo.py) ------------------------------------------------------------
    a += _tiers("todo", "todo_added", [
        (1, "todo_add_1", "Apuntado", "Apunta tu primera tarea con `lista`.", C),
        (25, "todo_add_25", "Agenda andante", "Apunta 25 tareas en la lista.", R),
        (100, "todo_add_100", "Jefe de proyecto", "Apunta 100 tareas en la lista.", E),
    ])  # fmt: skip
    a += _tiers("todo", "todo_done", [
        (1, "todo_done_1", "Tachado", "Tacha tu primera tarea de la lista.", C),
        (25, "todo_done_25", "Productivo", "Tacha 25 tareas de la lista.", R),
        (100, "todo_done_100", "Máquina de tachar", "Tacha 100 tareas de la lista.", E),
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

    # 🎰 Tragaperras ---------------------------------------------------------------------
    a += _tiers("slots", "slots_spins", [
        (1, "slots_1", "Tirar de la palanca", "Juega tu primera tirada en la tragaperras.", C),
        (100, "slots_100", "Enganchado", "Juega 100 tiradas en la tragaperras.", C),
        (1_000, "slots_1k", "Zombi de la máquina", "Juega 1.000 tiradas.", E),
        (10_000, "slots_10k", "La máquina te conoce", "Juega 10.000 tiradas.", L),
        (50_000, "slots_50k", "Parte del mobiliario", "Juega 50.000 tiradas.", M),
    ])  # fmt: skip
    a += _tiers("slots", "slots_wins", [
        (10, "slotsw_10", "Tilín tilín", "Gana 10 tiradas en la tragaperras.", C),
        (100, "slotsw_100", "Luces y campanas", "Gana 100 tiradas en la tragaperras.", R),
        (1_000, "slotsw_1k", "Máquina de premios", "Gana 1.000 tiradas en la tragaperras.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_jackpots", [
        (1, "jackpot_1", "¡JACKPOT!", "Saca el premio gordo de la tragaperras.", E),
        (5, "jackpot_5", "Rey del jackpot", "Saca el premio gordo 5 veces.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_jackpot_max", [
        (50_000, "jackpot_50k", "Bote gordo", "Llévate un bote de 50.000 Y$ o más.", L),
        (250_000, "jackpot_250k", "Bote histórico", "Llévate un bote de 250.000 Y$ o más.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_win_max", [
        (10_000, "slots_rain", "Lluvia de monedas", "Gana 10.000 Y$ en una tirada.", R),
        (100_000, "slots_storm", "Tormenta de monedas", "Gana 100.000 Y$ en una tirada.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_three_C", [
        (1, "slots_cherries", "Fruta prohibida", "Saca 🍒 🍒 🍒 en la línea.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_L", [
        (1, "slots_lemons", "Limonada", "Saca 🍋 🍋 🍋 en la línea.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_G", [
        (1, "slots_grapes", "Vendimia", "Saca 🍇 🍇 🍇 en la línea.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_B", [
        (1, "slots_bells", "Campanadas", "Saca 🔔 🔔 🔔 en la línea.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_D", [
        (1, "slots_diamonds", "Diamantes en bruto", "Saca 💎 💎 💎 en la línea.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_7", [
        (1, "slots_777", "Siete vidas", "Saca 7️⃣ 7️⃣ 7️⃣ en la línea.", L),
        (5, "slots_777_5", "Lucky seven", "Saca 7️⃣ 7️⃣ 7️⃣ cinco veces.", M),
    ])  # fmt: skip
    a.append(Achievement(
        id="slots_fruit_shop",
        name="Frutería completa",
        description="Saca un trío de cada: 🍒, 🍋, 🍇, 🔔, 💎 y 7️⃣.",
        category="slots",
        rarity=L,
        conditions=tuple((f"slots_three_{s}", 1) for s in "CLGBD7"),
    ))  # fmt: skip
    a += _tiers("slots", "slots_ldw", [
        (1, "ldw_1", "Ganar perdiendo", "Cobra un premio más pequeño que tu apuesta.", C, True),
        (100, "ldw_100", "Me sale a cuenta", "Cobra 100 premios más pequeños que tu apuesta.", R),
        (1_000, "ldw_1k", "Contabilidad creativa",
         "Cobra 1.000 premios más pequeños que tu apuesta.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_near_miss", [
        (1, "nearmiss_1", "Por un pelo", "Quédate a un símbolo de un premio gordo.", C),
        (25, "nearmiss_25", "¡Ay, bendito!", "Quédate 25 veces a un símbolo del premio gordo.", R),
        (100, "nearmiss_100", "La próxima sí", "Quédate 100 veces a un símbolo.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_anticipation", [
        (10, "antic_10", "Corazón en un puño", "Ve frenar despacio el tercer rodillo 10 veces.", C),
        (100, "antic_100", "Taquicardia", "Ve frenar despacio el tercer rodillo 100 veces.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_scatter_tease", [
        (10, "tease_10", "Te faltó una entrada", "Saca dos 🎟️ y no el tercero 10 veces.", C, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_free_triggers", [
        (1, "free_1", "Entrada VIP", "Consigue giros gratis.", C),
        (10, "free_10", "Pase de temporada", "Consigue giros gratis 10 veces.", R),
        (50, "free_50", "Abono vitalicio", "Consigue giros gratis 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_free_spins", [
        (100, "freespin_100", "Barra libre", "Juega 100 giros gratis.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_hot_spins", [
        (1, "hot_1", "Al rojo vivo", "Juega una tirada con la máquina caliente.", C),
        (50, "hot_50", "Quemado", "Juega 50 tiradas con la máquina caliente.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_hot_big", [
        (1, "hot_big", "Fuego real", "Gana ×20 o más con la máquina caliente.", E, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_wild_wins", [
        (10, "wild_10", "Comodín al rescate", "Gana 10 tiradas gracias al 🃏.", C),
        (100, "wild_100", "Amigo del comodín", "Gana 100 tiradas gracias al 🃏.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_turbo", [
        (100, "turbo_100", "Sin frenos", "Juega 100 tiradas en modo turbo.", C),
        (1_000, "turbo_1k", "Turbodiésel", "Juega 1.000 tiradas en modo turbo.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_auto", [
        (1, "auto_1", "Piloto automático", "Usa Auto ×10.", C),
        (50, "auto_50", "Ni lo miro", "Usa Auto ×10 50 veces.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_session_max", [
        (100, "session_100", "Una más y lo dejo", "Juega 100 tiradas sin cerrar la máquina.", R,
         True),
        (500, "session_500", "Sin pestañear", "Juega 500 tiradas sin cerrar la máquina.", E, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_pot_fed", [
        (1_000, "pot_1k", "Alimentador del bote", "Aporta 1.000 Y$ al bote.", C),
        (10_000, "pot_10k", "Mecenas del bote", "Aporta 10.000 Y$ al bote.", R),
        (100_000, "pot_100k", "El bote es tuyo (o casi)", "Aporta 100.000 Y$ al bote.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_night", [
        (1, "slots_night", "Ludopatía nocturna", "Juega a la tragaperras entre las 3 y las 6.", C,
         True),
    ])  # fmt: skip
    a.append(Achievement(
        id="casino_trilero",
        name="Trilero",
        description="Juega a la ruleta, al blackjack y a la tragaperras.",
        category="casino",
        rarity=R,
        conditions=(("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1)),
    ))  # fmt: skip

    # 🌋 Botes (de momento, volcan) ---------------------------------------------------------
    a += _tiers("botes", "botes_spins", [
        (1, "botes_1", "Hold & win", "Juega tu primera tirada en el Volcán.",
         C),
        (100, "botes_100", "Coleccionista de monedas", "Juega 100 tiradas en los botes.", C),
        (1_000, "botes_1k", "Maletín al hombro", "Juega 1.000 tiradas en los botes.",
         E),
        (10_000, "botes_10k", "Socio de la casa", "Juega 10.000 tiradas en los botes.",
         L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_spins_volcan", [
        (100, "botes_timanfaya", "Turista en Timanfaya", "Juega 100 tiradas en el Volcán.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_collects", [
        (1, "botes_collect", "Recogida", "Recoge las monedas con el recogedor.", C),
        (50, "botes_collect_50", "Barrendero de monedas", "Haz 50 recogidas.", R),
        (500, "botes_collect_500", "Aspiradora", "Haz 500 recogidas.", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_double_collect", [
        (1, "botes_double", "Por las dos puntas", "Recoge con recogedor en el rodillo 1 y el 5.",
         R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_near_miss", [
        (25, "botes_near", "Monedas al viento",
         "Deja 25 veces un buen puñado de monedas sin recoger.", R, True),
    ])  # fmt: skip
    a += _tiers("botes", "botes_ways_5", [
        (1, "botes_five", "De punta a punta", "Gana con un símbolo en los cinco rodillos.", C),
        (25, "botes_five_25", "Ways a mansalva", "Gana 25 veces con los cinco rodillos.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_wild_wins", [
        (50, "botes_wild", "Comodín de confianza", "Gana 50 tiradas con ayuda del comodín.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_chips", [
        (25, "botes_chips", "Fichas al bote", "Saca 25 fichas de bote en el juego base.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonuses", [
        (1, "botes_bonus", "¡Maletín lleno!", "Llena un maletín y juega su bonus.", C),
        (25, "botes_bonus_25", "Abonado al bonus", "Juega 25 bonus.", R),
        (100, "botes_bonus_100", "El bonus me conoce", "Juega 100 bonus.", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_green", [
        (1, "botes_green", "Verde que te quiero verde", "Juega un bonus verde.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_blue", [
        (1, "botes_blue", "Azul celeste", "Juega un bonus azul.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_red", [
        (1, "botes_red", "Rojo pasión", "Juega un bonus rojo.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_grand", [
        (1, "botes_grand_bonus", "Fin del mundo", "Juega el gran bonus de los tres colores.", R),
        (10, "botes_grand_bonus_10", "Apocalipsis en bucle", "Juega 10 grandes bonus.", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_mini", [
        (1, "botes_mini", "MINI", "Gana el bote MINI (10 monedas en un bonus).", R),
        (10, "botes_mini_10", "Minis en serie", "Gana 10 botes MINI.", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_major", [
        (1, "botes_major", "MAJOR", "Gana el bote MAJOR (15 monedas en un bonus).", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_grand", [
        (1, "botes_grand", "GRAND", "Llena la pantalla de monedas y llévate el GRAND.", M),
    ])  # fmt: skip
    a += _tiers("botes", "botes_almost_grand", [
        (1, "botes_19", "Por una moneda", "Acaba un bonus con 19 monedas.", L, True),
    ])  # fmt: skip
    a += _tiers("botes", "botes_mult_max", [
        (5, "botes_mult_5", "Multiplicador ×5", "Lleva el multiplicador de un bonus a ×5.", R),
        (10, "botes_mult_10", "Multiplicador ×10", "Lleva el multiplicador de un bonus a ×10.",
         L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_instant", [
        (10, "botes_instant", "¡Pum, por dos!", "Saca 10 multiplicadores inmediatos.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_maximizer", [
        (1, "botes_max", "Botes al máximo", "Saca un maximizador de botes.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_mystery", [
        (25, "botes_mystery", "Misterio resuelto", "Destapa 25 símbolos misteriosos.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_max", [
        (10_000, "botes_bonus_10k", "Maletín de billetes", "Gana 10.000 Y$ en un bonus.", R),
        (100_000, "botes_bonus_100k", "Maletín de lingotes", "Gana 100.000 Y$ en un bonus.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("botes", "botes_win_max", [
        (10_000, "botes_win_10k", "Lluvia de lava", "Gana 10.000 Y$ en una tirada base.", R),
    ], unit="money")  # fmt: skip
    a += _tiers("botes", "botes_turbo", [
        (100, "botes_turbo", "Turbo en la mina", "Juega 100 tiradas en turbo.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_auto", [
        (10, "botes_auto", "Que trabaje la máquina", "Usa Auto ×10 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_night", [
        (1, "botes_night", "Erupción de madrugada", "Juega a los botes entre las 3 y las 6.", C,
         True),
    ])  # fmt: skip

    # 🚀 Crash --------------------------------------------------------------------------
    a += _tiers("crash", "crash_rounds", [
        (1, "crash_1", "Despegue", "Juega tu primera ronda de Crash.", C),
        (100, "crash_100", "Piloto", "Juega 100 rondas de Crash.", R),
        (1_000, "crash_1k", "Astronauta", "Juega 1.000 rondas de Crash.", E),
        (5_000, "crash_5k", "Vives en órbita", "Juega 5.000 rondas de Crash.", L),
    ])  # fmt: skip
    a += _tiers("crash", "crash_cashouts", [
        (10, "crashc_10", "Paracaidista", "Retírate a tiempo 10 veces.", C),
        (100, "crashc_100", "Saltador profesional", "Retírate a tiempo 100 veces.", R),
        (1_000, "crashc_1k", "Siempre a tiempo", "Retírate a tiempo 1.000 veces.", E),
    ])  # fmt: skip
    a += _tiers("crash", "crash_cashout_max", [
        (200, "crash_x2", "Doble o nada", "Retírate en 2x o más.", C),
        (1_000, "crash_x10", "Diez veces", "Retírate en 10x o más.", R),
        (5_000, "crash_x50", "Estratosfera", "Retírate en 50x o más.", E),
        (10_000, "crash_x100", "Centenario", "Retírate en 100x o más.", L),
        (100_000, "crash_x1000", "Hasta la Luna", "Retírate en 1.000x.", M, True),
    ])  # fmt: skip
    a += _tiers("crash", "crash_win_max", [
        (10_000, "crash_fuel", "Combustible", "Gana 10.000 Y$ en una ronda de Crash.", R),
        (100_000, "crash_gold", "Cohete de oro", "Gana 100.000 Y$ en una ronda de Crash.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("crash", "crash_auto", [
        (10, "crasha_10", "Control de crucero", "Cobra 10 veces con el auto-retiro.", C),
        (100, "crasha_100", "Sin manos", "Cobra 100 veces con el auto-retiro.", R),
    ])  # fmt: skip
    a += _tiers("crash", "crash_close", [
        (1, "crash_close", "Por los pelos", "Retírate a menos de un 5 % de la explosión.", R),
        (10, "crash_close_10", "Nervios de acero", "Retírate por los pelos 10 veces.", E),
    ])  # fmt: skip
    a += _tiers("crash", "crash_last_out", [
        (1, "crash_last", "El último en saltar",
         "Sé el último en retirarse con más gente aún dentro.", R),
    ])  # fmt: skip
    a += _tiers("crash", "crash_instant", [
        (1, "crash_ramp", "Ni despegó", "Pierde en una ronda que explota en 1,00x.", C, True),
    ])  # fmt: skip
    a += _tiers("crash", "crash_greedy", [
        (1, "crash_greedy", "La avaricia rompe el saco",
         "Pierde en una ronda que llegó a 10x.", R, True),
    ])  # fmt: skip
    a += _tiers("crash", "crash_moon", [
        (1, "crash_moon", "Testigo lunar", "Juega una ronda que llega a 100x.", E, True),
    ])  # fmt: skip
    a += _tiers("crash", "crash_party_max", [
        (3, "crash_crew", "Tripulación", "Juega una ronda con 3 personas.", C),
        (6, "crash_charter", "Vuelo chárter", "Juega una ronda con 6 personas.", R),
    ])  # fmt: skip

    # 💣 Minas --------------------------------------------------------------------------
    a += _tiers("mines", "mines_games", [
        (1, "mines_1", "Zapador", "Juega tu primera partida de Minas.", C),
        (100, "mines_100", "Artificiero", "Juega 100 partidas de Minas.", R),
        (1_000, "mines_1k", "Campo minado", "Juega 1.000 partidas de Minas.", E),
    ])  # fmt: skip
    a += _tiers("mines", "mines_gems", [
        (100, "gems_100", "Buscador", "Destapa 100 diamantes.", C),
        (1_000, "gems_1k", "Minero", "Destapa 1.000 diamantes.", R),
        (10_000, "gems_10k", "Mina de diamantes", "Destapa 10.000 diamantes.", E),
    ])  # fmt: skip
    a += _tiers("mines", "mines_cashouts", [
        (10, "minesc_10", "Retirada a tiempo", "Cobra 10 partidas de Minas.", C),
        (100, "minesc_100", "Sangre fría", "Cobra 100 partidas de Minas.", R),
    ])  # fmt: skip
    a += _tiers("mines", "mines_booms", [
        (1, "boom_1", "Boom", "Pisa una mina.", C),
        (100, "boom_100", "Saltaminas", "Pisa 100 minas.", R),
    ])  # fmt: skip
    a += _tiers("mines", "mines_first_boom", [
        (1, "boom_first", "A la primera",
         "Pisa una mina justo después de la casilla segura.", C, True),
    ])  # fmt: skip
    a += _tiers("mines", "mines_almost", [
        (1, "boom_almost", "Tan cerca", "Pisa una mina cuando solo quedaba una casilla buena.",
         E, True),
    ])  # fmt: skip
    a += _tiers("mines", "mines_mult_max", [
        (500, "mines_x5", "Cinco veces", "Cobra en ×5 o más.", C),
        (2_000, "mines_x20", "Veinte veces", "Cobra en ×20 o más.", R),
        (10_000, "mines_x100", "Cien veces", "Cobra en ×100 o más.", E),
        (100_000, "mines_x1000", "Mil veces", "Cobra en ×1.000 o más.", L),
        (1_000_000, "mines_x10k", "Diez mil veces", "Cobra en ×10.000 o más.", M),
    ])  # fmt: skip
    a += _tiers("mines", "mines_win_max", [
        (10_000, "mines_rich", "Veta de oro", "Gana 10.000 Y$ en una partida de Minas.", R),
        (100_000, "mines_richer", "Filón", "Gana 100.000 Y$ en una partida de Minas.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("mines", "mines_24", [
        (1, "mines_24", "Ruleta rusa al revés", "Gana con 12 minas, el máximo.", C),
    ])  # fmt: skip
    a += _tiers("mines", "mines_clear", [
        (1, "mines_clear", "Desminado", "Destapa todas las casillas buenas.", R),
    ])  # fmt: skip
    a += _tiers("mines", "mines_clear_hard", [
        (1, "mines_clear_5", "Artificiero de élite",
         "Destapa todas las casillas buenas con 5 minas o más.", M, True),
    ])  # fmt: skip
    a += _tiers("mines", "mines_streak_max", [
        (10, "mines_streak_10", "Pisando firme", "Destapa 10 casillas en una partida.", C),
        (15, "mines_streak_15", "Detector humano", "Destapa 15 casillas en una partida.", R),
        (20, "mines_streak_20", "Pies de plomo", "Destapa 20 casillas en una partida.", E),
    ])  # fmt: skip
    a += _tiers("mines", "mines_random", [
        (50, "mines_dice", "Que decida el destino", "Destapa 50 casillas con 🎲.", C),
    ])  # fmt: skip
    a.append(Achievement(
        id="casino_all_games",
        name="Todoterreno",
        description="Juega a la ruleta, al blackjack, a la tragaperras, al Crash y a Minas.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1),
        ),
    ))  # fmt: skip

    # 🌸 Pachinko -----------------------------------------------------------------------
    a += _tiers("pachinko", "pachinko_volleys", [
        (1, "pachi_1", "Primera bola", "Lanza tu primera tanda en el pachinko.", C),
        (100, "pachi_100", "Salón de Akihabara", "Lanza 100 tandas en el pachinko.", R),
        (1_000, "pachi_1k", "Ojos de neón", "Lanza 1.000 tandas en el pachinko.", E),
        (10_000, "pachi_10k", "Vives en el salón", "Lanza 10.000 tandas en el pachinko.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_starts", [
        (100, "pachi_start_100", "Por la ranura", "Mete 100 bolas por START.", C),
        (1_000, "pachi_start_1k", "Tulipán abierto", "Mete 1.000 bolas por START.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_reach", [
        (1, "pachi_reach", "¡REACH!", "Mira un reach en la pantalla.", C),
        (100, "pachi_reach_100", "Corazón en un puño", "Mira 100 reach.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_fake_reach", [
        (25, "pachi_fake_25", "Me la volvió a hacer", "Pierde 25 reach por un número.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_atari", [
        (1, "pachi_atari", "¡ATARI!", "Saca tres iguales en la pantalla.", R),
        (25, "pachi_atari_25", "Bendecido por Jovani", "Saca 25 ataris.", E),
        (100, "pachi_atari_100", "La compuerta te quiere", "Saca 100 ataris.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_rush", [
        (1, "pachi_rush", "Kakuhen", "Saca un rush (atari con número impar).", R),
        (10, "pachi_rush_10", "Adicto al rush", "Saca 10 rush.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_super", [
        (1, "pachi_777", "7️⃣7️⃣7️⃣", "Saca el SUPER RUSH.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_renchan_max", [
        (3, "pachi_ren_3", "Renchan", "Encadena 3 premios gordos en un rush.", R),
        (5, "pachi_ren_5", "Racha imparable", "Encadena 5 premios gordos.", E),
        (10, "pachi_ren_10", "Lluvia de bolas", "Encadena 10 premios gordos.", L),
        (15, "pachi_ren_15", "Fiebre total", "Encadena 15 premios gordos.", M, True),
        (25, "pachi_ren_25", "El oni sonríe", "Encadena 25 premios gordos, el máximo (Oni).",
         M, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_board_sakura", [
        (100, "pachi_hanami", "Hanami", "Lanza 100 tandas en el tablero Sakura.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_board_oni", [
        (100, "pachi_infierno", "Bajada a los infiernos", "Lanza 100 tandas en el tablero Oni.",
         R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_atari_dragon", [
        (1, "pachi_dragon", "Perla del dragón", "Saca un atari en el tablero Dragón.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_atari_oni", [
        (1, "pachi_oni", "Domador de onis", "Saca un atari en el tablero Oni.", E),
        (10, "pachi_oni_10", "Amigo de los demonios", "Saca 10 ataris en el tablero Oni.", L),
    ])  # fmt: skip
    a.append(Achievement(
        id="pachi_tourist",
        name="Turista de salones",
        description="Juega en los cuatro tableros del pachinko.",
        category="pachinko",
        rarity=R,
        conditions=tuple((f"pachinko_board_{key}", 1) for key in PACHINKO_BOARDS),
    ))  # fmt: skip
    a += _tiers("pachinko", "pachinko_corners", [
        (1, "pachi_corner", "Esquinita", "Mete una bola en un bolsillo de esquina.", C),
        (25, "pachi_corner_25", "Francotirador", "Mete 25 bolas en las esquinas.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_full_hold", [
        (1, "pachi_hold", "Reserva llena", "Llena las 4 reservas en una tanda.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_wasted", [
        (1, "pachi_limbo", "Bolas al limbo", "Mete una bola en START con la reserva llena.", R,
         True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_blank", [
        (1, "pachi_blank", "Todas por el desagüe", "Pierde las 10 bolas de una tanda.", R, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_win_max", [
        (10_000, "pachi_rich", "Bandeja llena", "Gana 10.000 Y$ en una tanda.", R),
        (100_000, "pachi_richer", "Rey del salón", "Gana 100.000 Y$ en una tanda.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("pachinko", "pachinko_session_max", [
        (50, "pachi_session", "Sin levantarse del taburete", "Lanza 50 tandas en una máquina.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_burst", [
        (10, "pachi_burst", "Mano en el gatillo", "Usa la Ráfaga 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_turbo", [
        (100, "pachi_turbo", "Prisa japonesa", "Lanza 100 tandas en turbo.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_night", [
        (1, "pachi_night", "Salón 24 horas", "Juega al pachinko entre las 3 y las 6.", C, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="casino_six_games",
        name="Ludópata integral",
        description="Juega a los seis juegos del casino, pachinko incluido.",
        category="casino",
        rarity=E,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
        ),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_seven_games",
        name="Los siete pecados",
        description="Juega a los siete juegos del casino, botes incluidos.",
        category="casino",
        rarity=L,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
            ("botes_spins", 1),
        ),
    ))  # fmt: skip

    # 🏦 Banco: Bizum ----------------------------------------------------------------------
    a += _tiers("bizum", "bizum_sent_count", [
        (1, "bizum_1", "Te hago un Bizum", "Manda tu primer Bizum.", C),
        (25, "bizum_25", "Cuentas claras", "Manda 25 Bizums.", R),
        (100, "bizum_100", "Tesorero del grupo", "Manda 100 Bizums.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_sent", [
        (10_000, "bizumy_10k", "Invito yo", "Manda 10.000 Y$ en Bizums.", C),
        (100_000, "bizumy_100k", "Banco de los colegas", "Manda 100.000 Y$ en Bizums.", R),
        (1_000_000, "bizumy_1m", "Herencia en vida", "Manda 1.000.000 Y$ en Bizums.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "bizum_received_count", [
        (1, "bizumr_1", "Te ha llegado un Bizum", "Recibe tu primer Bizum.", C),
        (25, "bizumr_25", "Con amigos así", "Recibe 25 Bizums.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_received", [
        (100_000, "bizumr_100k", "Vivo de los colegas", "Recibe 100.000 Y$ en Bizums.", R),
    ], unit="money")  # fmt: skip
    a.append(Achievement(
        id="bizum_espaldas",
        name="A espaldas de Sánchez",
        description=(
            f"Manda un Bizum de más de {_thousands(BIZUM_MAX_OPERATION)} Y$, el máximo "
            "por operación en España (1.000 €)."
        ),
        category="bizum",
        rarity=E,
        conditions=(("bizum_max", BIZUM_MAX_OPERATION + 1),),
        unit="money",
        story=BIZUM_LIMIT_STORY,
    ))  # fmt: skip
    a += _tiers("bizum", "bizum_day_max", [
        (BIZUM_MAX_DAILY + 1, "bizum_daily", "Límite diario",
         f"Manda más de {_thousands(BIZUM_MAX_DAILY)} Y$ en Bizums en un día (2.000 €).", R,
         True),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "bizum_full", [
        (5, "bizum_pitufeo", "Pitufeo",
         f"Manda 5 Bizums de justo {_thousands(BIZUM_MAX_OPERATION)} Y$, sin pasarte ni un "
         "yapdólar.", R, True),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_min", [
        (1, "bizum_cents", "Te debo 50 céntimos",
         f"Manda un Bizum de {BIZUM_MIN_AMOUNT} Y$, el mínimo.", C, True),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_broke", [
        (1, "bizum_broke", "Todo por un amigo", "Quédate a cero mandando un Bizum.", R, True),
    ])  # fmt: skip

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
    a.append(Achievement(
        id="tax_first",
        name="Bienvenido a España",
        description="Paga IRPF por primera vez.",
        category="economy",
        rarity=C,
        conditions=(("tax_paid", 1),),
        secret=True,
        story=FIRST_TAX_STORY,
    ))  # fmt: skip
    a += _tiers("economy", "tax_paid", [
        (1_000, "tax_1k", "Contribuyente", "Paga 1.000 Y$ de IRPF.", C),
        (10_000, "tax_10k", "Patriota fiscal", "Paga 10.000 Y$ de IRPF.", R),
        (100_000, "tax_100k", "Favorito de Perro Sanxe", "Paga 100.000 Y$ de IRPF.", E),
        (1_000_000, "tax_1m", "Mecenas del Estado", "Paga 1.000.000 Y$ de IRPF.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "wealth_tax_paid", [
        (1, "wealth_1", "Grande de España", "Paga el Impuesto sobre el Patrimonio.", R),
        (10_000, "wealth_10k", "Fortuna amenazada", "Paga 10.000 Y$ de Patrimonio.", E),
        (100_000, "wealth_100k", "Perro Sanxe te pone velas",
         "Paga 100.000 Y$ de Patrimonio.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "wealth_tax_weeks", [
        (10, "wealth_10w", "Rico de toda la vida", "Paga Patrimonio 10 semanas.", L),
    ])  # fmt: skip
    # Intereses de la cuenta (cogs/intereses.py: day_stats, savings_stats y hint_for)
    a += _tiers("bizum", "interest_earned", [
        (1, "interest_1", "La octava maravilla del mundo",
         "Cobra intereses por primera vez. Einstein lo flipaba con esto.", C),
        (1_000, "interest_1k", "El dinero trabaja por ti", "Cobra 1.000 Y$ netos de intereses.", R),
        (10_000, "interest_10k", "Vivir de las rentas", "Cobra 10.000 Y$ netos de intereses.", E),
        (100_000, "interest_100k", "Rentista de toda la vida",
         "Cobra 100.000 Y$ netos de intereses.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "interest_days", [
        (30, "interest_30d", "Cliente fiel", "Cobra intereses 30 días.", C),
        (365, "interest_365d", "El BCE me sigue en Instagram", "Cobra intereses 365 días.", L),
    ])  # fmt: skip
    a.append(Achievement(
        id="interest_tax_1", name="Sanxe cobra antes que tú",
        description="Que te retengan el 19 % de tus intereses.", category="bizum",
        rarity=C, conditions=(("interest_tax", 1),), unit="money", story=INTEREST_TAX_STORY,
    ))  # fmt: skip
    a += _tiers("bizum", "interest_tax", [
        (10_000, "interest_tax_10k", "Perro Sanxe se fuma un puro con tus ahorros",
         "Paga 10.000 Y$ de IRPF por tus intereses.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "interest_avg_max", [
        (INTEREST_TIERS[0][0], "interest_tier_2", "He leído la letra pequeña",
         f"Pasa de {_thousands(INTEREST_TIERS[0][0])} Y$ de saldo medio y que el banco te baje "
         "el tipo.", C),
        (100_000, "interest_mattress", "Para eso lo dejo en el colchón",
         f"Ten 100.000 Y$ de saldo medio: el banco no paga nada por encima de "
         f"{_thousands(INTEREST_TOP)}.", R),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "interest_capped", [
        (1, "interest_falcon", "Ahorro en Falcon",
         f"Cobra el máximo de la cuenta: {_thousands(INTEREST_DAILY_MAX)} Y$ brutos en un día.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_capped_streak", [
        (30, "interest_capped_30", "Rentista de barrio",
         "Cobra el máximo diario 30 días seguidos.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_floor_streak", [
        (90, "interest_grandma", "El plazo fijo de la abuela",
         f"Pasa 90 días seguidos sin bajar de {_thousands(INTEREST_TIERS[0][0])} Y$.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_resist_streak", [
        (30, "interest_resist", "Manual de resistencia",
         f"Pasa 30 días seguidos sin bajar de {_thousands(RESIST_BALANCE)} Y$.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_still_streak", [
        (5, "interest_still_5", "Cinco días de reflexión",
         "Cobra intereses 5 días seguidos sin mover ni un Y$.", R),
        (7, "interest_still_7", "Esto lo pagamos entre todos",
         "Cobra intereses 7 días seguidos sin hacer absolutamente nada.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_ant_streak", [
        (7, "interest_ant", "Hormiguita", "Cobra intereses 7 días seguidos sin gastar nada.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_grasshopper", [
        (1, "interest_grasshopper", "La cigarra",
         "Cobra una nómina y acaba el día sin para una tirada.", C),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_gambled", [
        (1, "interest_gambled", "Me lo fundo en intereses",
         "Pierde en el casino, el mismo día, lo que te acaba de pagar el banco.", C),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_beats_imv", [
        (1, "interest_beats_imv", "Paguita de rentista",
         "Cobra en un día más de intereses que de IMV.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_comeback", [
        (1, "interest_comeback", "Volví solo a por los intereses",
         "Vuelve tras una semana sin aparecer y encuéntrate los intereses cobrados.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_zero", [
        (1, "interest_zero", "Cero patatero",
         "Pasa un día activo con el monedero a cero: ni un Y$ de intereses.", C, True),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_rounding", [
        (1, "interest_rounding", "Redondeo a favor de Hacienda",
         "Cobra tan pocos intereses que el redondeo deja a Sanxe con más del 25 %.", C, True),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_bizum_trick", [
        (1, "interest_bizum_trick", "Ingeniería fiscal de barrio",
         "Haz un Bizum que deje tu saldo justo por debajo de un tramo de la cuenta.", R, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="savings_bracket", name="Me suben de tramo del ahorro",
        description="Que la liquidación semanal de tus intereses llegue al tramo del 21 %.",
        category="bizum", rarity=R, conditions=(("savings_rate_max", 21),),
        story=SAVINGS_BRACKET_STORY,
    ))  # fmt: skip
    a += _tiers("economy", "donated", [
        (1, "donate_1", "Alma caritativa", "Dona a una ONG.", C),
        (10_000, "donate_10k", "Filántropo de postureo", "Dona 10.000 Y$ a ONGs.", R),
        (100_000, "donate_100k", "Mecenas del chiringuito", "Dona 100.000 Y$ a ONGs.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "ongs_supported", [
        (4, "donate_all", "Accionista del tercer sector", "Dona a las 4 ONGs.", R),
    ])  # fmt: skip
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

    # 🎟️ Loterías -------------------------------------------------------------------------
    a += _tiers("lottery", "lottery_bets", [
        (1, "lotto_1", "Hoy me toca", "Compra un décimo o una apuesta.", C),
        (50, "lotto_50", "Fijo en la administración", "Compra 50 décimos o apuestas.", R),
        (500, "lotto_500", "Cliente de Doña Manolita", "Compra 500 décimos o apuestas.", E),
        (5_000, "lotto_5k", "La suerte al por mayor", "Compra 5.000 décimos o apuestas.", L),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_spent", [
        (10_000, "lspend_10k", "Impuesto a la ilusión", "Juega 10.000 Y$ a la lotería.", C),
        (100_000, "lspend_100k", "El Estado te lo agradece", "Juega 100.000 Y$.", R),
        (1_000_000, "lspend_1m", "Mecenas de Hacienda", "Juega 1.000.000 Y$.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("lottery", "lottery_prizes", [
        (1, "lwin_1", "¡Me ha tocado!", "Cobra un premio de lotería, aunque sea el reintegro.", C),
        (25, "lwin_25", "Tocado por la suerte", "Cobra 25 premios de lotería.", R),
        (250, "lwin_250", "Cuestión de estadística", "Cobra 250 premios de lotería.", E),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_won", [
        (10_000, "lwon_10k", "Para gastos", "Gana 10.000 Y$ en loterías.", C),
        (100_000, "lwon_100k", "Pellizco", "Gana 100.000 Y$ en loterías.", R),
        (1_000_000, "lwon_1m", "Pelotazo", "Gana 1.000.000 Y$ en loterías.", E),
        (10_000_000, "lwon_10m", "Me jubilo", "Gana 10.000.000 Y$ en loterías.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("lottery", "lottery_win_max", [
        (100_000, "lbig_100k", "Un buen pellizco", "Cobra 100.000 Y$ con un solo boleto.", E),
        (4_000_000, "lbig_4m", "Hoy no se trabaja", "Cobra 4.000.000 Y$ con un solo boleto.",
         M, True),
    ], unit="money")  # fmt: skip
    a += _tiers("lottery", "lottery_reintegros", [
        (1, "reint_1", "Al menos lo recupero", "Cobra un reintegro.", C),
        (50, "reint_50", "Vuelta a empezar", "Cobra 50 reintegros.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_navidad", [
        (1, "xmas_1", "Espíritu navideño", "Compra un décimo de Navidad.", C),
        (20, "xmas_20", "La peña de la oficina", "Compra 20 décimos de Navidad.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_nino", [
        (1, "nino_1", "Los Reyes también juegan", "Compra un décimo del Niño.", C),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_pedrea", [
        (1, "pedrea", "Pedrea", "Cobra una pedrea de la Lotería de Navidad.", E, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_gordo_navidad", [
        (1, "el_gordo", "EL GORDO", "Te toca el Gordo de Navidad.", M, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_euro_bets", [
        (1, "euro_1", "Europeísta", "Juega una apuesta de Euromillones.", C),
        (100, "euro_100", "Soñando en euros", "Juega 100 apuestas de Euromillones.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_lotto4", [
        (1, "lotto4", "Cuatro de seis", "Acierta 4 números en la Primitiva o la Bonoloto.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_lotto5", [
        (1, "lotto5", "Rozando el cielo", "Acierta 5 en la Primitiva o la Bonoloto.", L, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_jackpot", [
        (1, "lotto_jackpot", "Bote", "Llévate la 1ª categoría de un juego de bote.", M, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_scratches", [
        (1, "rasca_1", "Rasca y gana", "Rasca un boleto de la ONCE.", C),
        (100, "rasca_100", "Uña de oro", "Rasca 100 boletos.", R),
        (1_000, "rasca_1k", "Sin uñas", "Rasca 1.000 boletos.", E),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_scratch_top", [
        (1, "rasca_top", "Premio máximo", "Saca el premio más alto de un rasca.", L, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_draw_bets_max", [
        (MAX_LOTTERY_PER_DRAW, "brute_force", "Fuerza bruta",
         f"Juega {MAX_LOTTERY_PER_DRAW} apuestas o décimos en un mismo sorteo.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_broke_buy", [
        (1, "lotto_broke", "Todo al número", "Gasta todo tu saldo en lotería.", L, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="gravamen",
        name="Hacienda también juega",
        description="Paga el gravamen especial de un premio de lotería.",
        category="lottery",
        rarity=E,
        conditions=(("lottery_gravamen", 1),),
        secret=True,
        story=LOTTERY_TAX_STORY,
    ))  # fmt: skip

    # 🛍️ Tienda ---------------------------------------------------------------------------
    a += _tiers("shop", "shop_purchases", [
        (1, "shop_1", "Estrenando cartera", "Compra algo en la tienda.", C),
        (10, "shop_10", "Cliente fijo", "Haz 10 compras en la tienda.", C),
        (50, "shop_50", "Comprador compulsivo", "Haz 50 compras en la tienda.", R),
        (200, "shop_200", "Tarjeta de socio", "Haz 200 compras en la tienda.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_spent", [
        (10_000, "spend_10k", "Consumista", "Gasta 10.000 Y$ en la tienda.", C),
        (100_000, "spend_100k", "Motor de la economía", "Gasta 100.000 Y$ en la tienda.", R),
        (1_000_000, "spend_1m", "El PIB eres tú", "Gasta 1.000.000 Y$ en la tienda.", E),
        (10_000_000, "spend_10m", "Ballena", "Gasta 10.000.000 Y$ en la tienda.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_igic", [
        (1_000, "igic_1k", "Aquí hasta el café paga", "Paga 1.000 Y$ de IGIC.", C),
        (25_000, "igic_25k", "Sostén del Cabildo", "Paga 25.000 Y$ de IGIC.", R),
        (250_000, "igic_250k", "Contribuyente canario de honor", "Paga 250.000 Y$ de IGIC.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_roles", [
        (1, "shoprole_1", "Con estilo", "Cómprate un rol.", C),
        (5, "shoprole_5", "Armario lleno", "Compra 5 roles.", R),
        (20, "shoprole_20", "Camaleón", "Compra 20 roles.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_renewals", [
        (3, "renew_3", "Inquilino fiel", "Renueva un alquiler de rol 3 veces.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_boosts", [
        (1, "boost_1", "Turbo", "Compra un potenciador de XP.", C),
        (10, "boost_10", "Dopado", "Compra 10 potenciadores de XP.", R),
        (50, "boost_50", "Nitro humano", "Compra 50 potenciadores de XP.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_boost_queue_max", [
        (3, "boost_queue", "Turbo en cola", "Ten 3 potenciadores esperando turno a la vez.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_collection_max", [
        (1, "collect_1", "Primera pieza", "Consigue un coleccionable.", C),
        (5, "collect_5", "Vitrina", "Ten 5 coleccionables distintos.", R),
        (15, "collect_15", "Museo privado", "Ten 15 coleccionables distintos.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_sale_buys", [
        (1, "sale_1", "Cazador de rebajas", "Compra algo rebajado.", C),
        (10, "sale_10", "Black Friday", "Compra 10 cosas rebajadas.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_discount_max", [
        (50, "sale_half", "A mitad de precio", "Compra algo con un 50 % de rebaja o más.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_luxury", [
        (1, "luxury_1", "Nuevo rico", "Compra algo que paga el IGIC de lujo (15 %).", R),
        (10, "luxury_10", "Clase alta", "Compra 10 cosas con IGIC de lujo.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_big_buy_max", [
        (50_000, "bigbuy_50k", "Capricho caro", "Paga 50.000 Y$ de una sola vez.", R),
        (500_000, "bigbuy_500k", "Tarjeta negra", "Paga 500.000 Y$ de una sola vez.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_limited", [
        (1, "limited_1", "Edición limitada", "Compra una unidad de una edición limitada.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_first_serial", [
        (1, "serial_1", "Unidad nº 1", "Llévate la primera unidad de una edición limitada.", E,
         True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_last_unit", [
        (1, "last_unit", "El último mohicano", "Llévate la última unidad de algo.", E, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_broke_buy", [
        (1, "broke_buy", "Lo quiero, lo tengo", "Gástate todo tu saldo en una compra.", L, True),
    ])  # fmt: skip

    # 🪏 Trabajo ------------------------------------------------------------------------
    a += _tiers("work", "work_shifts", [
        (1, "pala_1", "Coge la pala", "Ficha tu primer turno.", C),
        (10, "pala_10", "Currante", "Ficha 10 turnos.", C),
        (100, "pala_100", "Obrero del mes", "Ficha 100 turnos.", R),
        (500, "pala_500", "Mula de carga", "Ficha 500 turnos.", E),
        (1_000, "pala_1k", "Toda una vida con la pala", "Ficha 1.000 turnos.", L),
    ])  # fmt: skip
    a += _tiers("work", "work_promotions", [
        (1, "ascenso_1", "Me han subido el sueldo (en bruto)", "Asciende por primera vez.", C),
    ])  # fmt: skip
    a += _tiers("work", "work_jobs_top", [
        (1, "top_1", "Lo más alto del escalafón", "Llega al puesto 5 de un oficio.", E),
        (3, "top_3", "Currículum de Pokédex", "Llega al puesto 5 de 3 oficios.", L),
    ])  # fmt: skip
    a.append(Achievement(
        id="paguita", name="Me quedo con la paguita",
        description="Rechaza un ascenso.", category="work", rarity=R,
        conditions=(("work_declined", 1),), story=DECLINE_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_demoted", [
        (1, "degradado", "De vuelta a la pala", "Que te bajen de puesto.", C, True),
    ])  # fmt: skip
    a += _tiers("work", "work_job_changes", [
        (5, "culo_inquieto", "Culo inquieto", "Cambia de oficio 5 veces.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_perfect", [
        (1, "turno_100", "Turno perfecto", "Saca un 100 en un turno.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_good", [
        (20, "empleado_mes", "Empleado del mes", "Haz 20 turnos de 90 o más.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_shifts_day_max", [
        (8, "doble_jornada", "Jornada partida… en dos jornadas", "Ficha 8 turnos en un día.", E),
        (12, "que_es_dormir", "¿Qué es dormir?", "Ficha 12 turnos en un día.", L, True),
    ])  # fmt: skip
    a += _tiers("work", "work_streak_max", [
        (7, "domingo_debiles", "El domingo es para los débiles", "Ficha 7 días seguidos.", R),
        (30, "senor_pala", "Tus hijos te llaman «el señor de la pala»",
         "Ficha 30 días seguidos.", L),
    ])  # fmt: skip
    a += _tiers("work", "work_night", [
        (1, "turno_noche", "Turno de noche", "Ficha entre las 0:00 y las 6:00.", C),
        (25, "vampiro", "Vampiro laboral", "Ficha 25 turnos de madrugada.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_sunday", [
        (10, "misa_doce", "Misa de doce en la obra", "Ficha 10 turnos en domingo.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_birthday", [
        (1, "cumple_pala", "Feliz cumpleaños, a currar", "Ficha el día de tu cumpleaños.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_christmas", [
        (1, "nochebuena", "Nochebuena en la oficina", "Ficha el 24 o el 25 de diciembre.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_reyes", [
        (1, "reyes_extra", "Los Reyes me trajeron horas extra", "Ficha el 6 de enero.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_mayday", [
        (1, "ironia", "Ironía", "Ficha el 1 de mayo, Día del Trabajador.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_family_zero", [
        (1, "madre_discord", "Tu madre se enteró de que existes por Discord",
         "Deja la familia a 0.", R, True),
        (3, "intervencion", "Intervención familiar", "Deja la familia a 0 tres veces.", E, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="ochenta_horas", name="80 horas al año, ja",
        description="Pasa del límite legal de horas extra.", category="work", rarity=R,
        conditions=(("work_past_limit", 1),), secret=True, story=OVERTIME_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_zombie", [
        (1, "zombi", "Zombi asalariado", "Ficha con la batería por debajo de 0.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_coffees_day_max", [
        (3, "barraquito_iv", "Barraquito intravenoso", "Tómate 3 cafés en un día.", C),
        (4, "temblores", "Temblores de oficina", "Tómate el cuarto café del día.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_accidents", [
        (1, "parte", "Parte de accidente", "Ten un accidente laboral.", C, True),
        (5, "mutua", "La mutua ya te conoce", "Ten 5 accidentes laborales.", E, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="primera_nomina", name="Mi primera nómina (y mi primer disgusto)",
        description="Cobra tu primera nómina.", category="work", rarity=C,
        conditions=(("work_payslips", 1),), story=FIRST_PAYSLIP_STORY,
    ))  # fmt: skip
    a.append(Achievement(
        id="tramo", name="Me suben de tramo",
        description="Que te retengan un 30 % o más de IRPF en una nómina.",
        category="work", rarity=R, conditions=(("work_irpf_pct_max", 30),),
        story=BRACKET_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_half_salary", [
        (1, "medio_sueldo", "Medio sueldo para Sanxe",
         "Cobra una nómina cuyos impuestos pasen del 80 % del neto.", R),
    ])  # fmt: skip
    a.append(Achievement(
        id="socio_hacienda", name="Socio de Hacienda",
        description="En 7 días, paga en impuestos (nóminas, IGIC y Patrimonio) más que tu neto.",
        category="work", rarity=L, conditions=(("work_partner", 1),), secret=True,
        story=PARTNER_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_max_base", [
        (1, "tope", "Tope de cotización", "Pasa de la base máxima de cotización.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_taxes", [
        (1_000_000, "sanxe_pala", "Perro Sanxe come de tu pala",
         "Paga 1.000.000 Y$ entre IRPF y Seguridad Social trabajando.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("work", "imv_with_salary", [
        (1, "compatible", "Compatibilidad total", "Cobra el IMV con nómina esa semana.", C),
    ])  # fmt: skip
    a += _tiers("work", "imv_floor", [
        (1, "rico_paguita", "Demasiado rico para la paguita",
         "Cobra el IMV mínimo por lo que ganas trabajando.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_black", [
        (1, "negro_1", "En B", "Cobra un turno en negro.", C, True),
    ])  # fmt: skip
    a += _tiers("work", "work_caught_inspeccion", [
        (1, "inspeccion", "Inspección de Trabajo llama dos veces",
         "Que te pille la Inspección.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_fee", [
        (1, "autonomo", "Autónomo y sin vacaciones", "Paga la cuota de autónomos.", R),
    ])  # fmt: skip

    # 👷 Oficios ------------------------------------------------------------------------
    a += _tiers("jobs", "work_pipes", [
        (1, "tuberia", "Tubería rota", "Rompe algo cavando.", C),
        (25, "barrio_sin_agua", "El barrio sin agua", "Rompe 25 cosas cavando.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_slackers", [
        (20, "cinco_miran", "Cinco miran, uno cava", "Pilla a 20 escaqueados.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_overruns", [
        (20, "listo_uco", "Más listo que la UCO", "Encuentra 20 sobrecostes.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_retiree", [
        (1, "jubilado", "Jubilado inspector", "Explícale la obra a un jubilado.", C),
    ])  # fmt: skip
    a += _tiers("jobs", "work_calima", [
        (1, "calima", "Parada por calima", "Para la obra por calima.", C),
    ])  # fmt: skip
    a += _tiers("jobs", "work_top_obra", [
        (1, "constructor", "Constructor", "Llega a constructor.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_perfect_orders", [
        (100, "una_cana", "Ponme una caña", "Saca 100 comandas perfectas.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_tip", [
        (1, "propina", "Propina de guiri", "Guárdate una propina en el bolsillo.", C),
    ])  # fmt: skip
    a += _tiers("jobs", "work_dine_dash", [
        (1, "sinpa", "Ni un sinpa", "Persigue a una mesa que se iba sin pagar.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_happy_clients", [
        (30, "cliente_razon", "El cliente siempre tiene razón", "Atiende bien 30 marrones.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_top_hosteleria", [
        (1, "chiringuito", "Chiringuito propio", "Llega a dueño del chiringuito.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_perfect_votes", [
        (50, "disciplina", "Disciplina de voto", "Vota 50 veces lo que diga el partido.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_dodged", [
        (20, "mi_libro", "No he venido a hablar de mi libro",
         "Esquiva 20 preguntas en rueda de prensa.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_no_recuerdo", [
        (20, "no_me_consta", "No me consta", "Sal vivo de 20 preguntas en comisión.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_envelope", [
        (1, "sobre", "Sobre en la gabardina", "Acepta un sobre.", R, True),
        (10, "sobres", "Coleccionista de sobres", "Acepta 10 sobres.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_envelope_refused", [
        (1, "honrado", "Honrado (de momento)", "Rechaza un sobre.", C, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_kickback", [
        (1, "fundacion", "Para la fundación", "Acepta una comisión de obra pública.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_cronyism", [
        (1, "enchufe", "Enchufado", "Coloca a un sobrino.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_falcon", [
        (1, "falcon", "Agenda oficial", "Vete a un concierto en el avión oficial.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_caught_uco", [
        (1, "imputado", "Imputado", "Que te pille la UCO.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_pardoned", [
        (1, "indultado", "Indultado", "Que te indulten después de pillarte.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_clinging", [
        (1, "sillon", "Pegado al sillón", "Niégate a dimitir.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_resigned", [
        (1, "dimision", "Dimisión", "Dimite. Algo insólito.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_top_politica", [
        (1, "giratoria", "Puerta giratoria", "Llega a consejero de una eléctrica.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_communion_bizum", [
        (1, "comunion", "La comunión del sobrino, en diferido",
         "Manda un Bizum en vez de ir a la comunión.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_mom_ghosted", [
        (1, "visto", "Visto a las 23:47", "Déjale el visto a tu madre.", C, True),
    ])  # fmt: skip

    # 🏥 Sanidad ------------------------------------------------------------------------
    a.append(Achievement(
        id="guardia_1", name="Primera guardia",
        description="Haz tu primera guardia.", category="sanidad", rarity=C,
        conditions=(("work_guards", 1),), story=GUARD_STORY,
    ))  # fmt: skip
    a += _tiers("sanidad", "work_guards", [
        (10, "guardia_10", "De guardia", "Haz 10 guardias.", R),
        (50, "guardia_50", "Vives en el hospital", "Haz 50 guardias.", E),
        (100, "guardia_100", "La cama de guardias es tuya", "Haz 100 guardias.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_zombie_guard", [
        (1, "treinta_seis", "36 horas despierto", "Haz una guardia con la batería en negativo.",
         E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_off_duty_tries", [
        (1, "saliente", "Saliente, pero con ganas", "Intenta fichar estando saliente.", C, True),
        (10, "adicto_hospital", "Adicto al hospital", "Intenta fichar saliente 10 veces.",
         R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_missed_guards", [
        (1, "tutor", "El tutor te busca", "Sáltate las guardias mínimas del MIR.", C, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_stretcher", [
        (50, "celador_pro", "Celador todoterreno", "Haz 50 traslados perfectos.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_rounds", [
        (50, "ronda_seis", "La ronda de las seis", "Haz 50 rondas perfectas en planta.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_triage", [
        (30, "ojo_clinico", "Ojo clínico", "Acierta 30 triajes.", R),
        (200, "manchester", "Triaje de Manchester", "Acierta 200 triajes.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_mir", [
        (50, "numero_uno", "Número uno del MIR", "Acierta 50 preguntas del MIR.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_google", [
        (30, "doctor_google", "Doctor Google", "Gana 30 consultas.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_cancun", [
        (1, "cancun", "Congreso en Cancún", "Acepta el «congreso» del visitador médico.", R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_cancun_refused", [
        (1, "etica", "Ética de manual", "Rechaza el congreso en Cancún.", C, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_caught_expediente", [
        (1, "expediente", "Expediente disciplinario", "Que te pillen el congreso.", E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_strike", [
        (1, "huelguista", "Huelguista", "Súmate a la huelga de residentes.", R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_scab", [
        (1, "esquirol", "Esquirol", "Trabaja durante la huelga.", R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_waitlist", [
        (1, "lista_infinita", "Lista de espera infinita", "«Optimiza» la lista de espera.",
         R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_aggressive", [
        (5, "seguridad_sala", "Seguridad, a la sala 3", "Sobrevive a 5 familiares alterados.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_clap", [
        (1, "aplausos", "Aplausos de las ocho", "Saluda al vecino que aún aplaude.", C, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_shift_swap", [
        (3, "cambio_turno", "Comodín de la supervisora", "Acepta 3 cambios de turno.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_top_sanidad", [
        (1, "adjunto", "Médico adjunto", "Llega a médico adjunto.", L),
    ])  # fmt: skip

    # 💻 Oficina ------------------------------------------------------------------------
    a += _tiers("oficina", "work_coffee_orders", [
        (50, "becario_cafe", "Becario del café", "Acierta 50 rondas de cafés.", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_bugs", [
        (1, "mi_maquina", "Funciona en mi máquina", "Encuentra tu primer bug.", C),
        (100, "cazabugs", "Cazabugs", "Encuentra 100 bugs.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_reviews", [
        (30, "guardian", "Guardián de producción", "Para 30 cambios peligrosos.", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_meetings", [
        (30, "podia_correo", "Esta reunión podía ser un correo", "Acorta 30 reuniones.", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_pitches", [
        (30, "humo", "Humo de calidad", "Convence a 30 inversores.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_remote", [
        (1, "sofa", "Desde el sofá", "Teletrabaja por primera vez.", C),
        (50, "nomada_salon", "Nómada digital del salón", "Teletrabaja 50 turnos.", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_office", [
        (50, "presentismo", "Presentismo", "Ve a la oficina 50 turnos (que te vean).", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_always_online", [
        (1, "siempre_linea", "Siempre en línea", "Contesta al jefe a las once de la noche.", C),
        (10, "esclavo_slack", "Esclavo de la mensajería", "Contesta fuera de hora 10 veces.",
         E, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_disconnect", [
        (1, "desconexion", "Desconexión digital", "No contestes fuera de hora.", C),
    ])  # fmt: skip
    a += _tiers("oficina", "work_deploy_friday", [
        (1, "viernes", "Viernes de despliegue", "Despliega un viernes por la tarde.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_useless_meeting", [
        (5, "reunionitis", "Reunionitis", "Ve a 5 reuniones inútiles.", C),
    ])  # fmt: skip
    a += _tiers("oficina", "work_meeting_killed", [
        (1, "mata_reuniones", "Matarreuniones", "Cancela una reunión con un correo.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_linkedin", [
        (1, "agradecido", "Agradecido y emocionado de anunciar", "Publica en LinkedIn.", C, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_paintball", [
        (1, "paintball", "Fuego amigo", "Ve al paintball de empresa.", C, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_ai_ninja", [
        (1, "ninja", "Formador de ninjas", "Enséñale el código al ninja de la IA.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_ireland", [
        (1, "dublin", "Farol irlandés", "Usa la oferta de Dublín para pedir aumento.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_options_max", [
        (1_000_000, "rico_papel", "Rico en papel", "Acumula 1.000.000 Y$ en stock options.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("oficina", "work_exit", [
        (1, "unicornio", "Unicornio", "Vive un exit.", L, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_bankrupt", [
        (1, "quiebra", "Quiebra", "Que tu startup quiebre con tus opciones dentro.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_top_oficina", [
        (1, "cto", "CTO", "Llega a CTO de startup.", L),
    ])  # fmt: skip

    # 🇭🇰 Hong Kong ---------------------------------------------------------------------
    a += _tiers("hongkong", "work_abroad", [
        (1, "expat", "Néih hóu, Hong Kong", "Vete a trabajar a Hong Kong.", R),
        (3, "expat_3", "Ida y vuelta", "Vete a Hong Kong 3 veces.", E),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_hk_shifts", [
        (10, "hk_10", "Expat de manual", "Haz 10 turnos desde Hong Kong.", R),
        (100, "hk_100", "Ya no vuelves", "Haz 100 turnos desde Hong Kong.", E),
        (500, "hk_500", "Más de aquí que de allí", "Haz 500 turnos desde Hong Kong.", L),
    ])  # fmt: skip
    a.append(Achievement(
        id="no_residente", name="183 días",
        description="Deja de ser residente fiscal en España.", category="hongkong", rarity=E,
        conditions=(("work_nonresident", 1),), story=NONRESIDENT_STORY,
    ))  # fmt: skip
    a += _tiers("hongkong", "work_7p", [
        (1, "siete_p", "Exento por el 7.p", "Cobra con la exención por trabajos en el extranjero.",
         R),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_double_tax", [
        (1, "doble_imposicion", "Sin doble imposición",
         "Descuenta lo pagado en Hong Kong de tu IRPF.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_hk_tax", [
        (200_000, "hk_tax", "Contribuyente en Hong Kong",
         "Deja 200.000 Y$ entre salaries tax y MPF.", R),
    ], unit="money")  # fmt: skip
    a += _tiers("hongkong", "work_jetlag", [
        (1, "jetlag", "Jet lag", "Ficha desde Hong Kong cuando en Canarias es de madrugada.", C),
        (25, "reloj_roto", "Reloj biológico roto", "25 turnos con jet lag.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_t8", [
        (1, "t8", "Señal 8", "Quédate en casa con el tifón.", R, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_t8_hero", [
        (1, "t8_heroe", "Ni el tifón te para", "Ve a la oficina con señal 8.", E, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_dimsum", [
        (1, "dimsum", "Dim sum con Robuso", "Desayuna dim sum con Robuso en Hong Kong.",
         R, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_lkf", [
        (1, "lkf", "Lan Kwai Fong", "Sal de afterwork y acaba en un karaoke.", C, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_videocall", [
        (1, "videollamada", "Videollamada a las tres", "Contesta a tu madre de madrugada.",
         C, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_proved_residence", [
        (1, "vivo_aqui", "Vivo aquí, lo juro", "Demuestra a Hacienda que vives fuera.", R, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_caught_hacienda", [
        (1, "residencia_ficticia", "Residencia fiscal ficticia",
         "Que Hacienda te regularice por vivir «fuera».", E, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "imv_abroad", [
        (1, "paguita_hk", "Paguita desde Hong Kong", "Intenta cobrar el IMV viviendo fuera.",
         C, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_return", [
        (1, "vuelta", "Vuelta a casa", "Vuelve de Hong Kong.", C),
    ])  # fmt: skip
    # `hongkong`: mirar la hora de allí (ver `hong_kong_clock_stats`).
    a += _tiers("hongkong", "hk_clock", [
        (1, "hkclock_1", "¿Qué hora es allí?", "Mira la hora de Hong Kong.", C),
        (25, "hkclock_25", "Reloj de Robuso", "Mira la hora de Hong Kong 25 veces.", R),
        (100, "hkclock_100", "Doble zona horaria", "Mira la hora de Hong Kong 100 veces.", E),
        (500, "hkclock_500", "Viaje oficial en Falcon",
         "Mira la hora de Hong Kong 500 veces. Con tanto interés, ya habrías ido en Falcon.", L),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_tomorrow", [
        (1, "hk_manana", "Viajero del futuro", "Mira la hora cuando en Hong Kong ya es mañana.",
         C),
        (50, "hk_diferido", "Vives en diferido", "50 veces mirando el mañana de Hong Kong.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_sleeping", [
        (1, "hk_no_despiertes", "No despiertes a Robuso",
         "Mira la hora cuando en Hong Kong son entre las 2:00 y las 6:00.", C),
        (25, "hk_insomne", "Insomne transoceánico",
         "25 veces mirando la hora de madrugada en Hong Kong.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_lunch", [
        (1, "hk_cha_chaan", "Hora del cha chaan teng",
         "Mira la hora cuando Robuso está almorzando (12:00–14:00 allí).", C),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_tour", [
        (1, "hk_gira", "Gira asiática",
         "Mira la hora de Hong Kong de madrugada en Canarias, como en una gira oficial por "
         "China.", R, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_new_year", [
        (1, "hk_ano_nuevo", "Año nuevo por adelantado",
         "Mira la hora en el primer minuto del año en Hong Kong, horas antes que en Canarias.",
         E, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="beckham", name="Ley Beckham",
        description="Vuelve tras 5 «años» fuera y tributa al 24 %.", category="hongkong",
        rarity=L, conditions=(("work_beckham", 1),), secret=True, story=BECKHAM_STORY,
    ))  # fmt: skip
    a += _tiers("hongkong", "work_abroad_days_max", [
        (7, "semana_fuera", "Una semana fuera", "Pasa 7 días seguidos en Hong Kong.", R),
        (35, "cinco_anos", "Cinco «años» fuera", "Pasa 35 días seguidos en Hong Kong.", L),
    ])  # fmt: skip

    # 🪏 Trabajo (más) --------------------------------------------------------------------
    a += _tiers("jobs", "work_jobs_tried", [
        (3, "probador", "Probando oficios", "Trabaja en 3 oficios distintos.", R),
        (5, "curriculum_infinito", "Currículum infinito", "Trabaja en los 5 oficios.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_zero", [
        (1, "cero", "¿Has venido a trabajar?", "Saca un 0 en un turno.", C, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_black_total", [
        (10, "sumergida", "Economía sumergida", "Cobra 10 turnos en B.", R, True),
    ])  # fmt: skip

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


def hong_kong_clock_stats(hong_kong: datetime, canary: datetime) -> dict[str, int]:
    """Contadores que suma mirar la hora de Hong Kong con `hongkong`.

    Args:
        hong_kong: Hora local de Hong Kong en ese momento.
        canary: Hora local de Canarias en ese mismo momento.
    """
    stats = {"hk_clock": 1}
    if hong_kong.date() > canary.date():
        stats["hk_clock_tomorrow"] = 1
    if 2 <= hong_kong.hour < 6:
        stats["hk_clock_sleeping"] = 1
    if 12 <= hong_kong.hour < 14:
        stats["hk_clock_lunch"] = 1
    if is_night(canary.hour):
        stats["hk_clock_tour"] = 1
    if (hong_kong.month, hong_kong.day, hong_kong.hour, hong_kong.minute) == (1, 1, 0, 0):
        stats["hk_clock_new_year"] = 1
    return stats


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


def slots_stats(
    spin: Spin,
    *,
    stake: int,
    payout: int,
    jackpot: int,
    free: bool,
    hot: bool,
    turbo: bool,
    session_spins: int,
    when: datetime,
) -> StatDelta:
    """Contadores de una tirada de tragaperras (sin lo común del casino).

    Args:
        stake: Apuesta de la tirada; en un giro gratis, la que lo activó.
        payout: Lo que ha devuelto la línea (con la máquina caliente incluida).
        jackpot: Lo que se ha llevado del bote (0 si nada).
        free: Si era un giro gratis.
        hot: Si la máquina estaba caliente.
        session_spins: Tiradas en esta máquina, contando esta.
        when: Hora local de la tirada.
    """
    delta = StatDelta(add={"slots_spins": 1}, peak={"slots_session_max": session_spins})
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    paid_stake = 0 if free else stake
    won = payout + jackpot
    net = won - paid_stake
    bump("slots_wins", net > 0)
    bump("slots_ldw", 0 < won < paid_stake)
    if net > 0:
        delta.peak["slots_win_max"] = net
    if jackpot:
        bump("slots_jackpots")
        delta.peak["slots_jackpot_max"] = jackpot
    if spin.kind == SlotKind.THREE and spin.symbol is not None:
        bump(f"slots_three_{spin.symbol}")
    bump("slots_near_miss", spin.near_miss)
    bump("slots_anticipation", spin.anticipation and not turbo)
    bump("slots_scatter_tease", spin.scatters == 2)
    bump("slots_free_triggers", spin.triggers_free_spins)
    bump("slots_free_spins", free)
    bump("slots_hot_spins", hot)
    bump("slots_hot_big", hot and stake > 0 and payout >= 20 * stake)
    bump("slots_wild_wins", payout > 0 and SLOT_WILD in spin.line)
    bump("slots_turbo", turbo)
    bump("slots_pot_fed", amount=0 if free else slots_pot_share(stake))
    bump("slots_night", 3 <= when.hour < 6)
    return delta


def hold_win_stats(
    spin: HoldWinSpin,
    *,
    theme: str,
    stake: int,
    payout: int,
    trigger: HoldWinTrigger | None,
    turbo: bool,
    session_spins: int,
    when: datetime,
) -> StatDelta:
    """Contadores de una tirada base de Botes (sin lo común del casino).

    Args:
        theme: Máquina (de momento, `volcan`).
        payout: Lo cobrado en la tirada (ways más recogida).
        trigger: Bonus que dispara la tirada, si alguno.
        session_spins: Tiradas en esta máquina, contando esta.
        when: Hora local de la tirada.
    """
    del session_spins  # de momento sin logros de sesión larga
    delta = StatDelta(add={"botes_spins": 1, f"botes_spins_{theme}": 1})
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    bump("botes_collects", spin.collectors > 0 and bool(spin.coins))
    bump("botes_double_collect", spin.collectors == 2 and bool(spin.coins))
    bump("botes_near_miss", spin.near_miss)
    bump("botes_ways_5", any(win.reels == 5 for win in spin.wins))
    bump("botes_wild_wins", spin.wild_win)
    bump("botes_chips", amount=sum(spin.chips.values()))
    bump("botes_turbo", turbo)
    bump("botes_night", 3 <= when.hour < 6)
    if payout - stake > 0:
        delta.peak["botes_win_max"] = payout - stake
    if trigger is not None:
        bump("botes_bonuses")
        bump(f"botes_bonus_{trigger.kind}")
    return delta


def hold_win_bonus_stats(
    result: HoldWinBonusResult, steps: Iterable[HoldWinStep], *, amount: int
) -> StatDelta:
    """Contadores de un bonus de Botes terminado.

    El bonus en sí ya se contó al dispararse (`hold_win_stats`); aquí va lo
    que ha pasado dentro.

    Args:
        steps: Tiradas del bonus, en orden.
        amount: Lo cobrado en Y$.
    """
    delta = StatDelta(peak={"botes_mult_max": result.multiplier, "botes_bonus_max": amount})
    for step in steps:
        for landing in step.landings:
            if landing.mystery:
                delta.add["botes_mystery"] = delta.add.get("botes_mystery", 0) + 1
            if landing.cell.kind == "instant":
                delta.add["botes_instant"] = delta.add.get("botes_instant", 0) + 1
        if step.maximized:
            delta.add["botes_maximizer"] = delta.add.get("botes_maximizer", 0) + 1
    for name in result.jackpots:
        delta.add[f"botes_{name}"] = 1
    if result.coins == 19:
        delta.add["botes_almost_grand"] = 1
    return delta


def pachinko_stats(
    volley: PachinkoVolley,
    *,
    stake: int,
    won: int,
    turbo: bool,
    session_volleys: int,
    when: datetime,
) -> StatDelta:
    """Contadores de una tanda de pachinko (sin lo común del casino).

    Args:
        stake: Apuesta de la tanda.
        won: Lo que ha devuelto (apuesta incluida).
        turbo: Si se jugó sin animación (turbo o Ráfaga).
        session_volleys: Tandas en esta máquina, contando esta.
        when: Hora local de la tanda.
    """
    delta = StatDelta(add={"pachinko_volleys": 1}, peak={"pachinko_session_max": session_volleys})
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    net = won - stake
    if net > 0:
        delta.peak["pachinko_win_max"] = net
    bump("pachinko_starts", amount=volley.starts)
    bump("pachinko_reach", amount=sum(1 for d in volley.draws if d.reach))
    bump("pachinko_fake_reach", amount=sum(1 for d in volley.draws if d.reach and not d.atari))
    bump("pachinko_atari", amount=sum(1 for d in volley.draws if d.atari))
    bump("pachinko_rush", amount=sum(1 for d in volley.draws if d.kind == PachinkoKind.RUSH))
    bump("pachinko_super", amount=sum(1 for d in volley.draws if d.kind == PachinkoKind.SUPER))
    if volley.draws:
        delta.peak["pachinko_renchan_max"] = max(d.jackpots for d in volley.draws)
    bump("pachinko_corners", amount=volley.corners)
    bump("pachinko_full_hold", len(volley.draws) >= 4)
    bump("pachinko_wasted", volley.wasted > 0)
    bump("pachinko_blank", volley.total_balls == 0)
    bump("pachinko_turbo", turbo)
    bump("pachinko_night", 3 <= when.hour < 6)
    key = volley.board.key
    bump(f"pachinko_board_{key}")
    bump(f"pachinko_atari_{key}", amount=sum(1 for d in volley.draws if d.atari))
    return delta


def crash_stats(seat: CrashSeat, *, crash_cents: int, players: int, last_out: bool) -> StatDelta:
    """Contadores de un jugador en una ronda de Crash ya pagada (sin lo común del casino).

    Args:
        seat: Su asiento, con el multiplicador al que se retiró (si lo hizo).
        crash_cents: Punto de explosión de la ronda.
        players: Jugadores de la ronda.
        last_out: Si fue el último en retirarse quedando gente dentro.
    """
    delta = StatDelta(add={"crash_rounds": 1}, peak={"crash_party_max": players})
    add = delta.add

    def bump(stat: str, condition: bool = True) -> None:
        if condition:
            add[stat] = add.get(stat, 0) + 1

    cashed = seat.cashed_cents
    if cashed is not None:
        bump("crash_cashouts")
        delta.peak["crash_cashout_max"] = cashed
        if seat.net > 0:
            delta.peak["crash_win_max"] = seat.net
        bump("crash_auto", seat.by_auto)
        # A menos de un 5 % de la explosión: 2,00x con explosión en 2,09x.
        bump("crash_close", crash_cents * 100 <= cashed * 105)
        bump("crash_last_out", last_out)
    else:
        bump("crash_instant", crash_cents == 100)
        bump("crash_greedy", crash_cents >= 1_000)
    bump("crash_moon", crash_cents >= 10_000)
    return delta


def mines_stats(game: MinesGame) -> StatDelta:
    """Contadores de una partida de Minas terminada (sin lo común del casino)."""
    delta = StatDelta(add={"mines_games": 1}, peak={"mines_streak_max": game.gems})
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    bump("mines_gems", amount=game.gems)
    bump("mines_random", amount=game.random_picks)
    if game.status is MinesStatus.CASHED:
        bump("mines_cashouts")
        delta.peak["mines_mult_max"] = game.cents
        if game.net > 0:
            delta.peak["mines_win_max"] = game.net
        bump("mines_24", game.mines == MINES_MAX)
        bump("mines_clear", game.cleared)
        bump("mines_clear_hard", game.cleared and game.mines >= 5)
    elif game.status is MinesStatus.BUSTED:
        bump("mines_booms")
        # La primera casilla es segura: "a la primera" es la que va justo después.
        bump("mines_first_boom", game.gems == 1)
        bump("mines_almost", game.gems >= 1 and game.safe_total - game.gems == 1)
    return delta


def shop_stats(
    *,
    kind: str,
    total: int,
    tax: int,
    discount_pct: int,
    luxury: bool,
    serial: int | None,
    last_unit: bool,
    renewed: bool,
    collection: int,
    queued_boosts: int,
    balance_after: int,
) -> StatDelta:
    """Estadísticas de una compra de la tienda.

    Args:
        kind: Tipo de artículo (`"rol"`, `"xp"` u `"objeto"`).
        total: Lo pagado, IGIC incluido.
        tax: IGIC pagado.
        discount_pct: Rebaja aplicada en %.
        luxury: Si pagó el IGIC de lujo.
        serial: Número de serie de la unidad, si era limitada.
        last_unit: Si se llevó la última unidad.
        renewed: Si alargó un alquiler de rol.
        collection: Coleccionables distintos que tiene tras comprar.
        queued_boosts: Potenciadores suyos sin acabar (en marcha o en cola).
        balance_after: Saldo tras pagar.
    """
    delta = StatDelta(
        add={"shop_purchases": 1, "shop_spent": total, "shop_igic": tax},
        peak={"shop_big_buy_max": total},
    )

    def bump(stat: str, condition: bool = True) -> None:
        if condition:
            delta.add[stat] = delta.add.get(stat, 0) + 1

    bump("shop_roles", kind == "rol")
    bump("shop_renewals", renewed)
    bump("shop_boosts", kind == "xp")
    bump("shop_sale_buys", discount_pct > 0)
    bump("shop_luxury", luxury)
    bump("shop_limited", serial is not None)
    bump("shop_first_serial", serial == 1)
    bump("shop_last_unit", last_unit)
    bump("shop_broke_buy", balance_after == 0)
    if discount_pct:
        delta.peak["shop_discount_max"] = discount_pct
    if kind == "objeto":
        delta.peak["shop_collection_max"] = collection
    if kind == "xp":
        delta.peak["shop_boost_queue_max"] = queued_boosts
    return delta


# -- Bizum ----------------------------------------------------------------------------


def bizum_stats(*, amount: int, sent_today: int, balance_after: int) -> StatDelta:
    """Estadísticas de quien manda un Bizum.

    Args:
        amount: Lo enviado.
        sent_today: Lo enviado hoy, este Bizum incluido.
        balance_after: Saldo de quien envía tras enviar.
    """
    delta = StatDelta(
        add={"bizum_sent_count": 1, "bizum_sent": amount},
        peak={"bizum_max": amount, "bizum_day_max": sent_today},
    )
    if amount == BIZUM_MAX_OPERATION:
        delta.add["bizum_full"] = 1
    if amount == BIZUM_MIN_AMOUNT:
        delta.add["bizum_min"] = 1
    if balance_after == 0:
        delta.add["bizum_broke"] = 1
    return delta


def bizum_received_stats(*, amount: int, balance_after: int) -> StatDelta:
    """Estadísticas de quien recibe un Bizum."""
    return StatDelta(
        add={"bizum_received_count": 1, "bizum_received": amount},
        peak={"balance_max": balance_after},
    )


# -- Loterías --------------------------------------------------------------------------

#: Etiquetas de un boleto premiado que cuentan para logros concretos.
LOTTERY_TAGS = frozenset({"reintegro", "pedrea", "gordo_navidad", "jackpot", "lotto4", "lotto5"})


def lottery_buy_stats(
    *, game: str, units: int, cost: int, owned_in_draw: int, balance_after: int
) -> StatDelta:
    """Estadísticas de una compra de décimos o apuestas.

    Args:
        game: Clave del juego (`bot.services.lottery.GAMES`).
        units: Décimos o apuestas comprados.
        cost: Lo pagado.
        owned_in_draw: Décimos o apuestas del miembro en ese sorteo tras comprar.
        balance_after: Saldo tras pagar.
    """
    delta = StatDelta(
        add={"lottery_bets": units, "lottery_spent": cost},
        peak={"lottery_draw_bets_max": owned_in_draw},
    )
    per_game = {
        "navidad": "lottery_navidad",
        "nino": "lottery_nino",
        "euromillones": "lottery_euro_bets",
    }
    if game in per_game:
        delta.add[per_game[game]] = units
    if balance_after == 0:
        delta.add["lottery_broke_buy"] = 1
    return delta


def lottery_prize_stats(prizes: Iterable[tuple[int, int, frozenset[str]]]) -> StatDelta:
    """Estadísticas de los boletos premiados de un miembro en un sorteo.

    Args:
        prizes: `(premio bruto, gravamen, etiquetas)` de cada boleto premiado.
            Las etiquetas salen de `LOTTERY_TAGS`.
    """
    delta = StatDelta()
    for gross, tax, tags in prizes:
        delta.merge(
            StatDelta(
                add={"lottery_prizes": 1, "lottery_won": gross},
                peak={"lottery_win_max": gross},
            )
        )
        for tag in tags & LOTTERY_TAGS:
            stat = "lottery_reintegros" if tag == "reintegro" else f"lottery_{tag}"
            delta.merge(StatDelta(add={stat: 1}))
        if tax:
            delta.merge(StatDelta(add={"lottery_gravamen": tax, "tax_paid": tax}))
    return delta


def scratch_stats(*, cost: int, prize: int, tax: int, top: bool, balance_after: int) -> StatDelta:
    """Estadísticas de un rasca.

    Args:
        cost: Precio del rasca.
        prize: Premio bruto (0 si no toca).
        tax: Gravamen especial pagado.
        top: Si es el premio más alto de su tabla.
        balance_after: Saldo tras cobrar el premio.
    """
    delta = StatDelta(add={"lottery_scratches": 1, "lottery_spent": cost})
    if balance_after == 0:
        delta.add["lottery_broke_buy"] = 1
    if prize:
        delta.merge(lottery_prize_stats([(prize, tax, frozenset())]))
    if top:
        delta.add["lottery_scratch_top"] = 1
    return delta


# -- Trabajo (`pala`) --------------------------------------------------------------------

#: Contadores de logros por contenido de minijuego: `contenido → estadística`.
_WORK_CONTENT_STATS = {
    "camilla": "work_stretcher",
    "ronda": "work_rounds",
    "triaje": "work_triage",
    "mir": "work_mir",
    "consulta": "work_google",
    "cafes": "work_coffee_orders",
    "bugs": "work_bugs",
    "revisiones": "work_reviews",
    "reuniones": "work_meetings",
    "inversores": "work_pitches",
    "vagos": "work_slackers",
    "sobrecostes": "work_overruns",
    "platos": "work_perfect_orders",
    "comandas": "work_perfect_orders",
    "cocina": "work_perfect_orders",
    "votos": "work_perfect_votes",
    "chiringuito": "work_happy_clients",
    "prensa": "work_dodged",
    "comision": "work_no_recuerdo",
}


def work_stats(outcome: ShiftOutcome, *, birthday: bool = False) -> StatDelta:
    """Estadísticas de un turno de `pala`.

    Args:
        outcome: Lo que ha pasado en el turno.
        birthday: Si el miembro ha fichado el día de su cumpleaños.
    """
    delta = StatDelta(
        add={"work_shifts": 1},
        peak={
            "work_shifts_day_max": outcome.shifts_today,
            "work_streak_max": outcome.streak_days,
            "balance_max": outcome.balance,
        },
    )
    add = delta.add
    if outcome.score >= 100:
        add["work_perfect"] = 1
    if outcome.score >= 90:
        add["work_good"] = 1
    if outcome.night:
        add["work_night"] = 1
    if outcome.sunday:
        add["work_sunday"] = 1
    if birthday:
        add["work_birthday"] = 1
    if outcome.holiday in ("Nochebuena", "Navidad"):
        add["work_christmas"] = 1
    elif outcome.holiday == "Reyes":
        add["work_reyes"] = 1
    elif outcome.holiday == "el Día del Trabajador":
        add["work_mayday"] = 1
    if outcome.kind.value == "negro":
        add["work_black"] = 1
        add["work_past_limit"] = 1
    if outcome.caught:
        add["work_caught_inspeccion"] = 1
    if outcome.battery_before < 0:
        add["work_zombie"] = 1
    if outcome.accident:
        add["work_accidents"] = 1
    if outcome.intervention:
        add["work_family_zero"] = 1
    if outcome.fee:
        add["work_fee"] = 1
    if outcome.game.broken:
        add["work_pipes"] = outcome.game.broken
    content = outcome.position.content.partition(":")[0]
    if (stat := _WORK_CONTENT_STATS.get(content)) is not None:
        hits = outcome.game.perfect_rounds if content in MEMORY_CONTENT else outcome.game.correct
        if hits:
            add[stat] = hits
    slip = outcome.payslip
    if slip is not None:
        add["work_payslips"] = 1
        if slip.irpf:
            add["tax_paid"] = slip.irpf
        if slip.total_taxes:
            add["work_taxes"] = slip.ss_worker + slip.irpf
        delta.peak["work_irpf_pct_max"] = round(slip.rates.irpf * 100)
        if slip.net > 0 and slip.total_taxes >= 0.8 * slip.net:
            add["work_half_salary"] = 1
        if slip.rates.over_max_base:
            add["work_max_base"] = 1
    taxes, net = outcome.week_taxes
    if net > 0 and taxes >= net:
        add["work_partner"] = 1
    if outcome.score == 0:
        add["work_zero"] = 1
    if outcome.kind.value == "negro":
        add["work_black_total"] = 1
    if outcome.kind.value == "guardia":
        add["work_guards"] = 1
        if outcome.battery_before < 0:
            add["work_zombie_guard"] = 1
    if outcome.missed_guards:
        add["work_missed_guards"] = 1
    if outcome.remote:
        add["work_remote"] = 1
    elif outcome.job.key == "oficina" and not outcome.abroad:
        add["work_office"] = 1
    if outcome.abroad:
        add["work_hk_shifts"] = 1
        if outcome.canary_night:
            add["work_jetlag"] = 1
        if outcome.phase == "no_residente":
            add["work_nonresident"] = 1
    foreign = outcome.foreign
    if foreign is not None:
        add["work_hk_tax"] = foreign.foreign
        if foreign.exempt:
            add["work_7p"] = 1
        if foreign.double_tax_relief:
            add["work_double_tax"] = 1
        if foreign.irpf:
            add["tax_paid"] = foreign.irpf
    if outcome.options_total:
        delta.peak["work_options_max"] = outcome.options_total
    if outcome.exit_payout:
        add["work_exit"] = 1
    if outcome.bankrupt:
        add["work_bankrupt"] = 1
    return delta


#: Contenidos de memoria: cuentan las rondas perfectas, no los aciertos sueltos.
MEMORY_CONTENT = frozenset(
    {"platos", "comandas", "cocina", "carteles", "votos", "camilla", "ronda", "cafes"}
)
