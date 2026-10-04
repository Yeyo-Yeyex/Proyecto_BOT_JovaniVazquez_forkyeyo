"""Las ONGs del bot y los donativos deducibles.

Las cuatro ONGs son de broma: dicen servir a una causa y hacen justo lo
contrario, o se gastan lo donado en conseguir más dinero. Donar es un gasto
de verdad (el dinero se queda en la cuenta de la ONG, no vuelve a nadie), pero
desgrava: el art. 19.1 de la Ley 49/2002 permite deducir de la cuota del IRPF
el 80 % de los primeros 250 € y el 40 % del resto. La regla está en
`bot.services.taxes.donation_deduction` y la devolución llega con la renta del
lunes. Es el truco de siempre: donar para pagar menos a Perro Sanxe.

Tratamiento fiscal del donativo: no paga IGIC (no hay contraprestación, art. 4
de la Ley 20/1991) ni Donaciones (quien recibe es una entidad, y el ISD solo
grava a personas físicas: art. 3 de la Ley 29/1987).

Cada ONG tiene un monedero con `user_id` negativo en `economy_wallets`. Los
ids no se cambian nunca: son los que guardan el dinero.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from bot.services.taxes import (
    DONATION_BASE_LIMIT,
    DONATION_FULL_LIMIT,
    DONATION_FULL_RATE,
    DONATION_REST_RATE,
)


@dataclass(frozen=True, slots=True)
class Ong:
    """Una ONG del catálogo.

    Attributes:
        key: Identificador estable; es lo que se guarda en cada donativo.
        account_id: `user_id` (negativo) de su monedero.
        mission: Lo que dice que hace.
        reality: Lo que hace de verdad.
        spending: En qué se gasta un donativo; se elige uno al azar.
    """

    key: str
    account_id: int
    name: str
    emoji: str
    mission: str
    reality: str
    spending: tuple[str, ...]


ONGS: tuple[Ong, ...] = (
    Ong(
        key="desmontar",
        account_id=-1,
        name="Fundación Desmontando España",
        emoji="🧨",
        mission="Fomentar la convivencia y el diálogo entre todos los españoles.",
        reality=(
            "Desmontar España pieza a pieza. De momento solo ha conseguido desmontar "
            "la cuenta corriente de sus donantes."
        ),
        spending=(
            "un congreso en Bruselas sobre cómo organizar más congresos en Bruselas",
            "un estudio de 300 páginas que concluye que hace falta otro estudio",
            "las dietas de un viaje en primera para debatir sobre la desigualdad",
            "un mapa nuevo de España sin España",
        ),
    ),
    Ong(
        key="ayudante",
        account_id=-2,
        name="Asociación Ayuda al Ayudante",
        emoji="🤝",
        mission="Ayudar a las personas que más lo necesitan.",
        reality="Ayuda a quien más lo necesita: su junta directiva.",
        spending=(
            "el coche de empresa del vicepresidente segundo",
            "una cena de gala para recaudar fondos para la próxima cena de gala",
            "la subida de sueldo del director de transparencia",
            "un curso de liderazgo en Marbella para el comité de ética",
        ),
    ),
    Ong(
        key="mares",
        account_id=-3,
        name="Mares Limpios Sociedad Limitada",
        emoji="🛥️",
        mission="Proteger los océanos y la fauna marina.",
        reality=(
            "Supervisa el océano desde un yate de 40 metros que gasta más gasóleo "
            "que un pueblo entero."
        ),
        spending=(
            "gasóleo para el yate de supervisión",
            "una campaña con influencers en las Maldivas",
            "pajitas de papel para la fiesta en el yate",
            "el segundo yate, por si el primero se ensucia",
        ),
    ),
    Ong(
        key="hambre",
        account_id=-4,
        name="Fundación Cubiertos de Plata contra el Hambre",
        emoji="🍽️",
        mission="Acabar con el hambre en el mundo.",
        reality=(
            "Acaba con el hambre de su patronato en un banquete anual con langosta "
            "y siete tenedores por persona."
        ),
        spending=(
            "langosta para el banquete solidario",
            "un sumiller para maridar la rueda de prensa",
            "manteles de hilo bordados con el logo de la fundación",
            "el café de especialidad del patronato (con mango, por supuesto)",
        ),
    ),
)
BY_KEY: dict[str, Ong] = {ong.key: ong for ong in ONGS}


def find_ong(text: str) -> Ong | None:
    """Busca una ONG por su clave o por una palabra de su nombre (sin mayúsculas)."""
    wanted = text.strip().lower()
    if not wanted:
        return None
    if wanted in BY_KEY:
        return BY_KEY[wanted]
    for ong in ONGS:
        if wanted in ong.name.lower():
            return ong
    return None


def pick_spending(ong: Ong, rng: random.Random | None = None) -> str:
    """En qué se ha gastado la ONG tu donativo."""
    return (rng or random).choice(ong.spending)


def deduction_rule_text() -> str:
    """La regla de la deducción en una línea, para enseñarla al donar."""
    first = f"{DONATION_FULL_LIMIT:,}".replace(",", ".")
    return (
        f"{DONATION_FULL_RATE:.0%} de los primeros {first} Y$ donados en la semana y "
        f"{DONATION_REST_RATE:.0%} del resto, hasta el {DONATION_BASE_LIMIT:.0%} de lo que "
        "ganes esa semana y nunca más del IRPF que hayas pagado"
    ).replace("%", " %")
