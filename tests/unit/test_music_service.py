"""Pruebas de bot.services.music: reglas de pistas y cola, sin red ni Discord."""

from __future__ import annotations

import pytest

from bot.services.music import (
    MAX_TRACK_DURATION_SECONDS,
    MusicQueue,
    QueueFullError,
    Track,
    TrackTooLongError,
    TrackUnavailableError,
    build_track_from_info,
    format_duration,
    format_ffmpeg_headers,
    volume_percent_to_factor,
)


def make_track(title: str = "Canción de prueba") -> Track:
    """Crea una pista de prueba válida y corta."""
    return Track(
        title=title,
        webpage_url="https://example.com/video",
        stream_url="https://example.com/stream",
        duration_seconds=120,
        requested_by="Alguien",
    )


class TestFormatDuration:
    def test_formatea_minutos_y_segundos(self) -> None:
        assert format_duration(65) == "1:05"

    def test_formatea_horas_cuando_procede(self) -> None:
        assert format_duration(3725) == "1:02:05"

    def test_devuelve_en_directo_si_no_hay_duracion(self) -> None:
        assert format_duration(None) == "En directo"


class TestVolumePercentToFactor:
    def test_convierte_porcentaje_a_factor(self) -> None:
        assert volume_percent_to_factor(100) == pytest.approx(1.0)
        assert volume_percent_to_factor(50) == pytest.approx(0.5)
        assert volume_percent_to_factor(200) == pytest.approx(2.0)


class TestFormatFfmpegHeaders:
    def test_devuelve_none_si_no_hay_cabeceras(self) -> None:
        assert format_ffmpeg_headers(None) is None
        assert format_ffmpeg_headers({}) is None

    def test_formatea_cabeceras_con_crlf(self) -> None:
        headers = {"User-Agent": "prueba/1.0", "Referer": "https://example.com"}

        result = format_ffmpeg_headers(headers)

        assert result == "User-Agent: prueba/1.0\r\nReferer: https://example.com\r\n"


class TestBuildTrackFromInfo:
    def test_construye_una_pista_valida(self) -> None:
        info = {
            "title": "Mi canción",
            "webpage_url": "https://example.com/video",
            "url": "https://example.com/stream",
            "duration": 180,
        }

        track = build_track_from_info(info, requested_by="Ana")

        assert track == Track(
            title="Mi canción",
            webpage_url="https://example.com/video",
            stream_url="https://example.com/stream",
            duration_seconds=180,
            requested_by="Ana",
        )

    def test_usa_original_url_si_falta_webpage_url(self) -> None:
        info = {
            "title": "Mi canción",
            "original_url": "https://example.com/original",
            "url": "https://example.com/stream",
            "duration": 30,
        }

        track = build_track_from_info(info, requested_by="Ana")

        assert track.webpage_url == "https://example.com/original"

    def test_usa_la_url_como_titulo_si_falta(self) -> None:
        info = {
            "webpage_url": "https://example.com/video",
            "url": "https://example.com/stream",
            "duration": 30,
        }

        track = build_track_from_info(info, requested_by="Ana")

        assert track.title == "https://example.com/video"

    def test_conserva_las_cabeceras_http_de_yt_dlp(self) -> None:
        info = {
            "webpage_url": "https://example.com/video",
            "url": "https://example.com/stream",
            "duration": 30,
            "http_headers": {"User-Agent": "prueba/1.0"},
        }

        track = build_track_from_info(info, requested_by="Ana")

        assert track.http_headers == {"User-Agent": "prueba/1.0"}

    def test_sin_cabeceras_http_queda_en_none(self) -> None:
        info = {
            "webpage_url": "https://example.com/video",
            "url": "https://example.com/stream",
            "duration": 30,
        }

        track = build_track_from_info(info, requested_by="Ana")

        assert track.http_headers is None

    def test_rechaza_si_falta_la_url_de_audio(self) -> None:
        info = {"webpage_url": "https://example.com/video", "duration": 30}

        with pytest.raises(TrackUnavailableError):
            build_track_from_info(info, requested_by="Ana")

    def test_rechaza_si_falta_la_pagina_de_origen(self) -> None:
        info = {"url": "https://example.com/stream", "duration": 30}

        with pytest.raises(TrackUnavailableError):
            build_track_from_info(info, requested_by="Ana")

    def test_rechaza_duracion_desconocida(self) -> None:
        info = {
            "webpage_url": "https://example.com/video",
            "url": "https://example.com/stream",
            "duration": None,
        }

        with pytest.raises(TrackTooLongError):
            build_track_from_info(info, requested_by="Ana")

    def test_rechaza_duracion_por_encima_del_limite(self) -> None:
        info = {
            "webpage_url": "https://example.com/video",
            "url": "https://example.com/stream",
            "duration": MAX_TRACK_DURATION_SECONDS + 1,
        }

        with pytest.raises(TrackTooLongError) as excinfo:
            build_track_from_info(info, requested_by="Ana")

        assert excinfo.value.duration_seconds == MAX_TRACK_DURATION_SECONDS + 1


class TestMusicQueue:
    def test_cola_vacia_al_crear(self) -> None:
        queue = MusicQueue()

        assert len(queue) == 0
        assert queue.pop_next() is None
        assert queue.snapshot() == []

    def test_add_y_pop_next_respetan_orden_fifo(self) -> None:
        queue = MusicQueue()
        primera = make_track("Primera")
        segunda = make_track("Segunda")

        queue.add(primera)
        queue.add(segunda)

        assert len(queue) == 2
        assert queue.pop_next() == primera
        assert queue.pop_next() == segunda
        assert queue.pop_next() is None

    def test_add_respeta_el_tamano_maximo(self) -> None:
        queue = MusicQueue(max_size=1)
        queue.add(make_track("Primera"))

        with pytest.raises(QueueFullError):
            queue.add(make_track("Segunda"))

    def test_remove_at_quita_la_pista_correcta_y_mantiene_el_resto(self) -> None:
        queue = MusicQueue()
        primera = make_track("Primera")
        segunda = make_track("Segunda")
        tercera = make_track("Tercera")
        queue.add(primera)
        queue.add(segunda)
        queue.add(tercera)

        removida = queue.remove_at(2)

        assert removida == segunda
        assert queue.snapshot() == [primera, tercera]

    def test_remove_at_lanza_error_fuera_de_rango(self) -> None:
        queue = MusicQueue()
        queue.add(make_track())

        with pytest.raises(IndexError):
            queue.remove_at(5)
        with pytest.raises(IndexError):
            queue.remove_at(0)

    def test_clear_vacia_la_cola(self) -> None:
        queue = MusicQueue()
        queue.add(make_track())
        queue.add(make_track())

        queue.clear()

        assert len(queue) == 0
        assert queue.snapshot() == []

    def test_snapshot_no_permite_mutar_la_cola_interna(self) -> None:
        queue = MusicQueue()
        queue.add(make_track("Primera"))

        snapshot = queue.snapshot()
        snapshot.append(make_track("Intrusa"))

        assert len(queue) == 1
