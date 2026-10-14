"""El Rastro: `vender` y `rastro`, la segunda mano entre miembros.

- `vender <objeto> <precio> [comprador]`: con comprador, le hace una oferta de venta
  directa (botones 🤝 Aceptar y ✖️ Rechazar, solo para él; ↩️ Retirar, para quien
  vende) que caduca a los diez minutos. El precio puede ser 0 («de regalo»). Sin
  comprador, monta un puesto en el Rastro con ese precio de salida: el objeto sale
  de la mochila y cualquiera puede pujar con 🔨 Pujar. Si dos o más lo quieren, es
  una subasta: cada puja sube al menos un 5 % y da una hora más.
- `rastro`: el tablón con los puestos abiertos del servidor, sus pujas y cuándo se
  adjudican, con 🔨 para pujar y ↩️ para retirar los tuyos sin pujas.

Reglas y fiscalidad en `bot.services.rastro`: solo se venden coleccionables y
tapetes; quien compra tiene que tener el nivel mínimo del artículo y no pasarse de
su límite por persona; paga el 5,5 % de ITP sobre el mayor entre precio y valor de
referencia; la parte regalada está exenta, como el Bizum; y quien vende paga el
19 % de la plusvalía. El dinero pasa por `EconomyService.market`.

Los puestos abiertos se guardan en memoria (`listings`) para que los botones de
pujar abran su formulario sin leer la base de datos (Discord da 3 segundos). Una
tarea mira cada `CLOSE_EVERY` segundos los que han llegado a su hora, los adjudica
o devuelve el objeto, y lo anuncia en el canal donde se montó el puesto. Los botones
🔨 y ↩️ son `DynamicItem`: siguen valiendo tras reiniciar el bot.

Logros: categoría 🧺 Rastro. Pujar y comprar son acciones con dinero: llaman a
`renta.remind` y su resultado lleva `renta.hint` con la mascota (`Event.TRADE`).
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.cogs import shop as tienda
from bot.repositories.economy import BalanceLimitError, InsufficientFundsError
from bot.repositories.rastro import Listing, MarketRepository, Trade
from bot.repositories.shop import InventoryEntry, ShopRepository
from bot.services.achievements import (
    market_bid_stats,
    market_expired_stats,
    market_listing_stats,
    market_outbid_stats,
    market_purchase_stats,
    market_sale_stats,
    market_withdraw_stats,
)
from bot.services.economy import EconomyService, format_amount, parse_amount
from bot.services.levels import TIMEZONE, calculate_level_progress
from bot.services.pets import Event, Moment
from bot.services.rastro import (
    AUCTION_SECONDS,
    MARKET_NAME,
    MIN_RAISE_PCT,
    OFFER_SECONDS,
    Deal,
    MarketError,
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
#: Puestos por página del tablón (una fila de botones 🔨 y otra de ↩️).
PAGE_SIZE = 5

#: Lo que grita Jovani cuando alguien puja.
BID_CRIES = (
    "¡A la una!",
    "¡A la una, a las dos…!",
    "¿Quién da más? ¿Nadie? ¿Ni Florentino?",
    "¡Esto sube más que la luz con Sánchez!",
    "¡Más pujas que candidatos en las primarias del PSOE!",
    "¡Qué subasta, mi amor! Ni la de los pisos de la SAREB.",
    "¡Toma mangó! ¡Esto se calienta!",
    "¡La puja más alta desde el precio del aceite!",
    "¡Pujan como si Hacienda no mirara! (Mira.)",
)
#: Lo que dice al adjudicar.
SOLD_CRIES = (
    "🔨 ¡Adjudicado!",
    "🔨 ¡A la una, a las dos y a las tres: adjudicado!",
    "🔨 ¡Vendido! Más rápido que una ley en el BOE un viernes por la tarde.",
    "🔨 ¡Adjudicado! Ni Sotheby's, mi amor.",
    "🔨 ¡Adjudicado! Esto en Waterloo no se ve.",
)
#: Lo que dice si un puesto cierra sin pujas.
EXPIRED_CRIES = (
    "Nadie lo ha querido, ni regalado. Como un programa electoral de hace cuatro años.",
    "Una semana al sol y ni una puja. Ni en el Rastro de Cascorro, mi amor.",
    "Ni una oferta. Ni Milei con la motosierra lo habría rebajado tanto.",
)
#: Lo que dice al cerrar una venta directa.
DEAL_CRIES = (
    "🤝 ¡Trato hecho!",
    "🤝 ¡Trato hecho! Firmado y sin notario, como los pactos de investidura.",
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


class Rastro(commands.Cog):
    """Ventas de segunda mano entre miembros: ofertas directas y subastas."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: MarketRepository,
        shop: ShopRepository,
        *,
        levels: MessageStatsRepository | None = None,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.shop = shop
        self.levels = levels
        self.clock = clock
        self.rng = rng or random.Random()
        #: Puestos abiertos de todos los servidores, por id.
        self.listings: dict[int, Listing] = {}

    async def cog_load(self) -> None:
        """Carga los puestos abiertos, registra los botones y arranca el cierre."""
        await self.repository.initialize()
        self.listings = {listing.id: listing for listing in await self.repository.open_listings()}
        self.bot.add_dynamic_items(BidButton, WithdrawButton, PageButton)
        self._closer.start()

    async def cog_unload(self) -> None:
        """Para el cierre de puestos y suelta los botones."""
        self._closer.cancel()
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
            return "Los bots no compran nada, mi amor. Ni con el kit digital.", None, None
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
        return None, embed, view

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
                f"¡Ay, bendito! No te llega: tienes {format_amount(error.balance)} y hace falta "
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
            problem = "Ese puesto ya ha cerrado. ¡Adjudicado a otro, mi amor!"
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
                f"¡Ay, bendito! No te llega: tienes {format_amount(error.balance)} y se retiene "
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
        name="vender", description="Vende un coleccionable o un tapete a alguien o en el Rastro."
    )
    @app_commands.describe(
        objeto="Lo que vendes, de tu mochila.",
        precio="Cuánto pides (5000, 5k…). A una persona, 0 o «regalo» para regalarlo.",
        comprador="A quién se lo vendes. Sin nadie, va a un puesto del Rastro y se subasta.",
    )
    @app_commands.guild_only()
    async def vender(
        self,
        interaction: discord.Interaction,
        objeto: str,
        precio: str,
        comprador: discord.Member | None = None,
    ) -> None:
        """Hace una oferta de venta directa o monta un puesto en el Rastro. Público."""
        assert interaction.guild is not None  # guild_only
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
        precio: str,
        comprador: discord.Member | None = None,
        *,
        objeto: str,
    ) -> None:
        """Versión de texto: `.vender 5k @miembro Marquesado` o `.vender 5k Marquesado`."""
        assert ctx.guild is not None
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
        """Borra el Rastro del servidor que deja el bot."""
        for listing_id in [i for i, item in self.listings.items() if item.guild_id == guild.id]:
            self.listings.pop(listing_id, None)
        await self.repository.delete_guild_data(guild.id)


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


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, el Rastro, la tienda y los niveles del bot."""
    await bot.add_cog(Rastro(bot, bot.economy, bot.rastro, bot.shop, levels=bot.message_stats))
