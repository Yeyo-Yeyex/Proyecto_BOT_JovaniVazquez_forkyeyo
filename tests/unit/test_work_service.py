"""Pruebas de los casos de uso de `pala` (`bot.services.pala`) con la economía real.

Se usa SQLite temporal, un reloj controlable y `roll` sustituido para decidir
cuándo hay accidente, inspección o evento.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from bot.repositories.economy import SHOP_ACCOUNT_ID, STATE_ACCOUNT_ID, EconomyRepository
from bot.repositories.work import WorkRepository
from bot.services import pala
from bot.services.achievements import work_stats
from bot.services.economy import (
    DAILY_BASE,
    IMV_FLOOR_SHARE,
    IMV_WORK_EXEMPT,
    STARTING_BALANCE,
    EconomyService,
    imv_after_work,
)
from bot.services.levels import TIMEZONE
from bot.services.pala import NeedsBlack, WorkError, WorkService
from bot.services.taxes import igic
from bot.services.work import (
    ACCIDENT_ZOMBIE,
    BATTERY_MAX,
    FLAT_RATE_FEE,
    FOLLOWERS_START,
    INSPECTION_SURCHARGE,
    LEGAL_EXTRAS_PER_WEEK,
    MEGAVIRAL,
    ORDINARY_SHIFTS,
    PERFORMANCE_START,
    RAIN_ACCIDENT,
    RAIN_PAY,
    RATING_BLOCK,
    RATING_BLOCK_SECONDS,
    RATING_RESET,
    RATING_START,
    SELF_EMPLOYED_FEE,
    SHIFT_COST,
    TRIENNIUM_DAYS,
    TRIENNIUM_PAY,
    WISH_BY_KEY,
    WISH_PAY,
    WISH_PERFORMANCE,
    Career,
    ShiftKind,
    Wish,
    follower_gain,
    rating_pay,
    shift_pay,
)
from bot.services.work_catalog import COFFEE_BY_KEY, JOB_BY_KEY
from bot.services.work_games import MiniGame

GUILD = 1
USER = 10
# Martes 6 de octubre de 2026 a mediodía, hora canaria.
START = 1_791_284_400.0


class Clock:
    """Reloj controlable."""

    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Dice:
    """Sustituto de `pala.roll`: decide qué probabilidades salen."""

    def __init__(self) -> None:
        self.hits: set[float] = set()

    def __call__(self, rng: random.Random, chance: float) -> bool:
        return chance in self.hits


@pytest.fixture
def dice(monkeypatch: pytest.MonkeyPatch) -> Dice:
    fake = Dice()
    monkeypatch.setattr(pala, "roll", fake)
    return fake


#: Un deseo que ningún turno cumple.
NO_WISH = Wish("ninguno", "Nada.", lambda facts: False)


@pytest.fixture(autouse=True)
def sin_deseo(monkeypatch: pytest.MonkeyPatch) -> None:
    """El deseo del día cambia el sueldo y la barra: fuera, salvo en sus pruebas.

    Las pruebas del deseo vuelven a sustituir `pala.wish_for` por el que quieren.
    """
    monkeypatch.setattr(pala, "wish_for", lambda *args: NO_WISH)


async def make(tmp_path: Path, clock: Clock | None = None) -> WorkService:
    clock = clock or Clock()
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    repository = WorkRepository(tmp_path / "bot.db")
    await repository.initialize()
    return WorkService(repository, economy, clock=clock, rng=random.Random(1))


def play(game: MiniGame, *, perfect: bool = True) -> None:
    """Juega el turno entero, bien o pulsando siempre el primer botón."""
    now = game.started_at
    while not game.finished(now):
        if game.showing:
            game.hide(now)
            continue
        current = game.current
        assert current is not None
        if perfect:
            option = (
                current.answer[game.step] if game.mechanic.value == "memoria" else current.answer[0]
            )
        else:
            option = next(i for i in range(len(current.options)) if i not in current.answer)
        game.press(option, now)
        now += 0.5


async def work(service: WorkService, *, perfect: bool = True, black_ok: bool = False):  # noqa: ANN201
    shift = await service.start_shift(GUILD, USER, black_ok=black_ok)
    play(shift.game, perfect=perfect)
    return await service.finish_shift(GUILD, USER, shift)


def ledger(tmp_path: Path, user_id: int) -> tuple[int, int]:
    """`(suma del libro, saldo)` de una cuenta."""
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total), int(row[0]) if row else 0


# -- Contratar y fichar ---------------------------------------------------------------------


async def test_sin_contrato_no_se_ficha(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    assert await service.status(GUILD, USER) is None
    with pytest.raises(WorkError):
        await service.start_shift(GUILD, USER)


async def test_un_turno_paga_nomina_y_el_estado_recibe_todo_lo_retenido(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    outcome = await work(service)
    slip = outcome.payslip
    assert slip is not None
    assert outcome.kind is ShiftKind.ORDINARY
    assert outcome.score == 100
    assert slip.gross == round(JOB_BY_KEY["obra"].position(1).base_pay * 1.3)
    assert outcome.balance == STARTING_BALANCE + slip.net
    total, balance = ledger(tmp_path, USER)
    assert total == balance == outcome.balance
    state_total, state = ledger(tmp_path, STATE_ACCOUNT_ID)
    assert state_total == state == slip.ss_worker + slip.irpf + slip.ss_employer
    treasury = await service.economy.treasury(GUILD, since=0)
    assert treasury.collected_total == slip.total_taxes
    assert treasury.top_contributors == ((USER, slip.total_taxes),)


async def test_cada_turno_gasta_bateria_y_cuenta_para_la_jornada(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    outcome = await work(service)
    assert outcome.battery_after == BATTERY_MAX - 18
    status = await service.status(GUILD, USER)
    assert status is not None
    assert status.contract.shifts_today == 1
    assert status.contract.progress["good"] == 1


async def test_pasadas_las_extras_legales_hay_que_aceptar_el_b(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "obra")
    kinds = []
    for _ in range(ORDINARY_SHIFTS + LEGAL_EXTRAS_PER_WEEK):
        kinds.append((await work(service)).kind)
        clock.now += 600
    assert kinds.count(ShiftKind.EXTRA) == LEGAL_EXTRAS_PER_WEEK
    with pytest.raises(NeedsBlack):
        await service.start_shift(GUILD, USER)
    before = await service.economy.balance(GUILD, USER)
    state_before = ledger(tmp_path, STATE_ACCOUNT_ID)[1]
    outcome = await work(service, black_ok=True)
    assert outcome.kind is ShiftKind.BLACK
    assert outcome.payslip is None and outcome.black > 0
    assert outcome.balance == before + outcome.black
    # En negro: ni un yapdólar para el Estado y no lo ve el IMV.
    assert ledger(tmp_path, STATE_ACCOUNT_ID)[1] == state_before


async def test_si_la_inspeccion_pilla_el_b_devuelves_con_recargo_y_sin_imv(
    tmp_path: Path, dice: Dice
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    contract_service = service
    await contract_service.hire(GUILD, USER, "obra")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.shifts_today = ORDINARY_SHIFTS
    status.contract.extras_week = LEGAL_EXTRAS_PER_WEEK
    status.contract.shift_day = "2026-10-06"
    await service.repository.save_contract(GUILD, USER, status.contract)
    dice.hits.add(pala.INSPECTION_CHANCE)
    outcome = await work(service, black_ok=True)
    assert outcome.caught
    assert outcome.fine == round(outcome.black * (1 + INSPECTION_SURCHARGE))
    assert ledger(tmp_path, STATE_ACCOUNT_ID)[1] == outcome.fine
    daily = await service.economy.claim_daily(GUILD, USER)
    assert not daily.claimed and daily.suspended_until > clock.now


async def test_el_trabajo_reduce_el_imv_pero_nunca_lo_quita(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "politica")
    status = await service.status(GUILD, USER)
    assert status is not None
    # Comisario europeo, lo más alto de la rama Bruselas: sueldos enormes.
    status.contract.level, status.contract.branch = JOB_BY_KEY["politica"].top, "bruselas"
    await service.repository.save_contract(GUILD, USER, status.contract)
    for _ in range(5):
        await work(service)
        clock.now += 600
    net = await service.economy.work_week_net(GUILD, USER)
    assert net > IMV_WORK_EXEMPT * 10
    daily = await service.economy.claim_daily(GUILD, USER)
    assert daily.claimed
    assert daily.full_amount == DAILY_BASE
    assert daily.amount == round(DAILY_BASE * IMV_FLOOR_SHARE)
    assert daily.reduction == DAILY_BASE - daily.amount


def test_la_reduccion_del_imv_sigue_la_regla_de_la_mitad() -> None:
    assert imv_after_work(1_500, IMV_WORK_EXEMPT) == 1_500
    # 14.000 Y$ de nómina de más son 1.400 Y$ de IMV (otra escala); la mitad, 700
    # a la semana: 100 al día.
    assert imv_after_work(1_500, IMV_WORK_EXEMPT + 14_000) == 1_500 - 100
    assert imv_after_work(1_500, 10**9) == 300


async def test_el_autonomo_paga_la_cuota_una_vez_por_semana(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "obra")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level = 5  # constructor, autónomo
    await service.repository.save_contract(GUILD, USER, status.contract)
    first = await work(service)
    clock.now += 600
    second = await work(service)
    assert first.fee == SELF_EMPLOYED_FEE and second.fee == 0
    assert first.payslip is not None and first.payslip.ss_worker == 0
    total, balance = ledger(tmp_path, USER)
    assert total == balance


async def test_trabajar_zombi_puede_acabar_en_baja_con_prestacion(
    tmp_path: Path, dice: Dice
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "obra")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.battery = -10
    status.contract.battery_at = clock.now
    await service.repository.save_contract(GUILD, USER, status.contract)
    dice.hits.add(ACCIDENT_ZOMBIE)
    outcome = await work(service)
    assert outcome.accident and outcome.sick_pay > 0
    with pytest.raises(WorkError, match="baja"):
        await service.start_shift(GUILD, USER)


async def test_sin_bateria_no_se_ficha_pero_un_barraquito_ayuda(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "obra")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.battery = -30
    status.contract.battery_at = clock.now
    await service.repository.save_contract(GUILD, USER, status.contract)
    with pytest.raises(WorkError, match="reventado"):
        await service.start_shift(GUILD, USER)
    coffee = COFFEE_BY_KEY["barraquito"]
    battery, coffees, balance = await service.buy_coffee(GUILD, USER, "barraquito")
    assert battery == -30 + coffee.battery and coffees == 1
    assert balance == STARTING_BALANCE - coffee.price - igic(coffee.price)
    assert ledger(tmp_path, STATE_ACCOUNT_ID)[1] == igic(coffee.price)
    assert ledger(tmp_path, SHOP_ACCOUNT_ID)[1] == coffee.price
    await service.start_shift(GUILD, USER)


# -- Ascensos ------------------------------------------------------------------------


async def test_ascenso_completo_con_formacion(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "obra")
    for _day in range(2):
        for _ in range(2):
            await work(service)
            clock.now += 600
        clock.now += 86_400
    status = await service.status(GUILD, USER)
    assert status is not None
    assert status.blockers == ["Conseguir el curso de PRL de 20 horas (en 🎓 Formación)"]
    with pytest.raises(WorkError):
        await service.accept_promotion(GUILD, USER)
    name, base, tax, _ = await service.buy_training(GUILD, USER)
    assert "PRL" in name and tax == igic(base)
    with pytest.raises(WorkError):
        await service.buy_training(GUILD, USER)
    status = await service.accept_promotion(GUILD, USER)
    assert status.contract.level == 2
    assert status.position.title == "Oficial de segunda"


async def test_cambiar_de_oficio_y_volver_recupera_el_puesto(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level = 3
    await service.repository.save_contract(GUILD, USER, status.contract)
    status = await service.hire(GUILD, USER, "hosteleria")
    assert status.contract.level == 1
    status = await service.hire(GUILD, USER, "obra")
    assert status.contract.level == 3
    with pytest.raises(WorkError):
        await service.hire(GUILD, USER, "obra")
    assert (await service.history(GUILD, USER))["obra"] == Career(3, 3, "")


# -- Eventos -------------------------------------------------------------------------


async def test_el_sobre_de_la_uco_cobra_multa_y_pregunta_si_dimites(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "politica")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level = 3
    await service.repository.save_contract(GUILD, USER, status.contract)
    await money(service, 50_000)
    dice.hits.add(0.25)  # el riesgo del sobre
    result = await service.resolve_event(GUILD, USER, "sobre", 0)
    assert result.black > 0 and result.caught and result.caught_by == "uco"
    assert not result.pardoned
    assert result.fine == round(result.black * 1.2)
    assert result.follow_up is not None and result.follow_up.key == "dimision"
    resign = await service.resolve_event(GUILD, USER, "dimision", 0)
    assert resign.demoted
    status = await service.status(GUILD, USER)
    assert status is not None and status.contract.level == 2
    total, balance = ledger(tmp_path, USER)
    assert total == balance


async def test_el_indulto_te_libra_de_la_multa(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "politica")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level = 3
    await service.repository.save_contract(GUILD, USER, status.contract)
    dice.hits.update({0.25, pala.PARDON_CHANCE})
    result = await service.resolve_event(GUILD, USER, "sobre", 0)
    assert result.caught and result.pardoned and result.fine == 0
    assert result.stats == {"work_envelope": 1, "work_pardoned": 1, "work_caught_uco": 1}


async def test_quedarse_paga_media_base_en_nomina(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    result = await service.resolve_event(GUILD, USER, "quedarse", 0)
    assert result.paid is not None
    assert result.paid.gross == round(JOB_BY_KEY["obra"].position(1).base_pay * 0.5)
    status = await service.status(GUILD, USER)
    assert status is not None and status.contract.family < 100


# -- Canales -------------------------------------------------------------------------


async def test_los_canales_de_la_pala_se_anaden_y_se_quitan(tmp_path: Path) -> None:
    service = await make(tmp_path)
    assert await service.channels(GUILD) == frozenset()
    assert await service.toggle_channel(GUILD, 5) == (True, frozenset({5}))
    assert await service.toggle_channel(GUILD, 6) == (True, frozenset({5, 6}))
    assert await service.toggle_channel(GUILD, 5) == (False, frozenset({6}))
    await service.clear_channels(GUILD)
    assert await service.channels(GUILD) == frozenset()


def test_el_reloj_de_las_pruebas_es_un_martes_a_mediodia() -> None:
    from datetime import datetime

    moment = datetime.fromtimestamp(START, TIMEZONE)
    assert moment.weekday() == 1 and moment.hour == 12


# -- Sanidad: guardias ---------------------------------------------------------------


async def set_level(service: WorkService, job: str, level: int) -> None:
    await service.hire(GUILD, USER, job)
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level = level
    await service.repository.save_contract(GUILD, USER, status.contract)


async def test_el_celador_no_hace_guardias(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "sanidad")
    with pytest.raises(WorkError, match="guardias"):
        await service.start_shift(GUILD, USER, guard=True)


async def test_en_sanidad_el_turno_normal_apenas_sube_la_barra(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await set_level(service, "sanidad", 2)
    await work(service)
    status = await service.status(GUILD, USER)
    assert status is not None
    assert status.contract.performance == 50 + 5


async def test_la_guardia_paga_mas_rinde_el_doble_y_te_deja_saliente(
    tmp_path: Path, dice: Dice
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await set_level(service, "sanidad", 2)
    shift = await service.start_shift(GUILD, USER, guard=True)
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    base = JOB_BY_KEY["sanidad"].position(2).base_pay
    assert outcome.kind is ShiftKind.GUARD
    assert outcome.payslip is not None
    assert outcome.payslip.gross == round(base * 1.3 * 1.6)
    status = await service.status(GUILD, USER)
    assert status is not None
    assert status.contract.performance == 50 + 50
    assert status.contract.shifts_today == 0  # no es jornada ordinaria
    assert status.contract.guards_week == 1
    assert status.contract.progress["guards"] == 1
    with pytest.raises(pala.OffDuty):
        await service.start_shift(GUILD, USER)
    clock.now += 12 * 3600 + 1
    await service.start_shift(GUILD, USER)


async def test_al_mir_le_baja_la_barra_si_no_hizo_las_guardias(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await set_level(service, "sanidad", 4)
    await work(service, perfect=False)  # turno del martes: abre la semana sin guardias
    status = await service.status(GUILD, USER)
    assert status is not None
    before = status.contract.performance
    clock.now += 7 * 86_400  # el martes siguiente
    outcome = await work(service, perfect=False)
    assert outcome.missed_guards == 2
    status = await service.status(GUILD, USER)
    assert status is not None
    # Sin la penalización bajaría como mucho 25 por el mal turno; con ella, 40 más.
    assert status.contract.performance == max(0, before - 25 - 2 * 20)


async def test_en_la_rama_publica_la_guardia_cuadra_quirofanos_y_cuenta_la_nochevieja(
    tmp_path: Path, dice: Dice
) -> None:
    from datetime import datetime

    from bot.services.achievements import work_stats

    service = await make(tmp_path)
    await service.hire(GUILD, USER, "sanidad")
    status = await service.status(GUILD, USER)
    assert status is not None
    # Nivel y rama a la vez: un puesto de rama sin rama se baja al tronco.
    status.contract.level, status.contract.branch = 6, "publica"
    await service.repository.save_contract(GUILD, USER, status.contract)
    shift = await service.start_shift(GUILD, USER, guard=True)
    assert shift.position.title == "Jefe de sección"
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    add = work_stats(outcome).add
    assert add["work_surgeries"] == len(shift.game.rounds)
    assert add["work_guards"] == 1 and "work_guard_nye" not in add
    outcome.now = datetime(2026, 12, 31, 22, tzinfo=TIMEZONE).timestamp()
    assert work_stats(outcome).add["work_guard_nye"] == 1


async def test_en_la_rama_hotelera_las_pulseras_cuentan_en_los_logros(
    tmp_path: Path, dice: Dice
) -> None:
    from bot.services.achievements import work_stats

    service = await make(tmp_path)
    await service.hire(GUILD, USER, "hosteleria")
    status = await service.status(GUILD, USER)
    assert status is not None
    # Nivel y rama a la vez: un puesto de rama sin rama se baja al tronco.
    status.contract.level, status.contract.branch = 6, "hotel"
    await service.repository.save_contract(GUILD, USER, status.contract)
    shift = await service.start_shift(GUILD, USER)
    assert shift.position.title == "Director de hotel en primera línea"
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    add = work_stats(outcome).add
    assert add["work_wristbands"] == len(shift.game.rounds)


# -- Oficina: teletrabajo, stock options --------------------------------------------


async def test_el_teletrabajo_cansa_la_mitad_y_rinde_la_mitad(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "oficina")
    shift = await service.start_shift(GUILD, USER, remote=True)
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    assert outcome.remote
    assert outcome.battery_after == BATTERY_MAX - 9
    status = await service.status(GUILD, USER)
    # Teletrabajo: la mitad de 25 más el bonus de la familia feliz.
    assert status is not None and status.contract.performance == 50 + (25 + 3) // 2


async def test_teletrabajando_te_escriben_fuera_de_hora(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "oficina")
    dice.hits.add(pala.REMOTE_PING_CHANCE)
    shift = await service.start_shift(GUILD, USER, remote=True)
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    assert outcome.event is not None and outcome.event.key == "desconexion"


async def test_el_cto_cobra_en_opciones_y_el_exit_paga_exento_y_con_irpf(
    tmp_path: Path, dice: Dice
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await set_level(service, "oficina", 5)
    first = await work(service)
    assert first.options_added == round(first.gross * 0.3)
    assert first.payslip is not None and first.payslip.gross == first.gross - first.options_added
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.options = 3_000_000
    await service.repository.save_contract(GUILD, USER, status.contract)
    dice.hits.add(pala.EXIT_CHANCE)
    clock.now += 600
    before = await service.economy.balance(GUILD, USER)
    outcome = await work(service)
    assert outcome.exit_payout >= 3_000_000 * 2
    assert outcome.options_total == 0
    gained = outcome.balance - before
    assert gained > pala.OPTIONS_EXEMPT  # la parte exenta llega entera
    total, balance = ledger(tmp_path, USER)
    assert total == balance


async def test_si_la_startup_quiebra_las_opciones_se_pierden(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await set_level(service, "oficina", 5)
    dice.hits.add(pala.BANKRUPT_CHANCE)
    outcome = await work(service)
    assert outcome.bankrupt and outcome.options_total == 0


async def set_branch(service: WorkService, level: int, branch: str) -> None:
    await service.hire(GUILD, USER, "oficina")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level, status.contract.branch = level, branch
    await service.repository.save_contract(GUILD, USER, status.contract)


async def test_en_la_unicornio_se_cobra_en_opciones_en_todos_los_puestos(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await set_branch(service, 8, "unicornio")
    outcome = await work(service)
    assert outcome.options_added == round(outcome.gross * 0.3)
    assert outcome.payslip is not None
    assert outcome.payslip.gross == outcome.gross - outcome.options_added
    assert outcome.options_total == outcome.options_added > 0


async def test_en_la_consultora_no_se_acumulan_opciones_nuevas(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await set_branch(service, 8, "consultora")
    outcome = await work(service)
    assert outcome.options_added == 0 and outcome.options_total == 0


async def test_las_opciones_que_ya_tienes_dan_exit_o_quiebran_aunque_ascendieras_a_la_consultora(
    tmp_path: Path, dice: Dice
) -> None:
    for chance in (pala.EXIT_CHANCE, pala.BANKRUPT_CHANCE):
        service = await make(tmp_path / str(chance))
        await set_branch(service, 7, "consultora")
        status = await service.status(GUILD, USER)
        assert status is not None
        status.contract.options = 3_000_000
        await service.repository.save_contract(GUILD, USER, status.contract)
        dice.hits = {chance}
        outcome = await work(service)
        assert outcome.options_added == 0 and outcome.options_total == 0
        if chance == pala.EXIT_CHANCE:
            assert outcome.exit_payout >= 3_000_000 * 2 and not outcome.bankrupt
        else:
            assert outcome.bankrupt and outcome.exit_payout == 0


async def test_un_turno_de_jefe_de_proyecto_suma_las_horas_imputadas(
    tmp_path: Path, dice: Dice
) -> None:
    from bot.services.achievements import work_stats

    service = await make(tmp_path)
    await set_branch(service, 6, "consultora")
    shift = await service.start_shift(GUILD, USER)
    assert shift.position.title == "Jefe de proyecto"
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    assert work_stats(outcome).add["work_hours_billed"] == len(shift.game.rounds)


# -- Oficina: Hong Kong -------------------------------------------------------------


async def money(service: WorkService, amount: int) -> None:
    from bot.repositories.economy import LedgerEntry

    await service.economy.repository.apply(GUILD, USER, [LedgerEntry(amount, "prueba")])


async def test_el_becario_no_se_va_a_hong_kong(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "oficina")
    with pytest.raises(WorkError, match="Hong Kong"):
        await service.move_abroad(GUILD, USER)


async def test_desde_hong_kong_paga_alli_y_no_hay_imv(tmp_path: Path, dice: Dice) -> None:
    from bot.repositories.economy import HK_ACCOUNT_ID

    clock = Clock()
    service = await make(tmp_path, clock)
    await set_level(service, "oficina", 3)
    await money(service, 50_000)
    status = await service.move_abroad(GUILD, USER)
    assert status.contract.abroad == "hk" and status.phase == "residente"
    assert status.battery == BATTERY_MAX - 30  # jet lag
    with pytest.raises(WorkError, match="Hong Kong"):
        await service.hire(GUILD, USER, "obra")
    daily = await service.economy.claim_daily(GUILD, USER)
    assert not daily.claimed and daily.abroad == "hk"

    state_before = ledger(tmp_path, STATE_ACCOUNT_ID)[1]
    outcome = await work(service)
    foreign = outcome.foreign
    assert foreign is not None and outcome.payslip is None
    base = JOB_BY_KEY["oficina"].position(3).base_pay
    assert foreign.gross == round(round(base * 1.3) * 2)
    assert foreign.resident and foreign.exempt > 0
    assert ledger(tmp_path, HK_ACCOUNT_ID)[1] == foreign.foreign
    assert ledger(tmp_path, STATE_ACCOUNT_ID)[1] - state_before == foreign.irpf
    total, balance = ledger(tmp_path, USER)
    assert total == balance

    clock.now += 5 * 86_400  # más de 4 días fuera: ya no es residente
    outcome = await work(service)
    assert outcome.phase == "no_residente"
    assert outcome.foreign is not None and outcome.foreign.irpf == 0
    assert outcome.foreign.exempt == 0


async def test_volver_tras_cinco_anos_fuera_trae_la_ley_beckham(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await set_level(service, "oficina", 4)
    await money(service, 100_000)
    await service.move_abroad(GUILD, USER)
    clock.now += 36 * 86_400
    status, beckham, days = await service.come_home(GUILD, USER)
    assert beckham and days >= 35
    assert status.beckham and not status.contract.abroad
    assert (await service.economy.claim_daily(GUILD, USER)).abroad is None
    outcome = await work(service)
    assert outcome.beckham
    assert outcome.payslip is not None and outcome.payslip.rates.irpf == pytest.approx(0.24)


async def test_volver_pronto_no_trae_beckham(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await set_level(service, "oficina", 3)
    await money(service, 100_000)
    await service.move_abroad(GUILD, USER)
    clock.now += 2 * 86_400
    status, beckham, _days = await service.come_home(GUILD, USER)
    assert not beckham and not status.beckham


async def test_ignorar_la_carta_de_hacienda_puede_salir_cara(tmp_path: Path, dice: Dice) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await set_level(service, "oficina", 3)
    await money(service, 50_000)
    await service.move_abroad(GUILD, USER)
    dice.hits.add(0.4)
    state_before = ledger(tmp_path, STATE_ACCOUNT_ID)[1]
    result = await service.resolve_event(GUILD, USER, "carta_hacienda", 1)
    assert result.caught and result.caught_by == "hacienda"
    assert result.fine == round(JOB_BY_KEY["oficina"].position(3).base_pay * 3)
    assert ledger(tmp_path, STATE_ACCOUNT_ID)[1] - state_before == result.fine
    # Hacienda no suspende el IMV (eso es cosa de la Inspección y la UCO).
    assert await service.economy.repository.imv_suspended_until(GUILD, USER) == 0


# -- Herramientas de curro y logros del minijuego ------------------------------------------


async def test_las_herramientas_entran_en_el_turno_y_en_los_logros(
    tmp_path: Path, dice: Dice
) -> None:
    from bot.services.achievements import work_stats

    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    shift = await service.start_shift(
        GUILD, USER, tools={"seguro_rc", "chaleco_reflectante", "argumentario", "huevo"}
    )
    assert {t.key for t in shift.perks.tools} == {"seguro_rc", "chaleco_reflectante"}
    game = shift.game
    assert game.retries == 1 and game.insured
    # Rompe dos cosas: la primera la perdona el chaleco; la segunda la paga el seguro.
    now = game.started_at
    while game.saved + game.insured_breaks < 2 and not game.finished(now):
        current = game.current
        assert current is not None
        game.press(next(iter(current.penalty)) if current.penalty else current.answer[0], now)
    play(game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    add = work_stats(outcome).add
    assert add["work_saves"] == 1 and add["work_insured"] == 1
    assert "work_clean_digs" not in add  # rompió algo, aunque pagara el seguro


async def test_un_turno_limpio_y_rapido_cuenta_para_sus_logros(tmp_path: Path, dice: Dice) -> None:
    from bot.services.achievements import work_stats

    service = await make(tmp_path)
    await service.hire(GUILD, USER, "politica")
    shift = await service.start_shift(GUILD, USER)
    play(shift.game)  # medio segundo por jugada: sobra más de la mitad del tiempo
    outcome = await service.finish_shift(GUILD, USER, shift)
    add = work_stats(outcome).add
    assert add["work_posters"] == len(shift.game.rounds)
    assert add["work_memory_flawless"] == 1 and add["work_fast"] == 1
    assert "work_first_miss" not in add and "work_last_second" not in add
    assert "work_constitution" not in add
    from datetime import datetime

    outcome.now = datetime(2026, 12, 6, 12, tzinfo=TIMEZONE).timestamp()
    assert work_stats(outcome).add["work_constitution"] == 1


# -- Reparto: el tiempo, la valoración de la app y la tarifa plana -------------------------


async def test_con_lluvia_el_rider_cobra_mas_y_paga_la_tarifa_plana(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "weather", lambda now: "lluvia")
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "reparto")
    outcome = await work(service)
    base = JOB_BY_KEY["reparto"].position(1).base_pay
    gross = round(shift_pay(base, 100, ShiftKind.ORDINARY) * rating_pay(RATING_START))
    assert outcome.weather == "lluvia"
    assert outcome.gross == round(gross * RAIN_PAY)
    assert outcome.fee == FLAT_RATE_FEE < SELF_EMPLOYED_FEE
    assert outcome.payslip is not None and outcome.payslip.ss_worker == 0
    assert outcome.rating == RATING_START + 2
    total, balance = ledger(tmp_path, USER)
    assert total == balance


async def test_con_lluvia_te_puedes_caer_aunque_vayas_descansado(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "weather", lambda now: "lluvia")
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "reparto")
    dice.hits.add(RAIN_ACCIDENT)
    outcome = await work(service)
    assert outcome.battery_before == BATTERY_MAX
    assert outcome.accident


async def test_con_calima_el_turno_cansa_la_mitad_mas(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "weather", lambda now: "calima")
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "reparto")
    outcome = await work(service)
    assert outcome.battery_after == BATTERY_MAX - round(18 * 1.5)


async def test_con_mala_valoracion_la_app_te_desconecta(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "weather", lambda now: "sol")
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "reparto")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.rating = RATING_BLOCK + 1
    await service.repository.save_contract(GUILD, USER, status.contract)
    outcome = await work(service, perfect=False)
    assert outcome.blocked and outcome.rating == RATING_RESET
    with pytest.raises(WorkError, match="desconectado"):
        await service.start_shift(GUILD, USER)
    clock.now += RATING_BLOCK_SECONDS + 1
    await service.start_shift(GUILD, USER)


async def test_un_evento_del_reparto_mueve_la_valoracion(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "reparto")
    await service.resolve_event(GUILD, USER, "sexto_sin_ascensor", 1)
    status = await service.status(GUILD, USER)
    assert status is not None and status.contract.rating == RATING_START - 4


async def test_en_las_ramas_del_reparto_no_hay_valoracion_pero_si_tiempo(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "weather", lambda now: "lluvia")
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "reparto")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level, status.contract.branch = 6, "correos"
    status.contract.rating = RATING_START + 7
    await service.repository.save_contract(GUILD, USER, status.contract)
    base = JOB_BY_KEY["reparto"].position(6, "correos").base_pay
    outcome = await work(service)
    # Sin estrellas: ni cambian ni multiplican el sueldo; la lluvia sigue pagando.
    assert outcome.rating == 0 and outcome.weather == "lluvia"
    assert outcome.gross == round(shift_pay(base, 100, ShiftKind.ORDINARY) * RAIN_PAY)
    await service.resolve_event(GUILD, USER, "sexto_sin_ascensor", 1)
    status = await service.status(GUILD, USER)
    assert status is not None and status.contract.rating == RATING_START + 7
    # En el tronco, en cambio, la nota mueve la valoración y el sueldo.
    status.contract.level, status.contract.branch = 5, ""
    await service.repository.save_contract(GUILD, USER, status.contract)
    trunk = await work(service, perfect=False)
    assert trunk.rating != 0


async def test_un_turno_del_jefe_de_unidad_de_correos_suma_los_envios_clasificados(
    tmp_path: Path, dice: Dice
) -> None:
    from bot.services.achievements import work_stats

    service = await make(tmp_path)
    await service.hire(GUILD, USER, "reparto")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level, status.contract.branch = 6, "correos"
    await service.repository.save_contract(GUILD, USER, status.contract)
    shift = await service.start_shift(GUILD, USER)
    assert shift.position.title == "Jefe de unidad de reparto"
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    assert work_stats(outcome).add["work_parcels"] == len(shift.game.rounds)


# -- Funcionariado: oposición, plaza fija y trienios ---------------------------------------


async def test_estudiar_no_paga_ni_es_jornada_pero_suma_temario(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "funcionariado")
    shift = await service.start_shift(GUILD, USER, study=True)
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    assert outcome.kind is ShiftKind.STUDY
    assert outcome.gross == 0 and outcome.payslip is None
    assert outcome.temario == len(shift.game.rounds)
    assert outcome.battery_after == BATTERY_MAX - 18
    status = await service.status(GUILD, USER)
    assert status is not None
    assert status.contract.shifts_today == 0
    assert status.contract.progress["temario"] == outcome.temario
    assert outcome.balance == STARTING_BALANCE  # no se mueve ni un Y$
    stats = work_stats(outcome)
    assert stats.add == {"work_study": 1, "work_temario": outcome.temario}


async def test_solo_se_estudia_si_hay_oposicion(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    with pytest.raises(WorkError, match="opositar"):
        await service.start_shift(GUILD, USER, study=True)
    # Del inspector en adelante ya no se oposita.
    await set_level(service, "funcionariado", JOB_BY_KEY["funcionariado"].trunk_top)
    with pytest.raises(WorkError, match="opositar"):
        await service.start_shift(GUILD, USER, study=True)


async def test_con_plaza_fija_no_te_bajan_de_puesto(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await set_level(service, "funcionariado", 3)
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.performance = 10
    status.contract.warned = True
    await service.repository.save_contract(GUILD, USER, status.contract)
    outcome = await work(service, perfect=False)
    assert outcome.performance == "warned"
    status = await service.status(GUILD, USER)
    assert status is not None and status.contract.level == 3


async def test_los_trienios_suben_el_sueldo(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await set_level(service, "funcionariado", 2)
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.job_since = START - 2 * TRIENNIUM_DAYS * 86_400
    await service.repository.save_contract(GUILD, USER, status.contract)
    outcome = await work(service)
    base = JOB_BY_KEY["funcionariado"].position(2).base_pay
    assert outcome.triennia == 2
    tenured = shift_pay(base, 100, ShiftKind.ORDINARY, tenured=True)
    assert tenured == round(base * 1.1)
    assert outcome.gross == round(tenured * (1 + 2 * TRIENNIUM_PAY))


async def hire_in_branch(service: WorkService, branch: str) -> None:
    """Un funcionario con 2 trienios cumplidos en el puesto 6 de la rama."""
    await service.hire(GUILD, USER, "funcionariado")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level, status.contract.branch = 6, branch
    status.contract.job_since = START - 2 * TRIENNIUM_DAYS * 86_400
    await service.repository.save_contract(GUILD, USER, status.contract)


async def test_un_turno_de_subdirector_general_suma_el_boe_y_cobra_trienios(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await hire_in_branch(service, "alta")
    shift = await service.start_shift(GUILD, USER)
    assert shift.position.title == "Subdirector general"
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    base = JOB_BY_KEY["funcionariado"].position(6, "alta").base_pay
    # Plaza fija (nota entre el 90 % y el 110 %) y trienios.
    tenured = shift_pay(base, outcome.score, ShiftKind.ORDINARY, tenured=True)
    assert outcome.triennia == 2
    assert outcome.gross == round(tenured * (1 + 2 * TRIENNIUM_PAY))
    assert work_stats(outcome).add["work_boe"] == len(shift.game.rounds)


async def test_un_turno_de_asesor_de_grandes_fortunas_no_cobra_trienios_ni_tiene_plaza(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await hire_in_branch(service, "giratoria")
    status = await service.status(GUILD, USER)
    assert status is not None and status.triennia == 0
    shift = await service.start_shift(GUILD, USER)
    assert shift.position.title == "Asesor fiscal de grandes fortunas"
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    base = JOB_BY_KEY["funcionariado"].position(6, "giratoria").base_pay
    # Excedencia voluntaria: la nota mueve el sueldo del 70 al 130 % y no hay trienios.
    assert outcome.triennia == 0
    assert outcome.gross == shift_pay(base, outcome.score, ShiftKind.ORDINARY)
    assert work_stats(outcome).add["work_wealth"] == len(shift.game.rounds)
    assert "work_triennia_max" not in work_stats(outcome).peak


async def test_en_la_puerta_giratoria_te_pueden_bajar_de_puesto(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await hire_in_branch(service, "giratoria")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.performance = 10
    status.contract.warned = True
    await service.repository.save_contract(GUILD, USER, status.contract)
    outcome = await work(service, perfect=False)
    # Sin plaza fija, la barra a cero no se queda en un aviso.
    assert outcome.performance != "warned"


# -- Influencer: viralidad y seguidores ----------------------------------------------------


async def test_hacerse_viral_multiplica_el_sueldo_y_los_seguidores(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "viral_roll", lambda rng: MEGAVIRAL)
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "influencer")
    outcome = await work(service)
    base = JOB_BY_KEY["influencer"].position(1).base_pay
    assert outcome.viral == MEGAVIRAL
    assert outcome.gross == round(shift_pay(base, 100, ShiftKind.ORDINARY) * MEGAVIRAL)
    gained = follower_gain(100, 1, MEGAVIRAL)
    assert outcome.followers_gained == gained
    assert outcome.followers == FOLLOWERS_START + gained
    stats = work_stats(outcome)
    assert stats.add["work_viral"] == stats.add["work_megaviral"] == 1
    assert stats.peak["work_followers_max"] == outcome.followers
    total, balance = ledger(tmp_path, USER)
    assert total == balance


async def test_la_funa_quita_seguidores_y_cambiar_de_curro_no(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "influencer")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.followers = 1_000
    await service.repository.save_contract(GUILD, USER, status.contract)
    result = await service.resolve_event(GUILD, USER, "funa", 1)
    assert result.stats == {"work_cancelled": 1}
    await service.hire(GUILD, USER, "obra")
    await service.hire(GUILD, USER, "influencer")
    status = await service.status(GUILD, USER)
    assert status is not None and status.contract.followers == 700


async def test_un_turno_de_tertuliano_suma_su_contenido_y_sigue_moviendo_los_seguidores(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "viral_roll", lambda rng: MEGAVIRAL)
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "influencer")
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.level, status.contract.branch = 6, "tele"
    status.contract.followers = 1_000
    await service.repository.save_contract(GUILD, USER, status.contract)
    shift = await service.start_shift(GUILD, USER)
    assert shift.position.title == "Tertuliano"
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    gained = follower_gain(outcome.score, 6, MEGAVIRAL)
    assert outcome.viral == MEGAVIRAL and outcome.followers_gained == gained
    assert outcome.followers == 1_000 + gained
    stats = work_stats(outcome)
    assert stats.add["work_tertulia"] == len(shift.game.rounds)
    assert stats.add["work_viral"] == 1


# -- Carrera larga: ramas, ventajas, jubilación, pensión y deseo del día ----------------------

POLITICA = JOB_BY_KEY["politica"]
DAY = 86_400


async def at_level(service: WorkService, level: int, branch: str = "", **fields: object) -> None:
    """Contrato de política en ese puesto y rama (y con esos campos)."""
    await service.hire(GUILD, USER, "politica")
    contract = await service.repository.contract(GUILD, USER)
    assert contract is not None
    contract.level, contract.branch = level, branch
    for name, value in fields.items():
        setattr(contract, name, value)
    await service.repository.save_contract(GUILD, USER, contract)


async def test_al_acabar_el_tronco_se_elige_rama_y_se_recuerda(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "promotion_blockers", lambda *args, **kwargs: [])
    service = await make(tmp_path)
    await at_level(service, POLITICA.trunk_top)
    status = await service.status(GUILD, USER)
    assert status is not None
    assert [b.key for b in status.fork] == ["moncloa", "bruselas"]
    assert status.next_position is None
    with pytest.raises(WorkError, match="rama"):
        await service.accept_promotion(GUILD, USER)
    status = await service.accept_promotion(GUILD, USER, "bruselas")
    assert (status.contract.level, status.contract.branch) == (POLITICA.trunk_top + 1, "bruselas")
    assert status.position.title == "Eurodiputado"
    assert not status.fork
    # Cambiar de oficio y volver te deja en la misma rama.
    await service.hire(GUILD, USER, "obra")
    status = await service.hire(GUILD, USER, "politica")
    assert (status.contract.level, status.contract.branch) == (POLITICA.trunk_top + 1, "bruselas")
    history = await service.history(GUILD, USER)
    assert history["politica"] == Career(POLITICA.trunk_top + 1, POLITICA.trunk_top + 1, "bruselas")


async def test_en_una_rama_no_se_elige_otra_al_ascender(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "promotion_blockers", lambda *args, **kwargs: [])
    service = await make(tmp_path)
    await at_level(service, POLITICA.trunk_top + 1, "moncloa")
    # La rama que llegue se ignora: el ascenso sigue por la suya.
    status = await service.accept_promotion(GUILD, USER, "bruselas")
    assert (status.contract.level, status.contract.branch) == (POLITICA.trunk_top + 2, "moncloa")
    assert status.position.title == "Ministro"


async def test_si_te_bajan_al_tronco_se_olvida_la_rama(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await at_level(service, POLITICA.trunk_top + 1, "moncloa", performance=0, warned=True)
    outcome = await work(service, perfect=False)
    assert outcome.performance == "demoted"
    status = await service.status(GUILD, USER)
    assert status is not None
    assert (status.contract.level, status.contract.branch) == (POLITICA.trunk_top, "")
    assert status.fork  # vuelve a elegir


async def test_las_ventajas_de_los_puestos_se_suman_al_turno(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await at_level(service, POLITICA.trunk_top)
    shift = await service.start_shift(GUILD, USER)
    perks = shift.perks
    # Concejal (+5 % de tiempo), diputado autonómico (−10 % de cansancio), diputado en
    # el Congreso (un fallo gratis) y portavoz (una respuesta tachada).
    assert len(perks.rewards) == 4
    assert perks.extra_time == pytest.approx(0.05)
    assert perks.battery == pytest.approx(0.10)
    assert perks.retries == 1 and perks.fifty
    assert shift.game.seconds == pytest.approx(POLITICA.position(5).seconds * 1.05)
    play(shift.game)
    outcome = await service.finish_shift(GUILD, USER, shift)
    assert outcome.battery_before - outcome.battery_after == pytest.approx(SHIFT_COST * 0.9)


async def test_en_lo_mas_alto_de_una_rama_te_jubilas_y_sigues_por_la_otra(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await at_level(service, POLITICA.top, "moncloa", performance=90)
    status = await service.status(GUILD, USER)
    assert status is not None and status.can_retire
    retirement = await service.retire(GUILD, USER)
    assert retirement.branch.key == "moncloa"
    assert retirement.title == "Consejero de una eléctrica"
    assert retirement.following is not None and retirement.following.key == "bruselas"
    contract = retirement.status.contract
    assert (contract.level, contract.branch) == (POLITICA.trunk_top + 1, "bruselas")
    assert contract.performance == PERFORMANCE_START
    assert retirement.status.retired == {"moncloa"}
    assert not retirement.status.can_retire
    with pytest.raises(WorkError):
        await service.retire(GUILD, USER)
    # Si le bajan al tronco, ya solo puede elegir la rama que le queda.
    contract.level, contract.branch = POLITICA.trunk_top, ""
    await service.repository.save_contract(GUILD, USER, contract)
    status = await service.status(GUILD, USER)
    assert status is not None and [b.key for b in status.fork] == ["bruselas"]


async def test_jubilado_de_todas_las_ramas_se_queda_arriba(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await at_level(service, POLITICA.top, "moncloa")
    await service.retire(GUILD, USER)
    contract = await service.repository.contract(GUILD, USER)
    assert contract is not None
    contract.level = POLITICA.top
    await service.repository.save_contract(GUILD, USER, contract)
    retirement = await service.retire(GUILD, USER)
    assert retirement.following is None
    contract = retirement.status.contract
    assert (contract.level, contract.branch) == (POLITICA.top, "bruselas")
    pension = await service.pension(GUILD, USER)
    assert pension.amount == sum(b.pension for b in POLITICA.branches)
    assert [branch.key for _job, branch in pension.branches] == ["bruselas", "moncloa"]


async def test_la_pension_se_cobra_una_vez_al_dia_con_irpf_y_sin_cotizar(
    tmp_path: Path, dice: Dice
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    assert not (await service.pension(GUILD, USER)).due
    with pytest.raises(WorkError, match="jubilarse"):
        await service.claim_pension(GUILD, USER)
    await at_level(service, POLITICA.top, "moncloa")
    await service.retire(GUILD, USER)
    moncloa = POLITICA.branch("moncloa")
    assert moncloa is not None
    pension = await service.pension(GUILD, USER)
    assert pension.due and pension.amount == moncloa.pension
    state_before = ledger(tmp_path, STATE_ACCOUNT_ID)[1]
    slip, balance = await service.claim_pension(GUILD, USER)
    assert slip.gross == moncloa.pension
    assert slip.ss_worker == slip.ss_employer == 0
    assert slip.net == slip.gross - slip.irpf
    assert ledger(tmp_path, STATE_ACCOUNT_ID)[1] - state_before == slip.irpf
    total, wallet = ledger(tmp_path, USER)
    assert total == wallet == balance
    assert not (await service.pension(GUILD, USER)).due
    with pytest.raises(WorkError, match="Ya cobraste"):
        await service.claim_pension(GUILD, USER)
    clock.now += DAY
    assert (await service.pension(GUILD, USER)).due
    await service.claim_pension(GUILD, USER)


async def test_si_el_pago_de_la_pension_falla_se_puede_volver_a_cobrar(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = await make(tmp_path)
    await at_level(service, POLITICA.top, "bruselas")
    await service.retire(GUILD, USER)

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("la base de datos se ha ido de vacaciones")

    monkeypatch.setattr(service.economy, "pay_salary", broken)
    with pytest.raises(RuntimeError):
        await service.claim_pension(GUILD, USER)
    assert (await service.pension(GUILD, USER)).due


async def test_cumplir_el_deseo_del_dia_paga_mas_sube_la_barra_y_hace_racha(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    wish = WISH_BY_KEY["nota_90"]
    monkeypatch.setattr(pala, "wish_for", lambda *args: wish)
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "obra")
    status = await service.status(GUILD, USER)
    assert status is not None and status.wish is wish
    outcome = await work(service)
    base = JOB_BY_KEY["obra"].position(1).base_pay
    assert outcome.wish is wish and outcome.wish_streak == 1
    assert outcome.gross == round(shift_pay(base, 100, ShiftKind.ORDINARY) * WISH_PAY)
    status = await service.status(GUILD, USER)
    assert status is not None and status.wish is None
    # +3 de la familia en casa feliz.
    assert status.contract.performance == PERFORMANCE_START + 25 + 3 + WISH_PERFORMANCE
    stats = work_stats(outcome)
    assert stats.add["work_wishes"] == stats.add["work_wish_nota_90"] == 1
    assert stats.peak["work_wish_streak_max"] == 1
    # El mismo día no se cobra dos veces.
    clock.now += 600
    second = await work(service)
    assert second.wish is None
    assert second.gross == shift_pay(base, 100, ShiftKind.ORDINARY)
    # Al día siguiente, racha; si se salta un día, vuelve a 1.
    clock.now += DAY
    assert (await work(service)).wish_streak == 2
    clock.now += 2 * DAY
    assert (await work(service)).wish_streak == 1


async def test_un_turno_malo_no_cumple_el_deseo(
    tmp_path: Path, dice: Dice, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "wish_for", lambda *args: WISH_BY_KEY["nota_90"])
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    outcome = await work(service, perfect=False)
    assert outcome.wish is None
    status = await service.status(GUILD, USER)
    assert status is not None and status.wish is not None


async def test_el_historial_de_antes_de_las_ramas_se_migra(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            """
            CREATE TABLE work_history (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, job TEXT NOT NULL,
                level INTEGER NOT NULL, max_level INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, job)
            )
            """
        )
        connection.execute(
            "INSERT INTO work_history VALUES (?, ?, ?, 5, 5)", (GUILD, USER, "politica")
        )
    service = await make(tmp_path)
    assert (await service.history(GUILD, USER))["politica"] == Career(5, 5, "")
    # Quien era consejero (puesto 5) sigue en el 5: ahora es portavoz y elige rama.
    status = await service.hire(GUILD, USER, "politica")
    assert status.position.title == "Portavoz del grupo parlamentario"
    assert status.fork


async def test_una_rama_que_ya_no_existe_devuelve_al_final_del_tronco(tmp_path: Path) -> None:
    service = await make(tmp_path)
    await at_level(service, POLITICA.trunk_top + 2, "andorra")
    status = await service.status(GUILD, USER)
    assert status is not None
    assert (status.contract.level, status.contract.branch) == (POLITICA.trunk_top, "")
    assert status.fork


# -- Familia: tramos, regalos y favores -----------------------------------------------------


async def set_family(service: WorkService, family: float) -> None:
    status = await service.status(GUILD, USER)
    assert status is not None
    status.contract.family = family
    status.contract.family_at = status.contract.battery_at = status.now
    await service.repository.save_contract(GUILD, USER, status.contract)


async def test_cada_turno_cuesta_familia_y_lo_cuenta_partida_a_partida(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    await set_family(service, 50)
    outcome = await work(service)
    assert outcome.family_parts == [("turno", -6)]
    assert outcome.family_after == 44


async def test_con_la_familia_en_crisis_la_barra_no_sube(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    await set_family(service, 5)
    await work(service)
    status = await service.status(GUILD, USER)
    assert status is not None and status.contract.performance == PERFORMANCE_START


async def test_un_regalo_sube_la_familia_se_gasta_y_tiene_espera(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await service.hire(GUILD, USER, "obra")
    await set_family(service, 50)
    spent: list[str] = []

    async def consume() -> bool:
        spent.append("flores_mama")
        return True

    gift, family = await service.give_gift(GUILD, USER, "flores_mama", consume)
    assert gift.family == 10 and family == 60 and spent == ["flores_mama"]
    with pytest.raises(WorkError, match="todavía está con el último"):
        await service.give_gift(GUILD, USER, "flores_mama", consume)
    assert spent == ["flores_mama"]  # en espera no se gasta
    clock.now += gift.cooldown
    await service.give_gift(GUILD, USER, "flores_mama", consume)
    assert len(spent) == 2


async def test_un_regalo_que_ya_no_tienes_no_cuenta(tmp_path: Path) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")

    async def gone() -> bool:
        return False

    with pytest.raises(WorkError, match="no está en tu mochila"):
        await service.give_gift(GUILD, USER, "flores_mama", gone)
    status = await service.status(GUILD, USER)
    assert status is not None and status.contract.family_used == {}


async def test_el_tupper_recarga_y_cuesta_familia(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    await work(service)
    before = await service.status(GUILD, USER)
    assert before is not None
    result = await service.ask_favor(GUILD, USER, "tupper")
    status = await service.status(GUILD, USER)
    assert status is not None
    assert status.battery == pytest.approx(min(BATTERY_MAX, before.battery + 30))
    assert result.family == pytest.approx(before.family - 15)
    assert status.contract.coffees == 0  # no cuenta para los temblores
    with pytest.raises(WorkError, match="otra vez"):
        await service.ask_favor(GUILD, USER, "tupper")


async def test_sin_familia_no_hay_favores(tmp_path: Path) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    await set_family(service, 20)
    with pytest.raises(WorkError, match="no está para favores"):
        await service.ask_favor(GUILD, USER, "tupper")


async def test_tu_madre_te_cubre_el_saliente(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await set_level(service, "sanidad", 2)
    shift = await service.start_shift(GUILD, USER, guard=True)
    play(shift.game)
    await service.finish_shift(GUILD, USER, shift)
    status = await service.status(GUILD, USER)
    assert status is not None and status.off_duty
    await service.ask_favor(GUILD, USER, "cubre")
    status = await service.status(GUILD, USER)
    assert status is not None and not status.off_duty


async def test_tu_hermana_te_da_un_ordinario_mas(tmp_path: Path, dice: Dice) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    with pytest.raises(WorkError, match="Aún te quedan"):
        await service.ask_favor(GUILD, USER, "cambio")
    for _ in range(ORDINARY_SHIFTS):
        await work(service)
    status = await service.status(GUILD, USER)
    assert status is not None and status.next_kind is ShiftKind.EXTRA
    await set_family(service, 100)
    await service.ask_favor(GUILD, USER, "cambio")
    status = await service.status(GUILD, USER)
    assert status is not None and status.next_kind is ShiftKind.ORDINARY


async def test_lo_haces_por_los_ninos_y_el_turno_trae_mas_tiempo(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    plain = await service.start_shift(GUILD, USER)
    play(plain.game)
    await service.finish_shift(GUILD, USER, plain)
    await set_family(service, 100)
    await service.ask_favor(GUILD, USER, "ninos")
    shift = await service.start_shift(GUILD, USER)
    assert shift.perks.extra_time == pytest.approx(plain.perks.extra_time + 0.15)
    play(shift.game)
    await service.finish_shift(GUILD, USER, shift)
    status = await service.status(GUILD, USER)
    assert status is not None and not status.contract.motivated


async def test_el_bizum_de_mama_adelanta_un_sueldo_y_se_devuelve_entero(
    tmp_path: Path, dice: Dice
) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    base = JOB_BY_KEY["obra"].position(1).base_pay
    before = await service.economy.balance(GUILD, USER)
    state_before = ledger(tmp_path, STATE_ACCOUNT_ID)[1]
    result = await service.ask_favor(GUILD, USER, "bizum")
    assert result.amount == base and result.balance == before + base
    assert ledger(tmp_path, STATE_ACCOUNT_ID)[1] == state_before  # exento, como un Bizum
    with pytest.raises(WorkError, match="otra vez"):
        await service.ask_favor(GUILD, USER, "bizum")
    repaid = 0
    for _ in range(10):
        outcome = await work(service)
        assert outcome.mom_repaid <= round(outcome.net * 0.5) + 1
        repaid += outcome.mom_repaid
        status = await service.status(GUILD, USER)
        assert status is not None
        if not status.contract.mom_debt:
            break
    assert repaid == base
    total, balance = ledger(tmp_path, USER)
    assert total == balance
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (net,) = connection.execute(
            "SELECT SUM(delta) FROM economy_ledger WHERE reason LIKE ?", ("bizum:mama%",)
        ).fetchone()
    assert net == 0  # lo que se creó al pedirlo se destruye al devolverlo


async def test_la_excusa_familiar_te_libra_del_marron_pero_no_de_dimitir(tmp_path: Path) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    result = await service.dodge_event(GUILD, USER, "quedarse")
    assert result.family == pytest.approx(90)
    with pytest.raises(WorkError, match="otra vez"):
        await service.dodge_event(GUILD, USER, "quedarse")
    with pytest.raises(WorkError, match="ni tu madre"):
        await service.dodge_event(GUILD, USER, "dimision")


async def test_cambiar_de_curro_conserva_la_familia_y_lo_que_le_debes(tmp_path: Path) -> None:
    service = await make(tmp_path)
    await service.hire(GUILD, USER, "obra")
    await service.ask_favor(GUILD, USER, "bizum")
    status = await service.hire(GUILD, USER, "hosteleria")
    assert status.contract.mom_debt == JOB_BY_KEY["obra"].position(1).base_pay
    assert "bizum" in status.contract.family_used
    assert status.family == pytest.approx(80)
