"""Comandos de imagen: `magik` deforma una imagen o un avatar.

`/magik` y `.magik` son el mismo comando: comparten la lógica a través de
`CommandResponder` (`bot.utils.responder`). La imagen se toma, por orden:

1. Un archivo adjunto (en `/magik`, el parámetro `imagen`; en `.magik`, el
   adjunto del propio mensaje o del mensaje al que se responde).
2. El avatar del miembro indicado.
3. El avatar de quien ejecuta el comando.

Solo se aceptan adjuntos de Discord y avatares, nunca URLs arbitrarias: así
el bot no puede usarse para hacer peticiones a direcciones elegidas por el
usuario. El procesado (CPU) se ejecuta fuera del event loop, con un límite de
trabajos simultáneos y un enfriamiento por usuario.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.magik import (
    MAX_INPUT_BYTES,
    ImageTooLargeError,
    InvalidImageError,
    apply_magik,
)
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

# Segundos mínimos entre dos usos de un mismo usuario.
COOLDOWN_SECONDS = 5

# Trabajos de procesado simultáneos en todo el bot; el resto espera turno.
MAX_CONCURRENT_JOBS = 2

# Tamaño con el que se pide el avatar: de sobra, ya que se reduce a 320 px.
AVATAR_SIZE = 512

# Fuente de la imagen: un adjunto o un avatar, ambos con `read()`.
ImageSource = discord.Attachment | discord.Asset


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    """Indica si Discord declara el adjunto como imagen."""
    return bool(attachment.content_type and attachment.content_type.startswith("image/"))


def _first_image(message: discord.Message | None) -> discord.Attachment | None:
    """Primer adjunto de imagen de un mensaje, o `None`."""
    if message is None:
        return None
    return next((a for a in message.attachments if _is_image_attachment(a)), None)


def _avatar_of(user: discord.abc.User) -> discord.Asset:
    """Avatar como imagen estática PNG (el primer fotograma si es animado)."""
    return user.display_avatar.replace(size=AVATAR_SIZE, format="png")


class Images(commands.Cog):
    """Comandos que transforman imágenes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._jobs = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._last_use: dict[int, float] = {}

    def _cooldown_remaining(self, user_id: int) -> float:
        """Segundos que faltan para que el usuario pueda volver a usar el comando."""
        elapsed = time.monotonic() - self._last_use.get(user_id, float("-inf"))
        return max(0.0, COOLDOWN_SECONDS - elapsed)

    async def _magik_impl(self, responder: CommandResponder, source: ImageSource) -> None:
        """Lógica compartida entre `/magik` y `.magik`."""
        member = responder.member
        if responder.guild is None or member is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        if isinstance(source, discord.Attachment):
            if not _is_image_attachment(source):
                await responder.send_error("Ese archivo no es una imagen.")
                return
            if source.size > MAX_INPUT_BYTES:
                limit_mb = MAX_INPUT_BYTES // (1024 * 1024)
                await responder.send_error(f"La imagen es demasiado grande (máximo {limit_mb} MB).")
                return

        remaining = self._cooldown_remaining(member.id)
        if remaining > 0:
            await responder.send_error(f"Espera {remaining:.0f} s antes de volver a usar magik.")
            return
        self._last_use[member.id] = time.monotonic()

        await responder.start_progress("🌀 Distorsionando...")

        try:
            data = await source.read()
        except (discord.HTTPException, discord.NotFound):
            logger.warning("No se pudo descargar la imagen para magik", exc_info=True)
            await responder.finish("No se pudo descargar la imagen. Inténtalo de nuevo.")
            return

        try:
            async with self._jobs:
                result = await asyncio.to_thread(apply_magik, data)
        except ImageTooLargeError:
            await responder.finish("La imagen es demasiado grande para procesarla.")
            return
        except InvalidImageError:
            await responder.finish("No pude leer esa imagen. Prueba con un PNG o JPEG.")
            return
        except Exception:
            # Cualquier fallo imprevisto del procesado: se registra y se avisa,
            # para que el aviso de progreso nunca se quede colgado.
            logger.exception("Error inesperado al aplicar magik")
            await responder.finish("Algo salió mal al deformar la imagen.")
            return

        try:
            await responder.finish(file=discord.File(io.BytesIO(result), filename="magik.png"))
        except discord.Forbidden:
            logger.warning("Sin permiso para adjuntar archivos en el canal", exc_info=True)
            await responder.finish(
                "No tengo permiso para adjuntar archivos en este canal. "
                "Actívame **Adjuntar archivos** y vuelve a probar."
            )
        except discord.HTTPException:
            logger.warning("No se pudo enviar la imagen de magik", exc_info=True)
            await responder.finish("No pude enviar la imagen. Inténtalo de nuevo.")

    @app_commands.command(name="magik", description="Deforma una imagen o un avatar.")
    @app_commands.guild_only()
    @app_commands.describe(
        imagen="Imagen a deformar.",
        miembro="Miembro cuyo avatar deformar (por defecto, el tuyo).",
    )
    async def magik(
        self,
        interaction: discord.Interaction,
        imagen: discord.Attachment | None = None,
        miembro: discord.Member | None = None,
    ) -> None:
        """Deforma con seam carving la imagen adjunta o el avatar indicado."""
        source = imagen or _avatar_of(miembro or interaction.user)
        await self._magik_impl(InteractionResponder(interaction), source)

    @commands.command(name="magik")
    @commands.guild_only()
    async def magik_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.magik`) de `/magik`.

        Usa el adjunto del mensaje, o el del mensaje al que se responde.
        """
        replied = ctx.message.reference.resolved if ctx.message.reference else None
        source = (
            _first_image(ctx.message)
            or _first_image(replied if isinstance(replied, discord.Message) else None)
            or _avatar_of(miembro or ctx.author)
        )
        await self._magik_impl(ContextResponder(ctx), source)


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de imágenes en el cliente."""
    await bot.add_cog(Images(bot))
