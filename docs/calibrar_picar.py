"""Calibra el juego del pico (`picar`): valores de los minerales con RTP exacto.

Uso:

    python docs/calibrar_picar.py --semillas 1000000 --tabla ../picar-tabla
    python docs/calibrar_picar.py --semillas 1000000 --tabla ../picar-tabla --reusar

El juego elige cada tirada de una biblioteca fija de semillas (de 0 a N - 1), así que
el RTP no se estima: se cuenta. Pasos:

1. **Simular la biblioteca.** Cada semilla con cada pico, sin fotogramas, en todos los
   núcleos. De cada partida se guarda cuántos minerales de cada tipo rompe, el producto
   de sus bloques de multiplicador, si llega al dragón, la fila más honda y cuántos
   ticks dura. La tabla va a `--tabla` (fuera del repo, ~30 MB por pico con un
   millón): con `--reusar` se usa la que haya si se hizo con los mismos parámetros, para
   cambiar valores, costes o topes sin volver a simular.
2. **Buscar `k`.** Lo que suma cada mineral (en diezmilésimas del coste, `picar.SCALE`)
   es `BASE_VALUES × bonus × k`, redondeado (1 como poco). El RTP crece con `k`, así que
   se busca por bisección el mayor `k` cuyo RTP, contado exacto con los premios
   redondeados a Y$, no pasa del 99,5 %. Después se afina: se sube de uno en uno el
   mineral que más acerca al 99,5 % sin pasarse, hasta que ninguno cabe. El dragón paga
   el tope, y cualquier otro premio se queda en él como mucho.
3. **Huella.** Las primeras semillas de cada pico, jugadas otra vez, resumidas en un hash
   (`picar.fingerprint`). El bot lo comprueba al arrancar.
4. **Escribir** `src/bot/assets/picar/calibracion.json` y el informe
   `docs/picar-calibracion.md`.

Si el dragón solo ya pasa del 99,5 % (sale demasiado para su tope), no hay `k` posible:
el script lo dice y no escribe nada. Hay que bajar el tope o hacer el End más duro.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from fractions import Fraction
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bot.services.picar import (  # noqa: E402
    BASE_VALUES,
    CALIBRATION,
    OFFERS,
    SCALE,
    TARGET_RTP,
    fingerprint,
)
from bot.services.picar_physics import PICKS, TICKS_PER_SECOND, params_hash, simulate  # noqa: E402
from bot.services.picar_world import LAYERS, MINERALS  # noqa: E402

REPORT = Path(__file__).resolve().parent / "picar-calibracion.md"
CHUNK = 2_000
NETHER_ROW = next(layer.top for layer in LAYERS if layer.key == "nether")
END_ROW = next(layer.top for layer in LAYERS if layer.key == "end")


Chunk = tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]


def _chunk(args: tuple[str, int, int]) -> Chunk:
    key, start, stop = args
    n = stop - start
    counts = np.zeros((n, len(MINERALS)), dtype=np.uint16)
    dragon = np.zeros(n, dtype=bool)
    rows = np.zeros(n, dtype=np.uint16)
    ticks = np.zeros(n, dtype=np.uint32)
    mult = np.zeros(n, dtype=np.int64)
    for i, seed in enumerate(range(start, stop)):
        run = simulate(seed, key, record=False)
        for j, mineral in enumerate(MINERALS):
            counts[i, j] = run.broken.get(mineral, 0)
        dragon[i], rows[i], ticks[i] = run.dragon, run.max_row, run.ticks
        mult[i] = run.multiplier
    return start, counts, dragon, rows, ticks, mult


def library(key: str, seeds: int, table: Path, reuse: bool, procs: int) -> dict[str, np.ndarray]:
    """La tabla de la biblioteca de un pico (la simula o la lee de `table`)."""
    path = table / f"{key}.npz"
    if reuse and path.exists():
        data = dict(np.load(path))
        if str(data["params"]) == params_hash() and len(data["dragon"]) == seeds:
            print(f"  {key}: tabla reutilizada", flush=True)
            return data
        print(f"  {key}: la tabla guardada no vale (otros parámetros o semillas)", flush=True)
    counts = np.zeros((seeds, len(MINERALS)), dtype=np.uint16)
    dragon = np.zeros(seeds, dtype=bool)
    rows = np.zeros(seeds, dtype=np.uint16)
    ticks = np.zeros(seeds, dtype=np.uint32)
    mult = np.zeros(seeds, dtype=np.int64)
    jobs = [(key, s, min(seeds, s + CHUNK)) for s in range(0, seeds, CHUNK)]
    start = time.perf_counter()
    done = 0
    with Pool(procs) as pool:
        for first, c, d, r, t, x in pool.imap_unordered(_chunk, jobs):
            n = len(d)
            counts[first : first + n], dragon[first : first + n] = c, d
            rows[first : first + n], ticks[first : first + n] = r, t
            mult[first : first + n] = x
            done += n
            if done % (CHUNK * 50) == 0 or done == seeds:
                rate = done / (time.perf_counter() - start)
                print(f"  {key}: {done:,}/{seeds:,} ({rate:,.0f} por segundo)", flush=True)
    data = {
        "counts": counts,
        "dragon": dragon,
        "rows": rows,
        "ticks": ticks,
        "mult": mult,
        "params": np.array(params_hash()),
    }
    table.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **data)
    return data


def values_for(key: str, k: float) -> dict[str, int]:
    bonus = OFFERS[key].bonus
    return {m: max(1, round(BASE_VALUES[m] * bonus * k)) for m in MINERALS}


def payouts(key: str, data: dict[str, np.ndarray], values: dict[str, int]) -> np.ndarray:
    offer = OFFERS[key]
    vector = np.array([values[m] for m in MINERALS], dtype=np.int64)
    units = (data["counts"].astype(np.int64) @ vector) * data["mult"]
    top = offer.cost * offer.top
    pay = np.minimum(top, offer.cost * units // SCALE)
    return np.where(data["dragon"], top, pay)


def rtp_of(key: str, data: dict[str, np.ndarray], values: dict[str, int]) -> Fraction:
    total = int(payouts(key, data, values).sum())
    return Fraction(total, len(data["dragon"]) * OFFERS[key].cost)


def solve(key: str, data: dict[str, np.ndarray]) -> tuple[dict[str, int], Fraction]:
    """Lo que suma cada mineral, con el mayor RTP que no pasa del objetivo."""
    offer = OFFERS[key]
    floor = Fraction(int(data["dragon"].sum()) * offer.top, len(data["dragon"]))
    if floor > TARGET_RTP:
        raise SystemExit(
            f"{offer.name}: solo el dragón ya devuelve el {float(floor):.2%}; "
            "baja el tope o endurece el End."
        )
    lo, hi = 0.0, 1.0
    while rtp_of(key, data, values_for(key, hi)) <= TARGET_RTP:
        lo, hi = hi, hi * 2
    for _ in range(60):
        mid = (lo + hi) / 2
        if rtp_of(key, data, values_for(key, mid)) <= TARGET_RTP:
            lo = mid
        else:
            hi = mid
    values = values_for(key, lo)
    rtp = rtp_of(key, data, values)
    # Afinado: un mineral más de cada vez, el que más se acerca sin pasarse.
    while True:
        best: tuple[Fraction, str] | None = None
        for mineral in MINERALS:
            trial = {**values, mineral: values[mineral] + 1}
            value = rtp_of(key, data, trial)
            if rtp < value <= TARGET_RTP and (best is None or value > best[0]):
                best = (value, mineral)
        if best is None:
            return values, rtp
        rtp, mineral = best
        values[mineral] += 1


def stats(key: str, data: dict[str, np.ndarray], values: dict[str, int]) -> dict[str, float]:
    offer = OFFERS[key]
    pay = payouts(key, data, values)
    mult = pay / offer.cost
    n = len(pay)
    dragon = data["dragon"]
    rows = data["rows"]
    pct = np.percentile(mult, [10, 50, 90, 99, 99.9])
    return {
        "dragon": float(dragon.mean()),
        "dragon_rtp": float(dragon.sum() * offer.top / n),
        "nether": float((rows >= NETHER_ROW).mean()),
        "end": float((rows >= END_ROW).mean()),
        "win": float((pay > offer.cost).mean()),
        "x10": float((pay >= 10 * offer.cost).mean()),
        "zero": float((pay == 0).mean()),
        "p10": float(pct[0]),
        "p50": float(pct[1]),
        "p90": float(pct[2]),
        "p99": float(pct[3]),
        "p999": float(pct[4]),
        "max": float(mult.max()),
        "rows": float(rows.mean()),
        "seconds": float(data["ticks"].mean() / TICKS_PER_SECOND),
        "seconds_p90": float(np.percentile(data["ticks"], 90) / TICKS_PER_SECOND),
    }


def report(result: dict[str, dict], seeds: int, elapsed: float) -> str:
    lines = [
        "# Calibración del juego del pico (`picar`)",
        "",
        f"Sacada con `docs/calibrar_picar.py` el {date.today():%d/%m/%Y}, con una biblioteca "
        f"de {seeds:,} semillas por pico ({elapsed / 60:.0f} min). El RTP es exacto: es la "
        "media de los premios de todas las semillas de la biblioteca, que es de donde sale "
        "cada tirada. Los multiplicadores son el premio entre el coste, ya redondeado a Y$.",
        "",
        "| Pico | Coste | Tope | RTP | Gana algo | ×10 o más | Nada "
        "| p50 | p90 | p99 | p99,9 | Máx. |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, item in result.items():
        s, offer = item["stats"], OFFERS[key]
        lines.append(
            f"| {offer.name} | {offer.cost:,} | ×{offer.top:,} | {float(item['rtp']):.4%} | "
            f"{s['win']:.1%} | {s['x10']:.2%} | {s['zero']:.1%} | ×{s['p50']:.2f} | "
            f"×{s['p90']:.2f} | ×{s['p99']:.1f} | ×{s['p999']:.1f} | ×{s['max']:.0f} |"
        )
    lines += [
        "",
        "| Pico | Fila media | Nether | End | Dragón | Del RTP, el dragón | Duración media | p90 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key, item in result.items():
        s = item["stats"]
        dragon = f"1 de cada {1 / s['dragon']:,.0f}" if s["dragon"] else "nunca"
        lines.append(
            f"| {OFFERS[key].name} | {s['rows']:.0f} | {s['nether']:.2%} | {s['end']:.3%} | "
            f"{dragon} | {s['dragon_rtp']:.2%} | {s['seconds']:.0f} s | {s['seconds_p90']:.0f} s |"
        )
    lines += ["", "Lo que suma cada mineral, en diezmilésimas del coste (10.000 es ×1):", ""]
    lines.append("| Pico | " + " | ".join(MINERALS) + " |")
    lines.append("|---|" + "---|" * len(MINERALS))
    for key, item in result.items():
        lines.append(
            f"| {OFFERS[key].name} | " + " | ".join(str(item["values"][m]) for m in MINERALS) + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--semillas", type=int, default=1_000_000)
    parser.add_argument("--tabla", type=Path, required=True)
    parser.add_argument("--reusar", action="store_true")
    parser.add_argument("--procesos", type=int, default=os.cpu_count() or 1)
    args = parser.parse_args()

    start = time.perf_counter()
    result: dict[str, dict] = {}
    for key in OFFERS:
        print(f"{OFFERS[key].name}:", flush=True)
        data = library(key, args.semillas, args.tabla, args.reusar, args.procesos)
        values, rtp = solve(key, data)
        result[key] = {"values": values, "rtp": rtp, "stats": stats(key, data, values)}
        print(f"  RTP {float(rtp):.5%}  valores {values}", flush=True)
    elapsed = time.perf_counter() - start
    payload = {
        "library": args.semillas,
        "params": params_hash(),
        "fingerprint": fingerprint(),
        "date": f"{date.today():%Y-%m-%d}",
        "picks": {
            key: {
                "cost": OFFERS[key].cost,
                "top": OFFERS[key].top,
                "values": item["values"],
                "rtp": f"{item['rtp'].numerator}/{item['rtp'].denominator}",
                "stats": {k: round(v, 6) for k, v in item["stats"].items()},
            }
            for key, item in result.items()
        },
    }
    CALIBRATION.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    REPORT.write_text(report(result, args.semillas, elapsed), encoding="utf-8")
    print(f"Escrito {CALIBRATION} y {REPORT}")


if __name__ == "__main__":
    assert PICKS.keys() == OFFERS.keys()
    main()
