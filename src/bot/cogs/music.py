"""Reproductor de música por servidor: cola, control y conexión de voz.

Cada servidor tiene su propio `GuildMusicState` con cola, pista actual y
volumen independientes. La extracción de audio (`yt-dlp`) y la propia
reproducción (`ffmpeg`) son operaciones bloqueantes o dirigidas por hilos
ajenos al event loop; este cog las aísla con `asyncio.to_thread` y con el
patrón `after=` de `discord.py`, que llama a nuestro código desde un hilo
distinto y por eso se reencola con `asyncio.run_coroutine_threadsafe`.

Cada acción tiene un único nombre corto, idéntico en las dos interfaces:
comando de aplicación (`/play`) y comando de texto (`.play`). Ambas
comparten exactamente la misma lógica de negocio a través de `CommandResponder`
(`bot.utils.responder`), una abstracción compartida con el resto de cogs
que oculta si el origen fue una `discord.Interaction` o un mensaje de texto.
"""

from __future__ import annotations

import asyncio
import logging

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from bot.services.music import (
    DEFAULT_VOLUME_PERCENT,
    MAX_VOLUME_PERCENT,
    MIN_VOLUME_PERCENT,
    MusicQueue,
    QueueFullError,
    Track,
    TrackTooLongError,
    TrackUnavailableError,
    build_track_from_info,
    format_duration,
    format_ffmpeg_headers,
    volume_percent_to_factor,
)
from bot.services.music_source import extract_track_info
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

# Tiempo sin reproducir nada antes de abandonar el canal de voz. Evita que
# el bot ocupe un canal indefinidamente tras vaciarse la cola.
IDLE_DISCONNECT_SECONDS = 5 * 60

# Reconectar el flujo de audio ante cortes de red transitorios del origen.
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"


class GuildMusicState:
    """Estado de reproducción de un servidor: cola, conexión y volumen."""

    def __init__(self) -> None:
        self.queue = MusicQueue()
        self.voice_client: discord.VoiceClient | None = None
        self.current: Track | None = None
        self.volume_percent: int = DEFAULT_VOLUME_PERCENT
        self.lock = asyncio.Lock()
        self.idle_task: asyncio.Task[None] | None = None
        # Un `/stop` deliberado pone esto en True para que el callback de
        # fin de pista no encadene la siguiente canción de la cola.
        self.stopping = False
        # Canal donde se pidió la última pista, para poder avisar de
        # fallos de reproducción que ocurren fuera de una interacción.
        self.text_channel: discord.abc.Messageable | None = None


class Music(commands.Cog):
    """Comandos de reproducción de música en canales de voz."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}

    def _get_state(self, guild_id: int) -> GuildMusicState:
        """Obtiene el estado del servidor, creándolo la primera vez."""
        state = self._states.get(guild_id)
        if state is None:
            state = GuildMusicState()
            self._states[guild_id] = state
        return state

    async def _require_connected_state(self, responder: CommandResponder) -> GuildMusicState | None:
        """Valida contexto de servidor, conexión de voz y canal del miembro.

        Responde con un mensaje de error y devuelve `None` si algo falla,
        para que el comando que llama pueda simplemente devolver el
        control sin duplicar el manejo de errores.
        """
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return None

        state = self._get_state(guild.id)
        if state.voice_client is None:
            await responder.send_error("El bot no está conectado a ningún canal de voz.")
            return None

        member = responder.member
        if (
            member is None
            or member.voice is None
            or member.voice.channel != state.voice_client.channel
        ):
            await responder.send_error("Debes estar en el mismo canal de voz que el bot.")
            return None

        return state

    @app_commands.command(name="play", description="Reproduce una canción o la añade a la cola.")
    @app_commands.guild_only()
    @app_commands.describe(consulta="Nombre de la canción o enlace a reproducir.")
    async def play(self, interaction: discord.Interaction, consulta: str) -> None:
        """Une el bot al canal de voz del miembro y encola o reproduce la pista."""
        await self._play_impl(InteractionResponder(interaction), consulta)

    @commands.command(name="play")
    @commands.guild_only()
    async def play_text(self, ctx: commands.Context, *, consulta: str) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._play_impl(ContextResponder(ctx), consulta)

    async def _play_impl(self, responder: CommandResponder, consulta: str) -> None:
        """Lógica compartida por todas las variantes de reproducir una pista."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        member = responder.member
        if member is None or member.voice is None:
            await responder.send_error(
                "Debes estar conectado a un canal de voz para reproducir música."
            )
            return

        state = self._get_state(guild.id)
        if state.voice_client is not None and state.voice_client.channel != member.voice.channel:
            await responder.send_error("Debes estar en el mismo canal de voz que el bot.")
            return

        await responder.start_progress()

        if state.voice_client is None:
            try:
                state.voice_client = await member.voice.channel.connect()
            except (discord.ClientException, TimeoutError) as error:
                logger.warning(
                    "No se pudo conectar al canal de voz %s del servidor %s: %s",
                    member.voice.channel.id,
                    guild.id,
                    error,
                )
                await responder.finish("No se pudo conectar al canal de voz. Inténtalo de nuevo.")
                return

        try:
            info = await asyncio.to_thread(extract_track_info, consulta)
        except yt_dlp.utils.DownloadError as error:
            logger.info("No se pudo resolver la consulta de música %r: %s", consulta, error)
            await responder.finish(
                "No se encontró ninguna pista reproducible para esa búsqueda o enlace."
            )
            return

        try:
            track = build_track_from_info(info, requested_by=member.display_name)
        except TrackTooLongError as error:
            limit_text = format_duration(30 * 60)
            await responder.finish(
                f"Esa pista dura demasiado o es un directo sin duración conocida "
                f"(límite: {limit_text}). Duración detectada: "
                f"{format_duration(error.duration_seconds)}."
            )
            return
        except TrackUnavailableError:
            await responder.finish("La fuente no proporcionó audio reproducible para esa consulta.")
            return

        async with state.lock:
            try:
                state.queue.add(track)
            except QueueFullError:
                await responder.finish(
                    "La cola de este servidor está llena; espera a que avancen pistas."
                )
                return

            state.text_channel = responder.channel
            position = len(state.queue)
            starting_now = state.current is None and not (
                state.voice_client and state.voice_client.is_playing()
            )
            if starting_now:
                await self._play_next(guild.id)

        if starting_now:
            await responder.finish(
                f"▶️ Reproduciendo ahora: **{track.title}** "
                f"({format_duration(track.duration_seconds)})."
            )
        else:
            await responder.finish(
                f"🎶 Añadida a la cola en la posición {position}: **{track.title}** "
                f"({format_duration(track.duration_seconds)})."
            )

    async def _play_next(self, guild_id: int) -> None:
        """Reproduce la siguiente pista de la cola, o queda a la espera si no hay más.

        Debe llamarse siempre con `state.lock` ya adquirido.
        """
        state = self._states.get(guild_id)
        if state is None or state.voice_client is None:
            return

        self._cancel_idle_task(state)

        track = state.queue.pop_next()
        if track is None:
            state.current = None
            self._schedule_idle_disconnect(guild_id)
            return

        state.current = track
        try:
            before_options = FFMPEG_BEFORE_OPTIONS
            headers = format_ffmpeg_headers(track.http_headers)
            if headers:
                # ffmpeg exige que el valor de -headers vaya entre comillas
                # y termine en salto de línea; se concatena tras el resto.
                before_options = f'{before_options} -headers "{headers}"'
            source = discord.FFmpegPCMAudio(
                track.stream_url,
                before_options=before_options,
                options=FFMPEG_OPTIONS,
            )
            volume_source = discord.PCMVolumeTransformer(
                source, volume=volume_percent_to_factor(state.volume_percent)
            )
        except discord.ClientException:
            logger.exception(
                "No se pudo iniciar ffmpeg para reproducir en el servidor %s", guild_id
            )
            await self._notify(state, f"⚠️ No se pudo reproducir **{track.title}**.")
            await self._play_next(guild_id)
            return

        def _after_playback(error: Exception | None) -> None:
            # discord.py invoca este callback desde el hilo del reproductor
            # de audio, nunca desde el event loop: hay que reencolar la
            # continuación como una tarea segura para hilos.
            asyncio.run_coroutine_threadsafe(self._advance(guild_id, error), self.bot.loop)

        state.voice_client.play(volume_source, after=_after_playback)

    async def _advance(self, guild_id: int, error: Exception | None) -> None:
        """Continúa la cola tras el fin de una pista, salvo que sea un `/stop`."""
        state = self._states.get(guild_id)
        if state is None:
            return

        async with state.lock:
            if state.stopping:
                state.stopping = False
                return
            if error is not None:
                logger.warning("Error de reproducción en el servidor %s: %s", guild_id, error)
            await self._play_next(guild_id)

    async def _notify(self, state: GuildMusicState, content: str) -> None:
        """Avisa en el último canal conocido; ignora fallos de envío."""
        if state.text_channel is None:
            return
        try:
            await state.text_channel.send(content)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo enviar un aviso de música", exc_info=True)

    def _cancel_idle_task(self, state: GuildMusicState) -> None:
        """Cancela el temporizador de desconexión por inactividad si existe."""
        if state.idle_task is not None and not state.idle_task.done():
            state.idle_task.cancel()
        state.idle_task = None

    def _schedule_idle_disconnect(self, guild_id: int) -> None:
        """Programa una desconexión tras `IDLE_DISCONNECT_SECONDS` sin cola."""
        state = self._states.get(guild_id)
        if state is None:
            return
        self._cancel_idle_task(state)
        state.idle_task = asyncio.create_task(self._idle_disconnect(guild_id))

    async def _idle_disconnect(self, guild_id: int) -> None:
        """Desconecta el canal de voz si sigue inactivo tras la espera."""
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
        except asyncio.CancelledError:
            return

        state = self._states.get(guild_id)
        if state is None or state.current is not None:
            return
        async with state.lock:
            if state.current is not None or len(state.queue) > 0:
                return
            await self._disconnect(state)

    async def _disconnect(self, state: GuildMusicState) -> None:
        """Detiene la reproducción, vacía la cola y sale del canal de voz."""
        self._cancel_idle_task(state)
        state.stopping = True
        state.queue.clear()
        state.current = None
        if state.voice_client is not None:
            if state.voice_client.is_playing() or state.voice_client.is_paused():
                state.voice_client.stop()
            try:
                await state.voice_client.disconnect(force=True)
            except discord.HTTPException:
                logger.warning("Error al desconectar del canal de voz", exc_info=True)
            state.voice_client = None

    @app_commands.command(name="pause", description="Pausa la canción actual.")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        """Pausa la reproducción actual sin vaciar la cola."""
        await self._pause_impl(InteractionResponder(interaction))

    @commands.command(name="pause")
    @commands.guild_only()
    async def pause_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._pause_impl(ContextResponder(ctx))

    async def _pause_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de pausar."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        if state.voice_client is None or not state.voice_client.is_playing():
            await responder.send_error("No hay ninguna pista reproduciéndose.")
            return
        state.voice_client.pause()
        await responder.send("⏸️ Reproducción pausada.")

    @app_commands.command(name="resume", description="Reanuda la canción pausada.")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        """Reanuda la reproducción si estaba en pausa."""
        await self._resume_impl(InteractionResponder(interaction))

    @commands.command(name="resume")
    @commands.guild_only()
    async def resume_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._resume_impl(ContextResponder(ctx))

    async def _resume_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de reanudar."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        if state.voice_client is None or not state.voice_client.is_paused():
            await responder.send_error("No hay ninguna pista en pausa.")
            return
        state.voice_client.resume()
        await responder.send("▶️ Reproducción reanudada.")

    @app_commands.command(name="skip", description="Salta a la siguiente canción.")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        """Detiene la pista actual; el callback de fin de pista encola la siguiente."""
        await self._skip_impl(InteractionResponder(interaction))

    @commands.command(name="skip")
    @commands.guild_only()
    async def skip_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._skip_impl(ContextResponder(ctx))

    async def _skip_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de saltar."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        if state.voice_client is None or not (
            state.voice_client.is_playing() or state.voice_client.is_paused()
        ):
            await responder.send_error("No hay ninguna pista que saltar.")
            return
        state.voice_client.stop()
        await responder.send("⏭️ Pista saltada.")

    @app_commands.command(name="stop", description="Detiene la música y desconecta al bot.")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        """Vacía la cola, detiene la reproducción y desconecta del canal de voz."""
        await self._stop_impl(InteractionResponder(interaction))

    @commands.command(name="stop")
    @commands.guild_only()
    async def stop_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._stop_impl(ContextResponder(ctx))

    async def _stop_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de parar."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        async with state.lock:
            await self._disconnect(state)
        await responder.send("⏹️ Música detenida y bot desconectado.")

    @app_commands.command(name="queue", description="Muestra la canción actual y la cola.")
    @app_commands.guild_only()
    async def queue_(self, interaction: discord.Interaction) -> None:
        """Lista la pista en curso y hasta diez pistas siguientes."""
        await self._queue_impl(InteractionResponder(interaction))

    @commands.command(name="queue")
    @commands.guild_only()
    async def queue_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._queue_impl(ContextResponder(ctx))

    async def _queue_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de mostrar la cola."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        state = self._get_state(guild.id)
        if state.current is None:
            await responder.send_error("No hay ninguna pista en reproducción ni en cola.")
            return

        lines = [
            f"▶️ **Ahora:** {state.current.title} "
            f"({format_duration(state.current.duration_seconds)}) — "
            f"pedida por {state.current.requested_by}"
        ]
        upcoming = state.queue.snapshot()
        for position, track in enumerate(upcoming[:10], start=1):
            lines.append(
                f"{position}. {track.title} ({format_duration(track.duration_seconds)}) — "
                f"pedida por {track.requested_by}"
            )
        if len(upcoming) > 10:
            lines.append(f"… y {len(upcoming) - 10} pista(s) más.")

        await responder.send("\n".join(lines))

    @app_commands.command(name="remove", description="Quita una canción de la cola.")
    @app_commands.guild_only()
    @app_commands.describe(posicion="Posición en /queue, empezando en 1.")
    async def remove(self, interaction: discord.Interaction, posicion: int) -> None:
        """Elimina la pista en la posición indicada sin afectar a la actual."""
        await self._remove_impl(InteractionResponder(interaction), posicion)

    @commands.command(name="remove")
    @commands.guild_only()
    async def remove_text(self, ctx: commands.Context, posicion: int) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._remove_impl(ContextResponder(ctx), posicion)

    async def _remove_impl(self, responder: CommandResponder, posicion: int) -> None:
        """Lógica compartida por todas las variantes de quitar una pista."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        async with state.lock:
            try:
                track = state.queue.remove_at(posicion)
            except IndexError:
                await responder.send_error(f"No hay ninguna pista en la posición {posicion}.")
                return
        await responder.send(f"🗑️ Se quitó de la cola: **{track.title}**.")

    @app_commands.command(name="clear", description="Vacía la cola sin parar la canción actual.")
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction) -> None:
        """Vacía la cola de pistas pendientes."""
        await self._clear_impl(InteractionResponder(interaction))

    @commands.command(name="clear")
    @commands.guild_only()
    async def clear_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._clear_impl(ContextResponder(ctx))

    async def _clear_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de limpiar la cola."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        async with state.lock:
            state.queue.clear()
        await responder.send("🧹 Cola vaciada.")

    @app_commands.command(name="volume", description="Cambia el volumen (1-200%).")
    @app_commands.guild_only()
    @app_commands.describe(valor="Porcentaje de volumen entre 1 y 200.")
    async def volume(
        self,
        interaction: discord.Interaction,
        valor: app_commands.Range[int, MIN_VOLUME_PERCENT, MAX_VOLUME_PERCENT],
    ) -> None:
        """Ajusta el volumen, incluso mientras una pista ya se reproduce."""
        await self._volume_impl(InteractionResponder(interaction), valor)

    @commands.command(name="volume")
    @commands.guild_only()
    async def volume_text(self, ctx: commands.Context, valor: int) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        if not (MIN_VOLUME_PERCENT <= valor <= MAX_VOLUME_PERCENT):
            await ctx.send(
                f"El volumen debe estar entre {MIN_VOLUME_PERCENT} y {MAX_VOLUME_PERCENT}%."
            )
            return
        await self._volume_impl(ContextResponder(ctx), valor)

    async def _volume_impl(
        self,
        responder: CommandResponder,
        valor: int,
    ) -> None:
        """Lógica compartida por todas las variantes de ajustar el volumen."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        state.volume_percent = valor
        if state.voice_client is not None and isinstance(
            state.voice_client.source, discord.PCMVolumeTransformer
        ):
            state.voice_client.source.volume = volume_percent_to_factor(valor)
        await responder.send(f"🔊 Volumen ajustado al {valor}%.")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Limpia el estado si el bot se queda solo o lo desconectan externamente."""
        state = self._states.get(member.guild.id)
        if state is None or state.voice_client is None:
            return

        if member.id == self.bot.user.id and after.channel is None:
            # El bot fue expulsado del canal o desconectado por otra vía;
            # se descarta la referencia para no operar sobre un cliente muerto.
            async with state.lock:
                state.voice_client = None
                state.current = None
                state.queue.clear()
                self._cancel_idle_task(state)
            return

        channel = state.voice_client.channel
        if channel is None or member.id == self.bot.user.id:
            return
        human_members = [candidate for candidate in channel.members if not candidate.bot]
        if not human_members:
            async with state.lock:
                await self._disconnect(state)

    async def cog_unload(self) -> None:
        """Cierra conexiones de voz y cancela temporizadores al descargar el cog."""
        for state in self._states.values():
            self._cancel_idle_task(state)
            if state.voice_client is not None:
                try:
                    await state.voice_client.disconnect(force=True)
                except discord.HTTPException:
                    logger.warning(
                        "Error al desconectar durante la descarga del cog", exc_info=True
                    )


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de música en el cliente."""
    await bot.add_cog(Music(bot))
