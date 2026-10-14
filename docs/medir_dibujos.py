"""Mide lo que tarda cada juego en tener su GIF listo, como en el NAS (4 hilos).

Para decidir con números antes y después de tocar el dibujo de un juego
(«Juego nuevo con dibujo» en CLAUDE.md). Por cada juego da dos tiempos, en ms:

- `1 abierto`: la primera jugada tras un rato sin jugar, con solo el proceso de
  Node que siempre está abierto (los demás se cierran a los 10 minutos).
- `caliente`: la mediana de 4 jugadas seguidas, con todos los procesos abiertos.

Uso, desde la raíz del repo (necesita `node` y `npm ci` hecho):

    python docs/medir_dibujos.py                     # todos los juegos, 4 hilos
    python docs/medir_dibujos.py moneda caballos     # solo esos
    python docs/medir_dibujos.py --repo ../viejo     # el código de otra copia

`--hilos N` (4 por defecto, los del N100) ata el proceso y los Node que abre a N
procesadores, saltando de dos en dos si hay hyperthreading; `--hilos 0` no ata
nada. Para comparar con la versión de antes: `git worktree add ../viejo
upstream/main` y medir las dos copias, mejor dos veces cada una (el ruido en
esta máquina ronda el 5 %). Con `--repo`, Node usa el `node_modules` de esta copia.

Un juego nuevo se añade a `GAMES` con su escena y una jugada de sus pruebas.
Solo usa lo instalado para las pruebas; no escribe nada.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent
HOT_PLAYS = 4


def pin(threads: int) -> None:
    """Ata este proceso (y los que abra) a `threads` procesadores."""
    count = os.cpu_count() or threads
    step = 2 if count >= 2 * threads else 1
    cpus = [i * step for i in range(min(threads, count))]
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, cpus)
    elif sys.platform == "win32":
        import ctypes

        mask = sum(1 << cpu for cpu in cpus)
        kernel = ctypes.windll.kernel32
        kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), mask)


def games() -> dict[str, tuple[Callable[[], Any], Callable[[Any], Awaitable[Any]]]]:
    """Cada juego: cómo crear su escena (con Node, sin Chromium) y una jugada de prueba."""
    from craps_fakes import game_after, table_for
    from test_bacara_node import BEFORE, HEADER, THIRDS
    from test_backgammon_scene import META as BG_META
    from test_backgammon_scene import typical_turn
    from test_bus_scene import CARDS
    from test_coin_scene import lost_game
    from test_horses_cog import fixed_result
    from test_horses_scene import card_of
    from test_roulette_scene_browser import HISTORY, outcome

    from bot.services.bacara_scene import BacaraScene
    from bot.services.backgammon_scene import BackgammonScene
    from bot.services.bus_scene import BusScene
    from bot.services.coin import Side
    from bot.services.coin_scene import CoinScene
    from bot.services.craps_scene import CrapsScene
    from bot.services.horses_scene import SceneRenderer
    from bot.services.roulette_scene import RouletteScene

    no = "/no/existe/chromium"
    spin = outcome(17, [("17", 100), ("rojo", 50)], {17: 200})
    return {
        "moneda": (
            lambda: CoinScene(executable_path=no),
            lambda s: s.toss(lost_game(), start=Side.CARA, seed=3),
        ),
        "autobus": (lambda: BusScene(executable_path=no), lambda s: s.reveal(CARDS, 3, seed=4)),
        "dados": (
            lambda: CrapsScene(executable_path=no),
            lambda s: s.throw(table_for(game_after((1, 5))), seed=4),
        ),
        "bacara": (
            lambda: BacaraScene(executable_path=no),
            lambda s: s.coup(THIRDS, BEFORE, HEADER, seed=4),
        ),
        "ruleta": (
            lambda: RouletteScene(executable_path=no),
            lambda s: s.spin(spin, history=HISTORY, seed=3),
        ),
        "caballos": (
            lambda: SceneRenderer(executable_path=no),
            lambda s: s.race(card_of(), fixed_result((0, 1, 2, 3, 4, 5))),
        ),
        # Un turno contra Jovani: tu jugada con una comida, su tirada, su jugada y tus dados.
        "backgammon": (
            lambda: BackgammonScene(executable_path=no),
            lambda s: s.animate(BG_META, *typical_turn()),
        ),
    }


async def measure(
    make: Callable[[], Any], play: Callable[[Any], Awaitable[Any]]
) -> tuple[float, float]:
    """Milisegundos con un solo proceso abierto y en caliente (mediana)."""
    from bot.services import node_scene

    await node_scene.warm()
    scene = make()
    await play(scene)  # carga la escena en el proceso que siempre está abierto
    pool = node_scene._pool(None)
    for worker in list(pool.workers[node_scene.MIN_WORKERS :]):
        pool.workers.remove(worker)
        await worker.close()
    start = time.perf_counter()
    await play(scene)
    first = time.perf_counter() - start
    hot = []
    for _ in range(HOT_PLAYS):
        start = time.perf_counter()
        await play(scene)
        hot.append(time.perf_counter() - start)
    await scene.close()
    await node_scene.shutdown()
    return first * 1000, statistics.median(hot) * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("juegos", nargs="*", help="Juegos a medir (todos si no se dice).")
    parser.add_argument("--repo", type=Path, default=HERE, help="Copia del repo a medir.")
    parser.add_argument("--hilos", type=int, default=4, help="Procesadores (0: sin atar).")
    args = parser.parse_args()
    repo = args.repo.resolve()
    sys.path[:0] = [str(repo / "src"), str(repo / "tests"), str(repo / "tests" / "unit")]
    os.environ.setdefault("NODE_PATH", str(HERE / "node_modules"))
    if args.hilos:
        pin(args.hilos)
    table = games()
    names = args.juegos or list(table)
    print(f"{'juego':9} {'1 abierto':>10} {'caliente':>9}")
    for name in names:
        first, hot = asyncio.run(measure(*table[name]))
        print(f"{name:9} {first:10.0f} {hot:9.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
