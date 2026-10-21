"""Pruebas del Rastro: reglas, impuestos, ventas directas y subastas con dinero de verdad.

La economía, la tienda y el Rastro son los reales sobre un SQLite temporal. En cada
flujo se comprueba que el libro cuadra con los saldos, que el Estado recibe
exactamente el ITP y la plusvalía y que el depósito del Rastro acaba vacío.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs.rastro import (
    MemberForm,
    MonteSellButton,
    OfferView,
    PawnButton,
    Rastro,
    RedeemButton,
    StallForm,
    VenderPanel,
    pending,
    pick_entry,
)
from bot.repositories.economy import (
    MARKET_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.repositories.monte import MonteRepository
from bot.repositories.rastro import MarketRepository
from bot.repositories.shop import ShopRepository
from bot.services.achievements import (
    BY_ID,
    market_purchase_stats,
    market_sale_stats,
)
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.rastro import (
    AUCTION_SECONDS,
    LISTING_SECONDS,
    MIN_RAISE,
    Deal,
    MarketError,
    escrow,
    is_tapete,
    min_bid,
    parse_sale_price,
    untradeable,
)
from bot.services.shop import Kind, ShopItem, quote
from bot.services.skins import SKINS
from bot.services.taxes import capital_gain_tax, itp

GUILD = 1
SELLER = 10
BUYER = 20
RIVAL = 30
NOW = datetime(2026, 10, 14, 18, tzinfo=TIMEZONE).timestamp()


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# -- Reglas ----------------------------------------------------------------------------


def test_el_itp_va_sobre_el_mayor_entre_precio_y_valor() -> None:
    barato = Deal(price=100, reference=10_000, cost=0)
    caro = Deal(price=20_000, reference=10_000, cost=0)
    assert barato.itp_base == 10_000 and barato.itp == itp(10_000)
    assert caro.itp_base == 20_000 and caro.itp == itp(20_000)


def test_vender_barato_no_baja_el_itp_y_lo_regalado_queda_exento() -> None:
    regalo = Deal(price=0, reference=10_000, cost=10_000)
    assert regalo.itp == itp(10_000)
    assert regalo.gift == 10_000
    assert regalo.buyer_pays == regalo.itp
    assert regalo.gain_tax == 0 and regalo.seller_gets == 0


def test_la_plusvalia_solo_paga_si_se_gana_y_sale_de_lo_cobrado() -> None:
    gana = Deal(price=15_000, reference=10_000, cost=10_000)
    assert gana.gain == 5_000 and gana.gain_tax == capital_gain_tax(5_000) == 950
    assert gana.seller_gets == 15_000 - 950
    pierde = Deal(price=5_000, reference=10_000, cost=10_000)
    assert pierde.gain == 0 and pierde.gain_tax == 0


def test_lo_que_cuesta_a_quien_compra_incluye_el_valor_regalado_y_el_itp() -> None:
    # Arts. 35.1.b y 36 LIRPF: la parte regalada vale lo que valía y el ITP suma.
    deal = Deal(price=1, reference=10_000, cost=0)
    assert deal.buyer_cost == 10_000 + itp(10_000)


def test_cada_puja_sube_al_menos_lo_minimo() -> None:
    assert min_bid(500, None) == 500
    assert min_bid(500, 100) == 100 + MIN_RAISE
    assert min_bid(500, 10_000) == 10_500  # el 5 %
    assert escrow(1_000, 10_000) == 1_000 + itp(10_000)


def test_regalar_solo_vale_en_una_venta_directa() -> None:
    assert parse_sale_price("regalo", allow_gift=True) == 0
    assert parse_sale_price("5k", allow_gift=False) == 5_000
    with pytest.raises(ValueError):
        parse_sale_price("0", allow_gift=False)


def test_solo_se_venden_coleccionables_y_tapetes_que_se_compran() -> None:
    assert untradeable(Kind.TROPHY, None) is None
    assert untradeable(Kind.TROPHY, "verificado") is None
    for kind in (Kind.ROLE, Kind.BOOST, Kind.PET):
        assert untradeable(kind, None) is not None
    tapete = next(s for s in SKINS if is_tapete(s) and s.for_sale and not s.custom)
    assert untradeable(Kind.TROPHY, tapete.catalog_key) is None
    otra = next(s for s in SKINS if not is_tapete(s))
    assert untradeable(Kind.TROPHY, otra.catalog_key) is not None
    for skin in SKINS:
        if is_tapete(skin) and (skin.achievement or skin.free or skin.custom):
            assert untradeable(Kind.TROPHY, skin.catalog_key) is not None


# -- Con dinero de verdad --------------------------------------------------------------


async def make_stores(
    tmp_path: Path, clock: Clock
) -> tuple[EconomyService, ShopRepository, MarketRepository]:
    db = tmp_path / "bot.db"
    economy_repo = EconomyRepository(db, starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    shop = ShopRepository(db)
    await shop.initialize()
    market = MarketRepository(db)
    await market.initialize()
    return EconomyService(economy_repo, clock=clock), shop, market


async def give(economy: EconomyService, user_id: int, amount: int) -> None:
    await economy.repository.apply(GUILD, user_id, [LedgerEntry(amount, "test")])


async def stocked_item(shop: ShopRepository, price: int = 10_000, **fields: object) -> ShopItem:
    it = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Yate", emoji="🛥️", description="", price=price,
        igic="general", now=NOW,
    )  # fmt: skip
    if fields:
        it = await shop.update_item(GUILD, it.id, **fields)
    return it


async def bought(economy: EconomyService, shop: ShopRepository, it: ShopItem, user_id: int) -> int:
    """Compra `it` en la tienda para `user_id` y devuelve la entrada de su mochila."""
    price = quote(it, NOW)
    await give(economy, user_id, price.total)
    await economy.purchase(
        GUILD, user_id, base=price.base, tax=price.tax, concept="objeto",
        reserve=shop.reserve(GUILD, user_id, it.id, expected=price, level=99, now=NOW),
    )  # fmt: skip
    entries = await shop.inventory(GUILD, user_id, NOW)
    return max(e.id for e in entries if e.item_id == it.id)


def rows(tmp_path: Path, sql: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(sql, args).fetchall()


def assert_books_balance(tmp_path: Path) -> None:
    """Cada monedero coincide con la suma de su libro."""
    mismatches = rows(
        tmp_path,
        """
        SELECT w.user_id, w.balance, COALESCE(SUM(l.delta), 0) FROM economy_wallets w
        LEFT JOIN economy_ledger l ON l.guild_id = w.guild_id AND l.user_id = w.user_id
        GROUP BY w.guild_id, w.user_id HAVING w.balance != COALESCE(SUM(l.delta), 0)
        """,
    )
    assert mismatches == []


async def owner_of(shop: ShopRepository, entry_id: int) -> int | None:
    for user in (SELLER, BUYER, RIVAL, MARKET_ACCOUNT_ID):
        if any(e.id == entry_id for e in await shop.inventory(GUILD, user, NOW)):
            return user
    return None


async def test_venta_directa_barata_paga_itp_sobre_el_valor_y_va_al_estado(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop)
    entry = await bought(economy, shop, it, SELLER)
    await give(economy, BUYER, 5_000)
    state = await economy.state_balance(GUILD)
    seller_before = await economy.balance(GUILD, SELLER)
    buyer_before = await economy.balance(GUILD, BUYER)

    trade, balances = await economy.market(
        GUILD, market.sell(GUILD, SELLER, BUYER, entry, 300, NOW)
    )

    tax = itp(10_000)
    assert trade.deal is not None and trade.deal.itp == tax and trade.deal.gain_tax == 0
    assert balances[BUYER] == buyer_before - 300 - tax
    assert balances[SELLER] == seller_before + 300
    assert await economy.state_balance(GUILD) == state + tax
    assert await owner_of(shop, entry) == BUYER
    assert_books_balance(tmp_path)
    breakdown = await economy.repository.tax_breakdown(GUILD)
    assert breakdown[BUYER]["itp"] == tax


async def test_vender_con_ganancia_paga_el_19_por_ciento_de_plusvalia(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop)
    entry = await bought(economy, shop, it, SELLER)
    await give(economy, BUYER, 50_000)
    state = await economy.state_balance(GUILD)

    trade, _balances = await economy.market(
        GUILD, market.sell(GUILD, SELLER, BUYER, entry, 20_000, NOW)
    )

    deal = trade.deal
    assert deal is not None and deal.cost == 10_000
    assert deal.gain_tax == capital_gain_tax(10_000)
    assert await economy.state_balance(GUILD) == state + itp(20_000) + deal.gain_tax
    breakdown = await economy.repository.tax_breakdown(GUILD)
    assert breakdown[SELLER]["irpf_ahorro"] == deal.gain_tax
    assert_books_balance(tmp_path)


async def test_quien_compra_regalado_no_paga_plusvalia_al_revender_a_su_valor(
    tmp_path: Path,
) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop)
    entry = await bought(economy, shop, it, SELLER)
    await give(economy, BUYER, 50_000)
    await give(economy, RIVAL, 50_000)
    await economy.market(GUILD, market.sell(GUILD, SELLER, BUYER, entry, 0, NOW))

    trade, _ = await economy.market(GUILD, market.sell(GUILD, BUYER, RIVAL, entry, 10_000, NOW))

    assert trade.deal is not None and trade.deal.gain_tax == 0


async def test_si_no_llega_no_se_mueve_ni_el_objeto_ni_el_dinero(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop, price=100_000)
    entry = await bought(economy, shop, it, SELLER)
    state = await economy.state_balance(GUILD)

    with pytest.raises(InsufficientFundsError):
        await economy.market(GUILD, market.sell(GUILD, SELLER, BUYER, entry, 90_000, NOW))

    assert await owner_of(shop, entry) == SELLER
    assert await economy.state_balance(GUILD) == state
    assert rows(tmp_path, "SELECT COUNT(*) FROM market_sales") == [(0,)]


async def test_no_se_puede_pasar_del_limite_por_persona(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop, per_user=1)
    entry = await bought(economy, shop, it, SELLER)
    await bought(economy, shop, it, BUYER)

    with pytest.raises(MarketError):
        await economy.market(GUILD, market.sell(GUILD, SELLER, BUYER, entry, 100, NOW))
    assert await owner_of(shop, entry) == SELLER


async def test_las_mascotas_y_los_roles_no_se_venden(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.BOOST, name="Café", emoji="☕", description="", price=100,
        igic="general", now=NOW, duration=3600, multiplier=150,
    )  # fmt: skip
    entry = await bought(economy, shop, it, SELLER)
    with pytest.raises(MarketError):
        await economy.market(GUILD, market.sell(GUILD, SELLER, BUYER, entry, 100, NOW))
    with pytest.raises(MarketError):
        await market.open_stall(GUILD, SELLER, entry, start_price=100, channel_id=None, now=NOW)


async def test_subasta_completa_devuelve_al_superado_y_adjudica_al_mejor(tmp_path: Path) -> None:
    clock = Clock()
    economy, shop, market = await make_stores(tmp_path, clock)
    it = await stocked_item(shop)
    entry = await bought(economy, shop, it, SELLER)
    await give(economy, BUYER, 50_000)
    await give(economy, RIVAL, 50_000)
    buyer_before = await economy.balance(GUILD, BUYER)
    rival_before = await economy.balance(GUILD, RIVAL)
    seller_before = await economy.balance(GUILD, SELLER)
    state = await economy.state_balance(GUILD)

    listing = await market.open_stall(
        GUILD, SELLER, entry, start_price=8_000, channel_id=5, now=NOW
    )
    assert await owner_of(shop, entry) == MARKET_ACCOUNT_ID
    assert listing.expires_at == NOW + LISTING_SECONDS

    first, _ = await economy.market(GUILD, market.bid(GUILD, listing.id, BUYER, 8_000, NOW))
    assert first.itp == itp(10_000)
    raise_to = first.listing.next_bid
    second, balances = await economy.market(
        GUILD, market.bid(GUILD, listing.id, RIVAL, raise_to, NOW + 60)
    )
    assert second.outbid == BUYER and second.outbid_amount == 8_000
    assert balances[BUYER] == buyer_before  # devuelto entero, ITP incluido
    assert await economy.balance(GUILD, MARKET_ACCOUNT_ID) == escrow(raise_to, 10_000)
    assert second.listing.close_at == NOW + 60 + AUCTION_SECONDS

    with pytest.raises(MarketError):
        await economy.market(GUILD, market.close(GUILD, listing.id, NOW + 120))

    trade, _ = await economy.market(
        GUILD, market.close(GUILD, listing.id, NOW + 60 + AUCTION_SECONDS)
    )

    deal = trade.deal
    assert deal is not None and trade.buyer_id == RIVAL and deal.price == raise_to
    assert await owner_of(shop, entry) == RIVAL
    assert await economy.balance(GUILD, MARKET_ACCOUNT_ID) == 0
    assert await economy.balance(GUILD, RIVAL) == rival_before - raise_to - deal.itp
    assert await economy.balance(GUILD, SELLER) == seller_before + deal.seller_gets
    assert await economy.state_balance(GUILD) == state + deal.itp + deal.gain_tax
    assert_books_balance(tmp_path)


async def test_pujas_que_no_valen(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop)
    entry = await bought(economy, shop, it, SELLER)
    await give(economy, BUYER, 50_000)
    listing = await market.open_stall(
        GUILD, SELLER, entry, start_price=5_000, channel_id=None, now=NOW
    )

    for bidder, amount in ((BUYER, 4_999), (SELLER, 6_000)):
        with pytest.raises(MarketError):
            await economy.market(GUILD, market.bid(GUILD, listing.id, bidder, amount, NOW))
    await economy.market(GUILD, market.bid(GUILD, listing.id, BUYER, 5_000, NOW))
    with pytest.raises(MarketError):  # ya va ganando
        await economy.market(GUILD, market.bid(GUILD, listing.id, BUYER, 9_000, NOW))
    with pytest.raises(MarketError):  # con pujas no se retira
        await market.withdraw(GUILD, listing.id, SELLER, NOW)
    with pytest.raises(InsufficientFundsError):
        await economy.market(GUILD, market.bid(GUILD, listing.id, RIVAL, 900_000, NOW))


async def test_un_puesto_sin_pujas_vuelve_a_su_dueno(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop)
    entry = await bought(economy, shop, it, SELLER)
    listing = await market.open_stall(
        GUILD, SELLER, entry, start_price=5_000, channel_id=None, now=NOW
    )
    # Mientras está en el puesto no cuenta en el patrimonio de nadie.
    assert all(row[0] > 0 for row in await shop.holdings(GUILD, None, NOW))

    trade, _ = await economy.market(GUILD, market.close(GUILD, listing.id, listing.expires_at))

    assert trade.expired and trade.deal is None
    assert await owner_of(shop, entry) == SELLER


async def test_retirar_un_puesto_sin_pujas(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop)
    entry = await bought(economy, shop, it, SELLER)
    listing = await market.open_stall(
        GUILD, SELLER, entry, start_price=5_000, channel_id=None, now=NOW
    )
    with pytest.raises(MarketError):
        await market.withdraw(GUILD, listing.id, BUYER, NOW)
    closed = await market.withdraw(GUILD, listing.id, SELLER, NOW)
    assert closed.status == "cancelled"
    assert await owner_of(shop, entry) == SELLER


async def test_si_quien_gana_ya_no_puede_tenerlo_la_subasta_queda_desierta(
    tmp_path: Path,
) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop, per_user=1)
    entry = await bought(economy, shop, it, SELLER)
    await give(economy, BUYER, 50_000)
    listing = await market.open_stall(
        GUILD, SELLER, entry, start_price=5_000, channel_id=None, now=NOW
    )
    await economy.market(GUILD, market.bid(GUILD, listing.id, BUYER, 5_000, NOW))
    await bought(economy, shop, it, BUYER)  # se compra otro en la tienda mientras tanto
    after_shop = await economy.balance(GUILD, BUYER)

    trade, _ = await economy.market(GUILD, market.close(GUILD, listing.id, NOW + AUCTION_SECONDS))

    assert trade.void
    assert await owner_of(shop, entry) == SELLER
    assert await economy.balance(GUILD, MARKET_ACCOUNT_ID) == 0
    assert await economy.balance(GUILD, BUYER) == after_shop + escrow(5_000, 10_000)
    assert_books_balance(tmp_path)


async def test_no_se_pueden_tener_mas_de_cinco_puestos(tmp_path: Path) -> None:
    economy, shop, market = await make_stores(tmp_path, Clock())
    it = await stocked_item(shop, price=10)
    for _ in range(5):
        entry = await bought(economy, shop, it, SELLER)
        await market.open_stall(GUILD, SELLER, entry, start_price=10, channel_id=None, now=NOW)
    entry = await bought(economy, shop, it, SELLER)
    with pytest.raises(MarketError):
        await market.open_stall(GUILD, SELLER, entry, start_price=10, channel_id=None, now=NOW)


# -- Cog -------------------------------------------------------------------------------


async def make_cog(tmp_path: Path, clock: Clock) -> Rastro:
    economy, shop, market = await make_stores(tmp_path, clock)
    monte = MonteRepository(tmp_path / "bot.db")
    await monte.initialize()
    bot = MagicMock()
    bot.get_guild.return_value = None
    return Rastro(bot, economy, market, shop, monte, clock=clock)


async def test_elegir_el_objeto_por_nombre_o_por_id(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    it = await stocked_item(cog.shop)
    entry = await bought(cog.economy, cog.shop, it, SELLER)
    entries = await cog.shop.inventory(GUILD, SELLER, NOW)
    assert pick_entry(entries, "yate").id == entry  # type: ignore[union-attr]
    assert pick_entry(entries, str(entry)).id == entry  # type: ignore[union-attr]
    missing = pick_entry(entries, "Falcon")
    assert isinstance(missing, str) and "Yate" in missing


async def test_el_tablon_vacio_y_con_un_puesto(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    empty = cog.board(GUILD, SELLER, 0)
    assert "manta" in empty["embed"].description  # type: ignore[union-attr]
    it = await stocked_item(cog.shop)
    entry = await bought(cog.economy, cog.shop, it, SELLER)
    listing = await cog.repository.open_stall(
        GUILD, SELLER, entry, start_price=5_000, channel_id=None, now=NOW
    )
    cog.listings[listing.id] = listing
    board = cog.board(GUILD, SELLER, 0)
    assert f"#{listing.id}" in board["embed"].description  # type: ignore[union-attr]
    ids = [child.custom_id for child in board["view"].children]  # type: ignore[union-attr]
    assert f"rastro:pujar:{listing.id}" in ids and f"rastro:retirar:{listing.id}" in ids


async def test_el_cierre_adjudica_lo_que_ha_llegado_a_su_hora(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    it = await stocked_item(cog.shop)
    entry = await bought(cog.economy, cog.shop, it, SELLER)
    await give(cog.economy, BUYER, 50_000)
    listing = await cog.repository.open_stall(
        GUILD, SELLER, entry, start_price=5_000, channel_id=None, now=NOW
    )
    bid, _ = await cog.economy.market(
        GUILD, cog.repository.bid(GUILD, listing.id, BUYER, 5_000, NOW)
    )
    cog.listings[listing.id] = bid.listing
    assert await cog.close_due() == 0
    clock.now = NOW + AUCTION_SECONDS
    assert await cog.close_due() == 1
    assert cog.listings == {}
    assert await owner_of(cog.shop, entry) == BUYER


# -- Logros ----------------------------------------------------------------------------


def test_los_logros_del_rastro_salen_de_sus_estadisticas() -> None:
    sunday = datetime(2026, 10, 18, 12, tzinfo=TIMEZONE)
    sale = market_sale_stats(price=0, cost=500, gain_tax=0, bids=0, when=sunday)
    assert sale.add["market_gifts_given"] == 1 and sale.add["market_loss"] == 1
    assert sale.add["market_sunday"] == 1
    purchase = market_purchase_stats(
        price=666, itp=55, auction=True, serial=3, title=False, tapete=True,
        balance_after=1, when=sunday,
    )  # fmt: skip
    assert purchase.add["market_666"] == 1 and purchase.add["market_itp"] == 55
    assert purchase.add["market_tapete_bought"] == 1
    rastro = [a for a in BY_ID.values() if a.category == "rastro"]
    assert len(rastro) >= 40
    assert sum(a.secret for a in rastro) >= len(rastro) // 7


# -- Panel de vender -------------------------------------------------------------------


def member(user_id: int, name: str = "Diego") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id, user.bot, user.display_name, user.mention = user_id, False, name, f"<@{user_id}>"
    return user


def clicked(user: MagicMock) -> MagicMock:
    """Una interacción de `user` en el servidor, como las que llegan del panel."""
    interaction = fake_interaction(user)
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.id = GUILD
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.channel.id = 99
    interaction.original_response = AsyncMock(return_value=MagicMock())
    return interaction


def shown(interaction: MagicMock) -> dict:
    """Lo último que se pintó en el mensaje del panel."""
    return interaction.edit_original_response.await_args.kwargs


async def test_vender_sin_argumentos_abre_el_panel_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    it = await stocked_item(cog.shop)
    await bought(cog.economy, cog.shop, it, SELLER)
    interaction = clicked(member(SELLER))
    await cog.vender.callback(cog, interaction)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    sent = interaction.followup.send.await_args.kwargs
    assert sent["ephemeral"] is True and sent["embed"].title == "🧺 Vender"
    panel = sent["view"]
    assert isinstance(panel, VenderPanel) and panel.selected is None
    ids = {child.custom_id for child in panel.children}
    assert {"vender:objeto", "vender:miembro", "vender:rastro", "vender:monte"} <= ids
    assert all(c.disabled for c in panel.children if c.custom_id != "vender:objeto")


async def test_vender_con_objeto_y_sin_precio_abre_el_panel_con_el_elegido(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, Clock())
    it = await stocked_item(cog.shop)
    entry = await bought(cog.economy, cog.shop, it, SELLER)
    interaction = clicked(member(SELLER))
    await cog.vender.callback(cog, interaction, objeto=str(entry))
    sent = interaction.followup.send.await_args.kwargs
    assert sent["view"].selected == entry
    backpack = sent["embed"].fields[-1].value
    assert "Valor de referencia en el Rastro: **10.000 Y$**" in backpack
    assert "El Monte te lo compra por **8.500 Y$**" in backpack
    sell = next(c for c in sent["view"].children if c.custom_id == "vender:monte")
    assert not sell.disabled and "8.500" in sell.label


async def test_vender_por_texto_sin_nada_saca_el_panel_en_el_canal(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    ctx = MagicMock()
    ctx.guild = MagicMock(spec=discord.Guild)
    ctx.guild.id = GUILD
    ctx.author = member(SELLER)
    ctx.send = AsyncMock()
    await cog.vender_text.callback(cog, ctx)
    sent = ctx.send.await_args.kwargs
    assert isinstance(sent["view"], VenderPanel)
    assert "No tienes nada que se pueda vender" in sent["embed"].fields[-1].value


async def test_el_atajo_con_objeto_y_precio_sigue_como_siempre(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    it = await stocked_item(cog.shop)
    stall_entry = await bought(cog.economy, cog.shop, it, SELLER)
    offer_entry = await bought(cog.economy, cog.shop, it, SELLER)
    interaction = clicked(member(SELLER))
    await cog.vender.callback(cog, interaction, objeto=str(stall_entry), precio="5000")
    posted = interaction.response.send_message.await_args
    assert "monta un puesto" in posted.kwargs["embed"].description
    assert len(cog.listings) == 1
    buyer = member(BUYER, "Ana")
    interaction = clicked(member(SELLER))
    await cog.vender.callback(
        cog, interaction, objeto=str(offer_entry), precio="100", comprador=buyer
    )
    posted = interaction.response.send_message.await_args
    assert posted.args[0] == buyer.mention
    assert posted.kwargs["allowed_mentions"].users == [buyer]
    view = posted.kwargs["view"]
    assert isinstance(view, OfferView) and view.message is not None


async def test_el_panel_vende_empena_y_desempena_en_el_monte(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    it = await stocked_item(cog.shop)
    sold, pawned = [await bought(cog.economy, cog.shop, it, SELLER) for _ in range(2)]
    owner = member(SELLER)
    panel = VenderPanel(cog, GUILD, owner)
    start = await cog.economy.balance(GUILD, SELLER)

    panel.selected = sold
    await panel.screen()
    interaction = clicked(owner)
    await next(c for c in panel.children if isinstance(c, MonteSellButton)).callback(interaction)
    assert "El Monte te compra" in shown(interaction)["content"]
    assert await cog.economy.balance(GUILD, SELLER) == start + 8_500

    panel.selected = pawned
    await panel.screen()
    interaction = clicked(owner)
    await next(c for c in panel.children if isinstance(c, PawnButton)).callback(interaction)
    assert "Empeñas" in shown(interaction)["content"]
    (ticket,) = cog.tickets.values()
    assert await cog.economy.balance(GUILD, SELLER) == start + 8_500 + ticket.loan
    assert [i.kind for i in pending(_bot_with(cog), GUILD, SELLER)] == ["monte_ticket"]

    redeem = next(c for c in shown(interaction)["view"].children if isinstance(c, RedeemButton))
    assert redeem.custom_id == f"vender:desempenar:{ticket.id}"
    interaction = clicked(owner)
    await redeem.callback(interaction)
    assert "Recuperas" in shown(interaction)["content"]
    assert cog.tickets == {} and await owner_of(cog.shop, pawned) == SELLER
    assert_books_balance(tmp_path)


def _bot_with(cog: Rastro) -> MagicMock:
    """Un bot que solo tiene cargado `cog`, para las funciones puente."""
    bot = MagicMock()
    bot.get_cog.side_effect = lambda name: cog if name == "Rastro" else None
    return bot


async def test_dos_operaciones_del_monte_a_la_vez_no_pasan(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    it = await stocked_item(cog.shop)
    entry = await bought(cog.economy, cog.shop, it, SELLER)
    owner = member(SELLER)
    panel = VenderPanel(cog, GUILD, owner)
    panel.selected = entry
    await panel.screen()
    cog.working.add((GUILD, SELLER))
    interaction = clicked(owner)
    await next(c for c in panel.children if isinstance(c, MonteSellButton)).callback(interaction)
    assert "está con lo anterior" in interaction.response.send_message.await_args.args[0]
    assert await owner_of(cog.shop, entry) == SELLER


async def test_el_panel_publica_la_oferta_y_el_puesto_en_el_canal(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    it = await stocked_item(cog.shop)
    offered, listed = [await bought(cog.economy, cog.shop, it, SELLER) for _ in range(2)]
    owner, buyer = member(SELLER), member(BUYER, "Ana")
    panel = VenderPanel(cog, GUILD, owner)

    panel.selected = offered
    form = MemberForm(panel)
    form.buyer._values = [buyer]
    form.price._value = "100"
    interaction = clicked(owner)
    await form.on_submit(interaction)
    posted = interaction.followup.send.await_args
    assert posted.args[0] == buyer.mention and posted.kwargs["wait"] is True
    assert posted.kwargs["allowed_mentions"].users == [buyer]
    view = posted.kwargs["view"]
    assert isinstance(view, OfferView) and view.message is not None and view.price == 100
    assert "Oferta publicada" in shown(interaction)["content"] and panel.selected is None

    panel.selected = listed
    form = StallForm(panel)
    form.price._value = "5000"
    interaction = clicked(owner)
    await form.on_submit(interaction)
    (listing,) = cog.listings.values()
    assert listing.start_price == 5_000 and listing.channel_id == 99
    assert "monta un puesto" in interaction.followup.send.await_args.kwargs["embed"].description
    assert "Puesto montado" in shown(interaction)["content"]


async def test_un_error_del_formulario_sale_en_el_panel_y_no_publica(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    it = await stocked_item(cog.shop)
    entry = await bought(cog.economy, cog.shop, it, SELLER)
    owner = member(SELLER)
    panel = VenderPanel(cog, GUILD, owner)
    panel.selected = entry
    form = MemberForm(panel)
    form.buyer._values = [owner]
    form.price._value = "100"
    interaction = clicked(owner)
    await form.on_submit(interaction)
    interaction.followup.send.assert_not_awaited()
    assert "blanquear" in shown(interaction)["content"] and panel.selected == entry


async def test_lo_que_el_monte_no_acepta_dice_por_que_y_va_a_los_otros_sitios(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, Clock())
    it = await stocked_item(cog.shop)
    entry, _dupe = await cog.shop.grant(GUILD, SELLER, it, NOW)
    panel = VenderPanel(cog, GUILD, member(SELLER))
    panel.selected = entry.id
    kwargs = await panel.screen()
    assert "El Monte no lo acepta: Eso no salió de la tienda" in kwargs["embed"].fields[-1].value
    buttons = {c.custom_id: c for c in panel.children}
    assert buttons["vender:monte"].disabled and buttons["vender:empenar"].disabled
    assert not buttons["vender:miembro"].disabled and not buttons["vender:rastro"].disabled


async def test_el_panel_solo_lo_maneja_quien_lo_abre(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    panel = VenderPanel(cog, GUILD, member(SELLER))
    interaction = clicked(member(BUYER))
    assert not await panel.interaction_check(interaction)
    assert "no es tuyo" in interaction.response.send_message.await_args.args[0]
