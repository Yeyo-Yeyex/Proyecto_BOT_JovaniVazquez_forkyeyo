"""Reglas del trabajo (`pala`): puestos, batería, horas extra, familia y ascensos.

Lógica pura, sin Discord ni base de datos. El catálogo de oficios está en
`bot.services.work_catalog`, los minijuegos en `bot.services.work_games` y la
persistencia en `bot.repositories.work`; `WorkService` (al final de este
módulo) junta todo con la economía.

Cómo funciona un turno, en resumen:

- Cada miembro tiene, como mucho, un contrato: un oficio y un puesto (1 a 5).
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
- La **familia** (0–100) no da dinero: baja con las horas extra, la
  madrugada y los domingos, y sube con los días libres. Alimenta eventos y
  logros. A 0 llega la intervención familiar: al día siguiente, sin extras.
- **Ascensos** al estilo de los Sims (`promotion_blockers`): barra llena, unos
  días en el puesto, las tareas del puesto y, a veces, una formación.
- **Sin despidos**: con la barra a cero hay un aviso y, si se repite, te bajan
  un puesto. Tras `LEAVE_AFTER_DAYS` días sin fichar, excedencia.

Reglas propias de algunos oficios (campos de `Job`):

- **Guardias** (sanidad): turno doble aparte de la jornada (`ShiftKind.GUARD`).
  Gasta mucha batería, paga `GUARD_PAY` veces la base aunque dure el doble (la
  hora de guardia sale más barata, como en muchos servicios de salud) y mueve
  la barra el doble. No son horas extra: en el Estatuto Marco (Ley 55/2003)
  son jornada complementaria, así que no tienen límite ni B. Después te
  quedas saliente `GUARD_REST_SECONDS`. Los turnos ordinarios de sanidad suben
  la barra como mucho `Job.ordinary_cap`: para rendir hay que hacer guardias.
  El residente (MIR) tiene guardias mínimas por semana (`Job.guards_required`).
- **Teletrabajo** (oficina, Ley 10/2021): cansa la mitad y no resta familia,
  pero la barra sube la mitad porque el jefe no te ve, y a veces te escriben
  fuera de hora.
- **Expatriarse** (oficina, `Job.abroad_from`): trabajar desde Hong Kong. La
  fiscalidad está en `bot.services.taxes` (salaries tax, MPF, art. 7.p LIRPF,
  art. 80 LIRPF y Ley Beckham) y la residencia, aquí (`residence_phase`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from bot.services.levels import TIMEZONE, local_day

# -- Batería ---------------------------------------------------------------------------

BATTERY_MAX = 100
#: Por debajo de esto no se puede fichar.
BATTERY_MIN = -30
#: Por debajo de esto estás reventado.
TIRED = 20
#: Puntos por hora; de 0 a 100 en 20 h, la misma espera que el IMV.
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
FAMILY_EXTRA = -8
FAMILY_NIGHT = -5
FAMILY_SUNDAY = -3
FAMILY_REST_DAY = 10
#: Por debajo de esto salen los eventos familiares.
FAMILY_WORRIED = 40
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
GUARD_FAMILY = -15
#: Saliente de guardia: no se puede fichar. Valor de juego; en la realidad el
#: Supremo reconoció al personal sanitario 36 h seguidas de descanso semanal (2019).
GUARD_REST_SECONDS = 12 * 3600
#: Lo que baja la barra al residente por cada guardia mínima que no hizo la semana pasada.
MISSED_GUARD_PENALTY = 20

# -- Teletrabajo (oficina) ---------------------------------------------------------------

REMOTE_COST_FACTOR = 0.5
REMOTE_FAMILY = 2
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
FLIGHT_PRICE = 9_000
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

#: Cuota semanal de autónomos. Valor de juego: desde el RDL 13/2022 la cuota real
#: depende de los rendimientos (tramos); aquí es fija y se cobra al fichar el
#: primer turno de la semana, trabajes lo que trabajes.
SELF_EMPLOYED_FEE = 1_200


class Mechanic(StrEnum):
    """Motor de minijuego de un puesto (ver `bot.services.work_games`)."""

    DIG = "cavar"
    SPOT = "detectar"
    MEMORY = "memoria"
    DIALOGUE = "dialogo"


class ShiftKind(StrEnum):
    """Tipo de turno según la jornada."""

    ORDINARY = "ordinario"
    EXTRA = "extra"
    BLACK = "negro"
    GUARD = "guardia"


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
class Position:
    """Un puesto de un oficio.

    Attributes:
        level: 1 a 5.
        title: Nombre del puesto.
        base_pay: Bruto de un turno ordinario con puntuación media.
        mechanic: Motor del minijuego.
        seconds: Duración del minijuego.
        tasks: Tareas para ascender al siguiente puesto.
        days: Días distintos fichando en el puesto para poder ascender.
        training: Formación necesaria para ENTRAR en este puesto.
        self_employed: Si es autónomo (sin cotización por turno, con cuota).
        content: Clave del contenido del minijuego.
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


@dataclass(frozen=True, slots=True)
class Job:
    """Un oficio con sus cinco puestos."""

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

    def position(self, level: int) -> Position:
        """Puesto de nivel `level` (1–5)."""
        return self.positions[level - 1]

    def has_guards(self, level: int) -> bool:
        """Si en ese puesto se pueden hacer guardias."""
        return self.guards_from is not None and level >= self.guards_from

    def can_go_abroad(self, level: int) -> bool:
        """Si en ese puesto se puede trabajar desde Hong Kong."""
        return self.abroad_from is not None and level >= self.abroad_from

    def required_guards(self, level: int) -> int:
        """Guardias mínimas por semana en ese puesto."""
        return dict(self.guards_required).get(level, 0)

    @property
    def top(self) -> int:
        """Nivel máximo del oficio."""
        return len(self.positions)


# -- Contrato --------------------------------------------------------------------------


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
    family: int = FAMILY_MAX
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
    #: Último día de descanso ya sumado a la familia (ver `roll_over_day`).
    rest_day: str = ""
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


def battery_now(contract: Contract, now: float) -> float:
    """Batería en `now`, con la recarga de las horas pasadas."""
    hours = max(0.0, now - contract.battery_at) / 3600
    return min(float(BATTERY_MAX), contract.battery + hours * BATTERY_REGEN_PER_HOUR)


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

    También aplica la recuperación de la familia por los días enteros sin
    fichar desde el último turno y corta la racha si hubo descanso. Es
    idempotente: se puede llamar y guardar varias veces el mismo día (al
    tomar un café, al ascender) sin que la familia se recupere dos veces,
    porque `rest_day` recuerda hasta qué día ya se contó.

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
                last_rest = today - timedelta(days=1)
                counted = date.fromisoformat(contract.rest_day) if contract.rest_day else last
                new = (last_rest - max(last, counted)).days
                if new > 0:
                    contract.family = min(FAMILY_MAX, contract.family + new * FAMILY_REST_DAY)
                    contract.rest_day = last_rest.isoformat()
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


def shift_cost(kind: ShiftKind, *, remote: bool = False) -> int:
    """Batería que gasta un turno."""
    if kind is ShiftKind.GUARD:
        cost = GUARD_COST
    elif kind is ShiftKind.ORDINARY:
        cost = SHIFT_COST
    else:
        cost = EXTRA_SHIFT_COST
    return round(cost * (REMOTE_COST_FACTOR if remote else 1))


def shift_pay(base: int, score: int, kind: ShiftKind) -> int:
    """Bruto (o lo cobrado en B) de un turno con puntuación `score` (0–100)."""
    factor = 0.7 + 0.6 * max(0, min(100, score)) / 100
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
) -> int:
    """Cuánto mueve la barra un turno: +25 uno perfecto, −25 uno a cero.

    Una guardia lo mueve el doble; el teletrabajo, la mitad. `cap` limita lo que
    sube un turno que no es guardia (sanidad: para rendir, guardias).
    """
    delta = (max(0, min(100, score)) - 50) // 2
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


def family_cost(
    kind: ShiftKind,
    now: float,
    *,
    remote: bool = False,
    tz: ZoneInfo = TIMEZONE,
    abroad: bool = False,
) -> int:
    """Lo que cambia la familia un turno (casi siempre, a peor).

    La madrugada se mide en la hora de donde trabajas (`tz`); el domingo y los
    festivos, en la de casa.
    """
    cost = 0
    if kind is ShiftKind.GUARD:
        cost += GUARD_FAMILY
    elif kind is not ShiftKind.ORDINARY:
        cost += FAMILY_EXTRA
    if is_night(now, tz):
        cost += FAMILY_NIGHT
    if local_day(now).weekday() == 6 or holiday(local_day(now)) is not None:
        cost += FAMILY_SUNDAY
    if remote:
        cost += REMOTE_FAMILY
    if abroad:
        cost += ABROAD_FAMILY
    return cost


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
    (24, 12): "Nochebuena",
    (25, 12): "Navidad",
    (31, 12): "Nochevieja",
}


def holiday(day: date) -> str | None:
    """Nombre del festivo de `day`, si lo es."""
    return HOLIDAYS.get((day.day, day.month))


def apply_performance(contract: Contract, delta: int) -> str | None:
    """Mueve la barra y aplica el aviso o la degradación.

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
    if contract.level <= 1:
        # En el primer puesto no hay a dónde bajar: se queda el aviso.
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
) -> list[str]:
    """Lo que falta para que te ofrezcan el ascenso (vacío = ya toca).

    Args:
        days_in_position: Días distintos fichando en el puesto actual.
        trainings: Formaciones que ya tiene el miembro.
        today: Día de hoy (ISO); si rechazó una oferta hoy, no se repite.
    """
    if contract.level >= job.top:
        return ["Ya estás en lo más alto. Más arriba solo hay una puerta giratoria."]
    position = job.position(contract.level)
    missing: list[str] = []
    if contract.performance < PERFORMANCE_MAX:
        missing.append(f"Llenar la barra de rendimiento ({contract.performance}/100)")
    if days_in_position < position.days:
        missing.append(f"Fichar {position.days} días distintos en el puesto ({days_in_position})")
    for task in position.tasks:
        done = contract.progress.get(task.stat, 0)
        if done < task.goal:
            missing.append(f"{task.text} ({done}/{task.goal})")
    following = job.position(contract.level + 1)
    if following.training is not None and following.training.key not in trainings:
        missing.append(f"Conseguir {following.training.name} (en 🎓 Formación)")
    if contract.declined_day == today and not missing:
        missing.append("Rechazaste el ascenso hoy. Mañana te lo vuelven a ofrecer.")
    return missing


def promote(contract: Contract, now: float) -> None:
    """Sube un puesto: barra a la mitad, tareas a cero y nueva antigüedad."""
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


def roll(rng: random.Random, chance: float) -> bool:
    """`True` con probabilidad `chance`."""
    return chance > 0 and rng.random() < chance
