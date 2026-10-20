"""Calibra el Monte de Piedad y los préstamos de `banco` con las reglas reales del bot.

Uso, desde la raíz del repo (`PYTHONIOENCODING=utf-8` en la consola de Windows):

    python docs/monte_sim.py
    python docs/monte_sim.py --informe docs/metricas/informe-2026-10-17.json

Mira cuatro cosas, cada una con las constantes y funciones del bot:

1. Objetos gratis: si el Monte comprase lo que no salió de la tienda (cajas ganadas
   jugando, regalos de mascotas), cada 📦 gratis sería una devolución en efectivo sobre
   lo apostado. Se compara con la ventaja de la casa de cada juego (RTP del informe).
2. Rebajas: si paga sobre el precio de catálogo, comprar rebajado y vender al Monte
   crea dinero. Tope del porcentaje en ese caso.
3. Tabla por porcentaje de venta `s`: lo que se pierde en una compra y venta, cuánto
   abarata una herramienta de curro que se revende, el préstamo máximo para que
   empeñar y no volver no esquive el embargo, y el interés mínimo para que empeñar el
   domingo no esquive el Patrimonio del lunes.
4. Lo que podría entrar de golpe el primer día (lo que ya hay en las mochilas).
5. Préstamos: lo más que presta el banco según lo que se cobra al día y cuánto tarda el
   embargo de nómina (art. 607 LEC) en recuperar un préstamo que no se paga.

Las cifras elegidas (20/10/2026): vender al 85 % de lo pagado, empeñar al 55 % y devolver
un 5 % más (`bot.services.monte`); los topes de las secciones 1 a 3 los comprueba
`tests/unit/test_monte.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bot.services import loans as lending  # noqa: E402
from bot.services.housing import EURIBOR_START  # noqa: E402
from bot.services.housing_furniture import FURNITURE_KEYS  # noqa: E402
from bot.services.lootboxes import (  # noqa: E402
    BOX_BY_KEY,
    FREE_BOX_LEVELS,
    FREE_BOX_MIN_STAKE,
    FREE_BOX_PLAYS,
    expected_value,
    is_piece,
)
from bot.services.monte import PAWN_SHARE, SELL_SHARE  # noqa: E402
from bot.services.pets import GIFT_CHANCE, GIFT_MAX_PRICE, gift_weight  # noqa: E402
from bot.services.shop import MAX_DISCOUNT, Kind  # noqa: E402
from bot.services.shop_catalog import CATALOG, is_skin, is_title, use_of  # noqa: E402
from bot.services.taxes import (  # noqa: E402
    GARNISH_SHARE,
    IGIC_GENERAL_RATE,
    IGIC_RATES,
    WEALTH_BRACKETS,
)
from bot.services.work_tools import TOOLS  # noqa: E402

#: Jugadas mínimas de un juego en el informe para fiarse de su RTP.
MIN_PLAYS = 200
SHARES = [x / 100 for x in range(50, 100, 5)]


def pct(x: float) -> str:
    return f"{x * 100:5.1f} %"


def free_items(report: dict | None) -> float:
    """Sección 1. Devuelve el `s` máximo si el Monte pagara a precio de catálogo lo gratis."""
    carton = expected_value(BOX_BY_KEY["caja_carton"])
    staked = FREE_BOX_PLAYS * FREE_BOX_MIN_STAKE
    rake = carton / staked
    regalo = expected_value(BOX_BY_KEY["caja_regalo"])
    gifts = [
        e.price
        for e in CATALOG
        if e.kind is Kind.TROPHY
        and e.stock is None
        and e.price <= GIFT_MAX_PRICE
        and use_of(e.key) is None
        and not is_title(e.key)
        and not is_skin(e.key)
        and not is_piece(e.key)
        and e.key not in FURNITURE_KEYS
    ]
    weights = [gift_weight(p) for p in gifts]
    gift_ev = GIFT_CHANCE * sum(p * w for p, w in zip(gifts, weights, strict=True)) / sum(weights)

    print("## 1. Objetos que no salieron de la tienda")
    print(
        f"📦 gratis cada {FREE_BOX_PLAYS} jugadas de {FREE_BOX_MIN_STAKE}+ Y$ "
        f"({staked:,} Y$ apostados): vale {carton:,.0f} Y$ de media a catálogo "
        f"→ {pct(rake)} de lo apostado"
    )
    print(f"🎁 gratis cada {FREE_BOX_LEVELS} niveles: {regalo:,.0f} Y$ de media")
    print(
        f"Regalo de mascota: {gift_ev:,.0f} Y$ de media por primer cuidado del día "
        f"({len(gifts)} candidatos)"
    )
    if report is None:
        return 0.0
    games = [g for g in report["casino"] if g["jugadas"] >= MIN_PLAYS and g["apostado"]]
    print(
        f"\nSi el Monte pagara s × catálogo por las piezas de la 📦 gratis, la devolución "
        f"en efectivo sería {pct(rake)} × s de lo apostado. Ventaja de la casa medida "
        f"({report['desde']} a {report['hasta']}, juegos con {MIN_PLAYS}+ jugadas):"
    )
    worst = 1.0
    for g in sorted(games, key=lambda g: -g["jugadas"]):
        edge = 1 - g["pagado"] / g["apostado"]
        cap = max(0.0, edge / rake)
        worst = min(worst, cap)
        print(
            f"  {g['juego']:<12} {g['jugadas']:>6} jugadas  ventaja {pct(edge)}  "
            f"→ jugar sale gratis o a favor con s ≥ {pct(cap)}"
        )
    print(f"→ Con objetos gratis a catálogo, el Monte no podría pagar más de {pct(worst)}.")
    return worst


def discounts() -> None:
    lowest = min(r.rate for r in IGIC_RATES)
    cap = (1 - MAX_DISCOUNT / 100) * (1 + lowest)
    print("\n## 2. Rebajas de la tienda")
    print(
        f"Rebaja máxima {MAX_DISCOUNT} %: si el Monte paga sobre el precio de catálogo, "
        f"comprar rebajado y venderle crea dinero en cuanto s > {pct(cap)}."
    )
    print(
        "Si paga sobre lo que se pagó de verdad en la tienda (base sin IGIC), "
        "nunca crea dinero con s < 100 %."
    )


def table() -> None:
    top_wealth = WEALTH_BRACKETS[-1][1]
    g = IGIC_GENERAL_RATE
    print("\n## 3. Por porcentaje de venta s (sobre lo pagado en la tienda, sin IGIC)")
    print(
        f"IGIC general {pct(g)}, embargo {pct(GARNISH_SHARE)}, Patrimonio marginal "
        f"máximo {pct(top_wealth)} semanal"
    )
    print("   s   | pierdes al revender | herramienta/mueble | préstamo máx | interés mín")
    print("       | (de lo que pagaste) | cuesta (× precio)  | (embargo)    | (Patrimonio)")
    for s in SHARES:
        mark = "◀ elegido" if abs(s - SELL_SHARE) < 1e-9 else ""
        loss = (1 + g - s) / (1 + g)
        loan = (1 - GARNISH_SHARE) * s
        rate = top_wealth * (1 - loan) / loan
        print(
            f" {pct(s)} |      {pct(loss)}        |      {loss:4.2f}          |"
            f"   {pct(loan)}    |   {pct(rate)} {mark}"
        )
    print(f"Elegido: vender al {pct(SELL_SHARE)} y empeñar al {pct(PAWN_SHARE)}.")
    tools = sorted({e.price for e in CATALOG if e.key in {t.key for t in TOOLS}})
    furniture = sorted({e.price for e in CATALOG if e.key in FURNITURE_KEYS})
    print(
        f"Herramientas: {len(tools)} precios de {tools[0]:,} a {tools[-1]:,} Y$; "
        f"muebles: {len(furniture)} de {furniture[0]:,} a {furniture[-1]:,} Y$."
    )
    print(
        "«Cuesta» = lo que te sale usar una herramienta o mueble y revenderla, en "
        "proporción a no revenderla: su amortización se acorta en la misma proporción."
    )


def one_off(report: dict | None, held: int | None) -> None:
    print("\n## 4. El primer día")
    if held is None and report is not None:
        held = report["economia"]["cuentas_sistema"].get("tienda")
        print(
            f"Caja de la tienda {held:,} Y$ (todo lo gastado, también lo ya comido o "
            "usado): es un tope por arriba de lo que hay en las mochilas."
        )
    if held is None:
        print("Sin informe ni --mochilas: no se puede estimar.")
        return
    supply = report["economia"]["masa_monetaria"] if report else None
    for s in (0.5, 0.7, 0.8, 0.85, 0.9):
        line = f"  s = {pct(s)}: hasta {s * held:,.0f} Y$"
        if supply:
            line += f" ({s * held / supply:.1f} × el dinero de los miembros, {supply:,})"
        print(line)
    print(
        "Cifra exacta en el NAS (lo comprado en la tienda que sigue en mochilas):\n"
        "  SELECT SUM(p.base) FROM shop_purchases p JOIN shop_inventory i "
        "ON i.id = p.inventory_id\n  WHERE p.refunded = 0 AND i.status = 'active' "
        "AND i.kind = 'objeto' AND i.user_id > 0;"
    )


def loans() -> None:
    """Sección 5: lo que presta el banco y cuánto tarda el embargo en recuperarlo."""
    rate = lending.loan_rate(EURIBOR_START, [])
    print("\n## 5. Préstamos (cliente nuevo, sin seguro, Euríbor de partida)")
    print(f"TIN {pct(rate)}; las cuotas de todo no pasan del 35 % de lo que se ingresa.")
    # Neto de nómina al día: un turno del puesto 1 deja ~1,1 IMV máximos (1.650 Y$); cuatro
    # turnos, una jornada; y un puesto alto de rama, cinco veces eso.
    for label, net_day in (("1 turno de P1", 1_650), ("jornada de P1", 6_600),
                           ("jornada alta", 33_000)):  # fmt: skip
        room = lending.room_for(net_day, 0)
        parts = []
        for months in lending.TERMS:
            most = lending.max_principal(room, rate, months, insured=False)
            offer = lending.quote(most, months, rate, insured=False)
            parts.append(f"{months} m: {most:,} (cuesta {offer.cost:,})")
        print(f"  {label:<14} {net_day:>6,}/día · caben {room:,}/mes · " + " · ".join(parts))
    print("Si no paga, lo que debe pasa a deuda con el banco y se embarga de las nóminas:")
    for label, net_day in (("jornada de P1", 6_600), ("jornada alta", 33_000)):
        room = lending.room_for(net_day, 0)
        owed = lending.max_principal(room, rate, max(lending.TERMS), insured=False)
        per_day = lending.wage_garnish(0, net_day)
        days = owed / per_day if per_day else float("inf")
        print(
            f"  {label:<14} debe {owed:,} · se embargan {per_day:,}/día "
            f"(SMI diario {lending.SMI_DAY:,}) · {days:,.0f} días de trabajo para saldarlo"
        )
    print("Con nómina, no pagar no sale gratis; sin trabajar no hay sueldo que embargar, pero")
    print("tampoco préstamo (sin ingresos no cabe nada en el 35 %).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--informe", type=Path, help="informe JSON de `informe`")
    parser.add_argument("--mochilas", type=int, help="Y$ comprados que siguen en mochilas")
    args = parser.parse_args()
    path = args.informe
    if path is None:
        found = sorted((ROOT / "docs" / "metricas").glob("informe-*.json"))
        path = found[-1] if found else None
    report = json.loads(path.read_text(encoding="utf-8")) if path else None
    free_items(report)
    discounts()
    table()
    one_off(report, args.mochilas)
    loans()


if __name__ == "__main__":
    main()
