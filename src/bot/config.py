"""Lectura y validación de la configuración del bot.

Este es el único módulo que debe leer variables de entorno directamente
(ver Biblia.txt, sección 4 "Configuración"). El resto de la aplicación
recibe una instancia de :class:`BotConfig` ya validada.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Se lanza cuando falta una variable obligatoria o tiene un valor inválido."""


# Niveles de log aceptados por el módulo `logging` de Python.
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Valor por defecto seguro: suficiente para operar sin generar ruido excesivo.
DEFAULT_LOG_LEVEL = "INFO"

# Prefijo de los comandos de texto (".poner", ".ayuda"...). Los comandos de
# aplicación ("/poner") funcionan siempre, con independencia de este valor.
DEFAULT_COMMAND_PREFIX = "."


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Configuración validada necesaria para arrancar el bot.

    Attributes:
        token: Token de autenticación del bot de Discord. Es secreto y
            nunca debe registrarse en logs ni exponerse en mensajes.
        log_level: Nivel de log a usar en toda la aplicación.
        command_prefix: Prefijo de los comandos de texto. Cada comando
            funciona igual con este prefijo (`.poner`) y como comando de
            aplicación (`/poner`).
        casino_channel_ids: Canales donde se permiten los juegos del casino.
            Vacío significa "cualquier canal".
    """

    token: str
    log_level: str = DEFAULT_LOG_LEVEL
    command_prefix: str = DEFAULT_COMMAND_PREFIX
    casino_channel_ids: frozenset[int] = frozenset()


def load_config(env: os._Environ[str] | dict[str, str] | None = None) -> BotConfig:
    """Construye y valida la configuración a partir de variables de entorno.

    Args:
        env: Mapeo de variables de entorno a usar. Por defecto, ``os.environ``.
            Permite inyectar un diccionario en pruebas sin tocar el entorno real.

    Returns:
        Una instancia de :class:`BotConfig` lista para usar.

    Raises:
        ConfigError: Si falta ``DISCORD_TOKEN``, si ``LOG_LEVEL`` no es un
            nivel de log válido o si ``CASINO_CHANNEL_IDS`` no son IDs.
    """
    source = env if env is not None else os.environ

    token = source.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "Falta la variable de entorno DISCORD_TOKEN. "
            "Define el token del bot antes de arrancar (ver .env.example)."
        )

    log_level = source.get("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"LOG_LEVEL inválido: '{log_level}'. "
            f"Valores permitidos: {', '.join(sorted(VALID_LOG_LEVELS))}."
        )

    # Un COMMAND_PREFIX vacío o con espacios (p. ej. una línea `COMMAND_PREFIX=`
    # en el .env) dejaría el bot sin comandos de texto; se usa el punto.
    command_prefix = source.get("COMMAND_PREFIX", "").strip() or DEFAULT_COMMAND_PREFIX

    casino_channel_ids = _parse_channel_ids(
        "CASINO_CHANNEL_IDS", source.get("CASINO_CHANNEL_IDS", "")
    )

    return BotConfig(
        token=token,
        log_level=log_level,
        command_prefix=command_prefix,
        casino_channel_ids=casino_channel_ids,
    )


def _parse_channel_ids(name: str, raw: str) -> frozenset[int]:
    """Lee una lista de IDs de canal separados por comas.

    Raises:
        ConfigError: Si algún elemento no es un ID numérico de Discord.
    """
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ConfigError(
                f"{name} inválido: '{part}' no es un ID de canal. "
                "Usa IDs numéricos separados por comas."
            )
        ids.add(int(part))
    return frozenset(ids)
