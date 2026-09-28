"""Pruebas de integración locales para el registro de slash commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.app import BotClient


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
            root_commands = {command.name: command for command in client.tree.get_commands()}

            assert root_commands.keys() == {"ping", "nivel", "ranking"}
        finally:
            await client.close()

    asyncio.run(load_cogs())
