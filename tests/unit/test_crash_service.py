"""Pruebas de bot.services.crash: punto de explosión, curva, ronda y retorno."""

from __future__ import annotations

import pytest

from bot.services.crash import (
    MAX_CENTS,
    CrashError,
    CrashRound,
    Seat,
    crash_point,
    format_multiplier,
    looks_like_multiplier,
    multiplier_at,
    parse_multiplier,
    payout,
    seconds_to,
)


def test_el_punto_de_explosion_cumple_p_igual_a_099_entre_x() -> None:
    # Con u repartido de forma uniforme y fina, la fracción de rondas que
    # llegan a c debe ser 99 / c (en centésimas): retorno del 99 % para
    # cualquier estrategia de retirada fija.
    steps = 200_000
    points = [crash_point(i / steps) for i in range(steps)]
    for target in (101, 150, 200, 500, 1_000, 10_000):
        reached = sum(1 for p in points if p >= target) / steps
        assert reached == pytest.approx(99 / target, rel=0.01)
        assert target / 100 * reached == pytest.approx(0.99, rel=0.01)


def test_el_1_por_ciento_explota_en_1x_y_hay_tope() -> None:
    assert crash_point(0.0) == 100
    assert crash_point(0.0099) == 100
    assert crash_point(0.02) == 101
    assert crash_point(0.999_999_9) == MAX_CENTS
    with pytest.raises(ValueError):
        crash_point(1.0)


def test_la_curva_duplica_cada_45_segundos() -> None:
    assert multiplier_at(0) == 100
    assert multiplier_at(4.5) in (199, 200)
    assert multiplier_at(15) == pytest.approx(1_008, abs=2)
    assert multiplier_at(10_000) == MAX_CENTS
    for cents in (150, 200, 1_000):
        assert multiplier_at(seconds_to(cents) + 1e-6) == cents


def test_pago_y_formato() -> None:
    assert payout(100, 247) == 247
    assert payout(333, 150) == 499
    assert format_multiplier(247) == "2,47x"
    assert format_multiplier(100_000) == "1.000,00x"


@pytest.mark.parametrize(
    ("text", "cents"), [("2", 200), ("2x", 200), ("1,5", 150), ("1.75x", 175), ("x3", 300)]
)
def test_parse_multiplier_acepta_formatos(text: str, cents: int) -> None:
    assert parse_multiplier(text) == cents


@pytest.mark.parametrize("text", ["1", "1x", "abc", "2000x", "0,5"])
def test_parse_multiplier_rechaza_lo_que_no_vale(text: str) -> None:
    with pytest.raises(ValueError):
        parse_multiplier(text)


def test_solo_parece_multiplicador_si_lleva_x() -> None:
    assert looks_like_multiplier("2x")
    assert looks_like_multiplier("x1,5")
    assert not looks_like_multiplier("500")
    assert not looks_like_multiplier("2k")


def test_retirarse_cobra_y_no_dos_veces() -> None:
    round_ = CrashRound(crash_cents=300)
    round_.sit(Seat(1, "Ana", 100))
    seat = round_.cash_out(1, 250)
    assert seat.payout == 250 and seat.net == 150
    with pytest.raises(CrashError):
        round_.cash_out(1, 260)


def test_retirarse_a_mano_en_el_punto_exacto_es_tarde() -> None:
    round_ = CrashRound(crash_cents=300)
    round_.sit(Seat(1, "Ana", 100))
    with pytest.raises(CrashError):
        round_.cash_out(1, 300)


def test_no_sentado_no_puede_retirarse_ni_sentarse_dos_veces() -> None:
    round_ = CrashRound(crash_cents=300)
    with pytest.raises(CrashError):
        round_.cash_out(9, 150)
    round_.sit(Seat(1, "Ana", 100))
    with pytest.raises(CrashError):
        round_.sit(Seat(1, "Ana", 100))


def test_auto_retiro_cobra_su_objetivo_exacto_y_en_orden() -> None:
    round_ = CrashRound(crash_cents=500)
    round_.sit(Seat(1, "Ana", 100, auto_cents=200))
    round_.sit(Seat(2, "Leo", 100, auto_cents=150))
    round_.sit(Seat(3, "Eva", 100, auto_cents=600))  # no llega
    round_.sit(Seat(4, "Sol", 100))
    assert round_.next_auto() == 150
    due = round_.due_autos(230)
    assert [s.user_id for s in due] == [2, 1]
    assert [s.cashed_cents for s in due] == [150, 200]
    assert all(s.by_auto for s in due)
    assert round_.next_auto() is None
    assert {s.user_id for s in round_.riding} == {3, 4}


def test_auto_retiro_en_el_punto_exacto_cobra() -> None:
    round_ = CrashRound(crash_cents=200)
    round_.sit(Seat(1, "Ana", 100, auto_cents=200))
    assert round_.next_auto() == 200
    assert round_.due_autos(200)[0].payout == 200
