"""Pruebas del cog `pisos`: el panel en imágenes, la navegación, la caja y cada noche."""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from interaction_fakes import fake_interaction

from bot.cogs.pisos import (
    BARRIO,
    CAJA,
    CARTERA,
    FICHA,
    MAPA,
    VPO,
    Painter,
    Panel,
    Pisos,
    SharesModal,
    TenantOffer,
    ecb_embed,
    effort_text,
    promotion_embed,
    requirements_text,
)
from bot.repositories.economy import EconomyRepository, LedgerEntry
from bot.repositories.housing import DayRun, HousingRepository, MemberDay, News
from bot.repositories.housing_finance import HOME_RENTED
from bot.services import housing as rules
from bot.services.achievements import PISOS_EVENT_PREFIX
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


async def test_el_adelanto_pinta_de_uno_en_uno_y_lo_mas_probable_primero(tmp_path: Path) -> None:
    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", 100)
    sizes: list[int] = []
    paint = scene.paint

    async def counted(states: list[dict[str, Any]]) -> list[bytes]:
        sizes.append(len(states))
        await asyncio.sleep(0)  # un dibujo de verdad deja pasar a los demás
        return await paint(states)

    scene.paint = counted  # type: ignore[method-assign]
    await open_panel(cog, ana, guild_with(ana))
    await cog.painter._worker

    assert set(sizes) == {1}  # nunca varias pantallas de golpe
    order = [s["screen"] for s in scene.painted[1:]]
    assert order[:2] == ["cartera", "vpo"]  # lo que se pulsa más, primero
    assert scene.painted[3]["barrio"]["key"] == "taco"  # y los barrios donde ya tienes algo


async def test_un_clic_sobre_lo_que_se_pinta_por_adelantado_espera_y_no_lo_pinta_otra_vez() -> None:
    scene, gate = FakeScene(), asyncio.Event()
    paint = scene.paint

    async def held(states: list[dict[str, Any]]) -> list[bytes]:
        await gate.wait()
        return await paint(states)

    scene.paint = held  # type: ignore[method-assign]
    painter = Painter(scene)  # type: ignore[arg-type]
    state = {"screen": "mapa", "x": 1}
    painter.prefetch([state])
    await asyncio.sleep(0)  # el adelanto ya está pintando (parado en la puerta)
    click = asyncio.create_task(painter.paint([dict(state)]))
    await asyncio.sleep(0)
    assert not click.done()

    gate.set()
    assert await click == await painter.paint([state])
    assert len(scene.painted) == 1


async def test_el_adelanto_suelta_lo_que_ya_no_vale() -> None:
    scene, valid = FakeScene(), {"yes": True}
    paint = scene.paint

    async def changing(states: list[dict[str, Any]]) -> list[bytes]:
        valid["yes"] = False  # el panel cambia de pantalla mientras se pinta la primera
        return await paint(states)

    scene.paint = changing  # type: ignore[method-assign]
    painter = Painter(scene)  # type: ignore[arg-type]
    painter.prefetch(
        [{"screen": "a"}, {"screen": "b"}, {"screen": "c"}], still=lambda: valid["yes"]
    )
    assert painter._worker is not None
    await painter._worker
    assert [s["screen"] for s in scene.painted] == ["a"]


async def test_desde_la_ficha_y_la_caja_se_pinta_antes_el_mapa_la_cartera_y_la_ficha(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "taco_bajo"
    panel.screen = FICHA
    assert (MAPA, {}) in panel._neighbours() and (CARTERA, {}) in panel._neighbours()
    panel.screen = BARRIO
    panel.barrio = "taco"
    assert [s for s, _ in panel._neighbours()].count(FICHA) == 3 and (
        CARTERA,
        {},
    ) in panel._neighbours()
    panel.screen = CAJA
    assert panel._neighbours() == [(FICHA, {"listing": "taco_bajo"}), (CARTERA, {}), (MAPA, {})]


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
                            (CAJA, (False, 10)), (CAJA, (True, 10)), (VPO, None)):  # fmt: skip
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


def salary(tmp_path: Path, user_id: int, net_per_day: int, clock: Clock) -> None:
    """Nóminas de los últimos 30 días que suman `net_per_day` × 30 (lo que mira el banco)."""
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
            "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, ?, ?)",
            (GUILD, user_id, net_per_day * 30, net_per_day * 30, clock.now - 3600),
        )


def ids(panel: Panel) -> list[str]:
    return [getattr(item, "custom_id", "") for item in panel.children]


async def test_la_caja_compra_con_hipoteca_y_luego_se_amortiza_con_dos_clics(
    tmp_path: Path,
) -> None:
    cog, scene, clock = await make(tmp_path)
    ana = member(10, "Ana")
    salary(tmp_path, ana.id, 20_000, clock)
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(1_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "schamann_bajo"
    await panel.open_checkout(clicked(ana, []), selling=False, shares=rules.SHARES)
    assert "pisos:financiar" in ids(panel)

    await panel._toggle_mortgage(clicked(ana, []))
    assert panel.financed
    labels = [line["label"] for line in scene.painted[-1]["lines"]]
    assert "Sale de tu bolsillo" in labels
    await panel._pay(clicked(ana, []))

    purchase = panel.done[1]  # type: ignore[index]
    assert purchase.loan == round(purchase.quote.price * rules.MORTGAGE_LTV)
    pocket = purchase.quote.total - purchase.loan
    assert await cog.economy.balance(GUILD, ana.id) == STARTING_BALANCE + 1_000_000 - pocket

    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10_000_000, "prueba")])
    await panel._back_to_listing(clicked(ana, []))
    assert "pisos:amortizar" in ids(panel)
    arm = clicked(ana, [], custom_id="pisos:amortizar")
    await panel._arm(arm)
    arm.response.edit_message.assert_awaited_once()
    assert "pisos:amortizar:si" in ids(panel)  # el primer clic no cobra
    assert (await cog.service.market(GUILD)).mortgages_of(ana.id)

    await panel._amortize(clicked(ana, []))
    assert not (await cog.service.market(GUILD)).mortgages_of(ana.id)
    assert panel.confirm is None and "pisos:amortizar" not in ids(panel)


async def test_sin_nomina_la_hipoteca_avisa_y_no_compra(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "schamann_bajo"
    panel.screen, panel.pending, panel.financed = CAJA, (False, rules.SHARES), True
    pay = clicked(ana, [])

    await panel._pay(pay)

    assert panel.done is None
    assert "banco" in pay.followup.send.await_args.args[0]
    assert (await cog.service.market(GUILD)).holdings == {}


async def test_la_vpo_se_apunta_se_sortea_y_se_compra_desde_su_pantalla(tmp_path: Path) -> None:
    cog, scene, clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    await panel._go_vpo(clicked(ana, []))
    shown = scene.painted[-1]
    assert shown["screen"] == "vpo" and shown["next_draw"] == "lunes 19/10"
    assert "pisos:sorteo" in ids(panel)

    await panel._vpo_signup(clicked(ana, []))
    assert ana.id in (await cog.service.market(GUILD)).signups
    clock.now += 6 * 86_400  # martes 20: se cierra el lunes 19, que sortea
    (run,) = await cog.close_guild(GUILD)
    assert run.draws and run.draws[0][0] == ana.id

    salary(tmp_path, ana.id, 6_000, clock)
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(50_000, "notaría")])
    await panel._go_vpo(clicked(ana, []))
    await panel._arm(clicked(ana, [], custom_id="pisos:comprar_vpo"))
    buy = clicked(ana, [])
    await panel._vpo_buy(buy)
    market = await cog.service.market(GUILD)
    assert market.vpo_of(ana.id) == run.draws[0][1]
    assert "pisos:devolver" in ids(panel)

    # El Estado la recompra con lo que cobró al venderla.
    await panel._arm(clicked(ana, [], custom_id="pisos:devolver"))
    await panel._vpo_return(clicked(ana, []))
    market = await cog.service.market(GUILD)
    assert market.vpo_of(ana.id) is None and ana.id not in market.homes
    assert "pisos:sorteo" in ids(panel)


async def test_alquilar_a_un_miembro_le_pide_que_acepte(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    bea.mention = "<@20>"
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.economy.repository.apply(GUILD, bea.id, [LedgerEntry(100_000, "fianza")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    panel = await open_panel(cog, ana, guild_with(ana, bea))
    panel.barrio, panel.listing = "taco", "taco_bajo"
    await panel._back_to_listing(clicked(ana, []))
    assert {"pisos:vivir", "pisos:miembro", "pisos:licencia"} <= set(ids(panel))

    await panel._arm(clicked(ana, [], custom_id="pisos:miembro"))
    assert "pisos:inquilino" in ids(panel)
    pick = clicked(ana, [], values=[str(bea.id)])
    await panel._pick_tenant(pick)
    offer = pick.followup.send.await_args.kwargs["view"]
    assert isinstance(offer, TenantOffer) and offer.tenant is bea
    text = pick.followup.send.await_args.kwargs["embed"].description
    assert "nómina" in text and "garantía adicional" in text  # Bea no cobra nómina

    stranger = fake_interaction(ana)
    await offer.accept.callback(stranger)  # type: ignore[attr-defined]
    stranger.response.send_message.assert_awaited_once()  # no es para ella

    yes = clicked(bea, [])
    await offer.accept.callback(yes)  # type: ignore[attr-defined]
    market = await cog.service.market(GUILD)
    assert market.leases["taco_bajo"].member == bea.id
    assert market.homes[bea.id] == (HOME_RENTED, "taco_bajo")
    assert offer.is_finished()
    rent = market.leases["taco_bajo"].rent
    deposit = rules.deposit_for(rent, guarantee=True)
    assert await cog.economy.balance(GUILD, bea.id) == STARTING_BALANCE + 100_000 - deposit


def test_la_tasa_de_esfuerzo_habla_como_el_banco_de_espana() -> None:
    assert "sobreesfuerzo" in effort_text(4_000, 6_000)
    assert "unicornio" in effort_text(1_000, 6_000)
    assert "casero" in effort_text(9_000, 6_000)
    assert "nada" in effort_text(4_000, 0)
    assert "ni aval" in requirements_text(1_000, 6_000)
    assert "padres" in requirements_text(4_000, 6_000)


async def test_el_lunes_francfort_habla_en_el_canal_si_hay_mercado(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = guild_with(ana)
    guild.text_channels = [channel]
    cog.bot.get_guild = lambda _id: guild
    monday = datetime(2026, 10, 19).date()
    await cog._announce_ecb(GUILD, monday, 0.02, 0.0225)
    channel.send.assert_not_awaited()  # nadie tiene pisos: no se molesta a nadie

    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    await cog._announce_ecb(GUILD, monday, 0.02, 0.0225)
    embed = channel.send.await_args.kwargs["embed"]
    assert rules.ecb_phrase(0.02, 0.0225, monday) in embed.description
    assert "2,00 % → 2,25 %" in embed.description and "3,25 % fijo" in embed.description
    assert isinstance(ecb_embed(0.02, 0.02, monday), discord.Embed)


async def test_si_ejecutan_lo_que_avalaron_tus_padres_lo_paga_la_familia(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from bot.cogs import pisos

    cog, _scene, _clock = await make(tmp_path)
    run = DayRun(day=datetime(2026, 10, 20).date())
    run.aval_executions = [10]
    cog.service.close_days = AsyncMock(return_value=[run])  # type: ignore[method-assign]
    cog.tax_stats = AsyncMock(return_value={})  # type: ignore[method-assign]
    lost: list[int] = []
    noted: dict[int, Any] = {}

    async def lose(_bot: object, _guild: int, who: int) -> None:
        lost.append(who)

    def note(_bot: object, _guild: int, who: int, delta: Any, *_rest: object) -> None:
        noted[who] = delta

    monkeypatch.setattr(pisos.curro, "parents_lose_home", lose)
    monkeypatch.setattr(pisos.logros, "note", note)
    await cog.close_guild(GUILD)
    assert lost == [10]
    assert noted[10].add[f"{PISOS_EVENT_PREFIX}ejecucion_aval"] == 1


async def test_cada_noche_lo_personal_va_solo_a_su_miembro(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from bot.cogs import pisos

    cog, _scene, _clock = await make(tmp_path)
    run = DayRun(day=datetime(2026, 10, 19).date())
    run.news = [
        News("taco_bajo", run.day, "ejecucion", 1, 10, None),
        News("taco_bajo", run.day, "impago", 1, 30, None),  # 30: el miembro de alquiler
    ]
    run.owners = {"taco_bajo": (10, 20)}
    run.members = {20: MemberDay(rent=100)}
    run.mortgage_unpaid, run.bono, run.installments = [20], {30: 500}, {10: 1}
    cog.service.close_days = AsyncMock(return_value=[run])  # type: ignore[method-assign]
    cog.tax_stats = AsyncMock(return_value={})  # type: ignore[method-assign]
    noted: dict[int, Any] = {}

    def note(_bot: object, _guild: int, who: int, delta: Any, *_rest: object) -> None:
        noted[who] = delta

    monkeypatch.setattr(pisos.logros, "note", note)

    await cog.close_guild(GUILD)

    ev = PISOS_EVENT_PREFIX
    assert noted[10].add[f"{ev}ejecucion"] == 1 and noted[10].add["pisos_installments"] == 1
    assert f"{ev}ejecucion" not in noted[20].add
    assert noted[20].add[f"{ev}cuota_impagada"] == 1 and noted[20].add[f"{ev}impago"] == 1
    assert noted[30].add == {"pisos_bono": 500}


async def test_la_obra_nueva_se_anuncia_y_apunta_el_logro_del_promotor(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from bot.cogs import pisos

    cog, _scene, clock = await make(tmp_path)
    clock.now = datetime(2026, 10, 20, 12, tzinfo=TIMEZONE).timestamp()  # martes
    ana = member(10, "Ana")
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = guild_with(ana)
    guild.text_channels = [channel]
    cog.bot.get_guild = lambda _id: guild
    asked: list[int] = []

    async def builders(_bot: object, guild_id: int) -> tuple[int, ...]:
        asked.append(guild_id)
        return (ana.id,)

    noted: list[tuple[int, Any]] = []

    def note(_bot: object, _guild: int, who: int, delta: Any, *_rest: object) -> None:
        noted.append((who, delta))

    monkeypatch.setattr(pisos.curro, "ladrillo_builders", builders)
    monkeypatch.setattr(pisos.logros, "note", note)
    runs = await cog.close_guild(GUILD)  # cierra el lunes 19
    assert asked == [GUILD]
    (run,) = [r for r in runs if r.promotions]
    embed = channel.send.await_args_list[-1].kwargs["embed"]
    assert "Ana" in embed.description and "5 %" in embed.description
    assert any(who == ana.id and d.add.get("pisos_promotions") == 1 for who, d in noted)
    listing = (await cog.service.market(GUILD)).listing(run.promotions[0][1])
    assert listing is not None
    assert promotion_embed(listing, 1.0, "Ana").title.endswith(listing.title.split(": ")[1])
