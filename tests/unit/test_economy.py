"""Pruebas de la economía: repositorio SQLite y reglas de `EconomyService`."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from bot.repositories.economy import (
    MAX_BALANCE,
    BalanceLimitError,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.services.economy import (
    DAILY_COOLDOWN_SECONDS,
    DAILY_STREAK_WINDOW_SECONDS,
    STARTING_BALANCE,
    EconomyService,
    daily_amount,
    format_amount,
    parse_amount,
)

GUILD = 1
USER = 10


class FakeClock:
    """Reloj controlable para probar esperas y rachas sin dormir."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def make_service(tmp_path: Path, clock: FakeClock | None = None) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=clock or FakeClock())


def ledger_sum(tmp_path: Path, user_id: int = USER) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


async def test_monedero_nuevo_empieza_con_el_saldo_inicial_y_queda_en_el_libro(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)

    assert await service.balance(GUILD, USER) == STARTING_BALANCE
    assert ledger_sum(tmp_path) == STARTING_BALANCE


async def test_apuesta_ganada_cobra_y_paga_en_la_misma_operacion(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    balance = await service.settle_bet(GUILD, USER, game="ruleta", stake=100, payout=200)

    assert balance == STARTING_BALANCE + 100
    assert ledger_sum(tmp_path) == balance


async def test_apuesta_sin_saldo_no_cobra_ni_paga_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(InsufficientFundsError) as error:
        await service.settle_bet(
            GUILD, USER, game="ruleta", stake=STARTING_BALANCE + 1, payout=10**6
        )

    assert error.value.balance == STARTING_BALANCE
    assert await service.balance(GUILD, USER) == STARTING_BALANCE
    assert ledger_sum(tmp_path) == STARTING_BALANCE


async def test_all_in_perdido_deja_el_saldo_en_cero(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    balance = await service.settle_bet(GUILD, USER, game="ruleta", stake=STARTING_BALANCE, payout=0)

    assert balance == 0


async def test_apuestas_simultaneas_no_gastan_dos_veces_el_mismo_dinero(tmp_path: Path) -> None:
    """Dos all-in a la vez: solo uno puede cobrarse; el otro falla por saldo."""
    service = await make_service(tmp_path)
    await service.balance(GUILD, USER)

    results = await asyncio.gather(
        *(
            service.settle_bet(GUILD, USER, game="ruleta", stake=STARTING_BALANCE, payout=0)
            for _ in range(5)
        ),
        return_exceptions=True,
    )

    failures = [r for r in results if isinstance(r, InsufficientFundsError)]
    assert len(failures) == 4
    assert await service.balance(GUILD, USER) == 0
    assert ledger_sum(tmp_path) == 0


async def test_premio_que_supera_el_tope_se_rechaza_entero(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(BalanceLimitError):
        await service.settle_bet(GUILD, USER, game="ruleta", stake=1, payout=MAX_BALANCE)

    assert await service.balance(GUILD, USER) == STARTING_BALANCE


async def test_apply_comprueba_cada_paso_en_orden(tmp_path: Path) -> None:
    """Un premio posterior no puede financiar una apuesta que no se podía pagar."""
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=0)
    await repository.initialize()

    with pytest.raises(InsufficientFundsError):
        await repository.apply(GUILD, USER, [LedgerEntry(-10, "a"), LedgerEntry(50, "b")])

    assert await repository.balance(GUILD, USER) == 0


async def test_saldos_aislados_por_servidor(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    await service.settle_bet(GUILD, USER, game="ruleta", stake=500, payout=0)

    assert await service.balance(GUILD + 1, USER) == STARTING_BALANCE


async def test_daily_primera_vez_cobra_la_base(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    result = await service.claim_daily(GUILD, USER)

    assert result.claimed
    assert result.amount == daily_amount(1)
    assert result.streak == 1
    assert result.balance == STARTING_BALANCE + daily_amount(1)


async def test_daily_repetido_antes_de_tiempo_no_cobra(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    first = await service.claim_daily(GUILD, USER)
    clock.now += DAILY_COOLDOWN_SECONDS - 1

    second = await service.claim_daily(GUILD, USER)

    assert not second.claimed
    assert second.balance == first.balance
    assert second.next_claim_at == first.next_claim_at


async def test_daily_seguido_sube_la_racha_y_la_cantidad(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    await service.claim_daily(GUILD, USER)
    clock.now += DAILY_COOLDOWN_SECONDS

    result = await service.claim_daily(GUILD, USER)

    assert result.streak == 2
    assert result.amount == daily_amount(2) > daily_amount(1)


async def test_daily_tras_mas_de_48h_reinicia_la_racha(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    await service.claim_daily(GUILD, USER)
    clock.now += DAILY_STREAK_WINDOW_SECONDS + 1

    result = await service.claim_daily(GUILD, USER)

    assert result.streak == 1


async def test_daily_simultaneo_solo_cobra_una_vez(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    results = await asyncio.gather(*(service.claim_daily(GUILD, USER) for _ in range(5)))

    assert sum(r.claimed for r in results) == 1
    assert await service.balance(GUILD, USER) == STARTING_BALANCE + daily_amount(1)


def test_la_recompensa_diaria_tiene_tope() -> None:
    assert daily_amount(1000) == daily_amount(11)


async def test_borrar_servidor_elimina_su_economia(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.settle_bet(GUILD, USER, game="ruleta", stake=500, payout=0)

    await service.delete_guild_data(GUILD)

    assert ledger_sum(tmp_path) == 0
    assert await service.balance(GUILD, USER) == STARTING_BALANCE


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500", 500),
        ("1.500", 1500),
        ("2k", 2000),
        ("2.5k", 2500),
        ("2,5k", 2500),
        ("1M", 1_000_000),
        ("all", 777),
        ("TODO", 777),
        ("mitad", 388),
    ],
)
def test_parse_amount_entiende_los_formatos_habituales(text: str, expected: int) -> None:
    assert parse_amount(text, balance=777) == expected


@pytest.mark.parametrize("text", ["", "abc", "-5", "0", "1.5", "1,23"])
def test_parse_amount_rechaza_cantidades_invalidas(text: str) -> None:
    with pytest.raises(ValueError):
        parse_amount(text, balance=777)


def test_all_in_con_saldo_cero_no_es_una_apuesta_valida() -> None:
    with pytest.raises(ValueError):
        parse_amount("all", balance=0)


def test_format_amount_usa_punto_de_miles() -> None:
    assert format_amount(1234567) == "1.234.567 Y$"


async def test_place_bet_cobra_y_pay_winnings_paga_despues(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    after_bet = await service.place_bet(GUILD, USER, game="blackjack", stake=300)
    after_pay = await service.pay_winnings(GUILD, USER, game="blackjack", amount=750)

    assert after_bet == STARTING_BALANCE - 300
    assert after_pay == STARTING_BALANCE + 450
    assert ledger_sum(tmp_path) == after_pay


async def test_place_bet_sin_saldo_no_cobra(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(InsufficientFundsError):
        await service.place_bet(GUILD, USER, game="blackjack", stake=STARTING_BALANCE + 1)

    assert await service.balance(GUILD, USER) == STARTING_BALANCE


async def test_pay_winnings_de_cero_no_anota_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.place_bet(GUILD, USER, game="blackjack", stake=100)

    balance = await service.pay_winnings(GUILD, USER, game="blackjack", amount=0)

    assert balance == STARTING_BALANCE - 100
