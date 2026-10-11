"""La tienda de dentro de una partida de Legislatura (el mercado entre sesiones).

No es la `tienda` del bot: aquí se paga con fondos de la partida, que no se
compran con yapdollars (si se pudieran comprar, el juego premiaría al rico y
no al que juega bien). Lo que se compra con yapdollars son los aspectos de
`bot.services.legislatura.cosmetics`, que no cambian las reglas.

Cada vez que se abre el mercado trae:

- `run.shop_slots` ofertas (asesores, encuestas o normativas, con pesos 20/4/4
  como Balatro),
- dos sobres,
- el tratado del periodo, si no se ha comprado ya.

Los precios se calculan al enseñarlos (`price_of`), no al sacar la oferta, para
que comprar Aranceles encarezca al momento lo chino que ya está en el
escaparate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bot.services.legislatura.cards import RANKS, Card, Edition, Enhancement, Suit
from bot.services.legislatura.catalog import ITEMS, STAKES, items_of
from bot.services.legislatura.items import RARITY_WEIGHTS, Category, ItemDef, PackSpec, Rarity

if TYPE_CHECKING:
    from bot.services.legislatura.run import AdvisorSlot, Run

#: Peso de cada categoría en las ofertas sueltas.
OFFER_WEIGHTS: dict[Category, float] = {
    Category.ASESOR: 20,
    Category.ENCUESTA: 4,
    Category.NORMATIVA: 4,
}
#: Probabilidad (1 de cada N) de cada edición en un asesor de la tienda, de la más rara a la
#: más común; se mira en ese orden y gana la primera que toque.
EDITION_ODDS: tuple[tuple[Edition, int], ...] = (
    (Edition.OFFSHORE, 300),
    (Edition.PATROCINADO, 100),
    (Edition.VIRAL, 50),
    (Edition.FONDOS_UE, 25),
)
EDITION_COST: dict[Edition, int] = {
    Edition.FONDOS_UE: 2,
    Edition.VIRAL: 3,
    Edition.PATROCINADO: 5,
    Edition.OFFSHORE: 5,
}
#: Lo que cuesta la primera vuelta a la tienda; cada una sube 1.
REROLL_BASE = 5
PACKS_PER_MARKET = 2


@dataclass(slots=True)
class Offer:
    """Una oferta del escaparate o una opción de un sobre.

    Attributes:
        key: Clave del catálogo (`None` si es una carta de la baraja).
        edition: Edición, si es un asesor que la trae.
        card: La carta, si es un sobre de militantes (sin id hasta quedársela).
        sold: Si ya se ha comprado o elegido.
        free: Si sale gratis (abstención «Indulto»).
    """

    key: str | None
    edition: Edition | None = None
    card: dict[str, object] | None = None
    sold: bool = False
    free: bool = False

    def to_dict(self) -> dict[str, object]:
        """Forma JSON."""
        return {
            "k": self.key,
            "e": self.edition.value if self.edition else None,
            "c": self.card,
            "s": self.sold,
            "f": self.free,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Offer:
        """Inversa de `to_dict`."""
        edition = data.get("e")
        return cls(
            key=data.get("k"),  # type: ignore[arg-type]
            edition=Edition(edition) if edition else None,
            card=data.get("c"),  # type: ignore[arg-type]
            sold=bool(data.get("s")),
            free=bool(data.get("f")),
        )


@dataclass(slots=True)
class Market:
    """El mercado abierto entre dos sesiones.

    Attributes:
        offers: Ofertas sueltas.
        packs: Sobres a la venta.
        voucher: Tratado del periodo (si queda).
        reroll_cost: Lo que cuesta la siguiente vuelta.
        discount: Factor de precio de este mercado (abstención «Rebajas»).
    """

    offers: list[Offer] = field(default_factory=list)
    packs: list[Offer] = field(default_factory=list)
    voucher: str | None = None
    reroll_cost: int = REROLL_BASE
    discount: float = 1.0

    def to_dict(self) -> dict[str, object]:
        """Forma JSON."""
        return {
            "o": [o.to_dict() for o in self.offers],
            "p": [p.to_dict() for p in self.packs],
            "v": self.voucher,
            "r": self.reroll_cost,
            "d": self.discount,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Market:
        """Inversa de `to_dict`."""
        return cls(
            offers=[Offer.from_dict(o) for o in data["o"]],  # type: ignore[union-attr]
            packs=[Offer.from_dict(p) for p in data["p"]],  # type: ignore[union-attr]
            voucher=data.get("v"),  # type: ignore[arg-type]
            reroll_cost=int(data["r"]),  # type: ignore[arg-type]
            discount=float(data.get("d", 1.0)),  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class PackState:
    """Un sobre abierto: opciones y cuántas quedan por elegir."""

    key: str
    options: list[Offer]
    picks_left: int

    def to_dict(self) -> dict[str, object]:
        """Forma JSON."""
        return {"k": self.key, "o": [o.to_dict() for o in self.options], "n": self.picks_left}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PackState:
        """Inversa de `to_dict`."""
        return cls(
            key=str(data["k"]),
            options=[Offer.from_dict(o) for o in data["o"]],  # type: ignore[union-attr]
            picks_left=int(data["n"]),  # type: ignore[arg-type]
        )


def _roll_edition(run: Run, stream: str) -> Edition | None:
    rng = run.rng.stream(stream)
    for edition, one_in in EDITION_ODDS:
        if rng.randrange(one_in) == 0:
            return edition
    return None


def _pick_advisor(run: Run, stream: str, *, rarity: Rarity | None = None) -> str | None:
    """Un asesor que no se tenga ya y que nadie en la mesa bloquee."""
    owned = {slot.key for slot in run.advisors}
    pool = [
        i for i in items_of(Category.ASESOR) if i.key not in owned and run.blocked_reason(i) is None
    ]
    if not pool:
        return None
    if rarity is None:
        rarities = [r for r, w in RARITY_WEIGHTS.items() if w and any(i.rarity is r for i in pool)]
        if not rarities:
            return None
        rarity = run.rng.weighted(
            stream + ":rareza", rarities, [RARITY_WEIGHTS[r] for r in rarities]
        )
    candidates = [i.key for i in pool if i.rarity is rarity] or [i.key for i in pool]
    return run.rng.choice(stream, candidates)


def _pick(run: Run, category: Category, stream: str) -> Offer | None:
    if category is Category.ASESOR:
        key = _pick_advisor(run, stream)
        return Offer(key, _roll_edition(run, stream + ":edicion")) if key else None
    pool = [i.key for i in items_of(category)]
    return Offer(run.rng.choice(stream, pool)) if pool else None


def _random_card(run: Run, stream: str) -> dict[str, object]:
    rng = run.rng.stream(stream)
    card = Card(0, rng.choice(RANKS), rng.choice(list(Suit)))
    if rng.randrange(3) == 0:
        card.enhancement = rng.choice(list(Enhancement))
    return card.to_dict()


def roll_offers(run: Run, label: str) -> list[Offer]:
    """Las ofertas sueltas de un mercado (o de una vuelta)."""
    categories = list(OFFER_WEIGHTS)
    weights = [OFFER_WEIGHTS[c] for c in categories]
    offers = []
    for n in range(run.shop_slots):
        stream = f"tienda:{label}:{n}"
        category = run.rng.weighted(stream + ":tipo", categories, weights)
        offer = _pick(run, category, stream)
        if offer is not None:
            offers.append(offer)
    return offers


def open_market(run: Run) -> Market:
    """Monta el mercado al acabar una sesión. Consume las abstenciones de tienda."""
    label = f"{run.period}:{run.session}"
    market = Market(offers=roll_offers(run, label))
    packs = items_of(Category.SOBRE)
    market.packs = [
        Offer(run.rng.choice(f"tienda:{label}:sobre:{n}", [p.key for p in packs]))
        for n in range(PACKS_PER_MARKET)
    ]
    if run.period_voucher and run.period_voucher not in run.vouchers:
        market.voucher = run.period_voucher
    if "rebajas" in run.tags:
        run.tags.remove("rebajas")
        market.discount = 0.5
    if "indulto" in run.tags:
        first = next((o for o in market.offers if ITEMS[o.key].category is Category.ASESOR), None)
        if first is not None:
            run.tags.remove("indulto")
            first.free = True
    return market


def price_of(run: Run, item: ItemDef, edition: Edition | None = None) -> int:
    """Precio actual de `item` con la mesa, los tratados, la prima y las abstenciones."""
    price = item.cost + (EDITION_COST[edition] if edition else 0)
    if item.category is Category.ASESOR:
        price += STAKES[run.stake].advisor_surcharge
    for slot in run.advisors:
        price = run.effect_of(slot).price(run, slot, item, price)
    factor = run.market.discount if run.market else 1.0
    if "mercado_unico" in run.vouchers:
        factor *= 0.75
    return max(1, math.floor(price * factor))


def sell_value(run: Run, slot: AdvisorSlot) -> int:
    """Lo que se cobra al vender un asesor: la mitad de su precio, con los efectos de la mesa."""
    item = ITEMS[slot.key]
    value = max(1, (item.cost + (EDITION_COST[slot.edition] if slot.edition else 0)) // 2)
    for other in run.advisors:
        value = run.effect_of(other).sell_value(run, other, slot, value)
    return value


def roll_pack(run: Run, key: str, spec: PackSpec) -> list[Offer]:
    """Opciones de un sobre recién comprado."""
    label = f"sobre:{run.period}:{run.session}:{key}"
    options: list[Offer] = []
    for n in range(spec.size):
        stream = f"{label}:{n}"
        if spec.category is None:
            options.append(Offer(None, card=_random_card(run, stream)))
            continue
        offer = _pick(run, spec.category, stream)
        if offer is not None and all(o.key != offer.key for o in options):
            options.append(offer)
    return options
