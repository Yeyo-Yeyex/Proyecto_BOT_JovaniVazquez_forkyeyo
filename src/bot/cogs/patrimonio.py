"""Impuesto sobre el Patrimonio: cada lunes, Perro Sanxe cobra a quien tenga mucho.

Una tarea cada 10 minutos mira si la semana anterior ya se cobró en cada
servidor; si no, cobra la cuota (`bot.services.taxes.wealth_tax`) a todos los
monederos que pasen del mínimo exento y lo anuncia en `#chat-general` (o en el
canal del sistema). La primera semana tras activarlo en un servidor no se
cobra, para que nadie pague por sorpresa al desplegar.

`saldo` avisa de lo que te tocaría pagar y `hacienda` suma lo recaudado. No
es una interacción, así que el aviso de la Renta no aplica; el anuncio
público hace de recordatorio. Logros: `wealth_tax_paid` y `wealth_tax_weeks`
(categoría Economía y Hacienda).

Comandos (no mueven dinero; ver `bot.services.net_worth`):

- `patrimonio [miembro]`: todos los activos de alguien (efectivo, bienes de
  la tienda, boletos de lotería pendientes y la renta por cobrar), con su
  valor, su puesto y el Patrimonio que le tocaría pagar el lunes.
- `fortunas`: la lista de todos los miembros por patrimonio, con el reparto
  de la riqueza del servidor (Gini y lo que tiene el más rico).

Logros de los dos comandos: `patrimonio_stats` (Economía y Hacienda).

Permisos: enviar mensajes e insertar enlaces en el canal del anuncio.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.services.achievements import StatDelta, patrimonio_stats
from bot.services.economy import (
    EconomyService,
    WealthCharge,
    format_amount,
    week_label,
    week_start,
)
from bot.services.levels import local_day
from bot.services.net_worth import NetWorth, build, gini, holding_line, top_share, verdict
from bot.services.taxes import TAX_COLLECTOR, WEALTH_MINIMUM, wealth_tax
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.lottery import LotteryRepository
    from bot.repositories.shop import ShopRepository

logger = logging.getLogger(__name__)

ANNOUNCE_CHANNEL_NAME = "chat-general"
#: Cuántos contribuyentes se nombran en el anuncio.
ANNOUNCE_TOP = 5
COLOR = discord.Color.from_rgb(170, 21, 27)
#: Cuántos miembros salen con nombre en `fortunas`.
FORTUNES_TOP = 15


def _decimal(value: float, digits: int) -> str:
    """`0.4321, 2` → `0,43`."""
    return f"{value:.{digits}f}".replace(".", ",")


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

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        *,
        shop: ShopRepository | None = None,
        lottery: LotteryRepository | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.shop = shop
        self.lottery = lottery
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

    # -- `patrimonio` y `fortunas` ------------------------------------------------------

    async def fortunes(self, guild_id: int) -> list[NetWorth]:
        """Patrimonio de todos los miembros, del más rico al menos."""
        now = time.time()
        balances = await self.economy.member_balances(guild_id)
        holdings = await self.shop.holdings(guild_id, None, now) if self.shop else []
        lottery = await self.lottery.open_stakes(guild_id) if self.lottery else {}
        users = set(balances) | {row[0] for row in holdings} | set(lottery)
        renta = {}
        for user_id in users:
            pending = await self.economy.pending_declarations(guild_id, user_id)
            if total := sum(d.refund for d in pending):
                renta[user_id] = total
        return build(balances, holdings, lottery, renta)

    @staticmethod
    def _name(guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member is None:
            return f"<@{user_id}>"
        return discord.utils.escape_markdown(member.display_name)

    async def _patrimonio_impl(
        self, responder: CommandResponder, member: discord.abc.User | None
    ) -> None:
        guild = responder.guild
        author = responder.member
        if guild is None or author is None:
            await responder.send_error("El patrimonio solo se mira dentro de un servidor.")
            return
        target = member or author
        everyone = await self.fortunes(guild.id)
        worth = next((w for w in everyone if w.user_id == target.id), None)
        if worth is None:
            worth = NetWorth(user_id=target.id)
        ranked = [w for w in everyone if w.total > 0]
        rank = next((i for i, w in enumerate(ranked, 1) if w.user_id == target.id), len(ranked))
        lines = [
            f"💵 Efectivo: **{format_amount(worth.cash)}**",
            f"🛍️ Bienes de la tienda: **{format_amount(worth.goods_value)}** ({len(worth.goods)})",
            f"🎟️ Boletos de lotería por sortear: **{format_amount(worth.lottery)}**",
            f"📬 Renta por cobrar: **{format_amount(worth.renta)}**",
        ]
        embed = discord.Embed(
            title=f"🏰 Patrimonio de {discord.utils.escape_markdown(target.display_name)}",
            description=(
                f"# {format_amount(worth.total)}\n"
                + (f"Puesto {rank} de {len(ranked)} en `fortunas`\n" if worth.total > 0 else "")
                + f"*{verdict(worth, rank, len(ranked))}*"
            ),
            color=COLOR,
        )
        embed.add_field(name="Activos", value="\n".join(lines), inline=False)
        if worth.goods:
            shown = [holding_line(item) for item in worth.goods[:15]]
            if len(worth.goods) > 15:
                shown.append(f"-# Y {len(worth.goods) - 15} cosas más.")
            text = "\n".join(shown)
            embed.add_field(name="Bienes", value=text[:1_024], inline=False)
        tax = wealth_tax(worth.cash)
        embed.add_field(
            name=f"🐶 {TAX_COLLECTOR}",
            value=(
                f"El lunes te cobraría {format_amount(tax)} de Patrimonio (solo mira el "
                f"efectivo que pase de {format_amount(WEALTH_MINIMUM)})."
                if tax
                else f"No llegas al mínimo del Patrimonio ({format_amount(WEALTH_MINIMUM)} en "
                "efectivo). Sanxe te perdona esta semana."
            ),
            inline=False,
        )
        embed.set_footer(
            text="Bienes al precio pagado sin IGIC; lo que caduca vale la vida que le queda."
        )
        await responder.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        if not author.bot:
            await logros.track(
                self.bot,
                guild.id,
                author,
                responder.channel,
                patrimonio_stats(
                    listing=False,
                    snooping=target.id != author.id,
                    net_worth=worth.total if target.id == author.id else 0,
                    illiquid=target.id == author.id and worth.illiquid_share >= 0.5,
                    rank=rank if target.id == author.id else 0,
                    people=len(ranked),
                ),
            )

    async def _fortunas_impl(self, responder: CommandResponder) -> None:
        guild = responder.guild
        author = responder.member
        if guild is None or author is None:
            await responder.send_error("Las fortunas solo se miran dentro de un servidor.")
            return
        everyone = [w for w in await self.fortunes(guild.id) if w.total > 0]
        totals = [w.total for w in everyone]
        medals = ("🥇", "🥈", "🥉")
        lines = [
            f"{medals[i] if i < len(medals) else f'`{i + 1}.`'} {self._name(guild, w.user_id)} · "
            f"**{format_amount(w.total)}**\n-# 💵 {format_amount(w.cash)} · 🛍️ "
            f"{format_amount(w.goods_value)}"
            + (f" · 🎟️ {format_amount(w.lottery)}" if w.lottery else "")
            + (f" · 📬 {format_amount(w.renta)}" if w.renta else "")
            for i, w in enumerate(everyone[:FORTUNES_TOP])
        ]
        if len(everyone) > FORTUNES_TOP:
            lines.append(f"-# Y {len(everyone) - FORTUNES_TOP} más. `patrimonio @miembro`.")
        total = sum(totals)
        cash = sum(w.cash for w in everyone)
        embed = discord.Embed(
            title="💎 Fortunas del servidor",
            description=(
                f"Patrimonio de todos: **{format_amount(total)}** "
                f"({format_amount(cash)} en efectivo, {format_amount(total - cash)} en cosas)\n"
                f"Gini: **{_decimal(gini(totals), 2)}** · el más rico tiene el "
                f"{_decimal(top_share(totals, 0) * 100, 0)} % · el 10 % más rico, el "
                f"{_decimal(top_share(totals, 0.1) * 100, 0)} %"
            ),
            color=COLOR,
        )
        if not lines:
            embed.add_field(name="Nadie tiene nada", value="Ni un Y$ en todo el servidor.")
        block = ""
        first = True
        for line in lines:
            if len(block) + len(line) + 1 > 1_024:
                embed.add_field(name="La lista" if first else "\u200b", value=block, inline=False)
                block, first = "", False
            block = f"{block}\n{line}" if block else line
        if block:
            embed.add_field(name="La lista" if first else "\u200b", value=block, inline=False)
        embed.set_footer(
            text="Efectivo, bienes de la tienda, boletos por sortear y renta por cobrar. "
            "Gini: 0 si todos tienen lo mismo, 1 si uno lo tiene todo."
        )
        await responder.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        if not author.bot:
            rank = next((i for i, w in enumerate(everyone, 1) if w.user_id == author.id), 0)
            await logros.track(
                self.bot,
                guild.id,
                author,
                responder.channel,
                patrimonio_stats(
                    listing=True,
                    snooping=False,
                    net_worth=0,
                    illiquid=False,
                    rank=rank,
                    people=len(everyone),
                ),
            )

    @app_commands.command(
        name="patrimonio", description="Todo lo que tienes (o lo que tiene alguien), valorado."
    )
    @app_commands.describe(miembro="De quién mirar el patrimonio (por defecto, el tuyo).")
    @app_commands.guild_only()
    async def patrimonio(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Activos de un miembro: efectivo, bienes, boletos y renta por cobrar."""
        await self._patrimonio_impl(InteractionResponder(interaction), miembro)

    @commands.command(name="patrimonio")
    @commands.guild_only()
    async def patrimonio_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.patrimonio [miembro]`)."""
        await self._patrimonio_impl(ContextResponder(ctx), miembro)

    @app_commands.command(
        name="fortunas", description="Lista de todos los miembros por patrimonio."
    )
    @app_commands.guild_only()
    async def fortunas(self, interaction: discord.Interaction) -> None:
        """La lista Forbes del servidor y cómo de repartida está la riqueza."""
        await self._fortunas_impl(InteractionResponder(interaction))

    @commands.command(name="fortunas")
    @commands.guild_only()
    async def fortunas_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.fortunas`)."""
        await self._fortunas_impl(ContextResponder(ctx))


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Patrimonio(bot, bot.economy, shop=bot.shop, lottery=bot.lottery))
