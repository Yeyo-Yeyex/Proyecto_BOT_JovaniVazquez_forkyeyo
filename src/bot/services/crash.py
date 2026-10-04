"""Reglas del Crash: el punto de explosión, la curva del cohete y la ronda.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.crash`) lleva el reloj,
cobra y paga a través de la economía y pinta; aquí solo se decide quién
cobra cuánto.

Cómo funciona una ronda:

1. Antes de despegar se sortea el punto de explosión (`crash_point`). Nadie
   lo ve hasta que explota.
2. El multiplicador sube con el tiempo según `multiplier_at`: 2x a los 4,5 s,
   10x a los 15 s, 100x a los 30 s.
3. Cada jugador puede retirarse cuando quiera (o fijar un auto-retiro). Si se
   retira antes de la explosión, cobra apuesta × multiplicador; si no, lo
   pierde todo.

Los multiplicadores se guardan en centésimas enteras (`250` = 2,50x) para que
los pagos no dependan de redondeos de coma flotante.

Reparto del punto de explosión: P(llegar a x) = 0,99 / x. Por eso, retirarse
siempre en el mismo multiplicador devuelve de media el 99 % de lo apostado,
sea cual sea, y el 1 % de las rondas explota en 1,00x (nadie cobra). Es el
reparto de los Crash de los casinos online (Bustabit, Stake).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

#: Retorno al jugador en centésimas de unidad: 99 %, ventaja de la casa 1 %.
RTP_CENTS = 99
#: Multiplicador máximo (×1000): la ronda explota ahí como muy tarde (~45 s).
MAX_CENTS = 100_000
#: El auto-retiro más bajo posible. 1,00x no tiene sentido: no ganas nada.
MIN_AUTO_CENTS = 101
#: Velocidad de la curva: el multiplicador se duplica cada 4,5 s.
DOUBLING_SECONDS = 4.5
GROWTH_PER_SECOND = math.log(2) / DOUBLING_SECONDS


def crash_point(u: float) -> int:
    """Punto de explosión en centésimas a partir de un azar uniforme en [0, 1).

    `floor(99 / (1 - u))` hace que P(punto ≥ c) = 99 / c para cualquier
    c ≥ 100 (en centésimas). Los valores por debajo de 100 se quedan en 1,00x.

    Raises:
        ValueError: Si `u` no está en [0, 1).
    """
    if not 0 <= u < 1:
        raise ValueError("u debe estar en [0, 1).")
    cents = math.floor(RTP_CENTS / (1 - u))
    return max(100, min(MAX_CENTS, cents))


def multiplier_at(seconds: float) -> int:
    """Multiplicador (centésimas, redondeado hacia abajo) tras `seconds` de vuelo."""
    if seconds <= 0:
        return 100
    if seconds >= seconds_to(MAX_CENTS):
        return MAX_CENTS
    return min(MAX_CENTS, max(100, math.floor(100 * math.exp(GROWTH_PER_SECOND * seconds))))


def seconds_to(cents: int) -> float:
    """Segundos de vuelo hasta que el multiplicador llega a `cents`."""
    return math.log(max(cents, 100) / 100) / GROWTH_PER_SECOND


def payout(stake: int, cents: int) -> int:
    """Lo que cobra una apuesta retirada en `cents` (apuesta incluida)."""
    return stake * cents // 100


def format_multiplier(cents: int) -> str:
    """`247` → `2,47x`; los miles llevan punto (`1.000,00x`)."""
    whole, frac = divmod(cents, 100)
    return f"{whole:,}".replace(",", ".") + f",{frac:02d}x"


_MULTIPLIER = re.compile(r"^[x×]?(\d{1,4})(?:[.,](\d{1,2}))?[x×]?$")


def parse_multiplier(text: str) -> int:
    """Convierte `2`, `2x`, `2,5`, `1.75x` o `x3` en centésimas.

    Raises:
        ValueError: Con un mensaje mostrable si no se entiende o se sale de
            1,01x–1.000x.
    """
    match = _MULTIPLIER.match(text.strip().lower().replace(" ", ""))
    if match is None:
        raise ValueError(f"No entiendo `{text}` como multiplicador. Prueba `2x` o `1,5`.")
    whole = int(match.group(1))
    frac = (match.group(2) or "0").ljust(2, "0")
    cents = whole * 100 + int(frac)
    if not MIN_AUTO_CENTS <= cents <= MAX_CENTS:
        raise ValueError(
            f"El auto-retiro va de {format_multiplier(MIN_AUTO_CENTS)} a "
            f"{format_multiplier(MAX_CENTS)}."
        )
    return cents


def looks_like_multiplier(text: str) -> bool:
    """Si el texto lleva marca de multiplicador (`2x`, `x1,5`), no de cantidad."""
    value = text.strip().lower()
    return bool(_MULTIPLIER.match(value)) and ("x" in value or "×" in value)


# -- Ronda -----------------------------------------------------------------------------


class CrashError(Exception):
    """Acción no válida en la ronda; el mensaje se puede enseñar al usuario."""


@dataclass(slots=True)
class Seat:
    """Un jugador en la ronda.

    Attributes:
        stake: Lo apostado (ya cobrado por la economía).
        auto_cents: Auto-retiro en centésimas, si lo tiene.
        cashed_cents: Multiplicador al que se retiró; `None` si sigue dentro
            o si explotó con él dentro.
        by_auto: Si se retiró por el auto-retiro.
    """

    user_id: int
    name: str
    stake: int
    auto_cents: int | None = None
    cashed_cents: int | None = None
    by_auto: bool = False

    @property
    def cashed(self) -> bool:
        """Si ya se retiró."""
        return self.cashed_cents is not None

    @property
    def payout(self) -> int:
        """Lo cobrado (0 si no se retiró)."""
        return payout(self.stake, self.cashed_cents) if self.cashed_cents else 0

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta. Antes de la explosión, un no retirado cuenta como perdido."""
        return self.payout - self.stake


@dataclass(slots=True)
class CrashRound:
    """Una ronda: el punto de explosión y los asientos.

    No sabe nada del tiempo: el cog le dice en qué multiplicador va el cohete.
    """

    crash_cents: int
    seats: dict[int, Seat] = field(default_factory=dict)

    def sit(self, seat: Seat) -> None:
        """Sienta a un jugador.

        Raises:
            CrashError: Si ya estaba sentado.
        """
        if seat.user_id in self.seats:
            raise CrashError("Ya estás dentro de esta ronda.")
        self.seats[seat.user_id] = seat

    def cash_out(self, user_id: int, cents: int, *, by_auto: bool = False) -> Seat:
        """Retira a un jugador en `cents`.

        Raises:
            CrashError: Si no está en la ronda, ya se retiró o el cohete ya
                había explotado (`cents` por encima del punto de explosión).
        """
        seat = self.seats.get(user_id)
        if seat is None:
            raise CrashError("No estás en esta ronda. Entra en la siguiente.")
        if seat.cashed:
            raise CrashError("Ya te habías retirado.")
        if cents > self.crash_cents or (cents == self.crash_cents and not by_auto):
            # A mano, llegar justo al punto de explosión ya es tarde; el
            # auto-retiro en el punto exacto sí cobra (P = 0,99 / x exacta).
            raise CrashError("¡Tarde! El cohete ya había explotado.")
        seat.cashed_cents = max(100, cents)
        seat.by_auto = by_auto
        return seat

    def next_auto(self) -> int | None:
        """El auto-retiro pendiente más bajo que se va a cumplir, si hay alguno."""
        targets = [
            s.auto_cents
            for s in self.seats.values()
            if not s.cashed and s.auto_cents is not None and s.auto_cents <= self.crash_cents
        ]
        return min(targets) if targets else None

    def due_autos(self, cents: int) -> list[Seat]:
        """Retira a todos los que tenían auto-retiro en `cents` o menos.

        Cada uno cobra justo su objetivo, no el multiplicador actual: el
        auto-retiro es exacto aunque el reloj del bot vaya a saltos.
        """
        due = sorted(
            (
                s
                for s in self.seats.values()
                if not s.cashed
                and s.auto_cents is not None
                and s.auto_cents <= min(cents, self.crash_cents)
            ),
            key=lambda s: s.auto_cents or 0,
        )
        for seat in due:
            self.cash_out(seat.user_id, seat.auto_cents or 0, by_auto=True)
        return due

    @property
    def riding(self) -> list[Seat]:
        """Los que siguen dentro del cohete."""
        return [s for s in self.seats.values() if not s.cashed]

    @property
    def total_staked(self) -> int:
        """Todo lo apostado en la ronda."""
        return sum(s.stake for s in self.seats.values())
