"""La trastienda: el panel con el que los administradores montan la tienda.

Lo abre `catalogo` (cog Admin). No es un cog: son las vistas y formularios
del panel, separados de `bot.cogs.shop` para que el escaparate no crezca sin
límite.

Qué se puede hacer, todo con botones y formularios, sin código ni PRs:

- **➕ Rol**: elegir un rol del servidor, ponerle precio y decidir si es para
  siempre o un alquiler (`7d`, `12h`, `2 semanas`…).
- **➕ Potenciador**: multiplicador de XP (`x2`, `1,5`) y cuánto dura.
- **➕ Coleccionable**: un objeto de capricho, con existencias limitadas si
  se quiere (cada unidad sale numerada).
- **✏️ Editar** cualquier artículo: datos, existencias, límite por persona,
  nivel mínimo, rebaja con fecha de fin, tipo de IGIC, ocultarlo o retirarlo.
  Vale también para los del surtido de serie (`bot.services.shop_catalog`).
- **📦 Reponer surtido**: vuelve a poner a la venta los artículos de serie que
  se hayan retirado. Los que siguen en el catálogo (aunque estén ocultos) no se
  duplican.

En el nombre se puede empezar por un emoji (`🛥️ Yate de Perro Sanxe`) y se
usa como icono del artículo.

Permisos: el panel solo responde a quien lo abrió y solo si sigue siendo
administrador; se comprueba en cada pulsación, no solo al abrirlo.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.cogs.shop import COLOR, VIEW_TIMEOUT, _button, item_card, kind_detail
from bot.services.economy import format_amount
from bot.services.shop import (
    MAX_DESCRIPTION,
    MAX_NAME,
    MAX_STOCK,
    Kind,
    ShopError,
    ShopItem,
    clean_text,
    format_multiplier,
    format_span,
    parse_discount,
    parse_level,
    parse_limit,
    parse_multiplier,
    parse_price,
    parse_span,
    rate_label,
    split_emoji,
)
from bot.services.taxes import IGIC_DEFAULT, IGIC_RATES, TAX_COLLECTOR
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.cogs.shop import Tienda

logger = logging.getLogger(__name__)

PANEL_COLOR = discord.Color.from_rgb(88, 101, 242)
PANEL_PAGE_SIZE = 5
NOT_ADMIN = "Solo los administradores pueden tocar la trastienda."

#: Ejemplos que salen en los formularios.
DEFAULT_BOOST_NAME = "⚡ Turbo de XP"


def _is_admin(user: discord.abc.User) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


def admin_card(item: ShopItem, guild: discord.Guild, now: float) -> str:
    """Resumen de un artículo para la lista de la trastienda."""
    tags = [f"#{item.id}", kind_detail(item)]
    sold = f"{item.sold}/{item.stock}" if item.stock is not None else str(item.sold)
    tags.append(f"vendidos {sold}")
    if item.on_sale(now):
        tags.append(f"🏷️ -{item.discount} %")
    if not item.visible:
        tags.append("🙈 oculto")
    if item.kind is Kind.ROLE and guild.get_role(item.role_id or 0) is None:
        tags.append("⚠️ el rol ya no existe")
    return (
        f"**{item.emoji} {item.name}** · {format_amount(item.price)} + IGIC "
        f"{rate_label(item.igic_rate.rate)}\n-# " + " · ".join(tags)
    )


# -- Formularios ----------------------------------------------------------------------


Handler = Callable[[discord.Interaction, dict[str, str]], Awaitable[None]]


class Field:
    """Un campo de formulario: clave, etiqueta y cómo se rellena."""

    def __init__(
        self,
        key: str,
        label: str,
        *,
        default: str = "",
        placeholder: str | None = None,
        required: bool = False,
        long: bool = False,
        max_length: int = 100,
        hint: str | None = None,
    ) -> None:
        self.key = key
        self.label = label
        self.default = default
        self.placeholder = placeholder
        self.required = required
        self.long = long
        self.max_length = max_length
        self.hint = hint


class ItemForm(ui.Modal):
    """Formulario genérico de la trastienda; `handler` recibe los valores escritos."""

    def __init__(self, title: str, fields: list[Field], handler: Handler) -> None:
        super().__init__(title=title[:45], timeout=VIEW_TIMEOUT)
        self.handler = handler
        self.inputs: dict[str, ui.TextInput] = {}
        for field in fields:
            text_input: ui.TextInput = ui.TextInput(
                default=field.default or None,
                placeholder=field.placeholder,
                required=field.required,
                style=discord.TextStyle.paragraph if field.long else discord.TextStyle.short,
                max_length=field.max_length,
            )
            self.inputs[field.key] = text_input
            self.add_item(ui.Label(text=field.label, description=field.hint, component=text_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {key: text_input.value for key, text_input in self.inputs.items()}
        # Todos los formularios guardan en la base de datos: se aceptan antes, y
        # los manejadores contestan con `edit` o `notify`.
        await ack(interaction)
        try:
            await self.handler(interaction, values)
        except (ValueError, ShopError) as error:
            await notify(interaction, f"❌ {error}")


def _name_field(default: str = "") -> Field:
    return Field(
        "name",
        "Nombre",
        default=default,
        placeholder="🛥️ Yate de Perro Sanxe",
        required=True,
        max_length=MAX_NAME + 10,
        hint="Si empieza por un emoji, será su icono.",
    )


def _price_field(default: str = "") -> Field:
    return Field(
        "price",
        "Precio sin IGIC",
        default=default,
        placeholder="5000, 5k, 1,5m…",
        required=True,
        max_length=20,
        hint="El IGIC se suma en caja (7 % por defecto).",
    )


def _description_field(default: str = "") -> Field:
    return Field(
        "description",
        "Descripción",
        default=default,
        placeholder="Para los que brillan sin pedir permiso.",
        long=True,
        max_length=MAX_DESCRIPTION,
    )


def _role_span_field(default: str = "") -> Field:
    return Field(
        "duration",
        "Duración del alquiler",
        default=default,
        placeholder="Vacío = para siempre · 7d · 12h · 2 semanas",
        max_length=20,
        hint="Sin unidad son días. Volver a comprar alarga el alquiler.",
    )


def _boost_fields(item: ShopItem | None = None) -> list[Field]:
    return [
        Field(
            "multiplier",
            "Multiplicador de XP",
            default=format_multiplier(item.multiplier or 200) if item else "x2",
            placeholder="x2, 1,5, 150 %",
            required=True,
            max_length=10,
            hint="De ×1,1 a ×3. Multiplica el XP de mensajes y voz.",
        ),
        Field(
            "duration",
            "Cuánto dura",
            default=_span_text(item.duration) if item else "2h",
            placeholder="2h, 1d…",
            required=True,
            max_length=20,
            hint="Sin unidad son horas. Si ya tienes uno, se pone a la cola.",
        ),
    ]


def _span_text(seconds: int | None) -> str:
    """Duración en el formato que acepta `parse_span` (`7d`, `36h`)."""
    if seconds is None:
        return ""
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    return f"{seconds // 3600}h"


def _parse_name(values: dict[str, str], kind: Kind) -> tuple[str, str]:
    emoji, name = split_emoji(values.get("name"), kind)
    return emoji, clean_text(name, MAX_NAME, what="El nombre")


# -- Elegir rol -----------------------------------------------------------------------


class RolePicker(ui.View):
    """Paso previo de ➕ Rol: elegir qué rol del servidor se vende."""

    def __init__(self, panel: AdminPanel) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.panel = panel
        select: ui.RoleSelect = ui.RoleSelect(placeholder="¿Qué rol quieres vender?")
        select.callback = self._picked  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.panel.interaction_check(interaction)

    async def _picked(self, interaction: discord.Interaction) -> None:
        role = self.select.values[0]
        panel = self.panel
        if (problem := panel.cog.role_problem(panel.guild, role)) is not None:
            await interaction.response.edit_message(content=f"❌ {problem}", view=self)
            return

        async def create(submit: discord.Interaction, values: dict[str, str]) -> None:
            emoji, name = _parse_name(values, Kind.ROLE)
            item = await panel.cog.repository.create_item(
                panel.guild.id,
                kind=Kind.ROLE,
                name=name,
                emoji=emoji,
                description=clean_text(
                    values.get("description"),
                    MAX_DESCRIPTION,
                    what="La descripción",
                    required=False,
                ),
                price=parse_price(values["price"]),
                igic=IGIC_DEFAULT.key,
                now=panel.cog.clock(),
                role_id=role.id,
                duration=parse_span(values.get("duration"), "d"),
            )
            await edit(
                submit,
                content=f"✅ A la venta: {item.emoji} **{item.name}** ({kind_detail(item)}).",
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await panel.refresh(f"✅ Nuevo: {item.emoji} {item.name}")

        await interaction.response.send_modal(
            ItemForm(
                f"Vender {role.name}",
                [
                    _name_field(f"🎭 {role.name}"),
                    _price_field(),
                    _role_span_field(),
                    _description_field(),
                ],
                create,
            )
        )


# -- Editor de un artículo ------------------------------------------------------------


class ItemEditor(ui.LayoutView):
    """Ficha editable de un artículo, con sus botones y el tipo de IGIC."""

    def __init__(self, panel: AdminPanel, item: ShopItem) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.panel = panel
        self.item = item
        self.notice: str | None = None
        self.confirm_delete = False
        self.deleted = False
        self.rebuild()

    @property
    def cog(self) -> Tienda:
        return self.panel.cog

    def rebuild(self) -> None:
        self.clear_items()
        item = self.item
        now = self.cog.clock()
        container = ui.Container(accent_colour=COLOR if item.visible else PANEL_COLOR)
        if self.deleted:
            container.add_item(
                ui.TextDisplay(
                    f"🗑️ **{item.emoji} {item.name}** ha salido del catálogo. "
                    "Lo que ya se vendió sigue en las mochilas."
                )
            )
            self.add_item(container)
            return
        details = [
            f"IGIC: {item.igic_rate.label} ({rate_label(item.igic_rate.rate)}, "
            f"{item.igic_rate.law})",
            f"Vendidos: {item.sold}"
            + (f" de {item.stock}" if item.stock is not None else " (sin límite de existencias)"),
            "Límite por persona: "
            + (str(item.per_user) if item.per_user is not None else "sin límite"),
            f"Nivel mínimo: {item.min_level or 'ninguno'}",
            "Visible en la tienda" if item.visible else "🙈 Oculto: no sale en la tienda",
        ]
        text = item_card(item, now) + "\n\n" + "\n".join(f"-# {d}" for d in details)
        if self.notice:
            text = f"{self.notice}\n\n{text}"
        container.add_item(ui.TextDisplay(text))
        self.add_item(container)

        actions: ui.ActionRow = ui.ActionRow()
        actions.add_item(_button("✏️ Datos", self._edit_data, style=discord.ButtonStyle.primary))
        actions.add_item(_button("📦 Límites", self._edit_limits))
        actions.add_item(_button("🏷️ Rebaja", self._edit_sale))
        actions.add_item(
            _button("🙈 Ocultar" if item.visible else "👁️ Mostrar", self._toggle_visible)
        )
        self.add_item(actions)

        options = [
            discord.SelectOption(
                label=f"IGIC {rate.label} · {rate_label(rate.rate)}",
                value=rate.key,
                description=f"{rate.example} ({rate.law})"[:100],
                default=rate.key == item.igic_rate.key,
            )
            for rate in IGIC_RATES
        ]
        select: ui.Select = ui.Select(placeholder="Tipo de IGIC", options=options)

        async def pick_rate(interaction: discord.Interaction) -> None:
            await self._save(interaction, "🧾 IGIC cambiado.", igic=select.values[0])

        select.callback = pick_rate  # type: ignore[method-assign]
        igic_row: ui.ActionRow = ui.ActionRow()
        igic_row.add_item(select)
        self.add_item(igic_row)

        danger: ui.ActionRow = ui.ActionRow()
        if self.confirm_delete:
            danger.add_item(
                _button("⚠️ Sí, retirarlo", self._delete, style=discord.ButtonStyle.danger)
            )
            danger.add_item(_button("Mejor no", self._cancel_delete))
        else:
            danger.add_item(
                _button(
                    "🗑️ Retirar del catálogo", self._ask_delete, style=discord.ButtonStyle.danger
                )
            )
        self.add_item(danger)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.panel.interaction_check(interaction)

    async def _save(self, interaction: discord.Interaction, notice: str, **fields: object) -> None:
        """Guarda cambios, repinta la ficha y refresca la trastienda."""
        await ack(interaction)
        self.item = await self.cog.repository.update_item(
            self.panel.guild.id, self.item.id, **fields
        )
        self.notice = notice
        self.rebuild()
        await edit(interaction, view=self, allowed_mentions=discord.AllowedMentions.none())
        await self.panel.refresh()

    async def _edit_data(self, interaction: discord.Interaction) -> None:
        item = self.item
        fields = [_name_field(f"{item.emoji} {item.name}"), _price_field(str(item.price))]
        if item.kind is Kind.ROLE:
            fields.append(_role_span_field(_span_text(item.duration)))
        elif item.kind is Kind.BOOST:
            fields += _boost_fields(item)
        fields.append(_description_field(item.description))

        async def save(submit: discord.Interaction, values: dict[str, str]) -> None:
            emoji, name = _parse_name(values, item.kind)
            changes: dict[str, object] = {
                "name": name,
                "emoji": emoji,
                "price": parse_price(values["price"]),
                "description": clean_text(
                    values.get("description"),
                    MAX_DESCRIPTION,
                    what="La descripción",
                    required=False,
                ),
            }
            if item.kind is Kind.ROLE:
                changes["duration"] = parse_span(values.get("duration"), "d")
            elif item.kind is Kind.BOOST:
                changes["multiplier"] = parse_multiplier(values["multiplier"])
                changes["duration"] = parse_span(values["duration"], "h")
                if changes["duration"] is None:
                    raise ValueError("Un potenciador tiene que durar algo, mi amor.")
            await self._save(submit, "✅ Datos guardados.", **changes)

        await interaction.response.send_modal(ItemForm(f"Editar {item.name}", fields, save))

    async def _edit_limits(self, interaction: discord.Interaction) -> None:
        item = self.item
        fields = [
            Field(
                "stock",
                "Existencias totales",
                default=str(item.stock) if item.stock is not None else "",
                placeholder="Vacío = ilimitadas",
                max_length=6,
                hint=f"Cuenta lo ya vendido ({item.sold}). Si son limitadas, van numeradas.",
            ),
            Field(
                "per_user",
                "Máximo por persona",
                default=str(item.per_user) if item.per_user is not None else "",
                placeholder="Vacío = sin límite",
                max_length=6,
            ),
            Field(
                "min_level",
                "Nivel mínimo",
                default=str(item.min_level or ""),
                placeholder="Vacío = cualquiera",
                max_length=3,
            ),
        ]

        async def save(submit: discord.Interaction, values: dict[str, str]) -> None:
            await self._save(
                submit,
                "📦 Límites guardados.",
                stock=parse_limit(values.get("stock"), "Las existencias", MAX_STOCK),
                per_user=parse_limit(values.get("per_user"), "El máximo por persona", MAX_STOCK),
                min_level=parse_level(values.get("min_level")),
            )

        await interaction.response.send_modal(ItemForm(f"Límites de {item.name}", fields, save))

    async def _edit_sale(self, interaction: discord.Interaction) -> None:
        item = self.item
        fields = [
            Field(
                "discount",
                "Rebaja en %",
                default=str(item.discount or ""),
                placeholder="20 · vacío o 0 la quita",
                max_length=3,
                hint="Hasta el 90 %. El IGIC se calcula sobre el precio rebajado.",
            ),
            Field(
                "duration",
                "Cuánto dura",
                placeholder="48h, 3d · vacío = hasta que la quites",
                max_length=20,
                hint="Sin unidad son horas.",
            ),
        ]

        async def save(submit: discord.Interaction, values: dict[str, str]) -> None:
            pct = parse_discount(values.get("discount"))
            span = parse_span(values.get("duration"), "h") if pct else None
            until = self.cog.clock() + span if span is not None else None
            notice = f"🏷️ Rebaja del {pct} % puesta." if pct else "🏷️ Rebaja quitada."
            await self._save(submit, notice, discount=pct, discount_until=until)

        await interaction.response.send_modal(ItemForm(f"Rebaja de {item.name}", fields, save))

    async def _toggle_visible(self, interaction: discord.Interaction) -> None:
        visible = not self.item.visible
        notice = "👁️ Vuelve a estar a la venta." if visible else "🙈 Oculto de la tienda."
        await self._save(interaction, notice, visible=int(visible))

    async def _ask_delete(self, interaction: discord.Interaction) -> None:
        self.confirm_delete = True
        self.notice = "⚠️ ¿Seguro? Desaparece del catálogo (lo ya vendido se queda)."
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def _cancel_delete(self, interaction: discord.Interaction) -> None:
        self.confirm_delete, self.notice = False, None
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def _delete(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        await self.cog.repository.delete_item(self.panel.guild.id, self.item.id)
        self.deleted = True
        self.rebuild()
        self.stop()
        await edit(interaction, view=self, allowed_mentions=discord.AllowedMentions.none())
        await self.panel.refresh(f"🗑️ Retirado: {self.item.emoji} {self.item.name}")


# -- Panel ----------------------------------------------------------------------------


class AdminPanel(ui.LayoutView):
    """La trastienda: lista del catálogo, ventas y botones para crear y editar."""

    def __init__(self, cog: Tienda, guild: discord.Guild, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.page = 0
        self.items_all: list[ShopItem] = []
        self.header = ""
        self.notice: str | None = None
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None

    async def load(self) -> None:
        """Lee el catálogo completo (con lo oculto) y las ventas."""
        self.items_all = await self.cog.repository.items(self.guild.id, include_hidden=True)
        stats = await self.cog.repository.stats(self.guild.id)
        self.header = (
            f"# 🛠️ Trastienda del colmado\n"
            f"{len(self.items_all)} artículo{'' if len(self.items_all) == 1 else 's'} · "
            f"{stats.sales} venta{'' if stats.sales == 1 else 's'} · facturado "
            f"{format_amount(stats.revenue)} · IGIC para {TAX_COLLECTOR}: "
            f"{format_amount(stats.tax)}\n"
            "-# Precios sin IGIC. Solo lo ven y lo tocan los administradores."
        )
        self.rebuild()

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.items_all) // PANEL_PAGE_SIZE))

    def rebuild(self) -> None:
        self.clear_items()
        now = self.cog.clock()
        self.page = min(self.page, self.pages - 1)
        container = ui.Container(accent_colour=PANEL_COLOR)
        text = self.header + (f"\n\n{self.notice}" if self.notice else "")
        container.add_item(ui.TextDisplay(text))
        container.add_item(ui.Separator())
        page_items = self.items_all[self.page * PANEL_PAGE_SIZE : (self.page + 1) * PANEL_PAGE_SIZE]
        if not page_items:
            container.add_item(
                ui.TextDisplay("Todavía no hay nada. Empieza con los botones de abajo. 👇")
            )
        for item in page_items:
            container.add_item(
                ui.Section(
                    ui.TextDisplay(admin_card(item, self.guild, now)),
                    accessory=_button("✏️ Editar", self._edit(item.id)),
                )
            )
        self.add_item(container)

        create: ui.ActionRow = ui.ActionRow()
        green = discord.ButtonStyle.success
        create.add_item(_button("➕ Rol", self._new_role, style=green))
        create.add_item(_button("➕ Potenciador", self._new_boost, style=green))
        create.add_item(_button("➕ Coleccionable", self._new_trophy, style=green))
        create.add_item(_button("📦 Reponer surtido", self._restock))
        self.add_item(create)

        nav: ui.ActionRow = ui.ActionRow()
        nav.add_item(_button("◀", self._move(-1), disabled=self.page == 0))
        nav.add_item(
            _button(f"Página {self.page + 1}/{self.pages}", self._refresh_button, disabled=True)
        )
        nav.add_item(_button("▶", self._move(1), disabled=self.page >= self.pages - 1))
        nav.add_item(_button("🔄", self._refresh_button))
        self.add_item(nav)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo quien abrió el panel, y solo si sigue siendo administrador."""
        if interaction.user.id == self.owner.id and _is_admin(interaction.user):
            return True
        await interaction.response.send_message(NOT_ADMIN, ephemeral=True)
        return False

    async def refresh(self, notice: str | None = None) -> None:
        """Vuelve a leer el catálogo y repinta el panel donde esté."""
        self.notice = notice
        await self.load()
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(
                    view=self, allowed_mentions=discord.AllowedMentions.none()
                )
            elif self.message is not None:
                await self.message.edit(view=self, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            # El panel efímero caduca a los 15 minutos; los cambios ya están guardados.
            logger.debug("No se pudo refrescar la trastienda", exc_info=True)

    def _move(self, step: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.page = max(0, min(self.pages - 1, self.page + step))
            self.rebuild()
            await interaction.response.edit_message(view=self)

        return callback

    async def _refresh_button(self, interaction: discord.Interaction) -> None:
        self.notice = None
        await ack(interaction)
        await self.load()
        await edit(interaction, view=self)

    def _edit(self, item_id: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await ack(interaction, new_message=True)
            item = await self.cog.repository.item(self.guild.id, item_id)
            if item is None:
                await edit(interaction, content="Ese artículo ya no existe.")
                return
            await edit(
                interaction,
                view=ItemEditor(self, item),
                allowed_mentions=discord.AllowedMentions.none(),
            )

        return callback

    async def _restock(self, interaction: discord.Interaction) -> None:
        """Vuelve a poner a la venta los artículos de serie que se hayan retirado."""
        await ack(interaction)
        added = await self.cog.restock(self.guild.id)
        self.notice = (
            f"📦 Repuestos {added} artículo{'' if added == 1 else 's'} del surtido de serie."
            if added
            else "📦 El surtido de serie ya está completo: no faltaba nada."
        )
        await self.load()
        await edit(interaction, view=self)

    async def _new_role(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Elige el rol. Tiene que estar por debajo del mío y sin permisos de moderación.",
            view=RolePicker(self),
            ephemeral=True,
        )

    async def _new_boost(self, interaction: discord.Interaction) -> None:
        async def create(submit: discord.Interaction, values: dict[str, str]) -> None:
            emoji, name = _parse_name(values, Kind.BOOST)
            span = parse_span(values["duration"], "h")
            if span is None:
                raise ValueError("Un potenciador tiene que durar algo, mi amor.")
            item = await self.cog.repository.create_item(
                self.guild.id,
                kind=Kind.BOOST,
                name=name,
                emoji=emoji,
                description=clean_text(
                    values.get("description"),
                    MAX_DESCRIPTION,
                    what="La descripción",
                    required=False,
                ),
                price=parse_price(values["price"]),
                igic=IGIC_DEFAULT.key,
                now=self.cog.clock(),
                duration=span,
                multiplier=parse_multiplier(values["multiplier"]),
            )
            await notify(
                submit,
                f"✅ A la venta: {item.emoji} **{item.name}** "
                f"({format_multiplier(item.multiplier or 100)} durante {format_span(span)}).",
            )
            await self.refresh(f"✅ Nuevo: {item.emoji} {item.name}")

        fields = [_name_field(DEFAULT_BOOST_NAME), _price_field(), *_boost_fields()]
        fields.append(_description_field())
        await interaction.response.send_modal(ItemForm("Nuevo potenciador", fields, create))

    async def _new_trophy(self, interaction: discord.Interaction) -> None:
        async def create(submit: discord.Interaction, values: dict[str, str]) -> None:
            emoji, name = _parse_name(values, Kind.TROPHY)
            item = await self.cog.repository.create_item(
                self.guild.id,
                kind=Kind.TROPHY,
                name=name,
                emoji=emoji,
                description=clean_text(
                    values.get("description"),
                    MAX_DESCRIPTION,
                    what="La descripción",
                    required=False,
                ),
                price=parse_price(values["price"]),
                igic=IGIC_DEFAULT.key,
                now=self.cog.clock(),
                stock=parse_limit(values.get("stock"), "Las existencias", MAX_STOCK),
            )
            edition = f", {item.stock} unidades numeradas" if item.stock is not None else ""
            await notify(submit, f"✅ A la venta: {item.emoji} **{item.name}**{edition}.")

            await self.refresh(f"✅ Nuevo: {item.emoji} {item.name}")

        fields = [
            _name_field(),
            _price_field(),
            Field(
                "stock",
                "Existencias",
                placeholder="Vacío = ilimitadas · 10 = edición limitada",
                max_length=6,
                hint="Si son limitadas, cada unidad sale con su número de serie.",
            ),
            _description_field(),
        ]
        await interaction.response.send_modal(ItemForm("Nuevo coleccionable", fields, create))

    async def on_timeout(self) -> None:
        for child in self.walk_children():
            if isinstance(child, ui.Button | ui.Select):
                child.disabled = True
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la trastienda", exc_info=True)
