"""Pruebas de bot.repositories.sqlite: la conexión común en modo WAL."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.repositories import sqlite
from bot.repositories.economy import EconomyRepository
from bot.services.economy import STARTING_BALANCE


def test_la_conexion_usa_wal_y_no_sincroniza_en_cada_commit(tmp_path: Path) -> None:
    connection = sqlite.connect(tmp_path / "datos" / "bot.sqlite3")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        # 1 = NORMAL: en modo WAL, un commit no espera al disco.
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert connection.execute("SELECT 1 AS uno").fetchone()["uno"] == 1
    finally:
        connection.close()


async def test_los_repositorios_dejan_la_base_en_wal(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    repository = EconomyRepository(path, starting_balance=STARTING_BALANCE)
    await repository.initialize()

    # Una conexión cualquiera, sin pasar por el módulo, ve el modo guardado en el archivo.
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        raw.close()
