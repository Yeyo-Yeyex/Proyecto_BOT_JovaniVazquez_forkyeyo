"""Auditoría de las loterías, simulada con el código real del bot.

Uso, desde la raíz del repositorio::

    PYTHONIOENCODING=utf-8 python docs/auditoria_loteria.py              # un año, 20 y 60 personas
    PYTHONIOENCODING=utf-8 python docs/auditoria_loteria.py --dias 90 --miembros 10

Para cada juego de bote (Primitiva, Bonoloto, Gordo y Euromillones) juega un servidor
de `--miembros` personas en varios escenarios de participación (todo el servidor con
una apuesta por sorteo, unas pocas personas con varias, una sola al tope), con las
probabilidades del servidor (`server_odds`), el bombo cargado (`draw_result`), los
premios fijos y el bote (`settle_pool`) y el mínimo del Estado que sube cada día
(`guarantee_for`, con el Estado holgado). Saca:

- lo que devuelve a los jugadores (premios / lo apostado): por encima del 100 %, el
  Estado pone dinero de su bolsillo;
- lo que pone o gana el Estado al mes (lo vendido menos lo pagado);
- cuántas veces cae el bote al mes y de cuánto es cuando cae;
- y comprueba que en ningún sorteo una categoría cobra más que una superior.

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

#: Escenarios de participación: `(nombre, parte del servidor que juega, apuestas por sorteo)`.
SCENARIOS = (
    ("todos, 1 apuesta", 1.0, 1),
    ("1 de cada 4, 3 apuestas", 0.25, 3),
    ("1 persona al tope", 0.0, lot.POOL_MAX_PER_DRAW),
)


def simulate_pool(
    game: lot.Game,
    members: int,
    days: int,
    seed: int,
    *,
    share: float = 1.0,
    bets: int = 1,
) -> dict[str, object]:
    """Un servidor de `members` personas jugando `days` días a `game`.

    Args:
        share: Parte del servidor que juega (al menos una persona).
        bets: Apuestas automáticas de cada una en cada sorteo.
    """
    rng = random.Random(seed)
    odds = lot.server_odds(game, members)
    players = max(1, round(members * share))
    step = 7 / len(game.weekly)  # días entre sorteos, de media
    draws = round(days / step)
    jackpot = lot.jackpot_key(game)
    totals: dict[str, object] = dict.fromkeys(("sales", "paid", "topup", "jackpots"), 0)
    pots: list[int] = []
    problems: list[str] = []
    carry = 0
    since = 0.0
    for index in range(draws):
        now = index * step
        tickets = [(i, lot.random_pick(game, rng)) for i in range(players * bets)]
        sales = game.price * len(tickets)
        result = lot.draw_result(game, rng, tickets=[(p, 1) for _, p in tickets], odds=odds)
        guarantee = lot.guarantee_for(game, 10**12, now - since)
        settled = lot.settle_pool(
            game,
            sales=sales,
            carry=carry,
            guarantee=guarantee,
            tickets=tickets,
            result=result,
            members=members,
        )
        prizes = [c.prize for c in settled.categories if c.winners]
        if any(low > high for high, low in zip(prizes, prizes[1:], strict=False)):
            problems.append(f"inversión de premios: {prizes}")
        if next(c for c in settled.categories if c.key == jackpot).winners:
            totals["jackpots"] += 1  # type: ignore[operator]
            pots.append(settled.jackpot)
            since = now
        totals["sales"] += sales  # type: ignore[operator]
        totals["paid"] += settled.paid  # type: ignore[operator]
        totals["topup"] += settled.topup  # type: ignore[operator]
        carry = settled.carry
    months = days / 30
    totals.update(
        draws=draws,
        months=months,
        carry=carry,
        pots=pots,
        problems=problems,
        rtp=totals["paid"] / max(1, totals["sales"]),  # type: ignore[operator]
        state_per_month=(totals["sales"] - totals["paid"]) / months,  # type: ignore[operator]
    )
    return totals


def _thousands(value: int) -> str:
    return f"{value:+,}".replace(",", ".") if value < 0 else f"{value:,}".replace(",", ".")


def main(argv: list[str] | None = None) -> int:
    """Imprime la auditoría; sale con 1 si una categoría cobra más que una superior."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dias", type=int, default=365)
    parser.add_argument("--miembros", type=int, nargs="*", default=[20, 60])
    parser.add_argument("--semilla", type=int, default=1)
    args = parser.parse_args(argv)
    failed = False
    print("Juegos de bote (el Estado holgado; «Estado/mes» negativo es lo que pone él):")
    for game in lot.GAMES:
        if not game.categories:
            continue
        print(f"  {game.name}")
        for members in args.miembros:
            for name, share, bets in SCENARIOS:
                t = simulate_pool(game, members, args.dias, args.semilla, share=share, bets=bets)
                pots = sorted(t["pots"])  # type: ignore[arg-type]
                median = pots[len(pots) // 2] if pots else 0
                state = _thousands(int(t["state_per_month"]))  # type: ignore[call-overload]
                print(
                    f"    {members:>3} personas, {name:<24} devuelve {t['rtp']:7.1%} · "
                    f"Estado/mes {state:>8} Y$ · "
                    f"bote {t['jackpots'] / t['months']:.2f} veces al mes, "  # type: ignore[operator]
                    f"mediana {_thousands(median)} Y$"
                )
                for problem in sorted(set(t["problems"]))[:5]:  # type: ignore[call-overload]
                    failed = True
                    print(f"      ⚠️ {problem}")
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
