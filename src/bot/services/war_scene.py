"""La Guerra con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/guerra/escena.html`) pinta el tapete (con skins), el zapato, las
cartas del bacará (con su vuelo y su giro), las fichas de la apuesta y la lateral, el
«VS», los carteles y el panel de cuotas y resultado. Python decide todo lo que se ve
(`deal_states`, `war_states` y `surrender_states`): la escena solo pinta, y el dibujo
de Pillow (el `CardRenderer` del blackjack) lee la misma mano.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`CardRenderer`), si tampoco hay navegador: solo la imagen final, sin GIF.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el siguiente.
El juego nunca se queda sin imagen.

Tres animaciones, cada una un GIF de ~1-2 s cuyo último fotograma es el PNG que se
queda en el mensaje:

- **Repartir** (`deal_states`): la tuya sale del zapato y se voltea; un poco de
  suspense; la de la banca. Si no empatan, el cartel con el resultado y el importe.
  Si empatan, el cartel de empate y la mesa espera la decisión.
- **A la guerra** (`war_states`): desde el empate, las cartas se apartan, entra la
  segunda ficha, la banca quema 3 cartas boca abajo y salen las dos de la guerra.
- **Rendirse** (`surrender_states`): la bandera blanca y las cartas se apagan.

Cada una depende solo de la mano (`WarGame`), así que el cog las pinta por adelantado
(`cogs.war.WarTable.prepare`).
"""

from __future__ import annotations

import asyncio
import math
import random
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.bacara_render import Media, durations, encode
from bot.services.blackjack import Card
from bot.services.browser_scene import BrowserScene
from bot.services.cards_render import CardRenderer, HandView
from bot.services.node_scene import NodeScene, patch_png
from bot.services.war import TIE_PAYS, WAR_TIE_BONUS, Result, WarGame, main_net, rank_value

SCENE = Path(__file__).resolve().parent.parent / "assets" / "guerra" / "escena.html"

W, H = 640, 420
#: De dónde salen las cartas: la boca del zapato, arriba a la derecha.
SHOE_XY = (558.0, 46.0)
CENTER_X = 320.0
BANK_Y, YOU_Y = 112.0, 310.0
SCALE = 1.2
#: En la guerra, las cartas del empate se apartan y salen otras dos a su lado.
WAR_SCALE = 1.05
OLD_X, NEW_X = 262.0, 378.0
BURN_XY = (452.0, 112.0)
BURN_SCALE = 0.55

FLY_FRAMES = 6
FLIP_FRAMES = 4
RESULT_FRAMES = 5
#: Las pausas se hacen con fotogramas más largos, no con más fotogramas.
FLY_MS = 35
FLIP_MS = 45
SUSPENSE_MS = 340


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


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


def meta_state(skin: dict[str, object] | None) -> dict[str, Any]:
    """Lo fijo de la escena: el tapete del jugador."""
    return {"skin": skin}


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
    scale: float = SCALE,
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


def _fly(
    card: Card | None, dest: tuple[float, float], scale: float, i: int, n: int, rot: float = 0.0
) -> dict[str, Any]:
    """La carta `i` de `n` de su vuelo desde el zapato hasta `dest`, boca abajo."""
    e = _ease((i + 1) / n)
    return _c(
        card,
        _lerp(SHOE_XY[0], dest[0], e),
        _lerp(SHOE_XY[1], dest[1], e) - 46 * math.sin(math.pi * e),
        scale=_lerp(0.45, scale, e),
        rot=_lerp(0.7, rot, e),
    )


def _turn(card: Card, x: float, y: float, scale: float, i: int, n: int, **kw: float) -> dict:
    """El giro `i` de `n` de una carta: se levanta y enseña la cara."""
    t = (i + 1) / n
    return _c(card, x, y, scale=scale, flip=t, lift=22 * math.sin(math.pi * t), **kw)


class _Reel:
    """Los fotogramas de una animación. Lo que no cambia (fichas, huecos) se guarda aquí."""

    def __init__(self, game: WarGame) -> None:
        self.game = game
        self.slots: list[tuple[float, float, float]] = [
            (CENTER_X, BANK_Y, SCALE),
            (CENTER_X, YOU_Y, SCALE),
        ]
        self.war = 0.0
        self.vs = 1.0
        self.burn = False
        self.side_state = "on" if game.side else "off"
        self.side_text = money(game.side) if game.side else ""
        self.banner: dict[str, Any] | None = None
        self.banner_spec: dict[str, Any] = {}
        self.result: dict[str, Any] | None = None
        self.frames: list[dict[str, Any]] = []

    def add(self, cards: list[dict[str, Any]], ms: int, sparks: list | None = None) -> None:
        game = self.game
        self.frames.append(
            {
                "cards": cards,
                "slots": [list(s) for s in self.slots],
                "bet": {
                    "text": money(game.stake * 2) if self.war >= 1 else money(game.stake),
                    "chip": short(game.stake),
                    "stake": game.stake,
                    "war": self.war,
                },
                "side": {
                    "text": self.side_text,
                    "chip": short(game.side),
                    "stake": game.side,
                    "state": self.side_state,
                },
                "vs": self.vs,
                "burn": self.burn,
                "banner": self.banner,
                "result": self.result,
                "sparks": sparks or [],
                "ms": ms,
            }
        )

    def hold(self, ms: int) -> None:
        """Alarga el último fotograma (una pausa sin fotogramas de más)."""
        self.frames[-1]["ms"] += ms

    def banner_at(self, spec: dict[str, Any] | None, a: float, dx: float = 0.0) -> None:
        self.banner = None if spec is None or a <= 0 else {**spec, "a": round(a, 3), "dx": dx}
        self.vs = 0.0 if self.banner else 1.0


def _seed(game: WarGame) -> int:
    return game.player[0].rank * 100 + game.dealer[0].rank * 7 + len(game.player)


def _sparks(seed: int, cx: float, cy: float, k: int, n: int) -> list[list[float]]:
    """Chispas del ganador en el paso `k` de `n`: mismas semillas, mismas chispas."""
    rng = random.Random(seed)
    t = (k + 1) / n
    out = []
    for _ in range(16):
        angle = rng.uniform(0, math.tau)
        speed = rng.uniform(26, 78)
        out.append(
            [
                round(cx + math.cos(angle) * speed * t, 1),
                round(cy + math.sin(angle) * speed * t + 18 * t * t, 1),
                round(rng.uniform(3.5, 7) * (1 - 0.5 * t), 1),
                round(1 - 0.85 * t, 2),
                rng.choice((0, 0, 2, 1)),
            ]
        )
    return out


# Qué se dice en el panel y el cartel de cada desenlace: (clase, titular, cartel, icono).
LOOKS: dict[Result | None, tuple[str, str, str, str | None]] = {
    Result.WIN: ("win", "GANAS", "GANAS", None),
    Result.LOSE: ("lose", "PIERDES", "PIERDES", None),
    Result.SURRENDER: ("surrender", "RENDICIÓN", "TE RINDES", "flag"),
    Result.WAR_WIN: ("win", "GUERRA GANADA", "GUERRA GANADA", "swords"),
    Result.WAR_TIE: ("win", "GUERRA EMPATADA", "GUERRA EMPATADA", "swords"),
    Result.WAR_LOSE: ("lose", "GUERRA PERDIDA", "GUERRA PERDIDA", "swords"),
    None: ("tie", "EMPATE", "EMPATE", None),
}
SUBS: dict[Result | None, str] = {
    Result.WIN: "COBRAS 1:1",
    Result.LOSE: "GANA LA BANCA",
    Result.SURRENDER: "RECUPERAS LA\nMITAD DE LA FICHA",
    Result.WAR_WIN: "COBRAS 1:1 DE\nLA GUERRA",
    Result.WAR_TIE: f"PREMIO EXTRA\n{WAR_TIE_BONUS}× INCLUIDO",
    Result.WAR_LOSE: "CAEN LAS\nDOS FICHAS",
    None: "RENDIRSE O\nA LA GUERRA",
}


def _signed(amount: int) -> str:
    return ("+" if amount > 0 else "-" if amount < 0 else "") + money(abs(amount))


def _resolve_side(reel: _Reel) -> None:
    """La lateral de empate se resuelve con las dos primeras cartas."""
    game = reel.game
    if not game.side:
        return
    if game.tied:
        reel.side_state, reel.side_text = "win", _signed(game.side * TIE_PAYS)
    else:
        reel.side_state, reel.side_text = "lose", _signed(-game.side)


def _outcome(reel: _Reel, result: Result | None) -> None:
    """Pone el panel y el cartel del desenlace (sin animar el cartel)."""
    game = reel.game
    kind, title, banner_title, icon = LOOKS[result]
    net = main_net(game) if result is not None else 0
    sub = SUBS[result]
    if game.side and result is not None:
        sub += f"\nLATERAL {_signed(game.side * TIE_PAYS if game.tied else -game.side)}"
    reel.result = {
        "kind": kind,
        "title": title,
        "amount": _signed(net) if result is not None else None,
        "sub": sub,
    }
    reel.banner_spec = {
        "kind": kind,
        "title": banner_title,
        "sub": _signed(net)
        if result is not None and net
        else ("¿GUERRA?" if result is None else None),
        "icon": icon,
    }


def _end(
    reel: _Reel,
    cards: Any,
    result: Result | None,
    seed: int,
    winner: tuple[float, float] | None,
) -> None:
    """Los fotogramas finales: el cartel que salta, las chispas y la mesa que se queda."""
    _outcome(reel, result)
    pops = (0.3, 0.7, 1.12, 1.0, 1.0)
    for k, a in enumerate(pops):
        reel.banner_at(reel.banner_spec, a)
        last = k == len(pops) - 1
        sparks = _sparks(seed, *winner, k, len(pops) - 1) if winner and not last else []
        reel.add(cards(min(1.0, (k + 1) / 3)), 40, sparks)


def _tie_cards(game: WarGame, t: float = 1.0) -> list[dict[str, Any]]:
    p, d = game.player[0], game.dealer[0]
    return [
        _c(d, CENTER_X, BANK_Y, flip=1, glow=0.55 * t),
        _c(p, CENTER_X, YOU_Y, flip=1, glow=0.55 * t),
    ]


def _tie_frame(reel: _Reel, ms: int) -> None:
    """El fotograma del empate, igual al último de «Repartir» (con su cartel)."""
    _resolve_side(reel)
    _outcome(reel, None)
    reel.banner_at(reel.banner_spec, 1.0)
    reel.add(_tie_cards(reel.game), ms)


def deal_states(game: WarGame) -> list[dict[str, Any]]:
    """La animación de repartir: la tuya, la de la banca, y el resultado o el empate.

    Args:
        game: La mano recién repartida (`WarGame.deal`), con o sin empate.
    """
    p, d = game.player[0], game.dealer[0]
    reel = _Reel(game)
    reel.add([], 260)
    yours = (CENTER_X, YOU_Y)
    for i in range(FLY_FRAMES):
        reel.add([_fly(p, yours, SCALE, i, FLY_FRAMES)], FLY_MS)
    for i in range(FLIP_FRAMES):
        reel.add([_turn(p, *yours, SCALE, i, FLIP_FRAMES)], FLIP_MS)
    reel.hold(SUSPENSE_MS - FLIP_MS)
    mine = _c(p, *yours, flip=1)
    for i in range(FLY_FRAMES):
        reel.add([_fly(d, (CENTER_X, BANK_Y), SCALE, i, FLY_FRAMES), mine], FLY_MS)
    reel.hold(SUSPENSE_MS)
    for i in range(FLIP_FRAMES):
        reel.add([_turn(d, CENTER_X, BANK_Y, SCALE, i, FLIP_FRAMES), mine], FLIP_MS)
    _resolve_side(reel)
    seed = _seed(game)
    diff = rank_value(p) - rank_value(d)

    def cards(t: float) -> list[dict[str, Any]]:
        if diff == 0:
            return _tie_cards(game, t)
        return [
            _c(
                d,
                CENTER_X,
                BANK_Y,
                flip=1,
                glow=0.9 * t if diff < 0 else 0,
                dim=0.35 * t * (diff > 0),
            ),
            _c(
                p,
                CENTER_X,
                YOU_Y,
                flip=1,
                glow=0.9 * t if diff > 0 else 0,
                dim=0.35 * t * (diff < 0),
            ),
        ]

    result = None if diff == 0 else game.result
    winner = None if diff == 0 else (CENTER_X, YOU_Y if diff > 0 else BANK_Y)
    _end(reel, cards, result, seed, winner if diff > 0 else None)
    reel.frames[-1]["ms"] = 60_000
    return reel.frames


def war_states(game: WarGame) -> list[dict[str, Any]]:
    """La animación de ir a la guerra: parte del empate y acaba con el resultado.

    Args:
        game: La mano tras `go_to_war` (cuatro cartas y tres quemadas).
    """
    (p0, p1), (d0, d1) = game.player[:2], game.dealer[:2]
    reel = _Reel(game)
    # Arranca igual que acaba «Repartir» con empate: la misma mesa y el mismo cartel.
    _tie_frame(reel, 120)

    def old(t: float) -> list[dict[str, Any]]:
        e = _ease(t)
        x, s = _lerp(CENTER_X, OLD_X, e), _lerp(SCALE, WAR_SCALE, e)
        return [
            _c(d0, x, BANK_Y, scale=s, flip=1, glow=0.55 * (1 - e), dim=0.3 * e),
            _c(p0, x, YOU_Y, scale=s, flip=1, glow=0.55 * (1 - e), dim=0.3 * e),
        ]

    war_banner = {"kind": "war", "title": "¡A LA GUERRA!", "sub": None, "icon": "swords"}
    reel.result = {
        "kind": "tie",
        "title": "¡GUERRA!",
        "amount": None,
        "sub": f"OTRA FICHA IGUAL\nY SE QUEMAN {len(game.burned)}",
    }
    reel.slots = [(NEW_X, BANK_Y, WAR_SCALE), (NEW_X, YOU_Y, WAR_SCALE)]
    for k in range(3):
        reel.war = (k + 1) / 3
        reel.banner_at(
            {**war_banner, "kind": "tie", "title": "EMPATE", "icon": None}, 0.5 - 0.25 * k
        )
        if k == 2:
            reel.banner_at(war_banner, 0.5)
        reel.add(old((k + 1) / 3), 30)
    done = old(1.0)
    for k, a in enumerate((0.9, 1.12, 1.0)):
        reel.banner_at(war_banner, a, dx=(3 if k % 2 else -3))
        reel.add(done, 38)
    # La banca quema las cartas boca abajo, una a una, a un lado.
    reel.burn = True
    pile: list[dict[str, Any]] = []
    for j, card in enumerate(game.burned):
        spot = (BURN_XY[0] + 3 * j, BURN_XY[1] + 4 * j)
        rot = -0.15 + 0.12 * j
        for i in range(4):
            reel.banner_at(war_banner, 1.0, dx=(3 if i % 2 else -3))
            reel.add([*done, *pile, _fly(card, spot, BURN_SCALE, i, 4, rot)], 28)
        pile.append(_c(None, *spot, scale=BURN_SCALE, rot=rot))
    reel.add([*done, *pile], 60)
    for a in (0.6, 0.2):
        reel.banner_at(war_banner, a)
        reel.add([*done, *pile], 30)
    reel.banner_at(None, 0)
    # Las dos de la guerra: primero la tuya, luego la de la banca.
    mine_at, bank_at = (NEW_X, YOU_Y), (NEW_X, BANK_Y)
    for i in range(4):
        reel.add([*done, *pile, _fly(p1, mine_at, WAR_SCALE, i, 4)], 30)
    for i in range(3):
        reel.add([*done, *pile, _turn(p1, *mine_at, WAR_SCALE, i, 3)], 38)
    reel.hold(SUSPENSE_MS - 38)
    mine = _c(p1, *mine_at, scale=WAR_SCALE, flip=1)
    for i in range(4):
        reel.add([*done, *pile, mine, _fly(d1, bank_at, WAR_SCALE, i, 4)], 30)
    reel.hold(SUSPENSE_MS)
    for i in range(3):
        reel.add([*done, *pile, mine, _turn(d1, *bank_at, WAR_SCALE, i, 3)], 38)
    diff = rank_value(p1) - rank_value(d1)

    def cards(t: float) -> list[dict[str, Any]]:
        return [
            *done,
            *pile,
            _c(
                d1,
                *bank_at,
                scale=WAR_SCALE,
                flip=1,
                glow=(0.9 if diff < 0 else 0.5 if diff == 0 else 0) * t,
            ),
            _c(
                p1,
                *mine_at,
                scale=WAR_SCALE,
                flip=1,
                glow=(0.9 if diff > 0 else 0.5 if diff == 0 else 0) * t,
            ),
        ]

    _end(reel, cards, game.result, _seed(game), (NEW_X, YOU_Y) if diff >= 0 else None)
    reel.frames[-1]["ms"] = 60_000
    return reel.frames


def surrender_states(game: WarGame) -> list[dict[str, Any]]:
    """La animación de rendirse: la bandera blanca y las cartas se apagan.

    Args:
        game: La mano tras `surrender` (el empate sigue en la mesa).
    """
    reel = _Reel(game)
    _tie_frame(reel, 160)
    _outcome(reel, game.result)
    pops = (0.4, 0.8, 1.1, 1.0, 1.0)
    for k, a in enumerate(pops):
        reel.banner_at(reel.banner_spec, a)
        t = min(1.0, (k + 1) / 3)
        d, p = _tie_cards(game, 1 - t)
        reel.add([{**d, "dim": round(0.6 * t, 3)}, {**p, "dim": round(0.6 * t, 3)}], 40)
    reel.frames[-1]["ms"] = 60_000
    return reel.frames


# -- Pillow: el dibujo de reserva --------------------------------------------------------------

RESULT_BADGES = {
    Result.WIN: "GANAS",
    Result.LOSE: "PIERDES",
    Result.SURRENDER: "RENDICIÓN",
    Result.WAR_WIN: "GUERRA GANADA",
    Result.WAR_TIE: "GUERRA EMPATADA",
    Result.WAR_LOSE: "GUERRA PERDIDA",
}


def _title(who: str, cards: list[Card]) -> str:
    return f"{who} · " + " → ".join(card.label for card in cards)


def render_table(renderer: CardRenderer, game: WarGame, *, burning: bool = False) -> bytes:
    """PNG de la mesa con el `CardRenderer` del blackjack (el plan C de la escena).

    Args:
        burning: Enseña solo las primeras cartas (como antes de salir las de la guerra).
    """
    count = 1 if burning else len(game.player)
    player, dealer = game.player[:count], game.dealer[:count]
    badge = None
    good = False
    if game.result is not None and not burning:
        net = main_net(game)
        badge = RESULT_BADGES[game.result]
        if net > 0:
            badge += " +" + f"{net:,}".replace(",", ".")
        good = net > 0
    return renderer.render(
        dealer=dealer,
        hide_hole=False,
        dealer_title=_title("BANCA", dealer),
        hands=[
            HandView(
                cards=player,
                title=_title("TÚ", player),
                active=game.awaiting_choice or burning,
                badge=badge,
                badge_good=good,
            )
        ],
    )


class WarScene:
    """Dibuja la Guerra con Node, si no con Chromium y, si no, con Pillow (`fallback`).

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
        self.painter = NodeScene(SCENE, name="El pintor de la guerra", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de la guerra",
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

    async def _media(
        self, game: WarGame, states: list[dict[str, Any]], skin: dict[str, object] | None
    ) -> Media:
        patches = await self._frames(meta_state(skin), states)
        if patches is None:
            png = await asyncio.to_thread(render_table, self.fallback, game)
            return Media(gif=None, png=png, seconds=0.0)
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations(states)))

    async def deal(self, game: WarGame, *, skin: dict[str, object] | None = None) -> Media:
        """El GIF de repartir (y el PNG de cómo queda la mesa), con el tapete `skin`."""
        return await self._media(game, deal_states(game), skin)

    async def war(self, game: WarGame, *, skin: dict[str, object] | None = None) -> Media:
        """El GIF de ir a la guerra tras un empate (`game` ya ha pasado por `go_to_war`)."""
        return await self._media(game, war_states(game), skin)

    async def surrender(self, game: WarGame, *, skin: dict[str, object] | None = None) -> Media:
        """El GIF de rendirse tras un empate (`game` ya ha pasado por `surrender`)."""
        return await self._media(game, surrender_states(game), skin)

    async def still(self, game: WarGame, *, skin: dict[str, object] | None = None) -> bytes:
        """El PNG de la mesa como ha quedado la mano, sin animar (al caducar la mesa)."""
        if game.result is Result.SURRENDER:
            states = surrender_states(game)
        elif game.went_to_war:
            states = war_states(game)
        else:
            states = deal_states(game)
        patches = await self._frames(meta_state(skin), [{**states[-1], "full": True}])
        if patches is None:
            return await asyncio.to_thread(render_table, self.fallback, game)
        return await asyncio.to_thread(patch_png, patches[0])
