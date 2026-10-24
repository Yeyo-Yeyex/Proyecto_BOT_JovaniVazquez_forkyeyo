"""Pruebas de la fase 5 de los negocios: las cadenas (`docs/negocios.md`, «Fase 5 al detalle»).

Con la base de datos de verdad, como `test_negocio_service.py`, del que se toman las piezas:
varios locales por dueño con las franjas y la cuota de la persona, la sociedad que lleva todos
sus locales (una caja, una gestoría por noche y un Sociedades por semana con lo de todos) y
cerrar un local de una sociedad que tiene más sin liquidarla.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_negocio_service import (
    DAY,
    GUILD,
    USER,
    Clock,
    give,
    make,
    open_cafe,
    play,
    sin_azar_de_la_pala,  # noqa: F401  (fixture autouse: sin sorpresas en la `pala`)
    wallet,
)

from bot.repositories.business import companies_in
from bot.repositories.economy import company_account
from bot.services import business_sl as sl
from bot.services.business import MAX_LOCALES, opening_quote
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.housing_catalog import BARRIO_BY_KEY
from bot.services.negocio import BusinessError, BusinessService


def sql(tmp_path: Path, query: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(query, args).fetchall()


async def open_shop(service: BusinessService, barrio: str = "vegueta") -> int:
    """Monta una tienda de barrio y devuelve su número."""
    business, _fee = await service.open(
        GUILD, USER, sector="tienda", barrio=barrio, name="Súper Pepe", palette=1, emblem=2
    )
    return business.id


async def stock_up(service: BusinessService, biz: int) -> None:
    business = await service.business(GUILD, USER, biz)
    assert business is not None
    for product in business.sector_info.products:
        await service.buy(GUILD, USER, product.key, 2, biz=biz)


# -- Varios locales ----------------------------------------------------------------------


async def test_un_dueno_lleva_varios_locales_con_sus_franjas_y_una_cuota(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    await give(service, 3_000_000)
    cafe = (await service.owned(GUILD, USER))[0].id
    shop = await open_shop(service)
    assert [b.id for b in await service.owned(GUILD, USER)] == [cafe, shop]
    # Dos del mismo sector en el mismo barrio se comerían el cupo.
    with pytest.raises(BusinessError, match="mismos clientes"):
        await service.open(
            GUILD, USER, sector="tienda", barrio="vegueta", name="Otra", palette=0, emblem=0
        )
    # Los turnos de los dos gastan las franjas de la misma persona.
    await stock_up(service, cafe)
    await stock_up(service, shop)
    for biz in (cafe, shop):
        turn = await service.start_turn(GUILD, USER, biz=biz)
        assert turn.business.id == biz
        clock.now = play(turn.game, clock.now)
        await service.finish_turn(GUILD, USER, turn)
    assert await service.franjas_today(GUILD, USER) == 2
    # La cuota de autónomo es una por persona: el segundo local no cobra otra.
    fees = sql(
        tmp_path,
        "SELECT COUNT(*) FROM economy_ledger WHERE user_id = ? AND reason LIKE '%autonomos%'",
        USER,
    )[0][0]
    assert fees == 1


async def test_no_se_pasa_del_maximo_de_locales(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await give(service, 10_000_000)
    barrios = ["triana", "vegueta", "isleta", "tafira", "telde"]
    for barrio in barrios[:MAX_LOCALES]:
        await open_shop(service, barrio)
    with pytest.raises(BusinessError, match=f"{MAX_LOCALES} locales"):
        await open_shop(service, barrios[MAX_LOCALES])


async def test_cerrar_un_local_de_autonomo_deja_los_demas(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    await give(service, 3_000_000)
    shop = await open_shop(service)
    closing = await service.close(GUILD, USER, biz=shop)
    assert closing.business.id == shop and not closing.company
    owned = await service.owned(GUILD, USER)
    assert [b.sector for b in owned] == ["cafeteria"]


# -- La sociedad de la cadena --------------------------------------------------------------


async def company_chain(tmp_path: Path, clock: Clock) -> tuple[BusinessService, int, int]:
    """Una cafetería y una tienda que pasan a la sociedad: `(servicio, café, tienda)`."""
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    await give(service, 3_000_000)
    cafe = (await service.owned(GUILD, USER))[0].id
    shop = await open_shop(service)
    await service.constitute(GUILD, USER, 300_000, biz=cafe)
    return service, cafe, shop


async def test_la_sociedad_se_queda_con_todos_los_locales(tmp_path: Path) -> None:
    clock = Clock()
    service, cafe, shop = await company_chain(tmp_path, clock)
    owned = await service.owned(GUILD, USER)
    assert all(b.is_company and b.company_id == cafe for b in owned)
    # Una sola sociedad: lo que se cambia desde un local se ve desde el otro.
    await service.contribute(GUILD, USER, 50_000, biz=shop)
    view = await service.company_view(GUILD, USER, cafe)
    assert view is not None and len(view.locales) == 2
    assert view.cash == 300_000 + 50_000
    assert view.business.company["contributed"] == view.locales[1].company["contributed"]
    # Lo que compra la tienda sale de la caja de la sociedad.
    await stock_up(service, shop)
    assert wallet(tmp_path, company_account(cafe))[1] < 350_000
    with pytest.raises(BusinessError, match="ya es una sociedad"):
        await service.constitute(GUILD, USER, 300_000, biz=shop)


async def test_la_sociedad_abre_el_local_nuevo_con_su_caja(tmp_path: Path) -> None:
    clock = Clock()
    service, cafe, _shop = await company_chain(tmp_path, clock)
    account = company_account(cafe)
    await service.contribute(GUILD, USER, 1_000_000)
    cash = wallet(tmp_path, account)[1]
    _total, owner = wallet(tmp_path, USER)
    garage, _fee = await service.open(
        GUILD, USER, sector="taller", barrio="telde", name="Taller Pepe", palette=0, emblem=0
    )
    quote = opening_quote(SECTOR_BY_KEY["taller"], BARRIO_BY_KEY["telde"])
    assert garage.is_company and garage.company_id == cafe
    assert wallet(tmp_path, account)[1] == cash - quote.total
    assert wallet(tmp_path, USER)[1] == owner  # el dueño no paga nada
    # La obra del local nuevo es un activo nuevo en Canarias: cuenta como inversión de la RIC.
    assert garage.assets and garage.assets[0]["key"] == "obra"


async def test_una_gestoria_por_noche_y_un_sociedades_con_lo_de_todos(tmp_path: Path) -> None:
    clock = Clock()
    service, cafe, shop = await company_chain(tmp_path, clock)
    await service.contribute(GUILD, USER, 1_000_000)
    for biz, sales in ((cafe, 120_000), (shop, 80_000)):
        fresh = await service.business(GUILD, USER, biz)
        assert fresh is not None
        fresh.add("ventas", sales)
        await service.repository.save(GUILD, fresh)
    clock.now += 6 * DAY  # del martes al lunes: se cierra la noche del domingo
    runs = await service.close_nights(GUILD)
    weeks = [r.corporate for r in runs if r.corporate is not None]
    assert len(weeks) == 1  # un solo Sociedades para los dos locales
    # La base es lo de la semana de los dos locales, sin lo de antes de constituirla.
    owned = await service.owned(GUILD, USER)
    totals: dict[str, int] = {}
    for business in owned:
        for key, value in business.last_week.items():
            totals[key] = totals.get(key, 0) + value
    assert totals["ventas"] >= 200_000
    assert weeks[0]["base"] == sl.tax_base(totals, owned[0].company["offset"])
    nights = len({r.day for r in runs})
    books = sql(
        tmp_path,
        "SELECT COUNT(*) FROM economy_ledger WHERE user_id = ? AND reason = 'sociedad:gestoria'",
        company_account(cafe),
    )[0][0]
    assert books == nights  # una gestoría por noche, no una por local
    taxes = sql(tmp_path, "SELECT SUM(tax) FROM economy_business_tax WHERE kind = 'sociedades'")
    assert taxes[0][0] == weeks[0]["tax"]
    company = (await service.business(GUILD, USER, shop)).company  # type: ignore[union-attr]
    assert company["results"] == [weeks[0]["profit"]]


async def test_cerrar_un_local_de_la_sociedad_no_la_liquida(tmp_path: Path) -> None:
    clock = Clock()
    service, cafe, shop = await company_chain(tmp_path, clock)
    await service.contribute(GUILD, USER, 1_000_000)
    account = company_account(cafe)
    shop_business = await service.business(GUILD, USER, shop)
    assert shop_business is not None
    shop_business.add("ventas", 90_000)
    await service.repository.save(GUILD, shop_business)
    _total, owner = wallet(tmp_path, USER)
    cash = wallet(tmp_path, account)[1]
    closing = await service.close(GUILD, USER, biz=shop)
    assert closing.company and closing.liquidated == 0
    assert wallet(tmp_path, USER)[1] == owner  # al socio no le llega nada: sigue en la caja
    assert wallet(tmp_path, account)[1] >= cash + closing.refund  # la fianza vuelve a la caja
    left = await service.owned(GUILD, USER)
    assert [b.id for b in left] == [cafe] and left[0].is_company
    carry = left[0].company["carry"]
    assert carry["sales"] >= 90_000
    # El domingo, lo de la tienda cerrada entra en Sociedades.
    clock.now += 6 * DAY
    runs = await service.close_nights(GUILD)
    week = next(r.corporate for r in runs if r.corporate is not None)
    left = await service.owned(GUILD, USER)
    offset = left[0].company["offset"]
    assert week["base"] == sl.tax_base(left[0].last_week, offset) + carry["base"]
    assert "carry" not in (await service.business(GUILD, USER, cafe)).company  # type: ignore[union-attr]
    # Cerrar el último sí liquida la sociedad.
    final = await service.close(GUILD, USER, biz=cafe)
    assert final.company and wallet(tmp_path, account)[1] == 0


async def test_el_patrimonio_cuenta_la_caja_de_la_sociedad_una_vez(tmp_path: Path) -> None:
    clock = Clock()
    service, cafe, _shop = await company_chain(tmp_path, clock)
    owned = await service.owned(GUILD, USER)
    cash = wallet(tmp_path, company_account(cafe))[1]
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        value = companies_in(connection, GUILD)[USER]
    from bot.services.business import book_value, stock_value

    expected = cash + sum(stock_value(b) + book_value(b) - b.rent_debt for b in owned)
    assert value == expected


def test_la_venta_mira_tres_ejercicios() -> None:
    assert sl.SALE_WEEKS == 3
