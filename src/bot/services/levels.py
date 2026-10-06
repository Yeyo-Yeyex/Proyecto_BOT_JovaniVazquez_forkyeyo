"""Reglas de experiencia y progresión del sistema de niveles.

Fuentes de XP (todas pasan por las reglas de este módulo; el cog solo las
dispara y el repositorio solo las guarda de forma atómica):

- **Mensajes:** 15–25 XP, como mucho uno por minuto y persona.
- **Primer mensaje del día:** +50 XP.
- **Voz:** 4–6 XP por minuto sin mute ni ensordecido, fuera del canal AFK y
  con al menos otra persona sin mutear en el canal.
- **Reacciones recibidas:** +5 XP cuando otra persona reacciona a tu
  mensaje, con un tope de 100 XP al día.
- **Racha:** cada día seguido escribiendo suma un 2 % al XP de mensajes y voz,
  hasta un +20 %.

Los "días" son días naturales en hora canaria (`TIMEZONE`). Subir de nivel
paga yapdollars brutos (`level_reward`); el IRPF lo aplica la economía.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

XP_PER_LEVEL_BASE = 100
HISTORICAL_XP_PER_MESSAGE = 20
MIN_MESSAGE_XP = 15
MAX_MESSAGE_XP = 25
DEFAULT_XP_COOLDOWN_SECONDS = 60
MIN_XP_COOLDOWN_SECONDS = 10
MAX_XP_COOLDOWN_SECONDS = 3600

TIMEZONE = ZoneInfo("Atlantic/Canary")

FIRST_MESSAGE_OF_DAY_BONUS = 50
MIN_VOICE_XP = 4
MAX_VOICE_XP = 6
REACTION_XP = 5
MAX_REACTION_XP_PER_DAY = 100
STREAK_BONUS_PER_DAY = 0.02
MAX_STREAK_BONUS = 0.20

#: Premio bruto por nivel alcanzado: `LEVEL_REWARD_PER_LEVEL × nivel`, el doble
#: en los múltiplos de `LEVEL_MILESTONE`.
LEVEL_REWARD_PER_LEVEL = 100
LEVEL_MILESTONE = 5


@dataclass(frozen=True, slots=True)
class LevelProgress:
    """Progreso derivado de la experiencia total de un miembro."""

    level: int
    xp_in_level: int
    xp_for_next_level: int


def calculate_level_progress(total_xp: int) -> LevelProgress:
    """Convierte experiencia total en nivel y progreso hacia el siguiente nivel.

    El nivel 1 requiere 100 XP; cada nivel posterior requiere 100 XP más
    que el anterior. El nivel se deriva siempre del XP persistido para que
    los datos no se desincronicen.

    Args:
        total_xp: Experiencia total no negativa.

    Returns:
        Nivel actual, XP acumulado en ese nivel y XP necesarios para subir.
    """
    remaining_xp = max(0, total_xp)
    level = 0
    required_xp = XP_PER_LEVEL_BASE

    while remaining_xp >= required_xp:
        remaining_xp -= required_xp
        level += 1
        required_xp = XP_PER_LEVEL_BASE * (level + 1)

    return LevelProgress(
        level=level,
        xp_in_level=remaining_xp,
        xp_for_next_level=required_xp,
    )


# -- Días y rachas ------------------------------------------------------------------


def local_day(now: float) -> date:
    """Día natural en hora canaria del instante `now` (epoch)."""
    return datetime.fromtimestamp(now, TIMEZONE).date()


def streak_multiplier(streak_days: int) -> float:
    """Multiplicador por racha: día 1 = ×1, cada día extra +2 %, tope +20 %."""
    return 1 + min(max(0, streak_days - 1) * STREAK_BONUS_PER_DAY, MAX_STREAK_BONUS)


def next_streak(last_active_day: str | None, today: date, streak_days: int) -> int:
    """Racha tras escribir hoy, sabiendo el último día con mensajes."""
    if last_active_day == today.isoformat():
        return max(1, streak_days)
    if last_active_day == (today - timedelta(days=1)).isoformat():
        return streak_days + 1
    return 1


# -- Estado por miembro y decisiones ----------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberActivity:
    """Estado persistido de un miembro que usan las reglas de XP.

    Attributes:
        total_xp: Experiencia total.
        last_awarded_at: Último mensaje que dio XP (epoch), para el enfriamiento.
        last_active_day: Último día (ISO, hora canaria) con mensajes que dieron XP.
        streak_days: Días seguidos con mensajes hasta `last_active_day`.
        reaction_day: Día (ISO) al que corresponde `reaction_xp`.
        reaction_xp: XP ganado por reacciones ese día.
    """

    total_xp: int = 0
    last_awarded_at: float | None = None
    last_active_day: str | None = None
    streak_days: int = 0
    reaction_day: str | None = None
    reaction_xp: int = 0


def boosted(base_xp: int, *, streak_days: int) -> int:
    """Aplica la racha a un XP base."""
    return round(base_xp * streak_multiplier(streak_days))


def message_award(
    state: MemberActivity,
    *,
    now: float,
    cooldown_seconds: int,
    base_xp: int,
) -> MemberActivity | None:
    """Nuevo estado tras un mensaje, o `None` si sigue en enfriamiento."""
    if state.last_awarded_at is not None and now - state.last_awarded_at < cooldown_seconds:
        return None
    today = local_day(now)
    first_today = state.last_active_day != today.isoformat()
    streak = next_streak(state.last_active_day, today, state.streak_days)
    xp = boosted(base_xp, streak_days=streak)
    if first_today:
        xp += FIRST_MESSAGE_OF_DAY_BONUS
    return replace(
        state,
        total_xp=state.total_xp + xp,
        last_awarded_at=now,
        last_active_day=today.isoformat(),
        streak_days=streak,
    )


def current_streak(state: MemberActivity, today: date) -> int:
    """Racha vigente hoy: se mantiene si escribió hoy o ayer; si no, es 0."""
    if state.last_active_day in {today.isoformat(), (today - timedelta(days=1)).isoformat()}:
        return state.streak_days
    return 0


def voice_award(state: MemberActivity, *, now: float, base_xp: int) -> MemberActivity:
    """Nuevo estado tras un minuto en voz. La voz no mueve la racha, solo la usa."""
    streak = current_streak(state, local_day(now))
    xp = boosted(base_xp, streak_days=streak)
    return replace(state, total_xp=state.total_xp + xp)


def reaction_award(state: MemberActivity, *, now: float) -> MemberActivity | None:
    """Nuevo estado tras recibir una reacción, o `None` si ya llegó al tope diario."""
    today = local_day(now).isoformat()
    earned_today = state.reaction_xp if state.reaction_day == today else 0
    xp = min(REACTION_XP, MAX_REACTION_XP_PER_DAY - earned_today)
    if xp <= 0:
        return None
    return replace(
        state,
        total_xp=state.total_xp + xp,
        reaction_day=today,
        reaction_xp=earned_today + xp,
    )


# -- Premios por nivel ----------------------------------------------------------------


def level_reward(level: int) -> int:
    """Yapdollars brutos por alcanzar `level`."""
    if level <= 0:
        return 0
    reward = LEVEL_REWARD_PER_LEVEL * level
    return reward * 2 if level % LEVEL_MILESTONE == 0 else reward


def rewards_between(previous_level: int, new_level: int) -> int:
    """Suma de premios de los niveles alcanzados al pasar de uno a otro."""
    return sum(level_reward(level) for level in range(previous_level + 1, new_level + 1))
