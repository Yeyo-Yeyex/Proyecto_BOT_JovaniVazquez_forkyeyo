"""Almacén en disco de los audios de la beernight.

Cada servidor sube sus propios audios para cada momento de la noche
(`SoundSlot`): alguien bebe, salta un evento, hay un chivatazo, empieza o
acaba la noche. Se guardan ya convertidos a Ogg/Opus (ver
`bot.services.entrance_sound.build_source_clip`), en:

    <base>/<guild_id>/<momento>/<user_id>-<milisegundos>.ogg

El nombre del archivo dice quién lo subió y cuándo, así que no hace falta
base de datos. No se guarda el adjunto original ni su nombre. Toda la E/S de
disco sale del event loop con `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bot.services.beernight import MAX_SOUNDS_PER_SLOT, BeernightError, SoundSlot

_NAME = re.compile(r"^(\d+)-(\d+)\.ogg$")


@dataclass(frozen=True, slots=True)
class StoredSound:
    """Un audio guardado.

    Attributes:
        name: Nombre del archivo; identifica el audio para borrarlo.
        uploader_id: Quien lo subió.
        uploaded_at: Cuándo, en segundos Unix.
    """

    name: str
    uploader_id: int
    uploaded_at: float


class BeernightSoundStore:
    """Lee, guarda y borra los audios de la beernight.

    Args:
        base_dir: Carpeta persistente (en Docker, dentro del volumen `.data`).
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def _slot_dir(self, guild_id: int, slot: SoundSlot) -> Path:
        # Los IDs son enteros y el momento sale de un enum: la ruta no se
        # construye con texto del usuario.
        return self.base_dir / str(int(guild_id)) / SoundSlot(slot).value

    def make_work_dir(self) -> tempfile.TemporaryDirectory[str]:
        """Carpeta temporal en el mismo disco, para mover archivos de forma atómica."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=self.base_dir, prefix=".tmp-")

    def _list_sync(self, guild_id: int, slot: SoundSlot) -> list[StoredSound]:
        directory = self._slot_dir(guild_id, slot)
        try:
            names = sorted(os.listdir(directory))
        except FileNotFoundError:
            return []
        sounds = []
        for name in names:
            if match := _NAME.match(name):
                sounds.append(StoredSound(name, int(match[1]), int(match[2]) / 1000))
        return sounds

    async def list_sounds(self, guild_id: int, slot: SoundSlot) -> list[StoredSound]:
        """Audios de un momento, del más antiguo al más nuevo."""
        return await asyncio.to_thread(self._list_sync, guild_id, slot)

    async def counts(self, guild_id: int) -> dict[SoundSlot, int]:
        """Cuántos audios hay en cada momento."""

        def _counts() -> dict[SoundSlot, int]:
            return {slot: len(self._list_sync(guild_id, slot)) for slot in SoundSlot}

        return await asyncio.to_thread(_counts)

    async def save(
        self, guild_id: int, slot: SoundSlot, uploader_id: int, clip: Path, now: float
    ) -> StoredSound:
        """Mueve un clip ya convertido a su sitio.

        Raises:
            BeernightError: Si el momento ya tiene `MAX_SOUNDS_PER_SLOT` audios.
        """

        def _save() -> StoredSound:
            if len(self._list_sync(guild_id, slot)) >= MAX_SOUNDS_PER_SLOT:
                raise BeernightError(
                    f"Ese momento ya tiene {MAX_SOUNDS_PER_SLOT} audios. "
                    "Borra alguno en ⚙️ Ajustes antes de subir otro."
                )
            directory = self._slot_dir(guild_id, slot)
            directory.mkdir(parents=True, exist_ok=True)
            stamp = int(now * 1000)
            name = f"{int(uploader_id)}-{stamp}.ogg"
            while (directory / name).exists():
                stamp += 1
                name = f"{int(uploader_id)}-{stamp}.ogg"
            os.replace(clip, directory / name)
            return StoredSound(name, int(uploader_id), stamp / 1000)

        return await asyncio.to_thread(_save)

    async def read_random(
        self, guild_id: int, slot: SoundSlot, rng: random.Random | None = None
    ) -> bytes | None:
        """Un audio del momento elegido al azar, o `None` si no hay ninguno."""

        def _read() -> bytes | None:
            sounds = self._list_sync(guild_id, slot)
            if not sounds:
                return None
            chosen = (rng or random).choice(sounds)
            try:
                return (self._slot_dir(guild_id, slot) / chosen.name).read_bytes()
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_read)

    async def delete(self, guild_id: int, slot: SoundSlot, name: str) -> bool:
        """Borra un audio por su nombre. Devuelve si existía."""
        if not _NAME.match(name):
            return False

        def _delete() -> bool:
            try:
                (self._slot_dir(guild_id, slot) / name).unlink()
            except FileNotFoundError:
                return False
            return True

        return await asyncio.to_thread(_delete)

    async def delete_guild(self, guild_id: int) -> None:
        """Borra todos los audios de un servidor."""
        directory = self.base_dir / str(int(guild_id))
        await asyncio.to_thread(shutil.rmtree, directory, True)
