"""Pruebas de bot.services.slots: líneas, rodillos, retorno de la máquina y calor.

Las cifras del retorno se calculan recorriendo todas las paradas posibles de
los rodillos (unas 29.000), así que son exactas y no dependen del azar. Lo
único simulado es el efecto conjunto de los giros gratis y la máquina
caliente, con una semilla fija.
"""

from __future__ import annotations

import itertools
import random

import pytest

from bot.services.slots import (
    BELL,
    CHERRY,
    DIAMOND,
    FREE_SPINS,
    GRAPE,
    HEAT_MAX,
    LEMON,
    POT_SHARE_PERCENT,
    REEL_STRIPS,
    SCATTER,
    SEVEN,
    SYMBOLS,
    WILD,
    Kind,
    SlotMachine,
    evaluate_line,
    heat_bar,
    line_payout,
    next_heat,
    paytable_lines,
    pot_share,
    spin_at,
)

ALL_STOPS = list(itertools.product(*(range(len(strip)) for strip in REEL_STRIPS)))


def find_stops(predicate) -> tuple[int, int, int]:  # noqa: ANN001
    """Primera parada de los rodillos cuya tirada cumple `predicate`."""
    for stops in ALL_STOPS:
        if predicate(spin_at(stops)):
            return stops
    raise AssertionError("Ninguna parada cumple la condición")


# -- Líneas ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "kind", "symbol", "halves"),
    [
        ((WILD, WILD, WILD), Kind.JACKPOT, WILD, 0),
        ((SEVEN, SEVEN, SEVEN), Kind.THREE, SEVEN, 400),
        ((SEVEN, WILD, SEVEN), Kind.THREE, SEVEN, 400),
        ((WILD, WILD, BELL), Kind.THREE, BELL, 40),
        ((DIAMOND, DIAMOND, DIAMOND), Kind.THREE, DIAMOND, 120),
        ((GRAPE, GRAPE, GRAPE), Kind.THREE, GRAPE, 20),
        ((LEMON, LEMON, LEMON), Kind.THREE, LEMON, 8),
        ((CHERRY, CHERRY, CHERRY), Kind.THREE, CHERRY, 8),
        ((CHERRY, CHERRY, BELL), Kind.TWO_CHERRIES, None, 4),
        ((WILD, CHERRY, BELL), Kind.TWO_CHERRIES, None, 4),
        ((CHERRY, BELL, BELL), Kind.CHERRY, None, 1),
        ((BELL, CHERRY, CHERRY), Kind.NONE, None, 0),
        ((SEVEN, SEVEN, BELL), Kind.NONE, None, 0),
    ],
)
def test_valor_de_cada_linea(line, kind, symbol, halves) -> None:  # noqa: ANN001
    assert evaluate_line(line) == (kind, symbol, halves)


def test_el_ticket_rompe_los_trios_aunque_haya_comodines() -> None:
    assert evaluate_line((WILD, WILD, SCATTER))[0] == Kind.TWO_CHERRIES
    assert evaluate_line((SEVEN, SEVEN, SCATTER))[0] == Kind.NONE


# -- Rodillos -------------------------------------------------------------------------


def test_las_tiras_solo_usan_simbolos_conocidos() -> None:
    for strip in REEL_STRIPS:
        assert set(strip) <= set(SYMBOLS)


def test_nunca_se_ven_dos_tickets_en_el_mismo_rodillo() -> None:
    """Si no, contar "un 🎟️ por rodillo" no sería lo mismo que contar 🎟️."""
    for strip in REEL_STRIPS:
        for index, symbol in enumerate(strip):
            if symbol == SCATTER:
                for offset in (1, 2):
                    assert strip[(index + offset) % len(strip)] != SCATTER


def test_la_cuadricula_sale_de_la_tira_alrededor_de_la_parada() -> None:
    spin = spin_at((0, 0, 0))
    for reel, strip in enumerate(REEL_STRIPS):
        assert spin.grid[0][reel] == strip[-1]
        assert spin.grid[1][reel] == strip[0]
        assert spin.grid[2][reel] == strip[1]


def test_la_maquina_elige_paradas_dentro_de_cada_tira() -> None:
    machine = SlotMachine(random.Random(1).randrange)
    for _ in range(200):
        stops = machine.spin().stops
        assert all(0 <= s < len(strip) for s, strip in zip(stops, REEL_STRIPS, strict=True))


def test_en_los_giros_gratis_los_tickets_no_cuentan() -> None:
    stops = find_stops(lambda s: s.triggers_free_spins)
    assert not spin_at(stops, count_scatters=False).triggers_free_spins


def test_el_jackpot_tiene_su_parada() -> None:
    spin = spin_at(find_stops(lambda s: s.is_jackpot))
    assert spin.line == (WILD, WILD, WILD)
    assert spin.anticipation


def test_casi_premio_con_el_que_faltaba_justo_encima_o_debajo() -> None:
    spin = spin_at(
        find_stops(
            lambda s: (
                s.line[0] == SEVEN
                and s.line[1] == SEVEN
                and s.line[2] != SEVEN
                and SEVEN in (s.grid[0][2], s.grid[2][2])
            )
        )
    )
    assert spin.near_miss
    assert spin.anticipation


def test_un_premio_gordo_en_la_linea_no_es_casi_premio() -> None:
    assert not spin_at(find_stops(lambda s: s.is_jackpot)).near_miss


def test_la_cuadricula_en_emojis_marca_la_linea() -> None:
    text = spin_at((0, 0, 0)).emoji_grid()
    rows = text.splitlines()
    assert len(rows) == 3
    assert rows[1].startswith("▶️") and rows[1].endswith("◀️")


# -- Retorno de la máquina ------------------------------------------------------------


def exact_stats() -> dict[str, float]:
    total = len(ALL_STOPS)
    spins = [spin_at(stops) for stops in ALL_STOPS]
    return {
        "line": sum(s.pay_halves for s in spins) / 2 / total,
        "hit": sum(1 for s in spins if s.pay_halves) / total,
        "ldw": sum(1 for s in spins if 0 < s.pay_halves < 2) / total,
        "jackpot": sum(1 for s in spins if s.is_jackpot) / total,
        "free": sum(1 for s in spins if s.triggers_free_spins) / total,
    }


def test_las_cifras_de_la_documentacion_son_las_reales() -> None:
    stats = exact_stats()
    assert stats["line"] == pytest.approx(0.83, abs=0.005)
    assert stats["hit"] == pytest.approx(0.31, abs=0.01)
    # Dos de cada tres premios devuelven menos de lo apostado.
    assert stats["ldw"] / stats["hit"] == pytest.approx(2 / 3, abs=0.03)
    assert 1 / stats["jackpot"] == pytest.approx(14_400, rel=0.01)
    assert 1 / stats["free"] == pytest.approx(133, rel=0.02)


def test_el_retorno_total_es_el_de_un_casino_de_verdad() -> None:
    """Línea + giros gratis + máquina caliente ≈ 91 %; con el bote, ≈ 94 %.

    Se simula con apuesta 100 para que el medio premio no pierda decimales.
    """
    machine = SlotMachine(random.Random(2026).randrange)
    stake, paid, returned, heat, free = 100, 0, 0, 0, 0
    for _ in range(400_000):
        is_free = free > 0
        if is_free:
            free -= 1
        else:
            paid += stake
        spin = machine.spin(free=is_free)
        hot = heat >= HEAT_MAX
        payout = line_payout(spin, stake, hot=hot)
        returned += payout
        heat = next_heat(heat, paid=payout > 0, was_hot=hot)
        if spin.triggers_free_spins:
            free += FREE_SPINS
    house = returned / paid
    assert 0.88 < house < 0.94
    assert 0.91 < house + POT_SHARE_PERCENT / 100 < 0.97


# -- Dinero y calor -------------------------------------------------------------------


def test_el_medio_premio_y_la_maquina_caliente() -> None:
    cherry = spin_at(find_stops(lambda s: s.kind == Kind.CHERRY))
    assert line_payout(cherry, 100) == 50
    assert line_payout(cherry, 100, hot=True) == 100
    assert line_payout(cherry, 1) == 0


def test_parte_del_bote() -> None:
    assert pot_share(100) == 3
    assert pot_share(33) == 0


def test_el_calor_sube_con_cada_premio_y_la_tirada_caliente_lo_gasta() -> None:
    heat = 0
    for _ in range(HEAT_MAX + 2):
        heat = next_heat(heat, paid=True, was_hot=False)
    assert heat == HEAT_MAX
    assert next_heat(heat, paid=False, was_hot=False) == HEAT_MAX
    assert next_heat(heat, paid=True, was_hot=True) == 0


def test_la_barra_de_calor_usa_formas() -> None:
    assert heat_bar(2) == "🔥🔥▫️▫️▫️"
    assert heat_bar(99) == "🔥" * HEAT_MAX


def test_la_tabla_de_premios_menciona_el_bote_y_los_giros() -> None:
    text = "\n".join(paytable_lines())
    assert "BOTE" in text
    assert f"{FREE_SPINS} giros gratis" in text
