"""Qué se ve en Legislatura: los estados que pinta la escena, sin pintar nada.

La partida se enseña en **dos imágenes**, como las dos mitades de la pantalla de
Balatro:

- **Panel** (izquierda, `PANEL_W`×`H`): la sesión y su objetivo, el jefe, los
  votos de la sesión, la última combinación con sus votos × mult, jugadas,
  descartes, fondos, periodo y prima. Siempre es un PNG.
- **Mesa** (derecha, `TABLE_W`×`H`): asesores arriba, consumibles, la mano y el
  mazo. Un PNG quieto entre jugadas y un GIF cuando se puntúa, si da tiempo a
  pintarlo (`should_animate`); si no, PNG del final, como el blackjack.

Por qué separadas: la mesa es lo que se anima y el panel casi nunca cambia a la
vez, así que el GIF solo lleva la mesa (menos píxeles que comprimir) y el panel
se cambia solo cuando hace falta, conservando el adjunto de la mesa al editar.
Y por qué el panel va **después** del GIF: si se subiera ya con los votos
nuevos, destriparía la cuenta mientras el GIF aún la está haciendo. El cog
manda primero el panel de antes con el GIF y, al acabar la animación
(`Timeline.seconds` más el margen de siempre), cambia solo el panel.

Las dos imágenes las pinta una sola escena de canvas (`assets/legislatura/
escena.html`, con `setup` y `renderFrames` como las demás) en Node y, si no
hay Node, en Chromium: un solo proceso de pintor para las dos vistas. Cada
estado lleva `view` («panel» o «mesa»). Aquí solo se decide qué se ve y en qué
fotograma; la escena no decide nada, como en la moneda o el pollo.

Marcar cartas para jugar no repinta nada: solo cambian los botones (contestar a
cada clic subiendo una imagen sería lento). La mesa se repinta al jugar, al
descartar y al cambiar de fase.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from bot.services.legislatura.cards import Card
from bot.services.legislatura.catalog import BOSSES, ITEMS, STAKES
from bot.services.legislatura.cosmetics import Skin
from bot.services.legislatura.effects import ScoreContext, StepKind
from bot.services.legislatura.hands import HANDS, HandType
from bot.services.legislatura.run import PERIODS, Run

#: Alto común de las dos imágenes, para que queden a la misma altura una al lado de otra.
H = 450
PANEL_W = 240
TABLE_W = 800
#: Duración de un fotograma de la animación (20 fps: suficiente para cartas que suben y bajan).
FRAME_MS = 50
#: Fotogramas de cada paso de la cuenta mientras caben en el presupuesto.
STEP_FRAMES = 4
#: Fotogramas fijos del principio (las cartas suben al centro) y del final (el total).
INTRO_FRAMES = 6
OUTRO_FRAMES = 8
#: Tope de fotogramas de una jugada: con muchos pasos, cada uno dura menos.
MAX_FRAMES = 72
#: Lo último del GIF se queda quieto (el total) hasta que llega el panel nuevo.
FINAL_FRAME_MS = 60_000
#: Tiempo máximo que se acepta esperar al dibujo de un GIF antes de enseñar la jugada.
#: Por encima, se manda el PNG del final con la cuenta en texto.
ANIMATION_BUDGET_MS = 1_500


def _card(card: Card, *, debuffed: bool = False) -> dict[str, Any]:
    """Una carta tal cual la pinta la escena."""
    return {
        "id": card.id,
        "rank": card.rank,
        "suit": card.suit.value,
        "enh": card.enhancement.value if card.enhancement else None,
        "ed": card.edition.value if card.edition else None,
        "seal": card.seal.value if card.seal else None,
        "off": debuffed,
    }


def _skins(skins: Mapping[str, Skin] | None) -> dict[str, Any]:
    """Lo que la escena necesita de los aspectos elegidos (`cosmetics.resolve`)."""
    skins = skins or {}
    felt = skins.get("tapete")
    return {
        "felt": list(map(list, felt.felt)) if felt and felt.felt else None,
        "back": skins["dorso"].key if "dorso" in skins else None,
        "art": {
            where.split(":", 1)[1]: skin.key
            for where, skin in skins.items()
            if where.startswith("asesor:")
        },
    }


def meta_state(skins: Mapping[str, Skin] | None = None) -> dict[str, Any]:
    """`setup` de la escena: tamaños y aspectos (no cambian en toda la partida)."""
    return {"h": H, "panel_w": PANEL_W, "table_w": TABLE_W, "frame_ms": FRAME_MS, **_skins(skins)}


def panel_state(
    run: Run, *, round_score: int | None = None, hand: HandType | None = None
) -> dict[str, Any]:
    """El panel de la izquierda.

    Args:
        run: La partida.
        round_score: Votos que enseñar (los de antes de una jugada mientras se
            anima, para no destripar el resultado). Por defecto, los actuales.
        hand: Combinación que enseñar con sus votos × mult; por defecto, la última jugada.
    """
    shown = hand or (HandType(run.last_hand) if run.last_hand else None)
    level = run.hand_levels.get(shown.value, 1) if shown else 1
    votes, mult = HANDS[shown].at_level(level) if shown else (0, 0)
    boss = BOSSES[run.boss] if run.boss else None
    return {
        "view": "panel",
        "phase": run.phase.value,
        "session": run.session_name,
        "boss": {"name": boss.name, "text": boss.text, "bloc": boss.bloc.value}
        if boss and run.session == 2
        else None,
        "target": run.target,
        "score": run.round_score if round_score is None else round_score,
        "hand": {"name": HANDS[shown].name, "level": level, "votes": votes, "mult": mult}
        if shown
        else None,
        "hands": run.hands_left,
        "discards": run.discards_left,
        "fondos": run.fondos,
        "period": run.period,
        "periods": PERIODS,
        "extended": run.extended,
        "stake": STAKES[run.stake].name,
    }


def table_state(run: Run, *, selected: Collection[int] = ()) -> dict[str, Any]:
    """La mesa quieta: asesores, consumibles, mano y mazo."""
    boss = run.boss_effect
    return {
        "view": "mesa",
        "phase": run.phase.value,
        "advisors": [
            {
                "uid": s.uid,
                "key": s.key,
                "name": ITEMS[s.key].name,
                "bloc": ITEMS[s.key].bloc.value,
                "ed": s.edition.value if s.edition else None,
            }
            for s in run.advisors
        ],
        "max_advisors": run.max_advisors,
        "consumables": [
            {"key": k, "name": ITEMS[k].name, "kind": ITEMS[k].category.value.upper()}
            for k in run.consumables
        ],
        "max_consumables": run.max_consumables,
        "hand": [
            {
                **_card(run.cards[i], debuffed=bool(boss and boss.debuffs(run, run.cards[i]))),
                "up": i in selected,
            }
            for i in run.hand
        ],
        "deck": len(run.draw),
        "deck_total": len(run.cards),
        "hide_suits": bool(boss and boss.hides_suits),
    }


@dataclass(frozen=True, slots=True)
class Timeline:
    """Animación de una jugada: los estados de la mesa y cuánto dura cada uno."""

    frames: list[dict[str, Any]]
    durations: list[int]

    @property
    def seconds(self) -> float:
        """Lo que dura hasta el total (sin contar el último fotograma, que se queda quieto)."""
        return sum(self.durations[:-1]) / 1000


def score_timeline(before: dict[str, Any], played: list[int], ctx: ScoreContext) -> Timeline:
    """La cuenta de una jugada, paso a paso, sobre la mesa de antes de jugar.

    Args:
        before: `table_state` sacado justo antes de `Run.play` (con las cartas
            jugadas aún en la mano).
        played: Ids jugados, en orden.
        ctx: Lo que devuelve la jugada (`PlayOutcome.context`).

    Cada paso de `ctx.steps` señala su origen (una carta o un asesor) y saca su
    cartel («+30 votos», «×1,5 mult»); la ficha de votos × mult va sumando. Con
    muchos pasos, cada uno dura menos fotogramas para no pasar de `MAX_FRAMES`.
    """
    steps = [s for s in ctx.steps if s.source != "base"]
    base = [s for s in ctx.steps if s.source == "base"]
    votes = sum(int(s.amount) for s in base if s.kind is StepKind.VOTES)
    mult = sum(s.amount for s in base if s.kind is StepKind.MULT)
    room = MAX_FRAMES - INTRO_FRAMES - OUTRO_FRAMES
    per_step = max(1, min(STEP_FRAMES, room // max(len(steps), 1)))
    # Si ni a un fotograma por paso caben, se agrupan pasos seguidos en un mismo fotograma.
    group = max(1, math.ceil(len(steps) / room)) if len(steps) > room else 1

    common = {
        "view": "mesa",
        "advisors": before["advisors"],
        "max_advisors": before["max_advisors"],
        "consumables": before["consumables"],
        "max_consumables": before["max_consumables"],
        "deck": before["deck"],
        "deck_total": before["deck_total"],
        "hide_suits": before["hide_suits"],
        "hand": [c for c in before["hand"] if c["id"] not in played],
        "played": [c for c in before["hand"] if c["id"] in played],
        "scoring": [c.id for c in ctx.scoring],
        "hand_name": HANDS[ctx.evaluation.hand].name,
    }
    frames: list[dict[str, Any]] = []
    durations: list[int] = []

    def add(state: dict[str, Any], count: int, ms: int = FRAME_MS) -> None:
        for n in range(count):
            frames.append({**common, **state, "f": n, "of": count})
            durations.append(ms)

    add({"stage": "intro", "votes": votes, "mult": mult}, INTRO_FRAMES)
    for start in range(0, len(steps), group):
        chunk = steps[start : start + group]
        for step in chunk:
            if step.kind is StepKind.VOTES:
                votes += int(step.amount)
            elif step.kind is StepKind.MULT:
                mult += step.amount
            elif step.kind is StepKind.XMULT:
                mult *= step.amount
        last = chunk[-1]
        add(
            {
                "stage": "step",
                "card": last.card_id,
                "advisor": last.source if last.source in ITEMS else None,
                "kind": last.kind.value,
                "text": last.text,
                "votes": votes,
                "mult": round(mult, 2),
            },
            per_step,
        )
    add(
        {"stage": "total", "votes": ctx.votes, "mult": round(ctx.mult, 2), "total": ctx.total},
        OUTRO_FRAMES,
    )
    durations[-1] = FINAL_FRAME_MS
    return Timeline(frames, durations)


def should_animate(frames: int, ms_per_frame: float | None) -> bool:
    """Si se pinta el GIF o se va directo al PNG del final.

    Args:
        frames: Fotogramas de la animación.
        ms_per_frame: Lo que tarda de media un fotograma en este bot (pintar y
            montar el GIF), medido por el pintor; `None` si aún no hay medida
            (se prueba con el GIF y la primera jugada da la medida).

    Se decide antes de pintar y no cortando a medias: cortar un dibujo en Node
    obliga a matar y arrancar sus procesos (CLAUDE.md, «Rendimiento en el NAS»).
    """
    if ms_per_frame is None:
        return True
    return frames * ms_per_frame <= ANIMATION_BUDGET_MS
