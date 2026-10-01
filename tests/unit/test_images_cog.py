"""Pruebas de bot.cogs.images: `/magik` y `ºmagik` comparten lógica y validaciones."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

from bot.cogs.images import COOLDOWN_SECONDS, Images
from bot.services.magik import MAX_INPUT_BYTES
from bot.utils.responder import CommandResponder


class FakeResponder(CommandResponder):
    """Responder que registra cada envío, sin tocar Discord."""

    def __init__(self, *, user_id: int = 1, in_guild: bool = True) -> None:
        self.guild = SimpleNamespace(id=10) if in_guild else None
        self.member = MagicMock(spec=discord.Member) if in_guild else None
        if self.member is not None:
            self.member.id = user_id
        self.channel = MagicMock()
        self.errors: list[str] = []
        self.finished: list[dict[str, object]] = []
        self.progress: list[str] = []

    async def send(self, content=None, **kwargs) -> None:  # noqa: ANN001
        raise AssertionError("magik no debe usar send")

    async def send_error(self, content: str) -> None:
        self.errors.append(content)

    async def start_progress(self, placeholder: str = "") -> None:
        self.progress.append(placeholder)

    async def finish(self, content=None, **kwargs) -> None:  # noqa: ANN001
        self.finished.append({"content": content, **kwargs})


def png_bytes(width: int = 64, height: int = 48) -> bytes:
    """PNG liso pequeño y válido."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def make_attachment(
    *,
    content_type: str | None = "image/png",
    size: int = 1000,
    data: bytes | None = None,
) -> MagicMock:
    """Adjunto de Discord simulado."""
    attachment = MagicMock(spec=discord.Attachment)
    attachment.content_type = content_type
    attachment.size = size
    attachment.read = AsyncMock(return_value=png_bytes() if data is None else data)
    return attachment


def make_asset(data: bytes | None = None) -> MagicMock:
    """Avatar simulado."""
    asset = MagicMock(spec=discord.Asset)
    asset.read = AsyncMock(return_value=png_bytes() if data is None else data)
    return asset


@pytest.mark.asyncio
async def test_magik_responde_con_un_png_adjunto() -> None:
    """El resultado se envía como `magik.png` sustituyendo al aviso de progreso."""
    cog = Images(MagicMock())
    responder = FakeResponder()

    await cog._magik_impl(responder, make_attachment())

    assert responder.errors == []
    assert len(responder.progress) == 1
    file = responder.finished[0]["file"]
    assert file.filename == "magik.png"
    assert file.fp.read(8) == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_magik_funciona_tambien_con_un_avatar() -> None:
    """Un `discord.Asset` (avatar) se procesa igual que un adjunto."""
    cog = Images(MagicMock())
    responder = FakeResponder()

    await cog._magik_impl(responder, make_asset())

    assert responder.finished[0]["file"].filename == "magik.png"


@pytest.mark.asyncio
async def test_magik_rechaza_adjuntos_que_no_son_imagen() -> None:
    """Un adjunto con tipo distinto de imagen no se descarga ni se procesa."""
    cog = Images(MagicMock())
    responder = FakeResponder()
    attachment = make_attachment(content_type="application/pdf")

    await cog._magik_impl(responder, attachment)

    assert responder.errors == ["Ese archivo no es una imagen."]
    attachment.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_magik_rechaza_adjuntos_sin_tipo_declarado() -> None:
    """Sin `content_type` no se puede confiar en que sea una imagen."""
    cog = Images(MagicMock())
    responder = FakeResponder()

    await cog._magik_impl(responder, make_attachment(content_type=None))

    assert responder.errors == ["Ese archivo no es una imagen."]


@pytest.mark.asyncio
async def test_magik_rechaza_adjuntos_pesados_antes_de_descargarlos() -> None:
    """El tamaño declarado se comprueba antes de leer ningún byte."""
    cog = Images(MagicMock())
    responder = FakeResponder()
    attachment = make_attachment(size=MAX_INPUT_BYTES + 1)

    await cog._magik_impl(responder, attachment)

    assert "demasiado grande" in responder.errors[0]
    attachment.read.assert_not_awaited()
    assert responder.progress == []


@pytest.mark.asyncio
async def test_magik_informa_si_el_archivo_no_se_puede_leer_como_imagen() -> None:
    """Una imagen corrupta produce un aviso amable, no un traceback."""
    cog = Images(MagicMock())
    responder = FakeResponder()

    await cog._magik_impl(responder, make_attachment(data=b"basura"))

    assert "No pude leer esa imagen" in responder.finished[0]["content"]
    assert "file" not in responder.finished[0]


@pytest.mark.asyncio
async def test_magik_informa_si_falla_la_descarga() -> None:
    """Un error HTTP al descargar se comunica sin romper el comando."""
    cog = Images(MagicMock())
    responder = FakeResponder()
    attachment = make_attachment()
    attachment.read = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "fallo"))

    await cog._magik_impl(responder, attachment)

    assert "No se pudo descargar" in responder.finished[0]["content"]


@pytest.mark.asyncio
async def test_magik_solo_funciona_en_servidores() -> None:
    """Fuera de un servidor responde con un error y no hace nada más."""
    cog = Images(MagicMock())
    responder = FakeResponder(in_guild=False)
    attachment = make_attachment()

    await cog._magik_impl(responder, attachment)

    assert "solo está disponible dentro de un servidor" in responder.errors[0]
    attachment.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_magik_aplica_enfriamiento_por_usuario() -> None:
    """El mismo usuario no puede repetir el comando al instante; otro sí."""
    cog = Images(MagicMock())
    first = FakeResponder(user_id=1)
    repeated = FakeResponder(user_id=1)
    other_user = FakeResponder(user_id=2)

    await cog._magik_impl(first, make_attachment())
    await cog._magik_impl(repeated, make_attachment())
    await cog._magik_impl(other_user, make_attachment())

    assert first.errors == []
    assert repeated.errors and "Espera" in repeated.errors[0]
    assert repeated.finished == []
    assert other_user.errors == []
    assert COOLDOWN_SECONDS > 0


class FailingFinishResponder(FakeResponder):
    """Responder cuyo primer `finish` con archivo falla, como Discord sin permisos."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def finish(self, content=None, **kwargs) -> None:  # noqa: ANN001
        if "file" in kwargs:
            raise self._error
        await super().finish(content, **kwargs)


@pytest.mark.asyncio
async def test_magik_avisa_si_el_bot_no_puede_adjuntar_archivos() -> None:
    """Sin el permiso «Adjuntar archivos» se explica qué activar, en vez de colgarse."""
    cog = Images(MagicMock())
    responder = FailingFinishResponder(discord.Forbidden(MagicMock(status=403), "sin permiso"))

    await cog._magik_impl(responder, make_attachment())

    assert "Adjuntar archivos" in responder.finished[-1]["content"]


@pytest.mark.asyncio
async def test_magik_avisa_si_falla_el_envio_de_la_imagen() -> None:
    """Un error HTTP al enviar el resultado se comunica con un mensaje claro."""
    cog = Images(MagicMock())
    responder = FailingFinishResponder(discord.HTTPException(MagicMock(status=500), "fallo"))

    await cog._magik_impl(responder, make_attachment())

    assert "No pude enviar la imagen" in responder.finished[-1]["content"]


@pytest.mark.asyncio
async def test_magik_no_deja_colgado_el_progreso_ante_un_error_inesperado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fallo imprevisto del procesado se registra y se avisa al usuario."""
    cog = Images(MagicMock())
    responder = FakeResponder()
    boom = MagicMock(side_effect=RuntimeError("fallo interno"))
    monkeypatch.setitem(Images._magik_impl.__globals__, "apply_magik", boom)

    await cog._magik_impl(responder, make_attachment())

    assert "Algo salió mal" in responder.finished[0]["content"]
    assert "fallo interno" not in responder.finished[0]["content"]


def test_magik_tiene_el_mismo_nombre_en_slash_y_texto() -> None:
    """`/magik` y `ºmagik` se llaman igual y el de texto no tiene alias."""
    cog = Images(MagicMock())

    assert [c.name for c in cog.get_app_commands()] == ["magik"]
    assert [c.name for c in cog.get_commands()] == ["magik"]
    assert not cog.get_commands()[0].aliases


@pytest.mark.asyncio
async def test_slash_usa_la_imagen_adjunta_antes_que_el_avatar() -> None:
    """Con `imagen`, no se usa ningún avatar."""
    cog = Images(MagicMock())
    cog._magik_impl = AsyncMock()
    attachment = make_attachment()

    await cog.magik.callback(cog, MagicMock(), attachment, MagicMock())

    assert cog._magik_impl.await_args.args[1] is attachment


@pytest.mark.asyncio
async def test_slash_sin_imagen_usa_el_avatar_del_miembro_indicado() -> None:
    """Sin adjunto, `miembro` decide el avatar; si no, el de quien ejecuta."""
    cog = Images(MagicMock())
    cog._magik_impl = AsyncMock()
    member = MagicMock()
    interaction = MagicMock()

    await cog.magik.callback(cog, interaction, None, member)
    await cog.magik.callback(cog, interaction, None, None)

    first, second = (call.args[1] for call in cog._magik_impl.await_args_list)
    assert first is member.display_avatar.replace.return_value
    assert second is interaction.user.display_avatar.replace.return_value


def make_text_context(
    *, attachments: list | None = None, replied_attachments: list | None = None
) -> MagicMock:
    """Contexto de texto con adjuntos propios y/o en el mensaje al que se responde."""
    ctx = MagicMock()
    ctx.message.attachments = attachments or []
    if replied_attachments is None:
        ctx.message.reference = None
    else:
        replied = MagicMock(spec=discord.Message)
        replied.attachments = replied_attachments
        ctx.message.reference.resolved = replied
    return ctx


@pytest.mark.asyncio
async def test_texto_prefiere_el_adjunto_del_propio_mensaje() -> None:
    """`ºmagik` con una imagen adjunta usa esa imagen."""
    cog = Images(MagicMock())
    cog._magik_impl = AsyncMock()
    own = make_attachment()
    replied = make_attachment()
    ctx = make_text_context(attachments=[own], replied_attachments=[replied])

    await cog.magik_text.callback(cog, ctx, None)

    assert cog._magik_impl.await_args.args[1] is own


@pytest.mark.asyncio
async def test_texto_usa_la_imagen_del_mensaje_respondido() -> None:
    """Respondiendo a un mensaje con imagen, se deforma esa imagen."""
    cog = Images(MagicMock())
    cog._magik_impl = AsyncMock()
    replied = make_attachment()
    ctx = make_text_context(replied_attachments=[replied])

    await cog.magik_text.callback(cog, ctx, None)

    assert cog._magik_impl.await_args.args[1] is replied


@pytest.mark.asyncio
async def test_texto_ignora_adjuntos_que_no_son_imagen() -> None:
    """Un PDF adjunto no cuenta: se cae al avatar de quien escribe."""
    cog = Images(MagicMock())
    cog._magik_impl = AsyncMock()
    ctx = make_text_context(attachments=[make_attachment(content_type="application/pdf")])

    await cog.magik_text.callback(cog, ctx, None)

    assert cog._magik_impl.await_args.args[1] is ctx.author.display_avatar.replace.return_value


@pytest.mark.asyncio
async def test_texto_con_miembro_usa_su_avatar() -> None:
    """`ºmagik @alguien` deforma el avatar del miembro mencionado."""
    cog = Images(MagicMock())
    cog._magik_impl = AsyncMock()
    member = MagicMock()
    ctx = make_text_context()

    await cog.magik_text.callback(cog, ctx, member)

    assert cog._magik_impl.await_args.args[1] is member.display_avatar.replace.return_value
