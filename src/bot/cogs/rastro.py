"""El Rastro: `vender`, `trueque` y `rastro`, la segunda mano entre miembros, y el Monte de
Piedad, que vive en el panel de `vender`.

- `vender` sin nada abre tu panel «🧺 Vender» (privado con `/`, en el canal con `.`; solo
  lo maneja quien lo abre). Eliges algo de tu mochila y ves su valor de referencia del
  Rastro y lo que da el Monte (o por qué no lo acepta), y lo mandas a uno de cuatro
  sitios: 🤝 a un miembro (un formulario con el miembro y el precio), 🧺 al Rastro (un
  formulario con el precio de salida), 💰 vendérselo al Monte o 🏷️ empeñarlo. Tus
  papeletas se desempeñan con 🎟️. Con `/vender objeto:X` el panel abre con X elegido.
- `vender <objeto> <precio> [comprador]` (el atajo, igual que antes del panel): con
  comprador, le hace una oferta de venta directa (botones 🤝 Aceptar y ✖️ Rechazar,
  solo para él; ↩️ Retirar, para quien vende) que caduca a los diez minutos. El precio
  puede ser 0 («de regalo»). Sin comprador, monta un puesto en el Rastro con ese precio
  de salida: el objeto sale de la mochila y cualquiera puede pujar con 🔨 Pujar. Si dos
  o más lo quieren, es una subasta: cada puja sube al menos un 5 % y da una hora más.
- `trueque <tu objeto> <miembro> <su objeto> [dinero]`: le propone cambiar un objeto
  de tu mochila por uno de la suya, con dinero encima si quieres. Botones 🤝 y ✖️
  para quien recibe la oferta y ↩️ para quien la hace; caduca a los diez minutos.
  Es una permuta: cada uno paga el ITP de lo que recibe y la plusvalía de lo que
  entrega (`bot.services.rastro.Swap`).
- `rastro`: el tablón con los puestos abiertos del servidor, sus pujas y cuándo se
  adjudican, con 🔨 para pujar y ↩️ para retirar los tuyos sin pujas.

Reglas y fiscalidad en `bot.services.rastro`: solo se venden coleccionables y
tapetes; quien compra tiene que tener el nivel mínimo del artículo y no pasarse de
su límite por persona; paga el 5,5 % de ITP sobre el mayor entre precio y valor de
referencia; la parte regalada está exenta, como el Bizum; y quien vende paga el
19 % de la plusvalía. El dinero pasa por `EconomyService.market`.

El Monte de Piedad (reglas en `bot.services.monte`, persistencia en
`bot.repositories.monte`) compra y presta con la caja de la tienda por
`EconomyService.monte` (fiscalidad en su docstring). Sus papeletas abiertas se guardan
en memoria (`tickets`) y una tarea mira cada `FORFEIT_EVERY` segundos las vencidas: el
objeto se queda en el Monte. Una operación con dinero a la vez por miembro (`working`).

Los puestos abiertos se guardan en memoria (`listings`) para que los botones de
pujar abran su formulario sin leer la base de datos (Discord da 3 segundos). Una
tarea mira cada `CLOSE_EVERY` segundos los que han llegado a su hora, los adjudica
o devuelve el objeto, y lo anuncia en el canal donde se montó el puesto. Los botones
🔨 y ↩️ son `DynamicItem`: siguen valiendo tras reiniciar el bot.

Al montar un puesto, la tienda avisa por privado a quien tenga ese artículo en su
lista de deseos (`shop.wish_listed`, aparte para no hacer esperar), y la ficha de la
tienda enseña la puja más baja de cada artículo en venta (`floor`).

Logros: categoría 🧺 Rastro, también los del Monte (`monte_*_stats`). Pujar, comprar,
vender al Monte, empeñar y desempeñar son acciones con dinero: llaman a `renta.remind`
y su resultado lleva `renta.hint` con la mascota (`Event.TRADE`). Las pujas, los puestos
con pujas y las papeletas del Monte salen en `saldo` (`pending`).
"""

from __future__ import annotations

import logging
import random
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.cogs import shop as tienda
from bot.repositories.economy import (
    SHOP_ACCOUNT_ID,
    BalanceLimitError,
    InsufficientFundsError,
    TillEmptyError,
)
from bot.repositories.monte import Appraisal, MonteRepository, Ticket
from bot.repositories.rastro import Listing, MarketRepository, Trade
from bot.repositories.shop import InventoryEntry, ShopRepository
from bot.services.achievements import (
    StatDelta,
    market_bid_stats,
    market_expired_stats,
    market_listing_stats,
    market_offer_stats,
    market_outbid_stats,
    market_purchase_stats,
    market_sale_stats,
    market_swap_stats,
    market_withdraw_stats,
    monte_forfeit_stats,
    monte_pawn_stats,
    monte_redeem_stats,
    monte_sale_stats,
)
from bot.services.economy import EconomyService, format_amount, parse_amount
from bot.services.levels import TIMEZONE, calculate_level_progress
from bot.services.monte import (
    MAX_TICKETS,
    MONTE_NAME,
    PAWN_FEE,
    PAWN_SECONDS,
    PAWN_SHARE,
    SELL_SHARE,
    MonteError,
)
from bot.services.pending import Pending, Side
from bot.services.pets import Event, Moment
from bot.services.rastro import (
    AUCTION_SECONDS,
    MARKET_NAME,
    MIN_RAISE_PCT,
    OFFER_SECONDS,
    Deal,
    MarketError,
    Swap,
    is_tapete,
    parse_sale_price,
    untradeable,
)
from bot.services.shop_catalog import is_title
from bot.services.skins import skin_of_item
from bot.services.taxes import CAPITAL_GAIN_RATE, ITP_RATE, TAX_COLLECTOR
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.message_stats import MessageStatsRepository

logger = logging.getLogger(__name__)

#: Toldo de puesto del Rastro.
COLOR = discord.Color.from_rgb(196, 120, 64)
COLOR_DONE = discord.Color.from_rgb(87, 242, 135)
#: Cada cuánto se miran los puestos que han llegado a su hora.
CLOSE_EVERY = 30
#: Cada cuánto se miran las papeletas vencidas del Monte.
FORFEIT_EVERY = 60
#: Lo que sigue respondiendo el panel de `vender`.
PANEL_TIMEOUT = 900
#: Lo que cabe en el desplegable de la mochila (el máximo de Discord).
MAX_OPTIONS = 25
#: Puestos por página del tablón (una fila de botones 🔨 y otra de ↩️).
PAGE_SIZE = 5

#: Lo que grita Jovani cuando alguien puja.
BID_CRIES = (
    "¡A la una!",
    "¡A la una, a las dos…!",
    "¿Quién da más? ¿Nadie? ¿Ni Florentino?",
    "¡Esto sube más que la luz!",
    "¡Toma mangó! ¡Esto se calienta!",
    "¡Se pone interesante!",
    "¡La puja más alta desde el precio del aceite!",
    "¡Pujan como si Hacienda no mirara! (Mira.)",
    "¡Qué subasta!",
)
#: Lo que dice al adjudicar.
SOLD_CRIES = (
    "🔨 ¡Adjudicado!",
    "🔨 ¡A la una, a las dos y a las tres: adjudicado!",
    "🔨 ¡Vendido!",
    "🔨 ¡Adjudicado! Ni Sotheby's.",
    "🔨 ¡Vendido! Más rápido que una ley en el BOE un viernes por la tarde.",
)
#: Lo que dice si un puesto cierra sin pujas.
EXPIRED_CRIES = (
    "Nadie lo ha querido, ni regalado.",
    "Una semana al sol y ni una puja. Ni en el Rastro de Cascorro.",
    "Ni una oferta. Vuelve a la mochila.",
)
#: Lo que dice al cerrar un trueque.
SWAP_CRIES = (
    "🔄 ¡Trueque hecho!",
    "🔄 ¡Trueque hecho! Como los cromos del recreo, pero con ITP.",
    "🔄 ¡Cambio cerrado!",
    "🔄 ¡Trato hecho! Hoy por ti, mañana por mí, y Hacienda por los dos.",
    "🔄 ¡Cambiazo!",
)
#: Lo que dice al cerrar una venta directa.
DEAL_CRIES = (
    "🤝 ¡Trato hecho!",
    "🤝 ¡Trato hecho, y sin notario!",
    "🤝 ¡Trato hecho! Hacienda ya se ha enterado, tranquilos.",
    "🤝 ¡Vendido! ¿Con pulpa? ¡Sin pulpa!",
)


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


def entry_label(entry: InventoryEntry) -> str:
    """`🏰 Marquesado de Mogán nº 3/10`."""
    text = f"{entry.emoji} {entry.name}"
    if entry.serial is not None:
        text += f" nº {entry.serial}/{entry.edition}" if entry.edition else f" nº {entry.serial}"
    return text


def sellable(entries: list[InventoryEntry]) -> list[InventoryEntry]:
    """Lo de la mochila que se puede vender, en orden de nombre y número de serie."""
    found = [e for e in entries if untradeable(e.kind, e.catalog_key) is None]
    return sorted(found, key=lambda e: (e.name.casefold(), e.serial or 0, e.id))


def pick_entry(entries: list[InventoryEntry], text: str) -> InventoryEntry | str:
    """El objeto que se quiere vender (por id del autocompletado o por nombre).

    Returns:
        El objeto, o un mensaje de error para enseñarlo tal cual.
    """
    found = sellable(entries)
    if not found:
        return (
            "No tienes nada que se pueda vender en el Rastro: solo coleccionables y "
            "tapetes. Pásate por la `tienda`."
        )
    text = text.strip()
    if text.isdigit():
        for entry in found:
            if entry.id == int(text):
                return entry
    needle = text.casefold()
    exact = [e for e in found if e.name.casefold() == needle]
    near = exact or [e for e in found if needle and needle in e.name.casefold()]
    if near:
        return near[0]
    names = ", ".join(dict.fromkeys(f"{e.emoji} {e.name}" for e in found[:8]))
    shown = discord.utils.escape_markdown(text)
    return f"No encuentro «{shown}» en tu mochila. Puedes vender: {names}."


def deal_lines(deal: Deal, seller: str) -> list[str]:
    """Las cuentas de una venta, para la oferta y para el resultado."""
    itp_pct = f"{ITP_RATE * 100:.1f}".replace(".", ",")
    lines = [
        f"> 🏷️ Valor de referencia: **{format_amount(deal.reference)}** (precio de la tienda)",
        f"> 🧾 ITP del {itp_pct} % sobre {format_amount(deal.itp_base)}: "
        f"**{format_amount(deal.itp)}** a cargo de quien compra",
        f"> 💸 Quien compra paga **{format_amount(deal.buyer_pays)}** en total",
    ]
    if deal.gift:
        lines.append(
            f"> 🎁 La parte regalada ({format_amount(deal.gift)}) está exenta de Donaciones, "
            "como el Bizum."
        )
    if deal.gain_tax:
        lines.append(
            f"> 📈 {seller} paga el {round(CAPITAL_GAIN_RATE * 100)} % de plusvalía: "
            f"**{format_amount(deal.gain_tax)}** y cobra {format_amount(deal.seller_gets)}"
        )
    return lines


def tax_note(deal: Deal) -> str:
    """Línea pequeña con la mordida de Hacienda en una venta del Rastro."""
    bite = deal.itp + deal.gain_tax
    if not bite:
        return f"-# 🐶 {TAX_COLLECTOR} no rasca nada: ni valor, ni ITP, ni plusvalía."
    return f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(bite)} entre ITP y plusvalía."


def swap_lines(swap: Swap, proposer: str, target: str) -> list[str]:
    """Las cuentas de un trueque, para la oferta y para el resultado."""
    itp_pct = f"{ITP_RATE * 100:.1f}".replace(".", ",")
    gain_pct = round(CAPITAL_GAIN_RATE * 100)
    lines = [
        f"> 🏷️ Valores de referencia: **{format_amount(swap.given_reference)}** lo de "
        f"{proposer} y **{format_amount(swap.received_reference)}** lo de {target}",
        f"> 🧾 ITP del {itp_pct} % de lo que recibe cada uno: {proposer} "
        f"**{format_amount(swap.proposer_itp)}**, {target} **{format_amount(swap.target_itp)}**",
    ]
    if swap.proposer_gain_tax or swap.target_gain_tax:
        lines.append(
            f"> 📈 Plusvalía del {gain_pct} % de lo que entrega cada uno: {proposer} "
            f"**{format_amount(swap.proposer_gain_tax)}**, {target} "
            f"**{format_amount(swap.target_gain_tax)}**"
        )
    target_side = (
        f"paga **{format_amount(swap.target_pays)}**"
        if swap.target_pays >= 0
        else f"cobra **{format_amount(-swap.target_pays)}**"
    )
    lines.append(
        f"> 💸 En total, {proposer} paga **{format_amount(swap.proposer_pays)}** y {target} "
        f"{target_side}"
    )
    return lines


def listing_line(listing: Listing, seller: str) -> str:
    """Una línea del tablón del Rastro."""
    if listing.top_bid is None:
        state = f"salida **{format_amount(listing.start_price)}**, sin pujas"
    else:
        state = (
            f"va por **{format_amount(listing.top_bid)}** ({listing.bids} "
            f"{'puja' if listing.bids == 1 else 'pujas'})"
        )
    when = f"<t:{int(listing.deadline)}:R>"
    closes = f"se adjudica {when}" if listing.close_at is not None else f"cierra {when}"
    return f"**#{listing.id}** {listing.label} · de {seller}\n> {state} · {closes}"


def ticket_line(ticket: Ticket) -> str:
    """Una línea de una papeleta abierta del Monte."""
    return (
        f"**#{ticket.id}** {ticket.label} · devuelves **{format_amount(ticket.repay)}** · "
        f"vence <t:{int(ticket.due_at)}:R>"
    )


#: Lo que devuelve una operación del Monte: el texto del resultado y sus logros (o `None`).
Outcome = tuple[str, StatDelta | None]


class Rastro(commands.Cog):
    """Ventas de segunda mano entre miembros (ofertas directas y subastas) y el Monte."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: MarketRepository,
        shop: ShopRepository,
        monte: MonteRepository,
        *,
        levels: MessageStatsRepository | None = None,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.shop = shop
        self.monte = monte
        self.levels = levels
        self.clock = clock
        self.rng = rng or random.Random()
        #: Puestos abiertos de todos los servidores, por id.
        self.listings: dict[int, Listing] = {}
        #: Papeletas abiertas del Monte de todos los servidores, por id (`saldo` y la tarea).
        self.tickets: dict[int, Ticket] = {}
        #: `(servidor, miembro)` con una operación del Monte en marcha: una a la vez.
        self.working: set[tuple[int, int]] = set()

    async def cog_load(self) -> None:
        """Carga puestos y papeletas, registra los botones y arranca las dos tareas."""
        await self.repository.initialize()
        await self.monte.initialize()
        self.listings = {listing.id: listing for listing in await self.repository.open_listings()}
        self.tickets = {t.id: t for t in await self.monte.open_tickets()}
        self.bot.add_dynamic_items(BidButton, WithdrawButton, PageButton)
        self._closer.start()
        self._forfeiter.start()

    async def cog_unload(self) -> None:
        """Para el cierre de puestos y el de papeletas y suelta los botones."""
        self._closer.cancel()
        self._forfeiter.cancel()
        self.bot.remove_dynamic_items(BidButton, WithdrawButton, PageButton)

    async def member_level(self, guild_id: int, user_id: int) -> int:
        """Nivel del miembro (0 si los niveles no están disponibles)."""
        if self.levels is None:
            return 0
        return calculate_level_progress(await self.levels.member_xp(guild_id, user_id)).level

    async def _requirements(self, guild_id: int, user_id: int, min_level: int) -> str | None:
        """Por qué alguien no puede tener el artículo (nivel mínimo), o `None`."""
        if min_level and (level := await self.member_level(guild_id, user_id)) < min_level:
            return (
                f"Hace falta nivel {min_level} para tener eso y va por el {level}. "
                "Lo que cuesta esfuerzo no se compra de segunda mano."
            )
        return None

    # -- vender ------------------------------------------------------------------------

    async def sellable_entries(self, guild_id: int, user_id: int) -> list[InventoryEntry]:
        """Lo que un miembro puede vender ahora."""
        return sellable(await self.shop.inventory(guild_id, user_id, self.clock()))

    async def sell(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable | None,
        seller: discord.abc.User,
        entry_text: str,
        price_text: str,
        buyer: discord.abc.User | None,
    ) -> tuple[str | None, discord.Embed | None, ui.View | None]:
        """Prepara una venta directa o monta un puesto.

        Returns:
            `(texto, embed, vista)`. Sin embed, el texto es un error para quien vende.
            Con embed es público: la oferta (que menciona a quien compra) o el puesto.
        """
        if buyer is not None and buyer.bot:
            return "Los bots no compran nada. Ni con el kit digital.", None, None
        if buyer is not None and buyer.id == seller.id:
            return "Venderte algo a ti mismo es blanquear, no comerciar.", None, None
        entries = await self.shop.inventory(guild.id, seller.id, self.clock())
        entry = pick_entry(entries, entry_text)
        if isinstance(entry, str):
            return entry, None, None
        try:
            price = parse_sale_price(price_text, allow_gift=buyer is not None)
        except ValueError as error:
            return str(error), None, None
        if buyer is None:
            return await self._open_stall(guild, channel, seller, entry, price)
        reference, min_level, _per_user, cost = await self.repository.appraise(
            guild.id, entry.item_id, entry.id, seller.id
        )
        if (problem := await self._requirements(guild.id, buyer.id, min_level)) is not None:
            return problem, None, None
        deal = Deal(price, reference, cost)
        view = OfferView(self, guild.id, seller, buyer, entry, price)
        what = f"por **{format_amount(price)}**" if price else "**de regalo**"
        lines = [
            f"🧺 **{_name(seller)}** le ofrece a {buyer.mention} **{entry_label(entry)}** {what}.",
            *deal_lines(deal, _name(seller)),
            f"-# La oferta caduca <t:{int(self.clock()) + OFFER_SECONDS}:R>. Las cifras se "
            "recalculan al aceptar.",
        ]
        embed = discord.Embed(description="\n".join(lines), color=COLOR)
        return buyer.mention, embed, view

    async def offered(
        self, guild_id: int, seller: discord.abc.User, channel: object, view: ui.View | None
    ) -> None:
        """Logros de quien acaba de publicar una oferta directa (después de publicarla)."""
        if isinstance(view, OfferView):
            await logros.track(self.bot, guild_id, seller, channel, market_offer_stats())

    async def _open_stall(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable | None,
        seller: discord.abc.User,
        entry: InventoryEntry,
        price: int,
    ) -> tuple[str | None, discord.Embed | None, ui.View | None]:
        now = self.clock()
        channel_id = getattr(channel, "id", None)
        try:
            listing = await self.repository.open_stall(
                guild.id, seller.id, entry.id, start_price=price, channel_id=channel_id, now=now
            )
        except MarketError as error:
            return str(error), None, None
        self.listings[listing.id] = listing
        await tienda.refresh_worn(self.bot, guild.id, seller.id)
        reference, _level, _per_user = await self.repository.reference(guild.id, listing.item_id)
        lines = [
            f"🧺 **{_name(seller)}** monta un puesto en {MARKET_NAME}: **{listing.label}**",
            f"> Precio de salida **{format_amount(price)}** · valor de referencia "
            f"{format_amount(reference)}",
            f"> Quien se lo lleve paga además el ITP sobre el mayor de los dos. Si lo quieren "
            f"varios, se subasta: cada puja sube al menos un {MIN_RAISE_PCT} % y da "
            f"{AUCTION_SECONDS // 60} minutos más («a la una, a las dos…»).",
            f"-# Si nadie puja, cierra <t:{int(listing.expires_at)}:R> y vuelve a tu mochila. "
            "Mira todos los puestos con `rastro`.",
        ]
        embed = discord.Embed(description="\n".join(lines), color=COLOR)
        view = ui.View(timeout=None)
        view.add_item(BidButton(listing.id))
        await logros.track(
            self.bot, guild.id, seller, channel, market_listing_stats(when=self._when(now))
        )
        # Quien lo tenga en su lista de deseos se entera por privado (va aparte).
        tienda.wish_listed(self.bot, guild.id, listing)
        return None, embed, view

    # -- trueque -----------------------------------------------------------------------

    async def barter(
        self,
        guild: discord.Guild,
        proposer: discord.abc.User,
        target: discord.abc.User,
        mine_text: str,
        theirs_text: str,
        money_text: str | None,
    ) -> tuple[str | None, discord.Embed | None, ui.View | None]:
        """Prepara la oferta de un trueque.

        Returns:
            `(texto, embed, vista)`. Sin embed, el texto es un error para quien propone;
            con embed es la oferta pública, que menciona a quien la recibe.
        """
        if target.bot:
            return "Los bots no cambian cromos. Ni con el kit digital.", None, None
        if target.id == proposer.id:
            return "Cambiarte algo contigo mismo es hacer limpieza, no trueque.", None, None
        now = self.clock()
        mine = pick_entry(await self.shop.inventory(guild.id, proposer.id, now), mine_text)
        if isinstance(mine, str):
            return mine, None, None
        theirs = pick_entry(await self.shop.inventory(guild.id, target.id, now), theirs_text)
        if isinstance(theirs, str):
            return (
                f"{_name(target)} no tiene eso para cambiar: solo coleccionables y tapetes. "
                f"Mira su mochila con `perfil`.",
                None,
                None,
            )
        if mine.item_id == theirs.item_id:
            return "Cambiar una cosa por otra igual es perder el tiempo.", None, None
        try:
            money = parse_sale_price(money_text, allow_gift=True) if money_text else 0
        except ValueError as error:
            return str(error), None, None
        given_ref, given_level, _per, given_cost = await self.repository.appraise(
            guild.id, mine.item_id, mine.id, proposer.id
        )
        got_ref, got_level, _per, got_cost = await self.repository.appraise(
            guild.id, theirs.item_id, theirs.id, target.id
        )
        for member, level in ((target, given_level), (proposer, got_level)):
            if (problem := await self._requirements(guild.id, member.id, level)) is not None:
                return problem, None, None
        swap = Swap(given_ref, given_cost, got_ref, got_cost, money)
        view = SwapView(self, guild.id, proposer, target, mine, theirs, money)
        extra = f" y **{format_amount(money)}**" if money else ""
        lines = [
            f"🔄 **{_name(proposer)}** le propone a {target.mention} un trueque: su "
            f"**{entry_label(mine)}**{extra} por tu **{entry_label(theirs)}**.",
            *swap_lines(swap, _name(proposer), _name(target)),
            f"-# La oferta caduca <t:{int(now) + OFFER_SECONDS}:R>. Las cifras se recalculan "
            "al aceptar.",
        ]
        embed = discord.Embed(description="\n".join(lines), color=COLOR)
        return target.mention, embed, view

    async def accept_swap(self, interaction: discord.Interaction, offer: SwapView) -> bool:
        """Cierra un trueque al pulsar 🤝 (ya con `ack`). Devuelve si se hizo."""
        guild = interaction.guild
        if guild is None:
            return False
        proposer, target = offer.proposer, offer.target
        for member, item_id in ((target, offer.given_item), (proposer, offer.received_item)):
            _ref, level, _per = await self.repository.reference(guild.id, item_id)
            if (problem := await self._requirements(guild.id, member.id, level)) is not None:
                await notify(interaction, problem)
                return False
        now = self.clock()
        try:
            barter, balances = await self.economy.market(
                guild.id,
                self.repository.swap(
                    guild.id,
                    proposer.id,
                    target.id,
                    offer.given_entry,
                    offer.received_entry,
                    offer.money,
                    now,
                ),
            )
        except MarketError as error:
            await notify(interaction, str(error))
            return False
        except InsufficientFundsError as error:
            await notify(
                interaction,
                f"A uno de los dos no le llega para el dinero y los impuestos "
                f"(se quedaba en {format_amount(error.balance)}).",
            )
            return False
        except BalanceLimitError:
            await notify(interaction, "A uno de los dos ya no le cabe más dinero en la cuenta.")
            return False
        swap = barter.swap
        lines = [
            f"{self.rng.choice(SWAP_CRIES)} **{_name(proposer)}** se lleva "
            f"**{offer.received_label}** y **{_name(target)}**, **{offer.given_label}**"
            + (f" y {format_amount(swap.money)}." if swap.money else "."),
            *swap_lines(swap, _name(proposer), _name(target)),
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(swap.taxes)} entre ITP y plusvalía."
            if swap.taxes
            else f"-# 🐶 {TAX_COLLECTOR} no rasca nada: ni valor, ni ITP, ni plusvalía.",
        ]
        if (
            hint := await renta.hint(self.bot, guild.id, target.id, Moment(Event.TRADE))
        ) is not None:
            lines.append(hint)
        await edit(
            interaction,
            content=None,
            embed=discord.Embed(description="\n".join(lines), color=COLOR_DONE),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        for member in (proposer, target):
            await tienda.refresh_worn(self.bot, guild.id, member.id)
        await renta.remind(self.bot, interaction)
        when = self._when(now)
        titles = is_title(barter.given[3]) and is_title(barter.received[3])
        for member, gave, got, is_proposer in (
            (proposer, swap.given_reference + swap.money, swap.received_reference, True),
            (target, swap.received_reference, swap.given_reference + swap.money, False),
        ):
            await logros.track(
                self.bot,
                guild.id,
                member,
                interaction.channel,
                market_swap_stats(
                    gave=gave,
                    got=got,
                    money=swap.money,
                    proposer=is_proposer,
                    titles=titles,
                    when=when,
                ),
            )
        return True

    def _when(self, now: float) -> datetime:
        return datetime.fromtimestamp(now, TIMEZONE)

    async def accept_offer(self, interaction: discord.Interaction, offer: OfferView) -> bool:
        """Cierra una venta directa al pulsar 🤝 (ya con `ack`). Devuelve si se vendió."""
        guild = interaction.guild
        if guild is None:
            return False
        buyer, seller = offer.buyer, offer.seller
        _ref, min_level, _per_user = await self.repository.reference(guild.id, offer.item_id)
        if (problem := await self._requirements(guild.id, buyer.id, min_level)) is not None:
            await notify(interaction, problem)
            return False
        now = self.clock()
        try:
            trade, balances = await self.economy.market(
                guild.id,
                self.repository.sell(
                    guild.id, seller.id, buyer.id, offer.entry_id, offer.price, now
                ),
            )
        except MarketError as error:
            await notify(interaction, str(error))
            return False
        except InsufficientFundsError as error:
            await notify(
                interaction,
                f"No te llega: tienes {format_amount(error.balance)} y hace falta "
                "el precio más el ITP.",
            )
            return False
        except BalanceLimitError:
            await notify(interaction, "A quien vende ya no le cabe más dinero en la cuenta.")
            return False
        deal = trade.deal
        assert deal is not None
        lines = [
            f"{self.rng.choice(DEAL_CRIES)} **{_name(buyer)}** se lleva **{offer.label}** de "
            f"**{_name(seller)}** "
            + (f"por **{format_amount(deal.price)}**." if deal.price else "**de regalo**."),
            *deal_lines(deal, _name(seller)),
            tax_note(deal),
        ]
        if (
            hint := await renta.hint(self.bot, guild.id, buyer.id, Moment(Event.TRADE))
        ) is not None:
            lines.append(hint)
        await edit(
            interaction,
            content=None,
            embed=discord.Embed(description="\n".join(lines), color=COLOR_DONE),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await tienda.refresh_worn(self.bot, guild.id, seller.id)
        await renta.remind(self.bot, interaction)
        await self._track_trade(guild.id, interaction.channel, trade, balances, now)
        return True

    async def _track_trade(
        self,
        guild_id: int,
        channel: object,
        trade: Trade,
        balances: dict[int, int],
        now: float,
    ) -> None:
        """Logros de quien vende y de quien compra."""
        deal = trade.deal
        guild = self.bot.get_guild(guild_id)
        if deal is None or trade.buyer_id is None or guild is None:
            return
        when = self._when(now)
        bids = trade.listing.bids if trade.listing else 0
        seller = guild.get_member(trade.seller_id)
        buyer = guild.get_member(trade.buyer_id)
        if seller is not None:
            await logros.track(
                self.bot,
                guild_id,
                seller,
                channel,
                market_sale_stats(
                    price=deal.price, cost=deal.cost, gain_tax=deal.gain_tax, bids=bids, when=when
                ),
            )
        if buyer is not None:
            balance = balances.get(buyer.id)
            if balance is None:
                balance = await self.economy.balance(guild_id, buyer.id)
            skin = skin_of_item(trade.catalog_key)
            await logros.track(
                self.bot,
                guild_id,
                buyer,
                channel,
                market_purchase_stats(
                    price=deal.price,
                    itp=deal.itp,
                    auction=trade.listing is not None,
                    serial=trade.serial,
                    title=is_title(trade.catalog_key),
                    tapete=skin is not None and is_tapete(skin),
                    balance_after=balance,
                    when=when,
                ),
            )

    # -- Pujas -------------------------------------------------------------------------

    async def open_bid_form(self, interaction: discord.Interaction, listing_id: int) -> None:
        """Abre el formulario de puja. Solo mira memoria: `send_modal` va primero."""
        listing = self.listings.get(listing_id)
        if listing is None or interaction.guild is None or listing.guild_id != interaction.guild.id:
            await interaction.response.send_message(
                "Ese puesto ya no está en el Rastro.", ephemeral=True
            )
            return
        problem = None
        if interaction.user.id == listing.seller_id:
            problem = "Pujar por lo tuyo para inflar el precio es de la Gürtel. Aquí no."
        elif interaction.user.id == listing.top_bidder:
            problem = "Ya vas ganando: no te pujes a ti mismo, que no es el CIS."
        elif self.clock() >= listing.deadline:
            problem = "Ese puesto ya ha cerrado. ¡Adjudicado a otro!"
        if problem is not None:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        await interaction.response.send_modal(BidForm(self, listing))

    async def place_bid(self, interaction: discord.Interaction, listing_id: int, text: str) -> None:
        """Puja desde el formulario: retiene puja e ITP y devuelve lo del anterior."""
        await ack(interaction)
        guild = interaction.guild
        user = interaction.user
        listing = self.listings.get(listing_id)
        if guild is None or listing is None:
            await notify(interaction, "Ese puesto ya no está en el Rastro.")
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            amount = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        _ref, min_level, _per_user = await self.repository.reference(guild.id, listing.item_id)
        if (problem := await self._requirements(guild.id, user.id, min_level)) is not None:
            await notify(interaction, problem)
            return
        now = self.clock()
        try:
            bid, _balances = await self.economy.market(
                guild.id, self.repository.bid(guild.id, listing_id, user.id, amount, now)
            )
        except MarketError as error:
            await notify(interaction, str(error))
            return
        except InsufficientFundsError as error:
            await notify(
                interaction,
                f"No te llega: tienes {format_amount(error.balance)} y se retiene "
                "la puja más su ITP.",
            )
            return
        listing = bid.listing
        self.listings[listing.id] = listing
        lines = [
            f"🔨 Pujas **{format_amount(amount)}** por **{listing.label}**. Te retenemos además "
            f"{format_amount(bid.itp)} de ITP, que solo pagas si te lo llevas. Si alguien puja "
            "más, te lo devolvemos entero.",
        ]
        if (hint := await renta.hint(self.bot, guild.id, user.id, Moment(Event.TRADE))) is not None:
            lines.append(hint)
        await notify(interaction, "\n".join(lines))
        public = [
            f"🔨 {user.mention} puja **{format_amount(amount)}** por **{listing.label}**. "
            f"{self.rng.choice(BID_CRIES)} Se adjudica <t:{int(listing.deadline)}:R> si nadie "
            "da más.",
        ]
        mentions = [user]
        outbid = guild.get_member(bid.outbid) if bid.outbid is not None else None
        if outbid is not None:
            public.append(
                f"{outbid.mention}, te han superado: te devolvemos tu puja y su ITP. "
                "¿Vas a dejar que se lo lleve?"
            )
            mentions.append(outbid)
        await notify(
            interaction,
            "\n".join(public),
            ephemeral=False,
            view=_bid_view(listing.id),
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=mentions),
        )
        await renta.remind(self.bot, interaction)
        await logros.track(
            self.bot, guild.id, user, interaction.channel,
            market_bid_stats(amount=amount, when=self._when(now)),
        )  # fmt: skip
        if outbid is not None:
            await logros.track(
                self.bot, guild.id, outbid, interaction.channel, market_outbid_stats()
            )

    # -- Retirar y cerrar --------------------------------------------------------------

    async def withdraw(self, interaction: discord.Interaction, listing_id: int) -> None:
        """Retira un puesto propio sin pujas (botón ↩️ del tablón)."""
        listing = self.listings.get(listing_id)
        if listing is None:
            await interaction.response.send_message(
                "Ese puesto ya no está en el Rastro.", ephemeral=True
            )
            return
        if listing.seller_id != interaction.user.id:
            await interaction.response.send_message("Ese puesto no es tuyo.", ephemeral=True)
            return
        await ack(interaction)
        try:
            await self.repository.withdraw(
                listing.guild_id, listing_id, listing.seller_id, self.clock()
            )
        except MarketError as error:
            await notify(interaction, str(error))
            return
        self.listings.pop(listing_id, None)
        await edit(
            interaction,
            **self.board(listing.guild_id, interaction.user.id, 0),
        )
        await notify(interaction, f"↩️ **{listing.label}** vuelve a tu mochila.")
        await logros.track(
            self.bot, listing.guild_id, interaction.user, interaction.channel,
            market_withdraw_stats(),
        )  # fmt: skip

    @tasks.loop(seconds=CLOSE_EVERY)
    async def _closer(self) -> None:
        await self.close_due()

    @_closer.before_loop
    async def _before_closer(self) -> None:
        await self.bot.wait_until_ready()

    async def close_due(self) -> int:
        """Adjudica o devuelve los puestos que han llegado a su hora. Devuelve cuántos."""
        now = self.clock()
        closed = 0
        for listing in [item for item in self.listings.values() if item.deadline <= now]:
            try:
                trade, balances = await self.economy.market(
                    listing.guild_id, self.repository.close(listing.guild_id, listing.id, now)
                )
            except MarketError:
                # Ya cerrado por otro camino: la memoria se pone al día.
                self.listings.pop(listing.id, None)
                continue
            except Exception:
                logger.exception("No se pudo cerrar el puesto %s del Rastro", listing.id)
                continue
            self.listings.pop(listing.id, None)
            closed += 1
            try:
                await self._announce(trade, balances, now)
            except discord.HTTPException:
                logger.warning("No se pudo anunciar el cierre del puesto %s", listing.id)
        return closed

    async def _announce(self, trade: Trade, balances: dict[int, int], now: float) -> None:
        """Cuenta en el canal del puesto cómo ha acabado."""
        listing = trade.listing
        assert listing is not None
        guild = self.bot.get_guild(listing.guild_id)
        channel = self.bot.get_channel(listing.channel_id) if listing.channel_id else None
        seller = guild.get_member(trade.seller_id) if guild else None
        label = listing.label
        if trade.expired:
            text = (
                f"🧺 {seller.mention if seller else 'Alguien'}: **{label}** vuelve a tu mochila. "
            )
            text += self.rng.choice(EXPIRED_CRIES)
            mentions = [seller] if seller else []
            if seller is not None:
                await logros.track(
                    self.bot, listing.guild_id, seller, channel, market_expired_stats()
                )
        elif trade.void:
            buyer = guild.get_member(trade.buyer_id) if guild and trade.buyer_id else None
            text = (
                f"🧺 La subasta de **{label}** queda desierta: quien la ganaba ya tiene todos "
                "los que se pueden tener. Se le devuelve su puja y el objeto vuelve a "
                f"{seller.mention if seller else 'su dueño'}."
            )
            mentions = [m for m in (seller, buyer) if m is not None]
        else:
            deal = trade.deal
            assert deal is not None and trade.buyer_id is not None
            buyer = guild.get_member(trade.buyer_id) if guild else None
            text = "\n".join(
                [
                    f"{self.rng.choice(SOLD_CRIES)} **{label}** para "
                    f"{buyer.mention if buyer else 'alguien'} por **{format_amount(deal.price)}**"
                    f" ({listing.bids} {'puja' if listing.bids == 1 else 'pujas'}). "
                    f"{seller.mention if seller else 'Quien vendía'} cobra "
                    f"{format_amount(deal.seller_gets)}.",
                    tax_note(deal),
                ]
            )
            mentions = [m for m in (seller, buyer) if m is not None]
            await self._track_trade(listing.guild_id, channel, trade, balances, now)
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(
                text,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=mentions
                ),
            )

    # -- Monte de Piedad ---------------------------------------------------------------

    @tasks.loop(seconds=FORFEIT_EVERY)
    async def _forfeiter(self) -> None:
        await self.forfeit_due()

    @_forfeiter.before_loop
    async def _before_forfeiter(self) -> None:
        await self.bot.wait_until_ready()

    async def forfeit_due(self) -> list[Ticket]:
        """Cierra las papeletas vencidas (el objeto se queda en el Monte). Nunca lanza."""
        try:
            lost = await self.monte.forfeit_due(self.clock())
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron cerrar las papeletas vencidas del Monte")
            return []
        for ticket in lost:
            self.tickets.pop(ticket.id, None)
            logros.note(self.bot, ticket.guild_id, ticket.user_id, monte_forfeit_stats())
        return lost

    def tickets_of(self, guild_id: int, user_id: int) -> list[Ticket]:
        """Las papeletas abiertas de alguien, de la que vence antes a la última."""
        return sorted(
            (t for t in self.tickets.values() if t.guild_id == guild_id and t.user_id == user_id),
            key=lambda t: t.due_at,
        )

    async def monte_sell(
        self, interaction: discord.Interaction, guild_id: int, entry_id: int
    ) -> Outcome:
        """Vende un objeto al Monte (ya con `ack`). Devuelve el resultado y sus logros."""
        user = interaction.user
        try:
            sale, balance = await self.economy.monte(
                guild_id, self.monte.sell(guild_id, user.id, entry_id, self.clock())
            )
        except MonteError as error:
            return str(error), None
        except TillEmptyError:
            return "La caja de la tienda no llega para pagarte eso ahora mismo.", None
        except BalanceLimitError:
            return "No te cabe más dinero en la cuenta.", None
        lines = [
            f"💰 El Monte te compra **{sale.label}** por **{format_amount(sale.amount)}**. "
            f"Te quedas con {format_amount(balance)}.",
        ]
        if (hint := await renta.hint(self.bot, guild_id, user.id, Moment(Event.TRADE))) is not None:
            lines.append(hint)
        await tienda.refresh_worn(self.bot, guild_id, user.id)
        return "\n".join(lines), monte_sale_stats(
            amount=sale.amount, title=is_title(sale.catalog_key)
        )

    async def monte_pawn(
        self, interaction: discord.Interaction, guild_id: int, entry_id: int
    ) -> Outcome:
        """Empeña un objeto (ya con `ack`). Devuelve el resultado y sus logros."""
        user = interaction.user
        try:
            ticket, balance = await self.economy.monte(
                guild_id, self.monte.pawn(guild_id, user.id, entry_id, self.clock())
            )
        except MonteError as error:
            return str(error), None
        except TillEmptyError:
            return "La caja de la tienda no llega para prestarte eso ahora mismo.", None
        except BalanceLimitError:
            return "No te cabe más dinero en la cuenta.", None
        self.tickets[ticket.id] = ticket
        lines = [
            f"🏷️ Empeñas **{ticket.label}**: te prestan **{format_amount(ticket.loan)}** "
            f"(te quedas con {format_amount(balance)}). Devuelve "
            f"**{format_amount(ticket.repay)}** antes de <t:{int(ticket.due_at)}:f> para "
            "recuperarlo; si no, se lo queda el Monte.",
        ]
        if (hint := await renta.hint(self.bot, guild_id, user.id, Moment(Event.TRADE))) is not None:
            lines.append(hint)
        await tienda.refresh_worn(self.bot, guild_id, user.id)
        open_tickets = len(self.tickets_of(guild_id, user.id))
        return "\n".join(lines), monte_pawn_stats(loan=ticket.loan, open_tickets=open_tickets)

    async def monte_redeem(
        self, interaction: discord.Interaction, guild_id: int, ticket_id: int
    ) -> Outcome:
        """Desempeña una papeleta (ya con `ack`). Devuelve el resultado y sus logros."""
        user = interaction.user
        now = self.clock()
        try:
            ticket, balance = await self.economy.monte(
                guild_id, self.monte.redeem(guild_id, user.id, ticket_id, now)
            )
        except MonteError as error:
            # Vencida o perdida: la memoria se pone al día (solo con las papeletas propias).
            if (ticket := self.tickets.get(ticket_id)) is not None and ticket.user_id == user.id:
                self.tickets.pop(ticket_id, None)
            return str(error), None
        except InsufficientFundsError as error:
            return (
                f"No te llega: tienes {format_amount(error.balance)} y para desempeñarlo hace "
                "falta lo prestado más el interés."
            ), None
        self.tickets.pop(ticket.id, None)
        lines = [
            f"🎟️ Recuperas **{ticket.label}** por **{format_amount(ticket.repay)}**. Te "
            f"quedas con {format_amount(balance)}.",
        ]
        if (hint := await renta.hint(self.bot, guild_id, user.id, Moment(Event.TRADE))) is not None:
            lines.append(hint)
        return "\n".join(lines), monte_redeem_stats(seconds_left=ticket.due_at - now)

    # -- Panel de vender ---------------------------------------------------------------

    async def panel_embed(
        self, guild_id: int, user_id: int, selected: int | None
    ) -> tuple[discord.Embed, list[Appraisal], list[Ticket]]:
        """El panel «🧺 Vender»: lo vendible de la mochila, lo elegido y tus papeletas.

        Returns:
            `(embed, lo vendible por orden de nombre, papeletas abiertas)`. Lo que no se
            vende ni en el Rastro (skins que no son tapetes) no sale.
        """
        found = await self.monte.appraisals(guild_id, user_id, self.clock())
        goods = sorted(
            (a for a in found if untradeable(a.entry.kind, a.entry.catalog_key) is None),
            key=lambda a: (a.entry.name.casefold(), a.entry.serial or 0, a.entry.id),
        )
        tickets = self.tickets_of(guild_id, user_id)
        till = (await self.economy.system_balances(guild_id, [SHOP_ACCOUNT_ID]))[SHOP_ACCOUNT_ID]
        embed = discord.Embed(
            title="🧺 Vender",
            color=COLOR,
            description=(
                "Elige algo de tu mochila y a dónde va:\n"
                "🤝 **A un miembro**: le ofreces un precio (o se lo regalas) y decide.\n"
                f"🧺 **Al Rastro**: montas un puesto con un precio de salida y, si lo quieren "
                f"varios, se subasta. Quien compra paga el ITP; tú, la plusvalía si ganas.\n"
                f"💰 **Al {MONTE_NAME}**: te lo compra al **{round(SELL_SHARE * 100)} %** de "
                "lo que pagaste en la tienda (sin IGIC).\n"
                f"🏷️ **Empeñarlo**: el Monte te presta el **{round(PAWN_SHARE * 100)} %** y lo "
                f"recuperas devolviendo un {round(PAWN_FEE * 100)} % más en "
                f"{PAWN_SECONDS // 86_400} días; si no, se lo queda.\n"
                f"-# El Monte paga con la caja de la tienda ({format_amount(till)}) y no acepta "
                "lo que no salió de la tienda ni lo numerado."
            ),
        )
        embed.add_field(
            name=f"🎟️ Tus papeletas ({len(tickets)}/{MAX_TICKETS})",
            value="\n".join(ticket_line(t) for t in tickets)[:1024] or "Ninguna.",
            inline=False,
        )
        if not goods:
            value = (
                "No tienes nada que se pueda vender: solo coleccionables y tapetes. Pásate "
                "por la `tienda`."
            )
        else:
            value = f"Puedes vender **{len(goods)}** cosas."
            if len(goods) > MAX_OPTIONS:
                value += f" En la lista salen las {MAX_OPTIONS} primeras por orden de nombre."
        choice = next((a for a in goods if a.entry.id == selected), None)
        if choice is not None:
            reference, _level, _per_user = await self.repository.reference(
                guild_id, choice.entry.item_id
            )
            value += (
                f"\n\n**{entry_label(choice.entry)}**\n> 🏷️ Valor de referencia en el Rastro: "
                f"**{format_amount(reference)}**"
            )
            if (deal := choice.offer) is not None:
                value += (
                    f"\n> 💰 El Monte te lo compra por **{format_amount(deal.sale)}** (te costó "
                    f"{format_amount(deal.paid)})\n> 🏷️ Te presta **{format_amount(deal.loan)}** "
                    f"y devuelves **{format_amount(deal.repay)}**"
                )
            else:
                value += f"\n> 🚫 El Monte no lo acepta: {choice.refusal}"
        embed.add_field(name="🎒 Tu mochila", value=value[:1024], inline=False)
        return embed, goods, tickets

    async def open_panel(
        self, guild: discord.Guild, owner: discord.abc.User, wanted: str | None
    ) -> VenderPanel:
        """Prepara el panel de `vender`, con `wanted` ya elegido si se encuentra."""
        panel = VenderPanel(self, guild.id, owner)
        if wanted:
            found = await self.monte.appraisals(guild.id, owner.id, self.clock())
            entry = pick_entry([a.entry for a in found], wanted)
            if isinstance(entry, str):
                panel.notice = entry
            else:
                panel.selected = entry.id
        return panel

    # -- Tablón ------------------------------------------------------------------------

    def board(self, guild_id: int, viewer_id: int, page: int) -> dict[str, object]:
        """El tablón del Rastro (`rastro`): embed y botones de una página."""
        listings = sorted(
            (item for item in self.listings.values() if item.guild_id == guild_id),
            key=lambda item: (item.deadline, item.id),
        )
        guild = self.bot.get_guild(guild_id)
        pages = max(1, -(-len(listings) // PAGE_SIZE))
        page = min(max(page, 0), pages - 1)
        shown = listings[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

        def seller_name(user_id: int) -> str:
            member = guild.get_member(user_id) if guild else None
            return _name(member) if member else f"<@{user_id}>"

        if shown:
            body = "\n".join(listing_line(item, seller_name(item.seller_id)) for item in shown)
        else:
            body = (
                "No hay ni una manta en el suelo. Monta tu puesto con `vender` (sin "
                "comprador) y que pujen."
            )
        embed = discord.Embed(title=f"🧺 {MARKET_NAME}", description=body, color=COLOR)
        embed.set_footer(
            text=(
                f"Página {page + 1}/{pages} · ITP {str(ITP_RATE * 100).replace('.', ',')} % "
                "para quien compra · plusvalía del 19 % para quien vende"
            )
        )
        view = ui.View(timeout=None)
        for item in shown:
            view.add_item(BidButton(item.id, row=0))
        for item in shown:
            if item.seller_id == viewer_id and item.top_bidder is None:
                view.add_item(WithdrawButton(item.id, row=1))
        if pages > 1:
            for label, target, disabled in (
                ("◀", page - 1, page == 0),
                ("▶", page + 1, page == pages - 1),
            ):
                view.add_item(PageButton(target, label=label, disabled=disabled))
        return {"embed": embed, "view": view}

    # -- Comandos ----------------------------------------------------------------------

    @app_commands.command(
        name="vender",
        description="Vende un coleccionable o un tapete: a alguien, en el Rastro o al Monte.",
    )
    @app_commands.describe(
        objeto="Lo que vendes, de tu mochila. Sin precio, abre el panel con esto elegido.",
        precio="Cuánto pides (5000, 5k…). A una persona, 0 o «regalo» para regalarlo.",
        comprador="A quién se lo vendes. Sin nadie, va a un puesto del Rastro y se subasta.",
    )
    @app_commands.guild_only()
    async def vender(
        self,
        interaction: discord.Interaction,
        objeto: str | None = None,
        precio: str | None = None,
        comprador: discord.Member | None = None,
    ) -> None:
        """Sin objeto o sin precio, abre tu panel privado de `vender`. Con los dos, hace
        una oferta de venta directa o monta un puesto en el Rastro (público)."""
        assert interaction.guild is not None  # guild_only
        if objeto is None or precio is None:
            await self._open_panel_slash(interaction, objeto)
            return
        content, embed, view = await self.sell(
            interaction.guild, interaction.channel, interaction.user, objeto, precio, comprador
        )
        if embed is None:
            await interaction.response.send_message(content or "No se pudo.", ephemeral=True)
            return
        mentions = [comprador] if comprador else []
        await interaction.response.send_message(
            content,
            embed=embed,
            view=view or discord.utils.MISSING,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=mentions),
        )
        if isinstance(view, OfferView):
            view.message = await interaction.original_response()
        await self.offered(interaction.guild.id, interaction.user, interaction.channel, view)

    async def _open_panel_slash(self, interaction: discord.Interaction, wanted: str | None) -> None:
        """Abre el panel privado. Leer la mochila puede tardar: se difiere."""
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            panel = await self.open_panel(interaction.guild, interaction.user, wanted)
            kwargs = await panel.screen()
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo abrir el panel de vender en %s", interaction.guild.id)
            await interaction.followup.send("No se puede vender ahora mismo.", ephemeral=True)
            return
        panel.message = await interaction.followup.send(ephemeral=True, wait=True, **kwargs)

    @vender.autocomplete("objeto")
    async def _objeto_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        entries = await self.sellable_entries(interaction.guild.id, interaction.user.id)
        needle = current.casefold()
        return [
            app_commands.Choice(name=entry_label(entry)[:100], value=str(entry.id))
            for entry in entries
            if needle in entry.name.casefold()
        ][:25]

    @commands.command(name="vender")
    @commands.guild_only()
    async def vender_text(
        self,
        ctx: commands.Context,
        precio: str | None = None,
        comprador: discord.Member | None = None,
        *,
        objeto: str | None = None,
    ) -> None:
        """Versión de texto: `.vender 5k @miembro Marquesado` o `.vender 5k Marquesado`.

        `.vender` solo abre el panel en el canal (lo maneja quien lo pide), y
        `.vender Marquesado`, con eso ya elegido.
        """
        assert ctx.guild is not None
        if objeto is None:
            try:
                panel = await self.open_panel(ctx.guild, ctx.author, precio)
                kwargs = await panel.screen()
            except (OSError, sqlite3.Error):
                logger.exception("No se pudo abrir el panel de vender en %s", ctx.guild.id)
                await ctx.reply("No se puede vender ahora mismo.", mention_author=False)
                return
            panel.message = await ctx.send(**kwargs)  # type: ignore[arg-type]
            return
        assert precio is not None  # con objeto, el precio ya llegó delante
        content, embed, view = await self.sell(
            ctx.guild, ctx.channel, ctx.author, objeto, precio, comprador
        )
        if embed is None:
            await ctx.reply(content or "No se pudo.", mention_author=False)
            return
        mentions = [comprador] if comprador else []
        message = await ctx.send(
            content,
            embed=embed,
            view=view,  # type: ignore[arg-type]
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=mentions),
        )
        if isinstance(view, OfferView):
            view.message = message
        await self.offered(ctx.guild.id, ctx.author, ctx.channel, view)

    @app_commands.command(
        name="trueque", description="Cambia un objeto de tu mochila por uno de la de otro."
    )
    @app_commands.guild_only()
    @app_commands.describe(
        objeto="Lo que das de tu mochila (coleccionables y tapetes).",
        miembro="Con quién lo cambias.",
        a_cambio="Lo que quieres de su mochila.",
        dinero="Dinero que añades tú (por defecto, nada).",
    )
    async def trueque(
        self,
        interaction: discord.Interaction,
        objeto: str,
        miembro: discord.Member,
        a_cambio: str,
        dinero: str | None = None,
    ) -> None:
        """Propone un trueque en el canal. Público: menciona a quien lo recibe."""
        assert interaction.guild is not None  # guild_only
        content, embed, view = await self.barter(
            interaction.guild, interaction.user, miembro, objeto, a_cambio, dinero
        )
        if embed is None:
            await interaction.response.send_message(content or "No se pudo.", ephemeral=True)
            return
        await interaction.response.send_message(
            content,
            embed=embed,
            view=view or discord.utils.MISSING,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[miembro]),
        )
        if isinstance(view, SwapView):
            view.message = await interaction.original_response()

    @trueque.autocomplete("objeto")
    async def _trueque_mine_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._objeto_autocomplete(interaction, current)

    @trueque.autocomplete("a_cambio")
    async def _trueque_theirs_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Lo que se puede pedir de la mochila del miembro ya elegido."""
        member = getattr(interaction.namespace, "miembro", None)
        if interaction.guild is None or member is None:
            return []
        entries = await self.sellable_entries(interaction.guild.id, member.id)
        needle = current.casefold()
        return [
            app_commands.Choice(name=entry_label(entry)[:100], value=str(entry.id))
            for entry in entries
            if needle in entry.name.casefold()
        ][:25]

    @commands.command(name="trueque")
    @commands.guild_only()
    async def trueque_text(
        self,
        ctx: commands.Context,
        miembro: discord.Member,
        objeto: str,
        a_cambio: str,
        dinero: str | None = None,
    ) -> None:
        """Versión de texto: `.trueque @miembro "tu objeto" "su objeto" [dinero]`."""
        assert ctx.guild is not None
        content, embed, view = await self.barter(
            ctx.guild, ctx.author, miembro, objeto, a_cambio, dinero
        )
        if embed is None:
            await ctx.reply(content or "No se pudo.", mention_author=False)
            return
        message = await ctx.send(
            content,
            embed=embed,
            view=view,  # type: ignore[arg-type]
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[miembro]),
        )
        if isinstance(view, SwapView):
            view.message = message

    @app_commands.command(name="rastro", description="Mira los puestos del Rastro y puja.")
    @app_commands.guild_only()
    async def rastro(self, interaction: discord.Interaction) -> None:
        """Enseña el tablón del Rastro. Solo lo ve quien lo pide (sale de memoria)."""
        assert interaction.guild is not None
        await interaction.response.send_message(
            ephemeral=True,
            **self.board(interaction.guild.id, interaction.user.id, 0),  # type: ignore[arg-type]
        )

    @commands.command(name="rastro")
    @commands.guild_only()
    async def rastro_text(self, ctx: commands.Context) -> None:
        """Versión de texto: enseña el tablón del Rastro en el canal."""
        assert ctx.guild is not None
        await ctx.send(**self.board(ctx.guild.id, ctx.author.id, 0))  # type: ignore[arg-type]

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra el Rastro y el Monte del servidor que deja el bot."""
        for listing_id in [i for i, item in self.listings.items() if item.guild_id == guild.id]:
            self.listings.pop(listing_id, None)
        for ticket_id in [i for i, t in self.tickets.items() if t.guild_id == guild.id]:
            self.tickets.pop(ticket_id, None)
        await self.repository.delete_guild_data(guild.id)
        await self.monte.delete_guild_data(guild.id)


def _bid_view(listing_id: int) -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(BidButton(listing_id))
    return view


class OfferView(ui.View):
    """Oferta de venta directa: 🤝 y ✖️ para quien compra, ↩️ para quien vende."""

    def __init__(
        self,
        cog: Rastro,
        guild_id: int,
        seller: discord.abc.User,
        buyer: discord.abc.User,
        entry: InventoryEntry,
        price: int,
    ) -> None:
        super().__init__(timeout=OFFER_SECONDS)
        self.cog = cog
        self.guild_id = guild_id
        self.seller = seller
        self.buyer = buyer
        self.entry_id = entry.id
        self.item_id = entry.item_id
        self.label = entry_label(entry)
        self.price = price
        #: Mensaje de la oferta, para apagarla al caducar.
        self.message: discord.Message | None = None
        #: Hay un 🤝 en marcha: un segundo clic no puede cobrar dos veces.
        self.busy = False

    async def _close(self, interaction: discord.Interaction, text: str) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(description=text, color=COLOR),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ui.button(
        label="Aceptar", emoji="🤝", style=discord.ButtonStyle.success, custom_id="rastro:oferta:si"
    )
    async def accept(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.buyer.id:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)
            return
        if self.busy:
            await interaction.response.send_message(
                "Un momento, que lo está firmando.", ephemeral=True
            )
            return
        self.busy = True
        await ack(interaction)
        try:
            if await self.cog.accept_offer(interaction, self):
                self.stop()
        finally:
            self.busy = False

    @ui.button(
        label="Rechazar",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="rastro:oferta:no",
    )
    async def reject(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.buyer.id:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)
            return
        await self._close(
            interaction, f"✖️ {_name(self.buyer)} no quiere **{self.label}**. ¡Qué desprecio!"
        )

    @ui.button(
        label="Retirar",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="rastro:oferta:retirar",
    )
    async def withdraw(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.seller.id:
            await interaction.response.send_message("Esa oferta no es tuya.", ephemeral=True)
            return
        await self._close(interaction, f"↩️ {_name(self.seller)} se lo ha pensado mejor.")

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(
                content=None,
                embed=discord.Embed(
                    description=f"⌛ La oferta de **{self.label}** ha caducado.", color=COLOR
                ),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass


class SwapView(ui.View):
    """Oferta de trueque: 🤝 y ✖️ para quien la recibe, ↩️ para quien la hace."""

    def __init__(
        self,
        cog: Rastro,
        guild_id: int,
        proposer: discord.abc.User,
        target: discord.abc.User,
        given: InventoryEntry,
        received: InventoryEntry,
        money: int,
    ) -> None:
        super().__init__(timeout=OFFER_SECONDS)
        self.cog = cog
        self.guild_id = guild_id
        self.proposer = proposer
        self.target = target
        self.given_entry, self.given_item = given.id, given.item_id
        self.received_entry, self.received_item = received.id, received.item_id
        self.given_label = entry_label(given)
        self.received_label = entry_label(received)
        self.money = money
        #: Mensaje de la oferta, para apagarla al caducar.
        self.message: discord.Message | None = None
        #: Hay un 🤝 en marcha: un segundo clic no puede cambiar dos veces.
        self.busy = False

    async def _close(self, interaction: discord.Interaction, text: str) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(description=text, color=COLOR),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ui.button(
        label="Aceptar",
        emoji="🤝",
        style=discord.ButtonStyle.success,
        custom_id="rastro:trueque:si",
    )
    async def accept(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("Ese trueque no es para ti.", ephemeral=True)
            return
        if self.busy:
            await interaction.response.send_message(
                "Un momento, que lo está firmando.", ephemeral=True
            )
            return
        self.busy = True
        await ack(interaction)
        try:
            if await self.cog.accept_swap(interaction, self):
                self.stop()
        finally:
            self.busy = False

    @ui.button(
        label="Rechazar",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="rastro:trueque:no",
    )
    async def reject(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("Ese trueque no es para ti.", ephemeral=True)
            return
        await self._close(
            interaction,
            f"✖️ {_name(self.target)} no cambia **{self.received_label}** por "
            f"**{self.given_label}**. Ni con el Falcon de regalo.",
        )

    @ui.button(
        label="Retirar",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="rastro:trueque:retirar",
    )
    async def withdraw(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.proposer.id:
            await interaction.response.send_message("Ese trueque no es tuyo.", ephemeral=True)
            return
        await self._close(interaction, f"↩️ {_name(self.proposer)} se lo ha pensado mejor.")

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(
                content=None,
                embed=discord.Embed(
                    description=f"⌛ El trueque de **{self.given_label}** ha caducado.",
                    color=COLOR,
                ),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass


class BidForm(ui.Modal):
    """Formulario 🔨: cuánto se puja (por defecto, lo mínimo)."""

    def __init__(self, cog: Rastro, listing: Listing) -> None:
        super().__init__(title=f"🔨 Pujar por #{listing.id}"[:45])
        self.cog = cog
        self.listing_id = listing.id
        self.amount: ui.TextInput = ui.TextInput(
            label=f"Tu puja (mínimo {format_amount(listing.next_bid)})"[:45],
            default=str(listing.next_bid),
            max_length=20,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.place_bid(interaction, self.listing_id, self.amount.value)


class BidButton(ui.DynamicItem[ui.Button], template=r"rastro:pujar:(?P<id>\d+)"):
    """🔨 Pujar por un puesto. Sigue valiendo tras reiniciar el bot."""

    def __init__(self, listing_id: int, *, row: int | None = None) -> None:
        super().__init__(
            ui.Button(
                label=f"#{listing_id}",
                emoji="🔨",
                style=discord.ButtonStyle.primary,
                custom_id=f"rastro:pujar:{listing_id}",
                row=row,
            )
        )
        self.listing_id = listing_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /
    ) -> BidButton:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if (cog := find_cog(interaction.client, Rastro)) is not None:  # type: ignore[arg-type]
            await cog.open_bid_form(interaction, self.listing_id)


class WithdrawButton(ui.DynamicItem[ui.Button], template=r"rastro:retirar:(?P<id>\d+)"):
    """↩️ Retirar un puesto propio sin pujas."""

    def __init__(self, listing_id: int, *, row: int | None = None) -> None:
        super().__init__(
            ui.Button(
                label=f"#{listing_id}",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"rastro:retirar:{listing_id}",
                row=row,
            )
        )
        self.listing_id = listing_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /
    ) -> WithdrawButton:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if (cog := find_cog(interaction.client, Rastro)) is not None:  # type: ignore[arg-type]
            await cog.withdraw(interaction, self.listing_id)


class PageButton(ui.DynamicItem[ui.Button], template=r"rastro:pag:(?P<page>-?\d+)"):
    """◀ ▶ del tablón. Solo cambia memoria: contesta directamente."""

    def __init__(self, page: int, *, label: str = "▶", disabled: bool = False) -> None:
        super().__init__(
            ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"rastro:pag:{page}",
                disabled=disabled,
                row=2,
            )
        )
        self.page = page

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /
    ) -> PageButton:
        return cls(int(match["page"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Rastro)  # type: ignore[arg-type]
        if cog is None or interaction.guild is None:
            return
        await interaction.response.edit_message(
            **cog.board(interaction.guild.id, interaction.user.id, self.page)  # type: ignore[arg-type]
        )


# -- Panel de vender -------------------------------------------------------------------


class VenderPanel(ui.View):
    """El panel «🧺 Vender» de un miembro: su mochila, cuatro destinos y sus papeletas."""

    def __init__(self, cog: Rastro, guild_id: int, owner: discord.abc.User) -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        #: Objeto elegido de la mochila (id de la entrada).
        self.selected: int | None = None
        #: Lo que se acaba de hacer, encima del panel.
        self.notice: str | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Este panel no es tuyo: abre el tuyo con `vender`.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            pass

    async def screen(self) -> dict[str, object]:
        """El embed y los botones del panel, leídos de nuevo."""
        embed, goods, tickets = await self.cog.panel_embed(
            self.guild_id, self.owner.id, self.selected
        )
        choice = next((a for a in goods if a.entry.id == self.selected), None)
        if choice is None:
            self.selected = None
        self.clear_items()
        if goods:
            shown = goods[:MAX_OPTIONS]
            if choice is not None and choice not in shown:
                shown = [*goods[: MAX_OPTIONS - 1], choice]
            self.add_item(ItemSelect(shown, self.selected))
        offer = choice.offer if choice is not None else None
        self.add_item(MemberButton(disabled=choice is None))
        self.add_item(StallButton(disabled=choice is None))
        self.add_item(MonteSellButton(offer.sale if offer else None))
        self.add_item(PawnButton(offer.loan if offer else None, len(tickets) >= MAX_TICKETS))
        for ticket in tickets[:MAX_TICKETS]:
            self.add_item(RedeemButton(ticket.id, ticket.repay))
        content, self.notice = self.notice, None
        return {
            "content": content,
            "embed": embed,
            "view": self,
            "allowed_mentions": discord.AllowedMentions.none(),
        }

    async def refresh(self, interaction: discord.Interaction) -> None:
        """Vuelve a pintar el panel (ya con `ack`)."""
        try:
            kwargs = await self.screen()
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer el panel de vender de %s", self.owner.id)
            await notify(interaction, "No se puede vender ahora mismo. Prueba en un rato.")
            return
        await edit(interaction, **kwargs)

    async def money(
        self, interaction: discord.Interaction, run: Callable[[], Awaitable[Outcome]]
    ) -> None:
        """Hace una operación del Monte (una a la vez por miembro) y repinta con el
        resultado. Los logros y el aviso de la Renta van después de contestar."""
        key = (self.guild_id, self.owner.id)
        if key in self.cog.working:
            await interaction.response.send_message(
                "Un momento, que el Monte está con lo anterior.", ephemeral=True
            )
            return
        self.cog.working.add(key)
        await ack(interaction)
        try:
            self.notice, delta = await run()
        finally:
            self.cog.working.discard(key)
        await self.refresh(interaction)
        await renta.remind(self.cog.bot, interaction)
        if delta is not None:
            await logros.track(self.cog.bot, self.guild_id, self.owner, interaction.channel, delta)

    async def publish(
        self, interaction: discord.Interaction, price_text: str, buyer: discord.abc.User | None
    ) -> None:
        """Desde un formulario: publica en el canal la oferta a `buyer` o, sin él, monta el
        puesto, igual que el atajo de `vender`, y repinta el panel con un aviso."""
        await ack(interaction)
        guild = interaction.guild
        if guild is None or self.selected is None:
            await notify(interaction, "Elige antes algo de tu mochila.")
            return
        content, embed, view = await self.cog.sell(
            guild, interaction.channel, interaction.user, str(self.selected), price_text, buyer
        )
        if embed is None:
            self.notice = content or "No se pudo."
            await self.refresh(interaction)
            return
        message = await interaction.followup.send(
            content or discord.utils.MISSING,
            embed=embed,
            view=view or discord.utils.MISSING,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[buyer] if buyer else []
            ),
            wait=True,
        )
        if isinstance(view, OfferView):
            view.message = message
        self.selected = None
        self.notice = (
            f"🤝 Oferta publicada en el canal para {_name(buyer)}."
            if buyer is not None
            else "🧺 Puesto montado: el anuncio está en el canal."
        )
        await self.refresh(interaction)
        await self.cog.offered(guild.id, interaction.user, interaction.channel, view)


class ItemSelect(ui.Select["VenderPanel"]):
    """Elige un objeto de la mochila."""

    def __init__(self, goods: list[Appraisal], selected: int | None) -> None:
        options = []
        for found in goods:
            deal = found.offer
            options.append(
                discord.SelectOption(
                    label=entry_label(found.entry)[:100],
                    description=(
                        f"Monte: vender {format_amount(deal.sale)} · empeñar "
                        f"{format_amount(deal.loan)}"
                        if deal is not None
                        else "Solo a un miembro o en el Rastro"
                    )[:100],
                    value=str(found.entry.id),
                    default=found.entry.id == selected,
                )
            )
        super().__init__(
            placeholder="Elige algo de tu mochila", options=options, custom_id="vender:objeto",
            row=0,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await ack(interaction)
        panel.selected = int(self.values[0])
        await panel.refresh(interaction)


class MemberForm(ui.Modal):
    """🤝 A quién y por cuánto (0 o «regalo» para regalarlo)."""

    def __init__(self, panel: VenderPanel) -> None:
        super().__init__(title="🤝 Vender a un miembro", timeout=PANEL_TIMEOUT)
        self.panel = panel
        self.buyer: ui.UserSelect = ui.UserSelect(
            placeholder="Elige a quién", custom_id="vender:form:comprador", required=True
        )
        self.price: ui.TextInput = ui.TextInput(
            placeholder="5000, 5k… o 0 para regalarlo", max_length=20, required=True
        )
        self.add_item(ui.Label(text="A quién", component=self.buyer))
        self.add_item(ui.Label(text="Precio", component=self.price))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.buyer.values:
            await interaction.response.send_message("Elige a quién se lo vendes.", ephemeral=True)
            return
        await self.panel.publish(interaction, self.price.value, self.buyer.values[0])


class StallForm(ui.Modal):
    """🧺 El precio de salida del puesto."""

    def __init__(self, panel: VenderPanel) -> None:
        super().__init__(title="🧺 Poner en el Rastro", timeout=PANEL_TIMEOUT)
        self.panel = panel
        self.price: ui.TextInput = ui.TextInput(
            placeholder="5000, 5k…", max_length=20, required=True
        )
        self.add_item(ui.Label(text="Precio de salida", component=self.price))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.publish(interaction, self.price.value, None)


class MemberButton(ui.Button["VenderPanel"]):
    """🤝 A un miembro: abre el formulario (solo mira memoria antes)."""

    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="A un miembro", emoji="🤝", style=discord.ButtonStyle.primary,
            custom_id="vender:miembro", row=1, disabled=disabled,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        if panel.selected is None:
            await interaction.response.send_message(
                "Elige antes algo de tu mochila.", ephemeral=True
            )
            return
        await interaction.response.send_modal(MemberForm(panel))


class StallButton(ui.Button["VenderPanel"]):
    """🧺 Al Rastro: abre el formulario del precio de salida."""

    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="Al Rastro", emoji="🧺", style=discord.ButtonStyle.primary,
            custom_id="vender:rastro", row=1, disabled=disabled,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        if panel.selected is None:
            await interaction.response.send_message(
                "Elige antes algo de tu mochila.", ephemeral=True
            )
            return
        await interaction.response.send_modal(StallForm(panel))


class MonteSellButton(ui.Button["VenderPanel"]):
    """💰 Vender al Monte lo elegido."""

    def __init__(self, amount: int | None) -> None:
        label = f"Al Monte por {format_amount(amount)}" if amount is not None else "Al Monte"
        super().__init__(
            label=label[:80], emoji="💰", style=discord.ButtonStyle.danger,
            custom_id="vender:monte", row=1, disabled=amount is None,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None and panel.selected is not None
        entry, panel.selected = panel.selected, None
        await panel.money(
            interaction, lambda: panel.cog.monte_sell(interaction, panel.guild_id, entry)
        )


class PawnButton(ui.Button["VenderPanel"]):
    """🏷️ Empeñar lo elegido."""

    def __init__(self, amount: int | None, full: bool) -> None:
        label = f"Empeñar por {format_amount(amount)}" if amount is not None else "Empeñar"
        super().__init__(
            label=label[:80], emoji="🏷️", style=discord.ButtonStyle.secondary,
            custom_id="vender:empenar", row=1, disabled=amount is None or full,
        )  # fmt: skip

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None and panel.selected is not None
        entry, panel.selected = panel.selected, None
        await panel.money(
            interaction, lambda: panel.cog.monte_pawn(interaction, panel.guild_id, entry)
        )


class RedeemButton(ui.Button["VenderPanel"]):
    """🎟️ Desempeñar una papeleta."""

    def __init__(self, ticket_id: int, repay: int) -> None:
        super().__init__(
            label=f"Desempeñar #{ticket_id} ({format_amount(repay)})"[:80],
            emoji="🎟️",
            style=discord.ButtonStyle.success,
            custom_id=f"vender:desempenar:{ticket_id}",
            row=2,
        )
        self.ticket_id = ticket_id

    async def callback(self, interaction: discord.Interaction) -> None:
        panel = self.view
        assert panel is not None
        await panel.money(
            interaction,
            lambda: panel.cog.monte_redeem(interaction, panel.guild_id, self.ticket_id),
        )


def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Pujas y puestos con pujas de alguien en el Rastro y sus papeletas del Monte (para
    `saldo`), o `[]`.

    Sale de los puestos y las papeletas que el cog tiene en memoria: no toca la base de
    datos.
    """
    cog = find_cog(bot, Rastro)
    if cog is None:
        return []
    items = []
    for listing in cog.listings.values():
        if listing.guild_id != guild_id or listing.top_bid is None:
            continue
        if listing.top_bidder == user_id:
            items.append(
                Pending(
                    Side.HELD, "rastro_bid", "🧺",
                    f"Tu puja en el Rastro por {listing.label}: "
                    f"{format_amount(listing.top_escrow or listing.top_bid)} con ITP",
                    listing.top_escrow or listing.top_bid, listing.close_at,
                )
            )  # fmt: skip
        elif listing.seller_id == user_id:
            items.append(
                Pending(
                    Side.COLLECT, "rastro_sale", "🧺",
                    f"Tu puesto del Rastro ({listing.label}) va por "
                    f"{format_amount(listing.top_bid)}",
                    listing.top_bid, listing.close_at,
                )
            )  # fmt: skip
    items += [
        Pending(
            Side.PAY,
            "monte_ticket",
            "🏷️",
            f"Papeleta del Monte ({ticket.label}): devuelve {format_amount(ticket.repay)} "
            "para recuperarlo",
            ticket.repay,
            ticket.due_at,
        )  # fmt: skip
        for ticket in cog.tickets_of(guild_id, user_id)
    ]
    return items


def floor(bot: commands.Bot, guild_id: int, item_id: int) -> tuple[int, float] | None:
    """La puja más baja que se puede hacer ahora por un artículo en el Rastro, y cuándo
    cierra ese puesto; `None` si nadie lo vende. Para la ficha de la tienda.

    Sale de los puestos que el cog tiene en memoria: no toca la base de datos.
    """
    cog = find_cog(bot, Rastro)
    if cog is None:
        return None
    stalls = [
        listing
        for listing in cog.listings.values()
        if listing.guild_id == guild_id and listing.item_id == item_id
    ]
    if not stalls:
        return None
    best = min(stalls, key=lambda listing: listing.next_bid)
    return best.next_bid, best.deadline


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, el Rastro, la tienda, el Monte y los niveles del bot."""
    await bot.add_cog(
        Rastro(bot, bot.economy, bot.rastro, bot.shop, bot.monte, levels=bot.message_stats)
    )
