"""Pruebas de los negocios de `pala` (`bot.services.negocio`) con la base de datos de verdad.

Cubren el dinero (que el libro cuadra y que el Estado recibe exactamente sus impuestos), las
franjas que comparten el negocio y la `pala`, el cupo de la demanda, las noches con su
desahucio, la cuota única de autónomo, la reducción de jornada y la excedencia.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from bot.repositories.business import BusinessRepository
from bot.repositories.economy import (
    HOUSING_DEPOSIT_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    EconomyRepository,
    LedgerEntry,
)
from bot.repositories.work import WorkRepository
from bot.services import pala
from bot.services.agenda import FRANJAS_DIA
from bot.services.business import EVICTION_NIGHTS, nightly_bill, opening_quote
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.business_games import COINS, TurnGame
from bot.services.business_view import cliente_state
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.housing_catalog import BARRIO_BY_KEY
from bot.services.loans import wage_garnish
from bot.services.negocio import BusinessError, BusinessService
from bot.services.pala import WorkError, WorkService
from bot.services.work import (
    BATTERY_MAX,
    BATTERY_MIN,
    EXCEDENCIA_COOLDOWN_DAYS,
    EXCEDENCIA_MIN_DAYS,
    EXCEDENCIA_SENIORITY_DAYS,
    FLAT_RATE_FEE,
    ORDINARY_SHIFTS,
    PERFORMANCE_START,
    SELF_EMPLOYED_FEE,
    SHIFT_COST,
    Wish,
)

GUILD = 1
USER = 10
# Martes 6 de octubre de 2026 a mediodía, hora canaria.
START = 1_791_284_400.0
DAY = 86_400


class Clock:
    """Reloj controlable."""

    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def sin_azar_de_la_pala(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin deseos, accidentes ni eventos en la `pala`: aquí se mira el negocio."""
    monkeypatch.setattr(pala, "wish_for", lambda *args: Wish("no", "Nada.", lambda f: False))
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


async def make(tmp_path: Path, clock: Clock) -> tuple[BusinessService, WorkService]:
    database = tmp_path / "bot.db"
    economy_repo = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    work_repo = WorkRepository(database)
    await work_repo.initialize()
    work = WorkService(work_repo, economy, clock=clock, rng=random.Random(1))
    repository = BusinessRepository(database)
    await repository.initialize()
    service = BusinessService(repository, economy, work, clock=clock, rng=random.Random(1))
    work.business = service
    return service, work


async def give(service: BusinessService, amount: int) -> None:
    await service.economy.grant(GUILD, USER, amount=amount, reason="prueba")


def wallet(tmp_path: Path, user_id: int) -> tuple[int, int]:
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


def business_taxes(tmp_path: Path) -> dict[str, int]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        rows = connection.execute(
            "SELECT kind, SUM(tax) FROM economy_business_tax GROUP BY kind"
        ).fetchall()
    return {str(kind): int(total) for kind, total in rows}


def activity_records(tmp_path: Path) -> list[tuple[str, int, int]]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        rows = connection.execute(
            "SELECT concept, gross, withheld FROM economy_tax_records "
            "WHERE kind = 'actividad' ORDER BY id"
        ).fetchall()
    return [(str(c), int(g), int(w)) for c, g, w in rows]


async def open_cafe(service: BusinessService) -> None:
    await give(service, 1_000_000)
    await service.open(
        GUILD, USER, sector="cafeteria", barrio="triana", name="Bar Pepe", palette=1, emblem=2
    )


def play(game: TurnGame, now: float) -> float:
    """Juega el turno entero sin fallar y devuelve la hora a la que acaba."""
    while not game.finished(now):
        client = game.current
        assert client is not None
        if game.sector.game == "comandas":
            for key, n in client.order.items():
                for _ in range(n):
                    game.press(f"p:{key}", now)
            game.press("serve", now)
        elif game.sector.game == "caja":
            if client.hidden:
                game.press("thief", now)
            due = game.due()
            for coin in COINS:
                while due >= coin:
                    game.press(f"c:{coin}", now)
                    due -= coin
            game.press("charge", now)
        else:
            game.press(f"d:{client.fault}", now)
            game.press("q:0", now)
        now += 2
    return now


# -- Montar ---------------------------------------------------------------------------


async def test_montar_cobra_tasa_acondicionamiento_y_fianza_y_el_estado_cobra_lo_suyo(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await give(service, 1_000_000)
    state_before = wallet(tmp_path, STATE_ACCOUNT_ID)[1]
    business, fee = await service.open(
        GUILD, USER, sector="cafeteria", barrio="triana", name=" Bar <@1> Pepe ", palette=1,
        emblem=2,
    )  # fmt: skip
    quote = opening_quote(SECTOR_BY_KEY["cafeteria"], BARRIO_BY_KEY["triana"])
    assert business.name == "Bar Pepe"
    assert fee == FLAT_RATE_FEE  # tarifa plana la primera semana
    total, balance = wallet(tmp_path, USER)
    assert total == balance == STARTING_BALANCE + 1_000_000 - quote.total - fee
    assert wallet(tmp_path, HOUSING_DEPOSIT_ACCOUNT_ID) == (quote.deposit, quote.deposit)
    state_gain = wallet(tmp_path, STATE_ACCOUNT_ID)[1] - state_before
    assert state_gain == quote.license + quote.setup_igic + fee
    assert business_taxes(tmp_path) == {"tasa": quote.license, "igic": quote.setup_igic}
    # La tasa es gasto de la actividad; el acondicionamiento se amortiza (fase 2) y la
    # fianza no es gasto.
    assert [(c, g) for c, g, _w in activity_records(tmp_path)] == [
        ("negocio:apertura", -quote.license),
    ]
    cost = quote.setup + quote.setup_igic
    assert business.assets == [{"key": "obra", "cost": cost, "rate": 0.1, "left": cost}]


async def test_sin_dinero_no_se_monta_ni_se_guarda_nada(tmp_path: Path) -> None:
    service, _work = await make(tmp_path, Clock())
    with pytest.raises(BusinessError):
        await service.open(
            GUILD, USER, sector="taller", barrio="mogan", name="Taller", palette=0, emblem=0
        )
    assert await service.business(GUILD, USER) is None
    assert await service.economy.balance(GUILD, USER) == STARTING_BALANCE


async def test_uno_por_persona_y_el_nombre_no_puede_quedar_vacio(tmp_path: Path) -> None:
    service, _work = await make(tmp_path, Clock())
    await give(service, 1_000_000)
    with pytest.raises(BusinessError):
        await service.open(
            GUILD, USER, sector="tienda", barrio="ofra", name=" <@1> ** ", palette=0, emblem=0
        )
    await service.open(
        GUILD, USER, sector="tienda", barrio="ofra", name="Súper", palette=0, emblem=0
    )
    with pytest.raises(BusinessError):
        await service.open(
            GUILD, USER, sector="tienda", barrio="ofra", name="Otro", palette=0, emblem=0
        )


# -- Comprar --------------------------------------------------------------------------


async def test_comprar_al_mayorista_paga_su_igic_y_es_gasto_de_la_actividad(
    tmp_path: Path,
) -> None:
    service, _work = await make(tmp_path, Clock())
    await open_cafe(service)
    taxes_before = business_taxes(tmp_path)
    business, base, tax, _balance = await service.buy(GUILD, USER, "bocadillo", 2)
    product = SECTOR_BY_KEY["cafeteria"].product("bocadillo")
    assert product is not None
    assert business.stock["bocadillo"] == 2 * product.pack
    assert base == 2 * product.pack * product.cost and tax == round(base * 0.07)
    assert business_taxes(tmp_path)["igic"] == taxes_before["igic"] + tax
    assert activity_records(tmp_path)[-1] == ("negocio:compras", -(base + tax), 0)
    total, balance = wallet(tmp_path, USER)
    assert total == balance


async def test_el_pan_y_la_leche_van_al_tipo_cero(tmp_path: Path) -> None:
    service, _work = await make(tmp_path, Clock())
    await give(service, 1_000_000)
    await service.open(
        GUILD, USER, sector="tienda", barrio="ofra", name="Súper", palette=0, emblem=0
    )
    _business, base, tax, _balance = await service.buy(GUILD, USER, "pan", 1)
    assert base > 0 and tax == 0


# -- Turnos ---------------------------------------------------------------------------


async def test_un_turno_cobra_las_ventas_con_retencion_sobre_el_margen(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await service.buy(GUILD, USER, product.key, 2)
    state_before = wallet(tmp_path, STATE_ACCOUNT_ID)[1]
    turn = await service.start_turn(GUILD, USER)
    clock.now = play(turn.game, clock.now)
    result = await service.finish_turn(GUILD, USER, turn)
    summary = result.summary
    assert summary.sales > 0 and summary.grade in ("S", "A")
    assert wallet(tmp_path, STATE_ACCOUNT_ID)[1] - state_before == result.withheld
    concept, gross, withheld = activity_records(tmp_path)[-1]
    assert (concept, gross, withheld) == ("negocio:ventas", summary.sales, result.withheld)
    assert result.withheld <= summary.margin
    assert result.business.reputation > 50
    for key, units in summary.units.items():
        assert (
            result.business.stock[key] == 2 * SECTOR_BY_KEY["cafeteria"].product(key).pack - units
        )  # type: ignore[union-attr]
    total, balance = wallet(tmp_path, USER)
    assert total == balance == result.balance


async def test_el_imv_y_el_banco_ven_lo_que_deja_el_negocio(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await service.buy(GUILD, USER, product.key, 2)
    # Montar y comprar son gasto de la actividad, pero no restan: solo cuenta la cuota.
    assert await service.economy.work_week_net(GUILD, USER) == -FLAT_RATE_FEE
    clock.now += 8 * DAY  # la apertura, las compras y la cuota quedan fuera de la semana
    turn = await service.start_turn(GUILD, USER)
    clock.now = play(turn.game, clock.now)
    result = await service.finish_turn(GUILD, USER, turn)
    net = result.summary.sales - result.withheld
    assert net > 0
    assert await service.economy.work_week_net(GUILD, USER) == net
    # El banco mira 30 días: ahí la apertura aún pesa más que las ventas.
    assert await service.economy.work_net_days(GUILD, USER, 30) == -FLAT_RATE_FEE


async def test_el_turno_sorteado_antes_de_abrir_es_el_que_sale(tmp_path: Path) -> None:
    service, _work = await make(tmp_path, Clock())
    await open_cafe(service)
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await service.buy(GUILD, USER, product.key, 2)
    status = await service.status(GUILD, USER)
    assert status is not None
    plan = service.plan()
    ahead = service.preview(status, plan)
    assert ahead is not None
    turn = await service.start_turn(GUILD, USER, plan)
    assert [c.seed for c in turn.game.clients] == [c.seed for c in ahead.clients]
    assert cliente_state(turn.business, turn.game) == cliente_state(status.business, ahead)


async def test_sin_curro_el_negocio_cansa_igual_y_la_bateria_pasa_al_contrato(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, work = await make(tmp_path, clock)
    await open_cafe(service)
    await service.buy(GUILD, USER, "cafe", 5)
    turn = await service.start_turn(GUILD, USER)
    assert turn.battery_before == BATTERY_MAX
    assert await work.battery(GUILD, USER) == BATTERY_MAX - SHIFT_COST
    turn.game.finish(clock.now)
    await service.finish_turn(GUILD, USER, turn)
    await work.repository.save_person_battery(GUILD, USER, BATTERY_MIN, clock.now)
    with pytest.raises(BusinessError, match="reventado"):
        await service.start_turn(GUILD, USER)
    # Al firmar el primer curro no se estrena batería: sigue reventado.
    await work.hire(GUILD, USER, "obra")
    assert await work.battery(GUILD, USER) == pytest.approx(BATTERY_MIN)


async def test_sin_stock_no_se_abre(tmp_path: Path) -> None:
    service, _work = await make(tmp_path, Clock())
    await open_cafe(service)
    with pytest.raises(BusinessError, match="almacén"):
        await service.start_turn(GUILD, USER)


async def test_sin_curro_hay_ocho_franjas_para_el_negocio(tmp_path: Path) -> None:
    clock = Clock()
    service, work = await make(tmp_path, clock)
    await open_cafe(service)
    await service.buy(GUILD, USER, "cafe", 20)
    for _ in range(FRANJAS_DIA):
        turn = await service.start_turn(GUILD, USER)
        turn.game.finish(clock.now)
        await service.finish_turn(GUILD, USER, turn)
        clock.now += 60
    assert (await work.agenda(GUILD, USER)).free == 0
    with pytest.raises(BusinessError, match="franjas"):
        await service.start_turn(GUILD, USER)


async def test_la_jornada_reserva_sus_franjas_y_las_extras_se_las_quitan_al_negocio(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, work = await make(tmp_path, clock)
    await work.hire(GUILD, USER, "obra")
    agenda = await work.agenda(GUILD, USER)
    assert agenda.free == FRANJAS_DIA - ORDINARY_SHIFTS
    # Una jornada entera y dos horas extra: quedan dos franjas.
    for _ in range(ORDINARY_SHIFTS + 2):
        shift = await work.start_shift(GUILD, USER)
        shift.game.finish(clock.now)
        await work.finish_shift(GUILD, USER, shift)
        clock.now += 3_600  # una hora entre turnos: la batería se recarga un poco
    assert (await work.agenda(GUILD, USER)).free == FRANJAS_DIA - ORDINARY_SHIFTS - 2
    await open_cafe(service)
    await service.buy(GUILD, USER, "cafe", 20)
    for _ in range(2):
        turn = await service.start_turn(GUILD, USER)
        turn.game.finish(clock.now)
        await service.finish_turn(GUILD, USER, turn)
        clock.now += 3_600
    with pytest.raises(BusinessError, match="franjas"):
        await service.start_turn(GUILD, USER)
    # Y sin franjas libres, la pala tampoco deja hacer más horas extra.
    with pytest.raises(WorkError, match="franjas"):
        await work.start_shift(GUILD, USER, black_ok=True)


async def test_el_turno_de_negocio_gasta_la_bateria_de_quien_tiene_curro(tmp_path: Path) -> None:
    clock = Clock()
    service, work = await make(tmp_path, clock)
    await work.hire(GUILD, USER, "obra")
    await open_cafe(service)
    await service.buy(GUILD, USER, "cafe", 5)
    before = await work.battery(GUILD, USER)
    turn = await service.start_turn(GUILD, USER)
    assert turn.battery_before == before
    assert (await work.battery(GUILD, USER)) < before  # type: ignore[operator]


async def test_el_cupo_del_barrio_se_agota_entre_todos(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    await service.buy(GUILD, USER, "cafe", 20)
    business = await service.business(GUILD, USER)
    assert business is not None
    today = pala.local_day(clock.now)
    pool = service.repository
    await pool.begin_turn(GUILD, business, today, 10_000)  # otro negocio se lo ha llevado todo
    with pytest.raises(BusinessError, match="barrio"):
        await service.start_turn(GUILD, USER)


# -- Las noches -----------------------------------------------------------------------


async def test_cada_noche_se_paga_el_local_con_su_igic_y_la_retencion_del_casero(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    business = await service.business(GUILD, USER)
    assert business is not None
    bill = nightly_bill(business)
    state_before = wallet(tmp_path, STATE_ACCOUNT_ID)[1]
    balance_before = wallet(tmp_path, USER)[1]
    clock.now += DAY
    runs = await service.close_nights(GUILD)
    assert [(r.due, r.unpaid) for r in runs] == [(bill.total, 0)]
    taxes = business_taxes(tmp_path)
    assert taxes["retencion"] == bill.withholding
    fee = SELF_EMPLOYED_FEE if clock.now - business.opened_at >= 7 * DAY else 0
    assert wallet(tmp_path, STATE_ACCOUNT_ID)[1] - state_before == bill.taxes + fee
    assert balance_before - wallet(tmp_path, USER)[1] == bill.total + fee
    # Cerrar otra vez el mismo día no cobra nada más.
    assert await service.close_nights(GUILD) == []


async def test_sin_dinero_el_local_se_debe_y_a_la_tercera_noche_te_desahucian(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    balance = wallet(tmp_path, USER)[1]
    await service.economy.repository.apply(GUILD, USER, [pala_entry(-balance)])
    business = await service.business(GUILD, USER)
    assert business is not None
    deposit = business.deposit
    clock.now += EVICTION_NIGHTS * DAY
    runs = await service.close_nights(GUILD)
    assert [r.evicted for r in runs] == [False] * (EVICTION_NIGHTS - 1) + [True]
    assert runs[-1].unpaid > 0
    assert await service.business(GUILD, USER) is None
    # La deuda pasa de la fianza: el casero se la queda entera y no vuelve nada.
    assert wallet(tmp_path, HOUSING_DEPOSIT_ACCOUNT_ID)[1] == 0
    assert wallet(tmp_path, USER)[1] == 0
    closed = await service.closed(GUILD, USER)
    assert [row["reason"] for row in closed] == ["desahucio"]
    assert deposit > 0


def pala_entry(delta: int) -> LedgerEntry:
    return LedgerEntry(delta, "prueba")


async def test_las_ventas_del_turno_pagan_el_embargo_de_la_deuda_con_el_banco(
    tmp_path: Path,
) -> None:
    """El art. 607 LEC alcanza a lo que gana un autónomo (607.6) y se acumula (607.3)."""
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await service.buy(GUILD, USER, product.key, 2)
    debt = 1_000_000
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS housing_bank_debts "
            "(guild_id INTEGER, user_id INTEGER, amount INTEGER, PRIMARY KEY (guild_id, user_id))"
        )
        connection.execute("INSERT INTO housing_bank_debts VALUES (?, ?, ?)", (GUILD, USER, debt))
    turn = await service.start_turn(GUILD, USER)
    clock.now = play(turn.game, clock.now)
    result = await service.finish_turn(GUILD, USER, turn)
    net = result.summary.sales - result.withheld - result.garnished
    assert result.bank_garnished == wage_garnish(0, net) > 0
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (left,) = connection.execute("SELECT amount FROM housing_bank_debts").fetchone()
    assert left == debt - result.bank_garnished
    total, balance = wallet(tmp_path, USER)
    assert total == balance == result.balance


async def test_cerrar_devuelve_la_fianza(tmp_path: Path) -> None:
    service, _work = await make(tmp_path, Clock())
    await open_cafe(service)
    business = await service.business(GUILD, USER)
    assert business is not None
    before = wallet(tmp_path, USER)[1]
    closing = await service.close(GUILD, USER)
    assert closing.refund == business.deposit and closing.forfeit == 0
    assert wallet(tmp_path, USER)[1] == before + business.deposit
    assert wallet(tmp_path, HOUSING_DEPOSIT_ACCOUNT_ID)[1] == 0
    total, balance = wallet(tmp_path, USER)
    assert total == balance


async def test_una_sola_cuota_de_autonomo_aunque_tambien_lo_seas_en_la_pala(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, work = await make(tmp_path, clock)
    await open_cafe(service)  # paga la tarifa plana de esta semana
    await work.hire(GUILD, USER, "reparto")  # el rider es falso autónomo
    shift = await work.start_shift(GUILD, USER)
    shift.game.finish(clock.now)
    outcome = await work.finish_shift(GUILD, USER, shift)
    assert outcome.fee == 0
    assert await work.dues(GUILD, USER) == (0, 0)


# -- Reducción de jornada y excedencia ------------------------------------------------


async def test_la_reduccion_entra_manana_y_quita_las_horas_extra(tmp_path: Path) -> None:
    clock = Clock()
    _service, work = await make(tmp_path, clock)
    await work.hire(GUILD, USER, "obra")
    status = await work.set_jornada(GUILD, USER, 2)
    assert status.contract.jornada == ORDINARY_SHIFTS and status.contract.jornada_next == 2
    clock.now += DAY
    assert (await work.agenda(GUILD, USER)).free == FRANJAS_DIA - 2
    for _ in range(2):
        shift = await work.start_shift(GUILD, USER)
        shift.game.finish(clock.now)
        await work.finish_shift(GUILD, USER, shift)
    with pytest.raises(WorkError, match="reducción"):
        await work.start_shift(GUILD, USER, black_ok=True)


async def test_la_excedencia_pide_un_ano_dura_cuatro_meses_y_vuelve_con_la_barra_a_medias(
    tmp_path: Path,
) -> None:
    clock = Clock()
    _service, work = await make(tmp_path, clock)
    await work.hire(GUILD, USER, "obra")
    with pytest.raises(WorkError, match="año"):
        await work.request_excedencia(GUILD, USER)
    clock.now += EXCEDENCIA_SENIORITY_DAYS * DAY
    status = await work.request_excedencia(GUILD, USER)
    assert status.contract.on_excedencia
    assert (await work.agenda(GUILD, USER)).free == FRANJAS_DIA
    with pytest.raises(WorkError, match="excedencia"):
        await work.start_shift(GUILD, USER)
    with pytest.raises(WorkError, match="meses"):
        await work.end_excedencia(GUILD, USER)
    clock.now += EXCEDENCIA_MIN_DAYS * DAY
    status = await work.end_excedencia(GUILD, USER)
    assert not status.contract.on_excedencia
    assert status.contract.performance == PERFORMANCE_START
    with pytest.raises(WorkError, match="años"):
        await work.request_excedencia(GUILD, USER)
    clock.now += EXCEDENCIA_COOLDOWN_DAYS * DAY
    assert (await work.request_excedencia(GUILD, USER)).contract.on_excedencia
