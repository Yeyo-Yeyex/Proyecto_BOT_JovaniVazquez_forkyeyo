"""Rarezas de los logros de suerte de las ONGs: sorteos, regalos y exclusivos.

Juega años de sorteos de los viernes con las reglas reales del bot
(`bot.services.donations`: `draw_winner`, `RAFFLE_MIN`, `PATRON_LEVELS`,
`RAFFLE_EXCLUSIVE`) y dice cuántos días tarda la mediana de los jugadores en cada meta.
La rareza sale de la escala de la Biblia (Común hasta 3 días, Raro hasta 14, Épico hasta
60, Legendario hasta 240 y Mítico más) y se compara con la del catálogo.

Supuestos:

- El jugador dona `RAFFLE_MIN` a `--ongs` ONGs cada semana (lo justo para entrar en sus
  sorteos), así que sube de nivel de mecenas despacio, como sus rivales.
- Cada sorteo tiene `--rivales` donantes más que hacen lo mismo.
- El premio exclusivo sale con `RAFFLE_EXCLUSIVE` (y con `exclusive` de cada nivel al
  subir) y siempre es uno que aún no tiene (`Tienda.grant_ong_gift`).

Uso: `python docs/auditoria_ongs.py [--ongs 10 3] [--rivales 2] [--jugadores 400]`.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.services.achievements import CATALOG, Rarity  # noqa: E402
from bot.services.donations import (  # noqa: E402
    GIFTS_BY_ONG,
    ONGS,
    PATRON_LEVELS,
    RAFFLE_EXCLUSIVE,
    RAFFLE_MIN,
    draw_winner,
)

#: Estadísticas de suerte que se miden aquí.
STATS = (
    "ong_raffle_won",
    "ong_gifts",
    "ong_exclusive_gifts",
    "ong_exclusives_owned",
    "ong_exclusive_set_max",
)
#: Escala de la Biblia: días como mucho para cada rareza.
SCALE = ((3, Rarity.COMMON), (14, Rarity.RARE), (60, Rarity.EPIC), (240, Rarity.LEGENDARY))
ME = 0


def rarity_for(days: float) -> Rarity:
    """La rareza que pide la Biblia para algo que tarda `days` días."""
    return next((r for limit, r in SCALE if days <= limit), Rarity.MYTHIC)


def simulate(ongs: int, rivals: int, weeks: int, rng: random.Random) -> dict[str, list[int]]:
    """Un jugador: para cada estadística, su valor al final de cada semana."""
    keys = [o.key for o in ONGS[:ongs]]
    total = dict.fromkeys(keys, 0)
    owned = {k: 0 for k in keys}
    stats = dict.fromkeys(STATS, 0)
    history: dict[str, list[int]] = {s: [] for s in STATS}

    def gift(ong: str, exclusive: float) -> None:
        stats["ong_gifts"] += 1
        if owned[ong] < len(GIFTS_BY_ONG[ong]) and rng.random() < exclusive:
            owned[ong] += 1
            stats["ong_exclusive_gifts"] += 1

    for _week in range(weeks):
        for ong in keys:
            before = total[ong]
            total[ong] += RAFFLE_MIN
            for level in PATRON_LEVELS:
                if before < level.threshold <= total[ong]:
                    gift(ong, level.exclusive)
            entries = {ME: (RAFFLE_MIN, total[ong])}
            entries |= {i: (RAFFLE_MIN, total[ong]) for i in range(1, rivals + 1)}
            if draw_winner(entries, rng) == ME:
                stats["ong_raffle_won"] += 1
                gift(ong, RAFFLE_EXCLUSIVE)
        stats["ong_exclusives_owned"] = sum(owned.values())
        stats["ong_exclusive_set_max"] = max(owned.values())
        for stat in STATS:
            history[stat].append(stats[stat])
    return history


def days_to(histories: list[list[int]], goal: int) -> float | None:
    """Mediana de días hasta llegar a `goal` (`None` si la mitad no llega)."""
    weeks = [next((w + 1 for w, v in enumerate(h) if v >= goal), None) for h in histories]
    reached = sorted(w for w in weeks if w is not None)
    if len(reached) * 2 < len(weeks):
        return None
    return statistics.median([w if w is not None else 10**6 for w in weeks]) * 7


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ongs", type=int, nargs="*", default=[10, 3])
    parser.add_argument("--rivales", type=int, default=2)
    parser.add_argument("--jugadores", type=int, default=400)
    parser.add_argument("--semanas", type=int, default=520)
    parser.add_argument("--semilla", type=int, default=1)
    args = parser.parse_args(argv)
    rng = random.Random(args.semilla)
    goals = [a for a in CATALOG if len(a.conditions) == 1 and a.stat in STATS]
    print(f"Entrada: {RAFFLE_MIN} Y$ por ONG y semana · {args.rivales} rivales por sorteo")
    for ongs in args.ongs:
        runs = [simulate(ongs, args.rivales, args.semanas, rng) for _ in range(args.jugadores)]
        print(f"\n## Donando a {ongs} ONGs ({ongs * RAFFLE_MIN} Y$ a la semana)\n")
        print("| Logro | Meta | Días (mediana) | Pide | Tiene |")
        print("|---|---|---|---|---|")
        for achievement in goals:
            stat, goal = achievement.conditions[0]
            days = days_to([r[stat] for r in runs], goal)
            wanted = rarity_for(days) if days is not None else Rarity.MYTHIC
            shown = f"{days:.0f}" if days is not None else "nunca (10 años)"
            flag = "" if wanted is achievement.rarity else " ⚠️"
            print(
                f"| {achievement.id} | {goal} | {shown} | {wanted.value[0]} | "
                f"{achievement.rarity.value[0]}{flag} |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
