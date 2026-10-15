"""Pruebas de bot.services.tower: multiplicadores exactos, retorno del 99 % y partida."""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from bot.services.tower import (
    DEFAULT_DIFFICULTY,
    DIFFICULTIES,
    DIFFICULTY_BY_KEY,
    FLOORS,
    MILESTONES,
    Difficulty,
    Status,
    TowerError,
    TowerGame,
    format_multiplier,
    milestone,
    multiplier,
    multiplier_cents,
    parse_difficulty,
    payout,
    risk_summary,
    survival,
)


def fixed(difficulty: Difficulty, dragon_door: int = 0, stake: int = 100) -> TowerGame:
    """Partida con un único dragón por piso, siempre en la misma puerta."""
    assert difficulty.dragons == 1
    dragons = tuple(frozenset({dragon_door}) for _ in range(FLOORS))
    return TowerGame(stake=stake, difficulty=difficulty, dragons=dragons)


def test_las_cinco_dificultades_y_la_de_por_defecto() -> None:
    assert [d.key for d in DIFFICULTIES] == ["facil", "normal", "dificil", "experto", "maestro"]
    assert [(d.width, d.dragons) for d in DIFFICULTIES] == [(4, 1), (3, 1), (2, 1), (3, 2), (4, 3)]
    assert DEFAULT_DIFFICULTY is Difficulty.EASY
    assert Difficulty("maestro") is Difficulty.MASTER
    assert DIFFICULTY_BY_KEY["dificil"].label == "Difícil"


@pytest.mark.parametrize("difficulty", DIFFICULTIES, ids=lambda d: d.key)
def test_cobrar_en_cualquier_piso_devuelve_el_99_por_ciento(difficulty: Difficulty) -> None:
    assert multiplier(difficulty, 0) == 1
    previous = Fraction(1)
    for floors in range(1, FLOORS + 1):
        mult = multiplier(difficulty, floors)
        chance = Fraction(difficulty.width - difficulty.dragons, difficulty.width) ** floors
        assert survival(difficulty, floors) == chance
        # Valor esperado exacto de «subir k pisos y cobrar»: P(llegar) × multiplicador.
        assert chance * mult == Fraction(99, 100)
        assert mult > previous  # sin tope y siempre creciente
        previous = mult


def test_multiplicadores_conocidos() -> None:
    assert multiplier_cents(Difficulty.EASY, 1) == 132  # 0,99 / (3/4)
    assert multiplier_cents(Difficulty.NORMAL, 1) == 148  # 0,99 / (2/3)
    assert multiplier_cents(Difficulty.HARD, 1) == 198  # 0,99 / (1/2)
    assert multiplier_cents(Difficulty.EXPERT, 1) == 297  # 0,99 / (1/3)
    assert multiplier_cents(Difficulty.MASTER, 1) == 396  # 0,99 / (1/4)
    assert payout(100, Difficulty.EASY, 1) == 132
    assert payout(100, Difficulty.EASY, 0) == 100
    assert format_multiplier(multiplier_cents(Difficulty.EASY, 1)) == "×1,32"


def test_coronar_paga_el_premio_gordo_de_cada_dificultad_sin_tope() -> None:
    tops = {d.key: multiplier(d, FLOORS) for d in DIFFICULTIES}
    assert tops["facil"] == Fraction(99, 100) * Fraction(4, 3) ** 9
    assert tops["normal"] == Fraction(99, 100) * Fraction(3, 2) ** 9
    assert tops["dificil"] == Fraction(99, 100) * 2**9
    assert tops["experto"] == Fraction(99, 100) * 3**9
    assert tops["maestro"] == Fraction(99, 100) * 4**9
    assert multiplier_cents(Difficulty.MASTER, FLOORS) == 25_952_256
    assert payout(100, Difficulty.MASTER, FLOORS) == 25_952_256
    # Más riesgo, más premio gordo.
    ordered = [tops[k] for k in ("facil", "normal", "dificil", "experto", "maestro")]
    assert ordered == sorted(ordered)


def test_los_pisos_deben_caber_en_la_torre() -> None:
    with pytest.raises(ValueError):
        multiplier(Difficulty.EASY, FLOORS + 1)
    with pytest.raises(ValueError):
        multiplier(Difficulty.EASY, -1)


def test_parse_difficulty_acepta_claves_y_nombres() -> None:
    assert parse_difficulty("dificil") is Difficulty.HARD
    assert parse_difficulty(" Difícil ") is Difficulty.HARD
    assert parse_difficulty("FÁCIL") is Difficulty.EASY
    assert parse_difficulty("m") is Difficulty.MASTER
    with pytest.raises(ValueError, match="facil"):
        parse_difficulty("imposible")


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize("difficulty", DIFFICULTIES, ids=lambda d: d.key)
def test_new_sortea_los_dragones_de_los_nueve_pisos_y_no_se_mueven(
    difficulty: Difficulty, seed: int
) -> None:
    game = TowerGame.new(100, difficulty, random.Random(seed))
    assert len(game.dragons) == FLOORS
    assert all(len(floor) == difficulty.dragons for floor in game.dragons)
    assert all(door in range(difficulty.width) for floor in game.dragons for door in floor)
    placed = game.dragons
    door = next(d for d in range(difficulty.width) if d not in game.dragons[0])
    game.climb(door)
    assert game.dragons == placed


def test_new_rechaza_una_apuesta_que_no_es_positiva() -> None:
    with pytest.raises(ValueError):
        TowerGame.new(0, Difficulty.EASY, random.Random(1))


def test_subir_pisos_y_cobrar_paga() -> None:
    game = fixed(Difficulty.NORMAL)
    assert game.cents == 100 and game.floor == 0
    assert game.next_cents == multiplier_cents(Difficulty.NORMAL, 1)
    assert game.safe_chance == Fraction(2, 3)
    assert game.climb(1) and game.climb(2)
    assert game.floor == 2 and game.picks == [1, 2]
    assert game.cashout_value == payout(100, Difficulty.NORMAL, 2)
    assert game.next_value == payout(100, Difficulty.NORMAL, 3)
    paid = game.cash_out()
    assert paid == payout(100, Difficulty.NORMAL, 2)
    assert game.status is Status.CASHED
    assert game.payout == paid and game.net == paid - 100


def test_pisar_un_dragon_lo_pierde_todo() -> None:
    game = fixed(Difficulty.HARD)
    assert game.climb(1)
    assert not game.climb(0)
    assert game.status is Status.BURNED
    assert game.burned == 0 and game.floor == 1
    assert game.payout == 0 and game.net == -100
    with pytest.raises(TowerError):
        game.climb(1)
    with pytest.raises(TowerError):
        game.cash_out()


def test_no_se_puede_cobrar_sin_subir_ni_elegir_una_puerta_inexistente() -> None:
    game = fixed(Difficulty.EASY)
    with pytest.raises(TowerError):
        game.cash_out()
    for door in (-1, 4):
        with pytest.raises(TowerError):
            game.climb(door)
    assert game.playing and game.floor == 0


def test_coronar_la_torre() -> None:
    game = fixed(Difficulty.EASY)
    for _ in range(FLOORS):
        assert not game.crowned
        assert game.climb(3)
    assert game.crowned and game.next_cents is None and game.next_value is None
    assert game.cash_out() == payout(100, Difficulty.EASY, FLOORS)
    assert game.cashout_value == 1_318  # 100 × 0,99 × (4/3)^9 redondeado hacia abajo


def test_al_azar_elige_una_puerta_del_piso_y_lo_cuenta() -> None:
    game = fixed(Difficulty.NORMAL)
    rng = random.Random(3)
    for _ in range(5):
        door = game.random_door(rng)
        assert 0 <= door < game.difficulty.width
        if not game.climb(door, random_pick=True):
            break
    assert game.random_picks >= 1
    game.status = Status.BURNED
    with pytest.raises(TowerError):
        game.random_door(rng)


def test_frases_de_hito_por_piso() -> None:
    for floor, lines in MILESTONES.items():
        assert len(lines) >= 3
        assert milestone(floor, random.Random(1)) in lines
    assert milestone(2) is None
    assert milestone(FLOORS) in MILESTONES[FLOORS]


def test_el_menu_enseña_puertas_dragones_y_el_premio_de_coronar() -> None:
    assert risk_summary(Difficulty.EASY) == "4 puertas, 1 dragón · Coronar: ×13"
    assert risk_summary(Difficulty.HARD) == "2 puertas, 1 dragón · Coronar: ×506"
    assert risk_summary(Difficulty.MASTER) == "4 puertas, 3 dragones · Coronar: ×259.522"
    assert all(len(risk_summary(d)) <= 100 for d in DIFFICULTIES)
