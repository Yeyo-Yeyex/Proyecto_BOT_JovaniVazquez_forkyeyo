"""Pruebas del repositorio de las apuestas deportivas: partidos, marcadores y estado de la API."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bot.repositories.sports import FeedState, SportsRepository
from bot.services.sports import Match, Pick, Score, priced

NOW = 1_800_000_000.0
LIGA = "soccer_spain_la_liga"
CHAMPIONS = "soccer_uefa_champs_league"


def match(
    event_id: str,
    *,
    sport: str = LIGA,
    commence: float = NOW + 3_600,
    home: str = "Real Madrid",
    away: str = "FC Barcelona",
    odds: tuple[int, int, int] = (210, 340, 350),
) -> Match:
    return Match(
        event_id,
        sport,
        home,
        away,
        commence,
        {Pick.HOME: odds[0], Pick.DRAW: odds[1], Pick.AWAY: odds[2]},
    )


async def make_repo(tmp_path: Path) -> SportsRepository:
    repo = SportsRepository(tmp_path / "bot.db")
    await repo.initialize()
    return repo


async def test_guardar_cuotas_y_leer_los_proximos_partidos(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    late = match("tarde", commence=NOW + 9_000)
    early = match("pronto", commence=NOW + 100, odds=(150, 400, 600))

    await repo.save_odds(LIGA, [late, early], now=NOW, remaining=480)

    assert await repo.upcoming(LIGA, now=NOW) == [early, late]


async def test_upcoming_solo_da_futuros_de_su_competicion(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(
        LIGA,
        [match("futuro"), match("empezado", commence=NOW - 1)],
        now=NOW - 7_200,
        remaining=None,
    )
    await repo.save_odds(CHAMPIONS, [match("europa", sport=CHAMPIONS)], now=NOW, remaining=None)

    assert [m.event_id for m in await repo.upcoming(LIGA, now=NOW)] == ["futuro"]
    assert [m.event_id for m in await repo.upcoming(CHAMPIONS, now=NOW)] == ["europa"]


async def test_upcoming_desempata_por_el_nombre_del_equipo_de_casa(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    b = match("b", home="Valencia")
    a = match("a", home="Athletic")

    await repo.save_odds(LIGA, [b, a], now=NOW, remaining=None)

    assert [m.event_id for m in await repo.upcoming(LIGA, now=NOW)] == ["a", "b"]


async def test_un_partido_que_deja_de_venir_se_queda_sin_cuotas_y_no_sale(
    tmp_path: Path,
) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a"), match("b")], now=NOW, remaining=None)

    await repo.save_odds(LIGA, [match("a")], now=NOW + 60, remaining=None)

    assert [m.event_id for m in await repo.upcoming(LIGA, now=NOW + 60)] == ["a"]


async def test_un_partido_que_vuelve_a_venir_recupera_sus_cuotas(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a")], now=NOW, remaining=None)
    await repo.save_odds(LIGA, [], now=NOW + 60, remaining=None)
    assert await repo.upcoming(LIGA, now=NOW + 60) == []

    await repo.save_odds(LIGA, [match("a", odds=(180, 330, 400))], now=NOW + 120, remaining=None)

    (back,) = await repo.upcoming(LIGA, now=NOW + 120)
    assert back.odds == {Pick.HOME: 180, Pick.DRAW: 330, Pick.AWAY: 400}


async def test_guardar_cuotas_de_una_competicion_no_toca_las_de_otra(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(CHAMPIONS, [match("europa", sport=CHAMPIONS)], now=NOW, remaining=None)

    await repo.save_odds(LIGA, [], now=NOW + 60, remaining=None)

    assert [m.event_id for m in await repo.upcoming(CHAMPIONS, now=NOW + 60)] == ["europa"]


async def test_los_partidos_ya_empezados_conservan_sus_cuotas_al_refrescar(
    tmp_path: Path,
) -> None:
    """Los empezados no se vacían: el resultado y las apuestas siguen colgando de ellos."""
    repo = await make_repo(tmp_path)
    started = match("empezado", commence=NOW + 10)
    await repo.save_odds(LIGA, [started], now=NOW, remaining=None)

    await repo.save_odds(LIGA, [], now=NOW + 20, remaining=None)
    await repo.save_scores(LIGA, {"empezado": Score(1, 0)}, now=NOW + 9_000, remaining=None)

    assert await repo.scores(["empezado"]) == {"empezado": Score(1, 0)}


async def test_un_partido_con_cuotas_a_cero_no_sale_aunque_sea_futuro(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a", odds=(0, 0, 0))], now=NOW, remaining=None)

    assert await repo.upcoming(LIGA, now=NOW) == []


async def test_un_signo_sin_cuota_se_guarda_como_cero(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a", odds=(0, 400, 900))], now=NOW, remaining=None)

    (saved,) = await repo.upcoming(LIGA, now=NOW)
    assert saved.odds[Pick.HOME] == 0
    assert saved.open_picks == [Pick.DRAW, Pick.AWAY]


async def test_volver_a_guardar_un_partido_actualiza_cuotas_y_comienzo(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a")], now=NOW, remaining=None)

    moved = match("a", commence=NOW + 7_200, odds=(190, 350, 400))
    await repo.save_odds(LIGA, [moved], now=NOW + 60, remaining=None)

    assert await repo.upcoming(LIGA, now=NOW + 60) == [moved]


# -- Marcadores -------------------------------------------------------------------------


async def test_guardar_marcadores_y_leerlos_por_partido(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a"), match("b"), match("c")], now=NOW, remaining=None)

    await repo.save_scores(
        LIGA, {"a": Score(2, 1), "b": Score(0, 0)}, now=NOW + 9_000, remaining=None
    )

    assert await repo.scores(["a", "b", "c", "nunca-visto"]) == {
        "a": Score(2, 1),
        "b": Score(0, 0),
    }


async def test_un_marcador_de_cero_a_cero_cuenta_como_marcador(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a")], now=NOW, remaining=None)

    await repo.save_scores(LIGA, {"a": Score(0, 0)}, now=NOW + 9_000, remaining=None)

    assert (await repo.scores(["a"]))["a"].pick is Pick.DRAW


async def test_sin_ids_no_hay_marcadores(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)

    assert await repo.scores([]) == {}


async def test_un_marcador_de_un_partido_desconocido_no_inventa_filas(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)

    await repo.save_scores(LIGA, {"fantasma": Score(1, 1)}, now=NOW, remaining=None)

    assert await repo.scores(["fantasma"]) == {}


async def test_guardar_cuotas_no_borra_el_marcador_ya_conocido(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a", commence=NOW + 10)], now=NOW, remaining=None)
    await repo.save_scores(LIGA, {"a": Score(3, 2)}, now=NOW + 9_000, remaining=None)

    await repo.save_odds(LIGA, [match("a", commence=NOW + 10)], now=NOW + 9_100, remaining=None)

    assert await repo.scores(["a"]) == {"a": Score(3, 2)}


# -- Estado de la API -------------------------------------------------------------------


async def test_sin_consultas_el_estado_de_la_api_esta_vacio(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)

    assert await repo.feed(LIGA) == FeedState()


async def test_el_estado_apunta_cuando_se_pidieron_cuotas_y_resultados(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)

    await repo.save_odds(LIGA, [match("a")], now=NOW, remaining=490)
    assert await repo.feed(LIGA) == FeedState(odds_at=NOW, scores_at=None, remaining=490)

    await repo.save_scores(LIGA, {}, now=NOW + 500, remaining=488)
    assert await repo.feed(LIGA) == FeedState(odds_at=NOW, scores_at=NOW + 500, remaining=488)
    assert await repo.feed(CHAMPIONS) == FeedState()


async def test_los_creditos_se_conservan_si_la_respuesta_no_los_trae(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [], now=NOW, remaining=77)

    await repo.save_odds(LIGA, [], now=NOW + 60, remaining=None)
    await repo.save_scores(LIGA, {}, now=NOW + 120, remaining=None)

    assert (await repo.feed(LIGA)).remaining == 77


async def test_los_creditos_cero_no_se_confunden_con_que_no_vengan(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [], now=NOW, remaining=50)

    await repo.save_odds(LIGA, [], now=NOW + 60, remaining=0)

    assert (await repo.feed(LIGA)).remaining == 0


async def test_mark_checked_apunta_la_consulta_fallida_sin_tocar_el_resto(
    tmp_path: Path,
) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [], now=NOW, remaining=300)

    await repo.mark_checked(LIGA, "scores_at", now=NOW + 40)
    await repo.mark_checked(LIGA, "odds_at", now=NOW + 80)

    assert await repo.feed(LIGA) == FeedState(odds_at=NOW + 80, scores_at=NOW + 40, remaining=300)


async def test_mark_checked_sin_estado_previo_lo_crea(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)

    await repo.mark_checked(LIGA, "odds_at", now=NOW)

    assert await repo.feed(LIGA) == FeedState(odds_at=NOW)


@pytest.mark.parametrize("column", ["remaining", "sport", "odds_at; DROP TABLE sports_feed", ""])
async def test_mark_checked_rechaza_una_columna_rara(tmp_path: Path, column: str) -> None:
    repo = await make_repo(tmp_path)

    with pytest.raises(ValueError):
        await repo.mark_checked(LIGA, column, now=NOW)

    assert await repo.feed(LIGA) == FeedState()


# -- Limpieza ---------------------------------------------------------------------------


async def test_prune_borra_los_partidos_anteriores_al_corte(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(
        LIGA,
        [
            match("viejo", commence=NOW - 10 * 86_400),
            match("limite", commence=NOW - 86_400),
            match("nuevo", commence=NOW + 100),
        ],
        now=NOW - 11 * 86_400,
        remaining=None,
    )
    await repo.save_scores(
        LIGA,
        {"viejo": Score(1, 0), "limite": Score(2, 2), "nuevo": Score(0, 1)},
        now=NOW,
        remaining=None,
    )

    await repo.prune(before=NOW - 86_400)

    # `commence < before`: el que empieza justo en el corte se queda.
    assert set(await repo.scores(["viejo", "limite", "nuevo"])) == {"limite", "nuevo"}


async def test_prune_no_toca_el_estado_de_la_api(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a")], now=NOW, remaining=12)

    await repo.prune(before=NOW + 10**9)

    assert await repo.feed(LIGA) == FeedState(odds_at=NOW, remaining=12)
    assert await repo.upcoming(LIGA, now=NOW) == []


async def test_initialize_dos_veces_no_borra_nada(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a")], now=NOW, remaining=5)

    await repo.initialize()

    assert [m.event_id for m in await repo.upcoming(LIGA, now=NOW)] == ["a"]
    assert (await repo.feed(LIGA)).remaining == 5


# -- Más/menos y goles esperados (9.22.0) ---------------------------------------------

FAIR = {Pick.HOME: 0.45, Pick.DRAW: 0.27, Pick.AWAY: 0.28}


async def test_el_mas_menos_y_los_goles_vuelven_iguales_con_sus_cuotas(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    saved = priced(match("goles"), {2.5: 0.52, 3.5: 0.3}, FAIR)
    assert saved.goals is not None and saved.side

    await repo.save_odds(LIGA, [saved], now=NOW, remaining=None, totals=True)

    assert await repo.upcoming(LIGA, now=NOW) == [saved]


async def test_un_partido_sin_mas_menos_vuelve_sin_mercados_de_goles(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("solo1x2")], now=NOW, remaining=None)

    (back,) = await repo.upcoming(LIGA, now=NOW)

    assert (dict(back.totals), back.goals, dict(back.side)) == ({}, None, {})


async def test_solo_la_consulta_con_mas_menos_apunta_cuando_se_pidio(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_odds(LIGA, [match("a")], now=NOW, remaining=400)
    assert (await repo.feed(LIGA)).totals_at is None

    await repo.save_odds(LIGA, [match("a")], now=NOW + 60, remaining=398, totals=True)
    await repo.save_odds(LIGA, [match("a")], now=NOW + 120, remaining=397)

    state = await repo.feed(LIGA)
    assert (state.odds_at, state.totals_at, state.remaining) == (NOW + 120, NOW + 60, 397)


async def test_una_base_de_antes_gana_las_columnas_del_mas_menos(tmp_path: Path) -> None:
    # Las tablas de la 8.13.0, sin el más/menos ni los goles esperados.
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.executescript(
            """
            CREATE TABLE sports_events (
                event_id TEXT PRIMARY KEY, sport TEXT NOT NULL, home TEXT NOT NULL,
                away TEXT NOT NULL, commence REAL NOT NULL,
                odds_home INTEGER NOT NULL DEFAULT 0, odds_draw INTEGER NOT NULL DEFAULT 0,
                odds_away INTEGER NOT NULL DEFAULT 0, odds_at REAL NOT NULL,
                home_score INTEGER, away_score INTEGER
            );
            CREATE TABLE sports_feed (
                sport TEXT PRIMARY KEY, odds_at REAL, scores_at REAL, remaining INTEGER
            );
            """
        )
        connection.execute(
            "INSERT INTO sports_events VALUES "
            "('viejo', ?, 'A', 'B', ?, 210, 340, 350, ?, NULL, NULL)",
            (LIGA, NOW + 3_600, NOW),
        )
        connection.execute("INSERT INTO sports_feed VALUES (?, ?, NULL, 450)", (LIGA, NOW))

    repo = await make_repo(tmp_path)

    (old,) = await repo.upcoming(LIGA, now=NOW)
    assert (old.event_id, old.goals, dict(old.totals)) == ("viejo", None, {})
    assert (await repo.feed(LIGA)) == FeedState(NOW, None, 450, None)
    await repo.save_odds(LIGA, [priced(old, {2.5: 0.5}, FAIR)], now=NOW, remaining=449, totals=True)
    (new,) = await repo.upcoming(LIGA, now=NOW)
    assert new.goals is not None
