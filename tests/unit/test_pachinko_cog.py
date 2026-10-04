"""Pruebas de bot.cogs.pachinko: la máquina con botones, el dinero y la Ráfaga.

Se usa la economía real sobre un SQLite temporal (para comprobar que el
dinero se mueve de verdad y que el libro cuadra), tandas trucadas y un
renderizador falso.
"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot.cogs.pachinko as pachinko_module
from bot.cogs.pachinko import (
    BURST_VOLLEYS,
    GIF_NAME,
    PNG_NAME,
    Pachinko,
    PachinkoPlay,
    PachinkoView,
    burst_text,
    parse_stake,
    result_text,
)
from bot.repositories.economy import EconomyRepository
from bot.services.achievements import pachinko_stats
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.pachinko import (
    FEVER_BALLS,
    MIN_STAKE,
    ROWS,
    START_POCKET,
    Ball,
    Draw,
    Kind,
    PachinkoMachine,
    Volley,
    build_volley,
)
from bot.services.pachinko_render import PachinkoMedia

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555
MISS = Draw((1, 2, 3), Kind.MISS, False, 0)


def ball_in(pocket: int) -> Ball:
    return Ball((1,) * pocket + (0,) * (ROWS - pocket))


#: Diez bolas en OUT: no devuelve nada.
BLANK = build_volley([ball_in(3)] * 10, lambda: MISS)
#: Un rush de 4 premios gordos: 4 × 30 bolas.
RUSH = build_volley(
    [ball_in(START_POCKET)] + [ball_in(3)] * 9, lambda: Draw((5, 5, 5), Kind.RUSH, True, 4)
)
#: Devuelve algo, pero menos de lo apostado (3 bolas de 10).
SMALL = build_volley([ball_in(1)] + [ball_in(3)] * 9, lambda: MISS)


class FakeRenderer:
    """Devuelve bytes fijos: las pruebas no necesitan dibujar el tablero."""

    def render(self, volley, *, turbo: bool = False) -> PachinkoMedia:  # noqa: ANN001
        return PachinkoMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    def still_png(self, volley) -> bytes:  # noqa: ANN001
        return b"STILL"

    def idle_png(self) -> bytes:
        return b"IDLE"


class RiggedMachine(PachinkoMachine):
    """Lanza las tandas que se le digan, en orden; después, siempre `BLANK`."""

    def __init__(self, sequence: Iterable[Volley] = ()) -> None:
        super().__init__()
        self.sequence = list(sequence)

    def launch(self) -> Volley:
        return self.sequence.pop(0) if self.sequence else BLANK


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
    interaction = MagicMock()
    interaction.user = make_user(user_id)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.edit_original_response = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


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
    volley = build_volley([ball_in(START_POCKET)] * 6 + [ball_in(3)] * 4, lambda: MISS)
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
    assert attachment_names(interaction.response.edit_message.await_args) == [GIF_NAME]
    assert attachment_names(interaction.edit_original_response.await_args) == [PNG_NAME]


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

    assert attachment_names(interaction.response.edit_message.await_args) == [PNG_NAME]
    interaction.edit_original_response.assert_not_awaited()


async def test_el_turbo_se_recuerda_para_la_siguiente_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await make_view(cog)._toggle_turbo(make_interaction())
    assert make_view(cog).turbo


async def test_sin_saldo_no_lanza_y_avisa_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=STARTING_BALANCE + 10)
    interaction = make_interaction()

    await view._launch(interaction)

    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.edit_message.assert_not_awaited()
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
        [ball_in(START_POCKET)] + [ball_in(3)] * 9, lambda: Draw((3, 3, 3), Kind.RUSH, True, 5)
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


async def test_la_mitad_no_baja_del_minimo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=15)
    await view._halve(make_interaction())
    assert view.stake == MIN_STAKE


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


def test_estadisticas_de_una_tanda_en_blanco() -> None:
    delta = pachinko_stats(
        BLANK,
        stake=100,
        won=0,
        turbo=True,
        session_volleys=1,
        when=datetime(2026, 1, 1, 12, tzinfo=TIMEZONE),
    )
    assert delta.add["pachinko_blank"] == 1
    assert delta.add["pachinko_turbo"] == 1
    assert "pachinko_win_max" not in delta.peak
