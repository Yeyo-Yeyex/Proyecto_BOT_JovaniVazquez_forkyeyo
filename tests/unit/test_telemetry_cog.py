"""Piezas sueltas del cog de telemetría: clases de mensaje, nombres de botón, subidas y sistema."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from bot import __version__
from bot.cogs import telemetry as telemetry_cog
from bot.cogs.telemetry import (
    LONG_MESSAGE,
    SHORT_MESSAGE,
    LogCounter,
    LoopWatchdog,
    Telemetry,
    _count_choices,
    _image_uploaded,
    _image_waits,
    _timed_response,
    _timed_upload,
    blocking_site,
    exception_site,
    item_name,
    message_types,
    publish_report,
    read_container_usage,
    read_system_usage,
    route_name,
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


def report_channel(last_files: list[str]) -> MagicMock:
    """Canal de informes cuyo último mensaje lleva adjuntos con esos nombres."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    last = MagicMock(attachments=[MagicMock(filename=name) for name in last_files])

    async def history(limit: int):  # noqa: ANN202, ARG001
        yield last

    channel.history = history
    return channel


async def test_el_informe_diario_se_publica_en_el_canal() -> None:
    channel = report_channel(["informe-2026-10-15.json"])
    await publish_report(channel, "informe-2026-10-16.json", '{"dia": 16}')
    sent = channel.send.await_args.kwargs["file"]
    assert sent.filename == "informe-2026-10-16.json"
    assert sent.fp.read() == b'{"dia": 16}'


async def test_un_reinicio_el_mismo_dia_no_repite_el_informe() -> None:
    channel = report_channel(["informe-2026-10-16.json"])
    await publish_report(channel, "informe-2026-10-16.json", "{}")
    channel.send.assert_not_awaited()


def test_route_name_quita_ids_y_tokens() -> None:
    url = "https://discord.com/api/v10/channels/1384280704539562054/messages/123?x=1"
    assert route_name("PATCH", url) == "PATCH /channels/#/messages/#"
    token_url = (
        "https://discord.com/api/v10/webhooks/99/aW50ZXJhY3Rpb246MTIzNDU2Nzg5/messages/@original"
    )
    assert route_name("PATCH", token_url) == "PATCH /webhooks/#/#/messages/@original"


def test_los_429_del_log_se_cuentan_por_ruta() -> None:
    async def scenario() -> MagicMock:
        telemetry = MagicMock()
        handler = LogCounter(telemetry, asyncio.get_running_loop())
        record = logging.LogRecord(
            "discord.http",
            logging.WARNING,
            "",
            0,
            "We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds.",
            ("PUT", "https://discord.com/api/v10/guilds/1/members/2/roles/3", 1.5),
            None,
        )
        handler.emit(record)
        return telemetry

    telemetry = asyncio.run(scenario())
    kinds = [call.args[:2] for call in telemetry.count.call_args_list]
    assert ("ratelimit", "PUT /guilds/#/members/#/roles/#") in kinds
    assert kinds[0][0] == "log"


def test_la_primera_imagen_de_una_interaccion_cuenta_su_espera(monkeypatch) -> None:  # noqa: ANN001
    seen: list[tuple[str, str, float | None]] = []

    def observer(kind: str, name: str, ms: float | None, error: bool) -> None:
        seen.append((kind, name, ms))

    route = MagicMock(webhook_token="tok")
    gif = discord.File(io.BytesIO(b"x"), filename="moneda123.gif")
    data = discord.File(io.BytesIO(b"x"), filename="informe.json")
    telemetry_service.event_observers.add(observer)
    try:
        _image_waits["tok"] = 10.0
        _image_uploaded(route, [data], 10.5)  # sin imagen: no cuenta ni gasta el token
        _image_uploaded(route, [gif], 11.25)
        _image_uploaded(route, [gif], 12.0)  # la segunda imagen ya no cuenta
        _image_uploaded("sin token", [gif], 12.0)
    finally:
        telemetry_service.event_observers.discard(observer)
        _image_waits.pop("tok", None)
    assert seen == [("imagen", "moneda#.gif", 1250.0)]


def test_solo_cuentan_las_vistas_que_caducan_sin_pararse() -> None:
    seen: list[str] = []

    def observer(kind: str, name: str, ms: float | None, error: bool) -> None:
        seen.append(f"{kind}:{name}")

    class Mesa(discord.ui.View):
        pass

    async def scenario() -> None:
        telemetry_cog.install_hooks()
        abierta, parada = Mesa(timeout=60), Mesa(timeout=60)
        parada.stop()
        abierta._dispatch_timeout()
        parada._dispatch_timeout()

    telemetry_service.event_observers.add(observer)
    try:
        asyncio.run(scenario())
    finally:
        telemetry_service.event_observers.discard(observer)
    assert [event for event in seen if event.startswith("caducada")] == ["caducada:Mesa"]


def test_read_container_usage_lee_cgroup_base_de_datos_y_disco(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "cpu.stat").write_text("usage_usec 5000000\nuser_usec 1\n")
    (tmp_path / "memory.events").write_text("low 0\noom 2\noom_kill 1\n")
    monkeypatch.setattr(telemetry_cog, "CGROUP_CPU_FILE", tmp_path / "cpu.stat")
    monkeypatch.setattr(telemetry_cog, "CGROUP_EVENTS_FILE", tmp_path / "memory.events")
    database = tmp_path / "bot.sqlite3"
    database.write_bytes(b"x" * 2**20)
    (tmp_path / "bot.sqlite3-wal").write_bytes(b"x" * 2**19)
    cpu_us, oom_kills, db_mb, disk_free_mb = read_container_usage(database)
    assert (cpu_us, oom_kills, db_mb) == (5_000_000.0, 1, 1.5)
    assert disk_free_mb is not None and disk_free_mb > 0
    monkeypatch.setattr(telemetry_cog, "CGROUP_CPU_FILE", tmp_path / "no-existe")
    assert read_container_usage(None)[0] is None


def test_la_cpu_es_la_del_ultimo_minuto_sobre_todos_los_hilos(monkeypatch) -> None:  # noqa: ANN001
    cog = Telemetry(MagicMock(), MagicMock())
    moments = iter([100.0, 160.0, 220.0])
    monkeypatch.setattr(telemetry_cog.time, "perf_counter", lambda: next(moments))
    monkeypatch.setattr(telemetry_cog, "cpu_count", lambda: 4)
    assert cog._cpu_pct(0.0) is None  # la primera lectura no tiene con qué comparar
    # 120 s de CPU en 60 s con 4 hilos: la mitad.
    assert cog._cpu_pct(120e6) == 50.0
    assert cog._cpu_pct(None) is None


async def test_el_arranque_se_cuenta_una_vez_con_la_version(monkeypatch) -> None:  # noqa: ANN001
    bot = MagicMock(spec=[])
    telemetry = MagicMock()
    for _ in range(2):
        cog = Telemetry(bot, telemetry)
        for loop in (cog.flush_loop, cog.prune_loop, cog.export_loop):
            monkeypatch.setattr(loop, "start", lambda: None)
        monkeypatch.setattr(
            telemetry_cog.asyncio, "create_task", lambda coro, name=None: coro.close()
        )
        monkeypatch.setattr(telemetry_cog, "LoopWatchdog", MagicMock())
        await cog.cog_load()
        logging.getLogger().removeHandler(cog._log_counter)
        telemetry_service.render_observers.discard(cog._on_render)
        telemetry_service.upload_observers.discard(cog._on_upload)
        telemetry_service.event_observers.discard(cog._on_event)
    starts = [c for c in telemetry.count.call_args_list if c.args[0] == "arranque"]
    assert len(starts) == 1 and starts[0].args[1] == __version__


@contextlib.contextmanager
def captured_events() -> Iterator[list[tuple[str, str, float | None, bool]]]:
    """Lo que avisa `report_event` mientras dura el bloque."""
    seen: list[tuple[str, str, float | None, bool]] = []

    def observer(kind: str, name: str, ms: float | None, error: bool) -> None:
        seen.append((kind, name, ms, error))

    telemetry_service.event_observers.add(observer)
    try:
        yield seen
    finally:
        telemetry_service.event_observers.discard(observer)


def test_cada_vista_cuenta_al_abrirse_al_pararse_y_lo_que_duro() -> None:
    class Panel(discord.ui.View):
        pass

    async def scenario() -> None:
        telemetry_cog.install_hooks()
        panel = Panel(timeout=60)
        panel.stop()
        panel.stop()  # parar dos veces no cuenta dos cierres

    with captured_events() as seen:
        asyncio.run(scenario())
    kinds = [(kind, name) for kind, name, _ms, _error in seen]
    assert kinds == [("vista", "Panel"), ("vista_cerrada", "Panel")]
    assert seen[1][2] is not None and seen[1][2] >= 0


def test_un_10062_se_cuenta_con_el_boton_que_lo_pidio() -> None:
    response = MagicMock(status=404, reason="Not Found")

    async def original(self: object, *args: object, **kwargs: object) -> None:
        raise discord.NotFound(response, {"code": 10062, "message": "Unknown interaction"})

    respond = _timed_response(original)
    interaction = MagicMock(id=77, command=None)
    interaction.created_at = discord.utils.utcnow()
    parent = MagicMock(_parent=interaction)
    parent.is_done.return_value = False

    async def scenario() -> None:
        telemetry_cog._remember(interaction, "CoinView.heads")
        with pytest.raises(discord.NotFound):
            await respond(parent)

    with captured_events() as seen:
        asyncio.run(scenario())
    telemetry_cog._item_names.pop(77, None)
    assert [(kind, name) for kind, name, _ms, _error in seen] == [("10062", "CoinView.heads")]


def test_cada_opcion_de_un_desplegable_se_cuenta_sin_cifras() -> None:
    telemetry = telemetry_service.TelemetryService(None)
    interaction = MagicMock(guild_id=1)
    interaction.client = MagicMock(telemetry=telemetry)
    _count_choices(interaction, "HelpView.callback", ["🎰 Casino", "artículo 12345"])
    names = sorted(key.name for key in telemetry.drain().counters)
    assert names == ["HelpView.callback → artículo #", "HelpView.callback → 🎰 Casino"]


def test_las_imagenes_subidas_avisan_de_su_peso_en_kb() -> None:
    async def original(self: object, route: object, **kwargs: object) -> str:
        return "ok"

    request = _timed_upload(original)
    gif = discord.File(io.BytesIO(b"x" * 2048), filename="moneda123.gif")
    data = discord.File(io.BytesIO(b"x" * 4096), filename="informe.json")

    with captured_events() as seen:
        asyncio.run(request(None, "ruta", files=[gif, data]))
    assert [(k, n, ms) for k, n, ms, _e in seen if k == "peso"] == [("peso", "moneda#.gif", 2.0)]


def test_las_excepciones_se_cuentan_por_tipo_y_funcion_del_bot() -> None:
    space: dict = {"KeyError": KeyError}
    code = "def settle():\n    raise KeyError('x')\n"
    exec(compile(code, "/app/bot/cogs/coin.py", "exec"), space)  # noqa: S102
    try:
        space["settle"]()
    except KeyError as error:
        trace = error.__traceback__
    assert exception_site(trace) == "bot/cogs/coin.py:settle"
    try:
        int("x")
    except ValueError as error:
        # Sin funciones del bot, la última de la traza.
        assert exception_site(error.__traceback__).endswith(
            ":test_las_excepciones_se_cuentan_por_tipo_y_funcion_del_bot"
        )
    # En el NAS el bot va instalado como paquete, dentro de `site-packages`.
    installed = "/usr/local/lib/python3.13/site-packages/bot/cogs/coin.py"
    exec(compile(code, installed, "exec"), space)  # noqa: S102
    try:
        space["settle"]()
    except KeyError as error:
        assert exception_site(error.__traceback__) == "bot/cogs/coin.py:settle"

    async def scenario() -> MagicMock:
        telemetry = MagicMock()
        handler = LogCounter(telemetry, asyncio.get_running_loop())
        try:
            space["settle"]()
        except KeyError:
            record = logging.LogRecord(
                "bot.cogs.coin", logging.ERROR, "", 0, "falló", (), sys.exc_info()
            )
        handler.emit(record)
        return telemetry

    telemetry = asyncio.run(scenario())
    kinds = [call.args[:2] for call in telemetry.count.call_args_list]
    assert ("excepcion", "KeyError · bot/cogs/coin.py:settle") in kinds


async def test_comandos_mal_escritos_se_cuentan_como_uso_mal() -> None:
    telemetry = MagicMock()
    bot = MagicMock()
    cog = Telemetry(bot, telemetry)
    ctx = MagicMock()
    ctx.command.qualified_name = "informe"
    ctx.guild.id = 1
    await cog.on_command_error(ctx, commands.BadArgument("no es un número"))
    message = MagicMock(spec=discord.Message)
    message.guild = MagicMock(id=1)
    message.author = bot.user
    message.author.bot = True
    message.content = "Uso: `.tajo` (estado), `.tajo #canal` o `.tajo todos`."
    await cog.on_message(message)
    wrong = [call.args[1] for call in telemetry.count.call_args_list if call.args[0] == "uso_mal"]
    assert wrong == ["informe", "tajo"]
