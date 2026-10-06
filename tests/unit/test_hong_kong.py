"""Pruebas de `hongkong`: diferencia horaria, textos de Robuso y logros."""

from __future__ import annotations

from datetime import datetime

from bot.services.achievements import hong_kong_clock_stats
from bot.services.hong_kong import (
    CANARY,
    HONG_KONG,
    ROBUSO_SCHEDULE,
    clock_text,
    clocks_at,
    long_date,
    robuso_status,
)


def canary(*args: int) -> float:
    """Epoch de una fecha y hora de Canarias."""
    return datetime(*args, tzinfo=CANARY).timestamp()


def test_en_verano_hong_kong_va_siete_horas_por_delante() -> None:
    clocks = clocks_at(canary(2026, 7, 1, 10, 0))
    assert clocks.hours_ahead == 7
    assert clocks.hong_kong.hour == 17
    assert not clocks.is_tomorrow


def test_en_invierno_va_ocho_horas_por_delante() -> None:
    clocks = clocks_at(canary(2026, 12, 1, 10, 0))
    assert clocks.hours_ahead == 8
    assert clocks.hong_kong.hour == 18


def test_por_la_tarde_en_canarias_alli_ya_es_manana() -> None:
    clocks = clocks_at(canary(2026, 10, 6, 20, 30))
    assert clocks.is_tomorrow
    text = clock_text(clocks)
    assert "03:30" in text
    assert "miércoles 7 de octubre" in text
    assert "allí ya es mañana" in text
    assert "7 horas" in text


def test_fecha_larga_en_espanol() -> None:
    assert long_date(datetime(2026, 10, 6, tzinfo=HONG_KONG)) == "martes 6 de octubre"


def test_robuso_tiene_texto_para_cada_hora() -> None:
    assert [start for start, _ in ROBUSO_SCHEDULE] == sorted(s for s, _ in ROBUSO_SCHEDULE)
    assert "durmiendo" in robuso_status(3)
    assert "almorzando" in robuso_status(12)
    assert "karaoke" in robuso_status(23)
    assert all(robuso_status(hour) for hour in range(24))


def test_logros_del_reloj_segun_la_hora_de_alli_y_de_aqui() -> None:
    # 20:30 en Canarias = 3:30 del día siguiente en Hong Kong.
    clocks = clocks_at(canary(2026, 10, 6, 20, 30))
    stats = hong_kong_clock_stats(clocks.hong_kong, clocks.canary)
    assert stats == {"hk_clock": 1, "hk_clock_tomorrow": 1, "hk_clock_sleeping": 1}

    # 3:00 en Canarias = 10:00 en Hong Kong: gira asiática de madrugada.
    clocks = clocks_at(canary(2026, 10, 6, 3, 0))
    assert hong_kong_clock_stats(clocks.hong_kong, clocks.canary) == {
        "hk_clock": 1,
        "hk_clock_tour": 1,
    }


def test_logro_de_ano_nuevo_en_el_primer_minuto_de_hong_kong() -> None:
    # 1 de enero, 0:00 en Hong Kong = 31 de diciembre, 16:00 en Canarias.
    clocks = clocks_at(canary(2026, 12, 31, 16, 0))
    stats = hong_kong_clock_stats(clocks.hong_kong, clocks.canary)
    assert stats["hk_clock_new_year"] == 1
    assert stats["hk_clock_tomorrow"] == 1
