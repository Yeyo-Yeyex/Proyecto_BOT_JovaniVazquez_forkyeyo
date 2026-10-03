"""Efectos de imagen al estilo Dank Memer: plantillas con avatares y textos.

Port de `imgen`, el generador de imágenes de Dank Memer (licencia MIT,
https://github.com/DankMemer/imgen), sin su servidor web ni sus bases de
datos: cada efecto es una función pura que el cog ejecuta en un hilo.

Uso típico desde el cog::

    request = build_request(effect, [avatar_bytes], ["texto"], ["Nombre", "usuario"])
    result = render(effect, request)   # bloqueante: dentro de asyncio.to_thread

Las plantillas viven en `bot/assets/memes`. Las diferencias con el original
(emojis, filtros reimplementados, `dream` sin TensorFlow) están explicadas
en `toolkit.py` y `animated_effects.py`.
"""

from __future__ import annotations

from PIL import Image

from bot.services.image_input import open_image

# Importar los módulos registra sus efectos en EFFECTS.
from bot.services.memes import (  # noqa: F401
    animated_effects,
    avatar_effects,
    mixed_effects,
    text_effects,
    video_effects,
)
from bot.services.memes.registry import (
    EFFECTS,
    Effect,
    MemeInputError,
    MemeRequest,
    MemeResult,
    encode,
)
from bot.services.memes.toolkit import clean_text

__all__ = [
    "EFFECTS",
    "MAX_TEXT_LENGTH",
    "Effect",
    "MemeInputError",
    "MemeRequest",
    "MemeResult",
    "build_request",
    "render",
]

# Caracteres máximos por campo de texto: de sobra para cualquier plantilla
# y evita que alguien pida dibujar un libro entero.
MAX_TEXT_LENGTH = 300

# Lado máximo con el que se trabaja una imagen de usuario. Los avatares de
# Discord llegan a 512 px; una foto de móvil se reduce para que efectos que
# usan la imagen a tamaño natural (`meme`, `gay`, `brazzers`...) no tarden.
INPUT_MAX_SIDE = 1024


def _load_avatar(data: bytes) -> Image.Image:
    image = open_image(data, draft_side=INPUT_MAX_SIDE // 2).convert("RGBA")
    image.thumbnail((INPUT_MAX_SIDE, INPUT_MAX_SIDE), Image.Resampling.LANCZOS)
    return image


def build_request(
    effect: Effect, avatars: list[bytes], texts: list[str], usernames: list[str]
) -> MemeRequest:
    """Valida y prepara las entradas de un efecto.

    Args:
        effect: Efecto que se va a ejecutar.
        avatars: Bytes de cada imagen, en el orden que espera el efecto.
        texts: Campos de texto tal como los escribió el usuario.
        usernames: `[nombre visible, nombre de usuario]` del protagonista.

    Raises:
        MemeInputError: Si faltan o sobran textos, o alguno es demasiado largo.
        ImageTooLargeError, InvalidImageError: Si una imagen no es válida.
    """
    cleaned = [clean_text(text) for text in texts]
    cleaned = [text for text in cleaned if text]
    if len(cleaned) < effect.texts:
        raise MemeInputError("Faltan textos.")
    if len(cleaned) > effect.texts + effect.optional_texts:
        raise MemeInputError("Sobran textos.")
    if any(len(text) > MAX_TEXT_LENGTH for text in cleaned):
        raise MemeInputError(f"Cada texto puede tener como mucho {MAX_TEXT_LENGTH} caracteres.")
    return MemeRequest(
        avatars=[_load_avatar(data) for data in avatars[: effect.avatars]],
        texts=cleaned,
        usernames=[clean_text(name) or "alguien" for name in usernames],
    )


def render(effect: Effect, request: MemeRequest) -> MemeResult:
    """Ejecuta el efecto y devuelve el archivo codificado (operación bloqueante, CPU)."""
    result = effect.render(request)
    if isinstance(result, Image.Image):
        return encode(result, effect.output)
    return result
