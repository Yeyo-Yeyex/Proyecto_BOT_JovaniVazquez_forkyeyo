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
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TypeVar

import discord

from bot.repositories.economy import (
    HK_ACCOUNT_ID,
    SHOP_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    BalanceLimitError,
    BetSettlement,
    BizumReceipt,
    DailyClaim,
    DonationReceipt,
    EconomyRepository,
    InsufficientFundsError,
    JackpotRecord,
    LedgerEntry,
    LotteryPayout,
    SlotsSettlement,
    Treasury,
    WealthCharge,
    WealthRun,
)
from bot.services.bizum import MIN_AMOUNT as BIZUM_MIN_AMOUNT
from bot.services.levels import TIMEZONE, local_day
from bot.services.taxes import (
    IGIC_GENERAL_RATE,
    LOTTERY_EXEMPT,
    MAX_PENDING_DECLARATIONS,
    PROJECTION_WINDOW_SECONDS,
    TAX_COLLECTOR,
    WAGE_YAPDOLLARS_PER_EURO,
    WEALTH_MINIMUM,
    ForeignPayslip,
    Payslip,
    compute_beckham_payslip,
    compute_hk_payslip,
    compute_payslip,
    compute_self_employed_payslip,
    compute_withholding,
    donation_deduction,
    format_rate,
    gambling_day_tax,
    wage_to_fiscal,
    wealth_tax,
    weekly_refund,
)

__all__ = [
    "BalanceLimitError",
    "BetSettlement",
    "BizumReceipt",
    "CURRENCY_EMOJI",
    "CURRENCY_NAME",
    "CURRENCY_SYMBOL",
    "DailyResult",
    "Declaration",
    "DonationReceipt",
    "WealthCharge",
    "WealthRun",
    "RentaClaim",
    "EconomyService",
    "SalaryResult",
    "imv_after_work",
    "IncomeResult",
    "InsufficientFundsError",
    "JackpotRecord",
    "LotteryPayout",
    "SlotsSettlement",
    "STARTING_BALANCE",
    "SHOP_ACCOUNT_ID",
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

T = TypeVar("T")

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

#: Compatibilidad del IMV con el trabajo (`pala`), inspirada en el incentivo al
#: empleo de los arts. 3 y 4 del RD 789/2022 (redacción del RD 240/2026): no
#: cuenta el 100 % del aumento de ingresos del trabajo hasta 6.000 € al año y
#: sí la mitad de lo que pase. En el juego se mira el neto de las nóminas de
#: los últimos 7 días: los primeros `IMV_WORK_EXEMPT` (6.000 € al año, en Y$ de
#: nómina y pasados a una semana) no cuentan, y de lo que pase se quita del IMV
#: la mitad de su valor en euros, repartida entre los 7 días. Nómina e IMV van
#: en escalas distintas (100 y 10 Y$ por euro, ver
#: `taxes.WAGE_YAPDOLLARS_PER_EURO`), así que cada Y$ de nómina de más quita
#: 0,05 Y$ de IMV. Simplificación: la ley compara con el año anterior y aquí se
#: compara con la última semana.
IMV_WORK_WINDOW_SECONDS = 7 * 86_400
IMV_WORK_EXEMPT = 6_000 * WAGE_YAPDOLLARS_PER_EURO * 7 // 365
IMV_WORK_TAPER = 0.5
#: El IMV nunca baja de esta parte de lo que tocaría por la racha (decisión del
#: proyecto: trabajar mucho lo reduce, pero no lo quita).
IMV_FLOOR_SHARE = 0.20


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
    debt = (
        f"\nDeuda pública (premios de lotería sin fondos): {format_amount(treasury.debt)}"
        if treasury.debt
        else ""
    )
    embed = discord.Embed(
        title="🏛️ Hacienda",
        description=(
            f"Saldo de la cuenta del Estado: **{format_amount(treasury.balance)}**\n"
            f"Recaudado en {year}: **{format_amount(treasury.collected_since)}**\n"
            f"Recaudado desde siempre: {format_amount(treasury.collected_total)}{debt}"
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
    embed.add_field(
        name="Qué se cobra",
        value=(
            "IRPF con retención en premios, niveles, cumpleaños y saludos · IRPF del "
            "casino por días, con renta semanal · Patrimonio cada lunes por lo que pase "
            f"de {format_amount(WEALTH_MINIMUM)} · IGIC en las compras de la `tienda` "
            f"(general del {IGIC_GENERAL_RATE:.0%}) · Los donativos a ONGs desgravan en la renta"
            " · Los `bizum` entre miembros, exentos de Donaciones"
            f" · Gravamen especial del 20% en los premios de `loteria` que pasen de "
            f"{format_amount(LOTTERY_EXEMPT)}"
            " · Nóminas de la `pala`: IRPF y Seguridad Social del trabajador y de la empresa"
            " (se apunta a quien cobra) · Multas de la Inspección"
        ).replace("%", " %"),
        inline=False,
    )
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
class SalaryResult:
    """Una nómina cobrada.

    Attributes:
        payslip: Desglose (bruto, cotizaciones, IRPF y neto).
        balance: Saldo tras cobrar.
    """

    payslip: Payslip
    balance: int


@dataclass(frozen=True, slots=True)
class ForeignSalaryResult:
    """Una nómina cobrada en Hong Kong."""

    payslip: ForeignPayslip
    balance: int


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
        full_amount: Lo que tocaba por la racha antes de descontar el trabajo.
        work_net: Neto de las nóminas de los últimos 7 días.
        suspended_until: Si está suspendido por la Inspección, hasta cuándo
            (epoch); 0 si no.
        abroad: País donde vive, si vive fuera (y entonces no cobra).
    """

    claimed: bool
    amount: int
    balance: int
    streak: int
    next_claim_at: float
    full_amount: int = 0
    work_net: int = 0
    suspended_until: float = 0.0
    abroad: str | None = None

    @property
    def reduction(self) -> int:
        """Lo que se ha quitado del IMV por trabajar."""
        return max(0, self.full_amount - self.amount)


def daily_amount(streak: int) -> int:
    """Cantidad del IMV para una racha (1 = primer día)."""
    return DAILY_BASE + min((streak - 1) * DAILY_STREAK_BONUS, DAILY_MAX_BONUS)


def imv_after_work(amount: int, weekly_net: int) -> int:
    """IMV de un día tras descontar lo cobrado trabajando (ver `IMV_WORK_EXEMPT`).

    Args:
        amount: Lo que toca por la racha (`daily_amount`).
        weekly_net: Neto de las nóminas de los últimos 7 días (Y$ de nómina).
    """
    excess = max(0, weekly_net - IMV_WORK_EXEMPT)
    reduction = -(-wage_to_fiscal(round(excess * IMV_WORK_TAPER)) // 7)  # hacia arriba
    floor = round(amount * IMV_FLOOR_SHARE)
    return max(floor, amount - reduction)


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

    async def play_slots(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str,
        stake: int,
        payout: int,
        share: int,
        jackpot: bool,
        seed: int,
    ) -> SlotsSettlement:
        """Cobra y paga una tirada de tragaperras y mueve el bote común.

        Tratamiento fiscal: juego, igual que `settle_bet`. Los premios de
        máquinas y casinos son ganancia patrimonial que va a la base general
        (art. 33.1 LIRPF) y las pérdidas solo compensan ganancias de juego
        (art. 33.5.d LIRPF): por eso entra en la retención diaria del casino y
        en la declaración semanal. El jackpot también: el gravamen especial
        del 20 % (disposición adicional 33ª LIRPF) es solo para loterías del
        Estado, ONCE y Cruz Roja, no para tragaperras.

        La parte de la apuesta que va al bote no es un impuesto ni sale del
        bolsillo del jugador aparte: es dinero de la apuesta que la casa no
        se queda. Un giro gratis (`stake=0`) no aporta nada.

        Args:
            game: Motivo corto para el libro (`"tragaperras"`).
            stake: Apuesta; 0 en los giros gratis.
            payout: Lo que devuelve la línea, apuesta incluida.
            share: Parte de la apuesta que va al bote.
            jackpot: Si se lleva el bote entero.
            seed: Lo que pone la casa en un bote nuevo o recién vaciado.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        now = self._clock()
        return await self.repository.settle_slots(
            guild_id,
            user_id,
            game=game,
            stake=stake,
            payout=payout,
            share=share,
            jackpot=jackpot,
            seed=seed,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
            window_seconds=PROJECTION_WINDOW_SECONDS,
        )

    async def slots_pot(self, guild_id: int, *, seed: int) -> int:
        """Bote común de la tragaperras del servidor (lo siembra si es nuevo)."""
        return await self.repository.slots_pot(guild_id, seed=seed)

    async def last_jackpot(self, guild_id: int) -> JackpotRecord | None:
        """Último jackpot de la tragaperras del servidor."""
        return await self.repository.last_jackpot(guild_id)

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
            "deduction_for": donation_deduction,
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

    async def charge_wealth_tax(self, guild_id: int) -> tuple[date, WealthRun]:
        """Cobra el Impuesto sobre el Patrimonio de la semana que acaba de cerrar.

        Tratamiento fiscal: Impuesto sobre el Patrimonio (Ley 19/1991), escala
        y mínimo en `bot.services.taxes.wealth_tax`. Lo recaudado va al Estado.
        Es idempotente: si la semana ya se cobró, no hace nada.

        Returns:
            `(lunes de la semana cobrada, resultado)`.
        """
        now = self._clock()
        week = week_start(local_day(now)) - timedelta(days=7)
        run = await self.repository.charge_wealth_tax(
            guild_id, week=week.isoformat(), now=now, tax_for=wealth_tax
        )
        return week, run

    async def donate(
        self, guild_id: int, user_id: int, *, ong_key: str, ong_account: int, amount: int
    ) -> DonationReceipt:
        """Dona `amount` a una ONG. Es un gasto: el dinero se queda en la ONG.

        Tratamiento fiscal: sin IGIC ni Donaciones (ver `bot.services.donations`);
        desgrava en el IRPF (art. 19.1 de la Ley 49/2002) y la deducción sale a
        devolver en la renta de la semana (`donation_deduction`), desde la
        cuenta del Estado.

        Raises:
            InsufficientFundsError: Si no le llega el saldo.
        """
        if amount <= 0:
            raise ValueError("El donativo debe ser positivo.")
        now = self._clock()
        return await self.repository.donate(
            guild_id,
            user_id,
            ong=ong_key,
            ong_account=ong_account,
            amount=amount,
            week=week_start(local_day(now)).isoformat(),
            now=now,
        )

    async def bizum(
        self, guild_id: int, sender_id: int, receiver_id: int, *, amount: int
    ) -> BizumReceipt:
        """Manda un Bizum: `amount` pasa entero de un miembro a otro.

        Tratamiento fiscal: exento del Impuesto sobre Sucesiones y Donaciones
        por decisión del proyecto, y sin IRPF para quien recibe (art. 6.4
        LIRPF: lo sujeto a Donaciones no tributa por IRPF). En la vida real
        sería una donación sujeta (art. 3.1.b de la Ley 29/1987) que Canarias
        solo bonifica al 99,9 % entre familia de los grupos I y II (art. 26
        sexies del Decreto Legislativo 1/2009); el bot trata a todo el servidor
        como grupo II y redondea el 0,1 % a cero. Detalle en
        `bot.services.bizum`. Como no hay impuesto, el Estado no recibe nada.

        Args:
            amount: Cantidad; al menos `bot.services.bizum.MIN_AMOUNT`. Los
                máximos de Bizum no se aplican aquí: se informan en el
                resultado (`sent_today`) para los logros.

        Raises:
            ValueError: Si la cantidad no llega al mínimo o el destino no es
                otro miembro. El mensaje se puede enseñar al usuario.
            InsufficientFundsError: Si no le llega. No se mueve nada.
            BalanceLimitError: Si quien recibe superaría el saldo máximo.
        """
        if amount < BIZUM_MIN_AMOUNT:
            raise ValueError(
                f"Bizum no deja mandar menos de {format_amount(BIZUM_MIN_AMOUNT)} (0,50 €)."
            )
        if sender_id == receiver_id:
            raise ValueError("Un Bizum a ti mismo no es un Bizum, es mirar el saldo.")
        now = self._clock()
        day_start = datetime.combine(local_day(now), datetime.min.time(), TIMEZONE).timestamp()
        return await self.repository.bizum(
            guild_id, sender_id, receiver_id, amount=amount, now=now, day_start=day_start
        )

    async def purchase(
        self,
        guild_id: int,
        user_id: int,
        *,
        base: int,
        tax: int,
        concept: str,
        reserve: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, int]:
        """Cobra una compra de la tienda con su IGIC.

        Tratamiento fiscal: IGIC. Una compra es una entrega de bienes o una
        prestación de servicios a título oneroso hecha en Canarias (art. 4 de
        la Ley 20/1991), así que paga IGIC al tipo del artículo (arts. 51 a 59
        de la Ley 4/2012; ver `bot.services.taxes.IGIC_RATES`) sobre la base
        ya rebajada (art. 22 de la Ley 20/1991). No hay IRPF: gastar no es
        renta. La base va a la caja de la tienda (`SHOP_ACCOUNT_ID`) y el
        IGIC al Estado, en la misma transacción.

        Args:
            base: Base imponible (precio tras la rebaja); positiva.
            tax: IGIC de esa base.
            concept: Motivo corto y estable para el libro (`"rol"`, `"xp"`…).
            reserve: Comprobaciones y apuntes de la tienda dentro de la
                transacción (ver `EconomyRepository.purchase`).

        Returns:
            `(lo que devuelva reserve, saldo final)`.

        Raises:
            InsufficientFundsError: Si no le llega. No se mueve nada.
        """
        return await self.repository.purchase(
            guild_id,
            user_id,
            base=base,
            tax=tax,
            concept=concept,
            now=self._clock(),
            reserve=reserve,
        )

    async def refund_purchase(
        self,
        guild_id: int,
        user_id: int,
        *,
        base: int,
        tax: int,
        concept: str,
        release: Callable[[sqlite3.Connection], None],
    ) -> int:
        """Devuelve una compra que no se pudo entregar (p. ej. un rol que no se pudo dar).

        Es una factura rectificativa: la caja devuelve la base y el Estado
        el IGIC, que deja de contar como recaudado.

        Returns:
            El saldo final.
        """
        return await self.repository.refund_purchase(
            guild_id,
            user_id,
            base=base,
            tax=tax,
            concept=concept,
            now=self._clock(),
            release=release,
        )

    async def state_balance(self, guild_id: int) -> int:
        """Saldo de la cuenta del Estado."""
        return await self.repository.state_balance(guild_id)

    async def lottery(
        self,
        guild_id: int,
        *,
        charges: Sequence[tuple[int, int, str]] = (),
        payouts: Sequence[LotteryPayout] = (),
        hook: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, dict[int, int]]:
        """Cobra boletos de lotería y paga premios, con el Estado de banca.

        Tratamiento fiscal:

        - **Compra:** sin IGIC. Las loterías, apuestas y juegos de la SELAE y
          la ONCE están exentos (art. 10.1.19º de la Ley 20/1991 del REF de
          Canarias). No es gasto deducible ni pérdida de juego: lo jugado a la
          lotería no compensa nada en la renta (art. 33.5.d LIRPF solo deja
          compensar pérdidas de juego con ganancias de juego, y estos premios
          ni siquiera están en la base general). El dinero va al Estado.
        - **Premio:** gravamen especial del 20 % sobre lo que pase de 40.000 €
          por décimo o apuesta (disposición adicional 33ª LIRPF), retenido
          por el pagador y definitivo. No entra en la retención diaria del
          casino ni en la renta semanal. Lo calcula `taxes.lottery_tax`.
        - **Banca:** los premios salen de la cuenta del Estado. Si no le llega,
          emite deuda pública por la diferencia (`economy_public_debt`).

        Args:
            charges: `(user_id, importe, motivo)` de cada compra.
            payouts: Premios con su gravamen ya calculado.
            hook: Apuntes de la lotería dentro de la misma transacción.

        Returns:
            `(lo que devuelva hook, saldo final de cada miembro tocado)`.

        Raises:
            InsufficientFundsError: Si a alguien no le llega. No se mueve nada.
        """
        return await self.repository.lottery(
            guild_id, charges=charges, payouts=payouts, now=self._clock(), hook=hook
        )

    async def ong_totals(self, guild_id: int) -> dict[str, tuple[int, int]]:
        """Por ONG: `(recaudado, donantes)` en el servidor."""
        return await self.repository.ong_totals(guild_id)

    async def treasury(self, guild_id: int, *, since: float, top: int = 5) -> Treasury:
        """Cuenta del Estado: saldo, recaudación total y desde `since`, y quién más paga."""
        return await self.repository.treasury(guild_id, since, top)

    async def claim_daily(self, guild_id: int, user_id: int) -> DailyResult:
        """Cobra el IMV si ya toca; si no, informa de cuándo.

        Lo cobrado trabajando la última semana lo reduce (`imv_after_work`) y
        una sanción de la Inspección de Trabajo lo suspende unos días.
        """
        now = self._clock()
        abroad = await self.repository.residence(guild_id, user_id)
        if abroad is not None:
            # Ley 19/2021: hay que residir en España (art. 10) y comunicar las
            # salidas de más de 90 días al año (art. 36.e). Quien se ha mudado
            # a trabajar fuera, no cobra.
            state = await self.repository.daily_state(guild_id, user_id)
            return DailyResult(
                claimed=False,
                amount=0,
                balance=await self.balance(guild_id, user_id),
                streak=state.streak if state else 0,
                next_claim_at=now,
                abroad=abroad,
            )
        suspended = await self.repository.imv_suspended_until(guild_id, user_id)
        if suspended > now:
            state = await self.repository.daily_state(guild_id, user_id)
            return DailyResult(
                claimed=False,
                amount=0,
                balance=await self.balance(guild_id, user_id),
                streak=state.streak if state else 0,
                next_claim_at=suspended,
                suspended_until=suspended,
            )
        work_net = await self.repository.work_net(guild_id, user_id, now - IMV_WORK_WINDOW_SECONDS)
        full: list[int] = []

        def decide(previous: DailyClaim | None) -> tuple[int, int] | None:
            if previous is None:
                streak = 1
            else:
                elapsed = now - previous.last_claimed_at
                if elapsed < DAILY_COOLDOWN_SECONDS:
                    return None
                streak = previous.streak + 1 if elapsed <= DAILY_STREAK_WINDOW_SECONDS else 1
            full.append(daily_amount(streak))
            return imv_after_work(full[-1], work_net), streak

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
                full_amount=full[-1],
                work_net=work_net,
            )

        state = await self.repository.daily_state(guild_id, user_id)
        assert state is not None  # solo se rechaza si ya hubo un cobro previo
        return DailyResult(
            claimed=False,
            amount=0,
            balance=await self.balance(guild_id, user_id),
            streak=state.streak,
            next_claim_at=state.last_claimed_at + DAILY_COOLDOWN_SECONDS,
            work_net=work_net,
        )

    # -- Trabajo (`pala`) -------------------------------------------------------------

    async def pay_salary(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        self_employed: bool = False,
        beckham: bool = False,
    ) -> SalaryResult:
        """Paga la nómina de un turno de `pala`.

        Tratamiento fiscal: rendimiento del trabajo (art. 17.1 LIRPF) con
        retención de IRPF y cotización a la Seguridad Social (ver la nómina en
        `bot.services.taxes`). Al Estado van la retención, la cotización del
        trabajador y la de la empresa. La de la empresa se crea de la nada,
        porque la empresa no existe: es una decisión del proyecto para que el
        trabajo engorde las arcas (y los botes de la lotería).

        Un autónomo cobra rendimientos de actividades económicas (art. 27
        LIRPF): sin cotización por turno (paga la cuota con
        `charge_self_employed_fee`) y con el IRPF por la escala, como un pago a
        cuenta simplificado.

        Quien vuelve de trabajar fuera puede tributar por la Ley Beckham
        (`beckham=True`, art. 93 LIRPF): IRPF al 24 % en vez de la escala.

        Args:
            gross: Bruto del turno; positivo.
            concept: Motivo corto y estable para el libro (`"pala:obra"`).
            self_employed: Si quien cobra es autónomo.
            beckham: Si tributa por la Ley Beckham.
        """
        if gross <= 0:
            raise ValueError("El sueldo debe ser positivo.")
        slip, balance = await self.repository.credit_salary(
            guild_id,
            user_id,
            gross=gross,
            concept=concept,
            now=self._clock(),
            payslip_for=(
                compute_self_employed_payslip
                if self_employed
                else compute_beckham_payslip
                if beckham
                else compute_payslip
            ),
            window_seconds=PROJECTION_WINDOW_SECONDS,
        )
        return SalaryResult(payslip=slip, balance=balance)

    async def pay_foreign_salary(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        resident: bool,
        exempt_left: int,
    ) -> ForeignSalaryResult:
        """Paga la nómina de un turno trabajado desde Hong Kong.

        Tratamiento fiscal (detalle en `bot.services.taxes.compute_hk_payslip`):
        MPF y salaries tax de Hong Kong, que van a su cuenta (`HK_ACCOUNT_ID`),
        no al Estado. Si sigue siendo residente fiscal en España, la parte que
        pasa de la exención del art. 7.p LIRPF paga IRPF con la deducción por
        doble imposición del art. 80 LIRPF; si ya no lo es, España no cobra nada.

        Args:
            resident: Si sigue siendo residente fiscal en España.
            exempt_left: Exención del art. 7.p que le queda hoy, en Y$.
        """
        if gross <= 0:
            raise ValueError("El sueldo debe ser positivo.")

        def payslip_for(amount: int, recent_hk: int) -> ForeignPayslip:
            return compute_hk_payslip(
                amount, recent_hk=recent_hk, resident=resident, exempt_left=exempt_left
            )

        slip, balance = await self.repository.credit_foreign_salary(
            guild_id,
            user_id,
            gross=gross,
            concept=concept,
            country="hk",
            account_id=HK_ACCOUNT_ID,
            now=self._clock(),
            payslip_for=payslip_for,
            window_seconds=PROJECTION_WINDOW_SECONDS,
        )
        return ForeignSalaryResult(payslip=slip, balance=balance)

    async def set_residence(self, guild_id: int, user_id: int, country: str | None) -> None:
        """Apunta que se va a vivir fuera (`country`) o que vuelve (`None`)."""
        await self.repository.set_residence(guild_id, user_id, country, self._clock())

    async def foreign_taxes(self, guild_id: int, user_id: int, country: str = "hk") -> int:
        """Lo que se ha quedado otro país de las nóminas de un miembro."""
        return await self.repository.foreign_taxes(guild_id, user_id, country)

    async def charge_self_employed_fee(
        self, guild_id: int, user_id: int, *, amount: int, concept: str
    ) -> tuple[int, int]:
        """Cobra la cuota semanal de autónomos.

        Tratamiento fiscal: cotización al RETA (sistema de cotización por
        ingresos reales del RDL 13/2022; en el juego, cuota fija). Va al Estado
        como Seguridad Social. Si no llega el saldo, se cobra lo que haya.

        Returns:
            `(cobrado, saldo_final)`.
        """
        if amount <= 0:
            raise ValueError("La cuota debe ser positiva.")
        return await self.repository.charge_contribution(
            guild_id, user_id, amount=amount, concept=concept, now=self._clock()
        )

    async def pay_undeclared(
        self, guild_id: int, user_id: int, *, amount: int, concept: str
    ) -> int:
        """Paga dinero en negro: sin retención, sin cotizar y sin que lo vea el IMV.

        Tratamiento fiscal: debería tributar como rendimiento del trabajo
        (art. 17.1 LIRPF) y cotizar (art. 147 LGSS), pero nadie lo declara: es
        el chiste. Si la Inspección lo descubre, `sanction` lo regulariza. No
        deja rastro en `economy_tax_records` ni en `economy_payroll`.

        Returns:
            El saldo final.
        """
        if amount <= 0:
            raise ValueError("La cantidad debe ser positiva.")
        return await self.repository.apply(
            guild_id, user_id, [LedgerEntry(amount, f"negro:{concept}")]
        )

    async def sanction(
        self, guild_id: int, user_id: int, *, amount: int, concept: str
    ) -> tuple[int, int]:
        """Cobra una multa o regularización al Estado (Inspección de Trabajo, UCO…).

        Tratamiento fiscal: sanción administrativa; no es un impuesto, pero va
        al Estado y `hacienda` la cuenta. Si no llega el saldo, se cobra lo que
        haya (no se generan deudas).

        Returns:
            `(cobrado, saldo_final)`.
        """
        if amount <= 0:
            raise ValueError("La multa debe ser positiva.")
        return await self.repository.sanction(
            guild_id, user_id, amount=amount, concept=concept, now=self._clock()
        )

    async def suspend_imv(self, guild_id: int, user_id: int, *, seconds: float) -> float:
        """Suspende el IMV `seconds` segundos desde ahora. Devuelve hasta cuándo."""
        until = self._clock() + seconds
        await self.repository.suspend_imv(guild_id, user_id, until)
        return until

    async def work_week_net(self, guild_id: int, user_id: int) -> int:
        """Neto de las nóminas de los últimos 7 días (lo que mira el IMV)."""
        return await self.repository.work_net(
            guild_id, user_id, self._clock() - IMV_WORK_WINDOW_SECONDS
        )

    async def week_tax_burden(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """`(impuestos, neto)` de los últimos 7 días, para «Socio de Hacienda».

        Los impuestos son la Seguridad Social de las nóminas (las dos partes),
        su IRPF y además el IGIC de las compras y el Patrimonio de la semana.
        El neto es lo cobrado en nómina.
        """
        since = self._clock() - IMV_WORK_WINDOW_SECONDS
        _gross, ss, irpf, net = await self.repository.payroll_totals(guild_id, user_id, since)
        other = await self.repository.other_taxes(guild_id, user_id, since)
        return ss + irpf + other, net

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra la economía del servidor (el bot ha salido de él)."""
        await self.repository.delete_guild_data(guild_id)
