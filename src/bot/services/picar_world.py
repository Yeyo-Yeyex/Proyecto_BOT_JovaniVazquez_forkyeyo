"""Mundo del juego del pico (`picar`): los bloques, las capas y cómo se generan.

Lógica pura, sin Pillow ni Discord. El mundo es una cuadrícula de `COLUMNS` columnas
y filas sin fin hacia abajo (la fila 0 es la superficie; las negativas, el cielo). En
la fila `DRAGON_ROW` espera el dragón: llegar hasta ella acaba la partida.

**El mundo solo depende de la semilla.** Se genera por trozos de `CHUNK` filas y cada
trozo tiene su propio generador (`Random(f"picar:mundo:{semilla}:{trozo}")`), así que
da igual en qué orden los pida la física o si el dibujo vuelve a generarlos desde cero:
la misma semilla da siempre el mismo mundo, y el mismo para todos los picos. Solo se
generan los trozos que el pico llega a tocar. Los generadores con texto como semilla
pasan por SHA-512 y dan lo mismo en cualquier máquina; el sorteo de cada casilla usa
`randrange` sobre pesos enteros, sin coma flotante.

Cada bloque (`Block`) tiene su dureza (la vida que le quita al pico cada golpe), su
resistencia (lo que aguanta antes de romperse: cada golpe le quita la potencia del
pico) y si es mineral. **El valor de los minerales no vive aquí**: la física no puede
depender de él (ver `docs/plan-picar.md`), y los premios salen de la calibración.

Las capas (`Layer`) fijan qué sale en cada franja de filas, cuántas cuevas hay y
cuántos barrancos. Las cuevas son huecos en forma de elipse dentro de un trozo y dan
ritmo a la caída. Los bloques de multiplicador (oro ×2, diamante ×3, netherite ×5 y la
baliza ×10, `Kind.MULT`) multiplican todo el premio y se multiplican entre sí: son lo que
da colas largas a los premios. Los barrancos (`Ravine`) son grietas de 1 o 2 columnas y de 20 a 60
filas por las que el pico cae libre: junto con los cofres del tesoro y las mesas de
crafteo, son lo que de vez en cuando deja a un pico bajar mucho más de lo normal (las
colas largas de las metas de profundidad, `docs/plan-picar.md`). Un barranco cruza
trozos, así que no puede salir del generador de un trozo: cada región de
`RAVINE_REGION` filas tiene su propio generador y decide si empieza uno en ella. Como
ninguno mide más que una región, en un trozo solo entran los de su región y los de la
anterior.

Las cifras son de partida: cualquier cambio en bloques, capas, cuevas o barrancos
cambia las partidas y obliga a recalibrar (`picar_physics.params_hash` lo detecta).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from itertools import accumulate
from random import Random

#: Ancho del mundo, en bloques.
COLUMNS = 9
#: Filas por trozo de generación.
CHUNK = 16
#: Fila del dragón: el pico que llega hasta aquí lo mata y la partida acaba.
DRAGON_ROW = 700
#: Filas por región de barrancos y alto de un barranco (ambos incluidos). El alto
#: máximo no puede pasar de la región (ver la docstring del módulo).
RAVINE_REGION = 64
RAVINE_ROWS = (20, 60)


class Kind(Enum):
    """Qué hace un bloque al romperse, aparte de desaparecer."""

    PLAIN = "plain"
    #: Revienta los bloques de alrededor (`picar_physics`).
    TNT = "tnt"
    #: Le devuelve al pico una parte de su vida inicial (`Block.life`).
    CHEST = "chest"
    #: Mesa de crafteo: le devuelve al pico toda su vida inicial.
    CRAFT = "craft"
    #: Multiplica todo el premio de la partida por `Block.mult`.
    MULT = "mult"


class Dimension(Enum):
    """Las tres dimensiones; cada una será un GIF propio al dibujar la partida."""

    OVERWORLD = "overworld"
    NETHER = "nether"
    END = "end"


@dataclass(frozen=True)
class Block:
    """Un tipo de bloque. Su `id` es su posición en `BLOCKS` (0 es el aire)."""

    key: str
    name: str
    #: Vida que le quita al pico cada golpe.
    hardness: int
    #: Lo que aguanta: cada golpe le resta la potencia del pico.
    resistance: int
    mineral: bool = False
    kind: Kind = Kind.PLAIN
    #: Solo cofres: vida que dan, en tanto por ciento de la vida inicial del pico
    #: (un número al azar entre los dos, ambos incluidos).
    life: tuple[int, int] = (0, 0)
    #: Solo bloques de multiplicador: por cuánto multiplican el premio.
    mult: int = 1


BLOCKS: tuple[Block, ...] = (
    Block("air", "Aire", 0, 0),
    # Superficie y cuevas.
    Block("dirt", "Tierra", 1, 2),
    Block("stone", "Piedra", 2, 4),
    Block("coal", "Carbón", 2, 4, mineral=True),
    Block("iron", "Hierro", 3, 6, mineral=True),
    Block("gold", "Oro", 3, 6, mineral=True),
    Block("redstone", "Redstone", 3, 6, mineral=True),
    Block("lapis", "Lapislázuli", 3, 6, mineral=True),
    Block("tnt", "TNT", 1, 1, kind=Kind.TNT),
    Block("chest", "Cofre", 1, 3, kind=Kind.CHEST, life=(15, 40)),
    Block("treasure", "Cofre del tesoro", 1, 4, kind=Kind.CHEST, life=(80, 120)),
    Block("crafting_table", "Mesa de crafteo", 1, 3, kind=Kind.CRAFT),
    Block("gold_block", "Bloque de oro", 2, 6, kind=Kind.MULT, mult=2),
    Block("diamond_block", "Bloque de diamante", 3, 8, kind=Kind.MULT, mult=3),
    Block("netherite_block", "Bloque de netherite", 4, 10, kind=Kind.MULT, mult=5),
    Block("beacon", "Baliza", 2, 6, kind=Kind.MULT, mult=10),
    # Profundidad.
    Block("deepslate", "Pizarra profunda", 3, 7),
    Block("diamond", "Diamante", 5, 10, mineral=True),
    Block("emerald", "Esmeralda", 5, 10, mineral=True),
    # Nether.
    Block("netherrack", "Netherrack", 1, 2),
    Block("blackstone", "Piedra negra", 3, 6),
    Block("quartz", "Cuarzo", 2, 4, mineral=True),
    Block("nether_gold", "Oro del Nether", 2, 4, mineral=True),
    Block("ancient_debris", "Escombros ancestrales", 8, 20, mineral=True),
    # End.
    Block("end_stone", "Piedra del End", 4, 9),
    Block("obsidian", "Obsidiana", 8, 30),
    Block("end_crystal", "Cristal del End", 6, 14, mineral=True),
    Block("shulker", "Caja de shulker", 1, 3, kind=Kind.CHEST, life=(15, 40)),
)

AIR = 0
BLOCK_ID: dict[str, int] = {block.key: i for i, block in enumerate(BLOCKS)}
#: Claves de los minerales, en el orden de `BLOCKS`.
MINERALS: tuple[str, ...] = tuple(block.key for block in BLOCKS if block.mineral)


@dataclass(frozen=True)
class Layer:
    """Una franja de filas: desde `top` hasta el `top` de la siguiente capa."""

    key: str
    name: str
    top: int
    dimension: Dimension
    #: Pesos enteros de cada bloque (clave, peso).
    weights: tuple[tuple[str, int], ...]
    #: Cuevas por trozo: un número al azar entre los dos, ambos incluidos.
    caves: tuple[int, int] = (0, 0)
    #: Probabilidad, en tanto por mil, de que empiece un barranco en una región cuya
    #: fila central caiga en esta capa.
    ravine: int = 0


LAYERS: tuple[Layer, ...] = (
    Layer(
        "soil",
        "Superficie",
        0,
        Dimension.OVERWORLD,
        (("dirt", 85), ("stone", 10), ("coal", 4), ("chest", 1)),
    ),
    Layer(
        "caves",
        "Cuevas",
        4,
        Dimension.OVERWORLD,
        (
            ("stone", 700),
            ("dirt", 50),
            ("coal", 90),
            ("iron", 60),
            ("gold", 15),
            ("redstone", 35),
            ("lapis", 15),
            ("tnt", 20),
            ("chest", 8),
            ("treasure", 2),
            ("crafting_table", 4),
            ("gold_block", 3),
        ),
        caves=(1, 3),
        ravine=300,
    ),
    Layer(
        "deep",
        "Profundidad",
        120,
        Dimension.OVERWORLD,
        (
            ("deepslate", 700),
            ("iron", 40),
            ("gold", 25),
            ("redstone", 50),
            ("lapis", 25),
            ("diamond", 15),
            ("emerald", 5),
            ("tnt", 25),
            ("chest", 10),
            ("treasure", 2),
            ("crafting_table", 3),
            ("gold_block", 2),
            ("diamond_block", 2),
        ),
        caves=(1, 2),
        ravine=300,
    ),
    Layer(
        "nether",
        "Nether",
        250,
        Dimension.NETHER,
        (
            ("netherrack", 550),
            ("blackstone", 300),
            ("quartz", 80),
            ("nether_gold", 60),
            ("ancient_debris", 6),
            ("tnt", 10),
            ("netherite_block", 2),
        ),
        caves=(1, 2),
        ravine=120,
    ),
    Layer(
        "end",
        "End",
        500,
        Dimension.END,
        (
            ("end_stone", 880),
            ("obsidian", 80),
            ("end_crystal", 8),
            ("shulker", 2),
            ("beacon", 1),
        ),
        caves=(0, 1),
    ),
)

_TOPS = [layer.top for layer in LAYERS]


def layer_at(row: int) -> Layer:
    """Capa a la que pertenece una fila (la primera para el cielo)."""
    return LAYERS[max(0, bisect_right(_TOPS, row) - 1)]


@dataclass(frozen=True)
class _Table:
    """Pesos de una capa listos para sortear con un solo `randrange`."""

    ids: tuple[int, ...]
    cumulative: tuple[int, ...]

    @classmethod
    def of(cls, layer: Layer) -> _Table:
        ids = tuple(BLOCK_ID[key] for key, _ in layer.weights)
        return cls(ids, tuple(accumulate(weight for _, weight in layer.weights)))

    def draw(self, rng: Random) -> int:
        return self.ids[bisect_right(self.cumulative, rng.randrange(self.cumulative[-1]))]


_TABLES = {layer.key: _Table.of(layer) for layer in LAYERS}


@dataclass(frozen=True)
class Ravine:
    """Un barranco: desde la fila `top`, una franja de aire por fila.

    `cols[i]` es la primera columna de la franja en la fila `top + i` y `width`, cuántas
    columnas mide.
    """

    top: int
    width: int
    cols: tuple[int, ...]

    def rows(self) -> range:
        return range(self.top, self.top + len(self.cols))


@lru_cache(maxsize=512)
def ravines(seed: int, region: int) -> tuple[Ravine, ...]:
    """Barrancos que empiezan en una región (ninguno o uno).

    Empiezan por debajo de la fila 0 (la superficie no se abre) y acaban antes del
    dragón. La franja se desplaza una columna a un lado o al otro en una de cada tres
    filas, sin salirse del mundo.
    """
    first = region * RAVINE_REGION
    if first < 0 or first >= DRAGON_ROW:
        return ()
    rng = Random(f"picar:barranco:{seed}:{region}")
    if rng.randrange(1000) >= layer_at(first + RAVINE_REGION // 2).ravine:
        return ()
    top = max(1, first + rng.randrange(RAVINE_REGION))
    height = min(rng.randint(*RAVINE_ROWS), DRAGON_ROW - top)
    width = rng.randint(1, 2)
    col = rng.randrange(COLUMNS - width + 1)
    cols = []
    for _ in range(height):
        cols.append(col)
        step = rng.randrange(6)
        if step == 0 and col > 0:
            col -= 1
        elif step == 1 and col < COLUMNS - width:
            col += 1
    return (Ravine(top, width, tuple(cols)),) if cols else ()


def generate_chunk(seed: int, chunk: int) -> list[list[int]]:
    """Bloques de las filas `chunk * CHUNK` a `(chunk + 1) * CHUNK - 1`, sin romper.

    Primero se sortea cada casilla con los pesos de su capa, luego se excavan las cuevas
    de la capa de la primera fila del trozo (elipses de 1 a 3 bloques de radio
    horizontal y de 1 a 2 de vertical, que no salen del trozo) y al final los trozos de
    barranco que caigan en él. Las filas del dragón y las de más abajo son aire (el
    dragón no es un bloque).
    """
    rng = Random(f"picar:mundo:{seed}:{chunk}")
    first = chunk * CHUNK
    rows: list[list[int]] = []
    for row in range(first, first + CHUNK):
        if row < 0 or row >= DRAGON_ROW:
            rows.append([AIR] * COLUMNS)
            continue
        table = _TABLES[layer_at(row).key]
        rows.append([table.draw(rng) for _ in range(COLUMNS)])
    if first < 0 or first >= DRAGON_ROW:
        return rows
    low, high = layer_at(first).caves
    for _ in range(rng.randint(low, high) if high else 0):
        cx, cy = rng.randrange(COLUMNS), rng.randrange(CHUNK)
        rx, ry = rng.randint(1, 3), rng.randint(1, 2)
        limit = rx * rx * ry * ry
        for dy in range(-ry, ry + 1):
            r = cy + dy
            if not 0 <= r < CHUNK:
                continue
            for dx in range(-rx, rx + 1):
                c = cx + dx
                if 0 <= c < COLUMNS and dx * dx * ry * ry + dy * dy * rx * rx <= limit:
                    rows[r][c] = AIR
    region = first // RAVINE_REGION
    for ravine in ravines(seed, region - 1) + ravines(seed, region):
        for i, col in enumerate(ravine.cols):
            r = ravine.top + i - first
            if 0 <= r < CHUNK:
                for c in range(col, col + ravine.width):
                    rows[r][c] = AIR
    return rows


class World:
    """El mundo de una partida: los bloques que quedan y lo que aguanta cada uno.

    Los trozos se generan la primera vez que se pide una de sus filas. `row(r)`
    devuelve la lista de la fila (mutable y compartida, para que la física no la copie
    en cada tick) y `hp(r)` lo que le queda a cada casilla de esa fila.
    """

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._rows: dict[int, list[int]] = {}
        self._hp: dict[int, list[int]] = {}

    def _load(self, row: int) -> None:
        chunk = row // CHUNK
        for i, cells in enumerate(generate_chunk(self.seed, chunk)):
            r = chunk * CHUNK + i
            self._rows[r] = cells
            self._hp[r] = [BLOCKS[b].resistance for b in cells]

    def row(self, row: int) -> list[int]:
        cells = self._rows.get(row)
        if cells is None:
            self._load(row)
            cells = self._rows[row]
        return cells

    def hp(self, row: int) -> list[int]:
        if row not in self._hp:
            self._load(row)
        return self._hp[row]

    def block(self, row: int, col: int) -> int:
        """Id del bloque de una casilla; fuera de las columnas, aire."""
        if not 0 <= col < COLUMNS:
            return AIR
        return self.row(row)[col]

    def remove(self, row: int, col: int) -> None:
        self.row(row)[col] = AIR
        self.hp(row)[col] = 0
