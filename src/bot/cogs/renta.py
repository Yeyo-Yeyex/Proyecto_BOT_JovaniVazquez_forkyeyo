"""Campaña de la Renta: la declaración del IRPF de cada semana.

El año fiscal del bot es la semana. Durante la semana cada cobro retiene a cuenta
(las nóminas, los premios y los alquileres con el tipo de cada uno; el casino, el 19
% de lo ganado cada día). Cada lunes este cog cierra la semana anterior en cada
servidor (`EconomyService.close_irpf_weeks`): sale la declaración de cada miembro con
toda su renta, sus deducciones y lo retenido.

- **Si sale a pagar**, al abrirla se paga entera o fraccionada: el 60 % al momento y
  el 40 % al final del jueves, sin intereses ni recargo (art. 62.2 del Reglamento del
  IRPF). Hay hasta el final del lunes para elegir; si no, se cobra entera. Lo que no
  llegue queda como deuda con Hacienda, sin recargo: se embarga el 30 % de todo lo que
  se cobra (`taxes.GARNISH_SHARE`), se barre el monedero los lunes y se puede liquidar
  con el botón **Liquidar deuda**. Los cobros que hace Hacienda sola (el embargo, el
  segundo plazo) se avisan con una línea pequeña en la siguiente acción con dinero.
- **Si sale a devolver**, primero paga lo que debiera y el resto hay que presentarlo,
  desde el casino (la primera vez en la campaña que alguien juega con la renta
  pendiente le sale un mensaje que solo ve él con el botón **Presentar**; luego solo
  una línea pequeña) o con `renta`. Al presentar, el bot publica una línea en el canal
  para que se compare. Hay `CLAIM_WEEKS` semanas para presentarla (los cuatro años de
  la ley); después prescribe y el dinero se queda en el Estado.

**Expectativa.** Como en la vida real, quien no lleva la cuenta de todo lo que cobra no
sabe hasta el lunes si le toca pagar o le devuelven: `renta` no adelanta el resultado
de la semana en curso. El lunes el anuncio de `#chat-general` (o del canal del
sistema) solo da los totales del servidor y a cada miembro le llega una vez, en su
siguiente acción con dinero (`hint`), que ya tiene la declaración, sin la cifra. La
abre en `renta`: un sobre con el botón **Abrir** y, al abrirlo, el **resumen** (las
cuentas en cinco líneas) con un botón para ver el **detalle** (el porqué de cada cifra,
con sus artículos) y otro para volver (`bot.services.renta_report`). `saldo` tampoco
adelanta nada: de una declaración sin abrir solo dice que está sin abrir (`pending`).

Cada semana el bot crea un evento de Discord ("📬 Campaña de la Renta") que dura hasta
el domingo, para que se vea sin ocupar un canal. Necesita el permiso **Gestionar
eventos**; sin él, todo lo demás funciona igual.

Las semanas de antes de la 8.0.0 en un servidor se declaran con la regla vieja (solo
el casino y los donativos, siempre a devolver), al vuelo.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import intereses
from bot.cogs import pets as mascotas
from bot.repositories.economy import PAYMENT_PENDING, PAYMENT_SPLIT
from bot.services.achievements import StatDelta
from bot.services.economy import (
    Declaration,
    EconomyService,
    IrpfPayment,
    IrpfRun,
    IrpfStatement,
    fiscal_week,
    format_amount,
    installment_deadlines,
    monday_epoch,
    week_label,
    week_start,
)
from bot.services.levels import TIMEZONE, local_day
from bot.services.pending import Pending, Side
from bot.services.pets import Moment
from bot.services.renta_report import detail_text, envelope_text, summary_text
from bot.services.taxes import CLAIM_WEEKS, TAX_COLLECTOR
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

EVENT_PREFIX = "📬 Campaña de la Renta"
VIEW_TIMEOUT = 600
ANNOUNCE_CHANNEL_NAME = "chat-general"
COLOR = discord.Color.from_rgb(170, 21, 27)
#: Límite del texto de un embed de Discord.
EMBED_LIMIT = 4_096

#: Modos de la vista de `renta`.
SEALED = "sobre"
SUMMARY = "resumen"
DETAIL = "detalle"

#: Frases del anuncio del lunes, según cómo le haya ido a Hacienda.
MONDAY_GOOD: tuple[str, ...] = (
    "Esta semana se recauda más de lo que se devuelve.",
    "Cuentas cerradas: el Estado sale ganando esta semana.",
    "La recaudación sube otra vez: buena semana para el Estado.",
    "Hacienda somos todos, pero esta semana unos más que otros.",
    "La Agencia Tributaria agradece su colaboración y le recuerda que no tenía elección.",
    "Bruselas aplaude el ajuste. El ajuste eras tú.",
)
MONDAY_REFUND: tuple[str, ...] = (
    "Esta semana se devuelve más de lo que se cobra.",
    "Semana de devoluciones: el Estado sale perdiendo.",
    "Lo retenido de más vuelve a casa, si presentas la renta.",
    "Esta semana el Estado sale perdiendo. Ya lo recuperará con el IBI.",
    "Devoluciones a mansalva: alguien en Hacienda ha pulsado el botón equivocado.",
)
#: Frases del aviso de que ya está la declaración (sin decir qué sale).
NOTICE_LINES: tuple[str, ...] = (
    "Hacienda ya ha hecho tus cuentas de la semana {week}. ¿Pagas o te devuelven?",
    "Ha llegado la carta de {collector} con tu renta de la semana {week}. Ábrela si te atreves.",
    "Tu declaración de la semana {week} está lista. Respira hondo.",
    "{collector} ha terminado de sumar tu semana {week}. Él ya sabe el resultado; tú todavía no.",
)


def draft_text(declarations: list[Declaration]) -> str:
    """Borrador de las declaraciones a devolver, para mostrar a su dueño."""
    total = sum(d.refund for d in declarations)
    lines = [
        f"📬 **Tienes la renta sin presentar: te salen {format_amount(total)} a devolver.**",
    ]
    if len(declarations) > 1:
        lines += [
            f"-# Semana {week_label(d.week_start)}: {format_amount(d.refund)}" for d in declarations
        ]
    lines.append(
        f"-# Hay {CLAIM_WEEKS} semanas para presentar cada una (los cuatro años de la ley); "
        f"después prescribe y se la queda {TAX_COLLECTOR}."
    )
    return "\n".join(lines)


def notice_text(week: date) -> str:
    """Aviso de que ya está una declaración, sin decir qué sale (para `hint`)."""
    phrase = NOTICE_LINES[week.toordinal() // 7 % len(NOTICE_LINES)]
    text = phrase.format(week=week_label(week), collector=TAX_COLLECTOR)
    return f"-# 📑 {text} Ábrela con `/renta` (si sale a pagar, hay hasta el final del lunes)."


def announcement_text(week: date, run: IrpfRun) -> str:
    """Anuncio público del cierre de una semana: solo los totales, sin nombres.

    Cada uno descubre lo suyo al abrir su declaración: nadie se entera antes por el canal.
    """
    pay = [r for r in run.returns if r.result > 0]
    back = [r for r in run.returns if r.result < 0]
    total_pay = sum(r.result for r in pay)
    total_back = -sum(r.result for r in back)
    pool = MONDAY_GOOD if total_pay >= total_back else MONDAY_REFUND
    return "\n".join(
        (
            f"🧾 **{TAX_COLLECTOR} ha hecho las cuentas de la semana {week_label(week)}:** "
            f"{len(run.returns)} declaraciones, {len(pay)} a pagar y {len(back)} a devolver.",
            f"Entre todos: {format_amount(total_pay)} para el Estado y "
            f"{format_amount(total_back)} de vuelta.",
            f"-# {pool[week.toordinal() // 7 % len(pool)]} Cada uno abre la suya con `/renta`; "
            "quien tenga que pagar, hasta el final del lunes.",
        )
    )


def pending_hint() -> str:
    """Línea pequeña para el resultado de una jugada si hay renta pendiente."""
    return "-# 📬 Tienes la renta sin presentar: `/renta`"


def debt_text(debt: int) -> str:
    """Lo que se debe a Hacienda, con cómo se cobra (en `renta`, junto al botón)."""
    return (
        f"⚖️ **Debes {format_amount(debt)} a Hacienda.** {TAX_COLLECTOR} se queda el 30 % de "
        "todo lo que cobras hasta saldarla. Puedes liquidarla ya con el botón."
    )


def embargo_hint(embargoed: int, left: int) -> str:
    """Línea pequeña de lo embargado desde la última acción (`hint`)."""
    rest = f"; aún debes {format_amount(left)}" if left else ": deuda saldada"
    return f"-# ⚖️ {TAX_COLLECTOR} te ha embargado {format_amount(embargoed)}{rest}."


def second_installment_hint(paid: int, unpaid: int) -> str:
    """Línea pequeña del segundo plazo de la Renta, cobrado solo el jueves (`hint`)."""
    text = f"-# 🐶 Se ha cobrado el segundo plazo de tu Renta: {format_amount(paid)}."
    if unpaid:
        text += f" {format_amount(unpaid)} no llegaron y quedan como deuda."
    return text


def payment_text(payment: IrpfPayment | None) -> str:
    """Lo que ha pasado al pulsar **Pagar todo** o **Fraccionar**."""
    if payment is None:
        return "Esta declaración ya no estaba pendiente de pago."
    if payment.second:
        text = (
            f"✂️ **Fraccionada.** Pagas {format_amount(payment.charged)} ahora y "
            f"{format_amount(payment.second)} el jueves."
        )
    else:
        text = f"✅ **Pagada:** {format_amount(payment.charged)}."
    if payment.debt:
        text += f" {format_amount(payment.debt)} no llegaron y quedan como deuda, sin recargo."
    return text + f" Saldo: **{format_amount(payment.balance)}**."


def debt_payment_text(paid: int, left: int, balance: int) -> str:
    """Lo que ha pasado al pulsar **Liquidar deuda**."""
    if not paid:
        if not left:
            return "Ya no debes nada a Hacienda."
        return "No tienes saldo para pagar nada de la deuda."
    if left:
        return (
            f"⚖️ Pagados {format_amount(paid)}; aún debes {format_amount(left)}. "
            f"Saldo: **{format_amount(balance)}**."
        )
    return (
        f"✅ **Deuda liquidada:** {format_amount(paid)}. Ya no debes nada a Hacienda. "
        f"Saldo: **{format_amount(balance)}**."
    )


def renta_embed(
    mode: str,
    statement: IrpfStatement | None,
    pending: list[Declaration],
    *,
    rate: float | None = None,
    extra: str = "",
    debt: int = 0,
) -> discord.Embed:
    """El embed de `renta` en un modo: el sobre, el resumen o el detalle.

    Args:
        debt: Lo que debe a Hacienda; sale fuera del sobre, con el botón para liquidarla.
    """
    parts: list[str] = []
    if statement is None:
        parts.append(
            f"📑 Aún no tienes ninguna declaración. El lunes {TAX_COLLECTOR} hará las cuentas "
            "de esta semana: si te retuvo de menos la pagas aquí (entera o en dos plazos), y "
            "si te retuvo de más te lo devuelve si la presentas aquí."
        )
    elif mode == SEALED:
        parts.append(envelope_text(statement))
    elif mode == DETAIL:
        parts.append(detail_text(statement, rate=rate))
    else:
        parts.append(summary_text(statement))
    if pending and mode != SEALED:
        parts.append(draft_text(pending))
    if debt and mode != SEALED:
        parts.append(debt_text(debt))
    if extra:
        parts.append(extra)
    text = "\n\n".join(parts)
    if len(text) > EMBED_LIMIT:
        text = text[: EMBED_LIMIT - 2] + "…"
    return discord.Embed(title="🧾 Renta", description=text, color=COLOR)


async def remind(bot: commands.Bot, interaction: discord.Interaction) -> None:
    """Atajo para el casino: avisa de la renta pendiente si el cog está cargado."""
    if (cog := find_cog(bot, Renta)) is not None:
        await cog.remind(interaction)


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Lo pendiente con Hacienda (para `saldo`, ver `Renta.pending_for`), o `[]`.

    Nunca lanza: si algo falla se registra y `saldo` sale sin estas líneas.
    """
    if (cog := find_cog(bot, Renta)) is None:
        return []
    try:
        return await cog.pending_for(guild_id, user_id)
    except Exception:
        logger.exception("No se pudo consultar lo pendiente de la renta")
        return []


async def hint(
    bot: commands.Bot, guild_id: int, user_id: int, moment: Moment | None = None
) -> str | None:
    """Líneas pequeñas para el resultado de cualquier acción con dinero, o `None`.

    Una sola vez, que ya tiene su declaración del lunes (sin decir qué sale); la de la
    renta pendiente de presentar; una sola vez, la de los intereses que aún no ha visto
    (`bot.cogs.intereses`); la de las cajas gratis que ha ganado jugando
    (`bot.cogs.shop.notice`); y lo que diga su mascota activa (`bot.cogs.pets.cameo`).
    Así la declaración, el cobro diario de la cuenta y las mascotas llegan a todos los
    juegos sin tocarlos uno a uno.

    Args:
        moment: Lo que acaba de pasar (`bot.services.pets.bet_moment` en una
            jugada), para que la mascota reaccione a eso y para que puedan
            aparecer mascotas nuevas. Sin él, la mascota habla de dinero en
            general y no aparece ninguna.
    """
    lines = []
    if (cog := find_cog(bot, Renta)) is not None and (
        line := await cog.hint_for(guild_id, user_id)
    ):
        lines.append(line)
    if line := await intereses.hint(bot, guild_id, user_id):
        lines.append(line)
    # Import tardío: `shop` importa este módulo.
    from bot.cogs import shop as tienda

    if line := tienda.notice(bot, guild_id, user_id):
        lines.append(line)
    if line := await mascotas.cameo(bot, guild_id, user_id, moment):
        lines.append(line)
    return "\n".join(lines) if lines else None


class RentaView(discord.ui.View):
    """La declaración en `renta`: abrir el sobre, resumen o detalle, pagar y presentar.

    Pasar del resumen al detalle solo cambia lo que se enseña (la declaración ya está
    cargada): contesta en el acto. Abrir el sobre, pagar (entera o fraccionada),
    liquidar la deuda y presentar tocan la base de datos.
    """

    def __init__(
        self,
        cog: Renta,
        owner_id: int,
        statement: IrpfStatement | None,
        pending: list[Declaration],
        *,
        mode: str,
        rate: float | None = None,
        extra: str = "",
        debt: int = 0,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id
        self.statement = statement
        self.pending = pending
        self.mode = mode
        self.rate = rate
        self.extra = extra
        self.debt = debt
        self._rebuild()

    def embed(self) -> discord.Embed:
        """El embed del modo en que está."""
        return renta_embed(
            self.mode,
            self.statement,
            self.pending,
            rate=self.rate,
            extra=self.extra,
            debt=self.debt,
        )

    def _rebuild(self) -> None:
        self.clear_items()
        if self.statement is not None:
            if self.mode == SEALED:
                self._button("Abrir", "📑", discord.ButtonStyle.primary, "renta:abrir", self._open)
            elif self.mode == SUMMARY:
                self._button(
                    "Ver el detalle", "🔍", discord.ButtonStyle.secondary, "renta:detalle",
                    self._detail,
                )  # fmt: skip
            else:
                self._button(
                    "Ver el resumen", "📄", discord.ButtonStyle.secondary, "renta:resumen",
                    self._summary,
                )  # fmt: skip
        statement = self.statement if self.mode != SEALED else None
        if statement is not None and statement.filed.payment == PAYMENT_PENDING:
            self._button(
                "Pagar todo", "💳", discord.ButtonStyle.success, "renta:pagar", self._pay_full
            )
            self._button(
                "Fraccionar", "✂️", discord.ButtonStyle.primary, "renta:fraccionar",
                self._pay_split,
            )  # fmt: skip
        if self.pending and self.mode != SEALED:
            self._button(
                "Presentar", "📬", discord.ButtonStyle.success, "renta:presentar", self._present
            )
        if self.debt and self.mode != SEALED:
            self._button(
                "Liquidar deuda", "⚖️", discord.ButtonStyle.danger, "renta:liquidar",
                self._settle_debt,
            )  # fmt: skip

    def _button(
        self, label: str, emoji: str, style: discord.ButtonStyle, custom_id: str, callback
    ) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
        button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=custom_id)
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esta declaración no es tuya.", ephemeral=True)
            return False
        return True

    async def _open(self, interaction: discord.Interaction) -> None:
        """Abre el sobre: marca la declaración como vista y enseña el resumen."""
        await ack(interaction)
        if self.statement is not None and interaction.guild is not None:
            await self.cog.economy.open_irpf_return(
                interaction.guild.id, self.owner_id, self.statement.week
            )
            self.cog.track_opened(interaction, self.statement)
        self.mode = SUMMARY
        self._rebuild()
        await edit(interaction, embed=self.embed(), view=self)

    async def _detail(self, interaction: discord.Interaction) -> None:
        self.mode = DETAIL
        self._rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)
        if interaction.guild is not None:
            logros.note(
                self.cog.bot,
                interaction.guild.id,
                self.owner_id,
                StatDelta(add={"renta_detail": 1}),
                interaction.channel_id,
            )

    async def _summary(self, interaction: discord.Interaction) -> None:
        self.mode = SUMMARY
        self._rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _present(self, interaction: discord.Interaction) -> None:
        await self.cog.present(interaction)

    async def _pay_full(self, interaction: discord.Interaction) -> None:
        await self.cog.pay(interaction, self, split=False)

    async def _pay_split(self, interaction: discord.Interaction) -> None:
        await self.cog.pay(interaction, self, split=True)

    async def _settle_debt(self, interaction: discord.Interaction) -> None:
        await self.cog.settle_debt(interaction, self)
        self.stop()


class PresentView(discord.ui.View):
    """Botón Presentar del aviso del casino; solo lo puede pulsar su dueño."""

    def __init__(self, cog: Renta, owner_id: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esta declaración no es tuya.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Presentar", emoji="📬", style=discord.ButtonStyle.success)
    async def present(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.cog.present(interaction)
        self.stop()


class Renta(commands.Cog):
    """Declaración semanal del IRPF: cierre de los lunes, avisos y evento de campaña."""

    def __init__(self, bot: commands.Bot, economy: EconomyService) -> None:
        self.bot = bot
        self.economy = economy
        #: `(servidor, usuario, lunes de la campaña)` a quienes ya se avisó con
        #: el efímero; en esta campaña solo verán la línea pequeña.
        self._reminded: set[tuple[int, int, date]] = set()
        self._events_forbidden: set[int] = set()
        #: `(servidor, usuario)` → hasta cuándo se le ha avisado de lo que Hacienda cobra
        #: sola (el embargo, el segundo plazo). Lo de antes de arrancar no se avisa.
        # shortcut: en memoria, un reinicio se salta el aviso de lo cobrado mientras estaba
        # apagado; pasarlo a la base si se echa en falta.
        self._seen: dict[tuple[int, int], float] = {}
        self._started = economy.now()

    async def cog_load(self) -> None:
        self._campaign_events.start()

    async def cog_unload(self) -> None:
        self._campaign_events.cancel()

    # -- Recordatorios desde el casino -----------------------------------------------

    async def _sealed(self, guild_id: int, user_id: int) -> bool:
        """Si su última declaración está sin abrir (entonces no se adelanta nada)."""
        statement = await self.economy.irpf_statement(guild_id, user_id)
        return statement is not None and not statement.opened

    async def hint_for(self, guild_id: int, user_id: int) -> str | None:
        """Líneas pequeñas de la renta: declaración nueva (sin cifra), la que falta presentar
        y lo que Hacienda ha cobrado sola desde la última vez (embargo y segundo plazo)."""
        lines = []
        try:
            for week in await self.economy.take_irpf_notices(guild_id, user_id):
                lines.append(notice_text(week))
            if not lines and not await self._sealed(guild_id, user_id):
                if await self.economy.pending_declarations(guild_id, user_id):
                    lines.append(pending_hint())
            lines += await self._collected_lines(guild_id, user_id)
        except Exception:
            logger.exception("No se pudo consultar la renta")
        return "\n".join(lines) if lines else None

    async def _collected_lines(self, guild_id: int, user_id: int) -> list[str]:
        """Avisos de lo que Hacienda ha cobrado sola desde el último aviso, una vez cada uno."""
        key = (guild_id, user_id)
        since, until = self._seen.get(key, self._started), self.economy.now()
        self._seen[key] = until
        embargoed, paid, unpaid, left = await self.economy.irpf_notices(
            guild_id, user_id, since, until
        )
        lines = []
        if embargoed:
            lines.append(embargo_hint(embargoed, left))
            logros.note(self.bot, guild_id, user_id, StatDelta(add={"debt_garnished": embargoed}))
        if paid or unpaid:
            lines.append(second_installment_hint(paid, unpaid))
        return lines

    async def pending_for(self, guild_id: int, user_id: int) -> list[Pending]:
        """Lo pendiente con Hacienda, para `saldo`: deuda, plazos de la Renta y devoluciones.

        Si la última declaración está sin abrir, sale sin cifra y sin las devoluciones:
        el sobre se abre en `renta`. El plazo que se dice vale para cualquier resultado
        («si sale a pagar»), así que tampoco adelanta nada.
        """
        items = []
        if debt := await self.economy.tax_debt(guild_id, user_id):
            items.append(
                Pending(
                    Side.PAY, "renta_debt", "⚖️",
                    f"Deuda con Hacienda: {format_amount(debt)} · {TAX_COLLECTOR} se queda el "
                    "30 % de todo lo que cobras hasta saldarla; se liquida en `/renta`",
                    debt,
                )
            )  # fmt: skip
        now = self.economy.now()
        statement = await self.economy.irpf_statement(guild_id, user_id)
        if statement is not None and not statement.opened:
            due, _second = installment_deadlines(fiscal_week(statement.week))
            text = "Renta de la semana sin abrir: ábrela en `/renta`"
            if now < due:
                text += "; si sale a pagar, hay plazo hasta el final del lunes"
            items.append(
                Pending(Side.PAY, "renta_sealed", "📬", text, 0, due if now < due else None)
            )
            return items
        if statement is not None:
            filed = statement.filed
            if filed.payment == PAYMENT_PENDING:
                items.append(
                    Pending(
                        Side.PAY, "renta_due", "💸",
                        f"Renta: {format_amount(filed.result)} a pagar · entera o fraccionada "
                        "en `/renta`; si no eliges, se cobra entera",
                        filed.result, filed.pay_due,
                    )
                )  # fmt: skip
            elif filed.payment == PAYMENT_SPLIT and not filed.second_at:
                items.append(
                    Pending(
                        Side.PAY, "renta_second", "✂️",
                        f"Segundo plazo de la Renta: {format_amount(filed.second)}",
                        filed.second, filed.second_due,
                    )
                )  # fmt: skip
        declarations = await self.economy.pending_declarations(guild_id, user_id)
        if total := sum(d.refund for d in declarations):
            # shortcut: cuenta el plazo desde la semana siguiente a la declarada; si el
            # cierre llegó tarde (bot apagado), caduca algo después de lo que dice.
            oldest = min(d.week_start for d in declarations)
            items.append(
                Pending(
                    Side.COLLECT, "renta_refund", "📬",
                    f"Devolución de la Renta: {format_amount(total)} · preséntala en `/renta` "
                    "antes de que caduque",
                    total, monday_epoch(oldest + timedelta(weeks=1 + CLAIM_WEEKS)),
                )
            )  # fmt: skip
        return items

    async def remind(self, interaction: discord.Interaction) -> None:
        """Tras una jugada con botón o slash: avisa en efímero, una vez por campaña.

        Debe llamarse cuando la interacción ya tiene respuesta: el aviso va
        como mensaje de seguimiento que solo ve quien ha jugado. Si la última
        declaración está sin abrir, no avisa: el sobre se abre en `renta`.
        """
        guild = interaction.guild
        if guild is None or not interaction.response.is_done():
            return
        campaign = week_start(local_day(time.time()))
        key = (guild.id, interaction.user.id, campaign)
        if key in self._reminded:
            return
        try:
            if await self._sealed(guild.id, interaction.user.id):
                return
            pending = await self.economy.pending_declarations(guild.id, interaction.user.id)
        except Exception:
            logger.exception("No se pudo consultar la renta pendiente")
            return
        if not pending:
            return
        self._reminded.add(key)
        try:
            await interaction.followup.send(
                draft_text(pending),
                view=PresentView(self, interaction.user.id),
                ephemeral=True,
            )
        except discord.HTTPException:
            logger.warning("No se pudo enviar el aviso de renta", exc_info=True)

    def track_opened(self, interaction: discord.Interaction, statement: IrpfStatement) -> None:
        """Apunta a los logros que ha abierto una declaración (y qué le salió)."""
        if interaction.guild is None:
            return
        result = statement.filed.result
        logros.note(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            StatDelta(
                add={
                    "renta_opened": 1,
                    "renta_opened_pay": int(result > 0),
                    "renta_opened_refund": int(result < 0),
                }
            ),
            interaction.channel_id,
        )

    # -- Presentar ---------------------------------------------------------------------

    async def present(self, interaction: discord.Interaction) -> None:
        """Cobra la devolución y lo anuncia en el canal."""
        guild = interaction.guild
        if guild is None:
            return
        # Cobrar la devolución va a la base de datos: se acepta el clic antes.
        await ack(interaction)
        claim = await self.economy.claim_declarations(guild.id, interaction.user.id)
        if not claim.declarations:
            await edit(interaction, content="No tienes ninguna declaración pendiente.", view=None)
            return
        await edit(
            interaction,
            content=(
                f"✅ Declaración presentada: +{format_amount(claim.refunded)}. "
                f"Saldo: **{format_amount(claim.balance)}**."
            ),
            embed=None,
            view=None,
        )
        weeks = len(claim.declarations)
        extra = f" ({weeks} semanas)" if weeks > 1 else ""
        channel = interaction.channel
        if channel is not None and hasattr(channel, "send"):
            try:
                await channel.send(  # type: ignore[union-attr]
                    f"📬 **{discord.utils.escape_markdown(interaction.user.display_name)}** "
                    f"presenta la renta: le salen **{format_amount(claim.refunded)}** "
                    f"a devolver{extra}.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning("No se pudo anunciar la declaración", exc_info=True)
        await logros.track(
            self.bot,
            guild.id,
            interaction.user,
            channel,
            StatDelta(
                add={"renta_filed": 1, "renta_refunded": claim.refunded},
                peak={"balance_max": claim.balance},
            ),
        )

    # -- Pagar ---------------------------------------------------------------------------

    async def pay(self, interaction: discord.Interaction, view: RentaView, *, split: bool) -> None:
        """Paga la declaración abierta, entera o fraccionada, y rehace la vista."""
        guild = interaction.guild
        if guild is None or view.statement is None:
            return
        # Pagar va a la base de datos: se acepta el clic antes.
        await ack(interaction)
        payment = await self.economy.pay_irpf_return(
            guild.id, interaction.user.id, view.statement.week, split=split
        )
        fresh = await self._view(
            guild, interaction.user, mode=view.mode, notice=payment_text(payment)
        )
        await edit(interaction, embed=fresh.embed(), view=fresh)
        if payment is not None:
            self._note_payment(guild.id, payment, interaction.channel_id, split=split)

    async def settle_debt(self, interaction: discord.Interaction, view: RentaView) -> None:
        """Liquida la deuda con Hacienda con lo que haya en el monedero."""
        guild = interaction.guild
        if guild is None:
            return
        await ack(interaction)
        paid, left, balance = await self.economy.pay_tax_debt(guild.id, interaction.user.id)
        # Lo pagado a mano no es un embargo: que `hint` no lo avise como tal.
        self._seen[(guild.id, interaction.user.id)] = self.economy.now()
        fresh = await self._view(
            guild, interaction.user, mode=view.mode, notice=debt_payment_text(paid, left, balance)
        )
        await edit(interaction, embed=fresh.embed(), view=fresh)
        if paid:
            logros.note(
                self.bot,
                guild.id,
                interaction.user.id,
                StatDelta(add={"debt_paid": paid}, peak={"balance_max": balance}),
                interaction.channel_id,
            )

    def _note_payment(
        self, guild_id: int, payment: IrpfPayment, channel_id: int | None, *, split: bool = False
    ) -> None:
        """Apunta a los logros un plazo de la Renta: lo pagado, la deuda y si se fraccionó."""
        add = {"renta_paid": payment.charged, "renta_debt": payment.debt, "renta_split": int(split)}
        logros.note(
            self.bot,
            guild_id,
            payment.user_id,
            StatDelta(add={k: v for k, v in add.items() if v}),
            channel_id,
        )

    # -- Comando -----------------------------------------------------------------------

    async def _view(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        *,
        mode: str | None = None,
        notice: str = "",
    ) -> RentaView:
        """La vista de `renta` de un miembro: su última declaración, lo pendiente y la deuda.

        Args:
            mode: El modo en que se enseña (sin él, el sobre si está sin abrir).
            notice: Lo que acaba de pasar (un pago), arriba del resto de avisos.
        """
        statement = await self.economy.irpf_statement(guild.id, member.id)
        pending = await self.economy.pending_declarations(guild.id, member.id)
        rate = await self.economy.irpf_rate(guild.id, member.id)
        debt = await self.economy.tax_debt(guild.id, member.id)
        # La base del ahorro (intereses) se liquida sola cada lunes; aquí solo se enseña.
        savings = await intereses.savings_line(self.bot, guild.id, member.id) or ""
        if mode is None:
            mode = SEALED if statement is not None and not statement.opened else SUMMARY
        extra = "\n\n".join(part for part in (notice, savings) if part)
        return RentaView(
            self, member.id, statement, pending, mode=mode, rate=rate, extra=extra, debt=debt
        )

    @app_commands.command(name="renta", description="Mira y presenta tu declaración de la renta.")
    @app_commands.guild_only()
    async def renta(self, interaction: discord.Interaction) -> None:
        """Tu declaración: el sobre (si está sin abrir), el resumen o el detalle."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "La renta solo se presenta dentro de un servidor.", ephemeral=True
            )
            return
        # Leer la declaración va a la base de datos: se contesta antes.
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = await self._view(interaction.guild, interaction.user)
        await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)

    @commands.command(name="renta")
    @commands.guild_only()
    async def renta_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.renta`); se ve en el canal porque no puede ser efímera."""
        if ctx.guild is None:
            return
        view = await self._view(ctx.guild, ctx.author)
        await ctx.send(embed=view.embed(), view=view)

    # -- Cierre del lunes y evento semanal ---------------------------------------------

    @tasks.loop(minutes=10)
    async def _campaign_events(self) -> None:
        """Cierra las semanas que falten y mantiene un evento de Discord por campaña."""
        now = datetime.now(TIMEZONE)
        monday = week_start(now.date())
        name = f"{EVENT_PREFIX} · semana {week_label(monday - timedelta(days=7))}"
        end = datetime.combine(monday + timedelta(days=7), datetime.min.time(), TIMEZONE)
        for guild in self.bot.guilds:
            await self._close_weeks(guild)
            await self._settle_payments(guild)
            if guild.id in self._events_forbidden:
                continue
            try:
                await self._sync_guild_event(guild, name, now, end)
            except discord.Forbidden:
                self._events_forbidden.add(guild.id)
                logger.warning(
                    "Sin permiso para gestionar eventos en el servidor %s; "
                    "la renta funciona sin evento.",
                    guild.id,
                )
            except Exception:
                logger.exception("Error con el evento de la renta en el servidor %s", guild.id)

    async def _close_weeks(self, guild: discord.Guild) -> None:
        """Cierra el IRPF de las semanas acabadas que falten y lo anuncia. Nunca lanza."""
        try:
            closed = await self.economy.close_irpf_weeks(guild.id)
        except Exception:
            logger.exception("No se pudo cerrar el IRPF en el servidor %s", guild.id)
            return
        if not closed:
            return
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        for week, run in closed:
            if not run.returns:
                continue
            if channel is not None:
                try:
                    await channel.send(
                        announcement_text(week, run),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.warning("No se pudo anunciar la renta del lunes", exc_info=True)
            for filed in run.returns:
                logros.note(
                    self.bot,
                    guild.id,
                    filed.user_id,
                    StatDelta(
                        add={"renta_closed": 1},
                        peak={"renta_rate_max": round(filed.rate * 100)},
                    ),
                    channel.id if channel is not None else None,
                )

    async def _settle_payments(self, guild: discord.Guild) -> None:
        """Cobra los plazos vencidos de la Renta y los apunta a los logros. Nunca lanza.

        Las que nadie pagó al acabar el lunes se cobran enteras, y las fraccionadas, su 40 %
        al acabar el jueves. Cada miembro se entera con `hint` en su siguiente acción.
        """
        try:
            payments = await self.economy.settle_irpf_payments(guild.id)
        except Exception:
            logger.exception("No se pudieron cobrar los plazos de la Renta en %s", guild.id)
            return
        for payment in payments:
            self._note_payment(guild.id, payment, None)

    @_campaign_events.before_loop
    async def _before_campaign_events(self) -> None:
        await self.bot.wait_until_ready()

    async def _sync_guild_event(
        self, guild: discord.Guild, name: str, now: datetime, end: datetime
    ) -> None:
        current: discord.ScheduledEvent | None = None
        for event in guild.scheduled_events:
            if not event.name.startswith(EVENT_PREFIX):
                continue
            if event.name == name:
                current = event
            elif event.status is discord.EventStatus.active:
                await event.end()
            elif event.status is discord.EventStatus.scheduled:
                await event.cancel()
        if current is None:
            await guild.create_scheduled_event(
                name=name,
                start_time=now + timedelta(minutes=1),
                end_time=end,
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
                location="/renta o jugando en el casino",
                description=(
                    "Ya ha salido la declaración de la semana pasada: ábrela con /renta. Si "
                    f"era a pagar, {TAX_COLLECTOR} ya se la ha cobrado; si es a devolver, "
                    f"preséntala allí. Hay {CLAIM_WEEKS} semanas para presentarla; después "
                    "prescribe."
                ),
            )
        elif current.status is discord.EventStatus.scheduled and current.start_time <= now:
            await current.start()


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Renta(bot, bot.economy))
