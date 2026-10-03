"""Efectos que combinan un avatar (o un nombre) con texto: tuits, citas, memes...

Las posiciones son las de `imgen` (Dank Memer). Los nombres llegan en
`MemeRequest.usernames` como `[nombre visible, nombre de usuario]` del
protagonista del efecto.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from PIL import Image, ImageDraw, ImageEnhance

from bot.services.memes.registry import MemeRequest, effect
from bot.services.memes.toolkit import (
    asset,
    circle_mask,
    draw_fitted_text,
    draw_text,
    font,
    paste,
    text_size,
    wrap,
)


def _name(request: MemeRequest) -> str:
    return request.usernames[0] if request.usernames else "alguien"


def _handle(request: MemeRequest) -> str:
    return request.usernames[1] if len(request.usernames) > 1 else _name(request)


@effect(
    "byemom",
    "«Adiós, mamá»: buscando algo comprometido.",
    avatars=1,
    texts=1,
    example="cómo borrar el historial",
)
def byemom(request: MemeRequest) -> Image.Image:
    base = asset("byemom/mom").convert("RGBA")
    avatar = request.avatars[0].convert("RGBA").resize((70, 70), Image.Resampling.BICUBIC)
    big_avatar = avatar.resize((125, 125), Image.Resampling.BICUBIC)
    text_layer = Image.new("RGBA", (350, 25))
    bye_layer = Image.new("RGBA", (180, 51), (255, 255, 255))
    typeface = font("arial.ttf", 20)
    bye_font = font("arimobold.ttf", 14)
    message = f"Alright {_name(request)} im leaving the house to run some errands"
    draw_text(text_layer, (0, 0), wrap(typeface, request.text, 500), typeface)
    draw_text(bye_layer, (0, 0), wrap(typeface, message, 200), bye_font, (42, 40, 165))
    text_layer = text_layer.rotate(24.75, resample=Image.Resampling.BICUBIC, expand=True)
    paste(base, text_layer, (350, 443))
    base.paste(bye_layer, (150, 7))
    paste(base, avatar, (530, 15))
    paste(base, big_avatar, (70, 340))
    return base


@effect("floor", "«El suelo es...»", avatars=1, texts=1, example="lava")
def floor(request: MemeRequest) -> Image.Image:
    base = asset("floor/floor").convert("RGBA")
    avatar = request.avatars[0].resize((45, 45)).convert("RGBA")
    typeface = font("sans.ttf", 22)
    draw_text(base, (168, 36), wrap(typeface, request.text, 300), typeface)
    paste(base, avatar, (100, 90))
    paste(base, avatar.resize((23, 23)), (330, 90))
    return base


@effect("garfield", "Garfield: prohibido.", avatars=1, texts=1, example="no se permite pensar")
def garfield(request: MemeRequest) -> Image.Image:
    base = asset("garfield/garfield").convert("RGB")
    no_entry = asset("garfield/no_entry").convert("RGBA")
    no_entry = no_entry.resize((224, 224), Image.Resampling.LANCZOS)
    typeface = font("arial.ttf", 28)
    avatar = request.avatars[0].resize((192, 192), Image.Resampling.LANCZOS).convert("RGBA")
    paste(base, avatar, (296, 219))
    paste(base, no_entry, (280, 203))
    paste(base, avatar.resize((212, 212), Image.Resampling.LANCZOS), (40, 210))
    draw_text(base, (15, 0), wrap(typeface, request.text, base.width), typeface)
    return base


@effect("livereaction", "Reacción en directo.", avatars=1, texts=1, example="yo el lunes")
def livereaction(request: MemeRequest) -> Image.Image:
    image = asset("livereaction/livereaction").convert("RGB")
    inset = request.avatars[0].convert("RGB").resize((929, 526), Image.Resampling.LANCZOS)
    image.paste(inset, (15, 164))
    draw_fitted_text(
        image,
        request.text.upper(),
        (207, 16, 524, 146),
        "arimobold.ttf",
        120,
        minimum=7,
        max_lines=1,
        fill="white",
    )
    return image


@effect(
    "meme",
    "Meme clásico: texto arriba y abajo.",
    avatars=1,
    texts=1,
    optional_texts=1,
    example="cuando compilas | a la primera",
)
def meme(request: MemeRequest) -> Image.Image:
    image = request.avatars[0].convert("RGBA")
    typeface = font("impact.ttf", max(10, image.height // 10))

    def draw_block(text: str, at_bottom: bool) -> None:
        lines = wrap(typeface, text.upper(), image.width).splitlines()
        if not lines:
            return
        line_height = text_size("A", typeface)[1]
        y = image.height - line_height * len(lines) - 10 if at_bottom else 0
        draw = ImageDraw.Draw(image)
        for line in lines:
            width, _ = text_size(line, typeface)
            # El contorno negro de 2 px imita los cuatro dibujos desplazados del original.
            draw.text(
                (image.width / 2 - width / 2, y),
                line,
                font=typeface,
                fill="white",
                stroke_width=2,
                stroke_fill="black",
            )
            y += line_height

    draw_block(request.texts[0], at_bottom=False)
    if len(request.texts) > 1:
        draw_block(request.texts[1], at_bottom=True)
    return image


@effect("obama", "Obama dándose a sí mismo una medalla.", avatars=1, output="jpeg")
def obama(request: MemeRequest) -> Image.Image:
    base = asset("obama/obama")
    typeface = font("arimobold.ttf", 36)
    avatar = request.avatars[0].resize((200, 200), Image.Resampling.LANCZOS).convert("RGBA")
    name = wrap(typeface, _name(request), 400)
    width, _ = text_size(name, typeface)
    paste(base, avatar, (120, 73))
    paste(base, avatar, (365, 0))
    draw_text(base, (int(210 - width / 2), 400), name, typeface, "white")
    draw_text(base, (int(470 - width / 2), 300), name, typeface, "white")
    return base


@effect(
    "quote",
    "Cita falsa con formato de mensaje de Discord.",
    avatars=1,
    texts=1,
    example="yo nunca dije eso",
)
def quote(request: MemeRequest) -> Image.Image:
    avatar = request.avatars[0].resize((150, 150)).convert("RGBA")
    base = Image.new("RGBA", (1500, 300))
    name_font = font("medium.woff", 60)
    time_font = font("medium.woff", 40)
    text_font = font("semibold.woff", 55)
    base.paste(avatar, (15, 75), circle_mask(avatar.size))
    name = _name(request)
    draw_text(base, (230, 70), name, name_font, "white")
    draw_text(base, (230, 150), request.text, text_font, (160, 160, 160))
    stamp = f"Today at {datetime.now(UTC).strftime('%H:%M')}"
    draw_text(
        base, (230 + text_size(name, name_font)[0] + 20, 90), stamp, time_font, (125, 125, 125)
    )
    return base.resize((500, 100), Image.Resampling.LANCZOS)


@effect(
    "sword",
    "«Corto mis problemas con...»",
    texts=2,
    example="los lunes | una siesta",
    output="jpeg",
)
def sword(request: MemeRequest) -> Image.Image:
    base = asset("sword/sword")
    typeface = font("verdana.ttf", 48)
    temp = Image.new("RGBA", (1200, 800), color=(0, 0, 0, 0))
    sword_text = wrap(typeface, request.texts[0], 3000)
    food = wrap(typeface, request.texts[1], 300)
    draw_text(temp, (0, 0), sword_text, typeface, "white")
    temp = temp.rotate(-25, expand=True)
    draw_text(base, (330, 330), _name(request), typeface, "white")
    paste(base, temp, (-30, 605))
    width, _ = text_size(food, typeface)
    draw_text(base, ((base.width - width) / 2 - 20, 830), food, typeface)
    return base


@effect(
    "tweet",
    "Tuit falso con tu nombre.",
    avatars=1,
    texts=1,
    example="los lunes deberían ser ilegales",
)
def tweet(request: MemeRequest) -> Image.Image:
    base = asset("tweet/trump").convert("RGBA")
    avatar = request.avatars[0].resize((98, 98)).convert("RGBA")
    body_font = font("segoeuireg.ttf", 50)
    name_font = font("robotomedium.ttf", 40)
    small_font = font("robotoregular.ttf", 29)
    count_font = font("robotoregular.ttf", 35)

    # Esquinas redondeadas de 10 px para el avatar.
    circle = Image.new("L", (20, 20), 0)
    ImageDraw.Draw(circle).ellipse((0, 0, 20, 20), fill=255)
    alpha = Image.new("L", avatar.size, 255)
    width, height = avatar.size
    alpha.paste(circle.crop((0, 0, 10, 10)), (0, 0))
    alpha.paste(circle.crop((0, 10, 10, 20)), (0, height - 10))
    alpha.paste(circle.crop((10, 0, 20, 10)), (width - 10, 0))
    alpha.paste(circle.crop((10, 10, 20, 20)), (width - 10, height - 10))
    avatar.putalpha(alpha)
    paste(base, avatar, (42, 38))

    # Palabra a palabra, para pintar de azul las menciones y hashtags.
    x, y = 45, 160
    for word in request.text.split(" "):
        word += " "
        if x > 1000:
            x, y = 45, y + 65
        color = "#1b95e0" if word.startswith(("@", "#")) else "black"
        draw_text(base, (x, y), word, body_font, color)
        x += text_size(word, body_font)[0]

    stamp = datetime.now().strftime("%-I:%M %p - %d %b %Y")
    draw_text(base, (160, 45), wrap(name_font, _name(request), 1150), name_font)
    draw_text(base, (160, 95), wrap(small_font, f"@{_handle(request)}", 1150), small_font, "grey")
    draw_text(base, (40, 570), stamp, small_font, "grey")
    draw_text(base, (40, 486), f"{random.randint(0, 99999):,}", count_font, "#2C5F63")
    draw_text(base, (205, 486), f"{random.randint(0, 99999):,}", count_font, "#2C5F63")
    return base


@effect(
    "unpopular", "Opinión impopular.", avatars=1, texts=1, example="el verano es demasiado largo"
)
def unpopular(request: MemeRequest) -> Image.Image:
    avatar = request.avatars[0].resize((666, 666)).convert("RGBA")
    base = asset("unpopular/unpopular").convert("RGBA")
    typeface = font("semibold.woff", 100)
    reticle = asset("unpopular/reticle").convert("RGBA")
    temp = Image.new("RGBA", (1200, 800), color=(0, 0, 0, 0))
    square = Image.new("RGBA", (360, 270), (0, 0, 0, 0))
    mono = avatar.resize((300, 310)).rotate(16, expand=True).convert("1")
    darkened = ImageEnhance.Brightness(mono.convert("RGB")).enhance(0.5)
    square.paste(darkened, (0, 0), mono)
    avatar.putalpha(circle_mask(avatar.size))
    paste(base, avatar, (1169, 1169))
    paste(base, avatar.resize((368, 368)), (140, 250))
    paste(base, reticle, (1086, 1086))
    paste(base, square, (-20, 1670))
    draw_text(temp, (0, 0), wrap(typeface, request.text, 1150), typeface)
    paste(base, temp.rotate(1, expand=True), (620, 280))
    return base


@effect("whothisis", "«¿Sabes quién es este?»", avatars=1, texts=1, example="el de la foto")
def whothisis(request: MemeRequest) -> Image.Image:
    base = asset("whothisis/whothisis").convert("RGBA")
    avatar = request.avatars[0].resize((215, 215)).convert("RGBA")
    paste(base, avatar, (523, 15))
    paste(base, avatar, (509, 567))
    draw_text(base, (545, 465), request.text, font("arimobold.ttf", 40), "white")
    return base


@effect("youtube", "Comentario de YouTube.", avatars=1, texts=1, example="primero")
def youtube(request: MemeRequest) -> Image.Image:
    avatar = request.avatars[0].resize((52, 52)).convert("RGBA")
    base = asset("youtube/youtube").convert("RGBA")
    name_font = font("robotomedium.ttf", 17)
    time_font = font("robotoregular.ttf", 17)
    comment_font = font("robotoregular.ttf", 19)
    avatar.putalpha(circle_mask(avatar.size))
    paste(base, avatar, (17, 33))
    name = _name(request)
    minutes = random.randint(1, 59)
    stamp = f"{minutes} minute{'' if minutes == 1 else 's'} ago"
    draw_text(base, (92, 34), wrap(name_font, name, 1150), name_font)
    draw_text(base, (100 + text_size(name, name_font)[0], 34), stamp, time_font, "grey")
    draw_text(base, (92, 59), wrap(comment_font, request.text, 550), comment_font)
    return base
