"""Registro de efectos de imagen: qué entradas pide cada uno y cómo se ejecuta.

Cada efecto es una función pura que recibe un :class:`MemeRequest` (avatares
ya decodificados, textos y nombres) y devuelve una imagen de Pillow o un
:class:`MemeResult` ya codificado. El decorador :func:`effect` la registra con
sus requisitos, de modo que el cog genera los comandos y la ayuda leyendo el
registro en vez de mantener listas a mano.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image

# Formatos de salida admitidos y la extensión del archivo que se envía.
OUTPUT_FORMATS: dict[str, str] = {"png": "png", "jpeg": "jpg", "gif": "gif", "mp4": "mp4"}


class MemeInputError(Exception):
    """Los datos aportados no sirven para el efecto (p. ej. texto que no cabe).

    El mensaje está en español y se puede mostrar tal cual al usuario.
    """


@dataclass(frozen=True, slots=True)
class MemeRequest:
    """Entradas de un efecto, ya validadas por el cog.

    Attributes:
        avatars: Imágenes en RGBA, en el orden que pide el efecto (normalmente
            quien ejecuta el comando primero y su objetivo después).
        texts: Campos de texto ya limpios, tantos como pide el efecto.
        usernames: Nombre visible y nombre de usuario del protagonista.
    """

    avatars: list[Image.Image] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    usernames: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Primer texto, o cadena vacía: atajo para los efectos de un solo campo."""
        return self.texts[0] if self.texts else ""


@dataclass(frozen=True, slots=True)
class MemeResult:
    """Archivo generado, listo para enviarse a Discord."""

    data: bytes
    extension: str


@dataclass(frozen=True, slots=True)
class Effect:
    """Un efecto registrado y lo que necesita para ejecutarse.

    Attributes:
        name: Nombre del comando (el mismo que en Dank Memer).
        description: Frase corta en español para la ayuda.
        avatars: Número de imágenes que usa (0, 1 o 2).
        texts: Campos de texto obligatorios.
        optional_texts: Campos adicionales que pueden omitirse.
        example: Argumentos de ejemplo para la ayuda (sin el nombre del comando).
        output: Formato con el que se codifica si la función devuelve una imagen.
    """

    name: str
    description: str
    render: Callable[[MemeRequest], Image.Image | MemeResult]
    avatars: int = 0
    texts: int = 0
    optional_texts: int = 0
    example: str = ""
    output: str = "png"

    @property
    def kind(self) -> str:
        """Categoría para agrupar la ayuda: avatar, texto, mixto o vídeo."""
        if self.output == "mp4":
            return "video"
        if self.avatars and self.texts + self.optional_texts:
            return "mixed"
        return "avatar" if self.avatars else "text"


EFFECTS: dict[str, Effect] = {}


def effect(
    name: str,
    description: str,
    *,
    avatars: int = 0,
    texts: int = 0,
    optional_texts: int = 0,
    example: str = "",
    output: str = "png",
) -> Callable[[Callable[[MemeRequest], Image.Image | MemeResult]], Callable]:
    """Decorador que registra una función como efecto con sus requisitos.

    Raises:
        ValueError: Si el nombre ya está registrado o el formato no existe.
    """
    if output not in OUTPUT_FORMATS:
        raise ValueError(f"Formato de salida desconocido: {output}")

    def register(function: Callable[[MemeRequest], Image.Image | MemeResult]) -> Callable:
        if name in EFFECTS:
            raise ValueError(f"Efecto duplicado: {name}")
        # La descripción hace de docstring: así cada efecto queda documentado
        # una sola vez y la ayuda del bot muestra lo mismo que el código.
        function.__doc__ = function.__doc__ or description
        EFFECTS[name] = Effect(
            name=name,
            description=description,
            render=function,
            avatars=avatars,
            texts=texts,
            optional_texts=optional_texts,
            example=example,
            output=output,
        )
        return function

    return register


def encode(image: Image.Image, output: str) -> MemeResult:
    """Codifica una imagen estática en el formato indicado."""
    buffer = io.BytesIO()
    if output == "jpeg":
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
    else:
        # Sin `optimize`: en plantillas grandes tarda 6 veces más y apenas ahorra un 3 %.
        image.save(buffer, format="PNG")
    return MemeResult(buffer.getvalue(), OUTPUT_FORMATS[output])


def encode_gif(frames: list[Image.Image], **options: object) -> MemeResult:
    """Codifica fotogramas como GIF animado que se repite sin fin."""
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        optimize=True,
        **options,
    )
    return MemeResult(buffer.getvalue(), "gif")
