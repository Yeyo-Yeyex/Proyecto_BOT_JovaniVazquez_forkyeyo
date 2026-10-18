"""Tienda: `tienda` y la mochila, la caja con IGIC y la caducidad de los alquileres.

- `tienda` abre el escaparate de El Colmado de Jovani: pestañas por sección
  (roles, potenciadores, coleccionables), cada artículo con su botón
  **Comprar**, rebajas tachadas, existencias que quedan y etiquetas de
  "nuevo" y "lo más vendido". Cualquiera puede comprar desde el escaparate de
  otro: la caja se abre solo para quien pulsa.
- La caja enseña el ticket antes de pagar (precio, rebaja, base, IGIC y
  total) y pide confirmar. Al pagar sale la factura simplificada, que solo ve
  el comprador, y un aviso público en el canal para presumir. Desde
  `BIG_PURCHASE` (100.000 Y$) el aviso lleva un pregón delante.
- La mochila (botón 🎒 del escaparate, o sección 🎒 Objetos de `perfil`)
  enseña lo que tiene alguien. Su dueño puede ponerse y
  quitarse los roles que compró para siempre (útil para los de color) y usar
  los objetos que se usan: tirarle un huevo a alguien, mandar un burofax,
  abrir una caja botín… (reglas en `bot.services.shop_uses`). El resultado
  sale en el canal; si es contra alguien, se le menciona. También elige qué
  título luce (o ninguno).
- **Títulos** (`CatalogEntry.title`): objetos que se lucen junto al nombre en
  `perfil`, `fortunas` y `ranking`. Comprar uno te lo pone; uno a la vez. Se
  guarda en la columna `equipped` de la mochila, como los roles, y el cog lo
  tiene en memoria (`titles`) para que esas listas no lean la base de datos.
  No salen en la caja botín ni en los regalos de las mascotas.
- **Cajas** (`bot.services.lootboxes`): cuatro por niveles y tres de temporada, que se
  abren desde la mochila con una animación de tres fotogramas sobre un mismo mensaje y
  dan piezas de colección o, muy de vez en cuando, un tapete. Las de temporada solo se
  venden en sus meses (`on_sale`). La caja botín vieja ya no se vende pero se abre.
  Una cada 100 jugadas del casino y una de regalo cada 5 niveles se ganan gratis
  (`grant_box`, que llaman `apuestas` y `message_stats`).
- Surtido de serie: la primera vez que se abre la tienda en un servidor se
  llena con los artículos de `bot.services.shop_catalog`, repartidos en
  pasillos que el escaparate deja filtrar. Desde ahí, los administradores los
  tocan con `catalogo` (cog Admin; la trastienda vive en
  `bot.cogs.shop_admin`) como cualquier otro artículo.

Dinero: cada compra pasa por `EconomyService.purchase` con su IGIC (tipo por
artículo, ver `bot.services.taxes.IGIC_RATES`), que va al Estado. Pagar llama
a `renta.remind` y la factura lleva la línea de la renta pendiente. Si el rol
comprado no se puede dar, se devuelve todo con `refund_purchase`.

Logros: categoría Tienda, con `achievements.track` al pagar (`shop_stats`),
al usar un objeto (`shop_use_stats`) y para quien lo recibe (`shop_hit_stats`).
Usar un objeto no mueve dinero, así que no lleva impuestos ni gancho de la
Renta: lo que tributa es la compra.

Otros cogs: `xp_multiplier(bot, …)` da el multiplicador de XP de los
potenciadores; lo usa `bot.cogs.message_stats` al dar XP por mensajes y voz.
Lo lee de una caché en memoria, sin tocar la base de datos en cada mensaje.
`owned_keys(bot, …)` da las claves de serie de lo que tiene un miembro; lo
usa `bot.cogs.work` al fichar para aplicar las herramientas de curro.
`title_of(bot, …)` da el título que luce un miembro, de memoria; lo usan
`perfil`, `fortunas` y `ranking`.

Permisos: **Gestionar roles** y que el rol del bot esté por encima de los
roles que se venden. Sin ellos, la tienda no deja ponerlos a la venta ni
cobrarlos.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import pets as mascotas
from bot.cogs import renta
from bot.repositories.shop import InventoryEntry, ShopRepository
from bot.services import skins as skins_registry
from bot.services.achievements import (
    lootbox_free_stats,
    lootbox_stats,
    shop_hit_stats,
    shop_skin_stats,
    shop_stats,
    shop_title_stats,
    shop_use_stats,
)
from bot.services.economy import (
    CURRENCY_EMOJI,
    EconomyService,
    InsufficientFundsError,
    format_amount,
)
from bot.services.levels import TIMEZONE, calculate_level_progress
from bot.services.lootboxes import (
    BOX_BY_KEY,
    COLLECTION_BY_KEY,
    FRAME_SECONDS,
    Rarity,
    box_of,
    free_box_line,
    glow_frame,
    is_piece,
    reveal_frame,
    roll,
    shake_frame,
)
from bot.services.lootboxes import in_season as box_in_season
from bot.services.pets import Event, Moment
from bot.services.pets_catalog import species_of
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
from bot.services.shop_catalog import (
    AISLE_BY_KEY,
    AISLES,
    CATALOG,
    HOUSE_AISLE,
    RETIRED_ITEMS,
    REVISED_PRICES,
    TITLE_KEYS,
    CatalogEntry,
    aisle_of,
    food_of,
    is_skin,
    is_title,
    use_of,
)
from bot.services.shop_uses import (
    MAX_NICK,
    Target,
    Use,
    UseResult,
    clean_shout,
    megaphone_text,
    mystery_text,
    nickname_text,
    pick_mystery,
    resolve,
)
from bot.services.skin_images import SkinImage
from bot.services.skins import (
    GAMES,
    SKIN_BY_KEY,
    Skin,
    fits,
    free_skins,
    in_season,
    rewards_for,
    skin_of_item,
    wearable_games,
)
from bot.services.skins import params as skin_params_of
from bot.services.skins import skins_of as game_skins
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.services.work_tools import TOOL_BY_KEY
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

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
        "{who} estrena {item}.",
        "{who} se ha comprado {item}. Ahora sí que brilla.",
        "{who} sale del colmado con {item} puesto. Mírala, mírala.",
        "{who} luce {item}. Yo soy yo, y así me tienes que soportar.",
        "{who} estrena {item} y ya no saluda igual.",
        "{who} se pone {item} y sale a pasearlo.",
    ),
    Kind.BOOST: (
        "{who} se mete un {item} en vena. A yapear se ha dicho.",
        "{who} pilla {item}: ojo, que viene con turbo.",
        "{who} compra {item}. El XP no se va a ganar solo.",
        "{who} se toma {item}. Más chute que un decreto ley.",
        "{who} activa {item}. Hazme café… ¡quiero café!",
    ),
    Kind.TROPHY: (
        "{who} se lleva {item} pa' la casa.",
        "{who} compra {item}. Pa' presumir, que pa' eso está.",
        "{who} añade {item} a la vitrina. Qué nivel.",
        "{who} pone {item} en la vitrina, al lado del Nobel de la Paz de Obama.",
        "{who} se lleva {item}. ¿Qué? ¿Qué? ¡Toma mangó!",
    ),
    Kind.PET: (
        "{who} sale del colmado con {item} en brazos. Familia nueva.",
        "{who} adopta {item}. Ya tiene a quien contarle sus pérdidas.",
        "{who} se lleva {item} a casa. Que alguien avise a la protectora: ha ido bien.",
        "{who} adopta {item}. Lagartijo, iguana, lagarto: la familia crece.",
        "{who} adopta {item}, el primero de la familia que no le pide un Bizum.",
    ),
}
#: Aviso de compra de un objeto que se usa (un huevo, un burofax…): suena a amenaza.
ANNOUNCE_USABLE: tuple[str, ...] = (
    "{who} sale del colmado con {item} en la mano. Alguien va a cobrar. 👀",
    "{who} se compra {item}. Yo que tú no le daba la espalda.",
    "{who} pilla {item}. Esto no va a acabar bien.",
)

#: Aviso de compra de un título: se estrena puesto.
ANNOUNCE_TITLE: tuple[str, ...] = (
    "Desde hoy, {who} luce {item}. Tratamiento de usted, por favor.",
    "{who} se compra {item} y ya no saluda igual.",
    "{who} estrena {item}. Que conste en el BOE.",
)
#: Desde cuánto (IGIC incluido) una compra se anuncia a bombo y platillo.
BIG_PURCHASE = 100_000
#: Aviso de una compra de las gordas, delante del aviso normal.
ANNOUNCE_BIG: tuple[str, ...] = (
    "📣 ¡Atención, servidor! Ha entrado dinero de verdad en el colmado.",
    "📣 Se paran las máquinas del casino: alguien acaba de tirar la casa por la ventana.",
    "📣 Jovani cierra la caja y sale a la puerta a mirar: compra gorda.",
)

#: Pestañas del escaparate: clave, etiqueta y si un artículo entra en ella.
TABS: tuple[tuple[str, str], ...] = (
    ("todo", "🛍️ Todo"),
    ("rol", "🎭 Roles"),
    ("xp", "⚡ XP"),
    ("mascota", "🐾 Mascotas"),
    ("objeto", "💎 Vitrina"),
    ("uso", "🫳 Para usar"),
    ("skin", "🎨 Skins"),
)
#: Valor del desplegable de títulos para quitárselo.
NO_TITLE = "-"
#: Botones por fila de pestañas (Discord admite 5 por fila).
TABS_PER_ROW = 5


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


def _button(
    label: str,
    callback: Callable[[discord.Interaction], Awaitable[None]],
    *,
    custom_id: str,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    disabled: bool = False,
) -> ui.Button:
    """Botón con su callback ya enganchado y su `custom_id` fijo."""
    button: ui.Button = ui.Button(label=label, style=style, disabled=disabled, custom_id=custom_id)
    button.callback = callback  # type: ignore[method-assign]
    return button


# -- Textos ---------------------------------------------------------------------------


def local_now(epoch: float) -> datetime:
    """La hora de Canarias en `epoch`: la que manda en las temporadas."""
    return datetime.fromtimestamp(epoch, TIMEZONE)


def season_name(item: ShopItem) -> str | None:
    """La temporada en la que se vende un artículo, o `None` si se vende todo el año."""
    skin = skin_of_item(item.catalog_key)
    if skin is not None:
        return skin.season.name if skin.season else None
    box = box_of(item.catalog_key)
    return box.season.name if box is not None and box.season else None


def on_sale(item: ShopItem, when: datetime) -> bool:
    """Si un artículo se vende ahora: las skins y las cajas de temporada, solo en sus meses."""
    skin = skin_of_item(item.catalog_key)
    if skin is not None:
        return in_season(skin, when)
    box = box_of(item.catalog_key)
    return box is None or box_in_season(box, when)


def kind_detail(item: ShopItem) -> str:
    """Qué es el artículo, en pocas palabras."""
    if item.kind is Kind.ROLE:
        span = "para siempre" if item.duration is None else f"durante {format_span(item.duration)}"
        return f"Rol <@&{item.role_id}> {span}"
    if item.kind is Kind.BOOST:
        return (
            f"XP {format_multiplier(item.multiplier or 100)} durante {format_span(item.duration)}"
        )
    if item.kind is Kind.PET:
        species = species_of(item.catalog_key)
        return f"🐾 Mascota {species.rarity.lower()}" if species else "🐾 Mascota"
    if (use := use_of(item.catalog_key)) is not None:
        return use.summary
    if (tool := TOOL_BY_KEY.get(item.catalog_key or "")) is not None:
        return f"🛠️ Herramienta de `pala`: {tool.effect}"
    if is_title(item.catalog_key):
        return "🎖️ Título: se luce junto a tu nombre"
    if (skin := skin_of_item(item.catalog_key)) is not None:
        game = GAMES[skin.game]
        season = f" · 🗓️ de {skin.season.name}" if skin.season else ""
        return f"🎨 Skin de {game.label}: cambia el {game.thing}{season}"
    return "Coleccionable"


def in_tab(item: ShopItem, tab: str) -> bool:
    """Si un artículo sale en una pestaña del escaparate."""
    usable = use_of(item.catalog_key) is not None
    skin = is_skin(item.catalog_key)
    if tab == "uso":
        return usable
    if tab == "skin":
        return skin
    if tab == "objeto":
        return item.kind is Kind.TROPHY and not usable and not skin
    return tab == "todo" or item.kind.value == tab


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


def owned_tag(item: ShopItem, owned: int) -> str | None:
    """La etiqueta de lo que ya tiene quien mira el escaparate, o `None` si no tiene.

    Con el límite por persona cumplido lo dice, para no ir a la caja para nada.
    """
    if owned <= 0:
        return None
    if item.per_user is not None and owned >= item.per_user:
        return "✅ Ya lo tienes" if item.per_user == 1 else f"✅ Tienes {owned}, el máximo"
    return "✅ Lo tienes" if owned == 1 else f"✅ Tienes {owned}"


def item_card(item: ShopItem, now: float, *, best_seller: bool = False, owned: int = 0) -> str:
    """Texto de un artículo en el escaparate.

    Args:
        owned: Cuántos tiene en vigor quien mira (en su mochila), para verlo de un vistazo.
    """
    lines = [f"### {item.emoji} {item.name}", price_text(item, now)]
    if item.description:
        lines.append(item.description)
    tags = [aisle_of(item.catalog_key).label, kind_detail(item)]
    if tag := owned_tag(item, owned):
        tags.insert(0, f"**{tag}**")
    if food_of(item.catalog_key):
        tags.append("🍽️ Se come (para tu mascota)")
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


def group_entries(entries: list[InventoryEntry]) -> list[list[InventoryEntry]]:
    """Entradas de la mochila agrupadas por artículo, en el orden en que llegan."""
    groups: dict[int, list[InventoryEntry]] = defaultdict(list)
    for entry in entries:
        groups[entry.item_id].append(entry)
    return list(groups.values())


def trophy_lines(entries: list[InventoryEntry]) -> list[str]:
    """Objetos agrupados por artículo, con sus números de serie y cómo se usan."""
    lines = []
    for group in group_entries(entries):
        first = group[0]
        text = f"{first.emoji} **{first.name}**"
        if len(group) > 1:
            text += f" ×{len(group)}"
        serials = sorted(e.serial for e in group if e.serial is not None)
        if serials and first.edition:
            numbers = ", ".join(str(s) for s in serials)
            text += f" · nº {numbers} de {first.edition}"
        if (use := use_of(first.catalog_key)) is not None:
            text += f" · *{use.verb.lower()}*"
        if is_title(first.catalog_key):
            text += " · 🎖️ *lo luces*" if any(e.equipped for e in group) else " · 🎖️ título"
        lines.append(text)
    return lines


def skin_lines(entries: list[InventoryEntry], worn: Mapping[str, str] | None = None) -> list[str]:
    """Las skins de la mochila por grupo y, con `worn` (mesa → clave), lo que lleva cada mesa.

    Un tapete vale en todas las mesas con fieltro y cada una lleva el suyo.
    """
    owned = [skin for e in entries if (skin := skin_of_item(e.catalog_key)) is not None]
    lines = []
    for group in GAMES.values():
        mine = [s for s in owned if s.game == group.key]
        if mine:
            lines.append(f"{group.label}: " + " · ".join(f"**{s.label}**" for s in mine))
    if owned and worn is not None:
        on = [
            f"{g.emoji} {SKIN_BY_KEY[worn[g.key]].name if g.key in worn else 'de serie'}"
            for g in GAMES.values()
            if g.wearable and any(fits(s, g.key) for s in owned)
        ]
        lines.append("-# Puestas: " + " · ".join(on))
    return lines


def _usable(entry: InventoryEntry) -> bool:
    return entry.kind is Kind.TROPHY and use_of(entry.catalog_key) is not None


def _clip(text: str, limit: int) -> str:
    """Recorta por líneas enteras para no partir un emoji o un formato a la mitad."""
    if len(text) <= limit:
        return text
    kept: list[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > limit - 20:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept) + "\n-# …y más cosas."


def _select_emoji(emoji: str) -> discord.PartialEmoji | str | None:
    """Emoji válido para una opción de desplegable (los de bandera y los raros fallan)."""
    try:
        return discord.PartialEmoji.from_str(emoji)
    except (TypeError, ValueError):
        return None


# -- Escaparate -----------------------------------------------------------------------


class Storefront(ui.LayoutView):
    """El escaparate: pestañas, pasillos, artículos con su botón Comprar y páginas.

    Las pestañas, el pasillo y las páginas son del dueño; si otro los toca, se
    le abre su propio escaparate. **Comprar** lo puede pulsar cualquiera.
    """

    def __init__(self, cog: Tienda, guild: discord.Guild, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.tab = "todo"
        #: Pasillo elegido (`None`: todos).
        self.aisle: str | None = None
        self.page = 0
        self.items_all: list[ShopItem] = []
        self.best_seller: int | None = None
        self.balance = 0
        #: Cuántos tiene el dueño de cada artículo (por id), para marcarlos.
        self.owned: Counter[int] = Counter()
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None

    async def load(self) -> None:
        """Lee el catálogo, el más vendido, el saldo del dueño y lo que ya tiene."""
        self.items_all = await self.cog.repository.items(self.guild.id)
        self.best_seller = await self.cog.repository.best_seller(self.guild.id)
        self.balance = await self.cog.economy.balance(self.guild.id, self.owner.id)
        entries = await self.cog.repository.inventory(
            self.guild.id, self.owner.id, self.cog.clock()
        )
        self.owned = Counter(entry.item_id for entry in entries)
        self.rebuild()

    @property
    def shown(self) -> list[ShopItem]:
        """Artículos de la pestaña y el pasillo elegidos."""
        when = local_now(self.cog.clock())
        return [
            i
            for i in self.items_all
            if in_tab(i, self.tab)
            and (self.aisle is None or aisle_of(i.catalog_key).key == self.aisle)
            and on_sale(i, when)
        ]

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.shown) // PAGE_SIZE))

    def rebuild(self) -> None:
        """Monta los componentes con el estado actual."""
        self.clear_items()
        now = self.cog.clock()
        self.page = min(self.page, self.pages - 1)
        container = ui.Container(accent_colour=COLOR)
        where = f" · {AISLE_BY_KEY[self.aisle].label}" if self.aisle else ""
        container.add_item(
            ui.TextDisplay(
                f"# 🛒 {SHOP_NAME}{where}\n"
                f"¡Bienvenido, {_name(self.owner)}! Aquí se gasta lo que te dan el casino "
                f"y el IMV. "
                f"Los precios van sin IGIC: {TAX_COLLECTOR} cobra en caja.\n"
                f"-# {CURRENCY_EMOJI} Saldo de {_name(self.owner)}: {format_amount(self.balance)}"
            )
        )
        # Solo las pestañas con algo (los roles no vienen de serie); si no caben
        # en una fila, siguen en la de abajo.
        shown_tabs = []
        for key, label in TABS:
            count = sum(1 for i in self.items_all if in_tab(i, key))
            if count or key in ("todo", self.tab):
                shown_tabs.append((key, label, count))
        for start in range(0, len(shown_tabs), TABS_PER_ROW):
            tabs: ui.ActionRow = ui.ActionRow()
            for key, label, count in shown_tabs[start : start + TABS_PER_ROW]:
                style = (
                    discord.ButtonStyle.primary
                    if key == self.tab
                    else discord.ButtonStyle.secondary
                )
                tabs.add_item(
                    _button(
                        f"{label} · {count}",
                        self._tab(key),
                        custom_id=f"tienda:tab:{key}",
                        style=style,
                    )
                )
            container.add_item(tabs)
        if (aisles := self._aisle_select()) is not None:
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(aisles)
            container.add_item(row)
        container.add_item(ui.Separator())

        page_items = self.shown[self.page * PAGE_SIZE : (self.page + 1) * PAGE_SIZE]
        if not page_items:
            container.add_item(
                ui.TextDisplay(
                    "El colmado está vacío. Un admin tiene que llenarlo con `catalogo`."
                    if not self.items_all
                    else "No hay nada por aquí. Prueba otro pasillo u otra pestaña."
                )
            )
        for item in page_items:
            if item.sold_out:
                buy = _button(
                    "Agotado", self._noop, custom_id=f"tienda:agotado:{item.id}", disabled=True
                )
            else:
                buy = _button(
                    f"Comprar · {money(quote(item, now).base)}",
                    self._buy(item.id),
                    custom_id=f"tienda:comprar:{item.id}",
                    style=discord.ButtonStyle.success,
                )
            card = item_card(
                item, now, best_seller=item.id == self.best_seller, owned=self.owned[item.id]
            )
            container.add_item(ui.Section(ui.TextDisplay(card), accessory=buy))
        self.add_item(container)

        nav: ui.ActionRow = ui.ActionRow()
        nav.add_item(
            _button(
                "◀",
                self._move(-1),
                custom_id="tienda:pagina:anterior",
                disabled=self.page == 0,
            )
        )
        nav.add_item(
            _button(
                f"Página {self.page + 1}/{self.pages}",
                self._noop,
                custom_id="tienda:pagina",
                disabled=True,
            )
        )
        nav.add_item(
            _button(
                "▶",
                self._move(1),
                custom_id="tienda:pagina:siguiente",
                disabled=self.page >= self.pages - 1,
            )
        )
        nav.add_item(
            _button(
                "🎒 Mi mochila",
                self._backpack,
                custom_id="tienda:mochila",
                style=discord.ButtonStyle.primary,
            )
        )
        self.add_item(nav)

    def _aisle_select(self) -> ui.Select | None:
        """Desplegable de pasillos, con los que tienen algo en la pestaña elegida."""
        counts: dict[str, int] = defaultdict(int)
        for item in self.items_all:
            if in_tab(item, self.tab):
                counts[aisle_of(item.catalog_key).key] += 1
        present = [a for a in (*AISLES, HOUSE_AISLE) if counts.get(a.key)]
        if len(present) < 2 and self.aisle is None:
            return None
        options = [
            discord.SelectOption(
                label="Todos los pasillos",
                value="*",
                emoji="🧭",
                default=self.aisle is None,
            )
        ]
        options += [
            discord.SelectOption(
                label=f"{a.name} · {counts[a.key]}",
                value=a.key,
                emoji=a.emoji,
                default=a.key == self.aisle,
            )
            for a in present[:24]
        ]
        select: ui.Select = ui.Select(
            placeholder="🧭 Elige pasillo", options=options, custom_id="tienda:pasillo"
        )

        async def callback(interaction: discord.Interaction) -> None:
            value = select.values[0]
            aisle = None if value == "*" else value
            if await self._redirect(interaction, self.tab, aisle):
                return
            self.aisle, self.page = aisle, 0
            self.rebuild()
            await interaction.response.edit_message(view=self)

        select.callback = callback  # type: ignore[method-assign]
        return select

    async def _noop(self, interaction: discord.Interaction) -> None:
        await ack(interaction)

    async def _redirect(
        self, interaction: discord.Interaction, tab: str, aisle: str | None
    ) -> bool:
        """Si no es el dueño, le abre su propio escaparate. Devuelve si lo hizo."""
        if interaction.user.id == self.owner.id:
            return False
        own = Storefront(self.cog, self.guild, interaction.user)
        own.tab, own.aisle = tab, aisle
        # Cargar el escaparate va a la base de datos: se acepta el clic antes.
        await ack(interaction, new_message=True)
        await own.load()
        await edit(interaction, view=own)
        own.interaction = interaction
        return True

    def _tab(self, tab: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction, tab, self.aisle):
                return
            await ack(interaction)
            self.tab, self.page = tab, 0
            await self.load()
            await edit(interaction, view=self)

        return callback

    def _move(self, step: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction, self.tab, self.aisle):
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
        await ack(interaction, new_message=True)
        view = await self.cog.backpack_view(self.guild, interaction.user, interaction.user)
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
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
        #: Si se abrió desde el 🎨 de una mesa, la mesa donde se pone la skin al pagar.
        self.skin_game: str | None = None
        self.result: str | None = None
        self.failed = False
        self.done = False
        self._lock = TimedLock("shop:Checkout._lock")
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
                custom_id="caja:pagar",
                style=discord.ButtonStyle.success,
                disabled=missing > 0,
            )
        )
        row.add_item(_button("Cancelar", self._cancel, custom_id="caja:cancelar"))
        self.add_item(row)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """La caja es solo de quien la abrió (en la práctica ya es efímera)."""
        return interaction.user.id == self.buyer.id

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.failed, self.result = True, "Compra cancelada. Aquí no ha pasado nada."
        self.rebuild()
        self.stop()
        await interaction.response.edit_message(view=self)

    async def _pay(self, interaction: discord.Interaction) -> None:
        # El ack va antes del candado: un doble clic no espera a la compra del primero.
        await ack(interaction)
        async with self._lock:
            if self.done or self.failed:
                return
            paid = await self.cog.complete_purchase(self, interaction)
            self.rebuild()
            self.stop()
            await interaction.edit_original_response(
                view=self, allowed_mentions=discord.AllowedMentions.none()
            )
        if paid is not None:
            await self.cog.after_purchase(interaction, self, paid)


# -- Mochila --------------------------------------------------------------------------


def backpack_text(
    entries: list[InventoryEntry], now: float, worn: Mapping[str, str] | None = None
) -> str:
    """Lo que hay en una mochila, por tipos, en texto (mochila y `perfil`).

    `worn`: la skin que lleva cada mesa (`Tienda.worn_skins`).
    """
    sections = []
    for kind in Kind:
        mine = [
            e for e in entries if e.kind is kind and not _usable(e) and not is_skin(e.catalog_key)
        ]
        if not mine:
            continue
        lines = (
            trophy_lines(mine)
            if kind in (Kind.TROPHY, Kind.PET)
            else [entry_line(e, now) for e in sorted(mine, key=lambda e: e.starts_at)]
        )
        if kind is Kind.PET:
            lines.append("-# Cuídalas y elige cuál va contigo con `mascota`.")
        sections.append(f"### {kind.icon} {kind.title}\n" + "\n".join(lines))
    usable = [e for e in entries if _usable(e)]
    if usable:
        sections.append("### 🫳 Para usar\n" + "\n".join(trophy_lines(usable)))
    if skins := skin_lines(entries, worn):
        sections.append("### 🎨 Skins\n" + "\n".join(skins))
    if not sections:
        sections.append("Vacía. Date una vuelta por la `tienda`, que hay cositas.")
    return _clip("\n\n".join(sections), 3800)


def skin_options(
    entries: list[InventoryEntry], worn: Mapping[str, str], game: str
) -> list[discord.SelectOption]:
    """Opciones del selector 🎨 de una mesa: la de serie y las suyas que valen aquí.

    El valor es `juego:clave` (`ruleta:` para la de serie); `worn` es lo que
    lleva cada mesa (`Tienda.worn_skins`).
    """
    g = GAMES[game]
    mine = [s for e in entries if (s := skin_of_item(e.catalog_key)) and fits(s, game)]
    options = [
        discord.SelectOption(
            label=f"{g.thing.capitalize()} de serie"[:100],
            value=f"{game}:",
            emoji=_select_emoji(g.emoji),
            default=game not in worn,
        )
    ]
    options += [
        discord.SelectOption(
            label=s.name[:100],
            value=f"{game}:{s.key}",
            emoji=_select_emoji(s.emoji),
            default=worn.get(game) == s.key,
        )
        for s in {s.key: s for s in mine}.values()
    ]
    return options[:25]


def skin_game_options(
    entries: list[InventoryEntry], worn: Mapping[str, str]
) -> list[discord.SelectOption]:
    """Opciones de la mochila: una por mesa donde tiene alguna skin (abre su selector)."""
    owned = [s for e in entries if (s := skin_of_item(e.catalog_key)) is not None]
    options = []
    for g in GAMES.values():
        if not g.wearable or not any(fits(s, g.key) for s in owned):
            continue
        now = SKIN_BY_KEY[worn[g.key]].name if g.key in worn else f"{g.thing} de serie"
        options.append(
            discord.SelectOption(
                label=f"{g.name}: {now}"[:100], value=g.key, emoji=_select_emoji(g.emoji)
            )
        )
    return options[:25]


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
        worn = self.cog.worn_skins(self.guild.id, self.member.id)
        container.add_item(ui.TextDisplay(backpack_text(self.entries, now, worn)))
        self.add_item(container)
        usable = [e for e in self.entries if _usable(e)]

        if usable and self.viewer.id == self.member.id:
            groups = group_entries(usable)
            use_options = []
            for group in groups[:25]:
                first = group[0]
                use = use_of(first.catalog_key)
                assert use is not None  # _usable lo garantiza
                count = f" ×{len(group)}" if len(group) > 1 else ""
                use_options.append(
                    discord.SelectOption(
                        label=f"{first.name}{count}"[:100],
                        value=str(first.item_id),
                        emoji=_select_emoji(first.emoji),
                        description=use.summary.removeprefix("🫳 ")[:100],
                    )
                )
            use_select: ui.Select = ui.Select(
                placeholder="🫳 Usar algo", options=use_options, custom_id="mochila:usar"
            )

            async def use_callback(interaction: discord.Interaction) -> None:
                await self.cog.start_use(interaction, self, int(use_select.values[0]))

            use_select.callback = use_callback  # type: ignore[method-assign]
            use_row: ui.ActionRow = ui.ActionRow()
            use_row.add_item(use_select)
            self.add_item(use_row)

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
                placeholder="👔 Ponerte o quitarte un rol", options=options, custom_id="mochila:rol"
            )

            async def callback(interaction: discord.Interaction) -> None:
                await self.cog.toggle_role(interaction, self, int(select.values[0]))

            select.callback = callback  # type: ignore[method-assign]
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(select)
            self.add_item(row)

        titles = [e for e in self.entries if is_title(e.catalog_key)]
        if titles and self.viewer.id == self.member.id:
            # Uno a la vez; si hubiera dos marcados, vale el más reciente.
            worn = max((e.id for e in titles if e.equipped), default=None)
            title_options = [
                discord.SelectOption(
                    label=e.name[:100],
                    value=e.catalog_key or "",
                    emoji=_select_emoji(e.emoji),
                    description="Lo luces ahora" if e.id == worn else "Elígelo para lucirlo",
                    default=e.id == worn,
                )
                for e in titles[:24]
            ]
            title_options.append(
                discord.SelectOption(label="Sin título", value=NO_TITLE, emoji="🚫")
            )
            title_select: ui.Select = ui.Select(
                placeholder="🎖️ Qué título luces",
                options=title_options,
                custom_id="mochila:titulo",
            )

            async def title_callback(interaction: discord.Interaction) -> None:
                await self.cog.choose_title(interaction, self, title_select.values[0])

            title_select.callback = title_callback  # type: ignore[method-assign]
            title_row: ui.ActionRow = ui.ActionRow()
            title_row.add_item(title_select)
            self.add_item(title_row)

        if self.viewer.id == self.member.id and (options := skin_game_options(self.entries, worn)):
            skin_select: ui.Select = ui.Select(
                placeholder="🎨 Cambiar la skin de una mesa",
                options=options,
                custom_id="mochila:skin",
            )

            async def skin_callback(interaction: discord.Interaction) -> None:
                await self.cog.open_skin_picker(interaction, skin_select.values[0])

            skin_select.callback = skin_callback  # type: ignore[method-assign]
            skin_row: ui.ActionRow = ui.ActionRow()
            skin_row.add_item(skin_select)
            self.add_item(skin_row)

    def _role_name(self, entry: InventoryEntry) -> str | None:
        role = self.guild.get_role(entry.role_id or 0)
        return role.name if role else None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.member.id:
            return True
        await interaction.response.send_message(
            "Esa mochila no es tuya. Mira la tuya en `perfil`, sección 🎒 Objetos.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        await _close_view(self, self.message, self.interaction)


# -- Usar objetos ---------------------------------------------------------------------


class TargetPicker(ui.View):
    """Desplegable de miembros para usar un objeto contra alguien. Solo lo ve quien lo usa."""

    def __init__(self, cog: Tienda, backpack: Backpack, entry: InventoryEntry, use: Use) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.backpack = backpack
        self.entry = entry
        self.use = use
        self.done = False
        select: ui.UserSelect = ui.UserSelect(
            placeholder=f"{entry.emoji} {use.verb}: ¿a quién?", min_values=1, max_values=1
        )
        select.callback = self._picked  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.backpack.member.id

    async def _picked(self, interaction: discord.Interaction) -> None:
        if self.done:
            await ack(interaction)
            return
        self.done = True
        self.stop()
        target = self.select.values[0]
        await self.cog.perform_use(interaction, self.backpack, self.entry, self.use, target=target)


class UseTextForm(ui.Modal):
    """Formulario del megáfono y del DNI falso."""

    def __init__(self, cog: Tienda, backpack: Backpack, entry: InventoryEntry, use: Use) -> None:
        nickname = use.special == "nickname"
        super().__init__(title=f"{use.verb}"[:45], timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.backpack = backpack
        self.entry = entry
        self.use = use
        self.text: ui.TextInput = ui.TextInput(
            placeholder="Tu nuevo apodo" if nickname else "Lo que quieres gritar",
            max_length=MAX_NICK if nickname else 150,
            required=True,
        )
        label = "Nombre del DNI" if nickname else "Qué gritas por el megáfono"
        self.add_item(ui.Label(text=label, component=self.text))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.perform_use(
            interaction, self.backpack, self.entry, self.use, text=self.text.value
        )


class SkinPicker(ui.View):
    """Selector privado de skin de una mesa (botón 🎨 o mochila).

    Las opciones `buy:<artículo>` son las que se venden y aún no tiene: abren la
    caja, y al pagar se la pone en esta mesa.
    """

    def __init__(self, cog: Tienda, options: list[discord.SelectOption], game: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        select: ui.Select = ui.Select(placeholder="🎨 Elige", options=options)

        async def callback(interaction: discord.Interaction) -> None:
            self.stop()
            value = select.values[0]
            if value.startswith("buy:"):
                await self.cog.open_checkout(
                    interaction, int(value.removeprefix("buy:")), skin_game=game
                )
                return
            await self.cog.choose_skin(interaction, value)

        select.callback = callback  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)


def skin_button(bot: commands.Bot, game: str, *, row: int | None = None) -> ui.Button | None:
    """El botón 🎨 para poner en una mesa de `game`, o `None` si no tiene skins.

    El juego lo añade a su vista; el clic abre el selector privado
    (`Tienda.open_skin_picker`). Sin la tienda cargada, no hay botón.
    """
    if game not in GAMES or not game_skins(game) or find_cog(bot, Tienda) is None:
        return None
    button: ui.Button = ui.Button(
        emoji="🎨", style=discord.ButtonStyle.secondary, row=row, custom_id=f"{game}:skin"
    )

    async def callback(interaction: discord.Interaction) -> None:
        if (cog := find_cog(bot, Tienda)) is None:
            await notify(interaction, "La tienda está cerrada ahora mismo.")
            return
        await cog.open_skin_picker(interaction, game)

    button.callback = callback  # type: ignore[method-assign]
    return button


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
        catalog: Sequence[CatalogEntry] = CATALOG,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.levels = levels
        self.clock = clock
        #: Surtido de serie que se mete en cada servidor (las pruebas pasan `()`).
        self.catalog = tuple(catalog)
        self.rng = rng or random.Random()
        #: Potenciadores sin acabar por `(servidor, miembro)`: `(inicio, fin, %)`.
        self.boosts: dict[tuple[int, int], list[tuple[float, float, int]]] = defaultdict(list)
        #: Servidores cuyo surtido ya se ha mirado desde que arrancó el bot.
        self._stocked: set[int] = set()
        #: Último uso de cada objeto que no se gasta: `(servidor, miembro, uso)` → epoch.
        self.last_used: dict[tuple[int, int, str], float] = {}
        #: Título que luce cada `(servidor, miembro)`, ya con su emoji. En memoria
        #: para que `fortunas` y `ranking` no lean la base de datos por cada nombre.
        self.titles: dict[tuple[int, int], str] = {}
        #: Skin que lleva cada `(servidor, miembro, juego)`: su clave de `skins`.
        #: Sin entrada, la de serie. En memoria para no leer la base de datos al
        #: pintar cada tirada.
        self.skins: dict[tuple[int, int, str], str] = {}
        #: Imagen de cada skin propia por `(servidor, miembro, skin)` (`Skin.custom`).
        #: En memoria por lo mismo; solo la tienen quienes compraron una y la
        #: subieron, y cada una pesa decenas de KB (`skin_images.MAX_SIDE`).
        self.skin_images: dict[tuple[int, int, str], SkinImage] = {}
        #: Segundos entre fotogramas al abrir una caja (las pruebas lo ponen a 0).
        self.lootbox_pause = FRAME_SECONDS
        #: Avisos de cajas ganadas jugando que aún no ha visto su dueño: salen en el
        #: siguiente resultado con dinero (`notices_for`, que llama `renta.hint`).
        self.notices: dict[tuple[int, int], list[str]] = defaultdict(list)

    async def cog_load(self) -> None:
        """Carga potenciadores, títulos y skins puestos, y arranca la caducidad."""
        # Idempotente: el bot ya lo hace en `setup_hook`, pero así el cog se
        # puede cargar solo (pruebas, recarga en caliente).
        await self.repository.initialize()
        for guild_id, user_id, start, end, pct in await self.repository.active_boosts(self.clock()):
            self.boosts[(guild_id, user_id)].append((start, end, pct))
        for guild_id, user_id, _key, emoji, name in await self.repository.worn_among(TITLE_KEYS):
            self.titles[(guild_id, user_id)] = f"{emoji} {name}"
        for guild_id, user_id, game, key in await self.repository.worn_skins():
            if (skin := skin_of_item(key)) is not None and fits(skin, game):
                self.skins[(guild_id, user_id, game)] = skin.key
        for guild_id, user_id, skin_key, data in await self.repository.skin_images():
            self.skin_images[(guild_id, user_id, skin_key)] = SkinImage.of(data)
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

    async def owned_keys(self, guild_id: int, user_id: int) -> frozenset[str]:
        """Claves de serie de lo que tiene el miembro en vigor (para `pala`)."""
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        return frozenset(e.catalog_key for e in entries if e.catalog_key)

    async def consume_key(self, guild_id: int, user_id: int, catalog_key: str) -> bool:
        """Gasta una unidad del objeto de serie `catalog_key` (los regalos de `pala`).

        `False` si no le queda ninguna.
        """
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        ids = sorted(e.id for e in entries if e.catalog_key == catalog_key)
        return bool(ids) and await self.repository.consume(guild_id, user_id, ids[0])

    def queued_boosts(
        self, guild_id: int, user_id: int, now: float
    ) -> list[tuple[float, float, int]]:
        """Potenciadores del miembro que no han acabado, en orden."""
        return sorted(w for w in self.boosts.get((guild_id, user_id), []) if w[1] > now)

    # -- Títulos ---------------------------------------------------------------------

    def title(self, guild_id: int, user_id: int) -> str | None:
        """Título que luce un miembro (`🏰 Marquesado de Mogán`), o `None`."""
        return self.titles.get((guild_id, user_id))

    async def wear_title(self, guild_id: int, user_id: int, catalog_key: str | None) -> None:
        """Le pone a un miembro el título `catalog_key` (o se lo quita con `None`)."""
        await self.repository.wear_one(guild_id, user_id, catalog_key, TITLE_KEYS)
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        worn = next((e for e in entries if e.catalog_key == catalog_key), None)
        if catalog_key is None or worn is None:
            self.titles.pop((guild_id, user_id), None)
        else:
            self.titles[(guild_id, user_id)] = f"{worn.emoji} {worn.name}"

    async def choose_title(
        self, interaction: discord.Interaction, view: Backpack, value: str
    ) -> None:
        """Cambia el título que luce el dueño de la mochila y la repinta."""
        member = interaction.user
        key = None if value == NO_TITLE else value
        before = self.title(view.guild.id, member.id)
        # Guardarlo va a la base de datos: se acepta el clic antes.
        await ack(interaction)
        await self.wear_title(view.guild.id, member.id, key)
        view.entries = await self.repository.inventory(view.guild.id, member.id, self.clock())
        view.rebuild()
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
        after = self.title(view.guild.id, member.id)
        if after != before:
            await logros.track(
                self.bot,
                view.guild.id,
                member,
                interaction.channel,
                shop_title_stats(worn=after is not None),
            )

    async def refresh_worn(self, guild_id: int, user_id: int) -> None:
        """Vuelve a leer el título y las skins de un miembro tras perder un objeto.

        Lo usa el Rastro (`bot.cogs.rastro`) cuando algo sale de la mochila (a un
        puesto o a otra persona): si lo llevaba puesto, deja de lucirlo.
        """
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        owned = {e.catalog_key for e in entries if e.catalog_key}
        worn = next((e for e in entries if e.catalog_key in TITLE_KEYS and e.equipped), None)
        if worn is None:
            self.titles.pop((guild_id, user_id), None)
        else:
            self.titles[(guild_id, user_id)] = f"{worn.emoji} {worn.name}"
        for key, skin_key in list(self.skins.items()):
            skin = SKIN_BY_KEY.get(skin_key)
            if key[:2] == (guild_id, user_id) and (skin is None or skin.catalog_key not in owned):
                del self.skins[key]

    # -- Skins -----------------------------------------------------------------------

    def skin(self, guild_id: int, user_id: int, game: str) -> Skin | None:
        """La skin que lleva un miembro en `game`, o `None` (la de serie)."""
        key = self.skins.get((guild_id, user_id, game))
        return SKIN_BY_KEY.get(key) if key else None

    def scene_params(self, guild_id: int, user_id: int, game: str) -> dict[str, object]:
        """Lo que pinta la escena de `game` para este miembro (con su imagen, si es propia)."""
        skin = self.skin(guild_id, user_id, game)
        image = self.skin_image(guild_id, user_id, skin.key) if skin and skin.custom else None
        return skin_params_of(game, skin, image)

    # -- Imágenes propias (`Skin.custom`; las sube `pieles`) -------------------------

    def skin_image(self, guild_id: int, user_id: int, skin_key: str) -> SkinImage | None:
        """La imagen que ha subido un miembro para su skin propia `skin_key`, o `None`."""
        return self.skin_images.get((guild_id, user_id, skin_key))

    async def owns_skin(self, guild_id: int, user_id: int, skin: Skin) -> bool:
        """Si la skin está en la mochila del miembro."""
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        return any(e.catalog_key == skin.catalog_key for e in entries)

    async def set_skin_image(
        self, guild_id: int, user_id: int, skin: Skin, image: SkinImage, *, set_by: int
    ) -> None:
        """Guarda la imagen de la skin propia de un miembro; se ve desde la próxima jugada."""
        await self.repository.set_skin_image(
            guild_id, user_id, skin.key, image.data, set_by=set_by, now=self.clock()
        )
        self.skin_images[(guild_id, user_id, skin.key)] = image

    async def remove_skin_image(self, guild_id: int, user_id: int, skin: Skin) -> bool:
        """Borra la imagen de la skin propia de un miembro; `False` si no tenía."""
        removed = await self.repository.delete_skin_image(guild_id, user_id, skin.key)
        self.skin_images.pop((guild_id, user_id, skin.key), None)
        return removed

    def wear_on_purchase(
        self, guild_id: int, user_id: int, skin: Skin, game: str | None
    ) -> list[str]:
        """Dónde se pone una skin al comprarla.

        En la mesa desde la que se compra (`game`); si no, en su juego, o, si es
        un tapete, en las mesas con fieltro que llevaban el de serie.
        """
        if game is not None and fits(skin, game):
            return [game]
        if GAMES[skin.game].wearable:
            return [skin.game]
        worn = self.worn_skins(guild_id, user_id)
        return [g.key for g in wearable_games(skin) if g.key not in worn]

    def worn_skins(self, guild_id: int, user_id: int) -> dict[str, str]:
        """Lo que lleva un miembro en cada mesa: juego → clave de la skin (sin las de serie)."""
        return {
            g: k for (gid, uid, g), k in self.skins.items() if (gid, uid) == (guild_id, user_id)
        }

    async def wear_skin(self, guild_id: int, user_id: int, game: str, skin: Skin | None) -> None:
        """Le pone a un miembro la skin `skin` en la mesa `game` (la de serie con `None`)."""
        await self.repository.wear_skin(guild_id, user_id, game, skin.catalog_key if skin else None)
        if skin is None:
            self.skins.pop((guild_id, user_id, game), None)
        else:
            self.skins[(guild_id, user_id, game)] = skin.key

    async def sync_reward_skins(
        self, guild_id: int, user_id: int, achievement_ids: list[str] | set[str]
    ) -> list[Skin]:
        """Mete en la mochila las skins que regalan estos logros y le faltan.

        Se llama al desbloquear un logro y al abrir la mochila (así llegan
        también las skins nuevas de logros que ya se tenían). Devuelve las
        que ha dado.
        """
        return await self._grant_skins(guild_id, user_id, rewards_for(set(achievement_ids)))

    async def grant_free_skins(self, guild_id: int, user_id: int, game: str) -> list[Skin]:
        """Mete en la mochila las skins gratis de `game` que le falten (`Skin.free`).

        Se llama al abrir el 🎨 de ese juego y desde el propio juego, no al abrir
        la mochila: así no se llena la de quien nunca ha jugado.
        """
        return await self._grant_skins(guild_id, user_id, free_skins(game))

    async def _grant_skins(
        self, guild_id: int, user_id: int, wanted: tuple[Skin, ...]
    ) -> list[Skin]:
        """Da las skins de `wanted` que no tenga; devuelve las que ha dado."""
        if not wanted:
            return []
        now = self.clock()
        owned = {e.catalog_key for e in await self.repository.inventory(guild_id, user_id, now)}
        given = []
        for skin in wanted:
            if skin.catalog_key in owned:
                continue
            await self.stock_up(guild_id)
            item = await self.repository.item_by_key(guild_id, skin.catalog_key)
            if item is None:
                continue
            await self.repository.grant(guild_id, user_id, item, now)
            given.append(skin)
        return given

    async def claim_reward_skins(self, guild_id: int, user_id: int) -> None:
        """Da las skins de logros que ya tenía y le faltan (al abrir la mochila o el 🎨).

        Si ninguna skin se gana con logros, ni mira los logros: abrir la mochila
        no paga una lectura de más.
        """
        if any(skin.achievement for skin in skins_registry.SKINS):
            await self.sync_reward_skins(guild_id, user_id, await self._unlocked(guild_id, user_id))

    async def _unlocked(self, guild_id: int, user_id: int) -> set[str]:
        """Logros que tiene un miembro, para las skins que se ganan con ellos."""
        from bot.cogs.achievements import Achievements  # evita el import circular

        cog = find_cog(self.bot, Achievements)
        if cog is None:
            return set()
        return set((await cog.fresh_profile(guild_id, user_id)).unlocked)

    async def choose_skin(
        self,
        interaction: discord.Interaction,
        value: str,
        *,
        backpack: Backpack | None = None,
    ) -> None:
        """Cambia la skin de un juego (`juego:clave`; `juego:` para la de serie).

        Desde la mochila la repinta; desde el botón 🎨 de una mesa contesta en
        el mensaje privado del selector.
        """
        member = interaction.user
        guild = interaction.guild
        game, _sep, key = value.partition(":")
        skin = SKIN_BY_KEY.get(key) if key else None
        if guild is None or game not in GAMES or (skin is not None and not fits(skin, game)):
            await notify(interaction, "Esa skin ya no existe.")
            return
        # Comprobar la mochila y guardarlo van a la base de datos: se acepta antes.
        await ack(interaction)
        if skin is not None:
            owned = await self.repository.inventory(guild.id, member.id, self.clock())
            if not any(e.catalog_key == skin.catalog_key for e in owned):
                await notify(interaction, "Esa skin no está en tu mochila.")
                return
        before = self.skin(guild.id, member.id, game)
        await self.wear_skin(guild.id, member.id, game, skin)
        label = skin.label if skin else f"{GAMES[game].thing} de serie"
        if backpack is not None:
            backpack.entries = await self.repository.inventory(guild.id, member.id, self.clock())
            backpack.rebuild()
            await edit(interaction, view=backpack, allowed_mentions=discord.AllowedMentions.none())
        else:
            await edit(
                interaction,
                content=f"🎨 Listo: en {GAMES[game].label} verás **{label}** en la próxima jugada.",
                view=None,
            )
        if before != skin:
            await logros.track(
                self.bot, guild.id, member, interaction.channel, shop_skin_stats(swapped=True)
            )

    async def open_skin_picker(self, interaction: discord.Interaction, game: str) -> None:
        """El botón 🎨 de una mesa: elige tu skin de `game` en un mensaje privado."""
        guild = interaction.guild
        if guild is None or game not in GAMES:
            await notify(interaction, "Aquí no hay skins que elegir.")
            return
        # Leer la mochila va a la base de datos: «pensando…» privado mientras tanto.
        await ack(interaction, new_message=True)
        member = interaction.user
        await self.claim_reward_skins(guild.id, member.id)
        await self.grant_free_skins(guild.id, member.id, game)
        now = self.clock()
        entries = await self.repository.inventory(guild.id, member.id, now)
        options = skin_options(entries, self.worn_skins(guild.id, member.id), game)
        options += await self._skin_offers(guild.id, entries, game, now, 25 - len(options))
        g = GAMES[game]
        if len(options) == 1:
            await edit(
                interaction,
                content=(
                    f"🎨 No hay ningún {g.thing} para {g.label} ahora mismo. Mira la pestaña "
                    "🎨 Skins de la `tienda`."
                ),
            )
            return
        picker = SkinPicker(self, options, game)
        await edit(
            interaction,
            content=f"🎨 ¿Qué {g.thing} quieres en {g.label}? Los 🛒 se compran aquí mismo.",
            view=picker,
        )

    async def _skin_offers(
        self, guild_id: int, entries: list[InventoryEntry], game: str, now: float, room: int
    ) -> list[discord.SelectOption]:
        """Las skins de `game` que se venden ahora y no tiene, como opciones `buy:<artículo>`."""
        if room <= 0:
            return []
        await self.stock_up(guild_id)
        owned = {e.catalog_key for e in entries}
        offers = []
        for skin in game_skins(game):
            if (
                not skin.for_sale
                or skin.catalog_key in owned
                or not in_season(skin, local_now(now))
            ):
                continue
            item = await self.repository.item_by_key(guild_id, skin.catalog_key)
            if item is None or not item.visible:
                continue
            level = f" · nivel {skin.min_level}" if skin.min_level else ""
            offers.append(
                discord.SelectOption(
                    label=f"🛒 {skin.name} · {format_amount(quote(item, now).total)}"[:100],
                    value=f"buy:{item.id}",
                    emoji=_select_emoji(skin.emoji),
                    description=f"Comprar y ponerlo en {GAMES[game].name}{level}"[:100],
                )
            )
        return offers[:room]

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
            return "@everyone no se vende."
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

    async def stock_up(self, guild_id: int) -> int:
        """Mete en el servidor el surtido de serie que le falte (una vez por arranque).

        Returns:
            Cuántos artículos se han añadido.
        """
        if guild_id in self._stocked or not self.catalog:
            return 0
        added = await self.repository.stock_catalog(
            guild_id, self.catalog, self.clock(), revised=REVISED_PRICES, retired=RETIRED_ITEMS
        )
        self._stocked.add(guild_id)
        if added:
            logger.info("Surtido de serie: %s artículos nuevos en %s", added, guild_id)
        return added

    async def restock(self, guild_id: int) -> int:
        """Vuelve a meter los artículos de serie que se hayan retirado."""
        added = await self.repository.stock_catalog(
            guild_id,
            self.catalog,
            self.clock(),
            restock=True,
            revised=REVISED_PRICES,
            retired=RETIRED_ITEMS,
        )
        self._stocked.add(guild_id)
        return added

    async def storefront(self, guild: discord.Guild, owner: discord.abc.User) -> Storefront:
        """Escaparate listo para enviar."""
        await self.stock_up(guild.id)
        view = Storefront(self, guild, owner)
        await view.load()
        return view

    async def backpack_embed(self, guild: discord.Guild, member: discord.abc.User) -> discord.Embed:
        """Sección 🎒 Objetos de `perfil`: la mochila en texto, sin botones."""
        entries = await self.repository.inventory(guild.id, member.id, self.clock())
        embed = discord.Embed(
            title=f"🎒 Mochila de {_name(member)}",
            description=backpack_text(entries, self.clock(), self.worn_skins(guild.id, member.id)),
            color=COLOR,
        )
        embed.set_footer(text="Para usar objetos o ponerte roles, abre la mochila.")
        return embed

    async def item_count(self, guild_id: int, user_id: int) -> int:
        """Cuántas cosas en vigor tiene alguien en la mochila."""
        return len(await self.repository.inventory(guild_id, user_id, self.clock()))

    async def backpack_view(
        self, guild: discord.Guild, member: discord.abc.User, viewer: discord.abc.User
    ) -> Backpack:
        """Mochila de `member` vista por `viewer`.

        Antes de leerla, le da las skins de logros que le falten (las de logros
        que ya tenía cuando la skin llegó al bot).
        """
        if viewer.id == member.id:
            await self.claim_reward_skins(guild.id, member.id)
        entries = await self.repository.inventory(guild.id, member.id, self.clock())
        return Backpack(self, guild, member, viewer, entries)

    async def toggle_role(
        self, interaction: discord.Interaction, view: Backpack, entry_id: int
    ) -> None:
        """Pone o quita un rol comprado para siempre y repinta la mochila."""
        entry = next((e for e in view.entries if e.id == entry_id), None)
        member = interaction.user
        if entry is None or not isinstance(member, discord.Member):
            await notify(interaction, "Ese rol ya no está en tu mochila.")
            return
        role = view.guild.get_role(entry.role_id or 0)
        if (problem := self.role_problem(view.guild, role)) is not None:
            await notify(interaction, problem)
            return
        assert role is not None  # role_problem lo comprueba
        # Cambiar el rol en Discord y guardarlo tardan: se acepta el clic antes.
        await ack(interaction)
        equip = not entry.equipped
        try:
            if equip:
                await member.add_roles(role, reason="Se lo pone desde la mochila")
            else:
                await member.remove_roles(role, reason="Se lo quita desde la mochila")
        except discord.HTTPException:
            await notify(interaction, "No he podido cambiarte el rol. Revisa mis permisos.")
            return
        await self.repository.set_equipped(view.guild.id, member.id, entry.id, equip)
        view.entries = await self.repository.inventory(view.guild.id, member.id, self.clock())
        view.rebuild()
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())

    # -- Usar objetos ----------------------------------------------------------------

    def cooldown_left(self, guild_id: int, user_id: int, use: Use, now: float) -> int:
        """Segundos que faltan para poder volver a usar un objeto que no se gasta."""
        if use.consumes or not use.cooldown:
            return 0
        last = self.last_used.get((guild_id, user_id, use.key))
        if last is None:
            return 0
        return max(0, int(last + use.cooldown - now + 0.999))

    async def start_use(
        self, interaction: discord.Interaction, backpack: Backpack, item_id: int
    ) -> None:
        """Primer paso de usar un objeto desde la mochila.

        Si el uso pide a alguien, abre el desplegable de miembros; si pide
        texto, el formulario; si no, lo usa ya.
        """
        entries = [e for e in backpack.entries if e.item_id == item_id and _usable(e)]
        if not entries:
            await interaction.response.send_message("Eso ya no está en tu mochila.", ephemeral=True)
            return
        entry = min(entries, key=lambda e: e.id)
        use = use_of(entry.catalog_key)
        assert use is not None  # _usable lo garantiza
        wait = self.cooldown_left(backpack.guild.id, backpack.member.id, use, self.clock())
        if wait:
            await interaction.response.send_message(
                f"⏳ Espera {wait} s antes de volver a usar {entry.emoji} {entry.name}. "
                "Que los vecinos también descansan.",
                ephemeral=True,
            )
            return
        if use.target is Target.MEMBER:
            picker = TargetPicker(self, backpack, entry, use)
            await interaction.response.send_message(
                f"{entry.emoji} **{entry.name}**: elige a quién.", view=picker, ephemeral=True
            )
            return
        if use.target is Target.TEXT:
            await interaction.response.send_modal(UseTextForm(self, backpack, entry, use))
            return
        await self.perform_use(interaction, backpack, entry, use)

    async def perform_use(
        self,
        interaction: discord.Interaction,
        backpack: Backpack,
        entry: InventoryEntry,
        use: Use,
        *,
        target: discord.abc.User | None = None,
        text: str | None = None,
    ) -> None:
        """Usa un objeto: lo gasta si toca, publica el resultado y apunta los logros.

        Ningún uso mueve dinero, así que no hay impuestos ni gancho de la Renta
        (Biblia, sección 4): lo que tributó fue la compra.
        """
        guild, member = backpack.guild, interaction.user
        # Gastar el objeto y sortear premios van a la base de datos: se acepta antes.
        await ack(interaction)
        now = self.clock()
        shout = ""
        if self.cooldown_left(guild.id, member.id, use, now):
            await notify(interaction, "⏳ Todavía no. Respira.")
            return

        if use.special == "megaphone":
            try:
                shout = discord.utils.escape_mentions(
                    discord.utils.escape_markdown(clean_shout(text or ""))
                )
            except ValueError as error:
                await notify(interaction, f"❌ {error}")
                return
        if use.special == "nickname":
            problem = self._nickname_problem(guild, member, text)
            if problem is not None:
                await notify(interaction, f"❌ {problem}")
                return

        if use.consumes and not await self.repository.consume(guild.id, member.id, entry.id):
            await notify(
                interaction,
                "Eso ya lo has gastado. Mira tu mochila en `perfil`.",
                ephemeral=True,
            )
            return

        who = f"**{_name(member)}**"
        item_label = f"{entry.emoji} {entry.name}"
        prize: ShopItem | None = None
        if use.special == "lootbox":
            await self._perform_lootbox(interaction, backpack, entry, who, now)
            return
        if use.special == "mystery":
            outcome = await self._open_mystery(guild.id, member.id, who, entry, now)
            if outcome is None:
                await self.repository.restore(entry.id)
                await notify(
                    interaction,
                    "La caja está vacía: no hay coleccionables que sortear. Te la devuelvo.",
                )
                return
            result, prize = outcome
        elif use.special == "megaphone":
            result = megaphone_text(who, shout)
        elif use.special == "nickname":
            assert isinstance(member, discord.Member)
            new = " ".join((text or "").split())[:MAX_NICK]
            old = member.display_name
            try:
                await member.edit(nick=new, reason="DNI falso de la tienda")
            except discord.HTTPException:
                await self.repository.restore(entry.id)
                await notify(
                    interaction,
                    "❌ Discord no me deja cambiarte el apodo. Te devuelvo el DNI.",
                )
                return
            result = nickname_text(who, discord.utils.escape_markdown(old), new)
        else:
            mention = target.mention if target is not None else None
            result = resolve(
                use,
                who=who,
                item=item_label,
                rng=self.rng,
                now=now,
                target=mention,
                target_is_self=target is not None and target.id == member.id,
                target_is_bot=target is not None and target.bot,
            )

        victim = (
            target if target is not None and not target.bot and target.id != member.id else None
        )
        mentions = (
            discord.AllowedMentions(users=[victim], everyone=False, roles=False)
            if victim is not None
            else discord.AllowedMentions.none()
        )
        text = result.text
        if pet := await mascotas.cameo(self.bot, guild.id, member.id, Moment(Event.USE)):
            text += f"\n{pet}"
        await notify(interaction, text, ephemeral=False, allowed_mentions=mentions)
        if not use.consumes:
            self.last_used[(guild.id, member.id, use.key)] = now
        await self._refresh_backpack(backpack)

        collection = await self.repository.collection_size(guild.id, member.id)
        await logros.track(
            self.bot,
            guild.id,
            member,
            interaction.channel,
            shop_use_stats(
                use=use.key,
                flags=result.flags,
                consumed=use.consumes,
                targeted=victim is not None,
                at_self=target is not None and target.id == member.id,
                at_bot=target is not None and target.bot,
                prize_price=prize.price if prize is not None else None,
                collection=collection,
                when=now,
            ),
        )
        if victim is not None:
            await logros.track(
                self.bot, guild.id, victim, interaction.channel, shop_hit_stats(use=use.key)
            )

    async def _perform_lootbox(
        self,
        interaction: discord.Interaction,
        backpack: Backpack,
        entry: InventoryEntry,
        who: str,
        now: float,
    ) -> None:
        """Abre una caja de `lootboxes` (la unidad ya está gastada y el clic aceptado).

        Sortea (`lootboxes.roll`), mete la pieza o el tapete en la mochila sin cobrar
        (`ShopRepository.grant`) y lo enseña con una animación de tres fotogramas sobre un
        mismo mensaje (agitar la caja, el color de la rareza, la pieza), de ~1,5 s en total.
        Todo lo que toca la base de datos va antes del primer fotograma. Abrir no mueve
        dinero (Biblia, «Usar un objeto no mueve dinero»): lo que tributó fue la compra, con
        IGIC general. Si no hay nada que dar (el servidor ha retirado todas las piezas), la
        caja vuelve a la mochila.
        """
        guild, member = backpack.guild, interaction.user
        box = box_of(entry.catalog_key)
        if box is None:
            await self.repository.restore(entry.id)
            await notify(interaction, "Esa caja no sé abrirla. Te la devuelvo.")
            return
        # La caja ya está gastada: si la base de datos falla antes de dar la pieza, se
        # devuelve entera en vez de perderse.
        try:
            await self.stock_up(guild.id)
            mine = await self.repository.inventory(guild.id, member.id, now)
            owned = {e.catalog_key for e in mine if e.catalog_key}
            items = {
                i.catalog_key: i
                for i in await self.repository.items(guild.id, include_hidden=True)
                if i.catalog_key
            }
            # Una pieza se da aunque esté oculta (siempre lo está); un tapete solo si se
            # sigue vendiendo y sin existencias limitadas.
            have = {k for k, i in items.items() if is_piece(k) or (i.visible and i.stock is None)}
            drop = roll(box, self.rng, owned=owned, have=have)
            if drop is None:
                await self.repository.restore(entry.id)
                await notify(
                    interaction,
                    "La caja está vacía: no hay piezas que sortear. Te la devuelvo.",
                )
                return
            _given, dupe = await self.repository.grant(
                guild.id, member.id, items[drop.catalog_key], now
            )
        except Exception:
            logger.exception("No se pudo abrir una caja de %s; se devuelve", member.id)
            await self.repository.restore(entry.id)
            await notify(interaction, "La caja se ha atascado. Te la devuelvo entera.")
            return
        held = owned | {drop.catalog_key}
        collection = COLLECTION_BY_KEY[drop.piece.collection] if drop.piece else None
        have_in_collection = (
            sum(1 for p in collection.pieces if p.key in held) if collection is not None else 0
        )
        last = reveal_frame(
            who,
            box,
            drop,
            dupe=dupe,
            owned=have_in_collection,
            rng=self.rng,
            price_text=format_amount(drop.price),
        )
        # Las piezas buenas y los tapetes son un momento sonado: la mascota sale siempre.
        notable = drop.skin is not None or drop.rarity >= Rarity.LEGENDARY
        moment = Moment(Event.LOOTBOX, notable)
        if pet := await mascotas.cameo(self.bot, guild.id, member.id, moment):
            last += f"\n{pet}"
        await self._animate(
            interaction, [shake_frame(who, box, self.rng), glow_frame(who, box, drop), last]
        )
        await self._refresh_backpack(backpack)

        collection_size = await self.repository.collection_size(guild.id, member.id)
        await logros.track(
            self.bot,
            guild.id,
            member,
            interaction.channel,
            shop_use_stats(
                use="lootbox",
                flags=(),
                consumed=True,
                targeted=False,
                at_self=False,
                at_bot=False,
                prize_price=None,
                collection=collection_size,
                when=now,
            ),
        )
        await logros.track(
            self.bot,
            guild.id,
            member,
            interaction.channel,
            lootbox_stats(
                box=box.key,
                rarity=drop.rarity.stat,
                tapete=drop.skin is not None,
                dupe=dupe,
                seasonal=box.season is not None,
                collection=collection.key if collection is not None else None,
                owned=have_in_collection,
                when=now,
            ),
        )

    async def _animate(self, interaction: discord.Interaction, frames: list[str]) -> None:
        """Manda el primer fotograma al canal y lo va editando con los demás.

        Si una edición falla (mensaje borrado, límite de Discord) se salta el resto y el
        último fotograma, que es el resultado, sale como mensaje nuevo: nunca se pierde.
        """
        mentions = discord.AllowedMentions.none()
        try:
            message = await interaction.followup.send(
                frames[0], ephemeral=False, wait=True, allowed_mentions=mentions
            )
        except discord.HTTPException:
            # Sin el primer fotograma no hay animación: el resultado sale solo, en privado
            # si tampoco deja en público. La pieza ya está en la mochila.
            logger.warning("No se pudo empezar la apertura de una caja", exc_info=True)
            try:
                await notify(interaction, frames[-1], allowed_mentions=mentions)
            except discord.HTTPException:
                logger.warning("Ni siquiera se pudo enseñar la caja abierta", exc_info=True)
            return
        try:
            for frame in frames[1:]:
                await asyncio.sleep(self.lootbox_pause)
                await message.edit(content=frame, allowed_mentions=mentions)
        except (discord.HTTPException, AttributeError):
            logger.warning("No se pudo animar la apertura de una caja", exc_info=True)
            await notify(interaction, frames[-1], ephemeral=False, allowed_mentions=mentions)

    async def grant_free_box(
        self, guild_id: int, user_id: int, box_key: str, *, count: int = 1, because: str
    ) -> str | None:
        """Mete en la mochila `count` cajas ganadas jugando, sin cobrar, y devuelve el aviso.

        Es un regalo del colmado, como `ShopRepository.grant`: no mueve dinero (Biblia,
        sección 4), así que no hay ni IGIC ni gancho de la Renta. Cuenta para los logros
        de cajas gratis.

        Args:
            because: Por qué se gana, para el aviso («tus 100 jugadas»).

        Returns:
            La línea del aviso, o `None` si el servidor no tiene la caja.
        """
        box = BOX_BY_KEY[box_key]
        await self.stock_up(guild_id)
        item = await self.repository.item_by_key(guild_id, box.key)
        if item is None or count < 1:
            return None
        now = self.clock()
        for _ in range(count):
            await self.repository.grant(guild_id, user_id, item, now)
        logros.note(self.bot, guild_id, user_id, lootbox_free_stats(count))
        return free_box_line(box, because=because, count=count)

    def notices_for(self, guild_id: int, user_id: int) -> str | None:
        """Los avisos de cajas ganadas que aún no ha visto (se sirven una sola vez)."""
        lines = self.notices.pop((guild_id, user_id), None)
        return "\n".join(lines) if lines else None

    async def _open_mystery(
        self, guild_id: int, user_id: int, who: str, box: InventoryEntry, now: float
    ) -> tuple[UseResult, ShopItem] | None:
        """Sortea y entrega el premio de una caja botín, o `None` si no hay premios.

        Premios: coleccionables visibles, sin uso y de existencias ilimitadas,
        para no gastar unidades numeradas de nadie. Los títulos no tocan: se
        lucen porque se han pagado.
        """
        candidates = [
            item
            for item in await self.repository.items(guild_id)
            if item.kind is Kind.TROPHY
            and item.stock is None
            and item.id != box.item_id
            and use_of(item.catalog_key) is None
            and not is_title(item.catalog_key)
            and not is_skin(item.catalog_key)
            and not is_piece(item.catalog_key)
        ]
        if not candidates:
            return None
        prize = candidates[pick_mystery([c.price for c in candidates], self.rng)]
        _entry, dupe = await self.repository.grant(guild_id, user_id, prize, now)
        label = f"{prize.emoji} **{prize.name}** ({format_amount(prize.price)})"
        return mystery_text(who, label, price=prize.price, dupe=dupe), prize

    @staticmethod
    def _nickname_problem(
        guild: discord.Guild, member: discord.abc.User, text: str | None
    ) -> str | None:
        """Por qué no se puede cambiar el apodo con el DNI falso, o `None`."""
        new = " ".join((text or "").split())
        if not new:
            return "El DNI necesita un nombre."
        if len(new) > MAX_NICK:
            return f"Discord no deja apodos de más de {MAX_NICK} caracteres."
        if not isinstance(member, discord.Member):
            return "Esto solo funciona en un servidor."
        me = guild.me
        if not me.guild_permissions.manage_nicknames:
            return "Me falta el permiso **Gestionar apodos**. El DNI sigue en tu mochila."
        if member.id == guild.owner_id or member.top_role >= me.top_role:
            return (
                "Tu rol está por encima del mío y no te puedo cambiar el apodo. "
                "El DNI sigue en tu mochila."
            )
        return None

    async def _refresh_backpack(self, backpack: Backpack) -> None:
        """Vuelve a pintar la mochila tras usar algo (si se puede; si no, da igual)."""
        backpack.entries = await self.repository.inventory(
            backpack.guild.id, backpack.member.id, self.clock()
        )
        backpack.rebuild()
        try:
            if backpack.interaction is not None:
                await backpack.interaction.edit_original_response(
                    view=backpack, allowed_mentions=discord.AllowedMentions.none()
                )
            elif backpack.message is not None:
                await backpack.message.edit(
                    view=backpack, allowed_mentions=discord.AllowedMentions.none()
                )
        except discord.HTTPException:
            logger.debug("No se pudo repintar la mochila", exc_info=True)

    # -- Caja ------------------------------------------------------------------------

    async def open_checkout(
        self, interaction: discord.Interaction, item_id: int, *, skin_game: str | None = None
    ) -> None:
        """Abre la caja de un artículo para quien ha pulsado Comprar.

        `skin_game`: la mesa desde cuyo 🎨 se compra una skin, para ponérsela ahí.
        """
        guild = interaction.guild
        buyer = interaction.user
        if guild is None or not isinstance(buyer, discord.Member):
            await notify(interaction, "La tienda solo abre en un servidor.")
            return
        # La caja lee artículo, mochila, nivel y saldo: se acepta el clic antes.
        # Los errores rellenan el «pensando…» privado en vez de dejarlo colgado.
        await ack(interaction, new_message=True)
        item = await self.repository.item(guild.id, item_id)
        if item is None or not item.visible:
            await edit(
                interaction,
                content="Ese artículo ya no está a la venta. Abre la `tienda` otra vez.",
            )
            return
        now = self.clock()
        error, note = await self._precheck(guild, buyer, item, now)
        if error is not None:
            await edit(interaction, content=f"❌ {error}")
            return
        balance = await self.economy.balance(guild.id, buyer.id)
        view = Checkout(self, guild, buyer, item, quote(item, now), balance=balance, note=note)
        view.skin_game = skin_game
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())

    async def _precheck(
        self, guild: discord.Guild, buyer: discord.Member, item: ShopItem, now: float
    ) -> tuple[str | None, str | None]:
        """Lo que se puede saber antes de cobrar: `(error, nota para la caja)`.

        Las reglas de existencias y límites se vuelven a mirar al pagar,
        dentro de la transacción (`ShopRepository.reserve`).
        """
        note = None
        if not on_sale(item, local_now(now)):
            season = season_name(item) or "su temporada"
            return f"Eso solo se vende en {season}. Vuelve entonces.", None
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
            return "Agotado. Llegaste tarde.", None
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
        if not on_sale(view.item, local_now(now)):
            # La caja se abrió en temporada y se ha pagado fuera: no se cobra.
            view.failed, view.result = True, "❌ Se acabó la temporada de ese artículo."
            return None
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
                f"❌ No te llega: tienes {format_amount(error.balance)} "
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
        elif item.kind is Kind.PET:
            got = (
                f"{item.emoji} {item.name} ya vive contigo. Ponle nombre, cuídala y elige "
                "si te acompaña con `mascota`."
            )
        else:
            got = f"{item.emoji} {item.name} ya está en tu mochila."
            if sale.serial is not None:
                got += f" Unidad nº {sale.serial} de {sale.edition}."
            if (use := use_of(item.catalog_key)) is not None:
                got += f" Úsalo desde la mochila ({use.verb.lower()})."
            if is_title(item.catalog_key):
                got += " Ya lo luces junto a tu nombre; lo cambias en la mochila."
            if (skin := skin_of_item(item.catalog_key)) is not None:
                game = GAMES[skin.game]
                got += (
                    f" Ya la llevas puesta en {game.label}; la cambias en la mochila o con "
                    "el botón 🎨 de la mesa."
                    if game.wearable
                    else f" Vale en todas las {game.name.lower()}: te la pongo en la mesa desde "
                    "la que la compras o en las que llevaban el de serie, y cada mesa la "
                    "cambias con su botón 🎨 o en la mochila."
                )
        lines = [
            "### ✅ ¡Es tuyo!",
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
        if hint := await renta.hint(self.bot, buyer.guild.id, buyer.id, Moment(Event.BUY)):
            lines.append(hint)
        return "\n".join(lines)

    async def after_purchase(
        self,
        interaction: discord.Interaction,
        view: Checkout,
        paid: tuple[ShopItem, Sale, int],
        *,
        announce: bool = True,
    ) -> None:
        """Aviso público, aviso de la Renta y logros, después de enseñar la factura.

        `announce=False` se salta el aviso público (compras desde otro panel).
        """
        item, sale, balance = paid
        title = is_title(item.catalog_key)
        skin = skin_of_item(item.catalog_key)
        if skin is not None:
            for game in self.wear_on_purchase(view.guild.id, view.buyer.id, skin, view.skin_game):
                await self.wear_skin(view.guild.id, view.buyer.id, game, skin)
        if title:
            # Comprar un título te lo pone, antes de anunciarlo.
            await self.wear_title(view.guild.id, view.buyer.id, item.catalog_key)
            lines = ANNOUNCE_TITLE
        elif use_of(item.catalog_key):
            lines = ANNOUNCE_USABLE
        else:
            lines = ANNOUNCE_LINES[item.kind]
        if announce:
            line = self.rng.choice(lines).format(
                who=f"**{_name(view.buyer)}**", item=f"{item.emoji} **{item.name}**"
            )
            extra = f" (nº {sale.serial} de {sale.edition})" if sale.serial is not None else ""
            big = view.price.total >= BIG_PURCHASE
            header = f"{self.rng.choice(ANNOUNCE_BIG)}\n" if big else ""
            try:
                await interaction.followup.send(
                    f"{header}🛍️ {line}{extra}\n-# Pagó {format_amount(view.price.total)}, "
                    f"de los que {format_amount(view.price.tax)} son IGIC para {TAX_COLLECTOR}.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.debug("No se pudo anunciar una compra", exc_info=True)
        # Comprar gasta dinero: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)
        if item.kind is Kind.PET:
            await mascotas.adopted(self.bot, view.guild.id, view.buyer, interaction.channel, item)
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
                catalog_key=item.catalog_key,
                aisle=aisle_of(item.catalog_key).key,
                title=title,
                skin=skin is not None,
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
        for key in [k for k in self.titles if k[0] == guild.id]:
            del self.titles[key]
        for skin_key in [k for k in self.skins if k[0] == guild.id]:
            del self.skins[skin_key]
        for image_key in [k for k in self.skin_images if k[0] == guild.id]:
            del self.skin_images[image_key]

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(name="tienda", description="Abre El Colmado de Jovani: roles, XP y más.")
    @app_commands.guild_only()
    async def tienda(self, interaction: discord.Interaction) -> None:
        """Enseña el escaparate en el canal; comprar abre una caja privada."""
        assert interaction.guild is not None  # guild_only
        # El escaparate se lee de la base de datos: «pensando…» mientras tanto.
        await interaction.response.defer(thinking=True)
        view = await self.storefront(interaction.guild, interaction.user)
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
        view.interaction = interaction

    @commands.command(name="tienda")
    @commands.guild_only()
    async def tienda_text(self, ctx: commands.Context) -> None:
        """Versión de texto de `tienda`."""
        assert ctx.guild is not None  # guild_only
        view = await self.storefront(ctx.guild, ctx.author)
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
        if interaction is not None:
            # Reponer y cargar el catálogo tardan: «pensando…» privado mientras tanto.
            await ack(interaction, new_message=True)
        await self.stock_up(guild.id)
        panel = AdminPanel(self, guild, owner)
        await panel.load()
        if interaction is not None:
            await edit(interaction, view=panel, allowed_mentions=discord.AllowedMentions.none())
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


def title_of(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """Título que luce un miembro (`🏰 Marquesado de Mogán`), o `None`.

    Lo usan `perfil`, `fortunas` y `ranking`. Sale de memoria: no toca la base
    de datos, así que se puede llamar por cada nombre de una lista.
    """
    if (cog := find_cog(bot, Tienda)) is not None:
        return cog.title(guild_id, user_id)
    return None


def skin_of(bot: commands.Bot, guild_id: int, user_id: int, game: str) -> Skin | None:
    """La skin que lleva un miembro en `game`, o `None` (la de serie). De memoria."""
    if (cog := find_cog(bot, Tienda)) is not None:
        return cog.skin(guild_id, user_id, game)
    return None


def skin_params(bot: commands.Bot, guild_id: int, user_id: int, game: str) -> dict[str, object]:
    """Lo que la escena de `game` tiene que pintar para este miembro.

    Los parámetros de serie con los de su skin encima (`skins.params`), y su
    imagen si la skin es propia. Si la tienda no está cargada, los de serie: el
    juego nunca se queda sin dibujo.
    """
    if (cog := find_cog(bot, Tienda)) is not None:
        return cog.scene_params(guild_id, user_id, game)
    return skin_params_of(game, None)


async def refresh_worn(bot: commands.Bot, guild_id: int, user_id: int) -> None:
    """Puente de `Tienda.refresh_worn`; no hace nada si la tienda no está cargada."""
    if (cog := find_cog(bot, Tienda)) is not None:
        await cog.refresh_worn(guild_id, user_id)


async def grant_reward_skins(
    bot: commands.Bot, guild_id: int, user_id: int, achievement_ids: list[str]
) -> list[Skin]:
    """Da las skins que regalan estos logros recién desbloqueados (`achievements`)."""
    if (cog := find_cog(bot, Tienda)) is None:
        return []
    try:
        return await cog.sync_reward_skins(guild_id, user_id, achievement_ids)
    except Exception:
        logger.exception("No se pudieron dar las skins de logros a %s", user_id)
        return []


async def grant_free_skins(bot: commands.Bot, guild_id: int, user_id: int, game: str) -> None:
    """Da las skins gratis de `game` que le falten (`Tienda.grant_free_skins`).

    No lanza: si la tienda no está o falla, el juego sigue con el gallo de serie.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return
    try:
        await cog.grant_free_skins(guild_id, user_id, game)
    except Exception:
        logger.exception("No se pudieron dar las skins gratis de %s a %s", game, user_id)


async def grant_box(
    bot: commands.Bot,
    guild_id: int,
    user_id: int,
    box_key: str,
    *,
    because: str,
    count: int = 1,
    pending: bool = False,
) -> str | None:
    """Regala una caja ganada jugando (`Tienda.grant_free_box`) y devuelve la línea de aviso.

    `apuestas` da una de cartón cada 100 jugadas y `message_stats`, una de regalo cada 5
    niveles. Con `pending`, el aviso espera a `notice` (el siguiente resultado con dinero
    de ese miembro) en vez de devolverse. No lanza: si la tienda no está o falla, solo se
    pierde la caja, y queda en el log.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return None
    try:
        line = await cog.grant_free_box(guild_id, user_id, box_key, count=count, because=because)
    except Exception:
        logger.exception("No se pudo dar la caja %s a %s", box_key, user_id)
        return None
    if line is not None and pending:
        cog.notices[(guild_id, user_id)].append(line)
        return None
    return line


def notice(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """El aviso pendiente de cajas ganadas, una sola vez; lo añade `renta.hint`."""
    if (cog := find_cog(bot, Tienda)) is None:
        return None
    return cog.notices_for(guild_id, user_id)


async def consume_key(bot: commands.Bot, guild_id: int, user_id: int, catalog_key: str) -> bool:
    """Gasta una unidad de un objeto de serie; `False` si no lo tiene o la tienda no está.

    `pala` lo usa para dar los regalos para la familia.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return False
    return await cog.consume_key(guild_id, user_id, catalog_key)


async def open_aisle(
    bot: commands.Bot, interaction: discord.Interaction, aisle: str | None, *, tab: str = "todo"
) -> bool:
    """Rellena una respuesta ya aceptada (`ack`) con el escaparate abierto en `aisle`.

    Es el atajo 🎁 de `pala` al pasillo de la familia y el de 🐾 Adoptar y 🥣 Comida
    del panel de `mascota`. `aisle` `None` son todos los pasillos; `tab`, la
    pestaña (`TABS`). `False` si la tienda no está cargada (y no se toca la respuesta).
    """
    if (cog := find_cog(bot, Tienda)) is None or interaction.guild is None:
        return False
    view = await cog.storefront(interaction.guild, interaction.user)
    view.aisle, view.tab, view.page = aisle, tab, 0
    view.rebuild()
    await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
    view.interaction = interaction
    return True


async def prepare_checkout(
    bot: commands.Bot, interaction: discord.Interaction, catalog_key: str
) -> Checkout | None:
    """Caja del artículo de serie `catalog_key`, sin enseñarla, para pagarla desde otro panel.

    `pala` la usa para comprar y dar un regalo sin salir del panel. Si no se puede
    comprar (nivel, existencias), la caja viene con `failed` y el motivo en
    `result`. `None` si la tienda no está cargada o no lo vende.
    """
    cog = find_cog(bot, Tienda)
    guild, buyer = interaction.guild, interaction.user
    if cog is None or guild is None or not isinstance(buyer, discord.Member):
        return None
    await cog.stock_up(guild.id)
    item = await cog.repository.item_by_key(guild.id, catalog_key)
    if item is None or not item.visible:
        return None
    now = cog.clock()
    error, note = await cog._precheck(guild, buyer, item, now)
    balance = await cog.economy.balance(guild.id, buyer.id)
    view = Checkout(cog, guild, buyer, item, quote(item, now), balance=balance, note=note)
    view.stop()  # no se enseña: solo guarda el precio y el comprador
    if error is not None:
        view.failed, view.result = True, f"❌ {error}"
    return view


async def pay_checkout(
    view: Checkout, interaction: discord.Interaction
) -> tuple[ShopItem, Sale, int] | None:
    """Cobra una caja de `prepare_checkout`; `None` y el motivo en `view.result` si no."""
    return await view.cog.complete_purchase(view, interaction)


async def after_checkout(
    view: Checkout, interaction: discord.Interaction, paid: tuple[ShopItem, Sale, int]
) -> None:
    """Renta y logros de una compra de `pay_checkout`, sin anunciarla en el canal."""
    await view.cog.after_purchase(interaction, view, paid, announce=False)


async def owned_keys(bot: commands.Bot, guild_id: int, user_id: int) -> frozenset[str]:
    """Claves de serie de lo que tiene un miembro; vacío si la tienda no está cargada.

    `pala` lo usa para las herramientas de curro. Si la base de datos falla, el
    turno sigue sin herramientas: mejor currar sin casco que no poder fichar.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return frozenset()
    try:
        return await cog.owned_keys(guild_id, user_id)
    except Exception:
        logger.exception("No se pudo leer la mochila de %s para la pala", user_id)
        return frozenset()


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, la tienda y los niveles del bot."""
    await bot.add_cog(Tienda(bot, bot.economy, bot.shop, levels=bot.message_stats))
