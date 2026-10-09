"""Pruebas de bot.cogs.pachinko: la máquina con botones, el dinero y la Ráfaga.

Se usa la economía real sobre un SQLite temporal (para comprobar que el
dinero se mueve de verdad y que el libro cuadra), tandas trucadas y un
renderizador falso.
"""

from __future__ import annotations

import random
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

import bot.cogs.pachinko as pachinko_module
from bot.cogs.pachinko import (
    BURST_VOLLEYS,
    GIF_NAME,
    PNG_NAME,
    RANDOM_BOARD,
    Pachinko,
    PachinkoPlay,
    PachinkoView,
    burst_text,
    machine_embed,
    parse_args,
    parse_stake,
    paytable_embed,
    result_text,
)
from bot.repositories.economy import EconomyRepository
from bot.services.achievements import (
    PACHINKO_CLEAN_BOUNCES,
    PACHINKO_SLOW_FRAMES,
    PACHINKO_SWIFT_FRAMES,
    pachinko_stats,
)
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.pachinko import (
    BOARDS,
    CLASSIC,
    MIN_STAKE,
    ONI,
    SAKURA,
    Ball,
    Board,
    Draw,
    Kind,
    PachinkoMachine,
    Volley,
    build_volley,
)
from bot.services.pachinko_motion import BallMotion, VolleyMotion, motion_for
from bot.services.pachinko_physics import Start
from bot.services.pachinko_render import PachinkoMedia

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555
MISS = Draw((1, 2, 3), Kind.MISS, False, 0)


START_POCKET = CLASSIC.start_pocket
FEVER_BALLS = CLASSIC.fever_balls


def ball_in(pocket: int, board: Board = CLASSIC) -> Ball:
    return Ball((1,) * pocket + (0,) * (board.rows - pocket))


def blank(board: Board = CLASSIC) -> Volley:
    """Diez bolas en OUT: no devuelve nada."""
    return build_volley(board, [ball_in(3, board)] * 10, lambda: MISS)


BLANK = blank()
#: Un rush de 4 premios gordos en la Clásica: 4 × 30 bolas.
RUSH = build_volley(
    CLASSIC,
    [ball_in(START_POCKET)] + [ball_in(3)] * 9,
    lambda: Draw((5, 5, 5), Kind.RUSH, True, 4),
)
#: Devuelve algo, pero menos de lo apostado (3 bolas de 10).
SMALL = build_volley(CLASSIC, [ball_in(1)] + [ball_in(3)] * 9, lambda: MISS)


class FakeRenderer:
    """Devuelve bytes fijos: las pruebas no necesitan dibujar el tablero.

    Apunta en `motions` el movimiento que recibe cada dibujo.
    """

    def __init__(self) -> None:
        self.motions: list[VolleyMotion | None] = []

    def render(self, volley, *, turbo: bool = False, motion=None) -> PachinkoMedia:  # noqa: ANN001
        self.motions.append(motion)
        return PachinkoMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    def still_png(self, volley, motion=None) -> bytes:  # noqa: ANN001
        self.motions.append(motion)
        return b"STILL"

    def idle_png(self, board) -> bytes:  # noqa: ANN001
        return f"IDLE:{board.key}".encode()


class RiggedMachine(PachinkoMachine):
    """Lanza las tandas que se le digan, en orden; después, tandas en blanco.

    Apunta en `boards` el tablero que pide cada tanda.
    """

    def __init__(self, sequence: Iterable[Volley] = ()) -> None:
        super().__init__()
        self.sequence = list(sequence)
        self.boards: list[str] = []

    def launch(self, board: Board) -> Volley:
        self.boards.append(board.key)
        return self.sequence.pop(0) if self.sequence else blank(board)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """La animación no hace falta esperarla en las pruebas."""
    monkeypatch.setattr(pachinko_module, "REVEAL_MARGIN_SECONDS", 0)


async def make_cog(tmp_path: Path, sequence=(), channels=frozenset()) -> Pachinko:  # noqa: ANN001
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Pachinko(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        machine=RiggedMachine(sequence),
        casino_channel_ids=channels,
    )


def make_user(user_id: int = OWNER_ID, name: str = "Diego") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


def make_view(cog: Pachinko, stake: int = 100) -> PachinkoView:
    return PachinkoView(cog, guild_id=GUILD_ID, owner=make_user(), stake=stake)


def attachment_names(call) -> list[str]:  # noqa: ANN001
    return [file.filename for file in call.kwargs["attachments"]]


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


def make_play(volley: Volley, *, stake: int = 100, won: int = 0) -> PachinkoPlay:
    settlement = MagicMock()
    settlement.balance = 1_000
    settlement.tax_delta = 0
    return PachinkoPlay(
        volley=volley,
        stake=stake,
        won=won,
        settlement=settlement,
        media=PachinkoMedia(b"", b"", 0.0),
        session_volleys=1,
        motion=motion_for(volley),
    )


# -- Presentación -------------------------------------------------------------------


def test_el_rush_se_celebra_con_sus_numeros() -> None:
    text = result_text(make_play(RUSH, won=1_200), random.Random(0))
    assert "5 5 5" in text
    assert "×4" in text


def test_devolver_menos_de_lo_apostado_se_celebra_como_premio() -> None:
    text = result_text(make_play(SMALL, won=30), random.Random(0))
    assert "Cobras" in text


def test_la_reserva_llena_lo_dice() -> None:
    volley = build_volley(CLASSIC, [ball_in(START_POCKET)] * 6 + [ball_in(3)] * 4, lambda: MISS)
    assert "limbo" in result_text(make_play(volley), random.Random(0))


def test_resumen_de_la_rafaga() -> None:
    text = burst_text([make_play(BLANK), make_play(RUSH, won=1_200)], "Parado: ¡ATARI!")
    assert "2 tandas" in text and "×4" in text and "ATARI" in text


def test_apuesta_por_defecto_minimo_y_formatos() -> None:
    assert parse_stake(None, 1_000) == 100
    assert parse_stake(None, 40) == 40
    assert parse_stake("all", 777) == 777
    with pytest.raises(ValueError):
        parse_stake("5", 1_000)
    with pytest.raises(ValueError):
        parse_stake("azul", 1_000)


# -- Dinero -------------------------------------------------------------------------


async def test_lanzar_cobra_anima_y_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [RUSH])
    view = make_view(cog)
    interaction = make_interaction()

    await view._launch(interaction)

    won = 100 * 4 * FEVER_BALLS // 10
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    treasury = await cog.economy.treasury(GUILD_ID, since=0)
    # Lo que no ha llegado al jugador es exactamente lo que se ha quedado el Estado.
    assert balance + treasury.balance == STARTING_BALANCE - 100 + won
    assert ledger_sum(tmp_path, OWNER_ID) == balance
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == treasury.balance
    interaction.response.defer.assert_awaited_once()
    first, final = interaction.edit_original_response.await_args_list
    assert attachment_names(first) == [GIF_NAME]
    assert attachment_names(final) == [PNG_NAME]


async def test_perder_despues_de_ganar_devuelve_el_irpf_desde_el_estado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [RUSH])
    await cog.economy.grant(GUILD_ID, OWNER_ID, amount=100_000, reason="test")
    view = make_view(cog, stake=1_000)
    await view._launch(make_interaction())
    after_win = (await cog.economy.treasury(GUILD_ID, since=0)).balance

    view.stake = 10_000
    await view._launch(make_interaction())  # BLANK: pierde 10.000

    after_loss = (await cog.economy.treasury(GUILD_ID, since=0)).balance
    assert after_loss < after_win
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == after_loss
    assert ledger_sum(tmp_path, OWNER_ID) == await cog.economy.balance(GUILD_ID, OWNER_ID)


async def test_en_turbo_solo_se_edita_una_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.turbo = True
    interaction = make_interaction()

    await view._launch(interaction)

    assert attachment_names(interaction.edit_original_response.await_args) == [PNG_NAME]
    interaction.edit_original_response.assert_awaited_once()


async def test_el_turbo_se_recuerda_para_la_siguiente_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await make_view(cog)._toggle_turbo(make_interaction())
    assert make_view(cog).turbo


async def test_sin_saldo_no_lanza_y_avisa_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=STARTING_BALANCE + 10)
    interaction = make_interaction()

    await view._launch(interaction)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    interaction.edit_original_response.assert_not_awaited()
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_clic_mientras_caen_las_bolas_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view._busy = True
    interaction = make_interaction()

    await view._launch(interaction)

    interaction.response.defer.assert_awaited_once()
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_otro_miembro_no_puede_jugar_en_tu_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    interaction = make_interaction(user_id=99)
    assert not await make_view(cog).interaction_check(interaction)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_la_rafaga_juega_cinco_tandas_con_una_sola_edicion(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    interaction = make_interaction()

    await view._burst(interaction)

    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * BURST_VOLLEYS
    interaction.edit_original_response.assert_awaited_once()
    assert view.session_volleys == BURST_VOLLEYS


async def test_la_rafaga_para_con_un_atari_y_lo_anuncia(tmp_path: Path) -> None:
    long_rush = build_volley(
        CLASSIC,
        [ball_in(START_POCKET)] + [ball_in(3)] * 9,
        lambda: Draw((3, 3, 3), Kind.RUSH, True, 5),
    )
    cog = await make_cog(tmp_path, [BLANK, long_rush])
    view = make_view(cog)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    view.message = MagicMock(channel=channel)

    await view._burst(make_interaction())

    assert view.session_volleys == 2
    assert "Parado" in view.last_text
    channel.send.assert_awaited_once()  # 5 premios gordos encadenados: se anuncia


async def test_un_rush_corto_no_llena_el_canal(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    await cog.shout(make_play(RUSH, won=1_200), make_user(), channel)
    channel.send.assert_not_awaited()


async def test_el_movimiento_se_calcula_fuera_del_event_loop_tambien_sin_dibujar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turbo y Ráfaga no dibujan, pero los logros necesitan los choques: `motion_for` siempre."""
    threads: list[str] = []
    real = pachinko_module.motion_for

    def spy(volley: Volley) -> VolleyMotion:
        threads.append(threading.current_thread().name)
        return real(volley)

    monkeypatch.setattr(pachinko_module, "motion_for", spy)
    cog = await make_cog(tmp_path, [RUSH, BLANK])

    drawn = await cog.play(GUILD_ID, OWNER_ID, stake=100, board=CLASSIC, turbo=False)
    silent = await cog.play(GUILD_ID, OWNER_ID, stake=100, board=CLASSIC, turbo=True, render=False)

    assert len(threads) == 2
    assert threading.main_thread().name not in threads
    assert drawn.motion == motion_for(RUSH) and silent.motion == motion_for(BLANK)
    assert cog.renderer.motions == [drawn.motion]  # el dibujo reutiliza el mismo cálculo


async def test_si_el_movimiento_falla_no_se_mueve_dinero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(volley: Volley) -> VolleyMotion:
        raise RuntimeError("la física no cuadra")

    monkeypatch.setattr(pachinko_module, "motion_for", broken)
    cog = await make_cog(tmp_path, [RUSH])
    with pytest.raises(RuntimeError):
        await cog.play(GUILD_ID, OWNER_ID, stake=100, board=CLASSIC, turbo=True)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_la_rafaga_dibuja_el_ultimo_con_su_movimiento(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    await view._burst(make_interaction())
    assert len(cog.renderer.motions) == 1
    assert isinstance(cog.renderer.motions[0], VolleyMotion)


async def test_la_mitad_no_baja_del_minimo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=15)
    await view._halve(make_interaction())
    assert view.stake == MIN_STAKE


# -- Tableros -----------------------------------------------------------------------


async def test_la_maquina_empieza_en_la_clasica(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    assert view.board is CLASSIC
    await view._launch(make_interaction())
    assert cog.machine.boards == ["clasica"]


async def test_elegir_tablero_cambia_la_imagen_y_las_tandas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.board_select._values = ["oni"]  # lo que Discord rellena al elegir
    interaction = make_interaction()

    await view._choose_board(interaction)

    kwargs = interaction.edit_original_response.await_args.kwargs

    assert kwargs["attachments"][0].filename == PNG_NAME
    assert "Oni" in kwargs["embed"].title
    assert [o.default for o in view.board_select.options if o.value == "oni"] == [True]
    await view._launch(make_interaction())
    assert cog.machine.boards == ["oni"]
    # Se recuerda para la próxima máquina.
    assert make_view(cog).board is ONI


async def test_al_azar_cada_tanda_cae_en_un_tablero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    picks = iter([0, 3, 1, 2, 0])
    cog.machine._randbelow = lambda n: next(picks)  # type: ignore[attr-defined]
    view = PachinkoView(cog, guild_id=GUILD_ID, owner=make_user(), stake=10, board_key=RANDOM_BOARD)
    assert view.board is None

    await view._burst(make_interaction())

    assert cog.machine.boards == ["sakura", "oni", "clasica", "dragon", "sakura"]
    assert "Al azar" in (await view.current_embed()).title


def test_el_texto_dice_que_tablero_ha_tocado_al_azar() -> None:
    volley = blank(ONI)
    text = result_text(make_play(volley), random.Random(0), random_board=True)
    assert "Oni" in text


def test_la_tabla_de_premios_resume_todos_los_tableros() -> None:
    text = paytable_embed(SAKURA).description
    assert all(board.name in text for board in BOARDS.values())
    assert "Sakura" in paytable_embed(None).description


def test_el_embed_dice_el_riesgo() -> None:
    embed = machine_embed(owner="Diego", balance=1_000, stake=100, turbo=False, board=ONI)
    assert "Oni" in embed.title
    assert any(field.value == ONI.risk for field in embed.fields)


def test_cantidad_y_tablero_en_cualquier_orden() -> None:
    assert parse_args("500", "oni") == ("500", "oni")
    assert parse_args("Dragón", "2k") == ("2k", "dragon")
    assert parse_args("azar", None) == (None, RANDOM_BOARD)
    assert parse_args(None, None) == (None, None)
    with pytest.raises(ValueError):
        parse_args("oni", "sakura")
    with pytest.raises(ValueError):
        parse_args("500", "600")


# -- Comando ------------------------------------------------------------------------


async def test_pachinko_abre_la_maquina_parada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send = AsyncMock(return_value=MagicMock())

    await cog._pachinko_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=CASINO_CHANNEL),
        user=make_user(),
        amount_text="250",
        send=send,
        send_error=AsyncMock(),
    )

    kwargs = send.await_args.kwargs
    assert kwargs["file"].filename == PNG_NAME
    assert kwargs["view"].stake == 250


async def test_pachinko_con_tablero_lo_abre_y_lo_recuerda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send = AsyncMock(return_value=MagicMock())

    await cog._pachinko_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=CASINO_CHANNEL),
        user=make_user(),
        amount_text=None,
        send=send,
        send_error=AsyncMock(),
        board_key="sakura",
    )

    assert send.await_args.kwargs["view"].board is SAKURA
    assert cog.board_default(GUILD_ID, OWNER_ID) == "sakura"


async def test_pachinko_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))
    send_error = AsyncMock()

    await cog._pachinko_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=1234),
        user=make_user(),
        amount_text=None,
        send=AsyncMock(),
        send_error=send_error,
    )

    assert "pachinko" in send_error.await_args.args[0].lower()


async def test_pachinko_con_mas_de_lo_que_tienes_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send_error = AsyncMock()

    await cog._pachinko_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(),
        user=make_user(),
        amount_text="5000",
        send=AsyncMock(),
        send_error=send_error,
    )

    send_error.assert_awaited_once()


# -- Logros -------------------------------------------------------------------------


def test_estadisticas_de_un_rush() -> None:
    delta = pachinko_stats(
        RUSH,
        motion=motion_for(RUSH),
        stake=100,
        won=1_200,
        turbo=False,
        session_volleys=3,
        when=datetime(2026, 1, 1, 4, tzinfo=TIMEZONE),
    )
    assert delta.add["pachinko_volleys"] == 1
    assert delta.add["pachinko_atari"] == 1
    assert delta.add["pachinko_rush"] == 1
    assert delta.add["pachinko_night"] == 1
    assert delta.peak["pachinko_renchan_max"] == 4
    assert delta.peak["pachinko_win_max"] == 1_100
    assert "pachinko_super" not in delta.add
    assert delta.add["pachinko_board_clasica"] == 1
    assert delta.add["pachinko_atari_clasica"] == 1


def test_estadisticas_de_una_tanda_en_blanco() -> None:
    delta = pachinko_stats(
        BLANK,
        motion=motion_for(BLANK),
        stake=100,
        won=0,
        turbo=True,
        session_volleys=1,
        when=datetime(2026, 1, 1, 12, tzinfo=TIMEZONE),
    )
    assert delta.add["pachinko_blank"] == 1
    assert delta.add["pachinko_turbo"] == 1
    assert "pachinko_win_max" not in delta.peak


def fake_motion(*falls: tuple[int, ...], pocket: int = 0) -> VolleyMotion:
    """Movimiento de mentira para fijar cifras.

    Una bola por `(fotogramas, rebotes, choques)`, con opcionalmente su bolsillo y los
    fotogramas de retraso al final.
    """
    balls = []
    for index, (frames, bounces, collisions, *extra) in enumerate(falls):
        balls.append(
            BallMotion(
                pocket=extra[0] if extra else pocket,
                start=Start(100.0, 0.0),
                launch=index * 3,
                points=((100.0, 170.0),) * frames,
                landing=index * 3 + frames,
                bounces=bounces,
                collisions=collisions,
                delay=extra[1] if len(extra) > 1 else 0,
            )
        )
    return VolleyMotion(tuple(balls))


WHEN = datetime(2026, 1, 1, 12, tzinfo=TIMEZONE)


def stats_of(volley: Volley, motion: VolleyMotion):  # noqa: ANN201
    return pachinko_stats(
        volley, motion=motion, stake=100, won=0, turbo=False, session_volleys=1, when=WHEN
    )


def test_estadisticas_de_rebotes_y_duracion_de_una_tanda_fija() -> None:
    """Tres bolas con cifras conocidas (limpia, lenta y rápida) y siete normales."""
    clean = (30, PACHINKO_CLEAN_BOUNCES, 0)
    slow = (PACHINKO_SLOW_FRAMES + 1, 12, 0)
    swift = (PACHINKO_SWIFT_FRAMES - 1, 9, 0)
    motion = fake_motion(clean, slow, swift, *[(30, 11, 0)] * 7)
    delta = stats_of(BLANK, motion)
    assert delta.add["pachinko_bounces"] == PACHINKO_CLEAN_BOUNCES + 12 + 9 + 77
    assert delta.peak["pachinko_bounce_volley_max"] == delta.add["pachinko_bounces"]
    assert delta.peak["pachinko_bounce_max"] == 12
    assert delta.add["pachinko_slow_balls"] == 1
    assert delta.add["pachinko_swift_balls"] == 1
    assert delta.add["pachinko_clean_balls"] == 1


def test_las_cifras_de_la_duracion_se_cuentan_en_su_limite() -> None:
    motion = fake_motion(
        (PACHINKO_SLOW_FRAMES, 11, 0),
        (PACHINKO_SLOW_FRAMES - 1, 11, 0),
        (PACHINKO_SWIFT_FRAMES, 11, 0),
        (PACHINKO_SWIFT_FRAMES + 1, 11, 0),
        (30, PACHINKO_CLEAN_BOUNCES + 1, 0),
    )
    delta = stats_of(BLANK, motion)
    assert delta.add["pachinko_slow_balls"] == 1
    assert delta.add["pachinko_swift_balls"] == 1
    assert "pachinko_clean_balls" not in delta.add


def test_estadisticas_de_choques_de_una_tanda_fija() -> None:
    """Seis bolas chocan (cuatro choques) y dos de ellas acaban en las esquinas de Sakura."""
    rows = SAKURA.rows
    falls = [
        (30, 11, 2), (30, 11, 2), (30, 11, 1), (30, 11, 1), (30, 11, 0), (30, 11, 0),
        (30, 11, 1, 0), (30, 11, 1, rows), (30, 11, 0, 0, 6), (30, 11, 0, 3, 3),
    ]  # fmt: skip
    delta = stats_of(blank(SAKURA), fake_motion(*falls, pocket=3))
    assert delta.add["pachinko_hits"] == 4  # cada choque cuenta una vez aunque lo sufran dos
    assert delta.peak["pachinko_hits_max"] == 4
    assert delta.peak["pachinko_hit_ball_max"] == 2
    assert delta.peak["pachinko_balls_hit_max"] == 6
    assert delta.add["pachinko_hit_corner"] == 2  # la de la esquina sin choque no cuenta
    assert delta.add["pachinko_delayed"] == 2
    assert "pachinko_no_hits" not in delta.add


def test_una_tanda_sin_choques_cuenta_como_tranquila() -> None:
    delta = stats_of(BLANK, fake_motion(*[(30, 11, 0)] * 10))
    assert delta.add["pachinko_no_hits"] == 1
    assert "pachinko_hits" not in delta.add
    assert "pachinko_hit_corner" not in delta.add
    assert delta.peak["pachinko_hits_max"] == 0


def test_las_estadisticas_salen_del_movimiento_real_con_choques() -> None:
    """Con la tanda de choques de `test_pachinko_motion`, los contadores son los del movimiento."""
    volley = build_volley(
        CLASSIC,
        [
            Ball((0, 1, 1, 0, 0, 0, 0, 0, 0, 0), 12),
            Ball((0, 1, 1, 1, 1, 1, 0, 1, 0, 1), 7),
            Ball((0, 1, 1, 1, 1, 0, 1, 0, 0, 1), 19),
            Ball((1, 0, 0, 1, 0, 0, 1, 0, 0, 0), 17),
            Ball((0, 1, 0, 0, 1, 1, 1, 1, 1, 0), 5),
            Ball((0, 1, 1, 1, 0, 1, 0, 1, 1, 0), 16),
            Ball((0, 1, 0, 0, 1, 1, 0, 1, 0, 1), 8),
            Ball((1, 1, 1, 0, 1, 0, 0, 0, 0, 0), 9),
            Ball((0, 0, 1, 1, 0, 1, 0, 0, 1, 1), 13),
            Ball((1, 0, 0, 1, 0, 0, 0, 0, 1, 0), 16),
        ],
        lambda: MISS,
    )
    motion = motion_for(volley)
    delta = stats_of(volley, motion)
    assert motion.collisions > 0
    assert delta.add["pachinko_bounces"] == motion.bounces
    assert delta.add["pachinko_hits"] == motion.collisions
    assert delta.peak["pachinko_hits_max"] == motion.collisions
    assert delta.peak["pachinko_hit_ball_max"] == max(b.collisions for b in motion.balls)
    assert "pachinko_clean_volleys" not in delta.add


def test_los_rebotes_no_dependen_de_lo_que_paga_la_tanda() -> None:
    """La caída solo se ve: con las mismas bolas, dos sorteos cuentan los mismos rebotes."""
    balls = [Ball((1,) * 5 + (0,) * 5, index) for index in range(10)]
    when = datetime(2026, 1, 1, 12, tzinfo=TIMEZONE)
    miss_volley = build_volley(CLASSIC, balls, lambda: MISS)
    rush_volley = build_volley(CLASSIC, balls, lambda: RUSH.draws[0])
    miss = pachinko_stats(
        miss_volley, motion=motion_for(miss_volley),
        stake=100, won=0, turbo=False, session_volleys=1, when=when,
    )  # fmt: skip
    rush = pachinko_stats(
        rush_volley, motion=motion_for(rush_volley),
        stake=100, won=5_000, turbo=False, session_volleys=1, when=when,
    )  # fmt: skip
    assert miss.add["pachinko_bounces"] == rush.add["pachinko_bounces"]
    assert miss.peak["pachinko_bounce_max"] == rush.peak["pachinko_bounce_max"]
