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
    HousingError,
    HousingRepository,
    Market,
    MemberDay,
    News,
    Purchase,
    Sale,
    VpoDeal,
)
from bot.services import housing as rules
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

    async def income(self, guild_id: int, user_id: int) -> int:
        """Lo que el banco cree que gana alguien al día (nóminas y el 70 % de las rentas)."""
        work = await self.economy.work_net_days(guild_id, user_id, rules.INCOME_DAYS)
        rent = await self.repository.income(
            guild_id, user_id, self.today() - timedelta(days=rules.INCOME_DAYS)
        )
        return rules.daily_income(work, rent.rent)

    async def work_income(self, guild_id: int, user_id: int) -> int:
        """Neto medio de nómina al día de los últimos 30 días (lo que miran VPO y bono)."""
        work = await self.economy.work_net_days(guild_id, user_id, rules.INCOME_DAYS)
        return work // rules.INCOME_DAYS

    async def buy(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        shares: int,
        *,
        expected: int | None = None,
        financed: bool = False,
    ) -> Trade:
        """Compra participaciones, al contado o con hipoteca (ver `EconomyService.housing`).

        Args:
            expected: Lo que sale del bolsillo según la caja; si sube, no se compra.
            financed: Con hipoteca del 80 % (el banco mira `income`).

        Raises:
            HousingError: Si no se puede (quedan menos libres, el banco no presta…).
            PriceChanged: Si lo que sale del bolsillo ya no es `expected`.
            InsufficientFundsError: Si no le llega. No se mueve nada.
        """
        now = self._clock()
        income = await self.income(guild_id, user_id) if financed else 0
        run = self.repository.buy(
            guild_id, user_id, key, shares, local_day(now), now, expected=expected,
            financed=financed, income=income,
        )  # fmt: skip
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

    async def amortize(self, guild_id: int, user_id: int, mortgage_id: int) -> tuple[int, int]:
        """Cancela una hipoteca de golpe. Devuelve `(lo pagado, saldo)`.

        Raises:
            HousingError: Si la hipoteca ya no existe.
            InsufficientFundsError: Si no le llega.
        """
        now = self._clock()
        run = self.repository.amortize(guild_id, user_id, mortgage_id, local_day(now), now)
        paid, balances = await self.economy.housing(guild_id, run)
        return paid, balances[user_id]

    async def vpo_signup(self, guild_id: int, user_id: int, *, join: bool = True) -> None:
        """Se apunta (o se borra) del sorteo de VPO de los lunes.

        Raises:
            HousingError: Si gana demasiado (más de 3,5 veces el IPREM) o ya tiene casa.
        """
        if join and await self.work_income(guild_id, user_id) > rules.vpo_income_cap():
            raise HousingError(
                "Ganas demasiado para una VPO: el límite son 3,5 veces el IPREM. "
                "Felicidades, eres clase media: búscate la vida en el mercado."
            )
        await self.repository.vpo_signup(guild_id, user_id, self._clock(), join)

    async def vpo_buy(self, guild_id: int, user_id: int, unit: str) -> tuple[VpoDeal, int]:
        """Compra la VPO que le ha tocado. Devuelve `(la compra, saldo)`.

        Raises:
            HousingError: Si no le toca, gana demasiado o el banco no presta.
            InsufficientFundsError: Si no le llega para la notaría.
        """
        if await self.work_income(guild_id, user_id) > rules.vpo_income_cap():
            raise HousingError("Desde que te apuntaste ganas demasiado: la VPO ya no es para ti.")
        now = self._clock()
        income = await self.income(guild_id, user_id)
        run = self.repository.vpo_buy(guild_id, user_id, unit, local_day(now), now, income=income)
        deal, balances = await self.economy.housing(guild_id, run)
        return deal, balances[user_id]

    async def vpo_return(self, guild_id: int, user_id: int) -> tuple[VpoDeal, int]:
        """Devuelve su VPO al Estado a precio tasado. Devuelve `(la devolución, saldo)`.

        Raises:
            HousingError: Si no tiene VPO.
            InsufficientFundsError: Si el Estado no tiene partida para recomprarla.
        """
        now = self._clock()
        run = self.repository.vpo_return(guild_id, user_id, local_day(now), now)
        deal, balances = await self.economy.housing(guild_id, run)
        return deal, balances[user_id]

    async def set_home(self, guild_id: int, user_id: int, key: str) -> None:
        """Se muda a un piso suyo (vivienda habitual)."""
        now = self._clock()
        await self.repository.set_home(guild_id, user_id, key, local_day(now), now)

    async def leave_home(self, guild_id: int, user_id: int) -> str:
        """Deja su vivienda habitual. Devuelve el tipo que deja."""
        now = self._clock()
        return await self.repository.leave_home(guild_id, user_id, local_day(now), now)

    async def rent_to_member(self, guild_id: int, owner_id: int, key: str, member_id: int) -> Lease:
        """Alquila un piso a un miembro; si gana poco, cobra el Bono Alquiler Joven."""
        now = self._clock()
        bono = await self.work_income(guild_id, member_id) <= rules.bono_income_cap()
        return await self.repository.rent_to_member(
            guild_id, owner_id, key, member_id, local_day(now), now, bono=bono
        )

    async def buy_license(self, guild_id: int, user_id: int, key: str) -> tuple[int, int]:
        """Pide la licencia vacacional. Devuelve `(tasa, saldo)`."""
        now = self._clock()
        run = self.repository.buy_license(guild_id, user_id, key, local_day(now), now)
        fee, balances = await self.economy.housing(guild_id, run)
        return fee, balances[user_id]

    async def found_socimi(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """Monta su SOCIMI. Devuelve `(lo pagado, saldo)`."""
        now = self._clock()
        run = self.repository.found_socimi(guild_id, user_id, local_day(now), now)
        fee, balances = await self.economy.housing(guild_id, run)
        return fee, balances[user_id]

    async def accept_offer(self, guild_id: int, user_id: int, key: str) -> Trade:
        """Vende entero al fondo buitre al precio de su oferta."""
        market = await self.market(guild_id)
        held = market.holdings.get(key, {}).get(user_id)
        if held is None:
            raise HousingError("No tienes nada en este inmueble.")
        now = self._clock()
        run = self.repository.sell(
            guild_id, user_id, key, held.shares, local_day(now), now, vulture=True
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
