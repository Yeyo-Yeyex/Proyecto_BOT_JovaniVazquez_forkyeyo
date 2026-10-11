"""Bacará: qué se ve en la mesa, la animación de cada mano y el dibujo de reserva con Pillow.

La imagen buena la pinta Node con canvas (`bot.services.bacara_scene` y
`assets/bacara/escena.html`). Este módulo decide **qué** se ve en cada
fotograma y lo pinta también con Pillow, mucho más sencillo y solo quieto, para
cuando no hay Node ni navegador. Las dos versiones leen los mismos diccionarios
(`board_state` y `coup_states`), así que cada carta y cada bola de la carretera
están en el mismo sitio en las dos.

**La mesa** (640×420): arriba el rótulo, el zapato (de donde salen las cartas) y
el contador del zapato; a la izquierda el JUGADOR (azul) y a la derecha la
BANCA (roja), cada uno con dos huecos de carta de pie y uno tumbado debajo para
la tercera, que en los casinos se da de lado; el total de cada mano en una
ficha grande por fuera; y abajo la carretera grande con el recuento del zapato.

**La animación de una mano** (`coup_states`) solo depende de las cartas y de la
carretera, nunca de las apuestas: por eso se puede pintar antes de que nadie
apueste (el cog la pinta mientras la mesa está abierta).

1. Si es la primera del zapato, el cartel de ZAPATO NUEVO con las quemadas.
2. Reparto: cuatro cartas boca abajo vuelan desde el zapato, alternando
   Jugador y Banca.
3. Se descubren las dos del Jugador a la vez y aparece su total; luego las de
   la Banca. Con 8 o 9, cartel de NATURAL y chispas.
4. **El apretón** de cada tercera carta: llega tumbada y boca abajo, se levanta
   un poco y se va despegando por su lado largo a tirones, con pausas largas
   (`peel`, de 0 a 1). Mientras se despega solo se ven los palos, sin los
   índices de las esquinas, como cuando se aprieta una carta de verdad: con
   media carta despegada ya se adivina si es un 6 o un 9. Al final se da la
   vuelta de golpe y el total cambia.
5. Resultado: se ilumina el lado ganador, el perdedor se apaga, sale el cartel
   y la bola nueva cae en la carretera.

Las pausas se hacen con fotogramas más largos (`ms` de cada estado), no con
más fotogramas: unos 70-100 fotogramas y 4-7 s de GIF.
"""

from __future__ import annotations

import io
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from bot.services.bacara import (
    ROAD_ROWS,
    Coup,
    Result,
    RoadCell,
    Side,
    big_road,
    hand_points,
    road_stats,
)
from bot.services.blackjack import Card
from bot.services.cards_render import CardRenderer
from bot.utils.gif import local_palette_gif

W, H = 640, 420
#: Se dibuja al doble y se reduce (bordes suaves), como el resto de escenas.
S = 2
FRAME_MS = 40
#: El último fotograma se queda quieto un minuto (el cog pone el PNG después).
FINAL_FRAME_MS = 60_000

CARD_W, CARD_H = 80, 112
#: De dónde salen las cartas: la boca del zapato, arriba en el centro.
SHOE_X, SHOE_Y = 320, 44
#: Centro de cada hueco: dos de pie y la tercera tumbada debajo.
PLAYER_SLOTS = ((125.0, 122.0), (215.0, 122.0), (170.0, 226.0))
BANKER_SLOTS = ((425.0, 122.0), (515.0, 122.0), (470.0, 226.0))
#: Fichas con el total de cada mano, por fuera de las cartas.
PLAYER_BADGE = (42.0, 122.0)
BANKER_BADGE = (598.0, 122.0)
#: La carretera: origen, lado de cada casilla y columnas que caben.
ROAD_X, ROAD_Y, ROAD_CELL = 14, 284, 20
ROAD_COLS = 22
#: Panel del recuento, a la derecha de la carretera.
PANEL_X = 466

#: Orden del reparto: Jugador, Banca, Jugador, Banca, tercera del Jugador, tercera de la Banca.
DEAL_ORDER = ((Side.PLAYER, 0), (Side.BANKER, 0), (Side.PLAYER, 1), (Side.BANKER, 1))
DEAL_FRAMES = 7
DEAL_STAGGER = 3
FLIP_FRAMES = 7
POP_FRAMES = 4
#: El apretón: (hasta dónde se despega, fotogramas para llegar, pausa después en ms).
SQUEEZE = ((0.3, 3, 320), (0.64, 3, 420))
RESULT_FRAMES = 12

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"


@dataclass(frozen=True, slots=True)
class Media:
    """Lo que el cog sube: el GIF de la mano, el PNG final y cuánto dura el GIF.

    Attributes:
        gif: El GIF; `None` en el plan B de Pillow (solo imagen fija).
        png: El último fotograma.
        seconds: Lo que dura el GIF hasta el último fotograma (0 sin GIF).
    """

    gif: bytes | None
    png: bytes
    seconds: float


@dataclass(frozen=True, slots=True)
class Header:
    """Lo que dice el rótulo de arriba: zapato, mano y cartas que quedan."""

    shoe: int = 1
    hand: int = 0
    left: int = 0


# -- Estados: lo que lee la escena ---------------------------------------------------------------


def meta_state() -> dict[str, Any]:
    """Lo fijo de la escena (nada por ahora: la mesa es siempre la misma)."""
    return {"rows": ROAD_ROWS, "cols": ROAD_COLS}


def _card(card: Card) -> dict[str, Any]:
    return {"rank": card.rank, "suit": card.suit, "label": card.label, "red": card.is_red}


def slot_xy(side: Side, index: int) -> tuple[float, float]:
    """Centro del hueco `index` (0-2) de un lado."""
    return (PLAYER_SLOTS if side is Side.PLAYER else BANKER_SLOTS)[index]


def _card_state(
    card: Card,
    side: Side,
    index: int,
    *,
    x: float | None = None,
    y: float | None = None,
    rot: float | None = None,
    scale: float = 1.0,
    flip: float = 1.0,
    peel: float | None = None,
    lift: float = 0.0,
    dx: float = 0.0,
    glow: float = 0.0,
    dim: float = 0.0,
) -> dict[str, Any]:
    """Una carta sobre la mesa.

    Args:
        flip: 0 dorso, 1 cara (el giro se ve con la escala horizontal).
        peel: Apretón: qué parte de la carta se ha despegado (0-1) por su lado
            largo; `None` si no se está apretando. Mientras se aprieta se ve el
            dorso y, en la parte despegada, la cara sin índices.
        lift: Cuánto se levanta (sombra más lejos, algo más grande).
        glow: Brillo del lado ganador (0-1).
        dim: Cuánto se apaga (lado perdedor, 0-1).
    """
    sx, sy = slot_xy(side, index)
    return {
        "card": _card(card),
        "side": side.key,
        "x": round(sx if x is None else x, 2) + round(dx, 2),
        "y": round(sy if y is None else y, 2),
        "rot": round((math.pi / 2 if index == 2 else 0.0) if rot is None else rot, 4),
        "scale": round(scale, 3),
        "flip": round(flip, 3),
        "peel": None if peel is None else round(peel, 3),
        "lift": round(lift, 2),
        "glow": round(glow, 3),
        "dim": round(dim, 3),
    }


def _badge(value: int | None, pop: float = 1.0, *, win: bool = False) -> dict[str, Any]:
    """Ficha con el total de una mano (`None`: aún sin cartas a la vista)."""
    return {"value": value, "pop": round(pop, 3), "win": win}


def _road(results: Sequence[Result], *, fresh: float | None = None) -> dict[str, Any]:
    """La carretera grande y el recuento del zapato.

    Args:
        fresh: Escala de la última casilla (0-1, con rebote) si acaba de caer;
            `None` si ya estaba.
    """
    cells = big_road(results)
    last_col = max((c.col for c in cells), default=0)
    offset = max(0, last_col - (ROAD_COLS - 1))
    stats = road_stats(results)
    newest = len(results) - 1
    return {
        "cells": [_cell(c, offset) for c in cells if c.col >= offset],
        "fresh": None if fresh is None else round(fresh, 3),
        "newest": newest,
        "stats": {
            "banker": stats.banker,
            "player": stats.player,
            "tie": stats.tie,
            "naturals": stats.naturals,
            "streak": stats.streak,
            "streakSide": None if stats.streak_side is None else stats.streak_side.letter,
        },
    }


def _cell(cell: RoadCell, offset: int) -> list[Any]:
    """`[col, fila, lado (B/J/E), empates, natural, índice]` en columnas visibles."""
    return [cell.col - offset, cell.row, cell.side.letter, cell.ties, cell.natural, cell.index]


def _state(
    cards: list[dict[str, Any]],
    *,
    road: dict[str, Any],
    header: Header,
    player: dict[str, Any] | None = None,
    banker: dict[str, Any] | None = None,
    win: str | None = None,
    win_glow: float = 0.0,
    banner: dict[str, Any] | None = None,
    sparks: Sequence[Sequence[float]] = (),
    ms: int = FRAME_MS,
    full: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "cards": cards,
        "player": player or _badge(None),
        "banker": banker or _badge(None),
        "win": win,
        "winGlow": round(win_glow, 3),
        "banner": banner,
        "road": road,
        "header": {"shoe": header.shoe, "hand": header.hand, "left": header.left},
        "sparks": [list(s) for s in sparks],
        "ms": ms,
    }
    if full:
        state["full"] = True
    return state


def _banner(kind: str, title: str, sub: str = "", a: float = 1.0) -> dict[str, Any]:
    """Cartel en el centro: `kind` es `jugador`, `banca`, `empate`, `natural` o `shoe`."""
    return {"kind": kind, "title": title, "sub": sub, "a": round(a, 3)}


def _smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def _back(u: float) -> float:
    """Entrada con un pequeño rebote (0 → 1 pasando por ~1,1)."""
    u = min(1.0, max(0.0, u))
    c = 1.9
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def _burst(
    x: float, y: float, t: float, seed: int, *, count: int, size: float = 7.0, spread: float = 80
) -> list[list[float]]:
    """Chispas `[x, y, radio, alfa, clase]` que salen de (x, y) y caen; `t` va de 0 a 1.

    Clase: 0 dorada, 1 verde, 2 blanca, 3 roja, 4 azul.
    """
    rng = random.Random(seed)
    sparks = []
    for _ in range(count):
        angle = rng.uniform(0, math.tau)
        speed = rng.uniform(0.4, 1.0) * spread
        kind = rng.choice((0, 0, 2))
        px = x + math.cos(angle) * speed * t
        py = y + math.sin(angle) * speed * t + 40 * t * t
        radius = size * rng.uniform(0.6, 1.2) * (1 - 0.35 * t)
        alpha = max(0.0, 1 - t**2)
        sparks.append([round(px, 1), round(py, 1), round(radius, 1), round(alpha, 2), kind])
    return sparks


WIN_TITLES = {Side.PLAYER: "GANA EL JUGADOR", Side.BANKER: "GANA LA BANCA", Side.TIE: "EMPATE"}


def result_banner(coup: Coup, a: float = 1.0) -> dict[str, Any]:
    """Cartel del resultado: quién gana y el tanteo (`7 a 5`, con el ganador primero)."""
    winner = coup.winner
    p, b = coup.player_total, coup.banker_total
    score = f"{b} a {p}" if winner is Side.BANKER else f"{p} a {b}"
    return _banner(winner.key, WIN_TITLES[winner], score, a)


def board_state(
    results: Sequence[Result],
    header: Header,
    coup: Coup | None = None,
) -> dict[str, Any]:
    """La mesa quieta: vacía o con la última mano repartida y su resultado.

    Es lo que se enseña mientras se apuesta (`coup` es la mano anterior) y el
    último fotograma de cada GIF.
    """
    if coup is None:
        return _state([], road=_road(results), header=header, ms=FINAL_FRAME_MS)
    return _final_state(coup, results, header)


def _final_state(coup: Coup, results: Sequence[Result], header: Header) -> dict[str, Any]:
    winner = coup.winner
    cards = _all_cards(coup, winner=winner, glow=1.0, dim=1.0)
    return _state(
        cards,
        road=_road(results),
        header=header,
        player=_badge(coup.player_total, win=winner in (Side.PLAYER, Side.TIE)),
        banker=_badge(coup.banker_total, win=winner in (Side.BANKER, Side.TIE)),
        win=winner.key,
        win_glow=1.0,
        banner=result_banner(coup),
        ms=FINAL_FRAME_MS,
    )


def _all_cards(
    coup: Coup, *, winner: Side | None = None, glow: float = 0.0, dim: float = 0.0
) -> list[dict[str, Any]]:
    """Todas las cartas de la mano, de cara y en su hueco."""
    out = []
    for side, cards in ((Side.PLAYER, coup.player), (Side.BANKER, coup.banker)):
        won = winner is side or winner is Side.TIE
        lost = winner is not None and not won
        for i, card in enumerate(cards):
            out.append(
                _card_state(
                    card, side, i, glow=glow if won else 0.0, dim=dim * 0.55 if lost else 0.0
                )
            )
    return out


def coup_states(
    coup: Coup,
    before: Sequence[Result],
    header: Header,
    *,
    burned: int = 0,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """La animación entera de una mano, fotograma a fotograma.

    Args:
        coup: La mano.
        before: Resultados del zapato antes de esta mano (la carretera de partida).
        header: El rótulo con esta mano ya contada.
        burned: Cartas quemadas al abrir el zapato, si es su primera mano (cartel).
        seed: Semilla de las chispas y el temblor (misma semilla, mismo GIF).
    """
    rng = random.Random(seed)
    road_before = _road(before)
    after = [*before, Result.of(coup)]
    states: list[dict[str, Any]] = []
    # Estado de cada carta durante la animación: `None` mientras no ha salido.
    order = [(Side.PLAYER, 0), (Side.BANKER, 0), (Side.PLAYER, 1), (Side.BANKER, 1)]
    if len(coup.player) == 3:
        order.append((Side.PLAYER, 2))
    if len(coup.banker) == 3:
        order.append((Side.BANKER, 2))
    hands = {Side.PLAYER: coup.player, Side.BANKER: coup.banker}
    live: dict[tuple[Side, int], dict[str, Any]] = {}
    shown = {Side.PLAYER: 0, Side.BANKER: 0}  # cartas de cara contadas en el total
    pops = {Side.PLAYER: 1.0, Side.BANKER: 1.0}

    def badge(side: Side) -> dict[str, Any]:
        n = shown[side]
        return _badge(hand_points(hands[side][:n]) if n else None, pops[side])

    def frame(
        *,
        ms: int = FRAME_MS,
        banner: dict[str, Any] | None = None,
        sparks: Sequence[Sequence[float]] = (),
        full: bool = False,
        road: dict[str, Any] | None = None,
        win: str | None = None,
        win_glow: float = 0.0,
    ) -> None:
        cards = [live[k] for k in order if k in live]
        # La que se está apretando o levantando va encima de las demás.
        cards.sort(key=lambda c: c["lift"])
        states.append(
            _state(
                [dict(c) for c in cards],
                road=road or road_before,
                header=header,
                player=badge(Side.PLAYER),
                banker=badge(Side.BANKER),
                win=win,
                win_glow=win_glow,
                banner=banner,
                sparks=sparks,
                ms=ms,
                full=full,
            )
        )

    # 1. Mesa limpia (y el cartel del zapato nuevo, si toca).
    if coup.number == 1:
        sub = f"8 BARAJAS · {burned} QUEMADAS" if burned else "8 BARAJAS"
        for k in range(6):
            frame(banner=_banner("shoe", "ZAPATO NUEVO", sub, _back(k / 5)), full=k == 0)
        frame(banner=_banner("shoe", "ZAPATO NUEVO", sub), ms=700)
        for k in range(4):
            frame(banner=_banner("shoe", "ZAPATO NUEVO", sub, 1 - (k + 1) / 4))
    else:
        frame(ms=200, full=True)

    # 2. Reparto de las cuatro primeras, boca abajo, con las salidas solapadas.
    total = DEAL_STAGGER * 3 + DEAL_FRAMES
    for f in range(1, total + 1):
        for n, key in enumerate(order[:4]):
            u = (f - n * DEAL_STAGGER) / DEAL_FRAMES
            if u <= 0:
                continue
            side, index = key
            live[key] = _flying(hands[side][index], side, index, u, rng)
        frame()

    # 3. Se descubren las dos del Jugador y luego las dos de la Banca.
    for side in (Side.PLAYER, Side.BANKER):
        for f in range(1, FLIP_FRAMES + 1):
            u = _smooth(f / FLIP_FRAMES)
            for index in (0, 1):
                live[(side, index)] = _card_state(
                    hands[side][index], side, index, flip=u, lift=10 * math.sin(math.pi * u)
                )
            frame()
        shown[side] = 2
        for f in range(1, POP_FRAMES + 1):
            pops[side] = _back(f / POP_FRAMES)
            frame()
        pops[side] = 1.0

    # Natural: cartel y chispas sobre quien lo tiene.
    if coup.natural:
        side = Side.PLAYER if coup.natural_of(Side.PLAYER) else Side.BANKER
        points = coup.natural_of(side) or 0
        if coup.natural_of(Side.PLAYER) and coup.natural_of(Side.BANKER):
            side = Side.PLAYER if coup.player_total >= coup.banker_total else Side.BANKER
            points = max(coup.player_total, coup.banker_total)
        bx, by = PLAYER_BADGE if side is Side.PLAYER else BANKER_BADGE
        title = f"¡NATURAL {points}!"
        for f in range(8):
            frame(
                banner=_banner("natural", title, side.label.upper(), _back(f / 6)),
                sparks=_burst(bx, by, f / 8, seed + 1, count=14),
                ms=50,
            )
        frame(banner=_banner("natural", title, side.label.upper()), ms=420)

    # 4. Terceras cartas: llegan tumbadas y se aprietan.
    for side in (Side.PLAYER, Side.BANKER):
        key = (side, 2)
        if key not in order:
            continue
        card = hands[side][2]
        for f in range(1, DEAL_FRAMES + 1):
            live[key] = _flying(card, side, 2, f / DEAL_FRAMES, rng)
            frame()
        _squeeze(live, key, card, side, frame, rng)
        shown[side] = 3
        for f in range(1, POP_FRAMES + 1):
            pops[side] = _back(f / POP_FRAMES)
            frame()
        pops[side] = 1.0

    # 5. Resultado: brillo del ganador, el perdedor se apaga y cae la bola.
    winner = coup.winner
    for f in range(1, RESULT_FRAMES + 1):
        u = _smooth(f / RESULT_FRAMES)
        for side in (Side.PLAYER, Side.BANKER):
            won = winner is side or winner is Side.TIE
            for index, card in enumerate(hands[side]):
                live[(side, index)] = _card_state(
                    card, side, index, glow=u if won else 0.0, dim=0.0 if won else 0.55 * u
                )
        if winner is Side.TIE:
            sx, sy = W / 2, 150.0
        else:
            sx, sy = PLAYER_BADGE if winner is Side.PLAYER else BANKER_BADGE
        frame(
            banner=result_banner(coup, _back(min(1.0, f / 7))),
            sparks=_burst(sx, sy, f / RESULT_FRAMES, seed + 2, count=18, spread=110),
            road=_road(after, fresh=_back(max(0.0, (f - 4) / (RESULT_FRAMES - 4)))),
            win=winner.key,
            win_glow=u,
        )
    final = _final_state(coup, after, header)
    states.append(final)
    return states


def _flying(card: Card, side: Side, index: int, u: float, rng: random.Random) -> dict[str, Any]:
    """Una carta boca abajo de camino a su hueco desde el zapato (`u` de 0 a 1)."""
    tx, ty = slot_xy(side, index)
    e = 1 - (1 - min(1.0, u)) ** 3
    end_rot = math.pi / 2 if index == 2 else 0.0
    # Sale del zapato girada y pequeña, y se asienta con una vuelta de más.
    start_rot = end_rot + (-0.9 if side is Side.PLAYER else 0.9)
    return _card_state(
        card,
        side,
        index,
        x=SHOE_X + (tx - SHOE_X) * e,
        y=SHOE_Y + (ty - SHOE_Y) * e - 24 * math.sin(math.pi * e),
        rot=start_rot + (end_rot - start_rot) * e,
        scale=0.55 + 0.45 * e,
        flip=0.0,
        lift=18 * (1 - e),
    )


def _squeeze(
    live: dict[tuple[Side, int], dict[str, Any]],
    key: tuple[Side, int],
    card: Card,
    side: Side,
    frame: Any,
    rng: random.Random,
) -> None:
    """El apretón de una tercera carta: se levanta, se despega a tirones y se da la vuelta."""
    for f in range(1, 4):
        live[key] = _card_state(card, side, 2, flip=0.0, peel=0.0, lift=6 * f)
        frame()
    peel = 0.0
    for target, frames, pause in SQUEEZE:
        start = peel
        for f in range(1, frames + 1):
            peel = start + (target - start) * _smooth(f / frames)
            shake = rng.uniform(-1.2, 1.2)
            live[key] = _card_state(card, side, 2, flip=0.0, peel=peel, lift=18, dx=shake)
            frame(ms=60)
        live[key] = _card_state(card, side, 2, flip=0.0, peel=peel, lift=18)
        frame(ms=pause)
    # Se da la vuelta de golpe y cae en su sitio.
    for f in range(1, 5):
        u = f / 4
        live[key] = _card_state(card, side, 2, flip=0.5 + 0.5 * u, lift=18 * (1 - u))
        frame()


def durations(states: Sequence[dict[str, Any]]) -> list[int]:
    """Milisegundos de cada fotograma; el último se queda quieto un minuto."""
    return [int(s["ms"]) for s in states[:-1]] + [FINAL_FRAME_MS]


def encode(frames: Sequence[Image.Image], frame_ms: Sequence[int]) -> Media:
    """GIF (con el último fotograma quieto) y PNG del último fotograma."""
    gif = local_palette_gif(list(frames), list(frame_ms))
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=sum(frame_ms[:-1]) / 1000)


# -- Pillow: el dibujo de reserva ------------------------------------------------------------------

FELT = (16, 92, 58)
PLAYER_RGB = (52, 120, 230)
BANKER_RGB = (214, 52, 60)
TIE_RGB = (40, 176, 96)
GOLD = (255, 214, 92)
SIDE_RGB = {"J": PLAYER_RGB, "B": BANKER_RGB, "E": TIE_RGB}


@cache
def _font(size: int, *, title: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TITLE_FONT if title else TEXT_FONT), size * S)


@cache
def _cards() -> CardRenderer:
    return CardRenderer()


@cache
def _background() -> Image.Image:
    """Tapete, zonas de Jugador y Banca y el marco de la carretera."""
    img = Image.new("RGB", (W * S, H * S), FELT)
    d = ImageDraw.Draw(img)
    for r in range(0, 160, 8):
        shade = int(36 * (1 - r / 160))
        d.rectangle(
            (r * S, r * S, (W - r) * S, (H - r) * S),
            outline=(16 - shade // 4, 92 - shade, 58 - shade // 2),
            width=8 * S,
        )
    for (cx, _), rgb, label in (
        (PLAYER_SLOTS[2], PLAYER_RGB, "JUGADOR"),
        (BANKER_SLOTS[2], BANKER_RGB, "BANCA"),
    ):
        d.rounded_rectangle(
            ((cx - 140) * S, 40 * S, (cx + 140) * S, 272 * S),
            16 * S,
            outline=rgb,
            width=2 * S,
        )
        _centered(d, (cx, 52), label, _font(11), rgb)
    d.rounded_rectangle(
        (
            (ROAD_X - 4) * S,
            (ROAD_Y - 4) * S,
            (ROAD_X + ROAD_COLS * ROAD_CELL + 4) * S,
            (ROAD_Y + ROAD_ROWS * ROAD_CELL + 4) * S,
        ),
        6 * S,
        fill=(244, 240, 228),
    )
    for col in range(ROAD_COLS + 1):
        x = (ROAD_X + col * ROAD_CELL) * S
        d.line((x, ROAD_Y * S, x, (ROAD_Y + ROAD_ROWS * ROAD_CELL) * S), fill=(200, 196, 186))
    for row in range(ROAD_ROWS + 1):
        y = (ROAD_Y + row * ROAD_CELL) * S
        d.line((ROAD_X * S, y, (ROAD_X + ROAD_COLS * ROAD_CELL) * S, y), fill=(200, 196, 186))
    return img


def _centered(
    d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: Any, fill: Any
) -> None:
    d.text((xy[0] * S, xy[1] * S), text, font=font, fill=fill, anchor="mm")


def _paint_card(img: Image.Image, state: dict[str, Any]) -> None:
    info = state["card"]
    faceup = state["flip"] > 0.5 and state["peel"] is None
    card = Card(info["rank"], info["suit"]) if faceup else None
    sprite = _cards().card_sprite(card).resize((CARD_W * S, CARD_H * S), Image.Resampling.LANCZOS)
    if abs(state["rot"]) > 0.01:
        sprite = sprite.rotate(math.degrees(-state["rot"]), expand=True)
    if state["dim"] > 0:
        rgb = ImageEnhance.Brightness(sprite.convert("RGB")).enhance(1 - 0.6 * state["dim"])
        sprite = Image.merge("RGBA", (*rgb.split(), sprite.getchannel("A")))
    x = int(state["x"] * S - sprite.width / 2)
    y = int((state["y"] - state["lift"]) * S - sprite.height / 2)
    if state["glow"] > 0:
        ImageDraw.Draw(img).rounded_rectangle(
            (x - 3 * S, y - 3 * S, x + sprite.width + 3 * S, y + sprite.height + 3 * S),
            10 * S,
            outline=GOLD,
            width=3 * S,
        )
    img.paste(sprite, (x, y), sprite)


def _paint_badge(
    d: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    badge: dict[str, Any],
    rgb: tuple[int, int, int],
) -> None:
    if badge["value"] is None:
        return
    x, y = xy
    r = 24
    d.ellipse(
        ((x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S),
        fill=rgb,
        outline=GOLD if badge["win"] else (255, 255, 255),
        width=3 * S,
    )
    _centered(d, (x, y + 1), str(badge["value"]), _font(26, title=True), (255, 255, 255))


def _paint_road(d: ImageDraw.ImageDraw, road: dict[str, Any]) -> None:
    for col, row, letter, ties, natural, _index in road["cells"]:
        cx = ROAD_X + col * ROAD_CELL + ROAD_CELL / 2
        cy = ROAD_Y + row * ROAD_CELL + ROAD_CELL / 2
        r = ROAD_CELL / 2 - 3
        d.ellipse(
            ((cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S),
            outline=SIDE_RGB[letter],
            width=3 * S,
        )
        if natural:
            d.ellipse(((cx - 2) * S, (cy - 2) * S, (cx + 2) * S, (cy + 2) * S), fill=GOLD)
        if ties:
            d.line(
                ((cx - r) * S, (cy + r) * S, (cx + r) * S, (cy - r) * S), fill=TIE_RGB, width=2 * S
            )
    stats = road["stats"]
    y = ROAD_Y + 6
    for letter, label, value in (
        ("B", "BANCA", stats["banker"]),
        ("J", "JUGADOR", stats["player"]),
        ("E", "EMPATE", stats["tie"]),
    ):
        d.ellipse(
            ((PANEL_X + 4) * S, y * S, (PANEL_X + 20) * S, (y + 16) * S), fill=SIDE_RGB[letter]
        )
        d.text(
            ((PANEL_X + 28) * S, (y + 8) * S),
            f"{label}  {value}",
            font=_font(12),
            fill=(255, 255, 255),
            anchor="lm",
        )
        y += 26
    if stats["streak"] >= 2 and stats["streakSide"]:
        d.text(
            ((PANEL_X + 4) * S, (y + 8) * S),
            f"RACHA {stats['streakSide']} ×{stats['streak']}",
            font=_font(12),
            fill=GOLD,
            anchor="lm",
        )


def paint(meta: dict[str, Any], state: dict[str, Any]) -> Image.Image:
    """Un fotograma con Pillow, a 640×420 (más sencillo que el de Node)."""
    img = _background().copy()
    d = ImageDraw.Draw(img)
    header = state["header"]
    _centered(d, (W / 2, 18), "BACARÁ · PUNTO Y BANCA", _font(16, title=True), GOLD)
    if header["hand"]:
        _centered(
            d,
            (W / 2, 270),
            f"ZAPATO {header['shoe']} · MANO {header['hand']} · QUEDAN {header['left']}",
            _font(9),
            (220, 230, 220),
        )
    for card in state["cards"]:
        _paint_card(img, card)
    d = ImageDraw.Draw(img)
    _paint_badge(d, PLAYER_BADGE, state["player"], PLAYER_RGB)
    _paint_badge(d, BANKER_BADGE, state["banker"], BANKER_RGB)
    _paint_road(d, state["road"])
    banner = state["banner"]
    if banner:
        rgb = {"jugador": PLAYER_RGB, "banca": BANKER_RGB, "empate": TIE_RGB}.get(
            banner["kind"], (200, 150, 30)
        )
        d.rounded_rectangle(
            ((W / 2 - 130) * S, 150 * S, (W / 2 + 130) * S, 200 * S),
            12 * S,
            fill=rgb,
            outline=(255, 255, 255),
            width=2 * S,
        )
        _centered(d, (W / 2, 168), banner["title"], _font(20, title=True), (255, 255, 255))
        if banner["sub"]:
            _centered(d, (W / 2, 189), banner["sub"], _font(11), (255, 236, 170))
    return img.convert("RGB").reduce(S)


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=6)
    return out.getvalue()


class BacaraRenderer:
    """Dibujo de reserva con Pillow: la mesa quieta y el final de cada mano, sin GIF."""

    def board(self, results: Sequence[Result], header: Header, coup: Coup | None = None) -> bytes:
        """PNG de la mesa quieta (vacía o con la última mano)."""
        return _png(paint(meta_state(), board_state(results, header, coup)))

    def coup(self, coup: Coup, before: Sequence[Result], header: Header) -> Media:
        """La mano ya resuelta, como imagen fija (sin GIF)."""
        after = [*before, Result.of(coup)]
        png = self.board(after, header, coup)
        return Media(gif=None, png=png, seconds=0.0)
