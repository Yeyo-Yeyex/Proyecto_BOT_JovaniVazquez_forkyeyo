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

# Prefijo usado únicamente como respaldo si los comandos de aplicación
# (slash commands) no están disponibles en algún contexto de desarrollo.
DEFAULT_COMMAND_PREFIX = "."


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Configuración validada necesaria para arrancar el bot.

    Attributes:
        token: Token de autenticación del bot de Discord. Es secreto y
            nunca debe registrarse en logs ni exponerse en mensajes.
        log_level: Nivel de log a usar en toda la aplicación.
        command_prefix: Prefijo de comandos de texto, usado solo como
            respaldo de desarrollo; la interfaz principal son los
            comandos de aplicación (slash commands).
    """

    token: str
    log_level: str = DEFAULT_LOG_LEVEL
    command_prefix: str = DEFAULT_COMMAND_PREFIX


def load_config(env: os._Environ[str] | dict[str, str] | None = None) -> BotConfig:
    """Construye y valida la configuración a partir de variables de entorno.

    Args:
        env: Mapeo de variables de entorno a usar. Por defecto, ``os.environ``.
            Permite inyectar un diccionario en pruebas sin tocar el entorno real.

    Returns:
        Una instancia de :class:`BotConfig` lista para usar.

    Raises:
        ConfigError: Si falta ``DISCORD_TOKEN`` o si ``LOG_LEVEL`` no es un
            nivel de log válido.
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

    command_prefix = source.get("COMMAND_PREFIX", DEFAULT_COMMAND_PREFIX)

    return BotConfig(token=token, log_level=log_level, command_prefix=command_prefix)
