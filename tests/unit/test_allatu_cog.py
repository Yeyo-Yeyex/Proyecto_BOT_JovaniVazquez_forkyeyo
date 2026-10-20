"""Pruebas de bot.cogs.allatu: el plató con botones y el movimiento de dinero.

Se usa la economía real sobre un SQLite temporal y repartos de cajas colocados a
mano. La mayoría van sin dibujo (el plató de texto, instantáneo); las de imagen
usan el doble de `render_fakes` y miran qué se pinta y cuándo.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from fractions import Fraction
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction
from render_fakes import FakeAllatuScene

from bot.cogs.allatu import CONDOLENCE_LINES, Allatu, AllatuBoard
from bot.repositories.economy import EconomyRepository
from bot.services.allatu import BOXES, ROUNDS, Ending, Phase, Table
from bot.services.economy import STARTING_BALANCE, EconomyService

GUILD_ID = 1
OWNER_ID = 10


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def click(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


async def make_cog(tmp_path: Path, *, images: bool = False) -> Allatu:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Allatu(
        MagicMock(),
        economy=EconomyService(repository),
        rng=random.Random(7),
        renderer=FakeAllatuScene(images=images),  # type: ignore[arg-type]
        ahead=images,
    )


async def open_board(
    cog: Allatu, *, amount: str = "100", table: Table | None = None
) -> AllatuBoard:
    errors = AsyncMock()
    await cog._allatu_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=amount,
        table=table,
        send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
        send_error=errors,
    )
    errors.assert_not_awaited()
    (board,) = cog.boards
    return board


def rig(board: AllatuBoard, values: list[Fraction], gordo: int = 10) -> None:
    """Pone los premios de cada caja (la prueba elige la partida)."""
    assert board.game is not None
    board.game.values = tuple(values)
    board.game.gordo = gordo


def buttons(board: AllatuBoard) -> list[ui.Button]:
    return [item for item in board.walk_children() if isinstance(item, ui.Button)]


def by_id(board: AllatuBoard, custom_id: str) -> ui.Button:
    (button,) = [b for b in buttons(board) if b.custom_id == f"allatu:{custom_id}"]
    return button


def ledger_sum(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, OWNER_ID),
        ).fetchone()
    return int(total)


async def to_offer(board: AllatuBoard) -> None:
    """Abre cajas hasta que llama la banca, sin sacar el gordo (que trae el pésame)."""
    game = board.game
    assert game is not None
    while game.phase is Phase.OPEN:
        box = next((b for b in game.openable if game.values[b] != game.gordo), game.openable[0])
        await board._press_box(click(), box)


async def test_el_plato_cobra_y_pone_22_cajas_y_tres_controles(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    cajas = [b for b in buttons(board) if (b.custom_id or "").startswith("allatu:caja:")]
    assert [b.custom_id for b in cajas] == [f"allatu:caja:{i}" for i in range(BOXES)]
    assert len(buttons(board)) == 25
    assert board.game is not None and board.game.phase is Phase.PICK
    assert "Elige tu caja" in (board.embed.description or "")


async def test_elegir_caja_y_abrir_hasta_que_llama_la_banca(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    await board._press_box(click(), 4)
    game = board.game
    assert game is not None and game.own == 4
    assert by_id(board, "caja:4").disabled and by_id(board, "caja:4").emoji.name == "🔒"
    await to_offer(board)
    assert game.phase is Phase.OFFER and len(game.opened) == ROUNDS[0]
    trato = by_id(board, "trato")
    assert trato.label is not None and trato.label.startswith("✅ Trato")
    opened = by_id(board, f"caja:{game.opened[0]}")
    assert opened.disabled and (opened.label or "").startswith("×")
    assert "Perro Sanxe ofrece" in (board.embed.description or "")


async def test_trato_paga_la_oferta_y_el_libro_cuadra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    await board._press_box(click(), 0)
    await to_offer(board)
    game = board.game
    assert game is not None and game.offer is not None
    offer = game.offer.amount
    await board._deal(click())
    assert game.ending is Ending.DEAL and game.payout == offer
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance == ledger_sum(tmp_path)
    assert balance <= STARTING_BALANCE - 100 + offer
    # Al acabar: otra partida, apuesta y menú de tabla.
    assert by_id(board, "again").label.startswith("🔁 Otra")
    assert any(isinstance(i, ui.Select) for i in board.walk_children())


async def test_no_trato_hasta_el_final_y_cambiar_de_caja(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    rig(board, [Fraction(0)] * 21 + [Fraction(10)])
    await board._press_box(click(), 0)
    game = board.game
    assert game is not None
    while game.phase is not Phase.FINAL:
        if game.phase is Phase.OPEN:
            await board._press_box(click(), game.openable[0])
        else:
            await board._no_deal(click())
    assert game.other == 21
    assert by_id(board, "cambio").label == "🔄 Cambio por la 22"
    await board._swap(click())
    assert game.ending is Ending.SWAPPED and game.payout == 1_000
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance == ledger_sum(tmp_path)
    assert balance > STARTING_BALANCE


async def test_regatear_una_sola_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    game = board.game
    assert game is not None
    game.haggle_luck = (True,) * len(ROUNDS)
    await board._press_box(click(), 0)
    await to_offer(board)
    before = game.offer
    assert before is not None and not by_id(board, "regatear").disabled
    await board._haggle(click())
    assert game.offer is not None and game.offer.haggled
    assert game.offer.amount >= before.amount
    assert by_id(board, "regatear").disabled
    assert "Sube la oferta" in (board.embed.description or "") or "🤝" in (
        board.embed.description or ""
    )


async def test_cambiar_de_tabla_antes_de_elegir_caja_no_mueve_dinero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, table=Table.NORMAL)
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    await board._cycle_table(click())
    assert board.game is not None and board.game.table is Table.PICANTE
    assert board.game.phase is Phase.PICK and board.game.stake == 100
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == balance
    assert cog.table_for(GUILD_ID, OWNER_ID) is Table.PICANTE


async def test_caducar_sin_caja_devuelve_y_con_oferta_la_acepta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    await board.force_settle()
    assert board.game is None
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    cog.boards.clear()
    board = await open_board(cog)
    await board._press_box(click(), 0)
    await to_offer(board)
    game = board.game
    assert game is not None and game.offer is not None
    offer = game.offer.amount
    await board.force_settle()
    assert game.ending is Ending.DEAL and game.payout == offer
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == ledger_sum(tmp_path)


async def test_solo_el_dueno_juega(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    other = click(99)
    assert await board.interaction_check(other) is False
    other.response.send_message.assert_awaited()


async def test_sin_saldo_no_abre_plato(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._allatu_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=str(STARTING_BALANCE + 1),
        table=None,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited()
    assert not cog.boards


async def test_la_racha_de_dias_sigue_o_se_reinicia(tmp_path: Path) -> None:
    from datetime import date

    cog = await make_cog(tmp_path)
    day = date(2026, 10, 16)
    assert await cog.play_day(GUILD_ID, OWNER_ID, day) == (day.toordinal(),) * 2
    nxt = date(2026, 10, 17)
    assert await cog.play_day(GUILD_ID, OWNER_ID, nxt) == (day.toordinal(), nxt.toordinal())
    gap = date(2026, 10, 20)
    assert await cog.play_day(GUILD_ID, OWNER_ID, gap) == (gap.toordinal(),) * 2


# -- Con imagen ------------------------------------------------------------------------


@pytest.fixture
def no_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.cogs.allatu.REVEAL_MARGIN_SECONDS", 0)


async def settle_painter(board: AllatuBoard) -> None:
    """Deja que el pintor de fondo acabe lo pendiente."""
    for _ in range(200):
        await asyncio.sleep(0)
        if board._next_job() is None:
            break
    await asyncio.sleep(0)


async def test_con_imagen_cada_caja_se_pinta_una_vez_y_antes_del_clic(
    tmp_path: Path, no_margin: None
) -> None:
    cog = await make_cog(tmp_path, images=True)
    board = await open_board(cog)
    renderer: FakeAllatuScene = cog.renderer  # type: ignore[assignment]
    assert ("opening",) in renderer.calls
    await settle_painter(board)
    # Antes de elegir: las 22 imágenes de elegir caja, la ruleta de la otra tabla y
    # las 22 cajas.
    assert ("picks",) in renderer.calls
    assert sum(1 for c in renderer.calls if c[0] == "open_box") == BOXES
    await board._press_box(click(), 0)
    await settle_painter(board)
    renderer.calls.clear()
    await board._press_box(click(), 5)
    # La caja 5 ya estaba pintada: solo se pinta el plató de después.
    assert ("open_box", 5) not in renderer.calls
    assert ("still",) in renderer.calls
    assert board.image == "allatu.png"


async def test_con_imagen_la_oferta_tiene_pintados_sus_tres_botones(
    tmp_path: Path, no_margin: None
) -> None:
    cog = await make_cog(tmp_path, images=True)
    board = await open_board(cog)
    renderer: FakeAllatuScene = cog.renderer  # type: ignore[assignment]
    await board._press_box(click(), 0)
    game = board.game
    assert game is not None
    while game.phase is Phase.OPEN:
        await board._press_box(click(), game.openable[0])
    assert ("call",) in renderer.calls
    await settle_painter(board)
    assert {("deal",), ("no_deal",), ("haggle",)} <= set(renderer.calls)
    renderer.calls.clear()
    await board._deal(click())
    assert ("deal",) not in renderer.calls
    assert game.phase is Phase.DONE
    await settle_painter(board)
    # Al acabar ya está la ruleta de 🔁.
    assert ("opening",) in renderer.calls
    renderer.calls.clear()
    await board._again(click())
    assert board.game is not None and board.game.phase is Phase.PICK
    await settle_painter(board)
    # La ruleta de 🔁 no se repinta al pulsar: la única que se pinta es la de la
    # tabla siguiente, por adelantado y después de las imágenes de elegir caja.
    assert renderer.calls.count(("opening",)) == 1
    assert renderer.calls.index(("picks",)) < renderer.calls.index(("opening",))


async def test_abrir_el_gordo_trae_el_pesame_y_no_trato_sigue_la_ronda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    rig(board, [Fraction(0), Fraction(10)] + [Fraction(1)] * 20)
    await board._press_box(click(), 0)
    await board._press_box(click(), 1)
    game = board.game
    assert game is not None and game.offer is not None and game.offer.condolence
    text = board.embed.description or ""
    assert any(line in text for line in CONDOLENCE_LINES)
    assert "Con ❌ sigue la ronda" in text
    assert by_id(board, "trato").label.startswith("✅ Trato")
    await board._no_deal(click())
    assert game.phase is Phase.OPEN and game.left_in_round == ROUNDS[0] - 1
