"""Lista de cosas que hacer del servidor: el comando `lista`.

- `lista`: vuelve a publicar la lista en el canal actual.
- `lista <tarea> [prioridad alta|media|baja]`: la apunta y vuelve a publicar
  la lista. Con `/lista`, la prioridad también se puede elegir en un
  desplegable. Si no se dice, el bot la pregunta con tres botones (en privado
  con `/lista`; con `.lista`, en un mensaje que solo puede pulsar quien la
  apunta y que se borra al elegir). Sin respuesta en dos minutos, es media.

La lista sale siempre ordenada (alta, media, baja y, dentro de cada una, de
la más antigua a la más nueva), en páginas de 25 que se pasan con ◀ y ▶, y
solo hay una a la vista: al publicar otra, el bot borra la anterior. Con
`.lista <tarea>` también borra el mensaje de quien la apunta, para que el
canal no se llene de órdenes sueltas; para eso necesita **Gestionar
mensajes** (si no lo tiene, lo deja y sigue).

Las tareas se tachan con el menú de la propia lista (el de cada página).
Puede tacharlas quien las apuntó o un administrador; al tacharlas se borran.
Los mismos pueden editarlas con el menú ✏️ de debajo: abre un formulario con
el nombre ya escrito y la prioridad elegida, y al guardar la lista se
actualiza en su sitio. La tarea conserva su antigüedad.

Todo sigue valiendo tras reiniciar el bot: los menús y las flechas son
`DynamicItem` y el formulario lo contesta `Lista.on_interaction` por su
`custom_id` (`lista:form:<tarea>:<página>`), aunque se abriera antes del reinicio.

Logros: apuntar (`todo_added`), tachar (`todo_done`) y editar (`todo_edited`,
`todo_demoted`), categoría 📝 Lista.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from functools import partial
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.repositories.todo import TodoRepository
from bot.services.achievements import StatDelta
from bot.services.telemetry import TimedLock
from bot.services.todo import (
    DEFAULT_PRIORITY,
    MAX_TASK_LENGTH,
    MAX_TASKS,
    PAGE_SIZE,
    Priority,
    Task,
    TaskError,
    can_complete,
    parse_task,
    sort_tasks,
)
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, claim_custom_ids, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(250, 190, 60)
SELECT_ID = "lista:hecha"
PAGE_ID = "lista:pag"
EDIT_ID = "lista:editar"
FORM_ID = "lista:form"
FORM_RE = re.compile(r"lista:form:(?P<id>\d+):(?P<page>\d+)")
NO_MENTIONS = discord.AllowedMentions.none()
USAGE = "`lista <tarea> prioridad alta|media|baja`"
#: Segundos para elegir la prioridad antes de apuntarla como media.
PICK_TIMEOUT = 120


def _author_name(guild: discord.Guild, user_id: int) -> str:
    """Nombre visible de quien apuntó la tarea, o una mención si ya no está."""
    member = guild.get_member(user_id)
    return discord.utils.escape_markdown(member.display_name) if member else f"<@{user_id}>"


def build_board(
    guild: discord.Guild, tasks: list[Task], note: str | None = None, page: int = 0
) -> tuple[discord.Embed, discord.ui.View | None]:
    """Embed con una página de la lista ordenada y el menú para tachar.

    Args:
        note: Línea opcional sobre el último cambio («Diego apuntó…»).
        page: Página que se enseña, desde 0. Si ya no existe (se tacharon
            tareas), sale la última.

    Returns:
        `(embed, vista)`; la vista es `None` si no queda nada pendiente.
    """
    ordered = sort_tasks(tasks)
    pages = max(1, math.ceil(len(ordered) / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    shown = ordered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    if ordered:
        lines = [
            f"`{index:>2}.` {task.priority.emoji} **{discord.utils.escape_markdown(task.text)}**"
            f" · {_author_name(guild, task.author_id)}"
            for index, task in enumerate(shown, start=page * PAGE_SIZE + 1)
        ]
        description = "\n".join(lines)
    else:
        description = "Nada pendiente. A gozar la vida, que esto está al día. 🌴"
    if note:
        description = f"{note}\n\n{description}"
    embed = discord.Embed(
        title=f"📝 Cosas que hacer ({len(ordered)}/{MAX_TASKS})",
        description=description,
        color=COLOR,
    )
    footer = "🔥 alta · 📌 media · 💤 baja — apunta con .lista <tarea> prioridad alta"
    if pages > 1:
        footer = f"Página {page + 1}/{pages} · {footer}"
    embed.set_footer(text=footer)
    if not ordered:
        return embed, None
    select: discord.ui.Select = discord.ui.Select(
        custom_id=f"{SELECT_ID}:{page}",
        placeholder="✅ Tachar tareas hechas…",
        min_values=1,
        max_values=len(shown),
        options=[
            discord.SelectOption(
                label=task.text[:100],
                value=str(task.id),
                emoji=task.priority.emoji,
                description=f"Prioridad {task.priority.label}",
            )
            for task in shown
        ],
        row=0,
    )
    # El valor lleva prioridad y autor: así ✏️ abre el formulario (y dice si no es
    # tuya) sin esperar a la base de datos, como pide Discord antes de un formulario.
    edit_select: discord.ui.Select = discord.ui.Select(
        custom_id=f"{EDIT_ID}:{page}",
        placeholder="✏️ Editar una tarea…",
        options=[
            discord.SelectOption(
                label=task.text[:100],
                value=f"{task.id}:{task.priority.value}:{task.author_id}",
                emoji=task.priority.emoji,
                description=f"Prioridad {task.priority.label}",
            )
            for task in shown
        ],
        row=1,
    )
    view = discord.ui.View(timeout=None)
    view.add_item(DoneSelect(select, page))
    view.add_item(EditSelect(edit_select, page))
    if pages > 1:
        # Cada flecha lleva su destino: Discord no admite dos custom_id iguales.
        for label, target, disabled in (
            ("◀", max(page - 1, 0), page == 0),
            ("▶", min(page + 1, pages - 1), page == pages - 1),
        ):
            button: discord.ui.Button = discord.ui.Button(
                label=label,
                custom_id=f"{PAGE_ID}:{target}",
                style=discord.ButtonStyle.secondary,
                disabled=disabled,
                row=2,
            )
            view.add_item(PageButton(button, target))
    return embed, view


class DoneSelect(
    discord.ui.DynamicItem[discord.ui.Select], template=r"lista:hecha(?::(?P<page>\d+))?"
):
    """Menú ✅ para tachar tareas de la lista.

    Es un `DynamicItem` para que siga funcionando tras reiniciar el bot: las
    opciones viajan en el propio mensaje y aquí solo se leen los valores
    elegidos (ids de tarea) y la página, para volver a enseñar la misma. Las
    listas de antes de las páginas (`lista:hecha` a secas) son la página 0.
    """

    def __init__(self, item: discord.ui.Select, page: int = 0) -> None:
        super().__init__(item)
        self.page = page

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Select,
        match: re.Match[str],
        /,
    ) -> DoneSelect:
        return cls(item, int(match["page"] or 0))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Lista)  # type: ignore[arg-type]
        if cog is not None:
            ids = [int(value) for value in self.item.values if value.isdigit()]
            await cog.complete_from_menu(interaction, ids, self.page)


class PageButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lista:pag:(?P<page>\d+)"):
    """Flecha ◀/▶ de la lista; lleva en el custom_id la página a la que va."""

    def __init__(self, item: discord.ui.Button, page: int) -> None:
        super().__init__(item)
        self.page = page

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> PageButton:
        return cls(item, int(match["page"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Lista)  # type: ignore[arg-type]
        if cog is not None:
            await cog.show_page(interaction, self.page)


class EditSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"lista:editar:(?P<page>\d+)"):
    """Menú ✏️ de la lista: abre el formulario para editar la tarea elegida."""

    def __init__(self, item: discord.ui.Select, page: int) -> None:
        super().__init__(item)
        self.page = page

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Select,
        match: re.Match[str],
        /,
    ) -> EditSelect:
        return cls(item, int(match["page"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Lista)  # type: ignore[arg-type]
        if cog is None or not self.item.values:
            return
        value = self.item.values[0]
        # Tras reiniciar, `item` sale del mensaje y trae sus opciones: el texto es la etiqueta.
        label = next((o.label for o in self.item.options if o.value == value), "")
        task_id, priority, author_id = (int(part) for part in value.split(":"))
        await cog.open_edit_form(
            interaction, task_id, label, Priority(priority), author_id, self.page
        )


def edit_form(task_id: int, text: str, priority: Priority, page: int) -> ui.Modal:
    """Formulario ✏️ con el nombre y la prioridad actuales ya puestos.

    No tiene `on_submit`: lo contesta `Lista.on_interaction`, así vale igual si el
    bot se reinicia mientras está abierto.
    """
    form = ui.Modal(
        title="✏️ Editar tarea", custom_id=f"{FORM_ID}:{task_id}:{page}", timeout=PICK_TIMEOUT * 5
    )
    form.add_item(
        ui.Label(
            text="Tarea",
            component=ui.TextInput(custom_id="texto", default=text, max_length=MAX_TASK_LENGTH),
        )
    )
    form.add_item(
        ui.Label(
            text="Prioridad",
            component=ui.Select(
                custom_id="prioridad",
                options=[
                    discord.SelectOption(
                        label=p.label.capitalize(),
                        value=str(p.value),
                        emoji=p.emoji,
                        default=p is priority,
                    )
                    for p in Priority
                ],
            ),
        )
    )
    return form


def form_values(data: dict[str, Any]) -> dict[str, str]:
    """Lo escrito o elegido en un formulario enviado, por `custom_id` de cada campo.

    Los campos llegan dentro de una `Label` (`component`) o, en formularios antiguos,
    de una fila (`components`); los desplegables traen `values` y las cajas, `value`.
    """
    found: dict[str, str] = {}
    pending = list(data.get("components", []))
    while pending:
        node = pending.pop()
        pending += node.get("components", [])
        if "component" in node:
            pending.append(node["component"])
        if "custom_id" in node:
            values = node.get("values")
            found[node["custom_id"]] = values[0] if values else str(node.get("value") or "")
    return found


class PriorityPicker(discord.ui.View):
    """Tres botones para elegir la prioridad de una tarea que llegó sin ella.

    Solo los pulsa quien la apunta. Sin elegir en `PICK_TIMEOUT` segundos, se
    apunta como media. La tarea no se guarda hasta entonces: si el bot se
    reinicia en medio, se pierde y hay que volver a escribirla.

    Args:
        prompt: La interacción de `/lista` cuya respuesta privada es esta
            pregunta, o `None` con `.lista`, que guarda su mensaje en `message`.
    """

    def __init__(
        self,
        cog: Lista,
        author: discord.Member,
        channel: discord.abc.Messageable,
        text: str,
        prompt: discord.Interaction | None = None,
    ) -> None:
        super().__init__(timeout=PICK_TIMEOUT)
        self.cog, self.author, self.channel, self.text = cog, author, channel, text
        self.prompt = prompt
        self.message: discord.Message | None = None
        # Un clic y el fin del plazo pueden llegar juntos: solo apunta el primero.
        self.done = False
        for priority in Priority:
            button: discord.ui.Button = discord.ui.Button(
                label=priority.label.capitalize(),
                emoji=priority.emoji,
                style=discord.ButtonStyle.secondary,
            )
            button.callback = partial(self.choose, priority)  # type: ignore[method-assign]
            self.add_item(button)

    async def choose(self, priority: Priority, interaction: discord.Interaction) -> None:
        """Clic en una prioridad: apunta la tarea y quita la pregunta."""
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Esa tarea no es tuya: apunta las tuyas con `/lista`.", ephemeral=True
            )
            return
        if self.done:
            await ack(interaction)
            return
        self.done = True
        self.stop()
        # Apuntar espera al candado y escribe: se acepta el clic antes.
        await ack(interaction)
        await self._finish(priority, interaction)

    async def on_timeout(self) -> None:
        if self.done:
            return
        self.done = True
        await self._finish(DEFAULT_PRIORITY, self.prompt)

    async def _finish(self, priority: Priority, answer: discord.Interaction | None) -> None:
        """Apunta la tarea y quita la pregunta (o la cambia por el error)."""
        error = await self.cog.save(self.author, self.channel, self.text, priority)
        try:
            if self.message is not None:
                if error:
                    await self.message.edit(content=error, view=None)
                else:
                    await self.message.delete()
            elif answer is not None:
                text = error or f"{priority.emoji} Apuntada con prioridad {priority.label}."
                await edit(answer, content=text, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # Alguien borró la pregunta: la tarea ya está apuntada igual.
            pass


class Lista(commands.Cog):
    """Lista de tareas compartida del servidor, ordenada por prioridad."""

    def __init__(self, bot: commands.Bot, repository: TodoRepository) -> None:
        self.bot = bot
        self.repository = repository
        # Un cambio a la vez por servidor: si dos personas apuntan a la vez,
        # cada una borraría la lista de la otra y quedarían dos a la vista.
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(DoneSelect, PageButton, EditSelect)
        claim_custom_ids(f"{FORM_ID}:")

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(DoneSelect, PageButton, EditSelect)

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, TimedLock("todo:Lista._locks"))

    async def _delete_old_board(self, guild_id: int, keep: int | None = None) -> None:
        """Borra el mensaje de la lista anterior, si sigue ahí."""
        board = await self.repository.get_board(guild_id)
        if board is None or board[1] == keep:
            return
        channel = self.bot.get_channel(board[0])
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.get_partial_message(board[1]).delete()  # type: ignore[attr-defined]
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # Ya no existe o no se puede borrar: la nueva lista sale igual.
            pass

    async def _publish(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        note: str | None = None,
    ) -> None:
        """Publica la lista al final de `channel` y quita la anterior."""
        embed, view = build_board(guild, await self.repository.list_tasks(guild.id), note)
        kwargs: dict[str, object] = {"embed": embed, "allowed_mentions": NO_MENTIONS}
        if view is not None:
            kwargs["view"] = view
        message = await channel.send(**kwargs)  # type: ignore[arg-type]
        await self._delete_old_board(guild.id, keep=message.id)
        await self.repository.set_board(guild.id, message.channel.id, message.id)

    async def save(
        self,
        author: discord.Member,
        channel: discord.abc.Messageable,
        text: str,
        priority: Priority,
    ) -> str | None:
        """Apunta una tarea ya validada y publica la lista en `channel`.

        Returns:
            El motivo si no se pudo apuntar (lista llena), o `None`.
        """
        guild = author.guild
        async with self._lock(guild.id):
            try:
                task = await self.repository.add_task(guild.id, author.id, text, priority)
            except TaskError as error:
                return str(error)
            await self._publish(guild, channel, self._added_note(author, task))
        await logros.track(self.bot, guild.id, author, channel, StatDelta(add={"todo_added": 1}))
        return None

    @staticmethod
    def _added_note(author: discord.Member, task: Task) -> str:
        name = discord.utils.escape_markdown(author.display_name)
        text = discord.utils.escape_markdown(task.text)
        return f"➕ **{name}** apuntó {task.priority.emoji} «{text}»."

    @staticmethod
    def _ask_priority(author: discord.Member, text: str) -> str:
        name = discord.utils.escape_markdown(author.display_name)
        return f"**{name}**, ¿qué prioridad tiene «{discord.utils.escape_markdown(text)}»?"

    # -- Tachar y pasar página -------------------------------------------------------

    async def complete_from_menu(
        self, interaction: discord.Interaction, task_ids: list[int], page: int = 0
    ) -> None:
        """Tacha las tareas elegidas en el menú y actualiza esa misma lista."""
        guild, user = interaction.guild, interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return
        # Tachar espera al candado de la lista y escribe: se acepta el clic antes.
        await ack(interaction)
        async with self._lock(guild.id):
            tasks = await self.repository.get_tasks(guild.id, task_ids)
            is_admin = user.guild_permissions.administrator
            allowed = [t for t in tasks if can_complete(t, user.id, is_admin=is_admin)]
            if not allowed:
                text = (
                    "Solo puede tachar una tarea quien la apuntó o un administrador."
                    if tasks
                    else "Esas tareas ya estaban tachadas."
                )
                await notify(interaction, text)
                return
            removed = set(await self.repository.remove_tasks(guild.id, [t.id for t in allowed]))
            done = [t for t in allowed if t.id in removed]
            name = discord.utils.escape_markdown(user.display_name)
            # Como mucho tres títulos, para que la nota no se coma el embed.
            titles = ", ".join(f"«{discord.utils.escape_markdown(t.text)}»" for t in done[:3])
            if len(done) > 3:
                titles += f" y {len(done) - 3} más"
            note = f"✅ **{name}** tachó {titles}." if done else None
            skipped = len(tasks) - len(allowed)
            if skipped:
                note = (note or "") + f"\n-# {skipped} no eran suyas y siguen en la lista."
            remaining = await self.repository.list_tasks(guild.id)
            embed, view = build_board(guild, remaining, note, page)
            # Editar el mismo mensaje lo mantiene en su sitio y sin avisos extra.
            await edit(interaction, embed=embed, view=view)
        if done:
            await logros.track(
                self.bot,
                guild.id,
                user,
                interaction.channel,
                StatDelta(add={"todo_done": len(done)}, peak={"todo_done_batch_max": len(done)}),
            )

    async def show_page(self, interaction: discord.Interaction, page: int) -> None:
        """Flechas ◀/▶: enseña otra página en el mismo mensaje."""
        guild = interaction.guild
        if guild is None:
            return
        # Lee la base de datos: se acepta el clic antes.
        await ack(interaction)
        embed, view = build_board(guild, await self.repository.list_tasks(guild.id), page=page)
        await edit(interaction, embed=embed, view=view)

    # -- Editar ----------------------------------------------------------------------

    async def open_edit_form(
        self,
        interaction: discord.Interaction,
        task_id: int,
        text: str,
        priority: Priority,
        author_id: int,
        page: int,
    ) -> None:
        """Menú ✏️: abre el formulario de la tarea elegida (si es tuya o eres admin)."""
        guild, user = interaction.guild, interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return
        # `send_modal` tiene que ser la primera respuesta: aquí solo se mira memoria.
        if not (user.guild_permissions.administrator or user.id == author_id):
            await interaction.response.send_message(
                "Solo puede editar una tarea quien la apuntó o un administrador.", ephemeral=True
            )
        else:
            await interaction.response.send_modal(edit_form(task_id, text, priority, page))
        # Discord deja el menú con la tarea elegida y no avisa si se vuelve a elegir la
        # misma (al cerrar el formulario sin guardar): se repone la lista para soltarla.
        if interaction.message is not None:
            _, view = build_board(guild, await self.repository.list_tasks(guild.id), page=page)
            try:
                await interaction.message.edit(view=view)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Formulario ✏️ enviado, también si se abrió antes de reiniciar el bot."""
        if interaction.type is not discord.InteractionType.modal_submit:
            return
        data: dict[str, Any] = dict(interaction.data or {})
        match = FORM_RE.fullmatch(str(data.get("custom_id", "")))
        if match is None:
            return
        values = form_values(data)
        chosen = values.get("prioridad", "")
        await self.save_edit(
            interaction,
            int(match["id"]),
            values.get("texto", ""),
            Priority(int(chosen)) if chosen.isdigit() else None,
            int(match["page"]),
        )

    async def save_edit(
        self,
        interaction: discord.Interaction,
        task_id: int,
        raw: str,
        priority: Priority | None,
        page: int = 0,
    ) -> None:
        """Guarda lo del formulario ✏️ y actualiza la lista en su sitio.

        `priority` es la del desplegable; si falta, vale la escrita en el texto
        («… prioridad alta») o, si tampoco, la que tenía.
        """
        guild, user = interaction.guild, interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return
        # Guardar espera al candado y escribe: se acepta el formulario antes.
        await ack(interaction)
        try:
            text, priority = parse_task(raw, priority)
        except TaskError as error:
            await notify(interaction, str(error))
            return
        async with self._lock(guild.id):
            found = await self.repository.get_tasks(guild.id, [task_id])
            if not found:
                await notify(interaction, "Esa tarea ya no está: la tacharon mientras la editabas.")
                return
            before = found[0]
            if not can_complete(before, user.id, is_admin=user.guild_permissions.administrator):
                await notify(
                    interaction, "Solo puede editar una tarea quien la apuntó o un administrador."
                )
                return
            priority = before.priority if priority is None else priority
            if (text, priority) == (before.text, before.priority):
                return
            after = await self.repository.update_task(guild.id, task_id, text, priority)
            if after is None:
                await notify(interaction, "Esa tarea ya no está: la tacharon mientras la editabas.")
                return
            note = self._edited_note(user, before, after)
            embed, view = build_board(guild, await self.repository.list_tasks(guild.id), note, page)
            # El formulario sale del menú de la lista: se edita ese mismo mensaje.
            await edit(interaction, embed=embed, view=view)
        demoted = before.priority is Priority.ALTA and after.priority is Priority.BAJA
        await logros.track(
            self.bot,
            guild.id,
            user,
            interaction.channel,
            StatDelta(add={"todo_edited": 1, **({"todo_demoted": 1} if demoted else {})}),
        )

    @staticmethod
    def _edited_note(user: discord.Member, before: Task, after: Task) -> str:
        name = discord.utils.escape_markdown(user.display_name)
        old = discord.utils.escape_markdown(before.text)
        new = discord.utils.escape_markdown(after.text)
        if old == new:
            return (
                f"✏️ **{name}** pasó «{new}» de {before.priority.emoji} {before.priority.label}"
                f" a {after.priority.emoji} {after.priority.label}."
            )
        return (
            f"✏️ **{name}** cambió {before.priority.emoji} «{old}»"
            f" por {after.priority.emoji} «{new}»."
        )

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(name="lista", description="Lista de cosas que hacer: apunta o enséñala.")
    @app_commands.describe(
        tarea=f"Qué hay que hacer (máx. {MAX_TASK_LENGTH} caracteres). Vacío: enseña la lista.",
        prioridad="Alta, media o baja. Si no la eliges, te la pregunta.",
    )
    @app_commands.choices(
        prioridad=[
            app_commands.Choice(name=f"{p.emoji} {p.label.capitalize()}", value=p.value)
            for p in Priority
        ]
    )
    @app_commands.guild_only()
    async def lista(
        self,
        interaction: discord.Interaction,
        tarea: str | None = None,
        prioridad: app_commands.Choice[int] | None = None,
    ) -> None:
        """`/lista`: apunta `tarea` (si llega) y publica la lista en este canal."""
        guild, user, channel = interaction.guild, interaction.user, interaction.channel
        if guild is None or not isinstance(user, discord.Member) or channel is None:
            return
        if tarea is None:
            # Leer la lista y esperar al candado puede pasar de los 3 segundos de
            # Discord: se acepta antes. La respuesta del slash es la propia lista,
            # así no queda el «X usó /lista» suelto encima de otro mensaje del bot.
            await ack(interaction)
            async with self._lock(guild.id):
                embed, view = build_board(guild, await self.repository.list_tasks(guild.id))
                await edit(interaction, embed=embed, view=view, allowed_mentions=NO_MENTIONS)
                message = await interaction.original_response()
                await self._delete_old_board(guild.id, keep=message.id)
                await self.repository.set_board(guild.id, message.channel.id, message.id)
            return
        chosen = Priority(prioridad.value) if prioridad is not None else None
        try:
            text, priority = parse_task(tarea, chosen)
        except TaskError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        if priority is None:
            picker = PriorityPicker(self, user, channel, text, prompt=interaction)
            await interaction.response.send_message(
                self._ask_priority(user, text), view=picker, ephemeral=True
            )
            return
        await ack(interaction, new_message=True)
        error = await self.save(user, channel, text, priority)
        await edit(interaction, content=error or f"{priority.emoji} Apuntada.")

    @commands.command(name="lista")
    @commands.guild_only()
    async def lista_text(self, ctx: commands.Context, *, tarea: str | None = None) -> None:
        """Versión de texto: `.lista` o `.lista arreglar el purge prioridad alta`."""
        guild, author = ctx.guild, ctx.author
        if guild is None or not isinstance(author, discord.Member):
            return
        text, priority = None, None
        if tarea is not None:
            try:
                text, priority = parse_task(tarea)
            except TaskError as error:
                await ctx.send(f"{error}\nUso: {USAGE}", allowed_mentions=NO_MENTIONS)
                return
        if text is not None and priority is not None:
            error = await self.save(author, ctx.channel, text, priority)
            if error:
                await ctx.send(error, allowed_mentions=NO_MENTIONS)
                return
        try:
            # El texto ya queda en la lista (o en la pregunta): la orden sobra.
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        if text is None:
            async with self._lock(guild.id):
                await self._publish(guild, ctx.channel)
        elif priority is None:
            picker = PriorityPicker(self, author, ctx.channel, text)
            picker.message = await ctx.send(
                self._ask_priority(author, text), view=picker, allowed_mentions=NO_MENTIONS
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra la lista del servidor cuando el bot sale de él."""
        await self.repository.delete_guild_data(guild.id)
        self._locks.pop(guild.id, None)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el repositorio compartido del bot."""
    await bot.add_cog(Lista(bot, bot.todo))
