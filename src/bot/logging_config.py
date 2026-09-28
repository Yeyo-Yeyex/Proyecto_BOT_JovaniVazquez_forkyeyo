"""Configuración centralizada de logging para toda la aplicación.

Centraliza el formato y el nivel de logs (ver Biblia.txt, sección 7
"Errores, logs y observabilidad"). No se usa `print` como sistema de logs.
"""

from __future__ import annotations

import logging
import sys

# Formato compacto pero con contexto suficiente para depurar en producción:
# fecha, nivel, nombre del logger (módulo) y mensaje.
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str) -> None:
    """Configura el logging raíz de la aplicación.

    Debe llamarse una única vez al arrancar el proceso, antes de crear
    el cliente de Discord, para que todos los loggers hijos hereden la
    configuración.

    Args:
        level: Nivel de log válido (por ejemplo "INFO" o "DEBUG").
    """
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
    )

    # La librería discord.py es muy verbosa en DEBUG; se deja en INFO como
    # mínimo salvo que se pida explícitamente más detalle.
    if level != "DEBUG":
        logging.getLogger("discord").setLevel(logging.INFO)
