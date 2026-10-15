"""Gestión centralizada de errores de comandos de texto (`.`) y de aplicación (`/`).

Sin este cog, los errores de comandos de texto (un argumento mal escrito, un
miembro que no existe, un fallo inesperado) solo aparecen en el log y el
usuario no recibe ninguna respuesta, y los de `/` terminan en el genérico
"la aplicación no ha respondido". Aquí se convierten en un mensaje breve y
seguro: nunca se muestran trazas ni datos internos (ver Biblia.txt).

También rescata los clics que discord.py tira sin contestar, que Discord enseña
como «Esta interacción ha fallado» (`install_click_rescue`):

- Un clic sobre un botón que su vista acaba de rehacer (casi todos los juegos
  vacían y vuelven a poner sus botones en cada jugada) llega antes de que Discord
  tenga el mensaje nuevo. Va al botón que lo sustituye, con el mismo `custom_id`,
  como si hubiera llegado un instante después; si ese ya está apagado, o no hay,
  se acepta sin cambiar nada.
- Un clic o un formulario de una vista caducada, terminada o de antes de un
  reinicio se contesta con un aviso privado.
- Un botón o formulario que lanza una excepción avisa en privado, además de
  apuntarla en el log.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui.view import BaseView, ViewStore

from bot.services.telemetry import TelemetryService
from bot.utils.interactions import is_claimed, notify

logger = logging.getLogger(__name__)

GENERIC_ERROR = "Algo salió mal ejecutando el comando. Inténtalo de nuevo."
GUILD_ONLY_ERROR = "Este comando solo está disponible dentro de un servidor."
ADMIN_ONLY_ERROR = "Solo los administradores pueden usar este comando."
#: Aviso para un clic en botones que ya no atiende nadie.
EXPIRED_CLICK = (
    "Estos botones ya caducaron, como las promesas de campaña. Ábrelo otra vez con su comando.",
    "Aquí ya no queda nadie, ni el Gobierno en agosto. Ábrelo otra vez con su comando.",
    "Estos botones llevan más tiempo parados que una obra del AVE. Ábrelo otra vez con su comando.",
    "Ese mensaje ya no responde: o se acabó el tiempo o me he reiniciado. ¡Quiero café! "
    "Ábrelo otra vez con su comando.",
    "Estos botones ya no van, como el juicio de Bibi: aplazados para siempre. Ábrelo otra vez "
    "con su comando.",
    "Aquí no queda nadie, ni el groyper del sótano. Ábrelo otra vez con su comando.",
)
#: Aviso para un botón o formulario que revienta (el error queda en el log).
BUTTON_ERROR = (
    "Algo ha petado con ese botón, como la red eléctrica en abril. Ya está apuntado; "
    "prueba otra vez.",
    "Ese botón se ha liado más que un pacto de Junts. Ya está apuntado; prueba otra vez.",
    "Error del sistema, y no es el de Hacienda. Ya está apuntado; prueba otra vez.",
    "Ese botón lo ha tumbado algo que ni la Cúpula de Hierro para. Ya está apuntado; prueba "
    "otra vez.",
)
# Marca de los métodos de discord.py ya envueltos (cada `load_extension` vuelve a
# ejecutar este módulo, pero las clases son las mismas).
_WRAPPED = "_click_rescue_wrapped"
# Tareas de los avisos en marcha, para que no se las lleve el recolector.
_pending: set[asyncio.Task[None]] = set()


def describe_command_error(error: commands.CommandError) -> str | None:
    """Mensaje para el usuario de un error de comando de texto.

    Returns:
        El texto a mostrar, o `None` si no hay que responder nada (por
        ejemplo, un mensaje que empieza por el prefijo pero no es un comando).
    """
    if isinstance(error, commands.CommandNotFound):
        return None
    if isinstance(error, commands.NoPrivateMessage):
        return GUILD_ONLY_ERROR
    if isinstance(error, commands.MissingRequiredArgument):
        return f"Falta el argumento `{error.param.name}`. Escribe `ayuda` para ver cómo se usa."
    if isinstance(error, commands.UserInputError):
        return "No entendí algún argumento. Escribe `ayuda` para ver cómo se usa."
    if isinstance(error, commands.MissingPermissions):
        return ADMIN_ONLY_ERROR
    if isinstance(error, commands.CheckFailure):
        return "No puedes usar este comando aquí."
    return GENERIC_ERROR


def describe_app_command_error(error: app_commands.AppCommandError) -> str:
    """Mensaje para el usuario de un error de comando de aplicación."""
    if isinstance(error, app_commands.NoPrivateMessage):
        return GUILD_ONLY_ERROR
    if isinstance(error, app_commands.MissingPermissions):
        return ADMIN_ONLY_ERROR
    if isinstance(error, app_commands.CheckFailure):
        return "No puedes usar este comando aquí."
    return GENERIC_ERROR


def _count(interaction: discord.Interaction, name: str) -> None:
    """Cuenta en la telemetría un clic contestado aquí (no pasa por ninguna vista)."""
    telemetry = getattr(interaction.client, "telemetry", None)
    if isinstance(telemetry, TelemetryService):
        telemetry.count(
            "button",
            name,
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            ms=telemetry.take_answer(interaction.id),
            error=not interaction.response.is_done(),
        )


async def _answer_expired(interaction: discord.Interaction) -> None:
    """Contesta en privado a un clic o formulario que ya no atiende nadie."""
    try:
        await interaction.response.send_message(random.choice(EXPIRED_CLICK), ephemeral=True)
    except discord.HTTPException:
        logger.debug("No se pudo avisar de un botón caducado", exc_info=True)
    _count(interaction, "(caducado)")


async def _accept_quietly(interaction: discord.Interaction) -> None:
    """Acepta sin cambiar nada un clic sobre un botón que ya no está o está apagado."""
    try:
        await interaction.response.defer()
    except discord.HTTPException:
        logger.debug("No se pudo aceptar un clic repetido", exc_info=True)
    _count(interaction, "(clic repetido)")


def rescue_click(
    store: ViewStore, component_type: int, custom_id: str, interaction: discord.Interaction
) -> Coroutine[Any, Any, None] | None:
    """Rescata un clic que `ViewStore.dispatch_view` no va a pasar a ninguna vista.

    Repite la búsqueda de discord.py (por mensaje y, si no, persistente). Devuelve
    lo que hay que esperar para contestar, o `None` si lo atiende otro: una vista
    viva, un botón dinámico, un listener propio (`claim_custom_ids`) o el botón que
    sustituye al pulsado, al que se le pasa aquí mismo.
    """
    if is_claimed(custom_id) or any(p.fullmatch(custom_id) for p in store._dynamic_items):
        return None
    message_id = interaction.message.id if interaction.message is not None else None
    key = (component_type, custom_id)
    item = store._views.get(message_id, {}).get(key) or store._views.get(None, {}).get(key)
    if item is None:
        return _answer_expired(interaction)
    if item.view is not None:
        return _answer_expired(interaction) if item.view.is_finished() else None
    # La vista vació sus botones y puso otros, y Discord aún no tiene el mensaje nuevo.
    view = store._synced_message_views.get(message_id) if message_id is not None else None
    if view is None or view.is_finished():
        return _accept_quietly(interaction)
    current = next(
        (
            child
            for child in view.walk_children()
            if child.is_dispatchable()
            and child.type.value == component_type
            and getattr(child, "custom_id", None) == custom_id
        ),
        None,
    )
    if current is None or getattr(current, "disabled", False):
        return _accept_quietly(interaction)
    task = view._dispatch_item(current, interaction)
    if task is not None:
        store.add_task(task)
    return None


def _schedule(answer: Coroutine[Any, Any, None] | None) -> None:
    if answer is not None:
        task = asyncio.create_task(answer)
        _pending.add(task)
        task.add_done_callback(_pending.discard)


def _rescued_view(original: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(original)
    def dispatch(self: ViewStore, component_type: int, custom_id: str, interaction: Any) -> None:
        original(self, component_type, custom_id, interaction)
        _schedule(rescue_click(self, component_type, custom_id, interaction))

    return dispatch


def _rescued_modal(original: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(original)
    def dispatch(self: ViewStore, custom_id: str, interaction: Any, *args: Any) -> None:
        original(self, custom_id, interaction, *args)
        if custom_id not in self._modals:
            _schedule(_answer_expired(interaction))

    return dispatch


def _noticed_error(original: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    @functools.wraps(original)
    async def on_error(self: Any, interaction: discord.Interaction, *args: Any) -> None:
        await original(self, interaction, *args)
        try:
            await notify(interaction, random.choice(BUTTON_ERROR))
        except discord.HTTPException:
            logger.debug("No se pudo avisar del fallo de un botón", exc_info=True)

    return on_error


def install_click_rescue() -> None:
    """Envuelve, una sola vez por proceso, por donde discord.py pierde clics.

    `ViewStore.dispatch_view` y `dispatch_modal` (clics sin vista viva) son
    internos: si cambian, falla `tests/integration/test_click_rescue.py`. El
    `on_error` de vistas y formularios, que solo apunta el error en el log, además
    avisa a quien pulsó (las vistas con su propio `on_error` siguen con el suyo).
    """
    for owner, name, wrapper in (
        (ViewStore, "dispatch_view", _rescued_view),
        (ViewStore, "dispatch_modal", _rescued_modal),
        (BaseView, "on_error", _noticed_error),
        (discord.ui.Modal, "on_error", _noticed_error),
    ):
        original = owner.__dict__[name]
        if not getattr(original, _WRAPPED, False):
            wrapped = wrapper(original)
            setattr(wrapped, _WRAPPED, True)
            setattr(owner, name, wrapped)


class Errors(commands.Cog):
    """Responde con mensajes claros cuando falla un comando."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._previous_tree_handler = bot.tree.on_error

    async def cog_load(self) -> None:
        """Instala el gestor de errores de los comandos de aplicación y el rescate de clics."""
        self.bot.tree.error(self.on_app_command_error)
        install_click_rescue()

    async def cog_unload(self) -> None:
        """Restaura el gestor de errores original del árbol de comandos."""
        self.bot.tree.on_error = self._previous_tree_handler

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Informa del error de un comando de texto y registra los inesperados."""
        message = describe_command_error(error)
        if isinstance(error, commands.CommandInvokeError):
            logger.error(
                "Error en el comando de texto %r",
                ctx.command and ctx.command.name,
                exc_info=error.original,
            )
        if message is None:
            return
        try:
            await ctx.send(message)
        except discord.HTTPException:
            logger.warning("No se pudo enviar el aviso de error", exc_info=True)

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Informa del error de un comando de aplicación y registra los inesperados."""
        telemetry = getattr(self.bot, "telemetry", None)
        if isinstance(telemetry, TelemetryService) and interaction.command is not None:
            # El cog `Telemetry` cuenta los que terminan bien; los fallidos solo pasan por aquí.
            telemetry.count(
                "slash",
                interaction.command.qualified_name,
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                ms=telemetry.take_answer(interaction.id),
                error=True,
            )
        if isinstance(error, app_commands.CommandInvokeError):
            logger.error(
                "Error en el comando de aplicación %r",
                interaction.command and interaction.command.name,
                exc_info=error.original,
            )
        message = describe_app_command_error(error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning("No se pudo enviar el aviso de error", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    """Registra el gestor de errores en el cliente."""
    await bot.add_cog(Errors(bot))
