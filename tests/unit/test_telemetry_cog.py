"""Piezas sueltas del cog de telemetría: clases de mensaje, nombres de botón y sistema."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import discord

from bot.cogs.telemetry import (
    LONG_MESSAGE,
    SHORT_MESSAGE,
    item_name,
    message_types,
    read_system_usage,
)


def message(content: str, **kwargs: object) -> MagicMock:
    fake = MagicMock(spec=discord.Message)
    fake.content = content
    fake.attachments = kwargs.get("attachments", [])
    fake.stickers = kwargs.get("stickers", [])
    fake.reference = kwargs.get("reference")
    return fake


def test_message_types_clasifica_por_largo_en_los_bordes() -> None:
    assert message_types(message("a" * SHORT_MESSAGE)) == ["corto"]
    assert message_types(message("a" * (SHORT_MESSAGE + 1))) == ["medio"]
    assert message_types(message("a" * LONG_MESSAGE)) == ["medio"]
    assert message_types(message("a" * (LONG_MESSAGE + 1))) == ["largo"]


def test_message_types_marca_adjuntos_stickers_enlaces_y_respuestas() -> None:
    found = message_types(
        message("http://x.es", attachments=[object()], stickers=[object()], reference=object())
    )
    assert found == ["adjunto", "sticker", "enlace", "respuesta", "corto"]
    # Una imagen sin texto no es ni corta ni larga.
    assert message_types(message("", attachments=[object()])) == ["adjunto"]


def test_item_name_usa_vista_y_metodo_aunque_el_custom_id_sea_al_azar() -> None:
    class Mesa(discord.ui.View):
        @discord.ui.button(label="Tirar")
        async def roll(self, interaction: discord.Interaction, _b: discord.ui.Button) -> None:
            return None

    class Apostar(discord.ui.Button):
        async def callback(self, interaction: discord.Interaction) -> None:
            return None

    async def scenario() -> None:
        view = Mesa()
        subclass = Apostar(label="x")
        plain = discord.ui.Button(label="y")

        async def doblar(interaction: discord.Interaction) -> None:
            return None

        plain.callback = doblar  # type: ignore[method-assign]
        view.add_item(subclass)
        view.add_item(plain)
        assert [item_name(view, item) for item in view.children] == [
            "Mesa.roll",
            "Mesa.Apostar",
            "Mesa.doblar",
        ]

    asyncio.run(scenario())


def test_read_system_usage_devuelve_cifras_validas() -> None:
    memory_mb, rss_mb, chromium, node = read_system_usage()
    assert memory_mb is None or memory_mb > 0
    assert rss_mb >= 0
    assert chromium >= 0 and node >= 0
