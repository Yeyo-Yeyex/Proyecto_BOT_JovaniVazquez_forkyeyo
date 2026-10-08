"""Pruebas del comando `reinicio` y del aviso de novedades: el buzón
(bot.services.deploy) y el cog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import deploy as deploy_cog
from bot.cogs.deploy import (
    DEPLOYERS,
    NEWS_FIRST_STAT,
    NEWS_READ_STAT,
    Deploy,
    news_embed,
    result_message,
)
from bot.services.deploy import (
    NEWS_FILE,
    NEWS_LIMIT,
    REQUEST_FILE,
    RESULT_FILE,
    RUNNING_FILE,
    STALE_AFTER,
    DeployRequest,
    DeployResult,
    Mailbox,
    news_lines,
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


# -- Novedades ---------------------------------------------------------------------------


def test_recoger_las_novedades_las_borra(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path)
    (tmp_path / NEWS_FILE).write_text("Pollo más rápido\n\n  Caballos  \n")

    assert mailbox.take_news() == ["Pollo más rápido", "Caballos"]
    assert not (tmp_path / NEWS_FILE).exists()
    assert mailbox.take_news() is None


def test_un_archivo_de_novedades_vacio_no_se_publica(tmp_path: Path) -> None:
    (tmp_path / NEWS_FILE).write_text("\n")

    assert Mailbox(tmp_path).take_news() is None


def test_muchas_novedades_se_resumen_y_caben_en_un_embed() -> None:
    items = [f"PR {n} " + "x" * 500 for n in range(NEWS_LIMIT + 5)]

    lines = news_lines(items)

    assert len(lines) == NEWS_LIMIT + 1
    assert lines[-1] == "…y 5 más."
    assert len(news_embed(items).description or "") <= 4096


def _guild_with_channel(guild_id: int = 1) -> tuple[MagicMock, MagicMock]:
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.text_channels = [channel]
    return guild, channel


def _interaction(guild_id: int, user_id: int) -> MagicMock:
    interaction = fake_interaction(SimpleNamespace(id=user_id, bot=False))
    interaction.guild = SimpleNamespace(id=guild_id)
    return interaction


async def _publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Deploy, list, int]:
    """Cog con unas novedades ya publicadas; devuelve los logros apuntados y la edición."""
    guild, channel = _guild_with_channel()
    bot = MagicMock()
    bot.guilds = [guild]
    cog = make_cog(tmp_path, bot)
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / NEWS_FILE).write_text("Carreras de caballos\n")
    tracked: list = []

    async def fake_track(bot, guild_id, user, channel, delta) -> None:  # noqa: ANN001
        tracked.append((user.id, delta.add))

    monkeypatch.setattr(deploy_cog.logros, "track", fake_track)
    monkeypatch.setattr(deploy_cog.mascotas, "cameo", AsyncMock(return_value=None))
    monkeypatch.setattr(deploy_cog.renta, "remind", AsyncMock())

    assert await cog.announce_news() is True

    embed = channel.send.await_args.kwargs["embed"]
    assert "Carreras de caballos" in embed.description
    button = channel.send.await_args.kwargs["view"].children[0]
    edition = int(button.custom_id.rsplit(":", 1)[1])
    assert await cog.announce_news() is False
    return cog, tracked, edition


@pytest.mark.asyncio
async def test_leer_las_novedades_da_logros_y_el_primero_se_lleva_el_suyo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, tracked, edition = await _publish(tmp_path, monkeypatch)

    await cog.read_news(_interaction(1, YEYO), edition)
    await cog.read_news(_interaction(1, DANI), edition)

    assert tracked == [
        (YEYO, {NEWS_READ_STAT: 1, NEWS_FIRST_STAT: 1}),
        (DANI, {NEWS_READ_STAT: 1}),
    ]


@pytest.mark.asyncio
async def test_leerlas_dos_veces_no_cuenta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cog, tracked, edition = await _publish(tmp_path, monkeypatch)
    await cog.read_news(_interaction(1, YEYO), edition)
    again = _interaction(1, YEYO)

    await cog.read_news(again, edition)

    assert len(tracked) == 1
    assert "Ya te lo habías leído" in again.edit_original_response.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_un_aviso_viejo_ya_no_cuenta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cog, tracked, edition = await _publish(tmp_path, monkeypatch)
    old = _interaction(1, YEYO)

    await cog.read_news(old, edition - 1)

    assert tracked == []
    assert "derogadas" in old.edit_original_response.await_args.kwargs["content"]
