"""Donativos a las ONGs del bot: `donar`.

- `donar` sin argumentos enseña las cuatro ONGs, lo que dicen que hacen, lo
  que hacen de verdad y cuánto llevan recaudado en el servidor.
- `donar <ong> <cantidad>` dona. El dinero va a la cuenta de la ONG y no
  vuelve. A cambio desgrava: la deducción (art. 19.1 de la Ley 49/2002) rebaja
  la cuota de la declaración del lunes (sube lo que sale a devolver o baja lo que
  sale a pagar), hasta la cuota de esa semana. Reglas en `bot.services.donations`
  y `bot.services.taxes`.

El resultado es público, para presumir de generosidad. Donar gasta dinero, así
que en `/donar` lleva el aviso de la Renta y la mascota habla del donativo
(`Event.DONATE`). Logros en Economía y Hacienda con `donation_stats`: lo donado,
los donativos, cada ONG, las cuatro, y las fechas y cifras con guasa.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.services.achievements import donation_stats
from bot.services.donations import ONGS, deduction_rule_text, find_ong, pick_spending
from bot.services.economy import (
    CURRENCY_EMOJI,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import Event, Moment
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.cogs import find_cog
from bot.utils.interactions import notify
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(120, 190, 120)


def catalog_embed(totals: dict[str, tuple[int, int]]) -> discord.Embed:
    """Las ONGs, con lo que dicen, lo que hacen y lo recaudado en el servidor."""
    embed = discord.Embed(
        title="🤝 ONGs que desgravan",
        description=(
            "Dona con `donar <ong> <cantidad>` y recupera parte en la renta del lunes: "
            f"{deduction_rule_text()}. Así le pagas menos a {TAX_COLLECTOR}."
        ),
        color=COLOR,
    )
    for ong in ONGS:
        total, donors = totals.get(ong.key, (0, 0))
        embed.add_field(
            name=f"{ong.emoji} {ong.name} · `{ong.key}`",
            value=(
                f"**Dice:** {ong.mission}\n**Hace:** {ong.reality}\n"
                f"-# Recaudado aquí: {format_amount(total)} de {donors} "
                f"{'donante' if donors == 1 else 'donantes'}"
            ),
            inline=False,
        )
    return embed


class Donaciones(commands.Cog):
    """Donativos deducibles a las ONGs de broma."""

    def __init__(self, bot: commands.Bot, economy: EconomyService) -> None:
        self.bot = bot
        self.economy = economy

    async def _donar_impl(
        self,
        responder: CommandResponder,
        user: discord.abc.User,
        ong_text: str | None,
        amount_text: str | None,
    ) -> int:
        """Enseña el catálogo o dona.

        Returns:
            Lo donado, o 0 si no se ha donado (para el aviso de la Renta).
        """
        guild = responder.guild
        if guild is None:
            await responder.send_error("Las ONGs solo aceptan donativos dentro de un servidor.")
            return 0
        if not ong_text or not amount_text:
            totals = await self.economy.ong_totals(guild.id)
            await responder.send(embed=catalog_embed(totals))
            return 0
        ong = find_ong(ong_text)
        if ong is None:
            keys = ", ".join(f"`{o.key}`" for o in ONGS)
            await responder.send_error(f"No conozco esa ONG. Prueba con {keys}.")
            return 0
        balance = await self.economy.balance(guild.id, user.id)
        try:
            amount = parse_amount(amount_text, balance)
            receipt = await self.economy.donate(
                guild.id, user.id, ong_key=ong.key, ong_account=ong.account_id, amount=amount
            )
        except ValueError as error:
            await responder.send_error(str(error))
            return 0
        except InsufficientFundsError as error:
            await responder.send_error(f"No te llega: tienes {format_amount(error.balance)}.")
            return 0
        name = discord.utils.escape_markdown(user.display_name)
        lines = [
            f"{ong.emoji} **{name}** dona **{format_amount(amount)}** a **{ong.name}**.",
            f"Con tu dinero han comprado: {pick_spending(ong)}. ¡Qué generosidad!",
            f"{CURRENCY_EMOJI} Saldo: **{format_amount(receipt.balance)}**",
            f"-# 🐶 Esta semana llevas {format_amount(receipt.donated_week)} donados. "
            f"Desgrava el {deduction_rule_text()}; te vuelve en la renta del lunes.",
        ]
        hint = await renta.hint(self.bot, guild.id, user.id, Moment(Event.DONATE))
        if hint is not None:
            lines.append(hint)
        await responder.send(
            embed=discord.Embed(description="\n".join(lines), color=COLOR),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await logros.track(
            self.bot,
            guild.id,
            user,
            responder.channel,
            donation_stats(
                ong=ong.key,
                amount=amount,
                ongs_supported=receipt.ongs_supported,
                balance_after=receipt.balance,
                when=datetime.now(TIMEZONE),
            ),
        )
        return amount

    @app_commands.command(name="donar", description="Dona a una ONG y desgrava en la renta.")
    @app_commands.describe(
        ong="A quién donas (sin elegir, ves la lista).",
        cantidad="Cuánto: 500, 2k, mitad, all…",
    )
    @app_commands.choices(
        ong=[app_commands.Choice(name=f"{o.emoji} {o.name}", value=o.key) for o in ONGS]
    )
    @app_commands.guild_only()
    async def donar(
        self,
        interaction: discord.Interaction,
        ong: app_commands.Choice[str] | None = None,
        cantidad: str | None = None,
    ) -> None:
        """Dona a una ONG o, sin argumentos, enseña la lista."""
        donated = await self._donar_impl(
            InteractionResponder(interaction),
            interaction.user,
            ong.value if ong else None,
            cantidad,
        )
        if donated:
            # Donar gasta dinero: gancho de la Renta (ver Biblia.txt, sección 4).
            await renta.remind(self.bot, interaction)

    @commands.command(name="donar")
    @commands.guild_only()
    async def donar_text(
        self, ctx: commands.Context, ong: str | None = None, cantidad: str | None = None
    ) -> None:
        """Versión de texto: `.donar` para la lista, `.donar <ong> <cantidad>` para donar."""
        await self._donar_impl(ContextResponder(ctx), ctx.author, ong, cantidad)


async def give(
    bot: commands.Bot, interaction: discord.Interaction, ong_key: str, amount_text: str
) -> int:
    """Dona desde otro cog (el consejo de `renta`) igual que `/donar`: sale en el canal.

    La interacción ya tiene que estar aceptada (`ack`): el resultado va de seguimiento.

    Returns:
        Lo donado, o 0 si no se pudo (sin saldo, cantidad mal escrita, cog sin cargar).
    """
    if (cog := find_cog(bot, Donaciones)) is None:
        await notify(interaction, "Ahora mismo no se puede donar.")
        return 0
    return await cog._donar_impl(  # noqa: SLF001
        InteractionResponder(interaction), interaction.user, ong_key, amount_text
    )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Donaciones(bot, bot.economy))
