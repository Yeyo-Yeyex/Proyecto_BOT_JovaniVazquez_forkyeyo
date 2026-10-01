"""Cog con comandos y eventos generales de propósito básico.

Sirve como punto de partida y ejemplo de convención para futuros cogs: un
comando de aplicación simple (`/ping`) sin dependencias externas, y el
comando de ayuda (`/ayuda`) que lista todos los comandos del bot.

Como el resto de comandos del proyecto, ambos admiten también su versión
de texto con el prefijo configurado (por ejemplo `ºping`, `ºayuda`) a
través de `CommandResponder` (`bot.utils.responder`).
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

# Color de acento de los embeds del cog, coherente con el resto del bot.
EMBED_COLOR = discord.Color.blurple()
HELP_FIELD_VALUE_LIMIT = 900


def _describe_command_prefixes(bot: commands.Bot) -> str:
    """Construye una descripción legible de los prefijos de texto activos.

    `bot.command_prefix` puede ser un `str`, una tupla/lista de `str` o un
    callable; esta función normaliza los tres casos para el embed de ayuda.
    """
    prefix = bot.command_prefix
    if callable(prefix):
        return "un prefijo dinámico"
    prefixes = (prefix,) if isinstance(prefix, str) else tuple(prefix)
    return ", ".join(f"`{p}`" for p in prefixes)


def _add_help_fields(embed: discord.Embed, title: str, lines: list[str]) -> None:
    """Añade líneas en campos separados sin superar los límites de Discord."""
    chunks: list[list[str]] = []
    current: list[str] = []
    current_length = 0

    for line in lines:
        if current and current_length + len(line) + 1 > HELP_FIELD_VALUE_LIMIT:
            chunks.append(current)
            current = []
            current_length = 0
        current.append(line)
        current_length += len(line) + 1

    if current:
        chunks.append(current)

    for index, chunk in enumerate(chunks, start=1):
        field_name = f"{title} ({index}/{len(chunks)})" if len(chunks) > 1 else title
        embed.add_field(name=field_name, value="\n".join(chunk), inline=False)


def _format_slash_command(command: app_commands.Command | app_commands.Group) -> str:
    """Formatea el nombre slash y sus argumentos para la lista de ayuda."""
    name = f"/{command.name}"
    if not isinstance(command, app_commands.Command):
        return name

    parameters = [
        f"<{parameter.name}>" if parameter.required else f"[{parameter.name}]"
        for parameter in command.parameters
    ]
    return f"{name} {' '.join(parameters)}" if parameters else name


def build_help_embed(bot: commands.Bot) -> discord.Embed:
    """Construye el embed de ayuda listando dinámicamente todos los comandos.

    Se apoya en `bot.tree` (comandos de aplicación) y `bot.commands`
    (comandos de texto) en vez de mantener una lista escrita a mano, para
    que la ayuda nunca se desincronice de los comandos realmente
    registrados (ver Biblia.txt, norma de no duplicar información).
    """
    embed = discord.Embed(
        title="📖 Comandos disponibles",
        description=(
            "Todos los comandos de aplicación (`/comando`) también funcionan "
            f"como comando de texto con {_describe_command_prefixes(bot)} "
            "(por ejemplo `ºayuda`)."
        ),
        color=EMBED_COLOR,
    )

    slash_lines = [
        f"**{_format_slash_command(command)}** — {command.description or 'Sin descripción.'}"
        for command in sorted(bot.tree.get_commands(), key=lambda c: c.name)
    ]
    _add_help_fields(embed, "Comandos /", slash_lines)

    text_lines = []
    for command in sorted(bot.commands, key=lambda c: c.name):
        signature = f" {command.signature}" if command.signature else ""
        invocations = [f"º{command.name}{signature}"]
        invocations.extend(f"º{alias}{signature}" for alias in command.aliases)
        description = command.help or "Sin descripción."
        text_lines.append(f"**{' · '.join(invocations)}** — {description}")
    _add_help_fields(embed, "Comandos º", text_lines)

    embed.set_footer(text="Usa /ayuda o ºayuda para consultar esta lista.")
    return embed


class General(commands.Cog):
    """Comandos generales que no pertenecen a un dominio más específico."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _ping_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida entre `/ping` y `ºping`."""
        start = time.perf_counter()
        latency_ms = round(self.bot.latency * 1000)
        elapsed_ms = round((time.perf_counter() - start) * 1000)

        await responder.send(
            f"🏓 Pong! Latencia de la conexión: {latency_ms} ms "
            f"(tiempo de respuesta: {elapsed_ms} ms).",
            ephemeral=True,
        )

    @app_commands.command(name="ping", description="Comprueba que el bot responde y su latencia.")
    async def ping(self, interaction: discord.Interaction) -> None:
        """Responde con la latencia actual de la conexión con Discord.

        No requiere permisos especiales. Responde de forma efímera porque
        el resultado solo es relevante para quien ejecuta el comando.
        """
        await self._ping_impl(InteractionResponder(interaction))

    @commands.command(name="ping")
    async def ping_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`ºping`) de `/ping`."""
        await self._ping_impl(ContextResponder(ctx))

    async def _help_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida entre `/ayuda` y `ºayuda`/`ºhelp`."""
        await responder.send(embed=build_help_embed(self.bot), ephemeral=True)

    @app_commands.command(name="ayuda", description="Lista todos los comandos disponibles.")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Muestra un embed con todos los comandos de aplicación y de texto."""
        await self._help_impl(InteractionResponder(interaction))

    @commands.command(name="ayuda", aliases=["help"])
    async def help_command_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`ºayuda`, `ºhelp`) de `/ayuda`."""
        await self._help_impl(ContextResponder(ctx))


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(General(bot))
