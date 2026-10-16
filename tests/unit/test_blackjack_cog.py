"""Pruebas de bot.cogs.blackjack: mesa con botones y movimiento de dinero.

Se usa la economía real sobre un SQLite temporal, un zapato preparado (las
cartas salen en el orden indicado) y una escena falsa que apunta qué se le pide
dibujar (sin GIF, así que cada jugada acaba en un PNG de relleno).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction
from render_fakes import FakeBlackjackScene

import bot.cogs.blackjack as blackjack_module
from bot.cogs.blackjack import PNG_NAME, Blackjack, BlackjackTable
from bot.repositories.economy import EconomyRepository
from bot.services.blackjack import (
    CUT_CARD,
    DECKS,
    INSURANCE_PAYS,
    PIT_BOSS_SPREAD,
    Action,
    BlackjackGame,
    Card,
    new_shoe,
)
from bot.services.blackjack_scene import Media
from bot.services.economy import STARTING_BALANCE, EconomyService

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555


def c(rank: int, suit: int = 0) -> Card:
    return Card(rank, suit)


def stacked(*cards: Card) -> Callable[[], list[Card]]:
    """Zapato que reparte `cards` en ese orden (jugador, banca, jugador, banca…).

    Se rellena por debajo con sietes para que nunca se acabe.
    """

    def factory() -> list[Card]:
        return [c(7)] * 20 + list(reversed(cards))

    return factory


#: Lo usan también otras pruebas (`test_achievements`) para montar el cog.
FakeRenderer = FakeBlackjackScene


@pytest.fixture(autouse=True)
def no_reveal_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blackjack_module, "REVEAL_MARGIN_SECONDS", 0)


async def make_cog(tmp_path: Path, *cards: Card, channels=frozenset()) -> Blackjack:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Blackjack(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        shoe_factory=stacked(*cards),
        casino_channel_ids=channels,
    )


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


async def open_table(cog: Blackjack, *, amount: str | None = "100", channel_id: int = 1):  # noqa: ANN201
    message = MagicMock()
    message.edit = AsyncMock()
    send = AsyncMock(return_value=message)
    send_error = AsyncMock()
    await cog._bj_impl(
        guild=SimpleNamespace(id=GUILD_ID),  # type: ignore[arg-type]
        channel=SimpleNamespace(id=channel_id),
        user=make_user(),
        amount_text=amount,
        send=send,
        send_error=send_error,
    )
    table = send.await_args.kwargs["view"] if send.await_args else None
    return table, send, send_error, message


async def balance(cog: Blackjack) -> int:
    return await cog.economy.balance(GUILD_ID, OWNER_ID)


async def test_bj_reparte_al_momento_y_cobra_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))

    table, send, send_error, message = await open_table(cog)

    send_error.assert_not_awaited()
    # Primero la mesa «repartiendo…» (Discord da 3 s) y luego la imagen.
    assert "files" not in send.await_args.kwargs
    assert [f.filename for f in message.edit.await_args.kwargs["attachments"]] == [PNG_NAME]
    assert isinstance(table, BlackjackTable)
    assert table.game.player_turn
    assert table in cog.tables
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_blackjack_al_repartir_paga_3_a_2(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(1), c(9), c(13), c(8))

    table, *_ = await open_table(cog)

    assert table.game.settled
    assert await balance(cog) == STARTING_BALANCE + 150
    assert not table.deal_button.disabled


async def test_pedir_y_pasarse_pierde_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8), c(10))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table.act(interaction, Action.HIT)

    assert table.game.settled
    assert table.game.hands[0].busted
    assert await balance(cog) == STARTING_BALANCE - 100
    # El clic se acepta antes de pagar; luego, banca destapando y resultado.
    interaction.response.defer.assert_awaited_once()
    assert interaction.edit_original_response.await_count >= 2


async def test_plantarse_y_la_banca_roba_hasta_pasarse(tmp_path: Path) -> None:
    # Jugador 10+8 = 18; banca 10+6 = 16, roba un 10 y se pasa.
    cog = await make_cog(tmp_path, c(10), c(10), c(8), c(6), c(10))
    table, *_ = await open_table(cog)

    await table.act(make_interaction(), Action.STAND)

    assert len(table.game.dealer) == 3
    assert await balance(cog) == STARTING_BALANCE + 100
    assert table.streak == 1


async def test_doblar_cobra_el_doble_y_da_una_carta(tmp_path: Path) -> None:
    # Jugador 6+5 = 11, dobla y recibe un 10 (21); banca 10+8 = 18.
    cog = await make_cog(tmp_path, c(6), c(10), c(5), c(8), c(10))
    table, *_ = await open_table(cog)

    await table.act(make_interaction(), Action.DOUBLE)

    assert table.game.hands[0].doubled
    assert await balance(cog) == STARTING_BALANCE + 200


async def test_doblar_sin_saldo_avisa_y_no_cambia_la_mano(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(6), c(10), c(5), c(8), c(10))
    table, *_ = await open_table(cog, amount="all")
    interaction = make_interaction()

    await table.act(interaction, Action.DOUBLE)

    assert "necesitas" in interaction.followup.send.await_args.args[0]

    assert len(table.game.hands[0].cards) == 2
    assert await balance(cog) == 0


async def test_separar_cobra_otra_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(8), c(10), c(8, 1), c(9), c(3), c(2))
    table, *_ = await open_table(cog)

    await table.act(make_interaction(), Action.SPLIT)

    assert len(table.game.hands) == 2
    assert await balance(cog) == STARTING_BALANCE - 200


async def test_accion_ilegal_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table.act(interaction, Action.SPLIT)

    interaction.response.defer.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_otro_miembro_no_puede_jugar_tu_mano(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))
    table, *_ = await open_table(cog)
    intruder = make_interaction(user_id=99)

    assert not await table.interaction_check(intruder)
    assert intruder.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_repartir_otra_mano_vuelve_a_cobrar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8), c(10))
    table, *_ = await open_table(cog)
    await table.act(make_interaction(), Action.HIT)  # se pasa: -100

    await table._deal_again(make_interaction())

    assert table.game.player_turn
    assert await balance(cog) == STARTING_BALANCE - 200


async def test_repartir_con_la_mano_en_juego_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table._deal_again(interaction)

    interaction.response.defer.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_mesa_caducada_planta_y_paga_la_mano(tmp_path: Path) -> None:
    # Jugador 20 contra banca 10+7 = 17: al caducar se planta y gana.
    cog = await make_cog(tmp_path, c(10), c(10), c(10), c(7))
    table, _, _, message = await open_table(cog)

    await table.on_timeout()

    assert table.game.settled
    assert await balance(cog) == STARTING_BALANCE + 100
    assert table not in cog.tables
    message.edit.assert_awaited()


async def test_mesa_caducada_con_la_mano_ya_pagada_solo_apaga_los_botones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path, c(10), c(10), c(10), c(7))
    table, _, _, message = await open_table(cog)
    await table.force_settle()
    assert table.game.settled
    still = AsyncMock(return_value=b"PNG")
    monkeypatch.setattr(cog.renderer, "still", still)
    message.edit.reset_mock()

    await table.on_timeout()

    still.assert_not_awaited()
    kwargs = message.edit.await_args.kwargs
    assert "attachments" not in kwargs
    assert all(b.disabled for b in table.children if isinstance(b, discord.ui.Button))


async def test_mesa_caducada_con_la_mano_a_medias_la_planta_y_sube_la_mesa(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, c(10), c(10), c(10), c(7))
    table, _, _, message = await open_table(cog)
    assert not table.game.settled
    message.edit.reset_mock()

    await table.on_timeout()

    assert table.game.settled
    (png,) = message.edit.await_args.kwargs["attachments"]
    assert png.filename == PNG_NAME


async def test_apagar_el_bot_paga_las_manos_abiertas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(10), c(10), c(7))
    table, *_ = await open_table(cog)

    await cog.cog_unload()

    assert table.game.settled
    assert await balance(cog) == STARTING_BALANCE + 100
    assert not cog.tables


async def test_bj_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))

    table, send, send_error, _ = await open_table(cog, channel_id=2)

    send.assert_not_awaited()
    assert f"<#{CASINO_CHANNEL}>" in send_error.await_args.args[0]
    assert await balance(cog) == STARTING_BALANCE


async def test_bj_con_mas_de_lo_que_tienes_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)

    _, send, send_error, _ = await open_table(cog, amount="5k")

    send.assert_not_awaited()
    assert "No te llega" in send_error.await_args.args[0]


async def test_all_in_tras_la_mano_pone_todo_el_saldo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8), c(10))
    table, *_ = await open_table(cog)
    await table.act(make_interaction(), Action.HIT)

    await table._all_in(make_interaction())

    assert table.stake == STARTING_BALANCE - 100


async def test_la_banca_con_blackjack_se_lleva_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(13), c(9), c(1))

    table, *_ = await open_table(cog)

    assert table.game.settled
    assert table.game.net == -100
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_con_un_as_de_la_banca_salen_los_botones_del_seguro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(1), c(9), c(13))

    table, *_ = await open_table(cog)

    assert table.game.insurance_pending
    assert all(b in table.children for b in table.insurance_buttons.values())
    assert all(b.disabled for b in table.action_buttons.values())
    assert table.deal_button.disabled


async def test_seguro_cobra_media_apuesta_y_paga_2_a_1(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(1), c(9), c(13))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table.insure(interaction, take=True)

    assert table.game.settled
    assert table.game.insurance_paid
    assert await balance(cog) == STARTING_BALANCE
    interaction.response.defer.assert_awaited_once()
    assert all(b not in table.children for b in table.insurance_buttons.values())


async def test_sin_seguro_y_sin_blackjack_de_la_banca_se_juega(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(1), c(9), c(7))
    table, *_ = await open_table(cog)

    await table.insure(make_interaction(), take=False)

    assert table.game.player_turn
    assert not table.action_buttons[Action.HIT].disabled
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_con_blackjack_y_as_de_la_banca_se_ofrece_el_seguro_con_sus_cifras(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, c(1), c(1), c(13), c(13))
    table, *_ = await open_table(cog)

    assert table.game.insurance_pending
    embed = blackjack_module.table_embed(
        owner="Diego", game=table.game, balance=0, stake=100, streak=0
    )
    assert "Tienes blackjack: asegurado cobras 100 Y$ pase lo que pase (dinero par)" in (
        embed.description or ""
    )

    await table.insure(make_interaction(), take=True)

    # La banca también tenía blackjack: la mano empata y el seguro paga 2:1.
    assert table.game.settled
    assert await balance(cog) == STARTING_BALANCE + 50 * INSURANCE_PAYS


async def test_blackjack_sin_asegurar_y_banca_sin_blackjack_cobra_3_a_2(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(1), c(1), c(13), c(7))
    table, *_ = await open_table(cog)

    await table.insure(make_interaction(), take=False)

    assert table.game.settled
    assert await balance(cog) == STARTING_BALANCE + 150


async def test_la_mesa_no_tiene_tope_de_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))
    await cog.economy.grant(GUILD_ID, OWNER_ID, amount=50_000, reason="prueba")
    start = await balance(cog)

    table, _, send_error, _ = await open_table(cog, amount="all")

    send_error.assert_not_awaited()
    assert table.game.stake == start
    assert await balance(cog) == 0


async def test_all_in_y_doblar_ficha_llegan_al_saldo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8), c(10))
    await cog.economy.grant(GUILD_ID, OWNER_ID, amount=50_000, reason="prueba")
    table, *_ = await open_table(cog)
    await table.act(make_interaction(), Action.HIT)
    left = await balance(cog)

    await table._all_in(make_interaction())
    assert table.stake == left
    await table._halve(make_interaction())
    await table._double_stake(make_interaction())
    assert table.stake == left // 2 * 2


def test_dinero_par_con_apuesta_impar_da_las_dos_cifras() -> None:
    # 5 de apuesta: el seguro cuesta 2 y paga 4; sin él, el 3:2 da 7 y quedan 5.
    text = blackjack_module.natural_insurance_text(5, 2)
    assert "asegurado cobras 4 Y$ o 5 Y$" in text
    assert "sin seguro, 7 Y$ o empate" in text


# -- Zapato de mesa: contar cartas ----------------------------------------------------


async def test_el_zapato_dura_entre_manos_y_se_pueden_contar_cartas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.new_shoe = lambda: new_shoe(random.Random(7).shuffle)
    table, *_ = await open_table(cog)
    shoe = table.shoe
    left = len(shoe)
    assert left < DECKS * 52
    table.game.stand_all()
    table.game.settled = True
    assert table._shoe_for(100) is shoe, "la mano siguiente sigue con el mismo zapato"
    assert table.shoe_note is None
    embed = await table.current_embed(balance=0)
    assert any(f.name == "Zapato" and str(left) in f.value for f in embed.fields)


async def test_la_carta_de_corte_baraja_un_zapato_nuevo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.new_shoe = lambda: new_shoe(random.Random(7).shuffle)
    table = BlackjackTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    table.shoe = [c(7)] * (CUT_CARD - 1)
    shoe = table._shoe_for(100)
    assert len(shoe) == DECKS * 52
    assert table.shoe_note == blackjack_module.SHUFFLE_LINE
    assert table._shoe_event == (False, True, 0.0, None)


async def test_el_jefe_de_sala_baraja_si_subes_mucho_con_la_cuenta_alta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.new_shoe = lambda: new_shoe(random.Random(7).shuffle)
    table = BlackjackTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    # Solo quedan dieces: ya han salido las cartas bajas y la cuenta está por las nubes.
    table.shoe, table.lowest = [c(10)] * 104, 100
    assert table._shoe_for(PIT_BOSS_SPREAD * 100) is table.shoe, "una subida de 1 a 4 vale"
    table.shoe, table.lowest = [c(10)] * 104, 100
    shoe = table._shoe_for(PIT_BOSS_SPREAD * 100 + 1)
    assert len(shoe) == DECKS * 52
    assert table.shoe_note in blackjack_module.PIT_BOSS_LINES
    assert table.lowest == PIT_BOSS_SPREAD * 100 + 1
    assert table._shoe_event[0] is True


# -- GIF por adelantado ----------------------------------------------------------------


class RecordingScene:
    """Escena falsa con GIF: apunta cada jugada pintada (la mano de antes y la de después)."""

    def __init__(self) -> None:
        self.calls: list[tuple[BlackjackGame | None, BlackjackGame, object, str]] = []

    async def step(
        self,
        before: BlackjackGame | None,
        after: BlackjackGame,
        *,
        shoe: object = None,
        streak: object = (0, 0),
        skin: object = None,
    ) -> Media:
        key = (skin or {}).get("key", "serie")  # type: ignore[attr-defined]
        self.calls.append((deepcopy(before), deepcopy(after), shoe, key))
        return Media(gif=f"GIF{len(self.calls)}".encode(), png=b"png", seconds=0.0)

    async def still(self, game: BlackjackGame, *, streak: int = 0, skin: object = None) -> bytes:
        return b"png"

    async def close(self) -> None:
        return None


async def ahead_cog(tmp_path: Path, *shoes: list[Card]) -> tuple[Blackjack, RecordingScene]:
    """Cog que pinta por adelantado; cada zapato barajado es el siguiente de `shoes`."""
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    scene = RecordingScene()
    queue = list(shoes)
    cog = Blackjack(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=scene,  # type: ignore[arg-type]
        shoe_factory=lambda: list(queue.pop(0) if len(queue) > 1 else queue[0]),
        ahead=True,
    )
    return cog, scene


def shoe(*cards: Card, filler: int = 200) -> list[Card]:
    """Zapato que reparte `cards` en ese orden, con `filler` sietes debajo."""
    return [c(7)] * filler + list(reversed(cards))


async def settle_painting(table: BlackjackTable) -> None:
    """Espera a que la mesa acabe de pintar por adelantado."""
    while table.ahead.task is not None and not table.ahead.task.done():
        await asyncio.sleep(0)


def uploaded(interaction: MagicMock, index: int = 0) -> bytes:
    """Los bytes del archivo que subió la edición `index` del mensaje."""
    edits = interaction.edit_original_response.await_args_list
    return edits[index].kwargs["attachments"][0].fp.read()


def deals(scene: RecordingScene) -> list[tuple[BlackjackGame | None, BlackjackGame, object, str]]:
    """Lo pintado para repartir otra mano (tras una ya pagada)."""
    return [call for call in scene.calls if call[0] is not None and call[0].settled]


async def test_en_tu_turno_se_pinta_cada_accion_y_pedir_sube_la_pintada(tmp_path: Path) -> None:
    # Tú 10+6 = 16 contra un 9 de la banca: lo probable es pedir.
    cog, scene = await ahead_cog(tmp_path, shoe(c(10), c(9), c(6), c(8), c(3)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    painted = [call[1] for call in scene.calls[1:]]
    assert [len(g.hands[0].cards) for g in painted] == [3, 2, 3]  # pedir, plantarse, doblar
    assert painted[0].player_turn and painted[1].settled and painted[2].hands[0].doubled
    assert await balance(cog) == STARTING_BALANCE - 100, "lo pintado no cobra nada"

    interaction = make_interaction()
    await table.act(interaction, Action.HIT)

    # Sube el GIF de pedir ya pintado, en una sola edición y con los botones encendidos.
    assert uploaded(interaction) == b"GIF2"
    assert interaction.edit_original_response.await_count == 1
    assert table.game.hands[0].cards == painted[0].hands[0].cards
    assert not table.action_buttons[Action.STAND].disabled
    assert table._image == blackjack_module.GIF_NAME
    # Y se pinta ya lo siguiente: las acciones con 19.
    await settle_painting(table)
    assert scene.calls[-1][0].hands[0].cards == table.game.hands[0].cards


async def test_plantarse_sube_el_gif_de_la_banca_y_luego_el_resultado(tmp_path: Path) -> None:
    # Tú 10+8 = 18; la banca 10+6 = 16 roba un 10 y se pasa.
    cog, scene = await ahead_cog(tmp_path, shoe(c(10), c(10), c(8), c(6), c(10)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    interaction = make_interaction()

    await table.act(interaction, Action.STAND)

    assert uploaded(interaction, 0) == b"GIF2"  # con 18, plantarse va primero
    gif_edit, png_edit = interaction.edit_original_response.await_args_list
    assert "Saldo" not in [f.name for f in gif_edit.kwargs["embed"].fields]
    assert "Saldo" in [f.name for f in png_edit.kwargs["embed"].fields]
    assert png_edit.kwargs["attachments"][0].filename == PNG_NAME
    assert len(table.game.dealer) == 3 and table.streak == 1
    assert await balance(cog) == STARTING_BALANCE + 100


async def test_entre_manos_se_pinta_repartir_con_cada_ficha_y_la_mitad_ya_esta(
    tmp_path: Path,
) -> None:
    # Blackjack al repartir (la mano acaba sola) y la siguiente: tú 9+9, banca 5+10.
    cog, scene = await ahead_cog(
        tmp_path, shoe(c(1), c(9), c(13), c(8), c(9), c(5), c(9, 1), c(10))
    )
    table, *_ = await open_table(cog)
    await settle_painting(table)
    assert [g.stake for _, g, _, _ in deals(scene)] == [100, 50, 200, STARTING_BALANCE + 150]

    await table._halve(make_interaction())
    await settle_painting(table)
    painted = len(scene.calls)
    interaction = make_interaction()
    await table._deal_again(interaction)

    assert uploaded(interaction) == b"GIF3"  # el de ½, pintado antes de cambiar la ficha
    assert table.game.hands[0].cards == [c(9), c(9, 1)] and table.game.stake == 50
    repainted = [call for call in scene.calls[painted:] if call[0] is not None and call[0].settled]
    assert not repainted, "no se ha vuelto a pintar el reparto"


async def test_la_carta_de_corte_reparte_del_zapato_barajado_que_ya_estaba_pintado(
    tmp_path: Path,
) -> None:
    # El primer zapato llega a la carta de corte en esta mano: la siguiente sale del otro.
    first = shoe(c(10), c(10), c(8), c(9), filler=CUT_CARD - 2)
    second = shoe(c(1), c(5), c(6), c(10))
    cog, scene = await ahead_cog(tmp_path, first, second)
    table, *_ = await open_table(cog)
    await table.act(make_interaction(), Action.STAND)
    await settle_painting(table)
    deal = next(call for call in deals(scene) if call[1].stake == 100)
    assert deal[2] == blackjack_module.Shoe(left=CUT_CARD - 2, shuffle="cut")

    await table._deal_again(make_interaction())

    assert table.shoe_note == blackjack_module.SHUFFLE_LINE
    assert table.game.hands[0].cards == deal[1].hands[0].cards == [c(1), c(6)]
    assert table.game.dealer == deal[1].dealer


async def test_con_un_as_de_la_banca_se_pintan_los_dos_seguros(tmp_path: Path) -> None:
    cog, scene = await ahead_cog(tmp_path, shoe(c(10), c(1), c(9), c(13)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    assert [call[1].insurance for call in scene.calls[1:3]] == [0, 50]  # sin seguro primero

    interaction = make_interaction()
    await table.insure(interaction, take=True)

    assert uploaded(interaction) == b"GIF3"
    assert table.game.insurance_paid
    assert await balance(cog) == STARTING_BALANCE, "el seguro cubre justo la mano perdida"


async def test_cambiar_de_tapete_vuelve_a_pintar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, scene = await ahead_cog(tmp_path, shoe(c(10), c(9), c(6), c(8), c(3)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    before = len(scene.calls)

    monkeypatch.setattr(table, "skin", lambda: {"key": "tapete_oro"})
    table.prepare()
    await settle_painting(table)

    assert [call[3] for call in scene.calls[before:]] == ["tapete_oro"] * 3


async def test_cambiar_la_ficha_no_vuelve_a_subir_la_imagen(tmp_path: Path) -> None:
    cog, _ = await ahead_cog(tmp_path, shoe(c(1), c(9), c(13), c(8)))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table._halve(interaction)

    kwargs = interaction.edit_original_response.await_args.kwargs
    assert "attachments" not in kwargs
    # La mano acabó en el PNG: el embed sigue apuntando a él.
    assert kwargs["embed"].image.url == f"attachment://{PNG_NAME}"
