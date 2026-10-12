"""Pruebas de bot.repositories.gallos: la ficha del gallo de cada miembro en SQLite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from bot.repositories.gallos import GallosRepository
from bot.services.gallos import Finish, default_name


async def make_repository(tmp_path: Path) -> GallosRepository:
    repository = GallosRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


async def test_un_gallo_nuevo_lleva_el_nombre_de_serie(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    record = await repository.get(1, 10)
    assert record.name == default_name(10) and record.fights == 0


async def test_pelear_y_renombrar_se_guardan(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    day = date(2026, 10, 12)
    before, after = await repository.record_fight(1, 10, won=True, finish=Finish.KO, day=day)
    assert before.fights == 0 and (after.fights, after.wins, after.kos) == (1, 1, 1)
    renamed = await repository.rename(1, 10, "Espolón de Oro")
    assert renamed.name == "Espolón de Oro" and renamed.wins == 1
    stored = await repository.get(1, 10)
    assert stored == renamed and stored.last_day == day


async def test_ranking_y_borrar_servidor(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    day = date(2026, 10, 12)
    await repository.record_fight(1, 10, won=True, finish=Finish.KO, day=day)
    await repository.record_fight(1, 11, won=False, finish=Finish.KO, day=day)
    await repository.record_fight(2, 12, won=True, finish=Finish.KO, day=day)
    ranking = await repository.ranking(1)
    assert [user for user, _ in ranking] == [10, 11]
    await repository.delete_guild_data(1)
    assert await repository.ranking(1) == []
    assert [user for user, _ in await repository.ranking(2)] == [12]
