"""Lo que enseña cada pantalla del panel de `pisos`, listo para pintar.

El panel es solo imágenes: cada pantalla es un diccionario que pinta la escena
(`assets/pisos/escena.html`, ver `bot.services.housing_scene`). Aquí se decide todo
lo que sale y se formatea cada cifra; la escena no calcula nada. Cada pantalla lleva
también `fallback`, unas líneas de texto para el dibujo de reserva con Pillow.

Sin emojis en nada de lo que se pinta: ni Node ni el Chromium del Docker traen su
fuente. Lo que lleva icono (el tipo de inmueble, el inquilino, el nivel de renta)
manda su clave y la escena dibuja el icono.

Pantallas (`screen`):

- `mapa`: Gran Canaria y Tenerife con una chincheta por barrio.
- `barrio`: los tres inmuebles de un barrio.
- `ficha`: un inmueble con todas sus cuentas.
- `cartera`: lo que tiene un miembro.
- `caja`: la cuenta de una compra o una venta, antes o después de pagar.

Las claves son estables: la escena las lee tal cual. Una pantalla nueva o una clave
nueva se añade aquí y en la escena a la vez.
"""

from __future__ import annotations

from typing import Any

from bot.repositories.housing import Holding, Market, MemberDay, News
from bot.services import housing as rules
from bot.services.economy import format_amount
from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIOS,
    ISLANDS,
    KINDS,
    LISTING_BY_KEY,
    RENT_LEVELS,
    TENANT_BY_KEY,
    Listing,
    listings_of,
)

State = dict[str, Any]

#: Cómo se dice cada estado de un inmueble con dueño.
STATUS_TEXT = {
    rules.VACANT: "Vacío, buscando inquilino",
    rules.RENTED: "Alquilado",
    rules.DEFAULTING: "El inquilino no paga",
    rules.EVICTING: "Desahucio en marcha",
    rules.SQUATTED: "Okupado",
}

#: Qué pasa en una noticia, en una línea. `{tenant}` y `{amount}` se rellenan.
NEWS_TEXT = {
    "compra": "{who} compra participaciones",
    "venta": "{who} vende participaciones",
    "firma": "{tenant} firma el contrato",
    "renueva": "{tenant} renueva el contrato",
    "se_va": "{tenant} se va del piso",
    "impago": "{tenant} deja de pagar",
    "paga": "{tenant} se pone al día (milagro)",
    "okupa": "Entran okupas en el piso vacío",
    "juicio": "Desahucio pedido: el juzgado da fecha",
    "desahucio": "Desahucio hecho: el piso vuelve a estar libre",
    "averia": "Se rompe el termo: {amount}",
    "humedades": "Humedades en el baño: {amount}",
    "derrama": "Derrama de la comunidad: {amount}",
    "ite": "Inspección Técnica de Edificios: {amount}",
    "vecino": "El vecino del 3.º pone una queja por el ruido",
    "cerradura": "Cambio de cerradura: {amount}",
}


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}".replace(".", ",") + " %"


def _change(index: float) -> str:
    delta = index - 1.0
    if abs(delta) < 0.0005:
        return "="
    return ("+" if delta > 0 else "−") + _pct(abs(delta))


def _name(names: dict[int, str], member: int) -> str:
    return names.get(member) or "Alguien"


def listing_status(market: Market, key: str, user_id: int) -> tuple[str, str]:
    """`(etiqueta, tipo)` de un inmueble: `venta`, `parcial`, `tuyo`, `compartido` o `ajeno`."""
    holders = market.shares_of(key)
    free = market.free_shares(key)
    mine = holders.get(user_id, 0)
    if not holders:
        return "En venta", "venta"
    if mine == rules.SHARES:
        return "Tuyo entero", "tuyo"
    if mine:
        return f"Tienes el {mine} %", "compartido"
    if free:
        return f"Queda el {free} % libre", "parcial"
    return "Vendido", "ajeno"


def news_line(item: News, names: dict[int, str]) -> str:
    """Una noticia en una línea."""
    tenant = TENANT_BY_KEY.get(item.tenant or "")
    text = NEWS_TEXT.get(item.kind, item.kind).format(
        who=_name(names, item.user_id) if item.user_id else "Alguien",
        tenant=tenant.name if tenant else "El inquilino",
        amount=format_amount(item.amount),
    )
    return f"{item.day.strftime('%d/%m')} · {LISTING_BY_KEY[item.listing].title}: {text}"


def map_state(market: Market, user_id: int, name: str, cash: int) -> State:
    """El mapa: cada barrio con su índice, lo que queda libre y si tienes algo allí."""
    islands = []
    for island, island_name in ISLANDS.items():
        pins = []
        for barrio in BARRIOS:
            if barrio.island != island:
                continue
            items = listings_of(barrio.key)
            index = market.index(barrio.key)
            pins.append(
                {
                    "key": barrio.key,
                    "name": barrio.name,
                    "x": barrio.x,
                    "y": barrio.y,
                    "change": _change(index),
                    "up": index > 1.0005,
                    "down": index < 0.9995,
                    "owned": any(user_id in market.holdings.get(i.key, {}) for i in items),
                    "free": sum(market.free_shares(i.key) for i in items),
                    "from": format_amount(min(rules.price(i, index) for i in items)),
                }
            )
        islands.append({"key": island, "name": island_name, "barrios": pins})
    worth = sum(market.value(h) for h in market.of_member(user_id))
    cheapest = [f"{pin['name']}: desde {pin['from']}" for i in islands for pin in i["barrios"]]
    return {
        "screen": "mapa",
        "title": "PISOS DE JOVANI",
        "subtitle": "El mercado inmobiliario de Canarias",
        "user": {"name": name, "cash": format_amount(cash), "worth": format_amount(worth)},
        "islands": islands,
        "footer": "Elige un barrio en el desplegable · precios reales × 10",
        "fallback": [
            "Pisos de Jovani: el mercado de Canarias",
            f"{name}: {format_amount(cash)} en efectivo, {format_amount(worth)} en ladrillo",
            *cheapest,
        ],
    }


def _card(market: Market, listing: Listing, user_id: int, names: dict[int, str]) -> State:
    index = market.index(listing.barrio)
    status, status_kind = listing_status(market, listing.key, user_id)
    lease = market.leases.get(listing.key)
    holders = market.shares_of(listing.key)
    value = rules.price(listing, index)
    rent = rules.reference_rent(listing, index)
    return {
        "key": listing.key,
        "kind": listing.kind,
        "kind_name": KINDS[listing.kind].name,
        "title": listing.title,
        "m2": f"{listing.m2} m²",
        "floor": listing.floor,
        "energy": listing.energy,
        "price": format_amount(value),
        "share_price": format_amount(rules.share_price(listing, index)),
        "rent": format_amount(rent) + "/mes",
        "yield": _pct(rent * 12 / value),
        "status": status,
        "status_kind": status_kind,
        "free": market.free_shares(listing.key),
        "owners": [
            {"name": _name(names, m), "shares": s, "mine": m == user_id}
            for m, s in sorted(holders.items(), key=lambda kv: -kv[1])
        ],
        "lease": STATUS_TEXT.get(lease.status, "") if lease else "",
    }


def barrio_state(market: Market, barrio_key: str, user_id: int, names: dict[int, str]) -> State:
    """Un barrio con sus tres inmuebles."""
    barrio = BARRIO_BY_KEY[barrio_key]
    index = market.index(barrio_key)
    cards = [_card(market, item, user_id, names) for item in listings_of(barrio_key)]
    return {
        "screen": "barrio",
        "barrio": {
            "key": barrio.key,
            "name": barrio.name,
            "town": barrio.town,
            "island": ISLANDS[barrio.island],
            "blurb": barrio.blurb,
            "index": f"{index:.3f}".replace(".", ","),
            "change": _change(index),
            "up": index > 1.0005,
            "down": index < 0.9995,
            "price_m2": format_amount(round(barrio.price_m2 * index * 10)) + "/m²",
        },
        "cards": cards,
        "footer": "Toca un inmueble para ver su ficha",
        "fallback": [
            f"{barrio.name} ({barrio.town})",
            barrio.blurb,
            *(f"{c['title']}: {c['price']} · {c['rent']} · {c['status']}" for c in cards),
        ],
    }


def listing_state(
    market: Market, key: str, user_id: int, names: dict[int, str], news: list[News]
) -> State:
    """La ficha de un inmueble: precio, renta, gastos, dueños, inquilino y noticias."""
    listing = LISTING_BY_KEY[key]
    index = market.index(listing.barrio)
    card = _card(market, listing, user_id, names)
    lease = market.leases.get(key)
    level = RENT_LEVELS[lease.level if lease else "referencia"]
    costs = rules.monthly_costs(listing)
    rent = rules.reference_rent(listing, index)
    asked = rules.asking_rent(listing, level, index)
    tenant = TENANT_BY_KEY.get(lease.tenant or "") if lease else None
    holding: Holding | None = market.holdings.get(key, {}).get(user_id)
    net = rent - costs.total
    full = rules.purchase_quote(listing, rules.SHARES, index).total
    state: State = {
        "screen": "ficha",
        **card,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "town": BARRIO_BY_KEY[listing.barrio].town,
        "blurb": listing.blurb,
        "rent_ref": format_amount(rent) + "/mes",
        "rent_asked": format_amount(asked) + "/mes",
        "level": level.name,
        "level_key": level.key,
        "costs": [
            {"label": "Comunidad", "amount": format_amount(costs.community)},
            {"label": "Seguro con impago", "amount": format_amount(costs.insurance)},
            {"label": "IBI", "amount": format_amount(costs.ibi)},
        ],
        "payback": f"{full / max(net, 1) / 30:.1f}".replace(".", ",") + " meses",
        "lease_status": lease.status if lease else "",
        "tenant": tenant.name if tenant else "",
        "tenant_key": tenant.key if tenant else "",
        "tenant_line": tenant.line if tenant else "",
        "contract_rent": format_amount(lease.rent) + "/mes" if lease and lease.rent else "",
        "until": lease.until.strftime("%d/%m/%Y") if lease and lease.until else "",
        "eviction_at": lease.eviction_at.strftime("%d/%m/%Y")
        if lease and lease.eviction_at
        else "",
        "arrears": format_amount(lease.arrears) if lease and lease.arrears else "",
        "mine": None,
        "news": [news_line(n, names) for n in news[:4]],
        "footer": "1 día del bot = 1 mes del contrato · ITP 6,5 % al comprar",
    }
    if holding is not None:
        value = market.value(holding)
        state["mine"] = {
            "shares": holding.shares,
            "value": format_amount(value),
            "paid": format_amount(holding.cost),
            "gain": ("+" if value >= holding.cost else "−")
            + format_amount(abs(value - holding.cost)),
            "up": value >= holding.cost,
        }
    state["fallback"] = [
        f"{listing.title} ({card['m2']}, {listing.floor})",
        f"{state['barrio']}: {card['price']} · {card['share_price']} la participación",
        f"Renta de referencia {state['rent_ref']} · pide {state['rent_asked']}",
        f"{card['status']} · {card['lease'] or 'en venta'}",
        listing.blurb,
    ]
    return state


def portfolio_state(
    market: Market,
    user_id: int,
    name: str,
    cash: int,
    week: MemberDay,
    news: list[News],
    names: dict[int, str],
) -> State:
    """Lo que tiene un miembro: cada inmueble, lo cobrado en 7 días y las noticias."""
    items = []
    for holding in market.of_member(user_id):
        listing = LISTING_BY_KEY[holding.listing]
        lease = market.leases.get(holding.listing)
        items.append(
            {
                "key": listing.key,
                "kind": listing.kind,
                "title": listing.title,
                "barrio": BARRIO_BY_KEY[listing.barrio].name,
                "shares": holding.shares,
                "value": format_amount(market.value(holding)),
                "status": STATUS_TEXT.get(lease.status, "") if lease else "",
                "status_kind": lease.status if lease else "",
                "rent": format_amount(lease.rent * holding.shares // rules.SHARES) + "/mes"
                if lease and lease.rent
                else "—",
            }
        )
    worth = sum(market.value(h) for h in market.of_member(user_id))
    state: State = {
        "screen": "cartera",
        "user": {"name": name, "cash": format_amount(cash), "worth": format_amount(worth)},
        "items": items,
        "week": {
            "rent": format_amount(week.rent),
            "ibi": format_amount(week.ibi),
            "costs": format_amount(week.costs),
            "net": format_amount(week.rent - week.ibi - week.costs),
        },
        "news": [news_line(n, names) for n in news[:6]],
        "empty": not items,
        "footer": "Lo cobrado es antes del IRPF, que se retiene al cobrar",
    }
    state["fallback"] = [
        f"Los pisos de {name}: {format_amount(worth)}",
        f"Últimos 7 días: {state['week']['rent']} de renta, {state['week']['net']} netos",
        *(f"{i['title']} ({i['shares']} %): {i['value']} · {i['status']}" for i in items),
    ] if items else [f"{name} no tiene ningún piso. Generación inquilina."]  # fmt: skip
    return state


def checkout_state(
    listing: Listing,
    *,
    selling: bool,
    lines: list[tuple[str, int, str]],
    balance: int,
    after: int,
    note: str,
    stamp: str | None = None,
) -> State:
    """La caja de una compra o una venta.

    Args:
        lines: `(concepto, importe, tipo)` en orden; `tipo` es `normal`, `tax`
            (impuesto: lleva a Perro Sanxe) o `total`.
        balance: Saldo antes.
        after: Saldo después.
        stamp: Sello encima (`PAGADO`, `VENDIDO`) cuando ya está hecho.
    """
    return {
        "screen": "caja",
        "selling": selling,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "lines": [
            {"label": label, "amount": format_amount(amount), "type": kind}
            for label, amount, kind in lines
        ],
        "balance": format_amount(balance),
        "after": format_amount(after),
        "short": after < 0,
        "note": note,
        "stamp": stamp,
        "footer": "Escritura ante notario · el dinero de verdad no existe",
        "fallback": [
            f"{'Venta' if selling else 'Compra'}: {listing.title}",
            *(f"{label}: {format_amount(amount)}" for label, amount, _kind in lines),
            f"Saldo: {format_amount(balance)} → {format_amount(after)}",
        ],
    }
