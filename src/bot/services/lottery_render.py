"""Lo que se ve en la lotería: el contenido de cada imagen y de cada fotograma.

La escena (`assets/loteria/escena.html`, pintada por `bot.services.lottery_scene`)
no decide nada: este módulo le pasa los textos ya formateados, el orden en que se
rasca o salen las bolas y el tiempo de cada fotograma. Todo es determinista: lo que
parece azar (el camino de la moneda, el bailoteo de las bolas, la serie del
décimo) sale de una semilla y del número de fotograma, así que pintar antes del
clic y pintar al pulsar dan los mismos bytes.

Cinco imágenes, cada una un `mode` de la escena:

- **rasca** (GIF y PNG final): el boleto de la ONCE con sus nueve casillas. Una
  moneda las rasca de una en una y las que deciden van al final (`scratch_order`):
  con premio, la tercera casilla igual es la última; sin premio, la pareja más
  alta se destapa pronto y su compañera en la penúltima. Las últimas casillas se
  rascan más despacio.
- **decimo** (PNG): el décimo de la Nacional con su número, el sorteo, la serie y
  la fracción, y, después del sorteo, el sello de premiado.
- **boleto** (PNG): el resguardo de la Primitiva, la Bonoloto, el Gordo o
  Euromillones con una fila de bolas por apuesta y, después del sorteo, los
  aciertos marcados.
- **sorteo** (GIF y PNG final): las bolas saliendo del bombo una a una (juegos de
  bote) o las cinco cifras del número premiado (Nacional), y el cartel del bote.
- **cartel** (PNG): el escaparate de la administración con los botes y los
  próximos sorteos.

Los tamaños son los de la imagen final; la escena pinta al doble y reduce.
"""

from __future__ import annotations

import hashlib
import io
import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PIL import Image

from bot.services.economy import format_amount
from bot.services.lottery import (
    DRAW_TIMEZONE,
    Game,
    Kind,
    Pick,
    Prize,
    classify,
    nacional_prizes,
)
from bot.utils.gif import local_palette_gif

#: Tamaño de cada imagen, `(ancho, alto)`. El boleto crece con las apuestas (`slip_size`).
SCRATCH_SIZE = (640, 400)
TICKET_SIZE = (720, 330)
SLIP_WIDTH = 640
DRAW_SIZE = (720, 405)
POSTER_SIZE = (720, 405)

#: Milisegundos por fotograma del rasca y del sorteo (múltiplos de 10: la unidad del GIF).
SCRATCH_FRAME_MS = 40
DRAW_FRAME_MS = 50
#: El último fotograma se queda quieto: el GIF no se repite (`loop=False`) y, en los
#: clientes que lo repiten igual, tarda un minuto en volver a empezar.
FINAL_FRAME_MS = 60_000

#: Fotogramas que tarda en rascarse cada casilla, en el orden en que se rascan: las
#: seis primeras deprisa y las tres que deciden, cada vez más despacio.
SCRATCH_CELL_FRAMES = (4, 4, 4, 4, 4, 4, 6, 9, 13)
#: Fotogramas con el boleto entero antes de empezar y del revelado del final.
SCRATCH_INTRO_FRAMES = 2
SCRATCH_REVEAL_FRAMES = 9

#: Juegos de bote: fotogramas de vuelo de cada bola, de pausa tras posarse, de
#: entrada (el bombo girando) y del cartel del final.
BALL_FLIGHT_FRAMES = 8
BALL_PAUSE_FRAMES = 3
DRAW_INTRO_FRAMES = 8
DRAW_BANNER_FRAMES = 10
#: Nacional: vuelo de cada cifra del primer número, de las de los siguientes (más
#: rápidas) y pausa entre números.
DIGIT_FLIGHT_FRAMES = 7
DIGIT_FAST_FRAMES = 4
NUMBER_PAUSE_FRAMES = 6
#: Números de la Nacional que salen en la animación (el resto va en el texto).
DRAWN_NUMBERS = {"navidad": 3, "nino": 3}

#: Ancho de una fila de bolas del boleto y alto de cada parte.
SLIP_HEADER = 118
SLIP_ROW = 46
SLIP_FOOTER = 58
#: Filas por columna del boleto: con más apuestas, dos columnas.
SLIP_ROWS_PER_COLUMN = 5

_DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip

#: Nombre corto de cada juego en las imágenes (sin el «Lotería Nacional del»).
TITLES = {
    "jueves": "LOTERÍA NACIONAL",
    "sabado": "LOTERÍA NACIONAL",
    "navidad": "LOTERÍA DE NAVIDAD",
    "nino": "LOTERÍA DEL NIÑO",
    "primitiva": "LA PRIMITIVA",
    "bonoloto": "BONOLOTO",
    "gordo": "EL GORDO",
    "euromillones": "EUROMILLONES",
    "x10": "RASCA X10",
    "7ymedia": "RASCA 7 Y MEDIA",
}
#: Nombre de cada sorteo de la Nacional en el cartel.
NACIONAL_NAMES = {"jueves": "Jueves", "sabado": "Sábado", "navidad": "Navidad", "nino": "El Niño"}


@dataclass(frozen=True, slots=True)
class Media:
    """Lo que el cog sube: el GIF, el PNG del último fotograma y lo que dura el GIF."""

    gif: bytes
    png: bytes
    seconds: float


def seed_of(*parts: object) -> int:
    """Semilla estable (32 bits) a partir de cualquier cosa: la misma entrada, la misma."""
    digest = hashlib.blake2b(repr(parts).encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big")


def when_text(draw_at: float, *, hour: bool = True) -> str:
    """`Jueves 29 de octubre · 21:30` en hora de Madrid."""
    moment = datetime.fromtimestamp(draw_at, DRAW_TIMEZONE)
    text = f"{_DAYS[moment.weekday()].capitalize()} {moment.day} de {_MONTHS[moment.month - 1]}"
    return f"{text} · {moment:%H:%M}" if hour else text


def _seconds(durations: Sequence[int]) -> float:
    """Lo que dura el GIF hasta el último fotograma (el quieto no cuenta)."""
    return sum(durations[:-1]) / 1000


def encode(frames: list[Image.Image], durations: Sequence[int]) -> Media:
    """GIF sin repetición (el último fotograma, quieto) y el PNG de ese último.

    Recibe los fotogramas tal cual los da `NodeScene.render` (ya se están
    codificando mientras se pintaban): ni se copian ni se reordenan.
    """
    gif = local_palette_gif(frames, list(durations), loop=False)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=_seconds(durations))


def _smooth(t: float) -> float:
    """Arranca y frena suave (0 → 1)."""
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


# -- Rasca ------------------------------------------------------------------------------


def scratch_order(cells: Sequence[int], prize: int | None, rng: random.Random) -> list[int]:
    """En qué orden se rascan las nueve casillas, dejando para el final las que deciden.

    Con premio, una casilla igual sale pronto, otra a mitad y la tercera, la
    última. Sin premio, si hay una pareja, la más alta: una pronto y la otra en la
    penúltima, así que la última aún puede dar el premio y no lo da.
    """
    indices = list(range(len(cells)))
    if prize is not None:
        hits = [i for i, value in enumerate(cells) if value == prize]
        rest = [i for i in indices if i not in hits]
        rng.shuffle(hits)
        rng.shuffle(rest)
        return [rest[0], hits[0], rest[1], rest[2], hits[1], rest[3], rest[4], rest[5], hits[2]]
    pairs = [value for value, count in Counter(cells).items() if count == 2]
    if not pairs:
        rng.shuffle(indices)
        return indices
    best = max(pairs)
    pair = [i for i, value in enumerate(cells) if value == best]
    rest = [i for i in indices if i not in pair]
    rng.shuffle(pair)
    rng.shuffle(rest)
    return [rest[0], pair[0], *rest[1:6], pair[1], rest[6]]


def scratch_near_miss(cells: Sequence[int], prize: int | None, top: int) -> bool:
    """Si un rasca sin premio enseña dos casillas del premio más alto de su tabla."""
    return prize is None and Counter(cells)[top] == 2


def scratch_meta(game: Game, cells: Sequence[int], *, seed: int) -> dict[str, Any]:
    """Lo fijo de un rasca: el boleto, sus casillas y la tabla de premios gordos.

    Args:
        cells: Las nueve cantidades (`lottery.scratch_grid`), en Y$.
        seed: Del camino de la moneda y del número de serie.
    """
    assert game.scratch is not None
    prizes, _ = game.scratch
    w, h = SCRATCH_SIZE
    return {
        "mode": "rasca",
        "w": w,
        "h": h,
        "game": game.key,
        "title": TITLES[game.key],
        "price": format_amount(game.price),
        "top": [format_amount(p.amount) for p in prizes[:4]],
        "cells": [format_amount(value) for value in cells],
        "serial": f"{seed % 10_000_000:07d}-{seed // 10_000_000 % 100:02d}",
        "seed": seed,
    }


def scratch_banner(prize: Prize | None, tax: int) -> dict[str, str]:
    """El cartel del final del rasca."""
    if prize is None:
        return {"tone": "lose", "title": "SIN PREMIO", "amount": "", "note": ""}
    note = f"Gravamen: {format_amount(tax)}" if tax else ""
    return {"tone": "win", "title": "¡PREMIO!", "amount": format_amount(prize.amount), "note": note}


def scratch_states(
    cells: Sequence[int],
    order: Sequence[int],
    banner: Mapping[str, str],
    *,
    top: int | None = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Fotogramas del rasca y sus duraciones, con el revelado y el fotograma quieto.

    Cada estado dice cuánto está rascada cada casilla (`p`, de 0 a 1), cuál se
    está rascando (`active`, para la moneda), qué casillas brillan al final
    (`glow`) y con qué tono (`glowTone`: `win`, las tres iguales; `near`, las dos
    del premio más alto de un rasca sin premio) y el cartel. El último es el PNG.

    Args:
        top: Premio más alto de la tabla del rasca, para resaltar el «casi».
    """
    progress = [0.0] * len(cells)
    states: list[dict[str, Any]] = []
    counts = Counter(cells)
    glow = [i for i, value in enumerate(cells) if counts[value] >= 3]
    tone = "win"
    if not glow and top is not None and counts[top] == 2:
        glow = [i for i, value in enumerate(cells) if value == top]
        tone = "near"

    def state(active: int, shine: float = 0.0, banner_a: float = 0.0) -> dict[str, Any]:
        return {
            "p": [round(p, 4) for p in progress],
            "active": active,
            "glow": glow if shine > 0 else [],
            "glowA": round(shine, 3),
            "glowTone": tone,
            "banner": dict(banner) if banner_a > 0 else None,
            "bannerA": round(banner_a, 3),
        }

    for _ in range(SCRATCH_INTRO_FRAMES):
        states.append(state(-1))
    for cell, frames in zip(order, SCRATCH_CELL_FRAMES, strict=True):
        for k in range(1, frames + 1):
            progress[cell] = _smooth(k / frames)
            states.append(state(cell))
    for k in range(1, SCRATCH_REVEAL_FRAMES + 1):
        # El brillo late dos veces y se queda; el cartel entra a la vez.
        pulse = 0.65 + 0.35 * math.cos(k / SCRATCH_REVEAL_FRAMES * 4 * math.pi)
        states.append(state(-1, shine=pulse, banner_a=min(1.0, k / (SCRATCH_REVEAL_FRAMES - 3))))
    durations = [SCRATCH_FRAME_MS] * (len(states) - 1) + [FINAL_FRAME_MS]
    return states, durations


# -- Décimo y boleto ---------------------------------------------------------------------


def ticket_series(number: int, draw_at: float, ticket_id: int) -> tuple[int, int]:
    """Serie y fracción de un décimo, inventadas pero estables para el mismo boleto."""
    seed = seed_of("decimo", number, int(draw_at), ticket_id)
    return 1 + seed % 120, 1 + seed // 120 % 10


def prize_stamp(won: Sequence[tuple[int, str]]) -> dict[str, str]:
    """Sello de un décimo o un boleto ya sorteado: lo cobrado o «sin premio»."""
    if not won:
        return {"tone": "lose", "title": "SIN PREMIO", "amount": "", "detail": ""}
    best = max(won)[1]
    more = f" y {len(won) - 1} más" if len(won) > 1 else ""
    return {
        "tone": "win",
        "title": "PREMIADO",
        "amount": format_amount(sum(amount for amount, _ in won)),
        "detail": best + more,
    }


def ticket_state(
    game: Game,
    number: int,
    *,
    draw_at: float,
    quantity: int,
    ticket_id: int,
    owner: str,
    result: Mapping | None = None,
) -> dict[str, Any]:
    """El décimo (o el billete, con `quantity` 10) de la Nacional, sorteado o no."""
    program = game.program
    assert program is not None
    serie, fraction = ticket_series(number, draw_at, ticket_id)
    w, h = TICKET_SIZE
    stamp = None
    if result is not None:
        won = nacional_prizes(game, number, result)
        stamp = prize_stamp([(amount * quantity, label) for amount, label in won])
    return {
        "mode": "decimo",
        "w": w,
        "h": h,
        "game": game.key,
        "title": TITLES[game.key],
        "draw": f"Sorteo del {when_text(draw_at, hour=False).lower()}",
        "number": f"{number:05d}",
        "price": f"{format_amount(game.price)} el décimo",
        "first": f"{program.main[0].label}: {format_amount(program.main[0].amount)} al décimo",
        "serie": f"Serie {serie:03d} · Fracción {fraction}",
        "quantity": quantity,
        "owner": owner,
        "barcode": seed_of("barra", number, int(draw_at), ticket_id),
        "stamp": stamp,
    }


def slip_size(rows: int) -> tuple[int, int]:
    """Tamaño del boleto con `rows` apuestas (dos columnas a partir de seis)."""
    per_column = max(1, min(rows, SLIP_ROWS_PER_COLUMN))
    return SLIP_WIDTH, SLIP_HEADER + per_column * SLIP_ROW + SLIP_FOOTER


def _extra_label(game: Game) -> str:
    return {Kind.LOTTO: "R", Kind.GORDO: "C", Kind.EURO: "★"}[game.kind]


def _row(game: Game, pick: Pick, result: Mapping | None, prizes: Mapping[str, int]) -> dict:
    """Una apuesta del boleto: sus bolas y, si ya hay sorteo, lo que ha acertado."""
    row: dict[str, Any] = {
        "n": [f"{n:02d}" for n in pick.numbers],
        "x": [f"{e:02d}" if game.kind is Kind.EURO else str(e) for e in pick.extra],
        "hit": None,
        "xhit": None,
        "tag": "",
    }
    if result is None:
        return row
    drawn = set(result["numbers"])
    comp = result.get("comp")
    row["hit"] = ["hit" if n in drawn else "comp" if n == comp else "" for n in pick.numbers]
    if game.kind is Kind.EURO:
        row["xhit"] = [e in result["stars"] for e in pick.extra]
    else:
        own = result["reintegro"] if game.kind is Kind.LOTTO else result["clave"]
        row["xhit"] = [pick.extra[0] == own]
    key, reintegro = classify(game, pick, result)
    parts = []
    if key is not None:
        cat = game.category(key)
        amount = "BOTE" if cat.jackpot else format_amount(prizes.get(key, 0))
        parts.append(f"{cat.rule} · {amount}")
    if reintegro and game.reintegro:
        parts.append("reintegro")
    row["tag"] = " + ".join(parts)
    return row


def slip_state(
    game: Game,
    picks: Sequence[Pick],
    *,
    draw_at: float,
    owner: str,
    jackpot: int,
    prizes: Mapping[str, int],
    result: Mapping | None = None,
) -> dict[str, Any]:
    """El resguardo de unas apuestas de un juego de bote, sorteado o no.

    Args:
        jackpot: Bote que se juega (se enseña arriba).
        prizes: Premios fijos de las categorías (`lottery.category_prizes`), para
            decir cuánto ha ganado cada fila.
    """
    w, h = slip_size(len(picks))
    winning = None
    if result is not None:
        extra = (
            result["stars"]
            if game.kind is Kind.EURO
            else [result["reintegro"] if game.kind is Kind.LOTTO else result["clave"]]
        )
        winning = {
            "n": [f"{n:02d}" for n in result["numbers"]],
            "x": [f"{e:02d}" if game.kind is Kind.EURO else str(e) for e in extra],
            "comp": f"{result['comp']:02d}" if "comp" in result else None,
        }
    return {
        "mode": "boleto",
        "w": w,
        "h": h,
        "game": game.key,
        "title": TITLES[game.key],
        "draw": when_text(draw_at),
        "price": f"{format_amount(game.price)} la apuesta",
        "xlabel": _extra_label(game),
        "jackpot": format_amount(jackpot),
        "total": format_amount(game.price * len(picks)),
        "owner": owner,
        "rows": [_row(game, pick, result, prizes) for pick in picks],
        "result": winning,
        "code": f"{seed_of('boleto', game.key, int(draw_at), owner) % 10**10:010d}",
    }


# -- Sorteos ------------------------------------------------------------------------------


def draw_banner(
    *, won: int, jackpot: int, next_jackpot: int, next_draw_at: float, winners: int
) -> dict[str, str]:
    """El cartel del final de un sorteo de bote: ¡BOTE! o lo que pasa al siguiente.

    Args:
        won: Acertantes del bote.
        jackpot: Bote que había en juego.
        next_jackpot: Bote del próximo sorteo, si nadie lo ha acertado.
        winners: Personas del servidor con algún premio.
    """
    people = f"{winners} premiado{'s' if winners != 1 else ''} en el servidor"
    if won:
        return {
            "tone": "win",
            "title": "¡BOTE!",
            "amount": format_amount(jackpot),
            "sub": f"{won} acertante{'s' if won != 1 else ''} · {people}",
        }
    return {
        "tone": "carry",
        "title": "BOTE SIN DUEÑO",
        "amount": format_amount(next_jackpot),
        "sub": f"Para el {when_text(next_draw_at, hour=False).lower()} · {people}",
    }


def nacional_banner(*, winners: int, paid: int) -> dict[str, str]:
    """El cartel del final de un sorteo de la Nacional: cuántos del servidor cobran."""
    if not winners:
        return {"tone": "lose", "title": "SIN PREMIOS", "amount": "", "sub": "En el servidor"}
    return {
        "tone": "win",
        "title": f"{winners} PREMIADO{'S' if winners != 1 else ''}",
        "amount": format_amount(paid),
        "sub": "En el servidor",
    }


def _slots(game: Game) -> list[dict[str, str]]:
    """Huecos de la combinación ganadora: números y, detrás, lo especial del juego."""
    if game.kind is Kind.LOTTO:
        extra = [{"kind": "comp", "label": "C"}, {"kind": "r", "label": "R"}]
        return [{"kind": "main"}] * 6 + extra
    if game.kind is Kind.GORDO:
        return [{"kind": "main"}] * 5 + [{"kind": "clave", "label": "CLAVE"}]
    return [{"kind": "main"}] * 5 + [{"kind": "star", "label": "★"}] * 2


def _values(game: Game, result: Mapping, order: Sequence[int]) -> list[str]:
    """Lo que va en cada hueco, con los números en el orden `order`."""
    numbers = [f"{result['numbers'][i]:02d}" for i in order]
    if game.kind is Kind.LOTTO:
        return [*numbers, f"{result['comp']:02d}", str(result["reintegro"])]
    if game.kind is Kind.GORDO:
        return [*numbers, str(result["clave"])]
    return [*numbers, *(f"{s:02d}" for s in result["stars"])]


def pool_draw(
    game: Game, result: Mapping, *, draw_at: float, banner: Mapping[str, str]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[int]]:
    """Sorteo de un juego de bote: `(meta, fotogramas, duraciones)`.

    Las bolas salen en un orden barajado con la semilla del sorteo (en la tele
    salen desordenadas) y cada una vuela a su hueco de la combinación ordenada,
    así que la fila se va llenando a saltos y al final no cambia nada de sitio.
    Detrás de los números salen el complementario y el reintegro, la clave o las
    estrellas. El último fotograma lleva el cartel del bote.
    """
    seed = seed_of("sorteo", game.key, int(draw_at))
    rng = random.Random(seed)
    main = len(result["numbers"])
    order = list(range(main))
    rng.shuffle(order)
    slots = _slots(game)
    values = _values(game, result, list(range(main)))
    # Qué hueco se llena en cada salida: los números barajados y lo demás, en su orden.
    filling = [*order, *range(main, len(slots))]
    w, h = DRAW_SIZE
    meta = {
        "mode": "sorteo",
        "kind": game.kind.value,
        "w": w,
        "h": h,
        "game": game.key,
        "title": TITLES[game.key],
        "date": when_text(draw_at),
        "slots": slots,
        "seed": seed,
    }
    shown: list[str | None] = [None] * len(slots)
    states: list[dict[str, Any]] = []
    spin = 0.0

    def state(fly: dict | None = None, banner_a: float = 0.0, final: bool = False) -> dict:
        return {
            "spin": round(spin, 4),
            "shown": list(shown),
            "fly": fly,
            "sorted": final,
            "banner": dict(banner) if banner_a > 0 else None,
            "bannerA": round(banner_a, 3),
        }

    for _ in range(DRAW_INTRO_FRAMES):
        spin += 0.35
        states.append(state())
    for slot in filling:
        for k in range(1, BALL_FLIGHT_FRAMES + 1):
            spin += 0.35
            t = round(_smooth(k / BALL_FLIGHT_FRAMES), 4)
            states.append(state({"slot": slot, "v": values[slot], "t": t}))
        shown[slot] = values[slot]
        for _ in range(BALL_PAUSE_FRAMES):
            spin += 0.2
            states.append(state())
    for k in range(1, DRAW_BANNER_FRAMES + 1):
        spin += 0.1
        states.append(state(banner_a=min(1.0, k / (DRAW_BANNER_FRAMES - 3)), final=True))
    durations = [DRAW_FRAME_MS] * (len(states) - 1) + [FINAL_FRAME_MS]
    return meta, states, durations


def nacional_draw(
    game: Game, result: Mapping, *, draw_at: float, banner: Mapping[str, str]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[int]]:
    """Sorteo de la Nacional: las cifras de los números premiados, una a una.

    Salen el 1º y el 2º premio (y el 3º en Navidad y el Niño): cada cifra de un
    bombo, el primer número despacio y los siguientes más rápido. El último
    fotograma añade los reintegros y el cartel.
    """
    program = game.program
    assert program is not None
    labels = {p.key: (p.label, p.amount) for p in program.main}
    count = DRAWN_NUMBERS.get(game.key, 2)
    rows = []
    for key, number in result["main"][:count]:
        label, amount = labels[key]
        rows.append(
            {"label": label.upper(), "amount": format_amount(amount), "digits": f"{number:05d}"}
        )
    seed = seed_of("sorteo", game.key, int(draw_at))
    w, h = DRAW_SIZE
    meta = {
        "mode": "sorteo",
        "kind": "nacional",
        "w": w,
        "h": h,
        "game": game.key,
        "title": TITLES[game.key],
        "date": when_text(draw_at),
        "rows": [{"label": r["label"], "amount": r["amount"]} for r in rows],
        "seed": seed,
    }
    shown = [[None] * 5 for _ in rows]
    states: list[dict[str, Any]] = []
    spin = 0.0

    def state(fly: dict | None = None, banner_a: float = 0.0, final: bool = False) -> dict:
        return {
            "spin": round(spin, 4),
            "digits": [list(r) for r in shown],
            "fly": fly,
            "reintegros": [str(d) for d in result["reintegros"]] if final else None,
            "banner": dict(banner) if banner_a > 0 else None,
            "bannerA": round(banner_a, 3),
        }

    for _ in range(DRAW_INTRO_FRAMES):
        spin += 0.35
        states.append(state())
    for r, row in enumerate(rows):
        frames = DIGIT_FLIGHT_FRAMES if r == 0 else DIGIT_FAST_FRAMES
        for c, digit in enumerate(row["digits"]):
            for k in range(1, frames + 1):
                spin += 0.35
                fly = {"row": r, "col": c, "v": digit, "t": round(_smooth(k / frames), 4)}
                states.append(state(fly))
            shown[r][c] = digit
        for _ in range(NUMBER_PAUSE_FRAMES):
            spin += 0.2
            states.append(state())
    for k in range(1, DRAW_BANNER_FRAMES + 1):
        spin += 0.1
        states.append(state(banner_a=min(1.0, k / (DRAW_BANNER_FRAMES - 3)), final=True))
    durations = [DRAW_FRAME_MS] * (len(states) - 1) + [FINAL_FRAME_MS]
    return meta, states, durations


# -- Cartel -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PosterGame:
    """Un juego en el cartel de la administración.

    Attributes:
        game: El juego.
        draw_at: Su próximo sorteo.
        amount: Bote (juegos de bote) o primer premio al décimo (Nacional), en Y$.
        hot: Si el bote ya ha subido mucho (más del doble de con lo que empieza).
    """

    game: Game
    draw_at: float
    amount: int
    hot: bool = False


def poster_state(
    pools: Sequence[PosterGame], nacional: Sequence[PosterGame], *, now: float
) -> dict[str, Any]:
    """El cartel de inicio: los botes en grande y, debajo, la Nacional.

    El sorteo de Navidad o del Niño que esté más cerca sale con los días que faltan.
    """
    w, h = POSTER_SIZE
    botes = [
        {
            "game": item.game.key,
            "name": TITLES[item.game.key],
            "amount": format_amount(item.amount),
            "when": when_text(item.draw_at),
            "grow": f"+{format_amount(item.game.jackpot_daily)} al día",
            "hot": item.hot,
        }
        for item in pools
    ]
    rows = []
    for item in nacional:
        days = max(0, math.ceil((item.draw_at - now) / 86_400))
        rows.append(
            {
                "game": item.game.key,
                "name": NACIONAL_NAMES[item.game.key],
                "amount": format_amount(item.amount),
                "when": when_text(item.draw_at),
                "days": days,
            }
        )
    return {"mode": "cartel", "w": w, "h": h, "botes": botes, "nacional": rows}
