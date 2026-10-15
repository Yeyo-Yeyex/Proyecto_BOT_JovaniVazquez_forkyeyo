"""Pruebas del cog `pisos`: el panel en imágenes, la navegación, la caja y cada noche."""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from interaction_fakes import fake_interaction

from bot.cogs.pisos import BARRIO, CAJA, CARTERA, FICHA, MAPA, Painter, Panel, Pisos, SharesModal
from bot.repositories.economy import EconomyRepository, LedgerEntry
from bot.repositories.housing import HousingRepository
from bot.services import housing as rules
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.housing_catalog import BARRIOS, listings_of
from bot.services.housing_market import HousingService
from bot.services.levels import TIMEZONE

GUILD = 1


class FakeScene:
    """Pinta «PNG» de mentira y cuenta cuántas pantallas le piden."""

    def __init__(self) -> None:
        self.painted: list[dict[str, Any]] = []

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes]:
        self.painted.extend(states)
        return [f"png:{state['screen']}:{len(self.painted)}".encode() for state in states]

    async def close(self) -> None:
        return None


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now


def member(user_id: int, name: str) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.bot = False
    return user


async def make(tmp_path: Path) -> tuple[Pisos, FakeScene, Clock]:
    clock = Clock(datetime(2026, 10, 14, 12, tzinfo=TIMEZONE))
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    housing = HousingRepository(tmp_path / "bot.db")
    await housing.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    service = HousingService(housing, economy, clock=clock, rng=random.Random(3))
    scene = FakeScene()
    cog = Pisos(MagicMock(), economy=economy, repository=housing, service=service,
                renderer=scene)  # type: ignore[arg-type]  # fmt: skip
    return cog, scene, clock


def guild_with(*members: MagicMock) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    by_id = {m.id: m for m in members}
    guild.get_member = by_id.get
    return guild


async def open_panel(cog: Pisos, owner: MagicMock, guild: MagicMock) -> Panel:
    ctx = MagicMock()
    ctx.guild = guild
    ctx.author = owner
    ctx.channel = MagicMock()
    ctx.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    await cog.pisos_text.callback(cog, ctx)  # type: ignore[attr-defined]
    kwargs = ctx.send.await_args.kwargs
    assert isinstance(kwargs["file"], discord.File)
    assert kwargs.get("embed") is None  # solo imagen
    panel = kwargs["view"]
    assert isinstance(panel, Panel)
    return panel


def clicked(user: MagicMock, events: list[str], **data: Any) -> MagicMock:
    interaction = fake_interaction(user, events=events)
    interaction.data = data
    interaction.channel = MagicMock()
    return interaction


async def test_el_panel_abre_el_mapa_y_pinta_los_barrios_por_adelantado(tmp_path: Path) -> None:
    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))

    assert panel.screen == MAPA
    assert scene.painted[0]["screen"] == "mapa"
    await cog.painter._worker  # el adelanto: los 20 barrios y la cartera
    screens = [s["screen"] for s in scene.painted[1:]]
    assert screens.count("barrio") == len(BARRIOS) and "cartera" in screens


async def test_navegar_contesta_antes_de_pintar_y_no_resube_lo_que_ya_se_ve(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    events: list[str] = []

    barrio = BARRIOS[4].key
    interaction = clicked(ana, events, values=[barrio])
    await panel._pick_barrio(interaction)

    assert events[0] == "response.defer"
    assert panel.screen == BARRIO and panel.barrio == barrio
    assert "attachments" in interaction.edit_original_response.await_args.kwargs

    again = clicked(ana, [], values=[barrio])
    await panel._pick_barrio(again)
    assert "attachments" not in again.edit_original_response.await_args.kwargs

    slot = clicked(ana, [], custom_id="pisos:ver:2")
    await panel._open_listing(slot)
    assert panel.screen == FICHA and panel.listing == listings_of(barrio)[2].key


async def test_solo_el_duenio_mueve_su_panel(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    panel = await open_panel(cog, ana, guild_with(ana, bea))
    interaction = fake_interaction(bea)
    assert await panel.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once()


async def test_comprar_por_la_caja_cobra_y_sella_pagado(tmp_path: Path) -> None:
    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.barrio = "schamann"
    await panel._open_listing(clicked(ana, [], custom_id="pisos:ver:1"))
    assert panel.listing == "schamann_bajo"

    form = fake_interaction(ana)
    await panel._buy(form)
    modal = form.response.send_modal.await_args.args[0]
    assert isinstance(modal, SharesModal) and modal.most == rules.SHARES

    modal.amount._value = "40"  # lo que escribe en el formulario
    submit = clicked(ana, [])
    await modal.on_submit(submit)
    assert panel.screen == CAJA and panel.pending == (False, 40)
    preview = scene.painted[-1] if scene.painted[-1]["screen"] == "caja" else None
    assert preview is None or preview["stamp"] is None

    pay = clicked(ana, [])
    await panel._pay(pay)

    assert panel.done is not None and panel.done[0] == "PAGADO"
    market = await cog.service.market(GUILD)
    assert market.shares_of("schamann_bajo") == {ana.id: 40}
    total = panel.done[1].quote.total
    assert await cog.economy.balance(GUILD, ana.id) == STARTING_BALANCE + 5_000_000 - total
    painted = [s for s in scene.painted if s["screen"] == "caja" and s["stamp"] == "PAGADO"]
    assert painted

    await panel._go_portfolio(clicked(ana, []))
    assert panel.screen == CARTERA


async def test_sin_dinero_la_caja_avisa_y_no_compra(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "adeje_chalet"
    panel.screen, panel.pending = CAJA, (False, rules.SHARES)
    pay = clicked(ana, [])

    await panel._pay(pay)

    assert panel.done is None
    assert "No te llega" in pay.followup.send.await_args.args[0]
    assert (await cog.service.market(GUILD)).holdings == {}


async def test_el_pintor_no_pinta_dos_veces_la_misma_pantalla() -> None:
    scene = FakeScene()
    painter = Painter(scene)  # type: ignore[arg-type]
    state = {"screen": "mapa", "x": 1}
    first = await painter.paint([state])
    second = await painter.paint([dict(state)])
    assert first == second and len(scene.painted) == 1


async def test_cada_noche_se_cierran_los_dias_sin_romper_nada(tmp_path: Path) -> None:
    cog, _scene, clock = await make(tmp_path)
    await cog.economy.repository.apply(GUILD, 10, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, 10, "taco_bajo", 100)
    clock.now += 86_400

    first = await cog.close_guild(GUILD)
    again = await cog.close_guild(GUILD)
    clock.now += 2 * 86_400
    later = await cog.close_guild(GUILD)

    # La primera vez solo se cierra ayer; luego, todos los días que falten.
    assert [r.day.day for r in first] == [14] and again == []
    assert [r.day.day for r in later] == [15, 16]
    assert (await cog.service.market(GUILD)).leases["taco_bajo"].arrears >= 0


async def test_la_funcion_puente_del_patrimonio_no_rompe_sin_el_cog() -> None:
    from bot.cogs import pisos

    bot = SimpleNamespace(get_cog=lambda _name: None)
    assert await pisos.fiscal_of(bot, GUILD, 10) == 0  # type: ignore[arg-type]


async def test_un_segundo_clic_en_pagar_no_vuelve_a_cobrar(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana))
    await panel.refresh()
    panel.listing, panel.screen, panel.pending = "taco_trastero", CAJA, (False, 10)

    await panel._pay(clicked(ana, []))
    balance = await cog.economy.balance(GUILD, ana.id)
    await panel._pay(clicked(ana, []))

    assert await cog.economy.balance(GUILD, ana.id) == balance
    assert (await cog.service.market(GUILD)).shares_of("taco_trastero") == {ana.id: 10}


async def test_si_otro_compra_en_el_barrio_la_caja_se_actualiza_y_no_cobra(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    for user in (ana, bea):
        await cog.economy.repository.apply(GUILD, user.id, [LedgerEntry(5_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana, bea))
    await panel.refresh()
    panel.listing, panel.screen, panel.pending = "taco_trastero", CAJA, (False, 10)
    await cog.service.buy(GUILD, bea.id, "taco_piso3", 100)  # sube el índice de Taco
    before = await cog.economy.balance(GUILD, ana.id)
    pay = clicked(ana, [])

    await panel._pay(pay)

    assert panel.done is None
    assert "ha cambiado" in pay.followup.send.await_args.args[0]
    assert await cog.economy.balance(GUILD, ana.id) == before
    await panel._pay(clicked(ana, []))  # con la caja ya actualizada, sí
    assert panel.done is not None


async def test_un_fallo_en_un_servidor_no_para_la_tarea_de_cada_noche(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    broken = AsyncMock(side_effect=[RuntimeError("roto"), []])
    cog.close_guild = broken  # type: ignore[method-assign]
    cog.bot.guilds = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    await cog._nightly.coro(cog)
    assert broken.await_count == 2


async def test_todas_las_pantallas_respetan_los_limites_de_discord(tmp_path: Path) -> None:
    """Etiquetas, opciones y filas: Discord rechaza el mensaje entero si una se pasa."""
    from bot.services.housing_catalog import LISTINGS

    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10**9, "prueba")])
    for listing in LISTINGS[:30]:
        await cog.service.buy(GUILD, ana.id, listing.key, 60)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_leases SET status = 'impago', tenant = 'familia'")
    panel = await open_panel(cog, ana, guild_with(ana))
    await panel.refresh()
    for screen, pending in ((MAPA, None), (BARRIO, None), (FICHA, None), (CARTERA, None),
                            (CAJA, (False, 10)), (CAJA, (True, 10))):  # fmt: skip
        panel.screen, panel.pending, panel.done = screen, pending, None
        panel.barrio, panel.listing = "vegueta", "vegueta_edificio"
        panel.rebuild()
        rows: dict[int, int] = {}
        for item in panel.children:
            rows[item.row or 0] = rows.get(item.row or 0, 0) + (
                5 if isinstance(item, discord.ui.Select) else 1
            )
            if isinstance(item, discord.ui.Button):
                assert len(item.label or "") <= 80
                assert len(item.custom_id or "") <= 100
            if isinstance(item, discord.ui.Select):
                assert 1 <= len(item.options) <= 25
                assert len(item.placeholder or "") <= 150
                for option in item.options:
                    assert len(option.label) <= 100
                    assert len(option.description or "") <= 100
        assert len(panel.children) <= 25 and max(rows) <= 4
        assert all(width <= 5 for width in rows.values()), (screen, rows)
    for selling, most in ((False, 100), (True, 60)):
        modal = SharesModal(panel, selling=selling, most=most)
        assert len(modal.title) <= 45
        assert len(modal.amount.label) <= 45
        assert len(modal.amount.placeholder or "") <= 100


async def test_abrir_otro_panel_para_el_anterior_del_mismo_miembro(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    guild = guild_with(ana, bea)
    first = await open_panel(cog, ana, guild)
    other = await open_panel(cog, bea, guild)
    second = await open_panel(cog, ana, guild)
    assert first.is_finished() and first not in cog.panels
    assert second in cog.panels and other in cog.panels
