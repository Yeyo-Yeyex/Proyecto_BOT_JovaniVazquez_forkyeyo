"""Dibujo de las máquinas de Botes (el Volcán, la Mina y el Olimpo): GIF y PNG.

Estilo de tragaperras de casino ilustrada: todo dibujado, con piezas gordas,
borde oscuro grueso y brillo. Cada máquina tiene su escenario:

- **Volcán:** fondo magenta con estampado de lava y un volcán morado echando lava
  detrás del rótulo «VOLCÁN» (Luckiest Guy, rellena de lava agrietada), marco de
  templo morado con arco y filo de oro, casillas de rodillo magenta y la recogida
  con chorros de lava.
- **Mina:** roca oscura con vetas de oro, la boca de un túnel con raíles detrás del
  rótulo, vigas de madera a los lados, antorchas y raíles abajo; marco de madera,
  casillas de roca y la recogida con lluvia de oro.
- **Olimpo:** cielo con nubes, un frontón de mármol con el rayo de Zeus detrás del
  rótulo, columnas a los lados, marco de mármol y oro, casillas de azul lapislázuli
  y la recogida con rayos.

Los símbolos son PNG de `assets/botes`; los del Volcán son anteriores y los de la
Mina y el Olimpo salen de `docs/dibujar_botes.py`.

Una máquina nueva necesita su `Style`, su fondo en `BACKDROPS`, su escenario en
`SCENES`, su rótulo en `TITLES` y sus símbolos `<máquina>_pay0…5`, `_wild` y
`_collect`.

Una tirada del juego base es un GIF de ~2 s: los cinco rodillos caen y paran de
izquierda a derecha con rebote. Si el recogedor está en el rodillo 1 y ya se
ven monedas, el 5 gira más tiempo y su ventana parpadea. Después se marcan los
premios y la recogida, y sale el cartel con lo ganado.

En el bonus cada tirada es un GIF corto: las casillas vacías giran y lo que
cae aparece con un golpe. Los misteriosos se ven un momento antes de
revelarse. El modo turbo se salta los GIF y manda solo el PNG final.

Las imágenes se montan en RGB. El PNG final va a color completo (~120 KB) y es
lo único que se manda en turbo. En el GIF cada fotograma lleva su propia paleta
de 255 colores sacada de lo que cambia en él (las fotos de los símbolos no caben
en una paleta común) y solo guarda los píxeles que cambian
(`bot.utils.gif.local_palette_gif`). Una tirada base cuesta ~0,6 s de CPU
fuera del event loop y pesa 300-450 KB; una tirada del bonus, ~150-250 KB.
Fondo, símbolos y casillas se preparan una vez por máquina (unos pocos MB en
memoria; el pico al dibujar ronda los 50 MB).

Las monedas se dibujan
aquí y se distinguen por la forma, no solo por el color
(círculo verde, hexágono azul, estrella roja), también con deuteranopia.
"""

from __future__ import annotations

import io
import math
import random
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from bot.services.hold_win import (
    CELLS,
    CHIP_WEIGHTS,
    COIN_VALUES,
    GRAND_COINS,
    MAJOR_COINS,
    MINI_COINS,
    REELS,
    ROWS,
    THEMES,
    BaseSpin,
    BonusStep,
    Cell,
    Kind,
    Tier,
    cell_index,
    to_amount,
)
from bot.utils.gif import local_palette_gif

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "botes"
FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
)

# -- Medidas ------------------------------------------------------------------------

CELL = 66
SYMBOL = 54
GAP = 5
PITCH = CELL + GAP
MARGIN = 16
GRID_W = REELS * CELL + (REELS - 1) * GAP
GRID_H = ROWS * CELL + (ROWS - 1) * GAP
WIDTH = GRID_W + MARGIN * 2
#: Rótulo de la máquina (como el de los pachinkos): «JOVANI VÁZQUEZ» y el nombre.
TITLE_Y = 6
TITLE_H = 46
HEADER_Y = TITLE_Y + TITLE_H + 6
HEADER_H = 52
GRID_Y = HEADER_Y + HEADER_H + 10
FOOTER_Y = GRID_Y + GRID_H + 12
FOOTER_H = 46
HEIGHT = FOOTER_Y + FOOTER_H + 12

FRAME_MS = 100
FINAL_FRAME_MS = 60_000
#: Fotograma en el que para cada rodillo, y el del último con anticipación.
STOP_FRAMES = (4, 5, 6, 7, 8)
ANTICIPATION_STOP = 16
TRAVEL = (6, 7, 8, 9, 10)
ANTICIPATION_TRAVEL = 18
OVERSHOOT = 0.16
BOUNCE_FRAMES = 2
BLUR_SPEED = 0.45
BLUR_PIXELS = 14


def _font(size: int) -> ImageFont.FreeTypeFont:
    return _cached_font(size)


@lru_cache(maxsize=32)
def _cached_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def short_amount(amount: int) -> str:
    """Cantidad corta para que quepa en una moneda: `950`, `1,2k`, `35k`, `1,5M`."""
    if amount < 1_000:
        return str(amount)
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if amount >= divisor:
            value = amount / divisor
            text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            text = text.rstrip("0").rstrip(".") if "." in text else text
            return text.replace(".", ",") + suffix
    return str(amount)


# -- Estilos de cada máquina ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Style:
    """Colores, materiales y efecto de una máquina.

    Attributes:
        cells: Material de las casillas (`_cell_tile`); de momento solo
            `"reel"`, el rodillo de casino.
        title, title_glow: Relleno y halo del rótulo.
        effect: Recogida: `lightning` (rayos), `lava` (chorros) o cualquier otro
            (lluvia de estrellas de oro).
    """

    top: tuple[int, int, int]
    bottom: tuple[int, int, int]
    frame: tuple[int, int, int]
    frame_dark: tuple[int, int, int]
    cells: str
    glow: tuple[int, int, int]
    title: tuple[int, int, int]
    title_glow: tuple[int, int, int]
    effect: str
    plaques: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] = (
        ((150, 160, 178), (60, 66, 80)),
        ((255, 205, 70), (120, 76, 10)),
        ((205, 120, 255), (70, 20, 110)),
    )
    #: Casilla llena y vacía (arriba y abajo del degradado) y su filo, por defecto las del Volcán.
    cell_full: tuple[tuple[int, int, int], tuple[int, int, int]] = ((190, 50, 150), (96, 14, 96))
    cell_empty: tuple[tuple[int, int, int], tuple[int, int, int]] = ((70, 10, 70), (40, 4, 44))
    cell_rims: tuple[tuple[int, int, int], tuple[int, int, int]] = ((120, 30, 110), (90, 20, 90))


STYLES: dict[str, Style] = {
    "volcan": Style(
        top=(255, 70, 110),
        bottom=(120, 0, 50),
        frame=(255, 196, 60),
        frame_dark=(90, 14, 80),
        cells="reel",
        glow=(255, 200, 60),
        title=(255, 230, 90),
        title_glow=(255, 90, 0),
        effect="lava",
        plaques=(
            ((255, 196, 60), (200, 40, 120)),
            ((255, 196, 60), (230, 90, 20)),
            ((255, 196, 60), (110, 30, 150)),
        ),
    ),
    "mina": Style(
        top=(104, 76, 56),
        bottom=(66, 46, 34),
        frame=(214, 150, 70),
        frame_dark=(36, 22, 14),
        cells="reel",
        glow=(255, 190, 70),
        title=(255, 224, 110),
        title_glow=(255, 130, 20),
        effect="gold",
        plaques=(
            ((255, 205, 90), (176, 84, 32)),
            ((255, 205, 90), (96, 112, 142)),
            ((255, 205, 90), (196, 128, 12)),
        ),
        cell_full=((92, 70, 58), (42, 32, 28)),
        cell_empty=((44, 33, 28), (24, 17, 15)),
        cell_rims=((150, 108, 62), (70, 50, 36)),
    ),
    "olimpo": Style(
        top=(60, 120, 232),
        bottom=(198, 224, 255),
        frame=(255, 210, 90),
        frame_dark=(12, 24, 78),
        cells="reel",
        glow=(255, 236, 120),
        title=(255, 240, 150),
        title_glow=(120, 190, 255),
        effect="lightning",
        plaques=(
            ((255, 210, 90), (120, 66, 200)),
            ((255, 210, 90), (30, 130, 236)),
            ((255, 210, 90), (24, 70, 176)),
        ),
        cell_full=((62, 100, 196), (22, 42, 116)),
        cell_empty=((26, 42, 104), (10, 18, 58)),
        cell_rims=((140, 178, 244), (44, 66, 140)),
    ),
}

GOLD = (255, 205, 70)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

TIER_COLORS = {
    Tier.GREEN: ((40, 190, 100), (170, 250, 200), (12, 90, 44)),
    Tier.BLUE: ((40, 120, 235), (170, 210, 255), (14, 50, 120)),
    Tier.RED: ((230, 50, 50), (255, 190, 120), (120, 14, 14)),
}


# -- Formas -------------------------------------------------------------------------


def _gradient(size: tuple[int, int], top: tuple[int, ...], bottom: tuple[int, ...]) -> Image.Image:
    width, height = size
    t = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    a = np.array(top, dtype=np.float32)[None, None, :]
    b = np.array(bottom, dtype=np.float32)[None, None, :]
    column = a + (b - a) * t
    pixels = np.repeat(column, width, axis=1)
    return Image.fromarray(pixels.clip(0, 255).astype(np.uint8), "RGB")


def _polygon(cx: float, cy: float, radius: float, sides: int, rotation: float) -> list:
    return [
        (
            cx + radius * math.cos(rotation + 2 * math.pi * i / sides),
            cy + radius * math.sin(rotation + 2 * math.pi * i / sides),
        )
        for i in range(sides)
    ]


def _star(cx: float, cy: float, outer: float, inner: float, points: int) -> list:
    coords = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = -math.pi / 2 + math.pi * i / points
        coords.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return coords


def _text_fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int) -> ImageFont:
    size = start
    while size > 9:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 1
    return _font(9)


def _outlined_text(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int] = WHITE,
    stroke: tuple[int, int, int] = BLACK,
    width: int = 2,
) -> None:
    draw.text(
        center, text, font=font, fill=fill, anchor="mm", stroke_width=width, stroke_fill=stroke
    )


@lru_cache(maxsize=512)
def coin_image(tier: Tier, text: str, size: int = SYMBOL) -> Image.Image:
    """Moneda dibujada con su valor: círculo verde, hexágono azul o estrella roja."""
    scale = 3
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    base, light, dark = TIER_COLORS[tier]
    c = big / 2
    if tier == Tier.GREEN:
        draw.ellipse((2 * scale, 2 * scale, big - 2 * scale, big - 2 * scale), fill=dark)
        draw.ellipse((6 * scale, 6 * scale, big - 6 * scale, big - 6 * scale), fill=base)
        draw.ellipse(
            (11 * scale, 11 * scale, big - 11 * scale, big - 11 * scale),
            outline=light,
            width=2 * scale,
        )
    elif tier == Tier.BLUE:
        draw.polygon(_polygon(c, c, c - scale, 6, -math.pi / 2), fill=WHITE)
        draw.polygon(_polygon(c, c, c - 4 * scale, 6, -math.pi / 2), fill=dark)
        draw.polygon(_polygon(c, c, c - 7 * scale, 6, -math.pi / 2), fill=base)
        draw.polygon(_polygon(c, c - 6 * scale, c / 2.6, 6, -math.pi / 2), fill=light + (110,))
    else:
        draw.polygon(_star(c, c, c - scale, c * 0.62, 8), fill=GOLD)
        draw.polygon(_star(c, c, c - 5 * scale, c * 0.55, 8), fill=dark)
        draw.polygon(_star(c, c, c - 8 * scale, c * 0.5, 8), fill=base)
        draw.ellipse((c - c * 0.42, c - c * 0.42, c + c * 0.42, c + c * 0.42), fill=light + (70,))
    font = _text_fit(draw, text, int(big * 0.78), int(big * 0.36))
    _outlined_text(draw, (c, c + scale), text, font, width=3 * scale)
    return image.resize((size, size), Image.Resampling.LANCZOS)


@lru_cache(maxsize=16)
def chip_image(name: str, size: int = SYMBOL) -> Image.Image:
    """Ficha de bote del juego base: `MINI` plateada o `MAJOR` dorada."""
    scale = 3
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    rim, face = ((200, 210, 225), (120, 132, 150)) if name == "mini" else (GOLD, (196, 124, 20))
    draw.ellipse((3 * scale, 3 * scale, big - 3 * scale, big - 3 * scale), fill=rim)
    for i in range(12):
        angle = 2 * math.pi * i / 12
        x = big / 2 + (big / 2 - 7 * scale) * math.cos(angle)
        y = big / 2 + (big / 2 - 7 * scale) * math.sin(angle)
        draw.ellipse((x - 3 * scale, y - 3 * scale, x + 3 * scale, y + 3 * scale), fill=WHITE)
    draw.ellipse((11 * scale, 11 * scale, big - 11 * scale, big - 11 * scale), fill=face)
    label = name.upper()
    font = _text_fit(draw, label, int(big * 0.6), int(big * 0.22))
    _outlined_text(draw, (big / 2, big / 2), label, font, width=2 * scale)
    return image.resize((size, size), Image.Resampling.LANCZOS)


# -- Piezas por máquina --------------------------------------------------------------


def _vertical_blur(image: Image.Image, radius: int) -> Image.Image:
    """Desenfoque vertical: el de un rodillo a toda velocidad."""
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    padded = np.pad(pixels, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    summed = np.cumsum(padded, axis=0)
    summed = np.concatenate([np.zeros_like(summed[:1]), summed], axis=0)
    window = 2 * radius + 1
    blurred = (summed[window:] - summed[:-window]) / window
    return Image.fromarray(blurred.clip(0, 255).astype(np.uint8), "RGB")


def cell_xy(index: int) -> tuple[int, int]:
    """Esquina superior izquierda de una casilla en la imagen."""
    reel, row = divmod(index, ROWS)
    return MARGIN + reel * PITCH, GRID_Y + row * PITCH


@dataclass(slots=True)
class _Kit:
    """Lo precalculado de una máquina: fondo, casillas y símbolos."""

    style: Style
    background: Image.Image
    cell_bg: Image.Image
    empty_bg: Image.Image
    symbols: dict[str, Image.Image]
    cells: dict[object, Image.Image] = field(default_factory=dict)
    blurred: dict[object, Image.Image] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Media:
    """Imágenes de una tirada: GIF (vacío en turbo), PNG final y duración."""

    gif: bytes
    png: bytes
    seconds: float


@dataclass(frozen=True, slots=True)
class Panel:
    """Lo que se ve arriba (botes) y abajo (maletines) en el juego base.

    Attributes:
        mini, major, grand: Valor de cada bote en Y$ a la apuesta actual.
        cases: `(monedas, tamaño)` del maletín verde, azul y rojo.
    """

    mini: int
    major: int
    grand: int
    cases: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class BonusPanel:
    """Marcadores del bonus.

    Attributes:
        name: Nombre del bonus.
        won: Botes ya alcanzados (`"mini"`, `"major"`, `"grand"`).
    """

    name: str
    mini: int
    major: int
    grand: int
    respins_left: int
    reset_value: int
    multiplier: int
    coins: int
    won: tuple[str, ...] = ()
    maximized: bool = False


@dataclass(frozen=True, slots=True)
class Banner:
    """Cartel grande sobre la rejilla: título (opcional) y cantidad."""

    title: str | None
    amount: str
    tier: int = 0  # 0 normal, 1 grande, 2 mega, 3 épico


class HoldWinRenderer:
    """Dibuja las máquinas de Botes con piezas precalculadas.

    Es seguro llamarlo desde varios hilos: cada máquina se prepara una vez con
    un cerrojo y después solo se lee (las cachés de casillas se llenan con el
    cerrojo puesto).
    """

    def __init__(self, assets: Path = ASSETS) -> None:
        self._assets = assets
        self._lock = threading.RLock()
        self._kits: dict[str, _Kit] = {}

    # -- Preparación ----------------------------------------------------------------

    def _kit(self, theme: str) -> _Kit:
        with self._lock:
            kit = self._kits.get(theme)
            if kit is None:
                kit = self._build_kit(theme)
                self._kits[theme] = kit
            return kit

    def _load(self, name: str) -> Image.Image:
        image = Image.open(self._assets / f"{name}.png").convert("RGBA")
        return image.resize((SYMBOL, SYMBOL), Image.Resampling.LANCZOS)

    def _build_kit(self, theme: str) -> _Kit:
        style = STYLES[theme]
        symbols = {
            name: self._load(f"{theme}_{name}")
            for name in ("pay0", "pay1", "pay2", "pay3", "pay4", "pay5", "wild", "collect")
        }
        for name in ("ticket", "extra", "instant", "max", "mystery"):
            symbols[name] = self._load(name)
        kit = _Kit(
            style=style,
            background=self._background(theme, style),
            cell_bg=_cell_tile(style, empty=False),
            empty_bg=_cell_tile(style, empty=True),
            symbols=symbols,
        )
        return kit

    def _background(self, theme: str, style: Style) -> Image.Image:
        """Fondo ilustrado, marco y rótulo de la máquina.

        Se dibuja al doble de tamaño y se reduce: los bordes y las curvas salen
        suaves. Solo se hace una vez por máquina.
        """
        big = _illustrated_backdrop(theme, style, (WIDTH * S, HEIGHT * S))
        SCENES[theme](big, style, random.Random(theme))  # misma decoración siempre
        image = big.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        TITLES[theme](image, THEMES[theme].title.upper(), style, self._assets / "fonts")
        return image.convert("RGB")

    def scene_backdrop(self, theme: str) -> bytes:
        """El mismo fondo, marco y rótulo, al doble (PNG): el de la escena de Node.

        La escena (`hold_win_scene`) pinta encima los rodillos, los paneles y los efectos.
        """
        style = STYLES[theme]
        big = _illustrated_backdrop(theme, style, (WIDTH * S, HEIGHT * S))
        SCENES[theme](big, style, random.Random(theme))
        TITLES[theme](big, THEMES[theme].title.upper(), style, self._assets / "fonts", k=S)
        return self._png(big.convert("RGB"))

    # -- Casillas -------------------------------------------------------------------

    def _symbol_for(self, kit: _Kit, cell: Cell, stake: int) -> Image.Image:
        if cell.kind == Kind.PAY:
            return kit.symbols[f"pay{cell.symbol}"]
        if cell.kind == Kind.COIN:
            assert cell.tier is not None
            return coin_image(cell.tier, short_amount(to_amount(cell.value, stake)))
        if cell.kind == Kind.CHIP:
            return chip_image(str(cell.symbol))
        if cell.kind in (Kind.WILD, Kind.COLLECT):
            return kit.symbols[cell.kind]
        return self._modifier(kit, cell)

    @staticmethod
    def _badge(base: Image.Image, text: str, color: tuple[int, int, int]) -> Image.Image:
        image = base.copy()
        draw = ImageDraw.Draw(image)
        font = _text_fit(draw, text, SYMBOL - 6, 20)
        _outlined_text(draw, (SYMBOL / 2, SYMBOL - 12), text, font, fill=color, width=3)
        return image

    def _modifier(self, kit: _Kit, cell: Cell) -> Image.Image:
        if cell.kind == Kind.TICKET:
            return self._badge(kit.symbols["ticket"], f"+×{cell.value}", GOLD)
        if cell.kind == Kind.EXTRA:
            return self._badge(kit.symbols["extra"], "+1", (140, 255, 160))
        if cell.kind == Kind.INSTANT:
            factor = f"{cell.value / 10:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            return self._badge(kit.symbols["instant"], f"×{factor}", WHITE)
        if cell.kind == Kind.MAXIMIZER:
            return self._badge(kit.symbols["max"], "MAX", GOLD)
        if cell.kind in (Kind.MYSTERY_RED, Kind.MYSTERY_BLUE):
            return self._mystery(kit.symbols["mystery"], cell.kind == Kind.MYSTERY_RED)
        raise ValueError(f"Casilla sin dibujo: {cell.kind}")

    @staticmethod
    def _mystery(question: Image.Image, red: bool) -> Image.Image:
        """Misterioso: rojo en círculo, azul en rombo (forma distinta, no solo color)."""
        image = Image.new("RGBA", (SYMBOL, SYMBOL), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if red:
            draw.ellipse((2, 2, SYMBOL - 3, SYMBOL - 3), fill=(200, 30, 40), outline=GOLD, width=3)
        else:
            c = SYMBOL / 2
            draw.polygon(_polygon(c, c, c - 2, 4, 0), fill=(30, 90, 220), outline=WHITE)
        q = question.resize((40, 40), Image.Resampling.LANCZOS)
        image.paste(q, (11, 11), q)
        return image

    def _cell_image(self, kit: _Kit, cell: Cell | None, stake: int, *, blurred: bool = False):
        """Casilla completa (fondo + símbolo), con caché por máquina."""
        key = (cell, stake if cell is not None and cell.kind == Kind.COIN else 0)
        cache = kit.blurred if blurred else kit.cells
        with self._lock:
            found = cache.get(key)
            if found is not None:
                return found
            if cell is None:
                image = kit.empty_bg.copy()
            else:
                image = kit.cell_bg.copy()
                symbol = self._symbol_for(kit, cell, stake)
                offset = (CELL - SYMBOL) // 2
                image.alpha_composite(symbol, (offset, offset))
            if blurred:
                # Desenfoque fuerte en vertical y suave en horizontal: parece
                # movimiento y el GIF pesa algo menos que con la casilla nítida.
                image = _vertical_blur(image, BLUR_PIXELS).filter(ImageFilter.BoxBlur(2))
                image = image.convert("RGBA")
            if len(cache) > 600:
                cache.clear()
            cache[key] = image
            return image

    # -- Paneles --------------------------------------------------------------------

    def _plaques(
        self,
        image: Image.Image,
        values: tuple[int, int, int],
        *,
        style: Style,
        won: Sequence[str] = (),
        maximized: bool = False,
    ) -> None:
        """Placas de los botes Mini, Major y Grand en la cabecera, con los colores de la máquina."""
        draw = ImageDraw.Draw(image, "RGBA")
        width = (WIDTH - 2 * MARGIN - 2 * 8) // 3
        specs = tuple(
            (label, rim, face)
            for label, (rim, face) in zip(("MINI", "MAJOR", "GRAND"), style.plaques, strict=True)
        )
        for i, ((label, rim, face), value) in enumerate(zip(specs, values, strict=True)):
            name = label.lower()
            x0 = MARGIN + i * (width + 8)
            box = (x0, HEADER_Y, x0 + width, HEADER_Y + HEADER_H)
            fill = face
            if name in won:
                fill = tuple(min(255, c + 70) for c in face)
            draw.rounded_rectangle(box, radius=12, fill=fill + (235,), outline=rim, width=3)
            if maximized and name in ("mini", "major"):
                draw.rounded_rectangle(
                    (box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2), radius=14, outline=WHITE
                )
            _outlined_text(draw, ((box[0] + box[2]) / 2, HEADER_Y + 14), label, _font(13), rim)
            text = f"{short_amount(value)} Y$"
            font = _text_fit(draw, text, width - 10, 20)
            _outlined_text(draw, ((box[0] + box[2]) / 2, HEADER_Y + 35), text, font)

    def _cases(self, image: Image.Image, cases: Sequence[tuple[int, int]], style: Style) -> None:
        """Maletines con su barra de progreso y la forma de su moneda."""
        draw = ImageDraw.Draw(image, "RGBA")
        width = (WIDTH - 2 * MARGIN - 2 * 8) // 3
        for i, (tier, (coins, size)) in enumerate(zip(Tier, cases, strict=True)):
            x0 = MARGIN + i * (width + 8)
            box = (x0, FOOTER_Y, x0 + width, FOOTER_Y + FOOTER_H)
            base, light, dark = TIER_COLORS[tier]
            draw.rounded_rectangle(
                box, radius=10, fill=style.frame_dark + (220,), outline=base, width=2
            )
            icon = coin_image(tier, "", 30)
            image.paste(icon, (x0 + 6, FOOTER_Y + 8), icon)
            bar = (x0 + 42, FOOTER_Y + 26, x0 + width - 8, FOOTER_Y + 36)
            draw.rounded_rectangle(bar, radius=5, fill=(255, 255, 255, 40))
            fraction = min(1.0, coins / size) if size else 0
            if fraction > 0:
                end = bar[0] + max(10, int((bar[2] - bar[0]) * fraction))
                draw.rounded_rectangle((bar[0], bar[1], end, bar[3]), radius=5, fill=light)
            text = f"{coins}/{size}"
            draw.text(
                (x0 + 42, FOOTER_Y + 6),
                text,
                font=_font(14),
                fill=WHITE,
                stroke_width=2,
                stroke_fill=BLACK,
            )

    def _bonus_footer(self, image: Image.Image, panel: BonusPanel, style: Style) -> None:
        """Tiradas que quedan, multiplicador y monedas hacia los botes."""
        draw = ImageDraw.Draw(image, "RGBA")
        box = (MARGIN, FOOTER_Y, WIDTH - MARGIN, FOOTER_Y + FOOTER_H)
        draw.rounded_rectangle(
            box, radius=10, fill=style.frame_dark + (225,), outline=style.frame, width=2
        )
        # Tiradas: rombos llenos (quedan) y huecos.
        x = MARGIN + 12
        for i in range(panel.reset_value):
            filled = i < panel.respins_left
            points = _polygon(x + 8, FOOTER_Y + 23, 9, 4, 0)
            draw.polygon(points, fill=style.glow if filled else None, outline=WHITE)
            x += 20
        draw.text((MARGIN + 12, FOOTER_Y + 30), "", font=_font(10), fill=WHITE)
        # Multiplicador.
        mx = MARGIN + 12 + 6 * 20 + 6
        text = f"×{panel.multiplier}"
        _outlined_text(draw, (mx + 26, FOOTER_Y + 23), text, _font(24), GOLD, width=3)
        # Monedas hacia los botes.
        bar = (mx + 64, FOOTER_Y + 28, WIDTH - MARGIN - 12, FOOTER_Y + 38)
        draw.rounded_rectangle(bar, radius=5, fill=(255, 255, 255, 40))
        span = bar[2] - bar[0]
        fill_end = bar[0] + int(span * min(1, panel.coins / GRAND_COINS))
        if panel.coins:
            draw.rounded_rectangle((bar[0], bar[1], fill_end, bar[3]), radius=5, fill=style.glow)
        for goal in (MINI_COINS, MAJOR_COINS, GRAND_COINS):
            gx = bar[0] + span * goal // GRAND_COINS
            draw.line((gx - 1, bar[1] - 4, gx - 1, bar[3] + 2), fill=WHITE, width=2)
        draw.text(
            (bar[0], FOOTER_Y + 5),
            f"{panel.coins}/{GRAND_COINS} monedas",
            font=_font(14),
            fill=WHITE,
            stroke_width=2,
            stroke_fill=BLACK,
        )

    # -- Efectos --------------------------------------------------------------------

    @staticmethod
    def _center(index: int) -> tuple[float, float]:
        x, y = cell_xy(index)
        return x + CELL / 2, y + CELL / 2

    def _effect(
        self, image: Image.Image, style: Style, source: int, targets: Sequence[int], t: float
    ) -> None:
        """Efecto de recogida de la máquina, de `source` a cada moneda, hasta `t` (0–1)."""
        draw = ImageDraw.Draw(image, "RGBA")
        sx, sy = self._center(source)
        for target in targets:
            tx, ty = self._center(target)
            rng = random.Random(source * 31 + target)
            if style.effect == "lightning":
                points = [(sx, sy)]
                steps = 7
                for k in range(1, steps):
                    f = k / steps
                    jitter = 14 * (1 - abs(0.5 - f) * 2)
                    points.append(
                        (
                            sx + (tx - sx) * f + rng.uniform(-jitter, jitter),
                            sy + (ty - sy) * f + rng.uniform(-jitter, jitter),
                        )
                    )
                points.append((tx, ty))
                cut = max(2, int(len(points) * t + 0.999))
                shown = points[:cut]
                # Halo azul ancho, rayo dorado, núcleo blanco y chispas que se salen del trazo.
                draw.line(shown, fill=(130, 190, 255, 60), width=13, joint="curve")
                draw.line(shown, fill=(255, 236, 120, 110), width=8, joint="curve")
                draw.line(shown, fill=(255, 226, 80, 235), width=5, joint="curve")
                draw.line(shown, fill=WHITE, width=2, joint="curve")
                for px, py in shown[1:-1]:
                    angle = rng.uniform(0, 2 * math.pi)
                    length = rng.uniform(6, 11)
                    twig = [
                        (px, py),
                        (px + length * math.cos(angle), py + length * math.sin(angle)),
                    ]
                    draw.line(twig, fill=(255, 236, 120, 230), width=2)
                    draw.line(twig, fill=WHITE, width=1)
                if cut == len(points):
                    for r, alpha in ((17, 50), (11, 80), (6, 130)):
                        draw.ellipse((tx - r, ty - r, tx + r, ty + r), fill=(255, 240, 150, alpha))
            elif style.effect == "lava":
                lift = 60 + rng.randint(0, 30)
                count = 14
                for k in range(int(count * t) + 1):
                    f = k / count
                    x = sx + (tx - sx) * f
                    y = sy + (ty - sy) * f - lift * 4 * f * (1 - f)
                    r = 7 - 3 * f
                    draw.ellipse(
                        (x - r - 2, y - r - 2, x + r + 2, y + r + 2), fill=(255, 80, 0, 150)
                    )
                    draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 210, 90, 230))
            else:
                # Lluvia de oro: monedas que vuelan en arco con su brillo y destellos.
                lift = 50 + rng.randint(0, 30)
                count = 14
                for k in range(int(count * t) + 1):
                    f = k / count
                    x = sx + (tx - sx) * f
                    y = sy + (ty - sy) * f - lift * 4 * f * (1 - f)
                    r = 8 - 3 * f
                    spin = abs(math.cos(k * 1.3))  # la moneda gira: ancha y estrecha
                    rx = max(2.5, r * (0.45 + 0.55 * spin))
                    draw.ellipse(
                        (x - r - 3, y - r - 3, x + r + 3, y + r + 3), fill=(255, 190, 40, 120)
                    )
                    draw.ellipse((x - rx - 1.5, y - r - 1.5, x + rx + 1.5, y + r + 1.5),
                                 fill=(110, 56, 8, 255))  # fmt: skip
                    draw.ellipse((x - rx, y - r, x + rx, y + r), fill=(255, 208, 60, 255))
                    draw.ellipse((x - rx * 0.6, y - r * 0.6, x + rx * 0.6, y + r * 0.6),
                                 fill=(255, 240, 150, 255))  # fmt: skip
                    draw.ellipse((x - rx * 0.55, y - r * 0.65, x - rx * 0.1, y - r * 0.25),
                                 fill=WHITE)  # fmt: skip
                    if k % 3 == 0:
                        sparkle = rng.choice((5, 7))
                        sx2, sy2 = x + rng.uniform(-9, 9), y + rng.uniform(-9, 9)
                        draw.polygon(_star(sx2, sy2, sparkle, sparkle / 3, 4), fill=WHITE)

    @staticmethod
    def _outline(image: Image.Image, cells: Sequence[int], color, width: int = 4) -> None:
        draw = ImageDraw.Draw(image)
        for index in cells:
            x, y = cell_xy(index)
            draw.rounded_rectangle(
                (x - 2, y - 2, x + CELL + 1, y + CELL + 1), radius=13, outline=color, width=width
            )

    def _banner(self, image: Image.Image, banner: Banner, style: Style, pulse: int = 0) -> None:
        """Cartel con lo ganado, centrado sobre la rejilla."""
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        height = 76 if banner.title is None else 112
        cy = GRID_Y + GRID_H // 2
        box = (MARGIN - 4, cy - height // 2, WIDTH - MARGIN + 4, cy + height // 2)
        # Colores que ya están en la paleta de la máquina: así no salen grises.
        colors = (style.frame, GOLD, style.title_glow, style.glow)
        rim = colors[min(banner.tier, 3)]
        draw.rounded_rectangle(box, radius=18, fill=(0, 0, 0, 235), outline=rim, width=5 + pulse)
        if banner.tier >= 2:
            rng = random.Random(banner.amount)
            for _ in range(24):
                x = rng.uniform(box[0], box[2])
                y = rng.uniform(box[1] - 8, box[3] + 8)
                size = rng.uniform(4, 9) + pulse
                draw.polygon(_star(x, y, size, size / 3, 4), fill=GOLD + (230,))
        y = cy
        if banner.title is not None:
            font = _text_fit(draw, banner.title, WIDTH - 2 * MARGIN - 20, 34 + 2 * pulse)
            _outlined_text(draw, (WIDTH / 2, cy - 22), banner.title, font, style.title, rim, 4)
            y = cy + 24
        font = _text_fit(draw, banner.amount, WIDTH - 2 * MARGIN - 20, 38 + 2 * pulse)
        _outlined_text(draw, (WIDTH / 2, y), banner.amount, font, WHITE, BLACK, width=4)
        image.alpha_composite(overlay)

    # -- Composición ----------------------------------------------------------------

    def _canvas(self, kit: _Kit) -> Image.Image:
        return kit.background.convert("RGBA")

    def _paint_grid(
        self,
        image: Image.Image,
        kit: _Kit,
        cells: Sequence[Cell | None],
        stake: int,
        *,
        dim: frozenset[int] = frozenset(),
    ) -> None:
        for index, cell in enumerate(cells):
            tile = self._cell_image(kit, cell, stake)
            image.alpha_composite(tile, cell_xy(index))
        if dim:
            shade = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 120))
            for index in dim:
                image.alpha_composite(shade, cell_xy(index))

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    # -- Juego base -----------------------------------------------------------------

    def _base_final(
        self,
        kit: _Kit,
        spin: BaseSpin,
        stake: int,
        panel: Panel,
        *,
        highlight: bool,
        effect_t: float | None = None,
        banner: Banner | None = None,
        pulse: int = 0,
    ) -> Image.Image:
        image = self._canvas(kit)
        self._plaques(image, (panel.mini, panel.major, panel.grand), style=kit.style)
        self._cases(image, panel.cases, kit.style)
        win_cells: set[int] = set()
        if highlight:
            for win in spin.wins:
                win_cells |= win.cells
            if spin.collectors:
                win_cells |= {i for i, _c in spin.coins}
                win_cells |= set(spin.collector_cells)
        dim = frozenset(range(CELLS)) - frozenset(win_cells) if win_cells else frozenset()
        self._paint_grid(image, kit, spin.grid, stake, dim=dim)
        if highlight and win_cells:
            self._outline(image, sorted(win_cells), kit.style.glow if pulse else GOLD)
        if effect_t is not None and spin.collectors:
            for source in spin.collector_cells:
                self._effect(image, kit.style, source, [i for i, _c in spin.coins], effect_t)
        if banner is not None:
            self._banner(image, banner, kit.style, pulse)
        return image

    def base_still(
        self,
        theme: str,
        spin: BaseSpin,
        *,
        stake: int,
        panel: Panel,
        highlight: bool = False,
        banner: Banner | None = None,
    ) -> bytes:
        """PNG de la máquina parada (al abrirla, en turbo o tras el Auto)."""
        kit = self._kit(theme)
        image = self._base_final(kit, spin, stake, panel, highlight=highlight, banner=banner)
        return self._png(image.convert("RGB"))

    def render_base(
        self,
        theme: str,
        spin: BaseSpin,
        *,
        stake: int,
        panel: Panel,
        banner: Banner | None = None,
        turbo: bool = False,
        rng: random.Random | None = None,
    ) -> Media:
        """Animación y PNG final de una tirada del juego base.

        Args:
            panel: Botes y maletines tras la tirada.
            banner: Cartel de lo ganado (o `None` si no se gana nada).
            turbo: Solo el PNG final, sin GIF.
        """
        kit = self._kit(theme)
        wins = bool(spin.wins or spin.collect_points)
        # El cartel solo sale en la animación: en la imagen final taparía la tirada.
        final = self._base_final(kit, spin, stake, panel, highlight=wins)
        final_png = self._png(final.convert("RGB"))
        if turbo:
            return Media(gif=b"", png=final_png, seconds=0.0)

        rng = rng or random.Random()
        stop_frames = list(STOP_FRAMES)
        travels = list(TRAVEL)
        if spin.anticipation:
            stop_frames[-1] = ANTICIPATION_STOP
            travels[-1] = ANTICIPATION_TRAVEL
        strips = []
        for reel in range(REELS):
            final_cells = [spin.grid[cell_index(reel, row)] for row in range(ROWS)]
            fillers = [_filler(rng, reel) for _ in range(travels[reel] + ROWS + 1)]
            strips.append(final_cells + fillers)

        static = self._canvas(kit)
        self._plaques(static, (panel.mini, panel.major, panel.grand), style=kit.style)
        self._cases(static, panel.cases, kit.style)
        frames: list[Image.Image] = []
        last = max(stop_frames)
        previous = [float(travels[r]) for r in range(REELS)]
        for frame_index in range(last + 1):
            image = static.copy()
            for reel in range(REELS):
                position = _reel_position(frame_index, stop_frames[reel], travels[reel])
                speed = abs(previous[reel] - position)
                previous[reel] = position
                self._paint_reel(image, kit, reel, strips[reel], position, speed, stake)
            if spin.anticipation and stop_frames[-2] <= frame_index < last and frame_index % 4 < 2:
                x, _y = cell_xy(cell_index(REELS - 1, 0))
                ImageDraw.Draw(image).rounded_rectangle(
                    (x - 4, GRID_Y - 4, x + CELL + 3, GRID_Y + GRID_H + 3),
                    radius=14,
                    outline=kit.style.glow,
                    width=5,
                )
            frames.append(image)
        durations = [FRAME_MS] * len(frames)

        if wins:
            lit = self._base_final(kit, spin, stake, panel, highlight=True)
            lit2 = self._base_final(kit, spin, stake, panel, highlight=True, pulse=1)
            frames += [lit, lit2, lit, lit2]
            durations += [120, 120, 120, 120]
            if spin.collectors:
                for t in (0.25, 0.5, 0.75, 1.0):
                    frames.append(
                        self._base_final(kit, spin, stake, panel, highlight=True, effect_t=t)
                    )
                    durations.append(80)
            if banner is not None:
                frames.append(
                    self._base_final(
                        kit, spin, stake, panel, highlight=True, banner=banner, pulse=1
                    )
                )
                durations.append(220)
        frames.append(final)
        durations.append(FINAL_FRAME_MS)
        gif = local_palette_gif(frames, durations)
        return Media(gif=gif, png=final_png, seconds=sum(durations[:-1]) / 1000)

    def _paint_reel(
        self,
        image: Image.Image,
        kit: _Kit,
        reel: int,
        strip: Sequence[Cell],
        position: float,
        speed: float,
        stake: int,
    ) -> None:
        """Pinta la ventana de un rodillo con la tira en `position` (0 = parado)."""
        blurred = speed > BLUR_SPEED
        x = MARGIN + reel * PITCH
        window = Image.new("RGBA", (CELL, GRID_H), (0, 0, 0, 0))
        base = math.floor(position)
        shift = position - base
        for i in range(base - 1, base + ROWS + 2):
            if not 0 <= i < len(strip):
                continue
            y = round((i - position) * PITCH)
            if -CELL < y < GRID_H:
                tile = self._cell_image(kit, strip[i], stake, blurred=blurred)
                window.alpha_composite(tile, (0, max(0, y)), (0, max(0, -y)))
        del shift
        image.alpha_composite(window, (x, GRID_Y))

    # -- Bonus ----------------------------------------------------------------------

    def _bonus_image(
        self,
        kit: _Kit,
        board: Sequence[Cell | None],
        stake: int,
        panel: BonusPanel,
        *,
        pop: dict[int, float] | None = None,
        flash: frozenset[int] = frozenset(),
        spinning: dict[int, Cell] | None = None,
        banner: Banner | None = None,
        pulse: int = 0,
    ) -> Image.Image:
        image = self._canvas(kit)
        self._plaques(
            image,
            (panel.mini, panel.major, panel.grand),
            style=kit.style,
            won=panel.won,
            maximized=panel.maximized,
        )
        self._bonus_footer(image, panel, kit.style)
        for index in range(CELLS):
            x, y = cell_xy(index)
            if spinning is not None and index in spinning:
                tile = self._cell_image(kit, spinning[index], stake, blurred=True)
                image.alpha_composite(tile, (x, y))
                continue
            cell = board[index]
            if cell is None:
                image.alpha_composite(self._cell_image(kit, None, stake), (x, y))
                continue
            image.alpha_composite(kit.cell_bg, (x, y))
            symbol = self._symbol_for(kit, cell, stake)
            scale = (pop or {}).get(index, 1.0)
            if scale != 1.0:
                size = max(8, int(SYMBOL * scale))
                symbol = symbol.resize((size, size), Image.Resampling.BILINEAR)
            offset_x = x + (CELL - symbol.width) // 2
            offset_y = y + (CELL - symbol.height) // 2
            # Recortar si el golpe se sale de la casilla.
            image.alpha_composite(symbol, (max(0, offset_x), max(0, offset_y)))
            if cell.is_coin:
                ImageDraw.Draw(image).rounded_rectangle(
                    (x, y, x + CELL - 1, y + CELL - 1), radius=12, outline=kit.style.glow, width=2
                )
        if flash:
            glow = Image.new("RGBA", (CELL, CELL), (255, 255, 255, 110))
            for index in flash:
                image.alpha_composite(glow, cell_xy(index))
        if banner is not None:
            self._banner(image, banner, kit.style, pulse)
        return image

    def bonus_still(
        self,
        theme: str,
        board: Sequence[Cell | None],
        *,
        stake: int,
        panel: BonusPanel,
        banner: Banner | None = None,
    ) -> bytes:
        """PNG del bonus parado."""
        kit = self._kit(theme)
        image = self._bonus_image(kit, board, stake, panel, banner=banner)
        return self._png(image.convert("RGB"))

    def render_bonus_step(
        self,
        theme: str,
        before: Sequence[Cell | None],
        step: BonusStep,
        *,
        stake: int,
        panel_before: BonusPanel,
        panel_after: BonusPanel,
        banner: Banner | None = None,
        turbo: bool = False,
        rng: random.Random | None = None,
    ) -> Media:
        """Una tirada del bonus: giran las vacías, cae lo nuevo y se aplica.

        Args:
            before: Rejilla antes de la tirada (con los modificadores viejos).
            panel_before, panel_after: Marcadores antes y después.
            banner: Cartel final si el bonus acaba en esta tirada.
        """
        kit = self._kit(theme)
        final = self._bonus_image(kit, step.board, stake, panel_after, banner=banner)
        final_png = self._png(final.convert("RGB"))
        if turbo:
            return Media(gif=b"", png=final_png, seconds=0.0)

        rng = rng or random.Random()
        # Los modificadores de la tirada anterior se van antes de girar.
        board = [cell if cell is not None and cell.is_coin else None for cell in before]
        empty = [i for i, cell in enumerate(board) if cell is None]
        frames: list[Image.Image] = []
        durations: list[int] = []
        landed = {landing.index: landing for landing in step.landings}
        spin_frames = 9
        for k in range(spin_frames):
            spinning = {
                i: _bonus_filler(rng, stake)
                for i in empty
                if not (i in landed and k >= spin_frames - 2 - (i % ROWS) // 2)
            }
            current = list(board)
            for i in landed:
                if i not in spinning:
                    current[i] = _shown(landed[i])
            frames.append(
                self._bonus_image(kit, current, stake, panel_before, spinning=spinning or None)
            )
            durations.append(FRAME_MS)
        # Lo que cae: un golpe (grande y a su tamaño) y, si era misterioso, se revela.
        current = list(board)
        for i, landing in landed.items():
            current[i] = _shown(landing)
        if landed:
            for scale in (1.3, 1.12, 1.0):
                frames.append(
                    self._bonus_image(
                        kit,
                        current,
                        stake,
                        panel_before,
                        pop={i: scale for i in landed},
                        flash=frozenset(landed) if scale > 1.2 else frozenset(),
                    )
                )
                durations.append(90)
            if any(landing.mystery for landing in step.landings):
                durations[-1] = 350
                revealed = list(current)
                for i, landing in landed.items():
                    revealed[i] = landing.cell
                for scale in (0.6, 1.2, 1.0):
                    frames.append(
                        self._bonus_image(
                            kit,
                            revealed,
                            stake,
                            panel_before,
                            pop={i: scale for i, x in landed.items() if x.mystery},
                        )
                    )
                    durations.append(90)
                current = revealed
            if step.instant != 10:
                coins = frozenset(
                    i for i, c in enumerate(step.board) if c is not None and c.is_coin
                )
                frames.append(self._bonus_image(kit, current, stake, panel_before, flash=coins))
                durations.append(160)
        if banner is not None:
            for pulse in (1, 0, 1):
                frames.append(
                    self._bonus_image(
                        kit, step.board, stake, panel_after, banner=banner, pulse=pulse
                    )
                )
                durations.append(170)
        frames.append(final)
        durations.append(FINAL_FRAME_MS)
        gif = local_palette_gif(frames, durations)
        return Media(gif=gif, png=final_png, seconds=sum(durations[:-1]) / 1000)


# -- Escenarios de cada máquina ---------------------------------------------------------
#
# Todo se dibuja al doble de tamaño (`S`), así que las coordenadas van por 2.

S = 2


def _grid_box(pad: int) -> tuple[int, int, int, int]:
    """Caja de la rejilla, en coordenadas dobles, con `pad` píxeles (normales) de más."""
    return (
        (MARGIN - pad) * S,
        (GRID_Y - pad) * S,
        (MARGIN + GRID_W + pad) * S,
        (GRID_Y + GRID_H + pad) * S,
    )


def _glow_layer(image: Image.Image, painter, radius: int) -> None:  # noqa: ANN001
    """Pinta con `painter(draw)` en una capa aparte, la desenfoca y la suma (halo)."""
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    painter(ImageDraw.Draw(layer))
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius)))
    image.alpha_composite(layer)


def _halo(image: Image.Image, box: tuple, color: tuple[int, int, int, int], radius: int) -> None:
    """Resplandor: una elipse de `color` desenfocada, sin el disco duro debajo."""
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(box, fill=color)
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius)))


def _illustrated_backdrop(theme: str, style: Style, size: tuple[int, int]) -> Image.Image:
    """Fondo ilustrado: degradado radial y un dibujo de fondo de la máquina."""
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    cx, cy = width / 2, height * 0.12
    dist = np.sqrt((xx - cx) ** 2 + ((yy - cy) * 0.8) ** 2) / (height * 0.9)
    t = np.clip(dist, 0, 1)[..., None]
    a = np.array(style.top, np.float32)
    b = np.array(style.bottom, np.float32)
    pixels = a + (b - a) * t
    image = Image.fromarray(pixels.astype(np.uint8), "RGB").convert("RGBA")
    BACKDROPS[theme](image, style)
    return image


def _volcano_backdrop(image: Image.Image, style: Style) -> None:
    """Estampado de lava enfriada y un volcán morado con el cráter echando lava."""
    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    # Ondas de lava enfriada de fondo, como un estampado.
    for row, y in enumerate(range(-20 * S, height, 34 * S)):
        shift = (row % 2) * 30 * S
        for x in range(-40 * S + shift, width, 60 * S):
            draw.arc((x, y, x + 60 * S, y + 40 * S), 200, 340, fill=(255, 140, 170, 60),
                     width=4 * S)  # fmt: skip
    # Volcán morado detrás del rótulo, con el cráter echando lava.
    top_y = 30 * S
    cone = [(-60 * S, GRID_Y * S + 40 * S), (width * 0.36, top_y), (width * 0.64, top_y),
            (width + 60 * S, GRID_Y * S + 40 * S)]  # fmt: skip
    _glow_layer(
        image,
        lambda d: d.ellipse(
            (width * 0.25, -60 * S, width * 0.75, top_y + 70 * S), fill=(255, 210, 60, 200)
        ),
        30,
    )
    draw.polygon(cone, fill=(80, 20, 104))
    draw.polygon(
        [(width * 0.36, top_y), (width * 0.64, top_y), (width * 0.75, GRID_Y * S),
         (width * 0.25, GRID_Y * S)],
        fill=(120, 44, 150),
    )  # fmt: skip
    draw.line(cone[:3] + [cone[3]], fill=(46, 6, 40), width=5 * S)
    draw.rectangle((width * 0.36, top_y - 4 * S, width * 0.64, top_y + 10 * S),
                   fill=(255, 220, 70))  # fmt: skip
    rng = random.Random("volcan-drips")
    for k in range(7):
        x = width * (0.37 + 0.26 * k / 6)
        length = rng.uniform(20, 70) * S
        draw.rounded_rectangle((x - 5 * S, top_y, x + 5 * S, top_y + length), radius=5 * S,
                               fill=(255, 210, 60))  # fmt: skip
        draw.ellipse((x - 8 * S, top_y + length - 8 * S, x + 8 * S, top_y + length + 8 * S),
                     fill=(255, 190, 40))  # fmt: skip


def _volcano_scene(image: Image.Image, style: Style, rng: random.Random) -> None:
    """Marco de templo morado con borde de oro y arco arriba, como un mueble de casino."""
    w, h = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    for _ in range(40):
        x, y = rng.uniform(0, w), rng.uniform(GRID_Y * S, h)
        r = rng.choice((1, 2, 3)) * S / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 220, 120, 160))
    box = _grid_box(9)
    purple, deep, gold = (110, 30, 140), (46, 6, 40), style.frame
    # Arco sobre la rejilla.
    arch = (box[0] + 20 * S, box[1] - 26 * S, box[2] - 20 * S, box[1] + 30 * S)
    draw.rounded_rectangle(
        (arch[0] - 4 * S, arch[1] - 4 * S, arch[2] + 4 * S, arch[3]), radius=26 * S, fill=deep
    )
    draw.rounded_rectangle(arch, radius=22 * S, fill=purple, outline=gold, width=3 * S)
    _glow_layer(
        image,
        lambda d: d.rounded_rectangle(box, radius=14 * S, outline=(255, 120, 40, 255), width=6 * S),
        10,
    )
    draw.rounded_rectangle(
        (box[0] - 4 * S, box[1] - 4 * S, box[2] + 4 * S, box[3] + 4 * S), radius=18 * S, fill=deep
    )
    draw.rounded_rectangle(box, radius=14 * S, fill=purple, outline=gold, width=4 * S)
    draw.rounded_rectangle(
        (box[0] + 5 * S, box[1] + 5 * S, box[2] - 5 * S, box[3] - 5 * S),
        radius=10 * S,
        fill=(54, 6, 50),
    )
    # Remaches de oro en las esquinas del arco.
    for x in (arch[0] + 12 * S, arch[2] - 12 * S):
        draw.ellipse((x - 4 * S, arch[1] + 8 * S, x + 4 * S, arch[1] + 16 * S), fill=gold)


def _title_font(fonts: Path, name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(fonts / name), size)


def _title(
    image: Image.Image,
    text: str,
    style: Style,
    fonts: Path,
    *,
    fill: tuple[tuple[int, int, int], tuple[int, int, int]] = ((255, 246, 140), (255, 70, 10)),
    edge: tuple[int, int, int] = (46, 6, 40),
    crack: tuple[int, int, int] | None = (150, 20, 10),
    k: int = 1,
) -> None:
    """Rótulo en letras gordas (Luckiest Guy): degradado `fill`, borde `edge`, halo y grietas.

    Por defecto, el «VOLCÁN»: lava agrietada con borde morado. `k` es la escala de la imagen
    (2 para el fondo de la escena de Node, que se pinta al doble).
    """
    font = _title_font(fonts, "luckiest-guy.woff", 46 * k)
    cx, cy = WIDTH * k / 2, (TITLE_Y + TITLE_H / 2 + 4) * k
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((cx, cy), text, font=font, anchor="mm", fill=style.title_glow,
                              stroke_width=8 * k, stroke_fill=style.title_glow)  # fmt: skip
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(7 * k)))
    outline = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(outline).text((cx, cy + 3 * k), text, font=font, anchor="mm", fill=edge,
                                 stroke_width=5 * k, stroke_fill=edge)  # fmt: skip
    image.alpha_composite(outline)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).text((cx, cy), text, font=font, anchor="mm", fill=255)
    lava = Image.new("RGBA", image.size, (0, 0, 0, 0))
    band = _gradient((WIDTH * k, TITLE_H * k), *fill).convert("RGBA")
    lava.paste(band, (0, TITLE_Y * k))
    # Grietas oscuras dentro de las letras.
    cracks = ImageDraw.Draw(lava)
    rng = random.Random("grietas")
    for _ in range(14 if crack else 0):
        x = rng.uniform(cx - 120 * k, cx + 120 * k)
        y = rng.uniform((TITLE_Y + 8) * k, (TITLE_Y + TITLE_H - 6) * k)
        cracks.line(
            [(x, y), (x + rng.uniform(-8, 8) * k, y + rng.uniform(4, 10) * k),
             (x + rng.uniform(-10, 10) * k, y + rng.uniform(10, 18) * k)],
            fill=crack, width=k,
        )  # fmt: skip
    image.paste(lava, (0, 0), mask)
    shine = Image.new("L", image.size, 0)
    ImageDraw.Draw(shine).rectangle(
        (0, TITLE_Y * k, WIDTH * k, (TITLE_Y + TITLE_H * 0.38) * k), fill=90
    )
    image.paste(
        Image.new("RGBA", image.size, WHITE + (255,)), (0, 0), ImageChops.multiply(mask, shine)
    )


# -- Mina ---------------------------------------------------------------------------


def _wood(draw: ImageDraw.ImageDraw, box: tuple, *, vertical: bool, rng: random.Random) -> None:
    """Viga de madera con borde oscuro, vetas y luz por un lado."""
    x0, y0, x1, y1 = box
    draw.rectangle((x0 - S, y0 - S, x1 + S, y1 + S), fill=(34, 18, 8))
    draw.rectangle(box, fill=(150, 94, 42))
    if vertical:
        draw.rectangle((x0, y0, x0 + (x1 - x0) * 0.34, y1), fill=(176, 114, 54))
        for _ in range(8):
            x = rng.uniform(x0 + S, x1 - S)
            y = rng.uniform(y0, y1 - 30 * S)
            draw.line((x, y, x + rng.uniform(-S, S), y + rng.uniform(14, 40) * S),
                      fill=(104, 62, 26), width=S)  # fmt: skip
    else:
        draw.rectangle((x0, y0, x1, y0 + (y1 - y0) * 0.34), fill=(176, 114, 54))
        for _ in range(8):
            y = rng.uniform(y0 + S, y1 - S)
            x = rng.uniform(x0, x1 - 30 * S)
            draw.line((x, y, x + rng.uniform(14, 40) * S, y + rng.uniform(-S, S)),
                      fill=(104, 62, 26), width=S)  # fmt: skip


def _mine_backdrop(image: Image.Image, style: Style) -> None:
    """Roca oscura con vetas de oro y la boca de un túnel con raíles detrás del rótulo."""
    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    rng = random.Random("mina-roca")
    for _ in range(38):
        cx, cy, r = rng.uniform(0, width), rng.uniform(0, height), rng.uniform(22, 62) * S
        angles = sorted(rng.uniform(0, 2 * math.pi) for _ in range(7))
        shape = [(cx + r * math.cos(a) * rng.uniform(0.7, 1.1),
                  cy + r * math.sin(a) * rng.uniform(0.7, 1.1)) for a in angles]  # fmt: skip
        tone = rng.choice(((0, 0, 0, 46), (255, 200, 140, 34), (0, 0, 0, 28)))
        draw.polygon(shape, fill=tone, outline=(0, 0, 0, 90))
    veins = []
    for _ in range(14):
        x, y = rng.uniform(0, width), rng.uniform(0, height * 0.9)
        points = [(x, y)]
        for _ in range(rng.randint(4, 7)):
            x += rng.uniform(-26, 26) * S
            y += rng.uniform(8, 24) * S
            points.append((x, y))
        veins.append(points)
    _glow_layer(
        image, lambda d: [d.line(v, fill=(255, 190, 60, 150), width=6 * S) for v in veins], 8
    )
    for points in veins:
        draw.line(points, fill=(255, 214, 90, 230), width=2 * S, joint="curve")
        draw.line(points, fill=(255, 250, 200, 200), width=max(1, S // 2), joint="curve")
    # Boca del túnel: oscuridad al fondo, una luz y raíles en perspectiva.
    # Solo se ve lo que queda por encima de las placas y a los lados del rótulo.
    cx, floor = width / 2, 58 * S
    horizon = 14 * S
    mouth = (width * 0.2, 8 * S, width * 0.8, GRID_Y * S + 12 * S)
    draw.rounded_rectangle(mouth, radius=60 * S, fill=(14, 8, 6))
    _glow_layer(
        image,
        lambda d: d.ellipse(
            (cx - 70 * S, horizon - 30 * S, cx + 70 * S, horizon + 22 * S), fill=(255, 196, 80, 120)
        ),
        14,
    )
    # Vigas de apeo de la boca: dos postes con jabalcones y un dintel que cruza todo.
    for x in (width * 0.26, width * 0.74):
        draw.line((x, 10 * S, x, floor), fill=(34, 18, 8), width=7 * S)
        draw.line((x, 10 * S, x, floor), fill=(122, 76, 36), width=5 * S)
    for x0, x1 in ((width * 0.26, width * 0.31), (width * 0.74, width * 0.69)):
        draw.line((x0, 30 * S, x1, 12 * S), fill=(34, 18, 8), width=6 * S)
        draw.line((x0, 30 * S, x1, 12 * S), fill=(134, 84, 38), width=4 * S)
    # Marco de madera de la boca.
    draw.rounded_rectangle(mouth, radius=60 * S, outline=(34, 18, 8), width=12 * S)
    draw.rounded_rectangle(mouth, radius=60 * S, outline=(146, 90, 40), width=8 * S)


def _big(image: Image.Image, painter, x: float, y: float, k: float) -> None:  # noqa: ANN001
    """Pinta `painter(capa, x, y)` en una capa, la amplía `k` veces y la pone en (`x`, `y`)."""
    w, h = 120 * S, 70 * S
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    painter(layer, w // 2, 52 * S)
    layer = layer.resize((int(w * k), int(h * k)), Image.Resampling.LANCZOS)
    image.alpha_composite(layer, (int(x - layer.width / 2), int(y - 52 * S * k)))


def _nugget(draw: ImageDraw.ImageDraw, x: float, y: float, r: float) -> None:
    """Pepita angulosa de oro con una cara clara y otra oscura (`r` en píxeles dobles)."""
    shape = [(x - r, y), (x - r * 0.6, y - r * 0.8), (x + r * 0.3, y - r), (x + r, y - r * 0.2),
             (x + r * 0.7, y + r * 0.6), (x - r * 0.3, y + r * 0.7)]  # fmt: skip
    draw.polygon(shape, fill=(222, 140, 20), outline=(38, 20, 8))
    draw.polygon([shape[0], shape[1], shape[2], (x, y - r * 0.1)], fill=(255, 238, 130))
    draw.polygon([shape[3], shape[4], shape[5], (x, y - r * 0.1)], fill=(190, 108, 12))
    draw.line(shape + [shape[0]], fill=(38, 20, 8), width=S)


def _cart(image: Image.Image, x: float, y: float) -> None:
    """Vagoneta cargada de oro sobre un trozo de raíl (`x`, `y` = centro del suelo)."""
    _halo(image, (x - 26 * S, y - 40 * S, x + 26 * S, y - 6 * S), (255, 190, 60, 110), 9)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((x - 30 * S, y + 6 * S, x + 30 * S, y + 9 * S), fill=(190, 196, 212),
                   outline=(40, 40, 52))  # fmt: skip
    for k in range(-2, 3):
        draw.rectangle((x + k * 12 * S - 3 * S, y + 9 * S, x + k * 12 * S + 3 * S, y + 13 * S),
                       fill=(122, 76, 36), outline=(34, 18, 8))  # fmt: skip
    for dx, dy, r in ((-9, -17, 7), (0, -21, 8), (9, -17, 7), (-3, -15, 6)):
        _nugget(draw, x + dx * S, y + dy * S, r * S)
    body = [(x - 20 * S, y - 14 * S), (x + 20 * S, y - 14 * S), (x + 15 * S, y + 1 * S),
            (x - 15 * S, y + 1 * S)]  # fmt: skip
    draw.polygon(body, fill=(112, 96, 92), outline=(24, 14, 10))
    draw.polygon([body[0], body[1], (x + 18 * S, y - 9 * S), (x - 18 * S, y - 9 * S)],
                 fill=(166, 150, 144))  # fmt: skip
    draw.line(body + [body[0]], fill=(24, 14, 10), width=S)
    draw.line((x - 20 * S, y - 14 * S, x + 20 * S, y - 14 * S), fill=(255, 214, 90), width=S)
    for wx in (-9, 9):
        draw.ellipse((x + (wx - 5) * S, y - 3 * S, x + (wx + 5) * S, y + 7 * S), fill=(60, 62, 76),
                     outline=(20, 20, 26), width=S)  # fmt: skip
        draw.ellipse((x + (wx - 1.5) * S, y + 0.5 * S, x + (wx + 1.5) * S, y + 3.5 * S),
                     fill=(220, 226, 240))  # fmt: skip


def _gold_pile(image: Image.Image, x: float, y: float) -> None:
    """Montón de pepitas con dos lingotes delante (`x`, `y` = centro del suelo)."""
    _halo(image, (x - 26 * S, y - 40 * S, x + 26 * S, y - 4 * S), (255, 190, 60, 110), 9)
    draw = ImageDraw.Draw(image, "RGBA")
    for dx, dy, r in ((-12, -6, 9), (10, -8, 10), (-2, -20, 11), (-16, -16, 7), (14, -22, 7)):
        _nugget(draw, x + dx * S, y + dy * S, r * S)
    for dx in (-10, 10):
        bar = (x + (dx - 11) * S, y + 2 * S, x + (dx + 11) * S, y + 12 * S)
        draw.polygon([(bar[0] + 3 * S, bar[1]), (bar[2] - 3 * S, bar[1]),
                      (bar[2], bar[1] + 4 * S),
                      (bar[0], bar[1] + 4 * S)], fill=(255, 244, 170),
                     outline=(38, 20, 8))  # fmt: skip
        draw.rectangle((bar[0], bar[1] + 4 * S, bar[2], bar[3]), fill=(236, 168, 30),
                       outline=(38, 20, 8))  # fmt: skip


def _mine_scene(image: Image.Image, style: Style, rng: random.Random) -> None:
    """Vigas de madera, antorchas, marco de tablones y raíles abajo, como una galería de mina."""
    w, h = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    for _ in range(36):
        x, y = rng.uniform(0, w), rng.uniform(GRID_Y * S, h)
        r = rng.choice((1, 2, 3)) * S / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 210, 120, 110))
    # Vigas: dos postes y un dintel arriba, con jabalcones en las esquinas.
    _wood(draw, (0, 0, w, 6 * S), vertical=False, rng=rng)
    _big(image, lambda im, x, y: _cart(im, x, y), 50 * S, 58 * S, 1.4)
    _big(image, lambda im, x, y: _gold_pile(im, x, y), w - 50 * S, 58 * S, 1.4)
    # Marco de tablones alrededor de la rejilla.
    box = _grid_box(7)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle(
        (box[0] - 4 * S, box[1] - 4 * S, box[2] + 4 * S, box[3] + 4 * S), radius=10 * S,
        fill=(34, 18, 8))  # fmt: skip
    draw.rounded_rectangle(box, radius=7 * S, fill=(150, 94, 42), outline=style.frame, width=S)
    draw.rounded_rectangle(
        (box[0], box[1], box[2], box[1] + 3 * S), radius=3 * S, fill=(184, 122, 58)
    )
    draw.rounded_rectangle((box[0] + 6 * S, box[1] + 6 * S, box[2] - 6 * S, box[3] - 6 * S),
                           radius=4 * S, fill=(24, 14, 10))  # fmt: skip
    # Vetas de la madera y juntas de las vigas, con remaches de hierro a lo largo.
    grain = random.Random("mina-marco")
    for _ in range(26):
        x = grain.uniform(box[0] + 8 * S, box[2] - 8 * S)
        for y in (box[1] + 1.5 * S, box[3] - 4.5 * S):
            draw.line((x, y, x + grain.uniform(8, 24) * S, y), fill=(112, 68, 28, 200), width=S)
    for y in (box[1] + 1.5 * S, box[3] - 4.5 * S):
        draw.line((box[0] + 8 * S, y + 1.5 * S, box[2] - 8 * S, y + 1.5 * S), fill=(104, 62, 26),
                  width=1)  # fmt: skip
    for x in range(int(box[0]) + 26 * S, int(box[2]) - 20 * S, 28 * S):
        for y in (box[1] + 3 * S, box[3] - 3 * S):
            draw.ellipse((x - 1.3 * S, y - 1.3 * S, x + 1.3 * S, y + 1.3 * S), fill=(214, 218, 230),
                         outline=(40, 40, 50))  # fmt: skip
    for y in range(int(box[1]) + 30 * S, int(box[3]) - 20 * S, 40 * S):
        for x in (box[0] + 3 * S, box[2] - 3 * S):
            draw.ellipse((x - 1.3 * S, y - 1.3 * S, x + 1.3 * S, y + 1.3 * S), fill=(214, 218, 230),
                         outline=(40, 40, 50))  # fmt: skip
    # Escuadras de hierro en las esquinas.
    for x, y, sx, sy in (
        (box[0], box[1], 1, 1), (box[2], box[1], -1, 1),
        (box[0], box[3], 1, -1), (box[2], box[3], -1, -1),
    ):  # fmt: skip
        plate = [(x - sx * 3 * S, y - sy * 3 * S), (x + sx * 17 * S, y - sy * 3 * S),
                 (x + sx * 17 * S, y + sy * 5 * S), (x + sx * 5 * S, y + sy * 5 * S),
                 (x + sx * 5 * S, y + sy * 17 * S), (x - sx * 3 * S, y + sy * 17 * S)]  # fmt: skip
        draw.polygon(plate, fill=(120, 128, 150), outline=(26, 26, 34))
        draw.line(plate[:3], fill=(210, 216, 232), width=S)
        draw.ellipse((x + sx * 1 * S - 1.6 * S, y + sy * 1 * S - 1.6 * S,
                      x + sx * 1 * S + 1.6 * S, y + sy * 1 * S + 1.6 * S), fill=(236, 240, 250),
                     outline=(30, 30, 40))  # fmt: skip
    # Raíles al pie, con sus traviesas.
    floor = h - 11 * S
    draw.rectangle((0, floor, w, h), fill=(30, 18, 10))
    for x in range(8 * S, w, 22 * S):
        draw.rectangle((x, floor + S, x + 12 * S, h), fill=(122, 76, 36), outline=(40, 22, 10))
    for y in (floor + 2 * S, floor + 7 * S):
        draw.rectangle((0, y, w, y + 2 * S), fill=(190, 196, 210), outline=(70, 74, 88))


# -- Olimpo -------------------------------------------------------------------------


def _cloud(draw: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    """Nube de bolas blancas."""
    for dx, dy, r in ((-1.2, 0.2, 0.7), (-0.5, -0.3, 0.95), (0.4, -0.45, 1.05), (1.2, 0.0, 0.8),
                      (0.0, 0.25, 1.1)):  # fmt: skip
        draw.ellipse((x + (dx - r) * size, y + (dy - r) * size, x + (dx + r) * size,
                      y + (dy + r) * size), fill=(255, 255, 255, 235))  # fmt: skip


def _olympus_backdrop(image: Image.Image, style: Style) -> None:
    """Cielo con haces de luz, nubes y estrellitas."""
    width, height = image.size
    rng = random.Random("olimpo-cielo")
    rays = Image.new("RGBA", image.size, (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays)
    far = height * 1.5
    for k in range(9):
        a0 = math.radians(60 + k * 7.5)
        a1 = a0 + math.radians(3)
        rd.polygon([(width / 2, -10 * S), (width / 2 + far * math.cos(a0), far * math.sin(a0)),
                    (width / 2 + far * math.cos(a1), far * math.sin(a1))],
                   fill=(255, 255, 255, 34))  # fmt: skip
    image.alpha_composite(rays)
    clouds = Image.new("RGBA", image.size, (0, 0, 0, 0))
    cd = ImageDraw.Draw(clouds)
    for _ in range(16):
        y = rng.uniform(height * 0.3, height) if rng.random() < 0.7 else rng.uniform(0, height)
        _cloud(cd, rng.uniform(0, width), y, rng.uniform(10, 22) * S)
    image.alpha_composite(clouds.filter(ImageFilter.GaussianBlur(2 * S)))
    draw = ImageDraw.Draw(image, "RGBA")
    for _ in range(26):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        r = rng.choice((1, 2)) * S / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 200))


def _bolt(cx: float, top: float, bottom: float, half: float) -> list[tuple[float, float]]:
    """Rayo en zigzag de `top` a `bottom`, de ancho `half` a cada lado."""
    h = bottom - top
    return [(cx + half * 0.7, top), (cx - half, top + h * 0.52), (cx - half * 0.1, top + h * 0.52),
            (cx - half * 0.8, bottom), (cx + half, top + h * 0.4),
            (cx + half * 0.15, top + h * 0.4),
            (cx + half * 1.3, top)]  # fmt: skip


def _meander(draw: ImageDraw.ImageDraw, box: tuple) -> None:
    """Greca griega (meandro) de azul noche por los cuatro lados del marco de mármol."""
    u, band = 8 * S, 4 * S  # largo de cada greca y alto de la cinta
    key = ((0, 1), (0, 0), (1, 0), (1, 0.7), (0.3, 0.7), (0.3, 0.35), (0.65, 0.35))
    ink = (36, 60, 150)
    x0, y0, x1, y1 = box
    inset = 3 * S
    for top in (True, False):
        y = y0 + inset if top else y1 - inset - band
        x = x0 + 8 * S
        while x + u <= x1 - 8 * S:
            pts = [(x + kx * u * 0.9, y + (ky if top else 1 - ky) * band) for kx, ky in key]
            draw.line(pts, fill=ink, width=3)
            x += u
    for left in (True, False):
        x = x0 + inset if left else x1 - inset - band
        y = y0 + 8 * S
        while y + u <= y1 - 8 * S:
            pts = [(x + (ky if left else 1 - ky) * band, y + kx * u * 0.9) for kx, ky in key]
            draw.line(pts, fill=ink, width=3)
            y += u


def _olympus_scene(image: Image.Image, style: Style, rng: random.Random) -> None:
    """Frontón de mármol con el rayo de Zeus, columnas, marco de mármol y oro, y nubes al pie."""
    w, h = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    # Frontón: mármol por fuera y un tímpano azul con el rayo dentro.
    roof = [(3 * S, 56 * S), (3 * S, 26 * S), (w / 2, 2 * S), (w - 3 * S, 26 * S),
            (w - 3 * S, 56 * S)]  # fmt: skip
    draw.polygon(roof, fill=(238, 242, 252))
    inset = [(8 * S, 53 * S), (8 * S, 28 * S), (w / 2, 8 * S), (w - 8 * S, 28 * S),
             (w - 8 * S, 53 * S)]  # fmt: skip
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).polygon(inset, fill=255)
    sky = Image.new("RGBA", image.size)
    sky.paste(_gradient((w, 60 * S), (70, 110, 220), (16, 28, 100)), (0, 0))
    image.paste(sky, (0, 0), mask)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.line(roof + [roof[0]], fill=(255, 210, 90), width=2 * S, joint="curve")
    draw.line(inset + [inset[0]], fill=(14, 24, 84), width=S, joint="curve")
    # Rayos de sol que salen del centro del frontón, recortados al tímpano.
    burst = Image.new("RGBA", image.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(burst)
    for k in range(15):
        a0 = math.radians(k * 12 + 2)
        a1 = a0 + math.radians(6)
        bd.polygon([(w / 2, 34 * S),
                    (w / 2 + 400 * S * math.cos(a0), 34 * S + 400 * S * math.sin(a0)),
                    (w / 2 + 400 * S * math.cos(a1), 34 * S + 400 * S * math.sin(a1))],
                   fill=(255, 226, 120, 170))  # fmt: skip
    image.paste(burst, (0, 0), ImageChops.multiply(mask, burst.getchannel("A")))
    _halo(image, (w / 2 - 105 * S, 10 * S, w / 2 + 105 * S, 52 * S), (255, 236, 140, 200), 16)
    bolt = _bolt(w / 2, 6 * S, 56 * S, 36 * S)
    _glow_layer(image, lambda d: d.polygon(bolt, fill=(255, 226, 90, 255)), 14)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.polygon(bolt, fill=(255, 240, 120), outline=(255, 190, 30), width=S)
    for sx in (w / 2 - 138 * S, w / 2 + 138 * S):
        small = _bolt(sx, 4 * S, 56 * S, 17 * S)
        _glow_layer(image, lambda d, small=small: d.polygon(small, fill=(255, 226, 90, 200)), 6)
        draw = ImageDraw.Draw(image, "RGBA")
        draw.polygon(small, fill=(255, 244, 150), outline=(255, 190, 30), width=S)
    # Cornisa con triglifos de oro.
    draw.rectangle((0, 54 * S, w, 59 * S), fill=(238, 242, 252), outline=(14, 24, 84))
    for x in range(10 * S, w, 24 * S):
        draw.rectangle((x, 55 * S, x + 6 * S, 58 * S), fill=(255, 210, 90))
    # Marco de mármol y oro alrededor de la rejilla.
    box = _grid_box(7)
    draw.rounded_rectangle(
        (box[0] - 4 * S, box[1] - 4 * S, box[2] + 4 * S, box[3] + 4 * S), radius=10 * S,
        fill=(14, 24, 84))  # fmt: skip
    draw.rounded_rectangle(
        box, radius=7 * S, fill=(238, 242, 252), outline=style.frame, width=2 * S
    )
    draw.rounded_rectangle((box[0] + 8 * S, box[1] + 8 * S, box[2] - 8 * S, box[3] - 8 * S),
                           radius=4 * S, fill=(10, 18, 60))  # fmt: skip
    _meander(draw, box)
    # Nubes por delante, al pie de la máquina.
    clouds = Image.new("RGBA", image.size, (0, 0, 0, 0))
    cd = ImageDraw.Draw(clouds)
    for x in range(0, w + 40 * S, 38 * S):
        _cloud(cd, x, h - 2 * S, 9 * S + (x // (38 * S)) % 3 * 2 * S)
    image.alpha_composite(clouds.filter(ImageFilter.GaussianBlur(S)))


def _veins(
    size: tuple[int, int], rng: random.Random, box: tuple[int, int, int, int], count: int,
    colors: Sequence[tuple[int, int, int, int]], scale: int = 1,
) -> Image.Image:  # fmt: skip
    """Capa con vetas que serpentean de lado a lado de `box` (oro, mármol), a escala `scale`."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = box
    for k in range(count):
        x, y = rng.uniform(x0 - 10 * scale, x1), rng.uniform(y0, y1)
        points = [(x, y)]
        for _ in range(rng.randint(4, 7)):
            x += rng.uniform(6, 18) * scale
            y = min(y1, max(y0, y + rng.uniform(-9, 9) * scale))
            points.append((x, y))
        width = rng.choice((1, 1, 2)) * scale
        draw.line(points, fill=colors[k % len(colors)], width=width, joint="curve")
    return layer


def _stone_title(
    image: Image.Image,
    text: str,
    style: Style,
    fonts: Path,
    *,
    stops: Sequence[tuple[float, tuple[int, int, int]]],
    edge: tuple[int, int, int],
    rim: tuple[int, int, int] | None,
    vein_colors: Sequence[tuple[int, int, int, int]],
    seed: str,
    size: int = 46,
    dy: int = 0,
    k: int = 1,
) -> None:
    """Rótulo como el del Volcán: letras gordas con relieve, borde grueso, sombra y halo.

    El relleno es un degradado de varias paradas (`stops`) con vetas encima, un filo
    de color (`rim`) entre el borde y la letra y un brillo en el tercio de arriba. `k` es la
    escala de la imagen, como en `_title`.
    """
    size *= k
    font = _title_font(fonts, "luckiest-guy.woff", size)
    cx, cy = WIDTH * k / 2, (TITLE_Y + TITLE_H / 2 + 4 + dy) * k
    canvas = image.size

    def stamp(offset: float, width: int, fill: tuple[int, ...]) -> Image.Image:
        layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((cx, cy + offset), text, font=font, anchor="mm", fill=fill,
                                   stroke_width=width, stroke_fill=fill)  # fmt: skip
        return layer

    image.alpha_composite(
        stamp(0, 8 * k, style.title_glow + (255,)).filter(ImageFilter.GaussianBlur(7 * k))
    )
    image.alpha_composite(
        stamp(7 * k, 8 * k, (0, 0, 0, 170)).filter(ImageFilter.GaussianBlur(1.5 * k))
    )
    image.alpha_composite(stamp(3 * k, 7 * k, edge + (255,)))
    if rim is not None:
        image.alpha_composite(stamp(0, 3 * k, rim + (255,)))
    mask = Image.new("L", canvas, 0)
    ImageDraw.Draw(mask).text((cx, cy), text, font=font, anchor="mm", fill=255)
    top, bottom = int(cy - size * 0.72), int(cy + size * 0.5)
    t = np.clip((np.arange(canvas[1]) - top) / (bottom - top), 0, 1)[:, None]
    face = np.zeros((canvas[1], 3), np.float32)
    for channel in range(3):
        face[:, channel] = np.interp(t[:, 0], [s[0] for s in stops], [s[1][channel] for s in stops])
    fill = Image.fromarray(np.repeat(face[:, None, :], canvas[0], axis=1).astype(np.uint8), "RGB")
    fill = fill.convert("RGBA")
    rng = random.Random(seed)
    fill.alpha_composite(
        _veins(
            canvas,
            rng,
            (int(cx - size * 1.9), top + 4 * k, int(cx + size * 1.9), bottom - 8 * k),
            11,
            vein_colors,
            k,
        )  # fmt: skip
    )
    image.paste(fill, (0, 0), mask)
    # Brillo arriba y una línea de luz en el borde de abajo de cada letra, para el relieve.
    shine = Image.new("L", canvas, 0)
    ImageDraw.Draw(shine).rectangle((0, top, WIDTH * k, top + (bottom - top) * 0.4), fill=96)
    image.paste(Image.new("RGBA", canvas, WHITE + (255,)), (0, 0), ImageChops.multiply(mask, shine))
    low = ImageChops.subtract(mask, ImageChops.offset(mask, 0, -3 * k))
    image.paste(
        Image.new("RGBA", canvas, (0, 0, 0, 255)), (0, 0), low.point(lambda v: v * 70 // 255)
    )


def _mine_title(image: Image.Image, text: str, style: Style, fonts: Path, k: int = 1) -> None:
    """«MINA»: letras de oro con vetas y brillo, borde de madera oscura y filo de cobre."""
    _stone_title(
        image, text, style, fonts,
        stops=((0.0, (255, 252, 190)), (0.45, (255, 205, 60)), (1.0, (196, 104, 10))),
        edge=(38, 20, 8), rim=(150, 78, 20),
        vein_colors=((170, 96, 10, 200), (255, 255, 220, 190), (210, 130, 20, 170)),
        seed="mina-rotulo", size=58, dy=2, k=k,
    )  # fmt: skip


def _olympus_title(image: Image.Image, text: str, style: Style, fonts: Path, k: int = 1) -> None:
    """«OLIMPO»: letras de mármol con vetas, borde azul noche y filo dorado."""
    _stone_title(
        image, text, style, fonts,
        stops=((0.0, (255, 255, 255)), (0.55, (226, 234, 250)), (1.0, (150, 170, 214))),
        edge=(10, 20, 70), rim=(255, 206, 80),
        vein_colors=((120, 140, 190, 120), (170, 186, 224, 130), (96, 112, 170, 100)),
        seed="olimpo-rotulo", size=52, dy=1, k=k,
    )  # fmt: skip


#: Fondo, escenario (marco y decoración) y rótulo de cada máquina.
BACKDROPS = {"volcan": _volcano_backdrop, "mina": _mine_backdrop, "olimpo": _olympus_backdrop}
SCENES = {"volcan": _volcano_scene, "mina": _mine_scene, "olimpo": _olympus_scene}
TITLES = {"volcan": _title, "mina": _mine_title, "olimpo": _olympus_title}


def rad_gradient(size: tuple[int, int], inner: tuple, outer: tuple) -> Image.Image:
    """Degradado radial desde el centro (un poco por arriba) hacia los bordes."""
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dist = np.sqrt((xx - width / 2) ** 2 + (yy - height * 0.4) ** 2) / (width * 0.75)
    t = np.clip(dist, 0, 1)[..., None]
    a, b = np.array(inner, np.float32), np.array(outer, np.float32)
    return Image.fromarray((a + (b - a) * t).astype(np.uint8), "RGB").convert("RGBA")


def _cell_tile(style: Style, *, empty: bool, big: bool = False) -> Image.Image:
    """Fondo de una casilla según el material de la máquina (llena o vacía en el bonus).

    `"reel"` es un rodillo de casino: un degradado con los colores de la máquina
    (`cell_full`, `cell_empty`) que oscurece hacia abajo, sin bordes marcados,
    para que cada columna parezca una tira continua. Con `big`, al doble (para la
    escena de Node).
    """
    if style.cells != "reel":
        raise ValueError(f"Material de casilla desconocido: {style.cells}")
    size = CELL * S
    top, bottom = style.cell_empty if empty else style.cell_full
    face = rad_gradient((size, size), top, bottom)
    rim = style.cell_rims[1] if empty else style.cell_rims[0]
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=3 * S, fill=255)
    tile.paste(face, (0, 0), mask)
    ImageDraw.Draw(tile).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=3 * S, outline=rim, width=S
    )
    return tile if big else tile.resize((CELL, CELL), Image.Resampling.LANCZOS)


def cell_tile_png(theme: str, *, empty: bool) -> bytes:
    """PNG de la casilla de `theme` al doble (llena o vacía), para la escena de Node."""
    buffer = io.BytesIO()
    _cell_tile(STYLES[theme], empty=empty, big=True).save(buffer, format="PNG")
    return buffer.getvalue()


def _shown(landing) -> Cell:
    """Lo que se ve al caer: el misterioso tapado o la casilla de verdad."""
    if landing.mystery:
        return Cell(landing.mystery)
    return landing.cell


def _filler(rng: random.Random, reel: int) -> Cell:
    from bot.services.hold_win import random_filler

    return random_filler(rng, reel)


_BONUS_FILLER_TIERS = (Tier.GREEN, Tier.BLUE, Tier.RED)


def _bonus_filler(rng: random.Random, stake: int) -> Cell:
    """Casilla que pasa girando en el bonus (no tiene que ver con lo que caerá)."""
    roll = rng.randrange(10)
    if roll < 6:
        tier = _BONUS_FILLER_TIERS[rng.randrange(3)]
        value = rng.choice([v for v, _w in COIN_VALUES[tier]])
        return Cell(Kind.COIN, tier=tier, value=value)
    if roll < 8:
        return Cell(Kind.MYSTERY_RED if roll == 6 else Kind.MYSTERY_BLUE)
    return Cell(Kind.CHIP, symbol=CHIP_WEIGHTS[rng.randrange(2)][0])


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def _reel_position(frame: int, stop_frame: int, travel: float) -> float:
    """Desplazamiento de la tira (en casillas) en un fotograma: de `travel` a 0, con rebote."""
    main_end = stop_frame - BOUNCE_FRAMES
    if frame >= stop_frame:
        return 0.0
    if frame >= main_end:
        t = (frame - main_end) / BOUNCE_FRAMES
        return -OVERSHOOT * (1 - t)
    t = frame / main_end
    return (travel + OVERSHOOT) * (1 - _ease_out(t)) - OVERSHOOT
