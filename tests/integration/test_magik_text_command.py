"""Integración de `ºmagik` con objetos reales de discord.py.

Se construyen un servidor, un canal y un mensaje reales (con un adjunto) y se
procesan con `bot.get_context` + `bot.invoke`, igual que lo haría el gateway.
Solo se sustituye la capa de red (`bot.http`). Así se comprueba todo el camino
real: prefijo `º`, conversión de argumentos, lectura del adjunto, procesado,
envío del archivo y gestión de errores; justo lo que los mocks no ven.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

from bot.app import INITIAL_EXTENSIONS, BotClient

BOT_USER = {"id": "999", "username": "bot", "discriminator": "0", "avatar": None, "bot": True}


def png_bytes() -> bytes:
    """PNG pequeño y válido."""
    buffer = io.BytesIO()
    Image.new("RGB", (200, 150), (30, 140, 220)).save(buffer, format="PNG")
    return buffer.getvalue()


def bot_message(message_id: str, content: str) -> dict:
    """Respuesta de la API para un mensaje enviado por el bot."""
    return {
        "id": message_id,
        "channel_id": "2",
        "guild_id": "1",
        "author": BOT_USER,
        "content": content,
        "attachments": [],
        "embeds": [],
        "pinned": False,
        "mention_everyone": False,
        "tts": False,
        "type": 0,
        "edited_timestamp": None,
        "timestamp": "2026-10-01T20:00:00+00:00",
        "mentions": [],
        "mention_roles": [],
        "flags": 0,
    }


class Harness:
    """Bot real con la red sustituida y un canal de servidor real."""

    def __init__(self, tmp_path: Path) -> None:
        self.bot = BotClient(command_prefix="!", database_path=tmp_path / "stats.sqlite3")
        self.sent: list[dict] = []
        self.deleted: list[int] = []
        self.channel: discord.TextChannel | None = None
        self.forbid_files = False

    async def start(self) -> None:
        bot = self.bot
        await bot._async_setup_hook()
        for extension in INITIAL_EXTENSIONS:
            await bot.load_extension(extension)

        state = bot._connection
        state.user = discord.ClientUser(state=state, data=BOT_USER)
        guild = discord.Guild(
            state=state,
            data={
                "id": "1",
                "name": "servidor",
                "roles": [],
                "channels": [],
                "members": [],
                "emojis": [],
                "stickers": [],
                "features": [],
                "member_count": 2,
                "unavailable": False,
                "owner_id": "5",
            },
        )
        state._add_guild(guild)
        self.channel = discord.TextChannel(
            state=state,
            guild=guild,
            data={
                "id": "2",
                "type": 0,
                "name": "general",
                "position": 0,
                "permission_overwrites": [],
                "nsfw": False,
                "parent_id": None,
            },
        )
        guild._add_channel(self.channel)

        async def send_message(channel_id: int, *, params: object) -> dict:
            if self.forbid_files and params.files:
                raise discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "permisos")
            content = (params.payload or {}).get("content", "")
            self.sent.append(
                {"content": content, "files": [f.filename for f in params.files or []]}
            )
            return bot_message("50", content)

        async def edit_message(channel_id: int, message_id: int, *, params: object) -> dict:
            content = (params.payload or {}).get("content", "")
            self.sent.append({"content": content, "files": [], "edited": True})
            return bot_message(str(message_id), content)

        async def delete_message(channel_id: int, message_id: int, **_: object) -> None:
            self.deleted.append(message_id)

        bot.http.send_message = send_message
        bot.http.edit_message = edit_message
        bot.http.delete_message = delete_message
        bot.http.get_from_cdn = AsyncMock(return_value=png_bytes())

    async def say(self, content: str, *, attach_image: bool = True) -> None:
        """El usuario 5 escribe `content` (con o sin imagen adjunta) y se procesa."""
        attachments = []
        if attach_image:
            attachments = [
                {
                    "id": "7",
                    "filename": "foto.png",
                    "size": 3000,
                    "url": "https://cdn.discordapp.com/x/foto.png",
                    "proxy_url": "https://media.discordapp.net/x/foto.png",
                    "content_type": "image/png",
                    "width": 200,
                    "height": 150,
                }
            ]
        message = discord.Message(
            state=self.bot._connection,
            channel=self.channel,
            data={
                "id": "100",
                "channel_id": "2",
                "guild_id": "1",
                "author": {"id": "5", "username": "user", "discriminator": "0", "avatar": None},
                "member": {
                    "roles": [],
                    "joined_at": "2026-01-01T00:00:00+00:00",
                    "deaf": False,
                    "mute": False,
                    "flags": 0,
                },
                "content": content,
                "tts": False,
                "mention_everyone": False,
                "mentions": [],
                "mention_roles": [],
                "pinned": False,
                "type": 0,
                "flags": 0,
                "embeds": [],
                "edited_timestamp": None,
                "timestamp": "2026-10-01T20:00:00+00:00",
                "attachments": attachments,
            },
        )
        ctx = await self.bot.get_context(message)
        await self.bot.invoke(ctx)
        # Deja correr los listeners (p. ej. el gestor de errores).
        await asyncio.sleep(0.05)

    async def close(self) -> None:
        await self.bot.close()


@pytest.fixture
async def harness(tmp_path: Path):
    """Bot de prueba listo para recibir mensajes."""
    h = Harness(tmp_path)
    await h.start()
    yield h
    await h.close()


@pytest.mark.asyncio
async def test_magik_con_imagen_adjunta_envia_el_png_y_borra_el_progreso(harness: Harness) -> None:
    """`ºmagik` + imagen: aviso de progreso, mensaje nuevo con `magik.png` y aviso borrado."""
    await harness.say("ºmagik")

    assert harness.sent[0]["content"] == "🌀 Distorsionando..."
    assert harness.sent[1]["files"] == ["magik.png"]
    assert harness.deleted == [50]


@pytest.mark.asyncio
async def test_magik_sin_adjunto_usa_el_avatar_y_tambien_responde(harness: Harness) -> None:
    """Sin imagen adjunta se cae al avatar de quien escribe (descargado por la CDN)."""
    await harness.say("ºmagik", attach_image=False)

    assert harness.sent[-1]["files"] == ["magik.png"]


@pytest.mark.asyncio
async def test_magik_sin_permiso_para_adjuntar_explica_que_activar(harness: Harness) -> None:
    """Si el bot no puede adjuntar archivos, lo dice en vez de quedarse en «Distorsionando…»."""
    harness.forbid_files = True

    await harness.say("ºmagik")

    assert "Adjuntar archivos" in harness.sent[-1]["content"]


@pytest.mark.asyncio
async def test_un_comando_de_texto_sin_argumentos_obligatorios_responde(harness: Harness) -> None:
    """`ºplay` sin nada ya no se ignora: explica qué falta."""
    await harness.say("ºplay", attach_image=False)

    assert "consulta" in harness.sent[-1]["content"]


@pytest.mark.asyncio
async def test_un_mensaje_con_prefijo_pero_sin_comando_se_ignora(harness: Harness) -> None:
    """`ºloquesea` no existe: el bot no contesta nada."""
    await harness.say("ºloquesea", attach_image=False)

    assert harness.sent == []
