"""Auditoría de las loterías, simulada con el código real del bot.

Uso, desde la raíz del repositorio::

    PYTHONIOENCODING=utf-8 python docs/auditoria_loteria.py              # un año, 20 y 60 personas
    PYTHONIOENCODING=utf-8 python docs/auditoria_loteria.py --dias 90 --miembros 10

Para cada juego de bote (Primitiva, Bonoloto, Gordo y Euromillones) juega un servidor
de `--miembros` personas donde cada una echa una apuesta automática en cada sorteo,
con las probabilidades del servidor (`server_odds`), el bombo cargado
(`draw_result`), el reparto (`settle_pool`) y el bote garantizado del Estado
(`guarantee_for`, con el Estado holgado). Mide cuánto de su parte de premios
reparte en todo el periodo (lo repartido más el bote que queda, frente a la parte de
premios de lo vendido más lo que pone el Estado) y comprueba que en ningún sorteo
una categoría cobra más que una superior. En un
sorteo suelto sí puede repartirse algo más de su parte (muchos reintegros o premios
fijos a la vez): lo cubre el Estado, como en la vida real, y se cuenta aparte.
Saca:

- el retorno al jugador (premios / recaudación), frente al `prize_rate` del juego;
- cuántas veces cae el bote al mes (la meta es una, `JACKPOT_PERIOD_DAYS`);
- lo que pone el Estado para llegar al bote garantizado;
- el bote que queda al final.

Para la Nacional y los rascas, el retorno exacto de sus tablas
(`nacional_expected_return` y la tabla de la emisión). El resultado comentado está en
`docs/auditoria-loteria.md`. Solo lee el código; no cambia nada.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.services import lottery as lot  # noqa: E402


def simulate_pool(game: lot.Game, members: int, days: int, seed: int) -> dict[str, float]:
    """Un servidor de `members` personas jugando `days` días a `game`."""
    rng = random.Random(seed)
    odds = lot.server_odds(game, members)
    draws = round(len(game.weekly) * days / 7)
    jackpot = next(c for c in game.categories if c.jackpot)
    totals = dict.fromkeys(("sales", "paid", "topup", "jackpots", "draws", "over"), 0.0)
    carry = 0
    problems: list[str] = []
    for _ in range(draws):
        tickets = [(i, lot.random_pick(game, rng)) for i in range(members)]
        sales = game.price * members
        result = lot.draw_result(game, rng, tickets=[(p, 1) for _, p in tickets], odds=odds)
        guarantee = lot.guarantee_for(game, 10**12, members)
        settled = lot.settle_pool(
            game, sales=sales, carry=carry, guarantee=guarantee, tickets=tickets, result=result
        )
        # Lo que puede repartirse: la recaudación por su parte de premios, el bote que
        # venía y lo que ponga el Estado. El reintegro sale del 10 % de la Primitiva y
        # la Bonoloto, que también está dentro de `prize_rate`.
        available = sales * game.prize_rate + carry + settled.topup
        totals["over"] += settled.paid + settled.carry > available + 1
        prizes = [c.prize for c in settled.categories if c.winners]
        if any(low > high for high, low in zip(prizes, prizes[1:], strict=False)):
            problems.append(f"inversión de premios: {prizes}")
        won = next(c for c in settled.categories if c.key == jackpot.key).winners
        totals["jackpots"] += bool(won)
        totals["sales"] += sales
        totals["paid"] += settled.paid
        totals["topup"] += settled.topup
        totals["draws"] += 1
        carry = settled.carry
    totals["carry"] = carry
    # Lo que se reparte (con el bote que queda) frente a la parte de premios de lo
    # vendido: por encima de 1, el Estado pone la diferencia (ver el .md).
    budget = totals["sales"] * game.prize_rate + totals["topup"]
    totals["budget_used"] = (totals["paid"] + carry) / budget
    totals["months"] = days / 30
    totals["problems"] = problems  # type: ignore[assignment]
    return totals


def main(argv: list[str] | None = None) -> int:
    """Imprime la auditoría; sale con 1 si una categoría cobra más que una superior."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dias", type=int, default=365)
    parser.add_argument("--miembros", type=int, nargs="*", default=[20, 60])
    parser.add_argument("--semilla", type=int, default=1)
    args = parser.parse_args(argv)
    failed = False
    print("Juegos de bote (cada miembro, una apuesta por sorteo):")
    for game in lot.GAMES:
        if not game.categories:
            continue
        for members in args.miembros:
            t = simulate_pool(game, members, args.dias, args.semilla)
            rtp = t["paid"] / t["sales"]
            per_month = t["jackpots"] / t["months"]
            print(
                f"  {game.name:<24} {members:>3} personas: devuelve {rtp:6.1%} "
                f"(prize_rate {game.prize_rate:.0%}) · bote {per_month:.2f} veces al mes · "
                f"el Estado pone {t['topup'] / t['sales']:.1%} de lo vendido y cubre "
                f"{t['over'] / t['draws']:.0%} de los sorteos · "
                f"reparte el {t['budget_used']:.1%} de su parte · "
                f"bote final {int(t['carry']):,} Y$".replace(",", ".")
            )
            for problem in sorted(set(t["problems"]))[:5]:  # type: ignore[arg-type]
                failed = True
                print(f"    ⚠️ {problem}")
    print("\nLotería Nacional (retorno de la emisión en un sorteo):")
    rng = random.Random(args.semilla)
    for game in lot.GAMES:
        if game.kind is lot.Kind.NACIONAL:
            print(f"  {game.name:<24} {lot.nacional_expected_return(game, rng):.1%}")
    print("\nRascas (retorno de la emisión):")
    for game in lot.GAMES:
        if game.scratch is not None:
            prizes, total = game.scratch
            back = sum(p.amount * p.count for p in prizes) / (game.price * total)
            print(f"  {game.name:<24} {back:.1%} · premio 1 de cada {lot.scratch_odds(game)}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
