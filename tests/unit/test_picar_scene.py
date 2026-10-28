"""Pruebas del dibujo del pico (`bot.services.picar_scene`).

Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la raíz); la
comparación con Chromium, si no hay navegador en la máquina.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene
from bot.services.picar import Calibration, Outcome, play
from bot.services.picar_scene import (
    FINAL_HOLD_MS,
    HEIGHT,
    SCENE,
    TRANSITION_HOLD_MS,
    WIDTH,
    PicarScene,
    _rows_for,
    camera_path,
    segment_meta,
    story,
    swap_ms,
)

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")


@pytest.fixture(scope="module")
def cal() -> Calibration:
    return Calibration.load()


@pytest.fixture(scope="module")
def deep(cal: Calibration) -> Outcome:
    """Una tirada de diamante que cruza al Nether (semilla 0)."""
    outcome = play("diamond", cal, seed=0)
    assert outcome.run.max_row >= 250
    return outcome


def mean_difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def test_el_guion_tiene_una_pausa_por_dimension_y_el_cartel_al_final(
    cal: Calibration, deep: Outcome
) -> None:
    shots, tl = story(deep, cal.picks["diamond"].values)
    ends = [shot for shot in shots if shot.end]
    assert len(ends) == len(tl.dimensions) == 1
    assert ends[0].ms == TRANSITION_HOLD_MS and ends[0].state["banner"][0] == "ENTRANDO AL NETHER"
    assert shots[-1].ms == FINAL_HOLD_MS and shots[-1].state["card"]["alpha"] == 1
    assert all(shot.ms % 10 == 0 for shot in shots)  # el GIF mide en centésimas
    # Lo que se ve sale de la partida: la vida y el multiplicador acaban donde acaba ella.
    assert tl.life[-1] == deep.run.life
    assert tl.units[-1] == deep.units


def test_la_camara_nunca_deja_al_pico_fuera(deep: Outcome) -> None:
    camera = camera_path(deep.run)
    for (_, y, _), top in zip(deep.run.frames, camera[1:], strict=True):
        assert top < y * 32 < top + HEIGHT


def test_la_meta_lleva_el_trozo_de_mundo_que_se_ve(cal: Calibration, deep: Outcome) -> None:
    shots, tl = story(deep, cal.picks["diamond"].values)
    states = [shot.state for shot in shots[200:260]]
    rows = _rows_for(states)
    meta = segment_meta(tl.run, tl.gone, tl.cracks, rows, pick="diamond")
    assert len(meta["grid"]) == len(rows) and meta["top"] == rows.start
    for state in states:
        assert rows.start * 32 <= state["cam"] and state["cam"] + HEIGHT <= rows.stop * 32


def test_la_pausa_tapa_lo_que_tarda_en_subirse_el_siguiente() -> None:
    assert swap_ms(100_000) >= 1_800
    assert swap_ms(8 * 1024 * 1024) > swap_ms(2 * 1024 * 1024) > swap_ms(500_000)


@needs_node
@module_loop
async def test_la_tirada_sale_en_gif_que_caben_en_el_limite(
    cal: Calibration, deep: Outcome
) -> None:
    scene = PicarScene(executable_path="/no/existe/chromium")
    budget = 3 * 1024 * 1024
    values = cal.picks["diamond"].values
    try:
        parts = [part async for part in scene.parts(deep, values, budget=budget)]
        png = await scene.still(deep, values)
    finally:
        await scene.close()
    assert not scene.painter.disabled
    assert len(parts) >= 3 and parts[-1].last and not any(p.last for p in parts[:-1])
    for part in parts:
        gif = Image.open(io.BytesIO(part.gif))
        assert gif.format == "GIF" and gif.size == (WIDTH, HEIGHT)
        assert len(part.gif) <= budget * 1.15
        if not part.last:
            assert part.hold_ms >= swap_ms(len(part.gif)) - 50
    last = Image.open(io.BytesIO(parts[-1].gif))
    last.seek(last.n_frames - 1)
    final = Image.open(io.BytesIO(png)).convert("RGB")
    assert final.size == (WIDTH, HEIGHT)
    assert mean_difference(last.convert("RGB"), final) < 4


@needs_node
@module_loop
async def test_repartida_pinta_lo_mismo_que_de_un_tiron(cal: Calibration, deep: Outcome) -> None:
    shots, tl = story(deep, cal.picks["diamond"].values)
    states = [shot.state for shot in shots[100:136]]
    meta = segment_meta(tl.run, tl.gone, tl.cracks, _rows_for(states), pick="diamond")
    painter = NodeScene(SCENE, name="El pintor del pico")
    calls: list[str] = []
    original = painter._request

    def spy(call: str, args: list | None = None, **extra: object) -> dict:
        calls.append(call)
        return original(call, args, **extra)

    painter._request = spy  # type: ignore[method-assign]
    try:
        split = await painter.render(meta, states)
        setups = calls.count("setup")
        whole = await painter.render(meta, states, procs=1)
    finally:
        painter._request = original  # type: ignore[method-assign]
        await painter.close()
    assert split is not None and whole is not None
    assert setups > 1  # de verdad se repartió
    a = browser_scene.assemble(split, (WIDTH, HEIGHT))
    b = browser_scene.assemble(whole, (WIDTH, HEIGHT))
    assert [x.tobytes() for x in a] == [y.tobytes() for y in b]


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_el_pozo(cal: Calibration, deep: Outcome) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    shots, tl = story(deep, cal.picks["diamond"].values)
    painter = NodeScene(SCENE, name="El pintor del pico")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    picks = [0, 40, 200, len(shots) // 2]
    picks += [i for i, shot in enumerate(shots) if shot.end] + [len(shots) - 1]
    states = [shots[i].state for i in picks]
    try:
        for state in states:
            meta = segment_meta(tl.run, tl.gone, tl.cracks, _rows_for([state]), pick="diamond")
            from_browser = await browser.render(meta, [state])
            from_node = await painter.render(meta, [state])
            if from_browser is None:
                pytest.skip("Chromium no arranca en esta máquina")
            assert from_node is not None
            a, b = await asyncio.gather(
                asyncio.to_thread(browser_scene.assemble, from_browser, (WIDTH, HEIGHT)),
                asyncio.to_thread(browser_scene.assemble, from_node, (WIDTH, HEIGHT)),
            )
            assert mean_difference(a[0], b[0]) < 4, state["frame"]
    finally:
        await painter.close()
        await browser.close()
