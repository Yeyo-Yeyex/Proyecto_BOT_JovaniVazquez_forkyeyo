"""Bienvenida y despedida automáticas, con GIF configurable y botón para saludar.

Al entrar alguien, el bot publica en el canal de bienvenida (el configurado
con `bienv`, o `#chat-general` si no hay ninguno) una frase al azar de
`assets/bienvenidas.txt` con el GIF del servidor. Sin GIF configurado se
manda el vídeo de Kratos de siempre. Si quien entra ya había estado, la
frase sale de la sección `[vuelta]` del mismo archivo.

El mensaje lleva el botón **👋 Dar la bienvenida**: cada miembro puede
pulsarlo una vez durante el primer día del recién llegado. El botón cuenta
los saludos y alimenta los logros de Social (`welcomes_given` y, si se
saluda en el primer minuto, `welcomes_fast`). Saludar da un regalo
simbólico a los dos, con retención de IRPF como los de cumpleaños (detalle
y norma en `bot.services.welcome`), y por eso lleva el aviso de la Renta.

La configuración (`bienv`) es un comando de administración y vive en el
cog `Admin`; aquí solo se lee.

Permisos e intents: **Server Members Intent** para recibir entradas y
salidas; en el canal, enviar mensajes, adjuntar archivos e insertar enlaces.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.repositories.welcome import WelcomeRepository, WelcomeSettings
from bot.services.achievements import StatDelta
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    IncomeResult,
    format_amount,
    tax_line,
)
from bot.services.welcome import (
    GREETER_GIFT,
    WELCOMED_GIFT,
    GifError,
    GifKind,
    GreetCheck,
    WelcomePhrases,
    check_greeting,
    classify_gif,
    is_fast_greeting,
    parse_phrases,
    pick_phrase,
    render_phrase,
)

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

CHAT_GENERAL_CHANNEL_NAME = "chat-general"
ASSETS_PATH = Path(__file__).resolve().parent.parent / "assets"
WELCOME_VIDEO_PATH = ASSETS_PATH / "bienvenida.mp4"
WELCOME_PHRASES_PATH = ASSETS_PATH / "bienvenidas.txt"
FAREWELL_INSULTS_PATH = ASSETS_PATH / "despedidas.txt"
COLOR = discord.Color.from_rgb(88, 200, 120)

# Se usa solo si el archivo de frases falta o queda vacío, para que la
# despedida nunca se quede sin texto por un error de edición.
FALLBACK_FAREWELL_INSULTS = ("se ha ido del servidor.",)


def load_farewell_insults(path: Path | None = None) -> tuple[str, ...]:
    """Lee las frases de despedida desde un archivo de texto editable.

    Cada línea no vacía y que no empiece por ``#`` se trata como una frase
    disponible. Se recarga en cada despedida para que los cambios en el
    archivo se apliquen sin reiniciar el bot.

    Args:
        path: Ruta del archivo de frases; por defecto, `FAREWELL_INSULTS_PATH`
            (se resuelve en tiempo de llamada para admitir sustituirla en
            pruebas).

    Returns:
        Tupla de frases disponibles; si el archivo falta o está vacío,
        devuelve `FALLBACK_FAREWELL_INSULTS`.
    """
    path = path if path is not None else FAREWELL_INSULTS_PATH
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.warning("No se pudo leer el archivo de frases de despedida: %s", path)
        return FALLBACK_FAREWELL_INSULTS

    insults = tuple(
        line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
    )
    if not insults:
        logger.warning("El archivo de frases de despedida está vacío: %s", path)
        return FALLBACK_FAREWELL_INSULTS
    return insults


def load_welcome_phrases(path: Path | None = None) -> WelcomePhrases:
    """Lee `bienvenidas.txt`; si falta, devuelve las frases de reserva.

    Se relee en cada entrada, como las despedidas, para poder editarlo en
    caliente. Formato en `bot.services.welcome.parse_phrases`.
    """
    path = path if path is not None else WELCOME_PHRASES_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("No se pudo leer el archivo de frases de bienvenida: %s", path)
        text = ""
    return parse_phrases(text)


def welcome_payload(phrase: str, gif_url: str | None) -> tuple[str, discord.Embed | None, bool]:
    """Contenido, embed y si hace falta el vídeo por defecto.

    Returns:
        `(texto, embed, usar_video)`. Con un GIF directo va en el embed; con
        una página de Tenor/Giphy, como enlace suelto al final del texto para
        que Discord lo despliegue; sin GIF (o con uno que ya no valga), se
        usa el vídeo.
    """
    if gif_url:
        try:
            kind = classify_gif(gif_url)
        except GifError:
            logger.warning("El GIF de bienvenida guardado no es válido: %s", gif_url)
        else:
            if kind is GifKind.DIRECT:
                return phrase, discord.Embed(color=COLOR).set_image(url=gif_url), False
            return f"{phrase}\n{gif_url}", None, False
    return phrase, None, True


class GreetButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"bienv:saludar:(?P<user>\d+):(?P<joined>\d+)",
):
    """Botón 👋 Dar la bienvenida.

    Es un `DynamicItem`: el recién llegado y su hora de entrada van en el
    `custom_id`, así el botón sigue funcionando tras reiniciar el bot y no
    hace falta consultar la base de datos para saber si ha caducado.
    """

    def __init__(self, user_id: int, joined_at: int, count: int = 0) -> None:
        label = "Dar la bienvenida" + (f" · {count}" if count else "")
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="👋",
                style=discord.ButtonStyle.success,
                custom_id=f"bienv:saludar:{user_id}:{joined_at}",
            )
        )
        self.user_id = user_id
        self.joined_at = joined_at

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> GreetButton:
        return cls(int(match["user"]), int(match["joined"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Welcome")  # type: ignore[attr-defined]
        if isinstance(cog, Welcome):
            await cog.greet_from_button(interaction, self.user_id, self.joined_at)


def greet_view(user_id: int, joined_at: int, count: int = 0) -> discord.ui.View:
    """Vista sin caducidad con el botón de saludo (lo reengancha `DynamicItem`)."""
    view = discord.ui.View(timeout=None)
    view.add_item(GreetButton(user_id, joined_at, count))
    return view


class Welcome(commands.Cog):
    """Gestiona los mensajes de entrada y salida de miembros y el botón de saludo.

    Args:
        repository: Ajustes y entradas. `None` deja la bienvenida de siempre
            (vídeo en `#chat-general`, sin botón ni detección de vuelta).
        economy: Para el regalo por saludar. `None` deja saludar sin regalo.
        clock: Fuente de tiempo en segundos epoch; inyectable en pruebas.
    """

    def __init__(
        self,
        bot: commands.Bot,
        repository: WelcomeRepository | None = None,
        *,
        economy: EconomyService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.economy = economy
        self._clock = clock

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(GreetButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(GreetButton)

    # -- Canal y ajustes ------------------------------------------------------------

    def _chat_general(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Busca el canal de texto reservado para los mensajes automáticos."""
        return discord.utils.get(
            guild.text_channels,
            name=CHAT_GENERAL_CHANNEL_NAME,
        )

    async def settings(self, guild_id: int) -> WelcomeSettings:
        """Ajustes del servidor; los de por defecto si no hay o falla la lectura."""
        if self.repository is None:
            return WelcomeSettings()
        try:
            return await self.repository.settings(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron leer los ajustes de bienvenida de %s", guild_id)
            return WelcomeSettings()

    def welcome_channel(
        self, guild: discord.Guild, settings: WelcomeSettings
    ) -> discord.TextChannel | None:
        """Canal configurado si sigue existiendo; si no, `#chat-general`."""
        if settings.channel_id is not None:
            channel = guild.get_channel(settings.channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
        return self._chat_general(guild)

    # -- Entrada ----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Da la bienvenida al miembro con una frase, el GIF y el botón de saludo."""
        guild = member.guild
        settings = await self.settings(guild.id)
        channel = self.welcome_channel(guild, settings)
        if channel is None:
            logger.warning(
                "No existe el canal #%s en el servidor %s; se omitió la bienvenida",
                CHAT_GENERAL_CHANNEL_NAME,
                guild.id,
            )
            return

        joined_at = self._clock()
        returning = False
        if self.repository is not None:
            try:
                record = await self.repository.record_join(guild.id, member.id, now=joined_at)
                returning = record.joins > 1
            except (OSError, sqlite3.Error):
                logger.exception("No se pudo apuntar la entrada de %s", member.id)

        phrases = await asyncio.to_thread(load_welcome_phrases)
        phrase = render_phrase(pick_phrase(phrases, returning=returning), member.mention)
        content, embed, use_video = welcome_payload(phrase, settings.gif_url)
        if use_video and not WELCOME_VIDEO_PATH.is_file():
            logger.error("No se encuentra el vídeo de bienvenida: %s", WELCOME_VIDEO_PATH)
            use_video = False

        kwargs: dict[str, Any] = {
            "content": content,
            "allowed_mentions": discord.AllowedMentions(
                everyone=False, roles=False, users=[member], replied_user=False
            ),
        }
        if embed is not None:
            kwargs["embed"] = embed
        # Sin base de datos no se pueden contar saludos ni impedir repetidos.
        if self.repository is not None and not member.bot:
            kwargs["view"] = greet_view(member.id, int(joined_at))
        video: discord.File | None = None
        try:
            if use_video:
                video = discord.File(WELCOME_VIDEO_PATH, filename="bienvenida.mp4")
                kwargs["file"] = video
            await channel.send(**kwargs)
        except (discord.Forbidden, discord.HTTPException, OSError):
            logger.warning(
                "No se pudo enviar la bienvenida al canal %s del servidor %s",
                channel.id,
                guild.id,
                exc_info=True,
            )
        finally:
            if video is not None:
                video.close()

    async def preview(
        self, member: discord.Member, settings: WelcomeSettings
    ) -> tuple[str, discord.Embed | None]:
        """Texto y embed de una bienvenida de muestra para `bienv` (sin botón ni vídeo)."""
        phrases = await asyncio.to_thread(load_welcome_phrases)
        phrase = render_phrase(random.choice(phrases.welcomes), member.mention)
        content, embed, use_video = welcome_payload(phrase, settings.gif_url)
        if use_video:
            content += "\n-# Sin GIF configurado: se adjunta el vídeo de Kratos."
        return content, embed

    # -- Saludar ----------------------------------------------------------------------

    async def greet_from_button(
        self, interaction: discord.Interaction, newcomer_id: int, joined_at: int
    ) -> None:
        """Respuesta al botón 👋: suma el saludo, paga el regalo o explica por qué no."""
        guild = interaction.guild
        if guild is None or self.repository is None:
            return
        greeter = interaction.user
        now = self._clock()
        check = check_greeting(newcomer_id, greeter.id, joined_at=joined_at, now=now)
        if check is GreetCheck.SELF:
            await interaction.response.send_message(
                "¡Ay, bendito! No te puedes dar la bienvenida a ti mismo. Ya estás dentro, "
                "mi amor, relájate y tómate un cafecito.",
                ephemeral=True,
            )
            return
        if check is GreetCheck.EXPIRED:
            await interaction.response.send_message(
                "Llegaste tarde a la fiesta: ya no es nuevo, el botón dura un día.",
                ephemeral=True,
            )
            return
        try:
            count = await self.repository.add_greeting(guild.id, newcomer_id, greeter.id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo apuntar un saludo de bienvenida")
            await interaction.response.send_message(
                "Se me cayó el café encima y no pude apuntar el saludo. Prueba otra vez.",
                ephemeral=True,
            )
            return
        if count is None:
            await interaction.response.send_message(
                "Ya le diste la bienvenida. Con una vez basta, no lo agobies.", ephemeral=True
            )
            return
        # Editar el mensaje con el contador es la confirmación pública: una sola
        # llamada a Discord, sin mensajes extra en el canal.
        await interaction.response.edit_message(view=greet_view(newcomer_id, joined_at, count))
        incomes = await self._pay_greeting(guild.id, newcomer_id, greeter.id)
        delta = StatDelta(add={"welcomes_given": 1})
        if incomes is not None:
            mine, theirs = incomes
            text = (
                f"👋 ¡Wepa! Saludo entregado. {CURRENCY_EMOJI} +{format_amount(GREETER_GIFT)} "
                f"brutos para ti y +{format_amount(WELCOMED_GIFT)} para <@{newcomer_id}>, "
                "para que se estrene en la ruleta.\n"
                f"{tax_line(mine.gross, mine.tax, mine.rate)}"
            )
            hint = await renta.hint(self.bot, guild.id, greeter.id)
            if hint is not None:
                text += f"\n{hint}"
            try:
                await interaction.followup.send(
                    text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                )
            except discord.HTTPException:
                logger.warning("No se pudo confirmar el regalo de bienvenida", exc_info=True)
            # Saludar da dinero: gancho de la Renta (ver Biblia.txt, sección 4).
            await renta.remind(self.bot, interaction)
            delta.add["tax_paid"] = mine.tax
            delta.peak["balance_max"] = mine.balance
            if theirs.tax:
                logros.note(
                    self.bot, guild.id, newcomer_id, StatDelta(add={"tax_paid": theirs.tax})
                )
        if is_fast_greeting(joined_at=joined_at, now=now):
            delta.add["welcomes_fast"] = 1
        await logros.track(self.bot, guild.id, greeter, interaction.channel, delta)

    async def _pay_greeting(
        self, guild_id: int, newcomer_id: int, greeter_id: int
    ) -> tuple[IncomeResult, IncomeResult] | None:
        """Paga el regalo de un saludo a los dos, con retención de IRPF (ver el servicio).

        Returns:
            `(lo de quien saluda, lo del recién llegado)`, o `None` si no hay
            economía o falla el pago. Un fallo se registra y no impide contar
            el saludo.
        """
        if self.economy is None:
            return None
        try:
            mine = await self.economy.pay_income(
                guild_id, greeter_id, gross=GREETER_GIFT, concept="bienv:saludar"
            )
            theirs = await self.economy.pay_income(
                guild_id, newcomer_id, gross=WELCOMED_GIFT, concept="bienv:saludado"
            )
        except (OSError, sqlite3.Error, BalanceLimitError):
            logger.exception("No se pudo pagar el regalo de bienvenida")
            return None
        return mine, theirs

    # -- Salida -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Despide al miembro con una frase elegida al azar."""
        settings = await self.settings(member.guild.id)
        channel = self.welcome_channel(member.guild, settings)
        if channel is None:
            logger.warning(
                "No existe el canal #%s en el servidor %s; se omitió la despedida",
                CHAT_GENERAL_CHANNEL_NAME,
                member.guild.id,
            )
            return

        insults = await asyncio.to_thread(load_farewell_insults)
        insult = random.choice(insults)
        display_name = discord.utils.escape_markdown(member.display_name)
        try:
            await channel.send(
                f"👋 **{display_name}** {insult}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo enviar la despedida al canal %s del servidor %s",
                channel.id,
                member.guild.id,
                exc_info=True,
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los ajustes y entradas del servidor cuando el bot sale de él."""
        if self.repository is not None:
            await self.repository.delete_guild_data(guild.id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog de bienvenida y despedida en el cliente."""
    await bot.add_cog(Welcome(bot, bot.welcome, economy=bot.economy))
