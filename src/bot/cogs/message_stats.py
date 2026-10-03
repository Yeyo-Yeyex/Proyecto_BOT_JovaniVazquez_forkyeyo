"""Registro de mensajes, experiencia y comandos de consulta de niveles.

Fuentes de XP en vivo (las reglas están en `bot.services.levels`):

- `on_message`: XP por mensaje, bonus del primer mensaje del día y racha.
- `on_raw_reaction_add`: XP para el autor de un mensaje cuando otro reacciona.
- `_voice_tick`: cada minuto, XP para quien está en voz sin mutear. Lee la
  caché de estados de voz de discord.py, así que no hace llamadas a la API.
  La misma tarea anuncia la hora feliz.

Subir de nivel paga yapdollars con retención de IRPF (`EconomyService`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import time
from collections import Counter, OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.repositories.message_stats import LevelAward, MessageStatsRepository
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    IncomeResult,
    format_amount,
    tax_line,
)
from bot.services.levels import (
    HAPPY_HOUR_MULTIPLIER,
    MAX_MESSAGE_XP,
    MAX_VOICE_XP,
    MIN_MESSAGE_XP,
    MIN_VOICE_XP,
    MemberActivity,
    calculate_level_progress,
    happy_hour,
    is_happy_hour,
    local_day,
    message_award,
    reaction_award,
    rewards_between,
    voice_award,
)
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

RANK_MEDALS = ("🥇", "🥈", "🥉")
RANKING_PAGE_SIZE = 10
#: Reacciones (mensaje, quien reacciona) ya premiadas que se recuerdan para
#: que quitar y volver a poner la misma reacción no dé XP otra vez.
REACTION_MEMORY = 5_000


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

    def __init__(
        self,
        bot: commands.Bot,
        repository: MessageStatsRepository,
        economy: EconomyService | None = None,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.economy = economy
        self._scan_tasks: dict[int, asyncio.Task[None]] = {}
        self._rewarded_reactions: OrderedDict[tuple[int, int], None] = OrderedDict()
        #: Último día (ISO) en que se anunció la hora feliz, por servidor.
        self._happy_hour_announced: dict[int, str] = {}

    async def cog_load(self) -> None:
        """Arranca la tarea de XP por voz."""
        self._voice_tick.start()

    async def cog_unload(self) -> None:
        """Detiene la tarea de XP por voz."""
        self._voice_tick.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Da XP por mensaje (con su enfriamiento); nunca guarda el contenido."""
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
            or message.is_system()
        ):
            return

        guild_id = message.guild.id
        now = time.time()
        base_xp = random.randint(MIN_MESSAGE_XP, MAX_MESSAGE_XP)
        happy = is_happy_hour(guild_id, now)

        def decide(_user_id: int, state: MemberActivity, cooldown: int) -> MemberActivity | None:
            return message_award(
                state, now=now, cooldown_seconds=cooldown, base_xp=base_xp, happy=happy
            )

        awards = await self.repository.grant_activity(guild_id, [message.author.id], decide)
        award = awards.get(message.author.id)
        if award is not None:
            await self._handle_level_up(message.guild, message.channel, message.author, award)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Da XP al autor de un mensaje cuando otra persona reacciona."""
        author_id = payload.message_author_id
        if (
            payload.guild_id is None
            or author_id is None
            or author_id == payload.user_id
            or payload.member is None
            or payload.member.bot
        ):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        author = guild.get_member(author_id)
        if author is None or author.bot:
            return
        key = (payload.message_id, payload.user_id)
        if key in self._rewarded_reactions:
            return
        self._rewarded_reactions[key] = None
        if len(self._rewarded_reactions) > REACTION_MEMORY:
            self._rewarded_reactions.popitem(last=False)

        now = time.time()

        def decide(_user_id: int, state: MemberActivity, _cooldown: int) -> MemberActivity | None:
            return reaction_award(state, now=now)

        awards = await self.repository.grant_activity(guild.id, [author_id], decide)
        award = awards.get(author_id)
        channel = guild.get_channel_or_thread(payload.channel_id)
        if award is not None and isinstance(channel, discord.abc.Messageable):
            await self._handle_level_up(guild, channel, author, award)

    # -- Voz y hora feliz ---------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def _voice_tick(self) -> None:
        """Cada minuto: XP por voz y aviso de la hora feliz."""
        try:
            enabled = await self.repository.enabled_guild_ids()
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer qué servidores tienen niveles")
            return
        now = time.time()
        for guild in self.bot.guilds:
            if guild.id not in enabled:
                continue
            try:
                await self._announce_happy_hour_if_due(guild, now)
                for channel in guild.voice_channels:
                    await self._award_voice_channel(guild, channel, now)
            except Exception:
                # Una excepción sin capturar pararía la tarea para siempre; se
                # registra y el siguiente minuto se vuelve a intentar.
                logger.exception("Error dando XP de voz en el servidor %s", guild.id)

    @_voice_tick.before_loop
    async def _before_voice_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def _award_voice_channel(
        self, guild: discord.Guild, channel: discord.VoiceChannel, now: float
    ) -> None:
        """Da XP de voz a quien esté hablando de verdad en `channel`."""
        if guild.afk_channel is not None and channel.id == guild.afk_channel.id:
            return
        eligible = [member for member in channel.members if is_voice_active(member)]
        # Hace falta alguien más para que cuente: nadie farmea solo.
        if len(eligible) < 2:
            return
        happy = is_happy_hour(guild.id, now)
        base = {member.id: random.randint(MIN_VOICE_XP, MAX_VOICE_XP) for member in eligible}

        def decide(user_id: int, state: MemberActivity, _cooldown: int) -> MemberActivity:
            return voice_award(state, now=now, base_xp=base[user_id], happy=happy)

        awards = await self.repository.grant_activity(guild.id, list(base), decide)
        by_id = {member.id: member for member in eligible}
        for user_id, award in awards.items():
            await self._handle_level_up(guild, channel, by_id[user_id], award)

    async def _announce_happy_hour_if_due(self, guild: discord.Guild, now: float) -> None:
        """Avisa una vez al día, al empezar la hora feliz, en el canal del sistema."""
        if not is_happy_hour(guild.id, now):
            return
        today = local_day(now).isoformat()
        if self._happy_hour_announced.get(guild.id) == today:
            return
        self._happy_hour_announced[guild.id] = today
        channel = guild.system_channel
        if channel is None:
            return
        end = happy_hour(guild.id, local_day(now)) + 1
        try:
            await channel.send(
                f"🎉 **¡Hora feliz!** Hasta las {end % 24:02d}:00 los mensajes y la voz "
                f"dan XP ×{HAPPY_HOUR_MULTIPLIER}."
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo anunciar la hora feliz en el servidor %s", guild.id, exc_info=True
            )

    # -- Subidas de nivel ---------------------------------------------------------------

    async def _handle_level_up(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        member: discord.abc.User,
        award: LevelAward,
    ) -> None:
        """Si `award` hace subir de nivel, paga el premio y lo anuncia."""
        previous_level = calculate_level_progress(award.previous_xp).level
        current_level = calculate_level_progress(award.total_xp).level
        if current_level <= previous_level:
            return
        reward = await self._pay_level_reward(guild, member, previous_level, current_level)
        await self._announce_level_up(guild, channel, member, current_level, reward=reward)

    async def _pay_level_reward(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        previous_level: int,
        current_level: int,
    ) -> IncomeResult | None:
        """Paga los yapdollars de los niveles alcanzados; `None` si no se pudo."""
        gross = rewards_between(previous_level, current_level)
        if self.economy is None or gross <= 0:
            return None
        try:
            return await self.economy.pay_income(
                guild.id, member.id, gross=gross, concept=f"nivel:{current_level}"
            )
        except (OSError, sqlite3.Error, BalanceLimitError):
            logger.exception(
                "No se pudo pagar el premio de nivel %s a %s en el servidor %s",
                current_level,
                member.id,
                guild.id,
            )
            return None

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
        *,
        reward: IncomeResult | None = None,
    ) -> None:
        """Publica el aviso en el canal donde se ganó el nivel, con el premio cobrado."""
        text = (
            f"¡{discord.utils.escape_markdown(member.display_name)} "
            f"ha alcanzado el nivel **{level}**!"
        )
        if reward is not None:
            text += (
                f" {CURRENCY_EMOJI} +{format_amount(reward.net)}\n"
                f"{tax_line(reward.gross, reward.tax, reward.rate)}"
            )
        try:
            await channel.send(
                text,
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


def is_voice_active(member: discord.Member) -> bool:
    """Si un miembro cuenta para el XP de voz: persona, sin mute ni ensordecer."""
    voice = member.voice
    return (
        not member.bot
        and voice is not None
        and not (voice.self_mute or voice.self_deaf or voice.mute or voice.deaf)
    )


async def setup(bot: commands.Bot) -> None:
    """Registra los comandos de estadísticas y el contador de mensajes en vivo."""
    repository = bot.message_stats
    await bot.add_cog(MessageStats(bot, repository, getattr(bot, "economy", None)))
