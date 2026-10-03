"""Teléfono escacharrado con traductores: el caso de uso de `/babel`.

Un texto en español se traduce en cadena por decenas de idiomas elegidos al
azar y al final vuelve al español. Cada salto pierde un poco de sentido, y
tras 100 saltos el resultado suele ser absurdo: esa es la gracia.

Las traducciones se piden en el momento, no salen de una lista de frases
preparadas: así cada tirada es distinta y funciona con cualquier texto. El
traductor es una dependencia inyectable (:data:`Translator`) para poder
probar la cadena sin red; :class:`GoogleTranslator` es la implementación
real.

Sobre el traductor real: usa el mismo endpoint público y sin clave que la
extensión de Google Translate para navegadores (`client=gtx`). No es una API
documentada ni tiene garantías: Google puede limitar la IP (HTTP 429 o una
redirección a su página de captcha) si se abusa. Por eso el cog solo permite
una cadena a la vez y la cadena se corta en cuanto Google se queja, en lugar
de insistir.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

#: Idioma de partida y de llegada.
HOME_LANGUAGE = "es"

#: Traducciones de una tirada completa: 99 idiomas intermedios + la vuelta al español.
TOTAL_HOPS = 100

#: Fallos seguidos (sin contar el límite de tasa) a partir de los cuales se
#: da la cadena por rota: lo normal es que la red o Google estén caídos.
MAX_CONSECUTIVE_FAILURES = 5

#: Idiomas intermedios posibles, con su nombre en español para el progreso.
#: Códigos de Google Translate; el español queda fuera porque es la llegada.
LANGUAGES: dict[str, str] = {
    "af": "afrikáans", "sq": "albanés", "de": "alemán", "am": "amárico",
    "ar": "árabe", "hy": "armenio", "as": "asamés", "ay": "aimara",
    "az": "azerí", "bm": "bambara", "bn": "bengalí", "bho": "bhojpuri",
    "be": "bielorruso", "my": "birmano", "bs": "bosnio", "bg": "búlgaro",
    "kn": "canarés", "ca": "catalán", "ceb": "cebuano", "cs": "checo",
    "ny": "chichewa", "zh-CN": "chino", "si": "cingalés", "ko": "coreano",
    "co": "corso", "ht": "criollo haitiano", "hr": "croata", "da": "danés",
    "dv": "divehi", "doi": "dogri", "sk": "eslovaco", "sl": "esloveno",
    "eo": "esperanto", "et": "estonio", "eu": "euskera", "ee": "ewe",
    "tl": "filipino", "fi": "finés", "fr": "francés", "fy": "frisón",
    "gd": "gaélico escocés", "cy": "galés", "gl": "gallego",
    "ka": "georgiano", "el": "griego", "gn": "guaraní", "gu": "guyaratí",
    "ha": "hausa", "haw": "hawaiano", "iw": "hebreo", "hi": "hindi",
    "hmn": "hmong", "hu": "húngaro", "ig": "igbo", "ilo": "ilocano",
    "id": "indonesio", "en": "inglés", "ga": "irlandés", "is": "islandés",
    "it": "italiano", "ja": "japonés", "jw": "javanés", "km": "jemer",
    "kk": "kazajo", "rw": "kinyarwanda", "ky": "kirguís", "gom": "konkaní",
    "kri": "krio", "ku": "kurdo", "lo": "lao", "la": "latín", "lv": "letón",
    "ln": "lingala", "lt": "lituano", "lg": "luganda", "lb": "luxemburgués",
    "mk": "macedonio", "mai": "maithili", "ml": "malayalam", "ms": "malayo",
    "mg": "malgache", "mt": "maltés", "mi": "maorí", "mr": "maratí",
    "mni-Mtei": "meitei", "lus": "mizo", "mn": "mongol", "nl": "neerlandés",
    "ne": "nepalí", "no": "noruego", "or": "oriya", "om": "oromo",
    "pa": "panyabí", "ps": "pastún", "fa": "persa", "pl": "polaco",
    "pt": "portugués", "qu": "quechua", "ro": "rumano", "ru": "ruso",
    "sm": "samoano", "sa": "sánscrito", "sr": "serbio", "st": "sesoto",
    "nso": "sesotho del norte", "sn": "shona", "sd": "sindi", "so": "somalí",
    "ckb": "sorani", "sw": "suajili", "sv": "sueco", "su": "sundanés",
    "th": "tailandés", "ta": "tamil", "tt": "tártaro", "tg": "tayiko",
    "te": "telugu", "ti": "tigriña", "ts": "tsonga", "tr": "turco",
    "tk": "turcomano", "ak": "twi", "uk": "ucraniano", "ug": "uigur",
    "ur": "urdu", "uz": "uzbeko", "vi": "vietnamita", "xh": "xhosa",
    "yi": "yidis", "yo": "yoruba", "zu": "zulú",
}  # fmt: skip


class TranslationError(Exception):
    """Una traducción concreta ha fallado (idioma no admitido, red, respuesta rara)."""


class RateLimitedError(TranslationError):
    """Google ha limitado la IP: hay que parar la cadena, no reintentar."""


#: Traduce `text` de `source` a `target` y devuelve el texto traducido.
Translator = Callable[[str, str, str], Awaitable[str]]

#: Aviso de progreso: (traducciones hechas, total, código del idioma actual).
ProgressCallback = Callable[[int, int, str], Awaitable[None]]


@dataclass(frozen=True)
class BabelResult:
    """Resultado de una cadena de traducciones.

    Attributes:
        original: Texto de partida.
        final: Texto de llegada (en español salvo que la cadena se cortase y
            tampoco pudiera volver al español; ver `final_language`).
        final_language: Código del idioma en que está `final`.
        route: Idiomas por los que pasó realmente el texto, de principio a fin
            (empieza y, si todo va bien, termina en `es`).
        stopped_early: `True` si la cadena se cortó antes de completarse.
    """

    original: str
    final: str
    final_language: str
    route: tuple[str, ...]
    stopped_early: bool

    @property
    def hops(self) -> int:
        """Traducciones que se llegaron a hacer."""
        return len(self.route) - 1


def pick_route(rng: random.Random | None = None, hops: int = TOTAL_HOPS) -> list[str]:
    """Elige los idiomas intermedios al azar, sin repetir.

    Devuelve `hops - 1` idiomas: la última traducción es siempre al español.
    """
    rng = rng or random.Random()
    return rng.sample(sorted(LANGUAGES), hops - 1)


async def run_chain(
    text: str,
    route: Sequence[str],
    translate: Translator,
    on_progress: ProgressCallback | None = None,
) -> BabelResult:
    """Traduce `text` por cada idioma de `route` y vuelve al español.

    Un idioma que falla se salta (la cadena sigue desde el último que
    funcionó). Si Google limita la tasa o fallan demasiados seguidos, la
    cadena se corta y se intenta una sola traducción de vuelta al español
    para que el resultado sea legible; si tampoco sale, se devuelve el texto
    tal como esté.

    Args:
        text: Texto en español.
        route: Idiomas intermedios, en orden.
        translate: Traductor que se usará en cada salto.
        on_progress: Se llama tras cada salto con el avance.

    Returns:
        El resultado, con la ruta que se recorrió de verdad.
    """
    current_text = text
    current_lang = HOME_LANGUAGE
    visited = [HOME_LANGUAGE]
    total = len(route) + 1
    consecutive_failures = 0
    stopped_early = False

    for step, target in enumerate(route, start=1):
        try:
            translated = await translate(current_text, current_lang, target)
        except RateLimitedError:
            logger.warning("Babel: Google limitó la tasa en el salto %d (%s)", step, target)
            stopped_early = True
            break
        except TranslationError as error:
            logger.info("Babel: se salta %s (%s)", target, error)
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                stopped_early = True
                break
            continue

        consecutive_failures = 0
        # Una traducción vacía no sirve como siguiente eslabón: se ignora.
        if translated.strip():
            current_text, current_lang = translated, target
            visited.append(target)
        if on_progress is not None:
            await on_progress(step, total, target)

    if current_lang != HOME_LANGUAGE:
        try:
            current_text = await translate(current_text, current_lang, HOME_LANGUAGE)
            current_lang = HOME_LANGUAGE
            visited.append(HOME_LANGUAGE)
        except TranslationError as error:
            logger.warning("Babel: no se pudo volver al español (%s)", error)
            stopped_early = True

    return BabelResult(
        original=text,
        final=current_text,
        final_language=current_lang,
        route=tuple(visited),
        stopped_early=stopped_early,
    )


def parse_google_response(data: Any) -> str:
    """Extrae el texto traducido de la respuesta JSON de `translate_a/single`.

    La respuesta es una lista anidada; el primer elemento contiene un trozo
    por frase, y cada trozo empieza por el texto traducido:
    `[[["Hello world. ", "Hola mundo. ", ...], ["I like cheese.", ...]], ...]`.

    Raises:
        TranslationError: Si la estructura no es la esperada.
    """
    segments = data[0] if isinstance(data, list) and data else None
    if not isinstance(segments, list):
        raise TranslationError("respuesta con un formato inesperado")
    return "".join(
        segment[0]
        for segment in segments
        if isinstance(segment, list) and segment and isinstance(segment[0], str)
    )


class GoogleTranslator:
    """Traductor real sobre el endpoint público de Google Translate.

    Recibe la sesión HTTP desde fuera para que su ciclo de vida lo gestione
    el cog (se cierra al descargarlo).
    """

    URL = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def __call__(self, text: str, source: str, target: str) -> str:
        """Traduce `text` de `source` a `target`.

        Raises:
            RateLimitedError: Si Google responde 429 o redirige (a su captcha).
            TranslationError: Ante cualquier otro fallo de red o de formato.
        """
        params = {"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text}
        try:
            # Sin seguir redirecciones: cuando Google bloquea, redirige a una
            # página de captcha que devolvería HTML con un 200 engañoso.
            async with self._session.get(self.URL, params=params, allow_redirects=False) as resp:
                if resp.status == 429 or 300 <= resp.status < 400:
                    raise RateLimitedError(f"HTTP {resp.status}")
                if resp.status != 200:
                    raise TranslationError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise TranslationError(type(error).__name__) from error
        return parse_google_response(data)


# --- Varios nombres a la vez -------------------------------------------------
#
# Para babelizar varios apodos o canales con un solo comando, los nombres se
# traducen juntos, uno por línea, en una única cadena: 100 peticiones en total
# en vez de 100 por nombre. Google respeta los saltos de línea, pero algún
# idioma puede juntar o partir líneas; esa vuelta se descarta (se trata como un
# idioma que falla) para no mezclar el nombre de uno con el de otro.


def keep_lines(translate: Translator, count: int) -> Translator:
    """Envuelve `translate` para exigir que el resultado conserve `count` líneas.

    Raises (desde el traductor devuelto):
        TranslationError: Si la traducción no tiene exactamente `count` líneas
            con contenido.
    """

    async def wrapped(text: str, source: str, target: str) -> str:
        translated = await translate(text, source, target)
        lines = [line.strip() for line in translated.strip().split("\n")]
        if len(lines) != count or not all(lines):
            raise TranslationError(f"{len(lines)} líneas en vez de {count}")
        return "\n".join(lines)

    return wrapped


# Adorno inicial de un nombre: emojis, separadores como «│» o «・», guiones…
# Todo lo que no sea letra o número hasta la primera palabra.
_DECORATION = re.compile(r"^[\W_]*")


def split_decoration(name: str) -> tuple[str, str]:
    """Separa el adorno inicial del nombre (`"🎮・juegos"` → `("🎮・", "juegos")`).

    El adorno se conserva tal cual y solo se traduce el resto: así un canal
    babelizado sigue en su sitio visual dentro de la lista de canales.
    """
    prefix = _DECORATION.match(name)
    cut = prefix.end() if prefix else 0
    return name[:cut], name[cut:]


def readable_channel_name(core: str) -> str:
    """Convierte `chat-general` en `chat general` para que se traduzca como frase.

    Discord vuelve a poner los guiones al guardar el nombre de un canal de texto.
    """
    return core.replace("-", " ").replace("_", " ").strip()


class ChannelRenameLimiter:
    """Cuenta los renombrados de cada canal para no chocar con Discord.

    Discord solo deja renombrar un mismo canal 2 veces cada 10 minutos; si se
    pide una tercera, discord.py se queda esperando en silencio hasta que pase
    la ventana. Es mejor avisar al usuario antes de empezar.

    Vive en memoria: tras un reinicio se olvida, en el peor caso Discord hace
    esperar al bot, pero no falla.
    """

    WINDOW_SECONDS = 600
    MAX_RENAMES = 2

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._history: dict[int, list[float]] = {}

    def _recent(self, channel_id: int) -> list[float]:
        now = self._clock()
        recent = [t for t in self._history.get(channel_id, []) if now - t < self.WINDOW_SECONDS]
        if recent:
            self._history[channel_id] = recent
        else:
            self._history.pop(channel_id, None)  # no acumular canales viejos
        return recent

    def can_rename(self, channel_id: int) -> bool:
        """Indica si el canal admite otro renombrado sin que Discord haga esperar."""
        return len(self._recent(channel_id)) < self.MAX_RENAMES

    def record(self, channel_id: int) -> None:
        """Apunta un renombrado hecho ahora."""
        self._history[channel_id] = [*self._recent(channel_id), self._clock()]
