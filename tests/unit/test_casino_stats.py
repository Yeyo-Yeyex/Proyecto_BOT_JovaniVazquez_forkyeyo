"""Pruebas de las estadísticas del casino (`apuestas`).

Cubren las cuentas puras (`bot.services.casino_stats`), los agregados del
repositorio (`casino_plays`), la lectura del libro de la economía, los embeds
de cada página y que la función puente no rompa nunca al juego que la llama.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import apuestas as apuestas_cog
from bot.cogs.apuestas import PAGES, Apuestas, Panel
from bot.repositories.casino_stats import CasinoStatsRepository
from bot.repositories.economy import EconomyRepository
from bot.services.achievements import APUESTAS_PAGES, apuestas_stats
from bot.services.casino_stats import (
    STAKE_BUCKETS,
    LedgerTotals,
    Period,
    Play,
    Report,
    Totals,
    bucket_index,
    day_streaks,
    ledger_from_rows,
    period_start,
)
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import TIMEZONE

GUILD = 1
ANA = 10
BEA = 20
# Martes 6 de octubre de 2026, 21:00 en Canarias (UTC+1).
NOW = datetime(2026, 10, 6, 21, 0, tzinfo=TIMEZONE).timestamp()


class FakeClock:
    """Reloj controlable."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def make_repo(tmp_path: Path) -> CasinoStatsRepository:
    repository = CasinoStatsRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


def play(game: str, stake: int, payout: int, *, balance: int = 1_000, tax: int = 0) -> Play:
    return Play(game=game, stake=stake, payout=payout, tax=tax, balance_after=balance)


# -- Reglas puras -------------------------------------------------------------------------


def test_el_all_in_es_el_mismo_que_el_de_los_logros() -> None:
    # Tenía 100, apuesta 100 y gana 100: queda con 200.
    assert play("ruleta", 100, 200, balance=200).all_in
    # Tenía 500 y apuesta 100.
    assert not play("ruleta", 100, 0, balance=400).all_in
    # Una jugada gratis nunca es all-in.
    assert not play("botes", 0, 300, balance=300).all_in


def test_los_tramos_de_apuesta_cubren_todo_y_respetan_los_bordes() -> None:
    assert bucket_index(1) == 0
    assert bucket_index(99) == 0
    assert bucket_index(100) == 1
    assert bucket_index(101) == 2
    assert bucket_index(10**12) == len(STAKE_BUCKETS) - 1


def test_rachas_de_dias_cuentan_seguidos_y_la_actual_vive_hasta_ayer() -> None:
    days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-05", "2026-10-06"]
    assert day_streaks(days, date(2026, 10, 6)) == (3, 2)
    assert day_streaks(days, date(2026, 10, 7)) == (3, 2)
    assert day_streaks(days, date(2026, 10, 9)) == (3, 0)
    assert day_streaks([], date(2026, 10, 9)) == (0, 0)


def test_los_periodos_empiezan_a_medianoche_y_siempre_no_tiene_inicio() -> None:
    assert period_start(Period.TODAY, 1_000_000.0) == 1_000_000.0
    assert period_start(Period.WEEK, 1_000_000.0) == 1_000_000.0 - 6 * 86_400
    assert period_start(Period.ALL, 1_000_000.0) is None


def test_el_libro_junta_los_temas_de_los_botes_e_ignora_lo_que_no_es_casino() -> None:
    ledger = ledger_from_rows(
        [
            ("ruleta:apuesta", 3, -300),
            ("ruleta:premio", 1, 360),
            ("volcan:apuesta", 2, -200),
            ("volcan:premio", 1, 50),
            ("tragaperras:bote", 1, 5_000),
            ("tienda:premio", 1, 999),
        ],
        withheld=100,
        refunded_day=30,
        refunded_renta=20,
        first_at=5.0,
    )
    assert ledger.by_game["ruleta"] == (3, 300, 360)
    assert ledger.by_game["botes"] == (2, 200, 50)
    assert ledger.by_game["tragaperras"] == (0, 0, 5_000)
    assert "tienda" not in ledger.by_game
    assert ledger.sanxe_net == 50
    assert ledger.wagered == 500


def test_las_cifras_derivadas_de_los_totales() -> None:
    totals = Totals(plays=4, wagered=400, paid=300, wins=1, losses=3, withheld=10, refunded=4)
    assert totals.net == -100
    assert totals.rtp == pytest.approx(0.75)
    assert totals.house_edge == pytest.approx(0.25)
    assert totals.average_stake == 100
    assert totals.after_tax == -106
    assert totals.win_rate == pytest.approx(0.25)
    assert Totals().rtp is None


# -- Repositorio --------------------------------------------------------------------------


async def test_el_informe_agrega_jugadas_por_juego_con_ganadas_empates_y_gratis(
    tmp_path: Path,
) -> None:
    repository = await make_repo(tmp_path)
    for item in (
        play("ruleta", 100, 0),
        play("ruleta", 100, 3_600, tax=500),
        play("ruleta", 200, 200),
        play("blackjack", 50, 100),
        play("botes", 0, 700),
    ):
        await repository.record(GUILD, ANA, item, now=NOW)

    report = await repository.report(GUILD, ANA, since=None, today=date(2026, 10, 6))

    roulette = report.by_game["ruleta"]
    assert (roulette.plays, roulette.wins, roulette.pushes, roulette.losses) == (3, 1, 1, 1)
    assert roulette.busts == 1
    assert roulette.wagered == 400
    assert roulette.paid == 3_800
    assert roulette.best == 3_500
    assert roulette.worst == -100
    assert roulette.max_mult == pytest.approx(36.0)
    assert roulette.withheld == 500
    botes = report.by_game["botes"]
    assert (botes.plays, botes.free_plays, botes.free_paid) == (0, 1, 700)
    total = report.total
    assert total.plays == 4
    assert total.wagered == 450
    assert report.favorite == "ruleta"
    assert report.players == 1
    assert report.days == ("2026-10-06",)
    # Hora canaria, no UTC.
    assert report.by_hour[21][0] == 5
    assert report.by_weekday[1][0] == 5  # martes
    assert report.ranking == ()


async def test_la_mediana_y_los_tramos_ignoran_las_jugadas_gratis(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    for stake in (10, 100, 100, 5_000):
        await repository.record(GUILD, ANA, play("crash", stake, 0), now=NOW)
    await repository.record(GUILD, ANA, play("botes", 0, 50), now=NOW)

    report = await repository.report(GUILD, None, since=None, today=date(2026, 10, 6))

    assert report.median_stake == 100
    assert report.buckets[bucket_index(10)] == 1
    assert report.buckets[bucket_index(100)] == 2
    assert report.buckets[bucket_index(5_000)] == 1
    assert sum(report.buckets) == 4


async def test_el_periodo_deja_fuera_lo_anterior(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    await repository.record(GUILD, ANA, play("minas", 100, 0), now=NOW - 10 * 86_400)
    await repository.record(GUILD, ANA, play("minas", 100, 250), now=NOW)

    week = await repository.report(GUILD, ANA, since=NOW - 3_600, today=date(2026, 10, 6))
    always = await repository.report(GUILD, ANA, since=None, today=date(2026, 10, 6))

    assert week.total.plays == 1
    assert always.total.plays == 2
    assert week.recorded_since == always.first_at == NOW - 10 * 86_400


async def test_el_ranking_y_los_records_del_servidor(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    await repository.record(GUILD, ANA, play("pollo", 100, 10_000), now=NOW)
    await repository.record(GUILD, BEA, play("pollo", 5_000, 0), now=NOW)
    await repository.record(GUILD, BEA, play("pollo", 100, 150), now=NOW)
    await repository.record(GUILD + 1, ANA, play("pollo", 9_999, 0), now=NOW)

    report = await repository.report(GUILD, None, since=None, today=date(2026, 10, 6))

    by_user = {row.user_id: row for row in report.ranking}
    assert by_user[ANA].net == 9_900
    assert by_user[BEA].net == -4_950
    assert report.top_wins[0].user_id == ANA
    assert report.top_losses[0].stake == 5_000
    assert report.top_stakes[0].stake == 5_000  # el otro servidor no cuenta
    assert report.top_mults[0].mult == pytest.approx(100.0)
    assert report.players == 2


async def test_la_grafica_diaria_rellena_los_dias_sin_jugadas(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    await repository.record(GUILD, ANA, play("ruleta", 100, 0), now=NOW - 86_400)

    report = await repository.report(GUILD, ANA, since=None, today=date(2026, 10, 6))

    assert len(report.daily) == 14
    assert report.daily[-1] == ("2026-10-06", 0, 0)
    assert report.daily[-2] == ("2026-10-05", 1, -100)


async def test_salir_del_servidor_borra_sus_jugadas(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    await repository.record(GUILD, ANA, play("ruleta", 100, 0), now=NOW)
    await repository.delete_guild_data(GUILD)
    report = await repository.report(GUILD, None, since=None, today=date(2026, 10, 6))
    assert report.empty


async def test_una_jugada_con_cifras_negativas_no_se_apunta(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)
    with pytest.raises(ValueError):
        await repository.record(GUILD, ANA, play("ruleta", -1, 0), now=NOW)


# -- Libro de la economía -----------------------------------------------------------------


async def test_el_libro_cuenta_apuestas_premios_e_irpf_del_casino(tmp_path: Path) -> None:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    economy = EconomyService(economy_repo, clock=FakeClock())
    await economy.grant(GUILD, ANA, amount=100_000, reason="prueba")
    await economy.settle_bet(GUILD, ANA, game="ruleta", stake=10_000, payout=360_000)
    await economy.settle_bet(GUILD, ANA, game="ruleta", stake=1_000, payout=0)

    rows, withheld, refunded_day, refunded_renta, first_at = await economy.casino_ledger(GUILD, ANA)
    ledger = ledger_from_rows(
        rows,
        withheld=withheld,
        refunded_day=refunded_day,
        refunded_renta=refunded_renta,
        first_at=first_at,
    )

    assert ledger.by_game["ruleta"] == (2, 11_000, 360_000)
    assert withheld > 0
    assert refunded_day > 0  # perder después de ganar devuelve en el día
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (state_in,) = connection.execute(
            "SELECT SUM(delta) FROM economy_ledger WHERE user_id = 0 AND reason LIKE '%juego'"
        ).fetchone()
    assert state_in == withheld - refunded_day
    # El libro del servidor no mezcla al Estado.
    server_rows, *_ = await economy.casino_ledger(GUILD)
    assert (
        ledger_from_rows(
            server_rows, withheld=0, refunded_day=0, refunded_renta=0, first_at=None
        ).by_game
        == ledger.by_game
    )


# -- Logros -------------------------------------------------------------------------------


def test_los_logros_de_mirar_cuentan_consultas_cotilleo_y_secretos() -> None:
    when = datetime(2026, 10, 6, 3, 0, tzinfo=TIMEZONE)
    delta = apuestas_stats(
        page="resumen",
        period="siempre",
        opened=True,
        own=True,
        snooping=False,
        plays=150,
        rtp=0.4,
        net=-120_000,
        when=when,
    )
    assert delta.add["apuestas_views"] == 1
    assert delta.add["apuestas_insomnia"] == 1
    assert delta.add["apuestas_denial"] == 1
    assert delta.add["apuestas_ruin"] == 1
    assert "apuestas_snoop" not in delta.add
    page_change = apuestas_stats(
        page="ranking",
        period=None,
        opened=False,
        own=False,
        snooping=False,
        plays=0,
        rtp=None,
        net=0,
        when=when,
    )
    assert page_change.add == {"apuestas_page_ranking": 1}


def test_las_paginas_de_los_logros_son_las_del_panel() -> None:
    assert tuple(PAGES) == APUESTAS_PAGES


# -- Embeds -------------------------------------------------------------------------------


async def full_report(tmp_path: Path, user_id: int | None) -> Report:
    repository = await make_repo(tmp_path)
    games = ("ruleta", "blackjack", "tragaperras", "botes", "crash", "minas", "pollo", "pachinko")
    for index in range(200):
        game = games[index % len(games)]
        stake = (index % 7 + 1) * 1_337
        payout = 0 if index % 3 else stake * (index % 50 + 2)
        await repository.record(
            GUILD,
            1_000 + index % 12,
            play(game, stake, payout, balance=index * 10, tax=index % 5 - 2),
            now=NOW - index * 3_000,
        )
    report = await repository.report(GUILD, user_id, since=None, today=date(2026, 10, 6))
    ledger = LedgerTotals(
        by_game={g: (10**6, 10**12, 9 * 10**11) for g in games},
        withheld=10**9,
        refunded_day=10**8,
        refunded_renta=10**7,
        first_at=NOW - 10**7,
    )
    from bot.services.casino_stats import with_ledger

    return with_ledger(report, ledger)


def check_limits(embed: discord.Embed) -> None:
    assert len(embed) <= 6_000
    assert len(embed.description or "") <= 4_096
    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert len(field.name) <= 256
        assert 0 < len(field.value) <= 1_024


@pytest.mark.parametrize("server", [True, False])
async def test_todas_las_paginas_caben_en_un_embed(tmp_path: Path, server: bool) -> None:
    report = await full_report(tmp_path, None if server else 1_000)
    names = {1_000 + i: "Nombre_muy_largo_de_alguien_" + str(i) for i in range(12)}
    panel = Panel(
        report,
        scope="Servidor con un nombre bastante largo",
        member_scope=not server,
        period=Period.ALL,
        names=names,
        today=datetime(2026, 10, 6, tzinfo=TIMEZONE),
    )
    for page in PAGES:
        check_limits(panel.embed(page))


@pytest.mark.parametrize("page", list(PAGES))
async def test_las_paginas_vacias_no_rompen(tmp_path: Path, page: str) -> None:
    repository = await make_repo(tmp_path)
    report = await repository.report(GUILD, ANA, since=None, today=date(2026, 10, 6))
    panel = Panel(
        report,
        scope="Ana",
        member_scope=True,
        period=Period.TODAY,
        names={},
        today=datetime(2026, 10, 6, tzinfo=TIMEZONE),
    )
    check_limits(panel.embed(page))


# -- Cog y función puente -----------------------------------------------------------------


def user(user_id: int, *, is_bot: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = is_bot
    member.display_name = f"m{user_id}"
    return member


async def make_cog(tmp_path: Path) -> tuple[MagicMock, Apuestas]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    bot = MagicMock()
    cog = Apuestas(
        bot,
        await make_repo(tmp_path),
        EconomyService(economy_repo, clock=FakeClock()),
        clock=FakeClock(),
    )
    bot.get_cog = MagicMock(return_value=cog)
    return bot, cog


async def test_la_funcion_puente_apunta_la_jugada_con_el_premio_calculado(tmp_path: Path) -> None:
    bot, cog = await make_cog(tmp_path)
    await apuestas_cog.record(
        bot, GUILD, user(ANA), game="minas", stake=100, net=-100, balance_after=0, tax=0
    )
    await apuestas_cog.record(
        bot, GUILD, user(ANA), game="minas", stake=100, net=150, balance_after=150, tax=20
    )
    await apuestas_cog.record(
        bot, GUILD, user(99, is_bot=True), game="minas", stake=1, net=0, balance_after=1, tax=0
    )
    report = await cog.report(GUILD, ANA, Period.ALL)
    minas = report.by_game["minas"]
    assert (minas.plays, minas.wagered, minas.paid, minas.broke, minas.withheld) == (
        2,
        200,
        250,
        1,
        20,
    )
    assert (await cog.report(GUILD, 99, Period.ALL)).empty


async def test_la_funcion_puente_no_lanza_si_falla_o_no_hay_cog(tmp_path: Path) -> None:
    bot, cog = await make_cog(tmp_path)
    cog.repository.record = AsyncMock(side_effect=sqlite3.OperationalError("disco lleno"))
    await apuestas_cog.record(
        bot, GUILD, user(ANA), game="ruleta", stake=100, net=0, balance_after=0, tax=0
    )
    empty_bot = MagicMock()
    empty_bot.get_cog = MagicMock(return_value=None)
    await apuestas_cog.record(
        empty_bot, GUILD, user(ANA), game="ruleta", stake=100, net=0, balance_after=0, tax=0
    )


async def test_el_comando_abre_el_panel_y_cuenta_la_consulta(tmp_path: Path) -> None:
    bot, cog = await make_cog(tmp_path)
    track = AsyncMock()
    original = apuestas_cog.logros.track
    apuestas_cog.logros.track = track
    try:
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.name = "Servidor"
        guild.get_member = MagicMock(return_value=None)
        embed, view = await cog._open(guild, user(ANA), user(BEA), None)
    finally:
        apuestas_cog.logros.track = original
    assert embed.title and "Resumen" in embed.title
    delta = track.await_args.args[4]
    assert delta.add["apuestas_views"] == 1
    assert delta.add["apuestas_snoop"] == 1
    assert not view.server_scope
