"""Reglas de los cumpleaños: fechas, regalos y qué cuenta como felicitación.

Solo se guarda día y mes, nunca el año de nacimiento: no hace falta para
felicitar y así el bot no sabe la edad de nadie.

Dinero (yapdollars). Tratamiento fiscal: exento de IRPF, porque lo que
tributa por el Impuesto sobre Sucesiones y Donaciones no está sujeto al IRPF
(art. 6.4 de la Ley 35/2006) y un regalo es una donación (art. 3.1.b de la
Ley 29/1987). El ISD aún no existe en el bot; cuando exista, se aplicará aquí:

- El día del cumpleaños, el cumpleañero recibe `BIRTHDAY_GIFT`.
- Cada persona que le felicita recibe `GREETER_REWARD` y le suma
  `GREETED_BONUS` al cumpleañero. Una vez por persona y cumpleaños.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

BIRTHDAY_GIFT = 3_000
GREETER_REWARD = 500
GREETED_BONUS = 100

MONTH_NAMES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

_DATE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.]\d{2,4})?$")

#: Palabras o emojis que convierten un mensaje dirigido al cumpleañero en
#: felicitación. Basta con una.
_GREETING = re.compile(
    r"\b(feliz|felicidades|felicitaciones|felicito|cumple\w*|hbd|happy\s+birthday)\b"
    r"|🎂|🎉|🥳|🎈|🎁",
    re.IGNORECASE,
)


def parse_birthday(text: str) -> tuple[int, int]:
    """Convierte `14/02` (o `14-2`, `14.02.1999`…) en `(día, mes)`.

    Si viene el año se ignora: no se guarda.

    Raises:
        ValueError: Si no es una fecha válida. El mensaje es para el usuario.
    """
    match = _DATE.match(text.strip())
    if match is None:
        raise ValueError("Escribe la fecha como `dd/mm`, por ejemplo `14/02`.")
    day, month = int(match.group(1)), int(match.group(2))
    # 2000 es bisiesto: así el 29/02 es válido.
    if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(2000, month)[1]:
        raise ValueError(f"`{text.strip()}` no es una fecha que exista.")
    return day, month


def celebration_date(day: int, month: int, year: int) -> date:
    """Día en que se celebra en `year`. El 29/02 se celebra el 28/02 si no es bisiesto."""
    if month == 2 and day == 29 and not calendar.isleap(year):
        return date(year, 2, 28)
    return date(year, month, day)


def is_birthday(day: int, month: int, today: date) -> bool:
    """Si `today` es el día de celebrar ese cumpleaños."""
    return celebration_date(day, month, today.year) == today


def days_until(day: int, month: int, today: date) -> int:
    """Días que faltan hasta el próximo cumpleaños (0 si es hoy)."""
    this_year = celebration_date(day, month, today.year)
    if this_year >= today:
        return (this_year - today).days
    return (celebration_date(day, month, today.year + 1) - today).days


def format_birthday(day: int, month: int) -> str:
    """`(14, 2)` → `14 de febrero`."""
    return f"{day} de {MONTH_NAMES[month - 1]}"


def is_greeting(text: str) -> bool:
    """Si el texto suena a felicitación de cumpleaños."""
    return _GREETING.search(text) is not None
