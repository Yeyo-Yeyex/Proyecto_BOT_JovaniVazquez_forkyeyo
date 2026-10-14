"""Pruebas de bot.services.housing_scene: el panel de `pisos` con canvas (Node, Chromium o Pillow).

Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la
raíz). La comparación con Chromium se salta también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io
import time
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready, own_node

from bot.repositories.housing import Holding, Market, MemberDay, News
from bot.services import browser_scene, node_scene
from bot.services import housing as rules
from bot.services import housing_view as view
from bot.services.browser_scene import BrowserScene
from bot.services.housing_catalog import LISTING_BY_KEY, LISTINGS
from bot.services.housing_scene import SCENE, H, HousingScene, W

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
SIZE = (W, H)
META = {"w": W, "h": H}

#: Un inmueble de cada tipo, para que se dibujen todas las fachadas.
BY_KIND = {
    kind: next(item.key for item in LISTINGS if item.kind == kind)
    for kind in dict.fromkeys(item.kind for item in LISTINGS)
}
LONGEST = max(LISTINGS, key=lambda item: len(item.title))


def market() -> Market:
    """Un mercado con dueños, inquilinos al día, impagos, desahucios y okupas."""
    holdings = {
        "schamann_bajo": {
            10: Holding("schamann_bajo", 10, 60, 260000, 280000, 0),
            20: Holding("schamann_bajo", 20, 25, 1, 1, 0),
        },
        "vegueta_edificio": {30: Holding("vegueta_edificio", 30, 100, 1, 1, 0)},
        "adeje_chalet": {10: Holding("adeje_chalet", 10, 100, 900000, 960000, 0)},
    }
    leases = {
        "schamann_bajo": rules.Lease(
            status="alquilado", tenant="enfermeros", rent=4300, until=date(2026, 12, 14)
        ),
        "vegueta_edificio": rules.Lease(
            status="impago", tenant="aleman", rent=90000, arrears=12000
        ),
        "adeje_chalet": rules.Lease(status="vacio"),
    }
    # El usuario 10 tiene un poco de todo: la cartera llena (10 inmuebles).
    for position, item in enumerate(LISTINGS[:9]):
        if item.key in holdings:
            continue
        holdings[item.key] = {10: Holding(item.key, 10, 5 + 10 * position, 1, 1, 0)}
        status = ("alquilado", "vacio", "impago", "desahucio", "okupa")[position % 5]
        tenant = None if status in ("vacio", "okupa") else "familia"
        leases[item.key] = rules.Lease(
            status=status,
            tenant=tenant,
            rent=1500 * (position + 1),
            arrears=3000,
            eviction_at=date(2026, 11, 20) if status == "desahucio" else None,
        )
    # Un inmueble con muchos dueños (uno de nombre larguísimo) para la barra de dueños.
    crowded = holdings[LISTINGS[0].key]
    for owner, shares in ((40, 12), (50, 9), (60, 7)):
        crowded[owner] = Holding(LISTINGS[0].key, owner, shares, 1, 1, 0)
    return Market(
        indexes={"schamann": 1.08, "adeje": 0.97, "vegueta": 1.0, "canteras": 1.0003},
        holdings=holdings,
        leases=leases,
    )


NAMES = {
    10: "Ana",
    20: "Bea",
    30: "Perro Sanxe",
    40: "Jovani el implacable de Moncloa",
    50: "Carlos",
    60: "Diego",
}
NEWS = [
    News("schamann_bajo", date(2026, 10, 14), "firma", 0, None, "enfermeros"),
    News("schamann_bajo", date(2026, 10, 13), "averia", 3200, None, "enfermeros"),
    News("schamann_bajo", date(2026, 10, 12), "compra", 0, 20, None),
    News("schamann_bajo", date(2026, 10, 11), "vecino", 0, None, "enfermeros"),
]


def checkout(key: str, **kwargs: Any) -> dict[str, Any]:
    return view.checkout_state(LISTING_BY_KEY[key], **kwargs)


def samples() -> dict[str, dict[str, Any]]:
    """Las pantallas de muestra: las cinco, con casos largos, vacíos y de todos los tipos."""
    m = market()
    week = MemberDay(30000, 1200, 4000)
    out: dict[str, dict[str, Any]] = {
        "mapa": view.map_state(m, 10, "Ana", 120000),
        "barrio_schamann": view.barrio_state(m, "schamann", 10, NAMES),
        "barrio_vegueta": view.barrio_state(m, "vegueta", 10, NAMES),
        "ficha_schamann_bajo": view.listing_state(m, "schamann_bajo", 10, NAMES, NEWS),
        "ficha_adeje_chalet": view.listing_state(m, "adeje_chalet", 10, NAMES, []),
        "cartera": view.portfolio_state(m, 10, "Ana", 120000, week, NEWS, NAMES),
        "cartera_vacia": view.portfolio_state(m, 99, "Nadie", 500, MemberDay(), [], NAMES),
        "caja_compra": checkout(
            "schamann_bajo",
            selling=False,
            lines=[
                ("Precio (15 participaciones)", 65100, "normal"),
                ("ITP 6,5 %", 4232, "tax"),
                ("Notaría y registro", 977, "normal"),
                ("Total", 70309, "total"),
            ],
            balance=120000,
            after=49691,
            note="Comprar sube el índice de Schamann un 0,2 %",
        ),
        "caja_venta": checkout(
            "schamann_bajo",
            selling=True,
            lines=[
                ("Precio (60 participaciones)", 281000, "normal"),
                ("Agencia 3 %", -8430, "normal"),
                ("IRPF de la ganancia 19 %", -1200, "tax"),
                ("Cobras", 271370, "total"),
            ],
            balance=49691,
            after=321061,
            note="Vendes al mercado",
            stamp="VENDIDO",
        ),
    }
    for kind, key in BY_KIND.items():
        out[f"ficha_{kind}"] = view.listing_state(m, key, 10, NAMES, NEWS if kind == "bajo" else [])
    out["ficha_titulo_largo"] = view.listing_state(m, LONGEST.key, 10, NAMES, NEWS)
    out["cartera_con_cero"] = view.portfolio_state(m, 20, "Bea", 5, MemberDay(), [], NAMES)
    out["ficha_muchos_duenos"] = view.listing_state(m, LISTINGS[0].key, 10, NAMES, NEWS)
    out["ficha_desahucio"] = view.listing_state(m, LISTINGS[3].key, 10, NAMES, [])
    out["barrio_isleta"] = view.barrio_state(m, "isleta", 10, NAMES)
    return out


NINE = [
    state
    for name, state in samples().items()
    if name
    in {
        "mapa",
        "barrio_schamann",
        "barrio_vegueta",
        "ficha_schamann_bajo",
        "ficha_adeje_chalet",
        "cartera",
        "cartera_vacia",
        "caja_compra",
        "caja_venta",
    }
]


def png_image(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == SIZE
    return image.convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


# -- Reserva con Pillow ------------------------------------------------------------------


async def test_sin_node_ni_chromium_cada_pantalla_cae_a_pillow() -> None:
    scene = HousingScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        pngs = await scene.paint(list(samples().values()))
        still = await scene.still(NINE[0])
    finally:
        await scene.close()
    assert scene.disabled
    assert len(pngs) == len(samples())
    for png in [*pngs, still]:
        assert len(png_image(png).getcolors(1 << 20) or []) > 20


async def test_sin_estados_no_se_pinta_nada() -> None:
    scene = HousingScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        assert await scene.paint([]) == []
    finally:
        await scene.close()


# -- Node ---------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> HousingScene:
    """Una escena con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = HousingScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_cada_pantalla_es_un_png_del_tamano_y_distinta(scene: HousingScene) -> None:
    states = list(samples().values())
    pngs = await scene.paint(states)
    assert not scene.painter.disabled and len(pngs) == len(states)
    images = [png_image(png) for png in pngs]
    for image in images:
        assert len(image.getcolors(1 << 20) or []) > 1000
    for first, other in zip(images, images[1:], strict=False):
        assert difference(first, other) > 0.05
    assert (await scene.still(states[3])) == pngs[3]


@needs_node
@module_loop
async def test_con_node_una_pantalla_desconocida_desactiva_el_pintor() -> None:
    other = HousingScene(executable_path=NO_CHROMIUM)
    try:
        png = (await other.paint([{"screen": "nada", "fallback": ["Nada"]}]))[0]
    finally:
        await other.close()
    assert other.painter.disabled
    png_image(png)  # cae a Pillow


@needs_node
@module_loop
async def test_las_pantallas_repartidas_pintan_lo_mismo_que_con_un_proceso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada pantalla es entera: repartir el dibujo entre procesos no cambia ni un byte."""
    states = NINE * 4
    node = own_node()
    sharing = HousingScene(executable_path=NO_CHROMIUM, node=node)
    try:
        # Abre los procesos de refuerzo: sin ellos, el primero lo pintaría todo.
        assert await sharing.painter.render(META, states[:8]) is not None
        pool = node_scene._pool(node)
        deadline = time.monotonic() + 20
        while len([w for w in pool.workers if w.alive and not w.busy]) < 3:
            assert time.monotonic() < deadline, "No se han abierto los procesos de refuerzo"
            await asyncio.sleep(0.05)
        alone = await sharing.painter.render(META, states, procs=1)
        assert alone is not None
        setups: list[str] = []
        call = node_scene._Process.call

        async def counted(self: node_scene._Process, request: dict, **kw: bool):  # noqa: ANN202
            setups.extend(["setup"] if request["call"] == "setup" else [])
            return await call(self, request, **kw)

        monkeypatch.setattr(node_scene._Process, "call", counted)
        split = await sharing.painter.render(META, states)
        assert split is not None
        # Cada tramo empieza con su `setup`: con uno solo no se habría repartido.
        assert len(setups) > 1, "El dibujo no se ha repartido"
        pngs = await sharing.paint(states)
        assert len(split) == len(alone) == len(pngs) == len(states)
        for a, b, png in zip(split, alone, pngs, strict=True):
            assert a["image"].tobytes() == b["image"].tobytes()
            assert png_image(png).tobytes() == a["image"].tobytes()
    finally:
        await sharing.close()
        await node_scene.shutdown()


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo(scene: HousingScene) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    states = list(samples().values())
    try:
        from_browser = await browser.render(META, states)
    finally:
        await browser.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    from_node = await scene.painter.render(META, states)
    assert from_node is not None
    a, b = await asyncio.gather(
        asyncio.to_thread(browser_scene.assemble, from_browser, SIZE),
        asyncio.to_thread(browser_scene.assemble, from_node, SIZE),
    )
    for name, x, y in zip(samples(), a, b, strict=True):
        assert difference(x.convert("RGB"), y.convert("RGB")) < 4, name
