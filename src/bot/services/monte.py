"""Reglas del Monte de Piedad: venderle un objeto o empeñarlo, sin Discord ni base de datos.

El Monte es uno de los mostradores de `banco` (los Montes de Piedad eran de las cajas de
ahorros). Compra y presta con el dinero de la caja de la tienda (`SHOP_ACCOUNT_ID`): lo que
se gastó en la tienda vuelve a circular, y nunca más de lo que se gastó.

- **Vender** (`SELL_SHARE`): el Monte paga el 85 % de lo que se pagó por esa unidad en la
  tienda, sin IGIC y con la rebaja si la tuvo (`paid`). El objeto no vuelve.
- **Empeñar** (`PAWN_SHARE`): presta el 55 % y da una papeleta. Quien devuelve el préstamo
  más un 5 % (`PAWN_FEE`) antes de `PAWN_SECONDS` recupera el objeto; si no, se lo queda el
  Monte. Mientras tanto el objeto lo guarda el Monte (`PAWN_ACCOUNT_ID` en la mochila): no
  se usa, no se luce y no cuenta en el patrimonio de nadie.

**Por qué sobre lo pagado y no sobre el precio de catálogo** (cifras en
`docs/monte_sim.py`): con el catálogo, comprar rebajado (hasta el 90 %) y venderle al Monte
creaba dinero en cuanto pagara más del 10 %, y las piezas de la 📦 gratis (un 9,5 % de lo
apostado a valor de catálogo) convertían el casino en una máquina de hacer dinero. Por eso
el Monte no admite lo que no salió de la tienda: cajas ganadas jugando, regalos de las
mascotas, lo que da `crear`. Tampoco lo numerado o de edición limitada: si se lo quedara,
desaparecería del servidor para siempre. Todo eso se vende en el Rastro.

**Topes de las cifras** (los comprueba una prueba):

- El préstamo no pasa del 70 % de lo que da vender (`1 - GARNISH_SHARE`): si no, a quien
  debe a Hacienda le saldría mejor empeñar y no volver (el préstamo no se embarga) que
  vender y pagar el embargo.
- El interés supera lo que se ahorra en el Patrimonio empeñando el domingo y desempeñando
  el lunes (lo empeñado no cuenta): el tipo marginal más alto por la parte que no vuelve
  en efectivo.

Fiscalidad en `EconomyService.monte`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bot.services.rastro import untradeable
from bot.services.shop import Kind

#: Nombre del mostrador, para los textos.
MONTE_NAME = "Monte de Piedad"
#: Parte de lo pagado en la tienda que da el Monte por comprar un objeto.
SELL_SHARE = 0.85
#: Parte de lo pagado en la tienda que presta al empeñarlo.
PAWN_SHARE = 0.55
#: Interés de una papeleta, sobre lo prestado y por todo el plazo.
PAWN_FEE = 0.05
#: Lo que dura una papeleta: tres días para desempeñar.
PAWN_SECONDS = 3 * 86_400
#: Papeletas abiertas a la vez por persona, para que el mostrador quepa.
MAX_TICKETS = 5


class MonteError(Exception):
    """Algo del Monte que no se puede hacer; el mensaje se enseña tal cual."""


@dataclass(frozen=True, slots=True)
class Offer:
    """Lo que da el Monte por un objeto.

    Attributes:
        paid: Lo que se pagó por esa unidad en la tienda, sin IGIC.
        sale: Lo que paga por comprarlo.
        loan: Lo que presta por empeñarlo.
        repay: Lo que hay que devolver para desempeñarlo.
    """

    paid: int
    sale: int
    loan: int
    repay: int

    @property
    def interest(self) -> int:
        """Lo que cuesta la papeleta: lo que se devuelve de más."""
        return self.repay - self.loan


def offer(paid: int) -> Offer:
    """Lo que da el Monte por algo que costó `paid` en la tienda.

    Lo que cobra el miembro se redondea hacia abajo y el interés hacia arriba.
    """
    loan = math.floor(paid * PAWN_SHARE)
    return Offer(
        paid=paid,
        sale=math.floor(paid * SELL_SHARE),
        loan=loan,
        repay=loan + math.ceil(loan * PAWN_FEE),
    )


def refusal(kind: Kind, catalog_key: str | None, *, numbered: bool, paid: int) -> str | None:
    """Por qué el Monte no acepta un objeto de la mochila, o `None` si lo acepta.

    Args:
        numbered: Si lleva número de serie o su artículo es de edición limitada.
        paid: Lo que se pagó por esa unidad en la tienda, sin IGIC (0 si no se compró).
    """
    if kind is not Kind.TROPHY:
        return (
            "El Monte solo acepta coleccionables y tapetes: ni mascotas, ni roles, ni "
            "potenciadores."
        )
    if (reason := untradeable(kind, catalog_key)) is not None:
        return reason
    if numbered:
        return (
            "Lo numerado o de edición limitada no entra en el Monte: si se lo quedara, "
            "desaparecería del servidor. Eso se vende en el Rastro."
        )
    if paid <= 0:
        return (
            "Eso no salió de la tienda (una caja, un regalo, un premio): el Monte solo "
            "devuelve dinero que se gastó. Véndelo en el Rastro."
        )
    return None
