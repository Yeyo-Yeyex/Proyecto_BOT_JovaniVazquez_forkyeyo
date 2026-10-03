"""Pruebas de bot.cogs.casino: mesa de ruleta, `ruleta`, `saldo` y `daily`.

Se usa la economía real sobre un SQLite temporal (para comprobar que el
dinero se mueve de verdad), una rueda trucada y un renderizador falso.
"""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot.cogs.casino as casino_module
from bot.cogs.casino import (
    GIF_NAME,
    PNG_NAME,
    Casino,
    RouletteTable,
    parse_command_args,
    result_text,
    table_embed,
)
from bot.repositories.economy import EconomyRepository
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.roulette import DOUBLE_ZERO, OUTSIDE_BETS, SpinOutcome, Wheel, parse_bet
from bot.services.roulette_render import SpinMedia

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555


class FakeRenderer:
    """Devuelve bytes fijos: las pruebas no necesitan dibujar la rueda."""

    def media(self, pocket: int) -> SpinMedia:
        return SpinMedia(gif=b"GIF", png=b"PNG")

    def idle_png(self) -> bytes:
        return b"IDLE"


@pytest.fixture(autouse=True)
def no_spin_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """La animación no hace falta esperarla en las pruebas."""
    monkeypatch.setattr(casino_module, "SPIN_SECONDS", 0)
    monkeypatch.setattr(casino_module, "REVEAL_MARGIN_SECONDS", 0)


async def make_cog(tmp_path: Path, pocket: int = 17, channels=frozenset()) -> Casino:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Casino(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        wheel=Wheel(lambda n: pocket),
        casino_channel_ids=channels,
    )


def make_user(user_id: int = OWNER_ID, name: str = "Diego") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
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
    return interaction


def attachment_names(call) -> list[str]:  # noqa: ANN001
    return [file.filename for file in call.kwargs["attachments"]]


# -- Argumentos -------------------------------------------------------------------


def test_ruleta_sin_argumentos_usa_la_ficha_por_defecto() -> None:
    assert parse_command_args(None, None, 1000) == (100, None)


def test_ruleta_con_poco_saldo_baja_la_ficha_por_defecto() -> None:
    assert parse_command_args(None, None, 40) == (40, None)


def test_ruleta_con_cantidad_y_apuesta() -> None:
    stake, bet = parse_command_args("all", "rojo", 777)
    assert stake == 777
    assert bet is OUTSIDE_BETS["red"]


def test_ruleta_con_solo_una_apuesta_juega_con_la_ficha_por_defecto() -> None:
    stake, bet = parse_command_args("rojo", None, 1000)
    assert stake == 100
    assert bet is OUTSIDE_BETS["red"]


def test_ruleta_numero_solo_se_entiende_como_cantidad() -> None:
    """`.ruleta 17` es una ficha de 17, no un pleno: la cantidad va primero."""
    assert parse_command_args("17", None, 1000) == (17, None)


def test_ruleta_con_argumento_incomprensible_da_ejemplos() -> None:
    with pytest.raises(ValueError, match="Ejemplos"):
        parse_command_args("azul", None, 1000)


# -- Presentación -----------------------------------------------------------------


def test_texto_de_pleno_ganado_es_de_gran_premio() -> None:
    outcome = SpinOutcome(17, parse_bet("17"), 100, 3600)

    text = result_text(outcome, random.Random(0))

    assert "17" in text
    assert "💥" in text
    assert "3.500 Y$" in text


def test_texto_de_apuesta_perdida_muestra_lo_perdido() -> None:
    outcome = SpinOutcome(DOUBLE_ZERO, OUTSIDE_BETS["red"], 250, 0)

    text = result_text(outcome, random.Random(0))

    assert "00" in text
    assert "-250 Y$" in text


def test_mesa_a_cero_sugiere_daily() -> None:
    embed = table_embed(owner="Diego", balance=0, stake=100, history=[])
    assert "daily" in (embed.description or "")
    assert embed.image.url == f"attachment://{PNG_NAME}"


def test_mesa_muestra_historial_con_colores() -> None:
    embed = table_embed(owner="Diego", balance=10, stake=10, history=[1, 2, DOUBLE_ZERO])
    assert "🔴1 · ⚫2 · 🟢00" in (embed.footer.text or "")


# -- Mesa -------------------------------------------------------------------------


async def test_apostar_cobra_gira_y_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=17)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    interaction = make_interaction()

    await table.play(interaction, parse_bet("17"))

    first = interaction.response.edit_message.await_args
    assert attachment_names(first) == [GIF_NAME]
    final = interaction.edit_original_response.await_args
    assert attachment_names(final) == [PNG_NAME]
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE + 3500
    assert cog.history(GUILD_ID) == [17]
    assert table.streak == 1
    assert not table.repeat_button.disabled


async def test_apuesta_perdida_reinicia_la_racha(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=0)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    table.streak = 3

    await table.play(make_interaction(), OUTSIDE_BETS["red"])

    assert table.streak == 0
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100


async def test_sin_saldo_no_gira_y_avisa_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=STARTING_BALANCE + 1)
    interaction = make_interaction()

    await table.play(interaction, OUTSIDE_BETS["red"])

    interaction.response.edit_message.assert_not_awaited()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_clic_mientras_gira_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    table._busy = True
    interaction = make_interaction()

    await table.play(interaction, OUTSIDE_BETS["red"])

    interaction.response.defer.assert_awaited_once()
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_otro_miembro_no_puede_jugar_en_tu_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    intruder = make_interaction(user_id=99)

    allowed = await table.interaction_check(intruder)

    assert not allowed
    assert intruder.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_all_in_pone_todo_el_saldo_como_ficha(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)

    await table._all_in(make_interaction())

    assert table.stake == STARTING_BALANCE


async def test_x2_no_pasa_del_saldo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=800)

    await table._double_stake(make_interaction())

    assert table.stake == STARTING_BALANCE


async def test_doblar_juega_la_ultima_apuesta_con_el_doble(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=1)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    await table.play(make_interaction(), OUTSIDE_BETS["red"])

    await table._double_and_repeat(make_interaction())

    assert table.stake == 200
    # Gana 100 y luego 200 al rojo (el 1 es rojo).
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE + 300


async def test_doblar_sin_saldo_suficiente_no_cambia_la_ficha(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=1)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=STARTING_BALANCE)
    table.last_bet = OUTSIDE_BETS["black"]
    interaction = make_interaction()

    await table._double_and_repeat(interaction)

    assert table.stake == STARTING_BALANCE
    interaction.response.edit_message.assert_not_awaited()


# -- Comando ----------------------------------------------------------------------


async def run_command(cog: Casino, *, channel_id: int, amount=None, bet=None):  # noqa: ANN001, ANN201
    message = MagicMock()
    message.edit = AsyncMock()
    send = AsyncMock(return_value=message)
    send_error = AsyncMock()
    await cog._ruleta_impl(
        guild=SimpleNamespace(id=GUILD_ID),  # type: ignore[arg-type]
        channel=SimpleNamespace(id=channel_id),
        user=make_user(),
        amount_text=amount,
        bet_text=bet,
        send=send,
        send_error=send_error,
    )
    return send, send_error, message


async def test_ruleta_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))

    send, send_error, _ = await run_command(cog, channel_id=123)

    send.assert_not_awaited()
    assert f"<#{CASINO_CHANNEL}>" in send_error.await_args.args[0]


async def test_ruleta_abre_la_mesa_con_la_rueda_en_reposo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))

    send, send_error, _ = await run_command(cog, channel_id=CASINO_CHANNEL, amount="250")

    send_error.assert_not_awaited()
    kwargs = send.await_args.kwargs
    assert kwargs["file"].filename == PNG_NAME
    assert isinstance(kwargs["view"], RouletteTable)
    assert kwargs["view"].stake == 250


async def test_ruleta_con_apuesta_gira_al_momento(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=17)

    send, _, message = await run_command(cog, channel_id=1, amount="all", bet="17")

    assert [f.filename for f in send.await_args.kwargs["files"]] == [GIF_NAME]
    assert attachment_names(message.edit.await_args) == [PNG_NAME]
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE * 36


async def test_ruleta_con_mas_de_lo_que_tienes_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)

    send, send_error, _ = await run_command(cog, channel_id=1, amount="5k")

    send.assert_not_awaited()
    assert "No te llega" in send_error.await_args.args[0]


async def test_salir_del_servidor_borra_su_economia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.economy.settle_bet(GUILD_ID, OWNER_ID, game="ruleta", stake=500, payout=0)
    cog.record(GUILD_ID, 5)

    await cog.on_guild_remove(SimpleNamespace(id=GUILD_ID))  # type: ignore[arg-type]

    assert cog.history(GUILD_ID) == []
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


class RecordingResponder:
    """Responder mínimo que guarda lo enviado."""

    def __init__(self) -> None:
        self.guild = SimpleNamespace(id=GUILD_ID)
        self.sent: list[dict] = []
        self.errors: list[str] = []

    async def send(self, content=None, **kwargs) -> None:  # noqa: ANN001
        self.sent.append({"content": content, **kwargs})

    async def send_error(self, content: str) -> None:
        self.errors.append(content)


async def test_saldo_abre_el_monedero_con_el_saldo_inicial(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = RecordingResponder()

    await cog._saldo_impl(responder, make_user(), None)  # type: ignore[arg-type]

    assert "1.000 Y$" in responder.sent[0]["embed"].description


async def test_daily_dos_veces_seguidas_solo_paga_una(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = RecordingResponder()
    user = make_user()
    user.display_avatar.url = "https://example.invalid/a.png"

    await cog._daily_impl(responder, user)  # type: ignore[arg-type]
    await cog._daily_impl(responder, user)  # type: ignore[arg-type]

    assert len(responder.sent) == 1
    assert "Ya cobraste" in responder.errors[0]
