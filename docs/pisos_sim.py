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
    python docs/pisos_sim.py --duraciones # cada duración de contrato y comprar las llaves

Con `--duraciones` (9.23.0, `docs/pisos-llaves.md`) dice cuánto se cobra con cada duración de
contrato si el dueño no hace nada, si no renueva cuando el barrio ha subido o si compra las
llaves, con el índice quieto, subiendo o en olas; y cuánto gana o pierde comprar las llaves
según lo que haya subido el barrio.

En la consola de Windows hace falta `PYTHONIOENCODING=utf-8`.
"""

from __future__ import annotations

import argparse
import math
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


#: Cómo se mueve el índice del barrio en `--duraciones`: quieto, subiendo un 40 % en un año
#: (360 días) y en olas de ±25 % cada 120 días.
PATHS = {
    "quieto": lambda d: 1.0,
    "sube": lambda d: 1 + 0.4 * min(d, 360) / 360,
    "olas": lambda d: 1 + 0.25 * math.sin(2 * math.pi * d / 120),
}


def term_run(
    listing: Listing, term: int, strategy: str, path: str, seed: int, days: int = 360,
    rise: float = 0.15,
) -> float:  # fmt: skip
    """Renta cobrada al día (menos abogados y llaves) entre la de referencia de hoy, con la
    duración `term` y una estrategia del dueño:

    - `pasivo`: no hace nada (renueva quien quiere).
    - `no_renueva`: si el índice ha subido un `rise` desde que firmó, avisa de que no
      renueva (para volver a alquilar con la renta de ese día).
    - `llaves`: si el índice ha subido el doble que eso, le compra las llaves con la oferta
      generosa (sin fama: un solo piso).
    """
    rng = random.Random(seed)
    start = date(2026, 1, 1)
    lease = housing.Lease(level="referencia", term=term, since=start)
    signed_at = 1.0
    total = 0.0
    for offset in range(days):
        day = start + timedelta(days=offset)
        index = PATHS[path](offset)
        out = housing.advance_day(listing, lease, day, index, rng)
        lease = out.lease
        if any(e.key == "firma" for e in out.events):
            signed_at = index
        evicting = lease.status == housing.EVICTING
        lease = _evict(lease, day, rng)
        if lease.status == housing.EVICTING and not evicting:
            fee = housing.eviction_fee(lease.rent or housing.reference_rent(listing, index))
            total -= (fee + round(fee * housing.SERVICES_IGIC)) / housing.reference_rent(listing)
        total += out.rent / housing.reference_rent(listing, index)
        if lease.status != housing.RENTED or lease.leaving or lease.tenant is None:
            continue
        if strategy == "no_renueva" and index >= signed_at * (1 + rise):
            lease = replace(lease, leaving=housing.LEAVE_NO_RENEW)
        elif strategy == "llaves" and index >= signed_at * (1 + 2 * rise):
            if lease.keys_wait is not None and day < lease.keys_wait:
                continue
            quote = housing.keys_quote(listing, lease, day, 0, f"{seed}:{offset}", index)
            answer = housing.keys_answer(housing.KEYS_RICH, quote.fair, quote.reserve,
                                         quote.free, quote.risk, rng)  # fmt: skip
            if answer.accepted:
                total -= answer.paid / housing.reference_rent(listing, index)
                lease = housing.leaves_in(lease, day, housing.LEAVE_KEYS, housing.KEYS_MOVE_DAYS)
            else:
                lease = housing.keys_refused(lease, day, answer)
    return total / days


def terms(seeds: int) -> None:
    """Cada duración con cada estrategia y cada índice, en un piso de referencia normal."""
    listing = next(i for i in LISTINGS if i.kind == "piso2")
    strategies = ("pasivo", "no_renueva", "llaves")
    for path in PATHS:
        print(f"Índice {path} (renta cobrada al día / renta de referencia de ese día):")
        print(f"{'duración':>9} " + " ".join(f"{s:>11}" for s in strategies))
        for term in housing.TERMS:
            row = [
                statistics.mean(term_run(listing, term, s, path, seed) for seed in range(seeds))
                for s in strategies
            ]
            print(f"{term:>6} d. " + " ".join(f"{v:>11.3f}" for v in row))
        print()
    print("Comprar las llaves cuando el índice ha subido (48 días de contrato por delante):")
    print(f"{'sube':>6} {'justa':>7} {'generosa':>9} {'ganas':>7} {'neto justa':>11} "
          f"{'neto gen.':>10}  (en meses de la renta vieja)")  # fmt: skip
    rent = housing.reference_rent(listing)
    for jump in (0.1, 0.15, 0.2, 0.4):
        fair = rich = gained = 0.0
        n = seeds * 50
        for seed in range(n):
            rng = random.Random(seed)
            day = date(2026, 1, 1)
            old = housing.Lease(
                status=housing.RENTED, level="referencia", tenant="enfermeros", rent=rent,
                until=day + timedelta(days=48), since=day, signed=day,
            )  # fmt: skip
            quote = housing.keys_quote(listing, old, day, 0, str(seed), 1 + jump)
            fair += quote.fair / rent
            rich += housing.keys_offer_amount(housing.KEYS_RICH, quote.fair) / rent
            # Lo que gana es la renta nueva menos la vieja hasta que acabe, menos los días
            # vacío buscando a otro (con la probabilidad real de encontrarlo).
            new_rent = housing.reference_rent(listing, 1 + jump)
            find = housing.find_chance(listing, housing.RENT_LEVELS["referencia"], day)
            empty = 0
            while rng.random() >= find:
                empty += 1
            months = 48 - housing.KEYS_MOVE_DAYS - empty
            gained += (new_rent * months - rent * (48 - housing.KEYS_MOVE_DAYS)) / rent
        print(f"{jump:>5.0%} {fair / n:>7.1f} {rich / n:>9.1f} {gained / n:>7.1f} "
              f"{(gained - fair) / n:>11.1f} {(gained - rich) / n:>10.1f}")  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--niveles", action="store_true")
    parser.add_argument("--duraciones", action="store_true")
    args = parser.parse_args()
    if args.niveles:
        levels(args.seeds)
        return
    if args.duraciones:
        terms(args.seeds)
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
