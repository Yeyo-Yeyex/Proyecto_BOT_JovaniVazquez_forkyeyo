"""Gestión centralizada de errores de comandos de texto (`.`) y de aplicación (`/`).

Sin este cog, los errores de comandos de texto (un argumento mal escrito, un
miembro que no existe, un fallo inesperado) solo aparecen en el log y el
usuario no recibe ninguna respuesta, y los de `/` terminan en el genérico
"la aplicación no ha respondido". Aquí se convierten en un mensaje breve y
seguro: nunca se muestran trazas ni datos internos (ver Biblia.txt).
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

GENERIC_ERROR = "Algo salió mal ejecutando el comando. Inténtalo de nuevo."
GUILD_ONLY_ERROR = "Este comando solo está disponible dentro de un servidor."
ADMIN_ONLY_ERROR = "Solo los administradores pueden usar este comando."


def describe_command_error(error: commands.CommandError) -> str | None:
    """Mensaje para el usuario de un error de comando de texto.

    Returns:
        El texto a mostrar, o `None` si no hay que responder nada (por
        ejemplo, un mensaje que empieza por el prefijo pero no es un comando).
    """
    if isinstance(error, commands.CommandNotFound):
        return None
    if isinstance(error, commands.NoPrivateMessage):
        return GUILD_ONLY_ERROR
    if isinstance(error, commands.MissingRequiredArgument):
        return f"Falta el argumento `{error.param.name}`. Escribe `help` para ver cómo se usa."
    if isinstance(error, commands.UserInputError):
        return "No entendí algún argumento. Escribe `help` para ver cómo se usa."
    if isinstance(error, commands.MissingPermissions):
        return ADMIN_ONLY_ERROR
    if isinstance(error, commands.CheckFailure):
        return "No puedes usar este comando aquí."
    return GENERIC_ERROR


def describe_app_command_error(error: app_commands.AppCommandError) -> str:
    """Mensaje para el usuario de un error de comando de aplicación."""
    if isinstance(error, app_commands.NoPrivateMessage):
        return GUILD_ONLY_ERROR
    if isinstance(error, app_commands.MissingPermissions):
        return ADMIN_ONLY_ERROR
    if isinstance(error, app_commands.CheckFailure):
        return "No puedes usar este comando aquí."
    return GENERIC_ERROR


class Errors(commands.Cog):
    """Responde con mensajes claros cuando falla un comando."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._previous_tree_handler = bot.tree.on_error

    async def cog_load(self) -> None:
        """Instala el gestor de errores de los comandos de aplicación."""
        self.bot.tree.error(self.on_app_command_error)

    async def cog_unload(self) -> None:
        """Restaura el gestor de errores original del árbol de comandos."""
        self.bot.tree.on_error = self._previous_tree_handler

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Informa del error de un comando de texto y registra los inesperados."""
        message = describe_command_error(error)
        if isinstance(error, commands.CommandInvokeError):
            logger.error(
                "Error en el comando de texto %r",
                ctx.command and ctx.command.name,
                exc_info=error.original,
            )
        if message is None:
            return
        try:
            await ctx.send(message)
        except discord.HTTPException:
            logger.warning("No se pudo enviar el aviso de error", exc_info=True)

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Informa del error de un comando de aplicación y registra los inesperados."""
        if isinstance(error, app_commands.CommandInvokeError):
            logger.error(
                "Error en el comando de aplicación %r",
                interaction.command and interaction.command.name,
                exc_info=error.original,
            )
        message = describe_app_command_error(error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning("No se pudo enviar el aviso de error", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    """Registra el gestor de errores en el cliente."""
    await bot.add_cog(Errors(bot))
