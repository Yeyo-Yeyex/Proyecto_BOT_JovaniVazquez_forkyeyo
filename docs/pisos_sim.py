"""Calibra `pisos`: cuánto renta cada inmueble y en cuánto se paga solo, con el código real.

Simula cada inmueble del catálogo durante `DAYS` días (meses del juego) con
`housing.advance_day` y `housing.settle_day`, el nivel de renta de referencia y un
dueño que solo tiene ese piso, y saca la renta neta media al día (después de vacíos,
imprevistos, IBI, comunidad, seguro e IRPF) y los días que tarda en pagar lo que costó
(precio, ITP y notaría). El IRPF es el de la declaración de cada semana
(`taxes.week_tax`), como en el bot desde la 8.0.0. Desde la 8.1.0 el seguro del hogar paga
el impuesto de primas, las reparaciones su IGIC y cada desahucio el abogado y el procurador.

Además dice cuánto dinero se crea de la nada si todo el servidor tiene alquilados los
inmuebles más baratos, para compararlo con lo que pagan las nóminas de `pala` (Biblia,
«Vigilar el dinero que se crea de la nada»), y lo que pide el banco por la hipoteca de
cada vivienda: la entrada, la cuota y el neto de nómina al día con que cabe en el 35 %.

Con `--niveles` dice, para un inmueble de cada tipo de barrio, cuánto se cobra de media
con cada nivel de renta frente a la de referencia: los nómadas dependen del barrio
(`housing_catalog.NOMAD_DEMAND`).

Uso (desde la raíz del repo):

    python docs/pisos_sim.py              # todos los inmuebles, 3 semillas
    python docs/pisos_sim.py --seeds 10   # más semillas, cifras más estables
    python docs/pisos_sim.py --niveles    # renta cobrada por nivel y tipo de barrio

En la consola de Windows hace falta `PYTHONIOENCODING=utf-8`.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bot.services import housing  # noqa: E402
from bot.services.housing_catalog import BARRIOS, LISTINGS, Listing  # noqa: E402
from bot.services.taxes import PropertyWeek, WeekIncome, week_tax  # noqa: E402

#: Días simulados (meses del juego): dos años y medio de alquiler.
DAYS = 30
#: Nóminas de `pala` que se pagaron al día en el servidor (informe del 14/10/2026:
#: 765.138 Y$ brutos en 30 días).
PALA_PER_DAY = 765_138 // 30


def simulate(listing: Listing, seed: int) -> float:
    """Neto medio al día de un inmueble entero de un dueño que solo tiene eso.

    El IRPF es el de la declaración de cada semana con solo esa renta (`taxes.week_tax`):
    gastos devengados por inmueble, tope de reparaciones y reducción del art. 23.2.
    """
    rng = random.Random(seed)
    start = date(2026, 10, 1)
    lease = housing.Lease(since=start)
    paid = housing.price(listing)
    reduction = housing.contract_reduction(listing, lease.level, index=1.0, rent=1, last_rent=0)
    total = 0
    week = PropertyWeek(ref=listing.key, reduction=reduction)
    for offset in range(DAYS):
        day = start + timedelta(days=offset)
        out = housing.advance_day(listing, lease, day, 1.0, rng)
        taxes = round(out.costs.insurance * housing.IPS_RATE) + round(
            housing.repairs_of(out.events) * housing.SERVICES_IGIC
        )
        payout = housing.settle_day(out.rent, out.costs, out.extra, lease.arrears,
                                    lease.arrears_ibi, taxes=taxes)  # fmt: skip
        lease = replace(out.lease, arrears=payout.arrears, arrears_ibi=payout.arrears_ibi)
        evicting = lease.status == housing.EVICTING
        lease = _evict(lease, day, rng)
        if lease.status == housing.EVICTING and not evicting:
            # Abogado y procurador (sin seguro de impago), con su IGIC: un gasto deducible.
            fee = housing.eviction_fee(lease.rent or housing.reference_rent(listing))
            total -= fee + round(fee * housing.SERVICES_IGIC)
        gross = payout.net + payout.ibi + payout.costs + payout.taxes
        week = week.merged(
            PropertyWeek(
                ref=listing.key,
                income=gross,
                finance=out.extra,
                costs=out.costs.total + housing.amortization(paid),
                reduction=reduction,
            )
        )
        total += payout.net
        if offset % 7 == 6 or offset == DAYS - 1:
            total -= week_tax(WeekIncome(properties=(week,))).quota
            week = PropertyWeek(ref=listing.key, reduction=reduction)
    return total / DAYS


def _evict(lease: housing.Lease, day: date, rng: random.Random) -> housing.Lease:
    """Lo que hace un dueño atento: pide el desahucio de okupas y de quien debe tres meses.

    Sin esto los okupas se quedan para siempre y la renta sale muy por debajo de la real.
    """
    if lease.status == housing.SQUATTED or (
        lease.status == housing.DEFAULTING and lease.unpaid >= 3
    ):
        return housing.start_eviction(lease, day, rng)
    return lease


def collected(listing: Listing, level: str, seeds: int, days: int = 3_000) -> float:
    """Renta cobrada media al día con un nivel, entre la renta de referencia."""
    total = 0
    start = date(2026, 1, 1)
    for seed in range(seeds):
        rng = random.Random(seed)
        lease = housing.Lease(level=level, since=start)
        for offset in range(days):
            day = start + timedelta(days=offset)
            out = housing.advance_day(listing, lease, day, 1.0, rng)
            lease = _evict(out.lease, day, rng)
            total += out.rent
    return total / (days * seeds) / housing.reference_rent(listing)


def levels(seeds: int) -> None:
    """Renta cobrada por nivel en una vivienda de cada tipo de barrio."""
    print(f"{'barrio':10} {'referencia':>11} {'mercado':>8} {'nómadas':>8}")
    seen: set[str] = set()
    for barrio in BARRIOS:
        if barrio.theme in seen:
            continue
        seen.add(barrio.theme)
        listing = next(
            i for i in LISTINGS if i.barrio == barrio.key and i.kind in housing.HOME_KINDS
        )
        row = [collected(listing, level, seeds) for level in ("referencia", "mercado", "nomadas")]
        print(f"{barrio.theme:10} {row[0]:>11.2f} {row[1]:>8.2f} {row[2]:>8.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--niveles", action="store_true")
    args = parser.parse_args()
    if args.niveles:
        levels(args.seeds)
        return
    rows = []
    for listing in LISTINGS:
        nets = [simulate(listing, seed) for seed in range(args.seeds)]
        net = statistics.mean(nets)
        cost = housing.purchase_quote(listing, housing.SHARES).total
        rows.append((housing.price(listing), listing.key, housing.reference_rent(listing), net,
                     cost / max(net, 1) / 30))  # fmt: skip
    print(f"{'inmueble':24} {'precio':>12} {'renta/mes':>10} {'neto/día':>9} {'se paga en':>11}")
    for price, key, rent, net, months in sorted(rows):
        print(f"{key:24} {price:>12,} {rent:>10,} {net:>9,.0f} {months:>8.1f} meses")
    cheap = sorted(rows)[:10]
    created = sum(rent for _p, _k, rent, _n, _m in cheap)
    print()
    print(f"Los 10 más baratos alquilados crean {created:,} Y$ al día de la nada;")
    every = sum(rent for _p, _k, rent, _n, _m in rows)
    print(f"los {len(rows)} del catálogo alquilados, {every:,} Y$ al día;")
    print(f"las nóminas de pala pagan unos {PALA_PER_DAY:,} Y$ brutos al día.")
    print()
    print("Hipoteca del 80 % al Euríbor de partida + 1 % y el neto de nómina que pide el banco:")
    rate = housing.EURIBOR_START + housing.MORTGAGE_SPREAD
    for price, key, _rent, _net, _months in sorted(rows):
        listing = next(item for item in LISTINGS if item.key == key)
        if listing.kind not in housing.HOME_KINDS:
            continue
        quote = housing.purchase_quote(listing, housing.SHARES)
        loan = round(quote.price * housing.MORTGAGE_LTV)
        payment = housing.installment(loan, rate)
        need = round(payment / housing.DTI_MAX)
        print(f"{key:24} {price:>12,} entrada {quote.total - loan:>10,}"
              f" cuota {payment:>7,} nómina {need:>7,}/día")  # fmt: skip
    print(
        f"VPO: hasta {housing.vpo_income_cap():,}/día de nómina; bono: hasta "
        f"{housing.bono_income_cap():,}/día."
    )


if __name__ == "__main__":
    main()
