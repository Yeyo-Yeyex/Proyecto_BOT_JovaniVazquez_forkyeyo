"""Cog `reinicio` y avisos de novedades: el bot al día con `main`.

Solo pueden usarlo las personas de `DEPLOYERS` (quien mantiene el bot y quien
lo hostea). El bot no se reinicia a sí mismo: deja una nota en el buzón
compartido con el NAS (`bot.services.deploy`) y `actualizar.sh --solicitud`,
lanzado cada minuto por cron, descarga `main`, reconstruye y reinicia. Cuando
el script termina, este cog publica el resultado en el canal donde se pidió,
lo haga el bot viejo (si no hubo reinicio) o el nuevo.

Un reinicio espera a que acabe lo que hay a medias (protocolo en
`bot.services.deploy`), tanto el de `reinicio` como el despliegue de noche:

1. Anunciado (`announced`): el NAS está construyendo la imagen nueva. Todo sigue
   abierto y a quien use un comando se le avisa una vez de que en unos minutos
   habrá reinicio (`RESTART_SOON`).
2. Esperando (`restarting`): la imagen está lista y el NAS pide permiso. No corre
   ningún comando nuevo salvo `reinicio` y `bloquear`, los paneles abiertos siguen
   y la primera vez que alguien pulsa uno se le avisa de que termine. En cuanto no
   queda nada a medias (`in_progress`) se contesta `listo` y el NAS reinicia.
3. Apagándose (`closing`, lo pone `BotClient.close`, también en un reinicio a mano
   o por falta de memoria): tampoco se atienden botones, las partidas se cierran.

Si el NAS contesta sin reiniciar (no pudo compilar) o las notas caducan, se vuelve
a abrir. Los controles de los comandos y los botones están en `bot.cogs.admin`,
junto a los de `bloquear`.

A medias cuenta: un turno en marcha de los cogs que lo apuntan en `working`
(`pala`, el negocio, `banco`, `pisos`, el Rastro) y cualquier vista (panel, mesa,
partida) que alguien haya tocado o el bot haya cambiado en los últimos
`IDLE_SECONDS`. Una vista abandonada no hace esperar: caduca o se cierra al
apagar, como siempre.

Novedades: cada vez que el NAS despliega commits nuevos (de noche o por
`reinicio`), deja en el buzón la lista de lo que entró en `main`: PR y
commits subidos sin PR. Este cog pide a GitHub la
descripción de cada uno (`bot.services.changelog`) y publica el aviso en cada
servidor: la versión que se ha desplegado (`bot.__version__`), la lista de
cambios con su enlace y autor y, en modo detallado, una
ficha por PR con su descripción, como en GitHub. Canal, detalle y si se
publica o no se configuran con `cambios` (cog `Admin`); el canal
puede ser un hilo. El aviso es
informativo: sin botones, logros ni bromas.

`reinicio` no tiene logros: es una utilidad interna de dos personas (excepción
de la Biblia, sección "Logros"). No sale en `ayuda`.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui.view import BaseView, ViewStore

from bot import __version__
from bot.cogs.errors import RESTART_SOON
from bot.repositories.news import NewsSettings
from bot.services.changelog import (
    Fetch,
    NewsEntry,
    NewsItem,
    fetch_pulls,
    github_fetcher,
    parse_news,
)
from bot.services.deploy import DeployRequest, DeployResult, Mailbox
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

#: Quién puede pedir el reinicio: Yeyo y Dani (el que hostea el bot en su NAS).
DEPLOYERS: frozenset[int] = frozenset({403646452414545921, 498473711687434241})

#: Cada cuánto se mira si el NAS ha dejado el resultado, novedades o una petición.
POLL_SECONDS = 15
#: Una vista que nadie toca ni cambia en este tiempo ya no hace esperar al reinicio.
IDLE_SECONDS = 120
#: Comandos que no avisan del reinicio (son los que lo manejan).
QUIET_COMMANDS = frozenset({"reinicio", "bloquear"})
#: Atributo con el último momento (`time.monotonic`) en que se tocó o cambió una vista.
SEEN_ATTRIBUTE = "_restart_seen"
#: Marca de los métodos de discord.py ya envueltos por `install_activity`.
_ACTIVITY_WRAPPED = "_restart_activity_wrapped"


def _seen(original: Callable[..., Any]) -> Callable[..., Any]:
    """Apunta en la vista que acaba de estar activa (`SEEN_ATTRIBUTE`)."""

    @functools.wraps(original)
    def add_view(self: ViewStore, view: BaseView, *args: Any, **kwargs: Any) -> Any:
        setattr(view, SEEN_ATTRIBUTE, time.monotonic())
        return original(self, view, *args, **kwargs)

    return add_view


def _clicked(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def run(self: BaseView, *args: Any) -> Any:
        setattr(self, SEEN_ATTRIBUTE, time.monotonic())
        return await original(self, *args)

    return run


def install_activity() -> None:
    """Apunta cuándo se usó por última vez cada vista, una sola vez por proceso.

    Una vista está activa al guardarla discord.py (al enviarla o editar su mensaje:
    `ViewStore.add_view`) y al pulsarla (`BaseView._scheduled_task`, también los
    botones del minijuego de `pala` y del negocio, que la vista rechaza y atiende
    su listener). Son internos de discord.py: si cambian, falla
    `tests/integration/test_bloquear.py`.
    """
    for owner, name, wrapper in (
        (ViewStore, "add_view", _seen),
        (BaseView, "_scheduled_task", _clicked),
    ):
        original = owner.__dict__[name]
        if not getattr(original, _ACTIVITY_WRAPPED, False):
            wrapped = wrapper(original)
            setattr(wrapped, _ACTIVITY_WRAPPED, True)
            setattr(owner, name, wrapped)


def live_views(bot: object) -> list[BaseView]:
    """Las vistas que atiende ahora discord.py (sin las terminadas)."""
    store = getattr(getattr(bot, "_connection", None), "_view_store", None)
    if not isinstance(store, ViewStore):
        return []
    views: dict[int, BaseView] = {id(v): v for v in store._synced_message_views.values()}
    for items in store._views.values():
        for item in items.values():
            if item.view is not None:
                views[id(item.view)] = item.view
    return [view for view in views.values() if not view.is_finished()]


def in_progress(bot: object, *, idle: float = IDLE_SECONDS) -> int:
    """Cuántas cosas hay a medias que un reinicio debe esperar (ver el módulo)."""
    now = time.monotonic()
    busy = 0
    for cog in getattr(bot, "cogs", {}).values():
        working = getattr(cog, "working", None)
        if isinstance(working, set):
            busy += len(working)
    for view in live_views(bot):
        if now - getattr(view, SEEN_ATTRIBUTE, float("-inf")) < idle:
            busy += 1
    return busy


NEWS_CHANNEL_NAME = "chat-general"
NEWS_COLOR = discord.Color.from_rgb(200, 160, 60)
NEWS_TITLE = "📜 Novedades del bot"
#: Largo máximo de cada línea de la lista, para que quepa en un embed.
NEWS_LINE_CHARS = 160
#: Largo máximo de la descripción de un embed; lo que no cabe sigue en otro.
DESCRIPTION_CHARS = 4_096
#: Límites de Discord por mensaje: 10 embeds y 6.000 caracteres entre todos.
EMBEDS_PER_MESSAGE = 10
CHARS_PER_MESSAGE = 6_000


def result_message(result: DeployResult) -> str:
    """Texto público con el resultado del reinicio."""
    who = f"<@{result.request.user_id}> " if result.request else ""
    summary = result.summary or "sin detalles; mira `.despliegue/actualizar.log` en el NAS"
    if result.ok:
        return f"✅ {who}Ya estoy de vuelta, actualizaíto y con flow. {summary}"
    return f"❌ {who}El reinicio no ha salido, sigo con la versión de antes. {summary}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summary_line(entry: NewsEntry) -> str:
    """Una línea de la lista, como «What's Changed» de GitHub: título, PR y autor.

    Un commit subido a `main` sin PR lleva el enlace al commit y su autor.
    """
    item = entry.item
    text = f"• {_clip(item.headline, NEWS_LINE_CHARS)}"
    if item.sha is not None:
        text += f" · [{item.sha[:7]}]({item.commit_url})"
        if item.author:
            text += f" · {item.author}"
        return text
    pull = entry.pull
    if pull is None:
        return text
    text += f" · [#{pull.number}]({pull.url})"
    if pull.author:
        text += f" · {pull.author}"
    return text


def _split(text: str, limit: int = DESCRIPTION_CHARS) -> list[str]:
    """Parte un texto en trozos de `limit` caracteres como mucho, por saltos de línea."""
    chunks: list[str] = []
    for line in text.split("\n"):
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if chunks and len(chunks[-1]) + 1 + len(line) <= limit:
            chunks[-1] += "\n" + line
        else:
            chunks.append(line)
    # Un trozo que solo sea un salto de línea daría un embed vacío, que Discord rechaza.
    return [chunk for chunk in chunks if chunk.strip()] or [text]


def news_summary(entries: list[NewsEntry]) -> list[discord.Embed]:
    """Primeros embeds del aviso: la lista entera de cambios desplegados."""
    header = f"Cambios incluidos en la versión {__version__}, que acaba de desplegarse:"
    text = "\n".join([header, "", *(_summary_line(entry) for entry in entries)])
    embeds = [discord.Embed(description=chunk, color=NEWS_COLOR) for chunk in _split(text)]
    embeds[0].title = f"{NEWS_TITLE} · v{__version__}"
    return embeds


def news_detail(entry: NewsEntry) -> list[discord.Embed]:
    """Ficha de un PR con su descripción entera, en varios embeds si no cabe en uno.

    Vacía si GitHub no lo encontró.
    """
    pull = entry.pull
    if pull is None:
        return []
    if pull.body:
        description = pull.body
    elif entry.item.commits:
        description = "\n".join(f"• {commit}" for commit in entry.item.commits)
    else:
        description = "Sin descripción."
    embeds = [discord.Embed(description=chunk, color=NEWS_COLOR) for chunk in _split(description)]
    first, last = embeds[0], embeds[-1]
    first.title = _clip(entry.item.headline, 256)
    first.url = pull.url
    if pull.author:
        first.set_author(name=pull.author, url=pull.author_url, icon_url=pull.author_avatar)
    last.timestamp = pull.merged_at
    last.set_footer(text=f"{pull.repo} · PR #{pull.number}")
    return embeds


def news_messages(entries: list[NewsEntry], *, detailed: bool) -> list[list[discord.Embed]]:
    """Embeds del aviso repartidos en mensajes que caben en los límites de Discord."""
    embeds = news_summary(entries)
    if detailed:
        embeds += [embed for entry in entries for embed in news_detail(entry)]
    messages: list[list[discord.Embed]] = []
    size = 0
    for embed in embeds:
        if (
            not messages
            or len(messages[-1]) == EMBEDS_PER_MESSAGE
            or (size + len(embed) > CHARS_PER_MESSAGE)
        ):
            messages.append([])
            size = 0
        messages[-1].append(embed)
        size += len(embed)
    return messages


#: Dónde se puede publicar el aviso: un canal de texto o un hilo (también un post de foro).
NewsTarget = discord.TextChannel | discord.Thread


async def resolve_target(guild: discord.Guild, channel_id: int) -> NewsTarget | None:
    """El canal de texto o hilo con ese ID, aunque el hilo esté archivado.

    Discord no guarda los hilos archivados en la caché del bot: si no está,
    se pide a la API. `None` si no existe, el bot no lo ve o no es un sitio
    donde se pueda publicar (un canal de voz, un foro entero).
    """
    channel = guild.get_channel_or_thread(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
    return channel if isinstance(channel, NewsTarget) else None


async def news_channel(guild: discord.Guild, settings: NewsSettings) -> NewsTarget | None:
    """Canal del aviso: el elegido con `cambios`, si no `#chat-general` o el del sistema.

    Si el elegido es un hilo archivado, al escribir en él Discord lo desarchiva
    solo. Si se ha borrado o el bot ya no lo ve, se vuelve al canal de serie.
    """
    if settings.channel_id is not None:
        channel = await resolve_target(guild, settings.channel_id)
        if channel is not None:
            return channel
    return discord.utils.get(guild.text_channels, name=NEWS_CHANNEL_NAME) or guild.system_channel


def can_post(channel: NewsTarget, me: discord.Member) -> bool:
    """Si el bot puede escribir ahí: en un hilo cuenta el permiso de hilos, no el de mensajes."""
    permissions = channel.permissions_for(me)
    if isinstance(channel, discord.Thread):
        if channel.locked and not permissions.manage_threads:
            return False
        return permissions.send_messages_in_threads
    return permissions.send_messages


class OldNewsButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"novedades:leido:(?P<edition>\d+)",
):
    """Botón 📜 Leído que llevaban los avisos de novedades antiguos.

    Los avisos nuevos no tienen botón, pero los ya publicados siguen en los
    canales; sin esto, pulsarlos daría «Esta interacción ha fallado».
    """

    def __init__(self, edition: int) -> None:
        super().__init__(
            discord.ui.Button(label="Leído", emoji="📜", custom_id=f"novedades:leido:{edition}")
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> OldNewsButton:
        return cls(int(match["edition"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        # Solo memoria: se contesta directamente.
        await interaction.response.send_message(
            "Este botón ya no hace nada. Las novedades se publican sin él.", ephemeral=True
        )


class Deploy(commands.Cog, name="Despliegue"):
    """Comando `reinicio`, aviso de su resultado y avisos de novedades."""

    def __init__(
        self,
        bot: commands.Bot,
        mailbox: Mailbox | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        fetch: Fetch | None = None,
    ) -> None:
        self.bot = bot
        self.mailbox = mailbox or Mailbox()
        self._clock = clock or (lambda: datetime.now(UTC))
        #: Cómo se piden los PR; sin él, a la API de GitHub (las pruebas lo cambian).
        self._fetch = fetch
        #: El NAS va a desplegar: se avisa a quien use un comando, pero todo sigue.
        self.announced = False
        #: El NAS espera a que acabe lo que hay a medias: no se abren comandos nuevos.
        self.restarting = False
        #: El bot ya se está apagando: tampoco se atienden botones.
        self.closing = False
        #: Quién ha visto ya el aviso del reinicio al pulsar un botón.
        self.warned: set[int] = set()
        #: Quién ha visto ya el aviso del reinicio al usar un comando.
        self.told: set[int] = set()

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(OldNewsButton)
        install_activity()
        self.mailbox.mark_can_wait()
        self._poll.start()

    async def cog_unload(self) -> None:
        self._poll.cancel()

    async def request_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida entre `/reinicio` y `.reinicio`."""
        member = responder.member
        if member is None or member.id not in DEPLOYERS:
            await responder.send_error("🚫 Este botón rojo no es pa' ti, mi pana.")
            return
        channel_id = getattr(responder.channel, "id", None)
        if channel_id is None:
            await responder.send_error("No sé en qué canal avisarte; pídelo desde un canal.")
            return

        now = self._clock()
        pending = self.mailbox.pending(now)
        if pending is not None:
            minutes = int((now - pending.requested_at).total_seconds() // 60)
            await responder.send_error(
                f"⏳ Ya hay un reinicio pedido por <@{pending.user_id}> hace {minutes} min. "
                "Si en un par de minutos no pasa nada, el cron del NAS no está puesto "
                "(README, «Actualización automática»)."
            )
            return

        try:
            self.mailbox.request(DeployRequest(channel_id, member.id, now))
        except OSError:
            logger.exception("No se pudo dejar la petición de reinicio en el buzón")
            await responder.send_error(
                "No puedo escribir en el buzón del NAS (`.despliegue/buzon`). "
                "¿Se arrancó el bot con `actualizar.sh`? Mira el README."
            )
            return
        logger.info("Reinicio pedido por %s", member.id)
        self.announced = True
        await responder.send(
            "🔁 Pedido. En menos de un minuto el NAS baja lo último de GitHub y construye "
            "la versión nueva; mientras, todo sigue abierto y aviso a quien use un comando. "
            "Cuando esté lista no se abren partidas nuevas, espero a que acaben las que "
            "haya a medias (como mucho 15 minutos) y me reinicio. Aviso aquí cuando acabe.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="reinicio", description="Reinicia el bot con lo último de GitHub.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def request_slash(self, interaction: discord.Interaction) -> None:
        """Pide al NAS que actualice y reinicie el bot.

        Solo para los IDs de `DEPLOYERS`, comprobado en cada uso; que Discord
        oculte el `/` a quien no es administrador es solo estética. Efecto
        visible: el bot se reinicia unos segundos y luego publica el resultado
        en este canal.
        """
        await self.request_impl(InteractionResponder(interaction))

    @commands.command(name="reinicio", aliases=["met"], hidden=True)
    @commands.guild_only()
    async def request_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.reinicio` o `.met`) de `/reinicio`."""
        await self.request_impl(ContextResponder(ctx))

    async def announce(self) -> bool:
        """Publica el resultado del NAS si ya lo hay. Devuelve si había."""
        result = self.mailbox.take_result()
        if result is None:
            return False
        logger.info("Resultado del reinicio: ok=%s %s", result.ok, result.summary)
        if result.request is None:
            return True
        channel = self.bot.get_channel(result.request.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            logger.warning("No encuentro el canal %s para el aviso", result.request.channel_id)
            return True
        try:
            await channel.send(
                result_message(result),
                allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
            )
        except discord.HTTPException:
            logger.exception("No se pudo publicar el resultado del reinicio")
        return True

    async def announce_news(self) -> bool:
        """Publica en cada servidor las novedades que haya dejado el NAS.

        GitHub se consulta una vez por aviso, no por servidor.

        Returns:
            Si había novedades (aunque no se hayan podido publicar en todos).
        """
        lines = self.mailbox.take_news()
        if lines is None:
            return False
        items = parse_news(lines)
        logger.info("Novedades desplegadas: %s", [item.headline for item in items])
        entries = await self._with_pulls(items)
        for guild in self.bot.guilds:
            settings = await self._settings(guild.id)
            channel = await news_channel(guild, settings) if settings.enabled else None
            if channel is None:
                continue
            try:
                for embeds in news_messages(entries, detailed=settings.detailed):
                    await channel.send(embeds=embeds)
            except discord.HTTPException:
                logger.exception("No se pudieron publicar las novedades en %s", guild.id)
        return True

    async def _with_pulls(self, items: list[NewsItem]) -> list[NewsEntry]:
        if self._fetch is not None:
            return await fetch_pulls(items, self._fetch)
        async with aiohttp.ClientSession() as session:
            return await fetch_pulls(items, github_fetcher(session))

    async def _settings(self, guild_id: int) -> NewsSettings:
        repository = getattr(self.bot, "news", None)
        if repository is None:
            return NewsSettings()
        try:
            return await repository.settings(guild_id)
        except Exception:
            # Mejor el aviso con los ajustes de serie que ninguno.
            logger.exception("No se pudieron leer los ajustes de novedades de %s", guild_id)
            return NewsSettings()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los ajustes del aviso del servidor que el bot abandona."""
        repository = getattr(self.bot, "news", None)
        if repository is not None:
            await repository.delete_guild_data(guild.id)

    def in_progress(self) -> int:
        """Cuántas cosas hay a medias que el reinicio debe esperar."""
        return in_progress(self.bot)

    def _phase(self) -> None:
        """Pone `announced` y `restarting` según las notas del NAS."""
        now = self._clock()
        preparing = self.mailbox.preparing(now) is not None
        announced = (
            preparing
            or self.mailbox.pending(now) is not None
            or self.mailbox.notice(now) is not None
        )
        if preparing and not self.restarting:
            logger.info("El NAS pide reiniciar: espero a %d cosas a medias", self.in_progress())
        if not announced:
            # El NAS contestó sin reiniciarme (no pudo compilar) o las notas caducaron.
            self.told.clear()
            self.warned.clear()
        self.announced, self.restarting = announced, preparing

    async def _answer_nas(self) -> None:
        """Con el NAS esperando y nada a medias, le contesta que ya puede reiniciar."""
        if not self.restarting or self.mailbox.is_ready():
            return
        busy = self.in_progress()
        if busy:
            logger.debug("Reinicio en espera: %d cosas a medias", busy)
            return
        try:
            self.mailbox.ready(self._clock())
        except OSError:
            logger.exception("No pude contestar al NAS que ya puede reiniciar")
            return
        logger.info("Nada a medias: el NAS ya puede reiniciar")

    @commands.Cog.listener()
    async def on_app_command_completion(
        self, interaction: discord.Interaction, command: app_commands.Command[Any, ..., Any]
    ) -> None:
        """Tras un `/`, avisa una vez a cada uno de que viene un reinicio."""
        if not self._should_tell(interaction.user.id, command.qualified_name):
            return
        if not interaction.response.is_done():
            return
        self.told.add(interaction.user.id)
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(RESTART_SOON, ephemeral=True)

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        """Tras un comando `.`, avisa una vez a cada uno de que viene un reinicio."""
        if ctx.command is None or not self._should_tell(ctx.author.id, ctx.command.qualified_name):
            return
        self.told.add(ctx.author.id)
        with contextlib.suppress(discord.HTTPException):
            await ctx.send(RESTART_SOON, delete_after=120)

    def _should_tell(self, user_id: int, command: str) -> bool:
        return (
            self.announced
            and not self.restarting
            and not self.closing
            and command not in QUIET_COMMANDS
            and user_id not in self.told
        )

    @tasks.loop(seconds=POLL_SECONDS)
    async def _poll(self) -> None:
        await self.announce()
        await self.announce_news()
        self._phase()
        await self._answer_nas()

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(Deploy(bot))
