"""Pruebas de los stats de los gallos: probabilidad, cuota, entrenamiento y muerte.

Son reglas puras de `bot.services.gallos`; el cobro, la base de datos y los botones
tienen las suyas en `test_gallos_repository.py` y `test_gallos_cog.py`.
"""

from __future__ import annotations

import math
import random
from datetime import date
from fractions import Fraction

import pytest

from bot.services.gallos import (
    DEATH_CHANCE,
    HOUSE,
    MAX_WIN,
    MIN_WIN,
    RTP,
    TRAIN_COOLDOWN,
    TRAIN_PER_DAY,
    RoosterRecord,
    Stat,
    TrainError,
    after_train,
    check_training,
    death_chance,
    default_name,
    dies,
    duel_chance,
    house_chance,
    house_payout,
    reborn,
    stat_bar,
    train_gain,
    train_price,
    trains_today,
    win_probability,
)

# -- Probabilidad y cuota -----------------------------------------------------------------


def test_sin_entrenar_cada_rival_juega_como_antes_de_los_stats() -> None:
    for rival in HOUSE:
        assert house_chance(rival, 0) == rival.chance, rival.key
        assert rival.multiplier_for(0) == rival.multiplier, rival.key
        assert house_payout(1000, rival) == int(1000 * rival.multiplier), rival.key


def test_entre_dos_gallos_iguales_cada_uno_gana_la_mitad() -> None:
    assert duel_chance(0, 0) == duel_chance(70, 70) == Fraction(1, 2)


def test_la_probabilidad_sigue_la_formula_del_logit_con_k_3() -> None:
    p0 = Fraction(3, 10)
    for diff in (-80, -25, 10, 50, 100):
        expected = 1 / (1 + math.exp(-(math.log(0.3 / 0.7) + 3 * diff / 100)))
        assert float(win_probability(p0, diff, 0)) == pytest.approx(expected, abs=1e-4)


def test_entrenar_sube_la_probabilidad_y_nunca_sale_de_3_a_97() -> None:
    for rival in HOUSE:
        chances = [house_chance(rival, power) for power in range(0, 101, 10)]
        assert chances == sorted(chances), rival.key
        assert all(MIN_WIN <= c <= MAX_WIN for c in chances), rival.key
    assert win_probability(Fraction(1, 2), 100, -1000) == MAX_WIN
    assert win_probability(Fraction(1, 2), -1000, 100) == MIN_WIN


def test_en_un_duelo_los_dos_lados_suman_uno_y_gana_mas_el_mas_fuerte() -> None:
    assert duel_chance(60, 20) > Fraction(1, 2) > duel_chance(20, 60)
    assert duel_chance(60, 20) + duel_chance(20, 60) == pytest.approx(1, abs=1e-4)


def test_la_cuota_recalculada_mantiene_el_rtp_con_cualquier_poder() -> None:
    for rival in HOUSE:
        for power in (0, 17, 33.3, 50, 75, 100):
            chance = house_chance(rival, power)
            assert chance * rival.multiplier_for(power) == RTP, (rival.key, power)


def test_entrenar_cambia_el_riesgo_no_el_margen() -> None:
    falcon = next(r for r in HOUSE if r.key == "falcon")
    assert falcon.multiplier_for(100) < falcon.multiplier_for(0)
    # Lo esperado por Y$ apostado es el RTP con el gallo que sea (menos el redondeo al Y$).
    stake = 10_000
    for power in (0, 50, 100):
        expected = float(house_chance(falcon, power)) * house_payout(stake, falcon, power)
        assert expected == pytest.approx(float(RTP) * stake, rel=1e-3)


def test_el_poder_de_los_rivales_crece_con_su_dificultad() -> None:
    powers = [r.power for r in HOUSE]
    assert powers == sorted(powers) and len(set(powers)) == len(powers)
    assert all(0 < p <= 100 for p in powers)


def test_el_poder_es_la_media_de_los_tres_stats() -> None:
    record = RoosterRecord(name="Pepe", strength=60, speed=30, endurance=0)
    assert record.power == 30
    assert record.stat(Stat.STRENGTH) == 60 and record.stat(Stat.ENDURANCE) == 0
    assert RoosterRecord(name="Nuevo").power == 0


# -- Entrenamiento ------------------------------------------------------------------------


def test_precio_del_entrenamiento_200_por_uno_mas_nivel_sobre_20_al_cuadrado() -> None:
    assert train_price(0) == 200
    assert train_price(40) == 1800
    assert train_price(100) == 7200
    for level in range(0, 101):
        assert train_price(level) % 10 == 0
        assert abs(train_price(level) - 200 * (1 + level / 20) ** 2) <= 5
    prices = [train_price(level) for level in range(0, 101)]
    assert prices == sorted(prices)


def test_cada_entrenamiento_sube_3_2_o_1_segun_el_nivel() -> None:
    assert [train_gain(level) for level in (0, 39, 40, 69, 70, 99)] == [3, 3, 2, 2, 1, 1]
    day = date(2026, 10, 15)
    record = after_train(RoosterRecord(name="Pepe", strength=38), Stat.STRENGTH, now=1, day=day)
    assert record.strength == 41  # 38 + 3: el salto se decide por el nivel de partida
    record = after_train(record, Stat.STRENGTH, now=2, day=day)
    assert record.strength == 43


def test_un_stat_nunca_pasa_de_100() -> None:
    day = date(2026, 10, 15)
    assert (
        after_train(RoosterRecord(name="Pepe", speed=99), Stat.SPEED, now=0, day=day).speed == 100
    )
    with pytest.raises(TrainError, match="máximo"):
        check_training(RoosterRecord(name="Pepe", speed=100), Stat.SPEED, now=0, day=day)


def test_hay_una_hora_de_espera_entre_entrenamientos() -> None:
    day = date(2026, 10, 15)
    record = after_train(RoosterRecord(name="Pepe"), Stat.STRENGTH, now=100_000, day=day)
    with pytest.raises(TrainError, match="descansa"):
        check_training(record, Stat.SPEED, now=100_000 + TRAIN_COOLDOWN - 1, day=day)
    check_training(record, Stat.SPEED, now=100_000 + TRAIN_COOLDOWN, day=day)


def test_como_mucho_seis_entrenamientos_al_dia_y_al_dia_siguiente_se_reinicia() -> None:
    day = date(2026, 10, 15)
    record = RoosterRecord(name="Pepe")
    now = 1_000_000.0
    for _ in range(TRAIN_PER_DAY):
        check_training(record, Stat.STRENGTH, now=now, day=day)
        record = after_train(record, Stat.STRENGTH, now=now, day=day)
        now += TRAIN_COOLDOWN
    assert trains_today(record, day) == TRAIN_PER_DAY
    with pytest.raises(TrainError, match="entrenamientos"):
        check_training(record, Stat.STRENGTH, now=now, day=day)
    tomorrow = date(2026, 10, 16)
    assert trains_today(record, tomorrow) == 0
    check_training(record, Stat.STRENGTH, now=now, day=tomorrow)


# -- Muerte -------------------------------------------------------------------------------


def test_la_probabilidad_de_morir_baja_con_el_aguante_del_10_al_5_por_ciento() -> None:
    assert death_chance(0) == pytest.approx(DEATH_CHANCE) == pytest.approx(0.10)
    assert death_chance(100) == pytest.approx(0.05)
    assert death_chance(50) == pytest.approx(0.075)


class Roll(random.Random):
    """Azar cuyo único `random()` vale `value`."""

    def __init__(self, value: float) -> None:
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def test_se_muere_si_el_sorteo_cae_por_debajo_de_la_probabilidad() -> None:
    tough = RoosterRecord(name="Roca", endurance=100)
    weak = RoosterRecord(name="Pluma")
    assert dies(weak, Roll(0.09)) and not dies(tough, Roll(0.09))
    assert dies(tough, Roll(0.049)) and not dies(tough, Roll(0.05))


def test_el_gallo_que_sustituye_al_muerto_empieza_a_cero_pero_el_dueno_guarda_su_cupo() -> None:
    day = date(2026, 10, 15)
    dead = RoosterRecord(
        name="Veterano", fights=40, wins=30, kos=9, strength=60, speed=40, endurance=20,
        last_train=5.0, train_day=day, trains_today=3, deaths=1,
    )  # fmt: skip
    new = reborn(dead, 42)
    assert new.name == default_name(42)
    assert (new.fights, new.wins, new.kos, new.streak, new.best_streak) == (0, 0, 0, 0, 0)
    assert (new.strength, new.speed, new.endurance) == (0, 0, 0)
    assert new.deaths == 2
    assert (new.last_train, new.train_day, new.trains_today) == (5.0, day, 3)


def test_barras_de_stat() -> None:
    assert stat_bar(0) == "░" * 10 and stat_bar(100) == "█" * 10
    assert stat_bar(55) == "█" * 6 + "░" * 4


def test_contra_la_casa_la_cuota_nunca_baja_de_1_01() -> None:
    from bot.services.gallos import HOUSE, MAX_HOUSE_WIN, RTP, house_chance

    for rival in HOUSE:
        for power in (0, 50, 100):
            p = house_chance(rival, power)
            assert p <= MAX_HOUSE_WIN
            assert RTP / p >= Fraction(101, 100) - Fraction(1, 1000)
