"""Doble de `discord.Interaction` que se comporta como Discord al responder.

Una interacción admite una sola respuesta (`defer`, `edit_message`,
`send_message` o `send_modal`); después, `is_done()` devuelve `True` y lo
siguiente va por `edit_original_response` o `followup.send`. Una segunda
respuesta lanza `discord.InteractionResponded`, como en producción.

Con un `MagicMock` sin más, `is_done()` siempre es falso y las pruebas no ven
si un botón contesta tarde o dos veces. Se usa desde las pruebas de los cogs con
`from interaction_fakes import fake_interaction` (`pyproject.toml` añade `tests/`
al path de importación de pytest).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

RESPONSES = ("defer", "edit_message", "send_message", "send_modal")


def fake_interaction(user: object | None = None, *, events: list[str] | None = None) -> MagicMock:
    """Interacción falsa con respuesta única y registro de lo que se llama.

    Args:
        user: Lo que devolverá `interaction.user`.
        events: Si se pasa, cada respuesta apunta en ella su nombre
            (`"response.defer"`, `"edit_original_response"`…), en orden.
    """
    interaction = MagicMock()
    if user is not None:
        interaction.user = user
    done = False

    def response(name: str) -> AsyncMock:
        async def respond(*args: object, **kwargs: object) -> None:
            nonlocal done
            if done:
                raise discord.InteractionResponded(interaction)
            done = True
            if events is not None:
                events.append(f"response.{name}")

        return AsyncMock(side_effect=respond)

    def later(name: str) -> AsyncMock:
        async def send(*args: object, **kwargs: object) -> None:
            if events is not None:
                events.append(name)

        return AsyncMock(side_effect=send)

    for name in RESPONSES:
        setattr(interaction.response, name, response(name))
    interaction.response.is_done = MagicMock(side_effect=lambda: done)
    interaction.edit_original_response = later("edit_original_response")
    interaction.followup.send = later("followup.send")
    return interaction
