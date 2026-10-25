"""💳 Financiar con el bot real: de la caja de la tienda a la oferta del banco y vuelta.

La oferta (`banco_compras`) busca el cog del banco y compra con la caja de la tienda: lo que
cruza de un cog a otro se prueba con `BotClient` e `INITIAL_EXTENSIONS` (CLAUDE.md).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction
from render_fakes import use_fake_drawings

import bot.repositories.sqlite as sqlite_module
from bot.app import BotClient
from bot.cogs.banco_compras import FinanceView
from bot.repositories.housing_finance import LOAN_BNPL
from bot.services import credit
from bot.services.economy import STARTING_BALANCE
from bot.services.shop import emblematic, quote
from tests.integration.test_button_speed import GUILD_ID, OWNER_ID, load_bot, make_owner, module_of


def _interaction(client: BotClient, owner: MagicMock, events: list[str] | None = None) -> MagicMock:
    interaction = fake_interaction(owner, events=events)
    interaction.client = client
    interaction.guild = owner.guild
    interaction.guild_id = GUILD_ID
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.channel.send = AsyncMock()
    return interaction


async def _checkout(client: BotClient, owner: MagicMock, top: int):  # noqa: ANN202
    """Una caja con un artículo que se financia en Paga en 3 (sin nómina no cabe otro)."""
    tienda = client.get_cog("Tienda")
    await tienda.stock_up(GUILD_ID)
    now = tienda.clock()
    item = next(
        i
        for i in await client.shop.items(GUILD_ID)
        if not emblematic(i)
        and credit.BNPL_MIN <= quote(i, now).total <= top
        and not i.min_level
        and i.visible
        and i.role_id is None
    )
    checkout = module_of(client, "Tienda").Checkout(
        tienda, owner.guild, owner, item, quote(item, now), balance=STARTING_BALANCE, note=None
    )
    return tienda, item, checkout


def _finance_view(interaction: MagicMock) -> FinanceView:
    view = interaction.edit_original_response.call_args.kwargs["view"]
    assert isinstance(view, FinanceView)
    return view


async def test_financiar_desde_la_caja_compra_y_deja_la_cuota(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        owner = make_owner()
        tienda, item, checkout = await _checkout(client, owner, credit.BNPL_MAX)
        buttons = [c.custom_id for c in checkout.walk_children() if hasattr(c, "custom_id")]
        assert "caja:financiar" in buttons

        opened = _interaction(client, owner)
        await checkout._finance(opened)
        offer = _finance_view(opened)
        assert offer.plan is credit.Plan.BNPL and offer.problem() is None

        signed = _interaction(client, owner)
        await offer._sign(signed)

        assert checkout.done and checkout.paid is not None
        assert signed.edit_original_response.call_args.kwargs["view"] is checkout
        housing = client.get_cog("Pisos").service
        (loan,) = (await housing.market(GUILD_ID)).loans_of(OWNER_ID)
        assert loan.kind == LOAN_BNPL and loan.balance == checkout.price.total
        assert credit.ipc(GUILD_ID) > 0  # tras comprar, el IPC del crédito ya cuenta lo financiado
        # Lo financiado entró y salió: sin contar los premios de los logros, el monedero se
        # queda como estaba.
        with sqlite3.connect(tmp_path / "bot.sqlite3") as connection:
            (rewards,) = connection.execute(
                "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger "
                "WHERE guild_id = ? AND user_id = ? AND reason LIKE 'logro:%'",
                (GUILD_ID, OWNER_ID),
            ).fetchone()
        assert await client.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE + rewards
        entries = await client.shop.inventory(GUILD_ID, OWNER_ID, tienda.clock())
        assert any(entry.item_id == item.id for entry in entries)
    finally:
        await client.close()


async def test_si_la_compra_falla_la_financiacion_se_deshace(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        owner = make_owner()
        tienda, item, checkout = await _checkout(client, owner, credit.BNPL_MAX)
        opened = _interaction(client, owner)
        await checkout._finance(opened)
        offer = _finance_view(opened)
        # Entre abrir la oferta y firmar, el precio cambia: la caja no cobra.
        await client.shop.update_item(GUILD_ID, item.id, price=item.price + 1)

        signed = _interaction(client, owner)
        await offer._sign(signed)

        assert not checkout.done
        assert "no debes nada" in (offer.notice or "")
        housing = client.get_cog("Pisos").service
        assert (await housing.market(GUILD_ID)).loans_of(OWNER_ID) == []
        assert await client.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    finally:
        await client.close()


async def test_abrir_la_oferta_y_firmar_contestan_antes_de_la_base_de_datos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_drawings(monkeypatch)
    client = await load_bot(tmp_path)
    try:
        owner = make_owner()
        _tienda, _item, checkout = await _checkout(client, owner, credit.BNPL_MAX)
        events: list[str] = []
        real_connect = sqlite_module.connect

        def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            events.append("db")
            return real_connect(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(sqlite_module, "connect", connect)
        opened = _interaction(client, owner, events)
        await checkout._finance(opened)
        assert "db" in events and events[0].startswith("response."), events[:4]

        events.clear()
        signed = _interaction(client, owner, events)
        await _finance_view(opened)._sign(signed)
        assert "db" in events and events[0].startswith("response."), events[:4]
    finally:
        await client.close()
