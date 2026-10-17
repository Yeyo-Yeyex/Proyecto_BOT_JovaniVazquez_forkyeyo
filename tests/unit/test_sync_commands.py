"""Pruebas de `BotClient.sync_commands`: sincronizar sin quitar el comando de la Activity.

Con las Activities activadas, Discord crea un comando de entrada (tipo 4) que
discord.py no conoce, y rechaza una sustitución en bloque que lo deje fuera: con
`tree.sync()` a secas el bot no arrancaría. La API de Discord se sustituye por dobles.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from bot.app import ENTRY_POINT, ENTRY_POINT_TYPE, BotClient

APP_ID = 99


async def ping(interaction) -> None:  # noqa: ANN001
    """Comando de prueba."""


@pytest.fixture
def client(tmp_path: Path) -> BotClient:
    bot = BotClient(command_prefix=".", database_path=tmp_path / "bot.db")
    bot._connection.application_id = APP_ID
    bot.tree.add_command(app_commands.Command(name="ping", description="Pong", callback=ping))
    bot.tree.sync = AsyncMock(return_value=["ping"])  # type: ignore[method-assign]
    bot.http.bulk_upsert_global_commands = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda application_id, payload: payload
    )
    bot.http.edit_global_command = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda application_id, command_id, payload: {
            "id": command_id,
            "type": ENTRY_POINT_TYPE,
            "handler": 2,
            **payload,
        }
    )
    return bot


async def test_sin_activity_sincroniza_como_siempre(client: BotClient) -> None:
    client.http.get_global_commands = AsyncMock(return_value=[{"type": 1, "name": "ping"}])

    assert await client.sync_commands() == 1

    client.tree.sync.assert_awaited_once()
    client.http.bulk_upsert_global_commands.assert_not_awaited()


async def test_con_activity_renombra_y_conserva_el_comando_de_entrada(client: BotClient) -> None:
    client.http.get_global_commands = AsyncMock(
        return_value=[
            {"id": "5", "type": 1, "name": "ping"},
            {
                "id": "7",
                "type": ENTRY_POINT_TYPE,
                "name": "launch",
                "description": "Launch an activity",
                "handler": 2,
                "integration_types": [0, 1],
                "contexts": None,
                "version": "1",
            },
        ]
    )

    assert await client.sync_commands() == 2

    client.tree.sync.assert_not_awaited()
    client.http.edit_global_command.assert_awaited_once_with(APP_ID, "7", ENTRY_POINT)
    payload = client.http.bulk_upsert_global_commands.await_args.kwargs["payload"]
    assert payload[0]["name"] == "ping"
    # Discord abre la Activity (handler 2) y lo que no se puede escribir no se reenvía.
    assert payload[1] == {**ENTRY_POINT, "type": ENTRY_POINT_TYPE, "handler": 2}


async def test_con_el_nombre_ya_puesto_no_lo_vuelve_a_editar(client: BotClient) -> None:
    client.http.get_global_commands = AsyncMock(
        return_value=[{"id": "7", "type": ENTRY_POINT_TYPE, "handler": 2, **ENTRY_POINT}]
    )

    await client.sync_commands()

    client.http.edit_global_command.assert_not_awaited()
    payload = client.http.bulk_upsert_global_commands.await_args.kwargs["payload"]
    assert payload[-1]["name"] == ENTRY_POINT["name"]


async def test_si_discord_no_deja_renombrarlo_arranca_con_el_nombre_que_tenga(
    client: BotClient,
) -> None:
    """El nombre es estético: un fallo al renombrar no puede dejar el bot sin arrancar."""
    error = discord.HTTPException(MagicMock(status=400, reason="Bad Request"), "nombre")
    client.http.edit_global_command = AsyncMock(side_effect=error)
    client.http.get_global_commands = AsyncMock(
        return_value=[
            {
                "id": "7",
                "type": ENTRY_POINT_TYPE,
                "handler": 2,
                "name": "launch",
                "description": "Launch",
            }
        ]
    )

    assert await client.sync_commands() == 2

    payload = client.http.bulk_upsert_global_commands.await_args.kwargs["payload"]
    assert payload[-1]["name"] == "launch"
