"""Lista de cosas que hacer del servidor: el comando `lista`.

- `lista`: vuelve a publicar la lista en el canal actual.
- `lista <tarea> [prioridad alta|media|baja]`: la apunta (media si no se
  dice) y vuelve a publicar la lista. Con `/lista`, la prioridad también
  se puede elegir en un desplegable.

La lista sale siempre ordenada (alta, media, baja y, dentro de cada una, de
la más antigua a la más nueva) y solo hay una a la vista: al publicar otra,
el bot borra la anterior. Con `.lista <tarea>` también borra el mensaje de
quien la apunta, para que el canal no se llene de órdenes sueltas; para eso
necesita **Gestionar mensajes** (si no lo tiene, lo deja y sigue).

Las tareas se tachan con el menú de la propia lista. Puede tacharlas quien
las apuntó o un administrador; al tacharlas se borran.

Logros: apuntar (`todo_added`) y tachar (`todo_done`), categoría 📝 Lista.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.repositories.todo import TodoRepository
from bot.services.achievements import StatDelta
from bot.services.todo import (
    MAX_TASK_LENGTH,
    MAX_TASKS,
    Priority,
    Task,
    TaskError,
    can_complete,
    parse_task,
    sort_tasks,
)
from bot.utils.cogs import find_cog

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(250, 190, 60)
SELECT_ID = "lista:hecha"
NO_MENTIONS = discord.AllowedMentions.none()
USAGE = "`lista <tarea> prioridad alta|media|baja`"


def _author_name(guild: discord.Guild, user_id: int) -> str:
    """Nombre visible de quien apuntó la tarea, o una mención si ya no está."""
    member = guild.get_member(user_id)
    return discord.utils.escape_markdown(member.display_name) if member else f"<@{user_id}>"


def build_board(
    guild: discord.Guild, tasks: list[Task], note: str | None = None
) -> tuple[discord.Embed, discord.ui.View | None]:
    """Embed con la lista ordenada y el menú para tachar.

    Args:
        note: Línea opcional sobre el último cambio («Diego apuntó…»).

    Returns:
        `(embed, vista)`; la vista es `None` si no queda nada pendiente.
    """
    ordered = sort_tasks(tasks)
    if ordered:
        lines = [
            f"`{index:>2}.` {task.priority.emoji} **{discord.utils.escape_markdown(task.text)}**"
            f" · {_author_name(guild, task.author_id)}"
            for index, task in enumerate(ordered, start=1)
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
    embed.set_footer(text="🔥 alta · 📌 media · 💤 baja — apunta con .lista <tarea> prioridad alta")
    if not ordered:
        return embed, None
    select: discord.ui.Select = discord.ui.Select(
        custom_id=SELECT_ID,
        placeholder="✅ Tachar tareas hechas…",
        min_values=1,
        max_values=len(ordered),
        options=[
            discord.SelectOption(
                label=task.text[:100],
                value=str(task.id),
                emoji=task.priority.emoji,
                description=f"Prioridad {task.priority.label}",
            )
            for task in ordered
        ],
    )
    view = discord.ui.View(timeout=None)
    view.add_item(DoneSelect(select))
    return embed, view


class DoneSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"lista:hecha"):
    """Menú ✅ para tachar tareas de la lista.

    Es un `DynamicItem` para que siga funcionando tras reiniciar el bot: las
    opciones viajan en el propio mensaje y aquí solo se leen los valores
    elegidos (ids de tarea).
    """

    def __init__(self, item: discord.ui.Select) -> None:
        super().__init__(item)

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Select,
        match: re.Match[str],
        /,
    ) -> DoneSelect:
        return cls(item)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Lista)  # type: ignore[arg-type]
        if cog is not None:
            ids = [int(value) for value in self.item.values if value.isdigit()]
            await cog.complete_from_menu(interaction, ids)


class Lista(commands.Cog):
    """Lista de tareas compartida del servidor, ordenada por prioridad."""

    def __init__(self, bot: commands.Bot, repository: TodoRepository) -> None:
        self.bot = bot
        self.repository = repository
        # Un cambio a la vez por servidor: si dos personas apuntan a la vez,
        # cada una borraría la lista de la otra y quedarían dos a la vista.
        self._locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(DoneSelect)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(DoneSelect)

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

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

    async def _add(
        self, guild: discord.Guild, author: discord.Member, raw: str, priority: Priority | None
    ) -> Task:
        """Valida y apunta una tarea. Lanza `TaskError` con el motivo si no se puede."""
        text, chosen = parse_task(raw, priority)
        return await self.repository.add_task(guild.id, author.id, text, chosen)

    @staticmethod
    def _added_note(author: discord.Member, task: Task) -> str:
        name = discord.utils.escape_markdown(author.display_name)
        text = discord.utils.escape_markdown(task.text)
        return f"➕ **{name}** apuntó {task.priority.emoji} «{text}»."

    # -- Tachar ----------------------------------------------------------------------

    async def complete_from_menu(
        self, interaction: discord.Interaction, task_ids: list[int]
    ) -> None:
        """Tacha las tareas elegidas en el menú y actualiza esa misma lista."""
        guild, user = interaction.guild, interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return
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
                await interaction.response.send_message(text, ephemeral=True)
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
            embed, view = build_board(guild, await self.repository.list_tasks(guild.id), note)
            # Editar el mismo mensaje lo mantiene en su sitio y sin avisos extra.
            await interaction.response.edit_message(embed=embed, view=view)
        if done:
            await logros.track(
                self.bot,
                guild.id,
                user,
                interaction.channel,
                StatDelta(add={"todo_done": len(done)}),
            )

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(name="lista", description="Lista de cosas que hacer: apunta o enséñala.")
    @app_commands.describe(
        tarea=f"Qué hay que hacer (máx. {MAX_TASK_LENGTH} caracteres). Vacío: enseña la lista.",
        prioridad="Alta, media o baja. Por defecto, media.",
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
        note = None
        async with self._lock(guild.id):
            if tarea is not None:
                chosen = Priority(prioridad.value) if prioridad is not None else None
                try:
                    task = await self._add(guild, user, tarea, chosen)
                except TaskError as error:
                    await interaction.response.send_message(str(error), ephemeral=True)
                    return
                note = self._added_note(user, task)
            embed, view = build_board(guild, await self.repository.list_tasks(guild.id), note)
            kwargs: dict[str, object] = {"embed": embed, "allowed_mentions": NO_MENTIONS}
            if view is not None:
                kwargs["view"] = view
            # La respuesta del slash es la propia lista: así no queda el
            # «X usó /lista» suelto encima de otro mensaje del bot.
            await interaction.response.send_message(**kwargs)  # type: ignore[arg-type]
            message = await interaction.original_response()
            await self._delete_old_board(guild.id, keep=message.id)
            await self.repository.set_board(guild.id, message.channel.id, message.id)
        if tarea is not None:
            await logros.track(self.bot, guild.id, user, channel, StatDelta(add={"todo_added": 1}))

    @commands.command(name="lista")
    @commands.guild_only()
    async def lista_text(self, ctx: commands.Context, *, tarea: str | None = None) -> None:
        """Versión de texto: `.lista` o `.lista arreglar el purge prioridad alta`."""
        guild, author = ctx.guild, ctx.author
        if guild is None or not isinstance(author, discord.Member):
            return
        note = None
        async with self._lock(guild.id):
            if tarea is not None:
                try:
                    task = await self._add(guild, author, tarea, None)
                except TaskError as error:
                    await ctx.send(f"{error}\nUso: {USAGE}", allowed_mentions=NO_MENTIONS)
                    return
                note = self._added_note(author, task)
            try:
                # El texto ya queda en la lista: el mensaje de la orden sobra.
                await ctx.message.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            await self._publish(guild, ctx.channel, note)
        if tarea is not None:
            await logros.track(
                self.bot, guild.id, author, ctx.channel, StatDelta(add={"todo_added": 1})
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra la lista del servidor cuando el bot sale de él."""
        await self.repository.delete_guild_data(guild.id)
        self._locks.pop(guild.id, None)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el repositorio compartido del bot."""
    await bot.add_cog(Lista(bot, bot.todo))
