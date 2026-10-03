"""Pruebas de las reglas de la ruleta americana (`bot.services.roulette`)."""

from __future__ import annotations

from fractions import Fraction

import pytest

from bot.services.roulette import (
    BLACK_NUMBERS,
    DOUBLE_ZERO,
    INSIDE_SHAPES,
    OUTSIDE_ALIASES,
    OUTSIDE_BETS,
    POCKETS,
    RED_NUMBERS,
    WHEEL_ORDER,
    Wheel,
    bet_from_key,
    inside_bet,
    label,
    parse_bet,
    play,
)


def all_bets():
    yield from OUTSIDE_BETS.values()
    for numbers in INSIDE_SHAPES:
        yield inside_bet(numbers)


def house_edge(bet) -> Fraction:
    """Ventaja exacta de la casa: lo que pierde de media cada unidad apostada."""
    returned = sum(bet.total_return(1, pocket) for pocket in POCKETS)
    return 1 - Fraction(returned, len(POCKETS))


def test_la_rueda_americana_tiene_38_casillas_distintas() -> None:
    assert sorted(WHEEL_ORDER) == list(POCKETS)
    assert len(RED_NUMBERS) == len(BLACK_NUMBERS) == 18


def test_en_la_rueda_se_alternan_rojo_y_negro_salvo_en_los_ceros() -> None:
    numbers = [p for p in WHEEL_ORDER]
    for current, following in zip(numbers, numbers[1:] + numbers[:1], strict=True):
        if 0 in (current, following) or DOUBLE_ZERO in (current, following):
            continue
        assert (current in RED_NUMBERS) != (following in RED_NUMBERS)


@pytest.mark.parametrize("bet", list(all_bets()), ids=lambda bet: bet.key)
def test_la_casa_gana_el_5_26_por_ciento_en_toda_apuesta_salvo_la_de_cinco(bet) -> None:
    expected = Fraction(3, 38) if len(bet.numbers) == 5 else Fraction(2, 38)
    assert house_edge(bet) == expected


@pytest.mark.parametrize(
    ("size", "payout"), [(1, 35), (2, 17), (3, 11), (4, 8), (5, 6), (6, 5), (12, 2), (18, 1)]
)
def test_pagos_del_tapete_americano(size: int, payout: int) -> None:
    bets = [bet for bet in all_bets() if len(bet.numbers) == size]
    assert bets
    assert {bet.payout for bet in bets} == {payout}


def test_numero_de_combinaciones_interiores_legales() -> None:
    by_size: dict[int, int] = {}
    for numbers in INSIDE_SHAPES:
        by_size[len(numbers)] = by_size.get(len(numbers), 0) + 1
    # 38 plenos; 57 caballos de números + 5 con ceros; 12 transversales + 3
    # tríos; 22 cuadros; 1 línea de cinco; 11 seisenas.
    assert by_size == {1: 38, 2: 62, 3: 15, 4: 22, 5: 1, 6: 11}


def test_ceros_pierden_todas_las_apuestas_exteriores() -> None:
    for bet in OUTSIDE_BETS.values():
        assert not bet.wins(0)
        assert not bet.wins(DOUBLE_ZERO)


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("17", {17}),
        ("0", {0}),
        ("00", {DOUBLE_ZERO}),
        ("0-00", {0, DOUBLE_ZERO}),
        ("17-20", {17, 20}),
        ("13 14 15", {13, 14, 15}),
        ("17,18,20,21", {17, 18, 20, 21}),
        ("0-00-1-2-3", {0, DOUBLE_ZERO, 1, 2, 3}),
        ("00 2 3", {DOUBLE_ZERO, 2, 3}),
    ],
)
def test_parse_bet_interiores(text: str, numbers: set[int]) -> None:
    assert parse_bet(text).numbers == frozenset(numbers)


@pytest.mark.parametrize("text", ["rojo", "NEGRO", "par", "1-18", "19-36", "d2", "13-24", "c3"])
def test_parse_bet_exteriores_por_nombre(text: str) -> None:
    bet = parse_bet(text)
    assert bet is OUTSIDE_BETS[OUTSIDE_ALIASES[text.lower()]]


@pytest.mark.parametrize("text", ["", "azul", "37", "100", "17-19", "1-2-3-4", "3-4", "000"])
def test_parse_bet_rechaza_apuestas_ilegales(text: str) -> None:
    with pytest.raises(ValueError):
        parse_bet(text)


@pytest.mark.parametrize("bet", list(all_bets()), ids=lambda bet: bet.key)
def test_cada_apuesta_se_recupera_desde_su_clave(bet) -> None:
    assert bet_from_key(bet.key) == bet


def test_el_id_de_un_boton_cabe_en_el_limite_de_discord() -> None:
    longest = max(len(bet.key) for bet in all_bets())
    assert longest + len("ruleta:bet:") <= 100


def test_play_paga_pleno_35_a_1() -> None:
    wheel = Wheel(lambda n: 17)

    outcome = play(wheel, parse_bet("17"), 100)

    assert outcome.pocket == 17
    assert outcome.total_return == 3600
    assert outcome.net == 3500
    assert outcome.won


def test_play_pierde_al_rojo_si_sale_00() -> None:
    wheel = Wheel(lambda n: DOUBLE_ZERO)

    outcome = play(wheel, OUTSIDE_BETS["red"], 100)

    assert label(outcome.pocket) == "00"
    assert outcome.total_return == 0
    assert outcome.net == -100


def test_wheel_por_defecto_cubre_las_38_casillas() -> None:
    wheel = Wheel()
    seen = {wheel.spin() for _ in range(5000)}
    assert seen == set(POCKETS)
