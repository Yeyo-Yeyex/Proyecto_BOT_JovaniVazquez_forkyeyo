"""Ventaja de la casa del póker (Casino Hold'em), simulada en bloque con numpy.

Uso, desde la raíz del repo:

    python docs/rtp_poker.py --rapido    # ~3 s: 300.000 flops, ± 0,6 %
    python docs/rtp_poker.py             # ~30 s: 3 millones de flops, ± 0,2 %
    python docs/rtp_poker.py --aprender  # reaprende la estrategia (~100 s)

La primera vez, o si cambia una regla o un pago del bot, aprende antes la
estrategia (~100 s) y la guarda en la carpeta temporal; después solo mide.

Por qué no es exacta, como `docs/rtp_bacara.py`: decidir bien exige saber lo que
vale ir con cada flop, y eso son ~1 millón de finales (turn, river y las dos de
la banca) por cada uno de los ~26 millones de flops posibles. Se simula, con
cuidado de que la cifra no salga inflada:

1. **Evaluador en bloque** (`evaluate`): máscaras de 13 bits de las alturas y
   de cada palo, miradas en tablas precalculadas (`_tables`), ~0,5 µs por mano.
   Da una puntuación entera que ordena igual que `bot.services.poker.evaluate`, y
   `net` paga igual que `bot.services.poker.payout`: lo comprueba
   `tests/unit/test_rtp_poker.py`, así que si cambia una regla del bot y no la de
   aquí, la prueba avisa.
2. **Muchos finales por flop** (`finish`): cada flop se juega con `k` turn, river
   y banca distintos y cuenta su media. Quita el ruido del final; lo que queda es
   la diferencia entre flops, que solo baja con más flops.
3. **Estrategia aprendida y medida aparte.** Con 16 millones de flops se aprende
   cuándo ir: los flops se agrupan por lo que se ve al decidir (`features`) y se
   va donde ir pierde menos que el ante. La ventaja se mide con **otros** flops,
   así el ruido de aprender no se cuela. Esta estrategia es algo peor que la
   óptima, así que la ventaja real es la que sale o un poco menos.

Resultado (octubre de 2026): 2,6 % ± 0,2 % del ante yendo en un 82 % de las manos,
contra el 2,16 % publicado para estas reglas y esta tabla, que es el de la
estrategia óptima; la que se aprende aquí pierde unas décimas por decidir con
rasgos y no con las cinco cartas exactas. Ir siempre cuesta un 8 %. La docstring
de `bot.services.poker`, el README y FUNCIONALIDADES citan el 2,16 %: si cambia
una regla o un pago, se vuelve a pasar.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.services import poker  # noqa: E402

#: Alturas 0..12 (del 2 al as); una carta es `altura * 4 + palo`.
AR = np.arange(13)
BIT = (1 << AR).astype(np.int64)
#: Escaleras de 5 alturas seguidas, de la más alta (al as) a la rueda (al 5).
STRAIGHTS = [(h, 0b11111 << (h - 4)) for h in range(12, 3, -1)] + [(3, (1 << 12) | 0b1111)]
#: Lo que paga el ante por categoría (`Rank.order`), además de devolverse.
ANTE_PAYS = np.array([poker.ante_pays(rank) for rank in poker.Rank])
FOUR = 2  # la pareja de cuatros, en alturas 0..12
CHUNK = 250_000
#: Con cuánto se aprende la estrategia que se guarda en caché.
LEARN_FLOPS, LEARN_FINALES = 16_000_000, 2


def _tables() -> tuple[np.ndarray, ...]:
    """Tablas por máscara de 13 bits (una por conjunto de alturas): todo se mira, nada se cuenta.

    Devuelve los bits a 1, la altura más alta (-1 si vacía), la escalera más alta
    (-1 si no hay) y `KICK[k]`: las `k` alturas más altas en base 13, la mayor en
    la cifra más alta, con ceros si faltan.
    """
    masks = range(1 << 13)
    popcount = np.array([bin(m).count("1") for m in masks], dtype=np.int64)
    high = np.array([m.bit_length() - 1 for m in masks], dtype=np.int64)
    straight = np.full(1 << 13, -1, dtype=np.int64)
    for top, mask in reversed(STRAIGHTS):  # la más alta, la última en escribirse
        hit = (np.arange(1 << 13) & mask) == mask
        straight[hit] = top
    kick = np.zeros((6, 1 << 13), dtype=np.int64)
    for m in masks:
        rest, digits = m, []
        for _ in range(5):
            digits.append(rest.bit_length() - 1 if rest else 0)
            rest &= ~(1 << digits[-1]) if rest else 0
        for k in range(1, 6):
            kick[k, m] = sum(d * 13 ** (k - 1 - i) for i, d in enumerate(digits[:k]))
    return popcount, high, straight, kick


POPCOUNT, HIGH, STRAIGHT, KICK = _tables()


def evaluate(cards: np.ndarray) -> np.ndarray:
    """Puntuación de la mejor mano de cada fila de `cards` (N, k), con k de 3 a 7.

    Ordena igual que `poker.evaluate`: categoría (`Rank.order`) × 13^5 más las
    alturas que desempatan, de la que más pesa a la que menos. Todo son máscaras
    de 13 bits (qué alturas salen una, dos, tres o cuatro veces, y las de cada
    palo) que se miran en las tablas de `_tables`. Con menos de cinco cartas sirve
    para las características del flop (pareja, trío…), no para comparar.
    """
    n = len(cards)
    ranks, suits = cards // 4, cards % 4
    rows = np.arange(n)[:, None]
    counts = np.bincount((rows * 13 + ranks).ravel(), minlength=n * 13).reshape(n, 13)
    m1, m2, m3, m4 = ((counts >= k) @ BIT for k in (1, 2, 3, 4))
    per_suit = (
        np.bincount((rows * 4 + suits).ravel(), weights=BIT[ranks].ravel(), minlength=n * 4)
        .reshape(n, 4)
        .astype(np.int64)
    )
    flush_bits = np.where(POPCOUNT[per_suit] >= 5, per_suit, 0).max(1)
    flush = flush_bits > 0

    straight_flush = np.where(flush, STRAIGHT[flush_bits], -1)
    straight = STRAIGHT[m1]
    quad, trips = HIGH[m4], HIGH[m3]
    pair = HIGH[m2]
    full_pair = HIGH[m2 & ~np.where(trips >= 0, BIT[trips], 0)]
    second = HIGH[m2 & ~np.where(pair >= 0, BIT[pair], 0)]

    def drop(mask: np.ndarray, *hs: np.ndarray) -> np.ndarray:
        for h in hs:
            mask = mask & ~np.where(h >= 0, BIT[h], 0)
        return mask

    p4, p3 = 13**4, 13**3
    conds = [
        straight_flush >= 0,
        quad >= 0,
        (trips >= 0) & (full_pair >= 0),
        flush,
        straight >= 0,
        trips >= 0,
        second >= 0,
        pair >= 0,
    ]
    values = [
        np.where(straight_flush == 12, 9, 8) * 13**5 + straight_flush * p4,
        7 * 13**5 + quad * p4 + KICK[1][drop(m1, quad)] * p3,
        6 * 13**5 + trips * p4 + full_pair * p3,
        5 * 13**5 + KICK[5][flush_bits],
        4 * 13**5 + straight * p4,
        3 * 13**5 + trips * p4 + KICK[2][drop(m1, trips)] * 13**2,
        2 * 13**5 + pair * p4 + second * p3 + KICK[1][drop(m1, pair, second)] * 13**2,
        1 * 13**5 + pair * p4 + KICK[3][drop(m1, pair)] * 13,
    ]
    return np.select(conds, values, KICK[5][m1])


def category(score: np.ndarray) -> np.ndarray:
    """La categoría (`Rank.order`) de una puntuación de `evaluate`."""
    return score // 13**5


def net(player: np.ndarray, dealer: np.ndarray) -> np.ndarray:
    """Lo que gana o pierde yendo, en antes (ir cuesta `CALL_FACTOR`), como `poker.payout`."""
    cat_p, cat_d = category(player), category(dealer)
    top_d = (dealer % 13**5) // 13**4
    qualifies = (cat_d >= 2) | ((cat_d == 1) & (top_d >= FOUR))
    call = poker.CALL_FACTOR
    won = ANTE_PAYS[cat_p]
    return np.select(
        [~qualifies, player > dealer, player == dealer],
        [won, won + call, 0],
        -(1 + call),
    )


def FEATURES(flop_cat, hi, lo, flush_draw, overcards, windows, top, lo_over):  # noqa: ANN001, ANN201, N802
    """(tamaño, valor) de cada rasgo de la clave, después de la categoría."""
    return (
        (4, np.minimum(flop_cat, 3)), (13, hi), (13, lo), (3, flush_draw),
        (3, overcards), (3, np.minimum(windows, 2)), (13, top), (4, lo_over),
    )  # fmt: skip


#: Claves posibles: 10 categorías por el producto de los tamaños de `FEATURES`.
KEYS = 10 * 4 * 13 * 13 * 3 * 3 * 3 * 13 * 4


def features(known: np.ndarray) -> np.ndarray:
    """Clave entera de lo que se ve al decidir: tus dos cartas y el flop (N, 5)."""
    five = evaluate(known)
    flop_cat = category(evaluate(known[:, 2:]))  # 0, 1 (pareja) o 3 (trío) en la mesa
    hole = known[:, :2] // 4
    hi, lo = hole.max(1), hole.min(1)
    suits = known % 4
    # Color: 0 nada, 1 tres del mismo palo con alguna tuya (backdoor), 2 cuatro.
    suited = (suits[..., None] == np.arange(4)).sum(1).max(1)
    flush_draw = np.clip(suited - 2, 0, 2)
    overcards = (hole > (known[:, 2:] // 4).max(1)[:, None]).sum(1)
    lo_over = (lo[:, None] > known[:, 2:] // 4).sum(1)  # cartas de la mesa por debajo de tu baja
    bits = np.bitwise_or.reduce(BIT[known // 4], axis=1)
    windows = sum(
        (POPCOUNT[bits & mask] >= 4).astype(int) for _, mask in STRAIGHTS
    )  # 1, proyecto de escalera; 2 o más, de dos lados
    top = (five % 13**5) // 13**4
    key = category(five)
    for size, value in FEATURES(flop_cat, hi, lo, flush_draw, overcards, windows, top, lo_over):
        key = key * size + value
    return key


def distinct(rng: np.random.Generator, n: int, k: int, m: int) -> np.ndarray:
    """`n` filas de `k` índices distintos de `range(m)`, al azar y en orden al azar.

    Se sortean con repetición y se vuelven a sortear solo las filas que repiten
    (con 9 de 52, una de cada cinco): mucho más barato que barajar 52 por fila.
    """
    out = rng.integers(0, m, (n, k))
    while True:
        s = np.sort(out, axis=1)
        bad = (s[:, 1:] == s[:, :-1]).any(1)
        if not bad.any():
            return out
        out[bad] = rng.integers(0, m, (int(bad.sum()), k))


def deal(rng: np.random.Generator, n: int) -> np.ndarray:
    """`n` repartos de 9 cartas distintas: tus 2, las 2 de la banca y las 5 comunitarias."""
    return distinct(rng, n, 9, 52)


def flops(rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
    """`n` situaciones de decidir (tus 2 y el flop) y las 47 cartas que quedan en cada una."""
    known = distinct(rng, n, 5, 52)
    seen = np.zeros((n, 52), dtype=bool)
    np.put_along_axis(seen, known, True, axis=1)
    rest = np.argsort(seen, axis=1, kind="stable")[:, :47]
    return known, rest


def finish(rng: np.random.Generator, known: np.ndarray, rest: np.ndarray, k: int) -> np.ndarray:
    """Lo que daría ir con cada flop, de media en `k` finales (turn, river y la banca)."""
    gain = np.zeros(len(known))
    for _ in range(k):
        four = np.take_along_axis(rest, distinct(rng, len(known), 4, 47), axis=1)
        player = np.concatenate([known, four[:, 2:]], axis=1)
        dealer = np.concatenate([four, known[:, 2:]], axis=1)
        gain += net(evaluate(player), evaluate(dealer))
    return gain / k


def audit_strategy(known: np.ndarray) -> np.ndarray:
    """Cuándo va el jugador de `docs/auditoria_logros.py` (sin su azar: con nada, no va)."""
    made = category(evaluate(known)) > 0
    high = (known[:, :2] // 4 >= 11).any(1)
    return made | high


def learn(n_flops: int, k: int, seed: int) -> np.ndarray:
    """Cuándo ir, por clave de `features`: `True` si ir pierde de media menos que el ante.

    Con `n_flops` flops y `k` finales cada uno. Una clave que no salió, se va.
    """
    rng = np.random.default_rng(seed)
    total = np.zeros(KEYS)
    count = np.zeros_like(total)
    for start in range(0, n_flops, CHUNK):
        known, rest = flops(rng, min(CHUNK, n_flops - start))
        key = features(known)
        total += np.bincount(key, finish(rng, known, rest, k), minlength=KEYS)
        count += np.bincount(key, minlength=KEYS)
    return np.where(count > 0, total > -count, True)


def rules_hash() -> str:
    """Huella de lo que cambia la estrategia: reglas y pagos del bot y los rasgos de aquí."""
    parts = (
        ANTE_PAYS.tobytes(), poker.CALL_FACTOR, poker.QUALIFY_PAIR,
        inspect.getsource(features), inspect.getsource(FEATURES), LEARN_FLOPS, LEARN_FINALES,
    )  # fmt: skip
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


def cached_strategy(*, fresh: bool = False) -> tuple[np.ndarray, bool]:
    """La estrategia aprendida, de la caché si las reglas no han cambiado.

    Aprender cuesta lo mismo que medir, y para medir rápido hace falta haber
    aprendido con muchos flops (con pocos, la estrategia se sobreajusta y la
    ventaja sale inflada), así que se guarda en la carpeta temporal del sistema
    con la huella de `rules_hash`: si cambia un pago o una regla, se reaprende.

    Returns:
        La tabla de `learn` y si salió de la caché.
    """
    path = Path(tempfile.gettempdir()) / f"rtp_poker_{rules_hash()}.npy"
    if path.exists() and not fresh:
        return np.unpackbits(np.load(path))[:KEYS].astype(bool), True
    go = learn(LEARN_FLOPS, LEARN_FINALES, seed=12345)
    np.save(path, np.packbits(go))
    return go, False


def measure(go: np.ndarray, n_flops: int, k: int, seed: int) -> dict[str, float]:
    """Ventaja con la estrategia `go`, yendo siempre y como la auditoría, en `n_flops` flops nuevos.

    Cada flop se juega con `k` finales y cuenta su media: así se quita casi todo
    el ruido del turn, el river y la banca. Lo que queda es la diferencia entre
    flops, que solo baja con más flops.
    """
    rng = np.random.default_rng(seed)
    sums = {name: [0.0, 0.0] for name in ("learned", "always", "audit", "calls", "saving")}
    for start in range(0, n_flops, CHUNK):
        known, rest = flops(rng, min(CHUNK, n_flops - start))
        key = features(known)
        gain = finish(rng, known, rest, k)
        for name, result in (
            ("learned", np.where(go[key], gain, -1.0)),
            ("always", gain),
            ("audit", np.where(audit_strategy(known), gain, -1.0)),
            ("calls", go[key].astype(float)),
            # Lo que gana retirarse donde toca frente a ir siempre: misma mano, poco ruido.
            ("saving", np.where(go[key], 0.0, -1.0 - gain)),
        ):
            sums[name][0] += result.sum()
            sums[name][1] += (result**2).sum()
    out: dict[str, float] = {"flops": n_flops}
    for name, (s, s2) in sums.items():
        mean = s / n_flops
        out[name] = mean
        out[f"{name}_se"] = (max(s2 / n_flops - mean**2, 0) / n_flops) ** 0.5
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--flops", type=int, default=3_000_000)
    parser.add_argument("--finales", type=int, default=4, help="finales por flop")
    parser.add_argument("--rapido", action="store_true", help="300.000 flops, para iterar")
    parser.add_argument("--aprender", action="store_true", help="reaprende aunque haya caché")
    parser.add_argument("--semilla", type=int, default=1)
    args = parser.parse_args()
    n = 300_000 if args.rapido else args.flops
    began = time.perf_counter()
    go, cached = cached_strategy(fresh=args.aprender)
    learned_in = time.perf_counter() - began
    r = measure(go, n, args.finales, args.semilla)
    took = time.perf_counter() - began - learned_in
    where = "de la caché" if cached else f"aprendida en {learned_in:.0f} s y guardada"
    print(f"Estrategia {where}.")
    hands = f"{n * args.finales:,}".replace(",", ".")
    print(f"{n:,} flops × {args.finales} finales ({hands} manos) en {took:.1f} s".replace(",", "."))
    for name, label in (
        ("learned", "estrategia aprendida"),
        ("audit", "jugador de la auditoría"),
        ("always", "ir siempre"),
    ):
        edge, se = -r[name] * 100, r[f"{name}_se"] * 100
        print(f"  {label:<24} ventaja {edge:5.2f} % ± {1.96 * se:.2f} % del ante")
    saving, se = r["saving"] * 100, r["saving_se"] * 100
    print(f"  retirarse donde toca ahorra {saving:.2f} % ± {1.96 * se:.2f} % frente a ir siempre")
    print(f"  va con la aprendida en un {r['calls'] * 100:.1f} % de las manos (publicado: 82 %)")
    print("  publicado para estas reglas: 2,16 %")


if __name__ == "__main__":
    main()
