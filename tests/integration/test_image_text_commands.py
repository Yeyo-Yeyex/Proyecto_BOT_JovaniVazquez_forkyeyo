"""Integración de los comandos de imagen (`.magik` y los efectos) con objetos reales de discord.py.

Se construyen un servidor, un canal y un mensaje reales (con un adjunto) y se
procesan con `bot.get_context` + `bot.invoke`, igual que lo haría el gateway.
Solo se sustituye la capa de red (`bot.http`). Así se comprueba todo el camino
real: prefijo `.`, conversión de argumentos, lectura del adjunto, procesado,
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
        self.bot = BotClient(command_prefix=".", database_path=tmp_path / "stats.sqlite3")
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
                {
                    "content": content,
                    "files": [f.filename for f in params.files or []],
                    "embeds": (params.payload or {}).get("embeds", []),
                }
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

    async def say(
        self, content: str, *, attach_image: bool = True, mentions: list[dict] | None = None
    ) -> None:
        """El usuario 5 escribe `content` (con o sin imagen adjunta) y se procesa.

        `mentions` son los usuarios que Discord declara mencionados en el mensaje.
        """
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
                "mentions": mentions or [],
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
async def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Bot de prueba listo para recibir mensajes.

    `apply_magik` (la distorsión por costuras, ~1 s incluso con un PNG de 200x150) se
    sustituye por una función que devuelve la imagen tal cual. Lo que miran estas pruebas
    de `.magik` es el camino del comando (aviso de progreso, archivo `magik.png`, aviso
    borrado, permisos, enfriamiento), no el dibujo, que prueban `test_magik_service.py` y
    `test_images_cog.py`. Se cambia en el servicio ANTES de cargar los cogs, porque
    `load_extension` vuelve a ejecutar `bot.cogs.images` y recoge el sustituto; los efectos
    de `memes` se siguen dibujando de verdad (de ellos depende la extensión del archivo).
    """
    monkeypatch.setattr("bot.services.magik.apply_magik", lambda data, *args, **kwargs: data)
    h = Harness(tmp_path)
    await h.start()
    yield h
    await h.close()


@pytest.mark.asyncio
async def test_magik_con_imagen_adjunta_envia_el_png_y_borra_el_progreso(harness: Harness) -> None:
    """`.magik` + imagen: aviso de progreso, mensaje nuevo con `magik.png` y aviso borrado."""
    await harness.say(".magik")

    assert harness.sent[0]["content"] == "🌀 Distorsionando..."
    assert harness.sent[1]["files"] == ["magik.png"]
    assert harness.deleted == [50]


@pytest.mark.asyncio
async def test_magik_sin_adjunto_usa_el_avatar_y_tambien_responde(harness: Harness) -> None:
    """Sin imagen adjunta se cae al avatar de quien escribe (descargado por la CDN)."""
    await harness.say(".magik", attach_image=False)

    assert harness.sent[-1]["files"] == ["magik.png"]


@pytest.mark.asyncio
async def test_magik_sin_permiso_para_adjuntar_explica_que_activar(harness: Harness) -> None:
    """Si el bot no puede adjuntar archivos, lo dice en vez de quedarse en «Distorsionando…»."""
    harness.forbid_files = True

    await harness.say(".magik")

    assert "Adjuntar archivos" in harness.sent[-1]["content"]


@pytest.mark.asyncio
async def test_un_comando_de_texto_sin_argumentos_obligatorios_responde(harness: Harness) -> None:
    """`.poner` sin nada ya no se ignora: explica qué falta."""
    await harness.say(".poner", attach_image=False)

    assert "consulta" in harness.sent[-1]["content"]


@pytest.mark.asyncio
async def test_un_mensaje_con_prefijo_pero_sin_comando_se_ignora(harness: Harness) -> None:
    """`.loquesea` no existe: el bot no contesta nada."""
    await harness.say(".loquesea", attach_image=False)

    assert harness.sent == []


OTHER_USER = {"id": "6", "username": "otro", "discriminator": "0", "avatar": None}


@pytest.mark.asyncio
async def test_un_efecto_de_texto_envia_la_imagen_generada(harness: Harness) -> None:
    """`.changemymind texto` genera la plantilla con el texto y la envía como JPEG."""
    await harness.say(".changemymind la piña va en la pizza", attach_image=False)

    assert harness.sent[0]["content"] == "🎨 Generando..."
    assert harness.sent[1]["files"] == ["changemymind.jpg"]
    assert harness.deleted == [50]


@pytest.mark.asyncio
async def test_un_efecto_animado_usa_el_avatar_de_quien_escribe(harness: Harness) -> None:
    """Sin menciones ni adjuntos, `.trigger` usa el avatar del autor y devuelve un GIF."""
    await harness.say(".trigger", attach_image=False)

    assert harness.sent[-1]["files"] == ["trigger.gif"]


@pytest.mark.asyncio
async def test_un_efecto_de_dos_avatares_con_mencion(harness: Harness) -> None:
    """`.slap @otro` combina el avatar del autor y el del mencionado."""
    await harness.say(".slap <@6>", attach_image=False, mentions=[OTHER_USER])

    assert harness.sent[-1]["files"] == ["slap.png"]


@pytest.mark.asyncio
async def test_un_efecto_de_dos_avatares_sin_objetivo_explica_el_uso(harness: Harness) -> None:
    """`.slap` sin nadie a quien pegar responde con el uso, sin generar nada."""
    await harness.say(".slap", attach_image=False)

    assert len(harness.sent) == 1
    assert "Menciona a alguien" in harness.sent[0]["content"]
    assert "`.slap @miembro`" in harness.sent[0]["content"]


@pytest.mark.asyncio
async def test_faltan_textos_separados_por_barra(harness: Harness) -> None:
    """`.brain` pide cuatro textos separados con `|` y lo dice si faltan."""
    await harness.say(".brain uno | dos", attach_image=False)

    assert "Falta texto" in harness.sent[0]["content"]
    assert "<texto1> | <texto2> | <texto3> | <texto4>" in harness.sent[0]["content"]


@pytest.mark.asyncio
async def test_varios_textos_separados_por_barra_generan_la_imagen(harness: Harness) -> None:
    """Con los cuatro textos, `.brain` genera su imagen."""
    await harness.say(".brain uno | dos | tres | cuatro", attach_image=False)

    assert harness.sent[-1]["files"] == ["brain.jpg"]


@pytest.mark.asyncio
async def test_el_enfriamiento_es_comun_a_todos_los_comandos_de_imagen(harness: Harness) -> None:
    """Tras `.magik`, un efecto inmediato del mismo usuario tiene que esperar."""
    await harness.say(".magik")
    await harness.say(".trigger", attach_image=False)

    assert "Espera" in harness.sent[-1]["content"]


@pytest.mark.asyncio
async def test_memes_lista_los_efectos_en_un_embed(harness: Harness) -> None:
    """`.memes` envía la lista agrupada de efectos."""
    await harness.say(".memes", attach_image=False)

    embed = harness.sent[0]["embeds"][0]
    assert "efectos de imagen" in embed["title"]
    assert any("trigger" in field["value"] for field in embed["fields"])


@pytest.mark.asyncio
async def test_la_ayuda_lista_los_efectos_por_nombre(harness: Harness) -> None:
    """`.ayuda` muestra `memes` y los efectos, todos solo por nombre."""
    await harness.say(".ayuda", attach_image=False)

    fields = harness.sent[0]["embeds"][0]["fields"]
    text = "\n".join(field["value"] for field in fields)
    names = [name.strip("`") for name in text.replace("\n", " · ").split(" · ")]
    assert "memes" in names
    assert "trigger" in names
    assert "changemymind" in names
