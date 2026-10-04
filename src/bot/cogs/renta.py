"""Campaña de la Renta: declaración semanal del IRPF del casino.

Durante la semana el casino retiene día a día. Al cerrarse la semana, las
pérdidas de unos días compensan las ganancias de otros y lo retenido de
más sale a devolver (ver `bot.services.taxes.weekly_refund`). Para cobrarlo
hay que presentar la declaración:

- **Desde el casino:** la primera vez en la campaña que alguien juega con la
  renta pendiente, le sale un mensaje que solo ve él con un botón
  **Presentar**. En las jugadas siguientes solo queda una línea pequeña en
  el resultado. Discord solo permite mensajes efímeros como respuesta a un
  botón o a un comando slash, así que se enganchan a esas acciones.
- **Con `renta`:** muestra el borrador y el mismo botón.

Al presentar, el bot publica una línea en el canal ("Diego presenta la
renta: le salen 2.340 Y$ a devolver") para que se compare.

Se guardan como máximo las 2 últimas semanas pendientes, sin fecha de
caducidad; cuando sale a devolver una tercera, la más antigua se pierde.

Cada semana el bot crea un evento de Discord ("📬 Campaña de la Renta") que
dura hasta el domingo, para que se vea sin ocupar un canal. Necesita el
permiso **Gestionar eventos**; sin él, todo lo demás funciona igual.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.services.economy import (
    Declaration,
    EconomyService,
    format_amount,
    week_label,
    week_start,
)
from bot.services.levels import TIMEZONE, local_day
from bot.services.taxes import MAX_PENDING_DECLARATIONS, TAX_COLLECTOR

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

EVENT_PREFIX = "📬 Campaña de la Renta"
VIEW_TIMEOUT = 300


def draft_text(declarations: list[Declaration]) -> str:
    """Borrador de la declaración para mostrar a su dueño."""
    total = sum(d.refund for d in declarations)
    lines = [
        f"📬 **Tienes la renta sin presentar: te salen {format_amount(total)} a devolver.**",
    ]
    if len(declarations) > 1:
        lines += [
            f"-# Semana {week_label(d.week_start)}: {format_amount(d.refund)}" for d in declarations
        ]
    lines.append(
        f"-# Se guardan las {MAX_PENDING_DECLARATIONS} últimas semanas sin presentar; "
        f"si se acumula otra, la más antigua se la queda {TAX_COLLECTOR}."
    )
    return "\n".join(lines)


def pending_hint() -> str:
    """Línea pequeña para el resultado de una jugada si hay renta pendiente."""
    return "-# 📬 Tienes la renta sin presentar: `/renta`"


async def remind(bot: commands.Bot, interaction: discord.Interaction) -> None:
    """Atajo para el casino: avisa de la renta pendiente si el cog está cargado."""
    cog = bot.get_cog("Renta")
    if isinstance(cog, Renta):
        await cog.remind(interaction)


async def hint(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """Atajo para el casino: línea de renta pendiente si el cog está cargado."""
    cog = bot.get_cog("Renta")
    if isinstance(cog, Renta):
        return await cog.hint_for(guild_id, user_id)
    return None


class PresentView(discord.ui.View):
    """Botón Presentar del borrador; solo lo puede pulsar su dueño."""

    def __init__(self, cog: Renta, owner_id: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esta declaración no es tuya.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Presentar", emoji="📬", style=discord.ButtonStyle.success)
    async def present(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.cog.present(interaction)
        self.stop()


class Renta(commands.Cog):
    """Declaración semanal del casino, recordatorios y evento de campaña."""

    def __init__(self, bot: commands.Bot, economy: EconomyService) -> None:
        self.bot = bot
        self.economy = economy
        #: `(servidor, usuario, lunes de la campaña)` a quienes ya se avisó con
        #: el efímero; en esta campaña solo verán la línea pequeña.
        self._reminded: set[tuple[int, int, date]] = set()
        self._events_forbidden: set[int] = set()

    async def cog_load(self) -> None:
        self._campaign_events.start()

    async def cog_unload(self) -> None:
        self._campaign_events.cancel()

    # -- Recordatorios desde el casino -----------------------------------------------

    async def hint_for(self, guild_id: int, user_id: int) -> str | None:
        """Línea pequeña si el usuario tiene renta pendiente; `None` si no."""
        try:
            pending = await self.economy.pending_declarations(guild_id, user_id)
        except Exception:
            logger.exception("No se pudo consultar la renta pendiente")
            return None
        return pending_hint() if pending else None

    async def remind(self, interaction: discord.Interaction) -> None:
        """Tras una jugada con botón o slash: avisa en efímero, una vez por campaña.

        Debe llamarse cuando la interacción ya tiene respuesta: el aviso va
        como mensaje de seguimiento que solo ve quien ha jugado.
        """
        guild = interaction.guild
        if guild is None or not interaction.response.is_done():
            return
        campaign = week_start(local_day(time.time()))
        key = (guild.id, interaction.user.id, campaign)
        if key in self._reminded:
            return
        try:
            pending = await self.economy.pending_declarations(guild.id, interaction.user.id)
        except Exception:
            logger.exception("No se pudo consultar la renta pendiente")
            return
        if not pending:
            return
        self._reminded.add(key)
        try:
            await interaction.followup.send(
                draft_text(pending),
                view=PresentView(self, interaction.user.id),
                ephemeral=True,
            )
        except discord.HTTPException:
            logger.warning("No se pudo enviar el aviso de renta", exc_info=True)

    # -- Presentar ---------------------------------------------------------------------

    async def present(self, interaction: discord.Interaction) -> None:
        """Cobra la devolución y lo anuncia en el canal."""
        guild = interaction.guild
        if guild is None:
            return
        claim = await self.economy.claim_declarations(guild.id, interaction.user.id)
        if not claim.declarations:
            await interaction.response.edit_message(
                content="No tienes ninguna declaración pendiente.", view=None
            )
            return
        await interaction.response.edit_message(
            content=(
                f"✅ Declaración presentada: +{format_amount(claim.refunded)}. "
                f"Saldo: **{format_amount(claim.balance)}**."
            ),
            view=None,
        )
        weeks = len(claim.declarations)
        extra = f" ({weeks} semanas)" if weeks > 1 else ""
        channel = interaction.channel
        if channel is not None and hasattr(channel, "send"):
            try:
                await channel.send(  # type: ignore[union-attr]
                    f"📬 **{discord.utils.escape_markdown(interaction.user.display_name)}** "
                    f"presenta la renta: le salen **{format_amount(claim.refunded)}** "
                    f"a devolver{extra}.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning("No se pudo anunciar la declaración", exc_info=True)

    # -- Comando -----------------------------------------------------------------------

    async def _draft(
        self, guild: discord.Guild | None, member: discord.abc.User | None
    ) -> tuple[str, PresentView | None]:
        """Texto del borrador y su botón (o `None` si no hay nada que presentar)."""
        if guild is None or member is None:
            return "La renta solo se presenta dentro de un servidor.", None
        pending = await self.economy.pending_declarations(guild.id, member.id)
        if not pending:
            return (
                "📬 No tienes nada que declarar. Las semanas en las que el casino te "
                "retiene de más salen a devolver el lunes siguiente.",
                None,
            )
        return draft_text(pending), PresentView(self, member.id)

    @app_commands.command(name="renta", description="Mira y presenta tu declaración de la renta.")
    @app_commands.guild_only()
    async def renta(self, interaction: discord.Interaction) -> None:
        """Borrador de la declaración semanal con el botón para presentarla."""
        text, view = await self._draft(interaction.guild, interaction.user)
        if view is None:
            await interaction.response.send_message(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, view=view, ephemeral=True)

    @commands.command(name="renta")
    @commands.guild_only()
    async def renta_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.renta`); se ve en el canal porque no puede ser efímera."""
        text, view = await self._draft(ctx.guild, ctx.author)
        if view is None:
            await ctx.send(text)
        else:
            await ctx.send(text, view=view)

    # -- Evento semanal ----------------------------------------------------------------

    @tasks.loop(minutes=10)
    async def _campaign_events(self) -> None:
        """Mantiene un evento de Discord por campaña: lo crea, lo abre y cierra el viejo."""
        now = datetime.now(TIMEZONE)
        monday = week_start(now.date())
        name = f"{EVENT_PREFIX} · semana {week_label(monday - timedelta(days=7))}"
        end = datetime.combine(monday + timedelta(days=7), datetime.min.time(), TIMEZONE)
        for guild in self.bot.guilds:
            if guild.id in self._events_forbidden:
                continue
            try:
                await self._sync_guild_event(guild, name, now, end)
            except discord.Forbidden:
                self._events_forbidden.add(guild.id)
                logger.warning(
                    "Sin permiso para gestionar eventos en el servidor %s; "
                    "la renta funciona sin evento.",
                    guild.id,
                )
            except Exception:
                logger.exception("Error con el evento de la renta en el servidor %s", guild.id)

    @_campaign_events.before_loop
    async def _before_campaign_events(self) -> None:
        await self.bot.wait_until_ready()

    async def _sync_guild_event(
        self, guild: discord.Guild, name: str, now: datetime, end: datetime
    ) -> None:
        current: discord.ScheduledEvent | None = None
        for event in guild.scheduled_events:
            if not event.name.startswith(EVENT_PREFIX):
                continue
            if event.name == name:
                current = event
            elif event.status is discord.EventStatus.active:
                await event.end()
            elif event.status is discord.EventStatus.scheduled:
                await event.cancel()
        if current is None:
            await guild.create_scheduled_event(
                name=name,
                start_time=now + timedelta(minutes=1),
                end_time=end,
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
                location="/renta o jugando en el casino",
                description=(
                    "Ya puedes presentar la declaración de la semana pasada. Lo que el "
                    "casino te retuvo de más sale a devolver: preséntala con /renta o "
                    "jugando en el casino. Las 2 últimas semanas sin presentar se "
                    f"guardan; si se acumula otra, la más antigua se la queda {TAX_COLLECTOR}."
                ),
            )
        elif current.status is discord.EventStatus.scheduled and current.start_time <= now:
            await current.start()


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Renta(bot, bot.economy))
