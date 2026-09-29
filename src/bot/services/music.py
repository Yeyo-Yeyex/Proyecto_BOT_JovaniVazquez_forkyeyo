"""Reglas de reproducción de música: pistas, cola y límites del MVP.

Este módulo no depende de `discord.py` ni hace I/O de red: se limita a
transformar los metadatos ya extraídos (por ejemplo, con `yt-dlp`) en un
`Track` validado y a gestionar la cola en memoria de un servidor. Mantenerlo
así permite probar las reglas de negocio sin conectar a Discord ni a
Internet.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

# Límite de duración por pista, para evitar que una única canción (o un
# directo sin duración conocida) acapare el reproductor de un servidor.
MAX_TRACK_DURATION_SECONDS = 30 * 60

# Límite de pistas en cola por servidor, para acotar el uso de memoria.
MAX_QUEUE_SIZE = 50

# El volumen se expresa al usuario como un porcentaje entero; internamente
# se traduce a un factor 0.0-2.0 que acepta `discord.PCMVolumeTransformer`.
MIN_VOLUME_PERCENT = 1
MAX_VOLUME_PERCENT = 200
DEFAULT_VOLUME_PERCENT = 100


class TrackUnavailableError(Exception):
    """La fuente no devolvió información reproducible para la consulta."""


class TrackTooLongError(Exception):
    """La pista supera el límite de duración permitido o no tiene fin."""

    def __init__(self, duration_seconds: int | None) -> None:
        self.duration_seconds = duration_seconds
        super().__init__("La pista supera la duración máxima permitida.")


class QueueFullError(Exception):
    """La cola del servidor alcanzó su tamaño máximo."""


@dataclass(frozen=True, slots=True)
class Track:
    """Pista lista para reproducirse, ya validada y con metadatos resueltos."""

    title: str
    webpage_url: str
    stream_url: str
    duration_seconds: int | None
    requested_by: str
    # Cabeceras HTTP que `yt-dlp` asoció a `stream_url` (User-Agent, etc.).
    # YouTube rechaza con 403 las peticiones de `ffmpeg` que no las
    # reproducen exactamente, así que hay que reenviarlas al reproducir.
    http_headers: dict[str, str] | None = None


def format_duration(duration_seconds: int | None) -> str:
    """Da formato `m:ss` (u `h:mm:ss`) a una duración; usa un guion si falta.

    Args:
        duration_seconds: Duración en segundos, o `None` si se desconoce
            (por ejemplo, en directos).

    Returns:
        Cadena de duración legible en español, o `"En directo"` si falta.
    """
    if duration_seconds is None:
        return "En directo"

    hours, remainder = divmod(int(duration_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def build_track_from_info(info: dict, requested_by: str) -> Track:
    """Convierte la información cruda de extracción en un `Track` validado.

    Separar esta transformación de la llamada de red permite probar las
    reglas de negocio (duración máxima, campos ausentes) sin depender de
    `yt-dlp` ni de Internet en las pruebas.

    Args:
        info: Diccionario de metadatos, con el formato que produce
            `yt_dlp.YoutubeDL.extract_info` tras resolver un único vídeo.
        requested_by: Nombre visible de quien solicitó la pista.

    Returns:
        Un `Track` listo para encolarse.

    Raises:
        TrackUnavailableError: Si faltan la URL de audio o la página de
            origen, o si el diccionario no describe una pista reproducible.
        TrackTooLongError: Si la duración supera `MAX_TRACK_DURATION_SECONDS`
            o no se puede determinar (posible directo indefinido).
    """
    stream_url = info.get("url")
    webpage_url = info.get("webpage_url") or info.get("original_url")
    if not stream_url or not webpage_url:
        raise TrackUnavailableError("La fuente no proporcionó una URL de audio reproducible.")

    duration = info.get("duration")
    if duration is None or duration > MAX_TRACK_DURATION_SECONDS:
        raise TrackTooLongError(duration)

    title = info.get("title") or webpage_url
    http_headers = info.get("http_headers")
    return Track(
        title=str(title),
        webpage_url=str(webpage_url),
        stream_url=str(stream_url),
        duration_seconds=int(duration),
        requested_by=requested_by,
        http_headers=dict(http_headers) if http_headers else None,
    )


def volume_percent_to_factor(volume_percent: int) -> float:
    """Traduce un porcentaje visible al factor que espera `discord.py`."""
    return volume_percent / 100


def format_ffmpeg_headers(http_headers: dict[str, str] | None) -> str | None:
    """Da a las cabeceras HTTP el formato `CRLF` que espera `ffmpeg -headers`.

    YouTube (y otras fuentes) exigen que la petición a la URL de streaming
    lleve las mismas cabeceras (User-Agent, Referer, etc.) con las que
    `yt-dlp` la resolvió; sin ellas, la respuesta es un 403.

    Args:
        http_headers: Cabeceras asociadas a la pista, o `None` si no hay.

    Returns:
        Una cadena `"Clave: valor\\r\\n..."` lista para `-headers`, o
        `None` si no hay cabeceras que enviar.
    """
    if not http_headers:
        return None
    return "".join(f"{key}: {value}\r\n" for key, value in http_headers.items())


class MusicQueue:
    """Cola de reproducción en memoria, aislada por servidor.

    No es segura para hilos; el cog que la usa debe serializar el acceso
    con un `asyncio.Lock` si varias interacciones pueden llegar a la vez.
    """

    def __init__(self, max_size: int = MAX_QUEUE_SIZE) -> None:
        self._max_size = max_size
        self._tracks: deque[Track] = deque()

    def __len__(self) -> int:
        return len(self._tracks)

    def add(self, track: Track) -> None:
        """Añade una pista al final de la cola.

        Raises:
            QueueFullError: Si la cola ya alcanzó `max_size` pistas.
        """
        if len(self._tracks) >= self._max_size:
            raise QueueFullError(f"La cola ya tiene el máximo de {self._max_size} pistas.")
        self._tracks.append(track)

    def pop_next(self) -> Track | None:
        """Extrae y devuelve la primera pista, o `None` si la cola está vacía."""
        if not self._tracks:
            return None
        return self._tracks.popleft()

    def remove_at(self, position: int) -> Track:
        """Quita y devuelve la pista en la posición 1-based indicada.

        Raises:
            IndexError: Si la posición está fuera de rango.
        """
        index = position - 1
        if index < 0 or index >= len(self._tracks):
            raise IndexError(f"No hay ninguna pista en la posición {position}.")
        track = self._tracks[index]
        del self._tracks[index]
        return track

    def clear(self) -> None:
        """Vacía la cola sin afectar a la pista que se esté reproduciendo."""
        self._tracks.clear()

    def snapshot(self) -> list[Track]:
        """Copia superficial de la cola, en orden de reproducción."""
        return list(self._tracks)
