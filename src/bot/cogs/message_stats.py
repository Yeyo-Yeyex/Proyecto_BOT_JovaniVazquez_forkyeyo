"""Registro de mensajes, experiencia y comandos de consulta de niveles."""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.repositories.message_stats import MessageStatsRepository
from bot.services.levels import (
    MAX_MESSAGE_XP,
    MIN_MESSAGE_XP,
    calculate_level_progress,
)
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

RANK_MEDALS = ("🥇", "🥈", "🥉")
RANKING_PAGE_SIZE = 10


def build_ranking_embed(
    guild: discord.Guild,
    page: int,
    entries: Sequence[tuple[int, str, int]],
) -> discord.Embed:
    """Construye una tarjeta visual de ranking con podio y progreso de nivel.

    Args:
        guild: Servidor cuyo ranking se presenta.
        page: Número de página, empezando por 1.
        entries: Posición global, nombre legible y XP total de cada usuario.

    Returns:
        Embed listo para enviarse como respuesta de Discord.
    """
    embed = discord.Embed(
        title="🏆 Ranking de niveles",
        description="Los miembros con más experiencia de este servidor.",
        color=discord.Color.from_rgb(242, 177, 52),
    )
    embed.set_author(
        name=guild.name,
        icon_url=guild.icon.url if guild.icon is not None else None,
    )
    if guild.icon is not None:
        embed.set_thumbnail(url=guild.icon.url)

    for index, (_position, display_name, total_xp) in enumerate(entries[:3]):
        progress = calculate_level_progress(total_xp)
        progress_bar = _format_progress_bar(progress.xp_in_level, progress.xp_for_next_level)
        medal = RANK_MEDALS[index]
        embed.add_field(
            name=f"{medal} {display_name}",
            value=(
                f"**Nivel {progress.level}** · {total_xp:,} XP\n"
                f"`{progress_bar}`\n"
                f"{progress.xp_in_level:,}/{progress.xp_for_next_level:,} al siguiente"
            ),
            inline=True,
        )

    remaining = entries[3:]
    if remaining:
        lines = []
        for position, display_name, total_xp in remaining:
            progress = calculate_level_progress(total_xp)
            lines.append(
                f"**#{position} · {display_name}**\n"
                f"Nivel {progress.level}　·　{total_xp:,} XP　·　"
                f"{progress.xp_in_level:,}/{progress.xp_for_next_level:,} al siguiente"
            )
        embed.add_field(name="Clasificación", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Página {page} · Se muestran hasta {RANKING_PAGE_SIZE} miembros")
    return embed


def _format_progress_bar(current_xp: int, required_xp: int, width: int = 10) -> str:
    """Representa el progreso del nivel como una barra compacta."""
    filled = min(width, int(current_xp / required_xp * width))
    return "█" * filled + "░" * (width - filled)


class MessageStats(commands.Cog):
    """Registra XP por mensaje y expone consultas de nivel y ranking."""

    def __init__(self, bot: commands.Bot, repository: MessageStatsRepository) -> None:
        self.bot = bot
        self.repository = repository
        self._scan_tasks: dict[int, asyncio.Task[None]] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Guarda cada mensaje nuevo elegible como agregado, sin su contenido."""
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
            or message.is_system()
        ):
            return

        award = await self.repository.award_message_xp(
            message.guild.id,
            message.author.id,
            random.randint(MIN_MESSAGE_XP, MAX_MESSAGE_XP),
            time.time(),
        )
        if award is None:
            return

        previous_level = calculate_level_progress(award.previous_xp).level
        current_level = calculate_level_progress(award.total_xp).level
        if current_level > previous_level:
            await self._announce_level_up(
                message.guild,
                message.channel,
                message.author,
                current_level,
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las estadísticas del servidor cuando el bot deja de pertenecer a él."""
        await self.repository.delete_guild_data(guild.id)
        logger.info("Se eliminaron las estadísticas del servidor %s", guild.id)

    async def import_history(self, interaction: discord.Interaction) -> None:
        """Inicia o reanuda una importación histórica en segundo plano.

        Requiere permiso de administrar el servidor. La tarea se separa de
        la interacción para que pueda durar más que el tiempo de respuesta
        de un comando de Discord.
        """
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Este comando solo está disponible dentro de un servidor.",
                ephemeral=True,
            )
            return

        current_task = self._scan_tasks.get(guild.id)
        if current_task is not None and not current_task.done():
            await interaction.response.send_message(
                "Ya hay una importación en curso. Consulta `/niveles importacion`.",
                ephemeral=True,
            )
            return

        existing_status = await self.repository.import_status(guild.id)
        if existing_status is not None and existing_status.status in {
            "running",
            "completed",
        }:
            if existing_status.status == "completed":
                text = (
                    "La importación histórica de este servidor ya terminó. "
                    "No se repetirá para evitar duplicar recuentos."
                )
            else:
                text = "Ya existe una importación activa. Consulta `/niveles importacion`."
            await interaction.response.send_message(text, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        channels = await self._discover_readable_channels(guild)
        cutoff_id = discord.utils.time_snowflake(datetime.now(UTC), high=False)
        started = await self.repository.start_import(guild.id, list(channels), cutoff_id)
        if not started:
            status = await self.repository.import_status(guild.id)
            if status is not None and status.status == "completed":
                message = (
                    "La importación histórica de este servidor ya terminó. "
                    "No se repetirá para evitar duplicar recuentos."
                )
            else:
                message = "Ya existe una importación activa. Consulta `/niveles importacion`."
            await interaction.edit_original_response(content=message)
            return

        task = asyncio.create_task(
            self._run_import(guild, channels),
            name=f"message-history-import:{guild.id}",
        )
        self._scan_tasks[guild.id] = task
        await interaction.edit_original_response(
            content=(
                f"Importación iniciada para {len(channels)} canales e hilos "
                "accesibles. Puedes consultar el avance con "
                "`/niveles importacion`."
            )
        )

    async def import_status(self, interaction: discord.Interaction) -> None:
        """Muestra el estado persistido de la importación del servidor actual."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Este comando solo está disponible dentro de un servidor.",
                ephemeral=True,
            )
            return

        status = await self.repository.import_status(guild.id)
        if status is None:
            text = "Todavía no se ha iniciado una importación histórica."
        else:
            labels = {
                "running": "en curso",
                "completed": "completada",
                "partial": "parcial",
                "interrupted": "interrumpida; puedes reanudarla con `/niveles importar`",
            }
            text = (
                f"Importación {labels.get(status.status, status.status)}: "
                f"{status.scanned_channels}/{status.total_channels} canales "
                f"completados; {status.messages_scanned:,} mensajes revisados, "
                f"{status.messages_counted:,} de usuarios contados; "
                f"{status.failed_channels} canales con errores."
            )
            if status.failed_channels:
                text += (
                    " Revisa los permisos `Ver canal` y `Leer el historial "
                    "de mensajes` en los canales afectados."
                )

        await interaction.response.send_message(text, ephemeral=True)

    async def message_count(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member | None = None,
    ) -> None:
        """Consulta el total histórico y en vivo de un miembro del servidor."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Este comando solo está disponible dentro de un servidor.",
                ephemeral=True,
            )
            return

        member = miembro or interaction.user
        count = await self.repository.message_count(guild.id, member.id)
        await interaction.response.send_message(
            f"{member.mention} tiene **{count:,} mensajes** registrados en este servidor.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _level_impl(
        self,
        responder: CommandResponder,
        miembro: discord.Member | None,
    ) -> None:
        """Lógica compartida entre `/level` y `.level`."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        member = miembro or responder.member
        if member is None:
            await responder.send_error("No se pudo identificar a quién consultar.")
            return

        settings = await self.repository.level_settings(guild.id)
        if settings is None or not settings.historical_seeded:
            await responder.send_error(
                "Los niveles todavía no están inicializados en este servidor."
            )
            return

        total_xp = await self.repository.member_xp(guild.id, member.id)
        progress = calculate_level_progress(total_xp)
        await responder.send(
            f"**{discord.utils.escape_markdown(member.display_name)}** — "
            f"nivel **{progress.level}**, {total_xp:,} XP. "
            f"Progreso: {progress.xp_in_level:,}/{progress.xp_for_next_level:,} XP "
            "hacia el siguiente nivel.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="level", description="Consulta tu nivel o el de otro miembro.")
    @app_commands.guild_only()
    async def level(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member | None = None,
    ) -> None:
        """Muestra nivel, XP total y avance hacia el siguiente nivel."""
        await self._level_impl(InteractionResponder(interaction), miembro)

    @commands.command(name="level")
    @commands.guild_only()
    async def level_text(
        self,
        ctx: commands.Context,
        miembro: discord.Member | None = None,
    ) -> None:
        """Versión de texto (`.level`) de `/level`."""
        await self._level_impl(ContextResponder(ctx), miembro)

    async def _ranking_impl(self, responder: CommandResponder, pagina: int) -> None:
        """Lógica compartida entre `/top` y `.top`."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        settings = await self.repository.level_settings(guild.id)
        if settings is None or not settings.historical_seeded:
            await responder.send_error(
                "Los niveles todavía no están inicializados en este servidor."
            )
            return

        page_size = RANKING_PAGE_SIZE
        entries = await self.repository.level_leaderboard(
            guild.id, page_size, (pagina - 1) * page_size
        )
        if not entries:
            await responder.send_error(f"No hay perfiles en la página {pagina}.")
            return

        await responder.start_progress("🔄 Preparando el ranking...")
        name_semaphore = asyncio.Semaphore(5)

        async def resolve_entry(position: int, entry: tuple[int, int]) -> tuple[int, str, int]:
            user_id, total_xp = entry
            async with name_semaphore:
                display_name = await self._resolve_display_name(guild, user_id)
            return position, display_name, total_xp

        resolved_entries = await asyncio.gather(
            *(
                resolve_entry(position, entry)
                for position, entry in enumerate(
                    entries,
                    start=(pagina - 1) * page_size + 1,
                )
            )
        )

        await responder.finish(
            embed=build_ranking_embed(guild, pagina, resolved_entries),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="top", description="Muestra el ranking del servidor.")
    @app_commands.guild_only()
    async def ranking(
        self,
        interaction: discord.Interaction,
        pagina: app_commands.Range[int, 1, 100] = 1,
    ) -> None:
        """Muestra hasta diez perfiles por página, ordenados por XP."""
        await self._ranking_impl(InteractionResponder(interaction), pagina)

    @commands.command(name="top")
    @commands.guild_only()
    async def ranking_text(self, ctx: commands.Context, pagina: int = 1) -> None:
        """Versión de texto (`.top`) de `/top`."""
        if not (1 <= pagina <= 100):
            await ctx.send("La página debe estar entre 1 y 100.")
            return
        await self._ranking_impl(ContextResponder(ctx), pagina)

    async def _resolve_display_name(self, guild: discord.Guild, user_id: int) -> str:
        """Resuelve el nombre actual del miembro o su nombre global de Discord.

        Los miembros que no están en caché se consultan individualmente para
        no requerir el intent privilegiado de lista de miembros. Si el usuario
        ya no pertenece al servidor, se intenta obtener su nombre global.
        """
        member = guild.get_member(user_id)
        if member is not None:
            return discord.utils.escape_markdown(member.display_name)

        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            member = None
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo resolver el miembro %s en el servidor %s",
                user_id,
                guild.id,
                exc_info=True,
            )
            member = None

        if member is not None:
            return discord.utils.escape_markdown(member.display_name)

        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            logger.info("La cuenta Discord %s ya no está disponible", user_id)
            return "Cuenta no disponible"
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo obtener el nombre global del usuario %s",
                user_id,
                exc_info=True,
            )
            return "Nombre no disponible"

        return discord.utils.escape_markdown(user.global_name or user.name)

    async def _announce_level_up(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        member: discord.abc.User,
        level: int,
    ) -> None:
        """Publica el aviso en el mismo canal donde se ganó el nivel."""
        try:
            await channel.send(
                f"¡{discord.utils.escape_markdown(member.display_name)} "
                f"ha alcanzado el nivel **{level}**!",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo publicar la subida de nivel en canal %s del servidor %s",
                getattr(channel, "id", "desconocido"),
                guild.id,
                exc_info=True,
            )

    async def _discover_readable_channels(
        self, guild: discord.Guild
    ) -> dict[int, discord.abc.Messageable]:
        """Reúne canales de texto e hilos accesibles, incluidos hilos archivados."""
        channels: dict[int, discord.abc.Messageable] = {}
        for channel in guild.text_channels:
            channels[channel.id] = channel
        for thread in guild.threads:
            channels[thread.id] = thread

        parents = [
            channel
            for channel in guild.channels
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel))
        ]
        for parent in parents:
            await self._add_archived_threads(parent, channels, private=False)
            await self._add_archived_threads(parent, channels, private=True)

        return channels

    async def _add_archived_threads(
        self,
        parent: discord.TextChannel | discord.ForumChannel,
        channels: dict[int, discord.abc.Messageable],
        *,
        private: bool,
    ) -> None:
        """Añade hilos archivados; las restricciones de acceso quedan explícitas en logs."""
        if isinstance(parent, discord.ForumChannel):
            if private:
                return
            joined_modes = (False,)
        else:
            joined_modes = (True, False) if private else (False,)

        for joined in joined_modes:
            try:
                if isinstance(parent, discord.ForumChannel):
                    archived_threads: AsyncIterator[discord.Thread] = parent.archived_threads(
                        limit=None
                    )
                else:
                    archived_threads = parent.archived_threads(
                        limit=None,
                        private=private,
                        joined=joined,
                    )
                async for thread in archived_threads:
                    channels[thread.id] = thread
            except (discord.Forbidden, discord.HTTPException) as error:
                logger.info(
                    "No se pudieron enumerar hilos archivados del canal %s "
                    "(privados=%s, joined=%s): %s",
                    parent.id,
                    private,
                    joined,
                    error,
                )

    async def _run_import(
        self,
        guild: discord.Guild,
        channels: dict[int, discord.abc.Messageable],
    ) -> None:
        """Cuenta cada canal pendiente y guarda su agregado de forma reanudable."""
        guild_id = guild.id
        try:
            status = await self.repository.import_status(guild_id)
            if status is None:
                logger.error("Falta el estado de importación del servidor %s", guild_id)
                return

            channel_by_id = channels
            for channel_id in await self.repository.pending_channel_ids(guild_id):
                channel = channel_by_id.get(channel_id)
                if channel is None:
                    await self.repository.mark_channel_failed(
                        guild_id, channel_id, "El canal ya no existe o no es accesible."
                    )
                    continue

                counts: Counter[int] = Counter()
                messages_scanned = 0
                messages_counted = 0
                await self.repository.update_channel_progress(guild_id, channel_id, 0, 0)
                try:
                    history = channel.history(
                        limit=None,
                        before=discord.Object(id=status.cutoff_id),
                        oldest_first=False,
                    )
                    async for message in history:
                        messages_scanned += 1
                        if (
                            message.author.bot
                            or message.webhook_id is not None
                            or message.is_system()
                        ):
                            continue
                        counts[message.author.id] += 1
                        messages_counted += 1
                        if messages_scanned % 100 == 0:
                            await self.repository.update_channel_progress(
                                guild_id,
                                channel_id,
                                messages_scanned,
                                messages_counted,
                            )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
                    await self.repository.mark_channel_failed(
                        guild_id, channel_id, type(error).__name__
                    )
                    logger.warning(
                        "No se pudo leer el historial del canal %s en el servidor %s: %s",
                        channel_id,
                        guild_id,
                        error,
                    )
                    continue

                await self.repository.update_channel_progress(
                    guild_id,
                    channel_id,
                    messages_scanned,
                    messages_counted,
                )
                await self.repository.save_channel_counts(guild_id, channel_id, dict(counts))

            await self.repository.finish_import(guild_id)
            final_status = await self.repository.import_status(guild_id)
            if final_status is not None:
                logger.info(
                    "Importación histórica del servidor %s: estado=%s, canales=%d/%d, fallidos=%d",
                    guild_id,
                    final_status.status,
                    final_status.scanned_channels,
                    final_status.total_channels,
                    final_status.failed_channels,
                )
        except asyncio.CancelledError:
            raise
        except (OSError, sqlite3.Error):
            logger.exception(
                "Error de almacenamiento durante la importación del servidor %s",
                guild_id,
            )
            await self.repository.mark_import_interrupted(guild_id)
        except Exception:
            logger.exception(
                "Error inesperado durante la importación del servidor %s",
                guild_id,
            )
            await self.repository.mark_import_interrupted(guild_id)
        finally:
            self._scan_tasks.pop(guild_id, None)


async def setup(bot: commands.Bot) -> None:
    """Registra los comandos de estadísticas y el contador de mensajes en vivo."""
    repository = bot.message_stats
    await bot.add_cog(MessageStats(bot, repository))
