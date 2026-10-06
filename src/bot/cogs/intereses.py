"""Cuenta remunerada: cada día el monedero paga intereses y Perro Sanxe se lleva su parte.

Una tarea cada 10 minutos mira en cada servidor si ya se pagaron los intereses
del día anterior; si no, los paga (`EconomyService.pay_interest`, reglas en
`bot.services.interest`) a todos a la vez, también a los que no hacen nada. Los
lunes, además, liquida la semana anterior con la escala del ahorro.

No hay comando propio ni anuncio público diario (sería spam). El cobro se cuenta
así:

- La próxima vez que el miembro hace algo con dinero, el resultado lleva una
  línea pequeña: «Ayer el banco te pagó X (Perro Sanxe se llevó Y)». Va por el
  mismo hueco que el aviso de la Renta (`renta.hint`), así que llega a todos los
  juegos y comandos con dinero sin tocarlos uno a uno.
- `saldo` enseña lo cobrado ayer, el saldo medio de hoy y lo que daría mañana.
- `renta` enseña la última liquidación del ahorro.

Logros: categoría Economía y Hacienda. Los de cada día se avisan en
`#chat-general` (o el canal del sistema), así quien se ha pasado una semana sin
aparecer se entera de que ha desbloqueado «Esto lo pagamos entre todos».

Permisos: enviar mensajes en el canal de los avisos de logros.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.services.achievements import StatDelta
from bot.services.economy import (
    EconomyService,
    InterestNotice,
    format_amount,
    week_label,
)
from bot.services.interest import (
    INTEREST_DAILY_MAX,
    INTEREST_TIERS,
    ROUNDING_RATE,
    SPIN_PRICE,
    DayOutcome,
    SavingsSettlement,
)
from bot.services.levels import local_day
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.cogs import find_cog

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

ANNOUNCE_CHANNEL_NAME = "chat-general"
#: Días sin mirar a partir de los cuales volver tiene logro propio.
COMEBACK_DAYS = 7


def rate_label(rate: float) -> str:
    """`0.0125` → `1,25 %`."""
    return f"{rate * 100:g}".replace(".", ",") + " %"


def tiers_label() -> str:
    """`2,5 % hasta 4.000 · 1,25 % hasta 20.000 · 0,4 % hasta 70.000`."""
    return " · ".join(
        f"{rate_label(rate)} hasta {format_amount(ceiling)}" for ceiling, rate in INTEREST_TIERS
    )


def day_stats(outcome: DayOutcome) -> StatDelta:
    """Estadísticas de logros de un día de la cuenta, haya cobrado o no."""
    payment, facts, streaks = outcome.payment, outcome.facts, outcome.streaks
    add: dict[str, int] = {}
    if payment.gross > 0:
        add.update(interest_earned=payment.net, interest_days=1, interest_tax=payment.tax)
        if payment.gross >= INTEREST_DAILY_MAX:
            add["interest_capped"] = 1
        if payment.tax > payment.gross * ROUNDING_RATE:
            add["interest_rounding"] = 1
    elif facts.active:
        # Ha estado por aquí, pero el monedero ha pasado el día a cero.
        add["interest_zero"] = 1
    if facts.payroll and facts.close < SPIN_PRICE:
        add["interest_grasshopper"] = 1
    if facts.interest_in > 0 and facts.casino_net <= -facts.interest_in:
        add["interest_gambled"] = 1
    if facts.imv > 0 and facts.interest_in > facts.imv:
        add["interest_beats_imv"] = 1
    if facts.bizum_out and any(
        ceiling - SPIN_PRICE <= facts.close < ceiling for ceiling, _rate in INTEREST_TIERS
    ):
        add["interest_bizum_trick"] = 1
    return StatDelta(
        add=add,
        peak={
            "interest_avg_max": payment.average,
            "interest_capped_streak": streaks.capped,
            "interest_floor_streak": streaks.floor,
            "interest_resist_streak": streaks.resist,
            "interest_still_streak": streaks.still,
            "interest_ant_streak": streaks.ant,
        },
    )


def savings_stats(settlement: SavingsSettlement) -> StatDelta:
    """Estadísticas de logros de una liquidación semanal del ahorro."""
    return StatDelta(
        add={"interest_tax": settlement.charged} if settlement.charged else {},
        peak={"savings_rate_max": round(settlement.rate * 100)},
    )


def notice_lines(notice: InterestNotice, today: date) -> list[str]:
    """Líneas pequeñas con lo que el miembro no ha visto de la cuenta."""
    lines = []
    if notice.days:
        net = format_amount(notice.gross - notice.tax)
        bite = f"{TAX_COLLECTOR} se llevó {format_amount(notice.tax)}"
        if notice.days == 1 and notice.first_day == (today - timedelta(days=1)).isoformat():
            lines.append(f"-# 🏦 Ayer el banco te pagó {net} de intereses ({bite}).")
        else:
            lines.append(
                f"-# 🏦 Mientras no mirabas, el banco te pagó {net} de intereses en "
                f"{notice.days} días ({bite})."
            )
    for week, charged, rate in notice.savings:
        lines.append(
            f"-# 🐶 Liquidación del ahorro ({week_label(date.fromisoformat(week))}): "
            f"{TAX_COLLECTOR} te cobra {format_amount(charged)} más"
            + (f", que llegaste al tramo del {rate} %." if rate > 19 else " por el redondeo.")
        )
    return lines


async def hint(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """Puente: líneas de intereses sin ver, o `None`. No lanza; sin el cog, `None`."""
    if (cog := find_cog(bot, Intereses)) is not None:
        return await cog.hint_for(guild_id, user_id)
    return None


async def savings_line(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """Puente: línea con la última liquidación del ahorro, para `renta`."""
    if (cog := find_cog(bot, Intereses)) is not None:
        return await cog.savings_line(guild_id, user_id)
    return None


class Intereses(commands.Cog):
    """Paga los intereses del monedero cada día y liquida el ahorro cada lunes."""

    def __init__(self, bot: commands.Bot, economy: EconomyService) -> None:
        self.bot = bot
        self.economy = economy
        #: `(servidor, día)` ya revisados, para no tocar la base de datos cada 10
        #: minutos durante todo el día.
        self._done: set[tuple[int, date]] = set()
        #: Miembros sin nada nuevo que contar desde el último pago. Se vacía en
        #: cada pago, así la mayoría de acciones no consultan la base de datos.
        self._clean: set[tuple[int, int]] = set()

    async def cog_load(self) -> None:
        self._daily.start()

    async def cog_unload(self) -> None:
        self._daily.cancel()

    @tasks.loop(minutes=10)
    async def _daily(self) -> None:
        for guild in self.bot.guilds:
            await self.run_guild(guild)

    @_daily.before_loop
    async def _before_daily(self) -> None:
        await self.bot.wait_until_ready()

    def _channel_id(self, guild: discord.Guild) -> int | None:
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        return channel.id if channel is not None else None

    async def run_guild(self, guild: discord.Guild) -> None:
        """Paga los días cerrados que falten en `guild` y liquida la semana. Nunca lanza."""
        today = local_day(time.time())
        if (guild.id, today) in self._done:
            return
        try:
            result = await self.economy.pay_interest(guild.id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron pagar los intereses en el servidor %s", guild.id)
            return
        # Solo hace falta recordar el día de hoy.
        self._done = {key for key in self._done if key[1] == today}
        self._done.add((guild.id, today))
        if result.days or result.savings:
            self._clean = {key for key in self._clean if key[0] != guild.id}
        channel_id = self._channel_id(guild)
        for _day, run in result.days:
            for outcome in run.outcomes:
                logros.note(
                    self.bot, guild.id, outcome.payment.user_id, day_stats(outcome), channel_id
                )
        if result.savings is not None:
            for settlement in result.savings[1].settlements:
                logros.note(
                    self.bot, guild.id, settlement.user_id, savings_stats(settlement), channel_id
                )

    async def hint_for(self, guild_id: int, user_id: int) -> str | None:
        """Lo que el miembro no ha visto de la cuenta, una sola vez, o `None`."""
        key = (guild_id, user_id)
        if key in self._clean:
            return None
        try:
            notice = await self.economy.take_interest_notice(guild_id, user_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron leer los intereses de %s", user_id)
            return None
        self._clean.add(key)
        today = local_day(time.time())
        if notice.first_day and (today - date.fromisoformat(notice.first_day)).days >= (
            COMEBACK_DAYS
        ):
            guild = self.bot.get_guild(guild_id)
            logros.note(
                self.bot,
                guild_id,
                user_id,
                StatDelta(add={"interest_comeback": 1}),
                self._channel_id(guild) if guild is not None else None,
            )
        lines = notice_lines(notice, today)
        return "\n".join(lines) if lines else None

    async def savings_line(self, guild_id: int, user_id: int) -> str | None:
        """Resumen de la última liquidación del ahorro, o `None` si no hay."""
        try:
            last = await self.economy.last_savings(guild_id, user_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer la liquidación del ahorro de %s", user_id)
            return None
        if last is None:
            return None
        week, gross, withheld, quota, rate, charged = last
        return (
            f"-# 🏦 Base del ahorro ({week_label(date.fromisoformat(week))}): "
            f"{format_amount(gross)} de intereses, cuota de {format_amount(quota)} "
            f"(tramo del {rate} %), {format_amount(withheld)} ya retenidos y "
            f"{format_amount(charged)} cobrados el lunes. No hay que presentarla."
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Intereses(bot, bot.economy))
