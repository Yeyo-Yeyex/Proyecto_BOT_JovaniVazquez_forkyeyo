"""Pruebas de dividir inmuebles por plantas y de alquilarlos por habitaciones (9.12.0).

Por plantas cada planta pasa a ser un inmueble (dueños, hipoteca, contrato y venta propios)
y el entero deja de existir; por habitaciones el piso conserva sus dueños y cada habitación
tiene su inquilino, y se deshace al juntarlas o cuando el piso vuelve al mercado.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from bot.repositories import housing_finance as finance
from bot.repositories.economy import STATE_ACCOUNT_ID
from bot.repositories.housing import HousingError, Market, Split
from bot.services import housing as rules
from bot.services.housing_catalog import BARRIO_BY_KEY, KINDS, LISTING_BY_KEY, LISTINGS, ROOM_KIND
from tests.unit.test_housing import (
    ANA,
    BEA,
    GUILD,
    Clock,
    Scripted,
    at,
    give,
    housing_taxes,
    ledger_sum,
    make,
)

DUPLEX = LISTING_BY_KEY["canteras_duplex"]  # 2 plantas: «6.º y 7.º»
EDIFICIO = LISTING_BY_KEY["triana_edificio"]  # «Bajo a 3.º»: 4 plantas
PISO = LISTING_BY_KEY["isleta_piso3"]  # 85 m²: 3 habitaciones


# -- Reglas --------------------------------------------------------------------------------


def test_las_claves_de_plantas_y_habitaciones() -> None:
    assert rules.unit_key("a_b", rules.FLOORS, 2) == "a_b~2"
    assert rules.unit_key("a_b", rules.ROOMS, 1) == "a_b~h1"
    assert rules.parent_key("a_b~h1") == "a_b" and rules.parent_key("a_b") is None
    assert rules.parent_key("a_b~2~h3") == "a_b~2"  # una habitación de una planta
    assert rules.is_room("a_b~h1") and rules.is_room("a_b~2~h3")
    assert not rules.is_room("a_b~2") and not rules.is_room("a_b")


def test_cuantas_plantas_y_habitaciones_salen() -> None:
    assert rules.floors_of(EDIFICIO) == 4
    assert rules.floors_of(LISTING_BY_KEY["santacruz_edificio"]) == 5  # «Bajo a 4.º»
    assert rules.floors_of(DUPLEX) == 2
    assert rules.rooms_of(PISO) == 2 + 1  # 85 // 30 = 2, pero como poco 3
    assert rules.rooms_of(LISTING_BY_KEY["mogan_villa"]) == rules.ROOMS_MAX
    for listing in LISTINGS:
        floors = rules.split_problem(listing, rules.FLOORS) is None
        rooms = rules.split_problem(listing, rules.ROOMS) is None
        two = listing.m2 >= rules.HALVES_MIN_M2.get(listing.kind, 10**9)
        assert floors == (listing.kind in rules.FLOOR_KINDS or two), listing.key
        assert rooms == (listing.kind in rules.ROOM_PARENT_KINDS), listing.key
    assert rules.split_problem(DUPLEX, rules.FLOORS, promotion=True)


def test_las_plantas_piden_un_25_mas_y_valen_lo_de_antes_mas_el_60_de_la_obra() -> None:
    quote = rules.split_quote(EDIFICIO, rules.FLOORS)
    units = rules.split_units(EDIFICIO, rules.FLOORS, quote.units, quote.cost)
    # Un local en el bajo, las viviendas encima y un trastero por vivienda (9.13.0).
    kinds = ["local", "piso3", "piso3", "atico", "trastero", "trastero", "trastero"]
    assert [u.kind for u in units] == kinds
    assert [u.floor for u in units][:4] == ["Bajo", "1.º", "2.º", "Ático"]
    assert units[-1].floor == "Trastero 3"
    assert sum(u.m2 for u in units if u.kind != "trastero") == EDIFICIO.m2
    assert all(u.barrio == EDIFICIO.barrio for u in units)
    assert sum(u.kind in rules.HOME_KINDS for u in units) == 3  # el local y los trasteros no
    # Los trasteros, a precio de trastero del barrio.
    plain = replace(units[-1], rent_bonus=0.0, value_bonus=0.0)
    assert rules.reference_rent(units[-1]) == rules.reference_rent(plain)
    rent = sum(rules.reference_rent(u) for u in units)
    assert rent == pytest.approx(rules.reference_rent(EDIFICIO) * 1.25, rel=0.002)
    value = sum(rules.price(u) for u in units)
    expected = rules.price(EDIFICIO) + rules.REFORM_VALUE_SHARE * quote.cost
    assert value == pytest.approx(expected, rel=0.002)
    assert value - rules.price(EDIFICIO) < quote.total  # dividir para vender pierde
    # El ático pide más por metro que el bajo.
    assert rules.reference_rent(units[3]) > rules.reference_rent(units[0])


def test_los_edificios_divididos_antes_siguen_como_estaban() -> None:
    """La 9.12.0 guardó solo las plantas: su bajo sigue siendo vivienda y no tiene trasteros."""
    units = rules.split_units(EDIFICIO, rules.FLOORS, 4, 1_200_000)
    assert [u.kind for u in units] == ["bajo", "piso3", "piso3", "atico"]
    assert [u.key for u in units] == [f"{EDIFICIO.key}~{n}" for n in range(1, 5)]
    rent = sum(rules.reference_rent(u) for u in units)
    assert rent == pytest.approx(rules.reference_rent(EDIFICIO) * 1.25, rel=0.002)
    # Una casona o un dúplex no tienen trasteros ni local.
    assert rules.storage_of(DUPLEX) == 0 and rules.storage_of(EDIFICIO) == 3


def test_la_obra_de_dividir_paga_igic_icio_licencia_y_ajd() -> None:
    quote = rules.split_quote(EDIFICIO, rules.FLOORS)
    assert quote.units == 4 + 3
    assert quote.cost == (20_000 * 4 + 2_000 * 3 + 80 * EDIFICIO.m2) * 10
    assert quote.igic == round(quote.cost * rules.SERVICES_IGIC)
    assert quote.icio == round(quote.cost * rules.ICIO_RATE)
    assert quote.license == rules.WORKS_LICENSE_FEE
    assert quote.ajd == round(rules.price(EDIFICIO) * 0.0075)  # art. 36 DLeg 1/2009
    assert quote.days == rules.FLOOR_DAYS
    rooms = rules.split_quote(PISO, rules.ROOMS)
    assert rooms.cost == 4_000 * 3 * 10 and rooms.igic == round(rooms.cost * rules.SERVICES_IGIC)
    assert rooms.icio == rooms.license == rooms.ajd == 0  # amueblar no es obra


def test_las_habitaciones_suben_segun_el_barrio_y_el_piso_vale_lo_mismo() -> None:
    quote = rules.split_quote(PISO, rules.ROOMS)
    rooms = rules.split_units(PISO, rules.ROOMS, quote.units, quote.cost)
    assert {r.kind for r in rooms} == {ROOM_KIND} and len(rooms) == 3
    gain = rules.ROOM_RENT_GAIN[BARRIO_BY_KEY[PISO.barrio].theme]
    rent = sum(rules.reference_rent(r) for r in rooms)
    assert rent == pytest.approx(rules.reference_rent(PISO) * (1 + gain), rel=0.003)
    assert sum(rules.price(r) for r in rooms) == pytest.approx(rules.price(PISO), rel=0.002)
    # Donde hay universidad o playa compensa más que en el campo.
    assert rules.ROOM_RENT_GAIN["centro"] > rules.ROOM_RENT_GAIN["obrero"]
    assert rules.ROOM_RENT_GAIN["obrero"] > rules.ROOM_RENT_GAIN["rural"]


def test_una_habitacion_es_vivienda_salvo_para_nomadas_y_su_contrato_dura_un_ano() -> None:
    (room, *_rest) = rules.split_units(PISO, rules.ROOMS, 3, 0)
    assert rules.is_dwelling(room, "referencia")
    assert not rules.is_dwelling(room, rules.NOMAD_LEVEL)  # temporada (DGT)
    assert rules.contract_months(room, rules.RENT_LEVELS["referencia"]) == rules.ROOM_MONTHS
    assert rules.contract_months(PISO, rules.RENT_LEVELS["referencia"]) == 60
    assert KINDS[ROOM_KIND].community == 60  # comunidad y suministros del casero
    assert ROOM_KIND not in rules.HOME_KINDS  # no cuenta como otra vivienda (gran tenedor)


def test_el_mercado_ve_las_plantas_en_lugar_del_entero() -> None:
    market = Market(
        splits={
            EDIFICIO.key: Split(rules.FLOORS, 4, 1_200_000),
            PISO.key: Split(rules.ROOMS, 3, 120_000),
        }
    )
    keys = market.keys()
    assert EDIFICIO.key not in keys
    assert [f"{EDIFICIO.key}~{n}" for n in range(1, 5)] == [k for k in keys if "~" in k]
    assert PISO.key in keys and f"{PISO.key}~h1" not in keys
    assert market.room_keys() == [f"{PISO.key}~h{n}" for n in range(1, 4)]
    assert market.listing(f"{EDIFICIO.key}~4").kind == "atico"
    assert market.owner_key(f"{PISO.key}~h2") == PISO.key
    assert market.owner_key(f"{EDIFICIO.key}~2") == f"{EDIFICIO.key}~2"
    # El barrio sigue teniendo diez tarjetas: la del edificio, con sus plantas dentro.
    assert EDIFICIO in market.listings_of(EDIFICIO.barrio)


# -- Con la base de datos ------------------------------------------------------------------


async def test_dividir_por_plantas_reparte_duenos_e_hipoteca_y_cobra(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, DUPLEX.key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        finance.add_mortgage_in(connection, GUILD, ANA, DUPLEX.key, 3_000_001, 0.03, 12_345, 0)
    with pytest.raises(HousingError):
        await service.split(GUILD, BEA, DUPLEX.key, rules.FLOORS)  # no es suyo
    with pytest.raises(HousingError):
        await service.split(GUILD, ANA, DUPLEX.key, "sotano")
    held = (await service.market(GUILD)).holdings[DUPLEX.key][ANA]
    before, state = await economy.balance(GUILD, ANA), ledger_sum(tmp_path, STATE_ACCOUNT_ID)

    done, balance = await service.split(GUILD, ANA, DUPLEX.key, rules.FLOORS)

    quote = done.quote
    assert balance == before - quote.total
    taxes = quote.igic + quote.icio + quote.license + quote.ajd
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == taxes
    assert housing_taxes(tmp_path, "ajd") == quote.ajd
    market = await service.market(GUILD)
    units = [f"{DUPLEX.key}~1", f"{DUPLEX.key}~2"]
    assert list(done.units) == units
    assert DUPLEX.key not in market.keys() and DUPLEX.key not in market.leases
    assert DUPLEX.key not in market.holdings
    assert all(market.holdings[u][ANA].shares == 100 for u in units)
    assert sum(market.holdings[u][ANA].paid for u in units) == held.paid + quote.cost
    assert sum(market.holdings[u][ANA].cost for u in units) == held.cost + quote.total
    mortgages = market.mortgages_of(ANA)
    assert sorted(m.listing for m in mortgages) == units
    assert sum(m.principal for m in mortgages) == 3_000_001
    assert sum(m.payment for m in mortgages) == 12_345
    assert all(market.leases[u].status == rules.OBRAS for u in units)
    assert market.whole_homes(ANA) == 2
    # El entero ya no se compra ni se vende: se hace con cada planta.
    with pytest.raises(HousingError):
        await service.buy(GUILD, BEA, DUPLEX.key, 10)
    with pytest.raises(HousingError):
        await service.split(GUILD, ANA, DUPLEX.key, rules.FLOORS)
    for d in range(15, 15 + rules.FLOOR_DAYS + 1):
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    market = await service.market(GUILD)
    assert all(market.leases[u].status == rules.VACANT for u in units)
    # Una planta se vende sola; la otra sigue siendo suya.
    await service.sell(GUILD, ANA, units[0], 100)
    market = await service.market(GUILD)
    assert units[0] not in market.holdings and market.holdings[units[1]][ANA].shares == 100
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)


async def test_por_habitaciones_cada_una_tiene_su_inquilino_y_cobra_el_piso(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, PISO.key, 100)

    done, _balance = await service.split(GUILD, ANA, PISO.key, rules.ROOMS)

    rooms = list(done.units)
    market = await service.market(GUILD)
    assert market.leases[PISO.key].status == rules.BY_ROOMS
    assert market.holdings[PISO.key][ANA].shares == 100  # el piso sigue siendo suyo
    assert all(market.shares_of(r) == {ANA: 100} and market.manager(r) == ANA for r in rooms)
    assert market.whole_homes(ANA) == 1
    with pytest.raises(HousingError):
        await service.buy(GUILD, BEA, rooms[0], 10)  # una habitación no va sola
    with pytest.raises(HousingError):
        await service.sell(GUILD, ANA, rooms[0], 100)
    # Acaba de amueblarse, siempre aparece inquilino y nadie deja de pagar (ni imprevistos).
    service.rng = Scripted(fill=0.5)
    for d in range(15, 15 + rules.ROOM_DAYS + 4):
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    market = await service.market(GUILD)
    leases = [market.leases[r] for r in rooms]
    assert all(lease.status == rules.RENTED for lease in leases)
    assert all(rules.TENANT_BY_KEY[lease.tenant].kinds == {ROOM_KIND} for lease in leases)
    assert all(lease.until <= lease.since + timedelta(days=rules.ROOM_MONTHS) for lease in leases)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        paid = connection.execute(
            "SELECT COUNT(DISTINCT listing), SUM(rent) FROM housing_income "
            "WHERE guild_id = ? AND user_id = ? AND rent > 0",
            (GUILD, ANA),
        ).fetchone()
    assert paid[0] == 3 and paid[1] > 0  # cobra la renta de las tres
    # Ocupadas no se juntan, y con inquilinos no se vende al mercado sin deudas pendientes.
    with pytest.raises(HousingError):
        await service.join_rooms(GUILD, ANA, PISO.key)
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)


async def test_juntar_las_habitaciones_y_venderlo_las_deshace(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, PISO.key, 100)
    await service.split(GUILD, ANA, PISO.key, rules.ROOMS)
    with pytest.raises(HousingError):
        await service.join_rooms(GUILD, BEA, PISO.key)
    await service.join_rooms(GUILD, ANA, PISO.key)
    market = await service.market(GUILD)
    assert PISO.key not in market.splits and not market.room_keys()
    assert market.leases[PISO.key].status == rules.VACANT
    # Otra vez por habitaciones y se vende entero al mercado: vuelve sin dividir.
    await service.split(GUILD, ANA, PISO.key, rules.ROOMS)
    await service.sell(GUILD, ANA, PISO.key, 100)
    market = await service.market(GUILD)
    assert PISO.key not in market.splits and not market.room_keys()
    assert not any(key.startswith(f"{PISO.key}~") for key in market.leases)
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)


async def test_con_varios_duenos_dividir_lo_vota_la_junta_por_unanimidad(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    for member in (ANA, BEA):
        await give(economy, member, 10_000_000)
    await service.buy(GUILD, ANA, DUPLEX.key, 60)
    await service.buy(GUILD, BEA, DUPLEX.key, 40)
    with pytest.raises(HousingError):
        await service.split(GUILD, ANA, DUPLEX.key, rules.FLOORS)  # no lo tiene entero
    assert rules.MOTION_BY_KEY[rules.FLOORS].threshold == rules.UNANIMITY
    vote = await service.propose(GUILD, ANA, DUPLEX.key, rules.FLOORS)
    index = (await service.market(GUILD)).index(DUPLEX.barrio)
    before = {m: await economy.balance(GUILD, m) for m in (ANA, BEA)}
    result = await service.vote(GUILD, BEA, vote.id, True)
    assert result.vote.status == "aprobada", result.vote.note
    market = await service.market(GUILD)
    quote = rules.split_quote(DUPLEX, rules.FLOORS, index)
    spent = {m: before[m] - await economy.balance(GUILD, m) for m in (ANA, BEA)}
    assert sum(spent.values()) == quote.total and spent[ANA] > spent[BEA]
    for unit in (f"{DUPLEX.key}~1", f"{DUPLEX.key}~2"):
        assert market.shares_of(unit) == {ANA: 60, BEA: 40}


def test_una_habitacion_vacia_no_se_okupa_y_un_piso_vacio_si() -> None:
    from datetime import date

    day = date(2026, 10, 20)
    empty = rules.Lease(since=day - timedelta(days=rules.SQUAT_AFTER_DAYS + 1))
    (room, *_rest) = rules.split_units(PISO, rules.ROOMS, 3, 0)
    # Tres imprevistos que no pasan, nadie que alquile y los okupas, que sí.
    stays = rules.advance_day(room, empty, day, 1.0, Scripted([0.99] * 4 + [0.0]))
    assert stays.lease.status == rules.VACANT
    # El piso entero tiene seis imprevistos posibles (está en un bloque).
    taken = rules.advance_day(PISO, empty, day, 1.0, Scripted([0.99] * 7 + [0.0]))
    assert taken.lease.status == rules.SQUATTED


async def test_un_negocio_se_instala_en_el_local_del_bajo(tmp_path: Path) -> None:
    from bot.repositories.housing import HousingRepository

    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    await give(economy, ANA, 30_000_000)
    await service.buy(GUILD, ANA, EDIFICIO.key, 100)
    done, _balance = await service.split(GUILD, ANA, EDIFICIO.key, rules.FLOORS)
    local = done.units[0]
    assert len(done.units) == 7
    for d in range(15, 15 + rules.FLOOR_DAYS + 1):
        clock.now = at(d).timestamp()
        await service.close_days(GUILD)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        locals_ = HousingRepository.business_locals_in(connection, GUILD, ANA)
        assert [(listing.key, status) for listing, status in locals_] == [(local, rules.VACANT)]
        HousingRepository.occupy_business_local_in(
            connection, GUILD, ANA, local, at(23).date(), clock.now
        )
    market = await service.market(GUILD)
    assert market.leases[local].status == rules.OWN_BUSINESS
    assert market.whole_homes(ANA) == 3


# -- Dividir en dos (9.16.0) --------------------------------------------------------------


def test_un_piso_grande_un_local_o_un_bajo_amplio_se_dividen_en_dos() -> None:
    piso = LISTING_BY_KEY["triana_piso3"]  # 95 m²
    quote = rules.split_quote(piso, rules.FLOORS)
    assert quote.units == 2 and rules.halves(piso)
    units = rules.split_units(piso, rules.FLOORS, quote.units, quote.cost)
    assert [u.floor for u in units] == ["2.º izda.", "2.º dcha."]
    assert sum(u.m2 for u in units) == piso.m2 and {u.kind for u in units} == {"estudio"}
    rent = sum(rules.reference_rent(u) for u in units)
    assert rent == pytest.approx(rules.reference_rent(piso) * 1.25, rel=0.003)
    local = LISTING_BY_KEY["triana_local"]
    assert {u.kind for u in rules.split_units(local, rules.FLOORS, 2, 0)} == {"local"}
    # Lo pequeño no: cada mitad no serviría.
    for key in ("triana_estudio", "isleta_bajo"):
        assert rules.split_problem(LISTING_BY_KEY[key], rules.FLOORS)
    # Una mitad (o una planta) no se divide otra vez.
    assert rules.split_problem(units[0], rules.FLOORS)
    assert not rules.halves(DUPLEX) and not rules.halves(EDIFICIO)  # esos, por plantas


async def test_dividir_en_dos_deja_las_mitades_en_su_planta_del_edificio(tmp_path: Path) -> None:
    from bot.services import housing_view as view
    from bot.services.housing_streets import building_of

    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    await give(economy, ANA, 10_000_000)
    atico = LISTING_BY_KEY["canteras_atico"]  # 110 m²
    await service.buy(GUILD, ANA, atico.key, 100)
    market = await service.market(GUILD)
    option = view.split_state(market, atico.key, ANA)["options"][0]
    assert option["name"] == "Dividir en dos" and not option["problem"]
    done, _balance = await service.split(GUILD, ANA, atico.key, rules.FLOORS)
    assert list(done.units) == [f"{atico.key}~1", f"{atico.key}~2"]
    market = await service.market(GUILD)
    assert market.whole_homes(ANA) == 2
    building = building_of(atico.key)
    cut = view.building_state(market, building.key, ANA, {})
    level = next(u.level for u in building.units if u.key == atico.key)
    floor = next(f for f in cut["floors"] if f["level"] == level)
    halves = [u for u in floor["units"] if u["key"].startswith(atico.key)]
    assert [u["half"] for u in halves] == ["izda.", "dcha."]
    assert all(u["mine"] and not u["divisible"] for u in halves)
    assert sum(u["area"] for u in halves) == atico.m2
    assert view.units_state(market, atico.key, ANA, {})["mode_name"] == "Dividido en dos"
