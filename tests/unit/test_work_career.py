"""Carrera de la 8.0.0: curva de sueldos, días, experiencia, veteranía y pensión.

Las invariantes recorren todos los oficios del catálogo y uno inventado solo con su P3 y la
clase de sus ramas: un oficio nuevo que las incumpla rompe la CI (receta «Añadir un oficio»
de CLAUDE.md).
"""

from __future__ import annotations

import pytest

from bot.services.economy import DAILY_BASE, DAILY_MAX_BONUS
from bot.services.taxes import SS_WORKER_RATE, Pace, WeekIncome, compute_payslip, week_tax
from bot.services.work import (
    BRANCH_DAYS,
    BRANCH_GROWTH,
    LEGACY_DAYS,
    PENSION_CAP,
    TRUNK_STEPS,
    VETERAN_MAX,
    Branch,
    BranchKind,
    Contract,
    Job,
    Mechanic,
    PayCurve,
    Position,
    Task,
    Training,
    catch_up,
    experience_factor,
    pension_amount,
    promote,
    promotion_blockers,
    required_days,
    required_tasks,
    veteran_bonus,
)
from bot.services.work_catalog import JOB_BY_KEY, JOBS

#: Nota media de un jugador normal y lo que multiplica la base (`work.shift_pay`).
NOTA_FACTOR = 0.7 + 0.6 * 75 / 100
#: Neto mínimo por ascenso, por etapa (con `W` y la cuota de la semana a jornada completa).
TRUNK_MIN_STEP = 0.35
CURVE_TRUNK_MAX_STEP = 0.55
BRANCH_MIN_STEP = {BranchKind.SAFE: 0.15, BranchKind.RISKY: 0.23}
#: Banda de la base de lo más alto de una rama, por clase.
TOP_BAND = {BranchKind.SAFE: (25_000, 40_000), BranchKind.RISKY: (38_000, 60_000)}
#: Días que piden los puestos de una carrera entera (tronco y rama), sin experiencia.
CAREER_DAYS = (75, 100)


def week_net(position: Position) -> int:
    """Neto de una semana a jornada completa (28 turnos) en el puesto, solo con el sueldo."""
    gross = round(position.base_pay * NOTA_FACTOR) * 28
    if position.self_employed:
        return gross - position.fee - week_tax(WeekIncome(business=gross - position.fee)).quota
    social = round(gross * SS_WORKER_RATE)
    return gross - social - week_tax(WeekIncome(work=gross, social=social)).quota


def career_problems(job: Job) -> list[str]:
    """Lo que incumple un oficio de las reglas de la curva (vacío = cumple)."""
    problems = []
    p1 = job.positions[0]
    p1_net = p1.base_pay if p1.self_employed else compute_payslip(p1.base_pay, Pace()).net
    if p1_net < (DAILY_BASE + DAILY_MAX_BONUS) * 1.05:
        problems.append(f"{job.key}: P1 deja {p1_net} por turno, menos que el IMV × 1,05")
    for branch_key, ladder in job.ladders:
        branch = job.branch(branch_key)
        nets = [week_net(p) for p in ladder]
        for before, position, net, prev in zip(ladder, ladder[1:], nets[1:], nets, strict=False):
            step = net / prev - 1
            where = f"{job.key}/{branch_key or '-'} P{position.level}"
            if position.level <= job.trunk_top:
                if step < TRUNK_MIN_STEP:
                    problems.append(f"{where}: el neto sube {step:.0%} (tronco ≥ 35 %)")
                if position.level > 3 and step > CURVE_TRUNK_MAX_STEP:
                    problems.append(f"{where}: el neto sube {step:.0%} (P4-P5 ≤ 55 %)")
            elif branch is not None and step < BRANCH_MIN_STEP[branch.kind]:
                problems.append(f"{where}: el neto sube {step:.0%} ({branch.kind})")
            if position.training is not None:
                turns = position.training.price / position.base_pay
                if not 2 <= turns <= 4:
                    problems.append(f"{where}: la formación cuesta {turns:.1f} turnos (2-4)")
            assert before.level + 1 == position.level
        if branch is not None:
            low, high = TOP_BAND[branch.kind]
            if not low <= ladder[-1].base_pay <= high:
                problems.append(f"{job.key}/{branch_key}: P10 de {ladder[-1].base_pay}")
        days = sum(required_days(p, job) for p in ladder)
        if not CAREER_DAYS[0] <= days <= CAREER_DAYS[1]:
            problems.append(f"{job.key}/{branch_key}: la carrera pide {days} días")
        for position in ladder:
            for task in position.tasks:
                if str(task.goal) not in task.text:
                    problems.append(f"{job.key} P{position.level}: «{task.text}» sin su número")
    return problems


def invented_job() -> Job:
    """Un oficio nuevo solo con su P3, la clase de sus ramas y la tabla de días."""
    curve = PayCurve(4_600)

    def position(level: int, pay: int, kind: str = "", training: bool = False) -> Position:
        tasks = (Task("good", "Haz 5 turnos de 90 o más", "good", 5),) if level < 10 else ()
        days = BRANCH_DAYS.get(level, LEGACY_DAYS.get(level, 0)) if level < 10 else 0
        course = (
            Training(f"curso{level}{kind}", "un curso", curve.course(level, 3, kind))
            if training
            else None
        )
        return Position(
            level,
            f"Puesto {level}",
            pay,
            Mechanic.DIALOGUE,
            60,
            tasks,
            days=days,
            content="x",
            training=course,
        )

    trunk = (
        position(1, 1_800),
        position(2, 2_800),
        position(3, curve.p3),
        position(4, curve.pay(4)),
        position(5, curve.pay(5)),
    )
    branches = tuple(
        Branch(
            kind.value,
            kind.value,
            "x",
            "x",
            tuple(position(lvl, curve.pay(lvl, kind), kind, lvl % 2 == 1) for lvl in range(6, 11)),
            pension=0,
            retired_as="x",
            kind=kind,
        )
        for kind in BranchKind
    )
    return Job(
        key="prueba", name="Prueba", emoji="x", blurb="x", positions=trunk, branches=branches
    )


# -- La curva ----------------------------------------------------------------------------


def test_la_curva_sube_p4_y_p5_desde_p3_y_las_ramas_por_su_clase() -> None:
    curve = PayCurve(4_500)
    p4 = round(4_500 * TRUNK_STEPS[0], -2)
    p5 = round(p4 * TRUNK_STEPS[1], -2)
    assert (curve.pay(4), curve.pay(5)) == (p4, p5)
    for kind, growth in BRANCH_GROWTH.items():
        assert curve.pay(6, kind) == round(p5 * growth, -2)
        assert curve.pay(10, kind) == round(p5 * growth**5, -2)
    assert curve.pay(10, BranchKind.RISKY) > curve.pay(10, BranchKind.SAFE)
    assert curve.course(7, 2.5, BranchKind.SAFE) == round(2.5 * curve.pay(7, BranchKind.SAFE), -2)
    with pytest.raises(ValueError):
        curve.pay(3)
    with pytest.raises(ValueError):
        curve.pay(6)


def test_todos_los_oficios_cumplen_la_curva() -> None:
    problems = [p for job in JOBS for p in career_problems(job)]
    assert not problems, "\n".join(problems)


def test_un_oficio_nuevo_solo_con_su_p3_cumple_la_curva() -> None:
    assert career_problems(invented_job()) == []


def test_los_puestos_del_catalogo_salen_de_su_curva() -> None:
    for job in JOBS:
        curve = PayCurve(job.positions[2].base_pay)
        for branch_key, ladder in job.ladders:
            kind = job.branch(branch_key).kind if branch_key else ""
            for position in ladder[3:]:
                in_branch = position.level > job.trunk_top
                assert position.base_pay == curve.pay(position.level, kind if in_branch else "")
                if in_branch and position.level < job.top:
                    assert position.days == BRANCH_DAYS[position.level]


# -- Experiencia, veteranía y días de antes ----------------------------------------------


def test_la_experiencia_acorta_mas_el_tronco_que_las_ramas_y_tiene_suelo() -> None:
    assert experience_factor(0, trunk=True) == experience_factor(0, trunk=False) == 1
    assert experience_factor(1, trunk=True) < experience_factor(1, trunk=False)
    assert experience_factor(20, trunk=True) == pytest.approx(0.2)
    assert experience_factor(20, trunk=False) == pytest.approx(0.3)


def test_la_experiencia_rebaja_tambien_las_tareas_y_su_texto() -> None:
    job = JOB_BY_KEY["obra"]
    position = job.position(1)
    assert required_tasks(position, job) == position.tasks
    tasks = required_tasks(position, job, completed_elsewhere=1)
    for before, after in zip(position.tasks, tasks, strict=True):
        assert 1 <= after.goal < before.goal or before.goal == 1
        assert str(after.goal) in after.text and after.key == before.key


def test_la_veterania_sube_el_sueldo_un_10_por_rama_con_tope() -> None:
    assert veteran_bonus(0) == 0
    assert veteran_bonus(2) == pytest.approx(0.2)
    assert veteran_bonus(40) == VETERAN_MAX


def test_quien_ya_estaba_en_un_puesto_conserva_los_dias_de_antes() -> None:
    job = JOB_BY_KEY["obra"]
    position = job.position(7, "ladrillo")
    assert required_days(position, job) == BRANCH_DAYS[7]
    assert required_days(position, job, legacy=True) == LEGACY_DAYS[7]
    contract = Contract(job="obra", level=7, branch="ladrillo", legacy_days=True)
    contract.performance = 100
    contract.progress = {t.stat: t.goal for t in position.tasks}
    common = {"trainings": {"costas", "convenio"}, "today": "2026-10-19"}
    ready = promotion_blockers(contract, job, days_in_position=LEGACY_DAYS[7], **common)
    assert ready == []
    promote(contract, 0.0)
    assert contract.legacy_days is False
    contract.performance = 100
    contract.progress = {t.stat: t.goal for t in job.position(8, "ladrillo").tasks}
    waiting = promotion_blockers(contract, job, days_in_position=LEGACY_DAYS[8], **common)
    assert any("días distintos" in line for line in waiting)


def test_cambiar_a_un_oficio_igual_de_pagado_se_recupera_pronto() -> None:
    for before in JOBS:
        top = max(ladder[-1].base_pay for _b, ladder in before.ladders)
        for after in JOBS:
            best = max(ladder[-1].base_pay for _b, ladder in after.ladders)
            if after is before or best * (1 + veteran_bonus(2)) < top:
                continue
            outlook = catch_up(after, top, 2)
            assert outlook is not None and outlook[0] <= 40, (before.key, after.key, outlook)
            assert outlook[1] >= 0


# -- Pensión -----------------------------------------------------------------------------


def test_la_pension_crece_con_cada_rama_y_nunca_pasa_del_tope() -> None:
    every = [(b.kind, 1.0) for job in JOBS for b in job.branches]
    amounts = [pension_amount(every[:n]) for n in range(len(every) + 1)]
    assert amounts[0] == 0
    assert all(b > a for a, b in zip(amounts, amounts[1:], strict=False))
    assert amounts[-1] <= PENSION_CAP
    assert pension_amount([(BranchKind.SAFE, 1.0)]) > pension_amount([(BranchKind.RISKY, 1.0)])
    assert pension_amount([(BranchKind.SAFE, 0.5)]) < pension_amount([(BranchKind.SAFE, 1.0)])
