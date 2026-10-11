"""Cadena de puntuación de una jugada de Legislatura.

El orden es el de Balatro, porque es el que hace que colocar bien los
asesores importe:

1. Los asesores (y el jefe) cambian las reglas de mirar la jugada.
2. Votos y mult de base de la combinación, a su nivel.
3. Cada carta que puntúa, de izquierda a derecha: sus votos, su etiqueta, su
   edición, su sello y los asesores que miran cartas. Se repite tantas veces
   como activaciones tenga (Voto delegado, Fábrica de Shenzhen…).
4. Cada carta que se queda en la mano: Funcionario y asesores que miran la mano.
5. Cada asesor, de izquierda a derecha: su edición (si suma) y su efecto «por
   jugada»; la edición Patrocinado multiplica después del efecto.
6. El jefe de la moción, si tiene algo que decir de la jugada entera.
7. Lo que se rompe (Promesas) y los contadores que crecen (`after_hand`).

Todo pasa por `ScoreContext`, que apunta cada paso para enseñarlo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.services.legislatura.cards import Card, Edition, Enhancement, Seal, rank_votes
from bot.services.legislatura.effects import ScoreContext
from bot.services.legislatura.hands import HANDS, Rules, evaluate

if TYPE_CHECKING:
    from bot.services.legislatura.run import Run

#: Probabilidad (1 de cada N) de que se rompa una Promesa electoral al puntuar.
PROMISE_BREAK_ONE_IN = 4


def _score_card(ctx: ScoreContext, card: Card) -> None:
    """Una activación de una carta jugada."""
    run = ctx.run
    if not card.is_stone:
        ctx.add_votes("carta", rank_votes(card.rank), card)
    match card.enhancement:
        case Enhancement.ENCHUFADO:
            ctx.add_votes("carta", 30, card)
        case Enhancement.MITIN:
            ctx.add_mult("carta", 4, card)
        case Enhancement.PROMESA:
            ctx.times_mult("carta", 2, card)
        case Enhancement.LADRILLO:
            ctx.add_votes("carta", 50, card)
        case Enhancement.LOTERIA:
            if run.rng.chance("carta:loteria:mult", 5):
                ctx.add_mult("carta", 20, card)
            if run.rng.chance("carta:loteria:fondos", 15):
                ctx.earn("carta", 20, card)
        case _:
            pass
    match card.edition:
        case Edition.FONDOS_UE:
            ctx.add_votes("carta", 50, card)
        case Edition.VIRAL:
            ctx.add_mult("carta", 10, card)
        case Edition.PATROCINADO:
            ctx.times_mult("carta", 1.5, card)
        case _:
            pass
    if card.seal is Seal.BOE:
        ctx.earn("carta", 3, card)
    for slot in run.advisors:
        run.effect_of(slot).on_card_scored(ctx, slot, card)


def _activations(ctx: ScoreContext, card: Card) -> int:
    """Cuántas veces se activa una carta (1 + las repeticiones)."""
    extra = 1 if card.seal is Seal.VOTO_DELEGADO else 0
    for slot in ctx.run.advisors:
        extra += ctx.run.effect_of(slot).retriggers(ctx, slot, card)
    return 1 + extra


def score(run: Run, played: list[Card]) -> ScoreContext:
    """Puntúa `played` con la mano y la mesa actuales. No saca ni repone cartas.

    Returns:
        El contexto con el total (`ctx.total`), la cuenta paso a paso y las
        cartas que se rompen (`ctx.destroyed`), para que la partida lo aplique.
    """
    boss = run.boss_effect
    flags = {"short": False, "gaps": False, "all_score": False}
    for slot in run.advisors:
        run.effect_of(slot).rules(run, slot, flags)
    if boss:
        boss.rules(run, None, flags)
    evaluation = evaluate(played, Rules(**flags))
    by_id = {c.id: c for c in played}
    held = [run.cards[i] for i in run.hand if i not in by_id]
    ctx = ScoreContext(
        run=run,
        evaluation=evaluation,
        played=list(played),
        scoring=[by_id[i] for i in evaluation.scoring],
        held=held,
    )

    votes, mult = HANDS[evaluation.hand].at_level(run.hand_levels.get(evaluation.hand, 1))
    ctx.add_votes("base", votes)
    ctx.add_mult("base", mult)

    for card in ctx.scoring:
        if boss and boss.debuffs(run, card):
            ctx.note("jefe", "🚫 No puntúa", card)
            continue
        for _ in range(_activations(ctx, card)):
            _score_card(ctx, card)
        if card.enhancement is Enhancement.PROMESA and run.rng.chance(
            "carta:promesa", PROMISE_BREAK_ONE_IN
        ):
            ctx.destroyed.add(card.id)
            ctx.note("carta", "💔 Promesa incumplida: la carta se rompe", card)

    for card in held:
        if boss and boss.debuffs(run, card):
            continue
        for _ in range(_activations(ctx, card)):
            if card.enhancement is Enhancement.FUNCIONARIO:
                ctx.times_mult("carta", 1.5, card)
            for slot in run.advisors:
                run.effect_of(slot).on_held(ctx, slot, card)

    for slot in run.advisors:
        if slot.edition is Edition.FONDOS_UE:
            ctx.add_votes(slot.key, 50)
        elif slot.edition is Edition.VIRAL:
            ctx.add_mult(slot.key, 10)
        run.effect_of(slot).on_hand(ctx, slot)
        if slot.edition is Edition.PATROCINADO:
            ctx.times_mult(slot.key, 1.5)
    if boss:
        boss.on_hand(ctx, None)

    for slot in list(run.advisors):
        run.effect_of(slot).after_hand(ctx, slot)
    return ctx
