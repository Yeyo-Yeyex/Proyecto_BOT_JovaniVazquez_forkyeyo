"""Piezas ya pintadas de la máquina del pachinko: nombres, medidas y carga.

El mueble, el campo de clavos, los bolsillos, la bola, los adornos y las
bombillas se pintan con canvas en un Chromium **una sola vez, fuera del bot**
(`docs/pachinko_piezas.py` con `src/bot/assets/pachinko/escena.html`) y se
guardan como PNG en `src/bot/assets/pachinko/<tablero>/`. El bot no abre
ningún navegador para el pachinko: `bot.services.pachinko_render` carga esos
PNG con Pillow y monta cada fotograma pegándolos.

Este módulo solo sabe qué archivos hay, cuánto miden y cómo leerlos. Si falta
alguno, no tiene el tamaño que toca o no se puede abrir, esa pieza no sale en
el resultado y el dibujo con Pillow la sustituye (se avisa una vez por tablero
en el log): el juego nunca se queda sin imagen.

`piezas.json`, junto a los PNG, guarda un resumen (`piece_hash`) de los datos
con que se pintaron (geometría, temas, colores y la propia escena). Con él,
`python docs/pachinko_piezas.py --comprobar` y las pruebas saben si los PNG
siguen siendo los de la geometría y los temas actuales.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path

from PIL import Image

from bot.services.pachinko_physics import HEIGHT, LCD_BOX, POCKET_H, WIDTH

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "pachinko"
SCENE_PATH = ASSETS_DIR / "escena.html"
#: Carpeta de las piezas de un tablero: `PIECES_DIR / board.key`.
PIECES_DIR = ASSETS_DIR
MANIFEST = "piezas.json"

# -- Medidas de los recortes (en píxeles de la imagen final) -------------------------

#: Lado de las celdas: la bola, el adorno, la bombilla, el punto de la reserva y la chapa
#: del contador de bolas de cada bolsillo.
BALL_CELL = 13
DECORATION_CELL = 37
BULB_CELL = 7
HOLD_CELL = 15
BADGE_CELL = 17
#: Margen que el bolsillo iluminado deja alrededor del bolsillo para su resplandor.
LIT_PAD = 8
#: Pantallas de fondo, por modo: reposo y las dos que se alternan en un reach y en un atari.
SCREEN_KEYS = ("reposo", "reach_a", "reach_b", "atari_a", "atari_b")
DECORATION_STEPS = 4

BACKGROUND_FILE = "fondo.png"
LIT_FILE = "iluminado.png"
BALL_FILE = "bola.png"
BULBS_FILE = "bombillas.png"
HOLD_FILE = "reserva.png"
BADGE_FILE = "chapa.png"


def decoration_file(step: int) -> str:
    """Nombre del archivo del paso `step` (0-3) del adorno."""
    return f"adorno_{step}.png"


def screen_file(key: str) -> str:
    """Nombre del archivo de la pantalla de fondo `key` (de `SCREEN_KEYS`)."""
    return f"pantalla_{key}.png"


def screen_size() -> tuple[int, int]:
    """Medida de las pantallas: `LCD_BOX` entera (los dos extremos se pintan)."""
    x0, y0, x1, y1 = LCD_BOX
    return x1 - x0 + 1, y1 - y0 + 1


def lit_cell_size(pocket_width: float) -> tuple[int, int]:
    """Celda de un bolsillo iluminado: el bolsillo y su resplandor, sin vecinos."""
    return math.ceil(pocket_width) + 2 * LIT_PAD, POCKET_H + 2 * LIT_PAD


def expected_sizes(
    bulb_count: int, pockets: int, pocket_width: float
) -> dict[str, tuple[int, int]]:
    """Medida que tiene que tener cada pieza, por nombre de archivo.

    Args:
        bulb_count: Colores distintos de bombilla del tablero (celdas del atlas).
        pockets: Bolsillos del tablero (celdas del atlas de los iluminados).
        pocket_width: Ancho de cada bolsillo (`Geometry.dx`).
    """
    cell_w, cell_h = lit_cell_size(pocket_width)
    sizes = {
        BACKGROUND_FILE: (WIDTH, HEIGHT),
        LIT_FILE: (cell_w * pockets, cell_h),
        BALL_FILE: (BALL_CELL, BALL_CELL),
        BULBS_FILE: (BULB_CELL * bulb_count, BULB_CELL),
        HOLD_FILE: (HOLD_CELL * 2, HOLD_CELL),
        BADGE_FILE: (BADGE_CELL, BADGE_CELL),
    }
    for step in range(DECORATION_STEPS):
        sizes[decoration_file(step)] = (DECORATION_CELL, DECORATION_CELL)
    for key in SCREEN_KEYS:
        sizes[screen_file(key)] = screen_size()
    return sizes


def piece_hash(data: object, scene: Path = SCENE_PATH) -> str:
    """Resumen de lo que hace falta para pintar las piezas de un tablero.

    Args:
        data: Los datos que se le pasan a la escena (JSON-able): geometría,
            tema y colores.
        scene: La escena HTML; si cambia, las piezas también.
    """
    digest = hashlib.sha256()
    digest.update(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
    digest.update(scene.read_bytes())
    return digest.hexdigest()[:16]


def load_pieces(
    board_key: str,
    bulb_count: int,
    pockets: int,
    pocket_width: float,
    directory: Path = PIECES_DIR,
) -> dict[str, Image.Image]:
    """Los PNG de un tablero que existen, se abren y miden lo que deben.

    Los que no valen no salen en el resultado; se avisa una vez (por tablero)
    con el nombre de los que faltan, para que quien despliegue sepa que ese
    tablero se está dibujando con Pillow.

    Args:
        board_key: Clave del tablero (nombre de su carpeta).
        bulb_count: Colores distintos de bombilla (celdas del atlas).
        pockets: Bolsillos del tablero.
        pocket_width: Ancho de cada bolsillo.
        directory: Carpeta que contiene las de cada tablero.

    Returns:
        Las imágenes en RGBA, por nombre de archivo.
    """
    folder = directory / board_key
    pieces: dict[str, Image.Image] = {}
    problems: list[str] = []
    for name, size in expected_sizes(bulb_count, pockets, pocket_width).items():
        path = folder / name
        try:
            with Image.open(path) as opened:
                image = opened.convert("RGBA")
        except (OSError, ValueError) as exc:
            problems.append(f"{name} ({type(exc).__name__})")
            continue
        if image.size != size:
            problems.append(f"{name} (mide {image.size}, tendría que medir {size})")
            continue
        pieces[name] = image
    if problems:
        _warn_once(folder, problems)
    return pieces


_warned: set[Path] = set()


def _warn_once(folder: Path, problems: list[str]) -> None:
    if folder in _warned:
        return
    _warned.add(folder)
    logger.warning(
        "Pachinko: piezas que faltan o no valen en %s, se dibujan con Pillow: %s. "
        "Se regeneran con `python docs/pachinko_piezas.py`.",
        folder,
        ", ".join(problems),
    )
