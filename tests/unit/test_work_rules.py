"""Pruebas de las reglas puras del trabajo (`bot.services.work`) y del catálogo."""

from __future__ import annotations

import random
from collections import Counter
from datetime import date, datetime

import pytest

from bot.services.levels import TIMEZONE
from bot.services.taxes import compute_pension_payslip, compute_self_employed_payslip
from bot.services.work import (
    BATTERY_MAX,
    BATTERY_MIN,
    FAMILY_EXTRA,
    FAMILY_NIGHT,
    FAMILY_ORDINARY,
    FAMILY_REGEN_PER_HOUR,
    FAMILY_SUNDAY,
    FAMILY_TIERS,
    LEGAL_EXTRAS_PER_WEEK,
    MAX_TRIENNIA,
    MEGAVIRAL,
    ORDINARY_SHIFTS,
    PERFORMANCE_MAX,
    PERFORMANCE_START,
    RATING_BLOCK,
    RATING_MAX,
    RATING_MIN,
    TRIENNIUM_DAYS,
    WISH_BY_KEY,
    Contract,
    ShiftKind,
    WishFacts,
    accident_chance,
    apply_performance,
    battery_eta,
    battery_now,
    change_family,
    family_breakdown,
    family_cost,
    family_now,
    family_ready_at,
    family_tier,
    follower_gain,
    next_shift_kind,
    performance_delta,
    promote,
    promotion_blockers,
    rating_delta,
    rating_pay,
    rested_at,
    roll_over_day,
    settle_branch,
    shift_cost,
    shift_pay,
    stars,
    triennia,
    viral_roll,
    weather,
    wish_for,
    wish_streak,
)
from bot.services.work_catalog import (
    DIALOGUE_PACKS,
    EVENTS,
    JOB_BY_KEY,
    JOBS,
    MEMORY_PACKS,
    SORT_PACKS,
    events_for,
)
from bot.services.work_games import _SPOT
from bot.services.work_tools import NO_PERKS, with_rewards


def at(year: int, month: int, day: int, hour: int = 12) -> float:
    return datetime(year, month, day, hour, tzinfo=TIMEZONE).timestamp()


# -- Catálogo --------------------------------------------------------------------------


def test_cada_oficio_tiene_puestos_correlativos_con_sueldo_creciente() -> None:
    assert {job.key for job in JOBS} == {
        "obra",
        "hosteleria",
        "politica",
        "sanidad",
        "oficina",
        "reparto",
        "funcionariado",
        "influencer",
    }
    for job in JOBS:
        for branch, ladder in job.ladders:
            assert [p.level for p in ladder] == list(range(1, job.top + 1)), (job.key, branch)
            pays = [p.base_pay for p in ladder]
            assert pays == sorted(pays), (job.key, branch)
            assert not ladder[-1].tasks  # del último no se asciende
            for position in ladder[:-1]:
                assert position.tasks and position.days >= 2


def test_las_ramas_salen_del_final_del_tronco_sin_formacion_y_con_pension() -> None:
    for job in JOBS:
        if not job.branches:
            continue
        assert len(job.branches) >= 2, job.key
        assert len({len(b.positions) for b in job.branches}) == 1, job.key  # mismo techo
        assert len({b.key for b in job.branches}) == len(job.branches)
        for branch in job.branches:
            # Al elegir rama no se puede comprar antes su formación: la entrada es gratis.
            assert branch.positions[0].training is None, (job.key, branch.key)
            assert branch.positions[0].level == job.trunk_top + 1
            assert branch.pension > 0 and branch.retired_as
    keys = [p.training.key for j in JOBS for p in j.all_positions if p.training]
    assert len(keys) == len(set(keys))  # una formación no sirve para dos puestos


def test_los_minijuegos_duran_de_30_a_60_segundos_segun_el_nivel() -> None:
    for job in JOBS:
        assert [p.seconds for p in job.positions[:2]] == [30, 30]
        assert all(p.seconds == 60 for p in job.all_positions[3:])


def test_el_contenido_de_cada_puesto_existe() -> None:
    for job in JOBS:
        for position in job.all_positions:
            kind = position.content.partition(":")[0]
            known = kind in MEMORY_PACKS or kind in DIALOGUE_PACKS or kind in SORT_PACKS
            assert known or kind in {"cavar", "pedidos", "metricas", *_SPOT}, position.content


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
    # Familia tranquila (y que no sube de tramo en una hora): recarga normal.
    contract = Contract(job="obra", level=1, battery=10, battery_at=0, family=50, family_at=0)
    assert battery_now(contract, 3600) == 15
    assert battery_now(contract, 100 * 3600) == BATTERY_MAX


def test_la_recarga_depende_del_tramo_de_la_familia() -> None:
    def after_one_hour(family: float) -> float:
        contract = Contract(
            job="obra", level=1, battery=0, battery_at=0, family=family, family_at=0
        )
        return battery_now(contract, 3600)

    assert after_one_hour(100) == 7.5
    assert after_one_hour(50) == 5
    assert after_one_hour(20) == 2.5
    assert after_one_hour(0) == 1.25


def test_la_recarga_cambia_de_ritmo_cuando_la_familia_sube_de_tramo() -> None:
    # De 0 a 15 (en crisis) tarda 7,5 h a ×0,25; luego ×0,5.
    contract = Contract(job="obra", level=1, battery=0, battery_at=0, family=0, family_at=0)
    crossing = 15 / FAMILY_REGEN_PER_HOUR
    expected = crossing * 5 * 0.25 + (10 - crossing) * 5 * 0.5
    assert battery_now(contract, 10 * 3600) == pytest.approx(expected)


def test_battery_eta_cuadra_con_la_recarga() -> None:
    for family in (0, 30, 60, 100):
        contract = Contract(
            job="obra", level=1, battery=40, battery_at=0, family=family, family_at=0
        )
        full = battery_eta(contract, BATTERY_MAX, 0)
        assert battery_now(contract, full) == pytest.approx(BATTERY_MAX)
        assert battery_now(contract, full - 60) < BATTERY_MAX
    full_contract = Contract(job="obra", level=1, battery=BATTERY_MAX, battery_at=0)
    assert battery_eta(full_contract, BATTERY_MAX, 123) == 123


def test_rested_at_es_cuando_se_puede_volver_a_fichar() -> None:
    contract = Contract(
        job="obra", level=1, battery=BATTERY_MIN - 10, battery_at=0, family=0, family_at=0
    )
    back = rested_at(contract, 0)
    assert battery_now(contract, back) > BATTERY_MIN
    assert battery_now(contract, back - 120) <= BATTERY_MIN


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


def test_cada_turno_cuesta_familia_y_mas_las_extras_la_madrugada_y_el_domingo() -> None:
    sunday_night = at(2026, 10, 11, hour=2)
    assert family_cost(ShiftKind.ORDINARY, at(2026, 10, 6)) == FAMILY_ORDINARY
    assert family_cost(ShiftKind.EXTRA, sunday_night) == FAMILY_EXTRA + FAMILY_NIGHT + FAMILY_SUNDAY
    assert family_breakdown(ShiftKind.EXTRA, sunday_night) == [
        ("extra", FAMILY_EXTRA),
        ("madrugada", FAMILY_NIGHT),
        ("domingo", FAMILY_SUNDAY),
    ]
    assert family_cost(ShiftKind.ORDINARY, at(2026, 10, 6), remote=True) > FAMILY_ORDINARY


def test_la_familia_se_recupera_sola_por_horas_hasta_el_maximo() -> None:
    contract = Contract(job="obra", level=1, family=20, family_at=0)
    assert family_now(contract, 5 * 3600) == 20 + 5 * FAMILY_REGEN_PER_HOUR
    assert family_now(contract, 1_000 * 3600) == 100


def test_los_dias_libres_cortan_la_racha_pero_no_suman_familia_de_golpe() -> None:
    contract = Contract(job="obra", level=1, family=20, shift_day="2026-10-01", streak_days=5)
    rest = roll_over_day(contract, at(2026, 10, 5))
    assert rest == 3
    assert contract.family == 20
    assert contract.streak_days == 0


def test_los_tramos_de_la_familia_van_de_mejor_a_peor() -> None:
    assert [t.low for t in FAMILY_TIERS] == sorted((t.low for t in FAMILY_TIERS), reverse=True)
    assert FAMILY_TIERS[-1].low == 0
    assert family_tier(100).name == "En casa feliz"
    assert family_tier(74.9).name == "Tranquila"
    assert family_tier(0).name == "En crisis"
    happy, calm, angry, crisis = FAMILY_TIERS
    assert happy.performance(12) == 15 and calm.performance(12) == 12
    assert angry.performance(12) == 6 and crisis.performance(12) == 0
    # Lo que baja la barra no cambia con la familia.
    assert all(t.performance(-10) == -10 for t in FAMILY_TIERS)


def test_a_cero_llega_la_intervencion_para_el_dia_siguiente() -> None:
    now = at(2026, 10, 6, 12)
    contract = Contract(job="obra", level=1, family=5, family_at=now, battery_at=now)
    assert change_family(contract, -20, now) is True
    assert contract.family == 0 and contract.no_extras_day == "2026-10-07"
    # Ya estaba a cero: no vuelve a saltar.
    assert change_family(contract, -5, now) is False


def test_la_espera_de_regalos_y_favores_va_por_clave() -> None:
    contract = Contract(job="obra", level=1, family_used={"tupper": 1_000.0})
    assert family_ready_at(contract, "tupper", 3600) == 4_600
    assert family_ready_at(contract, "flores_mama", 3600) == 0


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


# -- Oficios nuevos: el tiempo, la app, la plaza fija, los trienios y la viralidad ------------


def test_el_tiempo_es_el_mismo_toda_la_hora_y_casi_siempre_hace_sol() -> None:
    start = at(2026, 10, 6, 0)
    assert weather(start) == weather(start + 3599)
    seen = Counter(weather(start + hour * 3600) for hour in range(24 * 365))
    assert set(seen) == {"sol", "lluvia", "calima"}
    assert seen["sol"] > seen["lluvia"] > seen["calima"]


def test_la_valoracion_multiplica_el_sueldo_entre_085_y_115() -> None:
    assert rating_pay(RATING_MAX) == pytest.approx(1.15)
    assert rating_pay(RATING_BLOCK) == pytest.approx(0.85)
    assert rating_pay(RATING_MIN) == pytest.approx(0.85)
    assert rating_delta(100) > 0 > rating_delta(0)
    assert stars(45) == "⭐ 4,5"


def test_los_trienios_tienen_tope() -> None:
    contract = Contract(job="funcionariado", level=2, job_since=at(2026, 1, 1))
    assert triennia(contract, at(2026, 1, 1) + (TRIENNIUM_DAYS - 1) * 86_400) == 0
    assert triennia(contract, at(2026, 1, 1) + TRIENNIUM_DAYS * 86_400) == 1
    assert triennia(contract, at(2030, 1, 1)) == MAX_TRIENNIA
    assert triennia(Contract(job="obra", level=1), at(2030, 1, 1)) == 0


def test_con_plaza_fija_la_nota_mueve_poco_el_sueldo() -> None:
    assert shift_pay(1_000, 0, ShiftKind.ORDINARY, tenured=True) == 900
    assert shift_pay(1_000, 100, ShiftKind.ORDINARY, tenured=True) == 1_100
    assert shift_pay(1_000, 0, ShiftKind.ORDINARY) == 700


def test_con_plaza_fija_solo_hay_aviso() -> None:
    contract = Contract(job="funcionariado", level=4, performance=10, warned=True)
    assert apply_performance(contract, -25, tenured=True) == "warned"
    assert contract.level == 4
    assert apply_performance(contract, -25) == "demoted"


def test_la_viralidad_paga_de_media_como_un_sueldo_normal() -> None:
    rng = random.Random(3)
    rolls = [viral_roll(rng) for _ in range(100_000)]
    assert 0.95 < sum(rolls) / len(rolls) < 1.15
    assert min(rolls) >= 0.3 and max(rolls) == MEGAVIRAL
    assert follower_gain(40, 5, 10.0) == 0
    assert follower_gain(100, 2, 1.0) == 240


def test_estudiar_cansa_como_un_turno_y_no_resta_familia_como_un_extra() -> None:
    assert shift_cost(ShiftKind.STUDY) == shift_cost(ShiftKind.ORDINARY)
    assert family_cost(ShiftKind.STUDY, at(2026, 10, 6, 12)) == FAMILY_ORDINARY


# -- Carrera larga ----------------------------------------------------------------------------


def facts(**changes: object) -> WishFacts:
    """Un turno normal (nota 70 a mediodía), con lo que se cambie."""
    base = {
        "score": 70,
        "kind": ShiftKind.ORDINARY,
        "battery_before": 80.0,
        "hour": 12,
        "weather": "",
        "time_left": 0.0,
        "seconds": 60.0,
        "completed": False,
        "shifts_today": 1,
        "remote": False,
        "viral": 0.0,
    }
    base.update(changes)
    return WishFacts(**base)  # type: ignore[arg-type]


def test_el_deseo_del_dia_es_el_mismo_todo_el_dia_y_cambia_de_un_dia_a_otro() -> None:
    obra = JOB_BY_KEY["obra"]
    first = wish_for(10, "2026-10-12", obra, 1)
    assert all(wish_for(10, "2026-10-12", obra, 1) is first for _ in range(5))
    month = {wish_for(10, f"2026-10-{day:02d}", obra, 1).key for day in range(1, 31)}
    assert len(month) > 3


def test_el_deseo_del_dia_solo_pide_lo_que_se_puede_hacer_en_el_puesto() -> None:
    def keys(job: str, level: int) -> set[str]:
        return {wish_for(user, "2026-10-12", JOB_BY_KEY[job], level).key for user in range(400)}

    assert not keys("obra", 1) & {"lluvia", "guardia", "teletrabajo", "algoritmo"}
    assert "lluvia" in keys("reparto", 1)
    assert "guardia" not in keys("sanidad", 1)  # el celador no hace guardias
    assert "guardia" in keys("sanidad", 2)
    assert "algoritmo" in keys("influencer", 1)


def test_cada_deseo_se_cumple_con_su_turno_y_no_con_uno_normal() -> None:
    met = {
        "nota_90": facts(score=90),
        "perfecto": facts(score=100),
        "rapido": facts(completed=True, time_left=30.0),
        "madrugador": facts(hour=9),
        "nocturno": facts(hour=21),
        "reventado": facts(score=80, battery_before=29.0),
        "tres": facts(shifts_today=3),
        "extra": facts(kind=ShiftKind.EXTRA),
        "lluvia": facts(weather="lluvia"),
        "guardia": facts(kind=ShiftKind.GUARD),
        "teletrabajo": facts(remote=True, score=80),
        "algoritmo": facts(viral=1.2),
    }
    assert set(met) == set(WISH_BY_KEY)
    for key, shift in met.items():
        assert WISH_BY_KEY[key].met(shift), key
        assert not WISH_BY_KEY[key].met(facts()), key
    # Un turno extra en B no vale para el deseo del turno extra.
    assert not WISH_BY_KEY["extra"].met(facts(kind=ShiftKind.BLACK))


def test_la_racha_de_deseos_sigue_si_fue_ayer_y_si_no_vuelve_a_uno() -> None:
    contract = Contract(job="obra", level=1, wish_day="2026-10-11", wish_streak=4)
    assert wish_streak(contract, date(2026, 10, 12)) == 5
    assert wish_streak(contract, date(2026, 10, 13)) == 1


def test_la_carrera_por_ramas() -> None:
    politica = JOB_BY_KEY["politica"]
    assert (politica.trunk_top, politica.top) == (5, 10)
    assert politica.forks(5) and not politica.forks(5, "moncloa") and not politica.forks(4)
    assert politica.next_position(5) is None
    assert politica.next_position(6, "bruselas") is politica.position(7, "bruselas")
    assert politica.position(10, "moncloa").title == "Consejero de una eléctrica"
    assert politica.position(10, "bruselas").title == "Comisario europeo"
    with pytest.raises(ValueError):
        politica.position(6)
    assert len(politica.all_positions) == 15
    # Un oficio sin ramas sigue igual.
    obra = JOB_BY_KEY["obra"]
    assert obra.top == obra.trunk_top == 5 and not obra.forks(5)
    assert obra.ladders == (("", obra.positions),)


def test_las_ventajas_son_las_de_los_puestos_por_los_que_has_pasado() -> None:
    politica = JOB_BY_KEY["politica"]
    assert politica.perks(1) == ()
    assert len(politica.perks(5)) == 4
    moncloa = politica.perks(7, "moncloa")
    assert len(moncloa) == 6 and moncloa[-1] is politica.position(7, "moncloa").perk
    perks = with_rewards(NO_PERKS, moncloa)
    assert perks.retries == 1 and perks.fifty
    assert perks.extra_time == pytest.approx(0.10)
    assert perks.battery == pytest.approx(0.15)
    assert "🏅" in perks.summary
    assert with_rewards(NO_PERKS, ()) is NO_PERKS


def test_al_final_del_tronco_no_se_pide_formacion_y_sin_ramas_libres_no_se_asciende() -> None:
    politica = JOB_BY_KEY["politica"]
    contract = Contract(job="politica", level=5, performance=PERFORMANCE_MAX)
    contract.progress = {"smooth": 99, "good": 99, "events": 99}
    kwargs = {"days_in_position": 99, "trainings": set(), "today": "2026-10-12"}
    assert promotion_blockers(contract, politica, **kwargs) == []  # type: ignore[arg-type]
    blocked = promotion_blockers(
        contract,
        politica,
        retired={"moncloa", "bruselas"},
        **kwargs,  # type: ignore[arg-type]
    )
    assert blocked and "jubilaste" in blocked[0]


def test_al_bajar_al_tronco_se_olvida_la_rama() -> None:
    politica = JOB_BY_KEY["politica"]
    contract = Contract(job="politica", level=5, branch="moncloa")
    settle_branch(contract, politica)
    assert contract.branch == ""
    contract = Contract(job="politica", level=8, branch="moncloa")
    settle_branch(contract, politica)
    assert (contract.level, contract.branch) == (8, "moncloa")


def test_los_eventos_de_cada_rama_solo_salen_en_ella() -> None:
    moncloa = {e.key for e in events_for("politica", 6, 100, branch="moncloa")}
    bruselas = {e.key for e in events_for("politica", 6, 100, branch="bruselas")}
    trunk = {e.key for e in events_for("politica", 5, 100)}
    assert {"mocion", "socio", "cis", "omnibus"} <= moncloa
    assert {"qatargate", "estrasburgo", "dietas", "cena_lobby"} <= bruselas
    assert not moncloa & {"qatargate", "estrasburgo", "dietas", "cena_lobby"}
    assert not trunk & (moncloa ^ bruselas)
    assert "sobre" in moncloa and "sobre" in bruselas  # los de toda la política siguen


def test_la_pension_paga_irpf_sin_cotizar_y_menos_que_un_autonomo() -> None:
    gross, recent = 5_000, 5_000 * 6
    pension = compute_pension_payslip(gross, recent)
    assert pension.ss_worker == pension.ss_employer == 0
    assert 0 < pension.irpf < gross
    # Con los gastos y la reducción de los rendimientos del trabajo, retiene menos
    # que la misma cifra cobrada como autónomo.
    assert pension.irpf <= compute_self_employed_payslip(gross, recent).irpf
    with pytest.raises(ValueError):
        compute_pension_payslip(0, 0)
