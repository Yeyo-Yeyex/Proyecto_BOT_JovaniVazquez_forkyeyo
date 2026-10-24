"""El panel de `hacienda`: la cuenta del Estado en pestañas y la factura de un miembro.

El comando vive en el cog del casino (`Casino._hacienda_view`), que lee las cifras una
sola vez (`tax_report.Snapshot`); cambiar de pestaña solo cambia memoria, así que se
contesta directamente con `edit_message`. Quien no abrió el panel y pulsa una pestaña
recibe su propia copia en privado, con las mismas cifras, y el panel de quien lo abrió
no se mueve.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

import discord
from discord import ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.services.achievements import hacienda_page_stats
from bot.services.tax_report import COLOR, PAGE_TEXT, PAGES, Snapshot, leader

logger = logging.getLogger(__name__)

VIEW_TIMEOUT = 600
_NO_MENTIONS = discord.AllowedMentions.none()


class HaciendaPanel(ui.LayoutView):
    """La cuenta del Estado, con una pestaña a la vista.

    Args:
        bot: Para los logros de cada pestaña.
        snapshot: Las cifras, leídas al abrir el panel.
        owner: Quien lo abrió (el único que lo mueve).
        page: Pestaña de inicio (clave de `tax_report.PAGES`).
        read_at: Cuándo se leyeron las cifras (por defecto, ahora).
    """

    def __init__(
        self,
        bot: commands.Bot,
        snapshot: Snapshot,
        owner: discord.abc.User,
        *,
        page: str = "resumen",
        read_at: float | None = None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.bot = bot
        self.snapshot = snapshot
        self.owner = owner
        self.page = page
        self.read_at = time.time() if read_at is None else read_at
        self.seen = {page}
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None
        self.rebuild()

    def rebuild(self) -> None:
        """Monta la pestaña actual."""
        self.clear_items()
        container = ui.Container(accent_colour=COLOR)
        container.add_item(
            ui.TextDisplay(
                f"# 🏛️ Hacienda\n-# La cuenta del Estado · cifras de <t:{int(self.read_at)}:R>"
            )
        )
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(PAGE_TEXT[self.page](self.snapshot)))
        container.add_item(ui.Separator())
        row: ui.ActionRow = ui.ActionRow()
        for key, emoji, label in PAGES:
            button: ui.Button = ui.Button(
                label=label,
                emoji=emoji,
                style=(
                    discord.ButtonStyle.primary
                    if key == self.page
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"hacienda:pagina:{key}",
            )
            button.callback = self._tab(key)  # type: ignore[method-assign]
            row.add_item(button)
        container.add_item(row)
        self.add_item(container)

    def _tab(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id == self.owner.id:
                panel = self
                panel.page = key
                panel.rebuild()
                await interaction.response.edit_message(view=panel, allowed_mentions=_NO_MENTIONS)
            else:
                panel = HaciendaPanel(
                    self.bot, self.snapshot, interaction.user, page=key, read_at=self.read_at
                )
                await interaction.response.send_message(
                    view=panel, ephemeral=True, allowed_mentions=_NO_MENTIONS
                )
                panel.interaction = interaction
            first_time_all = key not in panel.seen and len(panel.seen) == len(PAGES) - 1
            panel.seen.add(key)
            await logros.track(
                self.bot,
                self.snapshot.guild_id,
                interaction.user,
                interaction.channel,
                hacienda_page_stats(
                    page=key,
                    top=leader(self.snapshot, key) == interaction.user.id,
                    seen_all=first_time_all,
                ),
            )

        return callback

    async def on_timeout(self) -> None:
        """Apaga las pestañas al caducar."""
        for child in self.walk_children():
            if isinstance(child, ui.Button):
                child.disabled = True
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar un panel de hacienda", exc_info=True)


def bill_card(text: str) -> ui.LayoutView:
    """La factura de un miembro: un solo bloque, sin botones."""
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_colour=COLOR)
    container.add_item(ui.TextDisplay(text))
    view.add_item(container)
    return view
