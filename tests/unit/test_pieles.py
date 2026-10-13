"""Pruebas de `pieles` y de las skins propias: limpiar la imagen, guardarla, pintarla y permisos.

La tienda y la economía son las reales sobre un SQLite temporal (los dobles de
`test_shop.py`); Discord se sustituye por `fake_interaction` y adjuntos falsos.
"""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from PIL import Image
from test_shop import (
    BUYER,
    GUILD,
    OTHER,
    Clock,
    make_cog,
    make_guild,
    make_interaction,
    make_member,
    pay_for,
    texts,
)

from bot.cogs import shop as shop_cog
from bot.cogs.pieles import ImageForm, Pieles, SkinPanel, may_manage
from bot.cogs.shop import Tienda
from bot.services import skin_images, skins
from bot.services.achievements import (
    shop_skin_censored_stats,
    shop_skin_image_stats,
    shop_skin_panel_stats,
)
from bot.services.shop_catalog import CATALOG_BY_KEY
from bot.services.skin_images import MAX_SIDE, SkinImage, SkinImageError, prepare

PROPIO = skins.SKIN_BY_KEY["tapete_propio"]
#: El artículo del tapete propio, barato y sin nivel para no tener que dar dinero ni XP.
PROPIO_ITEM = replace(CATALOG_BY_KEY[PROPIO.catalog_key], price=100, min_level=0)


def png(size: tuple[int, int] = (1600, 900), mode: str = "RGB") -> bytes:
    color = (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def attachment(data: bytes) -> MagicMock:
    file = MagicMock(spec=discord.Attachment)
    file.size = len(data)
    file.read = AsyncMock(return_value=data)
    return file


# -- Limpiar la imagen ----------------------------------------------------------------


def test_la_imagen_se_reduce_y_se_guarda_en_jpeg() -> None:
    image = prepare(png((1600, 900)))
    with Image.open(io.BytesIO(image.data)) as out:
        assert out.format == "JPEG"
        assert max(out.size) == MAX_SIDE
        assert out.size[0] / out.size[1] == pytest.approx(16 / 9, rel=0.01)
    assert image == SkinImage.of(image.data)
    assert image.data_url.startswith("data:image/jpeg;base64,")


def test_lo_transparente_queda_sobre_el_verde_del_fieltro() -> None:
    image = prepare(png((40, 40), "RGBA"))
    with Image.open(io.BytesIO(image.data)) as out:
        r, g, _b = out.convert("RGB").getpixel((20, 20))
    assert g > 40 and r < 200  # mezcla del rojo medio transparente con el verde


def test_se_quitan_los_metadatos_de_la_foto() -> None:
    exif = Image.Exif()
    exif[0x010F] = "Móvil de Ábalos"  # fabricante; el GPS iría igual
    buffer = io.BytesIO()
    Image.new("RGB", (50, 50), "blue").save(buffer, format="JPEG", exif=exif)
    with Image.open(io.BytesIO(prepare(buffer.getvalue()).data)) as out:
        assert not out.getexif()


@pytest.mark.parametrize(
    "raw",
    [b"", b"esto no es una imagen", b"GIF89a roto"],
)
def test_lo_que_no_es_una_imagen_se_rechaza(raw: bytes) -> None:
    with pytest.raises(SkinImageError):
        prepare(raw)


def test_se_rechazan_los_formatos_raros_y_las_imagenes_enormes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, format="BMP")
    with pytest.raises(SkinImageError, match="PNG"):
        prepare(buffer.getvalue())
    monkeypatch.setattr(skin_images, "MAX_SOURCE_PIXELS", 100)
    with pytest.raises(SkinImageError, match="enorme"):
        prepare(png((20, 20)))
    monkeypatch.setattr(skin_images, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(SkinImageError, match="Pesa"):
        prepare(png((20, 20)))


# -- El registro ----------------------------------------------------------------------


def test_la_imagen_va_en_los_parametros_y_su_resumen_en_la_clave() -> None:
    image = prepare(png((60, 40)))
    bare = skins.params("moneda", PROPIO)
    assert bare["key"] == "tapete_propio" and bare["image"] == ""
    with_image = skins.params("moneda", PROPIO, image)
    assert with_image["key"] == f"tapete_propio:{image.digest}"
    assert with_image["image"] == image.data_url
    # Una skin que no es propia no se lleva imagen aunque se la pasen.
    oro = skins.SKIN_BY_KEY["tapete_oro"]
    assert skins.params("moneda", oro, image)["image"] == ""
    assert skins.custom_skins() == (PROPIO,)


def test_estadisticas_de_las_pieles() -> None:
    assert shop_skin_image_stats(own=True).add == {"shop_skin_image_uploads": 1}
    assert shop_skin_image_stats(own=False).add == {"shop_skin_image_admin": 1}
    assert shop_skin_censored_stats().add == {"shop_skin_image_censored": 1}
    assert shop_skin_panel_stats().add == {"shop_skin_panel": 1}


# -- La tienda la guarda y la pinta ----------------------------------------------------


async def owner_with_skin(tmp_path: Path) -> tuple[Tienda, MagicMock, MagicMock]:
    cog = await make_cog(tmp_path, Clock(), catalog=(PROPIO_ITEM,))
    guild = make_guild()
    member = make_member(guild)
    await pay_for(cog, guild, member, PROPIO.catalog_key)
    return cog, guild, member


def make_pieles(tienda: Tienda) -> Pieles:
    bot = MagicMock()
    bot.get_cog = lambda name: tienda if name == "Tienda" else None
    tienda.bot = bot
    return Pieles(bot)


async def test_comprar_el_tapete_propio_lo_pone_liso_hasta_subir_la_imagen(tmp_path: Path) -> None:
    cog, _guild, _member = await owner_with_skin(tmp_path)
    assert cog.skin(GUILD, BUYER, "moneda") == PROPIO
    assert cog.scene_params(GUILD, BUYER, "moneda")["image"] == ""


async def test_la_imagen_subida_llega_a_la_escena_y_se_recupera_al_arrancar(
    tmp_path: Path,
) -> None:
    cog, _guild, member = await owner_with_skin(tmp_path)
    pieles = make_pieles(cog)

    text = await pieles.upload(GUILD, member, member, attachment(png()), None)

    image = cog.skin_image(GUILD, BUYER, PROPIO.key)
    assert image is not None and "🖼️" in text
    params = shop_cog.skin_params(pieles.bot, GUILD, BUYER, "ruleta")
    assert params["image"] == image.data_url
    assert params["key"] == f"tapete_propio:{image.digest}"
    # Al reiniciar, sale de la base de datos.
    again = Tienda(MagicMock(), cog.economy, cog.repository, clock=cog.clock, catalog=())
    await again.cog_load()
    again._expire.cancel()
    assert again.skin_image(GUILD, BUYER, PROPIO.key) == image


async def test_sin_la_skin_no_se_sube_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(PROPIO_ITEM,))
    member = make_member(make_guild())
    text = await make_pieles(cog).upload(GUILD, member, member, attachment(png()), None)
    assert "tienda" in text
    assert cog.skin_image(GUILD, BUYER, PROPIO.key) is None


async def test_una_imagen_que_no_vale_no_se_guarda(tmp_path: Path) -> None:
    cog, _guild, member = await owner_with_skin(tmp_path)
    text = await make_pieles(cog).upload(GUILD, member, member, attachment(b"nope"), None)
    assert text.startswith("❌")
    assert cog.skin_image(GUILD, BUYER, PROPIO.key) is None


async def test_solo_el_dueno_o_un_admin_tocan_la_imagen(tmp_path: Path) -> None:
    cog, guild, owner = await owner_with_skin(tmp_path)
    pieles = make_pieles(cog)
    stranger = make_member(guild, OTHER)
    stranger.guild_permissions.administrator = False
    owner.guild_permissions.administrator = False
    assert may_manage(owner, owner) and not may_manage(stranger, owner)

    text = await pieles.upload(GUILD, stranger, owner, attachment(png()), None)
    assert "admins" in text and cog.skin_image(GUILD, BUYER, PROPIO.key) is None

    stranger.guild_permissions.administrator = True
    await pieles.upload(GUILD, stranger, owner, attachment(png()), None)
    assert cog.skin_image(GUILD, BUYER, PROPIO.key) is not None
    text = await pieles.remove(GUILD, stranger, owner, PROPIO, None)
    assert "Diego" in text
    assert cog.skin_image(GUILD, BUYER, PROPIO.key) is None
    assert cog.scene_params(GUILD, BUYER, "moneda")["image"] == ""


# -- El panel -------------------------------------------------------------------------


def buttons(view: ui.LayoutView) -> dict[str, ui.Button]:
    return {c.label: c for c in view.walk_children() if isinstance(c, ui.Button) and c.label}


async def test_el_panel_ofrece_comprar_el_tapete_propio_a_quien_no_lo_tiene(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(PROPIO_ITEM,))
    member = make_member(make_guild())
    interaction = make_interaction(member)

    await make_pieles(cog).pieles.callback(make_pieles(cog), interaction)

    panel = interaction.edit_original_response.await_args.kwargs["view"]
    assert isinstance(panel, SkinPanel)
    assert any(label.startswith("🛒 Tapete propio") for label in buttons(panel))
    assert "🖼️ Subir imagen" not in buttons(panel)


async def test_subir_la_imagen_desde_el_formulario_repinta_el_panel(tmp_path: Path) -> None:
    cog, _guild, member = await owner_with_skin(tmp_path)
    pieles = make_pieles(cog)
    opening = make_interaction(member)
    await pieles.pieles.callback(pieles, opening)
    panel = opening.edit_original_response.await_args.kwargs["view"]
    assert "sin imagen" in texts(panel)

    click = make_interaction(member)
    await buttons(panel)["🖼️ Subir imagen"].callback(click)
    form = click.response.send_modal.await_args.args[0]
    assert isinstance(form, ImageForm)
    submit = make_interaction(member)
    form.upload._values = [attachment(png())]  # lo que rellena Discord al enviar
    await form.on_submit(submit)

    submit.response.defer.assert_awaited_once()  # se acepta antes de descargar y guardar
    kwargs = submit.edit_original_response.await_args.kwargs
    assert [f.filename for f in kwargs["attachments"]] == ["tapete_propio.jpg"]
    assert "con imagen" in texts(kwargs["view"])
    assert "🗑️ Quitar imagen" in buttons(kwargs["view"])

    remove = make_interaction(member)
    await buttons(kwargs["view"])["🗑️ Quitar imagen"].callback(remove)
    remove.response.defer.assert_awaited_once()
    assert remove.edit_original_response.await_args.kwargs["attachments"] == []
    assert cog.skin_image(GUILD, BUYER, PROPIO.key) is None


async def test_pieles_con_imagen_la_sube_sin_panel(tmp_path: Path) -> None:
    cog, _guild, member = await owner_with_skin(tmp_path)
    pieles = make_pieles(cog)
    interaction = make_interaction(member)

    await pieles.pieles.callback(pieles, interaction, imagen=attachment(png()))

    assert interaction.response.defer.await_args.kwargs["ephemeral"] is True
    assert "🖼️" in interaction.followup.send.await_args.args[0]
    assert cog.skin_image(GUILD, BUYER, PROPIO.key) is not None


async def test_quien_no_es_admin_no_abre_las_pieles_de_otro(tmp_path: Path) -> None:
    cog, guild, owner = await owner_with_skin(tmp_path)
    pieles = make_pieles(cog)
    snoop = make_member(guild, OTHER)
    snoop.guild_permissions.administrator = False
    interaction = make_interaction(snoop)

    await pieles.pieles.callback(pieles, interaction, miembro=owner)

    assert "admins" in interaction.followup.send.await_args.args[0]
    interaction.edit_original_response.assert_not_awaited()


async def test_el_panel_de_otro_solo_lo_toca_quien_lo_abrio(tmp_path: Path) -> None:
    cog, guild, owner = await owner_with_skin(tmp_path)
    pieles = make_pieles(cog)
    admin = make_member(guild, OTHER)
    panel = await pieles.open_panel(guild, admin, owner)
    assert isinstance(panel, SkinPanel) and not panel.own
    # Un admin ve la imagen de otro y la puede subir, pero no le cambia la skin de las mesas.
    assert "🖼️ Subir imagen" in buttons(panel)
    assert not any(isinstance(c, ui.Select) for c in panel.walk_children())
    assert await panel.interaction_check(make_interaction(admin))
    assert not await panel.interaction_check(make_interaction(owner))
    # Y si deja de ser admin, ya no.
    admin.guild_permissions.administrator = False
    assert not await panel.interaction_check(make_interaction(admin))


async def test_la_version_de_texto_sube_el_adjunto(tmp_path: Path) -> None:
    cog, _guild, member = await owner_with_skin(tmp_path)
    pieles = make_pieles(cog)
    ctx = MagicMock()
    ctx.guild = member.guild
    ctx.author = member
    ctx.message.attachments = [attachment(png())]
    ctx.send = AsyncMock()
    ctx.typing = MagicMock(return_value=AsyncMock())

    await pieles.pieles_text.callback(pieles, ctx)

    assert "🖼️" in ctx.send.await_args.args[0]
    assert cog.skin_image(GUILD, BUYER, PROPIO.key) is not None
