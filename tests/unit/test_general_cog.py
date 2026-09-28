"""Pruebas de bot.cogs.general: comportamiento del comando /ping."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.general import General


@pytest.mark.asyncio
async def test_ping_responde_de_forma_efimera_con_la_latencia() -> None:
    """El comando /ping debe responder una sola vez, en efímero, con la latencia del bot."""
    fake_bot = MagicMock()
    fake_bot.latency = 0.123  # segundos -> se espera 123 ms redondeados

    cog = General(fake_bot)

    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.ping.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    _, kwargs = interaction.response.send_message.call_args
    assert kwargs["ephemeral"] is True
    message = interaction.response.send_message.call_args.args[0]
    assert "123 ms" in message
