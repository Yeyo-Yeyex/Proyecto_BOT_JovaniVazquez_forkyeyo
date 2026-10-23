"""Buzón entre el bot y `actualizar.sh` para pedir un reinicio desde Discord.

El bot corre dentro de Docker y no puede (ni debe) reiniciarse a sí mismo: eso
exigiría darle el socket de Docker, que equivale a ser root en el NAS. En su
lugar deja una nota en una carpeta compartida con el NAS y es el propio NAS
quien actualiza y reinicia:

1. `reinicio` escribe `solicitud.json` (canal y autor).
2. Cada minuto, `actualizar.sh --solicitud` mira si hay nota. Si la hay, la
   renombra a `en_curso.json`, descarga `main`, reconstruye y reinicia.
3. Al terminar, el script escribe `resultado.txt`: `ok` o `error` en la
   primera línea y un resumen en la segunda.
4. El bot (el mismo o el recién arrancado) ve el resultado, lo publica en el
   canal de la petición y borra la nota y el resultado.

Antes de reiniciar, el NAS espera a que el bot cierre lo que hay a medias:

- Al decidir que despliega (por `reinicio` o de noche), deja `aviso.json`: el bot
  avisa de que se va a reiniciar a quien use un comando, pero todo sigue abierto
  mientras se construye la imagen.
- Con la imagen lista, deja `preparar.json` y espera `listo.json` (como mucho
  `ESPERA_PARTIDAS` segundos, 15 minutos de serie). El bot deja de abrir partidas,
  espera a que acaben las que hay y contesta `listo.json`.
- Solo espera si el bot dejó `sabe_esperar` al arrancar y el contenedor está en
  marcha: un bot de antes de esto no contestaría nunca.

Las tres notas llevan la hora (epoch) en que se dejaron y el script las borra al
acabar. Si se quedan ahí (el script se cortó), el bot las descarta cuando caducan
para no quedarse sin abrir partidas para siempre.

Aparte, cada vez que despliega commits nuevos (de noche o por `reinicio`), el
script deja `novedades.txt`: una línea por PR fusionado o commit subido a
`main` desde el despliegue anterior. El bot lo recoge y lo borra
(`take_news`); qué lleva cada línea y cómo se completa con GitHub está en
`bot.services.changelog`.

La carpeta es `.despliegue/buzon`, montada en el contenedor por
`docker-compose.yml`. Este módulo no depende de discord.py.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: Carpeta del buzón, relativa al directorio de trabajo (/app en Docker).
DEFAULT_MAILBOX = Path(".despliegue/buzon")

REQUEST_FILE = "solicitud.json"
RUNNING_FILE = "en_curso.json"
RESULT_FILE = "resultado.txt"
NEWS_FILE = "novedades.txt"
NOTICE_FILE = "aviso.json"
PREPARE_FILE = "preparar.json"
READY_FILE = "listo.json"
CAN_WAIT_FILE = "sabe_esperar"

#: Una petición sin resultado tras este tiempo se da por perdida (el script se
#: cortó o el cron no está puesto) y deja pedir otra.
STALE_AFTER = timedelta(minutes=30)
#: Un aviso de despliegue dura lo que tarda la imagen en construirse; más que esto,
#: el script se cortó.
NOTICE_STALE_AFTER = timedelta(hours=2)
#: El NAS espera como mucho 15 minutos (`ESPERA_PARTIDAS`); con margen.
PREPARE_STALE_AFTER = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class DeployRequest:
    """Quién pidió el reinicio, desde qué canal y cuándo."""

    channel_id: int
    user_id: int
    requested_at: datetime

    def to_json(self) -> str:
        return json.dumps(
            {
                "channel_id": self.channel_id,
                "user_id": self.user_id,
                "requested_at": self.requested_at.isoformat(),
            }
        )

    @classmethod
    def from_json(cls, text: str) -> DeployRequest:
        data = json.loads(text)
        return cls(
            channel_id=int(data["channel_id"]),
            user_id=int(data["user_id"]),
            requested_at=datetime.fromisoformat(data["requested_at"]),
        )


@dataclass(frozen=True, slots=True)
class DeployResult:
    """Lo que escribió `actualizar.sh` al acabar."""

    ok: bool
    summary: str
    request: DeployRequest | None


class Mailbox:
    """Lee y escribe las notas del buzón. Toda la E/S de archivos está aquí."""

    def __init__(self, path: Path = DEFAULT_MAILBOX) -> None:
        self.path = path

    def pending(self, now: datetime) -> DeployRequest | None:
        """La petición en cola o en curso, si la hay y no está caducada.

        Las caducadas se borran: si el script se cortó a medias, no deben
        bloquear el comando para siempre.
        """
        for name in (RUNNING_FILE, REQUEST_FILE):
            file = self.path / name
            request = _read_request(file)
            if request is None:
                continue
            if now - request.requested_at > STALE_AFTER:
                logger.warning("Petición de reinicio caducada; la descarto: %s", request)
                file.unlink(missing_ok=True)
                continue
            return request
        return None

    def request(self, request: DeployRequest) -> None:
        """Deja la petición para el NAS.

        Raises:
            OSError: Si no se puede escribir (carpeta sin montar o sin permisos).
        """
        self.path.mkdir(parents=True, exist_ok=True)
        # Se escribe aparte y se renombra para que el script nunca lea una
        # nota a medio escribir.
        tmp = self.path / f"{REQUEST_FILE}.tmp"
        tmp.write_text(request.to_json(), encoding="utf-8")
        tmp.replace(self.path / REQUEST_FILE)

    def take_result(self) -> DeployResult | None:
        """Recoge el resultado del script, si ya lo hay, y vacía el buzón."""
        result_file = self.path / RESULT_FILE
        try:
            lines = result_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None
        except OSError:
            logger.exception("No se pudo leer el resultado del reinicio")
            return None
        request = _read_request(self.path / RUNNING_FILE)
        for name in (RESULT_FILE, RUNNING_FILE):
            try:
                (self.path / name).unlink(missing_ok=True)
            except OSError:
                logger.exception("No se pudo borrar %s del buzón", name)
        status = lines[0].strip() if lines else ""
        summary = " ".join(line.strip() for line in lines[1:] if line.strip())
        return DeployResult(ok=status == "ok", summary=summary, request=request)

    def take_news(self) -> list[str] | None:
        """Recoge la lista de PR desplegados, si la hay, y la borra del buzón.

        Returns:
            Una línea del archivo por elemento, sin las vacías; `None` si no hay
            novedades pendientes (o el archivo no se pudo leer o borrar: mejor
            no publicarlas que publicarlas en bucle cada 15 s).
        """
        news_file = self.path / NEWS_FILE
        try:
            text = news_file.read_text(encoding="utf-8")
            news_file.unlink()
        except FileNotFoundError:
            return None
        except OSError:
            logger.exception("No se pudieron recoger las novedades del buzón")
            return None
        items = [line.strip() for line in text.splitlines() if line.strip()]
        return items or None

    def notice(self, now: datetime) -> datetime | None:
        """Cuándo avisó el NAS de que va a desplegar, si hay aviso y no está caducado."""
        return self._stamp(NOTICE_FILE, now, NOTICE_STALE_AFTER)

    def preparing(self, now: datetime) -> datetime | None:
        """Cuándo pidió el NAS que se cierren las partidas, si lo pidió y no caducó."""
        return self._stamp(PREPARE_FILE, now, PREPARE_STALE_AFTER)

    def ready(self, now: datetime) -> None:
        """Contesta al NAS que ya no queda nada a medias: puede reiniciar.

        Raises:
            OSError: Si no se puede escribir.
        """
        self._write(READY_FILE, str(int(now.timestamp())))

    def is_ready(self) -> bool:
        """Si el bot ya contestó `listo` a la petición de ahora."""
        return (self.path / READY_FILE).exists()

    def mark_can_wait(self) -> None:
        """Dice al NAS que este bot sabe cerrar sus partidas antes de un reinicio."""
        try:
            self._write(CAN_WAIT_FILE, "1")
        except OSError:
            # Sin buzón montado (fuera de Docker) no hay NAS que lo lea.
            logger.debug("No se pudo dejar %s en el buzón", CAN_WAIT_FILE, exc_info=True)

    def _write(self, name: str, text: str) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        tmp = self.path / f"{name}.tmp"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path / name)

    def _stamp(self, name: str, now: datetime, stale: timedelta) -> datetime | None:
        """La hora que lleva la nota `name`; borra la caducada y no hace caso a la rota."""
        file = self.path / name
        try:
            text = file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            logger.warning("No se pudo leer %s del buzón", name)
            return None
        try:
            stamp = datetime.fromtimestamp(int(text), tz=now.tzinfo)
        except (ValueError, OverflowError, OSError):
            logger.warning("Nota %s ilegible: %r", name, text[:40])
            return None
        if now - stamp > stale:
            logger.warning("Nota %s caducada; la descarto", name)
            file.unlink(missing_ok=True)
            return None
        return stamp


def _read_request(file: Path) -> DeployRequest | None:
    """Lee una nota; `None` si no existe o está estropeada."""
    try:
        return DeployRequest.from_json(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("Nota de reinicio ilegible: %s", file)
        return None
