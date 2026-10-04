"""Cog de administración: moderación y utilidades solo para administradores.

Comandos (todos con `/` y con `.`, mismo nombre):
`purge`, `mute`, `unmute`, `kick`, `ban`, `unban`, `lock`, `unlock`,
`slow`, `say`, `nick`, `role`, `bienv`.

Autorización: solo miembros con el permiso **Administrador** del servidor.
Se comprueba en el servidor en cada invocación (`cog_check` para `.` e
`interaction_check` para `/`); además, `default_permissions` oculta los
slash commands del menú a quien no es administrador, pero eso es solo
estética (ver Biblia.txt, sección 6).

Permisos que necesita el bot, según el comando: Gestionar mensajes
(`purge`), Aislar temporalmente a miembros (`mute`/`unmute`), Expulsar
(`kick`), Banear (`ban`/`unban`), Gestionar canales (`lock`/`unlock`/
`slow`), Gestionar apodos (`nick`) y Gestionar roles (`role`). Si falta
alguno, el comando lo dice en vez de fallar en silencio.

Cada acción queda en el registro de auditoría de Discord con el motivo y
quién la pidió.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.repositories.welcome import WelcomeSettings
from bot.services.moderation import (
    MAX_PURGE,
    MAX_SLOWMODE_SECONDS,
    format_duration,
    hierarchy_error,
    parse_duration,
    parse_user_id,
)
from bot.services.welcome import GifError, classify_gif
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

NOT_ADMIN = "Solo los administradores pueden usar este comando."
BOT_FORBIDDEN = "No tengo permiso para hacer eso. Revisa mis roles y permisos en el servidor."
# Discord borra en bloque solo mensajes de menos de 14 días; los más antiguos
# irían de uno en uno (una petición por mensaje), así que `purge` los ignora.
BULK_DELETE_WINDOW = timedelta(days=14)
# Mensajes recientes que `purge` revisa cuando filtra por miembro.
PURGE_SCAN_LIMIT = 500
# Segundos que sigue visible la confirmación de `.purge` antes de borrarse.
PURGE_NOTICE_SECONDS = 5
# Discord limita el motivo del registro de auditoría a 512 caracteres.
AUDIT_REASON_LIMIT = 512
MAX_NICK_LENGTH = 32
# `say` puede mencionar usuarios, pero nunca @everyone, @here ni roles.
SAY_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)
# Palabras que en `bienv` quitan el GIF y vuelven al vídeo de Kratos.
GIF_RESET_WORDS = {"quitar", "video", "vídeo", "ninguno"}

PurgeableChannel = discord.TextChannel | discord.Thread | discord.VoiceChannel


def _audit_reason(actor: discord.abc.User, reason: str | None) -> str:
    """Motivo para el registro de auditoría, con quién ejecutó el comando."""
    text = f"{reason.strip() if reason else 'Sin motivo'} (por {actor})"
    return text[:AUDIT_REASON_LIMIT]


class Admin(commands.Cog):
    """Comandos de moderación reservados a administradores."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # --- Autorización -----------------------------------------------------

    async def cog_check(self, ctx: commands.Context) -> bool:  # type: ignore[override]
        """Solo administradores y solo dentro de un servidor (comandos `.`)."""
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not isinstance(ctx.author, discord.Member) or (
            not ctx.author.guild_permissions.administrator
        ):
            raise commands.MissingPermissions(["administrator"])
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # type: ignore[override]
        """Solo administradores y solo dentro de un servidor (comandos `/`)."""
        if interaction.guild is None:
            raise app_commands.NoPrivateMessage(
                interaction.command.name if interaction.command else ""
            )
        user = interaction.user
        if not isinstance(user, discord.Member) or not user.guild_permissions.administrator:
            raise app_commands.MissingPermissions(["administrator"])
        return True

    # --- Utilidades comunes -----------------------------------------------

    async def _attempt(self, responder: CommandResponder, action: Awaitable[object]) -> bool:
        """Ejecuta una llamada a Discord; si el bot no tiene permiso, lo explica.

        Returns:
            `True` si la acción se completó.
        """
        try:
            await action
        except discord.Forbidden:
            await responder.send_error(BOT_FORBIDDEN)
            return False
        return True

    async def _check_target(
        self, responder: CommandResponder, target: discord.Member, *, allow_self: bool = False
    ) -> bool:
        """Comprueba la jerarquía de roles; si no se puede, lo explica."""
        guild = target.guild
        actor = responder.member
        if actor is None:
            await responder.send_error(NOT_ADMIN)
            return False
        error = hierarchy_error(
            actor, target, guild.me, owner_id=guild.owner_id or 0, allow_self=allow_self
        )
        if error is not None:
            await responder.send_error(error)
            return False
        return True

    # --- purge ------------------------------------------------------------

    async def _purge(
        self,
        channel: PurgeableChannel,
        amount: int,
        member: discord.Member | None,
        reason: str,
        before: discord.Message | None = None,
    ) -> int:
        """Borra hasta `amount` mensajes recientes (de `member`, si se indica).

        Returns:
            Número de mensajes borrados.
        """
        deleted = 0

        def check(message: discord.Message) -> bool:
            nonlocal deleted
            if member is not None and message.author.id != member.id:
                return False
            if deleted >= amount:
                return False
            deleted += 1
            return True

        removed = await channel.purge(
            limit=amount if member is None else PURGE_SCAN_LIMIT,
            check=check,
            before=before,
            after=discord.utils.utcnow() - BULK_DELETE_WINDOW,
            reason=reason,
        )
        return len(removed)

    @app_commands.command(name="purge", description="Borra mensajes recientes del canal.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        cantidad="Cuántos mensajes (1-100).", miembro="Solo los de este miembro."
    )
    async def purge(
        self,
        interaction: discord.Interaction,
        cantidad: app_commands.Range[int, 1, MAX_PURGE],
        miembro: discord.Member | None = None,
    ) -> None:
        """Borra los últimos mensajes del canal; responde en efímero."""
        responder = InteractionResponder(interaction)
        channel = interaction.channel
        if not isinstance(channel, PurgeableChannel):
            await responder.send_error("Aquí no se pueden borrar mensajes.")
            return
        await responder.start_progress(ephemeral=True)
        try:
            count = await self._purge(
                channel, cantidad, miembro, _audit_reason(interaction.user, "purge")
            )
        except discord.Forbidden:
            await responder.finish(BOT_FORBIDDEN)
            return
        await responder.finish(f"🧹 {count} mensaje(s) borrado(s).")

    @commands.command(name="purge")
    async def purge_text(
        self, ctx: commands.Context, cantidad: int, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.`); la confirmación se borra sola a los pocos segundos."""
        if not 1 <= cantidad <= MAX_PURGE:
            await ctx.send(f"La cantidad debe estar entre 1 y {MAX_PURGE}.")
            return
        if not isinstance(ctx.channel, PurgeableChannel):
            await ctx.send("Aquí no se pueden borrar mensajes.")
            return
        try:
            count = await self._purge(
                ctx.channel,
                cantidad,
                miembro,
                _audit_reason(ctx.author, "purge"),
                before=ctx.message,
            )
            await ctx.message.delete()
        except discord.Forbidden:
            await ctx.send(BOT_FORBIDDEN)
            return
        await ctx.send(f"🧹 {count} mensaje(s) borrado(s).", delete_after=PURGE_NOTICE_SECONDS)

    # --- mute / unmute ----------------------------------------------------

    @app_commands.command(name="mute", description="Aísla a un miembro durante un tiempo.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        miembro="A quién aislar.",
        duracion="Ej.: 10m, 2h, 1d, 1h30m (máx. 28d). Sin unidad, minutos.",
        motivo="Queda en el registro de auditoría.",
    )
    async def mute(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member,
        duracion: str,
        motivo: str | None = None,
    ) -> None:
        """Aplica un aislamiento temporal (timeout) de Discord."""
        await self._mute_impl(InteractionResponder(interaction), miembro, duracion, motivo)

    @commands.command(name="mute")
    async def mute_text(
        self, ctx: commands.Context, miembro: discord.Member, duracion: str, *, motivo: str = ""
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._mute_impl(ContextResponder(ctx), miembro, duracion, motivo or None)

    async def _mute_impl(
        self, responder: CommandResponder, member: discord.Member, text: str, reason: str | None
    ) -> None:
        duration = parse_duration(text)
        if duration is None:
            await responder.send_error("Duración no válida. Ej.: `10m`, `2h`, `1d` (máx. 28d).")
            return
        if not await self._check_target(responder, member):
            return
        assert responder.member is not None
        if await self._attempt(
            responder, member.timeout(duration, reason=_audit_reason(responder.member, reason))
        ):
            await responder.send(
                f"🔇 {member.mention} aislado durante {format_duration(duration)}."
            )

    @app_commands.command(name="unmute", description="Quita el aislamiento a un miembro.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def unmute(self, interaction: discord.Interaction, miembro: discord.Member) -> None:
        """Retira el timeout del miembro."""
        await self._unmute_impl(InteractionResponder(interaction), miembro)

    @commands.command(name="unmute")
    async def unmute_text(self, ctx: commands.Context, miembro: discord.Member) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._unmute_impl(ContextResponder(ctx), miembro)

    async def _unmute_impl(self, responder: CommandResponder, member: discord.Member) -> None:
        if not member.is_timed_out():
            await responder.send_error(f"{member.display_name} no está aislado.")
            return
        if not await self._check_target(responder, member):
            return
        assert responder.member is not None
        if await self._attempt(
            responder, member.timeout(None, reason=_audit_reason(responder.member, None))
        ):
            await responder.send(f"🔊 {member.mention} ya puede hablar.")

    # --- kick / ban / unban -----------------------------------------------

    @app_commands.command(name="kick", description="Expulsa a un miembro del servidor.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def kick(
        self, interaction: discord.Interaction, miembro: discord.Member, motivo: str | None = None
    ) -> None:
        """Expulsa al miembro; puede volver con una invitación."""
        await self._kick_impl(InteractionResponder(interaction), miembro, motivo)

    @commands.command(name="kick")
    async def kick_text(
        self, ctx: commands.Context, miembro: discord.Member, *, motivo: str = ""
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._kick_impl(ContextResponder(ctx), miembro, motivo or None)

    async def _kick_impl(
        self, responder: CommandResponder, member: discord.Member, reason: str | None
    ) -> None:
        if not await self._check_target(responder, member):
            return
        assert responder.member is not None
        if await self._attempt(
            responder, member.kick(reason=_audit_reason(responder.member, reason))
        ):
            await responder.send(f"👢 {member} expulsado.")

    @app_commands.command(name="ban", description="Banea a un miembro del servidor.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def ban(
        self, interaction: discord.Interaction, miembro: discord.Member, motivo: str | None = None
    ) -> None:
        """Banea al miembro sin borrar sus mensajes anteriores."""
        await self._ban_impl(InteractionResponder(interaction), miembro, motivo)

    @commands.command(name="ban")
    async def ban_text(
        self, ctx: commands.Context, miembro: discord.Member, *, motivo: str = ""
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._ban_impl(ContextResponder(ctx), miembro, motivo or None)

    async def _ban_impl(
        self, responder: CommandResponder, member: discord.Member, reason: str | None
    ) -> None:
        if not await self._check_target(responder, member):
            return
        assert responder.member is not None
        if await self._attempt(
            responder,
            member.ban(reason=_audit_reason(responder.member, reason), delete_message_seconds=0),
        ):
            await responder.send(f"🔨 {member} baneado.")

    @app_commands.command(name="unban", description="Levanta el baneo de un usuario.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(usuario="ID del usuario baneado.")
    async def unban(self, interaction: discord.Interaction, usuario: str) -> None:
        """Quita el baneo; el ID va como texto porque los IDs no caben en un entero de Discord."""
        await self._unban_impl(InteractionResponder(interaction), usuario)

    @commands.command(name="unban")
    async def unban_text(self, ctx: commands.Context, usuario: str) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._unban_impl(ContextResponder(ctx), usuario)

    async def _unban_impl(self, responder: CommandResponder, text: str) -> None:
        user_id = parse_user_id(text)
        guild = responder.guild
        if user_id is None or guild is None or responder.member is None:
            await responder.send_error("Necesito el ID numérico del usuario.")
            return
        try:
            await guild.unban(
                discord.Object(id=user_id), reason=_audit_reason(responder.member, None)
            )
        except discord.NotFound:
            await responder.send_error("Ese usuario no está baneado.")
            return
        except discord.Forbidden:
            await responder.send_error(BOT_FORBIDDEN)
            return
        await responder.send(
            f"✅ Baneo levantado a <@{user_id}>.", allowed_mentions=discord.AllowedMentions.none()
        )

    # --- lock / unlock / slow ---------------------------------------------

    async def _set_locked(self, responder: CommandResponder, locked: bool) -> None:
        """Bloquea o desbloquea el canal actual para `@everyone`.

        Solo toca los permisos de escribir y de crear hilos de `@everyone`;
        los roles con permisos propios (moderadores, bots) siguen escribiendo.
        """
        channel = responder.channel
        guild = responder.guild
        if not isinstance(channel, discord.TextChannel) or guild is None:
            await responder.send_error("Solo funciona en canales de texto.")
            return
        assert responder.member is not None
        everyone = guild.default_role
        overwrite = channel.overwrites_for(everyone)
        if (overwrite.send_messages is False) == locked:
            await responder.send_error(
                "El canal ya está bloqueado." if locked else "El canal no está bloqueado."
            )
            return
        value = False if locked else None
        overwrite.update(
            send_messages=value, create_public_threads=value, create_private_threads=value
        )
        if await self._attempt(
            responder,
            channel.set_permissions(
                everyone,
                overwrite=None if overwrite.is_empty() else overwrite,
                reason=_audit_reason(responder.member, "lock" if locked else "unlock"),
            ),
        ):
            await responder.send("🔒 Canal bloqueado." if locked else "🔓 Canal desbloqueado.")

    @app_commands.command(name="lock", description="Impide escribir en este canal.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def lock(self, interaction: discord.Interaction) -> None:
        """Quita a `@everyone` el permiso de escribir en el canal actual."""
        await self._set_locked(InteractionResponder(interaction), True)

    @commands.command(name="lock")
    async def lock_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._set_locked(ContextResponder(ctx), True)

    @app_commands.command(name="unlock", description="Vuelve a permitir escribir en este canal.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def unlock(self, interaction: discord.Interaction) -> None:
        """Deshace `lock` en el canal actual."""
        await self._set_locked(InteractionResponder(interaction), False)

    @commands.command(name="unlock")
    async def unlock_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._set_locked(ContextResponder(ctx), False)

    @app_commands.command(name="slow", description="Modo lento del canal (0 lo quita).")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(segundos="Espera entre mensajes, 0-21600.")
    async def slow(
        self,
        interaction: discord.Interaction,
        segundos: app_commands.Range[int, 0, MAX_SLOWMODE_SECONDS],
    ) -> None:
        """Cambia el modo lento del canal actual."""
        await self._slow_impl(InteractionResponder(interaction), segundos)

    @commands.command(name="slow")
    async def slow_text(self, ctx: commands.Context, segundos: int) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._slow_impl(ContextResponder(ctx), segundos)

    async def _slow_impl(self, responder: CommandResponder, seconds: int) -> None:
        channel = responder.channel
        if not 0 <= seconds <= MAX_SLOWMODE_SECONDS:
            await responder.send_error(f"Debe estar entre 0 y {MAX_SLOWMODE_SECONDS} segundos.")
            return
        if not isinstance(channel, discord.TextChannel | discord.Thread):
            await responder.send_error("Solo funciona en canales de texto e hilos.")
            return
        assert responder.member is not None
        if await self._attempt(
            responder,
            channel.edit(slowmode_delay=seconds, reason=_audit_reason(responder.member, "slow")),
        ):
            await responder.send(
                f"🐢 Modo lento: {format_duration(timedelta(seconds=seconds))}."
                if seconds
                else "🐇 Modo lento desactivado."
            )

    # --- say --------------------------------------------------------------

    @app_commands.command(name="say", description="El bot escribe tu mensaje.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(texto="Lo que dirá el bot.", canal="Dónde (por defecto, aquí).")
    async def say(
        self,
        interaction: discord.Interaction,
        texto: app_commands.Range[str, 1, 2000],
        canal: discord.TextChannel | None = None,
    ) -> None:
        """Publica `texto` como el bot. Nunca menciona a @everyone, @here ni roles."""
        responder = InteractionResponder(interaction)
        target = canal or interaction.channel
        if not isinstance(target, discord.abc.Messageable):
            await responder.send_error("No puedo escribir ahí.")
            return
        if await self._attempt(responder, target.send(texto, allowed_mentions=SAY_MENTIONS)):
            await responder.send("📣 Enviado.", ephemeral=True)

    @commands.command(name="say")
    async def say_text(self, ctx: commands.Context, *, texto: str) -> None:
        """Versión de texto (`.`): borra tu mensaje y el bot escribe en su lugar."""
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass  # Sin "Gestionar mensajes" el texto se publica igualmente.
        await ctx.send(texto[:2000], allowed_mentions=SAY_MENTIONS)

    # --- nick / role ------------------------------------------------------

    @app_commands.command(name="nick", description="Cambia o quita el apodo de un miembro.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(apodo="Vacío para quitarlo.")
    async def nick(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member,
        apodo: app_commands.Range[str, 1, MAX_NICK_LENGTH] | None = None,
    ) -> None:
        """Cambia el apodo del miembro en este servidor."""
        await self._nick_impl(InteractionResponder(interaction), miembro, apodo)

    @commands.command(name="nick")
    async def nick_text(
        self, ctx: commands.Context, miembro: discord.Member, *, apodo: str = ""
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._nick_impl(ContextResponder(ctx), miembro, apodo or None)

    async def _nick_impl(
        self, responder: CommandResponder, member: discord.Member, nick: str | None
    ) -> None:
        if nick is not None and len(nick) > MAX_NICK_LENGTH:
            await responder.send_error(f"Máximo {MAX_NICK_LENGTH} caracteres.")
            return
        if not await self._check_target(responder, member, allow_self=True):
            return
        assert responder.member is not None
        if await self._attempt(
            responder, member.edit(nick=nick, reason=_audit_reason(responder.member, "nick"))
        ):
            await responder.send(
                f"✏️ Apodo de {member.mention}: **{discord.utils.escape_markdown(nick)}**."
                if nick
                else f"✏️ Apodo de {member.mention} quitado.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.command(name="role", description="Da o quita un rol a un miembro.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def role(
        self, interaction: discord.Interaction, miembro: discord.Member, rol: discord.Role
    ) -> None:
        """Si el miembro tiene el rol, se lo quita; si no, se lo da."""
        await self._role_impl(InteractionResponder(interaction), miembro, rol)

    @commands.command(name="role")
    async def role_text(
        self, ctx: commands.Context, miembro: discord.Member, *, rol: discord.Role
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._role_impl(ContextResponder(ctx), miembro, rol)

    async def _role_impl(
        self, responder: CommandResponder, member: discord.Member, role: discord.Role
    ) -> None:
        actor = responder.member
        guild = member.guild
        assert actor is not None
        if role.is_default() or role.managed:
            await responder.send_error("Ese rol no se puede asignar a mano.")
            return
        if actor.id != guild.owner_id and role >= actor.top_role:
            await responder.send_error("Ese rol es igual o superior al tuyo.")
            return
        if role >= guild.me.top_role:
            await responder.send_error("Ese rol es igual o superior al mío; súbeme en la lista.")
            return
        reason = _audit_reason(actor, "role")
        had_role = role in member.roles
        action = (
            member.remove_roles(role, reason=reason)
            if had_role
            else member.add_roles(role, reason=reason)
        )
        if await self._attempt(responder, action):
            verb = "quitado a" if had_role else "dado a"
            await responder.send(
                f"🏷️ Rol {role.mention} {verb} {member.mention}.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    # --- bienv ------------------------------------------------------------

    @app_commands.command(name="bienv", description="Configura el GIF y el canal de bienvenida.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        gif="Enlace de Tenor, Giphy o .gif; «quitar» vuelve al vídeo.",
        canal="Canal donde se da la bienvenida.",
    )
    async def bienv(
        self,
        interaction: discord.Interaction,
        gif: app_commands.Range[str, 1, 512] | None = None,
        canal: discord.TextChannel | None = None,
    ) -> None:
        """Cambia el GIF o el canal de bienvenida y enseña cómo queda (solo a ti)."""
        await self._bienv_impl(InteractionResponder(interaction), gif, canal)

    @commands.command(name="bienv")
    async def bienv_text(
        self,
        ctx: commands.Context,
        canal: discord.TextChannel | None = None,
        *,
        gif: str = "",
    ) -> None:
        """Versión de texto: `.bienv`, `.bienv <enlace>`, `.bienv quitar`, `.bienv #canal`."""
        await self._bienv_impl(ContextResponder(ctx), gif.strip() or None, canal)

    async def _bienv_impl(
        self,
        responder: CommandResponder,
        gif: str | None,
        channel: discord.TextChannel | None,
    ) -> None:
        """Guarda lo que haya cambiado y responde con el resumen y una vista previa.

        Sin argumentos solo enseña la configuración actual.
        """
        guild = responder.guild
        member = responder.member
        repository = getattr(self.bot, "welcome", None)
        welcome = self.bot.get_cog("Welcome")
        if guild is None or member is None or repository is None or welcome is None:
            await responder.send_error("La bienvenida no está disponible ahora mismo.")
            return
        current: WelcomeSettings = await repository.settings(guild.id)
        gif_url = current.gif_url
        if gif is not None:
            if gif.lower() in GIF_RESET_WORDS:
                gif_url = None
            else:
                try:
                    classify_gif(gif)
                except GifError as error:
                    await responder.send_error(str(error))
                    return
                gif_url = gif.strip()
        updated = WelcomeSettings(
            gif_url=gif_url,
            channel_id=channel.id if channel is not None else current.channel_id,
        )
        if updated != current:
            await repository.save_settings(guild.id, updated)

        target = welcome.welcome_channel(guild, updated)
        lines = ["✅ Bienvenida actualizada." if updated != current else "👋 Bienvenida actual."]
        lines.append(f"Canal: {target.mention if target else '⚠️ ninguno (crea #chat-general)'}")
        lines.append(
            f"GIF: <{gif_url}>" if gif_url else "GIF: ninguno, se manda el vídeo de Kratos."
        )
        if target is not None and not target.permissions_for(guild.me).send_messages:
            lines.append("⚠️ No puedo escribir en ese canal; revisa mis permisos.")
        content, embed = await welcome.preview(member, updated)
        lines += ["", "**Así se verá:**", content]
        await responder.send(
            "\n".join(lines),
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de administración en el cliente."""
    await bot.add_cog(Admin(bot))
