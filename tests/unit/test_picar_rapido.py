"""El motor en C del calibrador (`docs/picar_rapido.c`) juega igual que el de Python.

Si se desviaran, la calibración no valdría para el bot (y el bot lo vería al arrancar,
con la huella). El script vive en `docs/` (no es paquete), así que se carga por ruta. Se
salta si no hay compilador de C.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "picar_rapido.py"


def _load():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("picar_rapido", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rapido = _load()
needs_cc = pytest.mark.skipif(rapido.compiler() is None, reason="no hay compilador de C")


@needs_cc
def test_el_azar_de_c_es_el_de_python() -> None:
    for text in ("picar:mundo:7:3", "picar:fisica:999999", "picar:barranco:0:-1", "x" * 200):
        python = random.Random(text)
        assert rapido.mt_sample(text, 700) == [python.getrandbits(32) for _ in range(700)]


@needs_cc
def test_el_motor_en_c_juega_igual_que_el_de_python() -> None:
    rapido.check(25)
    # Semillas grandes, como las del final de la biblioteca.
    for seed in (999_999, 123_456_789):
        for key in ("wood", "netherite"):
            fast = rapido.simulate(seed, key)
            slow = rapido.ph.simulate(seed, key, record=False)
            assert fast.fingerprint() == slow.fingerprint()
