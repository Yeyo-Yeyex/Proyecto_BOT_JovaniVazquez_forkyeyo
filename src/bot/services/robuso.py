"""Robuso y su categoría secreta de logros (🎸 Robuso): quién es y qué cuenta.

Robuso es un miembro del servidor (`ROBUSO_ID`) y una broma interna del grupo: sus
fotos de Hong Kong, sus quejas de los compañeros del curro, su música alternativa, sus
juegos «tipo Robuso» (indies, pixelados o que solo juega él), el bajo, decir que viene a
jugar y no venir, y que el resto son sus «amigos de Discord». La categoría tiene logros
que solo puede conseguir él (su descripción no lo dice, para que quede la duda) y otros
para los demás por tratar con él. No sale en el menú de logros hasta conseguir uno de los suyos
(`Category.secret` en `bot.services.achievements`).

Aquí está lo puro: qué cuenta de un mensaje, de un juego y de lo que ya suman otros
logros (`echoes`). El cog `bot.cogs.robuso` mira los eventos de Discord, recuerda en
memoria lo que dura un rato (la promesa de venir, la despedida, la vuelta) y suma con
`achievements.note`. Del texto solo se miran propiedades y se olvida, como en el resto
de logros.

Robuso no vive siempre en Hong Kong: va unas semanas y vuelve a Madrid, y quizá más
adelante se vaya más tiempo. Lo de Hong Kong solo cuenta mientras está allí (`in_hk`,
que cambia un administrador con `robuso`), para no gastar la broma.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.services.hong_kong import HONG_KONG

#: Id de Discord de Robuso.
ROBUSO_ID = 430508168385134622
#: Hora de Madrid, donde vive cuando no está en Hong Kong.
MADRID = ZoneInfo("Europe/Madrid")

DAY_SECONDS = 86_400
#: Días sin escribir (o sin entrar en llamada) para que volver cuente como una vuelta.
RETURN_DAYS = 7
#: Lo que tiene para entrar en llamada después de decir que va a jugar.
PROMISE_SECONDS = 2 * 60 * 60
#: Lo que puede pasar como mucho entre despedirse y volver a escribir.
GOODBYE_SECONDS = 15 * 60
#: Tras una vuelta, lo que tiene el resto para darle la bienvenida en ese canal.
WELCOME_SECONDS = 60 * 60
#: Segundos en llamada por debajo de los cuales salir cuenta como asomarse.
PEEK_SECONDS = 60
#: Caracteres de un mensaje suyo para que cuente como texto largo.
ESSAY_CHARS = 500

#: Lo que se cuenta como mucho una vez al día por persona: un logro de 40 pide 40 días
#: hablando del tema y no 40 mensajes seguidos. Lo que se puede forzar a propósito no es
#: un mérito (Biblia, «Rarezas»).
DAILY = frozenset(
    {
        "robuso_coworkers", "robuso_work_rant", "robuso_altmusic", "robuso_bass",
        "robuso_indie_talk", "robuso_roleplay", "robuso_essays", "robuso_hk_photos",
        "robuso_hk_talk", "robuso_hk_night", "robuso_hk_office", "robuso_madrid_night",
        "robuso_deleted", "robuso_peeks", "robuso_status", "robuso_solo_game",
        "robuso_indie_play", "robuso_flakes", "robuso_kept_word", "robuso_pings",
        "robuso_named", "robuso_wakeups", "robuso_waited", "robuso_coop", "robuso_friends_said",
        "robuso_fake_goodbye",
    }
)  # fmt: skip

#: Estadísticas de siempre que, si son de Robuso, suman además una suya: +1 por cada
#: vez que llegan (un mensaje, un minuto de voz, una jugada, una canción…).
ECHOES: dict[str, str] = {
    "messages": "robuso_msgs",
    "voice_minutes": "robuso_vc_minutes",
    "casino_wagered": "robuso_casino",
    "music_queued": "robuso_dj",
    "hk_clock": "robuso_clock",
    "shop_used_robuso": "robuso_self_call",
    "work_shifts": "robuso_shifts",
    "bizum_received_count": "robuso_bizum_in",
    "laugh_reacts_received": "robuso_funny",
}

#: Quita tildes y diéresis pero deja la ñ, como los demás detectores de logros.
_ACCENTS = str.maketrans("áéíóúüàèìòùâêîôû", "aeiouuaeiouaeiou")

# Los patrones van sobre el texto en minúsculas y sin tildes. «india» a secas no cuenta
# (es el país): la broma son sus compañeros de trabajo, no los habitantes de ningún sitio.
_COWORKERS = re.compile(
    r"\bindi(?:o|os|as)\b|\bcompañer[oa]s? (?:del? (?:curro|trabajo)|de la oficina)\b"
)
_WORK_RANT = re.compile(
    r"\bmi jef[ea]\b|\b(?:el|del|al) curro\b|\bhoras extras?\b|\breuniones?\b|\bdeadlines?\b"
    r"|\bburn ?out\b|\bodio (?:el|mi) (?:trabajo|curro)\b"
)
_ALT_MUSIC = re.compile(
    r"\bshoegaze\b|\bpost[- ]?(?:punk|rock|hardcore)\b|\bmath[- ]?rock\b|\bmidwest emo\b|\bemo\b"
    r"|\bindie\b(?! ?(?:games?|juegos?))|\bgrunge\b|\bdream ?pop\b|\blo[- ]?fi\b|\bslowcore\b"
    r"|\bkrautrock\b|\bvinilos?\b|\b(?:musica|rock|pop) alternativ[oa]\b|\bpitchfork\b"
    r"|\brate ?your ?music\b|\bradiohead\b|\bpixies\b|\bsonic youth\b|\bjoy division\b"
    r"|\bthe smiths\b|\bthe cure\b|\bmy bloody valentine\b|\bslowdive\b|\bdeftones\b|\bmitski\b"
    r"|\bphoebe bridgers\b|\bbig thief\b|\bcar seat headrest\b|\bblack country\b|\bblack midi\b"
    r"|\bking gizzard\b|\bidles\b|\bfontaines\b|\bmac demarco\b|\btame impala\b|\bthe strokes\b"
    r"|\barctic monkeys\b|\bpavement\b|\bmodest mouse\b|\bneutral milk hotel\b|\bmolchat doma\b"
)
_MUSIC_LINK = re.compile(
    r"https?://(?:open\.spotify\.com|(?:[\w-]+\.)?bandcamp\.com|soundcloud\.com"
    r"|music\.apple\.com|music\.youtube\.com)/"
)
#: «bajo» a secas es una palabra normal («bajo la mesa», «voy a bajar»): solo cuenta tocarlo.
_BASS = re.compile(
    r"\bbajistas?\b"
    r"|\b(?:tocar|toco|tocando|tocaba|tocado|ensayar|afinar|afinando) (?:el |mi )?bajo\b"
    r"|\bmi bajo\b|\bel bajo (?:electrico|acustico|de cinco|fretless)\b|\bslap\b|\bpedalera\b"
    r"|\bampli(?:ficador)?\b|\blocal de ensayo\b|\bfender\b|\bsquier\b|\bibanez\b|\bjazz bass\b"
    r"|\bprecision bass\b|\bpua\b|\bmetronomo\b|\b(?:un|el|del) bolo\b|\bmi (?:banda|grupo)\b"
    r"|\btocar (?:musica|en directo)\b|\bjam session\b"
)
#: Juegos «tipo Robuso»: indies, pixelados y poco conocidos. Sin «celeste» ni «gris»,
#: que son colores.
_INDIE_GAMES = re.compile(
    r"\b(?:juegos?|games?) indies?\b|\bindie games?\b|\bpixel ?art\b|\bpixelad[oa]s?\b"
    r"|\b(?:8|16) ?bits\b|\brogue ?(?:like|lite)s?\b|\bmetroidvanias?\b|\bsouls ?likes?\b"
    r"|\bitch\.?io\b|\bgame ?jam\b|\bearly access\b|\bacceso anticipado\b|\bhollow knight\b"
    r"|\bsilksong\b|\bundertale\b|\bdeltarune\b|\bstardew\b|\bhades\b|\bbalatro\b|\bdead cells\b"
    r"|\bterraria\b|\bouter wilds\b|\bdisco elysium\b|\binscryption\b|\bcult of the lamb\b"
    r"|\bdave the diver\b|\bvampire survivors\b|\bslay the spire\b|\brisk of rain\b"
    r"|\benter the gungeon\b|\bbinding of isaac\b|\bomori\b|\bhyper light\b|\bkatana zero\b"
    r"|\bcuphead\b|\bshovel knight\b|\bloop hero\b|\bnoita\b|\brain world\b|\banimal well\b"
    r"|\bpizza tower\b|\bultrakill\b|\blethal company\b|\bdwarf fortress\b|\bfactorio\b"
    r"|\brimworld\b|\bbaba is you\b|\bhotline miami\b|\bblasphemous\b|\bsea of stars\b"
    r"|\bbuckshot roulette\b|\bsignalis\b|\bdome keeper\b|\btunic\b|\bcassette beasts\b"
)
_GAME_LINK = re.compile(r"https?://(?:store\.steampowered\.com/app|[\w-]+\.itch\.io)/?")
#: Juegos que conoce todo el mundo: jugar a otro cuenta como «tipo Robuso».
_MAINSTREAM = re.compile(
    r"\b(?:minecraft|league of legends|fortnite|valorant|counter[- ]strike|grand theft auto|gta"
    r"|ea sports fc|fifa|rocket league|among us|call of duty|warzone|roblox|the sims|los sims"
    r"|apex legends|overwatch|dota|world of warcraft|genshin|elden ring|pubg|rainbow six"
    r"|destiny|the witcher|cyberpunk|red dead|marvel rivals|helldivers|baldur's gate"
    r"|hearthstone|teamfight tactics|clash royale|brawl stars|pokemon|zelda|mario|fall guys"
    r"|dead by daylight|sea of thieves|palworld|phasmophobia|rust|the finals|deadlock|diablo"
    r"|god of war|spider-man|assassin's creed|battlefield|steam)\b"
)
_HK_TALK = re.compile(
    r"\bdim sum\b|\bcha chaan teng\b|\bkowloon\b|\btsim sha tsui\b|\bmong kok\b|\bwan chai\b"
    r"|\bcauseway bay\b|\blan kwai fong\b|\bvictoria (?:harbour|peak)\b|\bstar ferry\b"
    r"|\boctopus\b|\bmtr\b|\blantau\b|\bmaca[ou]\b|\btifon\b|\btyphoon\b|\bsiu mai\b|\bhar gow\b"
    r"|\bcantones\b|\bhong kong\b|\bhk\b"
)
_GOODBYE = re.compile(
    r"\bme voy\b|\bme piro\b|\bme las piro\b|\bme largo\b|\badios\b|\bchao+\b|\bbye\b"
    r"|\bhasta (?:luego|mañana|otra)\b|\ba (?:dormir|mimir)\b|\bbuenas noches\b"
    r"|\bme desconecto\b|\bnos vemos\b"
)
_PLAY_PROMISE = re.compile(
    r"\b(?:ahora|luego|ya|en un rato|en nada|esta noche|despues|mas tarde|en \d+|en cinco"
    r"|en diez) (?:me conecto|entro|me uno|voy|juego|jugamos)\b"
    r"|\bme (?:conecto|uno|apunto)\b|\bjugamos\b|\bvoy a jugar\b"
    r"|\b(?:nos )?echamos (?:una|unas|un)\b"
)
#: Acotaciones de teatro en cursiva: «*suspira*», «*se va dramáticamente*».
_ROLEPLAY = re.compile(r"^\*[^*\s][^*]{0,80}\*$")
_FRIENDS = re.compile(r"\bamig[oa]s? de discord\b")
_NAME = re.compile(r"\brobuso\b")


def _plain(text: str) -> str:
    """Minúsculas y sin tildes, para que los patrones no dependan de cómo se escriba."""
    return text.lower().translate(_ACCENTS)


def _hits(found: Mapping[str, object]) -> dict[str, int]:
    return {stat: 1 for stat, hit in found.items() if hit}


def promises_to_play(content: str) -> bool:
    """Si el mensaje dice que va a jugar o a conectarse («ahora me conecto», «¿jugamos?»)."""
    return bool(_PLAY_PROMISE.search(_plain(content)))


def says_goodbye(content: str) -> bool:
    """Si el mensaje es una despedida («me voy», «a mimir», «buenas noches»…)."""
    return bool(_GOODBYE.search(_plain(content)))


def names_robuso(content: str) -> bool:
    """Si el mensaje dice «Robuso» por escrito (una mención `<@id>` no lo dice)."""
    return bool(_NAME.search(_plain(content)))


def chat_stats(content: str) -> dict[str, int]:
    """Lo que suma a cualquiera, Robuso incluido: decir «amigos de Discord»."""
    return _hits({"robuso_friends_said": _FRIENDS.search(_plain(content))})


def author_stats(content: str, *, now: float, in_hk: bool, images: int) -> dict[str, int]:
    """Lo que suma a Robuso un mensaje suyo.

    El mensaje en sí ya lo cuenta el eco de `messages` (`echoes`); aquí van los temas,
    las fotos y las horas.

    Args:
        content: Texto; se mira y se olvida.
        now: Momento del mensaje (epoch).
        in_hk: Si está en Hong Kong: lo de allí solo cuenta entonces, y la madrugada
            de Madrid, cuando no.
        images: Imágenes adjuntas.
    """
    text = content.strip()
    plain = _plain(text)
    found: dict[str, object] = {
        "robuso_coworkers": _COWORKERS.search(plain),
        "robuso_work_rant": _WORK_RANT.search(plain),
        "robuso_altmusic": _ALT_MUSIC.search(plain) or _MUSIC_LINK.search(plain),
        "robuso_bass": _BASS.search(plain),
        "robuso_indie_talk": _INDIE_GAMES.search(plain) or _GAME_LINK.search(plain),
        "robuso_roleplay": _ROLEPLAY.match(text) and any(ch.isalpha() for ch in text),
        "robuso_essays": len(text) >= ESSAY_CHARS,
    }
    if in_hk:
        hk = datetime.fromtimestamp(now, HONG_KONG)
        found["robuso_hk_photos"] = images > 0
        found["robuso_hk_talk"] = _HK_TALK.search(plain)
        found["robuso_hk_night"] = 2 <= hk.hour < 6
        found["robuso_hk_office"] = hk.weekday() < 5 and 10 <= hk.hour < 18
        found["robuso_hk_new_year"] = (hk.month, hk.day, hk.hour) == (1, 1, 0)
    else:
        found["robuso_madrid_night"] = 2 <= datetime.fromtimestamp(now, MADRID).hour < 6
    return _hits(found) | chat_stats(text)


def fan_stats(
    content: str,
    *,
    now: float,
    pinged: bool,
    replied: bool,
    reply_images: bool,
    laughed: bool,
    in_hk: bool,
    silent_for: float | None,
) -> dict[str, int]:
    """Lo que suma a otro miembro un mensaje que va para Robuso o habla de él.

    Args:
        content: Texto; se mira y se olvida.
        now: Momento del mensaje (epoch).
        pinged: Si menciona a Robuso (sin contar la mención de responderle).
        replied: Si responde a un mensaje de Robuso.
        reply_images: Si ese mensaje de Robuso llevaba imágenes.
        laughed: Si el mensaje es una risa (`analyze_laugh`).
        in_hk: Si Robuso está en Hong Kong.
        silent_for: Segundos desde el último mensaje de Robuso; `None` si no se sabe.
    """
    named = names_robuso(content)
    hk_hour = datetime.fromtimestamp(now, HONG_KONG).hour
    missing = silent_for is not None and silent_for >= RETURN_DAYS * DAY_SECONDS
    found = {
        "robuso_pings": pinged,
        "robuso_named": named,
        "robuso_replies": replied,
        "robuso_laughs": replied and laughed,
        "robuso_envy": replied and reply_images and in_hk,
        "robuso_wakeups": pinged and in_hk and hk_hour < 7,
        "robuso_missed": (pinged or named) and missing,
    }
    return _hits(found) | chat_stats(content)


def game_stats(name: str, *, others_playing: bool) -> dict[str, int]:
    """Lo que suma a Robuso empezar a jugar a `name` (su actividad de Discord).

    Args:
        others_playing: Si alguien más del servidor está jugando a eso ahora mismo.
    """
    plain = _plain(name)
    return _hits(
        {
            "robuso_indie_play": _INDIE_GAMES.search(plain),
            "robuso_solo_game": not others_playing and not _MAINSTREAM.search(plain),
        }
    )


def echoes(add: Mapping[str, int]) -> dict[str, int]:
    """Lo que suma a Robuso lo que suma por lo de siempre (`ECHOES`): +1 por estadística."""
    return {mine: 1 for stat, mine in ECHOES.items() if add.get(stat, 0) > 0}
