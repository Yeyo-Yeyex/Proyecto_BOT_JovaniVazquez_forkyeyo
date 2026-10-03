"""Pruebas de bot.cogs.casino: mesa de ruleta, `ruleta`, `saldo` y `imv`.

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
from bot.services.roulette import (
    DOUBLE_ZERO,
    OUTSIDE_BETS,
    RoundOutcome,
    Wager,
    Wheel,
    parse_bet,
)
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
    assert parse_command_args(None, None, 1000) == (100, [])


def test_ruleta_con_poco_saldo_baja_la_ficha_por_defecto() -> None:
    assert parse_command_args(None, None, 40) == (40, [])


def test_ruleta_con_cantidad_y_apuesta() -> None:
    stake, bets = parse_command_args("all", "rojo", 777)
    assert stake == 777
    assert bets == [OUTSIDE_BETS["red"]]


def test_ruleta_con_solo_una_apuesta_juega_con_la_ficha_por_defecto() -> None:
    stake, bets = parse_command_args("rojo", None, 1000)
    assert stake == 100
    assert bets == [OUTSIDE_BETS["red"]]


def test_ruleta_numero_solo_se_entiende_como_cantidad() -> None:
    """`.ruleta 17` es una ficha de 17, no un pleno: la cantidad va primero."""
    assert parse_command_args("17", None, 1000) == (17, [])


def test_ruleta_con_argumento_incomprensible_da_ejemplos() -> None:
    with pytest.raises(ValueError, match="Ejemplos"):
        parse_command_args("azul", None, 1000)


# -- Presentación -----------------------------------------------------------------


def test_texto_de_pleno_ganado_es_de_gran_premio() -> None:
    outcome = RoundOutcome(17, (Wager(parse_bet("17"), 100),), (3600,))

    text = result_text(outcome, random.Random(0))

    assert "17" in text
    assert "💥" in text
    assert "3.500 Y$" in text


def test_texto_de_apuesta_perdida_muestra_lo_perdido() -> None:
    outcome = RoundOutcome(DOUBLE_ZERO, (Wager(OUTSIDE_BETS["red"], 250),), (0,))

    text = result_text(outcome, random.Random(0))

    assert "00" in text
    assert "-250 Y$" in text


def test_mesa_a_cero_sugiere_imv() -> None:
    embed = table_embed(owner="Diego", balance=0, stake=100, history=[])
    assert "imv" in (embed.description or "")
    assert embed.image.url == f"attachment://{PNG_NAME}"


def test_mesa_muestra_historial_con_colores() -> None:
    embed = table_embed(owner="Diego", balance=10, stake=10, history=[1, 2, DOUBLE_ZERO])
    assert "🔴1 · ⚫2 · 🟢00" in (embed.footer.text or "")


# -- Mesa -------------------------------------------------------------------------


async def test_apostar_cobra_gira_y_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=17)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    interaction = make_interaction()

    await table.choose(interaction, parse_bet("17"))

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

    await table.choose(make_interaction(), OUTSIDE_BETS["red"])

    assert table.streak == 0
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100


async def test_sin_saldo_no_gira_y_avisa_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=STARTING_BALANCE + 1)
    interaction = make_interaction()

    await table.choose(interaction, OUTSIDE_BETS["red"])

    interaction.response.edit_message.assert_not_awaited()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_clic_mientras_gira_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    table._busy = True
    interaction = make_interaction()

    await table.choose(interaction, OUTSIDE_BETS["red"])

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
    await table.choose(make_interaction(), OUTSIDE_BETS["red"])

    await table._double_and_repeat(make_interaction())

    assert table.stake == 200
    # Gana 100 y luego 200 al rojo (el 1 es rojo).
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE + 300


async def test_doblar_sin_saldo_suficiente_no_cambia_la_ficha(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=1)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=STARTING_BALANCE)
    table.last_wagers = (Wager(OUTSIDE_BETS["black"], STARTING_BALANCE),)
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


# -- Varias apuestas ----------------------------------------------------------------


def test_ruleta_con_varias_apuestas_por_texto() -> None:
    stake, bets = parse_command_args("50", "rojo + 17 + d2", 1000)
    assert stake == 50
    assert [b.key for b in bets] == ["red", "in:17", "dozen2"]


def test_ruleta_all_con_varias_apuestas_reparte_el_saldo() -> None:
    stake, bets = parse_command_args("all", "rojo + negro + 0", 1000)
    assert stake == 333
    assert len(bets) == 3


def test_ruleta_varias_apuestas_sin_cantidad() -> None:
    stake, bets = parse_command_args("rojo", "+ 17", 1000)
    assert stake == 100
    assert [b.key for b in bets] == ["red", "in:17"]


def test_texto_de_varias_apuestas_marca_cada_una() -> None:
    outcome = RoundOutcome(
        1,
        (Wager(OUTSIDE_BETS["red"], 100), Wager(parse_bet("17"), 50)),
        (200, 0),
    )

    text = result_text(outcome, random.Random(0))

    assert "✅ 🔴 Rojo · 100 Y$ → +100 Y$" in text
    assert "❌ Pleno 17 · 50 Y$" in text
    assert "+50 Y$" in text.splitlines()[1]


def test_texto_de_acierto_parcial_dice_cuanto_recuperas() -> None:
    outcome = RoundOutcome(
        1,
        (Wager(OUTSIDE_BETS["red"], 10), Wager(parse_bet("17"), 100)),
        (20, 0),
    )

    text = result_text(outcome, random.Random(0))

    assert "-90 Y$" in text
    assert "Recuperas 20 Y$ de 110 Y$" in text


async def test_modo_varias_pone_fichas_sin_cobrar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    await table._toggle_mode(make_interaction())

    await table.choose(make_interaction(), OUTSIDE_BETS["red"])
    await table.choose(make_interaction(), parse_bet("1"))

    assert [(w.bet.key, w.stake) for w in table.slip] == [("red", 100), ("in:1", 100)]
    assert not table.spin_button.disabled
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_girar_juega_todas_las_fichas_en_una_tirada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=1)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    table.multi = True
    await table.choose(make_interaction(), OUTSIDE_BETS["red"])
    await table.choose(make_interaction(), parse_bet("17"))
    interaction = make_interaction()

    await table._spin_slip(interaction)

    assert attachment_names(interaction.response.edit_message.await_args) == [GIF_NAME]
    # Rojo 100 gana +100; pleno 17 pierde 100: se queda igual.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    assert table.slip == ()
    assert len(table.last_wagers) == 2
    assert cog.history(GUILD_ID) == [1]


async def test_no_se_pueden_poner_mas_fichas_que_saldo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=600)
    table.multi = True
    await table.choose(make_interaction(), OUTSIDE_BETS["red"])
    interaction = make_interaction()

    await table.choose(interaction, OUTSIDE_BETS["black"])

    assert len(table.slip) == 1
    assert "Necesitas 1.200 Y$" in interaction.response.send_message.await_args.args[0]


async def test_all_in_en_modo_varias_usa_lo_que_queda_libre(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=300)
    table.multi = True
    await table.choose(make_interaction(), OUTSIDE_BETS["red"])

    await table._all_in(make_interaction())

    assert table.stake == STARTING_BALANCE - 300


async def test_doblar_dobla_todas_las_apuestas_de_la_ultima_tirada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, pocket=1)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    table.multi = True
    await table.choose(make_interaction(), OUTSIDE_BETS["red"])
    await table.choose(make_interaction(), OUTSIDE_BETS["odd"])
    await table._spin_slip(make_interaction())

    await table._double_and_repeat(make_interaction())

    assert [w.stake for w in table.last_wagers] == [200, 200]
    # Primera tirada: +200. Segunda (doble): +400.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE + 600


async def test_cambiar_de_modo_quita_las_fichas_puestas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    table = RouletteTable(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)
    table.multi = True
    await table.choose(make_interaction(), OUTSIDE_BETS["red"])

    await table._toggle_mode(make_interaction())

    assert not table.multi
    assert table.slip == ()
    assert table.spin_button.disabled


async def test_comando_con_varias_apuestas_gira_y_deja_la_mesa_en_modo_varias(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, pocket=1)

    send, send_error, _ = await run_command(cog, channel_id=1, amount="100", bet="rojo + impar")

    send_error.assert_not_awaited()
    table = send.await_args.kwargs["view"]
    assert table.multi
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE + 200


async def test_comando_con_varias_apuestas_sin_saldo_avisa_del_total(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)

    send, send_error, _ = await run_command(cog, channel_id=1, amount="600", bet="rojo + negro")

    send.assert_not_awaited()
    assert "1.200 Y$" in send_error.await_args.args[0]


async def test_imv_avisa_de_que_esta_exento(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = RecordingResponder()
    user = make_user()
    user.display_avatar.url = "https://example.invalid/a.png"

    await cog._daily_impl(responder, user)  # type: ignore[arg-type]

    assert "exento" in responder.sent[0]["embed"].description


async def test_hacienda_muestra_la_cuenta_del_estado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.economy.pay_income(1, 10, gross=30_000, concept="nivel:20")
    responder = RecordingResponder()
    responder.guild = SimpleNamespace(id=1, get_member=lambda _id: None)

    await cog._hacienda_impl(responder)  # type: ignore[arg-type]

    embed = responder.sent[0]["embed"]
    assert embed.title == "🏛️ Hacienda"
    assert "Quién más ha pagado" in embed.fields[0].name
    assert "<@10>" in embed.fields[0].value
