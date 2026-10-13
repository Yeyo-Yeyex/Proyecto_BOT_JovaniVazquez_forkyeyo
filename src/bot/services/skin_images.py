"""Imágenes propias de las skins: la foto que sube un miembro para su tapete.

Hay skins que no traen su dibujo, sino que lo pone su dueño
(`Skin.custom`, como el 🖼️ Tapete propio): una imagen por persona y skin, que
se guarda en la base de datos (`ShopRepository.set_skin_image`) y llega a la
escena dentro de `meta.skin.image` como un data URL. La pinta `tapete.js`
(dibujo `imagen`).

Aquí solo se limpia lo que sube el miembro, sin Discord ni base de datos. Un
adjunto es entrada no confiable, así que:

- solo se aceptan PNG, JPEG, WebP y GIF (del GIF, el primer fotograma), y se
  mira el formato real del archivo, no su nombre;
- se rechaza antes de descomprimir lo que pase de `MAX_UPLOAD_BYTES` o de
  `MAX_SOURCE_PIXELS` (una imagen pequeña en bytes puede ocupar gigas al
  abrirla: una «bomba de descompresión»);
- se gira según su EXIF, se reduce a `MAX_SIDE` píxeles por lado y se vuelve a
  codificar como JPEG. Así se tiran los metadatos (el EXIF puede llevar el GPS
  de la foto) y lo que se guarda pesa poco y se pinta igual en Node y en Chromium.

Es trabajo de CPU (~decenas de ms): se llama con `asyncio.to_thread`.
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

#: Lo más grande que se acepta subir (lo que deja Discord sin Nitro).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
#: Píxeles de la imagen original como máximo (40 megapíxeles, una foto de móvil sobrada).
MAX_SOURCE_PIXELS = 40_000_000
#: Lado mayor de lo que se guarda. Las mesas miden menos de 1.000 px de ancho.
MAX_SIDE = 960
JPEG_QUALITY = 85
#: Formatos que se aceptan, según Pillow (el formato real, no la extensión).
ACCEPTED_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "GIF"})
#: Fondo de las zonas transparentes: el verde del fieltro de serie.
TRANSPARENT_FILL = (27, 115, 69)


class SkinImageError(ValueError):
    """La imagen no vale; el mensaje se puede enseñar tal cual al miembro."""


@dataclass(frozen=True, slots=True)
class SkinImage:
    """Una imagen ya limpia, lista para guardar y pintar.

    Attributes:
        data: El JPEG.
        digest: Resumen corto del JPEG. Entra en la clave de la skin
            (`skins.params`), así cambiar de imagen invalida lo pintado antes del clic.
    """

    data: bytes
    digest: str

    @classmethod
    def of(cls, data: bytes) -> SkinImage:
        """La imagen de un JPEG ya limpio (lo que se lee de la base de datos)."""
        return cls(data, hashlib.sha256(data).hexdigest()[:16])

    @property
    def data_url(self) -> str:
        """El JPEG como data URL, que es como lo carga la escena."""
        return "data:image/jpeg;base64," + base64.b64encode(self.data).decode("ascii")


def prepare(raw: bytes) -> SkinImage:
    """Comprueba y limpia una imagen subida por un miembro.

    Raises:
        SkinImageError: Si no es una imagen, es de un formato que no se acepta o
            es demasiado grande.
    """
    if not raw:
        raise SkinImageError("El archivo está vacío.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise SkinImageError(
            f"Pesa demasiado: el máximo son {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ACCEPTED_FORMATS:
                raise SkinImageError("Solo valen imágenes PNG, JPG, WebP o GIF.")
            if image.width * image.height > MAX_SOURCE_PIXELS:
                raise SkinImageError("La imagen es enorme: bájale la resolución.")
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            flat = _flatten(image)
    except SkinImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise SkinImageError("Eso no es una imagen que yo sepa abrir.") from error
    buffer = io.BytesIO()
    flat.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return SkinImage.of(buffer.getvalue())


def _flatten(image: Image.Image) -> Image.Image:
    """La imagen en RGB, con lo transparente sobre el verde del fieltro."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, TRANSPARENT_FILL)
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")
