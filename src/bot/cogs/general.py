"""Cog con comandos generales: `/ping` y `/help`.

Cada comando tiene un único nombre corto, idéntico en las dos interfaces:
comando de aplicación (`/ping`) y comando de texto (`.ping`). Ambas
comparten la misma lógica a través de `CommandResponder`
(`bot.utils.responder`).
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import DEFAULT_COMMAND_PREFIX
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

# Color de acento de los embeds del cog, coherente con el resto del bot.
EMBED_COLOR = discord.Color.blurple()

# Orden y título de cada categoría de la ayuda, por nombre de cog. Un cog
# nuevo que no esté aquí aparece igualmente, al final, como "Otros".
HELP_CATEGORIES: dict[str, str] = {
    "Music": "🎵 Música",
    "Entrance": "🔔 Entradas",
    "Images": "🎨 Imagen (solo con prefijo)",
    "MessageStats": "📊 Niveles",
    "General": "⚙️ General",
}
OTHER_CATEGORY = "📦 Otros"


def _format_arguments(bot: commands.Bot, command: commands.Command) -> str:
    """Devuelve los argumentos como `<obligatorio> [opcional]`, o cadena vacía.

    Se leen del comando de aplicación homónimo, que es la fuente de verdad
    de los nombres de parámetros; si no existe, se usa la firma del texto.
    """
    slash = bot.tree.get_command(command.name)
    if isinstance(slash, app_commands.Command):
        parts = [
            f"<{parameter.name}>" if parameter.required else f"[{parameter.name}]"
            for parameter in slash.parameters
        ]
        return " ".join(parts)
    return command.signature


def _describe(bot: commands.Bot, command: commands.Command) -> str:
    """Descripción corta del comando, tomada del comando de aplicación homónimo."""
    slash = bot.tree.get_command(command.name)
    if isinstance(slash, app_commands.Command) and slash.description:
        return slash.description
    return (command.help or "").splitlines()[0] if command.help else "Sin descripción."


def build_help_embed(bot: commands.Bot) -> discord.Embed:
    """Construye la ayuda: una línea corta por comando, agrupada por categoría.

    Los comandos se leen de `bot.commands` y `bot.tree` en vez de mantener
    una lista escrita a mano, para que la ayuda nunca se desincronice de lo
    realmente registrado. Como `/nombre` y `.nombre` son idénticos, cada
    comando aparece una sola vez. Los comandos ocultos (los 108 efectos de
    imagen) no se listan aquí: tienen su propia lista en `.memes`.
    """
    prefix = _text_prefix(bot)
    embed = discord.Embed(
        title="📖 Comandos",
        description=(
            f"Funcionan igual con `/` y con `{prefix}`. "
            f"Ej: `/play despacito` o `{prefix}play despacito`"
        ),
        color=EMBED_COLOR,
    )

    by_category: dict[str, list[str]] = {}
    for cog_name, title in (*HELP_CATEGORIES.items(), (None, OTHER_CATEGORY)):
        for command in sorted(
            (c for c in bot.commands if not c.hidden and _category_key(c) == cog_name),
            key=lambda c: _definition_index(bot, c),
        ):
            arguments = _format_arguments(bot, command)
            arguments = f" `{arguments}`" if arguments else ""
            line = f"**{command.name}**{arguments} — {_describe(bot, command)}"
            by_category.setdefault(title, []).append(line)

    for title, lines in by_category.items():
        embed.add_field(name=title, value="\n".join(lines), inline=False)

    return embed


def _text_prefix(bot: commands.Bot) -> str:
    """Prefijo de texto con el que se mostrarán los ejemplos de la ayuda.

    Se lee del propio bot para que la ayuda no se desincronice si cambia
    `COMMAND_PREFIX`. Si hubiera varios prefijos, se muestra el primero.
    """
    prefix = bot.command_prefix
    if isinstance(prefix, str):
        return prefix
    if isinstance(prefix, (list, tuple)) and prefix:
        return str(prefix[0])
    return DEFAULT_COMMAND_PREFIX


def _category_key(command: commands.Command) -> str | None:
    """Nombre de cog si tiene categoría conocida; `None` para "Otros"."""
    return command.cog_name if command.cog_name in HELP_CATEGORIES else None


def _definition_index(bot: commands.Bot, command: commands.Command) -> int:
    """Posición del comando en su cog, para respetar el orden de definición."""
    cog = bot.get_cog(command.cog_name) if command.cog_name else None
    if cog is None:
        return 0
    return cog.get_commands().index(command)


class General(commands.Cog):
    """Comandos generales que no pertenecen a un dominio más específico."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _ping_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida entre `/ping` y `.ping`."""
        start = time.perf_counter()
        latency_ms = round(self.bot.latency * 1000)
        elapsed_ms = round((time.perf_counter() - start) * 1000)

        await responder.send(
            f"🏓 Pong! Latencia de la conexión: {latency_ms} ms "
            f"(tiempo de respuesta: {elapsed_ms} ms).",
            ephemeral=True,
        )

    @app_commands.command(name="ping", description="Comprueba que el bot responde.")
    async def ping(self, interaction: discord.Interaction) -> None:
        """Responde con la latencia actual de la conexión con Discord.

        No requiere permisos especiales. Responde de forma efímera porque
        el resultado solo es relevante para quien ejecuta el comando.
        """
        await self._ping_impl(InteractionResponder(interaction))

    @commands.command(name="ping")
    async def ping_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.ping`) de `/ping`."""
        await self._ping_impl(ContextResponder(ctx))

    async def _help_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida entre `/help` y `.help`."""
        await responder.send(embed=build_help_embed(self.bot), ephemeral=True)

    @app_commands.command(name="help", description="Muestra todos los comandos.")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Muestra un embed con todos los comandos."""
        await self._help_impl(InteractionResponder(interaction))

    @commands.command(name="help")
    async def help_command_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.help`) de `/help`."""
        await self._help_impl(ContextResponder(ctx))


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(General(bot))
