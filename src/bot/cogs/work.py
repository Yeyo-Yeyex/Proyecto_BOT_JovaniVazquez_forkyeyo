"""Trabajo: `pala`, coger la pala y currar.

`pala` abre el panel de tu curro (solo lo toca su dueño):

- Sin contrato, un menú para elegir oficio (obra, hostelería, política, sanidad u
  oficina).
- Con contrato: puesto, sueldo, batería, rendimiento, familia, jornada y lo
  que falta para ascender. Botones para ⛏️ **Fichar**, 📈 **Ascender** cuando
  toca, 🎓 **Formación** cuando hace falta un curso y 📜 **Vida laboral**, y
  menús para la máquina de café y para cambiar de oficio.

Según el oficio y el puesto aparecen también 🚑 **Guardia** (sanidad), 🏠
**Teletrabajo** (oficina) y 🌏 **Irse a Hong Kong** / ✈️ **Volver a casa**
(oficina, desde senior). Viviendo fuera no se cambia de oficio.

Fichar lanza el minijuego del puesto en el mismo mensaje (cavar, detectar,
memoria o diálogo; de 30 a 60 s). Al acabar, o al agotarse el tiempo, se
cobra la nómina y se enseña con su desglose; a veces sale un evento con dos
opciones. Las reglas están en `bot.services.work`, los minijuegos en
`bot.services.work_games` y los casos de uso (y el dinero) en
`bot.services.pala`.

Dinero: nóminas con IRPF y Seguridad Social (`pay_salary`), turnos en B y
sobres sin declarar (`pay_undeclared`), multas (`sanction`), cuota de
autónomos, café y formación con IGIC. Todas las acciones con dinero que llegan
por botón llevan el aviso de la Renta. Alimenta los logros de 🪏 Trabajo y 👷
Oficios.

Canales: si un administrador elige canales con `tajo`, `pala` solo se abre
en ellos. Permisos del bot: enviar mensajes en el canal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.services.achievements import StatDelta, work_stats
from bot.services.economy import CURRENCY_EMOJI, format_amount
from bot.services.levels import TIMEZONE, local_day
from bot.services.pala import (
    EventResult,
    NeedsBlack,
    OffDuty,
    Shift,
    ShiftOutcome,
    Status,
    WorkError,
    WorkService,
)
from bot.services.taxes import TAX_COLLECTOR, ForeignPayslip, Payslip, format_rate, igic
from bot.services.work import (
    FAMILY_WORRIED,
    FLIGHT_PRICE,
    LEGAL_EXTRAS_PER_WEEK,
    MAX_COFFEES,
    ORDINARY_SHIFTS,
    SELF_EMPLOYED_FEE,
    Mechanic,
    ShiftKind,
    battery_bar,
    battery_state,
    performance_bar,
)
from bot.services.work_catalog import CAUGHT_TEXT, COFFEES, JOB_BY_KEY, JOBS, Event
from bot.services.work_games import GRACE_SECONDS
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Segundos sin tocar el panel antes de que caduque.
PANEL_TIMEOUT = 300
COLOR_IDLE = discord.Color.from_rgb(230, 126, 34)
COLOR_GAME = discord.Color.from_rgb(88, 101, 242)
COLOR_BAD = discord.Color.from_rgb(80, 84, 92)

MECHANIC_NAMES = {
    Mechanic.DIG: "cavar",
    Mechanic.SPOT: "detectar",
    Mechanic.MEMORY: "memoria",
    Mechanic.DIALOGUE: "diálogo",
}
KIND_TEXT = {
    ShiftKind.ORDINARY: "ordinario",
    ShiftKind.EXTRA: "extra (×1,25)",
    ShiftKind.BLACK: "extra en B 🤫",
    ShiftKind.GUARD: "de guardia 🚑",
}
PHASE_TEXT = {
    "residente": "sigues siendo residente fiscal en España (art. 7.p LIRPF: exento hasta "
    "60.100 € al año)",
    "no_residente": "ya no eres residente fiscal en España: solo paga Hong Kong",
}


def decimal(value: float) -> str:
    """`2.5` → `2,5` (coma decimal, al estilo español)."""
    return f"{value:.1f}".replace(".", ",")


def _pct(rate: float) -> str:
    return format_rate(rate)


def payslip_text(slip: Payslip, *, title: str, self_employed: bool) -> str:
    """Desglose de una nómina para enseñarla."""
    lines = [f"📄 **Nómina** · {title}", f"Bruto: **{format_amount(slip.gross)}**"]
    if slip.ss_worker:
        lines.append(
            f"Seguridad Social: −{format_amount(slip.ss_worker)} ({_pct(slip.rates.ss_worker)})"
        )
    if slip.irpf:
        lines.append(f"IRPF: −{format_amount(slip.irpf)} ({_pct(slip.rates.irpf)})")
    else:
        lines.append("IRPF: 0 Y$ (con lo que cobras al año, aún no llegas al mínimo)")
    lines.append(f"## {CURRENCY_EMOJI} Neto: {format_amount(slip.net)}")
    if self_employed:
        lines.append(
            "-# 🧾 Eres autónomo: no cotizas por turno, pagas la cuota cada semana "
            f"({format_amount(SELF_EMPLOYED_FEE)})."
        )
    else:
        lines.append(
            f"-# 🏢 La empresa paga además {format_amount(slip.ss_employer)} de Seguridad "
            f"Social. Coste total: {format_amount(slip.employer_cost)}."
        )
    if slip.net > 0 and slip.total_taxes:
        share = round(slip.total_taxes / slip.net * 100)
        lines.append(
            f"-# 🐶 {TAX_COLLECTOR} se queda {format_amount(slip.total_taxes)} de este turno, "
            f"un {share} % de lo que te llega."
        )
    return "\n".join(lines)


def foreign_payslip_text(slip: ForeignPayslip, *, title: str) -> str:
    """Desglose de una nómina cobrada en Hong Kong."""
    lines = [
        f"📄 **Payslip** · {title} · 🇭🇰 Hong Kong",
        f"Bruto (paquete de expatriado): **{format_amount(slip.gross)}**",
        f"MPF (5 %): −{format_amount(slip.mpf_worker)}",
        f"Salaries tax: −{format_amount(slip.hk_tax)}"
        if slip.hk_tax
        else "Salaries tax: 0 Y$ (aún no pasas de la deducción personal de HK$132.000)",
    ]
    if slip.resident:
        lines.append(f"Exento en España (art. 7.p LIRPF): {format_amount(slip.exempt)}")
        if slip.irpf or slip.double_tax_relief:
            lines.append(
                f"IRPF español: −{format_amount(slip.irpf)} (ya descontados "
                f"{format_amount(slip.double_tax_relief)} por doble imposición, art. 80 LIRPF)"
            )
    else:
        lines.append("IRPF español: 0 Y$ (ya no eres residente en España)")
    lines.append(f"## {CURRENCY_EMOJI} Neto: {format_amount(slip.net)}")
    lines.append(
        f"-# 🇭🇰 Hong Kong se queda {format_amount(slip.foreign)} (MPF de los dos y salaries "
        f"tax). {TAX_COLLECTOR} se queda {format_amount(slip.irpf)} y está que trina."
    )
    return "\n".join(lines)


def status_text(status: Status, notes: list[str]) -> str:
    """Texto del panel de un contrato."""
    contract, job, position = status.contract, status.job, status.position
    state = battery_state(status.battery)
    state_text = {"bien": "", "reventado": " · **reventado**", "zombi": " · **zombi** 🧟"}[state]
    lines = [
        f"## {job.emoji} {position.title} · {job.name} ({contract.level}/{job.top})",
        f"Sueldo base: **{format_amount(position.base_pay)}** por turno · Minijuego: "
        f"{MECHANIC_NAMES[position.mechanic]} ({position.seconds} s)"
        + (" · autónomo" if position.self_employed else ""),
        f"{battery_bar(status.battery)}{state_text}",
        performance_bar(contract.performance)
        + (" · ⚠️ aviso: si vuelve a 0, te bajan" if contract.warned else ""),
        f"❤️ Familia: {contract.family}/100" + (" 😟" if contract.family < FAMILY_WORRIED else ""),
        f"🗓️ Hoy: {min(contract.shifts_today, ORDINARY_SHIFTS)}/{ORDINARY_SHIFTS} turnos "
        f"ordinarios · Extras legales esta semana: "
        f"{min(contract.extras_week, LEGAL_EXTRAS_PER_WEEK)}/{LEGAL_EXTRAS_PER_WEEK}",
        "Próximo turno: "
        + ("cuando acabe el saliente" if status.off_duty else KIND_TEXT[status.next_kind]),
    ]
    if status.can_guard:
        required = job.required_guards(contract.level)
        lines.append(
            f"🚑 Guardias esta semana: {contract.guards_week}"
            + (f"/{required} obligatorias" if required else "")
            + " · la barra solo sube de verdad con guardias"
        )
    if status.off_duty:
        lines.append(f"🛌 **Saliente de guardia** hasta <t:{int(contract.off_duty_until)}:t>.")
    if contract.abroad:
        days = (status.now - contract.abroad_since) / 86_400
        lines.append(
            f"🇭🇰 **En Hong Kong** desde hace {decimal(days)} días: {PHASE_TEXT[status.phase]}. "
            "Sin IMV mientras vivas fuera."
        )
    if status.beckham:
        lines.append(
            f"⚽ **Ley Beckham** hasta <t:{int(contract.beckham_until)}:d>: IRPF al 24 % fijo."
        )
    if job.options_level == contract.level:
        lines.append(
            f"🦄 Stock options acumuladas: **{format_amount(contract.options)}** (en papel)"
        )
    if status.sick:
        lines.append(f"🩹 **De baja** hasta <t:{int(contract.sick_until)}:t>.")
    if status.on_leave:
        lines.append(
            "🏖️ Vuelves de una excedencia: la barra empieza a la mitad en tu próximo turno."
        )
    following = status.next_position
    if following is None:
        lines.append("\n🏆 **Estás en lo más alto del oficio.**")
    elif status.promotion_ready:
        lines.append(f"\n📈 **¡Te ofrecen el ascenso a {following.title}!** Pulsa 📈 Ascender.")
    else:
        lines.append(
            f"\n**Para ascender a {following.title}** "
            f"({format_amount(following.base_pay)} por turno):"
        )
        lines.extend(f"- {item}" for item in status.blockers)
    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines)


def outcome_text(outcome: ShiftOutcome) -> str:
    """Resultado de un turno."""
    game = outcome.game
    where = " · 🏠 teletrabajo" if outcome.remote else " · 🇭🇰" if outcome.abroad else ""
    lines = [f"## ⛏️ Turno {KIND_TEXT[outcome.kind]}{where} · {outcome.score}/100"]
    if outcome.missed_guards:
        lines.append(
            f"📋 **Tu tutor te busca:** la semana pasada te faltaron {outcome.missed_guards} "
            "guardias obligatorias. La barra lo paga."
        )
    detail = f"{game.correct} aciertos"
    if game.mechanic is Mechanic.DIG and game.broken:
        detail += f" · 💥 {game.broken} roturas"
    if game.mechanic is Mechanic.MEMORY:
        detail = f"{game.perfect_rounds} rondas perfectas"
    lines.append(f"-# {detail}")
    if outcome.payslip is not None:
        lines.append(
            payslip_text(
                outcome.payslip,
                title=outcome.position.title,
                self_employed=outcome.position.self_employed,
            )
        )
    if outcome.foreign is not None:
        lines.append(foreign_payslip_text(outcome.foreign, title=outcome.position.title))
    if outcome.beckham:
        lines.append("-# ⚽ Ley Beckham: IRPF al 24 % fijo (art. 93 LIRPF).")
    if outcome.options_added:
        lines.append(
            f"🦄 +{format_amount(outcome.options_added)} en stock options (no se cobran). "
            f"Llevas {format_amount(outcome.options_total)} en papel."
        )
    if outcome.exit_payout:
        lines.append(f"## 🚀 ¡EXIT! Tus opciones valen {format_amount(outcome.exit_payout)}")
        lines.append("-# Exentas hasta 500.000 Y$ (Ley 28/2022); el resto, con IRPF.")
    if outcome.bankrupt:
        lines.append("💀 **La startup quiebra.** Tus stock options valen lo que el papel.")
    if outcome.kind is ShiftKind.GUARD:
        lines.append(
            "🛌 Ahora estás **saliente**: 12 h sin fichar. La guardia pagó "
            "1,6 veces la base por el doble de trabajo: la hora sale más barata."
        )
    if outcome.black:
        lines.append(f"## 🤫 En B: +{format_amount(outcome.black)}")
        lines.append(f"-# Sin IRPF, sin cotizar y sin que lo vea el IMV. {TAX_COLLECTOR} llora.")
    if outcome.caught:
        lines.append(CAUGHT_TEXT["inspeccion"])
        lines.append(f"-# Multa cobrada: {format_amount(outcome.fine)}.")
    if outcome.fee:
        lines.append(f"🧾 Cuota de autónomos de la semana: −{format_amount(outcome.fee)}.")
    lines.append(
        f"{battery_bar(outcome.battery_after)} · ❤️ {outcome.family_after}/100 · "
        f"Saldo: **{format_amount(outcome.balance)}**"
    )
    if outcome.accident:
        lines.append(
            "🚑 **Accidente laboral.** Ibas reventado y pasó lo que tenía que pasar: 24 h de "
            f"baja. La mutua te paga {format_amount(outcome.sick_pay)} brutos (el 75 % de tu "
            "sueldo diario medio)."
        )
    if outcome.performance == "warned":
        lines.append("⚠️ **Tu jefe te ha llamado al despacho.** Si la barra vuelve a 0, te bajan.")
    elif outcome.performance == "demoted":
        lines.append("📉 **Te han bajado de puesto.** De vuelta a la pala.")
    if outcome.intervention:
        lines.append(
            "👪 **Intervención familiar.** Tu familia ya no te reconoce: mañana, nada de extras."
        )
    if outcome.promotion_ready:
        lines.append("📈 **¡Te ofrecen el ascenso!** Vuelve al panel y pulsa 📈 Ascender.")
    return "\n".join(lines)


def event_result_text(result: EventResult) -> str:
    """Lo que pasa al elegir una opción de un evento."""
    lines = [result.text]
    if result.paid is not None:
        lines.append(
            f"{CURRENCY_EMOJI} +{format_amount(result.paid.net)} netos "
            f"({format_amount(result.paid.gross)} brutos, con sus impuestos)."
        )
    if result.black:
        lines.append(f"🤫 +{format_amount(result.black)} en negro.")
    if result.caught:
        lines.append(CAUGHT_TEXT[result.caught_by])
        if result.pardoned:
            lines.append(
                "🕊️ **Pero el Consejo de Ministros te indulta.** No pagas nada. "
                "La justicia es igual para todos, aunque no lo parezca."
            )
        else:
            lines.append(f"-# Multa cobrada: {format_amount(result.fine)}.")
    if result.demoted:
        lines.append("📉 Bajas un puesto.")
    return "\n".join(lines)


class PalaPanel(ui.LayoutView):
    """El panel de un miembro: su curro, el minijuego y los resultados.

    Solo lo toca su dueño. Guarda el turno en marcha; el dinero y el estado
    se guardan siempre a través de `WorkService`.
    """

    def __init__(self, cog: Work, *, guild_id: int, owner: discord.abc.User) -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.service = cog.service
        self.guild_id = guild_id
        self.owner = owner
        self.message: discord.Message | None = None
        self.channel: object = None
        self.shift: Shift | None = None
        self.notes: list[str] = []
        self.pending_event: Event | None = None
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task[None] | None = None
        self._finishing = False

    # -- Dibujo -----------------------------------------------------------------------

    def _button(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> ui.Button:
        button: ui.Button = ui.Button(label=label[:80], style=style, disabled=disabled)
        button.callback = callback  # type: ignore[method-assign]
        return button

    def _frame(self, text: str, color: discord.Color, rows: list[ui.ActionRow]) -> None:
        self.clear_items()
        container = ui.Container(accent_colour=color)
        container.add_item(ui.TextDisplay(text[:3900]))
        self.add_item(container)
        for row in rows:
            if row.children:
                self.add_item(row)

    def show_hiring(self, history: dict[str, tuple[int, int]], notes: list[str]) -> None:
        """Pantalla para elegir oficio (sin contrato)."""
        lines = [
            "## 🪏 Coge la pala",
            "Elige un curro. Cada uno tiene 5 puestos, su minijuego y sus marrones. "
            "Puedes cambiar cuando quieras: si vuelves a un oficio, entras en el puesto "
            "en el que lo dejaste.",
            "",
        ]
        for job in JOBS:
            level = history.get(job.key, (1, 1))[0]
            lines.append(
                f"{job.emoji} **{job.name}** · {job.blurb}\n-# Entrarías de "
                f"{job.position(level).title} ({format_amount(job.position(level).base_pay)} "
                "por turno)"
            )
        lines.extend(notes)
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(self._job_select(None))
        self._frame("\n".join(lines), COLOR_IDLE, [row])

    def _job_select(self, current: str | None) -> ui.Select:
        options = [
            discord.SelectOption(
                label=job.name, value=job.key, emoji=job.emoji, description=job.blurb[:100]
            )
            for job in JOBS
            if job.key != current
        ]
        select: ui.Select = ui.Select(
            placeholder="💼 Cambiar de curro" if current else "💼 Elige tu curro",
            options=options,
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self._hire(interaction, select.values[0])

        select.callback = callback  # type: ignore[method-assign]
        return select

    def _coffee_select(self, coffees_today: int) -> ui.Select:
        options = [
            discord.SelectOption(
                label=f"{coffee.name} · +{coffee.battery} 🔋",
                value=coffee.key,
                emoji=coffee.emoji,
                description=(
                    f"{format_amount(coffee.price + igic(coffee.price))} con IGIC"
                    + (" · ⚠️ temblores" if coffees_today >= MAX_COFFEES else "")
                ),
            )
            for coffee in COFFEES
        ]
        select: ui.Select = ui.Select(
            placeholder=f"☕ Máquina de café ({coffees_today}/{MAX_COFFEES} hoy)", options=options
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self._coffee(interaction, select.values[0])

        select.callback = callback  # type: ignore[method-assign]
        return select

    def show_status(self, status: Status) -> None:
        """Panel de un contrato."""
        notes, self.notes = self.notes, []
        green = discord.ButtonStyle.success
        actions: ui.ActionRow = ui.ActionRow()
        blocked = status.sick or status.off_duty
        actions.add_item(self._button("⛏️ Fichar", self._clock_in, style=green, disabled=blocked))
        if status.can_guard:
            actions.add_item(
                self._button(
                    "🚑 Guardia",
                    self._clock_in_guard,
                    style=discord.ButtonStyle.danger,
                    disabled=blocked,
                )
            )
        if status.job.remote:
            actions.add_item(
                self._button("🏠 Teletrabajo", self._clock_in_remote, disabled=blocked)
            )
        if status.promotion_ready:
            actions.add_item(
                self._button("📈 Ascender", self._offer, style=discord.ButtonStyle.primary)
            )
        following = status.next_position
        if (
            following is not None
            and following.training is not None
            and following.training.key not in status.trainings
        ):
            price = following.training.price + igic(following.training.price)
            actions.add_item(self._button(f"🎓 Formación · {format_amount(price)}", self._training))
        more: ui.ActionRow = ui.ActionRow()
        more.add_item(self._button("📜 Vida laboral", self._career))
        flight = format_amount(FLIGHT_PRICE + igic(FLIGHT_PRICE))
        if status.can_go_abroad:
            more.add_item(self._button(f"🌏 Irse a Hong Kong · {flight}", self._go_abroad))
        elif status.contract.abroad:
            more.add_item(self._button(f"✈️ Volver a casa · {flight}", self._go_home))
        coffee_row: ui.ActionRow = ui.ActionRow()
        coffee_row.add_item(self._coffee_select(status.contract.coffees))
        rows = [actions, more, coffee_row]
        if not status.contract.abroad:
            job_row: ui.ActionRow = ui.ActionRow()
            job_row.add_item(self._job_select(status.contract.job))
            rows.append(job_row)
        self._frame(status_text(status, notes), COLOR_IDLE, rows)

    def show_game(self) -> None:
        """Pantalla del minijuego en marcha."""
        shift = self.shift
        assert shift is not None
        game = shift.game
        current = game.current
        head = [f"### {shift.job.emoji} {shift.position.title} · turno {KIND_TEXT[shift.kind]}"]
        if game.header:
            head.append(game.header)
        if shift.tremors:
            head.append("☕☕☕☕ *Te tiemblan las manos: los botones bailan.*")
        head.append(f"⏱️ Se acaba <t:{int(game.deadline) + 1}:R>")
        rows: list[ui.ActionRow] = []
        if current is None:
            self._frame("\n".join(head), COLOR_GAME, [])
            return
        if game.showing:
            head.append(current.reveal or "")
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(
                self._button("✅ Memorizado", self._hide, style=discord.ButtonStyle.primary)
            )
            self._frame("\n".join(head), COLOR_GAME, [row])
            return
        body = current.prompt
        if game.mechanic is Mechanic.MEMORY:
            body += f"\n-# {game.step}/{len(current.answer)}"
        if game.last:
            body += f"\n-# Anterior: {game.last}"
        indices = list(range(len(current.options)))
        if shift.tremors:
            self.service.rng.shuffle(indices)
        row = ui.ActionRow()
        for i in indices:
            if len(row.children) == 5:
                rows.append(row)
                row = ui.ActionRow()
            row.add_item(self._button(current.options[i], self._option(i)))
        rows.append(row)
        self._frame("\n".join([*head, body]), COLOR_GAME, rows)

    def show_outcome(self, outcome: ShiftOutcome, extra: str | None = None) -> None:
        """Resultado del turno, con el evento si sale."""
        text = outcome_text(outcome)
        if extra:
            text += "\n" + extra
        self.pending_event = outcome.event
        self._show_after(text, outcome.event)

    def _show_after(self, text: str, event: Event | None) -> None:
        row: ui.ActionRow = ui.ActionRow()
        if event is not None:
            text += f"\n\n### 🎲 Marrón\n{event.text}"
            for index, (label, _outcome) in enumerate(event.options):
                row.add_item(
                    self._button(
                        label, self._choose(event.key, index), style=discord.ButtonStyle.primary
                    )
                )
        else:
            row.add_item(
                self._button("⛏️ Otro turno", self._clock_in, style=discord.ButtonStyle.success)
            )
            row.add_item(self._button("🏠 Panel", self._home))
        self._frame(text, COLOR_IDLE, [row])

    def disable_all(self) -> None:
        """Apaga todos los controles (panel caducado)."""
        for item in self.walk_children():
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño toca su pala."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta pala es de {self.owner.display_name}. Coge la tuya con `pala`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había turno a medias, se cierra y se cobra."""
        if self.shift is not None:
            await self._finish(None)
        self.cog.panels.discard(self)
        self.disable_all()
        await self._edit(None)

    async def _edit(self, interaction: discord.Interaction | None) -> None:
        try:
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.edit_message(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo editar el panel de pala", exc_info=True)

    async def refresh(self, interaction: discord.Interaction | None = None) -> None:
        """Vuelve al panel con el estado actual."""
        status = await self.service.status(self.guild_id, self.owner.id)
        if status is None:
            history = await self.service.history(self.guild_id, self.owner.id)
            notes, self.notes = self.notes, []
            self.show_hiring(history, notes)
        else:
            self.show_status(status)
        await self._edit(interaction)

    # -- Acciones del panel -----------------------------------------------------------

    async def _home(self, interaction: discord.Interaction) -> None:
        await self.refresh(interaction)

    async def _hire(self, interaction: discord.Interaction, job_key: str) -> None:
        had_job = await self.service.status(self.guild_id, self.owner.id) is not None
        try:
            status = await self.service.hire(self.guild_id, self.owner.id, job_key)
        except WorkError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        self.notes.append(
            f"✍️ Firmas de **{status.position.title}** en {status.job.emoji} {status.job.name}. "
            "¡A currar, mi amor!"
        )
        self.show_status(status)
        await self._edit(interaction)
        if had_job:
            await logros.track(
                self.cog.bot,
                self.guild_id,
                self.owner,
                self.channel,
                StatDelta(add={"work_job_changes": 1}),
            )

    async def _coffee(self, interaction: discord.Interaction, key: str) -> None:
        try:
            battery, coffees, _balance = await self.service.buy_coffee(
                self.guild_id, self.owner.id, key
            )
        except WorkError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        coffee = next(c for c in COFFEES if c.key == key)
        note = (
            f"{coffee.emoji} Te tomas un {coffee.name.lower()}: {battery_bar(battery)} "
            f"({format_amount(coffee.price + igic(coffee.price))}, IGIC incluido)."
        )
        if coffees > MAX_COFFEES:
            note += " Te tiemblan hasta las pestañas."
        self.notes.append(note)
        await self.refresh(interaction)
        await renta.remind(self.cog.bot, interaction)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(peak={"work_coffees_day_max": coffees}),
        )

    async def _training(self, interaction: discord.Interaction) -> None:
        try:
            name, base, tax, _balance = await self.service.buy_training(
                self.guild_id, self.owner.id
            )
        except WorkError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        self.notes.append(
            f"🎓 Consigues {name}: {format_amount(base)} + {format_amount(tax)} de IGIC."
        )
        await self.refresh(interaction)
        await renta.remind(self.cog.bot, interaction)

    async def _career(self, interaction: discord.Interaction) -> None:
        history = await self.service.history(self.guild_id, self.owner.id)
        lines = [f"📜 **Vida laboral de {discord.utils.escape_markdown(self.owner.display_name)}**"]
        for job in JOBS:
            if job.key in history:
                level, top = history[job.key]
                lines.append(
                    f"{job.emoji} {job.name}: {job.position(level).title} "
                    f"(máximo: {job.position(top).title})"
                )
        if len(lines) == 1:
            lines.append("Nada todavía. Ni unas prácticas.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def _offer(self, interaction: discord.Interaction) -> None:
        status = await self.service.status(self.guild_id, self.owner.id)
        if status is None or not status.promotion_ready or status.next_position is None:
            await self.refresh(interaction)
            return
        following = status.next_position
        text = (
            f"## 📈 Te ofrecen el ascenso\n**{following.title}** · "
            f"{format_amount(following.base_pay)} por turno (ahora "
            f"{format_amount(status.position.base_pay)}).\n"
            "Cobrar más reduce tu IMV, pero cada Y$ de más solo te quita medio. "
            "¿Lo aceptas?"
        )
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(self._button("✅ Acepto", self._accept, style=discord.ButtonStyle.success))
        row.add_item(self._button("🙅 Me quedo con la paguita", self._decline))
        self._frame(text, COLOR_IDLE, [row])
        await self._edit(interaction)

    async def _accept(self, interaction: discord.Interaction) -> None:
        try:
            status = await self.service.accept_promotion(self.guild_id, self.owner.id)
        except WorkError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        self.notes.append(
            f"🎉 **¡Ascendido a {status.position.title}!** Ahora cobras "
            f"{format_amount(status.position.base_pay)} brutos por turno. ¡Wepa!"
        )
        self.show_status(status)
        await self._edit(interaction)
        delta = StatDelta(add={"work_promotions": 1})
        delta.merge(await self.cog.top_stats(self.guild_id, self.owner.id))
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)

    async def _decline(self, interaction: discord.Interaction) -> None:
        await self.service.decline_promotion(self.guild_id, self.owner.id)
        self.notes.append("🪑 Rechazas el ascenso. Mañana te lo vuelven a ofrecer.")
        await self.refresh(interaction)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"work_declined": 1}),
        )

    # -- Turno ------------------------------------------------------------------------

    async def _clock_in(
        self,
        interaction: discord.Interaction,
        *,
        black_ok: bool = False,
        guard: bool = False,
        remote: bool = False,
    ) -> None:
        async with self._lock:
            if self.shift is not None:
                await interaction.response.defer()
                return
            key = (self.guild_id, self.owner.id)
            if key in self.cog.working:
                # Dos paneles abiertos no pueden fichar a la vez: el segundo se
                # saltaría la jornada, la batería y la pregunta del B.
                await interaction.response.send_message(
                    "Ya estás fichando en otro panel. Una pala cada vez, mi amor.", ephemeral=True
                )
                return
            try:
                shift = await self.service.start_shift(
                    self.guild_id, self.owner.id, black_ok=black_ok, guard=guard, remote=remote
                )
            except NeedsBlack as error:
                row: ui.ActionRow = ui.ActionRow()
                row.add_item(
                    self._button(
                        "💶 Venga, en B", self._clock_in_black, style=discord.ButtonStyle.danger
                    )
                )
                row.add_item(self._button("🏠 Me voy a casa", self._home))
                self._frame(
                    f"## 🤫 Horas extra\n{error}\n-# Cobras el turno sin impuestos, pero si "
                    "viene la Inspección devuelves todo con recargo y te quedas sin IMV 3 días.",
                    COLOR_BAD,
                    [row],
                )
                await self._edit(interaction)
                return
            except OffDuty as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                await logros.track(
                    self.cog.bot,
                    self.guild_id,
                    self.owner,
                    self.channel,
                    StatDelta(add={"work_off_duty_tries": 1}),
                )
                return
            except WorkError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return
            self.shift = shift
            self.cog.working.add(key)
            self._finishing = False
            self.show_game()
            await self._edit(interaction)
            if interaction.message is not None:
                self.message = interaction.message
            self._timer = asyncio.create_task(self._deadline(shift))
            self.cog.timers.add(self._timer)
            self._timer.add_done_callback(self.cog.timers.discard)

    async def _clock_in_black(self, interaction: discord.Interaction) -> None:
        await self._clock_in(interaction, black_ok=True)

    async def _clock_in_guard(self, interaction: discord.Interaction) -> None:
        await self._clock_in(interaction, guard=True)

    async def _clock_in_remote(self, interaction: discord.Interaction) -> None:
        await self._clock_in(interaction, remote=True)

    # -- Hong Kong --------------------------------------------------------------------

    async def _go_abroad(self, interaction: discord.Interaction) -> None:
        try:
            status = await self.service.move_abroad(self.guild_id, self.owner.id)
        except WorkError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        self.notes.append(
            "✈️ **¡Te vas a Hong Kong!** Doce horas de vuelo, un jet lag que te deja la "
            "batería tiritando y Robuso esperándote en el aeropuerto. Cobrarás el doble "
            "(paquete de expatriado), pero pagarás MPF y "
            "salaries tax, y no hay IMV mientras vivas fuera (art. 36.e de la Ley 19/2021). "
            "Pasados 4 días dejas de ser residente fiscal en España (183 días del año, "
            "art. 9.1.a LIRPF, a la escala del juego)."
        )
        self.show_status(status)
        await self._edit(interaction)
        await renta.remind(self.cog.bot, interaction)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"work_abroad": 1}),
        )

    async def _go_home(self, interaction: discord.Interaction) -> None:
        try:
            status, beckham, days = await self.service.come_home(self.guild_id, self.owner.id)
        except WorkError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        note = f"🏠 **Vuelves a casa** tras {decimal(days)} días en Hong Kong. Tu madre llora."
        if beckham:
            note += (
                " Y como has pasado fuera 5 «años», te toca la **Ley Beckham** (art. 93 LIRPF): "
                "durante 6 semanas tu IRPF es un 24 % fijo."
            )
        self.notes.append(note)
        self.show_status(status)
        await self._edit(interaction)
        await renta.remind(self.cog.bot, interaction)
        delta = StatDelta(add={"work_return": 1}, peak={"work_abroad_days_max": int(days)})
        if beckham:
            delta.add["work_beckham"] = 1
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)

    async def _deadline(self, shift: Shift) -> None:
        """Cierra el turno al acabarse el tiempo aunque nadie pulse nada."""
        delay = shift.game.deadline + GRACE_SECONDS - self.service.now()
        await asyncio.sleep(max(0.0, delay))
        if self.shift is shift:
            await self._finish(None)

    def _option(self, index: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            shift = self.shift
            if shift is None or self._finishing:
                await interaction.response.defer()
                return
            now = self.service.now()
            shift.game.press(index, now)
            if shift.game.finished(now):
                await self._finish(interaction)
                return
            self.show_game()
            await self._edit(interaction)

        return callback

    async def _hide(self, interaction: discord.Interaction) -> None:
        if self.shift is None:
            await interaction.response.defer()
            return
        self.shift.game.hide(self.service.now())
        self.show_game()
        await self._edit(interaction)

    async def _finish(self, interaction: discord.Interaction | None) -> None:
        """Cobra el turno y enseña el resultado (una sola vez por turno)."""
        if self._finishing or self.shift is None:
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.defer()
            return
        self._finishing = True
        shift = self.shift
        if self._timer is not None and asyncio.current_task() is not self._timer:
            self._timer.cancel()
        try:
            outcome = await self.service.finish_shift(self.guild_id, self.owner.id, shift)
        except WorkError as error:
            self.shift = None
            self.notes.append(str(error))
            await self.refresh(interaction)
            return
        finally:
            self.shift = None
            self.cog.working.discard((self.guild_id, self.owner.id))
        extra = await renta.hint(self.cog.bot, self.guild_id, self.owner.id)
        self.show_outcome(outcome, extra)
        await self._edit(interaction)
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        delta = work_stats(
            outcome, birthday=await self.cog.is_birthday(self.guild_id, self.owner.id)
        )
        delta.merge(await self.cog.top_stats(self.guild_id, self.owner.id))
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)

    def _choose(
        self, event_key: str, option: int
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if self.pending_event is None or self.pending_event.key != event_key:
                await interaction.response.defer()
                return
            self.pending_event = None
            try:
                result = await self.service.resolve_event(
                    self.guild_id, self.owner.id, event_key, option
                )
            except WorkError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return
            self.pending_event = result.follow_up
            self._show_after(
                f"### 🎲 {result.event.options[option][0]}\n" + event_result_text(result),
                result.follow_up,
            )
            await self._edit(interaction)
            if result.paid is not None or result.black:
                await renta.remind(self.cog.bot, interaction)
            if result.stats:
                await logros.track(
                    self.cog.bot,
                    self.guild_id,
                    self.owner,
                    self.channel,
                    StatDelta(add=dict(result.stats)),
                )

        return callback


class Work(commands.Cog, name="Trabajo"):
    """`pala`: oficios, turnos con minijuego, nóminas y ascensos."""

    def __init__(self, bot: commands.Bot, service: WorkService) -> None:
        self.bot = bot
        self.service = service
        self.panels: set[PalaPanel] = set()
        #: Miembros con un turno en marcha (en cualquier panel).
        self.working: set[tuple[int, int]] = set()
        #: Temporizadores de turnos en marcha, para cancelarlos al descargar.
        self.timers: set[asyncio.Task[None]] = set()

    async def cog_unload(self) -> None:
        """Cierra los turnos a medias (se cobran) y cancela los temporizadores."""
        for panel in list(self.panels):
            if panel.shift is not None:
                with contextlib.suppress(Exception):
                    await panel._finish(None)
            panel.stop()
        self.panels.clear()
        for timer in list(self.timers):
            timer.cancel()

    async def is_birthday(self, guild_id: int, user_id: int) -> bool:
        """Si hoy (hora canaria) es el cumpleaños del miembro."""
        birthdays = getattr(self.bot, "birthdays", None)
        if birthdays is None:
            return False
        try:
            birthday = await birthdays.get_birthday(guild_id, user_id)
        except Exception:
            logger.exception("No se pudo consultar el cumpleaños de %s", user_id)
            return False
        if birthday is None:
            return False
        today = local_day(datetime.now(TIMEZONE).timestamp())
        return (birthday.day, birthday.month) == (today.day, today.month)

    async def top_stats(self, guild_id: int, user_id: int) -> StatDelta:
        """Máximos de la carrera: oficios coronados y puesto 5 de cada uno."""
        history = await self.service.history(guild_id, user_id)
        delta = StatDelta(peak={})
        tops = [job for job, (_level, top) in history.items() if top >= JOB_BY_KEY[job].top]
        delta.peak["work_jobs_top"] = len(tops)
        delta.peak["work_jobs_tried"] = len(history)
        for job in tops:
            delta.peak[f"work_top_{job}"] = 1
        return delta

    async def channel_error(self, guild: discord.Guild, channel: object) -> str | None:
        """Mensaje si `pala` no se puede usar en este canal, o `None`."""
        allowed = await self.service.channels(guild.id)
        if not allowed:
            return None
        channel_id = getattr(channel, "id", None)
        parent_id = getattr(channel, "parent_id", None)
        if channel_id in allowed or parent_id in allowed:
            return None
        where = ", ".join(f"<#{c}>" for c in sorted(allowed))
        return f"La pala se coge en {where}, mi amor. Aquí se viene a hablar."

    async def _open(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/pala` y `.pala`: abre el panel."""
        if guild is None:
            await send_error("La pala solo se coge dentro de un servidor.")
            return
        if error := await self.channel_error(guild, channel):
            await send_error(error)
            return
        panel = PalaPanel(self, guild_id=guild.id, owner=user)
        panel.channel = channel
        status = await self.service.status(guild.id, user.id)
        if status is None:
            panel.show_hiring(await self.service.history(guild.id, user.id), [])
        else:
            panel.show_status(status)
        panel.message = await send(view=panel, allowed_mentions=discord.AllowedMentions.none())
        self.panels.add(panel)

    @app_commands.command(name="pala", description="Coge la pala: tu curro, turnos y ascensos.")
    @app_commands.guild_only()
    async def pala(self, interaction: discord.Interaction) -> None:
        """Abre el panel de tu curro. Solo en los canales elegidos con `tajo`, si los hay."""

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._open(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )

    @commands.command(name="pala")
    @commands.guild_only()
    async def pala_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.pala`) de `/pala`."""

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._open(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra el trabajo del servidor cuando el bot deja de pertenecer a él."""
        await self.service.repository.delete_guild_data(guild.id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el servicio de trabajo del bot."""
    await bot.add_cog(Work(bot, bot.work))
