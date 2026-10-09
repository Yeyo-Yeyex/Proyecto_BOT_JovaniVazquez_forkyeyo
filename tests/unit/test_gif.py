"""Pruebas del GIF rápido (`bot.utils.gif`): se ve igual que los fotogramas originales."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from bot.utils.gif import local_palette_gif, shared_palette_gif

SIZE = (60, 40)


def decoded(gif: bytes) -> list[tuple[np.ndarray, int]]:
    """Lo que enseña un visor: cada fotograma ya compuesto, en RGB, con su duración."""
    image = Image.open(io.BytesIO(gif))
    out = []
    for index in range(image.n_frames):
        image.seek(index)
        out.append((np.asarray(image.convert("RGB")), image.info.get("duration", 0)))
    return out


def timeline(frames: list[tuple[np.ndarray, int]]) -> list[tuple[bytes, int]]:
    """Fotogramas seguidos iguales juntos: Pillow los funde en uno y suma sus duraciones."""
    out: list[tuple[bytes, int]] = []
    for pixels, duration in frames:
        key = pixels.tobytes()
        if out and out[-1][0] == key:
            out[-1] = (key, out[-1][1] + duration)
        else:
            out.append((key, duration))
    return out


def moving_square(count: int) -> list[Image.Image]:
    """Un fondo con franjas y un cuadrado que se mueve: la mayoría de píxeles no cambia."""
    frames = []
    for step in range(count):
        image = Image.new("RGB", SIZE, (30, 30, 40))
        pixels = np.asarray(image).copy()
        pixels[::4] = (90, 20, 120)
        pixels[10:20, 5 + step * 3 : 15 + step * 3] = (250, 200, 60)
        frames.append(Image.fromarray(pixels, "RGB"))
    return frames


def shared(frames: list[Image.Image], colors: int) -> list[Image.Image]:
    palette = frames[0].quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    return [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]


def expected(frames: list[Image.Image], durations: list[int]) -> list[tuple[bytes, int]]:
    rgb = [(np.asarray(f.convert("RGB")), d) for f, d in zip(frames, durations, strict=True)]
    return timeline(rgb)


def test_paleta_comun_se_ve_igual_que_los_fotogramas() -> None:
    frames = shared(moving_square(8), colors=16)
    durations = [50] * 7 + [1000]
    gif = shared_palette_gif(frames, durations)
    assert timeline(decoded(gif)) == expected(frames, durations)


def test_paleta_comun_funde_los_fotogramas_repetidos_sin_perder_tiempo() -> None:
    """Como en los destellos de la tragaperras: el mismo fotograma varias veces seguidas."""
    base = shared(moving_square(3), colors=16)
    frames = [base[0], base[1], base[1], base[1], base[2]]
    durations = [40, 40, 40, 40, 900]
    gif = shared_palette_gif(frames, durations)
    assert timeline(decoded(gif)) == expected(frames, durations)


def test_paleta_comun_llena_tambien_se_ve_igual() -> None:
    """Con los 256 colores en uso no queda índice transparente y cada fotograma va entero."""
    gradient = np.arange(256, dtype=np.uint8).reshape(16, 16)
    frames = []
    for shift in range(3):
        image = Image.fromarray(np.roll(gradient, shift, axis=1), "P")
        image.putpalette([v for i in range(256) for v in (i, 255 - i, (i * 7) % 256)])
        frames.append(image)
    durations = [60, 60, 500]
    gif = shared_palette_gif(frames, durations)
    assert timeline(decoded(gif)) == expected(frames, durations)


def test_paleta_comun_no_engorda_la_paleta() -> None:
    """El transparente reutiliza un hueco de la paleta: 16 colores siguen siendo 4 bits."""
    frames = shared(moving_square(4), colors=16)
    gif = Image.open(io.BytesIO(shared_palette_gif(frames, 50)))
    assert len(gif.getpalette() or []) // 3 <= 16


def test_paleta_comun_repite_o_no_segun_loop() -> None:
    frames = shared(moving_square(3), colors=8)
    assert "loop" in Image.open(io.BytesIO(shared_palette_gif(frames, 50))).info
    assert "loop" not in Image.open(io.BytesIO(shared_palette_gif(frames, 50, loop=False))).info


def test_paleta_comun_rechaza_paletas_distintas_o_ninguna() -> None:
    a = shared(moving_square(2), colors=8)
    other = a[1].copy()
    other.putpalette([255 - v for v in a[1].getpalette() or []])
    with pytest.raises(ValueError):
        shared_palette_gif([a[0], other], 50)
    with pytest.raises(ValueError):
        shared_palette_gif([a[0], a[1].convert("RGB")], 50)
    with pytest.raises(ValueError):
        shared_palette_gif([], 50)


def test_paleta_local_se_ve_igual_si_caben_los_colores() -> None:
    """Pocos colores en cada zona que cambia: la paleta propia los guarda exactos."""
    frames = moving_square(6)
    durations = [70] * 5 + [2000]
    gif = local_palette_gif(frames, durations)
    assert timeline(decoded(gif)) == expected(frames, durations)


def test_paleta_local_rechaza_ninguno() -> None:
    with pytest.raises(ValueError):
        local_palette_gif([], 50)
