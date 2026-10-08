"""Persistencia de las carreras de caballos: la forma de cada caballo y el Gran Premio.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `horse_records`: por servidor y caballo, carreras, victorias, podios, los
  últimos puestos y cuándo corrió por última vez (`HorseRecord`). Es lo que
  hace que el establo tenga historia: la forma sale en la parrilla y un
  caballo que acaba de correr sale cansado.
- `horse_meta`: por servidor, carreras corridas, carreras desde el último Gran
  Premio, cuándo fue y el bote que espera al siguiente.

Los rasgos de los caballos no se guardan: son fijos y viven en
`bot.services.horses.STABLE`. El dinero tampoco: pasa por la economía. Solo
se guardan IDs de servidor y claves de caballo; todo se borra cuando el bot
sale del servidor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.horses import GRAND_PRIX_POT, HorseRecord, updated_record

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Meta:
    """Contadores de un servidor.

    Attributes:
        races: Carreras corridas en el servidor.
        since_grand_prix: Carreras normales desde el último Gran Premio.
        last_grand_prix: Cuándo fue el último Gran Premio (epoch, 0 si nunca).
        pot: Bote del próximo Gran Premio.
    """

    races: int = 0
    since_grand_prix: int = 0
    last_grand_prix: float = 0.0
    pot: int = GRAND_PRIX_POT


def _form(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split(",") if p)


class HorseRepository:
    """Acceso SQLite al historial del establo y al Gran Premio.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS horse_records (
                    guild_id INTEGER NOT NULL,
                    horse TEXT NOT NULL,
                    races INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    places INTEGER NOT NULL DEFAULT 0,
                    form TEXT NOT NULL DEFAULT '',
                    last_race REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, horse)
                );

                CREATE TABLE IF NOT EXISTS horse_meta (
                    guild_id INTEGER PRIMARY KEY,
                    races INTEGER NOT NULL DEFAULT 0,
                    since_grand_prix INTEGER NOT NULL DEFAULT 0,
                    last_grand_prix REAL NOT NULL DEFAULT 0,
                    pot INTEGER NOT NULL
                );
                """
            )

    async def records(self, guild_id: int) -> dict[str, HorseRecord]:
        """Historial de los caballos que ya han corrido en el servidor."""
        return await self._run(self._records_sync, guild_id)

    def _records_sync(self, guild_id: int) -> dict[str, HorseRecord]:
        with self._connect() as connection:
            return self._records_in(connection, guild_id)

    async def meta(self, guild_id: int) -> Meta:
        """Contadores del servidor (los de partida si nunca ha corrido nadie)."""
        return await self._run(self._meta_sync, guild_id)

    def _meta_sync(self, guild_id: int) -> Meta:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM horse_meta WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        if row is None:
            return Meta()
        return Meta(
            races=int(row["races"]),
            since_grand_prix=int(row["since_grand_prix"]),
            last_grand_prix=float(row["last_grand_prix"]),
            pot=int(row["pot"]),
        )

    async def record_race(
        self,
        guild_id: int,
        finish: Iterable[tuple[str, int]],
        *,
        now: float,
        grand_prix: bool,
        pot: int | None = None,
    ) -> Meta:
        """Apunta una carrera terminada: la forma de cada caballo y los contadores.

        Args:
            finish: Pares `(clave del caballo, puesto)`.
            now: Cuándo terminó (epoch).
            grand_prix: Si era el Gran Premio (pone a cero la cuenta hasta el siguiente).
            pot: Bote que queda para el próximo Gran Premio; `None` lo deja como
                está. Una carrera normal no lo toca: si se pasara el bote que
                leyó al abrir, pisaría el de un Gran Premio de otro canal que
                acabara mientras tanto.

        Returns:
            Los contadores del servidor tras la carrera.
        """
        return await self._run(self._record_race_sync, guild_id, list(finish), now, grand_prix, pot)

    def _record_race_sync(
        self,
        guild_id: int,
        finish: list[tuple[str, int]],
        now: float,
        grand_prix: bool,
        pot: int | None,
    ) -> Meta:
        with self._connect() as connection:
            records = self._records_in(connection, guild_id)
            for horse, position in finish:
                record = updated_record(records.get(horse, HorseRecord()), position, now)
                connection.execute(
                    """
                    INSERT INTO horse_records
                        (guild_id, horse, races, wins, places, form, last_race)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, horse) DO UPDATE SET
                        races = excluded.races, wins = excluded.wins,
                        places = excluded.places, form = excluded.form,
                        last_race = excluded.last_race
                    """,
                    (
                        guild_id,
                        horse,
                        record.races,
                        record.wins,
                        record.places,
                        ",".join(str(p) for p in record.form),
                        record.last_race,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM horse_meta WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            before = (
                Meta()
                if row is None
                else Meta(
                    races=int(row["races"]),
                    since_grand_prix=int(row["since_grand_prix"]),
                    last_grand_prix=float(row["last_grand_prix"]),
                    pot=int(row["pot"]),
                )
            )
            after = Meta(
                races=before.races + 1,
                since_grand_prix=0 if grand_prix else before.since_grand_prix + 1,
                last_grand_prix=now if grand_prix else before.last_grand_prix,
                pot=before.pot if pot is None else pot,
            )
            connection.execute(
                """
                INSERT INTO horse_meta (guild_id, races, since_grand_prix, last_grand_prix, pot)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (guild_id) DO UPDATE SET
                    races = excluded.races, since_grand_prix = excluded.since_grand_prix,
                    last_grand_prix = excluded.last_grand_prix, pot = excluded.pot
                """,
                (guild_id, after.races, after.since_grand_prix, after.last_grand_prix, after.pot),
            )
        return after

    @staticmethod
    def _records_in(connection: sqlite3.Connection, guild_id: int) -> dict[str, HorseRecord]:
        rows = connection.execute(
            "SELECT * FROM horse_records WHERE guild_id = ?", (guild_id,)
        ).fetchall()
        return {
            str(row["horse"]): HorseRecord(
                races=int(row["races"]),
                wins=int(row["wins"]),
                places=int(row["places"]),
                form=_form(str(row["form"])),
                last_race=float(row["last_race"]),
            )
            for row in rows
        }

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo guardado de un servidor (al salir el bot de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM horse_records WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM horse_meta WHERE guild_id = ?", (guild_id,))
