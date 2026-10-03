"""Reglas de la economía del bot: la moneda, los yapdollars, y cómo se mueven.

Este es el único punto de entrada al dinero para el resto del bot. El casino,
la recompensa diaria y cualquier sistema futuro (tienda, trabajos, premios
por nivel) deben llamar a `EconomyService` en vez de tocar saldos, para que
todas las reglas compartan la misma moneda, el mismo libro de movimientos y
las mismas garantías de atomicidad (ver `bot.repositories.economy`).

La moneda es ficticia: no se compra ni se canjea por nada con valor real.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from bot.repositories.economy import (
    BalanceLimitError,
    DailyClaim,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)

__all__ = [
    "BalanceLimitError",
    "CURRENCY_EMOJI",
    "CURRENCY_NAME",
    "CURRENCY_SYMBOL",
    "DailyResult",
    "EconomyService",
    "InsufficientFundsError",
    "STARTING_BALANCE",
    "format_amount",
    "is_all_in",
    "parse_amount",
]

CURRENCY_NAME = "yapdollars"
CURRENCY_SYMBOL = "Y$"
CURRENCY_EMOJI = "🪙"

#: Saldo con el que empieza cualquier miembro la primera vez que usa la economía.
STARTING_BALANCE = 1_000

#: Recompensa diaria: base más un extra por cada día seguido, con tope.
DAILY_BASE = 500
DAILY_STREAK_BONUS = 100
DAILY_MAX_BONUS = 1_000
#: Se usan horas en vez de días de calendario para no depender de zonas
#: horarias: 20 h permite cobrar cada día aunque se haga a horas distintas,
#: y la racha se pierde si pasan más de 48 h sin cobrar.
DAILY_COOLDOWN_SECONDS = 20 * 3600
DAILY_STREAK_WINDOW_SECONDS = 48 * 3600


def format_amount(amount: int) -> str:
    """Formatea una cantidad al estilo español: `1.250 Y$`."""
    return f"{amount:,}".replace(",", ".") + f" {CURRENCY_SYMBOL}"


_ALL_IN_WORDS = {"all", "allin", "all-in", "todo", "max"}
_HALF_WORDS = {"mitad", "half"}
_SUFFIXES = {"k": 1_000, "m": 1_000_000}
_SUFFIXED = re.compile(r"^(\d+(?:[.,]\d+)?)([km])$")


def is_all_in(text: str) -> bool:
    """Si el texto pide apostar todo el saldo (`all`, `todo`, `max`…)."""
    return text.strip().lower().replace(" ", "") in _ALL_IN_WORDS


def parse_amount(text: str, balance: int) -> int:
    """Convierte lo que escribe el usuario en una cantidad entera.

    Acepta `500`, `1.500` (punto como separador de miles), `2k`, `2.5k`,
    `1m`, `all`/`todo` (todo el saldo) y `mitad`.

    Raises:
        ValueError: Si el texto no es una cantidad válida o no es positiva.
            El mensaje está pensado para mostrarse al usuario.
    """
    value = text.strip().lower().replace(" ", "")
    if value in _ALL_IN_WORDS:
        amount = balance
    elif value in _HALF_WORDS:
        amount = balance // 2
    elif match := _SUFFIXED.match(value):
        number = float(match.group(1).replace(",", "."))
        amount = int(number * _SUFFIXES[match.group(2)])
    elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+|\d+", value):
        amount = int(re.sub(r"[.,]", "", value))
    else:
        raise ValueError(f"No entiendo `{text}` como cantidad. Prueba `500`, `2k` o `all`.")
    if amount <= 0:
        raise ValueError("La cantidad tiene que ser mayor que cero.")
    return amount


@dataclass(frozen=True, slots=True)
class DailyResult:
    """Resultado de intentar cobrar la recompensa diaria.

    Attributes:
        claimed: Si se ha cobrado ahora.
        amount: Cantidad cobrada (0 si no se cobró).
        balance: Saldo tras la operación.
        streak: Racha de días seguidos tras la operación.
        next_claim_at: Momento (epoch) desde el que se puede volver a cobrar.
    """

    claimed: bool
    amount: int
    balance: int
    streak: int
    next_claim_at: float


def daily_amount(streak: int) -> int:
    """Cantidad de la recompensa diaria para una racha (1 = primer día)."""
    return DAILY_BASE + min((streak - 1) * DAILY_STREAK_BONUS, DAILY_MAX_BONUS)


class EconomyService:
    """Casos de uso de la economía, independientes de Discord.

    Args:
        repository: Persistencia de monederos y movimientos.
        clock: Fuente de tiempo en segundos epoch; inyectable en pruebas.
    """

    def __init__(
        self, repository: EconomyRepository, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.repository = repository
        self._clock = clock

    async def balance(self, guild_id: int, user_id: int) -> int:
        """Saldo actual; abre el monedero con `STARTING_BALANCE` si no existía."""
        return await self.repository.balance(guild_id, user_id)

    async def settle_bet(
        self, guild_id: int, user_id: int, *, game: str, stake: int, payout: int
    ) -> int:
        """Cobra una apuesta y paga su premio en una sola operación.

        Args:
            game: Nombre corto del juego, usado como motivo en el libro.
            stake: Cantidad apostada; debe ser positiva.
            payout: Cantidad total devuelta al jugador (apuesta incluida);
                0 si pierde.

        Returns:
            El saldo final.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta. No se
                cobra ni se paga nada.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        if payout < 0:
            raise ValueError("El premio no puede ser negativo.")
        entries = [LedgerEntry(-stake, f"{game}:apuesta")]
        if payout:
            entries.append(LedgerEntry(payout, f"{game}:premio"))
        return await self.repository.apply(guild_id, user_id, entries)

    async def place_bet(self, guild_id: int, user_id: int, *, game: str, stake: int) -> int:
        """Cobra una apuesta de un juego que se resuelve más tarde (p. ej. blackjack).

        A diferencia de `settle_bet`, el premio se paga después con
        `pay_winnings`, cuando el juego termina. Así el dinero en juego sale
        del saldo desde el primer momento y no se puede gastar dos veces.

        Returns:
            El saldo tras cobrar.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        return await self.repository.apply(
            guild_id, user_id, [LedgerEntry(-stake, f"{game}:apuesta")]
        )

    async def pay_winnings(self, guild_id: int, user_id: int, *, game: str, amount: int) -> int:
        """Paga lo devuelto por un juego ya cobrado con `place_bet`.

        Returns:
            El saldo tras pagar (sin cambios si `amount` es 0).

        Raises:
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        if amount < 0:
            raise ValueError("El premio no puede ser negativo.")
        entries = [LedgerEntry(amount, f"{game}:premio")] if amount else []
        return await self.repository.apply(guild_id, user_id, entries)

    async def claim_daily(self, guild_id: int, user_id: int) -> DailyResult:
        """Cobra la recompensa diaria si ya toca; si no, informa de cuándo."""
        now = self._clock()

        def decide(previous: DailyClaim | None) -> tuple[int, int] | None:
            if previous is None:
                streak = 1
            else:
                elapsed = now - previous.last_claimed_at
                if elapsed < DAILY_COOLDOWN_SECONDS:
                    return None
                streak = previous.streak + 1 if elapsed <= DAILY_STREAK_WINDOW_SECONDS else 1
            return daily_amount(streak), streak

        claimed = await self.repository.claim_daily(guild_id, user_id, now=now, decide=decide)
        if claimed is not None:
            amount, balance = claimed
            state = await self.repository.daily_state(guild_id, user_id)
            assert state is not None  # se acaba de escribir en la misma operación
            return DailyResult(
                claimed=True,
                amount=amount,
                balance=balance,
                streak=state.streak,
                next_claim_at=now + DAILY_COOLDOWN_SECONDS,
            )

        state = await self.repository.daily_state(guild_id, user_id)
        assert state is not None  # solo se rechaza si ya hubo un cobro previo
        return DailyResult(
            claimed=False,
            amount=0,
            balance=await self.balance(guild_id, user_id),
            streak=state.streak,
            next_claim_at=state.last_claimed_at + DAILY_COOLDOWN_SECONDS,
        )

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra la economía del servidor (el bot ha salido de él)."""
        await self.repository.delete_guild_data(guild_id)
