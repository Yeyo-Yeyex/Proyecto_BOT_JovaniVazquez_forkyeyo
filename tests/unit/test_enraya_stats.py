"""Pruebas de los contadores de logros del 4 en raya (`enraya_stats` y `enraya_bet_stats`).

Cada partida se monta con listas de columnas, como las jugaría alguien, y se
mira qué contadores suma. Las fechas se eligen por lo que tienen de especial
(4 de abril, Nochevieja, viernes 13…) y se comprueba antes que lo son.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

import pytest

from bot.services.achievements import (
    CATALOG,
    ENRAYA_BEAT_PREFIX,
    enraya_bet_stats,
    enraya_stats,
)
from bot.services.enraya import LEVEL_BY_KEY, Board, Disc, Ending, GameEvent, PlayerRecord

NOON = datetime(2026, 10, 14, 12, 0)
HORIZONTAL = [0, 0, 1, 1, 2, 2, 3]
VERTICAL = [0, 1, 0, 1, 0, 1, 0]
DIAGONAL = [0, 1, 1, 2, 3, 2, 2, 3, 3, 6, 3]
FIVE = [0, 0, 1, 1, 3, 3, 4, 4, 2]
DOUBLE = [5, 4, 5, 4, 4, 0, 2, 6, 5, 3, 3, 2, 5]
LONG_WIN = [0, 1, 1, 2, 5, 1, 3, 0, 5, 0, 0, 2, 4, 2, 6]
#: Las amarillas ganan con la ficha 42, la que llena el tablero.
FULL_WIN = [
    6, 2, 2, 5, 4, 0, 4, 6, 4, 4, 0, 0, 6, 1, 1, 5, 4, 5, 4, 2, 3,
    1, 5, 5, 2, 1, 6, 2, 0, 6, 2, 1, 1, 6, 0, 5, 0, 3, 3, 3, 3, 3,
]  # fmt: skip
DRAW = [
    3, 3, 0, 2, 5, 4, 5, 6, 4, 2, 2, 3, 3, 5, 5, 2, 2, 4, 3, 3, 1,
    1, 2, 1, 5, 5, 0, 4, 0, 4, 4, 6, 6, 6, 6, 0, 1, 0, 6, 0, 1, 1,
]  # fmt: skip


def played(columns: list[int]) -> Board:
    board = Board()
    for column in columns:
        board = board.play(column)
    return board


def event(
    ending: Ending,
    columns: list[int],
    *,
    level: str | None = "normal",
    disc: Disc = Disc.RED,
    blocks: int = 0,
    missed: int = 0,
    stake: int = 0,
) -> GameEvent:
    return GameEvent(
        ending=ending,
        disc=disc,
        level=LEVEL_BY_KEY[level] if level else None,
        board=played(columns),
        blocks=blocks,
        missed=missed,
        stake=stake,
    )


def stats(game: GameEvent, record: PlayerRecord | None = None, **kwargs):  # noqa: ANN201
    kwargs.setdefault("when", NOON)
    return enraya_stats(game, record or PlayerRecord(), **kwargs)


def test_cada_partida_suma_una_y_lleva_las_rachas_como_maximos() -> None:
    record = PlayerRecord(best_streak=4, day_streak=3)
    delta = stats(event(Ending.LOSS, HORIZONTAL, disc=Disc.YELLOW), record)
    assert delta.add["enraya_games"] == 1
    assert delta.peak == {"enraya_streak_max": 4, "enraya_days_max": 3}


def test_ganar_en_horizontal_contra_un_nivel_cuenta_la_victoria_y_el_nivel() -> None:
    delta = stats(event(Ending.WIN, HORIZONTAL, level="dificil"))
    add = delta.add
    assert add["enraya_wins"] == 1 and add["enraya_win_h"] == 1
    assert add[f"{ENRAYA_BEAT_PREFIX}dificil"] == 1
    assert "enraya_win_v" not in add and "enraya_win_d" not in add
    assert "enraya_losses" not in add and "enraya_duels" not in add
    # Soltó cuatro fichas: raya relámpago.
    assert add["enraya_quick_win"] == 1


@pytest.mark.parametrize(
    ("columns", "counter"),
    [(HORIZONTAL, "enraya_win_h"), (VERTICAL, "enraya_win_v"), (DIAGONAL, "enraya_win_d")],
)
def test_cada_direccion_ganadora_suma_su_contador(columns: list[int], counter: str) -> None:
    delta = stats(event(Ending.WIN, columns))
    assert delta.add[counter] == 1
    others = {"enraya_win_h", "enraya_win_v", "enraya_win_d"} - {counter}
    assert not others & delta.add.keys()


def test_una_raya_larga_o_doble_se_nota() -> None:
    five = stats(event(Ending.WIN, FIVE))
    assert five.add["enraya_five"] == 1 and "enraya_double" not in five.add
    double = stats(event(Ending.WIN, DOUBLE))
    assert double.add["enraya_double"] == 1 and "enraya_five" not in double.add
    assert double.add["enraya_win_v"] == 1 and double.add["enraya_win_d"] == 1


def test_ganar_despacio_no_cuenta_como_relampago() -> None:
    delta = stats(event(Ending.WIN, LONG_WIN))
    assert "enraya_quick_win" not in delta.add


def test_ganar_con_la_ultima_ficha_del_tablero_se_apunta() -> None:
    board = played(FULL_WIN)
    assert board.full and board.winner is Disc.YELLOW
    game = GameEvent(Ending.WIN, Disc.YELLOW, LEVEL_BY_KEY["normal"], board)
    add = stats(game).add
    assert add["enraya_last_piece_win"] == 1 and add["enraya_full_board"] == 1
    # El tablero lleno sin raya cuenta como lleno, pero no como victoria con la última ficha.
    draw = stats(event(Ending.DRAW, DRAW)).add
    assert draw["enraya_full_board"] == 1 and "enraya_last_piece_win" not in draw


def test_perder_rendirse_o_dormirse_se_cuentan_cada_uno_en_lo_suyo() -> None:
    loss = stats(event(Ending.LOSS, HORIZONTAL, disc=Disc.YELLOW))
    assert loss.add["enraya_losses"] == 1
    assert "enraya_resigns" not in loss.add and "enraya_timeouts" not in loss.add
    resign = stats(event(Ending.RESIGN, HORIZONTAL[:5]))
    assert resign.add["enraya_losses"] == 1 and resign.add["enraya_resigns"] == 1
    timeout = stats(event(Ending.TIMEOUT, HORIZONTAL[:5], level=None))
    assert timeout.add["enraya_losses"] == 1 and timeout.add["enraya_timeouts"] == 1


def test_una_derrota_rapida_es_la_que_llega_con_cuatro_fichas_del_rival() -> None:
    quick = stats(event(Ending.LOSS, HORIZONTAL, disc=Disc.YELLOW))
    assert quick.add["enraya_quick_loss"] == 1
    slow = stats(event(Ending.LOSS, LONG_WIN, disc=Disc.YELLOW))
    assert "enraya_quick_loss" not in slow.add


def test_las_tablas_cuentan_y_contra_imposible_tienen_su_logro() -> None:
    draw = stats(event(Ending.DRAW, DRAW, level="normal"))
    assert draw.add["enraya_draws"] == 1 and draw.add["enraya_full_board"] == 1
    assert "enraya_draw_imposible" not in draw.add
    hard = stats(event(Ending.DRAW, DRAW, level="imposible"))
    assert hard.add["enraya_draw_imposible"] == 1


def test_los_logros_de_nivel_dependen_del_nivel_y_de_quien_salio() -> None:
    won = stats(event(Ending.WIN, HORIZONTAL, level="imposible"), jovani_first=True)
    assert won.add[f"{ENRAYA_BEAT_PREFIX}imposible"] == 1
    assert won.add["enraya_beat_imposible_second"] == 1
    first = stats(event(Ending.WIN, HORIZONTAL, level="imposible"), jovani_first=False)
    assert "enraya_beat_imposible_second" not in first.add
    easy = stats(event(Ending.WIN, HORIZONTAL, level="facil"), jovani_first=True)
    assert "enraya_beat_imposible_second" not in easy.add
    lost = stats(event(Ending.LOSS, HORIZONTAL, level="facil", disc=Disc.YELLOW))
    assert lost.add["enraya_lose_facil"] == 1
    other = stats(event(Ending.LOSS, HORIZONTAL, level="normal", disc=Disc.YELLOW))
    assert "enraya_lose_facil" not in other.add


def test_tapar_y_dejar_pasar_se_suman_por_cantidad() -> None:
    delta = stats(event(Ending.LOSS, HORIZONTAL, disc=Disc.YELLOW, blocks=3, missed=2))
    assert delta.add["enraya_blocks"] == 3
    assert delta.add["enraya_missed"] == 2 and delta.add["enraya_missed_loss"] == 1
    won = stats(event(Ending.WIN, HORIZONTAL, missed=1))
    assert won.add["enraya_missed"] == 1 and "enraya_missed_loss" not in won.add
    clean = stats(event(Ending.WIN, HORIZONTAL))
    assert not {"enraya_blocks", "enraya_missed"} & clean.add.keys()


def test_un_duelo_con_apuesta_cuenta_sus_contadores() -> None:
    delta = stats(
        event(Ending.WIN, HORIZONTAL, level=None, stake=500),
        net=450,
        crowd_pool=700,
        rake=25,
    )
    add = delta.add
    assert add["enraya_duels"] == 1 and add["enraya_duel_wins"] == 1
    assert add["enraya_casino_duels"] == 1 and "enraya_friendlies" not in add
    assert add["enraya_rake"] == 25
    assert delta.peak["enraya_win_max"] == 450 and delta.peak["enraya_crowd_max"] == 700
    assert not [key for key in add if key.startswith(ENRAYA_BEAT_PREFIX)]


def test_un_duelo_amistoso_y_uno_grande_o_el_42() -> None:
    friendly = stats(event(Ending.LOSS, HORIZONTAL, level=None, disc=Disc.YELLOW))
    assert friendly.add["enraya_friendlies"] == 1 and "enraya_casino_duels" not in friendly.add
    assert "enraya_duel_wins" not in friendly.add
    big = stats(event(Ending.WIN, HORIZONTAL, level=None, stake=10_000))
    assert big.add["enraya_duel_big"] == 1 and "enraya_stake_42" not in big.add
    odd = stats(event(Ending.WIN, HORIZONTAL, level=None, stake=42))
    assert odd.add["enraya_stake_42"] == 1 and "enraya_duel_big" not in odd.add


def test_perder_un_duelo_no_apunta_ganancia_aunque_haya_grada() -> None:
    delta = stats(
        event(Ending.LOSS, HORIZONTAL, level=None, stake=500, disc=Disc.YELLOW),
        net=-500,
        crowd_pool=300,
    )
    assert "enraya_win_max" not in delta.peak and delta.peak["enraya_crowd_max"] == 300


def test_el_rival_que_se_duerme_y_los_retos_se_pasan_tal_cual() -> None:
    delta = stats(
        event(Ending.WIN, HORIZONTAL, level=None),
        rival_slept=True,
        retos=2,
        retos_full=True,
    )
    assert delta.add["enraya_opp_timeouts"] == 1
    assert delta.add["enraya_retos"] == 2 and delta.add["enraya_retos_full"] == 1
    plain = stats(event(Ending.WIN, HORIZONTAL))
    assert not {"enraya_opp_timeouts", "enraya_retos", "enraya_retos_full"} & plain.add.keys()


@pytest.mark.parametrize(
    ("when", "counter"),
    [
        (datetime(2026, 4, 4, 12), "enraya_april4"),
        (datetime(2026, 12, 31, 12), "enraya_nochevieja"),
        (datetime(2026, 5, 30, 12), "enraya_canarias"),
        (datetime(2026, 10, 14, 3), "enraya_night"),
    ],
)
def test_las_fechas_especiales_tienen_su_contador(when: datetime, counter: str) -> None:
    assert counter in stats(event(Ending.WIN, HORIZONTAL), when=when).add
    assert counter not in stats(event(Ending.WIN, HORIZONTAL), when=NOON).add


def test_la_noche_son_las_horas_de_las_dos_a_las_seis() -> None:
    def night(hour: int) -> bool:
        when = datetime(2026, 10, 14, hour)
        return "enraya_night" in stats(event(Ending.WIN, HORIZONTAL), when=when).add

    assert [night(h) for h in (1, 2, 5, 6)] == [False, True, True, False]


def test_perder_un_viernes_13_se_apunta_y_ganar_no() -> None:
    friday13 = datetime(2026, 2, 13, 12)
    assert friday13.weekday() == 4
    lost = stats(event(Ending.LOSS, HORIZONTAL, disc=Disc.YELLOW), when=friday13)
    assert lost.add["enraya_friday13"] == 1
    won = stats(event(Ending.WIN, HORIZONTAL), when=friday13)
    assert "enraya_friday13" not in won.add
    thursday = datetime(2026, 2, 12, 12)
    assert (
        "enraya_friday13"
        not in stats(event(Ending.LOSS, HORIZONTAL, disc=Disc.YELLOW), when=thursday).add
    )


# -- Apuestas de la grada --------------------------------------------------------------------


def test_acertar_en_la_grada_apunta_la_cuota_maxima() -> None:
    delta = enraya_bet_stats(won=True, refunded=False, odds_cents=285, rake=10, when=NOON)
    assert delta.add == {"enraya_bets": 1, "enraya_bets_won": 1, "enraya_rake": 10}
    assert delta.peak == {"enraya_bet_odds_max": 285}


def test_fallar_en_la_grada_solo_cuenta_la_apuesta() -> None:
    delta = enraya_bet_stats(won=False, refunded=False, odds_cents=0, rake=0, when=NOON)
    assert delta.add == {"enraya_bets": 1} and not delta.peak


def test_una_apuesta_devuelta_no_cuenta_como_acierto() -> None:
    delta = enraya_bet_stats(won=True, refunded=True, odds_cents=0, rake=0, when=NOON)
    assert delta.add == {"enraya_bets": 1, "enraya_bet_refund": 1}
    assert not delta.peak


def test_apostar_de_madrugada_en_la_grada_cuenta_como_noche() -> None:
    night = enraya_bet_stats(
        won=False, refunded=False, odds_cents=0, rake=0, when=datetime(2026, 10, 14, 4)
    )
    assert night.add["enraya_night"] == 1


@pytest.mark.xfail(
    strict=True,
    reason="Seis logros del 4 en raya repiten el nombre de otro (achievements.py: "
    "enraya_win_100, enraya_gran_coalicion, enraya_vertical, enraya_diagonal_50, "
    "enraya_ojo, enraya_contra_todos). Cuando se rebauticen, quitar la marca.",
)
def test_los_logros_del_4_en_raya_no_repiten_el_nombre_de_otro_logro() -> None:
    uses = Counter(a.name for a in CATALOG)
    repeated = sorted(a.id for a in CATALOG if a.category == "enraya" and uses[a.name] > 1)
    assert repeated == []
