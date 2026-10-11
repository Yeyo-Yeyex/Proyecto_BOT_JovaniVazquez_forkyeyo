"""Pruebas de bot.repositories.legislatura: partidas activas, cierre, perfiles y la diaria."""

from __future__ import annotations

from pathlib import Path

from bot.repositories.legislatura import LegislaturaRepository, Profile
from bot.services.legislatura.run import Run


async def make_repo(tmp_path: Path) -> LegislaturaRepository:
    repository = LegislaturaRepository(tmp_path / "bot.db", clock=lambda: 1_000.0)
    await repository.initialize()
    return repository


async def test_una_partida_activa_se_guarda_y_se_recupera_igual(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    run = Run.new("REPO0001")
    run_id = await repository.create(1, 2, run, entry=300)
    assert run_id
    run.start_session()
    await repository.save(run_id, run)
    loaded = await repository.active(1, 2)
    assert loaded is not None
    assert loaded[0] == run_id
    assert loaded[1].to_dict() == run.to_dict()


async def test_no_se_pueden_tener_dos_partidas_activas(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    assert await repository.create(1, 2, Run.new("A0000001"), entry=0)
    assert await repository.create(1, 2, Run.new("B0000001"), entry=0) is None
    assert await repository.create(1, 3, Run.new("C0000001"), entry=0)


async def test_cerrar_borra_el_estado_y_libera_la_plaza(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    run = Run.new("CIERRE01")
    run_id = await repository.create(1, 2, run, entry=300)
    assert run_id
    await repository.finish(run_id, run, status="perdida", payout=0)
    assert await repository.active(1, 2) is None
    assert await repository.create(1, 2, Run.new("OTRA0001"), entry=300)


async def test_el_perfil_se_guarda_entero(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    assert await repository.profile(1, 2) == Profile()
    profile = Profile(max_stake=3, unlocks={"tecnocracia"}, skins={"tapete": "tapete_falcon"})
    await repository.save_profile(1, 2, profile)
    assert await repository.profile(1, 2) == profile


async def test_la_diaria_guarda_el_mejor_resultado_y_ordena(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    day = "2026-10-11"
    await repository.record_daily(1, day, 10, 1, score=500, periods=2)
    await repository.record_daily(1, day, 10, 1, score=300, periods=1)
    await repository.record_daily(1, day, 11, 2, score=900, periods=2)
    assert await repository.daily_run(1, day, 10) == 1
    ranking = await repository.daily_ranking(1, day)
    assert [(e.user_id, e.score, e.periods) for e in ranking] == [(11, 900, 2), (10, 500, 2)]


async def test_borrar_un_servidor_no_toca_otros(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    await repository.create(1, 2, Run.new("SERV0001"), entry=0)
    await repository.create(9, 2, Run.new("SERV0002"), entry=0)
    await repository.delete_guild_data(1)
    assert await repository.active(1, 2) is None
    assert await repository.active(9, 2) is not None
