"""La animación de una pelea de gallos: qué se ve en cada fotograma.

Python decide todo lo que se ve y la escena de canvas (`assets/gallos/escena.html`,
ver `bot.services.gallos_scene`) solo lo pinta. Aquí se traduce una `Fight`
ya decidida a una lista de estados, uno por fotograma (`timeline`), con su
duración, y los estados quietos de antes y después de pelear (`intro_state`,
`final_state`).

**Contrato con la escena** (lo que lee; nada más):

- `meta`: lo fijo de la pelea. Por esquina (`red`, `blue`): `name`, `tag`
  (dueño u origen, en mayúsculas), `skin` (los parámetros de
  `skins.params("gallos", …)`) y `odds` (texto de la cuota o del bote). Además
  `title`, `subtitle` y `seed` (para el público y las plumas).
- Cada estado: por esquina, `x` e `y` (pies del gallo, en píxeles de la mesa de
  640×360), `face` (1 mira a la derecha, -1 a la izquierda), `pose` (`POSES`),
  `t` (avance de la pose, 0-1), `hp` (vida que enseña la barra) y `flash`
  (destello blanco al recibir, 0-1). Y `round`, `shake` (sacudida de cámara en
  píxeles), `crowd` (salto del público, 0-1), `fx` (efectos: onomatopeyas,
  plumas, estrellas, polvo) y `banner` (cartel grande, o `None`).

Si Node y Chromium fallan, `GallosRenderer` pinta con Pillow una mesa sencilla
(los dos gallos como siluetas de su color y el resultado), sin animación: el
juego nunca se queda sin imagen.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from bot.services.gallos import (
    MAX_HP,
    MOVE_BY_KEY,
    Corner,
    Exchange,
    Fight,
    Finish,
)
from bot.utils.gif import local_palette_gif

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"

#: Tamaño de la mesa (el de las demás escenas del casino).
W, H = 640, 360
#: Suelo del reñidero (los pies de los gallos) y sitio de cada esquina.
GROUND = 292
HOME = {Corner.RED: 214.0, Corner.BLUE: 426.0}
#: Hasta dónde se acerca el que ataca (distancia entre los dos gallos).
REACH = 78.0
#: Duración de cada fotograma y del último (el GIF se queda quieto al final).
FRAME_MS = 60
FINAL_FRAME_MS = 60_000

#: Poses que sabe pintar la escena.
POSES = ("idle", "windup", "strike", "hit", "dodge", "ko", "flee", "crow", "walk")


@dataclass(frozen=True, slots=True)
class Media:
    """Imágenes de una pelea: GIF, PNG del final y duración de la animación."""

    gif: bytes
    png: bytes
    seconds: float


@dataclass(frozen=True, slots=True)
class Fighter:
    """Lo fijo de un gallo en la pelea (lo que va en `meta`)."""

    name: str
    tag: str
    skin: dict[str, object]
    odds: str = ""


def meta_state(
    red: Fighter, blue: Fighter, *, title: str, subtitle: str, seed: int
) -> dict[str, Any]:
    """Lo fijo de la pelea, tal como lo lee la escena."""

    def one(f: Fighter) -> dict[str, Any]:
        skin = {k: list(v) if isinstance(v, tuple) else v for k, v in f.skin.items()}
        return {"name": f.name, "tag": f.tag.upper(), "skin": skin, "odds": f.odds}

    return {
        "red": one(red),
        "blue": one(blue),
        "title": title,
        "subtitle": subtitle,
        "seed": seed,
    }


# -- Estados -----------------------------------------------------------------------------


def _bird(
    corner: Corner,
    *,
    x: float | None = None,
    y: float = 0.0,
    pose: str = "idle",
    t: float = 0.0,
    hp: int = MAX_HP,
    flash: float = 0.0,
) -> dict[str, Any]:
    assert pose in POSES, pose
    return {
        "x": round(HOME[corner] if x is None else x, 2),
        "y": round(GROUND - y, 2),
        "face": 1 if corner is Corner.RED else -1,
        "pose": pose,
        "t": round(t, 3),
        "hp": hp,
        "flash": round(flash, 3),
    }


def _state(
    red: dict[str, Any],
    blue: dict[str, Any],
    *,
    round_number: int = 0,
    shake: float = 0.0,
    crowd: float = 0.0,
    fx: list[dict[str, Any]] | None = None,
    banner: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "red": red,
        "blue": blue,
        "round": round_number,
        "shake": round(shake, 2),
        "crowd": round(crowd, 3),
        "fx": fx or [],
        "banner": banner,
    }


def _birds(
    hp: dict[Corner, int], **overrides: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Los dos gallos en reposo en su esquina, con lo que cambie cada uno."""
    red = _bird(Corner.RED, hp=hp[Corner.RED], **overrides.get("red", {}))
    blue = _bird(Corner.BLUE, hp=hp[Corner.BLUE], **overrides.get("blue", {}))
    return red, blue


def _key(corner: Corner) -> str:
    return "red" if corner is Corner.RED else "blue"


def intro_state(*, banner: dict[str, str] | None = None) -> dict[str, Any]:
    """Antes de pelear: los dos gallos en su esquina, a vida completa."""
    red, blue = _birds({Corner.RED: MAX_HP, Corner.BLUE: MAX_HP})
    return _state(red, blue, banner=banner)


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def _intro(states: list[tuple[dict[str, Any], int]]) -> None:
    """Los gallos entran caminando desde fuera y se plantan."""
    frames = 10
    for i in range(frames):
        k = _ease((i + 1) / frames)
        red = _bird(Corner.RED, x=-60 + (HOME[Corner.RED] + 60) * k, pose="walk", t=i / 4 % 1)
        blue = _bird(
            Corner.BLUE, x=W + 60 - (W + 60 - HOME[Corner.BLUE]) * k, pose="walk", t=i / 4 % 1
        )
        states.append((_state(red, blue, crowd=k), FRAME_MS))
    banner = {"kind": "intro", "title": "¡A PELEAR!", "sub": ""}
    for i in range(6):
        red, blue = _birds({Corner.RED: MAX_HP, Corner.BLUE: MAX_HP})
        states.append((_state(red, blue, crowd=(i % 2) * 0.6, banner=banner), FRAME_MS))


def _round_banner(
    states: list[tuple[dict[str, Any], int]], number: int, hp: dict[Corner, int]
) -> None:
    banner = {"kind": "round", "title": f"ASALTO {number}", "sub": ""}
    for i in range(6):
        red, blue = _birds(hp, red={"pose": "idle", "t": i / 6}, blue={"pose": "idle", "t": i / 6})
        states.append((_state(red, blue, round_number=number, banner=banner), FRAME_MS))


def _exchange(
    states: list[tuple[dict[str, Any], int]],
    exchange: Exchange,
    hp: dict[Corner, int],
    rng: random.Random,
) -> None:
    """Un intercambio: carga, ataque, impacto (o esquiva) y vuelta a su sitio."""
    attacker, target = exchange.attacker, exchange.attacker.other
    a, d = _key(attacker), _key(target)
    direction = 1 if attacker is Corner.RED else -1
    start = HOME[attacker]
    strike_x = HOME[target] - direction * REACH
    move = MOVE_BY_KEY[exchange.move]
    flying = exchange.move in ("patada", "espolonazo")
    rnd = exchange.round

    def frame(
        a_bird: dict[str, Any],
        d_bird: dict[str, Any],
        ms: int = FRAME_MS,
        **kwargs: Any,
    ) -> None:
        birds = {a: a_bird, d: d_bird}
        states.append((_state(birds["red"], birds["blue"], round_number=rnd, **kwargs), ms))

    # Carga: se agacha y echa el cuerpo atrás.
    for i in range(2):
        frame(
            _bird(
                attacker, x=start - direction * 6 * i, pose="windup", t=(i + 1) / 2, hp=hp[attacker]
            ),
            _bird(target, hp=hp[target]),
        )
    # Ataque: se lanza (saltando si es patada o espolonazo).
    for i in range(3):
        k = _ease((i + 1) / 3)
        x = start + (strike_x - start) * k
        lift = (1 - (2 * k - 1) ** 2) * (46 if flying else 10)
        d_pose = "dodge" if exchange.dodged and i == 2 else "idle"
        d_x = HOME[target] + direction * (30 if exchange.dodged else 0) * k
        d_lift = 22 * k if exchange.dodged else 0.0
        frame(
            _bird(attacker, x=x, y=lift, pose="strike", t=k, hp=hp[attacker]),
            _bird(target, x=d_x, y=d_lift, pose=d_pose, t=k, hp=hp[target]),
        )
    hit_x, hit_y = HOME[target] - direction * 30, GROUND - 58
    if exchange.dodged:
        fx = [
            {
                "kind": "sound",
                "text": "¡FIUU!",
                "x": hit_x,
                "y": hit_y - 20,
                "scale": 0.9,
                "alpha": 1.0,
                "tone": "miss",
            }
        ]
        for i in range(3):
            k = (i + 1) / 3
            frame(
                _bird(
                    attacker, x=strike_x + direction * 14 * k, pose="strike", t=1.0, hp=hp[attacker]
                ),
                _bird(
                    target,
                    x=HOME[target] + direction * 30 * (1 - k),
                    y=22 * (1 - k),
                    pose="dodge",
                    t=1 - k,
                    hp=hp[target],
                ),
                fx=[{**fx[0], "alpha": round(1 - k * 0.7, 3), "scale": 0.9 + 0.2 * k}],
            )
    else:
        before = hp[target]
        hp[target] = exchange.hp[0] if target is Corner.RED else exchange.hp[1]
        crit = exchange.crit
        sound = "¡CRÍTICO!" if crit else move.sound
        color = rng.randrange(1_000_000)
        for i in range(4):
            k = (i + 1) / 4
            shown = round(before + (hp[target] - before) * min(1.0, k * 1.5))
            fx: list[dict[str, Any]] = [
                {
                    "kind": "sound",
                    "text": sound,
                    "x": hit_x,
                    "y": hit_y - 18 * k,
                    "scale": (1.3 if crit else 1.0) * (0.7 + 0.5 * min(1.0, k * 2)),
                    "alpha": round(1 - max(0.0, k - 0.6) * 1.5, 3),
                    "tone": "crit" if crit else "hit",
                },
                {
                    "kind": "feathers",
                    "x": HOME[target],
                    "y": GROUND - 40,
                    "t": round(k, 3),
                    "corner": d,
                    "seed": color,
                    "count": 9 if crit else 6,
                },
            ]
            if i == 0:
                fx.append({"kind": "burst", "x": hit_x, "y": hit_y, "t": 0.0, "big": crit})
            frame(
                _bird(
                    attacker, x=strike_x - direction * 4 * k, pose="strike", t=1.0, hp=hp[attacker]
                ),
                _bird(
                    target,
                    x=HOME[target] + direction * 16 * (1 - (1 - k) ** 2),
                    pose="hit",
                    t=k,
                    hp=shown,
                    flash=max(0.0, 1 - 2 * k),
                ),
                shake=(8 if crit else 4) * (1 - k),
                crowd=1 - k,
                fx=fx,
            )
    # Vuelta a su sitio.
    attacker_from = strike_x
    target_from = HOME[target] + (0 if exchange.dodged else direction * 16)
    for i in range(2):
        k = _ease((i + 1) / 2)
        frame(
            _bird(
                attacker,
                x=attacker_from + (start - attacker_from) * k,
                pose="idle",
                t=k,
                hp=hp[attacker],
            ),
            _bird(
                target,
                x=target_from + (HOME[target] - target_from) * k,
                pose="idle",
                t=k,
                hp=hp[target],
            ),
        )


def _finish_banner(fight: Fight, names: dict[Corner, str]) -> dict[str, str]:
    winner = names[fight.winner]
    if fight.finish is Finish.KO:
        return {"kind": "ko", "title": "¡KO!", "sub": f"GANA {winner}"}
    if fight.finish is Finish.FLED:
        return {"kind": "fled", "title": "¡SALE POR PATAS!", "sub": f"GANA {winner}"}
    return {"kind": "points", "title": "A LOS PUNTOS", "sub": f"GANA {winner}"}


def _ending(
    states: list[tuple[dict[str, Any], int]],
    fight: Fight,
    hp: dict[Corner, int],
    names: dict[Corner, str],
) -> None:
    """El final: el que pierde cae (o huye) y el que gana canta."""
    winner, loser = fight.winner, fight.loser
    w, lo = _key(winner), _key(loser)
    banner = _finish_banner(fight, names)
    rnd = fight.rounds
    away = -1 if loser is Corner.RED else 1
    for i in range(16):
        k = (i + 1) / 16
        if fight.finish is Finish.KO:
            loser_bird = _bird(loser, pose="ko", t=min(1.0, k * 3), hp=hp[loser])
            fx = [{"kind": "stars", "x": HOME[loser], "y": GROUND - 34, "t": round(k, 3)}]
        elif fight.finish is Finish.FLED:
            loser_bird = _bird(
                loser, x=HOME[loser] + away * 420 * _ease(k), pose="flee", t=k * 4 % 1, hp=hp[loser]
            )
            fx = [
                {
                    "kind": "dust",
                    "x": HOME[loser] + away * 420 * _ease(k) - away * 20,
                    "y": GROUND,
                    "t": round(k, 3),
                }
            ]
        else:
            loser_bird = _bird(loser, pose="idle", t=k, hp=hp[loser])
            fx = []
        winner_bird = _bird(winner, pose="crow", t=k, hp=hp[winner])
        if i >= 6:
            fx.append(
                {
                    "kind": "sound",
                    "text": "¡KIKIRIKÍ!",
                    "x": HOME[winner],
                    "y": GROUND - 120,
                    "scale": 0.8 + 0.2 * k,
                    "alpha": 1.0,
                    "tone": "crow",
                }
            )
        birds = {w: winner_bird, lo: loser_bird}
        states.append(
            (
                _state(
                    birds["red"],
                    birds["blue"],
                    round_number=rnd,
                    crowd=(i % 2) * 1.0,
                    fx=fx,
                    banner=banner if i >= 4 else None,
                ),
                FRAME_MS,
            )
        )


def final_state(fight: Fight, names: dict[Corner, str]) -> dict[str, Any]:
    """El último fotograma, quieto: cómo acabó (es también el PNG del resultado)."""
    hp = {c: fight.hp(c) for c in Corner}
    winner, loser = fight.winner, fight.loser
    birds = {
        _key(winner): _bird(winner, pose="crow", t=1.0, hp=hp[winner]),
        _key(loser): (
            _bird(loser, pose="ko", t=1.0, hp=hp[loser])
            if fight.finish is Finish.KO
            else _bird(loser, x=-200 if loser is Corner.RED else W + 200, pose="flee", hp=hp[loser])
            if fight.finish is Finish.FLED
            else _bird(loser, pose="idle", t=1.0, hp=hp[loser])
        ),
    }
    fx = (
        [{"kind": "stars", "x": HOME[loser], "y": GROUND - 34, "t": 1.0}]
        if fight.finish is Finish.KO
        else []
    )
    return _state(
        birds["red"],
        birds["blue"],
        round_number=fight.rounds,
        fx=fx,
        banner=_finish_banner(fight, names),
    )


def timeline(
    fight: Fight, names: dict[Corner, str], *, seed: int
) -> list[tuple[dict[str, Any], int]]:
    """Los fotogramas de la pelea con su duración en milisegundos (sin el final quieto)."""
    rng = random.Random(seed)
    states: list[tuple[dict[str, Any], int]] = []
    hp = {Corner.RED: MAX_HP, Corner.BLUE: MAX_HP}
    _intro(states)
    current = 0
    for exchange in fight.exchanges:
        if exchange.round != current:
            current = exchange.round
            _round_banner(states, current, hp)
        _exchange(states, exchange, hp, rng)
    _ending(states, fight, hp, names)
    return states


def encode(frames: list[Image.Image], durations: list[int]) -> Media:
    """GIF (el último fotograma, quieto) y PNG del último fotograma."""
    seconds = sum(durations) / 1000
    gif = local_palette_gif(frames, [*durations, FINAL_FRAME_MS])
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=seconds)


# -- Pillow (reserva) --------------------------------------------------------------------


def _hex(value: object, default: tuple[int, int, int] = (200, 200, 200)) -> tuple[int, int, int]:
    text = str(value)
    if text.startswith("#") and len(text) == 7:
        return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
    return default


class GallosRenderer:
    """La mesa con Pillow, sin animación: solo para cuando no hay Node ni navegador."""

    def _font(self, path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            return ImageFont.load_default()

    def still(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        """PNG de un estado (el de antes de pelear o el del final)."""
        image = Image.new("RGB", (W, H), (34, 22, 14))
        draw = ImageDraw.Draw(image)
        draw.ellipse(
            (40, 200, W - 40, H + 60), fill=(196, 160, 104), outline=(120, 80, 40), width=6
        )
        title = self._font(TITLE_FONT, 30)
        text = self._font(TEXT_FONT, 14)
        draw.text((W / 2, 28), meta["title"], font=title, fill=(255, 210, 90), anchor="mm")
        draw.text((W / 2, 56), meta["subtitle"], font=text, fill=(240, 230, 210), anchor="mm")
        for key in ("red", "blue"):
            bird, info = state[key], meta[key]
            if not -50 < bird["x"] < W + 50:
                continue
            x, y = bird["x"], bird["y"]
            body = _hex(info["skin"].get("body"))
            comb = _hex(info["skin"].get("comb"), (220, 40, 40))
            lying = bird["pose"] == "ko"
            if lying:
                draw.ellipse((x - 44, y - 34, x + 44, y), fill=body)
            else:
                draw.ellipse((x - 34, y - 80, x + 34, y - 20), fill=body)
                draw.ellipse(
                    (x - 12 + 20 * bird["face"], y - 112, x + 12 + 20 * bird["face"], y - 78),
                    fill=body,
                )
                draw.polygon(
                    [
                        (x + 14 * bird["face"], y - 112),
                        (x + 22 * bird["face"], y - 126),
                        (x + 30 * bird["face"], y - 112),
                    ],
                    fill=comb,
                )
            draw.text((x, y + 14), info["name"], font=text, fill=(255, 255, 255), anchor="mm")
            bar_x = 30 if key == "red" else W - 230
            draw.rectangle((bar_x, 80, bar_x + 200, 92), fill=(60, 20, 20))
            draw.rectangle((bar_x, 80, bar_x + 2 * bird["hp"], 92), fill=(90, 210, 90))
        if state.get("banner"):
            draw.rounded_rectangle((120, 130, W - 120, 200), 16, fill=(20, 16, 12))
            draw.text(
                (W / 2, 152), state["banner"]["title"], font=title, fill=(255, 210, 90), anchor="mm"
            )
            draw.text(
                (W / 2, 184), state["banner"]["sub"], font=text, fill=(255, 255, 255), anchor="mm"
            )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", compress_level=6)
        return buffer.getvalue()

    def fight(self, meta: dict[str, Any], fight: Fight, names: dict[Corner, str]) -> Media:
        """«GIF» de un solo fotograma (el final) y su PNG."""
        png = self.still(meta, final_state(fight, names))
        image = Image.open(io.BytesIO(png)).convert("RGB")
        return encode([image], [])
