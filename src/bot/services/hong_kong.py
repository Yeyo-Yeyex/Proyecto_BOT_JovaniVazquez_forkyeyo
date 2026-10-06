"""Reloj de Hong Kong para `hongkong`: qué hora es allí y qué anda haciendo Robuso.

Robuso es un amigo del servidor que vive en Hong Kong. El comando enseña la
hora de allí, la de Canarias y la diferencia entre las dos, que cambia con el
horario de verano: Hong Kong no lo tiene (UTC+8 todo el año) y Canarias sí
(UTC+0 en invierno, UTC+1 en verano), así que van 8 horas por delante en
invierno y 7 en verano. Se calcula con `zoneinfo`, sin llamadas a la red.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

HONG_KONG = ZoneInfo("Asia/Hong_Kong")
CANARY = ZoneInfo("Atlantic/Canary")

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip

#: Qué hace Robuso según la hora de Hong Kong: `(desde la hora, texto)`, en orden.
ROBUSO_SCHEDULE: tuple[tuple[int, str], ...] = (
    (0, "😴 Robuso está durmiendo. No le escribas, bebé, que mañana madruga."),
    (7, "🥟 Robuso anda desayunando dim sum. Así cualquiera empieza bien el día."),
    (10, "💼 Robuso está en la oficina, dándolo todo."),
    (12, "🍜 Robuso está almorzando en un cha chaan teng. Arroz, té con leche y pa'lante."),
    (14, "💼 Robuso sigue currando. Ya mismo sale, tranquilo."),
    (19, "🌃 Robuso está cenando con vistas a Victoria Harbour. Qué vida, mi amor."),
    (22, "🎤 Robuso puede que esté en un karaoke de Lan Kwai Fong. Escríbele, que contesta."),
)


@dataclass(frozen=True, slots=True)
class Clocks:
    """El mismo instante en Hong Kong y en Canarias.

    Attributes:
        hong_kong: Hora local de Hong Kong.
        canary: Hora local de Canarias.
    """

    hong_kong: datetime
    canary: datetime

    @property
    def hours_ahead(self) -> int:
        """Horas que Hong Kong va por delante de Canarias (7 u 8)."""
        offset = self.hong_kong.utcoffset()
        local = self.canary.utcoffset()
        assert offset is not None and local is not None
        return int((offset - local).total_seconds() // 3600)

    @property
    def is_tomorrow(self) -> bool:
        """Si en Hong Kong ya es el día siguiente que en Canarias."""
        return self.hong_kong.date() > self.canary.date()


def clocks_at(now: float) -> Clocks:
    """Hora de Hong Kong y de Canarias en el instante `now` (epoch)."""
    return Clocks(
        hong_kong=datetime.fromtimestamp(now, HONG_KONG),
        canary=datetime.fromtimestamp(now, CANARY),
    )


def long_date(moment: datetime) -> str:
    """Fecha como «martes 6 de octubre»."""
    return f"{WEEKDAYS[moment.weekday()]} {moment.day} de {MONTHS[moment.month - 1]}"


def robuso_status(hour: int) -> str:
    """Qué anda haciendo Robuso a esa hora de Hong Kong (0–23)."""
    text = ROBUSO_SCHEDULE[0][1]
    for start, status in ROBUSO_SCHEDULE:
        if hour >= start:
            text = status
    return text


def clock_text(clocks: Clocks) -> str:
    """Mensaje completo de `hongkong`."""
    hk = clocks.hong_kong
    tomorrow = " (allí ya es mañana)" if clocks.is_tomorrow else ""
    return "\n".join(
        [
            f"🇭🇰 En Hong Kong son las **{hk:%H:%M}** del {long_date(hk)}{tomorrow}.",
            f"🏝️ En Canarias, las {clocks.canary:%H:%M}: Hong Kong va "
            f"{clocks.hours_ahead} horas por delante.",
            robuso_status(hk.hour),
        ]
    )
