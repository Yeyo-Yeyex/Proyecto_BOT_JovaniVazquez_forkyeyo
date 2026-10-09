"""Pruebas de `perfil`: secciones, rachas, logros de mirar y quién maneja el panel.

Las secciones de verdad (nivel, patrimonio, trabajo, logros y mochila) las pinta
cada cog; que lleguen con el bot real lo prueba `tests/integration/test_cog_bridges.py`.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import discord
from interaction_fakes import fake_interaction

from bot.cogs.perfil import (
    SECTIONS,
    STREAKS,
    Perfil,
    PerfilView,
    perfil_stats,
    streaks_embed,
)
from bot.services.achievements import BY_ID, PERFIL_SECTIONS, with_derived
from bot.services.levels import TIMEZONE

GUILD_ID = 1


def member(user_id: int, name: str = "Diego", *, bot: bool = False) -> MagicMock:
    found = MagicMock(spec=discord.Member)
    found.id = user_id
    found.bot = bot
    found.display_name = name
    return found


def guild() -> MagicMock:
    found = MagicMock(spec=discord.Guild)
    found.id = GUILD_ID
    return found


def test_el_menu_tiene_las_siete_secciones_y_cabe_en_un_desplegable() -> None:
    assert [key for key, *_ in SECTIONS] == list(PERFIL_SECTIONS)
    assert len(SECTIONS) <= 25


def test_las_rachas_ensenan_solo_los_records_que_hay() -> None:
    embed = streaks_embed("Diego", {"work_streak_max": 12, "imv_streak_max": 0}, writing_now=3)

    assert "3 días seguidos escribiendo" in embed.description
    assert "Días seguidos fichando: **12** días" in embed.fields[0].value
    assert "IMV" not in embed.fields[0].value


def test_sin_rachas_lo_dice() -> None:
    embed = streaks_embed("Diego", {}, writing_now=0)

    assert "no llevas racha" in embed.description
    assert "Ni una racha" in embed.fields[0].value


def test_cada_racha_del_perfil_es_una_estadistica_que_cuentan_los_logros() -> None:
    counted = {stat for achievement in BY_ID.values() for stat, _ in achievement.conditions}
    assert {stat for stat, *_ in STREAKS} <= counted


def test_mirarse_el_perfil_de_madrugada_es_secreto_y_el_de_otro_es_cotilleo() -> None:
    diego, ana = member(1), member(2, "Ana")
    night = datetime(2026, 10, 9, 3, 30, tzinfo=TIMEZONE).timestamp()
    noon = datetime(2026, 10, 9, 12, 0, tzinfo=TIMEZONE).timestamp()

    assert perfil_stats(diego, diego, night).add == {
        "perfil_views": 1,
        "perfil_seen_resumen": 1,
        "perfil_night": 1,
    }
    assert "perfil_night" not in perfil_stats(diego, diego, noon).add
    assert perfil_stats(diego, ana, night).add == {"perfil_others": 1}


def test_expediente_completo_cuenta_las_secciones_distintas() -> None:
    seen = {f"perfil_seen_{key}": 1 for key in PERFIL_SECTIONS[:-1]}
    assert with_derived(seen)["perfil_sections"] == len(PERFIL_SECTIONS) - 1
    seen["perfil_seen_objetos"] = 3
    assert with_derived(seen)["perfil_sections"] == len(PERFIL_SECTIONS)


async def test_los_bots_no_tienen_perfil() -> None:
    cog = Perfil(MagicMock())

    result = await cog.open(
        guild=guild(), author=member(1), target=member(9, bot=True), channel=None
    )

    assert isinstance(result, str)
    assert "bots" in result


async def test_fuera_de_un_servidor_no_hay_perfil() -> None:
    result = await Perfil(MagicMock()).open(guild=None, author=member(1), target=None, channel=None)
    assert isinstance(result, str)


async def test_solo_quien_abre_el_perfil_lo_maneja() -> None:
    owner, other = member(1), member(2, "Ana")
    view = PerfilView(Perfil(MagicMock()), guild=guild(), owner=owner, target=owner, channel=None)

    assert await view.interaction_check(fake_interaction(owner))
    intruder = fake_interaction(other)
    assert not await view.interaction_check(intruder)
    assert intruder.response.is_done()


def test_los_botones_cambian_con_la_seccion() -> None:
    owner, other = member(1), member(2, "Ana")
    mine = PerfilView(Perfil(MagicMock()), guild=guild(), owner=owner, target=owner, channel=None)
    assert len(mine.children) == 1  # solo el menú en el resumen
    mine.key = "logros"
    mine.rebuild()
    assert "categorías" in mine.children[1].label
    theirs = PerfilView(Perfil(MagicMock()), guild=guild(), owner=owner, target=other, channel=None)
    theirs.key = "objetos"
    theirs.rebuild()
    assert "solo mirar" in theirs.children[1].label
