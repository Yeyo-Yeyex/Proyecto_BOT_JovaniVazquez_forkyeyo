"""Pruebas del comando `reinicio`: el buzón (bot.services.deploy) y el cog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.deploy import DEPLOYERS, Deploy, result_message
from bot.services.deploy import (
    REQUEST_FILE,
    RESULT_FILE,
    RUNNING_FILE,
    STALE_AFTER,
    DeployRequest,
    DeployResult,
    Mailbox,
)
from bot.utils.responder import CommandResponder

NOW = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)
YEYO = 403646452414545921
DANI = 498473711687434241


class FakeResponder(CommandResponder):
    """Responder que registra cada envío, sin tocar Discord."""

    def __init__(self, user_id: int | None, channel_id: int = 555) -> None:
        self.guild = SimpleNamespace(id=1)
        self.member = SimpleNamespace(id=user_id) if user_id is not None else None
        self.channel = SimpleNamespace(id=channel_id)
        self.sent: list[str] = []
        self.errors: list[str] = []

    async def send(self, content=None, **kwargs) -> None:  # noqa: ANN001, ANN003
        self.sent.append(content)

    async def send_error(self, content: str) -> None:
        self.errors.append(content)

    async def start_progress(self, placeholder: str = "", **kwargs) -> None:  # noqa: ANN003
        raise AssertionError("reinicio no usa progreso")

    async def update_progress(self, content: str) -> None:
        raise AssertionError("reinicio no usa progreso")

    async def finish(self, content=None, **kwargs) -> None:  # noqa: ANN001, ANN003
        raise AssertionError("reinicio no usa progreso")


def make_cog(tmp_path: Path, bot: object | None = None) -> Deploy:
    return Deploy(bot or MagicMock(), Mailbox(tmp_path / "buzon"), clock=lambda: NOW)


# -- Buzón -------------------------------------------------------------------------------


def test_los_que_pueden_reiniciar_son_yeyo_y_dani() -> None:
    assert DEPLOYERS == {YEYO, DANI}


def test_la_peticion_queda_escrita_para_el_nas(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path / "buzon")
    mailbox.request(DeployRequest(555, YEYO, NOW))

    written = DeployRequest.from_json((tmp_path / "buzon" / REQUEST_FILE).read_text())
    assert written == DeployRequest(555, YEYO, NOW)
    assert mailbox.pending(NOW) == written
    assert not (tmp_path / "buzon" / f"{REQUEST_FILE}.tmp").exists()


def test_la_peticion_en_curso_tambien_cuenta_como_pendiente(tmp_path: Path) -> None:
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / RUNNING_FILE).write_text(DeployRequest(555, DANI, NOW).to_json())

    assert Mailbox(buzon).pending(NOW + timedelta(minutes=5)).user_id == DANI


def test_una_peticion_caducada_se_descarta(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path / "buzon")
    mailbox.request(DeployRequest(555, YEYO, NOW))

    assert mailbox.pending(NOW + STALE_AFTER + timedelta(seconds=1)) is None
    assert not (tmp_path / "buzon" / REQUEST_FILE).exists()


def test_una_nota_estropeada_no_bloquea(tmp_path: Path) -> None:
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / REQUEST_FILE).write_text("{esto no es json")

    assert Mailbox(buzon).pending(NOW) is None


def test_recoger_el_resultado_vacia_el_buzon(tmp_path: Path) -> None:
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / RUNNING_FILE).write_text(DeployRequest(555, YEYO, NOW).to_json())
    (buzon / RESULT_FILE).write_text("ok\nDesplegado abc1234: Logros nuevos\n")
    mailbox = Mailbox(buzon)

    result = mailbox.take_result()

    expected = DeployResult(
        True, "Desplegado abc1234: Logros nuevos", DeployRequest(555, YEYO, NOW)
    )
    assert result == expected
    assert not any(buzon.iterdir())
    assert mailbox.take_result() is None


def test_un_resultado_de_error(tmp_path: Path) -> None:
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / RESULT_FILE).write_text("error\nLa imagen no se ha podido construir.\n")

    result = Mailbox(buzon).take_result()

    assert result is not None and not result.ok
    assert result.request is None
    assert result.summary == "La imagen no se ha podido construir."


# -- Cog -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solo_yeyo_y_dani_pueden_pedirlo(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder(user_id=12345)

    await cog.request_impl(responder)

    assert responder.errors and not responder.sent
    assert not (tmp_path / "buzon").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [YEYO, DANI])
async def test_pedirlo_deja_la_nota_y_confirma(tmp_path: Path, user_id: int) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder(user_id=user_id)

    await cog.request_impl(responder)

    assert not responder.errors
    assert "Pedido" in responder.sent[0]
    assert Mailbox(tmp_path / "buzon").pending(NOW) == DeployRequest(555, user_id, NOW)


@pytest.mark.asyncio
async def test_no_se_pide_dos_veces(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    await cog.request_impl(FakeResponder(user_id=YEYO))
    second = FakeResponder(user_id=DANI)

    await cog.request_impl(second)

    assert "Ya hay un reinicio pedido" in second.errors[0]
    assert Mailbox(tmp_path / "buzon").pending(NOW).user_id == YEYO


@pytest.mark.asyncio
async def test_si_no_puede_escribir_lo_explica(tmp_path: Path) -> None:
    (tmp_path / "buzon").write_text("un archivo donde debería haber una carpeta")
    cog = make_cog(tmp_path)
    responder = FakeResponder(user_id=YEYO)

    await cog.request_impl(responder)

    assert "buzón" in responder.errors[0]


@pytest.mark.asyncio
async def test_publica_el_resultado_en_el_canal_de_la_peticion(tmp_path: Path) -> None:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    cog = make_cog(tmp_path, bot)
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / RUNNING_FILE).write_text(DeployRequest(555, YEYO, NOW).to_json())
    (buzon / RESULT_FILE).write_text("ok\nDesplegado abc1234: Pollo más rápido\n")

    assert await cog.announce() is True

    bot.get_channel.assert_called_once_with(555)
    text = channel.send.await_args.args[0]
    assert f"<@{YEYO}>" in text and "abc1234" in text
    assert await cog.announce() is False


def test_mensaje_de_error_dice_que_sigue_la_version_anterior() -> None:
    text = result_message(DeployResult(False, "Hay archivos tocados a mano.", None))

    assert text.startswith("❌") and "versión de antes" in text
