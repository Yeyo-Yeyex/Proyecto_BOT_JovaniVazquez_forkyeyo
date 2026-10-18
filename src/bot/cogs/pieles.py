"""Pieles: `pieles`, el panel para manejar las skins de cada uno.

Las skins (`bot.services.skins`) se compran en la `tienda` y se llevan por mesa.
Este comando las junta todas en un panel, las de hoy y las que vengan, porque
lee los registros `GAMES` y `SKINS` y no una lista propia:

- qué skins tienes y cuál lleva cada mesa, con un desplegable para cambiar la
  de una mesa (abre el mismo selector que el botón 🎨 de la mesa);
- las **skins propias** (`Skin.custom`, hoy el 🖼️ Tapete propio): la imagen
  que llevan, con 🖼️ **Subir imagen** (un formulario con archivo) y 🗑️
  **Quitar imagen**. Si no la tienes, el botón 🛒 abre la caja para comprarla.

Uso: `/pieles [miembro] [imagen]` o `.pieles [@miembro]` con la imagen adjunta.
Con `imagen` (o un adjunto) se sube directamente, sin panel. Con `miembro`, un
**administrador** maneja la imagen de otro (cambiarla o quitarla, por ejemplo si
es indecente); los demás solo ven y tocan lo suyo. El permiso se comprueba en el
servidor en cada acción, no solo al abrir el panel.

La imagen se ve en cuanto se sube, sin revisión previa: la moderación es
quitarla después. Se limpia y se guarda con `bot.services.skin_images` y la
tienda (`Tienda.set_skin_image`), y la escena la pinta desde la siguiente
jugada. Subirla no mueve dinero (lo que se paga es la skin, en la tienda).

Logros (categoría Tienda): abrir el panel, subir imágenes, quitarle o cambiarle
la imagen a otro como admin y quedarse sin ella por un admin.

Permisos del bot: enviar mensajes y adjuntar archivos (la imagen sale en el panel).
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import pets as mascotas
from bot.cogs.shop import Tienda, skin_game_options, skin_lines
from bot.repositories.shop import InventoryEntry
from bot.services.achievements import (
    shop_skin_censored_stats,
    shop_skin_image_stats,
    shop_skin_panel_stats,
)
from bot.services.economy import format_amount
from bot.services.pets import Event, Moment
from bot.services.shop import quote
from bot.services.skin_images import MAX_UPLOAD_BYTES, SkinImageError, prepare
from bot.services.skins import Skin, custom_skins, wearable_games
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(255, 176, 59)
VIEW_TIMEOUT = 600
NO_MENTIONS = discord.AllowedMentions.none()

#: Lo que dice el bot al subir una imagen, por variar.
UPLOADED_LINES = (
    "🖼️ Imagen puesta en {skin}. Desde la próxima jugada, la verás en la mesa.",
    "🖼️ {skin} estrena imagen.",
    "🖼️ Hecho: {skin} ya lleva tu imagen.",
    "🖼️ Imagen colgada en {skin}. Más personalizado que el Falcon.",
    "🖼️ {skin} con imagen nueva. Yo soy yo… y así me tienes que soportar.",
)


def _is_admin(user: discord.abc.User) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


def may_manage(actor: discord.abc.User, target: discord.abc.User) -> bool:
    """Si `actor` puede tocar las pieles de `target`: las suyas, o cualquiera si es admin."""
    return actor.id == target.id or _is_admin(actor)


def image_name(skin: Skin) -> str:
    """Nombre del adjunto con la imagen de una skin propia en el panel."""
    return f"{skin.key}.jpg"


def owned_custom(entries: list[InventoryEntry]) -> list[Skin]:
    """Las skins propias que hay en una mochila, en el orden del registro."""
    keys = {e.catalog_key for e in entries}
    return [s for s in custom_skins() if s.catalog_key in keys]


def where_text(skin: Skin) -> str:
    """En qué mesas se ve una skin, para el panel."""
    tables = wearable_games(skin)
    return " ".join(g.emoji for g in tables) if len(tables) > 1 else tables[0].label


class ImageForm(ui.Modal):
    """Formulario 🖼️ Subir imagen: un campo de archivo."""

    def __init__(self, panel: SkinPanel, skin: Skin) -> None:
        super().__init__(title=f"Imagen de {skin.name}"[:45], timeout=VIEW_TIMEOUT)
        self.panel = panel
        self.skin = skin
        self.upload: ui.FileUpload = ui.FileUpload(min_values=1, max_values=1, required=True)
        self.add_item(
            ui.Label(
                text="Tu imagen",
                description=f"PNG, JPG, WebP o GIF, hasta {MAX_UPLOAD_BYTES // 2**20} MB.",
                component=self.upload,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.cog.form_upload(interaction, self.panel, self.skin, self.upload.values)


class SkinPanel(ui.LayoutView):
    """El panel de `pieles` de un miembro, que maneja quien lo abre (él o un admin).

    `files` son las imágenes de sus skins propias, que van adjuntas al mensaje.
    """

    def __init__(
        self,
        cog: Pieles,
        tienda: Tienda,
        guild: discord.Guild,
        member: discord.abc.User,
        viewer: discord.abc.User,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.tienda = tienda
        self.guild = guild
        self.member = member
        self.viewer = viewer
        self.entries: list[InventoryEntry] = []
        #: Skins propias que se venden y no tiene: `(skin, artículo, precio)`.
        self.offers: list[tuple[Skin, int, int]] = []
        #: Última línea de resultado (subida, borrado), bajo la cabecera.
        self.note: str | None = None
        self.message: discord.Message | None = None

    @property
    def own(self) -> bool:
        """Si el panel es de quien lo mira."""
        return self.viewer.id == self.member.id

    async def load(self) -> None:
        """Lee la mochila y lo que se vende, y monta el panel."""
        guild_id, user_id = self.guild.id, self.member.id
        now = self.tienda.clock()
        if self.own:
            await self.tienda.claim_reward_skins(guild_id, user_id)
        self.entries = await self.tienda.repository.inventory(guild_id, user_id, now)
        self.offers = []
        if self.own:
            await self.tienda.stock_up(guild_id)
            mine = {s.key for s in owned_custom(self.entries)}
            for skin in custom_skins():
                if skin.key in mine or not skin.for_sale:
                    continue
                item = await self.tienda.repository.item_by_key(guild_id, skin.catalog_key)
                if item is not None and item.visible:
                    self.offers.append((skin, item.id, quote(item, now).total))
        self.rebuild()

    def files(self) -> list[discord.File]:
        """Las imágenes de sus skins propias, para adjuntarlas al mensaje del panel."""
        files = []
        for skin in owned_custom(self.entries):
            image = self.tienda.skin_image(self.guild.id, self.member.id, skin.key)
            if image is not None:
                files.append(discord.File(io.BytesIO(image.data), filename=image_name(skin)))
        return files

    def rebuild(self) -> None:
        self.clear_items()
        guild_id, user_id = self.guild.id, self.member.id
        worn = self.tienda.worn_skins(guild_id, user_id)
        container = ui.Container(accent_colour=COLOR)
        title = "Tus pieles" if self.own else f"Pieles de {self.member.display_name}"
        container.add_item(ui.TextDisplay(f"# 🎨 {title}"))
        if self.note:
            container.add_item(ui.TextDisplay(self.note))
        container.add_item(ui.Separator())
        lines = skin_lines(self.entries, worn) or [
            "Ninguna skin todavía. Mira la pestaña 🎨 Skins de la `tienda`."
        ]
        container.add_item(ui.TextDisplay("\n".join(lines)))

        for skin in owned_custom(self.entries):
            image = self.tienda.skin_image(guild_id, user_id, skin.key)
            container.add_item(ui.Separator())
            state = "con imagen" if image else "sin imagen: de momento, fieltro liso"
            container.add_item(ui.TextDisplay(f"### {skin.label} · {where_text(skin)}\n-# {state}"))
            if image is not None:
                container.add_item(
                    ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{image_name(skin)}"))
                )
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(
                self._button(
                    "🖼️ Subir imagen",
                    self._open_form(skin),
                    f"pieles:subir:{skin.key}",
                    primary=True,
                )
            )
            if image is not None:
                row.add_item(
                    self._button("🗑️ Quitar imagen", self._remove(skin), f"pieles:quitar:{skin.key}")
                )
            container.add_item(row)

        if self.offers:
            container.add_item(ui.Separator())
            offer_row: ui.ActionRow = ui.ActionRow()
            for skin, item_id, price in self.offers[:5]:
                offer_row.add_item(
                    self._button(
                        f"🛒 {skin.name} · {format_amount(price)}",
                        self._buy(item_id),
                        f"pieles:comprar:{item_id}",
                    )
                )
            container.add_item(offer_row)
        self.add_item(container)

        if self.own and (options := skin_game_options(self.entries, worn)):
            select: ui.Select = ui.Select(
                placeholder="🎨 Cambiar la skin de una mesa",
                options=options,
                custom_id="pieles:skin",
            )

            async def wear(interaction: discord.Interaction) -> None:
                await self.tienda.open_skin_picker(interaction, select.values[0])

            select.callback = wear  # type: ignore[method-assign]
            wear_row: ui.ActionRow = ui.ActionRow()
            wear_row.add_item(select)
            self.add_item(wear_row)

    @staticmethod
    def _button(  # noqa: ANN205
        label: str,
        callback,  # noqa: ANN001
        custom_id: str,
        *,
        primary: bool = False,
    ) -> ui.Button:
        style = discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary
        button: ui.Button = ui.Button(label=label[:80], style=style, custom_id=custom_id)
        button.callback = callback  # type: ignore[method-assign]
        return button

    def _open_form(self, skin: Skin):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            # Un formulario tiene que ser la primera respuesta: nada de `ack` antes.
            await interaction.response.send_modal(ImageForm(self, skin))

        return callback

    def _remove(self, skin: Skin):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.button_remove(interaction, self, skin)

        return callback

    def _buy(self, item_id: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            await self.tienda.open_checkout(interaction, item_id)

        return callback

    async def refresh(self, interaction: discord.Interaction) -> None:
        """Vuelve a leer la mochila y repinta el panel (con `ack` ya hecho)."""
        await self.load()
        await edit(interaction, view=self, attachments=self.files(), allowed_mentions=NO_MENTIONS)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.viewer.id and may_manage(interaction.user, self.member):
            return True
        await interaction.response.send_message(
            "Este panel no es tuyo. Abre el tuyo con `pieles`.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass  # Borrado o sin permisos: el panel ya no se puede tocar igual.


class Pieles(commands.Cog):
    """Las skins de cada uno en un panel, con las imágenes de las skins propias."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _tienda(self) -> Tienda | None:
        return find_cog(self.bot, Tienda)

    # -- Imágenes ------------------------------------------------------------------

    async def upload(
        self,
        guild_id: int,
        actor: discord.abc.User,
        member: discord.abc.User,
        attachment: discord.Attachment,
        channel: object,
        *,
        skin: Skin | None = None,
    ) -> str:
        """Limpia y guarda la imagen de una skin propia de `member`.

        Sin `skin`, la primera propia que tenga. Comprueba el permiso otra vez:
        el dueño o un admin.

        Returns:
            El texto para quien la sube: lo que ha pasado o por qué no.
        """
        tienda = self._tienda()
        if tienda is None:
            return "La tienda está cerrada ahora mismo."
        if not may_manage(actor, member):
            return "Solo los admins pueden tocar las pieles de otro."
        owned = owned_custom(await tienda.repository.inventory(guild_id, member.id, tienda.clock()))
        if skin is None:
            skin = owned[0] if owned else None
        if skin is None or skin not in owned:
            who = "No tienes" if actor.id == member.id else f"{member.display_name} no tiene"
            return f"{who} ninguna piel que lleve imagen. El 🖼️ Tapete propio está en la `tienda`."
        if attachment.size > MAX_UPLOAD_BYTES:
            return f"❌ Pesa demasiado: el máximo son {MAX_UPLOAD_BYTES // 2**20} MB."
        try:
            raw = await attachment.read()
            image = await asyncio.to_thread(prepare, raw)
        except SkinImageError as error:
            return f"❌ {error}"
        except discord.HTTPException:
            logger.warning("No se pudo descargar la imagen de una piel", exc_info=True)
            return "No se pudo descargar el archivo. Inténtalo de nuevo."
        await tienda.set_skin_image(guild_id, member.id, skin, image, set_by=actor.id)
        own = actor.id == member.id
        await logros.track(self.bot, guild_id, actor, channel, shop_skin_image_stats(own=own))
        line = tienda.rng.choice(UPLOADED_LINES).format(skin=f"**{skin.label}**")
        if not own:
            line += f" (de {member.display_name})"
        if pet := await mascotas.cameo(self.bot, guild_id, member.id, Moment(Event.USE)):
            line += f"\n{pet}"
        return line

    async def remove(
        self,
        guild_id: int,
        actor: discord.abc.User,
        member: discord.abc.User,
        skin: Skin,
        channel: object,
    ) -> str:
        """Quita la imagen de una skin propia de `member` (el dueño o un admin)."""
        tienda = self._tienda()
        if tienda is None:
            return "La tienda está cerrada ahora mismo."
        if not may_manage(actor, member):
            return "Solo los admins pueden tocar las pieles de otro."
        if not await tienda.remove_skin_image(guild_id, member.id, skin):
            return "Esa piel ya no tenía imagen."
        if actor.id != member.id:
            await logros.track(self.bot, guild_id, actor, channel, shop_skin_image_stats(own=False))
            await logros.track(self.bot, guild_id, member, channel, shop_skin_censored_stats())
            return f"🗑️ Imagen de {skin.label} quitada a {member.display_name}."
        return f"🗑️ Imagen de {skin.label} quitada: vuelve el fieltro liso."

    async def form_upload(
        self,
        interaction: discord.Interaction,
        panel: SkinPanel,
        skin: Skin,
        attachments: list[discord.Attachment],
    ) -> None:
        """El formulario 🖼️ Subir imagen: guarda la imagen y repinta el panel."""
        # Descargar, limpiar y guardar tardan: se acepta el envío antes.
        await ack(interaction)
        if not attachments:
            await notify(interaction, "No ha llegado ningún archivo.")
            return
        panel.note = await self.upload(
            panel.guild.id,
            interaction.user,
            panel.member,
            attachments[0],
            interaction.channel,
            skin=skin,
        )
        await panel.refresh(interaction)

    async def button_remove(
        self, interaction: discord.Interaction, panel: SkinPanel, skin: Skin
    ) -> None:
        """El botón 🗑️ Quitar imagen: la borra y repinta el panel."""
        await ack(interaction)
        panel.note = await self.remove(
            panel.guild.id, interaction.user, panel.member, skin, interaction.channel
        )
        await panel.refresh(interaction)

    # -- Comando -------------------------------------------------------------------

    async def open_panel(
        self, guild: discord.Guild, viewer: discord.abc.User, member: discord.abc.User
    ) -> SkinPanel | str:
        """El panel de `member` para `viewer`, o el motivo por el que no se abre."""
        tienda = self._tienda()
        if tienda is None:
            return "La tienda está cerrada ahora mismo."
        if not may_manage(viewer, member):
            return "Solo los admins pueden abrir las pieles de otro. Las tuyas, con `pieles`."
        panel = SkinPanel(self, tienda, guild, member, viewer)
        await panel.load()
        return panel

    @app_commands.command(
        name="pieles", description="Tus skins: qué lleva cada mesa y la imagen de tu tapete propio."
    )
    @app_commands.guild_only()
    @app_commands.describe(
        miembro="Solo admins: las pieles de otro miembro.",
        imagen="Sube la imagen de tu 🖼️ Tapete propio sin abrir el panel.",
    )
    async def pieles(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member | None = None,
        imagen: discord.Attachment | None = None,
    ) -> None:
        """`/pieles`: abre el panel en privado o, con `imagen`, la sube directamente."""
        guild, user = interaction.guild, interaction.user
        if guild is None:
            return
        member = miembro or user
        # Leer la mochila (y descargar la imagen) va a la base de datos: «pensando…» privado.
        await interaction.response.defer(thinking=True, ephemeral=True)
        if imagen is not None:
            text = await self.upload(guild.id, user, member, imagen, interaction.channel)
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        panel = await self.open_panel(guild, user, member)
        if isinstance(panel, str):
            await interaction.followup.send(panel, ephemeral=True)
            return
        await edit(interaction, view=panel, attachments=panel.files(), allowed_mentions=NO_MENTIONS)
        await logros.track(self.bot, guild.id, user, interaction.channel, shop_skin_panel_stats())

    @commands.command(name="pieles")
    @commands.guild_only()
    async def pieles_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto: `.pieles [@miembro]`, con la imagen adjunta para subirla."""
        guild, author = ctx.guild, ctx.author
        if guild is None:
            return
        member = miembro or author
        if ctx.message.attachments:
            async with ctx.typing():
                text = await self.upload(
                    guild.id, author, member, ctx.message.attachments[0], ctx.channel
                )
            await ctx.send(text, allowed_mentions=NO_MENTIONS)
            return
        panel = await self.open_panel(guild, author, member)
        if isinstance(panel, str):
            await ctx.send(panel, allowed_mentions=NO_MENTIONS)
            return
        panel.message = await ctx.send(
            view=panel, files=panel.files(), allowed_mentions=NO_MENTIONS
        )
        await logros.track(self.bot, guild.id, author, ctx.channel, shop_skin_panel_stats())


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog (necesita la tienda cargada antes)."""
    await bot.add_cog(Pieles(bot))
