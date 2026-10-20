"""Apuestas deportivas: `deportes`, cuotas fijas sobre partidos de verdad.

`/deportes` (y `.deportes`) abre un panel con los próximos partidos de cada competición
de `bot.services.sports.SPORTS` (LaLiga y la Champions) y sus cuotas 1X2:

- Un botón por competición y, debajo, un desplegable con sus próximos partidos (los
  `MENU_SIZE` que antes empiezan).
- Al elegir un partido salen sus tres signos (1, X y 2) con su cuota. Cada botón abre
  un formulario con la cantidad (500, 2k, mitad, all…) y apuesta a esa cuota, que queda
  fijada. Si la cuota ha cambiado desde que se pintó el panel, avisa y no cobra.
- 🎟️ **Mis apuestas** enseña las que esperan resultado y las últimas resueltas.

Solo lo maneja quien lo abre. Con `/deportes` el panel es efímero; con `.deportes` se ve
en el canal. Abrirlo y moverse por él no toca la base de datos: los partidos viven en
memoria (`matches`) y se rehacen cada vez que llegan cuotas nuevas.

Cuotas y resultados. Cada 5 minutos (`tick`) el cog mira qué toca pedir a The Odds API
(`bot.services.sports.odds_due` y `scores_due`), lo guarda en `bot.repositories.sports` y
resuelve lo que ya tiene resultado (`settle`): paga y, en el canal donde se apostó, sale
el marcador con quién cobra y quién no (solo menciona a quien cobra o recupera). Lo que
lleva `VOID_AFTER` sin resultado se anula y se devuelve. Sin `ODDS_API_KEY` no se pide
nada y el panel dice que no está configurado; las apuestas que quedaran se anulan a su
hora.

Dinero (`EconomyService.sports_bet`, `settle_sports_bet` y `void_sports_bet`): lo
apostado espera en el depósito de las apuestas deportivas y, al resolverse, apuesta y
premio entran a la vez en el día del casino, con la retención del IRPF del juego.
Tratamiento fiscal en `EconomyService.sports_bet`. Las cuotas llevan el margen de la casa
(`RTP`): de media desaparece más dinero del que se crea. Lo que espera sale en `saldo`
(`pending`). Apostar es gastar: lleva `renta.remind` y la línea de `renta.hint`.

Logros: sección ⚽ Deportes del grupo 🎰 Casino (`sports_bet_stats`,
`sports_result_stats`), más lo común del casino al resolverse (`casino_play` y
`apuestas.record`). No admite porras (`porras.EXCLUDED_GAMES`): se resuelve días después.

Si `CASINO_CHANNEL_IDS` está configurado, solo se abre en esos canales. Permisos del bot
en el canal: enviar mensajes e insertar enlaces. No necesita intents especiales.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error
from bot.repositories.economy import BetSettlement
from bot.repositories.sports import SportsRepository
from bot.services.achievements import (
    casino_stats,
    sports_bet_stats,
    sports_result_stats,
    sports_slip_stats,
    sports_void_stats,
)
from bot.services.casino_stats import mult
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    SportsBet,
    SportsBetTakenError,
    format_amount,
    is_all_in,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pending import Pending, Side
from bot.services.pets import Moment
from bot.services.sports import (
    GAME,
    LOOKAHEAD_SECONDS,
    MAX_STAKE,
    MIN_ODDS,
    MIN_STAKE,
    RESULT_DELAY,
    SPORT_BY_KEY,
    SPORTS,
    VOID_AFTER,
    FeedError,
    Match,
    OddsClient,
    Pick,
    Score,
    days_from,
    odds_due,
    parse_odds,
    parse_scores,
    payout,
    scores_due,
)
from bot.services.telemetry import TimedLock
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Segundos que se puede manejar el panel (menos que los 15 minutos del token de Discord).
VIEW_TIMEOUT = 600
#: Partidos en el desplegable (el máximo de Discord) y en la portada del panel.
MENU_SIZE = 25
LISTED = 8
#: Apuestas que enseña 🎟️ Mis apuestas y que pueden salir en `saldo`.
SLIP_SIZE = 15
#: Apostantes por mensaje de resultado; el resto sale como «y N más».
RESULT_LINES = 20
#: Segundos que se espera a la API de cuotas.
REQUEST_TIMEOUT_SECONDS = 20
#: Los partidos que empezaron hace más de esto se borran (una vez al día).
PRUNE_AFTER = 14 * 86_400
WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
COLOR = discord.Color.from_rgb(46, 160, 67)

NOT_CONFIGURED = (
    "Las apuestas deportivas no están configuradas en este bot: falta la clave de la API "
    "de cuotas (`ODDS_API_KEY`)."
)


def odds_text(odds: int) -> str:
    """`185` → `×1,85`."""
    return mult(odds / 100)


def kickoff_text(epoch: float) -> str:
    """`sáb 25/10 21:00` en hora canaria (para donde no caben fechas de Discord)."""
    local = datetime.fromtimestamp(epoch, TIMEZONE)
    return f"{WEEKDAYS[local.weekday()]} {local:%d/%m %H:%M}"


def pick_label(home: str, away: str, pick: str) -> str:
    """`1 (Real Madrid)`, `X (empate)` o `2 (Barcelona)`."""
    return {"1": f"1 ({home})", "X": "X (empate)", "2": f"2 ({away})"}.get(pick, pick)


def odds_line(match: Match) -> str:
    """`1 ×1,85 · X ×3,60 · 2 ×4,10`; `—` en un signo sin cuota."""
    return " · ".join(
        f"{pick.value} {odds_text(match.odds[pick]) if pick in match.open_picks else '—'}"
        for pick in Pick
    )


def panel_embed(
    sport: str,
    matches: list[Match],
    selected: Match | None,
    *,
    configured: bool,
    odds_at: float | None,
) -> discord.Embed:
    """El panel de `deportes`: los partidos de una competición o la ficha de uno."""
    label = SPORT_BY_KEY[sport].label
    embed = discord.Embed(title=f"⚽ Apuestas deportivas · {label}", color=COLOR)
    lines: list[str] = []
    if not configured:
        lines.append(NOT_CONFIGURED)
    elif selected is not None:
        start = int(selected.commence)
        lines += [f"**{selected.title}**", f"🕒 <t:{start}:F> (<t:{start}:R>)", ""]
        for pick in Pick:
            team = selected.team(pick)
            if pick in selected.open_picks:
                odds = selected.odds[pick]
                lines.append(
                    f"`{pick.value}` {team} · **{odds_text(odds)}** · 100 Y$ cobran "
                    f"{format_amount(payout(100, odds))}"
                )
            else:
                lines.append(f"`{pick.value}` {team} · sin cuota")
        lines += ["", "Pulsa un signo para apostar."]
    elif matches:
        for match in matches[:LISTED]:
            lines.append(
                f"`{kickoff_text(match.commence)}` **{match.title}**\n-# {odds_line(match)}"
            )
        if len(matches) > LISTED:
            lines.append(f"-# Y {len(matches) - LISTED} partidos más en el desplegable.")
        lines += ["", "Elige un partido en el desplegable para apostar."]
    else:
        days = LOOKAHEAD_SECONDS // 86_400
        lines.append(
            f"No hay partidos con cuotas en los próximos {days} días. Mira la otra "
            "competición o vuelve más tarde."
        )
    lines.append(
        f"-# Cuota fija al apostar · se cobra con el marcador final · una apuesta por "
        f"partido, de {format_amount(MIN_STAKE)} a {format_amount(MAX_STAKE)}."
    )
    if configured and odds_at is not None:
        lines.append(f"-# Cuotas actualizadas <t:{int(odds_at)}:R>.")
    embed.description = "\n".join(lines)
    return embed


def slip_embed(bets: list[SportsBet], name: str) -> discord.Embed:
    """🎟️ Mis apuestas: las que esperan resultado y las últimas resueltas."""
    embed = discord.Embed(title=f"🎟️ Apuestas deportivas de {name}", color=COLOR)
    waiting, done = [], []
    for bet in bets:
        what = f"{pick_label(bet.home, bet.away, bet.pick)} en **{bet.home} – {bet.away}**"
        if bet.status == "open":
            waiting.append(
                f"⏳ {format_amount(bet.stake)} al {what} a {odds_text(bet.odds)} · "
                f"cobrarías {format_amount(payout(bet.stake, bet.odds))} · "
                f"<t:{int(bet.commence)}:R>"
            )
        elif bet.status == "won":
            done.append(
                f"✅ {format_amount(bet.stake)} al {what} a {odds_text(bet.odds)} · "
                f"cobraste {format_amount(bet.payout)}"
            )
        elif bet.status == "lost":
            done.append(f"❌ {format_amount(bet.stake)} al {what} a {odds_text(bet.odds)}")
        else:
            done.append(f"↩️ {format_amount(bet.stake)} al {what} · anulada y devuelta")
    if waiting:
        embed.add_field(name="Esperando resultado", value="\n".join(waiting)[:1024], inline=False)
    if done:
        embed.add_field(name="Últimas resueltas", value="\n".join(done)[:1024], inline=False)
    if not bets:
        embed.description = "Aún no has apostado a ningún partido. Abre uno en el desplegable."
    return embed


@dataclass(frozen=True, slots=True)
class Resolved:
    """Una apuesta recién resuelta, para el mensaje del resultado y los logros.

    Attributes:
        bet: La apuesta ya cerrada (`won`, `lost` o `void`).
        score: El marcador, o `None` si se anuló.
        settlement: Lo que movió en el día del casino (`None` si se anuló).
        balance: Saldo de su dueño al terminar.
    """

    bet: SportsBet
    score: Score | None
    settlement: BetSettlement | None
    balance: int


def result_message(resolved: list[Resolved]) -> str:
    """Mensaje del resultado de un partido para los que apostaron en un canal."""
    first = resolved[0].bet
    league = SPORT_BY_KEY[first.sport].label if first.sport in SPORT_BY_KEY else first.sport
    score = resolved[0].score
    if score is None:
        lines = [
            f"↩️ **{first.home} – {first.away}** ({league}) no tiene resultado: aplazado, "
            "suspendido o sin datos. Se devuelve lo apostado."
        ]
        lines += [
            f"• <@{r.bet.user_id}> recupera {format_amount(r.bet.stake)}"
            for r in resolved[:RESULT_LINES]
        ]
    else:
        lines = [f"⚽ **Final: {first.home} {score.home}–{score.away} {first.away}** · {league}"]
        for r in resolved[:RESULT_LINES]:
            bet = r.bet
            what = f"{pick_label(bet.home, bet.away, bet.pick)} a {odds_text(bet.odds)}"
            if bet.status == "won":
                tax = r.settlement.tax_delta if r.settlement is not None else 0
                irpf = f" · 🐶 IRPF −{format_amount(tax)}" if tax > 0 else ""
                lines.append(
                    f"✅ <@{bet.user_id}> {what}: cobra **{format_amount(bet.payout)}** "
                    f"(+{format_amount(bet.payout - bet.stake)}){irpf}"
                )
            else:
                lines.append(f"❌ <@{bet.user_id}> {what}: pierde {format_amount(bet.stake)}")
    if len(resolved) > RESULT_LINES:
        lines.append(f"-# Y {len(resolved) - RESULT_LINES} apuestas más.")
    return "\n".join(lines)


def win_streak(bets: list[SportsBet]) -> int:
    """Apuestas acertadas seguidas, de la última resuelta hacia atrás (sin anuladas)."""
    settled = sorted(
        (b for b in bets if b.status in ("won", "lost")),
        key=lambda b: (b.settled_at or 0.0, b.id),
        reverse=True,
    )
    streak = 0
    for bet in settled:
        if bet.status != "won":
            break
        streak += 1
    return streak


# -- Panel ------------------------------------------------------------------------------


class StakeModal(discord.ui.Modal):
    """Pide cuánto apostar a un signo."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="¿Cuánto?", placeholder="500, 2k, mitad, all…", max_length=20
    )

    def __init__(self, view: SportsView, match: Match, pick: Pick) -> None:
        odds = match.odds[pick]
        super().__init__(title=f"{pick.value} · {match.team(pick)} {odds_text(odds)}"[:45])
        self.panel = view
        self.match = match
        self.pick = pick
        self.odds = odds

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Hace la apuesta."""
        await self.panel.cog.place(
            interaction, self.match, self.pick, seen=self.odds, text=str(self.amount.value)
        )


class MatchSelect(discord.ui.Select):
    """Desplegable con los próximos partidos de la competición del panel."""

    def __init__(self, view: SportsView, matches: list[Match]) -> None:
        options = [
            discord.SelectOption(
                label=f"{kickoff_text(m.commence)} · {m.title}"[:100],
                value=m.event_id,
                description=odds_line(m)[:100],
                default=m.event_id == view.event_id,
            )
            for m in matches
        ]
        super().__init__(
            placeholder="Elige un partido…", options=options, row=1, custom_id="deportes:partido"
        )
        self.sports_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.sports_view.event_id = self.values[0]
        await self.sports_view.show(interaction)


class SportsView(discord.ui.View):
    """Panel de `deportes`: competición, partido y signo.

    Args:
        cog: El cog, que tiene los partidos en memoria y apuesta.
        owner_id: Quien abrió el panel (el único que lo maneja).
        sport: Competición que se enseña.
    """

    def __init__(self, cog: Deportes, *, owner_id: int, sport: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id
        self.sport = sport
        self.event_id: str | None = None
        self.message: discord.Message | discord.InteractionMessage | None = None
        self.rebuild()

    def selected(self) -> Match | None:
        """El partido elegido, si sigue sin empezar."""
        return next((m for m in self.cog.upcoming(self.sport) if m.event_id == self.event_id), None)

    def embed(self) -> discord.Embed:
        """El panel tal y como está."""
        return panel_embed(
            self.sport,
            self.cog.upcoming(self.sport),
            self.selected(),
            configured=self.cog.client is not None,
            odds_at=self.cog.odds_at.get(self.sport),
        )

    def rebuild(self) -> None:
        """Rehace los botones con la competición y el partido elegidos."""
        self.clear_items()
        for sport in SPORTS:
            button: discord.ui.Button = discord.ui.Button(
                label=sport.label,
                style=(
                    discord.ButtonStyle.primary
                    if sport.key == self.sport
                    else discord.ButtonStyle.secondary
                ),
                row=0,
                custom_id=f"deportes:liga:{sport.key}",
            )
            button.callback = self._sport_callback(sport.key)  # type: ignore[method-assign]
            self.add_item(button)
        matches = self.cog.upcoming(self.sport)[:MENU_SIZE]
        if matches:
            self.add_item(MatchSelect(self, matches))
        if (match := self.selected()) is not None:
            for pick in Pick:
                open_pick = pick in match.open_picks
                button = discord.ui.Button(
                    label=(
                        f"{pick.value} · {match.team(pick)} "
                        f"{odds_text(match.odds[pick]) if open_pick else '—'}"
                    )[:80],
                    style=discord.ButtonStyle.success,
                    row=2,
                    disabled=not open_pick,
                    custom_id=f"deportes:signo:{pick.value}",
                )
                button.callback = self._pick_callback(pick)  # type: ignore[method-assign]
                self.add_item(button)
        slip: discord.ui.Button = discord.ui.Button(
            label="🎟️ Mis apuestas",
            style=discord.ButtonStyle.secondary,
            row=3,
            custom_id="deportes:mis",
        )
        slip.callback = self._slip  # type: ignore[method-assign]
        self.add_item(slip)

    def _sport_callback(self, sport: str) -> Callable:
        async def callback(interaction: discord.Interaction) -> None:
            self.sport = sport
            self.event_id = None
            await self.show(interaction)

        return callback

    def _pick_callback(self, pick: Pick) -> Callable:
        async def callback(interaction: discord.Interaction) -> None:
            # Un formulario tiene que ser la primera respuesta: aquí solo se mira memoria.
            match = self.selected()
            if match is None or pick not in match.open_picks:
                await interaction.response.send_message(
                    "Ese partido ya ha empezado o ese signo ya no tiene cuota.", ephemeral=True
                )
                return
            await interaction.response.send_modal(StakeModal(self, match, pick))

        return callback

    async def _slip(self, interaction: discord.Interaction) -> None:
        await self.cog.slip(interaction)

    async def show(self, interaction: discord.Interaction) -> None:
        """Repinta el panel. Todo sale de memoria: se contesta directamente."""
        self.rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Este panel es de otra persona. Abre el tuyo con `/deportes`.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logger.debug("No se pudo quitar el panel de deportes al caducar")


# -- Cog ----------------------------------------------------------------------------------


class Deportes(commands.Cog):
    """Apuestas deportivas con cuotas fijas.

    Args:
        bot: Cliente.
        economy: Economía (el dinero de las apuestas).
        repository: Partidos, cuotas y resultados.
        api_key: Clave de The Odds API; vacía, no se pide nada.
        casino_channel_ids: Canales del casino (vacío = cualquiera).
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
        session: Sesión HTTP; inyectable en pruebas. Si no se da, se crea al cargar.
    """

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: SportsRepository,
        *,
        api_key: str = "",
        casino_channel_ids: frozenset[int] = frozenset(),
        clock: Callable[[], float] = time.time,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.api_key = api_key
        self.casino_channel_ids = casino_channel_ids
        self.clock = clock
        self._session = session
        self._own_session = session is None
        self.client: OddsClient | None = None
        #: Próximos partidos de cada competición y cuándo llegaron sus cuotas.
        self.matches: dict[str, list[Match]] = {}
        self.odds_at: dict[str, float] = {}
        self._settling = TimedLock("sports:Deportes._settling")
        self._pruned = 0.0

    async def cog_load(self) -> None:
        """Carga los partidos guardados, abre la conexión con la API y arranca el bucle."""
        now = self.clock()
        for sport in SPORTS:
            await self._reload(sport.key, now)
        if self.api_key:
            if self._session is None:
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
                self._session = aiohttp.ClientSession(timeout=timeout)
            self.client = OddsClient(self._session, self.api_key)
        self._feed.start()

    async def cog_unload(self) -> None:
        """Para el bucle y cierra la conexión."""
        self._feed.cancel()
        if self._own_session and self._session is not None:
            await self._session.close()

    async def _reload(self, sport: str, now: float) -> None:
        """Vuelve a leer de la base los próximos partidos de una competición."""
        self.matches[sport] = await self.repository.upcoming(sport, now=now)
        state = await self.repository.feed(sport)
        if state.odds_at is not None:
            self.odds_at[sport] = state.odds_at

    def upcoming(self, sport: str) -> list[Match]:
        """Los partidos de una competición que aún no han empezado (de memoria)."""
        now = self.clock()
        return [m for m in self.matches.get(sport, []) if m.commence > now]

    def find(self, event_id: str) -> Match | None:
        """Un partido que aún no ha empezado, por su id (de memoria)."""
        for sport in SPORTS:
            for match in self.upcoming(sport.key):
                if match.event_id == event_id:
                    return match
        return None

    # -- Apostar ---------------------------------------------------------------------

    async def place(
        self, interaction: discord.Interaction, match: Match, pick: Pick, *, seen: int, text: str
    ) -> None:
        """Cobra la apuesta del formulario a la cuota que se vio, si sigue igual."""
        # Cobrar va a la base de datos: se acepta antes.
        await ack(interaction)
        user = interaction.user
        guild_id = interaction.guild_id
        if guild_id is None or user.bot:
            await notify(interaction, "Solo se apuesta dentro de un servidor.")
            return
        now = self.clock()
        current = self.find(match.event_id)
        if current is None:
            await notify(interaction, "Ese partido ya ha empezado o ya no tiene cuotas.")
            return
        odds = current.odds.get(pick, 0)
        if odds < MIN_ODDS:
            await notify(interaction, "Ese signo ya no tiene cuota.")
            return
        if odds != seen:
            await notify(
                interaction,
                f"La cuota ha cambiado: el {pick_label(current.home, current.away, pick)} paga "
                f"ahora {odds_text(odds)} (antes, {odds_text(seen)}). Vuelve a pulsarlo si te "
                "sigue valiendo.",
            )
            return
        balance = await self.economy.balance(guild_id, user.id)
        try:
            stake = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if is_all_in(text):
            stake = min(stake, MAX_STAKE)
        if not MIN_STAKE <= stake <= MAX_STAKE:
            await notify(
                interaction,
                f"La apuesta va de {format_amount(MIN_STAKE)} a {format_amount(MAX_STAKE)} "
                "por partido.",
            )
            return
        ticket = SportsBet(
            id=0,
            guild_id=guild_id,
            user_id=user.id,
            channel_id=interaction.channel_id or 0,
            event_id=current.event_id,
            sport=current.sport,
            home=current.home,
            away=current.away,
            commence=current.commence,
            pick=pick.value,
            odds=odds,
            stake=stake,
            created_at=now,
        )
        try:
            bet, balance = await self.economy.sports_bet(ticket, game=GAME)
        except SportsBetTakenError:
            await notify(interaction, "Ya tienes una apuesta a este partido: una por partido.")
            return
        except InsufficientFundsError as error:
            await notify(interaction, f"No te llega: tienes {format_amount(error.balance)}.")
            return
        lines = [
            f"🎟️ Apuestas **{format_amount(stake)}** al "
            f"**{pick_label(bet.home, bet.away, bet.pick)}** en **{current.title}** a "
            f"{odds_text(odds)}.",
            f"Si aciertas, cobras **{format_amount(payout(stake, odds))}**. Empieza "
            f"<t:{int(bet.commence)}:R> y el resultado sale en este canal.",
            f"{CURRENCY_EMOJI} Te quedan **{format_amount(balance)}**",
            "-# 🐶 Lo que ganes cuenta en el casino del día en que acaba el partido, con la "
            "retención del IRPF del juego.",
        ]
        # Apostar es un resultado con dinero: la mascota activa puede opinar.
        if hint := await renta.hint(self.bot, guild_id, user.id, Moment()):
            lines.append(hint)
        await notify(interaction, "\n".join(lines))
        # Apostar es gastar: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)
        await logros.track(
            self.bot,
            guild_id,
            user,
            interaction.channel,
            sports_bet_stats(
                stake=stake,
                odds=odds,
                pick=pick.value,
                sport=current.sport,
                balance_before=balance + stake,
                lead=current.commence - now,
                when=datetime.fromtimestamp(now, TIMEZONE),
            ),
        )

    async def slip(self, interaction: discord.Interaction) -> None:
        """🎟️ Mis apuestas, en un mensaje privado aparte."""
        await ack(interaction, new_message=True)
        guild_id = interaction.guild_id or 0
        bets = await self.economy.sports_bets(guild_id, interaction.user.id, limit=SLIP_SIZE)
        name = discord.utils.escape_markdown(interaction.user.display_name)
        await edit(interaction, embed=slip_embed(bets, name))
        await logros.track(
            self.bot, guild_id, interaction.user, interaction.channel, sports_slip_stats()
        )

    # -- Cuotas y resultados ---------------------------------------------------------

    @tasks.loop(minutes=5)
    async def _feed(self) -> None:
        try:
            await self.tick()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error con las apuestas deportivas")

    @_feed.before_loop
    async def _before_feed(self) -> None:
        await self.bot.wait_until_ready()

    async def tick(self) -> None:
        """Pide a la API lo que toque y resuelve lo que ya tiene resultado."""
        now = self.clock()
        if self.client is not None:
            bets = await self.economy.open_sports_bets()
            states = {sport.key: await self.repository.feed(sport.key) for sport in SPORTS}
            # Los créditos son de la cuenta, no de la competición: vale la última respuesta.
            latest = max(states.values(), key=lambda s: max(s.odds_at or 0.0, s.scores_at or 0.0))
            remaining = latest.remaining
            for sport in SPORTS:
                state = states[sport.key]
                kickoffs = [m.commence for m in self.upcoming(sport.key)]
                if odds_due(
                    now=now,
                    fetched_at=state.odds_at,
                    next_kickoff=min(kickoffs, default=None),
                    remaining=remaining,
                ):
                    remaining = await self._fetch_odds(sport.key, now, remaining)
                waiting = [
                    b.commence
                    for b in bets
                    if b.sport == sport.key and b.commence + RESULT_DELAY <= now
                ]
                oldest = min(waiting, default=None)
                if oldest is not None and scores_due(
                    now=now, checked_at=state.scores_at, oldest=oldest
                ):
                    remaining = await self._fetch_scores(
                        sport.key, now, days_from(now=now, oldest=oldest), remaining
                    )
        await self.settle(now)
        if now - self._pruned >= 86_400:
            self._pruned = now
            await self.repository.prune(before=now - PRUNE_AFTER)

    async def _fetch_odds(self, sport: str, now: float, remaining: int | None) -> int | None:
        """Pide y guarda las cuotas de una competición. Devuelve los créditos que quedan."""
        assert self.client is not None
        try:
            reply = await self.client.odds(sport, now=now)
        except FeedError as error:
            logger.warning("Cuotas de %s: %s", sport, error)
            await self.repository.mark_checked(sport, "odds_at", now=now)
            return remaining
        await self.repository.save_odds(
            sport, parse_odds(reply.events, now=now), now=now, remaining=reply.remaining
        )
        await self._reload(sport, now)
        return reply.remaining if reply.remaining is not None else remaining

    async def _fetch_scores(
        self, sport: str, now: float, days: int, remaining: int | None
    ) -> int | None:
        """Pide y guarda los resultados de una competición. Devuelve los créditos que quedan."""
        assert self.client is not None
        try:
            reply = await self.client.scores(sport, days=days)
        except FeedError as error:
            logger.warning("Resultados de %s: %s", sport, error)
            await self.repository.mark_checked(sport, "scores_at", now=now)
            return remaining
        await self.repository.save_scores(
            sport, parse_scores(reply.events), now=now, remaining=reply.remaining
        )
        return reply.remaining if reply.remaining is not None else remaining

    async def settle(self, now: float) -> list[Resolved]:
        """Resuelve las apuestas con resultado y anula las que ya no lo tendrán.

        Returns:
            Lo resuelto en esta vuelta (también se anuncia y se cuenta).
        """
        async with self._settling:
            bets = [b for b in await self.economy.open_sports_bets() if b.commence <= now]
            if not bets:
                return []
            scores = await self.repository.scores(sorted({b.event_id for b in bets}))
            resolved = []
            for bet in bets:
                score = scores.get(bet.event_id)
                if score is None and now < bet.commence + VOID_AFTER:
                    continue
                if (done := await self._close(bet, score)) is not None:
                    resolved.append(done)
        groups: dict[tuple[int, int, str], list[Resolved]] = {}
        for item in resolved:
            key = (item.bet.guild_id, item.bet.channel_id, item.bet.event_id)
            groups.setdefault(key, []).append(item)
        for group in groups.values():
            await self._announce(group)
            await self._count(group)
        return resolved

    async def _close(self, bet: SportsBet, score: Score | None) -> Resolved | None:
        """Paga o anula una apuesta; `None` si otro ya la había cerrado."""
        if score is not None:
            prize = payout(bet.stake, bet.odds) if score.pick == bet.pick else 0
            try:
                done = await self.economy.settle_sports_bet(
                    bet.guild_id, bet.id, payout=prize, game=GAME
                )
            except BalanceLimitError:
                logger.warning("La apuesta %s pasaría el saldo máximo; se anula.", bet.id)
                score = None
            else:
                if done is None:
                    return None
                closed, settlement = done
                return Resolved(closed, score, settlement, settlement.balance)
        voided = await self.economy.void_sports_bet(bet.guild_id, bet.id, game=GAME)
        if voided is None:
            return None
        closed, balance = voided
        return Resolved(closed, None, None, balance)

    async def _announce(self, group: list[Resolved]) -> None:
        """El resultado en el canal donde se apostó; menciona a quien cobra o recupera."""
        channel = self.bot.get_channel(group[0].bet.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        pinged = [r.bet.user_id for r in group if r.bet.payout > 0][:RESULT_LINES]
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=u) for u in pinged]
        )
        try:
            await channel.send(result_message(group), allowed_mentions=mentions)
        except discord.HTTPException:
            logger.warning("No se pudo anunciar el resultado de %s", group[0].bet.event_id)

    async def _count(self, group: list[Resolved]) -> None:
        """Logros y estadísticas del casino de cada apuesta resuelta."""
        for item in group:
            bet = item.bet
            guild = self.bot.get_guild(bet.guild_id)
            member = guild.get_member(bet.user_id) if guild is not None else None
            if member is None:
                continue
            channel = self.bot.get_channel(bet.channel_id)
            if item.score is None or item.settlement is None:
                await logros.track(self.bot, bet.guild_id, member, channel, sports_void_stats())
                continue
            net = bet.payout - bet.stake
            streak = 0
            if bet.status == "won":
                recent = await self.economy.sports_bets(bet.guild_id, bet.user_id, limit=SLIP_SIZE)
                streak = win_streak(recent)
            delta = sports_result_stats(
                stake=bet.stake,
                odds=bet.odds,
                pick=bet.pick,
                payout=bet.payout,
                home=item.score.home,
                away=item.score.away,
                streak=streak,
            )
            delta.merge(
                casino_stats(
                    stake=bet.stake,
                    net=net,
                    balance_after=item.balance,
                    tax_delta=item.settlement.tax_delta,
                )
            )
            await logros.casino_play(self.bot, bet.guild_id, member, channel, delta, net=net)
            await apuestas.record(
                self.bot,
                bet.guild_id,
                member,
                game=GAME,
                stake=bet.stake,
                net=net,
                balance_after=item.balance,
                tax=item.settlement.tax_delta,
            )

    # -- Comandos --------------------------------------------------------------------

    def _view(self, owner_id: int) -> SportsView:
        """Un panel nuevo, en la primera competición con partidos."""
        sport = next((s.key for s in SPORTS if self.upcoming(s.key)), SPORTS[0].key)
        return SportsView(self, owner_id=owner_id, sport=sport)

    @app_commands.command(
        name="deportes", description="Apuesta a partidos de LaLiga y la Champions con cuotas."
    )
    @app_commands.guild_only()
    async def deportes(self, interaction: discord.Interaction) -> None:
        """Abre el panel de apuestas deportivas (efímero). Sin permisos especiales."""
        if error := casino_channel_error(
            self.casino_channel_ids, interaction.channel, "Las apuestas deportivas"
        ):
            await interaction.response.send_message(error, ephemeral=True)
            return
        view = self._view(interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @commands.command(name="deportes")
    @commands.guild_only()
    async def deportes_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.deportes`): el panel se ve en el canal."""
        if error := casino_channel_error(
            self.casino_channel_ids, ctx.channel, "Las apuestas deportivas"
        ):
            await ctx.send(error)
            return
        view = self._view(ctx.author.id)
        view.message = await ctx.send(embed=view.embed(), view=view)


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Las apuestas deportivas de alguien que esperan resultado (para `saldo`), o `[]`.

    Nunca lanza: si algo falla se registra y `saldo` sale sin estas líneas.
    """
    cog = find_cog(bot, Deportes)
    if cog is None:
        return []
    try:
        bets = await cog.economy.sports_bets(guild_id, user_id, limit=SLIP_SIZE)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudieron leer las apuestas deportivas de %s", user_id)
        return []
    return [
        Pending(
            Side.HELD,
            "sports_bet",
            "⚽",
            f"{format_amount(bet.stake)} al {pick_label(bet.home, bet.away, bet.pick)} en "
            f"{bet.home} – {bet.away} (cobrarías {format_amount(payout(bet.stake, bet.odds))})",
            bet.stake,
            bet.commence,
        )
        for bet in bets
        if bet.status == "open"
    ]


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, los partidos y la clave de la API del bot."""
    await bot.add_cog(
        Deportes(
            bot,
            bot.economy,
            bot.sports,
            api_key=bot.odds_api_key,
            casino_channel_ids=bot.casino_channel_ids,
        )
    )
