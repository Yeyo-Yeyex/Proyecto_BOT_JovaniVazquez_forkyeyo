"""Pruebas de bot.cogs.enraya: la mesa contra Jovani, los duelos con grada y los retos que pagan.

Se usa la economía real sobre un SQLite temporal, un dibujante falso (las
imágenes tienen sus propias pruebas), esperas a cero salvo las largas (el reto,
el turno), un reloj fijo y un azar cuyo primer número decide los colores del
duelo (`Fixed`). Los retos del día se sustituyen por unos que no se cumplen en
una partida (salvo en las pruebas que cobran retos): así el dinero que se mueve
en cada prueba es solo el que esa prueba espera.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import enraya as enraya_cog
from bot.cogs.enraya import GRADA_MOVES, Ahead, Duel, Enraya, Phase, SoloTable
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import EconomyRepository
from bot.repositories.enraya import EnrayaRepository
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.enraya import (
    CHALLENGE_BY_KEY,
    LEVEL_BY_KEY,
    Board,
    Challenge,
    Disc,
    Ending,
    GameEvent,
    grada_payouts,
    jovani_move,
    plan_seed,
    reward_for,
)
from bot.services.enraya_render import Media
from bot.services.gallos import duel_prize
from bot.services.levels import TIMEZONE
from bot.services.taxes import gambling_day_tax

GUILD_ID = 1
OWNER_ID = 10
RIVAL_ID = 11
FAN_IDS = (20, 21, 22)
#: Lo que retiene el casino al que gana un duelo de 500 Y$.
PRIZE_TAX = gambling_day_tax(duel_prize(500) - 500)
#: El primer `random()` del azar: por encima de 0.5 no se cambian los colores del duelo.
KEEP_COLORS, SWAP_COLORS = 0.9, 0.1
NOON = datetime(2026, 10, 14, 12, 0, tzinfo=TIMEZONE).timestamp()

HORIZONTAL = [0, 0, 1, 1, 2, 2, 3]
#: Las rojas (que salen) ganan con su cuarta ficha, sin que las amarillas hagan nada.
DRAW = [
    3, 3, 0, 2, 5, 4, 5, 6, 4, 2, 2, 3, 3, 5, 5, 2, 2, 4, 3, 3, 1,
    1, 2, 1, 5, 5, 0, 4, 0, 4, 4, 6, 6, 6, 6, 0, 1, 0, 6, 0, 1, 1,
]  # fmt: skip
#: Las amarillas ya tienen tres en la fila de abajo (0, 1 y 2) y amenazan la columna 3.
YELLOW_THREAT = [6, 0, 5, 1, 6, 2]


class Fixed(random.Random):
    """Azar cuyo primer `random()` es `first` (los colores del duelo); lo demás, de una semilla."""

    def __init__(self, first: float) -> None:
        super().__init__(7)
        self.first: float | None = first

    def getrandbits(self, k: int) -> int:
        # Sin esto, `randrange` (la semilla de cada turno) gastaría el primer `random()`.
        return super().getrandbits(k)

    def random(self) -> float:
        if self.first is not None:
            value, self.first = self.first, None
            return value
        return super().random()


class Clock:
    """Reloj de pared que solo avanza cuando la prueba lo dice."""

    def __init__(self) -> None:
        self.now = NOON

    def __call__(self) -> float:
        return self.now

    def next_day(self) -> None:
        self.now += timedelta(days=1).total_seconds()


#: Retos que no se cumplen en una partida de las que juegan estas pruebas.
SLOW_CHALLENGES = tuple(CHALLENGE_BY_KEY[key] for key in ("jugar_3", "dificil_1", "imposible_1"))
#: Tres retos (uno de cada dificultad) que se cumplen con cualquier partida.
SURE_CHALLENGES = tuple(
    Challenge(f"seguro_{tier}", f"Reto seguro {tier}", 1, tier, lambda _event: 1)
    for tier in range(3)
)


@pytest.fixture(autouse=True)
def calm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin margen tras el GIF y sin retos que se cumplan por sorpresa."""
    monkeypatch.setattr(enraya_cog, "REVEAL_MARGIN_SECONDS", 0)
    monkeypatch.setattr(enraya_cog, "daily_challenges", lambda *_args: SLOW_CHALLENGES)


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


async def make_cog(tmp_path: Path, first: float = KEEP_COLORS, *, ahead: bool = False) -> Enraya:
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    repository = EnrayaRepository(tmp_path / "bot.db")
    await repository.initialize()

    async def no_sleep(seconds: float) -> None:
        # Las esperas del GIF no se esperan; las del reto y el turno, nunca acaban.
        if seconds >= 30:
            await asyncio.Event().wait()

    return Enraya(
        MagicMock(),
        economy=EconomyService(economy_repository),
        repository=repository,
        rng=Fixed(first),
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


async def net(cog: Enraya, user_id: int) -> int:
    """Lo que ha ganado (o perdido) un miembro respecto a su saldo de bienvenida."""
    return await cog.economy.balance(GUILD_ID, user_id) - STARTING_BALANCE


def played(columns: list[int]) -> Board:
    board = Board()
    for column in columns:
        board = board.play(column)
    return board


def button(view: Any, custom_id: str) -> Any:
    return next(item for item in view.children if item.custom_id == f"enraya:{custom_id}")


def text_of(interaction: MagicMock, method: str = "followup.send") -> str:
    target: Any = interaction
    for part in method.split("."):
        target = getattr(target, part)
    return str(target.await_args.args[0])


# -- Contra Jovani -------------------------------------------------------------------------


async def open_panel(cog: Enraya, user: MagicMock | None = None) -> SoloTable:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    duel = await cog._enraya_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=user or make_user(OWNER_ID),
        amount_text=None,
        rival=None,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    assert duel is None
    assert send.await_args.kwargs["file"].filename == "enraya.png"
    assert send.await_args.kwargs["embed"].image.url == "attachment://enraya.png"
    (panel,) = cog.panels
    return panel


async def click(panel: SoloTable, custom_id: str, user: MagicMock | None = None) -> MagicMock:
    interaction = fake_interaction(user or panel.owner)
    await button(panel, custom_id).callback(interaction)
    return interaction


def irpf_juego(tax: int) -> list[tuple[str, int]]:
    """El apunte de la retención del juego, si la hay (los días que se gana poco no hay)."""
    return [("irpf:juego", -tax)] if tax else []


#: Lo que cobra quien gana un duelo de 500: el premio y, si la hay, su retención.
PRIZE_MOVES = [("enraya:premio", duel_prize(500)), *irpf_juego(PRIZE_TAX)]


async def test_abrir_la_mesa_manda_el_tablero_vacio_con_sus_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    cog.renderer.still.assert_awaited_once()
    assert panel.board.moves == () and panel.player is Disc.RED and not panel.over
    ids = {item.custom_id for item in panel.children}
    assert {f"enraya:col{c}" for c in range(7)} <= ids
    assert "enraya:retos" in ids and "enraya:level" in ids
    assert "enraya:resign" not in ids  # aún no has soltado ninguna ficha
    assert ledger_is_empty(tmp_path)


def ledger_is_empty(tmp_path: Path) -> bool:
    return all(not ledger(tmp_path, uid) for uid in (OWNER_ID, RIVAL_ID, *FAN_IDS))


async def test_soltar_ficha_pone_la_tuya_y_la_respuesta_de_jovani(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    interaction = await click(panel, "col3")
    assert len(panel.board.moves) == 2
    mine, reply = panel.board.moves
    assert (mine.column, mine.row, mine.disc) == (3, 0, Disc.RED)
    assert reply.disc is Disc.YELLOW
    assert panel.note is not None and "Jovani" in panel.note
    assert not panel.over and panel.board.turn is Disc.RED
    cog.renderer.animate.assert_awaited_once()
    interaction.edit_original_response.assert_awaited()
    interaction.response.defer.assert_awaited_once()
    # Con la partida empezada ya hay botón de rendirse.
    assert "enraya:resign" in {item.custom_id for item in panel.children}
    assert ledger_is_empty(tmp_path)


async def test_la_respuesta_de_jovani_es_la_de_la_semilla_del_turno(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    seed, level = panel.turn_seed, panel.level
    after = Board().play(2)
    expected = jovani_move(after, level, plan_seed(seed, 2))
    await click(panel, "col2")
    assert panel.board.moves[1].column == expected


async def test_solo_el_dueno_toca_su_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    stranger = fake_interaction(make_user(RIVAL_ID))
    assert await panel.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once()
    assert "Esta mesa es de" in stranger.response.send_message.await_args.args[0]
    assert await panel.interaction_check(fake_interaction(make_user(OWNER_ID))) is True


async def test_una_columna_llena_se_rechaza_sin_jugar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    panel.board = played([0] * 6)
    panel.rebuild()
    assert button(panel, "col0").disabled and not button(panel, "col1").disabled
    interaction = await click(panel, "col0")
    interaction.response.send_message.assert_awaited_once()
    assert "no admite" in interaction.response.send_message.await_args.args[0]
    assert len(panel.board.moves) == 6
    cog.renderer.animate.assert_not_awaited()


async def test_ganar_acaba_la_partida_la_apunta_y_enseña_el_resultado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    panel.board = played(HORIZONTAL[:-1])  # las rojas ganan soltando en la 4
    panel.rebuild()
    interaction = await click(panel, "col3")
    assert panel.over and panel.board.winner is Disc.RED and len(panel.board.moves) == 7
    assert panel.result is not None and "Le ganas" in panel.result
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert (record.games, record.wins, record.losses, record.streak) == (1, 1, 0, 1)
    assert record.best_level == panel.level.order
    assert panel.record == record
    # Primero el GIF con «fichas cayendo» y luego el resultado con el PNG.
    edits = interaction.edit_original_response.await_args_list
    assert len(edits) >= 2
    assert edits[-1].kwargs["embed"].description == panel.result
    assert edits[-1].kwargs["attachments"][0].filename == "enraya.png"
    assert edits[-2].kwargs["attachments"][0].filename == "enraya.gif"
    assert {"enraya:rematch", "enraya:retos"} <= {item.custom_id for item in panel.children}
    assert ledger_is_empty(tmp_path)


async def test_perder_contra_jovani_cuenta_derrota_y_corta_la_racha(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    panel.level = LEVEL_BY_KEY["imposible"]  # no se despista: gana en el acto
    panel.board = played(YELLOW_THREAT)
    panel.rebuild()
    await click(panel, "col5")
    assert panel.over and panel.board.winner is Disc.YELLOW
    assert panel.result is not None and "hace cuatro en raya" in panel.result
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert (record.games, record.wins, record.losses, record.streak) == (1, 0, 1, 0)


async def test_rendirse_cuenta_como_perder(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    await click(panel, "col3")
    interaction = await click(panel, "resign")
    assert panel.over and panel.board.winner is None
    assert panel.result is not None and "Te rindes" in panel.result
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert (record.games, record.wins, record.losses, record.streak) == (1, 0, 1, 0)
    interaction.edit_original_response.assert_awaited()
    assert ledger_is_empty(tmp_path)


async def test_rendirse_sin_haber_soltado_ficha_no_hace_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    interaction = fake_interaction(panel.owner)
    await panel._resign(interaction)
    interaction.response.defer.assert_awaited_once()
    assert not panel.over and (await cog.repository.get(GUILD_ID, OWNER_ID)).games == 0


async def test_la_revancha_alterna_quien_sale(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    panel.board = played(HORIZONTAL[:-1])
    panel.rebuild()
    await click(panel, "col3")
    assert panel.over and panel.player_first

    # Segunda partida: sale Jovani, con las rojas, y te toca con las amarillas.
    await click(panel, "rematch")
    assert not panel.over and panel.games == 1 and not panel.player_first
    assert panel.player is Disc.YELLOW and panel.result is None
    assert len(panel.board.moves) == 1 and panel.board.last.disc is Disc.RED
    assert panel.note is not None and "Jovani sale" in panel.note
    await click(panel, "col3")
    assert [m.disc for m in panel.board.moves[:2]] == [Disc.RED, Disc.YELLOW]
    assert panel.board.moves[1].column == 3

    # Tercera: vuelves a salir tú, con el tablero vacío.
    await click(panel, "resign")
    assert panel.over
    await click(panel, "rematch")
    assert panel.player_first and panel.board.moves == () and panel.games == 2
    assert panel.player is Disc.RED


async def test_la_revancha_solo_vale_al_acabar_la_partida(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    interaction = fake_interaction(panel.owner)
    await panel._rematch(interaction)
    interaction.response.defer.assert_awaited_once()
    assert panel.games == 0 and panel.player_first


async def test_el_nivel_se_cambia_con_el_tablero_vacio_y_no_a_mitad_de_partida(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    interaction = fake_interaction(panel.owner)
    interaction.data = {"values": ["dificil"]}
    await panel._choose_level(interaction)
    assert panel.level is LEVEL_BY_KEY["dificil"]
    assert cog.renderer.still.await_count == 2  # el tablero de entrada y el nuevo nivel
    await click(panel, "col3")
    midgame = fake_interaction(panel.owner)
    midgame.data = {"values": ["facil"]}
    await panel._choose_level(midgame)
    assert panel.level is LEVEL_BY_KEY["dificil"]


async def test_si_la_mesa_caduca_a_medias_cuenta_como_rendirse(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    await click(panel, "col3")
    await panel.on_timeout()
    assert panel.over and panel not in cog.panels
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert (record.games, record.losses) == (1, 1)
    assert all(item.disabled for item in panel.children)


async def test_si_la_mesa_caduca_sin_jugar_no_se_apunta_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    await panel.on_timeout()
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).games == 0


async def test_los_retos_se_ven_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    interaction = await click(panel, "retos")
    interaction.response.defer.assert_awaited_once()
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "Retos de hoy" in embed.description
    for challenge in SLOW_CHALLENGES:
        assert challenge.label in embed.description
    assert "0/" in embed.description


async def test_con_pintado_por_adelantado_el_clic_usa_el_dibujo_ya_hecho(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, ahead=True)
    panel = await open_panel(cog)
    assert panel.ahead.task is not None
    await panel.ahead.task
    ready = await panel.ahead.take(panel._key(), 3)
    assert ready is not None and len(ready.board.moves) == 2
    # Una columna, rendirse no (aún no has jugado) y los otros tres niveles.
    assert cog.renderer.animate.await_count == 7
    assert cog.renderer.still.await_count == 1 + 3
    await click(panel, "col3")
    assert panel.board is ready.board
    await panel.ahead.task  # el turno siguiente se pinta en segundo plano
    assert cog.renderer.animate.await_count > 7


# -- Retos que pagan -----------------------------------------------------------------------


async def test_los_retos_cumplidos_se_pagan_con_su_concepto_en_el_libro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enraya_cog, "daily_challenges", lambda *_args: SURE_CHALLENGES)
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    panel.board = played(HORIZONTAL[:-1])
    panel.rebuild()
    await click(panel, "col3")
    moves = ledger(tmp_path, OWNER_ID)
    assert [reason for reason, _amount in moves] == [
        f"enraya:reto:{c.key}" for c in SURE_CHALLENGES
    ]
    for (_reason, amount), challenge in zip(moves, SURE_CHALLENGES, strict=True):
        assert 0 < amount <= reward_for(challenge, 1)
    assert panel.result is not None
    assert all(f"«{c.label}»" in panel.result for c in SURE_CHALLENGES)
    assert "completa los tres retos" in panel.result
    # Y el dinero solo se movió por los retos: nada de apuestas contra Jovani.
    assert not [m for m in moves if m[0] == "enraya:apuesta"]
    assert await net(cog, OWNER_ID) == sum(amount for _reason, amount in moves)


async def test_un_reto_cumplido_no_se_paga_dos_veces_el_mismo_dia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enraya_cog, "daily_challenges", lambda *_args: SURE_CHALLENGES)
    cog = await make_cog(tmp_path)
    event = GameEvent(Ending.WIN, Disc.RED, LEVEL_BY_KEY["normal"], played(HORIZONTAL))
    first = await cog.settle(GUILD_ID, make_user(OWNER_ID), event)
    assert first.paid == sum(reward_for(c, 1) for c in SURE_CHALLENGES) and first.retos_full
    assert first.record is not None and len(first.record.completed) == 3
    before = ledger(tmp_path, OWNER_ID)
    second = await cog.settle(GUILD_ID, make_user(OWNER_ID), event)
    assert second.paid == 0 and not second.retos_full
    assert ledger(tmp_path, OWNER_ID) == before


async def test_la_racha_de_dias_sube_el_premio_de_los_retos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enraya_cog, "daily_challenges", lambda *_args: SURE_CHALLENGES)
    cog = await make_cog(tmp_path)
    user = make_user(OWNER_ID)
    event = GameEvent(Ending.WIN, Disc.RED, LEVEL_BY_KEY["normal"], played(HORIZONTAL))
    await cog.settle(GUILD_ID, user, event)
    cog.wall_clock.next_day()
    second = await cog.settle(GUILD_ID, user, event)
    assert second.record is not None and second.record.after.day_streak == 2
    assert second.paid == sum(reward_for(c, 2) for c in SURE_CHALLENGES)
    assert second.paid > sum(reward_for(c, 1) for c in SURE_CHALLENGES)
    assert any("2 días seguidos" in line for line in second.lines)


async def test_el_contenido_de_la_ficha_y_los_retos_de_hoy(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    event = GameEvent(Ending.WIN, Disc.RED, LEVEL_BY_KEY["dificil"], played(HORIZONTAL))
    await cog.settle(GUILD_ID, make_user(OWNER_ID), event)
    embed = await cog.enraya_embed(GUILD_ID, make_user(OWNER_ID))
    text = embed.description
    assert "1 partida" in text and "1 victoria" in text
    assert LEVEL_BY_KEY["dificil"].name in text and "Puesto 1" in text
    assert "1/3" in text  # jugar_3
    assert all(c.label in text for c in SLOW_CHALLENGES)


# -- Abrir mesa, retar y errores -------------------------------------------------------------


async def run_impl(cog: Enraya, **overrides: Any) -> tuple[Duel | None, AsyncMock, AsyncMock]:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    params: dict[str, Any] = {
        "guild": MagicMock(id=GUILD_ID),
        "channel": None,
        "user": make_user(OWNER_ID),
        "amount_text": None,
        "rival": None,
        "send": send,
        "send_error": errors,
    }
    params.update(overrides)
    duel = await cog._enraya_impl(**params)
    return duel, send, errors


async def test_contra_jovani_no_se_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel, send, errors = await run_impl(cog, amount_text="100")
    assert duel is None and not cog.panels
    errors.assert_awaited_once()
    assert "no se apuesta" in errors.await_args.args[0]
    send.assert_not_awaited()
    assert ledger_is_empty(tmp_path)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"guild": None}, "dentro de un servidor"),
        ({"rival": make_user(OWNER_ID)}, "retarte a ti mismo"),
        ({"rival": MagicMock(id=RIVAL_ID, bot=True)}, "bots no juegan"),
        ({"rival": make_user(RIVAL_ID), "amount_text": "mucho"}, "No entiendo"),
        ({"rival": make_user(RIVAL_ID), "amount_text": "0"}, "mayor que cero"),
        ({"rival": make_user(RIVAL_ID), "amount_text": "999999"}, "Necesitas"),
    ],
    ids=["sin_servidor", "uno_mismo", "un_bot", "cantidad_rara", "cero", "sin_saldo"],
)
async def test_los_retos_mal_planteados_se_rechazan_sin_mover_dinero(
    tmp_path: Path, overrides: dict[str, Any], fragment: str
) -> None:
    cog = await make_cog(tmp_path)
    duel, send, errors = await run_impl(cog, **overrides)
    assert duel is None and not cog.duels and not cog.busy
    errors.assert_awaited_once()
    assert fragment in errors.await_args.args[0]
    send.assert_not_awaited()
    assert ledger_is_empty(tmp_path)


async def test_el_canal_de_casino_manda_si_esta_configurado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({999})
    duel, send, errors = await run_impl(cog, channel=MagicMock(id=5))
    assert duel is None and not cog.panels
    errors.assert_awaited_once()
    send.assert_not_awaited()


async def test_el_comando_de_barra_abre_la_mesa_o_reta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    interaction = fake_interaction(make_user(OWNER_ID))
    interaction.guild = MagicMock(id=GUILD_ID)
    interaction.channel = None
    interaction.original_response = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await Enraya.enraya.callback(cog, interaction, None, None)
    assert len(cog.panels) == 1 and not cog.duels

    challenger = fake_interaction(make_user(FAN_IDS[0]))
    challenger.guild = MagicMock(id=GUILD_ID)
    challenger.channel = None
    challenger.original_response = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await Enraya.enraya.callback(cog, challenger, make_user(RIVAL_ID), "200")
    (duel,) = cog.duels
    assert duel.stake == 200 and duel.interactions[FAN_IDS[0]] is challenger
    assert challenger.response.send_message.await_args.kwargs["file"].filename == "enraya.png"


# -- Duelos --------------------------------------------------------------------------------


async def challenge(cog: Enraya, amount: str | None = "500") -> Duel:
    duel, _send, errors = await run_impl(
        cog, amount_text=amount, rival=make_user(RIVAL_ID), user=make_user(OWNER_ID)
    )
    errors.assert_not_awaited()
    assert duel is not None and duel.phase is Phase.CHALLENGE
    assert cog.duels == {duel} and cog.busy == {(GUILD_ID, OWNER_ID), (GUILD_ID, RIVAL_ID)}
    return duel


async def start(cog: Enraya, amount: str | None = "500") -> Duel:
    duel = await challenge(cog, amount)
    await duel.accept(fake_interaction(make_user(RIVAL_ID)))
    assert duel.phase is Phase.PLAYING
    return duel


async def play(duel: Duel, columns: list[int]) -> None:
    for column in columns:
        await duel.drop(fake_interaction(duel.member(duel.board.turn)), column)


def finish_timers(duel: Duel) -> None:
    if duel.timer is not None:
        duel.timer.cancel()


def last_edit(duel: Duel) -> Any:
    return duel.message.edit.await_args.kwargs


async def test_retar_manda_el_reto_y_no_cobra_hasta_aceptar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    assert duel.timer is not None and duel.stake == 500
    assert ledger_is_empty(tmp_path)
    finish_timers(duel)


async def test_aceptar_cobra_la_apuesta_a_los_dos_y_empieza_la_partida(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    for user_id in (OWNER_ID, RIVAL_ID):
        assert ledger(tmp_path, user_id) == [("enraya:apuesta", -500)]
    assert duel.players == {Disc.RED: duel.challenger, Disc.YELLOW: duel.rival}
    assert duel.timer is not None and duel.deadline > cog.wall_clock()
    finish_timers(duel)


async def test_los_colores_se_sortean_al_aceptar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SWAP_COLORS)
    duel = await start(cog)
    assert duel.players[Disc.RED] is duel.rival and duel.players[Disc.YELLOW] is duel.challenger
    finish_timers(duel)


async def test_aceptar_solo_lo_puede_hacer_el_retado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    for user_id in (OWNER_ID, FAN_IDS[0]):
        stranger = fake_interaction(make_user(user_id))
        await duel.accept(stranger)
        assert "no es para ti" in stranger.response.send_message.await_args.args[0]
    assert duel.phase is Phase.CHALLENGE and ledger_is_empty(tmp_path)
    finish_timers(duel)


async def test_rechazar_no_mueve_dinero_y_libera_a_los_dos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    await duel.reject(fake_interaction(make_user(FAN_IDS[0])))
    assert duel.phase is Phase.CHALLENGE
    await duel.reject(fake_interaction(make_user(OWNER_ID)))
    assert duel.phase is Phase.CHALLENGE
    await duel.reject(fake_interaction(make_user(RIVAL_ID)))
    assert duel.phase is Phase.DONE and not cog.duels and not cog.busy
    assert ledger_is_empty(tmp_path)


async def test_retirar_el_reto_no_mueve_dinero_y_solo_lo_hace_quien_reta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    await duel.withdraw(fake_interaction(make_user(RIVAL_ID)))
    assert duel.phase is Phase.CHALLENGE
    await duel.withdraw(fake_interaction(make_user(OWNER_ID)))
    assert duel.phase is Phase.DONE and not cog.duels and not cog.busy
    assert ledger_is_empty(tmp_path)


async def test_un_reto_sin_respuesta_caduca_sin_mover_dinero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    assert duel.timer is not None
    duel.timer.cancel()
    cog.sleep = AsyncMock()
    await duel._expire()
    assert duel.phase is Phase.DONE and not cog.busy
    assert "Nadie contestó" in last_edit(duel)["embed"].description
    assert ledger_is_empty(tmp_path)


async def test_no_se_puede_estar_en_dos_duelos_a_la_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    for user, rival in (
        (make_user(FAN_IDS[0]), make_user(RIVAL_ID)),
        (make_user(OWNER_ID), make_user(FAN_IDS[0])),
    ):
        other, _send, errors = await run_impl(cog, user=user, rival=rival, amount_text="100")
        assert other is None
        errors.assert_awaited_once()
        assert "ya está en un duelo" in errors.await_args.args[0]
    assert cog.duels == {duel}
    finish_timers(duel)


async def test_al_acabar_un_duelo_los_dos_pueden_volver_a_jugar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog, None)
    await duel.resign(fake_interaction(duel.challenger))
    assert not cog.busy and not cog.duels
    again = await challenge(cog, None)
    assert again is not duel
    finish_timers(again)


async def test_si_al_retado_no_le_llega_no_se_cobra_a_nadie(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.economy.place_bet(GUILD_ID, RIVAL_ID, game="otro", stake=STARTING_BALANCE - 100)
    duel = await challenge(cog, "500")
    interaction = fake_interaction(make_user(RIVAL_ID))
    await duel.accept(interaction)
    assert duel.phase is Phase.CHALLENGE
    interaction.followup.send.assert_awaited_once()
    assert ledger(tmp_path, OWNER_ID) == []
    assert ledger(tmp_path, RIVAL_ID) == [("otro:apuesta", -(STARTING_BALANCE - 100))]
    finish_timers(duel)


async def test_si_a_quien_reta_ya_no_le_llega_se_devuelve_lo_del_retado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog, "500")
    await cog.economy.place_bet(GUILD_ID, OWNER_ID, game="otro", stake=STARTING_BALANCE - 100)
    await duel.accept(fake_interaction(make_user(RIVAL_ID)))
    assert duel.phase is Phase.CHALLENGE
    assert ledger(tmp_path, RIVAL_ID) == [("enraya:apuesta", -500), ("enraya:premio", 500)]
    assert await net(cog, RIVAL_ID) == 0
    finish_timers(duel)


async def test_solo_suelta_ficha_a_quien_le_toca(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    red, yellow = duel.member(Disc.RED), duel.member(Disc.YELLOW)
    out_of_turn = fake_interaction(yellow)
    await duel.drop(out_of_turn, 3)
    assert out_of_turn.response.send_message.await_args.args[0] == "Espera tu turno."
    stranger = fake_interaction(make_user(FAN_IDS[0]))
    await duel.drop(stranger, 3)
    assert "no juegas este duelo" in stranger.response.send_message.await_args.args[0]
    assert duel.board.moves == ()
    await duel.drop(fake_interaction(red), 3)
    assert [(m.column, m.disc) for m in duel.board.moves] == [(3, Disc.RED)]
    again = fake_interaction(red)
    await duel.drop(again, 3)
    assert again.response.send_message.await_args.args[0] == "Espera tu turno."
    assert duel.board.turn is Disc.YELLOW
    finish_timers(duel)


async def test_en_un_duelo_una_columna_llena_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    duel.board = played([0] * 6)
    interaction = fake_interaction(duel.member(duel.board.turn))
    await duel.drop(interaction, 0)
    assert "llena" in interaction.response.send_message.await_args.args[0]
    assert len(duel.board.moves) == 6
    finish_timers(duel)


async def test_ganar_un_duelo_paga_el_premio_al_ganador_y_nada_al_otro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    red, yellow = duel.member(Disc.RED), duel.member(Disc.YELLOW)
    await play(duel, HORIZONTAL)
    assert duel.phase is Phase.DONE and duel.board.winner is Disc.RED
    assert ledger(tmp_path, red.id) == [
        ("enraya:apuesta", -500),
        ("enraya:premio", duel_prize(500)),
        *irpf_juego(PRIZE_TAX),
    ]
    assert ledger(tmp_path, yellow.id) == [("enraya:apuesta", -500)]
    assert await net(cog, red.id) == duel_prize(500) - 500 - PRIZE_TAX
    assert await net(cog, yellow.id) == -500
    assert not cog.busy and not cog.duels
    assert last_edit(duel)["view"] is None
    assert "Gana" in last_edit(duel)["embed"].description
    assert "Ganas el duelo" in text_of(duel.interactions[red.id])
    assert "Pierdes el duelo" in text_of(duel.interactions[yellow.id])
    winner = await cog.repository.get(GUILD_ID, red.id)
    loser = await cog.repository.get(GUILD_ID, yellow.id)
    assert (winner.wins, winner.losses, loser.wins, loser.losses) == (1, 0, 0, 1)


async def test_ganan_las_amarillas_si_las_rojas_se_equivocan(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    red, yellow = duel.member(Disc.RED), duel.member(Disc.YELLOW)
    await play(duel, [6, 0, 6, 1, 5, 2, 5, 3])
    assert duel.board.winner is Disc.YELLOW
    assert ledger(tmp_path, yellow.id)[-len(PRIZE_MOVES) :] == PRIZE_MOVES
    assert ledger(tmp_path, red.id) == [("enraya:apuesta", -500)]


async def test_las_tablas_devuelven_la_apuesta_a_los_dos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await play(duel, DRAW)
    assert duel.phase is Phase.DONE and duel.board.full and duel.board.winner is None
    for user_id in (OWNER_ID, RIVAL_ID):
        assert ledger(tmp_path, user_id) == [("enraya:apuesta", -500), ("enraya:premio", 500)]
        assert await net(cog, user_id) == 0
        record = await cog.repository.get(GUILD_ID, user_id)
        assert (record.games, record.draws, record.wins, record.losses) == (1, 1, 0, 0)
    assert "recupera" in last_edit(duel)["embed"].description


@pytest.mark.parametrize("resigner", [Disc.RED, Disc.YELLOW])
async def test_rendirse_en_un_duelo_da_el_premio_al_otro(tmp_path: Path, resigner: Disc) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await play(duel, [3, 3])  # los dos han jugado; ahora le toca a las rojas
    quitter, other = duel.member(resigner), duel.member(resigner.other)
    await duel.resign(fake_interaction(quitter))
    assert duel.phase is Phase.DONE
    assert ledger(tmp_path, other.id)[-len(PRIZE_MOVES) :] == PRIZE_MOVES
    assert ledger(tmp_path, quitter.id) == [("enraya:apuesta", -500)]
    assert (await cog.repository.get(GUILD_ID, quitter.id)).losses == 1
    assert (await cog.repository.get(GUILD_ID, other.id)).wins == 1
    assert "rinde" in last_edit(duel)["embed"].description


async def test_rendirse_solo_lo_pueden_hacer_los_jugadores(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    stranger = fake_interaction(make_user(FAN_IDS[0]))
    await duel.resign(stranger)
    assert "no juegas este duelo" in stranger.response.send_message.await_args.args[0]
    assert duel.phase is Phase.PLAYING
    finish_timers(duel)


async def test_quien_se_duerme_pierde_y_el_otro_cobra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await play(duel, [3])
    sleeper, winner = duel.member(Disc.YELLOW), duel.member(Disc.RED)
    assert duel.timer is not None
    duel.timer.cancel()
    duel.deadline = cog.wall_clock() - 1
    await duel._turn_timeout(len(duel.board.moves))
    assert duel.phase is Phase.DONE
    assert ledger(tmp_path, winner.id)[-len(PRIZE_MOVES) :] == PRIZE_MOVES
    assert ledger(tmp_path, sleeper.id) == [("enraya:apuesta", -500)]
    assert "por tiempo" in last_edit(duel)["embed"].description
    assert (await cog.repository.get(GUILD_ID, sleeper.id)).losses == 1
    assert not cog.busy


async def test_un_aviso_de_sueno_viejo_no_acaba_un_duelo_que_ya_siguio(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await play(duel, [3])
    assert duel.timer is not None
    duel.timer.cancel()
    duel.deadline = cog.wall_clock() - 1
    await duel._turn_timeout(0)  # el aviso era del turno anterior
    assert duel.phase is Phase.PLAYING
    finish_timers(duel)


async def test_un_duelo_amistoso_no_mueve_dinero_pero_cuenta_para_la_ficha(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog, None)
    assert duel.stake == 0
    await play(duel, HORIZONTAL)
    assert duel.phase is Phase.DONE
    for user_id in (OWNER_ID, RIVAL_ID):
        assert ledger(tmp_path, user_id) == []
        assert await net(cog, user_id) == 0
    red = await cog.repository.get(GUILD_ID, duel.member(Disc.RED).id)
    assert (red.games, red.wins) == (1, 1)


async def test_un_duelo_amistoso_paga_los_retos_que_cumplan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enraya_cog, "daily_challenges", lambda *_args: SURE_CHALLENGES)
    cog = await make_cog(tmp_path)
    duel = await start(cog, None)
    await play(duel, HORIZONTAL)
    for user_id in (OWNER_ID, RIVAL_ID):
        assert [r for r, _a in ledger(tmp_path, user_id)] == [
            f"enraya:reto:{c.key}" for c in SURE_CHALLENGES
        ]


# -- La grada --------------------------------------------------------------------------------


async def bet(duel: Duel, fan: int, disc: Disc, amount: str) -> MagicMock:
    interaction = fake_interaction(make_user(fan))
    await duel.place(interaction, disc, amount)
    return interaction


async def test_apostar_en_la_grada_cobra_al_espectador(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    interaction = await bet(duel, FAN_IDS[0], Disc.RED, "200")
    assert ledger(tmp_path, FAN_IDS[0]) == [("enraya:apuesta", -200)]
    assert duel.crowd[FAN_IDS[0]].stake == 200 and duel.crowd[FAN_IDS[0]].disc is Disc.RED
    interaction.response.send_message.assert_awaited_once()
    assert "200" in interaction.response.send_message.await_args.args[0]
    finish_timers(duel)


async def test_cada_espectador_apuesta_una_vez_y_los_jugadores_no_apuestan(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    fan = fake_interaction(make_user(FAN_IDS[0]))
    await duel._open_bet(fan, Disc.RED)
    fan.response.send_modal.assert_awaited_once()
    await duel.place(fake_interaction(make_user(FAN_IDS[0])), Disc.RED, "100")
    again = fake_interaction(make_user(FAN_IDS[0]))
    await duel._open_bet(again, Disc.YELLOW)
    again.response.send_modal.assert_not_awaited()
    player = fake_interaction(make_user(OWNER_ID))
    await duel._open_bet(player, Disc.RED)
    player.response.send_modal.assert_not_awaited()
    await bet(duel, FAN_IDS[0], Disc.YELLOW, "100")
    assert len(duel.crowd) == 1 and ledger(tmp_path, FAN_IDS[0]) == [("enraya:apuesta", -100)]
    finish_timers(duel)


@pytest.mark.parametrize("amount", ["mucho", "0", "999999"])
async def test_una_apuesta_de_grada_invalida_no_cobra(tmp_path: Path, amount: str) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    interaction = await bet(duel, FAN_IDS[0], Disc.RED, amount)
    interaction.response.send_message.assert_awaited_once()
    assert not duel.crowd and ledger(tmp_path, FAN_IDS[0]) == []
    finish_timers(duel)


async def test_la_grada_se_cierra_con_las_primeras_fichas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await bet(duel, FAN_IDS[0], Disc.RED, "100")
    await play(duel, list(range(GRADA_MOVES)))
    assert len(duel.board.moves) == GRADA_MOVES
    late = await bet(duel, FAN_IDS[1], Disc.YELLOW, "100")
    assert "cerrada" in late.response.send_message.await_args.args[0]
    assert FAN_IDS[1] not in duel.crowd and ledger(tmp_path, FAN_IDS[1]) == []
    opened = fake_interaction(make_user(FAN_IDS[1]))
    await duel._open_bet(opened, Disc.RED)
    opened.response.send_modal.assert_not_awaited()
    finish_timers(duel)


async def test_la_grada_paga_segun_el_reparto_de_la_porra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    red, yellow = duel.member(Disc.RED), duel.member(Disc.YELLOW)
    sides = (Disc.RED, Disc.YELLOW, Disc.RED)
    for fan, disc in zip(FAN_IDS, sides, strict=True):
        await bet(duel, fan, disc, "200")
    await play(duel, HORIZONTAL)  # ganan las rojas
    payouts = grada_payouts({k: s.bet for k, s in duel.crowd.items()}, Disc.RED)
    paid = 0
    withheld = PRIZE_TAX
    for fan in FAN_IDS:
        moves = ledger(tmp_path, fan)
        assert moves[0] == ("enraya:apuesta", -200)
        prizes = [amount for reason, amount in moves[1:] if reason != "irpf:juego"]
        assert sum(prizes) == payouts[fan]
        paid += payouts[fan]
        tax = gambling_day_tax(payouts[fan] - 200)
        withheld += tax
        assert await net(cog, fan) == payouts[fan] - 200 - tax
    assert payouts[FAN_IDS[1]] == 0 and payouts[FAN_IDS[0]] > 200
    # La grada no crea dinero: se reparte lo apostado menos la comisión.
    assert paid <= 600
    # Y en todo el duelo no se crea dinero: lo que falta es la comisión de la casa y lo
    # que retiene el casino a los que ganan.
    total = sum([await net(cog, uid) for uid in (red.id, yellow.id, *FAN_IDS)])
    assert total == -(2 * 500 - duel_prize(500)) - (600 - paid) - withheld
    assert "grada" in last_edit(duel)["embed"].description.lower()


async def test_las_tablas_devuelven_tambien_lo_apostado_a_la_grada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await bet(duel, FAN_IDS[0], Disc.RED, "300")
    await bet(duel, FAN_IDS[1], Disc.YELLOW, "100")
    await play(duel, DRAW)
    assert ledger(tmp_path, FAN_IDS[0]) == [("enraya:apuesta", -300), ("enraya:premio", 300)]
    assert ledger(tmp_path, FAN_IDS[1]) == [("enraya:apuesta", -100), ("enraya:premio", 100)]
    assert [await net(cog, uid) for uid in FAN_IDS[:2]] == [0, 0]


async def test_si_toda_la_grada_apuesta_a_lo_mismo_se_le_devuelve_su_apuesta(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await bet(duel, FAN_IDS[0], Disc.RED, "300")
    await bet(duel, FAN_IDS[1], Disc.RED, "100")
    await play(duel, HORIZONTAL)
    assert ledger(tmp_path, FAN_IDS[0]) == [("enraya:apuesta", -300), ("enraya:premio", 300)]
    assert ledger(tmp_path, FAN_IDS[1]) == [("enraya:apuesta", -100), ("enraya:premio", 100)]
    assert "recupera" in last_edit(duel)["embed"].description


async def test_la_grada_tambien_cobra_en_un_duelo_amistoso(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog, None)
    await bet(duel, FAN_IDS[0], Disc.RED, "100")
    await bet(duel, FAN_IDS[1], Disc.YELLOW, "100")
    await play(duel, HORIZONTAL)
    # Los jugadores no mueven dinero, pero la porra del público se reparte.
    assert ledger(tmp_path, OWNER_ID) == [] and ledger(tmp_path, RIVAL_ID) == []
    assert await net(cog, FAN_IDS[0]) > 0 and await net(cog, FAN_IDS[1]) == -100


# -- Apagar el bot ---------------------------------------------------------------------------


async def test_apagar_con_el_duelo_a_medias_devuelve_lo_apostado_a_todos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await bet(duel, FAN_IDS[0], Disc.RED, "300")
    await play(duel, [3])
    await cog.cog_unload()
    for user_id, stake in ((OWNER_ID, 500), (RIVAL_ID, 500), (FAN_IDS[0], 300)):
        assert ledger(tmp_path, user_id) == [("enraya:apuesta", -stake), ("enraya:premio", stake)]
        assert await net(cog, user_id) == 0
    assert duel.phase is Phase.DONE and not duel.crowd
    assert "suspendido" in last_edit(duel)["embed"].description
    assert not cog.duels
    cog.renderer.close.assert_awaited_once()


async def test_apagar_con_un_reto_sin_aceptar_no_mueve_dinero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await challenge(cog)
    await cog.cog_unload()
    assert duel.phase is Phase.DONE
    assert ledger_is_empty(tmp_path)
    assert "cierra la mesa" in last_edit(duel)["embed"].description


async def test_apagar_un_duelo_ya_terminado_no_devuelve_nada_otra_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    duel = await start(cog)
    await play(duel, HORIZONTAL)
    before = [ledger(tmp_path, uid) for uid in (OWNER_ID, RIVAL_ID)]
    await duel.shutdown()
    assert [ledger(tmp_path, uid) for uid in (OWNER_ID, RIVAL_ID)] == before


async def test_apagar_cierra_las_mesas_contra_jovani(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = await open_panel(cog)
    await cog.cog_unload()
    assert not cog.panels and panel.is_finished()


async def test_al_salir_de_un_servidor_se_borran_sus_fichas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    event = GameEvent(Ending.WIN, Disc.RED, LEVEL_BY_KEY["normal"], played(HORIZONTAL))
    await cog.settle(GUILD_ID, make_user(OWNER_ID), event)
    await cog.on_guild_remove(MagicMock(id=GUILD_ID))
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).games == 0


# -- Pintar por adelantado -----------------------------------------------------------------


async def let_run(condition: Any) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("la condición no llegó a cumplirse")


async def test_sin_adelanto_no_se_pinta_nada_ni_se_devuelve_nada() -> None:
    ahead = Ahead(False)
    paint = AsyncMock()
    ahead.plan("turno", [1, 2], paint)
    assert ahead.task is None and ahead.key is None
    assert await ahead.take("turno", 1) is None
    paint.assert_not_awaited()


async def test_el_adelanto_pinta_en_orden_y_entrega_lo_pintado() -> None:
    ahead = Ahead(True)
    painted: list[int] = []

    async def paint(item: Any) -> str:
        painted.append(item)
        return f"imagen {item}"

    ahead.plan("turno", [3, 2, 4], paint)
    assert ahead.task is not None
    await ahead.task
    assert painted == [3, 2, 4]
    assert await ahead.take("turno", 2) == "imagen 2"
    assert await ahead.take("otro turno", 2) is None  # el turno ya no es ese
    assert await ahead.take("turno", 9) is None  # nunca se pidió


async def test_planear_el_mismo_turno_otra_vez_no_repinta() -> None:
    ahead = Ahead(True)
    paint = AsyncMock(return_value="x")
    ahead.plan("turno", [1], paint)
    first = ahead.task
    ahead.plan("turno", [1], paint)
    assert ahead.task is first
    await first
    paint.assert_awaited_once()


async def test_lo_que_se_esta_pintando_se_espera_y_lo_que_falta_no() -> None:
    ahead = Ahead(True)
    gate = asyncio.Event()

    async def paint(item: Any) -> str:
        if item == 1:
            await gate.wait()
        return f"imagen {item}"

    ahead.plan("turno", [1, 2], paint)
    await let_run(lambda: ahead.current == 1)
    assert await ahead.take("turno", 2) is None  # aún no toca pintarla
    waiting = asyncio.create_task(ahead.take("turno", 1))
    await asyncio.sleep(0)
    assert not waiting.done()
    gate.set()
    assert await waiting == "imagen 1"
    assert ahead.task is not None
    await ahead.task


async def test_cambiar_de_turno_deja_sin_pintar_lo_que_quedaba() -> None:
    ahead = Ahead(True)
    gate = asyncio.Event()
    painted: list[Any] = []

    async def paint(item: Any) -> Any:
        painted.append(item)
        if item == "a1":
            await gate.wait()
        return item

    ahead.plan("a", ["a1", "a2"], paint)
    await let_run(lambda: ahead.current == "a1")
    futures_a = ahead.futures
    ahead.plan("b", ["b1"], paint)
    gate.set()
    assert ahead.task is not None
    await ahead.task
    assert painted == ["a1", "b1"]
    assert futures_a["a2"].result() is None
    assert await ahead.take("b", "b1") == "b1"


async def test_soltar_el_adelanto_descarta_lo_pendiente() -> None:
    ahead = Ahead(True)
    gate = asyncio.Event()
    painted: list[Any] = []

    async def paint(item: Any) -> Any:
        painted.append(item)
        await gate.wait()
        return item

    ahead.plan("turno", [1, 2], paint)
    await let_run(lambda: ahead.current == 1)
    futures = ahead.futures
    ahead.drop()
    gate.set()
    assert ahead.task is not None
    await ahead.task
    assert painted == [1] and futures[2].result() is None
    assert await ahead.take("turno", 2) is None


async def test_si_un_dibujo_falla_el_resto_sigue(caplog: pytest.LogCaptureFixture) -> None:
    ahead = Ahead(True)

    async def paint(item: Any) -> Any:
        if item == 1:
            raise RuntimeError("sin Node")
        return item

    ahead.plan("turno", [1, 2], paint)
    assert ahead.task is not None
    await ahead.task
    assert await ahead.take("turno", 1) is None
    assert await ahead.take("turno", 2) == 2
    assert "No se pudo pintar por adelantado" in caplog.text
