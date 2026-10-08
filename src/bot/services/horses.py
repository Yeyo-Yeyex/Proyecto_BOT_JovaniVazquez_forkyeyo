"""Reglas de las carreras de caballos: el establo, la carrera, las cuotas y los boletos.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.horses`) abre la carrera,
cobra y paga a través de la economía y pinta; aquí solo se decide quién gana y
cuánto paga cada boleto.

**Los caballos no son iguales.** Cada uno tiene cinco rasgos fijos (`Horse`):

- `speed`: velocidad de crucero.
- `stamina`: hasta qué parte de una milla aguanta esa velocidad. A partir de
  ahí se cansa y pierde hasta un `FADE` de velocidad al final. En carreras
  largas el punto de cansancio llega antes (`effective_stamina`).
- `start`: cómo sale de los cajones. Decide quién va delante al principio.
- `going`: el terreno en el que rinde mejor (seco, blando o barro).
- `consistency`: cuánto cambia su día bueno de su día malo. Un caballo
  irregular sorprende a los favoritos o se hunde.

**La carrera se simula por tramos** (`SEGMENTS` tramos iguales). En cada uno,
la velocidad de cada caballo sale de sus rasgos, del terreno, de su forma del
día y de un poco de azar. Además pueden pasar cosas raras: un tropiezo, un
caballo que se desboca o que empiece a llover a mitad de carrera y la pista
se ablande. El orden de llegada sale de los tiempos.

**Las cuotas salen de simular esa misma carrera** `ODDS_TRIALS` veces antes de
abrir las apuestas (`estimate`), con su terreno, su distancia, sus caballos
cansados y su probabilidad de lluvia. La cuota de cada boleto es su retorno
(`RTP`) entre su probabilidad. Nadie fija el 25 %: el favorito paga poco y el
caballo flojo mucho, y las dos cuotas dicen la verdad. Ninguna estrategia
gana a la larga; lo que cambia es el riesgo que eliges.

La simulación de las cuotas usa un azar distinto del de la carrera de verdad,
así que la estimación no sabe nada del resultado. El error de estimar con
muestras hace que algún boleto raro pague algo más o algo menos de lo justo;
con `ODDS_TRIALS` tiradas ese error es de pocas centésimas, muy por debajo del
margen de la casa.

Las cuotas se guardan en centésimas enteras (`420` = 4,20x), como el Crash,
para que los pagos no dependan de redondeos de coma flotante.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

GAME = "caballos"

#: Tramos de la simulación. Diez basta para que se vean adelantamientos.
SEGMENTS = 10
#: Metros de la milla de referencia: el aguante (`stamina`) se mide en ella.
REFERENCE_DISTANCE = 1_600
#: Largo de un caballo, en metros: la unidad de las ventajas («a dos cuerpos»).
HORSE_LENGTH = 2.4
#: Velocidad media de un caballo de carreras, en m/s (solo da escala al tiempo).
BASE_SPEED = 16.0

#: Velocidad que pierde al final quien ya no aguanta (3 %).
FADE = 0.03
#: Desviación de la forma del día según la regularidad (de 0 a 1).
FORM_SD_MIN = 0.002
FORM_SD_MAX = 0.006
#: Azar de cada tramo.
SEGMENT_SD = 0.006
#: Ventaja de la salida en los dos primeros tramos: ±2,5 % y ±1 %.
START_EDGE = (0.05, 0.02)
#: Ventaja por terreno según la distancia al preferido (0, 1 o 2 escalones).
GOING_EDGE = (0.004, 0.0, -0.005)
#: Lo que pierde un caballo que corrió hace poco.
TIRED_PENALTY = 0.003
#: Tropiezo: probabilidad por caballo y carrera y velocidad del tramo en que tropieza.
STUMBLE_CHANCE = 0.03
STUMBLE_SPEED = 0.90
#: Caballo desbocado: probabilidad por caballo y carrera y velocidad desde que se va.
BOLT_CHANCE = 0.004
BOLT_SPEED = 0.70
#: Tramo en el que empieza a llover, si llueve.
RAIN_SEGMENT = 5

#: Carreras simuladas para estimar las cuotas.
ODDS_TRIALS = 80_000

#: Retorno al jugador de cada tipo de boleto. La casa se queda más en los
#: boletos combinados, como en las quinielas hípicas de verdad.
RTP = {
    "ganador": 0.95,
    "colocado": 0.95,
    "gemela": 0.92,
    "trio": 0.90,
}
#: La cuota más baja (1,01x: si no, no ganas nada) y la más alta (×5.000).
MIN_ODDS = 101
MAX_ODDS = 500_000

#: Caballos en una carrera normal y en el Gran Premio.
FIELD_SIZE = 6
GRAND_PRIX_FIELD = 8

#: Distancias de una carrera normal y la del Gran Premio, en metros.
DISTANCES = (1_200, 1_600, 2_000)
GRAND_PRIX_DISTANCE = 2_400

#: Probabilidades de lluvia que puede anunciar el parte.
RAIN_CHANCES = (0.0, 0.0, 0.1, 0.2, 0.3, 0.5)


class HorseError(ValueError):
    """Boleto o carrera no válidos; el mensaje se puede enseñar al usuario."""


# -- Terreno ---------------------------------------------------------------------------


class Going(Enum):
    """Estado de la pista. El orden importa: cada escalón está más mojado."""

    SECO = ("seco", "☀️", "Seco")
    BLANDO = ("blando", "🌦️", "Blando")
    BARRO = ("barro", "🌧️", "Barro")

    def __init__(self, key: str, emoji: str, label: str) -> None:
        self.key = key
        self.emoji = emoji
        self.label = label

    @property
    def index(self) -> int:
        """Escalón de humedad: 0 seco, 1 blando, 2 barro."""
        return list(Going).index(self)

    def wetter(self) -> Going:
        """El terreno tras llover (el barro no se moja más)."""
        order = list(Going)
        return order[min(self.index + 1, len(order) - 1)]


# -- Establo ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Horse:
    """Un caballo del establo. Sus rasgos no cambian nunca.

    Attributes:
        key: Identificador estable (se guarda en la base de datos y en los logros).
        name: Nombre que se enseña.
        silks: Colores de la chaquetilla del jinete (principal, secundario).
        pattern: Dibujo de la chaquetilla: `liso`, `banda`, `rayas` o `lunares`.
        speed: Velocidad de crucero relativa (1 = la media).
        stamina: Parte de una milla que aguanta sin cansarse (1 = toda).
        start: Salida, de 0 (se duerme) a 1 (un cohete).
        going: Terreno preferido.
        consistency: Regularidad, de 0 (siempre igual) a 1 (una lotería).
        quote: Lo que dice su preparador; sale en la ficha.
    """

    key: str
    name: str
    silks: tuple[tuple[int, int, int], tuple[int, int, int]]
    pattern: str
    speed: float
    stamina: float
    start: float
    going: Going
    consistency: float
    quote: str


_S, _B, _M = Going.SECO, Going.BLANDO, Going.BARRO

#: El establo: los mismos 16 caballos en todos los servidores. Cada servidor
#: guarda su propia forma (`HorseRecord`). Las claves no se cambian nunca.
STABLE: tuple[Horse, ...] = (
    Horse("falcon", "Falcon Presidencial", ((235, 235, 240), (200, 30, 45)), "banda",
          1.0075, 0.55, 0.95, _S, 0.35, "Despega antes que nadie y aterriza donde le da la gana."),
    Horse("manual", "Manual de Resistencia", ((200, 30, 45), (250, 250, 250)), "rayas",
          1.0025, 1.00, 0.15, _B, 0.15, "Siempre lo dan por muerto. Siempre."),
    Horse("paguita", "La Paguita", ((40, 150, 90), (250, 220, 60)), "liso",
          1.0032, 0.80, 0.50, _B, 0.05, "Llega puntual todos los meses. Ni antes ni después."),
    Horse("gofio", "Gofio Express", ((250, 200, 40), (40, 100, 200)), "banda",
          1.0040, 0.80, 0.60, _S, 0.30, "Desayuna gofio con leche y sale disparado."),
    Horse("fango", "Máquina del Fango", ((110, 80, 50), (30, 30, 30)), "lunares",
          1.0018, 0.90, 0.40, _M, 0.40, "En seco no vale nada. En barro, vuela."),
    Horse("puerta", "Puerta Giratoria", ((150, 90, 220), (250, 250, 250)), "rayas",
          1.0047, 0.70, 0.50, _S, 0.95, "Nunca sabes por dónde va a salir."),
    Horse("pegasus", "Pegasus", ((20, 20, 30), (60, 220, 120)), "liso",
          1.0069, 0.60, 0.70, _S, 0.70, "Sabe lo que vas a apostar antes que tú."),
    Horse("uco", "La UCO", ((40, 90, 50), (220, 200, 120)), "banda",
          1.0015, 0.98, 0.35, _M, 0.20, "Lenta, pero al final siempre llega."),
    Horse("colchon", "Colchón de la Moncloa", ((240, 240, 250), (90, 140, 230)), "rayas",
          1.0031, 0.75, 0.50, _B, 0.45, "Recién estrenado y descansadísimo."),
    Horse("rodalies", "Rodalies con Retraso", ((250, 140, 30), (250, 250, 250)), "banda",
          1.0020, 0.95, 0.00, _B, 0.55, "Sale tarde. Siempre. Es su estilo."),
    Horse("timple", "Timple Veloz", ((230, 60, 140), (250, 220, 60)), "lunares",
          1.0053, 0.65, 0.80, _S, 0.45, "Cinco cuerdas y cuatro patas, todas afinadas."),
    Horse("robuso", "Robuso de Hong Kong", ((210, 20, 30), (250, 210, 40)), "liso",
          1.0032, 0.82, 0.55, _M, 0.50, "Entrena en el monzón. El barro le sabe a casa."),
    Horse("wepa", "Wepa Boricua", ((30, 80, 200), (230, 40, 50)), "rayas",
          1.0065, 0.58, 0.85, _S, 0.60, "¡Wepa! Sale como un cohete y luego ya veremos."),
    Horse("nextgen", "Fondos Next Gen", ((30, 60, 150), (250, 210, 0)), "lunares",
          1.0020, 0.85, 0.45, _B, 0.85, "Llegan, pero nadie sabe cuándo."),
    Horse("amnistia", "Amnistía Total", ((250, 250, 250), (230, 120, 30)), "banda",
          1.0047, 0.72, 0.60, _B, 0.50, "Le perdonan todas las salidas en falso."),
    Horse("bono", "Bono Social", ((90, 190, 200), (40, 40, 60)), "liso",
          1.0020, 0.88, 0.45, _M, 0.30, "Humilde, pero en barro tira de orgullo."),
)  # fmt: skip
STABLE_BY_KEY: dict[str, Horse] = {h.key: h for h in STABLE}

#: Nombres de las carreras normales. El Gran Premio tiene el suyo.
RACE_NAMES = (
    "Premio Consejo de Ministros",
    "Premio Puerta del Sol",
    "Handicap de la Moncloa",
    "Premio Bono Transporte",
    "Clásico del Teide",
    "Premio Comisión de Investigación",
    "Copa del Mojo Picón",
    "Premio Rueda de Prensa sin Preguntas",
    "Premio Decreto Ómnibus",
    "Milla del Barranco",
    "Premio Presupuestos Prorrogados",
    "Premio Wepa de San Juan",
)
GRAND_PRIX_NAME = "Gran Premio Perro Sanxe"


def effective_stamina(horse: Horse, distance: int) -> float:
    """Parte de la carrera que aguanta sin cansarse (`stamina` escalado a la distancia)."""
    return max(0.25, min(1.0, horse.stamina * REFERENCE_DISTANCE / distance))


def place_slots(field_size: int) -> int:
    """Puestos que pagan «colocado»: 2 con menos de 8 caballos y 3 con 8 o más.

    Es la regla de las apuestas «each way» de los hipódromos británicos.
    """
    return 3 if field_size >= 8 else 2


# -- La carrera ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RaceCard:
    """Lo que se sabe de una carrera antes de salir.

    Attributes:
        horses: Caballos en orden de cajón (el dorsal es la posición + 1).
        going: Terreno a la salida.
        distance: Metros.
        rain_chance: Probabilidad de que llueva a mitad de carrera.
        tired: Claves de los caballos que corrieron hace poco.
        name: Nombre de la carrera.
        grand_prix: Si es el Gran Premio.
    """

    horses: tuple[Horse, ...]
    going: Going
    distance: int
    rain_chance: float = 0.0
    tired: frozenset[str] = frozenset()
    name: str = "Premio"
    grand_prix: bool = False

    @property
    def size(self) -> int:
        """Caballos en la carrera."""
        return len(self.horses)


@dataclass(frozen=True, slots=True)
class RaceResult:
    """Cómo acabó una carrera.

    Attributes:
        order: Índices de cajón en orden de llegada.
        times: Tiempo de cada caballo (por índice de cajón), en segundos.
        splits: Tiempo acumulado al final de cada tramo, por caballo
            (`SEGMENTS + 1` valores, el primero 0). Sirve para dibujar.
        stumbles: Tramo en el que tropezó cada caballo que tropezó.
        bolted: Tramo desde el que se desbocó cada caballo desbocado.
        rained: Si llovió a mitad de carrera.
    """

    order: tuple[int, ...]
    times: tuple[float, ...]
    splits: tuple[tuple[float, ...], ...]
    stumbles: dict[int, int] = field(default_factory=dict)
    bolted: dict[int, int] = field(default_factory=dict)
    rained: bool = False

    @property
    def winner(self) -> int:
        """Índice de cajón del ganador."""
        return self.order[0]

    def position(self, index: int) -> int:
        """Puesto (1 = primero) del caballo de cajón `index`."""
        return self.order.index(index) + 1

    def lengths_behind(self, index: int, distance: int) -> float:
        """Cuerpos que le saca el ganador al caballo `index` en la meta."""
        winner_time = self.times[self.winner]
        speed = distance / winner_time
        return (self.times[index] - winner_time) * speed / HORSE_LENGTH

    def position_at(self, index: int, seconds: float, distance: int) -> float:
        """Metros recorridos por el caballo `index` a los `seconds` de carrera.

        Pasada la meta sigue corriendo al ritmo de su último tramo, para que
        el dibujo no se pare en seco.
        """
        splits = self.splits[index]
        step = distance / SEGMENTS
        if seconds <= 0:
            return 0.0
        for segment in range(SEGMENTS):
            start, end = splits[segment], splits[segment + 1]
            if seconds <= end:
                return step * (segment + (seconds - start) / (end - start))
        last = splits[-1] - splits[-2]
        return distance + (seconds - splits[-1]) * step / last

    def leader_at(self, seconds: float, distance: int) -> int:
        """Índice del caballo que va primero a los `seconds` de carrera."""
        return max(
            range(len(self.times)),
            key=lambda i: (self.position_at(i, seconds, distance), -self.times[i]),
        )


def _speeds(card: RaceCard, rng: np.random.Generator, trials: int) -> tuple[np.ndarray, dict]:
    """Velocidades de cada caballo en cada tramo de `trials` carreras a la vez.

    Devuelve un array `(trials, caballos, tramos)` en m/s y los sucesos raros
    (tropiezos, desbocados y lluvia) para poder contarlos en la narración.
    """
    horses = card.horses
    size = len(horses)
    k = SEGMENTS
    speed = np.array([h.speed for h in horses], dtype=np.float64)
    stamina = np.array([effective_stamina(h, card.distance) for h in horses])
    start = np.array([h.start for h in horses])
    form_sd = np.array([FORM_SD_MIN + (FORM_SD_MAX - FORM_SD_MIN) * h.consistency for h in horses])
    tired = np.array([TIRED_PENALTY if h.key in card.tired else 0.0 for h in horses])
    preferred = np.array([h.going.index for h in horses])

    base = speed - tired  # (size,)
    # Cansancio: a partir de su punto de aguante pierde hasta FADE al final.
    middle = (np.arange(k) + 0.5) / k  # (k,)
    over = np.clip(middle[None, :] - stamina[:, None], 0, None) / np.maximum(
        1 - stamina[:, None], 1e-9
    )
    fatigue = 1 - FADE * over  # (size, k)
    # Salida: solo los dos primeros tramos.
    launch = np.zeros((size, k))
    for segment, edge in enumerate(START_EDGE):
        launch[:, segment] = edge * (start - 0.5)

    def going_edge(going_index: np.ndarray) -> np.ndarray:
        steps = np.abs(going_index - preferred[None, :])  # (trials, size)
        return np.choose(np.minimum(steps, 2), GOING_EDGE)

    rain = rng.random(trials) < card.rain_chance  # (trials,)
    dry_index = np.full((trials, size), card.going.index)
    wet_index = np.full((trials, size), card.going.wetter().index)
    edge_before = going_edge(dry_index)  # (trials, size)
    edge_after = np.where(rain[:, None], going_edge(wet_index), edge_before)
    after_rain = np.arange(k) >= RAIN_SEGMENT  # (k,)
    going = np.where(after_rain[None, None, :], edge_after[:, :, None], edge_before[:, :, None])

    form = rng.standard_normal((trials, size)) * form_sd[None, :]
    # float32: la mitad de memoria y de tiempo, y de sobra para unas centésimas.
    noise = rng.standard_normal((trials, size, k), dtype=np.float32) * np.float32(SEGMENT_SD)
    v = (base[None, :, None] + going + form[:, :, None] + launch[None, :, :] + noise) * fatigue[
        None, :, :
    ]

    stumble = rng.random((trials, size)) < STUMBLE_CHANCE
    stumble_at = rng.integers(0, k, (trials, size))
    hit = stumble[:, :, None] & (np.arange(k)[None, None, :] == stumble_at[:, :, None])
    v = np.where(hit, v * STUMBLE_SPEED, v)

    bolt = rng.random((trials, size)) < BOLT_CHANCE
    bolt_at = rng.integers(2, k, (trials, size))
    gone = bolt[:, :, None] & (np.arange(k)[None, None, :] >= bolt_at[:, :, None])
    v = np.where(gone, v * BOLT_SPEED, v)

    events = {
        "rain": rain,
        "stumble": stumble,
        "stumble_at": stumble_at,
        "bolt": bolt,
        "bolt_at": bolt_at,
    }
    return v * BASE_SPEED, events


def run_race(card: RaceCard, rng: np.random.Generator) -> RaceResult:
    """Corre la carrera de verdad, con el azar de `rng`."""
    v, events = _speeds(card, rng, 1)
    step = card.distance / SEGMENTS
    segment_times = step / v[0]  # (size, k)
    splits = np.concatenate([np.zeros((card.size, 1)), np.cumsum(segment_times, axis=1)], axis=1)
    times = splits[:, -1]
    # Empate exacto (no pasa nunca con flotantes): decide el cajón.
    order = tuple(int(i) for i in np.lexsort((np.arange(card.size), times)))
    return RaceResult(
        order=order,
        times=tuple(float(t) for t in times),
        splits=tuple(tuple(float(s) for s in row) for row in splits),
        stumbles={
            int(i): int(events["stumble_at"][0, i])
            for i in range(card.size)
            if events["stumble"][0, i]
        },
        bolted={
            int(i): int(events["bolt_at"][0, i]) for i in range(card.size) if events["bolt"][0, i]
        },
        rained=bool(events["rain"][0]),
    )


# -- Probabilidades y cuotas -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Odds:
    """Probabilidades estimadas de una carrera y las cuotas que salen de ellas.

    Attributes:
        win: Probabilidad de ganar de cada caballo.
        place: Probabilidad de quedar colocado de cada caballo.
        exacta: Probabilidad de cada par (1º, 2º), con índice `1º * n + 2º`.
        trifecta: Probabilidad de cada trío en orden, con índice
            `1º * n² + 2º * n + 3º`.
    """

    win: tuple[float, ...]
    place: tuple[float, ...]
    exacta: tuple[float, ...]
    trifecta: tuple[float, ...]

    @property
    def size(self) -> int:
        """Caballos en la carrera."""
        return len(self.win)

    def probability(self, pick: Pick) -> float:
        """Probabilidad estimada de que el boleto `pick` cobre."""
        n = self.size
        a = pick.horses
        if pick.kind is BetKind.WIN:
            return self.win[a[0]]
        if pick.kind is BetKind.PLACE:
            return self.place[a[0]]
        if pick.kind is BetKind.EXACTA:
            return self.exacta[a[0] * n + a[1]]
        return self.trifecta[a[0] * n * n + a[1] * n + a[2]]

    def odds(self, pick: Pick) -> int:
        """Cuota del boleto en centésimas (`420` = paga 4,20 veces lo apostado)."""
        return odds_cents(self.probability(pick), RTP[pick.kind.key])

    def favourite(self) -> int:
        """Índice del favorito (el de más probabilidad de ganar)."""
        return max(range(self.size), key=lambda i: self.win[i])


def odds_cents(probability: float, rtp: float) -> int:
    """Cuota en centésimas para una probabilidad y un retorno, con sus topes."""
    if probability <= 0:
        return MAX_ODDS
    return max(MIN_ODDS, min(MAX_ODDS, math.floor(100 * rtp / probability)))


def estimate(card: RaceCard, rng: np.random.Generator, trials: int = ODDS_TRIALS) -> Odds:
    """Simula `trials` veces la carrera y cuenta cuántas veces pasa cada cosa.

    Es la parte cara (~0,1-0,3 s): el cog la llama fuera del event loop.
    """
    v, _events = _speeds(card, rng, trials)
    times = (card.distance / SEGMENTS / v).sum(axis=2)  # (trials, size)
    order = np.argsort(times, axis=1)
    n = card.size
    win = np.bincount(order[:, 0], minlength=n) / trials
    slots = place_slots(n)
    place = np.bincount(order[:, :slots].ravel(), minlength=n) / trials
    exacta = np.bincount(order[:, 0] * n + order[:, 1], minlength=n * n) / trials
    trifecta = (
        np.bincount(order[:, 0] * n * n + order[:, 1] * n + order[:, 2], minlength=n**3) / trials
    )
    return Odds(
        win=tuple(float(x) for x in win),
        place=tuple(float(x) for x in place),
        exacta=tuple(float(x) for x in exacta),
        trifecta=tuple(float(x) for x in trifecta),
    )


def format_odds(cents: int) -> str:
    """`420` → `4,20x`; de 100x para arriba, sin decimales (`1.250x`)."""
    if cents >= 10_000:
        return f"{cents // 100:,}x".replace(",", ".")
    return f"{cents // 100},{cents % 100:02d}x"


def payout(stake: int, cents: int) -> int:
    """Lo que devuelve un boleto ganador (apuesta incluida), redondeado hacia abajo."""
    return stake * cents // 100


# -- Boletos ---------------------------------------------------------------------------------


class BetKind(Enum):
    """Tipo de boleto: cuántos caballos se eligen y qué tienen que hacer."""

    WIN = ("ganador", "🥇", "Ganador", 1)
    PLACE = ("colocado", "🎗️", "Colocado", 1)
    EXACTA = ("gemela", "🥈", "Gemela", 2)
    TRIFECTA = ("trio", "🏆", "Trío", 3)

    def __init__(self, key: str, emoji: str, label: str, picks: int) -> None:
        self.key = key
        self.emoji = emoji
        self.label = label
        #: Caballos que hay que elegir.
        self.picks = picks

    def explain(self, field_size: int) -> str:
        """Qué tiene que pasar para cobrar, en una línea."""
        if self is BetKind.WIN:
            return "Tu caballo gana."
        if self is BetKind.PLACE:
            return f"Tu caballo queda entre los {place_slots(field_size)} primeros."
        if self is BetKind.EXACTA:
            return "Aciertas el 1º y el 2º, en orden."
        return "Aciertas el podio entero, en orden."


BET_KINDS: dict[str, BetKind] = {k.key: k for k in BetKind}
#: Formas de escribir el tipo en `.caballo`.
_KIND_WORDS = {
    "ganador": BetKind.WIN,
    "gana": BetKind.WIN,
    "g": BetKind.WIN,
    "colocado": BetKind.PLACE,
    "coloca": BetKind.PLACE,
    "c": BetKind.PLACE,
    "place": BetKind.PLACE,
    "gemela": BetKind.EXACTA,
    "trio": BetKind.TRIFECTA,
    "trío": BetKind.TRIFECTA,
}


@dataclass(frozen=True, slots=True)
class Pick:
    """Lo que dice un boleto: el tipo y los caballos (índices de cajón, en orden)."""

    kind: BetKind
    horses: tuple[int, ...]

    def wins(self, order: tuple[int, ...]) -> bool:
        """Si el boleto cobra con este orden de llegada."""
        if self.kind is BetKind.WIN:
            return order[0] == self.horses[0]
        if self.kind is BetKind.PLACE:
            return self.horses[0] in order[: place_slots(len(order))]
        return tuple(order[: self.kind.picks]) == self.horses

    def numbers(self) -> str:
        """Dorsales del boleto: `3` o `3-5-1`."""
        return "-".join(str(i + 1) for i in self.horses)

    def label(self) -> str:
        """`🥇 Ganador 3` o `🏆 Trío 3-5-1`."""
        return f"{self.kind.emoji} {self.kind.label} {self.numbers()}"


_NUMBERS = re.compile(r"^\d+(?:[-/,.]\d+){0,2}$")


def parse_pick(text: str, field_size: int, kind_text: str | None = None) -> Pick:
    """Interpreta una elección escrita: `3`, `3c`, `3-5`, `3-5-1` y, opcional, el tipo.

    Sin tipo, un número es «ganador», dos son «gemela» y tres, «trío». `3c` o
    el tipo `colocado` lo cambian a colocado.

    Raises:
        HorseError: Si los dorsales no existen, se repiten o no casan con el tipo.
    """
    text = text.strip().lower()
    kind: BetKind | None = None
    if text.endswith("c") and text[:-1].isdigit():
        kind = BetKind.PLACE
        text = text[:-1]
    if kind_text:
        word = kind_text.strip().lower()
        if word not in _KIND_WORDS:
            raise HorseError("Tipo de boleto desconocido: ganador, colocado, gemela o trío.")
        kind = _KIND_WORDS[word]
    if not _NUMBERS.match(text):
        raise HorseError("Elige caballos por su dorsal: `3`, `3-5` o `3-5-1`.")
    numbers = [int(n) for n in re.split(r"[-/,.]", text)]
    if any(not 1 <= n <= field_size for n in numbers):
        raise HorseError(f"En esta carrera corren los dorsales del 1 al {field_size}.")
    if len(set(numbers)) != len(numbers):
        raise HorseError("No puedes repetir caballo en un boleto.")
    if kind is None:
        kind = {1: BetKind.WIN, 2: BetKind.EXACTA, 3: BetKind.TRIFECTA}[len(numbers)]
    if len(numbers) != kind.picks:
        raise HorseError(f"Un boleto de {kind.label.lower()} lleva {kind.picks} caballo(s).")
    return Pick(kind, tuple(n - 1 for n in numbers))


# -- Narración y ventajas --------------------------------------------------------------------


def margin_text(lengths: float) -> str:
    """Ventaja en el idioma del hipódromo: «una nariz», «una cabeza», «dos cuerpos»."""
    if lengths < 0.05:
        return "un hocico"
    if lengths < 0.15:
        return "una nariz"
    if lengths < 0.3:
        return "una cabeza"
    if lengths < 0.45:
        return "un cuello"
    if lengths < 0.8:
        return "medio cuerpo"
    if lengths < 1.25:
        return "un cuerpo"
    whole = round(lengths)
    words = {2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete", 8: "ocho"}
    return f"{words.get(whole, str(whole))} cuerpos"


#: Ventaja por debajo de la cual hay foto-finish (en cuerpos).
PHOTO_FINISH_LENGTHS = 0.25


def photo_finish(result: RaceResult, distance: int) -> bool:
    """Si el 1º y el 2º llegan tan juntos que hace falta la foto."""
    return result.lengths_behind(result.order[1], distance) < PHOTO_FINISH_LENGTHS


def comeback(result: RaceResult, distance: int, index: int) -> bool:
    """Si el caballo `index` iba último a mitad de carrera."""
    half = result.splits[index][SEGMENTS // 2]
    positions = [result.position_at(i, half, distance) for i in range(len(result.times))]
    return positions[index] <= min(positions)


@dataclass(frozen=True, slots=True)
class Tip:
    """El pronóstico de Perro Sanxe: un caballo y lo seguro que está."""

    horse: int
    confidence: int


def sanxe_tip(odds: Odds, rng: np.random.Generator) -> Tip:
    """Pronóstico de Perro Sanxe: muy seguro de sí mismo y bastante peor que las cuotas.

    Elige con probabilidades más planas que las de verdad (raíz cuadrada), así
    que a menudo no va con el favorito. La seguridad que anuncia es un número
    alto sin relación con nada, como en las encuestas.
    """
    weights = np.sqrt(np.array(odds.win))
    weights /= weights.sum()
    horse = int(rng.choice(len(weights), p=weights))
    return Tip(horse=horse, confidence=int(rng.integers(87, 100)))


def form_line(form: tuple[int, ...]) -> str:
    """Últimos puestos, el más reciente a la derecha: `3-1-5-2-1`. `—` si es nuevo."""
    return "-".join(str(p) for p in form) if form else "—"


@dataclass(frozen=True, slots=True)
class HorseRecord:
    """Lo que un servidor sabe de un caballo: su historial en ese servidor.

    Attributes:
        races: Carreras corridas.
        wins: Victorias.
        places: Veces en el podio (los tres primeros).
        form: Últimos puestos, el más reciente al final (como mucho `FORM_SIZE`).
        last_race: Cuándo corrió por última vez (epoch, 0 si nunca).
    """

    races: int = 0
    wins: int = 0
    places: int = 0
    form: tuple[int, ...] = ()
    last_race: float = 0.0


#: Puestos que se recuerdan de cada caballo.
FORM_SIZE = 5
#: Un caballo que corrió hace menos de esto sale cansado (🥵).
REST_SECONDS = 15 * 60


def updated_record(record: HorseRecord, position: int, now: float) -> HorseRecord:
    """El historial de un caballo tras acabar `position`º en una carrera."""
    return HorseRecord(
        races=record.races + 1,
        wins=record.wins + (position == 1),
        places=record.places + (position <= 3),
        form=(*record.form, position)[-FORM_SIZE:],
        last_race=now,
    )


def new_card(
    rng: np.random.Generator,
    records: dict[str, HorseRecord],
    *,
    now: float,
    grand_prix: bool = False,
) -> RaceCard:
    """Sortea una carrera: caballos, terreno, distancia, parte del tiempo y nombre.

    Los caballos que corrieron hace menos de `REST_SECONDS` pueden salir, pero
    cansados (la cuota ya lo tiene en cuenta).
    """
    size = GRAND_PRIX_FIELD if grand_prix else FIELD_SIZE
    chosen = rng.choice(len(STABLE), size=size, replace=False)
    horses = tuple(STABLE[int(i)] for i in chosen)
    going = list(Going)[int(rng.choice(3, p=[0.5, 0.35, 0.15]))]
    rain = float(rng.choice(RAIN_CHANCES)) if going is not Going.BARRO else 0.0
    distance = GRAND_PRIX_DISTANCE if grand_prix else int(rng.choice(DISTANCES))
    tired = frozenset(
        h.key
        for h in horses
        if (record := records.get(h.key)) is not None
        and record.last_race
        and now - record.last_race < REST_SECONDS
    )
    name = GRAND_PRIX_NAME if grand_prix else RACE_NAMES[int(rng.integers(len(RACE_NAMES)))]
    return RaceCard(
        horses=horses,
        going=going,
        distance=distance,
        rain_chance=rain,
        tired=tired,
        name=name,
        grand_prix=grand_prix,
    )


# -- Gran Premio ---------------------------------------------------------------------------------

#: Carreras normales que tienen que correrse entre dos Grandes Premios.
GRAND_PRIX_EVERY = 8
#: Tiempo mínimo entre dos Grandes Premios del mismo servidor (4 h).
GRAND_PRIX_COOLDOWN = 4 * 3600
#: Bote con el que arranca el Gran Premio y lo que crece cada vez que queda
#: desierto, hasta el tope. Sale de la nada (ver el cog).
GRAND_PRIX_POT = 2_500
GRAND_PRIX_POT_STEP = 2_500
GRAND_PRIX_POT_MAX = 25_000
#: Apuesta mínima para entrar en el reparto del bote: una tirada.
GRAND_PRIX_MIN_STAKE = 100


def grand_prix_due(
    *, since: int, last: float, now: float, cooldown: float = GRAND_PRIX_COOLDOWN
) -> bool:
    """Si toca Gran Premio: `GRAND_PRIX_EVERY` carreras y `cooldown` desde el último."""
    return since >= GRAND_PRIX_EVERY and now - last >= cooldown


def pot_eligible(pick: Pick, stake: int, order: tuple[int, ...]) -> bool:
    """Si un boleto entra en el reparto del bote: acierta el ganador y apuesta lo bastante.

    Cuentan el ganador, la gemela y el trío acertados (todos aciertan quién
    gana); el colocado no. Una persona lleva un solo boleto por carrera, así
    que nadie puede cubrir todos los caballos para quedarse el bote.
    """
    return stake >= GRAND_PRIX_MIN_STAKE and pick.kind is not BetKind.PLACE and pick.wins(order)


def next_pot(pot: int, *, won: bool) -> int:
    """El bote del próximo Gran Premio: vuelve al inicio si se lo llevan, crece si no."""
    if won:
        return GRAND_PRIX_POT
    return min(GRAND_PRIX_POT_MAX, pot + GRAND_PRIX_POT_STEP)


# -- Narración y casi aciertos ------------------------------------------------------------------


def _name(card: RaceCard, index: int) -> str:
    return f"**{card.horses[index].name}**"


def commentary(card: RaceCard, result: RaceResult) -> list[str]:
    """La carrera contada por la radio, en pocas líneas y en orden.

    Sale del resultado ya decidido: no hay nada que adivinar, solo contar
    quién iba delante en cada tramo y lo que pasó por el camino.
    """
    distance = card.distance
    lines: list[str] = []

    def leader_at_segment(segment: int) -> int:
        # El instante en que el primero de ese momento cierra el tramo.
        moment = min(result.splits[i][segment] for i in range(card.size))
        return result.leader_at(moment, distance)

    first = leader_at_segment(1)
    lines.append(f"¡Se abren los cajones! {_name(card, first)} sale como un tiro.")
    for index, segment in sorted(result.stumbles.items(), key=lambda kv: kv[1]):
        lines.append(f"¡Ay! {_name(card, index)} tropieza en el tramo {segment + 1}.")
    if result.rained:
        lines.append(f"Empieza a llover: la pista pasa a {card.going.wetter().label.lower()}.")
    middle = leader_at_segment(SEGMENTS // 2)
    if middle != first:
        lines.append(f"A mitad de carrera manda {_name(card, middle)}.")
    else:
        lines.append(f"{_name(card, first)} sigue delante a mitad de carrera.")
    for index in result.bolted:
        lines.append(f"¡{_name(card, index)} se ha desbocado y se va hacia la grada!")
    stretch = leader_at_segment(SEGMENTS - 2)
    if stretch != middle:
        lines.append(f"¡Recta final y {_name(card, stretch)} se pone primero!")
    winner = result.winner
    margin = margin_text(result.lengths_behind(result.order[1], distance))
    if photo_finish(result, distance):
        lines.append(f"📸 ¡Foto-finish! Gana {_name(card, winner)} por {margin}.")
    elif comeback(result, distance, winner):
        lines.append(f"¡Remontada histórica! {_name(card, winner)} iba último y gana por {margin}.")
    elif winner != stretch:
        lines.append(
            f"¡Pero en los últimos metros aparece {_name(card, winner)} y gana por {margin}!"
        )
    else:
        lines.append(f"¡{_name(card, winner)} gana por {margin}!")
    return lines


def near_miss(card: RaceCard, result: RaceResult, odds: Odds, pick: Pick, stake: int) -> str | None:
    """Lo que se te escapó: cuánto habrías cobrado si tu boleto llega a acertar por poco.

    Solo para boletos que fallan por muy poco: el caballo entra justo detrás
    del puesto que pagaba, la gemela o el trío salen con los caballos buenos
    pero en otro orden. `None` si no hay nada que lamentar.
    """
    if pick.wins(result.order):
        return None
    distance = card.distance
    order = result.order
    prize = payout(stake, odds.odds(pick))
    first = pick.horses[0]
    if pick.kind is BetKind.WIN and order[1] == first:
        margin = margin_text(result.lengths_behind(first, distance))
        return f"Entró 2º a {margin}. Habrías cobrado {prize:,} Y$.".replace(",", ".")
    if pick.kind is BetKind.PLACE:
        slots = place_slots(card.size)
        if order[slots] == first:
            gap = result.lengths_behind(first, distance) - result.lengths_behind(
                order[slots - 1], distance
            )
            return (
                f"Se quedó a {margin_text(gap)} del puesto que pagaba. "
                f"Habrías cobrado {prize:,} Y$."
            ).replace(",", ".")
    if pick.kind in (BetKind.EXACTA, BetKind.TRIFECTA):
        real = tuple(order[: pick.kind.picks])
        if set(real) == set(pick.horses):
            right = Pick(pick.kind, real)
            paid = payout(stake, odds.odds(right))
            return (
                f"¡Los caballos buenos, pero en otro orden! El {right.numbers()} pagaba "
                f"{format_odds(odds.odds(right))}: {paid:,} Y$."
            ).replace(",", ".")
    return None
