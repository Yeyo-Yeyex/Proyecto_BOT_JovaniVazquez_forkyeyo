"""RTP del blackjack con estrategia básica, simulado con el código real del bot.

Uso, desde la raíz del repo:

    python docs/rtp_blackjack.py                  # 2 millones de manos en 4 procesos
    python docs/rtp_blackjack.py --manos 400000   # más rápido, menos preciso

Juega `bot.services.blackjack.BlackjackGame` tal cual, con la estrategia básica
de 6 barajas, banca que se planta en 17 blando, doblar tras separar y sin
rendición (las reglas del bot). Nunca toma el seguro, que es lo que hace la
estrategia básica; aparte mide lo que devuelve el seguro por cada ficha metida
en él, contando todas las veces que se ofrece (también con blackjack propio).

Salida: RTP de la mano (devuelto / apostado), su error típico, frecuencia de
blackjack del jugador y de la banca, RTP del seguro y, con blackjack propio, lo
que se cobra asegurado frente a sin asegurar. El RTP es lo que la Biblia y la
docstring de `bot.services.blackjack` citan: al cambiar una regla o un pago, se
vuelve a pasar y se actualizan las cifras.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.services import blackjack as bj  # noqa: E402

H, S, D, P, DS = "H", "S", "D", "P", "DS"


def _up(game: bj.BlackjackGame) -> int:
    """Carta de la banca a la vista: 2-10, y el as como 11."""
    value = game.dealer[0].value
    return 11 if value == 1 else value


def _hard(total: int, up: int) -> str:
    if total <= 8:
        return H
    if total == 9:
        return D if 3 <= up <= 6 else H
    if total == 10:
        return D if up <= 9 else H
    if total == 11:
        return D if up <= 10 else H
    if total == 12:
        return S if 4 <= up <= 6 else H
    if total <= 16:
        return S if up <= 6 else H
    return S


def _soft(total: int, up: int) -> str:
    if total in (13, 14):
        return D if up in (5, 6) else H
    if total in (15, 16):
        return D if 4 <= up <= 6 else H
    if total == 17:
        return D if 3 <= up <= 6 else H
    if total == 18:
        if 3 <= up <= 6:
            return DS
        return S if up in (2, 7, 8) else H
    return S


def _pair(value: int, up: int) -> bool:
    """Si se separa la pareja de `value` (as = 1) contra `up`."""
    if value in (1, 8):
        return True
    if value == 9:
        return up in (2, 3, 4, 5, 6, 8, 9)
    if value == 7:
        return up <= 7
    if value == 6:
        return up <= 6
    if value == 4:
        return up in (5, 6)
    if value in (2, 3):
        return up <= 7
    return False


def basic_action(game: bj.BlackjackGame) -> bj.Action:
    """Jugada de la estrategia básica para la mano activa."""
    hand = game.current
    up = _up(game)
    if game.can(bj.Action.SPLIT) and _pair(hand.cards[0].value, up):
        return bj.Action.SPLIT
    total, soft = bj.hand_total(hand.cards)
    move = _soft(total, up) if soft else _hard(total, up)
    if move in (D, DS):
        if game.can(bj.Action.DOUBLE):
            return bj.Action.DOUBLE
        move = S if move == DS else H
    return bj.Action.HIT if move == H else bj.Action.STAND


def _play(hands: int, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    totals = dict.fromkeys(
        (
            "staked",
            "returned",
            "sq",
            "player_bj",
            "dealer_bj",
            "offers",
            "offer_hits",
            "bj_offers",
            "bj_offer_dealer",
        ),
        0.0,
    )
    for _ in range(hands):
        game = bj.BlackjackGame(stake=100, shoe=bj.new_shoe(rng.shuffle))
        game.deal()
        natural = game.hands[0].natural
        if game.insurance_pending:
            dealer_bj = bj.is_blackjack(game.dealer)
            totals["offers"] += 1
            totals["offer_hits"] += dealer_bj
            if natural:
                totals["bj_offers"] += 1
                totals["bj_offer_dealer"] += dealer_bj
            game.decide_insurance(False)
        while game.player_turn:
            game.act(basic_action(game))
        game.reveal_hole()
        while game.dealer_should_draw():
            game.dealer_draw()
        game.settle()
        totals["staked"] += game.total_stake
        totals["returned"] += game.total_return
        totals["sq"] += (game.net / 100) ** 2
        totals["player_bj"] += natural
        totals["dealer_bj"] += bj.is_blackjack(game.dealer)
    totals["hands"] = hands
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manos", type=int, default=2_000_000)
    parser.add_argument("--procesos", type=int, default=4)
    parser.add_argument("--semilla", type=int, default=1)
    args = parser.parse_args(argv)
    share = args.manos // args.procesos
    with ProcessPoolExecutor(args.procesos) as pool:
        parts = list(
            pool.map(
                _play, [share] * args.procesos, range(args.semilla, args.semilla + args.procesos)
            )
        )
    t = {key: sum(part[key] for part in parts) for key in parts[0]}
    n = t["hands"]
    rtp = t["returned"] / t["staked"]
    # Error típico del neto medio por mano, pasado a proporción de lo apostado.
    mean_net = (t["returned"] - t["staked"]) / 100 / n
    sd = math.sqrt(max(t["sq"] / n - mean_net**2, 0))
    error = sd / math.sqrt(n) * n * 100 / t["staked"]
    p_ins = t["offer_hits"] / t["offers"]
    insurance_rtp = p_ins * (1 + bj.INSURANCE_PAYS)
    print(f"Manos: {n:,.0f}")
    print(f"RTP con estrategia básica, sin seguro: {rtp:.4%} (± {error:.2%})")
    print(f"Ventaja de la casa: {1 - rtp:.3%}")
    print(
        f"Blackjack del jugador: {t['player_bj'] / n:.3%} · de la banca: {t['dealer_bj'] / n:.3%}"
    )
    print(f"Seguro: la banca tiene blackjack en {p_ins:.2%} de los ases", end=" ")
    print(f"→ devuelve {insurance_rtp:.2%}")
    if t["bj_offers"]:
        p_bj = t["bj_offer_dealer"] / t["bj_offers"]
        pays = bj.blackjack_prize(100) / 100
        half = 0.5
        # Asegurado: con blackjack de la banca, empate y el seguro cobra 2:1; si no,
        # la mano cobra su premio y la media apuesta del seguro se pierde.
        insured = p_bj * half * bj.INSURANCE_PAYS + (1 - p_bj) * (pays - half)
        plain = (1 - p_bj) * pays
        print(
            f"Con blackjack propio y as a la vista ({t['bj_offers'] / n:.3%} de las manos): "
            f"asegurado cobras {insured:.3f} apuestas de media y sin asegurar {plain:.3f} "
            f"(la banca tiene blackjack el {p_bj:.2%})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
