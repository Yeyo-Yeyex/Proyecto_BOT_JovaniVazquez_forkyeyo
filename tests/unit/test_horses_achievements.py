"""Logros de las carreras de caballos: qué cuenta cada boleto (`horses_stats`)."""

from __future__ import annotations

from datetime import datetime

from bot.services.achievements import (
    AVAILABLE,
    CASINO_GROUP,
    CATEGORY_BY_KEY,
    HORSE_BACKED_KINDS_STAT,
    HORSE_BACKED_MAX_STAT,
    horses_stats,
    with_derived,
)
from bot.services.horses import SEGMENTS, STABLE_BY_KEY, BetKind, Going, Pick, RaceCard, RaceResult
from bot.services.levels import TIMEZONE

SIX = ("falcon", "manual", "paguita", "uco", "fango", "puerta")
NOON = datetime(2026, 3, 10, 12, tzinfo=TIMEZONE)


def card_of(**kw) -> RaceCard:  # noqa: ANN003
    kw.setdefault("going", Going.SECO)
    return RaceCard(horses=tuple(STABLE_BY_KEY[k] for k in SIX), distance=1_600, **kw)


def result_of(order: tuple[int, ...], gaps: tuple[float, ...] | None = None, **kw) -> RaceResult:  # noqa: ANN003
    gaps = gaps or tuple(float(i) for i in range(len(order)))
    times = [0.0] * len(order)
    for place, index in enumerate(order):
        times[index] = 100.0 + gaps[place]
    splits = tuple(tuple(t * s / SEGMENTS for s in range(SEGMENTS + 1)) for t in times)
    return RaceResult(order=order, times=tuple(times), splits=splits, **kw)


def stats(card: RaceCard, result: RaceResult, pick: Pick, **kw) -> dict[str, int]:  # noqa: ANN003
    params = {
        "stake": 100,
        "odds_cents": 300,
        "net": 200,
        "pot_share": 0,
        "tip_horse": 0,
        "alone": False,
        "via": "panel",
        "players": 1,
        "favourite": 0,
        "favourite_odds": 250,
        "photo": False,
        "comeback": False,
        "tax_delta": 0,
        "when": NOON,
    }
    params.update(kw)
    delta = horses_stats(card=card, result=result, pick=pick, **params)
    return {**delta.add, **delta.peak}


def test_la_categoria_va_en_el_casino_y_pasa_de_cuarenta_logros() -> None:
    assert CATEGORY_BY_KEY["horses"].group == CASINO_GROUP.key
    horses = [a for a in AVAILABLE if a.category == "horses"]
    assert len(horses) > 40
    secrets = sum(a.secret for a in horses)
    assert len(horses) // 10 <= secrets <= len(horses) // 3


def test_un_boleto_ganador_cuenta_sus_cosas() -> None:
    card = card_of(going=Going.BARRO, tired=frozenset({"uco"}))
    result = result_of((3, 0, 1, 2, 4, 5), stumbles={3: 2}, rained=True)
    got = stats(
        card,
        result,
        Pick(BetKind.WIN, (3,)),
        odds_cents=1_200,
        net=1_100,
        tip_horse=1,
        alone=True,
        photo=True,
        comeback=True,
        tax_delta=50,
    )
    for stat in (
        "horse_bets", "horse_kind_ganador", "horse_dist_1600", "horse_backed_uco", "horse_hits",
        "horse_hits_ganador", "horse_photo_wins", "horse_rain_wins", "horse_mud_wins",
        "horse_lone_wins", "horse_taxed", "horse_long_shots", "horse_tired_wins",
        "horse_stumble_wins", "horse_comebacks", "horse_contra_sanxe", "horse_uco_wins",
    ):  # fmt: skip
        assert got.get(stat) == 1, stat
    assert got["horse_odds_max"] == 1_200
    assert got["horse_win_max"] == 1_100
    assert "horse_sanxe_hits" not in got
    assert "horse_manual_comeback" not in got


def test_perder_por_una_nariz_y_quedar_ultimo() -> None:
    card = card_of()
    second = stats(
        card,
        result_of((1, 0, 2, 3, 4, 5), gaps=(0, 0.01, 1, 2, 3, 4)),
        Pick(BetKind.WIN, (0,)),
        net=-100,
    )
    assert second["horse_seconds"] == 1
    assert second["horse_nose_losses"] == 1
    assert "horse_hits" not in second
    last = stats(card, result_of((1, 2, 3, 4, 5, 0)), Pick(BetKind.WIN, (0,)), net=-100)
    assert last["horse_lasts"] == 1
    assert "horse_fav_flops" not in last  # favorito a 2,50x: no cuenta, hace falta 2x o menos
    flop = stats(
        card,
        result_of((1, 2, 3, 4, 5, 0)),
        Pick(BetKind.WIN, (0,)),
        net=-100,
        favourite_odds=180,
    )
    assert flop["horse_fav_flops"] == 1


def test_gemela_al_reves_y_trio_desordenado() -> None:
    card = card_of()
    result = result_of((0, 1, 2, 3, 4, 5))
    reversed_ = stats(card, result, Pick(BetKind.EXACTA, (1, 0)), net=-100)
    assert reversed_["horse_reversed"] == 1
    jumbled = stats(card, result, Pick(BetKind.TRIFECTA, (2, 0, 1)), net=-100)
    assert jumbled["horse_jumbled"] == 1
    hit = stats(card, result, Pick(BetKind.TRIFECTA, (0, 1, 2)), net=5_000)
    assert hit["horse_hits_trio"] == 1
    assert hit["horse_backed_falcon"] == hit["horse_backed_manual"] == 1
    assert hit["horse_falcon_bets"] == 1


def test_bote_gran_premio_botones_y_madrugada() -> None:
    card = card_of(grand_prix=True)
    got = stats(
        card,
        result_of((0, 1, 2, 3, 4, 5)),
        Pick(BetKind.WIN, (0,)),
        pot_share=2_500,
        via="sanxe",
        players=7,
        when=datetime(2026, 3, 10, 4, tzinfo=TIMEZONE),
    )
    assert got["horse_gp_bets"] == 1
    assert got["horse_pot_wins"] == 1
    assert got["horse_pot_max"] == 2_500
    assert got["horse_via_sanxe"] == 1
    assert got["horse_sanxe_hits"] == 1
    assert got["horse_party_max"] == 7
    assert got["horse_night"] == 1


def test_el_establo_y_el_caballo_favorito_se_derivan() -> None:
    full = with_derived({"horse_backed_falcon": 30, "horse_backed_uco": 2, "horse_backed_x": 99})
    assert full[HORSE_BACKED_KINDS_STAT] == 2
    assert full[HORSE_BACKED_MAX_STAT] == 30


def test_listo_cuenta_quien_lo_pulsa_y_quien_arranca() -> None:
    card = card_of()
    result = result_of((0, 1, 2, 3, 4, 5))
    pick = Pick(BetKind.WIN, (1,))
    got = stats(card, result, pick, ready=True, starter=True, flash=True)
    assert got["horse_ready"] == 1
    assert got["horse_starter"] == 1
    assert got["horse_flash"] == 1
    calm = stats(card, result, pick)
    assert "horse_ready" not in calm and "horse_starter" not in calm
