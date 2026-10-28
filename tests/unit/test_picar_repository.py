"""Pruebas de bot.repositories.picar: las tiradas, los récords y la racha de días."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from bot.repositories.picar import PicarRepository

GUILD = 1
OTHER_GUILD = 2
USER = 10
OTHER = 11
DAY = date(2026, 10, 28)


async def make_repository(tmp_path: Path) -> PicarRepository:
    repository = PicarRepository(tmp_path / "bot.sqlite3")
    await repository.initialize()
    return repository


async def dig(
    repository: PicarRepository, *, user: int = USER, guild: int = GUILD, row: int = 40,
    pick: str = "iron", day: date = DAY,
):  # fmt: skip
    return await repository.record(
        guild, user, pick=pick, seed=7, params="hash", cost=200, payout=100, units=5_000,
        multiplier=1, max_row=row, dragon=False, day=day,
    )  # fmt: skip


async def test_devuelve_los_records_de_antes_de_la_tirada(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    first = await dig(repository, row=40)
    assert (first.best, first.server_best) == (None, None)
    second = await dig(repository, user=OTHER, row=90)
    assert (second.best, second.server_best) == (None, 40)
    third = await dig(repository, row=10)
    assert (third.best, third.server_best) == (40, 90)
    # Cada pico y cada servidor llevan sus récords.
    assert (await dig(repository, pick="gold")).server_best is None
    assert (await dig(repository, guild=OTHER_GUILD)).server_best is None
    assert await repository.records(GUILD, USER) == {"iron": 40, "gold": 40}


async def test_la_racha_cuenta_dias_seguidos_y_se_corta_al_saltarse_uno(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    assert (await dig(repository)).streak == 1
    assert (await dig(repository)).streak == 1  # el mismo día no suma
    assert (await dig(repository, day=DAY + timedelta(days=1))).streak == 2
    assert (await dig(repository, day=DAY + timedelta(days=2))).streak == 3
    assert (await dig(repository, day=DAY + timedelta(days=4))).streak == 1
    assert (await dig(repository, user=OTHER, day=DAY + timedelta(days=4))).streak == 1


async def test_salir_del_servidor_borra_tiradas_y_rachas(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await dig(repository)
    await dig(repository, day=DAY + timedelta(days=1))
    await repository.delete_guild_data(GUILD)
    assert await repository.records(GUILD, USER) == {}
    assert (await dig(repository, day=DAY + timedelta(days=2))).streak == 1
