"""Pruebas de las loterías: reglas reales, reparto, dinero con el Estado de banca y el cog.

Las reglas se comprueban contra las cifras oficiales (probabilidades de SELAE,
el 70 % de la Nacional, las tablas de los rascas de la ONCE). El dinero se
prueba con la economía y el repositorio reales sobre un SQLite temporal.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bot.cogs.lottery import Loteria, odds_table, scratch_text, ticket_tags
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.repositories.lottery import DrawChanged, LotteryRepository
from bot.services.achievements import lottery_buy_stats, lottery_prize_stats, scratch_stats
from bot.services.economy import (
    STARTING_BALANCE,
    EconomyService,
    InsufficientFundsError,
    LotteryPayout,
)
from bot.services.lottery import (
    DRAW_TIMEZONE,
    GAME_BY_KEY,
    MAX_PER_DRAW,
    LotteryError,
    Pick,
    _no_inversion,
    classify,
    format_pick,
    guarantee_for,
    jackpot_estimate,
    nacional_expected_return,
    nacional_prizes,
    next_draw,
    parse_pick,
    random_pick,
    scratch,
    scratch_grid,
    settle_nacional,
    settle_pool,
)
from bot.services.taxes import LOTTERY_EXEMPT, lottery_tax

GUILD = 1
ALICE = 10
BOB = 11
NOW = datetime(2026, 10, 5, 19, 0, tzinfo=DRAW_TIMEZONE).timestamp()  # lunes

PRIMITIVA = GAME_BY_KEY["primitiva"]
BONOLOTO = GAME_BY_KEY["bonoloto"]
GORDO = GAME_BY_KEY["gordo"]
EURO = GAME_BY_KEY["euromillones"]
JUEVES = GAME_BY_KEY["jueves"]
NAVIDAD = GAME_BY_KEY["navidad"]


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def make(
    tmp_path: Path, clock: Clock | None = None
) -> tuple[EconomyService, LotteryRepository]:
    database = tmp_path / "bot.sqlite3"
    repository = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await repository.initialize()
    lottery = LotteryRepository(database)
    await lottery.initialize()
    return EconomyService(repository, clock=clock or Clock()), lottery


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.sqlite3") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


# -- Probabilidades y programas reales -----------------------------------------------------


def odds(game_key: str) -> dict[str, int]:
    game = GAME_BY_KEY[game_key]
    return {c.key: c.odds(game.combinations) for c in game.categories}


def test_probabilidades_de_la_primitiva_son_las_oficiales() -> None:
    assert odds("primitiva") == {
        "especial": 139_838_160,
        "1": 15_537_573,
        "2": 2_330_636,
        "3": 55_491,
        "4": 1_032,
        "5": 57,
    }


def test_probabilidades_de_euromillones_y_gordo_son_las_oficiales() -> None:
    euro = odds("euromillones")
    assert euro["1"] == 139_838_160 and euro["2"] == 6_991_908 and euro["13"] == 22
    assert odds("gordo")["1"] == 31_625_100
    assert odds("gordo")["8"] == 19


@pytest.mark.parametrize("key", ["jueves", "sabado", "navidad", "nino"])
def test_la_nacional_devuelve_el_70_por_ciento_de_la_emision(key: str) -> None:
    rate = nacional_expected_return(GAME_BY_KEY[key], random.Random(7))
    assert 0.69 <= rate <= 0.701


def test_la_navidad_cuadra_al_euro_con_el_programa_real() -> None:
    assert nacional_expected_return(NAVIDAD, random.Random(1)) == pytest.approx(0.70, abs=1e-9)


@pytest.mark.parametrize(("key", "rate"), [("x10", 0.64), ("7ymedia", 0.59)])
def test_los_rascas_devuelven_lo_de_su_emision(key: str, rate: float) -> None:
    game = GAME_BY_KEY[key]
    assert game.scratch is not None
    prizes, total = game.scratch
    paid = sum(p.amount * p.count for p in prizes)
    assert paid / (game.price * total) == pytest.approx(rate, abs=0.001)


def test_precios_a_escala_de_10_yapdollars_por_euro() -> None:
    prices = {key: GAME_BY_KEY[key].price for key in GAME_BY_KEY}
    assert prices["primitiva"] == 10 and prices["bonoloto"] == 5 and prices["gordo"] == 15
    assert prices["euromillones"] == 22 and prices["jueves"] == 30 and prices["navidad"] == 200


# -- Apuestas -------------------------------------------------------------------------------


def test_parse_pick_lee_numeros_reintegro_clave_y_estrellas() -> None:
    rng = random.Random(1)
    assert parse_pick(PRIMITIVA, "49 3 14, 22 30 41 R7", rng) == Pick((3, 14, 22, 30, 41, 49), (7,))
    assert parse_pick(GORDO, "1 2 3 4 54 c0", rng) == Pick((1, 2, 3, 4, 54), (0,))
    assert parse_pick(EURO, "1 2 3 4 50 ★ 12 1", rng) == Pick((1, 2, 3, 4, 50), (1, 12))
    assert parse_pick(JUEVES, "07", rng) == Pick((7,))
    assert format_pick(JUEVES, Pick((7,))) == "00007"


def test_parse_pick_sin_reintegro_lo_pone_al_azar() -> None:
    pick = parse_pick(PRIMITIVA, "1 2 3 4 5 6", random.Random(1))
    assert len(pick.extra) == 1 and 0 <= pick.extra[0] <= 9


@pytest.mark.parametrize(
    ("game_key", "text"),
    [
        ("primitiva", "1 2 3 4 5"),
        ("primitiva", "1 2 3 4 5 5"),
        ("primitiva", "1 2 3 4 5 50"),
        ("primitiva", "1 2 3 4 5 6 R12"),
        ("euromillones", "1 2 3 4 5 * 13 1"),
        ("euromillones", "1 2 3 4 5 * 3 3"),
        ("jueves", "123456"),
        ("jueves", "12a"),
    ],
)
def test_parse_pick_rechaza_apuestas_imposibles(game_key: str, text: str) -> None:
    with pytest.raises(LotteryError):
        parse_pick(GAME_BY_KEY[game_key], text, random.Random(1))


def test_apuesta_codificada_vuelve_igual() -> None:
    rng = random.Random(3)
    for game in (PRIMITIVA, GORDO, EURO, JUEVES):
        pick = random_pick(game, rng)
        assert Pick.decode(pick.encode()) == pick


# -- Calendario -----------------------------------------------------------------------------


def test_proximos_sorteos_en_hora_de_madrid() -> None:
    def madrid(moment: float) -> tuple:
        local = datetime.fromtimestamp(moment, DRAW_TIMEZONE)
        return (local.month, local.day, local.hour, local.minute)

    assert madrid(next_draw(PRIMITIVA, NOW)) == (10, 5, 21, 30)  # hoy lunes
    assert madrid(next_draw(EURO, NOW)) == (10, 6, 21, 0)  # martes
    assert madrid(next_draw(GORDO, NOW)) == (10, 11, 21, 30)  # domingo
    assert madrid(next_draw(JUEVES, NOW)) == (10, 8, 21, 30)
    assert madrid(next_draw(NAVIDAD, NOW)) == (12, 22, 9, 0)
    after_xmas = datetime(2026, 12, 22, 10, tzinfo=DRAW_TIMEZONE).timestamp()
    assert datetime.fromtimestamp(next_draw(NAVIDAD, after_xmas)).year == 2027


def test_el_sorteo_siguiente_es_estrictamente_posterior() -> None:
    at = next_draw(BONOLOTO, NOW)
    assert next_draw(BONOLOTO, at) > at


# -- Clasificación y reparto -------------------------------------------------------------

LOTTO_RESULT = {"numbers": [1, 2, 3, 4, 5, 6], "comp": 7, "reintegro": 3}


def test_clasificacion_de_la_primitiva() -> None:
    def cat(numbers: tuple, r: int) -> tuple:
        return classify(PRIMITIVA, Pick(numbers, (r,)), LOTTO_RESULT)

    assert cat((1, 2, 3, 4, 5, 6), 3) == ("especial", True)
    assert cat((1, 2, 3, 4, 5, 6), 0) == ("1", False)
    assert cat((1, 2, 3, 4, 5, 7), 0) == ("2", False)
    assert cat((1, 2, 3, 4, 5, 9), 0) == ("3", False)
    assert cat((1, 2, 3, 4, 8, 9), 3) == ("4", True)
    assert cat((1, 2, 3, 10, 8, 9), 0) == ("5", False)
    assert cat((1, 2, 11, 10, 8, 9), 0) == (None, False)
    # En la Bonoloto no hay especial: 6 aciertos con reintegro es la 1ª.
    assert classify(BONOLOTO, Pick((1, 2, 3, 4, 5, 6), (3,)), LOTTO_RESULT) == ("1", True)


def test_categorias_desiertas_bajan_y_el_resto_va_al_bote() -> None:
    # Solo hay un acertante de 4: la 2ª y la 3ª, desiertas, bajan a la 4ª
    # (norma 8ª.1.3); la especial y la 1ª pasan al bote (8ª.1.1 y 1.2).
    settlement = settle_pool(
        PRIMITIVA,
        sales=100_000,
        carry=5_000,
        guarantee=0,
        tickets=[(1, Pick((1, 2, 3, 4, 8, 9), (0,)))],
        result=LOTTO_RESULT,
    )
    variable = 100_000 * 0.45
    assert settlement.prizes == {1: 2_700 + 4_950 + 7_200}  # 6 + 11 + 16 % de 45.000
    assert settlement.carry == int(5_000 + variable * (0.30 + 0.37))


def test_sin_acertantes_de_4_todo_el_fondo_variable_pasa_al_bote() -> None:
    settlement = settle_pool(
        PRIMITIVA,
        sales=1_000,
        carry=0,
        guarantee=0,
        tickets=[(1, Pick((10, 11, 12, 13, 14, 15), (0,)))],
        result=LOTTO_RESULT,
    )
    assert settlement.prizes == {}
    assert settlement.carry == int(1_000 * 0.45)


def test_los_tres_aciertos_cobran_su_premio_fijo_y_el_reintegro_aparte() -> None:
    settlement = settle_pool(
        PRIMITIVA,
        sales=10,
        carry=0,
        guarantee=0,
        tickets=[(1, Pick((1, 2, 3, 10, 11, 12), (3,)))],
        result=LOTTO_RESULT,
    )
    # 8 € de la 5ª y 1 € de reintegro, aunque la venta no llegue: paga el Estado.
    assert settlement.prizes == {1: 80 + 10}
    assert settlement.labels[1] == ["Reintegro", "5ª (3 aciertos)"]


def test_una_categoria_inferior_nunca_cobra_mas_que_la_superior() -> None:
    assert _no_inversion([100.0, 1_000.0], [10, 1]) == [100.0, 100.0]
    assert _no_inversion([1_000.0, 100.0], [1, 10]) == [1_000.0, 10.0]
    # Tres categorías: se juntan las que hagan falta.
    assert _no_inversion([10.0, 10.0, 100.0], [1, 1, 1]) == pytest.approx([40.0, 40.0, 40.0])


def test_reparto_de_la_primitiva_respeta_el_orden_de_categorias() -> None:
    # Muchos acertantes de 5 y uno solo de 4: la 3ª no puede quedar por debajo.
    tickets = [(i, Pick((1, 2, 3, 4, 5, 9), (0,))) for i in range(50)]
    tickets.append((99, Pick((1, 2, 3, 4, 10, 11), (0,))))
    settlement = settle_pool(
        PRIMITIVA, sales=10_000, carry=0, guarantee=0, tickets=tickets, result=LOTTO_RESULT
    )
    assert settlement.prizes[0] >= settlement.prizes[99]


def test_el_estado_completa_el_bote_garantizado_si_hay_acertante() -> None:
    result = {"numbers": [1, 2, 3, 4, 5], "stars": [1, 2]}
    winner = [(1, Pick((1, 2, 3, 4, 5), (1, 2)))]
    settlement = settle_pool(
        EURO, sales=22, carry=0, guarantee=50_000, tickets=winner, result=result
    )
    assert settlement.prizes == {1: 50_000}
    assert settlement.topup == 50_000 - round(22 * 0.5 * 0.432)


def test_sin_acertante_el_bote_garantizado_no_se_paga_ni_se_acumula() -> None:
    result = {"numbers": [1, 2, 3, 4, 5], "stars": [1, 2]}
    loser = [(1, Pick((10, 20, 30, 40, 45), (9, 10)))]
    settlement = settle_pool(
        EURO, sales=2_200, carry=0, guarantee=50_000, tickets=loser, result=result
    )
    assert settlement.topup == 0
    # Todo el fondo de categorías (sin la reserva del 4,80 %) pasa al bote.
    assert settlement.carry == int(2_200 * 0.5 * (1 - 0.048))


def test_el_bote_garantizado_depende_del_saldo_del_estado() -> None:
    assert guarantee_for(EURO, 1_000) == 250
    assert guarantee_for(EURO, 10**12) == EURO.guarantee
    assert guarantee_for(PRIMITIVA, 10**12) == 0
    assert jackpot_estimate(EURO, sales=0, carry=100, guarantee=250) == 250


def test_premios_de_la_nacional_por_decimo() -> None:
    result = {
        "main": [["1", 48288], ["2", 83206]],
        "pedrea": [],
        "extractions": {"4": [7910], "3": [578], "2": [36, 36]},
        "reintegros": [8, 3, 4],
    }

    def total(number: int) -> int:
        return sum(amount for amount, _ in nacional_prizes(JUEVES, number, result))

    assert total(48288) == 300_000 + 30  # 1er premio y reintegro
    assert total(48287) == 12_000 + 300  # aproximación y centena
    assert total(18288) == 750 + 30  # 4 últimas cifras y reintegro
    assert total(10036) == 2 * 60  # la extracción de 2 cifras salió dos veces
    assert total(12345) == 0
    settlement = settle_nacional(JUEVES, tickets=[(1, 48288, 2)], result=result)
    assert settlement.prizes == {1: 2 * 300_030}


def test_en_navidad_el_gordo_no_cobra_ademas_el_reintegro() -> None:
    result = {"main": [["1", 12345]], "pedrea": [], "extractions": {}, "reintegros": [5]}
    prizes = dict((label, amount) for amount, label in nacional_prizes(NAVIDAD, 12345, result))
    assert prizes == {"EL GORDO": 4_000_000}


# -- Rascas -------------------------------------------------------------------------------


def test_rasca_con_premio_enseña_tres_iguales_y_sin_premio_ninguna_terna() -> None:
    game = GAME_BY_KEY["x10"]
    assert game.scratch is not None
    rng = random.Random(5)
    prize = game.scratch[0][5]
    cells = scratch_grid(game, prize, rng)
    assert len(cells) == 9 and cells.count(prize.amount) == 3
    assert all(cells.count(v) <= 2 for v in set(cells) - {prize.amount})
    empty = scratch_grid(game, None, rng)
    assert all(empty.count(v) <= 2 for v in empty)
    text = scratch_text(game, prize, cells, 0)
    assert text.count("||") >= 20  # todas las casillas tapadas


def test_rascas_salen_con_la_frecuencia_de_la_emision() -> None:
    game = GAME_BY_KEY["7ymedia"]
    rng = random.Random(11)
    wins = sum(scratch(game, rng) is not None for _ in range(20_000))
    assert 0.20 < wins / 20_000 < 0.25  # 1 de cada 4,40


# -- Impuestos ----------------------------------------------------------------------------


def test_gravamen_especial_del_20_por_ciento_sobre_40000_euros() -> None:
    assert LOTTERY_EXEMPT == 400_000
    assert lottery_tax(400_000) == 0
    assert lottery_tax(4_000_000) == 720_000  # Gordo de Navidad: 80.000 € de 400.000 €


# -- Dinero -------------------------------------------------------------------------------


async def test_comprar_paga_al_estado_sin_igic_y_apunta_las_apuestas(tmp_path: Path) -> None:
    economy, lottery = await make(tmp_path)
    draw_at = next_draw(PRIMITIVA, NOW)
    picks = [(Pick((1, 2, 3, 4, 5, 6), (3,)).encode(), 1)] * 3
    (draw_id, owned), balances = await economy.lottery(
        GUILD,
        charges=[(ALICE, 30, "loteria:primitiva")],
        hook=lottery.reserve(
            GUILD, ALICE, game="primitiva", draw_at=draw_at, picks=picks, price=10,
            channel_id=5, now=NOW,
        ),
    )  # fmt: skip
    assert owned == 3 and balances[ALICE] == STARTING_BALANCE - 30
    assert await economy.state_balance(GUILD) == 30
    assert ledger_sum(tmp_path, ALICE) == balances[ALICE]
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == 30
    draw = (await lottery.open_draws(GUILD))[("primitiva", draw_at)]
    assert (draw.id, draw.sales, draw.tickets, draw.channel_id) == (draw_id, 30, 3, 5)


async def test_compra_sin_saldo_o_con_sorteo_cerrado_no_mueve_nada(tmp_path: Path) -> None:
    economy, lottery = await make(tmp_path)
    draw_at = next_draw(PRIMITIVA, NOW)
    with pytest.raises(InsufficientFundsError):
        await economy.lottery(
            GUILD,
            charges=[(ALICE, STARTING_BALANCE + 1, "loteria:primitiva")],
            hook=lottery.reserve(
                GUILD, ALICE, game="primitiva", draw_at=draw_at, picks=[("1|1", 1)], price=10,
                channel_id=None, now=NOW,
            ),
        )  # fmt: skip
    with pytest.raises(LotteryError):
        await economy.lottery(
            GUILD,
            charges=[(ALICE, 10, "loteria:primitiva")],
            hook=lottery.reserve(
                GUILD, ALICE, game="primitiva", draw_at=draw_at, picks=[("1|1", 1)], price=10,
                channel_id=None, now=draw_at,
            ),
        )  # fmt: skip
    assert await economy.balance(GUILD, ALICE) == STARTING_BALANCE
    assert await lottery.open_draws(GUILD) == {}


async def test_no_se_pueden_pasar_del_maximo_por_sorteo(tmp_path: Path) -> None:
    economy, lottery = await make(tmp_path)
    draw_at = next_draw(JUEVES, NOW)
    with pytest.raises(LotteryError):
        await economy.lottery(
            GUILD,
            charges=[(ALICE, 30, "loteria:jueves")],
            hook=lottery.reserve(
                GUILD, ALICE, game="jueves", draw_at=draw_at, picks=[("7|", MAX_PER_DRAW + 1)],
                price=30, channel_id=None, now=NOW,
            ),
        )  # fmt: skip


async def test_premio_gordo_paga_gravamen_al_estado_y_emite_deuda_si_no_llega(
    tmp_path: Path,
) -> None:
    economy, lottery = await make(tmp_path)
    _, balances = await economy.lottery(
        GUILD,
        payouts=[LotteryPayout(ALICE, 4_000_000, 720_000, "navidad")],
        hook=lambda connection: None,
    )
    assert balances[ALICE] == STARTING_BALANCE + 4_000_000 - 720_000
    # El Estado no tenía nada: emite 4.000.000 de deuda y recupera el gravamen.
    assert await economy.state_balance(GUILD) == 720_000
    treasury = await economy.treasury(GUILD, since=0)
    assert treasury.debt == 4_000_000
    assert treasury.collected_total == 720_000
    assert treasury.top_contributors == ((ALICE, 720_000),)
    assert ledger_sum(tmp_path, ALICE) == balances[ALICE]
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == 720_000


async def test_premio_pequeno_sale_del_estado_sin_deuda(tmp_path: Path) -> None:
    economy, lottery = await make(tmp_path)
    await economy.lottery(GUILD, charges=[(BOB, 500, "loteria:x10")], hook=lambda connection: None)
    await economy.lottery(
        GUILD, payouts=[LotteryPayout(ALICE, 200, 0, "x10")], hook=lambda connection: None
    )
    assert await economy.state_balance(GUILD) == 300
    assert (await economy.treasury(GUILD, since=0)).debt == 0


# -- Sorteos con el cog ----------------------------------------------------------------------


def make_cog(economy: EconomyService, lottery: LotteryRepository, clock: Clock) -> Loteria:
    bot = MagicMock()
    bot.get_guild.return_value = None  # sin anuncio ni logros: solo el dinero
    return Loteria(bot, economy, lottery, clock=clock, rng=random.Random(42))


async def buy(
    economy: EconomyService, lottery: LotteryRepository, user: int, game: str, picks: list
) -> None:
    g = GAME_BY_KEY[game]
    units = sum(q for _, q in picks)
    await economy.lottery(
        GUILD,
        charges=[(user, g.price * units, f"loteria:{game}")],
        hook=lottery.reserve(
            GUILD, user, game=game, draw_at=next_draw(g, NOW),
            picks=[(p.encode(), q) for p, q in picks], price=g.price, channel_id=None, now=NOW,
        ),
    )  # fmt: skip


async def test_el_sorteo_reparte_cuadra_el_libro_y_no_se_repite(tmp_path: Path) -> None:
    clock = Clock()
    economy, lottery = await make(tmp_path, clock)
    rng = random.Random(9)
    await buy(economy, lottery, ALICE, "primitiva", [(random_pick(PRIMITIVA, rng), 1)] * 1)
    await buy(
        economy, lottery, BOB, "primitiva", [(random_pick(PRIMITIVA, rng), 1) for _ in range(40)]
    )
    cog = make_cog(economy, lottery, clock)
    assert await cog.run_due_draws() == 0  # aún no es la hora
    clock.now = next_draw(PRIMITIVA, NOW) + 1
    assert await cog.run_due_draws() == 1
    assert await cog.run_due_draws() == 0  # ya está cerrado
    draw = await lottery.last_draw(GUILD, "primitiva")
    assert draw is not None and draw.status == "drawn" and draw.result is not None
    tickets = await lottery.tickets(draw.id)
    paid = sum(t.prize - t.tax for t in tickets)
    assert await economy.balance(GUILD, ALICE) + await economy.balance(GUILD, BOB) == (
        2 * STARTING_BALANCE - 410 + paid
    )
    assert await economy.state_balance(GUILD) == 410 - paid
    for user in (ALICE, BOB, STATE_ACCOUNT_ID):
        assert ledger_sum(tmp_path, user) == (
            await economy.balance(GUILD, user) if user else await economy.state_balance(GUILD)
        )
    # Lo que no se ha repartido del fondo queda de bote para el siguiente.
    assert await lottery.carry(GUILD, "primitiva") == draw.summary["carry"] > 0


async def test_cerrar_dos_veces_el_mismo_sorteo_no_paga_dos_veces(tmp_path: Path) -> None:
    economy, lottery = await make(tmp_path)
    await buy(economy, lottery, ALICE, "jueves", [(Pick((7,)), 1)])
    (draw,) = (await lottery.open_draws(GUILD)).values()
    hook = lottery.close(draw, result={}, summary={}, prizes={}, carry=None, now=NOW)
    await economy.lottery(GUILD, hook=hook)
    with pytest.raises(DrawChanged):
        await economy.lottery(
            GUILD,
            payouts=[LotteryPayout(ALICE, 100, 0, "jueves")],
            hook=lottery.close(draw, result={}, summary={}, prizes={}, carry=None, now=NOW),
        )
    assert await economy.balance(GUILD, ALICE) == STARTING_BALANCE - 30


async def test_sorteo_de_la_nacional_paga_los_decimos(tmp_path: Path) -> None:
    clock = Clock()
    economy, lottery = await make(tmp_path, clock)
    await buy(economy, lottery, ALICE, "jueves", [(Pick((n,)), 1) for n in range(30)])
    cog = make_cog(economy, lottery, clock)
    clock.now = next_draw(JUEVES, NOW) + 1
    assert await cog.run_due_draws() == 1
    draw = await lottery.last_draw(GUILD, "jueves")
    assert draw is not None and draw.result is not None
    tickets = await lottery.tickets(draw.id)
    expected = {
        t.id: sum(
            a for a, _ in nacional_prizes(JUEVES, Pick.decode(t.pick).numbers[0], draw.result)
        )
        for t in tickets
    }
    assert {t.id: t.prize for t in tickets} == expected
    # Al menos los reintegros: 3 terminaciones de cada 10 números.
    assert sum(1 for t in tickets if t.prize) >= 9


# -- Textos y logros -------------------------------------------------------------------------


def test_tabla_de_probabilidades_lista_todas_las_categorias() -> None:
    table = odds_table(EURO)
    assert table.count("1 entre") == 13
    assert "139.838.160" in table


def test_etiquetas_de_logros_de_un_boleto() -> None:
    assert ticket_tags(PRIMITIVA, ["Reintegro", "4ª (4 aciertos)"]) == {"reintegro", "lotto4"}
    assert ticket_tags(PRIMITIVA, ["1ª (6 aciertos)"]) == {"jackpot"}
    assert ticket_tags(NAVIDAD, ["EL GORDO"]) == {"gordo_navidad"}
    assert ticket_tags(NAVIDAD, ["Pedrea", "Reintegro"]) == {"pedrea", "reintegro"}


def test_estadisticas_de_loterias() -> None:
    buy_delta = lottery_buy_stats(
        game="navidad", units=2, cost=400, owned_in_draw=2, balance_after=0
    )
    assert buy_delta.add == {
        "lottery_bets": 2,
        "lottery_spent": 400,
        "lottery_navidad": 2,
        "lottery_game_navidad": 2,
        "lottery_broke_buy": 1,
    }
    prize = lottery_prize_stats([(4_000_000, 720_000, frozenset({"gordo_navidad"}))])
    assert prize.add["lottery_gordo_navidad"] == 1
    assert prize.add["lottery_gravamen"] == 720_000 and prize.add["tax_paid"] == 720_000
    assert prize.peak["lottery_win_max"] == 4_000_000
    rasca = scratch_stats(cost=20, prize=0, tax=0, top=False, balance_after=10)
    assert rasca.add == {"lottery_scratches": 1, "lottery_spent": 20}


async def test_panel_de_inicio_enseña_sorteos_y_botes(tmp_path: Path) -> None:
    economy, lottery = await make(tmp_path)
    cog = make_cog(economy, lottery, Clock())
    text = await cog.tab_text(GUILD, ALICE, "inicio", "jueves")
    assert "Euromillones" in text and "Navidad" in text and "bote" in text
    assert "Primitiva" in await cog.tab_text(GUILD, ALICE, "primitiva", "jueves")
    assert "Nada todavía" in await cog.tab_text(GUILD, ALICE, "mios", "jueves")
