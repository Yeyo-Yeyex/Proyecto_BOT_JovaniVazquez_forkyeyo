"""Pruebas de bot.cogs.entrance: comando `/entrada` y disparo al entrar a voz."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.entrance import Entrance, parse_text_option
from bot.repositories.entrance_sounds import EntranceSoundStore
from bot.services.entrance_sound import EntranceSoundError

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg no está instalado"
)


class FakeResponder:
    """Responder que guarda lo que el cog intenta enviar."""

    def __init__(self, *, user_id: int = 7) -> None:
        self.guild = SimpleNamespace(id=10)
        self.member = MagicMock(spec=discord.Member)
        self.member.id = user_id
        self.channel = MagicMock()
        self.sent: list[tuple[str | None, dict[str, object]]] = []
        self.errors: list[str] = []
        self.finished: list[dict[str, object]] = []
        self.progress_ephemeral: list[bool] = []

    async def send(self, content=None, **kwargs) -> None:  # noqa: ANN001
        self.sent.append((content, kwargs))

    async def send_error(self, content: str) -> None:
        self.errors.append(content)

    async def start_progress(self, placeholder: str = "", *, ephemeral: bool = False) -> None:
        self.progress_ephemeral.append(ephemeral)

    async def finish(self, content=None, **kwargs) -> None:  # noqa: ANN001
        self.finished.append({"content": content, **kwargs})


def tone_bytes(tmp_path: Path, seconds: float) -> bytes:
    """WAV sintético de la duración indicada."""
    path = tmp_path / f"tono-{seconds}.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    return path.read_bytes()


def make_attachment(data: bytes, *, content_type: str | None = "audio/wav") -> MagicMock:
    """Adjunto de Discord simulado con el contenido dado."""
    attachment = MagicMock(spec=discord.Attachment)
    attachment.content_type = content_type
    attachment.size = len(data)
    attachment.read = AsyncMock(return_value=data)
    return attachment


def make_cog(tmp_path: Path) -> Entrance:
    """Cog con un almacén en una carpeta temporal y un bot simulado."""
    bot = MagicMock()
    bot.is_closed = MagicMock(return_value=False)
    return Entrance(bot, EntranceSoundStore(tmp_path / "entradas"))


def test_parse_text_option_entiende_volumen_borrar_y_nada() -> None:
    assert parse_text_option(None) == (None, False)
    assert parse_text_option("150%") == (150, False)
    assert parse_text_option("BORRAR") == (None, True)
    with pytest.raises(EntranceSoundError):
        parse_text_option("fuerte")
    with pytest.raises(EntranceSoundError):
        parse_text_option("500")


@requires_ffmpeg
@pytest.mark.asyncio
async def test_subir_un_audio_valido_lo_guarda_y_adjunta_la_vista_previa(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(responder, make_attachment(tone_bytes(tmp_path, 2.0)), 150, False)

    assert responder.errors == []
    assert responder.progress_ephemeral == [True]
    assert "volumen 150 %" in responder.finished[-1]["content"]
    assert isinstance(responder.finished[-1]["file"], discord.File)
    assert await cog.store.has_sound(10, 7)
    assert await cog.store.read_volume(10, 7, 100) == 150


@requires_ffmpeg
@pytest.mark.asyncio
async def test_un_audio_de_mas_de_3_segundos_se_rechaza_sin_guardar_nada(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(responder, make_attachment(tone_bytes(tmp_path, 4.0)), None, False)

    assert "máximo es 3 s" in responder.finished[-1]["content"]
    assert not await cog.store.has_sound(10, 7)


@requires_ffmpeg
@pytest.mark.asyncio
async def test_cambiar_el_volumen_regenera_el_clip_sin_volver_a_subirlo(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    await cog._entrada_impl(
        FakeResponder(), make_attachment(tone_bytes(tmp_path, 1.0)), None, False
    )
    before = await cog.store.read_clip(10, 7)

    responder = FakeResponder()
    await cog._entrada_impl(responder, None, 40, False)

    assert await cog.store.read_volume(10, 7, 100) == 40
    assert await cog.store.read_clip(10, 7) != before
    assert "volumen 40 %" in responder.finished[-1]["content"]


@pytest.mark.asyncio
async def test_cambiar_el_volumen_sin_sonido_explica_que_hay_que_subir_uno(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(responder, None, 120, False)

    assert "Sube primero" in responder.errors[0]


@pytest.mark.asyncio
async def test_una_imagen_se_rechaza_antes_de_descargarla(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder()
    attachment = make_attachment(b"png", content_type="image/png")

    await cog._entrada_impl(responder, attachment, None, False)

    assert responder.errors == ["Ese archivo no parece un audio."]
    attachment.read.assert_not_called()


@pytest.mark.asyncio
async def test_borrar_sin_sonido_lo_dice_y_es_efimero(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(responder, None, None, True)

    assert responder.sent == [("No tenías sonido de entrada.", {"ephemeral": True})]


def test_el_enfriamiento_bloquea_repeticiones_de_la_misma_persona(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    assert cog._cooldown_allows(1, 2)
    assert not cog._cooldown_allows(1, 2)
    assert cog._cooldown_allows(1, 3)


def voice_event_member(*, bot: bool = False) -> MagicMock:
    """Miembro de un servidor sin canal AFK."""
    member = MagicMock(spec=discord.Member)
    member.id = 7
    member.bot = bot
    member.guild = SimpleNamespace(id=10, afk_channel=None)
    return member


@pytest.mark.asyncio
async def test_silenciarse_dentro_del_mismo_canal_no_dispara_el_sonido(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    cog.store.has_sound = AsyncMock(return_value=True)
    channel = SimpleNamespace(id=99)
    state = SimpleNamespace(channel=channel)

    await cog.on_voice_state_update(voice_event_member(), state, state)

    cog.store.has_sound.assert_not_called()
    assert cog._pending == {}


@pytest.mark.asyncio
async def test_entrar_a_un_canal_encola_y_arranca_el_reproductor(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    cog.store.has_sound = AsyncMock(return_value=True)
    cog._ensure_worker = MagicMock()
    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=SimpleNamespace(id=99))

    await cog.on_voice_state_update(voice_event_member(), before, after)

    assert list(cog._pending[10]) == [(7, 99)]
    cog._ensure_worker.assert_called_once()


@pytest.mark.asyncio
async def test_los_bots_no_tienen_sonido_de_entrada(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    cog.store.has_sound = AsyncMock(return_value=True)
    after = SimpleNamespace(channel=SimpleNamespace(id=99))

    await cog.on_voice_state_update(
        voice_event_member(bot=True), SimpleNamespace(channel=None), after
    )

    cog.store.has_sound.assert_not_called()


@pytest.mark.asyncio
async def test_si_la_musica_ocupa_la_voz_no_suena_ni_se_conecta(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    cog._join = AsyncMock()
    guild = MagicMock()
    guild.id = 10
    guild.voice_client = MagicMock()  # Conexión de la música.
    cog._pending[10] = deque([(7, 99)])

    await cog._drain(guild)

    cog._join.assert_not_called()
    assert not cog._pending[10]


@pytest.mark.asyncio
async def test_varios_sonidos_seguidos_reutilizan_la_conexion_y_salen_al_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = make_cog(tmp_path)
    channel = SimpleNamespace(id=99)
    guild = MagicMock()
    guild.id = 10
    guild.voice_client = None

    def member_in_channel(member_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=member_id, voice=SimpleNamespace(channel=channel))

    guild.get_member = member_in_channel
    cog._can_join = MagicMock(return_value=True)
    cog.store.read_clip = AsyncMock(return_value=b"clip")
    voice = MagicMock()
    voice.is_connected = MagicMock(return_value=True)
    voice.disconnect = AsyncMock()

    async def fake_join(_channel: object, current: object) -> MagicMock:
        guild.voice_client = voice
        return voice

    cog._join = AsyncMock(side_effect=fake_join)
    cog._play = AsyncMock()
    cog._pending[10] = deque([(1, 99), (2, 99)])

    # Se parchea en los globals reales del cog: las pruebas de integración
    # recargan el módulo con `load_extension`, así que importarlo aquí podría
    # devolver otra copia.
    monkeypatch.setitem(Entrance._drain.__globals__, "read_opus_packets", lambda data: [data])
    await cog._drain(guild)

    assert cog._play.await_count == 2
    assert cog._join.await_args_list[1].args[1] is voice  # Segunda vez reutiliza.
    voice.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_espera_al_callback_del_hilo_de_audio(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    voice = MagicMock()

    def fake_play(source: object, *, after) -> None:  # noqa: ANN001
        # discord.py llama a `after` desde otro hilo al terminar.
        asyncio.get_running_loop().run_in_executor(None, after, None)

    voice.play = fake_play
    await asyncio.wait_for(cog._play(voice, [b"x"] * 5), timeout=2)
    voice.stop.assert_not_called()
