"""El rescate de clics del cog `Errors` con el `ViewStore` real de discord.py.

discord.py tira sin contestar los clics que no encuentra en una vista viva y
Discord los enseña como «Esta interacción ha fallado». `install_click_rescue`
envuelve métodos internos de discord.py (`ViewStore.dispatch_view` y
`dispatch_modal`) y lee su estado (`_views`, `_synced_message_views`): si una
versión nueva los cambia, estas pruebas fallan.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import discord
import pytest
from discord.ui.view import ViewStore
from interaction_fakes import fake_interaction

from bot.cogs.errors import BUTTON_ERROR, EXPIRED_CLICK, install_click_rescue, rescue_click
from bot.utils.interactions import claim_custom_ids

MESSAGE_ID = 5
BUTTON = discord.ComponentType.button.value


@pytest.fixture(autouse=True)
def rescue() -> None:
    install_click_rescue()


def recording_button(
    custom_id: str, calls: list[str], tag: str, **kwargs: object
) -> discord.ui.Button:
    button: discord.ui.Button = discord.ui.Button(label=tag, custom_id=custom_id, **kwargs)

    async def callback(interaction: discord.Interaction) -> None:
        calls.append(tag)
        await interaction.response.defer()

    button.callback = callback  # type: ignore[method-assign]
    return button


def click(events: list[str], custom_id: str, *, message_id: int | None = MESSAGE_ID) -> MagicMock:
    interaction = fake_interaction(events=events)
    interaction.message = None if message_id is None else MagicMock(id=message_id)
    interaction.data = {"custom_id": custom_id, "component_type": BUTTON}
    interaction.client = MagicMock(telemetry=None)
    return interaction


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def store_with(view: discord.ui.View) -> ViewStore:
    store = ViewStore(MagicMock())
    store.add_view(view, MESSAGE_ID)
    return store


async def test_click_on_rebuilt_button_goes_to_its_replacement() -> None:
    calls: list[str] = []
    events: list[str] = []
    view = discord.ui.View()
    view.add_item(recording_button("juego:pedir", calls, "viejo"))
    store = store_with(view)
    # La vista rehace sus botones; Discord aún no tiene el mensaje nuevo.
    view.clear_items()
    view.add_item(recording_button("juego:pedir", calls, "nuevo"))

    store.dispatch_view(BUTTON, "juego:pedir", click(events, "juego:pedir"))
    await settle()

    assert calls == ["nuevo"]
    assert events == ["response.defer"]


async def test_click_on_rebuilt_button_now_disabled_is_accepted_quietly() -> None:
    calls: list[str] = []
    events: list[str] = []
    view = discord.ui.View()
    view.add_item(recording_button("juego:plantarse", calls, "viejo"))
    store = store_with(view)
    view.clear_items()
    view.add_item(recording_button("juego:plantarse", calls, "nuevo", disabled=True))

    store.dispatch_view(BUTTON, "juego:plantarse", click(events, "juego:plantarse"))
    await settle()

    assert calls == []
    assert events == ["response.defer"]


async def test_click_on_rebuilt_button_that_is_gone_is_accepted_quietly() -> None:
    calls: list[str] = []
    events: list[str] = []
    view = discord.ui.View()
    view.add_item(recording_button("juego:doblar", calls, "viejo"))
    store = store_with(view)
    view.clear_items()
    view.add_item(recording_button("juego:otra", calls, "otro"))

    store.dispatch_view(BUTTON, "juego:doblar", click(events, "juego:doblar"))
    await settle()

    assert calls == []
    assert events == ["response.defer"]


async def test_click_without_any_view_gets_a_private_notice() -> None:
    events: list[str] = []
    interaction = click(events, "juego:de-antes-del-reinicio")

    ViewStore(MagicMock()).dispatch_view(BUTTON, "juego:de-antes-del-reinicio", interaction)
    await settle()

    assert events == ["response.send_message"]
    text = interaction.response.send_message.await_args.args[0]
    assert text in EXPIRED_CLICK
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_click_on_stopped_view_gets_a_private_notice() -> None:
    calls: list[str] = []
    events: list[str] = []
    view = discord.ui.View()
    view.add_item(recording_button("juego:fin", calls, "boton"))
    store = store_with(view)
    view.stop()

    store.dispatch_view(BUTTON, "juego:fin", click(events, "juego:fin"))
    await settle()

    assert calls == []
    assert events == ["response.send_message"]


async def test_live_view_is_left_to_discord_py() -> None:
    calls: list[str] = []
    events: list[str] = []
    view = discord.ui.View()
    view.add_item(recording_button("juego:vivo", calls, "boton"))
    store = store_with(view)

    store.dispatch_view(BUTTON, "juego:vivo", click(events, "juego:vivo"))
    await settle()

    assert calls == ["boton"]
    assert events == ["response.defer"]


async def test_claimed_custom_ids_are_left_to_their_listener() -> None:
    events: list[str] = []
    claim_custom_ids("escucha:")

    ViewStore(MagicMock()).dispatch_view(BUTTON, "escucha:1:2:3", click(events, "escucha:1:2:3"))
    await settle()

    assert events == []


def test_dynamic_items_are_left_to_discord_py() -> None:
    class Dynamic(discord.ui.DynamicItem[discord.ui.Button], template=r"dinamico:(?P<n>[0-9]+)"):
        pass

    store = ViewStore(MagicMock())
    store.add_dynamic_items(Dynamic)

    assert rescue_click(store, BUTTON, "dinamico:7", click([], "dinamico:7")) is None


async def test_orphan_modal_gets_a_private_notice() -> None:
    events: list[str] = []
    interaction = click(events, "formulario-viejo")

    ViewStore(MagicMock()).dispatch_modal("formulario-viejo", interaction, [], {})
    await settle()

    assert events == ["response.send_message"]


async def test_a_button_that_raises_tells_who_clicked() -> None:
    events: list[str] = []
    view = discord.ui.View()
    button: discord.ui.Button = discord.ui.Button(label="Peta", custom_id="juego:peta")

    async def callback(interaction: discord.Interaction) -> None:
        raise RuntimeError("fallo de prueba")

    button.callback = callback  # type: ignore[method-assign]
    view.add_item(button)
    interaction = click(events, "juego:peta")

    store_with(view).dispatch_view(BUTTON, "juego:peta", interaction)
    await settle()

    assert events == ["response.send_message"]
    assert interaction.response.send_message.await_args.args[0] in BUTTON_ERROR
