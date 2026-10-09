"""Pruebas de los logros del revamp de la tragaperras.

Re-giro del tercer rodillo, doble o nada, bote misterioso, giro diario,
celebraciones por nivel, calor que se enfría y ticket de sesión: cada función de
estadísticas produce lo esperado y los logros saltan con esas estadísticas.
"""

from __future__ import annotations

import itertools
from datetime import datetime

from bot.services.achievements import (
    BY_ID,
    CATALOG,
    SLOTS_DOUBLE_MAX,
    StatDelta,
    newly_unlocked,
    slots_cooled_stats,
    slots_double_stats,
    slots_respin_stats,
    slots_stats,
    slots_ticket_stats,
)
from bot.services.levels import TIMEZONE
from bot.services.slots import HEAT_MAX, REEL_STRIPS, Kind, Spin, WinTier, spin_at

NOON = datetime(2026, 1, 1, 12, tzinfo=TIMEZONE)


def _spin(predicate) -> Spin:  # noqa: ANN001
    for stops in itertools.product(*(range(len(s)) for s in REEL_STRIPS)):
        spin = spin_at(stops)
        if predicate(spin):
            return spin
    raise AssertionError("Ninguna parada cumple la condición")


def _base(**kwargs: object) -> StatDelta:
    spin = _spin(lambda s: s.kind == Kind.THREE and s.symbol == "C")
    args: dict[str, object] = {
        "stake": 100,
        "payout": 600,
        "jackpot": 0,
        "free": False,
        "hot": False,
        "turbo": False,
        "session_spins": 3,
        "when": NOON,
    }
    args.update(kwargs)
    return slots_stats(spin, **args)  # type: ignore[arg-type]


def _unlocks(delta: StatDelta) -> set[str]:
    stats = dict(delta.add)
    for stat, value in delta.peak.items():
        stats[stat] = max(stats.get(stat, 0), value)
    return set(newly_unlocked(stats, []))


# -- slots_stats con los kwargs nuevos ------------------------------------------------


def test_sin_los_kwargs_nuevos_el_resultado_es_el_de_antes() -> None:
    plain = _base()
    explicit = _base(tier=None, mystery=False, drought=0, daily=False, daily_streak=0)
    assert plain.add == explicit.add
    assert plain.peak == explicit.peak
    new_stats = {s for s in plain.add if s.startswith(("slots_win_", "slots_daily"))}
    assert new_stats == set()


def test_cada_nivel_de_celebracion_suma_su_contador() -> None:
    for tier in (WinTier.BIG, WinTier.MEGA, WinTier.EPIC):
        delta = _base(tier=tier)
        assert delta.add[f"slots_win_{tier}"] == 1
        others = {f"slots_win_{t}" for t in ("big", "mega", "epic")} - {f"slots_win_{tier}"}
        assert not others & set(delta.add)
    assert "win_big_1" in _unlocks(_base(tier=WinTier.BIG))
    assert "win_mega_1" in _unlocks(_base(tier=WinTier.MEGA))
    assert "win_epic_1" in _unlocks(_base(tier=WinTier.EPIC))
    fanfare = {"slots_win_big": 1, "slots_win_mega": 1, "slots_win_epic": 1}
    assert "slots_fanfare" in newly_unlocked(fanfare, [])
    assert "slots_fanfare" not in newly_unlocked({"slots_win_big": 3, "slots_win_mega": 1}, [])


def test_gran_premio_con_1_yd_solo_si_se_ha_apostado() -> None:
    assert _base(stake=1, payout=6, tier=WinTier.BIG).add["slots_tiny_big"] == 1
    assert "slots_tiny_big" not in _base(stake=1, payout=6, tier=WinTier.BIG, free=True).add
    assert "slots_tiny_big" not in _base(stake=1, payout=6, tier=WinTier.BIG, daily=True).add
    assert "slots_tiny_big" not in _base(stake=1, payout=6).add


def test_bote_misterioso_sequia_y_bote_rapido() -> None:
    delta = _base(jackpot=40_000, mystery=True, drought=12_345)
    assert delta.add["slots_mystery_pots"] == 1
    assert delta.peak["slots_pot_drought"] == 12_345
    assert "slots_pot_quick" not in delta.add
    assert {"mystery_pot_1", "drought_10k"} <= _unlocks(delta)

    quick = _base(jackpot=6_000, drought=80)
    assert quick.add["slots_pot_quick"] == 1
    assert "slots_mystery_pots" not in quick.add
    assert "pot_quick" in _unlocks(quick)

    # Sin bote, ni misterio ni sequía cuentan.
    nothing = _base(mystery=True, drought=50_000)
    assert "slots_mystery_pots" not in nothing.add
    assert "slots_pot_drought" not in nothing.peak


def test_giro_diario_cuenta_dias_racha_y_premio() -> None:
    delta = _base(daily=True, daily_streak=7, tier=WinTier.BIG)
    assert delta.add["slots_daily"] == 1
    assert delta.peak["slots_daily_streak"] == 7
    assert delta.add["slots_daily_big"] == 1
    assert {"daily_1", "daily_streak_7", "daily_big"} <= _unlocks(delta)
    assert "slots_daily_big" not in _base(daily=True, daily_streak=1).add
    assert "slots_daily_streak" not in _base(daily_streak=4).peak


# -- Re-giro -----------------------------------------------------------------------------


def test_re_giro_fallado_en_cadena() -> None:
    delta = slots_respin_stats(price=150, payout=0, jackpot=0, chain=3)
    assert delta.add == {"slots_respins": 1, "slots_respin_spent": 150}
    assert delta.peak == {"slots_respin_price_max": 150, "slots_respin_fail_chain": 3}
    assert {"respin_1", "respin_fail_3"} <= _unlocks(delta)
    assert "respin_fail_4" not in _unlocks(delta)


def test_re_giro_acertado_tras_fallar_y_con_bote() -> None:
    delta = slots_respin_stats(price=2_000, payout=0, jackpot=50_000, chain=3)
    assert delta.add["slots_respin_saved"] == 1
    assert delta.add["slots_respin_bailout"] == 1
    assert delta.add["slots_respin_jackpots"] == 1
    assert "slots_respin_fail_chain" not in delta.peak
    assert {"respin_saved_1", "respin_bailout", "respin_jackpot", "respin_price_1k"} <= _unlocks(
        delta
    )

    first = slots_respin_stats(price=100, payout=1_000, jackpot=0, chain=1)
    assert first.add["slots_respin_saved"] == 1
    assert "slots_respin_bailout" not in first.add
    assert "slots_respin_jackpots" not in first.add


# -- Doble o nada ------------------------------------------------------------------------


def test_doble_ganado_hasta_el_quinto() -> None:
    delta = slots_double_stats(amount=8_000, won=True, chain=SLOTS_DOUBLE_MAX)
    assert delta.add == {"slots_doubles": 1, "slots_double_wins": 1, "slots_double_fives": 1}
    assert delta.peak == {"slots_double_chain": 5, "slots_double_win_max": 8_000}
    assert {"double_1", "double_win_1", "double_chain_3", "double_chain_5"} <= _unlocks(delta)

    second = slots_double_stats(amount=400, won=True, chain=2)
    assert "slots_double_fives" not in second.add


def test_doble_perdido_al_principio_y_en_el_quinto() -> None:
    first = slots_double_stats(amount=500, won=False, chain=0)
    assert first.add == {"slots_doubles": 1, "slots_double_nada": 1}
    assert first.peak == {"slots_double_loss_max": 500}
    assert "double_nada" in _unlocks(first)

    fifth = slots_double_stats(amount=16_000, won=False, chain=4)
    assert fifth.add == {"slots_doubles": 1, "slots_double_heartbreak": 1}
    assert {"double_heartbreak", "double_ouch"} <= _unlocks(fifth)

    middle = slots_double_stats(amount=1_000, won=False, chain=2)
    assert middle.add == {"slots_doubles": 1}


# -- Calor que se enfría -----------------------------------------------------------------


def test_calor_perdido_por_no_jugar() -> None:
    assert not slots_cooled_stats(lost=0)
    delta = slots_cooled_stats(lost=2)
    assert delta.add == {"slots_cooled": 2}
    assert delta.peak == {"slots_cooled_max": 2}
    assert "cooled_1" in _unlocks(delta)
    assert "cooled_hot" not in _unlocks(delta)
    assert "cooled_hot" in _unlocks(slots_cooled_stats(lost=HEAT_MAX))


# -- Ticket de sesión --------------------------------------------------------------------


def test_ticket_con_premios_en_bruto_y_neto_negativo() -> None:
    delta = slots_ticket_stats(spins=200, gross=15_000, staked=20_000, net=-5_000)
    assert delta.add == {"slots_tickets": 1}
    assert delta.peak == {
        "slots_ticket_gross_max": 15_000,
        "slots_ticket_loss_max": 5_000,
        "slots_ticket_creative": 15_000,
    }
    assert {"ticket_1", "ticket_gross_10k", "ticket_creative_10k"} <= _unlocks(delta)


def test_ticket_ganador_y_casos_raros() -> None:
    win = slots_ticket_stats(spins=50, gross=20_000, staked=5_000, net=15_000)
    assert win.peak["slots_ticket_best"] == 15_000
    assert "slots_ticket_creative" not in win.peak
    assert "ticket_best_10k" in _unlocks(win)

    taxed = slots_ticket_stats(spins=50, gross=5_100, staked=5_000, net=-20)
    assert taxed.add["slots_ticket_taxed"] == 1

    zero = slots_ticket_stats(spins=10, gross=1_000, staked=1_000, net=0)
    assert zero.add["slots_ticket_zero"] == 1

    dry = slots_ticket_stats(spins=12, gross=0, staked=1_200, net=-1_200)
    assert dry.add["slots_ticket_dry"] == 1
    assert "slots_ticket_creative" not in dry.peak

    quick = slots_ticket_stats(spins=1, gross=0, staked=100, net=-100)
    assert quick.add["slots_ticket_quick"] == 1
    assert "slots_ticket_dry" not in quick.add

    assert not slots_ticket_stats(spins=0, gross=0, staked=0, net=0)


def test_la_feria_entera_pide_todas_las_novedades() -> None:
    stats = {
        "slots_respins": 1,
        "slots_doubles": 1,
        "slots_daily": 1,
        "slots_cooled": 1,
        "slots_tickets": 1,
    }
    assert "slots_feria" in newly_unlocked(stats, [])
    assert "slots_feria" not in newly_unlocked({**stats, "slots_daily": 0}, [])


# -- Catálogo ----------------------------------------------------------------------------


def test_los_casi_premios_ya_no_son_regalados() -> None:
    # Con un casi-premio en un 20-25 % de las tiradas, las metas se multiplican.
    assert BY_ID["nearmiss_25"].goal == 1_000
    assert BY_ID["nearmiss_100"].goal == 5_000
    assert BY_ID["nearmiss_500"].goal == 25_000
    assert BY_ID["antic_100"].goal == 1_000


def test_hay_muchos_logros_nuevos_con_secretos() -> None:
    new_stats = {
        "slots_respins", "slots_respin_saved", "slots_respin_fail_chain", "slots_doubles",
        "slots_double_wins", "slots_daily", "slots_win_big", "slots_tickets", "slots_cooled",
        "slots_mystery_pots", "slots_ticket_dry", "slots_double_heartbreak",
    }  # fmt: skip
    new = [a for a in CATALOG if a.category == "slots" and a.stat in new_stats]
    assert len(new) >= 30
    assert any(a.secret for a in new)
