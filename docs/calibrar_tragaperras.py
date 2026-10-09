"""Busca los pesos del rodillo virtual de la tragaperras (`REEL_WEIGHTS`).

Uso, desde la raíz del repo:

    python docs/calibrar_tragaperras.py                 # semilla 1, 40.000 pasos
    python docs/calibrar_tragaperras.py --semilla 3 --pasos 80000

Imprime los pesos (para pegarlos en `bot.services.slots.REEL_WEIGHTS`) y las
cifras que salen con ellos. No cambia ningún archivo.

Cómo funciona: lo que pasa en cada parada de los tres rodillos (premio,
casi-premio, giros gratis…) no depende de los pesos, así que se calcula una
sola vez para las ~29.000 combinaciones. Después, cada cifra es una suma
ponderada por el producto de los pesos, que con numpy tarda milisegundos. Un
recocido simulado mueve los pesos de uno en uno y se queda con los cambios
que acercan las cifras a `OBJETIVOS`.

El retorno con la máquina caliente y los giros gratis se aproxima así: con
una probabilidad de premio `h`, la barra se llena cada `HEAT_MAX / h` tiradas
y la siguiente paga doble; cada tirada pagada trae de media `FREE_SPINS × f`
giros gratis, con `f` la probabilidad de sacarlos. Los tests del servicio
comprueban la cifra con una simulación completa.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bot.services.slots import FREE_SPINS, HEAT_MAX, REEL_STRIPS, spin_at  # noqa: E402

#: Cifra: (objetivo, tolerancia). Las de frecuencia van en «1 de cada N» y se
#: comparan en logaritmo; el resto, en tanto por uno.
OBJETIVOS = {
    "base": (0.965, 0.002),  # línea + calor + giros gratis; con el bote, 99,5 %
    "casi": (0.22, 0.01),
    "premio": (0.33, 0.01),
    "ldw_de_premios": (0.6, 0.03),
}
OBJETIVOS_1_DE = {
    "jackpot": (20_000, 0.15),
    "giros": (120, 0.1),
    "epico": (2_000, 0.25),
    "mega": (400, 0.25),
    "gran": (25, 0.25),
}


def tablas() -> dict[str, np.ndarray]:
    """Lo que pasa en cada combinación de paradas, sin pesos."""
    shape = tuple(len(strip) for strip in REEL_STRIPS)
    names = ("pago", "jackpot", "casi", "giros", "premio", "ldw", "gran", "mega", "epico")
    out = {name: np.zeros(shape) for name in names}
    for stops in itertools.product(*(range(n) for n in shape)):
        spin = spin_at(stops)
        pay = spin.pay_halves
        out["pago"][stops] = pay
        out["jackpot"][stops] = spin.is_jackpot
        out["casi"][stops] = spin.near_miss
        out["giros"][stops] = spin.triggers_free_spins
        out["premio"][stops] = pay > 0 or spin.is_jackpot
        out["ldw"][stops] = 0 < pay < 2
        # En medias apuestas: ×5, ×15 y ×50 lo apostado.
        out["gran"][stops] = pay >= 10
        out["mega"][stops] = pay >= 30
        out["epico"][stops] = pay >= 100 or spin.is_jackpot
    return out


def cifras(pesos: list[list[int]], t: dict[str, np.ndarray]) -> dict[str, float]:
    """Probabilidades y retorno con unos pesos."""
    a, b, c = (np.array(p, float) / sum(p) for p in pesos)
    peso = np.einsum("i,j,k->ijk", a, b, c)
    s = {name: float((peso * tabla).sum()) for name, tabla in t.items()}
    caliente = 1 / (HEAT_MAX / s["premio"] + 1)
    s["linea"] = s["pago"] / 2
    s["base"] = s["linea"] * (1 + caliente) * (1 + FREE_SPINS * s["giros"])
    s["ldw_de_premios"] = s["ldw"] / s["premio"]
    return s


def coste(s: dict[str, float]) -> float:
    """Distancia a los objetivos (0 = todos clavados)."""
    total = sum(((s[k] - meta) / tol) ** 2 for k, (meta, tol) in OBJETIVOS.items())
    for k, (meta, tol) in OBJETIVOS_1_DE.items():
        uno_de = 1 / max(s[k], 1e-12)
        total += (math.log(uno_de / meta) / tol) ** 2
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument("--pasos", type=int, default=40_000)
    args = parser.parse_args()

    t = tablas()
    rng = random.Random(args.semilla)
    pesos = [[10] * len(strip) for strip in REEL_STRIPS]
    mejor = coste(cifras(pesos, t))
    temperatura = 5.0
    for _ in range(args.pasos):
        rodillo = rng.randrange(3)
        casilla = rng.randrange(len(pesos[rodillo]))
        antes = pesos[rodillo][casilla]
        pesos[rodillo][casilla] = max(1, min(60, antes + rng.choice((-5, -2, -1, 1, 2, 5))))
        nuevo = coste(cifras(pesos, t))
        # Recocido: a veces acepta empeorar, para no quedarse en un mínimo local.
        if nuevo < mejor or rng.random() < math.exp((mejor - nuevo) / temperatura):
            mejor = nuevo
        else:
            pesos[rodillo][casilla] = antes
        temperatura = max(0.01, temperatura * 0.9995)

    s = cifras(pesos, t)
    print(json.dumps(pesos))
    print(f"coste {mejor:.2f}")
    for k in ("base", "linea", "casi", "premio", "ldw_de_premios"):
        print(f"{k}: {s[k]:.4f}")
    for k in OBJETIVOS_1_DE:
        print(f"{k}: 1 de cada {1 / s[k]:,.0f}")


if __name__ == "__main__":
    main()
