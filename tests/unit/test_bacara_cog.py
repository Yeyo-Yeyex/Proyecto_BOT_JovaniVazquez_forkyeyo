"""Pruebas de bot.cogs.bacara: la mesa compartida, las fichas, el reparto y el dinero.

Se usa la economía real sobre un SQLite temporal, un reloj falso y un dibujo
falso. Cada mano se fija a mano poniendo `state.pending` (la mano que el zapato
ya tiene repartida). El bucle no se arranca solo (`cog.start` es un doble): cada
prueba llama a `deal()` o `run()`. Sin `ahead`, nada se pinta por adelantado.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import bacara as cog_module
from bot.cogs.bacara import Bacara, Phase, Table
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.services.bacara import BANKER_MIN_STAKE, Coup, Side
from bot.services.bacara_render import Media
from bot.services.blackjack import Card
from bot.services.economy import STARTING_BALANCE, EconomyService

GUILD_ID = 1
CHANNEL_ID = 777
ANA, LEO, EVA = 10, 11, 12
START = 1_800_000_000.0

#: Banca 9 (natural) contra Jugador 3.
BANKER_WINS = Coup((Card(1, 0), Card(2, 1)), (Card(4, 2), Card(5, 3)), number=3, left=300)
#: Jugador 8 (natural) contra Banca 0.
PLAYER_WINS = Coup((Card(3, 0), Card(5, 1)), (Card(10, 2), Card(13, 3)), number=3, left=300)
#: Empate a 7.
TIE = Coup((Card(3, 0), Card(4, 1)), (Card(2, 2), Card(5, 3)), number=3, left=300)


class FakeClock:
    """Reloj que solo avanza cuando el bucle "duerme"."""

    def __init__(self) -> None:
        self.now = START

    def wall(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRenderer:
    def __init__(self) -> None:
        self.coups: list[Coup] = []

    async def board(self, *args, **kwargs) -> bytes:  # noqa: ANN002, ANN003
        return b"PNG"

    async def coup(self, coup: Coup, *args, **kwargs) -> Media:  # noqa: ANN002, ANN003
        self.coups.append(coup)
        return Media(gif=b"GIF", png=b"FINAL", seconds=1.0)

    async def close(self) -> None:
        pass


def make_user(user_id: int, name: str = "Ana") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL_ID
    message = MagicMock()
    message.id = 999
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    channel.test_message = message
    return channel


def make_interaction(user_id: int, name: str = "Ana") -> MagicMock:
    interaction = fake_interaction(make_user(user_id, name))
    interaction.guild = None
    return interaction


async def make_cog(tmp_path: Path, *, ahead: bool = False) -> tuple[Bacara, FakeClock]:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    clock = FakeClock()
    cog = Bacara(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        rng=random.Random(7),
        wall_clock=clock.wall,
        sleep=clock.sleep,
        ahead=ahead,
    )
    cog.start = MagicMock()  # type: ignore[method-assign]
    return cog, clock


async def bacara(
    cog: Bacara,
    channel: MagicMock,
    *,
    user_id: int = ANA,
    name: str = "Ana",
    amount: str | None = None,
    side: str | None = None,
) -> AsyncMock:
    errors = AsyncMock()
    await cog._bacara_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(user_id, name),
        amount_text=amount,
        side_text=side,
        confirm=AsyncMock(),
        send_error=errors,
    )
    return errors


def state_balance(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, STATE_ACCOUNT_ID),
        ).fetchone()
    return int(row[0]) if row else 0


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


async def open_table(cog: Bacara, channel: MagicMock) -> Table:
    errors = await bacara(cog, channel)
    errors.assert_not_awaited()
    table = cog.tables[CHANNEL_ID]
    table.message = channel.test_message
    return table


# -- Abrir y apostar ------------------------------------------------------------------------


async def test_bacara_sin_nada_abre_la_mesa(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    assert table.phase is Phase.OPEN
    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].title.startswith("🍸")
    assert kwargs["file"].filename == cog_module.TABLE_PNG
    assert kwargs["allowed_mentions"] == cog_module.NO_MENTIONS
    assert isinstance(kwargs["view"], cog_module.TableView)
    cog.start.assert_called_once_with(table)


async def test_bacara_con_lado_cobra_y_apunta_la_apuesta(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    errors = await bacara(cog, make_channel(), amount="500", side="banca")
    errors.assert_not_awaited()
    seat = cog.tables[CHANNEL_ID].seats[ANA]
    assert seat.bets == {Side.BANKER: 500}
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 500
    # La cantidad queda como ficha para los botones.
    assert cog.ficha(GUILD_ID, ANA) == 500


async def test_los_atajos_de_lado_valen(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    for text, side in (("b", Side.BANKER), ("punto", Side.PLAYER), ("e", Side.TIE)):
        errors = await bacara(cog, channel, amount="100", side=text)
        errors.assert_not_awaited()
        assert side in cog.tables[CHANNEL_ID].seats[ANA].bets


async def test_lado_mal_escrito_no_cobra(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    errors = await bacara(cog, make_channel(), amount="100", side="dragon")
    assert "Lado no válido" in errors.await_args.args[0]
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_la_banca_pide_un_minimo(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    errors = await bacara(cog, make_channel(), amount=str(BANKER_MIN_STAKE - 1), side="banca")
    assert "al menos" in errors.await_args.args[0]
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_sin_saldo_no_apuesta(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    errors = await bacara(cog, make_channel(), amount=str(STARTING_BALANCE + 1), side="jugador")
    errors.assert_awaited_once()
    assert CHANNEL_ID not in cog.tables


async def test_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({1234})
    errors = await bacara(cog, make_channel())
    assert "<#1234>" in errors.await_args.args[0]
    assert cog.tables == {}


async def test_bacara_con_la_mesa_abierta_la_baja_al_final(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await open_table(cog, channel)
    old = channel.test_message
    new = MagicMock(id=1000, edit=AsyncMock(), delete=AsyncMock())
    channel.send = AsyncMock(return_value=new)
    await bacara(cog, channel)
    assert cog.tables[CHANNEL_ID].message is new
    old.delete.assert_awaited_once()


async def test_el_boton_de_un_lado_acepta_antes_de_cobrar(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    table = await open_table(cog, make_channel())
    events: list[str] = []
    interaction = fake_interaction(make_user(LEO, "Leo"), events=events)
    original = cog.economy.place_bet

    async def place_bet(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        events.append("place_bet")
        return await original(*args, **kwargs)

    cog.economy.place_bet = place_bet  # type: ignore[method-assign]
    await table.bet_callback(Side.PLAYER)(interaction)
    assert events[0] == "response.defer"
    assert "place_bet" in events
    assert table.seats[LEO].bets == {Side.PLAYER: cog_module.DEFAULT_STAKE}


async def test_varias_fichas_se_suman_y_el_reloj_empieza_con_la_primera(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    table = await open_table(cog, make_channel())
    assert table.deadline is None
    await table.bet_callback(Side.BANKER)(make_interaction(ANA))
    assert table.deadline == clock.now + cog_module.BET_SECONDS
    assert table.first_bet.is_set()
    await table.bet_callback(Side.BANKER)(make_interaction(ANA))
    await table.bet_callback(Side.TIE)(make_interaction(ANA))
    stake = cog_module.DEFAULT_STAKE
    assert table.seats[ANA].bets == {Side.BANKER: 2 * stake, Side.TIE: stake}


async def test_cartas_es_solo_para_quien_ha_apostado(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    table = await open_table(cog, make_channel())
    nadie = make_interaction(ANA)
    await table.mark_ready(nadie)
    assert "Primero apuesta" in nadie.response.send_message.await_args.args[0]
    assert not table.go.is_set()


async def test_si_todos_estan_listos_se_reparte_ya(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    table = await open_table(cog, make_channel())
    await table.bet_callback(Side.BANKER)(make_interaction(ANA))
    await table.bet_callback(Side.PLAYER)(make_interaction(LEO, "Leo"))
    await table.mark_ready(make_interaction(ANA))
    assert not table.go.is_set()
    await table.mark_ready(make_interaction(LEO, "Leo"))
    assert table.go.is_set() and table.starter == LEO


async def test_doblar_vuelve_a_poner_lo_que_llevas(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    table = await open_table(cog, make_channel())
    await table.bet_callback(Side.PLAYER)(make_interaction(ANA))
    await table.double(make_interaction(ANA))
    assert table.seats[ANA].bets == {Side.PLAYER: 2 * cog_module.DEFAULT_STAKE}


async def test_repetir_sin_mano_anterior_avisa(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    table = await open_table(cog, make_channel())
    interaction = make_interaction(ANA)
    await table.repeat(interaction)
    assert "No tienes mano anterior" in interaction.response.send_message.await_args.args[0]


# -- Reparto y dinero -----------------------------------------------------------------------


async def test_el_reparto_paga_cada_lado_y_el_libro_cuadra(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    await bacara(cog, channel, amount="1000", side="banca")
    await bacara(cog, channel, user_id=LEO, name="Leo", amount="300", side="jugador")
    await bacara(cog, channel, user_id=EVA, name="Eva", amount="100", side="empate")
    table.state.pending = BANKER_WINS

    await table.deal()

    ana, leo, eva = (table.last_round.seats[i] for i in range(3))  # type: ignore[union-attr]
    assert (ana.prize, leo.prize, eva.prize) == (1_950, 0, 0)
    for user_id in (ANA, LEO, EVA):
        assert await cog.economy.balance(GUILD_ID, user_id) == ledger_sum(tmp_path, user_id)
    retained = sum(s.settlement.tax_delta for s in (ana, leo, eva) if s.settlement)
    assert state_balance(tmp_path) == retained
    assert ana.settlement is not None
    expected = STARTING_BALANCE + ana.net - ana.settlement.tax_delta
    assert await cog.economy.balance(GUILD_ID, ANA) == expected


async def test_empate_devuelve_jugador_y_banca_y_paga_ocho_a_uno(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    await bacara(cog, channel, amount="200", side="banca")
    await bacara(cog, channel, amount="100", side="empate")
    table.state.pending = TIE
    await table.deal()
    seat = table.last_round.seats[0]  # type: ignore[union-attr]
    assert seat.prize == 200 + 900
    assert seat.net == 800


async def test_el_reparto_enseña_el_gif_y_vuelve_a_abrir_la_mesa(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    await bacara(cog, channel, amount="100", side="jugador")
    table.state.pending = PLAYER_WINS
    await table.deal()
    edits = [call.kwargs for call in channel.test_message.edit.await_args_list]
    gif = next(
        e for e in edits if "attachments" in e and e["attachments"][0].filename == "mano.gif"
    )
    assert gif["view"] is table.closed_view
    assert all(item.disabled for item in gif["view"].children)
    final = edits[-1]
    assert final["attachments"][0].filename == cog_module.TABLE_PNG
    assert final["view"] is table.view
    assert "Gana el jugador 8 a 0" in final["embed"].description
    assert table.phase is Phase.OPEN and table.seats == {}
    assert table.state.results[-1].winner is Side.PLAYER
    assert table.png == b"FINAL"


async def test_un_clic_tardio_no_cobra_mientras_se_reparte(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    table = await open_table(cog, make_channel())
    table.phase = Phase.DEALING
    interaction = make_interaction(ANA)
    await table.bet_callback(Side.BANKER)(interaction)
    assert "No va más" in interaction.response.send_message.await_args.args[0]
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_repetir_pone_lo_de_la_mano_anterior_y_reparte_si_estas_solo(
    tmp_path: Path,
) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    await bacara(cog, channel, amount="200", side="banca")
    await bacara(cog, channel, amount="50", side="empate")
    table.state.pending = PLAYER_WINS
    await table.deal()
    await table.repeat(make_interaction(ANA))
    assert table.seats[ANA].bets == {Side.BANKER: 200, Side.TIE: 50}
    assert table.go.is_set()


async def test_la_mano_se_pinta_mientras_se_apuesta(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, ahead=True)
    channel = make_channel()
    table = await open_table(cog, channel)
    assert table.prepared is not None
    await bacara(cog, channel, amount="100", side="jugador")
    coup, _media = await table.prepared
    await table.deal()
    # Se jugó la mano que se pintó, y no se volvió a pintar.
    assert table.state.results[-1].winner is coup.winner
    assert cog.renderer.coups[0] is coup  # type: ignore[attr-defined]
    # La siguiente ya se está pintando.
    assert table.prepared is not None
    await table.prepared
    assert len(cog.renderer.coups) == 2  # type: ignore[attr-defined]


async def test_cada_apuesta_dice_a_las_porras_cuando_se_hizo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, clock = await make_cog(tmp_path)
    record = AsyncMock()
    monkeypatch.setattr(cog_module.apuestas, "record", record)
    channel = make_channel()
    table = await open_table(cog, channel)
    await bacara(cog, channel, amount="100", side="banca")
    table.state.pending = BANKER_WINS
    await table.deal()
    details = dict(record.await_args.kwargs["details"])
    assert details["started"] == int(START)
    assert details["natural"] == 1 and details["banca"] == 1
    assert record.await_args.kwargs["game"] == "bacara"


# -- Cierre ---------------------------------------------------------------------------------


async def test_apagar_con_la_mesa_abierta_devuelve_lo_apostado(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await open_table(cog, channel)
    await bacara(cog, channel, amount="400", side="banca")
    await cog.cog_unload()
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    assert cog.tables == {}


async def test_mesa_sin_apuestas_se_cierra_y_el_zapato_sigue(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    state = table.state
    pending = state.take()
    await table.run()
    assert CHANNEL_ID not in cog.tables
    assert clock.now >= START + cog_module.IDLE_SECONDS
    embed = channel.test_message.edit.await_args.kwargs["embed"]
    assert "recoge" in embed.description
    # Al volver a abrir, el mismo zapato y la mano que esperaba.
    channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await bacara(cog, channel)
    assert cog.tables[CHANNEL_ID].state is state
    assert state.take() is pending


async def test_bucle_completo_reparte_y_libera_el_canal(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    await bacara(cog, channel, amount="100", side="jugador")
    table.state.pending = PLAYER_WINS
    await table.run()
    assert table.state.results[-1].winner is Side.PLAYER
    assert CHANNEL_ID not in cog.tables


async def test_si_el_dibujo_aun_no_esta_se_apagan_los_botones_ya(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, ahead=True)
    channel = make_channel()
    gate = asyncio.Event()
    original = cog.renderer.coup

    async def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        await gate.wait()
        return await original(*args, **kwargs)

    cog.renderer.coup = slow  # type: ignore[method-assign]
    table = await open_table(cog, channel)
    await bacara(cog, channel, amount="100", side="jugador")
    dealing = asyncio.create_task(table.deal())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    first = channel.test_message.edit.await_args.kwargs
    assert first["view"] is table.closed_view
    gate.set()
    await dealing
