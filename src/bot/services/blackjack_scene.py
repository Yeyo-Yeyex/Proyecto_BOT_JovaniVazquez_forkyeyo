"""El blackjack con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/blackjack/escena.html`) pinta el tapete (con skins), el zapato y la
bandeja de descartes, las cartas (con su vuelo y su giro), las fichas, los totales, los
carteles, las chispas y la racha. Python decide todo lo que se ve y la escena solo pinta.

Cada jugada es un GIF que va de cómo estaba la mesa a cómo queda (`step_states`): se
comparan la mano de antes y la de después y se anima lo que ha cambiado, en el orden en
que pasa en una mesa de verdad:

1. **Repartir** (sin mano de antes o con la anterior ya pagada): las cartas de la mano
   anterior van a la bandeja, el zapato se baraja si toca (carta de corte o jefe de
   sala), entra la ficha y salen tu carta, la de la banca, la tuya y la tapada. Si la
   banca enseña un as, la mesa espera el seguro; si enseña un 10 o un as, mira la tapada.
2. **Seguro**: la ficha del seguro y la banca mira la tapada.
3. **Tu jugada**: separar (las dos cartas se apartan y cada una recibe otra), doblar (la
   segunda ficha y una carta de lado) o pedir.
4. **La banca**, si acaba tu turno: destapa y roba carta a carta, con suspense.
5. **El resultado**, si la mano acaba: cada mano con lo suyo, las fichas que se van a la
   banca o llegan de ella, el cartel con el importe que sube, chispas y la racha.

El último fotograma de cada jugada es el primero de la siguiente, así que las animaciones
encadenan sin saltos, y es también el PNG que se queda en el mensaje (`still_state`).

**Para quien cuenta cartas**, la mesa enseña lo mínimo de un casino: la bandeja de
descartes crece con lo que ha salido (con una muesca por baraja, sin números) y el zapato
mengua, con la carta de corte roja dentro hasta que sale. Nada más: ni cuenta ni barajas
que quedan. Las cuentas son de quien juega.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los fotogramas en
   RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`CardRenderer`), si tampoco hay navegador: solo la imagen final, sin GIF.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el siguiente. El
juego nunca se queda sin imagen. Cada jugada depende solo de la mano, así que el cog la
pinta antes del clic (`cogs.blackjack.BlackjackTable.prepare`).
"""

from __future__ import annotations

import asyncio
import io
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.blackjack import (
    CUT_CARD,
    DECKS,
    BlackjackGame,
    Card,
    Hand,
    Result,
    hand_total,
    is_blackjack,
)
from bot.services.browser_scene import BrowserScene
from bot.services.cards_render import CardRenderer, HandView
from bot.services.node_scene import NodeScene, patch_png
from bot.utils.gif import local_palette_gif

SCENE = Path(__file__).resolve().parent.parent / "assets" / "blackjack" / "escena.html"

# Estas medidas las usa también la escena: si cambian aquí, cambian allí.
W, H = 640, 420
#: Cartas de un zapato entero.
SHOE_CARDS = DECKS * 52
#: Por dónde salen las cartas del zapato (arriba a la derecha) y entran en la bandeja.
SHOE_XY = (556.0, 50.0)
TRAY_XY = (84.0, 40.0)
#: De dónde salen y adónde van las fichas de la banca.
BANK_XY = (320.0, 24.0)
DEALER_Y, PLAYER_Y = 104.0, 300.0
DEALER_STEP = 28.0
HAND_STEP = 24.0
#: Centro de cada mano: una sola, o dos tras separar (más pequeñas).
HAND_X = {1: (320.0,), 2: (196.0, 444.0)}
HAND_SCALE = {1: 1.0, 2: 0.9}
DEALER_PILL_Y, HAND_PILL_Y = 178.0, 232.0
CHIP_Y = 392.0
CHIP_GAP = 38.0
INSURANCE_XY = (482.0, 210.0)
#: Cuánto se aparta (x, y) la carta de doblar, que va de lado.
SIDEWAYS = math.pi / 2
SIDEWAYS_SHIFT = (14.0, 6.0)

FLY_FRAMES = 5
FLIP_FRAMES = 3
FLY_MS = 30
FLIP_MS = 38
#: Pausa antes de cada carta de la banca: el suspense, con fotogramas largos.
SUSPENSE_MS = 260
FINAL_FRAME_MS = 60_000


@dataclass(frozen=True, slots=True)
class Media:
    """Lo que el cog sube: el GIF de la jugada, el PNG final y cuánto dura el GIF.

    Attributes:
        gif: El GIF; `None` en el plan B de Pillow (solo imagen fija).
        png: El último fotograma.
        seconds: Lo que dura el GIF hasta el último fotograma (0 sin GIF).
    """

    gif: bytes | None
    png: bytes
    seconds: float


@dataclass(frozen=True, slots=True)
class Shoe:
    """Lo que pasa con el zapato al repartir una mano.

    Attributes:
        left: Cartas que quedaban en el zapato al acabar la mano anterior (antes de
            barajar, si se baraja).
        shuffle: `None`, `"cut"` (salió la carta de corte) o `"boss"` (el jefe de sala).
    """

    left: int
    shuffle: str | None = None


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


def encode(frames: Sequence[Image.Image], frame_ms: Sequence[int], *, loop: bool) -> Media:
    """GIF y PNG del último fotograma.

    Args:
        loop: Si el GIF se repite. Las jugadas que dejan la mano abierta no se repiten:
            el GIF se queda en el mensaje (no se cambia por el PNG) y no debe volver a
            empezar mientras el jugador piensa.
    """
    gif = local_palette_gif(list(frames), list(frame_ms), loop=loop)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=sum(frame_ms[:-1]) / 1000)


def durations(states: Sequence[dict[str, Any]]) -> list[int]:
    """Milisegundos de cada fotograma; el último se queda quieto un minuto."""
    return [int(s["ms"]) for s in states[:-1]] + [FINAL_FRAME_MS]


# -- Estados: lo que lee la escena ---------------------------------------------------------------


def money(amount: int) -> str:
    """`12.500`, con puntos."""
    return f"{amount:,}".replace(",", ".")


def short(amount: int) -> str:
    """Cifra corta para la ficha: `100`, `2,5k`, `1M`."""
    for unit, suffix in ((1_000_000, "M"), (1_000, "k")):
        if amount >= unit:
            text = f"{amount / unit:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            return text + suffix
    return str(amount)


def signed(amount: int) -> str:
    """`+500`, `-1.000` o `0`."""
    return ("+" if amount > 0 else "-" if amount < 0 else "") + money(abs(amount))


def meta_state(skin: dict[str, object] | None) -> dict[str, Any]:
    """Lo fijo de la escena: el tapete del jugador y las medidas del zapato."""
    return {"skin": skin, "cards": SHOE_CARDS, "cut": CUT_CARD, "decks": DECKS}


def _card(card: Card | None) -> dict[str, Any] | None:
    if card is None:
        return None
    return {"rank": card.rank, "suit": card.suit, "label": card.label, "red": card.is_red}


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _c(
    card: Card | None,
    x: float,
    y: float,
    *,
    scale: float = 1.0,
    flip: float = 0.0,
    rot: float = 0.0,
    lift: float = 0.0,
    glow: float = 0.0,
    dim: float = 0.0,
) -> dict[str, Any]:
    return {
        "card": _card(card),
        "x": round(x, 2),
        "y": round(y, 2),
        "rot": round(rot, 4),
        "scale": round(scale, 3),
        "flip": round(flip, 3),
        "lift": round(lift, 2),
        "glow": round(glow, 3),
        "dim": round(dim, 3),
    }


def _chip(x: float, y: float, stake: int, a: float, *, gold: bool = False) -> dict[str, Any]:
    return {
        "x": round(x, 2),
        "y": round(y, 2),
        "stake": stake,
        "chip": short(stake),
        "a": round(max(0.0, min(1.0, a)), 3),
        "gold": gold,
    }


def dealer_x(index: float, count: float) -> float:
    """Dónde va la carta `index` de la banca con `count` cartas en la mesa (en abanico)."""
    return 320.0 - (count - 1) * DEALER_STEP / 2 + index * DEALER_STEP


def hand_x(center: float, index: float, count: float, scale: float) -> float:
    """Dónde va la carta `index` de una mano de `count` cartas centrada en `center`."""
    step = HAND_STEP * scale
    return center - (count - 1) * step / 2 + index * step


# Cómo se ve cada desenlace de una mano: (clase de la etiqueta, texto).
RESULT_LOOK: dict[Result, tuple[str, str]] = {
    Result.BLACKJACK: ("bj", "BLACKJACK"),
    Result.WIN: ("win", "GANA"),
    Result.PUSH: ("push", "EMPATE"),
    Result.LOSE: ("lose", "PIERDE"),
    Result.BUST: ("bust", "SE PASA"),
}

INSURANCE_BANNER = {"kind": "info", "title": "¿SEGURO?", "sub": "PAGA 2:1", "icon": "shield"}
NO_BJ_BANNER = {"kind": "info", "title": "SIN BLACKJACK", "sub": None, "icon": None}
SHUFFLE_BANNERS = {
    "cut": {"kind": "shuffle", "title": "ZAPATO NUEVO", "sub": "CARTA DE CORTE", "icon": None},
    "boss": {"kind": "boss", "title": "EL JEFE DE SALA BARAJA", "sub": None, "icon": "eye"},
}


@dataclass(slots=True)
class _HandLook:
    """Lo que se ve de una mano del jugador en un fotograma."""

    cards: list[Card]
    stake: int
    doubled: bool = False
    result: Result | None = None
    #: Cartas que ocupa la mano al colocarse (sube poco a poco mientras llega una).
    spread: float | None = None
    #: La ficha (0-1 mientras entra), la segunda al doblar (0-1 mientras cae) y lo que
    #: se va a la banca al perder (0-1).
    bet_a: float = 1.0
    double_a: float = 0.0
    lost: float = 0.0
    #: El premio que llega de la banca (0-1) y lo que se cobra (apuesta incluida).
    prize_a: float = 0.0
    prize: int = 0
    #: Brillo de las cartas ganadoras y velo de las perdedoras (0-1).
    glow: float = 0.0
    dim: float = 0.0
    #: Escala de la etiqueta del total (el «pop» del resultado).
    pop: float = 1.0

    @property
    def base(self) -> int:
        """La ficha de cada montón (al doblar, dos iguales)."""
        return self.stake // 2 if self.doubled else self.stake


class _Reel:
    """Los fotogramas de una jugada y lo que se ve de la mesa en cada momento.

    El estado de la mesa (cartas, fichas, etiquetas, zapato) vive aquí; cada animación lo
    cambia poco a poco y `add` saca un fotograma de cómo está. Lo que vuela (una carta
    del zapato a la mano) va aparte, encima de todo.
    """

    def __init__(self, *, left: int, streak: int) -> None:
        self.left = left
        self.tray = 0
        self.streak = streak
        self.streak_pop = 1.0
        #: Cartas de la banca ya en la mesa y cuántas van boca arriba (la tapada es la 2.ª).
        self.dealer: list[Card] = []
        self.dealer_shown = 0
        self.dealer_spread: float | None = None
        #: La tapada: cuánto se levanta al mirarla (0-1) y si no se pinta (se está volteando).
        self.peek = 0.0
        self.hole_hidden = False
        self.hands: list[_HandLook] = []
        self.active: int | None = None
        #: Posición de cada mano mientras se separan (si no, la de su número de manos).
        self.centers: list[float] | None = None
        self.scale = 1.0
        self.insurance = 0
        self.insurance_state = "off"
        self.insurance_a = 0.0
        self.banner: dict[str, Any] | None = None
        self.labels_a = 1.0
        self.frames: list[dict[str, Any]] = []

    # -- Lo que se ve --------------------------------------------------------------------

    def _centers(self) -> list[float]:
        if self.centers is not None:
            return self.centers
        return list(HAND_X[max(1, min(2, len(self.hands)))])

    def _dealer_cards(self) -> list[dict[str, Any]]:
        out = []
        count = self.dealer_spread if self.dealer_spread is not None else len(self.dealer)
        for i, card in enumerate(self.dealer):
            hole = i == 1 and self.dealer_shown < 2
            if hole and self.hole_hidden:
                continue
            up = i < self.dealer_shown
            out.append(
                _c(
                    card if up else None,
                    dealer_x(i, count),
                    DEALER_Y - (8 * self.peek if hole else 0),
                    flip=1 if up else 0,
                    rot=(-0.06 * self.peek) if hole else 0.0,
                )
            )
        return out

    def _hand_cards(self) -> list[dict[str, Any]]:
        out = []
        centers = self._centers()
        for h, look in enumerate(self.hands):
            count = look.spread if look.spread is not None else len(look.cards)
            for i, card in enumerate(look.cards):
                sideways = look.doubled and i == 2
                x = hand_x(centers[h], i, count, self.scale)
                y = PLAYER_Y
                if sideways:
                    x, y = x + SIDEWAYS_SHIFT[0] * self.scale, y + SIDEWAYS_SHIFT[1]
                out.append(
                    _c(
                        card,
                        x,
                        y,
                        scale=self.scale,
                        flip=1,
                        rot=SIDEWAYS if sideways else 0.0,
                        glow=look.glow,
                        dim=look.dim,
                    )
                )
        return out

    def _labels(self) -> list[dict[str, Any]]:
        out = []
        if self.dealer:
            visible = self.dealer[: max(1, self.dealer_shown)]
            total = hand_total(visible)[0]
            if self.dealer_shown < 2:
                kind, text = "hidden", str(total)
            elif len(self.dealer) == 2 and is_blackjack(self.dealer):
                kind, text = "bj", "BLACKJACK"
            elif total > 21:
                kind, text = "bust", f"{total} · SE PASA"
            else:
                kind, text = "dealer", str(total)
            out.append(
                {"x": 320.0, "y": DEALER_PILL_Y, "text": text, "kind": kind, "a": self.labels_a}
            )
        centers = self._centers()
        for h, look in enumerate(self.hands):
            if not look.cards:
                continue
            total = hand_total(look.cards)[0]
            if look.result is not None:
                kind, word = RESULT_LOOK[look.result]
                net = look.prize - look.stake
                text = word if look.result is Result.BLACKJACK else f"{total} · {word}"
                if net > 0:
                    text += f" +{money(net)}"
            elif total > 21:
                kind, text = "bust", f"{total} · SE PASA"
            elif self.active == h:
                kind, text = "active", str(total)
            else:
                kind, text = "hand", str(total)
            out.append(
                {
                    "x": round(centers[h], 2),
                    "y": HAND_PILL_Y,
                    "text": text,
                    "kind": kind,
                    "a": round(self.labels_a * look.pop, 3),
                }
            )
        return out

    def _chips(self) -> list[dict[str, Any]]:
        out = []
        centers = self._centers()
        for h, look in enumerate(self.hands):
            x = centers[h] - CHIP_GAP / 2
            spots = [(x, CHIP_Y, look.bet_a)]
            if look.doubled:
                drop = 40 * (1 - look.double_a)
                spots.append((x + CHIP_GAP * 0.62, CHIP_Y + drop, look.double_a))
            for sx, sy, a in spots:
                if look.lost > 0:
                    t = _ease(look.lost)
                    sx, sy, a = _lerp(sx, BANK_XY[0], t), _lerp(sy, BANK_XY[1], t), a * (1 - t)
                if a > 0:
                    out.append(_chip(sx, sy, look.base, a))
            won = look.prize - look.stake
            if look.prize_a > 0 and won > 0:
                t = _ease(look.prize_a)
                end = x + CHIP_GAP * (1.4 if look.doubled else 1.0)
                out.append(
                    _chip(
                        _lerp(BANK_XY[0], end, t),
                        _lerp(BANK_XY[1], CHIP_Y, t),
                        won,
                        min(1.0, look.prize_a * 1.6),
                        gold=look.result is Result.BLACKJACK,
                    )
                )
        if self.insurance and self.insurance_a > 0:
            x, y = INSURANCE_XY
            if self.insurance_state == "lose":
                t = _ease(1 - self.insurance_a)
                x, y = _lerp(x, BANK_XY[0], t), _lerp(y, BANK_XY[1], t)
            out.append(_chip(x, y, self.insurance, self.insurance_a))
            if self.insurance_state == "win":
                out.append(_chip(x + CHIP_GAP, y, self.insurance * 2, self.insurance_a))
        return out

    def add(
        self,
        ms: int,
        flying: Sequence[dict[str, Any]] = (),
        sparks: list | None = None,
        *,
        chips: Sequence[dict[str, Any]] | None = None,
        labels: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        """Un fotograma de la mesa tal como está, con `flying` por encima de las cartas.

        `chips` y `labels` sustituyen a las de la mesa (al recogerla).
        """
        self.frames.append(
            {
                "cards": [*self._dealer_cards(), *self._hand_cards(), *flying],
                "labels": list(labels) if labels is not None else self._labels(),
                "chips": list(chips) if chips is not None else self._chips(),
                "shoe": {"left": self.left, "tray": self.tray},
                "streak": {"n": self.streak, "a": round(self.streak_pop, 3)},
                "banner": self.banner,
                "sparks": sparks or [],
                "ms": ms,
            }
        )

    def hold(self, ms: int) -> None:
        """Alarga el último fotograma (una pausa sin fotogramas de más)."""
        self.frames[-1]["ms"] += ms

    def banner_at(self, spec: dict[str, Any] | None, a: float) -> None:
        self.banner = None if spec is None or a <= 0 else {**spec, "a": round(a, 3)}

    def fly(
        self,
        card: Card | None,
        dest: tuple[float, float],
        *,
        scale: float = 1.0,
        rot: float = 0.0,
        face_up: bool = True,
        during: Callable[[float], None],
        land: Callable[[], None],
    ) -> None:
        """Una carta del zapato a `dest` y, si va boca arriba, se voltea al llegar.

        `during(e)` va haciendo hueco en la mesa mientras vuela (e de 0 a 1) y `land` la
        deja en la mesa: el último fotograma ya es la mesa con la carta puesta.
        """
        self.left -= 1
        flight = FLY_FRAMES if face_up else FLY_FRAMES - 1
        for i in range(flight):
            e = _ease((i + 1) / FLY_FRAMES)
            during(e)
            moving = _c(
                card,
                _lerp(SHOE_XY[0], dest[0], e),
                _lerp(SHOE_XY[1], dest[1], e) - 40 * math.sin(math.pi * e),
                scale=_lerp(0.5, scale, e),
                rot=_lerp(0.8, rot, e),
            )
            self.add(FLY_MS, [moving])
        if face_up:
            for i in range(FLIP_FRAMES - 1):
                t = (i + 1) / FLIP_FRAMES
                turning = _c(
                    card, *dest, scale=scale, rot=rot, flip=t, lift=18 * math.sin(math.pi * t)
                )
                self.add(FLIP_MS, [turning])
        land()
        self.add(FLIP_MS if face_up else FLY_MS)


def _seed(game: BlackjackGame) -> int:
    cards = [*game.dealer, *(c for hand in game.hands for c in hand.cards)]
    return sum((i + 1) * (c.rank * 4 + c.suit) for i, c in enumerate(cards)) + len(cards)


def _sparks(seed: int, k: int, n: int, *, gold: bool) -> list[list[float]]:
    """Chispas del premio en el paso `k` de `n`, desde el cartel: misma semilla, mismas."""
    rng = random.Random(seed)
    t = (k + 1) / n
    out = []
    for _ in range(22 if gold else 16):
        angle = rng.uniform(0, math.tau)
        speed = rng.uniform(40, 130)
        out.append(
            [
                round(320 + math.cos(angle) * speed * t, 1),
                round(205 + math.sin(angle) * speed * t * 0.7 + 22 * t * t, 1),
                round(rng.uniform(3.5, 7.5) * (1 - 0.5 * t), 1),
                round(1 - 0.85 * t, 2),
                rng.choice((0, 0, 0, 2) if gold else (0, 0, 2, 1)),
            ]
        )
    return out


# -- La mesa tal como está ------------------------------------------------------------------------


def _on_table(game: BlackjackGame) -> int:
    return len(game.dealer) + sum(len(h.cards) for h in game.hands)


def outcome_banner(game: BlackjackGame) -> dict[str, Any]:
    """El cartel del resultado de una mano pagada: titular y lo ganado o perdido."""
    net = game.net
    results = {hand.result for hand in game.hands}
    if game.insurance_paid:
        kind, title = "push", "EL SEGURO TE CUBRE"
    elif net > 0 and results == {Result.BLACKJACK}:
        kind, title = "bj", "¡BLACKJACK!"
    elif net > 0:
        kind = "win"
        title = "¡LA BANCA SE PASA!" if game.dealer_total > 21 else "¡GANAS!"
    elif net == 0:
        kind, title = "push", "EMPATE"
    elif results == {Result.BUST}:
        kind, title = "lose", "TE PASAS"
    elif is_blackjack(game.dealer):
        kind, title = "lose", "BLACKJACK DE LA BANCA"
    else:
        kind, title = "lose", "GANA LA BANCA"
    return {"kind": kind, "title": title, "sub": signed(net) if net else None, "icon": None}


def _load(reel: _Reel, game: BlackjackGame) -> None:
    """Pone la mesa como está la mano `game` (sin animar): lo que se ve al empezar una jugada."""
    reel.dealer = list(game.dealer)
    reel.dealer_shown = len(game.dealer) if game.hole_revealed else min(1, len(game.dealer))
    reel.hands = [
        _HandLook(
            cards=list(h.cards),
            stake=h.stake,
            doubled=h.doubled,
            double_a=1.0 if h.doubled else 0.0,
        )
        for h in game.hands
    ]
    reel.scale = HAND_SCALE[max(1, min(2, len(game.hands)))]
    reel.active = game.active if game.player_turn and len(game.hands) > 1 else None
    reel.insurance = game.insurance
    reel.insurance_state = "on" if game.insurance else "off"
    reel.insurance_a = 1.0 if game.insurance else 0.0
    reel.tray = SHOE_CARDS - reel.left - _on_table(game)
    if game.insurance_pending:
        reel.banner_at(INSURANCE_BANNER, 1.0)
    if game.settled:
        _settled_look(reel, game)


def _settled_look(reel: _Reel, game: BlackjackGame) -> None:
    """La mesa de una mano pagada, quieta: el último fotograma del resultado."""
    reel.dealer_shown = len(game.dealer)
    reel.peek = 0.0
    reel.active = None
    for look, hand in zip(reel.hands, game.hands, strict=True):
        look.result = hand.result
        look.prize = hand.payout()
        good = hand.result in (Result.WIN, Result.BLACKJACK)
        bad = hand.result in (Result.LOSE, Result.BUST)
        look.glow = 0.7 if good else 0.0
        look.dim = 0.4 if bad else 0.0
        look.lost = 1.0 if bad else 0.0
        look.prize_a = 1.0 if good else 0.0
        look.pop = 1.0
    if game.insurance:
        reel.insurance_state = "win" if game.insurance_paid else "lose"
        reel.insurance_a = 1.0 if game.insurance_paid else 0.0
    reel.banner_at(outcome_banner(game), 1.0)


# -- Animaciones -------------------------------------------------------------------------------


def _deal_to_hand(reel: _Reel, h: int, card: Card, *, sideways: bool = False) -> None:
    """Una carta del zapato a la mano `h`; la mano se aparta mientras llega."""
    look = reel.hands[h]
    count = len(look.cards) + 1
    x = hand_x(reel._centers()[h], count - 1, count, reel.scale)
    y = PLAYER_Y
    if sideways:
        x, y = x + SIDEWAYS_SHIFT[0] * reel.scale, y + SIDEWAYS_SHIFT[1]

    def during(e: float) -> None:
        look.spread = count - 1 + e if look.cards else None

    def land() -> None:
        look.cards.append(card)
        look.spread = None

    reel.fly(
        card,
        (x, y),
        scale=reel.scale,
        rot=SIDEWAYS if sideways else 0.0,
        during=during,
        land=land,
    )


def _deal_to_dealer(reel: _Reel, card: Card, *, face_up: bool) -> None:
    """Una carta del zapato a la banca (boca abajo, la tapada)."""
    count = len(reel.dealer) + 1

    def during(e: float) -> None:
        reel.dealer_spread = count - 1 + e if reel.dealer else None

    def land() -> None:
        reel.dealer.append(card)
        reel.dealer_spread = None
        if face_up:
            reel.dealer_shown = len(reel.dealer)

    reel.fly(
        card, (dealer_x(count - 1, count), DEALER_Y), face_up=face_up, during=during, land=land
    )


def _reveal_hole(reel: _Reel) -> None:
    """La banca destapa su segunda carta."""
    hole = reel.dealer[1]
    x = dealer_x(1, len(reel.dealer))
    reel.peek = 0.0
    reel.hole_hidden = True
    for i in range(FLIP_FRAMES):
        t = (i + 1) / (FLIP_FRAMES + 1)
        reel.add(FLIP_MS, [_c(hole, x, DEALER_Y, flip=t, lift=20 * math.sin(math.pi * t))])
    reel.hole_hidden = False
    reel.dealer_shown = len(reel.dealer)
    reel.add(FLIP_MS)


def _peek(reel: _Reel) -> None:
    """La banca levanta la esquina de la tapada para mirar si tiene blackjack."""
    for p in (0.5, 1.0, 1.0, 0.4):
        reel.peek = p
        reel.add(45)
    reel.peek = 0.0
    reel.add(45)


def _pop_banner(reel: _Reel, spec: dict[str, Any], *, stay: bool) -> None:
    """Un cartel que salta y, si no se queda (`stay`), se va tras una pausa."""
    for a in (0.4, 0.85, 1.1, 1.0):
        reel.banner_at(spec, a)
        reel.add(40)
    if not stay:
        reel.hold(280)
        _fade_banner(reel)


def _fade_banner(reel: _Reel) -> None:
    if reel.banner is None:
        return
    spec = {k: v for k, v in reel.banner.items() if k != "a"}
    for a in (0.6, 0.25):
        reel.banner_at(spec, a)
        reel.add(35)
    reel.banner_at(None, 0)


def _sweep(reel: _Reel) -> None:
    """Las cartas de la mano anterior van boca abajo a la bandeja y se recogen las fichas."""
    _fade_banner(reel)
    cards = [*reel._dealer_cards(), *reel._hand_cards()]
    chips = reel._chips()
    labels = reel._labels()
    reel.dealer, reel.dealer_shown, reel.hands = [], 0, []
    reel.insurance, reel.insurance_state, reel.insurance_a = 0, "off", 0.0
    start, total = reel.tray, len(cards)
    steps = 6
    for k in range(steps):
        t = (k + 1) / steps
        flying = []
        landed = 0
        for j, c in enumerate(cards):
            # Se recogen una tras otra: la primera sale antes.
            raw = min(1.0, max(0.0, t * 1.4 - 0.4 * j / max(1, total)))
            landed += raw >= 1.0
            lag = _ease(raw)
            flying.append(
                {
                    **c,
                    "x": round(_lerp(c["x"], TRAY_XY[0], lag), 2),
                    "y": round(_lerp(c["y"], TRAY_XY[1], lag), 2),
                    "scale": round(_lerp(c["scale"], 0.4, lag), 3),
                    "rot": round(_lerp(c["rot"], -SIDEWAYS, lag), 4),
                    "flip": round(c["flip"] * (1 - min(1.0, lag * 2)), 3),
                    "glow": 0.0,
                    "dim": 0.0,
                }
            )
        reel.tray = start + landed
        fading = [
            {**c, "y": round(c["y"] + 18 * t, 2), "a": round(c["a"] * (1 - t), 3)} for c in chips
        ]
        faded = [{**lab, "a": round(lab["a"] * (1 - t), 3)} for lab in labels]
        last = k == steps - 1
        reel.add(
            32, [] if last else flying, chips=[] if last else fading, labels=[] if last else faded
        )
    reel.tray = start + total
    reel.frames[-1]["shoe"]["tray"] = reel.tray


def _shuffle(reel: _Reel, why: str) -> None:
    """La bandeja vuelve al zapato y se baraja (carta de corte o jefe de sala)."""
    spec = SHUFFLE_BANNERS[why]
    tray, left = reel.tray, reel.left
    steps = 7
    for k in range(steps):
        t = (k + 1) / steps
        e = _ease(t)
        reel.tray = round(tray * (1 - e))
        reel.left = round(left + (SHOE_CARDS - left) * e)
        reel.banner_at(spec, (0.4, 0.85, 1.1, 1.0, 1.0, 1.0, 1.0)[k])
        flying = []
        if k < steps - 1:
            for j in range(3):
                u = (t * 2 + j * 0.33) % 1
                flying.append(
                    _c(
                        None,
                        _lerp(TRAY_XY[0], SHOE_XY[0], u),
                        _lerp(TRAY_XY[1], SHOE_XY[1], u) - 34 * math.sin(math.pi * u),
                        scale=0.42,
                        rot=-0.6 + 1.2 * u,
                    )
                )
        reel.add(40, flying)
    reel.hold(360)
    _fade_banner(reel)


def _place_bet(reel: _Reel, stake: int) -> None:
    """La ficha entra en su sitio."""
    look = _HandLook(cards=[], stake=stake, bet_a=0.0)
    reel.hands = [look]
    reel.scale = HAND_SCALE[1]
    for k in range(3):
        look.bet_a = _ease((k + 1) / 3)
        reel.add(32)


def _split(reel: _Reel, game: BlackjackGame) -> None:
    """Las dos cartas se apartan, cada una a su mano, entra la segunda ficha y se reparten."""
    first, second = reel.hands[0].cards
    stake = reel.hands[0].stake
    reel.hands = [
        _HandLook(cards=[first], stake=stake),
        _HandLook(cards=[second], stake=stake, bet_a=0.0),
    ]
    start = [hand_x(HAND_X[1][0], 0, 2, 1.0), hand_x(HAND_X[1][0], 1, 2, 1.0)]
    end = HAND_X[2]
    steps = 5
    for k in range(steps):
        t = _ease((k + 1) / steps)
        reel.centers = [_lerp(start[0], end[0], t), _lerp(start[1], end[1], t)]
        reel.scale = _lerp(HAND_SCALE[1], HAND_SCALE[2], t)
        reel.hands[1].bet_a = t
        reel.add(34)
    reel.centers = None
    reel.scale = HAND_SCALE[2]
    reel.active = 0
    for h, hand in enumerate(game.hands):
        _deal_to_hand(reel, h, hand.cards[1])


def _double(reel: _Reel, h: int) -> None:
    """La segunda ficha, igual que la primera, cae a su lado."""
    look = reel.hands[h]
    look.stake, look.doubled = look.stake * 2, True
    for k in range(3):
        look.double_a = _ease((k + 1) / 3)
        reel.add(32)


def _dealer_turn(reel: _Reel, hole_was_up: bool, count_before: int, game: BlackjackGame) -> None:
    """La banca destapa (si no lo estaba) y roba carta a carta, con suspense."""
    reel.active = None
    if not hole_was_up and game.hole_revealed:
        reel.hold(160)
        _reveal_hole(reel)
    for card in game.dealer[count_before:]:
        reel.hold(SUSPENSE_MS)
        _deal_to_dealer(reel, card, face_up=True)


def _settle(reel: _Reel, game: BlackjackGame, streak_after: int) -> None:
    """El resultado: etiquetas, fichas que se van o llegan, el cartel y las chispas."""
    reel.hold(140)
    spec = outcome_banner(game)
    net = game.net
    gold = spec["kind"] == "bj"
    pops = (0.3, 0.7, 1.12, 1.0, 1.0, 1.0)
    labels = (0.8, 1.18, 1.0, 1.0, 1.0, 1.0)
    reel.active = None
    for look, hand in zip(reel.hands, game.hands, strict=True):
        look.result = hand.result
        look.prize = hand.payout()
    steps = len(pops)
    for k in range(steps):
        e = _ease(min(1.0, (k + 1) / 4))
        for look in reel.hands:
            win = look.result in (Result.WIN, Result.BLACKJACK)
            bad = look.result in (Result.LOSE, Result.BUST)
            look.glow = 0.7 * e if win else 0.0
            look.dim = 0.4 * e if bad else 0.0
            look.lost = e if bad else 0.0
            look.prize_a = e if win else 0.0
            look.pop = labels[k]
        if game.insurance:
            reel.insurance_state = "win" if game.insurance_paid else "lose"
            reel.insurance_a = 1.0 if game.insurance_paid else 1 - e
        # El importe del cartel sube hasta lo ganado (o baja hasta lo perdido).
        counted = round(net * _ease(min(1.0, (k + 1) / (steps - 1))))
        reel.banner_at({**spec, "sub": signed(counted) if net else None}, pops[k])
        reel.streak_pop = 1.0
        if k == 2 and streak_after != reel.streak:
            reel.streak, reel.streak_pop = streak_after, 1.25
        sparks = _sparks(_seed(game), k, steps - 1, gold=gold) if net > 0 and k < steps - 1 else []
        reel.add(40, sparks=sparks)
    reel.streak, reel.streak_pop = streak_after, 1.0
    _settled_look(reel, game)
    reel.add(40)


# -- Una jugada entera --------------------------------------------------------------------------


def step_states(
    before: BlackjackGame | None,
    after: BlackjackGame,
    *,
    shoe: Shoe | None = None,
    streak: tuple[int, int] = (0, 0),
) -> list[dict[str, Any]]:
    """Los fotogramas de una jugada: de cómo estaba la mesa (`before`) a cómo queda.

    Args:
        before: La mano antes de la jugada (una copia); `None` o una mano ya pagada si la
            jugada es repartir otra (entonces es la que se recoge a la bandeja).
        after: La mano después de la jugada, con la banca ya jugada y pagada si acabó.
        shoe: Al repartir tras otra mano, lo que pasó con el zapato (`Shoe`); si no se
            da, no se ha barajado.
        streak: La racha antes y después de la jugada (cambia si la mano acaba).

    Returns:
        Un estado por fotograma (ver la cabecera de la escena); el último dura un minuto
        y es la mesa como queda.
    """
    dealing = before is None or before.settled
    if dealing and before is not None and shoe is not None:
        left = shoe.left
    elif dealing:
        left = len(after.shoe) + _on_table(after)
    else:
        assert before is not None
        left = len(after.shoe) + _on_table(after) - _on_table(before)
    reel = _Reel(left=left, streak=streak[0])
    if dealing:
        _deal_steps(reel, before, after, shoe)
    else:
        assert before is not None
        _load(reel, before)
        reel.add(60)
        if before.insurance_pending:
            _insure_steps(reel, after)
        else:
            _act_steps(reel, before, after)
    if after.settled:
        _settle(reel, after, streak[1])
    reel.frames[-1]["ms"] = FINAL_FRAME_MS
    return reel.frames


def _deal_steps(
    reel: _Reel, prev: BlackjackGame | None, game: BlackjackGame, shoe: Shoe | None
) -> None:
    """Recoger la mano anterior, barajar si toca, la ficha y las cuatro cartas."""
    if prev is not None:
        _load(reel, prev)
        reel.add(120)
        _sweep(reel)
        if shoe is not None and shoe.shuffle:
            _shuffle(reel, shoe.shuffle)
    else:
        reel.tray = SHOE_CARDS - reel.left
        reel.add(160)
    _place_bet(reel, game.stake)
    (p0, p1), (d0, d1) = game.hands[0].cards[:2], game.dealer[:2]
    _deal_to_hand(reel, 0, p0)
    _deal_to_dealer(reel, d0, face_up=True)
    _deal_to_hand(reel, 0, p1)
    _deal_to_dealer(reel, d1, face_up=False)
    if game.insurance_pending:
        reel.hold(120)
        _pop_banner(reel, INSURANCE_BANNER, stay=True)
        return
    _after_peek(reel, game)


def _after_peek(reel: _Reel, game: BlackjackGame) -> None:
    """La banca mira la tapada (si enseña un as o un 10) y la destapa si se acaba la mano."""
    peeks = game.dealer[0].value in (1, 10)
    if peeks:
        reel.hold(120)
        _peek(reel)
    if game.hole_revealed:
        _dealer_turn(reel, False, 2, game)
    elif peeks:
        _pop_banner(reel, NO_BJ_BANNER, stay=False)


def _insure_steps(reel: _Reel, after: BlackjackGame) -> None:
    """La ficha del seguro (si se toma), la banca mira y la mano sigue o se acaba."""
    if after.insurance:
        reel.insurance, reel.insurance_state = after.insurance, "on"
        for k in range(3):
            reel.insurance_a = _ease((k + 1) / 3)
            reel.add(34)
    _fade_banner(reel)
    _after_peek(reel, after)


def _act_steps(reel: _Reel, before: BlackjackGame, after: BlackjackGame) -> None:
    """Tu jugada (separar, doblar o pedir) y, si acaba tu turno, la de la banca."""
    if len(before.hands) == 1 and len(after.hands) == 2:
        _split(reel, after)
    else:
        for h, (old, new) in enumerate(zip(before.hands, after.hands, strict=True)):
            if new.doubled and not old.doubled:
                _double(reel, h)
            for card in new.cards[len(old.cards) :]:
                _deal_to_hand(reel, h, card, sideways=new.doubled)
    if after.player_turn:
        reel.active = after.active if len(after.hands) > 1 else None
        reel.add(40)
        return
    _dealer_turn(reel, before.hole_revealed, len(before.dealer), after)


def still_state(game: BlackjackGame, *, streak: int = 0) -> dict[str, Any]:
    """El fotograma de la mesa como está la mano, sin animar (`full`)."""
    reel = _Reel(left=len(game.shoe), streak=streak)
    _load(reel, game)
    reel.add(FINAL_FRAME_MS)
    return {**reel.frames[-1], "full": True}


# -- Pillow: el dibujo de reserva --------------------------------------------------------------

RESULT_BADGES = {
    Result.BLACKJACK: "BLACKJACK",
    Result.WIN: "GANA",
    Result.PUSH: "EMPATE",
    Result.LOSE: "PIERDE",
    Result.BUST: "SE PASA",
}


def hand_views(game: BlackjackGame) -> list[HandView]:
    """Manos del jugador listas para el dibujo de Pillow."""
    several = len(game.hands) > 1
    return [_hand_view(game, i, hand, several) for i, hand in enumerate(game.hands)]


def _hand_view(game: BlackjackGame, index: int, hand: Hand, several: bool) -> HandView:
    name = f"MANO {index + 1}" if several else "TÚ"
    doubled = " ×2" if hand.doubled else ""
    badge = None
    good = False
    if hand.result is not None:
        net = hand.payout() - hand.stake
        badge = RESULT_BADGES[hand.result]
        if net > 0:
            badge += " +" + money(net)
        good = net > 0
    return HandView(
        cards=hand.cards,
        title=f"{name} · {hand.total}{doubled}",
        active=game.player_turn and index == game.active,
        badge=badge,
        badge_good=good,
    )


def render_table(renderer: CardRenderer, game: BlackjackGame) -> bytes:
    """PNG de la mesa con Pillow (el plan C de la escena), en el estado actual de la mano."""
    hidden = not game.hole_revealed
    total = game.visible_dealer_total
    return renderer.render(
        dealer=game.dealer,
        hide_hole=hidden,
        dealer_title=f"BANCA · {total}{' + ?' if hidden else ''}",
        hands=hand_views(game),
    )


class BlackjackScene:
    """Dibuja el blackjack con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El `CardRenderer` de reserva.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: CardRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or CardRenderer()
        self.painter = NodeScene(SCENE, name="El pintor del blackjack", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador del blackjack",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya solo se dibuja con Pillow."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def _frames(
        self, meta: dict[str, Any], states: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """Recuadros de cada fotograma (ver `assemble`); `None` si no hay Node ni navegador."""
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def step(
        self,
        before: BlackjackGame | None,
        after: BlackjackGame,
        *,
        shoe: Shoe | None = None,
        streak: tuple[int, int] = (0, 0),
        skin: dict[str, object] | None = None,
    ) -> Media:
        """El GIF de una jugada (ver `step_states`) y el PNG de cómo queda la mesa.

        El GIF solo se repite si la mano acaba: entonces el cog lo cambia por el PNG
        cuando termina. Si la mano sigue, el GIF se queda en el mensaje y no se repite.
        """
        states = step_states(before, after, shoe=shoe, streak=streak)
        patches = await self._frames(meta_state(skin), states)
        if patches is None:
            png = await asyncio.to_thread(render_table, self.fallback, after)
            return Media(gif=None, png=png, seconds=0.0)
        loop = after.settled
        return await asyncio.to_thread(
            lambda: encode(assemble(patches), durations(states), loop=loop)
        )

    async def still(
        self, game: BlackjackGame, *, streak: int = 0, skin: dict[str, object] | None = None
    ) -> bytes:
        """El PNG de la mesa como está la mano, sin animar (al caducar la mesa)."""
        patches = await self._frames(meta_state(skin), [still_state(game, streak=streak)])
        if patches is None:
            return await asyncio.to_thread(render_table, self.fallback, game)
        return await asyncio.to_thread(patch_png, patches[0])
