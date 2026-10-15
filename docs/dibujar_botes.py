# ruff: noqa: E501  (las listas de puntos de los dibujos van en líneas largas)
"""Dibuja los símbolos PNG de las máquinas de Botes nuevas (la Mina y el Olimpo).

Regenera `src/bot/assets/botes/<máquina>_pay0…5.png`, `_wild.png` y `_collect.png`
(8 por máquina, 128×128 con fondo transparente, como los del Volcán). Se dibuja todo
con Pillow y numpy, a cuádruple tamaño y reduciendo al final, en el estilo de
tragaperras ilustrada del casino: piezas gordas con degradado, borde oscuro grueso,
brillo en el filo de arriba y un halo suave.

Uso (desde la raíz del repositorio)::

    python docs/dibujar_botes.py            # las dos máquinas
    python docs/dibujar_botes.py mina       # solo una
    python docs/dibujar_botes.py --hoja     # además, una hoja de contacto en hoja_botes.png

Los de la Mina (de menos a más): pico, lámpara de minero, carretilla, dinamita, pepita
de oro y lingote; comodín, diamante en bruto; recogedor, vagoneta de oro. Los del
Olimpo: ánfora, lira, casco, laurel, tridente y águila; comodín, rayo de Zeus;
recogedor, Zeus. Cada uno se distingue por la silueta y no solo por el color (hay
jugadores con deuteranopia). El rótulo «WILD» usa Luckiest Guy
(`src/bot/assets/botes/fonts`).

Los símbolos del Volcán no salen de aquí: son anteriores y no se tocan.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "bot" / "assets" / "botes"
FONT = ASSETS / "fonts" / "luckiest-guy.woff"

SIZE = 128
K = 4  # factor de supersampling
N = SIZE * K
FILL = 126  # lado mayor del símbolo con su borde, como los del Volcán (~85-97 % de la casilla)

Color = tuple[int, int, int]
Point = tuple[float, float]


# -- Herramientas ------------------------------------------------------------------------


def smooth(points: list[Point], *, closed: bool = True, steps: int = 10) -> list[Point]:
    """Curva Catmull-Rom que pasa por los puntos (para siluetas redondeadas)."""
    n = len(points)
    out: list[Point] = []
    for i in range(n if closed else n - 1):
        p0 = points[(i - 1) % n] if closed or i > 0 else points[i]
        p1, p2 = points[i], points[(i + 1) % n]
        p3 = points[(i + 2) % n] if closed or i + 2 < n else p2
        for s in range(steps):
            t = s / steps
            out.append(
                tuple(  # type: ignore[arg-type]
                    0.5
                    * (
                        2 * p1[k]
                        + (-p0[k] + p2[k]) * t
                        + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t
                        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t**3
                    )
                    for k in (0, 1)
                )
            )
    if not closed:
        out.append(points[-1])
    return out


def bar(a: Point, b: Point, w0: float, w1: float | None = None) -> list[Point]:
    """Cuadrilátero grueso de `a` a `b` con anchos `w0` y `w1` (en 128 px)."""
    w1 = w0 if w1 is None else w1
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1
    nx, ny = -dy / length, dx / length
    return [
        (a[0] + nx * w0 / 2, a[1] + ny * w0 / 2),
        (b[0] + nx * w1 / 2, b[1] + ny * w1 / 2),
        (b[0] - nx * w1 / 2, b[1] - ny * w1 / 2),
        (a[0] - nx * w0 / 2, a[1] - ny * w0 / 2),
    ]


def arc_points(
    cx: float, cy: float, rx: float, ry: float, a0: float, a1: float, n: int = 24
) -> list[Point]:
    """Puntos de un arco de elipse (grados, 0 = derecha, sentido de las agujas del reloj)."""
    return [
        (
            cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
            cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
        )
        for i in range(n + 1)
    ]


def mix(a: Color, b: Color, t: float) -> Color:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def dark(color: Color, amount: float = 0.55) -> Color:
    return mix(color, (0, 0, 0), amount)


class Pen:
    """Dibuja en una máscara con coordenadas de 128 px; `v` 0 borra (agujeros)."""

    def __init__(self, draw: ImageDraw.ImageDraw) -> None:
        self.d = draw

    @staticmethod
    def _s(points: list[Point]) -> list[Point]:
        return [(x * K, y * K) for x, y in points]

    def poly(self, points: list[Point], v: int = 255) -> None:
        self.d.polygon(self._s(points), fill=v)

    def ell(self, cx: float, cy: float, rx: float, ry: float | None = None, v: int = 255) -> None:
        ry = rx if ry is None else ry
        self.d.ellipse(((cx - rx) * K, (cy - ry) * K, (cx + rx) * K, (cy + ry) * K), fill=v)

    def rr(self, x0: float, y0: float, x1: float, y1: float, r: float, v: int = 255) -> None:
        self.d.rounded_rectangle((x0 * K, y0 * K, x1 * K, y1 * K), radius=r * K, fill=v)

    def line(self, points: list[Point], w: float, v: int = 255) -> None:
        self.d.line(self._s(points), fill=v, width=round(w * K), joint="curve")
        for x, y in (points[0], points[-1]):
            self.ell(x, y, w / 2, v=v)

    def star(self, cx: float, cy: float, outer: float, inner: float, points: int = 4) -> None:
        coords = []
        for i in range(points * 2):
            r = outer if i % 2 == 0 else inner
            angle = -math.pi / 2 + math.pi * i / points
            coords.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        self.poly(coords)


class Art:
    """Un símbolo: se le van añadiendo piezas y al final se le pone borde, halo y se reduce."""

    def __init__(self, outline: Color, glow: Color) -> None:
        self.img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
        self.outline = outline
        self.glow = glow

    @staticmethod
    def _mask(fn) -> Image.Image:  # noqa: ANN001
        mask = Image.new("L", (N, N), 0)
        fn(Pen(ImageDraw.Draw(mask)))
        return mask

    def part(
        self,
        fn,  # noqa: ANN001
        top: Color,
        bottom: Color | None = None,
        *,
        edge: Color | None = None,
        edge_w: float = 1.8,
        shine: float = 0.5,
        volume: float = 1.0,
    ) -> Image.Image:
        """Pieza con degradado vertical, filo oscuro, volumen (sombra abajo, luz arriba) y reflejo."""
        mask = self._mask(fn)
        box = mask.getbbox()
        if box is None:
            return mask
        bottom = top if bottom is None else bottom
        if edge_w:
            grown = mask.filter(ImageFilter.MaxFilter(2 * round(edge_w * K) + 1))
            band = Image.new("RGBA", (N, N), (*(edge or dark(bottom)), 255))
            band.putalpha(grown)
            self.img.alpha_composite(band)
        t = np.clip((np.arange(N) - box[1]) / max(1, box[3] - box[1]), 0, 1)[:, None, None]
        pixels = np.array(top, np.float32) + (np.array(bottom, np.float32) - top) * t
        fill = Image.fromarray(np.repeat(pixels, N, axis=1).astype(np.uint8), "RGB").convert("RGBA")
        fill.putalpha(mask)
        self.img.alpha_composite(fill)
        if shine:
            rim = ImageChops.subtract(mask, ImageChops.offset(mask, 0, round(3.2 * K)))
            rim = ImageChops.multiply(rim.filter(ImageFilter.GaussianBlur(K * 0.7)), mask)
            light = Image.new("RGBA", (N, N), (255, 255, 255, 255))
            light.putalpha(rim.point(lambda v: int(v * shine)))
            self.img.alpha_composite(light)
        w, h = (box[2] - box[0]) / K, (box[3] - box[1]) / K
        if volume and min(w, h) > 9:
            # Sombra propia en el lado de abajo y de la derecha, para que la pieza tenga cuerpo.
            low = ImageChops.subtract(mask, ImageChops.offset(mask, round(-4 * K), round(-5 * K)))
            low = ImageChops.multiply(low.filter(ImageFilter.GaussianBlur(K * 1.6)), mask)
            shade = Image.new("RGBA", (N, N), (*dark(bottom, 0.8), 255))
            shade.putalpha(low.point(lambda v: int(v * 0.55 * volume)))
            self.img.alpha_composite(shade)
        if volume and min(w, h) > 18:
            # Reflejo especular blando arriba a la izquierda.
            spot = Image.new("L", (N, N), 0)
            x0, y0 = box[0] + (box[2] - box[0]) * 0.18, box[1] + (box[3] - box[1]) * 0.12
            ImageDraw.Draw(spot).ellipse(
                (x0, y0, x0 + w * K * 0.26, y0 + h * K * 0.14), fill=255
            )
            spot = ImageChops.multiply(spot.filter(ImageFilter.GaussianBlur(K * 1.4)), mask)
            gloss = Image.new("RGBA", (N, N), (255, 255, 255, 255))
            gloss.putalpha(spot.point(lambda v: int(v * 0.6 * volume)))
            self.img.alpha_composite(gloss)
        return mask

    def flat(self, fn, color: Color, alpha: int = 255) -> None:  # noqa: ANN001
        """Pieza lisa, sin borde (rayos de luz, destellos, brillos)."""
        layer = Image.new("RGBA", (N, N), (*color, 255))
        layer.putalpha(self._mask(fn).point(lambda v: v * alpha // 255))
        self.img.alpha_composite(layer)

    def sparkle(self, x: float, y: float, r: float) -> None:
        self.flat(lambda p: p.star(x, y, r, r / 4.2), (255, 255, 255))

    def text(self, x: float, y: float, text: str, size: int, fill: Color, stroke: Color) -> None:
        font = ImageFont.truetype(str(FONT), size * K)
        ImageDraw.Draw(self.img).text(
            (x * K, y * K), text, font=font, anchor="mm", fill=fill, stroke_width=K * 2,
            stroke_fill=stroke,
        )  # fmt: skip

    def ribbon(self, y: float, label: str = "WILD") -> None:
        """Cinta con «WILD» a lo ancho, como la del comodín del Volcán."""
        self.part(lambda p: p.rr(8, y - 11, 120, y + 11, 6), (232, 170, 60), (140, 80, 14),
                  edge=(40, 20, 10), shine=0.6)  # fmt: skip
        self.text(64, y + 1, label, 21, (255, 244, 205), (60, 30, 10))

    def finish(self) -> Image.Image:
        alpha = self.img.getchannel("A")
        halo = alpha.filter(ImageFilter.GaussianBlur(4 * K)).point(lambda v: max(0, v - 30))
        solid = alpha.filter(ImageFilter.GaussianBlur(3.8 * K)).point(
            lambda v: 255 if v > 22 else 0
        )
        solid = solid.filter(ImageFilter.GaussianBlur(0.7 * K))
        out = Image.new("RGBA", (N, N), (0, 0, 0, 0))
        glow = Image.new("RGBA", (N, N), (*self.glow, 255))
        glow.putalpha(halo.point(lambda v: v * 90 // 255))
        out.alpha_composite(glow)
        edge = Image.new("RGBA", (N, N), (*self.outline, 255))
        edge.putalpha(solid)
        out.alpha_composite(edge)
        out.alpha_composite(self.img)
        # Se recorta al contorno y se escala para llenar la casilla, como los del Volcán.
        box = solid.point(lambda v: 255 if v > 128 else 0).getbbox() or (0, 0, N, N)
        w, h = box[2] - box[0], box[3] - box[1]
        m = 3 * K  # margen para que el halo no se corte en seco
        f = min(FILL * K / (max(w, h) + 2 * m), 1.5)
        wide = (box[0] - m, box[1] - m, box[2] + m, box[3] + m)
        size = (round((w + 2 * m) * f / K), round((h + 2 * m) * f / K))
        small = out.crop(wide).resize(size, Image.Resampling.LANCZOS)
        final = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        final.alpha_composite(small, ((SIZE - size[0]) // 2, (SIZE - size[1]) // 2))
        return final


def badge(art: Art, inner_top: Color, inner_bottom: Color) -> None:
    """Placa redonda de recogedor: aro de oro y fondo de la máquina (como la del Volcán)."""
    art.part(lambda p: p.ell(64, 64, 56), (255, 232, 110), (190, 120, 14), edge=(50, 24, 10),
             shine=0.7)  # fmt: skip
    art.part(lambda p: p.ell(64, 64, 48), inner_top, inner_bottom, edge_w=1.0, shine=0.0,
             edge=dark(inner_bottom, 0.7))  # fmt: skip


# -- La Mina ---------------------------------------------------------------------------

MINA_OUT = (38, 20, 8)
GOLD_T, GOLD_B = (255, 230, 100), (205, 130, 18)
WOOD_T, WOOD_B = (200, 130, 60), (120, 66, 22)
STEEL_T, STEEL_B = (214, 222, 236), (88, 100, 128)


def mina_art() -> Art:
    return Art(MINA_OUT, (255, 190, 70))


def mina_pay0() -> Image.Image:
    """Pico: mango de madera en diagonal y cabeza de acero curvada."""
    art = mina_art()
    base, tip = (26, 112), (80, 50)
    art.part(lambda p: (p.poly(bar(base, tip, 11, 9)), p.ell(*base, 5.5), p.ell(*tip, 4.5)),
             WOOD_T, WOOD_B, shine=0.4)  # fmt: skip
    art.part(lambda p: p.poly(bar((33, 103), (39, 96), 12, 12)), (120, 40, 30), (70, 18, 14),
             shine=0.3)  # fmt: skip
    ux, uy = (tip[0] - base[0]), (tip[1] - base[1])
    length = math.hypot(ux, uy)
    ux, uy = ux / length, uy / length
    vx, vy = -uy, ux
    cx, cy = 84, 46
    outer, inner = [], []
    for i in range(-10, 11):
        t = i / 10
        px = cx + vx * 42 * t - ux * 20 * t * t
        py = cy + vy * 42 * t - uy * 20 * t * t
        w = 3 + 10 * (1 - abs(t) ** 1.4)
        outer.append((px + ux * w, py + uy * w))
        inner.append((px - ux * w * 0.9, py - uy * w * 0.9))
    art.part(lambda p: p.poly(outer + inner[::-1]), STEEL_T, STEEL_B, shine=0.8)
    art.part(lambda p: p.poly(bar((cx - ux * 9 - vx * 7, cy - uy * 9 - vy * 7),
                                  (cx - ux * 9 + vx * 7, cy - uy * 9 + vy * 7), 6)),
             (150, 156, 176), (80, 88, 110), edge_w=0.8, shine=0.2)  # fmt: skip
    art.sparkle(106, 26, 9)
    return art.finish()


def mina_pay1() -> Image.Image:
    """Lámpara de minero: farol de latón con la llama encendida y el asa arriba."""
    art = mina_art()
    art.part(lambda p: p.line(smooth([(42, 34), (44, 12), (64, 6), (84, 12), (86, 34)], closed=False), 6),
             STEEL_T, STEEL_B, shine=0.5)  # fmt: skip
    art.part(lambda p: p.rr(41, 36, 87, 98, 12), (255, 252, 214), (255, 168, 30), shine=0.5)
    art.flat(lambda p: p.ell(64, 70, 17, 25), (255, 214, 70), 255)
    art.part(lambda p: p.poly(smooth([(64, 44), (76, 64), (78, 80), (64, 94), (50, 80), (52, 64)])),
             (255, 255, 236), (255, 150, 20), edge_w=1.2, edge=(214, 100, 10), shine=0.0)  # fmt: skip
    art.flat(lambda p: p.poly(smooth([(64, 62), (70, 76), (64, 88), (58, 76)])), (255, 255, 255))
    for x in (47, 81):
        art.part(lambda p, x=x: p.poly(bar((x, 38), (x, 96), 5)), (150, 94, 40), (84, 46, 14),
                 edge_w=1.0, shine=0.3)  # fmt: skip
    art.part(lambda p: p.poly([(34, 40), (94, 40), (84, 22), (44, 22)]), (240, 170, 50), (150, 80, 10),
             shine=0.6)  # fmt: skip
    art.part(lambda p: p.rr(30, 92, 98, 112, 6), (240, 170, 50), (150, 80, 10), shine=0.6)
    art.part(lambda p: p.ell(64, 22, 7, 5), STEEL_T, STEEL_B, edge_w=1.0, shine=0.4)
    art.sparkle(100, 36, 11)
    art.sparkle(26, 56, 7)
    return art.finish()


def mina_pay2() -> Image.Image:
    """Carretilla: caja roja con una montaña de pepitas, rueda delante y dos mangos."""
    art = mina_art()
    art.part(lambda p: p.poly(bar((10, 98), (50, 74), 6, 6)), WOOD_T, WOOD_B, shine=0.3)
    art.part(lambda p: p.poly(bar((14, 86), (50, 66), 6, 6)), WOOD_T, WOOD_B, shine=0.3)
    art.part(lambda p: p.poly(smooth([(30, 50), (50, 30), (74, 32), (92, 44), (100, 52)])
                              + [(100, 56), (30, 56)]), (255, 226, 96), (214, 140, 24))  # fmt: skip
    for x, y, r in ((46, 44, 8), (64, 38, 9), (82, 46, 8), (58, 50, 7)):
        art.part(lambda p, x=x, y=y, r=r: p.ell(x, y, r), (255, 238, 140), (220, 150, 30),
                 edge_w=1.0, shine=0.6)  # fmt: skip
    body = [(20, 54), (108, 54), (94, 90), (40, 90)]
    art.part(lambda p: p.poly(body), (242, 90, 52), (160, 34, 22), shine=0.7)
    art.part(lambda p: p.poly(bar((24, 62), (104, 62), 5)), (255, 160, 110), (230, 100, 60),
             edge_w=0.6, shine=0.0)  # fmt: skip
    art.part(lambda p: p.poly(bar((68, 90), (96, 104), 6)), STEEL_T, STEEL_B, shine=0.3)
    art.part(lambda p: p.ell(98, 98, 15), (80, 80, 90), (30, 30, 38), shine=0.4)
    art.part(lambda p: p.ell(98, 98, 6), (230, 235, 245), (130, 140, 160), edge_w=0.8, shine=0.0)
    art.part(lambda p: p.poly(bar((38, 90), (34, 108), 6, 4)), WOOD_T, WOOD_B, shine=0.2)
    return art.finish()


def mina_pay3() -> Image.Image:
    """Dinamita: tres cartuchos rojos atados con cinta, mecha y chispa."""
    art = mina_art()
    art.part(lambda p: p.line([(70, 42), (74, 30), (88, 24), (96, 14)], 3.5), (90, 70, 50),
             (60, 44, 30), edge_w=1.0, shine=0.0)  # fmt: skip
    for x in (36, 56, 76):
        art.part(lambda p, x=x: p.rr(x, 38, x + 20, 112, 9), (255, 92, 70), (170, 26, 34),
                 shine=0.8)  # fmt: skip
        art.flat(lambda p, x=x: p.rr(x + 4, 48, x + 8, 100, 2), (255, 190, 170), 150)
    art.part(lambda p: p.poly(bar((30, 76), (102, 76), 15)), (250, 232, 170), (200, 160, 80),
             shine=0.5)  # fmt: skip
    art.part(lambda p: p.poly(bar((30, 76), (102, 76), 4)), (110, 60, 30), (110, 60, 30),
             edge_w=0.0, shine=0.0)  # fmt: skip
    art.flat(lambda p: p.ell(96, 14, 14), (255, 150, 30), 180)
    art.flat(lambda p: p.star(96, 14, 14, 5, 8), (255, 220, 70))
    art.flat(lambda p: p.star(96, 14, 8, 3, 8), (255, 255, 255))
    return art.finish()


def mina_pay4() -> Image.Image:
    """Pepita de oro: tres pedruscos angulosos de oro macizo con facetas y destellos."""
    art = mina_art()
    for pts in (
        [(8, 100), (6, 82), (18, 68), (34, 74), (38, 92), (28, 104)],
        [(88, 104), (84, 84), (96, 66), (114, 70), (122, 90), (112, 104)],
    ):
        art.part(lambda p, pts=pts: p.poly(pts), (255, 236, 120), (200, 118, 14), shine=0.6)
    big = [(22, 108), (14, 78), (30, 50), (52, 28), (78, 30), (98, 52), (104, 82), (94, 108)]
    art.part(lambda p: p.poly(big), (255, 232, 100), (206, 120, 12), shine=0.7)
    art.part(lambda p: p.poly([(30, 50), (52, 28), (66, 44), (56, 64), (28, 70)]), (255, 252, 200),
             (255, 222, 90), edge_w=0.0, shine=0.0, volume=0.0)  # fmt: skip
    art.part(lambda p: p.poly([(66, 44), (78, 30), (98, 52), (88, 70), (68, 68)]), (255, 214, 70),
             (234, 160, 24), edge_w=0.0, shine=0.0, volume=0.0)  # fmt: skip
    art.part(lambda p: p.poly([(56, 64), (88, 70), (104, 82), (94, 108), (60, 104)]), (236, 164, 28),
             (176, 90, 8), edge_w=0.0, shine=0.0, volume=0.0)  # fmt: skip
    art.part(lambda p: p.poly([(28, 70), (56, 64), (60, 104), (22, 108), (14, 78)]), (248, 196, 52),
             (200, 120, 14), edge_w=0.0, shine=0.0, volume=0.0)  # fmt: skip
    art.flat(lambda p: p.poly(bar((34, 56), (52, 40), 4)), (255, 255, 255), 200)
    art.sparkle(100, 30, 12)
    art.sparkle(22, 40, 8)
    art.sparkle(112, 100, 6)
    return art.finish()


def _ingot(art: Art, x: float, y: float, w: float, h: float) -> None:
    """Lingote de lado: tapa clara arriba y frente biselada."""
    inset = w * 0.16
    art.part(lambda p: p.poly([(x + inset, y), (x + w - inset, y), (x + w, y + h * 0.35),
                               (x, y + h * 0.35)]), (255, 248, 190), (255, 224, 100),
             shine=0.4)  # fmt: skip
    art.part(lambda p: p.poly([(x, y + h * 0.35), (x + w, y + h * 0.35), (x + w - inset * 0.5, y + h),
                               (x + inset * 0.5, y + h)]), (255, 214, 66), (200, 118, 12),
             shine=0.5)  # fmt: skip
    art.flat(lambda p: p.rr(x + w * 0.2, y + h * 0.5, x + w * 0.45, y + h * 0.62, 2), (255, 255, 230),
             170)  # fmt: skip


def mina_pay5() -> Image.Image:
    """Lingote: pirámide de tres barras de oro."""
    art = mina_art()
    _ingot(art, 8, 70, 56, 38)
    _ingot(art, 64, 70, 56, 38)
    _ingot(art, 36, 34, 56, 38)
    art.sparkle(100, 36, 11)
    art.sparkle(24, 52, 7)
    return art.finish()


def mina_wild() -> Image.Image:
    """Diamante en bruto: racimo de cristales sin tallar y la cinta WILD."""
    art = mina_art()
    ice_t, ice_b = (220, 255, 255), (40, 160, 214)
    art.part(lambda p: p.poly([(14, 98), (22, 62), (38, 50), (46, 70), (44, 98)]), (190, 240, 255),
             (30, 130, 200), shine=0.7)  # fmt: skip
    art.part(lambda p: p.poly([(114, 98), (106, 66), (92, 54), (84, 72), (86, 98)]), (190, 240, 255),
             (30, 130, 200), shine=0.7)  # fmt: skip
    art.part(lambda p: p.poly([(34, 100), (34, 44), (50, 20), (64, 8), (78, 20), (94, 44), (94, 100)]),
             ice_t, ice_b, shine=0.9)  # fmt: skip
    art.part(lambda p: p.poly([(64, 8), (50, 20), (34, 44), (64, 40)]), (255, 255, 255),
             (190, 238, 255), edge_w=0.0, shine=0.0)  # fmt: skip
    art.part(lambda p: p.poly([(64, 40), (94, 44), (78, 20), (64, 8)]), (120, 205, 245),
             (50, 150, 214), edge_w=0.0, shine=0.0)  # fmt: skip
    art.flat(lambda p: p.poly(bar((64, 40), (64, 100), 2.5)), (255, 255, 255), 130)
    art.sparkle(100, 26, 11)
    art.sparkle(22, 40, 6)
    art.ribbon(78)
    return art.finish()


def mina_collect() -> Image.Image:
    """Vagoneta de oro sobre raíles, dentro de una placa redonda."""
    art = mina_art()
    badge(art, (110, 78, 50), (40, 24, 14))
    art.flat(lambda p: p.ell(64, 56, 36, 28), (255, 200, 80), 70)
    art.part(lambda p: p.poly(bar((24, 98), (104, 98), 5)), (170, 120, 70), (90, 56, 28),
             edge_w=0.8, shine=0.3)  # fmt: skip
    for x, y, r in ((44, 40, 11), (64, 33, 12), (84, 40, 11), (54, 45, 9), (76, 46, 9)):
        art.part(lambda p, x=x, y=y, r=r: p.ell(x, y, r), (255, 240, 140), (222, 150, 24),
                 edge_w=1.0, shine=0.6)  # fmt: skip
    art.part(lambda p: p.poly([(22, 48), (106, 48), (94, 82), (34, 82)]), (232, 184, 50),
             (160, 96, 12), shine=0.7)  # fmt: skip
    art.part(lambda p: p.poly(bar((24, 56), (104, 56), 6)), (110, 120, 142), (60, 68, 88),
             edge_w=0.8, shine=0.4)  # fmt: skip
    for x in (44, 84):
        art.part(lambda p, x=x: p.ell(x, 90, 9), (90, 94, 108), (28, 30, 40), shine=0.4)
        art.part(lambda p, x=x: p.ell(x, 90, 3.5), (230, 235, 245), (130, 140, 160), edge_w=0.6,
                 shine=0.0)  # fmt: skip
    art.sparkle(96, 26, 9)
    return art.finish()


# -- El Olimpo -------------------------------------------------------------------------

OLIMPO_OUT = (10, 22, 66)


def olimpo_art() -> Art:
    return Art(OLIMPO_OUT, (150, 210, 255))


def olimpo_pay0() -> Image.Image:
    """Ánfora: dos asas, cuerpo panzudo de barro y una cenefa dorada."""
    art = olimpo_art()
    clay_t, clay_b = (248, 150, 70), (170, 70, 28)
    for sign in (-1, 1):
        handle = smooth([(64 + sign * 12, 24), (64 + sign * 36, 24), (64 + sign * 42, 46),
                         (64 + sign * 30, 66)], closed=False)  # fmt: skip
        art.part(lambda p, handle=handle: p.line(handle, 8), clay_t, clay_b, shine=0.4)
    body = smooth([(52, 12), (76, 12), (72, 26), (74, 36), (92, 52), (94, 76), (82, 98), (74, 112),
                   (54, 112), (46, 98), (34, 76), (36, 52), (54, 36), (56, 26)])  # fmt: skip
    art.part(lambda p: p.poly(body), clay_t, clay_b, shine=0.8)
    art.part(lambda p: p.rr(46, 8, 82, 18, 5), (255, 226, 110), (200, 130, 20), shine=0.7)
    art.part(lambda p: p.poly(bar((38, 66), (90, 66), 16)), (60, 24, 14), (60, 24, 14),
             edge_w=0.0, shine=0.0)  # fmt: skip
    zig = [(40 + i * 6, 62 if i % 2 == 0 else 72) for i in range(9)]
    art.flat(lambda p: p.line(zig, 3), (255, 220, 100))
    art.part(lambda p: p.rr(52, 108, 76, 116, 4), (255, 226, 110), (200, 130, 20), shine=0.5)
    return art.finish()


def olimpo_pay1() -> Image.Image:
    """Lira: dos brazos curvos, travesaño y cuerdas sobre la caja de resonancia."""
    art = olimpo_art()
    for k in range(5):
        x = 44 + k * 10
        art.flat(lambda p, x=x: p.poly(bar((x, 30), (x, 92), 2.6)), (255, 255, 255))
    for sign in (-1, 1):
        pts = smooth([(64 + sign * 20, 100), (64 + sign * 40, 76), (64 + sign * 38, 40),
                      (64 + sign * 26, 16)], closed=False)  # fmt: skip
        art.part(lambda p, pts=pts: p.line(pts, 9), GOLD_T, GOLD_B, shine=0.7)
        art.part(lambda p, sign=sign: p.ell(64 + sign * 26, 15, 7), (255, 244, 170), GOLD_B,
                 shine=0.6)  # fmt: skip
    art.part(lambda p: p.rr(36, 22, 92, 32, 5), (255, 236, 130), (210, 140, 20), shine=0.7)
    art.part(lambda p: p.poly(smooth([(36, 96), (64, 86), (92, 96), (90, 112), (64, 118), (38, 112)])),
             (255, 226, 100), (190, 110, 14), shine=0.8)  # fmt: skip
    art.part(lambda p: p.ell(64, 104, 8, 5), (110, 50, 20), (60, 24, 10), edge_w=0.6, shine=0.0)
    art.sparkle(100, 40, 7)
    return art.finish()


def olimpo_pay2() -> Image.Image:
    """Casco corintio de perfil: cúpula, penacho de crin, carrillera con rendija y nasal."""
    art = olimpo_art()
    bronze_t, bronze_b = (255, 232, 120), (176, 100, 18)
    # Crin del penacho: de la frente a la nuca, cayendo por detrás.
    crest = smooth([(84, 30), (66, 10), (38, 6), (14, 20), (6, 46), (10, 72), (20, 78), (24, 56),
                    (34, 38), (56, 32)])  # fmt: skip
    art.part(lambda p: p.poly(crest), (255, 110, 110), (176, 18, 40), shine=0.7)
    for i in range(5):
        x = 22 + i * 12
        art.flat(lambda p, x=x, i=i: p.poly(bar((x, 12 + abs(i - 2) * 3), (x - 6, 40), 2.4)),
                 (255, 190, 190), 150)  # fmt: skip
    # Cúpula, carrillera hacia delante y cubrenuca que se abre por detrás.
    shell = smooth([(14, 104), (22, 70), (24, 46), (42, 30), (68, 26), (92, 38), (104, 60), (104, 86),
                    (98, 108), (86, 118), (72, 114), (68, 96), (62, 84), (46, 88), (36, 104)])  # fmt: skip
    art.part(lambda p: p.poly(shell), bronze_t, bronze_b, shine=0.9)
    # Visera dorada y rendija del ojo.
    art.part(lambda p: p.poly(bar((30, 48), (100, 56), 6, 7)), (255, 248, 190), (226, 150, 30),
             edge_w=1.0, shine=0.3)  # fmt: skip
    art.part(lambda p: p.poly(smooth([(60, 62), (74, 58), (92, 64), (86, 76), (70, 76)])),
             (20, 12, 10), (50, 30, 20), edge_w=1.0, edge=(90, 50, 10), shine=0.0, volume=0.0)  # fmt: skip
    art.flat(lambda p: p.ell(80, 67, 3, 2), (255, 255, 255))
    # Nasal: la barra del frente que baja hasta la boca.
    art.part(lambda p: p.poly(bar((96, 60), (92, 100), 9, 6)), (255, 250, 200), (216, 140, 26),
             edge_w=1.0, shine=0.4)  # fmt: skip
    art.part(lambda p: p.poly(smooth([(54, 96), (66, 96), (70, 108), (58, 112)])), (180, 110, 24),
             (110, 56, 8), edge_w=0.0, shine=0.0, volume=0.0)  # fmt: skip
    art.sparkle(30, 34, 8)
    return art.finish()


def _leaf(cx: float, cy: float, ang: float, length: float, width: float) -> list[Point]:
    """Hoja apuntando a `ang`, con la base en (cx, cy)."""
    base = [(0, 0), (0.3, 0.45), (0.7, 0.4), (1, 0), (0.7, -0.4), (0.3, -0.45)]
    c, s_ = math.cos(ang), math.sin(ang)
    return smooth(
        [
            (
                cx + (x * length) * c - (y * width * 2) * s_,
                cy + (x * length) * s_ + (y * width * 2) * c,
            )
            for x, y in base
        ]
    )


def olimpo_pay3() -> Image.Image:
    """Laurel: corona de hojas abierta por arriba y lazo dorado."""
    art = olimpo_art()
    leaf_t, leaf_b = (150, 232, 90), (30, 130, 60)
    for sign in (-1, 1):
        for i in range(9):
            theta = math.radians(84 - i * 17)  # de abajo a arriba por el lado derecho
            cx, cy = 64 + 38 * math.cos(theta), 62 + 38 * math.sin(theta)
            tangent = theta - math.pi / 2  # hacia arriba, siguiendo el aro
            for side in (-1, 1):
                ang = tangent + side * 0.62
                ox, oy = cx + math.cos(theta) * side * 4, cy + math.sin(theta) * side * 4
                pts = _leaf(ox, oy, ang, 21, 7.5)
                if sign < 0:
                    pts = [(128 - x, y) for x, y in pts]
                art.part(lambda p, pts=pts: p.poly(pts), leaf_t, leaf_b, edge_w=1.0, shine=0.5)
    art.part(lambda p: p.poly([(64, 100), (46, 118), (54, 122), (64, 112), (74, 122), (82, 118)]),
             (255, 226, 100), (200, 120, 16), shine=0.6)  # fmt: skip
    art.part(lambda p: p.ell(64, 104, 7), (255, 240, 150), (210, 130, 20), shine=0.7)
    return art.finish()


def olimpo_pay4() -> Image.Image:
    """Tridente: mango largo y tres puntas con arpón, en plata de mar."""
    art = olimpo_art()
    t, b = (200, 244, 255), (50, 140, 200)
    art.part(lambda p: p.poly(bar((64, 52), (64, 118), 8, 6)), t, b, shine=0.5)
    art.part(lambda p: p.line(smooth([(26, 20), (26, 50), (44, 64), (64, 64), (84, 64), (102, 50),
                                      (102, 20)], closed=False), 7), t, b, shine=0.6)  # fmt: skip
    art.part(lambda p: p.poly(bar((64, 64), (64, 22), 8)), t, b, shine=0.5)
    for x in (26, 64, 102):
        art.part(lambda p, x=x: p.poly([(x, 4), (x + 12, 24), (x + 4, 24), (x + 4, 30), (x - 4, 30),
                                        (x - 4, 24), (x - 12, 24)]), (255, 240, 150), GOLD_B,
                 shine=0.7)  # fmt: skip
    art.part(lambda p: p.rr(52, 58, 76, 70, 5), (255, 236, 130), GOLD_B, shine=0.7)
    art.sparkle(104, 54, 7)
    return art.finish()


def olimpo_pay5() -> Image.Image:
    """Águila: alas abiertas con plumas escalonadas, cabeza blanca, pico y cola."""
    art = olimpo_art()
    wing_t, wing_b = (232, 160, 50), (120, 64, 10)
    for sign in (-1, 1):
        wing = [(72, 52), (96, 30), (122, 10), (112, 32), (124, 40), (112, 54), (120, 64),
                (104, 72), (108, 84), (88, 84), (74, 80)]  # fmt: skip
        wing = [(64 + sign * (x - 64), y) for x, y in wing]
        art.part(lambda p, wing=wing: p.poly(wing), wing_t, wing_b, shine=0.7)
        for fx, fy, tx, ty in ((80, 56, 112, 42), (80, 66, 112, 62), (80, 76, 102, 80)):
            art.flat(
                lambda p, fx=fx, fy=fy, tx=tx, ty=ty, sign=sign: p.line(
                    [(64 + sign * (fx - 64), fy), (64 + sign * (tx - 64), ty)], 2
                ),
                (100, 50, 6),
                210,
            )
    art.part(lambda p: p.poly([(52, 82), (76, 82), (86, 116), (64, 108), (42, 116)]), (255, 250, 240),
             (190, 200, 224), shine=0.5)  # fmt: skip
    art.part(lambda p: p.poly(smooth([(50, 60), (78, 60), (80, 90), (64, 98), (48, 90)])), wing_t,
             wing_b, shine=0.6)  # fmt: skip
    art.part(lambda p: p.ell(64, 44, 15, 16), (255, 255, 255), (196, 208, 232), shine=0.5)
    art.part(lambda p: p.poly([(70, 42), (92, 50), (78, 62), (72, 54)]), (255, 214, 70),
             (214, 130, 16), edge_w=1.0, shine=0.5)  # fmt: skip
    art.part(lambda p: p.ell(68, 40, 3.2), (40, 24, 8), (20, 10, 4), edge_w=0.0, shine=0.0)
    art.flat(lambda p: p.ell(69, 39, 1.1), (255, 255, 255))
    art.sparkle(104, 20, 7)
    return art.finish()


def olimpo_wild() -> Image.Image:
    """Rayo de Zeus: zigzag amarillo con núcleo blanco y la cinta WILD."""
    art = olimpo_art()
    bolt = [(82, 4), (34, 64), (58, 64), (40, 122), (96, 50), (68, 50), (92, 4)]
    art.part(lambda p: p.poly(bolt), (255, 252, 190), (255, 184, 20), shine=0.9)
    art.flat(lambda p: p.poly([(82, 14), (46, 62), (62, 62), (50, 104), (82, 54), (64, 54), (84, 14)]),
             (255, 255, 255), 190)  # fmt: skip
    art.sparkle(22, 26, 10)
    art.sparkle(108, 98, 8)
    art.ribbon(74)
    return art.finish()


def olimpo_collect() -> Image.Image:
    """Zeus: cabeza barbuda de pelo blanco con corona de laurel, sobre un cielo con rayo."""
    art = olimpo_art()
    badge(art, (120, 200, 255), (30, 70, 170))
    art.flat(lambda p: p.poly([(92, 18), (70, 52), (84, 52), (74, 80), (104, 40), (90, 40), (106, 18)]),
             (255, 236, 90), 235)  # fmt: skip
    hair = smooth([(28, 62), (26, 40), (40, 22), (64, 16), (88, 22), (102, 40), (100, 62),
                   (96, 86), (80, 108), (64, 112), (48, 108), (32, 86)])  # fmt: skip
    art.part(lambda p: p.poly(hair), (255, 255, 255), (186, 204, 236), edge_w=1.2, shine=0.5)
    art.part(lambda p: p.ell(64, 54, 22, 25), (255, 214, 168), (222, 154, 108), edge_w=1.1,
             shine=0.4)  # fmt: skip
    beard = smooth([(38, 62), (50, 66), (64, 62), (78, 66), (90, 62), (86, 88), (74, 106), (64, 110),
                    (54, 106), (42, 88)])  # fmt: skip
    art.part(lambda p: p.poly(beard), (255, 255, 255), (186, 204, 236), edge_w=1.2, shine=0.6)
    art.part(lambda p: p.poly(smooth([(52, 72), (64, 68), (76, 72), (64, 80)])), (170, 120, 96),
             (140, 90, 70), edge_w=0.0, shine=0.0)  # fmt: skip
    for sign in (-1, 1):
        art.part(lambda p, sign=sign: p.poly(bar((64 + sign * 4, 47), (64 + sign * 16, 41), 5, 3)),
                 (110, 110, 130), (70, 70, 90), edge_w=0.8, shine=0.0)  # fmt: skip
        art.part(lambda p, sign=sign: p.ell(64 + sign * 10, 52, 3), (40, 80, 200), (20, 40, 120),
                 edge_w=0.6, shine=0.0)  # fmt: skip
    art.part(lambda p: p.poly(bar((64, 52), (64, 66), 4, 6)), (236, 176, 130), (200, 130, 92),
             edge_w=0.8, shine=0.0)  # fmt: skip
    art.part(lambda p: p.line(smooth([(38, 36), (50, 28), (64, 26), (78, 28), (90, 36)],
                                     closed=False), 4), (255, 236, 110), (210, 140, 20),
             edge_w=1.0, shine=0.5)  # fmt: skip
    return art.finish()


# -- Programa -------------------------------------------------------------------------

THEMES: dict[str, dict[str, object]] = {
    "mina": {
        "pay0": mina_pay0, "pay1": mina_pay1, "pay2": mina_pay2, "pay3": mina_pay3,
        "pay4": mina_pay4, "pay5": mina_pay5, "wild": mina_wild, "collect": mina_collect,
    },
    "olimpo": {
        "pay0": olimpo_pay0, "pay1": olimpo_pay1, "pay2": olimpo_pay2, "pay3": olimpo_pay3,
        "pay4": olimpo_pay4, "pay5": olimpo_pay5, "wild": olimpo_wild, "collect": olimpo_collect,
    },
}  # fmt: skip


def main(argv: list[str]) -> None:
    sheet = "--hoja" in argv
    wanted = [a for a in argv if not a.startswith("--")] or list(THEMES)
    for theme in wanted:
        images = []
        for name, draw in THEMES[theme].items():
            image = draw()  # type: ignore[operator]
            image.save(ASSETS / f"{theme}_{name}.png", optimize=True)
            images.append(image)
        print(f"{theme}: {len(images)} símbolos en {ASSETS}")
        if sheet:
            contact = Image.new("RGBA", (SIZE * 8, SIZE), (70, 24, 70, 255))
            for i, image in enumerate(images):
                contact.alpha_composite(image, (i * SIZE, 0))
            contact.save(ROOT / f"hoja_{theme}.png")


if __name__ == "__main__":
    main(sys.argv[1:])
