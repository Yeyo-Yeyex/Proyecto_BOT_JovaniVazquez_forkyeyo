"""Pruebas de las reglas de XP: mensajes, voz, reacciones, rachas y premios."""

from __future__ import annotations

from datetime import date, datetime

from bot.services.levels import (
    FIRST_MESSAGE_OF_DAY_BONUS,
    MAX_REACTION_XP_PER_DAY,
    REACTION_XP,
    TIMEZONE,
    MemberActivity,
    level_reward,
    message_award,
    next_streak,
    reaction_award,
    rewards_between,
    streak_multiplier,
    voice_award,
)


def at(day: int, hour: int = 10) -> float:
    """Epoch de las `hour` (hora canaria) del `day` de octubre de 2026."""
    return datetime(2026, 10, day, hour, tzinfo=TIMEZONE).timestamp()


def test_primer_mensaje_del_dia_da_bonus_y_el_segundo_no() -> None:
    first = message_award(MemberActivity(), now=at(4), cooldown_seconds=60, base_xp=20)
    assert first is not None
    assert first.total_xp == 20 + FIRST_MESSAGE_OF_DAY_BONUS
    assert first.streak_days == 1

    second = message_award(first, now=at(4) + 60, cooldown_seconds=60, base_xp=20)
    assert second is not None
    assert second.total_xp == first.total_xp + 20


def test_mensaje_en_enfriamiento_no_da_nada() -> None:
    state = MemberActivity(total_xp=100, last_awarded_at=at(4), last_active_day="2026-10-04")
    assert message_award(state, now=at(4) + 59, cooldown_seconds=60, base_xp=20) is None


def test_racha_sube_si_escribe_al_dia_siguiente_y_se_reinicia_si_falla() -> None:
    assert next_streak("2026-10-03", date(2026, 10, 4), 4) == 5
    assert next_streak("2026-10-04", date(2026, 10, 4), 4) == 4
    assert next_streak("2026-10-01", date(2026, 10, 4), 4) == 1
    assert next_streak(None, date(2026, 10, 4), 0) == 1


def test_multiplicador_de_racha_con_tope() -> None:
    assert streak_multiplier(1) == 1
    assert streak_multiplier(2) == 1.02
    assert streak_multiplier(11) == streak_multiplier(500) == 1.2


def test_voz_usa_la_racha_vigente_sin_moverla() -> None:
    state = MemberActivity(total_xp=0, last_active_day="2026-10-03", streak_days=6)
    after = voice_award(state, now=at(4), base_xp=5)
    assert after.total_xp == round(5 * streak_multiplier(6))
    assert after.streak_days == 6

    caducada = MemberActivity(last_active_day="2026-09-01", streak_days=6)
    assert voice_award(caducada, now=at(4), base_xp=5).total_xp == 5


def test_reacciones_tienen_tope_diario_y_se_reinician_al_dia_siguiente() -> None:
    state = MemberActivity()
    for _ in range(MAX_REACTION_XP_PER_DAY // REACTION_XP):
        updated = reaction_award(state, now=at(4))
        assert updated is not None
        state = updated
    assert state.reaction_xp == MAX_REACTION_XP_PER_DAY
    assert reaction_award(state, now=at(4)) is None

    tomorrow = reaction_award(state, now=at(5))
    assert tomorrow is not None
    assert tomorrow.reaction_xp == REACTION_XP


def test_premios_por_nivel_doblan_en_multiplos_de_cinco() -> None:
    assert level_reward(1) == 100
    assert level_reward(4) == 400
    assert level_reward(5) == 1_000
    assert level_reward(31) == 3_100
    assert rewards_between(3, 5) == 400 + 1_000
    assert rewards_between(5, 5) == 0
