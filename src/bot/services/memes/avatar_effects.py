"""Efectos que colocan uno o dos avatares sobre una plantilla, sin texto.

Posiciones, tamaños y capas son los de `imgen` (Dank Memer). En los efectos de
dos avatares, el primero es quien ejecuta el comando y el segundo su
objetivo (p. ej. en `slap`, quien pega y quien recibe).
"""

from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps

from bot.services.memes.registry import MemeRequest, effect
from bot.services.memes.toolkit import asset, paste, skew


def _avatar(request: MemeRequest, index: int, size: tuple[int, int]) -> Image.Image:
    """Avatar `index` redimensionado y en RGBA."""
    return request.avatars[index].resize(size).convert("RGBA")


def _over(stem: str, request: MemeRequest, size: tuple[int, int], position: tuple[int, int]):
    """Plantilla encima del avatar: el avatar asoma por las zonas transparentes."""
    base = asset(stem).convert("RGBA")
    final = Image.new("RGBA", base.size)
    paste(final, _avatar(request, 0, size), position)
    paste(final, base, (0, 0))
    return final


def _under(stem: str, request: MemeRequest, size: tuple[int, int], position: tuple[int, int]):
    """Avatar encima de la plantilla, en la posición indicada."""
    base = asset(stem).convert("RGBA")
    paste(base, _avatar(request, 0, size), position)
    return base


# --- Un avatar pegado sobre la plantilla -----------------------------------


@effect("aborted", "El médico te explica por qué es un aborto.", avatars=1, output="jpeg")
def aborted(request: MemeRequest) -> Image.Image:
    return _under("aborted/aborted", request, (90, 90), (390, 130))


@effect("affect", "«¿Esto afectará a mi bebé?»", avatars=1, output="jpeg")
def affect(request: MemeRequest) -> Image.Image:
    return _under("affect/affect", request, (200, 157), (180, 383))


@effect("cancer", "Cáncer de piel.", avatars=1)
def cancer(request: MemeRequest) -> Image.Image:
    return _under("cancer/cancer", request, (100, 100), (351, 200))


@effect("delete", "Borrar esta basura.", avatars=1)
def delete(request: MemeRequest) -> Image.Image:
    return _under("delete/delete", request, (195, 195), (120, 135))


@effect("disability", "Una discapacidad que no se ve.", avatars=1)
def disability(request: MemeRequest) -> Image.Image:
    return _under("disability/disability", request, (175, 175), (450, 325))


@effect("egg", "Dentro de un huevo.", avatars=1)
def egg(request: MemeRequest) -> Image.Image:
    base = asset("egg/egg").resize((350, 350)).convert("RGBA")
    paste(base, _avatar(request, 0, (50, 50)), (143, 188))
    return base


@effect("failure", "La lección de hoy: el fracaso.", avatars=1)
def failure(request: MemeRequest) -> Image.Image:
    return _under("failure/failure", request, (215, 215), (143, 525))


@effect("hitler", "Peor que Hitler.", avatars=1)
def hitler(request: MemeRequest) -> Image.Image:
    return _under("hitler/hitler", request, (140, 140), (46, 43)).convert("RGB")


@effect("rip", "Lápida con tu cara.", avatars=1)
def rip(request: MemeRequest) -> Image.Image:
    base = asset("rip/rip").convert("RGBA").resize((642, 806))
    paste(base, _avatar(request, 0, (300, 300)), (175, 385))
    return base


@effect("roblox", "Personaje de Roblox.", avatars=1)
def roblox(request: MemeRequest) -> Image.Image:
    return _under("roblox/roblox", request, (56, 74), (168, 41))


@effect("sickfilth", "Baneado por asqueroso.", avatars=1)
def sickfilth(request: MemeRequest) -> Image.Image:
    return _under("ban/ban", request, (400, 400), (70, 344))


@effect("ugly", "Demasiado feo.", avatars=1)
def ugly(request: MemeRequest) -> Image.Image:
    return _under("ugly/ugly", request, (175, 175), (120, 55))


@effect("wanted", "Cartel de se busca.", avatars=1)
def wanted(request: MemeRequest) -> Image.Image:
    return _under("wanted/wanted", request, (447, 447), (145, 282))


@effect("whodidthis", "¿Quién ha hecho esto?", avatars=1)
def whodidthis(request: MemeRequest) -> Image.Image:
    return _under("whodidthis/whodidthis", request, (720, 405), (0, 159))


@effect("trash", "A la basura (borroso).", avatars=1)
def trash(request: MemeRequest) -> Image.Image:
    base = asset("trash/trash").convert("RGBA")
    blurred = _avatar(request, 0, (483, 483)).filter(ImageFilter.GaussianBlur(radius=6))
    paste(base, blurred, (480, 0))
    return base


# --- Plantilla encima del avatar --------------------------------------------


@effect("dab", "Haciendo el dab.", avatars=1)
def dab(request: MemeRequest) -> Image.Image:
    return _over("dab/dab", request, (500, 500), (300, 0))


@effect("door", "Asomado por la puerta.", avatars=1)
def door(request: MemeRequest) -> Image.Image:
    return _over("door/door", request, (479, 479), (250, 0))


@effect("fakenews", "Noticias falsas.", avatars=1)
def fakenews(request: MemeRequest) -> Image.Image:
    return _over("fakenews/fakenews", request, (400, 400), (390, 0))


@effect("fedora", "M'lady, con sombrero fedora.", avatars=1)
def fedora(request: MemeRequest) -> Image.Image:
    return _over("fedora/fedora", request, (275, 275), (112, 101))


@effect("laid", "En la cama, sonriendo.", avatars=1)
def laid(request: MemeRequest) -> Image.Image:
    return _over("laid/laid", request, (115, 115), (512, 360))


@effect("satan", "Satán en persona.", avatars=1)
def satan(request: MemeRequest) -> Image.Image:
    return _over("satan/satan", request, (195, 195), (200, 90))


@effect("bongocat", "Bongo Cat tocando encima.", avatars=1)
def bongocat(request: MemeRequest) -> Image.Image:
    avatar = _avatar(request, 0, (750, 750))
    paste(avatar, asset("bongocat/bongocat").convert("RGBA"), (0, 0))
    return avatar


@effect("brazzers", "Logo de Brazzers en la esquina.", avatars=1)
def brazzers(request: MemeRequest) -> Image.Image:
    avatar = request.avatars[0].convert("RGBA")
    logo = asset("brazzers/brazzers")
    # Igual que el original: el logo se escala según la proporción del avatar
    # y acaba ocupando media anchura de su tamaño natural.
    aspect = avatar.width / avatar.height
    new_width, new_height = int(logo.width * aspect), int(logo.height * aspect)
    scale = new_width / avatar.width
    logo = logo.resize((int(new_width / scale / 2), int(new_height / scale / 2))).convert("RGBA")
    paste(avatar, logo, (avatar.width - logo.width, avatar.height - logo.height))
    return avatar


@effect("gay", "Bandera arcoíris por encima.", avatars=1)
def gay(request: MemeRequest) -> Image.Image:
    avatar = request.avatars[0].convert("RGBA")
    flag = asset("gay/gay").convert("RGBA").resize(avatar.size)
    flag.putalpha(128)
    paste(avatar, flag, (0, 0))
    return avatar.convert("RGB")


@effect("jail", "Entre rejas y en blanco y negro.", avatars=1)
def jail(request: MemeRequest) -> Image.Image:
    bars = asset("jail/jail").resize((350, 350))
    base = request.avatars[0].convert("LA").resize((350, 350))
    base.paste(bars, (0, 0), bars.convert("RGBA"))
    return base.convert("RGBA")


@effect("invert", "Colores invertidos.", avatars=1)
def invert(request: MemeRequest) -> Image.Image:
    image = request.avatars[0].convert("RGBA")
    red, green, blue, alpha = image.split()
    inverted = ImageOps.invert(Image.merge("RGB", (red, green, blue)))
    return Image.merge("RGBA", (*inverted.split(), alpha))


# --- Avatar proyectado en perspectiva ---------------------------------------


@effect("goggles", "Lo que ve a través de las gafas.", avatars=1, output="jpeg")
def goggles(request: MemeRequest) -> Image.Image:
    base = asset("goggles/goggles").convert("RGBA")
    projected = skew(
        request.avatars[0].convert("RGBA"), [(32, 297), (171, 295), (180, 456), (41, 463)]
    )
    paste(base, projected, (0, 0))
    return base.resize((base.width, int(base.height / 1.5)), Image.Resampling.LANCZOS)


@effect("ipad", "En la pantalla de un iPad.", avatars=1)
def ipad(request: MemeRequest) -> Image.Image:
    canvas = Image.new("RGBA", (2048, 1364), (0, 0, 0, 0))
    screen = _avatar(request, 0, (512, 512))
    paste(canvas, skew(screen, [(476, 484), (781, 379), (956, 807), (668, 943)]), (0, 0))
    paste(canvas, asset("ipad/ipad").convert("RGBA"), (0, 0))
    return canvas.resize((512, 341), Image.Resampling.LANCZOS)


@effect("kimborder", "Kim Jong-un mirando tu foto.", avatars=1)
def kimborder(request: MemeRequest) -> Image.Image:
    base = asset("kimborder/kimborder").convert("RGBA")
    canvas = Image.new("RGBA", base.size, (0, 0, 0, 0))
    photo = request.avatars[0].convert("RGBA")
    paste(canvas, skew(photo, [(0, 402), (476, 413), (444, 638), (0, 638)]), (0, 0))
    paste(canvas, base, (0, 0))
    return canvas


# --- Dos avatares: quien ejecuta y su objetivo ------------------------------


@effect("bed", "El monstruo debajo de la cama.", avatars=2)
def bed(request: MemeRequest) -> Image.Image:
    base = asset("bed/bed").convert("RGBA")
    first = _avatar(request, 0, (100, 100))
    small = first.resize((70, 70))
    paste(base, first, (25, 100))
    paste(base, first, (25, 300))
    paste(base, small, (53, 450))
    paste(base, _avatar(request, 1, (70, 70)), (53, 575))
    return base


@effect("corporate", "«Encuentra las diferencias» corporativo.", avatars=2)
def corporate(request: MemeRequest) -> Image.Image:
    base = asset("corporate/corporate").convert("RGBA")
    first = request.avatars[0].convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS)
    second = request.avatars[1].convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS)
    paste(base, skew(first, [(208, 44), (718, 84), (548, 538), (20, 446)]), (0, 0))
    paste(
        base,
        skew(second, [(858, 112), (1600, 206), (1312, 666), (634, 546)], resolution=1400),
        (0, 0),
    )
    return base.resize((base.width // 2, base.height // 2))


@effect("madethis", "«Yo hice esto».", avatars=2)
def madethis(request: MemeRequest) -> Image.Image:
    base = asset("madethis/madethis").convert("RGBA")
    second = _avatar(request, 1, (111, 111))
    paste(base, _avatar(request, 0, (130, 130)), (92, 271))
    for position in ((422, 267), (406, 678), (412, 1121)):
        paste(base, second, position)
    return base


@effect("screams", "Gritándose el uno al otro.", avatars=2)
def screams(request: MemeRequest) -> Image.Image:
    base = asset("screams/screams").convert("RGBA")
    paste(base, _avatar(request, 0, (175, 175)), (200, 1))
    paste(base, _avatar(request, 1, (156, 156)), (136, 231))
    return base


@effect("slap", "Batman abofeteando a Robin.", avatars=2)
def slap(request: MemeRequest) -> Image.Image:
    base = asset("batslap/batslap").resize((1000, 500)).convert("RGBA")
    paste(base, _avatar(request, 1, (220, 220)), (580, 260))
    paste(base, _avatar(request, 0, (200, 200)), (350, 70))
    return base.convert("RGB")


@effect("spank", "Azotes.", avatars=2)
def spank(request: MemeRequest) -> Image.Image:
    base = asset("spank/spank").resize((500, 500)).convert("RGBA")
    paste(base, _avatar(request, 0, (140, 140)), (225, 5))
    paste(base, _avatar(request, 1, (120, 120)), (350, 220))
    return base
