"""Persistencia de la bienvenida.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `welcome_settings`: GIF y canal de bienvenida de cada servidor. Sin fila,
  se usa el comportamiento por defecto (vídeo de Kratos en `#chat-general`).
- `welcome_joins`: cuándo entró cada miembro por última vez y cuántas veces.
  Sirve para saber si alguien vuelve y para el plazo del botón 👋.
- `welcome_greetings`: quién ha dado la bienvenida a quién en su última
  entrada; la clave primaria impide saludar dos veces a la misma persona.

Solo se guardan IDs y marcas de tiempo. Todo se borra cuando el bot sale
del servidor. Las reglas viven en `bot.services.welcome`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class WelcomeSettings:
    """Ajustes de bienvenida de un servidor.

    Attributes:
        gif_url: Enlace del GIF; `None` usa el vídeo por defecto.
        channel_id: Canal de bienvenida; `None` usa `#chat-general`.
    """

    gif_url: str | None = None
    channel_id: int | None = None


@dataclass(frozen=True, slots=True)
class JoinRecord:
    """Última entrada de un miembro.

    Attributes:
        joined_at: Momento (epoch) de la última entrada.
        joins: Veces que ha entrado desde que el bot lo cuenta.
    """

    joined_at: float
    joins: int


class WelcomeRepository:
    """Acceso SQLite a los ajustes y entradas de la bienvenida.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

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
                CREATE TABLE IF NOT EXISTS welcome_settings (
                    guild_id INTEGER PRIMARY KEY,
                    gif_url TEXT,
                    channel_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS welcome_joins (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at REAL NOT NULL,
                    joins INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS welcome_greetings (
                    guild_id INTEGER NOT NULL,
                    newcomer_id INTEGER NOT NULL,
                    greeter_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, newcomer_id, greeter_id)
                );
                """
            )

    # -- Ajustes ---------------------------------------------------------------------

    async def settings(self, guild_id: int) -> WelcomeSettings:
        """Ajustes del servidor; los de por defecto si no hay ninguno guardado."""
        return await self._run(self._settings_sync, guild_id)

    def _settings_sync(self, guild_id: int) -> WelcomeSettings:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT gif_url, channel_id FROM welcome_settings WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        if row is None:
            return WelcomeSettings()
        return WelcomeSettings(gif_url=row["gif_url"], channel_id=row["channel_id"])

    async def save_settings(self, guild_id: int, settings: WelcomeSettings) -> None:
        """Guarda los ajustes completos del servidor (sustituye a los anteriores)."""
        await self._run(self._save_settings_sync, guild_id, settings)

    def _save_settings_sync(self, guild_id: int, settings: WelcomeSettings) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO welcome_settings (guild_id, gif_url, channel_id) VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE
                SET gif_url = excluded.gif_url, channel_id = excluded.channel_id
                """,
                (guild_id, settings.gif_url, settings.channel_id),
            )

    # -- Entradas y saludos ------------------------------------------------------------

    async def record_join(self, guild_id: int, user_id: int, *, now: float) -> JoinRecord:
        """Apunta una entrada y borra los saludos de la anterior.

        Returns:
            La entrada recién apuntada; `joins > 1` si ya había estado.
        """
        return await self._run(self._record_join_sync, guild_id, user_id, now)

    def _record_join_sync(self, guild_id: int, user_id: int, now: float) -> JoinRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO welcome_joins (guild_id, user_id, joined_at) VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE
                SET joined_at = excluded.joined_at, joins = joins + 1
                RETURNING joined_at, joins
                """,
                (guild_id, user_id, now),
            ).fetchone()
            # Quien vuelve puede recibir saludos otra vez.
            connection.execute(
                "DELETE FROM welcome_greetings WHERE guild_id = ? AND newcomer_id = ?",
                (guild_id, user_id),
            )
        return JoinRecord(joined_at=row["joined_at"], joins=row["joins"])

    async def last_join(self, guild_id: int, user_id: int) -> JoinRecord | None:
        """Última entrada apuntada del miembro, si la hay."""
        return await self._run(self._last_join_sync, guild_id, user_id)

    def _last_join_sync(self, guild_id: int, user_id: int) -> JoinRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT joined_at, joins FROM welcome_joins WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        return None if row is None else JoinRecord(row["joined_at"], row["joins"])

    async def add_greeting(self, guild_id: int, newcomer_id: int, greeter_id: int) -> int | None:
        """Apunta un saludo.

        Returns:
            Cuántos saludos lleva el recién llegado, o `None` si `greeter_id`
            ya le había saludado (no se apunta otra vez).
        """
        return await self._run(self._add_greeting_sync, guild_id, newcomer_id, greeter_id)

    def _add_greeting_sync(self, guild_id: int, newcomer_id: int, greeter_id: int) -> int | None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO welcome_greetings (guild_id, newcomer_id, greeter_id)
                VALUES (?, ?, ?) ON CONFLICT DO NOTHING
                """,
                (guild_id, newcomer_id, greeter_id),
            )
            if cursor.rowcount == 0:
                return None
            (count,) = connection.execute(
                "SELECT COUNT(*) FROM welcome_greetings WHERE guild_id = ? AND newcomer_id = ?",
                (guild_id, newcomer_id),
            ).fetchone()
        return int(count)

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo del servidor (el bot ha salido de él)."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            for table in ("welcome_settings", "welcome_joins", "welcome_greetings"):
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
