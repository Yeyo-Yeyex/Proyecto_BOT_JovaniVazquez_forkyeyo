"""Extracción de metadatos de audio mediante `yt-dlp`.

Aislado de `bot.services.music` a propósito: este módulo hace I/O de red y
lanza subprocesos (a través de `yt-dlp`), por lo que nunca debe importarse
desde código que se ejecute directamente en el event loop. El cog de música
debe llamar a `extract_track_info` con `asyncio.to_thread`.
"""

from __future__ import annotations

from typing import Any

import yt_dlp

# Opciones de extracción: solo se resuelve una pista de audio, sin
# descargar el vídeo. `default_search` permite pasar texto libre además de
# URLs directas, igual que el propio `yt-dlp` en la línea de comandos.
_YDL_OPTIONS: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch1",
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": False,
    "nocheckcertificate": True,
    "source_address": "0.0.0.0",
    # Forzar el cliente "android" evita el 403 que YouTube devuelve a
    # menudo cuando se reproduce el enlace directo con el cliente "web"
    # por defecto fuera de un navegador real.
    "extractor_args": {"youtube": {"player_client": ["android"]}},
}


def extract_track_info(query: str) -> dict[str, Any]:
    """Resuelve una consulta o URL a los metadatos de una única pista.

    Es una operación de red y CPU bloqueante (invoca `yt-dlp`, que a su vez
    puede lanzar procesos externos); debe ejecutarse fuera del event loop,
    por ejemplo con `await asyncio.to_thread(extract_track_info, query)`.

    Args:
        query: Texto de búsqueda o URL soportada por `yt-dlp`.

    Returns:
        El diccionario de metadatos del primer resultado encontrado, listo
        para pasarse a `bot.services.music.build_track_from_info`.

    Raises:
        yt_dlp.utils.DownloadError: Si la fuente no se puede resolver, no
            existe o no es compatible.
    """
    with yt_dlp.YoutubeDL(_YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)

    if info is None:
        raise yt_dlp.utils.DownloadError("No se encontró ningún resultado para la consulta.")

    # Las búsquedas devuelven una lista de resultados en "entries"; los
    # enlaces directos devuelven ya la información de la pista.
    entries = info.get("entries")
    if entries:
        first_entry = next((entry for entry in entries if entry is not None), None)
        if first_entry is None:
            raise yt_dlp.utils.DownloadError("No se encontró ningún resultado para la consulta.")
        return first_entry
    return info
