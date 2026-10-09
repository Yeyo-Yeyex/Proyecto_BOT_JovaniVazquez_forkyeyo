"""Pruebas del dinero de la tragaperras: apuesta, premio, IRPF y bote común.

Usan la economía real sobre un SQLite temporal y comprueban que el libro de
cada monedero cuadra con su saldo, que el Estado recibe exactamente lo
retenido y que el bote solo se mueve si la tirada se ha cobrado. El bote
misterioso («tiene que caer antes de X») se prueba con el sorteo forzado.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bot.repositories.economy import (
    SLOTS_POT_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    SlotsSettlement,
)
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
        stake=30_000,
        payout=0,
        share=900,
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


# -- Bote misterioso («tiene que caer antes de X») ----------------------------------

CAP = SEED + 100


class FixedDraw:
    """Sorteo de la cifra oculta forzado: apunta los intervalos y devuelve lo pedido."""

    def __init__(self, *values: int) -> None:
        self.values = list(values)
        self.calls: list[tuple[int, int]] = []

    def __call__(self, low: int, high: int) -> int:
        self.calls.append((low, high))
        return self.values.pop(0)


async def spin(
    service: EconomyService,
    draw: FixedDraw | None = None,
    *,
    stake: int = 100,
    share: int = 10,
    jackpot: bool = False,
    cap: int = CAP,
    seed: int = SEED,
) -> SlotsSettlement:
    return await service.play_slots(
        GUILD,
        USER,
        game="tragaperras",
        stake=stake,
        payout=0,
        share=share,
        jackpot=jackpot,
        seed=seed,
        cap=cap,
        draw_hit=draw,
    )


async def test_el_misterio_cae_justo_al_cruzar_la_cifra_oculta_y_no_antes(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    draw = FixedDraw(SEED + 30, SEED + 50)

    first = await spin(service, draw)
    second = await spin(service, draw)

    # La cifra se sortea entre el bote de ahora (la semilla) y el tope.
    assert draw.calls == [(SEED, CAP)]
    assert (first.jackpot, first.mystery, first.pot) == (0, False, SEED + 10)
    assert (second.jackpot, second.mystery) == (0, False)
    assert second.spins_since == 2

    third = await spin(service, draw)

    assert third.mystery is True
    assert third.jackpot == SEED + 30
    assert third.pot == SEED
    assert (third.spins_since, third.drought) == (0, 3)
    # Al vaciarse se sortea la siguiente cifra, de la semilla al tope.
    assert draw.calls[-1] == (SEED, CAP)
    assert ledger_sum(tmp_path, USER) == third.bet.balance
    assert ledger_sum(tmp_path, SLOTS_POT_ACCOUNT_ID) == third.pot
    record = await service.last_jackpot(GUILD)
    assert record is not None
    assert (record.user_id, record.amount, record.mystery) == (USER, third.jackpot, True)
    assert await service.slots_spins_since(GUILD) == 0


async def test_el_misterio_tributa_como_el_jackpot(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    draw = FixedDraw(BIG_SEED + 3, BIG_SEED + 50)

    result = await spin(service, draw, share=3, seed=BIG_SEED, cap=BIG_SEED * 2)

    assert result.mystery is True
    assert result.jackpot == BIG_SEED + 3
    assert result.bet.tax_delta > 0
    treasury = await service.treasury(GUILD, since=0)
    assert treasury.balance == result.bet.day_withheld
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == treasury.balance


async def test_sin_tope_el_bote_no_cae_solo_ni_sortea(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    draw = FixedDraw()

    await service.grant(GUILD, USER, amount=10 * CAP, reason="prueba")

    for _ in range(5):
        result = await spin(service, draw, stake=CAP, share=CAP, cap=0)

    assert draw.calls == []
    assert (result.jackpot, result.mystery) == (0, False)
    assert result.pot == SEED + 5 * CAP
    assert result.spins_since == 5


async def test_sin_tope_ni_sorteo_se_porta_como_antes(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    result = await service.play_slots(
        GUILD, USER, game="tragaperras", stake=100, payout=0, share=3, jackpot=False, seed=SEED
    )

    assert (result.jackpot, result.mystery, result.pot) == (0, False, SEED + 3)


async def test_el_jackpot_normal_resortea_la_cifra_y_reinicia_las_tiradas(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    draw = FixedDraw(CAP, SEED + 40, CAP)
    await spin(service, draw)
    await spin(service, draw)

    result = await spin(service, draw, jackpot=True)

    assert result.mystery is False
    assert result.jackpot == SEED + 30
    assert (result.spins_since, result.drought) == (0, 3)
    assert draw.calls == [(SEED, CAP), (SEED, CAP)]
    assert await service.slots_spins_since(GUILD) == 0
    record = await service.last_jackpot(GUILD)
    assert record is not None and record.mystery is False

    # La cifra nueva (semilla + 40) manda ahora: con 30 no cae, con 40 sí.
    assert (await spin(service, draw, share=30)).mystery is False
    assert (await spin(service, draw, share=10)).mystery is True


async def test_los_giros_gratis_no_cuentan_tiradas_ni_hacen_caer_el_bote(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    draw = FixedDraw(SEED)  # Caería con cualquier aportación.

    result = await spin(service, draw, stake=0, share=0)

    assert (result.jackpot, result.mystery, result.spins_since) == (0, False, 0)
    assert await service.slots_spins_since(GUILD) == 0


async def test_un_bote_por_encima_del_tope_cae_en_la_siguiente_aportacion(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    # Un servidor que ya tenía el bote muy crecido antes de que hubiera tope.
    await service.grant(GUILD, USER, amount=10 * CAP, reason="prueba")
    await spin(service, None, stake=CAP, share=CAP, cap=0)
    pot = await service.slots_pot(GUILD, seed=SEED)
    assert pot > CAP
    draw = FixedDraw(pot + 1, SEED + 20)

    result = await spin(service, draw, share=1)

    assert draw.calls[0] == (pot, pot + 1)
    assert result.mystery is True
    assert result.jackpot == pot + 1


async def test_tiradas_sin_bote_es_cero_en_un_servidor_nuevo(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    assert await service.slots_spins_since(GUILD) == 0


async def test_la_base_antigua_gana_la_columna_del_misterio(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE economy_slots_jackpots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL CHECK (amount > 0),
                won_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO economy_slots_jackpots (guild_id, user_id, amount, won_at)"
            " VALUES (?, ?, ?, ?)",
            (GUILD, USER, 7_000, 1.0),
        )
    connection.close()

    service = await make_service(tmp_path)
    # Inicializar dos veces no vuelve a añadirla.
    await service.repository.initialize()

    record = await service.last_jackpot(GUILD)
    assert record is not None
    assert (record.amount, record.mystery) == (7_000, False)


async def test_si_baja_el_tope_la_cifra_oculta_se_vuelve_a_sortear(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    draw = FixedDraw(CAP, SEED + 20, SEED + 50)
    await spin(service, draw, cap=CAP)

    result = await spin(service, draw, cap=SEED + 50)

    # La cifra de antes (el tope viejo) ya no cae «antes de» el tope nuevo.
    assert draw.calls[1] == (SEED + 10, SEED + 50)
    assert result.mystery is True
    assert result.jackpot == SEED + 20
