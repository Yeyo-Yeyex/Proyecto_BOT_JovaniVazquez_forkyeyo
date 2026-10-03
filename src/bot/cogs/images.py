"""Comandos de imagen: `magik` y los 108 efectos al estilo Dank Memer.

Todos son **solo comandos de texto** (`.trigger @alguien`): los slash commands
se reservan para el resto del bot, y Discord además limita un bot a 100
comandos de `/`. Cada efecto se registra como un comando de texto con el
nombre que tiene en Dank Memer, generado desde `bot.services.memes.EFFECTS`.
Para no inundar la ayuda general, esos comandos van ocultos y se listan con
`.memes`.

Cómo se leen los argumentos de un efecto (ver :func:`parse_arguments`):

- Las menciones (`@alguien`) eligen de quién es el avatar. Responder a un
  mensaje cuenta como mencionar a su autor.
- Una imagen adjunta (en el mensaje o en el mensaje al que se responde)
  sustituye al avatar del objetivo.
- El resto es el texto. Los efectos de varios textos los separan con `|`:
  `.brain agua | zumo | café | café a las 3`.

Solo se aceptan adjuntos de Discord y avatares, nunca URLs arbitrarias: así
el bot no puede usarse para hacer peticiones a direcciones elegidas por el
usuario. El procesado (CPU, y `ffmpeg` en los vídeos) se ejecuta fuera del
event loop, con un límite de trabajos simultáneos y un enfriamiento por
usuario compartido por todos los comandos de imagen.

Requisitos: permiso **Adjuntar archivos** en el canal y el intent de
contenido de mensajes (como el resto de comandos de texto).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import discord
from discord.ext import commands

from bot.services.image_input import MAX_INPUT_BYTES, ImageTooLargeError, InvalidImageError
from bot.services.magik import apply_magik
from bot.services.memes import EFFECTS, Effect, MemeInputError, build_request, render
from bot.utils.responder import CommandResponder, ContextResponder

logger = logging.getLogger(__name__)

# Segundos mínimos entre dos usos de un mismo usuario.
COOLDOWN_SECONDS = 5

# Trabajos de procesado simultáneos en todo el bot; el resto espera turno.
MAX_CONCURRENT_JOBS = 2

# Tamaño con el que se pide el avatar: de sobra para cualquier plantilla.
AVATAR_SIZE = 512

# Fuente de la imagen: un adjunto o un avatar, ambos con `read()`.
ImageSource = discord.Attachment | discord.Asset

# Lo que hace un comando con los bytes descargados: devuelve el archivo y su extensión.
Work = Callable[[list[bytes]], tuple[bytes, str]]

_MENTION = re.compile(r"<@!?(\d+)>")

# Títulos de cada grupo en `.memes`, por `Effect.kind`.
KIND_TITLES: dict[str, str] = {
    "avatar": "🖼️ Con avatar",
    "mixed": "💬 Avatar + texto",
    "text": "📝 Solo texto",
    "video": "🎬 Vídeo",
}


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    """Indica si Discord declara el adjunto como imagen."""
    return bool(attachment.content_type and attachment.content_type.startswith("image/"))


def _first_image(message: discord.Message | None) -> discord.Attachment | None:
    """Primer adjunto de imagen de un mensaje, o `None`."""
    if message is None:
        return None
    return next((a for a in message.attachments if _is_image_attachment(a)), None)


def _avatar_of(user: discord.abc.User) -> discord.Asset:
    """Avatar como imagen estática PNG (el primer fotograma si es animado)."""
    return user.display_avatar.replace(size=AVATAR_SIZE, format="png")


def _replied_message(ctx: commands.Context) -> discord.Message | None:
    """Mensaje al que responde el comando, si Discord lo ha resuelto."""
    reference = ctx.message.reference
    resolved = reference.resolved if reference else None
    return resolved if isinstance(resolved, discord.Message) else None


def parse_arguments(
    raw: str, *, split_texts: bool, names: Mapping[int, str] | None = None
) -> tuple[list[int], list[str]]:
    """Separa las menciones del texto de un comando de efecto.

    Args:
        raw: Lo que el usuario escribió tras el nombre del comando.
        split_texts: Si el efecto admite varios textos, se separan por `|`;
            si solo admite uno, el `|` forma parte del texto.
        names: Si se da, cada mención se sustituye en el texto por el nombre
            de ese usuario (en los efectos sin avatar, `@Ana` debe leerse
            «Ana»). Si no, las menciones se quitan del texto.

    Returns:
        IDs mencionados en orden de aparición (sin repetir) y los textos.
    """
    mentioned: list[int] = []
    for match in _MENTION.finditer(raw):
        user_id = int(match.group(1))
        if user_id not in mentioned:
            mentioned.append(user_id)

    def replace(match: re.Match[str]) -> str:
        return (names or {}).get(int(match.group(1)), " ")

    text = _MENTION.sub(replace, raw).strip()
    if not text:
        return mentioned, []
    parts = text.split("|") if split_texts else [text]
    return mentioned, [part.strip() for part in parts]


def usage(effect: Effect) -> str:
    """Forma de uso de un efecto, p. ej. `.slap @miembro` o `.boo <texto1> | <texto2>`."""
    parts = [f".{effect.name}"]
    if effect.avatars == 1:
        parts.append("[@miembro o imagen]")
    elif effect.avatars == 2:
        parts.append("@miembro")
    if effect.texts == 1:
        parts.append("<texto>")
    elif effect.texts > 1:
        parts.append(" | ".join(f"<texto{i}>" for i in range(1, effect.texts + 1)))
    if effect.optional_texts:
        parts.append("[| <texto abajo>]")
    return " ".join(parts)


def _usage_with_example(effect: Effect) -> str:
    example = f"  Ej: `.{effect.name} {effect.example}`" if effect.example else ""
    return f"Uso: `{usage(effect)}`{example}"


@dataclass(slots=True)
class EffectInputs:
    """Lo que se ha resuelto a partir del mensaje, antes de descargar nada.

    Attributes:
        sources: Imágenes a descargar, en el orden que espera el efecto.
        subject: Usuario protagonista: su nombre aparece en `tweet`, `quote`...
        texts: Textos escritos por el usuario.
    """

    sources: list[ImageSource]
    subject: discord.abc.User
    texts: list[str]


def resolve_inputs(
    effect: Effect,
    raw: str,
    *,
    author: discord.abc.User,
    message: discord.Message,
    replied: discord.Message | None,
) -> EffectInputs | str:
    """Decide qué imágenes y textos usa un efecto, o devuelve el error a mostrar.

    Reglas: los objetivos son los mencionados (o, si no hay, el autor del
    mensaje respondido). Con un avatar se usa la imagen adjunta, o el avatar
    del primer objetivo, o el del autor. Con dos, el primero es el autor y
    el segundo el objetivo, salvo que se mencione a dos personas.
    """
    by_id = {user.id: user for user in message.mentions}
    names = {user.id: user.display_name for user in message.mentions}
    mentioned_ids, texts = parse_arguments(
        raw,
        split_texts=effect.texts + effect.optional_texts > 1,
        names=names if effect.avatars == 0 else None,
    )
    targets: list[discord.abc.User] = [by_id[i] for i in mentioned_ids if i in by_id]
    if not targets and replied is not None:
        targets = [replied.author]

    attachment = _first_image(message) or _first_image(replied)
    subject = targets[0] if targets else author

    sources: list[ImageSource] = []
    if effect.avatars == 1:
        sources = [attachment or _avatar_of(subject)]
    elif effect.avatars == 2:
        if len(targets) >= 2:
            sources = [_avatar_of(targets[0]), attachment or _avatar_of(targets[1])]
        elif attachment is not None:
            sources = [_avatar_of(author), attachment]
        elif targets:
            sources = [_avatar_of(author), _avatar_of(targets[0])]
        else:
            return f"Menciona a alguien o responde a su mensaje. Uso: `{usage(effect)}`"

    if len(texts) < effect.texts:
        return f"Falta texto. {_usage_with_example(effect)}"
    if len(texts) > effect.texts + effect.optional_texts:
        return f"Sobran textos. {_usage_with_example(effect)}"
    return EffectInputs(sources=sources, subject=subject, texts=texts)


class Images(commands.Cog):
    """Comandos que transforman imágenes (solo de texto)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._jobs = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._last_use: dict[int, float] = {}

    async def cog_load(self) -> None:
        """Registra un comando de texto oculto por cada efecto de `EFFECTS`."""
        for effect in EFFECTS.values():
            self.bot.add_command(self._build_effect_command(effect))

    async def cog_unload(self) -> None:
        """Retira los comandos de efecto, que no forman parte de la clase del cog."""
        for name in EFFECTS:
            self.bot.remove_command(name)

    def _build_effect_command(self, effect: Effect) -> commands.Command:
        """Crea el comando `.<efecto>`; solo funciona dentro de un servidor."""

        async def callback(ctx: commands.Context, *, argumentos: str = "") -> None:
            await self.run_effect(ctx, effect, argumentos)

        command = commands.Command(callback, name=effect.name, help=effect.description, hidden=True)
        return commands.guild_only()(command)

    def _cooldown_remaining(self, user_id: int) -> float:
        """Segundos que faltan para que el usuario pueda volver a usar un comando de imagen."""
        elapsed = time.monotonic() - self._last_use.get(user_id, float("-inf"))
        return max(0.0, COOLDOWN_SECONDS - elapsed)

    async def _process(
        self,
        responder: CommandResponder,
        sources: list[ImageSource],
        work: Work,
        *,
        filename: str,
        progress: str,
        usage_hint: str = "",
    ) -> None:
        """Valida, descarga, procesa en un hilo y envía el resultado.

        Cualquier error se comunica al usuario y el aviso de progreso nunca se
        queda colgado. Los errores imprevistos se registran con su traza.
        """
        member = responder.member
        if responder.guild is None or member is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        for source in sources:
            if isinstance(source, discord.Attachment):
                if not _is_image_attachment(source):
                    await responder.send_error("Ese archivo no es una imagen.")
                    return
                if source.size > MAX_INPUT_BYTES:
                    limit_mb = MAX_INPUT_BYTES // (1024 * 1024)
                    await responder.send_error(
                        f"La imagen es demasiado grande (máximo {limit_mb} MB)."
                    )
                    return

        remaining = self._cooldown_remaining(member.id)
        if remaining > 0:
            await responder.send_error(
                f"Espera {remaining:.0f} s antes de volver a usar un comando de imagen."
            )
            return
        self._last_use[member.id] = time.monotonic()

        await responder.start_progress(progress)

        try:
            data = [await source.read() for source in sources]
        except (discord.HTTPException, discord.NotFound):
            logger.warning("No se pudo descargar una imagen para %s", filename, exc_info=True)
            await responder.finish("No se pudo descargar la imagen. Inténtalo de nuevo.")
            return

        try:
            async with self._jobs:
                content, extension = await asyncio.to_thread(work, data)
        except ImageTooLargeError:
            await responder.finish("La imagen es demasiado grande para procesarla.")
            return
        except InvalidImageError:
            await responder.finish("No pude leer esa imagen. Prueba con un PNG o JPEG.")
            return
        except MemeInputError as error:
            hint = f" Uso: `{usage_hint}`" if usage_hint else ""
            await responder.finish(f"{error}{hint}")
            return
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg superó el tiempo máximo en %s", filename)
            await responder.finish("El vídeo tardó demasiado en generarse. Inténtalo más tarde.")
            return
        except Exception:
            # Cualquier fallo imprevisto del procesado: se registra y se avisa,
            # para que el aviso de progreso nunca se quede colgado.
            logger.exception("Error inesperado al generar %s", filename)
            await responder.finish("Algo salió mal al generar la imagen.")
            return

        try:
            await responder.finish(
                file=discord.File(io.BytesIO(content), filename=f"{filename}.{extension}")
            )
        except discord.Forbidden:
            logger.warning("Sin permiso para adjuntar archivos en el canal", exc_info=True)
            await responder.finish(
                "No tengo permiso para adjuntar archivos en este canal. "
                "Actívame **Adjuntar archivos** y vuelve a probar."
            )
        except discord.HTTPException:
            logger.warning("No se pudo enviar el resultado de %s", filename, exc_info=True)
            await responder.finish("No pude enviar la imagen. Inténtalo de nuevo.")

    async def _magik_impl(self, responder: CommandResponder, source: ImageSource) -> None:
        """Deforma con seam carving la imagen o el avatar indicado."""
        await self._process(
            responder,
            [source],
            lambda data: (apply_magik(data[0]), "png"),
            filename="magik",
            progress="🌀 Distorsionando...",
        )

    @commands.command(name="magik")
    @commands.guild_only()
    async def magik_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Deforma una imagen o un avatar.

        Usa el adjunto del mensaje, o el del mensaje al que se responde; si no
        hay, el avatar del miembro indicado o el tuyo.
        """
        source = (
            _first_image(ctx.message)
            or _first_image(_replied_message(ctx))
            or _avatar_of(miembro or ctx.author)
        )
        await self._magik_impl(ContextResponder(ctx), source)

    async def run_effect(self, ctx: commands.Context, effect: Effect, raw: str) -> None:
        """Ejecuta un efecto a partir de un mensaje `.efecto ...`."""
        responder = ContextResponder(ctx)
        inputs = resolve_inputs(
            effect, raw, author=ctx.author, message=ctx.message, replied=_replied_message(ctx)
        )
        if isinstance(inputs, str):
            await responder.send_error(inputs)
            return

        names = [inputs.subject.display_name, inputs.subject.name]

        def work(data: list[bytes]) -> tuple[bytes, str]:
            result = render(effect, build_request(effect, data, inputs.texts, names))
            return result.data, result.extension

        progress = "🎬 Generando vídeo..." if effect.output == "mp4" else "🎨 Generando..."
        await self._process(
            responder,
            inputs.sources,
            work,
            filename=effect.name,
            progress=progress,
            usage_hint=usage(effect),
        )

    @commands.command(name="memes")
    async def memes_text(self, ctx: commands.Context, efecto: str | None = None) -> None:
        """Lista los efectos de imagen, o explica cómo se usa uno."""
        await ctx.send(embed=build_memes_embed(efecto))


def build_memes_embed(name: str | None = None) -> discord.Embed:
    """Embed con todos los efectos agrupados, o con el uso de uno concreto."""
    color = discord.Color.blurple()
    if name is not None:
        effect = EFFECTS.get(name.lower().lstrip("."))
        if effect is None:
            return discord.Embed(
                description=f"No existe el efecto `{name}`. Escribe `.memes` para verlos todos.",
                color=color,
            )
        embed = discord.Embed(title=f".{effect.name}", description=effect.description, color=color)
        embed.add_field(name="Uso", value=f"`{usage(effect)}`", inline=False)
        if effect.example:
            embed.add_field(name="Ejemplo", value=f"`.{effect.name} {effect.example}`")
        return embed

    embed = discord.Embed(
        title=f"🎨 {len(EFFECTS)} efectos de imagen",
        description=(
            "`.trigger @alguien` · `.changemymind texto` · `.brain a | b | c | d`\n"
            "Mencionar o responder a alguien usa su avatar; una imagen adjunta lo sustituye.\n"
            "`.memes efecto` explica uno."
        ),
        color=color,
    )
    for kind, title in KIND_TITLES.items():
        names = sorted(e.name for e in EFFECTS.values() if e.kind == kind)
        if names:
            embed.add_field(name=f"{title} ({len(names)})", value=" · ".join(names), inline=False)
    return embed


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de imágenes en el cliente."""
    await bot.add_cog(Images(bot))
