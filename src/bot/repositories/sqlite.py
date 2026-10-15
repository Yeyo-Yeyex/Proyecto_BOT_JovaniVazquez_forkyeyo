"""Conexión SQLite común a todos los repositorios, ajustada para responder rápido.

Todos los repositorios comparten un único archivo y abren una conexión por
operación. Con la configuración de serie de SQLite (diario `DELETE` y
`synchronous=FULL`) cada escritura fuerza varias sincronizaciones del disco
(`fsync`), y en el NAS cada una puede tardar decenas o cientos de milisegundos.
Como cada mensaje del chat ya escribe (XP, logros), un botón que tenía que
escribir esperaba su turno detrás de esas sincronizaciones y Discord daba la
interacción por fallida a los 3 segundos.

Lo que se cambia y por qué:

- `journal_mode=WAL`: las escrituras se añaden a un registro aparte y las
  lecturas no esperan a las escrituras. El modo se guarda en el archivo, así
  que basta con pedirlo; repetirlo en cada conexión no cuesta nada.
- `synchronous=NORMAL`: en modo WAL, un commit ya no sincroniza el disco; solo
  lo hace el punto de control (checkpoint) que vuelca el registro en la base.
  Si se va la luz se pueden perder las últimas transacciones, pero la base no
  se corrompe (documentación de SQLite, «WAL mode»). Un apagado normal no
  pierde nada.
- Sin checkpoint al cerrar: como cada operación abre y cierra su conexión, la
  última en cerrarse volcaría el registro y sincronizaría el disco en cada
  operación (`SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE`).
- **Checkpoint cada minuto (`checkpoint`).** Se creía que, sin el de cerrar, SQLite
  volcaría el registro solo al llegar a unas 1.000 páginas, pero con conexiones que
  se abren y se cierran en cada operación no lo recicla nunca: crecía sin fin
  (36 MB en 3.000 escrituras) y abrir cada conexión tardaba más cuanto más crecía,
  porque tiene que leerlo entero. Era lo que hacía que la tragaperras fuera cada vez
  más lenta (de 29 a 60 ms por tirada en 600 tiradas) y, con ella, todo el bot.
  `BotClient` lo vuelca y lo vacía (`TRUNCATE`) cada `CHECKPOINT_SECONDS`: un solo
  `fsync` por minuto en vez de uno por operación. El modo `PASSIVE` no vale: vuelca
  las páginas, pero el registro sigue creciendo.

Con el registro aparte, la base vive en tres archivos (`.sqlite3`, `-wal` y
`-shm`): una copia de seguridad hecha a mano copia los tres a la vez, con el bot
parado (ver README).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Segundos que una conexión espera a que otra suelte el bloqueo de escritura
#: antes de fallar con «database is locked».
BUSY_TIMEOUT_SECONDS = 30
#: Cada cuánto `BotClient` vuelca y vacía el registro WAL (`checkpoint`).
CHECKPOINT_SECONDS = 60
#: Lo que espera el checkpoint a que acaben las lecturas y escrituras en curso. Mientras
#: espera, las escrituras nuevas esperan detrás: mejor corto, y si no, al minuto siguiente.
CHECKPOINT_BUSY_SECONDS = 2


def connect(database_path: Path, *, isolation_level: str | None = "DEFERRED") -> sqlite3.Connection:
    """Abre una conexión a `database_path` con filas por nombre y modo WAL.

    Crea la carpeta si no existe.

    Args:
        database_path: Archivo SQLite del bot.
        isolation_level: El de `sqlite3.connect`. `None` deja la conexión en
            autocommit para quien abre sus transacciones a mano (`BEGIN
            IMMEDIATE`), como el repositorio de la economía.
    """
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        database_path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=isolation_level
    )
    connection.row_factory = sqlite3.Row
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def checkpoint(database_path: Path) -> tuple[int, int, int]:
    """Vuelca el registro WAL en la base y lo deja vacío (`PRAGMA wal_checkpoint(TRUNCATE)`).

    Es síncrono: se llama en un hilo (`asyncio.to_thread`). Si hay lecturas o
    escrituras en curso espera `CHECKPOINT_BUSY_SECONDS` como mucho; si no le da
    tiempo, no vacía nada y lo deja para la vez siguiente.

    Returns:
        Lo que devuelve SQLite: `(ocupado, páginas del registro, páginas volcadas)`;
        `ocupado` es 1 si no pudo terminar.
    """
    if not database_path.exists():
        return (0, 0, 0)
    connection = sqlite3.connect(database_path, timeout=CHECKPOINT_BUSY_SECONDS)
    try:
        row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        connection.close()
    return int(row[0]), int(row[1]), int(row[2])
