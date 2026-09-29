"""Pruebas de los avisos de bienvenida y despedida."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import welcome
from bot.cogs.welcome import Welcome, load_farewell_insults


def make_member() -> tuple[SimpleNamespace, MagicMock]:
    """Crea un miembro de prueba y el canal #chat-general de su servidor."""
    channel = MagicMock()
    channel.id = 123
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = SimpleNamespace(id=456, text_channels=[channel])
    member = SimpleNamespace(
        id=789,
        guild=guild,
        mention="<@789>",
        display_name="Miembro de prueba",
    )
    return member, channel


@pytest.mark.asyncio
async def test_member_join_envia_mensaje_video_y_mencion_controlada(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La bienvenida adjunta el vídeo y solo menciona al nuevo miembro."""
    member, channel = make_member()
    video_path = tmp_path / "bienvenida.mp4"
    video_path.write_bytes(b"video de prueba")
    monkeypatch.setattr(welcome, "WELCOME_VIDEO_PATH", video_path)
    cog = Welcome(MagicMock())

    await cog.on_member_join(member)

    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs["content"] == f"{member.mention} **¿QUIÉN ERES?**"
    assert kwargs["file"].filename == "bienvenida.mp4"
    assert kwargs["allowed_mentions"].to_dict() == {
        "parse": [],
        "users": [member.id],
    }


@pytest.mark.asyncio
async def test_member_join_sin_video_no_envia_mensaje(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si falta el vídeo, no se envía una bienvenida incompleta."""
    member, channel = make_member()
    missing_video = tmp_path / "missing-video.mp4"
    monkeypatch.setattr(welcome, "WELCOME_VIDEO_PATH", missing_video)
    cog = Welcome(MagicMock())

    await cog.on_member_join(member)

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_remove_envia_insulto_aleatorio_sin_menciones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La despedida usa una frase del archivo editable y no activa menciones."""
    member, channel = make_member()
    member.display_name = "@everyone Miembro"
    insults_path = tmp_path / "despedidas.txt"
    insults_path.write_text(
        "# comentario ignorado\n\nprimera frase de prueba.\nsegunda frase de prueba.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(welcome, "FAREWELL_INSULTS_PATH", insults_path)
    monkeypatch.setattr(welcome.random, "choice", lambda choices: choices[0])
    cog = Welcome(MagicMock())

    await cog.on_member_remove(member)

    channel.send.assert_awaited_once()
    args, kwargs = channel.send.await_args
    assert "primera frase de prueba." in args[0]
    assert "@everyone" in args[0]
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


def test_load_farewell_insults_usa_reserva_si_falta_el_archivo(tmp_path: Path) -> None:
    """Si el archivo de frases no existe, se usa la lista de reserva."""
    missing_path = tmp_path / "no-existe.txt"

    insults = load_farewell_insults(missing_path)

    assert insults == welcome.FALLBACK_FAREWELL_INSULTS


def test_load_farewell_insults_usa_reserva_si_el_archivo_esta_vacio(tmp_path: Path) -> None:
    """Un archivo solo con comentarios o líneas vacías no deja la lista sin frases."""
    empty_path = tmp_path / "vacio.txt"
    empty_path.write_text("# solo comentarios\n\n   \n", encoding="utf-8")

    insults = load_farewell_insults(empty_path)

    assert insults == welcome.FALLBACK_FAREWELL_INSULTS


@pytest.mark.asyncio
async def test_evento_sin_chat_general_no_intenta_enviar(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """La ausencia del canal se registra y no provoca un error de evento."""
    member, _ = make_member()
    member.guild.text_channels = []
    cog = Welcome(MagicMock())

    await cog.on_member_remove(member)

    assert "No existe el canal #chat-general" in caplog.text
