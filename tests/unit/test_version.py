"""Pruebas de la versión del bot (`bot.__version__`) y de `CHANGELOG.md`.

Comprueban lo que se puede comprobar de la sección «Versiones» de la Biblia:
el número es válido, `CHANGELOG.md` tiene su entrada arriba y el historial va
de mayor a menor sin repetir. Si el escalón elegido es el correcto lo revisa
quien fusiona.
"""

from __future__ import annotations

import re
from pathlib import Path

from bot import __version__

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
_ENTRY = re.compile(r"^### (\d+)\.(\d+)\.(\d+) · \d{4}-\d{2}-\d{2}$", re.MULTILINE)


def _versions() -> list[tuple[int, int, int]]:
    return [
        tuple(map(int, match)) for match in _ENTRY.findall(CHANGELOG.read_text(encoding="utf-8"))
    ]


def test_la_version_es_mayor_menor_parche() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_el_changelog_empieza_por_la_version_actual() -> None:
    versions = _versions()

    assert versions, "CHANGELOG.md no tiene entradas «### X.Y.Z · AAAA-MM-DD»"
    assert ".".join(map(str, versions[0])) == __version__, (
        "sube `__version__` y añade su entrada arriba en CHANGELOG.md a la vez"
    )


def test_el_changelog_va_de_mayor_a_menor_sin_repetir() -> None:
    versions = _versions()

    assert versions == sorted(set(versions), reverse=True)
