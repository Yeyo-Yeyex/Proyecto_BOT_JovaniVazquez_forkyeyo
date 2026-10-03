"""Efectos que escriben uno o varios textos sobre una plantilla.

Coordenadas, fuentes, anchos de línea y recortes son los de `imgen` (Dank
Memer). Los efectos de varios campos reciben los textos en el orden en que
el usuario los separa con `|`, igual que los parámetros `text1`, `text2`...
del original.
"""

from __future__ import annotations

import math

from PIL import Image

from bot.services.memes.registry import MemeRequest, effect
from bot.services.memes.toolkit import (
    asset,
    auto_text_size,
    draw_fitted_text,
    draw_text,
    font,
    paste,
    text_size,
    wrap,
)


def _clip(text: str, limit: int) -> str:
    """Recorta a `limit` caracteres terminando en «...», como hacía el original."""
    return text if len(text) < limit else text[: limit - 3] + "..."


def _simple(
    stem: str,
    request: MemeRequest,
    font_name: str,
    size: int,
    position,
    width,
    fill="black",
    limit: int | None = None,
) -> Image.Image:
    """Plantilla con un único texto partido en líneas en una posición fija."""
    base = asset(stem)
    typeface = font(font_name, size)
    text = wrap(typeface, request.text, width)
    if limit is not None:
        text = text[:limit]
    draw_text(base, position, text, typeface, fill)
    return base


def _rotated(
    stem: str,
    request: MemeRequest,
    font_name: str,
    size: int,
    position,
    width,
    angle: float,
    offset=(0, 0),
) -> Image.Image:
    """Texto dibujado en una capa aparte y girado para seguir la perspectiva de la foto."""
    base = asset(stem).convert("RGBA")
    layer = Image.new("RGBA", base.size)
    typeface = font(font_name, size)
    draw_text(layer, position, wrap(typeface, request.text, width), typeface)
    layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC)
    paste(base, layer, offset)
    return base


# --- Un texto ---------------------------------------------------------------


@effect("abandon", "Abandonar al bebé.", texts=1, example="mis deberes", output="jpeg")
def abandon(request: MemeRequest) -> Image.Image:
    return _simple("abandon/abandon", request, "verdana.ttf", 24, (25, 413), 320)


@effect("armor", "Nada atraviesa esta armadura.", texts=1, example="tus argumentos", output="jpeg")
def armor(request: MemeRequest) -> Image.Image:
    base = asset("armor/armor").convert("RGBA")
    typeface, text = auto_text_size(request.text, font("sans.ttf", 25), 207, font_scalar=0.8)
    draw_text(base, (34, 371), text, typeface)
    return base


@effect("changemymind", "Change my mind.", texts=1, example="la piña va en la pizza", output="jpeg")
def changemymind(request: MemeRequest) -> Image.Image:
    base = asset("changemymind/changemymind").convert("RGBA")
    layer = Image.new("RGBA", base.size)
    typeface, text = auto_text_size(request.text, font("sans.ttf", 25), 310)
    draw_text(layer, (290, 300), text, typeface)
    paste(base, layer.rotate(23, resample=Image.Resampling.BICUBIC), (0, 0))
    return base


@effect("cry", "Llorando mientras lo escribe.", texts=1, example="lunes", output="jpeg")
def cry(request: MemeRequest) -> Image.Image:
    return _simple("cry/cry", request, "tahoma.ttf", 20, (382, 80), 180)


@effect(
    "emergencymeeting",
    "Reunión de emergencia (Among Us).",
    texts=1,
    example="alguien se comió mi yogur",
    output="jpeg",
)
def emergencymeeting(request: MemeRequest) -> Image.Image:
    base = asset("emergencymeeting/emergencymeeting")
    typeface = font("medium.woff", 33)
    draw_text(base, (0, 0), wrap(typeface, _clip(request.text, 140), 750), typeface)
    return base


@effect("excuseme", "«Perdona, ¿qué?»", texts=1, example="¿no te gusta el café?", output="jpeg")
def excuseme(request: MemeRequest) -> Image.Image:
    return _simple("excuseme/excuseme", request, "sans.ttf", 40, (20, 15), 787)


@effect("expanddong", "Tu texto escrito con letras-meme.", texts=1, example="hola")
def expanddong(request: MemeRequest) -> Image.Image:
    text = request.text[:500]
    lines = math.ceil((len(text) * 128) / 1920) + 1
    base = Image.new("RGBA", (1920, lines * 128), (255, 255, 255, 0))
    letters: dict[str, Image.Image] = {}
    line = pos = 0
    for word in text.split(" "):
        if 15 - pos <= len(word):
            pos = 0
            line += 1
        for char in word.lower():
            if char.isascii() and char.isalpha():
                if char not in letters:
                    letters[char] = asset(f"expanddong/{char}")
                base.paste(letters[char], (pos * 128, line * 128))
            pos += 1
        pos += 1
        if pos >= 15:
            pos = 0
            line += 1
    return base


@effect("facts", "Libro de hechos irrefutables.", texts=1, example="el agua moja", output="jpeg")
def facts(request: MemeRequest) -> Image.Image:
    return _rotated("facts/facts", request, "verdana.ttf", 25, (90, 600), 400, -13).convert("RGB")


@effect("godwhy", "«Dios, ¿por qué?»", texts=1, example="otro lunes", output="jpeg")
def godwhy(request: MemeRequest) -> Image.Image:
    base = asset("godwhy/godwhy").resize((1061, 1080), Image.Resampling.LANCZOS)
    typeface = font("verdana.ttf", 24)
    draw_text(base, (35, 560), wrap(typeface, _clip(request.text, 127), 370), typeface)
    return base


@effect(
    "humansgood", "«Los humanos son buenos».", texts=1, example="dormir la siesta", output="jpeg"
)
def humansgood(request: MemeRequest) -> Image.Image:
    base = asset("humansgood/humansgood").convert("RGBA")
    typeface, text = auto_text_size(request.text, font("sans.ttf", 25), 125, font_scalar=0.7)
    draw_text(base, (525, 762), text, typeface)
    return base


@effect("inator", "El invento del Dr. Doofenshmirtz.", texts=1, example="apaga", output="jpeg")
def inator(request: MemeRequest) -> Image.Image:
    base = asset("inator/inator")
    typeface = font("verdana.ttf", 24)
    text = request.text
    draw_text(base, (370, 0), wrap(typeface, text, 340), typeface)
    ending = "nator" if text.endswith(("i", "y", "e", "a", "u", "o")) else "inator"
    draw_text(base, (370, 380), wrap(typeface, text + ending, 335), typeface)
    return base


@effect("jarvis", "«Jarvis, ...»", texts=1, example="borra los lunes", output="jpeg")
def jarvis(request: MemeRequest) -> Image.Image:
    image = asset("jarvis/jarvis").convert("RGB")
    draw_fitted_text(image, request.text, (20, 14, 620, 194), "arimobold.ttf", 46, max_lines=4)
    return image


@effect("keepurdistance", "Mantén la distancia.", texts=1, example="los spoilers", output="jpeg")
def keepurdistance(request: MemeRequest) -> Image.Image:
    base = asset("keepurdistance/keepurdistance").convert("RGB")
    typeface = font("MontserratBold.ttf", 24)
    text = _clip(request.text.upper(), 30)
    draw_text(base, (92, 660), wrap(typeface, text, 440), typeface, "white")
    return base


@effect(
    "note", "Pasando una nota en clase.", texts=1, example="¿me prestas los apuntes?", output="jpeg"
)
def note(request: MemeRequest) -> Image.Image:
    return _rotated("note/note", request, "sans.ttf", 16, (455, 420), 150, -23)


@effect(
    "nothing", "«No hay nada que...»", texts=1, example="un buen café no arregle", output="jpeg"
)
def nothing(request: MemeRequest) -> Image.Image:
    return _simple("nothing/nothing", request, "medium.woff", 33, (340, 5), 200, limit=120)


@effect("ohno", "«Oh no, es estúpido».", texts=1, example="mi código", output="jpeg")
def ohno(request: MemeRequest) -> Image.Image:
    size = 16 if len(request.text) > 38 else 32
    return _simple("ohno/ohno", request, "sans.ttf", size, (340, 30), 260)


@effect(
    "piccolo",
    "Piccolo pensativo.",
    texts=1,
    example="¿y si los lunes no existieran?",
    output="jpeg",
)
def piccolo(request: MemeRequest) -> Image.Image:
    return _simple("piccolo/piccolo", request, "medium.woff", 33, (5, 5), 850, limit=300)


@effect(
    "presentation",
    "Presentación de Lisa Simpson.",
    texts=1,
    example="el café es una necesidad",
    output="jpeg",
)
def presentation(request: MemeRequest) -> Image.Image:
    return _simple("presentation/presentation", request, "verdana.ttf", 24, (150, 80), 330)


@effect(
    "savehumanity", "Lo que salvará a la humanidad.", texts=1, example="la siesta", output="jpeg"
)
def savehumanity(request: MemeRequest) -> Image.Image:
    return _rotated("humanity/humanity", request, "sans.ttf", 16, (490, 410), 180, -7)


@effect("shit", "Pisando una mierda.", texts=1, example="mis notas", output="jpeg")
def shit(request: MemeRequest) -> Image.Image:
    return _rotated("shit/shit", request, "segoeuireg.ttf", 30, (0, 570), 350, 52, (0, 50))


@effect(
    "slapsroof", "*golpea el techo* «Aquí cabe mucho...»", texts=1, example="sueño", output="jpeg"
)
def slapsroof(request: MemeRequest) -> Image.Image:
    base = asset("slapsroof/slapsroof")
    typeface = font("medium.woff", 33)
    draw_text(base, (335, 31), wrap(typeface, request.text + " in it", 1150), typeface)
    return base


@effect("stroke", "¿Te está dando un ictus?", texts=1, example="aksjdhaksjd", output="jpeg")
def stroke(request: MemeRequest) -> Image.Image:
    return _simple("stroke/stroke", request, "verdana.ttf", 12, (272, 287), 75)


@effect(
    "thesearch",
    "La búsqueda de vida inteligente.",
    texts=1,
    example="mi grupo de clase",
    output="jpeg",
)
def thesearch(request: MemeRequest) -> Image.Image:
    return _simple("search/thesearch", request, "sans.ttf", 16, (65, 335), 178)


@effect("todo", "Lista de tareas.", texts=1, example="dormir", output="jpeg")
def todo(request: MemeRequest) -> Image.Image:
    image = asset("todo/todo").convert("RGB")
    layer = Image.new("RGBA", (360, 220))
    draw_fitted_text(layer, request.text, (8, 4, 352, 215), "arimobold.ttf", 34, max_lines=8)
    layer = layer.rotate(15, resample=Image.Resampling.BICUBIC, expand=True)
    paste(image, layer, (345, 215))
    return image


@effect(
    "violence",
    "La violencia nunca es la respuesta... salvo con esto.",
    texts=1,
    example="la gente que no pone el intermitente",
    output="jpeg",
)
def violence(request: MemeRequest) -> Image.Image:
    return _simple("violence/violence", request, "arimobold.ttf", 24, (355, 0), 270)


@effect("vr", "Realidad virtual.", texts=1, example="tocar hierba", output="jpeg")
def vr(request: MemeRequest) -> Image.Image:
    base = asset("vr/vr").convert("RGBA")
    typeface, text = auto_text_size(request.text, font("sans.ttf", 25), 207, font_scalar=0.8)
    width, _ = text_size(text, typeface)
    draw_text(base, (int(170 - width / 2), 485), text, typeface)
    return base


@effect("walking", "El cartel del que pasea.", texts=1, example="se acerca el lunes", output="jpeg")
def walking(request: MemeRequest) -> Image.Image:
    return _simple("walking/walking", request, "sans.ttf", 50, (35, 35), 1000)


# --- Dos o más textos, separados con | --------------------------------------


@effect(
    "balloon", "No sueltes el globo.", texts=2, example="mis ahorros | la tienda", output="jpeg"
)
def balloon(request: MemeRequest) -> Image.Image:
    base = asset("balloon/balloon").convert("RGBA")
    typeface = font("sans.ttf", 25)
    balloon_text, label = request.texts
    for width, scalar, position in (
        (162, 1, (80, 180)),
        (170, 0.95, (50, 530)),
        (110, 0.8, (500, 520)),
    ):
        fitted_font, fitted = auto_text_size(balloon_text, typeface, width, font_scalar=scalar)
        draw_text(base, position, fitted, fitted_font)
    label_font, label_text = auto_text_size(label, typeface, 125)
    draw_text(base, (620, 155), label_text, label_font)
    return base


@effect("boo", "Fantasma: «bu» y «¡ah!».", texts=2, example="un examen | yo", output="jpeg")
def boo(request: MemeRequest) -> Image.Image:
    base = asset("boo/boo").convert("RGBA")
    typeface = font("sans.ttf", 25)
    for text, position in zip(request.texts, ((35, 54), (267, 57)), strict=True):
        fitted_font, fitted = auto_text_size(text, typeface, 144, font_scalar=0.7)
        draw_text(base, position, fitted, fitted_font)
    return base


@effect(
    "brain",
    "Cerebro que se expande (4 niveles).",
    texts=4,
    example="agua | zumo | café | café a las 3 de la mañana",
    output="jpeg",
)
def brain(request: MemeRequest) -> Image.Image:
    base = asset("brain/brain")
    typeface = font("verdana.ttf", 30)
    for text, y in zip(request.texts, (40, 230, 420, 610), strict=True):
        draw_text(base, (15, y), wrap(typeface, text, 225).strip(), typeface)
    return base


@effect("cheating", "Copiando en el examen.", texts=2, example="yo | el empollón", output="jpeg")
def cheating(request: MemeRequest) -> Image.Image:
    base = asset("cheating/cheating")
    typeface = font("medium.woff", 26)
    me, classmate = (wrap(typeface, text, 150)[:50] for text in request.texts)
    draw_text(base, (15, 300), me, typeface, "white")
    draw_text(base, (155, 200), classmate, typeface, "white")
    return base


@effect(
    "citation",
    "Multa de Papers, Please.",
    texts=3,
    example="M.O.A. CITATION | Llegar tarde | PENALTY ASSESSED - 5 CREDITS",
    output="jpeg",
)
def citation(request: MemeRequest) -> Image.Image:
    base = asset("citation/citation")
    typeface = font("bmmini.ttf", 16)
    first, second, footer = request.texts
    draw_text(base, (20, 10), wrap(typeface, first, 320), typeface, "white")
    draw_text(base, (20, 45), wrap(typeface, second, 320), typeface, "white")
    width, _ = text_size(footer, typeface)
    draw_text(base, ((base.width - width) / 2, 130), footer, typeface, "white")
    return base


@effect(
    "confusedcat",
    "La señora gritando al gato.",
    texts=2,
    example="tú | yo sin culpa",
    output="jpeg",
)
def confusedcat(request: MemeRequest) -> Image.Image:
    base = asset("confusedcat/confusedcat")
    typeface = font("medium.woff", 36)
    ladies, cat = (wrap(typeface, text, 510)[:100] for text in request.texts)
    draw_text(base, (5, 5), ladies, typeface)
    draw_text(base, (516, 5), cat, typeface)
    return base


@effect("doglemon", "El perro y el limón.", texts=2, example="el examen | yo", output="jpeg")
def doglemon(request: MemeRequest) -> Image.Image:
    base = asset("doglemon/doglemon")
    typeface = font("medium.woff", 30)
    lemon, dog = request.texts
    draw_text(base, (850, 100), wrap(typeface, lemon, 450)[:180], typeface)
    draw_text(base, (500, 100), wrap(typeface, dog, 450)[:200], typeface, "white")
    return base


@effect(
    "expandingwwe",
    "Cerebro expandiéndose, versión lucha libre (5).",
    texts=5,
    example="hola | buenas | saludos | salutaciones | qué pasa, máquina",
    output="jpeg",
)
def expandingwwe(request: MemeRequest) -> Image.Image:
    base = asset("expandingwwe/expandingwwe")
    typeface = font("verdana.ttf", 30)
    for text, y in zip(request.texts, (5, 205, 410, 620, 825), strict=True):
        draw_text(base, (5, y), wrap(typeface, text, 225).strip(), typeface)
    return base


@effect(
    "farmer", "El granjero y las nubes.", texts=2, example="un dragón | un pájaro", output="jpeg"
)
def farmer(request: MemeRequest) -> Image.Image:
    base = asset("farmer/farmer")
    typeface = font("verdana.ttf", 24)
    clouds, farmer_text = _clip(request.texts[0], 150), _clip(request.texts[1], 100)
    draw_text(base, (50, 300), wrap(typeface, clouds, 580), typeface, "white")
    draw_text(base, (50, 825), wrap(typeface, farmer_text, 580), typeface, "white")
    return base


@effect("fuck", "Mírame a los ojos.", texts=2, example="yo | la nevera", output="jpeg")
def fuck(request: MemeRequest) -> Image.Image:
    base = asset("fuck/fuck")
    typeface = font("verdana.ttf", 24)
    draw_text(base, (200, 600), wrap(typeface, request.texts[0], 320), typeface, "white")
    draw_text(base, (750, 700), wrap(typeface, request.texts[1], 320), typeface, "white")
    return base


@effect(
    "justpretending",
    "«Solo fingíamos ser idiotas».",
    texts=2,
    example="nosotros | idiotas",
    output="jpeg",
)
def justpretending(request: MemeRequest) -> Image.Image:
    base = asset("justpretending/justpretending")
    typeface = font("verdana.ttf", 24)
    first, second = request.texts
    draw_text(base, (678, 12), wrap(typeface, first, 320), typeface)
    for position in ((9, 800), (399, 808), (59, 917), (425, 910)):
        draw_text(base, position, wrap(typeface, second, 100), typeface)
    return base


@effect(
    "knowyourlocation",
    "«Sabemos dónde vives».",
    texts=2,
    example="¿quieres galletas? | ya vamos",
    output="jpeg",
)
def knowyourlocation(request: MemeRequest) -> Image.Image:
    base = asset("knowyourlocation/knowyourlocation").convert("RGBA")
    typeface = font("sans.ttf", 25)
    top_font, top = auto_text_size(request.texts[0], typeface, 630)
    bottom_font, bottom = auto_text_size(request.texts[1], typeface, 539)
    draw_text(base, (64, 131), top, top_font)
    draw_text(base, (120, 450), bottom, bottom_font)
    return base


@effect("lick", "Lamiendo.", texts=2, example="yo | la tapa del yogur", output="jpeg")
def lick(request: MemeRequest) -> Image.Image:
    base = asset("lick/lick")
    typeface = font("verdana.ttf", 24)
    draw_text(base, (80, 200), wrap(typeface, request.texts[0], 220), typeface, "white")
    draw_text(base, (290, 240), wrap(typeface, request.texts[1], 320), typeface, "white")
    return base


@effect("machine", "Mete esto en la máquina y sale aquello.", texts=2, example="café | código")
def machine(request: MemeRequest) -> Image.Image:
    image = asset("machine/machine").convert("RGB")
    draw_fitted_text(
        image,
        request.texts[0],
        (299, 468, 820, 585),
        "arimobold.ttf",
        39,
        max_lines=2,
        fill="white",
    )
    draw_fitted_text(
        image,
        request.texts[1],
        (175, 792, 820, 888),
        "arimobold.ttf",
        42,
        max_lines=3,
        fill="white",
    )
    return image


@effect(
    "master",
    "El maestro y sus seguidores.",
    texts=3,
    example="yo | mis excusas | el profe",
    output="jpeg",
)
def master(request: MemeRequest) -> Image.Image:
    base = asset("master/master").convert("RGBA")
    first, second, third = request.texts
    typeface, text1 = auto_text_size(first, font("sans.ttf", 25), 250, font_scalar=0.2)
    typeface, text2 = auto_text_size(second, typeface, 250, font_scalar=0.3)
    typeface, text3 = auto_text_size(third, typeface, 300, font_scalar=0.2)
    layer = Image.new("RGBA", base.size)
    draw_text(base, (457, 513), text1, typeface, "white")
    draw_text(layer, (350, 330), text2, typeface, "white")
    draw_text(base, (148, 151), text3, typeface, "white")
    paste(base, layer.rotate(8, resample=Image.Resampling.BICUBIC), (0, 0))
    return base


@effect(
    "plan",
    "El plan de Gru que sale mal.",
    texts=3,
    example="estudiar | aprobar | suspender",
    output="jpeg",
)
def plan(request: MemeRequest) -> Image.Image:
    base = asset("plan/plan").convert("RGBA")
    typeface = font("sans.ttf", 16)
    first, second, third = (wrap(typeface, text, 120) for text in request.texts)
    for text, position in (
        (first, (190, 60)),
        (second, (510, 60)),
        (third, (190, 280)),
        (third, (510, 280)),
    ):
        draw_text(base, position, text, typeface)
    return base


@effect(
    "sneakyfox",
    "El zorro escondido.",
    texts=2,
    example="yo | la nevera a las 3 a. m.",
    output="jpeg",
)
def sneakyfox(request: MemeRequest) -> Image.Image:
    base = asset("sneakyfox/sneakyfox")
    typeface = font("arimobold.ttf", 36)
    fox = wrap(typeface, request.texts[0], 500)[:180]
    other = wrap(typeface, request.texts[1], 450)[:180]
    draw_text(base, (300, 350), fox, typeface)
    draw_text(base, (670, 120), other, typeface)
    return base


@effect(
    "surprised",
    "«Yo: ... / También yo: ...»",
    texts=2,
    example="quiero dormir pronto | 3 a. m. viendo vídeos",
    output="jpeg",
)
def surprised(request: MemeRequest) -> Image.Image:
    base = asset("surprised/surprised").convert("RGBA")
    typeface = font("robotoregular.ttf", 36)
    draw_text(base, (20, 20), wrap(typeface, "me: " + request.texts[0], 650), typeface, "white")
    draw_text(
        base, (20, 140), wrap(typeface, "also me: " + request.texts[1], 650), typeface, "white"
    )
    return base


@effect(
    "theoffice",
    "Corporate quiere que veas la diferencia (The Office).",
    texts=2,
    example="mi código | mi código con comentarios",
    output="jpeg",
)
def theoffice(request: MemeRequest) -> Image.Image:
    base = asset("theoffice/theoffice").convert("RGB")
    typeface = font("verdana.ttf", 28)
    draw_text(base, (125, 200), wrap(typeface, request.texts[0], 200), typeface, "white")
    draw_text(base, (420, 250), wrap(typeface, request.texts[1], 200), typeface, "white")
    return base


@effect("violentsparks", "Chispas violentas.", texts=2, example="yo | el lunes", output="jpeg")
def violentsparks(request: MemeRequest) -> Image.Image:
    base = asset("violentsparks/violentsparks")
    typeface = font("medium.woff", 36)
    draw_text(base, (15, 5), wrap(typeface, request.texts[0], 550), typeface, "white")
    draw_text(base, (350, 430), wrap(typeface, request.texts[1], 200), typeface)
    return base
