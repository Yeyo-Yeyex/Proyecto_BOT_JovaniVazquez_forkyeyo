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
    INSPECTION_SURCHARGE,
    LEGAL_EXTRAS_PER_WEEK,
    ORDINARY_SHIFTS,
    SELF_EMPLOYED_FEE,
    ShiftKind,
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
    status.contract.level = 5  # consejero: sueldos enormes
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
    assert (await service.history(GUILD, USER))["obra"] == (3, 3)


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
    assert status is not None and status.contract.performance == 50 + 12


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
