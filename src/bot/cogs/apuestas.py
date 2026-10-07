"""Estadísticas del casino: el comando `apuestas` y el apunte de cada jugada.

`/apuestas [miembro]` (y `.apuestas`) abre un panel con siete páginas que se
eligen en un desplegable:

- 📊 **Resumen:** jugadas, dinero apostado y devuelto, apuesta media, mediana y
  máxima, porcentaje de victorias, RTP real, ventaja de la casa, all-in,
  rachas de días y la distribución de las apuestas por tramos.
- 🎲 **Por juego:** lo mismo para cada juego y una gráfica del RTP real.
- 🏆 **Récords:** mayores premios, mayores palos, mejores multiplicadores y
  mayores apuestas.
- 🕐 **Horarios:** jugadas y resultado por hora, por día de la semana y en los
  últimos 14 días.
- 🏅 **Ranking:** quién apuesta más, quién gana, quién financia el casino,
  mejor RTP, más all-in y más IRPF.
- 🏛️ **Hacienda:** lo retenido, lo devuelto en el día y en la renta, y cómo
  funciona la retención del casino.
- 📒 **Libro:** el dinero de cada juego según el libro de la economía, desde
  el primer día (antes de que existieran las estadísticas por jugada).

Con los botones se cambia el periodo (hoy, 7 días, 30 días, siempre) y el
ámbito (el miembro o el servidor entero). Solo quien abre el panel lo maneja.
Con `/apuestas` el panel es efímero; con `.apuestas` se ve en el canal.

Apuntar jugadas: cada juego del casino llama a `record` (función puente de
este módulo) al terminar una jugada, junto a `achievements.casino_play`. No
mueve dinero ni lanza: si falla, se registra en el log y el juego sigue. La
misma llamada cuenta la jugada para las porras (`bot.cogs.porras`).

Logros: categoría 📊 Estadísticas del grupo 🎰 Casino (`apuestas_stats`).
No necesita permisos especiales ni intents.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.repositories.casino_stats import CasinoStatsRepository
from bot.services.achievements import apuestas_stats
from bot.services.casino_stats import (
    GAMES,
    MIN_PLAYS_FOR_RATES,
    REFERENCE_STAKE,
    STAKE_BUCKETS,
    TOP,
    WEEKDAYS,
    Highlight,
    Period,
    Play,
    PlayerRow,
    Report,
    Totals,
    bar,
    day_streaks,
    game_label,
    ledger_from_rows,
    money,
    mult,
    number,
    pct,
    period_start,
    sanxe_line,
    signed,
    verdict,
    with_ledger,
)
from bot.services.economy import EconomyService, day_bounds, format_amount
from bot.services.levels import TIMEZONE, local_day
from bot.services.taxes import MAX_PENDING_DECLARATIONS, TAX_COLLECTOR
from bot.utils.cogs import find_cog

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(212, 175, 55)
#: Segundos que el panel acepta botones.
VIEW_TIMEOUT = 300

#: Páginas del panel: clave → (emoji, nombre). La clave es estable (logros).
PAGES: dict[str, tuple[str, str]] = {
    "resumen": ("📊", "Resumen"),
    "juegos": ("🎲", "Por juego"),
    "records": ("🏆", "Récords"),
    "horario": ("🕐", "Horarios"),
    "ranking": ("🏅", "Ranking"),
    "hacienda": ("🏛️", "Hacienda"),
    "libro": ("📒", "Libro (desde el día uno)"),
}


# -- Formato ------------------------------------------------------------------------------


def _date(epoch: float | None) -> str:
    if epoch is None:
        return "—"
    return datetime.fromtimestamp(epoch, TIMEZONE).strftime("%d/%m/%Y")


def _short_date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, TIMEZONE).strftime("%d/%m")


def _decimal(value: float) -> str:
    """`12.345` → `12,3` (un decimal, coma española)."""
    return f"{value:.1f}".replace(".", ",")


def _code(lines: list[str]) -> str:
    return "```\n" + "\n".join(lines) + "\n```"


def _who(user_id: int, names: dict[int, str]) -> str:
    return names.get(user_id) or f"<@{user_id}>"


def _highlight_line(rank: int, item: Highlight, names: dict[int, str] | None, value: str) -> str:
    who = f"{_who(item.user_id, names)} · " if names is not None else ""
    emoji = GAMES.get(item.game, ("🎲", ""))[0]
    return f"`{rank}.` {who}{emoji} {value} · {_short_date(item.created_at)}"


def _ranking_lines(
    rows: list[PlayerRow], names: dict[int, str], value: Callable[[PlayerRow], str]
) -> str:
    if not rows:
        return "Nadie todavía."
    return "\n".join(
        f"`{i}.` {_who(row.user_id, names)} · {value(row)}" for i, row in enumerate(rows, 1)
    )


def _empty_text(scope: str) -> str:
    return (
        f"{scope} no tiene jugadas apuntadas en este periodo. Las estadísticas por "
        "jugada empezaron a contarse con este comando; el dinero de antes está en 📒 Libro."
    )


class Panel:
    """Construye los embeds de cada página a partir de un `Report`.

    Args:
        report: Las cifras.
        scope: Nombre visible del ámbito (el miembro o el servidor).
        member_scope: Si el ámbito es un miembro (sin ranking ni nombres en récords).
        period: Periodo que se enseña.
        names: Nombres visibles de los miembros que salen en récords y ranking.
        today: Día de hoy (hora canaria), para las rachas.
    """

    def __init__(
        self,
        report: Report,
        *,
        scope: str,
        member_scope: bool,
        period: Period,
        names: dict[int, str],
        today: datetime,
    ) -> None:
        self.report = report
        self.scope = scope
        self.member_scope = member_scope
        self.period = period
        self.names = names
        self.today = today

    def embed(self, page: str) -> discord.Embed:
        """Embed de `page` (una clave de `PAGES`)."""
        emoji, name = PAGES[page]
        title = f"{emoji} Casino · {name}"
        embed = discord.Embed(title=title, color=COLOR)
        builder = getattr(self, f"_{page}")
        builder(embed)
        footer = f"{self.scope} · {self.period.label}"
        if page != "libro" and self.report.recorded_since is not None:
            footer += f" · jugadas apuntadas desde el {_date(self.report.recorded_since)}"
        embed.set_footer(text=footer)
        return embed

    # -- Páginas ----------------------------------------------------------------------------

    def _resumen(self, embed: discord.Embed) -> None:
        report = self.report
        if report.empty:
            embed.description = _empty_text(self.scope)
            return
        t = report.total
        embed.description = f"*{verdict(t)}*"
        days = len(report.days)
        plays_line = [f"**{number(t.plays)}** apostadas · {number(t.free_plays)} gratis"]
        if days:
            plays_line.append(f"{number(days)} días con juego · {_decimal(t.plays / days)} al día")
        if not self.member_scope:
            plays_line.append(f"{number(report.players)} jugadores distintos")
        if report.favorite:
            plays_line.append(f"Favorito: {game_label(report.favorite)}")
        embed.add_field(name="🎲 Jugadas", value="\n".join(plays_line), inline=True)
        embed.add_field(
            name="💸 Dinero",
            value=(
                f"Apostado **{format_amount(t.wagered)}**\n"
                f"Devuelto {format_amount(t.paid)}\n"
                f"Resultado **{signed(t.net)}**\n"
                f"Tras {TAX_COLLECTOR}: {signed(t.after_tax)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="📏 Apuesta",
            value=(
                f"Media **{money(t.average_stake)}**\n"
                f"Mediana {money(report.median_stake)}\n"
                f"Máxima {format_amount(t.max_stake)}\n"
                f"= {_decimal(t.average_stake / REFERENCE_STAKE)} tiradas de {REFERENCE_STAKE}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🎯 Acierto",
            value=(
                f"Ganadas {number(t.wins)} ({pct(t.win_rate)})\n"
                f"Empates {number(t.pushes)}\n"
                f"Perdidas {number(t.losses)}\n"
                f"Perdidas enteras {number(t.busts)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="📈 Rendimiento",
            value=(
                f"RTP real **{pct(t.rtp, 2)}**\n"
                f"Ventaja de la casa {pct(t.house_edge, 2)}\n"
                f"Por jugada {signed(t.average_net)}\n"
                f"Premio medio +{money(t.average_win)}\n"
                f"Palo medio −{money(t.average_loss)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔥 Riesgo",
            value=(
                f"All-in {number(t.all_ins)} · ganados {number(t.all_in_wins)} "
                f"({pct(t.all_in_wins / t.all_ins if t.all_ins else None, 0)})\n"
                f"A cero {number(t.broke)} veces\n"
                f"Mayor premio +{format_amount(t.best)}\n"
                f"Mayor palo {money(t.worst)}\n"
                f"Mejor multiplicador {mult(t.max_mult)}"
            ),
            inline=True,
        )
        best_streak, current = day_streaks(report.days, self.today.date())
        embed.add_field(
            name="📅 Constancia",
            value=(
                f"Racha más larga: {best_streak} días seguidos\n"
                f"Racha actual: {current} días\n"
                f"Primera jugada del periodo: {_date(report.first_at)}"
            ),
            inline=False,
        )
        top = max(report.buckets) if report.buckets else 0
        total = sum(report.buckets) or 1
        lines = [
            f"{label:>13} {bar(count, top)} {number(count):>7} {_decimal(count / total * 100):>5}%"
            for (_, label), count in zip(STAKE_BUCKETS, report.buckets, strict=True)
        ]
        embed.add_field(name="🧮 Tamaño de las apuestas", value=_code(lines), inline=False)

    def _juegos(self, embed: discord.Embed) -> None:
        report = self.report
        if report.empty:
            embed.description = _empty_text(self.scope)
            return
        ordered = sorted(
            report.by_game.items(), key=lambda kv: kv[1].plays + kv[1].free_plays, reverse=True
        )
        rtps = [t.rtp or 0.0 for _, t in ordered]
        top = max(max(rtps, default=0.0), 1.0)
        lines = [
            f"{GAMES.get(game, ('', game))[1]:<11} {bar(t.rtp or 0.0, top, 10)} {pct(t.rtp, 1):>8}"
            for game, t in ordered
        ]
        embed.description = (
            "RTP real de cada juego (lo devuelto por cada 100 apostados):\n" + _code(lines)
        )
        for game, t in ordered:
            free = f" + {number(t.free_plays)} gratis" if t.free_plays else ""
            embed.add_field(
                name=game_label(game),
                value=(
                    f"{number(t.plays)} jugadas{free}\n"
                    f"Apostado {format_amount(t.wagered)}\n"
                    f"Media {money(t.average_stake)} · máx. {format_amount(t.max_stake)}\n"
                    f"Ganadas {pct(t.win_rate)} · RTP {pct(t.rtp)}\n"
                    f"Resultado {signed(t.net)}\n"
                    f"Mejor +{format_amount(t.best)} · {mult(t.max_mult)}"
                ),
                inline=True,
            )

    def _records(self, embed: discord.Embed) -> None:
        report = self.report
        if report.empty:
            embed.description = _empty_text(self.scope)
            return
        names = None if self.member_scope else self.names

        def block(items: tuple[Highlight, ...], value: Callable[[Highlight], str]) -> str:
            if not items:
                return "Nada todavía."
            return "\n".join(
                _highlight_line(i, item, names, value(item)) for i, item in enumerate(items, 1)
            )

        embed.add_field(
            name="💰 Mayores premios",
            value=block(report.top_wins, lambda h: f"**{signed(h.net)}** ({mult(h.mult)})"),
            inline=False,
        )
        embed.add_field(
            name="💀 Mayores palos",
            value=block(report.top_losses, lambda h: f"**{signed(h.net)}**"),
            inline=False,
        )
        embed.add_field(
            name="🚀 Mejores multiplicadores",
            value=block(
                report.top_mults,
                lambda h: f"**{mult(h.mult)}** con {format_amount(h.stake)}",
            ),
            inline=False,
        )
        embed.add_field(
            name="🐋 Mayores apuestas",
            value=block(report.top_stakes, lambda h: f"**{format_amount(h.stake)}**"),
            inline=False,
        )

    def _horario(self, embed: discord.Embed) -> None:
        report = self.report
        if report.empty:
            embed.description = _empty_text(self.scope)
            return
        top = max(count for count, _ in report.by_hour) or 1
        hours = [
            f"{hour:02d}h {bar(count, top, 10)} {number(count):>6} {signed(net):>14}"
            for hour, (count, net) in enumerate(report.by_hour)
        ]
        embed.add_field(name="🕐 Por hora (jugadas · resultado)", value=_code(hours), inline=False)
        top = max(count for count, _ in report.by_weekday) or 1
        days = [
            f"{WEEKDAYS[i][:3]} {bar(count, top, 10)} {number(count):>6} {signed(net):>14}"
            for i, (count, net) in enumerate(report.by_weekday)
        ]
        embed.add_field(name="📆 Por día de la semana", value=_code(days), inline=False)
        top = max(count for _, count, _ in report.daily) or 1
        daily = [
            f"{day[8:10]}/{day[5:7]} {bar(count, top, 10)} {number(count):>6} {signed(net):>14}"
            for day, count, net in report.daily
        ]
        embed.add_field(
            name="🗓️ Últimos 14 días (no depende del periodo)", value=_code(daily), inline=False
        )
        busiest = max(range(24), key=lambda h: report.by_hour[h][0])
        worst = min(range(24), key=lambda h: report.by_hour[h][1])
        best = max(range(24), key=lambda h: report.by_hour[h][1])
        parts = [f"Hora con más jugadas: **{busiest:02d}h**."]
        if report.by_hour[worst][1] < 0:
            parts.append(
                f"Hora más ruinosa: **{worst:02d}h** ({signed(report.by_hour[worst][1])})."
            )
        if report.by_hour[best][1] > 0:
            parts.append(f"Hora más rentable: **{best:02d}h** ({signed(report.by_hour[best][1])}).")
        embed.description = " ".join(parts)

    def _ranking(self, embed: discord.Embed) -> None:
        report = self.report
        rows = list(report.ranking)
        if not rows:
            embed.description = _empty_text(self.scope)
            return
        names = self.names

        def top(key: Callable[[PlayerRow], float], pool: list[PlayerRow] | None = None):
            return sorted(pool if pool is not None else rows, key=key, reverse=True)[:TOP]

        embed.add_field(
            name="🐋 Más apostado",
            value=_ranking_lines(
                top(lambda r: r.wagered), names, lambda r: format_amount(r.wagered)
            ),
            inline=False,
        )
        embed.add_field(
            name="📈 Más ganadores",
            value=_ranking_lines(
                [r for r in top(lambda r: r.net) if r.net > 0], names, lambda r: signed(r.net)
            ),
            inline=False,
        )
        embed.add_field(
            name="🏦 Mejores clientes de la casa",
            value=_ranking_lines(
                [r for r in top(lambda r: -r.net) if r.net < 0], names, lambda r: signed(r.net)
            ),
            inline=False,
        )
        embed.add_field(
            name="🎲 Más jugadas",
            value=_ranking_lines(top(lambda r: r.plays), names, lambda r: number(r.plays)),
            inline=False,
        )
        eligible = [r for r in rows if r.plays >= MIN_PLAYS_FOR_RATES]
        embed.add_field(
            name=f"🍀 Mejor RTP (mín. {MIN_PLAYS_FOR_RATES} jugadas)",
            value=_ranking_lines(
                top(lambda r: r.rtp or 0.0, eligible), names, lambda r: pct(r.rtp, 2)
            ),
            inline=False,
        )
        embed.add_field(
            name="💥 Mayor premio de una vez",
            value=_ranking_lines(
                [r for r in top(lambda r: r.best) if r.best > 0],
                names,
                lambda r: "+" + format_amount(r.best),
            ),
            inline=False,
        )
        embed.add_field(
            name="🧨 Más all-in",
            value=_ranking_lines(
                [r for r in top(lambda r: r.all_ins) if r.all_ins],
                names,
                lambda r: f"{number(r.all_ins)} (a cero {number(r.broke)} veces)",
            ),
            inline=False,
        )
        embed.add_field(
            name=f"🐶 Más IRPF del juego para {TAX_COLLECTOR}",
            value=_ranking_lines(
                [r for r in top(lambda r: r.withheld - r.refunded) if r.withheld > r.refunded],
                names,
                lambda r: format_amount(r.withheld - r.refunded),
            ),
            inline=False,
        )

    def _hacienda(self, embed: discord.Embed) -> None:
        t = self.report.total
        ledger = self.report.ledger
        embed.description = (
            "**Cómo te cobra Hacienda el casino**\n"
            "1. Cada día se mira tu resultado neto del casino (premios menos apuestas). "
            "Si vas arriba, se retiene el IRPF que toca a esa ganancia, al momento.\n"
            "2. Si después pierdes ese mismo día, la retención se recalcula y te devuelve "
            "lo que sobra en la misma jugada.\n"
            "3. El lunes sale la renta de la semana: se suman todos los días, las pérdidas "
            "de unos compensan las ganancias de otros y te devuelve lo retenido de más. "
            "Hay que presentarla con `renta` (se guardan las "
            f"{MAX_PENDING_DECLARATIONS} últimas semanas).\n"
            "Si la semana acaba en positivo, lo que corresponde a esa ganancia no vuelve: "
            "es el impuesto de verdad, no un adelanto."
        )
        embed.add_field(
            name=f"🧾 En este periodo ({self.period.label.lower()})",
            value=(
                f"Retenido {format_amount(t.withheld)}\n"
                f"Devuelto en el día {format_amount(t.refunded)}\n"
                f"Neto {money(t.withheld - t.refunded)}\n"
                f"Tipo efectivo sobre lo ganado {pct(t.effective_tax)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="📒 Desde el primer día",
            value=(
                f"Retenido {format_amount(ledger.withheld)}\n"
                f"Devuelto en el día {format_amount(ledger.refunded_day)}\n"
                f"Devuelto en la renta {format_amount(ledger.refunded_renta)}\n"
                f"Se queda {TAX_COLLECTOR} **{money(ledger.sanxe_net)}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Balance de Hacienda",
            value=sanxe_line(ledger.withheld, ledger.refunded_day + ledger.refunded_renta),
            inline=False,
        )

    def _libro(self, embed: discord.Embed) -> None:
        ledger = self.report.ledger
        if not ledger.by_game:
            embed.description = f"{self.scope} no tiene ni una apuesta en el libro."
            return
        embed.description = (
            "Lo que dice el libro de la economía desde la primera apuesta "
            f"({_date(ledger.first_at)}). No depende del periodo. Un doble del blackjack "
            "cuenta como otra apuesta."
        )
        ordered = sorted(ledger.by_game.items(), key=lambda kv: kv[1][1], reverse=True)
        for game, (bets, wagered, paid) in ordered:
            rtp = paid / wagered if wagered else None
            embed.add_field(
                name=game_label(game),
                value=(
                    f"{number(bets)} apuestas\n"
                    f"Apostado {format_amount(wagered)}\n"
                    f"Pagado {format_amount(paid)}\n"
                    f"RTP {pct(rtp)} · {signed(paid - wagered)}"
                ),
                inline=True,
            )
        total_rtp = ledger.paid / ledger.wagered if ledger.wagered else None
        embed.add_field(
            name="🧮 Total",
            value=(
                f"{number(ledger.bets)} apuestas · media "
                f"{money(ledger.wagered / ledger.bets if ledger.bets else 0)}\n"
                f"Apostado {format_amount(ledger.wagered)} · pagado {format_amount(ledger.paid)}\n"
                f"RTP {pct(total_rtp, 2)} · resultado {signed(ledger.paid - ledger.wagered)}"
            ),
            inline=False,
        )


# -- Panel interactivo --------------------------------------------------------------------


class PageSelect(discord.ui.Select):
    """Desplegable con las páginas."""

    def __init__(self, view: StatsView) -> None:
        options = [
            discord.SelectOption(label=name, value=key, emoji=emoji, default=key == view.page)
            for key, (emoji, name) in PAGES.items()
        ]
        super().__init__(placeholder="Elige qué mirar", options=options, row=0)
        self.stats_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.stats_view.show(interaction, page=self.values[0])


class StatsView(discord.ui.View):
    """Panel de `apuestas`: página, periodo y ámbito.

    Args:
        cog: El cog, para volver a leer las cifras.
        owner: Quien abrió el panel (el único que lo maneja).
        guild: Servidor.
        member: Miembro cuyas cifras se miran.
    """

    def __init__(
        self,
        cog: Apuestas,
        *,
        owner: discord.abc.User,
        guild: discord.Guild,
        member: discord.Member | discord.User,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner = owner
        self.guild = guild
        self.member = member
        self.page = "resumen"
        self.period = Period.ALL
        self.server = False
        self.message: discord.Message | None = None
        self._build()

    def _build(self) -> None:
        self.clear_items()
        self.add_item(PageSelect(self))
        for period in Period:
            button = discord.ui.Button(
                label=period.label,
                style=(
                    discord.ButtonStyle.primary
                    if period is self.period
                    else discord.ButtonStyle.secondary
                ),
                row=1,
            )
            button.callback = self._period_callback(period)
            self.add_item(button)
        scope = discord.ui.Button(
            label=(
                f"👤 Ver a {self.member.display_name}"[:80] if self.server else "🌍 Ver el servidor"
            ),
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=self.page == "ranking",
        )
        scope.callback = self._toggle_scope
        self.add_item(scope)

    def _period_callback(self, period: Period) -> Callable:
        async def callback(interaction: discord.Interaction) -> None:
            await self.show(interaction, period=period)

        return callback

    async def _toggle_scope(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, server=not self.server)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            "Este panel es de otra persona. Abre el tuyo con `/apuestas`.", ephemeral=True
        )
        return False

    @property
    def server_scope(self) -> bool:
        """Si se enseña el servidor (el ranking siempre es del servidor)."""
        return self.server or self.page == "ranking"

    async def render(self) -> discord.Embed:
        """Embed de la página actual con las cifras recién leídas."""
        report = await self.cog.report(
            self.guild.id, None if self.server_scope else self.member.id, self.period
        )
        if self.server_scope:
            scope = self.guild.name
        else:
            scope = self.member.display_name
        panel = Panel(
            report,
            scope=scope,
            member_scope=not self.server_scope,
            period=self.period,
            names=self.cog.names(self.guild, report),
            today=datetime.now(TIMEZONE),
        )
        return panel.embed(self.page)

    async def show(
        self,
        interaction: discord.Interaction,
        *,
        page: str | None = None,
        period: Period | None = None,
        server: bool | None = None,
    ) -> None:
        """Cambia lo pedido y repinta el panel."""
        if page is not None:
            self.page = page
        if period is not None:
            self.period = period
        if server is not None:
            self.server = server
        self._build()
        embed = await self.render()
        await interaction.response.edit_message(embed=embed, view=self)
        await logros.track(
            self.cog.bot,
            self.guild.id,
            interaction.user,
            interaction.channel,
            apuestas_stats(
                page=self.page,
                period=period.key if period is not None else None,
                opened=False,
                own=False,
                snooping=False,
                plays=0,
                rtp=None,
                net=0,
                when=datetime.now(TIMEZONE),
            ),
        )

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logger.debug("No se pudo quitar el panel de apuestas al caducar")


# -- Cog ----------------------------------------------------------------------------------


class Apuestas(commands.Cog):
    """Estadísticas del casino.

    Args:
        bot: Cliente.
        repository: Jugadas apuntadas.
        economy: Economía, para leer el libro desde el primer día.
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
    """

    def __init__(
        self,
        bot: commands.Bot,
        repository: CasinoStatsRepository,
        economy: EconomyService,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.economy = economy
        self._clock = clock

    async def record_play(self, guild_id: int, user_id: int, play: Play) -> None:
        """Apunta una jugada terminada (ver la función puente `record`)."""
        await self.repository.record(guild_id, user_id, play, now=self._clock())

    async def report(self, guild_id: int, user_id: int | None, period: Period) -> Report:
        """Cifras de un ámbito y un periodo, con el libro de la economía."""
        now = self._clock()
        today = local_day(now)
        since = period_start(period, day_bounds(today)[0])
        report = await self.repository.report(guild_id, user_id, since=since, today=today)
        rows, withheld, refunded_day, refunded_renta, first_at = await self.economy.casino_ledger(
            guild_id, user_id
        )
        ledger = ledger_from_rows(
            rows,
            withheld=withheld,
            refunded_day=refunded_day,
            refunded_renta=refunded_renta,
            first_at=first_at,
        )
        return with_ledger(report, ledger)

    @staticmethod
    def names(guild: discord.Guild, report: Report) -> dict[int, str]:
        """Nombres visibles de quienes salen en récords y ranking (sin consultar a Discord)."""
        ids = {row.user_id for row in report.ranking}
        for items in (report.top_wins, report.top_losses, report.top_mults, report.top_stakes):
            ids.update(item.user_id for item in items)
        out = {}
        for user_id in ids:
            member = guild.get_member(user_id)
            if member is not None:
                out[user_id] = discord.utils.escape_markdown(member.display_name)
        return out

    async def _open(
        self,
        guild: discord.Guild,
        owner: discord.abc.User,
        member: discord.Member | discord.User,
        channel: object,
    ) -> tuple[discord.Embed, StatsView]:
        view = StatsView(self, owner=owner, guild=guild, member=member)
        embed = await view.render()
        own = member.id == owner.id
        totals = Totals()
        if own:
            # Los secretos miran tus cifras de siempre, sea cual sea el periodo.
            mine = await self.repository.report(
                guild.id, owner.id, since=None, today=local_day(self._clock())
            )
            totals = mine.total
        await logros.track(
            self.bot,
            guild.id,
            owner,
            channel,
            apuestas_stats(
                page="resumen",
                period=Period.ALL.key,
                opened=True,
                own=own,
                snooping=not own,
                plays=totals.plays,
                rtp=totals.rtp,
                net=totals.net,
                when=datetime.now(TIMEZONE),
            ),
        )
        return embed, view

    @app_commands.command(
        name="apuestas",
        description="Estadísticas del casino: las tuyas, las de alguien o las del servidor.",
    )
    @app_commands.describe(miembro="De quién mirar las cifras (por defecto, las tuyas).")
    @app_commands.guild_only()
    async def apuestas(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Abre el panel de estadísticas (efímero). Sin permisos especiales."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Solo funciona en un servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = miembro or interaction.user
        embed, view = await self._open(guild, interaction.user, member, interaction.channel)
        view.message = await interaction.followup.send(
            embed=embed, view=view, ephemeral=True, wait=True
        )

    @commands.command(name="apuestas")
    @commands.guild_only()
    async def apuestas_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.apuestas [miembro]`); se ve en el canal."""
        assert ctx.guild is not None
        member = miembro or ctx.author
        embed, view = await self._open(ctx.guild, ctx.author, member, ctx.channel)
        view.message = await ctx.send(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las jugadas del servidor al salir de él."""
        await self.repository.delete_guild_data(guild.id)


async def record(
    bot: commands.Bot,
    guild_id: int,
    user: discord.abc.User,
    *,
    game: str,
    stake: int,
    net: int,
    balance_after: int,
    tax: int,
    details: tuple[tuple[str, int], ...] = (),
) -> None:
    """Apunta una jugada terminada del casino para `apuestas`. Nunca lanza.

    Se llama al terminar cada jugada, con las mismas cifras que `casino_stats`.
    También se la pasa a las porras (`bot.cogs.porras.observe`): si quien juega
    protagoniza una, la jugada cuenta. Así un juego nuevo tiene porras sin más.

    Args:
        game: Clave de `bot.services.casino_stats.GAMES`.
        stake: Lo apostado (0 en las jugadas gratis).
        net: Ganancia o pérdida de la jugada (devuelto menos apostado).
        balance_after: Saldo del jugador al terminar.
        tax: IRPF que movió la jugada: positivo retenido, negativo devuelto.
        details: Datos propios del juego para las porras (`Play.details`).
    """
    # Import tardío: `porras` importa este módulo para apuntar sus propias jugadas.
    from bot.cogs import porras

    if user.bot:
        return
    play = Play(
        game=game,
        stake=max(0, stake),
        payout=max(0, stake + net),
        tax=tax,
        balance_after=balance_after,
        details=details,
    )
    await porras.observe(bot, guild_id, user.id, play)
    cog = find_cog(bot, Apuestas)
    if cog is None:
        return
    try:
        await cog.record_play(guild_id, user.id, play)
    except Exception:
        logger.exception("No se pudo apuntar la jugada de %s en %s", user.id, game)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el repositorio de jugadas y la economía del bot."""
    await bot.add_cog(Apuestas(bot, bot.casino_stats, bot.economy))
