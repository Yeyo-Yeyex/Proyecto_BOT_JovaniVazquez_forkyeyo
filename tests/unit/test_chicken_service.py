"""Pruebas de bot.services.chicken: multiplicadores, atropellos y autocobro del Pollo."""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from bot.services.chicken import (
    DIFFICULTIES,
    DIFFICULTY_BY_KEY,
    RTP,
    ChickenError,
    ChickenGame,
    Status,
    auto_targets,
    difficulty_summary,
    draw_hit_lane,
    format_multiplier,
    lanes_for_target,
    milestone,
    multiplier,
    multiplier_cents,
    parse_difficulty,
    payout,
    short_multiplier,
    survival,
)

MEDIA = DIFFICULTY_BY_KEY["media"]
HARDCORE = DIFFICULTY_BY_KEY["hardcore"]


class FixedRandom(random.Random):
    """`random()` devuelve los valores dados, en orden (y luego 0,99)."""

    def __init__(self, values: list[float]) -> None:
        super().__init__(0)
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0) if self._values else 0.99


def game(difficulty=MEDIA, *, hit: int | None, stake: int = 1_000) -> ChickenGame:  # noqa: ANN001
    return ChickenGame(stake=stake, difficulty=difficulty, hit_lane=hit)


@pytest.mark.parametrize("difficulty", DIFFICULTIES, ids=lambda d: d.key)
def test_cobrar_en_cualquier_carril_devuelve_el_99_por_ciento(difficulty) -> None:  # noqa: ANN001
    for lanes in range(1, difficulty.lanes + 1):
        assert survival(difficulty, lanes) * multiplier(difficulty, lanes) == RTP


def test_la_meta_de_cada_dificultad_es_la_anunciada() -> None:
    tops = {d.key: format_multiplier(multiplier_cents(d, d.lanes)) for d in DIFFICULTIES}
    assert tops == {
        "facil": "×2,63",
        "media": "×16,48",
        "dificil": "×85,86",
        "hardcore": "×2.105,55",
    }


def test_mas_dificultad_mas_premio_por_carril() -> None:
    firsts = [multiplier(d, 1) for d in DIFFICULTIES]
    assert firsts == sorted(firsts)


def test_en_la_acera_el_multiplicador_es_uno_y_fuera_de_rango_falla() -> None:
    assert multiplier(MEDIA, 0) == 1
    with pytest.raises(ValueError):
        multiplier(MEDIA, MEDIA.lanes + 1)


def test_el_pago_redondea_hacia_abajo() -> None:
    assert payout(100, MEDIA, 1) == 112  # 0,99 / 0,88 = 1,125
    assert multiplier(MEDIA, 1) == Fraction(99, 88)


def test_el_carril_del_atropello_se_tira_carril_a_carril() -> None:
    # Media atropella con 0,12: 0,5 y 0,3 pasan; 0,05 atropella en el tercero.
    assert draw_hit_lane(MEDIA, FixedRandom([0.5, 0.3, 0.05])) == 3
    assert draw_hit_lane(MEDIA, FixedRandom([0.99] * MEDIA.lanes)) is None


def test_cruzar_hasta_el_coche_y_atropello() -> None:
    g = game(hit=3)
    assert g.cross() and g.cross()
    assert g.crossed == 2 and g.playing
    assert not g.cross()
    assert g.status is Status.SPLAT
    assert g.payout == 0 and g.net == -1_000
    with pytest.raises(ChickenError):
        g.cross()


def test_cobrar_paga_el_multiplicador_y_dice_lo_que_se_dejo() -> None:
    g = game(hit=7)
    for _ in range(3):
        g.cross()
    assert g.cash_out() == payout(1_000, MEDIA, 3)
    assert g.status is Status.CASHED
    assert g.free_lanes_left == 3
    assert g.missed_cents == multiplier_cents(MEDIA, 6)


def test_por_los_pelos_y_carretera_libre() -> None:
    close = game(hit=2)
    close.cross()
    close.cash_out()
    assert close.free_lanes_left == 0
    free = game(hit=None)
    free.cross()
    free.cash_out()
    assert free.free_lanes_left is None
    assert free.missed_cents == multiplier_cents(MEDIA, MEDIA.lanes)


def test_no_se_cobra_desde_la_acera() -> None:
    with pytest.raises(ChickenError):
        game(hit=None).cash_out()


def test_llegar_a_la_meta() -> None:
    g = game(HARDCORE, hit=None)
    for _ in range(HARDCORE.lanes):
        g.cross()
    assert g.finished_road and g.next_cents is None and g.next_value is None
    with pytest.raises(ChickenError):
        g.cross()
    g.cash_out()
    assert g.cents == multiplier_cents(HARDCORE, HARDCORE.lanes)


def test_autocobro_para_al_llegar_al_objetivo() -> None:
    g = game(hit=None)
    hops = g.cross_until(300)
    assert g.cents >= 300 and multiplier_cents(MEDIA, g.crossed - 1) < 300
    assert hops == g.crossed == g.auto_lanes == lanes_for_target(MEDIA, 300)
    assert g.auto_target == 300 and g.playing


def test_autocobro_se_corta_con_el_atropello() -> None:
    g = game(hit=2)
    hops = g.cross_until(1_000)
    assert hops == 2 and g.status is Status.SPLAT and g.crossed == 1


def test_autocobro_ya_superado_falla() -> None:
    g = game(hit=None)
    g.cross_until(150)
    with pytest.raises(ChickenError):
        g.cross_until(120)


def test_objetivos_de_autocobro_por_debajo_de_la_meta() -> None:
    for difficulty in DIFFICULTIES:
        top = multiplier_cents(difficulty, difficulty.lanes)
        targets = auto_targets(difficulty)
        assert targets and all(t < top for t in targets)
    assert 100_000 in auto_targets(HARDCORE)
    assert 1_000 not in auto_targets(DIFFICULTY_BY_KEY["facil"])


def test_partida_nueva_sortea_y_valida_la_apuesta() -> None:
    g = ChickenGame.new(500, MEDIA, FixedRandom([0.5, 0.01]))
    assert g.hit_lane == 2 and g.stake == 500
    with pytest.raises(ValueError):
        ChickenGame.new(0, MEDIA, random.Random(1))


@pytest.mark.parametrize(
    ("text", "key"),
    [("facil", "facil"), ("Fácil", "facil"), ("d", "dificil"), ("hard", "dificil"),
     ("HARDCORE", "hardcore"), ("normal", "media")],
)  # fmt: skip
def test_dificultad_por_nombre_y_alias(text: str, key: str) -> None:
    assert parse_difficulty(text).key == key


def test_dificultad_desconocida() -> None:
    with pytest.raises(ValueError, match="dificultades"):
        parse_difficulty("pesadilla")


def test_textos() -> None:
    assert short_multiplier(112) == "×1,12"
    assert short_multiplier(1_648) == "×16,5"
    assert short_multiplier(210_555) == "×2.105"
    assert milestone(MEDIA.lanes, MEDIA.lanes).startswith("🏁")
    assert milestone(2, 22) is None
    assert difficulty_summary(HARDCORE) == "40 % por carril · 15 carriles · meta ×2.105"
