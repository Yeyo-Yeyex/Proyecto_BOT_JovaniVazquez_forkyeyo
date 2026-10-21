"""Lo que enseñan las pantallas del negocio y el inicio de `pala`: un diccionario por pantalla.

La escena (`assets/negocio/escena.html`, ver `bot.services.business_scene`) pinta cada
estado y no calcula nada: aquí se decide lo que sale y se formatea cada cifra (texto plano,
sin emojis: la escena no tiene su fuente). Las claves son estables y las lee la escena tal
cual; una pantalla nueva o una clave nueva se añade aquí y en la escena a la vez. El
contrato completo de cada pantalla está en `docs/negocios.md` («Las pantallas») y en la
cabecera de la escena.

Pantallas: `dia` (el inicio de `pala`), `montar`, `negocio` (portada y vista previa),
`almacen`, `cuentas`, `cliente` (el minijuego) y `cierre` (el resumen del turno). Desde la
fase 2, también `mejoras` (las mejoras del local y las campañas) y `resenas`; el traslado
se enseña con la vista previa de `negocio` (`traslado_state`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from bot.services.agenda import JORNADA_NAMES, Agenda
from bot.services.business import (
    RECOMMENDED_RATING,
    RECOMMENDED_REVIEWS,
    Business,
    MoveQuote,
    book_value,
    campaign_boost,
    nightly_bill,
    opening_quote,
    rating,
    recommended,
    regular_names,
    sale_price,
    stars,
)
from bot.services.business_catalog import (
    AMORTIZATION,
    CAMPAIGN_BY_KEY,
    CAMPAIGNS,
    EMBLEMS,
    PALETTES,
    PRICE_BY_KEY,
    REVIEW_LINES,
    SECTOR_BY_KEY,
    SECTORS,
    SIZES,
    equipment_for,
)
from bot.services.business_games import TurnGame
from bot.services.economy import format_amount
from bot.services.housing_catalog import BARRIO_BY_KEY, BARRIOS
from bot.services.levels import TIMEZONE
from bot.services.taxes import igic

State = dict[str, Any]

DAYS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
    "octubre", "noviembre", "diciembre",
)  # fmt: skip

#: Partidas de las cuentas de la semana: `(clave en Business.week_totals, nombre, clase)`.
ACCOUNT_ROWS: tuple[tuple[str, str, str], ...] = (
    ("ventas", "Ventas y propinas", "in"),
    ("compras", "Compras al mayorista", "out"),
    ("local", "Local (alquiler y suministros)", "out"),
    ("marketing", "Publicidad", "out"),
    ("amortizacion", "Amortización", "out"),
    ("apertura", "Tasas de apertura y traslado", "out"),
    ("cuota", "Cuota de autónomo", "tax"),
    ("irpf", "IRPF a cuenta", "tax"),
)
#: Título del tique según la nota del turno.
GRADE_TITLES: dict[str, str] = {
    "S": "Turno de leyenda",
    "A": "Buen turno",
    "B": "Turno cumplidor",
    "C": "Turno regular",
    "D": "Turno para olvidar",
}


def signed(amount: int) -> str:
    """`+1.234 Y$` o `−1.234 Y$` (el signo menos de verdad); `0 Y$` sin signo."""
    if amount == 0:
        return format_amount(0)
    if amount < 0:
        return "−" + format_amount(-amount)
    return "+" + format_amount(amount)


def plural(n: int, one: str, many: str) -> str:
    """`1 franja`, `3 franjas`."""
    return f"{n} {one if n == 1 else many}"


def date_text(now: float) -> str:
    """`Martes 20 de octubre`."""
    moment = datetime.fromtimestamp(now, TIMEZONE)
    return f"{DAYS[moment.weekday()]} {moment.day} de {MONTHS[moment.month - 1]}"


def clock_text(now: float) -> str:
    """`18:42` en hora canaria."""
    return datetime.fromtimestamp(now, TIMEZONE).strftime("%H:%M")


def _style(business: Business) -> State:
    main, accent = business.colors
    return {
        "name": business.name,
        "sector": business.sector,
        "main": main,
        "accent": accent,
        "emblem": business.emblem_key,
    }


def low_stock(business: Business) -> list[str]:
    """Productos con menos de medio lote."""
    return [
        p.name.lower()
        for p in business.sector_info.products
        if business.stock.get(p.key, 0) < max(1, p.pack // 2)
    ]


def _alert(business: Business) -> str | None:
    if business.rent_debt:
        return f"Debes {format_amount(business.rent_debt)} del local"
    low = low_stock(business)
    if len(low) == len(business.sector_info.products):
        return "Sin stock: pasa por el almacén"
    if low:
        return f"Poco stock: {low[0]}" + (f" y {len(low) - 1} más" if len(low) > 1 else "")
    return None


# -- El inicio de `pala` --------------------------------------------------------------


def dia_state(
    who: str,
    now: float,
    agenda: Agenda,
    work: Any | None,
    business: Business | None,
    battery_left: float | None = None,
) -> State:
    """El inicio de `pala`: las franjas del día y las tarjetas del curro y del negocio.

    Args:
        who: Nombre del miembro.
        agenda: Sus franjas de hoy.
        work: El `pala.Status` de su contrato, o `None` sin curro.
        business: Su negocio (con los contadores de hoy al día), o `None`.
        battery_left: Su batería sin curro (la que gasta en el negocio); con curro sale
            de `work`. Sin curro no hay familia que enseñar.
    """
    job: State | None = None
    jornada = "Sin curro"
    family = None
    battery = None if battery_left is None else max(0, min(100, round(battery_left)))
    if work is not None:
        contract = work.contract
        jornada = "Excedencia" if contract.on_excedencia else JORNADA_NAMES[contract.jornada]
        battery = max(0, min(100, round(work.battery)))
        family = max(0, min(100, round(work.family)))
        red, green, blue = work.job.rgb
        if contract.on_excedencia:
            note = "De excedencia: no ficha"
        elif contract.reduced:
            note = "Jornada reducida: sin horas extra"
        else:
            note = f"Próximo turno: {work.next_kind.value}"
        job = {
            "icon": work.job.key,
            "name": work.job.name,
            "title": work.position.title,
            "level": f"{contract.level}/{work.job.top}",
            "pay": f"{format_amount(work.position.base_pay)} por turno",
            "today": f"{agenda.ordinary} de {contract.jornada} turnos de jornada",
            "rgb": f"#{red:02x}{green:02x}{blue:02x}",
            "note": note,
        }
    card: State | None = None
    if business is not None:
        turns = business.turns_today
        card = {
            **_style(business),
            "sector_name": business.sector_info.name,
            "barrio": business.barrio_info.name,
            "stars": business.stars,
            "today": f"{signed(business.sales_today)} hoy",
            "status": plural(turns, "turno hoy", "turnos hoy") if turns else "Hoy aún cerrado",
            "alert": _alert(business),
        }
    free = agenda.free
    if work is None and business is None:
        tip = "Busca un curro o monta tu negocio: el día tiene 8 franjas para repartir."
    elif free == 0:
        tip = "Hoy ya no te quedan franjas libres. Mañana más."
    elif business is not None:
        tip = f"Te quedan {plural(free, 'franja', 'franjas')}: cada turno en tu negocio gasta una."
    else:
        tip = (
            f"La jornada reserva sus franjas aunque no fiches. Las otras {free} son para horas "
            "extra… o para un negocio propio."
        )
    return {
        "screen": "dia",
        "who": who,
        "date": date_text(now),
        "clock": clock_text(now),
        "slots": [{"kind": kind} for kind in agenda.slots()],
        "free": plural(free, "franja libre", "franjas libres"),
        "jornada": jornada,
        "battery": battery,
        "family": family,
        "job": job,
        "business": card,
        "tip": tip,
    }


# -- Montar ---------------------------------------------------------------------------


def montar_state(selected: str | None = None) -> State:
    """Elegir sector: los tres locales con sus cifras."""
    cards = []
    for sector in SECTORS:
        quotes = [opening_quote(sector, barrio) for barrio in BARRIOS]
        low, high = (format_amount(f(q.rent for q in quotes)) for f in (min, max))
        cards.append(
            {
                "key": sector.key,
                "name": sector.name,
                "blurb": sector.blurb,
                "lines": [
                    f"Local de {sector.m2} m²",
                    f"Alquiler: de {low} a {high} al día",
                    f"Montarlo: desde {format_amount(min(q.total for q in quotes))}",
                ],
                "selected": sector.key == selected,
            }
        )
    return {
        "screen": "montar",
        "title": "Elige qué negocio montas",
        "sectors": cards,
        "foot": "Eres autónomo: respondes con lo tuyo. El alquiler corre aunque no abras.",
    }


def preview_state(sector: str, barrio: str, name: str, palette: int, emblem: int) -> State:
    """La fachada de un negocio antes de montarlo (con el rótulo elegido)."""
    main, accent = PALETTES[palette % len(PALETTES)][1:]
    place = BARRIO_BY_KEY[barrio]
    quote = opening_quote(SECTOR_BY_KEY[sector], place)
    return {
        "screen": "negocio",
        "mode": "preview",
        "name": name,
        "sector": sector,
        "barrio": place.name,
        "theme": place.theme,
        "main": main,
        "accent": accent,
        "emblem": EMBLEMS[emblem % len(EMBLEMS)][0],
        "open": False,
        "stars": stars(50),
        "seed": hash_seed(f"{sector}:{barrio}:{name}"),
        "stats": [],
        "tags": [place.town, f"Alquiler: {format_amount(quote.rent)} al día"],
        "regulars": [],
        "foot": f"Montarlo cuesta {format_amount(quote.total)} con la fianza.",
    }


def hash_seed(text: str) -> int:
    """Una semilla estable de un texto (sin `hash()`, que cambia en cada arranque)."""
    seed = 0
    for char in text:
        seed = (seed * 131 + ord(char)) % 2_147_483_647
    return seed


# -- El negocio -----------------------------------------------------------------------


def negocio_state(business: Business, *, foot: str, rivals: int = 0) -> State:
    """La portada del negocio con las cifras de hoy.

    Fase 2: `rating` (media de las reseñas, cuántas y si sale recomendado), `campaign` (la
    campaña de hoy y lo que le queda), `equipment` (las claves de las mejoras instaladas) y
    `size` (el tamaño del local y si es propio).
    """
    bill = nightly_bill(business)
    week = sum(business.week_totals.values())
    tags = [PRICE_BY_KEY[business.price].name.split(" (")[0] + " de precio"]
    if alert := _alert(business):
        tags.append(alert)
    if rivals:
        tags.append(plural(rivals, "competidor en el barrio", "competidores en el barrio"))
    today = date.fromisoformat(business.day) if business.day else None
    return {
        "screen": "negocio",
        "mode": "portada",
        **_style(business),
        "barrio": business.barrio_info.name,
        "theme": business.barrio_info.theme,
        "open": business.turns_today > 0,
        "stars": business.stars,
        "seed": business.seed,
        "stats": [
            {"label": "Caja de hoy", "value": signed(business.sales_today)},
            {"label": "Clientes hoy", "value": str(business.clients_today)},
            {"label": "Turnos hoy", "value": str(business.turns_today)},
            {"label": "Esta noche", "value": signed(-bill.total)},
            {"label": "Esta semana", "value": signed(week)},
            {"label": "Mejor turno", "value": signed(business.best_turn)},
        ],
        "tags": tags[:4],
        "regulars": [
            {"name": name, "loyalty": business.regulars.get(name, 0)}
            for name in regular_names(business.seed)
        ],
        "rating": rating_state(business),
        "campaign": campaign_state(business, today) if today else None,
        "equipment": list(business.equipment),
        "size": size_text(business),
        "foot": foot,
    }


def size_text(business: Business) -> str:
    """`Local mediano · propio` o `Local pequeño`."""
    name = f"Local {business.size_info.name.lower()}"
    return f"{name} · propio" if business.own_listing else name


def rating_state(business: Business) -> State | None:
    """La media de las reseñas para la escena (`None` si aún no hay ninguna)."""
    average, count = rating(business)
    if not count:
        return None
    return {
        "avg": f"{average:.1f}".replace(".", ","),
        "value": round(average, 2),
        "count": plural(count, "reseña", "reseñas"),
        "badge": recommended(business),
    }


def campaign_state(business: Business, today: date) -> State | None:
    """La campaña de hoy (`None` si no hay)."""
    if not campaign_boost(business, today):
        return None
    campaign = CAMPAIGN_BY_KEY[business.campaign]
    left = (date.fromisoformat(business.campaign_until) - today).days + 1
    return {
        "key": campaign.key,
        "name": campaign.name,
        "left": plural(left, "día", "días"),
        "boost": f"+{round(campaign.boost * 100)} % de clientes",
    }


def almacen_state(business: Business, foot: str) -> State:
    """El stock: cada producto con sus unidades, coste, precio y si caduca."""
    low = set(low_stock(business))
    products = []
    for product in business.sector_info.products:
        products.append(
            {
                "key": product.key,
                "name": product.name,
                "units": str(business.stock.get(product.key, 0)),
                "cost": format_amount(product.cost),
                "price": format_amount(sale_price(product, business.level(product.key).key)),
                # Fase 2: el nivel de precios si no es el general (`None` si va con el resto).
                "level": (
                    business.level(product.key).name.split(" (")[0]
                    if product.key in business.prices
                    else None
                ),
                "spoil": (
                    f"Caduca un {round(product.spoil * 100)} % cada noche"
                    if product.spoil
                    else None
                ),
                "low": product.name.lower() in low,
            }
        )
    return {"screen": "almacen", **_style(business), "products": products, "foot": foot}


def _week_column(title: str, totals: dict[str, int]) -> State:
    rows = [
        {"label": label, "value": signed(totals[key]), "kind": kind}
        for key, label, kind in ACCOUNT_ROWS
        if totals.get(key)
    ]
    result = sum(totals.values())
    return {
        "title": title,
        "rows": rows,
        "total": {"label": "Resultado", "value": signed(result), "good": result >= 0},
    }


def cuentas_state(business: Business) -> State:
    """La cuenta de resultados de esta semana y la anterior."""
    weeks = [_week_column("Esta semana", business.week_totals)]
    if business.last_week:
        weeks.append(_week_column("La semana pasada", business.last_week))
    return {
        "screen": "cuentas",
        **_style(business),
        "weeks": weeks,
        "foot": "Criterio de caja: cada cosa cuenta el día que se cobra o se paga, salvo las "
        "mejoras y la obra, que se amortizan cada noche. La Renta del lunes ajusta el IRPF.",
    }


# -- Fase 2: mejoras, reseñas y traslado ------------------------------------------------


def mejoras_state(business: Business, today: date, foot: str = "") -> State:
    """Las mejoras del sector (instaladas o no) y las campañas de publicidad.

    Cada mejora: `key`, `name`, `effect`, `price` (con IGIC), `class` (su amortización) y
    `owned`; si está instalada, `book` (lo que queda por amortizar). Cada campaña: `key`,
    `name`, `effect`, `price` y `days`. `active` es la campaña en marcha (`None` si no hay).
    """
    left = {a["key"]: a["left"] for a in business.assets}
    items = []
    for item in equipment_for(business.sector):
        name, rate = AMORTIZATION[item.kind]
        owned = item.key in business.equipment
        items.append(
            {
                "key": item.key,
                "name": item.name,
                "effect": item.effect(),
                "price": format_amount(item.cost + igic(item.cost)),
                "class": f"{name} · {round(rate * 100)} % al año",
                "owned": owned,
                "book": (
                    f"Por amortizar: {format_amount(left.get(item.key, 0))}" if owned else None
                ),
            }
        )
    campaigns = [
        {
            "key": c.key,
            "name": c.name,
            "effect": f"+{round(c.boost * 100)} % de clientes y de cupo",
            "price": format_amount(c.cost + igic(c.cost)),
            "days": plural(c.days, "día", "días"),
        }
        for c in CAMPAIGNS
    ]
    return {
        "screen": "mejoras",
        **_style(business),
        "items": items,
        "campaigns": campaigns,
        "active": campaign_state(business, today),
        "book": f"Por amortizar en total: {format_amount(book_value(business))}",
        "foot": foot
        or "Las mejoras se amortizan cada noche: su gasto se reparte, no se paga dos veces.",
    }


def _when(day: str, today: date) -> str:
    days = (today - date.fromisoformat(day)).days
    return "hoy" if days <= 0 else "ayer" if days == 1 else f"hace {days} días"


def resenas_state(business: Business, today: date) -> State:
    """Las reseñas: la media, cuántas, si sale recomendado y las seis últimas."""
    average, count = rating(business)
    reviews = [
        {
            "name": who,
            "stars": n,
            "line": REVIEW_LINES[n][line % len(REVIEW_LINES[n])],
            "day": _when(day, today),
        }
        for day, who, n, line in reversed(business.reviews[-6:])
    ]
    badge = recommended(business)
    need = (
        f"Con {RECOMMENDED_REVIEWS} reseñas y {str(RECOMMENDED_RATING).replace('.', ',')} de "
        "media, sales recomendado: un 10 % más de clientes."
    )
    return {
        "screen": "resenas",
        **_style(business),
        "avg": f"{average:.1f}".replace(".", ",") if count else "—",
        "value": round(average, 2),
        "count": plural(count, "reseña", "reseñas"),
        "badge": badge,
        "reviews": reviews,
        "foot": "Recomendado en la guía del barrio." if badge else need,
    }


def traslado_state(
    business: Business, barrio: str, size: int, quote: MoveQuote, *, own: bool = False
) -> State:
    """La fachada del local al que se trasladaría (vista previa de `negocio`)."""
    main, accent = business.colors
    place = BARRIO_BY_KEY[barrio]
    rent = (
        "Local propio: sin alquiler"
        if own
        else f"Alquiler: {format_amount(quote.opening.rent)} al día"
    )
    lose = "" if quote.same_barrio else " Cambias de barrio: pierdes media clientela."
    return {
        "screen": "negocio",
        "mode": "preview",
        "name": business.name,
        "sector": business.sector,
        "barrio": place.name,
        "theme": place.theme,
        "main": main,
        "accent": accent,
        "emblem": business.emblem_key,
        "open": False,
        "stars": business.stars,
        "seed": business.seed,
        "stats": [],
        "tags": [place.town, f"Local {SIZES[size].name.lower()}", rent],
        "regulars": [],
        "foot": f"Trasladarte cuesta {format_amount(quote.total)}.{lose}",
    }


# -- Un turno -------------------------------------------------------------------------


def cliente_state(business: Business, game: TurnGame) -> State:
    """El minijuego: el cliente de ahora con lo que pide y la cola.

    Solo depende del cliente, de su sitio en la cola y del paso del taller: lo que pase
    con los de antes no cambia su imagen, y por eso se pinta antes de que llegue.
    """
    client = game.current
    assert client is not None
    state: State = {
        "screen": "cliente",
        **_style(business),
        "theme": business.barrio_info.theme,
        "client": {
            "seed": client.seed,
            "name": client.regular,
            "regular": client.regular is not None,
            "line": "Lo de siempre." if client.usual else client.line,
        },
        "queue": game.queue,
        "index": f"Cliente {game.index + 1} de {len(game.clients)}",
        # La caja del turno va en el texto: así la imagen del siguiente cliente no depende de
        # cómo acabe este y se puede pintar mientras se le atiende.
        "cash": "",
        "patience": f"{round(game.patience)} s",
    }
    if game.sector.game == "comandas":
        state["usual"] = client.usual
        state["order"] = (
            None if client.usual else [{"key": k, "n": n} for k, n in client.order.items()]
        )
    elif game.sector.game == "caja":
        state["basket"] = [
            {"key": k, "n": n, "price": format_amount(game.price_of(k) * n)}
            for k, n in client.order.items()
        ]
        state["total"] = format_amount(game.total())
        state["paid"] = format_amount(game.paid)
        state["thief"] = client.hidden is not None
    else:
        state["car"] = {"color": client.car_color, "model": client.car_model}
        state["symptom"] = client.symptom
        state["cue"] = client.cue
        state["step"] = game.step
    return state


def cierre_state(business: Business, result: Any) -> State:
    """El tique del turno (`negocio.TurnResult`)."""
    summary = result.summary
    total = len(summary.outcomes)
    stats = [
        {"label": "Atendidos", "value": f"{summary.served} de {total}"},
        {"label": "Caja", "value": signed(summary.revenue)},
        {"label": "Propinas", "value": signed(summary.tips)},
        {"label": "Coste de lo vendido", "value": signed(-summary.sold_cost)},
    ]
    if summary.waste_cost:
        stats.append({"label": "Perdido", "value": signed(-summary.waste_cost)})
    if result.withheld:
        stats.append({"label": "IRPF a cuenta", "value": signed(-result.withheld)})
    return {
        "screen": "cierre",
        **_style(business),
        "grade": summary.grade,
        "title": GRADE_TITLES[summary.grade],
        "stats": stats[:6],
        "stars_before": result.stars_before,
        "stars_after": business.stars,
        "lines": turn_lines(result)[:4],
    }


def turn_lines(result: Any) -> list[str]:
    """Lo que destacar de un turno (`negocio.TurnResult`), en frases cortas."""
    summary = result.summary
    lines = []
    if result.best:
        lines.append("Tu mejor turno hasta ahora")
    for name, change in summary.loyalty.items():
        if change > 0:
            loyalty = result.business.regulars.get(name, 0)
            lines.append(f"{name} sube a fidelidad {loyalty}")
        elif change < 0:
            loyalty = result.business.regulars.get(name, 0)
            lines.append(f"{name} baja a fidelidad {loyalty}")
    if caught := sum(1 for o in summary.outcomes if "caught" in o.events):
        lines.append(plural(caught, "listillo pillado", "listillos pillados"))
    if missed := summary.count("left"):
        lines.append(
            plural(missed, "cliente se cansó de esperar", "clientes se cansaron de esperar")
        )
    if gone := summary.count("gone"):
        lines.append(plural(gone, "se quedó en la cola", "se quedaron en la cola"))
    return lines
