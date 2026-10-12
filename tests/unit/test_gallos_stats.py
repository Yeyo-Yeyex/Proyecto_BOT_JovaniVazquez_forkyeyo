"""Pruebas de `gallos_stats` y `gallos_bet_stats`: lo que cuenta cada pelea para los logros."""

from __future__ import annotations

from datetime import date, datetime

from bot.services.achievements import (
    GALLOS_BEAT_PREFIX,
    GALLOS_SKIN_PREFIX,
    gallos_bet_stats,
    gallos_stats,
)
from bot.services.gallos import Corner, Exchange, Fight, Finish, RoosterRecord
from bot.services.levels import TIMEZONE

RED, BLUE = Corner.RED, Corner.BLUE
WHEN = datetime(2026, 10, 12, 3, 0, tzinfo=TIMEZONE)  # Hispanidad, de madrugada
RECORD = RoosterRecord(name="Pepe", fights=3, wins=3, streak=3, best_streak=3, day_streak=2,
                       last_day=date(2026, 10, 12))  # fmt: skip


def stats(fight: Fight, **kwargs: object) -> dict[str, int]:
    args: dict[str, object] = {
        "fight": fight,
        "corner": RED,
        "rival": "temu",
        "chance": 0.85,
        "stake": 100,
        "net": 12,
        "record": RECORD,
        "skin": "serie",
        "paid_skin": False,
        "crowd_pool": 0,
        "rake": 0,
        "when": WHEN,
    }
    args.update(kwargs)
    delta = gallos_stats(**args)  # type: ignore[arg-type]
    return {**delta.add, **delta.peak}


def test_ko_relampago_sin_un_rasguno_contra_la_casa() -> None:
    fight = Fight(RED, Finish.KO, (
        Exchange(1, BLUE, "picotazo", 0, False, True, (100, 100)),
        Exchange(1, RED, "patada", 64, True, False, (100, 36)),
        Exchange(1, RED, "espolonazo", 36, False, False, (100, 0)),
    ))  # fmt: skip
    got = stats(fight)
    assert got["gallos_fights"] == got["gallos_wins"] == got["gallos_kos"] == 1
    assert got["gallos_flawless"] == got["gallos_blitz"] == 1
    assert got["gallos_crits"] == 1 and got["gallos_dodges"] == 1
    assert got["gallos_damage_max"] == 64
    assert got[f"{GALLOS_BEAT_PREFIX}temu"] == 1
    assert got[f"{GALLOS_SKIN_PREFIX}serie"] == 1
    assert got["gallos_streak_max"] == 3 and got["gallos_days_max"] == 2
    assert got["gallos_hispanidad"] == got["gallos_night"] == 1
    assert got["gallos_win_max"] == 12
    assert "gallos_duels" not in got and "gallos_losses" not in got


def test_perder_un_duelo_huyendo_siendo_favorito() -> None:
    fight = Fight(BLUE, Finish.FLED, (Exchange(2, BLUE, "picotazo", 80, False, False, (20, 100)),))
    got = stats(fight, rival=None, chance=0.85, net=-100, stake=10_000, crowd_pool=5_000, rake=25)
    assert got["gallos_losses"] == got["gallos_fled"] == got["gallos_favourite_loss"] == 1
    assert got["gallos_duels"] == got["gallos_duel_big"] == 1
    assert got["gallos_crowd_max"] == 5_000 and got["gallos_rake"] == 25
    assert "gallos_duel_wins" not in got and "gallos_temu_loss" not in got


def test_perder_contra_temu_es_secreto_y_cuenta_aparte() -> None:
    fight = Fight(BLUE, Finish.POINTS, (Exchange(3, BLUE, "aletazo", 10, False, False, (90, 100)),))
    got = stats(fight, net=-100)
    assert got["gallos_temu_loss"] == 1 and f"{GALLOS_BEAT_PREFIX}temu" not in got


def test_apuesta_de_grada() -> None:
    won = gallos_bet_stats(won=True, refunded=False, odds_cents=320, rake=5, when=WHEN)
    assert won.add == {"gallos_bets": 1, "gallos_bets_won": 1, "gallos_rake": 5, "gallos_night": 1}
    assert won.peak == {"gallos_bet_odds_max": 320}
    refund = gallos_bet_stats(won=True, refunded=True, odds_cents=0, rake=0, when=WHEN)
    assert refund.add["gallos_bet_refund"] == 1 and "gallos_bets_won" not in refund.add
