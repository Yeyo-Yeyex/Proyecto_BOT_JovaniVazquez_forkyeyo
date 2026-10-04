"""Reglas de la economía del bot: la moneda, los yapdollars, y cómo se mueven.

Este es el único punto de entrada al dinero para el resto del bot. El casino,
el IMV (la recompensa diaria) y cualquier sistema futuro (tienda, trabajos, premios
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
from datetime import date, datetime, timedelta

import discord

from bot.repositories.economy import (
    STATE_ACCOUNT_ID,
    BalanceLimitError,
    BetSettlement,
    DailyClaim,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
    Treasury,
)
from bot.services.levels import TIMEZONE, local_day
from bot.services.taxes import (
    MAX_PENDING_DECLARATIONS,
    PROJECTION_WINDOW_SECONDS,
    TAX_COLLECTOR,
    compute_withholding,
    format_rate,
    gambling_day_tax,
    weekly_refund,
)

__all__ = [
    "BalanceLimitError",
    "BetSettlement",
    "CURRENCY_EMOJI",
    "CURRENCY_NAME",
    "CURRENCY_SYMBOL",
    "DailyResult",
    "Declaration",
    "RentaClaim",
    "EconomyService",
    "IncomeResult",
    "InsufficientFundsError",
    "STARTING_BALANCE",
    "STATE_ACCOUNT_ID",
    "Treasury",
    "format_amount",
    "week_label",
    "week_start",
    "gambling_tax_line",
    "is_all_in",
    "parse_amount",
    "tax_line",
    "treasury_embed",
]

CURRENCY_NAME = "yapdollars"
CURRENCY_SYMBOL = "Y$"
CURRENCY_EMOJI = "🪙"

#: Saldo con el que empieza cualquier miembro la primera vez que usa la economía.
STARTING_BALANCE = 1_000

#: IMV (recompensa diaria): base más un extra por cada día seguido, con tope.
#: Exento de IRPF, como el real (art. 7.y LIRPF).
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


def tax_line(gross: int, tax: int, rate: float) -> str:
    """Línea pequeña (subtexto de Discord) con lo que se queda Hacienda.

    La usan los premios por nivel y cualquier otro cobro con retención.
    """
    if tax <= 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} no te retiene nada: con tu renta de los últimos "
            "30 días no llegas al mínimo. Disfrútalo mientras dure."
        )
    return (
        f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(tax)} de IRPF "
        f"({format_rate(rate)} de {format_amount(gross)})."
    )


def gambling_tax_line(settlement: BetSettlement) -> str | None:
    """Subtexto con lo que retiene o devuelve Hacienda en una jugada, si algo cambia."""
    if settlement.tax_delta > 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(settlement.tax_delta)} de IRPF. "
            f"Hoy vas {format_amount(settlement.day_net)} arriba y llevas "
            f"{format_amount(settlement.day_withheld)} retenidos."
        )
    if settlement.tax_delta < 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} te devuelve {format_amount(-settlement.tax_delta)}: "
            "tus pérdidas de hoy compensan lo que habías ganado."
        )
    return None


def treasury_embed(treasury: Treasury, *, year: int, names: dict[int, str]) -> discord.Embed:
    """Tarjeta pública de la cuenta del Estado.

    Args:
        names: Nombre a mostrar de cada `user_id` de `top_contributors`.
    """
    embed = discord.Embed(
        title="🏛️ Hacienda",
        description=(
            f"Saldo de la cuenta del Estado: **{format_amount(treasury.balance)}**\n"
            f"Recaudado en {year}: **{format_amount(treasury.collected_since)}**\n"
            f"Recaudado desde siempre: {format_amount(treasury.collected_total)}"
        ),
        color=discord.Color.from_rgb(170, 21, 27),
    )
    if treasury.top_contributors:
        medals = ("🥇", "🥈", "🥉")
        lines = [
            f"{medals[i] if i < len(medals) else f'{i + 1}.'} {names[user_id]} · "
            f"{format_amount(paid)}"
            for i, (user_id, paid) in enumerate(treasury.top_contributors)
        ]
        embed.add_field(name="Quién más ha pagado", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Dinero en manos de {TAX_COLLECTOR}. Ya veremos qué hace con él.")
    return embed


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
class IncomeResult:
    """Un ingreso cobrado con su retención de IRPF.

    Attributes:
        gross: Cantidad bruta.
        tax: Retención que se queda Hacienda.
        rate: Tipo de retención aplicado (0–1).
        balance: Saldo tras cobrar.
    """

    gross: int
    tax: int
    rate: float
    balance: int

    @property
    def net(self) -> int:
        """Lo que llega al bolsillo."""
        return self.gross - self.tax


@dataclass(frozen=True, slots=True)
class Declaration:
    """Declaración semanal pendiente: la semana (lunes) y lo que sale a devolver."""

    week_start: date
    refund: int


@dataclass(frozen=True, slots=True)
class RentaClaim:
    """Resultado de presentar la renta."""

    declarations: tuple[Declaration, ...]
    refunded: int
    balance: int


def week_start(day: date) -> date:
    """Lunes de la semana de `day`."""
    return day - timedelta(days=day.weekday())


def week_label(start: date) -> str:
    """`2026-09-28` → `28/09–04/10`."""
    end = start + timedelta(days=6)
    return f"{start:%d/%m}–{end:%d/%m}"


def _week_end(week_iso: str) -> float:
    start = datetime.combine(date.fromisoformat(week_iso), datetime.min.time(), TIMEZONE)
    return (start + timedelta(days=7)).timestamp()


@dataclass(frozen=True, slots=True)
class DailyResult:
    """Resultado de intentar cobrar el IMV (la recompensa diaria).

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
    """Cantidad del IMV para una racha (1 = primer día)."""
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

    async def _gamble(
        self,
        guild_id: int,
        user_id: int,
        entries: list[LedgerEntry],
        *,
        adjust_tax: bool,
    ) -> BetSettlement:
        now = self._clock()
        return await self.repository.settle_gamble(
            guild_id,
            user_id,
            entries,
            day=local_day(now).isoformat(),
            now=now,
            adjust_tax=adjust_tax,
            day_tax=gambling_day_tax,
            window_seconds=PROJECTION_WINDOW_SECONDS,
        )

    async def settle_bet(
        self, guild_id: int, user_id: int, *, game: str, stake: int, payout: int
    ) -> BetSettlement:
        """Cobra una apuesta, paga su premio y ajusta el IRPF del día, todo junto.

        Args:
            game: Nombre corto del juego, usado como motivo en el libro.
            stake: Cantidad apostada; debe ser positiva.
            payout: Cantidad total devuelta al jugador (apuesta incluida);
                0 si pierde.

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
        return await self._gamble(guild_id, user_id, entries, adjust_tax=True)

    async def place_bet(
        self, guild_id: int, user_id: int, *, game: str, stake: int
    ) -> BetSettlement:
        """Cobra una apuesta de un juego que se resuelve más tarde (p. ej. blackjack).

        A diferencia de `settle_bet`, el premio se paga después con
        `pay_winnings`, cuando el juego termina, y es entonces cuando se
        ajusta el IRPF. Así el dinero en juego sale del saldo desde el primer
        momento y no se puede gastar dos veces.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        return await self._gamble(
            guild_id, user_id, [LedgerEntry(-stake, f"{game}:apuesta")], adjust_tax=False
        )

    async def pay_winnings(
        self, guild_id: int, user_id: int, *, game: str, amount: int
    ) -> BetSettlement:
        """Paga lo devuelto por un juego cobrado con `place_bet` y ajusta el IRPF del día.

        Raises:
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        if amount < 0:
            raise ValueError("El premio no puede ser negativo.")
        entries = [LedgerEntry(amount, f"{game}:premio")] if amount else []
        return await self._gamble(guild_id, user_id, entries, adjust_tax=True)

    async def grant(self, guild_id: int, user_id: int, *, amount: int, reason: str) -> int:
        """Da dinero que no es renta (p. ej. un regalo de cumpleaños): sin IRPF.

        Args:
            amount: Cantidad positiva.
            reason: Motivo corto y estable para el libro, p. ej. `"cumple:regalo"`.

        Returns:
            El saldo final.
        """
        if amount <= 0:
            raise ValueError("La cantidad debe ser positiva.")
        return await self.repository.apply(guild_id, user_id, [LedgerEntry(amount, reason)])

    @staticmethod
    def _withhold(gross: int, recent_income: int) -> int:
        return compute_withholding(gross, recent_income).tax

    async def pay_income(
        self, guild_id: int, user_id: int, *, gross: int, concept: str
    ) -> IncomeResult:
        """Paga un ingreso sujeto a IRPF (p. ej. el premio por subir de nivel).

        Args:
            gross: Cantidad bruta; debe ser positiva.
            concept: Motivo corto y estable para el libro, p. ej. `"nivel:12"`.
        """
        if gross <= 0:
            raise ValueError("El ingreso debe ser positivo.")
        tax, balance = await self.repository.credit_income(
            guild_id,
            user_id,
            gross=gross,
            concept=concept,
            now=self._clock(),
            withhold=self._withhold,
            window_seconds=PROJECTION_WINDOW_SECONDS,
        )
        return IncomeResult(gross=gross, tax=tax, rate=tax / gross, balance=balance)

    def _renta_rules(self) -> dict[str, object]:
        return {
            "current_week": week_start(local_day(self._clock())).isoformat(),
            "week_end": _week_end,
            "refund_for": weekly_refund,
            "window_seconds": PROJECTION_WINDOW_SECONDS,
            "keep": MAX_PENDING_DECLARATIONS,
        }

    async def pending_declarations(self, guild_id: int, user_id: int) -> list[Declaration]:
        """Declaraciones de semanas cerradas que salen a devolver y no se han presentado."""
        rows = await self.repository.pending_declarations(guild_id, user_id, **self._renta_rules())
        return [Declaration(date.fromisoformat(week), refund) for week, refund in rows]

    async def claim_declarations(self, guild_id: int, user_id: int) -> RentaClaim:
        """Presenta las declaraciones pendientes y cobra la devolución."""
        rows, total, balance = await self.repository.claim_declarations(
            guild_id, user_id, now=self._clock(), **self._renta_rules()
        )
        return RentaClaim(
            declarations=tuple(Declaration(date.fromisoformat(w), r) for w, r in rows),
            refunded=total,
            balance=balance,
        )

    async def treasury(self, guild_id: int, *, since: float, top: int = 5) -> Treasury:
        """Cuenta del Estado: saldo, recaudación total y desde `since`, y quién más paga."""
        return await self.repository.treasury(guild_id, since, top)

    async def claim_daily(self, guild_id: int, user_id: int) -> DailyResult:
        """Cobra el IMV si ya toca; si no, informa de cuándo."""
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
