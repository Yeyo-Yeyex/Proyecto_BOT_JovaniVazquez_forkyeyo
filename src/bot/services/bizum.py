"""Reglas del Bizum entre miembros: límites, cuándo uno se sale de ellos y su fiscalidad.

El `bizum` pasa yapdollars de un miembro a otro al momento. Los límites copian
los de Bizum en España pasados a yapdollars con `YAPDOLLARS_PER_EURO`
(10 Y$ = 1 €):

- Mínimo por operación: 0,50 € → `MIN_AMOUNT` (5 Y$). Por debajo no se envía.
- Máximo por operación: 1.000 € → `MAX_OPERATION` (10.000 Y$).
- Máximo enviado al día: 2.000 € → `MAX_DAILY` (20.000 Y$).

El mínimo se aplica de verdad. Los dos máximos no: el bot deja pasarse y lo
apunta, porque saltárselos es justo lo que premian los logros «A espaldas de
Sánchez» (un Bizum de más de 10.000 Y$) y «Límite diario». En la vida real el
banco lo rechazaría; aquí Perro Sanxe mira para otro lado.

Fiscalidad (decisión del proyecto, ver `Biblia.txt`): **exento** del Impuesto
sobre Sucesiones y Donaciones. En la vida real, dinero regalado entre amigos es
una donación sujeta (art. 3.1.b de la Ley 29/1987) y Canarias solo la bonifica al
99,9 % entre familia directa, grupos I y II (art. 26 sexies del Decreto
Legislativo 1/2009, en la redacción del Decreto ley 5/2023); entre amigos, grupo
IV, se paga entero. El bot trata a todo el servidor como familia de grupo II y
redondea ese 0,1 % residual a cero, para no ahogar a nadie a impuestos por
pagarle una pizza a un colega. Tampoco hay IRPF para quien recibe: lo que
tributa por Donaciones no tributa por IRPF (art. 6.4 LIRPF).
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.taxes import YAPDOLLARS_PER_EURO

#: Mínimo de Bizum por operación (0,50 €).
MIN_AMOUNT = YAPDOLLARS_PER_EURO // 2
#: Máximo de Bizum por operación (1.000 €). Pasarse desbloquea «A espaldas de Sánchez».
MAX_OPERATION = 1_000 * YAPDOLLARS_PER_EURO
#: Máximo de Bizum enviado en un día (2.000 €).
MAX_DAILY = 2_000 * YAPDOLLARS_PER_EURO
#: Caracteres del concepto. Es para que se lea bien en el chat; no se guarda.
CONCEPT_MAX = 60


@dataclass(frozen=True, slots=True)
class LimitCheck:
    """Si un Bizum se sale de los límites españoles.

    Attributes:
        over_operation: Supera el máximo por operación.
        over_daily: Con este envío, lo enviado hoy supera el máximo diario.
    """

    over_operation: bool
    over_daily: bool

    @property
    def any(self) -> bool:
        """Si se ha saltado algún límite."""
        return self.over_operation or self.over_daily


def check_limits(amount: int, sent_today: int) -> LimitCheck:
    """Compara un Bizum con los límites por operación y diario.

    Args:
        amount: Lo que se envía ahora.
        sent_today: Lo enviado hoy, este Bizum incluido.
    """
    return LimitCheck(over_operation=amount > MAX_OPERATION, over_daily=sent_today > MAX_DAILY)


def clean_concept(text: str | None) -> str | None:
    """Recorta el concepto a `CONCEPT_MAX` caracteres y lo deja en una línea.

    Devuelve `None` si queda vacío.
    """
    if text is None:
        return None
    one_line = " ".join(text.split())
    if not one_line:
        return None
    return one_line if len(one_line) <= CONCEPT_MAX else one_line[: CONCEPT_MAX - 1] + "…"
