"""Cog con comandos y eventos generales de propósito básico.

Sirve como punto de partida y ejemplo de convención para futuros cogs:
un comando de aplicación simple (`/ping`) sin dependencias externas.
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class General(commands.Cog):
    """Comandos generales que no pertenecen a un dominio más específico."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Comprueba que el bot responde y su latencia.")
    async def ping(self, interaction: discord.Interaction) -> None:
        """Responde con la latencia actual de la conexión con Discord.

        No requiere permisos especiales. Responde de forma efímera porque
        el resultado solo es relevante para quien ejecuta el comando.
        """
        start = time.perf_counter()
        latency_ms = round(self.bot.latency * 1000)
        elapsed_ms = round((time.perf_counter() - start) * 1000)

        await interaction.response.send_message(
            f"🏓 Pong! Latencia de la conexión: {latency_ms} ms "
            f"(tiempo de respuesta: {elapsed_ms} ms).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(General(bot))
