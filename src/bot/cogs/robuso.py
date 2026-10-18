"""🎸 Robuso: lo que hace Robuso (y lo que le hacen) para su categoría secreta de logros.

Sin comandos: solo escucha eventos. Qué cuenta lo decide `bot.services.robuso`; aquí
se mira Discord y se recuerda en memoria lo que dura un rato:

- **Mensajes** (`on_message`): los de Robuso (temas, fotos de Hong Kong, las horas,
  volver tras días sin escribir, despedirse y seguir escribiendo, decir que va a jugar)
  y los que van para él (respuestas, menciones, risas, nombrarlo cuando lleva días sin
  aparecer, darle la bienvenida al volver).
- **Reacciones** a sus mensajes (`on_raw_reaction_add`) y sus mensajes **borrados**
  (`on_message_delete`, solo los que siguen en la caché de discord.py).
- **Voz** (`on_voice_state_update` y `_tick`, cada minuto): si entra a jugar tras
  decirlo, si vuelve a la llamada tras días sin entrar, si se asoma y se va, sus minutos
  en llamada desde Hong Kong y quién se queda esperándolo cuando no viene.
- **Presencias** (`on_presence_update`; sin el «Presence Intent» no llegan y no cuentan):
  a qué juega, si nadie más juega a eso, quién juega a lo mismo y sus cambios de estado.

Dónde está (Hong Kong o Madrid) se guarda como sus viajes y vueltas en sus propias
estadísticas de logros: está en Hong Kong si ha ido (`robuso_trips`) más veces de las
que ha vuelto (`robuso_returns`). Así no hace falta tabla propia, y los logros de
viajes salen de lo mismo. Lo cambia un administrador con `robuso` (`set_place`). La
última vez que escribió o entró en voz sale de la actividad que ya guarda el panel
`admin` (`bot.last_seen`), leída una vez por servidor y seguida aquí en memoria.

Lo que solo vive en memoria (promesas, despedidas, bienvenidas, el «una vez al día»)
se pierde al reiniciar: como mucho, no cuenta algo de ese rato. Permisos: ninguno más
que leer los canales; los avisos de logros los pone el cog de logros.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from bot.cogs import achievements
from bot.cogs.achievements import Achievements
from bot.services.achievements import StatDelta, analyze_laugh, is_laugh_emoji
from bot.services.levels import TIMEZONE
from bot.services.robuso import (
    DAILY,
    DAY_SECONDS,
    GOODBYE_SECONDS,
    PEEK_SECONDS,
    PROMISE_SECONDS,
    RETURN_DAYS,
    ROBUSO_ID,
    WELCOME_SECONDS,
    author_stats,
    chat_stats,
    fan_stats,
    game_stats,
    names_robuso,
    promises_to_play,
    says_goodbye,
)
from bot.utils.cogs import find_cog

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Mensajes de Robuso de los que se recuerda quién ha respondido o reaccionado, para
#: que responder diez veces al mismo mensaje cuente una.
MEMORY = 2_000


def _games(member: discord.Member) -> set[str]:
    """A qué juega ahora según su actividad de Discord (en minúsculas)."""
    return {
        a.name.lower()
        for a in member.activities
        if a.type is discord.ActivityType.playing and a.name
    }


def _status(member: discord.Member) -> str | None:
    """Su estado personalizado, si tiene uno."""
    return next((a.name for a in member.activities if isinstance(a, discord.CustomActivity)), None)


class Robuso(commands.Cog):
    """Cuenta lo de 🎸 Robuso y guarda si está en Hong Kong o en Madrid."""

    def __init__(self, bot: commands.Bot, *, clock: Callable[[], float] = time.time) -> None:
        self.bot = bot
        self._clock = clock
        #: Si está en Hong Kong, por servidor (se lee una vez de sus estadísticas).
        self._in_hk: dict[int, bool] = {}
        #: Cuándo escribió y cuándo entró o salió de voz por última vez, por servidor.
        self._last_message: dict[int, float | None] = {}
        self._last_voice: dict[int, float | None] = {}
        #: Ha dicho que viene a jugar: `(hasta cuándo, canal)`, por servidor.
        self._promises: dict[int, tuple[float, int]] = {}
        #: Cuándo se despidió por última vez, por servidor.
        self._goodbyes: dict[int, float] = {}
        #: Acaba de volver: `(canal, hasta cuándo)` para darle la bienvenida.
        self._welcomes: dict[int, tuple[int, float]] = {}
        #: Quién lo ha echado de menos en esta ausencia (se vacía cuando escribe).
        self._missed: dict[int, set[int]] = {}
        #: Cuándo entró en voz, para pillarlo si se asoma y se va.
        self._joined: dict[int, float] = {}
        self._replied: OrderedDict[int, set[int]] = OrderedDict()
        self._reacted: OrderedDict[int, set[int]] = OrderedDict()
        self._laughed: OrderedDict[int, set[int]] = OrderedDict()
        #: Lo de `DAILY` ya contado hoy: `(servidor, miembro, estadística)`.
        self._day: date | None = None
        self._counted: set[tuple[int, int, str]] = set()

    async def cog_load(self) -> None:
        """Arranca la tarea de cada minuto."""
        self._tick.start()

    async def cog_unload(self) -> None:
        """Para la tarea de cada minuto."""
        self._tick.cancel()

    # -- Estado ---------------------------------------------------------------------

    async def _load(self, guild_id: int) -> bool:
        """Si Robuso está en Hong Kong; la primera vez lee también cuándo se le vio.

        Si la base de datos falla no se guarda nada y se vuelve a intentar a la próxima.
        """
        if guild_id in self._in_hk:
            return self._in_hk[guild_id]
        try:
            in_hk = await self._read_place(guild_id)
            repo = getattr(self.bot, "last_seen", None)
            seen = (await repo.guild(guild_id)).get(ROBUSO_ID) if repo is not None else None
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer lo de Robuso en el servidor %s", guild_id)
            return False
        self._last_message.setdefault(guild_id, getattr(seen, "message_at", None))
        self._last_voice.setdefault(guild_id, getattr(seen, "voice_at", None))
        return self._in_hk.setdefault(guild_id, in_hk)

    async def _read_place(self, guild_id: int) -> bool:
        stats = await self._robuso_stats(guild_id)
        return stats.get("robuso_trips", 0) > stats.get("robuso_returns", 0)

    async def _robuso_stats(self, guild_id: int) -> dict[str, int]:
        cog = find_cog(self.bot, Achievements)
        if cog is None:
            return {}
        return dict((await cog.fresh_profile(guild_id, ROBUSO_ID)).stats)

    async def in_hong_kong(self, guild_id: int) -> bool:
        """Si Robuso está ahora en Hong Kong, leído de sus estadísticas."""
        self._in_hk[guild_id] = await self._read_place(guild_id)
        return self._in_hk[guild_id]

    async def set_place(
        self, guild_id: int, *, hong_kong: bool, channel: discord.abc.Messageable | None
    ) -> bool:
        """Apunta que Robuso se va a Hong Kong (`hong_kong`) o vuelve a Madrid.

        Suma un viaje o una vuelta a sus estadísticas, con el día de la ida y, al
        volver, los días que ha pasado allí. Los logros que salten se anuncian en
        `channel`.

        Returns:
            Si ha cambiado algo (`False` si ya estaba ahí).

        Raises:
            LookupError: Si los logros no están cargados (es donde se guarda).
        """
        cog = find_cog(self.bot, Achievements)
        if cog is None:
            raise LookupError("Los logros no están cargados.")
        stats = await self._robuso_stats(guild_id)
        here = stats.get("robuso_trips", 0) > stats.get("robuso_returns", 0)
        self._in_hk[guild_id] = here
        if here == hong_kong:
            return False
        today = datetime.fromtimestamp(self._clock(), TIMEZONE).date().toordinal()
        if hong_kong:
            delta = StatDelta(add={"robuso_trips": 1}, peak={"robuso_left_on": today})
        else:
            left = stats.get("robuso_left_on", 0)
            delta = StatDelta(add={"robuso_returns": 1})
            if left:
                delta.peak["robuso_trip_days_max"] = today - left
        await cog.apply(guild_id, ROBUSO_ID, delta, channel)
        self._in_hk[guild_id] = hong_kong
        return True

    def _note(
        self,
        guild_id: int,
        user_id: int,
        add: dict[str, int],
        channel_id: int | None = None,
        peak: dict[str, int] | None = None,
    ) -> None:
        """Suma a un miembro, contando como mucho una vez al día lo de `DAILY`."""
        today = datetime.fromtimestamp(self._clock(), TIMEZONE).date()
        if today != self._day:
            self._day, self._counted = today, set()
        for stat in [s for s in add if s in DAILY]:
            key = (guild_id, user_id, stat)
            if key in self._counted:
                del add[stat]
            else:
                self._counted.add(key)
        if add or peak:
            delta = StatDelta(add=add, peak=peak or {})
            achievements.note(self.bot, guild_id, user_id, delta, channel_id)

    @staticmethod
    def _first(memory: OrderedDict[int, set[int]], message_id: int, user_id: int) -> bool:
        """Si es la primera vez que `user_id` hace esto con ese mensaje (olvida los viejos)."""
        users = memory.get(message_id)
        if users is None:
            users = memory[message_id] = set()
            if len(memory) > MEMORY:
                memory.popitem(last=False)
        if user_id in users:
            return False
        users.add(user_id)
        return True

    # -- Mensajes y reacciones ------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Lo que escribe Robuso y lo que le escriben. No guarda el texto."""
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
            or message.is_system()
        ):
            return
        if message.author.id == ROBUSO_ID:
            await self._robuso_wrote(message, message.guild.id)
        else:
            await self._fan_wrote(message, message.guild.id)

    async def _robuso_wrote(self, message: discord.Message, guild_id: int) -> None:
        channel_id = message.channel.id
        now = message.created_at.timestamp()
        in_hk = await self._load(guild_id)
        images = sum(1 for a in message.attachments if (a.content_type or "").startswith("image/"))
        add = author_stats(message.content, now=now, in_hk=in_hk, images=images)
        peak: dict[str, int] = {}
        last = self._last_message.get(guild_id)
        if last is not None and (days := int((now - last) // DAY_SECONDS)) >= 1:
            peak["robuso_silence_max"] = days
            if days >= RETURN_DAYS:
                self._welcomes[guild_id] = (channel_id, now + WELCOME_SECONDS)
        self._last_message[guild_id] = now
        self._missed.pop(guild_id, None)
        goodbye = says_goodbye(message.content)
        said = self._goodbyes.pop(guild_id, None)
        if said is not None and now - said <= GOODBYE_SECONDS and not goodbye:
            add["robuso_fake_goodbye"] = 1
        if goodbye:
            self._goodbyes[guild_id] = now
        voice = getattr(message.author, "voice", None)
        if promises_to_play(message.content) and getattr(voice, "channel", None) is None:
            self._promises.setdefault(guild_id, (now + PROMISE_SECONDS, channel_id))
        self._note(guild_id, ROBUSO_ID, add, channel_id, peak)

    async def _fan_wrote(self, message: discord.Message, guild_id: int) -> None:
        channel_id, author_id = message.channel.id, message.author.id
        now = message.created_at.timestamp()
        add = chat_stats(message.content)
        welcome = self._welcomes.get(guild_id)
        if welcome is not None and welcome[0] == channel_id and now <= welcome[1]:
            del self._welcomes[guild_id]
            add["robuso_welcomes"] = 1
        is_reply = message.reference is not None and message.type is discord.MessageType.reply
        replied = message.reference.resolved if is_reply and message.reference else None
        to_robuso = getattr(getattr(replied, "author", None), "id", None) == ROBUSO_ID
        pinged = not to_robuso and any(u.id == ROBUSO_ID for u in message.mentions)
        if pinged or to_robuso or names_robuso(message.content):
            in_hk = await self._load(guild_id)
            last = self._last_message.get(guild_id)
            first = (
                to_robuso
                and replied is not None
                and self._first(self._replied, replied.id, author_id)
            )
            attachments = getattr(replied, "attachments", ())
            add |= fan_stats(
                message.content,
                now=now,
                pinged=pinged,
                replied=first,
                reply_images=any((a.content_type or "").startswith("image/") for a in attachments),
                laughed=analyze_laugh(message.content).laughed,
                in_hk=in_hk,
                silent_for=None if last is None else now - last,
            )
            # Echarlo de menos cuenta una vez por persona en cada ausencia.
            missed = self._missed.setdefault(guild_id, set())
            if add.pop("robuso_missed", 0) and author_id not in missed:
                missed.add(author_id)
                add["robuso_missed"] = 1
        self._note(guild_id, author_id, add, channel_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Reacciones de los demás a los mensajes de Robuso (una por persona y mensaje)."""
        if (
            payload.guild_id is None
            or payload.message_author_id != ROBUSO_ID
            or payload.user_id == ROBUSO_ID
            or payload.member is None
            or payload.member.bot
        ):
            return
        add: dict[str, int] = {}
        if self._first(self._reacted, payload.message_id, payload.user_id):
            add["robuso_reacts"] = 1
        emoji = payload.emoji
        if is_laugh_emoji(emoji.name or str(emoji)) and self._first(
            self._laughed, payload.message_id, payload.user_id
        ):
            add["robuso_laughs"] = 1
        self._note(payload.guild_id, payload.user_id, add, payload.channel_id)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        """Robuso borra un mensaje suyo (si discord.py aún lo tenía en caché)."""
        if message.guild is not None and message.author.id == ROBUSO_ID:
            self._note(message.guild.id, ROBUSO_ID, {"robuso_deleted": 1})

    # -- Voz y presencias -----------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        """Robuso entra o sale de una llamada."""
        if member.id != ROBUSO_ID or before.channel == after.channel:
            return
        guild_id = member.guild.id
        now = self._clock()
        await self._load(guild_id)
        add: dict[str, int] = {}
        peak: dict[str, int] = {}
        if before.channel is None and after.channel not in (None, member.guild.afk_channel):
            assert after.channel is not None  # comprobado justo arriba
            self._joined[guild_id] = now
            promise = self._promises.pop(guild_id, None)
            if promise is not None and now <= promise[0]:
                add["robuso_kept_word"] = 1
            last = self._last_voice.get(guild_id)
            if last is not None and (days := int((now - last) // DAY_SECONDS)) >= 1:
                peak["robuso_voice_gap_max"] = days
                if days >= RETURN_DAYS:
                    for other in after.channel.members:
                        if not other.bot and other.id != ROBUSO_ID:
                            self._note(guild_id, other.id, {"robuso_reunions": 1}, after.channel.id)
        elif after.channel is None:
            joined = self._joined.pop(guild_id, None)
            if joined is not None and now - joined < PEEK_SECONDS:
                add["robuso_peeks"] = 1
        self._last_voice[guild_id] = now
        channel = after.channel or before.channel
        self._note(guild_id, ROBUSO_ID, add, channel.id if channel else None, peak)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        """A qué juega Robuso, quién juega con él y sus cambios de estado."""
        if after.bot:
            return
        guild = after.guild
        started = _games(after) - _games(before)
        if after.id == ROBUSO_ID:
            add: dict[str, int] = {}
            status = _status(after)
            if status and status != _status(before):
                add["robuso_status"] = 1
            for game in started:
                players = [
                    m
                    for m in guild.members
                    if m.id != ROBUSO_ID and not m.bot and game in _games(m)
                ]
                add |= game_stats(game, others_playing=bool(players))
                for player in players:
                    self._note(guild.id, player.id, {"robuso_coop": 1})
            self._note(guild.id, ROBUSO_ID, add)
        elif started:
            robuso = guild.get_member(ROBUSO_ID)
            if robuso is not None and started & _games(robuso):
                self._note(guild.id, after.id, {"robuso_coop": 1})

    # -- Cada minuto ----------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def _tick(self) -> None:
        """Cada minuto: plantones, lo que caduca y la voz desde Hong Kong."""
        try:
            self.check(self._clock())
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error en el minuto de los logros de Robuso")

    @_tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()

    def check(self, now: float) -> None:
        """Cuenta a quien deja plantado, borra lo caducado y suma sus minutos desde Hong Kong.

        Quien está en una llamada del servidor cuando vence la promesa de Robuso se ha
        quedado esperándolo. Los minutos desde Hong Kong cuentan si hay alguien más en
        su llamada.
        """
        for guild_id, (deadline, channel_id) in list(self._promises.items()):
            if now < deadline:
                continue
            del self._promises[guild_id]
            self._note(guild_id, ROBUSO_ID, {"robuso_flakes": 1}, channel_id)
            guild = self.bot.get_guild(guild_id)
            for channel in guild.voice_channels if guild is not None else ():
                if channel == channel.guild.afk_channel:
                    continue
                for member in channel.members:
                    if not member.bot and member.id != ROBUSO_ID:
                        self._note(guild_id, member.id, {"robuso_waited": 1}, channel.id)
        for guild_id, (_channel, until) in list(self._welcomes.items()):
            if now > until:
                del self._welcomes[guild_id]
        for guild_id, said in list(self._goodbyes.items()):
            if now - said > GOODBYE_SECONDS:
                del self._goodbyes[guild_id]
        for guild in self.bot.guilds:
            if not self._in_hk.get(guild.id):
                continue
            robuso = guild.get_member(ROBUSO_ID)
            voice = robuso.voice if robuso is not None else None
            channel = voice.channel if voice is not None else None
            if (
                channel is not None
                and channel != guild.afk_channel
                and sum(1 for m in channel.members if not m.bot) >= 2
            ):
                self._note(guild.id, ROBUSO_ID, {"robuso_hk_vc_minutes": 1}, channel.id)


# -- Puntos de entrada para otros cogs ----------------------------------------------------


async def in_hong_kong(bot: commands.Bot, guild_id: int) -> bool | None:
    """Si Robuso está en Hong Kong; `None` si el cog no está cargado."""
    cog = find_cog(bot, Robuso)
    return None if cog is None else await cog.in_hong_kong(guild_id)


async def set_place(
    bot: commands.Bot,
    guild_id: int,
    *,
    hong_kong: bool,
    channel: discord.abc.Messageable | None,
) -> bool | None:
    """Apunta dónde está Robuso (ver `Robuso.set_place`); `None` si el cog no está."""
    cog = find_cog(bot, Robuso)
    if cog is None:
        return None
    return await cog.set_place(guild_id, hong_kong=hong_kong, channel=channel)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog."""
    await bot.add_cog(Robuso(bot))
