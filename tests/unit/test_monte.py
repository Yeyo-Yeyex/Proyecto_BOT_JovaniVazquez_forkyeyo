"""Pruebas del Monte de Piedad: reglas, topes de las cifras y operaciones con dinero de verdad.

La economía, la tienda y el Monte son los reales sobre un SQLite temporal. En cada flujo se
comprueba que el libro cuadra con los saldos y que el dinero solo va entre el miembro y la
caja de la tienda (salvo el embargo de una venta, que va al Estado).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from bot.repositories.economy import (
    PAWN_ACCOUNT_ID,
    SHOP_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
    TillEmptyError,
)
from bot.repositories.monte import MonteRepository
from bot.repositories.rastro import MarketRepository
from bot.repositories.shop import ShopRepository, wealth_goods_in
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.monte import (
    MAX_TICKETS,
    PAWN_FEE,
    PAWN_SECONDS,
    PAWN_SHARE,
    SELL_SHARE,
    MonteError,
    offer,
    refusal,
)
from bot.services.shop import Kind, ShopItem, quote
from bot.services.skins import SKINS
from bot.services.taxes import GARNISH_SHARE, WEALTH_BRACKETS, garnish

GUILD = 1
OWNER = 10
OTHER = 20
NOW = datetime(2026, 10, 20, 18, tzinfo=TIMEZONE).timestamp()


# -- Reglas ----------------------------------------------------------------------------


def test_lo_que_da_el_monte_sale_de_lo_pagado_en_la_tienda() -> None:
    deal = offer(10_001)
    assert deal.sale == int(10_001 * SELL_SHARE)
    assert deal.loan == int(10_001 * PAWN_SHARE)
    # El interés se redondea hacia arriba: nunca sale gratis.
    assert deal.interest >= deal.loan * PAWN_FEE
    assert deal.repay == deal.loan + deal.interest


def test_empenar_y_no_volver_nunca_da_mas_que_vender_y_pagar_el_embargo() -> None:
    # Si no, a quien debe a Hacienda le saldría mejor perder la papeleta.
    assert PAWN_SHARE <= (1 - GARNISH_SHARE) * SELL_SHARE


def test_empenar_el_domingo_no_compensa_para_esquivar_el_patrimonio() -> None:
    # Lo empeñado no cuenta en el Patrimonio y lo prestado sí: se ahorraría el tipo más alto
    # por la parte que no vuelve en efectivo. El interés tiene que costar más.
    top_rate = WEALTH_BRACKETS[-1][1]
    assert PAWN_FEE * PAWN_SHARE > top_rate * (1 - PAWN_SHARE)


def test_el_monte_solo_acepta_lo_comprado_que_no_es_numerado() -> None:
    assert refusal(Kind.TROPHY, None, numbered=False, paid=1_000) is None
    for kind in (Kind.ROLE, Kind.BOOST, Kind.PET):
        assert refusal(kind, None, numbered=False, paid=1_000) is not None
    assert refusal(Kind.TROPHY, None, numbered=True, paid=1_000) is not None
    assert refusal(Kind.TROPHY, None, numbered=False, paid=0) is not None
    otra = next(s for s in SKINS if s.game != "tapete" and not s.for_sale)
    assert refusal(Kind.TROPHY, otra.catalog_key, numbered=False, paid=1_000) is not None


# -- Con dinero de verdad --------------------------------------------------------------


class Stores:
    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / "bot.db"
        self.repo = EconomyRepository(self.path, starting_balance=STARTING_BALANCE)
        self.economy = EconomyService(self.repo, clock=lambda: NOW)
        self.shop = ShopRepository(self.path)
        self.monte = MonteRepository(self.path)
        self.rastro = MarketRepository(self.path)

    async def start(self) -> Stores:
        await self.repo.initialize()
        await self.shop.initialize()
        await self.monte.initialize()
        await self.rastro.initialize()
        return self

    def rows(self, sql: str, *args: object) -> list[tuple]:
        with sqlite3.connect(self.path) as connection:
            return connection.execute(sql, args).fetchall()

    def assert_books_balance(self) -> None:
        mismatches = self.rows(
            """
            SELECT w.user_id, w.balance, COALESCE(SUM(l.delta), 0) FROM economy_wallets w
            LEFT JOIN economy_ledger l ON l.guild_id = w.guild_id AND l.user_id = w.user_id
            GROUP BY w.guild_id, w.user_id HAVING w.balance != COALESCE(SUM(l.delta), 0)
            """
        )
        assert mismatches == []

    async def give(self, user_id: int, amount: int) -> None:
        await self.repo.apply(GUILD, user_id, [LedgerEntry(amount, "test")])

    async def item(self, price: int = 10_000, **fields: object) -> ShopItem:
        it = await self.shop.create_item(
            GUILD, kind=Kind.TROPHY, name="Guitarra", emoji="🎸", description="", price=price,
            igic="general", now=NOW,
        )  # fmt: skip
        if fields:
            it = await self.shop.update_item(GUILD, it.id, **fields)
        return it

    async def buy(self, it: ShopItem, user_id: int = OWNER) -> int:
        price = quote(it, NOW)
        await self.give(user_id, price.total)
        await self.economy.purchase(
            GUILD, user_id, base=price.base, tax=price.tax, concept="objeto",
            reserve=self.shop.reserve(GUILD, user_id, it.id, expected=price, level=99, now=NOW),
        )  # fmt: skip
        entries = await self.shop.inventory(GUILD, user_id, NOW)
        return max(e.id for e in entries if e.item_id == it.id)

    async def till(self) -> int:
        return await self.economy.balance(GUILD, SHOP_ACCOUNT_ID)

    async def owner_of(self, entry_id: int) -> int | None:
        for user in (OWNER, OTHER, PAWN_ACCOUNT_ID):
            if any(e.id == entry_id for e in await self.shop.inventory(GUILD, user, NOW)):
                return user
        return None


async def test_vender_al_monte_paga_desde_la_caja_de_la_tienda(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    entry = await stores.buy(await stores.item(10_000))
    till = await stores.till()
    before = await stores.economy.balance(GUILD, OWNER)
    state = await stores.economy.state_balance(GUILD)

    sale, balance = await stores.economy.monte(GUILD, stores.monte.sell(GUILD, OWNER, entry, NOW))

    assert sale.paid == 10_000 and sale.amount == offer(10_000).sale
    assert balance == before + sale.amount
    assert await stores.till() == till - sale.amount
    assert await stores.economy.state_balance(GUILD) == state  # sin impuestos
    assert await stores.owner_of(entry) == PAWN_ACCOUNT_ID
    stores.assert_books_balance()


async def test_lo_comprado_rebajado_vale_lo_que_se_pago(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    entry = await stores.buy(await stores.item(10_000, discount=50))
    appraisal = next(
        a for a in await stores.monte.appraisals(GUILD, OWNER, NOW) if a.entry.id == entry
    )
    assert appraisal.offer is not None and appraisal.offer.paid == 5_000


async def test_lo_comprado_en_el_rastro_vale_lo_de_la_tienda(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    entry = await stores.buy(await stores.item(10_000))
    await stores.give(OTHER, 50_000)
    await stores.economy.market(GUILD, stores.rastro.sell(GUILD, OWNER, OTHER, entry, 30_000, NOW))
    appraisal = next(
        a for a in await stores.monte.appraisals(GUILD, OTHER, NOW) if a.entry.id == entry
    )
    assert appraisal.offer is not None and appraisal.offer.paid == 10_000


async def test_una_venta_paga_el_embargo_y_un_prestamo_no(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    first = await stores.buy(await stores.item(10_000))
    second = await stores.buy(await stores.item(10_000))
    stores.rows(
        "INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, ?)",
        GUILD, OWNER, 1_000_000,
    )  # fmt: skip
    state = await stores.economy.state_balance(GUILD)

    sale, _ = await stores.economy.monte(GUILD, stores.monte.sell(GUILD, OWNER, first, NOW))
    assert await stores.economy.state_balance(GUILD) == state + garnish(sale.amount)

    await stores.economy.monte(GUILD, stores.monte.pawn(GUILD, OWNER, second, NOW))
    assert await stores.economy.state_balance(GUILD) == state + garnish(sale.amount)
    stores.assert_books_balance()


async def test_empenar_y_desempenar_deja_el_interes_en_la_caja(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    entry = await stores.buy(await stores.item(10_000))
    till = await stores.till()
    before = await stores.economy.balance(GUILD, OWNER)

    ticket, balance = await stores.economy.monte(GUILD, stores.monte.pawn(GUILD, OWNER, entry, NOW))
    assert ticket.loan == offer(10_000).loan and ticket.due_at == NOW + PAWN_SECONDS
    assert balance == before + ticket.loan
    assert await stores.owner_of(entry) == PAWN_ACCOUNT_ID
    assert [t.id for t in await stores.monte.open_tickets()] == [ticket.id]

    await stores.give(OWNER, ticket.repay)
    closed, _ = await stores.economy.monte(
        GUILD, stores.monte.redeem(GUILD, OWNER, ticket.id, NOW + 60)
    )
    assert closed.status == "redeemed"
    assert await stores.owner_of(entry) == OWNER
    assert await stores.till() == till + ticket.repay - ticket.loan
    assert await stores.monte.open_tickets() == []
    stats = await stores.monte.stats(GUILD)
    assert stats.redeemed == 1 and stats.interest == ticket.repay - ticket.loan
    stores.assert_books_balance()


async def test_sin_dinero_para_desempenar_no_se_mueve_nada(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    entry = await stores.buy(await stores.item(10_000))
    ticket, balance = await stores.economy.monte(GUILD, stores.monte.pawn(GUILD, OWNER, entry, NOW))
    await stores.repo.apply(GUILD, OWNER, [LedgerEntry(-balance, "test")])
    with pytest.raises(InsufficientFundsError):
        await stores.economy.monte(GUILD, stores.monte.redeem(GUILD, OWNER, ticket.id, NOW))
    assert await stores.owner_of(entry) == PAWN_ACCOUNT_ID
    assert [t.id for t in await stores.monte.open_tickets()] == [ticket.id]


async def test_la_papeleta_vencida_se_pierde_y_el_objeto_es_del_monte(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    entry = await stores.buy(await stores.item(10_000))
    ticket, _ = await stores.economy.monte(GUILD, stores.monte.pawn(GUILD, OWNER, entry, NOW))
    late = NOW + PAWN_SECONDS
    with pytest.raises(MonteError):
        await stores.economy.monte(GUILD, stores.monte.redeem(GUILD, OWNER, ticket.id, late))
    assert await stores.monte.forfeit_due(late - 1) == []
    lost = await stores.monte.forfeit_due(late)
    assert [t.id for t in lost] == [ticket.id] and lost[0].status == "forfeited"
    assert await stores.owner_of(entry) == PAWN_ACCOUNT_ID
    assert (await stores.monte.stats(GUILD)).forfeited_lent == ticket.loan


async def test_el_monte_no_compra_lo_regalado_ni_lo_numerado(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    regalo, _ = await stores.shop.grant(GUILD, OWNER, await stores.item(5_000), NOW)
    numerado = await stores.buy(await stores.item(5_000, stock=3))
    found = {a.entry.id: a for a in await stores.monte.appraisals(GUILD, OWNER, NOW)}
    assert found[regalo.id].offer is None and found[numerado].offer is None
    for entry in (regalo.id, numerado):
        with pytest.raises(MonteError):
            await stores.economy.monte(GUILD, stores.monte.sell(GUILD, OWNER, entry, NOW))
    stores.assert_books_balance()


async def test_si_la_caja_no_llega_no_se_mueve_nada(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    entry = await stores.buy(await stores.item(10_000))
    await stores.repo.apply(GUILD, SHOP_ACCOUNT_ID, [LedgerEntry(-await stores.till(), "test")])
    with pytest.raises(TillEmptyError):
        await stores.economy.monte(GUILD, stores.monte.sell(GUILD, OWNER, entry, NOW))
    assert await stores.owner_of(entry) == OWNER


async def test_no_se_pueden_tener_mas_papeletas_de_la_cuenta(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    it = await stores.item(1_000)
    entries = [await stores.buy(it) for _ in range(MAX_TICKETS + 1)]
    for entry in entries[:MAX_TICKETS]:
        await stores.economy.monte(GUILD, stores.monte.pawn(GUILD, OWNER, entry, NOW))
    with pytest.raises(MonteError):
        await stores.economy.monte(GUILD, stores.monte.pawn(GUILD, OWNER, entries[-1], NOW))


async def test_lo_empenado_no_cuenta_en_el_patrimonio(tmp_path: Path) -> None:
    stores = await Stores(tmp_path).start()
    entry = await stores.buy(await stores.item(10_000))
    with sqlite3.connect(stores.path) as connection:
        connection.row_factory = sqlite3.Row
        assert wealth_goods_in(connection, GUILD, NOW)[OWNER] == 10_000
    await stores.economy.monte(GUILD, stores.monte.pawn(GUILD, OWNER, entry, NOW))
    with sqlite3.connect(stores.path) as connection:
        connection.row_factory = sqlite3.Row
        assert OWNER not in wealth_goods_in(connection, GUILD, NOW)
