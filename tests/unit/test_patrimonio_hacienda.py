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

from bot.cogs import patrimonio as patrimonio_cog
from bot.cogs.patrimonio import Patrimonio
from bot.repositories.economy import EconomyRepository
from bot.repositories.lottery import LotteryRepository
from bot.repositories.shop import ShopRepository
from bot.services.achievements import hacienda_stats, patrimonio_stats
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.net_worth import NetWorth, build, gini, top_share
from bot.services.shop import Kind, quote
from bot.services.tax_report import (
    TAX_KINDS,
    Group,
    TaxBill,
    add_bill_fields,
    bills,
    member_bill_embed,
    server_bill,
)

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


def test_los_embeds_de_hacienda_caben_con_muchos_miembros() -> None:
    every = {kind.key: 123_456_789 for kind in TAX_KINDS}
    all_bills = bills({1_000 + i: dict(every) for i in range(40)})
    server = server_bill(all_bills)
    names = {b.user_id: "Nombre_larguísimo_de_alguien_" + str(b.user_id) for b in all_bills}
    embed = discord.Embed(title="🏛️ Hacienda", description="x" * 300)
    embed.add_field(name="Qué se cobra", value="y" * 900)
    add_bill_fields(embed, all_bills, server, names)
    check(embed)
    personal = member_bill_embed(
        member_name="Ana",
        all_bills=all_bills,
        year_bills=all_bills,
        server=server,
        user_id=1_000,
        year=2026,
    )
    check(personal)
    empty = member_bill_embed(
        member_name="Nadie", all_bills=[], year_bills=[], server=TaxBill(0), user_id=5, year=2026
    )
    check(empty)


def check(embed: discord.Embed) -> None:
    assert len(embed) <= 6_000
    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert 0 < len(field.value) <= 1_024


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
        await cog._patrimonio_impl(responder, None)
        embed = responder.send.await_args.kwargs["embed"]
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
