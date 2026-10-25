"""Pruebas de las apuestas deportivas: cuotas, lectura de The Odds API, calendario y cliente.

Lógica pura de `bot.services.sports`. Las cuotas se prueban con la invariante económica
(la casa nunca regala: cada signo devuelve a lo sumo `RTP`) sobre muchos partidos
generados con una semilla, y el cliente con una sesión falsa, sin red.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from typing import Any

import aiohttp
import pytest

from bot.services.sports import (
    LOW_QUOTA_EVERY,
    MAX_ODDS,
    MIN_BOOKMAKERS,
    MIN_ODDS,
    MIN_TOTAL_BOOKS,
    ODDS_EVERY,
    PRE_MATCH_EVERY,
    PRE_MATCH_WINDOW,
    QUOTA_RESERVE,
    RESULT_DELAY,
    RHO,
    RTP,
    SCORES_EVERY,
    TOTALS_EVERY,
    TOTALS_PRE_MATCH_EVERY,
    FeedError,
    Goals,
    Market,
    OddsClient,
    Pick,
    Score,
    days_from,
    fair_probabilities,
    fit_goals,
    house_odds,
    market_of,
    odds_due,
    parse_odds,
    parse_scores,
    payout,
    pick_text,
    quota_allows_odds,
    score_grid,
    scores_due,
    side_odds,
    totals_due,
    wins,
)

NOW = 1_800_000_000.0
KICKOFF = NOW + 3_600
HOME, AWAY = "Real Madrid", "FC Barcelona"
KEY = "CLAVE-SECRETA-0123456789"


def book(home: float, draw: float, away: float) -> dict[Pick, float]:
    return {Pick.HOME: home, Pick.DRAW: draw, Pick.AWAY: away}


def bookmaker(home: float, draw: float, away: float, *, key: str = "h2h") -> dict[str, Any]:
    """Una casa con la forma de The Odds API: mercado con tres resultados."""
    return {
        "key": "casa",
        "title": "Casa",
        "last_update": 1_799_999_000,
        "markets": [
            {
                "key": key,
                "outcomes": [
                    {"name": HOME, "price": home},
                    {"name": AWAY, "price": away},
                    {"name": "Draw", "price": draw},
                ],
            }
        ],
    }


def event(
    *,
    event_id: str = "ev1",
    commence: float = KICKOFF,
    bookmakers: list[Any] | None = None,
) -> dict[str, Any]:
    if bookmakers is None:
        bookmakers = [bookmaker(2.1, 3.4, 3.5) for _ in range(MIN_BOOKMAKERS)]
    return {
        "id": event_id,
        "sport_key": "soccer_spain_la_liga",
        "sport_title": "La Liga - Spain",
        "commence_time": int(commence),
        "home_team": HOME,
        "away_team": AWAY,
        "bookmakers": bookmakers,
    }


# -- parse_odds -------------------------------------------------------------------------


def test_parse_odds_lee_un_partido_con_la_forma_de_la_api() -> None:
    (match,) = parse_odds([event()], now=NOW)

    assert (match.event_id, match.sport) == ("ev1", "soccer_spain_la_liga")
    assert (match.home, match.away, match.commence) == (HOME, AWAY, KICKOFF)
    assert set(match.odds) == set(Pick)
    assert match.open_picks == list(Pick)
    assert match.title == f"{HOME} – {AWAY}"
    assert [match.team(pick) for pick in Pick] == [HOME, "Empate", AWAY]


def test_parse_odds_reconoce_los_signos_por_nombre_y_no_por_orden() -> None:
    reordered = {
        "key": "casa",
        "markets": [
            {
                "key": "h2h",
                "outcomes": [
                    {"name": "Draw", "price": 3.4},
                    {"name": AWAY, "price": 3.5},
                    {"name": HOME, "price": 2.1},
                ],
            }
        ],
    }

    (a,) = parse_odds([event(bookmakers=[reordered] * MIN_BOOKMAKERS)], now=NOW)
    (b,) = parse_odds([event()], now=NOW)

    assert a.odds == b.odds


def test_parse_odds_salta_los_partidos_que_ya_han_empezado() -> None:
    started = event(event_id="a", commence=NOW - 1)
    exactly_now = event(event_id="b", commence=NOW)
    future = event(event_id="c", commence=NOW + 1)

    matches = parse_odds([started, exactly_now, future], now=NOW)

    assert [m.event_id for m in matches] == ["c"]


def test_parse_odds_exige_las_casas_minimas_con_los_tres_signos() -> None:
    few = event(bookmakers=[bookmaker(2.1, 3.4, 3.5)] * (MIN_BOOKMAKERS - 1))
    enough = event(event_id="ok", bookmakers=[bookmaker(2.1, 3.4, 3.5)] * MIN_BOOKMAKERS)

    assert [m.event_id for m in parse_odds([few, enough], now=NOW)] == ["ok"]


def test_parse_odds_descarta_las_casas_sin_los_tres_signos() -> None:
    two_ways = {
        "key": "casa",
        "markets": [
            {
                "key": "h2h",
                "outcomes": [{"name": HOME, "price": 1.5}, {"name": AWAY, "price": 2.5}],
            }
        ],
    }
    good = [bookmaker(2.1, 3.4, 3.5)] * (MIN_BOOKMAKERS - 1)

    assert parse_odds([event(bookmakers=[*good, two_ways])], now=NOW) == []
    assert len(parse_odds([event(bookmakers=[*good, bookmaker(2.1, 3.4, 3.5)])], now=NOW)) == 1


def test_parse_odds_ignora_otros_mercados_y_precios_que_no_son_cuota() -> None:
    spreads = bookmaker(2.1, 3.4, 3.5, key="spreads")
    one_to_one = bookmaker(1.0, 3.4, 3.5)  # una cuota de 1,00 no es cuota
    bad_price = bookmaker(2.1, 3.4, 3.5)
    bad_price["markets"][0]["outcomes"][0]["price"] = "mucho"
    missing_price = bookmaker(2.1, 3.4, 3.5)
    del missing_price["markets"][0]["outcomes"][0]["price"]
    good = [bookmaker(2.1, 3.4, 3.5)] * (MIN_BOOKMAKERS - 1)

    for broken in (spreads, one_to_one, bad_price, missing_price):
        assert parse_odds([event(bookmakers=[*good, broken])], now=NOW) == []


def test_parse_odds_salta_lo_mal_formado_y_sigue_con_el_resto() -> None:
    no_id = event()
    del no_id["id"]
    bad_time = event(event_id="t")
    bad_time["commence_time"] = "pronto"
    null_time = event(event_id="n")
    null_time["commence_time"] = None
    same_team = event(event_id="s")
    same_team["away_team"] = HOME
    no_books = event(event_id="b")
    no_books["bookmakers"] = None
    fine = event(event_id="ok")

    matches = parse_odds([no_id, bad_time, null_time, same_team, no_books, fine], now=NOW)

    assert [m.event_id for m in matches] == ["ok"]


def test_parse_odds_ordena_por_comienzo() -> None:
    late = event(event_id="late", commence=NOW + 9_000)
    early = event(event_id="early", commence=NOW + 100)
    middle = event(event_id="middle", commence=NOW + 5_000)

    assert [m.event_id for m in parse_odds([late, early, middle], now=NOW)] == [
        "early",
        "middle",
        "late",
    ]


@pytest.mark.parametrize(
    "broken",
    ["casa", ["lista"], 7, {"markets": ["h2h"]}, {"markets": [{"key": "h2h", "outcomes": ["x"]}]}],
)
def test_parse_odds_no_revienta_con_casas_que_no_son_objetos(broken: Any) -> None:
    good = [bookmaker(2.1, 3.4, 3.5)] * MIN_BOOKMAKERS

    matches = parse_odds([event(bookmakers=[broken, *good])], now=NOW)

    assert len(matches) == 1


def test_parse_odds_descarta_la_casa_con_un_resultado_raro_o_una_cuota_infinita() -> None:
    fourth = bookmaker(2.1, 3.4, 3.5)
    fourth["markets"][0]["outcomes"].append({"name": "Suspendido", "price": 50.0})
    endless = bookmaker(2.1, 3.4, 3.5)
    endless["markets"][0]["outcomes"][0]["price"] = float("inf")
    good = [bookmaker(2.1, 3.4, 3.5)] * (MIN_BOOKMAKERS - 1)

    for broken in (fourth, endless):
        assert parse_odds([event(bookmakers=[*good, broken])], now=NOW) == []


def test_parse_odds_salta_un_partido_cuyas_casas_no_son_una_lista() -> None:
    odd = event(event_id="raro")
    odd["bookmakers"] = 7

    assert [m.event_id for m in parse_odds([odd, event()], now=NOW)] == ["ev1"]


# -- Cuotas: la casa nunca regala -------------------------------------------------------


def random_event(
    rng: random.Random, event_id: str, commence: float
) -> tuple[dict[str, Any], list[list[float]]]:
    """Un partido con cuotas de casas realistas: probabilidades reales con ruido y margen.

    Devuelve el evento y las cuotas decimales de cada casa en el orden 1, X, 2.
    """
    # Los extremos (un favoritísimo, un empate casi imposible) salen a menudo a propósito.
    shape = rng.choice([(1, 1, 1), (4, 1, 1), (12, 1, 0.5), (30, 1, 0.3), (0.3, 1, 3)])
    weights = [rng.gammavariate(a, 1) + 0.001 for a in shape]
    rng.shuffle(weights)
    total = sum(weights)
    truth = [w / total for w in weights]
    bookmakers = []
    books = []
    for _ in range(rng.randint(MIN_BOOKMAKERS, 8)):
        noisy = [max(0.002, p * rng.uniform(0.9, 1.1)) for p in truth]
        norm = sum(noisy)
        margin = rng.uniform(1.02, 1.09)
        prices = [max(round(margin * norm / p, 2), 1.01) for p in noisy]
        bookmakers.append(bookmaker(*prices))
        books.append(prices)
    return event(event_id=event_id, commence=commence, bookmakers=bookmakers), books


def independent_fair(books: list[list[float]]) -> list[float]:
    """Probabilidad justa de cada signo, calculada aparte de `fair_probabilities`."""
    mean = [0.0, 0.0, 0.0]
    for prices in books:
        implied = [1 / price for price in prices]
        for i, value in enumerate(implied):
            mean[i] += value / sum(implied) / len(books)
    return mean


@pytest.mark.parametrize("seed", range(20))
def test_la_casa_nunca_regala_en_ningun_signo_de_ningun_partido(seed: int) -> None:
    rng = random.Random(seed)
    cases = [random_event(rng, f"e{i}", NOW + 100 + i) for i in range(40)]

    matches = parse_odds([ev for ev, _ in cases], now=NOW)

    assert len(matches) == len(cases)
    books_by_id = {ev["id"]: books for ev, books in cases}
    for match in matches:
        fair = dict(zip(Pick, independent_fair(books_by_id[match.event_id]), strict=True))
        for pick in match.open_picks:
            assert MIN_ODDS <= match.odds[pick] <= MAX_ODDS
            # Probabilidad justa por lo que paga: nunca por encima del retorno.
            assert fair[pick] * match.odds[pick] / 100 <= RTP + 1e-9
        for pick in set(Pick) - set(match.open_picks):
            assert match.odds[pick] == 0
        if len(match.open_picks) == len(Pick):
            # Apostar a los tres signos a la vez siempre pierde: sin arbitraje.
            assert sum(100 / match.odds[pick] for pick in Pick) >= 1 / RTP - 1e-9


def test_los_partidos_generados_cubren_favoritos_sin_cuota_y_cuotas_recortadas() -> None:
    rng = random.Random(1234)
    matches = parse_odds(
        [random_event(rng, f"e{i}", NOW + 100 + i)[0] for i in range(400)], now=NOW
    )

    assert any(len(m.open_picks) < len(Pick) for m in matches)
    assert any(MAX_ODDS in m.odds.values() for m in matches)
    assert any(len(m.open_picks) == len(Pick) for m in matches)


def test_un_favoritisimo_cuya_cuota_no_llega_al_minimo_sale_con_cero() -> None:
    # 100 * RTP / 0,96 = 97,9, por debajo de MIN_ODDS: no se ofrece.
    odds = house_odds({Pick.HOME: 0.96, Pick.DRAW: 0.03, Pick.AWAY: 0.01})

    assert odds[Pick.HOME] == 0
    assert odds[Pick.DRAW] >= MIN_ODDS
    assert odds[Pick.AWAY] >= MIN_ODDS


def test_el_signo_justo_en_el_minimo_se_ofrece_y_un_poco_mas_favorito_no() -> None:
    edge = 100 * RTP / MIN_ODDS  # probabilidad cuya cuota cae justo en MIN_ODDS
    rest = {Pick.DRAW: 0.05, Pick.AWAY: 0.05}

    assert house_odds({Pick.HOME: edge - 1e-6, **rest})[Pick.HOME] >= MIN_ODDS
    assert house_odds({Pick.HOME: edge + 1e-3, **rest})[Pick.HOME] == 0


def test_las_cuotas_por_encima_del_maximo_se_recortan() -> None:
    odds = house_odds({Pick.HOME: 0.9995, Pick.DRAW: 0.0003, Pick.AWAY: 0.0002})

    assert odds[Pick.DRAW] == MAX_ODDS
    assert odds[Pick.AWAY] == MAX_ODDS
    # Recortar solo baja el retorno.
    assert 0.0003 * odds[Pick.DRAW] / 100 < RTP


def test_una_probabilidad_cero_da_la_cuota_maxima_y_no_divide_por_cero() -> None:
    odds = house_odds({Pick.HOME: 0.5, Pick.DRAW: 0.5})

    assert odds[Pick.AWAY] == MAX_ODDS


def test_la_cuota_se_redondea_hacia_abajo_a_la_centesima() -> None:
    odds = house_odds({Pick.HOME: 0.5, Pick.DRAW: 0.3, Pick.AWAY: 0.2})

    assert odds[Pick.HOME] == int(100 * RTP / 0.5)
    assert odds[Pick.DRAW] == int(100 * RTP / 0.3)  # 313,33 baja a 313
    assert odds[Pick.AWAY] == int(100 * RTP / 0.2)


# -- fair_probabilities y payout --------------------------------------------------------


def test_las_probabilidades_justas_suman_uno_y_quitan_el_margen() -> None:
    sin_margen = fair_probabilities([book(2.0, 4.0, 4.0)])  # 1/2 + 1/4 + 1/4 = 1
    con_margen = fair_probabilities([book(1.8, 3.6, 3.6)])  # suma de 1/cuota > 1

    assert sin_margen == pytest.approx({Pick.HOME: 0.5, Pick.DRAW: 0.25, Pick.AWAY: 0.25})
    assert sum(1 / price for price in (1.8, 3.6, 3.6)) > 1
    assert sum(con_margen.values()) == pytest.approx(1)
    assert con_margen[Pick.HOME] == pytest.approx(0.5)


def test_las_probabilidades_justas_son_la_media_de_las_casas() -> None:
    fair = fair_probabilities([book(2.0, 4.0, 4.0), book(4.0, 4.0, 2.0)])

    assert fair[Pick.HOME] == pytest.approx(0.375)
    assert fair[Pick.DRAW] == pytest.approx(0.25)
    assert fair[Pick.AWAY] == pytest.approx(0.375)
    assert sum(fair.values()) == pytest.approx(1)


def test_sin_casas_no_hay_probabilidades() -> None:
    assert fair_probabilities([]) == {}


def test_el_pago_redondea_hacia_abajo() -> None:
    assert payout(100, 185) == 185
    assert payout(10, 101) == 10  # 10,1 baja a 10
    assert payout(33, 250) == 82  # 82,5 baja a 82
    assert payout(1, 199) == 1


# -- parse_scores y Score ---------------------------------------------------------------


def score_event(
    event_id: str = "ev1", *, completed: Any = True, home: Any = "2", away: Any = "1"
) -> dict[str, Any]:
    return {
        "id": event_id,
        "sport_key": "soccer_spain_la_liga",
        "commence_time": int(KICKOFF),
        "completed": completed,
        "home_team": HOME,
        "away_team": AWAY,
        "scores": [{"name": HOME, "score": home}, {"name": AWAY, "score": away}],
        "last_update": int(KICKOFF) + 7_000,
    }


def test_parse_scores_lee_solo_los_partidos_terminados() -> None:
    results = parse_scores(
        [
            score_event("done"),
            score_event("live", completed=False),
            score_event("none", completed=None),
        ]
    )

    assert results == {"done": Score(2, 1)}


def test_parse_scores_necesita_el_marcador_de_los_dos_equipos() -> None:
    one_team = score_event("uno")
    one_team["scores"] = [{"name": HOME, "score": "2"}]
    no_scores = score_event("sin")
    no_scores["scores"] = None
    wrong_names = score_event("otros")
    wrong_names["scores"] = [{"name": "A", "score": "1"}, {"name": "B", "score": "0"}]

    assert parse_scores([one_team, no_scores, wrong_names]) == {}


def test_parse_scores_ignora_lo_mal_formado_y_sigue() -> None:
    bad_number = score_event("malo", home="dos")
    null_number = score_event("nulo", away=None)
    no_id = score_event("x")
    del no_id["id"]
    odd_scores = score_event("raro")
    odd_scores["scores"] = ["2-1", 3]

    results = parse_scores(
        [bad_number, null_number, no_id, odd_scores, score_event("ok", home="0", away="0")]
    )

    assert results == {"ok": Score(0, 0)}


@pytest.mark.parametrize(
    ("home", "away", "pick"),
    [(2, 1, Pick.HOME), (0, 0, Pick.DRAW), (3, 3, Pick.DRAW), (0, 4, Pick.AWAY), (1, 2, Pick.AWAY)],
)
def test_el_marcador_da_el_signo_1_x_o_2(home: int, away: int, pick: Pick) -> None:
    assert Score(home, away).pick is pick


def test_los_signos_se_escriben_1_x_2() -> None:
    assert [pick.value for pick in Pick] == ["1", "X", "2"]


# -- Calendario -------------------------------------------------------------------------


def due(
    *,
    fetched_age: float | None,
    kickoff_in: float | None = None,
    remaining: int | None = None,
) -> bool:
    return odds_due(
        now=NOW,
        fetched_at=None if fetched_age is None else NOW - fetched_age,
        next_kickoff=None if kickoff_in is None else NOW + kickoff_in,
        remaining=remaining,
    )


def test_sin_datos_siempre_se_piden_las_cuotas_aunque_no_queden_creditos() -> None:
    assert due(fetched_age=None)
    assert due(fetched_age=None, remaining=0)


def test_las_cuotas_se_piden_cada_odds_every() -> None:
    assert not due(fetched_age=ODDS_EVERY - 1)
    assert due(fetched_age=ODDS_EVERY)
    assert due(fetched_age=ODDS_EVERY + 1)


def test_antes_del_comienzo_se_refrescan_si_tienen_mas_de_pre_match_every() -> None:
    inside = PRE_MATCH_WINDOW - 1

    assert not due(fetched_age=PRE_MATCH_EVERY - 1, kickoff_in=inside)
    assert due(fetched_age=PRE_MATCH_EVERY, kickoff_in=inside)
    # El borde de la ventana cuenta; un segundo más allá, no.
    assert due(fetched_age=PRE_MATCH_EVERY, kickoff_in=PRE_MATCH_WINDOW)
    assert not due(fetched_age=PRE_MATCH_EVERY, kickoff_in=PRE_MATCH_WINDOW + 1)


def test_un_partido_ya_empezado_o_ninguno_no_adelanta_el_refresco() -> None:
    assert not due(fetched_age=PRE_MATCH_EVERY, kickoff_in=-1)
    assert not due(fetched_age=PRE_MATCH_EVERY, kickoff_in=None)


def test_con_la_reserva_tocada_solo_se_piden_cada_low_quota_every() -> None:
    low = QUOTA_RESERVE

    assert not due(fetched_age=ODDS_EVERY + 1, remaining=low)
    assert not due(fetched_age=PRE_MATCH_EVERY, kickoff_in=60, remaining=low)
    assert not due(fetched_age=LOW_QUOTA_EVERY - 1, remaining=low)
    assert due(fetched_age=LOW_QUOTA_EVERY, remaining=low)
    assert due(fetched_age=LOW_QUOTA_EVERY, remaining=0)


def test_la_reserva_solo_se_toca_cuando_queda_igual_o_menos() -> None:
    assert quota_allows_odds(None)
    assert quota_allows_odds(QUOTA_RESERVE + 1)
    assert not quota_allows_odds(QUOTA_RESERVE)
    assert not quota_allows_odds(0)
    assert due(fetched_age=ODDS_EVERY, remaining=QUOTA_RESERVE + 1)


def test_los_resultados_no_se_piden_antes_de_result_delay() -> None:
    oldest = NOW - RESULT_DELAY

    assert not scores_due(now=oldest + RESULT_DELAY - 1, checked_at=None, oldest=oldest)
    assert scores_due(now=oldest + RESULT_DELAY, checked_at=None, oldest=oldest)


def test_sin_apuestas_abiertas_no_se_piden_resultados() -> None:
    assert not scores_due(now=NOW, checked_at=None, oldest=None)


def test_los_resultados_se_repiten_cada_scores_every() -> None:
    oldest = NOW - RESULT_DELAY - 10

    assert not scores_due(now=NOW, checked_at=NOW - SCORES_EVERY + 1, oldest=oldest)
    assert scores_due(now=NOW, checked_at=NOW - SCORES_EVERY, oldest=oldest)


def test_days_from_pide_un_dia_salvo_que_algo_lleve_casi_uno() -> None:
    hour = 3_600

    assert days_from(now=NOW, oldest=NOW - 2 * hour) == 1
    assert days_from(now=NOW, oldest=NOW - 20 * hour + 1) == 1
    assert days_from(now=NOW, oldest=NOW - 20 * hour) == 3
    assert days_from(now=NOW, oldest=NOW - 60 * hour) == 3


# -- OddsClient -------------------------------------------------------------------------


class FakeReply:
    """Respuesta de `session.get`: un context manager asíncrono como el de aiohttp."""

    def __init__(
        self,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        payload: Any = None,
        enter_error: BaseException | None = None,
        json_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers or {})
        self.payload = payload
        self.enter_error = enter_error
        self.json_error = json_error

    async def __aenter__(self) -> FakeReply:
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, reply: FakeReply) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, params: dict[str, str]) -> FakeReply:
        self.calls.append((url, params))
        return self.reply


def client_with(reply: FakeReply) -> tuple[OddsClient, FakeSession]:
    session = FakeSession(reply)
    return OddsClient(session, KEY), session  # type: ignore[arg-type]


async def test_el_cliente_pide_las_cuotas_1x2_europeas_con_la_clave() -> None:
    client, session = client_with(FakeReply(payload=[], headers={"x-requests-remaining": "497"}))

    reply = await client.odds("soccer_spain_la_liga", now=NOW)

    ((url, params),) = session.calls
    assert url == f"{OddsClient.BASE_URL}/soccer_spain_la_liga/odds/"
    assert params["apiKey"] == KEY
    assert params["regions"] == "eu"
    assert params["markets"] == "h2h"
    assert params["dateFormat"] == "unix"
    assert params["oddsFormat"] == "decimal"
    assert params["commenceTimeFrom"].endswith("Z")
    assert params["commenceTimeFrom"] < params["commenceTimeTo"]
    assert reply.events == []
    assert reply.remaining == 497


async def test_el_cliente_pide_los_resultados_de_los_ultimos_dias() -> None:
    client, session = client_with(FakeReply(payload=[score_event()]))

    reply = await client.scores("soccer_spain_la_liga", days=3)

    ((url, params),) = session.calls
    assert url.endswith("/soccer_spain_la_liga/scores/")
    assert params == {"daysFrom": "3", "dateFormat": "unix", "apiKey": KEY}
    assert reply.events == [score_event()]
    assert reply.remaining is None


@pytest.mark.parametrize(
    ("header", "expected"), [("497", 497), ("497.0", 497), ("0", 0), ("raro", None), (None, None)]
)
async def test_el_cliente_lee_los_creditos_que_quedan(
    header: str | None, expected: int | None
) -> None:
    headers = {} if header is None else {"x-requests-remaining": header}
    client, _ = client_with(FakeReply(payload=[], headers=headers))

    assert (await client.odds("s", now=NOW)).remaining == expected


async def test_el_cliente_se_queda_solo_con_los_eventos_que_son_objetos() -> None:
    client, _ = client_with(FakeReply(payload=[event(), "basura", 3, None, event(event_id="dos")]))

    reply = await client.odds("s", now=NOW)

    assert [e["id"] for e in reply.events] == ["ev1", "dos"]


@pytest.mark.parametrize("status", [401, 422, 429, 500, 503, 204])
async def test_un_estado_que_no_es_200_da_feed_error_sin_la_clave(status: int) -> None:
    client, _ = client_with(FakeReply(status=status, payload=[]))

    with pytest.raises(FeedError) as caught:
        await client.odds("s", now=NOW)

    assert str(status) in str(caught.value)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize(
    "failure",
    [
        aiohttp.ClientConnectionError(f"https://api.the-odds-api.com/v4?apiKey={KEY}"),
        aiohttp.ClientResponseError(None, (), status=500, message=f"apiKey={KEY}"),  # type: ignore[arg-type]
        TimeoutError(f"apiKey={KEY}"),
    ],
)
async def test_un_fallo_de_red_da_feed_error_sin_clave_ni_causa(failure: BaseException) -> None:
    client, _ = client_with(FakeReply(enter_error=failure))

    with pytest.raises(FeedError) as caught:
        await client.scores("s", days=1)

    assert KEY not in str(caught.value)
    assert KEY not in repr(caught.value)
    # `from None`: ni causa ni contexto visible, o el traceback enseñaría la URL con la clave.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


async def test_un_json_roto_da_feed_error_sin_clave_ni_causa() -> None:
    error = ValueError(f"no es json: apiKey={KEY}")
    client, _ = client_with(FakeReply(json_error=error))

    with pytest.raises(FeedError) as caught:
        await client.odds("s", now=NOW)

    assert KEY not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("payload", [{"message": "Invalid key"}, "hola", None, 7])
async def test_una_respuesta_que_no_es_una_lista_da_feed_error(payload: Any) -> None:
    client, _ = client_with(FakeReply(payload=payload))

    with pytest.raises(FeedError, match="lista"):
        await client.odds("s", now=NOW)


# -- Mercados de goles ------------------------------------------------------------------


def totals_market(*lines: tuple[float, float, float]) -> dict[str, Any]:
    """Un mercado `totals` de The Odds API: (línea, cuota del más, cuota del menos)."""
    outcomes = []
    for line, over, under in lines:
        outcomes += [
            {"name": "Over", "price": over, "point": line},
            {"name": "Under", "price": under, "point": line},
        ]
    return {"key": "totals", "outcomes": outcomes}


def with_totals(*lines: tuple[float, float, float]) -> dict[str, Any]:
    """Una casa con el 1X2 y el más/menos."""
    casa = bookmaker(2.1, 3.4, 3.5)
    casa["markets"].append(totals_market(*lines))
    return casa


def model_chance(goals: Goals, pick: str) -> float:
    """Probabilidad de una opción según el modelo, sumando la rejilla con `wins`."""
    return sum(
        cell
        for h, row in enumerate(score_grid(goals))
        for a, cell in enumerate(row)
        if wins(pick, Score(h, a))
    )


def test_cada_opcion_es_de_un_solo_mercado_y_se_escribe_en_claro() -> None:
    seen = [pick for market in Market for pick in market.picks]
    assert len(seen) == len(set(seen))
    assert all(market_of(pick) is market for market in Market for pick in market.picks)
    assert market_of("9-9") is None
    assert pick_text("1", HOME, AWAY) == f"1 ({HOME})"
    assert pick_text("+2.5", HOME, AWAY) == "Más de 2,5 goles"
    assert pick_text("-1.5", HOME, AWAY) == "Menos de 1,5 goles"
    assert pick_text("si", HOME, AWAY) == "Marcan los dos"
    assert pick_text("2-1", HOME, AWAY) == "Exacto 2-1"
    assert pick_text("otro", HOME, AWAY) == "Otro resultado exacto"


@pytest.mark.parametrize(
    ("pick", "home", "away", "won"),
    [
        ("1", 2, 1, True),
        ("X", 2, 1, False),
        ("+2.5", 2, 1, True),
        ("+2.5", 1, 1, False),
        ("-2.5", 1, 1, True),
        ("-1.5", 1, 0, True),
        ("-1.5", 1, 1, False),
        ("si", 1, 1, True),
        ("si", 3, 0, False),
        ("no", 0, 0, True),
        ("no", 2, 2, False),
        ("2-1", 2, 1, True),
        ("2-1", 1, 2, False),
        ("otro", 4, 0, True),
        ("otro", 3, 3, False),
        ("3-3", 3, 3, True),
    ],
)
def test_cada_opcion_gana_o_pierde_con_el_marcador_final(
    pick: str, home: int, away: int, won: bool
) -> None:
    assert wins(pick, Score(home, away)) is won


def test_el_exacto_y_otro_resultado_cubren_todos_los_marcadores_una_vez() -> None:
    for home in range(8):
        for away in range(8):
            hits = [pick for pick in Market.EXACT.picks if wins(pick, Score(home, away))]
            assert len(hits) == 1, (home, away, hits)


def test_la_rejilla_suma_uno_y_rho_da_mas_0_0_que_una_poisson() -> None:
    grid = score_grid(Goals(1.4, 1.1))
    assert sum(map(sum, grid)) == pytest.approx(1)
    assert RHO < 0
    assert grid[0][0] > math.exp(-1.4) * math.exp(-1.1)


def test_el_ajuste_recupera_los_goles_de_un_partido_inventado() -> None:
    truth = Goals(1.7, 0.9)
    fair = {pick: model_chance(truth, pick.value) for pick in Pick}
    totals = {line: model_chance(truth, f"+{line}") for line in (2.5, 3.5)}

    goals = fit_goals(fair, totals)

    assert goals is not None
    assert goals.home == pytest.approx(truth.home, abs=1e-3)
    assert goals.away == pytest.approx(truth.away, abs=1e-3)


def test_sin_mas_menos_o_sin_1x2_no_hay_ajuste() -> None:
    fair = {Pick.HOME: 0.45, Pick.DRAW: 0.27, Pick.AWAY: 0.28}
    assert fit_goals(fair, {}) is None
    assert fit_goals({}, {2.5: 0.5}) is None


def test_un_mas_menos_que_contradice_al_1x2_no_se_cree() -> None:
    # Casi seguro empate y casi seguro más de 4,5 goles: ningún par de medias da las dos.
    fair = {Pick.HOME: 0.02, Pick.DRAW: 0.96, Pick.AWAY: 0.02}
    assert fit_goals(fair, {4.5: 0.95}) is None


@pytest.mark.parametrize("seed", range(5))
def test_la_casa_nunca_regala_en_los_mercados_de_goles(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(40):
        goals = Goals(rng.uniform(0.3, 3.2), rng.uniform(0.3, 3.2))
        odds = side_odds(goals)
        assert set(odds) == {p for m in Market if m is not Market.RESULT for p in m.picks}
        for pick, cents in odds.items():
            market = market_of(pick)
            assert market is not None
            assert cents == 0 or MIN_ODDS <= cents <= MAX_ODDS
            assert model_chance(goals, pick) * cents / 100 <= market.rtp + 1e-9


def test_las_lineas_que_dan_las_casas_se_cotizan_con_su_probabilidad() -> None:
    odds = side_odds(Goals(1.4, 1.1), {2.5: 0.6, 4.5: 0.2})

    assert (odds["+2.5"], odds["-2.5"]) == (156, 235)  # 94 / 0,6 y 94 / 0,4, a la baja
    assert odds["+3.5"] == side_odds(Goals(1.4, 1.1))["+3.5"]  # la que no dan, del modelo
    assert "+4.5" not in odds  # una línea que el bot no ofrece no se cuela


def test_el_exacto_deja_mas_margen_que_el_resto() -> None:
    assert Market.EXACT.rtp < RTP
    assert {m.rtp for m in Market if m is not Market.EXACT} == {RTP}


def test_parse_odds_lee_el_mas_menos_y_abre_los_mercados_de_goles() -> None:
    casas = [with_totals((2.5, 1.9, 1.9), (3.0, 2.4, 1.6)) for _ in range(MIN_BOOKMAKERS)]

    (match,) = parse_odds([event(bookmakers=casas)], now=NOW)

    # La línea entera (3,0) puede quedar en empate de apuestas: no se usa.
    assert dict(match.totals) == {2.5: pytest.approx(0.5)}
    assert match.goals is not None
    assert match.markets() == list(Market)
    assert match.price("+2.5") > 0
    assert match.price("1") == match.odds[Pick.HOME]


def test_el_mas_menos_de_una_sola_casa_no_cuenta() -> None:
    casas = [bookmaker(2.1, 3.4, 3.5) for _ in range(MIN_BOOKMAKERS)]
    casas[0]["markets"].append(totals_market((2.5, 1.9, 1.9)))
    assert MIN_TOTAL_BOOKS > 1

    (match,) = parse_odds([event(bookmakers=casas)], now=NOW)

    assert not match.totals
    assert match.goals is None
    assert match.markets() == [Market.RESULT]
    assert match.price("+2.5") == 0


def test_sin_mas_menos_en_la_consulta_se_usa_el_que_ya_se_sabia() -> None:
    (match,) = parse_odds([event()], now=NOW, known_totals={"ev1": {2.5: 0.55}})

    assert dict(match.totals) == {2.5: 0.55}
    assert match.goals is not None


def test_el_mas_menos_mal_formado_se_salta() -> None:
    casas = [with_totals((2.5, 1.9, 1.9)) for _ in range(MIN_BOOKMAKERS)]
    casas[0]["markets"][-1]["outcomes"][0]["price"] = "mucho"
    casas[1]["markets"][-1]["outcomes"][1].pop("point")

    (match,) = parse_odds([event(bookmakers=casas)], now=NOW)

    # Solo queda una casa con la línea entera: no llega a `MIN_TOTAL_BOOKS`.
    assert MIN_BOOKMAKERS - 2 < MIN_TOTAL_BOOKS
    assert not match.totals


def test_el_mas_menos_se_pide_una_vez_al_dia_y_antes_de_un_comienzo() -> None:
    soon = NOW + PRE_MATCH_WINDOW - 60
    assert totals_due(now=NOW, fetched_at=None, next_kickoff=None, remaining=None)
    assert totals_due(now=NOW, fetched_at=NOW - TOTALS_EVERY, next_kickoff=None, remaining=400)
    assert not totals_due(now=NOW, fetched_at=NOW - 3_600, next_kickoff=None, remaining=400)
    early = NOW - TOTALS_PRE_MATCH_EVERY
    assert totals_due(now=NOW, fetched_at=early, next_kickoff=soon, remaining=400)
    late = NOW - TOTALS_PRE_MATCH_EVERY + 60
    assert not totals_due(now=NOW, fetched_at=late, next_kickoff=soon, remaining=400)


def test_con_la_reserva_tocada_no_se_pide_el_mas_menos() -> None:
    assert not totals_due(now=NOW, fetched_at=None, next_kickoff=None, remaining=QUOTA_RESERVE)


async def test_el_cliente_pide_el_mas_menos_solo_si_se_le_dice() -> None:
    client, session = client_with(FakeReply(payload=[]))

    await client.odds("soccer_spain_la_liga", now=NOW, totals=True)
    await client.odds("soccer_spain_la_liga", now=NOW)

    assert [params["markets"] for _url, params in session.calls] == ["h2h,totals", "h2h"]
