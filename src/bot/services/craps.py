"""Reglas del craps (`dados`): Pase o No pase con dos dados, el punto y las Odds.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.craps`) cobra al
empezar y al poner Odds, paga al terminar y pinta los dados
(`bot.services.craps_scene`).

**La salida.** El jugador apuesta a ✅ Pase o a 🚫 No pase y tira dos dados.
Con Pase, un 7 o un 11 («natural») gana y un 2, 3 o 12 («pifia») pierde. Con
No pase es al revés: el 2 y el 3 ganan, el 7 y el 11 pierden y el 12 empata
(«la barra»: sin ella, No pase tendría ventaja sobre la casa). Cualquier otro
total (4, 5, 6, 8, 9 o 10) es el **punto**.

**El punto.** Con el punto puesto se sigue tirando hasta que sale el punto
otra vez (gana Pase) o un 7 (gana No pase: es el **siete fuera**). El resto
de totales no deciden nada.

**Las Odds.** Con el punto puesto se puede poner, encima de la apuesta,
hasta `ODDS_MAX` veces lo apostado a lo mismo. Pagan la probabilidad exacta
(`TRUE_ODDS`: 2:1 al 4 y al 10, 3:2 al 5 y al 9, 6:5 al 6 y al 8; No pase
cobra lo inverso), así que no tienen ventaja de la casa. Es la única
decisión de la partida que no es la de salida, y la que la hace
interesante. El premio se redondea hacia abajo a yapdólares enteros.

**La ventaja de la casa sale sola.** Pase devuelve el 98,59 % y No pase el
98,64 %, con las reglas de cualquier casino y sin tocar ninguna cuota: lo
mismo que Minas, el Crash o Cara o cruz. Con Odds, la ventaja sobre el total
apostado baja.

**La mano.** El tirador conserva los dados hasta que saca un siete fuera,
aunque gane o pierda partidas por el camino (`Hand`). Los puntos que hace
en una mano son la «mano caliente» de los logros.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

#: Totales que se convierten en punto en la salida.
POINTS: tuple[int, ...] = (4, 5, 6, 8, 9, 10)
#: Lo que pagan las Odds de Pase por cada punto (a:b). No pase cobra b:a.
TRUE_ODDS: dict[int, tuple[int, int]] = {
    4: (2, 1),
    10: (2, 1),
    5: (3, 2),
    9: (3, 2),
    6: (6, 5),
    8: (6, 5),
}
#: Las Odds pueden llegar a estas veces lo apostado (en fichas de la apuesta).
ODDS_MAX = 3
#: Totales de la salida que ganan o pierden según la apuesta.
NATURALS = frozenset({7, 11})
CRAPS = frozenset({2, 3, 12})
#: El 12 de la salida empata con No pase.
BAR = 12

#: Nombre de cada total, para el texto y los carteles.
TOTAL_NAMES: dict[int, str] = {
    2: "Ojos de Pegasus",
    3: "Tres",
    4: "Cuatro",
    5: "Cinco",
    6: "Seis",
    7: "Siete",
    8: "Ocho",
    9: "Nueve",
    10: "Diez",
    11: "Once, como la ONCE",
    12: "Doble seis",
}


class Bet(Enum):
    """A qué se apuesta. Cada valor es `(clave, emoji, nombre)`."""

    PASS = ("pase", "✅", "Pase")
    DONT = ("nopase", "🚫", "No pase")

    def __init__(self, key: str, emoji: str, label: str) -> None:
        self.key = key
        self.emoji = emoji
        self.label = label


BET_BY_KEY: dict[str, Bet] = {b.key: b for b in Bet}
_ALIASES = {
    "pass": "pase",
    "p": "pase",
    "si": "pase",
    "sí": "pase",
    "no": "nopase",
    "dont": "nopase",
    "np": "nopase",
    "no-pase": "nopase",
    "no_pase": "nopase",
    "contra": "nopase",
}


def parse_bet(text: str) -> Bet:
    """`pase`, `pass`, `si` → Pase; `nopase`, `no`, `dont`, `contra` → No pase.

    Raises:
        ValueError: Con un mensaje mostrable si no es ninguna apuesta.
    """
    key = text.strip().lower().replace(" ", "")
    key = _ALIASES.get(key, key)
    if key not in BET_BY_KEY:
        raise ValueError("Elige `pase` o `nopase`.")
    return BET_BY_KEY[key]


def throw(rng: random.Random) -> tuple[int, int]:
    """Tira dos dados."""
    return rng.randint(1, 6), rng.randint(1, 6)


def total_chance(total: int) -> Fraction:
    """Probabilidad de sacar `total` con dos dados (1/36 el 2, 6/36 el 7…)."""
    return Fraction(6 - abs(total - 7), 36) if 2 <= total <= 12 else Fraction(0)


def point_chance(point: int) -> Fraction:
    """Probabilidad de hacer el punto antes que el siete (1/3 el 4, 5/11 el 6…)."""
    made = total_chance(point)
    return made / (made + total_chance(7))


def odds_profit(bet: Bet, point: int, amount: int) -> int:
    """Lo que ganan (sin devolver lo puesto) unas Odds de `amount` si aciertan.

    Pase cobra `TRUE_ODDS` (2:1 al 4) y No pase lo inverso (1:2 al 4). Se
    redondea hacia abajo, como un casino que paga en fichas.
    """
    a, b = TRUE_ODDS[point]
    if bet is Bet.DONT:
        a, b = b, a
    return amount * a // b


def format_odds(bet: Bet, point: int) -> str:
    """`6:5` para Pase al 6, `5:6` para No pase al 6."""
    a, b = TRUE_ODDS[point]
    return f"{a}:{b}" if bet is Bet.PASS else f"{b}:{a}"


def house_edge(bet: Bet) -> Fraction:
    """Ventaja de la casa de la apuesta de salida (sin Odds), calculada con las reglas.

    Pase: 7/495 (1,41 %). No pase: 3/220 (1,36 %).
    """
    win = sum(total_chance(t) for t in NATURALS)
    lose = sum(total_chance(t) for t in CRAPS)
    for p in POINTS:
        win += total_chance(p) * point_chance(p)
        lose += total_chance(p) * (1 - point_chance(p))
    if bet is Bet.DONT:
        # Gana lo que pierde Pase salvo el 12, que empata.
        win, lose = lose - total_chance(BAR), win
    return lose - win


class Status(Enum):
    """Estado de una partida."""

    COME_OUT = "salida"
    POINT = "punto"
    WON = "ganada"
    LOST = "perdida"
    PUSH = "empate"


class CrapsError(Exception):
    """Acción no válida en la partida; el mensaje se puede enseñar al usuario."""


@dataclass(frozen=True, slots=True)
class Roll:
    """Una tirada y lo que había antes de ella.

    Attributes:
        dice: Los dos dados.
        point: El punto que había puesto antes de tirar (`None` en la salida).
    """

    dice: tuple[int, int]
    point: int | None = None

    @property
    def total(self) -> int:
        """La suma de los dados."""
        return self.dice[0] + self.dice[1]

    @property
    def doubles(self) -> bool:
        """Si los dos dados son iguales."""
        return self.dice[0] == self.dice[1]

    @property
    def come_out(self) -> bool:
        """Si fue la tirada de salida."""
        return self.point is None

    @property
    def seven_out(self) -> bool:
        """Un 7 con el punto puesto: se acaba la mano del tirador."""
        return self.point is not None and self.total == 7

    @property
    def made(self) -> bool:
        """El punto, repetido antes que el 7."""
        return self.point is not None and self.total == self.point

    @property
    def hard(self) -> bool:
        """El punto hecho «por las malas»: con dobles (2-2, 3-3, 4-4 o 5-5)."""
        return self.made and self.doubles


@dataclass(slots=True)
class CrapsGame:
    """Una partida: de la salida a que la apuesta gana, pierde o empata.

    Attributes:
        stake: Lo apostado a Pase o No pase (ya cobrado por la economía).
        bet: A qué se apostó.
        odds: Lo puesto en Odds (también cobrado ya).
        point: El punto, cuando está puesto (se queda al terminar).
        rolls: Tiradas hechas, en orden.
    """

    stake: int
    bet: Bet
    odds: int = 0
    point: int | None = None
    rolls: list[Roll] = field(default_factory=list)
    status: Status = Status.COME_OUT

    @classmethod
    def new(cls, stake: int, bet: Bet) -> CrapsGame:
        """Empieza una partida en la salida.

        Raises:
            ValueError: Si la apuesta no es positiva.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        return cls(stake=stake, bet=bet)

    # -- Estado -----------------------------------------------------------------------

    @property
    def playing(self) -> bool:
        """Si se puede seguir tirando."""
        return self.status in (Status.COME_OUT, Status.POINT)

    @property
    def last(self) -> Roll | None:
        """La última tirada, si hubo."""
        return self.rolls[-1] if self.rolls else None

    @property
    def wagered(self) -> int:
        """Todo lo puesto: la apuesta y las Odds."""
        return self.stake + self.odds

    @property
    def odds_room(self) -> int:
        """Lo que aún se puede poner en Odds (0 si no hay punto o están al tope)."""
        if self.status is not Status.POINT:
            return 0
        return ODDS_MAX * self.stake - self.odds

    @property
    def odds_full(self) -> bool:
        """Si las Odds llegaron al tope."""
        return self.odds > 0 and self.odds >= ODDS_MAX * self.stake

    def potential(self) -> int:
        """Lo que se cobraría si la apuesta ganara ahora (apuesta y Odds incluidas)."""
        total = 2 * self.stake
        if self.odds and self.point is not None:
            total += self.odds + odds_profit(self.bet, self.point, self.odds)
        return total

    @property
    def payout(self) -> int:
        """Lo devuelto al terminar: lo apostado más el premio, o 0."""
        if self.status is Status.WON:
            return self.potential()
        if self.status is Status.PUSH:
            return self.stake
        return 0

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la partida terminada."""
        return self.payout - self.wagered

    # -- Acciones ---------------------------------------------------------------------

    def add_odds(self, amount: int) -> int:
        """Pone `amount` más en Odds (ya cobrado por la economía) y devuelve el total.

        Raises:
            CrapsError: Sin punto, al tope o con una cantidad que no cabe.
        """
        if self.status is not Status.POINT:
            raise CrapsError("Las Odds solo se ponen con el punto puesto.")
        if amount <= 0:
            raise CrapsError("Pon algo en las Odds.")
        if amount > self.odds_room:
            raise CrapsError(f"Las Odds llegan como mucho a {ODDS_MAX} veces la apuesta.")
        self.odds += amount
        return self.odds

    def roll(self, dice: tuple[int, int]) -> Roll:
        """Aplica una tirada ya hecha (`throw`) y decide la partida si toca.

        Raises:
            CrapsError: Si la partida ya terminó o los dados no existen.
        """
        if not self.playing:
            raise CrapsError("La partida ya ha terminado.")
        if not all(1 <= d <= 6 for d in dice):
            raise CrapsError("Esos dados no existen.")
        roll = Roll(dice, self.point)
        self.rolls.append(roll)
        total = roll.total
        passing = self.bet is Bet.PASS
        if roll.come_out:
            if total in NATURALS:
                self.status = Status.WON if passing else Status.LOST
            elif total == BAR and not passing:
                self.status = Status.PUSH
            elif total in CRAPS:
                self.status = Status.LOST if passing else Status.WON
            else:
                self.point = total
                self.status = Status.POINT
        elif roll.made:
            self.status = Status.WON if passing else Status.LOST
        elif roll.seven_out:
            self.status = Status.LOST if passing else Status.WON
        return roll

    def play(self, rng: random.Random) -> Roll:
        """Tira los dados y aplica la tirada."""
        return self.roll(throw(rng))


@dataclass(slots=True)
class Hand:
    """La mano del tirador: de que coge los dados a su siete fuera.

    Pasa por encima de las partidas: una mano puede ganar varias salidas y
    hacer varios puntos. La lleva la mesa del cog y se cierra al caducar.

    Attributes:
        rolls: Tiradas de la mano.
        points: Puntos hechos, en orden (pueden repetirse).
        seven_out: Si la mano terminó con un siete fuera.
    """

    rolls: int = 0
    points: list[int] = field(default_factory=list)
    seven_out: bool = False
    #: Último total y cuántas veces seguidas ha salido (el récord, en `repeat_max`).
    last_total: int | None = None
    repeat: int = 0
    repeat_max: int = 0

    def observe(self, roll: Roll) -> None:
        """Apunta una tirada.

        Raises:
            CrapsError: Si la mano ya se acabó.
        """
        if self.seven_out:
            raise CrapsError("Esa mano ya se ha acabado.")
        self.rolls += 1
        self.repeat = self.repeat + 1 if roll.total == self.last_total else 1
        self.repeat_max = max(self.repeat_max, self.repeat)
        self.last_total = roll.total
        if roll.made:
            self.points.append(roll.total)
        elif roll.seven_out:
            self.seven_out = True

    @property
    def distinct_points(self) -> int:
        """Puntos distintos hechos en la mano (los seis son el «fuego»)."""
        return len(set(self.points))


def milestone(points: int) -> str | None:
    """Frase para las manos calientes que merecen celebrarse; `None` para el resto."""
    return {
        2: "🔥 Dos puntos en la misma mano.",
        3: "🔥🔥 ¡Tres puntos! La mesa se calienta.",
        5: "🚒 ¡Cinco puntos! Que alguien llame a los bomberos.",
        7: "🌋 ¡Siete puntos! Esto ya sale en el telediario.",
        10: "🏆 ¡DIEZ PUNTOS EN UNA MANO! Leyenda del Casino del Estado.",
    }.get(points)
