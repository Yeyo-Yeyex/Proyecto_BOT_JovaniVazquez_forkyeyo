"""Reglas de los comandos de administración, sin dependencias de red.

Aquí viven las decisiones que no dependen de cómo se invoque el comando:
interpretar duraciones (`10m`, `2h`, `1d`), validar límites y decidir si un
miembro puede actuar sobre otro según la jerarquía de roles de Discord. El
cog de administración (`bot.cogs.admin`) solo traduce esto a llamadas a la
API y a mensajes.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Protocol

# Discord no admite aislamientos (timeouts) de más de 28 días.
MAX_TIMEOUT = timedelta(days=28)
# Límite del modo lento de un canal impuesto por Discord: 6 horas.
MAX_SLOWMODE_SECONDS = 6 * 60 * 60
# Mensajes que `purge` borra como mucho de una vez. Discord borra hasta 100
# por petición; más que eso multiplicaría llamadas a la API desde el NAS.
MAX_PURGE = 100

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_DURATION_RE = re.compile(r"(\d+)\s*([smhd]?)")


class _Role(Protocol):
    position: int


class _Member(Protocol):
    id: int
    top_role: _Role


def parse_duration(text: str) -> timedelta | None:
    """Convierte `30s`, `10m`, `2h`, `1d` o `1h30m` en una duración.

    Un número sin unidad son minutos (`.callar @x 10` = 10 minutos), que es lo
    que se espera casi siempre al aislar a alguien.

    Returns:
        La duración, o `None` si el texto no es válido, es cero o supera el
        máximo de Discord (28 días).
    """
    cleaned = text.strip().lower().replace(" ", "")
    if not cleaned or _DURATION_RE.sub("", cleaned):
        return None
    seconds = sum(
        int(amount) * _UNITS[unit or "m"] for amount, unit in _DURATION_RE.findall(cleaned)
    )
    duration = timedelta(seconds=seconds)
    if duration <= timedelta(0) or duration > MAX_TIMEOUT:
        return None
    return duration


def format_duration(duration: timedelta) -> str:
    """Representa una duración de forma corta: `1d 2h`, `10m`, `45s`."""
    remaining = int(duration.total_seconds())
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        amount, remaining = divmod(remaining, size)
        if amount:
            parts.append(f"{amount}{unit}")
    return " ".join(parts) or "0s"


def parse_user_id(text: str) -> int | None:
    """Extrae un ID de usuario de `123456789` o de una mención `<@123456789>`."""
    match = re.fullmatch(r"<@!?(\d{15,20})>|(\d{15,20})", text.strip())
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


def hierarchy_error(
    actor: _Member,
    target: _Member,
    bot_member: _Member,
    *,
    owner_id: int,
    allow_self: bool = False,
) -> str | None:
    """Motivo por el que `actor` no puede moderar a `target`, o `None` si puede.

    Replica las reglas de Discord para que el usuario reciba una explicación
    clara en vez de un error 403 genérico: nadie modera al dueño del
    servidor, y ni el admin ni el bot pueden actuar sobre alguien con un rol
    igual o superior al suyo (salvo el dueño, que está por encima de todo).

    Args:
        actor: Quien ejecuta el comando.
        target: Miembro sobre el que se actúa.
        bot_member: El propio bot dentro del servidor.
        owner_id: ID del dueño del servidor.
        allow_self: Si el actor puede aplicarse la acción a sí mismo (`nick`).
    """
    if target.id == actor.id and not allow_self:
        return "No puedes hacerte eso a ti mismo."
    if target.id == bot_member.id:
        return "Eso no me lo hago a mí mismo."
    if target.id == owner_id:
        return "Nadie puede hacerle eso al dueño del servidor."
    if (
        actor.id != owner_id
        and target.id != actor.id
        and target.top_role.position >= actor.top_role.position
    ):
        return "Su rol más alto es igual o superior al tuyo."
    if target.top_role.position >= bot_member.top_role.position:
        return "Su rol más alto es igual o superior al mío; súbeme en la lista de roles."
    return None
