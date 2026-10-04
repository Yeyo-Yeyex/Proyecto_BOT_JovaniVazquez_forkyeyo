"""Reglas de la bienvenida: el GIF configurable, las frases y quién puede saludar.

Lógica pura, sin Discord ni base de datos. El cog `bot.cogs.welcome` la usa
para montar el mensaje de entrada, y el comando de administración `bienv`
para validar el GIF antes de guardarlo.

El GIF admite dos tipos de enlace:

- **Directo** (`https://media.tenor.com/....gif`, `https://i.giphy.com/....gif`,
  cualquier `.gif`, `.webp`, `.png` o `.jpg`): va como imagen del embed y
  queda limpio.
- **Página** de Tenor o Giphy (`https://tenor.com/view/...`): es lo que da
  Discord con "Copiar enlace" en el selector de GIF. No se puede meter en
  un embed, así que se manda como enlace suelto y Discord lo despliega.

No se aceptan adjuntos de Discord (`cdn.discordapp.com`): sus enlaces
caducan a las 24 horas y la bienvenida se quedaría sin GIF.

Dinero (yapdollars). Saludar con el botón 👋 da un regalo simbólico, como
felicitar un cumpleaños: `GREETER_GIFT` a quien saluda y `WELCOMED_GIFT` al
recién llegado. Tratamiento fiscal: IRPF con retención. El dinero lo pone el
bot, no otro miembro, así que no es una donación entre particulares sujeta
al Impuesto sobre Sucesiones y Donaciones (art. 3.1.b de la Ley 29/1987),
sino una ganancia patrimonial (art. 33.1 de la Ley 35/2006 del IRPF). Se
cobra con `EconomyService.pay_income`; con cantidades tan pequeñas casi
nunca se retiene nada, salvo a quien ya pasa del mínimo.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

#: Largo máximo de un enlace de GIF. Discord admite 2.048 en la imagen de un
#: embed; con 512 sobra para Tenor y Giphy y se evita guardar basura.
MAX_GIF_URL_LENGTH = 512

#: Extensiones que Discord pinta como imagen dentro de un embed.
DIRECT_EXTENSIONS = (".gif", ".webp", ".png", ".jpg", ".jpeg")

#: Webs cuyas páginas de GIF despliega Discord solo con pegar el enlace.
PAGE_HOSTS = ("tenor.com", "giphy.com")

#: Dominios de adjuntos de Discord, que caducan (ver docstring del módulo).
EXPIRING_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")

#: Tiempo que dura el botón de dar la bienvenida.
GREETING_WINDOW_SECONDS = 24 * 3600

#: Saludar en este margen tras la entrada cuenta para "Más rápido que Hacienda".
FAST_GREETING_SECONDS = 60

#: Regalo a quien da la bienvenida: dos tiradas de ruleta a la apuesta por
#: defecto del casino (100 Y$). Simbólico a propósito, para que nadie entre y
#: salga del servidor con cuentas secundarias para cobrarlo.
GREETER_GIFT = 200
#: Lo que recibe el recién llegado por cada persona que le saluda: una tirada.
WELCOMED_GIFT = 100

#: Frases de reserva si el archivo de frases falta o está vacío.
FALLBACK_WELCOMES = ("{usuario} acaba de llegar. Portaos bien, que es nuevo.",)
FALLBACK_RETURNS = ("{usuario} ha vuelto. Nadie se va de aquí del todo.",)

#: Separador de secciones en `bienvenidas.txt`.
RETURNS_SECTION = "[vuelta]"


class GifKind(Enum):
    """Cómo se manda el GIF en el mensaje de bienvenida."""

    DIRECT = "direct"
    PAGE = "page"


class GifError(ValueError):
    """El enlace no sirve como GIF de bienvenida. El mensaje es para el usuario."""


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def classify_gif(url: str) -> GifKind:
    """Valida un enlace de GIF y dice cómo hay que mandarlo.

    Raises:
        GifError: Si no es `https`, es demasiado largo, es un adjunto de
            Discord o no es ni imagen directa ni página de Tenor/Giphy.
    """
    url = url.strip()
    if len(url) > MAX_GIF_URL_LENGTH:
        raise GifError(f"El enlace es demasiado largo (máximo {MAX_GIF_URL_LENGTH} caracteres).")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise GifError("Pásame un enlace que empiece por `https://`.")
    if _host_matches(host, EXPIRING_HOSTS):
        raise GifError("Los adjuntos de Discord caducan en un día. Usa un enlace de Tenor o Giphy.")
    if parts.path.lower().endswith(DIRECT_EXTENSIONS):
        return GifKind.DIRECT
    if _host_matches(host, PAGE_HOSTS):
        return GifKind.PAGE
    raise GifError(
        "Eso no parece un GIF. Vale un enlace de Tenor o Giphy, o uno que acabe en `.gif`."
    )


@dataclass(frozen=True, slots=True)
class WelcomePhrases:
    """Frases para quien entra por primera vez y para quien vuelve."""

    welcomes: tuple[str, ...]
    returns: tuple[str, ...]


def parse_phrases(text: str) -> WelcomePhrases:
    """Lee el formato de `bienvenidas.txt`.

    Una frase por línea. Las vacías y las que empiezan por `#` se ignoran.
    Lo que va tras una línea `[vuelta]` es para quien ya había estado en el
    servidor. Si una sección se queda vacía, se usa su reserva.
    """
    welcomes: list[str] = []
    returns: list[str] = []
    target = welcomes
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower() == RETURNS_SECTION:
            target = returns
            continue
        target.append(line)
    return WelcomePhrases(
        welcomes=tuple(welcomes) or FALLBACK_WELCOMES,
        returns=tuple(returns) or FALLBACK_RETURNS,
    )


def render_phrase(template: str, mention: str) -> str:
    """Sustituye `{usuario}` por la mención; si la frase no lo usa, va delante.

    Se usa `replace` y no `str.format` porque el archivo lo edita cualquiera:
    unas llaves sueltas no deben tumbar la bienvenida.
    """
    if "{usuario}" in template:
        return template.replace("{usuario}", mention)
    return f"{mention} {template}"


def pick_phrase(
    phrases: WelcomePhrases, *, returning: bool, rng: random.Random | None = None
) -> str:
    """Elige al azar una frase de la sección que toca."""
    pool = phrases.returns if returning else phrases.welcomes
    return (rng or random).choice(pool)


class GreetCheck(Enum):
    """Si un miembro puede dar la bienvenida a otro ahora."""

    OK = "ok"
    SELF = "self"
    EXPIRED = "expired"


def check_greeting(
    newcomer_id: int, greeter_id: int, *, joined_at: float, now: float
) -> GreetCheck:
    """Reglas del botón 👋: no a uno mismo y solo durante el primer día.

    La repetición (saludar dos veces a la misma persona) la impide la clave
    primaria en la base de datos, no esta función.
    """
    if newcomer_id == greeter_id:
        return GreetCheck.SELF
    if now - joined_at > GREETING_WINDOW_SECONDS:
        return GreetCheck.EXPIRED
    return GreetCheck.OK


def is_fast_greeting(*, joined_at: float, now: float) -> bool:
    """Si el saludo llega en el primer minuto tras la entrada."""
    return 0 <= now - joined_at <= FAST_GREETING_SECONDS
