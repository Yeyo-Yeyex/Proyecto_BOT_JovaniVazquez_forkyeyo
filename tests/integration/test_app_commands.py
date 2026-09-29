"""Pruebas de integración locales para el registro de slash commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.app import BotClient, _build_command_prefixes, build_intents


def test_cogs_registran_comandos_unicos_y_esperados(tmp_path: Path) -> None:
    """Los cogs se cargan juntos y exponen los comandos de niveles sin colisiones."""

    async def load_cogs() -> None:
        client = BotClient(
            command_prefix="!",
            database_path=tmp_path / "message_stats.sqlite3",
        )
        try:
            await client.load_extension("bot.cogs.general")
            await client.load_extension("bot.cogs.message_stats")
            await client.load_extension("bot.cogs.welcome")
            await client.load_extension("bot.cogs.music")
            root_commands = {command.name: command for command in client.tree.get_commands()}

            assert root_commands.keys() == {
                "ping",
                "nivel",
                "ranking",
                "reproducir",
                "p",
                "pausar",
                "pausa",
                "reanudar",
                "rs",
                "saltar",
                "s",
                "cola",
                "q",
                "quitar",
                "rm",
                "limpiar",
                "cl",
                "parar",
                "stop",
                "volumen",
                "vol",
            }
            assert client.get_cog("Welcome") is not None
            assert client.get_cog("Music") is not None

            text_commands = {command.name for command in client.commands}
            assert text_commands == {
                "reproducir",
                "pausar",
                "reanudar",
                "saltar",
                "parar",
                "cola",
                "quitar",
                "limpiar",
                "volumen",
            }
            text_aliases = {alias for command in client.commands for alias in command.aliases}
            assert text_aliases == {
                "p",
                "play",
                "pausa",
                "pause",
                "rs",
                "resume",
                "s",
                "skip",
                "stop",
                "q",
                "queue",
                "rm",
                "remove",
                "cl",
                "clear",
                "vol",
                "volume",
            }
        finally:
            await client.close()

    asyncio.run(load_cogs())


def test_build_intents_habilita_eventos_de_miembros() -> None:
    """Se activa el intent necesario para detectar entradas y salidas."""
    assert build_intents().members


def test_build_intents_habilita_contenido_de_mensajes() -> None:
    """Se activa el intent necesario para leer comandos de texto con prefijo."""
    assert build_intents().message_content


def test_build_command_prefixes_combina_prefijo_configurado_y_fijo() -> None:
    """El prefijo `º` siempre está disponible además del configurado."""
    assert _build_command_prefixes("!") == ("!", "º")


def test_build_command_prefixes_evita_duplicados() -> None:
    """Si el prefijo configurado ya es `º`, no se repite."""
    assert _build_command_prefixes("º") == ("º",)
