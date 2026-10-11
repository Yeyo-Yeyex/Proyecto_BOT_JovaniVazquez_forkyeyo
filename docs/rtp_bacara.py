"""RTP exacto del bacará con 8 barajas, calculado con las reglas reales del bot.

Uso, desde la raíz del repo:

    python docs/rtp_bacara.py           # exacto, unos segundos
    python docs/rtp_bacara.py --manos 200000   # además, un zapato simulado con corte

No simula: recorre todas las formas posibles de repartir una mano desde un
zapato lleno de 8 barajas (416 cartas, sin reposición) y pesa cada una por su
probabilidad. Solo importan los puntos de cada carta (0 a 9), así que son 10^4 a
10^6 caminos. Las decisiones salen de `bot.services.bacara` (`player_draws`,
`banker_draws` y `payout`), de modo que si cambia una regla o un pago, la
cifra cambia con ella.

Salida: probabilidad de cada resultado, frecuencia de naturales y de manos de 6
cartas, y el RTP de cada apuesta con 100, 20 y 30 Y$ (la comisión de la Banca
se redondea al Y$ más cercano, así que con fichas que no son múltiplo de 20 la
casa gana algo más; 20 Y$ es el mínimo a Banca). Con `--manos`, también lo que
se ve jugando zapatos de verdad, con su quema y su carta de corte (`Shoe`),
para comprobar que el corte no cambia nada.

Las cifras que citan la docstring de `bot.services.bacara`, el README y
FUNCIONALIDADES salen de aquí: al cambiar una regla, se vuelve a pasar.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.services import bacara as bc  # noqa: E402
from bot.services.blackjack import Card  # noqa: E402

#: Cartas de cada valor en un zapato lleno: el 0 junta 10, J, Q y K.
FULL = tuple((16 if v == 0 else 4) * bc.DECKS for v in range(10))
#: Una carta de muestra de cada valor, para preguntar a las reglas del bot.
SAMPLE = tuple(Card(10 if v == 0 else v, 0) for v in range(10))


def exact() -> dict[str, float]:
    """Probabilidades exactas de cada desenlace con un zapato lleno."""
    counts = list(FULL)
    total_cards = sum(counts)
    out: Counter[str] = Counter()

    def take(prob: float, left: int, fn) -> None:  # noqa: ANN001
        for v in range(10):
            n = counts[v]
            if not n:
                continue
            counts[v] -= 1
            fn(prob * n / left, left - 1, v)
            counts[v] += 1

    def finish(prob: float, player: list[int], banker: list[int]) -> None:
        p, b = sum(player) % 10, sum(banker) % 10
        winner = "tie" if p == b else ("player" if p > b else "banker")
        out[winner] += prob
        out["cards6"] += prob if len(player) + len(banker) == 6 else 0.0
        out["pair_p"] += prob if player[0] == player[1] else 0.0

    def after_four(prob: float, left: int, p1: int, b1: int, p2: int, b2: int) -> None:
        player, banker = [p1, p2], [b1, b2]
        pt, bt = (p1 + p2) % 10, (b1 + b2) % 10
        if pt >= 8 or bt >= 8:
            out["natural"] += prob
            finish(prob, player, banker)
            return
        if bc.player_draws(pt):

            def third(pr: float, lf: int, v: int) -> None:
                if bc.banker_draws(bt, SAMPLE[v]):
                    take(pr, lf, lambda q, _l, w: finish(q, [*player, v], [*banker, w]))
                else:
                    finish(pr, [*player, v], banker)

            take(prob, left, third)
        elif bc.banker_draws(bt, None):
            take(prob, left, lambda q, _l, w: finish(q, player, [*banker, w]))
        else:
            finish(prob, player, banker)

    take(
        1.0,
        total_cards,
        lambda a, la, p1: take(
            a,
            la,
            lambda b, lb, b1: take(
                b,
                lb,
                lambda c, lc, p2: take(c, lc, lambda d, ld, b2: after_four(d, ld, p1, b1, p2, b2)),
            ),
        ),
    )
    return dict(out)


def rtp(probs: dict[str, float], side: bc.Side, stake: int) -> float:
    """Lo que devuelve de media cada Y$ apostado a `side` con fichas de `stake`."""
    back = sum(
        probs[key] * bc.payout(side, stake, winner)
        for key, winner in (
            ("player", bc.Side.PLAYER),
            ("banker", bc.Side.BANKER),
            ("tie", bc.Side.TIE),
        )
    )
    return back / stake


def simulate(hands: int) -> Counter[str]:
    """Resultados de `hands` manos jugando zapatos enteros con quema y corte."""
    seen: Counter[str] = Counter()
    shoe = bc.Shoe.open()
    for _ in range(hands):
        if shoe.finished:
            shoe = bc.Shoe.open()
        seen[shoe.deal().winner.key] += 1
    return seen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manos", type=int, default=0, help="manos simuladas con zapato real")
    args = parser.parse_args()
    probs = exact()
    print("Bacará, 8 barajas, cálculo exacto con zapato lleno\n")
    for key, label in (("banker", "Banca"), ("player", "Jugador"), ("tie", "Empate")):
        print(f"  Gana {label:<8} {probs[key]:.6%}")
    print(f"  Natural        {probs['natural']:.4%}")
    print(f"  Mano de 6      {probs['cards6']:.4%}")
    print(f"  Pareja Jugador {probs['pair_p']:.4%}\n")
    print("  RTP por apuesta (ventaja de la casa entre paréntesis):")
    for side in bc.Side:
        line = "  ".join(
            f"{stake:>3} Y$: {rtp(probs, side, stake):.4%} ({1 - rtp(probs, side, stake):.2%})"
            for stake in (100, 20, 30)
        )
        print(f"    {side.label:<8} {line}")
    if args.manos:
        seen = simulate(args.manos)
        print(f"\n  Simulado con zapatos reales ({args.manos:,} manos):".replace(",", "."))
        for side in bc.Side:
            print(f"    {side.label:<8} {seen[side.key] / args.manos:.4%}")


if __name__ == "__main__":
    main()
