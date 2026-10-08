"""Pruebas de bot.services.horses: la carrera, las cuotas, los boletos y el Gran Premio."""

from __future__ import annotations

import numpy as np
import pytest

from bot.services import horses as h
from bot.services.horses import (
    FIELD_SIZE,
    GRAND_PRIX_COOLDOWN,
    GRAND_PRIX_EVERY,
    GRAND_PRIX_FIELD,
    GRAND_PRIX_MIN_STAKE,
    GRAND_PRIX_POT,
    GRAND_PRIX_POT_MAX,
    GRAND_PRIX_POT_STEP,
    RTP,
    SEGMENTS,
    STABLE,
    STABLE_BY_KEY,
    BetKind,
    Going,
    HorseError,
    HorseRecord,
    Pick,
    RaceCard,
    RaceResult,
)


def card_of(*keys: str, going: Going = Going.SECO, distance: int = 1_600, **kw) -> RaceCard:  # noqa: ANN003
    return RaceCard(
        horses=tuple(STABLE_BY_KEY[k] for k in keys), going=going, distance=distance, **kw
    )


SIX = ("falcon", "manual", "paguita", "gofio", "fango", "uco")


def fixed_result(order: tuple[int, ...], gaps: tuple[float, ...] | None = None) -> RaceResult:
    """Un resultado inventado: cada caballo a ritmo constante, con el orden pedido."""
    size = len(order)
    gaps = gaps or tuple(float(i) for i in range(size))
    times = [0.0] * size
    for place, index in enumerate(order):
        times[index] = 100.0 + gaps[place]
    splits = tuple(tuple(t * s / SEGMENTS for s in range(SEGMENTS + 1)) for t in times)
    return RaceResult(order=order, times=tuple(times), splits=splits)


# -- El establo ---------------------------------------------------------------------------


def test_el_establo_tiene_claves_y_nombres_unicos() -> None:
    assert len({x.key for x in STABLE}) == len(STABLE) == 16
    assert len({x.name for x in STABLE}) == len(STABLE)


def test_los_rasgos_estan_en_su_rango() -> None:
    for horse in STABLE:
        assert 0.98 < horse.speed < 1.03, horse.key
        assert 0 < horse.stamina <= 1, horse.key
        assert 0 <= horse.start <= 1, horse.key
        assert 0 <= horse.consistency <= 1, horse.key
        assert horse.pattern in {"liso", "banda", "rayas", "lunares"}, horse.key


def test_en_carreras_largas_se_aguanta_menos() -> None:
    falcon = STABLE_BY_KEY["falcon"]
    assert h.effective_stamina(falcon, 2_400) < h.effective_stamina(falcon, 1_200)
    assert h.effective_stamina(STABLE_BY_KEY["manual"], 1_200) == 1.0


def test_el_terreno_se_moja_un_escalon_y_el_barro_no_mas() -> None:
    assert Going.SECO.wetter() is Going.BLANDO
    assert Going.BLANDO.wetter() is Going.BARRO
    assert Going.BARRO.wetter() is Going.BARRO


# -- La carrera ---------------------------------------------------------------------------


def test_la_misma_semilla_da_la_misma_carrera() -> None:
    card = card_of(*SIX)
    a = h.run_race(card, np.random.default_rng(3))
    b = h.run_race(card, np.random.default_rng(3))
    assert a == b


def test_el_orden_sale_de_los_tiempos() -> None:
    card = card_of(*SIX)
    result = h.run_race(card, np.random.default_rng(1))
    times = [result.times[i] for i in result.order]
    assert times == sorted(times)
    assert sorted(result.order) == list(range(6))
    assert result.lengths_behind(result.winner, card.distance) == 0


def test_la_posicion_recorre_la_distancia_y_sigue_despues_de_la_meta() -> None:
    card = card_of(*SIX)
    result = h.run_race(card, np.random.default_rng(1))
    index = result.order[-1]
    assert result.position_at(index, 0, card.distance) == 0
    assert result.position_at(index, result.times[index], card.distance) == pytest.approx(1_600)
    assert result.position_at(index, result.times[index] + 5, card.distance) > 1_600


def test_un_caballo_mucho_mejor_gana_casi_siempre() -> None:
    """Los rasgos importan: no es un sorteo con la misma probabilidad para todos."""
    rng = np.random.default_rng(5)
    card = card_of(*SIX, going=Going.SECO, distance=1_200)
    odds = h.estimate(card, rng, trials=20_000)
    assert max(odds.win) > 2 / 6
    assert min(odds.win) < 1 / 12


def test_el_terreno_cambia_las_probabilidades() -> None:
    rng = np.random.default_rng(5)
    dry = h.estimate(card_of(*SIX, going=Going.SECO), rng, trials=20_000)
    mud = h.estimate(card_of(*SIX, going=Going.BARRO), rng, trials=20_000)
    fango = SIX.index("fango")
    assert mud.win[fango] > dry.win[fango] * 1.5


def test_un_caballo_cansado_baja_sus_probabilidades() -> None:
    rng = np.random.default_rng(5)
    fresh = h.estimate(card_of(*SIX), rng, trials=20_000)
    tired = h.estimate(card_of(*SIX, tired=frozenset({"falcon"})), rng, trials=20_000)
    assert tired.win[0] < fresh.win[0]


def test_las_probabilidades_suman_lo_que_deben() -> None:
    odds = h.estimate(card_of(*SIX), np.random.default_rng(2), trials=5_000)
    assert sum(odds.win) == pytest.approx(1)
    assert sum(odds.place) == pytest.approx(2)
    assert sum(odds.exacta) == pytest.approx(1)
    assert sum(odds.trifecta) == pytest.approx(1)
    gp = card_of(*SIX, "timple", "wepa", distance=2_400, grand_prix=True)
    assert sum(h.estimate(gp, np.random.default_rng(2), trials=5_000).place) == pytest.approx(3)


@pytest.mark.parametrize("kind", list(BetKind))
def test_cada_tipo_de_boleto_devuelve_su_rtp(kind: BetKind) -> None:
    """Apostar siempre al mismo boleto devuelve de media su RTP (con margen de muestreo).

    Se estiman las cuotas con un azar y se corren carreras con otro, como en el cog.
    """
    card = card_of(*SIX, going=Going.BLANDO, rain_chance=0.3)
    odds = h.estimate(card, np.random.default_rng(11), trials=80_000)
    picks = {
        BetKind.WIN: Pick(kind, (odds.favourite(),)),
        BetKind.PLACE: Pick(kind, (2,)),
        BetKind.EXACTA: Pick(kind, (0, 3)),
        BetKind.TRIFECTA: Pick(kind, (0, 3, 2)),
    }
    pick = picks[kind]
    cents = odds.odds(pick)
    rng = np.random.default_rng(99)
    races = 6_000 if kind in (BetKind.WIN, BetKind.PLACE) else 30_000
    v, _ = h._speeds(card, rng, races)
    times = (card.distance / SEGMENTS / v).sum(axis=2)
    orders = np.argsort(times, axis=1)
    hits = sum(pick.wins(tuple(int(x) for x in row)) for row in orders)
    returned = hits * cents / 100 / races
    assert returned == pytest.approx(RTP[kind.key], abs=0.06)


def test_cuotas_con_topes() -> None:
    assert h.odds_cents(0.0, 0.95) == h.MAX_ODDS
    assert h.odds_cents(0.99, 0.95) == h.MIN_ODDS
    assert h.odds_cents(0.5, 0.95) == 190
    assert h.payout(333, 190) == 632


def test_format_odds() -> None:
    assert h.format_odds(420) == "4,20x"
    assert h.format_odds(101) == "1,01x"
    assert h.format_odds(125_000) == "1.250x"


# -- Boletos ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind_text", "kind", "horses"),
    [
        ("3", None, BetKind.WIN, (2,)),
        ("3c", None, BetKind.PLACE, (2,)),
        ("3", "colocado", BetKind.PLACE, (2,)),
        ("3-5", None, BetKind.EXACTA, (2, 4)),
        ("3/5/1", None, BetKind.TRIFECTA, (2, 4, 0)),
        ("6", "ganador", BetKind.WIN, (5,)),
    ],
)
def test_parse_pick(text: str, kind_text: str | None, kind: BetKind, horses: tuple) -> None:
    pick = h.parse_pick(text, 6, kind_text)
    assert pick == Pick(kind, horses)


@pytest.mark.parametrize(
    ("text", "kind_text"),
    [("7", None), ("0", None), ("3-3", None), ("3-5", "ganador"), ("x", None), ("3", "quiniela")],
)
def test_parse_pick_rechaza_lo_que_no_vale(text: str, kind_text: str | None) -> None:
    with pytest.raises(HorseError):
        h.parse_pick(text, 6, kind_text)


def test_quien_cobra_cada_boleto() -> None:
    order = (2, 4, 0, 1, 3, 5)
    assert Pick(BetKind.WIN, (2,)).wins(order)
    assert not Pick(BetKind.WIN, (4,)).wins(order)
    assert Pick(BetKind.PLACE, (4,)).wins(order)
    assert not Pick(BetKind.PLACE, (0,)).wins(order)  # con 6 caballos pagan 2 puestos
    assert Pick(BetKind.PLACE, (0,)).wins((2, 4, 0, 1, 3, 5, 6, 7))  # con 8, pagan 3
    assert Pick(BetKind.EXACTA, (2, 4)).wins(order)
    assert not Pick(BetKind.EXACTA, (4, 2)).wins(order)
    assert Pick(BetKind.TRIFECTA, (2, 4, 0)).wins(order)
    assert not Pick(BetKind.TRIFECTA, (2, 0, 4)).wins(order)


def test_etiqueta_del_boleto() -> None:
    assert Pick(BetKind.TRIFECTA, (2, 4, 0)).label() == "🏆 Trío 3-5-1"


# -- Narración ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lengths", "text"),
    [(0.01, "un hocico"), (0.1, "una nariz"), (0.2, "una cabeza"), (0.6, "medio cuerpo"),
     (1.0, "un cuerpo"), (2.2, "dos cuerpos"), (14, "14 cuerpos")],
)  # fmt: skip
def test_margin_text(lengths: float, text: str) -> None:
    assert h.margin_text(lengths) == text


def test_la_narracion_cuenta_al_ganador_y_los_sucesos() -> None:
    card = card_of(*SIX)
    result = fixed_result((3, 0, 1, 2, 4, 5))
    result = RaceResult(
        order=result.order,
        times=result.times,
        splits=result.splits,
        stumbles={1: 4},
        bolted={5: 6},
        rained=True,
    )
    text = "\n".join(h.commentary(card, result))
    assert "Gofio Express" in text.splitlines()[-1]
    assert "tropieza" in text
    assert "desbocado" in text
    assert "llover" in text


def test_la_narracion_avisa_del_foto_finish() -> None:
    card = card_of(*SIX)
    result = fixed_result((3, 0, 1, 2, 4, 5), gaps=(0.0, 0.01, 2, 3, 4, 5))
    assert h.photo_finish(result, card.distance)
    assert "Foto-finish" in h.commentary(card, result)[-1]


def test_lo_que_se_te_escapo() -> None:
    card = card_of(*SIX)
    odds = h.estimate(card, np.random.default_rng(1), trials=5_000)
    result = fixed_result((3, 0, 1, 2, 4, 5), gaps=(0.0, 0.05, 2, 3, 4, 5))
    second = h.near_miss(card, result, odds, Pick(BetKind.WIN, (0,)), 100)
    assert second is not None and "Entró 2º" in second
    reversed_ = h.near_miss(card, result, odds, Pick(BetKind.EXACTA, (0, 3)), 100)
    assert reversed_ is not None and "4-1" in reversed_
    assert h.near_miss(card, result, odds, Pick(BetKind.WIN, (3,)), 100) is None
    assert h.near_miss(card, result, odds, Pick(BetKind.WIN, (5,)), 100) is None


def test_remontada_es_ir_ultimo_a_mitad_de_carrera() -> None:
    card = card_of(*SIX)
    # El 0 va el último a mitad (tramos lentos) y acelera al final.
    base = fixed_result((1, 2, 3, 4, 5, 0))
    splits = list(base.splits)
    splits[0] = tuple([0.0] + [12.0 * s for s in range(1, 6)] + [60 + 9.0 * s for s in range(1, 6)])
    times = list(base.times)
    times[0] = splits[0][-1]
    result = RaceResult(order=(0, 1, 2, 3, 4, 5), times=tuple(times), splits=tuple(splits))
    assert h.comeback(result, card.distance, 0)
    assert not h.comeback(result, card.distance, 1)


def test_el_pronostico_de_sanxe_es_un_caballo_de_la_carrera() -> None:
    odds = h.estimate(card_of(*SIX), np.random.default_rng(1), trials=5_000)
    tip = h.sanxe_tip(odds, np.random.default_rng(4))
    assert 0 <= tip.horse < 6
    assert 87 <= tip.confidence < 100


# -- Forma, parrilla y Gran Premio --------------------------------------------------------


def test_la_forma_guarda_los_ultimos_cinco_puestos() -> None:
    record = HorseRecord()
    for position in (1, 4, 2, 6, 3, 1):
        record = h.updated_record(record, position, now=10.0)
    assert record.form == (4, 2, 6, 3, 1)
    assert (record.races, record.wins, record.places) == (6, 2, 4)
    assert h.form_line(record.form) == "4-2-6-3-1"
    assert h.form_line(()) == "—"


def test_la_parrilla_sortea_caballos_distintos_y_marca_los_cansados() -> None:
    now = 10_000.0
    records = {"falcon": HorseRecord(last_race=now - 60), "manual": HorseRecord(last_race=1.0)}
    for seed in range(30):
        card = h.new_card(np.random.default_rng(seed), records, now=now)
        assert card.size == FIELD_SIZE
        assert len({x.key for x in card.horses}) == FIELD_SIZE
        assert card.distance in h.DISTANCES
        assert "manual" not in card.tired
        assert card.tired <= {"falcon"}
        if card.going is Going.BARRO:
            assert card.rain_chance == 0
    gp = h.new_card(np.random.default_rng(1), {}, now=now, grand_prix=True)
    assert gp.size == GRAND_PRIX_FIELD
    assert gp.distance == h.GRAND_PRIX_DISTANCE
    assert gp.name == h.GRAND_PRIX_NAME


def test_cuando_toca_gran_premio() -> None:
    assert not h.grand_prix_due(since=GRAND_PRIX_EVERY - 1, last=0, now=10**9)
    assert not h.grand_prix_due(since=GRAND_PRIX_EVERY, last=1_000, now=1_000 + 60)
    assert h.grand_prix_due(since=GRAND_PRIX_EVERY, last=0, now=GRAND_PRIX_COOLDOWN)


def test_quien_entra_en_el_bote() -> None:
    order = (2, 4, 0, 1, 3, 5, 6, 7)
    stake = GRAND_PRIX_MIN_STAKE
    assert h.pot_eligible(Pick(BetKind.WIN, (2,)), stake, order)
    assert h.pot_eligible(Pick(BetKind.EXACTA, (2, 4)), stake, order)
    assert not h.pot_eligible(Pick(BetKind.PLACE, (2,)), stake, order)
    assert not h.pot_eligible(Pick(BetKind.WIN, (2,)), stake - 1, order)
    assert not h.pot_eligible(Pick(BetKind.WIN, (4,)), stake, order)


def test_el_bote_crece_hasta_el_tope_y_vuelve_al_inicio() -> None:
    assert h.next_pot(GRAND_PRIX_POT, won=False) == GRAND_PRIX_POT + GRAND_PRIX_POT_STEP
    assert h.next_pot(GRAND_PRIX_POT_MAX, won=False) == GRAND_PRIX_POT_MAX
    assert h.next_pot(GRAND_PRIX_POT_MAX, won=True) == GRAND_PRIX_POT
