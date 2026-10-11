"""Telemetría interna: contadores en memoria, volcado por lotes e informe agregado.

Responsabilidad: contar *cuánto* se usa el bot (comandos, botones, mensajes,
voz...) y cómo va el sistema, sin guardar nunca contenido de mensajes. Los
contadores viven en memoria (`TelemetryService.count` es síncrono y barato,
porque se llama en cada mensaje y clic) y se vuelcan a SQLite por lotes con
`flush`. El informe (`build_report`) es una función pura que convierte los
agregados que devuelve el repositorio en un JSON pensado para que lo lea un
agente que decide mejoras.

Límites: no importa Discord ni toca SQLite (eso es `bot.repositories.telemetry`),
no mueve dinero y no da logros. Es interna; los comandos que la usen van aparte.

Tipos de evento (`kind`) usados. La lista es libre y no se valida, pero estos
son los acordados:

- `slash`: comando con `/`. `text`: el mismo comando escrito con `.`.
- `button`: clic en un botón o menú. `modal`: envío de un formulario.
- `message`: mensaje en un canal (el nombre es el id del canal).
- `message_type`: clase de mensaje (texto, imagen, enlace, sticker...).
- `reaction`: reacción añadida (el nombre es el id del canal).
- `voice`: actividad de voz (el nombre es el id del canal).
- `member`: entradas y salidas de miembros.
- `log`: avisos y errores registrados por el bot.
- `render`: dibujos de imágenes o GIF (con su duración en `ms`).

Privacidad: el único dato por persona es el `user_id` en `UserKey`, que se
borra a los 90 días y nunca sale en el informe (se seudonimiza como u1, u2...).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any

from bot.repositories.economy import (
    HK_ACCOUNT_ID,
    PORRA_ACCOUNT_ID,
    SHOP_ACCOUNT_ID,
    SLOTS_POT_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
)
from bot.services.levels import TIMEZONE

if TYPE_CHECKING:
    from bot.repositories.telemetry import TelemetryRepository

logger = logging.getLogger(__name__)

MAX_NAME = 100
"""Longitud máxima de un nombre de evento; lo que sobra se corta."""

PENDING_SECONDS = 600
"""Segundos que una latencia de primera respuesta espera a que la recoja `take_answer`."""

MAX_PENDING = 5000
"""Latencias pendientes como mucho; al pasarse se tiran las más antiguas."""

LATENCY_BUCKETS = (500, 1000, 3000)
"""Cortes de latencia en ms: `<500`, `<1000`, `<3000` y `>=3000`."""

REPORT_VERSION = 1

# Tipos cuyo nombre es un id de canal que se traduce a «#nombre» en el informe.
CHANNEL_KINDS = frozenset({"message", "voice", "reaction"})

# Nombres de las cuentas del sistema de la economía (user_id <= 0) en el informe.
SYSTEM_ACCOUNTS: dict[int, str] = {
    STATE_ACCOUNT_ID: "estado",
    SLOTS_POT_ACCOUNT_ID: "bote_tragaperras",
    SHOP_ACCOUNT_ID: "tienda",
    HK_ACCOUNT_ID: "hong_kong",
    PORRA_ACCOUNT_ID: "porras",
}


@dataclass(frozen=True)
class CounterKey:
    """Clave de un contador: día canario, hora local (0-23), servidor, tipo y nombre."""

    day: str
    hour: int
    guild_id: int
    kind: str
    name: str


@dataclass
class Counter:
    """Valores acumulados de una clave: eventos, errores y distribución de latencias (ms)."""

    events: int = 0
    errors: int = 0
    ms_count: int = 0
    ms_total: int = 0
    ms_max: int = 0
    ms_lt500: int = 0
    ms_lt1000: int = 0
    ms_lt3000: int = 0
    ms_ge3000: int = 0

    def add(self, *, events: int, error: bool, ms: float | None) -> None:
        """Suma `events` eventos; cuenta un error si `error` y la latencia `ms` si no es None."""
        self.events += events
        if error:
            self.errors += 1
        if ms is None:
            return
        rounded = round(ms)
        self.ms_count += 1
        self.ms_total += rounded
        self.ms_max = max(self.ms_max, rounded)
        low, mid, high = LATENCY_BUCKETS
        if ms < low:
            self.ms_lt500 += 1
        elif ms < mid:
            self.ms_lt1000 += 1
        elif ms < high:
            self.ms_lt3000 += 1
        else:
            self.ms_ge3000 += 1

    def merge(self, other: Counter) -> None:
        """Suma otro contador al actual (el máximo se queda con el mayor)."""
        self.events += other.events
        self.errors += other.errors
        self.ms_count += other.ms_count
        self.ms_total += other.ms_total
        self.ms_max = max(self.ms_max, other.ms_max)
        self.ms_lt500 += other.ms_lt500
        self.ms_lt1000 += other.ms_lt1000
        self.ms_lt3000 += other.ms_lt3000
        self.ms_ge3000 += other.ms_ge3000


@dataclass(frozen=True)
class UserKey:
    """Un usuario distinto que hizo algo (día, servidor, tipo, nombre) ese día."""

    day: str
    guild_id: int
    kind: str
    name: str
    user_id: int


@dataclass(frozen=True)
class SystemSample:
    """Muestra del estado del proceso. `memory_mb` es la del contenedor (cgroup) y puede faltar."""

    at: float
    day: str
    memory_mb: float | None
    rss_mb: float
    loop_lag_ms: float
    gateway_ms: float | None
    chromium: int
    node: int


@dataclass
class Batch:
    """Lo acumulado entre dos volcados."""

    counters: dict[CounterKey, Counter] = field(default_factory=dict)
    users: set[UserKey] = field(default_factory=set)
    samples: list[SystemSample] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Dice si no hay nada que escribir."""
        return not (self.counters or self.users or self.samples)


@dataclass
class ReportData:
    """Agregados que el repositorio entrega a `build_report`.

    - `usage`: dicts con `kind, name, events, errors, ms_count, ms_total, ms_max,
      lt500, lt1000, lt3000, ge3000`.
    - `daily`: tuplas `(day, kind, events, errors)`.
    - `hourly`: tuplas `(hour, kind, events)` solo de horas 0-23.
    - `users`: tuplas `(day, kind, name, user_id)`.
    - `system`: dicts con `day, memory_max, memory_avg, rss_max, lag_max,
      gateway_avg, chromium_max, node_max`.
    - `casino`: dicts con `game, plays, free, players, wagered, paid`.
    - `balances`: saldos de los miembros; `system_accounts`: `{user_id <= 0: saldo}`.
    - `ledger`: dicts con `reason, moves, inflow, outflow` (solo miembros).
    """

    usage: list[dict[str, Any]] = field(default_factory=list)
    daily: list[tuple[str, str, int, int]] = field(default_factory=list)
    hourly: list[tuple[int, str, int]] = field(default_factory=list)
    users: list[tuple[str, str, str, int]] = field(default_factory=list)
    system: list[dict[str, Any]] = field(default_factory=list)
    casino: list[dict[str, Any]] = field(default_factory=list)
    balances: list[int] = field(default_factory=list)
    system_accounts: dict[int, int] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)


def local_moment(at: float) -> tuple[str, int]:
    """Devuelve el día (`YYYY-MM-DD`) y la hora (0-23) canarios de un epoch."""
    moment = datetime.fromtimestamp(at, TIMEZONE)
    return moment.date().isoformat(), moment.hour


def gini(values: Sequence[int]) -> float:
    """Índice de Gini de `values`: 0 si está vacío o todo es cero, hacia 1 cuanto más desigual."""
    total = sum(values)
    count = len(values)
    if count == 0 or total <= 0:
        return 0.0
    ordered = sorted(values)
    weighted = sum((2 * rank - count - 1) * value for rank, value in enumerate(ordered, 1))
    return weighted / (count * total)


def _round_or_none(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def _channel_label(kind: str, name: str, channel_names: Mapping[int, str]) -> str:
    """Traduce el id de canal de `name` a «#nombre» si se conoce; si no, lo deja igual."""
    if kind in CHANNEL_KINDS and name.lstrip("-").isdigit():
        found = channel_names.get(int(name))
        if found is not None:
            return f"#{found}"
    return name


def _usage_entries(
    data: ReportData,
    channel_names: Mapping[int, str],
    users_by_key: Mapping[tuple[str, str], set[int]],
) -> list[dict[str, Any]]:
    rows = sorted(data.usage, key=lambda r: (r["kind"], -int(r["events"]), r["name"]))
    entries = []
    for row in rows:
        count = int(row["ms_count"])
        entries.append(
            {
                "tipo": row["kind"],
                "nombre": _channel_label(row["kind"], row["name"], channel_names),
                "eventos": int(row["events"]),
                "errores": int(row["errors"]),
                "usuarios": len(users_by_key.get((row["kind"], row["name"]), ())),
                "ms_medio": round(int(row["ms_total"]) / count, 1) if count else None,
                "ms_max": int(row["ms_max"]),
                "ms": {
                    "<500": int(row["lt500"]),
                    "<1000": int(row["lt1000"]),
                    "<3000": int(row["lt3000"]),
                    ">=3000": int(row["ge3000"]),
                },
            }
        )
    return entries


def _daily_entries(data: ReportData) -> dict[str, dict[str, dict[str, int]]]:
    per_day: dict[str, dict[str, dict[str, int]]] = {}
    for day, kind, events, errors in data.daily:
        per_day.setdefault(day, {})[kind] = {
            "eventos": int(events),
            "errores": int(errors),
            "usuarios": 0,
        }
    distinct: dict[tuple[str, str], set[int]] = {}
    for day, kind, _name, user_id in data.users:
        distinct.setdefault((day, kind), set()).add(user_id)
    for (day, kind), ids in distinct.items():
        entry = per_day.setdefault(day, {}).setdefault(
            kind, {"eventos": 0, "errores": 0, "usuarios": 0}
        )
        entry["usuarios"] = len(ids)
    return dict(sorted(per_day.items()))


def _activity(
    data: ReportData, since: date, channel_names: Mapping[int, str]
) -> dict[str, dict[str, list[int]]]:
    """Días de actividad por usuario seudonimizado (u1, u2... sin el user_id)."""
    first_seen: dict[int, str] = {}
    for day, _kind, _name, user_id in data.users:
        if user_id not in first_seen or day < first_seen[user_id]:
            first_seen[user_id] = day
    order = sorted(first_seen, key=lambda uid: (first_seen[uid], uid))
    alias = {uid: f"u{index}" for index, uid in enumerate(order, 1)}
    offsets: dict[str, dict[str, set[int]]] = {}
    for day, kind, name, user_id in data.users:
        label = f"{kind}:{_channel_label(kind, name, channel_names)}"
        shift = (date.fromisoformat(day) - since).days
        offsets.setdefault(alias[user_id], {}).setdefault(label, set()).add(shift)
    return {
        who: {label: sorted(days) for label, days in sorted(labels.items())}
        for who, labels in sorted(offsets.items(), key=lambda item: int(item[0][1:]))
    }


def _economy(data: ReportData) -> dict[str, Any]:
    balances = data.balances
    total = sum(balances)
    holders = sorted((b for b in balances if b > 0), reverse=True)
    top = math.ceil(len(holders) * 0.1)
    top_share = sum(holders[:top]) / total if total > 0 else 0.0
    accounts = {
        SYSTEM_ACCOUNTS.get(account, f"cuenta_{account}"): int(balance)
        for account, balance in sorted(data.system_accounts.items(), reverse=True)
    }
    reasons = sorted(
        (
            {
                "motivo": row["reason"],
                "movimientos": int(row["moves"]),
                "entradas": int(row["inflow"]),
                "salidas": int(row["outflow"]),
            }
            for row in data.ledger
        ),
        key=lambda r: (-(r["entradas"] + r["salidas"]), r["motivo"]),
    )
    return {
        "masa_monetaria": int(total),
        "miembros_con_saldo": len(holders),
        "gini": round(gini(balances), 4),
        "top10_pct": round(top_share, 4),
        "cuentas_sistema": accounts,
        "motivos": reasons,
    }


def build_report(
    data: ReportData,
    *,
    generated_at: float,
    days: int,
    commands: Iterable[str],
    channel_names: Mapping[int, str],
    guild_id: int = 0,
) -> dict[str, Any]:
    """Convierte los agregados en el informe JSON (claves en español, serializable tal cual).

    Args:
        data: Agregados del repositorio.
        generated_at: Epoch del momento del informe; fija el «hasta» canario.
        days: Ventana en días (incluye hoy).
        commands: Nombres de todos los comandos del bot, para `comandos_sin_uso`.
        channel_names: Nombres de canal por id, para traducir `message`, `voice`, `reaction`.
        guild_id: Servidor del informe (solo se copia a `servidor`).

    El `user_id` nunca aparece: la actividad va por seudónimos u1, u2...
    """
    until = datetime.fromtimestamp(generated_at, TIMEZONE).date()
    since = until - timedelta(days=max(days, 1) - 1)
    users_by_key: dict[tuple[str, str], set[int]] = {}
    for _day, kind, name, user_id in data.users:
        users_by_key.setdefault((kind, name), set()).add(user_id)
    hourly: dict[str, list[int]] = {}
    for hour, kind, events in data.hourly:
        if 0 <= hour <= 23:
            hourly.setdefault(kind, [0] * 24)[hour] += int(events)
    used = {row["name"] for row in data.usage if row["kind"] in ("slash", "text")}
    system = {
        row["day"]: {
            "memoria_mb_max": _round_or_none(row["memory_max"]),
            "memoria_mb_media": _round_or_none(row["memory_avg"]),
            "rss_mb_max": _round_or_none(row["rss_max"]),
            "lag_ms_max": _round_or_none(row["lag_max"]),
            "gateway_ms_media": _round_or_none(row["gateway_avg"]),
            "chromium_max": row["chromium_max"],
            "node_max": row["node_max"],
        }
        for row in sorted(data.system, key=lambda r: r["day"])
    }
    casino = [
        {
            "juego": row["game"],
            "jugadas": int(row["plays"]),
            "gratis": int(row["free"]),
            "jugadores": int(row["players"]),
            "apostado": int(row["wagered"]),
            "pagado": int(row["paid"]),
            "rtp": round(int(row["paid"]) / int(row["wagered"]), 4)
            if int(row["wagered"])
            else None,
        }
        for row in sorted(data.casino, key=lambda r: r["game"])
    ]
    return {
        "version": REPORT_VERSION,
        "generado": datetime.fromtimestamp(generated_at, TIMEZONE).isoformat(timespec="seconds"),
        "servidor": guild_id,
        "desde": since.isoformat(),
        "hasta": until.isoformat(),
        "dias": days,
        "uso": _usage_entries(data, channel_names, users_by_key),
        "por_dia": _daily_entries(data),
        "por_hora": dict(sorted(hourly.items())),
        "comandos_sin_uso": sorted(set(commands) - used),
        "actividad": _activity(data, since, channel_names),
        "sistema": system,
        "casino": casino,
        "economia": _economy(data),
    }


class TelemetryService:
    """Contadores en memoria con volcado periódico a SQLite.

    Args:
        repository: Destino de los volcados; con `None` los contadores se acumulan y
            `flush` no hace nada (útil en pruebas o si la telemetría está apagada).
        clock: Reloj en epoch, sustituible en pruebas.
    """

    def __init__(
        self,
        repository: TelemetryRepository | None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self._counters: dict[CounterKey, Counter] = {}
        self._users: set[UserKey] = set()
        self._samples: list[SystemSample] = []
        # id de interacción -> (ms, momento en que se guardó); el orden es la antigüedad.
        self._answers: OrderedDict[int, tuple[float, float]] = OrderedDict()
        self._flush_lock = asyncio.Lock()

    def count(
        self,
        kind: str,
        name: str,
        *,
        guild_id: int | None,
        user_id: int | None = None,
        ms: float | None = None,
        error: bool = False,
        events: int = 1,
        at: float | None = None,
    ) -> None:
        """Cuenta un evento en memoria (síncrono y barato).

        `guild_id=None` se guarda como 0 (sin servidor). `name` se corta a `MAX_NAME`.
        Si hay `user_id`, también se apunta que ese usuario hizo esto ese día.
        """
        name = name[:MAX_NAME]
        day, hour = local_moment(self.clock() if at is None else at)
        guild = guild_id or 0
        key = CounterKey(day, hour, guild, kind, name)
        counter = self._counters.get(key)
        if counter is None:
            counter = self._counters[key] = Counter()
        counter.add(events=events, error=error, ms=ms)
        if user_id is not None:
            self._users.add(UserKey(day, guild, kind, name, user_id))

    def answered(self, interaction_id: int, ms: float) -> None:
        """Guarda la latencia de primera respuesta hasta que `take_answer` la recoja.

        Si ya había una para ese id no se pisa (vale la primera). Pasado `MAX_PENDING`
        se tiran las más antiguas.
        """
        if interaction_id in self._answers:
            return
        self._answers[interaction_id] = (ms, self.clock())
        while len(self._answers) > MAX_PENDING:
            self._answers.popitem(last=False)

    def take_answer(self, interaction_id: int) -> float | None:
        """Saca y borra la latencia guardada de una interacción (None si no hay)."""
        found = self._answers.pop(interaction_id, None)
        return None if found is None else found[0]

    def sample(self, sample: SystemSample) -> None:
        """Apunta una muestra del estado del sistema."""
        self._samples.append(sample)

    def drain(self) -> Batch:
        """Se lleva todo lo acumulado, deja los buffers vacíos y tira las latencias caducadas."""
        batch = Batch(self._counters, self._users, self._samples)
        self._counters = {}
        self._users = set()
        self._samples = []
        limit = self.clock() - PENDING_SECONDS
        for interaction_id in [i for i, (_ms, stored) in self._answers.items() if stored < limit]:
            del self._answers[interaction_id]
        return batch

    async def flush(self) -> None:
        """Vuelca lo acumulado al repositorio.

        Si `write` falla se registra cuántos contadores se pierden y se sigue: perder
        un minuto de métricas es aceptable y no se reintenta.
        """
        async with self._flush_lock:
            batch = self.drain()
            if batch.is_empty() or self.repository is None:
                return
            try:
                await self.repository.write(batch)
            except Exception:
                logger.exception(
                    "No se pudo volcar la telemetría: se pierden %d contadores",
                    len(batch.counters),
                )

    async def prune(self) -> None:
        """Aplica la retención del repositorio (pliegue y borrado de datos viejos)."""
        if self.repository is not None:
            await self.repository.prune(now=self.clock())

    async def report(
        self,
        *,
        guild_id: int,
        days: int,
        commands: Iterable[str],
        channel_names: Mapping[int, str],
    ) -> dict[str, Any]:
        """Vuelca lo pendiente y devuelve el informe de los últimos `days` días de un servidor."""
        await self.flush()
        now = self.clock()
        today = datetime.fromtimestamp(now, TIMEZONE).date()
        since = today - timedelta(days=max(days, 1) - 1)
        since_ts = datetime.combine(since, dt_time.min, TIMEZONE).timestamp()
        if self.repository is None:
            data = ReportData()
        else:
            data = await self.repository.report_data(
                guild_id=guild_id, since_day=since.isoformat(), since_ts=since_ts
            )
        return build_report(
            data,
            generated_at=now,
            days=days,
            commands=commands,
            channel_names=channel_names,
            guild_id=guild_id,
        )
