"""Pruebas de bot.repositories.backgammon: la ficha y los retos del día de cada miembro."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from bot.repositories.backgammon import BackgammonRepository
from bot.services.backgammon import LEVEL_BY_KEY, Ending, GameEvent, PlayerRecord, Side

GUILD = 1
OTHER_GUILD = 2
USER = 10
DAY = date(2026, 10, 14)


def game(ending: Ending = Ending.WIN, level: str | None = "normal", kind: int = 1) -> GameEvent:
    return GameEvent(
        ending=ending,
        side=Side.WHITE,
        level=LEVEL_BY_KEY[level] if level else None,
        kind=kind,
    )


async def make_repository(tmp_path: Path) -> BackgammonRepository:
    repository = BackgammonRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


async def record(
    repository: BackgammonRepository,
    event: GameEvent | None = None,
    *,
    user: int = USER,
    guild: int = GUILD,
    day: date = DAY,
    progress: dict[str, int] | None = None,
    goals: dict[str, int] | None = None,
):
    return await repository.record_game(
        guild, user, event or game(), day=day, progress=progress or {}, goals=goals or {}
    )


async def test_un_miembro_nuevo_tiene_la_ficha_vacia(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    assert await repository.get(GUILD, USER) == PlayerRecord()
    assert await repository.daily(GUILD, USER, DAY) == {}
    assert await repository.ranking(GUILD) == []


async def test_la_primera_partida_crea_la_ficha(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    result = await record(repository, game(Ending.WIN, "dificil"))
    assert result.before == PlayerRecord()
    assert result.after.games == 1 and result.after.wins == 1 and result.after.streak == 1
    assert result.after.last_day == DAY and result.after.day_streak == 1
    assert result.after.best_level == LEVEL_BY_KEY["dificil"].order
    assert await repository.get(GUILD, USER) == result.after


async def test_cada_partida_actualiza_la_ficha_que_ya_existia(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    first = await record(repository)
    second = await record(repository, game(Ending.LOSS))
    assert second.before == first.after
    assert (second.after.games, second.after.wins, second.after.losses) == (2, 1, 1)
    assert second.after.streak == 0 and second.after.best_streak == 1
    third = await record(repository, game(Ending.WIN, kind=3))
    assert (third.after.gammons, third.after.backgammons, third.after.games) == (1, 1, 3)
    assert await repository.get(GUILD, USER) == third.after


async def test_los_dias_seguidos_se_guardan_con_la_fecha(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await record(repository, day=DAY)
    await record(repository, day=DAY + timedelta(days=1))
    stored = await repository.get(GUILD, USER)
    assert stored.day_streak == 2 and stored.last_day == DAY + timedelta(days=1)


async def test_cada_servidor_y_cada_miembro_tienen_su_ficha(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await record(repository)
    assert (await repository.get(GUILD, USER)).games == 1
    assert (await repository.get(OTHER_GUILD, USER)).games == 0
    assert (await repository.get(GUILD, USER + 1)).games == 0


# -- Retos ---------------------------------------------------------------------------------


async def test_un_reto_se_cumple_al_llegar_a_su_meta(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    goals = {"jugar_3": 3}
    for expected in (1, 2):
        result = await record(repository, progress={"jugar_3": 1}, goals=goals)
        assert result.completed == () and result.progress == {"jugar_3": expected}
    result = await record(repository, progress={"jugar_3": 1}, goals=goals)
    assert result.completed == ("jugar_3",) and result.progress == {"jugar_3": 3}
    assert await repository.daily(GUILD, USER, DAY) == {"jugar_3": (3, True)}


async def test_un_reto_cumplido_no_se_vuelve_a_cumplir(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    goals = {"ganar_2": 2}
    await record(repository, progress={"ganar_2": 1}, goals=goals)
    done = await record(repository, progress={"ganar_2": 1}, goals=goals)
    assert done.completed == ("ganar_2",)
    again = await record(repository, progress={"ganar_2": 1}, goals=goals)
    assert again.completed == ()
    # Sigue sumando, pero ya cumplido y cobrado.
    assert again.progress == {"ganar_2": 3}
    assert await repository.daily(GUILD, USER, DAY) == {"ganar_2": (3, True)}


async def test_una_partida_puede_cumplir_varios_retos_y_pasarse_de_la_meta(
    tmp_path: Path,
) -> None:
    repository = await make_repository(tmp_path)
    result = await record(
        repository,
        progress={"comer_5": 6, "jugar_3": 1, "ganar_2": 1},
        goals={"comer_5": 5, "jugar_3": 3, "ganar_2": 2},
    )
    assert result.completed == ("comer_5",)
    assert result.progress == {"comer_5": 6, "jugar_3": 1, "ganar_2": 1}


async def test_un_reto_sin_meta_conocida_nunca_se_cumple(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    result = await record(repository, progress={"jugar_3": 99}, goals={})
    assert result.completed == ()
    assert await repository.daily(GUILD, USER, DAY) == {"jugar_3": (99, False)}


async def test_una_partida_sin_progreso_no_toca_los_retos(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await record(repository, progress={"jugar_3": 1}, goals={"jugar_3": 3})
    result = await record(repository, progress={}, goals={"jugar_3": 3})
    assert result.completed == () and result.progress == {"jugar_3": 1}


async def test_los_retos_de_cada_miembro_y_servidor_van_aparte(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    goals = {"jugar_3": 1}
    mine = await record(repository, progress={"jugar_3": 1}, goals=goals)
    other_guild = await record(repository, guild=OTHER_GUILD, progress={"jugar_3": 1}, goals=goals)
    other_user = await record(repository, user=USER + 1, progress={"jugar_3": 1}, goals=goals)
    assert mine.completed == other_guild.completed == other_user.completed == ("jugar_3",)


async def test_los_retos_de_dias_pasados_se_borran_al_jugar_un_dia_nuevo(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    yesterday = DAY - timedelta(days=1)
    await record(repository, day=yesterday, progress={"jugar_3": 2}, goals={"jugar_3": 3})
    await record(repository, user=USER + 1, day=yesterday, progress={"jugar_3": 1})
    assert await repository.daily(GUILD, USER, yesterday) == {"jugar_3": (2, False)}

    result = await record(repository, day=DAY, progress={"ganar_2": 1}, goals={"ganar_2": 2})
    assert await repository.daily(GUILD, USER, yesterday) == {}
    assert await repository.daily(GUILD, USER, DAY) == {"ganar_2": (1, False)}
    assert result.progress == {"ganar_2": 1}
    # Lo de otros miembros no se toca hasta que jueguen ellos.
    assert await repository.daily(GUILD, USER + 1, yesterday) == {"jugar_3": (1, False)}


async def test_jugar_otra_partida_el_mismo_dia_conserva_los_retos(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await record(repository, progress={"jugar_3": 1}, goals={"jugar_3": 3})
    await record(repository, progress={"ganar_2": 1}, goals={"ganar_2": 2})
    assert await repository.daily(GUILD, USER, DAY) == {
        "jugar_3": (1, False),
        "ganar_2": (1, False),
    }


async def test_el_progreso_de_un_dia_no_se_mezcla_con_el_de_otro(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await record(repository, progress={"jugar_3": 1}, goals={"jugar_3": 3})
    tomorrow = DAY + timedelta(days=1)
    result = await record(repository, day=tomorrow, progress={"jugar_3": 1}, goals={"jugar_3": 3})
    assert result.progress == {"jugar_3": 1}
    assert await repository.daily(GUILD, USER, tomorrow) == {"jugar_3": (1, False)}


# -- Ranking y borrado -----------------------------------------------------------------------


async def test_el_ranking_ordena_por_victorias_gammons_nivel_racha_y_menos_partidas(
    tmp_path: Path,
) -> None:
    repository = await make_repository(tmp_path)
    # 11: dos victorias contra Normal. 12: dos contra Difícil (mejor nivel).
    for _ in range(2):
        await record(repository, game(Ending.WIN, "normal"), user=11)
        await record(repository, game(Ending.WIN, "dificil"), user=12)
    # 13: tres victorias, pero una derrota en medio (mejor racha 2).
    for ending in (Ending.WIN, Ending.WIN, Ending.LOSS, Ending.WIN):
        await record(repository, game(ending, "facil"), user=13)
    # 14: tres victorias seguidas, sin perder (mejor racha 3).
    for _ in range(3):
        await record(repository, game(Ending.WIN, "facil"), user=14)
    # 15: igual que 14 pero con una partida de más, aunque la pierda.
    for ending in (Ending.WIN, Ending.WIN, Ending.WIN, Ending.LOSS):
        await record(repository, game(ending, "facil"), user=15)
    # 16: sin victorias.
    await record(repository, game(Ending.LOSS), user=16)
    # 17: dos victorias contra Fácil, una de ellas un gammon: va antes que 12 y 11.
    await record(repository, game(Ending.WIN, "facil", kind=2), user=17)
    await record(repository, game(Ending.WIN, "facil"), user=17)

    ranking = await repository.ranking(GUILD)
    assert [user for user, _record in ranking] == [14, 15, 13, 17, 12, 11, 16]
    assert ranking[0][1].wins == 3
    assert (await repository.ranking(GUILD, limit=2))[1][0] == 15


async def test_el_ranking_es_de_cada_servidor(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await record(repository, user=11)
    await record(repository, user=12, guild=OTHER_GUILD)
    assert [user for user, _record in await repository.ranking(GUILD)] == [11]
    assert [user for user, _record in await repository.ranking(OTHER_GUILD)] == [12]


async def test_olvidar_un_servidor_borra_fichas_y_retos_solo_de_ese(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    for guild in (GUILD, OTHER_GUILD):
        await record(repository, guild=guild, progress={"jugar_3": 1}, goals={"jugar_3": 3})
    await repository.forget_guild(GUILD)
    assert await repository.get(GUILD, USER) == PlayerRecord()
    assert await repository.daily(GUILD, USER, DAY) == {}
    assert await repository.ranking(GUILD) == []
    assert (await repository.get(OTHER_GUILD, USER)).games == 1
    assert await repository.daily(OTHER_GUILD, USER, DAY) == {"jugar_3": (1, False)}


async def test_initialize_se_puede_repetir_sin_perder_datos(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await record(repository)
    await repository.initialize()
    assert (await repository.get(GUILD, USER)).games == 1
