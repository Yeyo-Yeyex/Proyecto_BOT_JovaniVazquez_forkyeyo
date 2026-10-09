"""Regenera la biblioteca de caídas del pachinko (`trayectorias.json`).

Uso, desde la raíz del repo:

    python docs/pachinko_trayectorias.py            # escribe el archivo de datos
    python docs/pachinko_trayectorias.py --resumen  # además, enseña cifras por tablero
    python docs/pachinko_trayectorias.py --comprobar  # no escribe: dice si el archivo está al día

Entradas: la geometría y las constantes físicas de `bot.services.pachinko_physics`
y los tableros de `bot.services.pachinko`. La semilla es fija (una por tablero),
así que ejecutarlo dos veces da el mismo archivo.

Efectos: sobrescribe `src/bot/assets/pachinko/trayectorias.json`. Para cada
tablero y cada bolsillo guarda `TRAJECTORIES_PER_POCKET` caídas: bolas
simuladas de verdad (gravedad, rebotes en clavos y paredes) que acaban en ese
bolsillo, con su condición inicial y la posición en cada fotograma del GIF.
En cada tanda, el bolsillo lo sortea `bot.services.pachinko` y
`bot.services.pachinko_motion` prueba como salida de cada bola las de su
bolsillo (con las bolas chocando entre sí, la caída final puede ser otra, pero
acaba en el mismo bolsillo); el pago no depende de ellas.

Hay que volver a ejecutarlo siempre que cambie la geometría (clavos, paredes,
bolsillos, adornos) o una constante física: la prueba
`test_la_biblioteca_guardada_se_puede_reproducir` falla si el archivo se queda
viejo. Después hay que revisar los logros de rebotes (`pachinko_bounces`,
`pachinko_bounce_max`…), que se calibran con estas mismas caídas
(`docs/auditoria-logros.md`).

Tarda unos 5 segundos (unas decenas de miles de simulaciones, ~0,5 ms cada una)
y no necesita red. El archivo pesa ~0,5 MB.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bot.services.pachinko import BOARDS  # noqa: E402
from bot.services.pachinko_physics import (  # noqa: E402
    DATA_PATH,
    TRAJECTORIES_PER_POCKET,
    bake_library,
    encode_library,
)


def hornear() -> dict:
    """Biblioteca de todos los tableros, con una semilla fija por tablero."""
    return {
        board.key: bake_library(board, random.Random(f"pachinko-{board.key}"))
        for board in BOARDS.values()
    }


def resumen(bibliotecas: dict) -> None:
    """Cifras de cada tablero: caídas, duración y rebotes."""
    for key, biblioteca in bibliotecas.items():
        todas = [t for caidas in biblioteca.values() for t in caidas]
        rebotes = [t.bounces for t in todas]
        print(
            f"{key}: {len(biblioteca)} bolsillos x {TRAJECTORIES_PER_POCKET} caídas; "
            f"duran {min(t.seconds for t in todas):.2f}-{max(t.seconds for t in todas):.2f} s "
            f"(mediana {statistics.median(t.seconds for t in todas):.2f}); "
            f"rebotes {min(rebotes)}-{max(rebotes)} (mediana {statistics.median(rebotes)})"
        )


def main() -> int:
    """Punto de entrada: genera, escribe o comprueba el archivo."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--resumen", action="store_true")
    parser.add_argument("--comprobar", action="store_true")
    args = parser.parse_args()
    bibliotecas = hornear()
    texto = encode_library(bibliotecas)
    if args.resumen:
        resumen(bibliotecas)
    if args.comprobar:
        al_dia = DATA_PATH.exists() and DATA_PATH.read_text(encoding="utf-8") == texto
        print("trayectorias.json está al día" if al_dia else "trayectorias.json está VIEJO")
        return 0 if al_dia else 1
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(texto, encoding="utf-8")
    print(f"{DATA_PATH}: {len(texto) / 1_000_000:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
