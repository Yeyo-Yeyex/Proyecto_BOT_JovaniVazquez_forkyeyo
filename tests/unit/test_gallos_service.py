"""Pruebas de bot.services.gallos: cuotas, la pelea, los repartos y la ficha del gallo."""

from __future__ import annotations

import random
from datetime import date
from fractions import Fraction

import pytest

from bot.services import skins
from bot.services.gallos import (
    DUEL_CUT,
    HOUSE,
    MAX_HP,
    MIN_POOL_PAYOUT,
    NAME_MAX,
    POOL_CUT,
    RTP,
    SIGNATURE_MOVE,
    Corner,
    Exchange,
    Fight,
    Finish,
    PoolBet,
    RoosterRecord,
    after_fight,
    clean_name,
    default_name,
    draw_winner,
    duel_prize,
    format_odds,
    house_multiplier,
    house_payout,
    move_name,
    narrate,
    pool_odds,
    pool_payouts,
    simulate,
)

RED, BLUE = Corner.RED, Corner.BLUE


# -- Cuotas de la casa -------------------------------------------------------------------


def test_cada_rival_devuelve_de_media_el_rtp() -> None:
    for rival in HOUSE:
        assert rival.chance * rival.multiplier == RTP, rival.key


def test_los_rivales_van_del_mas_facil_al_mas_dificil_y_no_se_repiten() -> None:
    chances = [r.chance for r in HOUSE]
    assert chances == sorted(chances, reverse=True)
    assert len({r.key for r in HOUSE}) == len(HOUSE)
    assert len({r.name for r in HOUSE}) == len(HOUSE)


def test_el_aspecto_de_los_rivales_solo_usa_parametros_del_gallo() -> None:
    defaults = skins.GAMES["gallos"].defaults
    for rival in HOUSE:
        assert set(rival.look) <= set(defaults), rival.key


def test_cuota_y_premio_redondean_hacia_abajo() -> None:
    assert house_multiplier(Fraction(1, 2)) == Fraction(19, 10)
    falcon = next(r for r in HOUSE if r.key == "falcon")
    assert house_payout(1000, falcon) == 1900
    assert house_payout(1, falcon) == 1
    with pytest.raises(ValueError):
        house_multiplier(Fraction(1))


def test_formato_de_cuotas() -> None:
    assert format_odds(Fraction(19, 10)) == "×1,90"
    assert format_odds(Fraction(12345, 10)) == "×1.234,50"


# -- La pelea ----------------------------------------------------------------------------


@pytest.mark.parametrize("winner", list(Corner))
def test_la_pelea_la_gana_quien_se_sortea(winner: Corner) -> None:
    rng = random.Random(3)
    for _ in range(300):
        fight = simulate(winner, rng)
        assert fight.winner is winner
        assert fight.hp(winner) > 0
        if fight.finish is Finish.KO:
            assert fight.hp(fight.loser) == 0
            last = fight.exchanges[-1]
            assert last.attacker is winner and not last.dodged
        if fight.finish is Finish.POINTS:
            assert fight.hp(winner) >= fight.hp(fight.loser)


def test_la_vida_baja_con_cada_golpe_y_la_esquiva_no_quita_nada() -> None:
    rng = random.Random(5)
    for _ in range(200):
        fight = simulate(RED, rng)
        hp = {RED: MAX_HP, BLUE: MAX_HP}
        for exchange in fight.exchanges:
            target = exchange.attacker.other
            if exchange.dodged:
                assert exchange.damage == 0 and not exchange.crit
            hp[target] = max(0, hp[target] - exchange.damage)
            assert exchange.hp == (hp[RED], hp[BLUE])


def test_dar_la_vuelta_cambia_las_esquinas_y_nada_mas() -> None:
    fight = simulate(RED, random.Random(9))
    mirrored = fight.mirrored()
    assert mirrored.winner is BLUE and mirrored.finish is fight.finish
    for a, b in zip(fight.exchanges, mirrored.exchanges, strict=True):
        assert b.attacker is a.attacker.other
        assert b.hp == (a.hp[1], a.hp[0])
        assert (b.move, b.damage, b.crit, b.dodged, b.round) == (
            a.move,
            a.damage,
            a.crit,
            a.dodged,
            a.round,
        )


def test_el_sorteo_respeta_la_probabilidad() -> None:
    rng = random.Random(11)
    wins = sum(draw_winner(Fraction(3, 10), rng) is RED for _ in range(20_000))
    assert 0.28 < wins / 20_000 < 0.32


def test_hay_peleas_de_los_tres_finales() -> None:
    rng = random.Random(13)
    finishes = {simulate(RED, rng).finish for _ in range(500)}
    assert finishes == set(Finish)


def test_el_golpe_estrella_cambia_el_nombre_de_la_patada() -> None:
    kick = Exchange(1, BLUE, SIGNATURE_MOVE, 30, False, False, (70, 100))
    assert move_name(kick, {BLUE: "Arancel del 200 %"}) == "Arancel del 200 %"
    assert move_name(kick, {}) == "Patada voladora"


def test_la_cronica_cuenta_el_final() -> None:
    names = {RED: "Pepe", BLUE: "Arancel"}
    ko = Fight(RED, Finish.KO, (Exchange(1, RED, "patada", 100, True, False, (100, 0)),))
    assert any("estrellitas" in line for line in narrate(ko, names))
    fled = Fight(BLUE, Finish.FLED, (Exchange(2, BLUE, "picotazo", 80, False, False, (20, 100)),))
    assert any("por patas" in line and "**Pepe**" in line for line in narrate(fled, names))
    points = Fight(RED, Finish.POINTS, (Exchange(3, RED, "aletazo", 10, False, False, (100, 90)),))
    assert any("puntos" in line for line in narrate(points, names))


# -- Duelo y grada -----------------------------------------------------------------------


def test_el_duelo_se_lleva_el_bote_menos_la_comision() -> None:
    assert duel_prize(500) == int(1000 * (1 - DUEL_CUT)) == 950


def test_la_grada_reparte_entre_los_que_aciertan_menos_la_comision() -> None:
    bets = {1: PoolBet(RED, 100), 2: PoolBet(BLUE, 300), 3: PoolBet(RED, 50)}
    payouts = pool_payouts(bets, RED)
    odds = (450 * (1 - POOL_CUT)) / 150
    assert payouts == {1: int(100 * odds), 2: 0, 3: int(50 * odds)}
    # No crea dinero: lo pagado no pasa de lo apostado.
    assert sum(payouts.values()) <= 450


def test_la_grada_sin_rival_devuelve_lo_apostado() -> None:
    bets = {1: PoolBet(RED, 100), 2: PoolBet(RED, 40)}
    assert pool_payouts(bets, BLUE) == {1: 100, 2: 40}
    assert pool_odds(bets)[BLUE] is None


def test_quien_acierta_nunca_cobra_menos_de_lo_que_apuesta() -> None:
    bets = {1: PoolBet(RED, 10_000), 2: PoolBet(BLUE, 1)}
    assert pool_odds(bets)[RED] == MIN_POOL_PAYOUT
    assert pool_payouts(bets, RED)[1] == 10_000


# -- Ficha del gallo ---------------------------------------------------------------------


def test_la_ficha_cuenta_rachas_ko_y_dias_seguidos() -> None:
    record = RoosterRecord(name="Pepe")
    day = date(2026, 10, 12)
    record = after_fight(record, won=True, finish=Finish.KO, day=day)
    record = after_fight(record, won=True, finish=Finish.POINTS, day=day)
    assert (record.fights, record.wins, record.kos, record.streak, record.day_streak) == (
        2,
        2,
        1,
        2,
        1,
    )
    record = after_fight(record, won=False, finish=Finish.FLED, day=date(2026, 10, 13))
    assert (record.streak, record.best_streak, record.day_streak, record.losses) == (0, 2, 2, 1)
    record = after_fight(record, won=True, finish=Finish.KO, day=date(2026, 10, 15))
    assert record.day_streak == 1


def test_nombre_de_serie_estable_y_nombre_limpio() -> None:
    assert default_name(42) == default_name(42)
    assert clean_name("  El   **Kiki** <@123> ") == "El Kiki 123"
    with pytest.raises(ValueError):
        clean_name("``**``")
    with pytest.raises(ValueError):
        clean_name("x" * (NAME_MAX + 1))
