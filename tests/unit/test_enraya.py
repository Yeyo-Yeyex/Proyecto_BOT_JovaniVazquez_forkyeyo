"""Pruebas de bot.services.enraya: reglas del 4 en raya, Jovani, retos del día y grada.

Las partidas se montan con listas de columnas (`played`), igual que las jugaría
alguien, y el azar de Jovani se fuerza con un `random.Random` cuyo primer número
es el que se quiere (no se confía en que dos probabilidades distintas den
resultados distintos).
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from bot.services.enraya import (
    CELLS,
    CHALLENGE_BY_KEY,
    CHALLENGES,
    COLS,
    DEFAULT_LEVEL,
    GRADA_MOVES,
    LEVEL_BY_KEY,
    LEVELS,
    ROWS,
    TIER_REWARDS,
    Board,
    ColumnFullError,
    Disc,
    Ending,
    GameEvent,
    Level,
    PlayerRecord,
    after_game,
    blocks_and_misses,
    daily_challenges,
    grada_odds,
    grada_open,
    grada_payouts,
    grada_totals,
    jovani_move,
    plan_seed,
    progress_for,
    reward_for,
)
from bot.services.gallos import POOL_CUT, Corner, PoolBet
from bot.services.poker_club import STREAK_MAX_PCT, streak_multiplier_pct

#: Las rojas ganan en horizontal, en la fila de abajo, con su cuarta ficha.
HORIZONTAL = [0, 0, 1, 1, 2, 2, 3]
#: Las rojas ganan en vertical en la columna 0.
VERTICAL = [0, 1, 0, 1, 0, 1, 0]
#: Las rojas ganan en la diagonal (0,0)-(3,3) con su sexta ficha.
DIAGONAL = [0, 1, 1, 2, 3, 2, 2, 3, 3, 6, 3]
#: La misma partida en espejo: las rojas ganan en la otra diagonal.
ANTIDIAGONAL = [COLS - 1 - c for c in DIAGONAL[:-1]] + [3]
#: Las rojas juntan cinco en la fila de abajo de una sola ficha (la columna 2).
FIVE = [0, 0, 1, 1, 3, 3, 4, 4, 2]
#: Una sola ficha de las rojas (la última, en la columna 2 o 3) hace una vertical y una diagonal.
DOUBLE = [5, 4, 5, 4, 4, 0, 2, 6, 5, 3, 3, 2, 5]
#: Tablero lleno sin cuatro en raya para nadie.
DRAW = [
    3, 3, 0, 2, 5, 4, 5, 6, 4, 2, 2, 3, 3, 5, 5, 2, 2, 4, 3, 3, 1,
    1, 2, 1, 5, 5, 0, 4, 0, 4, 4, 6, 6, 6, 6, 0, 1, 0, 6, 0, 1, 1,
]  # fmt: skip
#: Las rojas ganan con su octava ficha (partida larga, de 15 jugadas).
LONG_WIN = [0, 1, 1, 2, 5, 1, 3, 0, 5, 0, 0, 2, 4, 2, 6]
#: Las rojas tienen tres en la fila de abajo y le toca a las amarillas: deben tapar la 3.
RED_THREAT = [0, 0, 1, 1, 2]
#: Las amarillas tienen tres en la fila de abajo y les toca: ganan en la 3.
YELLOW_WINS_AT_3 = [6, 0, 5, 1, 6, 2, 5]


def played(columns: list[int]) -> Board:
    board = Board()
    for column in columns:
        board = board.play(column)
    return board


class Forced(random.Random):
    """Azar cuyo primer `random()` es `first` (la tirada del despiste); el resto, de una semilla."""

    def __init__(self, first: float, seed: int = 1) -> None:
        super().__init__(seed)
        self.first: float | None = first

    def random(self) -> float:
        if self.first is not None:
            value, self.first = self.first, None
            return value
        return super().random()


#: Una tirada que ningún nivel considera despiste.
NO_BLUNDER = 0.999


def event(
    ending: Ending,
    columns: list[int],
    *,
    level: str | None = "normal",
    disc: Disc = Disc.RED,
    blocks: int = 0,
    stake: int = 0,
) -> GameEvent:
    return GameEvent(
        ending=ending,
        disc=disc,
        level=LEVEL_BY_KEY[level] if level else None,
        board=played(columns),
        blocks=blocks,
        stake=stake,
    )


# -- Reglas ------------------------------------------------------------------------------


def test_las_fichas_caen_hasta_abajo_y_se_apilan() -> None:
    board = played([2, 2, 2, 5])
    assert [board.at(2, row) for row in range(3)] == [Disc.RED, Disc.YELLOW, Disc.RED]
    assert board.at(2, 3) is None
    assert board.at(5, 0) is Disc.YELLOW
    assert board.heights[2] == 3 and board.heights[5] == 1 and sum(board.heights) == 4
    assert board.last is not None and (board.last.column, board.last.row) == (5, 0)


def test_las_rojas_salen_y_los_turnos_se_alternan() -> None:
    board = Board()
    assert board.turn is Disc.RED
    board = board.play(0)
    assert board.turn is Disc.YELLOW
    assert board.play(0).turn is Disc.RED
    assert board.count(Disc.RED) == 1 and board.count(Disc.YELLOW) == 0


def test_play_no_cambia_el_tablero_de_antes() -> None:
    before = played([3])
    after = before.play(3)
    assert len(before.moves) == 1 and len(after.moves) == 2


def test_una_columna_llena_no_admite_mas_fichas() -> None:
    board = played([0] * ROWS)
    assert board.heights[0] == ROWS and not board.finished
    assert 0 not in board.legal()
    with pytest.raises(ColumnFullError):
        board.play(0)


@pytest.mark.parametrize("column", [-1, COLS])
def test_una_columna_que_no_existe_lanza_columna_llena(column: int) -> None:
    with pytest.raises(ColumnFullError):
        Board().play(column)


def test_no_se_juega_despues_de_acabar_la_partida() -> None:
    board = played(HORIZONTAL)
    assert board.finished
    with pytest.raises(ColumnFullError):
        board.play(6)


@pytest.mark.parametrize(
    ("columns", "direction"),
    [
        (HORIZONTAL, "h"),
        (VERTICAL, "v"),
        (DIAGONAL, "d"),
        (ANTIDIAGONAL, "a"),
    ],
)
def test_gana_quien_junta_cuatro_en_cada_direccion(columns: list[int], direction: str) -> None:
    board = played(columns)
    assert board.winner is Disc.RED and board.finished and not board.full
    assert [line.direction for line in board.winning_lines] == [direction]
    (line,) = board.winning_lines
    assert len(line.cells) == 4
    assert board.winning_cells() == sorted(line.cells)


def test_las_amarillas_tambien_ganan() -> None:
    board = played([*YELLOW_WINS_AT_3, 3])
    assert board.winner is Disc.YELLOW
    assert [line.direction for line in board.winning_lines] == ["h"]


def test_sin_cuatro_en_raya_no_gana_nadie() -> None:
    board = played([0, 1, 2, 3, 4, 5])
    assert board.winner is None and board.winning_lines == () and board.winning_cells() == []
    assert not board.finished


def test_una_raya_de_cinco_cuenta_las_cinco_casillas() -> None:
    board = played(FIVE)
    (line,) = board.winning_lines
    assert line.direction == "h" and len(line.cells) == 5
    assert board.winner is Disc.RED


def test_una_ficha_puede_hacer_dos_rayas_a_la_vez() -> None:
    board = played(DOUBLE)
    assert board.winner is Disc.RED
    assert {line.direction for line in board.winning_lines} == {"v", "d"}
    cells = board.winning_cells()
    assert len(cells) == len(set(cells)) == sum(len(line.cells) for line in board.winning_lines) - 1


def test_el_tablero_lleno_sin_raya_son_tablas() -> None:
    board = played(DRAW)
    assert len(board.moves) == CELLS and board.full
    assert board.winner is None and board.winning_lines == ()
    assert board.finished and board.legal() == []


def test_las_amenazas_son_las_columnas_donde_se_gana_ya() -> None:
    board = played(RED_THREAT)
    assert board.threats(Disc.RED) == [3]
    assert board.threats(Disc.YELLOW) == []
    # Una vertical de tres amenaza la columna de encima.
    vertical = played([0, 1, 0, 1, 0])
    assert vertical.threats(Disc.RED) == [0]


def test_una_amenaza_que_no_se_alcanza_aun_no_cuenta() -> None:
    # Las amarillas tienen tres en la fila 1, pero a la columna 3 le falta una ficha debajo.
    board = played([0, 0, 1, 1, 2, 2])
    assert board.threats(Disc.YELLOW) == []


def test_las_casillas_fuera_del_tablero_estan_vacias() -> None:
    board = played([0])
    assert board.at(-1, 0) is None and board.at(COLS, 0) is None and board.at(0, ROWS) is None


# -- Jovani ------------------------------------------------------------------------------


@pytest.mark.parametrize("level", LEVELS, ids=[lv.key for lv in LEVELS])
@pytest.mark.parametrize(
    ("columns", "expected"),
    [([*RED_THREAT, 6], 3), (YELLOW_WINS_AT_3, 3)],
    ids=["rojas", "amarillas"],
)
def test_jovani_gana_en_el_acto_en_todos_los_niveles(
    level: Level, columns: list[int], expected: int
) -> None:
    board = played(columns)
    assert expected in board.threats(board.turn)
    assert jovani_move(board, level, Forced(NO_BLUNDER)) == expected


@pytest.mark.parametrize("level", [lv for lv in LEVELS if lv.depth >= 2], ids=lambda lv: lv.key)
def test_jovani_tapa_una_amenaza_inmediata_si_piensa_dos_fichas(level: Level) -> None:
    board = played(RED_THREAT)
    assert board.threats(Disc.RED) == [3] and board.turn is Disc.YELLOW
    for seed in range(5):
        assert jovani_move(board, level, Forced(NO_BLUNDER, seed)) == 3


def test_jovani_prefiere_ganar_a_tapar() -> None:
    # Los dos ganan ya: gana quien juega, aunque pueda tapar al otro.
    board = played([0, 3, 0, 3, 0, 3, 5])  # las rojas amenazan la 0 y las amarillas la 3
    assert board.turn is Disc.YELLOW
    assert board.threats(Disc.RED) == [0] and board.threats(Disc.YELLOW) == [3]
    assert jovani_move(board, LEVEL_BY_KEY["imposible"], Forced(NO_BLUNDER)) == 3


@pytest.mark.parametrize("level", LEVELS, ids=[lv.key for lv in LEVELS])
def test_jovani_solo_juega_columnas_con_sitio(level: Level) -> None:
    # Todo el tablero salvo las últimas jugadas: solo queda un par de columnas.
    board = played(DRAW[:-3])
    legal = board.legal()
    assert 0 < len(legal) < COLS
    for seed in range(20):
        assert jovani_move(board, level, random.Random(seed)) in legal


def test_un_despiste_suelta_en_cualquier_columna_con_sitio() -> None:
    board = played(DRAW[:-3])
    legal = board.legal()
    for seed in range(20):
        choice = jovani_move(board, LEVEL_BY_KEY["facil"], Forced(0.0, seed))
        assert choice in legal


def test_un_despiste_ignora_la_mejor_jugada() -> None:
    # Con tirada 0.0 el nivel imposible tampoco se despista (su probabilidad es 0)...
    board = played(RED_THREAT)
    imposible = LEVEL_BY_KEY["imposible"]
    assert imposible.blunder == 0.0
    assert jovani_move(board, imposible, Forced(0.0)) == 3
    # ...pero uno que sí se despista suelta donde le toque el azar, no donde tapa.
    moves = {jovani_move(board, LEVEL_BY_KEY["facil"], Forced(0.0, seed)) for seed in range(60)}
    assert moves - {3}


def test_jovani_es_determinista_con_la_misma_semilla_de_turno() -> None:
    board = played([3, 3, 2])
    for level in LEVELS[:3]:
        first = jovani_move(board, level, plan_seed(1234, 4))
        again = jovani_move(board, level, plan_seed(1234, 4))
        assert first == again


def test_la_semilla_del_turno_depende_de_la_columna_y_de_la_semilla() -> None:
    assert plan_seed(7, 2).random() == plan_seed(7, 2).random()
    assert plan_seed(7, 2).random() != plan_seed(7, 3).random()
    assert plan_seed(7, 2).random() != plan_seed(8, 2).random()


def test_jovani_no_juega_si_la_partida_ha_acabado() -> None:
    with pytest.raises(ColumnFullError):
        jovani_move(played(HORIZONTAL), LEVEL_BY_KEY[DEFAULT_LEVEL], random.Random(1))
    with pytest.raises(ColumnFullError):
        jovani_move(played(DRAW), LEVEL_BY_KEY[DEFAULT_LEVEL], random.Random(1))


def test_los_niveles_son_cuatro_y_piensan_cada_vez_mas() -> None:
    assert [lv.order for lv in LEVELS] == list(range(len(LEVELS)))
    assert [lv.depth for lv in LEVELS] == sorted(lv.depth for lv in LEVELS)
    assert [lv.blunder for lv in LEVELS] == sorted((lv.blunder for lv in LEVELS), reverse=True)
    assert DEFAULT_LEVEL in LEVEL_BY_KEY


# -- tapar y dejar pasar -----------------------------------------------------------------


def test_cuenta_las_rayas_tapadas() -> None:
    board = played([*RED_THREAT, 3])
    assert blocks_and_misses(board, Disc.YELLOW) == (1, 0)
    assert blocks_and_misses(board, Disc.RED) == (0, 0)


def test_cuenta_las_victorias_que_se_dejan_pasar() -> None:
    board = played([*RED_THREAT, 2, 6])  # las rojas pudieron ganar en la 3 y soltaron en la 6
    assert blocks_and_misses(board, Disc.RED) == (0, 1)


def test_ganar_en_el_acto_no_cuenta_como_dejarla_pasar_ni_como_tapar() -> None:
    assert blocks_and_misses(played(HORIZONTAL), Disc.RED) == (0, 0)


# -- Retos del día -----------------------------------------------------------------------


DAY = date(2026, 10, 14)


def test_cada_dia_hay_tres_retos_uno_de_cada_dificultad() -> None:
    challenges = daily_challenges(1, 10, DAY)
    assert [c.tier for c in challenges] == [0, 1, 2]
    assert all(CHALLENGE_BY_KEY[c.key] is c for c in challenges)


def test_los_retos_son_los_mismos_todo_el_dia() -> None:
    assert daily_challenges(1, 10, DAY) == daily_challenges(1, 10, DAY)


def test_los_retos_cambian_de_un_dia_a_otro_para_casi_todos() -> None:
    users = range(100, 140)
    different = sum(
        daily_challenges(1, user, DAY) != daily_challenges(1, user, DAY + timedelta(days=1))
        for user in users
    )
    assert different >= len(users) * 0.9


def test_los_retos_de_dos_miembros_o_servidores_no_son_siempre_iguales() -> None:
    by_user = {daily_challenges(1, user, DAY) for user in range(100, 140)}
    by_guild = {daily_challenges(guild, 10, DAY) for guild in range(1, 41)}
    assert len(by_user) > 1 and len(by_guild) > 1


def test_el_catalogo_de_retos_esta_bien_formado() -> None:
    assert len({c.key for c in CHALLENGES}) == len(CHALLENGES) == len(CHALLENGE_BY_KEY)
    for tier in range(3):
        assert any(c.tier == tier for c in CHALLENGES)
    for challenge in CHALLENGES:
        assert challenge.goal >= 1
        assert challenge.reward == TIER_REWARDS[challenge.tier]
        assert "{goal}" not in challenge.label


def test_los_premios_suben_con_la_dificultad() -> None:
    assert list(TIER_REWARDS) == sorted(TIER_REWARDS)


# Casos de partida y lo que suman a los retos (todos los retos del catálogo).
PROGRESS_CASES = {
    "victoria horizontal contra normal tapando cinco": (
        event(Ending.WIN, HORIZONTAL, level="normal", blocks=5),
        {
            "jugar_3": 1,
            "ganar_2": 1,
            "normal_1": 1,
            "tapar_4": 5,
            "serio_4": 1,
            "rapido_1": 1,
            "tapar_10": 5,
            "serio_8": 1,
        },
    ),
    "victoria vertical contra facil": (
        event(Ending.WIN, VERTICAL, level="facil"),
        {"jugar_3": 1, "ganar_2": 1, "vertical_1": 1},
    ),
    "victoria diagonal contra imposible": (
        event(Ending.WIN, DIAGONAL, level="imposible"),
        {
            "jugar_3": 1,
            "ganar_2": 1,
            "normal_1": 1,
            "serio_4": 1,
            "dificil_1": 1,
            "diagonal_2": 1,
            "rapido_1": 1,
            "imposible_1": 1,
            "dificil_3": 1,
            "serio_8": 1,
            "diagonal_dificil_1": 1,
        },
    ),
    "victoria larga contra normal": (
        event(Ending.WIN, LONG_WIN, level="normal"),
        {"jugar_3": 1, "ganar_2": 1, "normal_1": 1, "serio_4": 1, "serio_8": 1},
    ),
    "derrota contra dificil": (
        event(Ending.LOSS, HORIZONTAL, level="dificil", disc=Disc.YELLOW),
        {"jugar_3": 1},
    ),
    "rendirse": (event(Ending.RESIGN, HORIZONTAL[:5], level="normal"), {"jugar_3": 1}),
    "tablas": (event(Ending.DRAW, DRAW, level="normal"), {"jugar_3": 1}),
    "victoria en duelo": (
        event(Ending.WIN, HORIZONTAL, level=None, stake=500),
        {
            "jugar_3": 1,
            "ganar_2": 1,
            "serio_4": 1,
            "rapido_1": 1,
            "serio_8": 1,
            "duelo_1": 1,
            "duelo_gana_1": 1,
        },
    ),
    "diagonal en duelo": (
        event(Ending.WIN, DIAGONAL, level=None),
        {
            "jugar_3": 1,
            "ganar_2": 1,
            "serio_4": 1,
            "diagonal_2": 1,
            "rapido_1": 1,
            "serio_8": 1,
            "duelo_1": 1,
            "duelo_gana_1": 1,
        },
    ),
    "derrota por tiempo en duelo": (
        event(Ending.TIMEOUT, HORIZONTAL[:5], level=None, disc=Disc.YELLOW),
        {"jugar_3": 1, "duelo_1": 1},
    ),
}


@pytest.mark.parametrize(("game", "expected"), PROGRESS_CASES.values(), ids=PROGRESS_CASES.keys())
def test_cada_partida_suma_lo_que_toca_a_cada_reto(
    game: GameEvent, expected: dict[str, int]
) -> None:
    assert progress_for(CHALLENGES, game) == expected


def test_progress_for_solo_mira_los_retos_que_le_pasan() -> None:
    game = event(Ending.WIN, HORIZONTAL, level="normal")
    mine = (CHALLENGE_BY_KEY["jugar_3"], CHALLENGE_BY_KEY["imposible_1"])
    assert progress_for(mine, game) == {"jugar_3": 1}


def test_las_partidas_ganadas_y_perdidas_se_distinguen() -> None:
    assert event(Ending.WIN, HORIZONTAL).won
    for ending in (Ending.LOSS, Ending.RESIGN, Ending.TIMEOUT):
        assert event(ending, HORIZONTAL).lost and not event(ending, HORIZONTAL).won
    draw = event(Ending.DRAW, DRAW)
    assert not draw.won and not draw.lost
    assert event(Ending.LOSS, HORIZONTAL).directions == frozenset()
    assert event(Ending.WIN, DOUBLE).directions == {"v", "d"}


@pytest.mark.parametrize("streak", range(1, 15))
def test_el_premio_de_un_reto_sube_con_la_racha(streak: int) -> None:
    for challenge in CHALLENGES:
        expected = challenge.reward * (100 + streak_multiplier_pct(streak)) // 100
        assert reward_for(challenge, streak) == expected


def test_la_racha_empieza_en_el_premio_base_y_tiene_tope() -> None:
    challenge = CHALLENGE_BY_KEY["dificil_3"]
    assert reward_for(challenge, 1) == challenge.reward
    assert reward_for(challenge, 2) > challenge.reward
    capped = challenge.reward * (100 + STREAK_MAX_PCT) // 100
    assert reward_for(challenge, 1000) == capped


# -- La ficha ----------------------------------------------------------------------------


def test_ganar_suma_racha_y_la_mejor_racha() -> None:
    record = PlayerRecord()
    for expected in (1, 2, 3):
        record = after_game(record, event(Ending.WIN, HORIZONTAL), DAY)
        assert record.streak == expected and record.best_streak == expected
    assert record.games == record.wins == 3 and record.losses == 0


@pytest.mark.parametrize("ending", [Ending.LOSS, Ending.RESIGN, Ending.TIMEOUT])
def test_perder_rendirse_o_dormirse_corta_la_racha_y_cuenta_derrota(ending: Ending) -> None:
    record = PlayerRecord(games=3, wins=3, streak=3, best_streak=3)
    after = after_game(record, event(ending, HORIZONTAL, disc=Disc.YELLOW), DAY)
    assert (after.streak, after.best_streak) == (0, 3)
    assert (after.games, after.wins, after.losses, after.draws) == (4, 3, 1, 0)


def test_las_tablas_no_tocan_la_racha() -> None:
    record = PlayerRecord(games=2, wins=2, streak=2, best_streak=2)
    after = after_game(record, event(Ending.DRAW, DRAW), DAY)
    assert (after.streak, after.draws, after.losses, after.games) == (2, 1, 0, 3)


def test_los_dias_seguidos_jugando_suben_se_quedan_o_vuelven_a_uno() -> None:
    game = event(Ending.WIN, HORIZONTAL)
    first = after_game(PlayerRecord(), game, DAY)
    assert (first.day_streak, first.last_day) == (1, DAY)
    same_day = after_game(first, game, DAY)
    assert same_day.day_streak == 1
    next_day = after_game(same_day, game, DAY + timedelta(days=1))
    assert next_day.day_streak == 2
    assert after_game(next_day, game, DAY + timedelta(days=2)).day_streak == 3
    skipped = after_game(next_day, game, DAY + timedelta(days=5))
    assert skipped.day_streak == 1 and skipped.last_day == DAY + timedelta(days=5)


def test_el_mejor_nivel_solo_sube_ganando_a_jovani() -> None:
    record = PlayerRecord()
    assert record.best_level == -1
    record = after_game(record, event(Ending.LOSS, HORIZONTAL, level="imposible"), DAY)
    assert record.best_level == -1
    record = after_game(record, event(Ending.WIN, HORIZONTAL, level=None), DAY)
    assert record.best_level == -1
    record = after_game(record, event(Ending.WIN, HORIZONTAL, level="dificil"), DAY)
    assert record.best_level == LEVEL_BY_KEY["dificil"].order
    record = after_game(record, event(Ending.WIN, HORIZONTAL, level="facil"), DAY)
    assert record.best_level == LEVEL_BY_KEY["dificil"].order


# -- La grada ----------------------------------------------------------------------------


def test_la_grada_esta_abierta_hasta_que_hay_las_fichas_de_cierre() -> None:
    assert grada_open(Board())
    assert grada_open(played(list(range(GRADA_MOVES - 1))))
    assert not grada_open(played(list(range(GRADA_MOVES))))
    assert not grada_open(played(HORIZONTAL))


RED, YELLOW = Disc.RED, Disc.YELLOW


def test_la_grada_suma_lo_apostado_a_cada_color() -> None:
    bets = {
        1: PoolBet(RED.corner, 100),
        2: PoolBet(YELLOW.corner, 300),
        3: PoolBet(RED.corner, 50),
    }
    assert grada_totals(bets) == {RED: 150, YELLOW: 300}
    assert grada_totals({}) == {RED: 0, YELLOW: 0}


def test_la_cuota_de_la_grada_es_none_si_nadie_apuesta_a_ese_color() -> None:
    odds = grada_odds({1: PoolBet(RED.corner, 100)})
    assert odds[YELLOW] is None and odds[RED] is not None
    both = grada_odds({1: PoolBet(RED.corner, 100), 2: PoolBet(YELLOW.corner, 100)})
    assert both[RED] == both[YELLOW] == 2 * (1 - POOL_CUT)


def test_la_grada_no_reparte_mas_de_lo_apostado() -> None:
    bets = {
        1: PoolBet(RED.corner, 100),
        2: PoolBet(YELLOW.corner, 333),
        3: PoolBet(RED.corner, 77),
        4: PoolBet(YELLOW.corner, 1),
    }
    pot = sum(bet.stake for bet in bets.values())
    for winner in Disc:
        payouts = grada_payouts(bets, winner)
        assert set(payouts) == set(bets)
        assert sum(payouts.values()) <= pot
        for user, bet in bets.items():
            assert (payouts[user] > 0) == (bet.corner is winner.corner)


def test_quien_acierta_cobra_en_proporcion_a_lo_que_puso() -> None:
    bets = {
        1: PoolBet(RED.corner, 100),
        2: PoolBet(RED.corner, 300),
        3: PoolBet(YELLOW.corner, 400),
    }
    payouts = grada_payouts(bets, RED)
    assert payouts[3] == 0
    assert payouts[2] == pytest.approx(3 * payouts[1], abs=3)
    assert payouts[1] + payouts[2] == pytest.approx(int(800 * (1 - POOL_CUT)), abs=2)


def test_en_tablas_la_grada_recupera_todo() -> None:
    bets = {1: PoolBet(RED.corner, 100), 2: PoolBet(YELLOW.corner, 250)}
    assert grada_payouts(bets, None) == {1: 100, 2: 250}
    assert grada_payouts({}, None) == {}


@pytest.mark.parametrize("winner", [RED, YELLOW])
def test_si_todos_apuestan_al_mismo_color_se_devuelve_lo_apostado(winner: Disc) -> None:
    bets = {1: PoolBet(RED.corner, 100), 2: PoolBet(RED.corner, 40)}
    assert grada_payouts(bets, winner) == {1: 100, 2: 40}


def test_los_colores_se_corresponden_con_las_esquinas_de_los_gallos() -> None:
    assert RED.corner is Corner.RED and YELLOW.corner is Corner.BLUE
    assert RED.other is YELLOW and YELLOW.other is RED
