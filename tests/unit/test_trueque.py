"""Pruebas del trueque del Rastro (`trueque`): cuentas, impuestos y botones.

La economía, la tienda y el Rastro son los reales sobre un SQLite temporal, con los
mismos ayudantes que `test_rastro.py`. En cada trueque se comprueba que los objetos
cambian de mochila, que el libro cuadra y que el Estado recibe justo el ITP y la
plusvalía de los dos.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction
from test_rastro import (
    BUYER,
    GUILD,
    NOW,
    SELLER,
    Clock,
    assert_books_balance,
    bought,
    give,
    make_cog,
    make_stores,
    owner_of,
    stocked_item,
)

from bot.cogs.rastro import SwapView
from bot.repositories.economy import InsufficientFundsError
from bot.services.achievements import market_swap_stats
from bot.services.rastro import MarketError, Swap
from bot.services.taxes import capital_gain_tax, itp

PROPOSER, TARGET = SELLER, BUYER


# -- Cuentas -----------------------------------------------------------------------------


def test_cada_uno_paga_el_itp_de_lo_que_recibe() -> None:
    swap = Swap(given_reference=10_000, given_cost=10_000, received_reference=4_000,
                received_cost=4_000)  # fmt: skip
    assert swap.proposer_itp == itp(4_000)
    assert swap.target_itp == itp(10_000)
    # Quien da lo caro no gana nada (recibe menos); quien da lo barato gana la diferencia.
    assert swap.proposer_gain == 0
    assert swap.target_gain == 6_000
    assert swap.target_gain_tax == capital_gain_tax(6_000)


def test_el_dinero_cuenta_para_quien_lo_cobra_y_se_descuenta_a_quien_lo_pone() -> None:
    swap = Swap(given_reference=1_000, given_cost=1_000, received_reference=5_000,
                received_cost=5_000, money=4_000)  # fmt: skip
    assert swap.proposer_gain == 0  # 5.000 recibidos menos 4.000 puestos: no gana
    assert swap.target_gain == 0  # 1.000 + 4.000 por algo que le costó 5.000
    assert swap.proposer_itp_base == 5_000
    assert swap.proposer_pays == 4_000 + itp(5_000)
    assert swap.target_pays == itp(1_000) - 4_000
    # Lo recibido cuesta su base más el ITP, para venderlo después.
    assert swap.proposer_cost == 5_000 + itp(5_000)
    assert swap.target_cost == 1_000 + itp(1_000)


def test_poner_mas_dinero_que_el_valor_sube_la_base_del_itp() -> None:
    swap = Swap(given_reference=0, given_cost=0, received_reference=100,
                received_cost=0, money=900)  # fmt: skip
    assert swap.proposer_itp_base == 900
    assert swap.target_gain == 900


def test_los_logros_del_trueque() -> None:
    from datetime import datetime

    from bot.services.levels import TIMEZONE

    monday = datetime(2026, 10, 12, 12, tzinfo=TIMEZONE)
    lucky = market_swap_stats(gave=100, got=5_000, money=0, proposer=True, titles=False,
                              when=monday).add  # fmt: skip
    assert lucky == {"market_swaps": 1, "market_swap_lopsided": 1}
    fleeced = market_swap_stats(gave=5_000, got=100, money=50, proposer=True, titles=True,
                                when=monday).add  # fmt: skip
    assert fleeced["market_swap_fleeced"] == 1 and fleeced["market_swap_money"] == 1
    assert fleeced["market_swap_titles"] == 1


# -- Con dinero de verdad --------------------------------------------------------------


async def two_items(tmp_path: Path) -> tuple:
    economy, shop, market = await make_stores(tmp_path, Clock())
    yate = await stocked_item(shop, 10_000)
    cuadro = await shop.create_item(
        GUILD, kind=yate.kind, name="Cuadro", emoji="🖼️", description="", price=4_000,
        igic="general", now=NOW,
    )  # fmt: skip
    mine = await bought(economy, shop, yate, PROPOSER)
    theirs = await bought(economy, shop, cuadro, TARGET)
    return economy, shop, market, mine, theirs


async def test_el_trueque_cambia_los_objetos_y_paga_los_impuestos_al_estado(
    tmp_path: Path,
) -> None:
    economy, shop, market, mine, theirs = await two_items(tmp_path)
    await give(economy, PROPOSER, 5_000)
    await give(economy, TARGET, 5_000)
    state = await economy.state_balance(GUILD)
    before = {u: await economy.balance(GUILD, u) for u in (PROPOSER, TARGET)}

    barter, balances = await economy.market(
        GUILD, market.swap(GUILD, PROPOSER, TARGET, mine, theirs, 500, NOW)
    )

    swap = barter.swap
    assert await owner_of(shop, mine) == TARGET
    assert await owner_of(shop, theirs) == PROPOSER
    assert balances[PROPOSER] == before[PROPOSER] - swap.proposer_pays
    assert balances[TARGET] == before[TARGET] - swap.target_pays
    assert await economy.state_balance(GUILD) == state + swap.taxes
    assert swap.taxes == itp(4_000) + itp(10_000) + capital_gain_tax(10_000 + 500 - 4_000)
    assert_books_balance(tmp_path)


async def test_si_a_alguien_no_le_llega_no_cambia_nada(tmp_path: Path) -> None:
    economy, shop, market, mine, theirs = await two_items(tmp_path)
    poor = await economy.balance(GUILD, PROPOSER)
    with pytest.raises(InsufficientFundsError):
        await economy.market(
            GUILD, market.swap(GUILD, PROPOSER, TARGET, mine, theirs, poor + 1, NOW)
        )
    assert await owner_of(shop, mine) == PROPOSER
    assert await owner_of(shop, theirs) == TARGET
    assert_books_balance(tmp_path)


async def test_no_se_cambia_lo_que_ya_no_esta_ni_con_uno_mismo(tmp_path: Path) -> None:
    economy, _shop, market, mine, theirs = await two_items(tmp_path)
    with pytest.raises(MarketError):
        await economy.market(GUILD, market.swap(GUILD, PROPOSER, PROPOSER, mine, theirs, 0, NOW))
    with pytest.raises(MarketError, match="ya no está"):
        await economy.market(GUILD, market.swap(GUILD, PROPOSER, TARGET, mine, mine, 0, NOW))


# -- Botones -----------------------------------------------------------------------------


def member(user_id: int) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    user.display_name = f"Miembro {user_id}"
    user.mention = f"<@{user_id}>"
    return user


async def test_la_oferta_de_trueque_se_acepta_con_su_boton(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    yate = await stocked_item(cog.shop, 10_000)
    cuadro = await cog.shop.create_item(
        GUILD, kind=yate.kind, name="Cuadro", emoji="🖼️", description="", price=4_000,
        igic="general", now=NOW,
    )  # fmt: skip
    mine = await bought(cog.economy, cog.shop, yate, PROPOSER)
    theirs = await bought(cog.economy, cog.shop, cuadro, TARGET)
    await give(cog.economy, TARGET, 5_000)
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    proposer, target = member(PROPOSER), member(TARGET)

    content, embed, view = await cog.barter(guild, proposer, target, "yate", "cuadro", None)
    assert content == target.mention and isinstance(view, SwapView)
    assert "Yate" in embed.description and "Cuadro" in embed.description

    stranger = fake_interaction(proposer)
    await view.accept.callback(stranger)
    assert "no es para ti" in stranger.response.send_message.await_args.args[0]

    click = fake_interaction(target)
    click.guild = guild
    await view.accept.callback(click)
    assert view.is_finished()
    assert await owner_of(cog.shop, mine) == TARGET
    assert await owner_of(cog.shop, theirs) == PROPOSER
    done = click.edit_original_response.await_args.kwargs["embed"].description
    assert "Cuadro" in done and "Yate" in done
    assert_books_balance(tmp_path)


async def test_un_trueque_con_un_bot_o_sin_el_objeto_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    robot = member(TARGET)
    robot.bot = True
    text, embed, _view = await cog.barter(guild, member(PROPOSER), robot, "yate", "x", None)
    assert embed is None and "bots" in text
    text, embed, _view = await cog.barter(
        guild, member(PROPOSER), member(TARGET), "yate", "x", None
    )
    assert embed is None and text
