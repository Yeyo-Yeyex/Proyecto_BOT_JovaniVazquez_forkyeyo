"""Pruebas de bot.cogs.poker: la mesa del Casino Hold'em con botones, precarga y dinero.

Se usa la economía real sobre un SQLite temporal y un dibujante falso (las
imágenes tienen sus propias pruebas) con el margen del GIF a cero. Las cartas se
fuerzan poniendo `view.cards` antes de repartir: la mano usa las ya sorteadas.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs import poker as poker_cog
from bot.cogs.poker import COLOR_IDLE, Poker, PokerView
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import EconomyRepository
from bot.services.blackjack import Card
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.poker import CALL_FACTOR, Deal, Outcome
from bot.services.poker_render import Media, Showdown

GUILD_ID = 1
OWNER_ID = 10
# Palos de blackjack.SUITS: 0 ♠, 1 ♥, 2 ♦, 3 ♣.
SPADE, HEART, DIAMOND, CLUB = range(4)
#: Tablero seco: K♦ 9♣ 6♥ 3♠ 2♣.
DRY = (Card(13, DIAMOND), Card(9, CLUB), Card(6, HEART), Card(3, SPADE), Card(2, CLUB))
#: Trío de reyes contra trío de nueves: gana el jugador (ante 1 a 1, ir 1 a 1).
WINNING = Deal(
    player=(Card(13, HEART), Card(13, SPADE)), dealer=(Card(9, DIAMOND), Card(9, SPADE)), board=DRY
)
#: Pareja de ochos contra trío de reyes: gana la banca.
LOSING = Deal(
    player=(Card(8, HEART), Card(8, SPADE)), dealer=(Card(13, CLUB), Card(13, SPADE)), board=DRY
)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(poker_cog, "REVEAL_MARGIN_SECONDS", 0)


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego" if user_id == OWNER_ID else "Intruso"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


def fake_renderer() -> MagicMock:
    renderer = MagicMock()
    renderer.deal = AsyncMock(return_value=Media(gif=b"DEAL", png=b"dealpng", seconds=0.0))
    renderer.showdown = AsyncMock(
        return_value=Showdown(
            call=Media(gif=b"CALL", png=b"callpng", seconds=0.0),
            fold=Media(gif=b"FOLD", png=b"foldpng", seconds=0.0),
        )
    )
    renderer.board = AsyncMock(return_value=b"png")
    renderer.close = AsyncMock()
    return renderer


async def make_cog(tmp_path: Path) -> Poker:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Poker(
        MagicMock(),
        economy=EconomyService(repository),
        rng=random.Random(1),
        renderer=fake_renderer(),
    )


async def open_table(
    cog: Poker, *, amount: str = "100", cards: Deal = WINNING
) -> tuple[PokerView, AsyncMock]:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    await cog._poker_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=amount,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    (view,) = cog.views
    view.cards = cards
    await asyncio.sleep(0)  # deja correr la precarga del reparto
    return view, send


def labels(view: PokerView) -> list[str]:
    return [item.label or "" for item in view.children if isinstance(item, ui.Button)]


async def balance(cog: Poker) -> int:
    return await cog.economy.balance(GUILD_ID, OWNER_ID)


def ledger(tmp_path: Path) -> list[tuple[str, int]]:
    """Movimientos del dueño (motivo, importe), en orden y sin el saldo de bienvenida."""
    connection = sqlite_module.connect(tmp_path / "bot.db")
    try:
        rows = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id",
            (GUILD_ID, OWNER_ID),
        ).fetchall()
    finally:
        connection.close()
    return [(row[0], row[1]) for row in rows if row[0] != "bienvenida"]


async def deal(view: PokerView) -> MagicMock:
    interaction = make_interaction()
    await view._deal_click(interaction)
    return interaction


# -- Abrir y repartir -------------------------------------------------------------------


async def test_abrir_la_mesa_no_cobra_y_ya_dibuja_el_reparto(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, send = await open_table(cog, amount="250")
    assert await balance(cog) == STARTING_BALANCE
    assert ledger(tmp_path) == []
    assert labels(view) == ["🃏 Repartir · 250 Y$", "½", "×2", "💰 All-in", "📋 Tabla"]
    assert "¿Una mano contra la banca?" in (send.await_args.kwargs["embed"].description or "")
    cog.renderer.deal.assert_awaited_once()


async def test_sin_saldo_no_se_abre_la_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._poker_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=str(STARTING_BALANCE + 1),
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert not cog.views


async def test_repartir_cobra_el_ante_y_ofrece_ir_o_retirarse(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    interaction = await deal(view)
    assert ledger(tmp_path) == [("poker:apuesta", -100)]
    assert view.hand is not None and view.hand.deciding
    assert labels(view) == ["✅ Ir · 200 Y$", "🏳️ Retirarse", "📋 Tabla"]
    gif_call, png_call = interaction.edit_original_response.await_args_list
    assert gif_call.kwargs["attachments"][0].fp.read() == b"DEAL"
    assert gif_call.kwargs["embed"].color == COLOR_IDLE
    text = png_call.kwargs["embed"].description or ""
    assert "Llevas trío de reyes" in text
    assert "Tus cartas: **K♥** **K♠**" in text
    # Los dos finales ya se dibujan mientras se mira el flop.
    await asyncio.sleep(0)
    assert cog.renderer.showdown.await_args.args[0] == WINNING


# -- Decidir ----------------------------------------------------------------------------


async def test_ir_y_ganar_paga_el_ante_y_la_de_ir(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await deal(view)
    interaction = make_interaction()
    await view._call(interaction)
    hand = view.hand
    assert hand is not None and hand.outcome is Outcome.WIN
    won = 2 * 100 + 2 * CALL_FACTOR * 100
    assert ledger(tmp_path) == [
        ("poker:apuesta", -100),
        ("poker:apuesta", -CALL_FACTOR * 100),
        ("poker:premio", won),
    ]
    assert await balance(cog) == STARTING_BALANCE + hand.net
    gif_call, png_call = interaction.edit_original_response.await_args_list
    assert gif_call.kwargs["attachments"][0].fp.read() == b"CALL"
    assert "gana a" in (png_call.kwargs["embed"].description or "")
    # Mano terminada: cartas nuevas y su reparto ya se dibuja.
    assert view.cards != WINNING
    assert labels(view)[0] == "🃏 Repartir · 100 Y$"


async def test_ir_y_perder_lo_pierde_todo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog, cards=LOSING)
    await deal(view)
    await view._call(make_interaction())
    assert view.hand is not None and view.hand.outcome is Outcome.LOSE
    assert await balance(cog) == STARTING_BALANCE - 100 * (1 + CALL_FACTOR)


async def test_retirarse_pierde_el_ante_y_dice_lo_que_habria_pasado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await deal(view)
    interaction = make_interaction()
    await view._fold(interaction)
    assert ledger(tmp_path) == [("poker:apuesta", -100)]
    gif_call, png_call = interaction.edit_original_response.await_args_list
    assert gif_call.kwargs["attachments"][0].fp.read() == b"FOLD"
    text = png_call.kwargs["embed"].description or ""
    assert "-100 Y$" in text
    assert "🔮 Yendo habrías ganado 300 Y$." in text


async def test_si_no_llega_para_ir_solo_puede_retirarse(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog, amount=str(STARTING_BALANCE // 2))
    await deal(view)
    interaction = make_interaction()
    await view._call(interaction)
    assert view.hand is not None and view.hand.deciding
    interaction.followup.send.assert_awaited_once()
    assert "Solo puedes retirarte" in interaction.followup.send.await_args.args[0]
    await view._fold(make_interaction())
    assert view.hand.outcome is Outcome.FOLD


async def test_all_in_deja_sitio_para_ir(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await view._all_in(make_interaction())
    assert view.stake == STARTING_BALANCE // (1 + CALL_FACTOR)


# -- Mesa -------------------------------------------------------------------------------


async def test_un_intruso_no_juega_pero_puede_ver_la_tabla(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    intruder = make_interaction(user_id=99)
    intruder.data = {"custom_id": "poker:deal"}
    assert not await view.interaction_check(intruder)
    peek = make_interaction(user_id=99)
    peek.data = {"custom_id": "poker:table"}
    assert await view.interaction_check(peek)
    await view._paytable(peek)
    embed = peek.response.send_message.await_args.kwargs["embed"]
    assert "**Escalera real** · 100 a 1" in (embed.description or "")


async def test_al_caducar_con_la_mano_a_medias_se_retira(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await deal(view)
    await view.on_timeout()
    assert view.hand is not None and view.hand.outcome is Outcome.FOLD
    assert await balance(cog) == STARTING_BALANCE - 100
    assert not cog.views


async def test_al_apagar_se_retira_y_cierra_el_pintor(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await deal(view)
    await cog.cog_unload()
    assert view.hand is not None and view.hand.outcome is Outcome.FOLD
    cog.renderer.close.assert_awaited_once()


async def test_un_full_cobrado_se_anuncia_en_el_canal(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    full = Deal(
        player=(Card(13, HEART), Card(13, SPADE)),
        dealer=(Card(9, DIAMOND), Card(4, SPADE)),
        board=(Card(13, DIAMOND), Card(9, CLUB), Card(9, HEART), Card(3, SPADE), Card(2, CLUB)),
    )
    view, _ = await open_table(cog, cards=full)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    view.channel = channel
    await deal(view)
    await view._call(make_interaction())
    channel.send.assert_awaited_once()
    assert "full de reyes y nueves" in channel.send.await_args.args[0]
