"""Impuesto sobre el Patrimonio: cada lunes, Perro Sanxe cobra a quien tenga mucho.

Una tarea cada 10 minutos mira si la semana anterior ya se cobró en cada
servidor; si no, cobra la cuota (`bot.services.taxes.wealth_quote`) a todos los
miembros cuyo patrimonio (efectivo, los inmuebles de `pisos` a su valor fiscal y
los objetos de la tienda) pase del mínimo exento, con el límite conjunto con el
IRPF de la semana (art. 31 de la Ley 19/1991), y lo anuncia en `#chat-general` (o
en el canal del sistema). Quien pasa de 37.000.000 Y$ declara también el Impuesto
de Solidaridad de las Grandes Fortunas, que en Canarias sale a cero.
La primera semana tras activarlo en un servidor no se cobra, para que nadie pague
por sorpresa al desplegar.

`saldo` avisa de lo que te tocaría pagar y `hacienda` suma lo recaudado. No
es una interacción, así que el aviso de la Renta no aplica; el anuncio
público hace de recordatorio. Logros: `wealth_tax_paid`, `wealth_tax_weeks`,
`wealth_embargo`, `wealth_joint_limit`, `wealth_goods_taxed` y
`wealth_solidarity` (categoría Economía y Hacienda).

Consultas (no mueven dinero; ver `bot.services.net_worth`):

- Sección 🏰 Patrimonio de `perfil` (`patrimonio_embed`): todos los activos
  de alguien (efectivo, pisos, bienes de la tienda, boletos de lotería pendientes
  y la renta por cobrar), con su valor, su puesto y el Patrimonio que le
  tocaría pagar el lunes (`quote`, la misma cuenta que usa `saldo` con
  `wealth_due`).
- `fortunas`: la lista de todos los miembros por patrimonio, con el reparto
  de la riqueza del servidor (Gini y lo que tiene el más rico) y el título
  que luce cada uno (`shop.title_of`).

Logros de las dos: `patrimonio_stats` (Economía y Hacienda).

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
from bot.cogs import pisos
from bot.cogs import shop as tienda
from bot.repositories.housing import assets_in as housing_assets_in
from bot.repositories.shop import wealth_goods_in
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
from bot.services.shop import Kind
from bot.services.taxes import (
    SOLIDARITY_WEALTH_THRESHOLD,
    TAX_COLLECTOR,
    WEALTH_MAX_REDUCTION,
    WEALTH_MINIMUM,
    WealthQuote,
    wealth_quote,
)
from bot.utils.cogs import find_cog
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.housing import HousingRepository
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
    total = sum(c.tax + c.debt for c in charges)
    medals = ("🥇", "🥈", "🥉")
    lines = [
        f"{medals[i] if i < len(medals) else '▫️'} **{names[c.user_id]}** paga "
        f"{format_amount(c.tax)} (tenía {format_amount(c.balance)})"
        + (
            f" y le embargan {format_amount(c.debt)} de sus alquileres: todo en ladrillo"
            if c.debt
            else ""
        )
        + (
            f". El límite con el IRPF le perdona {format_amount(c.reduced)}: rico de rentas bajas"
            if c.reduced
            else ""
        )
        for i, c in enumerate(charges[:ANNOUNCE_TOP])
    ]
    if len(charges) > ANNOUNCE_TOP:
        lines.append(f"…y {len(charges) - ANNOUNCE_TOP} más.")
    if fortunes := [c for c in charges if c.solidarity_subject]:
        paid = sum(c.solidarity for c in fortunes)
        who = "declara" if len(fortunes) == 1 else "declaran"
        lines.append(
            f"\n💎 {len(fortunes)} {who} el Impuesto de Solidaridad de las Grandes Fortunas "
            f"y paga{'n' if len(fortunes) > 1 else ''} {format_amount(paid)}: se descuenta el "
            "Patrimonio y Canarias no lo bonifica. Montero lo inventó para Madrid."
        )
    who = "rico paga" if len(charges) == 1 else "ricos pagan"
    embed = discord.Embed(
        title=f"🐶 {TAX_COLLECTOR} pasó por el Patrimonio",
        description=(
            f"Semana {week_label(week)}: {len(charges)} {who} "
            f"**{format_amount(total)}** en total.\n\n" + "\n".join(lines)
        ),
        color=COLOR,
    )
    embed.set_footer(
        text=(
            f"Paga cada lunes por lo que tengas (efectivo, pisos y objetos de la tienda) por "
            f"encima de {format_amount(WEALTH_MINIMUM)}, con el límite del 60 % de tu renta. "
            f"El ladrillo también se lo come {TAX_COLLECTOR}."
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
        housing: HousingRepository | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.shop = shop
        self.lottery = lottery
        self.housing = housing
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
        housing = self.housing
        assets = (
            (lambda connection: housing_assets_in(connection, guild.id))
            if housing is not None
            else None
        )
        goods = (
            (lambda connection: wealth_goods_in(connection, guild.id, time.time()))
            if self.shop is not None
            else None
        )
        try:
            week, run = await self.economy.charge_wealth_tax(guild.id, assets=assets, goods=goods)
        except Exception:
            # Nunca lanza: si saliera la excepción, `tasks.loop` dejaría de cobrar el
            # Patrimonio para siempre (ahora lee también los pisos).
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
                StatDelta(
                    add={
                        "wealth_tax_paid": charge.tax,
                        "wealth_tax_weeks": 1,
                        **({"wealth_embargo": 1} if charge.debt else {}),
                        **({"wealth_joint_limit": 1} if charge.reduced else {}),
                        **({"wealth_goods_taxed": 1} if charge.goods else {}),
                        **({"wealth_solidarity": 1} if charge.solidarity_subject else {}),
                    }
                ),
                channel.id if channel is not None else None,
            )

    # -- Patrimonio y `fortunas` --------------------------------------------------------

    async def fortunes(self, guild_id: int) -> list[NetWorth]:
        """Patrimonio de todos los miembros, del más rico al menos."""
        now = time.time()
        balances = await self.economy.member_balances(guild_id)
        holdings = await self.shop.holdings(guild_id, None, now) if self.shop else []
        lottery = await self.lottery.open_stakes(guild_id) if self.lottery else {}
        homes, homes_fiscal = (
            await self.housing.member_values(guild_id) if self.housing else ({}, {})
        )
        debts = await self.housing.member_debts(guild_id) if self.housing else {}
        users = set(balances) | {row[0] for row in holdings} | set(lottery) | set(homes)
        renta = {}
        for user_id in users:
            pending = await self.economy.pending_declarations(guild_id, user_id)
            if total := sum(d.refund for d in pending):
                renta[user_id] = total
        return build(balances, holdings, lottery, renta, homes, homes_fiscal, debts)

    @staticmethod
    def _name(guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member is None:
            return f"<@{user_id}>"
        return discord.utils.escape_markdown(member.display_name)

    def _titled(self, guild: discord.Guild, user_id: int) -> str:
        """Nombre para `fortunas`, con el título que luce si tiene uno."""
        name = self._name(guild, user_id)
        if (title := tienda.title_of(self.bot, guild.id, user_id)) is not None:
            return f"{name} · *{title}*"
        return name

    async def net_worth(self, guild_id: int, user_id: int) -> tuple[NetWorth, int, int]:
        """Patrimonio de un miembro, su puesto en `fortunas` y cuántos tienen algo."""
        everyone = await self.fortunes(guild_id)
        worth = next((w for w in everyone if w.user_id == user_id), None)
        if worth is None:
            worth = NetWorth(user_id=user_id)
        ranked = [w for w in everyone if w.total > 0]
        rank = next((i for i, w in enumerate(ranked, 1) if w.user_id == user_id), len(ranked))
        return worth, rank, len(ranked)

    async def quote(
        self, guild_id: int, user_id: int, *, cash: int, homes_fiscal: int, objects: int
    ) -> WealthQuote:
        """Lo que el lunes le cobraría de Patrimonio con lo que tiene ahora.

        Es la misma cuenta en `perfil` y en `saldo`: efectivo, pisos a su valor fiscal y
        objetos de la tienda, con el límite conjunto del IRPF de esta semana (art. 31) y
        las Grandes Fortunas.

        Args:
            objects: Valor de los objetos de la tienda que tributan (los trofeos).
        """
        irpf_base, irpf_quota = await self.economy.week_irpf(guild_id, user_id)
        return wealth_quote(
            cash + homes_fiscal + objects,
            unproductive=objects,
            irpf_base=irpf_base,
            irpf_quota=irpf_quota,
        )

    async def patrimonio_embed(
        self,
        guild: discord.Guild,
        author: discord.abc.User,
        target: discord.abc.User,
        channel: object,
    ) -> discord.Embed:
        """Sección 🏰 Patrimonio de `perfil`: activos de `target` y lo que paga el lunes.

        Apunta los logros de mirar el patrimonio (propio o ajeno) a `author`.
        """
        worth, rank, people = await self.net_worth(guild.id, target.id)
        lines = [
            f"💵 Efectivo: **{format_amount(worth.cash)}**",
            f"🏠 Pisos: **{format_amount(worth.homes)}**",
            f"🛍️ Bienes de la tienda: **{format_amount(worth.goods_value)}** ({len(worth.goods)})",
            f"🎟️ Boletos de lotería por sortear: **{format_amount(worth.lottery)}**",
            f"📬 Renta por cobrar: **{format_amount(worth.renta)}**",
        ]
        if worth.debts > 0:
            lines.append(
                f"🏦 Deudas: **−{format_amount(worth.debts)}** (hipotecas, préstamos y lo que "
                "debe al banco y a sus avalistas)"
            )
        elif worth.debts < 0:
            lines.append(f"🤝 Le deben sus avalados: **{format_amount(-worth.debts)}**")
        embed = discord.Embed(
            title=f"🏰 Patrimonio de {discord.utils.escape_markdown(target.display_name)}",
            description=(
                f"# {format_amount(worth.total)}\n"
                + (f"Puesto {rank} de {people} en `fortunas`\n" if worth.total > 0 else "")
                + f"*{verdict(worth, rank, people)}*"
            ),
            color=COLOR,
        )
        if await pisos.asnef(self.bot, guild.id, target.id):
            lines.append(
                "📛 En el fichero de morosos (ASNEF): ni préstamos ni hipotecas hasta "
                "pagar lo que debe al banco"
            )
        embed.add_field(
            name="Activos y deudas" if worth.debts else "Activos",
            value="\n".join(lines),
            inline=False,
        )
        if worth.goods:
            shown = [holding_line(item) for item in worth.goods[:15]]
            if len(worth.goods) > 15:
                shown.append(f"-# Y {len(worth.goods) - 15} cosas más.")
            text = "\n".join(shown)
            embed.add_field(name="Bienes", value=text[:1_024], inline=False)
        objects = sum(item.value for item in worth.goods if item.kind == Kind.TROPHY.value)
        quote = await self.quote(
            guild.id, target.id, cash=worth.cash, homes_fiscal=worth.homes_fiscal, objects=objects
        )
        debt = await self.economy.tax_debt(guild.id, target.id)
        text = (
            f"El lunes te cobraría {format_amount(quote.due)} de Patrimonio (efectivo, pisos "
            f"a su valor fiscal y objetos de la tienda, por lo que pase de "
            f"{format_amount(WEALTH_MINIMUM)}). Lo que no tengas en efectivo queda como "
            "deuda: se cobra el lunes siguiente o se embarga de tus alquileres."
            if quote.due
            else f"No llegas al mínimo del Patrimonio ({format_amount(WEALTH_MINIMUM)} "
            "entre efectivo, pisos y objetos). Sanxe te perdona esta semana."
        )
        if quote.reduced:
            text += (
                f"\nCon tu IRPF de esta semana, el límite conjunto (art. 31) te rebaja "
                f"{format_amount(quote.reduced)}: Patrimonio e IRPF no pasan del 60 % de lo "
                f"que ganas, y siempre pagas al menos un {1 - WEALTH_MAX_REDUCTION:.0%}."
            ).replace("%.", " %.")
        if quote.subject:
            text += (
                f"\n💎 Pasas de {format_amount(SOLIDARITY_WEALTH_THRESHOLD)}: declaras las "
                f"Grandes Fortunas y pagas {format_amount(quote.solidarity)}. Se descuenta "
                "el Patrimonio y Canarias no lo bonifica."
            )
        if debt:
            text += (
                f"\nDebes {format_amount(debt)} a Hacienda de semanas anteriores: se cobran "
                "del monedero el lunes y de lo próximo que te den tus pisos."
            )
        embed.add_field(name=f"🐶 {TAX_COLLECTOR}", value=text, inline=False)
        embed.set_footer(
            text="Bienes al precio pagado sin IGIC; lo que caduca vale la vida que le queda."
        )
        if not author.bot:
            own = target.id == author.id
            await logros.track(
                self.bot,
                guild.id,
                author,
                channel,
                patrimonio_stats(
                    listing=False,
                    snooping=not own,
                    net_worth=worth.total if own else 0,
                    illiquid=own and worth.illiquid_share >= 0.5,
                    rank=rank if own else 0,
                    people=people,
                ),
            )
        return embed

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
            f"{medals[i] if i < len(medals) else f'`{i + 1}.`'} {self._titled(guild, w.user_id)} · "
            f"**{format_amount(w.total)}**\n-# 💵 {format_amount(w.cash)} · 🛍️ "
            f"{format_amount(w.goods_value)}"
            + (f" · 🏠 {format_amount(w.homes)}" if w.homes else "")
            + (f" · 🎟️ {format_amount(w.lottery)}" if w.lottery else "")
            + (f" · 📬 {format_amount(w.renta)}" if w.renta else "")
            + (f" · 🏦 −{format_amount(w.debts)}" if w.debts > 0 else "")
            + (f" · 🤝 {format_amount(-w.debts)}" if w.debts < 0 else "")
            for i, w in enumerate(everyone[:FORTUNES_TOP])
        ]
        if len(everyone) > FORTUNES_TOP:
            lines.append(f"-# Y {len(everyone) - FORTUNES_TOP} más. `perfil @miembro`.")
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
            text="Efectivo, pisos, bienes de la tienda, boletos por sortear y renta por cobrar, "
            "menos lo que se debe al banco. "
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


async def wealth_due(
    bot: commands.Bot, guild_id: int, user_id: int, cash: int
) -> WealthQuote | None:
    """El Patrimonio que el lunes cobraría a alguien con `cash` en efectivo (para `saldo`).

    `None` sin el cog o si falla la lectura de los pisos o de la tienda.
    """
    cog = find_cog(bot, Patrimonio)
    if cog is None:
        return None
    try:
        homes = (await cog.housing.member_values(guild_id))[1] if cog.housing else {}
        goods = await cog.shop.holdings(guild_id, user_id, time.time()) if cog.shop else []
        objects = sum(value for _u, _n, _e, kind, value, _x in goods if kind == Kind.TROPHY.value)
        return await cog.quote(
            guild_id, user_id, cash=cash, homes_fiscal=homes.get(user_id, 0), objects=objects
        )
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo calcular el Patrimonio de %s", user_id)
        return None


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(
        Patrimonio(bot, bot.economy, shop=bot.shop, lottery=bot.lottery, housing=bot.housing)
    )
