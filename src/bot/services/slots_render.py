"""Animación de la tragaperras: un GIF por tirada y una imagen fija final.

La ruleta tiene 38 resultados y prerrenderiza un GIF por casilla. Aquí hay
unas 29.000 combinaciones de rodillos, así que no se puede: el GIF se monta
en cada tirada. Para que sea barato, todo lo caro se hace una sola vez al
crear el renderizador:

- Cada símbolo se dibuja ya cuantizado a una paleta fija común, en versión
  nítida y en versión desenfocada (la que se ve con el rodillo a toda
  velocidad). Montar un fotograma es pegar recortes de índices de paleta, sin
  volver a calcular colores.
- El marco de la máquina (fondo, ventanas y flechas de la línea) también
  está ya cuantizado.
- Lo mismo con el marcador del premio (cada carácter, a varios tamaños), los
  carteles de cada nivel, las monedas y los destellos: piezas en la paleta
  común con su máscara, que se pegan sin recalcular colores.

Cifras medidas (CPU de una tirada, tamaño del GIF; preparar las piezas
cuesta ~0,2 s una sola vez):

- Sin premio: ~0,02 s y ~100 KB.
- Premio sin nivel (cuenta de 0,6 s): ~0,025 s y ~115 KB.
- GRAN PREMIO: ~0,03 s y ~130 KB. MEGA: ~0,035 s y ~150 KB.
- ÉPICO con anticipación y jackpot (el peor caso): ~0,055 s y ~240 KB, de
  los que ~150 KB son ya los rodillos y los parpadeos de la línea.

El GIF lo escribe `bot.utils.gif.shared_palette_gif`. El modo turbo se salta
el GIF y manda solo la imagen final (~5 KB, con o sin premio).

La animación:

1. Los tres rodillos arrancan a la vez y paran de izquierda a derecha, con
   un pequeño rebote al frenar.
2. Si los dos primeros prometen algo gordo (`Spin.anticipation`), el
   tercero sigue girando más tiempo, frena despacio y su ventana se ilumina.
3. Si la línea paga, parpadea un marco sobre ella.
4. Si la tirada cobra algo (`won`), sale un marcador sobre la fila de abajo
   que cuenta de 0 a lo cobrado, arrancando y frenando suave. La cuenta dura
   más cuanto más alto es el nivel del premio (`ROLLUP_SECONDS`): es lo que
   hacen las máquinas de verdad, cuanto más dura la cuenta más grande se
   siente el premio, también cuando se cobra menos de lo apostado.
5. Si el premio tiene nivel (`bot.services.slots.win_tier`), un cartel tapa
   la fila de arriba: «¡GRAN PREMIO!», «¡MEGAPREMIO!» o «¡ÉPICO!». Entra
   creciendo y su efecto escala con el nivel: el GRAN parpadea, el MEGA
   además suelta monedas y el ÉPICO gira sus rayos, suelta más monedas y
   centellea.

Las filas de arriba y abajo solo están para el near-miss, así que en una
tirada con premio se pueden tapar: la línea siempre queda a la vista.

El último fotograma se queda quieto mucho tiempo y es idéntico al PNG final
(con el importe y el cartel si los hay): el bot cambia el GIF por el PNG al
acabar, así el mensaje deja de animarse aunque el cliente repita el GIF en
bucle (lo mismo que hace la ruleta).

Los símbolos son imágenes de `assets/slots` (ver su `LICENSE.txt`); el texto
usa Montserrat Bold (`assets/memes/fonts`, SIL Open Font License 1.1). Se
distinguen por la forma y no solo por el color, también con deuteranopia: los
tres carteles tienen forma y tamaño distintos (rectángulo, cinta con colas y
estallido de rayos), además del texto.
"""

from __future__ import annotations

import io
import math
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from bot.services.economy import format_amount
from bot.services.slots import (
    BELL,
    CHERRY,
    DIAMOND,
    GRAPE,
    LEMON,
    REEL_STRIPS,
    SCATTER,
    SEVEN,
    WILD,
    Spin,
    WinTier,
    win_tier,
)
from bot.utils.gif import shared_palette_gif

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "slots"
#: Letra del marcador y de los carteles (SIL Open Font License 1.1).
FONT = Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
SYMBOL_FILES = {
    CHERRY: "cherry.png",
    LEMON: "lemon.png",
    GRAPE: "grape.png",
    BELL: "bell.png",
    DIAMOND: "diamond.png",
    SEVEN: "seven.png",
    WILD: "wild.png",
    SCATTER: "scatter.png",
}

#: Celda de un símbolo dentro del rodillo, en píxeles.
CELL_W = 64
CELL_H = 58
#: Lado del símbolo dentro de la celda.
SYMBOL = 50
ROWS = 3
GAP = 8
MARGIN = 16
WINDOW_H = CELL_H * ROWS
WIDTH = MARGIN * 2 + CELL_W * 3 + GAP * 2
HEIGHT = MARGIN * 2 + WINDOW_H

FRAME_MS = 70
#: Fotograma en el que para cada rodillo (normal y con anticipación).
STOP_FRAMES = (10, 14, 18)
ANTICIPATION_STOP = 32
#: Celdas que recorre cada rodillo antes de parar: el tercero, más.
TRAVEL_CELLS = (10, 13, 16)
ANTICIPATION_TRAVEL = 24
#: Lo que se pasa de largo antes del rebote, en celdas.
OVERSHOOT = 0.18
BOUNCE_FRAMES = 3
#: Radio del desenfoque de los símbolos con el rodillo a toda velocidad.
BLUR_PIXELS = 5
#: Colores de la paleta común. Menos colores, GIF más pequeño.
PALETTE_COLORS = 64
#: Por encima de esta velocidad (celdas por fotograma) se pinta desenfocado.
BLUR_SPEED = 0.45
#: Parpadeos del marco de la línea cuando paga (cada uno, encendido y apagado).
FLASHES = 3
FLASH_FRAMES = 2
FINAL_FRAME_MS = 60_000

BACKGROUND = (43, 45, 49)  # gris de los embeds en el tema oscuro
CABINET = (28, 29, 33)
REEL_BG = (236, 233, 224)
REEL_SHADE = (205, 200, 188)
GOLD = (222, 178, 70)
HIGHLIGHT = (255, 214, 92)
GLOW = (255, 245, 200)
DARK_LINE = (60, 60, 66)
METER_BG = (14, 14, 18)
COIN_RIM = (150, 100, 24)
MEGA_RED = (196, 34, 52)
MEGA_TAIL = (130, 18, 34)
EPIC_PURPLE = (92, 40, 168)
EPIC_RAY = (255, 146, 40)
EPIC_INK = (36, 10, 72)
WHITE = (255, 255, 255)

# -- Premio: cuenta y carteles ------------------------------------------------------

#: Lo que dura la cuenta del premio según su nivel (`None`: premio sin nivel).
#: Más larga, premio que se siente más grande.
ROLLUP_SECONDS: dict[str | None, float] = {
    None: 0.6,
    WinTier.BIG: 1.2,
    WinTier.MEGA: 1.8,
    WinTier.EPIC: 2.5,
}
#: Marcador del premio: tapa la fila de abajo (la del near-miss).
METER_BOX = (MARGIN - 4, MARGIN + CELL_H * 2 + 6, WIDTH - MARGIN + 3, HEIGHT - 7)
METER_PADDING = 8
#: Tamaños de letra del marcador, de mayor a menor: se usa el mayor en el que
#: cabe el importe final, el mismo durante toda la cuenta.
METER_SIZES = (34, 28, 23, 19, 16)

#: Texto, tamaño (ancho, alto) y centro vertical del cartel de cada nivel. La
#: forma también cambia: rectángulo, cinta con colas y estallido de rayos.
BANNER_TEXT = {
    WinTier.BIG: "¡GRAN PREMIO!",
    WinTier.MEGA: "¡MEGAPREMIO!",
    WinTier.EPIC: "¡ÉPICO!",
}
BANNER_SIZE = {WinTier.BIG: (196, 38), WinTier.MEGA: (228, 48), WinTier.EPIC: (236, 70)}
BANNER_CENTER_Y = {
    WinTier.BIG: MARGIN + CELL_H // 2,
    WinTier.MEGA: MARGIN + CELL_H // 2,
    WinTier.EPIC: MARGIN + CELL_H // 2 - 6,
}
#: Escalas de los primeros fotogramas del cartel: entra creciendo.
BANNER_POP = (0.55, 0.8)
#: Cada cuántos fotogramas cambia el cartel de aspecto (parpadeo o giro de rayos).
BANNER_BLINK = {WinTier.BIG: 3, WinTier.MEGA: 3, WinTier.EPIC: 4}
#: Monedas que caen y destellos que centellean durante la cuenta.
COINS = {WinTier.BIG: 0, WinTier.MEGA: 5, WinTier.EPIC: 10}
SPARKLES = {WinTier.BIG: 0, WinTier.MEGA: 0, WinTier.EPIC: 8}
COIN = 14
#: Anchos de la moneda al girar (de cara, de medio lado, de canto, de medio lado).
COIN_PHASES = (14, 9, 4, 9)
SPARKLE_SIZES = (9, 13)
EPIC_RAYS = 18


@dataclass(frozen=True, slots=True)
class SlotsMedia:
    """Imágenes de una tirada.

    Attributes:
        gif: Animación del giro; vacía en modo turbo.
        png: Imagen fija final, igual que el último fotograma del GIF.
        seconds: Lo que dura la animación hasta el último fotograma.
    """

    gif: bytes
    png: bytes
    seconds: float


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


def rollup_frames(tier: str | None) -> int:
    """Fotogramas de la cuenta del premio para un nivel (`None`: sin nivel)."""
    return round(ROLLUP_SECONDS[tier] * 1000 / FRAME_MS)


def rollup_values(won: int, frames: int) -> list[int]:
    """Lo que enseña el marcador en cada fotograma de la cuenta.

    Arranca y frena suave, y el último es exactamente `won`.
    """
    return [round(won * _ease_in_out(k / frames)) for k in range(1, frames + 1)]


#: Una pieza ya cuantizada a la paleta común y su máscara (255 donde pinta).
Piece = tuple[Image.Image, Image.Image]


def reel_position(stop: int, frame: int, stop_frame: int, travel: float) -> float:
    """Posición (en celdas de la tira) de un rodillo en un fotograma.

    Los símbolos bajan: la posición decrece hasta `stop`, se pasa un poco
    (`OVERSHOOT`) y vuelve en `BOUNCE_FRAMES` fotogramas.
    """
    main_end = stop_frame - BOUNCE_FRAMES
    if frame >= stop_frame:
        return float(stop)
    if frame >= main_end:
        t = (frame - main_end) / BOUNCE_FRAMES
        return stop - OVERSHOOT * (1 - t)
    t = frame / main_end
    return stop + (travel + OVERSHOOT) * (1 - _ease_out(t)) - OVERSHOOT


class SlotsRenderer:
    """Dibuja las tiradas de la tragaperras con piezas precalculadas.

    Es seguro llamarlo desde varios hilos: las piezas se preparan una vez con
    un cerrojo y después solo se leen.
    """

    def __init__(self, assets: Path = ASSETS) -> None:
        self._assets = assets
        self._lock = threading.Lock()
        self._ready = False

    # -- Piezas ---------------------------------------------------------------------

    def _prepare(self) -> None:
        with self._lock:
            if self._ready:
                return
            symbols = {key: self._load(name) for key, name in SYMBOL_FILES.items()}
            sharp = {key: self._cell(img) for key, img in symbols.items()}
            blurred = {key: _vertical_blur(cell, BLUR_PIXELS) for key, cell in sharp.items()}
            frame = self._frame()
            glow = self._frame(glow=True)
            highlight = self._highlight_layer()
            meter = _meter_panel()
            glyphs = {size: _glyphs(size) for size in METER_SIZES}
            banners = {tier: _banner_variants(tier) for tier in BANNER_TEXT}
            coins = [_coin(width) for width in sorted(set(COIN_PHASES))]
            sparkles = [_sparkle(size) for size in SPARKLE_SIZES]

            # La paleta sale de todas las piezas juntas: así cada una cabe sin
            # perder colores y todas comparten índices.
            lit = frame.copy()
            lit.paste(highlight.convert("RGB"), (0, 0), highlight)
            pieces: list[Image.Image] = [lit, glow, meter]
            pieces += list(sharp.values()) + list(blurred.values())
            pieces += [glyph for table in glyphs.values() for glyph in table.values()]
            pieces += [image for variants in banners.values() for image in variants]
            pieces += coins + sparkles
            palette = _sample(pieces).quantize(
                colors=PALETTE_COLORS, method=Image.Quantize.MEDIANCUT
            )

            def q(image: Image.Image) -> Image.Image:
                return image.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)

            def piece(image: Image.Image) -> Piece:
                mask = image.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
                return q(image), mask

            self._palette = palette
            self._sharp = {key: q(cell) for key, cell in sharp.items()}
            self._blurred = {key: q(cell) for key, cell in blurred.items()}
            self._frame_p = q(frame)
            self._glow_p = q(glow)
            self._highlight = highlight
            self._meter = piece(meter)
            self._glyphs = {
                size: {char: q(glyph) for char, glyph in table.items()}
                for size, table in glyphs.items()
            }
            self._banners = {
                tier: [piece(image) for image in variants] for tier, variants in banners.items()
            }
            by_width = {width: piece(_coin(width)) for width in set(COIN_PHASES)}
            self._coins = [by_width[width] for width in COIN_PHASES]
            self._sparkles = [piece(sparkle) for sparkle in sparkles]
            self._ready = True

    def _load(self, name: str) -> Image.Image:
        image = Image.open(self._assets / name).convert("RGBA")
        if image.size != (SYMBOL, SYMBOL):
            image = image.resize((SYMBOL, SYMBOL), Image.Resampling.LANCZOS)
        return image

    @staticmethod
    def _cell(symbol: Image.Image) -> Image.Image:
        """Celda del rodillo con su símbolo centrado y una línea de separación."""
        cell = Image.new("RGB", (CELL_W, CELL_H), REEL_BG)
        draw = ImageDraw.Draw(cell)
        draw.line((0, CELL_H - 1, CELL_W, CELL_H - 1), fill=REEL_SHADE)
        cell.paste(symbol, ((CELL_W - SYMBOL) // 2, (CELL_H - SYMBOL) // 2), symbol)
        return cell

    @staticmethod
    def reel_x(reel: int) -> int:
        """Borde izquierdo de la ventana de un rodillo."""
        return MARGIN + reel * (CELL_W + GAP)

    def _frame(self, *, glow: bool = False) -> Image.Image:
        """Marco de la máquina: fondo, ventanas y flechas de la línea.

        Con `glow`, la ventana del tercer rodillo está iluminada (anticipación).
        """
        image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, WIDTH - 3, HEIGHT - 3), radius=14, fill=CABINET)
        draw.rounded_rectangle((2, 2, WIDTH - 3, HEIGHT - 3), radius=14, outline=GOLD, width=3)
        for reel in range(3):
            x = self.reel_x(reel)
            box = (x - 3, MARGIN - 3, x + CELL_W + 2, MARGIN + WINDOW_H + 2)
            color = GLOW if glow and reel == 2 else DARK_LINE
            draw.rectangle(box, outline=color, width=3)
        # Flechas de la línea de pago, fuera de las ventanas.
        mid = MARGIN + CELL_H * 1.5
        size = 7
        draw.polygon([(3, mid - size), (3 + size + 2, mid), (3, mid + size)], fill=GOLD)
        right = WIDTH - 4
        draw.polygon([(right, mid - size), (right - size - 2, mid), (right, mid + size)], fill=GOLD)
        return image

    def _highlight_layer(self) -> Image.Image:
        """Marco dorado sobre la línea de pago (capa con transparencia)."""
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        top = MARGIN + CELL_H
        draw.rounded_rectangle(
            (MARGIN - 6, top - 2, WIDTH - MARGIN + 5, top + CELL_H + 1),
            radius=8,
            outline=HIGHLIGHT,
            width=4,
        )
        return layer

    # -- Fotogramas -----------------------------------------------------------------

    def _reel_column(self, reel: int, position: float, blurred: bool) -> Image.Image:
        """Ventana de un rodillo con la tira en `position` (celda en la línea)."""
        strip = REEL_STRIPS[reel]
        cells = self._blurred if blurred else self._sharp
        column = Image.new("P", (CELL_W, WINDOW_H))
        base = math.floor(position)
        shift = (position - base) * CELL_H
        # La celda de la línea es la fila 1; arriba van las anteriores de la tira.
        for row in range(-1, ROWS + 1):
            symbol = strip[(base + row - 1) % len(strip)]
            y = round(row * CELL_H - shift)
            if -CELL_H < y < WINDOW_H:
                column.paste(cells[symbol], (0, y))
        return column

    def _compose(
        self, positions: tuple[float, ...], speeds: tuple[float, ...], *, glow: bool
    ) -> Image.Image:
        frame = (self._glow_p if glow else self._frame_p).copy()
        for reel, (position, speed) in enumerate(zip(positions, speeds, strict=True)):
            column = self._reel_column(reel, position, speed > BLUR_SPEED)
            frame.paste(column, (self.reel_x(reel), MARGIN))
        return frame

    def _with_highlight(self, frame: Image.Image) -> Image.Image:
        rgb = frame.convert("RGB")
        rgb.paste(self._highlight.convert("RGB"), (0, 0), self._highlight)
        return rgb.quantize(palette=self._palette, dither=Image.Dither.NONE)

    def still(self, stops: tuple[int, int, int], *, highlight: bool = False) -> Image.Image:
        """Fotograma con los rodillos parados en `stops`."""
        self._prepare()
        frame = self._compose(tuple(float(s) for s in stops), (0.0, 0.0, 0.0), glow=False)
        return self._with_highlight(frame) if highlight else frame

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def still_png(self, stops: tuple[int, int, int], *, highlight: bool = False) -> bytes:
        """PNG de la máquina parada (al abrirla o en modo turbo)."""
        return self._png(self.still(stops, highlight=highlight))

    # -- Premio -----------------------------------------------------------------------

    @staticmethod
    def _paste(frame: Image.Image, piece: Piece, center: tuple[int, int]) -> None:
        image, mask = piece
        frame.paste(image, (center[0] - image.width // 2, center[1] - image.height // 2), mask)

    def _meter_size(self, won: int) -> int:
        """Mayor tamaño de letra en el que cabe el importe en el marcador."""
        inner = METER_BOX[2] - METER_BOX[0] - 2 * METER_PADDING
        text = format_amount(won)
        for size in METER_SIZES:
            if sum(self._glyphs[size][char].width for char in text) <= inner:
                return size
        return METER_SIZES[-1]

    def _draw_meter(self, frame: Image.Image, amount: int, size: int) -> None:
        """Marcador con `amount` sobre la fila de abajo."""
        panel, mask = self._meter
        frame.paste(panel, METER_BOX[:2], mask)
        glyphs = [self._glyphs[size][char] for char in format_amount(amount)]
        x = (METER_BOX[0] + METER_BOX[2]) // 2 - sum(g.width for g in glyphs) // 2
        y = (METER_BOX[1] + METER_BOX[3]) // 2 - glyphs[0].height // 2
        for glyph in glyphs:
            frame.paste(glyph, (x, y))
            x += glyph.width

    def _draw_effects(self, frame: Image.Image, tier: str, index: int) -> None:
        """Monedas que caen y destellos del fotograma `index` de la cuenta.

        Las posiciones salen de fórmulas fijas, no del azar: la misma tirada
        se dibuja siempre igual.
        """
        span = HEIGHT + COIN
        for coin in range(COINS[tier]):
            x = COIN // 2 + round((coin * 0.618034) % 1 * (WIDTH - COIN))
            speed = 6 + (coin * 7) % 5
            y = (coin * 53 + index * speed) % span - COIN // 2
            self._paste(frame, self._coins[(index + coin) % len(self._coins)], (x, y))
        width, height = BANNER_SIZE[tier]
        for sparkle in range(SPARKLES[tier]):
            if (index + sparkle) % 3 == 0:
                continue
            angle = sparkle * 2 * math.pi / SPARKLES[tier] + 0.4
            x = WIDTH // 2 + round(math.cos(angle) * (width // 2 - 6))
            y = BANNER_CENTER_Y[tier] + round(math.sin(angle) * (height // 2 + 4))
            piece = self._sparkles[(index // 2 + sparkle) % len(self._sparkles)]
            self._paste(frame, piece, (x, y))

    def _banner_piece(self, tier: str, index: int | None) -> Piece:
        """Cartel en el fotograma `index` de la cuenta (`None`: el de la imagen final)."""
        variants = self._banners[tier]
        if index is None:
            return variants[0]
        if index < len(BANNER_POP):
            return variants[2 + index]
        return variants[(index // BANNER_BLINK[tier]) % 2]

    def _celebrate(
        self,
        base: Image.Image,
        amount: int,
        size: int,
        tier: str | None,
        index: int | None = None,
    ) -> Image.Image:
        """`base` con el marcador y, si hay nivel, el cartel (y sus efectos en la cuenta)."""
        frame = base.copy()
        if tier is not None:
            if index is not None:
                self._draw_effects(frame, tier, index)
            center = (WIDTH // 2, BANNER_CENTER_Y[tier])
            self._paste(frame, self._banner_piece(tier, index), center)
        self._draw_meter(frame, amount, size)
        return frame

    def render(
        self, spin: Spin, *, turbo: bool = False, won: int = 0, stake: int = 0
    ) -> SlotsMedia:
        """Animación y PNG final de una tirada.

        Args:
            turbo: Solo el PNG final, sin GIF (más rápido y casi sin datos).
            won: Todo lo cobrado en la tirada (línea más bote). Si es mayor que
                cero, el GIF acaba con la cuenta del premio y el PNG lo enseña.
            stake: Apuesta de la tirada (en un giro gratis, la que lo activó):
                con `won` decide el nivel del premio (`win_tier`).
        """
        self._prepare()
        wins = spin.pay_halves > 0 or spin.is_jackpot
        lit = self.still(spin.stops, highlight=wins)
        tier = win_tier(won, stake) if won > 0 else None
        size = self._meter_size(won) if won > 0 else 0
        final = self._celebrate(lit, won, size, tier) if won > 0 else lit
        if turbo:
            return SlotsMedia(gif=b"", png=self._png(final), seconds=0.0)

        stop_frames = list(STOP_FRAMES)
        travels = list(TRAVEL_CELLS)
        if spin.anticipation:
            stop_frames[2] = ANTICIPATION_STOP
            travels[2] = ANTICIPATION_TRAVEL
        last = stop_frames[2]

        frames: list[Image.Image] = []
        previous = tuple(
            reel_position(spin.stops[r], 0, stop_frames[r], travels[r]) for r in range(3)
        )
        for index in range(last + 1):
            positions = tuple(
                reel_position(spin.stops[r], index, stop_frames[r], travels[r]) for r in range(3)
            )
            speeds = tuple(abs(a - b) for a, b in zip(positions, previous, strict=True))
            previous = positions
            # La ventana del tercero se ilumina mientras se espera que pare.
            glow = spin.anticipation and stop_frames[1] <= index < last and index % 4 < 2
            frames.append(self._compose(positions, speeds, glow=glow))

        if wins:
            flashes = FLASHES * (2 if spin.is_jackpot else 1)
            plain = frames[-1]
            for _ in range(flashes):
                frames.extend([lit] * FLASH_FRAMES)
                frames.extend([plain] * FLASH_FRAMES)
        if won > 0:
            values = rollup_values(won, rollup_frames(tier))
            for index, amount in enumerate(values):
                frames.append(self._celebrate(lit, amount, size, tier, index))
        frames.append(final)

        durations = [FRAME_MS] * (len(frames) - 1) + [FINAL_FRAME_MS]
        seconds = FRAME_MS * (len(frames) - 1) / 1000
        gif = shared_palette_gif(frames, durations)
        return SlotsMedia(gif=gif, png=self._png(final), seconds=seconds)


def _sample(images: list[Image.Image]) -> Image.Image:
    """Todas las piezas en una sola imagen, para sacar la paleta común.

    Las piezas con transparencia se pegan sobre el color del mueble.
    """
    width = max(image.width for image in images)
    height = sum(image.height for image in images)
    sample = Image.new("RGB", (width, height), CABINET)
    y = 0
    for image in images:
        mask = image if image.mode == "RGBA" else None
        sample.paste(image.convert("RGB"), (0, y), mask)
        y += image.height
    return sample


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def _fit_font(text: str, max_width: float, start: int) -> ImageFont.FreeTypeFont:
    """La letra más grande (hasta `start`) en la que `text` cabe en `max_width`."""
    size = start
    font = _font(size)
    while size > 8 and font.getlength(text) > max_width:
        size -= 1
        font = _font(size)
    return font


def _meter_panel() -> Image.Image:
    """Fondo del marcador: caja oscura con borde dorado."""
    width = METER_BOX[2] - METER_BOX[0]
    height = METER_BOX[3] - METER_BOX[1]
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=10, fill=METER_BG, outline=GOLD, width=3
    )
    return panel


def _glyphs(size: int) -> dict[str, Image.Image]:
    """Cada carácter que puede salir en el importe, dorado sobre el marcador.

    Las cifras tienen todas el mismo ancho, para que la cuenta no baile.
    """
    font = _font(size)
    chars = set("0123456789.-" + format_amount(0))
    digits = "0123456789"
    inked = [char for char in chars if not char.isspace()]
    top = min(font.getbbox(char)[1] for char in inked)
    bottom = max(font.getbbox(char)[3] for char in inked)
    digit_width = math.ceil(max(font.getlength(d) for d in digits)) + 1
    glyphs: dict[str, Image.Image] = {}
    for char in chars:
        advance = font.getlength(char)
        width = digit_width if char in digits else max(1, math.ceil(advance) + 1)
        glyph = Image.new("RGB", (width, bottom - top), METER_BG)
        ImageDraw.Draw(glyph).text(((width - advance) / 2, -top), char, font=font, fill=HIGHLIGHT)
        glyphs[char] = glyph
    return glyphs


def _banner_variants(tier: str) -> list[Image.Image]:
    """Cartel de un nivel: sus dos aspectos (parpadeo) y los tamaños de entrada."""
    first = _banner(tier, 0)
    variants = [first, _banner(tier, 1)]
    for scale in BANNER_POP:
        size = (max(1, round(first.width * scale)), max(1, round(first.height * scale)))
        variants.append(first.resize(size, Image.Resampling.LANCZOS))
    return variants


def _banner(tier: str, variant: int) -> Image.Image:
    """Dibuja el cartel de un nivel. Cada nivel tiene su forma, no solo su color.

    - GRAN PREMIO: rectángulo dorado, el más pequeño.
    - MEGAPREMIO: cinta roja con colas en V, más ancha.
    - ÉPICO: estallido de rayos con un óvalo morado, el más grande.
    """
    width, height = BANNER_SIZE[tier]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text = BANNER_TEXT[tier]
    center = (width / 2, height / 2)
    if tier == WinTier.BIG:
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=9,
            fill=HIGHLIGHT if variant == 0 else GLOW,
            outline=CABINET,
            width=3,
        )
        font = _fit_font(text, width - 20, 22)
        ink, stroke, stroke_fill = CABINET, 0, None
    elif tier == WinTier.MEGA:
        tail = 24
        bottom = height - 1
        draw.polygon(
            [(0, 9), (tail + 4, 9), (tail + 4, bottom), (0, bottom), (9, (9 + bottom) // 2)],
            fill=MEGA_TAIL,
            outline=CABINET,
        )
        right = width - 1
        draw.polygon(
            [
                (right, 9),
                (right - tail - 4, 9),
                (right - tail - 4, bottom),
                (right, bottom),
                (right - 9, (9 + bottom) // 2),
            ],
            fill=MEGA_TAIL,
            outline=CABINET,
        )
        draw.rectangle(
            (tail, 0, width - tail - 1, height - 10),
            fill=MEGA_RED,
            outline=GOLD if variant == 0 else WHITE,
            width=3,
        )
        center = (width / 2, (height - 10) / 2)
        font = _fit_font(text, width - 2 * tail - 14, 24)
        ink = HIGHLIGHT if variant == 0 else WHITE
        stroke, stroke_fill = 2, MEGA_TAIL
    else:
        cx, cy = center
        offset = math.pi / (2 * EPIC_RAYS) if variant else 0.0
        points = []
        for k in range(EPIC_RAYS * 2):
            angle = offset + k * math.pi / EPIC_RAYS
            rx, ry = (cx - 1, cy - 1) if k % 2 == 0 else (cx * 0.72, cy * 0.66)
            points.append((cx + math.cos(angle) * rx, cy + math.sin(angle) * ry))
        draw.polygon(points, fill=HIGHLIGHT if variant == 0 else EPIC_RAY, outline=CABINET)
        draw.ellipse(
            (cx * 0.36, cy * 0.34, width - cx * 0.36, height - cy * 0.34),
            fill=EPIC_PURPLE,
            outline=GLOW,
            width=2,
        )
        font = _fit_font(text, cx * 1.15, 32)
        ink, stroke, stroke_fill = WHITE, 2, EPIC_INK
    draw.text(
        center,
        text,
        font=font,
        fill=ink,
        anchor="mm",
        stroke_width=stroke,
        stroke_fill=stroke_fill,
    )
    return image


def _coin(width: int) -> Image.Image:
    """Moneda vista con `width` de ancho: más estrecha cuanto más de canto."""
    image = Image.new("RGBA", (COIN, COIN), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    left = (COIN - width) // 2
    draw.ellipse((left, 0, left + width - 1, COIN - 1), fill=GOLD, outline=COIN_RIM)
    if width > 6:
        inset = width // 4
        draw.ellipse((left + inset, 3, left + width - 1 - inset, COIN - 4), fill=HIGHLIGHT)
    return image


def _sparkle(size: int) -> Image.Image:
    """Destello de cuatro puntas."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c = size // 2
    w = max(1, size // 6)
    last = size - 1
    points = [(c, 0), (c + w, c - w), (last, c), (c + w, c + w)]
    points += [(c, last), (c - w, c + w), (0, c), (c - w, c - w)]
    ImageDraw.Draw(image).polygon(points, fill=WHITE, outline=GLOW)
    return image


def _vertical_blur(image: Image.Image, radius: int) -> Image.Image:
    """Desenfoque solo vertical: el de un rodillo girando a toda velocidad.

    Media de `2·radius + 1` píxeles en vertical. Pillow no trae filtros
    direccionales de este tamaño, así que se hace con numpy (ya es dependencia).
    """
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    padded = np.pad(pixels, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    summed = np.cumsum(padded, axis=0)
    summed = np.concatenate([np.zeros_like(summed[:1]), summed], axis=0)
    window = 2 * radius + 1
    blurred = (summed[window:] - summed[:-window]) / window
    return Image.fromarray(blurred.clip(0, 255).astype(np.uint8), "RGB")
