"""Muestras de la lotería: cada imagen de `assets/loteria/escena.html` en sus casos.

Sirve para revisar la escena a ojo y a las pruebas (`tests/unit/test_lottery_scene.py`),
que importan `stills()` y `animations()`. Cada muestra sale de las funciones reales
(`lottery.scratch_grid`, `draw_result`, `lottery_render.*`), no de cifras escritas a mano.

Uso (desde la raíz del repo, con PYTHONPATH=src):

    python docs/muestras_loteria.py <carpeta> [muestra ...]

Deja en `<carpeta>` un PNG por imagen quieta y, por cada animación, su GIF, su PNG final y
una hoja de fotogramas (`<muestra>_hoja.png`, uno de cada pocos, con su número). Dice lo
que tardó cada una y lo que pesa cada GIF. Sin nombres, pinta todas.
"""

from __future__ import annotations

import asyncio
import io
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from bot.services import lottery as lot
from bot.services.lottery import DRAW_TIMEZONE, GAME_BY_KEY
from bot.services.lottery_render import (
    PosterGame,
    draw_banner,
    nacional_banner,
    nacional_draw,
    pool_draw,
    poster_state,
    scratch_banner,
    scratch_meta,
    scratch_order,
    scratch_states,
    slip_state,
    ticket_state,
)
from bot.services.lottery_scene import LotteryScene

NOW = datetime(2026, 10, 25, 18, 0, tzinfo=DRAW_TIMEZONE).timestamp()
#: Nombre con caracteres que la fuente no tiene (la escena los limpia).
OWNER = "Ana 🍀 Martín"

Animation = tuple[dict[str, Any], list[dict[str, Any]], list[int]]


def _scratch(key: str, *, prize_index: int | None, near: bool = False, seed: int = 7) -> Animation:
    """Un rasca: con el premio `prize_index` de su tabla, sin premio o con el «casi»."""
    game = GAME_BY_KEY[key]
    assert game.scratch is not None
    table = game.scratch[0]
    prize = table[prize_index] if prize_index is not None else None
    rng = random.Random(seed)
    top = table[0].amount
    while True:
        cells = lot.scratch_grid(game, prize, rng)
        if not near or (prize is None and cells.count(top) == 2):
            break
    order = scratch_order(cells, prize.amount if prize else None, random.Random(seed))
    tax = lot.gravamen(prize.amount) if prize else 0
    states, durations = scratch_states(cells, order, scratch_banner(prize, tax), top=top)
    return scratch_meta(game, cells, seed=seed), states, durations


def _pool(key: str, *, won: int, seed: int = 3) -> Animation:
    game = GAME_BY_KEY[key]
    draw_at = lot.next_draw(game, NOW)
    result = lot.draw_result(game, random.Random(seed))
    banner = draw_banner(
        won=won,
        jackpot=23_400,
        next_jackpot=23_800,
        next_draw_at=lot.next_draw(game, draw_at),
        winners=3,
    )
    return pool_draw(game, result, draw_at=draw_at, banner=banner)


def _nacional(key: str, *, seed: int = 5) -> Animation:
    game = GAME_BY_KEY[key]
    draw_at = lot.next_draw(game, NOW)
    result = lot.draw_result(game, random.Random(seed))
    banner = nacional_banner(winners=2, paid=1_230)
    return nacional_draw(game, result, draw_at=draw_at, banner=banner)


def animations() -> dict[str, Animation]:
    """Las animaciones: rascas con y sin premio, con el «casi», y un sorteo de cada juego."""
    return {
        "rasca_x10_premio": _scratch("x10", prize_index=6),
        "rasca_x10_nada": _scratch("x10", prize_index=None),
        "rasca_x10_casi": _scratch("x10", prize_index=None, near=True),
        "rasca_x10_gordo": _scratch("x10", prize_index=0),
        "rasca_7m_premio": _scratch("7ymedia", prize_index=5),
        "rasca_7m_nada": _scratch("7ymedia", prize_index=None, seed=11),
        "sorteo_primitiva_bote": _pool("primitiva", won=1),
        "sorteo_bonoloto": _pool("bonoloto", won=0),
        "sorteo_gordo": _pool("gordo", won=0),
        "sorteo_euromillones": _pool("euromillones", won=2),
        "sorteo_jueves": _nacional("jueves"),
        "sorteo_navidad": _nacional("navidad"),
        "sorteo_nino": _nacional("nino"),
    }


def _slip(key: str, bets: int, *, drawn: bool) -> dict[str, Any]:
    game = GAME_BY_KEY[key]
    rng = random.Random(bets)
    picks = [lot.random_pick(game, rng) for _ in range(bets)]
    draw_at = lot.next_draw(game, NOW)
    result = None
    if drawn:
        # Una apuesta con 4 aciertos, para ver los marcados.
        result = lot.result_for(game, picks[0], "4", rng)
    return slip_state(
        game,
        picks,
        draw_at=draw_at,
        owner=OWNER,
        jackpot=17_400,
        prizes=lot.category_prizes(game, 20),
        result=result,
    )


def _ticket(key: str, *, quantity: int = 1, won: bool | None = None) -> dict[str, Any]:
    game = GAME_BY_KEY[key]
    draw_at = lot.next_draw(game, NOW)
    number = 48_288
    result = None
    if won is not None:
        result = lot.draw_result(game, random.Random(1))
        if won:
            # El número de al lado del 1º: cobra la aproximación.
            number = (result["main"][0][1] + 1) % lot.NACIONAL_NUMBERS
        else:
            number = next(
                n for n in range(lot.NACIONAL_NUMBERS) if not lot.nacional_prizes(game, n, result)
            )
    return ticket_state(
        game,
        number,
        draw_at=draw_at,
        quantity=quantity,
        ticket_id=12,
        owner=OWNER,
        result=result,
    )


def _poster() -> dict[str, Any]:
    pools = [
        PosterGame(GAME_BY_KEY[key], lot.next_draw(GAME_BY_KEY[key], NOW), amount, hot=hot)
        for key, amount, hot in (
            ("primitiva", 17_400, True),
            ("bonoloto", 2_150, False),
            ("gordo", 8_450, False),
            ("euromillones", 61_000, True),
        )
    ]
    nacional = []
    for key in ("jueves", "sabado", "navidad", "nino"):
        game = GAME_BY_KEY[key]
        assert game.program is not None
        nacional.append(PosterGame(game, lot.next_draw(game, NOW), game.program.main[0].amount))
    return poster_state(pools, nacional, now=NOW)


def stills() -> dict[str, dict[str, Any]]:
    """Las imágenes quietas: décimos, boletos y el cartel."""
    return {
        "decimo_jueves": _ticket("jueves"),
        "decimo_sabado_billete": _ticket("sabado", quantity=10),
        "decimo_navidad": _ticket("navidad"),
        "decimo_nino": _ticket("nino"),
        "decimo_premiado": _ticket("jueves", won=True),
        "decimo_sin_premio": _ticket("navidad", won=False),
        "boleto_primitiva_1": _slip("primitiva", 1, drawn=False),
        "boleto_bonoloto_5": _slip("bonoloto", 5, drawn=False),
        "boleto_gordo_10": _slip("gordo", 10, drawn=False),
        "boleto_euromillones_3": _slip("euromillones", 3, drawn=False),
        "boleto_primitiva_sorteado": _slip("primitiva", 4, drawn=True),
        "boleto_euromillones_sorteado": _slip("euromillones", 7, drawn=True),
        "cartel": _poster(),
    }


def contact_sheet(frames: list[Image.Image], every: int = 6) -> Image.Image:
    """Uno de cada `every` fotogramas (y el último), en una rejilla con su número."""
    picked = [*range(0, len(frames) - 1, every), len(frames) - 1]
    w, h = frames[0].size
    scale = 0.5
    tw, th = int(w * scale), int(h * scale)
    columns = 4
    rows = (len(picked) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tw, rows * (th + 16)), "white")
    draw = ImageDraw.Draw(sheet)
    for n, index in enumerate(picked):
        x, y = n % columns * tw, n // columns * (th + 16)
        sheet.paste(frames[index].resize((tw, th)), (x, y + 16))
        draw.text((x + 4, y + 2), f"#{index}", fill="black")
    return sheet


def _gif_frames(gif: bytes) -> list[Image.Image]:
    image = Image.open(io.BytesIO(gif))
    frames = []
    for index in range(image.n_frames):
        image.seek(index)
        frames.append(image.convert("RGB"))
    return frames


async def main(folder: Path, names: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    scene = LotteryScene()
    try:
        for name, state in stills().items():
            if names and name not in names:
                continue
            start = time.perf_counter()
            png = await scene.still(state)
            took = time.perf_counter() - start
            if png is None:
                print(f"{name}: no se pudo pintar")
                continue
            (folder / f"{name}.png").write_bytes(png)
            print(f"{name}: {took * 1000:.0f} ms, {len(png) / 1024:.0f} KB")
        for name, (meta, states, durations) in animations().items():
            if names and name not in names:
                continue
            start = time.perf_counter()
            media = await scene.animate(meta, states, durations)
            took = time.perf_counter() - start
            if media is None:
                print(f"{name}: no se pudo pintar")
                continue
            (folder / f"{name}.gif").write_bytes(media.gif)
            (folder / f"{name}.png").write_bytes(media.png)
            sheet = contact_sheet(_gif_frames(media.gif))
            sheet.save(folder / f"{name}_hoja.png")
            print(
                f"{name}: {len(states)} fotogramas, {media.seconds:.1f} s de animación, "
                f"{took * 1000:.0f} ms, GIF {len(media.gif) / 1024:.0f} KB"
            )
    finally:
        await scene.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2:]))
