"""Pruebas de bot.cogs.mines: tablero con botones y movimiento de dinero.

Se usa la economía real sobre un SQLite temporal y un azar fijado para saber
dónde caen las minas.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs.mines import BOOM, GEM, MINE, Mines, MinesBoard
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.mines import MinesGame, Status, multiplier_cents, payout

GUILD_ID = 1
OWNER_ID = 10


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


async def make_cog(tmp_path: Path) -> Mines:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Mines(MagicMock(), economy=EconomyService(repository), rng=random.Random(7))


async def open_board(cog: Mines, *, amount: str = "100", mines: int | None = 3) -> MinesBoard:
    send = AsyncMock(return_value=MagicMock())
    errors = AsyncMock()
    await cog._minas_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=amount,
        mines=mines,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    (board,) = cog.boards
    return board


def place(board: MinesBoard, mines: set[int]) -> MinesGame:
    """Coloca las minas donde diga la prueba (el primer clic debe ir fuera)."""
    assert board.game is not None
    board.game.mine_tiles = frozenset(mines)
    board.game.mines = len(mines)
    return board.game


def buttons(board: MinesBoard) -> list[ui.Button]:
    return [item for item in board.walk_children() if isinstance(item, ui.Button)]


def ledger_sum(tmp_path: Path, user_id: int = OWNER_ID) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


def state_balance(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, STATE_ACCOUNT_ID),
        ).fetchone()
    return int(row[0]) if row else 0


async def test_minas_cobra_y_dibuja_25_casillas_mas_controles(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    all_buttons = buttons(board)
    tiles = [b for b in all_buttons if (b.custom_id or "").startswith("minas:tile:")]
    assert len(tiles) == 25
    assert board.total_children_count <= 40
    labels = [b.label for b in all_buttons if b.label]
    assert labels[0] == "💰 Cobrar" and "🎲 Al azar" in labels
    # Antes de destapar la primera, el menú de minas sigue a mano.
    assert len([i for i in board.walk_children() if isinstance(i, ui.Select)]) == 1


async def test_casilla_buena_sube_y_cobrar_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0, 1, 2})
    await board._reveal(make_interaction(), 10)
    await board._reveal(make_interaction(), 11)
    assert board.game is not None and board.game.gems == 2
    interaction = make_interaction()
    await board._cash_out(interaction)
    paid = payout(100, 3, 2)
    assert board.game.status is Status.CASHED
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    tax = state_balance(tmp_path)
    assert balance == STARTING_BALANCE - 100 + paid - tax
    assert ledger_sum(tmp_path) == balance
    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_awaited_once()
    # Tras cobrar, la fila de controles vuelve a ser la de jugar otra.
    assert any((b.label or "").startswith("🔁 Jugar") for b in buttons(board))


async def test_pisar_mina_lo_pierde_todo_y_enseña_el_tablero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0, 1, 2})
    await board._reveal(make_interaction(), 5)
    await board._reveal(make_interaction(), 1)
    assert board.game is not None and board.game.status is Status.BUSTED
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    emojis = {b.custom_id: str(b.emoji) for b in buttons(board) if b.emoji}
    assert emojis["minas:tile:1"] == BOOM
    assert emojis["minas:tile:0"] == MINE
    assert emojis["minas:tile:5"] == GEM
    assert all(b.disabled for b in buttons(board) if (b.custom_id or "").startswith("minas:tile"))


async def test_cobrar_sin_destapar_no_hace_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    interaction = make_interaction()
    await board._cash_out(interaction)
    interaction.response.defer.assert_awaited_once()
    assert board.game is not None and board.game.playing


async def test_limpiar_el_tablero_cobra_solo_y_sin_tope(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, mines=12)
    place(board, set(range(13, 25)))
    for tile in range(13):
        await board._reveal(make_interaction(), tile)
    assert board.game is not None and board.game.status is Status.CASHED
    # 100 × 0,99 × C(24, 12): el antiguo tope de ×10.000 lo dejaba en 1.000.000.
    assert board.game.payout == 267_711_444
    # Cobrado de verdad, menos la retención del juego (más de la mitad a este nivel).
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) > 100_000_000


async def test_al_azar_destapa_una_casilla(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, mines=1)
    await board._random(make_interaction())
    assert board.game is not None
    assert board.game.random_picks == 1


async def test_otro_miembro_no_puede_jugar_tu_tablero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    interaction = make_interaction(user_id=99)
    assert not await board.interaction_check(interaction)
    interaction.response.send_message.assert_awaited_once()


async def test_jugar_otra_vuelve_a_cobrar_y_las_minas_se_cambian(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0, 1, 2})
    await board._reveal(make_interaction(), 9)
    await board._reveal(make_interaction(), 0)  # boom
    (select,) = [i for i in board.walk_children() if isinstance(i, ui.Select)]
    assert len(select.options) == 12
    await board._choose_mines(make_interaction(), 5)
    assert board.mines == 5 and cog.mines_for(GUILD_ID, OWNER_ID) == 5
    assert board.total_children_count <= 40
    await board._double(make_interaction())
    assert board.stake == 200
    await board._again(make_interaction())
    assert board.game is not None and board.game.playing and board.game.mines == 5
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 300


async def test_cambiar_apuesta_en_plena_partida_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    interaction = make_interaction()
    await board._double(interaction)
    interaction.response.defer.assert_awaited_once()
    assert board.stake == 100


async def test_jugar_otra_sin_saldo_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, amount="all")
    place(board, {0, 1, 2})
    await board._reveal(make_interaction(), 9)
    await board._reveal(make_interaction(), 0)
    interaction = make_interaction()
    await board._again(interaction)
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_apagar_con_partida_a_medias_cobra_o_devuelve(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    await cog.cog_unload()
    # Sin destapar nada: se devuelve la apuesta.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    assert board.game is None

    board = await open_board(cog)
    place(board, {0, 1, 2})
    await board._reveal(make_interaction(), 9)
    await cog.cog_unload()
    assert board.game is not None and board.game.status is Status.CASHED
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert ledger_sum(tmp_path) == balance


async def test_tablero_caducado_cobra_y_apaga_los_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    board.message = MagicMock(edit=AsyncMock())
    place(board, {0, 1, 2})
    await board._reveal(make_interaction(), 9)
    await board.on_timeout()
    assert board.game is not None and board.game.status is Status.CASHED
    assert all(b.disabled for b in buttons(board))


async def test_minas_no_permitidas_se_rechazan(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._minas_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text="100",
        mines=24,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert Mines.parse_mines("5") == 5
    assert Mines.parse_mines("7m") == 7
    assert Mines.parse_mines(None) is None


async def test_minas_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({1234})
    errors = AsyncMock()
    await cog._minas_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=555),
        user=make_user(),
        amount_text="100",
        mines=None,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert not cog.boards


async def test_cobro_enorme_se_anuncia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    game = MinesGame(stake=100, mines=5, mine_tiles=frozenset(range(5)))
    for tile in range(5, 17):
        game.reveal(tile)
    game.cash_out()  # ×40,9
    await cog.shout(game, make_user(), channel)
    channel.send.assert_awaited_once()


async def test_la_primera_casilla_es_segura_y_devuelve_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    for _ in range(20):
        board = await open_board(cog, amount="1", mines=12)
        await board._reveal(make_interaction(), 12)
        assert board.game is not None and board.game.playing
        assert board.game.cashout_value == 1
        await board._cash_out(make_interaction())
        cog.boards.clear()


async def test_el_texto_cuenta_casillas_y_lo_que_suma_la_siguiente(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, mines=3)
    assert "La primera siempre es buena" in board.header()
    place(board, {0, 1, 2})
    for tile in (10, 11, 12):
        await board._reveal(make_interaction(), tile)
    text = board.header()
    assert "💎 **3/22**" in text and "¡Tres limpias!" in text
    extra = payout(100, 3, 4) - payout(100, 3, 3)
    assert f"(+{extra} Y$)" in text
    assert f"×{multiplier_cents(3, 4) // 100}," in text


async def test_batir_el_record_se_avisa(tmp_path: Path) -> None:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    load = AsyncMock(return_value=2)
    cog = Mines(
        MagicMock(), economy=EconomyService(repository), rng=random.Random(7), load_record=load
    )
    board = await open_board(cog, mines=1)
    place(board, {0})
    for tile in (5, 6):
        await board._reveal(make_interaction(), tile)
    assert "Récord personal" not in board.header()
    await board._reveal(make_interaction(), 7)
    assert "🏅 ¡Récord personal!" in board.header()
    await board._cash_out(make_interaction())
    assert await cog.record(GUILD_ID, OWNER_ID) == 3
    load.assert_awaited_once()  # el récord se lee una vez y luego va en memoria


async def test_el_menu_de_minas_sale_al_abrir_y_cambia_la_partida_sin_cobrar_otra_vez(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, mines=3)
    selects = [i for i in board.walk_children() if isinstance(i, ui.Select)]
    assert len(selects) == 1
    assert board.total_children_count <= 40
    await board._choose_mines(make_interaction(), 7)
    assert board.game is not None and board.game.playing
    assert board.game.mines == 7 and board.game.stake == 100
    assert cog.mines_for(GUILD_ID, OWNER_ID) == 7
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100


async def test_tras_destapar_la_primera_el_menu_desaparece_y_no_cambia_nada(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, mines=3)
    place(board, {0, 1, 2})
    await board._reveal(make_interaction(), 9)
    assert not [i for i in board.walk_children() if isinstance(i, ui.Select)]
    interaction = make_interaction()
    await board._choose_mines(interaction, 7)
    interaction.response.defer.assert_awaited_once()
    assert board.game is not None and board.game.mines == 3
