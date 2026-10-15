"""Casos de uso de `pisos`: comprar, vender, cambiar la renta, desahuciar y cerrar los días.

Junta las reglas (`bot.services.housing`), la persistencia
(`bot.repositories.housing`) y el dinero (`EconomyService.housing`). No sabe nada de
Discord: lo usa `bot.cogs.pisos`.
"""

from __future__ import annotations

import random
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from bot.repositories.housing import (
    DayRun,
    HousingRepository,
    Market,
    MemberDay,
    News,
    Purchase,
    Sale,
)
from bot.services.economy import EconomyService
from bot.services.housing import Lease
from bot.services.levels import local_day

#: Días cerrados que se recuperan como mucho si el bot ha estado apagado.
CATCH_UP_DAYS = 7


@dataclass(frozen=True, slots=True)
class Trade:
    """Una compra o venta hecha, con el saldo con que queda quien la hace."""

    deal: Purchase | Sale
    balance: int


class HousingService:
    """`pisos` para un bot.

    Args:
        repository: Persistencia de `pisos`.
        economy: El único camino del dinero.
        clock: Segundos epoch; inyectable en pruebas.
        rng: Azar de los inquilinos y los imprevistos; inyectable en pruebas.
    """

    def __init__(
        self,
        repository: HousingRepository,
        economy: EconomyService,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.repository = repository
        self.economy = economy
        self._clock = clock
        # `secrets` usa el azar del sistema: nadie puede adivinar quién dejará de pagar.
        self.rng = rng or secrets.SystemRandom()

    def today(self) -> date:
        """Hoy en hora canaria."""
        return local_day(self._clock())

    async def market(self, guild_id: int) -> Market:
        """Todo el mercado del servidor."""
        return await self.repository.market(guild_id)

    async def news(self, guild_id: int, listings: tuple[str, ...], limit: int = 8) -> list[News]:
        """Lo último que ha pasado en unos inmuebles."""
        return await self.repository.news(guild_id, listings, limit)

    async def week_income(self, guild_id: int, user_id: int) -> MemberDay:
        """Lo que un miembro ha cobrado y pagado por sus inmuebles en los últimos 7 días."""
        return await self.repository.income(guild_id, user_id, self.today() - timedelta(days=7))

    async def buy(
        self, guild_id: int, user_id: int, key: str, shares: int, *, expected: int | None = None
    ) -> Trade:
        """Compra participaciones (ver `EconomyService.housing` para los impuestos).

        Args:
            expected: El total que enseñó la caja; si ha cambiado, no se compra.

        Raises:
            HousingError: Si no se puede (quedan menos libres, número no válido).
            PriceChanged: Si el total ya no es `expected`.
            InsufficientFundsError: Si no le llega. No se mueve nada.
        """
        now = self._clock()
        run = self.repository.buy(
            guild_id, user_id, key, shares, local_day(now), now, expected=expected
        )
        purchase, balances = await self.economy.housing(guild_id, run)
        return Trade(purchase, balances[user_id])

    async def sell(
        self, guild_id: int, user_id: int, key: str, shares: int, *, expected: int | None = None
    ) -> Trade:
        """Vende participaciones al mercado.

        Args:
            expected: Lo que la caja dijo que cobraría; si ha cambiado, no se vende.

        Raises:
            HousingError: Si no tiene tantas.
            PriceChanged: Si lo que cobraría ya no es `expected`.
        """
        now = self._clock()
        run = self.repository.sell(
            guild_id, user_id, key, shares, local_day(now), now, expected=expected
        )
        sale, balances = await self.economy.housing(guild_id, run)
        return Trade(sale, balances[user_id])

    async def set_level(self, guild_id: int, user_id: int, key: str, level: str) -> Lease:
        """Cambia la renta que pide un inmueble (para el próximo contrato)."""
        return await self.repository.set_level(guild_id, user_id, key, level)

    async def evict(self, guild_id: int, user_id: int, key: str) -> Lease:
        """Pide el desahucio de quien no paga o de los okupas."""
        now = self._clock()
        return await self.repository.evict(guild_id, user_id, key, local_day(now), now, self.rng)

    async def close_days(self, guild_id: int) -> list[DayRun]:
        """Cierra los días que falten hasta ayer (como mucho `CATCH_UP_DAYS`).

        La primera vez en un servidor solo se cierra ayer. Cada día va en su
        propia transacción; si uno falla, los anteriores quedan cerrados.
        """
        now = self._clock()
        today = local_day(now)
        last = await self.repository.last_day(guild_id)
        first = today - timedelta(days=1) if last is None else last + timedelta(days=1)
        first = max(first, today - timedelta(days=CATCH_UP_DAYS))
        runs = []
        day = first
        while day < today:
            run, _balances = await self.economy.housing(
                guild_id, self.repository.close_day(guild_id, day, now, self.rng)
            )
            if not run.done_before:
                runs.append(run)
            day += timedelta(days=1)
        return runs
