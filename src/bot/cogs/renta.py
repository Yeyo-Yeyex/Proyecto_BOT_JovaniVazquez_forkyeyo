"""Campaña de la Renta: la declaración del IRPF de cada semana.

El año fiscal del bot es la semana. Durante la semana cada cobro retiene a cuenta
(las nóminas, los premios y los alquileres con el tipo de cada uno; el casino, el 19
% de lo ganado cada día). Cada lunes este cog cierra la semana anterior en cada
servidor (`EconomyService.close_irpf_weeks`): sale la declaración de cada miembro con
toda su renta, sus deducciones y lo retenido.

- **Si sale a pagar**, se cobra sola del monedero en ese momento; lo que no llegue
  queda como deuda con Hacienda y se cobra el lunes siguiente o de lo que rente en
  `pisos`.
- **Si sale a devolver**, primero paga lo que debiera y el resto hay que presentarlo,
  desde el casino (la primera vez en la campaña que alguien juega con la renta
  pendiente le sale un mensaje que solo ve él con el botón **Presentar**; luego solo
  una línea pequeña) o con `renta`. Al presentar, el bot publica una línea en el canal
  para que se compare. Hay `CLAIM_WEEKS` semanas para presentarla (los cuatro años de
  la ley); después prescribe y el dinero se queda en el Estado.

**Expectativa.** Como en la vida real, quien no lleva la cuenta de todo lo que cobra no
sabe hasta el lunes si le toca pagar o le devuelven: `renta` no adelanta el resultado
de la semana en curso. El lunes el anuncio de `#chat-general` (o del canal del
sistema) solo da los totales del servidor y a cada miembro le llega una vez, en su
siguiente acción con dinero (`hint`), que ya tiene la declaración, sin la cifra. La
abre en `renta`: un sobre con el botón **Abrir** y, al abrirlo, el **resumen** (las
cuentas en cinco líneas) con un botón para ver el **detalle** (el porqué de cada cifra,
con sus artículos) y otro para volver (`bot.services.renta_report`).

Cada semana el bot crea un evento de Discord ("📬 Campaña de la Renta") que dura hasta
el domingo, para que se vea sin ocupar un canal. Necesita el permiso **Gestionar
eventos**; sin él, todo lo demás funciona igual.

Las semanas de antes de la 8.0.0 en un servidor se declaran con la regla vieja (solo
el casino y los donativos, siempre a devolver), al vuelo.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import intereses
from bot.cogs import pets as mascotas
from bot.services.achievements import StatDelta
from bot.services.economy import (
    Declaration,
    EconomyService,
    IrpfRun,
    IrpfStatement,
    format_amount,
    week_label,
    week_start,
)
from bot.services.levels import TIMEZONE, local_day
from bot.services.pets import Moment
from bot.services.renta_report import detail_text, envelope_text, summary_text
from bot.services.taxes import CLAIM_WEEKS, TAX_COLLECTOR
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

EVENT_PREFIX = "📬 Campaña de la Renta"
VIEW_TIMEOUT = 600
ANNOUNCE_CHANNEL_NAME = "chat-general"
COLOR = discord.Color.from_rgb(170, 21, 27)
#: Límite del texto de un embed de Discord.
EMBED_LIMIT = 4_096

#: Modos de la vista de `renta`.
SEALED = "sobre"
SUMMARY = "resumen"
DETAIL = "detalle"

#: Frases del anuncio del lunes, según cómo le haya ido a Hacienda.
MONDAY_GOOD: tuple[str, ...] = (
    "Montero sonríe desde Sevilla: la recaudación vuelve a batir récord.",
    "Sanxe se compra otro Falcon con lo de esta semana. Bueno, el depósito.",
    "Hacienda somos todos, pero esta semana unos más que otros.",
    "La Agencia Tributaria agradece su colaboración y le recuerda que no tenía elección.",
    "Récord de recaudación: el Gobierno lo atribuye al «Manual de resistencia».",
    "Bruselas aplaude el ajuste. El ajuste eras tú.",
)
MONDAY_REFUND: tuple[str, ...] = (
    "Hacienda devuelve dinero: el CIS lo llama «escudo social», Feijóo, «limosna».",
    "Esta semana el Estado sale perdiendo. Ya lo recuperará con el IBI.",
    "Devoluciones a mansalva: alguien en Moncloa ha confundido el botón.",
    "Sanxe devuelve lo retenido de más. Tranquilos, solo es lo vuestro.",
)
#: Frases del aviso de que ya está la declaración (sin decir qué sale).
NOTICE_LINES: tuple[str, ...] = (
    "Hacienda ya ha hecho tus cuentas de la semana {week}. ¿Pagas o te devuelven?",
    "Ha llegado la carta de {collector} con tu renta de la semana {week}. Ábrela si te atreves.",
    "Tu declaración de la semana {week} está lista. Respira hondo.",
    "{collector} ha terminado de sumar tu semana {week}. Él ya sabe el resultado; tú todavía no.",
)


def draft_text(declarations: list[Declaration]) -> str:
    """Borrador de las declaraciones a devolver, para mostrar a su dueño."""
    total = sum(d.refund for d in declarations)
    lines = [
        f"📬 **Tienes la renta sin presentar: te salen {format_amount(total)} a devolver.**",
    ]
    if len(declarations) > 1:
        lines += [
            f"-# Semana {week_label(d.week_start)}: {format_amount(d.refund)}" for d in declarations
        ]
    lines.append(
        f"-# Hay {CLAIM_WEEKS} semanas para presentar cada una (los cuatro años de la ley); "
        f"después prescribe y se la queda {TAX_COLLECTOR}."
    )
    return "\n".join(lines)


def notice_text(week: date) -> str:
    """Aviso de que ya está una declaración, sin decir qué sale (para `hint`)."""
    phrase = NOTICE_LINES[week.toordinal() // 7 % len(NOTICE_LINES)]
    text = phrase.format(week=week_label(week), collector=TAX_COLLECTOR)
    return f"-# 📑 {text} Ábrela con `/renta`."


def announcement_text(week: date, run: IrpfRun) -> str:
    """Anuncio público del cierre de una semana: solo los totales, sin nombres.

    Cada uno descubre lo suyo al abrir su declaración: nadie se entera antes por el canal.
    """
    pay = [r for r in run.returns if r.result > 0]
    back = [r for r in run.returns if r.result < 0]
    total_pay = sum(r.result for r in pay)
    total_back = -sum(r.result for r in back)
    pool = MONDAY_GOOD if total_pay >= total_back else MONDAY_REFUND
    return "\n".join(
        (
            f"🧾 **{TAX_COLLECTOR} ha hecho las cuentas de la semana {week_label(week)}:** "
            f"{len(run.returns)} declaraciones, {len(pay)} a pagar y {len(back)} a devolver.",
            f"Entre todos: {format_amount(total_pay)} para el Estado y "
            f"{format_amount(total_back)} de vuelta.",
            f"-# {pool[week.toordinal() // 7 % len(pool)]} Cada uno abre la suya con `/renta`.",
        )
    )


def pending_hint() -> str:
    """Línea pequeña para el resultado de una jugada si hay renta pendiente."""
    return "-# 📬 Tienes la renta sin presentar: `/renta`"


def renta_embed(
    mode: str,
    statement: IrpfStatement | None,
    pending: list[Declaration],
    *,
    rate: float | None = None,
    extra: str = "",
) -> discord.Embed:
    """El embed de `renta` en un modo: el sobre, el resumen o el detalle."""
    parts: list[str] = []
    if statement is None:
        parts.append(
            f"📑 Aún no tienes ninguna declaración. El lunes {TAX_COLLECTOR} hará las cuentas "
            "de esta semana: si te retuvo de menos te lo cobra, y si te retuvo de más te lo "
            "devuelve si la presentas aquí."
        )
    elif mode == SEALED:
        parts.append(envelope_text(statement))
    elif mode == DETAIL:
        parts.append(detail_text(statement, rate=rate))
    else:
        parts.append(summary_text(statement))
    if pending and mode != SEALED:
        parts.append(draft_text(pending))
    if extra:
        parts.append(extra)
    text = "\n\n".join(parts)
    if len(text) > EMBED_LIMIT:
        text = text[: EMBED_LIMIT - 2] + "…"
    return discord.Embed(title="🧾 Renta", description=text, color=COLOR)


async def remind(bot: commands.Bot, interaction: discord.Interaction) -> None:
    """Atajo para el casino: avisa de la renta pendiente si el cog está cargado."""
    if (cog := find_cog(bot, Renta)) is not None:
        await cog.remind(interaction)


async def hint(
    bot: commands.Bot, guild_id: int, user_id: int, moment: Moment | None = None
) -> str | None:
    """Líneas pequeñas para el resultado de cualquier acción con dinero, o `None`.

    Una sola vez, que ya tiene su declaración del lunes (sin decir qué sale); la de la
    renta pendiente de presentar; una sola vez, la de los intereses que aún no ha visto
    (`bot.cogs.intereses`); la de las cajas gratis que ha ganado jugando
    (`bot.cogs.shop.notice`); y lo que diga su mascota activa (`bot.cogs.pets.cameo`).
    Así la declaración, el cobro diario de la cuenta y las mascotas llegan a todos los
    juegos sin tocarlos uno a uno.

    Args:
        moment: Lo que acaba de pasar (`bot.services.pets.bet_moment` en una
            jugada), para que la mascota reaccione a eso y para que puedan
            aparecer mascotas nuevas. Sin él, la mascota habla de dinero en
            general y no aparece ninguna.
    """
    lines = []
    if (cog := find_cog(bot, Renta)) is not None and (
        line := await cog.hint_for(guild_id, user_id)
    ):
        lines.append(line)
    if line := await intereses.hint(bot, guild_id, user_id):
        lines.append(line)
    # Import tardío: `shop` importa este módulo.
    from bot.cogs import shop as tienda

    if line := tienda.notice(bot, guild_id, user_id):
        lines.append(line)
    if line := await mascotas.cameo(bot, guild_id, user_id, moment):
        lines.append(line)
    return "\n".join(lines) if lines else None


class RentaView(discord.ui.View):
    """La declaración en `renta`: abrir el sobre, resumen o detalle, y presentar.

    Pasar del resumen al detalle solo cambia lo que se enseña (la declaración ya está
    cargada): contesta en el acto. Abrir el sobre y presentar tocan la base de datos.
    """

    def __init__(
        self,
        cog: Renta,
        owner_id: int,
        statement: IrpfStatement | None,
        pending: list[Declaration],
        *,
        mode: str,
        rate: float | None = None,
        extra: str = "",
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id
        self.statement = statement
        self.pending = pending
        self.mode = mode
        self.rate = rate
        self.extra = extra
        self._rebuild()

    def embed(self) -> discord.Embed:
        """El embed del modo en que está."""
        return renta_embed(
            self.mode, self.statement, self.pending, rate=self.rate, extra=self.extra
        )

    def _rebuild(self) -> None:
        self.clear_items()
        if self.statement is not None:
            if self.mode == SEALED:
                self._button("Abrir", "📑", discord.ButtonStyle.primary, "renta:abrir", self._open)
            elif self.mode == SUMMARY:
                self._button(
                    "Ver el detalle", "🔍", discord.ButtonStyle.secondary, "renta:detalle",
                    self._detail,
                )  # fmt: skip
            else:
                self._button(
                    "Ver el resumen", "📄", discord.ButtonStyle.secondary, "renta:resumen",
                    self._summary,
                )  # fmt: skip
        if self.pending and self.mode != SEALED:
            self._button(
                "Presentar", "📬", discord.ButtonStyle.success, "renta:presentar", self._present
            )

    def _button(
        self, label: str, emoji: str, style: discord.ButtonStyle, custom_id: str, callback
    ) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=custom_id)
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esta declaración no es tuya.", ephemeral=True)
            return False
        return True

    async def _open(self, interaction: discord.Interaction) -> None:
        """Abre el sobre: marca la declaración como vista y enseña el resumen."""
        await ack(interaction)
        if self.statement is not None and interaction.guild is not None:
            await self.cog.economy.open_irpf_return(
                interaction.guild.id, self.owner_id, self.statement.week
            )
            self.cog.track_opened(interaction, self.statement)
        self.mode = SUMMARY
        self._rebuild()
        await edit(interaction, embed=self.embed(), view=self)

    async def _detail(self, interaction: discord.Interaction) -> None:
        self.mode = DETAIL
        self._rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _summary(self, interaction: discord.Interaction) -> None:
        self.mode = SUMMARY
        self._rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _present(self, interaction: discord.Interaction) -> None:
        await self.cog.present(interaction)
        self.stop()


class PresentView(discord.ui.View):
    """Botón Presentar del aviso del casino; solo lo puede pulsar su dueño."""

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
    """Declaración semanal del IRPF: cierre de los lunes, avisos y evento de campaña."""

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

    async def _sealed(self, guild_id: int, user_id: int) -> bool:
        """Si su última declaración está sin abrir (entonces no se adelanta nada)."""
        statement = await self.economy.irpf_statement(guild_id, user_id)
        return statement is not None and not statement.opened

    async def hint_for(self, guild_id: int, user_id: int) -> str | None:
        """Líneas pequeñas de la renta: declaración nueva (sin cifra) y la que falta presentar."""
        lines = []
        try:
            for week in await self.economy.take_irpf_notices(guild_id, user_id):
                lines.append(notice_text(week))
            if not lines and not await self._sealed(guild_id, user_id):
                if await self.economy.pending_declarations(guild_id, user_id):
                    lines.append(pending_hint())
        except Exception:
            logger.exception("No se pudo consultar la renta")
        return "\n".join(lines) if lines else None

    async def remind(self, interaction: discord.Interaction) -> None:
        """Tras una jugada con botón o slash: avisa en efímero, una vez por campaña.

        Debe llamarse cuando la interacción ya tiene respuesta: el aviso va
        como mensaje de seguimiento que solo ve quien ha jugado. Si la última
        declaración está sin abrir, no avisa: el sobre se abre en `renta`.
        """
        guild = interaction.guild
        if guild is None or not interaction.response.is_done():
            return
        campaign = week_start(local_day(time.time()))
        key = (guild.id, interaction.user.id, campaign)
        if key in self._reminded:
            return
        try:
            if await self._sealed(guild.id, interaction.user.id):
                return
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

    def track_opened(self, interaction: discord.Interaction, statement: IrpfStatement) -> None:
        """Apunta a los logros que ha abierto una declaración (y qué le salió)."""
        if interaction.guild is None:
            return
        result = statement.filed.result
        logros.note(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            StatDelta(
                add={
                    "renta_opened": 1,
                    "renta_opened_pay": int(result > 0),
                    "renta_opened_refund": int(result < 0),
                }
            ),
            interaction.channel_id,
        )

    # -- Presentar ---------------------------------------------------------------------

    async def present(self, interaction: discord.Interaction) -> None:
        """Cobra la devolución y lo anuncia en el canal."""
        guild = interaction.guild
        if guild is None:
            return
        # Cobrar la devolución va a la base de datos: se acepta el clic antes.
        await ack(interaction)
        claim = await self.economy.claim_declarations(guild.id, interaction.user.id)
        if not claim.declarations:
            await edit(interaction, content="No tienes ninguna declaración pendiente.", view=None)
            return
        await edit(
            interaction,
            content=(
                f"✅ Declaración presentada: +{format_amount(claim.refunded)}. "
                f"Saldo: **{format_amount(claim.balance)}**."
            ),
            embed=None,
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
        await logros.track(
            self.bot,
            guild.id,
            interaction.user,
            channel,
            StatDelta(
                add={"renta_filed": 1, "renta_refunded": claim.refunded},
                peak={"balance_max": claim.balance},
            ),
        )

    # -- Comando -----------------------------------------------------------------------

    async def _view(self, guild: discord.Guild, member: discord.abc.User) -> RentaView:
        """La vista de `renta` de un miembro: su última declaración y lo pendiente."""
        statement = await self.economy.irpf_statement(guild.id, member.id)
        pending = await self.economy.pending_declarations(guild.id, member.id)
        rate = await self.economy.irpf_rate(guild.id, member.id)
        # La base del ahorro (intereses) se liquida sola cada lunes; aquí solo se enseña.
        savings = await intereses.savings_line(self.bot, guild.id, member.id) or ""
        mode = SEALED if statement is not None and not statement.opened else SUMMARY
        return RentaView(self, member.id, statement, pending, mode=mode, rate=rate, extra=savings)

    @app_commands.command(name="renta", description="Mira y presenta tu declaración de la renta.")
    @app_commands.guild_only()
    async def renta(self, interaction: discord.Interaction) -> None:
        """Tu declaración: el sobre (si está sin abrir), el resumen o el detalle."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "La renta solo se presenta dentro de un servidor.", ephemeral=True
            )
            return
        # Leer la declaración va a la base de datos: se contesta antes.
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = await self._view(interaction.guild, interaction.user)
        await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)

    @commands.command(name="renta")
    @commands.guild_only()
    async def renta_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.renta`); se ve en el canal porque no puede ser efímera."""
        if ctx.guild is None:
            return
        view = await self._view(ctx.guild, ctx.author)
        await ctx.send(embed=view.embed(), view=view)

    # -- Cierre del lunes y evento semanal ---------------------------------------------

    @tasks.loop(minutes=10)
    async def _campaign_events(self) -> None:
        """Cierra las semanas que falten y mantiene un evento de Discord por campaña."""
        now = datetime.now(TIMEZONE)
        monday = week_start(now.date())
        name = f"{EVENT_PREFIX} · semana {week_label(monday - timedelta(days=7))}"
        end = datetime.combine(monday + timedelta(days=7), datetime.min.time(), TIMEZONE)
        for guild in self.bot.guilds:
            await self._close_weeks(guild)
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

    async def _close_weeks(self, guild: discord.Guild) -> None:
        """Cierra el IRPF de las semanas acabadas que falten y lo anuncia. Nunca lanza."""
        try:
            closed = await self.economy.close_irpf_weeks(guild.id)
        except Exception:
            logger.exception("No se pudo cerrar el IRPF en el servidor %s", guild.id)
            return
        if not closed:
            return
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        for week, run in closed:
            if not run.returns:
                continue
            if channel is not None:
                try:
                    await channel.send(
                        announcement_text(week, run),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.warning("No se pudo anunciar la renta del lunes", exc_info=True)
            for filed in run.returns:
                logros.note(
                    self.bot,
                    guild.id,
                    filed.user_id,
                    StatDelta(
                        add={
                            "renta_closed": 1,
                            "renta_paid": filed.charged,
                            "renta_debt": filed.debt,
                        },
                        peak={"renta_rate_max": round(filed.rate * 100)},
                    ),
                    channel.id if channel is not None else None,
                )

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
                    "Ya ha salido la declaración de la semana pasada: ábrela con /renta. Si "
                    f"era a pagar, {TAX_COLLECTOR} ya se la ha cobrado; si es a devolver, "
                    f"preséntala allí. Hay {CLAIM_WEEKS} semanas para presentarla; después "
                    "prescribe."
                ),
            )
        elif current.status is discord.EventStatus.scheduled and current.start_time <= now:
            await current.start()


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Renta(bot, bot.economy))
