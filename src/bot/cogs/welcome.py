"""Mensajes automáticos de bienvenida y despedida en #chat-general."""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

CHAT_GENERAL_CHANNEL_NAME = "chat-general"
ASSETS_PATH = Path(__file__).resolve().parent.parent / "assets"
WELCOME_VIDEO_PATH = ASSETS_PATH / "bienvenida.mp4"
FAREWELL_INSULTS_PATH = ASSETS_PATH / "despedidas.txt"

# Se usa solo si el archivo de frases falta o queda vacío, para que la
# despedida nunca se quede sin texto por un error de edición.
FALLBACK_FAREWELL_INSULTS = ("se ha ido del servidor.",)


def load_farewell_insults(path: Path | None = None) -> tuple[str, ...]:
    """Lee las frases de despedida desde un archivo de texto editable.

    Cada línea no vacía y que no empiece por ``#`` se trata como una frase
    disponible. Se recarga en cada despedida para que los cambios en el
    archivo se apliquen sin reiniciar el bot.

    Args:
        path: Ruta del archivo de frases; por defecto, `FAREWELL_INSULTS_PATH`
            (se resuelve en tiempo de llamada para admitir sustituirla en
            pruebas).

    Returns:
        Tupla de frases disponibles; si el archivo falta o está vacío,
        devuelve `FALLBACK_FAREWELL_INSULTS`.
    """
    path = path if path is not None else FAREWELL_INSULTS_PATH
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.warning("No se pudo leer el archivo de frases de despedida: %s", path)
        return FALLBACK_FAREWELL_INSULTS

    insults = tuple(
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    )
    if not insults:
        logger.warning("El archivo de frases de despedida está vacío: %s", path)
        return FALLBACK_FAREWELL_INSULTS
    return insults


class Welcome(commands.Cog):
    """Gestiona los mensajes de entrada y salida de miembros."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _chat_general(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Busca el canal de texto reservado para los mensajes automáticos."""
        return discord.utils.get(
            guild.text_channels,
            name=CHAT_GENERAL_CHANNEL_NAME,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Da la bienvenida al miembro y adjunta el vídeo de Kratos."""
        channel = self._chat_general(member.guild)
        if channel is None:
            logger.warning(
                "No existe el canal #%s en el servidor %s; se omitió la bienvenida",
                CHAT_GENERAL_CHANNEL_NAME,
                member.guild.id,
            )
            return
        if not WELCOME_VIDEO_PATH.is_file():
            logger.error("No se encuentra el vídeo de bienvenida: %s", WELCOME_VIDEO_PATH)
            return

        content = f"{member.mention} **¿QUIÉN ERES?**"
        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=[member],
            replied_user=False,
        )
        video: discord.File | None = None
        try:
            video = discord.File(WELCOME_VIDEO_PATH, filename="bienvenida.mp4")
            await channel.send(
                content=content,
                file=video,
                allowed_mentions=allowed_mentions,
            )
        except (discord.Forbidden, discord.HTTPException, OSError):
            logger.warning(
                "No se pudo enviar la bienvenida al canal %s del servidor %s",
                channel.id,
                member.guild.id,
                exc_info=True,
            )
        finally:
            if video is not None:
                video.close()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Despide al miembro con una frase elegida al azar."""
        channel = self._chat_general(member.guild)
        if channel is None:
            logger.warning(
                "No existe el canal #%s en el servidor %s; se omitió la despedida",
                CHAT_GENERAL_CHANNEL_NAME,
                member.guild.id,
            )
            return

        insults = await asyncio.to_thread(load_farewell_insults)
        insult = random.choice(insults)
        display_name = discord.utils.escape_markdown(member.display_name)
        try:
            await channel.send(
                f"👋 **{display_name}** {insult}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo enviar la despedida al canal %s del servidor %s",
                channel.id,
                member.guild.id,
                exc_info=True,
            )


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de bienvenida y despedida en el cliente."""
    await bot.add_cog(Welcome(bot))
