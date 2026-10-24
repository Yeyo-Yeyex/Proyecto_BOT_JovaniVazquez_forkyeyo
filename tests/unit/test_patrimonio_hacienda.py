"""Pruebas de `patrimonio`, `fortunas` y la factura fiscal de `hacienda`.

Cubren el valor de los bienes de la tienda, los boletos pendientes, las
cuentas de la riqueza (Gini, parte del más rico), el desglose de impuestos
por miembro (directos, indirectos, devoluciones y Hong Kong) y que los
embeds quepan en Discord.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import hacienda_panel as hacienda_panel_module
from bot.cogs import patrimonio as patrimonio_cog
from bot.cogs.hacienda_panel import HaciendaPanel, bill_card
from bot.cogs.patrimonio import Patrimonio
from bot.repositories.economy import EconomyRepository, Treasury
from bot.repositories.lottery import LotteryRepository
from bot.repositories.shop import ShopRepository, wealth_goods_in
from bot.services.achievements import hacienda_page_stats, hacienda_stats, patrimonio_stats
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.net_worth import NetWorth, build, gini, top_share
from bot.services.shop import Kind, quote
from bot.services.tax_report import (
    BUSINESS_TAXES,
    HOUSING_TAXES,
    PAGE_LIMIT,
    PAGES,
    TAX_KINDS,
    Group,
    Snapshot,
    TaxBill,
    bar,
    bills,
    leader,
    member_page,
    solidarity_line,
    summary_page,
)
from bot.services.taxes import wealth_quote

GUILD = 1
ANA = 10
BEA = 20
NOW = datetime(2026, 10, 6, 18, tzinfo=TIMEZONE).timestamp()


class Clock:
    """Reloj controlable."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def stores(tmp_path: Path) -> tuple[EconomyService, ShopRepository, LotteryRepository]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    shop = ShopRepository(tmp_path / "bot.db")
    await shop.initialize()
    lottery = LotteryRepository(tmp_path / "bot.db")
    await lottery.initialize()
    return EconomyService(economy_repo, clock=Clock()), shop, lottery


async def buy(economy: EconomyService, shop: ShopRepository, item_id: int, user_id: int) -> None:
    current = await shop.item(GUILD, item_id)
    assert current is not None
    price = quote(current, NOW)
    await economy.purchase(
        GUILD,
        user_id,
        base=price.base,
        tax=price.tax,
        concept=current.kind.value,
        reserve=shop.reserve(GUILD, user_id, item_id, expected=price, level=0, now=NOW),
    )


# -- Riqueza ------------------------------------------------------------------------------


def test_gini_y_parte_del_mas_rico() -> None:
    assert gini([100, 100, 100]) == pytest.approx(0.0)
    assert gini([0, 0, 0, 1_000]) == pytest.approx(0.75)
    assert gini([]) == 0.0
    assert top_share([900, 50, 50], 0) == pytest.approx(0.9)
    assert top_share([], 0.1) == 0.0


def test_el_patrimonio_suma_efectivo_bienes_boletos_y_renta() -> None:
    worths = build(
        {ANA: 1_000, BEA: 50},
        [(BEA, "Yate", "🛥️", "objeto", 5_000, None)],
        {ANA: 200},
        {ANA: 30},
    )
    assert [w.user_id for w in worths] == [BEA, ANA]
    assert worths[0].total == 5_050 and worths[0].illiquid_share > 0.9
    assert worths[1].total == 1_230


async def test_los_bienes_valen_lo_pagado_sin_igic_y_lo_que_caduca_se_deprecia(
    tmp_path: Path,
) -> None:
    economy, shop, _lottery = await stores(tmp_path)
    await economy.grant(GUILD, ANA, amount=10_000, reason="prueba")
    yacht = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Yate", emoji="🛥️", description="", price=500,
        igic="lujo", now=NOW,
    )  # fmt: skip
    boost = await shop.create_item(
        GUILD, kind=Kind.BOOST, name="Doble XP", emoji="⚡", description="", price=1_000,
        igic="general", duration=3_600, multiplier=2, now=NOW,
    )  # fmt: skip
    await buy(economy, shop, yacht.id, ANA)
    await buy(economy, shop, boost.id, ANA)

    full = {row[1]: row[4] for row in await shop.holdings(GUILD, ANA, NOW)}
    half = {row[1]: row[4] for row in await shop.holdings(GUILD, None, NOW + 1_800)}
    gone = {row[1]: row[4] for row in await shop.holdings(GUILD, ANA, NOW + 3_601)}

    assert full == {"Yate": 500, "Doble XP": 1_000}
    assert half["Doble XP"] == 500
    assert gone == {"Yate": 500}


async def test_el_patrimonio_cuenta_los_objetos_de_la_tienda_y_no_los_potenciadores(
    tmp_path: Path,
) -> None:
    economy, shop, _lottery = await stores(tmp_path)
    await economy.grant(GUILD, ANA, amount=20_000_000, reason="prueba")
    sorolla = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Sorolla", emoji="🖼️", description="", price=12_000_000,
        igic="lujo", now=NOW,
    )  # fmt: skip
    boost = await shop.create_item(
        GUILD, kind=Kind.BOOST, name="Doble XP", emoji="⚡", description="", price=1_000,
        igic="general", duration=3_600, multiplier=2, now=NOW,
    )  # fmt: skip
    await buy(economy, shop, sorolla.id, ANA)
    await buy(economy, shop, boost.id, ANA)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        assert wealth_goods_in(connection, GUILD, NOW) == {ANA: 12_000_000}

    economy._clock = Clock(datetime(2026, 10, 5, 12, tzinfo=TIMEZONE).timestamp())
    await economy.charge_wealth_tax(GUILD)  # activación
    economy._clock = Clock(datetime(2026, 10, 12, 12, tzinfo=TIMEZONE).timestamp())
    _, run = await economy.charge_wealth_tax(
        GUILD, goods=lambda connection: wealth_goods_in(connection, GUILD, NOW)
    )
    (charge,) = run.charges
    cash = await economy.balance(GUILD, ANA) + charge.tax
    assert charge.goods == 12_000_000 and charge.balance == cash + 12_000_000
    # El cuadro no da rentas: su parte de la cuota no la rebaja el límite con el IRPF.
    assert charge.tax == wealth_quote(charge.balance, unproductive=12_000_000).due
    assert charge.tax > wealth_quote(charge.balance).due


def test_la_linea_de_las_grandes_fortunas_dice_que_sale_a_cero() -> None:
    text = solidarity_line(3, 1, 0)
    assert "3 declaraciones" in text and "1 fortuna " in text and "0 Y$" in text
    assert "Ley 38/2022" in text
    assert any(kind.key == "grandes_fortunas" for kind in TAX_KINDS)


async def test_boletos_de_sorteos_abiertos_cuentan_y_los_celebrados_no(tmp_path: Path) -> None:
    _economy, _shop, lottery = await stores(tmp_path)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO lottery_draws (id, guild_id, game, draw_at, status) VALUES "
            "(1, ?, 'primitiva', 1, 'open'), (2, ?, 'primitiva', 0, 'drawn')",
            (GUILD, GUILD),
        )
        connection.execute(
            "INSERT INTO lottery_tickets (draw_id, guild_id, user_id, pick, quantity, cost, "
            "created_at) VALUES (1, ?, ?, 'x', 1, 300, 0), (2, ?, ?, 'x', 1, 999, 0)",
            (GUILD, ANA, GUILD, ANA),
        )
    assert await lottery.open_stakes(GUILD) == {ANA: 300}


# -- Impuestos ----------------------------------------------------------------------------


async def test_la_factura_separa_directos_indirectos_y_no_duplica_el_irpf_de_la_nomina(
    tmp_path: Path,
) -> None:
    economy, shop, _lottery = await stores(tmp_path)
    salary = await economy.pay_salary(GUILD, ANA, gross=200_000, concept="pala:celador")
    await economy.pay_income(GUILD, ANA, gross=100_000, concept="nivel:30")
    item = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Yate", emoji="🛥️", description="", price=500,
        igic="lujo", now=NOW,
    )  # fmt: skip
    await buy(economy, shop, item.id, ANA)

    breakdown = await economy.tax_breakdown(GUILD)
    bill = bills(breakdown)[0]
    slip = salary.payslip

    assert bill.amounts["irpf_trabajo"] == slip.irpf
    assert bill.amounts["ss_trabajador"] == slip.ss_worker
    assert bill.amounts["ss_empresa"] == slip.ss_employer
    assert bill.amounts["igic"] == 75
    assert bill.amounts.get("irpf_otros", 0) > 0
    assert bill.indirect == slip.ss_employer + 75
    # Todo lo de la factura es lo que ha entrado en el Estado.
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (state,) = connection.execute(
            "SELECT SUM(delta) FROM economy_ledger WHERE user_id = ?", (STATE_ACCOUNT_ID,)
        ).fetchone()
    assert bill.total == state


async def test_las_devoluciones_de_igic_y_de_la_renta_restan(tmp_path: Path) -> None:
    economy, _shop, _lottery = await stores(tmp_path)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.executemany(
            "INSERT INTO economy_consumption_tax (guild_id, user_id, concept, base, tax, "
            "created_at) VALUES (?, ?, 'objeto', ?, ?, ?)",
            [(GUILD, ANA, 1_000, 70, NOW), (GUILD, ANA, -1_000, -70, NOW)],
        )
        connection.execute(
            "INSERT INTO economy_ledger (guild_id, user_id, delta, balance_after, reason, "
            "created_at) VALUES (?, ?, 40, 40, 'devolucion:renta', ?)",
            (GUILD, ANA, NOW),
        )
        connection.execute(
            "INSERT INTO economy_gambling_days (guild_id, user_id, day, net, withheld, "
            "updated_at) VALUES (?, ?, '2026-10-05', 1000, 100, ?)",
            (GUILD, ANA, NOW),
        )
    (bill,) = bills(await economy.tax_breakdown(GUILD))
    assert "igic" not in bill.amounts
    assert bill.refunded == 40
    assert bill.total == 60


def test_hong_kong_se_ve_pero_no_suma_al_total() -> None:
    bill = TaxBill(ANA, {"extranjero": 500, "irpf_trabajo": 100})
    assert bill.total == 100 and bill.foreign == 500
    assert {kind.group for kind in TAX_KINDS} == set(Group)


def snapshot_of(
    all_bills: list[TaxBill],
    *,
    housing: dict[int, dict[str, int]] | None = None,
    business: dict[int, dict[str, int]] | None = None,
    names: dict[int, str] | None = None,
) -> Snapshot:
    return Snapshot(
        guild_id=GUILD,
        year=2026,
        treasury=Treasury(123_456_789, 987_654_321, 123_456_789, (), debt=5_000),
        bills=all_bills,
        housing=housing or {},
        business=business or {},
        solidarity=(3, 1, 0),
        names=names or {},
    )


def texts(view: discord.ui.LayoutView) -> list[str]:
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def test_las_pestañas_de_hacienda_caben_con_muchos_miembros() -> None:
    every = {kind.key: 123_456_789 for kind in TAX_KINDS}
    all_bills = bills({1_000 + i: dict(every) for i in range(40)})
    names = {b.user_id: "Nombre_larguísimo_de_alguien_" + str(b.user_id) for b in all_bills}
    homes = {line.key: 9_876_543 for line in HOUSING_TAXES}
    shops = {line.key: 9_876_543 for line in BUSINESS_TAXES}
    snapshot = snapshot_of(
        all_bills,
        housing={b.user_id: dict(homes) for b in all_bills},
        business={b.user_id: dict(shops) for b in all_bills},
        names=names,
    )
    owner = MagicMock(spec=discord.Member, id=1_000, bot=False)
    for key, _emoji, _label in PAGES:
        panel = HaciendaPanel(MagicMock(), snapshot, owner, page=key)
        shown = texts(panel)
        assert len(shown[1]) <= PAGE_LIMIT, key
        # Discord no admite más de 4.000 caracteres entre todos los textos ni 40 componentes.
        assert sum(map(len, shown)) <= 4_000, key
        assert len(list(panel.walk_children())) <= 40
        ids = [c.custom_id for c in panel.walk_children() if isinstance(c, discord.ui.Button)]
        assert len(ids) == len(set(ids)) == len(PAGES)
    # Las listas que no caben dicen cuántos faltan.
    panel = HaciendaPanel(MagicMock(), snapshot, owner, page="empresas")
    assert "empresarios más." in texts(panel)[1]
    personal = member_page(
        member_name="Ana", all_bills=all_bills, year_bills=all_bills, user_id=1_000, year=2026,
        homes=homes, business=shops,
    )  # fmt: skip
    assert len(personal) <= 4_000
    assert "### 🏪 Por sus negocios" in personal and "### 🏠 Por sus pisos" in personal
    empty = member_page(
        member_name="Nadie", all_bills=[], year_bills=[], user_id=5, year=2026, homes={},
        business={},
    )  # fmt: skip
    assert "Nada que declarar" in texts(bill_card(empty))[0]


def test_el_resumen_reparte_lo_recaudado_en_barras() -> None:
    all_bills = bills({ANA: {"irpf_trabajo": 750, "igic": 250, "devuelto": 100}, BEA: {}})
    page = summary_page(snapshot_of(all_bills, names={ANA: "Ana"}))
    assert f"{bar(0.75)} **75,0 %** 🪏 IRPF nóm." in page
    assert f"{bar(0.25)} **25,0 %** 🛒 IGIC" in page
    assert "devuelto −100 Y$" in page and "🥇 Ana · **900 Y$**" in page
    assert bar(0.75) == "`▰▰▰▰▰▰▰▰▱▱`" and bar(2) == "`▰▰▰▰▰▰▰▰▰▰`"
    assert bar(0.01) == "`▰▱▱▱▱▱▱▱▱▱`" and bar(0) == "`▱▱▱▱▱▱▱▱▱▱`"
    assert "Nadie ha pagado" in summary_page(snapshot_of([]))


async def test_otro_miembro_que_pulsa_una_pestaña_recibe_su_copia() -> None:
    from interaction_fakes import fake_interaction

    all_bills = bills({ANA: {"igic": 300}, BEA: {"igic": 100}})
    snapshot = snapshot_of(
        all_bills,
        business={ANA: {"igic": 300}, BEA: {"igic": 100}},
        names={ANA: "Ana", BEA: "Bea"},
    )
    owner = MagicMock(spec=discord.Member, id=BEA, bot=False)
    panel = HaciendaPanel(MagicMock(), snapshot, owner)
    track = AsyncMock()
    stranger = fake_interaction()
    stranger.user = MagicMock(spec=discord.Member, id=ANA, bot=False)
    tab = next(
        c for c in panel.walk_children()
        if isinstance(c, discord.ui.Button) and c.custom_id == "hacienda:pagina:empresas"
    )  # fmt: skip
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(hacienda_panel_module.logros, "track", track)
        await tab.callback(stranger)
        assert panel.page == "resumen"  # el de Bea no se mueve
        sent = stranger.response.send_message.await_args.kwargs
        assert sent["ephemeral"] is True and sent["view"].page == "empresas"
        assert "Ana" in texts(sent["view"])[1]
        stats = track.await_args.args[-1]
        assert stats.add == {"hacienda_business": 1, "hacienda_business_top": 1}
        mine = fake_interaction()
        mine.user = owner
        await tab.callback(mine)
        assert panel.page == "empresas"
        assert mine.response.edit_message.await_args.kwargs["view"] is panel
        assert track.await_args.args[-1].add == {"hacienda_business": 1}


def check(embed: discord.Embed) -> None:
    assert len(embed) <= 6_000
    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert 0 < len(field.value) <= 1_024


def test_quien_encabeza_cada_pestaña() -> None:
    all_bills = bills({ANA: {"igic": 300}, BEA: {"igic": 100}})
    alone = snapshot_of(all_bills, housing={ANA: {"ibi": 5}}, business={BEA: {"igic": 1}})
    assert leader(alone, "gente") == ANA
    assert leader(alone, "caseros") is None and leader(alone, "empresas") is None
    both = snapshot_of(all_bills, housing={ANA: {"ibi": 5}, BEA: {"ibi": 9}})
    assert leader(both, "caseros") == BEA


# -- Logros -------------------------------------------------------------------------------


def test_logros_de_patrimonio_fortunas_y_hacienda() -> None:
    mine = patrimonio_stats(
        listing=False, snooping=False, net_worth=60_000, illiquid=True, rank=1, people=3
    )
    assert mine.add == {"patrimonio_views": 1, "patrimonio_illiquid": 1}
    assert mine.peak == {"net_worth_max": 60_000}
    listing = patrimonio_stats(
        listing=True, snooping=False, net_worth=0, illiquid=False, rank=3, people=3
    )
    assert listing.add == {"fortunas_views": 1, "fortunas_last": 1}
    tax = hacienda_stats(own=True, snooping=False, share=0.6, indirect_over_direct=True)
    assert tax.add == {
        "hacienda_views": 1,
        "hacienda_self": 1,
        "hacienda_pillar": 1,
        "hacienda_hidden": 1,
    }
    tab = hacienda_page_stats(page="caseros", top=True, seen_all=True)
    assert tab.add == {"hacienda_landlord_top": 1, "hacienda_all_tabs": 1}
    assert hacienda_page_stats(page="gente", top=True, seen_all=False).add == {}


# -- Cog ----------------------------------------------------------------------------------


def member(user_id: int, name: str) -> MagicMock:
    found = MagicMock(spec=discord.Member)
    found.id = user_id
    found.bot = False
    found.display_name = name
    return found


async def test_patrimonio_y_fortunas_responden_con_un_embed(tmp_path: Path) -> None:
    economy, shop, lottery = await stores(tmp_path)
    await economy.grant(GUILD, ANA, amount=80_000, reason="prueba")
    await economy.balance(GUILD, BEA)
    cog = Patrimonio(MagicMock(), economy, shop=shop, lottery=lottery)
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    guild.get_member = MagicMock(side_effect=lambda uid: member(uid, f"m{uid}"))
    responder = MagicMock()
    responder.guild = guild
    responder.member = member(ANA, "Ana")
    responder.channel = None
    responder.send = AsyncMock()
    track = AsyncMock()
    original = patrimonio_cog.logros.track
    patrimonio_cog.logros.track = track
    try:
        embed = await cog.patrimonio_embed(guild, responder.member, responder.member, None)
        assert "Ana" in embed.title
        assert "Patrimonio" in embed.fields[-1].value
        check(embed)
        await cog._fortunas_impl(responder)
        listing = responder.send.await_args.kwargs["embed"]
        assert "mANA" not in (listing.description or "")
        check(listing)
    finally:
        patrimonio_cog.logros.track = original
    first = track.await_args_list[0].args[4]
    assert first.peak["net_worth_max"] == STARTING_BALANCE + 80_000
    second = track.await_args_list[1].args[4]
    assert second.add["fortunas_first"] == 1


def test_quien_no_tiene_nada_tiene_patrimonio_cero() -> None:
    assert NetWorth(ANA).total == 0
    assert NetWorth(ANA).illiquid_share == 0.0


async def test_fortunas_enseña_el_titulo_que_luce_cada_uno(tmp_path: Path) -> None:
    economy, shop, lottery = await stores(tmp_path)
    await economy.grant(GUILD, ANA, amount=80_000, reason="prueba")
    tienda = MagicMock()
    tienda.title = lambda guild_id, user_id: "🏰 Marquesado de Mogán" if user_id == ANA else None
    bot = MagicMock()
    cog = Patrimonio(bot, economy, shop=shop, lottery=lottery)
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    guild.get_member = MagicMock(side_effect=lambda uid: member(uid, f"m{uid}"))
    responder = MagicMock()
    responder.guild = guild
    responder.member = member(BEA, "Bea")
    responder.channel = None
    responder.send = AsyncMock()
    original = patrimonio_cog.tienda.title_of
    patrimonio_cog.tienda.title_of = lambda _bot, g, u: tienda.title(g, u)
    track = patrimonio_cog.logros.track
    patrimonio_cog.logros.track = AsyncMock()
    try:
        await cog._fortunas_impl(responder)
    finally:
        patrimonio_cog.tienda.title_of = original
        patrimonio_cog.logros.track = track
    listing = responder.send.await_args.kwargs["embed"]
    text = "\n".join(f.value for f in listing.fields)
    assert f"m{ANA} · *🏰 Marquesado de Mogán*" in text
