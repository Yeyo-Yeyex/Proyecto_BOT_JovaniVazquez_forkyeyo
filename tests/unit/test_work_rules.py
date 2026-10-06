"""Pruebas de las reglas puras del trabajo (`bot.services.work`) y del catálogo."""

from __future__ import annotations

from datetime import datetime

from bot.services.levels import TIMEZONE
from bot.services.work import (
    BATTERY_MAX,
    FAMILY_EXTRA,
    FAMILY_NIGHT,
    FAMILY_REST_DAY,
    FAMILY_SUNDAY,
    LEGAL_EXTRAS_PER_WEEK,
    ORDINARY_SHIFTS,
    PERFORMANCE_MAX,
    PERFORMANCE_START,
    Contract,
    ShiftKind,
    accident_chance,
    apply_performance,
    battery_now,
    family_cost,
    next_shift_kind,
    performance_delta,
    promote,
    promotion_blockers,
    roll_over_day,
    shift_pay,
)
from bot.services.work_catalog import (
    DIALOGUE_PACKS,
    EVENTS,
    JOB_BY_KEY,
    JOBS,
    MEMORY_PACKS,
    events_for,
)
from bot.services.work_games import _SPOT


def at(year: int, month: int, day: int, hour: int = 12) -> float:
    return datetime(year, month, day, hour, tzinfo=TIMEZONE).timestamp()


# -- Catálogo --------------------------------------------------------------------------


def test_cada_oficio_tiene_cinco_puestos_con_sueldo_creciente() -> None:
    assert {job.key for job in JOBS} == {"obra", "hosteleria", "politica", "sanidad", "oficina"}
    for job in JOBS:
        assert [p.level for p in job.positions] == [1, 2, 3, 4, 5]
        pays = [p.base_pay for p in job.positions]
        assert pays == sorted(pays), job.key
        assert not job.positions[-1].tasks  # del último no se asciende
        for position in job.positions[:-1]:
            assert position.tasks and position.days >= 2


def test_los_minijuegos_duran_de_30_a_60_segundos_segun_el_nivel() -> None:
    for job in JOBS:
        assert [p.seconds for p in job.positions[:2]] == [30, 30]
        assert all(p.seconds == 60 for p in job.positions[3:])


def test_el_contenido_de_cada_puesto_existe() -> None:
    for job in JOBS:
        for position in job.positions:
            kind = position.content.partition(":")[0]
            known = kind in MEMORY_PACKS or kind in DIALOGUE_PACKS
            assert known or kind in {"cavar", *_SPOT}, position.content


def test_los_dialogos_tienen_rondas_de_sobra() -> None:
    for pack in DIALOGUE_PACKS.values():
        assert len(pack.items) >= 6
        for item in pack.items:
            assert len(item.bad) in (2, 3)


def test_la_corrupcion_solo_sale_en_politica() -> None:
    corruption = {"sobre", "enchufe", "mordida", "falcon"}
    assert corruption <= {e.key for e in events_for("politica", 5, 100)}
    assert not corruption & {e.key for e in events_for("obra", 5, 100)}


def test_los_eventos_familiares_solo_salen_con_la_familia_tocada() -> None:
    assert "madre" not in {e.key for e in events_for("obra", 1, 100)}
    assert "madre" in {e.key for e in events_for("obra", 1, 10)}
    assert all(len(event.options) == 2 for event in EVENTS)


# -- Batería, jornada y sueldo ------------------------------------------------------------


def test_la_bateria_se_recarga_cinco_puntos_por_hora_hasta_el_maximo() -> None:
    contract = Contract(job="obra", level=1, battery=10, battery_at=0)
    assert battery_now(contract, 3600) == 15
    assert battery_now(contract, 100 * 3600) == BATTERY_MAX


def test_cuatro_ordinarios_dos_extras_legales_y_luego_b() -> None:
    contract = Contract(job="obra", level=1)
    now = at(2026, 10, 6)
    roll_over_day(contract, now)
    kinds = []
    for _ in range(ORDINARY_SHIFTS + LEGAL_EXTRAS_PER_WEEK + 1):
        kind = next_shift_kind(contract, now)
        kinds.append(kind)
        contract.shifts_today += 1
        if kind is not ShiftKind.ORDINARY:
            contract.extras_week += 1
    assert kinds == (
        [ShiftKind.ORDINARY] * ORDINARY_SHIFTS
        + [ShiftKind.EXTRA] * LEGAL_EXTRAS_PER_WEEK
        + [ShiftKind.BLACK]
    )


def test_el_lunes_se_reinician_los_extras_de_la_semana() -> None:
    contract = Contract(job="obra", level=1)
    roll_over_day(contract, at(2026, 10, 9))  # viernes
    contract.extras_week = 2
    roll_over_day(contract, at(2026, 10, 12))  # lunes
    assert contract.extras_week == 0


def test_el_sueldo_va_del_70_al_130_y_las_extras_pagan_un_25_mas() -> None:
    assert shift_pay(1_000, 0, ShiftKind.ORDINARY) == 700
    assert shift_pay(1_000, 50, ShiftKind.ORDINARY) == 1_000
    assert shift_pay(1_000, 100, ShiftKind.ORDINARY) == 1_300
    assert shift_pay(1_000, 50, ShiftKind.EXTRA) == 1_250
    assert shift_pay(1_000, 50, ShiftKind.BLACK) == 1_000


def test_accidentes_solo_si_vas_reventado() -> None:
    assert accident_chance(50) == 0
    assert 0 < accident_chance(10) < accident_chance(-5)


def test_la_familia_sufre_con_extras_madrugada_y_domingo() -> None:
    sunday_night = at(2026, 10, 11, hour=2)
    assert family_cost(ShiftKind.ORDINARY, at(2026, 10, 6)) == 0
    assert family_cost(ShiftKind.EXTRA, sunday_night) == FAMILY_EXTRA + FAMILY_NIGHT + FAMILY_SUNDAY


def test_los_dias_libres_devuelven_familia_y_cortan_la_racha() -> None:
    contract = Contract(job="obra", level=1, family=20, shift_day="2026-10-01", streak_days=5)
    rest = roll_over_day(contract, at(2026, 10, 5))
    assert rest == 3
    assert contract.family == 20 + 3 * FAMILY_REST_DAY
    assert contract.streak_days == 0


# -- Rendimiento y ascensos ------------------------------------------------------------


def test_la_barra_se_mueve_con_la_puntuacion() -> None:
    assert performance_delta(100) == 25
    assert performance_delta(50) == 0
    assert performance_delta(0) == -25


def test_sin_despidos_primero_aviso_y_luego_bajan_un_puesto() -> None:
    contract = Contract(job="obra", level=3, performance=10)
    assert apply_performance(contract, -25) == "warned"
    assert contract.level == 3
    assert apply_performance(contract, -25) == "demoted"
    assert contract.level == 2
    assert contract.performance == PERFORMANCE_START


def test_en_el_primer_puesto_no_hay_a_donde_bajar() -> None:
    contract = Contract(job="obra", level=1, performance=0, warned=True)
    assert apply_performance(contract, -25) == "warned"
    assert contract.level == 1


def test_el_ascenso_pide_barra_dias_tareas_y_formacion() -> None:
    job = JOB_BY_KEY["obra"]
    contract = Contract(job="obra", level=1, performance=PERFORMANCE_MAX)
    contract.progress = {"good": 3, "clean": 2}
    blockers = promotion_blockers(contract, job, days_in_position=2, trainings=set(), today="x")
    assert len(blockers) == 1 and "PRL" in blockers[0]
    assert not promotion_blockers(contract, job, days_in_position=2, trainings={"prl20"}, today="x")
    assert promotion_blockers(contract, job, days_in_position=1, trainings={"prl20"}, today="x")


def test_rechazar_el_ascenso_lo_aplaza_hasta_manana() -> None:
    job = JOB_BY_KEY["obra"]
    contract = Contract(
        job="obra", level=1, performance=100, progress={"good": 3, "clean": 2}, declined_day="hoy"
    )
    assert promotion_blockers(contract, job, days_in_position=5, trainings={"prl20"}, today="hoy")
    assert not promotion_blockers(
        contract, job, days_in_position=5, trainings={"prl20"}, today="mañana"
    )


def test_ascender_sube_un_puesto_y_reinicia_tareas() -> None:
    contract = Contract(job="obra", level=1, performance=100, progress={"good": 9})
    promote(contract, now=123.0)
    assert contract.level == 2
    assert contract.progress == {}
    assert contract.performance == PERFORMANCE_START
    assert contract.position_since == 123.0


def test_la_familia_no_se_recupera_dos_veces_por_los_mismos_dias() -> None:
    """Un café o un ascenso guardan el contrato sin fichar: no debe sumar otra vez."""
    contract = Contract(job="obra", level=1, family=20, shift_day="2026-10-01")
    roll_over_day(contract, at(2026, 10, 5))
    roll_over_day(contract, at(2026, 10, 5, hour=18))
    assert contract.family == 20 + 3 * FAMILY_REST_DAY
    roll_over_day(contract, at(2026, 10, 6))
    assert contract.family == 20 + 4 * FAMILY_REST_DAY
