"""Simulador del negocio de `pala`: cuánto deja al día frente a la `pala` y a alquilar el local.

Juega días enteros con las funciones reales del bot: la cola de clientes y los tres
minijuegos (`business_games`), la demanda con su hora, sus estrellas y su precio
(`business.turn_clients`), las mermas y la reputación de cada noche, el recibo del local
(`nightly_bill`), la cuota de autónomo y el IRPF con la declaración de la semana
(`taxes.week_tax`, la actividad como única renta). El jugador compra stock para el día
siguiente y abre sus turnos a horas de jugador (por la mañana y por la tarde).

Sirve para calibrar los sectores de `business_catalog` (clientes, precios, costes, alquiler,
acondicionamiento): ver «Equilibrio» en `docs/negocios.md`.

Uso, desde la raíz del repo (`PYTHONIOENCODING=utf-8` en la consola de Windows):

    python docs/negocio_sim.py               # la tabla completa
    python docs/negocio_sim.py --dias 42     # más días

Jugadores: `perfecto` (todo bien y rápido), `bueno` (falla 1 de cada 8 clientes) y
`flojo` (falla 1 de cada 3). Dedicación: 4 turnos al día (con curro a jornada completa) u
8 (de excedencia o sin curro). La tabla da el neto medio diario de los días 15 al final
(con la reputación ya asentada), tras IRPF y cuota, y lo compara con el neto de cuatro
turnos de la `pala` en P1, P3 y P5 de obra.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bot.services.business import (  # noqa: E402
    REPUTATION_MAX,
    Business,
    drift,
    nightly_bill,
    opening_quote,
    spoil,
    turn_clients,
    turn_reputation,
    wholesale,
)
from bot.services.business_catalog import SECTORS, Sector  # noqa: E402
from bot.services.business_games import (  # noqa: E402
    COINS,
    TurnGame,
    apply_loyalty,
    make_clients,
)
from bot.services.housing_catalog import BARRIOS, Barrio  # noqa: E402
from bot.services.taxes import (  # noqa: E402
    SS_WORKER_RATE,
    WeekIncome,
    week_tax,
)
from bot.services.work import FLAT_RATE_FEE, SELF_EMPLOYED_FEE  # noqa: E402
from bot.services.work_catalog import JOB_BY_KEY  # noqa: E402

#: Horas a las que abre el jugador según sus turnos del día.
HOURS = {4: (10, 12, 17, 19), 8: (8, 9, 10, 11, 12, 17, 18, 19)}
#: Cada cuánto falla cada jugador.
PLAYERS = {"perfecto": 0.0, "bueno": 1 / 8, "flojo": 1 / 3}
#: Segundos por pulsación (el jugador perfecto va rápido).
CLICK_SECONDS = 1.5


def play_client(game: TurnGame, now: float, rng: random.Random, miss: float) -> float:
    """Atiende al cliente de ahora, bien o con un fallo típico. Devuelve la hora al acabar."""
    client = game.current
    assert client is not None
    wrong = rng.random() < miss
    kind = game.sector.game
    if kind == "comandas":
        order = dict(client.order)
        if wrong:  # sirve otra cosa
            key = next(iter(order))
            order[key] = order[key] + 1
        for key, n in order.items():
            for _ in range(n):
                now += CLICK_SECONDS
                game.press(f"p:{key}", now)
        game.press("serve", now)
    elif kind == "caja":
        if client.hidden and not wrong:
            game.press("thief", now)
        due = game.due() + (rng.choice((50, -50)) if wrong else 0)  # da de más o de menos
        for coin in COINS:
            while due >= coin:
                now += CLICK_SECONDS
                game.press(f"c:{coin}", now)
                due -= coin
        game.press("charge", now)
    else:
        fault = client.fault
        if wrong:
            fault = next(k for k in client.options if k != client.fault)
        now += 3 * CLICK_SECONDS
        game.press(f"d:{fault}", now)
        if game.current is client and game.step == "presupuesto":
            game.press("q:1", now)
        elif game.current is client:  # sin el recambio
            game.press("nopart", now)
    return now + CLICK_SECONDS


def restock(business: Business, need: dict[str, float]) -> int:
    """Compra lotes para que el stock cubra 1,3 días de lo que se gasta. Devuelve lo pagado."""
    paid = 0
    for product in business.sector_info.products:
        want = need.get(product.key, product.pack) * 1.3
        have = business.stock.get(product.key, 0)
        if have >= want:
            continue
        lots = max(1, int((want - have) // product.pack) + 1)
        base, tax = wholesale(product, lots * product.pack)
        business.stock[product.key] = have + lots * product.pack
        paid += base + tax
    return paid


@dataclass
class Run:
    """Lo que dejó una simulación."""

    net_day: float
    stars: float
    opening: int
    payback: float


def simulate(sector: Sector, barrio: Barrio, turns: int, miss: float, days: int, seed: int) -> Run:
    rng = random.Random(seed)
    business = Business(1, sector.key, barrio.key, "Sim", seed=seed)
    quote = opening_quote(sector, barrio)
    need = {p.key: float(p.pack) for p in sector.products}
    nets: list[float] = []
    week_net = 0
    cash = -quote.total + quote.deposit  # la fianza vuelve al cerrar: no es gasto
    payback = float("nan")
    for day in range(days):
        spent = restock(business, need)
        used: dict[str, int] = {}
        sales = 0
        for hour in HOURS[turns]:
            count = turn_clients(business, hour, rng.random())
            clients = make_clients(business, count, rng.randrange(2**31))
            if not clients:
                continue
            game = TurnGame(business, clients, now=0.0)
            now = 0.0
            while not game.finished(now):
                now = play_client(game, now, rng, miss)
            summary = game.summary()
            for key, units in summary.units.items():
                business.stock[key] = max(0, business.stock.get(key, 0) - units)
                used[key] = used.get(key, 0) + units
            change = turn_reputation(
                sector, summary.reputation, summary.served, price_reputation(business)
            )
            business.reputation = max(0.0, min(REPUTATION_MAX, business.reputation + change))
            business.regulars = apply_loyalty(business.regulars, summary.loyalty)
            sales += summary.sales
        need = {k: 0.7 * need.get(k, 0) + 0.3 * used.get(k, 0) for k in need}
        bill = nightly_bill(business).total
        spoil(business)
        if turns == 0:
            drift(business)
        fee = (FLAT_RATE_FEE if day < 7 else SELF_EMPLOYED_FEE) if day % 7 == 0 else 0
        day_net = sales - spent - bill - fee
        week_net += day_net
        tax = 0
        if day % 7 == 6:  # la declaración del lunes: la actividad es toda su renta
            tax = week_tax(WeekIncome(business=week_net)).quota
            week_net = 0
        cash += day_net - tax
        if payback != payback and cash >= 0:  # el primer día que recupera lo invertido
            payback = day + 1
        if day >= 14:
            nets.append(day_net - tax)  # el IRPF de la semana, el día que se declara
    return Run(
        net_day=sum(nets) / max(1, len(nets)),
        stars=business.stars,
        opening=quote.total,
        payback=payback,
    )


def pala_net(level: int) -> float:
    """Neto diario de cuatro turnos de jornada de obra en el puesto `level` (nota media)."""
    job = JOB_BY_KEY["obra"]
    gross_day = job.position(level, "").base_pay * 4
    week = gross_day * 7
    social = round(week * SS_WORKER_RATE)
    tax = week_tax(WeekIncome(work=week, social=social)).quota
    return (week - social - tax) / 7


def barrios_for(sector: Sector) -> list[Barrio]:
    """El barrio más barato, el del medio y el más caro para un sector."""
    ranked = sorted(BARRIOS, key=lambda b: opening_quote(sector, b).rent)
    return [ranked[0], ranked[len(ranked) // 2], ranked[-1]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dias", type=int, default=28)
    args = parser.parse_args()
    print("Pala (obra), neto diario a 4 turnos:")
    for level in (1, 3, 5):
        print(f"  P{level}: {pala_net(level):>8,.0f} Y$".replace(",", "."))
    print("(★ = estrellas al final; «a 5★» = día en que llega a las 5 estrellas)")
    print()
    print(f"{'sector':<10} {'barrio':<18} {'alquiler':>8} {'montar':>8} {'t':>2} "
          f"{'jugador':<9} {'neto/día':>9} {'★':>4} {'recupera':>9}")  # fmt: skip
    for sector in SECTORS:
        for barrio in barrios_for(sector):
            rent = opening_quote(sector, barrio).rent
            for turns in (4, 8):
                for player, miss in PLAYERS.items():
                    run = simulate(sector, barrio, turns, miss, args.dias, seed=7)
                    payback = f"día {run.payback:.0f}" if run.payback == run.payback else "nunca"
                    print(
                        f"{sector.key:<10} {barrio.name:<18} {rent:>8} {run.opening:>8} "
                        f"{turns:>2} {player:<9} {run.net_day:>9,.0f} {run.stars:>4} "
                        f"{payback:>9}".replace(",", ".")
                    )


if __name__ == "__main__":
    main()
