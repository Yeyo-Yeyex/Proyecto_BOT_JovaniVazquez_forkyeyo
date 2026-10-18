"""Pruebas de la fase 5 de la ampliación de `pisos` (8.4.0).

El vacacional con reseñas: la nota (huésped, muebles, imprevistos, ruido, barrio) mueve la
ocupación y da Superanfitrión; cada estancia paga limpieza y plataforma con su IGIC; y la
licencia sigue la Ley 6/2025 de Canarias (caduca, no se da en zona tensionada ni en obra
nueva y en un bloque la vota la comunidad).
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.repositories.economy import STATE_ACCOUNT_ID
from bot.repositories.housing import HousingError
from bot.services import housing as rules
from bot.services.housing_catalog import LISTING_BY_KEY, REVIEW_LINES
from bot.services.housing_furniture import (
    FURNITURE_KEYS,
    MAX_FURNITURE_STARS,
    NOMAD_BOOST,
    furniture_stars,
    nomad_boost,
)
from bot.services.taxes import igic
from tests.unit.test_housing import (
    ANA,
    BAJO,
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

DAY = date(2026, 10, 14)
CHALET = next(i for i in LISTING_BY_KEY.values() if i.kind == "chalet")


# -- Reglas --------------------------------------------------------------------------------


def test_la_nota_mueve_la_ocupacion_de_medio_a_uno_tres() -> None:
    assert rules.rating_factor(1.0, 2) == 1.0  # con pocas reseñas no cuenta
    assert rules.rating_factor(1.0, 10) == rules.rating_factor(1.5, 10) == 0.5
    assert rules.rating_factor(4.8, 10) == rules.rating_factor(5.0, 10) == 1.3
    assert 0.5 < rules.rating_factor(3.5, 10) < rules.rating_factor(4.5, 10) < 1.3


def test_superanfitrion_pide_48_y_diez_resenas_y_cobra_un_10_mas() -> None:
    assert rules.superhost(4.8, 10)
    assert not rules.superhost(4.8, 9) and not rules.superhost(4.7, 20)
    lease = rules.Lease(level=rules.VACATION_LEVEL, since=DAY)
    plain = rules.sign(BAJO, lease, "nordicos", DAY, 1.0)
    star = rules.sign(BAJO, replace(lease, rating=4.9, reviews=12), "nordicos", DAY, 1.0)
    assert star.rent == round(plain.rent * (1 + rules.SUPERHOST_PREMIUM), -1)


def test_las_estrellas_suben_con_muebles_y_bajan_con_averias_y_ruido() -> None:
    calm = Scripted([0.5], fill=0.5)  # sin ruido de azar: uniform(-0,6, 0,6) = 0

    def stars(**kw: object) -> int:
        args = {"furniture": 0.0, "pending": 0, "noisy": False, "rng": Scripted([0.5], fill=0.5)}
        args.update(kw)
        return rules.review_stars(BAJO, "nordicos", **args)  # type: ignore[arg-type]

    base = rules.review_stars(BAJO, "nordicos", furniture=0.0, pending=0, noisy=False, rng=calm)
    assert stars(furniture=MAX_FURNITURE_STARS) >= base
    assert stars(pending=2) < base
    assert stars(pending=2, noisy=True) <= stars(pending=2)
    worst = rules.review_stars(BAJO, "despedida", furniture=0.0, pending=5, noisy=True,
                               rng=Scripted([0.0]))  # fmt: skip
    assert worst == 1
    best = rules.review_stars(BAJO, "nordicos", furniture=0.8, pending=0, noisy=False,
                              rng=Scripted([0.99]))  # fmt: skip
    assert best == 5


def test_cada_estancia_paga_plataforma_y_al_irse_la_limpieza() -> None:
    assert rules.stay_costs(10_000, out=False) == 1_500
    assert rules.stay_costs(10_000, out=True) == 1_500 + rules.CLEANING_FEE


def test_la_ley_canaria_no_da_licencia_en_zona_tensionada_ni_en_obra_nueva() -> None:
    assert rules.license_problem(BAJO, index=1.0, promotion=False) is None
    assert "tensionada" in (rules.license_problem(BAJO, index=1.2, promotion=False) or "")
    assert "10 años" in (rules.license_problem(BAJO, index=1.0, promotion=True) or "")
    assert rules.needs_neighbours(BAJO)  # un bajo está en un bloque
    assert not rules.needs_neighbours(CHALET)
    building = next(i for i in LISTING_BY_KEY.values() if i.kind == "edificio")
    assert not rules.needs_neighbours(building)  # el edificio entero es toda la comunidad


def test_los_muebles_suben_la_nota_con_tope_y_la_fibra_atrae_nomadas() -> None:
    assert furniture_stars(frozenset(), "playa", None) == 0
    assert furniture_stars(FURNITURE_KEYS, "playa", "numerosa") == MAX_FURNITURE_STARS
    beach = furniture_stars({"mueble_playa"}, "playa", None)
    assert beach > furniture_stars({"mueble_playa"}, "rural", None)
    assert furniture_stars({"mueble_cuna"}, "playa", "numerosa") > furniture_stars(
        {"mueble_cuna"}, "playa", "nordicos"
    )
    assert nomad_boost({"mueble_fibra"}) == NOMAD_BOOST
    assert nomad_boost({"mueble_aire"}) == 1.0


def test_hay_frases_de_resena_para_cada_estrella() -> None:
    assert set(REVIEW_LINES) == {1, 2, 3, 4, 5}
    assert all(len(lines) >= 3 for lines in REVIEW_LINES.values())


# -- Con la base de datos --------------------------------------------------------------


def _vacation(tmp_path: Path, **extra: object) -> None:
    sets = ", ".join(f"{k} = ?" for k in extra)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', level = 'vacacional', license = 1, "
            "license_until = '2026-12-31', tenant = 'nordicos', rent = 20000, "
            "until = '2026-10-14'" + (", " + sets if sets else ""),
            tuple(extra.values()),
        )


async def test_al_irse_el_huesped_paga_limpieza_y_plataforma_y_deja_resena(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rules, "REVIEW_CHANCE", 1.0)
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    _vacation(tmp_path)
    asked: list[int] = []

    async def furniture(member: int) -> frozenset[str]:
        asked.append(member)
        return frozenset({"mueble_aire", "mueble_colchon"})

    paid = housing_taxes(tmp_path, "igic_gastos")  # el de la notaría al comprar
    clock.now = at(15).timestamp()
    (run,) = await service.close_days(GUILD, furniture=furniture)

    assert asked == [ANA]
    stay = rules.stay_costs(20_000, out=True)
    assert run.cleanings == [((ANA,), BAJO.key)]
    assert housing_taxes(tmp_path, "igic_gastos") - paid == igic(stay, rules.SERVICES_IGIC)
    ((owners, key, stars),) = run.reviews
    assert owners == (ANA,) and key == BAJO.key and 1 <= stars <= 5
    market = await service.market(GUILD)
    (review,) = market.reviews[BAJO.key]
    assert review.guest == "nordicos" and review.stars == stars
    lease = market.leases[BAJO.key]
    assert lease.reviews == 1 and lease.rating == stars
    assert lease.status == rules.VACANT  # se fue
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)
    # Si el inmueble vuelve al mercado, se va sin reseñas.
    await service.sell(GUILD, ANA, BAJO.key, 100)
    assert BAJO.key not in (await service.market(GUILD)).reviews


async def test_un_dia_de_estancia_paga_la_plataforma_pero_no_la_limpieza(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    _vacation(tmp_path, until="2026-10-20")
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    paid = housing_taxes(tmp_path, "igic_gastos")
    clock.now = at(15).timestamp()
    (run,) = await service.close_days(GUILD)
    assert run.cleanings == [] and run.reviews == []
    stay = rules.stay_costs(20_000, out=False)
    assert housing_taxes(tmp_path, "igic_gastos") - paid == igic(stay, rules.SERVICES_IGIC)
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) > state


async def test_la_licencia_se_renueva_fuera_de_zona_tensionada(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET level = 'vacacional', license = 1, "
            "license_until = '2026-10-14'"
        )
    clock.now = at(15).timestamp()
    (run,) = await service.close_days(GUILD)
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.license and lease.license_until == date(2026, 10, 14) + timedelta(
        days=rules.LICENSE_DAYS
    )
    assert any(n.kind == "licencia_renovada" and n.user_id == ANA for n in run.news)


async def test_la_licencia_caduca_en_zona_tensionada_y_vuelve_a_referencia(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET level = 'vacacional', license = 1, "
            "license_until = '2026-10-14'"
        )
        connection.execute(
            "INSERT OR REPLACE INTO housing_index (guild_id, barrio, value) VALUES (?, ?, 1.2)",
            (GUILD, BAJO.barrio),
        )
    clock.now = at(15).timestamp()
    (run,) = await service.close_days(GUILD)
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert not lease.license and lease.license_until is None
    assert lease.level == "referencia"
    kinds = [n.kind for n in run.news]
    assert "licencia_caduca" in kinds and "tope_ley" not in kinds


async def test_las_licencias_de_antes_empiezan_a_contar_al_cerrar(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_leases SET license = 1, license_until = NULL")
    clock.now = at(15).timestamp()
    await service.close_days(GUILD)
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.license_until == date(2026, 10, 14) + timedelta(days=rules.LICENSE_DAYS)


async def test_la_comunidad_puede_votar_que_no_y_no_se_paga_la_tasa(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)  # Scripted: 0,99, la comunidad dice no
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    before = await economy.balance(GUILD, ANA)
    fee, balance = await service.buy_license(GUILD, ANA, BAJO.key)
    assert fee == 0 and balance == before
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert not lease.license and lease.license_denied_until == DAY + timedelta(
        days=rules.LICENSE_RETRY_DAYS
    )
    service.rng = Scripted([0.0])
    with pytest.raises(HousingError, match="votó que no"):
        await service.buy_license(GUILD, ANA, BAJO.key)
    clock.now = at(21).timestamp()  # una semana después vuelve a votar y dice que sí
    fee, _balance = await service.buy_license(GUILD, ANA, BAJO.key)
    assert fee == rules.LICENSE_FEE
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.license and lease.license_until == date(2026, 10, 21) + timedelta(
        days=rules.LICENSE_DAYS
    )


async def test_sin_licencia_nueva_en_zona_tensionada(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT OR REPLACE INTO housing_index (guild_id, barrio, value) VALUES (?, ?, 1.2)",
            (GUILD, BAJO.barrio),
        )
    with pytest.raises(HousingError, match="tensionada"):
        await service.buy_license(GUILD, ANA, BAJO.key)
    with pytest.raises(HousingError):
        await service.buy_license(GUILD, BEA, BAJO.key)  # no es suyo
