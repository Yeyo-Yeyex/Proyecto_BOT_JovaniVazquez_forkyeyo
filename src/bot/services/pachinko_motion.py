"""Movimiento de una tanda de pachinko: las diez bolas cayendo a la vez y chocando entre sí.

Lógica pura, sin Pillow ni Discord. Une dos cosas:

- el sorteo de `bot.services.pachinko`, que decide el bolsillo de cada bola
  (cara o cruz por fila) y con él lo que paga la tanda, y
- la física de `bot.services.pachinko_physics`, que sabe simular varias bolas
  a la vez con choques bola contra bola.

**La lógica decide y la física obedece.** Con choques, la caída de una bola
depende de las demás, así que ya no vale una caída suelta de la biblioteca
(`pachinko_physics.library`, simuladas de antemano sin otras bolas). Lo que hace
`motion_for` es buscar, en tiempo de juego, una tanda entera en la que las diez
bolas, chocando de verdad, acaben cada una en el bolsillo que le tocó. Nada
está trucado: el resultado es una simulación completa, sin empujones, y solo se
elige entre condiciones iniciales (la entrada de cada bola).

Cómo se busca, bola a bola y en el orden en que se lanzan:

1. Se parte de una instantánea del sistema en el fotograma de lanzamiento de la
   bola `k`: las anteriores ya vuelan y no dependen de ella (aún no existe).
2. Se prueban como entrada de `k` las salidas (`Start`) de la biblioteca para
   su bolsillo, empezando por la que elegía `Ball.trajectory` y siguiendo en
   orden circular, hasta `MAX_TRIES`. Con cada una se simula el sistema entero
   (la instantánea más la bola nueva) hasta que entran todas las bolas que
   están en el aire. Se acepta la primera con la que todas entran en su
   bolsillo, sin atascarse ni salirse. Que una bola ya lanzada cambie de
   bolsillo por culpa de la nueva se descarta igual que si fallara la nueva.
3. Si ninguna sirve, `k` sale `DELAY_FRAMES` fotogramas más tarde (con el
   sistema avanzado hasta ese instante) y se repite. **La búsqueda termina
   siempre**: cada retraso deja menos bolas en el aire, y cuando no queda
   ninguna, la bola `k` vuela sola. Una bola sola no choca con nada, así que su
   caída es exactamente la de la biblioteca (`simulate` y `advance` hacen la
   misma cuenta), que acaba en su bolsillo por construcción. Ese caso ni se
   simula: se toma la caída de la biblioteca tal cual.
4. Los lanzamientos son siempre en fotogramas enteros, así los puntos de cada
   bola se toman en los mismos instantes que los fotogramas del GIF.

El resultado es un `VolleyMotion`: para cada bola, el fotograma en que sale,
sus puntos fotograma a fotograma, el fotograma en que entra, los rebotes
contra clavos y los choques contra otras bolas. Es determinista (la misma
tanda da siempre el mismo movimiento, en cualquier máquina, porque la física
solo usa `+ - * /` y `sqrt`) y lo usan el dibujo
(`bot.services.pachinko_render`) y los logros (`pachinko_stats`), por eso
`motion_for` guarda las últimas `MOTION_CACHE` tandas (una caché acotada: una
entrada son diez bolas de unas 30 posiciones).

Siempre se llama fuera del event loop (`asyncio.to_thread` en el cog). El coste
medido está en la docstring de `motion_for`.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from bot.services.pachinko import Ball, Board, Volley
from bot.services.pachinko_physics import (
    STEPS_PER_FRAME,
    Body,
    Start,
    advance,
    geometry_for,
    library,
)

#: Fotogramas entre el lanzamiento de una bola y el de la siguiente.
LAUNCH_GAP = 3
#: Salidas de la biblioteca que se prueban para cada bola antes de retrasarla.
MAX_TRIES = 8
#: Fotogramas que se retrasa una bola cuando ninguna de las `MAX_TRIES` sirve.
DELAY_FRAMES = 3
#: Tandas que guarda `motion_for` (el dibujo y los logros piden la misma tanda).
MOTION_CACHE = 64


@dataclass(frozen=True, slots=True)
class BallMotion:
    """Lo que hace una bola en la tanda: de que sale a que entra en su bolsillo.

    Attributes:
        pocket: Bolsillo en el que entra (el sorteado).
        start: Condición inicial con la que se lanzó.
        launch: Fotograma de la tanda en que aparece.
        points: Posición `(x, y)` en cada fotograma desde `launch`, a 0,1 px.
            El último es la bola ya dentro del bolsillo.
        landing: Primer fotograma en que la bola ya no se ve
            (`launch + len(points)`).
        bounces: Golpes contra clavos a más de `BOUNCE_MIN_SPEED`.
        collisions: Choques contra otras bolas (cada choque cuenta para las dos).
        rejected: Salidas probadas y descartadas antes de la buena, contando
            las de los retrasos (0 si valió la primera).
        delay: Fotogramas que se retrasó el lanzamiento respecto al hueco normal.
    """

    pocket: int
    start: Start
    launch: int
    points: tuple[tuple[float, float], ...]
    landing: int
    bounces: int
    collisions: int
    rejected: int = 0
    delay: int = 0

    @property
    def frames(self) -> int:
        """Fotogramas que la bola está a la vista."""
        return len(self.points)

    def position(self, frame: float) -> tuple[float, float] | None:
        """Dónde está la bola en el fotograma `frame` de la tanda.

        Devuelve `None` antes de lanzarla y desde que entra en el bolsillo.
        Entre dos fotogramas enteros interpola en línea recta entre los puntos.
        """
        relative = frame - self.launch
        if relative < 0 or relative >= len(self.points):
            return None
        index = int(relative)
        if index >= len(self.points) - 1:
            return self.points[-1]
        t = relative - index
        (x0, y0), (x1, y1) = self.points[index], self.points[index + 1]
        return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t


@dataclass(frozen=True, slots=True)
class VolleyMotion:
    """El movimiento de las bolas de una tanda, en el orden en que se lanzan.

    Attributes:
        balls: Una `BallMotion` por bola de la tanda.
    """

    balls: tuple[BallMotion, ...]

    @property
    def collisions(self) -> int:
        """Choques entre bolas de toda la tanda (cada choque, una vez)."""
        return sum(ball.collisions for ball in self.balls) // 2

    @property
    def bounces(self) -> int:
        """Golpes contra clavos de toda la tanda."""
        return sum(ball.bounces for ball in self.balls)

    @property
    def frames(self) -> int:
        """Fotogramas hasta que entra la última bola."""
        return max((ball.landing for ball in self.balls), default=0)

    @property
    def delayed(self) -> int:
        """Bolas que han salido más tarde de lo normal."""
        return sum(1 for ball in self.balls if ball.delay)


def _library_start(ball_falls: list, first: int, offset: int) -> Start:
    """Salida número `offset` a partir de `first`, en orden circular por la biblioteca."""
    return ball_falls[(first + offset) % len(ball_falls)].start


@functools.lru_cache(maxsize=MOTION_CACHE)
def _plan(board: Board, picks: tuple[tuple[int, int], ...]) -> VolleyMotion:
    """Busca el movimiento de una tanda; `picks` es `(bolsillo, caída preferida)` por bola.

    Raises:
        RuntimeError: Si la física no reproduce una caída de la biblioteca
            (señal de que la biblioteca está vieja; ver `pachinko_physics`).
    """
    geometry = geometry_for(board)
    falls = library(board)
    finished: dict[int, Body] = {}
    #: Bolas en el aire en el instante `step` (la instantánea del sistema).
    active: list[Body] = []
    step = 0
    frame = 0
    notes: dict[int, tuple[int, int]] = {}
    tail: list[Body] | None = None
    for ident, (pocket, first) in enumerate(picks):
        ball_falls = falls[pocket]
        rejected = 0
        delay = 0
        while True:
            launch_step = (frame + delay) * STEPS_PER_FRAME
            if launch_step > step:
                # Sin la bola nueva, el sistema sigue exactamente su curso ya validado.
                entered = advance(geometry, active, step, launch_step)
                if entered is None:
                    raise RuntimeError("La física no reproduce un sistema ya validado.")
                step = launch_step
                finished.update((body.ident, body) for body in entered)
            tail = None
            if not active:
                start = _library_start(ball_falls, first, 0)
                break
            accepted: Start | None = None
            for offset in range(MAX_TRIES):
                start = _library_start(ball_falls, first, offset)
                trial = [body.copy() for body in active]
                trial.append(Body.spawn(ident, pocket, start, launch_step))
                entered = advance(geometry, trial, launch_step)
                if entered is not None:
                    accepted, tail = start, entered
                    break
                rejected += 1
            if accepted is not None:
                start = accepted
                break
            delay += DELAY_FRAMES
        active.append(Body.spawn(ident, pocket, start, launch_step))
        notes[ident] = (rejected, delay)
        frame += LAUNCH_GAP + delay
    if tail is None:
        tail = advance(geometry, active, step)
        if tail is None:
            raise RuntimeError("La física no reproduce una caída de la biblioteca.")
    finished.update((body.ident, body) for body in tail)

    balls = []
    for ident in range(len(picks)):
        body = finished[ident]
        launch = body.launch // STEPS_PER_FRAME
        rejected, delay = notes[ident]
        balls.append(
            BallMotion(
                pocket=body.target,
                start=body.start,
                launch=launch,
                points=tuple(body.points),
                landing=launch + len(body.points),
                bounces=body.bounces,
                collisions=body.hits,
                rejected=rejected,
                delay=delay,
            )
        )
    return VolleyMotion(tuple(balls))


def motion_for(volley: Volley) -> VolleyMotion:
    """El movimiento de `volley`: cada bola, chocando con las demás, acaba en su bolsillo.

    Determinista y guardado en una caché de `MOTION_CACHE` tandas, así que el
    dibujo y los logros comparten el cálculo. Es trabajo de CPU: llamarlo con
    `asyncio.to_thread`.

    Coste, medido con 40 tandas por tablero (semillas fijas) en el equipo de
    desarrollo: mediana de 75 a 83 ms, el 10 % más lento entre 110 y 146 ms y
    máximo entre 135 y 203 ms. Del 33 al 43 % de las bolas necesitan más de una
    salida (las que chocan suelen acabar en otro bolsillo y se descartan); solo
    el 1 % sale con retraso, y nunca más de 6 fotogramas en 42.000 tandas. Salen
    0,62 choques por tanda (el 39 % de las tandas tiene alguno). Ver
    `docs/auditoria-logros.md`, «Pachinko: los choques».

    Args:
        volley: La tanda. Solo cuentan el tablero y, de cada bola, su bolsillo
            y su caída preferida (`Ball.trajectory`); el sorteo no interviene.
    """
    return _plan(volley.board, picks_of(volley.balls))


def picks_of(balls: tuple[Ball, ...]) -> tuple[tuple[int, int], ...]:
    """`(bolsillo, caída preferida)` de cada bola: la clave de la caché."""
    return tuple((ball.pocket, ball.trajectory) for ball in balls)
