"""Descarga los informes diarios que el bot publica en su canal (`/informe canal:`).

Recorre el canal entero y deja en `docs/metricas/` cada informe que aún no esté,
con su nombre (`informe-AAAA-MM-DD.json`). Escribe las rutas nuevas; la visión
global de todos sale con `python docs/analisis_metricas.py docs/metricas/`.
Necesita dos variables, en el entorno o en el `.env` de la raíz del repo (que no
se sube):

- `INFORME_TOKEN`: token de un bot que vea ese canal (Ver canal y Leer el historial).
- `INFORME_CANAL`: ID del canal.

Uso:

    python docs/descargar_informe.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://discord.com/api/v10"
# Sin un User-Agent de bot, Cloudflare corta las peticiones de urllib.
USER_AGENT = "DiscordBot (https://github.com/godzilin/Proyecto_BOT_JovaniVazquez, 1)"


def setting(name: str) -> str:
    """La variable `name` del entorno o, si no está, del `.env` de la raíz."""
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name and value.strip():
                return value.strip()
    sys.exit(f"Falta {name} (en el entorno o en .env); ver la cabecera de este script.")


def get(url: str, token: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bot {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
        return response.read()


def main() -> None:
    token, channel = setting("INFORME_TOKEN"), setting("INFORME_CANAL")
    folder = ROOT / "docs" / "metricas"
    found = new = 0
    before = ""
    while True:
        url = f"{API}/channels/{channel}/messages?limit=100{before}"
        messages = json.loads(get(url, token))  # del más nuevo al más viejo
        for message in messages:
            for attachment in message["attachments"]:
                name = Path(attachment["filename"]).name
                if not (name.startswith("informe") and name.endswith(".json")):
                    continue
                found += 1
                path = folder / name
                if not path.exists():
                    path.write_bytes(get(attachment["url"]))
                    new += 1
                    print(path.relative_to(ROOT).as_posix())
        if len(messages) < 100:
            break
        before = f"&before={messages[-1]['id']}"
    if not found:
        sys.exit("No hay ningún informe en el canal.")
    print(f"{new} nuevos de {found} informes en el canal.")


if __name__ == "__main__":
    main()
