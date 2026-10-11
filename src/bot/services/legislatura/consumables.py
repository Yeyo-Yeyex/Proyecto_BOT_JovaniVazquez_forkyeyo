"""Lo que hacen las normativas, las cloacas y los tratados de Legislatura.

Cada bloque legisla a su manera y eso es la broma:

- España, por decreto ley, que a veces no convalida el Congreso.
- La UE, por directiva, que tarda en transponerse (efecto diferido).
- EE. UU., por orden ejecutiva, que a veces tumba un juez federal.
- China, por plan quinquenal, que llega cuando llega (diferido en jugadas).
- Israel, como «nación startup»: copia lo que funciona.

Los efectos diferidos se apuntan en `Run.pending` y los resuelve la partida
llamando a `DELAYED[clave]` cuando se cumple su plazo.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from bot.services.legislatura.cards import Card, Enhancement, Suit
from bot.services.legislatura.items import Bloc, GameError

if TYPE_CHECKING:
    from bot.services.legislatura.run import Run


def decreto_ley(run: Run, cards: list[Card]) -> str:
    """🇪🇸 Pasa a oros las cartas elegidas; 1 de cada 3 veces el Congreso no lo convalida."""
    if run.rng.chance("normativa:decreto_ley", 3):
        return "🏛️ El Congreso no convalida el decreto. Ni Junts ni nadie."
    for card in cards:
        card.suit = Suit.OROS
    return f"📜 Decreto ley: {len(cards)} carta(s) pasan a oros."


def directiva_europea(run: Run, cards: list[Card]) -> str:
    """🇪🇺 Las cartas elegidas serán Funcionario dentro de 2 sesiones (transposición)."""
    run.defer("directiva_europea", cards, rounds=2)
    return "🇪🇺 Directiva aprobada. Entra en vigor dentro de 2 sesiones, si España la traspone."


def orden_ejecutiva(run: Run, cards: list[Card]) -> str:
    """🇺🇸 Las cartas elegidas pasan a Promesa electoral; 1 de cada 4 la tumba un juez."""
    if run.rng.chance("normativa:orden_ejecutiva", 4):
        return "⚖️ Un juez federal de Texas suspende la orden ejecutiva."
    for card in cards:
        card.enhancement = Enhancement.PROMESA
    return f"🖊️ Orden ejecutiva firmada con rotulador gordo: {len(cards)} Promesa(s)."


def plan_quinquenal(run: Run, cards: list[Card]) -> str:
    """🇨🇳 Las cartas elegidas serán Enchufado dentro de 5 jugadas."""
    run.defer("plan_quinquenal", cards, hands=5)
    return "🇨🇳 Plan quinquenal aprobado por unanimidad. Resultados en 5 jugadas."


def nacion_startup(run: Run, cards: list[Card]) -> str:
    """🇮🇱 Copia la carta elegida (con su etiqueta, edición y sello) a la baraja."""
    original = cards[0]
    copy = run.add_card(original.rank, original.suit, original.enhancement)
    copy.edition, copy.seal = original.edition, original.seal
    return f"🚀 Nación startup: el {original.label} levanta una ronda y sale una copia."


def rescate_troika(run: Run, cards: list[Card]) -> str:
    """🇪🇺 Cloaca: +20 fondos y una jugada menos por sesión para el resto de la partida."""
    run.fondos += 20
    run.base_hands = max(1, run.base_hands - 1)
    return "🏦 Rescate de la troika: +20 fondos y recortes hasta el final (−1 jugada)."


def guerra_arancelaria(run: Run, cards: list[Card]) -> str:
    """🇺🇸 Cloaca: duplica los asesores de EE. UU. que quepan y destruye los chinos."""
    gone = run.advisors_of(Bloc.CHINA)
    for slot in gone:
        run.remove_advisor(slot.uid)
    copied = 0
    for slot in run.advisors_of(Bloc.EEUU):
        if run.add_advisor(slot.key, slot.edition) is not None:
            copied += 1
    return f"💥 Guerra arancelaria: {len(gone)} asesor(es) chino(s) fuera, {copied} duplicado(s)."


def fondo_reptiles(run: Run, cards: list[Card]) -> str:
    """🇪🇸 Cloaca: un asesor legendario al azar (si cabe) y te quedas sin fondos."""
    legendary = run.legendary_keys()
    if not legendary:
        return "🐍 El fondo de reptiles está vacío."
    key = run.rng.choice("cloaca:fondo_reptiles", legendary)
    if run.add_advisor(key) is None:
        raise GameError("No te cabe otro asesor: vende uno antes.")
    run.fondos = 0
    return f"🐍 Del fondo de reptiles sale {run.item(key).name}. Los fondos, ni se sabe."


def _delayed_enhance(enhancement: Enhancement) -> Callable[[Run, list[Card]], str]:
    def apply(run: Run, cards: list[Card]) -> str:
        for card in cards:
            card.enhancement = enhancement
        return f"{len(cards)} carta(s) pasan a {enhancement.value}."

    return apply


#: Efectos diferidos: clave de la normativa → lo que pasa al cumplirse el plazo.
DELAYED: dict[str, Callable[[Run, list[Card]], str]] = {
    "directiva_europea": _delayed_enhance(Enhancement.FUNCIONARIO),
    "plan_quinquenal": _delayed_enhance(Enhancement.ENCHUFADO),
}


def schengen(run: Run) -> None:
    """🇪🇺 Tratado: un hueco más en la tienda."""
    run.shop_slots += 1


def acuerdo_paris(run: Run) -> None:
    """🌍 Tratado: un descarte más por sesión."""
    run.base_discards += 1


def mercado_unico(run: Run) -> None:
    """🇪🇺 Tratado: todo un 25 % más barato (lo aplica la tienda al ver el tratado)."""


def ruta_seda(run: Run) -> None:
    """🇨🇳 Tratado: un hueco más de consumibles (puerto de aguas profundas)."""
    run.max_consumables += 1
