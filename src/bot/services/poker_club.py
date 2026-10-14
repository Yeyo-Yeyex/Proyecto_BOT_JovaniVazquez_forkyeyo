"""El club del póker: rango, retos del día y racha. Lógica pura, sin Discord ni dinero.

El cog (`bot.cogs.poker`) le pasa cada mano decidida como un `HandEvent`, el
repositorio (`bot.repositories.poker`) guarda la XP, la racha y lo que lleva de
cada reto, y la economía paga los retos cumplidos.

**Rango.** Cada mano con un ante de al menos `XP_MIN_ANTE` (una tirada del
casino) da 1 punto de experiencia, y 1 más si la gana. El rango (`RANKS`) sale
de la XP acumulada: diez escalones de «Pringado de la timba» a «Dios del
tapete». Los rangos altos abren las mesas de ante alto (`Rank.max_ante`), y
algunos logros de rango dan tapetes. Un ante pequeño no da XP para que nadie
suba a base de manos de 1 Y$.

**Retos del día.** Cada miembro tiene tres retos al día, uno fácil, uno medio
y uno difícil (`daily_challenges`), sorteados con su servidor, su id y la
fecha: los mismos todo el día, distintos mañana, sin guardar nada para
elegirlos. Solo cuentan las manos con ante de al menos `XP_MIN_ANTE`. Al
cumplir uno se cobra al momento su premio (`Challenge.reward`) por la
racha (`streak_multiplier`).

**Racha.** Días seguidos jugando al menos una mano que cuente. Cada día de
racha sube un 10 % el premio de los retos, hasta el doble a los once días; un
día sin jugar la reinicia (`next_streak`).

**Equilibrio** (Biblia, «Equilibrio: medir en tiradas»). Los tres retos dan
750 Y$ brutos al día (la mitad del IMV máximo) y, con la racha a tope, 1.500
(un IMV), por jugar unas 30-40 manos. Es dinero que se crea: lo compensa la
ventaja de la casa del póker y del bote, y paga IRPF como un premio
(`EconomyService.pay_income`).
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from bot.services.poker import Outcome, Rank

#: Ante mínimo para que una mano dé XP y cuente en los retos y la racha.
XP_MIN_ANTE = 100
#: Cada día de racha sube el premio de los retos un 10 %, hasta este tope (el doble).
STREAK_STEP_PCT = 10
STREAK_MAX_PCT = 100


@dataclass(frozen=True, slots=True)
class PlayerRank:
    """Un escalón del club.

    Attributes:
        level: 1 a 10.
        xp: XP desde la que se llega.
        name: Lo que se enseña en la mesa y en los avisos.
        emoji: Junto al nombre.
        max_ante: Ante máximo con este rango (`None`, sin tope).
    """

    level: int
    xp: int
    name: str
    emoji: str
    max_ante: int | None


RANKS: tuple[PlayerRank, ...] = (
    PlayerRank(1, 0, "Pringado de la timba", "🐣", 10_000),
    PlayerRank(2, 50, "Mirón del Casino del Estado", "👀", 10_000),
    PlayerRank(3, 150, "Aprendiz de tahúr", "🃏", 50_000),
    PlayerRank(4, 400, "Fiera del bar del pueblo", "🍺", 50_000),
    PlayerRank(5, 1_000, "Habitual de la timba de los jueves", "🎩", 250_000),
    PlayerRank(6, 2_500, "Tiburón de Torrelodones", "🦈", 250_000),
    PlayerRank(7, 5_000, "Tahúr de Moncloa", "🏛️", None),
    PlayerRank(8, 10_000, "Farolero del Falcon", "✈️", None),
    PlayerRank(9, 20_000, "Leyenda de Benidorm", "🌴", None),
    PlayerRank(10, 50_000, "Dios del tapete", "👑", None),
)


def rank_of(xp: int) -> PlayerRank:
    """El rango que corresponde a `xp`."""
    return next(r for r in reversed(RANKS) if xp >= r.xp)


def next_rank(xp: int) -> PlayerRank | None:
    """El rango siguiente, o `None` si ya está en el último."""
    return next((r for r in RANKS if r.xp > xp), None)


def min_rank_for(ante: int) -> PlayerRank:
    """El primer rango con el que se puede poner este ante."""
    return next(r for r in RANKS if r.max_ante is None or ante <= r.max_ante)


# -- La mano que cuenta ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HandEvent:
    """Lo que el club necesita saber de una mano decidida.

    Attributes:
        ante: El ante.
        called: Si fue al final.
        outcome: Lo que pasó yendo (aunque se retirara: lo que habría pasado).
        rank: La mejor jugada de siete cartas del jugador.
        net: Ganancia o pérdida de la mano (sin el bote).
        bonus: Si jugó la apuesta del bote.
        bonus_won: Si la apuesta del bote cobró algo.
        others: Cuántos más jugaban la misma mano en la mesa.
        pocket_aces: Si tenía pareja de ases en la mano.
    """

    ante: int
    called: bool
    outcome: Outcome
    rank: Rank
    net: int
    bonus: bool = False
    bonus_won: bool = False
    others: int = 0
    pocket_aces: bool = False

    @property
    def counts(self) -> bool:
        """Si cuenta para XP, retos y racha (ante de al menos una tirada)."""
        return self.ante >= XP_MIN_ANTE

    @property
    def won(self) -> bool:
        """Si ganó la mano (yendo, con la banca jugando o sin jugar)."""
        return self.called and self.outcome in (Outcome.WIN, Outcome.NO_QUALIFY)


def xp_for(event: HandEvent) -> int:
    """XP de una mano: 1 por jugarla y 1 más si la gana; 0 con un ante pequeño."""
    if not event.counts:
        return 0
    return 1 + int(event.net > 0)


# -- Retos -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Challenge:
    """Un reto del día.

    Attributes:
        key: Clave estable (se guarda con lo que lleva cada uno).
        text: Lo que se enseña, con `{goal}` para la meta.
        goal: Cuántas veces hay que cumplirlo.
        tier: 0 fácil, 1 medio, 2 difícil.
        count: Cuánto suma una mano (0 si no cuenta).
    """

    key: str
    text: str
    goal: int
    tier: int
    count: Callable[[HandEvent], int]

    @property
    def reward(self) -> int:
        """Premio bruto al cumplirlo, antes de la racha."""
        return TIER_REWARDS[self.tier]

    @property
    def label(self) -> str:
        """El texto con la meta puesta."""
        return self.text.format(goal=self.goal)


#: Premio bruto de cada dificultad (fácil, medio, difícil).
TIER_REWARDS = (100, 250, 400)
TIER_NAMES = ("Fácil", "Medio", "Difícil")


def _made(order: int) -> Callable[[HandEvent], int]:
    return lambda e: int(e.called and e.rank.order >= order)


CHALLENGES: tuple[Challenge, ...] = (
    # Fáciles: jugando un rato salen solos.
    Challenge("manos_15", "Juega {goal} manos", 15, 0, lambda e: 1),
    Challenge("ir_10", "Ve al final {goal} veces", 10, 0, lambda e: int(e.called)),
    Challenge("ganar_4", "Gana {goal} manos a la banca", 4, 0, lambda e: int(e.won)),
    Challenge(
        "planton_2",
        "Que la banca no juegue {goal} veces cuando vas",
        2,
        0,
        lambda e: int(e.called and e.outcome is Outcome.NO_QUALIFY),
    ),
    Challenge("bote_5", "Juega {goal} manos con 💎 Bote", 5, 0, lambda e: int(e.bonus)),
    # Medios: piden suerte o constancia.
    Challenge("ganar_10", "Gana {goal} manos a la banca", 10, 1, lambda e: int(e.won)),
    Challenge("dobles_4", "Liga doble pareja o mejor {goal} veces yendo", 4, 1, _made(2)),
    Challenge(
        "compania_8",
        "Juega {goal} manos con más gente en la mesa",
        8,
        1,
        lambda e: int(e.others > 0),
    ),
    Challenge(
        "retirada_3",
        "Retírate de {goal} manos que habrías perdido",
        3,
        1,
        lambda e: int(not e.called and e.outcome is Outcome.LOSE),
    ),
    Challenge("trio_2", "Liga trío o mejor {goal} veces yendo", 2, 1, _made(3)),
    # Difíciles: un buen rato de mesa o una buena carta.
    Challenge("color_1", "Liga color o mejor yendo", 1, 2, _made(5)),
    Challenge("ganar_20", "Gana {goal} manos a la banca", 20, 2, lambda e: int(e.won)),
    Challenge("escalera_2", "Liga escalera o mejor {goal} veces yendo", 2, 2, _made(4)),
    Challenge(
        "bote_cobra_1",
        "Cobra la apuesta del 💎 Bote",
        1,
        2,
        lambda e: int(e.bonus_won),
    ),
    Challenge(
        "gana_mesa_6",
        "Gana {goal} manos con más gente en la mesa",
        6,
        2,
        lambda e: int(e.won and e.others > 0),
    ),
)
CHALLENGE_BY_KEY: dict[str, Challenge] = {c.key: c for c in CHALLENGES}


def daily_challenges(guild_id: int, user_id: int, day: date) -> tuple[Challenge, ...]:
    """Los tres retos de hoy de un miembro: uno de cada dificultad.

    Se sortean con una semilla de su servidor, su id y la fecha, así que son los
    mismos todo el día y no hace falta guardarlos.
    """
    digest = hashlib.sha256(f"{guild_id}:{user_id}:{day.isoformat()}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    return tuple(rng.choice([c for c in CHALLENGES if c.tier == tier]) for tier in range(3))


def progress_for(challenges: tuple[Challenge, ...], event: HandEvent) -> dict[str, int]:
    """Lo que suma esta mano a cada reto (sin los que no suma nada)."""
    if not event.counts:
        return {}
    return {c.key: n for c in challenges if (n := c.count(event))}


# -- Racha -----------------------------------------------------------------------------


def next_streak(streak: int, last_day: date | None, today: date) -> int:
    """La racha tras jugar hoy: sigue si jugó ayer, se queda si ya jugó hoy, si no, 1."""
    if last_day == today:
        return max(streak, 1)
    if last_day == today - timedelta(days=1):
        return streak + 1
    return 1


def streak_multiplier_pct(streak: int) -> int:
    """Lo que sube el premio de los retos con esta racha, en %: 10 por día tras el primero."""
    return min(STREAK_STEP_PCT * max(streak - 1, 0), STREAK_MAX_PCT)


def reward_for(challenge: Challenge, streak: int) -> int:
    """El premio bruto de un reto con la racha de hoy."""
    return challenge.reward * (100 + streak_multiplier_pct(streak)) // 100
