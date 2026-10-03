"""Comandos de broma: de momento, `/babel` (teléfono escacharrado con traductores).

`babel` pasa un texto por 99 idiomas al azar y lo devuelve al español, para
ver qué queda de él. Tiene dos modos, según lo que se le dé:

- **Frase**: `.babel el gato come pescado` (o responder a un mensaje con
  `.babel`) muestra el antes y el después.
- **Nombres**: si solo se le dan menciones, `.babel @Ana @Luis #general`,
  cambia de verdad el apodo de esos miembros y el nombre de esos canales por
  su versión babelizada. Todos los nombres viajan juntos en una sola cadena.

Permisos (los mismos que exige Discord para hacerlo a mano):

- Tu propio apodo: permiso **Cambiar apodo**.
- El apodo de otro: **Gestionar apodos** y un rol más alto que el suyo (el
  dueño del servidor no tiene esa limitación, pero su apodo no se puede tocar).
- Un canal: **Gestionar canales** en ese canal.
- El bot necesita **Gestionar apodos** y **Gestionar canales**, y un rol por
  encima de los miembros a los que renombra.

No hay forma de deshacerlo con el bot: el apodo se quita desde Discord y el
canal se renombra a mano.

Una tirada son 100 peticiones seguidas a Google Translate y tarda unos
segundos, así que solo corre una a la vez en todo el bot (además de no
saturar el NAS, evita que Google limite la IP) y el aviso de progreso se
edita cada pocas vueltas, no en todas, por el límite de ediciones de Discord.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from bot.services.babel import (
    HOME_LANGUAGE,
    LANGUAGES,
    BabelResult,
    ChannelRenameLimiter,
    GoogleTranslator,
    Translator,
    keep_lines,
    pick_route,
    readable_channel_name,
    run_chain,
    split_decoration,
)
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

#: Longitud máxima del texto en modo frase: cada vuelta lo envía entero a
#: Google, y en un embed los campos tienen un tope de 1024 caracteres.
MAX_TEXT_LENGTH = 300

#: Máximo de miembros y canales por comando en modo nombres.
MAX_TARGETS = 10

#: Límites de Discord para un apodo y un nombre de canal.
NICK_MAX_LENGTH = 32
CHANNEL_NAME_MAX_LENGTH = 100

#: Cada cuántas vueltas se actualiza el aviso de progreso.
PROGRESS_EVERY = 10

#: Tiempo máximo de cada petición a Google.
REQUEST_TIMEOUT_SECONDS = 10

#: Máximo de caracteres de un campo de embed (límite de Discord).
FIELD_LIMIT = 1024

EMBED_COLOR = discord.Color.gold()

BUSY_MESSAGE = "Ya hay algo dando la vuelta al mundo. Espera a que vuelva."

_USER_MENTION = re.compile(r"<@!?(\d+)>")
_CHANNEL_MENTION = re.compile(r"<#(\d+)>")
_ANY_MENTION = re.compile(r"<@!?(\d+)>|<#(\d+)>")


@dataclass
class RenameTarget:
    """Un miembro o canal a babelizar, con su nombre ya preparado para traducir.

    Attributes:
        target: El miembro o canal de Discord.
        label: Nombre actual tal como se muestra en el resultado.
        prefix: Adorno inicial (emojis, separadores) que se conserva sin traducir.
        text: Parte del nombre que se traduce.
    """

    target: discord.Member | discord.abc.GuildChannel
    label: str
    prefix: str
    text: str

    @property
    def is_channel(self) -> bool:
        """`True` si es un canal; `False` si es un miembro."""
        return not isinstance(self.target, discord.Member)

    def new_name(self, translated: str) -> str:
        """Nombre final: adorno + traducción, recortado al límite de Discord."""
        limit = CHANNEL_NAME_MAX_LENGTH if self.is_channel else NICK_MAX_LENGTH
        return (self.prefix + translated)[:limit].strip()


def _language_name(code: str) -> str:
    """Nombre en español de un código de idioma."""
    return "español" if code == HOME_LANGUAGE else LANGUAGES.get(code, code)


def _clip(text: str, limit: int = FIELD_LIMIT) -> str:
    """Recorta `text` a `limit` caracteres, marcando el corte con «…»."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_route(route: tuple[str, ...]) -> str:
    """Ruta como `es → ja → zu → … → es`, recortada si no cabe en un campo."""
    joined = " → ".join(route)
    if len(joined) <= FIELD_LIMIT:
        return joined
    # Se conserva el principio y el final, que son lo más legible.
    tail = " → ".join(route[-5:])
    head_budget = FIELD_LIMIT - len(tail) - len(" → … → ")
    head = joined[:head_budget].rsplit(" → ", 1)[0]
    return f"{head} → … → {tail}"


def _base_embed(result: BabelResult) -> discord.Embed:
    """Embed con el título común de los dos modos."""
    return discord.Embed(title=f"🗼 {result.hops} traducciones después…", color=EMBED_COLOR)


def _add_route(embed: discord.Embed, result: BabelResult) -> None:
    """Añade la ruta y, si la cadena se cortó, el aviso al pie."""
    embed.add_field(name="Ruta", value=_format_route(result.route), inline=False)
    if result.stopped_early:
        embed.set_footer(
            text="Google se cansó antes de terminar, así que ha dado menos vueltas. "
            "Prueba otra vez dentro de un rato."
        )


def build_result_embed(result: BabelResult) -> discord.Embed:
    """Embed del modo frase: el antes, el después y los idiomas por los que pasó."""
    embed = _base_embed(result)
    embed.add_field(name="Antes", value=_clip(result.original), inline=False)
    after_title = "Después"
    if result.final_language != HOME_LANGUAGE:
        after_title += f" (se quedó en {_language_name(result.final_language)})"
    embed.add_field(name=after_title, value=_clip(result.final) or "*(nada)*", inline=False)
    _add_route(embed, result)
    return embed


def build_rename_embed(
    result: BabelResult, changes: list[str], failures: list[str]
) -> discord.Embed:
    """Embed del modo nombres: qué se cambió, qué no y la ruta."""
    embed = _base_embed(result)
    if changes:
        embed.add_field(name="Bautizos", value=_clip("\n".join(changes)), inline=False)
    if failures:
        embed.add_field(name="No se pudo", value=_clip("\n".join(failures)), inline=False)
    _add_route(embed, result)
    return embed


def member_refusal(
    invoker: discord.Member, me: discord.Member, target: discord.Member
) -> str | None:
    """Motivo por el que `invoker` no puede babelizar el apodo de `target`, o `None`.

    Reproduce las reglas de Discord para cambiar apodos a mano, para que el
    bot no sirva de atajo a quien no podría hacerlo él mismo.
    """
    guild = target.guild
    if target.id == guild.owner_id:
        return "es el dueño del servidor y Discord no deja cambiarle el apodo"
    if target.id == invoker.id:
        perms = invoker.guild_permissions
        if not (perms.change_nickname or perms.manage_nicknames):
            return "no tienes permiso para cambiarte el apodo"
    else:
        if not invoker.guild_permissions.manage_nicknames:
            return "cambiar el apodo de otros requiere el permiso «Gestionar apodos»"
        if invoker.id != guild.owner_id and invoker.top_role <= target.top_role:
            return "tiene un rol igual o superior al tuyo"
    if target.id == me.id:
        if not me.guild_permissions.change_nickname:
            return "al bot le falta el permiso «Cambiar apodo»"
    else:
        if not me.guild_permissions.manage_nicknames:
            return "al bot le falta el permiso «Gestionar apodos»"
        if me.top_role <= target.top_role:
            return "tiene un rol igual o superior al del bot"
    return None


def channel_refusal(
    invoker: discord.Member,
    me: discord.Member,
    channel: discord.abc.GuildChannel,
    limiter: ChannelRenameLimiter,
) -> str | None:
    """Motivo por el que `invoker` no puede babelizar `channel`, o `None`."""
    if not channel.permissions_for(invoker).manage_channels:
        return "necesitas el permiso «Gestionar canales» en ese canal"
    if not channel.permissions_for(me).manage_channels:
        return "al bot le falta el permiso «Gestionar canales» en ese canal"
    if not limiter.can_rename(channel.id):
        return "Discord solo deja renombrar un canal 2 veces cada 10 minutos"
    return None


def only_mentions(text: str) -> bool:
    """`True` si el texto no tiene nada más que menciones (y espacios)."""
    return bool(_ANY_MENTION.search(text)) and not _ANY_MENTION.sub("", text).strip()


def mentions_to_names(text: str, guild: discord.Guild | None) -> str:
    """Sustituye `<@id>` y `<#id>` por el nombre visible, para traducirlos como palabras."""
    if guild is None:
        return text

    def user_name(match: re.Match[str]) -> str:
        member = guild.get_member(int(match.group(1)))
        return member.display_name if member else "alguien"

    def channel_name(match: re.Match[str]) -> str:
        channel = guild.get_channel(int(match.group(1)))
        return readable_channel_name(channel.name) if channel else "algún canal"

    return _CHANNEL_MENTION.sub(channel_name, _USER_MENTION.sub(user_name, text))


class Fun(commands.Cog):
    """Comandos sin más utilidad que reírse un rato."""

    def __init__(
        self,
        bot: commands.Bot,
        translator: Translator | None = None,
        limiter: ChannelRenameLimiter | None = None,
    ) -> None:
        self.bot = bot
        # Inyectable para las pruebas; si no se da, se crea en `cog_load`.
        self._translator = translator
        self._limiter = limiter or ChannelRenameLimiter()
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def cog_load(self) -> None:
        """Abre la sesión HTTP del traductor real (una para todo el cog)."""
        if self._translator is None:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._translator = GoogleTranslator(self._session)

    async def cog_unload(self) -> None:
        """Cierra la sesión HTTP al descargar el cog o apagar el bot."""
        if self._session is not None:
            await self._session.close()

    async def _run(
        self, responder: CommandResponder, text: str, translate: Translator
    ) -> BabelResult:
        """Hace la tirada con aviso de progreso. Hay que tener ya el candado."""
        await responder.start_progress("🗼 Despegando hacia el primer idioma…")

        async def report(step: int, total: int, language: str) -> None:
            if step % PROGRESS_EVERY == 0 and step < total:
                await responder.update_progress(
                    f"🗼 Vuelta {step}/{total} · ahora en {_language_name(language)}…"
                )

        return await run_chain(text, pick_route(), translate, report)

    async def _babel_impl(
        self, responder: CommandResponder, text: str, replied_text: str = ""
    ) -> None:
        """Lógica compartida entre `/babel` y `.babel`: elige el modo y lo ejecuta."""
        text = text.strip()
        if not text and not replied_text.strip():
            await responder.send_error(
                "Dime qué traducir: `.babel tu frase`, `.babel @alguien #canal` "
                "para cambiarles el nombre, o responde a un mensaje con `.babel`."
            )
            return
        if self._lock.locked():
            await responder.send_error(BUSY_MESSAGE)
            return
        if only_mentions(text):
            await self._rename_impl(responder, text)
        else:
            phrase = mentions_to_names(text, responder.guild) if text else replied_text
            await self._phrase_impl(responder, phrase.strip())

    async def _phrase_impl(self, responder: CommandResponder, text: str) -> None:
        """Modo frase: traduce el texto y muestra el antes y el después."""
        if len(text) > MAX_TEXT_LENGTH:
            await responder.send_error(
                f"Máximo {MAX_TEXT_LENGTH} caracteres; ese texto tiene {len(text)}."
            )
            return
        assert self._translator is not None  # `cog_load` ya lo ha creado
        async with self._lock:
            result = await self._run(responder, text, self._translator)
            await responder.finish(
                embed=build_result_embed(result),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _collect_targets(
        self, guild: discord.Guild, invoker: discord.Member, text: str
    ) -> tuple[list[RenameTarget], list[str]]:
        """Resuelve las menciones y separa lo que se puede renombrar de lo que no.

        Returns:
            Los objetivos válidos y una línea de motivo por cada rechazado.
        """
        me = guild.me
        targets: list[RenameTarget] = []
        refused: list[str] = []
        seen: set[str] = set()

        for match in _ANY_MENTION.finditer(text):
            user_id, channel_id = match.group(1), match.group(2)
            key = f"u{user_id}" if user_id else f"c{channel_id}"
            if key in seen:
                continue
            seen.add(key)

            target: discord.Member | discord.abc.GuildChannel
            if channel_id:
                channel = guild.get_channel(int(channel_id))
                if channel is None:
                    refused.append(f"{match.group(0)}: no encuentro ese canal")
                    continue
                target, label = channel, f"#{channel.name}"
                reason = channel_refusal(invoker, me, channel, self._limiter)
                prefix, core = split_decoration(channel.name)
                core = readable_channel_name(core)
            else:
                member = guild.get_member(int(user_id))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(user_id))
                    except discord.HTTPException:
                        refused.append(f"{match.group(0)}: no está en el servidor")
                        continue
                target, label = member, member.display_name
                reason = member_refusal(invoker, me, member)
                prefix, core = split_decoration(member.display_name)

            if reason:
                refused.append(f"**{label}**: {reason}")
            elif not core:
                refused.append(f"**{label}**: no tiene letras que traducir")
            else:
                targets.append(RenameTarget(target, label, prefix, core))

        return targets, refused

    async def _rename_impl(self, responder: CommandResponder, text: str) -> None:
        """Modo nombres: babeliza apodos y nombres de canal de verdad."""
        guild, invoker = responder.guild, responder.member
        if guild is None or invoker is None:
            await responder.send_error("Cambiar nombres solo funciona dentro de un servidor.")
            return
        if len({m.group(0) for m in _ANY_MENTION.finditer(text)}) > MAX_TARGETS:
            await responder.send_error(f"Como mucho {MAX_TARGETS} miembros o canales a la vez.")
            return

        targets, refused = await self._collect_targets(guild, invoker, text)
        if not targets:
            await responder.send_error(
                "No puedo cambiar ninguno de esos nombres:\n" + "\n".join(refused)
            )
            return
        # Buscar miembros ha podido ceder el turno: otra tirada pudo empezar.
        if self._lock.locked():
            await responder.send_error(BUSY_MESSAGE)
            return

        assert self._translator is not None  # `cog_load` ya lo ha creado
        async with self._lock:
            joined = "\n".join(t.text for t in targets)
            translate = keep_lines(self._translator, len(targets))
            result = await self._run(responder, joined, translate)

            changes: list[str] = []
            failures = list(refused)
            new_texts = result.final.split("\n")
            if result.hops == 0:
                failures.append("Google no ha traducido nada; no he tocado ningún nombre.")
            elif result.final_language != HOME_LANGUAGE or len(new_texts) != len(targets):
                # Se cortó lejos del español: mejor no dejar nombres a medias.
                failures.append("La cadena se cortó antes de volver al español; no he tocado nada.")
            else:
                reason = f"babel, pedido por {invoker} ({invoker.id})"
                for target, new_text in zip(targets, new_texts, strict=True):
                    line = await self._apply(target, target.new_name(new_text), reason)
                    (changes if line.startswith("✅") else failures).append(line)

            await responder.finish(
                embed=build_rename_embed(result, changes, failures),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _apply(self, target: RenameTarget, new_name: str, reason: str) -> str:
        """Aplica un nombre nuevo y devuelve la línea del resultado para el embed."""
        if not new_name:
            return f"**{target.label}**: la traducción se quedó en nada"
        try:
            if target.is_channel:
                await target.target.edit(name=new_name, reason=reason)
                self._limiter.record(target.target.id)
                shown = f"#{new_name}"
            else:
                await target.target.edit(nick=new_name, reason=reason)
                shown = new_name
        except discord.Forbidden:
            return f"**{target.label}**: Discord no me deja (revisa permisos y roles)"
        except discord.HTTPException as error:
            logger.warning("Babel: no se pudo renombrar %s: %s", target.target.id, error)
            return f"**{target.label}**: Discord ha dado un error"
        return f"✅ **{target.label}** → **{shown}**"

    @app_commands.command(
        name="babel", description="Traduce una frase, apodos o canales por 99 idiomas."
    )
    @app_commands.describe(
        texto="Una frase, o solo menciones (@alguien #canal) para cambiarles el nombre"
    )
    async def babel(self, interaction: discord.Interaction, texto: str) -> None:
        """Teléfono escacharrado con traductores; ver la docstring del módulo."""
        await self._babel_impl(InteractionResponder(interaction), texto)

    @commands.command(name="babel")
    async def babel_text(self, ctx: commands.Context, *, texto: str = "") -> None:
        """Versión de texto (`.babel`) de `/babel`.

        Sin texto, traduce el mensaje al que se responde (modo frase).
        """
        replied_text = ""
        reference = ctx.message.reference
        replied = reference.resolved if reference else None
        if not texto and isinstance(replied, discord.Message):
            replied_text = replied.clean_content
        await self._babel_impl(ContextResponder(ctx), texto, replied_text)


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(Fun(bot))
