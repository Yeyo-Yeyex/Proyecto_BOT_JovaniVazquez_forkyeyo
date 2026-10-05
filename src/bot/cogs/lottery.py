"""Loterías: `loteria`, un panel con botones para todos los juegos del Estado.

Un solo comando abre el panel. Las pestañas cambian de juego sin escribir
nada más:

- 🏠 **Inicio**: próximos sorteos, botes y lo que lleva vendido y repartido el
  Estado.
- 🎫 **Nacional**: jueves, sábado, Navidad y Niño. Décimo al azar, billete
  entero (10 décimos) o número elegido.
- 🔵 **Primitiva**, 🟢 **Bonoloto**, 🟡 **Gordo** y ⭐ **Euromillones**: apuestas
  automáticas (1, 5 o 10) o elegidas a mano, con su tabla de probabilidades.
- 🟣 **Rascas**: X10 y 7 y Media de la ONCE. Se resuelven al momento y las
  casillas salen tapadas con spoilers: se rascan pulsándolas.
- 🎟️ **Mis boletos**: lo que tienes en juego y lo último que ha salido.

Con `/` el panel es efímero (solo lo ves tú). Con `.loteria` es público, y si
otro pulsa un botón se le abre su propio panel.

Sorteos: una tarea cada minuto celebra los sorteos que ya tocan (solo los que
tienen apuestas en el servidor), paga los premios y anuncia el resultado en el
canal donde se compró por última vez. Las reglas, las probabilidades y el
reparto están en `bot.services.lottery`.

Dinero: compras y premios pasan por `EconomyService.lottery`. Las compras van
al Estado sin IGIC y los premios pagan el gravamen especial del 20 % por
encima de 400.000 Y$ (ver su docstring). Comprar y rascar llaman a
`renta.remind`. Los sorteos no son interacciones: no avisan de la renta.

Logros: categoría Loterías, con `achievements.track` al comprar, al rascar y
al cobrar un premio de sorteo.

Permisos: enviar mensajes en el canal de los anuncios.
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.cogs.casino import casino_channel_error
from bot.repositories.lottery import Draw, DrawChanged, LotteryRepository
from bot.services.achievements import lottery_buy_stats, lottery_prize_stats, scratch_stats
from bot.services.economy import (
    CURRENCY_EMOJI,
    EconomyService,
    InsufficientFundsError,
    LotteryPayout,
    format_amount,
)
from bot.services.lottery import (
    GAME_BY_KEY,
    MAX_PER_DRAW,
    Game,
    Kind,
    LotteryError,
    Pick,
    Prize,
    Settlement,
    draw_label,
    draw_result,
    format_pick,
    format_result,
    gravamen,
    guarantee_for,
    jackpot_estimate,
    next_draw,
    parse_pick,
    random_pick,
    scratch,
    scratch_grid,
    scratch_odds,
    settle_nacional,
    settle_pool,
)
from bot.services.taxes import LOTTERY_EXEMPT, TAX_COLLECTOR

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

TITLE = "Loterías y Apuestas de Jovani"
COLOR = discord.Color.from_rgb(0, 122, 204)
VIEW_TIMEOUT = 600
#: Pestañas del panel: `(clave, etiqueta)`. Dos filas de botones.
TABS: tuple[tuple[str, str], ...] = (
    ("inicio", "🏠 Inicio"),
    ("nacional", "🎫 Nacional"),
    ("primitiva", "🔵 Primitiva"),
    ("bonoloto", "🟢 Bonoloto"),
    ("gordo", "🟡 Gordo"),
    ("euromillones", "⭐ Euromillones"),
    ("rasca", "🟣 Rascas"),
    ("mios", "🎟️ Mis boletos"),
)
NACIONAL_GAMES = ("jueves", "sabado", "navidad", "nino")
POOL_GAMES = ("primitiva", "bonoloto", "gordo", "euromillones")
SCRATCH_GAMES = ("x10", "7ymedia")
#: Apuestas automáticas que ofrece cada botón.
QUICK_BETS = (1, 5, 10)
#: Décimos de un billete entero.
BILLETE = 10
#: Boletos del historial en "Mis boletos".
HISTORY_SIZE = 15
#: Ganadores que se nombran en el anuncio de un sorteo.
ANNOUNCE_WINNERS = 10

PLACEHOLDERS = {
    Kind.LOTTO: "3 14 22 30 41 49 R7   (el reintegro es opcional)",
    Kind.GORDO: "3 14 22 30 41 C7   (la clave es opcional)",
    Kind.EURO: "3 14 22 30 41 * 2 9   (las estrellas son opcionales)",
}


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


def _button(
    label: str,
    callback: Callable[[discord.Interaction], Awaitable[None]],
    *,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    disabled: bool = False,
) -> ui.Button:
    """Botón con su callback ya enganchado."""
    button: ui.Button = ui.Button(label=label, style=style, disabled=disabled)
    button.callback = callback  # type: ignore[method-assign]
    return button


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _when(moment: float) -> str:
    return f"<t:{int(moment)}:f> (<t:{int(moment)}:R>)"


# -- Textos ---------------------------------------------------------------------------


def odds_table(game: Game) -> str:
    """Tabla de categorías de un juego de bote: qué acertar, probabilidad y premio."""
    rows = []
    for cat in game.categories:
        prize = (
            format_amount(cat.fixed)
            if cat.fixed is not None
            else ("bote" if cat.jackpot else f"{cat.share:.0%} fondo".replace("%", " %"))
        )
        rows.append(
            f"{cat.label:<9} {cat.rule:<18} 1 entre {_thousands(cat.odds(game.combinations)):>11}"
            f"  {prize}"
        )
    if game.reintegro:
        name = "Reintegro" if game.kind is Kind.LOTTO else "Clave"
        rows.append(f"{name:<9} {'la apuesta':<18} 1 entre {'10':>11}  devuelve el precio")
    return "```\n" + "\n".join(rows) + "\n```"


def nacional_table(game: Game) -> str:
    """Premios principales de un sorteo de la Nacional, por décimo."""
    program = game.program
    assert program is not None
    lines = []
    for prize in program.main:
        count = f"{prize.count} × " if prize.count > 1 else ""
        lines.append(f"{prize.label}: {count}**{format_amount(prize.amount)}**")
    if program.pedrea:
        lines.append(
            f"Pedrea: {_thousands(program.pedrea.count)} × {format_amount(program.pedrea.amount)}"
        )
    for digits, count, amount in program.extractions:
        lines.append(f"Extracciones de {digits} cifras: {count} × {format_amount(amount)}")
    lines.append(
        f"Aproximaciones, centenas y terminaciones · Reintegro: {format_amount(game.price)}"
    )
    lines.append(
        f"-# 1 entre 100.000 para el 1er premio. Devuelve el {game.prize_rate:.0%} "
        "de lo vendido.".replace("%", " %")
    )
    return "\n".join(lines)


def scratch_card(game: Game) -> str:
    """Tabla resumida de un rasca."""
    assert game.scratch is not None
    prizes, total = game.scratch
    top = ", ".join(format_amount(p.amount) for p in prizes[:3])
    return (
        f"### {game.emoji} {game.name} · {format_amount(game.price)}\n"
        f"{game.blurb}\nPremios gordos: {top}…\n"
        f"-# 1 de cada {str(scratch_odds(game)).replace('.', ',')} boletos tiene premio · "
        f"devuelve el {game.prize_rate:.0%} · emisión de {_thousands(total)} boletos".replace(
            "%", " %"
        )
    )


def scratch_text(game: Game, prize: Prize | None, cells: list[int], tax: int) -> str:
    """El rasca recién comprado, con las casillas tapadas (spoilers de Discord)."""
    width = max(len(format_amount(c)) for c in cells)
    rows = []
    for row in range(3):
        rows.append(
            " ".join(f"||`{format_amount(c):^{width}}`||" for c in cells[row * 3 : row * 3 + 3])
        )
    if prize is None:
        verdict = "||Nada. El cartón va a la papelera.||"
    else:
        verdict = f"||🎉 ¡Tres iguales! Premio de **{format_amount(prize.amount)}**||"
    lines = [f"### {game.emoji} {game.name} · rasca las casillas", *rows, verdict]
    if tax:
        lines.append(
            f"-# 🐶 {TAX_COLLECTOR} se queda ||{format_amount(tax)}|| de gravamen especial "
            f"(20 % de lo que pasa de {format_amount(LOTTERY_EXEMPT)})."
        )
    return "\n".join(lines)


def ticket_tags(game: Game, labels: list[str]) -> frozenset[str]:
    """Etiquetas de logros de un boleto premiado (ver `LOTTERY_TAGS`)."""
    tags: set[str] = set()
    for label in labels:
        if label == "Reintegro":
            tags.add("reintegro")
        elif label == "Pedrea":
            tags.add("pedrea")
        elif game.key == "navidad" and label == "EL GORDO":
            tags.add("gordo_navidad")
    if game.categories:
        jackpot = next(c for c in game.categories if c.jackpot)
        names = {f"{c.label} ({c.rule})": c for c in game.categories}
        for label in labels:
            cat = names.get(label)
            if cat is None:
                continue
            if cat is jackpot or (game.key == "primitiva" and cat.key == "1"):
                tags.add("jackpot")
            if game.kind is Kind.LOTTO and cat.key == "4":
                tags.add("lotto4")
            if game.kind is Kind.LOTTO and cat.key in ("2", "3"):
                tags.add("lotto5")
    return frozenset(tags)


# -- Panel ----------------------------------------------------------------------------


class PickModal(ui.Modal):
    """Apuestas o número elegidos a mano."""

    def __init__(self, panel: LotteryPanel, game: Game) -> None:
        super().__init__(title=f"{game.emoji} {game.name}"[:45])
        self.panel = panel
        self.game = game
        if game.kind is Kind.NACIONAL:
            self.picks: ui.TextInput = ui.TextInput(
                label="Número (5 cifras)", placeholder="48288", min_length=1, max_length=5
            )
            self.amount: ui.TextInput | None = ui.TextInput(
                label=f"Décimos (1-{BILLETE})", default="1", min_length=1, max_length=2
            )
            self.add_item(self.picks)
            self.add_item(self.amount)
        else:
            self.picks = ui.TextInput(
                label="Una apuesta por línea (hasta 10)",
                style=discord.TextStyle.paragraph,
                placeholder=PLACEHOLDERS[game.kind],
                max_length=500,
            )
            self.amount = None
            self.add_item(self.picks)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Lee las apuestas y compra."""
        rng = self.panel.cog.rng
        try:
            if self.amount is not None:
                quantity = int(self.amount.value.strip() or "1")
                if not 1 <= quantity <= BILLETE:
                    raise LotteryError(f"Entre 1 y {BILLETE} décimos.")
                picks = [(parse_pick(self.game, self.picks.value, rng), quantity)]
            else:
                lines = [line for line in self.picks.value.splitlines() if line.strip()]
                if not 1 <= len(lines) <= 10:
                    raise LotteryError("Entre 1 y 10 apuestas, una por línea.")
                picks = [(parse_pick(self.game, line, rng), 1) for line in lines]
        except (LotteryError, ValueError) as error:
            message = str(error) if isinstance(error, LotteryError) else "Eso no es un número."
            self.panel.notice = f"❌ {message}"
            self.panel.rebuild()
            await interaction.response.edit_message(view=self.panel)
            return
        await self.panel.cog.buy(interaction, self.panel, self.game, picks)


class LotteryPanel(ui.LayoutView):
    """El panel de loterías de un miembro: pestañas, compra y resultados."""

    def __init__(self, cog: Loteria, guild: discord.Guild, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.tab = "inicio"
        self.nacional = "jueves"
        self.notice: str | None = None
        self.body = ""
        self.balance = 0
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None

    async def load(self) -> None:
        """Lee lo que hace falta para la pestaña actual y repinta."""
        self.balance = await self.cog.economy.balance(self.guild.id, self.owner.id)
        self.body = await self.cog.tab_text(self.guild.id, self.owner.id, self.tab, self.nacional)
        self.rebuild()

    @property
    def game(self) -> Game | None:
        """Juego de la pestaña actual (el sorteo elegido en la Nacional)."""
        if self.tab == "nacional":
            return GAME_BY_KEY[self.nacional]
        return GAME_BY_KEY.get(self.tab)

    def rebuild(self) -> None:
        """Monta los componentes con el estado actual."""
        self.clear_items()
        container = ui.Container(accent_colour=COLOR)
        container.add_item(
            ui.TextDisplay(
                f"# 🎟️ {TITLE}\n-# {CURRENCY_EMOJI} Saldo de {_name(self.owner)}: "
                f"{format_amount(self.balance)} · Todo lo que juegas va al Estado"
            )
        )
        for start in (0, 5):
            row: ui.ActionRow = ui.ActionRow()
            for key, label in TABS[start : start + 5]:
                selected = key == self.tab
                style = discord.ButtonStyle.primary if selected else discord.ButtonStyle.secondary
                row.add_item(_button(label, self._tab(key), style=style))
            container.add_item(row)
        container.add_item(ui.Separator())
        if self.tab == "nacional":
            subtabs: ui.ActionRow = ui.ActionRow()
            for key in NACIONAL_GAMES:
                game = GAME_BY_KEY[key]
                style = (
                    discord.ButtonStyle.primary
                    if key == self.nacional
                    else discord.ButtonStyle.secondary
                )
                short = game.name.replace("Lotería Nacional del ", "").replace("Lotería de", "")
                short = short.replace("Lotería del", "").strip().capitalize()
                subtabs.add_item(_button(f"{game.emoji} {short}", self._nacional(key), style=style))
            container.add_item(subtabs)
        container.add_item(ui.TextDisplay(self.body[:3500]))
        actions = self._actions()
        if actions is not None:
            container.add_item(actions)
        if self.notice:
            container.add_item(ui.Separator())
            container.add_item(ui.TextDisplay(self.notice[:1900]))
        self.add_item(container)

    def _actions(self) -> ui.ActionRow | None:
        game = self.game
        row: ui.ActionRow = ui.ActionRow()
        if self.tab == "rasca":
            for key in SCRATCH_GAMES:
                scratch_game = GAME_BY_KEY[key]
                row.add_item(
                    _button(
                        f"{scratch_game.emoji} Rascar {scratch_game.name.replace('Rasca ', '')} · "
                        f"{format_amount(scratch_game.price)}",
                        self._scratch(key),
                        style=discord.ButtonStyle.success,
                    )
                )
            return row
        if game is None:
            return None
        if game.kind is Kind.NACIONAL:
            row.add_item(
                _button(
                    f"🎲 Décimo al azar · {format_amount(game.price)}",
                    self._quick(game.key, 1),
                    style=discord.ButtonStyle.success,
                )
            )
            row.add_item(
                _button(
                    f"🎲 Billete (10 décimos) · {format_amount(game.price * BILLETE)}",
                    self._quick(game.key, BILLETE),
                    style=discord.ButtonStyle.success,
                )
            )
            row.add_item(_button("✍️ Elegir número", self._choose(game.key)))
            return row
        for count in QUICK_BETS:
            label = "apuesta" if count == 1 else "apuestas"
            row.add_item(
                _button(
                    f"🎲 {count} {label} · {format_amount(game.price * count)}",
                    self._quick(game.key, count),
                    style=discord.ButtonStyle.success,
                )
            )
        row.add_item(_button("✍️ Elegir números", self._choose(game.key)))
        return row

    async def _redirect(self, interaction: discord.Interaction) -> bool:
        """Si no es el dueño, le abre su propio panel. Devuelve si lo hizo."""
        if interaction.user.id == self.owner.id:
            return False
        own = LotteryPanel(self.cog, self.guild, interaction.user)
        own.tab, own.nacional = self.tab, self.nacional
        await own.load()
        await interaction.response.send_message(view=own, ephemeral=True)
        own.interaction = interaction
        return True

    def _tab(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            self.tab, self.notice = key, None
            await self.load()
            await interaction.response.edit_message(view=self)

        return callback

    def _nacional(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            self.nacional, self.notice = key, None
            await self.load()
            await interaction.response.edit_message(view=self)

        return callback

    def _quick(self, key: str, count: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            game = GAME_BY_KEY[key]
            rng = self.cog.rng
            if game.kind is Kind.NACIONAL:
                picks = [(random_pick(game, rng), count)]
            else:
                picks = [(random_pick(game, rng), 1) for _ in range(count)]
            await self.cog.buy(interaction, self, game, picks)

        return callback

    def _choose(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            await interaction.response.send_modal(PickModal(self, GAME_BY_KEY[key]))

        return callback

    def _scratch(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            await self.cog.scratch(interaction, self, GAME_BY_KEY[key])

        return callback

    async def on_timeout(self) -> None:
        """Apaga los botones al caducar."""
        for child in self.walk_children():
            if isinstance(child, ui.Button):
                child.disabled = True
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar un panel de loterías", exc_info=True)


# -- Cog ------------------------------------------------------------------------------


class Loteria(commands.Cog):
    """Loterías del Estado: panel, compras, rascas y sorteos."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: LotteryRepository,
        *,
        casino_channel_ids: frozenset[int] = frozenset(),
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.casino_channel_ids = casino_channel_ids
        self.clock = clock
        # Las bolas salen del generador del sistema operativo: nadie puede
        # predecir un sorteo mirando los anteriores.
        self.rng = rng or secrets.SystemRandom()
        self._settling = asyncio.Lock()

    async def cog_load(self) -> None:
        """Crea las tablas (idempotente) y arranca los sorteos."""
        await self.repository.initialize()
        self._draws.start()

    async def cog_unload(self) -> None:
        """Para la tarea de sorteos."""
        self._draws.cancel()

    # -- Textos de las pestañas ------------------------------------------------------

    async def tab_text(self, guild_id: int, user_id: int, tab: str, nacional: str) -> str:
        """Cuerpo de una pestaña del panel."""
        if tab == "inicio":
            return await self._home_text(guild_id, user_id)
        if tab == "mios":
            return await self._mine_text(guild_id, user_id)
        if tab == "rasca":
            return "\n\n".join(scratch_card(GAME_BY_KEY[key]) for key in SCRATCH_GAMES)
        key = nacional if tab == "nacional" else tab
        return await self._game_text(guild_id, user_id, GAME_BY_KEY[key])

    async def _jackpot(self, guild_id: int, game: Game, draw: Draw | None) -> int:
        carry = (await self.repository.pots(guild_id)).get(game.key, 0)
        guarantee = guarantee_for(game, await self.economy.state_balance(guild_id))
        sales = draw.sales if draw is not None else 0
        return jackpot_estimate(game, sales=sales, carry=carry, guarantee=guarantee)

    async def _home_text(self, guild_id: int, user_id: int) -> str:
        now = self.clock()
        open_draws = await self.repository.open_draws(guild_id)
        pots = await self.repository.pots(guild_id)
        balance = await self.economy.state_balance(guild_id)
        lines = ["## Próximos sorteos"]
        for key in (*NACIONAL_GAMES, *POOL_GAMES):
            game = GAME_BY_KEY[key]
            at = next_draw(game, now)
            draw = open_draws.get((key, at))
            extra = ""
            if game.categories:
                guarantee = guarantee_for(game, balance)
                bote = jackpot_estimate(
                    game,
                    sales=draw.sales if draw else 0,
                    carry=pots.get(key, 0),
                    guarantee=guarantee,
                )
                extra = f" · bote **{format_amount(bote)}**"
            lines.append(f"{game.emoji} **{game.name}** · <t:{int(at)}:R>{extra}")
        totals = await self.repository.totals(guild_id)
        lines.append(
            f"\n🏛️ El Estado ha vendido **{format_amount(totals.sold)}** y repartido "
            f"**{format_amount(totals.paid)}** en premios "
            f"({_thousands(totals.winners)} boletos premiados)."
        )
        lines.append(
            "-# Probabilidades reales: el gordo es casi imposible, lo que toca son "
            "reintegros y premios pequeños. Si el bote no tiene dueño, se acumula."
        )
        return "\n".join(lines)

    async def _game_text(self, guild_id: int, user_id: int, game: Game) -> str:
        now = self.clock()
        at = next_draw(game, now)
        draw = (await self.repository.open_draws(guild_id)).get((game.key, at))
        lines = [
            f"## {game.emoji} {game.name}",
            game.blurb,
            f"🗓️ Próximo sorteo: {_when(at)} · {format_amount(game.price)} "
            f"{'el décimo' if game.kind is Kind.NACIONAL else 'la apuesta'}",
        ]
        if game.categories:
            bote = await self._jackpot(guild_id, game, draw)
            lines.append(f"💰 Bote: **{format_amount(bote)}**")
            lines.append(odds_table(game))
        else:
            lines.append(nacional_table(game))
        mine = await self.repository.member_tickets(guild_id, user_id, game=game.key, draw_at=at)
        if mine:
            units = sum(t.quantity for t in mine)
            shown = [
                f"`{format_pick(game, Pick.decode(t.pick))}`"
                + (f" ×{t.quantity}" if t.quantity > 1 else "")
                for t in mine[:8]
            ]
            more = f" y {len(mine) - 8} más" if len(mine) > 8 else ""
            lines.append(f"🎟️ Llevas {units} en este sorteo: " + ", ".join(shown) + more)
        last = await self.repository.last_draw(guild_id, game.key)
        if last is not None and last.result is not None:
            lines.append(
                f"-# Último sorteo ({draw_label(game, last.draw_at)}): "
                + format_result(game, last.result).split("\n")[0]
            )
        return "\n".join(lines)

    async def _mine_text(self, guild_id: int, user_id: int) -> str:
        records = await self.repository.history(guild_id, user_id, limit=HISTORY_SIZE)
        if not records:
            return "## 🎟️ Mis boletos\nNada todavía. Hoy puede ser tu día, mi amor."
        pending = [r for r in records if r.status == "open"]
        done = [r for r in records if r.status != "open"]
        lines = ["## 🎟️ Mis boletos"]
        if pending:
            lines.append("**En juego**")
            for record in pending:
                game = GAME_BY_KEY[record.game]
                count = f" ×{record.quantity}" if record.quantity > 1 else ""
                lines.append(
                    f"{game.emoji} `{format_pick(game, Pick.decode(record.pick))}`{count} · "
                    f"<t:{int(record.draw_at)}:R>"
                )
        if done:
            lines.append("**Últimos resultados**")
            for record in done:
                game = GAME_BY_KEY[record.game]
                outcome = (
                    f"✅ {format_amount(record.prize)} ({record.detail})"
                    if record.prize
                    else "❌ Nada"
                )
                lines.append(
                    f"{game.emoji} `{format_pick(game, Pick.decode(record.pick))}` · {outcome}"
                )
        return "\n".join(lines)

    # -- Compras ---------------------------------------------------------------------

    async def open_panel(self, guild: discord.Guild, owner: discord.abc.User) -> LotteryPanel:
        """Panel listo para enviar."""
        panel = LotteryPanel(self, guild, owner)
        await panel.load()
        return panel

    async def buy(
        self,
        interaction: discord.Interaction,
        panel: LotteryPanel,
        game: Game,
        picks: list[tuple[Pick, int]],
    ) -> None:
        """Cobra unas apuestas (o décimos), las apunta y repinta el panel.

        Responde a la interacción editando el panel; después avisa de la
        renta y apunta los logros.
        """
        guild, user = panel.guild, interaction.user
        now = self.clock()
        draw_at = next_draw(game, now)
        units = sum(quantity for _, quantity in picks)
        cost = game.price * units
        try:
            (_, owned), balances = await self.economy.lottery(
                guild.id,
                charges=[(user.id, cost, f"loteria:{game.key}")],
                hook=self.repository.reserve(
                    guild.id,
                    user.id,
                    game=game.key,
                    draw_at=draw_at,
                    picks=[(pick.encode(), quantity) for pick, quantity in picks],
                    price=game.price,
                    channel_id=interaction.channel_id,
                    now=now,
                ),
            )
        except LotteryError as error:
            panel.notice = f"❌ {error}"
        except InsufficientFundsError as error:
            panel.notice = (
                f"❌ ¡Ay, bendito! Son {format_amount(cost)} y tienes "
                f"{format_amount(error.balance)}. Cobra el `imv` y vuelve."
            )
        else:
            shown = ", ".join(
                f"`{format_pick(game, pick)}`" + (f" ×{quantity}" if quantity > 1 else "")
                for pick, quantity in picks
            )
            lines = [
                f"✅ **{game.name}**, sorteo del <t:{int(draw_at)}:f>: {shown}",
                f"-# Pagas {format_amount(cost)}, sin IGIC (la lotería está exenta). "
                f"Llevas {owned} de {MAX_PER_DRAW} en este sorteo.",
            ]
            if hint := await renta.hint(self.bot, guild.id, user.id):
                lines.append(hint)
            panel.notice = "\n".join(lines)
            panel.balance = balances[user.id]
            await panel.load()
            await interaction.response.edit_message(view=panel)
            await renta.remind(self.bot, interaction)
            await logros.track(
                self.bot,
                guild.id,
                user,
                interaction.channel,
                lottery_buy_stats(
                    game=game.key,
                    units=units,
                    cost=cost,
                    owned_in_draw=owned,
                    balance_after=balances[user.id],
                ),
            )
            return
        panel.rebuild()
        await interaction.response.edit_message(view=panel)

    async def scratch(
        self, interaction: discord.Interaction, panel: LotteryPanel, game: Game
    ) -> None:
        """Vende un rasca, lo resuelve al momento y enseña las casillas tapadas."""
        guild, user = panel.guild, interaction.user
        prize = scratch(game, self.rng)
        gross = prize.amount if prize else 0
        tax = gravamen(gross) if gross else 0
        payouts = [LotteryPayout(user.id, gross, tax, game.key)] if gross else []
        try:
            _, balances = await self.economy.lottery(
                guild.id,
                charges=[(user.id, game.price, f"loteria:{game.key}")],
                payouts=payouts,
                hook=self.repository.record_scratch(
                    guild.id,
                    user.id,
                    game=game.key,
                    cost=game.price,
                    prize=gross,
                    tax=tax,
                    now=self.clock(),
                ),
            )
        except InsufficientFundsError as error:
            panel.notice = (
                f"❌ El rasca cuesta {format_amount(game.price)} y tienes "
                f"{format_amount(error.balance)}."
            )
            panel.rebuild()
            await interaction.response.edit_message(view=panel)
            return
        balance = balances[user.id]
        lines = [scratch_text(game, prize, scratch_grid(game, prize, self.rng), tax)]
        if hint := await renta.hint(self.bot, guild.id, user.id):
            lines.append(hint)
        panel.notice = "\n".join(lines)
        panel.balance = balance
        panel.rebuild()
        await interaction.response.edit_message(view=panel)
        await renta.remind(self.bot, interaction)
        assert game.scratch is not None
        await logros.track(
            self.bot,
            guild.id,
            user,
            interaction.channel,
            scratch_stats(
                cost=game.price,
                prize=gross,
                tax=tax,
                top=prize is not None and prize is game.scratch[0][0],
                balance_after=balance,
            ),
        )

    # -- Sorteos ---------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def _draws(self) -> None:
        try:
            await self.run_due_draws()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error celebrando sorteos de lotería")

    @_draws.before_loop
    async def _before_draws(self) -> None:
        await self.bot.wait_until_ready()

    async def run_due_draws(self) -> int:
        """Celebra todos los sorteos que ya tocan. Devuelve cuántos ha cerrado."""
        async with self._settling:
            done = 0
            for draw in await self.repository.due_draws(self.clock()):
                if await self.settle(draw):
                    done += 1
            return done

    async def settle(self, draw: Draw) -> bool:
        """Celebra un sorteo: saca las bolas, paga y lo anuncia.

        Returns:
            Si se ha cerrado (no lo hace si otro proceso ya lo cerró o si
            entró una apuesta a última hora; se reintenta en el siguiente minuto).
        """
        game = GAME_BY_KEY[draw.game]
        tickets = await self.repository.tickets(draw.id)
        result = draw_result(game, self.rng)
        carry: int | None = None
        if game.kind is Kind.NACIONAL:
            settlement = settle_nacional(
                game,
                tickets=[(t.id, Pick.decode(t.pick).numbers[0], t.quantity) for t in tickets],
                result=result,
            )
        else:
            guarantee = guarantee_for(game, await self.economy.state_balance(draw.guild_id))
            settlement = settle_pool(
                game,
                sales=draw.sales,
                carry=await self.repository.carry(draw.guild_id, game.key),
                guarantee=guarantee,
                tickets=[(t.id, Pick.decode(t.pick)) for t in tickets],
                result=result,
            )
            carry = settlement.carry
        by_id = {t.id: t for t in tickets}
        prizes: dict[int, tuple[int, int, str]] = {}
        totals: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for ticket_id, gross in settlement.prizes.items():
            ticket = by_id[ticket_id]
            # El gravamen se calcula por décimo o apuesta, que es como exime la ley.
            tax = gravamen(gross // ticket.quantity, ticket.quantity)
            prizes[ticket_id] = (gross, tax, ", ".join(settlement.labels[ticket_id]))
            totals[ticket.user_id][0] += gross
            totals[ticket.user_id][1] += tax
        payouts = [
            LotteryPayout(user_id, gross, tax, game.key)
            for user_id, (gross, tax) in sorted(totals.items())
        ]
        summary = {
            "categories": [[c.key, c.winners, c.prize] for c in settlement.categories],
            "carry": settlement.carry,
            "topup": settlement.topup,
            "sales": draw.sales,
            "paid": settlement.paid,
        }
        try:
            await self.economy.lottery(
                draw.guild_id,
                payouts=payouts,
                hook=self.repository.close(
                    draw,
                    result=result,
                    summary=summary,
                    prizes=prizes,
                    carry=carry,
                    now=self.clock(),
                ),
            )
        except DrawChanged:
            logger.info("El sorteo %s cambió mientras se repartía; se reintenta", draw.id)
            return False
        await self.announce(draw, game, result, settlement, tickets_by_id=by_id, prizes=prizes)
        return True

    async def announce(
        self,
        draw: Draw,
        game: Game,
        result: dict,
        settlement: Settlement,
        *,
        tickets_by_id: dict,
        prizes: dict[int, tuple[int, int, str]],
    ) -> None:
        """Anuncia el resultado en el canal del sorteo y apunta los logros. Nunca lanza."""
        guild = self.bot.get_guild(draw.guild_id)
        channel = self.bot.get_channel(draw.channel_id) if draw.channel_id else None
        if guild is None:
            return
        by_user: dict[int, list[tuple[int, int, frozenset[str]]]] = defaultdict(list)
        for ticket_id, (gross, tax, _) in prizes.items():
            ticket = tickets_by_id[ticket_id]
            by_user[ticket.user_id].append(
                (gross, tax, ticket_tags(game, settlement.labels[ticket_id]))
            )
        if isinstance(channel, discord.abc.Messageable):
            text = self.result_text(guild, draw, game, result, settlement, prizes, tickets_by_id)
            try:
                await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                logger.warning("No se pudo anunciar el sorteo %s", draw.id, exc_info=True)
        for user_id, rows in by_user.items():
            member = guild.get_member(user_id)
            if member is not None:
                await logros.track(self.bot, guild.id, member, channel, lottery_prize_stats(rows))

    def result_text(
        self,
        guild: discord.Guild,
        draw: Draw,
        game: Game,
        result: dict,
        settlement: Settlement,
        prizes: dict[int, tuple[int, int, str]],
        tickets_by_id: dict,
    ) -> str:
        """Texto público con el resultado de un sorteo."""
        lines = [f"## {game.emoji} Sorteo · {draw_label(game, draw.draw_at)}"]
        lines.append(format_result(game, result))
        # Un renglón por persona: su total y su mejor boleto, para no llenar el canal.
        per_user: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for ticket_id, (gross, _tax, _detail) in prizes.items():
            per_user[tickets_by_id[ticket_id].user_id].append((gross, ticket_id))
        ranking = sorted(per_user.items(), key=lambda item: -sum(g for g, _ in item[1]))
        if ranking:
            lines.append("**Premiados**")
            for user_id, won in ranking[:ANNOUNCE_WINNERS]:
                member = guild.get_member(user_id)
                who = _name(member) if member else f"<@{user_id}>"
                best_gross, best_id = max(won)
                pick = format_pick(game, Pick.decode(tickets_by_id[best_id].pick))
                best = f"`{pick}` · {format_amount(best_gross)} ({prizes[best_id][2]})"
                if len(won) == 1:
                    lines.append(f"🎉 **{who}** · {best}")
                else:
                    total = sum(g for g, _ in won)
                    lines.append(
                        f"🎉 **{who}** · {len(won)} boletos, {format_amount(total)} · mejor: {best}"
                    )
            if len(ranking) > ANNOUNCE_WINNERS:
                lines.append(f"…y {len(ranking) - ANNOUNCE_WINNERS} premiados más.")
        else:
            lines.append("Nadie del servidor ha cobrado nada. El Estado lo agradece.")
        if game.categories:
            jackpot = next(c for c in game.categories if c.jackpot)
            won = next(c for c in settlement.categories if c.key == jackpot.key).winners
            following = next_draw(game, draw.draw_at)
            if won:
                lines.append(f"💥 **¡BOTE!** {won} acertante(s) de la {jackpot.label}.")
            else:
                lines.append(
                    f"💰 Bote sin dueño: pasan **{format_amount(settlement.carry)}** al "
                    f"sorteo de <t:{int(following)}:R>."
                )
        taxed = sum(tax for _, tax, _ in prizes.values())
        footer = (
            f"-# Vendido: {format_amount(draw.sales)} · En premios: "
            f"{format_amount(settlement.paid)}"
        )
        if taxed:
            footer += f" · {TAX_COLLECTOR} se queda {format_amount(taxed)} de gravamen especial"
        lines.append(footer)
        return "\n".join(lines)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las loterías del servidor que el bot abandona."""
        await self.repository.delete_guild_data(guild.id)

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(
        name="loteria",
        description="Nacional, Primitiva, Bonoloto, Gordo, Euromillones y rascas de la ONCE.",
    )
    @app_commands.guild_only()
    async def loteria(self, interaction: discord.Interaction) -> None:
        """Abre el panel de loterías, efímero (solo lo ve quien lo abre)."""
        assert interaction.guild is not None  # guild_only
        channel = interaction.channel
        if error := casino_channel_error(self.casino_channel_ids, channel, "La lotería"):
            await interaction.response.send_message(error, ephemeral=True)
            return
        panel = await self.open_panel(interaction.guild, interaction.user)
        await interaction.response.send_message(
            view=panel, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )
        panel.interaction = interaction

    @commands.command(name="loteria")
    @commands.guild_only()
    async def loteria_text(self, ctx: commands.Context) -> None:
        """Versión de texto: el panel es público y cada uno juega en el suyo."""
        assert ctx.guild is not None  # guild_only
        if error := casino_channel_error(self.casino_channel_ids, ctx.channel, "La lotería"):
            await ctx.send(error)
            return
        panel = await self.open_panel(ctx.guild, ctx.author)
        panel.message = await ctx.send(view=panel, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y las loterías del bot."""
    await bot.add_cog(
        Loteria(bot, bot.economy, bot.lottery, casino_channel_ids=bot.casino_channel_ids)
    )
