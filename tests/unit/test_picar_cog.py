"""Pruebas del cog del pico (`bot.cogs.picar`) que no necesitan el bot entero."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from bot.cogs import picar as cog_module
from bot.services import picar_scene
from bot.services.picar_scene import Part


class FakeScene:
    def __init__(self, parts: list[Part]) -> None:
        self._parts = parts

    async def parts(self, outcome, values, *, budget):  # noqa: ANN001, ANN201
        for part in self._parts:
            yield part


async def play(parts: list[Part]) -> list[float]:
    cog = object.__new__(cog_module.Picar)
    cog.calibration = SimpleNamespace(picks={"iron": SimpleNamespace(values={})})
    cog.scene = FakeScene(parts)
    shown: list[float] = []

    async def edit_fn(**_kwargs: object) -> None:
        shown.append(time.monotonic())

    start = time.monotonic()
    await cog.play_parts(
        SimpleNamespace(pick="iron"),  # type: ignore[arg-type]
        edit_fn=edit_fn,
        embed_for=lambda index: None,  # type: ignore[arg-type,return-value]
        view=None,
        limit=1,
    )
    shown.append(time.monotonic())
    return [moment - start for moment in shown]


@pytest.fixture
def quick_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(picar_scene, "UPLOAD_FIXED_MS", 200)
    monkeypatch.setattr(cog_module, "UPLOAD_FIXED_MS", 200)
    monkeypatch.setattr(cog_module, "REVEAL_MARGIN_SECONDS", 0)


async def test_tras_un_corte_el_siguiente_se_sube_antes_de_la_pausa(quick_uploads) -> None:
    # 600 ms en movimiento y una subida de 200 ms: se pide a los 400 ms, no a los 600.
    first = Part(b"a", ms=1_000, hold_ms=400, last=False, frames=10, cut=True)
    second = Part(b"b", ms=600, hold_ms=300, last=True, frames=5)
    shown = await play([first, second])
    assert 0.35 <= shown[1] - shown[0] < 0.5
    # El último espera a su pausa menos lo que tarda en subirse la imagen final.
    assert 0.05 <= shown[2] - shown[1] < 0.2


async def test_tras_el_rotulo_de_una_dimension_se_sube_al_empezar_el_rotulo(
    quick_uploads,
) -> None:
    banner = Part(b"a", ms=900, hold_ms=500, last=False, frames=10)
    after = Part(b"b", ms=400, hold_ms=200, last=True, frames=5)
    shown = await play([banner, after])
    assert 0.38 <= shown[1] - shown[0] < 0.5
