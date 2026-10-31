"""Saca las texturas del juego del pico (`picar`) del pack Pixel Perfection CE.

Uso:

    git clone --depth 1 https://github.com/Athemis/pixelperfectionce ../pixelperfectionce
    python -I docs/picar_texturas.py ../pixelperfectionce

Escribe `src/bot/assets/picar/texturas.png` (un atlas de casillas de 16 × 16 en una
fila) y `src/bot/assets/picar/texturas.json` (la columna de cada clave). El dibujo
(`bot.services.picar_scene`) los lee de ahí; este script solo hace falta para volver
a sacarlos.

Las texturas son de Pixel Perfection CE (XSSheep y la comunidad), con licencia
CC BY-SA 4.0: se pueden tener en un repo público citando a los autores y con la misma
licencia, que va en `src/bot/assets/picar/LICENCIA.md` junto con lo que se ha cambiado.
Lo que el pack no trae (es de la 1.16) se saca de lo que sí trae:

- La pizarra profunda es la piedra oscurecida y tirando a azul.
- Los minerales de la profundidad son los de la piedra con su mena pegada sobre la
  pizarra (la mena son los píxeles en los que el mineral difiere de la piedra).
- El cristal del End es la mena del diamante, en morado, sobre la piedra del End.
- Los cofres son la cara del cofre del pack (tapa y caja juntas, con el cierre); el del
  tesoro y la caja de shulker, el mismo teñido de oro y de morado.
- El bloque misterioso es el bloque de oro, más naranja, con un marco oscuro y una «?»
  de píxeles (`QUESTION`), a la manera de los «lucky blocks».

El dragón es dibujo propio (`DRAGON`, más abajo) y va con la misma licencia.

Solo usa Pillow y la biblioteca estándar: se ejecuta con `-I` porque lee una descarga.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "src" / "bot" / "assets" / "picar"
TILE = 16

#: Clave -> archivo del pack, relativo a `assets/minecraft/textures/`.
DIRECT = {
    "dirt": "block/dirt.png",
    "grass": "block/grass_block_side.png",
    "stone": "block/stone.png",
    "coal": "block/coal_ore.png",
    "iron": "block/iron_ore.png",
    "gold": "block/gold_ore.png",
    "redstone": "block/redstone_ore.png",
    "lapis": "block/lapis_ore.png",
    "diamond": "block/diamond_ore.png",
    "emerald": "block/emerald_ore.png",
    "tnt": "block/tnt_side.png",
    "crafting_table": "block/crafting_table_front.png",
    "netherrack": "block/netherrack.png",
    "blackstone": "block/blackstone.png",
    "quartz": "block/nether_quartz_ore.png",
    "nether_gold": "block/nether_gold_ore.png",
    "ancient_debris": "block/ancient_debris_side.png",
    "end_stone": "block/end_stone.png",
    "obsidian": "block/obsidian.png",
    "bedrock": "block/bedrock.png",
    "gold_block": "block/gold_block.png",
    "diamond_block": "block/diamond_block.png",
    "netherite_block": "block/netherite_block.png",
    "beacon": "block/beacon.png",
    "portal": "block/nether_portal.png",
    "pick_wood": "item/wooden_pickaxe.png",
    "pick_stone": "item/stone_pickaxe.png",
    "pick_iron": "item/iron_pickaxe.png",
    "pick_gold": "item/golden_pickaxe.png",
    "pick_diamond": "item/diamond_pickaxe.png",
    "pick_netherite": "item/netherite_pickaxe.png",
    **{f"crack_{i}": f"block/destroy_stage_{i}.png" for i in range(10)},
}

#: Minerales de la capa honda: la mena de la versión en piedra sobre la pizarra.
DEEP_ORES = ("iron", "gold", "redstone", "lapis", "diamond", "emerald")

#: Medida del dragón (dibujo propio, simétrico: se dibuja la mitad izquierda y se refleja).
DRAGON_SIZE = (64, 34)


def _load(pack: Path, name: str) -> Image.Image:
    image = Image.open(pack / "assets" / "minecraft" / "textures" / name).convert("RGBA")
    # Las texturas animadas (el portal) son una tira vertical: vale el primer cuadro.
    return image.crop((0, 0, TILE, TILE)) if image.size != (TILE, TILE) else image


def _tint(image: Image.Image, factors: tuple[float, float, float]) -> Image.Image:
    r, g, b, a = image.split()
    return Image.merge(
        "RGBA",
        [
            channel.point(lambda v, f=f: max(0, min(255, round(v * f))))
            for channel, f in zip((r, g, b), factors, strict=True)
        ]
        + [a],
    )


def _ore_on(ore: Image.Image, stone: Image.Image, base: Image.Image) -> Image.Image:
    """La mena de `ore` (lo que difiere de `stone`) pegada sobre `base`."""
    result = base.copy()
    for y in range(TILE):
        for x in range(TILE):
            pixel = ore.getpixel((x, y))
            if pixel != stone.getpixel((x, y)):
                result.putpixel((x, y), pixel)
    return result


def _chest(pack: Path) -> Image.Image:
    """La cara del cofre: frente de la tapa, frente de la caja y el cierre."""
    sheet = Image.open(pack / "assets/minecraft/textures/entity/chest/normal.png").convert("RGBA")
    face = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    face.paste(sheet.crop((14, 14, 28, 19)), (1, 1))
    face.paste(sheet.crop((14, 33, 28, 43)), (1, 6))
    face.alpha_composite(sheet.crop((1, 1, 3, 5)), (7, 4))
    return face


#: La «?» del bloque misterioso, 6 × 9 píxeles («#» es tinta).
QUESTION = (
    ".####.",
    "##..##",
    "....##",
    "...##.",
    "..##..",
    "..##..",
    "......",
    "..##..",
    "..##..",
)


def _mystery(gold: Image.Image) -> Image.Image:
    """El bloque misterioso: el de oro, más naranja, con marco y una «?» en medio."""
    tile = _tint(gold, (1.05, 0.82, 0.45))
    ink, shade = (70, 34, 8, 255), (255, 236, 150, 255)
    for i in range(TILE):
        for x, y in ((i, 0), (i, TILE - 1), (0, i), (TILE - 1, i)):
            tile.putpixel((x, y), ink)
    left, top = (TILE - len(QUESTION[0])) // 2, (TILE - len(QUESTION)) // 2
    for dy, line in enumerate(QUESTION):
        for dx, char in enumerate(line):
            if char == "#":
                tile.putpixel((left + dx + 1, top + dy + 1), ink)
                tile.putpixel((left + dx, top + dy), shade)
    return tile


def _dragon() -> Image.Image:
    """El dragón del fondo del End: alas abiertas, cuernos y ojos morados.

    Polígonos sin suavizado sobre un lienzo pequeño, que al ampliarse sin suavizar se
    ven como pixel art. Se dibuja la mitad izquierda y se refleja.
    """
    from PIL import ImageDraw

    w, h = DRAGON_SIZE
    body, scale, membrane = (22, 19, 30, 255), (52, 48, 64, 255), (70, 62, 88, 255)
    bone, eye, glow = (120, 112, 140, 255), (232, 120, 255, 255), (150, 60, 200, 255)
    half = Image.new("RGBA", (w // 2, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(half)
    # Ala: membrana con el borde de abajo festoneado y los huesos encima.
    d.polygon(
        [
            (31, 12),
            (22, 7),
            (12, 3),
            (1, 0),
            (3, 6),
            (5, 13),
            (9, 12),
            (11, 18),
            (16, 16),
            (19, 22),
            (24, 19),
            (28, 22),
            (31, 20),
        ],
        fill=membrane,
    )
    for a, b in (((31, 12), (1, 0)), ((22, 7), (5, 13)), ((15, 4), (11, 18)), ((24, 8), (19, 22))):
        d.line([a, b], fill=bone)
    d.line([(1, 0), (3, 1)], fill=(200, 190, 215, 255))
    # Cuerpo, patas y cola.
    d.ellipse((24, 12, 39, 30), fill=body)
    d.rectangle((26, 15, 31, 27), fill=scale)
    d.polygon([(27, 26), (24, 31), (26, 31), (29, 27)], fill=body)
    d.polygon([(30, 29), (31, 33), (32, 33), (32, 29)], fill=body)
    # Cuello y cabeza, con cuernos y el ojo.
    d.rectangle((28, 6, 31, 14), fill=body)
    d.polygon([(24, 2), (31, 1), (31, 9), (26, 9), (24, 6)], fill=body)
    d.polygon([(25, 2), (23, 0), (26, 1)], fill=bone)
    d.line([(25, 7), (30, 7)], fill=scale)
    d.rectangle((26, 3, 28, 4), fill=eye)
    d.point((29, 4), fill=glow)
    for x, y in ((28, 17), (29, 21), (27, 24), (30, 13)):
        d.point((x, y), fill=bone)
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    image.paste(half, (0, 0))
    image.paste(half.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (w // 2, 0))
    return image


def main(pack: Path) -> None:
    tiles: dict[str, Image.Image] = {key: _load(pack, name) for key, name in DIRECT.items()}
    stone = tiles["stone"]
    tiles["deepslate"] = _tint(stone, (0.48, 0.5, 0.6))
    for key in DEEP_ORES:
        tiles[f"deep_{key}"] = _ore_on(tiles[key], stone, tiles["deepslate"])
    purple = _tint(tiles["diamond"], (1.3, 0.55, 1.5))
    tiles["end_crystal"] = _ore_on(purple, _tint(stone, (1.3, 0.55, 1.5)), tiles["end_stone"])
    chest = _chest(pack)
    tiles["chest"] = chest
    tiles["treasure"] = _tint(chest, (1.6, 1.25, 0.35))
    tiles["shulker"] = _tint(chest, (1.0, 0.6, 1.25))
    tiles["mystery"] = _mystery(tiles["gold_block"])

    keys = sorted(tiles)
    atlas = Image.new("RGBA", (TILE * len(keys), TILE), (0, 0, 0, 0))
    for i, key in enumerate(keys):
        atlas.paste(tiles[key], (i * TILE, 0))
    OUT.mkdir(parents=True, exist_ok=True)
    atlas.save(OUT / "texturas.png", optimize=True)
    _dragon().save(OUT / "dragon.png", optimize=True)
    index = {key: i for i, key in enumerate(keys)}
    (OUT / "texturas.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    print(f"{len(keys)} casillas en {OUT / 'texturas.png'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
