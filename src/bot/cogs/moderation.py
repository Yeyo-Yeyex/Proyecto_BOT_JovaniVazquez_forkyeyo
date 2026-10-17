"""Panel de moderación `admin`: actividad, sanciones, historial y notas en un menú.

`admin [miembro]` abre un panel privado (efímero) solo para administradores. Con
`/admin` sale directamente; con `.admin` sale un botón para abrirlo, porque un
mensaje de texto no puede ser privado y el panel enseña notas de moderador.

- **Servidor** (sin miembro elegido): miembros, conectados, en voz y aislados
  ahora, y listas de 💤 más inactivos, 💬 últimos en escribir, 🎙️ últimos en voz,
  🆕 recién llegados, 🔇 aislados y 📜 últimas sanciones. Botones del canal donde
  se abrió: cerrar, abrir, modo lento, borrar mensajes e indultar por ID.
- **Miembro** (elegido en el desplegable o con `admin @miembro`): cuenta, roles,
  último mensaje, última vez en voz y conectado, mensajes, aislamiento, historial
  de sanciones y notas de moderador. Botones para callar, hablar, echar, banear,
  apodo, dar o quitar roles, borrar sus mensajes en el canal, notas e historial.

Las acciones son las de los comandos sueltos del cog `Admin` (`callar`, `echar`…,
que siguen existiendo): el panel las llama con `find_cog`, así que valen las mismas
comprobaciones de jerarquía y permisos y dejan el mismo rastro en la auditoría.

Última actividad: este cog apunta, sin guardar contenido, cuándo escribe cada
miembro y en qué canal, cuándo entra o sale de voz y cuándo se le ve conectado
(`on_presence_update`, que necesita el «Presence Intent» del portal; sin él el bot
arranca igual y esa línea dice «sin datos»). Lo junta en memoria y lo vuelca a
`ActivityRepository` cada minuto. Solo sabe lo que ha visto desde que existe.

Historial: cada sanción del cog `Admin` se apunta con `record` (función puente) en
`ModerationRepository`. Las notas solo se ven aquí. Comando de administración: sin
logros (excepción de la Biblia, «Logros»).

Permisos del bot: los de cada acción (ver `bot.cogs.admin`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import admin as admin_cog
from bot.repositories.activity import LastSeen
from bot.services.moderation import MAX_PURGE, MAX_SLOWMODE_SECONDS, format_duration
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.moderation import Note, Sanction

logger = logging.getLogger(__name__)

#: El panel es efímero y Discord solo deja editarlo durante 15 minutos.
VIEW_TIMEOUT = 14 * 60
#: Cada cuánto se vuelca a la base la actividad juntada en memoria.
FLUSH_SECONDS = 60
#: Filas de cada lista del servidor.
LIST_SIZE = 15
#: Sanciones y notas que se ven en la ficha de un miembro.
FICHA_ITEMS = 5
#: Sanciones del botón 📜 Historial.
HISTORY_ITEMS = 25
MAX_NOTE_LENGTH = 500
FIELD_LIMIT = 1024
COLOR = discord.Color.from_rgb(190, 60, 60)
NOT_ADMIN = "Este panel es solo para administradores."
NO_ADMIN_COG = "Las herramientas de moderación no están disponibles ahora mismo."

#: Vistas del servidor: clave, emoji, nombre y descripción del desplegable.
SERVER_VIEWS: tuple[tuple[str, str, str, str], ...] = (
    ("resumen", "📋", "Resumen", "Cifras del servidor y últimas sanciones"),
    ("inactivos", "💤", "Más inactivos", "Quien lleva más tiempo sin dar señales"),
    ("escritos", "💬", "Últimos en escribir", "Quién ha escrito hace menos"),
    ("voz", "🎙️", "Últimos en voz", "Quién está o ha estado en voz"),
    ("nuevos", "🆕", "Recién llegados", "Las últimas entradas al servidor"),
    ("aislados", "🔇", "Aislados ahora", "Quién está callado y hasta cuándo"),
    ("sanciones", "📜", "Últimas sanciones", "Lo último que han hecho los admins"),
)

#: Cómo se lee cada tipo de sanción del historial.
KIND_LABELS = {
    "callar": "🔇 Callado",
    "hablar": "🔊 Descallado",
    "echar": "👢 Echado",
    "banear": "🔨 Baneado",
    "indultar": "✅ Indultado",
}

STATUS_LABELS = {
    discord.Status.online: "🟢 En línea",
    discord.Status.idle: "🌙 Ausente",
    discord.Status.dnd: "⛔ No molestar",
}

#: Frases de pie del panel, por variar: la moderación también tiene su gracia.
FOOTERS = (
    "Ni el CNI de Sánchez sabe tanto. Actividad desde que el bot la apunta.",
    "Más vigilado que un tuit de Musk. Actividad desde que el bot la apunta.",
    "Expediente X, versión Jovani. Actividad desde que el bot la apunta.",
)


def _when(stamp: float | None) -> str:
    """`hace 3 días` (en el idioma del cliente) o «sin rastro»."""
    if stamp is None:
        return "sin rastro"
    return discord.utils.format_dt(datetime.fromtimestamp(stamp, UTC), "R")


def _clip(text: str, limit: int = FIELD_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_admin(user: discord.abc.User) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


def last_active(member: discord.Member, seen: LastSeen, now: float) -> float | None:
    """Última señal de vida del miembro; `now` si está conectado o en voz ahora mismo."""
    if member.status is not discord.Status.offline or member.voice is not None:
        return now
    stamps = [s for s in (seen.message_at, seen.voice_at, seen.online_at) if s is not None]
    return max(stamps, default=None)


def sanction_line(sanction: Sanction) -> str:
    """Una línea del historial: cuándo, qué, quién y por qué."""
    label = KIND_LABELS.get(sanction.kind, sanction.kind)
    if sanction.seconds:
        label += f" {format_duration(timedelta(seconds=sanction.seconds))}"
    reason = f" — {discord.utils.escape_markdown(sanction.reason)}" if sanction.reason else ""
    who = f"<@{sanction.user_id}> por <@{sanction.actor_id}>"
    return f"{_when(sanction.created_at)} {label} {who}{reason}"


def note_line(note: Note) -> str:
    """Una nota: su número (para borrarla), cuándo, quién y el texto."""
    text = discord.utils.escape_markdown(note.text)
    return f"`#{note.id}` {_when(note.created_at)} <@{note.author_id}>: {text}"


async def record(
    bot: commands.Bot,
    guild_id: int,
    user_id: int,
    actor_id: int,
    kind: str,
    reason: str | None = None,
    seconds: int | None = None,
) -> None:
    """Apunta una sanción en el historial del panel; no hace nada si el cog no está."""
    cog = find_cog(bot, Moderation)
    if cog is None:
        return
    try:
        await cog.bot.moderation.record(guild_id, user_id, actor_id, kind, reason, seconds)
    except Exception:
        # El historial es un extra: si falla, la sanción ya está hecha y se dice en el log.
        logger.exception("No se pudo apuntar la sanción %s en el historial", kind)


# --- Formularios ---------------------------------------------------------------------


class _Form(discord.ui.Modal):
    """Formulario del panel: al enviarlo, acepta y llama a `action` con los textos."""

    def __init__(
        self,
        title: str,
        fields: list[discord.ui.TextInput],
        action: Callable[[discord.Interaction, list[str]], Awaitable[None]],
    ) -> None:
        super().__init__(title=title, timeout=VIEW_TIMEOUT)
        self.fields = fields
        self.action = action
        for field in fields:
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        await self.action(interaction, [field.value.strip() for field in self.fields])


def _text(
    label: str,
    *,
    default: str = "",
    required: bool = True,
    long: bool = False,
    max_length: int = 100,
) -> discord.ui.TextInput:
    return discord.ui.TextInput(
        label=label,
        default=default or None,
        required=required,
        max_length=max_length,
        style=discord.TextStyle.paragraph if long else discord.TextStyle.short,
    )


def _reason() -> discord.ui.TextInput:
    return _text("Motivo (queda en la auditoría)", required=False, max_length=400)


# --- Panel ---------------------------------------------------------------------------


class MemberSelect(discord.ui.UserSelect):
    """Desplegable para elegir a quién mirar."""

    def __init__(self, panel: AdminPanel) -> None:
        super().__init__(placeholder="🔎 Elige un miembro", row=0, custom_id="admin:miembro")
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        picked = self.values[0]
        member = (
            picked if isinstance(picked, discord.Member) else self.panel.guild.get_member(picked.id)
        )
        if member is None:
            await notify(interaction, "Ese usuario ya no está en el servidor.")
            return
        self.panel.member = member
        await self.panel.redraw(interaction)


class ServerViewSelect(discord.ui.Select):
    """Desplegable de las listas del servidor."""

    def __init__(self, panel: AdminPanel) -> None:
        options = [
            discord.SelectOption(
                label=label, value=key, emoji=emoji, description=text, default=key == panel.key
            )
            for key, emoji, label, text in SERVER_VIEWS
        ]
        super().__init__(
            placeholder="Elige una lista", options=options, row=1, custom_id="admin:vista"
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        self.panel.key = self.values[0]
        await self.panel.redraw(interaction)


class RoleToggle(discord.ui.RoleSelect):
    """Desplegable para dar o quitar un rol al miembro de la ficha."""

    def __init__(self, panel: AdminPanel) -> None:
        super().__init__(placeholder="🎭 Dar o quitar un rol", row=2, custom_id="admin:rol")
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        await self.panel.run(
            interaction, lambda admin, responder, member: admin._role_impl(responder, member, role)
        )


class AdminPanel(discord.ui.View):
    """Panel privado de `admin`: el servidor o la ficha de un miembro, con sus acciones.

    Solo lo maneja quien lo abrió, y solo mientras siga siendo administrador.
    """

    def __init__(
        self,
        cog: Moderation,
        guild: discord.Guild,
        owner: discord.abc.User,
        member: discord.Member | None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.member = member
        self.key = "resumen"
        self.rebuild()

    def rebuild(self) -> None:
        """Pone los controles de la vista actual (servidor o miembro)."""
        self.clear_items()
        self.add_item(MemberSelect(self))
        if self.member is None:
            self.add_item(ServerViewSelect(self))
            self._button("🔒 Cerrar canal", self._lock, "admin:cerrar", row=2)
            self._button("🔓 Abrir canal", self._unlock, "admin:abrir", row=2)
            self._button("🐢 Modo lento", self._slow, "admin:lento", row=2)
            self._button("🧹 Borrar mensajes", self._purge, "admin:borrar", row=2)
            self._button("✅ Indultar", self._unban, "admin:indultar", row=2)
            return
        danger = discord.ButtonStyle.danger
        self._button("🔇 Callar", self._mute, "admin:callar", row=1)
        self._button("🔊 Hablar", self._unmute, "admin:hablar", row=1)
        self._button("👢 Echar", self._kick, "admin:echar", row=1, style=danger)
        self._button("🔨 Banear", self._ban, "admin:banear", row=1, style=danger)
        self._button("✏️ Apodo", self._nick, "admin:apodo", row=1)
        self.add_item(RoleToggle(self))
        self._button("📝 Nota", self._add_note, "admin:nota", row=3)
        self._button("🗑️ Borrar nota", self._delete_note, "admin:quitarnota", row=3)
        self._button("📜 Historial", self._history, "admin:historial", row=3)
        self._button("🧹 Sus mensajes", self._purge_member, "admin:limpiar", row=3)
        self._button(
            "◀ Servidor", self._back, "admin:volver", row=3, style=discord.ButtonStyle.primary
        )

    def _button(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        custom_id: str,
        *,
        row: int,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=style, row=row, custom_id=custom_id
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo quien lo abrió y solo si sigue siendo administrador (se mira en cada clic)."""
        if interaction.user.id == self.owner.id and _is_admin(interaction.user):
            return True
        await notify(interaction, NOT_ADMIN)
        return False

    async def redraw(self, interaction: discord.Interaction) -> None:
        """Vuelve a pintar el panel en su mensaje (acepta el clic antes: lee la base)."""
        await ack(interaction)
        if self.member is not None:
            # El miembro puede haber cambiado (roles, apodo, aislamiento) o haberse ido.
            self.member = self.guild.get_member(self.member.id)
        self.rebuild()
        embed = await self.cog.embed(self)
        await edit(interaction, embed=embed, view=self)

    async def run(
        self,
        interaction: discord.Interaction,
        action: Callable[
            [admin_cog.Admin, InteractionResponder, discord.Member], Awaitable[object]
        ],
    ) -> None:
        """Hace una acción del cog `Admin` sobre el miembro de la ficha y repinta."""
        await ack(interaction)
        admin = find_cog(self.cog.bot, admin_cog.Admin)
        if admin is None or self.member is None:
            await notify(interaction, NO_ADMIN_COG)
            return
        await action(admin, InteractionResponder(interaction), self.member)
        await self.redraw(interaction)

    async def run_here(
        self,
        interaction: discord.Interaction,
        action: Callable[[admin_cog.Admin, InteractionResponder], Awaitable[object]],
    ) -> None:
        """Hace una acción del cog `Admin` sobre el canal o el servidor y repinta."""
        await ack(interaction)
        admin = find_cog(self.cog.bot, admin_cog.Admin)
        if admin is None:
            await notify(interaction, NO_ADMIN_COG)
            return
        await action(admin, InteractionResponder(interaction))
        await self.redraw(interaction)

    def _form(
        self,
        title: str,
        fields: list[discord.ui.TextInput],
        action: Callable[[discord.Interaction, list[str]], Awaitable[None]],
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        """Callback de botón que abre un formulario (primera respuesta: sin `ack`)."""

        async def open_form(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(_Form(title, fields, action))

        return open_form

    # -- Ficha del miembro ---------------------------------------------------------

    async def _mute(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            await self.run(submit, lambda a, r, m: a._mute_impl(r, m, values[0], values[1] or None))

        duration = _text("Duración (10m, 2h, 1d… máx. 28d)", default="10m", max_length=20)
        await self._form("🔇 Callar", [duration, _reason()], done)(interaction)

    async def _unmute(self, interaction: discord.Interaction) -> None:
        await self.run(interaction, lambda a, r, m: a._unmute_impl(r, m))

    async def _kick(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            await self.run(submit, lambda a, r, m: a._kick_impl(r, m, values[0] or None))

        await self._form("👢 Echar del servidor", [_reason()], done)(interaction)

    async def _ban(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            await self.run(submit, lambda a, r, m: a._ban_impl(r, m, values[0] or None))

        await self._form("🔨 Banear del servidor", [_reason()], done)(interaction)

    async def _nick(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            await self.run(submit, lambda a, r, m: a._nick_impl(r, m, values[0] or None))

        current = self.member.nick if self.member is not None else None
        field = _text(
            "Apodo (vacío para quitarlo)", default=current or "", required=False, max_length=32
        )
        await self._form("✏️ Apodo", [field], done)(interaction)

    async def _purge_member(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            await self._do_purge(submit, values[0], self.member)

        field = _text(
            f"Cuántos de sus mensajes de este canal (1-{MAX_PURGE})", default="20", max_length=3
        )
        await self._form("🧹 Borrar sus mensajes", [field], done)(interaction)

    async def _add_note(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            if self.member is not None:
                await self.cog.bot.moderation.add_note(
                    self.guild.id, self.member.id, submit.user.id, values[0]
                )
            await self.redraw(submit)

        field = _text("Nota (solo la ven los admins)", long=True, max_length=MAX_NOTE_LENGTH)
        await self._form("📝 Nota de moderador", [field], done)(interaction)

    async def _delete_note(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            number = values[0].lstrip("#")
            if not number.isdigit() or not await self.cog.bot.moderation.delete_note(
                self.guild.id, int(number)
            ):
                await notify(submit, "No hay ninguna nota con ese número.")
                return
            await self.redraw(submit)

        field = _text("Número de la nota (el #)", max_length=12)
        await self._form("🗑️ Borrar nota", [field], done)(interaction)

    async def _history(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.member is None:
            return
        sanctions = await self.cog.bot.moderation.sanctions(
            self.guild.id, self.member.id, limit=HISTORY_ITEMS
        )
        lines = [sanction_line(s) for s in sanctions] or ["Expediente limpio como el de un santo."]
        embed = discord.Embed(
            title=f"📜 Historial de {self.member.display_name}",
            description=_clip("\n".join(lines), 4096),
            color=COLOR,
        )
        await notify(interaction, embed=embed)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.member = None
        await self.redraw(interaction)

    # -- Servidor y canal ----------------------------------------------------------

    async def _lock(self, interaction: discord.Interaction) -> None:
        await self.run_here(interaction, lambda a, r: a._set_locked(r, True))

    async def _unlock(self, interaction: discord.Interaction) -> None:
        await self.run_here(interaction, lambda a, r: a._set_locked(r, False))

    async def _slow(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            if not values[0].isdigit():
                await notify(submit, f"Pon los segundos, de 0 a {MAX_SLOWMODE_SECONDS}.")
                return
            await self.run_here(submit, lambda a, r: a._slow_impl(r, int(values[0])))

        field = _text(
            f"Segundos entre mensajes (0 lo quita, máx. {MAX_SLOWMODE_SECONDS})",
            default="0",
            max_length=5,
        )
        await self._form("🐢 Modo lento de este canal", [field], done)(interaction)

    async def _purge(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            await self._do_purge(submit, values[0], None)

        field = _text(f"Cuántos mensajes de este canal (1-{MAX_PURGE})", default="10", max_length=3)
        await self._form("🧹 Borrar mensajes", [field], done)(interaction)

    async def _unban(self, interaction: discord.Interaction) -> None:
        async def done(submit: discord.Interaction, values: list[str]) -> None:
            await self.run_here(submit, lambda a, r: a._unban_impl(r, values[0]))

        field = _text("ID del usuario baneado", max_length=25)
        await self._form("✅ Indultar", [field], done)(interaction)

    async def _do_purge(
        self, interaction: discord.Interaction, text: str, member: discord.Member | None
    ) -> None:
        """Borra mensajes recientes del canal del panel (de `member`, si se indica)."""
        channel = interaction.channel
        admin = find_cog(self.cog.bot, admin_cog.Admin)
        if admin is None:
            await notify(interaction, NO_ADMIN_COG)
            return
        if not text.isdigit() or not 1 <= int(text) <= MAX_PURGE:
            await notify(interaction, f"La cantidad debe estar entre 1 y {MAX_PURGE}.")
            return
        if not isinstance(channel, admin_cog.PurgeableChannel):
            await notify(interaction, "Aquí no se pueden borrar mensajes.")
            return
        try:
            count = await admin._purge(
                channel, int(text), member, admin_cog._audit_reason(interaction.user, "purge")
            )
        except discord.Forbidden:
            await notify(interaction, admin_cog.BOT_FORBIDDEN)
            return
        await notify(interaction, f"🧹 {count} mensaje(s) borrado(s).")

    async def on_timeout(self) -> None:
        """Al caducar no se edita: el mensaje es efímero y ya no se deja tocar."""
        self.stop()


class OpenPanel(discord.ui.View):
    """Botón de `.admin`: abre el panel en privado a quien lo pulsa, si es administrador."""

    def __init__(self, cog: Moderation, member: discord.Member | None) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.member = member

    @discord.ui.button(
        label="🛡️ Abrir el panel", style=discord.ButtonStyle.danger, custom_id="admin:abrir_panel"
    )
    async def open_panel(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if interaction.guild is None or not _is_admin(interaction.user):
            await notify(interaction, NOT_ADMIN)
            return
        await ack(interaction, new_message=True)
        panel = AdminPanel(self.cog, interaction.guild, interaction.user, self.member)
        await edit(interaction, embed=await self.cog.embed(panel), view=panel)


# --- Cog -----------------------------------------------------------------------------


class Moderation(commands.Cog):
    """`admin`: panel de moderación con la actividad de los miembros."""

    def __init__(self, bot: BotClient) -> None:
        self.bot = bot
        # Cambios de actividad aún sin volcar: {(servidor, miembro): {columna: valor}}.
        self._pending: dict[tuple[int, int], dict[str, float | int]] = {}

    async def cog_load(self) -> None:
        self._flush.start()

    async def cog_unload(self) -> None:
        self._flush.cancel()
        await self._save()

    # Mismas comprobaciones que el cog `Admin`: solo administradores y dentro de un servidor.
    async def cog_check(self, ctx: commands.Context) -> bool:  # type: ignore[override]
        return await admin_cog.Admin.cog_check(self, ctx)  # type: ignore[arg-type]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # type: ignore[override]
        return await admin_cog.Admin.interaction_check(self, interaction)  # type: ignore[arg-type]

    # -- Actividad ------------------------------------------------------------------

    def _mark(self, guild_id: int, user_id: int, **fields: float | int) -> None:
        self._pending.setdefault((guild_id, user_id), {}).update(fields)

    @tasks.loop(seconds=FLUSH_SECONDS)
    async def _flush(self) -> None:
        await self._save()

    async def _save(self) -> None:
        batch, self._pending = self._pending, {}
        if not batch:
            return
        try:
            await self.bot.last_seen.save(batch)
        except Exception:
            logger.exception("No se pudo guardar la última actividad de %d miembros", len(batch))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        self._mark(
            message.guild.id,
            message.author.id,
            message_at=time.time(),
            message_channel=message.channel.id,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot or before.channel == after.channel:
            return
        channel = after.channel or before.channel
        assert channel is not None  # distintos y uno es None: el otro no lo es
        self._mark(member.guild.id, member.id, voice_at=time.time(), voice_channel=channel.id)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.bot or before.status == after.status:
            return
        # Conectarse, desconectarse o cambiar de estado: estaba en línea en este momento.
        self._mark(after.guild.id, after.id, online_at=time.time())

    async def last_seen(self, guild_id: int) -> dict[int, LastSeen]:
        """Lo guardado de cada miembro con lo que aún está en memoria encima."""
        seen = await self.bot.last_seen.guild(guild_id)
        for (gid, user_id), fields in self._pending.items():
            if gid == guild_id:
                old = seen.get(user_id, LastSeen())
                seen[user_id] = LastSeen(**{**_as_dict(old), **fields})
        return seen

    # -- Embeds ---------------------------------------------------------------------

    async def embed(self, panel: AdminPanel) -> discord.Embed:
        """Lo que enseña el panel ahora: la ficha del miembro o la lista del servidor."""
        if panel.member is not None:
            embed = await self.member_embed(panel.member)
        else:
            embed = await self.server_embed(panel.guild, panel.key)
        embed.set_footer(text=FOOTERS[int(time.time()) % len(FOOTERS)])
        return embed

    async def member_embed(self, member: discord.Member) -> discord.Embed:
        """Ficha de moderación de un miembro."""
        guild_id = member.guild.id
        seen = (await self.last_seen(guild_id)).get(member.id, LastSeen())
        sanctions = await self.bot.moderation.sanctions(guild_id, member.id, limit=FICHA_ITEMS)
        counts = await self.bot.moderation.sanction_counts(guild_id, member.id)
        notes = await self.bot.moderation.notes(guild_id, member.id, limit=FICHA_ITEMS)
        messages = await self.bot.message_stats.message_count(guild_id, member.id)

        embed = discord.Embed(
            title=f"🛡️ {member.display_name}",
            description=f"{member.mention} · `{member.id}`" + (" · 🤖 bot" if member.bot else ""),
            color=member.color if member.color.value else COLOR,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        joined = _when(member.joined_at.timestamp()) if member.joined_at else "no se sabe"
        embed.add_field(
            name="🪪 Cuenta",
            value=f"Creada {_when(member.created_at.timestamp())}\nEntró {joined}",
        )
        status = STATUS_LABELS.get(member.status)
        embed.add_field(
            name="📶 Conectado",
            value=f"{status} ahora" if status else f"⚫ Última vez {_when(seen.online_at)}",
        )
        embed.add_field(name="📊 Mensajes", value=f"{messages:,}".replace(",", "."))
        last_message = _when(seen.message_at)
        if seen.message_channel is not None:
            last_message += f" en <#{seen.message_channel}>"
        embed.add_field(name="💬 Último mensaje", value=last_message)
        if member.voice is not None and member.voice.channel is not None:
            voice = f"🔴 Ahora en <#{member.voice.channel.id}>"
        else:
            voice = _when(seen.voice_at)
            if seen.voice_channel is not None:
                voice += f" en <#{seen.voice_channel}>"
        embed.add_field(name="🎙️ Voz", value=voice)
        until = member.timed_out_until
        embed.add_field(
            name="🔇 Aislado",
            value=f"Hasta {discord.utils.format_dt(until, 'R')}"
            if member.is_timed_out() and until
            else "No",
        )
        roles = [role.mention for role in reversed(member.roles) if not role.is_default()]
        embed.add_field(
            name=f"🎭 Roles ({len(roles)})", value=_clip(" ".join(roles) or "Ninguno"), inline=False
        )
        summary = " · ".join(f"{KIND_LABELS.get(k, k)} ×{n}" for k, n in sorted(counts.items()))
        history = "\n".join(sanction_line(s) for s in sanctions)
        embed.add_field(
            name="📜 Sanciones",
            value=_clip(f"{summary}\n{history}" if counts else "Expediente limpio."),
            inline=False,
        )
        embed.add_field(
            name="📝 Notas de moderador",
            value=_clip("\n".join(note_line(n) for n in notes) or "Sin notas."),
            inline=False,
        )
        return embed

    async def server_embed(self, guild: discord.Guild, key: str) -> discord.Embed:
        """Resumen del servidor o una de sus listas (`SERVER_VIEWS`)."""
        now = time.time()
        humans = [m for m in guild.members if not m.bot]
        title = next(f"{emoji} {label}" for k, emoji, label, _ in SERVER_VIEWS if k == key)
        embed = discord.Embed(title=f"🛡️ {guild.name} · {title}", color=COLOR)
        if key == "sanciones":
            sanctions = await self.bot.moderation.sanctions(guild.id, limit=LIST_SIZE)
            embed.description = (
                "\n".join(sanction_line(s) for s in sanctions) or "Ninguna todavía. Paz social."
            )
            return embed
        if key == "resumen":
            online = sum(m.status is not discord.Status.offline for m in humans)
            voice = sum(m.voice is not None for m in humans)
            muted = sum(m.is_timed_out() for m in humans)
            embed.description = (
                f"👥 **{len(humans)}** personas y **{len(guild.members) - len(humans)}** bots\n"
                f"🟢 **{online}** conectadas · 🎙️ **{voice}** en voz · 🔇 **{muted}** aisladas"
            )
            sanctions = await self.bot.moderation.sanctions(guild.id, limit=FICHA_ITEMS)
            embed.add_field(
                name="📜 Últimas sanciones",
                value=_clip("\n".join(sanction_line(s) for s in sanctions) or "Ninguna todavía."),
                inline=False,
            )
            embed.add_field(
                name="Cómo se usa",
                value="Elige un miembro para ver su ficha y sancionarle, "
                "o una lista en el segundo menú. Los botones de canal "
                "actúan donde abriste el panel.",
                inline=False,
            )
            return embed

        seen = await self.last_seen(guild.id)
        blank = LastSeen()
        if key == "inactivos":
            ranked = sorted(humans, key=lambda m: last_active(m, seen.get(m.id, blank), now) or 0)
            lines = [
                f"{m.mention} — {_when(last_active(m, seen.get(m.id, blank), now))}" for m in ranked
            ]
        elif key == "escritos":
            wrote = [m for m in humans if seen.get(m.id, blank).message_at is not None]
            wrote.sort(key=lambda m: seen[m.id].message_at or 0, reverse=True)
            lines = [
                f"{m.mention} — {_when(seen[m.id].message_at)} en <#{seen[m.id].message_channel}>"
                for m in wrote
            ]
        elif key == "voz":
            live = [m for m in humans if m.voice is not None and m.voice.channel is not None]
            lines = [f"{m.mention} — 🔴 ahora en <#{m.voice.channel.id}>" for m in live]  # type: ignore[union-attr]
            past = [
                m for m in humans if m not in live and seen.get(m.id, blank).voice_at is not None
            ]
            past.sort(key=lambda m: seen[m.id].voice_at or 0, reverse=True)
            lines += [
                f"{m.mention} — {_when(seen[m.id].voice_at)} en <#{seen[m.id].voice_channel}>"
                for m in past
            ]
        elif key == "nuevos":
            joined = sorted(
                (m for m in humans if m.joined_at),
                key=lambda m: m.joined_at,
                reverse=True,  # type: ignore[arg-type,return-value]
            )
            lines = [f"{m.mention} — entró {_when(m.joined_at.timestamp())}" for m in joined]  # type: ignore[union-attr]
        else:  # aislados
            lines = [
                f"{m.mention} — hasta {discord.utils.format_dt(m.timed_out_until, 'R')}"
                for m in humans
                if m.is_timed_out() and m.timed_out_until
            ]
        embed.description = _clip("\n".join(lines[:LIST_SIZE]) or "Nadie por aquí.", 4096)
        return embed

    # -- Comando --------------------------------------------------------------------

    @app_commands.command(
        name="admin", description="Panel de moderación: actividad, sanciones y notas."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(miembro="Abrir directamente la ficha de este miembro.")
    async def admin(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Abre el panel en privado."""
        assert interaction.guild is not None  # guild_only
        await ack(interaction, new_message=True)
        panel = AdminPanel(self, interaction.guild, interaction.user, miembro)
        await edit(interaction, embed=await self.embed(panel), view=panel)

    @commands.command(name="admin")
    async def admin_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto: un botón que abre el panel en privado (las notas no son públicas)."""
        await ctx.send(
            "🛡️ El panel de moderación se abre en privado: pulsa el botón.",
            view=OpenPanel(self, miembro),
        )


def _as_dict(seen: LastSeen) -> dict[str, float | int | None]:
    return {name: getattr(seen, name) for name in LastSeen.__slots__}


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el panel de moderación."""
    await bot.add_cog(Moderation(bot))
