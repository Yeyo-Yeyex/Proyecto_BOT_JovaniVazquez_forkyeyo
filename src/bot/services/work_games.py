"""Minijuegos del trabajo (`pala`): cavar, detectar, memoria y diálogo.

Lógica pura: generan las rondas a partir del contenido de
`bot.services.work_catalog`, reciben pulsaciones y calculan la puntuación. El
cog solo dibuja botones y llama a `MiniGame.press`.

Son juegos de decidir bien con un reloj global, no de reflejos: Discord
tarda de 100 a 500 ms en ir y volver por cada pulsación y ese retraso varía,
así que un juego de pulsar a tiempo sería injusto. La velocidad solo da un
pequeño extra (`TIME_BONUS`) si se acaban todas las rondas antes de tiempo.

Puntuación (0–100): aciertos sobre el máximo posible, más el extra de tiempo.

- **Cavar**: cada turno trae un plano (qué marca del suelo es segura, cuál
  esconde una tubería o un cable y cuál es roca). Cada palada ofrece varios
  sitios: cavar en el seguro suma, en la roca no suma y romper algo resta.
- **Detectar**: una lista con una trampa (el obrero que se escaquea, la
  partida con sobrecoste). Acertar suma.
- **Memoria**: se enseña una secuencia (una comanda, una votación) y, al
  ocultarla, hay que repetirla en orden. Cada elemento acertado suma; un fallo
  acaba la ronda.
- **Diálogo**: una situación y tres respuestas. La buena suma.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from bot.services.work import TIRED_TIME_FACTOR, Mechanic, Position
from bot.services.work_catalog import (
    BUDGET_ITEMS,
    DIALOGUE_PACKS,
    DIG_DANGERS,
    DIG_HINTS,
    DIG_MARKS,
    MEMORY_PACKS,
    SLACKING,
    WORKER_NAMES,
    WORKING,
)

#: Puntos extra (sobre 100) por acabar antes de tiempo, proporcionales a lo que sobra.
TIME_BONUS = 10
#: Segundos de margen tras el reloj para pulsaciones que ya iban de camino.
GRACE_SECONDS = 1.5


@dataclass(slots=True)
class Round:
    """Una ronda.

    Attributes:
        prompt: Texto de la ronda (situación, lista, pregunta).
        options: Etiquetas de los botones.
        answer: Índices correctos; en memoria, la secuencia en orden.
        penalty: Índices que restan un punto (romper algo al cavar).
        neutral: Índices que no suman ni restan (roca).
        reveal: En memoria, lo que se enseña antes de ocultarlo.
    """

    prompt: str
    options: list[str]
    answer: list[int]
    penalty: set[int] = field(default_factory=set)
    neutral: set[int] = field(default_factory=set)
    reveal: str | None = None

    @property
    def worth(self) -> int:
        """Puntos que vale la ronda si se hace perfecta."""
        return len(self.answer)


@dataclass(slots=True)
class MiniGame:
    """Una partida de un minijuego.

    Attributes:
        mechanic: Motor.
        header: Lo que se enseña siempre arriba (el plano, la regla de oro).
        rounds: Rondas en orden.
        seconds: Tiempo total.
        started_at: Inicio (epoch).
    """

    mechanic: Mechanic
    header: str
    rounds: list[Round]
    seconds: float
    started_at: float
    index: int = 0
    #: En memoria: cuántos elementos de la secuencia lleva bien en la ronda.
    step: int = 0
    #: En memoria: si se está enseñando la secuencia.
    showing: bool = False
    points: int = 0
    broken: int = 0
    perfect_rounds: int = 0
    correct: int = 0
    last: str = ""
    finished_at: float | None = None

    def __post_init__(self) -> None:
        self.showing = self.mechanic is Mechanic.MEMORY

    @property
    def deadline(self) -> float:
        """Momento en que se acaba el tiempo."""
        return self.started_at + self.seconds

    @property
    def max_points(self) -> int:
        """Puntos posibles en toda la partida."""
        return sum(r.worth for r in self.rounds) or 1

    @property
    def current(self) -> Round | None:
        """Ronda en juego, o `None` si ya no quedan."""
        return self.rounds[self.index] if self.index < len(self.rounds) else None

    def finished(self, now: float) -> bool:
        """Si ya no se puede jugar (sin rondas o sin tiempo)."""
        return self.finished_at is not None or self.current is None or now > self.deadline

    def finish(self, now: float) -> None:
        """Cierra la partida (por tiempo o por terminar)."""
        if self.finished_at is None:
            self.finished_at = min(now, self.deadline)

    def hide(self, now: float) -> None:
        """Memoria: oculta la secuencia y empieza a repetir."""
        if not self.finished(now):
            self.showing = False

    def _next_round(self, now: float) -> None:
        self.index += 1
        self.step = 0
        self.showing = self.mechanic is Mechanic.MEMORY
        if self.current is None:
            self.finish(now)

    def press(self, option: int, now: float) -> bool:
        """Registra una pulsación. Devuelve si ha sido un acierto.

        Las pulsaciones fuera de tiempo (con un pequeño margen) cierran la
        partida sin contar.
        """
        current = self.current
        if current is None or self.finished_at is not None:
            return False
        if now > self.deadline + GRACE_SECONDS:
            self.finish(now)
            return False
        if self.mechanic is Mechanic.MEMORY:
            return self._press_memory(current, option, now)
        hit = option in current.answer
        if hit:
            self.points += 1
            self.correct += 1
            self.last = "✅"
        elif option in current.penalty:
            self.points -= 1
            self.broken += 1
            self.last = "💥"
        elif option in current.neutral:
            self.last = "🪨"
        else:
            self.last = "❌"
        self._next_round(now)
        return hit

    def _press_memory(self, current: Round, option: int, now: float) -> bool:
        if self.showing:
            return False
        if option == current.answer[self.step]:
            self.points += 1
            self.step += 1
            if self.step == len(current.answer):
                self.perfect_rounds += 1
                self.correct += 1
                self.last = "✅ ¡Perfecta!"
                self._next_round(now)
            return True
        self.last = f"❌ Era {current.options[current.answer[self.step]]}"
        self._next_round(now)
        return False

    def score(self) -> int:
        """Puntuación final (0–100)."""
        base = 100 * max(0, self.points) / self.max_points
        bonus = 0.0
        if self.current is None and self.finished_at is not None:
            left = max(0.0, self.deadline - self.finished_at)
            bonus = TIME_BONUS * left / self.seconds
        return max(0, min(100, round(base + bonus)))


# -- Generadores ------------------------------------------------------------------------


def _shuffled(rng: random.Random, good: list[str], bad: list[str]) -> tuple[list[str], list[int]]:
    options = good + bad
    order = list(range(len(options)))
    rng.shuffle(order)
    shuffled = [options[i] for i in order]
    answer = [order.index(i) for i in range(len(good))]
    return shuffled, answer


def _dig(rng: random.Random, spots: int, rounds: int) -> tuple[str, list[Round]]:
    marks = list(DIG_MARKS)
    rng.shuffle(marks)
    safe, danger, rock = marks[:2], marks[2:4], marks[4:6]
    dangers = rng.sample(DIG_DANGERS, 2)
    header = (
        f"📜 **Plano de hoy:** cava en {safe[0]} o {safe[1]} · "
        f"{danger[0]} = {dangers[0]} · {danger[1]} = {dangers[1]} · "
        f"{rock[0]} {rock[1]} = roca"
    )
    built = []
    for number in range(1, rounds + 1):
        others = [rng.choice(danger + rock) for _ in range(spots - 1)]
        cells = [rng.choice(safe), *others]
        order = list(range(spots))
        rng.shuffle(order)
        options = [cells[i] for i in order]
        answer = [order.index(0)]
        penalty = {i for i, mark in enumerate(options) if mark in danger}
        neutral = {i for i, mark in enumerate(options) if mark in rock}
        prompt = f"⛏️ Palada {number}/{rounds}. ¿Dónde cavas?"
        if rng.random() < 0.3:
            pointed = rng.randrange(spots)
            prompt += "\n" + rng.choice(DIG_HINTS).format(mark=f"el {pointed + 1}.º")
        built.append(
            Round(
                prompt,
                [f"{i + 1}. {mark}" for i, mark in enumerate(options)],
                answer,
                penalty,
                neutral,
            )
        )
    return header, built


def _slackers(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = []
    for number in range(1, rounds + 1):
        names = rng.sample(WORKER_NAMES, 5)
        lines = rng.sample(WORKING, 4)
        culprit = rng.randrange(5)
        lines.insert(culprit, rng.choice(SLACKING))
        text = "\n".join(f"**{name}**: {line}" for name, line in zip(names, lines, strict=True))
        built.append(
            Round(f"👷 Ronda {number}/{rounds}. ¿Quién se escaquea?\n{text}", names, [culprit])
        )
    return "🦺 Eres el encargado. Pilla al que no da palo al agua.", built


def _overruns(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = []
    for number in range(1, rounds + 1):
        items = rng.sample(BUDGET_ITEMS, 5)
        inflated = rng.randrange(5)
        lines = []
        for i, (name, price) in enumerate(items):
            factor = rng.uniform(6, 12) if i == inflated else rng.uniform(0.85, 1.2)
            amount = f"{round(price * factor):,}".replace(",", ".")
            lines.append(f"`{i + 1}.` {name} · **{amount} Y$**")
        built.append(
            Round(
                f"📑 Presupuesto {number}/{rounds} del ayuntamiento. ¿Qué partida lleva "
                f"sobrecoste?\n" + "\n".join(lines),
                [f"{i + 1}. {name}"[:80] for i, (name, _price) in enumerate(items)],
                [inflated],
            )
        )
    return (
        "🏗️ Eres constructor. Antes de firmar, encuentra la partida inflada "
        "(o hazte el loco y que la encuentre la UCO).",
        built,
    )


def _memory(rng: random.Random, pack_key: str, length: int, rounds: int) -> tuple[str, list[Round]]:
    pack = MEMORY_PACKS[pack_key]
    built = []
    for number in range(rounds):
        size = min(len(pack.items), max(5, length + 2))
        shown = rng.sample(pack.items, size)
        sequence = rng.sample(range(size), min(length + number // 2, size))
        labels = [f"{emoji} {name}" for emoji, name in shown]
        reveal = (
            pack.intro.format(n=rng.randint(1, 30))
            + "\n# "
            + " → ".join(shown[i][0] for i in sequence)
        )
        reveal += "\n-# " + " → ".join(shown[i][1] for i in sequence)
        built.append(
            Round(
                f"🧠 Ronda {number + 1}/{rounds}. {pack.ask}",
                labels,
                sequence,
                reveal=reveal,
            )
        )
    return "", built


def _dialogue(rng: random.Random, pack_key: str, rounds: int) -> tuple[str, list[Round]]:
    pack = DIALOGUE_PACKS[pack_key]
    built = []
    for number, item in enumerate(rng.sample(pack.items, min(rounds, len(pack.items))), 1):
        options, answer = _shuffled(rng, [item.good], list(item.bad))
        letters = "ABC"
        body = "\n".join(f"**{letters[i]}.** {text}" for i, text in enumerate(options))
        built.append(
            Round(
                f"💬 {number}/{rounds}. {item.situation}\n{body}",
                list(letters[: len(options)]),
                answer,
            )
        )
    return pack.rule, built


def rounds_for(position: Position) -> int:
    """Rondas del minijuego de un puesto (más tiempo, más rondas)."""
    if position.mechanic is Mechanic.DIG:
        return 8 if position.seconds <= 30 else 10
    if position.mechanic is Mechanic.SPOT:
        return 6
    if position.mechanic is Mechanic.MEMORY:
        return {30: 3, 40: 4}.get(position.seconds, 5)
    return 5 if position.seconds <= 40 else 6


def new_game(
    position: Position, rng: random.Random, *, now: float, tired: bool = False
) -> MiniGame:
    """Prepara el minijuego de un puesto.

    Args:
        tired: Reventado: el reloj corre con un 30 % menos de tiempo.
    """
    rounds = rounds_for(position)
    kind, _, arg = position.content.partition(":")
    if position.mechanic is Mechanic.DIG:
        header, built = _dig(rng, int(arg or 3), rounds)
    elif position.mechanic is Mechanic.SPOT:
        header, built = (_overruns if kind == "sobrecostes" else _slackers)(rng, rounds)
    elif position.mechanic is Mechanic.MEMORY:
        header, built = _memory(rng, kind, int(arg or 3), rounds)
    else:
        header, built = _dialogue(rng, kind, rounds)
    seconds = position.seconds * (TIRED_TIME_FACTOR if tired else 1)
    return MiniGame(position.mechanic, header, built, seconds, now)
