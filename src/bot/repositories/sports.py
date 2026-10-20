"""Persistencia de las apuestas deportivas: partidos, cuotas y resultados.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `sports_events`: cada partido que ha dado The Odds API, con sus cuotas en
  centésimas (0 si un signo no se ofrece) y, al acabar, el marcador. Los partidos son
  de todos los servidores: las cuotas no dependen de quién apueste.
- `sports_feed`: por competición, cuándo se pidieron las cuotas y los resultados por
  última vez y los créditos de la API que quedaban. Así un reinicio no gasta consultas.

El dinero no está aquí: las apuestas viven en la economía (`economy_sports_bets`), que
es la que manda. Las reglas, en `bot.services.sports`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.sports import Match, Pick, Score

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FeedState:
    """Lo último que se sabe de la API para una competición.

    Attributes:
        odds_at: Cuándo se pidieron las cuotas (epoch), o `None`.
        scores_at: Cuándo se pidieron los resultados (epoch), o `None`.
        remaining: Créditos que quedaban en la última respuesta, o `None`.
    """

    odds_at: float | None = None
    scores_at: float | None = None
    remaining: int | None = None


class SportsRepository:
    """Acceso SQLite a los partidos de las apuestas deportivas.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Conexión con transacción que se confirma al salir y se cierra siempre."""
        connection = sqlite.connect(self.database_path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                CREATE TABLE IF NOT EXISTS sports_events (
                    event_id TEXT PRIMARY KEY,
                    sport TEXT NOT NULL,
                    home TEXT NOT NULL,
                    away TEXT NOT NULL,
                    commence REAL NOT NULL,
                    odds_home INTEGER NOT NULL DEFAULT 0,
                    odds_draw INTEGER NOT NULL DEFAULT 0,
                    odds_away INTEGER NOT NULL DEFAULT 0,
                    odds_at REAL NOT NULL,
                    home_score INTEGER,
                    away_score INTEGER
                );

                CREATE INDEX IF NOT EXISTS sports_events_sport
                    ON sports_events (sport, commence);

                CREATE TABLE IF NOT EXISTS sports_feed (
                    sport TEXT PRIMARY KEY,
                    odds_at REAL,
                    scores_at REAL,
                    remaining INTEGER
                );
                """
            )

    async def save_odds(
        self, sport: str, matches: Sequence[Match], *, now: float, remaining: int | None
    ) -> None:
        """Guarda las cuotas recién pedidas de una competición.

        Los partidos que aún no han empezado y ya no vienen (aplazados o retirados) se
        quedan sin cuotas: no se puede apostar a lo que la API ya no da.
        """
        await self._run(self._save_odds_sync, sport, tuple(matches), now, remaining)

    def _save_odds_sync(
        self, sport: str, matches: tuple[Match, ...], now: float, remaining: int | None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sports_events SET odds_home = 0, odds_draw = 0, odds_away = 0
                WHERE sport = ? AND commence > ?
                """,
                (sport, now),
            )
            connection.executemany(
                """
                INSERT INTO sports_events
                    (event_id, sport, home, away, commence,
                     odds_home, odds_draw, odds_away, odds_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    home = excluded.home, away = excluded.away,
                    commence = excluded.commence, odds_home = excluded.odds_home,
                    odds_draw = excluded.odds_draw, odds_away = excluded.odds_away,
                    odds_at = excluded.odds_at
                """,
                [
                    (
                        m.event_id,
                        sport,
                        m.home,
                        m.away,
                        m.commence,
                        m.odds.get(Pick.HOME, 0),
                        m.odds.get(Pick.DRAW, 0),
                        m.odds.get(Pick.AWAY, 0),
                        now,
                    )
                    for m in matches
                ],
            )
            self._feed_in(connection, sport, "odds_at", now, remaining)

    async def save_scores(
        self, sport: str, scores: Mapping[str, Score], *, now: float, remaining: int | None
    ) -> None:
        """Apunta los marcadores finales y cuándo se preguntaron."""
        await self._run(self._save_scores_sync, sport, dict(scores), now, remaining)

    def _save_scores_sync(
        self, sport: str, scores: dict[str, Score], now: float, remaining: int | None
    ) -> None:
        with self._connect() as connection:
            connection.executemany(
                "UPDATE sports_events SET home_score = ?, away_score = ? WHERE event_id = ?",
                [(score.home, score.away, event_id) for event_id, score in scores.items()],
            )
            self._feed_in(connection, sport, "scores_at", now, remaining)

    @staticmethod
    def _feed_in(
        connection: sqlite3.Connection,
        sport: str,
        column: str,
        now: float,
        remaining: int | None,
    ) -> None:
        # `column` es `odds_at` o `scores_at`, nunca entrada del usuario.
        connection.execute(
            f"""
            INSERT INTO sports_feed (sport, {column}, remaining) VALUES (?, ?, ?)
            ON CONFLICT(sport) DO UPDATE SET
                {column} = excluded.{column},
                remaining = COALESCE(excluded.remaining, sports_feed.remaining)
            """,
            (sport, now, remaining),
        )

    async def mark_checked(self, sport: str, column: str, *, now: float) -> None:
        """Apunta una consulta fallida como hecha, para no reintentarla en cada vuelta.

        Args:
            column: `odds_at` o `scores_at`.
        """
        if column not in ("odds_at", "scores_at"):
            raise ValueError(column)
        await self._run(self._mark_checked_sync, sport, column, now)

    def _mark_checked_sync(self, sport: str, column: str, now: float) -> None:
        with self._connect() as connection:
            self._feed_in(connection, sport, column, now, None)

    async def feed(self, sport: str) -> FeedState:
        """Cuándo se preguntó por última vez y cuántos créditos quedaban."""
        return await self._run(self._feed_sync, sport)

    def _feed_sync(self, sport: str) -> FeedState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT odds_at, scores_at, remaining FROM sports_feed WHERE sport = ?",
                (sport,),
            ).fetchone()
        if row is None:
            return FeedState()
        return FeedState(row["odds_at"], row["scores_at"], row["remaining"])

    async def upcoming(self, sport: str, *, now: float) -> list[Match]:
        """Los partidos de una competición que aún no han empezado y tienen cuotas."""
        return await self._run(self._upcoming_sync, sport, now)

    def _upcoming_sync(self, sport: str, now: float) -> list[Match]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sports_events
                WHERE sport = ? AND commence > ?
                    AND (odds_home > 0 OR odds_draw > 0 OR odds_away > 0)
                ORDER BY commence, home
                """,
                (sport, now),
            ).fetchall()
        return [_match(row) for row in rows]

    async def scores(self, event_ids: Sequence[str]) -> dict[str, Score]:
        """Los marcadores que ya se saben de estos partidos."""
        return await self._run(self._scores_sync, tuple(event_ids))

    def _scores_sync(self, event_ids: tuple[str, ...]) -> dict[str, Score]:
        if not event_ids:
            return {}
        marks = ",".join("?" * len(event_ids))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT event_id, home_score, away_score FROM sports_events
                WHERE event_id IN ({marks}) AND home_score IS NOT NULL
                """,
                event_ids,
            ).fetchall()
        return {row["event_id"]: Score(row["home_score"], row["away_score"]) for row in rows}

    async def prune(self, *, before: float) -> None:
        """Borra los partidos que empezaron antes de `before` (ya no hacen falta)."""
        await self._run(self._prune_sync, before)

    def _prune_sync(self, before: float) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sports_events WHERE commence < ?", (before,))


def _match(row: sqlite3.Row) -> Match:
    return Match(
        event_id=row["event_id"],
        sport=row["sport"],
        home=row["home"],
        away=row["away"],
        commence=row["commence"],
        odds={
            Pick.HOME: row["odds_home"],
            Pick.DRAW: row["odds_draw"],
            Pick.AWAY: row["odds_away"],
        },
    )
