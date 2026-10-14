"""Piezas sueltas del cog de telemetría: clases de mensaje, nombres de botón, subidas y sistema."""

from __future__ import annotations

import asyncio
import io
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import discord
import pytest

from bot.cogs import telemetry as telemetry_cog
from bot.cogs.telemetry import (
    LONG_MESSAGE,
    SHORT_MESSAGE,
    LoopWatchdog,
    Telemetry,
    _timed_upload,
    blocking_site,
    item_name,
    message_types,
    read_system_usage,
    save_report,
)
from bot.services import telemetry as telemetry_service
from bot.services.telemetry import upload_name


def message(content: str, **kwargs: object) -> MagicMock:
    fake = MagicMock(spec=discord.Message)
    fake.content = content
    fake.attachments = kwargs.get("attachments", [])
    fake.stickers = kwargs.get("stickers", [])
    fake.reference = kwargs.get("reference")
    return fake


def test_message_types_clasifica_por_largo_en_los_bordes() -> None:
    assert message_types(message("a" * SHORT_MESSAGE)) == ["corto"]
    assert message_types(message("a" * (SHORT_MESSAGE + 1))) == ["medio"]
    assert message_types(message("a" * LONG_MESSAGE)) == ["medio"]
    assert message_types(message("a" * (LONG_MESSAGE + 1))) == ["largo"]


def test_message_types_marca_adjuntos_stickers_enlaces_y_respuestas() -> None:
    found = message_types(
        message("http://x.es", attachments=[object()], stickers=[object()], reference=object())
    )
    assert found == ["adjunto", "sticker", "enlace", "respuesta", "corto"]
    # Una imagen sin texto no es ni corta ni larga.
    assert message_types(message("", attachments=[object()])) == ["adjunto"]


def test_item_name_usa_vista_y_metodo_aunque_el_custom_id_sea_al_azar() -> None:
    class Mesa(discord.ui.View):
        @discord.ui.button(label="Tirar")
        async def roll(self, interaction: discord.Interaction, _b: discord.ui.Button) -> None:
            return None

    class Apostar(discord.ui.Button):
        async def callback(self, interaction: discord.Interaction) -> None:
            return None

    async def scenario() -> None:
        view = Mesa()
        subclass = Apostar(label="x")
        plain = discord.ui.Button(label="y")

        async def doblar(interaction: discord.Interaction) -> None:
            return None

        plain.callback = doblar  # type: ignore[method-assign]
        view.add_item(subclass)
        view.add_item(plain)
        assert [item_name(view, item) for item in view.children] == [
            "Mesa.roll",
            "Mesa.Apostar",
            "Mesa.doblar",
        ]

    asyncio.run(scenario())


def test_read_system_usage_devuelve_cifras_validas() -> None:
    memory_mb, rss_mb, chromium, node = read_system_usage()
    assert memory_mb is None or memory_mb > 0
    assert rss_mb >= 0
    assert chromium >= 0 and node >= 0


def test_upload_name_quita_las_cifras_y_pone_el_tramo_de_tamano() -> None:
    assert upload_name("moneda.gif", 249_999) == "moneda.gif · <250 KB"
    assert upload_name("moneda.gif", 250_000) == "moneda.gif · 250 KB-1 MB"
    assert upload_name("gallos.gif", 2_500_000) == "gallos.gif · 1-3 MB"
    assert upload_name("premiado-123456789.png", 5_000_000) == "premiado-#.png · >3 MB"


def test_las_subidas_con_archivos_se_miden_y_las_demas_no() -> None:
    seen: list[tuple[str, float, bool]] = []
    calls: list[object] = []

    async def original(self: object, route: object, **kwargs: object) -> str:
        calls.append(route)
        if kwargs.get("fail"):
            raise discord.DiscordException("caída")
        return "ok"

    request = _timed_upload(original)

    def observer(name: str, ms: float, error: bool) -> None:
        seen.append((name, ms, error))

    async def scenario() -> None:
        assert await request(None, "sin archivos", files=None) == "ok"
        gif = discord.File(io.BytesIO(b"x" * 300_000), filename="moneda.gif")
        assert await request(None, "con archivo", files=[gif]) == "ok"
        # Medir no mueve el punto de lectura: discord.py sube el archivo entero.
        assert gif.fp.tell() == 0
        with pytest.raises(discord.DiscordException):
            await request(None, "falla", files=[gif], fail=True)

    telemetry_service.upload_observers.add(observer)
    try:
        asyncio.run(scenario())
    finally:
        telemetry_service.upload_observers.discard(observer)
    assert calls == ["sin archivos", "con archivo", "falla"]
    assert [(name, error) for name, _, error in seen] == [
        ("moneda.gif · 250 KB-1 MB", False),
        ("moneda.gif · 250 KB-1 MB", True),
    ]
    assert all(ms >= 0 for _, ms, _ in seen)


def _parado(segundos: float) -> None:
    time.sleep(segundos)


def test_el_vigia_apunta_donde_estaba_parado_el_bucle() -> None:
    watchdog = LoopWatchdog(threading.get_ident())
    watchdog.start()
    try:
        watchdog.arm(time.perf_counter())
        _parado(0.5)
        assert watchdog.disarm() == "test_telemetry_cog.py:_parado"
        # Sin pasar de LAG_CULPRIT_MS no apunta nada.
        watchdog.arm(time.perf_counter())
        assert watchdog.disarm() is None
    finally:
        watchdog.stop()


def test_el_sitio_del_bloqueo_es_la_funcion_del_bot_mas_interna() -> None:
    space: dict = {}
    code = "import sys\ndef board():\n    return sys._getframe()\n"
    exec(compile(code, "/app/bot/services/coin_scene.py", "exec"), space)  # noqa: S102
    assert blocking_site(space["board"]()) == "bot/services/coin_scene.py:board"
    assert blocking_site(sys._getframe()).startswith("test_telemetry_cog.py:")


def test_el_informe_diario_se_guarda_entero_y_se_quedan_los_ultimos(tmp_path) -> None:  # noqa: ANN001
    for day in range(1, 6):
        save_report(tmp_path, f"informe-2026-10-0{day}.json", f'{{"dia": {day}}}', keep=3)
        os.utime(tmp_path / f"informe-2026-10-0{day}.json", (day, day))
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == [f"informe-2026-10-0{day}.json" for day in (3, 4, 5)]
    assert (tmp_path / "informe-2026-10-05.json").read_text(encoding="utf-8") == '{"dia": 5}'


async def test_sin_la_carpeta_de_informes_no_se_guarda_nada(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    cog = Telemetry(MagicMock(), MagicMock())
    monkeypatch.setattr(telemetry_cog, "REPORTS_DIR", tmp_path / "no-existe")
    await cog.export_loop.coro(cog)
    cog.telemetry.report.assert_not_called()
