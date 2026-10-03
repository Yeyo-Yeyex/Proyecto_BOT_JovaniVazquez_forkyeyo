"""Pruebas de integración locales para el registro de slash commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from discord import app_commands

from bot.app import INITIAL_EXTENSIONS, BotClient, build_intents
from bot.cogs.general import build_help_embed
from bot.services.memes import EFFECTS

# Máxima longitud de un nombre de comando: deben ser cortos y fáciles de teclear.
MAX_COMMAND_NAME_LENGTH = 8

EXPECTED_COMMANDS = {
    "ping",
    "entrada",
    "help",
    "level",
    "top",
    "play",
    "pause",
    "resume",
    "skip",
    "stop",
    "queue",
    "remove",
    "clear",
    "volume",
    "ruleta",
    "saldo",
    "daily",
}

# Comandos de imagen: solo de texto, para reservar los slash commands al resto.
TEXT_ONLY_COMMANDS = {"magik", "memes"}


def test_comandos_slash_y_texto_comparten_nombres_cortos_y_sin_alias(tmp_path: Path) -> None:
    """`/x` y `.x` son el mismo comando: mismos nombres, cortos, planos y sin alias."""

    async def load_cogs() -> None:
        client = BotClient(
            command_prefix=".",
            database_path=tmp_path / "message_stats.sqlite3",
        )
        try:
            for extension in INITIAL_EXTENSIONS:
                await client.load_extension(extension)

            slash = client.tree.get_commands()
            slash_names = {command.name for command in slash}
            text_names = {c.name for c in client.commands}
            effects = {c.name for c in client.commands if "help_group" in c.extras}

            assert slash_names == EXPECTED_COMMANDS
            assert text_names == slash_names | TEXT_ONLY_COMMANDS | set(EFFECTS)
            # Los efectos conservan el nombre de Dank Memer (algunos de más de
            # 8 letras), no ocupan slash commands y la ayuda los lista en bloque.
            assert effects == set(EFFECTS)
            assert all(isinstance(command, app_commands.Command) for command in slash)
            assert all(not command.aliases for command in client.commands)
            assert all(len(name) <= MAX_COMMAND_NAME_LENGTH for name in text_names - effects)
            assert client.get_cog("Welcome") is not None
            assert client.get_cog("Music") is not None
        finally:
            await client.close()

    asyncio.run(load_cogs())


def test_la_ayuda_real_es_breve_y_respeta_los_limites_de_discord(tmp_path: Path) -> None:
    """La ayuda lista cada comando una vez, agrupada, y cabe en un embed."""

    async def check_help() -> None:
        client = BotClient(
            command_prefix=".",
            database_path=tmp_path / "message_stats.sqlite3",
        )
        try:
            for extension in INITIAL_EXTENSIONS:
                await client.load_extension(extension)

            embed = build_help_embed(client)
            detailed = [f for f in embed.fields if "(" not in f.name or "prefijo" in f.name]
            text = "\n".join(field.value for field in detailed)
            listed = " · ".join(f.value for f in embed.fields if f not in detailed).split(" · ")

            assert [field.name for field in embed.fields] == [
                "🎵 Música",
                "🔔 Entradas",
                "🎨 Imagen (solo con prefijo)",
                "🖼️ Con avatar (46)",
                "💬 Avatar + texto (10)",
                "📝 Solo texto (49)",
                "🎬 Vídeo (3)",
                "📊 Niveles",
                "🎰 Casino",
                "⚙️ General",
            ]
            for name in EXPECTED_COMMANDS | TEXT_ONLY_COMMANDS:
                assert text.count(f"**{name}**") == 1
            assert len(text.splitlines()) == len(EXPECTED_COMMANDS | TEXT_ONLY_COMMANDS)
            # Cada efecto aparece una vez, solo por nombre.
            assert sorted(listed) == sorted(EFFECTS)
            assert all(len(field.value) <= 1024 for field in embed.fields)
            assert len(embed) <= 6000
        finally:
            await client.close()

    asyncio.run(check_help())


def test_build_intents_habilita_eventos_de_miembros() -> None:
    """Se activa el intent necesario para detectar entradas y salidas."""
    assert build_intents().members


def test_build_intents_habilita_contenido_de_mensajes() -> None:
    """Se activa el intent necesario para leer comandos de texto con prefijo."""
    assert build_intents().message_content


def test_el_bot_usa_unicamente_el_prefijo_configurado(tmp_path: Path) -> None:
    """No hay prefijos ocultos: solo el configurado activa comandos de texto."""

    async def check_prefix() -> None:
        client = BotClient(command_prefix=".", database_path=tmp_path / "s.sqlite3")
        try:
            assert client.command_prefix == "."
        finally:
            await client.close()

    asyncio.run(check_prefix())
