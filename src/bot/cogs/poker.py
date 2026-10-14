"""Póker (Casino Hold'em): `poker`, Texas Hold'em contra la banca con una sola decisión.

Cada jugador abre su propia mesa, que solo él toca. `poker 500` deja el ante en
500 Y$ y espera a 🃏 **Repartir**, que cobra el ante y reparte: dos cartas para
ti, dos boca abajo para la banca y el flop. Con eso a la vista se decide:
✅ **Ir** pone el doble del ante y se ve el final; 🏳️ **Retirarse** pierde el
ante. Si vas, la banca juega con pareja de cuatros o mejor; si no juega, el
ante cobra según la tabla y la apuesta de ir se devuelve. Reglas y pagos en
`bot.services.poker`.

Cada mano son dos GIF: el reparto y el final. Las nueve cartas se sortean antes
de pulsar y no dependen de lo que se elija, así que la mesa pinta el reparto de
la mano siguiente en cuanto termina la anterior y los dos finales (ir y
retirarse) mientras el jugador mira el flop. Al pulsar solo queda subir el GIF.
Lo pinta `bot.services.poker_scene` (Node, con Chromium y Pillow de reserva).

Al retirarse, el texto dice qué habría pasado yendo («🔮 Yendo habrías ganado
1.200 Y$»), que es lo que hace volver a jugar.

Dinero: juego. El ante se cobra al repartir y la apuesta de ir al ir
(`place_bet`); se paga al terminar la mano (`pay_winnings`, con 0 si se pierde
o se retira), que es cuando se ajusta el IRPF del día (art. 33 LIRPF: los
premios del juego son ganancia patrimonial). Si la mesa caduca (3 min sin
tocarla) o el bot se apaga de forma ordenada con una mano a medias, se
retira sola: ir pondría dinero que el jugador no ha decidido poner, y
devolver el ante dejaría mirar el flop gratis.

Si `CASINO_CHANNEL_IDS` está configurado, solo se juega en esos canales.
Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar
archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import casino_stats, poker_stats
from bot.services.blackjack import Card
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.services.poker import (
    CALL_FACTOR,
    GAME,
    Deal,
    Outcome,
    PokerError,
    PokerHand,
    Rank,
    ante_pays,
    deal,
    payout,
    paytable,
)
from bot.services.poker_render import Media, Showdown
from bot.services.poker_scene import PokerScene
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

DEFAULT_STAKE = 100
VIEW_TIMEOUT = 180
#: Desde esta jugada (full) se anuncia en el canal la mano ganada.
SHOUT_RANK = Rank.FULL_HOUSE
#: Margen tras el GIF antes de poner el PNG: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
GIF_NAME = "poker.gif"
PNG_NAME = "poker.png"

COLOR_IDLE = discord.Color.from_rgb(88, 101, 242)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)

# Textos en el tono de Jovani Vázquez.
DEALING_LINES = (
    "🃏 La banca baraja…",
    "🃏 Reparte el croupier del Casino del Estado…",
    "🎩 Se reparte con guante blanco, como en el Consejo de Ministros…",
    "📜 Un notario de Hacienda vigila la baraja…",
    "🕶️ Gafas de sol puestas, cara de póker…",
    "📺 El NO-DO retransmite el reparto…",
    "🍹 Jugo de china del bueno y a repartir…",
)
CALLING_LINES = (
    "✅ ¡Vas! Salen el turn y el river…",
    "👀 La banca levanta las cartas…",
    "🙏 Que no juegue la banca, que no juegue…",
    "🎙️ «Voy», que diría Rajoy, «y es el vecino el que elige al alcalde»…",
    "🛬 Aterriza el Falcon con el river…",
    "😬 Tezanos ya tiene la encuesta del river…",
)
FOLDING_LINES = (
    "🏳️ Tiras las cartas…",
    "🏳️ Retirada estratégica, como de Afganistán…",
    "🚪 Te vas, como Puigdemont en el maletero…",
    "🏳️ Dimites antes de que te cesen…",
)
WIN_LINES = (
    "¡Wepa!",
    "¡Le ganas a la banca!",
    "¡Eso es, mi amor!",
    "¡Ni Felipe en el 82!",
    "¡Toma mangó!",
    "¡La banca paga, por una vez!",
    "¡Mayoría absoluta en el tapete!",
    "¡Cara de póker y bolsillo lleno!",
)
NO_QUALIFY_LINES = (
    "¡La banca no se presenta!",
    "¡La banca se queda en Waterloo!",
    "¡Incomparecencia de la banca!",
    "¡La banca no da ni para pareja de cuatros!",
    "¡La banca se ha ido a por café! ¡Quiero café!",
)
TIE_LINES = (
    "¡Empate!",
    "¡Empate técnico, según el CIS!",
    "¡Gran coalición en el tapete!",
    "¡Tablas, como en el Congreso!",
)
LOSE_LINES = (
    "¡Ay, bendito!",
    "¡La banca siempre gana!",
    "¡Rescate bancario, pero para la banca!",
    "¡Sé fuerte!",
    "¡Te pilló la UCO!",
    "¡Estoy destruido, pero por fuera!",
    "¡Hacienda somos todos, y la banca más!",
)
FOLD_LINES = (
    "Te retiras",
    "Retirada a tiempo",
    "Dimisión aceptada",
    "Te bajas del carro",
    "Me piro, vampiro",
)


def cards_text(cards: tuple[Card, ...]) -> str:
    """`A♠ K♠`: las cartas en negrita para el texto."""
    return " ".join(f"**{card}**" for card in cards)


class PokerView(ui.View):
    """Mesa de un jugador: imagen, texto, botones y la precarga de los GIF.

    Guarda la mano en curso (o la última), el ante y las cartas de la mano
    siguiente (`cards`), que se sortean antes de repartir para dibujar su
    animación por adelantado. No guarda dinero: se cobra y se paga siempre por
    la economía.
    """

    def __init__(self, cog: Poker, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.hand: PokerHand | None = None
        self.balance = 0
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.channel: object = None
        self.started_at = 0.0
        self._busy = False
        self._lock = asyncio.Lock()
        self._last_interaction: discord.Interaction | None = None
        #: El reparto de `cards` y sus dos finales, dibujándose por adelantado.
        self._deal: asyncio.Task[Media] | None = None
        self._showdown: asyncio.Task[Showdown] | None = None
        #: Cartas de la mano que se está jugando o de la siguiente, su semilla de
        #: vuelo y el tapete del dueño (`tienda.skin_params`), que vale toda la mano.
        self.cards: Deal
        self.seed = 0
        self.skin: dict[str, object] = {}
        self.new_deck()

    # -- Precarga ---------------------------------------------------------------------

    def new_deck(self) -> None:
        """Sortea las cartas de la próxima mano y empieza a dibujar su reparto."""
        self.cancel_drawings()
        self.skin = tienda.skin_params(self.cog.bot, self.guild_id, self.owner.id, GAME)
        self.cards = deal(self.cog.rng)
        self.seed = self.cog.rng.randrange(1, 2**31)
        self._deal = asyncio.create_task(
            self.cog.renderer.deal(self.cards, seed=self.seed, skin=self.skin)
        )

    def prepare_showdown(self) -> None:
        """Empieza a dibujar los dos finales, después del reparto (de uno en uno)."""
        if self._showdown is not None:
            return
        cards, seed, skin, dealing = self.cards, self.seed, self.skin, self._deal

        async def paint() -> Showdown:
            if dealing is not None:
                await asyncio.gather(dealing, return_exceptions=True)
            return await self.cog.renderer.showdown(cards, seed=seed, skin=skin)

        self._showdown = asyncio.create_task(paint())

    def cancel_drawings(self) -> None:
        """Cancela los dibujos pendientes (mano terminada o mesa cerrada)."""
        for task in (self._deal, self._showdown):
            if task is not None:
                task.cancel()
        self._deal = self._showdown = None

    # -- Texto ------------------------------------------------------------------------

    def description(self) -> str:
        """Texto del embed según cómo va la mano."""
        hand = self.hand
        lines: list[str] = []
        if hand is not None and hand.deciding:
            cards = hand.cards
            lines.append(f"# 🃏 Llevas {cards.flop_value.label.lower()}")
            lines.append(f"Tus cartas: {cards_text(cards.player)} · Flop: {cards_text(cards.flop)}")
            lines.append(
                f"✅ **Ir** pone {format_amount(hand.call_stake)} más y se ve el final. "
                f"🏳️ **Retirarse** pierde el ante ({format_amount(hand.ante)})."
            )
            lines.append("-# La banca juega con pareja de cuatros o mejor.")
        else:
            lines.extend(self.result_lines(hand))
        if self.note:
            lines.append(self.note)
        lines.append(self.footer())
        if hand is None or not hand.deciding:
            if self.balance == 0:
                lines.append("**Estás a cero.** `imv` te recarga.")
            elif self.balance < self.stake * (1 + CALL_FACTOR):
                lines.append(
                    f"-# Ojo: para ir hacen falta {format_amount(self.stake * (1 + CALL_FACTOR))} "
                    "en total. Si no te llega, solo podrás retirarte."
                )
        return "\n".join(lines)

    def result_lines(self, hand: PokerHand | None) -> list[str]:
        """Cómo acabó la última mano (o la bienvenida, si no hubo)."""
        if hand is None:
            return [
                "# ♠️ ¿Una mano contra la banca?",
                "Casino Hold'em: pones el ante, ves tus dos cartas y el flop, y decides si "
                "vas (el doble del ante) o te retiras. Si la banca no juega, cobras el ante "
                "según la tabla.\n-# 📋 Tabla para ver los pagos. La casa se queda de media "
                "un 2,16 % del ante.",
            ]
        cards = hand.cards
        player, dealer = cards.player_value, cards.dealer_value
        mine = f"Tu **{player.label.lower()}**"
        theirs = f"**{dealer.label.lower()}** de la banca"
        if not hand.called:
            would = payout(cards, hand.ante, called=True) - hand.ante * (1 + CALL_FACTOR)
            if would > 0:
                hint = f"🔮 Yendo habrías ganado {format_amount(would)}."
            elif would == 0:
                hint = "🔮 Yendo habría sido empate."
            else:
                hint = f"🔮 Bien hecho: yendo habrías perdido {format_amount(-would)}."
            return [
                f"# 🏳️ {random.choice(FOLD_LINES)} -{format_amount(hand.ante)}",
                f"{mine} contra {theirs}.",
                hint,
            ]
        net = hand.net
        match hand.outcome:
            case Outcome.WIN:
                return [
                    f"# 🏆 {random.choice(WIN_LINES)} +{format_amount(net)}",
                    f"{mine} gana a {theirs}. El ante cobra {ante_pays(player.rank)} a 1 "
                    "y la de ir, 1 a 1.",
                ]
            case Outcome.NO_QUALIFY:
                return [
                    f"# 💰 {random.choice(NO_QUALIFY_LINES)} +{format_amount(net)}",
                    f"La banca solo lleva {dealer.label.lower()}. Con tu "
                    f"**{player.label.lower()}**, el ante cobra {ante_pays(player.rank)} a 1 "
                    "y la de ir se devuelve.",
                ]
            case Outcome.TIE:
                return [
                    f"# 🤝 {random.choice(TIE_LINES)}",
                    f"{mine} empata con {theirs}: se devuelve todo.",
                ]
        return [
            f"# 💥 {random.choice(LOSE_LINES)} -{format_amount(-net)}",
            f"{theirs[0].upper()}{theirs[1:]} gana a tu **{player.label.lower()}**.",
        ]

    def footer(self) -> str:
        """Línea pequeña: ante, lo que cuesta ir y saldo."""
        hand = self.hand
        ante = hand.ante if hand is not None and hand.deciding else self.stake
        return (
            f"-# Ante {format_amount(ante)} · Ir {format_amount(ante * CALL_FACTOR)} · "
            f"Saldo {format_amount(self.balance)}"
        )

    def color(self) -> discord.Color:
        hand = self.hand
        if hand is None or hand.deciding:
            return COLOR_IDLE
        if not hand.called or hand.outcome is Outcome.LOSE:
            return COLOR_LOST
        return COLOR_WIN

    def embed(
        self, *, image: str = PNG_NAME, text: str | None = None, color: discord.Color | None = None
    ) -> discord.Embed:
        """Embed con la imagen adjunta (`attachment://`)."""
        embed = discord.Embed(
            title=f"♠️ Póker · {self.owner.display_name}",
            description=text if text is not None else self.description(),
            color=color or self.color(),
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def waiting_text(self, lines: tuple[str, ...]) -> str:
        """Texto mientras se ve el GIF, preparado ANTES de jugar: no delata nada."""
        return f"# {random.choice(lines)}\n{self.footer()}"

    # -- Componentes ------------------------------------------------------------------

    def rebuild(self, *, busy: bool = False) -> None:
        """Vuelve a montar los botones con el estado actual."""
        self.clear_items()
        hand = self.hand
        if hand is not None and hand.deciding:
            self._button(
                f"✅ Ir · {format_amount(hand.call_stake)}",
                "call",
                self._call,
                style=discord.ButtonStyle.success,
                row=0,
                disabled=busy,
            )
            self._button(
                "🏳️ Retirarse",
                "fold",
                self._fold,
                style=discord.ButtonStyle.danger,
                row=0,
                disabled=busy,
            )
        else:
            self._button(
                f"🃏 Repartir · {format_amount(self.stake)}",
                "deal",
                self._deal_click,
                style=discord.ButtonStyle.primary,
                row=0,
                disabled=busy,
            )
            self._button("½", "half", self._halve, row=1, disabled=busy)
            self._button("×2", "x2", self._double, row=1, disabled=busy)
            self._button("💰 All-in", "allin", self._all_in, row=1, disabled=busy)
        self._button("📋 Tabla", "table", self._paytable, row=1)
        # El 🎨: elegir o comprar el tapete de esta mesa (no cambia la jugada).
        if (paint := tienda.skin_button(self.cog.bot, GAME, row=1)) is not None:
            paint.disabled = busy
            self.add_item(paint)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label, style=style, disabled=disabled, custom_id=f"{GAME}:{custom_id}", row=row
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def disable_all(self) -> None:
        """Apaga todos los botones (mesa caducada o GIF en marcha)."""
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa (la tabla de pagos la puede ver cualquiera)."""
        if interaction.user.id == self.owner.id:
            return True
        data: Any = interaction.data or {}
        if data.get("custom_id") == f"{GAME}:table":
            return True
        await interaction.response.send_message(
            f"Esta mesa es de {self.owner.display_name}. Abre la tuya con `poker`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había una mano a medias, se retira sola."""
        self.cog.views.discard(self)
        self.cancel_drawings()
        await self.force_settle()
        self.rebuild()
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(embed=self.embed(), view=self)
            elif self.message is not None:
                await self.message.edit(embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa del póker", exc_info=True)

    async def force_settle(self) -> None:
        """Retira la mano a medias (ir pondría dinero que nadie ha decidido poner)."""
        async with self._lock:
            hand = self.hand
            if hand is None or not hand.deciding:
                return
            hand.fold()
            settlement = await self._settle(hand)
        await self._after_hand(None, hand, settlement)

    # -- Dinero y mano ----------------------------------------------------------------

    async def _settle(self, hand: PokerHand) -> BetSettlement | None:
        """Paga la mano decidida (0 si se retiró o perdió) y prepara la nota fiscal."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=hand.payout
            )
        except BalanceLimitError:
            logger.warning("Premio del póker por encima del saldo máximo; no se paga.")
            self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
            return None
        self.balance = settlement.balance
        notes = []
        if tax := gambling_tax_line(settlement):
            notes.append(tax)
        if hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=hand.staked, net=hand.net, balance_after=settlement.balance),
        ):
            notes.append(hint)
        self.note = "\n".join(notes) or None
        return settlement

    async def _after_hand(
        self,
        interaction: discord.Interaction | None,
        hand: PokerHand,
        settlement: BetSettlement | None,
    ) -> None:
        """Lo que va después de enseñar el final: renta, logros, estadísticas y anuncio."""
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        balance = settlement.balance if settlement else self.balance
        tax = settlement.tax_delta if settlement else 0
        delta = poker_stats(hand, when=datetime.now(TIMEZONE))
        delta.merge(
            casino_stats(stake=hand.staked, net=hand.net, balance_after=balance, tax_delta=tax)
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self.channel, delta, net=hand.net
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=hand.staked,
            net=hand.net,
            balance_after=balance,
            tax=tax,
            details=(
                ("made", hand.cards.player_value.rank.order),
                ("fold", int(not hand.called)),
                ("started", int(self.started_at)),
            ),
        )
        await self.cog.shout(hand, self.owner, self.channel)

    async def _show(
        self, editor: Callable[..., Awaitable[Any]], media: Media, *, waiting: str
    ) -> None:
        """Enseña el GIF y después el PNG con los botones del estado nuevo.

        Durante el GIF los botones siguen apagados con las etiquetas de antes:
        reconstruirlos delataría el resultado.
        """
        if media.gif is not None:
            await editor(
                # Color neutro: el del final delataría el resultado antes del GIF.
                embed=self.embed(image=GIF_NAME, text=waiting, color=COLOR_IDLE),
                attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        self.rebuild()
        await editor(
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _wait(
        self, task: Awaitable[Any], editor: Callable[..., Awaitable[Any]], waiting: str
    ) -> Any:
        """Lo que se está dibujando, tapando la espera si aún no está.

        Los botones se apagan ya, sin reconstruirlos. Si el GIF no está listo, la
        mesa enseña la jugada mientras tanto: si no, tras el clic no cambia nada y
        parece que el botón no responde.
        """
        self.disable_all()
        if isinstance(task, asyncio.Task) and task.done():
            return await task

        async def show_waiting() -> None:
            try:
                await editor(embed=self.embed(text=waiting, color=COLOR_IDLE), view=self)
            except discord.HTTPException:
                logger.warning("No se pudo apagar la mesa del póker", exc_info=True)

        _, result = await asyncio.gather(show_waiting(), task)
        return result

    async def deal_hand(
        self,
        editor: Callable[..., Awaitable[Any]],
        interaction: discord.Interaction | None = None,
    ) -> str | None:
        """Cobra el ante, reparte las cartas ya sorteadas y enseña el flop.

        Args:
            editor: Cómo se cambia el mensaje de la mesa.
            interaction: La del botón, para el aviso de la Renta.

        Returns:
            Un error para el usuario si no se pudo repartir, o `None`.
        """
        if self._busy or (self.hand is not None and self.hand.deciding):
            return None
        self._busy = True
        try:
            async with self._lock:
                try:
                    settlement = await self.cog.economy.place_bet(
                        self.guild_id, self.owner.id, game=GAME, stake=self.stake
                    )
                except InsufficientFundsError as error:
                    return insufficient_text(error.balance, self.stake)
                self.balance = settlement.balance
                # Para las porras: una mano empezada antes del cierre no cuenta.
                self.started_at = time.time()
                self.hand = PokerHand.new(self.stake, self.cog.rng, cards=self.cards)
                self.note = None
                if interaction is not None:
                    self._last_interaction = interaction
            if self._deal is None:
                self._deal = asyncio.create_task(
                    self.cog.renderer.deal(self.cards, seed=self.seed, skin=self.skin)
                )
            waiting = self.waiting_text(DEALING_LINES)
            media = await self._wait(self._deal, editor, waiting)
            # Los dos finales se dibujan mientras se ve el reparto y el jugador piensa.
            self.prepare_showdown()
            await self._show(editor, media, waiting=waiting)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar el reparto del póker", exc_info=True)
        finally:
            self._busy = False
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        return None

    async def decide(
        self,
        called: bool,
        editor: Callable[..., Awaitable[Any]],
        interaction: discord.Interaction | None = None,
    ) -> str | None:
        """Va (`called`) o se retira, paga la mano y enseña el final.

        Returns:
            Un error para el usuario si no se pudo (no le llega para ir), o `None`.
        """
        hand = self.hand
        if self._busy or hand is None or not hand.deciding:
            return None
        self._busy = True
        settlement: BetSettlement | None = None
        try:
            async with self._lock:
                if not hand.deciding:
                    return None
                if called:
                    try:
                        bet = await self.cog.economy.place_bet(
                            self.guild_id, self.owner.id, game=GAME, stake=hand.call_stake
                        )
                    except InsufficientFundsError as error:
                        return (
                            f"{insufficient_text(error.balance, hand.call_stake)} "
                            "Solo puedes retirarte."
                        )
                    self.balance = bet.balance
                try:
                    if called:
                        hand.call()
                    else:
                        hand.fold()
                except PokerError:
                    return None
                settlement = await self._settle(hand)
                if interaction is not None:
                    self._last_interaction = interaction
            self.prepare_showdown()
            assert self._showdown is not None
            waiting = self.waiting_text(CALLING_LINES if called else FOLDING_LINES)
            showdown = await self._wait(self._showdown, editor, waiting)
            self.new_deck()
            await self._show(editor, showdown.call if called else showdown.fold, waiting=waiting)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar el final del póker", exc_info=True)
        finally:
            self._busy = False
        await self._after_hand(interaction, hand, settlement)
        return None

    # -- Botones ----------------------------------------------------------------------

    async def _deal_click(self, interaction: discord.Interaction) -> None:
        """🃏 Repartir: cobra el ante y reparte."""
        await ack(interaction)

        async def editor(**kwargs: Any) -> None:
            await edit(interaction, **kwargs)

        if error := await self.deal_hand(editor, interaction):
            await notify(interaction, error)

    async def _decide_click(self, interaction: discord.Interaction, called: bool) -> None:
        await ack(interaction)

        async def editor(**kwargs: Any) -> None:
            await edit(interaction, **kwargs)

        if error := await self.decide(called, editor, interaction):
            await notify(interaction, error)

    async def _call(self, interaction: discord.Interaction) -> None:
        """✅ Ir: pone el doble del ante y se ve el final."""
        await self._decide_click(interaction, True)

    async def _fold(self, interaction: discord.Interaction) -> None:
        """🏳️ Retirarse: pierde el ante y enseña lo que habría pasado."""
        await self._decide_click(interaction, False)

    async def _paytable(self, interaction: discord.Interaction) -> None:
        """📋 La tabla del ante, en privado (solo memoria: se contesta directamente)."""
        rows = "\n".join(f"**{label}** · {pays}" for label, pays in paytable())
        embed = discord.Embed(
            title="📋 Tabla del póker",
            description=(
                "Pones el ante y ves tus dos cartas y el flop. **Ir** cuesta el doble del "
                "ante; **retirarse** pierde el ante. Si vas, cada uno juega su mejor mano de "
                "cinco con sus dos cartas y las cinco de la mesa.\n\n"
                "La banca juega con **pareja de cuatros o mejor**. Si no juega, el ante cobra "
                "según la tabla y la de ir se devuelve. Si juega y le ganas, el ante cobra "
                "según la tabla y la de ir paga 1 a 1; si empatáis, se devuelve todo.\n\n"
                "**El ante paga:**\n" + rows + "\n\n-# Con la mejor estrategia (ir casi "
                "siempre), la casa se queda de media un 2,16 % del ante."
            ),
            color=COLOR_IDLE,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _idle(self) -> bool:
        return not self._busy and (self.hand is None or not self.hand.deciding)

    async def _restake(self, interaction: discord.Interaction, stake: int) -> None:
        """Cambia el ante y repinta el texto y los botones (la imagen se queda)."""
        self.stake = max(1, stake)
        self.note = None
        self._last_interaction = interaction
        self.rebuild()
        await edit(interaction, embed=self.embed(), view=self)

    async def _halve(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self._idle():
            return
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        await self._restake(interaction, self.stake // 2)

    async def _double(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self._idle():
            return
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        await self._restake(interaction, min(self.stake * 2, max(1, self.balance)))

    async def _all_in(self, interaction: discord.Interaction) -> None:
        """💰 All-in: el mayor ante con el que aún se puede ir (un tercio del saldo)."""
        await ack(interaction)
        if not self._idle():
            return
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        if self.balance <= 0:
            await notify(interaction, insufficient_text(0))
            return
        await self._restake(interaction, self.balance // (1 + CALL_FACTOR))


# -- Cog -------------------------------------------------------------------------------


class Poker(commands.Cog, name="Póker"):
    """El póker (Casino Hold'em) con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        casino_channel_ids: frozenset[int] = frozenset(),
        rng: random.Random | None = None,
        renderer: PokerScene | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.renderer = renderer or PokerScene()
        # Mesas abiertas: para retirar sus manos si el bot se apaga.
        self.views: set[PokerView] = set()

    async def cog_unload(self) -> None:
        """Al apagar, retira las manos a medias, cancela los dibujos y cierra el pintor."""
        for view in list(self.views):
            view.cancel_drawings()
            try:
                await view.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una mano del póker al apagar")
            view.stop()
        self.views.clear()
        await self.renderer.close()

    async def shout(self, hand: PokerHand, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal las manos cobradas con full o mejor."""
        if not hand.called or hand.outcome not in (Outcome.WIN, Outcome.NO_QUALIFY):
            return
        player = hand.cards.player_value
        if player.rank.order < SHOUT_RANK.order or not isinstance(channel, discord.abc.Messageable):
            return
        text = (
            f"📣 ♠️ ¡{user.mention} liga **{player.label.lower()}** al póker y el ante "
            f"cobra {ante_pays(player.rank)} a 1! **+{format_amount(hand.net)}**"
        )
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar una mano del póker", exc_info=True)

    # -- poker ------------------------------------------------------------------------

    async def _poker_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/poker` y `.poker`: abre la mesa."""
        if guild is None:
            await send_error("El póker solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El póker"):
            await send_error(error)
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = (
                parse_amount(amount_text, balance)
                if amount_text
                else max(1, min(DEFAULT_STAKE, balance))
            )
        except ValueError as error:
            await send_error(str(error))
            return
        if balance <= 0 or stake > balance:
            await send_error(insufficient_text(balance, stake))
            return

        # La mesa vacía va antes que la mesa: al crearla empieza a dibujarse el
        # reparto, y el pintor dibuja de uno en uno.
        skin = tienda.skin_params(self.bot, guild.id, user.id, GAME)
        png = await self.renderer.board(skin=skin)
        view = PokerView(self, guild_id=guild.id, owner=user, stake=stake)
        view.channel = channel
        view.balance = balance
        view.rebuild()
        view.message = await send(
            embed=view.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=view
        )
        self.views.add(view)

    @app_commands.command(
        name="poker",
        description="Póker Casino Hold'em contra la banca: ves el flop y decides si vas.",
    )
    @app_commands.describe(cantidad="Ante: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def poker(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Abre una mesa de póker.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. El ante
        se cobra al repartir y la apuesta de ir al ir.
        """
        responder = InteractionResponder(interaction)

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._poker_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=responder.send_error,
        )

    @commands.command(name="poker")
    @commands.guild_only()
    async def poker_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.poker` o `.poker 500`."""
        responder = ContextResponder(ctx)

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._poker_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Poker(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
