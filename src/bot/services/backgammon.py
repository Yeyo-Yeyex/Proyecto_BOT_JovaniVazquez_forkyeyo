"""Reglas del backgammon (`backgammon`): el tablero, Jovani (la máquina), los retos y el dinero.

Lógica pura, sin Discord ni dinero real. El cog cobra, paga y pinta; la ficha de
cada miembro y sus retos del día se guardan en un repositorio aparte. Este módulo
solo sabe de reglas, de la máquina y de cuentas.

**El juego.** Cada jugador tiene 15 fichas y los dados las llevan por los 24 puntos
hasta su casa; gana quien saca las 15 antes. Las blancas van de los puntos altos a
los bajos (su casa es el 1-6 y sacan «por debajo del 1»); las negras, al revés. Se
juega con dos dados (los dobles valen cuatro pasos) y las reglas oficiales:

- hay que usar todos los dados que se pueda y, si solo cabe uno, el mayor;
- con fichas en la barra hay que entrar antes de mover nada;
- no se cae en un punto con dos o más fichas rivales; una ficha sola se come y
  vuelve a la barra;
- solo se saca cuando las 15 están en casa, con el dado exacto o con uno mayor si
  no queda ninguna ficha más atrás.

**Dos maneras de jugar:**

- *Contra Jovani*, sin dinero (`Level`): cuatro niveles que cambian cuánto piensa
  (`Level.ply`) y cuánto se despista (`Level.blunder`). Contra la máquina no se
  apuesta; lo que paga son los retos.
- *Duelo contra otra persona*, con apuesta opcional y cubo de doblar. Cada uno
  retiene `escrow` (cubre un backgammon al valor máximo del cubo en ese momento) y
  al acabar se reparte según lo que valga la partida (`game_kind` × cubo). La
  comisión es la de los gallos (`gallos.DUEL_CUT`). Mientras se juegan los primeros
  turnos (`GRADA_TURNS`) el público apuesta a la porra con las reglas de la grada
  de los gallos.

**Retos del día y racha** (como el 4 en raya, `bot.services.enraya`): tres retos al
día por miembro, uno fácil, uno medio y uno difícil, sorteados con su servidor, su
id y la fecha. Cada día seguido jugando sube un 10 % el premio, hasta el doble.

**Equilibrio** (Biblia, «Equilibrio: medir en tiradas»). Los tres retos dan 750 Y$
brutos al día y, con la racha a tope, 1.500, como el 4 en raya y el club del
póker. Es dinero que se crea y paga IRPF como un premio; lo compensa, en parte, la
comisión de los duelos y de la grada, que se destruye.

**Jovani.** Evalúa posiciones con una heurística (`evaluate`) y mira una jugada
(`ply` 1) o jugada y respuesta (`ply` 2). Nada de redes neuronales: es una
heurística a mano, suficiente para ganar a quien no cuenta pips.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from fractions import Fraction

from bot.services.gallos import (
    Corner,
    PoolBet,
    duel_prize,
    pool_odds,
    pool_payouts,
    pool_totals,
)
from bot.services.poker_club import next_streak, streak_multiplier_pct

#: Clave del juego: motivo del libro, `casino_stats.GAMES` y porras.
GAME = "backgammon"
CHECKERS = 15
POINTS = 24
#: Origen especial de un paso: la barra.
BAR = 24
#: Destino especial de un paso: fuera del tablero (sacar ficha).
OFF = 25
#: Tope del cubo de doblar.
MAX_CUBE = 8
#: Turnos jugados (de los dos) mientras la grada de un duelo sigue abierta.
GRADA_TURNS = 4
#: Puntuación de una partida ganada o perdida en `evaluate`.
WIN_SCORE = 10_000.0


class Side(Enum):
    """Color de un jugador. Las blancas mueven de los puntos altos a los bajos."""

    WHITE = 1
    BLACK = 2

    @property
    def other(self) -> Side:
        return Side.BLACK if self is Side.WHITE else Side.WHITE

    @property
    def emoji(self) -> str:
        return "⚪" if self is Side.WHITE else "⚫"

    @property
    def label(self) -> str:
        return "blancas" if self is Side.WHITE else "negras"

    @property
    def corner(self) -> Corner:
        """La esquina de la grada de los gallos que hace de este color (`pool_*`)."""
        return Corner.RED if self is Side.WHITE else Corner.BLUE

    @property
    def _n(self) -> int:
        """Índice del color en las tuplas `(blancas, negras)`."""
        return 0 if self is Side.WHITE else 1


@dataclass(frozen=True, slots=True)
class Step:
    """Una ficha movida con un dado.

    Attributes:
        source: Casilla de origen (0..23) o `BAR`.
        target: Casilla de destino (0..23) o `OFF`.
        die: El dado que se gasta.
        hit: Si come una ficha rival (la manda a la barra).
    """

    source: int
    target: int
    die: int
    hit: bool = False


def point_number(index: int, side: Side) -> int:
    """El número del punto de la casilla `index` visto por `side` (el 1 es su último)."""
    return index + 1 if side is Side.WHITE else POINTS - index


@dataclass(frozen=True)
class Position:
    """Una posición inmutable y hashable.

    Attributes:
        points: 24 casillas; el índice `i` es el punto `i + 1` de las blancas. Positivo
            son fichas blancas y negativo, negras.
        bar: `(blancas, negras)` en la barra.
        off: `(blancas, negras)` ya sacadas.
    """

    points: tuple[int, ...]
    bar: tuple[int, int] = (0, 0)
    off: tuple[int, int] = (0, 0)

    @classmethod
    def initial(cls) -> Position:
        """La salida estándar."""
        points = [0] * POINTS
        for index, n in ((23, 2), (12, 5), (7, 3), (5, 5)):
            points[index] = n
            points[POINTS - 1 - index] = -n
        return cls(tuple(points))

    def count(self, side: Side, index: int) -> int:
        """Fichas de `side` en la casilla `index`."""
        value = self.points[index]
        return max(value, 0) if side is Side.WHITE else max(-value, 0)

    def on_bar(self, side: Side) -> int:
        return self.bar[side._n]

    def borne_off(self, side: Side) -> int:
        return self.off[side._n]

    def pip(self, side: Side) -> int:
        """La cuenta de pips: lo que le falta a `side` para sacar todo (la barra vale 25)."""
        if side is Side.WHITE:
            total = sum(v * (i + 1) for i, v in enumerate(self.points) if v > 0)
        else:
            total = sum(-v * (POINTS - i) for i, v in enumerate(self.points) if v < 0)
        return total + 25 * self.bar[side._n]

    def all_home(self, side: Side) -> bool:
        """Si las fichas que le quedan a `side` están todas en su casa (y ninguna en la barra)."""
        if self.bar[side._n]:
            return False
        outside = range(6, POINTS) if side is Side.WHITE else range(18)
        return not any(self.count(side, i) for i in outside)

    @property
    def winner(self) -> Side | None:
        """Quien ha sacado las 15 fichas, o `None`."""
        if self.off[0] == CHECKERS:
            return Side.WHITE
        if self.off[1] == CHECKERS:
            return Side.BLACK
        return None

    def contact(self) -> bool:
        """Si aún pueden cruzarse fichas (si no, es una carrera pura)."""
        white_back = POINTS if self.bar[0] else -1
        black_front = -1 if self.bar[1] else POINTS
        for i in range(POINTS - 1, -1, -1):
            if self.points[i] > 0:
                white_back = max(white_back, i)
                break
        for i in range(POINTS):
            if self.points[i] < 0:
                black_front = min(black_front, i)
                break
        return white_back > black_front

    def apply(self, side: Side, step: Step) -> Position:
        """La posición tras mover `step`; si el destino tiene una ficha rival sola, la come.

        El `hit` del paso se ignora: se calcula aquí.

        Raises:
            ValueError: Si el paso no es legal por sí mismo (casilla bloqueada, sin
                ficha en el origen, dado que no lleva al destino, sacar sin tener
                todo en casa, mover con fichas en la barra…).
        """
        legal = _steps_for(self, side, step.die)
        if not any(s.source == step.source and s.target == step.target for s in legal):
            raise ValueError(f"Paso ilegal para las {side.label}: {step}.")
        return _move(self, side, step.source, step.target)


# -- Generador de jugadas -----------------------------------------------------------------


def _steps_for(position: Position, side: Side, die: int) -> list[Step]:
    """Todos los pasos sueltos legales de `side` con un dado, sin mirar los demás dados."""
    white = side is Side.WHITE
    sign = 1 if white else -1
    points = position.points
    if position.bar[side._n]:
        # Entrar: la barra es una casilla virtual detrás de la última (24 o -1).
        target = (POINTS if white else -1) - sign * die
        value = points[target] * sign
        return [Step(BAR, target, die, value == -1)] if value >= -1 else []
    own = [i for i in range(POINTS) if points[i] * sign > 0]
    if not own:
        return []
    back = own[-1] if white else own[0]
    can_bear_off = back <= 5 if white else back >= 18
    steps: list[Step] = []
    for i in own:
        target = i - sign * die
        if 0 <= target < POINTS:
            value = points[target] * sign
            if value >= -1:
                steps.append(Step(i, target, die, value == -1))
        elif can_bear_off and (target in (-1, POINTS) or i == back):
            # Dado exacto, o mayor solo para la ficha más atrasada de la casa.
            steps.append(Step(i, OFF, die))
    return steps


def _move(position: Position, side: Side, source: int, target: int) -> Position:
    """Mueve sin comprobar nada (lo que ya sabe el generador)."""
    sign = 1 if side is Side.WHITE else -1
    me = side._n
    points = list(position.points)
    bar = list(position.bar)
    off = list(position.off)
    if source == BAR:
        bar[me] -= 1
    else:
        points[source] -= sign
    if target == OFF:
        off[me] += 1
    else:
        if points[target] * sign == -1:
            points[target] = 0
            bar[1 - me] += 1
        points[target] += sign
    return Position(tuple(points), (bar[0], bar[1]), (off[0], off[1]))


def dice_of(roll: tuple[int, int]) -> tuple[int, ...]:
    """Los dados que se pueden gastar con una tirada: cuatro si es un doble."""
    return (roll[0],) * 4 if roll[0] == roll[1] else roll


def _without(dice: tuple[int, ...], die: int) -> tuple[int, ...]:
    rest = list(dice)
    rest.remove(die)
    return tuple(rest)


def _depth(
    position: Position,
    side: Side,
    dice: tuple[int, ...],
    memo: dict[tuple[Position, tuple[int, ...]], int],
) -> int:
    """Cuántos de los `dice` (ordenados de mayor a menor) se pueden gastar como máximo."""
    key = (position, dice)
    cached = memo.get(key)
    if cached is not None:
        return cached
    best = 0
    for die in sorted(set(dice), reverse=True):
        rest = _without(dice, die)
        for step in _steps_for(position, side, die):
            used = 1 + _depth(_move(position, side, step.source, step.target), side, rest, memo)
            if used > best:
                best = used
                if best == len(dice):
                    memo[key] = best
                    return best
    memo[key] = best
    return best


def _first_dice(position: Position, side: Side, dice: tuple[int, ...], total: int) -> list[int]:
    """Los dados con los que puede empezar una jugada que gasta `total` dados.

    Si solo se puede gastar uno y los dados son distintos, manda el mayor mientras
    se pueda jugar.
    """
    distinct = sorted(set(dice), reverse=True)
    if total == 1 and len(distinct) > 1 and _steps_for(position, side, distinct[0]):
        return distinct[:1]
    return distinct


def _play_table(
    position: Position, side: Side, dice: tuple[int, ...]
) -> dict[Position, tuple[Step, ...]]:
    """Posición final → una jugada completa legal que llega a ella (sin repetir finales)."""
    dice = tuple(sorted(dice, reverse=True))
    memo: dict[tuple[Position, tuple[int, ...]], int] = {}
    total = _depth(position, side, dice, memo)
    if total == 0:
        return {position: ()}
    finals: dict[Position, tuple[Step, ...]] = {}
    seen: set[tuple[Position, tuple[int, ...]]] = set()

    def walk(
        pos: Position, left: tuple[int, ...], steps: tuple[Step, ...], todo: int, first: list[int]
    ) -> None:
        if todo == 0:
            finals.setdefault(pos, steps)
            return
        key = (pos, left)
        if key in seen:
            return
        seen.add(key)
        for die in first:
            rest = _without(left, die)
            for step in _steps_for(pos, side, die):
                nxt = _move(pos, side, step.source, step.target)
                if _depth(nxt, side, rest, memo) == todo - 1:
                    walk(nxt, rest, (*steps, step), todo - 1, sorted(set(rest), reverse=True))

    walk(position, dice, (), total, _first_dice(position, side, dice, total))
    return finals


def legal_plays(position: Position, side: Side, dice: tuple[int, ...]) -> list[tuple[Step, ...]]:
    """Todas las jugadas completas legales de `side` con `dice`, sin repetir posición final.

    Con las reglas oficiales: se gastan tantos dados como se pueda; si solo cabe
    uno, el mayor si se puede; con fichas en la barra se entra primero. Si no hay
    jugada, devuelve `[()]` (una jugada vacía).
    """
    return list(_play_table(position, side, dice).values())


def legal_steps(position: Position, side: Side, dice: tuple[int, ...]) -> list[Step]:
    """Los pasos sueltos con los que puede empezar alguna jugada de `legal_plays`.

    Sirven para mover ficha a ficha sin saltarse el uso del máximo de dados ni del
    mayor. Se miran todos los caminos (no solo uno por posición final), así que
    mover primero el dado pequeño sale si de verdad lleva a una jugada legal.
    Vacío si no queda nada que mover.
    """
    dice = tuple(sorted(dice, reverse=True))
    memo: dict[tuple[Position, tuple[int, ...]], int] = {}
    total = _depth(position, side, dice, memo)
    found: list[Step] = []
    for die in _first_dice(position, side, dice, total) if total else []:
        rest = _without(dice, die)
        for step in _steps_for(position, side, die):
            nxt = _move(position, side, step.source, step.target)
            if _depth(nxt, side, rest, memo) == total - 1 and step not in found:
                found.append(step)
    return found


def remaining_dice(dice: tuple[int, ...], step: Step) -> tuple[int, ...]:
    """Los dados que quedan tras gastar el de `step`.

    Raises:
        ValueError: Si `step.die` no está entre `dice`.
    """
    return _without(dice, step.die)


def format_step(step: Step, side: Side) -> str:
    """Un paso en notación de backgammon vista por `side`: `13/8`, `13/8*`, `bar/22`, `6/off`."""
    source = "bar" if step.source == BAR else str(point_number(step.source, side))
    target = "off" if step.target == OFF else str(point_number(step.target, side))
    return f"{source}/{target}{'*' if step.hit else ''}"


def format_play(steps: Sequence[Step], side: Side) -> str:
    """Una jugada: los pasos separados por espacios, o «—» si no se pudo mover."""
    return " ".join(format_step(step, side) for step in steps) or "—"


def opening_roll(rng: random.Random) -> tuple[int, int]:
    """La tirada de salida: `(dado de las blancas, dado de las negras)`, nunca iguales.

    Los empates se repiten. Sale quien saca más y juega esos dos dados.
    """
    while True:
        white, black = rng.randint(1, 6), rng.randint(1, 6)
        if white != black:
            return white, black


# -- Lo que vale la partida ------------------------------------------------------------------


def game_kind(position: Position, winner: Side) -> int:
    """Lo que vale ganar: 1 sencilla, 2 gammon, 3 backgammon.

    Gammon es que el perdedor no haya sacado ninguna ficha; backgammon, que además
    le quede alguna en la barra o en la casa del ganador.
    """
    loser = winner.other
    if position.borne_off(loser):
        return 1
    if position.on_bar(loser):
        return 3
    home = range(6) if winner is Side.WHITE else range(18, POINTS)
    return 3 if any(position.count(loser, i) for i in home) else 2


def resign_kind(position: Position, loser: Side) -> int:
    """Lo que vale rendirse (o dormirse) ahora.

    1, salvo que el rival ya haya sacado alguna ficha y `loser` ninguna: entonces
    se pierde lo que valdría perder esta posición (`game_kind`).
    """
    winner = loser.other
    if position.borne_off(winner) and not position.borne_off(loser):
        return game_kind(position, winner)
    return 1


# -- Jovani --------------------------------------------------------------------------------
#
# Heurística de posición, no una red neuronal. Todo se mira desde un lado en el
# «sistema de quien mira»: `m[k]` son sus fichas en su punto `k + 1` y `o[k]` las
# del rival en ese mismo punto; quien mira mueve de `k` altos a bajos y el rival,
# al revés. La barra del rival es una casilla virtual en `k = -1` y la propia, en 24.

#: Valor de cada punto de la casa hecho (puntos 1..6).
_HOME_W = (1.0, 1.5, 2.5, 3.5, 5.0, 4.5)
#: Valor de un ancla (punto hecho) en la casa rival (puntos 19..24 de quien mira).
_ANCHOR_W = (2.0, 3.5, 3.5, 2.5, 1.5, 1.0)
#: Valor de un bloqueo de tantos puntos seguidos delante de fichas rivales (índice = largo).
_PRIME_W = (0.0, 0.0, 0.0, 2.0, 5.0, 10.0, 18.0, 18.0)


def _build_ways() -> list[list[tuple[int, tuple[int, ...]]]]:
    """Para cada distancia, las formas de recorrerla con una tirada.

    Cada forma es `(máscara de tiradas, puntos intermedios)`. La máscara marca, de
    las 36 tiradas ordenadas, las que sirven; los intermedios son las distancias a
    las que la ficha tiene que parar y no puede haber un punto rival hecho.
    """
    ways: list[list[tuple[int, tuple[int, ...]]]] = [[] for _ in range(25)]
    for a in range(1, 7):
        ways[a].append((1 << ((a - 1) * 7), ()))
        for n in range(2, 5):
            ways[n * a].append((1 << ((a - 1) * 7), tuple(a * j for j in range(1, n))))
        for b in range(a + 1, 7):
            mask = (1 << ((a - 1) * 6 + b - 1)) | (1 << ((b - 1) * 6 + a - 1))
            ways[a].append((mask, ()))
            ways[b].append((mask, ()))
            ways[a + b].append((mask, (a,)))
            ways[a + b].append((mask, (b,)))
    return ways


_WAYS = _build_ways()


def _blot_risk(m: list[int], o: list[int], obar: int, enemy_board: int) -> float:
    """Pips esperados que se pierde por las fichas sueltas de `m` que el rival puede comer.

    Para cada ficha sola cuenta las tiradas (de 36) con las que algún atacante la
    alcanza, directas o sumando los dos dados sin pasar por un punto hecho, y las
    pondera por lo que cuesta que la coman: los pips que pierde, un tempo y la
    fuerza de la casa del rival donde tendrá que volver a entrar.
    """
    risk = 0.0
    for k in range(POINTS):
        if m[k] != 1:
            continue
        hits = 0
        attackers = (
            [-1] if obar else [j for j in range(k) if o[j]]
        )  # con fichas en la barra solo cuentan las que entran
        for j in attackers:
            for mask, mids in _WAYS[k - j]:
                if all(m[j + mid] < 2 for mid in mids):
                    hits |= mask
        if hits:
            risk += (hits.bit_count() / 36) * (25 - (k + 1) + 8 + 3 * enemy_board)
    return risk


def _side_score(m: list[int], o: list[int], mbar: int, obar: int, moff: int) -> float:
    """Lo que vale la estructura de quien mira: casa, anclas, bloqueos, barra y sacadas."""
    home_made = sum(1 for k in range(6) if m[k] >= 2)
    enemy_board = sum(1 for k in range(18, POINTS) if o[k] >= 2)
    score = sum(_HOME_W[k] for k in range(6) if m[k] >= 2)
    score += sum(_ANCHOR_W[k - 18] for k in range(18, POINTS) if m[k] >= 2)
    # Un bloqueo solo vale delante de alguna ficha rival que aún tenga que pasar.
    rearmost = -1 if obar else next((k for k in range(POINTS) if o[k]), POINTS)
    run = 0
    for k in range(POINTS + 1):
        if k < POINTS and m[k] >= 2:
            run += 1
            continue
        if run >= 3 and k - run > rearmost:
            score += _PRIME_W[min(run, 7)]
        run = 0
    score += obar * (6 + 2 * home_made)
    score -= mbar * (4 + 2 * enemy_board)
    score += moff
    score -= 0.7 * sum(c - 4 for c in m if c > 4)
    return score


def _race_pips(m: list[int], mbar: int, moff: int) -> float:
    """Pips de `m` más lo que se pierde al sacar: huecos en la casa y fichas apiladas atrás."""
    pips = sum(c * (k + 1) for k, c in enumerate(m)) + 25 * mbar
    waste = 0.4 * (CHECKERS - moff)
    top = max((k for k in range(6) if m[k]), default=-1)
    if mbar == 0 and not any(m[6:]):
        waste += 1.5 * sum(1 for k in range(top) if not m[k])
        waste += 1.0 * (max(m[0] - 2, 0) + max(m[1] - 3, 0))
    return pips + waste


def evaluate(position: Position, side: Side) -> float:
    """Lo buena que es la posición para `side`, recién movido (le toca tirar al rival).

    Positivo favorece a `side`; el cero es una posición igual. Una partida
    acabada vale `±WIN_SCORE`. Con contacto suma, en una escala de «pips»:

    - la diferencia de pips (lo que adelanta a su rival);
    - los puntos hechos en su casa, los bloqueos (primes) delante de fichas rivales,
      las anclas en la casa rival, las fichas rivales en la barra (más cuanto más
      fuerte su casa) y las ya sacadas, menos lo mismo para el rival;
    - menos las fichas sueltas ponderadas por las tiradas, directas e indirectas,
      con que el rival las come y por lo que cuesta que se las coman.

    Sin contacto es una carrera pura: la diferencia de pips con la pérdida
    esperada al sacar (huecos y apilamiento en los puntos bajos).
    """
    winner = position.winner
    if winner is not None:
        return WIN_SCORE if winner is side else -WIN_SCORE
    points = position.points
    if side is Side.WHITE:
        m = [v if v > 0 else 0 for v in points]
        o = [-v if v < 0 else 0 for v in points]
    else:
        m = [-v if v < 0 else 0 for v in reversed(points)]
        o = [v if v > 0 else 0 for v in reversed(points)]
    mbar, obar = position.bar[side._n], position.bar[side.other._n]
    moff, ooff = position.off[side._n], position.off[side.other._n]
    if not position.contact():
        # Pips del rival: sus fichas en `k` valen `24 - k`.
        return _race_pips(o[::-1], obar, ooff) - _race_pips(m, mbar, moff)
    opp_pips = sum(c * (POINTS - k) for k, c in enumerate(o)) + 25 * obar
    my_pips = sum(c * (k + 1) for k, c in enumerate(m)) + 25 * mbar
    enemy_board = sum(1 for k in range(18, POINTS) if o[k] >= 2)
    score = float(opp_pips - my_pips)
    score += _side_score(m, o, mbar, obar, moff)
    score -= _side_score(o[::-1], m[::-1], obar, mbar, ooff)
    return score - _blot_risk(m, o, obar, enemy_board)


@dataclass(frozen=True, slots=True)
class Level:
    """Un nivel de Jovani.

    Attributes:
        key: Clave estable (estadísticas, logros, retos).
        name: Lo que se enseña.
        emoji: Junto al nombre.
        ply: Jugadas que piensa: 1 mira su jugada, 2 mira también la mejor respuesta del rival.
        blunder: Probabilidad de jugar una jugada legal al azar.
        order: 0 el más fácil.
        blurb: Lo que dice el desplegable.
    """

    key: str
    name: str
    emoji: str
    ply: int
    blunder: float
    order: int
    blurb: str


LEVELS: tuple[Level, ...] = (
    Level("facil", "Jovani en el chiringuito", "🍹", 1, 0.45, 0,
          "Fácil. Tira los dados entre mojito y mojito, sin pulpa."),
    Level("normal", "Jovani de casino", "🎲", 1, 0.15, 1,
          "Normal. Sabe las reglas, pero la banca siempre gana. ¡Toma mangó!"),
    Level("dificil", "Jovani concentrado", "🧠", 1, 0.02, 2,
          "Difícil. Cuenta pips como Hacienda cuenta tus gastos."),
    Level("imposible", "Jovani con café de Puerto Rico", "🔥", 2, 0.0, 3,
          "Imposible. Mira tu respuesta antes de que tires. ¡Quiero café!"),
)  # fmt: skip
LEVEL_BY_KEY: dict[str, Level] = {level.key: level for level in LEVELS}
DEFAULT_LEVEL = "normal"

#: Las 21 tiradas distintas y lo que pesan sobre 36: los dobles 1, el resto 2.
_ROLLS: tuple[tuple[tuple[int, int], int], ...] = tuple(
    ((a, b), 1 if a == b else 2) for a in range(1, 7) for b in range(a, 7)
)
#: Cuántas de las mejores jugadas a ply 1 se miran a fondo a ply 2.
_FINALISTS = 5


def jovani_play(
    position: Position, side: Side, roll: tuple[int, int], level: Level, rng: random.Random
) -> tuple[Step, ...]:
    """La jugada que elige Jovani con `roll` en `position`.

    Con probabilidad `level.blunder`, una jugada legal al azar. Si no, a ply 1 la
    de mejor `evaluate`; a ply 2, de las cinco mejores a ply 1, la que da mejor
    media sobre las 21 tiradas del rival (los dobles pesan 1/36 y el resto 2/36)
    de su mejor respuesta a ply 1. Los empates se rompen con `rng`, así que con el
    mismo `rng` la jugada es siempre la misma.
    """
    table = _play_table(position, side, dice_of(roll))
    if rng.random() < level.blunder:
        return rng.choice(list(table.values()))
    if len(table) == 1:
        return next(iter(table.values()))
    cache: dict[tuple[Position, Side], float] = {}

    def value(pos: Position, who: Side) -> float:
        key = (pos, who)
        if key not in cache:
            cache[key] = evaluate(pos, who)
        return cache[key]

    scored = [(value(pos, side), pos) for pos in table]
    if level.ply >= 2 and len(scored) > 1:
        scored.sort(key=lambda item: -item[0])  # estable: a igual valor, el orden de la tabla
        scored = [(_two_ply(pos, side, value), pos) for _, pos in scored[:_FINALISTS]]
    best = max(score for score, _ in scored)
    return table[rng.choice([pos for score, pos in scored if score >= best - 1e-9])]


def _two_ply(position: Position, side: Side, value: Callable[[Position, Side], float]) -> float:
    """La media, sobre las tiradas del rival, de lo que vale para `side` su mejor respuesta."""
    if position.winner is not None:
        return value(position, side)
    rival = side.other
    total = 0
    for roll, weight in _ROLLS:
        replies = _play_table(position, rival, dice_of(roll))
        total += weight * max(value(reply, rival) for reply in replies)
    return -total / 36


def plan_seed(turn_seed: int, salt: int) -> random.Random:
    """El azar de la respuesta de Jovani para un turno y una `salt` (la tirada, por ejemplo).

    Sale de la semilla del turno, así que la respuesta pintada por adelantado y la
    que se calcula al pulsar son la misma.
    """
    return random.Random(turn_seed * 31 + salt)


# -- Lo que pasó en una partida (para retos y logros) ---------------------------------------


@dataclass(frozen=True, slots=True)
class Turn:
    """Un turno del registro de la partida: quién, qué sacó y qué movió."""

    side: Side
    roll: tuple[int, int]
    steps: tuple[Step, ...]


class Ending(Enum):
    """Cómo acaba una partida para quien la juega. `DROP` es no aceptar un doblado."""

    WIN = "win"
    LOSS = "loss"
    RESIGN = "resign"
    TIMEOUT = "timeout"
    DROP = "drop"


@dataclass(frozen=True, slots=True)
class GameEvent:
    """Una partida terminada, vista por uno de los dos jugadores.

    Attributes:
        ending: Cómo acaba para él.
        side: Su color.
        level: El nivel de Jovani, o `None` en un duelo.
        kind: Lo que valía la partida: 1, 2 (gammon) o 3 (backgammon).
        cube: El valor del cubo al acabar.
        stake: Lo que se jugó (0 contra Jovani o en un duelo amistoso).
        turns: Turnos que jugó.
        hits: Fichas rivales que comió.
        hit_taken: Veces que le comieron una ficha.
        doubles: Dobles que sacó.
        double_run: Máximo de dobles seguidos en sus turnos.
        max_deficit: La mayor desventaja de pips que tuvo tras cualquier turno.
        closed_out: Si llegó a tener los 6 puntos de su casa hechos con el rival en la barra.
        my_off: Fichas que sacó.
        opening: Si salió él.
    """

    ending: Ending
    side: Side
    level: Level | None
    kind: int
    cube: int = 1
    stake: int = 0
    turns: int = 0
    hits: int = 0
    hit_taken: int = 0
    doubles: int = 0
    double_run: int = 0
    max_deficit: int = 0
    closed_out: bool = False
    my_off: int = 0
    opening: bool = False

    @property
    def won(self) -> bool:
        return self.ending is Ending.WIN

    @property
    def lost(self) -> bool:
        return self.ending in (Ending.LOSS, Ending.RESIGN, Ending.TIMEOUT, Ending.DROP)

    @property
    def duel(self) -> bool:
        return self.level is None

    @property
    def gammon(self) -> bool:
        """Si ganó un gammon o un backgammon."""
        return self.won and self.kind >= 2

    @property
    def backgammon(self) -> bool:
        return self.won and self.kind == 3

    def beat_at_least(self, order: int) -> bool:
        """Si ganó a Jovani en un nivel de al menos `order`."""
        return self.won and self.level is not None and self.level.order >= order


def _closed_board(position: Position, side: Side) -> bool:
    home = range(6) if side is Side.WHITE else range(18, POINTS)
    return all(position.count(side, i) >= 2 for i in home) and position.on_bar(side.other) > 0


def event_for(
    log: Sequence[Turn],
    side: Side,
    *,
    ending: Ending,
    level: Level | None,
    kind: int,
    cube: int = 1,
    stake: int = 0,
) -> GameEvent:
    """Rehace la partida desde la salida con `log` y cuenta lo que hizo `side`.

    Raises:
        ValueError: Si algún paso del registro no es legal en su posición.
    """
    position = Position.initial()
    rival = side.other
    turns = hits = hit_taken = doubles = run = double_run = max_deficit = 0
    closed_out = False
    for turn in log:
        mine = turn.side is side
        for step in turn.steps:
            if step.target != OFF and position.count(turn.side.other, step.target) == 1:
                if mine:
                    hits += 1
                else:
                    hit_taken += 1
            position = position.apply(turn.side, step)
        if mine:
            turns += 1
            if turn.roll[0] == turn.roll[1]:
                doubles += 1
                run += 1
                double_run = max(double_run, run)
            else:
                run = 0
        max_deficit = max(max_deficit, position.pip(side) - position.pip(rival))
        closed_out = closed_out or _closed_board(position, side)
    return GameEvent(
        ending=ending,
        side=side,
        level=level,
        kind=kind,
        cube=cube,
        stake=stake,
        turns=turns,
        hits=hits,
        hit_taken=hit_taken,
        doubles=doubles,
        double_run=double_run,
        max_deficit=max_deficit,
        closed_out=closed_out,
        my_off=position.borne_off(side),
        opening=bool(log) and log[0].side is side,
    )


# -- Retos del día ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Challenge:
    """Un reto del día.

    Attributes:
        key: Clave estable (se guarda con lo que lleva cada uno).
        text: Lo que se enseña, con `{goal}` para la meta.
        goal: Cuántas veces hay que cumplirlo.
        tier: 0 fácil, 1 medio, 2 difícil.
        count: Cuánto suma una partida (0 si no cuenta).
    """

    key: str
    text: str
    goal: int
    tier: int
    count: Callable[[GameEvent], int] = field(compare=False)

    @property
    def reward(self) -> int:
        """Premio bruto al cumplirlo, antes de la racha."""
        return TIER_REWARDS[self.tier]

    @property
    def label(self) -> str:
        """El texto con la meta puesta."""
        return self.text.format(goal=self.goal)


#: Premio bruto de cada dificultad (fácil, medio, difícil): lo mismo que el 4 en raya.
TIER_REWARDS = (100, 250, 400)
TIER_NAMES = ("Fácil", "Medio", "Difícil")


def _beat(order: int) -> Callable[[GameEvent], int]:
    return lambda e: int(e.beat_at_least(order))


def _serious_win(e: GameEvent) -> int:
    """Una victoria en duelo o contra Jovani en Normal o más."""
    return int(e.won and (e.duel or e.beat_at_least(1)))


CHALLENGES: tuple[Challenge, ...] = (
    # Fáciles: jugando un rato salen solos.
    Challenge("jugar_3", "Juega {goal} partidas", 3, 0, lambda e: 1),
    Challenge("ganar_2", "Gana {goal} partidas", 2, 0, lambda e: int(e.won)),
    Challenge("normal_1", "Gánale a Jovani en Normal o más", 1, 0, _beat(1)),
    Challenge("comer_5", "Come {goal} fichas del rival", 5, 0, lambda e: e.hits),
    Challenge("dobles_3", "Saca {goal} dobles", 3, 0, lambda e: e.doubles),
    # Medios: piden constancia o algo de cabeza.
    Challenge("serio_4", "Gana {goal} partidas en Normal o más (o en duelo)", 4, 1, _serious_win),
    Challenge("dificil_1", "Gánale a Jovani en Difícil o más", 1, 1, _beat(2)),
    Challenge("comer_15", "Come {goal} fichas del rival", 15, 1, lambda e: e.hits),
    Challenge("intacto_1", "Gana en Normal o más (o en duelo) sin que te coman ninguna ficha", 1, 1,
              lambda e: int(_serious_win(e) and e.hit_taken == 0)),
    Challenge("duelo_1", "Juega un duelo contra otra persona", 1, 1, lambda e: int(e.duel)),
    Challenge("gammon_1", "Gana un gammon en Normal o más (o en duelo)", 1, 1,
              lambda e: int(e.gammon and _serious_win(e))),
    # Difíciles: un buen rato de tablero o una partida muy buena.
    Challenge("imposible_1", "Gánale a Jovani en Imposible", 1, 2, _beat(3)),
    Challenge("dificil_3", "Gánale {goal} veces a Jovani en Difícil o más", 3, 2, _beat(2)),
    Challenge("duelo_gana_1", "Gana un duelo contra otra persona", 1, 2,
              lambda e: int(e.duel and e.won)),
    Challenge("cierre_1", "Cierra el tablero al rival (6 puntos en tu casa y él en la barra)", 1, 2,
              lambda e: int(e.closed_out)),
    Challenge("backgammon_1", "Gana un backgammon", 1, 2, lambda e: int(e.backgammon)),
)  # fmt: skip
CHALLENGE_BY_KEY: dict[str, Challenge] = {c.key: c for c in CHALLENGES}


def daily_challenges(guild_id: int, user_id: int, day: date) -> tuple[Challenge, ...]:
    """Los tres retos de hoy de un miembro: uno de cada dificultad.

    Se sortean con una semilla de su servidor, su id y la fecha (y el nombre del
    juego, para que no coincidan con los de otros juegos), así que son los mismos
    todo el día y no hace falta guardarlos.
    """
    digest = hashlib.sha256(f"{GAME}:{guild_id}:{user_id}:{day.isoformat()}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    return tuple(rng.choice([c for c in CHALLENGES if c.tier == tier]) for tier in range(3))


def progress_for(challenges: tuple[Challenge, ...], event: GameEvent) -> dict[str, int]:
    """Lo que suma esta partida a cada reto (sin los que no suma nada)."""
    return {c.key: n for c in challenges if (n := c.count(event))}


def reward_for(challenge: Challenge, streak: int) -> int:
    """El premio bruto de un reto con la racha de hoy."""
    return challenge.reward * (100 + streak_multiplier_pct(streak)) // 100


# -- La ficha de cada miembro --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlayerRecord:
    """El historial de un miembro en el backgammon.

    Attributes:
        games: Partidas terminadas (contra Jovani y duelos).
        wins: Ganadas.
        losses: Perdidas (también rendirse, dormirse o no aceptar un doblado).
        gammons: Gammons y backgammons ganados.
        backgammons: Backgammons ganados.
        streak: Victorias seguidas ahora mismo.
        best_streak: La mejor racha de victorias.
        day_streak: Días seguidos jugando.
        last_day: Último día que jugó.
        best_level: Nivel más alto al que le ha ganado a Jovani (`Level.order`, -1 ninguno).
    """

    games: int = 0
    wins: int = 0
    losses: int = 0
    gammons: int = 0
    backgammons: int = 0
    streak: int = 0
    best_streak: int = 0
    day_streak: int = 0
    last_day: date | None = None
    best_level: int = -1


def after_game(record: PlayerRecord, event: GameEvent, day: date) -> PlayerRecord:
    """La ficha tras una partida (en el backgammon no hay tablas)."""
    streak = record.streak + 1 if event.won else 0
    best_level = record.best_level
    if event.won and event.level is not None:
        best_level = max(best_level, event.level.order)
    return PlayerRecord(
        games=record.games + 1,
        wins=record.wins + int(event.won),
        losses=record.losses + int(event.lost),
        gammons=record.gammons + int(event.gammon),
        backgammons=record.backgammons + int(event.backgammon),
        streak=streak,
        best_streak=max(record.best_streak, streak),
        day_streak=next_streak(record.day_streak, record.last_day, day),
        last_day=day,
        best_level=best_level,
    )


# -- La grada de los duelos ----------------------------------------------------------------


def grada_open(turns_played: int) -> bool:
    """Si el público aún puede apostar: hasta que se hayan jugado `GRADA_TURNS` turnos."""
    return turns_played < GRADA_TURNS


def grada_totals(bets: Mapping[int, PoolBet]) -> dict[Side, int]:
    """Lo apostado a cada color (las apuestas usan las esquinas de los gallos)."""
    totals = pool_totals(bets)
    return {side: totals[side.corner] for side in Side}


def grada_odds(bets: Mapping[int, PoolBet]) -> dict[Side, Fraction | None]:
    """Cuota a la porra de cada color si ganara ahora (`gallos.pool_odds`)."""
    odds = pool_odds(bets)
    return {side: odds[side.corner] for side in Side}


def grada_payouts(bets: Mapping[int, PoolBet], winner: Side) -> dict[int, int]:
    """Lo que cobra cada apostante (apuesta incluida). No hay tablas."""
    return pool_payouts(bets, winner.corner)


# -- El dinero de un duelo -----------------------------------------------------------------
#
# Funciones puras: el cobro real lo hace el cog. Cada jugador retiene `escrow` y al
# acabar se devuelve lo que sobra tras pagar lo que valía la partida.


def escrow(stake: int, cube: int) -> int:
    """Lo que cada jugador tiene retenido con el cubo en `cube`: cubre un backgammon."""
    return stake * cube * 3


def at_risk(stake: int, cube: int, kind: int) -> int:
    """Lo que se juega cada uno en una partida de valor `kind` (1, 2 o 3)."""
    return stake * cube * kind


def duel_payouts(stake: int, cube: int, kind: int, winner: Side) -> dict[Side, int]:
    """Lo que se devuelve a cada uno al acabar (lo retenido incluido).

    El perdedor recupera lo retenido menos lo que se jugaba (`at_risk`); el ganador,
    lo retenido más el premio de la apuesta sin la comisión de la casa
    (`gallos.duel_prize`) menos lo que él mismo puso. La suma devuelta más la
    comisión es lo retenido por los dos.
    """
    held = escrow(stake, cube)
    risked = at_risk(stake, cube, kind)
    return {
        winner: held + duel_prize(risked) - risked,
        winner.other: held - risked,
    }


def double_extra(stake: int, cube: int) -> int:
    """Lo que tiene que retener de más cada jugador para pasar el cubo de `cube` a `2 * cube`.

    Quien dobla lo pone al doblar y quien acepta, al aceptar; si el rival no
    acepta, a quien dobló se le devuelve con el resto (`duel_returns`).
    """
    return escrow(stake, cube * 2) - escrow(stake, cube)


def duel_returns(
    held: Mapping[Side, int], stake: int, cube: int, kind: int, winner: Side
) -> dict[Side, int]:
    """Lo que se devuelve a cada uno al acabar, con lo que cada uno tiene retenido.

    Como `duel_payouts`, pero lo retenido puede ser distinto para cada uno: quien
    dobla retiene la parte del cubo nuevo antes de que el rival acepte o pase. El
    perdedor recupera lo suyo menos lo que se jugaba (`at_risk`); el ganador, lo
    suyo más el premio sin la comisión (`gallos.duel_prize`) menos lo que puso.

    Raises:
        ValueError: Si el perdedor no tiene retenido lo que se juega (no debería
            pasar: `escrow` cubre un backgammon al valor del cubo).
    """
    risked = at_risk(stake, cube, kind)
    loser = winner.other
    if held[loser] < risked:
        raise ValueError(f"Retenido {held[loser]} y en juego {risked}.")
    return {winner: held[winner] + duel_prize(risked) - risked, loser: held[loser] - risked}


def can_double(cube: int, owner: Side | None, side: Side, stake: int) -> bool:
    """Si `side` puede doblar: hay apuesta, el cubo no está al tope y no es del rival."""
    return stake > 0 and cube < MAX_CUBE and owner in (None, side)
