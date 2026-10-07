"""Bizum entre miembros: `bizum <miembro> <cantidad> [concepto]`.

Pasa yapdollars de quien lo manda a otro miembro del servidor, al momento y
entero: está exento de Donaciones y sin IRPF (decisión del proyecto, norma real
y matices en `bot.services.bizum`). El mínimo es el de Bizum (0,50 € = 5 Y$).
Los máximos españoles (1.000 € por operación y 2.000 € al día) no se aplican:
el resultado avisa de que te has pasado y los logros «A espaldas de Sánchez» y
«Límite diario» lo premian.

El resultado es público y menciona a quien recibe, para que se entere. No se
puede mandar a uno mismo ni a un bot. Mandar dinero es gastarlo, así que en
`/bizum` lleva el aviso de la Renta. Alimenta los logros de la categoría Bizum,
tanto de quien envía como de quien recibe.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.services.achievements import bizum_received_stats, bizum_stats
from bot.services.bizum import MAX_DAILY, MAX_OPERATION, check_limits, clean_concept
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    parse_amount,
)
from bot.services.pets import Event, Moment
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Turquesa de Bizum.
COLOR = discord.Color.from_rgb(5, 195, 221)


class Bizum(commands.Cog):
    """Transferencias de yapdollars entre miembros, exentas de impuestos."""

    def __init__(self, bot: commands.Bot, economy: EconomyService) -> None:
        self.bot = bot
        self.economy = economy

    async def _bizum_impl(
        self,
        responder: CommandResponder,
        sender: discord.abc.User,
        receiver: discord.abc.User,
        amount_text: str,
        concept_text: str | None,
    ) -> bool:
        """Valida, mueve el dinero, enseña el resultado y apunta los logros.

        Returns:
            Si se ha mandado el Bizum (para el aviso de la Renta).
        """
        guild = responder.guild
        if guild is None:
            await responder.send_error("Los Bizums solo van dentro de un servidor.")
            return False
        if receiver.bot:
            await responder.send_error("Los bots no tienen Bizum, mi amor. Ni cuenta en el banco.")
            return False
        balance = await self.economy.balance(guild.id, sender.id)
        try:
            amount = parse_amount(amount_text, balance)
            receipt = await self.economy.bizum(guild.id, sender.id, receiver.id, amount=amount)
        except ValueError as error:
            await responder.send_error(str(error))
            return False
        except InsufficientFundsError as error:
            await responder.send_error(
                f"¡Ay, bendito! No te llega: tienes {format_amount(error.balance)}."
            )
            return False
        except BalanceLimitError:
            await responder.send_error("A esa persona ya no le cabe más dinero en la cuenta.")
            return False

        limits = check_limits(amount, receipt.sent_today)
        sender_name = discord.utils.escape_markdown(sender.display_name)
        lines = [
            f"💸 **{sender_name}** le hace un Bizum de **{format_amount(amount)}** a "
            f"{receiver.mention}.",
        ]
        if concept := clean_concept(concept_text):
            lines.append(f"> 📝 {discord.utils.escape_markdown(concept)}")
        lines.append(f"{CURRENCY_EMOJI} Te quedan **{format_amount(receipt.sender_balance)}**")
        if limits.over_operation:
            lines.append(
                f"🤫 Eso pasa del máximo de {format_amount(MAX_OPERATION)} por Bizum. En "
                "España el banco te lo para; aquí no se ha enterado nadie."
            )
        elif limits.over_daily:
            lines.append(
                f"🤫 Hoy llevas {format_amount(receipt.sent_today)} en Bizums y el máximo "
                f"diario es {format_amount(MAX_DAILY)}. Shhh."
            )
        lines.append(
            f"-# 🐶 {TAX_COLLECTOR} no toca ni un yapdólar: los Bizums entre colegas del "
            "servidor están exentos de Donaciones."
        )
        hint = await renta.hint(self.bot, guild.id, sender.id, Moment(Event.BIZUM))
        if hint is not None:
            lines.append(hint)
        # Las menciones dentro de un embed no avisan: la de quien recibe va también en el
        # texto, y es la única permitida.
        await responder.send(
            receiver.mention,
            embed=discord.Embed(description="\n".join(lines), color=COLOR),
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[receiver]),
        )
        await logros.track(
            self.bot,
            guild.id,
            sender,
            responder.channel,
            bizum_stats(
                amount=amount,
                sent_today=receipt.sent_today,
                balance_after=receipt.sender_balance,
            ),
        )
        await logros.track(
            self.bot,
            guild.id,
            receiver,
            responder.channel,
            bizum_received_stats(amount=amount, balance_after=receipt.receiver_balance),
        )
        return True

    @app_commands.command(
        name="bizum", description="Manda yapdollars a otro miembro, sin impuestos."
    )
    @app_commands.describe(
        destinatario="A quién se lo mandas.",
        cantidad="Cuánto: 500, 2k, mitad, all…",
        concepto="Para qué es (opcional, lo ve todo el canal).",
    )
    @app_commands.guild_only()
    async def bizum(
        self,
        interaction: discord.Interaction,
        destinatario: discord.Member,
        cantidad: str,
        concepto: str | None = None,
    ) -> None:
        """Manda un Bizum. Público; menciona a quien lo recibe."""
        sent = await self._bizum_impl(
            InteractionResponder(interaction), interaction.user, destinatario, cantidad, concepto
        )
        if sent:
            # Mandar dinero es gastarlo: gancho de la Renta (ver Biblia.txt, sección 4).
            await renta.remind(self.bot, interaction)

    @commands.command(name="bizum")
    @commands.guild_only()
    async def bizum_text(
        self,
        ctx: commands.Context,
        destinatario: discord.Member,
        cantidad: str,
        *,
        concepto: str | None = None,
    ) -> None:
        """Versión de texto: `.bizum @miembro 500 la pizza de ayer`."""
        await self._bizum_impl(ContextResponder(ctx), ctx.author, destinatario, cantidad, concepto)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Bizum(bot, bot.economy))
