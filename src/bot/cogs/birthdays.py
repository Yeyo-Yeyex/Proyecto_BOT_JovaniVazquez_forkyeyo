"""Cumpleaños: `cumple`, `cumples`, anuncio del día y felicitaciones con premio.

- `cumple [fecha] [miembro]`: sin argumentos muestra el tuyo; con `miembro`,
  el suyo; con `fecha` (`dd/mm`) guarda el tuyo. Una vez puesto, solo un
  administrador puede cambiarlo (`cumple 14/02 @miembro`), para que nadie
  lo mueva y cobre el regalo varias veces.
- `cumples`: los próximos cumpleaños del servidor.

Cada 5 minutos una tarea mira si hoy (hora canaria) cumple alguien. Si es
así, y no se había celebrado ya este año, le da el regalo y lo anuncia en
`#chat-general` (o en el canal del sistema) con un botón 🎉 Felicitar. Si
existe un rol llamado `BIRTHDAY_ROLE_NAME`, se lo pone durante el día; el
bot no crea roles por su cuenta (necesita **Gestionar roles** y estar por
encima de ese rol).

Felicitar cuenta tanto con el botón como escribiendo: un mensaje que
mencione o responda al cumpleañero y suene a felicitación ("feliz cumple",
"felicidades", 🎂…). El bot reacciona 🎉 a los mensajes que cuentan.
"""

from __future__ import annotations

import enum
import logging
import re
import sqlite3
import time
from datetime import date
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import renta
from bot.repositories.birthdays import BirthdayRepository
from bot.services.birthdays import (
    BIRTHDAY_GIFT,
    GREETED_BONUS,
    GREETER_REWARD,
    days_until,
    format_birthday,
    is_birthday,
    is_greeting,
    parse_birthday,
)
from bot.services.economy import CURRENCY_EMOJI, EconomyService, format_amount
from bot.services.levels import local_day
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

ANNOUNCE_CHANNEL_NAME = "chat-general"
BIRTHDAY_ROLE_NAME = "🎂 Cumpleañero"
UPCOMING_LIMIT = 10
COLOR = discord.Color.from_rgb(255, 140, 190)


class GreetOutcome(enum.Enum):
    """Resultado de intentar felicitar a alguien."""

    OK = enum.auto()
    SELF = enum.auto()
    NOT_TODAY = enum.auto()
    REPEATED = enum.auto()


class GreetButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cumple:felicitar:(?P<user>\d+):(?P<year>\d+)",
):
    """Botón 🎉 Felicitar del anuncio.

    Es un `DynamicItem`: el id del cumpleañero va en el `custom_id`, así el
    botón sigue funcionando aunque el bot se reinicie durante el día.
    """

    def __init__(self, user_id: int, year: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Felicitar",
                emoji="🎉",
                style=discord.ButtonStyle.success,
                custom_id=f"cumple:felicitar:{user_id}:{year}",
            )
        )
        self.user_id = user_id
        self.year = year

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> GreetButton:
        return cls(int(match["user"]), int(match["year"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Birthdays")  # type: ignore[attr-defined]
        if isinstance(cog, Birthdays):
            await cog.greet_from_button(interaction, self.user_id, self.year)


class Birthdays(commands.Cog):
    """Guarda cumpleaños, los anuncia y paga regalos y felicitaciones."""

    def __init__(
        self, bot: commands.Bot, repository: BirthdayRepository, economy: EconomyService
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.economy = economy
        #: Cumpleañeros de hoy por servidor, para no consultar la base de datos
        #: en cada mensaje. Lo rellena `_daily_check`.
        self._today: dict[int, tuple[date, frozenset[int]]] = {}

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(GreetButton)
        self._daily_check.start()

    async def cog_unload(self) -> None:
        self._daily_check.cancel()
        self.bot.remove_dynamic_items(GreetButton)

    # -- Felicitar -----------------------------------------------------------------

    async def greet(
        self, guild: discord.Guild, birthday_user_id: int, greeter: discord.abc.User, today: date
    ) -> GreetOutcome:
        """Registra y paga una felicitación si es válida."""
        if greeter.bot or greeter.id == birthday_user_id:
            return GreetOutcome.SELF
        birthday = await self.repository.get_birthday(guild.id, birthday_user_id)
        if birthday is None or not is_birthday(birthday.day, birthday.month, today):
            return GreetOutcome.NOT_TODAY
        first = await self.repository.add_greeting(
            guild.id, birthday_user_id, today.year, greeter.id
        )
        if not first:
            return GreetOutcome.REPEATED
        await self.economy.grant(
            guild.id, greeter.id, amount=GREETER_REWARD, reason="cumple:felicitar"
        )
        await self.economy.grant(
            guild.id, birthday_user_id, amount=GREETED_BONUS, reason="cumple:felicitado"
        )
        return GreetOutcome.OK

    async def greet_from_button(
        self, interaction: discord.Interaction, birthday_user_id: int, year: int
    ) -> None:
        """Respuesta (solo visible para quien pulsa) al botón 🎉 Felicitar."""
        guild = interaction.guild
        if guild is None:
            return
        today = local_day(time.time())
        outcome = (
            GreetOutcome.NOT_TODAY
            if year != today.year
            else await self.greet(guild, birthday_user_id, interaction.user, today)
        )
        texts = {
            GreetOutcome.OK: (
                f"🎉 ¡Felicitado! {CURRENCY_EMOJI} +{format_amount(GREETER_REWARD)} para ti "
                f"y +{format_amount(GREETED_BONUS)} para <@{birthday_user_id}>."
            ),
            GreetOutcome.SELF: "No puedes felicitarte a ti mismo. Buen intento.",
            GreetOutcome.NOT_TODAY: "Este cumpleaños ya pasó.",
            GreetOutcome.REPEATED: "Ya le felicitaste hoy.",
        }
        await interaction.response.send_message(
            texts[outcome], ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )
        if outcome is GreetOutcome.OK:
            # Felicitar da dinero: gancho de la Renta (ver Biblia.txt, sección 4).
            await renta.remind(self.bot, interaction)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Cuenta como felicitación un mensaje al cumpleañero que lo parezca."""
        if message.guild is None or message.author.bot:
            return
        cached = self._today.get(message.guild.id)
        if cached is None:
            return
        cached_day, birthday_ids = cached
        today = local_day(time.time())
        if cached_day != today or not birthday_ids:
            return
        targets = {user.id for user in message.mentions} & birthday_ids
        reference = message.reference.resolved if message.reference else None
        if isinstance(reference, discord.Message) and reference.author.id in birthday_ids:
            targets.add(reference.author.id)
        if not targets or not is_greeting(message.content):
            return
        greeted = False
        for user_id in targets:
            try:
                outcome = await self.greet(message.guild, user_id, message.author, today)
            except (OSError, sqlite3.Error):
                logger.exception("No se pudo registrar una felicitación")
                continue
            greeted = greeted or outcome is GreetOutcome.OK
        if greeted:
            try:
                await message.add_reaction("🎉")
            except (discord.Forbidden, discord.HTTPException):
                pass

    # -- Anuncio diario ------------------------------------------------------------

    @tasks.loop(minutes=5)
    async def _daily_check(self) -> None:
        """Celebra los cumpleaños de hoy que no se hayan celebrado aún."""
        today = local_day(time.time())
        for guild in self.bot.guilds:
            try:
                await self._check_guild(guild, today)
            except Exception:
                # Una excepción sin capturar pararía la tarea para siempre.
                logger.exception("Error revisando cumpleaños del servidor %s", guild.id)

    @_daily_check.before_loop
    async def _before_daily_check(self) -> None:
        await self.bot.wait_until_ready()

    async def _check_guild(self, guild: discord.Guild, today: date) -> None:
        birthdays = await self.repository.list_birthdays(guild.id)
        members = [
            member
            for b in birthdays
            if is_birthday(b.day, b.month, today)
            and (member := guild.get_member(b.user_id)) is not None
        ]
        self._today[guild.id] = (today, frozenset(member.id for member in members))
        await self._sync_role(guild, members)
        for member in members:
            if await self.repository.mark_celebrated(guild.id, member.id, today.year):
                await self.economy.grant(
                    guild.id, member.id, amount=BIRTHDAY_GIFT, reason="cumple:regalo"
                )
                await self._announce(guild, member, today.year)

    async def _announce(self, guild: discord.Guild, member: discord.Member, year: int) -> None:
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        if channel is None:
            return
        embed = discord.Embed(
            title="🎂 ¡Hoy hay cumpleaños!",
            description=(
                f"Es el cumpleaños de {member.mention}. Le caen "
                f"**{format_amount(BIRTHDAY_GIFT)}** de regalo.\n\n"
                f"Felicítale con el botón o escribiéndole: quien felicite se lleva "
                f"{format_amount(GREETER_REWARD)} y le suma {format_amount(GREETED_BONUS)} "
                "más al cumpleañero."
            ),
            color=COLOR,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        view = discord.ui.View(timeout=None)
        view.add_item(GreetButton(member.id, year))
        try:
            await channel.send(
                content=member.mention,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=[member]),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo anunciar el cumpleaños en el servidor %s", guild.id, exc_info=True
            )

    async def _sync_role(self, guild: discord.Guild, members: list[discord.Member]) -> None:
        """Pone el rol de cumpleañero a quien cumple hoy y se lo quita al resto."""
        role = discord.utils.get(guild.roles, name=BIRTHDAY_ROLE_NAME)
        if role is None:
            return
        today_ids = {member.id for member in members}
        try:
            for holder in role.members:
                if holder.id not in today_ids:
                    await holder.remove_roles(role, reason="Fin del cumpleaños")
            for member in members:
                if role not in member.roles:
                    await member.add_roles(role, reason="Cumpleaños")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo gestionar el rol de cumpleaños en el servidor %s",
                guild.id,
                exc_info=True,
            )

    # -- Comandos ------------------------------------------------------------------

    async def _cumple_impl(
        self,
        responder: CommandResponder,
        fecha: str | None,
        miembro: discord.Member | None,
    ) -> None:
        guild, author = responder.guild, responder.member
        if guild is None or author is None:
            await responder.send_error("Los cumpleaños solo funcionan dentro de un servidor.")
            return
        target = miembro or author
        name = discord.utils.escape_markdown(target.display_name)

        if fecha is None:
            birthday = await self.repository.get_birthday(guild.id, target.id)
            if birthday is None:
                hint = "Ponlo con `cumple dd/mm`." if target == author else ""
                await responder.send(f"**{name}** no ha puesto su cumpleaños. {hint}".strip())
                return
            await responder.send(
                f"🎂 El cumpleaños de **{name}** es el "
                f"**{format_birthday(birthday.day, birthday.month)}**."
            )
            return

        try:
            day, month = parse_birthday(fecha)
        except ValueError as error:
            await responder.send_error(str(error))
            return
        is_admin = author.guild_permissions.administrator
        if target != author and not is_admin:
            await responder.send_error("Solo un administrador puede poner el cumpleaños de otro.")
            return
        saved = await self.repository.set_birthday(
            guild.id, target.id, day, month, overwrite=is_admin
        )
        if not saved:
            await responder.send_error(
                "Ya tienes un cumpleaños puesto. Para cambiarlo, pídeselo a un administrador."
            )
            return
        await responder.send(
            f"🎂 Apuntado: el cumpleaños de **{name}** es el **{format_birthday(day, month)}**."
        )

    @app_commands.command(name="cumple", description="Pon o consulta un cumpleaños.")
    @app_commands.describe(
        fecha="Tu cumpleaños, dd/mm (p. ej. 14/02).", miembro="De quién consultarlo."
    )
    @app_commands.guild_only()
    async def cumple(
        self,
        interaction: discord.Interaction,
        fecha: str | None = None,
        miembro: discord.Member | None = None,
    ) -> None:
        """`/cumple`: muestra el tuyo, `fecha` lo guarda y `miembro` consulta otro."""
        await self._cumple_impl(InteractionResponder(interaction), fecha, miembro)

    @commands.command(name="cumple")
    @commands.guild_only()
    async def cumple_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.cumple`, `.cumple 14/02`, `.cumple @x`, `.cumple 14/02 @x`."""
        fecha: str | None = None
        miembro: discord.Member | None = None
        for arg in args:
            if re.match(r"^\d", arg) and not arg.isdigit():
                fecha = arg
            else:
                try:
                    miembro = await commands.MemberConverter().convert(ctx, arg)
                except commands.BadArgument:
                    await ContextResponder(ctx).send_error(f"No encuentro a `{arg}`.")
                    return
        await self._cumple_impl(ContextResponder(ctx), fecha, miembro)

    async def _cumples_impl(self, responder: CommandResponder) -> None:
        guild = responder.guild
        if guild is None:
            await responder.send_error("Los cumpleaños solo funcionan dentro de un servidor.")
            return
        today = local_day(time.time())
        upcoming = sorted(
            (
                (days_until(b.day, b.month, today), b, member)
                for b in await self.repository.list_birthdays(guild.id)
                if (member := guild.get_member(b.user_id)) is not None
            ),
            key=lambda entry: entry[0],
        )[:UPCOMING_LIMIT]
        if not upcoming:
            await responder.send("Nadie ha puesto su cumpleaños todavía. Usa `cumple dd/mm`.")
            return
        lines = []
        for days, birthday, member in upcoming:
            when = {0: "**¡hoy!**", 1: "mañana"}.get(days, f"en {days} días")
            lines.append(
                f"**{discord.utils.escape_markdown(member.display_name)}** · "
                f"{format_birthday(birthday.day, birthday.month)} · {when}"
            )
        embed = discord.Embed(
            title="🎂 Próximos cumpleaños", description="\n".join(lines), color=COLOR
        )
        await responder.send(embed=embed)

    @app_commands.command(name="cumples", description="Próximos cumpleaños del servidor.")
    @app_commands.guild_only()
    async def cumples(self, interaction: discord.Interaction) -> None:
        """Lista los próximos cumpleaños."""
        await self._cumples_impl(InteractionResponder(interaction))

    @commands.command(name="cumples")
    @commands.guild_only()
    async def cumples_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.cumples`) de `/cumples`."""
        await self._cumples_impl(ContextResponder(ctx))

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los cumpleaños del servidor cuando el bot sale de él."""
        await self.repository.delete_guild_data(guild.id)
        self._today.pop(guild.id, None)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Birthdays(bot, bot.birthdays, bot.economy))
