"""Reglas de la Guerra (Casino War): una carta para ti, una para la banca, gana la más alta.

Lógica pura, sin Discord ni base de datos. El dinero no se toca aquí: la partida
dice cuánto hay que cobrar antes de cada paso (`extra_stake`) y cuánto devolver al
terminar (`total_return`), y el cog lo mueve a través de la economía.

Reglas (las de los casinos, con la ventaja de Las Vegas):

- Zapato de 6 barajas barajado de nuevo en cada mano (`new_shoe`, el del
  blackjack): no se pueden contar cartas. El as es la carta más alta
  (`rank_value`) y los palos no cuentan.
- Se reparte una carta al jugador y otra a la banca. Gana la más alta: la tuya
  paga 1:1; si gana la banca, se lleva la apuesta.
- Empate. El jugador elige:
  - **Rendirse** (`surrender`): recupera la mitad de la apuesta, redondeando
    hacia abajo.
  - **Ir a la guerra** (`go_to_war`): pone otra apuesta igual, la banca quema
    `BURN_CARDS` cartas y sale una carta más para cada uno. Si la del jugador es
    mayor o igual, gana y cobra 1:1 **solo la apuesta de la guerra** (la original
    se devuelve); si es menor, pierde las dos. Si la guerra vuelve a empatar,
    además se paga un premio extra igual a la apuesta original (`WAR_TIE_BONUS`,
    la regla de Las Vegas).
- Apuesta lateral opcional, **Empate 10:1** (`TIE_PAYS`): una apuesta extra igual
  a la ficha que se cobra al repartir y paga 10:1 si las dos primeras cartas
  empatan. Es independiente de la mano.
- Sin tope de apuesta.

Lo que devuelve, de lo apostado (`docs/rtp_guerra.py`; exacto por cálculo y
comprobado jugando `WarGame` tal cual, 2 millones de manos):

- Ir siempre a la guerra: 97,67 %. La referencia habitual de ≈ 97,1 % es la
  variante en la que empatar la guerra solo gana la apuesta de la guerra; con el
  premio extra de Las Vegas, que aquí se paga, sube.
- Rendirse siempre: 96,30 %.
- Apuesta lateral de empate: 81,35 % (con 6 barajas empata 23 de cada 311
  veces, 7,40 %; 10:1 necesitaría 13,5 veces la apuesta).

Quemar tres cartas no cambia nada: las cartas de la guerra son dos al azar del
resto del zapato, así que se queman para que se vea como en la mesa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from bot.services.blackjack import Card, new_shoe

#: Cartas que la banca quema antes de repartir las de la guerra.
BURN_CARDS = 3
#: La apuesta lateral de empate paga 10:1: se devuelve lo apostado más diez veces.
TIE_PAYS = 10
#: Si la guerra empata, además se paga la apuesta original una vez más.
WAR_TIE_BONUS = 1
#: As = carta más alta.
ACE_VALUE = 14


def rank_value(card: Card) -> int:
    """Fuerza de la carta: del 2 al 13, con el as en 14."""
    return ACE_VALUE if card.rank == 1 else card.rank


class Result(Enum):
    """Desenlace de la mano principal."""

    WIN = "win"
    LOSE = "lose"
    SURRENDER = "surrender"
    WAR_WIN = "war_win"
    WAR_TIE = "war_tie"
    WAR_LOSE = "war_lose"


class IllegalAction(Exception):
    """La acción no está permitida en el estado actual de la mano."""


@dataclass(slots=True)
class WarGame:
    """Una mano de la Guerra de principio a fin.

    Uso: `deal()`; si `awaiting_choice`, `surrender()` o `go_to_war()` (antes
    de esta última, el cog cobra `extra_stake`); después `total_return`.

    Attributes:
        stake: Apuesta principal.
        side: Apuesta lateral de empate (0 si no se juega).
    """

    stake: int
    side: int = 0
    shoe: list[Card] = field(default_factory=new_shoe)
    player: list[Card] = field(default_factory=list)
    dealer: list[Card] = field(default_factory=list)
    burned: list[Card] = field(default_factory=list)
    result: Result | None = None

    # -- Reparto -------------------------------------------------------------------

    def deal(self) -> None:
        """Reparte una carta a cada uno; si no empatan, la mano acaba aquí."""
        if self.stake <= 0 or self.side < 0:
            raise ValueError("La apuesta debe ser positiva.")
        self.player.append(self.shoe.pop())
        self.dealer.append(self.shoe.pop())
        a, b = rank_value(self.player[0]), rank_value(self.dealer[0])
        if a > b:
            self.result = Result.WIN
        elif a < b:
            self.result = Result.LOSE

    @property
    def tied(self) -> bool:
        """Si las dos primeras cartas empataron (gane o no la lateral)."""
        return rank_value(self.player[0]) == rank_value(self.dealer[0])

    @property
    def awaiting_choice(self) -> bool:
        """Si hay empate y falta decidir entre rendirse e ir a la guerra."""
        return self.result is None

    @property
    def settled(self) -> bool:
        """Si la mano ya tiene resultado."""
        return self.result is not None

    @property
    def went_to_war(self) -> bool:
        """Si se jugó la guerra (hay segunda carta de cada lado)."""
        return len(self.player) > 1

    # -- Decisión tras el empate ---------------------------------------------------

    @property
    def extra_stake(self) -> int:
        """Lo que hay que cobrar antes de ir a la guerra: otra apuesta igual."""
        return self.stake

    def surrender(self) -> None:
        """Se rinde: recupera la mitad de la apuesta.

        Raises:
            IllegalAction: Si no hay un empate pendiente.
        """
        if not self.awaiting_choice:
            raise IllegalAction("surrender")
        self.result = Result.SURRENDER

    def go_to_war(self) -> None:
        """Va a la guerra: la banca quema cartas y salen otras dos.

        Raises:
            IllegalAction: Si no hay un empate pendiente.
        """
        if not self.awaiting_choice:
            raise IllegalAction("war")
        self.burned = [self.shoe.pop() for _ in range(BURN_CARDS)]
        self.player.append(self.shoe.pop())
        self.dealer.append(self.shoe.pop())
        a, b = rank_value(self.player[1]), rank_value(self.dealer[1])
        self.result = Result.WAR_WIN if a > b else Result.WAR_TIE if a == b else Result.WAR_LOSE

    # -- Cuentas -------------------------------------------------------------------

    @property
    def side_return(self) -> int:
        """Lo que devuelve la lateral: lo apostado más 10:1 si las dos primeras empatan."""
        return self.side * (1 + TIE_PAYS) if self.side and self.tied else 0

    @property
    def main_return(self) -> int:
        """Lo que devuelve la mano principal (0 hasta que acaba)."""
        s = self.stake
        match self.result:
            case Result.WIN:
                return 2 * s
            case Result.SURRENDER:
                return s // 2
            case Result.WAR_WIN:
                # La original se devuelve y la de la guerra cobra 1:1.
                return 3 * s
            case Result.WAR_TIE:
                return (3 + WAR_TIE_BONUS) * s
            case _:
                return 0

    @property
    def total_stake(self) -> int:
        """Todo lo puesto en la mesa: la ficha, la guerra si la hubo y la lateral."""
        return self.stake * (2 if self.went_to_war else 1) + self.side

    @property
    def total_return(self) -> int:
        """Total que se devuelve al jugador (0 hasta que la mano acaba)."""
        if not self.settled:
            return 0
        return self.main_return + self.side_return

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la mano completa."""
        return self.total_return - self.total_stake

    @property
    def won(self) -> bool:
        """Si la mano principal acaba en ventaja para el jugador (gana o gana la guerra)."""
        return self.result in (Result.WIN, Result.WAR_WIN, Result.WAR_TIE)


def main_net(game: WarGame) -> int:
    """Lo que gana o pierde la mano principal, sin la lateral."""
    return game.main_return - game.stake * (2 if game.went_to_war else 1)
