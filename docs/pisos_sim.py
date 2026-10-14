"""Calibra `pisos`: cuánto renta cada inmueble y en cuánto se paga solo, con el código real.

Simula cada inmueble del catálogo durante `DAYS` días (meses del juego) con
`housing.advance_day` y `housing.settle_day`, el nivel de renta de referencia y un
dueño que solo tiene ese piso, y saca la renta neta media al día (después de vacíos,
imprevistos, IBI, comunidad, seguro e IRPF) y los días que tarda en pagar lo que costó
(precio, ITP y notaría). El IRPF se calcula con `taxes.compute_withholding` y la renta
pasada a su escala con `housing.rent_to_fiscal`, como en el bot.

Además dice cuánto dinero se crea de la nada si todo el servidor tiene alquilados los
inmuebles más baratos, para compararlo con lo que pagan las nóminas de `pala` (Biblia,
«Vigilar el dinero que se crea de la nada»).

Uso (desde la raíz del repo):

    python docs/pisos_sim.py              # todos los inmuebles, 3 semillas
    python docs/pisos_sim.py --seeds 10   # más semillas, cifras más estables

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
from bot.services.housing_catalog import LISTINGS, Listing  # noqa: E402
from bot.services.taxes import compute_withholding  # noqa: E402

#: Días simulados (meses del juego): dos años y medio de alquiler.
DAYS = 30
#: Nóminas de `pala` que se pagaron al día en el servidor (informe del 14/10/2026:
#: 765.138 Y$ brutos en 30 días).
PALA_PER_DAY = 765_138 // 30


def simulate(listing: Listing, seed: int) -> float:
    """Neto medio al día de un inmueble entero de un dueño que solo tiene eso."""
    rng = random.Random(seed)
    start = date(2026, 10, 1)
    lease = housing.Lease(since=start)
    paid = housing.price(listing)
    total = 0
    week_fiscal: list[int] = []
    for offset in range(DAYS):
        day = start + timedelta(days=offset)
        out = housing.advance_day(listing, lease, day, 1.0, rng)
        payout = housing.settle_day(out.rent, out.costs, out.extra, lease.arrears,
                                    lease.arrears_ibi)  # fmt: skip
        lease = replace(out.lease, arrears=payout.arrears, arrears_ibi=payout.arrears_ibi)
        if lease.status == housing.DEFAULTING and lease.unpaid >= 3:
            lease = housing.start_eviction(lease, day, rng)
        gross = payout.net + payout.ibi + payout.costs
        taxable = housing.taxable_rent(
            gross, payout.ibi + payout.costs + housing.amortization(paid)
        )
        fiscal = housing.rent_to_fiscal(taxable)
        recent = sum(week_fiscal[-6:])
        rate = compute_withholding(fiscal, recent).rate if fiscal else 0.0
        week_fiscal.append(fiscal)
        total += payout.net - round(taxable * rate)
    return total / DAYS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
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
    print(f"las nóminas de pala pagan unos {PALA_PER_DAY:,} Y$ brutos al día.")


if __name__ == "__main__":
    main()
