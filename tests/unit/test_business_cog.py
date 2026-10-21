"""Pruebas del panel del negocio (`bot.cogs.business`) y del inicio de `pala` (`bot.cogs.work`).

Con los servicios de verdad sobre una base de datos temporal, los dos cogs montados en un
bot falso que sí los encuentra (`get_cog`) y una escena de mentira que devuelve un PNG por
pantalla (o `None`, para ver el panel en texto).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs.business import (
    CIERRE,
    CONFIRMAR,
    MONTAR,
    PORTADA,
    PREVIA,
    TURNO,
    Negocio,
    NegocioPanel,
    parse_game_id,
)
from bot.cogs.work import PalaPanel, Work
from bot.repositories.business import BusinessRepository
from bot.repositories.economy import EconomyRepository, LedgerEntry
from bot.repositories.work import WorkRepository
from bot.services import pala
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.business_games import COINS
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.negocio import BusinessService
from bot.services.pala import WorkService
from bot.services.work import Wish

GUILD = 1
OWNER = 10


class FakeScene:
    """Escena de mentira: apunta lo que pinta y devuelve un PNG de pega (o nada)."""

    def __init__(self, *, works: bool = True) -> None:
        self.works = works
        self.painted: list[dict[str, Any]] = []

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes] | None:
        self.painted.extend(states)
        if not self.works:
            return None
        return [f"png:{s['screen']}".encode() for s in states]

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def sin_azar_de_la_pala(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pala, "wish_for", lambda *args: Wish("no", "Nada.", lambda f: False))
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


def make_user(user_id: int = OWNER) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def click(user: MagicMock | None = None) -> MagicMock:
    interaction = fake_interaction(user or make_user())
    interaction.guild_id = GUILD
    interaction.message = MagicMock(edit=AsyncMock())
    interaction.data = {}
    return interaction


async def make(tmp_path: Path, *, works: bool = True) -> tuple[Work, Negocio, FakeScene]:
    database = tmp_path / "bot.db"
    economy_repo = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    economy = EconomyService(economy_repo)
    work_repo = WorkRepository(database)
    await work_repo.initialize()
    work = WorkService(work_repo, economy, rng=random.Random(1))
    repository = BusinessRepository(database)
    await repository.initialize()
    negocio = BusinessService(repository, economy, work, rng=random.Random(1))
    work.business = negocio
    bot = MagicMock()
    bot.birthdays = None
    cogs: dict[str, Any] = {}
    bot.get_cog = lambda name: cogs.get(name)
    scene = FakeScene(works=works)
    work_cog = Work(bot, work)
    business_cog = Negocio(bot, negocio, renderer=scene)  # type: ignore[arg-type]
    cogs["Trabajo"], cogs["Negocio"] = work_cog, business_cog
    return work_cog, business_cog, scene


async def open_home(cog: Work) -> tuple[PalaPanel, AsyncMock]:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await cog._open(
        guild=MagicMock(id=GUILD),
        channel=MagicMock(id=99, parent_id=None),
        user=make_user(),
        send=send,
        send_error=AsyncMock(),
    )
    return next(iter(cog.panels)), send


def labels(view: ui.View) -> list[str]:
    return [i.label or "" for i in view.walk_children() if isinstance(i, ui.Button)]


def button(view: ui.View, label: str) -> ui.Button:
    return next(
        b
        for b in view.walk_children()
        if isinstance(b, ui.Button) and (b.label or "").startswith(label)
    )


def galleries(view: ui.View) -> int:
    return sum(1 for i in view.walk_children() if isinstance(i, ui.MediaGallery))


def check_limits(view: ui.LayoutView) -> None:
    """Lo que Discord rechazaría: demasiados componentes, texto o botones por fila."""
    assert view.total_children_count <= 40
    text = "\n".join(i.content for i in view.walk_children() if isinstance(i, ui.TextDisplay))
    assert len(text) <= 4000
    for item in view.walk_children():
        if isinstance(item, ui.ActionRow):
            assert len(item.children) <= 5


async def opened_business(
    tmp_path: Path, *, works: bool = True
) -> tuple[Work, Negocio, NegocioPanel]:
    work_cog, business_cog, _scene = await make(tmp_path, works=works)
    await business_cog.service.economy.repository.apply(
        GUILD, OWNER, [LedgerEntry(2_000_000, "prueba")]
    )
    await business_cog.service.open(
        GUILD, OWNER, sector="cafeteria", barrio="triana", name="Bar Pepe", palette=0, emblem=0
    )
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await business_cog.service.buy(GUILD, OWNER, product.key, 2)
    home, _send = await open_home(work_cog)
    await button(home, "🏪 Negocio").callback(click())
    panel = next(iter(business_cog.panels))
    return work_cog, business_cog, panel


# -- El inicio ----------------------------------------------------------------------


async def test_el_inicio_pinta_el_dia_y_ofrece_buscar_curro_y_montar_negocio(
    tmp_path: Path,
) -> None:
    work_cog, _business_cog, scene = await make(tmp_path)
    panel, send = await open_home(work_cog)
    assert scene.painted[0]["screen"] == "dia"
    assert [s["kind"] for s in scene.painted[0]["slots"]] == ["libre"] * 8
    assert "💼 Buscar curro" in labels(panel) and "🏪 Montar negocio" in labels(panel)
    files = send.await_args.kwargs["files"]
    assert len(files) == 1 and files[0].filename == "pala.png"
    assert galleries(panel) == 1
    check_limits(panel)


async def test_con_curro_el_inicio_lleva_al_trabajo_y_el_trabajo_quita_la_imagen(
    tmp_path: Path,
) -> None:
    work_cog, _business_cog, scene = await make(tmp_path)
    await work_cog.service.hire(GUILD, OWNER, "obra")
    panel, _send = await open_home(work_cog)
    slots = [s["kind"] for s in scene.painted[0]["slots"]]
    assert slots == ["jornada"] * 4 + ["libre"] * 4
    interaction = click()
    await button(panel, "🪏 Trabajo").callback(interaction)
    assert interaction.edit_original_response.await_args.kwargs["attachments"] == []
    assert "🏠 Inicio" in labels(panel) and "⛏️ Fichar" in labels(panel)
    check_limits(panel)
    # Y vuelve al inicio con su imagen.
    back = click()
    await button(panel, "🏠 Inicio").callback(back)
    assert len(back.edit_original_response.await_args.kwargs["attachments"]) == 1


async def test_sin_pintor_el_inicio_sale_en_texto_con_las_franjas(tmp_path: Path) -> None:
    work_cog, _business_cog, _scene = await make(tmp_path, works=False)
    panel, send = await open_home(work_cog)
    assert galleries(panel) == 0
    assert send.await_args.kwargs["files"] == []
    text = "\n".join(i.content for i in panel.walk_children() if isinstance(i, ui.TextDisplay))
    assert "⬜" in text


async def test_la_jornada_se_reduce_desde_el_panel_del_curro(tmp_path: Path) -> None:
    work_cog, _business_cog, _scene = await make(tmp_path)
    await work_cog.service.hire(GUILD, OWNER, "obra")
    panel, _send = await open_home(work_cog)
    await button(panel, "🪏 Trabajo").callback(click())
    await panel._jornada(click(), "j:2")
    status = await work_cog.service.status(GUILD, OWNER)
    assert status is not None and status.contract.jornada_next == 2
    await panel._jornada(click(), "x:on")  # sin un «año» en el oficio no se puede
    status = await work_cog.service.status(GUILD, OWNER)
    assert status is not None and not status.contract.on_excedencia


# -- Montar -------------------------------------------------------------------------


async def test_montar_un_negocio_desde_el_inicio(tmp_path: Path) -> None:
    work_cog, business_cog, scene = await make(tmp_path)
    await business_cog.service.economy.repository.apply(
        GUILD, OWNER, [LedgerEntry(2_000_000, "prueba")]
    )
    home, _send = await open_home(work_cog)
    await button(home, "🏪 Montar negocio").callback(click())
    assert not work_cog.panels  # el mensaje pasa al panel del negocio
    panel = next(iter(business_cog.panels))
    assert panel.screen == MONTAR and scene.painted[-1]["screen"] == "montar"
    check_limits(panel)
    await panel._pick_sector(click(), "taller")
    assert panel.screen == PREVIA and panel.draft["barrio"]
    assert scene.painted[-1]["screen"] == "negocio" and scene.painted[-1]["mode"] == "preview"
    check_limits(panel)
    await panel._pick_palette(click(), "3")
    await panel._pick_emblem(click(), "5")
    await button(panel, "➡️ Seguir").callback(click())
    assert panel.screen == CONFIRMAR
    check_limits(panel)
    await button(panel, "✅ Montarlo").callback(click())
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.sector == "taller"
    assert (business.palette, business.emblem) == (3, 5)
    assert panel.screen == PORTADA
    check_limits(panel)


async def test_sin_dinero_montar_avisa_y_no_crea_nada(tmp_path: Path) -> None:
    work_cog, business_cog, _scene = await make(tmp_path)
    home, _send = await open_home(work_cog)
    await button(home, "🏪 Montar negocio").callback(click())
    panel = next(iter(business_cog.panels))
    await panel._pick_sector(click(), "cafeteria")
    await button(panel, "➡️ Seguir").callback(click())
    interaction = click()
    await button(panel, "✅ Montarlo").callback(interaction)
    assert "No te llega" in interaction.followup.send.await_args.args[0]
    assert await business_cog.service.business(GUILD, OWNER) is None


# -- La portada y el turno ------------------------------------------------------------


async def test_cada_pantalla_del_negocio_cabe_en_discord(tmp_path: Path) -> None:
    _work_cog, _business_cog, panel = await opened_business(tmp_path)
    assert panel.screen == PORTADA
    check_limits(panel)
    for name in ("📦 Almacén", "⬅️ Volver", "📊 Cuentas", "⬅️ Volver", "🎨 Rótulo"):
        await button(panel, name).callback(click())
        check_limits(panel)
    await panel._pick_product(click(), "bocadillo")
    check_limits(panel)


async def test_comprar_desde_el_almacen(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "📦 Almacén").callback(click())
    await panel._pick_product(click(), "zumo")
    before = (await business_cog.service.business(GUILD, OWNER)).stock["zumo"]  # type: ignore[union-attr]
    await button(panel, "🛒 5 lotes").callback(click())
    after = (await business_cog.service.business(GUILD, OWNER)).stock["zumo"]  # type: ignore[union-attr]
    assert after == before + 5 * SECTOR_BY_KEY["cafeteria"].product("zumo").pack  # type: ignore[union-attr]


async def play(panel: NegocioPanel) -> None:
    """Juega el turno pulsando los botones del minijuego como haría el dueño."""
    while panel.turn is not None:
        game = panel.turn.game
        client = game.current
        assert client is not None
        if game.sector.game == "comandas":
            actions = [f"p:{k}" for k, n in client.order.items() for _ in range(n)] + ["serve"]
        elif game.sector.game == "caja":
            due, actions = game.due(), ["thief"] if client.hidden else []
            for coin in COINS:
                while due >= coin:
                    actions.append(f"c:{coin}")
                    due -= coin
            actions.append("charge")
        else:
            actions = [f"d:{client.fault}", "q:0"]
        for action in actions:
            turn = panel.turn
            if turn is None:
                return
            # El botón tiene que estar en el panel con su `custom_id` de esta ronda.
            ids = [
                parse_game_id(b.custom_id or "")
                for b in panel.walk_children()
                if isinstance(b, ui.Button)
            ]
            assert (panel.token, turn.game.round, action) in ids or action in ("thief",)
            await panel.game_click(click(), turn.game.round, action)


async def test_un_turno_entero_cobra_y_acaba_en_el_tique(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.screen == TURNO and (GUILD, OWNER) in business_cog.working
    check_limits(panel)
    await play(panel)
    assert panel.screen == CIERRE and panel.result is not None
    assert panel.result.summary.sales > 0
    assert (GUILD, OWNER) not in business_cog.working
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.turns_today == 1
    assert business.sales_today == panel.result.summary.sales
    check_limits(panel)


async def test_el_primer_cliente_se_pinta_antes_de_abrir(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    painter = business_cog.painter
    assert painter._worker is not None
    await painter._worker
    scene: FakeScene = painter.scene  # type: ignore[assignment]
    ahead = [s for s in scene.painted if s["screen"] == "cliente"]
    assert len(ahead) == 1
    await button(panel, "🔑 Abrir").callback(click())
    # El cliente que sale es el que ya estaba pintado: no se vuelve a pintar.
    assert panel.scene_state() == ahead[0]
    assert scene.painted.count(ahead[0]) == 1


async def test_un_clic_de_una_ronda_vieja_no_cuenta(tmp_path: Path) -> None:
    _work_cog, _business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.turn is not None
    old = panel.turn.game.round
    await panel.game_click(click(), old, "p:cafe")
    await panel.game_click(click(), old + 50, "serve")  # de un cliente que ya no está
    assert panel.turn is not None and panel.turn.game.outcomes == []


async def test_mientras_atiendes_tu_negocio_no_se_ficha_en_la_pala(tmp_path: Path) -> None:
    work_cog, _business_cog, panel = await opened_business(tmp_path)
    await work_cog.service.hire(GUILD, OWNER, "obra")
    await button(panel, "🔑 Abrir").callback(click())
    work_panel = PalaPanel(work_cog, guild_id=GUILD, owner=make_user())
    interaction = click()
    await work_panel._clock_in(interaction)
    assert "negocio" in interaction.followup.send.await_args.args[0]
    assert work_panel.shift is None


async def test_sin_pintor_el_negocio_sale_en_texto(tmp_path: Path) -> None:
    _work_cog, _business_cog, panel = await opened_business(tmp_path, works=False)
    assert galleries(panel) == 0
    text = "\n".join(i.content for i in panel.walk_children() if isinstance(i, ui.TextDisplay))
    assert "Bar Pepe" in text and "Caja de hoy" in text


async def test_del_negocio_se_vuelve_al_inicio(tmp_path: Path) -> None:
    work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🏠 Inicio").callback(click())
    assert panel not in business_cog.panels
    home = next(iter(work_cog.panels))
    assert "🏪 Negocio" in labels(home)
