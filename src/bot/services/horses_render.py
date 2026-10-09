"""Dibujo de las carreras de caballos: la parrilla, la carrera en GIF y el boleto.

Tres imágenes, todas con formas de Pillow (sin sprites externos); lo único de
fuera son las fuentes de los Botes y de los memes:

1. **Parrilla** (`card`): la ficha de la carrera antes de salir. Un renglón por
   caballo con su dorsal en los colores de su cuadra, el nombre, la forma, el
   terreno que le gusta, una barra con su probabilidad de ganar y la cuota en
   grande. El pronóstico de Perro Sanxe lleva su sello.
2. **Carrera** (`race`): un GIF de vista lateral. Los caballos galopan de los
   cajones a la meta con las patas en movimiento y la clasificación en vivo
   abajo. Cámara virtual: el que va primero avanza a ritmo fijo hacia la meta
   y los demás se dibujan detrás a la distancia real que les saca, en
   cuerpos (`PX_PER_M`). Así se ve la carrera entera sin mover el fondo, que
   es lo que más pesa en un GIF. Si llueve, la pista se oscurece y cae agua;
   si hay foto-finish, los últimos metros van a cámara lenta y la imagen final
   lleva la foto de la meta en sepia. El último fotograma (el podio) es el
   PNG que el cog deja al final.
3. **Boleto** (`ticket`): el resguardo de una apuesta, con el sello de
   PREMIADO si cobra.

Coste: el fondo y los dibujos de cada caballo en cada postura se pintan una
vez al doble de tamaño, se reducen y se guardan. Cada fotograma copia el fondo
y pega encima los caballos y el marcador. Una carrera son ~120-160
fotogramas, ~1 s de CPU fuera del event loop, y el GIF pesa ~1-2 MB porque
cada fotograma solo guarda lo que cambia (`bot.utils.gif.local_palette_gif`).

Daltonismo: nada depende solo del color. Cada caballo lleva su dorsal escrito
en el lomo y en el marcador; el ganador sale en el podio con su número.
"""

from __future__ import annotations

import io
import math
import random
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from bot.services.horses import (
    RAIN_SEGMENT,
    BetKind,
    Going,
    Horse,
    HorseRecord,
    Odds,
    Pick,
    RaceCard,
    RaceResult,
    Tip,
    form_line,
    format_odds,
    margin_text,
    photo_finish,
)
from bot.utils.gif import local_palette_gif

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"

# -- Medidas de la carrera -----------------------------------------------------------------

W, H = 640, 360
#: Los dibujos se hacen al doble y se reducen: bordes sin dientes de sierra.
S = 2
STAND_H = 62
TRACK_TOP = 72
TRACK_BOTTOM = 312
HUD_TOP = 320
START_X = 70
FINISH_X = 560
#: Píxeles por metro de ventaja: un cuerpo (2,4 m) son ~14 px.
PX_PER_M = 5.8
#: Duración de la carrera en el GIF (hasta que entra el ganador).
RACE_SECONDS = 9.0
GRAND_PRIX_SECONDS = 11.0
FRAME_MS = 80
#: Fotogramas con los cajones cerrados antes de salir.
GATE_FRAMES = 6
#: Cámara lenta del foto-finish.
SLOW_MOTION = 4
FINAL_FRAME_MS = 60_000
#: Posturas del galope.
POSES = 8

# -- Colores ------------------------------------------------------------------------------

INK = (24, 22, 28)
WHITE = (250, 250, 246)
GOLD = (255, 196, 0)
SILVER = (200, 205, 215)
BRONZE = (205, 127, 50)
MUTED = (170, 174, 184)
HUD_BG = (18, 19, 23)
STAND = (44, 48, 60)
ROOF = (150, 30, 40)
RAIL = (245, 245, 245)
GRASS = (70, 140, 70)
RED = (220, 40, 50)
TRACK_COLORS = {
    Going.SECO: (214, 182, 128),
    Going.BLANDO: (178, 142, 98),
    Going.BARRO: (124, 94, 66),
}
#: Capas de pelo: alazán, castaño, tordo y negro.
COATS = ((150, 82, 40), (100, 60, 35), (170, 170, 175), (45, 38, 36))
PAPER = (250, 244, 226)
PAPER_EDGE = (220, 205, 170)
STAMP = (205, 30, 40)


@lru_cache(maxsize=16)
def _title(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TITLE_FONT), size)


@lru_cache(maxsize=16)
def _text(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TEXT_FONT), size)


@dataclass(frozen=True, slots=True)
class Media:
    """La carrera dibujada: el GIF, el PNG final y lo que dura el GIF."""

    gif: bytes
    png: bytes
    seconds: float


def coat_for(horse: Horse) -> tuple[int, int, int]:
    """Capa de un caballo: siempre la misma para la misma clave."""
    return COATS[sum(map(ord, horse.key)) % len(COATS)]


def _readable(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Tinta legible sobre un color: negra en colores claros, blanca en oscuros."""
    r, g, b = rgb
    return INK if 0.299 * r + 0.587 * g + 0.114 * b > 150 else WHITE


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


# -- Renderizador ---------------------------------------------------------------------------


class HorseRenderer:
    """Dibuja las carreras; seguro para usar desde varios hilos."""

    def __init__(self) -> None:
        self._sprites: dict[tuple, Image.Image] = {}
        self._tracks: dict[tuple, Image.Image] = {}
        self._lock = threading.Lock()

    # -- Parrilla ---------------------------------------------------------------------------

    def card(
        self,
        card: RaceCard,
        odds: Odds,
        records: dict[str, HorseRecord],
        *,
        tip: Tip | None = None,
        pot: int | None = None,
    ) -> bytes:
        """PNG de la parrilla de salida con las cuotas a ganador."""
        row_h = 46
        top = 74
        height = top + row_h * card.size + 34
        image = Image.new("RGB", (W, height), HUD_BG)
        d = ImageDraw.Draw(image)
        title_color = GOLD if card.grand_prix else WHITE
        d.text((18, 12), card.name.upper(), font=_title(26), fill=title_color)
        details = [f"{_thousands(card.distance)} m", f"Pista {card.going.label.lower()}"]
        if card.rain_chance:
            details.append(f"{round(card.rain_chance * 100)} % de lluvia")
        if pot is not None:
            details.append(f"Bote {_thousands(pot)} Y$")
        d.text((18, 46), " · ".join(details), font=_text(14), fill=MUTED)
        d.text((W - 18, 52), "GANADOR", font=_text(11), fill=MUTED, anchor="rs")
        best = max(odds.win) or 1.0
        for i, horse in enumerate(card.horses):
            y = top + i * row_h
            if i % 2 == 0:
                d.rectangle((0, y, W, y + row_h - 1), fill=(26, 27, 33))
            self._badge(d, 18, y + 7, horse, i + 1, size=32)
            d.text((62, y + 6), horse.name, font=_text(17), fill=WHITE)
            record = records.get(horse.key, HorseRecord())
            tags = [f"Forma {form_line(record.form)}", f"Le gusta: {horse.going.label.lower()}"]
            if horse.key in card.tired:
                tags.append("CANSADO")
            d.text((62, y + 27), " · ".join(tags), font=_text(11), fill=MUTED)
            bar = 120 * odds.win[i] / best
            d.rectangle((388, y + 31, 388 + 120, y + 35), fill=(50, 52, 60))
            d.rectangle((388, y + 31, 388 + bar, y + 35), fill=horse.silks[0], outline=INK)
            d.text(
                (W - 18, y + 24),
                format_odds(odds.odds(Pick(BetKind.WIN, (i,)))),
                font=_title(24),
                fill=GOLD if i == odds.favourite() else WHITE,
                anchor="rm",
            )
            if tip is not None and tip.horse == i:
                self._sanxe_stamp(d, 400, y + 4, tip.confidence)
        d.text(
            (18, height - 22),
            "La barra es la probabilidad de ganar. Las cuotas salen de simular la carrera.",
            font=_text(11),
            fill=MUTED,
        )
        return self._png(image)

    def _sanxe_stamp(self, d: ImageDraw.ImageDraw, x: float, y: float, confidence: int) -> None:
        text = f"SANXE {confidence} %"
        d.rounded_rectangle((x, y, x + 104, y + 20), radius=6, fill=STAMP)
        d.text((x + 52, y + 10), text, font=_text(11), fill=WHITE, anchor="mm")

    def _badge(
        self, d: ImageDraw.ImageDraw, x: float, y: float, horse: Horse, number: int, *, size: int
    ) -> None:
        """Dorsal: cuadrado con los colores de la cuadra y el número encima."""
        main, second = horse.silks
        # Borde gris: se ve tanto en la parrilla oscura como en colores muy oscuros.
        d.rounded_rectangle(
            (x, y, x + size, y + size), radius=size // 5, fill=main, outline=(130, 134, 146)
        )
        d.rectangle((x + 3, y + size - size // 4, x + size - 3, y + size - 3), fill=second)
        d.text(
            (x + size / 2, y + size / 2 - 2),
            str(number),
            font=_title(int(size * 0.7)),
            fill=_readable(main),
            anchor="mm",
            stroke_width=1,
            stroke_fill=INK if _readable(main) == WHITE else WHITE,
        )

    # -- Boleto ---------------------------------------------------------------------------

    def ticket(
        self,
        *,
        race: str,
        player: str,
        pick: Pick,
        names: Sequence[str],
        stake: int,
        odds: int,
        won: bool | None = None,
        prize: int | None = None,
        pot_share: int = 0,
    ) -> bytes:
        """PNG del boleto. `won=None` antes de la carrera; con `True` lleva PREMIADO."""
        w, h = 520, 228
        image = Image.new("RGB", (w, h), HUD_BG)
        d = ImageDraw.Draw(image)
        d.rounded_rectangle((6, 6, w - 6, h - 6), radius=10, fill=PAPER, outline=PAPER_EDGE)
        # Matriz troquelada a la izquierda.
        d.line((118, 14, 118, h - 14), fill=PAPER_EDGE, width=2)
        for y in range(16, h - 12, 12):
            d.ellipse((114, y, 122, y + 6), fill=HUD_BG)
        d.text((62, 40), "Nº", font=_text(12), fill=(120, 110, 90), anchor="mm")
        d.text((62, 80), pick.numbers(), font=_title(30), fill=INK, anchor="mm")
        d.text((62, 122), pick.kind.label.upper(), font=_text(12), fill=INK, anchor="mm")
        d.text((62, 196), "HIPÓDROMO", font=_text(10), fill=(120, 110, 90), anchor="mm")
        x = 136
        d.text((x, 20), "BOLETO · " + race.upper(), font=_text(12), fill=(120, 110, 90))
        d.text((x, 42), player[:28], font=_title(22), fill=INK)
        short = [n if len(n) <= 16 else n[:15] + "…" for n in names]
        lines = [" → ".join(short) if len(short) > 1 else short[0]]
        lines.append(f"Apuesta {_thousands(stake)} Y$ a {format_odds(odds)}")
        if prize is None:
            prize = stake * odds // 100
            lines.append(f"Si acierta cobras {_thousands(prize)} Y$")
        else:
            lines.append(f"Cobra {_thousands(prize)} Y$")
        if pot_share:
            lines.append(f"Bote del Gran Premio +{_thousands(pot_share)} Y$")
        y = 80
        for line in lines:
            d.text((x, y), line[:52], font=_text(14), fill=INK)
            y += 24
        if won:
            stamp = Image.new("RGBA", (260, 80), (0, 0, 0, 0))
            sd = ImageDraw.Draw(stamp)
            sd.rounded_rectangle((4, 4, 256, 76), radius=12, outline=STAMP + (175,), width=6)
            sd.text((130, 42), "PREMIADO", font=_title(44), fill=STAMP + (175,), anchor="mm")
            stamp = stamp.rotate(14, expand=True, resample=Image.Resampling.BICUBIC)
            image.paste(stamp, (w - stamp.width - 4, h - stamp.height - 2), stamp)
        return self._png(image)

    # -- Carrera ---------------------------------------------------------------------------

    def race(self, card: RaceCard, result: RaceResult) -> Media:
        """GIF de la carrera y PNG del podio."""
        rng = random.Random(hash((card.name, result.times)) & 0xFFFF)
        times, _slow = frame_times(card, result)
        photo = photo_finish(result, card.distance)
        rain_at = rain_start(result)

        frames: list[Image.Image] = []
        for n, t in enumerate(times):
            frames.append(
                self._frame(
                    card,
                    result,
                    t,
                    frame=n,
                    gate=n < GATE_FRAMES,
                    raining=rain_at is not None and t >= rain_at,
                    rng=rng,
                )
            )
        final = self._podium(card, result, frames[-1], photo=photo)
        durations = [FRAME_MS] * len(frames) + [FINAL_FRAME_MS]
        frames.append(final)
        gif = local_palette_gif(frames, durations)
        seconds = FRAME_MS * (len(frames) - 1) / 1000
        return Media(gif=gif, png=self._png(final), seconds=seconds)

    def _going_at(self, card: RaceCard, raining: bool) -> Going:
        return card.going.wetter() if raining else card.going

    def _track(self, card: RaceCard, going: Going) -> Image.Image:
        """Fondo de la carrera (grada, pista, postes y meta), pintado una vez."""
        key = (card.size, going, card.distance, card.grand_prix)
        with self._lock:
            cached = self._tracks.get(key)
        if cached is not None:
            return cached
        big = Image.new("RGB", (W * S, H * S), GRASS)
        d = ImageDraw.Draw(big)

        def xy(*values: float) -> list[float]:
            return [v * S for v in values]

        # Grada con público (puntos de colores fijos) y tejado.
        d.rectangle(xy(0, 0, W, STAND_H), fill=STAND)
        crowd = random.Random(7)
        for row in range(5):
            for col in range(80):
                cx = col * 8 + (4 if row % 2 else 0) + crowd.uniform(-1, 1)
                cy = 18 + row * 9
                color = crowd.choice(
                    ((230, 80, 80), (240, 200, 90), (90, 150, 230), WHITE, (120, 200, 120))
                )
                d.ellipse(xy(cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5), fill=color)
        d.rectangle(xy(0, 0, W, 10), fill=ROOF)
        for x in range(0, W, 24):
            d.polygon(xy(x, 10, x + 12, 16, x + 24, 10), fill=ROOF)
        if card.grand_prix:
            d.rounded_rectangle(xy(W / 2 - 120, 2, W / 2 + 120, 24), radius=6 * S, fill=GOLD)
            d.text(
                xy(W / 2, 14),
                "GRAN PREMIO",
                font=_title(18 * S),
                fill=INK,
                anchor="mm",
            )
        # Pista.
        d.rectangle(xy(0, TRACK_TOP, W, TRACK_BOTTOM), fill=TRACK_COLORS[going])
        lane_h = (TRACK_BOTTOM - TRACK_TOP) / card.size
        shade = tuple(max(0, c - 14) for c in TRACK_COLORS[going])
        for lane in range(1, card.size):
            y = TRACK_TOP + lane * lane_h
            d.line(xy(0, y, W, y), fill=shade, width=S)
        # Barandillas.
        for y in (TRACK_TOP - 8, TRACK_BOTTOM):
            d.rectangle(xy(0, y, W, y + 6), fill=RAIL)
            for x in range(0, W, 32):
                d.rectangle(xy(x, y + 6, x + 3, y + 10), fill=(200, 200, 200))
        # Postes de distancia.
        for left in (800, 400, 200):
            if left >= card.distance:
                continue
            x = START_X + (1 - left / card.distance) * (FINISH_X - START_X)
            d.rectangle(xy(x - 2, TRACK_TOP - 20, x + 2, TRACK_TOP - 2), fill=RED)
            d.text(
                xy(x, TRACK_TOP - 22),
                str(left),
                font=_text(10 * S),
                fill=WHITE,
                anchor="mb",
            )
        # Meta: poste a cuadros rojos y blancos de arriba abajo.
        size = 6
        for i, y in enumerate(range(TRACK_TOP - 8, TRACK_BOTTOM + 6, size)):
            color = RED if i % 2 else WHITE
            d.rectangle(xy(FINISH_X - 3, y, FINISH_X + 3, y + size), fill=color)
        d.text(xy(FINISH_X, TRACK_TOP - 24), "META", font=_title(14 * S), fill=GOLD, anchor="mb")
        # Marcador de abajo.
        d.rectangle(xy(0, HUD_TOP, W, H), fill=HUD_BG)
        track = big.resize((W, H), Image.Resampling.LANCZOS)
        with self._lock:
            if len(self._tracks) > 64:
                self._tracks.clear()
            self._tracks[key] = track
        return track

    def _sprite(self, horse: Horse, number: int, pose: int, height: int) -> Image.Image:
        """Un caballo con su jinete en una postura del galope (RGBA, mirando a la derecha).

        El punto (ancho, alto) de la imagen es el hocico: se pega por ahí.
        """
        key = (horse.key, number, pose, height)
        with self._lock:
            cached = self._sprites.get(key)
        if cached is not None:
            return cached
        k = height / 40 * S  # escala: el dibujo base mide 40 px de alto
        w, h = round(66 * k), round(46 * k)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        coat = coat_for(horse)
        dark = tuple(max(0, c - 40) for c in coat)
        main, second = horse.silks

        def p(*values: float) -> list[float]:
            return [v * k for v in values]

        phase = 2 * math.pi * pose / POSES
        # Patas: dos pares que se cruzan como en un galope.
        for base_x, offset, color in (
            (16, 0.0, dark),
            (44, math.pi, dark),
            (20, 0.6, coat),
            (48, math.pi + 0.6, coat),
        ):
            swing = math.sin(phase + offset)
            knee = (base_x + swing * 5, 37)
            hoof = (base_x + swing * 9, 44 - max(0.0, math.cos(phase + offset)) * 3)
            d.line(p(base_x, 28, *knee), fill=color, width=round(3.2 * k))
            d.line(p(*knee, *hoof), fill=color, width=round(2.6 * k))
        # Cola.
        tail = math.sin(phase) * 3
        d.line(
            p(10, 22, 2, 26 + tail, 3, 32 + tail), fill=dark, width=round(2.4 * k), joint="curve"
        )
        # Cuerpo, cuello y cabeza.
        bob = math.sin(phase * 2) * 1.2
        d.ellipse(p(8, 17 + bob, 52, 34 + bob), fill=coat, outline=INK, width=max(1, round(k)))
        d.polygon(p(44, 22 + bob, 52, 10 + bob, 58, 12 + bob, 54, 28 + bob), fill=coat)
        d.polygon(
            p(52, 9 + bob, 65, 14 + bob, 64, 19 + bob, 54, 17 + bob),
            fill=coat,
            outline=INK,
        )
        d.ellipse(p(57, 11 + bob, 59, 13 + bob), fill=INK)
        d.polygon(p(51, 9 + bob, 53, 4 + bob, 55, 9 + bob), fill=dark)
        # Mantilla con el dorsal.
        d.rectangle(p(18, 19 + bob, 31, 29 + bob), fill=WHITE, outline=INK)
        d.text(
            p(24.5, 24.5 + bob),
            str(number),
            font=_title(round(9 * k)),
            fill=INK,
            anchor="mm",
        )
        # Jinete: chaquetilla con el dibujo de la cuadra y gorra del otro color.
        jacket = p(30, 6 + bob, 42, 6 + bob, 40, 18 + bob, 30, 18 + bob)
        d.polygon(jacket, fill=main, outline=INK)
        if horse.pattern == "banda":
            d.line(p(31, 7 + bob, 40, 17 + bob), fill=second, width=round(2.4 * k))
        elif horse.pattern == "rayas":
            for x in (33, 36, 39):
                d.line(p(x, 7 + bob, x - 0.5, 17 + bob), fill=second, width=round(1.4 * k))
        elif horse.pattern == "lunares":
            for cx, cy in ((33, 9), (38, 12), (34, 15)):
                d.ellipse(p(cx - 1.2, cy - 1.2 + bob, cx + 1.2, cy + 1.2 + bob), fill=second)
        d.line(p(41, 10 + bob, 49, 14 + bob), fill=main, width=round(2.2 * k))
        d.ellipse(p(38, 0 + bob, 45, 7 + bob), fill=second, outline=INK)
        d.line(p(30, 18 + bob, 34, 26 + bob), fill=WHITE, width=round(2.2 * k))
        sprite = img.resize((round(w / S), round(h / S)), Image.Resampling.LANCZOS)
        with self._lock:
            if len(self._sprites) > 4_096:
                self._sprites.clear()
            self._sprites[key] = sprite
        return sprite

    def _positions(self, card: RaceCard, result: RaceResult, t: float) -> list[float]:
        return screen_positions(card, result, t)

    def _frame(
        self,
        card: RaceCard,
        result: RaceResult,
        t: float,
        *,
        frame: int,
        gate: bool,
        raining: bool,
        rng: random.Random,
    ) -> Image.Image:
        image = self._track(card, self._going_at(card, raining)).copy()
        d = ImageDraw.Draw(image)
        lane_h = (TRACK_BOTTOM - TRACK_TOP) / card.size
        height = round(min(40.0, lane_h * 1.15))
        xs = self._positions(card, result, t)
        for i, horse in enumerate(card.horses):
            pose = 0 if gate else (frame + i * 3) % POSES
            sprite = self._sprite(horse, i + 1, pose, height)
            feet = TRACK_TOP + (i + 1) * lane_h - 2
            x = round(xs[i] - sprite.width)
            y = round(feet - sprite.height)
            if x > W or x + sprite.width < 0:
                continue
            image.paste(sprite, (x, y), sprite)
            segment = int(t / max(result.splits[i][-1], 1e-9) * 10)
            if i in result.stumbles and result.stumbles[i] == segment and not gate:
                d.text((x + sprite.width / 2, y - 2), "¡!", font=_title(16), fill=RED, anchor="mb")
            if i in result.bolted and segment >= result.bolted[i] and not gate:
                for puff in range(3):
                    px = x - 6 - puff * 7
                    d.ellipse((px - 4, feet - 8, px + 4, feet), fill=(230, 220, 200))
        if gate:
            for i in range(card.size):
                top = TRACK_TOP + i * lane_h
                d.rectangle((START_X - 4, top + 2, START_X + 6, top + lane_h - 2), fill=SILVER)
                d.rectangle((START_X - 4, top + 2, START_X + 6, top + 6), fill=RED)
        if raining:
            for _ in range(36):
                rx = rng.uniform(0, W)
                ry = rng.uniform(TRACK_TOP - 10, TRACK_BOTTOM - 12)
                d.line((rx, ry, rx - 4, ry + 12), fill=(200, 215, 240), width=1)
        self._hud(d, card, result, t, gate=gate, raining=raining)
        return image

    def _hud(
        self,
        d: ImageDraw.ImageDraw,
        card: RaceCard,
        result: RaceResult,
        t: float,
        *,
        gate: bool,
        raining: bool = False,
    ) -> None:
        """Marcador de abajo: la carrera, el terreno y la clasificación en vivo."""
        d.text((14, HUD_TOP + 6), card.name.upper(), font=_title(16), fill=GOLD)
        going = self._going_at(card, raining).label.upper()
        if raining:
            going += " · LLUEVE"
        d.text(
            (14, HUD_TOP + 26),
            f"{_thousands(card.distance)} m · {going}",
            font=_text(11),
            fill=MUTED,
        )
        if gate:
            d.text(
                (W - 14, HUD_TOP + 20), "EN LOS CAJONES", font=_title(18), fill=WHITE, anchor="rm"
            )
            return
        distance = card.distance
        ranking = sorted(
            range(card.size),
            key=lambda i: (-min(result.position_at(i, t, distance), distance), result.times[i]),
        )
        # Los que ya han entrado mantienen su puesto de llegada.
        finished = [i for i in result.order if result.times[i] <= t]
        running = [i for i in ranking if i not in finished]
        ranking = finished + running
        x = W - 14
        for place in range(min(4, card.size) - 1, -1, -1):
            horse = card.horses[ranking[place]]
            size = 24
            x -= size
            self._badge(d, x, HUD_TOP + 9, horse, ranking[place] + 1, size=size)
            x -= 4
            d.text((x, HUD_TOP + 21), f"{place + 1}º", font=_text(12), fill=WHITE, anchor="rm")
            x -= 30

    def _podium(
        self, card: RaceCard, result: RaceResult, last: Image.Image, *, photo: bool
    ) -> Image.Image:
        """Último fotograma: la meta con el podio encima (y la foto si hizo falta)."""
        image = last.copy()
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle((0, TRACK_TOP - 10, W, HUD_TOP), fill=(10, 10, 14, 175))
        image.paste(overlay, (0, 0), overlay)
        d = ImageDraw.Draw(image)
        d.text((24, TRACK_TOP + 4), "LLEGADA", font=_title(28), fill=WHITE)
        medals = (GOLD, SILVER, BRONZE)
        for place, index in enumerate(result.order[:3]):
            y = TRACK_TOP + 48 + place * 68
            horse = card.horses[index]
            d.ellipse((24, y, 64, y + 40), fill=medals[place], outline=INK, width=2)
            d.text((44, y + 21), str(place + 1), font=_title(24), fill=INK, anchor="mm")
            self._badge(d, 76, y + 2, horse, index + 1, size=36)
            name = horse.name if len(horse.name) <= 24 else horse.name[:23] + "…"
            d.text((124, y + 2), name, font=_text(18), fill=WHITE)
            if place == 0:
                note = "Ganador"
            else:
                behind = result.lengths_behind(index, card.distance)
                note = f"a {margin_text(behind)}"
            d.text((124, y + 26), note, font=_text(12), fill=MUTED)
        if photo:
            self._photo(image, card, result)
        return image

    def _photo(self, image: Image.Image, card: RaceCard, result: RaceResult) -> None:
        """Foto-finish: la meta en sepia en el instante en que entra el ganador."""
        t = result.times[result.winner]
        moment = self._frame(
            card,
            result,
            t,
            frame=0,
            gate=False,
            raining=result.rained,
            rng=random.Random(0),
        )
        crop = moment.crop((FINISH_X - 90, TRACK_TOP - 8, FINISH_X + 40, TRACK_BOTTOM))
        sepia = ImageOps.colorize(ImageOps.grayscale(crop), (40, 26, 12), (250, 232, 196))
        x, y = W - 150, TRACK_TOP - 8
        image.paste(sepia, (x, y))
        w, h = sepia.size
        d = ImageDraw.Draw(image)
        d.rectangle((x - 3, y - 3, x + w + 2, y + h + 2), outline=WHITE, width=3)
        d.rectangle((x, y + h - 24, x + w, y + h), fill=INK)
        d.text((x + w / 2, y + h - 12), "FOTO-FINISH", font=_title(16), fill=GOLD, anchor="mm")

    # -- Codificación ------------------------------------------------------------------------

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.convert("RGB").quantize(colors=200, method=Image.Quantize.FASTOCTREE).save(
            buffer, format="PNG", optimize=True
        )
        return buffer.getvalue()


def frame_times(card: RaceCard, result: RaceResult) -> tuple[list[float], list[bool]]:
    """Segundo de carrera de cada fotograma del GIF y si va a cámara lenta.

    Los primeros `GATE_FRAMES` son los cajones cerrados (segundo 0). Luego la
    carrera avanza a ritmo fijo hasta que entra el tercero; si hay
    foto-finish, los últimos metros del ganador y del segundo van a
    `SLOW_MOTION` veces menos velocidad. Lo comparten el dibujo con Pillow y
    el del navegador (`bot.services.horses_scene`).
    """
    winner_time = result.times[result.winner]
    anim = GRAND_PRIX_SECONDS if card.grand_prix else RACE_SECONDS
    scale = winner_time / (anim * 1000 / FRAME_MS)  # segundos de carrera por fotograma
    third = result.times[result.order[min(2, card.size - 1)]]
    end = third + winner_time * 0.03
    photo = photo_finish(result, card.distance)
    slow_from = winner_time * 0.985
    slow_to = result.times[result.order[1]] + winner_time * 0.003
    times: list[float] = [0.0] * GATE_FRAMES
    slow_flags: list[bool] = [False] * GATE_FRAMES
    t = 0.0
    while t < end:
        slow = photo and slow_from <= t <= slow_to
        t += scale / SLOW_MOTION if slow else scale
        times.append(min(t, end))
        slow_flags.append(slow)
    return times, slow_flags


def rain_start(result: RaceResult) -> float | None:
    """Segundo en que empieza a llover (cuando el ganador cierra el tramo de la lluvia)."""
    if not result.rained:
        return None
    return result.splits[result.order[0]][RAIN_SEGMENT]


def screen_positions(
    card: RaceCard, result: RaceResult, t: float, *, start_x: float = START_X
) -> list[float]:
    """X de pantalla del hocico de cada caballo a los `t` segundos de carrera.

    Cámara virtual: el primero avanza a ritmo fijo de los cajones (`start_x`)
    a la meta y los demás van detrás a la distancia real que les saca
    (`PX_PER_M`).
    """
    distance = card.distance
    meters = [result.position_at(i, t, distance) for i in range(card.size)]
    lead = min(max(meters), distance)
    lead_x = start_x + (FINISH_X - start_x) * lead / distance
    return [lead_x - (lead - m) * PX_PER_M for m in meters]
