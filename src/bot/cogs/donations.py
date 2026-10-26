"""Donativos a las ONGs del bot: `donar`.

- `donar` sin argumentos abre el panel de las ONGs (Components V2). La portada tiene la
  regla de la deducción, tus niveles de mecenas y el muro de donantes. En el menú se
  elige una ONG y se ve lo que dice que hace, lo que hace de verdad, lo recaudado, sus
  mayores donantes, tu nivel con ella y sus regalos exclusivos, con botones para donar
  500, 5.000 u otra cantidad (formulario).
- `donar <ong> <cantidad>` dona. El dinero va a la cuenta de la ONG y no
  vuelve. A cambio desgrava: la deducción (art. 19.1 de la Ley 49/2002) rebaja
  la cuota de la declaración del lunes (sube lo que sale a devolver o baja lo que
  sale a pagar), hasta la cuota de esa semana. Reglas en `bot.services.donations`
  y `bot.services.taxes`.
- **Mecenas:** lo donado a cada ONG sube tu nivel con ella (`PATRON_LEVELS`). Al subir,
  la ONG te regala algo de la tienda (`tienda.grant_ong_gift`): casi siempre algo barato
  y, a veces (seguro en Oro y Patrono), uno de sus regalos exclusivos, que no se venden.
- **Sorteo:** cada viernes a las 20:00 (tarea de 10 minutos), cada ONG sortea un regalo
  entre quienes le donaron en los siete días anteriores, con más boletos cuanto más
  alto es su nivel (el lunes ya tiene la Renta y el Patrimonio). Cada sorteo se apunta
  una sola vez (`economy_ong_raffles`) y se anuncia en el canal. Ningún regalo mueve dinero.

El resultado de donar es público, para presumir de generosidad. Donar gasta dinero, así
que en `/donar` y en los botones del panel lleva el aviso de la Renta y la mascota habla
del donativo (`Event.DONATE`). Logros en Economía y Hacienda con `donation_stats` y
`ong_gift_stats`: lo donado, los donativos, cada ONG, todas, los niveles de mecenas, los
sorteos ganados, los regalos exclusivos y las fechas y cifras con guasa.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.cogs import shop as tienda
from bot.cogs.achievements import progress_bar
from bot.services.achievements import StatDelta, donation_stats, ong_gift_stats
from bot.services.donations import (
    BY_KEY,
    GIFT_CHEAP_MAX,
    GIFTS_BY_ONG,
    ONG_GIFTS,
    ONGS,
    PATRON_LEVELS,
    RAFFLE_EXCLUSIVE,
    Ong,
    PatronLevel,
    deduction_rule_text,
    draw_winner,
    find_ong,
    next_patron_level,
    patron_level,
    pick_spending,
    raffle_tickets,
    raffle_window,
)
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
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.services.shop import ShopItem

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(120, 190, 120)
VIEW_TIMEOUT = 600
#: Botones de donar rápido en la ficha de cada ONG.
QUICK_AMOUNTS = (500, 5_000)
#: Canal donde se anuncian los sorteos (si no está, el del sistema).
ANNOUNCE_CHANNEL_NAME = "chat-general"
EXCLUSIVE_KEYS = frozenset(g.catalog_key for g in ONG_GIFTS)
_NO_MENTIONS = discord.AllowedMentions.none()


def _name(guild: discord.Guild | None, user_id: int) -> str:
    member = guild.get_member(user_id) if guild is not None else None
    return discord.utils.escape_markdown(member.display_name) if member else f"<@{user_id}>"


def _board_lines(guild: discord.Guild | None, board: list[tuple[int, int]]) -> str:
    medals = ("🥇", "🥈", "🥉")
    return "\n".join(
        f"{medals[i] if i < 3 else f'{i + 1}.'} {_name(guild, user_id)} · {format_amount(total)}"
        for i, (user_id, total) in enumerate(board)
    )


def _level_text(level: PatronLevel | None) -> str:
    return f"{level.emoji} {level.name}" if level else "sin nivel"


def home_text(
    guild: discord.Guild | None, mine: dict[str, int], board: list[tuple[int, int]]
) -> str:
    """La portada del panel: la regla, los niveles, tus ONGs y el muro de donantes."""
    levels = " · ".join(f"{lvl.emoji} {format_amount(lvl.threshold)}" for lvl in PATRON_LEVELS)
    lines = [
        "# 🤝 ONGs que desgravan",
        f"Dona y recupera parte en la renta del lunes: {deduction_rule_text()}. Así le "
        f"pagas menos a {TAX_COLLECTOR}.",
        f"**Mecenas:** lo que donas a cada ONG sube tu nivel con ella ({levels}). Cada "
        "nivel trae un regalo y más boletos en el sorteo que hace cada ONG los viernes "
        "a las 20:00 entre quienes le donaron esa semana.",
        "### Tus ONGs",
    ]
    mine_lines = [
        f"{ong.emoji} {ong.name}: {format_amount(mine[ong.key])} · "
        f"{_level_text(patron_level(mine[ong.key]))}"
        for ong in ONGS
        if mine.get(ong.key)
    ]
    lines.append("\n".join(mine_lines) or "Aún no has donado a ninguna. Elige una en el menú.")
    lines.append("### 🏆 Muro de donantes")
    lines.append(_board_lines(guild, board) or "Nadie ha donado todavía. Hay sitio en la placa.")
    return "\n".join(lines)


def ong_text(
    guild: discord.Guild | None,
    ong: Ong,
    totals: dict[str, tuple[int, int]],
    mine: dict[str, int],
    board: list[tuple[int, int]],
) -> str:
    """La ficha de una ONG: lo que dice, lo que hace, tu nivel, sus donantes y regalos."""
    total, donors = totals.get(ong.key, (0, 0))
    given = mine.get(ong.key, 0)
    level, target = patron_level(given), next_patron_level(given)
    lines = [
        f"# {ong.emoji} {ong.name}",
        f"**Dice:** {ong.mission}\n**Hace:** {ong.reality}",
        f"-# Recaudado aquí: {format_amount(total)} de {donors} "
        f"{'donante' if donors == 1 else 'donantes'}",
        "### Tu nivel de mecenas",
        f"{_level_text(level)} · le has donado {format_amount(given)}",
    ]
    if target is not None:
        lines.append(
            f"`{progress_bar(given, target.threshold, 12)}` te faltan "
            f"{format_amount(target.threshold - given)} para {target.emoji} {target.name}"
        )
    else:
        lines.append("Eres Patrono: tu nombre va en la placa de la sede.")
    tickets = raffle_tickets(given)
    lines.append(
        f"-# Si le donas antes del viernes a las 20:00, entras en su sorteo con {tickets} "
        f"{'boleto' if tickets == 1 else 'boletos'}."
    )
    lines.append("### 🏆 Sus mayores donantes")
    lines.append(_board_lines(guild, board) or "Nadie le ha donado todavía.")
    lines.append("### 🎁 Regalos exclusivos")
    lines.append(" · ".join(f"{g.emoji} {g.name}" for g in GIFTS_BY_ONG[ong.key]))
    lines.append(
        f"-# No se venden. Salen pocas veces en el sorteo y al subir a 🥉 o 🥈, y seguro al "
        f"llegar a 🥇 Oro y a 🏛️ Patrono. Si no, la ONG regala algo de la tienda de hasta "
        f"{format_amount(GIFT_CHEAP_MAX)}."
    )
    return "\n".join(lines)


class AmountModal(ui.Modal, title="Donar"):
    """Otra cantidad para donar desde el panel."""

    amount: ui.TextInput = ui.TextInput(
        label="Cantidad", placeholder="500, 2k, mitad, all…", max_length=20
    )

    def __init__(self, panel: DonarPanel, ong: Ong) -> None:
        super().__init__(title=f"Donar a {ong.name}"[:45])
        self.panel = panel
        self.ong = ong

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.donate(interaction, self.ong, self.amount.value)


class DonarPanel(ui.LayoutView):
    """El panel de `donar`: la portada o la ficha de una ONG.

    Las cifras se leen al abrirlo y al cambiar de ONG o donar (con `ack` antes). Solo lo
    mueve quien lo abrió; los demás abren el suyo con `donar`.
    """

    def __init__(
        self,
        cog: Donaciones,
        owner: discord.abc.User,
        guild: discord.Guild,
        *,
        totals: dict[str, tuple[int, int]],
        mine: dict[str, int],
        board: list[tuple[int, int]],
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner = owner
        self.guild = guild
        self.totals = totals
        self.mine = mine
        self.home_board = board
        self.board: list[tuple[int, int]] = board
        self.page: Ong | None = None
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None
        self.rebuild()

    def rebuild(self) -> None:
        """Monta la portada o la ficha de la ONG elegida."""
        self.clear_items()
        container = ui.Container(accent_colour=COLOR)
        text = (
            home_text(self.guild, self.mine, self.home_board)
            if self.page is None
            else ong_text(self.guild, self.page, self.totals, self.mine, self.board)
        )
        container.add_item(ui.TextDisplay(text))
        container.add_item(ui.Separator())
        select: ui.Select = ui.Select(
            custom_id="donar:ong",
            placeholder="Elige una ONG",
            options=[
                discord.SelectOption(
                    label=ong.name,
                    value=ong.key,
                    emoji=ong.emoji,
                    description=f"Tu nivel: {_level_text(patron_level(self.mine.get(ong.key, 0)))}",
                    default=self.page is not None and ong.key == self.page.key,
                )
                for ong in ONGS
            ],
        )
        select.callback = self._choose  # type: ignore[method-assign]
        menu: ui.ActionRow = ui.ActionRow()
        menu.add_item(select)
        container.add_item(menu)
        if self.page is not None:
            row: ui.ActionRow = ui.ActionRow()
            for amount in QUICK_AMOUNTS:
                button: ui.Button = ui.Button(
                    label=f"Donar {format_amount(amount)}",
                    emoji="🤝",
                    style=discord.ButtonStyle.success,
                    custom_id=f"donar:dar:{amount}",
                )
                button.callback = self._quick(amount)  # type: ignore[method-assign]
                row.add_item(button)
            other: ui.Button = ui.Button(label="Otra cantidad", emoji="✏️", custom_id="donar:otra")
            other.callback = self._other  # type: ignore[method-assign]
            back: ui.Button = ui.Button(label="Portada", emoji="⬅️", custom_id="donar:inicio")
            back.callback = self._home  # type: ignore[method-assign]
            row.add_item(other)
            row.add_item(back)
            container.add_item(row)
        self.add_item(container)

    async def _mine_only(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner.id:
            return True
        await notify(interaction, "Este panel no es tuyo: abre el tuyo con `/donar`.")
        return False

    async def _choose(self, interaction: discord.Interaction) -> None:
        if not await self._mine_only(interaction):
            return
        values = interaction.data.get("values", []) if interaction.data else []
        ong = BY_KEY.get(values[0]) if values else None
        if ong is None:
            return
        # Los mayores donantes de la ONG salen de la base de datos: se acepta antes.
        await ack(interaction)
        self.page = ong
        self.board = await self.cog.economy.ong_board(self.guild.id, ong.key, 5)
        self.rebuild()
        await edit(interaction, view=self, allowed_mentions=_NO_MENTIONS)

    async def _home(self, interaction: discord.Interaction) -> None:
        if not await self._mine_only(interaction):
            return
        # Solo cambia memoria: se contesta directamente.
        self.page = None
        self.rebuild()
        await interaction.response.edit_message(view=self, allowed_mentions=_NO_MENTIONS)

    def _quick(self, amount: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            if self.page is not None and await self._mine_only(interaction):
                await self.donate(interaction, self.page, str(amount))

        return callback

    async def _other(self, interaction: discord.Interaction) -> None:
        # Abre un formulario: `send_modal` tiene que ser la primera respuesta.
        if self.page is not None and await self._mine_only(interaction):
            await interaction.response.send_modal(AmountModal(self, self.page))

    async def donate(self, interaction: discord.Interaction, ong: Ong, amount_text: str) -> None:
        """Dona desde el panel (botón o formulario) y rehace la ficha con las cifras nuevas."""
        await ack(interaction)
        donated = await self.cog.donar(
            InteractionResponder(interaction), interaction.user, ong.key, amount_text
        )
        if not donated:
            return
        economy = self.cog.economy
        self.totals = await economy.ong_totals(self.guild.id)
        self.mine = await economy.ong_member(self.guild.id, self.owner.id)
        self.board = await economy.ong_board(self.guild.id, ong.key, 5)
        self.home_board = await economy.ong_board(self.guild.id, None, 10)
        self.rebuild()
        try:
            await edit(interaction, view=self, allowed_mentions=_NO_MENTIONS)
        except discord.HTTPException:
            logger.debug("No se pudo rehacer el panel de donar", exc_info=True)
        # Donar gasta dinero: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.cog.bot, interaction)

    async def on_timeout(self) -> None:
        """Apaga el menú y los botones al caducar."""
        for child in self.walk_children():
            if isinstance(child, (ui.Button, ui.Select)):
                child.disabled = True
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar un panel de donar", exc_info=True)


class Donaciones(commands.Cog):
    """Donativos deducibles a las ONGs de broma, sus mecenas y sus sorteos."""

    def __init__(
        self, bot: commands.Bot, economy: EconomyService, rng: random.Random | None = None
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.rng = rng or random.Random()
        self._done: set[tuple[int, str]] = set()

    async def cog_load(self) -> None:
        self._raffles.start()

    async def cog_unload(self) -> None:
        self._raffles.cancel()

    async def panel(self, guild: discord.Guild, user: discord.abc.User) -> DonarPanel:
        """El panel de `donar`, con las cifras leídas ahora."""
        return DonarPanel(
            self,
            user,
            guild,
            totals=await self.economy.ong_totals(guild.id),
            mine=await self.economy.ong_member(guild.id, user.id),
            board=await self.economy.ong_board(guild.id, None, 10),
        )

    async def donar(
        self,
        responder: CommandResponder,
        user: discord.abc.User,
        ong_text_: str,
        amount_text: str,
    ) -> int:
        """Dona a una ONG, lo enseña y, si sube de nivel de mecenas, le da su regalo.

        Returns:
            Lo donado, o 0 si no se ha donado (para el aviso de la Renta).
        """
        guild = responder.guild
        if guild is None:
            await responder.send_error("Las ONGs solo aceptan donativos dentro de un servidor.")
            return 0
        ong = find_ong(ong_text_)
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
            allowed_mentions=_NO_MENTIONS,
        )
        # Lo que no hace falta para enseñar el donativo va después: el nivel y su regalo.
        given = (await self.economy.ong_member(guild.id, user.id)).get(ong.key, amount)
        reached = [lvl for lvl in PATRON_LEVELS if given - amount < lvl.threshold <= given]
        gifts = await self._level_gifts(responder, user, ong, reached)
        level = patron_level(given)
        delta = donation_stats(
            ong=ong.key,
            amount=amount,
            ongs_supported=receipt.ongs_supported,
            balance_after=receipt.balance,
            when=datetime.now(TIMEZONE),
            patron_level=PATRON_LEVELS.index(level) + 1 if level else 0,
        )
        for gift in gifts:
            delta.merge(ong_gift_stats(exclusive=gift.catalog_key in EXCLUSIVE_KEYS))
        await logros.track(self.bot, guild.id, user, responder.channel, delta)
        return amount

    async def _level_gifts(
        self,
        responder: CommandResponder,
        user: discord.abc.User,
        ong: Ong,
        reached: list[PatronLevel],
    ) -> list[ShopItem]:
        """Da y anuncia el regalo de cada nivel de mecenas alcanzado con este donativo."""
        guild = responder.guild
        if guild is None or not reached:
            return []
        keys = [g.catalog_key for g in GIFTS_BY_ONG[ong.key]]
        gifts, lines = [], []
        for level in reached:
            gift = await tienda.grant_ong_gift(
                self.bot,
                guild.id,
                user.id,
                keys,
                cheap_max=GIFT_CHEAP_MAX,
                exclusive=level.exclusive,
            )
            line = (
                f"{level.emoji} **{discord.utils.escape_markdown(user.display_name)}** ya es "
                f"mecenas **{level.name}** de {ong.emoji} {ong.name}."
            )
            if gift is not None:
                gifts.append(gift)
                rare = " (exclusivo, no se vende)" if gift.catalog_key in EXCLUSIVE_KEYS else ""
                line += f" Como agradecimiento le regalan **{gift.emoji} {gift.name}**{rare}."
            lines.append(line)
        try:
            await responder.send(
                embed=discord.Embed(description="\n".join(lines), color=COLOR),
                allowed_mentions=_NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.warning("No se pudo anunciar un nivel de mecenas", exc_info=True)
        return gifts

    async def patron_line(self, guild_id: int, user_id: int) -> str | None:
        """Su mejor nivel de mecenas, para `perfil` (`None` si no tiene ninguno)."""
        mine = await self.economy.ong_member(guild_id, user_id)
        best = max(
            ((total, BY_KEY[key]) for key, total in mine.items() if key in BY_KEY),
            default=None,
            key=lambda pair: pair[0],
        )
        level = patron_level(best[0]) if best else None
        if best is None or level is None:
            return None
        return f"🤝 Mecenas {level.emoji} {level.name} de {best[1].emoji} {best[1].name}"

    # -- Sorteos de los viernes -------------------------------------------------------

    @tasks.loop(minutes=10)
    async def _raffles(self) -> None:
        for guild in self.bot.guilds:
            await self.run_raffles(guild)

    @_raffles.before_loop
    async def _before_raffles(self) -> None:
        await self.bot.wait_until_ready()

    async def run_raffles(self, guild: discord.Guild) -> None:
        """Hace los sorteos del último viernes en `guild` si faltan. Nunca lanza."""
        window = raffle_window(datetime.now(TIMEZONE))
        key = (guild.id, window[1].isoformat())
        if key in self._done:
            return
        try:
            drawn = await self.economy.draw_ong_raffles(
                guild.id, window, lambda _ong, entries: draw_winner(entries, self.rng)
            )
        except Exception:
            # Si saliera la excepción, `tasks.loop` dejaría de sortear para siempre.
            logger.exception("No se pudieron sortear los regalos de las ONGs en %s", guild.id)
            return
        self._done.add(key)
        lines = []
        for ong_key, winner in drawn:
            ong = BY_KEY.get(ong_key)
            if ong is None:
                continue
            gift = await tienda.grant_ong_gift(
                self.bot,
                guild.id,
                winner,
                [g.catalog_key for g in GIFTS_BY_ONG[ong_key]],
                cheap_max=GIFT_CHEAP_MAX,
                exclusive=RAFFLE_EXCLUSIVE,
            )
            exclusive = gift is not None and gift.catalog_key in EXCLUSIVE_KEYS
            prize = (
                f"**{gift.emoji} {gift.name}**{' (exclusivo)' if exclusive else ''}"
                if gift is not None
                else "un abrazo (no quedaba nada que regalar)"
            )
            lines.append(f"{ong.emoji} {ong.name}: **{_name(guild, winner)}** se lleva {prize}")
            delta = ong_gift_stats(exclusive=exclusive) if gift is not None else StatDelta()
            delta.add["ong_raffle_won"] = 1
            logros.note(self.bot, guild.id, winner, delta)
        if not lines:
            return
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        if channel is None:
            return
        embed = discord.Embed(
            title="🎁 Sorteos de las ONGs",
            description="\n".join(lines)
            + "\n-# Entra quien dona esa semana; los mecenas llevan más boletos. `/donar`",
            color=COLOR,
        )
        try:
            await channel.send(embed=embed, allowed_mentions=_NO_MENTIONS)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudieron anunciar los sorteos en %s", guild.id, exc_info=True)

    # -- Comando ----------------------------------------------------------------------

    @app_commands.command(name="donar", description="Dona a una ONG y desgrava en la renta.")
    @app_commands.describe(
        ong="A quién donas (sin elegir, se abre el panel de las ONGs).",
        cantidad="Cuánto: 500, 2k, mitad, all…",
    )
    @app_commands.choices(
        ong=[app_commands.Choice(name=f"{o.emoji} {o.name}", value=o.key) for o in ONGS]
    )
    @app_commands.guild_only()
    async def donar_slash(
        self,
        interaction: discord.Interaction,
        ong: app_commands.Choice[str] | None = None,
        cantidad: str | None = None,
    ) -> None:
        """Dona a una ONG o, sin cantidad, abre el panel (en esa ONG si se eligió)."""
        assert interaction.guild is not None  # guild_only
        if ong is not None and cantidad:
            donated = await self.donar(
                InteractionResponder(interaction), interaction.user, ong.value, cantidad
            )
            if donated:
                # Donar gasta dinero: gancho de la Renta (ver Biblia.txt, sección 4).
                await renta.remind(self.bot, interaction)
            return
        # El panel lee varias cifras: se acepta el comando antes.
        await ack(interaction)
        panel = await self.panel(interaction.guild, interaction.user)
        if ong is not None:
            panel.page = BY_KEY[ong.value]
            panel.board = await self.economy.ong_board(interaction.guild.id, ong.value, 5)
            panel.rebuild()
        await edit(interaction, view=panel, allowed_mentions=_NO_MENTIONS)
        panel.interaction = interaction

    @commands.command(name="donar")
    @commands.guild_only()
    async def donar_text(
        self, ctx: commands.Context, ong: str | None = None, cantidad: str | None = None
    ) -> None:
        """Versión de texto: `.donar` abre el panel, `.donar <ong> <cantidad>` dona."""
        assert ctx.guild is not None  # guild_only
        if ong and cantidad:
            await self.donar(ContextResponder(ctx), ctx.author, ong, cantidad)
            return
        panel = await self.panel(ctx.guild, ctx.author)
        if ong and (found := find_ong(ong)) is not None:
            panel.page = found
            panel.board = await self.economy.ong_board(ctx.guild.id, found.key, 5)
            panel.rebuild()
        panel.message = await ctx.send(view=panel, allowed_mentions=_NO_MENTIONS)


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
    return await cog.donar(
        InteractionResponder(interaction), interaction.user, ong_key, amount_text
    )


async def patron_line(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """La línea de mecenas de `perfil`. No lanza: sin cog o con un fallo, no hay línea."""
    if (cog := find_cog(bot, Donaciones)) is None:
        return None
    try:
        return await cog.patron_line(guild_id, user_id)
    except Exception:
        logger.exception("No se pudo leer el nivel de mecenas de %s", user_id)
        return None


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Donaciones(bot, bot.economy))
