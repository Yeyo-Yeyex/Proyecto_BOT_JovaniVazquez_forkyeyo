"""Motor del juego del pico (`picar`): el pico cae, rebota y rompe bloques.

Lógica pura, sin Pillow ni Discord. `simulate(semilla, pico)` juega una partida entera
en el mundo de `bot.services.picar_world` y devuelve su `Run`: dónde estaba el pico en
cada tick, qué golpeó y rompió, y cuánto bajó. Los premios no se calculan aquí: la
física no conoce el valor de los minerales, y así la calibración puede cambiar valores
sin volver a simular (`docs/plan-picar.md`).

**El modelo.** El pico es un círculo de radio `RADIUS` (en bloques) con gravedad, a paso
fijo `DT`. Las paredes de los lados no se rompen. Contra un bloque:

- Si el pico se acerca a una cara (o se apoya en un suelo), es un golpe: el bloque
  pierde la potencia del pico y el pico pierde la dureza del bloque.
- Si el bloque se rompe, desaparece y el pico sigue con la mitad de la velocidad hacia
  él. Si aguanta, el pico rebota con la elasticidad del pico (`Pick.bounce`) y con un
  poco de ruido en la componente tangencial, para que dos caídas no se repitan.
- Contra un suelo, el rebote nunca baja de `MIN_BOUNCE`: el pico no puede quedarse
  quieto apoyado, así que cada vez que toca suelo es un golpe y le cuesta vida.
- La TNT, al romperse, revienta todo lo que haya a 1 o 2 bloques (otra TNT en el radio
  revienta también, en cadena) y empuja al pico. Lo que revienta cuenta como roto y no
  gasta vida.
- Un cofre roto le devuelve al pico una parte de su vida inicial (`Block.life`: entre
  el 15 y el 40 % el normal, entre el 80 y el 120 % el del tesoro). Una mesa de crafteo
  rota le devuelve toda la vida inicial; si ya tenía más, se queda como estaba. Un bloque
  de multiplicador roto multiplica el premio de la partida (`Run.multiplier`). Los
  cofres, las mesas y los multiplicadores que revienta una TNT cuentan igual.

La partida acaba cuando la vida llega a 0 o cuando el pico llega a la fila del dragón.

**Siempre termina.** Cada golpe cuesta al menos 1 de vida (no hay bloques con dureza
0) y el pico no puede apoyarse sin golpear. Entre golpe y golpe, solo puede caer, y la
caída acaba en un bloque o en el dragón. Por si acaso, `MAX_TICKS` corta la partida
(`Run.timeout`) y una prueba comprueba que no pasa nunca. Si el pico pasa
`STUCK_TICKS` sin bajar a una fila nueva, recibe un empujón al azar.

**Nunca atraviesa un bloque.** La velocidad no pasa de `MAX_SPEED`, que en un tick
avanza menos que el radio: el centro del pico no puede entrar en un bloque, y el choque
se resuelve sacándolo por la normal de la casilla que más se mete, con una holgura
(`SLOP`) para que el redondeo no lo deje rozando el borde. Al final de cada tick el pico
no se solapa con ningún bloque que quede, tampoco en el tick en que muere (lo comprueba
una prueba). Si en `MAX_RESOLVE` intentos no sale, vuelve a donde estaba al empezar el
tick (`Run.stalls`); con la holgura no pasa nunca, y una prueba lo vigila.

**Reproducible en cualquier máquina.** El bucle solo usa `+ - * /`, `sqrt`, `floor` y
`%`, que IEEE 754 da exactos y por tanto iguales en Windows y en Linux. Nada de `sin`,
`cos` ni `atan2`: vienen de la libm de cada sistema y pueden diferir en el último bit,
y en un sistema caótico eso es otra partida unos ticks después. El azar sale de un
generador propio de la partida (`Random(f"picar:fisica:{semilla}")`) y de los del
mundo; nada del `random` global ni de la hora. El giro del pico es solo de dibujo:
sale de los rebotes y no gasta azar, así que cambiar cómo gira no cambia las partidas.

Cualquier cambio en las constantes, en los picos o en el mundo cambia las partidas y
obliga a recalibrar: `params_hash` resume todos esos parámetros (y `ENGINE_VERSION`,
que se sube al cambiar la lógica de este archivo) y `Run.fingerprint` resume una
partida para comprobar que el NAS juega igual que el PC donde se calibró.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from math import floor, sqrt
from random import Random

from bot.services.picar_world import (
    AIR,
    BLOCKS,
    CHUNK,
    COLUMNS,
    DRAGON_ROW,
    LAYERS,
    MINERALS,
    RAVINE_REGION,
    RAVINE_ROWS,
    Block,
    Kind,
    World,
    layer_at,
)

#: Se sube al cambiar la lógica de `simulate` (no hace falta al tocar constantes:
#: ya entran en `params_hash`).
ENGINE_VERSION = 1

#: Ticks por segundo de simulación. El dibujo elige cuántos enseña.
TICKS_PER_SECOND = 60
DT = 1 / TICKS_PER_SECOND
#: Bloques por segundo al cuadrado.
GRAVITY = 60.0
RADIUS = 0.4
#: Velocidad máxima, en bloques por segundo. A 60 ticks por segundo son 0,22 bloques
#: por tick, menos que el radio: el pico no puede saltarse un bloque.
MAX_SPEED = 13.0
#: Velocidad mínima con la que rebota en un suelo.
MIN_BOUNCE = 5.0
#: Por debajo de esta velocidad hacia una cara que no es suelo, el roce no es golpe.
HIT_SPEED = 1.0
#: Elasticidad de las paredes de los lados.
WALL_BOUNCE = 0.6
#: Lo que conserva la componente tangencial en cada rebote.
FRICTION = 0.85
#: Ruido tangencial de cada rebote, en proporción a la velocidad del golpe.
NOISE = 1.4
#: Una normal con la componente vertical por debajo de esto (hacia arriba) es suelo.
FLOOR_NORMAL = -0.5
#: Lo que conserva el pico de la velocidad hacia un bloque que rompe.
BREAK_DAMPING = 0.5
#: Radios posibles de una TNT (en bloques) y empujón que da al pico.
TNT_RADII = (1, 2)
TNT_KICK = 9.0
#: Ticks sin bajar a una fila nueva antes del empujón, y su fuerza.
STUCK_TICKS = 600
NUDGE = 6.0
#: Corte de seguridad; una prueba comprueba que no se alcanza.
MAX_TICKS = 400_000
#: Intentos de sacar al pico de los bloques en un mismo tick.
MAX_RESOLVE = 6
#: Holgura con la que se saca al pico de un bloque. Sin ella, el redondeo deja la
#: distancia un pelo por debajo del radio y el pico sigue «tocando» el bloque.
SLOP = 1e-9


@dataclass(frozen=True)
class Pick:
    """Lo que la física sabe de un pico. Precio y premios viven en `picar`."""

    key: str
    name: str
    #: Vida inicial.
    life: int
    #: Lo que le quita a un bloque en cada golpe.
    power: int
    #: Elasticidad de sus rebotes (0 a 1).
    bounce: float


#: Vidas ajustadas el 28/10/2026 (`docs/plan-picar.md`, «Calibración»): los picos
#: pequeños por lo que dura una partida (madera ~8 s, oro ~10, piedra ~13, hierro ~18) y
#: los grandes por cuántos llegan al Nether (diamante ~25 %, netherite ~60 %).
PICKS: dict[str, Pick] = {
    pick.key: pick
    for pick in (
        Pick("wood", "Madera", 130, 1, 0.45),
        Pick("stone", "Piedra", 210, 2, 0.45),
        Pick("iron", "Hierro", 235, 3, 0.4),
        Pick("gold", "Oro", 145, 4, 0.5),
        Pick("diamond", "Diamante", 435, 4, 0.4),
        Pick("netherite", "Netherite", 590, 5, 0.35),
    )
}


@dataclass(frozen=True, slots=True)
class Event:
    """Algo que pasó en un tick.

    `kind` es uno de: `hit` (golpe a un bloque que aguanta), `break` (golpe que lo
    rompe), `blast` (bloque reventado por una TNT), `explode` (centro de una TNT),
    `chest` (vida que da un cofre, en `amount`), `craft` (vida que devuelve una mesa
    de crafteo, en `amount`), `mult` (bloque de multiplicador, por cuánto multiplica en
    `amount`), `layer` (el pico entra en una capa
    nueva, en `layer`), `nudge` (empujón por atasco), `dragon` y `death`.
    """

    tick: int
    kind: str
    row: int = 0
    col: int = 0
    block: str = ""
    amount: int = 0
    layer: str = ""


@dataclass
class Run:
    """Registro de una partida.

    `frames[i]` es el pico al final del tick `i + 1`: (x, y, ángulo en grados), en
    bloques desde la esquina de arriba a la izquierda de la fila 0. Va vacío si se
    simuló con `record=False` (la calibración solo necesita los recuentos).
    """

    seed: int
    pick: str
    ticks: int = 0
    life: int = 0
    frames: list[tuple[float, float, float]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    #: Bloques rotos por clave, a golpes o reventados.
    broken: dict[str, int] = field(default_factory=dict)
    #: Fila más honda que ha tocado el pico.
    max_row: int = 0
    layer: str = ""
    dragon: bool = False
    timeout: bool = False
    hits: int = 0
    life_gained: int = 0
    explosions: int = 0
    #: TNT de la cadena más larga (una TNT sola es una cadena de 1).
    longest_chain: int = 0
    #: Producto de los bloques de multiplicador rotos.
    multiplier: int = 1
    nudges: int = 0
    #: Ticks en que no salió de los bloques y volvió a donde estaba (debería ser 0).
    stalls: int = 0
    #: Pico al acabar: (x, y, ángulo), también con `record=False`.
    final: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def minerals(self) -> dict[str, int]:
        """Minerales rotos por clave, solo los que han salido."""
        return {key: self.broken[key] for key in MINERALS if self.broken.get(key)}

    def fingerprint(self) -> str:
        """Resumen de la partida, para comparar dos simulaciones bit a bit.

        Entra la posición final con `repr` (que reproduce el float exacto), así que
        un solo bit distinto en la física cambia la huella.
        """
        data = [
            self.seed,
            self.pick,
            self.ticks,
            self.life,
            sorted(self.broken.items()),
            self.max_row,
            self.dragon,
            self.hits,
            self.multiplier,
            [repr(v) for v in self.final],
        ]
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()


def params_hash() -> str:
    """Hash de todo lo que decide una partida: constantes, picos, bloques y capas."""
    constants = {
        name: repr(globals()[name])
        for name in (
            "ENGINE_VERSION",
            "TICKS_PER_SECOND",
            "GRAVITY",
            "RADIUS",
            "MAX_SPEED",
            "MIN_BOUNCE",
            "HIT_SPEED",
            "WALL_BOUNCE",
            "FRICTION",
            "NOISE",
            "FLOOR_NORMAL",
            "BREAK_DAMPING",
            "TNT_RADII",
            "TNT_KICK",
            "STUCK_TICKS",
            "NUDGE",
            "MAX_TICKS",
            "MAX_RESOLVE",
            "SLOP",
        )
    }
    data = {
        "constants": constants,
        "picks": [
            [p.key, p.life, p.power, repr(p.bounce)]
            for p in sorted(PICKS.values(), key=lambda p: p.key)
        ],
        "blocks": [
            [b.key, b.hardness, b.resistance, b.mineral, b.kind.value, list(b.life), b.mult]
            for b in BLOCKS
        ],
        "layers": [{**asdict(layer), "dimension": layer.dimension.value} for layer in LAYERS],
        "world": [COLUMNS, DRAGON_ROW, CHUNK, RAVINE_REGION, list(RAVINE_ROWS)],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _deepest(world: World, x: float, y: float) -> tuple[float, int, int, float, float]:
    """Casilla sólida que más se mete en el pico: (penetración, fila, columna, nx, ny).

    Penetración 0 si no toca ninguna. La normal apunta del bloque al pico. A igual
    penetración gana la primera por fila y columna, para que el orden sea fijo.
    """
    best = (0.0, 0, 0, 0.0, 0.0)
    r0, r1 = floor(y - RADIUS), floor(y + RADIUS)
    c0, c1 = max(0, floor(x - RADIUS)), min(COLUMNS - 1, floor(x + RADIUS))
    for r in range(max(0, r0), r1 + 1):
        cells = world.row(r)
        for c in range(c0, c1 + 1):
            if cells[c] == AIR:
                continue
            px = c if x < c else (c + 1 if x > c + 1 else x)
            py = r if y < r else (r + 1 if y > r + 1 else y)
            dx, dy = x - px, y - py
            d2 = dx * dx + dy * dy
            if d2 >= RADIUS * RADIUS:
                continue
            d = sqrt(d2)
            pen = RADIUS - d
            if pen > best[0]:
                # d > 0: el centro nunca entra en un bloque (ver la docstring).
                best = (pen, r, c, dx / d, dy / d)
    return best


def _explode(
    world: World, run: Run, tick: int, row: int, col: int, rng: Random, record: bool
) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, str]]]:
    """Revienta una TNT ya quitada del mundo y las que pille, en cadena.

    Devuelve los centros con su radio (para empujar al pico) y los bloques reventados
    que hacen algo (cofres, mesas y multiplicadores: fila, columna, bloque), que aplica
    quien llama. Los bloques reventados cuentan como rotos y no gastan vida del pico.
    """
    centres: list[tuple[int, int, int]] = []
    rewards: list[tuple[int, int, Block]] = []
    queue = [(row, col)]
    while queue:
        r0, c0 = queue.pop(0)
        radius = TNT_RADII[rng.randrange(len(TNT_RADII))]
        centres.append((r0, c0, radius))
        if record:
            run.events.append(Event(tick, "explode", r0, c0, "tnt", radius))
        for r in range(max(0, r0 - radius), min(DRAGON_ROW - 1, r0 + radius) + 1):
            cells = world.row(r)
            for c in range(max(0, c0 - radius), min(COLUMNS - 1, c0 + radius) + 1):
                b = cells[c]
                dr, dc = r - r0, c - c0
                if b == AIR or dr * dr + dc * dc > radius * radius:
                    continue
                block = BLOCKS[b]
                world.remove(r, c)
                run.broken[block.key] = run.broken.get(block.key, 0) + 1
                if record:
                    run.events.append(Event(tick, "blast", r, c, block.key))
                if block.kind is Kind.TNT:
                    queue.append((r, c))
                elif block.kind is not Kind.PLAIN:
                    rewards.append((r, c, block))
    return centres, rewards


def simulate(seed: int, pick: Pick | str, *, record: bool = True) -> Run:
    """Juega la partida de `seed` con `pick` y devuelve su registro.

    Con `record=False` no guarda fotogramas ni eventos (la calibración simula
    millones y solo quiere los recuentos); el resto del registro sale idéntico.
    """
    if isinstance(pick, str):
        pick = PICKS[pick]
    world = World(seed)
    rng = Random(f"picar:fisica:{seed}")
    run = Run(seed=seed, pick=pick.key)

    life = pick.life
    power, bounce = pick.power, pick.bounce
    x = COLUMNS / 2 + rng.uniform(-0.3, 0.3)
    y = -2.0
    vx, vy = rng.uniform(-1.5, 1.5), 0.0
    angle, spin = 0.0, 0.0
    gravity_step = GRAVITY * DT
    max_row, last_progress = -1, 0
    layer = ""
    frames, events = run.frames, run.events

    def reward(block: Block, current: int) -> int:
        """Vida que gana el pico al romper un cofre o una mesa con `current` de vida."""
        if block.kind is Kind.CRAFT:
            return max(0, pick.life - current)
        return pick.life * rng.randint(*block.life) // 100

    tick = 0
    while True:
        tick += 1
        if tick > MAX_TICKS:
            run.timeout = True
            tick -= 1
            break

        vy += gravity_step
        speed2 = vx * vx + vy * vy
        if speed2 > MAX_SPEED * MAX_SPEED:
            f = MAX_SPEED / sqrt(speed2)
            vx, vy = vx * f, vy * f
        px, py = x, y
        x += vx * DT
        y += vy * DT

        if x < RADIUS:
            x, vx = RADIUS, -vx * WALL_BOUNCE
        elif x > COLUMNS - RADIUS:
            x, vx = COLUMNS - RADIUS, -vx * WALL_BOUNCE

        if y + RADIUS >= DRAGON_ROW:
            y = DRAGON_ROW - RADIUS
            run.dragon = True
            max_row = DRAGON_ROW
            if record:
                frames.append((x, y, angle))
                events.append(Event(tick, "dragon", DRAGON_ROW, floor(x)))
            break

        dead = False
        for _ in range(MAX_RESOLVE):
            pen, r, c, nx, ny = _deepest(world, x, y)
            if pen == 0.0:
                break
            vn = vx * nx + vy * ny
            if vn >= 0.0 or dead:
                # Ya se separa, o ya ha muerto y solo falta dejarlo fuera de los
                # bloques para el último fotograma: solo se le saca.
                x += nx * (pen + SLOP)
                y += ny * (pen + SLOP)
                continue
            floor_hit = ny < FLOOR_NORMAL
            if not floor_hit and -vn < HIT_SPEED:
                x += nx * (pen + SLOP)
                y += ny * (pen + SLOP)
                vx -= vn * nx
                vy -= vn * ny
                continue

            # Golpe.
            run.hits += 1
            cells, hps = world.row(r), world.hp(r)
            block = BLOCKS[cells[c]]
            hps[c] -= power
            life -= block.hardness
            tx, ty = -ny, nx
            vt = vx * tx + vy * ty
            spin = spin * 0.5 + vt / RADIUS * 0.5
            if hps[c] <= 0:
                world.remove(r, c)
                run.broken[block.key] = run.broken.get(block.key, 0) + 1
                if record:
                    events.append(Event(tick, "break", r, c, block.key))
                # Sigue hacia donde iba, frenado.
                vx -= vn * nx * (1 - BREAK_DAMPING)
                vy -= vn * ny * (1 - BREAK_DAMPING)
                centres: list[tuple[int, int, int]] = []
                rewards: list[tuple[int, int, Block]] = []
                if block.kind is Kind.TNT:
                    centres, rewards = _explode(world, run, tick, r, c, rng, record)
                elif block.kind is not Kind.PLAIN:
                    rewards = [(r, c, block)]
                for cr, cc, prize in rewards:
                    if prize.kind is Kind.MULT:
                        run.multiplier *= prize.mult
                        if record:
                            events.append(Event(tick, "mult", cr, cc, prize.key, prize.mult))
                        continue
                    gained = reward(prize, life)
                    life += gained
                    run.life_gained += gained
                    if record:
                        events.append(Event(tick, prize.kind.value, cr, cc, prize.key, gained))
                if centres:
                    run.explosions += len(centres)
                    run.longest_chain = max(run.longest_chain, len(centres))
                    for cr, cc, radius in centres:
                        dx, dy = x - (cc + 0.5), y - (cr + 0.5)
                        d2 = dx * dx + dy * dy
                        reach = radius + 1.0
                        if 0.0 < d2 < reach * reach:
                            d = sqrt(d2)
                            push = TNT_KICK * (1 - d / reach)
                            vx += dx / d * push
                            vy += dy / d * push - push * 0.5
            else:
                if record:
                    events.append(Event(tick, "hit", r, c, block.key))
                out = -vn * bounce
                if floor_hit and out < MIN_BOUNCE:
                    out = MIN_BOUNCE
                vt = vt * FRICTION + rng.uniform(-NOISE, NOISE) * -vn
                vx = nx * out + tx * vt
                vy = ny * out + ty * vt
                x += nx * (pen + SLOP)
                y += ny * (pen + SLOP)
            if life <= 0:
                dead = True
        else:
            # No ha salido en `MAX_RESOLVE` intentos: vuelve a donde estaba, que no
            # tocaba nada (los bloques solo desaparecen), y se queda quieto.
            if _deepest(world, x, y)[0] > 0.0:
                x, y, vx, vy = px, py, 0.0, 0.0
                run.stalls += 1

        angle = (angle + spin * DT * 57.29577951308232) % 360.0
        row = floor(y)
        if row > max_row:
            max_row, last_progress = row, tick
            current = layer_at(max(0, row)).key
            if current != layer:
                layer = current
                if record:
                    events.append(Event(tick, "layer", row, floor(x), layer=layer))
        elif tick - last_progress >= STUCK_TICKS:
            last_progress = tick
            run.nudges += 1
            vx += NUDGE if rng.random() < 0.5 else -NUDGE
            vy -= NUDGE
            if record:
                events.append(Event(tick, "nudge", row, floor(x)))
        if record:
            frames.append((x, y, angle))
        if dead:
            if record:
                events.append(Event(tick, "death", floor(y), floor(x)))
            break

    run.ticks = tick
    run.life = life
    run.final = (x, y, angle)
    run.max_row = max(0, max_row)
    run.layer = layer_at(run.max_row).key if not run.dragon else "end"
    return run
