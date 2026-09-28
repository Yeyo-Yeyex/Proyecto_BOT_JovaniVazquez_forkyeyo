"""Punto de entrada ejecutable del bot: `python -m bot`.

Este módulo se mantiene mínimo a propósito: solo carga la configuración,
configura el logging y delega el arranque en `bot.app`.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv

from bot.app import start_bot
from bot.config import ConfigError, load_config
from bot.logging_config import configure_logging

logger = logging.getLogger(__name__)


def main() -> int:
    """Arranca el bot y devuelve el código de salida del proceso.

    Returns:
        0 si el bot se detuvo de forma controlada, 1 si la configuración
        es inválida o si ocurrió un error irrecuperable al arrancar.
    """
    # No falla si no existe .env: en producción las variables vienen del
    # entorno de despliegue, no de un archivo local.
    load_dotenv()

    try:
        config = load_config()
    except ConfigError as error:
        # La configuración aún no está validada aquí, así que se usa un
        # logging básico para poder informar el problema igualmente.
        logging.basicConfig(level="ERROR")
        logger.error("Error de configuración: %s", error)
        return 1

    configure_logging(config.log_level)

    try:
        asyncio.run(start_bot(config.token, command_prefix=config.command_prefix))
    except KeyboardInterrupt:
        logger.info("Apagado solicitado por el usuario (Ctrl+C).")
    except Exception:
        logger.exception("El bot se detuvo por un error no controlado.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
