"""Almacén en disco de los sonidos de entrada de cada miembro.

Por cada servidor y miembro se guardan, en `<base>/<guild_id>/`:

- `<user_id>.source.ogg`: la fuente normalizada, para regenerar el clip al
  cambiar el volumen sin pedir otra vez el archivo original.
- `<user_id>.ogg`: el clip final, con el volumen aplicado, que se reproduce.
- `<user_id>.json`: el volumen elegido.

No hace falta base de datos: son tres archivos pequeños por persona y el
borrado del sonido es borrar esos archivos. No se guarda el adjunto
original ni su nombre. Toda la E/S de disco sale del event loop con
`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EntranceSoundPaths:
    """Rutas de los archivos de un miembro en un servidor."""

    directory: Path
    source: Path
    clip: Path
    settings: Path


class EntranceSoundStore:
    """Lee, escribe y borra los sonidos de entrada guardados.

    Args:
        base_dir: Carpeta persistente (en Docker, dentro del volumen `.data`).
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def paths(self, guild_id: int, user_id: int) -> EntranceSoundPaths:
        """Rutas de los archivos del miembro.

        Los IDs son enteros de Discord, así que no pueden contener `..` ni
        separadores: la ruta no se construye con texto del usuario.
        """
        directory = self.base_dir / str(int(guild_id))
        stem = str(int(user_id))
        return EntranceSoundPaths(
            directory=directory,
            source=directory / f"{stem}.source.ogg",
            clip=directory / f"{stem}.ogg",
            settings=directory / f"{stem}.json",
        )

    def make_work_dir(self) -> tempfile.TemporaryDirectory[str]:
        """Carpeta temporal en el mismo disco, para mover archivos de forma atómica."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=self.base_dir, prefix=".tmp-")

    async def has_sound(self, guild_id: int, user_id: int) -> bool:
        """Indica si el miembro tiene un clip listo para reproducirse."""
        return await asyncio.to_thread(self.paths(guild_id, user_id).clip.is_file)

    async def read_clip(self, guild_id: int, user_id: int) -> bytes | None:
        """Devuelve el clip final, o `None` si el miembro no tiene sonido."""
        clip = self.paths(guild_id, user_id).clip

        def _read() -> bytes | None:
            try:
                return clip.read_bytes()
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_read)

    async def read_volume(self, guild_id: int, user_id: int, default: int) -> int:
        """Volumen guardado del miembro, o `default` si no hay o no es legible."""
        settings = self.paths(guild_id, user_id).settings

        def _read() -> int:
            try:
                value = json.loads(settings.read_text(encoding="utf-8"))["volume_percent"]
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                return default
            return value if isinstance(value, int) else default

        return await asyncio.to_thread(_read)

    async def save(
        self,
        guild_id: int,
        user_id: int,
        *,
        clip: Path,
        volume_percent: int,
        source: Path | None = None,
    ) -> None:
        """Guarda el clip (y la fuente, si cambia) sustituyendo los anteriores.

        `os.replace` es atómico dentro del mismo sistema de archivos: si el
        bot se reinicia a mitad, queda el sonido viejo o el nuevo, nunca uno
        a medio escribir.
        """
        paths = self.paths(guild_id, user_id)

        def _save() -> None:
            paths.directory.mkdir(parents=True, exist_ok=True)
            if source is not None:
                os.replace(source, paths.source)
            os.replace(clip, paths.clip)
            tmp_settings = paths.settings.with_suffix(".json.tmp")
            tmp_settings.write_text(
                json.dumps({"volume_percent": volume_percent}), encoding="utf-8"
            )
            os.replace(tmp_settings, paths.settings)

        await asyncio.to_thread(_save)

    async def delete(self, guild_id: int, user_id: int) -> bool:
        """Borra todos los archivos del miembro. Devuelve si había algo que borrar."""
        paths = self.paths(guild_id, user_id)

        def _delete() -> bool:
            removed = False
            for path in (paths.clip, paths.source, paths.settings):
                try:
                    path.unlink()
                    removed = True
                except FileNotFoundError:
                    pass
            return removed

        return await asyncio.to_thread(_delete)
