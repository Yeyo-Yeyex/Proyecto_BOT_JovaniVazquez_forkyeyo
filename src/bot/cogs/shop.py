"""Tienda: `tienda` y `mochila`, la caja con IGIC y la caducidad de los alquileres.

- `tienda` abre el escaparate de El Colmado de Jovani: pestañas por sección
  (roles, potenciadores, coleccionables), cada artículo con su botón
  **Comprar**, rebajas tachadas, existencias que quedan y etiquetas de
  "nuevo" y "lo más vendido". Cualquiera puede comprar desde el escaparate de
  otro: la caja se abre solo para quien pulsa.
- La caja enseña el ticket antes de pagar (precio, rebaja, base, IGIC y
  total) y pide confirmar. Al pagar sale la factura simplificada, que solo ve
  el comprador, y un aviso público en el canal para presumir.
- `mochila [miembro]` enseña lo que tiene alguien. Su dueño puede ponerse y
  quitarse los roles que compró para siempre (útil para los de color).
- El catálogo lo montan los administradores con `catalogo` (cog Admin; la
  trastienda vive en `bot.cogs.shop_admin`).

Dinero: cada compra pasa por `EconomyService.purchase` con su IGIC (tipo por
artículo, ver `bot.services.taxes.IGIC_RATES`), que va al Estado. Pagar llama
a `renta.remind` y la factura lleva la línea de la renta pendiente. Si el rol
comprado no se puede dar, se devuelve todo con `refund_purchase`.

Logros: categoría Tienda (`shop_stats`), con `achievements.track` al pagar.

Otros cogs: `xp_multiplier(bot, …)` da el multiplicador de XP de los
potenciadores; lo usa `bot.cogs.message_stats` al dar XP por mensajes y voz.
Lo lee de una caché en memoria, sin tocar la base de datos en cada mensaje.

Permisos: **Gestionar roles** y que el rol del bot esté por encima de los
roles que se venden. Sin ellos, la tienda no deja ponerlos a la venta ni
cobrarlos.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.repositories.shop import InventoryEntry, ShopRepository
from bot.services.achievements import shop_stats
from bot.services.economy import (
    CURRENCY_EMOJI,
    EconomyService,
    InsufficientFundsError,
    format_amount,
)
from bot.services.levels import calculate_level_progress
from bot.services.shop import (
    DANGEROUS_PERMISSIONS,
    SHOP_NAME,
    Kind,
    Quote,
    Sale,
    ShopError,
    ShopItem,
    active_multiplier,
    format_multiplier,
    format_span,
    money,
    quote,
    rate_label,
    receipt,
)
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.cogs import find_cog

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.message_stats import MessageStatsRepository

logger = logging.getLogger(__name__)

#: Dorado de colmado para el escaparate; verde y rojo para la caja.
COLOR = discord.Color.from_rgb(255, 176, 59)
COLOR_PAID = discord.Color.from_rgb(87, 242, 135)
COLOR_ERROR = discord.Color.from_rgb(237, 66, 69)
#: Artículos por página del escaparate. Cada uno usa 3 componentes y Discord
#: admite 40 por mensaje, así que 5 deja sitio a las pestañas y la navegación.
PAGE_SIZE = 5
VIEW_TIMEOUT = 600
#: Cada cuánto se quitan los roles alquilados que han vencido.
EXPIRY_MINUTES = 5

ANNOUNCE_LINES: dict[Kind, tuple[str, ...]] = {
    Kind.ROLE: (
        "{who} estrena {item}. ¡Qué flow, bebé!",
        "{who} se ha comprado {item}. Ahora sí que brilla.",
        "{who} sale del colmado con {item} puesto. Mírala, mírala.",
    ),
    Kind.BOOST: (
        "{who} se mete un {item} en vena. A yapear se ha dicho.",
        "{who} pilla {item}: ojo, que viene con turbo.",
        "{who} compra {item}. El XP no se va a ganar solo, mi amor.",
    ),
    Kind.TROPHY: (
        "{who} se lleva {item} pa' la casa. ¡Wepa!",
        "{who} compra {item}. Pa' presumir, que pa' eso está.",
        "{who} añade {item} a la vitrina. Qué nivel.",
    ),
}


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


def _button(
    label: str,
    callback: Callable[[discord.Interaction], Awaitable[None]],
    *,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    disabled: bool = False,
) -> ui.Button:
    """Botón con su callback ya enganchado."""
    button: ui.Button = ui.Button(label=label, style=style, disabled=disabled)
    button.callback = callback  # type: ignore[method-assign]
    return button


# -- Textos ---------------------------------------------------------------------------


def kind_detail(item: ShopItem) -> str:
    """Qué es el artículo, en pocas palabras."""
    if item.kind is Kind.ROLE:
        span = "para siempre" if item.duration is None else f"durante {format_span(item.duration)}"
        return f"Rol <@&{item.role_id}> {span}"
    if item.kind is Kind.BOOST:
        return (
            f"XP {format_multiplier(item.multiplier or 100)} durante {format_span(item.duration)}"
        )
    return "Coleccionable"


def price_text(item: ShopItem, now: float) -> str:
    """Precio de escaparate, con la rebaja tachada si la hay."""
    price = quote(item, now)
    if not price.discount:
        return f"**{format_amount(price.base)}**"
    until = f" hasta <t:{int(item.discount_until)}:R>" if item.discount_until is not None else ""
    return (
        f"~~{format_amount(price.price)}~~ **{format_amount(price.base)}** · "
        f"🏷️ -{price.discount_pct} %{until}"
    )


def item_card(item: ShopItem, now: float, *, best_seller: bool = False) -> str:
    """Texto de un artículo en el escaparate."""
    lines = [f"### {item.emoji} {item.name}", price_text(item, now)]
    if item.description:
        lines.append(item.description)
    tags = [kind_detail(item)]
    if best_seller:
        tags.append("🔥 Lo más vendido")
    if item.is_new(now):
        tags.append("🆕 Nuevo")
    if item.remaining is not None:
        tags.append(
            "❌ Agotado" if item.sold_out else f"📦 Quedan {item.remaining} de {item.stock}"
        )
    if item.min_level:
        tags.append(f"🔒 Nivel {item.min_level}")
    if item.per_user is not None:
        tags.append(f"👤 Máx. {item.per_user} por persona")
    lines.append("-# " + " · ".join(tags))
    return "\n".join(lines)


def ticket_preview(price: Quote) -> str:
    """El desglose de la caja, antes de pagar."""
    width = 26

    def row(left: str, right: str) -> str:
        return f"{left:<{width - len(right) - 1}} {right}"

    lines = [row("Precio", money(price.price))]
    if price.discount:
        lines.append(row(f"Rebaja -{price.discount_pct} %", f"-{money(price.discount)}"))
    lines += [
        row("Base imponible", money(price.base)),
        row(f"IGIC {rate_label(price.rate.rate)}", money(price.tax)),
        "─" * width,
        row("TOTAL", f"{money(price.total)} Y$"),
    ]
    return "```\n" + "\n".join(lines) + "\n```"


def igic_line(price: Quote) -> str:
    """La mordida del IGIC, en subtexto."""
    if not price.tax:
        return (
            f"-# 🐶 {TAX_COLLECTOR} no cobra nada: este artículo va al tipo cero "
            f"({price.rate.law})."
        )
    return (
        f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(price.tax)} de IGIC "
        f"({price.rate.label.lower()}, {rate_label(price.rate.rate)}, {price.rate.law})."
    )


def entry_line(entry: InventoryEntry, now: float) -> str:
    """Una línea de la mochila."""
    if entry.kind is Kind.ROLE:
        if entry.expires_at is not None:
            return f"{entry.emoji} <@&{entry.role_id}> · vence <t:{int(entry.expires_at)}:R>"
        state = "✅ puesto" if entry.equipped else "💤 guardado"
        return f"{entry.emoji} <@&{entry.role_id}> · {state}"
    if entry.kind is Kind.BOOST:
        boost = format_multiplier(entry.multiplier or 100)
        end = int(entry.expires_at or now)
        if entry.starts_at > now:
            start = int(entry.starts_at)
            return f"{entry.emoji} **{entry.name}** {boost} · en cola, empieza <t:{start}:R>"
        return f"{entry.emoji} **{entry.name}** {boost} · activo hasta <t:{end}:t> (<t:{end}:R>)"
    return f"{entry.emoji} **{entry.name}**"


def trophy_lines(entries: list[InventoryEntry]) -> list[str]:
    """Coleccionables agrupados por artículo, con sus números de serie."""
    groups: dict[int, list[InventoryEntry]] = defaultdict(list)
    for entry in entries:
        groups[entry.item_id].append(entry)
    lines = []
    for group in groups.values():
        first = group[0]
        text = f"{first.emoji} **{first.name}**"
        if len(group) > 1:
            text += f" ×{len(group)}"
        serials = sorted(e.serial for e in group if e.serial is not None)
        if serials and first.edition:
            numbers = ", ".join(str(s) for s in serials)
            text += f" · nº {numbers} de {first.edition}"
        lines.append(text)
    return lines


# -- Escaparate -----------------------------------------------------------------------


class Storefront(ui.LayoutView):
    """El escaparate: pestañas, artículos con su botón Comprar y páginas.

    Las pestañas y las páginas son del dueño; si otro las toca, se le abre
    su propio escaparate. **Comprar** lo puede pulsar cualquiera.
    """

    def __init__(self, cog: Tienda, guild: discord.Guild, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.kind: Kind | None = None
        self.page = 0
        self.items_all: list[ShopItem] = []
        self.best_seller: int | None = None
        self.balance = 0
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None

    async def load(self) -> None:
        """Lee el catálogo, el más vendido y el saldo del dueño."""
        self.items_all = await self.cog.repository.items(self.guild.id)
        self.best_seller = await self.cog.repository.best_seller(self.guild.id)
        self.balance = await self.cog.economy.balance(self.guild.id, self.owner.id)
        self.rebuild()

    @property
    def shown(self) -> list[ShopItem]:
        """Artículos de la pestaña elegida."""
        return [i for i in self.items_all if self.kind is None or i.kind is self.kind]

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.shown) // PAGE_SIZE))

    def rebuild(self) -> None:
        """Monta los componentes con el estado actual."""
        self.clear_items()
        now = self.cog.clock()
        self.page = min(self.page, self.pages - 1)
        container = ui.Container(accent_colour=COLOR)
        container.add_item(
            ui.TextDisplay(
                f"# 🛒 {SHOP_NAME}\n"
                f"¡Wepa, {_name(self.owner)}! Aquí se gasta lo que te dan el casino y el IMV. "
                f"Los precios van sin IGIC: {TAX_COLLECTOR} cobra en caja.\n"
                f"-# {CURRENCY_EMOJI} Saldo de {_name(self.owner)}: {format_amount(self.balance)}"
            )
        )
        tabs: ui.ActionRow = ui.ActionRow()
        for kind in (None, *Kind):
            count = sum(1 for i in self.items_all if kind is None or i.kind is kind)
            label = "🛍️ Todo" if kind is None else f"{kind.icon} {kind.title}"
            style = (
                discord.ButtonStyle.primary if kind is self.kind else discord.ButtonStyle.secondary
            )
            tabs.add_item(_button(f"{label} · {count}", self._tab(kind), style=style))
        container.add_item(tabs)
        container.add_item(ui.Separator())

        page_items = self.shown[self.page * PAGE_SIZE : (self.page + 1) * PAGE_SIZE]
        if not page_items:
            container.add_item(
                ui.TextDisplay(
                    "El colmado está vacío, mi amor. Un admin tiene que llenarlo con `catalogo`."
                    if not self.items_all
                    else "No hay nada en esta sección todavía."
                )
            )
        for item in page_items:
            if item.sold_out:
                buy = _button("Agotado", self._noop, disabled=True)
            else:
                buy = _button(
                    f"Comprar · {money(quote(item, now).base)}",
                    self._buy(item.id),
                    style=discord.ButtonStyle.success,
                )
            card = item_card(item, now, best_seller=item.id == self.best_seller)
            container.add_item(ui.Section(ui.TextDisplay(card), accessory=buy))
        self.add_item(container)

        nav: ui.ActionRow = ui.ActionRow()
        nav.add_item(_button("◀", self._move(-1), disabled=self.page == 0))
        nav.add_item(_button(f"Página {self.page + 1}/{self.pages}", self._noop, disabled=True))
        nav.add_item(_button("▶", self._move(1), disabled=self.page >= self.pages - 1))
        nav.add_item(_button("🎒 Mi mochila", self._backpack, style=discord.ButtonStyle.primary))
        self.add_item(nav)

    async def _noop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

    async def _redirect(self, interaction: discord.Interaction, kind: Kind | None) -> bool:
        """Si no es el dueño, le abre su propio escaparate. Devuelve si lo hizo."""
        if interaction.user.id == self.owner.id:
            return False
        own = Storefront(self.cog, self.guild, interaction.user)
        own.kind = kind
        await own.load()
        await interaction.response.send_message(view=own, ephemeral=True)
        own.interaction = interaction
        return True

    def _tab(self, kind: Kind | None) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction, kind):
                return
            self.kind, self.page = kind, 0
            await self.load()
            await interaction.response.edit_message(view=self)

        return callback

    def _move(self, step: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction, self.kind):
                return
            self.page = max(0, min(self.pages - 1, self.page + step))
            self.rebuild()
            await interaction.response.edit_message(view=self)

        return callback

    def _buy(self, item_id: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.open_checkout(interaction, item_id)

        return callback

    async def _backpack(self, interaction: discord.Interaction) -> None:
        view = await self.cog.backpack_view(self.guild, interaction.user, interaction.user)
        await interaction.response.send_message(
            view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )
        view.interaction = interaction

    async def on_timeout(self) -> None:
        """Apaga los botones al caducar."""
        await _close_view(self, self.message, self.interaction)


async def _close_view(
    view: ui.LayoutView, message: discord.Message | None, interaction: discord.Interaction | None
) -> None:
    """Desactiva los componentes de una vista caducada y la vuelve a pintar."""
    for child in view.walk_children():
        if isinstance(child, ui.Button | ui.Select):
            child.disabled = True
    try:
        if interaction is not None:
            await interaction.edit_original_response(view=view)
        elif message is not None:
            await message.edit(view=view)
    except discord.HTTPException:
        logger.debug("No se pudo cerrar una vista de la tienda", exc_info=True)


# -- Caja -----------------------------------------------------------------------------


class Checkout(ui.LayoutView):
    """La caja de un comprador: ticket, Pagar y Cancelar. Solo la ve él."""

    def __init__(
        self,
        cog: Tienda,
        guild: discord.Guild,
        buyer: discord.Member,
        item: ShopItem,
        price: Quote,
        *,
        balance: int,
        note: str | None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.buyer = buyer
        self.item = item
        self.price = price
        self.balance = balance
        self.note = note
        self.result: str | None = None
        self.failed = False
        self.done = False
        self._lock = asyncio.Lock()
        self.rebuild()

    def rebuild(self) -> None:
        """Pinta la caja, el resultado del pago o el error."""
        self.clear_items()
        item, price = self.item, self.price
        if self.done or self.failed:
            colour = COLOR_ERROR if self.failed else COLOR_PAID
            container = ui.Container(accent_colour=colour)
            container.add_item(ui.TextDisplay(self.result or ""))
            self.add_item(container)
            return
        container = ui.Container(accent_colour=COLOR)
        header = f"### 🧾 Caja · {item.emoji} {item.name}\n-# {kind_detail(item)}"
        container.add_item(ui.TextDisplay(header))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(ticket_preview(price)))
        lines = [igic_line(price)]
        if self.note:
            lines.append(self.note)
        missing = price.total - self.balance
        if missing > 0:
            lines.append(
                f"😬 Tienes {format_amount(self.balance)}: te faltan **{format_amount(missing)}**. "
                "Pásate por el casino o cobra el `imv`."
            )
        else:
            lines.append(
                f"{CURRENCY_EMOJI} Tienes {format_amount(self.balance)} → te quedan "
                f"**{format_amount(self.balance - price.total)}**"
            )
        container.add_item(ui.TextDisplay("\n".join(lines)))
        self.add_item(container)
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            _button(
                f"💳 Pagar {format_amount(price.total)}",
                self._pay,
                style=discord.ButtonStyle.success,
                disabled=missing > 0,
            )
        )
        row.add_item(_button("Cancelar", self._cancel))
        self.add_item(row)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """La caja es solo de quien la abrió (en la práctica ya es efímera)."""
        return interaction.user.id == self.buyer.id

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.failed, self.result = True, "Compra cancelada. Aquí no ha pasado nada, mi amor."
        self.rebuild()
        self.stop()
        await interaction.response.edit_message(view=self)

    async def _pay(self, interaction: discord.Interaction) -> None:
        async with self._lock:
            if self.done or self.failed:
                await interaction.response.defer()
                return
            await interaction.response.defer()
            paid = await self.cog.complete_purchase(self, interaction)
            self.rebuild()
            self.stop()
            await interaction.edit_original_response(
                view=self, allowed_mentions=discord.AllowedMentions.none()
            )
        if paid is not None:
            await self.cog.after_purchase(interaction, self, paid)


# -- Mochila --------------------------------------------------------------------------


class Backpack(ui.LayoutView):
    """La mochila de un miembro; su dueño puede ponerse y quitarse roles."""

    def __init__(
        self,
        cog: Tienda,
        guild: discord.Guild,
        member: discord.abc.User,
        viewer: discord.abc.User,
        entries: list[InventoryEntry],
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.viewer = viewer
        self.entries = entries
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None
        self.rebuild()

    def rebuild(self) -> None:
        self.clear_items()
        now = self.cog.clock()
        container = ui.Container(accent_colour=COLOR)
        container.add_item(ui.TextDisplay(f"# 🎒 Mochila de {_name(self.member)}"))
        container.add_item(ui.Separator())
        sections = []
        for kind in Kind:
            mine = [e for e in self.entries if e.kind is kind]
            if not mine:
                continue
            lines = (
                trophy_lines(mine)
                if kind is Kind.TROPHY
                else [entry_line(e, now) for e in sorted(mine, key=lambda e: e.starts_at)]
            )
            sections.append(f"### {kind.icon} {kind.title}\n" + "\n".join(lines))
        if not sections:
            sections.append("Vacía. Date una vuelta por la `tienda`, que hay cositas.")
        container.add_item(ui.TextDisplay("\n\n".join(sections)[:3800]))
        self.add_item(container)

        wardrobe = [e for e in self.entries if e.kind is Kind.ROLE and e.expires_at is None]
        if wardrobe and self.viewer.id == self.member.id:
            options = [
                discord.SelectOption(
                    label=(self._role_name(e) or e.name)[:100],
                    value=str(e.id),
                    description="Puesto: elígelo para quitártelo"
                    if e.equipped
                    else "Guardado: elígelo para ponértelo",
                )
                for e in wardrobe[:25]
            ]
            select: ui.Select = ui.Select(
                placeholder="👔 Ponerte o quitarte un rol", options=options
            )

            async def callback(interaction: discord.Interaction) -> None:
                await self.cog.toggle_role(interaction, self, int(select.values[0]))

            select.callback = callback  # type: ignore[method-assign]
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(select)
            self.add_item(row)

    def _role_name(self, entry: InventoryEntry) -> str | None:
        role = self.guild.get_role(entry.role_id or 0)
        return role.name if role else None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.member.id:
            return True
        await interaction.response.send_message(
            "Esa mochila no es tuya. Mira la tuya con `mochila`.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        await _close_view(self, self.message, self.interaction)


# -- Cog ------------------------------------------------------------------------------


class Tienda(commands.Cog):
    """El Colmado de Jovani: escaparate, caja, mochila y alquileres."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: ShopRepository,
        *,
        levels: MessageStatsRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.levels = levels
        self.clock = clock
        #: Potenciadores sin acabar por `(servidor, miembro)`: `(inicio, fin, %)`.
        self.boosts: dict[tuple[int, int], list[tuple[float, float, int]]] = defaultdict(list)

    async def cog_load(self) -> None:
        """Carga los potenciadores en curso y arranca la caducidad de alquileres."""
        # Idempotente: el bot ya lo hace en `setup_hook`, pero así el cog se
        # puede cargar solo (pruebas, recarga en caliente).
        await self.repository.initialize()
        for guild_id, user_id, start, end, pct in await self.repository.active_boosts(self.clock()):
            self.boosts[(guild_id, user_id)].append((start, end, pct))
        self._expire.start()

    async def cog_unload(self) -> None:
        """Para la tarea de caducidad."""
        self._expire.cancel()

    # -- Potenciadores ---------------------------------------------------------------

    def xp_multiplier(self, guild_id: int, user_id: int, now: float) -> float:
        """Multiplicador de XP de un miembro ahora mismo (1,0 sin potenciador)."""
        key = (guild_id, user_id)
        windows = self.boosts.get(key)
        if not windows:
            return 1.0
        windows[:] = [w for w in windows if w[1] > now]
        if not windows:
            del self.boosts[key]
            return 1.0
        return active_multiplier(windows, now)

    def queued_boosts(
        self, guild_id: int, user_id: int, now: float
    ) -> list[tuple[float, float, int]]:
        """Potenciadores del miembro que no han acabado, en orden."""
        return sorted(w for w in self.boosts.get((guild_id, user_id), []) if w[1] > now)

    # -- Comprobaciones --------------------------------------------------------------

    async def member_level(self, guild_id: int, user_id: int) -> int:
        """Nivel del miembro según su XP (0 si los niveles no están disponibles)."""
        if self.levels is None:
            return 0
        xp = await self.levels.member_xp(guild_id, user_id)
        return calculate_level_progress(xp).level

    @staticmethod
    def role_problem(guild: discord.Guild, role: discord.Role | None) -> str | None:
        """Por qué no se puede vender o dar `role`, o `None` si se puede."""
        if role is None:
            return "Ese rol ya no existe en el servidor."
        if role.is_default():
            return "@everyone no se vende, mi amor."
        if role.managed:
            return "Ese rol lo gestiona una integración (un bot o los boosts); no lo puedo dar."
        me = guild.me
        if not me.guild_permissions.manage_roles:
            return "Me falta el permiso **Gestionar roles**."
        if role >= me.top_role:
            return "Ese rol está por encima del mío: súbeme en la lista de roles del servidor."
        dangerous = [p for p in DANGEROUS_PERMISSIONS if getattr(role.permissions, p, False)]
        if dangerous:
            return (
                "Ese rol tiene permisos de moderación o administración "
                f"({', '.join(dangerous)}): no lo vendo, que se lía."
            )
        return None

    # -- Escaparate y mochila --------------------------------------------------------

    async def storefront(self, guild: discord.Guild, owner: discord.abc.User) -> Storefront:
        """Escaparate listo para enviar."""
        view = Storefront(self, guild, owner)
        await view.load()
        return view

    async def backpack_view(
        self, guild: discord.Guild, member: discord.abc.User, viewer: discord.abc.User
    ) -> Backpack:
        """Mochila de `member` vista por `viewer`."""
        entries = await self.repository.inventory(guild.id, member.id, self.clock())
        return Backpack(self, guild, member, viewer, entries)

    async def toggle_role(
        self, interaction: discord.Interaction, view: Backpack, entry_id: int
    ) -> None:
        """Pone o quita un rol comprado para siempre y repinta la mochila."""
        entry = next((e for e in view.entries if e.id == entry_id), None)
        member = interaction.user
        if entry is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "Ese rol ya no está en tu mochila.", ephemeral=True
            )
            return
        role = view.guild.get_role(entry.role_id or 0)
        if (problem := self.role_problem(view.guild, role)) is not None:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        assert role is not None  # role_problem lo comprueba
        equip = not entry.equipped
        try:
            if equip:
                await member.add_roles(role, reason="Se lo pone desde la mochila")
            else:
                await member.remove_roles(role, reason="Se lo quita desde la mochila")
        except discord.HTTPException:
            await interaction.response.send_message(
                "No he podido cambiarte el rol. Revisa mis permisos.", ephemeral=True
            )
            return
        await self.repository.set_equipped(view.guild.id, member.id, entry.id, equip)
        view.entries = await self.repository.inventory(view.guild.id, member.id, self.clock())
        view.rebuild()
        await interaction.response.edit_message(
            view=view, allowed_mentions=discord.AllowedMentions.none()
        )

    # -- Caja ------------------------------------------------------------------------

    async def open_checkout(self, interaction: discord.Interaction, item_id: int) -> None:
        """Abre la caja de un artículo para quien ha pulsado Comprar."""
        guild = interaction.guild
        buyer = interaction.user
        if guild is None or not isinstance(buyer, discord.Member):
            await interaction.response.send_message(
                "La tienda solo abre en un servidor.", ephemeral=True
            )
            return
        item = await self.repository.item(guild.id, item_id)
        if item is None or not item.visible:
            await interaction.response.send_message(
                "Ese artículo ya no está a la venta. Abre la `tienda` otra vez.", ephemeral=True
            )
            return
        now = self.clock()
        error, note = await self._precheck(guild, buyer, item, now)
        if error is not None:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        balance = await self.economy.balance(guild.id, buyer.id)
        view = Checkout(self, guild, buyer, item, quote(item, now), balance=balance, note=note)
        await interaction.response.send_message(
            view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    async def _precheck(
        self, guild: discord.Guild, buyer: discord.Member, item: ShopItem, now: float
    ) -> tuple[str | None, str | None]:
        """Lo que se puede saber antes de cobrar: `(error, nota para la caja)`.

        Las reglas de existencias y límites se vuelven a mirar al pagar,
        dentro de la transacción (`ShopRepository.reserve`).
        """
        note = None
        if item.kind is Kind.ROLE:
            role = guild.get_role(item.role_id or 0)
            if (problem := self.role_problem(guild, role)) is not None:
                return problem, None
            entries = await self.repository.inventory(guild.id, buyer.id, now)
            mine = [e for e in entries if e.role_id == item.role_id]
            rental = next((e for e in mine if e.expires_at is not None), None)
            if role in buyer.roles and not mine:
                return "Ya llevas ese rol, y no te lo vendió el colmado.", None
            if item.duration is not None and rental is not None:
                note = (
                    f"🔁 Ya lo tienes alquilado hasta <t:{int(rental.expires_at or now)}:f>: "
                    f"se suman {format_span(item.duration)}."
                )
        elif item.kind is Kind.BOOST:
            queue = self.queued_boosts(guild.id, buyer.id, now)
            if queue:
                note = (
                    f"⏳ Ya tienes {len(queue)} potenciador{'es' if len(queue) > 1 else ''}: "
                    f"este se pone a la cola y empieza <t:{int(queue[-1][1])}:R>."
                )
        level = await self.member_level(guild.id, buyer.id) if item.min_level else 0
        if item.min_level and level < item.min_level:
            return f"Hace falta nivel {item.min_level} y vas por el {level}. ¡A yapear!", None
        if item.sold_out:
            return "Agotado, mi amor. Llegaste tarde.", None
        return None, note

    async def complete_purchase(
        self, view: Checkout, interaction: discord.Interaction
    ) -> tuple[ShopItem, Sale, int] | None:
        """Cobra la compra de la caja y entrega el artículo.

        Deja en `view.result` el texto que verá el comprador. Si no se pudo
        dar el rol, devuelve el dinero.

        Returns:
            `(artículo, venta, saldo)` si se ha pagado, `None` si no.
        """
        guild, buyer = view.guild, view.buyer
        now = self.clock()
        level = await self.member_level(guild.id, buyer.id) if view.item.min_level else 0
        try:
            (item, sale), balance = await self.economy.purchase(
                guild.id,
                buyer.id,
                base=view.price.base,
                tax=view.price.tax,
                concept=view.item.kind.value,
                reserve=self.repository.reserve(
                    guild.id, buyer.id, view.item.id, expected=view.price, level=level, now=now
                ),
            )
        except ShopError as error:
            view.failed, view.result = True, f"❌ {error}"
            return None
        except InsufficientFundsError as error:
            view.failed = True
            view.result = (
                f"❌ ¡Ay, bendito! No te llega: tienes {format_amount(error.balance)} "
                f"y son {format_amount(view.price.total)}."
            )
            return None

        if item.kind is Kind.ROLE:
            role = guild.get_role(item.role_id or 0)
            problem = self.role_problem(guild, role)
            try:
                if problem is None and role is not None:
                    await buyer.add_roles(
                        role, reason=f"Compra en la tienda (ticket {sale.invoice})"
                    )
            except discord.HTTPException:
                logger.warning("No se pudo dar el rol %s comprado por %s", item.role_id, buyer.id)
                problem = "Discord no me dejó darte el rol."
            if problem is not None:
                try:
                    balance = await self.economy.refund_purchase(
                        guild.id,
                        buyer.id,
                        base=view.price.base,
                        tax=view.price.tax,
                        concept=item.kind.value,
                        release=self.repository.release(sale.purchase_id),
                    )
                except (InsufficientFundsError, ShopError):
                    # Solo pasa si el Estado ya se ha gastado ese IGIC en devoluciones.
                    logger.exception("No se pudo devolver la compra %s", sale.purchase_id)
                    view.failed = True
                    view.result = (
                        f"❌ {problem} Y encima no he podido devolverte el dinero: avisa a un "
                        f"admin con el ticket nº {sale.invoice}."
                    )
                    return None
                view.failed = True
                view.result = (
                    f"❌ {problem} Te he devuelto los {format_amount(view.price.total)} "
                    f"(IGIC incluido). Saldo: {format_amount(balance)}"
                )
                return None
        elif item.kind is Kind.BOOST and sale.expires_at is not None:
            self.boosts[(guild.id, buyer.id)].append(
                (sale.starts_at, sale.expires_at, item.multiplier or 100)
            )

        view.done = True
        view.result = await self._paid_text(item, view.price, sale, buyer, balance, now)
        return item, sale, balance

    async def _paid_text(
        self,
        item: ShopItem,
        price: Quote,
        sale: Sale,
        buyer: discord.Member,
        balance: int,
        now: float,
    ) -> str:
        """Lo que ve el comprador tras pagar: la factura y qué ha conseguido."""
        if item.kind is Kind.ROLE:
            got = (
                f"Ya llevas <@&{item.role_id}>"
                + ("" if sale.expires_at is None else f" hasta <t:{int(sale.expires_at)}:f>")
                + ("; se ha sumado a tu alquiler." if sale.renewed else ".")
            )
        elif item.kind is Kind.BOOST:
            boost = format_multiplier(item.multiplier or 100)
            got = (
                f"XP {boost} activo hasta <t:{int(sale.expires_at or now)}:t>."
                if sale.starts_at <= now
                else f"XP {boost} en cola: empieza <t:{int(sale.starts_at)}:R>."
            )
        else:
            got = f"{item.emoji} {item.name} ya está en tu mochila."
            if sale.serial is not None:
                got += f" Unidad nº {sale.serial} de {sale.edition}."
        lines = [
            "### ✅ ¡Wepa! Es tuyo",
            got,
            receipt(
                item,
                price,
                invoice=sale.invoice,
                when=now,
                buyer=buyer.display_name,
                serial=sale.serial,
                edition=sale.edition,
            ),
            igic_line(price),
            f"{CURRENCY_EMOJI} Saldo: **{format_amount(balance)}**",
        ]
        if hint := await renta.hint(self.bot, buyer.guild.id, buyer.id):
            lines.append(hint)
        return "\n".join(lines)

    async def after_purchase(
        self,
        interaction: discord.Interaction,
        view: Checkout,
        paid: tuple[ShopItem, Sale, int],
    ) -> None:
        """Aviso público, aviso de la Renta y logros, después de enseñar la factura."""
        item, sale, balance = paid
        line = random.choice(ANNOUNCE_LINES[item.kind]).format(
            who=f"**{_name(view.buyer)}**", item=f"{item.emoji} **{item.name}**"
        )
        extra = f" (nº {sale.serial} de {sale.edition})" if sale.serial is not None else ""
        try:
            await interaction.followup.send(
                f"🛍️ {line}{extra}\n-# Pagó {format_amount(view.price.total)}, de los que "
                f"{format_amount(view.price.tax)} son IGIC para {TAX_COLLECTOR}.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar una compra", exc_info=True)
        # Comprar gasta dinero: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)
        now = self.clock()
        await logros.track(
            self.bot,
            view.guild.id,
            view.buyer,
            interaction.channel,
            shop_stats(
                kind=item.kind.value,
                total=view.price.total,
                tax=view.price.tax,
                discount_pct=view.price.discount_pct,
                luxury=view.price.rate.key == "lujo",
                serial=sale.serial,
                last_unit=sale.last_unit,
                renewed=sale.renewed,
                collection=sale.collection,
                queued_boosts=len(self.queued_boosts(view.guild.id, view.buyer.id, now)),
                balance_after=balance,
            ),
        )

    # -- Caducidad -------------------------------------------------------------------

    @tasks.loop(minutes=EXPIRY_MINUTES)
    async def _expire(self) -> None:
        """Quita los roles alquilados que han vencido."""
        try:
            await self.expire_rentals()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error quitando roles alquilados vencidos")

    @_expire.before_loop
    async def _before_expire(self) -> None:
        await self.bot.wait_until_ready()

    async def expire_rentals(self) -> int:
        """Quita los alquileres vencidos y los marca. Devuelve cuántos ha cerrado.

        Si el miembro sigue teniendo el mismo rol por otra compra en vigor (uno
        para siempre puesto, u otro alquiler), no se le quita.
        """
        now = self.clock()
        due = await self.repository.due_rentals(now)
        for _entry_id, guild_id, user_id, role_id in due:
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None
            role = guild.get_role(role_id or 0) if guild else None
            if guild is None or member is None or role is None or role not in member.roles:
                continue
            others = await self.repository.inventory(guild_id, user_id, now)
            if any(
                e.role_id == role_id and (e.expires_at is not None or e.equipped) for e in others
            ):
                continue
            try:
                await member.remove_roles(role, reason="Alquiler de la tienda vencido")
            except discord.HTTPException:
                logger.info("No se pudo quitar el rol alquilado %s a %s", role_id, user_id)
        await self.repository.mark_expired([entry_id for entry_id, *_ in due])
        return len(due)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra la tienda del servidor que el bot abandona."""
        await self.repository.delete_guild_data(guild.id)
        for key in [k for k in self.boosts if k[0] == guild.id]:
            del self.boosts[key]

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(name="tienda", description="Abre El Colmado de Jovani: roles, XP y más.")
    @app_commands.guild_only()
    async def tienda(self, interaction: discord.Interaction) -> None:
        """Enseña el escaparate en el canal; comprar abre una caja privada."""
        assert interaction.guild is not None  # guild_only
        view = await self.storefront(interaction.guild, interaction.user)
        await interaction.response.send_message(
            view=view, allowed_mentions=discord.AllowedMentions.none()
        )
        view.interaction = interaction

    @commands.command(name="tienda")
    @commands.guild_only()
    async def tienda_text(self, ctx: commands.Context) -> None:
        """Versión de texto de `tienda`."""
        assert ctx.guild is not None  # guild_only
        view = await self.storefront(ctx.guild, ctx.author)
        view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="mochila", description="Lo que has comprado en la tienda.")
    @app_commands.describe(miembro="De quién (por defecto, la tuya).")
    @app_commands.guild_only()
    async def mochila(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Enseña la mochila; su dueño puede ponerse y quitarse roles."""
        assert interaction.guild is not None  # guild_only
        target = miembro or interaction.user
        view = await self.backpack_view(interaction.guild, target, interaction.user)
        await interaction.response.send_message(
            view=view, allowed_mentions=discord.AllowedMentions.none()
        )
        view.interaction = interaction

    @commands.command(name="mochila")
    @commands.guild_only()
    async def mochila_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto: `.mochila` o `.mochila @alguien`."""
        assert ctx.guild is not None  # guild_only
        target = miembro or ctx.author
        view = await self.backpack_view(ctx.guild, target, ctx.author)
        view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())

    # -- Trastienda (la abre el comando `catalogo` del cog Admin) ---------------------

    async def open_admin_panel(
        self,
        *,
        interaction: discord.Interaction | None = None,
        ctx: commands.Context | None = None,
    ) -> None:
        """Abre la trastienda para un administrador (efímera con `/`)."""
        from bot.cogs.shop_admin import AdminPanel  # evita el import circular

        guild = interaction.guild if interaction else ctx.guild if ctx else None
        owner = interaction.user if interaction else ctx.author if ctx else None
        if guild is None or owner is None:
            return
        panel = AdminPanel(self, guild, owner)
        await panel.load()
        if interaction is not None:
            await interaction.response.send_message(
                view=panel, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )
            panel.interaction = interaction
        elif ctx is not None:
            panel.message = await ctx.send(
                view=panel, allowed_mentions=discord.AllowedMentions.none()
            )


# -- Puntos de entrada para otros cogs ------------------------------------------------


def xp_multiplier(bot: commands.Bot, guild_id: int, user_id: int, now: float) -> float:
    """Multiplicador de XP por potenciadores; 1,0 si la tienda no está cargada."""
    if (cog := find_cog(bot, Tienda)) is not None:
        return cog.xp_multiplier(guild_id, user_id, now)
    return 1.0


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, la tienda y los niveles del bot."""
    await bot.add_cog(Tienda(bot, bot.economy, bot.shop, levels=bot.message_stats))
