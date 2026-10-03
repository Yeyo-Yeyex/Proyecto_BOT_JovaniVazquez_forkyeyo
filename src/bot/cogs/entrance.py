"""Sonidos de entrada: cada miembro sube un audio corto que suena al entrar a voz.

Comando: `/entrada` (y `ºentrada`/`.entrada` en texto), con un único nombre
para subir, ajustar el volumen, borrar y consultar el sonido propio.

Comportamiento en voz, pensado para gastar lo mínimo en el NAS:

- El bot NO se queda en el canal. Cuando alguien entra, se conecta
  ensordecido (`self_deaf=True`, así Discord no le envía el audio de nadie),
  reproduce el clip y se va.
- Si varias personas entran seguidas, los sonidos se encolan y se reproducen
  aprovechando la misma conexión; el bot sale al vaciar la cola.
- Si el bot ya está en voz por otra razón (la música), el sonido de entrada
  no suena: la música tiene prioridad.
- Cada persona dispara su sonido como mucho una vez cada `COOLDOWN_SECONDS`,
  para que entrar y salir en bucle no convierta al bot en una sirena.

Requisitos: intent de estados de voz (incluido en `Intents.default()`) y los
permisos **Conectar** y **Hablar** en los canales de voz.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from collections import deque
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.repositories.entrance_sounds import EntranceSoundStore
from bot.services.entrance_sound import (
    DEFAULT_VOLUME_PERCENT,
    MAX_CLIP_SECONDS,
    MAX_UPLOAD_BYTES,
    MAX_VOLUME_PERCENT,
    MIN_VOLUME_PERCENT,
    EntranceSoundError,
    OpusPacketSource,
    build_source_clip,
    check_duration,
    probe_audio_duration,
    read_opus_packets,
    render_clip,
    validate_volume,
)
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

#: Tiempo mínimo entre dos sonidos de entrada de la misma persona.
COOLDOWN_SECONDS = 30.0
#: Sonidos en espera por servidor. Si entra media lista de golpe, el resto se omite.
MAX_PENDING_PER_GUILD = 5
#: Límite de memoria del registro de enfriamientos (una entrada por persona).
MAX_COOLDOWN_ENTRIES = 1000
CONNECT_TIMEOUT_SECONDS = 10.0
#: Pausa tras conectar o moverse antes de emitir. Sin ella Discord puede
#: comerse el principio del clip mientras termina de establecer la sesión de voz.
PRE_ROLL_SECONDS = 0.3
#: Duración de cada paquete Opus generado por el servicio (`-frame_duration 20`).
PACKET_SECONDS = 0.02
#: Conversiones de audio simultáneas (cada una lanza un `ffmpeg`).
MAX_CONCURRENT_JOBS = 2
#: Tipos MIME que se aceptan cuando Discord informa uno. Si no lo informa,
#: decide `ffprobe`. Se admite vídeo porque un clip de vídeo con audio es
#: una forma habitual de compartir sonidos.
ACCEPTED_MIME_PREFIXES = ("audio/", "video/", "application/ogg")

USAGE_TEXT = (
    "Uso: adjunta un audio de hasta 3 s con `/entrada archivo:` (o `.entrada` con el "
    "archivo adjunto). Volumen: `/entrada volumen:150` o `.entrada 150` "
    f"({MIN_VOLUME_PERCENT}-{MAX_VOLUME_PERCENT} %). Borrar: `/entrada borrar:True` "
    "o `.entrada borrar`."
)


def _is_accepted_attachment(attachment: discord.Attachment) -> bool:
    """Filtro rápido por tipo MIME antes de descargar nada."""
    content_type = attachment.content_type
    return content_type is None or content_type.startswith(ACCEPTED_MIME_PREFIXES)


def parse_text_option(option: str | None) -> tuple[int | None, bool]:
    """Interpreta el argumento de `.entrada`: un volumen, `borrar` o nada.

    Returns:
        `(volumen, borrar)`.

    Raises:
        EntranceSoundError: Si el argumento no es ni un número ni `borrar`.
    """
    if option is None:
        return None, False
    cleaned = option.strip().lower().rstrip("%")
    if cleaned == "borrar":
        return None, True
    if cleaned.isdigit():
        return validate_volume(int(cleaned)), False
    raise EntranceSoundError(USAGE_TEXT)


class Entrance(commands.Cog):
    """Sonidos personalizados que suenan cuando un miembro entra a un canal de voz."""

    def __init__(self, bot: commands.Bot, store: EntranceSoundStore) -> None:
        self.bot = bot
        self.store = store
        self._jobs = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._pending: dict[int, deque[tuple[int, int]]] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._last_played: dict[tuple[int, int], float] = {}

    # ------------------------------------------------------------------ #
    # Comando
    # ------------------------------------------------------------------ #

    async def _entrada_impl(
        self,
        responder: CommandResponder,
        attachment: discord.Attachment | None,
        volume: int | None,
        delete: bool,
    ) -> None:
        """Lógica compartida entre `/entrada` y `ºentrada`."""
        guild, member = responder.guild, responder.member
        if guild is None or member is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        if delete:
            if attachment is not None or volume is not None:
                await responder.send_error("Para borrar tu sonido, usa `borrar` sin nada más.")
                return
            removed = await self.store.delete(guild.id, member.id)
            text = "🗑️ Sonido de entrada borrado." if removed else "No tenías sonido de entrada."
            await responder.send(text, ephemeral=True)
            return

        if attachment is None and volume is None:
            await self._send_status(responder, guild.id, member.id)
            return

        if attachment is not None:
            if not _is_accepted_attachment(attachment):
                await responder.send_error("Ese archivo no parece un audio.")
                return
            if attachment.size > MAX_UPLOAD_BYTES:
                limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                await responder.send_error(
                    f"El archivo es demasiado grande (máximo {limit_mb} MB)."
                )
                return
        elif not await self.store.has_sound(guild.id, member.id):
            await responder.send_error("Aún no tienes sonido. Sube primero un audio de hasta 3 s.")
            return

        await responder.start_progress("🎧 Preparando tu sonido...", ephemeral=True)
        try:
            async with self._jobs:
                clip, final_volume = await self._prepare(guild.id, member.id, attachment, volume)
        except EntranceSoundError as error:
            await responder.finish(f"❌ {error}")
            return
        except discord.HTTPException:
            logger.warning("No se pudo descargar el audio de entrada", exc_info=True)
            await responder.finish("No se pudo descargar el archivo. Inténtalo de nuevo.")
            return
        except Exception:
            # Fallo imprevisto: se registra con traza y se avisa, para que el
            # aviso de progreso nunca se quede colgado.
            logger.exception("Error inesperado al preparar un sonido de entrada")
            await responder.finish("Algo salió mal al preparar el sonido.")
            return

        seconds = len(read_opus_packets(clip)) * PACKET_SECONDS
        content = (
            f"✅ Sonido de entrada guardado ({seconds:.1f} s, volumen {final_volume} %). "
            "Sonará cuando entres a un canal de voz."
        )
        try:
            await responder.finish(content, file=discord.File(io.BytesIO(clip), "entrada.ogg"))
        except discord.HTTPException:
            # Ya está guardado; si el canal no admite adjuntos, basta el texto.
            logger.warning("No se pudo adjuntar la vista previa del sonido", exc_info=True)
            await responder.finish(content)

    async def _prepare(
        self,
        guild_id: int,
        user_id: int,
        attachment: discord.Attachment | None,
        volume: int | None,
    ) -> tuple[bytes, int]:
        """Convierte el adjunto (si hay) y genera el clip con el volumen final.

        Todo se escribe en una carpeta temporal y solo se mueve al sitio
        definitivo si cada paso ha salido bien.

        Returns:
            El clip final en bytes y el volumen aplicado.
        """
        final_volume = (
            volume
            if volume is not None
            else await self.store.read_volume(guild_id, user_id, DEFAULT_VOLUME_PERCENT)
        )
        work = await asyncio.to_thread(self.store.make_work_dir)
        try:
            work_dir = Path(work.name)
            new_source: Path | None = None
            if attachment is not None:
                upload = work_dir / "upload"
                data = await attachment.read()
                await asyncio.to_thread(upload.write_bytes, data)
                check_duration(await probe_audio_duration(upload))
                new_source = work_dir / "source.ogg"
                await build_source_clip(upload, new_source)
                render_from = new_source
            else:
                render_from = self.store.paths(guild_id, user_id).source
                if not await asyncio.to_thread(render_from.is_file):
                    raise EntranceSoundError("Falta tu audio original. Vuelve a subirlo.")

            clip_path = work_dir / "clip.ogg"
            await render_clip(render_from, clip_path, final_volume)
            clip = await asyncio.to_thread(clip_path.read_bytes)
            read_opus_packets(clip)  # Falla aquí, y no al entrar a voz, si salió mal.
            await self.store.save(
                guild_id, user_id, clip=clip_path, volume_percent=final_volume, source=new_source
            )
            return clip, final_volume
        finally:
            await asyncio.to_thread(work.cleanup)

    async def _send_status(self, responder: CommandResponder, guild_id: int, user_id: int) -> None:
        """Muestra si el miembro tiene sonido y con qué volumen."""
        if await self.store.has_sound(guild_id, user_id):
            volume = await self.store.read_volume(guild_id, user_id, DEFAULT_VOLUME_PERCENT)
            text = f"🔔 Tienes sonido de entrada (volumen {volume} %).\n{USAGE_TEXT}"
        else:
            text = f"🔕 No tienes sonido de entrada.\n{USAGE_TEXT}"
        await responder.send(text, ephemeral=True)

    @app_commands.command(name="entrada", description="Tu sonido al entrar a un canal de voz.")
    @app_commands.guild_only()
    @app_commands.describe(
        archivo=f"Audio de {MAX_CLIP_SECONDS:.0f} s como máximo (mp3, ogg, wav, m4a...).",
        volumen=f"Volumen de tu sonido ({MIN_VOLUME_PERCENT}-{MAX_VOLUME_PERCENT} %).",
        borrar="Quita tu sonido de entrada.",
    )
    async def entrada(
        self,
        interaction: discord.Interaction,
        archivo: discord.Attachment | None = None,
        volumen: app_commands.Range[int, MIN_VOLUME_PERCENT, MAX_VOLUME_PERCENT] | None = None,
        borrar: bool = False,
    ) -> None:
        """Sube, ajusta, borra o consulta el sonido de entrada propio.

        Sin argumentos muestra el estado. Las respuestas son efímeras salvo
        en la versión de texto, que no puede ocultarlas.
        """
        await self._entrada_impl(InteractionResponder(interaction), archivo, volumen, borrar)

    @commands.command(name="entrada")
    @commands.guild_only()
    async def entrada_text(self, ctx: commands.Context, opcion: str | None = None) -> None:
        """Versión de texto (`ºentrada`) de `/entrada`.

        El audio va adjunto al propio mensaje; `opcion` es un volumen o `borrar`.
        """
        responder = ContextResponder(ctx)
        try:
            volume, delete = parse_text_option(opcion)
        except EntranceSoundError as error:
            await responder.send_error(str(error))
            return
        attachment = ctx.message.attachments[0] if ctx.message.attachments else None
        await self._entrada_impl(responder, attachment, volume, delete)

    # ------------------------------------------------------------------ #
    # Reproducción al entrar a voz
    # ------------------------------------------------------------------ #

    def _cooldown_allows(self, guild_id: int, user_id: int) -> bool:
        """Registra el intento y dice si ha pasado el enfriamiento."""
        now = time.monotonic()
        key = (guild_id, user_id)
        if now - self._last_played.get(key, float("-inf")) < COOLDOWN_SECONDS:
            return False
        if len(self._last_played) >= MAX_COOLDOWN_ENTRIES:
            # Las entradas caducadas ya no bloquean nada: se pueden tirar.
            self._last_played = {
                k: t for k, t in self._last_played.items() if now - t < COOLDOWN_SECONDS
            }
        self._last_played[key] = now
        return True

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Encola el sonido de quien entra (o cambia) a un canal de voz.

        Silenciarse, ensordecerse o empezar a emitir también disparan este
        evento; se ignoran porque el canal no cambia.
        """
        if member.bot or after.channel is None or before.channel == after.channel:
            return
        guild = member.guild
        if guild.afk_channel is not None and after.channel.id == guild.afk_channel.id:
            return
        if not await self.store.has_sound(guild.id, member.id):
            return
        if not self._cooldown_allows(guild.id, member.id):
            return

        queue = self._pending.setdefault(guild.id, deque())
        if len(queue) >= MAX_PENDING_PER_GUILD:
            return
        queue.append((member.id, after.channel.id))
        self._ensure_worker(guild)

    def _ensure_worker(self, guild: discord.Guild) -> None:
        """Arranca el reproductor del servidor si no hay uno en marcha."""
        worker = self._workers.get(guild.id)
        if worker is None or worker.done():
            self._workers[guild.id] = asyncio.create_task(
                self._drain(guild), name=f"entrance-sounds-{guild.id}"
            )

    async def _drain(self, guild: discord.Guild) -> None:
        """Reproduce la cola del servidor reutilizando una sola conexión de voz."""
        voice: discord.VoiceClient | None = None
        queue = self._pending.setdefault(guild.id, deque())
        try:
            while queue:
                member_id, channel_id = queue.popleft()
                current = guild.voice_client
                if current is not None and current is not voice:
                    # El bot ya está en voz por otro motivo (música): no se interrumpe.
                    queue.clear()
                    break

                member = guild.get_member(member_id)
                channel = member.voice.channel if member and member.voice else None
                if channel is None or channel.id != channel_id:
                    continue  # Ya se fue o cambió de canal; su nuevo evento lo cubrirá.
                if not self._can_join(channel):
                    continue

                clip = await self.store.read_clip(guild.id, member_id)
                if clip is None:
                    continue
                try:
                    packets = read_opus_packets(clip)
                except EntranceSoundError:
                    logger.warning(
                        "Sonido de entrada dañado: servidor %s, miembro %s", guild.id, member_id
                    )
                    continue

                try:
                    voice = await self._join(channel, voice)
                except (discord.ClientException, discord.HTTPException, TimeoutError):
                    logger.warning(
                        "No se pudo entrar al canal %s para un sonido de entrada",
                        channel.id,
                        exc_info=True,
                    )
                    break

                await self._play(voice, packets)
                if not voice.is_connected():
                    # Nos desconectaron (o la música tomó la voz): no se insiste.
                    queue.clear()
                    break
        except Exception:
            logger.exception("Error inesperado reproduciendo sonidos de entrada en %s", guild.id)
        finally:
            if voice is not None and guild.voice_client is voice:
                try:
                    await voice.disconnect(force=False)
                except discord.HTTPException:
                    logger.warning(
                        "Error al salir del canal tras un sonido de entrada", exc_info=True
                    )
            self._workers.pop(guild.id, None)
            # Alguien pudo entrar mientras salíamos: su sonido no debe perderse.
            if queue and not self.bot.is_closed():
                self._ensure_worker(guild)

    def _can_join(self, channel: discord.abc.GuildChannel) -> bool:
        """Comprueba permisos y aforo antes de intentar conectar."""
        me = channel.guild.me
        permissions = channel.permissions_for(me)
        if not (permissions.connect and permissions.speak):
            return False
        user_limit = getattr(channel, "user_limit", 0)
        members = getattr(channel, "members", [])
        is_full = bool(user_limit) and len(members) >= user_limit
        return not is_full or permissions.move_members

    async def _join(
        self,
        channel: discord.VoiceChannel | discord.StageChannel,
        voice: discord.VoiceClient | None,
    ) -> discord.VoiceClient:
        """Conecta (ensordecido) o se mueve al canal, reutilizando la conexión si existe."""
        if voice is None or not voice.is_connected():
            voice = await channel.connect(timeout=CONNECT_TIMEOUT_SECONDS, self_deaf=True)
        elif voice.channel != channel:
            await voice.move_to(channel)
        else:
            return voice
        await asyncio.sleep(PRE_ROLL_SECONDS)
        return voice

    async def _play(self, voice: discord.VoiceClient, packets: list[bytes]) -> None:
        """Reproduce los paquetes y espera a que terminen (con un tope de seguridad)."""
        loop = asyncio.get_running_loop()
        finished = asyncio.Event()

        def _after(error: Exception | None) -> None:
            # Lo llama el hilo de audio de discord.py, no el event loop.
            if error is not None:
                logger.warning("Error de reproducción en un sonido de entrada: %s", error)
            loop.call_soon_threadsafe(finished.set)

        try:
            voice.play(OpusPacketSource(packets), after=_after)
        except discord.ClientException:
            logger.warning("No se pudo reproducir un sonido de entrada", exc_info=True)
            return
        try:
            await asyncio.wait_for(finished.wait(), timeout=len(packets) * PACKET_SECONDS + 5)
        except TimeoutError:
            voice.stop()

    async def cog_unload(self) -> None:
        """Cancela los reproductores en curso; su `finally` cierra la voz."""
        # Se vacían las colas antes de cancelar para que ningún `finally`
        # vuelva a arrancar un reproductor.
        for queue in self._pending.values():
            queue.clear()
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        for worker in workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de sonidos de entrada en el cliente."""
    await bot.add_cog(Entrance(bot, bot.entrance_sounds))
