"""Pruebas de las calles de `pisos` (9.14.0): 3 calles × 3 edificios por barrio, cada edificio
con su destacado y sus unidades nuevas, sin cambiar ninguna clave de antes."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import replace
from pathlib import Path

import discord
import pytest

from bot.repositories.economy import LedgerEntry
from bot.repositories.housing import Market
from bot.services import housing as rules
from bot.services import housing_streets as streets
from bot.services import housing_view as view
from bot.services.housing_catalog import BARRIOS, LISTING_BY_KEY, LISTINGS, listings_of
from tests.unit.test_housing import ANA, GUILD, Clock, Scripted, at, give, ledger_sum, make

#: La huella del catálogo de calles (claves, tipos, metros, plantas y dónde está cada edificio).
#: Solo se toca a propósito: cambió en la 9.15.0 (edificios de más y calles mezcladas) y en la
#: 9.16.0 (áticos caros).
FINGERPRINT = "71a6aac21f1dae3e"
#: La huella de las 866 unidades nuevas de la 9.14.0 (clave, tipo, metros y planta): pueden
#: tener dueño, así que nunca cambian. Lo nuevo va siempre con claves nuevas.
UNITS_9_14 = "aa15f9941ac23caa"
#: La de las 1.271 de la 9.15.0, sin los áticos caros que llegaron en la 9.16.0.
UNITS_9_15 = "9350b2fcec287e9d"


def test_el_catalogo_de_calles_no_cambia_sin_querer() -> None:
    assert streets.fingerprint() == FINGERPRINT


def test_las_unidades_de_la_9_14_siguen_exactamente_igual() -> None:
    old = [
        u
        for u in streets.STREET_UNITS
        if int(streets.building_of(u.key).key.rsplit("_e", 1)[1]) <= 9
        and not streets.is_penthouse(u.key)  # los áticos caros llegaron en la 9.16.0
    ]
    rows = sorted((u.key, u.kind, u.m2, u.floor) for u in old)
    assert len(rows) == 866
    assert hashlib.sha256(repr(rows).encode()).hexdigest()[:16] == UNITS_9_14


def test_cada_barrio_tiene_tres_calles_y_mas_edificios_cuanto_mas_denso() -> None:
    assert len(streets.STREETS) == 3 * len(BARRIOS)
    featured = Counter(b.featured for b in streets.BUILDINGS if b.featured)
    assert set(featured) == set(LISTING_BY_KEY) and set(featured.values()) == {1}
    per_street = {"centro": 5, "obrero": 5, "historico": 4, "playa": 4, "marinero": 4}
    per_street |= {"lujo": 4, "rural": 3}
    for barrio in BARRIOS:
        roads = streets.streets_of(barrio.key)
        assert len(roads) == 3
        assert {len(streets.buildings_of(r.key)) for r in roads} == {per_street[barrio.theme]}
        kinds = {b.kind for r in roads for b in streets.buildings_of(r.key)}
        if barrio.theme in ("centro", "obrero", "historico", "marinero", "playa", "lujo"):
            assert {streets.BLOQUE, streets.CASA} <= kinds, barrio.key  # pisos y casas mezclados
        # El edificio N es el de la tarjeta N del barrio.
        for n, item in enumerate(listings_of(barrio.key), start=1):
            assert streets.BUILDING_BY_KEY[f"{barrio.key}_e{n}"].featured == item.key
    # Los amarres y las naves van a la tercera calle (la del puerto o el polígono).
    for building in streets.BUILDINGS:
        if building.kind in (streets.PANTALAN, streets.POLIGONO):
            assert building.street.endswith("_c3"), building.key


def test_las_claves_de_antes_siguen_y_las_nuevas_no_chocan() -> None:
    new = [u.key for u in streets.STREET_UNITS]
    assert len(new) == len(set(new)) and not set(new) & set(LISTING_BY_KEY)
    for item in LISTINGS:
        building = streets.building_of(item.key)
        assert building is not None and item.key in [u.key for u in building.units]
    # Las plantas de un edificio dividido y las habitaciones están en el edificio de su padre.
    assert streets.building_of("triana_edificio~2").featured == "triana_edificio"
    assert streets.building_of("triana_e2_1a~h1").key == "triana_e2"
    assert streets.building_of("nada") is None


def test_el_destacado_vive_en_su_planta_y_el_bloque_es_como_su_barrio() -> None:
    piso = LISTING_BY_KEY["ofra_piso3"]  # «8.º»
    building = streets.building_of(piso.key)
    assert building.kind == streets.BLOQUE and building.floors >= 9
    assert next(u.level for u in building.units if u.key == piso.key) == 8
    # Un bloque del centro tiene sótano con garajes y un local en el bajo.
    triana = streets.BUILDING_BY_KEY["triana_e2"]
    kinds = Counter(rules.KINDS[streets.STREET_UNIT_BY_KEY[u.key].kind].key
                    for u in triana.units if u.key in streets.STREET_UNIT_BY_KEY)  # fmt: skip
    assert kinds["garaje"] >= 1 and kinds["local"] >= 1
    # Una casa es solo ella; un pantalán tiene varios amarres.
    assert len(streets.building_of("tafira_chalet").units) == 1
    assert len(streets.building_of("mogan_amarre").units) == streets.AMARRES + 1


def test_hay_piezas_baratas_para_empezar() -> None:
    cheap = [u for u in streets.STREET_UNITS if rules.price(u) <= 300_000]
    assert len(cheap) >= 200  # trasteros y plazas de garaje en casi todos los barrios
    assert {u.kind for u in cheap} <= {"trastero", "garaje", "amarre"}


def test_las_calles_pesan_en_el_indice_lo_mismo_que_los_nueve_destacados() -> None:
    for barrio in BARRIOS:
        featured = sum(rules.barrio_weight(item) for item in listings_of(barrio.key))
        street = sum(rules.barrio_weight(u) for u in streets.units_of_barrio(barrio.key))
        assert featured == pytest.approx(1) and street == pytest.approx(rules.STREETS_WEIGHT)
    market = Market()
    assert len(market.keys()) == len(LISTINGS) + len(streets.STREET_UNITS)
    building = streets.building_of("triana_e2_1a")
    street = streets.STREET_BY_KEY[building.street]
    assert market.listing("triana_e2_1a").title.endswith(f"{street.name}, {building.number}")


def test_el_plano_y_el_corte_cuentan_lo_que_hay() -> None:
    state = view.streets_state(Market(), "triana", ANA)
    assert [len(s["buildings"]) for s in state["streets"]] == [5, 5, 5]
    slots = sorted(b["slot"] for s in state["streets"] for b in s["buildings"])
    assert slots == list(range(1, 16))
    assert state["barrio"]["index"] == "1,000" and state["barrio"]["price_m2"].endswith("/m²")
    cut = view.building_state(Market(), "triana_e2", ANA, {})
    levels = [f["level"] for f in cut["floors"]]
    assert levels == sorted(levels, reverse=True) and levels[-1] == -1  # de la azotea al sótano
    units = [u for f in cut["floors"] for u in f["units"]]
    assert sorted(u["slot"] for u in units) == list(range(1, len(units) + 1))
    assert all(u["use"] == "En venta" and u["owner"] == "En venta" for u in units)
    assert sum(u["featured"] for u in units) == 1


async def test_una_unidad_nueva_se_compra_y_cobra_su_renta(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.5))
    await give(economy, ANA, 5_000_000)
    plaza = "triana_e2_g1"
    before = (await service.market(GUILD)).index("triana")
    await service.buy(GUILD, ANA, plaza, 100)
    market = await service.market(GUILD)
    assert market.shares_of(plaza) == {ANA: 100} and market.index("triana") > before
    for d in range(15, 19):
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    market = await service.market(GUILD)
    assert market.leases[plaza].status == rules.RENTED
    state = view.building_state(market, "triana_e2", ANA, {"ana": "Ana"})  # type: ignore[dict-item]
    mine = [u for f in state["floors"] for u in f["units"] if u["mine"]]
    assert [u["key"] for u in mine] == [plaza] and mine[0]["owner"] == "Tuya"
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)


async def test_del_barrio_a_las_calles_al_edificio_y_a_la_ficha(tmp_path: Path) -> None:
    from bot.cogs.pisos import BARRIO, CALLES, EDIFICIO, FICHA, MAX_OPTIONS
    from tests.unit.test_pisos_cog import clicked, guild_with, ids, member, open_panel
    from tests.unit.test_pisos_cog import make as make_cog

    cog, _scene, _clock = await make_cog(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "triana_e2_1b", 100)
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.barrio = "triana"
    await panel._back_to_barrio(clicked(ana, []))
    assert panel.screen == BARRIO and "pisos:calles" in ids(panel)
    await panel._go_streets(clicked(ana, []))
    assert panel.screen == CALLES
    assert {f"pisos:edificio:{n}" for n in range(1, 10)} <= set(ids(panel))
    buttons = {i.custom_id: i for i in panel.children if isinstance(i, discord.ui.Button)}
    assert buttons["pisos:edificio:2"].style == discord.ButtonStyle.success  # ahí tienes algo
    await panel._open_building(clicked(ana, [], custom_id="pisos:edificio:2"))
    assert panel.screen == EDIFICIO and panel.building == "triana_e2"
    (select,) = [i for i in panel.children if getattr(i, "custom_id", "") == "pisos:unidad"]
    assert "triana_e2_1b" in [o.value for o in select.options]
    await panel._pick_unit(clicked(ana, [], values=["triana_e2_1b"]))
    assert panel.screen == FICHA and panel.listing == "triana_e2_1b"
    assert "pisos:volver" in ids(panel) and "pisos:vender" in ids(panel)
    await panel._go_own_building(clicked(ana, []))
    assert panel.screen == EDIFICIO and panel.building == "triana_e2"
    # Ningún edificio pasa de lo que cabe en un desplegable de Discord.
    for building in streets.BUILDINGS:
        panel.building = building.key
        panel.rebuild()
        (select,) = [i for i in panel.children if getattr(i, "custom_id", "") == "pisos:unidad"]
        assert len(select.options) <= MAX_OPTIONS
        assert all(len(o.label) <= 100 and len(o.description or "") <= 100 for o in select.options)


async def test_las_calles_y_lo_comprado_en_ellas_cuentan_para_los_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.cogs import pisos as pisos_cog
    from tests.unit.test_pisos_cog import clicked, guild_with, member, open_panel
    from tests.unit.test_pisos_cog import make as make_cog

    noted: list[dict[str, int]] = []
    tracked: list[dict[str, int]] = []

    def note(_bot: object, _guild: int, _user: int, delta: object, *_rest: object) -> None:
        noted.append(delta.add)  # type: ignore[attr-defined]

    async def track(_bot: object, _guild: int, _user: object, _ch: object, delta: object) -> None:
        tracked.append(delta.peak)  # type: ignore[attr-defined]

    monkeypatch.setattr(pisos_cog.logros, "note", note)
    monkeypatch.setattr(pisos_cog.logros, "track", track)
    cog, _scene, _clock = await make_cog(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.barrio = "triana"
    await panel._go_streets(clicked(ana, []))
    await panel._open_building(clicked(ana, [], custom_id="pisos:edificio:2"))
    assert {"pisos_streets_opened": 1} in noted and {"pisos_bldg_triana_e2": 1} in noted
    trade = await cog.service.buy(GUILD, ana.id, "triana_e2_g1", 100)
    await cog.track_trade(GUILD, ana, None, trade.deal, trade.balance)
    assert tracked[-1]["pisos_street_units_max"] == 1
    assert tracked[-1]["pisos_buildings_max"] == 1


def discord_problems(panel) -> list[str]:  # noqa: ANN001
    """Lo que Discord rechazaría de los componentes de un panel (400 Invalid Form Body)."""
    problems = []
    ids = [getattr(i, "custom_id", None) for i in panel.children]
    repeated = {i for i in ids if i and ids.count(i) > 1}
    if repeated:
        problems.append(f"custom_id repetido: {sorted(repeated)}")
    rows: dict[int, int] = {}
    for item in panel.children:
        rows[item.row or 0] = rows.get(item.row or 0, 0) + (
            5 if isinstance(item, discord.ui.Select) else 1
        )
        if isinstance(item, discord.ui.Button) and not 0 < len(item.label or "") <= 80:
            problems.append(f"etiqueta: {item.label!r}")
        if isinstance(item, discord.ui.Select) and not 1 <= len(item.options) <= 25:
            problems.append(f"opciones: {len(item.options)}")
    if len(panel.children) > 25 or max(rows, default=0) > 4 or max(rows.values()) > 5:
        problems.append(f"filas: {rows}")
    return problems


async def test_ninguna_pantalla_de_calles_repite_un_custom_id(tmp_path: Path) -> None:
    """Revisión 9.15.1: el botón ⭐ Destacados y «◀ barrio» del edificio tenían el `custom_id` del
    desplegable de barrios (`pisos:barrio`) y Discord rechazaba el plano al elegir un barrio."""
    from bot.cogs.pisos import CALLES, EDIFICIO
    from tests.unit.test_pisos_cog import guild_with, member, open_panel
    from tests.unit.test_pisos_cog import make as make_cog

    cog, _scene, _clock = await make_cog(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    for barrio in BARRIOS:
        panel.screen, panel.barrio = CALLES, barrio.key
        panel.rebuild()
        assert not discord_problems(panel), (barrio.key, discord_problems(panel))
    for building in streets.BUILDINGS:
        panel.screen, panel.building, panel.barrio = EDIFICIO, building.key, building.barrio
        panel.rebuild()
        assert not discord_problems(panel), (building.key, discord_problems(panel))


async def test_el_plano_y_sus_botones_van_en_orden_de_lectura(tmp_path: Path) -> None:
    """Revisión 9.15.2: los números iban por la tarjeta del destacado (14, 3, 15…) y parecían al
    azar. Ahora el plano y los botones van 1, 2, 3… calle a calle y de izquierda a derecha."""
    from bot.cogs.pisos import CALLES
    from tests.unit.test_pisos_cog import guild_with, member, open_panel
    from tests.unit.test_pisos_cog import make as make_cog

    cog, _scene, _clock = await make_cog(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    for barrio in BARRIOS:
        state = view.streets_state(Market(), barrio.key, ANA)
        drawn = [b["slot"] for street in state["streets"] for b in street["buildings"]]
        assert drawn == list(range(1, len(drawn) + 1)), barrio.key
        panel.screen, panel.barrio = CALLES, barrio.key
        panel.rebuild()
        buttons = sorted(
            (b for b in panel.children if (b.custom_id or "").startswith("pisos:edificio:")),
            key=lambda b: (b.row, panel.children.index(b)),
        )
        assert [int(b.label.split(" · ")[0]) for b in buttons] == drawn, barrio.key
        # Cada botón abre el edificio que se ve con su número.
        keys = [b["key"] for street in state["streets"] for b in street["buildings"]]
        assert [f"{barrio.key}_e{b.custom_id.rsplit(':', 1)[1]}" for b in buttons] == keys


def test_las_unidades_de_la_9_15_siguen_y_los_aticos_caros_son_nuevos() -> None:
    old = [u for u in streets.STREET_UNITS if not streets.is_penthouse(u.key)]
    rows = sorted((u.key, u.kind, u.m2, u.floor) for u in old)
    assert len(rows) == 1271
    assert hashlib.sha256(repr(rows).encode()).hexdigest()[:16] == UNITS_9_15


def test_los_aticos_caros_estan_donde_hay_vistas_y_cuestan_mas() -> None:
    penthouses = [u for u in streets.STREET_UNITS if streets.is_penthouse(u.key)]
    assert 40 <= len(penthouses) <= 70
    themes = {streets.BARRIO_BY_KEY[u.barrio].theme for u in penthouses}
    assert themes <= {"centro", "historico", "playa", "marinero", "lujo"}
    for unit in penthouses:
        building = streets.building_of(unit.key)
        top = max(u.level for u in building.units)
        level = next(u.level for u in building.units if u.key == unit.key)
        assert unit.kind == "atico" and level == top  # arriba del todo
        plain = rules.price(replace(unit, value_bonus=0.0))
        assert rules.price(unit) > plain * 1.3  # la terraza y las vistas
    # Un sobreático va siempre sobre un ático; un ático con vistas, donde no lo había.
    for unit in penthouses:
        building = streets.building_of(unit.key)
        level = next(u.level for u in building.units if u.key == unit.key)
        below = [u.label for u in building.units if u.level == level - 1]
        assert unit.key.endswith("_sa") == any(b.startswith("Ático") for b in below), unit.key
