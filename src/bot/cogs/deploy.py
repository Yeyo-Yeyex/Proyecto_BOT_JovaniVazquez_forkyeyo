"""Cog `reinicio`: reinicia el bot con la última versión de `main`.

Solo pueden usarlo las personas de `DEPLOYERS` (quien mantiene el bot y quien
lo hostea). El bot no se reinicia a sí mismo: deja una nota en el buzón
compartido con el NAS (`bot.services.deploy`) y `actualizar.sh --solicitud`,
lanzado cada minuto por cron, descarga `main`, reconstruye y reinicia. Cuando
el script termina, este cog publica el resultado en el canal donde se pidió,
lo haga el bot viejo (si no hubo reinicio) o el nuevo.

No tiene logros: es una utilidad interna de dos personas (excepción de la
Biblia, sección "Logros"). No sale en `ayuda`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.services.deploy import DeployRequest, DeployResult, Mailbox
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

#: Quién puede pedir el reinicio: Yeyo y Dani (el que hostea el bot en su NAS).
DEPLOYERS: frozenset[int] = frozenset({403646452414545921, 498473711687434241})

#: Cada cuánto se mira si el NAS ha dejado el resultado.
POLL_SECONDS = 15


def result_message(result: DeployResult) -> str:
    """Texto público con el resultado del reinicio."""
    who = f"<@{result.request.user_id}> " if result.request else ""
    summary = result.summary or "sin detalles; mira `.despliegue/actualizar.log` en el NAS"
    if result.ok:
        return f"✅ {who}Ya estoy de vuelta, actualizaíto y con flow. {summary}"
    return f"❌ {who}El reinicio no ha salido, sigo con la versión de antes. {summary}"


class Deploy(commands.Cog, name="Despliegue"):
    """Comando `reinicio` y aviso del resultado."""

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

    async def cog_load(self) -> None:
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

    @tasks.loop(seconds=POLL_SECONDS)
    async def _poll(self) -> None:
        await self.announce()

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(Deploy(bot))
