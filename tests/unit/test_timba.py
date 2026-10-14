"""Pruebas de bot.services.timba: el Texas Hold'em sin límite entre jugadores.

Las cartas se escriben como texto (`"Ah"`, as de corazones; palos s ♠, h ♥, d ♦,
c ♣) y se ponen a mano tras repartir: las ciegas no dependen de ellas.
"""

from __future__ import annotations

import random

import pytest

from bot.services.blackjack import Card
from bot.services.poker import Rank
from bot.services.timba import (
    RAKE_CAP_BB,
    Act,
    Street,
    TimbaError,
    TimbaHand,
    build_pots,
    rake_for,
)

RANKS = {"A": 1, "T": 10, "J": 11, "Q": 12, "K": 13, **{str(n): n for n in range(2, 10)}}
SUITS = {"s": 0, "h": 1, "d": 2, "c": 3}
A, B, C = 1, 2, 3


def cards(text: str) -> tuple[Card, ...]:
    return tuple(Card(RANKS[c[0]], SUITS[c[1]]) for c in text.split())


def hand(
    *stacks: int,
    button: int = 0,
    holes: tuple[str, ...] = (),
    board: str = "2c 7d 9h Js 3c",
    sb: int = 10,
    bb: int = 20,
) -> TimbaHand:
    ids = (A, B, C, 4, 5, 6)
    h = TimbaHand.deal(
        list(zip(ids, stacks, strict=False)),
        button=button,
        small_blind=sb,
        big_blind=bb,
        rng=random.Random(1),
    )
    for player, text in zip(h.players, holes, strict=False):
        player.hole = cards(text)  # type: ignore[assignment]
    h.board = cards(board)
    return h


def stacks(h: TimbaHand) -> dict[int, int]:
    return {p.user_id: p.stack for p in h.players}


# -- Ciegas y turnos -------------------------------------------------------------------


def test_a_tres_ponen_ciegas_los_dos_tras_el_boton_y_habla_el_tercero() -> None:
    h = hand(1000, 1000, 1000)
    assert stacks(h) == {A: 1000, B: 990, C: 980}
    assert h.current is not None and h.current.user_id == A
    assert h.options().to_call == 20


def test_a_dos_el_boton_pone_la_pequena_y_habla_primero_antes_del_flop() -> None:
    h = hand(1000, 1000)
    assert stacks(h) == {A: 990, B: 980}
    assert h.current is not None and h.current.user_id == A
    h.act(A, Act.CALL)
    h.act(B, Act.CHECK)
    assert h.street is Street.FLOP
    assert h.current is not None and h.current.user_id == B, "después del flop habla la grande"


def test_la_ciega_grande_tiene_su_opcion_de_subir() -> None:
    h = hand(1000, 1000, 1000)
    h.act(A, Act.CALL)
    h.act(B, Act.CALL)
    assert h.street is Street.PREFLOP and h.current.user_id == C  # type: ignore[union-attr]
    assert h.options().can_raise
    h.act(C, Act.RAISE, 80)
    assert h.current.user_id == A  # type: ignore[union-attr]


def test_no_es_tu_turno() -> None:
    h = hand(1000, 1000, 1000)
    with pytest.raises(TimbaError, match="turno"):
        h.act(B, Act.CALL)


def test_no_se_puede_pasar_con_algo_que_igualar() -> None:
    h = hand(1000, 1000, 1000)
    with pytest.raises(TimbaError, match="pasar"):
        h.act(A, Act.CHECK)


# -- Subidas ---------------------------------------------------------------------------


def test_la_subida_minima_es_lo_que_subio_el_anterior() -> None:
    h = hand(1000, 1000, 1000)
    with pytest.raises(TimbaError, match="mínima"):
        h.act(A, Act.RAISE, 30)
    h.act(A, Act.RAISE, 60)  # sube 40
    assert h.options().min_raise == 100
    with pytest.raises(TimbaError):
        h.act(B, Act.RAISE, 90)
    h.act(B, Act.RAISE, 100)


def test_una_subida_corta_con_todo_no_reabre_a_quien_ya_hablo() -> None:
    h = hand(1000, 1000, 1000)
    h.act(A, Act.CALL)
    h.act(B, Act.CALL)
    h.act(C, Act.CHECK)
    # Flop: habla B (tras el botón A).
    h.players[2].stack = 130  # C solo tiene 130 más
    h.act(B, Act.RAISE, 100)
    h.act(C, Act.RAISE, 130)  # todo: sube 30, menos que 100
    h.act(A, Act.CALL)  # A no había hablado: podía subir, pero iguala
    opts = h.options()
    assert h.current.user_id == B  # type: ignore[union-attr]
    assert not opts.can_raise and opts.to_call == 30


def test_ir_con_todo_aunque_no_llegue_a_igualar() -> None:
    h = hand(1000, 15, 1000)  # B ni cubre la pequeña entera: pone 10
    h.act(A, Act.CALL)
    h.act(B, Act.CALL)  # le quedan 5: iguala con todo
    assert h.players[1].all_in


# -- Final de la mano ------------------------------------------------------------------


def test_si_todos_se_retiran_la_grande_se_lleva_las_ciegas_sin_comision() -> None:
    h = hand(1000, 1000, 1000)
    h.act(A, Act.FOLD)
    h.act(B, Act.FOLD)
    assert h.finished and h.result is not None
    assert h.result.won == {A: 0, B: 0, C: 30}
    assert h.result.rake == 0 and not h.result.showdown


def test_enfrentamiento_gana_la_mejor_mano_y_la_casa_cobra_su_comision() -> None:
    h = hand(1000, 1000, holes=("Ah Kh", "Qs Qd"), board="Kd 7c 2s 9h 4c")
    h.act(A, Act.CALL)
    h.act(B, Act.CHECK)
    for _ in range(3):  # flop, turn y river: pasan los dos
        h.act(B, Act.CHECK)
        h.act(A, Act.CHECK)
    result = h.result
    assert result is not None and result.showdown
    rake = rake_for(40, 20)
    assert result.won == {A: 40 - rake, B: 0}
    assert result.values[A].rank is Rank.PAIR


def test_la_comision_tiene_tope() -> None:
    assert rake_for(100_000, 20) == RAKE_CAP_BB * 20
    assert rake_for(100, 20) == 5


def test_all_in_antes_del_flop_enseña_todo_y_reparte_botes_secundarios() -> None:
    h = hand(
        100,
        300,
        300,
        holes=("Ah As", "Kh Ks", "Qh Qs"),
        board="2c 7d 9h Js 3c",
    )
    h.act(A, Act.RAISE, 100)  # todo
    h.act(B, Act.RAISE, 300)  # todo
    h.act(C, Act.CALL)  # todo
    result = h.result
    assert result is not None and result.showdown and h.street is Street.SHOWDOWN
    main, side = result.pots
    assert main.eligible == (A, B, C) and main.winners == (A,)
    assert side.eligible == (B, C) and side.winners == (B,)
    assert result.won[A] == 300 - main.rake
    assert result.won[B] == 400 - side.rake
    assert result.won[C] == 0


def test_bote_partido_con_ficha_suelta_para_el_primero_tras_el_boton() -> None:
    h = hand(1000, 1000, 1000, holes=("2h 3h", "2d 3d", "4c 5c"), board="Ah Kd Qc Js Ts")
    h.act(A, Act.CALL)
    h.act(B, Act.CALL)
    h.act(C, Act.RAISE, 21 + 20)  # deja un bote impar
    h.act(A, Act.CALL)
    h.act(B, Act.CALL)
    while not h.finished:
        h.act(h.current.user_id, Act.CHECK)  # type: ignore[union-attr]
    result = h.result
    assert result is not None
    # Los tres juegan la escalera de la mesa: se parte entre los tres.
    assert set(result.pots[0].winners) == {A, B, C}
    total = sum(result.won.values()) + result.rake
    assert total == h.pot
    first_after_button = result.pots[0].winners[0]
    assert first_after_button == B


def test_lo_que_nadie_iguala_se_devuelve() -> None:
    h = hand(1000, 300, holes=("Ah As", "Kh Ks"))
    h.act(A, Act.RAISE, 1000)
    h.act(B, Act.CALL)  # todo con 300
    result = h.result
    assert result is not None
    assert sum(result.won.values()) + result.rake == h.pot
    returned = next(p for p in result.pots if p.eligible == (A,))
    assert returned.amount == 700 and returned.rake == 0


def test_dormirse_pasa_si_puede_y_si_no_se_retira() -> None:
    h = hand(1000, 1000, 1000)
    assert h.timeout().act is Act.FOLD
    h.act(B, Act.CALL)
    assert h.timeout().act is Act.CHECK  # la grande puede pasar


def test_los_botes_cuentan_lo_de_los_retirados_sin_que_puedan_ganarlo() -> None:
    h = hand(1000, 1000, 1000)
    h.players[0].total, h.players[0].folded = 50, True
    h.players[1].total = 100
    h.players[2].total = 100
    assert build_pots(h.players) == [(250, (B, C))]


def test_mil_manos_al_azar_siempre_acaban_y_no_crean_ni_pierden_fichas() -> None:
    rng = random.Random(42)
    for _ in range(1_000):
        n = rng.randint(2, 6)
        start = [rng.choice((40, 100, 500, 2_000)) for _ in range(n)]
        h = TimbaHand.deal(
            list(zip(range(1, n + 1), start, strict=True)),
            button=rng.randrange(n),
            small_blind=10,
            big_blind=20,
            rng=rng,
        )
        for _step in range(200):
            if h.finished:
                break
            player = h.current
            assert player is not None
            opts = h.options()
            roll = rng.random()
            if roll < 0.15:
                h.act(player.user_id, Act.FOLD)
            elif roll < 0.35 and opts.can_raise:
                target = rng.randint(opts.min_raise, opts.max_raise)
                h.act(player.user_id, Act.RAISE, target)
            else:
                h.act(player.user_id, Act.CALL if opts.to_call else Act.CHECK)
        assert h.finished, "una mano no puede quedarse colgada"
        result = h.result
        assert result is not None
        assert all(p.stack >= 0 for p in h.players)
        assert sum(result.won.values()) + result.rake == h.pot
        assert sum(start) == sum(p.stack for p in h.players) + h.pot
