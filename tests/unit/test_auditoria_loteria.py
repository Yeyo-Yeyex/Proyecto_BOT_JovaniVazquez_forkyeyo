"""La auditoría de las loterías (`docs/auditoria_loteria.py`), corta, en cada prueba.

Juega un par de meses de cada juego de bote con el código real y comprueba que
ninguna categoría cobra más que una superior y que lo que pone el Estado se queda
en lo que sube el bote mínimo. El script vive en `docs/` (no es paquete), así que se
carga por ruta.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from bot.services import lottery as lot

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "auditoria_loteria.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("auditoria_loteria", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


al = _load()


@pytest.mark.parametrize("key", [g.key for g in lot.GAMES if g.categories])
def test_los_sorteos_de_bote_no_invierten_premios_ni_vacian_el_estado(key: str) -> None:
    game = lot.GAME_BY_KEY[key]
    totals = al.simulate_pool(game, 60, 60, 1)
    assert totals["problems"] == []
    assert totals["draws"] > 0
    # Un par de meses dan mucha varianza: solo se mira que lo que pone el Estado al
    # mes no se dispare por encima de un bote de un mes entero.
    month = game.jackpot_floor + 30 * game.jackpot_daily
    assert -totals["state_per_month"] < 2 * month
