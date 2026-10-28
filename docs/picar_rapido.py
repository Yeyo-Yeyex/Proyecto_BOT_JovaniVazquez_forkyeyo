"""Motor rápido del pico (`docs/picar_rapido.c`) para calibrar: compila, carga y compara.

El calibrador (`docs/calibrar_picar.py`) simula millones de partidas. En Python son
~5-60 ms cada una; este motor en C hace lo mismo unas 50 veces más rápido y da los mismos
resultados bit a bit (ver la cabecera de `picar_rapido.c`). El bot no lo usa: juega con
el motor de Python, y la huella que comprueba al arrancar dice si los dos coinciden.

- `build()` escribe `picar_params.h` con las constantes, los bloques y las capas del
  código de Python (una sola fuente de verdad), compila con `cc` y guarda la biblioteca en
  una carpeta de caché con el hash de todo, así que solo recompila si algo cambia.
- `simulate(seed, pick)` devuelve un `picar_physics.Run` sin fotogramas ni eventos, igual
  que `simulate(..., record=False)`.
- `check(seeds)` juega las mismas semillas con los dos motores y falla a la primera
  diferencia. El calibrador lo hace siempre antes de empezar.

Hace falta un compilador de C (`cc`, `gcc` o `clang`; en Windows, el de WSL o MinGW). Si no
hay, el calibrador usa el motor de Python y lo dice.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from functools import cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bot.services import picar_physics as ph  # noqa: E402
from bot.services import picar_world as pw  # noqa: E402

SOURCE = Path(__file__).resolve().parent / "picar_rapido.c"
FLAGS = ["-O2", "-ffp-contract=off", "-fno-fast-math", "-shared", "-fPIC"]
KINDS = {pw.Kind.PLAIN: 0, pw.Kind.TNT: 1, pw.Kind.CHEST: 2, pw.Kind.CRAFT: 3, pw.Kind.MULT: 4}


def header() -> str:
    """`picar_params.h`: todo lo que decide una partida, sacado del código de Python."""
    blocks = pw.BLOCKS
    layers = pw.LAYERS
    tables = [pw._TABLES[layer.key] for layer in layers]
    width = max(len(t.ids) for t in tables)
    lines = [
        "/* Generado por docs/picar_rapido.py: no se edita a mano. */",
        f"#define COLUMNS {pw.COLUMNS}",
        f"#define CHUNK {pw.CHUNK}",
        f"#define DRAGON_ROW {pw.DRAGON_ROW}",
        f"#define RAVINE_REGION {pw.RAVINE_REGION}",
        f"#define RAVINE_ROWS_MIN {pw.RAVINE_ROWS[0]}",
        f"#define RAVINE_ROWS_MAX {pw.RAVINE_ROWS[1]}",
        f"#define NBLOCKS {len(blocks)}",
        *(f"#define KIND_{kind.name} {value}" for kind, value in KINDS.items()),
    ]

    def array(ctype: str, name: str, values: list[int]) -> str:
        return f"static const {ctype} {name}[] = {{{', '.join(str(v) for v in values)}}};"

    lines += [
        array("int", "BLOCK_HARDNESS", [b.hardness for b in blocks]),
        array("int", "BLOCK_RESISTANCE", [b.resistance for b in blocks]),
        array("int", "BLOCK_KIND", [KINDS[b.kind] for b in blocks]),
        array("long long", "BLOCK_MULT", [b.mult for b in blocks]),
        array("long long", "BLOCK_LIFE_LO", [b.life[0] for b in blocks]),
        array("long long", "BLOCK_LIFE_HI", [b.life[1] for b in blocks]),
        f"#define NLAYERS {len(layers)}",
        array("int", "LAYER_TOP", [layer.top for layer in layers]),
        array("int", "LAYER_RAVINE", [layer.ravine for layer in layers]),
        array("int", "LAYER_CAVES_LO", [layer.caves[0] for layer in layers]),
        array("int", "LAYER_CAVES_HI", [layer.caves[1] for layer in layers]),
        array("int", "TABLE_LEN", [len(t.ids) for t in tables]),
        array("long long", "TABLE_TOTAL", [t.cumulative[-1] for t in tables]),
    ]
    ids = ", ".join("{" + ", ".join(str(i) for i in t.ids + (0,) * (width - len(t.ids))) + "}"
                    for t in tables)  # fmt: skip
    cum = ", ".join("{" + ", ".join(str(i) for i in t.cumulative + (0,) * (width - len(t.ids)))
                    + "}" for t in tables)  # fmt: skip
    lines += [
        f"static const int TABLE_ID[NLAYERS][{width}] = {{{ids}}};",
        f"static const long long TABLE_CUM[NLAYERS][{width}] = {{{cum}}};",
    ]
    for name in ("GRAVITY", "DT", "RADIUS", "MAX_SPEED", "MIN_BOUNCE", "HIT_SPEED",
                 "WALL_BOUNCE", "FRICTION", "NOISE", "FLOOR_NORMAL", "BREAK_DAMPING",
                 "TNT_KICK", "NUDGE", "SLOP"):  # fmt: skip
        # `repr` da el float exacto: C lo lee como el mismo double.
        lines.append(f"#define {name} ({float(getattr(ph, name))!r})")
    lines += [
        f"#define STUCK_TICKS {ph.STUCK_TICKS}",
        f"#define MAX_TICKS {ph.MAX_TICKS}",
        f"#define MAX_RESOLVE {ph.MAX_RESOLVE}",
        f"#define NTNT_RADII {len(ph.TNT_RADII)}",
        array("int", "TNT_RADII", list(ph.TNT_RADII)),
    ]
    return "\n".join(lines) + "\n"


class _Result(ctypes.Structure):
    _fields_ = [
        *((name, ctypes.c_longlong) for name in (
            "ticks", "life", "max_row", "dragon", "timeout", "hits", "life_gained",
            "explosions", "longest_chain", "nudges", "stalls", "multiplier")),
        ("x", ctypes.c_double),
        ("y", ctypes.c_double),
        ("angle", ctypes.c_double),
        ("broken", ctypes.c_longlong * len(pw.BLOCKS)),
    ]  # fmt: skip


def compiler() -> str | None:
    """El compilador de C que haya (`CC`, `cc`, `gcc` o `clang`)."""
    for name in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if name and shutil.which(name):
            return name
    return None


@cache
def build() -> ctypes.CDLL:
    """Compila el motor (si no está ya en la caché) y lo carga.

    Raises:
        RuntimeError: Si no hay compilador de C o falla la compilación.
    """
    cc = compiler()
    if cc is None:
        raise RuntimeError("No hay compilador de C (cc, gcc o clang).")
    text = header()
    digest = hashlib.sha256((text + SOURCE.read_text(encoding="utf-8")).encode()).hexdigest()
    cache_dir = Path(tempfile.gettempdir()) / "picar_rapido" / digest[:16]
    library = cache_dir / ("picar_rapido.dll" if os.name == "nt" else "picar_rapido.so")
    if not library.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "picar_params.h").write_text(text, encoding="utf-8")
        partial = library.with_suffix(f".{os.getpid()}.tmp")
        result = subprocess.run(
            [cc, *FLAGS, f"-I{cache_dir}", "-o", str(partial), str(SOURCE), "-lm"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"No compila el motor rápido:\n{result.stderr}")
        os.replace(partial, library)
    lib = ctypes.CDLL(str(library))
    lib.picar_simulate.argtypes = [
        ctypes.c_longlong, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_double,
        ctypes.POINTER(_Result),
    ]  # fmt: skip
    lib.picar_simulate.restype = ctypes.c_int
    lib.picar_mt_sample.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_int]
    return lib


def simulate(seed: int, pick: ph.Pick | str) -> ph.Run:
    """Como `picar_physics.simulate(seed, pick, record=False)`, en C."""
    if isinstance(pick, str):
        pick = ph.PICKS[pick]
    result = _Result()
    if build().picar_simulate(seed, pick.life, pick.power, pick.bounce, ctypes.byref(result)):
        raise MemoryError("El motor rápido no ha podido reservar memoria.")
    run = ph.Run(seed=seed, pick=pick.key)
    run.ticks, run.life = result.ticks, result.life
    run.max_row, run.dragon, run.timeout = result.max_row, bool(result.dragon), bool(result.timeout)
    run.hits, run.life_gained, run.explosions = result.hits, result.life_gained, result.explosions
    run.longest_chain, run.nudges, run.stalls = result.longest_chain, result.nudges, result.stalls
    run.multiplier = result.multiplier
    run.broken = {pw.BLOCKS[i].key: n for i, n in enumerate(result.broken) if n}
    run.final = (result.x, result.y, result.angle)
    run.layer = "end" if run.dragon else pw.layer_at(run.max_row).key
    return run


def mt_sample(text: str, count: int = 8) -> list[int]:
    """Los primeros enteros del generador de C sembrado con `text` (para comparar)."""
    out = (ctypes.c_uint32 * count)()
    build().picar_mt_sample(text.encode(), out, count)
    return list(out)


def check(seeds: int = 200, picks: tuple[str, ...] | None = None) -> None:
    """Juega `seeds` semillas de cada pico con los dos motores; falla si algo difiere.

    Raises:
        AssertionError: Con la primera semilla en la que no coinciden.
    """
    for key in picks or tuple(ph.PICKS):
        for seed in range(seeds):
            fast = simulate(seed, key)
            slow = ph.simulate(seed, key, record=False)
            if fast.fingerprint() != slow.fingerprint() or fast.broken != slow.broken:
                raise AssertionError(
                    f"El motor rápido no coincide con el de Python: {key}, semilla {seed} "
                    f"(C: {fast.ticks} ticks, fila {fast.max_row}; "
                    f"Python: {slow.ticks} ticks, fila {slow.max_row})"
                )


if __name__ == "__main__":
    import random
    import time

    for text in ("picar:mundo:0:0", "picar:fisica:12345", "x"):
        python = random.Random(text)
        assert mt_sample(text) == [python.getrandbits(32) for _ in range(8)], text
    print("Generador: igual que Python")
    start = time.perf_counter()
    check(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
    print(f"Motores: iguales ({time.perf_counter() - start:.1f} s)")
    for key in ph.PICKS:
        t = time.perf_counter()
        for seed in range(2000):
            simulate(seed, key)
        fast = (time.perf_counter() - t) / 2000
        t = time.perf_counter()
        for seed in range(100):
            ph.simulate(seed, key, record=False)
        slow = (time.perf_counter() - t) / 100
        print(f"{key:10} C {fast * 1000:6.3f} ms  Python {slow * 1000:6.2f} ms  ×{slow / fast:.0f}")
