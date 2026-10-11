"""Pruebas de las combinaciones de Legislatura (`bot.services.legislatura.hands`)."""

from __future__ import annotations

import pytest

from bot.services.legislatura.cards import Card, Enhancement, Suit, build_deck, rank_votes
from bot.services.legislatura.hands import HANDS, PRIORITY, HandType, Rules, evaluate

O, C, E, B = Suit.OROS, Suit.COPAS, Suit.ESPADAS, Suit.BASTOS  # noqa: E741


def cards(*specs: tuple[int, Suit] | tuple[int, Suit, Enhancement]) -> list[Card]:
    return [Card(n, *spec) for n, spec in enumerate(specs, start=1)]  # type: ignore[misc]


def test_la_baraja_de_serie_tiene_40_cartas_con_ids_unicos() -> None:
    deck = build_deck()
    assert len(deck) == 40
    assert len({c.id for c in deck}) == 40
    assert {c.rank for c in deck} == {1, 2, 3, 4, 5, 6, 7, 10, 11, 12}


def test_votos_de_cada_carta_as_11_figuras_10() -> None:
    assert rank_votes(1) == 11
    assert [rank_votes(r) for r in (10, 11, 12)] == [10, 10, 10]
    assert rank_votes(7) == 7


def test_todas_las_combinaciones_tienen_valores_y_prioridad() -> None:
    assert set(HANDS) == set(HandType) == set(PRIORITY)


@pytest.mark.parametrize(
    ("hand", "expected"),
    [
        (cards((3, O)), HandType.CARTA_ALTA),
        (cards((3, O), (3, C)), HandType.PAREJA),
        (cards((3, O), (3, C), (5, E), (5, B)), HandType.DOBLE_PAREJA),
        (cards((11, O), (12, O)), HandType.LAS_CUARENTA),
        (cards((4, O), (4, C), (4, E)), HandType.TRIO),
        (cards((3, O), (4, C), (5, E), (6, B), (7, O)), HandType.ESCALERA),
        (cards((7, O), (10, C), (11, E), (12, B), (1, O)), HandType.ESCALERA),
        (cards((1, O), (2, C), (3, E), (4, B), (5, O)), HandType.ESCALERA),
        (cards((1, O), (3, O), (5, O), (7, O), (12, O)), HandType.COLOR),
        (cards((2, O), (2, C), (2, E), (6, B), (6, O)), HandType.FULL),
        (cards((2, O), (2, C), (2, E), (2, B), (6, O)), HandType.POKER),
        (cards((4, C), (5, C), (6, C), (7, C), (10, C)), HandType.ESCALERA_COLOR),
        (cards((2, O), (2, C), (2, E), (2, B), (2, O)), HandType.REPOKER),
        (cards((2, O), (2, O), (2, O), (6, O), (6, O)), HandType.FULL_COLOR),
        (cards((2, O), (2, O), (2, O), (2, O), (2, O)), HandType.REPOKER_COLOR),
    ],
)
def test_reconoce_cada_combinacion(hand: list[Card], expected: HandType) -> None:
    assert evaluate(hand).hand is expected


def test_la_sota_va_pegada_al_siete_y_no_hay_vuelta_por_el_as() -> None:
    assert evaluate(cards((11, O), (12, C), (1, E), (2, B), (3, O))).hand is HandType.CARTA_ALTA


def test_las_cuarenta_ganan_a_la_doble_pareja_pero_no_al_trio() -> None:
    with_pairs = cards((11, O), (12, O), (11, C), (12, C))
    assert evaluate(with_pairs).hand is HandType.LAS_CUARENTA
    with_trio = cards((11, O), (12, O), (11, C), (11, E))
    assert evaluate(with_trio).hand is HandType.TRIO


def test_las_cuarenta_solo_puntuan_caballo_y_rey() -> None:
    hand = cards((11, O), (12, O), (3, C))
    assert evaluate(hand).scoring == (1, 2)


def test_solo_puntuan_las_cartas_de_la_jugada_y_el_ladrillo_siempre() -> None:
    hand = cards((3, O), (3, C), (7, E), (1, B, Enhancement.LADRILLO))
    result = evaluate(hand)
    assert result.hand is HandType.PAREJA
    assert result.scoring == (1, 2, 4)


def test_el_transfuga_cuenta_como_cualquier_palo() -> None:
    hand = cards((1, O), (3, O), (5, O), (7, O), (12, C, Enhancement.TRANSFUGA))
    assert evaluate(hand).hand is HandType.COLOR


def test_un_full_contiene_trio_y_pareja() -> None:
    result = evaluate(cards((2, O), (2, C), (2, E), (6, B), (6, O)))
    assert {HandType.TRIO, HandType.PAREJA, HandType.DOBLE_PAREJA} <= result.contains


def test_reglas_de_asesores_color_corto_y_huecos() -> None:
    four = cards((1, O), (3, O), (5, O), (7, O))
    assert evaluate(four).hand is HandType.CARTA_ALTA
    assert evaluate(four, Rules(short=True)).hand is HandType.COLOR
    gapped = cards((1, O), (3, C), (5, E), (7, B), (11, O))
    assert evaluate(gapped, Rules(gaps=True)).hand is HandType.ESCALERA


def test_no_se_juegan_mas_de_cinco_ni_ninguna() -> None:
    with pytest.raises(ValueError):
        evaluate([])
    with pytest.raises(ValueError):
        evaluate(build_deck()[:6])


def test_subir_de_nivel_suma_lo_suyo() -> None:
    info = HANDS[HandType.PAREJA]
    assert info.at_level(1) == (info.votes, info.mult)
    assert info.at_level(3) == (info.votes + 2 * info.votes_step, info.mult + 2 * info.mult_step)
