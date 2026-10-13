"""Reglas del trabajo (`pala`): puestos, batería, horas extra, familia y ascensos.

Lógica pura, sin Discord ni base de datos. El catálogo de oficios está en
`bot.services.work_catalog`, los minijuegos en `bot.services.work_games` y la
persistencia en `bot.repositories.work`; `WorkService` (al final de este
módulo) junta todo con la economía.

Cómo funciona un turno, en resumen:

- Cada miembro tiene, como mucho, un contrato: un oficio y un puesto (de 1 a
  `Job.top`; nada da por hecho cuántos hay).
- Fichar lanza el minijuego del puesto. La puntuación (0–100) mueve el sueldo
  entre el 70 % y el 130 % de la base (`shift_pay`) y la barra de rendimiento.
- La **batería** baja con cada turno y se recarga sola (`battery_now`). Por
  debajo de `TIRED` estás reventado: menos tiempo en el minijuego y riesgo de
  accidente laboral. Por debajo de 0, zombi. En `BATTERY_MIN` no se ficha.
- Los primeros `ORDINARY_SHIFTS` turnos del día son jornada ordinaria. Los
  siguientes son horas extra: pagan `OVERTIME_PAY` (el art. 35.1 ET obliga a
  pagarlas al menos como la hora ordinaria; el 1,25 imita los convenios) y
  cansan más. El art. 35.2 ET las limita a 80 horas al año; en el juego,
  `LEGAL_EXTRAS_PER_WEEK` turnos extra por semana. Pasado el límite, el jefe
  ofrece pagarlas en B.
- La **familia** (0–100) no da dinero: baja con cada turno (más las extras,
  el B, las guardias, la madrugada y los domingos) y se recupera sola por horas
  (`family_now`). Su tramo (`FAMILY_TIERS`) acelera o frena la recarga de la
  batería y lo que sube la barra: quien reparte sus turnos va «en casa feliz»
  y quien encadena turnos recarga a paso de tortuga. Alimenta eventos y logros.
  A 0 llega la intervención familiar: al día siguiente, sin extras, B ni
  guardias. Los regalos de la tienda la suben y los favores (`FAVORS`) la gastan.
- **Ascensos** al estilo de los Sims (`promotion_blockers`): barra llena, unos
  días en el puesto, las tareas del puesto y, a veces, una formación.
- **Sin despidos**: con la barra a cero hay un aviso y, si se repite, te bajan
  un puesto. Tras `LEAVE_AFTER_DAYS` días sin fichar, excedencia.

Carrera larga, también al estilo de los Sims:

- **Ramas** (`Job.branches`): un oficio puede tener un tronco común
  (`Job.positions`) y, al acabarlo, dos o más ramas (`Branch`) con sus propios
  puestos hasta `Job.top`. Al ascender desde el último puesto del tronco se
  elige rama (`Contract.branch`). Si te bajan al tronco, la rama se olvida y se
  vuelve a elegir.
- **Ventajas** (`Position.perk`): cada puesto puede dar una ventaja pequeña
  (más tiempo, un fallo gratis, una respuesta tachada, menos batería) que se
  conserva mientras sigas en ese oficio en ese puesto o más arriba
  (`Job.perks`). Se suman a las herramientas de la tienda.
- **Jubilación** (`Branch.pension`): en lo más alto de una rama te puedes
  jubilar de ella. Desde entonces cobras una pensión diaria (con su botón) y
  sigues en la otra rama, desde su primer puesto. La pensión es para siempre.
- **Deseo del día** (`wish_for`, `wish_met`): cada día, un objetivo pequeño
  al azar (estable para cada miembro y día). El turno que lo cumple paga
  `WISH_PAY` veces más y sube `WISH_PERFORMANCE` la barra. Cumplirlo días
  seguidos hace racha (`Contract.wish_streak`).

Reglas propias de algunos oficios (campos de `Job`):

- **Guardias** (sanidad): turno doble aparte de la jornada (`ShiftKind.GUARD`).
  Gasta mucha batería, paga `GUARD_PAY` veces la base aunque dure el doble (la
  hora de guardia sale más barata, como en muchos servicios de salud) y mueve
  la barra el doble. No son horas extra: en el Estatuto Marco (Ley 55/2003)
  son jornada complementaria, así que no tienen límite ni B. Después te
  quedas saliente `GUARD_REST_SECONDS`. Los turnos ordinarios de sanidad suben
  la barra como mucho `Job.ordinary_cap`: para rendir hay que hacer guardias.
  El residente (MIR) tiene guardias mínimas por semana (`Job.guards_required`).
- **Teletrabajo** (oficina, Ley 10/2021): cansa la mitad y resta menos familia,
  pero la barra sube la mitad porque el jefe no te ve, y a veces te escriben
  fuera de hora.
- **Expatriarse** (oficina, `Job.abroad_from`): trabajar desde Hong Kong. La
  fiscalidad está en `bot.services.taxes` (salaries tax, MPF, art. 7.p LIRPF,
  art. 80 LIRPF y Ley Beckham) y la residencia, aquí (`residence_phase`).
- **El tiempo** (reparto, `Job.weather`): cada hora hace sol, llueve o hay
  calima (`weather`). Con lluvia el turno paga más (`RAIN_PAY`) y hay riesgo de
  caída aunque vayas descansado; con calima cansa más.
- **Valoración de la app** (reparto, `Job.rating`): de 1 a 5 estrellas, se
  mueve con la nota de cada turno (`rating_delta`) y multiplica el sueldo
  (`rating_pay`). Por debajo de `RATING_BLOCK`, la app te desconecta unas horas.
  Solo cuenta en el tronco (`Job.rates`): en las ramas no hay valoración.
- **Tarifa plana** (reparto): los falsos autónomos pagan una cuota reducida
  (`Position.fee`, art. 38 ter de la Ley 20/2007 del Trabajo Autónomo).
- **Oposición** (funcionariado, `Job.study`): turnos de estudio que no pagan,
  cansan y suman temario (`ShiftKind.STUDY`). Hay que estudiar para ascender
  por el tronco; las ramas no se oposita (`Job.can_study`).
- **Plaza fija** (funcionariado, `Job.tenure_from`): desde ese puesto no te
  bajan nunca y la nota mueve poco el sueldo (`TENURE_PAY_SPREAD`); a cambio,
  **trienios** (`Job.seniority`): cada `TRIENNIUM_DAYS` días en el oficio, un
  `TRIENNIUM_PAY` más de sueldo, con tope (`MAX_TRIENNIA`). Ni la plaza ni los
  trienios valen en una rama de autónomos (`Job.tenured`, `Job.earns_triennia`):
  es la excedencia voluntaria (art. 89.2 del EBEP).
- **Viralidad** (influencer, `Job.viral`): el sueldo de cada turno se
  multiplica por una tirada (`viral_roll`) que casi siempre se queda corta y a
  veces revienta. Los seguidores (`Contract.followers`) suben con los turnos
  buenos y bajan con las funas; no dan dinero, dan logros y postureo.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import NamedTuple
from zoneinfo import ZoneInfo

from bot.services.levels import TIMEZONE, local_day

# -- Batería ---------------------------------------------------------------------------

BATTERY_MAX = 100
#: Por debajo de esto no se puede fichar.
BATTERY_MIN = -30
#: Por debajo de esto estás reventado.
TIRED = 20
#: Puntos por hora; de 0 a 100 en 20 h.
BATTERY_REGEN_PER_HOUR = 5.0
SHIFT_COST = 18
EXTRA_SHIFT_COST = 27
#: Reventado: el minijuego da este tanto menos de tiempo.
TIRED_TIME_FACTOR = 0.7

# -- Jornada y horas extra --------------------------------------------------------------

ORDINARY_SHIFTS = 4
OVERTIME_PAY = 1.25
LEGAL_EXTRAS_PER_WEEK = 2
#: Lo que paga un turno en B, sobre la base (sin impuestos).
BLACK_PAY = 1.0
INSPECTION_CHANCE = 0.10
#: Recargo sobre lo cobrado en negro cuando te pillan.
INSPECTION_SURCHARGE = 0.20
IMV_SUSPENSION_SECONDS = 3 * 86_400

# -- Accidentes ------------------------------------------------------------------------

ACCIDENT_TIRED = 0.08
ACCIDENT_ZOMBIE = 0.20
SICK_LEAVE_SECONDS = 24 * 3600
#: Prestación por accidente de trabajo: el 75 % de la base reguladora desde el
#: día siguiente al accidente. Aquí, del sueldo diario medio de los últimos 30 días.
SICK_PAY_RATE = 0.75

# -- Familia ---------------------------------------------------------------------------

FAMILY_MAX = 100
#: Lo que se recupera sola cada hora, como la batería.
FAMILY_REGEN_PER_HOUR = 1.5
#: Lo que cuesta cada turno, según su tipo, más los recargos de madrugada y domingo.
FAMILY_ORDINARY = -6
FAMILY_EXTRA = -12
FAMILY_BLACK = -15
FAMILY_NIGHT = -5
FAMILY_SUNDAY = -3
#: Por debajo de esto salen los eventos familiares.
FAMILY_WORRIED = 40
#: Los favores no se piden si dejarían la familia por debajo de esto (en crisis).
FAVOR_FLOOR = 15
#: Bizum de mamá: te adelanta un sueldo base del puesto y se le devuelve con
#: esta parte de lo que llega de cada turno, hasta saldar la deuda.
MOM_REPAY_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class FamilyTier:
    """Un tramo de la familia y lo que hace.

    No da dinero: cambia lo rápido que se recarga la batería (cuándo puedes
    volver a fichar) y lo que sube la barra de rendimiento (cuándo asciendes).
    Los números salen de simular tres estilos de juego (`docs/familia_pala.py`).

    Attributes:
        low: Desde qué valor de familia se está en el tramo.
        regen: Multiplica la recarga de la batería.
        bonus: Puntos de barra extra en cada turno que la sube.
        gain: Multiplica lo que sube la barra (nunca lo que baja).
    """

    low: int
    emoji: str
    name: str
    regen: float
    bonus: int = 0
    gain: float = 1.0

    def performance(self, delta: int) -> int:
        """Lo que mueve la barra un turno con la familia en este tramo."""
        if delta <= 0:
            return delta
        return int(delta * self.gain) + self.bonus


#: De mejor a peor. El primero cuyo `low` alcanza la familia es su tramo.
FAMILY_TIERS: tuple[FamilyTier, ...] = (
    FamilyTier(75, "🏡", "En casa feliz", 1.5, bonus=3),
    FamilyTier(40, "🙂", "Tranquila", 1.0),
    FamilyTier(15, "😤", "Mosqueada", 0.5, gain=0.5),
    FamilyTier(0, "💔", "En crisis", 0.25, gain=0.0),
)
#: Horas de madrugada (hora canaria), [inicio, fin).
NIGHT_HOURS = (0, 6)

# -- Rendimiento, ascensos y excedencia ----------------------------------------------------

PERFORMANCE_MAX = 100
#: Barra con la que se entra a un puesto nuevo o se vuelve de excedencia.
PERFORMANCE_START = 50
LEAVE_AFTER_DAYS = 7
#: Puntuación desde la que un turno cuenta como «bueno» para las tareas.
GOOD_SCORE = 90
PERFECT_SCORE = 100

# -- Café ------------------------------------------------------------------------------

MAX_COFFEES = 3
#: Uno de cada tantos turnos trae un evento.
EVENT_CHANCE = 1 / 6

# -- Guardias (sanidad) ----------------------------------------------------------------

GUARD_COST = 45
GUARD_PAY = 1.6
GUARD_TIME_FACTOR = 2
GUARD_FAMILY = -20
#: Saliente de guardia: no se puede fichar. Valor de juego; en la realidad el
#: Supremo reconoció al personal sanitario 36 h seguidas de descanso semanal (2019).
GUARD_REST_SECONDS = 12 * 3600
#: Lo que baja la barra al residente por cada guardia mínima que no hizo la semana pasada.
MISSED_GUARD_PENALTY = 20

# -- Teletrabajo (oficina) ---------------------------------------------------------------

REMOTE_COST_FACTOR = 0.5
#: Teletrabajar en casa devuelve parte de lo que cuesta el turno.
REMOTE_FAMILY = 4
#: Probabilidad de que te escriban fuera de hora en un turno de teletrabajo.
REMOTE_PING_CHANCE = 0.3

# -- Stock options (CTO) ---------------------------------------------------------------

#: Parte del bruto del CTO que no se cobra y se queda en opciones.
OPTIONS_SHARE = 0.3
EXIT_CHANCE = 0.02
BANKRUPT_CHANCE = 0.025
EXIT_MULTIPLIER = (2.0, 10.0)

# -- Hong Kong (oficina) ---------------------------------------------------------------

HONG_KONG = "hk"
HK_TIMEZONE = ZoneInfo("Asia/Hong_Kong")
#: Paquete de expatriado: el sueldo base se multiplica por esto.
ABROAD_PAY = 2.0
#: Cada turno lejos de casa resta familia.
ABROAD_FAMILY = -4
#: Billete de avión (base, con IGIC general; simplificación: en la realidad la
#: parte del vuelo fuera de Canarias no lo pagaría).
FLIGHT_PRICE = 25_000
#: El jet lag del primer día.
JET_LAG = -30
#: Escala del juego para la residencia fiscal: una semana fuera es un año fiscal,
#: como el ejercicio semanal de la renta y del Patrimonio. Pasar más de 183 días
#: del año fuera (art. 9.1.a LIRPF) son 4 días; los 5 períodos sin residir que
#: pide la Ley Beckham (art. 93 LIRPF) son 5 semanas, y el régimen dura el año
#: del regreso y 5 más (6 semanas).
NONRESIDENT_AFTER_DAYS = 4
BECKHAM_ABROAD_WEEKS = 5
BECKHAM_WEEKS = 6

# -- Autónomos -------------------------------------------------------------------------

#: Cuota semanal de autónomos, en Y$ de nómina (unos 100 € a la semana, del orden
#: de la cuota real de los tramos altos). Desde el RDL 13/2022 la cuota real
#: depende de los rendimientos; aquí es fija y se cobra al fichar el primer turno
#: de la semana, trabajes lo que trabajes.
SELF_EMPLOYED_FEE = 10_000
#: Tarifa plana de los nuevos autónomos: 80 € al mes (art. 38 ter de la Ley 20/2007),
#: unos 18,5 € a la semana. La pagan los falsos autónomos del reparto.
FLAT_RATE_FEE = 1_850

# -- El tiempo (reparto) -----------------------------------------------------------------

RAIN_CHANCE = 0.15
CALIMA_CHANCE = 0.10
#: Lo que paga de más un turno con lluvia (las apps suben la tarifa).
RAIN_PAY = 1.4
#: Riesgo de caída con lluvia, aunque vayas descansado.
RAIN_ACCIDENT = 0.05
#: Con calima, el turno cansa esto más.
CALIMA_COST_FACTOR = 1.5

# -- Valoración de la app (reparto) --------------------------------------------------------

#: En décimas de estrella: 50 = ⭐ 5,0.
RATING_MAX = 50
RATING_MIN = 10
RATING_START = 45
#: Por debajo de esto la app te desconecta.
RATING_BLOCK = 35
RATING_BLOCK_SECONDS = 6 * 3600
#: Con lo que vuelves tras la desconexión.
RATING_RESET = 40

# -- Funcionariado ----------------------------------------------------------------------

#: Con plaza fija la nota mueve el sueldo entre el 90 % y el 110 % de la base.
TENURE_PAY_SPREAD = (0.9, 0.2)
#: Un trienio son 3 «años»; a la escala del juego (una semana, un año), 21 días.
TRIENNIUM_DAYS = 21
TRIENNIUM_PAY = 0.05
MAX_TRIENNIA = 8

# -- Influencer -------------------------------------------------------------------------

FOLLOWERS_START = 100
#: Desde este multiplicador, el turno se ha hecho viral.
VIRAL_AT = 3.0
#: El premio gordo de la viralidad.
MEGAVIRAL = 15.0
#: Por debajo de este multiplicador, el vídeo solo lo ha visto tu madre.
FLOP_BELOW = 0.35

# -- Deseo del día -----------------------------------------------------------------------

#: El turno que cumple el deseo del día paga esto más (como un plus de objetivos).
WISH_PAY = 1.25
#: Y sube esto la barra de rendimiento (además de lo que suba por la nota).
WISH_PERFORMANCE = 10


class Mechanic(StrEnum):
    """Motor de minijuego de un puesto (ver `bot.services.work_games`)."""

    DIG = "cavar"
    SPOT = "detectar"
    MEMORY = "memoria"
    DIALOGUE = "dialogo"
    SORT = "clasificar"
    CALC = "calcular"


class ShiftKind(StrEnum):
    """Tipo de turno según la jornada."""

    ORDINARY = "ordinario"
    EXTRA = "extra"
    BLACK = "negro"
    GUARD = "guardia"
    #: Estudiar la oposición: no paga ni cuenta en la jornada.
    STUDY = "estudio"


@dataclass(frozen=True, slots=True)
class Task:
    """Tarea de un puesto para poder ascender.

    Attributes:
        key: Identificador estable dentro del puesto.
        text: Cómo se enseña.
        stat: Contador del progreso del puesto (`Contract.progress`).
        goal: Meta.
    """

    key: str
    text: str
    stat: str
    goal: int


@dataclass(frozen=True, slots=True)
class Training:
    """Formación que hace falta para entrar en un puesto (se compra una vez).

    Attributes:
        key: Identificador estable; es lo que se guarda.
        name: Cómo se enseña, con su artículo («el curso de PRL…»).
        price: Base imponible en Y$; paga IGIC general.
    """

    key: str
    name: str
    price: int


@dataclass(frozen=True, slots=True)
class Perk:
    """Ventaja que da un puesto, para siempre mientras sigas en él o más arriba.

    Attributes:
        text: Lo que hace, en una línea (para el panel y la oferta de ascenso).
        extra_time: Tiempo extra del minijuego, en tanto por uno.
        retry: Si perdona el primer fallo del turno.
        fifty: Si tacha una respuesta mala en los diálogos.
        battery: Lo que se ahorra de batería por turno, en tanto por uno.
    """

    text: str
    extra_time: float = 0.0
    retry: bool = False
    fifty: bool = False
    battery: float = 0.0


@dataclass(frozen=True, slots=True)
class Position:
    """Un puesto de un oficio.

    Attributes:
        level: De 1 a `Job.top`.
        title: Nombre del puesto.
        base_pay: Bruto de un turno ordinario con puntuación media.
        mechanic: Motor del minijuego.
        seconds: Duración del minijuego.
        tasks: Tareas para ascender al siguiente puesto.
        days: Días distintos fichando en el puesto para poder ascender.
        training: Formación necesaria para ENTRAR en este puesto.
        self_employed: Si es autónomo (sin cotización por turno, con cuota).
        content: Clave del contenido del minijuego.
        fee: Cuota semanal de autónomos si `self_employed` (la tarifa plana es menor).
        perk: Ventaja que se gana al llegar a este puesto (`None` = ninguna).
    """

    level: int
    title: str
    base_pay: int
    mechanic: Mechanic
    seconds: int
    tasks: tuple[Task, ...]
    days: int
    content: str
    training: Training | None = None
    self_employed: bool = False
    fee: int = SELF_EMPLOYED_FEE
    perk: Perk | None = None


@dataclass(frozen=True, slots=True)
class Branch:
    """Una rama de un oficio: los puestos que siguen al tronco común.

    Attributes:
        key: Identificador estable; es lo que se guarda en el contrato.
        name: Cómo se enseña («Moncloa»).
        emoji: Su emoji.
        blurb: Una línea para elegirla.
        positions: Sus puestos, del siguiente al tronco hasta `Job.top`.
        pension: Bruto de la pensión diaria al jubilarse de ella (Y$ de nómina).
        retired_as: Cómo se llama al jubilado de esta rama («consejero de una
            eléctrica»), para el panel y el anuncio.
    """

    key: str
    name: str
    emoji: str
    blurb: str
    positions: tuple[Position, ...]
    pension: int
    retired_as: str


@dataclass(frozen=True, slots=True)
class Job:
    """Un oficio: un tronco de puestos y, si los tiene, varias ramas al final.

    Sin ramas, `positions` son todos los puestos. Con ramas, `positions` es el
    tronco común y cada rama sigue desde el puesto siguiente hasta `top`.
    """

    key: str
    name: str
    emoji: str
    blurb: str
    positions: tuple[Position, ...]
    rgb: tuple[int, int, int] = (230, 126, 34)
    #: Desde qué puesto hay guardias (`None` = no hay).
    guards_from: int | None = None
    #: Tope de lo que sube la barra un turno que no es guardia (`None` = sin tope).
    ordinary_cap: int | None = None
    #: `(puesto, guardias mínimas por semana)`.
    guards_required: tuple[tuple[int, int], ...] = ()
    #: Si se puede teletrabajar.
    remote: bool = False
    #: Desde qué puesto se puede ir a Hong Kong (`None` = no se puede).
    abroad_from: int | None = None
    #: Puesto que cobra parte del sueldo en stock options (`None` = ninguno).
    options_level: int | None = None
    #: Rama donde se sigue cobrando en opciones, además del puesto `options_level`.
    options_branch: str = ""
    #: Si cada hora hace un tiempo que cambia el turno (lluvia, calima).
    weather: bool = False
    #: Si hay valoración de la app (estrellas que multiplican el sueldo).
    rating: bool = False
    #: Contenido de diálogo de los turnos de estudio (`None` = no se estudia).
    study: str | None = None
    #: Desde qué puesto hay plaza fija (`None` = nunca).
    tenure_from: int | None = None
    #: Si se cobran trienios.
    seniority: bool = False
    #: Si el sueldo depende de hacerse viral.
    viral: bool = False
    #: Ramas que siguen al tronco (vacío = el oficio acaba en el tronco).
    branches: tuple[Branch, ...] = ()

    def branch(self, key: str) -> Branch | None:
        """La rama con esa clave, o `None` (también con `""`, el tronco)."""
        return next((b for b in self.branches if b.key == key), None)

    def has_options(self, level: int, branch: str = "") -> bool:
        """Si en ese puesto de esa rama se cobra parte del sueldo en stock options."""
        return level == self.options_level or (
            bool(self.options_branch) and branch == self.options_branch
        )

    def rates(self, branch: str = "") -> bool:
        """Si te valora la app: solo en el tronco (rider o fundador), no en las ramas."""
        return self.rating and self.branch(branch) is None

    def ladder(self, branch: str = "") -> tuple[Position, ...]:
        """Puestos en orden por esa rama: el tronco y, si la hay, la rama."""
        chosen = self.branch(branch)
        return self.positions + chosen.positions if chosen else self.positions

    def position(self, level: int, branch: str = "") -> Position:
        """Puesto de nivel `level` (de 1 a `top`) por la rama `branch`.

        Raises:
            ValueError: Si el puesto está pasado el tronco y la rama no existe.
        """
        ladder = self.ladder(branch)
        if not 1 <= level <= len(ladder):
            raise ValueError(f"{self.key} no tiene puesto {level} por la rama {branch!r}.")
        return ladder[level - 1]

    def next_position(self, level: int, branch: str = "") -> Position | None:
        """Puesto siguiente, o `None` si no hay o si toca elegir rama (`forks`)."""
        if level >= self.top or self.forks(level, branch):
            return None
        return self.position(level + 1, branch)

    def forks(self, level: int, branch: str = "") -> bool:
        """Si el siguiente ascenso obliga a elegir rama (fin del tronco, sin rama)."""
        return bool(self.branches) and level == self.trunk_top and not self.branch(branch)

    def perks(self, level: int, branch: str = "") -> tuple[Perk, ...]:
        """Ventajas ganadas por el camino hasta el puesto `level`."""
        return tuple(p.perk for p in self.ladder(branch)[:level] if p.perk is not None)

    @property
    def all_positions(self) -> tuple[Position, ...]:
        """Todos los puestos: el tronco y los de todas las ramas."""
        return self.positions + tuple(p for b in self.branches for p in b.positions)

    @property
    def ladders(self) -> tuple[tuple[str, tuple[Position, ...]], ...]:
        """Cada carrera completa posible: `(rama, puestos)`; sin ramas, `("", tronco)`."""
        if not self.branches:
            return (("", self.positions),)
        return tuple((b.key, self.ladder(b.key)) for b in self.branches)

    @property
    def trunk_top(self) -> int:
        """Último puesto del tronco común (con ramas, donde se elige)."""
        return len(self.positions)

    def has_guards(self, level: int) -> bool:
        """Si en ese puesto se pueden hacer guardias."""
        return self.guards_from is not None and level >= self.guards_from

    def can_go_abroad(self, level: int) -> bool:
        """Si en ese puesto se puede trabajar desde Hong Kong."""
        return self.abroad_from is not None and level >= self.abroad_from

    def can_study(self, level: int) -> bool:
        """Si en ese puesto se estudia la oposición.

        Solo se oposita en el tronco: pasado el último puesto del tronco (en el
        funcionariado, el inspector) se asciende por libre designación o por
        dinero, no por temario.
        """
        return self.study is not None and level < self.trunk_top

    def tenured(self, level: int, branch: str = "") -> bool:
        """Si en ese puesto hay plaza fija.

        No la hay en una rama de puestos de autónomo (la puerta giratoria): quien
        pide la excedencia voluntaria deja de ser funcionario en activo y pierde
        la plaza fija.
        """
        return (
            self.tenure_from is not None
            and level >= self.tenure_from
            and not self.position(level, branch).self_employed
        )

    def earns_triennia(self, level: int, branch: str = "") -> bool:
        """Si en ese puesto corren trienios.

        En la excedencia voluntaria por interés particular no se computa
        antigüedad (art. 89.2 del EBEP, Real Decreto Legislativo 5/2015), así que
        en una rama de autónomos no se cobran.
        """
        return self.seniority and not self.position(level, branch).self_employed

    def required_guards(self, level: int) -> int:
        """Guardias mínimas por semana en ese puesto."""
        return dict(self.guards_required).get(level, 0)

    @property
    def top(self) -> int:
        """Nivel máximo del oficio (todas sus ramas acaban en el mismo)."""
        return self.trunk_top + (len(self.branches[0].positions) if self.branches else 0)


# -- Contrato --------------------------------------------------------------------------


class Career(NamedTuple):
    """Lo que queda guardado de un oficio en `work_history`.

    Attributes:
        level: Puesto en el que se quedó (al volver se entra en él).
        max_level: El puesto más alto al que llegó.
        branch: Rama en la que se quedó (`""` = tronco).
    """

    level: int
    max_level: int
    branch: str = ""


@dataclass(frozen=True, slots=True)
class ShiftRecord:
    """Un turno guardado en `work_shifts`.

    Attributes:
        kind: Valor de `ShiftKind`.
        gross: Bruto (o lo cobrado en negro).
        net: Neto que llegó al bolsillo.
    """

    job: str
    level: int
    day: str
    created_at: float
    score: int
    kind: str
    gross: int
    net: int


@dataclass(slots=True)
class Contract:
    """Estado laboral de un miembro (una fila de `work_contracts`).

    Los campos `*_day` y `week` son fechas ISO en hora canaria.
    """

    job: str
    level: int
    performance: int = PERFORMANCE_START
    warned: bool = False
    progress: dict[str, int] = field(default_factory=dict)
    position_since: float = 0.0
    battery: float = BATTERY_MAX
    battery_at: float = 0.0
    #: Familia en `family_at`; desde entonces se recupera sola (`family_now`).
    family: float = FAMILY_MAX
    family_at: float = 0.0
    last_shift_at: float = 0.0
    shift_day: str = ""
    shifts_today: int = 0
    week: str = ""
    extras_week: int = 0
    streak_days: int = 0
    fee_week: str = ""
    coffee_day: str = ""
    coffees: int = 0
    sick_until: float = 0.0
    declined_day: str = ""
    no_extras_day: str = ""
    #: Guardias de la semana `guard_week` (lunes ISO).
    guards_week: int = 0
    guard_week: str = ""
    #: Saliente de guardia: hasta cuándo no se puede fichar.
    off_duty_until: float = 0.0
    #: País donde trabaja (`""` = en casa, `"hk"` = Hong Kong) y desde cuándo.
    abroad: str = ""
    abroad_since: float = 0.0
    #: Hasta cuándo tributa por la Ley Beckham (24 % fijo).
    beckham_until: float = 0.0
    #: Stock options acumuladas (valor nominal en Y$).
    options: int = 0
    #: Exención del art. 7.p LIRPF usada el día `exempt_day`.
    exempt_day: str = ""
    exempt_used: int = 0
    #: Desde cuándo trabaja en este oficio (trienios).
    job_since: float = 0.0
    #: Valoración de la app, en décimas de estrella (reparto).
    rating: int = RATING_START
    #: Hasta cuándo la app te tiene desconectado (reparto).
    blocked_until: float = 0.0
    #: Seguidores (influencer).
    followers: int = FOLLOWERS_START
    #: Rama del oficio (`""` = tronco, o un oficio sin ramas).
    branch: str = ""
    #: Último día (ISO) en que cumplió el deseo del día, y cuántos días seguidos.
    wish_day: str = ""
    wish_streak: int = 0
    #: Último uso (epoch) de cada regalo y favor de la familia, por su clave.
    family_used: dict[str, float] = field(default_factory=dict)
    #: Lo que queda por devolverle a tu madre del Bizum de mamá (`MOM_REPAY_SHARE`).
    mom_debt: int = 0
    #: «Lo hago por los niños»: el próximo turno trae más tiempo.
    motivated: bool = False


def family_now(contract: Contract, now: float) -> float:
    """Familia en `now`, con lo que se ha recuperado sola desde `family_at`."""
    hours = max(0.0, now - contract.family_at) / 3600
    return min(float(FAMILY_MAX), contract.family + hours * FAMILY_REGEN_PER_HOUR)


def family_tier(family: float) -> FamilyTier:
    """Tramo de un valor de familia."""
    return next(t for t in FAMILY_TIERS if family >= t.low)


def _recharge(contract: Contract, at: float) -> tuple[float, float]:
    """Recarga de la batería por hora en `at` y hasta cuándo dura así.

    La recarga depende del tramo de la familia, y la familia sube sola: dura
    hasta que la familia llega al tramo siguiente (o para siempre en el mejor).
    """
    family = family_now(contract, at)
    rate = BATTERY_REGEN_PER_HOUR * family_tier(family).regen
    above = [t.low for t in FAMILY_TIERS if t.low > family]
    if not above or family >= FAMILY_MAX:
        return rate, math.inf
    # Al menos un segundo, para que el redondeo en el borde no deje el bucle quieto.
    return rate, at + max(1.0, (min(above) - family) / FAMILY_REGEN_PER_HOUR * 3600)


def battery_now(contract: Contract, now: float) -> float:
    """Batería en `now`, con la recarga de las horas pasadas, tramo a tramo de familia."""
    battery, at = contract.battery, contract.battery_at
    while at < now and battery < BATTERY_MAX:
        rate, until = _recharge(contract, at)
        end = min(now, until)
        battery += (end - at) / 3600 * rate
        at = end
    return min(float(BATTERY_MAX), battery)


def battery_eta(contract: Contract, target: float, now: float) -> float:
    """Momento (epoch) en que la batería, recargándose sola, llega a `target`."""
    battery, at = battery_now(contract, now), now
    while battery < target:
        rate, until = _recharge(contract, at)
        reach = at + (target - battery) / rate * 3600
        if reach <= until:
            return reach
        battery += (until - at) / 3600 * rate
        at = until
    return now


def rested_at(contract: Contract, now: float) -> float:
    """Momento desde el que se puede volver a fichar (la batería pasa de `BATTERY_MIN`)."""
    return battery_eta(contract, BATTERY_MIN, now) + 60


def settle(contract: Contract, now: float) -> None:
    """Apunta batería y familia tal como están en `now`.

    Hay que llamarlo antes de cambiar cualquiera de las dos: la recarga de la
    batería se calcula con la familia desde `battery_at`, así que las dos
    tienen que partir del mismo momento.
    """
    contract.battery = battery_now(contract, now)
    contract.family = family_now(contract, now)
    contract.battery_at = contract.family_at = now


def change_family(contract: Contract, delta: float, now: float) -> bool:
    """Suma `delta` a la familia (entre 0 y el máximo) y dice si llega la intervención.

    Si la familia cae a 0, mañana no hay extras, B ni guardias. `settle` va dentro.
    """
    settle(contract, now)
    before = contract.family
    contract.family = max(0.0, min(float(FAMILY_MAX), before + delta))
    if contract.family == 0 and before > 0:
        contract.no_extras_day = (local_day(now) + timedelta(days=1)).isoformat()
        return True
    return False


def family_ready_at(contract: Contract, key: str, cooldown: int) -> float:
    """Desde cuándo se puede volver a dar un regalo o pedir un favor (0 si nunca se usó)."""
    last = contract.family_used.get(key)
    return 0.0 if last is None else last + cooldown


def battery_state(battery: float) -> str:
    """Etiqueta de la batería: `"bien"`, `"reventado"` o `"zombi"`."""
    if battery < 0:
        return "zombi"
    if battery < TIRED:
        return "reventado"
    return "bien"


def battery_bar(battery: float, width: int = 10) -> str:
    """`🔋 ▰▰▰▱▱▱▱▱▱▱ 32` (o 🪫 si está baja). Negativos: barra vacía."""
    filled = max(0, min(width, round(battery / BATTERY_MAX * width)))
    icon = "🔋" if battery >= TIRED else "🪫"
    return f"{icon} {'▰' * filled}{'▱' * (width - filled)} {round(battery)}"


def performance_bar(value: int, width: int = 10) -> str:
    """`📈 ▰▰▰▰▰▱▱▱▱▱ 50/100`."""
    filled = max(0, min(width, round(value / PERFORMANCE_MAX * width)))
    return f"📈 {'▰' * filled}{'▱' * (width - filled)} {value}/{PERFORMANCE_MAX}"


def week_of(day: date) -> str:
    """Lunes ISO de la semana de `day`."""
    return (day - timedelta(days=day.weekday())).isoformat()


def is_night(now: float, tz: ZoneInfo = TIMEZONE) -> bool:
    """Si `now` cae en la madrugada (hora de `tz`; por defecto, la canaria)."""
    hour = datetime.fromtimestamp(now, tz).hour
    return NIGHT_HOURS[0] <= hour < NIGHT_HOURS[1]


def roll_over_day(contract: Contract, now: float) -> int:
    """Pone a cero los contadores del día y de la semana si han cambiado.

    También corta la racha si hubo descanso. Es idempotente: se puede llamar
    y guardar varias veces el mismo día (al tomar un café, al ascender). La
    familia no pasa por aquí: se recupera sola por horas (`family_now`).

    Returns:
        Días enteros de descanso desde el último turno (0 si fichó ayer u hoy).
    """
    today = local_day(now)
    rest_days = 0
    if contract.shift_day != today.isoformat():
        if contract.shift_day:
            last = date.fromisoformat(contract.shift_day)
            rest_days = max(0, (today - last).days - 1)
            if rest_days:
                contract.streak_days = 0
        contract.shifts_today = 0
    week = week_of(today)
    if contract.week != week:
        contract.week = week
        contract.extras_week = 0
    if contract.coffee_day != today.isoformat():
        contract.coffee_day = today.isoformat()
        contract.coffees = 0
    return rest_days


def next_shift_kind(contract: Contract, now: float) -> ShiftKind:
    """Qué sería el próximo turno: ordinario, extra legal o extra en B.

    Debe llamarse después de `roll_over_day`.
    """
    if contract.shifts_today < ORDINARY_SHIFTS:
        return ShiftKind.ORDINARY
    if contract.extras_week < LEGAL_EXTRAS_PER_WEEK:
        return ShiftKind.EXTRA
    return ShiftKind.BLACK


def shift_cost(kind: ShiftKind, *, remote: bool = False, calima: bool = False) -> int:
    """Batería que gasta un turno (estudiar cansa como un turno ordinario)."""
    if kind is ShiftKind.GUARD:
        cost = GUARD_COST
    elif kind in (ShiftKind.ORDINARY, ShiftKind.STUDY):
        cost = SHIFT_COST
    else:
        cost = EXTRA_SHIFT_COST
    factor = (REMOTE_COST_FACTOR if remote else 1) * (CALIMA_COST_FACTOR if calima else 1)
    return round(cost * factor)


def shift_pay(base: int, score: int, kind: ShiftKind, *, tenured: bool = False) -> int:
    """Bruto (o lo cobrado en B) de un turno con puntuación `score` (0–100).

    Con plaza fija (`tenured`) la nota apenas mueve el sueldo: del 90 % al 110 %.
    """
    low, spread = TENURE_PAY_SPREAD if tenured else (0.7, 0.6)
    factor = low + spread * max(0, min(100, score)) / 100
    if kind is ShiftKind.EXTRA:
        factor *= OVERTIME_PAY
    elif kind is ShiftKind.BLACK:
        factor *= BLACK_PAY
    elif kind is ShiftKind.GUARD:
        factor *= GUARD_PAY
    return max(1, round(base * factor))


def performance_delta(
    score: int,
    kind: ShiftKind = ShiftKind.ORDINARY,
    *,
    remote: bool = False,
    cap: int | None = None,
    family: FamilyTier | None = None,
) -> int:
    """Cuánto mueve la barra un turno: +25 uno perfecto, −25 uno a cero.

    El tramo de la familia con el que llegas (`family`) va primero: en casa
    feliz suma, en crisis no sube. Luego, una guardia lo mueve el doble y el
    teletrabajo, la mitad. `cap` limita lo que sube un turno que no es guardia
    (sanidad: para rendir, guardias).
    """
    delta = (max(0, min(100, score)) - 50) // 2
    if family is not None:
        delta = family.performance(delta)
    if kind is ShiftKind.GUARD:
        return delta * 2
    if remote:
        delta = int(delta / 2)
    if cap is not None:
        delta = min(delta, cap)
    return delta


def accident_chance(battery_before: float) -> float:
    """Probabilidad de accidente laboral según la batería al fichar."""
    if battery_before < 0:
        return ACCIDENT_ZOMBIE
    if battery_before < TIRED:
        return ACCIDENT_TIRED
    return 0.0


def family_breakdown(
    kind: ShiftKind,
    now: float,
    *,
    remote: bool = False,
    tz: ZoneInfo = TIMEZONE,
    abroad: bool = False,
) -> list[tuple[str, int]]:
    """Lo que cambia la familia un turno, partida a partida: `[("extra", -12), …]`.

    Todo turno cuesta (estudiar en casa, como uno ordinario). La madrugada se
    mide en la hora de donde trabajas (`tz`); el domingo y los festivos, en la
    de casa.
    """
    base = {
        ShiftKind.GUARD: ("guardia", GUARD_FAMILY),
        ShiftKind.EXTRA: ("extra", FAMILY_EXTRA),
        ShiftKind.BLACK: ("en B", FAMILY_BLACK),
    }.get(kind, ("turno", FAMILY_ORDINARY))
    parts = [base]
    if is_night(now, tz):
        parts.append(("madrugada", FAMILY_NIGHT))
    if local_day(now).weekday() == 6 or holiday(local_day(now)) is not None:
        parts.append(("domingo" if local_day(now).weekday() == 6 else "festivo", FAMILY_SUNDAY))
    if remote:
        parts.append(("en casa", REMOTE_FAMILY))
    if abroad:
        parts.append(("lejos de casa", ABROAD_FAMILY))
    return parts


def family_cost(
    kind: ShiftKind,
    now: float,
    *,
    remote: bool = False,
    tz: ZoneInfo = TIMEZONE,
    abroad: bool = False,
) -> int:
    """Lo que cambia la familia un turno, en total (casi siempre, a peor)."""
    return sum(v for _, v in family_breakdown(kind, now, remote=remote, tz=tz, abroad=abroad))


def residence_phase(contract: Contract, now: float) -> str:
    """Situación fiscal de quien trabaja fuera: `"casa"`, `"residente"` o `"no_residente"`.

    Quien pasa más de 183 días del año fuera deja de ser residente fiscal en
    España (art. 9.1.a LIRPF); en el juego, `NONRESIDENT_AFTER_DAYS` días.
    """
    if not contract.abroad:
        return "casa"
    if now - contract.abroad_since >= NONRESIDENT_AFTER_DAYS * 86_400:
        return "no_residente"
    return "residente"


def beckham_eligible(contract: Contract, now: float) -> bool:
    """Si al volver a España le toca la Ley Beckham (5 «años» fuera)."""
    return bool(contract.abroad) and (
        now - contract.abroad_since >= BECKHAM_ABROAD_WEEKS * 7 * 86_400
    )


#: Festivos que dan logro o restan familia (día, mes) → nombre.
HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1): "Año Nuevo",
    (6, 1): "Reyes",
    (1, 5): "el Día del Trabajador",
    (30, 5): "el Día de Canarias",
    (6, 12): "el Día de la Constitución",
    (24, 12): "Nochebuena",
    (25, 12): "Navidad",
    (31, 12): "Nochevieja",
}


def holiday(day: date) -> str | None:
    """Nombre del festivo de `day`, si lo es."""
    return HOLIDAYS.get((day.day, day.month))


def apply_performance(contract: Contract, delta: int, *, tenured: bool = False) -> str | None:
    """Mueve la barra y aplica el aviso o la degradación.

    Con plaza fija (`tenured`) no te bajan nunca: como mucho, el aviso.

    Returns:
        `"warned"` si es el primer aviso, `"demoted"` si te bajan de puesto o
        `None` si no pasa nada.
    """
    contract.performance = max(0, min(PERFORMANCE_MAX, contract.performance + delta))
    if contract.performance >= PERFORMANCE_START:
        contract.warned = False
    if contract.performance > 0:
        return None
    if not contract.warned:
        contract.warned = True
        return "warned"
    if contract.level <= 1 or tenured:
        # En el primer puesto no hay a dónde bajar, y a un funcionario de carrera
        # no lo baja nadie: se queda el aviso.
        return "warned"
    contract.level -= 1
    contract.performance = PERFORMANCE_START
    contract.warned = False
    contract.progress = {}
    return "demoted"


def promotion_blockers(
    contract: Contract,
    job: Job,
    *,
    days_in_position: int,
    trainings: set[str],
    today: str,
    retired: Collection[str] = (),
) -> list[str]:
    """Lo que falta para que te ofrezcan el ascenso (vacío = ya toca).

    Al final del tronco, el ascenso es a la rama que se elija al aceptarlo; las
    ramas no piden formación para entrar (lo comprueba una prueba del catálogo).

    Args:
        days_in_position: Días distintos fichando en el puesto actual.
        trainings: Formaciones que ya tiene el miembro.
        today: Día de hoy (ISO); si rechazó una oferta hoy, no se repite.
        retired: Ramas de este oficio de las que ya se jubiló.
    """
    if contract.level >= job.top:
        return ["Ya estás en lo más alto. Más arriba solo hay una puerta giratoria."]
    if job.forks(contract.level, contract.branch) and not open_branches(job, retired):
        return ["Ya te jubilaste de todas las ramas. Más arriba no queda nada."]
    position = job.position(contract.level, contract.branch)
    missing: list[str] = []
    if contract.performance < PERFORMANCE_MAX:
        missing.append(f"Llenar la barra de rendimiento ({contract.performance}/100)")
    if days_in_position < position.days:
        missing.append(f"Fichar {position.days} días distintos en el puesto ({days_in_position})")
    for task in position.tasks:
        done = contract.progress.get(task.stat, 0)
        if done < task.goal:
            missing.append(f"{task.text} ({done}/{task.goal})")
    following = job.next_position(contract.level, contract.branch)
    if (
        following is not None
        and following.training is not None
        and following.training.key not in trainings
    ):
        missing.append(f"Conseguir {following.training.name} (en 🎓 Formación)")
    if contract.declined_day == today and not missing:
        missing.append("Rechazaste el ascenso hoy. Mañana te lo vuelven a ofrecer.")
    return missing


def open_branches(job: Job, retired: Collection[str] = ()) -> tuple[Branch, ...]:
    """Ramas del oficio que aún se pueden elegir (de las otras ya se jubiló)."""
    return tuple(b for b in job.branches if b.key not in retired)


def settle_branch(contract: Contract, job: Job) -> None:
    """Deja la rama del contrato en un estado que existe.

    Si ha vuelto al tronco (te han bajado), la rama se olvida. Si está pasado
    el tronco en una rama que ya no está en el catálogo, vuelve al final del
    tronco a elegir otra.
    """
    if contract.level <= job.trunk_top:
        contract.branch = ""
    elif job.branch(contract.branch) is None:
        contract.level, contract.branch = job.trunk_top, ""


def promote(contract: Contract, now: float, branch: str = "") -> None:
    """Sube un puesto: barra a la mitad, tareas a cero y nueva antigüedad.

    Args:
        branch: Rama elegida, si el ascenso es el que sale del tronco.
    """
    if branch:
        contract.branch = branch
    contract.level += 1
    contract.performance = PERFORMANCE_START
    contract.warned = False
    contract.progress = {}
    contract.position_since = now
    contract.declined_day = ""


def on_leave(contract: Contract, now: float) -> bool:
    """Si lleva `LEAVE_AFTER_DAYS` o más sin fichar (excedencia)."""
    return bool(contract.last_shift_at) and now - contract.last_shift_at >= (
        LEAVE_AFTER_DAYS * 86_400
    )


def weather(now: float) -> str:
    """Tiempo que hace en Canarias esa hora: `"sol"`, `"lluvia"` o `"calima"`.

    Sale de la hora (canaria), no del azar del servicio: así el panel puede
    enseñarlo antes de fichar y es el mismo para todo el servidor.
    """
    moment = datetime.fromtimestamp(now, TIMEZONE)
    draw = random.Random(moment.strftime("%Y-%m-%d %H")).random()
    if draw < RAIN_CHANCE:
        return "lluvia"
    if draw < RAIN_CHANCE + CALIMA_CHANCE:
        return "calima"
    return "sol"


def rating_delta(score: int) -> int:
    """Cuánto mueve la valoración un turno, en décimas de estrella."""
    if score >= 90:
        return 2
    if score >= 70:
        return 1
    if score >= 50:
        return 0
    if score >= 30:
        return -2
    return -4


def rating_pay(rating: int) -> float:
    """Lo que multiplica el sueldo la valoración: de ×0,85 (3,5 ⭐) a ×1,15 (5 ⭐)."""
    clamped = max(RATING_BLOCK, min(RATING_MAX, rating))
    return 0.85 + 0.3 * (clamped - RATING_BLOCK) / (RATING_MAX - RATING_BLOCK)


def stars(rating: int) -> str:
    """`⭐ 4,5`."""
    return f"⭐ {rating // 10},{rating % 10}"


def triennia(contract: Contract, now: float) -> int:
    """Trienios cumplidos en el oficio (con tope)."""
    if not contract.job_since:
        return 0
    days = (now - contract.job_since) / 86_400
    return min(MAX_TRIENNIA, int(days // TRIENNIUM_DAYS))


def viral_roll(rng: random.Random) -> float:
    """Lo que multiplica el sueldo de un turno de influencer.

    Casi siempre se queda corto, a veces va bien y muy de vez en cuando
    revienta. La media ronda 1,04: un influencer gana como los demás, pero a
    golpes.
    """
    draw = rng.random()
    if draw < 0.005:
        return MEGAVIRAL
    if draw < 0.05:
        return rng.uniform(3.0, 6.0)
    if draw < 0.30:
        return rng.uniform(1.0, 2.0)
    return rng.uniform(0.3, 0.8)


def follower_gain(score: int, level: int, multiplier: float) -> int:
    """Seguidores que trae un turno: más con buena nota, más arriba y si se hace viral."""
    return round(max(0, score - 40) * level * level * multiplier)


def roll(rng: random.Random, chance: float) -> bool:
    """`True` con probabilidad `chance`."""
    return chance > 0 and rng.random() < chance


# -- Deseo del día -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WishFacts:
    """Lo que se sabe de un turno para ver si cumple el deseo del día.

    Attributes:
        hour: Hora local (canaria) al cerrar el turno.
        time_left: Segundos que sobraron del minijuego (0 si se agotó).
        seconds: Duración total del minijuego.
        completed: Si se jugaron todas las rondas antes de que se acabara el tiempo.
        shifts_today: Turnos de jornada del día, contando este.
        viral: Multiplicador de la viralidad (0 si el oficio no la tiene).
    """

    score: int
    kind: ShiftKind
    battery_before: float
    hour: int
    weather: str
    time_left: float
    seconds: float
    completed: bool
    shifts_today: int
    remote: bool
    viral: float


@dataclass(frozen=True, slots=True)
class Wish:
    """Un deseo del día: un objetivo pequeño para un turno.

    Attributes:
        key: Identificador estable (para logros y pruebas).
        text: Cómo se enseña en el panel.
        met: Si un turno lo cumple.
        fits: Si tiene sentido en ese oficio y puesto (`None` = en todos).
    """

    key: str
    text: str
    met: Callable[[WishFacts], bool]
    fits: Callable[[Job, int], bool] | None = None


#: Todos los deseos. Los que dependen del oficio solo salen donde se pueden cumplir.
WISHES: tuple[Wish, ...] = (
    Wish("nota_90", "Saca 90 o más en un turno.", lambda f: f.score >= 90),
    Wish("perfecto", "Saca un 100 redondo.", lambda f: f.score >= 100),
    Wish(
        "rapido",
        "Acaba el minijuego con la mitad del tiempo de sobra.",
        lambda f: f.completed and f.time_left >= f.seconds / 2,
    ),
    Wish(
        "madrugador",
        "Ficha antes de las 10:00 (hora canaria) y saca 70 o más.",
        lambda f: f.hour < 10 and f.score >= 70,
    ),
    Wish(
        "nocturno",
        "Ficha a partir de las 21:00 (hora canaria) y saca 70 o más.",
        lambda f: f.hour >= 21 and f.score >= 70,
    ),
    Wish(
        "reventado",
        "Saca 80 o más con la batería por debajo de 30.",
        lambda f: f.battery_before < 30 and f.score >= 80,
    ),
    Wish("tres", "Haz 3 turnos de jornada hoy (cuenta el tercero).", lambda f: f.shifts_today >= 3),
    Wish(
        "extra",
        "Echa un turno extra legal y saca 70 o más.",
        lambda f: f.kind is ShiftKind.EXTRA and f.score >= 70,
    ),
    Wish(
        "lluvia",
        "Reparte bajo la lluvia y saca 70 o más.",
        lambda f: f.weather == "lluvia" and f.score >= 70,
        lambda job, _level: job.weather,
    ),
    Wish(
        "guardia",
        "Haz una guardia y saca 70 o más.",
        lambda f: f.kind is ShiftKind.GUARD and f.score >= 70,
        lambda job, level: job.has_guards(level),
    ),
    Wish(
        "teletrabajo",
        "Teletrabaja un turno y saca 80 o más.",
        lambda f: f.remote and f.score >= 80,
        lambda job, _level: job.remote,
    ),
    Wish(
        "algoritmo",
        "Que el algoritmo te quiera: un turno que pase de ×1.",
        lambda f: f.viral > 1,
        lambda job, _level: job.viral,
    ),
)  # fmt: skip
WISH_BY_KEY: dict[str, Wish] = {wish.key: wish for wish in WISHES}


def wish_for(user_id: int, day: str, job: Job, level: int) -> Wish:
    """El deseo del día de un miembro: el mismo todo el día para el mismo puesto.

    Se sortea con una semilla de texto (miembro y día), que `random` convierte
    con SHA-512: sale igual tras reiniciar el bot, al revés que `hash()`.
    """
    options = [w for w in WISHES if w.fits is None or w.fits(job, level)]
    return random.Random(f"deseo:{user_id}:{day}").choice(options)


def wish_streak(contract: Contract, today: date) -> int:
    """Racha de deseos si hoy se cumple uno (sigue si el último fue ayer)."""
    yesterday = (today - timedelta(days=1)).isoformat()
    return contract.wish_streak + 1 if contract.wish_day == yesterday else 1
