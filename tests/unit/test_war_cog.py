"""Pruebas de bot.cogs.war: mesa con botones y movimiento de dinero.

Se usa la economía real sobre un SQLite temporal, un zapato preparado (las
cartas salen en el orden indicado) y una escena falsa que apunta qué se le pide
dibujar (sin GIF, así que cada jugada acaba en un PNG de relleno).
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction
from render_fakes import FakeWarScene

import bot.cogs.war as war_module
from bot.cogs.war import PNG_NAME, War, WarTable, result_headline
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.services.bacara_render import Media
from bot.services.blackjack import Card
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.taxes import gambling_day_tax
from bot.services.war import TIE_PAYS, WAR_TIE_BONUS, Result, WarGame

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555


def c(rank: int, suit: int = 0) -> Card:
    return Card(rank, suit)


def stacked(*cards: Card) -> Callable[[], list[Card]]:
    """Zapato que reparte `cards` en ese orden (jugador, banca, quemadas, jugador, banca)."""

    def factory() -> list[Card]:
        return [c(7)] * 20 + list(reversed(cards))

    return factory


@pytest.fixture(autouse=True)
def no_war_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(war_module, "REVEAL_MARGIN_SECONDS", 0)


async def make_cog(tmp_path: Path, *cards: Card, channels=frozenset()) -> War:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return War(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeWarScene(),  # type: ignore[arg-type]
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


async def open_table(cog: War, *, amount: str | None = "100", channel_id: int = 1):  # noqa: ANN201
    """Abre la mesa como `.guerra`: lo primero que se envía es «repartiendo…» y luego se edita."""
    message = MagicMock()
    message.edit = AsyncMock()
    send = AsyncMock(return_value=message)
    send_error = AsyncMock()
    await cog._guerra_impl(
        guild=SimpleNamespace(id=GUILD_ID),  # type: ignore[arg-type]
        channel=SimpleNamespace(id=channel_id),
        user=make_user(),
        amount_text=amount,
        send=send,
        send_error=send_error,
    )
    table = send.await_args.kwargs["view"] if send.await_args else None
    return table, send, send_error, message


async def balance(cog: War) -> int:
    return await cog.economy.balance(GUILD_ID, OWNER_ID)


def ledger_sum(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, OWNER_ID),
        ).fetchone()
    return int(total)


def state_balance(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, STATE_ACCOUNT_ID),
        ).fetchone()
    return int(row[0]) if row else 0


def custom_ids(table: WarTable) -> set[str]:
    return {b.custom_id for b in table.children if isinstance(b, discord.ui.Button)}


async def test_guerra_reparte_al_momento_y_cobra_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(4))

    table, send, send_error, message = await open_table(cog)

    send_error.assert_not_awaited()
    assert [f.filename for f in message.edit.await_args.kwargs["attachments"]] == [PNG_NAME]
    assert isinstance(table, WarTable)
    assert table in cog.tables
    # Carta más alta: gana, cobra 1:1 y la mesa queda lista para otra mano.
    assert table.game.result is Result.WIN
    assert not table.deal_button.disabled
    assert table.surrender_button.disabled and table.war_button.disabled
    assert await balance(cog) == STARTING_BALANCE + 100 - gambling_day_tax(100)


async def test_los_botones_llevan_custom_id_fijo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(4))
    table, *_ = await open_table(cog)

    assert custom_ids(table) == {
        "guerra:surrender",
        "guerra:war",
        "guerra:deal",
        "guerra:half",
        "guerra:x2",
        "guerra:allin",
        "guerra:side",
    }


async def test_si_gana_la_banca_se_lleva_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(4), c(10))

    table, *_ = await open_table(cog)

    assert table.game.result is Result.LOSE
    assert await balance(cog) == STARTING_BALANCE - 100
    assert table.streak == 0


async def test_el_empate_espera_la_decision_con_la_apuesta_cobrada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1))

    table, *_ = await open_table(cog)

    assert table.game.awaiting_choice
    assert not table.surrender_button.disabled and not table.war_button.disabled
    assert table.deal_button.disabled
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_si_discord_no_deja_ensenar_la_mesa_la_mano_se_cierra_en_el_acto(
    tmp_path: Path,
) -> None:
    """Con un empate a medias y sin mensaje, se rinde: nadie se queda con dinero colgado."""
    cog = await make_cog(tmp_path, c(9), c(9, 1))
    send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=404), "Unknown"))
    with pytest.raises(discord.HTTPException):
        await cog._guerra_impl(
            guild=SimpleNamespace(id=GUILD_ID),  # type: ignore[arg-type]
            channel=SimpleNamespace(id=1),
            user=make_user(),
            amount_text="100",
            send=send,
            send_error=AsyncMock(),
        )
    assert await balance(cog) == STARTING_BALANCE - 50
    assert not cog.tables


async def test_rendirse_devuelve_la_mitad(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table._surrender(interaction)

    assert table.game.result is Result.SURRENDER
    assert await balance(cog) == STARTING_BALANCE - 50
    # El clic se acepta antes de pagar; luego, resultado.
    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_awaited()
    assert not table.deal_button.disabled


async def test_ir_a_la_guerra_ganada_cobra_la_segunda_apuesta_y_paga_1_a_1(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1), c(2), c(3), c(4), c(12), c(5))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table._war(interaction)

    assert table.game.result is Result.WAR_WIN
    assert table.game.burned == [c(2), c(3), c(4)]
    assert await balance(cog) == STARTING_BALANCE + 100 - gambling_day_tax(100)
    interaction.response.defer.assert_awaited_once()
    # Primero la banca quema las cartas, luego sale el resultado.
    assert interaction.edit_original_response.await_count >= 2
    assert table.streak == 1


async def test_la_guerra_perdida_pierde_las_dos_apuestas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1), c(2), c(3), c(4), c(5), c(12))
    table, *_ = await open_table(cog)

    await table._war(make_interaction())

    assert table.game.result is Result.WAR_LOSE
    assert await balance(cog) == STARTING_BALANCE - 200


async def test_la_guerra_empatada_paga_el_premio_extra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1), c(2), c(3), c(4), c(8), c(8, 1))
    table, *_ = await open_table(cog)

    await table._war(make_interaction())

    assert table.game.result is Result.WAR_TIE
    assert await balance(cog) == (
        STARTING_BALANCE + 100 + 100 * WAR_TIE_BONUS - gambling_day_tax(100 + 100 * WAR_TIE_BONUS)
    )


async def test_cada_jugada_pide_su_dibujo_a_la_escena(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1), c(2), c(3), c(4), c(12), c(5))
    table, *_ = await open_table(cog)
    renderer = cog.renderer
    assert [name for name, _ in renderer.calls] == ["deal"]

    await table._war(make_interaction())

    # La guerra se pinta con las cuatro cartas y las tres quemadas ya repartidas.
    name, game = renderer.calls[-1]
    assert name == "war" and len(game.player) == 2 and len(game.burned) == 3


async def test_ir_a_la_guerra_sin_saldo_avisa_y_deja_decidir(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1), c(2), c(3), c(4), c(12), c(5))
    table, *_ = await open_table(cog, amount="all")
    interaction = make_interaction()

    await table._war(interaction)

    assert "Para ir a la guerra necesitas" in interaction.followup.send.await_args.args[0]
    assert table.game.awaiting_choice
    assert await balance(cog) == 0
    # Sigue pudiendo rendirse.
    await table._surrender(make_interaction())
    assert await balance(cog) == STARTING_BALANCE // 2


async def test_decidir_sin_empate_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(4))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table._war(interaction)

    interaction.response.defer.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE + 100 - gambling_day_tax(100)


async def test_la_lateral_se_cobra_con_la_principal_y_paga_10_a_1(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(4))
    table, *_ = await open_table(cog)
    await table._toggle_side(make_interaction())
    assert table.side_on
    assert "✅" in table.side_button.label
    cog.new_shoe = stacked(c(9), c(9, 1))

    await table._deal_again(make_interaction())

    # Principal 100 + lateral 100 cobradas al repartir.
    assert table.game.side == 100
    assert await balance(cog) == STARTING_BALANCE + 100 - gambling_day_tax(100) - 200
    await table._surrender(make_interaction())
    day_net = 100 - 200 + 50 + 100 * (1 + TIE_PAYS)
    assert await balance(cog) == STARTING_BALANCE + day_net - gambling_day_tax(day_net)


async def test_la_lateral_fallada_se_pierde(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(4))
    table, *_ = await open_table(cog)
    await table._toggle_side(make_interaction())

    await table._deal_again(make_interaction())

    assert table.game.result is Result.WIN
    assert await balance(cog) == STARTING_BALANCE + 100 + 100 - 100 - gambling_day_tax(100)


async def test_quitar_la_lateral_vuelve_a_dejarla_en_cero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(4))
    table, *_ = await open_table(cog)
    await table._toggle_side(make_interaction())
    await table._toggle_side(make_interaction())

    await table._deal_again(make_interaction())

    assert table.game.side == 0


async def test_el_libro_cuadra_con_el_saldo_en_una_mano_con_guerra(tmp_path: Path) -> None:
    """Principal, guerra y lateral pasan por `place_bet` y `pay_winnings`: el libro es el saldo."""
    cog = await make_cog(tmp_path, c(10), c(4))
    table, *_ = await open_table(cog)
    await table._toggle_side(make_interaction())
    cog.new_shoe = stacked(c(9), c(9, 1), c(2), c(3), c(4), c(12), c(5))
    await table._deal_again(make_interaction())
    await table._war(make_interaction())

    assert table.game.result is Result.WAR_WIN
    tax = state_balance(tmp_path)
    # Mano 1: +100. Mano 2: 200 al repartir, 100 de la guerra y 300 + 1.100 al cobrar.
    expected = STARTING_BALANCE + 100 - 200 - 100 + 300 + 100 * (1 + TIE_PAYS)
    assert await balance(cog) == expected - tax
    assert await balance(cog) == ledger_sum(tmp_path)


async def test_otro_miembro_no_puede_jugar_tu_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1))
    table, *_ = await open_table(cog)
    intruder = make_interaction(user_id=99)

    assert not await table.interaction_check(intruder)
    assert intruder.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_repartir_con_el_empate_sin_decidir_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table._deal_again(interaction)

    interaction.response.defer.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_repartir_otra_mano_vuelve_a_cobrar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(4), c(10))
    table, *_ = await open_table(cog)

    await table._deal_again(make_interaction())

    assert await balance(cog) == STARTING_BALANCE - 200


async def test_mesa_caducada_con_un_empate_se_rinde_y_paga_la_mitad(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1))
    table, _, _, message = await open_table(cog)
    message.edit.reset_mock()

    await table.on_timeout()

    assert table.game.result is Result.SURRENDER
    assert await balance(cog) == STARTING_BALANCE - 50
    assert table not in cog.tables
    (png,) = message.edit.await_args.kwargs["attachments"]
    assert png.filename == PNG_NAME


async def test_mesa_caducada_con_la_mano_ya_pagada_solo_apaga_los_botones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path, c(10), c(4))
    table, _, _, message = await open_table(cog)
    still = AsyncMock(return_value=b"PNG")
    monkeypatch.setattr(cog.renderer, "still", still)
    message.edit.reset_mock()

    await table.on_timeout()

    still.assert_not_awaited()
    assert "attachments" not in message.edit.await_args.kwargs
    assert all(b.disabled for b in table.children if isinstance(b, discord.ui.Button))
    assert await balance(cog) == STARTING_BALANCE + 100 - gambling_day_tax(100)


async def test_apagar_el_bot_paga_los_empates_abiertos_una_sola_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(9), c(9, 1))
    table, *_ = await open_table(cog)

    await cog.cog_unload()
    await table.force_settle()

    assert table.game.result is Result.SURRENDER
    assert await balance(cog) == STARTING_BALANCE - 50
    assert not cog.tables


async def test_guerra_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))

    _, send, send_error, _ = await open_table(cog, channel_id=2)

    send.assert_not_awaited()
    assert f"<#{CASINO_CHANNEL}>" in send_error.await_args.args[0]
    assert await balance(cog) == STARTING_BALANCE


async def test_guerra_con_mas_de_lo_que_tienes_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)

    _, send, send_error, _ = await open_table(cog, amount="5k")

    send.assert_not_awaited()
    assert "No te llega" in send_error.await_args.args[0]


async def test_all_in_con_la_lateral_deja_sitio_para_las_dos_apuestas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(4), c(10))
    table, *_ = await open_table(cog)
    await table._toggle_side(make_interaction())

    await table._all_in(make_interaction())

    balance_now = await balance(cog)
    assert table.stake * 2 <= balance_now
    assert table.stake == balance_now // 2


async def test_la_lateral_sin_saldo_para_las_dos_avisa_y_no_reparte(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(4), c(10))
    table, *_ = await open_table(cog, amount=str(STARTING_BALANCE - 100))
    await table._toggle_side(make_interaction())
    cog.new_shoe = stacked(c(10), c(4))
    interaction = make_interaction()

    await table._deal_again(interaction)

    assert interaction.followup.send.await_args is not None
    assert table.game.result is Result.LOSE  # sigue la anterior: no se ha repartido otra
    assert await balance(cog) == 100


def test_el_titular_cuenta_lo_ganado_y_la_lateral() -> None:
    rng = random.Random(1)
    won = WarGame(stake=100, side=100, shoe=[c(9, 1), c(9)])
    won.deal()
    won.surrender()
    text = result_headline(won, rng)
    assert "Te rindes" in text and "Recuperas" in text
    assert f"Empate {TIE_PAYS}:1" in text
    assert "Total de la mano" in text
    plain = WarGame(stake=100, shoe=[c(4), c(10)])
    plain.deal()
    assert "Total de la mano" not in result_headline(plain, rng)


# -- GIF por adelantado ----------------------------------------------------------------


class RecordingScene:
    """Escena falsa con GIF: apunta cada dibujo (qué, con qué ficha y lateral) y su tapete."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int, str]] = []
        self.gate: asyncio.Event | None = None

    async def _media(self, name: str, game: WarGame, skin: object) -> Media:
        if self.gate is not None:
            await self.gate.wait()
        key = (skin or {}).get("key", "serie")  # type: ignore[attr-defined]
        self.calls.append((name, game.stake, game.side, key))
        return Media(gif=f"GIF{len(self.calls)}".encode(), png=b"png", seconds=0.0)

    async def deal(self, game: WarGame, *, skin: object = None) -> Media:
        return await self._media("deal", game, skin)

    async def war(self, game: WarGame, *, skin: object = None) -> Media:
        return await self._media("war", game, skin)

    async def surrender(self, game: WarGame, *, skin: object = None) -> Media:
        return await self._media("surrender", game, skin)

    async def still(self, game: WarGame, *, skin: object = None) -> bytes:
        return b"png"

    async def close(self) -> None:
        return None


async def ahead_cog(tmp_path: Path, *shoes: list[Card]) -> tuple[War, RecordingScene]:
    """Cog que pinta por adelantado; cada mano baraja el siguiente zapato de `shoes`."""
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    scene = RecordingScene()
    queue = list(shoes)
    cog = War(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=scene,  # type: ignore[arg-type]
        shoe_factory=lambda: list(queue.pop(0) if len(queue) > 1 else queue[0]),
        ahead=True,
    )
    return cog, scene


def shoe(*cards: Card) -> list[Card]:
    return [c(7)] * 20 + list(reversed(cards))


async def settle_painting(table: WarTable) -> None:
    """Espera a que la mesa acabe de pintar por adelantado."""
    while table.ahead.task is not None and not table.ahead.task.done():
        await asyncio.sleep(0)


async def test_al_acabar_la_mano_se_pinta_ya_repartir_con_y_sin_lateral(tmp_path: Path) -> None:
    cog, scene = await ahead_cog(tmp_path, shoe(c(10), c(4)), shoe(c(3), c(8)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    # La mano de ahora (sin plan) y, antes de pulsar nada, la siguiente: sin lateral y con ella.
    assert scene.calls == [
        ("deal", 100, 0, "serie"),
        ("deal", 100, 0, "serie"),
        ("deal", 100, 100, "serie"),
    ]
    # Nada se ha cobrado de la siguiente.
    assert await balance(cog) == STARTING_BALANCE + 100 - gambling_day_tax(100)

    interaction = make_interaction()
    await table._deal_again(interaction)
    # El GIF que sube es el pintado por adelantado (sin lateral) y no se dibuja otro.
    gif = interaction.edit_original_response.await_args_list[0].kwargs["attachments"][0]
    assert gif.fp.read() == b"GIF2"
    assert table.game.player == [c(3)] and table.game.result is Result.LOSE
    # GIF y resultado, sin paso intermedio.
    assert interaction.edit_original_response.await_count == 2


async def test_con_la_lateral_puesta_sube_el_gif_pintado_con_ella(tmp_path: Path) -> None:
    cog, scene = await ahead_cog(tmp_path, shoe(c(10), c(4)), shoe(c(9), c(9, 1)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    await table._toggle_side(make_interaction())
    await settle_painting(table)
    painted = len(scene.calls)

    interaction = make_interaction()
    await table._deal_again(interaction)

    gif = interaction.edit_original_response.await_args_list[0].kwargs["attachments"][0]
    assert gif.fp.read() == b"GIF3"  # el de la lateral, ya pintado
    assert table.game.side == 100 and table.game.awaiting_choice
    # De lo nuevo solo se pinta lo del empate (guerra y rendirse), no otro «repartir».
    await settle_painting(table)
    assert [call[0] for call in scene.calls[painted:]] == ["war", "surrender"]


async def test_el_empate_pinta_la_guerra_y_rendirse_mientras_se_decide(tmp_path: Path) -> None:
    tie = shoe(c(9), c(9, 1), c(2), c(3), c(4), c(12), c(5))
    cog, scene = await ahead_cog(tmp_path, tie, shoe(c(1), c(2)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    assert [call[0] for call in scene.calls] == ["deal", "war", "surrender"]
    assert table.game.awaiting_choice

    interaction = make_interaction()
    await table._war(interaction)

    gif = interaction.edit_original_response.await_args_list[0].kwargs["attachments"][0]
    assert gif.fp.read() == b"GIF2"  # el de la guerra
    assert table.game.result is Result.WAR_WIN
    assert interaction.edit_original_response.await_count == 2


async def test_rendirse_sube_el_gif_pintado_de_rendirse(tmp_path: Path) -> None:
    tie = shoe(c(9), c(9, 1), c(2), c(3), c(4), c(12), c(5))
    cog, scene = await ahead_cog(tmp_path, tie, shoe(c(1), c(2)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    interaction = make_interaction()

    await table._surrender(interaction)

    gif = interaction.edit_original_response.await_args_list[0].kwargs["attachments"][0]
    assert gif.fp.read() == b"GIF3"
    assert table.game.result is Result.SURRENDER


async def test_cambiar_la_ficha_o_el_tapete_vuelve_a_pintar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, scene = await ahead_cog(tmp_path, shoe(c(10), c(4)), shoe(c(3), c(8)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    before = len(scene.calls)

    await table._double_stake(make_interaction())
    await settle_painting(table)
    assert [call[1] for call in scene.calls[before:]] == [200, 200]

    monkeypatch.setattr(table, "skin", lambda: {"key": "tapete_oro"})
    table.prepare()
    await settle_painting(table)
    assert [call[3] for call in scene.calls[before + 2 :]] == ["tapete_oro", "tapete_oro"]


async def test_si_el_dibujo_por_adelantado_no_ha_acabado_se_tapa_la_espera(
    tmp_path: Path,
) -> None:
    cog, scene = await ahead_cog(tmp_path, shoe(c(10), c(4)), shoe(c(3), c(8)))
    table, *_ = await open_table(cog)
    await settle_painting(table)
    scene.gate = asyncio.Event()
    table.ahead.drop()  # sin lo pintado: toca dibujar al pulsar
    interaction = make_interaction()
    task = asyncio.create_task(table._deal_again(interaction))
    while not interaction.edit_original_response.await_count:
        await asyncio.sleep(0)
    # Mientras se pinta, la mesa ya está apagada, sin resultado ni saldo y sin adjuntos nuevos.
    waiting = interaction.edit_original_response.await_args.kwargs
    assert "attachments" not in waiting
    assert [f.name for f in waiting["embed"].fields] == ["Ficha"]
    assert all(b.disabled for b in waiting["view"].children if isinstance(b, discord.ui.Button))
    scene.gate.set()
    await task
    assert interaction.edit_original_response.await_count == 3


async def test_el_gif_no_ensena_el_resultado_ni_el_saldo_hasta_el_final(tmp_path: Path) -> None:
    cog, _ = await ahead_cog(tmp_path, shoe(c(10), c(4)), shoe(c(3), c(8)))
    table, _, _, message = await open_table(cog)
    await settle_painting(table)
    gif_edit, png_edit = message.edit.await_args_list[-2:]
    assert [f.name for f in gif_edit.kwargs["embed"].fields] == ["Ficha"]
    assert "Saldo" in [f.name for f in png_edit.kwargs["embed"].fields]
    assert png_edit.kwargs["attachments"][0].filename == PNG_NAME


async def test_cambiar_la_ficha_no_vuelve_a_subir_la_imagen(tmp_path: Path) -> None:
    cog, _ = await ahead_cog(tmp_path, shoe(c(10), c(4)), shoe(c(3), c(8)))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table._halve(interaction)

    assert "attachments" not in interaction.edit_original_response.await_args.kwargs
