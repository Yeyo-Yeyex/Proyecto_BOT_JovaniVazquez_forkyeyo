"""La Activity de Discord: el casino de Jovani en pantalla grande, dentro de Discord.

Una Activity es una página web que Discord abre en un recuadro dentro de un canal
(de voz o de texto) o de un mensaje directo, desde el comando de entrada `/jovani`
(ver `BotClient.sync_commands`). Este cog la sirve desde el propio bot con el
servidor web de `aiohttp`, que ya trae discord.py: juega con la misma economía, los
mismos candados y la misma base de datos que los comandos, sin otro servicio.

De momento trae cara o cruz. La mesa es la de `moneda` (`Coin.web_table`): dinero,
impuestos, textos de Jovani, logros, `apuestas` y anuncios salen del mismo código.
Lo único distinto es la animación: la pinta el navegador del jugador con la misma
escena (`assets/moneda/escena.html`) y los fotogramas que manda Python, en vez de
un GIF dibujado en el NAS. Las partidas de la Activity cuentan además para sus
propios logros (`coin_activity_games`).

Cómo llega cada petición:

1. Discord sirve la página a través de su proxy (`<id de la app>.discordsays.com`),
   que la pide a la dirección pública puesta en el portal (URL Mappings, `/` →
   un túnel hasta el puerto `PORT` del NAS).
2. La página (`assets/actividad/`) pide a Discord un código de autorización y lo
   manda a `POST /api/token` con el `instance_id` de la Activity.
3. El bot cambia el código por un token con el secreto de la app
   (`DISCORD_CLIENT_SECRET`), pregunta a Discord quién es (`/users/@me`) y, con la
   API de instancias, que esa persona está de verdad dentro de esa Activity y
   dónde (servidor y canal). Lo que diga la página nunca vale como prueba. La
   sesión se guarda en memoria, con el token como llave, `SESSION_HOURS` horas.
4. Cada jugada (`/api/moneda`) llega con ese token. Sin sesión (tras un
   reinicio), 401: la página vuelve a pedir permiso sola.

El dinero es el del servidor donde se abre si el bot está en él; si no (un mensaje
directo o un servidor pequeño sin el bot, que es donde se puede abrir una Activity
sin verificar), el de `ACTIVITY_GUILD_ID`. En los dos casos hay que ser miembro.

Sin `DISCORD_CLIENT_SECRET` el cog no abre nada.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands

from bot.cogs.coin import Coin, CoinView, Finish
from bot.services.coin import Side
from bot.services.coin_render import toss_seconds
from bot.utils.cogs import find_cog

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Puerto del servidor web dentro del contenedor (el túnel de `docker-compose.yml` lo publica).
PORT = 8765
DISCORD_API = "https://discord.com/api/v10"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
PAGE = ASSETS / "actividad" / "index.html"
#: Horas que vale una sesión; luego la página vuelve a pedir permiso (sin que se note).
SESSION_HOURS = 12
#: Las peticiones de la página son JSON pequeños: lo que pase de aquí no es de la página.
MAX_BODY = 4096
#: Margen tras la animación antes de los logros y anuncios: que no destripen el final.
FINISH_MARGIN_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class Session:
    """Quién juega, con el dinero de qué servidor y desde qué canal (`None`: sin canal)."""

    user_id: int
    guild_id: int
    channel_id: int | None
    created: float


class ActivityError(Exception):
    """Un fallo que se le explica al jugador, con su código HTTP."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@web.middleware
async def errors(request: web.Request, handler: Any) -> web.StreamResponse:
    """Los fallos esperados, en JSON para la página; los inesperados, al log y genéricos."""
    try:
        return await handler(request)
    except ActivityError as error:
        return web.json_response({"error": error.message}, status=error.status)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Fallo en la Activity (%s %s)", request.method, request.path)
        return web.json_response({"error": "Algo ha fallado en el bot."}, status=500)


class Actividad(commands.Cog, name="Actividad"):
    """El servidor web de la Activity: página, permisos y jugadas (ver el módulo)."""

    def __init__(
        self, bot: commands.Bot, *, secret: str, guild_id: int | None, port: int = PORT
    ) -> None:
        self.bot = bot
        self.secret = secret
        self.guild_id = guild_id
        self.port = port
        self.sessions: dict[str, Session] = {}
        self.client: aiohttp.ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._page = ""
        # Logros y anuncios que esperan a que acabe la animación.
        self._finishing: set[asyncio.Task[None]] = set()

    async def cog_load(self) -> None:
        """Abre el servidor web si hay secreto. Si el puerto está cogido, el bot sigue sin él."""
        if not self.secret:
            return
        self._page = await asyncio.to_thread(PAGE.read_text, encoding="utf-8")
        self.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        self._runner = web.AppRunner(self.build_app(), access_log=None)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        except OSError:
            logger.exception("La Activity no puede abrir el puerto %d: sigue sin ella", self.port)
            await self._runner.cleanup()
            self._runner = None
            return
        logger.info("Activity escuchando en el puerto %d", self.port)

    async def cog_unload(self) -> None:
        """Termina lo pendiente (logros de las últimas tiradas) y cierra el servidor."""
        if self._finishing:
            await asyncio.gather(*self._finishing, return_exceptions=True)
        if self._runner is not None:
            await self._runner.cleanup()
        if self.client is not None:
            await self.client.close()

    def build_app(self) -> web.Application:
        """Las rutas: la página, sus archivos (`assets/`) y la API."""
        app = web.Application(client_max_size=MAX_BODY, middlewares=[errors])
        app.router.add_get("/", self.page)
        app.router.add_post("/api/token", self.token)
        app.router.add_get("/api/moneda", self.coin_state)
        app.router.add_post("/api/moneda", self.coin_action)
        # Todo `assets/` es público (está en el repositorio): escenas, fuentes y tapetes.
        app.router.add_static("/assets", ASSETS)
        return app

    # -- Página y permisos -------------------------------------------------------------

    async def page(self, request: web.Request) -> web.Response:
        """La página, con el ID de la app que necesita el SDK de Discord."""
        body = self._page.replace("%CLIENT_ID%", str(self.bot.application_id))
        return web.Response(text=body, content_type="text/html")

    async def token(self, request: web.Request) -> web.Response:
        """Cambia el código de Discord por un token y abre la sesión del jugador.

        Raises:
            ActivityError: 400 si la petición no es de la página; 401 si Discord no
                acepta el código; 403 si no está en esa Activity o no es del servidor.
        """
        body = await _json(request)
        code, instance_id = body.get("code"), body.get("instance_id")
        if not _short_text(code) or not _short_text(instance_id):
            raise ActivityError(400, "Petición mal hecha.")
        access_token = await self._exchange(code)
        user_id = await self._whoami(access_token)
        instance = await self._instance(instance_id)
        if str(user_id) not in instance.get("users", []):
            raise ActivityError(403, "No estás dentro de esta Activity.")
        location = instance.get("location") or {}
        guild = self._economy_guild(location.get("guild_id"))
        if guild.get_member(user_id) is None:
            raise ActivityError(403, f"Para jugar tienes que ser de {guild.name}.")
        channel = self.bot.get_channel(int(location.get("channel_id") or 0))
        channel_id = channel.id if getattr(channel, "guild", None) == guild else None
        now = time.time()
        # Las sesiones viejas se van al abrir una nueva: el diccionario no crece sin fin.
        self.sessions = {
            key: session
            for key, session in self.sessions.items()
            if now - session.created < SESSION_HOURS * 3600
        }
        self.sessions[access_token] = Session(user_id, guild.id, channel_id, now)
        return web.json_response({"access_token": access_token})

    async def _exchange(self, code: str) -> str:
        """El token de acceso a cambio del código (OAuth2 con el secreto de la app)."""
        assert self.client is not None
        data = {
            "client_id": str(self.bot.application_id),
            "client_secret": self.secret,
            "grant_type": "authorization_code",
            "code": code,
        }
        async with self.client.post(f"{DISCORD_API}/oauth2/token", data=data) as response:
            if response.status != 200:
                logger.warning("Discord no aceptó un código de la Activity (%d)", response.status)
                raise ActivityError(401, "Discord no ha dado permiso. Vuelve a abrir la Activity.")
            return str((await response.json())["access_token"])

    async def _whoami(self, access_token: str) -> int:
        """El ID de quien tiene el token, preguntado a Discord."""
        assert self.client is not None
        headers = {"Authorization": f"Bearer {access_token}"}
        async with self.client.get(f"{DISCORD_API}/users/@me", headers=headers) as response:
            if response.status != 200:
                raise ActivityError(401, "Discord no te reconoce. Vuelve a abrir la Activity.")
            return int((await response.json())["id"])

    async def _instance(self, instance_id: str) -> dict[str, Any]:
        """La Activity abierta (quién está y dónde), con el token del bot."""
        route = discord.http.Route(
            "GET",
            "/applications/{application_id}/activity-instances/{instance_id}",
            application_id=self.bot.application_id,
            instance_id=instance_id,
        )
        try:
            return await self.bot.http.request(route)
        except discord.NotFound:
            raise ActivityError(403, "Esta Activity ya no está abierta.") from None

    def _economy_guild(self, guild_id: str | None) -> discord.Guild:
        """El servidor de cuyo dinero se juega: donde se abre o, si no, `ACTIVITY_GUILD_ID`."""
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if guild is None and self.guild_id is not None:
            guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            raise ActivityError(403, "Abre la Activity en el servidor del bot.")
        return guild

    def _player(self, request: web.Request) -> tuple[Session, discord.Member]:
        """La sesión del token de la petición y el miembro que juega.

        Raises:
            ActivityError: 401 sin sesión (la página vuelve a pedir permiso); 403 si
                ya no es del servidor.
        """
        header = request.headers.get("Authorization", "")
        session = self.sessions.get(header.removeprefix("Bearer "))
        if session is None:
            raise ActivityError(401, "Sesión caducada.")
        guild = self.bot.get_guild(session.guild_id)
        member = guild.get_member(session.user_id) if guild is not None else None
        if member is None:
            raise ActivityError(403, "Ya no eres del servidor.")
        return session, member

    # -- Cara o cruz -------------------------------------------------------------------

    async def _table(self, request: web.Request) -> CoinView:
        """La mesa de la moneda de quien llama (`Coin.web_table`)."""
        session, member = self._player(request)
        coin = find_cog(self.bot, Coin)
        if coin is None:
            raise ActivityError(503, "Cara o cruz no está disponible ahora.")
        channel = self.bot.get_channel(session.channel_id) if session.channel_id else None
        return await coin.web_table(session.guild_id, member, channel)

    async def coin_state(self, request: web.Request) -> web.Response:
        """`GET /api/moneda`: la mesa tal como está, al abrir la Activity."""
        view = await self._table(request)
        return web.json_response(await view.web_open())

    async def coin_action(self, request: web.Request) -> web.Response:
        """`POST /api/moneda`: `cara`, `cruz`, `cobrar` o `apuesta` (con `cantidad`).

        Responde con lo que hay que enseñar (`CoinView.web_state`). Lo que va tras el
        final (logros, `apuestas`, anuncio) espera a que acabe la animación.
        """
        started = time.perf_counter()
        view = await self._table(request)
        body = await _json(request)
        action = body.get("accion")
        if action in ("cara", "cruz"):
            result = await view.web_flip(Side.CARA if action == "cara" else Side.CRUZ)
        elif action == "cobrar":
            result = await view.web_cash_out()
        elif action == "apuesta" and _short_text(body.get("cantidad")):
            result = await view.web_restake(body["cantidad"])
        else:
            raise ActivityError(400, "Petición mal hecha.")
        self.bot.telemetry.count(
            "actividad",
            f"moneda:{action}",
            guild_id=view.guild_id,
            user_id=view.owner.id,
            ms=(time.perf_counter() - started) * 1000,
            error=isinstance(result, str),
        )
        if isinstance(result, str):
            raise ActivityError(409, result)
        state, finish = result if isinstance(result, tuple) else (result, None)
        if finish is not None:
            self._finish_later(toss_seconds(state["states"]), finish)
        return web.json_response(state)

    def _finish_later(self, seconds: float, finish: Finish) -> None:
        """Logros, `apuestas` y anuncio de una partida, cuando el jugador ya ha visto el final."""

        async def run() -> None:
            await asyncio.sleep(seconds + FINISH_MARGIN_SECONDS)
            try:
                await finish()
            except Exception:
                logger.exception("No se pudo cerrar una partida de la moneda de la Activity")

        task = asyncio.create_task(run(), name="actividad-final")
        self._finishing.add(task)
        task.add_done_callback(self._finishing.discard)


def _short_text(value: object) -> bool:
    """Si es un texto de la página: no vacío y corto (códigos, IDs, cantidades)."""
    return isinstance(value, str) and 0 < len(value) <= 200


async def _json(request: web.Request) -> dict[str, Any]:
    """El cuerpo JSON de la petición, o 400 si no es un objeto JSON."""
    try:
        body = await request.json()
    except ValueError:
        raise ActivityError(400, "Petición mal hecha.") from None
    if not isinstance(body, dict):
        raise ActivityError(400, "Petición mal hecha.")
    return body


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el secreto y el servidor de `config` (`BotClient`)."""
    await bot.add_cog(Actividad(bot, secret=bot.activity_secret, guild_id=bot.activity_guild_id))
