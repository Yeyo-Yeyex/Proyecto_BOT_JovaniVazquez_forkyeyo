"""Pruebas del repositorio de la tragaperras: calor guardado y giro diario.

Usan un SQLite temporal. Lo importante del giro diario es que dos clics el
mismo día no cobren dos giros.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.repositories.slots import SlotsRepository

GUILD = 1
USER = 10
OTHER = 11


async def make_repository(tmp_path: Path) -> SlotsRepository:
    repository = SlotsRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


async def test_el_calor_se_guarda_y_se_carga(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)

    assert await repository.load_heat(GUILD, USER) is None

    await repository.save_heat(GUILD, USER, 4, 1_000.5)
    assert await repository.load_heat(GUILD, USER) == (4, 1_000.5)

    await repository.save_heat(GUILD, USER, 7, 2_000.0)
    assert await repository.load_heat(GUILD, USER) == (7, 2_000.0)
    assert await repository.load_heat(GUILD, OTHER) is None


async def test_el_calor_sobrevive_a_un_reinicio(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await repository.save_heat(GUILD, USER, 3, 50.0)

    reopened = await make_repository(tmp_path)

    assert await reopened.load_heat(GUILD, USER) == (3, 50.0)


async def test_el_giro_diario_no_se_cobra_dos_veces_el_mismo_dia(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)

    assert await repository.daily(GUILD, USER) is None
    assert await repository.claim_daily(GUILD, USER, today="2026-10-09", streak=1) is True
    assert await repository.claim_daily(GUILD, USER, today="2026-10-09", streak=5) is False
    # El segundo intento no toca la racha.
    assert await repository.daily(GUILD, USER) == ("2026-10-09", 1)


async def test_el_giro_diario_de_otro_dia_actualiza_la_racha(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await repository.claim_daily(GUILD, USER, today="2026-10-09", streak=1)

    assert await repository.claim_daily(GUILD, USER, today="2026-10-10", streak=2) is True
    assert await repository.daily(GUILD, USER) == ("2026-10-10", 2)


async def test_dos_clics_a_la_vez_solo_cobran_un_giro(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)

    results = await asyncio.gather(
        *(repository.claim_daily(GUILD, USER, today="2026-10-09", streak=1) for _ in range(5))
    )

    assert sorted(results) == [False, False, False, False, True]


async def test_salir_del_servidor_borra_calor_y_giros(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await repository.save_heat(GUILD, USER, 2, 1.0)
    await repository.claim_daily(GUILD, USER, today="2026-10-09", streak=1)
    await repository.save_heat(GUILD + 1, USER, 2, 1.0)

    await repository.delete_guild_data(GUILD)

    assert await repository.load_heat(GUILD, USER) is None
    assert await repository.daily(GUILD, USER) is None
    assert await repository.load_heat(GUILD + 1, USER) == (2, 1.0)
