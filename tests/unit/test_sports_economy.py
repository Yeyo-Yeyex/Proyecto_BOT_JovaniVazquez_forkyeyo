"""Pruebas del dinero de las apuestas deportivas: depósito, resolución, anulación e IRPF.

Lo apostado sale del monedero al depósito (`SPORTS_ACCOUNT_ID`) sin tocar el día del
casino. Al resolver vuelve del depósito y apuesta y premio entran a la vez en el día del
casino de hoy, con su retención del juego. Anular devuelve lo apostado sin tocar el casino.
Cada flujo prueba que el libro cuadra con el saldo, que el Estado recibe lo retenido y que
las devoluciones de Hacienda salen de su cuenta.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bot.repositories.economy import (
    SPORTS_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.services.economy import (
    STARTING_BALANCE,
    STATE_ACCOUNT_ID,
    EconomyService,
    SportsBet,
    SportsBetTakenError,
)
from bot.services.levels import local_day
from bot.services.sports import GAME, MAX_STAKE, MIN_STAKE, payout
from bot.services.taxes import GAMBLING_EXEMPT, gambling_day_tax

GUILD = 1
OTHER_GUILD = 2
USER = 10
OTHER = 11
CHANNEL = 99
KICKOFF = 1_800_003_600.0
ODDS = 250
STAKE = MIN_STAKE * 10
BIG = 10 * GAMBLING_EXEMPT


class FakeClock:
    """Reloj controlable."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def make_service(tmp_path: Path, clock: FakeClock | None = None) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=clock or FakeClock())


def make_bet(
    *,
    guild: int = GUILD,
    user: int = USER,
    event_id: str = "ev1",
    stake: int = STAKE,
    odds: int = ODDS,
    pick: str = "1",
    commence: float = KICKOFF,
) -> SportsBet:
    return SportsBet(
        id=0,
        guild_id=guild,
        user_id=user,
        channel_id=CHANNEL,
        event_id=event_id,
        sport="soccer_spain_la_liga",
        home="Real Madrid",
        away="FC Barcelona",
        commence=commence,
        pick=pick,
        odds=odds,
        stake=stake,
        created_at=1_800_000_000.0,
    )


async def rich_service(tmp_path: Path, clock: FakeClock | None = None) -> EconomyService:
    """Un servicio con `USER` y `OTHER` con saldo de sobra para apuestas grandes."""
    service = await make_service(tmp_path, clock)
    await service.grant(GUILD, USER, amount=100 * BIG, reason="test:saldo")
    await service.grant(GUILD, OTHER, amount=100 * BIG, reason="test:saldo")
    return service


def query(tmp_path: Path, sql: str, *params: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(sql, params).fetchall()


def wallet(tmp_path: Path, user_id: int, guild: int = GUILD) -> int:
    rows = query(
        tmp_path,
        "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
        guild,
        user_id,
    )
    return int(rows[0][0]) if rows else 0


def deposit(tmp_path: Path) -> int:
    return wallet(tmp_path, SPORTS_ACCOUNT_ID)


def state(tmp_path: Path) -> int:
    return wallet(tmp_path, STATE_ACCOUNT_ID)


def casino_days(tmp_path: Path, user_id: int = USER) -> list[tuple[str, int, int]]:
    return [
        (str(day), int(net), int(withheld))
        for day, net, withheld in query(
            tmp_path,
            "SELECT day, net, withheld FROM economy_gambling_days "
            "WHERE guild_id = ? AND user_id = ? ORDER BY day",
            GUILD,
            user_id,
        )
    ]


def assert_ledger_balances(tmp_path: Path) -> None:
    """Cada monedero (miembros, depósito y Estado) vale exactamente lo que suma su libro."""
    wallets = query(tmp_path, "SELECT guild_id, user_id, balance FROM economy_wallets")
    assert wallets
    for guild_id, user_id, balance in wallets:
        (total,) = query(
            tmp_path,
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            guild_id,
            user_id,
        )[0]
        assert total == balance, (guild_id, user_id)


def today(clock: FakeClock) -> str:
    return local_day(clock.now).isoformat()


# -- Apostar ----------------------------------------------------------------------------


async def test_apostar_cobra_y_llena_el_deposito_sin_tocar_el_casino(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    saved, balance = await service.sports_bet(make_bet(), game=GAME)

    assert saved.id > 0
    assert saved.status == "open"
    assert (saved.stake, saved.odds, saved.pick) == (STAKE, ODDS, "1")
    assert balance == STARTING_BALANCE - STAKE
    assert wallet(tmp_path, USER) == balance
    assert deposit(tmp_path) == STAKE
    assert casino_days(tmp_path) == []
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_apostar_deja_los_motivos_deposito_en_el_libro(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    await service.sports_bet(make_bet(), game=GAME)

    reasons = query(
        tmp_path,
        "SELECT user_id, delta, reason FROM economy_ledger WHERE reason = ? ORDER BY user_id",
        f"{GAME}:deposito",
    )
    assert reasons == [
        (SPORTS_ACCOUNT_ID, STAKE, f"{GAME}:deposito"),
        (USER, -STAKE, f"{GAME}:deposito"),
    ]


async def test_una_segunda_apuesta_al_mismo_partido_no_cobra_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.sports_bet(make_bet(pick="1"), game=GAME)
    before = (wallet(tmp_path, USER), deposit(tmp_path))

    with pytest.raises(SportsBetTakenError):
        await service.sports_bet(make_bet(pick="2", stake=STAKE * 2), game=GAME)

    assert (wallet(tmp_path, USER), deposit(tmp_path)) == before
    assert len(await service.open_sports_bets()) == 1
    assert_ledger_balances(tmp_path)


async def test_otro_partido_otro_miembro_u_otro_servidor_si_pueden_apostar(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    await service.sports_bet(make_bet(), game=GAME)

    await service.sports_bet(make_bet(event_id="ev2"), game=GAME)
    await service.sports_bet(make_bet(user=OTHER), game=GAME)
    await service.sports_bet(make_bet(guild=OTHER_GUILD), game=GAME)

    assert len(await service.open_sports_bets()) == 4
    assert deposit(tmp_path) == 3 * STAKE
    assert wallet(tmp_path, SPORTS_ACCOUNT_ID, OTHER_GUILD) == STAKE
    assert_ledger_balances(tmp_path)


async def test_apostar_sin_saldo_no_mueve_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(InsufficientFundsError):
        await service.sports_bet(make_bet(stake=STARTING_BALANCE + 1), game=GAME)

    assert await service.balance(GUILD, USER) == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert await service.open_sports_bets() == []
    assert casino_days(tmp_path) == []
    assert_ledger_balances(tmp_path)


async def test_se_puede_apostar_todo_el_saldo(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    _, balance = await service.sports_bet(make_bet(stake=STARTING_BALANCE), game=GAME)

    assert balance == 0
    assert deposit(tmp_path) == STARTING_BALANCE


@pytest.mark.parametrize(
    ("stake", "odds"), [(0, ODDS), (-5, ODDS), (STAKE, 100), (STAKE, 0), (STAKE, 50)]
)
async def test_una_apuesta_invalida_se_rechaza_sin_cobrar(
    tmp_path: Path, stake: int, odds: int
) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(ValueError):
        await service.sports_bet(make_bet(stake=stake, odds=odds), game=GAME)

    assert await service.balance(GUILD, USER) == STARTING_BALANCE
    assert await service.open_sports_bets() == []


# -- Ganar y perder ---------------------------------------------------------------------


async def test_ganar_paga_la_cuota_y_el_dia_ve_la_ganancia_neta(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)
    prize = payout(STAKE, ODDS)
    # La ganancia neta cabe en la exención, aunque el premio entero no: así se ve que el
    # IRPF se calcula con premio - apuesta y no con el premio.
    assert prize - STAKE < GAMBLING_EXEMPT

    settled = await service.settle_sports_bet(GUILD, saved.id, payout=prize, game=GAME)

    assert settled is not None
    bet, result = settled
    assert (bet.status, bet.payout, bet.settled_at) == ("won", prize, clock.now)
    assert result.day_net == prize - STAKE
    assert result.tax_delta == 0
    assert result.balance == STARTING_BALANCE + prize - STAKE
    assert wallet(tmp_path, USER) == result.balance
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == [(today(clock), prize - STAKE, 0)]
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_el_dia_ve_la_ganancia_neta_y_no_el_premio_aunque_este_pase_de_la_exencion(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    await service.grant(GUILD, USER, amount=2 * GAMBLING_EXEMPT, reason="test:saldo")
    stake = GAMBLING_EXEMPT
    saved, _ = await service.sports_bet(make_bet(stake=stake, odds=150), game=GAME)
    prize = payout(stake, 150)
    assert prize > GAMBLING_EXEMPT >= prize - stake

    settled = await service.settle_sports_bet(GUILD, saved.id, payout=prize, game=GAME)

    assert settled is not None
    assert settled[1].day_net == prize - stake
    assert settled[1].tax_delta == 0
    assert casino_days(tmp_path) == [(today(clock), prize - stake, 0)]


async def test_perder_deja_el_dia_en_menos_la_apuesta_y_el_deposito_vacio(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)

    settled = await service.settle_sports_bet(GUILD, saved.id, payout=0, game=GAME)

    assert settled is not None
    bet, result = settled
    assert (bet.status, bet.payout) == ("lost", 0)
    assert result.day_net == -STAKE
    assert result.tax_delta == 0
    assert result.balance == STARTING_BALANCE - STAKE
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == [(today(clock), -STAKE, 0)]
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_el_libro_lleva_apuesta_y_premio_del_juego_al_resolver(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)
    prize = payout(STAKE, ODDS)

    await service.settle_sports_bet(GUILD, saved.id, payout=prize, game=GAME)

    rows = query(
        tmp_path,
        "SELECT user_id, delta, reason FROM economy_ledger WHERE reason IN (?, ?) ORDER BY id",
        f"{GAME}:apuesta",
        f"{GAME}:premio",
    )
    assert rows == [(USER, -STAKE, f"{GAME}:apuesta"), (USER, prize, f"{GAME}:premio")]


async def test_el_dia_del_casino_es_el_de_cuando_se_resuelve_y_no_el_de_apostar(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)
    bet_day = today(clock)
    clock.now += 3 * 86_400

    await service.settle_sports_bet(GUILD, saved.id, payout=0, game=GAME)

    assert casino_days(tmp_path) == [(today(clock), -STAKE, 0)]
    assert today(clock) != bet_day


# -- Resolver o anular dos veces --------------------------------------------------------


async def test_resolver_dos_veces_no_mueve_nada_la_segunda(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)
    prize = payout(STAKE, ODDS)
    await service.settle_sports_bet(GUILD, saved.id, payout=prize, game=GAME)
    after = (wallet(tmp_path, USER), deposit(tmp_path), casino_days(tmp_path))
    ledger_rows = query(tmp_path, "SELECT COUNT(*) FROM economy_ledger")

    again = await service.settle_sports_bet(GUILD, saved.id, payout=prize, game=GAME)
    as_lost = await service.settle_sports_bet(GUILD, saved.id, payout=0, game=GAME)

    assert again is None
    assert as_lost is None
    assert (wallet(tmp_path, USER), deposit(tmp_path), casino_days(tmp_path)) == after
    assert query(tmp_path, "SELECT COUNT(*) FROM economy_ledger") == ledger_rows


async def test_anular_dos_veces_no_mueve_nada_la_segunda(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)
    await service.void_sports_bet(GUILD, saved.id, game=GAME)
    ledger_rows = query(tmp_path, "SELECT COUNT(*) FROM economy_ledger")

    again = await service.void_sports_bet(GUILD, saved.id, game=GAME)

    assert again is None
    assert wallet(tmp_path, USER) == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert query(tmp_path, "SELECT COUNT(*) FROM economy_ledger") == ledger_rows


async def test_una_apuesta_resuelta_no_se_puede_anular_ni_una_anulada_resolver(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    won, _ = await service.sports_bet(make_bet(event_id="a"), game=GAME)
    voided, _ = await service.sports_bet(make_bet(event_id="b"), game=GAME)
    await service.settle_sports_bet(GUILD, won.id, payout=payout(STAKE, ODDS), game=GAME)
    await service.void_sports_bet(GUILD, voided.id, game=GAME)
    before = (wallet(tmp_path, USER), deposit(tmp_path), casino_days(tmp_path))

    assert await service.void_sports_bet(GUILD, won.id, game=GAME) is None
    assert await service.settle_sports_bet(GUILD, voided.id, payout=500, game=GAME) is None

    assert (wallet(tmp_path, USER), deposit(tmp_path), casino_days(tmp_path)) == before
    assert_ledger_balances(tmp_path)


async def test_resolver_una_apuesta_de_otro_servidor_o_inexistente_no_hace_nada(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)

    assert await service.settle_sports_bet(OTHER_GUILD, saved.id, payout=0, game=GAME) is None
    assert await service.void_sports_bet(OTHER_GUILD, saved.id, game=GAME) is None
    assert await service.settle_sports_bet(GUILD, saved.id + 99, payout=0, game=GAME) is None

    assert deposit(tmp_path) == STAKE
    assert len(await service.open_sports_bets()) == 1


async def test_un_premio_negativo_se_rechaza_sin_mover_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)

    with pytest.raises(ValueError):
        await service.settle_sports_bet(GUILD, saved.id, payout=-1, game=GAME)

    assert deposit(tmp_path) == STAKE
    assert len(await service.open_sports_bets()) == 1


# -- Anular -----------------------------------------------------------------------------


async def test_anular_devuelve_lo_apostado_y_no_toca_el_casino(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.sports_bet(make_bet(), game=GAME)

    voided = await service.void_sports_bet(GUILD, saved.id, game=GAME)

    assert voided is not None
    bet, balance = voided
    assert (bet.status, bet.payout, bet.settled_at) == ("void", STAKE, clock.now)
    assert balance == STARTING_BALANCE
    assert wallet(tmp_path, USER) == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == []
    assert state(tmp_path) == 0
    assert (
        query(tmp_path, "SELECT COUNT(*) FROM economy_gambling_days WHERE guild_id = ?", GUILD)[0][
            0
        ]
        == 0
    )
    assert_ledger_balances(tmp_path)


async def test_anular_no_cuenta_para_el_irpf_aunque_ese_dia_se_haya_ganado(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    big, _ = await service.sports_bet(make_bet(event_id="grande", stake=BIG), game=GAME)
    await service.settle_sports_bet(GUILD, big.id, payout=payout(BIG, 400), game=GAME)
    open_bet, _ = await service.sports_bet(make_bet(event_id="aplazado"), game=GAME)
    days_before = casino_days(tmp_path)
    state_before = state(tmp_path)

    await service.void_sports_bet(GUILD, open_bet.id, game=GAME)

    assert casino_days(tmp_path) == days_before
    assert state(tmp_path) == state_before
    assert_ledger_balances(tmp_path)


# -- El libro y el Estado ---------------------------------------------------------------


async def test_el_libro_cuadra_con_cada_monedero_tras_ganar_perder_y_anular(
    tmp_path: Path,
) -> None:
    service = await rich_service(tmp_path)
    won, _ = await service.sports_bet(make_bet(event_id="a", stake=BIG), game=GAME)
    lost, _ = await service.sports_bet(make_bet(event_id="b", user=OTHER, stake=BIG), game=GAME)
    voided, _ = await service.sports_bet(make_bet(event_id="c", stake=STAKE), game=GAME)
    still_open, _ = await service.sports_bet(make_bet(event_id="d", user=OTHER), game=GAME)
    other_guild, _ = await service.sports_bet(make_bet(guild=OTHER_GUILD, event_id="a"), game=GAME)
    assert_ledger_balances(tmp_path)

    await service.settle_sports_bet(GUILD, won.id, payout=payout(BIG, 300), game=GAME)
    await service.settle_sports_bet(GUILD, lost.id, payout=0, game=GAME)
    await service.void_sports_bet(GUILD, voided.id, game=GAME)

    assert_ledger_balances(tmp_path)
    assert deposit(tmp_path) == still_open.stake
    assert wallet(tmp_path, SPORTS_ACCOUNT_ID, OTHER_GUILD) == other_guild.stake


async def test_el_estado_recibe_exactamente_lo_retenido_en_una_ganada_grande(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    saved, _ = await service.sports_bet(make_bet(stake=BIG), game=GAME)
    prize = payout(BIG, 400)
    net = prize - BIG
    assert net > GAMBLING_EXEMPT
    balance_before = wallet(tmp_path, USER)  # con lo apostado ya en el depósito

    settled = await service.settle_sports_bet(GUILD, saved.id, payout=prize, game=GAME)

    assert settled is not None
    result = settled[1]
    assert result.day_net == net
    assert result.tax_delta == gambling_day_tax(net) > 0
    assert result.day_withheld == result.tax_delta
    assert state(tmp_path) == result.tax_delta
    assert wallet(tmp_path, USER) == balance_before + prize - result.tax_delta
    assert casino_days(tmp_path) == [(today(clock), net, result.tax_delta)]
    treasury = await service.treasury(GUILD, since=0)
    assert treasury.balance == treasury.collected_total == result.tax_delta
    assert_ledger_balances(tmp_path)


async def test_el_estado_solo_recibe_en_el_ajuste_y_no_al_apostar(tmp_path: Path) -> None:
    service = await rich_service(tmp_path)

    await service.sports_bet(make_bet(stake=BIG), game=GAME)

    assert state(tmp_path) == 0


async def test_ganar_grande_y_perder_otra_apuesta_el_mismo_dia_devuelve_desde_el_estado(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    win, _ = await service.sports_bet(make_bet(event_id="gana", stake=BIG), game=GAME)
    lose, _ = await service.sports_bet(make_bet(event_id="pierde", stake=3 * BIG), game=GAME)
    won = await service.settle_sports_bet(GUILD, win.id, payout=payout(BIG, 400), game=GAME)
    assert won is not None
    withheld = won[1].tax_delta
    assert withheld > 0
    assert state(tmp_path) == withheld

    lost = await service.settle_sports_bet(GUILD, lose.id, payout=0, game=GAME)

    assert lost is not None
    result = lost[1]
    net = payout(BIG, 400) - BIG - 3 * BIG
    assert result.day_net == net
    assert result.tax_delta < 0
    assert (
        result.day_withheld == withheld + result.tax_delta == max(0, gambling_day_tax(max(net, 0)))
    )
    # Lo devuelto sale de la cuenta del Estado, que se queda con lo que sigue retenido.
    assert state(tmp_path) == result.day_withheld
    refunds = query(
        tmp_path,
        "SELECT user_id, delta FROM economy_ledger WHERE reason = ? ORDER BY id",
        "devolucion:irpf:juego",
    )
    assert refunds == [(STATE_ACCOUNT_ID, result.tax_delta), (USER, -result.tax_delta)]
    assert casino_days(tmp_path) == [(today(clock), net, result.day_withheld)]
    assert_ledger_balances(tmp_path)


async def test_la_devolucion_nunca_pasa_de_lo_que_hay_en_la_cuenta_del_estado(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    win, _ = await service.sports_bet(make_bet(event_id="gana", stake=BIG), game=GAME)
    lose, _ = await service.sports_bet(make_bet(event_id="pierde", stake=5 * BIG), game=GAME)
    await service.settle_sports_bet(GUILD, win.id, payout=payout(BIG, 400), game=GAME)
    # El Estado gasta lo recaudado antes de la devolución.
    await service.repository.apply(
        GUILD, STATE_ACCOUNT_ID, [LedgerEntry(-state(tmp_path), "test:gasto")]
    )

    settled = await service.settle_sports_bet(GUILD, lose.id, payout=0, game=GAME)

    assert settled is not None
    assert settled[1].tax_delta == 0
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_dos_apuestas_ganadas_el_mismo_dia_retienen_lo_mismo_que_una_sola(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    first, _ = await service.sports_bet(make_bet(event_id="a", stake=BIG), game=GAME)
    second, _ = await service.sports_bet(make_bet(event_id="b", stake=BIG), game=GAME)
    prize = payout(BIG, 400)

    one = await service.settle_sports_bet(GUILD, first.id, payout=prize, game=GAME)
    two = await service.settle_sports_bet(GUILD, second.id, payout=prize, game=GAME)

    assert one is not None
    assert two is not None
    total_net = 2 * (prize - BIG)
    assert two[1].day_net == total_net
    assert two[1].day_withheld == gambling_day_tax(total_net)
    assert state(tmp_path) == gambling_day_tax(total_net)
    assert_ledger_balances(tmp_path)


# -- Listados ---------------------------------------------------------------------------


async def test_las_apuestas_de_un_miembro_salen_las_abiertas_primero(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    old, _ = await service.sports_bet(make_bet(event_id="vieja", commence=KICKOFF), game=GAME)
    recent, _ = await service.sports_bet(
        make_bet(event_id="reciente", commence=KICKOFF + 5), game=GAME
    )
    clock.now += 1_000
    await service.settle_sports_bet(GUILD, old.id, payout=0, game=GAME)
    clock.now += 1_000
    await service.settle_sports_bet(GUILD, recent.id, payout=500, game=GAME)
    soon, _ = await service.sports_bet(
        make_bet(event_id="pronto", commence=KICKOFF - 500), game=GAME
    )
    later, _ = await service.sports_bet(
        make_bet(event_id="tarde", commence=KICKOFF + 900), game=GAME
    )
    await service.sports_bet(make_bet(event_id="ajena", user=OTHER), game=GAME)

    bets = await service.sports_bets(GUILD, USER, limit=10)

    # Abiertas primero (la que empieza más tarde arriba) y luego las resueltas, la última antes.
    assert [b.event_id for b in bets] == ["tarde", "pronto", "reciente", "vieja"]
    assert [b.status for b in bets] == ["open", "open", "won", "lost"]
    assert soon.id != later.id


async def test_las_apuestas_de_un_miembro_respetan_el_limite(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    for i in range(5):
        await service.sports_bet(make_bet(event_id=f"e{i}", stake=MIN_STAKE), game=GAME)

    assert len(await service.sports_bets(GUILD, USER, limit=3)) == 3
    assert await service.sports_bets(GUILD, OTHER, limit=3) == []
    assert await service.sports_bets(OTHER_GUILD, USER, limit=3) == []


async def test_las_apuestas_abiertas_son_de_todos_los_servidores_y_por_comienzo(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    late, _ = await service.sports_bet(
        make_bet(event_id="tarde", commence=KICKOFF + 500), game=GAME
    )
    early, _ = await service.sports_bet(
        make_bet(guild=OTHER_GUILD, event_id="pronto", commence=KICKOFF - 500), game=GAME
    )
    done, _ = await service.sports_bet(make_bet(event_id="hecha"), game=GAME)
    await service.settle_sports_bet(GUILD, done.id, payout=0, game=GAME)

    open_bets = await service.open_sports_bets()

    assert [(b.guild_id, b.event_id) for b in open_bets] == [
        (OTHER_GUILD, "pronto"),
        (GUILD, "tarde"),
    ]
    assert {b.id for b in open_bets} == {early.id, late.id}


# -- Borrar el servidor -----------------------------------------------------------------


async def test_borrar_los_datos_del_servidor_borra_sus_apuestas_y_deja_las_de_otro(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    await service.sports_bet(make_bet(), game=GAME)
    kept, _ = await service.sports_bet(make_bet(guild=OTHER_GUILD), game=GAME)

    await service.delete_guild_data(GUILD)

    assert [b.id for b in await service.open_sports_bets()] == [kept.id]
    assert await service.sports_bets(GUILD, USER, limit=10) == []
    assert wallet(tmp_path, SPORTS_ACCOUNT_ID) == 0
    assert wallet(tmp_path, SPORTS_ACCOUNT_ID, OTHER_GUILD) == kept.stake


async def test_los_limites_de_apuesta_del_servicio_deportivo_son_coherentes() -> None:
    assert 0 < MIN_STAKE <= MAX_STAKE
