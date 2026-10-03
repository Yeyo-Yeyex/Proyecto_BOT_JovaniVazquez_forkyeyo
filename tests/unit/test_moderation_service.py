"""Pruebas de bot.services.moderation: duraciones, IDs y jerarquía de roles."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from bot.services.moderation import (
    format_duration,
    hierarchy_error,
    parse_duration,
    parse_user_id,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10", timedelta(minutes=10)),
        ("10m", timedelta(minutes=10)),
        ("30s", timedelta(seconds=30)),
        ("2h", timedelta(hours=2)),
        ("1d", timedelta(days=1)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        (" 1H 30M ", timedelta(hours=1, minutes=30)),
        ("28d", timedelta(days=28)),
    ],
)
def test_parse_duration_acepta_unidades_y_minutos_por_defecto(
    text: str, expected: timedelta
) -> None:
    """Sin unidad son minutos; las unidades s/m/h/d se pueden combinar."""
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "0", "0m", "abc", "10x", "29d", "-5m", "m"])
def test_parse_duration_rechaza_textos_invalidos_cero_y_mas_de_28_dias(text: str) -> None:
    """Lo que Discord no aceptaría como aislamiento devuelve `None`."""
    assert parse_duration(text) is None


def test_format_duration_es_corto_y_omite_unidades_vacias() -> None:
    """`1d 2h`, sin `0m 0s` de relleno."""
    assert format_duration(timedelta(days=1, hours=2)) == "1d 2h"
    assert format_duration(timedelta(minutes=10)) == "10m"
    assert format_duration(timedelta(0)) == "0s"


def test_parse_user_id_acepta_id_o_mencion() -> None:
    """El ID se puede pegar tal cual o como mención."""
    assert parse_user_id("123456789012345678") == 123456789012345678
    assert parse_user_id("<@123456789012345678>") == 123456789012345678
    assert parse_user_id("<@!123456789012345678>") == 123456789012345678
    assert parse_user_id("pepito") is None
    assert parse_user_id("123") is None


def member(member_id: int, position: int) -> SimpleNamespace:
    """Miembro mínimo: ID y posición de su rol más alto."""
    return SimpleNamespace(id=member_id, top_role=SimpleNamespace(position=position))


OWNER = 1
ADMIN = member(2, 5)
BOT = member(3, 10)


def test_hierarchy_permite_moderar_a_alguien_por_debajo_del_admin_y_del_bot() -> None:
    """Caso normal: rol inferior al de ambos."""
    assert hierarchy_error(ADMIN, member(4, 1), BOT, owner_id=OWNER) is None


def test_hierarchy_bloquea_a_uno_mismo_al_bot_y_al_dueno() -> None:
    """Nunca sobre uno mismo (salvo `allow_self`), el bot ni el dueño."""
    assert hierarchy_error(ADMIN, ADMIN, BOT, owner_id=OWNER) is not None
    assert hierarchy_error(ADMIN, ADMIN, BOT, owner_id=OWNER, allow_self=True) is None
    assert hierarchy_error(ADMIN, BOT, BOT, owner_id=OWNER) is not None
    assert hierarchy_error(ADMIN, member(OWNER, 0), BOT, owner_id=OWNER) is not None


def test_hierarchy_bloquea_roles_iguales_o_superiores_al_admin() -> None:
    """Un admin no puede con otro de su mismo nivel o superior."""
    assert "tuyo" in hierarchy_error(ADMIN, member(4, 5), BOT, owner_id=OWNER)


def test_hierarchy_el_dueno_puede_con_todos_salvo_lo_que_supera_al_bot() -> None:
    """El dueño ignora su propio rol, pero el bot sigue limitado por el suyo."""
    owner = member(OWNER, 0)
    assert hierarchy_error(owner, member(4, 9), BOT, owner_id=OWNER) is None
    assert "mío" in hierarchy_error(owner, member(4, 10), BOT, owner_id=OWNER)
