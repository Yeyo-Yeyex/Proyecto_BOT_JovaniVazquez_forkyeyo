"""Contrato de los efectos de Legislatura y contexto de la puntuación.

Todo lo que cambia las reglas (asesores, jefes de la moción de censura) es un
`Effect`: una clase con ganchos que por defecto no hacen nada. La partida
llama a cada gancho en un momento fijo y en el orden de los asesores, de
izquierda a derecha, así que reordenarlos cambia el resultado, como en Balatro.

Ganchos y cuándo se llaman:

- `rules`: antes de mirar la jugada (color de 4 cartas, escaleras con huecos…).
- `retriggers`: cuántas veces más se activa una carta al puntuar o en la mano.
- `on_card_scored`: cada vez que puntúa una carta jugada.
- `on_held`: por cada carta que se queda en la mano.
- `on_hand`: una vez por jugada, después de las cartas (el «independiente»).
- `after_hand`: con la jugada ya sumada (contadores que crecen).
- `on_discard`: al descartar.
- `on_round_start` y `on_round_end`: al empezar y al ganar una sesión.
- `on_acquire` y `on_sell`: al entrar y salir de la mesa.
- `saves`: al perder una sesión, por si alguien salva al Gobierno.
- `price`, `sell_value` y `blocks`: en la tienda (miran cualquier artículo o
  asesor, no solo el propio).
- `debuffs` y `hides_suits`: solo los jefes; anulan cartas o esconden palos.

**Referencias cruzadas entre bloques.** Cada asesor es de un bloque
(`Bloc`: España, UE, EE. UU., China, Israel) y los ganchos ven toda la
partida, así que un asesor puede mirar a los de otros bloques: los aranceles
encarecen lo chino, el boicot a Eurovisión echa a los israelíes, la cumbre de
la OTAN cobra menos si Perro Sanxe está en la mesa. Esa es la regla de sátira
del proyecto convertida en mecánica: nadie se libra y todos se afectan.

`ScoreContext` apunta cada suma o multiplicación como un `ScoreStep`, con su
origen, para que el cog enseñe la cuenta paso a paso (el conteo que engancha).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from bot.services.legislatura.cards import Card
from bot.services.legislatura.hands import Evaluation

if TYPE_CHECKING:
    from bot.services.legislatura.items import ItemDef
    from bot.services.legislatura.run import AdvisorSlot, Run


class StepKind(StrEnum):
    """Qué hace un paso de la cuenta."""

    VOTES = "votos"
    MULT = "mult"
    XMULT = "xmult"
    FONDOS = "fondos"
    NOTE = "nota"


@dataclass(frozen=True, slots=True)
class ScoreStep:
    """Un paso de la cuenta, para enseñarlo.

    Attributes:
        source: Quién lo hace: la clave del asesor, `"carta"`, `"base"` o `"jefe"`.
        kind: Qué hace.
        amount: Cuánto (votos, mult, factor o fondos). 0 en las notas.
        text: Texto corto para el usuario («+30 votos», «Hungría veta»).
        card_id: La carta implicada, si la hay.
    """

    source: str
    kind: StepKind
    amount: float
    text: str
    card_id: int | None = None


@dataclass(slots=True)
class ScoreContext:
    """Estado de una jugada mientras se puntúa.

    Attributes:
        run: La partida (los efectos pueden mirarla y cambiar fondos o cartas).
        evaluation: Qué combinación es y qué cartas puntúan.
        played: Cartas jugadas, en orden.
        scoring: Cartas que puntúan, en orden.
        held: Cartas que se quedan en la mano.
        votes: Votos acumulados.
        mult: Mult acumulado.
        steps: La cuenta paso a paso.
        destroyed: Ids de cartas que se rompen al acabar la jugada.
    """

    run: Run
    evaluation: Evaluation
    played: list[Card]
    scoring: list[Card]
    held: list[Card]
    votes: int = 0
    mult: float = 0.0
    steps: list[ScoreStep] = field(default_factory=list)
    destroyed: set[int] = field(default_factory=set)

    def add_votes(self, source: str, amount: int, card: Card | None = None) -> None:
        """Suma votos y lo apunta."""
        if not amount:
            return
        self.votes += amount
        self.steps.append(
            ScoreStep(source, StepKind.VOTES, amount, f"{amount:+} votos", card and card.id)
        )

    def add_mult(self, source: str, amount: float, card: Card | None = None) -> None:
        """Suma mult y lo apunta."""
        if not amount:
            return
        self.mult += amount
        self.steps.append(
            ScoreStep(source, StepKind.MULT, amount, f"{amount:+g} mult", card and card.id)
        )

    def times_mult(self, source: str, factor: float, card: Card | None = None) -> None:
        """Multiplica el mult y lo apunta."""
        if factor == 1:
            return
        self.mult *= factor
        self.steps.append(
            ScoreStep(source, StepKind.XMULT, factor, f"×{factor:g} mult", card and card.id)
        )

    def earn(self, source: str, amount: int, card: Card | None = None) -> None:
        """Da (o quita) fondos de la partida al momento y lo apunta."""
        if not amount:
            return
        self.run.fondos += amount
        self.steps.append(
            ScoreStep(source, StepKind.FONDOS, amount, f"{amount:+} fondos", card and card.id)
        )

    def note(self, source: str, text: str, card: Card | None = None) -> None:
        """Apunta algo que pasa sin cambiar la cuenta («Hungría veta»)."""
        self.steps.append(ScoreStep(source, StepKind.NOTE, 0, text, card and card.id))

    @property
    def total(self) -> int:
        """Votos de la jugada: votos × mult, redondeado hacia abajo."""
        return int(self.votes * self.mult)


class Effect:
    """Efecto de un asesor o de un jefe. Los ganchos no hacen nada por defecto.

    `slot` es el asesor en la mesa (con su estado propio, `slot.state`), o
    `None` cuando el efecto es de un jefe.
    """

    def rules(self, run: Run, slot: AdvisorSlot | None, rules: dict[str, bool]) -> None:
        """Cambia las reglas de mirar jugadas (`short`, `gaps`, `all_score`)."""

    def retriggers(self, ctx: ScoreContext, slot: AdvisorSlot | None, card: Card) -> int:
        """Veces extra que se activa `card` (al puntuar y en la mano)."""
        return 0

    def on_card_scored(self, ctx: ScoreContext, slot: AdvisorSlot | None, card: Card) -> None:
        """Una carta jugada acaba de puntuar."""

    def on_held(self, ctx: ScoreContext, slot: AdvisorSlot | None, card: Card) -> None:
        """Una carta se queda en la mano durante la jugada."""

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        """Una vez por jugada, después de las cartas."""

    def after_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        """Con la jugada ya sumada (para contadores que crecen)."""

    def on_discard(self, run: Run, slot: AdvisorSlot | None, cards: list[Card]) -> None:
        """Al descartar `cards`."""

    def on_round_start(self, run: Run, slot: AdvisorSlot | None) -> None:
        """Al empezar una sesión."""

    def on_round_end(self, run: Run, slot: AdvisorSlot | None) -> None:
        """Al ganar una sesión, antes de cobrar."""

    def on_acquire(self, run: Run, slot: AdvisorSlot) -> None:
        """Al entrar en la mesa."""

    def on_sell(self, run: Run, slot: AdvisorSlot) -> None:
        """Al venderlo."""

    def price(self, run: Run, slot: AdvisorSlot | None, item: ItemDef, price: int) -> int:
        """Precio de `item` en la tienda mientras este efecto está en juego."""
        return price

    def sell_value(self, run: Run, slot: AdvisorSlot, sold: AdvisorSlot, value: int) -> int:
        """Lo que se cobra al vender `sold` (cualquier asesor) mientras este está en la mesa."""
        return value

    def blocks(self, run: Run, slot: AdvisorSlot | None, item: ItemDef) -> str | None:
        """Motivo para no dejar comprar `item` (`None` si se puede)."""
        return None

    def saves(self, run: Run, slot: AdvisorSlot | None) -> bool:
        """Si evita que caiga el Gobierno al perder una sesión (lo pregunta en orden)."""
        return False

    def debuffs(self, run: Run, card: Card) -> bool:
        """Solo jefes: si `card` no puntúa en esta sesión."""
        return False

    @property
    def hides_suits(self) -> bool:
        """Solo jefes: si el dibujo esconde los palos (Gran Cortafuegos)."""
        return False
