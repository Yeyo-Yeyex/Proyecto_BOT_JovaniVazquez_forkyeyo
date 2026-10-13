"""Pruebas de las skins: el registro, la compra, cambiarla y cómo se consiguen.

Las de la tienda usan la economía y la tienda reales sobre un SQLite temporal,
como `test_shop.py`. Las skins de temporada y de logro se prueban con skins de
mentira metidas en el registro (no hay ninguna real todavía).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from test_shop import (
    BUYER,
    GUILD,
    Clock,
    make_cog,
    make_guild,
    make_interaction,
    make_member,
    pay_for,
    texts,
)

from bot.cogs import shop as shop_cog
from bot.services import shop_catalog, skins
from bot.services.achievements import shop_skin_stats, shop_stats
from bot.services.levels import TIMEZONE
from bot.services.shop_catalog import CATALOG, CATALOG_BY_KEY, _skin
from bot.services.skins import GAMES, SKINS, Season, Skin, in_season, params

GRANATE = skins.SKIN_BY_KEY["ruleta_granate"]
#: El artículo del granate, barato para no tener que dar dinero.
GRANATE_ITEM = replace(CATALOG_BY_KEY[GRANATE.catalog_key], price=100)


# -- El registro ---------------------------------------------------------------------


def test_cada_skin_es_de_un_juego_y_solo_cambia_lo_que_su_escena_entiende() -> None:
    for skin in SKINS:
        assert skin.game in GAMES, skin.key
        merged = params(skin.game, skin)
        assert merged["key"] == skin.key
        assert set(merged) == set(GAMES[skin.game].defaults) | {"key"}


def test_cada_skin_tiene_su_articulo_en_la_tienda() -> None:
    for skin in SKINS:
        entry = CATALOG_BY_KEY[skin.catalog_key]
        assert entry.per_user == 1
        assert entry.visible == skin.for_sale
        assert entry.aisle in shop_catalog.AISLE_BY_KEY


def test_params_parte_de_los_de_serie() -> None:
    serie = params("tapete")
    assert serie["key"] == "serie"
    assert serie["felt"] == GAMES["tapete"].defaults["felt"]
    granate = params("tapete", GRANATE)
    assert granate["felt"] != serie["felt"]
    assert granate["trim"] == serie["trim"], "lo que la skin no cambia sale de serie"


def test_params_rechaza_skins_de_otro_juego_o_con_parametros_raros() -> None:
    wrong = Skin("x", "moneda", "🪙", "X", "", {}, price=1, aisle="lujo")
    with pytest.raises(ValueError, match="es de moneda"):
        params("tapete", wrong)
    odd = Skin("y", "tapete", "🟪", "Y", "", {"bola": "#fff"}, price=1, aisle="lujo")
    with pytest.raises(ValueError, match="no tiene"):
        params("tapete", odd)


def test_temporada_solo_en_sus_meses() -> None:
    carnaval = Skin(
        "c", "tapete", "🎭", "C", "", {}, price=1, aisle="lujo",
        season=Season("Carnaval", frozenset({2})),
    )  # fmt: skip
    assert in_season(carnaval, datetime(2026, 2, 14, tzinfo=TIMEZONE))
    assert not in_season(carnaval, datetime(2026, 3, 1, tzinfo=TIMEZONE))
    assert in_season(GRANATE, datetime(2026, 3, 1, tzinfo=TIMEZONE))


def test_estadisticas_de_skins() -> None:
    assert shop_skin_stats(swapped=True).add == {"shop_skin_swaps": 1}
    assert shop_skin_stats(swapped=False).add == {}
    delta = shop_stats(
        kind="objeto", total=100, tax=7, discount_pct=0, luxury=False, serial=None,
        last_unit=False, renewed=False, collection=1, queued_boosts=0, balance_after=0,
        skin=True,
    )  # fmt: skip
    assert delta.add["shop_skins"] == 1


# -- Comprar y cambiar ----------------------------------------------------------------


def skin_select(view: ui.LayoutView) -> ui.Select:
    return next(
        c for c in view.walk_children()
        if isinstance(c, ui.Select) and c.placeholder == "🎨 Cambiar una skin"
    )  # fmt: skip


async def test_comprar_una_skin_te_la_pone(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(GRANATE_ITEM,))
    guild = make_guild()
    member = make_member(guild)
    await pay_for(cog, guild, member, GRANATE.catalog_key)
    assert cog.skin(GUILD, BUYER, "tapete") == GRANATE
    bot = MagicMock()
    bot.get_cog.return_value = cog
    assert shop_cog.skin_of(bot, GUILD, BUYER, "tapete") == GRANATE
    assert shop_cog.skin_params(bot, GUILD, BUYER, "tapete")["key"] == "ruleta_granate"
    bot.get_cog.return_value = None
    assert shop_cog.skin_params(bot, GUILD, BUYER, "tapete")["key"] == "serie"


async def test_la_skin_sale_en_su_pestaña_y_en_la_mochila(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(GRANATE_ITEM,))
    guild = make_guild()
    member = make_member(guild)
    view = await cog.storefront(guild, member)
    view.tab = "skin"
    assert [i.catalog_key for i in view.shown] == [GRANATE.catalog_key]
    view.tab = "objeto"
    assert view.shown == [], "las skins no se mezclan con la vitrina"
    await pay_for(cog, guild, member, GRANATE.catalog_key)
    backpack = await cog.backpack_view(guild, member, member)
    assert "🎨 Skins" in texts(backpack) and "(puesta)" in texts(backpack)


async def test_cambiar_de_skin_desde_la_mochila(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(GRANATE_ITEM,))
    cog._unlocked = AsyncMock(return_value=set())  # type: ignore[method-assign]
    guild = make_guild()
    member = make_member(guild)
    await pay_for(cog, guild, member, GRANATE.catalog_key)
    backpack = await cog.backpack_view(guild, member, member)
    select = skin_select(backpack)
    assert [o.value for o in select.options] == ["tapete:", "tapete:ruleta_granate"]
    assert [o.value for o in select.options if o.default] == ["tapete:ruleta_granate"]

    click = make_interaction(member)
    await cog.choose_skin(click, "tapete:", backpack=backpack)
    assert cog.skin(GUILD, BUYER, "tapete") is None
    assert [o.value for o in skin_select(backpack).options if o.default] == ["tapete:"]

    await cog.choose_skin(make_interaction(member), "tapete:ruleta_granate", backpack=backpack)
    assert cog.skin(GUILD, BUYER, "tapete") == GRANATE


async def test_no_te_puedes_poner_una_skin_que_no_tienes(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(GRANATE_ITEM,))
    guild = make_guild()
    member = make_member(guild)
    click = make_interaction(member)
    click.guild = guild
    await cog.choose_skin(click, "tapete:ruleta_granate")
    assert cog.skin(GUILD, BUYER, "tapete") is None


async def test_las_skins_puestas_se_recuperan_al_arrancar(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock, catalog=(GRANATE_ITEM,))
    guild = make_guild()
    await pay_for(cog, guild, make_member(guild), GRANATE.catalog_key)
    fresh = shop_cog.Tienda(MagicMock(), cog.economy, cog.repository, clock=clock)
    fresh._expire = MagicMock()  # sin bucle real en las pruebas
    await fresh.cog_load()
    assert fresh.skin(GUILD, BUYER, "tapete") == GRANATE


# -- El botón 🎨 de las mesas -----------------------------------------------------------


async def test_boton_de_la_mesa_solo_si_el_juego_tiene_skins(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(GRANATE_ITEM,))
    bot = MagicMock()
    bot.get_cog.return_value = cog
    button = shop_cog.skin_button(bot, "tapete")
    assert isinstance(button, ui.Button) and str(button.emoji) == "🎨"
    assert shop_cog.skin_button(bot, "moneda") is None, "la moneda aún no admite skins"
    bot.get_cog.return_value = None
    assert shop_cog.skin_button(bot, "tapete") is None


async def test_el_selector_de_la_mesa_cambia_la_skin(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(GRANATE_ITEM,))
    cog._unlocked = AsyncMock(return_value=set())  # type: ignore[method-assign]
    guild = make_guild()
    member = make_member(guild)

    empty = make_interaction(member)
    await cog.open_skin_picker(empty, "tapete")
    assert "No tienes ningún tapete" in empty.edit_original_response.await_args.kwargs["content"]

    await pay_for(cog, guild, member, GRANATE.catalog_key)
    await cog.wear_skin(GUILD, BUYER, "tapete", None)
    opening = make_interaction(member)
    await cog.open_skin_picker(opening, "tapete")
    picker = opening.edit_original_response.await_args.kwargs["view"]
    assert isinstance(picker, shop_cog.SkinPicker)
    picker.select._values = ["tapete:ruleta_granate"]  # type: ignore[attr-defined]
    choosing = make_interaction(member)
    await picker.select.callback(choosing)
    assert cog.skin(GUILD, BUYER, "tapete") == GRANATE
    assert "Tapete granate" in choosing.edit_original_response.await_args.kwargs["content"]


# -- Temporada y logros ----------------------------------------------------------------

CARNAVAL = Skin(
    "ruleta_carnaval", "tapete", "🎭", "Tapete de Carnaval", "", {"trim": "#ff0"},
    price=100, aisle="canarias", season=Season("Carnaval", frozenset({2})),
)  # fmt: skip
TROFEO = Skin(
    "ruleta_trofeo", "tapete", "🏆", "Tapete de campeón", "", {"trim": "#fd0"},
    price=100, aisle="lujo", achievement="shop_1",
)  # fmt: skip


@pytest.fixture
def fake_skins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mete en el registro una skin de temporada y una de logro."""
    for skin in (CARNAVAL, TROFEO):
        monkeypatch.setitem(skins.SKIN_BY_KEY, skin.key, skin)
        monkeypatch.setitem(skins.SKIN_BY_CATALOG_KEY, skin.catalog_key, skin)
    monkeypatch.setattr(skins, "SKINS", (*SKINS, CARNAVAL, TROFEO))
    monkeypatch.setattr(shop_cog, "SKIN_KEYS", shop_catalog.SKIN_KEYS | {
        CARNAVAL.catalog_key, TROFEO.catalog_key
    })  # fmt: skip
    monkeypatch.setattr(shop_catalog, "SKIN_KEYS", shop_cog.SKIN_KEYS)


async def test_la_skin_de_temporada_solo_se_vende_en_su_temporada(
    tmp_path: Path, fake_skins: None
) -> None:
    march = Clock(datetime(2026, 3, 5, 18, tzinfo=TIMEZONE).timestamp())
    cog = await make_cog(tmp_path, march, catalog=(_skin(CARNAVAL),))
    guild = make_guild()
    member = make_member(guild)
    view = await cog.storefront(guild, member)
    view.tab = "skin"
    assert view.shown == [], "fuera de temporada no sale en el escaparate"
    item = await cog.repository.item_by_key(GUILD, CARNAVAL.catalog_key)
    assert item is not None
    opening = make_interaction(member)
    await cog.open_checkout(opening, item.id)
    assert (
        "solo se vende en Carnaval" in opening.edit_original_response.await_args.kwargs["content"]
    )

    march.now = datetime(2026, 2, 10, 18, tzinfo=TIMEZONE).timestamp()
    assert [i.catalog_key for i in view.shown] == [CARNAVAL.catalog_key]
    await pay_for(cog, guild, member, CARNAVAL.catalog_key)
    assert cog.skin(GUILD, BUYER, "tapete") == CARNAVAL


async def test_la_skin_de_logro_llega_sola_y_no_se_vende(tmp_path: Path, fake_skins: None) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(_skin(TROFEO),))
    guild = make_guild()
    member = make_member(guild)
    view = await cog.storefront(guild, member)
    view.tab = "skin"
    assert view.shown == [], "las de logro no se venden"

    assert await cog.sync_reward_skins(GUILD, BUYER, ["otro_logro"]) == []
    assert await cog.sync_reward_skins(GUILD, BUYER, ["shop_1"]) == [TROFEO]
    assert await cog.sync_reward_skins(GUILD, BUYER, ["shop_1"]) == [], "no se da dos veces"

    # Quien ya tenía el logro la recibe al abrir la mochila.
    other = make_member(guild, 11)
    cog._unlocked = AsyncMock(return_value={"shop_1"})  # type: ignore[method-assign]
    backpack = await cog.backpack_view(guild, other, other)
    assert "Tapete de campeón" in texts(backpack)


def test_ninguna_skin_es_premio_de_la_caja_botin() -> None:
    entries = [e for e in CATALOG if e.key.startswith(skins.CATALOG_PREFIX)]
    assert entries and all(shop_catalog.is_skin(e.key) for e in entries)


def test_skin_del_desplegable_sin_skins_no_sale() -> None:
    assert shop_cog.skin_options([]) == []


async def test_regalo_de_skin_sale_en_el_aviso_del_logro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.cogs import achievements as achievements_cog

    cog = achievements_cog.Achievements.__new__(achievements_cog.Achievements)
    cog.bot = MagicMock()
    cog._pay = AsyncMock(return_value=None)  # type: ignore[method-assign]
    monkeypatch.setattr(shop_cog, "grant_reward_skins", AsyncMock(return_value=[GRANATE]))
    monkeypatch.setattr(achievements_cog.mascotas, "cameo", AsyncMock(return_value=None))
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    await cog._reward_and_announce(GUILD, BUYER, ["shop_1"], channel)
    embed = channel.send.await_args.kwargs["embed"]
    assert "🟥 Tapete granate" in embed.description
