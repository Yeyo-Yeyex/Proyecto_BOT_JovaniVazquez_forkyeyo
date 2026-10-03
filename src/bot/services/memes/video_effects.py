"""Efectos que superponen texto a un vídeo o a un GIF de plantilla.

`imgen` los generaba con MoviePy. Aquí el texto se dibuja con Pillow en una
capa PNG y `ffmpeg` (ya instalado en la imagen Docker por la música) la
superpone al vídeo: así no se añade ninguna dependencia de Python. `kowalski`
es un GIF y se compone fotograma a fotograma con Pillow.

Cada llamada a `ffmpeg` usa un directorio temporal propio que se borra al
terminar, tiene un tiempo máximo y se limita a dos hilos para no saturar
el NAS.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from bot.services.memes.registry import MemeRequest, MemeResult, effect, encode_gif
from bot.services.memes.toolkit import asset_path, draw_fitted_text, frames, paste, render_caption

# Segundos máximos de una codificación; los tres vídeos tardan bastante menos.
FFMPEG_TIMEOUT_SECONDS = 90


def _overlay_video(
    template: str,
    overlay: Image.Image,
    *,
    filters: str,
    duration: float | None = None,
) -> MemeResult:
    """Superpone `overlay` al vídeo de plantilla con el grafo de filtros indicado.

    En `filters`, `[0:v]` es el vídeo y `[ov]` la capa ya convertida a RGBA;
    el grafo debe producir la salida `[out]`.

    Raises:
        subprocess.CalledProcessError: Si ffmpeg falla.
        subprocess.TimeoutExpired: Si tarda más de `FFMPEG_TIMEOUT_SECONDS`.
    """
    with tempfile.TemporaryDirectory(prefix="meme-") as directory:
        overlay_path = Path(directory) / "overlay.png"
        output_path = Path(directory) / "out.mp4"
        overlay.save(overlay_path)
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(asset_path(template)),
            "-loop",
            "1",
            "-i",
            str(overlay_path),
            "-filter_complex",
            f"[1:v]format=rgba[ov];{filters}",
            "-map",
            "[out]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "superfast",
            "-crf",
            "26",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-threads",
            "2",
            "-movflags",
            "+faststart",
            "-shortest",
        ]
        if duration is not None:
            command += ["-t", str(duration)]
        command.append(str(output_path))
        subprocess.run(command, check=True, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS)
        return MemeResult(output_path.read_bytes(), "mp4")


@effect(
    "crab", "Rave de cangrejos con tu texto.", texts=2, example="se fue | el lunes", output="mp4"
)
def crab(request: MemeRequest) -> MemeResult:
    top, bottom = (text.upper() for text in request.texts)
    layer = Image.new("RGBA", (854, 480))
    # El original centraba tres textos: arriba, una raya de separación y abajo.
    for text, y in ((top, 200), ("____________________", 210), (bottom, 270)):
        caption = render_caption(text, "verdana.ttf", 48, "white")
        paste(layer, caption, ((layer.width - caption.width) // 2, y))
    return _overlay_video(
        "crab/template",
        layer,
        filters="[ov]fade=in:st=0:d=1:alpha=1[faded];[0:v][faded]overlay=0:0:shortest=1[out]",
        duration=15.4,
    )


@effect("letmein", "«¡Déjame entrar!»", texts=1, example="cuando cierran la cocina", output="mp4")
def letmein(request: MemeRequest) -> MemeResult:
    text = request.text if len(request.text) < 400 else request.text[:400] + "..."
    caption = render_caption(text, "verdana.ttf", 32, "black", width=640, background="white")
    # H.264 con yuv420p exige alto par.
    height = caption.height + caption.height % 2
    return _overlay_video(
        "letmein/letmein",
        caption,
        filters=f"[0:v]pad=640:ih+{height}:0:{height}:white[padded];"
        "[padded][ov]overlay=0:0:shortest=1[out]",
    )


@effect(
    "scaryabove",
    "Algo terrorífico acecha arriba.",
    texts=1,
    example="el examen de mañana",
    output="mp4",
)
def scaryabove(request: MemeRequest) -> MemeResult:
    caption = Image.new("RGBA", (498, 99))
    draw_fitted_text(caption, request.text, (10, 2, 488, 97), "arimobold.ttf", 32, max_lines=3)
    return _overlay_video(
        "scaryabove/scaryabove", caption, filters="[0:v][ov]overlay=0:0:shortest=1[out]"
    )


@effect("kowalski", "Kowalski, análisis.", texts=1, example="por qué no he dormido", output="gif")
def kowalski(request: MemeRequest) -> MemeResult:
    caption = render_caption(request.text, "verdana.ttf", 36, "black", width=245, stroke_width=1)
    caption = caption.rotate(10, resample=Image.Resampling.BILINEAR, expand=True)
    with Image.open(asset_path("kowalski/kowalski")) as source:
        duration = source.info.get("duration", 100)
    out = []
    for frame in frames("kowalski/kowalski"):
        paste(frame, caption, (340, 65))
        out.append(frame)
    return encode_gif(out, duration=duration)
