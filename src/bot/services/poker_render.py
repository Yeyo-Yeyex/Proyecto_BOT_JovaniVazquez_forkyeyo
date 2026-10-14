"""Póker (Casino Hold'em): qué se ve en la mesa, sus animaciones y el dibujo de reserva con Pillow.

La imagen buena la pinta Node con canvas (`bot.services.poker_scene` y
`assets/poker/escena.html`). Este módulo decide **qué** se ve en cada
fotograma y lo pinta también con Pillow, más sencillo y solo quieto, para
cuando no hay Node ni navegador. Las dos versiones leen los mismos diccionarios
(`board_state`, `deal_states` y `showdown_states`).

**La mesa** (640×420): arriba la banca con sus dos cartas y el mazo a la
derecha; en el centro las cinco comunitarias y, entre medias, el aviso de que
la banca juega con pareja de cuatros; abajo las dos cartas del jugador. Junto a
cada mano, una pastilla con lo que lleva (`Pareja`, `Color`…). Abajo a la
izquierda, la tabla del ante (la fila que cobra se ilumina) y a la derecha las
casillas de ANTE e IR, con su ficha cuando hay apuesta.

**Dos animaciones por mano**, las dos sin depender del dinero:

1. **El reparto** (`deal_states`): las cartas salen del mazo boca abajo (las
   del jugador, las de la banca y el flop), se giran las del jugador y luego
   el flop, y la pastilla dice qué lleva. Acaba en la mesa de decidir.
2. **El final** (`showdown_states`), uno si va y otro si se retira. Si va, cae
   la ficha de IR, salen el turn y el river, la banca enseña sus cartas, se
   dice si juega y brillan las cinco cartas de la mano ganadora. Si se retira,
   sus cartas se apagan y se enseña igual lo que habría pasado.

Como las nueve cartas se sortean al repartir, el cog pinta el reparto antes de
que el jugador pulse y los dos finales mientras mira el flop: al pulsar, el GIF
ya está hecho. Las pausas se hacen con fotogramas más largos (`ms` de cada
estado), no con más fotogramas.
"""

from __future__ import annotations

import io
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from bot.services.blackjack import Card
from bot.services.cards_render import CardRenderer
from bot.services.poker import (
    ANTE_PAYS,
    Deal,
    HandValue,
    Outcome,
    Rank,
    ante_pays,
    evaluate,
    qualifies,
    showdown,
)
from bot.utils.gif import local_palette_gif

W, H = 640, 420
#: Se dibuja al doble y se reduce (bordes suaves), como el resto de escenas.
S = 2
FRAME_MS = 40
#: El último fotograma se queda quieto un minuto (el cog pone el PNG después).
FINAL_FRAME_MS = 60_000

CARD_W, CARD_H = 64, 90
#: El mazo, arriba a la derecha: de ahí salen todas las cartas.
DECK_X, DECK_Y = 578.0, 72.0
DEALER_SLOTS = ((285.0, 72.0), (355.0, 72.0))
BOARD_SLOTS = ((172.0, 200.0), (246.0, 200.0), (320.0, 200.0), (394.0, 200.0), (468.0, 200.0))
PLAYER_SLOTS = ((285.0, 330.0), (355.0, 330.0))
#: Pastillas con lo que lleva cada mano, a la derecha de sus cartas.
DEALER_PILL = (455.0, 72.0)
PLAYER_PILL = (455.0, 330.0)
#: Casillas de las apuestas: ANTE e IR (con la ficha cuando hay apuesta).
ANTE_SPOT = (578.0, 300.0)
CALL_SPOT = (578.0, 362.0)
#: Aviso de cuándo juega la banca y cartel del resultado.
QUALIFY_Y = 134.0
BANNER_Y = 266.0
#: La tabla del ante, abajo a la izquierda: esquina y alto de cada fila.
PAYS_X, PAYS_Y, PAYS_ROW = 14.0, 268.0, 19.0

DEAL_FRAMES = 7
DEAL_STAGGER = 3
FLIP_FRAMES = 7
POP_FRAMES = 4
RESULT_FRAMES = 12

#: Filas de la tabla del ante, de arriba abajo: (mano, pago). La última junta el resto.
PAY_ROWS: tuple[tuple[str, str], ...] = (
    *((rank.label.upper(), f"{pays}:1") for rank, pays in ANTE_PAYS.items()),
    ("ESCALERA O MENOS", "1:1"),
)

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
class Showdown:
    """Los dos finales posibles de una mano, pintados antes de decidir."""

    call: Media
    fold: Media


def pay_row(rank: Rank) -> int:
    """La fila de la tabla del ante que cobra con esta mano (la última, de escalera abajo)."""
    ranks = list(ANTE_PAYS)
    return ranks.index(rank) if rank in ANTE_PAYS else len(ranks)


# -- Estados: lo que lee la escena ---------------------------------------------------------------


def meta_state() -> dict[str, Any]:
    """Lo fijo de la escena: las filas de la tabla del ante."""
    return {"pays": [list(row) for row in PAY_ROWS]}


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
    """Cartel del resultado: `kind` es `win`, `nq`, `tie`, `lose` o `fold`."""
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
    x: float, y: float, t: float, seed: int, *, count: int, size: float = 7.0, spread: float = 90
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


#: Huecos de la mesa por nombre: `p0`/`p1` jugador, `d0`/`d1` banca, `b0`-`b4` comunitarias.
SLOTS: dict[str, tuple[float, float]] = {
    "p0": PLAYER_SLOTS[0],
    "p1": PLAYER_SLOTS[1],
    "d0": DEALER_SLOTS[0],
    "d1": DEALER_SLOTS[1],
    **{f"b{i}": xy for i, xy in enumerate(BOARD_SLOTS)},
}
#: Orden de dibujo (la última, encima).
DRAW_ORDER = ("d0", "d1", "b0", "b1", "b2", "b3", "b4", "p0", "p1")


def slot_cards(cards: Deal) -> dict[str, Card]:
    """La carta que va en cada hueco."""
    return {
        "p0": cards.player[0],
        "p1": cards.player[1],
        "d0": cards.dealer[0],
        "d1": cards.dealer[1],
        **{f"b{i}": card for i, card in enumerate(cards.board)},
    }


class _Timeline:
    """Fotogramas de una animación: las cartas vivas, las pastillas, las fichas y el cartel."""

    def __init__(self, cards: Deal) -> None:
        self.by_slot = slot_cards(cards)
        self.live: dict[str, dict[str, Any]] = {}
        self.player = _pill(None)
        self.dealer = _pill(None)
        self.spots = {"ante": 1.0, "call": 0.0, "fold": False}
        self.qualify: str | None = None
        self.pays: int | None = None
        self.banner: dict[str, Any] | None = None
        self.states: list[dict[str, Any]] = []

    def place(self, slot: str, **kwargs: Any) -> None:
        """Pone la carta del hueco `slot` (en su sitio salvo que se diga otro `xy`)."""
        xy = kwargs.pop("xy", SLOTS[slot])
        self.live[slot] = _card_state(self.by_slot[slot], xy, **kwargs)

    def frame(
        self, *, ms: int = FRAME_MS, sparks: Sequence[Sequence[float]] = (), full: bool = False
    ) -> None:
        cards = [self.live[k] for k in DRAW_ORDER if k in self.live]
        # La que se está levantando va encima de las demás.
        cards.sort(key=lambda c: c["lift"])
        self.states.append(
            _state(
                [dict(c) for c in cards],
                player=dict(self.player),
                dealer=dict(self.dealer),
                spots=dict(self.spots),
                qualify=self.qualify,
                pays=self.pays,
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
                    self.live[slot] = _flying(self.by_slot[slot], SLOTS[slot], u, rng)
            self.frame()

    def flip(self, slots: Sequence[str], *, stagger: int = 0) -> None:
        """Se giran las cartas de `slots` (a la vez, o una tras otra con `stagger`)."""
        total = stagger * (len(slots) - 1) + FLIP_FRAMES
        for f in range(1, total + 1):
            for n, slot in enumerate(slots):
                u = _smooth((f - n * stagger) / FLIP_FRAMES)
                if u > 0:
                    self.place(slot, flip=u, lift=10 * math.sin(math.pi * u))
            self.frame()

    def pop(self, side: str, text: str, *, win: bool = False) -> None:
        """La pastilla de `side` (`player` o `dealer`) cambia a `text` con un rebote."""
        for f in range(1, POP_FRAMES + 1):
            setattr(self, side, _pill(text, _back(f / POP_FRAMES), win=win))
            self.frame()
        setattr(self, side, _pill(text, win=win))


def _state(
    cards: list[dict[str, Any]],
    *,
    player: dict[str, Any] | None = None,
    dealer: dict[str, Any] | None = None,
    spots: dict[str, Any] | None = None,
    qualify: str | None = None,
    pays: int | None = None,
    banner: dict[str, Any] | None = None,
    sparks: Sequence[Sequence[float]] = (),
    ms: int = FRAME_MS,
    full: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "cards": cards,
        "player": player or _pill(None),
        "dealer": dealer or _pill(None),
        "spots": spots or {"ante": 0.0, "call": 0.0, "fold": False},
        "qualify": qualify,
        "pays": pays,
        "banner": banner,
        "sparks": [list(s) for s in sparks],
        "ms": ms,
    }
    if full:
        state["full"] = True
    return state


def _flying(card: Card, xy: tuple[float, float], u: float, rng: random.Random) -> dict[str, Any]:
    """Una carta boca abajo de camino a su hueco desde el mazo (`u` de 0 a 1)."""
    e = 1 - (1 - min(1.0, u)) ** 3
    start_rot = -0.8 + 0.1 * rng.random()
    return _card_state(
        card,
        (DECK_X + (xy[0] - DECK_X) * e, DECK_Y + (xy[1] - DECK_Y) * e - 20 * math.sin(math.pi * e)),
        rot=start_rot * (1 - e),
        scale=0.6 + 0.4 * e,
        flip=0.0,
        lift=16 * (1 - e),
    )


def short(value: HandValue) -> str:
    """Lo que dice la pastilla: la categoría en mayúsculas (`DOBLE PAREJA`)."""
    return value.rank.label.upper()


def decision_state(cards: Deal) -> dict[str, Any]:
    """La mesa al decidir: el jugador y el flop de cara, la banca boca abajo y el ante puesto."""
    by_slot = slot_cards(cards)
    shown = [
        _card_state(by_slot[k], SLOTS[k], flip=0.0 if k.startswith("d") else 1.0)
        for k in DRAW_ORDER
        if k[0] in "dp" or k in ("b0", "b1", "b2")
    ]
    return _state(
        shown,
        player=_pill(short(cards.flop_value)),
        spots={"ante": 1.0, "call": 0.0, "fold": False},
        ms=FINAL_FRAME_MS,
    )


def board_state() -> dict[str, Any]:
    """La mesa vacía, sin apuestas (al abrir la mesa)."""
    return _state([], ms=FINAL_FRAME_MS)


def deal_states(cards: Deal, *, seed: int = 0) -> list[dict[str, Any]]:
    """El reparto, fotograma a fotograma, hasta la mesa de decidir.

    Args:
        cards: Las nueve cartas de la mano.
        seed: Semilla del vuelo de las cartas (misma semilla, mismo GIF).
    """
    rng = random.Random(seed)
    t = _Timeline(cards)
    t.spots["ante"] = 0.0
    t.frame(ms=160, full=True)
    for f in range(1, POP_FRAMES + 1):
        t.spots["ante"] = round(_back(f / POP_FRAMES), 3)
        t.frame()
    t.spots["ante"] = 1.0
    t.fly(("p0", "d0", "p1", "d1"), rng)
    t.fly(("b0", "b1", "b2"), rng)
    t.frame(ms=200)
    t.flip(("p0", "p1"))
    t.frame(ms=260)
    t.flip(("b0", "b1", "b2"), stagger=3)
    t.pop("player", short(cards.flop_value))
    final = decision_state(cards)
    t.states.append(final)
    return t.states


def _verdict(cards: Deal, called: bool) -> dict[str, Any]:
    """El cartel final: qué ha pasado (o qué habría pasado, si se retiró)."""
    outcome = showdown(cards)
    player, dealer = cards.player_value, cards.dealer_value
    if not called:
        sub = {
            Outcome.WIN: "HABRÍAS GANADO",
            Outcome.NO_QUALIFY: "LA BANCA NO JUGABA",
            Outcome.TIE: "HABRÍA SIDO EMPATE",
            Outcome.LOSE: "BIEN RETIRADO",
        }[outcome]
        return _banner("fold", "TE RETIRAS", sub)
    match outcome:
        case Outcome.WIN:
            return _banner("win", "¡GANAS!", f"{short(player)} A {short(dealer)}")
        case Outcome.NO_QUALIFY:
            return _banner("nq", "LA BANCA NO JUEGA", f"ANTE {ante_pays(player.rank)}:1")
        case Outcome.TIE:
            return _banner("tie", "EMPATE", "SE DEVUELVE TODO")
    return _banner("lose", "GANA LA BANCA", f"{short(dealer)} A {short(player)}")


def _winning_cards(cards: Deal) -> tuple[set[tuple[int, int]], bool]:
    """Las cartas que brillan al final y si son del jugador (o de los dos, si empatan)."""
    outcome = showdown(cards)
    player = {(c.rank, c.suit) for c in cards.player_value.cards}
    dealer = {(c.rank, c.suit) for c in cards.dealer_value.cards}
    if outcome in (Outcome.WIN, Outcome.NO_QUALIFY):
        return player, True
    if outcome is Outcome.TIE:
        return player | dealer, True
    return dealer, False


def showdown_states(cards: Deal, *, called: bool, seed: int = 0) -> list[dict[str, Any]]:
    """El final de la mano desde la mesa de decidir, si va (`called`) o si se retira.

    Si se retira se enseña igual lo que habría pasado: sus cartas se apagan, pero
    salen el turn, el river y las de la banca.
    """
    rng = random.Random(seed)
    t = _Timeline(cards)
    start = decision_state(cards)
    t.live = {k: dict(c) for k, c in zip(_decision_slots(), start["cards"], strict=True)}
    t.player = dict(start["player"])
    t.frame(ms=120, full=True)
    fold_dim = 0.0
    if called:
        for f in range(1, POP_FRAMES + 2):
            t.spots["call"] = round(_back(f / (POP_FRAMES + 1)), 3)
            t.frame()
        t.spots["call"] = 1.0
    else:
        t.spots["fold"] = True
        for f in range(1, 7):
            fold_dim = 0.6 * _smooth(f / 6)
            for k in ("p0", "p1"):
                t.place(k, dim=fold_dim, xy=(SLOTS[k][0], SLOTS[k][1] + 6 * _smooth(f / 6)))
            t.frame()
    t.frame(ms=180)

    # Turn y river: cada una vuela, se gira y la pastilla del jugador se pone al día.
    for index in (3, 4):
        slot = f"b{index}"
        t.fly((slot,), rng)
        t.flip((slot,))
        seen = (*cards.player, *cards.board[: index + 1])
        t.pop("player", short(evaluate(seen)))
        t.frame(ms=220 if index == 3 else 320)

    # La banca enseña sus cartas y se dice si juega.
    t.flip(("d0", "d1"), stagger=2)
    dealer = cards.dealer_value
    t.pop("dealer", short(dealer))
    t.qualify = "si" if qualifies(dealer) else "no"
    t.frame(ms=420)

    # Resultado: brillan las cartas que ganan, el resto se apaga y sale el cartel.
    glow, player_won = _winning_cards(cards)
    outcome = showdown(cards)
    banner = _verdict(cards, called)
    if called and outcome in (Outcome.WIN, Outcome.NO_QUALIFY):
        t.pays = pay_row(cards.player_value.rank)
    t.player = _pill(t.player["text"], win=player_won)
    t.dealer = _pill(t.dealer["text"], win=not player_won or outcome is Outcome.TIE)
    celebrate = called and outcome in (Outcome.WIN, Outcome.NO_QUALIFY)
    for f in range(1, RESULT_FRAMES + 1):
        u = _smooth(f / RESULT_FRAMES)
        for slot, card in t.by_slot.items():
            lit = (card.rank, card.suit) in glow
            base_dim = fold_dim if slot[0] == "p" else 0.0
            t.place(
                slot,
                glow=u if lit else 0.0,
                dim=max(base_dim, 0.0 if lit else 0.5 * u),
                xy=(SLOTS[slot][0], SLOTS[slot][1] + (6 if base_dim else 0)),
            )
        t.banner = _banner(banner["kind"], banner["title"], banner["sub"], _back(min(1.0, f / 7)))
        sparks = (
            _burst(PLAYER_PILL[0], PLAYER_PILL[1], f / RESULT_FRAMES, seed + 2, count=18)
            if celebrate
            else ()
        )
        t.frame(sparks=sparks)
    t.banner = banner
    t.frame(ms=FINAL_FRAME_MS)
    return t.states


def _decision_slots() -> list[str]:
    """Los huecos de `decision_state`, en el orden en que salen sus cartas."""
    return [k for k in DRAW_ORDER if k[0] in "dp" or k in ("b0", "b1", "b2")]


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
BANNER_RGB = {
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
    """Tapete, huecos de las cartas, casillas y tabla del ante."""
    img = Image.new("RGB", (W * S, H * S), FELT)
    d = ImageDraw.Draw(img)
    _centered(d, (110, 24), "CASINO HOLD'EM", _font(20, title=True), GOLD)
    _centered(d, (200, DEALER_SLOTS[0][1]), "BANCA", _font(12), (255, 150, 150))
    _centered(d, (200, PLAYER_SLOTS[0][1]), "TÚ", _font(12), (150, 190, 255))
    _centered(d, (W / 2, QUALIFY_Y), "LA BANCA JUEGA CON PAREJA DE 4 O MÁS", _font(9), GOLD)
    for x, y in (*DEALER_SLOTS, *BOARD_SLOTS, *PLAYER_SLOTS):
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
    for (x, y), label in ((ANTE_SPOT, "ANTE"), (CALL_SPOT, "IR")):
        d.ellipse(((x - 24) * S, (y - 24) * S, (x + 24) * S, (y + 24) * S), outline=GOLD, width=S)
        _centered(d, (x, y), label, _font(10), GOLD)
    return img


def _paint_card(img: Image.Image, state: dict[str, Any]) -> None:
    info = state["card"]
    card = Card(info["rank"], info["suit"]) if state["flip"] > 0.5 else None
    sprite = _cards().card_sprite(card).resize((CARD_W * S, CARD_H * S), Image.Resampling.LANCZOS)
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


def paint(meta: dict[str, Any], state: dict[str, Any]) -> Image.Image:
    """Un fotograma con Pillow, a 640×420 (más sencillo que el de Node)."""
    img = _background().copy()
    for card in state["cards"]:
        _paint_card(img, card)
    d = ImageDraw.Draw(img)
    for (x, y), pill in ((PLAYER_PILL, state["player"]), (DEALER_PILL, state["dealer"])):
        if pill["text"]:
            d.rounded_rectangle(
                ((x - 62) * S, (y - 12) * S, (x + 62) * S, (y + 12) * S),
                12 * S,
                fill=(10, 40, 26),
                outline=GOLD if pill["win"] else WHITE,
                width=S,
            )
            _centered(d, (x, y), pill["text"], _font(10), WHITE)
    for row, (label, pays) in enumerate(meta["pays"]):
        y = PAYS_Y + 20 + row * PAYS_ROW
        rgb = GOLD if state["pays"] == row else (200, 220, 205)
        d.text((PAYS_X * S, y * S), label, font=_font(9), fill=rgb, anchor="lm")
        d.text(((PAYS_X + 196) * S, y * S), pays, font=_font(9), fill=rgb, anchor="rm")
    spots = state["spots"]
    for (x, y), on in ((ANTE_SPOT, spots["ante"]), (CALL_SPOT, spots["call"])):
        if on > 0.5:
            d.ellipse(((x - 15) * S, (y - 15) * S, (x + 15) * S, (y + 15) * S), fill=(200, 40, 50))
    banner = state["banner"]
    if banner:
        rgb = BANNER_RGB.get(banner["kind"], (90, 94, 102))
        d.rounded_rectangle(
            ((W / 2 - 150) * S, (BANNER_Y - 18) * S, (W / 2 + 150) * S, (BANNER_Y + 18) * S),
            12 * S,
            fill=rgb,
            outline=WHITE,
            width=2 * S,
        )
        _centered(d, (W / 2, BANNER_Y - 5), banner["title"], _font(16, title=True), WHITE)
        if banner["sub"]:
            _centered(d, (W / 2, BANNER_Y + 10), banner["sub"], _font(8), (255, 236, 170))
    return img.convert("RGB").reduce(S)


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=6)
    return out.getvalue()


def _still(states: Callable[[], list[dict[str, Any]]]) -> Media:
    """El último fotograma de una animación, sin GIF."""
    return Media(gif=None, png=_png(paint(meta_state(), states()[-1])), seconds=0.0)


class PokerRenderer:
    """Dibujo de reserva con Pillow: la mesa quieta y el final de cada animación, sin GIF."""

    def board(self) -> bytes:
        """PNG de la mesa vacía."""
        return _png(paint(meta_state(), board_state()))

    def deal(self, cards: Deal) -> Media:
        """La mesa de decidir, como imagen fija."""
        return _still(lambda: [decision_state(cards)])

    def showdown(self, cards: Deal) -> Showdown:
        """Los dos finales, como imágenes fijas."""
        return Showdown(
            call=_still(lambda: showdown_states(cards, called=True)),
            fold=_still(lambda: showdown_states(cards, called=False)),
        )
