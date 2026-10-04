"""Pruebas del dinero de la tragaperras: apuesta, premio, IRPF y bote común.

Usan la economía real sobre un SQLite temporal y comprueban que el libro de
cada monedero cuadra con su saldo, que el Estado recibe exactamente lo
retenido y que el bote solo se mueve si la tirada se ha cobrado.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bot.repositories.economy import SLOTS_POT_ACCOUNT_ID, EconomyRepository, InsufficientFundsError
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService

GUILD = 1
USER = 10
SEED = 5_000
BIG_SEED = 200_000


async def make_service(tmp_path: Path) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=lambda: 1_000_000.0)


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


async def test_el_bote_se_siembra_la_primera_vez(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    assert await service.slots_pot(GUILD, seed=SEED) == SEED
    assert await service.slots_pot(GUILD, seed=SEED) == SEED
    assert ledger_sum(tmp_path, SLOTS_POT_ACCOUNT_ID) == SEED


async def test_tirada_perdida_cobra_y_manda_su_parte_al_bote(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    result = await service.play_slots(
        GUILD, USER, game="tragaperras", stake=100, payout=0, share=3, jackpot=False, seed=SEED
    )

    assert result.bet.balance == STARTING_BALANCE - 100
    assert result.pot == SEED + 3
    assert result.jackpot == 0
    assert ledger_sum(tmp_path, USER) == result.bet.balance
    assert ledger_sum(tmp_path, SLOTS_POT_ACCOUNT_ID) == result.pot


async def test_sin_saldo_no_se_mueve_nada_tampoco_el_bote(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.slots_pot(GUILD, seed=SEED)

    with pytest.raises(InsufficientFundsError):
        await service.play_slots(
            GUILD,
            USER,
            game="tragaperras",
            stake=5_000,
            payout=0,
            share=150,
            jackpot=False,
            seed=SEED,
        )

    assert await service.balance(GUILD, USER) == STARTING_BALANCE
    assert await service.slots_pot(GUILD, seed=SEED) == SEED


async def test_el_jackpot_se_lleva_el_bote_entero_tributa_y_se_resiembra(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    # Un bote grande, para que el premio pase del mínimo exento y tribute.
    for _ in range(5):
        await service.play_slots(
            GUILD,
            USER,
            game="tragaperras",
            stake=100,
            payout=0,
            share=3,
            jackpot=False,
            seed=BIG_SEED,
        )
    pot_before = await service.slots_pot(GUILD, seed=BIG_SEED)

    result = await service.play_slots(
        GUILD, USER, game="tragaperras", stake=100, payout=0, share=3, jackpot=True, seed=BIG_SEED
    )

    assert result.jackpot == pot_before + 3
    assert result.pot == BIG_SEED
    # Gana mucho más de lo perdido hoy: Hacienda retiene y se lo queda el Estado.
    assert result.bet.tax_delta > 0
    treasury = await service.treasury(GUILD, since=0)
    assert treasury.balance == result.bet.day_withheld
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == treasury.balance
    assert ledger_sum(tmp_path, USER) == result.bet.balance
    assert ledger_sum(tmp_path, SLOTS_POT_ACCOUNT_ID) == result.pot
    record = await service.last_jackpot(GUILD)
    assert record is not None
    assert (record.user_id, record.amount) == (USER, result.jackpot)


async def test_giro_gratis_paga_sin_cobrar_ni_aportar_al_bote(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    result = await service.play_slots(
        GUILD, USER, game="tragaperras", stake=0, payout=50, share=0, jackpot=False, seed=SEED
    )

    assert result.bet.balance >= STARTING_BALANCE
    assert result.pot == SEED
    assert result.bet.day_net == 50


async def test_perder_despues_del_premio_devuelve_el_irpf(tmp_path: Path) -> None:
    """Las pérdidas del mismo día desgravan, igual que en la ruleta."""
    service = await make_service(tmp_path)
    win = await service.play_slots(
        GUILD,
        USER,
        game="tragaperras",
        stake=100,
        payout=60_000,
        share=3,
        jackpot=False,
        seed=SEED,
    )
    assert win.bet.tax_delta > 0

    loss = await service.play_slots(
        GUILD,
        USER,
        game="tragaperras",
        stake=40_000,
        payout=0,
        share=1_200,
        jackpot=False,
        seed=SEED,
    )

    assert loss.bet.tax_delta < 0
    treasury = await service.treasury(GUILD, since=0)
    assert treasury.balance == loss.bet.day_withheld
    assert ledger_sum(tmp_path, USER) == loss.bet.balance


async def test_salir_del_servidor_borra_el_historial_de_botes(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.play_slots(
        GUILD, USER, game="tragaperras", stake=100, payout=0, share=3, jackpot=True, seed=SEED
    )

    await service.delete_guild_data(GUILD)

    assert await service.last_jackpot(GUILD) is None
