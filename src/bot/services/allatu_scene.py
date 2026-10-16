"""Allá tú con canvas: en Node y, si no, en Chromium.

La escena (`assets/allatu/escena.html`) pinta el plató: las 22 cajas, tu caja en
su atril, el panel de premios con el gordo, la llamada de Perro Sanxe con su
oferta, la ruleta del gordo y el final a dos cajas. Python decide todo lo que se
ve en cada fotograma (las funciones `*_states` de aquí); la escena solo pinta.

**Vistas** (`view` de cada estado) y lo que lleva cada una:

- `board`: el plató quieto. `board` (ver `board_state`) y, si se funde, `fade`
  (0-1, negro encima).
- `wheel`: la ruleta del gordo al empezar. `tiers` (los gordos posibles, con su
  probabilidad), `pos` (posición del rodillo en casillas, con decimales: la
  casilla `round(pos) % len(tiers)` es la que queda en el centro), `glow`,
  `table`, `banner` y `fade`.
- `open`: una caja de cerca, que tiembla, se abre y enseña su premio. Solo
  depende de la caja y de lo que tiene (no de cuándo se abra), así que se pinta
  una vez por partida antes del primer clic. `n`, `v` (`×0,5`), `amount`
  (`50 Y$`), `tier`, `zoom`, `shake` ([dx, dy]), `glow`, `lid` (0-1), `rise`
  (0-1, la placa del premio que sube), `banner`, `flash` y `own` (si es tu caja,
  que lleva el cartel «TU CAJA»).
- `call`: la llamada de la banca. `board` (para el panel de premios), `label`
  (`OFERTA 2 DE 5`), `ring` (fotograma del timbre, o nada si ya ha cogido),
  `answer` (0-1, descuelga), `count` (0-1, el marcador que sube), `amount` (lo
  que marca ahora), `mult` (`×1,23`, al acabar de contar), `mood` (`rata`,
  `normal`, `generosa`), `stamp` (0-1, el sello del humor), `talk` (texto del
  bocadillo de Perro Sanxe), `hang` (0-1, cuelga), `coins` (0-1) y `banner`.
- `final`: las dos últimas cajas en sus atriles. `board`, `a` (tu caja de
  siempre) y `b` (la otra), cada una con `n`, `v`, `tier`, `lid`, `rise`;
  `swap` (0-1, se cruzan), `mine` (`a` o `b`: la que te llevas), `shake`,
  `banner`.

`banner` es `{text, sub, kind, a}` con `kind` en `good` (verde), `bad` (rojo),
`gordo` (oro con destellos), `deal` (oro), `hang` (gris) o `info` (azul). Cada
estado lleva además `ms` (lo que dura) y `t` (un reloj para focos y destellos).

La escena se pinta de dos maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.

Si tampoco hay navegador, cada método devuelve `None` y el cog sigue con el
tablero de texto: el juego nunca se queda sin mesa.
"""

from __future__ import annotations

import asyncio
import io
import math
import random
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.allatu import (
    BOXES,
    ROUNDS,
    AllatuGame,
    Ending,
    Phase,
    format_chance,
    format_multiplier,
    offer_mood,
    tier,
)
from bot.services.browser_scene import BrowserScene
from bot.services.economy import format_amount
from bot.services.node_scene import NodeScene, patch_png
from bot.utils.gif import local_palette_gif

SCENE = Path(__file__).resolve().parent.parent / "assets" / "allatu" / "escena.html"

#: Tamaño de la imagen (el de las demás escenas del casino).
W, H = 640, 360
#: El último fotograma del GIF se queda quieto (Discord repite los GIF).
FINAL_FRAME_MS = 60_000

#: Lo que grita el plató al abrir cada tipo de caja (se elige una por caja).
OPEN_BANNERS: dict[str, tuple[str, ...]] = {
    "gordo": ("¡ADIÓS AL GORDO!", "¡NOOO! ¡EL GORDO!", "¡SE VA EL GORDO!"),
    "rojo": ("¡AY, QUÉ DOLOR!", "¡SE VA UNA GORDA!", "¡MALA CAJA!"),
    "azul": ("¡FUERA!", "¡BIEN ABIERTA!", "¡A LA CALLE!", "¡ESA SOBRABA!"),
    "cero": ("¡A LA BASURA!", "¡CERO PATATERO!", "¡VACÍA, BIEN!"),
}
#: Lo que dice Perro Sanxe al regatear.
HAGGLE_TALK = ("¿UN POQUITO MÁS?", "¿Y SI LO SUBIMOS?", "TE LO DEJO A MITAD…")
HAGGLE_UP_TALK = ("VALE, POR SER TÚ", "LO SACO DEL FALCON", "TE SUBO, PERO CALLA")
HAGGLE_DOWN_TALK = ("¡TUT… TUT… TUT…!", "ME LLAMA BRUSELAS", "¡NI UN DURO MÁS!")
#: Lo que dice al llamar, según el humor de la oferta.
CALL_TALK: dict[str, tuple[str, ...]] = {
    "rata": ("TÓMALO O DÉJALO", "ES LO QUE HAY", "HACIENDA SOMOS TODOS"),
    "normal": ("UNA OFERTA JUSTA", "PIÉNSALO BIEN", "¿TRATO O NO TRATO?"),
    "generosa": ("ESTOY GENEROSO HOY", "NO TE LO VUELVO A DAR", "CÓGELO Y CORRE"),
}


@dataclass(frozen=True, slots=True)
class Media:
    """Una jugada: el GIF, el PNG del último fotograma y lo que dura el GIF."""

    gif: bytes
    png: bytes
    seconds: float


def _ease(t: float) -> float:
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _r(value: float) -> float:
    return round(value, 3)


def _shake(k: int, amp: float) -> list[float]:
    return [round(math.sin(k * 2.3) * amp, 1), round(math.cos(k * 1.9) * amp, 1)]


def meta_state(game: AllatuGame) -> dict[str, Any]:
    """Lo fijo de una partida: tabla, apuesta y gordo."""
    return {
        "table": game.table.key,
        "tableLabel": game.table.label.upper(),
        "stake": format_amount(game.stake),
        "gordo": format_multiplier(game.gordo),
    }


def _ladder(game: AllatuGame, *, done: bool) -> list[dict[str, Any]]:
    """Los 22 premios de menor a mayor, con los que ya han salido tachados."""
    out = [game.values[i] for i in game.opened]
    mine = game.own_value if done else None
    rows = []
    for value in sorted(game.values):
        row: dict[str, Any] = {"v": format_multiplier(value), "tier": tier(value, game.gordo)}
        if value in out:
            out.remove(value)
            row["out"] = True
        elif mine is not None and value == mine:
            mine = None
            row["mine"] = True
        rows.append(row)
    return rows


def _chips(game: AllatuGame) -> list[dict[str, str]]:
    """Las ofertas de la banca, en orden: cifra y qué pasó con cada una."""
    chips = []
    for offer in game.offers:
        if offer.hung_up:
            chips.append({"t": "COLGÓ", "k": "hang"})
            continue
        kind = "deal" if offer.accepted else ("up" if offer.haggled else "no")
        if offer is game.offer:
            kind = "now"
        chips.append({"t": format_amount(offer.amount), "k": kind})
    return chips


def _titles(game: AllatuGame) -> tuple[str, str]:
    """Título y subtítulo del plató según la fase."""
    if game.phase is Phase.PICK:
        return "ELIGE TU CAJA", f"EL GORDO DE HOY: {format_multiplier(game.gordo)}"
    if game.phase is Phase.OPEN:
        left = game.left_in_round
        caja = "CAJA" if left == 1 else "CAJAS"
        return f"RONDA {game.round + 1} DE {len(ROUNDS)}", f"ABRE {left} {caja} MÁS"
    if game.phase is Phase.OFFER:
        return "LLAMA LA BANCA", ""
    if game.phase is Phase.FINAL:
        return "¿TE QUEDAS O CAMBIAS?", "QUEDAN DOS CAJAS"
    if game.ending is Ending.DEAL:
        return "¡TRATO!", f"TE LLEVAS {format_amount(game.payout)}"
    return "¡FIN!", f"TE LLEVAS {format_amount(game.payout)}"


def board_state(game: AllatuGame) -> dict[str, Any]:
    """El plató de una partida tal como está.

    Cajas: `closed` (cerrada), `open` (abierta, con su premio), `own` (el hueco
    de tu caja, que está en el atril). Al acabar se ven todas abiertas.
    """
    done = not game.playing
    opened = set(game.opened)
    boxes = []
    for i in range(BOXES):
        box: dict[str, Any] = {"n": i + 1}
        value = game.values[i]
        if i == game.own:
            box["s"] = "own"
        elif i in opened or done:
            box |= {"s": "open", "v": format_multiplier(value), "tier": tier(value, game.gordo)}
        else:
            box["s"] = "closed"
        boxes.append(box)
    own: dict[str, Any] | None = None
    if game.own is not None:
        own = {"n": game.own + 1}
        if done:
            value = game.values[game.own]
            own |= {"v": format_multiplier(value), "tier": tier(value, game.gordo)}
    title, sub = _titles(game)
    state: dict[str, Any] = {
        "phase": game.phase.value,
        "boxes": boxes,
        "own": own,
        "ladder": _ladder(game, done=done),
        "title": title,
        "sub": sub,
        "offers": _chips(game),
        "gordoAlive": game.gordo_alive,
    }
    if done:
        state["result"] = {
            "text": f"+{format_amount(game.net)}" if game.net >= 0 else
            f"-{format_amount(-game.net)}",
            "kind": "good" if game.net >= 0 else "bad",
        }  # fmt: skip
    return state


def rest_state(game: AllatuGame) -> dict[str, Any]:
    """La imagen quieta de una partida: el plató o, con oferta, la llamada colgada al teléfono."""
    if game.phase is Phase.OFFER:
        return {**_call_rest(game), "ms": FINAL_FRAME_MS, "t": 0}
    return {"view": "board", "board": board_state(game), "ms": FINAL_FRAME_MS, "t": 0}


class _Timeline:
    """Estados en orden, con el reloj `t` de focos y destellos."""

    def __init__(self, start: int = 0) -> None:
        self.states: list[dict[str, Any]] = []
        self.start = start

    def add(self, ms: int, state: dict[str, Any]) -> None:
        self.states.append({**state, "ms": ms, "t": self.start + len(self.states)})

    def rest(self, game: AllatuGame) -> None:
        self.states.append({**rest_state(game), "t": self.start + len(self.states)})


def _banner(text: str, sub: str, kind: str, a: float) -> dict[str, Any]:
    return {"text": text, "sub": sub, "kind": kind, "a": _r(_clamp(a))}


def _fade_to(tl: _Timeline, last: dict[str, Any], game: AllatuGame, frames: int = 3) -> None:
    """Funde a negro lo último que se ve y vuelve con el plató de `game`."""
    keep = {k: v for k, v in last.items() if k not in ("ms", "t")}
    for k in range(frames):
        tl.add(55, {**keep, "fade": _r((k + 1) / frames)})
    board = board_state(game)
    for k in range(frames):
        tl.add(55, {"view": "board", "board": board, "fade": _r(1 - (k + 1) / frames)})
    tl.rest(game)


# -- La ruleta del gordo (al empezar) -----------------------------------------------------


def opening_states(game: AllatuGame) -> list[dict[str, Any]]:
    """La ruleta que sortea el gordo y el plató para elegir caja (`game` sin tocar)."""
    table = game.table
    tiers = [
        {"v": format_multiplier(mult), "p": format_chance(table.gordo_chance(mult))}
        for mult, _ in table.gordo
    ]
    target = [m for m, _ in table.gordo].index(game.gordo)
    n = len(tiers)
    # Cuatro vueltas largas al rodillo y frenada hasta la casilla del gordo.
    end = 4 * n + target
    steps = 26
    tl = _Timeline()
    base = {"view": "wheel", "tiers": tiers, "table": table.label.upper()}
    for k in range(steps):
        p = (k + 1) / steps
        ms = 40 + round(150 * p**3)
        tl.add(ms, {**base, "pos": _r(end * _ease_out(p)), "glow": 0})
    big = game.gordo >= 50
    text = "¡GORDO DE LOS GORDOS!" if game.gordo == table.max_gordo else "HOY EL GORDO ES"
    for k in range(10):
        a = (k + 1) / 4
        tl.add(
            80,
            {
                **base,
                "pos": end,
                "glow": _r(1 - k / 12),
                "banner": _banner(text, format_multiplier(game.gordo), "gordo" if big else "info", a),
            },
        )
    _fade_to(tl, tl.states[-1], game)
    return tl.states


# -- Abrir una caja ----------------------------------------------------------------------


def open_states(game: AllatuGame, box: int, *, own: bool = False) -> list[dict[str, Any]]:
    """Una caja de cerca: tiembla, se abre y enseña su premio.

    Solo depende de la caja y de lo que tiene: vale para cualquier momento de la
    partida. Acaba con el premio a la vista; el cog pone después el plató.

    Args:
        own: Si es tu caja (al aceptar un trato): lleva el cartel «TU CAJA» y
            el cartel dice si el trato fue bueno.
    """
    value = game.values[box]
    kind = tier(value, game.gordo)
    pick = random.Random(box * 7919 + game.gordo)
    base = {
        "view": "open",
        "n": box + 1,
        "v": format_multiplier(value),
        "amount": format_amount(game.amount_of(value)),
        "tier": kind,
        "own": own,
    }
    tl = _Timeline(start=box * 30)
    for k in range(5):
        tl.add(45, {**base, "zoom": _r(_ease((k + 1) / 5))})
    for k in range(7):
        p = (k + 1) / 7
        tl.add(80, {**base, "zoom": 1, "shake": _shake(k, 0.6 + 2.6 * p), "glow": _r(p)})
    for k in range(6):
        tl.add(50, {**base, "zoom": 1, "lid": _r(_ease_out((k + 1) / 6)), "glow": 1})
    flash = {"gordo": [255, 60, 30], "rojo": [255, 90, 60]}.get(kind)
    for k in range(4):
        p = (k + 1) / 4
        state = {**base, "zoom": 1, "lid": 1, "rise": _r(_ease_out(p)), "glow": _r(1 - p / 2)}
        if flash and k < 2:
            state["flash"] = [*flash, _r(0.35 * (1 - k / 2))]
        tl.add(60, state)
    if own:
        text = "¡BUEN TRATO!" if game.payout >= game.amount_of(value) else "¡TE HAN TIMADO!"
        sub = f"EN TU CAJA HABÍA {format_multiplier(value)}"
        bkind = "good" if game.payout >= game.amount_of(value) else "bad"
    else:
        text = pick.choice(OPEN_BANNERS[kind])
        sub = f"{format_multiplier(value)} · {format_amount(game.amount_of(value))}"
        bkind = {"gordo": "gordo", "rojo": "bad"}.get(kind, "good")
    for k in range(6):
        tl.add(
            70,
            {**base, "zoom": 1, "lid": 1, "rise": 1, "banner": _banner(text, sub, bkind, (k + 1) / 3)},
        )
    last = {k: v for k, v in tl.states[-1].items() if k not in ("ms", "t")}
    tl.add(FINAL_FRAME_MS, last)
    return tl.states


# -- La llamada de la banca --------------------------------------------------------------


def _talk(game: AllatuGame, lines: tuple[str, ...]) -> str:
    return lines[(len(game.opened) + game.gordo) % len(lines)]


def _call_base(game: AllatuGame) -> dict[str, Any]:
    offer = game.offer
    assert offer is not None
    label = "OFERTA FINAL" if offer.round == len(ROUNDS) - 1 else f"OFERTA {offer.round + 1} DE 5"
    if offer.haggled:
        label = "OFERTA REGATEADA"
    return {
        "view": "call",
        "board": board_state(game),
        "label": label,
        "mood": offer_mood(game),
    }


def _call_rest(game: AllatuGame) -> dict[str, Any]:
    offer = game.offer
    assert offer is not None
    mood = offer_mood(game)
    return {
        **_call_base(game),
        "answer": 1,
        "count": 1,
        "amount": format_amount(offer.amount),
        "mult": format_multiplier(Fraction(offer.amount, game.stake)),
        "stamp": 1 if mood != "normal" else 0,
        "talk": _talk(game, CALL_TALK[mood]),
    }


def _count_up(tl: _Timeline, base: dict[str, Any], start: int, end: int, frames: int) -> None:
    for k in range(frames):
        p = _ease_out((k + 1) / frames)
        tl.add(
            55, {**base, "answer": 1, "count": _r(p), "amount": format_amount(round(start + (end - start) * p))}
        )


def call_states(game: AllatuGame) -> list[dict[str, Any]]:
    """Suena el teléfono, descuelga Perro Sanxe y sube el marcador hasta la oferta.

    `game` está en `Phase.OFFER` (con la oferta recién hecha).
    """
    offer = game.offer
    assert offer is not None
    tl = _Timeline(start=len(game.opened) * 10)
    base = _call_base(game)
    for k in range(10):
        tl.add(70, {**base, "ring": k})
    for k in range(3):
        tl.add(70, {**base, "answer": _r((k + 1) / 3)})
    _count_up(tl, base, 0, offer.amount, 12)
    rest = _call_rest(game)
    if rest["stamp"]:
        for k in range(4):
            tl.add(70, {**rest, "stamp": _r((k + 1) / 4)})
    tl.rest(game)
    return tl.states


def deal_states(game: AllatuGame) -> list[dict[str, Any]]:
    """✅ Trato: el sello, la lluvia de monedas, tu caja abierta y el plató destapado.

    `game` está en `Phase.OFFER` y no se toca.
    """
    after = deepcopy(game)
    after.deal()
    assert after.own is not None
    rest = _call_rest(game)
    tl = _Timeline(start=len(game.opened) * 10)
    sub = f"TE LLEVAS {format_amount(after.payout)}"
    for k in range(10):
        p = (k + 1) / 10
        tl.add(
            65,
            {
                **rest,
                "stamp": 0,
                "coins": _r(p),
                "banner": _banner("¡TRATO HECHO!", sub, "deal", p * 2.5),
            },
        )
    reveal = open_states(after, after.own, own=True)
    tl.states += [{**s, "t": tl.start + len(tl.states) + i} for i, s in enumerate(reveal[:-1])]
    _fade_to(tl, reveal[-2], after)
    return tl.states


def haggle_states(game: AllatuGame) -> list[dict[str, Any]]:
    """🤝 Regatear: Perro Sanxe se lo piensa y sube la oferta o cuelga.

    `game` está en `Phase.OFFER` y no se toca; lo que pasa ya está sorteado
    (`AllatuGame.haggle_result`).
    """
    after = deepcopy(game)
    up = after.haggle()
    rest = _call_rest(game)
    tl = _Timeline(start=len(game.opened) * 10 + 5)
    talk = _talk(game, HAGGLE_TALK)
    for k in range(6):
        tl.add(90, {**rest, "stamp": 0, "talk": talk, "shake": _shake(k, 1.2)})
    for k in range(5):
        tl.add(120, {**rest, "stamp": 0, "talk": "." * (k % 3 + 1)})
    if up:
        new = after.offer
        old = game.offer
        assert new is not None and old is not None
        base = {**_call_base(after), "talk": _talk(game, HAGGLE_UP_TALK)}
        _count_up(tl, base, old.amount, new.amount, 10)
        final = _call_rest(after)
        for k in range(5):
            tl.add(
                70,
                {**final, "banner": _banner("¡SUBE LA OFERTA!", final["amount"], "good", (k + 1) / 2)},
            )
        tl.rest(after)
        return tl.states
    hang_talk = _talk(game, HAGGLE_DOWN_TALK)
    for k in range(4):
        tl.add(70, {**rest, "stamp": 0, "hang": _r((k + 1) / 4), "talk": hang_talk})
    hung = {**rest, "stamp": 0, "hang": 1, "talk": hang_talk}
    for k in range(6):
        tl.add(
            90,
            {**hung, "banner": _banner("¡HA COLGADO!", "ESTA RONDA, SIN OFERTA", "hang", (k + 1) / 2)},
        )
    _fade_to(tl, tl.states[-1], after)
    return tl.states


def no_deal_state(game: AllatuGame) -> dict[str, Any]:
    """❌ No trato: el plató para seguir (o el final a dos cajas). `game` no se toca."""
    after = deepcopy(game)
    after.no_deal()
    return rest_state(after)


# -- El final a dos cajas ----------------------------------------------------------------


def _final_box(game: AllatuGame, box: int) -> dict[str, Any]:
    value = game.values[box]
    return {"n": box + 1, "v": format_multiplier(value), "tier": tier(value, game.gordo)}


def final_states(game: AllatuGame, *, swap: bool) -> list[dict[str, Any]]:
    """🔒 Quedarse o 🔄 cambiar: las dos cajas, el cruce si cambia y las dos abiertas.

    `game` está en `Phase.FINAL` y no se toca.
    """
    assert game.own is not None
    other = game.other
    assert other is not None
    after = deepcopy(game)
    after.finish(swap=swap)
    mine = "b" if swap else "a"
    a, b = _final_box(game, game.own), _final_box(game, other)
    tl = _Timeline(start=600)
    board = board_state(game)

    def scene(**extra: Any) -> dict[str, Any]:
        return {"view": "final", "board": board, "mine": mine, **extra}

    def boxes(**lids: Any) -> dict[str, Any]:
        mine_lid = lids.get("mine", {})
        other_lid = lids.get("other", {})
        return {
            "a": {**a, **(mine_lid if mine == "a" else other_lid)},
            "b": {**b, **(mine_lid if mine == "b" else other_lid)},
        }

    for k in range(4):
        tl.add(60, scene(intro=_r((k + 1) / 4), **boxes()))
    if swap:
        for k in range(9):
            tl.add(60, scene(swap=_r(_ease((k + 1) / 9)), **boxes()))
    s = 1 if swap else 0
    for k in range(7):
        p = (k + 1) / 7
        tl.add(85, scene(swap=s, shake=_shake(k, 0.8 + 2.5 * p), glow=_r(p), **boxes()))
    for k in range(6):
        tl.add(55, scene(swap=s, **boxes(mine={"lid": _r(_ease_out((k + 1) / 6))})))
    for k in range(4):
        tl.add(60, scene(swap=s, **boxes(mine={"lid": 1, "rise": _r(_ease_out((k + 1) / 4))})))
    value = after.values[after.own] if after.own is not None else Fraction(0)
    kind = tier(value, game.gordo)
    good = after.net >= 0
    text = "¡EL GORDO!" if kind == "gordo" else ("¡PREMIO!" if good else "¡AY, BENDITO!")
    sub = f"TE LLEVAS {format_amount(after.payout)}"
    bkind = "gordo" if kind == "gordo" else ("good" if good else "bad")
    opened = {"lid": 1, "rise": 1}
    for k in range(6):
        tl.add(
            75,
            scene(swap=s, banner=_banner(text, sub, bkind, (k + 1) / 3), **boxes(mine=opened)),
        )
    other_value = game.values[game.own if swap else other]
    other_sub = f"LA OTRA TENÍA {format_multiplier(other_value)}"
    for k in range(6):
        lid = {"lid": _r(_ease_out(min(1, (k + 1) / 4))), "rise": _r(_clamp((k - 2) / 3))}
        tl.add(
            70,
            scene(swap=s, banner=_banner(text, other_sub, bkind, 1), **boxes(mine=opened, other=lid)),
        )
    _fade_to(tl, tl.states[-1], after)
    return tl.states


def encode(frames: list[Image.Image], states: list[dict[str, Any]]) -> Media:
    """GIF (con el último fotograma quieto) y PNG del último fotograma."""
    ms = [int(s["ms"]) for s in states[:-1]] + [FINAL_FRAME_MS]
    gif = local_palette_gif(frames, ms)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=sum(ms[:-1]) / 1000)


class AllatuScene:
    """Dibuja Allá tú con Node y, si no, con Chromium.

    Cada método devuelve `None` si no hay ninguno de los dos: el cog sigue con
    el tablero de texto. Ningún método toca la partida que recibe.

    Args:
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(self, *, executable_path: str | None = None, node: str | None = None) -> None:
        self.painter = NodeScene(SCENE, name="El pintor de Allá tú", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de Allá tú",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya solo queda el texto."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def _frames(
        self, meta: dict[str, Any], states: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """Recuadros de cada fotograma; `None` si no hay Node ni navegador."""
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def _gif(self, game: AllatuGame, states: list[dict[str, Any]]) -> Media | None:
        patches = await self._frames(meta_state(game), states)
        if patches is None:
            return None
        return await asyncio.to_thread(
            lambda: encode(browser_scene.assemble(patches, (W, H)), states)
        )

    async def still(self, game: AllatuGame) -> bytes | None:
        """PNG de cómo está `game` (el plató, o la llamada si hay oferta)."""
        patches = await self._frames(meta_state(game), [rest_state(game)])
        if patches is None:
            return None
        return await asyncio.to_thread(patch_png, patches[0])

    async def opening(self, game: AllatuGame) -> Media | None:
        """La ruleta del gordo de una partida nueva y el plató para elegir caja."""
        return await self._gif(game, opening_states(game))

    async def open_box(self, game: AllatuGame, box: int) -> Media | None:
        """Abrir la caja `box`: vale en cualquier momento de la partida."""
        return await self._gif(game, open_states(game, box))

    async def call(self, game: AllatuGame) -> Media | None:
        """La llamada de la banca con la oferta en juego (`game` en `Phase.OFFER`)."""
        return await self._gif(game, call_states(game))

    async def deal(self, game: AllatuGame) -> Media | None:
        """✅ Trato con la oferta en juego."""
        return await self._gif(game, deal_states(game))

    async def haggle(self, game: AllatuGame) -> Media | None:
        """🤝 Regatear la oferta en juego (lo que pasa ya está sorteado)."""
        return await self._gif(game, haggle_states(game))

    async def no_deal(self, game: AllatuGame) -> bytes | None:
        """❌ No trato: el PNG del plató para seguir."""
        patches = await self._frames(meta_state(game), [no_deal_state(game)])
        if patches is None:
            return None
        return await asyncio.to_thread(patch_png, patches[0])

    async def final(self, game: AllatuGame, *, swap: bool) -> Media | None:
        """🔒 Quedarse o 🔄 cambiar en el final a dos cajas."""
        return await self._gif(game, final_states(game, swap=swap))
