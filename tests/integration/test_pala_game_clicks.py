"""Los clics del minijuego de `pala` se cuentan bien en la telemetría, con el bot real.

Los botones del minijuego van en el panel (`PalaPanel`), pero no los atiende la
vista sino el listener `Work.on_interaction` (ver la docstring de
`bot.cogs.work`). Así que discord.py le pasa cada clic a los dos: primero
programa la tarea de la vista (`ViewStore.dispatch_view`) y luego el evento
`interaction`. La vista lo rechaza en `interaction_check` sin contestar, y si la
telemetría lo contara ahí lo vería sin respuesta: el informe sacaba
`button:PalaPanel.Button` con el 100 % de errores aunque el listener contestaba.

Aquí se pulsa un botón del minijuego como lo despacha discord.py y se mira lo
que apunta la telemetría.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.app import INITIAL_EXTENSIONS, BotClient

GUILD_ID = 1
OWNER_ID = 10


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
    await client.telemetry_repository.initialize()
    await client.message_stats.initialize()
    await client.economy.repository.initialize()
    await client.casino_stats.initialize()
    await client.birthdays.initialize()
    await client.achievements.initialize()
    await client.welcome.initialize()
    await client.shop.initialize()
    await client.housing.initialize()
    await client.work.repository.initialize()
    await client.horses.initialize()
    await client.gallos.initialize()
    await client.enraya.initialize()
    await client.backgammon.initialize()
    await client.poker.initialize()
    await client.porras.initialize()
    await client.todo.initialize()
    await client.slots_repository.initialize()
    for extension in INITIAL_EXTENSIONS:
        await client.load_extension(extension)
    return client


def make_owner() -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    owner = MagicMock(spec=discord.Member)
    owner.id = OWNER_ID
    owner.bot = False
    owner.display_name = "Diego"
    owner.mention = f"<@{OWNER_ID}>"
    owner.guild = guild
    owner.roles = []
    return owner


def click(client: BotClient, owner: MagicMock, custom_id: str, interaction_id: int) -> MagicMock:
    interaction = fake_interaction(owner)
    interaction.client = client
    interaction.id = interaction_id
    interaction.guild_id = GUILD_ID
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": custom_id, "component_type": 2}
    interaction.message = None
    return interaction


def buttons(client: BotClient) -> dict[str, tuple[int, int]]:
    """Clics apuntados en memoria: nombre → (eventos, errores)."""
    counters = client.telemetry._counters
    return {
        key.name: (counter.events, counter.errors)
        for key, counter in counters.items()
        if key.kind == "button"
    }


async def start_shift(client: BotClient, owner: MagicMock):  # noqa: ANN201
    cog = client.get_cog("Trabajo")
    module = sys.modules[type(cog).__module__]
    panel = module.PalaPanel(cog, guild_id=GUILD_ID, owner=owner)
    cog.panels.add(panel)
    await panel._hire(fake_interaction(owner), "obra")
    await panel._clock_in(fake_interaction(owner))
    assert panel.shift is not None
    return cog, module, panel


def game_button(module, panel) -> discord.ui.Button:  # noqa: ANN001
    return next(
        item
        for item in panel.walk_children()
        if isinstance(item, discord.ui.Button)
        and item.custom_id
        and module.parse_game_id(item.custom_id) is not None
    )


async def press_like_discord(cog, panel, button, interaction: MagicMock) -> None:  # noqa: ANN001
    """Como `ConnectionState.parse_interaction_create`: la vista y luego el evento."""
    view_task = asyncio.ensure_future(panel._scheduled_task(button, interaction))
    listener = asyncio.ensure_future(cog.on_interaction(interaction))
    await asyncio.wait_for(asyncio.gather(view_task, listener), timeout=3)


@pytest.mark.asyncio
async def test_un_clic_del_minijuego_contesta_y_no_cuenta_como_error(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        owner = make_owner()
        cog, module, panel = await start_shift(client, owner)
        client.telemetry._counters.clear()
        button = game_button(module, panel)
        interaction = click(client, owner, button.custom_id, 1)

        await press_like_discord(cog, panel, button, interaction)

        assert interaction.response.is_done()
        assert buttons(client) == {"PalaPanel.game_click": (1, 0)}
    finally:
        for panel in list(cog.panels):
            panel.stop()
        await client.close()


@pytest.mark.asyncio
async def test_un_clic_de_un_panel_caducado_contesta_y_se_cuenta(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        owner = make_owner()
        cog = client.get_cog("Trabajo")
        module = sys.modules[type(cog).__module__]
        interaction = click(client, owner, module.game_id(999, 0, 0), 1)

        await cog.on_interaction(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert buttons(client) == {"PalaPanel.game_click": (1, 0)}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fichar_dos_veces_seguidas_contesta_sin_esperar_al_primero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El segundo clic en ⛏️ Fichar no espera al candado sin contestar a Discord."""
    client = await load_bot(tmp_path)
    cog = client.get_cog("Trabajo")
    try:
        owner = make_owner()
        module = sys.modules[type(cog).__module__]
        panel = module.PalaPanel(cog, guild_id=GUILD_ID, owner=owner)
        cog.panels.add(panel)
        await panel._hire(fake_interaction(owner), "obra")
        started = asyncio.Event()
        release = asyncio.Event()
        start_shift = cog.service.start_shift

        async def slow_start(*args: object, **kwargs: object):  # noqa: ANN202
            started.set()
            await release.wait()
            return await start_shift(*args, **kwargs)

        monkeypatch.setattr(cog.service, "start_shift", slow_start)
        first, second = fake_interaction(owner), fake_interaction(owner)
        first_click = asyncio.ensure_future(panel._clock_in(first))
        await started.wait()
        second_click = asyncio.ensure_future(panel._clock_in(second))
        await asyncio.sleep(0)

        assert second.response.is_done()
        release.set()
        await asyncio.gather(first_click, second_click)
        assert panel.shift is not None
    finally:
        for panel in list(cog.panels):
            panel.stop()
        await client.close()
