"""Póker (Casino Hold'em): qué se ve en la mesa del canal, sus animaciones y el dibujo con Pillow.

La imagen buena la pinta Node con canvas (`bot.services.poker_scene` y
`assets/poker/escena.html`). Este módulo decide **qué** se ve en cada
fotograma y lo pinta también con Pillow, más sencillo y solo quieto, para
cuando no hay Node ni navegador. Las dos versiones leen los mismos diccionarios
(`board_state`, `deal_states` y `showdown_states`).

**La mesa** (820×500): arriba la banca con sus dos cartas y el mazo a la
derecha; en el centro las cinco comunitarias y, entre medias, el aviso de que
la banca juega con pareja de cuatros; a la izquierda la tabla del ante y a la
derecha la del 💎 Bote, con lo que lleva el bote común junto al título. Abajo,
en fila, un asiento por jugador (hasta `MAX_SEATS`, centrados según cuántos
haya): su nombre, sus dos cartas, una pastilla con lo que lleva, la ficha del
ante a la izquierda y la de ir a la derecha, y al final su resultado.

**Dos animaciones por mano:**

1. **El reparto** (`deal_states`): caen las fichas del ante, las cartas salen
   del mazo boca abajo (las de cada asiento, las de la banca y el flop), se
   giran las de los asientos y el flop, y cada pastilla dice qué lleva. Acaba en
   la mesa de decidir (`decision_state`).
2. **El final** (`showdown_states`), con lo que ha decidido cada uno: cae la
   ficha de ir de quien va y se apagan las cartas de quien se retira; salen el
   turn y el river, la banca enseña sus cartas, se dice si juega y cada asiento
   enseña su resultado, con brillo en las cartas de quien gana.

El reparto depende de cuántos se sientan y sus nombres, y el final de lo que
decide cada uno, así que el cog los pide al cerrar las apuestas y al cerrar las
decisiones, tapando la espera con un texto. Las pausas se hacen con fotogramas
más largos (`ms` de cada estado), no con más fotogramas.
"""

from __future__ import annotations

import io
import math
import random
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from bot.services.blackjack import Card
from bot.services.cards_render import CardRenderer
from bot.services.economy import format_amount
from bot.services.poker import (
    ANTE_PAYS,
    BONUS_FIXED,
    BONUS_POT,
    CALL_FACTOR,
    MAX_SEATS,
    HandValue,
    Outcome,
    TableDeal,
    bonus,
    evaluate,
    payout,
    qualifies,
    showdown,
)
from bot.utils.gif import local_palette_gif

W, H = 820, 500
#: Se dibuja al doble y se reduce (bordes suaves), como el resto de escenas.
S = 2
FRAME_MS = 40
#: El último fotograma se queda quieto un minuto (el cog pone el PNG después).
FINAL_FRAME_MS = 60_000

CARD_W, CARD_H = 64, 90
#: Las cartas de los asientos, más pequeñas que las de la mesa.
SEAT_SCALE = 0.72
#: El mazo, arriba a la derecha: de ahí salen todas las cartas.
DECK_X, DECK_Y = 770.0, 82.0
DEALER_SLOTS = ((375.0, 82.0), (445.0, 82.0))
DEALER_PILL = (548.0, 82.0)
BOARD_SLOTS = tuple((262.0 + 74 * i, 225.0) for i in range(5))
#: Aviso de cuándo juega la banca y cartel de lo que ha hecho.
QUALIFY_Y = 148.0
BANNER_Y = 296.0
#: Asientos: centro de las cartas, separación, nombre, pastilla y fichas.
SEAT_Y, SEAT_GAP = 404.0, 130.0
SEAT_NAME_Y, SEAT_PILL_Y = 352.0, 460.0
SEAT_CARD_DX = 25.0
SEAT_CHIP_DX = 60.0
#: Las tablas: la del ante a la izquierda y la del bote a la derecha.
PAYS_X, BONUS_X, TABLES_Y, TABLE_ROW, TABLE_W = 14.0, 630.0, 142.0, 17.0, 176.0

DEAL_FRAMES = 7
DEAL_STAGGER = 2
FLIP_FRAMES = 7
POP_FRAMES = 4
RESULT_FRAMES = 10

#: Filas de la tabla del ante: (mano, pago). La última junta el resto.
PAY_ROWS: tuple[tuple[str, str], ...] = (
    *((rank.label.upper(), f"{pays}:1") for rank, pays in ANTE_PAYS.items()),
    ("ESCALERA O MENOS", "1:1"),
)
#: Filas de la tabla del 💎 Bote (sin emojis: el Chromium del Docker no los trae).
BONUS_ROWS: tuple[tuple[str, str], ...] = (
    *((rank.label.upper(), "TODO" if p == 1000 else f"{p // 10} %")
      for rank, p in BONUS_POT.items()),
    *((rank.label.upper(), f"×{times}") for rank, times in BONUS_FIXED.items()),
)  # fmt: skip

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"


@dataclass(frozen=True, slots=True)
class Media:
    """Lo que el cog sube: el GIF, el PNG final y cuánto dura el GIF.

    Attributes:
        gif: El GIF; `None` en el plan B de Pillow (solo imagen fija).
        png: El último fotograma.
        seconds: Lo que dura el GIF hasta el último fotograma (0 sin GIF).
    """

    gif: bytes | None
    png: bytes
    seconds: float


@dataclass(frozen=True, slots=True)
class SeatView:
    """Lo que la mesa enseña de un asiento.

    Attributes:
        name: El nombre visible (se limpia con `seat_name`).
        ante: Su ante (para el resultado del final).
        bonus: Si juega el 💎 Bote.
    """

    name: str
    ante: int
    bonus: bool = False


def seat_name(name: str, limit: int = 11) -> str:
    """El nombre para el lienzo: sin emojis ni letras que la fuente no tiene, y corto.

    Se queda con las letras latinas (con tildes), números y algo de puntuación.
    Si no queda nada, «JUGADOR».
    """
    kept = "".join(
        ch
        for ch in unicodedata.normalize("NFC", name)
        if ch.isascii() and (ch.isalnum() or ch in " ._-") or "À" <= ch <= "ÿ"
    ).strip()
    if not kept:
        return "JUGADOR"
    return kept if len(kept) <= limit else kept[: limit - 1].rstrip() + "…"


def seat_x(index: int, seats: int) -> float:
    """El centro del asiento `index` con `seats` asientos, en fila y centrados."""
    return W / 2 + (index - (seats - 1) / 2) * SEAT_GAP


# -- Estados: lo que lee la escena ---------------------------------------------------------------


def meta_state() -> dict[str, Any]:
    """Lo fijo de la escena: las filas de la tabla del ante y las del bote."""
    return {
        "pays": [list(row) for row in PAY_ROWS],
        "bonus": [list(row) for row in BONUS_ROWS],
    }


def _card(card: Card) -> dict[str, Any]:
    return {"rank": card.rank, "suit": card.suit, "label": card.label, "red": card.is_red}


def _card_state(
    card: Card,
    xy: tuple[float, float],
    *,
    rot: float = 0.0,
    scale: float = 1.0,
    flip: float = 1.0,
    lift: float = 0.0,
    glow: float = 0.0,
    dim: float = 0.0,
) -> dict[str, Any]:
    """Una carta sobre la mesa.

    Args:
        flip: 0 dorso, 1 cara (el giro se ve con la escala horizontal).
        lift: Cuánto se levanta (sombra más lejos, algo más grande).
        glow: Brillo de las cartas de la mano ganadora (0-1).
        dim: Cuánto se apaga (cartas que no juegan o mano retirada, 0-1).
    """
    return {
        "card": _card(card),
        "x": round(xy[0], 2),
        "y": round(xy[1], 2),
        "rot": round(rot, 4),
        "scale": round(scale, 3),
        "flip": round(flip, 3),
        "lift": round(lift, 2),
        "glow": round(glow, 3),
        "dim": round(dim, 3),
    }


def _pill(text: str | None, pop: float = 1.0, *, win: bool = False) -> dict[str, Any]:
    """Pastilla con lo que lleva una mano (`None`: aún no se ve)."""
    return {"text": text, "pop": round(pop, 3), "win": win}


def _banner(kind: str, title: str, sub: str = "", a: float = 1.0) -> dict[str, Any]:
    """Cartel del centro: `kind` es `win`, `nq`, `tie`, `lose` o `fold` (da el color)."""
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
    x: float, y: float, t: float, seed: int, *, count: int, size: float = 6.0, spread: float = 70
) -> list[list[float]]:
    """Chispas `[x, y, radio, alfa, clase]` que salen de (x, y) y caen; `t` va de 0 a 1.

    Clase: 0 dorada, 1 verde, 2 blanca.
    """
    rng = random.Random(seed)
    sparks = []
    for _ in range(count):
        angle = rng.uniform(0, math.tau)
        speed = rng.uniform(0.4, 1.0) * spread
        kind = rng.choice((0, 0, 1, 2))
        px = x + math.cos(angle) * speed * t
        py = y + math.sin(angle) * speed * t + 40 * t * t
        radius = size * rng.uniform(0.6, 1.2) * (1 - 0.35 * t)
        alpha = max(0.0, 1 - t**2)
        sparks.append([round(px, 1), round(py, 1), round(radius, 1), round(alpha, 2), kind])
    return sparks


def short(value: HandValue) -> str:
    """Lo que dice la pastilla: la categoría en mayúsculas (`DOBLE PAREJA`)."""
    return value.rank.label.upper()


def slots_for(seats: int) -> dict[str, tuple[float, float]]:
    """Huecos de la mesa: `d0`/`d1` banca, `b0`-`b4` comunitarias y `s{i}a`/`s{i}b` asientos."""
    slots: dict[str, tuple[float, float]] = {"d0": DEALER_SLOTS[0], "d1": DEALER_SLOTS[1]}
    slots.update({f"b{i}": xy for i, xy in enumerate(BOARD_SLOTS)})
    for i in range(seats):
        x = seat_x(i, seats)
        slots[f"s{i}a"] = (x - SEAT_CARD_DX, SEAT_Y)
        slots[f"s{i}b"] = (x + SEAT_CARD_DX, SEAT_Y)
    return slots


def slot_cards(table: TableDeal, seats: int) -> dict[str, Card]:
    """La carta que va en cada hueco."""
    cards = {"d0": table.dealer[0], "d1": table.dealer[1]}
    cards.update({f"b{i}": card for i, card in enumerate(table.board)})
    for i in range(seats):
        cards[f"s{i}a"], cards[f"s{i}b"] = table.seats[i]
    return cards


def _seat_state(view: SeatView, x: float) -> dict[str, Any]:
    return {
        "x": round(x, 2),
        "name": seat_name(view.name),
        "bonus": view.bonus,
        "pill": _pill(None),
        "ante": 0.0,
        "call": 0.0,
        "fold": False,
        "badge": None,
    }


def _state(
    cards: list[dict[str, Any]],
    seats: list[dict[str, Any]],
    *,
    pot: int,
    dealer: dict[str, Any] | None = None,
    qualify: str | None = None,
    banner: dict[str, Any] | None = None,
    sparks: Sequence[Sequence[float]] = (),
    ms: int = FRAME_MS,
    full: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "cards": cards,
        "seats": seats,
        "dealer": dealer or _pill(None),
        "qualify": qualify,
        "banner": banner,
        "pot": format_amount(pot),
        "sparks": [list(s) for s in sparks],
        "ms": ms,
    }
    if full:
        state["full"] = True
    return state


class _Timeline:
    """Fotogramas de una animación: las cartas vivas, los asientos, la banca y el cartel."""

    def __init__(self, table: TableDeal, views: Sequence[SeatView], *, pot: int) -> None:
        n = len(views)
        if not 1 <= n <= MAX_SEATS:
            raise ValueError("Asientos fuera de la mesa.")
        self.table = table
        self.views = list(views)
        self.slots = slots_for(n)
        self.by_slot = slot_cards(table, n)
        self.order = ["d0", "d1", *(f"b{i}" for i in range(5))]
        self.order += [k for i in range(n) for k in (f"s{i}a", f"s{i}b")]
        self.live: dict[str, dict[str, Any]] = {}
        self.seats = [_seat_state(v, seat_x(i, n)) for i, v in enumerate(views)]
        self.dealer = _pill(None)
        self.qualify: str | None = None
        self.banner: dict[str, Any] | None = None
        self.pot = pot
        self.states: list[dict[str, Any]] = []

    def scale(self, slot: str) -> float:
        return SEAT_SCALE if slot.startswith("s") else 1.0

    def place(self, slot: str, **kwargs: Any) -> None:
        """Pone la carta del hueco `slot` (en su sitio salvo que se diga otro `xy`)."""
        xy = kwargs.pop("xy", self.slots[slot])
        kwargs.setdefault("scale", self.scale(slot))
        self.live[slot] = _card_state(self.by_slot[slot], xy, **kwargs)

    def frame(
        self, *, ms: int = FRAME_MS, sparks: Sequence[Sequence[float]] = (), full: bool = False
    ) -> None:
        cards = [self.live[k] for k in self.order if k in self.live]
        # La que se está levantando va encima de las demás.
        cards.sort(key=lambda c: c["lift"])
        self.states.append(
            _state(
                [dict(c) for c in cards],
                [
                    {**s, "pill": dict(s["pill"]), "badge": s["badge"] and dict(s["badge"])}
                    for s in self.seats
                ],
                pot=self.pot,
                dealer=dict(self.dealer),
                qualify=self.qualify,
                banner=dict(self.banner) if self.banner else None,
                sparks=sparks,
                ms=ms,
                full=full,
            )
        )

    def fly(self, slots: Sequence[str], rng: random.Random) -> None:
        """Las cartas de `slots` salen del mazo boca abajo, solapadas."""
        total = DEAL_STAGGER * (len(slots) - 1) + DEAL_FRAMES
        for f in range(1, total + 1):
            for n, slot in enumerate(slots):
                u = (f - n * DEAL_STAGGER) / DEAL_FRAMES
                if u > 0:
                    self.live[slot] = _flying(
                        self.by_slot[slot], self.slots[slot], u, rng, self.scale(slot)
                    )
            self.frame()

    def flip(self, slots: Sequence[str], *, stagger: int = 0, dim: float = 0.0) -> None:
        """Se giran las cartas de `slots` (a la vez, o una tras otra con `stagger`)."""
        total = stagger * (len(slots) - 1) + FLIP_FRAMES
        for f in range(1, total + 1):
            for n, slot in enumerate(slots):
                u = _smooth((f - n * stagger) / FLIP_FRAMES)
                if u > 0:
                    self.place(slot, flip=u, lift=10 * math.sin(math.pi * u), dim=dim)
            self.frame()

    def pop_seats(self, texts: Sequence[str | None], *, wins: Sequence[bool] = ()) -> None:
        """Las pastillas de los asientos cambian a `texts` con un rebote (las `None`, no)."""
        for f in range(1, POP_FRAMES + 1):
            for i, text in enumerate(texts):
                if text is not None:
                    won = bool(wins and wins[i])
                    self.seats[i]["pill"] = _pill(text, _back(f / POP_FRAMES), win=won)
            self.frame()
        for i, text in enumerate(texts):
            if text is not None:
                self.seats[i]["pill"] = _pill(text, win=bool(wins and wins[i]))

    def pop_dealer(self, text: str) -> None:
        for f in range(1, POP_FRAMES + 1):
            self.dealer = _pill(text, _back(f / POP_FRAMES))
            self.frame()
        self.dealer = _pill(text)


def _flying(
    card: Card, xy: tuple[float, float], u: float, rng: random.Random, scale: float
) -> dict[str, Any]:
    """Una carta boca abajo de camino a su hueco desde el mazo (`u` de 0 a 1)."""
    e = 1 - (1 - min(1.0, u)) ** 3
    start_rot = -0.8 + 0.1 * rng.random()
    return _card_state(
        card,
        (DECK_X + (xy[0] - DECK_X) * e, DECK_Y + (xy[1] - DECK_Y) * e - 20 * math.sin(math.pi * e)),
        rot=start_rot * (1 - e),
        scale=(0.6 + 0.4 * e) * scale,
        flip=0.0,
        lift=16 * (1 - e),
    )


def board_state(pot: int = 0) -> dict[str, Any]:
    """La mesa vacía, sin asientos (al abrirla o entre manos)."""
    return _state([], [], pot=pot, ms=FINAL_FRAME_MS)


def _seat_slots(i: int) -> tuple[str, str]:
    return f"s{i}a", f"s{i}b"


def _decision(t: _Timeline) -> None:
    """Deja `t` en la mesa de decidir: asientos y flop de cara, banca boca abajo, antes puestos."""
    for slot in t.order:
        if slot in ("b3", "b4"):
            continue
        t.place(slot, flip=0.0 if slot.startswith("d") else 1.0)
    for i, seat in enumerate(t.seats):
        seat["ante"] = 1.0
        hand = t.table.seat(i)
        seat["pill"] = _pill(short(hand.flop_value))


def decision_state(table: TableDeal, views: Sequence[SeatView], *, pot: int = 0) -> dict[str, Any]:
    """La mesa al decidir: cada asiento con sus cartas y lo que lleva, el flop y la banca tapada."""
    t = _Timeline(table, views, pot=pot)
    _decision(t)
    t.frame(ms=FINAL_FRAME_MS)
    return t.states[-1]


def deal_states(
    table: TableDeal, views: Sequence[SeatView], *, pot: int = 0, seed: int = 0
) -> list[dict[str, Any]]:
    """El reparto, fotograma a fotograma, hasta la mesa de decidir.

    Args:
        table: Las cartas de la mano.
        views: Los asientos ocupados, en orden.
        pot: Lo que lleva el 💎 Bote (se enseña junto a su tabla).
        seed: Semilla del vuelo de las cartas (misma semilla, mismo GIF).
    """
    rng = random.Random(seed)
    t = _Timeline(table, views, pot=pot)
    n = len(views)
    t.frame(ms=160, full=True)
    for f in range(1, POP_FRAMES + 1):
        for seat in t.seats:
            seat["ante"] = round(_back(f / POP_FRAMES), 3)
        t.frame()
    for seat in t.seats:
        seat["ante"] = 1.0
    # Como en la mesa de verdad: una carta a cada uno, otra a cada uno, y la banca.
    first = [f"s{i}a" for i in range(n)] + ["d0"]
    second = [f"s{i}b" for i in range(n)] + ["d1"]
    t.fly(first + second, rng)
    t.fly(("b0", "b1", "b2"), rng)
    t.frame(ms=200)
    t.flip([k for i in range(n) for k in _seat_slots(i)], stagger=1)
    t.frame(ms=220)
    t.flip(("b0", "b1", "b2"), stagger=3)
    t.pop_seats([short(table.seat(i).flop_value) for i in range(n)])
    final = _Timeline(table, views, pot=pot)
    _decision(final)
    final.frame(ms=FINAL_FRAME_MS)
    t.states.append(final.states[-1])
    return t.states


def seat_result(table: TableDeal, index: int, view: SeatView, called: bool) -> tuple[str, str]:
    """El resultado de un asiento para su placa: (`kind`, texto).

    `kind` da el color: `win` (gana), `nq` (la banca no juega), `tie`, `lose` o
    `fold`. El texto es lo que gana o pierde la mano (sin el bote).
    """
    if not called:
        return "fold", "RETIRADO"
    hand = table.seat(index)
    net = payout(hand, view.ante, called=True) - view.ante * (1 + CALL_FACTOR)
    match showdown(hand):
        case Outcome.WIN:
            return "win", f"+{format_amount(net)}"
        case Outcome.NO_QUALIFY:
            return "nq", f"+{format_amount(net)}"
        case Outcome.TIE:
            return "tie", "EMPATE"
    return "lose", f"-{format_amount(-net)}"


def showdown_states(
    table: TableDeal,
    views: Sequence[SeatView],
    called: Sequence[bool],
    *,
    pot: int = 0,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """El final de la mano desde la mesa de decidir, con lo que ha decidido cada asiento.

    Quien se retira ve igual el final con sus cartas apagadas: su placa dice
    RETIRADO. La apuesta del 💎 Bote se resuelve con las siete cartas de todos.
    """
    rng = random.Random(seed)
    n = len(views)
    t = _Timeline(table, views, pot=pot)
    _decision(t)
    t.frame(ms=120, full=True)

    # Cae la ficha de ir de quien va; quien se retira apaga sus cartas.
    for f in range(1, POP_FRAMES + 2):
        u = f / (POP_FRAMES + 1)
        for i, seat in enumerate(t.seats):
            if called[i]:
                seat["call"] = round(_back(u), 3)
            else:
                seat["fold"] = True
                for slot in _seat_slots(i):
                    t.place(slot, dim=0.6 * _smooth(u))
        t.frame()
    for i, seat in enumerate(t.seats):
        if called[i]:
            seat["call"] = 1.0
    t.frame(ms=180)

    def seat_dim(i: int) -> float:
        return 0.0 if called[i] else 0.6

    # Turn y river: cada una vuela, se gira y las pastillas se ponen al día.
    for index in (3, 4):
        slot = f"b{index}"
        t.fly((slot,), rng)
        t.flip((slot,))
        texts = [short(evaluate((*table.seats[i], *table.board[: index + 1]))) for i in range(n)]
        t.pop_seats(texts)
        t.frame(ms=200 if index == 3 else 300)

    # La banca enseña sus cartas y se dice si juega.
    t.flip(("d0", "d1"), stagger=2)
    dealer = evaluate((*table.dealer, *table.board))
    t.pop_dealer(short(dealer))
    plays = qualifies(dealer)
    t.qualify = "si" if plays else "no"
    for f in range(1, 7):
        t.banner = _banner(
            "lose" if plays else "nq",
            "LA BANCA JUEGA" if plays else "LA BANCA NO JUEGA",
            short(dealer),
            _back(f / 6),
        )
        t.frame()
    t.frame(ms=380)

    # Resultados: brillan las cartas de quien gana y cada placa sale con un rebote.
    results = [seat_result(table, i, views[i], called[i]) for i in range(n)]
    winners = [kind in ("win", "nq") for kind, _ in results]
    texts = [s["pill"]["text"] for s in t.seats]
    for i, s in enumerate(t.seats):
        s["pill"] = _pill(texts[i], win=winners[i])
    lucky = [views[i].bonus and bonus(table.seat(i).player_value.rank).won for i in range(n)]
    for f in range(1, RESULT_FRAMES + 1):
        u = _smooth(f / RESULT_FRAMES)
        sparks: list[list[float]] = []
        for i, seat in enumerate(t.seats):
            hand = table.seat(i).player_value
            lit = {(c.rank, c.suit) for c in hand.cards} if winners[i] else set()
            for slot in _seat_slots(i):
                card = t.by_slot[slot]
                t.place(slot, glow=u if (card.rank, card.suit) in lit else 0.0, dim=seat_dim(i))
            kind, text = results[i]
            seat["badge"] = {
                "kind": kind,
                "text": text,
                "a": round(_back(min(1.0, f / 6)), 3),
                "bonus": bool(lucky[i]),
            }
            if winners[i] or lucky[i]:
                x = seat["x"]
                sparks += _burst(x, SEAT_Y, f / RESULT_FRAMES, seed + i, count=10)
        t.frame(sparks=sparks)
    for seat in t.seats:
        seat["badge"]["a"] = 1.0
    t.frame(ms=FINAL_FRAME_MS)
    return t.states


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
GOLD = (255, 214, 92)
WHITE = (255, 255, 255)
BADGE_RGB = {
    "win": (40, 160, 80),
    "nq": (40, 160, 80),
    "tie": (52, 120, 230),
    "lose": (200, 50, 60),
    "fold": (90, 94, 102),
}


@cache
def _font(size: int, *, title: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TITLE_FONT if title else TEXT_FONT), size * S)


@cache
def _cards() -> CardRenderer:
    return CardRenderer()


def _centered(
    d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: Any, fill: Any
) -> None:
    d.text((xy[0] * S, xy[1] * S), text, font=font, fill=fill, anchor="mm")


@cache
def _background() -> Image.Image:
    """Tapete, huecos de la banca y la mesa, y las dos tablas."""
    img = Image.new("RGB", (W * S, H * S), FELT)
    d = ImageDraw.Draw(img)
    _centered(d, (110, 24), "CASINO HOLD'EM", _font(20, title=True), GOLD)
    _centered(d, (300, DEALER_SLOTS[0][1]), "BANCA", _font(12), (255, 150, 150))
    _centered(d, (W / 2, QUALIFY_Y), "LA BANCA JUEGA CON PAREJA DE 4 O MÁS", _font(9), GOLD)
    for x, y in (*DEALER_SLOTS, *BOARD_SLOTS):
        d.rounded_rectangle(
            (
                (x - CARD_W / 2) * S,
                (y - CARD_H / 2) * S,
                (x + CARD_W / 2) * S,
                (y + CARD_H / 2) * S,
            ),
            6 * S,
            outline=(60, 140, 100),
            width=S,
        )
    for x0, title, rows in ((PAYS_X, "EL ANTE PAGA", PAY_ROWS), (BONUS_X, "BOTE", BONUS_ROWS)):
        d.text((x0 * S, TABLES_Y * S), title, font=_font(10, title=True), fill=GOLD, anchor="lm")
        for row, (label, pays) in enumerate(rows):
            y = TABLES_Y + 18 + row * TABLE_ROW
            d.text((x0 * S, y * S), label, font=_font(8), fill=(220, 230, 215), anchor="lm")
            d.text(((x0 + TABLE_W) * S, y * S), pays, font=_font(8), fill=WHITE, anchor="rm")
    return img


def _paint_card(img: Image.Image, state: dict[str, Any]) -> None:
    info = state["card"]
    card = Card(info["rank"], info["suit"]) if state["flip"] > 0.5 else None
    w, h = round(CARD_W * state["scale"] * S), round(CARD_H * state["scale"] * S)
    sprite = _cards().card_sprite(card).resize((w, h), Image.Resampling.LANCZOS)
    if state["dim"] > 0:
        rgb = ImageEnhance.Brightness(sprite.convert("RGB")).enhance(1 - 0.6 * state["dim"])
        sprite = Image.merge("RGBA", (*rgb.split(), sprite.getchannel("A")))
    x = int(state["x"] * S - sprite.width / 2)
    y = int((state["y"] - state["lift"]) * S - sprite.height / 2)
    if state["glow"] > 0:
        ImageDraw.Draw(img).rounded_rectangle(
            (x - 3 * S, y - 3 * S, x + sprite.width + 3 * S, y + sprite.height + 3 * S),
            9 * S,
            outline=GOLD,
            width=3 * S,
        )
    img.paste(sprite, (x, y), sprite)


def _pill_box(d: ImageDraw.ImageDraw, xy: tuple[float, float], pill: dict[str, Any]) -> None:
    if not pill["text"]:
        return
    x, y = xy
    d.rounded_rectangle(
        ((x - 50) * S, (y - 10) * S, (x + 50) * S, (y + 10) * S),
        10 * S,
        fill=(10, 40, 26),
        outline=GOLD if pill["win"] else WHITE,
        width=S,
    )
    _centered(d, (x, y), pill["text"], _font(8), WHITE)


def paint(meta: dict[str, Any], state: dict[str, Any]) -> Image.Image:
    """Un fotograma con Pillow, a 820×500 (más sencillo que el de Node)."""
    img = _background().copy()
    for card in state["cards"]:
        _paint_card(img, card)
    d = ImageDraw.Draw(img)
    _centered(d, (110, 46), f"BOTE {state['pot']}", _font(10), GOLD)
    _pill_box(d, DEALER_PILL, state["dealer"])
    for seat in state["seats"]:
        x = seat["x"]
        _centered(d, (x, SEAT_NAME_Y), seat["name"], _font(9), WHITE)
        _pill_box(d, (x, SEAT_PILL_Y), seat["pill"])
        badge = seat["badge"]
        if badge:
            rgb = BADGE_RGB.get(badge["kind"], (90, 94, 102))
            d.rounded_rectangle(
                ((x - 48) * S, (SEAT_Y - 11) * S, (x + 48) * S, (SEAT_Y + 11) * S),
                8 * S,
                fill=rgb,
                outline=WHITE,
                width=S,
            )
            _centered(d, (x, SEAT_Y), badge["text"], _font(10), WHITE)
    banner = state["banner"]
    if banner:
        rgb = BADGE_RGB.get(banner["kind"], (90, 94, 102))
        d.rounded_rectangle(
            ((W / 2 - 140) * S, (BANNER_Y - 16) * S, (W / 2 + 140) * S, (BANNER_Y + 16) * S),
            12 * S,
            fill=rgb,
            outline=WHITE,
            width=2 * S,
        )
        _centered(d, (W / 2, BANNER_Y), f"{banner['title']} · {banner['sub']}", _font(10), WHITE)
    return img.convert("RGB").reduce(S)


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=6)
    return out.getvalue()


def _still(state: dict[str, Any]) -> Media:
    """Un estado como imagen fija, sin GIF."""
    return Media(gif=None, png=_png(paint(meta_state(), state)), seconds=0.0)


class PokerRenderer:
    """Dibujo de reserva con Pillow: la mesa quieta y el final de cada animación, sin GIF."""

    def board(self, pot: int = 0) -> bytes:
        """PNG de la mesa vacía."""
        return _png(paint(meta_state(), board_state(pot)))

    def deal(self, table: TableDeal, views: Sequence[SeatView], *, pot: int = 0) -> Media:
        """La mesa de decidir, como imagen fija."""
        return _still(decision_state(table, views, pot=pot))

    def showdown(
        self, table: TableDeal, views: Sequence[SeatView], called: Sequence[bool], *, pot: int = 0
    ) -> Media:
        """El final, como imagen fija."""
        return _still(showdown_states(table, views, called, pot=pot)[-1])
