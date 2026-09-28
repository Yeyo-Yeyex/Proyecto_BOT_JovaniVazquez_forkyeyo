"""Reglas de experiencia y progresión del sistema de niveles."""

from __future__ import annotations

from dataclasses import dataclass

XP_PER_LEVEL_BASE = 100
HISTORICAL_XP_PER_MESSAGE = 20
MIN_MESSAGE_XP = 15
MAX_MESSAGE_XP = 25
DEFAULT_XP_COOLDOWN_SECONDS = 60
MIN_XP_COOLDOWN_SECONDS = 10
MAX_XP_COOLDOWN_SECONDS = 3600


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
