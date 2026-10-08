"""Cog `reinicio` y avisos de novedades: el bot al día con `main`.

Solo pueden usarlo las personas de `DEPLOYERS` (quien mantiene el bot y quien
lo hostea). El bot no se reinicia a sí mismo: deja una nota en el buzón
compartido con el NAS (`bot.services.deploy`) y `actualizar.sh --solicitud`,
lanzado cada minuto por cron, descarga `main`, reconstruye y reinicia. Cuando
el script termina, este cog publica el resultado en el canal donde se pidió,
lo haga el bot viejo (si no hubo reinicio) o el nuevo.

Novedades: cada vez que el NAS despliega commits nuevos (de noche o por
`reinicio`), deja la lista de PR en el buzón y este cog la publica en
`#chat-general` (o en el canal del sistema) con un botón 📜 Leído. Quien lo
pulsa suma logros (❤️ Social): leerse unas novedades y ser el primero en
hacerlo. Solo cuenta el aviso más reciente publicado desde el último arranque;
así nadie cobra dos veces el mismo aviso tras un reinicio.

`reinicio` no tiene logros: es una utilidad interna de dos personas (excepción
de la Biblia, sección "Logros"). No sale en `ayuda`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import pets as mascotas
from bot.cogs import renta
from bot.services.achievements import StatDelta
from bot.services.deploy import DeployRequest, DeployResult, Mailbox, news_lines
from bot.services.pets import Event, Moment
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

#: Quién puede pedir el reinicio: Yeyo y Dani (el que hostea el bot en su NAS).
DEPLOYERS: frozenset[int] = frozenset({403646452414545921, 498473711687434241})

#: Cada cuánto se mira si el NAS ha dejado el resultado o novedades.
POLL_SECONDS = 15

NEWS_CHANNEL_NAME = "chat-general"
NEWS_COLOR = discord.Color.from_rgb(200, 160, 60)
#: Estadísticas de logros del botón 📜 Leído.
NEWS_READ_STAT = "news_read"
NEWS_FIRST_STAT = "news_first"


def result_message(result: DeployResult) -> str:
    """Texto público con el resultado del reinicio."""
    who = f"<@{result.request.user_id}> " if result.request else ""
    summary = result.summary or "sin detalles; mira `.despliegue/actualizar.log` en el NAS"
    if result.ok:
        return f"✅ {who}Ya estoy de vuelta, actualizaíto y con flow. {summary}"
    return f"❌ {who}El reinicio no ha salido, sigo con la versión de antes. {summary}"


def news_embed(items: list[str]) -> discord.Embed:
    """Aviso público con los PR que trae la versión recién desplegada."""
    return discord.Embed(
        title="📜 Novedades del bot",
        description="\n".join(
            [
                "Recién salido del horno (y del Consejo de Ministros):",
                "",
                *news_lines(items),
            ]
        ),
        color=NEWS_COLOR,
    ).set_footer(text="Pulsa 📜 Leído si te lo has leído entero. Hacienda toma nota.")


class NewsReadButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"novedades:leido:(?P<edition>\d+)",
):
    """Botón 📜 Leído del aviso de novedades.

    Es un `DynamicItem` para que los avisos viejos sigan respondiendo tras un
    reinicio (con un "ya está derogado") en vez de dar "interacción fallida".
    `edition` es la marca de tiempo del aviso: solo cuenta el último.
    """

    def __init__(self, edition: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Leído",
                emoji="📜",
                style=discord.ButtonStyle.secondary,
                custom_id=f"novedades:leido:{edition}",
            )
        )
        self.edition = edition

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> NewsReadButton:
        return cls(int(match["edition"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Deploy)  # type: ignore[arg-type]
        if cog is not None:
            await cog.read_news(interaction, self.edition)


class Deploy(commands.Cog, name="Despliegue"):
    """Comando `reinicio`, aviso de su resultado y avisos de novedades."""

    def __init__(
        self,
        bot: commands.Bot,
        mailbox: Mailbox | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.bot = bot
        self.mailbox = mailbox or Mailbox()
        self._clock = clock or (lambda: datetime.now(UTC))
        #: Último aviso de novedades por servidor: su edición y quién lo ha leído.
        #: En memoria: tras un reinicio los avisos anteriores ya no cuentan.
        self._news: dict[int, tuple[int, set[int]]] = {}

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(NewsReadButton)
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
        await responder.send(
            "🔁 Pedido. En menos de un minuto el NAS baja lo último de GitHub, "
            "reconstruye y me reinicia (unos minutos). Aviso aquí cuando acabe.",
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

    @commands.command(name="reinicio", hidden=True)
    @commands.guild_only()
    async def request_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.reinicio`) de `/reinicio`."""
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

        Returns:
            Si había novedades (aunque no se hayan podido publicar en todos).
        """
        items = self.mailbox.take_news()
        if items is None:
            return False
        logger.info("Novedades desplegadas: %s", items)
        edition = int(self._clock().timestamp())
        for guild in self.bot.guilds:
            channel = (
                discord.utils.get(guild.text_channels, name=NEWS_CHANNEL_NAME)
                or guild.system_channel
            )
            if channel is None:
                continue
            view = discord.ui.View(timeout=None)
            view.add_item(NewsReadButton(edition))
            try:
                await channel.send(embed=news_embed(items), view=view)
            except discord.HTTPException:
                logger.exception("No se pudieron publicar las novedades en %s", guild.id)
                continue
            self._news[guild.id] = (edition, set())
        return True

    async def read_news(self, interaction: discord.Interaction, edition: int) -> None:
        """Respuesta (solo visible para quien pulsa) al botón 📜 Leído.

        Cuenta para los logros una vez por persona y aviso, y solo en el aviso
        más reciente desde el último arranque.
        """
        guild = interaction.guild
        if guild is None:
            return
        # La mascota puede aparecer y eso se guarda: se acepta el clic antes.
        await ack(interaction, new_message=True)
        current = self._news.get(guild.id)
        if current is None or current[0] != edition:
            await edit(
                interaction, content="📜 Estas novedades ya están derogadas. Busca las últimas."
            )
            return
        readers = current[1]
        user = interaction.user
        if user.id in readers:
            await edit(interaction, content="Ya te lo habías leído. Ni el BOE se lee dos veces.")
            return
        first = not readers
        readers.add(user.id)
        text = (
            "🥇 Primero en leérselo. Ni la UCO se entera tan rápido."
            if first
            else "📜 Leído y conforme. Queda constancia en el registro."
        )
        if pet := await mascotas.cameo(self.bot, guild.id, user.id, Moment(Event.NEWS)):
            text = f"{text}\n{pet}"
        await edit(interaction, content=text)
        delta = StatDelta(add={NEWS_READ_STAT: 1})
        if first:
            delta.add[NEWS_FIRST_STAT] = 1
        await logros.track(self.bot, guild.id, user, interaction.channel, delta)
        # Los logros se pagan: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)

    @tasks.loop(seconds=POLL_SECONDS)
    async def _poll(self) -> None:
        await self.announce()
        await self.announce_news()

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(Deploy(bot))
