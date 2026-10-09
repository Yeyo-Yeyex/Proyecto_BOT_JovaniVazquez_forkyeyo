"""Pinta con Chromium las piezas de la máquina del pachinko y las guarda como PNG.

Uso, desde la raíz del repo:

    python docs/pachinko_piezas.py                      # pinta los cuatro tableros
    python docs/pachinko_piezas.py --tablero oni        # solo uno
    python docs/pachinko_piezas.py --comprobar          # no pinta: dice si los PNG están al día
    python docs/pachinko_piezas.py --navegador RUTA     # un Chromium concreto

Entradas: `src/bot/assets/pachinko/escena.html` (el dibujo en canvas) y los datos
que `bot.services.pachinko_render.piece_spec` saca para cada tablero: la geometría de
`bot.services.pachinko_physics.geometry_for` (los clavos que golpea la física), los
colores de `pachinko_render.THEMES` y las medidas de `bot.services.pachinko_pieces`. Esas
son la única fuente de las cifras: la escena no tiene ninguna propia.

Efectos: escribe en `src/bot/assets/pachinko/<tablero>/` los PNG de cada pieza (fondo con el
mueble, el rótulo, el campo y los bolsillos; bolsillos iluminados; bola; los cuatro pasos de
los adornos; bombillas; pantallas de cada modo; puntos de la reserva y chapa del contador) y
`piezas.json`, con un resumen de los datos con que se pintaron. El bot solo carga esos PNG:
no abre ningún navegador. Si falta alguno, esa pieza se dibuja con Pillow.

Hay que volver a ejecutarlo siempre que cambie la escena, la geometría del campo (clavos,
paredes, bolsillos, adornos), un tema (`THEMES`) o un color de `pachinko_render`. Con
`--comprobar` (que no necesita navegador y sale con 1 si algo está viejo) se ve si hace falta,
y la prueba `test_las_piezas_guardadas_estan_al_dia` falla si se olvida. Después de regenerar
hay que mirar el resultado (hoja de fotogramas de cada tablero) y, si la paleta del GIF
(`PALETTE_COLORS`) ya no basta, ajustarla.

Requisitos: `playwright` (dependencia del proyecto) y un Chromium que encuentre
(`PLAYWRIGHT_BROWSERS_PATH`, o `--navegador`). No hace falta red. Tarda unos 5 segundos para los
cuatro tableros (arrancar el navegador es casi todo) y los PNG pesan en total ~0,44 MB.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from bot.services.pachinko import BOARDS  # noqa: E402
from bot.services.pachinko_pieces import (  # noqa: E402
    MANIFEST,
    PIECES_DIR,
    SCENE_PATH,
    piece_hash,
)
from bot.services.pachinko_render import piece_problems, piece_spec  # noqa: E402


def carpeta(board_key: str) -> Path:
    """Carpeta de los PNG de un tablero."""
    return PIECES_DIR / board_key


def pintar(page, board_key: str) -> dict[str, bytes]:  # noqa: ANN001
    """Pide a la escena las piezas de un tablero y devuelve sus PNG."""
    spec = piece_spec(BOARDS[board_key])
    data_urls = page.evaluate("spec => renderPieces(spec)", spec)
    prefix = "data:image/png;base64,"
    return {name: base64.b64decode(url.removeprefix(prefix)) for name, url in data_urls.items()}


def guardar(board_key: str, pieces: dict[str, bytes]) -> int:
    """Escribe los PNG (recomprimidos sin pérdida) y `piezas.json`; devuelve los bytes."""
    folder = carpeta(board_key)
    folder.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, raw in pieces.items():
        buffer = io.BytesIO()
        Image.open(io.BytesIO(raw)).convert("RGBA").save(buffer, format="PNG", optimize=True)
        (folder / name).write_bytes(buffer.getvalue())
        sizes[name] = len(buffer.getvalue())
    spec = piece_spec(BOARDS[board_key])
    manifest = {"hash": piece_hash(spec, SCENE_PATH), "piezas": dict(sorted(sizes.items()))}
    (folder / MANIFEST).write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return sum(sizes.values())


def main() -> int:
    """Punto de entrada: pinta o comprueba las piezas."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tablero", choices=sorted(BOARDS), help="solo este tablero")
    parser.add_argument("--comprobar", action="store_true")
    parser.add_argument("--navegador", help="ruta de un Chromium concreto")
    args = parser.parse_args()
    keys = [args.tablero] if args.tablero else list(BOARDS)

    if args.comprobar:
        viejo = False
        for key in keys:
            problemas = piece_problems(BOARDS[key])
            if problemas:
                viejo = True
                print(f"{key}: VIEJO ({'; '.join(problemas)})")
            else:
                print(f"{key}: al día")
        return 1 if viejo else 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=args.navegador, args=["--disable-gpu", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 400, "height": 300}, device_scale_factor=1)
        page.on("pageerror", lambda error: print(f"error en la escena: {error}", file=sys.stderr))
        page.goto(SCENE_PATH.as_uri())
        page.evaluate("document.fonts.ready.then(() => true)")
        total = 0
        for key in keys:
            weight = guardar(key, pintar(page, key))
            total += weight
            print(f"{key}: {weight / 1000:.0f} KB en {carpeta(key)}")
        browser.close()
    print(f"Total: {total / 1000:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
