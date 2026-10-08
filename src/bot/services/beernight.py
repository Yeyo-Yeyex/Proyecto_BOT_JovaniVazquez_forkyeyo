"""Reglas de la beernight: ajustes, mandamientos activos, eventos y resúmenes.

Lógica pura, sin Discord ni base de datos (el estado vive en el cog y el
histórico en `bot.repositories.beernight`). Los textos de serie están en
`bot.services.beernight_catalog`.

Cómo funciona una noche:

- Hay unos cuantos **mandamientos activos** (`Settings.active`) que rotan
  cada `Settings.rotation` minutos: salen los más antiguos y entran otros del
  repertorio (`mandate_pool`), sin repetir familia si se puede.
- Cada `Settings.event_min`-`Settings.event_max` minutos salta un **evento**
  (`pick_event`) entre la gente presente que no se ha retirado.
- Cada sorbo se apunta con su motivo (`Reason`). El **tope por hora**
  (`Settings.cap`, 0 = sin tope) perdona lo que pase de ahí.
- Al cerrar, `summarize` saca el marcador, el MVP, el chivato, el mentiroso y
  el mandamiento más incumplido.

La beernight no mueve yapdollars: solo cuenta sorbos. Los logros que da sí
pagan su premio, pero eso lo hace el cog de logros como con cualquier otro.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from bot.services.beernight_catalog import (
    CUSTOM_FAMILY,
    DECREE_FAMILY,
    EVENTS,
    FAMILIES,
    MANDATES,
    EventKind,
    Mandate,
    NightEvent,
    Target,
)

#: Mililitros que se le suponen a un sorbo, para el resumen en litros.
SIP_ML = 25
#: Largo máximo del texto de un mandamiento propio.
MAX_CUSTOM_LENGTH = 120
#: Mandamientos propios por servidor (lo que cabe en un desplegable de Discord).
MAX_CUSTOM = 25
#: Sorbos que puede pedir un mandamiento propio.
MAX_CUSTOM_SIPS = 5
#: Cuánto dura un chivatazo sin confirmar antes de archivarse.
REPORT_SECONDS = 300
#: Cuánto tienen los demás para resolver un duelo, un reto o un reparto.
VOTE_SECONDS = 300
#: Votos de «mentira» que tumban un chivatazo (con 3 personas o menos, basta uno).
LIE_VOTES = 2
#: Horas que cuentan para el tope por persona.
CAP_WINDOW_SECONDS = 3600


class BeernightError(Exception):
    """Error esperado; su mensaje es apto para enseñárselo al usuario."""


class Reason(StrEnum):
    """Por qué bebe alguien. El valor se guarda en el histórico: no cambiarlo."""

    CONFESSION = "confesion"
    REPORT = "chivatazo"
    LIE = "mentira"
    EVENT = "evento"
    DUEL = "duelo"
    CHALLENGE = "reto"
    GIFT = "reparto"
    TOAST = "brindis"


REASON_LABELS: dict[Reason, str] = {
    Reason.CONFESSION: "🍺 confesión",
    Reason.REPORT: "🚨 chivatazo",
    Reason.LIE: "🤥 chivatazo falso",
    Reason.EVENT: "🎲 evento",
    Reason.DUEL: "⚔️ duelo",
    Reason.CHALLENGE: "🎤 reto",
    Reason.GIFT: "🎁 reparto",
    Reason.TOAST: "🥂 brindis",
}


# -- Sonidos ----------------------------------------------------------------------------


class SoundSlot(StrEnum):
    """Momento de la noche en el que suena un audio. El valor es la carpeta: no cambiarlo."""

    DRINK = "beber"
    EVENT = "evento"
    REPORT = "chivato"
    START = "inicio"
    END = "fin"


SOUND_LABELS: dict[SoundSlot, str] = {
    SoundSlot.DRINK: "🍺 Alguien bebe",
    SoundSlot.EVENT: "🎲 Evento aleatorio",
    SoundSlot.REPORT: "🚨 Chivatazo",
    SoundSlot.START: "▶️ Empieza la noche",
    SoundSlot.END: "🏁 Se acaba la noche",
}
#: Duración máxima de un audio de la beernight (los de entrada son de 3 s).
MAX_SOUND_SECONDS = 6.0
#: Audios por momento; si hay varios, suena uno al azar.
MAX_SOUNDS_PER_SLOT = 5
#: Segundos mínimos entre dos audios de «alguien bebe», para que no sea una sirena.
DRINK_SOUND_GAP = 20.0


# -- Ajustes ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bounds:
    """Límites de un ajuste numérico."""

    low: int
    high: int
    label: str


EVENT_BOUNDS = Bounds(2, 90, "Minutos entre eventos")
ACTIVE_BOUNDS = Bounds(2, 10, "Mandamientos activos")
ROTATION_BOUNDS = Bounds(5, 180, "Minutos entre rotaciones")
CAP_BOUNDS = Bounds(0, 100, "Tope de sorbos por hora")


@dataclass(frozen=True, slots=True)
class Settings:
    """Ajustes de la beernight de un servidor.

    Attributes:
        event_min: Mínimo de minutos entre dos eventos aleatorios.
        event_max: Máximo de minutos entre dos eventos aleatorios.
        active: Mandamientos activos a la vez.
        rotation: Minutos entre dos rotaciones de mandamientos.
        cap: Sorbos por persona y hora; lo que pasa se perdona. 0 = sin tope.
        sound: Si el bot pone los sonidos en la llamada.
        disabled_families: Familias de serie apagadas en este servidor.
    """

    event_min: int = 8
    event_max: int = 12
    active: int = 5
    rotation: int = 20
    cap: int = 0
    sound: bool = True
    disabled_families: frozenset[str] = frozenset()

    def validated(self) -> Settings:
        """Comprueba los límites y devuelve los mismos ajustes.

        Raises:
            BeernightError: Con el primer ajuste fuera de rango.
        """
        for value, bounds in (
            (self.event_min, EVENT_BOUNDS),
            (self.event_max, EVENT_BOUNDS),
            (self.active, ACTIVE_BOUNDS),
            (self.rotation, ROTATION_BOUNDS),
            (self.cap, CAP_BOUNDS),
        ):
            if not bounds.low <= value <= bounds.high:
                raise BeernightError(
                    f"{bounds.label}: tiene que estar entre {bounds.low} y {bounds.high}."
                )
        if self.event_min > self.event_max:
            raise BeernightError("El mínimo de minutos entre eventos no puede pasar del máximo.")
        unknown = self.disabled_families - {f.key for f in FAMILIES}
        if unknown:
            raise BeernightError("Hay familias de mandamientos que no existen.")
        return self

    def to_json(self) -> dict[str, object]:
        """Ajustes listos para `json.dumps`."""
        return {
            "event_min": self.event_min,
            "event_max": self.event_max,
            "active": self.active,
            "rotation": self.rotation,
            "cap": self.cap,
            "sound": self.sound,
            "disabled_families": sorted(self.disabled_families),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> Settings:
        """Lee unos ajustes guardados. Lo que falte o no valga vuelve al valor por defecto."""
        base = cls()
        values: dict[str, object] = {}
        for name in ("event_min", "event_max", "active", "rotation", "cap"):
            value = data.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                values[name] = value
        if isinstance(data.get("sound"), bool):
            values["sound"] = data["sound"]
        families = data.get("disabled_families")
        if isinstance(families, list):
            known = {f.key for f in FAMILIES}
            values["disabled_families"] = frozenset(
                f for f in families if isinstance(f, str) and f in known
            )
        candidate = replace(base, **values)  # type: ignore[arg-type]
        try:
            return candidate.validated()
        except BeernightError:
            return base


def parse_number(raw: str, bounds: Bounds) -> int:
    """Lee un número escrito en un formulario y comprueba su rango.

    Raises:
        BeernightError: Si no es un entero o se sale de `bounds`.
    """
    cleaned = raw.strip()
    if not cleaned.lstrip("-").isdigit():
        raise BeernightError(f"{bounds.label}: escribe un número entero.")
    value = int(cleaned)
    if not bounds.low <= value <= bounds.high:
        raise BeernightError(f"{bounds.label}: tiene que estar entre {bounds.low} y {bounds.high}.")
    return value


# -- Mandamientos -----------------------------------------------------------------------


def custom_mandate(custom_id: int, text: str, sips: int) -> Mandate:
    """Un mandamiento propio del servidor, con su clave estable `c<id>`."""
    return Mandate(f"c{custom_id}", CUSTOM_FAMILY.key, text, sips)


def decree_mandate(event: NightEvent) -> Mandate:
    """El mandamiento temporal que mete un decreto."""
    return Mandate(f"ev:{event.key}", DECREE_FAMILY.key, event.rule, event.sips)


def clean_custom_text(text: str) -> str:
    """Valida el texto de un mandamiento propio y lo deja en una línea.

    Raises:
        BeernightError: Si está vacío o es demasiado largo.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        raise BeernightError("El mandamiento no puede estar vacío.")
    if len(cleaned) > MAX_CUSTOM_LENGTH:
        raise BeernightError(
            f"El mandamiento es demasiado largo (máximo {MAX_CUSTOM_LENGTH} caracteres)."
        )
    return cleaned


def mandate_pool(settings: Settings, custom: Iterable[Mandate] = ()) -> list[Mandate]:
    """Repertorio del que salen los mandamientos: serie encendida más los propios."""
    pool = [m for m in MANDATES if m.family not in settings.disabled_families]
    pool.extend(custom)
    return pool


def pick_mandates(
    pool: Sequence[Mandate],
    count: int,
    rng: random.Random,
    *,
    exclude: Iterable[str] = (),
    avoid_families: Iterable[str] = (),
) -> list[Mandate]:
    """Elige `count` mandamientos nuevos, variando familias todo lo posible.

    Primero coge uno de cada familia que no esté ya en juego; cuando se acaban
    las familias, completa con cualquiera. Los propios del servidor van en su
    familia y salen como uno más.

    Args:
        exclude: Claves que ya están en juego (no se repiten).
        avoid_families: Familias ya en juego, que se dejan para el final.
    """
    taken = set(exclude)
    candidates = [m for m in pool if m.key not in taken]
    rng.shuffle(candidates)
    chosen: list[Mandate] = []
    families = set(avoid_families)
    for mandate in candidates:
        if len(chosen) >= count:
            break
        if mandate.family not in families:
            chosen.append(mandate)
            families.add(mandate.family)
    for mandate in candidates:
        if len(chosen) >= count:
            break
        if mandate not in chosen:
            chosen.append(mandate)
    return chosen


def rotation_size(active: int) -> int:
    """Cuántos mandamientos cambian en cada rotación: un tercio, al menos uno."""
    return max(1, -(-active // 3))


# -- Eventos ----------------------------------------------------------------------------


def next_event_delay(settings: Settings, rng: random.Random) -> float:
    """Segundos hasta el siguiente evento aleatorio."""
    return rng.uniform(settings.event_min, settings.event_max) * 60


def pick_event(
    players: int,
    rng: random.Random,
    *,
    events: Sequence[NightEvent] = EVENTS,
    recent: Iterable[str] = (),
) -> NightEvent | None:
    """Elige un evento que se pueda jugar con `players` personas presentes.

    No repite los de `recent` mientras quede otro. Sin nadie presente, ninguno.
    """
    if players < 1:
        return None
    playable = [e for e in events if e.players_needed <= players]
    fresh = [e for e in playable if e.key not in set(recent)]
    pool = fresh or playable
    return rng.choice(pool) if pool else None


@dataclass(frozen=True, slots=True)
class Casting:
    """A quién le toca un evento.

    Attributes:
        who: Protagonista (el de un reto, un reparto o un `DRINK` de uno).
        a: Primer participante de un duelo o de una pareja.
        b: Segundo participante.
        drinkers: Quien bebe ya, sin esperar a nadie (eventos `DRINK`).
    """

    who: int | None = None
    a: int | None = None
    b: int | None = None
    drinkers: tuple[int, ...] = ()


def cast_event(
    event: NightEvent,
    players: Sequence[int],
    rng: random.Random,
    *,
    sips: Mapping[int, int] | None = None,
    host: int | None = None,
    newest: int | None = None,
) -> Casting:
    """Reparte los papeles de un evento entre la gente presente.

    Args:
        players: Presentes y sin retirar, en cualquier orden.
        sips: Sorbos que lleva cada uno esta noche (para `MOST` y `LEAST`).
        host: Anfitrión, si está presente.
        newest: El último en llegar, si está presente.

    Raises:
        BeernightError: Si no hay gente suficiente para el evento.
    """
    if len(players) < event.players_needed:
        raise BeernightError("No hay gente suficiente para este evento.")
    sips = sips or {}
    if event.kind is EventKind.DUEL:
        a, b = rng.sample(list(players), 2)
        return Casting(a=a, b=b)
    if event.kind in (EventKind.CHALLENGE, EventKind.GIFT):
        return Casting(who=rng.choice(list(players)))
    if event.kind is not EventKind.DRINK:
        return Casting()
    target = event.target
    if target is Target.ALL:
        return Casting(drinkers=tuple(players))
    if target is Target.PAIR:
        a, b = rng.sample(list(players), 2)
        return Casting(a=a, b=b, drinkers=(a, b))
    if target is Target.ALL_BUT_ONE:
        spared = rng.choice(list(players))
        return Casting(who=spared, drinkers=tuple(p for p in players if p != spared))
    if target is Target.MOST or target is Target.LEAST:
        values = [sips.get(p, 0) for p in players]
        best = max(values) if target is Target.MOST else min(values)
        tied = [p for p in players if sips.get(p, 0) == best]
        who = rng.choice(tied)
    elif target is Target.HOST and host in players:
        who = host
    elif target is Target.NEWEST and newest in players:
        who = newest
    else:
        who = rng.choice(list(players))
    return Casting(who=who, drinkers=(who,))


def event_text(event: NightEvent, casting: Casting, name: Callable[[int | None], str]) -> str:
    """Anuncio del evento con los nombres ya puestos.

    Args:
        name: Cómo escribir a cada persona (una mención, normalmente).
    """
    return event.text.format(
        who=name(casting.who),
        a=name(casting.a),
        b=name(casting.b),
        n=event.sips,
        m=event.minutes,
    )


# -- Sorbos y tope ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SipRecord:
    """Un apunte del histórico: alguien bebe por algo.

    Attributes:
        user_id: Quien bebe.
        sips: Cuántos sorbos (ya con el tope aplicado).
        reason: Por qué.
        mandate: Clave del mandamiento incumplido, o del evento, si lo hay.
        by_user_id: Quien lo provoca (el chivato, quien reparte…), si no es él mismo.
        created_at: Marca de tiempo Unix.
        forgiven: Sorbos que el tope ha perdonado.
    """

    user_id: int
    sips: int
    reason: Reason
    mandate: str | None = None
    by_user_id: int | None = None
    created_at: float = 0.0
    forgiven: int = 0


def apply_cap(wanted: int, recent: int, cap: int) -> tuple[int, int]:
    """Aplica el tope por hora.

    Args:
        wanted: Sorbos que tocan.
        recent: Sorbos que ya lleva en la última hora.
        cap: Tope (0 = sin tope).

    Returns:
        `(sorbos que bebe, sorbos perdonados)`.
    """
    if cap <= 0:
        return wanted, 0
    room = max(0, cap - recent)
    drunk = min(wanted, room)
    return drunk, wanted - drunk


def lie_votes_needed(players: int) -> int:
    """Votos de «mentira» que tumban un chivatazo con `players` personas en la noche."""
    return 1 if players <= 3 else LIE_VOTES


# -- Resumen ----------------------------------------------------------------------------


@dataclass(slots=True)
class Summary:
    """Lo que dejó una noche, sacado de sus apuntes.

    Attributes:
        sips: Sorbos por persona (todas las que bebieron algo).
        reports_ok: Chivatazos confirmados por chivato.
        lies: Chivatazos falsos por chivato.
        confessions: Confesiones por persona.
        broken: Veces que se incumplió cada mandamiento (por clave).
        kinds: Motivos distintos por los que bebió cada persona.
        forgiven: Sorbos perdonados por el tope, por persona.
        biggest: Mayor trago de una sola vez, por persona.
        given: Sorbos que cada uno hizo beber a otros (repartos y chivatazos).
    """

    sips: Counter[int] = field(default_factory=Counter)
    reports_ok: Counter[int] = field(default_factory=Counter)
    lies: Counter[int] = field(default_factory=Counter)
    confessions: Counter[int] = field(default_factory=Counter)
    broken: Counter[str] = field(default_factory=Counter)
    kinds: dict[int, set[Reason]] = field(default_factory=dict)
    forgiven: Counter[int] = field(default_factory=Counter)
    biggest: Counter[int] = field(default_factory=Counter)
    given: Counter[int] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        """Sorbos de toda la noche."""
        return sum(self.sips.values())

    def mvp(self) -> int | None:
        """Quien más bebió (el primero en llegar a esa cifra si hay empate)."""
        return _top(self.sips)

    def snitch(self) -> int | None:
        """Quien más chivatazos acertó."""
        return _top(self.reports_ok)

    def liar(self) -> int | None:
        """Quien más chivatazos falsos metió."""
        return _top(self.lies)

    def most_broken(self) -> str | None:
        """El mandamiento que más veces se incumplió."""
        return _top(self.broken)


def _top[K](counter: Counter[K]) -> K | None:
    best = counter.most_common(1)
    return best[0][0] if best and best[0][1] > 0 else None


def summarize(records: Iterable[SipRecord]) -> Summary:
    """Resume los apuntes de una noche."""
    summary = Summary()
    for record in records:
        summary.sips[record.user_id] += record.sips
        summary.forgiven[record.user_id] += record.forgiven
        summary.kinds.setdefault(record.user_id, set()).add(record.reason)
        summary.biggest[record.user_id] = max(summary.biggest[record.user_id], record.sips)
        if record.reason is Reason.REPORT and record.by_user_id is not None:
            summary.reports_ok[record.by_user_id] += 1
        if record.reason is Reason.LIE:
            summary.lies[record.user_id] += 1
        if record.reason is Reason.CONFESSION:
            summary.confessions[record.user_id] += 1
        if record.reason in (Reason.REPORT, Reason.GIFT) and record.by_user_id is not None:
            summary.given[record.by_user_id] += record.sips
        if record.mandate and record.reason in (Reason.REPORT, Reason.CONFESSION):
            summary.broken[record.mandate] += 1
    return summary


def liters(sips: int) -> str:
    """Sorbos en litros, con coma decimal (a `SIP_ML` mililitros el sorbo)."""
    value = sips * SIP_ML / 1000
    return f"{value:.1f}".replace(".", ",")


def duration_text(seconds: float) -> str:
    """Duración legible: «2 h 15 min» o «40 min»."""
    minutes = max(0, int(seconds // 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{minutes} min"
