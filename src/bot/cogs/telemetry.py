"""Telemetría interna: cuenta cuánto se usa cada cosa del bot y cómo va el NAS.

Es para decidir mejoras, no para los miembros: no tiene comandos propios (el
informe lo saca `informe`, en el cog `Admin`), no da logros y no mueve dinero.
Nunca guarda el contenido de un mensaje: solo cuántos hay, en qué canal y de qué
clase (con adjunto, enlace, respuesta, corto o largo).

Qué cuenta y dónde lo engancha (tipos de `bot.services.telemetry`):

- `slash` y `text`: comandos terminados (`on_app_command_completion`,
  `on_command_completion`) y fallidos (`on_command_error` aquí; los de `/`, en
  el cog `Errors`).
- `button` y `modal`: cada clic o formulario, con el nombre `Vista.método`
  (`CoinView.heads`). discord.py no avisa de los clics con un evento que diga qué
  botón fue, así que se envuelve el método que los despacha (ver `install_hooks`).
  Un clic que termina sin respuesta a Discord cuenta como error: es el «Esta
  interacción ha fallado» que ve el miembro. Los botones que atiende un listener
  (marcados con `LISTENER_ITEM`) no los cuenta la vista sino el listener.
- Latencia de la primera respuesta (`defer`, `send_message`, `edit_message`,
  `send_modal`), desde que Discord creó la interacción. Es lo que tiene que
  quedar por debajo de 3 segundos.
- `message`, `message_type`, `reaction`, `voice` (minutos de persona en cada
  canal, leídos de la caché cada minuto) y `member` (altas y bajas).
- `log`: avisos y errores del log, por plantilla del mensaje (sin sus datos).
- `render`: lo que tarda cada dibujo de `NodeScene` y `BrowserScene`.
- `upload`: lo que tarda cada mensaje con archivos en subir a Discord, por nombre
  del archivo y tamaño. Se envuelve donde discord.py hace las peticiones con
  archivos (`HTTPClient.request` y, para las interacciones, el adaptador de
  webhooks).
- `lag`: cada vez que el bucle de eventos se para más de `LAG_CULPRIT_MS`, dónde
  estaba parado (la función del bot, `LoopWatchdog`) y cuánto.
- Al arrancar y una vez al día, el informe de `informe` se deja en `REPORTS_DIR`
  (`informes/`, montada desde el NAS) para leerlo por la red.
- Cada minuto, una muestra del sistema: memoria del contenedor (lo que vigila el
  `mem_limit` de `docker-compose.yml`), memoria del proceso, retraso del bucle
  de eventos, latencia con Discord y procesos de Chromium y Node abiertos.

Todo se suma en memoria y se escribe una vez por minuto (`TelemetryService.flush`),
así que contar un clic nunca abre la base de datos antes de contestar a Discord.
Al descargar el cog (también al cerrar el bot) se vuelca lo pendiente.

Necesita los intents de miembros y de contenido de mensajes que ya pide el bot
(`bot.app.build_intents`); no hace llamadas a la API de Discord.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import io
import json
import logging
import math
import os
import re
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import discord
import discord.webhook.async_
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui.view import BaseView, ViewStore

from bot.services import telemetry as telemetry_service
from bot.services.telemetry import LISTENER_ITEM, SystemSample, TelemetryService, local_moment

logger = logging.getLogger(__name__)

#: Cada cuánto se vuelcan los contadores a SQLite y se toma la muestra del sistema.
FLUSH_SECONDS = 60
#: Cada cuánto se mira si el bucle de eventos va con retraso.
LAG_PROBE_SECONDS = 0.5
#: Retraso del bucle a partir del cual se apunta qué lo tenía parado (tipo `lag`).
LAG_CULPRIT_MS = 250
#: Cada cuánto mira el vigía (`LoopWatchdog`) si el bucle sigue parado.
LAG_WATCH_SECONDS = 0.05
#: Carpeta donde se deja cada día el informe, montada desde el NAS
#: (`docker-compose.yml`) para leerlo por la red; si no existe, no se guarda.
REPORTS_DIR = Path("informes")
#: Días que abarca el informe diario y cuántos informes se guardan.
DAILY_REPORT_DAYS = 30
KEPT_REPORTS = 14
#: Mensajes de este tamaño o menos son «cortos»; de más de `LONG_MESSAGE`, «largos».
SHORT_MESSAGE = 20
LONG_MESSAGE = 200
#: Caracteres de la plantilla de un aviso del log que entran en su nombre.
LOG_TEMPLATE_LENGTH = 80

# Procesos que cuentan como Chromium o Node en la muestra del sistema (`/proc/*/comm`).
CHROMIUM_NAMES = frozenset({"chrome", "chromium", "headless_shell", "chrome_crashpad"})
NODE_NAMES = frozenset({"node"})
# Memoria del contenedor: cgroup v2 y, si no, v1.
CGROUP_MEMORY_FILES = (
    Path("/sys/fs/cgroup/memory.current"),
    Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
)
LINK = re.compile(r"https?://")

# Atributo que marca un método ya envuelto, para no envolverlo dos veces: cada
# `load_extension` vuelve a ejecutar este módulo, pero las clases de discord.py
# son las mismas.
_WRAPPED = "_telemetry_wrapped"
# Respuestas que cuentan como «primera respuesta» de una interacción.
FIRST_RESPONSES = ("defer", "send_message", "edit_message", "send_modal")


def _telemetry_of(client: object) -> TelemetryService | None:
    """La telemetría del bot, o `None` si ese cliente no tiene (pruebas, dobles)."""
    found = getattr(client, "telemetry", None)
    return found if isinstance(found, TelemetryService) else None


def _user_id(interaction: discord.Interaction) -> int | None:
    user = getattr(interaction, "user", None)
    return getattr(user, "id", None)


def _answered(interaction: discord.Interaction) -> None:
    """Apunta lo que tardó la primera respuesta, desde que Discord creó la interacción."""
    telemetry = _telemetry_of(interaction.client)
    if telemetry is None:
        return
    elapsed = (discord.utils.utcnow() - interaction.created_at).total_seconds() * 1000
    # Con el reloj del NAS un poco adelantado podría salir negativo.
    telemetry.answered(interaction.id, max(0.0, elapsed))


def _count_interaction(interaction: discord.Interaction, kind: str, name: str) -> None:
    """Cuenta un clic o formulario terminado, con su latencia y si se quedó sin respuesta."""
    telemetry = _telemetry_of(interaction.client)
    if telemetry is None:
        return
    telemetry.count(
        kind,
        name,
        guild_id=interaction.guild_id,
        user_id=_user_id(interaction),
        ms=telemetry.take_answer(interaction.id),
        error=not interaction.response.is_done(),
    )


def item_name(view: object, item: discord.ui.Item[Any]) -> str:
    """Nombre estable de un botón o menú: `Vista.método` (`CoinView.heads`).

    El `custom_id` no sirve: si no se fija, discord.py pone uno al azar. Se usa el
    nombre de la función del callback (`@discord.ui.button`), el de la clase del
    botón si es una subclase con su `callback`, o el de la función asignada.
    """
    callback = item.callback
    inner = getattr(callback, "callback", None)
    if inner is not None:
        part = getattr(inner, "__name__", type(item).__name__)
    elif getattr(callback, "__self__", None) is item:
        part = type(item).__name__
    else:
        part = getattr(callback, "__name__", type(item).__name__)
    return f"{type(view).__name__}.{part}"


def _wrap(owner: type, attribute: str, wrapper: Callable[[Any], Any]) -> None:
    """Sustituye `owner.attribute` por `wrapper(original)` una sola vez por proceso."""
    original = getattr(owner, attribute)
    if getattr(original, _WRAPPED, False):
        return
    wrapped = wrapper(original)
    setattr(wrapped, _WRAPPED, True)
    setattr(owner, attribute, wrapped)


def _timed_response(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def respond(self: discord.InteractionResponse, *args: Any, **kwargs: Any) -> Any:
        first = not self.is_done()
        result = await original(self, *args, **kwargs)
        if first:
            _answered(self._parent)
        return result

    return respond


def _counted_view_task(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def run(self: BaseView, item: discord.ui.Item[Any], interaction: Any) -> Any:
        try:
            return await original(self, item, interaction)
        finally:
            if not getattr(item, LISTENER_ITEM, False):
                _count_interaction(interaction, "button", item_name(self, item))

    return run


def _counted_modal_task(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def run(self: discord.ui.Modal, interaction: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await original(self, interaction, *args, **kwargs)
        finally:
            _count_interaction(interaction, "modal", type(self).__name__)

    return run


def _counted_dynamic_task(
    original: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def run(
        self: ViewStore, component_type: int, factory: type, interaction: Any, *args: Any
    ) -> Any:
        try:
            return await original(self, component_type, factory, interaction, *args)
        finally:
            _count_interaction(interaction, "button", factory.__name__)

    return run


def _upload_size(file: discord.File) -> int:
    """Bytes de un archivo por subir, sin mover el punto de lectura."""
    try:
        position = file.fp.tell()
        size = file.fp.seek(0, io.SEEK_END)
        file.fp.seek(position)
    except (OSError, ValueError):
        return 0
    return size


def _timed_upload(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def request(self: Any, *args: Any, **kwargs: Any) -> Any:
        files = kwargs.get("files") or ()
        if not files:
            return await original(self, *args, **kwargs)
        name = telemetry_service.upload_name(
            files[0].filename or "archivo", sum(_upload_size(file) for file in files)
        )
        start = time.perf_counter()
        failed = True
        try:
            result = await original(self, *args, **kwargs)
            failed = False
            return result
        finally:
            telemetry_service.report_upload(
                name, (time.perf_counter() - start) * 1000, error=failed
            )

    return request


def install_hooks() -> None:
    """Envuelve los puntos de discord.py por donde pasan todas las respuestas y clics.

    discord.py no tiene un evento «se ha contestado a una interacción» ni dice qué
    botón se pulsó (solo un `custom_id`, que muchas vistas dejan al azar), así que
    se envuelven:

    - `InteractionResponse.defer`, `send_message`, `edit_message` y `send_modal`:
      la primera de ellas marca la latencia de la interacción;
    - `BaseView._scheduled_task`, `Modal._scheduled_task` y
      `ViewStore.schedule_dynamic_item_call`: corren el callback de cada botón,
      formulario o botón dinámico y saben cuál es;
    - `HTTPClient.request` y `AsyncWebhookAdapter.request`: por ahí salen todos
      los mensajes con archivos (los de las interacciones, por el segundo) y se
      mide lo que tarda la subida.

    Los tres últimos son métodos internos: si discord.py los cambia, la prueba de
    integración de la telemetría falla. Los envoltorios no cambian lo que hace el
    método; si el cliente no tiene telemetría, no hacen nada. Es idempotente y no
    se deshace al descargar el cog (sin telemetría no cuentan nada).
    """
    for name in FIRST_RESPONSES:
        _wrap(discord.InteractionResponse, name, _timed_response)
    _wrap(BaseView, "_scheduled_task", _counted_view_task)
    _wrap(discord.ui.Modal, "_scheduled_task", _counted_modal_task)
    _wrap(ViewStore, "schedule_dynamic_item_call", _counted_dynamic_task)
    _wrap(discord.http.HTTPClient, "request", _timed_upload)
    _wrap(discord.webhook.async_.AsyncWebhookAdapter, "request", _timed_upload)


def message_types(message: discord.Message) -> list[str]:
    """Clases de un mensaje para `message_type`; mira el texto sin guardarlo."""
    content = message.content or ""
    kinds = []
    if message.attachments:
        kinds.append("adjunto")
    if message.stickers:
        kinds.append("sticker")
    if LINK.search(content):
        kinds.append("enlace")
    if message.reference is not None:
        kinds.append("respuesta")
    if content:
        if len(content) <= SHORT_MESSAGE:
            kinds.append("corto")
        elif len(content) <= LONG_MESSAGE:
            kinds.append("medio")
        else:
            kinds.append("largo")
    return kinds


def read_system_usage() -> tuple[float | None, float, int, int]:
    """Memoria del contenedor y del proceso (MB) y procesos de Chromium y Node.

    Lee `/sys` y `/proc`, así que es bloqueante: se llama con `asyncio.to_thread`.
    Fuera de Linux devuelve ceros y `None`.
    """
    memory_mb = None
    for path in CGROUP_MEMORY_FILES:
        try:
            memory_mb = int(path.read_text().strip()) / 2**20
            break
        except (OSError, ValueError):
            continue
    rss_mb = 0.0
    # Sin `/proc` (fuera de Linux) la memoria del proceso se queda en 0.
    with contextlib.suppress(OSError):
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss_mb = int(line.split()[1]) / 1024
                break
    chromium = node = 0
    try:
        pids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        try:
            command = Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            # El proceso ha terminado entre listar y leer.
            continue
        if command in CHROMIUM_NAMES:
            chromium += 1
        elif command in NODE_NAMES:
            node += 1
    return memory_mb, rss_mb, chromium, node


def blocking_site(frame: Any) -> str:
    """Dónde está parado un hilo: la función del bot más interna de su pila.

    `bot/services/coin_scene.py:board`. Si no hay ninguna del bot, la más
    interna sin más (`selectors.py:select` quiere decir que el bucle esperaba
    y lo que faltaba era CPU).
    """
    innermost = frame
    while frame is not None:
        parts = Path(frame.f_code.co_filename).parts
        if "bot" in parts:
            start = len(parts) - 1 - parts[::-1].index("bot")
            return f"{'/'.join(parts[start:])}:{frame.f_code.co_name}"
        frame = frame.f_back
    return f"{Path(innermost.f_code.co_filename).name}:{innermost.f_code.co_name}"


class LoopWatchdog:
    """Vigila desde otro hilo si el bucle de eventos se queda parado y apunta dónde.

    `_probe_lag` le dice cuándo tendría que despertar (`arm`). Si pasan
    `LAG_CULPRIT_MS` de esa hora y no ha despertado, el bucle está ocupado en
    otra cosa: se lee la pila de su hilo (`blocking_site`). Si el bloqueo es
    código en C que no suelta el GIL, el vigía no despierta hasta que acaba y
    puede apuntar lo que vino justo después.

    Args:
        loop_thread: El `threading.get_ident()` del hilo del bucle.
    """

    def __init__(self, loop_thread: int) -> None:
        self.loop_thread = loop_thread
        self.due = math.inf
        self.culprit: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="telemetria-vigia", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def arm(self, due: float) -> None:
        """El bucle tendría que despertar a la hora `due` (`time.perf_counter`)."""
        self.due = math.inf
        self.culprit = None
        self.due = due

    def disarm(self) -> str | None:
        """Ha despertado: dónde estaba parado, si ha llegado a pasar de `LAG_CULPRIT_MS`."""
        self.due = math.inf
        return self.culprit

    def _run(self) -> None:
        while not self._stop.wait(LAG_WATCH_SECONDS):
            late = time.perf_counter() - self.due
            if self.culprit is None and late * 1000 > LAG_CULPRIT_MS:
                frame = sys._current_frames().get(self.loop_thread)
                if frame is not None:
                    self.culprit = blocking_site(frame)


def report_inputs(bot: commands.Bot, guild: discord.Guild) -> tuple[list[str], dict[int, str]]:
    """Los comandos y los nombres de canal con los que se arma el informe de `guild`."""
    channel_names = {channel.id: channel.name for channel in guild.channels}
    channel_names.update({thread.id: thread.name for thread in guild.threads})
    # Los efectos de imagen son más de cien comandos de texto: si se listaran,
    # `comandos_sin_uso` sería casi entero de efectos.
    names = [c.name for c in bot.commands if "help_group" not in c.extras]
    return names, channel_names


def save_report(folder: Path, name: str, data: str, *, keep: int = KEPT_REPORTS) -> None:
    """Escribe el informe `name` en `folder` y borra los más viejos, dejando `keep`.

    Se escribe aparte y se renombra: quien lo lea por la red nunca ve uno a medias.
    """
    path = folder / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(data, encoding="utf-8")
    temporary.replace(path)
    reports = sorted(folder.glob("informe*.json"), key=lambda p: p.stat().st_mtime)
    for old in reports[:-keep]:
        old.unlink(missing_ok=True)


class LogCounter(logging.Handler):
    """Cuenta los avisos y errores del log por plantilla, sin sus argumentos.

    Se cuenta `record.msg` («%s no está disponible; …») y no el mensaje ya
    formateado, que podría llevar nombres o IDs. Los registros que llegan desde
    otros hilos (`asyncio.to_thread`) se pasan al bucle de eventos, porque los
    contadores no son seguros entre hilos.
    """

    def __init__(self, telemetry: TelemetryService, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(level=logging.WARNING)
        self.telemetry = telemetry
        self.loop = loop
        self.loop_thread = threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        name = f"{record.levelname} {record.name}: {str(record.msg)[:LOG_TEMPLATE_LENGTH]}"
        if threading.get_ident() == self.loop_thread:
            self.telemetry.count("log", name, guild_id=None)
        elif not self.loop.is_closed():
            self.loop.call_soon_threadsafe(
                functools.partial(self.telemetry.count, "log", name, guild_id=None)
            )


class Telemetry(commands.Cog):
    """Escucha eventos y cuenta en `bot.telemetry`; vuelca cada minuto."""

    def __init__(self, bot: commands.Bot, telemetry: TelemetryService) -> None:
        self.bot = bot
        self.telemetry = telemetry
        self._log_counter: LogCounter | None = None
        self._lag_task: asyncio.Task[None] | None = None
        self._max_lag_ms = 0.0
        self._watchdog: LoopWatchdog | None = None

    async def cog_load(self) -> None:
        """Engancha discord.py, el log y las escenas, y arranca los volcados."""
        install_hooks()
        self._log_counter = LogCounter(self.telemetry, asyncio.get_running_loop())
        logging.getLogger().addHandler(self._log_counter)
        telemetry_service.render_observers.add(self._on_render)
        telemetry_service.upload_observers.add(self._on_upload)
        self.flush_loop.start()
        self.prune_loop.start()
        self.export_loop.start()
        self._watchdog = LoopWatchdog(threading.get_ident())
        self._watchdog.start()
        self._lag_task = asyncio.create_task(self._probe_lag(), name="telemetria-retraso")

    async def cog_unload(self) -> None:
        """Para los volcados, suelta los enganches y guarda lo pendiente."""
        self.flush_loop.cancel()
        self.prune_loop.cancel()
        self.export_loop.cancel()
        if self._lag_task is not None:
            self._lag_task.cancel()
        if self._watchdog is not None:
            self._watchdog.stop()
        telemetry_service.render_observers.discard(self._on_render)
        telemetry_service.upload_observers.discard(self._on_upload)
        if self._log_counter is not None:
            logging.getLogger().removeHandler(self._log_counter)
        await self.telemetry.flush()

    def _on_render(self, scene: str, ms: float) -> None:
        self.telemetry.count("render", scene, guild_id=None, ms=ms)

    def _on_upload(self, name: str, ms: float, error: bool) -> None:
        self.telemetry.count("upload", name, guild_id=None, ms=ms, error=error)

    # --- Bucles -------------------------------------------------------------

    async def _probe_lag(self) -> None:
        """Mide cuánto tarda en despertar un `sleep` corto: el retraso del bucle.

        Si algo bloquea el bucle de eventos (un cálculo o una lectura de disco sin
        `to_thread`), todos los botones contestan tarde a la vez. Se guarda el peor
        retraso de cada minuto y, si pasa de `LAG_CULPRIT_MS`, qué lo tenía parado
        (tipo `lag`, con el nombre de `blocking_site`).
        """
        await self.bot.wait_until_ready()
        while True:
            start = time.perf_counter()
            if self._watchdog is not None:
                self._watchdog.arm(start + LAG_PROBE_SECONDS)
            await asyncio.sleep(LAG_PROBE_SECONDS)
            lag = (time.perf_counter() - start - LAG_PROBE_SECONDS) * 1000
            self._max_lag_ms = max(self._max_lag_ms, lag)
            culprit = self._watchdog.disarm() if self._watchdog is not None else None
            if culprit is not None and lag >= LAG_CULPRIT_MS:
                self.telemetry.count("lag", culprit, guild_id=None, ms=lag)

    @tasks.loop(seconds=FLUSH_SECONDS)
    async def flush_loop(self) -> None:
        """Cada minuto: minutos de voz, muestra del sistema y volcado a SQLite."""
        self._count_voice()
        await self._sample_system()
        await self.telemetry.flush()

    @flush_loop.before_loop
    async def _before_flush(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def prune_loop(self) -> None:
        """Una vez al día: pliega las horas viejas y borra lo que pasa de plazo."""
        await self.telemetry.prune()

    @prune_loop.before_loop
    async def _before_prune(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def export_loop(self) -> None:
        """Al arrancar y una vez al día: deja el informe en `REPORTS_DIR`.

        Es el mismo de `informe` (`DAILY_REPORT_DAYS` días), para que un agente lo
        lea desde la carpeta compartida del NAS sin pedirlo. Sin la carpeta (fuera
        de Docker o sin montarla) no hace nada.
        """
        if not await asyncio.to_thread(REPORTS_DIR.is_dir):
            return
        guilds = self.bot.guilds
        for guild in guilds:
            names, channel_names = report_inputs(self.bot, guild)
            report = await self.telemetry.report(
                guild_id=guild.id,
                days=DAILY_REPORT_DAYS,
                commands=names,
                channel_names=channel_names,
            )
            prefix = "informe" if len(guilds) == 1 else f"informe-{guild.id}"
            data = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
            try:
                await asyncio.to_thread(
                    save_report, REPORTS_DIR, f"{prefix}-{report['hasta']}.json", data
                )
            except OSError:
                logger.warning("No se pudo guardar el informe diario", exc_info=True)

    @export_loop.before_loop
    async def _before_export(self) -> None:
        await self.bot.wait_until_ready()

    def _count_voice(self) -> None:
        """Un minuto de voz por persona conectada (sin bots), leído de la caché."""
        for guild in self.bot.guilds:
            for channel in (*guild.voice_channels, *guild.stage_channels):
                for member in channel.members:
                    if not member.bot:
                        self.telemetry.count(
                            "voice", str(channel.id), guild_id=guild.id, user_id=member.id
                        )

    async def _sample_system(self) -> None:
        memory_mb, rss_mb, chromium, node = await asyncio.to_thread(read_system_usage)
        now = time.time()
        latency = self.bot.latency
        self.telemetry.sample(
            SystemSample(
                at=now,
                day=local_moment(now)[0],
                memory_mb=memory_mb,
                rss_mb=rss_mb,
                loop_lag_ms=self._max_lag_ms,
                gateway_ms=latency * 1000 if math.isfinite(latency) else None,
                chromium=chromium,
                node=node,
            )
        )
        self._max_lag_ms = 0.0

    # --- Eventos ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command[Any, ..., Any] | app_commands.ContextMenu,
    ) -> None:
        """Cuenta un comando `/` terminado, con lo que tardó en contestar."""
        self.telemetry.count(
            "slash",
            command.qualified_name,
            guild_id=interaction.guild_id,
            user_id=_user_id(interaction),
            ms=self.telemetry.take_answer(interaction.id),
            error=not interaction.response.is_done(),
        )

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        """Cuenta un comando de texto (`.`) terminado."""
        if ctx.command is None:
            return
        self.telemetry.count(
            "text",
            ctx.command.qualified_name,
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=ctx.author.id,
        )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Cuenta un comando de texto fallido (no los que no existen)."""
        if ctx.command is None or isinstance(error, commands.CommandNotFound):
            return
        self.telemetry.count(
            "text",
            ctx.command.qualified_name,
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=ctx.author.id,
            error=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Cuenta el mensaje por canal y sus clases; nunca guarda el texto."""
        if message.guild is None or message.author.bot:
            return
        guild_id = message.guild.id
        self.telemetry.count(
            "message", str(message.channel.id), guild_id=guild_id, user_id=message.author.id
        )
        for kind in message_types(message):
            self.telemetry.count("message_type", kind, guild_id=guild_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Cuenta una reacción por canal (sin bots)."""
        if payload.guild_id is None or (payload.member is not None and payload.member.bot):
            return
        self.telemetry.count(
            "reaction", str(payload.channel_id), guild_id=payload.guild_id, user_id=payload.user_id
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Cuenta un alta en el servidor."""
        self.telemetry.count("member", "alta", guild_id=member.guild.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Cuenta una baja del servidor."""
        self.telemetry.count("member", "baja", guild_id=member.guild.id)


async def setup(bot: commands.Bot) -> None:
    """Registra la telemetría si el cliente la tiene (`BotClient.telemetry`)."""
    telemetry = _telemetry_of(bot)
    if telemetry is None:
        logger.warning("El cliente no tiene telemetría; no se cuenta nada")
        return
    await bot.add_cog(Telemetry(bot, telemetry))
