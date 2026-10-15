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
- `vpo`: las viviendas de protección oficial, el sorteo de los lunes y lo tuyo.

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
    VPO_BY_KEY,
    VPO_UNITS,
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
    rules.HABITUAL: "Vive su dueño",
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
    "hipoteca": "{who} firma una hipoteca de {amount}",
    "amortiza": "{who} cancela la hipoteca: {amount}",
    "ejecucion": "El banco ejecuta la hipoteca de {who}: subasta por {amount}",
    "se_muda": "{who} se muda aquí",
    "firma_miembro": "{who} firma el alquiler por {amount} al mes",
    "partida_agotada": "Partida agotada: {who} se queda sin el Bono Alquiler Joven",
    "licencia": "Licencia vacacional concedida (tasa de {amount})",
    "tope_ley": "La Ley de Vivienda topa la renta: zona tensionada",
    "recargo_ibi": "IBI recargado por vivienda vacía: {amount}",
    "oferta_fondo": "Buitre Capital ofrece {amount} por el inmueble entero",
    "venta_fondo": "{who} se lo vende a Buitre Capital",
    "sorteo_vpo": "A {who} le toca la VPO en el sorteo",
    "compra_vpo": "{who} compra la VPO por {amount}",
    "devuelve_vpo": "{who} devuelve la VPO a la Administración",
    "vpo_caducada": "A {who} se le pasa el plazo de la VPO",
}

#: Cómo se llama cada noticia de las VPO (su `listing` es `vpo:<clave>`).
VPO_PREFIX = "vpo:"

#: Pegatinas con guasa (solo presentación: ninguna regla las mira). Rentabilidad bruta anual a
#: partir de la cual un inmueble es un «chollo»; índice del barrio desde el que hay «burbuja» y
#: por debajo del cual el precio es «de fondo buitre».
CHOLLO_YIELD = 0.08
BURBUJA_INDEX = 1.3
BUITRE_INDEX = 0.88


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}".replace(".", ",") + " %"


def _change(index: float) -> str:
    delta = index - 1.0
    if abs(delta) < 0.0005:
        return "="
    return ("+" if delta > 0 else "−") + _pct(abs(delta))


def _name(names: dict[int, str], member: int) -> str:
    return names.get(member) or "Alguien"


def market_badge(index: float) -> str | None:
    """La pegatina del barrio según su índice: `burbuja`, `tensionada`, `buitre` o `None`."""
    if index >= BURBUJA_INDEX:
        return "burbuja"
    if rules.tense(index):
        return "tensionada"
    return "buitre" if index <= BUITRE_INDEX else None


def listing_badges(yearly_yield: float, index: float, *, offer: bool = False) -> list[str]:
    """Las pegatinas de un inmueble, de más a menos importante.

    Args:
        yearly_yield: Renta de un año entre el precio (0,08 = 8 %).
        index: Índice de su barrio.
        offer: Si el fondo buitre ha hecho una oferta por él (la ficha).
    """
    out = ["chollo"] if yearly_yield >= CHOLLO_YIELD else []
    badge = market_badge(index)
    if badge in ("burbuja", "buitre"):
        out.append(badge)
    if offer and "buitre" not in out:
        out.append("buitre")
    return out


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


def news_line(item: News, names: dict[int, str], *, titled: bool = True) -> str:
    """Una noticia en una línea.

    Args:
        titled: Si empieza por el inmueble (en la cartera sí; en su propia ficha, no).
    """
    tenant = TENANT_BY_KEY.get(item.tenant or "")
    text = NEWS_TEXT.get(item.kind, item.kind).format(
        who=_name(names, item.user_id) if item.user_id else "Alguien",
        tenant=tenant.name if tenant else "El inquilino",
        amount=format_amount(item.amount),
    )
    listing = LISTING_BY_KEY.get(item.listing)
    vpo = VPO_BY_KEY.get(item.listing.removeprefix(VPO_PREFIX))
    title = listing.title if listing is not None else vpo.name if vpo is not None else ""
    where = f"{title}: " if titled and title else ""
    return f"{item.day.strftime('%d/%m')} · {where}{text}"


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
                    "tense": rules.tense(index),
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
        "badges": listing_badges(rent * 12 / value, index),
        "status": status,
        "status_kind": status_kind,
        "free": market.free_shares(listing.key),
        "owners": [
            {"name": _name(names, m), "shares": s, "mine": m == user_id}
            for m, s in sorted(holders.items(), key=lambda kv: -kv[1])
        ],
        "lease": STATUS_TEXT.get(lease.status, "") if lease else "",
        "lease_kind": lease.status if lease else "",
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
            "badge": market_badge(index),
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
        # Un edificio entero no se alquila a una sola persona: son ocho viviendas.
        "tenant": (
            f"Ocho inquilinos (uno, {tenant.name[0].lower()}{tenant.name[1:]})"
            if tenant and listing.kind == "edificio"
            else tenant.name
            if tenant
            else ""
        ),
        "tenant_key": tenant.key if tenant else "",
        "tenant_line": tenant.line if tenant else "",
        "contract_rent": format_amount(lease.rent) + "/mes" if lease and lease.rent else "",
        "until": lease.until.strftime("%d/%m/%Y") if lease and lease.until else "",
        "eviction_at": lease.eviction_at.strftime("%d/%m/%Y")
        if lease and lease.eviction_at
        else "",
        "arrears": format_amount(lease.arrears) if lease and lease.arrears else "",
        "mine": None,
        "news": [news_line(n, names, titled=False) for n in news[:4]],
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
    state["mortgage"] = mortgage_box(market.mortgages_of(user_id, key))
    home = None
    for member, (kind, ref) in market.homes.items():
        if ref == key and kind in ("propia", "alquiler"):
            home = (member, kind)
    state["home"] = (
        ("Vives aquí" if home[0] == user_id else f"Vive {_name(names, home[0])}")
        if home is not None and home[1] == "propia"
        else (
            ("Vives aquí de alquiler" if home[0] == user_id
             else f"De alquiler: {_name(names, home[0])}")
            if home is not None
            else ""
        )
    )  # fmt: skip
    state["bono"] = bool(lease and lease.member is not None and lease.bono)
    state["license"] = bool(lease and lease.license)
    state["tense"] = rules.tense(index)
    offer = market.offers.get((key, user_id))
    state["offer"] = (
        {"price": format_amount(offer[0]), "until": offer[1].strftime("%d/%m")} if offer else None
    )
    state["badges"] = listing_badges(
        rent * 12 / rules.price(listing, index), index, offer=bool(offer)
    )
    state["socimi"] = user_id in market.socimi
    state["fallback"] = [
        f"{listing.title} ({card['m2']}, {listing.floor})",
        f"{state['barrio']}: {card['price']} · {card['share_price']} la participación",
        f"Renta de referencia {state['rent_ref']} · pide {state['rent_asked']}",
        f"{card['status']} · {card['lease'] or 'en venta'}",
        listing.blurb,
    ]
    return state


def mortgage_box(mortgages: list) -> State | None:  # list[Mortgage]
    """Lo que se enseña de las hipotecas de un inmueble (o `None` si no tiene)."""
    if not mortgages:
        return None
    owed = sum(m.owed for m in mortgages)
    payment = sum(m.payment for m in mortgages)
    unpaid = sum(m.unpaid_amount for m in mortgages)
    return {
        "owed": format_amount(owed),
        "payment": format_amount(payment) + "/mes",
        "months": f"{max(m.months_left for m in mortgages)} meses",
        "rate": _pct(mortgages[0].rate, 2),
        "unpaid": format_amount(unpaid) if unpaid else "",
        "unpaid_count": max(m.unpaid_count for m in mortgages),
        "aval": any(m.aval for m in mortgages),
    }


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
                "mortgage": _mortgage_line(market.mortgages_of(user_id, listing.key)),
                "home": market.homes.get(user_id) == ("propia", listing.key),
            }
        )
    unit = market.vpo_of(user_id)
    if unit is not None:
        spec = VPO_BY_KEY[unit]
        items.insert(
            0,
            {
                "key": f"{VPO_PREFIX}{unit}",
                "kind": "vpo",
                "title": spec.name,
                "barrio": spec.town,
                "shares": rules.SHARES,
                "value": format_amount(spec.price),
                "badges": ["vpo"],
                "status": "Tu vivienda habitual (VPO)",
                "status_kind": rules.HABITUAL,
                "rent": "—",
                "mortgage": _mortgage_line(market.mortgages_of(user_id, f"{VPO_PREFIX}{unit}")),
                "home": True,
            },
        )
    worth = sum(market.value(h) for h in market.of_member(user_id))
    worth += VPO_BY_KEY[unit].price if unit is not None else 0
    debt = sum(m.owed for m in market.mortgages_of(user_id)) + market.bank_debts.get(user_id, 0)
    home = market.homes.get(user_id)
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
        "debt": format_amount(debt) if debt else "",
        "bank_debt": format_amount(market.bank_debts[user_id])
        if market.bank_debts.get(user_id)
        else "",
        "euribor": "Euríbor " + _pct(market.euribor, 2),
        "living": _living(market, home, names),
        "socimi": user_id in market.socimi,
        "footer": "Lo cobrado es antes del IRPF, que se retiene al cobrar",
    }
    state["fallback"] = [
        f"Los pisos de {name}: {format_amount(worth)}",
        f"Últimos 7 días: {state['week']['rent']} de renta, {state['week']['net']} netos",
        *(f"{i['title']} ({i['shares']} %): {i['value']} · {i['status']}" for i in items),
    ] if items else [f"{name} no tiene ningún piso. Generación inquilina."]  # fmt: skip
    return state


def _mortgage_line(mortgages: list) -> str:
    box = mortgage_box(mortgages)
    if box is None:
        return ""
    late = f" · debes {box['unpaid']}" if box["unpaid"] else ""
    return f"Hipoteca {box['owed']} · {box['payment']}{late}"


def _living(market: Market, home: tuple[str, str] | None, names: dict[int, str]) -> str:
    if home is None:
        return "Vives con tus padres"
    kind, ref = home
    if kind == "vpo":
        return f"Vives en tu {VPO_BY_KEY[ref].name}"
    title = LISTING_BY_KEY[ref].title if ref in LISTING_BY_KEY else "un piso"
    if kind == "alquiler":
        owners = market.shares_of(ref)
        boss = max(owners, key=owners.get) if owners else None
        landlord = _name(names, boss) if boss is not None else "el mercado"
        return f"De alquiler en «{title}» (casero: {landlord})"
    return f"Vives en «{title}»"


def vpo_state(
    market: Market,
    user_id: int,
    names: dict[int, str],
    *,
    eligible: bool,
    reason: str,
    next_draw: str,
    income: int,
) -> State:
    """Las VPO: de quién es cada una, el sorteo de los lunes y lo tuyo.

    Args:
        eligible: Si puede apuntarse (sin casa y sin pasar del límite de ingresos).
        reason: Por qué no, si no puede.
        next_draw: Cuándo es el próximo sorteo («lunes 19/10»).
        income: Su neto medio de nómina al día (lo que mira el límite).
    """
    rate = market.euribor + rules.MORTGAGE_SPREAD
    units = []
    for unit in VPO_UNITS:
        owner = market.vpo.get(unit.key)
        reserved = market.reservations.get(unit.key)
        if owner is not None:
            mine = owner.owner_id == user_id
            status = "Tuya" if mine else f"De {_name(names, owner.owner_id)}"
            kind = "tuyo" if mine else "ajeno"
        elif reserved is not None:
            mine = reserved[0] == user_id
            who = "ti" if mine else _name(names, reserved[0])
            status = f"Reservada para {who} hasta el {reserved[1].strftime('%d/%m')}"
            kind = "compartido" if mine else "parcial"
        else:
            status, kind = "Libre: entra en el próximo sorteo", "venta"
        units.append(
            {
                "key": unit.key,
                "name": unit.name,
                "town": unit.town,
                "island": ISLANDS[unit.island],
                "m2": f"{unit.m2} m²",
                "price": format_amount(unit.price),
                "payment": format_amount(rules.installment(unit.price, rate)) + "/mes",
                "status": status,
                "status_kind": kind,
                "blurb": unit.blurb,
            }
        )
    signed = user_id in market.signups
    reservation = market.reservation_of(user_id)
    state: State = {
        "screen": "vpo",
        "title": "VIVIENDA PROTEGIDA",
        "subtitle": "Sorteo cada lunes · hipoteca al 100 % con aval del Estado",
        "units": units,
        "user": {
            "signed_up": signed,
            "eligible": eligible,
            "reason": reason,
            "reservation": VPO_BY_KEY[reservation].name if reservation else "",
            "owns": market.vpo_of(user_id) is not None,
            "income": format_amount(income) + "/día",
            "income_ok": income <= rules.vpo_income_cap(),
            "home_ok": not market.owns_home(user_id)
            and market.homes.get(user_id, ("alquiler", ""))[0] == "alquiler",
            "cap": format_amount(rules.vpo_income_cap()) + "/día",
            "signups": len(market.signups),
        },
        "next_draw": next_draw,
        "euribor": "Euríbor " + _pct(market.euribor, 2),
        "footer": "Precio tasado · IGIC al tipo cero · no se alquila · se devuelve al Estado",
    }
    state["fallback"] = [
        "Vivienda protegida: sorteo cada lunes",
        f"Próximo sorteo: {next_draw} · {len(market.signups)} apuntados",
        *(f"{u['name']}: {u['price']} · {u['status']}" for u in units),
    ]
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
        "key": listing.key,
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
