"""Impuesto sobre el Patrimonio: cada lunes, Perro Sanxe cobra a quien tenga mucho.

Una tarea cada 10 minutos mira si la semana anterior ya se cobró en cada
servidor; si no, cobra la cuota (`bot.services.taxes.wealth_tax`) a todos los
monederos que pasen del mínimo exento y lo anuncia en `#chat-general` (o en el
canal del sistema). La primera semana tras activarlo en un servidor no se
cobra, para que nadie pague por sorpresa al desplegar.

No hay comando propio: `saldo` avisa de lo que te tocaría pagar y `hacienda`
suma lo recaudado. No es una interacción, así que el aviso de la Renta no
aplica; el anuncio público hace de recordatorio. Logros: `wealth_tax_paid` y
`wealth_tax_weeks` (categoría Economía y Hacienda).

Permisos: enviar mensajes e insertar enlaces en el canal del anuncio.
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
    WealthCharge,
    format_amount,
    week_label,
    week_start,
)
from bot.services.levels import local_day
from bot.services.taxes import TAX_COLLECTOR, WEALTH_MINIMUM

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

ANNOUNCE_CHANNEL_NAME = "chat-general"
#: Cuántos contribuyentes se nombran en el anuncio.
ANNOUNCE_TOP = 5
COLOR = discord.Color.from_rgb(170, 21, 27)


def wealth_embed(
    week: date, charges: tuple[WealthCharge, ...], names: dict[int, str]
) -> discord.Embed:
    """Anuncio público del cobro semanal del Patrimonio."""
    total = sum(c.tax for c in charges)
    medals = ("🥇", "🥈", "🥉")
    lines = [
        f"{medals[i] if i < len(medals) else '▫️'} **{names[c.user_id]}** paga "
        f"{format_amount(c.tax)} (tenía {format_amount(c.balance)})"
        for i, c in enumerate(charges[:ANNOUNCE_TOP])
    ]
    if len(charges) > ANNOUNCE_TOP:
        lines.append(f"…y {len(charges) - ANNOUNCE_TOP} más.")
    who = "rico paga" if len(charges) == 1 else "ricos pagan"
    embed = discord.Embed(
        title=f"🐶 ¡Wepa! {TAX_COLLECTOR} pasó por el Patrimonio",
        description=(
            f"Semana {week_label(week)}: {len(charges)} {who} "
            f"**{format_amount(total)}** en total.\n\n" + "\n".join(lines)
        ),
        color=COLOR,
    )
    embed.set_footer(
        text=(
            f"Paga cada lunes lo que tengas parado por encima de "
            f"{format_amount(WEALTH_MINIMUM)}. Gástalo, mi amor, que el dinero quieto "
            f"se lo come {TAX_COLLECTOR}."
        )
    )
    return embed


class Patrimonio(commands.Cog):
    """Cobra el Impuesto sobre el Patrimonio cada semana y lo anuncia."""

    def __init__(self, bot: commands.Bot, economy: EconomyService) -> None:
        self.bot = bot
        self.economy = economy
        #: `(servidor, lunes)` ya revisados, para no tocar la base de datos cada
        #: 10 minutos durante toda la semana.
        self._done: set[tuple[int, date]] = set()

    async def cog_load(self) -> None:
        self._weekly.start()

    async def cog_unload(self) -> None:
        self._weekly.cancel()

    @tasks.loop(minutes=10)
    async def _weekly(self) -> None:
        for guild in self.bot.guilds:
            await self.run_guild(guild)

    @_weekly.before_loop
    async def _before_weekly(self) -> None:
        await self.bot.wait_until_ready()

    async def run_guild(self, guild: discord.Guild) -> None:
        """Cobra la semana cerrada en `guild` si falta, y la anuncia. Nunca lanza."""
        last_week = week_start(local_day(time.time())) - timedelta(days=7)
        if (guild.id, last_week) in self._done:
            return
        try:
            week, run = await self.economy.charge_wealth_tax(guild.id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo cobrar el Patrimonio en el servidor %s", guild.id)
            return
        self._done.add((guild.id, week))
        if not run.charges:
            return
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        names = {}
        for charge in run.charges:
            member = guild.get_member(charge.user_id)
            names[charge.user_id] = (
                discord.utils.escape_markdown(member.display_name)
                if member is not None
                else f"<@{charge.user_id}>"
            )
        if channel is not None:
            try:
                await channel.send(
                    embed=wealth_embed(week, run.charges, names),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("No se pudo anunciar el Patrimonio en %s", guild.id, exc_info=True)
        for charge in run.charges:
            logros.note(
                self.bot,
                guild.id,
                charge.user_id,
                StatDelta(add={"wealth_tax_paid": charge.tax, "wealth_tax_weeks": 1}),
                channel.id if channel is not None else None,
            )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Patrimonio(bot, bot.economy))
