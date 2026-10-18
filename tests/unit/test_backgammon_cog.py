"""Pruebas de bot.cogs.backgammon: la mesa contra Jovani, los duelos con cubo y grada y los retos.

Se usa la economía real sobre un SQLite temporal, un dibujante falso (las
imágenes tienen sus propias pruebas), esperas a cero salvo las largas (el reto,
el turno), un reloj fijo y un azar con semilla. Lo que el azar decidiría (la
salida, los colores, los dados que vienen) se fija a mano en cada prueba. Los
retos del día se sustituyen por unos que no se cumplen en una partida: así el
dinero que se mueve en cada prueba es solo el que esa prueba espera.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import backgammon as backgammon_cog
from bot.cogs.backgammon import Backgammon, Duel, Hand, Phase, SoloTable, Stage
from bot.repositories import sqlite as sqlite_module
from bot.repositories.backgammon import BackgammonRepository
from bot.repositories.economy import EconomyRepository
from bot.services.backgammon import (
    CHALLENGE_BY_KEY,
    GRADA_TURNS,
    LEVEL_BY_KEY,
    POINTS,
    Ending,
    GameEvent,
    Position,
    Side,
    Turn,
    at_risk,
    double_extra,
    escrow,
    jovani_play,
    plan_seed,
)
from bot.services.backgammon_render import Cube, Media
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.gallos import duel_prize
from bot.services.levels import TIMEZONE
from bot.services.taxes import gambling_day_tax

GUILD_ID = 1
OWNER_ID = 10
RIVAL_ID = 11
FAN_IDS = (20, 21, 22)
NOON = datetime(2026, 10, 14, 12, 0, tzinfo=TIMEZONE).timestamp()
STAKE = 100
#: Las blancas sacan más en la salida (6 contra 1) y empiezan; al revés, las negras.
WHITE_STARTS, BLACK_STARTS = (6, 1), (1, 6)

#: Retos que no se cumplen en una partida de las que juegan estas pruebas.
SLOW_CHALLENGES = tuple(CHALLENGE_BY_KEY[key] for key in ("jugar_3", "dificil_1", "imposible_1"))


@pytest.fixture(autouse=True)
def calm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin margen tras el GIF y sin retos que se cumplan por sorpresa."""
    monkeypatch.setattr(backgammon_cog, "REVEAL_MARGIN_SECONDS", 0)
    monkeypatch.setattr(backgammon_cog, "daily_challenges", lambda *_args: SLOW_CHALLENGES)


class Clock:
    """Reloj de pared que solo avanza cuando la prueba lo dice."""

    def __init__(self) -> None:
        self.now = NOON

    def __call__(self) -> float:
        return self.now


def make_user(user_id: int) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = f"Persona {user_id}"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def fake_renderer() -> MagicMock:
    renderer = MagicMock()
    renderer.still = AsyncMock(return_value=b"png")
    renderer.animate = AsyncMock(return_value=Media(gif=b"GIF89a", png=b"png", seconds=0.0))
    renderer.close = AsyncMock()
    return renderer


async def make_cog(tmp_path: Path, *, ahead: bool = False) -> Backgammon:
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    repository = BackgammonRepository(tmp_path / "bot.db")
    await repository.initialize()

    async def no_sleep(seconds: float) -> None:
        # Las esperas del GIF no se esperan; las del reto y el turno, nunca acaban.
        if seconds >= 30:
            await asyncio.Event().wait()

    return Backgammon(
        MagicMock(),
        economy=EconomyService(economy_repository),
        repository=repository,
        rng=random.Random(7),
        renderer=fake_renderer(),
        sleep=no_sleep,
        wall_clock=Clock(),
        ahead=ahead,
    )


def ledger(tmp_path: Path, user_id: int) -> list[tuple[str, int]]:
    """Movimientos de un miembro (motivo, importe), sin el saldo de bienvenida."""
    connection = sqlite_module.connect(tmp_path / "bot.db")
    try:
        rows = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id",
            (GUILD_ID, user_id),
        ).fetchall()
    finally:
        connection.close()
    return [(row[0], row[1]) for row in rows if row[0] != "bienvenida"]


def ledger_is_empty(tmp_path: Path) -> bool:
    return all(not ledger(tmp_path, uid) for uid in (OWNER_ID, RIVAL_ID, *FAN_IDS))


async def net(cog: Backgammon, user_id: int) -> int:
    """Lo que ha ganado (o perdido) un miembro respecto a su saldo de bienvenida."""
    return await cog.economy.balance(GUILD_ID, user_id) - STARTING_BALANCE


def ids(view: Any) -> set[str]:
    return {item.custom_id for item in view.children}


def item(view: Any, custom_id: str) -> Any:
    return next(i for i in view.children if i.custom_id == f"backgammon:{custom_id}")


def position(white: dict[int, int], black: dict[int, int], *, off: tuple[int, int]) -> Position:
    """Una posición a mano: casilla → fichas de cada color (las sacadas, en `off`)."""
    points = [0] * POINTS
    for index, n in white.items():
        points[index] = n
    for index, n in black.items():
        points[index] = -n
    return Position(tuple(points), (0, 0), off)


#: A las blancas les queda una ficha en su punto 1; las negras ya han sacado una.
WHITE_ALMOST = position({0: 1}, {20: 14}, off=(14, 1))
#: Igual, pero las negras no han sacado ninguna: si ganan las blancas, gammon.
WHITE_ALMOST_GAMMON = position({0: 1}, {20: 15}, off=(14, 0))
#: A las negras les queda una ficha en su punto 1; las blancas, todas en el medio.
BLACK_ALMOST = position({12: 15}, {23: 1}, off=(0, 14))


# -- Contra Jovani -------------------------------------------------------------------------


async def open_panel(cog: Backgammon) -> SoloTable:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    duel = await cog._backgammon_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(OWNER_ID),
        amount_text=None,
        rival=None,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    assert duel is None
    assert send.await_args.kwargs["file"].filename == "backgammon.png"
    (panel,) = cog.panels
    return panel


async def press(view: Any, custom_id: str, user: MagicMock) -> MagicMock:
    interaction = fake_interaction(user)
    await item(view, custom_id).callback(interaction)
    return interaction


async def choose(view: Any, die: int, value: str, user: MagicMock) -> MagicMock:
    """Elige una ficha en el desplegable de un dado."""
    interaction = fake_interaction(user)
    interaction.data = {"values": [value]}
    await item(view, f"die{die}").callback(interaction)
    return interaction


async def play_first_steps(view: Any, hand_of: Any, user: MagicMock) -> None:
    """Mueve, ficha a ficha, el primer paso legal de cada momento hasta acabar la jugada."""
    while (hand := hand_of()) is not None and not hand.done:
        step = hand.legal()[0]
        await choose(view, step.die, f"{step.source}:{step.target}", user)


async def started(cog: Backgammon, opening: tuple[int, int] = WHITE_STARTS) -> SoloTable:
    panel = await open_panel(cog)
    panel.opening = opening
    await press(panel, "start", panel.owner)
    assert panel.phase == "play"
    return panel


def at_end(panel: SoloTable, start: Position, roll: tuple[int, int] = (1, 2)) -> None:
    """Pone la mesa en una posición preparada, con las blancas a punto de tirar `roll`."""
    panel.position = panel.origin = start
    panel.log = []
    panel.hand = Hand(start, Side.WHITE, roll)
    panel.rebuild()


async def test_abrir_la_mesa_enseña_la_salida_con_empezar_y_el_nivel(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    cog.renderer.still.assert_awaited_once()
    assert panel.phase == "start" and panel.hand is None
    assert {"backgammon:start", "backgammon:level", "backgammon:retos"} <= ids(panel)
    assert ledger_is_empty(tmp_path)


async def test_si_sales_tu_te_tocan_tus_dados_de_la_salida(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog, WHITE_STARTS)
    assert panel.log == [] and panel.hand is not None
    assert panel.hand.side is Side.WHITE and panel.hand.roll == (6, 1)
    assert {"backgammon:die6", "backgammon:die1", "backgammon:confirm"} <= ids(panel)
    assert item(panel, "confirm").disabled  # aún no has movido
    cog.renderer.animate.assert_awaited_once()


async def test_si_sale_jovani_mueve_con_la_salida_y_luego_tiras_tu(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    panel.opening = BLACK_STARTS
    mine = panel.upcoming[1]
    expected = jovani_play(
        Position.initial(), Side.BLACK, (6, 1), panel.level, plan_seed(panel.seed, 1)
    )
    await press(panel, "start", panel.owner)
    assert panel.log == [Turn(Side.BLACK, (6, 1), expected)]
    assert panel.hand is not None and panel.hand.roll == mine
    assert panel.note is not None and "Jovani" in panel.note


async def test_mover_ficha_a_ficha_y_deshacer(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    hand = panel.hand
    assert hand is not None
    step = next(s for s in hand.legal() if s.die == 6)
    await choose(panel, 6, f"{step.source}:{step.target}", panel.owner)
    assert hand.steps == [step] and hand.dice == (1,)
    assert not item(panel, "undo").disabled
    await press(panel, "undo", panel.owner)
    assert hand.steps == [] and hand.position == Position.initial()


async def test_una_ficha_que_ya_no_se_puede_mover_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    interaction = await choose(panel, 6, "3:9", panel.owner)
    interaction.response.send_message.assert_awaited_once()
    assert panel.hand is not None and panel.hand.steps == []


async def test_confirmar_juega_jovani_con_su_semilla_y_te_da_tus_dados(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    await play_first_steps(panel, lambda: panel.hand, panel.owner)
    hand = panel.hand
    assert hand is not None and hand.done
    roll_j, roll_h, seed = panel.upcoming
    expected = jovani_play(hand.position, Side.BLACK, roll_j, panel.level, plan_seed(seed, 0))
    await press(panel, "confirm", panel.owner)
    assert len(panel.log) == 2 and panel.log[1] == Turn(Side.BLACK, roll_j, expected)
    assert panel.hand is not None and panel.hand.roll == roll_h and panel.hand.steps == []
    assert ledger_is_empty(tmp_path)


async def test_solo_el_dueno_toca_su_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    stranger = fake_interaction(make_user(RIVAL_ID))
    assert not await panel.interaction_check(stranger)
    stranger.response.send_message.assert_awaited_once()


async def test_sacar_la_ultima_ficha_gana_y_apunta_la_partida(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    at_end(panel, WHITE_ALMOST)
    await play_first_steps(panel, lambda: panel.hand, panel.owner)
    await press(panel, "confirm", panel.owner)
    assert panel.phase == "over" and panel.result is not None
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert record.wins == 1 and record.gammons == 0 and record.best_level == panel.level.order
    assert "backgammon:start" in ids(panel)  # 🔁 Revancha


async def test_ganar_sin_que_jovani_saque_ninguna_es_gammon(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    at_end(panel, WHITE_ALMOST_GAMMON)
    await play_first_steps(panel, lambda: panel.hand, panel.owner)
    await press(panel, "confirm", panel.owner)
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert record.wins == 1 and record.gammons == 1
    assert panel.result is not None and "gammon" in panel.result


async def test_si_jovani_saca_la_ultima_pierdes(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    at_end(panel, BLACK_ALMOST)
    panel.upcoming = ((6, 5), (2, 1), 3)
    await play_first_steps(panel, lambda: panel.hand, panel.owner)
    await press(panel, "confirm", panel.owner)
    assert panel.phase == "over"
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert record.losses == 1 and record.wins == 0 and record.streak == 0


async def test_rendirse_cuenta_derrota(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    await press(panel, "resign", panel.owner)
    assert panel.phase == "over"
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).losses == 1


async def test_la_revancha_empieza_otra_partida(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    await press(panel, "resign", panel.owner)
    panel.opening = WHITE_STARTS
    await press(panel, "start", panel.owner)
    assert panel.phase == "play" and panel.games == 1 and panel.log == []
    assert panel.result is None


async def test_el_nivel_se_cambia_antes_de_empezar_y_no_a_mitad(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    interaction = fake_interaction(panel.owner)
    interaction.data = {"values": ["imposible"]}
    await item(panel, "level").callback(interaction)
    assert panel.level is LEVEL_BY_KEY["imposible"]
    panel.opening = WHITE_STARTS
    await press(panel, "start", panel.owner)
    assert "backgammon:level" not in ids(panel)


async def test_si_la_mesa_caduca_a_medias_cuenta_como_rendirse(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await started(cog)
    await panel.on_timeout()
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).losses == 1
    assert not cog.panels


async def test_si_la_mesa_caduca_sin_jugar_no_se_apunta_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    await panel.on_timeout()
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).games == 0


async def test_contra_jovani_no_se_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._backgammon_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(OWNER_ID),
        amount_text="500",
        rival=None,
        send=AsyncMock(),
        send_error=errors,
    )
    assert "no se apuesta" in errors.await_args.args[0]
    assert not cog.panels and ledger_is_empty(tmp_path)


async def test_con_pintado_por_adelantado_el_clic_usa_el_dibujo_ya_hecho(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, ahead=True)
    panel = await open_panel(cog)
    panel.opening = WHITE_STARTS
    panel.prepare()
    assert panel.ahead.task is not None
    await panel.ahead.task
    painted = cog.renderer.animate.await_count
    await press(panel, "start", panel.owner)
    # La salida ya estaba pintada: al pulsar no se vuelve a pintar.
    assert cog.renderer.animate.await_count == painted
    panel.ahead.drop()


async def test_los_retos_se_ven_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    interaction = await press(panel, "retos", panel.owner)
    interaction.response.defer.assert_awaited_once()
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "Retos de hoy" in embed.description


# -- Duelos ---------------------------------------------------------------------------------


async def challenge(cog: Backgammon, amount: str | None = str(STAKE)) -> Duel:
    errors = AsyncMock()
    duel = await cog._backgammon_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(OWNER_ID),
        amount_text=amount,
        rival=make_user(RIVAL_ID),
        send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
        send_error=errors,
    )
    errors.assert_not_awaited()
    assert duel is not None and duel.phase is Phase.CHALLENGE
    duel.swap = False
    duel.opening = WHITE_STARTS
    return duel


async def start(cog: Backgammon, amount: str | None = str(STAKE)) -> Duel:
    duel = await challenge(cog, amount)
    await duel.accept(fake_interaction(duel.rival))
    assert duel.phase is Phase.PLAYING
    return duel


def finish_timers(duel: Duel) -> None:
    if duel.timer is not None:
        duel.timer.cancel()


async def play_turn(duel: Duel) -> None:
    """Quien toca mueve el primer paso legal de cada momento y confirma."""
    user = duel.member(duel.turn)
    await play_first_steps(duel.view, lambda: duel.hand, user)
    await press(duel.view, "confirm", user)


async def to_decide(duel: Duel) -> None:
    """Juega el turno de las blancas: a las negras les toca decidir si doblan."""
    await play_turn(duel)
    assert duel.stage is Stage.DECIDE and duel.turn is Side.BLACK


async def test_retar_no_retiene_nada_hasta_aceptar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    assert ledger_is_empty(tmp_path)
    finish_timers(duel)


async def test_aceptar_retiene_la_apuesta_por_tres_a_los_dos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    for user_id in (OWNER_ID, RIVAL_ID):
        assert ledger(tmp_path, user_id) == [("backgammon:apuesta", -escrow(STAKE, 1))]
    assert duel.players == {Side.WHITE: duel.challenger, Side.BLACK: duel.rival}
    assert duel.hand is not None and duel.hand.side is Side.WHITE and duel.stage is Stage.MOVE
    finish_timers(duel)


async def test_los_colores_salen_del_sorteo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    duel.swap = True
    await duel.accept(fake_interaction(duel.rival))
    assert duel.players[Side.WHITE] is duel.rival
    finish_timers(duel)


async def test_si_al_retado_no_le_llega_para_el_triple_no_se_cobra_a_nadie(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog, str(STARTING_BALANCE // 3))
    await cog.economy.place_bet(GUILD_ID, RIVAL_ID, game="otro", stake=STARTING_BALANCE // 2)
    interaction = fake_interaction(duel.rival)
    await duel.accept(interaction)
    assert duel.phase is Phase.CHALLENGE
    assert ledger(tmp_path, OWNER_ID) == []
    finish_timers(duel)


async def test_retar_por_mas_de_un_tercio_del_saldo_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    duel = await cog._backgammon_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(OWNER_ID),
        amount_text=str(STARTING_BALANCE // 2),
        rival=make_user(RIVAL_ID),
        send=AsyncMock(),
        send_error=errors,
    )
    assert duel is None and "×3" in errors.await_args.args[0]


async def test_solo_mueve_a_quien_le_toca(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    assert duel.hand is not None
    step = duel.hand.legal()[0]
    interaction = await choose(duel.view, step.die, f"{step.source}:{step.target}", duel.rival)
    interaction.response.send_message.assert_awaited_once()
    assert duel.hand.steps == []
    finish_timers(duel)


async def test_tras_mover_el_rival_decide_si_dobla_o_tira(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await to_decide(duel)
    assert {"backgammon:roll", "backgammon:double"} <= ids(duel.view)
    roll = duel.next_roll
    await press(duel.view, "roll", duel.rival)
    assert duel.stage is Stage.MOVE and duel.hand is not None and duel.hand.roll == roll
    finish_timers(duel)


async def test_doblar_retiene_al_que_dobla_y_aceptar_al_que_acepta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await to_decide(duel)
    await press(duel.view, "double", duel.rival)
    extra = double_extra(STAKE, 1)
    assert duel.stage is Stage.OFFER
    assert ledger(tmp_path, RIVAL_ID)[-1] == ("backgammon:apuesta", -extra)
    assert len(ledger(tmp_path, OWNER_ID)) == 1
    await press(duel.view, "take", duel.challenger)
    assert ledger(tmp_path, OWNER_ID)[-1] == ("backgammon:apuesta", -extra)
    assert duel.cube == Cube(2, Side.WHITE)
    assert duel.stage is Stage.MOVE and duel.turn is Side.BLACK
    assert duel.held == dict.fromkeys(Side, escrow(STAKE, 2))
    finish_timers(duel)


async def test_pasar_un_doblado_da_la_partida_sencilla_al_que_dobla(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await to_decide(duel)
    await press(duel.view, "double", duel.rival)
    await press(duel.view, "drop", duel.challenger)
    assert duel.phase is Phase.DONE
    risked = at_risk(STAKE, 1, 1)
    won = duel_prize(risked) - risked
    assert await net(cog, RIVAL_ID) == won - gambling_day_tax(won)
    assert await net(cog, OWNER_ID) == -risked
    assert not cog.duels and not cog.busy
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert record.losses == 1


async def test_solo_dobla_quien_tiene_el_cubo_o_nadie(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await to_decide(duel)
    await press(duel.view, "double", duel.rival)
    await press(duel.view, "take", duel.challenger)
    await play_turn(duel)
    # Ahora el cubo es de las blancas y les toca: deciden.
    assert duel.stage is Stage.DECIDE and duel.turn is Side.WHITE
    await press(duel.view, "roll", duel.challenger)
    await play_turn(duel)
    # A las negras no les toca decidir: el cubo no es suyo, tiran sin preguntar.
    assert duel.stage is Stage.MOVE and duel.turn is Side.BLACK
    finish_timers(duel)


async def test_ganar_un_gammon_con_el_cubo_a_dos_cobra_por_cuatro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    # Las negras doblaron y las blancas aceptaron: lo retenido ya cubre el cubo a ×2.
    for member in (duel.challenger, duel.rival):
        await cog.economy.place_bet(
            GUILD_ID, member.id, game="backgammon", stake=double_extra(STAKE, 1)
        )
    duel.held = dict.fromkeys(Side, escrow(STAKE, 2))
    duel.cube = Cube(2, Side.WHITE)
    duel.position = duel.origin = WHITE_ALMOST_GAMMON
    duel.log = []
    duel.hand = Hand(WHITE_ALMOST_GAMMON, Side.WHITE, (1, 2))
    duel.turn, duel.stage = Side.WHITE, Stage.MOVE
    duel.view.playing()
    await play_turn(duel)
    assert duel.phase is Phase.DONE
    risked = at_risk(STAKE, 2, 2)
    won = duel_prize(risked) - risked
    assert await net(cog, OWNER_ID) == won - gambling_day_tax(won)
    assert await net(cog, RIVAL_ID) == -risked
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).gammons == 1


async def test_quien_se_duerme_pierde(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    cog.wall_clock.now = duel.deadline + 1
    await duel._turn_timeout(duel._key())
    assert duel.phase is Phase.DONE
    risked = at_risk(STAKE, 1, 1)
    won = duel_prize(risked) - risked
    assert await net(cog, RIVAL_ID) == won - gambling_day_tax(won)
    assert await net(cog, OWNER_ID) == -risked


async def test_un_aviso_de_sueno_viejo_no_acaba_un_duelo_que_ya_siguio(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    old = duel._key()
    await to_decide(duel)
    cog.wall_clock.now = duel.deadline + 1
    await duel._turn_timeout(old)
    assert duel.phase is Phase.PLAYING
    finish_timers(duel)


async def test_rendirse_en_un_duelo_da_el_premio_al_otro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await press(duel.view, "resign", duel.rival)
    risked = at_risk(STAKE, 1, 1)
    won = duel_prize(risked) - risked
    assert await net(cog, OWNER_ID) == won - gambling_day_tax(won)
    assert await net(cog, RIVAL_ID) == -risked


async def test_un_duelo_amistoso_no_mueve_dinero_ni_tiene_cubo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog, None)
    await play_turn(duel)
    assert duel.stage is Stage.MOVE and duel.turn is Side.BLACK  # sin cubo: se tira sin decidir
    await press(duel.view, "resign", duel.rival)
    assert ledger_is_empty(tmp_path)
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).wins == 1


async def bet(duel: Duel, fan: int, side: Side, amount: str) -> MagicMock:
    interaction = fake_interaction(make_user(fan))
    await duel.place(interaction, side, amount)
    return interaction


async def test_la_grada_cobra_y_paga_segun_la_porra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await bet(duel, FAN_IDS[0], Side.WHITE, "100")
    await bet(duel, FAN_IDS[1], Side.BLACK, "100")
    assert ledger(tmp_path, FAN_IDS[0]) == [("backgammon:apuesta", -100)]
    await press(duel.view, "resign", duel.rival)
    assert await net(cog, FAN_IDS[0]) > 0 and await net(cog, FAN_IDS[1]) == -100


async def test_la_grada_se_cierra_tras_los_primeros_turnos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    for _ in range(GRADA_TURNS):
        if duel.stage is Stage.DECIDE:
            await press(duel.view, "roll", duel.member(duel.turn))
        await play_turn(duel)
    interaction = await bet(duel, FAN_IDS[0], Side.WHITE, "100")
    assert "cerrada" in interaction.response.send_message.await_args.args[0]
    assert ledger(tmp_path, FAN_IDS[0]) == []
    finish_timers(duel)


async def test_apagar_a_medias_devuelve_todo_lo_retenido(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await bet(duel, FAN_IDS[0], Side.WHITE, "300")
    await to_decide(duel)
    await press(duel.view, "double", duel.rival)
    await cog.cog_unload()
    for user_id in (OWNER_ID, RIVAL_ID, FAN_IDS[0]):
        assert await net(cog, user_id) == 0
    assert duel.phase is Phase.DONE and not cog.duels
    cog.renderer.close.assert_awaited_once()


async def test_rechazar_no_mueve_dinero_y_libera_a_los_dos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    await press(duel.view, "reject", duel.rival)
    assert duel.phase is Phase.DONE and not cog.busy and ledger_is_empty(tmp_path)


async def test_no_se_puede_estar_en_dos_duelos_a_la_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    errors = AsyncMock()
    again = await cog._backgammon_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(FAN_IDS[0]),
        amount_text=None,
        rival=make_user(RIVAL_ID),
        send=AsyncMock(),
        send_error=errors,
    )
    assert again is None and "ya está en un duelo" in errors.await_args.args[0]
    finish_timers(duel)


async def test_al_salir_de_un_servidor_se_borran_sus_fichas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    event = GameEvent(Ending.WIN, Side.WHITE, LEVEL_BY_KEY["normal"], 1)
    await cog.settle(GUILD_ID, make_user(OWNER_ID), event)
    await cog.on_guild_remove(MagicMock(id=GUILD_ID))
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).games == 0
