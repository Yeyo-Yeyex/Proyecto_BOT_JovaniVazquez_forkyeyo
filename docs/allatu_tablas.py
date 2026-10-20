"""Mide las tablas de premios de Allá tú: cuánto pesa el gordo y si hay partida sin él.

Uso, desde la raíz del repo:

    python docs/allatu_tablas.py                 # 100.000 partidas por tabla
    python docs/allatu_tablas.py --partidas 20000

Por qué existe: en la 8.7 el gordo era el 72-90 % del valor de cada tabla y, al
abrirlo (20 de cada 22 partidas), en Picante e Infierno no quedaba ninguna caja de
×1 o más: el resto de la partida era abrir cajas para perder. La 8.8.0 puso una
escalera de cajas rojas debajo del gordo. Antes de tocar una tabla, se mira aquí.

Columnas:

- **Gordo**: lo que vale de media y qué parte es del valor de la tabla (22 × 0,99).
- **Rojas**: cajas de ×1 o más sin contar el gordo, y la mayor.
- **Final ≥ ×1**: probabilidad de cobrar ×1 o más llegando al final.
- **Jugador**: partidas con el código real (`AllatuGame`) y la estrategia de un
  jugador normal de `docs/auditoria_logros.py` (acepta cuando la oferta llega a lo
  que pide, regatea a veces y cambia de caja la mitad de las veces). Devuelve el
  % de lo apostado, cuántas partidas gana, cuántas gana de las que perdió el gordo
  y en cuántas llamó Perro Sanxe con el pésame.
"""

from __future__ import annotations

import argparse
import random
import sys
from fractions import Fraction

sys.path.insert(0, "src")

from bot.services.allatu import BOXES, RTP, TABLES, AllatuGame, Phase, Table  # noqa: E402

#: Lo que pide un jugador normal para aceptar (veces la apuesta), como en la auditoría.
PIDE = ((0.8, 20), (1.0, 30), (1.5, 25), (3.0, 15), (100.0, 10))
APUESTA = 100


def _elegir(rng: random.Random, opciones: tuple[tuple[float, int], ...]) -> float:
    return rng.choices([v for v, _ in opciones], weights=[w for _, w in opciones])[0]


def jugar(table: Table, rng: random.Random) -> AllatuGame:
    """Una partida de un jugador normal, hasta el final."""
    game = AllatuGame.new(APUESTA, table, rng)
    wants = _elegir(rng, PIDE)
    game.choose(rng.randrange(BOXES))
    while game.playing:
        if game.phase is Phase.OPEN:
            game.open(rng.choice(game.openable))
        elif game.phase is Phase.OFFER:
            offer = game.offer
            assert offer is not None
            if offer.amount >= wants * APUESTA:
                game.deal()
            elif game.can_haggle and rng.random() < 0.2:
                game.haggle()
            else:
                game.no_deal()
        else:
            game.finish(swap=rng.random() < 0.5)
    return game


def medir(table: Table, partidas: int, seed: int) -> str:
    """Una línea con las cifras de `table`."""
    total = BOXES * RTP
    reds = [v for v in table.base if v >= 1]
    rng = random.Random(seed)
    pagado = ganadas = perdido = ganadas_sin = pesames = 0
    for _ in range(partidas):
        game = jugar(table, rng)
        pagado += game.payout
        ganadas += game.net > 0
        pesames += any(o.condolence for o in game.offers)
        if game.gordo_lost:
            perdido += 1
            ganadas_sin += game.net > 0
    final = Fraction(len(reds) + 1, BOXES)
    return (
        f"{table.emoji} {table.label:9} gordo ×{float(table.gordo_mean):5.2f} "
        f"({float(table.gordo_mean / total):3.0%}) · rojas {len(reds)} hasta "
        f"×{float(max(table.base)):g} · final ≥ ×1 {float(final):3.0%} · jugador: "
        f"{pagado / (partidas * APUESTA):3.0%} devuelto, gana {ganadas / partidas:3.0%}, "
        f"sin el gordo gana {ganadas_sin / max(perdido, 1):3.0%}, "
        f"pésame en {pesames / partidas:3.0%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--partidas", type=int, default=100_000)
    parser.add_argument("--semilla", type=int, default=1)
    args = parser.parse_args()
    for table in TABLES:
        print(medir(table, args.partidas, args.semilla))


if __name__ == "__main__":
    main()
