"""Efectos de los asesores de Legislatura (los comodines de Balatro).

Primera tanda: uno o varios por bloque, elegidos para que cada tipo de gancho de
`bot.services.legislatura.effects` tenga al menos un uso real y para que los
bloques se crucen entre sí. Las fichas (nombre, precio, texto) están en
`bot.services.legislatura.catalog`; aquí solo va lo que hacen.

Cada clase guarda su estado en `slot.state` (un diccionario que se guarda con
la partida), nunca en atributos propios: hay una sola instancia por asesor
para todas las partidas.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from bot.services.legislatura.cards import Card, Suit
from bot.services.legislatura.effects import Effect, ScoreContext
from bot.services.legislatura.items import Bloc, ItemDef

if TYPE_CHECKING:
    from bot.services.legislatura.run import AdvisorSlot, Run


class PerroSanxe(Effect):
    """🇪🇸 Legendario. Cada oro que puntúa da ×1,5 mult; al acabar la sesión se queda el 21 %."""

    def on_card_scored(self, ctx: ScoreContext, slot: AdvisorSlot | None, card: Card) -> None:
        if card.has_suit(Suit.OROS):
            ctx.times_mult("perro_sanxe", 1.5, card)

    def on_round_end(self, run: Run, slot: AdvisorSlot | None) -> None:
        cut = math.floor(run.fondos * 0.21)
        if cut:
            run.fondos -= cut
            run.say(f"🐕 Perro Sanxe se queda {cut} fondos (el 21 %, como el IVA).")


class PuertaGiratoria(Effect):
    """🇪🇸 Vender cualquier asesor da el doble."""

    def sell_value(self, run: Run, slot: AdvisorSlot, sold: AdvisorSlot, value: int) -> int:
        return value * 2


class BoicotEurovision(Effect):
    """🇪🇸 +5 mult. No deja tener asesores de Israel y echa a los que haya."""

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        ctx.add_mult("boicot_eurovision", 5)

    def blocks(self, run: Run, slot: AdvisorSlot | None, item: ItemDef) -> str | None:
        if item.bloc is Bloc.ISRAEL and item.category.value == "asesor":
            return "RTVE ha boicoteado Eurovisión: nada de asesores israelíes."
        return None

    def on_acquire(self, run: Run, slot: AdvisorSlot) -> None:
        for other in list(run.advisors):
            if other.uid != slot.uid and run.item(other.key).bloc is Bloc.ISRAEL:
                run.remove_advisor(other.uid)
                run.say(f"📺 Boicot: {run.item(other.key).name} se va sin cobrar.")


class Aforado(Effect):
    """🇪🇸 Si cae el Gobierno, lo salva una vez y desaparece (el Supremo tarda años)."""

    def saves(self, run: Run, slot: AdvisorSlot | None) -> bool:
        if slot is None:
            return False
        run.remove_advisor(slot.uid)
        run.say("⚖️ El aforado recurre al Supremo y la sesión se da por buena.")
        return True


class VetoHungria(Effect):
    """🇪🇺 +10 mult, pero 1 de cada 8 jugadas Hungría veta y no suma nada."""

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        if ctx.run.rng.chance("asesor:veto_hungria", 8):
            ctx.note("veto_hungria", "🇭🇺 Veto de Hungría: sin unanimidad no hay mult.")
            return
        ctx.add_mult("veto_hungria", 10)


class MultaCompetencia(Effect):
    """🇪🇺 Al acabar la sesión, +3 fondos por cada asesor de EE. UU. (Bruselas multa a Big Tech)."""

    def on_round_end(self, run: Run, slot: AdvisorSlot | None) -> None:
        fined = len(run.advisors_of(Bloc.EEUU))
        if fined:
            run.fondos += 3 * fined
            run.say(f"🇪🇺 Multa de Competencia a {fined} empresa(s) americana(s): +{3 * fined}.")


class Aranceles(Effect):
    """🇺🇸 Lo chino cuesta un 50 % más en la tienda; sin asesores chinos, ×1,5 mult."""

    def price(self, run: Run, slot: AdvisorSlot | None, item: ItemDef, price: int) -> int:
        if item.bloc is Bloc.CHINA:
            return math.ceil(price * 1.5)
        return price

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        if not ctx.run.advisors_of(Bloc.CHINA):
            ctx.times_mult("aranceles", 1.5)


class CumbreOtan(Effect):
    """🇺🇸 +2 mult por cada otro asesor (aliados), pero cobra el 5 % de los fondos.

    Con Perro Sanxe en la mesa paga el 2,1 %, como España.
    """

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        allies = len(ctx.run.advisors) - 1
        ctx.add_mult("cumbre_otan", 2 * allies)

    def on_round_end(self, run: Run, slot: AdvisorSlot | None) -> None:
        if run.has_advisor("perro_sanxe"):
            cut = math.floor(run.fondos * 0.021)
            run.say(f"🛡️ La OTAN pide el 5 %; Perro Sanxe paga el 2,1 % ({cut}).")
        else:
            cut = math.ceil(run.fondos * 0.05)
            run.say(f"🛡️ Cuota de la OTAN: {cut} fondos.")
        run.fondos -= cut


class FabricaShenzhen(Effect):
    """🇨🇳 La primera carta que puntúa se activa una vez más (copia barata)."""

    def retriggers(self, ctx: ScoreContext, slot: AdvisorSlot | None, card: Card) -> int:
        return 1 if ctx.scoring and ctx.scoring[0].id == card.id else 0


class CreditoSocial(Effect):
    """🇨🇳 +1 mult por cada jugada (se acumula); cada descarte resta 2 para siempre."""

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        if slot is not None:
            ctx.add_mult("credito_social", int(slot.state.get("puntos", 0)))

    def after_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        if slot is not None:
            slot.state["puntos"] = int(slot.state.get("puntos", 0)) + 1

    def on_discard(self, run: Run, slot: AdvisorSlot | None, cards: list[Card]) -> None:
        if slot is not None:
            slot.state["puntos"] = max(0, int(slot.state.get("puntos", 0)) - 2)


class Asentamiento(Effect):
    """🇮🇱 Cada sesión se anexiona una carta del mazo (sale) y suma +3 mult por carta anexionada."""

    def on_round_end(self, run: Run, slot: AdvisorSlot | None) -> None:
        if slot is None or len(run.cards) <= run.hand_size:
            return
        victim = run.rng.choice("asesor:asentamiento", sorted(run.cards))
        label = run.cards[victim].label
        run.destroy_card(victim)
        slot.state["anexionadas"] = int(slot.state.get("anexionadas", 0)) + 1
        run.say(f"🏗️ Asentamiento: el {label} pasa a ser «territorio en disputa».")

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        if slot is not None:
            ctx.add_mult("asentamiento", 3 * int(slot.state.get("anexionadas", 0)))


class PaqueteAyuda(Effect):
    """🇮🇱 +4 fondos al acabar la sesión, solo si hay un asesor de EE. UU. que lo apruebe."""

    def on_round_end(self, run: Run, slot: AdvisorSlot | None) -> None:
        if run.advisors_of(Bloc.EEUU):
            run.fondos += 4
            run.say("📦 El Congreso de EE. UU. aprueba otro paquete de ayuda: +4.")


class CunadoConspiranoico(Effect):
    """🇪🇸 Se cree todos los bulos y la broma es él.

    Cada jugada que no basta para ganar la sesión le da la razón: +3 mult para
    siempre («¡lo sabía!»). Cuando la sesión sale bien, pierde la mitad, porque
    eso es justo lo que quieren que pienses. Con un asesor de Israel en la mesa
    no suma nada: se pasa la sesión vigilándolo.
    """

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        if slot is None:
            return
        quotes = ctx.run.item(slot.key).quotes
        if quotes:
            ctx.note(slot.key, ctx.run.rng.choice("asesor:cunado", quotes))
        if ctx.run.advisors_of(Bloc.ISRAEL):
            ctx.note(slot.key, "🔍 No suma: está demasiado ocupado vigilando al asesor israelí.")
            return
        ctx.add_mult(slot.key, int(slot.state.get("razon", 0)))

    def after_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        if slot is not None and ctx.run.round_score + ctx.total < ctx.run.target:
            slot.state["razon"] = int(slot.state.get("razon", 0)) + 3

    def on_round_end(self, run: Run, slot: AdvisorSlot | None) -> None:
        if slot is not None and slot.state.get("razon"):
            slot.state["razon"] = int(slot.state["razon"]) // 2
            run.say("🤔 El cuñado pierde convicción: ganar es justo lo que quieren que pienses.")
