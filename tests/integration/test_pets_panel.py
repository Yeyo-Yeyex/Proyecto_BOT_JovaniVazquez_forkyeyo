"""El panel de `mascota` con el bot real y el `ViewStore` de discord.py.

Un cuidado que se rechaza (la mascota pide un respiro) no edita el panel. Si
aun así rehiciera sus botones, discord.py seguiría apuntando a los viejos, ya
sin vista, y tiraría sin contestar todos los clics siguientes: «Esta
interacción ha fallado» hasta volver a abrir `mascota`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import discord
from discord.ui.view import ViewStore
from interaction_fakes import fake_interaction
from test_button_speed import GUILD_ID, OWNER_ID, load_bot, make_owner

MESSAGE_ID = 77
BUTTON = discord.ComponentType.button.value


async def click(store: ViewStore, owner: MagicMock, custom_id: str) -> tuple[MagicMock, list[str]]:
    """Un clic como lo manda Discord, por `ViewStore.dispatch_view`, hasta que acaba."""
    events: list[str] = []
    interaction = fake_interaction(owner, events=events)
    interaction.message = MagicMock(id=MESSAGE_ID)
    interaction.data = {"custom_id": custom_id, "component_type": BUTTON}
    interaction.guild_id = GUILD_ID
    interaction.client = MagicMock(telemetry=None)

    async def edited(*args: object, view: object = None, **kwargs: object) -> None:
        events.append("edit_original_response")
        # Como discord.py: editar con una vista la vuelve a registrar.
        if isinstance(view, discord.ui.LayoutView) and not view.is_finished():
            store.add_view(view, MESSAGE_ID)

    interaction.edit_original_response.side_effect = edited
    store.dispatch_view(BUTTON, custom_id, interaction)
    for _ in range(100):
        if any(e != "response.defer" for e in events):
            break
        await asyncio.sleep(0.02)
    return interaction, events


def live(store: ViewStore, custom_id: str) -> bool:
    """Si discord.py tiene ese botón del panel con su vista (y no uno huérfano)."""
    item = store._views[MESSAGE_ID][(BUTTON, custom_id)]
    return item.view is not None


async def test_un_cuidado_rechazado_no_deja_el_panel_sin_responder(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.pets.initialize()
    try:
        owner = make_owner()
        cog = client.get_cog("Mascotas")
        module = sys.modules[type(cog).__module__]
        await client.shop.stock_catalog(GUILD_ID, module.CATALOG, cog.clock())
        item = await client.shop.item_by_key(GUILD_ID, "mascota_gato")
        assert item is not None
        await client.shop.grant(GUILD_ID, OWNER_ID, item, cog.clock())
        panel = await cog.panel(owner.guild, owner, owner)
        store = client._connection._view_store
        store.add_view(panel, MESSAGE_ID)

        _, events = await click(store, owner, "mascota:cuidar:acariciar")
        assert events == ["response.defer", "edit_original_response"]

        # Otra caricia seguida: pide un respiro y el panel no se edita.
        rejected, events = await click(store, owner, "mascota:cuidar:acariciar")
        assert events == ["response.defer", "followup.send"]
        assert "respiro" in rejected.followup.send.await_args.args[0]
        assert all(
            live(store, f"mascota:{name}")
            for name in ("cuidar:acariciar", "cuidar:jugar", "cuidar:comer", "activar", "nombre")
        )

        _, events = await click(store, owner, "mascota:cuidar:jugar")
        assert events == ["response.defer", "edit_original_response"]
    finally:
        await client.close()


async def test_los_atajos_abren_la_tienda_en_las_cosas_de_mascotas(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.pets.initialize()
    try:
        owner = make_owner()
        cog = client.get_cog("Mascotas")
        module = sys.modules[type(cog).__module__]
        await client.shop.stock_catalog(GUILD_ID, module.CATALOG, cog.clock())
        item = await client.shop.item_by_key(GUILD_ID, "mascota_gato")
        await client.shop.grant(GUILD_ID, OWNER_ID, item, cog.clock())
        panel = await cog.panel(owner.guild, owner, owner)
        for name, tab, aisle in (("adoptar", "mascota", None), ("comida", "todo", "animales")):
            button = next(
                b
                for b in panel.walk_children()
                if getattr(b, "custom_id", None) == f"mascota:{name}"
            )
            interaction = fake_interaction(owner)
            interaction.guild = owner.guild
            await button.callback(interaction)
            shop = interaction.edit_original_response.await_args.kwargs["view"]
            assert (shop.prefs.tab, shop.prefs.aisle) == (tab, aisle)
            assert interaction.response.defer.await_args.kwargs == {
                "ephemeral": True,
                "thinking": True,
            }
    finally:
        await client.close()
