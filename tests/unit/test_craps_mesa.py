"""Pruebas de bot.cogs.craps_mesa: la mesa compartida de los dados (`dados mesa`).

Se usa la economía real sobre un SQLite temporal, un reloj falso, un dibujo falso y el
azar de guion de `craps_fakes` (cada tirada son dos dados, en orden). El bucle no se
arranca solo (`cog.start` es un doble): cada prueba llama a `roll()` o `run()`. Sin
`ahead`, nada se pinta por adelantado.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from craps_fakes import Scripted
from interaction_fakes import fake_interaction

from bot.cogs import craps as cog_module
from bot.cogs import craps_mesa
from bot.cogs.craps import Craps
from bot.cogs.craps_mesa import MesaView, Phase, remember
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.services.craps import Bet, Status, odds_profit
from bot.services.craps_render import OPENING_REST, Media, Table, board_state, throw_states
from bot.services.economy import STARTING_BALANCE, EconomyService

GUILD_ID = 1
CHANNEL_ID = 777
ANA, LEO, EVA = 10, 11, 12
START = 1_800_000_000.0
PASS, DONT = Bet.PASS, Bet.DONT

#: Tiradas con nombre (dos dados cada una).
NATURAL = (3, 4)  # 7
SNAKE_EYES = (1, 1)  # 2
SIX = (1, 5)  # 6, un punto
SIX_MADE = (2, 4)
ELEVEN = (5, 6)  # con el punto puesto no decide


class FakeClock:
    """Reloj que solo avanza cuando el bucle "duerme"."""

    def __init__(self) -> None:
        self.now = START

    def wall(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRenderer:
    """Dibujo falso: apunta cada mesa que le piden dibujar."""

    def __init__(self) -> None:
        self.drawn: list[Table] = []

    async def board(self, *args, **kwargs) -> bytes:  # noqa: ANN002, ANN003
        return b"PNG"

    async def throw(self, table: Table, **kwargs) -> Media:  # noqa: ANN003
        self.drawn.append(table)
        return Media(gif=b"GIF", png=b"FINAL", seconds=0.0, rest=OPENING_REST)

    async def close(self) -> None:
        pass


def make_user(user_id: int, name: str = "Ana") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


NAMES = {ANA: "Ana", LEO: "Leo", EVA: "Eva"}


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


def make_interaction(user_id: int) -> MagicMock:
    interaction = fake_interaction(make_user(user_id, NAMES[user_id]))
    interaction.guild = None
    return interaction


async def make_cog(
    tmp_path: Path, *rolls: tuple[int, int], ahead: bool = False
) -> tuple[Craps, FakeClock]:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    clock = FakeClock()
    cog = Craps(
        MagicMock(),
        economy=EconomyService(repository),
        rng=Scripted(*rolls),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        ahead=ahead,
        wall_clock=clock.wall,
        sleep=clock.sleep,
    )
    cog.start = MagicMock()  # type: ignore[method-assign]
    return cog, clock


async def mesa(
    cog: Craps,
    channel: MagicMock,
    *,
    user_id: int = ANA,
    amount: str | None = None,
    bet: Bet | None = None,
) -> AsyncMock:
    """`dados mesa` de alguien: abre (o baja) la mesa y, con apuesta, apunta su ficha."""
    errors = AsyncMock()
    await cog._mesa_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(user_id, NAMES[user_id]),
        amount_text=amount,
        bet=bet,
        confirm=AsyncMock(),
        send_error=errors,
    )
    return errors


async def open_table(cog: Craps, channel: MagicMock) -> craps_mesa.CrapsTable:
    errors = await mesa(cog, channel)
    errors.assert_not_awaited()
    table = cog.tables[CHANNEL_ID]
    table.message = channel.test_message
    return table


def ledger_sum(tmp_path: Path, user_id: int) -> int:
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


async def balance(cog: Craps, user_id: int) -> int:
    return await cog.economy.balance(GUILD_ID, user_id)


def ephemeral_text(interaction: MagicMock) -> str:
    return interaction.response.send_message.await_args.args[0]


# -- Abrir ---------------------------------------------------------------------------------


async def test_dados_mesa_abre_la_mesa_del_canal(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    assert table.phase is Phase.OPEN
    kwargs = channel.send.await_args.kwargs
    assert "mesa compartida" in kwargs["embed"].title
    assert kwargs["file"].filename == craps_mesa.PNG_NAME
    assert kwargs["allowed_mentions"] == craps_mesa.NO_MENTIONS
    assert isinstance(kwargs["view"], MesaView)
    cog.start.assert_called_once_with(table)  # type: ignore[attr-defined]


async def test_una_sola_mesa_por_canal_y_la_segunda_orden_la_baja(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    old = channel.test_message
    new_message = MagicMock(edit=AsyncMock(), delete=AsyncMock())
    channel.send = AsyncMock(return_value=new_message)
    (await mesa(cog, channel, user_id=LEO)).assert_not_awaited()
    assert cog.tables == {CHANNEL_ID: table}
    channel.send.assert_awaited_once()
    old.delete.assert_awaited_once()
    assert table.message is new_message


async def test_la_mesa_lleva_el_tapete_de_quien_la_abre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    seen: list[int] = []

    def params(bot: object, guild_id: int, user_id: int, game: str) -> dict[str, object]:
        seen.append(user_id)
        return {"key": "verde"}

    monkeypatch.setattr(craps_mesa.tienda, "skin_params", params)
    monkeypatch.setattr(cog_module.tienda, "skin_params", params)
    channel = make_channel()
    table = await open_table(cog, channel)
    assert seen == [ANA] and table.skin == {"key": "verde"}


async def test_botones_con_custom_id_fijo(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    table = await open_table(cog, make_channel())
    for view in table.views():
        ids = [b.custom_id for b in view.children if isinstance(b, discord.ui.Button)]
        assert all(i and i.startswith("dados:mesa:") for i in ids)
    open_ids = {b.custom_id for b in table.open_view.children if isinstance(b, discord.ui.Button)}
    assert {"dados:mesa:pase", "dados:mesa:nopase", "dados:mesa:tirar"} <= open_ids
    point_ids = {b.custom_id for b in table.point_view.children if isinstance(b, discord.ui.Button)}
    assert "dados:mesa:odds" in point_ids and "dados:mesa:pase" not in point_ids
    # Cada botón rehecho encuentra a su sustituto por el mismo custom_id.
    assert open_ids & point_ids >= {"dados:mesa:tirar", "dados:mesa:listo"}
    assert all(
        b.disabled
        for b in table.closed_view.children
        if isinstance(b, discord.ui.Button) and (b.custom_id or "").startswith("dados:mesa:")
    )


# -- Una ronda con dos jugadores ----------------------------------------------------------------


async def test_natural_pase_gana_no_pase_pierde_y_el_libro_cuadra(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, NATURAL)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="300", bet=DONT)
    # Se cobra al tirar la salida, no antes.
    assert await balance(cog, ANA) == STARTING_BALANCE
    assert table.is_shooter(ANA)
    seats = list(table.seats.values())
    await table.roll()
    ana, leo = seats
    assert ana.net == 100 and leo.net == -300
    assert table.line is not None and table.line.status is Status.WON
    for user_id in (ANA, LEO):
        assert await balance(cog, user_id) == ledger_sum(tmp_path, user_id)
    assert ana.settlement is not None
    assert await balance(cog, ANA) == STARTING_BALANCE + ana.net - ana.settlement.tax_delta
    assert await balance(cog, LEO) == STARTING_BALANCE - 300
    retained = sum(s.settlement.tax_delta for s in seats if s.settlement)
    assert state_balance(tmp_path) == retained
    # Vuelve la salida, con el resultado de cada uno a la vista.
    assert table.phase is Phase.OPEN and table.seats == {}
    embed = channel.test_message.edit.await_args.kwargs["embed"]
    assert "Ana" in embed.description and "+100" in embed.description
    assert "Leo" in embed.description and "-300" in embed.description


async def test_una_tirada_para_todos_dos_dados_una_vez(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, SNAKE_EYES)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="100", bet=DONT)
    ana, leo = list(table.seats.values())
    await table.roll()
    # Un 2 en la salida: pifia para Pase y gana No pase; los dos vieron los mismos dados.
    assert ana.game is not None and leo.game is not None
    assert ana.game.rolls[0].dice == leo.game.rolls[0].dice == SNAKE_EYES
    assert ana.game.status is Status.LOST and leo.game.status is Status.WON
    assert (ana.net, leo.net) == (-100, 100)


async def test_con_el_punto_puesto_las_odds_y_los_demas_esperan(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, SIX, SIX_MADE)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="100", bet=DONT)
    ana, leo = list(table.seats.values())
    await table.roll()
    assert table.phase is Phase.POINT and table.line is not None and table.line.point == 6
    assert table.view_now() is table.point_view
    # Quien llega con el punto puesto no puede apostar ni poner Odds: espera a la salida.
    late = make_interaction(EVA)
    await table.bet_callback(PASS)(late)
    assert "punto" in ephemeral_text(late)
    late = make_interaction(EVA)
    await table.odds(late)
    assert "siguiente salida" in ephemeral_text(late)
    # Ana pone Odds (otra apuesta igual) y se cobran al momento.
    click = make_interaction(ANA)
    await table.odds(click)
    assert ana.game is not None and ana.game.odds == 100
    assert await balance(cog, ANA) == STARTING_BALANCE - 200
    await table.roll()
    # El 6 otra vez: Pase con Odds cobra las Odds a 6:5; No pase pierde su apuesta.
    profit = odds_profit(PASS, 6, 100)
    assert ana.net == 100 + profit and leo.net == -100
    assert await balance(cog, ANA) == ledger_sum(tmp_path, ANA)
    assert await balance(cog, LEO) == ledger_sum(tmp_path, LEO)
    assert table.phase is Phase.OPEN


async def test_las_odds_llegan_como_mucho_a_tres_veces_la_apuesta(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, SIX)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await table.roll()
    for _ in range(3):
        await table.odds(make_interaction(ANA))
    top = make_interaction(ANA)
    await table.odds(top)
    seat = table.seats[ANA]
    assert seat.game is not None and seat.game.odds == craps_mesa.ODDS_MAX * 100
    top.followup.send.assert_awaited()
    assert "tope" in top.followup.send.await_args.args[0]


# -- El tirador ----------------------------------------------------------------------------


async def test_solo_el_tirador_tira_y_el_primero_en_apostar_lo_es(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    nobody = make_interaction(ANA)
    await table.shoot(nobody)
    assert "apuestas" in ephemeral_text(nobody)
    await mesa(cog, channel, user_id=LEO, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=DONT)
    assert table.is_shooter(LEO)
    intruder = make_interaction(ANA)
    await table.shoot(intruder)
    assert "Leo" in ephemeral_text(intruder)
    assert not table.go.is_set()
    await table.shoot(make_interaction(LEO))
    assert table.go.is_set()


async def test_el_cubilete_pasa_al_siguiente_con_el_siete_fuera(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, SIX, NATURAL, ELEVEN)
    channel = make_channel()
    table = await open_table(cog, channel)
    for user_id in (ANA, LEO, EVA):
        await mesa(cog, channel, user_id=user_id, amount="100", bet=PASS)
    await table.roll()  # el 6 pone el punto
    await table.roll()  # siete fuera: se acaba la mano de Ana
    assert table.hand.seven_out
    assert table.is_shooter(LEO)
    assert "Leo" in " ".join(table.report)
    # Leo apuesta solo; el siguiente siete fuera lo pasa a quien apueste después de él.
    await mesa(cog, channel, user_id=EVA, amount="100", bet=DONT)
    await mesa(cog, channel, user_id=LEO, amount="100", bet=PASS)
    await table.roll()  # un 11 de salida: natural
    assert table.is_shooter(LEO) and table.hand.rolls == 1  # mano nueva


async def test_con_un_solo_jugador_el_cubilete_se_queda(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, SIX, NATURAL)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await table.roll()
    await table.roll()
    assert table.is_shooter(ANA)


# -- Cuenta atrás y bucle --------------------------------------------------------------------


async def test_la_cuenta_atras_tira_sola_y_la_mesa_se_cierra_sin_apuestas(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path, NATURAL)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    assert table.deadline == START + craps_mesa.BET_SECONDS
    await table.run()
    # Nadie pulsó 🎲 Tirar: tiró el bot al acabar la cuenta atrás y pagó.
    assert await balance(cog, ANA) > STARTING_BALANCE - 100
    assert table.line is not None and table.line.status is Status.WON
    assert CHANNEL_ID not in cog.tables
    assert clock.now >= START + craps_mesa.BET_SECONDS
    embed = channel.test_message.edit.await_args.kwargs["embed"]
    assert "cerrada" in embed.title


async def test_si_todos_estan_listos_se_tira_ya(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="100", bet=DONT)
    stranger = make_interaction(EVA)
    await table.mark_ready(stranger)
    assert "apuesta" in ephemeral_text(stranger)
    await table.mark_ready(make_interaction(ANA))
    assert not table.go.is_set()
    await table.mark_ready(make_interaction(LEO))
    assert table.go.is_set()


async def test_repetir_pone_lo_de_la_ronda_anterior_y_marca_listo(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, NATURAL)
    channel = make_channel()
    table = await open_table(cog, channel)
    nothing = make_interaction(ANA)
    await table.repeat(nothing)
    assert "anterior" in ephemeral_text(nothing)
    await mesa(cog, channel, user_id=ANA, amount="250", bet=DONT)
    await table.roll()
    await table.repeat(make_interaction(ANA))
    seat = table.seats[ANA]
    assert (seat.bet, seat.stake, seat.ready) == (DONT, 250, True)
    assert table.go.is_set()


async def test_quien_no_llega_a_su_ficha_se_queda_fuera_de_la_ronda(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, NATURAL)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="500", bet=DONT)
    # Entre la apuesta y la salida se gasta el dinero.
    await cog.economy.place_bet(GUILD_ID, LEO, game="otro", stake=STARTING_BALANCE - 10)
    await table.roll()
    assert (
        "Leo" in " ".join(table.report)
        or "Leo" in channel.test_message.edit.await_args.kwargs["embed"].description
    )
    assert await balance(cog, LEO) == 10
    assert await balance(cog, ANA) == ledger_sum(tmp_path, ANA) > STARTING_BALANCE


async def test_sin_apuestas_la_mesa_se_cierra(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    await table.run()
    assert CHANNEL_ID not in cog.tables and table.phase is Phase.CLOSED
    assert clock.now >= START + craps_mesa.IDLE_SECONDS


# -- Cierre con el punto puesto -----------------------------------------------------------------


async def test_al_cerrar_con_el_punto_puesto_el_bot_tira_y_paga(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, SIX, ELEVEN, NATURAL)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="100", bet=DONT)
    await table.roll()
    await table.odds(make_interaction(LEO))  # Leo, con Odds
    assert await balance(cog, LEO) == STARTING_BALANCE - 200
    await cog.cog_unload()
    # Siete fuera: pierde Pase, gana No pase con sus Odds (a 5:6) y no queda nada colgado.
    assert await balance(cog, ANA) == STARTING_BALANCE - 100
    assert await balance(cog, LEO) > STARTING_BALANCE
    for user_id in (ANA, LEO):
        assert await balance(cog, user_id) == ledger_sum(tmp_path, user_id)
    assert table.phase is Phase.CLOSED and cog.tables == {}
    assert channel.test_message.edit.await_args.kwargs["view"] is None


async def test_cerrar_la_mesa_en_la_salida_no_deja_nada_cobrado(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="400", bet=PASS)
    await cog.cog_unload()
    assert await balance(cog, ANA) == STARTING_BALANCE


# -- Pintar antes del clic ----------------------------------------------------------------------


async def test_los_dados_se_pintan_al_abrir_la_ronda_y_no_se_vuelven_a_pintar(
    tmp_path: Path,
) -> None:
    cog, _clock = await make_cog(tmp_path, NATURAL, SIX, ELEVEN, ELEVEN, ELEVEN, ELEVEN, ahead=True)
    channel = make_channel()
    table = await open_table(cog, channel)
    renderer: FakeRenderer = cog.renderer  # type: ignore[assignment]
    # Antes de que nadie apueste, el GIF de la primera tirada ya se está pintando.
    assert table._painting is not None
    await table._painting
    assert len(renderer.drawn) == 1
    assert renderer.drawn[0].game is not None
    assert renderer.drawn[0].game.rolls[0].dice == NATURAL
    # Apostar no repinta nada: el dibujo no depende de las apuestas.
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="700", bet=DONT)
    await asyncio.sleep(0)
    assert len(renderer.drawn) == 1
    drawn_while_gif: list[int] = []
    real_sleep = cog.sleep

    async def watching(seconds: float) -> None:
        # La espera del GIF: la tarea del dibujo siguiente ya ha podido arrancar.
        for _ in range(3):
            await asyncio.sleep(0)
        drawn_while_gif.append(len(renderer.drawn))
        await real_sleep(seconds)

    cog.sleep = watching  # type: ignore[assignment]
    await table.roll()
    # Se jugó la tirada pintada y la siguiente ya se pintaba mientras se veía el GIF.
    assert table.history[0][0].dice == NATURAL
    assert drawn_while_gif == [2]
    assert table._painting is not None
    await table._painting
    assert len(renderer.drawn) == 2


async def test_si_el_dibujo_no_esta_los_botones_se_apagan_ya(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path, NATURAL, ELEVEN, ahead=True)
    channel = make_channel()
    gate = asyncio.Event()
    original = cog.renderer.throw

    async def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        await gate.wait()
        return await original(*args, **kwargs)

    cog.renderer.throw = slow  # type: ignore[method-assign]
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    rolling = asyncio.create_task(table.roll())
    views: list[object] = []
    for _ in range(200):  # cobrar y pagar van a la base de datos: hace falta tiempo real
        await asyncio.sleep(0.01)
        views = [call.kwargs.get("view") for call in channel.test_message.edit.await_args_list]
        if table.closed_view in views:
            break
    assert table.closed_view in views
    gate.set()
    await rolling


# -- Dibujo sin importes ------------------------------------------------------------------------


def crowd_table(*rolls: tuple[int, int]) -> Table:
    from bot.services.craps import Hand, table_line

    line, hand, history = table_line(), Hand(), []
    for dice in rolls:
        roll = line.roll(dice)
        hand.observe(roll)
        history.append((roll, PASS))
    return Table(line, hand, 1, PASS, tuple(history), crowd=craps_mesa.CROWD)


def test_el_dibujo_de_la_mesa_compartida_no_lleva_fichas_ni_importes() -> None:
    table = crowd_table(NATURAL)
    state = board_state(table, OPENING_REST)
    assert state["chips"] == []
    assert state["panel"]["bet"] == "MESA" and state["panel"]["odds"] is None
    assert "Y$" not in state["banner"]["sub"]
    states, _rest = throw_states(table, seed=3)
    assert all(frame["chips"] == [] for frame in states)


def test_el_dibujo_con_el_punto_puesto_enseña_lo_que_pagan_las_odds() -> None:
    state = board_state(crowd_table(SIX), OPENING_REST)
    assert state["panel"]["point"] == 6 and state["panel"]["pays"] == "6:5"


# -- Porras, logros y estadísticas -----------------------------------------------------------


async def test_cada_jugada_va_a_logros_y_a_las_porras_con_el_mismo_juego(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path, SNAKE_EYES)
    play = AsyncMock()
    record = AsyncMock()
    monkeypatch.setattr(craps_mesa.logros, "casino_play", play)
    monkeypatch.setattr(craps_mesa.apuestas, "record", record)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="100", bet=DONT)
    await mesa(cog, channel, user_id=EVA, amount="100", bet=DONT)
    await table.roll()
    assert play.await_count == 3 and record.await_count == 3
    deltas = {call.args[2].id: call.args[4] for call in play.await_args_list}
    assert deltas[ANA].add["dice_shooter"] == 1
    assert "dice_shooter" not in deltas[LEO].add
    # Leo y Eva apostaron contra el tirador y ganaron con su tirada.
    for user_id in (LEO, EVA):
        assert deltas[user_id].add["dice_table_vs_shooter"] == 1
        assert deltas[user_id].add["dice_table_shooter_wins"] == 1
    assert deltas[ANA].peak["dice_table_players_max"] == 3
    assert {call.kwargs["game"] for call in record.await_args_list} == {"dados"}
    details = dict(record.await_args_list[0].kwargs["details"])
    assert details["started"] == int(START) and details["rolls"] == 1


async def test_la_mano_caliente_la_cuenta_el_tirador(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path, SIX, SIX_MADE)
    play = AsyncMock()
    monkeypatch.setattr(craps_mesa.logros, "casino_play", play)
    channel = make_channel()
    table = await open_table(cog, channel)
    await mesa(cog, channel, user_id=ANA, amount="100", bet=PASS)
    await mesa(cog, channel, user_id=LEO, amount="100", bet=PASS)
    await table.roll()
    await table.roll()
    deltas = {call.args[2].id: call.args[4] for call in play.await_args_list}
    assert deltas[ANA].peak["dice_hand_points_max"] == 1
    assert deltas[LEO].peak["dice_hand_points_max"] == 0
    assert deltas[ANA].peak["dice_public_points_max"] == 1
    assert "dice_public_points_max" not in deltas[LEO].peak


# -- Comandos ----------------------------------------------------------------------------------


async def test_slash_dados_con_mesa_compartida_abre_la_mesa_y_apuesta(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    interaction = make_interaction(ANA)
    interaction.guild = MagicMock(id=GUILD_ID)
    interaction.channel = channel
    await cog.dados.callback(cog, interaction, "300", "pase", "compartida")  # type: ignore[call-arg]
    table = cog.tables[CHANNEL_ID]
    assert table.seats[ANA].bet is PASS and table.seats[ANA].stake == 300
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited()
    # Sin la opción, sigue siendo la mesa individual de siempre.
    assert cog.views == set()


async def test_texto_dados_mesa_abre_la_mesa_compartida(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    ctx = MagicMock()
    ctx.guild = MagicMock(id=GUILD_ID)
    ctx.channel = channel
    ctx.author = make_user(ANA)
    ctx.message.add_reaction = AsyncMock()
    ctx.send = AsyncMock()
    await cog.dados_text.callback(cog, ctx, "mesa", "no", "pase", "200")  # type: ignore[call-arg]
    table = cog.tables[CHANNEL_ID]
    assert table.seats[ANA].bet is DONT and table.seats[ANA].stake == 200
    ctx.message.add_reaction.assert_awaited_once()


async def test_dados_mesa_respeta_el_canal_del_casino(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({1})
    errors = await mesa(cog, make_channel())
    errors.assert_awaited_once()
    assert cog.tables == {}


# -- Memoria acotada -----------------------------------------------------------------------------


def test_lo_que_se_recuerda_tiene_tope() -> None:
    store: dict[int, int] = {}
    for i in range(craps_mesa.MAX_REMEMBERED + 50):
        remember(store, i, i)
    assert len(store) == craps_mesa.MAX_REMEMBERED
    assert 0 not in store and craps_mesa.MAX_REMEMBERED + 49 in store
