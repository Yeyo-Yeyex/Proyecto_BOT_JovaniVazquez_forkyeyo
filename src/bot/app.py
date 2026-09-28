"""Construcción y ciclo de vida del cliente de Discord.

Este módulo define cómo se crea el bot, qué extensiones (cogs) se cargan
y cómo se arranca y se cierra de forma ordenada. No lee variables de
entorno directamente: recibe una configuración ya validada
(ver `bot.config`).
"""

from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord.ext import commands

from bot.repositories.message_stats import MessageStatsRepository

logger = logging.getLogger(__name__)

# Cogs que se cargan al arrancar. Añadir aquí el nuevo módulo cuando se
# incorpore una funcionalidad nueva agrupada por dominio.
INITIAL_EXTENSIONS: tuple[str, ...] = (
    "bot.cogs.general",
    "bot.cogs.message_stats",
)


def build_intents() -> discord.Intents:
    """Crea los intents mínimos necesarios para la funcionalidad actual.

    Solo se solicitan los intents por defecto (no privilegiados). Si una
    funcionalidad futura necesita leer el contenido de los mensajes o el
    listado de miembros, deberá habilitarse aquí y también en el portal
    de desarrolladores de Discord (ver Biblia.txt, sección 6 "Seguridad
    y permisos").
    """
    return discord.Intents.default()


class BotClient(commands.Bot):
    """Cliente principal del bot.

    Encapsula la carga de extensiones y expone un punto único de
    arranque (`start_bot`) para mantener `__main__.py` mínimo.
    """

    def __init__(self, *, command_prefix: str, database_path: Path) -> None:
        self.message_stats = MessageStatsRepository(database_path)
        super().__init__(
            command_prefix=command_prefix,
            intents=build_intents(),
            # El texto de ayuda por defecto de discord.py no está en
            # español ni pensado para slash commands; se desactiva.
            help_command=None,
        )

    async def setup_hook(self) -> None:
        """Carga las extensiones y sincroniza los comandos de aplicación.

        `setup_hook` se ejecuta una vez, tras conectar pero antes de
        recibir eventos, que es el punto recomendado por discord.py para
        preparar el estado del bot.
        """
        await self.message_stats.initialize()
        await self.message_stats.recover_interrupted_imports()

        for extension in INITIAL_EXTENSIONS:
            await self.load_extension(extension)
            logger.info("Extensión cargada: %s", extension)

        synced = await self.tree.sync()
        logger.info("Comandos de aplicación sincronizados: %d", len(synced))

    async def on_ready(self) -> None:
        """Registra en el log que el bot está conectado y operativo."""
        assert self.user is not None  # on_ready implica sesión iniciada
        logger.info("Sesión iniciada como %s (ID: %s)", self.user, self.user.id)


async def start_bot(token: str, *, command_prefix: str, database_path: Path) -> None:
    """Crea el cliente y lo ejecuta hasta que se detenga o falle.

    Args:
        token: Token de autenticación del bot. Nunca se registra en logs.
        command_prefix: Prefijo de comandos de texto de respaldo.
        database_path: Ubicación de la base de datos persistente del bot.

    Raises:
        discord.LoginFailure: Si el token es inválido.
    """
    client = BotClient(command_prefix=command_prefix, database_path=database_path)
    async with client:
        await client.start(token)
