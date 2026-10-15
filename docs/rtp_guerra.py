"""RTP de la Guerra (Casino War): cálculo exacto y simulación con el código real del bot.

Uso, desde la raíz del repo:

    python docs/rtp_guerra.py                  # exacto + 2 millones de manos de cada estrategia
    python docs/rtp_guerra.py --manos 400000   # simulación más rápida, menos precisa

Mide tres cosas, siempre como lo devuelto por cada unidad de la ficha inicial
(la «ventaja de la casa» de las mesas es 1 menos esto):

1. **Ir siempre a la guerra** cuando se empata.
2. **Rendirse siempre** cuando se empata (recupera la mitad).
3. **La lateral de Empate 10:1**, por cada unidad apostada en ella.

El cálculo exacto usa `Fraction` y la composición del zapato de 6 barajas. Las
cartas de la guerra son dos al azar de lo que queda tras las dos que empataron
(quemar tres cartas no cambia nada por simetría), así que no hace falta simular.
La simulación juega `bot.services.war.WarGame` tal cual, con el zapato del
blackjack, y comprueba que las reglas del código dan lo mismo que la cuenta. Solo
baraja las últimas cartas del zapato (las únicas que se reparten), que es igual
de aleatorio y mucho más rápido.

Es lo que cita la docstring de `bot.services.war`: al cambiar una regla o un
pago, se vuelve a pasar y se actualizan las cifras.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.services import blackjack as bj  # noqa: E402
from bot.services import war  # noqa: E402

PER_RANK = 4 * bj.DECKS
CARDS = 13 * PER_RANK
TOP = war.BURN_CARDS + 4


def exact() -> dict[str, Fraction]:
    """Retornos exactos por unidad de ficha: guerra, rendición y lateral."""
    tie = Fraction(PER_RANK - 1, CARDS - 1)
    # Tras empatar con una carta de un rango quedan CARDS - 2 cartas: PER_RANK - 2 de ese
    # rango y PER_RANK de cada uno de los otros 12.
    left = CARDS - 2
    same = Fraction(PER_RANK - 2, left) * Fraction(PER_RANK - 3, left - 1)
    other = Fraction(PER_RANK, left) * Fraction(PER_RANK - 1, left - 1)
    war_tie = same + 12 * other
    war_win = (1 - war_tie) / 2
    war_lose = war_win
    # Neto de la mano al ir a la guerra: gana +1, empata +2 (guerra y premio), pierde −2.
    war_net = war_win * 1 + war_tie * (1 + war.WAR_TIE_BONUS) - war_lose * 2
    # Pagan igual la mano sin empate (gana +1, pierde −1, simétrico) y el empate de arriba.
    to_war = 1 + tie * war_net
    surrender = 1 - tie * Fraction(1, 2)
    side = tie * (1 + war.TIE_PAYS)
    return {
        "empate": tie,
        "empate_en_la_guerra": war_tie,
        "guerra": to_war,
        "rendirse": surrender,
        "lateral": side,
    }


def _shuffle(rng: random.Random):  # noqa: ANN202
    """Baraja solo el final del zapato, que es lo único que se reparte."""

    def shuffle(cards: list[bj.Card]) -> None:
        for i in range(len(cards) - 1, len(cards) - 1 - TOP, -1):
            j = rng.randrange(i + 1)
            cards[i], cards[j] = cards[j], cards[i]

    return shuffle


def play(hands: int, seed: int, *, go_to_war: bool) -> tuple[int, int, int, int]:
    """Juega `hands` manos con ficha 100; devuelve (apostado, devuelto, lateral, devuelto lateral).

    La lateral se juega en cada mano pero se cuenta aparte (`main_return` y `side_return`
    no se mezclan).
    """
    rng = random.Random(seed)
    shuffle = _shuffle(rng)
    stake_total = returned = side_stake = side_returned = 0
    for _ in range(hands):
        game = war.WarGame(100, side=100, shoe=bj.new_shoe(shuffle))
        game.deal()
        if game.awaiting_choice:
            game.go_to_war() if go_to_war else game.surrender()
        stake_total += game.stake  # la ficha inicial es la unidad del retorno
        # Lo que vuelve de la mano, menos la apuesta extra de la guerra.
        returned += game.main_return - (game.stake if game.went_to_war else 0)
        side_stake += game.side
        side_returned += game.side_return
    return stake_total, returned, side_stake, side_returned


def _run(args: tuple[int, int, bool]) -> tuple[int, int, int, int]:
    hands, seed, go_to_war = args
    return play(hands, seed, go_to_war=go_to_war)


def simulate(hands: int, *, go_to_war: bool, workers: int = 4) -> tuple[float, float, float]:
    """RTP (por ficha inicial), RTP de la lateral y su error típico, en una estrategia."""
    chunk = hands // workers
    jobs = [(chunk, 1000 + n + (0 if go_to_war else 77), go_to_war) for n in range(workers)]
    with ProcessPoolExecutor(workers) as pool:
        parts = list(pool.map(_run, jobs))
    stake = sum(p[0] for p in parts)
    returned = sum(p[1] for p in parts)
    side_stake = sum(p[2] for p in parts)
    side_returned = sum(p[3] for p in parts)
    # Error típico aproximado: la varianza de una mano ronda 1,0 fichas² .
    error = 1.0 / math.sqrt(stake / 100)
    return returned / stake, side_returned / side_stake, error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--manos", type=int, default=2_000_000)
    args = parser.parse_args()

    ex = exact()
    print("Exacto (6 barajas)")
    print(f"  empate en la primera carta : {float(ex['empate']):.4%}")
    print(f"  empate dentro de la guerra : {float(ex['empate_en_la_guerra']):.4%}")
    print(f"  ir siempre a la guerra     : {float(ex['guerra']):.4%}")
    print(f"  rendirse siempre           : {float(ex['rendirse']):.4%}")
    print(f"  lateral de empate (10:1)   : {float(ex['lateral']):.4%}")
    for label, go in (("ir a la guerra", True), ("rendirse", False)):
        rtp, side, error = simulate(args.manos, go_to_war=go)
        print(f"Simulado, {args.manos:,} manos, {label}: {rtp:.4%} (± {error:.2%})")
        print(f"  lateral de empate: {side:.4%}")


if __name__ == "__main__":
    main()
