"""Lo que se ve en cada fotograma del 4 en raya y su dibujo de reserva con Pillow.

Python decide todo lo que se ve: dónde está la ficha que cae en cada
fotograma (caída con gravedad y dos botes), qué fichas brillan al ganar, el
confeti y los carteles. La escena de canvas (`assets/enraya/escena.html`,
pintada por `bot.services.enraya_scene`) solo pinta estos estados; si no hay
Node ni navegador, `EnrayaRenderer` los pinta con Pillow, más sencillos.

**Lo fijo** (`meta_state`): los dos jugadores (nombre y una línea debajo), el
título, el subtítulo, la semilla y la geometría del tablero (`GEOMETRY`), la
misma para la escena y para Pillow.

**Cada fotograma** (`board_state` y los de `move_steps`):

- `grid`: 7 columnas de 6 filas (de abajo arriba): 0 vacía, 1 roja, 2 amarilla.
- `falling`: la ficha que cae, `{"col", "y", "disc"}`, con `y` en filas
  (0 la de abajo, `ROWS` el hueco de encima del tablero), o `None`.
- `last`: la última ficha colocada `[col, fila]` (lleva un aro), o `None`.
- `turn`: a quién le toca (1 rojas, 2 amarillas, 0 nadie): se ilumina su ficha.
- `win`: `{"cells", "disc", "t"}` con las casillas de la raya y el avance de su
  brillo (0 a 1), o `None`.
- `confetti`: `{"t", "disc"}`, avance del confeti (la semilla es la de `meta`), o `None`.
- `banner`: `{"title", "sub", "kind", "a"}` (`kind`: `win`, `lose`, `draw` o
  `time`; `a`, opacidad), o `None`.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from bot.services.enraya import COLS, ROWS, Board, Disc, Move
from bot.utils.gif import local_palette_gif

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"

W, H = 720, 480
CELL = 60
#: Esquina de arriba a la izquierda de las casillas (sin el marco) y tamaño de casilla.
GEOMETRY = {"x": (W - COLS * CELL) // 2, "y": 78, "cell": CELL}
#: El último fotograma se queda quieto (el GIF no se repite).
FINAL_FRAME_MS = 60_000
FRAME_MS = 30
#: Gravedad de la caída, en filas por segundo al cuadrado.
GRAVITY = 70.0
#: Lo que conserva la ficha de su velocidad al botar.
BOUNCE = 0.28
#: Lo que se queda la ficha arriba antes de soltarla.
HOVER_MS = 120
#: Lo que «piensa» Jovani antes de soltar la suya.
THINK_MS = 360
#: Fotogramas del brillo y el confeti al ganar.
WIN_FRAMES = 26
WIN_FRAME_MS = 45


@dataclass(frozen=True, slots=True)
class Media:
    """Imágenes de una jugada: GIF, PNG del final y duración de la animación."""

    gif: bytes
    png: bytes
    seconds: float


@dataclass(frozen=True, slots=True)
class Player:
    """Lo fijo de un jugador en la mesa: su nombre y una línea debajo."""

    name: str
    tag: str = ""


def meta_state(
    red: Player, yellow: Player, *, title: str, subtitle: str, seed: int
) -> dict[str, Any]:
    """Lo fijo de la partida, tal como lo lee la escena."""
    return {
        "red": {"name": red.name, "tag": red.tag},
        "yellow": {"name": yellow.name, "tag": yellow.tag},
        "title": title,
        "subtitle": subtitle,
        "seed": seed,
        "geom": dict(GEOMETRY),
    }


# -- Estados ------------------------------------------------------------------------------


def _grid(board: Board) -> list[list[int]]:
    return [[0 if d is None else d.value for d in column] for column in board.grid]


def banner(title: str, sub: str = "", *, kind: str = "win", a: float = 1.0) -> dict[str, Any]:
    """Un cartel para el final (`kind`: `win`, `lose`, `draw` o `time`)."""
    assert kind in ("win", "lose", "draw", "time"), kind
    return {"title": title, "sub": sub, "kind": kind, "a": round(a, 3)}


def board_state(
    board: Board,
    *,
    turn: Disc | None = None,
    falling: dict[str, Any] | None = None,
    win_t: float | None = None,
    confetti_t: float | None = None,
    end: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Un fotograma: el tablero con lo que haya encima.

    Args:
        board: Las fichas ya colocadas.
        turn: A quién le toca (se ilumina); `None` si nadie.
        falling: La ficha que cae (ver la cabecera).
        win_t: Avance del brillo de la raya ganadora, si la hay.
        confetti_t: Avance del confeti.
        end: Cartel del final (`banner`).
    """
    last = board.last
    win = None
    if win_t is not None and board.winner is not None:
        win = {
            "cells": [list(cell) for cell in board.winning_cells()],
            "disc": board.winner.value,
            "t": round(win_t, 3),
        }
    confetti = None
    if confetti_t is not None and board.winner is not None:
        confetti = {"t": round(confetti_t, 3), "disc": board.winner.value}
    return {
        "grid": _grid(board),
        "falling": falling,
        "last": [last.column, last.row] if last is not None and falling is None else None,
        "turn": turn.value if turn is not None else 0,
        "win": win,
        "confetti": confetti,
        "banner": end,
    }


def _fall(start: float, target: int) -> list[float]:
    """Alturas de la ficha en cada fotograma: cae con gravedad y bota dos veces."""
    heights: list[float] = []
    dt = FRAME_MS / 1000
    distance = start - target
    fall_time = math.sqrt(2 * distance / GRAVITY)
    t = dt
    while t < fall_time:
        heights.append(start - GRAVITY * t * t / 2)
        t += dt
    heights.append(float(target))
    speed = GRAVITY * fall_time
    for _ in range(2):
        speed *= BOUNCE
        up = speed / GRAVITY
        t = dt
        while t < 2 * up:
            heights.append(target + speed * t - GRAVITY * t * t / 2)
            t += dt
        heights.append(float(target))
    return [round(h, 3) for h in heights]


def drop_steps(before: Board, move: Move) -> list[tuple[dict, int]]:
    """Fotogramas de una ficha que se suelta en `before` y acaba en `move`.

    Returns:
        `(estado, ms)` de cada fotograma: la ficha arriba un momento, la caída y
        los botes. El tablero con la ficha ya puesta no va: lo pone quien sigue.
    """
    steps: list[tuple[dict, int]] = []
    hover = {"col": move.column, "y": float(ROWS), "disc": move.disc.value}
    steps.append((board_state(before, turn=move.disc, falling=hover), HOVER_MS))
    for y in _fall(float(ROWS), move.row):
        falling = {"col": move.column, "y": y, "disc": move.disc.value}
        steps.append((board_state(before, turn=move.disc, falling=falling), FRAME_MS))
    return steps


def win_steps(board: Board, *, end: dict[str, Any]) -> list[tuple[dict, int]]:
    """Fotogramas del final con raya: brillo, confeti y el cartel apareciendo."""
    steps = []
    for frame in range(WIN_FRAMES):
        t = (frame + 1) / WIN_FRAMES
        shown = {**end, "a": round(min(1.0, max(0.0, (t - 0.25) / 0.45)), 3)}
        steps.append(
            (board_state(board, win_t=t, confetti_t=t, end=shown if shown["a"] > 0 else None),
             WIN_FRAME_MS)
        )
    return steps


def end_steps(board: Board, *, end: dict[str, Any]) -> list[tuple[dict, int]]:
    """Fotogramas del final sin raya (tablas, rendirse, dormirse): el cartel aparece."""
    steps = []
    for frame in range(8):
        a = (frame + 1) / 8
        steps.append((board_state(board, end={**end, "a": round(a, 3)}), WIN_FRAME_MS))
    return steps


def final_state(board: Board, *, end: dict[str, Any] | None, turn: Disc | None) -> dict:
    """El fotograma quieto del final de una jugada (también el PNG de la mesa)."""
    if end is None:
        return board_state(board, turn=turn)
    win_t = 1.0 if board.winner is not None else None
    return board_state(board, win_t=win_t, end={**end, "a": 1.0})


def move_steps(
    before: Board,
    moves: list[Move],
    *,
    end: dict[str, Any] | None = None,
) -> tuple[list[tuple[dict, int]], dict[str, Any]]:
    """Todos los fotogramas de una jugada: una o dos fichas (la tuya y la de Jovani).

    Entre las dos fichas hay una pausa con el tablero quieto («Jovani piensa»).
    Si la última ficha acaba la partida, siguen el brillo y el cartel `end`.

    Returns:
        Los fotogramas `(estado, ms)` y el estado final, quieto.
    """
    steps: list[tuple[dict, int]] = []
    board = before
    for index, move in enumerate(moves):
        if index:
            steps.append((board_state(board, turn=move.disc), THINK_MS))
        steps += drop_steps(board, move)
        board = Board((*board.moves, move))
    after = board
    turn = None if after.finished or end is not None else after.turn
    if end is not None and after.winner is not None:
        steps.append((board_state(after), FRAME_MS * 3))
        steps += win_steps(after, end=end)
    elif end is not None:
        steps.append((board_state(after), FRAME_MS * 3))
        steps += end_steps(after, end=end)
    return steps, final_state(after, end=end, turn=turn)


def encode(frames: list[Image.Image], durations: list[int]) -> Media:
    """GIF que se juega una vez y se queda en el último fotograma, y su PNG."""
    seconds = sum(durations) / 1000
    gif = local_palette_gif(frames, [*durations, FINAL_FRAME_MS], loop=False)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=seconds)


# -- Pillow (reserva) -----------------------------------------------------------------------

_COLORS = {
    1: ((226, 46, 58), (150, 18, 30)),
    2: ((255, 206, 46), (190, 130, 10)),
}
_BOARD = (32, 78, 206)
_HOLE = (14, 20, 46)
_BG = (16, 22, 52)


class EnrayaRenderer:
    """El tablero con Pillow: sin brillos ni confeti, para cuando no hay Node ni navegador."""

    def _font(self, path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            return ImageFont.load_default()

    @staticmethod
    def _center(column: int, y: float) -> tuple[float, float]:
        x0, y0, cell = GEOMETRY["x"], GEOMETRY["y"], GEOMETRY["cell"]
        return x0 + (column + 0.5) * cell, y0 + (ROWS - 1 - y + 0.5) * cell

    def _disc(self, draw: ImageDraw.ImageDraw, column: int, y: float, disc: int) -> None:
        cx, cy = self._center(column, y)
        r = CELL * 0.4
        light, dark = _COLORS[disc]
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=light, outline=dark, width=4)

    def frame(self, meta: dict[str, Any], state: dict[str, Any]) -> Image.Image:
        """Un fotograma a partir de su estado."""
        image = Image.new("RGB", (W, H), _BG)
        draw = ImageDraw.Draw(image)
        title = self._font(TITLE_FONT, 26)
        text = self._font(TEXT_FONT, 13)
        draw.text((W / 2, 24), meta["title"], font=title, fill=(255, 214, 90), anchor="mm")
        draw.text((W / 2, 50), meta["subtitle"], font=text, fill=(220, 226, 255), anchor="mm")
        for disc, key, x in ((1, "red", 66), (2, "yellow", W - 66)):
            light, _ = _COLORS[disc]
            if state["turn"] == disc:
                draw.ellipse((x - 34, 160 - 34, x + 34, 160 + 34), outline=(255, 255, 255), width=3)
            draw.ellipse((x - 26, 160 - 26, x + 26, 160 + 26), fill=light)
            draw.text((x, 210), meta[key]["name"][:14], font=text, fill=(255, 255, 255), anchor="mm")
            draw.text((x, 230), meta[key]["tag"][:16], font=text, fill=(170, 180, 220), anchor="mm")
        if state["falling"] is not None:
            fall = state["falling"]
            self._disc(draw, fall["col"], fall["y"], fall["disc"])
        x0, y0 = GEOMETRY["x"], GEOMETRY["y"]
        board = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(board)
        bdraw.rounded_rectangle(
            (x0 - 12, y0 - 12, x0 + COLS * CELL + 12, y0 + ROWS * CELL + 12), 18, fill=_BOARD
        )
        for column in range(COLS):
            for row in range(ROWS):
                cx, cy = self._center(column, row)
                r = CELL * 0.4
                bdraw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 0, 0, 0))
        # Lo que cae va detrás del tablero: se ve por los agujeros.
        image.paste(board, (0, 0), board)
        draw = ImageDraw.Draw(image)
        for column in range(COLS):
            for row in range(ROWS):
                cx, cy = self._center(column, row)
                r = CELL * 0.4
                value = state["grid"][column][row]
                if value:
                    self._disc(draw, column, row, value)
                elif state["falling"] is None or state["falling"]["col"] != column:
                    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_HOLE)
        for column in range(COLS):
            cx, _ = self._center(column, 0)
            draw.text((cx, y0 + ROWS * CELL + 28), str(column + 1), font=text,
                      fill=(220, 226, 255), anchor="mm")  # fmt: skip
        if state["last"] is not None:
            cx, cy = self._center(*state["last"])
            draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), outline=(255, 255, 255), width=3)
        if state["win"] is not None:
            for column, row in state["win"]["cells"]:
                cx, cy = self._center(column, row)
                r = CELL * 0.45
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255), width=5)
        if state["banner"] is not None and state["banner"]["a"] >= 0.5:
            end = state["banner"]
            draw.rounded_rectangle((W / 2 - 230, 200, W / 2 + 230, 290), 16, fill=(10, 12, 30))
            big = self._font(TITLE_FONT, 34)
            draw.text((W / 2, 236), end["title"], font=big, fill=(255, 226, 120), anchor="mm")
            draw.text((W / 2, 270), end["sub"], font=text, fill=(230, 230, 240), anchor="mm")
        return image

    def still(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        """PNG de un estado quieto."""
        png = io.BytesIO()
        self.frame(meta, state).save(png, format="PNG", compress_level=6)
        return png.getvalue()

    def animate(
        self, meta: dict[str, Any], steps: list[tuple[dict, int]], final: dict[str, Any]
    ) -> Media:
        """GIF de unos fotogramas y PNG del final."""
        frames = [self.frame(meta, state) for state, _ms in steps]
        frames.append(self.frame(meta, final))
        return encode(frames, [ms for _state, ms in steps])
