"""Pruebas de bot.repositories.horses: la forma del establo y el bote del Gran Premio."""

from __future__ import annotations

from pathlib import Path

from bot.repositories.horses import HorseRepository, Meta
from bot.services.horses import FORM_SIZE, GRAND_PRIX_POT


async def make_repo(tmp_path: Path) -> HorseRepository:
    repository = HorseRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


async def test_un_servidor_nuevo_empieza_de_cero(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    assert await repository.records(1) == {}
    assert await repository.meta(1) == Meta(pot=GRAND_PRIX_POT)


async def test_cada_carrera_suma_forma_y_contadores(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    for n in range(FORM_SIZE + 2):
        await repository.record_race(
            1, [("falcon", 1 + n % 3), ("uco", 2)], now=float(n), grand_prix=False
        )
    records = await repository.records(1)
    assert records["falcon"].races == FORM_SIZE + 2
    assert len(records["falcon"].form) == FORM_SIZE
    assert records["uco"].last_race == FORM_SIZE + 1
    meta = await repository.meta(1)
    assert (meta.races, meta.since_grand_prix) == (FORM_SIZE + 2, FORM_SIZE + 2)


async def test_el_gran_premio_reinicia_la_cuenta_y_fija_el_bote(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    await repository.record_race(1, [("falcon", 1)], now=5.0, grand_prix=False)
    meta = await repository.record_race(1, [("falcon", 1)], now=9.0, grand_prix=True, pot=7_500)
    assert (meta.since_grand_prix, meta.last_grand_prix, meta.pot) == (0, 9.0, 7_500)


async def test_una_carrera_normal_no_pisa_el_bote(tmp_path: Path) -> None:
    """Dos canales: el Gran Premio deja el bote en 7.500 y la normal no lo devuelve atrás."""
    repository = await make_repo(tmp_path)
    await repository.record_race(1, [("falcon", 1)], now=1.0, grand_prix=True, pot=7_500)
    meta = await repository.record_race(1, [("uco", 1)], now=2.0, grand_prix=False)
    assert meta.pot == 7_500
    assert (await repository.meta(1)).pot == 7_500


async def test_salir_del_servidor_lo_borra_todo(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    await repository.record_race(1, [("falcon", 1)], now=1.0, grand_prix=False)
    await repository.record_race(2, [("falcon", 1)], now=1.0, grand_prix=False)
    await repository.delete_guild_data(1)
    assert await repository.records(1) == {}
    assert await repository.meta(1) == Meta(pot=GRAND_PRIX_POT)
    assert "falcon" in await repository.records(2)
