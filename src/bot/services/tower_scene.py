"""La Torre del dragón con canvas: en Node y, si no, en Chromium.

La escena (`assets/torre/escena.html`) pinta el piso por dentro (las puertas de
madera, hierro, runas y bronce, las antorchas, las ventanas y el suelo), la
cima al coronar, la torre entera al acabar y el panel de la derecha con lo que
se cobra y la torre en miniatura. Cada dificultad tiene su tema: de día, al
atardecer, de noche, en ruinas con brasas y de obsidiana con lava.

Python decide todo lo que se ve (`pick_states`, `cashout_states`,
`rest_state`): la escena solo pinta. Cada GIF es una jugada entera:

- **Puerta** (`pick_states`): la tensión (la puerta brilla y tiembla y late el
  borde; más larga cuanto más alto es el piso), y después el huevo, que abre la
  puerta y sube al piso de arriba (o a la cima, que corona: corona, dragón que
  se inclina y lluvia de monedas), o el dragón, que sale, escupe fuego y lo
  chamusca todo. Con 🎲 va antes una ruleta sobre las puertas.
- **Cobrar** (`cashout_states`): se abren en fantasma las puertas del piso
  siguiente (lo que había detrás) y llueven monedas hacia el panel.
- Al acabar (quemado, cobrado o coronado), la torre entera con los dragones
  encendiéndose de abajo arriba (`_ending`).

El último fotograma de cada GIF es el PNG que queda después
(`rest_state` de la partida resultante).

La escena se pinta de dos maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.

Si tampoco hay navegador, cada método devuelve `None` y el cog vuelve a la
torre de texto (`TowerBoard.tower_text`): la torre nunca se queda sin dibujo.

Coste: un GIF de puerta lleva ~20-40 fotogramas y uno que acaba la partida,
~40-60. La torre los pinta antes del clic, uno por botón (`TowerBoard.prepare`).
"""

from __future__ import annotations

import asyncio
import io
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.economy import format_amount
from bot.services.node_scene import NodeScene, patch_png
from bot.services.tower import (
    FLOORS,
    Difficulty,
    Status,
    TowerGame,
    format_multiplier,
    multiplier_cents,
)
from bot.utils.gif import local_palette_gif

SCENE = Path(__file__).resolve().parent.parent / "assets" / "torre" / "escena.html"

#: Tamaño de la imagen (el de las demás escenas del casino).
W, H = 640, 360
#: Fotograma normal de las animaciones.
FRAME_MS = 60
#: El último fotograma del GIF se queda quieto (Discord repite los GIF).
FINAL_FRAME_MS = 60_000
#: Tensión del primer piso; cada piso suma `TENSION_PER_FLOOR` fotogramas.
TENSION_FRAMES = 3
TENSION_PER_FLOOR = 1
#: La tensión va a menos fotogramas y más largos: no se nota y el GIF pesa menos.
TENSION_MS = 90


@dataclass(frozen=True, slots=True)
class Media:
    """Una jugada: el GIF, el PNG del último fotograma y lo que dura el GIF."""

    gif: bytes
    png: bytes
    seconds: float


def short_multiplier(cents: int) -> str:
    """`×1,32`; desde ×1.000, sin decimales (`×259.522`), para que quepa en el panel."""
    text = format_multiplier(cents)
    return text.split(",")[0] if cents >= 100_000 else text


def tension_frames(floor: int) -> int:
    """Fotogramas de tensión antes de abrir una puerta del piso `floor` (0-8)."""
    return TENSION_FRAMES + TENSION_PER_FLOOR * floor


def _ease(t: float) -> float:
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def meta_state(difficulty: Difficulty, stake: int, dragons: Any = ()) -> dict[str, Any]:
    """Lo fijo de una partida: tema, puertas, multiplicadores, apuesta y dragones."""
    return {
        "theme": difficulty.key,
        "width": difficulty.width,
        "label": difficulty.label.upper(),
        "short": [short_multiplier(multiplier_cents(difficulty, f + 1)) for f in range(FLOORS)],
        "stake": format_amount(stake),
        "dragons": [sorted(floor) for floor in dragons],
    }


def _meta(game: TowerGame) -> dict[str, Any]:
    return meta_state(game.difficulty, game.stake, game.dragons)


def panel_state(game: TowerGame, *, reveal: int = 0) -> dict[str, Any]:
    """El panel de la derecha: lo que se cobra y la torre en miniatura.

    Args:
        reveal: Cuántos pisos, de abajo arriba, enseñan ya sus dragones.
    """
    burned = [game.floor, game.burned] if game.burned is not None else None
    state: dict[str, Any] = {
        "picks": list(game.picks),
        "current": game.floor if game.playing else None,
        "reveal": reveal,
        "burned": burned,
    }
    if game.playing and game.floor:
        state |= {
            "label": "SI COBRAS AHORA",
            "mult": format_multiplier(game.cents),
            "value": format_amount(game.cashout_value),
        }
    elif game.playing:
        state |= {
            "label": "SI SUBES EL PRIMERO",
            "mult": format_multiplier(game.next_cents or 0),
            "value": format_amount(game.next_value or 0),
        }
    elif game.status is Status.CASHED:
        state |= {
            "label": "¡CORONADA!" if game.crowned else "COBRAS",
            "mult": format_multiplier(game.cents),
            "value": format_amount(game.payout),
            "valueColor": "#9dff8a",
        }
    else:
        state |= {
            "label": "QUEMADO",
            "labelColor": "#ff7a5a",
            "mult": "×0",
            "value": f"-{format_amount(game.stake)}",
            "valueColor": "#ff7a5a",
        }
    return state


def tower_state(game: TowerGame, tour: float) -> dict[str, Any]:
    """La torre entera con los dragones de los pisos por debajo de `tour` a la vista."""
    ghost = game.floor if game.status is Status.CASHED and not game.crowned else None
    return {
        "ms": FRAME_MS,
        "view": "tower",
        "tour": tour,
        "picks": list(game.picks),
        "burned": [game.floor, game.burned] if game.burned is not None else None,
        "ghost": ghost,
        "crowned": game.crowned,
        "panel": panel_state(game, reveal=min(FLOORS, math.ceil(tour))),
    }


def rest_state(game: TowerGame) -> dict[str, Any]:
    """La imagen quieta: el piso por jugar o, si acabó, la torre entera destapada."""
    if not game.playing:
        # `tour` pasado el último piso: todo a la vista y sin barrido de luz.
        return {**tower_state(game, FLOORS + 1), "ms": FINAL_FRAME_MS, "t": 0}
    return {
        "ms": FINAL_FRAME_MS,
        "t": 0,
        "view": "floor",
        "floor": game.floor,
        "picks": list(game.picks),
        "doors": [],
        "panel": panel_state(game),
    }


def opening_state(difficulty: Difficulty, stake: int) -> dict[str, Any]:
    """El primer piso de una partida nueva (no depende de dónde caigan los dragones)."""
    return rest_state(TowerGame(stake=stake, difficulty=difficulty, dragons=()))


class _Timeline:
    """Estados en orden, con el reloj `t` de antorchas y brasas."""

    def __init__(self, start: int) -> None:
        self.states: list[dict[str, Any]] = []
        self.start = start

    def add(self, ms: int, state: dict[str, Any]) -> None:
        self.states.append({**state, "ms": ms, "t": self.start + len(self.states)})


def _doors(
    width: int, special: dict[int, dict[str, Any]], *, dim: float = 0
) -> list[dict[str, Any]]:
    return [special.get(i, {"dim": dim} if dim else {}) for i in range(width)]


def _shake(k: int, amp: float) -> list[float]:
    return [round(math.sin(k * 2.1) * amp, 1), round(math.cos(k * 1.7) * amp, 1)]


def pick_states(game: TowerGame, door: int, *, roulette: bool = False) -> list[dict[str, Any]]:
    """Fotogramas de abrir `door` en el piso actual de `game` (que no se toca).

    Args:
        roulette: Si la puerta la eligió 🎲: antes va la ruleta sobre las puertas.
    """
    after = deepcopy(game)
    safe = after.climb(door, random_pick=roulette)
    if safe and after.crowned:
        after.cash_out()
    floor, width = game.floor, game.difficulty.width
    tl = _Timeline(start=floor * 40)
    before = panel_state(game)

    def scene(**extra: Any) -> dict[str, Any]:
        return {
            "view": "floor",
            "floor": floor,
            "picks": list(game.picks),
            "panel": before,
            **extra,
        }

    if roulette:
        steps = 2 * width + door + 1
        for k in range(steps):
            ms = 45 + round(115 * (k / (steps - 1)) ** 2)
            tl.add(ms, scene(doors=_doors(width, {k % width: {"ring": 1}})))
    tension = tension_frames(floor) // (2 if roulette else 1)
    heart = 0.15 + 0.45 * floor / (FLOORS - 1)
    for i in range(tension):
        p = (i + 1) / tension
        beat = 0.55 + 0.45 * abs(math.sin(i * math.pi / 3))
        shaking = {"glow": round(p * beat, 3), "dx": round(math.sin(i * 2.4) * (0.5 + 2.5 * p), 2)}
        tl.add(
            TENSION_MS,
            scene(
                doors=_doors(width, {door: shaking}, dim=round(0.35 * p, 3)),
                heart=round(p * beat * heart, 3),
            ),
        )
    if safe:
        _egg(tl, scene, after, door, width)
    else:
        _burn(tl, scene, after, door, width)
    return tl.states


def _egg(tl: _Timeline, scene: Any, after: TowerGame, door: int, width: int) -> None:
    for k in range(5):
        o = (k + 1) / 5
        opened = {
            "open": round(_ease(o), 3),
            "show": "egg",
            "a": round(o, 3),
            "glow": round(1 - o / 2, 3),
        }
        tl.add(40, scene(doors=_doors(width, {door: opened})))
    shown = {"open": 1, "show": "egg", "a": 1}
    for _ in range(3):
        tl.add(80, scene(doors=_doors(width, {door: {**shown, "sparkle": True}})))
    later = panel_state(after)
    for k in range(7):
        c = (k + 1) / 8
        panel = {"panel": later} if c >= 0.5 else {}
        tl.add(45, scene(doors=_doors(width, {door: shown}), climb=round(c, 3), **panel))
    if not after.crowned:
        tl.add(FINAL_FRAME_MS, rest_state(after))
        return
    tl.add(45, scene(doors=_doors(width, {door: shown}), climb=1, panel=later))
    banner_text = format_multiplier(after.cents)
    for k in range(15):
        tl.add(
            70,
            {
                "view": "summit",
                "panel": later,
                "crown": round(_clamp((k + 1) / 10), 3),
                "bow": round(_clamp((k - 4) / 8), 3),
                "coins": round(k * 0.09, 3),
                "banner": {
                    "text": "¡CORONADA!",
                    "sub": banner_text,
                    "kind": "crown",
                    "a": round(_clamp((k - 7) / 3), 3),
                    "y": 302,
                },
            },
        )
    _ending(tl, after)


def _burn(tl: _Timeline, scene: Any, after: TowerGame, door: int, width: int) -> None:
    for k in range(3):
        o = (k + 1) / 3
        tl.add(
            40,
            scene(
                doors=_doors(width, {door: {"open": round(o, 3)}}),
                dragon={"door": door, "rise": round(0.12 * o, 3), "mouth": 0},
            ),
        )
    opened = _doors(width, {door: {"open": 1}})
    for k in range(5):
        r = (k + 1) / 5
        dragon = {"door": door, "rise": round(0.12 + 0.88 * r, 3), "mouth": round(0.9 * r, 3)}
        tl.add(50, scene(doors=opened, dragon=dragon, shake=_shake(k, 1.5 * r)))
    for k in range(5):
        fire = (k + 1) / 5
        tl.add(
            60,
            scene(
                doors=opened,
                dragon={"door": door, "rise": 1, "mouth": 1, "fire": round(fire, 3)},
                shake=_shake(k + 5, 6 * fire),
                flash=[255, 120, 30, round(0.45 * fire, 3)],
            ),
        )
    later = panel_state(after)
    for k in range(5):
        c = (k + 1) / 5
        tl.add(
            100,
            scene(
                doors=opened,
                dragon={
                    "door": door,
                    "rise": 1,
                    "mouth": round(1 - 0.6 * c, 3),
                    "fire": round(1 - 0.7 * c, 3),
                },
                char=round(c, 3),
                flash=[255, 90, 20, round(0.45 * (1 - c), 3)],
                shake=_shake(k + 12, 3 * (1 - c)),
                panel=later,
                banner={
                    "text": "¡CHAMUSCADO!",
                    "sub": f"-{format_amount(after.stake)}",
                    "kind": "burn",
                    "a": round(_clamp(c * 1.6), 3),
                },
            ),
        )
    _ending(tl, after)


def cashout_states(game: TowerGame) -> list[dict[str, Any]]:
    """Fotogramas de cobrar en el piso actual de `game` (que no se toca).

    Las puertas del piso siguiente se abren en fantasma: lo que había detrás.
    """
    after = deepcopy(game)
    after.cash_out()
    floor, width = game.floor, game.difficulty.width
    tl = _Timeline(start=floor * 40)
    before, later = panel_state(game), panel_state(after)
    dragons = game.dragons[floor]

    def ghosts(opened: float, alpha: float) -> list[dict[str, Any]]:
        return [
            {
                "open": round(opened, 3),
                "show": "ghost-dragon" if i in dragons else "ghost-egg",
                "a": round(alpha, 3),
            }
            for i in range(width)
        ]

    def scene(**extra: Any) -> dict[str, Any]:
        return {"view": "floor", "floor": floor, "picks": list(game.picks), **extra}

    for k in range(6):
        o = (k + 1) / 6
        tl.add(50, scene(doors=ghosts(0.85 * _ease(o), o), panel=before))
    sub = f"{format_amount(after.payout)} · {format_multiplier(after.cents)}"
    for k in range(12):
        p = (k + 1) / 8
        banner = {"text": "¡COBRADO!", "sub": sub, "kind": "cash", "a": round(_clamp(p * 2), 3)}
        tl.add(
            55,
            scene(
                doors=ghosts(0.85, 1),
                coins=round(p, 3),
                banner=banner,
                panel=later if p >= 0.5 else before,
            ),
        )
    _ending(tl, after)
    return tl.states


def _ending(tl: _Timeline, after: TowerGame) -> None:
    """Fundido a negro y la torre entera, encendiendo los dragones de abajo arriba."""
    last = {k: v for k, v in tl.states[-1].items() if k not in ("ms", "t")}
    for k in range(3):
        tl.add(50, {**last, "fade": round((k + 1) / 3, 3)})
    for fade in (0.66, 0.33):
        tl.add(50, {**tower_state(after, 0), "fade": fade})
    for k in range(FLOORS):
        tl.add(70, tower_state(after, k + 0.5))
    tl.add(FINAL_FRAME_MS, rest_state(after))


def encode(frames: list[Image.Image], states: list[dict[str, Any]]) -> Media:
    """GIF (con el último fotograma quieto) y PNG del último fotograma."""
    ms = [int(s["ms"]) for s in states[:-1]] + [FINAL_FRAME_MS]
    gif = local_palette_gif(frames, ms)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=sum(ms[:-1]) / 1000)


class TowerScene:
    """Dibuja la Torre del dragón con Node y, si no, con Chromium.

    Cada método devuelve `None` si no hay ninguno de los dos: el cog vuelve a
    la torre de texto.

    Args:
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(self, *, executable_path: str | None = None, node: str | None = None) -> None:
        self.painter = NodeScene(SCENE, name="El pintor de la torre", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de la torre",
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

    async def _png(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes | None:
        patches = await self._frames(meta, [state])
        if patches is None:
            return None
        return await asyncio.to_thread(patch_png, patches[0])

    async def _gif(self, meta: dict[str, Any], states: list[dict[str, Any]]) -> Media | None:
        patches = await self._frames(meta, states)
        if patches is None:
            return None
        return await asyncio.to_thread(
            lambda: encode(browser_scene.assemble(patches, (W, H)), states)
        )

    async def opening(self, difficulty: Difficulty, stake: int) -> bytes | None:
        """PNG del primer piso de una partida nueva en `difficulty` con `stake`."""
        return await self._png(meta_state(difficulty, stake), opening_state(difficulty, stake))

    async def still(self, game: TowerGame) -> bytes | None:
        """PNG de cómo está `game`: su piso o, si acabó, la torre destapada."""
        return await self._png(_meta(game), rest_state(game))

    async def pick(self, game: TowerGame, door: int, *, roulette: bool = False) -> Media | None:
        """GIF de abrir `door` en el piso actual de `game` (sin tocar `game`)."""
        return await self._gif(_meta(game), pick_states(game, door, roulette=roulette))

    async def cashout(self, game: TowerGame) -> Media | None:
        """GIF de cobrar `game` en su piso actual (sin tocar `game`)."""
        return await self._gif(_meta(game), cashout_states(game))
