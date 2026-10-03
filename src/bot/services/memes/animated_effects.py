"""Efectos animados (GIF) y filtros que transforman el avatar entero.

Los GIF de `imgen` se reproducen tal cual. Tres filtros dependían de programas
o modelos externos y aquí se reimplementan con Pillow y numpy, para no
añadir dependencias al contenedor del NAS:

- `radialblur`: el original usaba `-rotational-blur 15` de ImageMagick; aquí
  se promedian copias giradas entre -7,5° y +7,5°, que es lo que hace ese filtro.
- `warp`: el original encadenaba `-implode`, `-roll` y `-swirl` de
  GraphicsMagick; aquí se aplican las mismas tres deformaciones remapeando
  coordenadas con numpy.
- `dream`: el original ejecutaba *DeepDream* con TensorFlow (cientos de MB
  de dependencias y RAM). Aquí es una imitación barata: se amplifican los
  detalles a varias escalas, se rota el tono según la luminosidad y se
  satura, lo que da el aspecto psicodélico sin red neuronal.
"""

from __future__ import annotations

import random

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from bot.services.memes.registry import MemeRequest, MemeResult, effect, encode_gif
from bot.services.memes.toolkit import asset, frames, paste

# --- GIF animados ------------------------------------------------------------


@effect("airpods", "Bailando con AirPods.", avatars=1, output="gif")
def airpods(request: MemeRequest) -> MemeResult:
    avatar = request.avatars[0].convert("RGBA").resize((128, 128))
    blank = Image.new("RGBA", (400, 128), (255, 255, 255, 0))
    out = []
    for left, right in zip(frames("airpods/left"), frames("airpods/right"), strict=False):
        frame = blank.copy()
        paste(frame, left, (0, 0))
        paste(frame, avatar, (136, 0))
        paste(frame, right, (272, 0))
        out.append(frame)
    return encode_gif(out, disposal=2, duration=30, transparency=0)


def _flag(stem: str, request: MemeRequest, size: int, alpha: int, duration: int) -> MemeResult:
    """Bandera animada con el avatar semitransparente encima, como `america` y `communism`."""
    avatar = request.avatars[0].convert("RGBA").resize((size, size))
    avatar.putalpha(alpha)
    out = []
    for frame in frames(stem):
        frame = frame.resize((size, size))
        paste(frame, avatar, (0, 0))
        out.append(frame.resize((256, 256)))
    return encode_gif(out, disposal=2, duration=duration)


@effect("america", "Ondeando la bandera de EE. UU.", avatars=1, output="gif")
def america(request: MemeRequest) -> MemeResult:
    return _flag("america/america", request, 480, 128, 30)


@effect("communism", "Ondeando la bandera comunista.", avatars=1, output="gif")
def communism(request: MemeRequest) -> MemeResult:
    return _flag("communism/communism", request, 300, 96, 40)


@effect("dank", "MLG: cuernos, Doritos y hitmarkers.", avatars=1, output="gif")
def dank(request: MemeRequest) -> MemeResult:
    avatar = request.avatars[0].resize((320, 320)).convert("RGBA")
    horn = asset("dank/horn").convert("RGBA").resize((100, 100))
    horn = horn.rotate(315, resample=Image.Resampling.BICUBIC)
    horn2 = ImageOps.mirror(
        horn.copy().resize((130, 130)).rotate(350, resample=Image.Resampling.BICUBIC)
    )
    hit = asset("dank/hit").convert("RGBA").resize((40, 40))
    gun = asset("dank/gun").convert("RGBA").resize((250, 205))
    faze = asset("dank/faze").convert("RGBA").resize((60, 40))
    blank = Image.new("RGBA", (256, 256), color=(254, 0, 0))
    paste(blank, avatar, (-20, -20))
    out = []
    for index in range(8):
        frame = blank.copy()
        # El primer fotograma va quieto; el resto tiembla al azar.
        if index == 0:
            positions = [(175, 0), (-60, 0), (90, 65), (5, 212)]
        else:
            positions = [
                (165 + random.randint(-8, 8), random.randint(0, 12)),
                (-50 + random.randint(-6, 6), random.randint(-2, 10)),
                (110 + random.randint(-30, 30), 55 + random.randint(-30, 30)),
                (12 + random.randint(-6, 6), 210 + random.randint(-2, 10)),
            ]
        paste(frame, horn, positions[0])
        paste(frame, horn2, positions[1])
        paste(frame, hit, positions[2])
        paste(frame, gun, (120, 130))
        paste(frame, faze, positions[3])
        out.append(frame)
    return encode_gif(out, duration=20)


@effect("salty", "Echándole sal.", avatars=1, output="gif")
def salty(request: MemeRequest) -> MemeResult:
    avatar = request.avatars[0].convert("RGBA").resize((256, 256))
    salt = asset("salty/salt").convert("RGBA").resize((256, 256))
    salt = salt.rotate(-130, resample=Image.Resampling.BICUBIC)
    blank = Image.new("RGBA", (256, 256))
    paste(blank, avatar, (0, 0))
    out = []
    for index in range(8):
        frame = blank.copy()
        if index == 0:
            paste(frame, salt, (-125, -125))
        else:
            paste(frame, salt, (-135 + random.randint(-5, 5), -135 + random.randint(-5, 5)))
        out.append(frame)
    return encode_gif(out, duration=20)


@effect("trigger", "TRIGGERED: temblando en rojo.", avatars=1, output="gif")
def trigger(request: MemeRequest) -> MemeResult:
    avatar = request.avatars[0].resize((320, 320)).convert("RGBA")
    banner = asset("triggered/triggered")
    tint = asset("triggered/red").convert("RGBA")
    blank = Image.new("RGBA", (256, 256), color=(231, 19, 29))
    out = []
    for index in range(8):
        frame = blank.copy()
        if index == 0:
            paste(frame, avatar, (-16, -16))
        else:
            paste(frame, avatar, (-32 + random.randint(-16, 16), -32 + random.randint(-16, 16)))
        paste(frame, tint, (0, 0))
        if index == 0:
            frame.paste(banner, (-10, 200))
        else:
            frame.paste(banner, (-12 + random.randint(-8, 8), 200 + random.randint(0, 12)))
        out.append(frame)
    return encode_gif(out, duration=20, disposal=2)


# --- Filtros -----------------------------------------------------------------


@effect("deepfry", "Frito: emojis, ruido y contraste a tope.", avatars=1)
def deepfry(request: MemeRequest) -> Image.Image:
    avatar = request.avatars[0].resize((400, 400)).convert("RGBA")
    joy, hand, hundred, fire = (
        asset(f"deepfry/{name}").resize((100, 100)).rotate(random.randint(-30, 30)).convert("RGBA")
        for name in ("joy", "ok-hand", "100", "fire")
    )
    paste(avatar, joy, (random.randint(20, 75), random.randint(20, 45)))
    paste(avatar, hand, (random.randint(20, 75), random.randint(150, 300)))
    paste(avatar, hundred, (random.randint(150, 300), random.randint(20, 45)))
    paste(avatar, fire, (random.randint(150, 300), random.randint(150, 300)))

    # Ruido uniforme de ±12 por píxel (el mismo valor en los tres canales),
    # vectorizado: el bucle píxel a píxel del original tarda segundos.
    pixels = np.asarray(avatar.convert("RGB"), dtype=np.int16)
    noise = np.random.randint(0, 26, size=pixels.shape[:2], dtype=np.int16) - 12
    noisy = Image.fromarray(np.clip(pixels + noise[..., None], 0, 255).astype(np.uint8))

    noisy = ImageEnhance.Contrast(noisy).enhance(random.randint(5, 20))
    noisy = ImageEnhance.Sharpness(noisy).enhance(17.5)
    return ImageEnhance.Color(noisy).enhance(random.randint(-15, 15))


def _working_copy(request: MemeRequest, max_side: int = 512) -> Image.Image:
    """Avatar en RGBA y reducido, para que los filtros pesados tarden poco."""
    image = request.avatars[0].convert("RGBA")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return image


@effect("radialblur", "Desenfoque giratorio.", avatars=1)
def radialblur(request: MemeRequest) -> Image.Image:
    image = _working_copy(request)
    color = np.zeros((image.height, image.width, 3), dtype=np.float32)
    weight = np.zeros((image.height, image.width, 1), dtype=np.float32)
    for angle in np.linspace(-7.5, 7.5, 15):
        rotated = np.asarray(
            image.rotate(float(angle), resample=Image.Resampling.BILINEAR), dtype=np.float32
        )
        # Media ponderada por la opacidad: las esquinas que el giro deja
        # vacías no oscurecen el resultado.
        alpha = rotated[..., 3:] / 255
        color += rotated[..., :3] * alpha
        weight += alpha
    blurred = color / np.maximum(weight, 1e-6)
    original_alpha = np.asarray(image, dtype=np.uint8)[..., 3:]
    pixels = np.concatenate((np.clip(blurred, 0, 255).astype(np.uint8), original_alpha), axis=2)
    return Image.fromarray(pixels, mode="RGBA")


def _remap(pixels: np.ndarray, source_x: np.ndarray, source_y: np.ndarray) -> np.ndarray:
    """Lee cada píxel de salida de la posición de origen indicada (vecino más cercano)."""
    height, width = pixels.shape[:2]
    xs = np.clip(np.rint(source_x), 0, width - 1).astype(np.intp)
    ys = np.clip(np.rint(source_y), 0, height - 1).astype(np.intp)
    return pixels[ys, xs]


@effect("warp", "Retorcido: implosión, desplazamiento y remolino.", avatars=1)
def warp(request: MemeRequest) -> Image.Image:
    image = _working_copy(request)
    pixels = np.asarray(image)
    height, width = pixels.shape[:2]

    # -roll: desplaza la imagen en círculo, como si fuera un toro.
    pixels = np.roll(pixels, (random.randint(0, 256), random.randint(0, 256)), axis=(0, 1))

    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    cx, cy = (width - 1) / 2, (height - 1) / 2
    radius = min(cx, cy)
    dx, dy = xs - cx, ys - cy
    distance = np.hypot(dx, dy) / radius
    inside = distance < 1

    # -implode con factor negativo (explosión): dentro del círculo se toma el
    # píxel de un radio menor, lo que hincha el centro.
    amount = random.randint(3, 15) / 10
    factor = np.where(inside, np.power(np.maximum(distance, 1e-6), amount), 1.0)
    dx, dy = dx * factor, dy * factor

    # -swirl: gira más cuanto más cerca del centro, hasta ±120–180°.
    degrees = random.choice((-1, 1)) * random.randint(120, 180)
    angle = np.where(inside, np.radians(degrees) * (1 - distance) ** 2, 0.0)
    cos, sin = np.cos(angle), np.sin(angle)
    source_x = cx + dx * cos - dy * sin
    source_y = cy + dx * sin + dy * cos
    return Image.fromarray(_remap(pixels, source_x, source_y), mode="RGBA")


@effect("dream", "Sueño psicodélico (imitación ligera de DeepDream).", avatars=1, output="jpeg")
def dream(request: MemeRequest) -> Image.Image:
    image = request.avatars[0].convert("RGB")
    image.thumbnail((600, 600), Image.Resampling.LANCZOS)
    pixels = np.asarray(image, dtype=np.float32)

    # Detalles amplificados a varias escalas: la diferencia entre la imagen y
    # su versión desenfocada son los bordes y texturas de ese tamaño.
    for radius, gain in ((1, 1.5), (3, 1.2), (8, 0.8)):
        blurred = np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
        pixels = pixels + gain * (pixels - blurred)
        image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))

    # Rotación de tono dependiente de la luminosidad: bandas de color
    # iridiscentes siguiendo las formas.
    hsv = np.asarray(image.convert("HSV"), dtype=np.int16)
    luminance = np.asarray(image.convert("L"), dtype=np.int16)
    hsv[..., 0] = (hsv[..., 0] + luminance * 3) % 256
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.6 + 40, 0, 255)
    image = Image.fromarray(hsv.astype(np.uint8), mode="HSV").convert("RGB")
    image = ImageEnhance.Contrast(image).enhance(1.3)
    return image.filter(ImageFilter.DETAIL)
