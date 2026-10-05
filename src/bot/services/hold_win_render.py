"""Dibujo de las máquinas de Botes (`volcan`, `olimpo`, `filon`): GIF y PNG.

Cada máquina tiene su rótulo de neón («JOVANI VÁZQUEZ» y el nombre, como los
pachinkos), su escenario, el material de sus casillas y su efecto propio, para
que se distingan de un vistazo:

- **Volcán**: cielo rojo con el cono en erupción, ríos de lava y ascuas; marco
  de magma con goterones; casillas de basalto oscuro con filo de lava; la
  recogida lanza chorros de lava.
- **Olimpo**: noche violeta con estrellas, nubes y rayos; columnas de mármol y
  marco de oro con greca griega; casillas de mármol blanco; la recogida son
  rayos.
- **Filón**: galería turquesa con cristales, vigas con guirnalda de bombillas y
  raíles; casillas de cajas de madera con clavos; la recogida es un reguero
  de chispas de oro.

Una tirada del juego base es un GIF de ~2 s: los cinco rodillos caen y paran de
izquierda a derecha con rebote. Si el recogedor está en el rodillo 1 y ya se
ven monedas, el 5 gira más tiempo y su ventana parpadea. Después se marcan los
premios y la recogida, y sale el cartel con lo ganado.

En el bonus cada tirada es un GIF corto: las casillas vacías giran y lo que
cae aparece con un golpe. Los misteriosos se ven un momento antes de
revelarse. El modo turbo se salta los GIF y manda solo el PNG final.

Las imágenes se montan en RGB y se pasan a una paleta fija por máquina, que se
calcula una vez. Todo lo caro (símbolos, fondos, paleta) se prepara la primera
vez y se reutiliza. Una tirada base cuesta ~0,3 s de CPU fuera del event loop y
pesa 150-350 KB de GIF (como el pachinko); una tirada del bonus, ~150 KB. El PNG
final ronda los 20 KB, y es lo único que se manda en turbo.

Los símbolos son emojis 3D de Noto (`assets/botes`, ver su `LICENSE.txt`). Las
monedas se dibujan aquí y se distinguen por la forma, no solo por el color
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
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
#: Colores de la paleta de cada máquina.
PALETTE_COLORS = 128
#: Índice de la paleta reservado para «igual que el fotograma anterior».
TRANSPARENT = 255


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
        cells: Material de las casillas: `"basalt"` (roca oscura con borde de
            lava), `"marble"` (mármol blanco con filo dorado) o `"wood"`
            (cajas de madera con clavos). Es lo que más distingue una máquina
            de otra a primera vista.
        title, title_glow: Relleno y halo del rótulo.
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


STYLES: dict[str, Style] = {
    "volcan": Style(
        top=(150, 30, 8),
        bottom=(14, 6, 6),
        frame=(255, 120, 20),
        frame_dark=(110, 30, 6),
        cells="basalt",
        glow=(255, 180, 50),
        title=(255, 214, 90),
        title_glow=(255, 70, 0),
        effect="lava",
    ),
    "olimpo": Style(
        top=(92, 58, 170),
        bottom=(22, 20, 74),
        frame=(240, 200, 90),
        frame_dark=(140, 104, 36),
        cells="marble",
        glow=(255, 236, 140),
        title=(255, 255, 255),
        title_glow=(120, 190, 255),
        effect="lightning",
    ),
    "filon": Style(
        top=(18, 54, 58),
        bottom=(8, 18, 22),
        frame=(150, 92, 44),
        frame_dark=(74, 42, 18),
        cells="wood",
        glow=(255, 214, 110),
        title=(255, 196, 70),
        title_glow=(60, 220, 200),
        effect="gold",
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
    """Lo precalculado de una máquina: fondo, casillas y paleta."""

    style: Style
    background: Image.Image
    cell_bg: Image.Image
    empty_bg: Image.Image
    symbols: dict[str, Image.Image]
    palette: Image.Image | None = None
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
            cell_bg=_cell_tile(style.cells, empty=False),
            empty_bg=_cell_tile(style.cells, empty=True),
            symbols=symbols,
        )
        kit.palette = self._palette(kit)
        return kit

    def _background(self, theme: str, style: Style) -> Image.Image:
        """Fondo, rótulo, marco y decoración fija de la máquina.

        Se dibuja al doble de tamaño y se reduce: los bordes y las curvas
        salen suaves. Solo se hace una vez por máquina.
        """
        big = _gradient((WIDTH * 2, HEIGHT * 2), style.top, style.bottom).convert("RGBA")
        rng = random.Random(theme)  # misma decoración siempre
        {"volcan": _volcano_scene, "olimpo": _olympus_scene, "filon": _mine_scene}[theme](
            big, style, rng
        )
        image = big.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        _title(image, THEMES[theme].title.upper(), style)
        return image.convert("RGB")

    def _palette(self, kit: _Kit) -> Image.Image:
        """Paleta común de la máquina, sacada de todas sus piezas juntas.

        El corte por mediana reparte los colores según cuántos píxeles hay de
        cada uno, así que las piezas pequeñas pero importantes (las monedas,
        los símbolos) se meten varias veces y el fondo a un cuarto de tamaño.
        Si no, una máquina marrón se quedaba sin verdes y la moneda verde
        salía azulada.
        """
        tiles: list[Image.Image] = []
        pieces = [kit.symbols[name] for name in sorted(kit.symbols)]
        coins = [coin_image(t, text) for t in Tier for text in ("10", "250", "1,5k")]
        chips = [chip_image("mini"), chip_image("major")]
        shade = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 120))
        for piece in pieces * 2 + coins * 3 + chips:
            tile = kit.cell_bg.copy()
            offset = (CELL - SYMBOL) // 2
            tile.alpha_composite(piece, (offset, offset))
            tiles.append(tile)
            dimmed = tile.copy()
            dimmed.alpha_composite(shade)
            tiles.append(dimmed)
        columns = 12
        rows = (len(tiles) + columns - 1) // columns
        small = kit.background.resize((WIDTH // 2, HEIGHT // 2))
        sample = Image.new("RGB", (columns * CELL, rows * CELL + small.height + 20), BLACK)
        for i, tile in enumerate(tiles):
            sample.paste(tile.convert("RGB"), ((i % columns) * CELL, (i // columns) * CELL))
        sample.paste(small, (0, rows * CELL))
        draw = ImageDraw.Draw(sample)
        # Colores de los carteles, los marcos de premio, el rótulo y los efectos,
        # en muestras grandes para que la paleta los guarde exactos (si no, el
        # blanco de los carteles salía grisáceo).
        swatches = (
            GOLD, WHITE, BLACK, kit.style.glow, kit.style.frame, kit.style.title,
            kit.style.title_glow, (255, 120, 30), (120, 200, 255),
        )  # fmt: skip
        for i, color in enumerate(swatches):
            x = small.width + 4 + (i % 4) * 40
            y = rows * CELL + (i // 4) * 30
            draw.rectangle((x, y, x + 39, y + 29), fill=color)
        return sample.quantize(colors=PALETTE_COLORS, method=Image.Quantize.MEDIANCUT)

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
        won: Sequence[str] = (),
        maximized: bool = False,
    ) -> None:
        """Placas de los botes Mini, Major y Grand en la cabecera."""
        draw = ImageDraw.Draw(image, "RGBA")
        width = (WIDTH - 2 * MARGIN - 2 * 8) // 3
        specs = (
            ("MINI", (150, 160, 178), (60, 66, 80)),
            ("MAJOR", GOLD, (120, 76, 10)),
            ("GRAND", (205, 120, 255), (70, 20, 110)),
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

    def _cases(self, image: Image.Image, cases: Sequence[tuple[int, int]]) -> None:
        """Maletines con su barra de progreso y la forma de su moneda."""
        draw = ImageDraw.Draw(image, "RGBA")
        width = (WIDTH - 2 * MARGIN - 2 * 8) // 3
        for i, (tier, (coins, size)) in enumerate(zip(Tier, cases, strict=True)):
            x0 = MARGIN + i * (width + 8)
            box = (x0, FOOTER_Y, x0 + width, FOOTER_Y + FOOTER_H)
            base, light, dark = TIER_COLORS[tier]
            draw.rounded_rectangle(box, radius=10, fill=(0, 0, 0, 150), outline=base, width=2)
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
        draw.rounded_rectangle(box, radius=10, fill=(0, 0, 0, 170), outline=style.frame, width=2)
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
                draw.line(points[:cut], fill=(255, 230, 90, 200), width=7)
                draw.line(points[:cut], fill=WHITE, width=3)
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
                count = 10
                for k in range(int(count * t) + 1):
                    f = k / count
                    x = sx + (tx - sx) * f + rng.uniform(-6, 6)
                    y = sy + (ty - sy) * f + rng.uniform(-6, 6)
                    size = rng.choice((5, 7, 9))
                    draw.polygon(_star(x, y, size, size / 3, 4), fill=(255, 230, 120, 230))
                    draw.polygon(_star(x, y, size / 2, size / 6, 4), fill=WHITE)

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

    def _quantize(self, kit: _Kit, image: Image.Image) -> Image.Image:
        assert kit.palette is not None
        return image.convert("RGB").quantize(palette=kit.palette, dither=Image.Dither.NONE)

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def _gif(self, kit: _Kit, frames: list[Image.Image], durations: list[int]) -> bytes:
        """GIF en el que cada fotograma solo guarda lo que cambia.

        Los píxeles iguales al fotograma anterior se marcan como transparentes
        (`TRANSPARENT`) y el GIF no borra lo anterior (`disposal=1`): se ve lo
        de debajo. Las zonas quietas quedan como largas tiras de un mismo
        índice, que el LZW del GIF comprime casi a cero. Así una tirada pesa la
        mitad que guardando cada fotograma entero.
        """
        assert kit.palette is not None
        palette = kit.palette.getpalette() or []
        palette = (palette + [0] * 768)[:768]
        images: list[Image.Image] = []
        previous: np.ndarray | None = None
        for frame in frames:
            current = np.asarray(self._quantize(kit, frame), dtype=np.uint8)
            if previous is None:
                out = current
            else:
                out = np.where(current == previous, TRANSPARENT, current).astype(np.uint8)
            previous = current
            image = Image.fromarray(out, "P")
            image.putpalette(palette)
            images.append(image)
        buffer = io.BytesIO()
        images[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=durations,
            loop=0,
            disposal=1,
            transparency=TRANSPARENT,
            optimize=False,
        )
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
        self._plaques(image, (panel.mini, panel.major, panel.grand))
        self._cases(image, panel.cases)
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
        return self._png(self._quantize(kit, image))

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
        final_png = self._png(self._quantize(kit, final))
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
        self._plaques(static, (panel.mini, panel.major, panel.grand))
        self._cases(static, panel.cases)
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
        gif = self._gif(kit, frames, durations)
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
        return self._png(self._quantize(kit, image))

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
        final_png = self._png(self._quantize(kit, final))
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
        gif = self._gif(kit, frames, durations)
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


def _bulbs(draw: ImageDraw.ImageDraw, box, color, every: int, rng: random.Random) -> None:  # noqa: ANN001
    """Bombillas alrededor de una caja, como en los muebles de los salones."""
    x0, y0, x1, y1 = box
    points = []
    for x in range(x0 + every // 2, x1, every):
        points += [(x, y0), (x, y1)]
    for y in range(y0 + every // 2, y1, every):
        points += [(x0, y), (x1, y)]
    for x, y in points:
        on = rng.random() < 0.7
        r = 4 * S // 2
        draw.ellipse((x - r - 3, y - r - 3, x + r + 3, y + r + 3), fill=color + (60 if on else 0,))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 250, 220) if on else (90, 80, 70))


def _volcano_scene(image: Image.Image, style: Style, rng: random.Random) -> None:
    """Volcán en erupción detrás de la rejilla, ríos de lava, ascuas y marco de magma."""
    w, h = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    # Resplandor del cielo y el cono del volcán (asoma por arriba y por los lados).
    _glow_layer(
        image,
        lambda d: d.ellipse((w * 0.2, -h * 0.05, w * 0.8, h * 0.35), fill=(255, 90, 10, 120)),
        40,
    )
    cone = [(-w * 0.1, h), (w * 0.38, h * 0.09), (w * 0.62, h * 0.09), (w * 1.1, h)]
    draw.polygon(cone, fill=(34, 14, 12))
    draw.polygon([(w * 0.38, h * 0.09), (w * 0.62, h * 0.09), (w * 0.57, h * 0.12),
                  (w * 0.43, h * 0.12)], fill=(255, 150, 30))  # fmt: skip
    # Ríos de lava por las laderas.
    for side in (-1, 1):
        for k in range(3):
            x = w * 0.5 + side * w * (0.06 + 0.04 * k)
            y = h * 0.11
            points = [(x, y)]
            while y < h:
                x += side * rng.uniform(4, 22) * S
                y += rng.uniform(18, 34) * S
                points.append((x, y))
            _glow_layer(
                image, lambda d, p=points: d.line(p, fill=(255, 80, 0, 200), width=7 * S), 6
            )
            draw.line(points, fill=(255, 210, 90, 255), width=2 * S)
    # Ascuas.
    for _ in range(90):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.choice((1, 1, 2, 3)) * S / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, rng.randint(120, 220), 40, 220))
    # Marco de magma: brillo naranja, borde amarillo y goterones.
    box = _grid_box(8)
    _glow_layer(
        image,
        lambda d: d.rounded_rectangle(box, radius=18 * S, outline=(255, 90, 0, 255), width=6 * S),
        10,
    )
    draw.rounded_rectangle(box, radius=18 * S, fill=(10, 4, 4, 210))
    draw.rounded_rectangle(box, radius=18 * S, outline=(255, 110, 10), width=5 * S)
    draw.rounded_rectangle(
        (box[0] + 3 * S, box[1] + 3 * S, box[2] - 3 * S, box[3] - 3 * S),
        radius=15 * S,
        outline=(255, 220, 100),
        width=S,
    )
    for _ in range(9):
        x = rng.uniform(box[0] + 30 * S, box[2] - 30 * S)
        length = rng.uniform(6, 14) * S
        draw.rounded_rectangle((x - 3 * S, box[1] - S, x + 3 * S, box[1] + length), radius=3 * S,
                               fill=(255, 130, 20))  # fmt: skip
        draw.ellipse((x - 4 * S, box[1] + length - 4 * S, x + 4 * S, box[1] + length + 4 * S),
                     fill=(255, 170, 40))  # fmt: skip


def _meander(draw: ImageDraw.ImageDraw, x0: int, x1: int, y: int, size: int, color) -> None:  # noqa: ANN001
    """Greca griega (meandro) en una franja horizontal."""
    x = x0
    step = size * 2
    while x + step <= x1:
        pts = [
            (x, y + size), (x, y), (x + size * 1.5, y), (x + size * 1.5, y + size * 0.66),
            (x + size * 0.5, y + size * 0.66), (x + size * 0.5, y + size * 0.33),
            (x + size, y + size * 0.33),
        ]  # fmt: skip
        draw.line(pts, fill=color, width=max(2, size // 6))
        draw.line((x, y + size, x + step, y + size), fill=color, width=max(2, size // 6))
        x += step


def _olympus_scene(image: Image.Image, style: Style, rng: random.Random) -> None:
    """Cielo del Olimpo: estrellas, nubes, rayos, columnas de mármol y marco de oro con greca."""
    w, h = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    for _ in range(110):
        x, y, r = rng.uniform(0, w), rng.uniform(0, h), rng.choice((1, 1, 2)) * S / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 240, 200))
    # Nubes abajo y arriba.
    for cy, alpha in ((h * 0.98, 230),):
        for _ in range(16):
            x = rng.uniform(-40, w + 40)
            r = rng.uniform(30, 60) * S
            draw.ellipse((x - r, cy - r * 0.55, x + r, cy + r * 0.55), fill=(236, 232, 255, alpha))
    # Rayos al fondo.
    for x0 in (w * 0.12, w * 0.88):
        points = [(x0, 0)]
        y, x = 0.0, x0
        while y < h * 0.5:
            y += rng.uniform(20, 40) * S
            x += rng.uniform(-18, 18) * S
            points.append((x, y))
        _glow_layer(image, lambda d, p=points: d.line(p, fill=(140, 200, 255, 220), width=6 * S), 8)
        draw.line(points, fill=(255, 255, 255), width=2 * S)
    # Columnas de mármol a los lados de la rejilla.
    box = _grid_box(8)
    for x0 in (2 * S, w - (MARGIN - 1) * S):
        x1 = x0 + (MARGIN - 4) * S
        draw.rectangle((x0, box[1], x1, box[3]), fill=(240, 236, 226))
        for k in range(1, 4):
            gx = x0 + (x1 - x0) * k / 4
            draw.line((gx, box[1] + 8 * S, gx, box[3] - 8 * S), fill=(196, 188, 170), width=S)
        draw.rectangle((x0 - 2 * S, box[1] - 4 * S, x1 + 2 * S, box[1] + 4 * S), fill=style.frame)
        draw.rectangle((x0 - 2 * S, box[3] - 4 * S, x1 + 2 * S, box[3] + 4 * S), fill=style.frame)
    # Marco de oro con greca arriba y abajo.
    draw.rounded_rectangle(box, radius=10 * S, fill=(18, 14, 60, 200))
    draw.rounded_rectangle(box, radius=10 * S, outline=(255, 224, 120), width=5 * S)
    draw.rounded_rectangle(
        (box[0] + 4 * S, box[1] + 4 * S, box[2] - 4 * S, box[3] - 4 * S),
        radius=8 * S,
        outline=style.frame_dark,
        width=S,
    )
    _meander(draw, box[0] + 14 * S, box[2] - 14 * S, box[1] - 2 * S, 5 * S, (255, 224, 120))
    _meander(draw, box[0] + 14 * S, box[2] - 14 * S, box[3] - 3 * S, 5 * S, (255, 224, 120))


def _mine_scene(image: Image.Image, style: Style, rng: random.Random) -> None:
    """Galería de mina: roca con cristales, vigas de madera, bombillas y raíles."""
    w, h = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    # Roca: losas irregulares un poco más claras que el fondo.
    for _ in range(70):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(14, 34) * S
        pts = []
        for i in range(6):
            angle = i * math.pi / 3
            reach = r * rng.uniform(0.7, 1.1)
            pts.append((x + reach * math.cos(angle), y + reach * math.sin(angle)))
        shade = rng.randint(0, 14)
        draw.polygon(pts, fill=(28 + shade, 58 + shade, 62 + shade, 120))
    # Cristales turquesa que brillan en la roca.
    for _ in range(14):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        size = rng.uniform(5, 11) * S
        _glow_layer(
            image,
            lambda d, x=x, y=y, s=size: d.polygon(
                [(x, y - s), (x + s * 0.45, y), (x, y + s * 0.8), (x - s * 0.45, y)],
                fill=(80, 240, 220, 230),
            ),
            5,
        )
    # Vigas: postes a los lados y travesaño sobre la rejilla.
    box = _grid_box(9)
    wood, dark = (150, 96, 48), (82, 50, 22)
    for x0 in (0, w - (MARGIN - 2) * S):
        x1 = x0 + (MARGIN - 2) * S
        draw.rectangle((x0, 0, x1, h), fill=wood)
        for y in range(10 * S, h, 26 * S):
            draw.line((x0 + 2 * S, y, x1 - 2 * S, y + 6 * S), fill=dark, width=S)
    beam = (0, box[1] - 10 * S, w, box[1] + 2 * S)
    draw.rectangle(beam, fill=wood)
    for x in range(8 * S, w, 30 * S):
        draw.line((x, beam[1] + 3 * S, x + 18 * S, beam[1] + 3 * S), fill=dark, width=S)
    for x in (6 * S, w - 6 * S):
        draw.ellipse((x - 3 * S, beam[1] + 3 * S, x + 3 * S, beam[1] + 9 * S), fill=(190, 190, 200))
    # Marco de la rejilla: madera oscura con remaches.
    draw.rounded_rectangle(box, radius=6 * S, fill=(14, 10, 8, 215))
    draw.rounded_rectangle(box, radius=6 * S, outline=(120, 74, 34), width=6 * S)
    draw.rounded_rectangle(
        (box[0] + 5 * S, box[1] + 5 * S, box[2] - 5 * S, box[3] - 5 * S),
        radius=4 * S,
        outline=dark,
        width=S,
    )
    for x in range(box[0] + 12 * S, box[2], 28 * S):
        for y in (box[1] + 3 * S, box[3] - 3 * S):
            draw.ellipse((x - 2 * S, y - 2 * S, x + 2 * S, y + 2 * S), fill=(200, 200, 210))
    # Guirnalda de bombillas en el travesaño y raíles abajo.
    _bulbs(draw, (0, beam[1] + 6 * S, w, beam[1] + 6 * S), (255, 200, 90), 22 * S, rng)
    rail_y = h - 5 * S
    for x in range(0, w, 16 * S):
        draw.rectangle((x, rail_y - 3 * S, x + 8 * S, rail_y + 3 * S), fill=(96, 60, 28))
    draw.line((0, rail_y - 2 * S, w, rail_y - 2 * S), fill=(170, 170, 180), width=2 * S)


def _title(image: Image.Image, text: str, style: Style) -> None:
    """Rótulo de neón: «JOVANI VÁZQUEZ» pequeño entre estrellas y el nombre grande con halo."""
    cx = WIDTH / 2
    small, big = _font(11), _font(30)
    signature = "JOVANI VÁZQUEZ"
    half = ImageDraw.Draw(image).textlength(signature, font=small) / 2 + 10

    def paint(draw: ImageDraw.ImageDraw, glow: bool) -> None:
        if glow:
            draw.text((cx, TITLE_Y + 31), text, font=big, fill=style.title_glow, anchor="mm",
                      stroke_width=6, stroke_fill=style.title_glow)  # fmt: skip
            return
        draw.text((cx, TITLE_Y + 8), signature, font=small, fill=style.title, anchor="mm",
                  stroke_width=2, stroke_fill=(0, 0, 0))  # fmt: skip
        for x in (cx - half, cx + half):
            draw.polygon(_star(x, TITLE_Y + 8, 6, 2.5, 5), fill=style.title, outline=(0, 0, 0))
        draw.text((cx, TITLE_Y + 31), text, font=big, fill=style.title, anchor="mm",
                  stroke_width=3, stroke_fill=(30, 10, 10))  # fmt: skip

    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    paint(ImageDraw.Draw(layer), glow=True)
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(5)))
    paint(ImageDraw.Draw(image), glow=False)


def _cell_tile(material: str, *, empty: bool) -> Image.Image:
    """Fondo de una casilla según el material de la máquina (llena o vacía en el bonus)."""
    size = CELL * S
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    radius = {"basalt": 12, "marble": 8, "wood": 4}[material] * S
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    rng = random.Random(material + str(empty))
    if material == "basalt":
        top, bottom = ((30, 16, 14), (14, 8, 8)) if empty else ((66, 50, 46), (30, 22, 22))
        face = _gradient((size, size), top, bottom).convert("RGBA")
        draw = ImageDraw.Draw(face, "RGBA")
        for _ in range(5):
            x, y = rng.uniform(0, size), rng.uniform(0, size)
            draw.line((x, y, x + rng.uniform(-30, 30), y + rng.uniform(10, 40)),
                      fill=(255, 90, 20, 60 if not empty else 90), width=2)  # fmt: skip
        rim, inner = (255, 110, 20), (120, 40, 10)
    elif material == "marble":
        top, bottom = ((44, 40, 110), (24, 22, 70)) if empty else ((252, 250, 245), (220, 214, 202))
        face = _gradient((size, size), top, bottom).convert("RGBA")
        draw = ImageDraw.Draw(face, "RGBA")
        vein = (90, 84, 160, 70) if empty else (200, 192, 180, 60)
        # Vetas solo en las vacías: en las llenas competían con el símbolo.
        for _ in range(3 if empty else 0):
            x = rng.uniform(0, size)
            points = [(x, 0)]
            for k in range(1, 6):
                points.append((x + rng.uniform(-20, 20), size * k / 5))
            draw.line(points, fill=vein, width=2)
        rim, inner = (230, 186, 70), (255, 240, 170)
    else:
        top, bottom = ((44, 32, 22), (26, 18, 12)) if empty else ((196, 140, 80), (150, 98, 50))
        face = _gradient((size, size), top, bottom).convert("RGBA")
        draw = ImageDraw.Draw(face, "RGBA")
        plank = size // 3
        for k in range(1, 3):
            draw.line((0, plank * k, size, plank * k), fill=(80, 46, 20, 200), width=2 * S)
        for _ in range(8):
            y = rng.uniform(0, size)
            draw.arc((rng.uniform(-40, size), y - 8, rng.uniform(size, size + 60), y + 8), 180, 360,
                     fill=(110, 70, 34, 120), width=2)  # fmt: skip
        rim, inner = (70, 40, 16), (230, 180, 110)
        for x, y in ((9, 9), (size - 9, 9), (9, size - 9), (size - 9, size - 9)):
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(190, 190, 200))
    tile.paste(face, (0, 0), mask)
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, outline=rim, width=3 * S)
    draw.rounded_rectangle(
        (3 * S, 3 * S, size - 1 - 3 * S, size - 1 - 3 * S),
        radius=max(2, radius - 3 * S),
        outline=inner + (90,) if not empty else inner + (40,),
        width=S,
    )
    return tile.resize((CELL, CELL), Image.Resampling.LANCZOS)


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
