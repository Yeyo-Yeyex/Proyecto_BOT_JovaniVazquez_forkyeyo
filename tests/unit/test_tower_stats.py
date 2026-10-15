"""Pruebas de `tower_stats` y del catálogo de logros de la Torre del dragón."""

from __future__ import annotations

from bot.services.achievements import AVAILABLE, BY_ID, CATEGORY_BY_KEY, Rarity, tower_stats
from bot.services.tower import DIFFICULTIES, FLOORS, Difficulty, TowerGame, multiplier_cents


def game(difficulty: Difficulty = Difficulty.EASY, stake: int = 100) -> TowerGame:
    """Partida con el dragón siempre en la puerta 0 (el resto de puertas son huevos)."""
    dragons = tuple(frozenset({0}) for _ in range(FLOORS))
    return TowerGame(stake=stake, difficulty=difficulty, dragons=dragons)


def climb(g: TowerGame, floors: int, *, dice: bool = False) -> None:
    for _ in range(floors):
        assert g.climb(1, random_pick=dice)


def test_una_partida_cobrada_cuenta_pisos_cobros_y_multiplicador() -> None:
    g = game()
    climb(g, 3)
    g.cash_out()
    delta = tower_stats(g)
    assert delta.add["tower_games"] == 1 and delta.add["tower_games_facil"] == 1
    assert delta.add["tower_floors"] == 3 and delta.add["tower_cashouts"] == 1
    assert delta.peak["tower_floor_max"] == 3 and delta.peak["tower_floors_max_facil"] == 3
    assert delta.peak["tower_mult_max"] == multiplier_cents(Difficulty.EASY, 3)
    assert delta.peak["tower_win_max"] == g.net
    assert "tower_burns" not in delta.add


def test_cobrar_tras_un_piso_y_tras_ocho_se_cuentan_aparte() -> None:
    one = game(Difficulty.MASTER)
    climb(one, 1)
    one.cash_out()
    delta = tower_stats(one)
    assert delta.add["tower_cash_one"] == 1 and delta.add["tower_cash_one_maestro"] == 1
    eight = game()
    climb(eight, FLOORS - 1)
    eight.cash_out()
    assert tower_stats(eight).add["tower_cash_eight"] == 1


def test_quemarse_en_el_primer_piso_de_facil() -> None:
    g = game()
    g.climb(0)
    delta = tower_stats(g)
    assert delta.add["tower_burns"] == 1 and delta.add["tower_first_burn"] == 1
    assert delta.add["tower_easy_first_burn"] == 1
    assert "tower_floors" not in delta.add and "tower_greedy" not in delta.add


def test_quemarse_en_el_primer_piso_de_otra_dificultad_no_es_el_secreto_de_facil() -> None:
    g = game(Difficulty.HARD)
    g.climb(0)
    delta = tower_stats(g)
    assert delta.add["tower_first_burn"] == 1
    assert "tower_easy_first_burn" not in delta.add


def test_quemarse_en_el_noveno_y_cerca_de_la_cima() -> None:
    g = game(Difficulty.HARD)
    climb(g, FLOORS - 1)
    g.climb(0)
    delta = tower_stats(g)
    assert delta.add["tower_ninth_burn"] == 1
    assert "tower_ninth_burn_hard" not in delta.add  # en Difícil no cuenta como «hard»
    assert delta.add["tower_burn_late"] == 1
    # Con ×10 o más ya ganado, el dragón se lo lleva todo.
    assert delta.add["tower_greedy"] == 1
    assert delta.peak["tower_floor_max"] == FLOORS - 1


def test_el_noveno_en_experto_o_maestro_cuenta_como_dificil() -> None:
    dragons = tuple(frozenset({0, 2}) for _ in range(FLOORS))
    g = TowerGame(stake=100, difficulty=Difficulty.EXPERT, dragons=dragons)
    climb(g, FLOORS - 1)
    g.climb(0)
    assert tower_stats(g).add["tower_ninth_burn_hard"] == 1


def test_coronar_cuenta_por_dificultad_y_con_dados() -> None:
    g = game(Difficulty.NORMAL)
    climb(g, FLOORS, dice=True)
    g.cash_out()
    delta = tower_stats(g)
    assert delta.add["tower_crowns"] == 1 and delta.add["tower_crown_normal"] == 1
    assert delta.add["tower_dice_crown"] == 1 and delta.add["tower_random"] == FLOORS
    plain = game(Difficulty.NORMAL)
    climb(plain, FLOORS)
    plain.cash_out()
    assert "tower_dice_crown" not in tower_stats(plain).add


def test_cobrar_una_cifra_con_guasa() -> None:
    # Un piso en Fácil paga ×1,32 de la apuesta, redondeado hacia abajo.
    for stake, amount, stat in (
        (589, 777, "tower_cash_777"),
        (505, 666, "tower_cash_666"),
        (53, 69, "tower_cash_69"),
    ):
        g = game(stake=stake)
        climb(g, 1)
        g.cash_out()
        assert g.payout == amount
        assert tower_stats(g).add.get(stat) == 1
    other = game(stake=100)
    climb(other, 1)
    other.cash_out()
    assert not {"tower_cash_777", "tower_cash_666", "tower_cash_69"} & set(tower_stats(other).add)


def test_la_categoria_y_el_reparto_de_logros() -> None:
    assert CATEGORY_BY_KEY["tower"].group == "casino_group"
    tower = [a for a in AVAILABLE if a.category == "tower"]
    assert len(tower) > 40
    secrets = [a for a in tower if a.secret]
    assert len(tower) / 8 <= len(secrets) * 1.0 <= len(tower) / 4  # más o menos uno de cada seis
    assert {a.rarity for a in tower} == set(Rarity)


def test_coronar_cada_dificultad_es_tan_raro_como_su_probabilidad() -> None:
    """Fácil es 3/4 elevado a 9 (7,5 %); Maestro, 1 entre 262.144: Mítico."""
    order = list(Rarity)
    rarity = {d.key: order.index(BY_ID[f"tower_crown_{d.key}"].rarity) for d in DIFFICULTIES}
    assert rarity["maestro"] == order.index(Rarity.MYTHIC)
    assert list(rarity.values()) == sorted(rarity.values())
    # Lo que se fuerza a propósito es Común o Raro.
    assert BY_ID["tower_all_levels"].rarity in (Rarity.COMMON, Rarity.RARE)
    assert BY_ID["tower_cash_one_25"].rarity in (Rarity.COMMON, Rarity.RARE)
