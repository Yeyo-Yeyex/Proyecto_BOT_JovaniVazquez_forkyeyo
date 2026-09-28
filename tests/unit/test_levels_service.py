"""Pruebas de fórmulas de progresión de niveles."""

from __future__ import annotations

import pytest

from bot.services.levels import calculate_level_progress


@pytest.mark.parametrize(
    ("total_xp", "expected_level", "xp_in_level", "xp_for_next"),
    [
        (-10, 0, 0, 100),
        (0, 0, 0, 100),
        (99, 0, 99, 100),
        (100, 1, 0, 200),
        (299, 1, 199, 200),
        (300, 2, 0, 300),
        (600, 3, 0, 400),
    ],
)
def test_calculate_level_progress_uses_increasing_thresholds(
    total_xp: int,
    expected_level: int,
    xp_in_level: int,
    xp_for_next: int,
) -> None:
    """Cada nivel consume su umbral completo y aumenta el requerido siguiente."""
    progress = calculate_level_progress(total_xp)

    assert progress.level == expected_level
    assert progress.xp_in_level == xp_in_level
    assert progress.xp_for_next_level == xp_for_next
