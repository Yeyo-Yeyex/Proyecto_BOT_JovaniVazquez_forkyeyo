"""Pruebas de la RIC y la ZEC de las sociedades de los negocios (Ley 19/1994): las reglas de
`bot.services.business_sl` y su paso por el Impuesto sobre Sociedades de cada domingo."""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.repositories.business import BusinessRepository
from bot.repositories.economy import EconomyRepository
from bot.repositories.work import WorkRepository
from bot.services import business_sl as sl
from bot.services import pala
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.negocio import BusinessError, BusinessService
from bot.services.pala import WorkService
from bot.services.taxes import weekly_yd
from bot.services.work import Wish

GUILD = 1
USER = 10
# Martes 6 de octubre de 2026 a mediodía, hora canaria.
START = 1_791_284_400.0
DAY = 86_400
SUNDAY = date(2026, 10, 11)


def company(**extra: object) -> dict:
    info = sl.new_company(300_000, 300_000, 0.0)
    info.update(extra)
    return info


def test_la_ric_saca_de_la_base_lo_dotado_y_lo_deja_indisponible() -> None:
    plain = company()
    base = 200_000
    normal = sl.settle_week(plain, base, weekly_sales=400_000, day=SUNDAY)
    info = company(ric_share=0.9)
    week = sl.settle_week(info, base, weekly_sales=400_000, day=SUNDAY)
    assert week["ric"] == round(base * 0.9)
    assert week["tax"] == sl.corporate_tax(base - week["ric"], weekly_sales=400_000)[0]
    assert week["tax"] < normal["tax"]
    # La dotación no se puede repartir: no está en lo disponible (la reserva legal sale de lo
    # que queda tras ella).
    assert info["retained"] == week["profit"] - week["reserve"] - week["ric"]
    assert plain["retained"] == normal["profit"] - normal["reserve"]
    assert sl.ric_pending(info) == sl.ric_locked(info) == week["ric"]
    assert sl.ric_due(info) == (SUNDAY + timedelta(days=sl.RIC_NIGHTS)).isoformat()


def test_sin_beneficio_no_hay_ric() -> None:
    info = company(ric_share=0.9)
    week = sl.settle_week(info, -50_000, weekly_sales=100_000, day=SUNDAY)
    assert week["ric"] == 0 and sl.ric_ledger(info) == []


def test_invertir_materializa_de_la_mas_vieja_y_lo_que_vence_se_integra() -> None:
    info = company(ric_share=0.5)
    sl.settle_week(info, 100_000, weekly_sales=200_000, day=SUNDAY)
    later = SUNDAY + timedelta(days=7)
    sl.settle_week(info, 100_000, weekly_sales=200_000, day=later)
    first, second = (e["amount"] for e in sl.ric_ledger(info))
    assert sl.ric_invest(info, first + 1_000, later) == first + 1_000
    assert sl.ric_ledger(info)[0]["left"] == 0 and sl.ric_ledger(info)[0]["done"]
    assert sl.ric_pending(info) == second - 1_000
    # A las tres semanas de la segunda, lo que queda vence y se integra en la base.
    due = later + timedelta(days=sl.RIC_NIGHTS)
    retained = info["retained"]
    week = sl.settle_week(company(), 0, weekly_sales=0)  # sin `day`: sin RIC
    assert week["integrated"] == 0
    info["ric_share"] = 0.0
    week = sl.settle_week(info, 0, weekly_sales=0, day=due)
    assert week["integrated"] == second - 1_000
    assert week["tax"] == sl.corporate_tax(second - 1_000, weekly_sales=0)[0]
    # Vuelve entera a lo disponible (menos el impuesto que paga ahora).
    assert info["retained"] == retained - week["tax"] + second
    # Lo invertido se libera a los 5 años (semanas) de invertirlo.
    kept = sl.ric_locked(info)
    assert kept == first
    integrated, released = sl.ric_settle(info, later + timedelta(days=sl.RIC_KEEP_NIGHTS))
    assert (integrated, released) == (0, first) and sl.ric_ledger(info) == []


def test_al_liquidar_se_integra_lo_que_queda_de_la_ric() -> None:
    info = company(ric_share=0.9)
    week = sl.settle_week(info, 100_000, weekly_sales=200_000, day=SUNDAY)
    assert sl.ric_close(info) == week["ric"] and sl.ric_ledger(info) == []


def test_la_zec_es_el_4_por_ciento_hasta_su_base_maxima() -> None:
    tax, rate = sl.zec_tax(100_000, 6, "gc", weekly_sales=200_000)
    assert tax == 4_000 and rate == 0.04
    cap = round(weekly_yd(1_800_000 + 500_000))  # un empleo por encima del mínimo
    big = cap + 1_000_000
    tax, _rate = sl.zec_tax(big, 6, "gc", weekly_sales=big * 2)
    rest, _ = sl.corporate_tax(1_000_000, weekly_sales=big * 2)
    assert tax == round(cap * 0.04) + rest


def test_quien_puede_entrar_en_la_zec() -> None:
    day = date(2026, 10, 22)
    assert "anexo" in (sl.zec_blocker("cafeteria", "gc", 9, day) or "")
    assert "6 empleos" in (sl.zec_blocker("taller", "gc", 5, day) or "")
    assert sl.zec_blocker("taller", "tf", 6, day) is None
    assert "31/12/2026" in (sl.zec_blocker("taller", "gc", 6, date(2027, 1, 1)) or "")


# -- Con el servicio -------------------------------------------------------------------


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def sin_azar_de_la_pala(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pala, "wish_for", lambda *args: Wish("no", "Nada.", lambda f: False))
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


async def make(tmp_path: Path, clock: Clock) -> BusinessService:
    database = tmp_path / "bot.db"
    economy_repo = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    work_repo = WorkRepository(database)
    await work_repo.initialize()
    work = WorkService(work_repo, economy, clock=clock, rng=random.Random(1))
    repository = BusinessRepository(database)
    await repository.initialize()
    service = BusinessService(repository, economy, work, clock=clock, rng=random.Random(1))
    work.business = service
    return service


async def open_company(service: BusinessService, sector: str = "cafeteria") -> None:
    await service.economy.grant(GUILD, USER, amount=5_000_000, reason="prueba")
    await service.open(
        GUILD, USER, sector=sector, barrio="triana", name="Casa", palette=0, emblem=0
    )
    await service.constitute(GUILD, USER, 300_000)
    await service.contribute(GUILD, USER, 1_000_000)


async def test_la_ric_se_dota_el_domingo_y_se_materializa_con_una_mejora(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_company(service)
    with pytest.raises(BusinessError):
        await service.set_ric(GUILD, USER, 0.7)
    await service.set_ric(GUILD, USER, 0.9)
    business = await service.business(GUILD, USER)
    assert business is not None
    # Unas ventas de la semana para que haya base el domingo.
    business.add("ventas", 400_000)
    await service.repository.save(GUILD, business)
    clock.now = START + 6 * DAY  # el lunes: se cierra el domingo
    runs = await service.close_nights(GUILD)
    week = next(r.corporate for r in runs if r.corporate)
    assert week["ric"] > 0
    business = await service.business(GUILD, USER)
    assert business is not None
    assert sl.ric_pending(business.company) == week["ric"]
    await service.buy_equipment(GUILD, USER, "terraza")
    business = await service.business(GUILD, USER)
    assert business is not None
    assert sl.ric_pending(business.company) < week["ric"]


async def test_la_zec_pide_un_taller_con_seis_empleos(tmp_path: Path) -> None:
    service = await make(tmp_path, Clock())
    await open_company(service, "taller")
    with pytest.raises(BusinessError, match="6 empleos"):
        await service.zec(GUILD, USER, join=True)
    business = await service.business(GUILD, USER)
    assert business is not None
    assert SECTOR_BY_KEY[business.sector].key in sl.ZEC_SECTORS
