"""Guerra (Casino War) con botones: comando `guerra`.

Sigue las mismas normas que el blackjack (`bot.cogs.blackjack`): mesa individual
que solo pulsa su dueño, dinero siempre a través de `EconomyService`, mismo
control de canales (`CASINO_CHANNEL_IDS`) y fichas con ½ / ×2 / All-in.
`.guerra 500` reparte al momento. Las reglas están en `bot.services.war`: una
carta para ti y otra para la banca, gana la más alta y, si empatan, eliges entre
🏳️ rendirte (media apuesta de vuelta) y ⚔️ ir a la guerra (otra apuesta igual). El
botón 🤝 Empate 10:1 suma una apuesta lateral igual a la ficha, que se cobra al
repartir.

El dinero en juego se cobra al repartir (y al ir a la guerra) y el premio se paga
al terminar la mano: `place_bet` + `pay_winnings`, así que tributa como juego
(retención diaria del IRPF y declaración semanal del casino, igual que el
blackjack). Si la mesa caduca o el bot se apaga con un empate sin decidir, la mano
se resuelve rindiéndose (es lo que menos pierde) para que nunca se quede dinero
colgado. Si el proceso muere de golpe a mitad de un empate, lo apostado en esa
mano se pierde (queda registrado en el libro de movimientos).

Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar
archivos (la imagen de la mesa).
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import casino_stats, war_stats
from bot.services.blackjack import Card, new_shoe
from bot.services.cards_render import CardRenderer, HandView
from bot.services.economy import (
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.pets import bet_moment
from bot.services.war import BURN_CARDS, TIE_PAYS, WAR_TIE_BONUS, Result, WarGame
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "guerra"
DEFAULT_STAKE = 100
TABLE_TIMEOUT = 180
#: Pausa mientras la banca quema las cartas de la guerra: lo justo para que se vea.
WAR_PAUSE_SECONDS = 0.8
PNG_NAME = "guerra.png"

COLOR_PLAYING = discord.Color.from_rgb(21, 83, 64)
COLOR_WAR = discord.Color.from_rgb(150, 40, 35)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_PUSH = discord.Color.from_rgb(120, 140, 160)

RULES_FOOTER = f"Gana la carta más alta · empate: rendirse o guerra · lateral {TIE_PAYS}:1"

#: Al ganar a la primera.
WIN_LINES = (
    "¡Tu carta manda!",
    "Carta más alta, bolsillo más lleno.",
    "La banca baja la cabeza.",
    "Ganas por un rango, como las votaciones de la Mesa del Congreso.",
    "Mayoría simple, y sin pactar con Junts.",
    "Ganas, y sin pasar por la Moncloa.",
    "Victoria por goleada de rango. Cobras, mi amor.",
    "Trump diría que has ganado por mucho más. Tú cobras lo de verdad.",
    "La banca pide un recuento. Cobras igual.",
    "Ganas, y el CIS ya lo había visto venir.",
    "Tu carta gobierna en solitario, sin hacer coaliciones.",
    "Ganas la mano como Ayuso gana en Madrid: sin discusión.",
)
#: Al perder a la primera.
LOSS_LINES = (
    "La banca tiene la carta más alta.",
    "Tu carta se queda corta, como el presupuesto.",
    "Perdiste por una carta. Casi nada, o sea, todo.",
    "La banca te pasa por encima como una directiva de Bruselas.",
    "Menos mal que no era a la guerra.",
    "Derrota por mayoría simple de rangos.",
    "Cartas como promesas electorales: altas las ajenas, bajas las tuyas.",
    "Ni el Falcon te saca de esta.",
    "La banca saca más que tú. Y no hay recurso.",
    "Perdiste. Pídele un recuento al Tribunal Supremo.",
    "La banca cobra y tú lo llamas aprendizaje.",
    "Capítulo uno del «Manual de resistencia», edición de bolsillo.",
)
#: Al aparecer el empate, mientras eliges.
TIE_LINES = (
    "Empate. Nadie parpadea.",
    "Mismo rango, distinto palo, igual problema.",
    "Empate técnico, como las encuestas del CIS cuando no sale su amigo.",
    "Dos cartas iguales y un dilema: la paz o la Guerra de las Galaxias.",
    "Empate. ¿Pacto o guerra? Como en cada investidura.",
    "Empate a todo, menos a nervios.",
    "Cartas iguales: a ver quién tiene más cara, como Rajoy en la moción.",
    "Empate. La diplomacia no paga, pero tampoco quema.",
)
#: Al rendirse.
SURRENDER_LINES = (
    "Bandera blanca, media ficha y a casa.",
    "Te rindes y recuperas la mitad. Dignidad, la justa.",
    "Retirada a tiempo: Rajoy lo llamaría estrategia.",
    "Te rindes. Gila ya había llamado al enemigo para avisar.",
    "Rendición con honores y con media ficha.",
    "Media ficha de vuelta. Puigdemont ha huido por menos.",
    "Pacto de última hora: pierdes la mitad y todos dicen que ganan.",
    "Te rindes como Sánchez ante los jueces: «ha sido un malentendido».",
)
#: Al ir a la guerra, mientras la banca quema las cartas.
WAR_LINES = (
    f"La banca quema {BURN_CARDS} cartas y reparte otras dos.",
    f"{BURN_CARDS} cartas al fuego, como los discos duros de Bárcenas.",
    "Guerra declarada. Trump firma un arancel por si acaso.",
    "Se declara la guerra. El CIS ya tiene la encuesta.",
    f"{BURN_CARDS} cartas quemadas y cero explicaciones, como en Moncloa.",
    "A la guerra, que el Falcon ya está calentando.",
    "Se quema el mazo y se decide el honor.",
)
#: Al ganar la guerra.
WAR_WIN_LINES = (
    "¡Ganas la guerra!",
    "Victoria sin bajas: ni un láser, como la Guerra de las Galaxias de Reagan.",
    "Guerra ganada, y te devuelven la ficha original.",
    "La guerra fría se calienta a tu favor.",
    "Guerra ganada. Trump lo celebra como si fuera suyo.",
    "Ganas la guerra cultural, que es la única que se gana sin leer.",
    "Ganas la guerra, y la banca firma el armisticio.",
)
#: Al perder la guerra.
WAR_LOSE_LINES = (
    "Guerra perdida: las dos fichas, a la banca.",
    "Guerra comercial perdida: arancel doble.",
    "Pierdes la guerra de las cifras: el CIS dice que ganaste.",
    "Dos fichas menos y mil excusas más.",
    "La banca arrasa. Ay, bendito.",
    "Perdiste la guerra cultural y también la otra.",
    "Guerra perdida. Si lo cuenta el NO-DO, es una victoria heroica.",
)
#: Si la guerra vuelve a empatar.
WAR_TIE_LINES = (
    "¡Doble empate! La guerra paga premio.",
    "Empate en la guerra: cobras la guerra, la original y el premio.",
    "Guerra fría con premio: no gana nadie, pero cobras tú.",
    "Segundo empate y la banca te da un premio extra sin saber por qué.",
    "Doble empate. Hasta Tezanos lo habría llamado mayoría absoluta.",
    "Tablas en la guerra, y tú con el botín.",
)
#: Acierto y fallo de la lateral de empate.
SIDE_HIT_LINES = (
    "¡La lateral acierta!",
    "Apostaste al empate y el empate vino.",
    "La lateral lo vio venir.",
    "El empate paga como una recalificación.",
)
SIDE_MISS_LINES = (
    "Sin empate, la lateral se queda en la mesa.",
    "El empate no vino. La banca da las gracias.",
    "Lateral perdida: ni tablas ni sobres.",
)

RESULT_BADGES = {
    Result.WIN: "GANAS",
    Result.LOSE: "PIERDES",
    Result.SURRENDER: "RENDICIÓN",
    Result.WAR_WIN: "GUERRA GANADA",
    Result.WAR_TIE: "GUERRA EMPATADA",
    Result.WAR_LOSE: "GUERRA PERDIDA",
}

EditFn = Callable[..., Awaitable[Any]]


# -- Presentación -------------------------------------------------------------------


def main_net(game: WarGame) -> int:
    """Lo que gana o pierde la mano principal, sin la lateral."""
    return game.main_return - game.stake * (2 if game.went_to_war else 1)


def _title(who: str, cards: list[Card]) -> str:
    return f"{who} · " + " → ".join(card.label for card in cards)


def render_table(renderer: CardRenderer, game: WarGame, *, burning: bool = False) -> bytes:
    """PNG de la mesa en el estado actual de la mano.

    Args:
        burning: Enseña solo las primeras cartas (la banca está quemando las de
            la guerra y todavía no han salido las otras dos).
    """
    count = 1 if burning else len(game.player)
    player, dealer = game.player[:count], game.dealer[:count]
    badge = None
    good = False
    if game.result is not None and not burning:
        net = main_net(game)
        badge = RESULT_BADGES[game.result]
        if net > 0:
            badge += " +" + f"{net:,}".replace(",", ".")
        good = net > 0
    return renderer.render(
        dealer=dealer,
        hide_hole=False,
        dealer_title=_title("BANCA", dealer),
        hands=[
            HandView(
                cards=player,
                title=_title("TÚ", player),
                active=game.awaiting_choice or burning,
                badge=badge,
                badge_good=good,
            )
        ],
    )


def side_line(game: WarGame, rng: random.Random) -> str:
    """Línea con lo que hizo la lateral de empate, si se jugó."""
    if not game.side:
        return ""
    if game.tied:
        return (
            f"\n🤝 {rng.choice(SIDE_HIT_LINES)} Empate {TIE_PAYS}:1 "
            f"+{format_amount(game.side * TIE_PAYS)}."
        )
    return f"\n🤝 {rng.choice(SIDE_MISS_LINES)} -{format_amount(game.side)}."


def result_headline(game: WarGame, rng: random.Random | None = None) -> str:
    """Titular grande con lo ganado o perdido en la mano principal, y la lateral debajo."""
    rng = rng or random.Random()
    net = main_net(game)
    stake = game.stake
    match game.result:
        case Result.WIN:
            text = f"# {rng.choice(WIN_LINES)} +{format_amount(net)}"
        case Result.LOSE:
            text = f"# -{format_amount(stake)}\n### {rng.choice(LOSS_LINES)}"
        case Result.SURRENDER:
            text = (
                f"# 🏳️ Te rindes\n### {rng.choice(SURRENDER_LINES)}\n"
                f"Recuperas {format_amount(game.main_return)}."
            )
        case Result.WAR_WIN:
            text = f"# ⚔️ {rng.choice(WAR_WIN_LINES)} +{format_amount(net)}"
        case Result.WAR_TIE:
            text = (
                f"# ⚔️ {rng.choice(WAR_TIE_LINES)} +{format_amount(net)}\n"
                f"Premio extra de {format_amount(stake * WAR_TIE_BONUS)} incluido."
            )
        case _:
            text = f"# -{format_amount(-net)}\n### {rng.choice(WAR_LOSE_LINES)}"
    text += side_line(game, rng)
    if game.side:
        sign = "+" if game.net > 0 else "-" if game.net < 0 else ""
        text += f"\n**Total de la mano: {sign}{format_amount(abs(game.net))}**"
    return text


def table_embed(
    *,
    owner: str,
    game: WarGame | None,
    balance: int,
    stake: int,
    side_on: bool,
    streak: int,
    paid: bool,
    headline: str | None = None,
    burning: bool = False,
    rng: random.Random | None = None,
) -> discord.Embed:
    """Embed de la mesa. `headline` es el resultado de la última mano, si acabó.

    Args:
        paid: Si la mano ya se pagó (la mesa está lista para otra).
        burning: La banca está quemando cartas: todavía no se ve el resultado.
    """
    rng = rng or random.Random()
    if game is None:
        description = "Pulsa 🃏 **Repartir**. El as manda y los palos no cuentan."
        color = COLOR_PLAYING
    elif burning:
        description = f"## ⚔️ ¡A la guerra!\n{rng.choice(WAR_LINES)}"
        color = COLOR_WAR
    elif game.awaiting_choice:
        cost = format_amount(game.extra_stake)
        description = (
            f"## 🤝 ¡Empate a {game.player[0].label}!\n{rng.choice(TIE_LINES)}\n"
            f"🏳️ **Rendirte**: recuperas {format_amount(game.stake // 2)}.\n"
            f"⚔️ **A la guerra** (otra ficha de {cost}): si tu carta iguala o supera a la de "
            f"la banca, cobras {cost} y se te devuelve la original; si no, caen las dos. "
            f"Si empatas otra vez, premio de {format_amount(game.stake * WAR_TIE_BONUS)} más."
        )
        color = COLOR_WAR
    else:
        description = headline or result_headline(game, rng)
        color = COLOR_WIN if game.net > 0 else COLOR_PUSH if game.net == 0 else COLOR_LOSS
    if balance == 0 and (game is None or paid):
        description += "\n\n**Estás a cero.** `imv` te recarga."
    embed = discord.Embed(title="⚔️ Guerra", description=description, color=color)
    embed.add_field(name="Saldo", value=format_amount(balance))
    playing = game is not None and not paid
    embed.add_field(
        name="En juego" if playing else "Ficha",
        value=format_amount(game.total_stake if playing else stake),  # type: ignore[union-attr]
    )
    embed.add_field(name="Racha", value=f"🔥 {streak}" if streak >= 2 else "—")
    embed.add_field(
        name=f"Empate {TIE_PAYS}:1",
        value=f"✅ {format_amount(stake)}" if side_on else "—",
    )
    embed.set_image(url=f"attachment://{PNG_NAME}")
    embed.set_footer(text=f"Mesa de {owner} · {RULES_FOOTER}")
    return embed


# -- Mesa ---------------------------------------------------------------------------


class WarTable(discord.ui.View):
    """Mesa de la Guerra de un jugador: un mensaje con botones.

    Guarda la mano en curso, la ficha, la lateral y las rachas. Solo su dueño puede
    pulsarla.
    """

    def __init__(self, cog: War, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=TABLE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        #: Si se repartirá con la apuesta lateral de empate.
        self.side_on = False
        self.game: WarGame | None = None
        self.streak = 0
        #: Rachas de la mesa para los logros: manos ganadas, perdidas y empatadas seguidas.
        self.win_run = 0
        self.lose_run = 0
        self.tie_run = 0
        self.headline: str | None = None
        self.message: discord.Message | None = None
        self.started_at = 0
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        #: Si la mano en curso ya se pagó (un solo pago por mano, también al apagar).
        self._paid = True
        #: Mano ya pagada que falta contar para los logros, con su saldo e IRPF.
        self._to_track: tuple[WarGame, int, int] | None = None
        self._build_buttons()

    # -- Botones --------------------------------------------------------------------

    def _add(
        self,
        label: str,
        row: int,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        custom_id: str,
    ) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=style, row=row, custom_id=f"{GAME}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)
        return button

    def _build_buttons(self) -> None:
        gray, green = discord.ButtonStyle.secondary, discord.ButtonStyle.success
        red = discord.ButtonStyle.danger
        self.surrender_button = self._add(
            "🏳️ Rendirse", 0, self._surrender, style=gray, custom_id="surrender"
        )
        self.war_button = self._add("⚔️ A la guerra", 0, self._war, style=red, custom_id="war")
        self.deal_button = self._add(
            "🃏 Repartir", 1, self._deal_again, style=green, custom_id="deal"
        )
        self.stake_buttons = [
            self._add("½", 1, self._halve, custom_id="half"),
            self._add("×2", 1, self._double_stake, custom_id="x2"),
            self._add("💰 All-in", 1, self._all_in, custom_id="allin"),
        ]
        self.side_button = self._add(
            f"🤝 Empate {TIE_PAYS}:1", 2, self._toggle_side, style=gray, custom_id="side"
        )
        self._update_buttons()

    def _update_buttons(self, *, locked: bool = False) -> None:
        """Activa rendirse y la guerra solo con un empate por decidir; el resto, entre manos."""
        deciding = self.game is not None and self.game.awaiting_choice
        for button in (self.surrender_button, self.war_button):
            button.disabled = locked or not deciding
        for button in (self.deal_button, *self.stake_buttons, self.side_button):
            button.disabled = locked or deciding
        self.side_button.style = (
            discord.ButtonStyle.success if self.side_on else discord.ButtonStyle.secondary
        )
        self.side_button.label = f"🤝 Empate {TIE_PAYS}:1" + (" ✅" if self.side_on else "")

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta mesa es de {self.owner.display_name}. Abre la tuya con `guerra`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Cierra la mesa; si quedaba un empate sin decidir, se rinde y se paga."""
        self.cog.tables.discard(self)
        # Una mano ya pagada se ve tal cual: solo hay que apagar los botones, sin
        # volver a subir la mesa (~1 s).
        unsettled = self.game is not None and not self._paid
        if unsettled:
            await self.force_settle()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            embed = await self.current_embed()
            kwargs: dict[str, Any] = {"embed": embed, "view": self}
            if self.game is not None and unsettled:
                png = await self.cog.render(self.game)
                kwargs["attachments"] = [discord.File(io.BytesIO(png), filename=PNG_NAME)]
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa de la guerra", exc_info=True)

    async def force_settle(self) -> None:
        """Termina la mano sin animación (caducidad o apagado) y paga lo que toque."""
        game = self.game
        if game is None or self._paid:
            return
        if game.awaiting_choice:
            game.surrender()
        await self._settle(game)
        await self._track()

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(
        self, balance: int | None = None, *, burning: bool = False
    ) -> discord.Embed:
        """Embed con el estado actual de la mesa."""
        if balance is None:
            balance = await self.balance()
        return table_embed(
            owner=self.owner.display_name,
            game=self.game,
            balance=balance,
            stake=self.stake,
            side_on=self.side_on,
            streak=self.streak,
            paid=self._paid,
            headline=self.headline,
            burning=burning,
        )

    async def _show(self, edit_fn: EditFn, *, locked: bool = False, burning: bool = False) -> None:
        """Dibuja la mesa y la envía con `edit_fn` (editar o enviar, según el caso)."""
        assert self.game is not None
        self._update_buttons(locked=locked)
        png = await self.cog.render(self.game, burning=burning)
        await edit_fn(
            embed=await self.current_embed(burning=burning),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )

    @property
    def side(self) -> int:
        """Apuesta lateral de la próxima mano: igual a la ficha, si está activada."""
        return self.stake if self.side_on else 0

    async def start(self, first_edit: EditFn) -> str | None:
        """Cobra la ficha (y la lateral) y reparte una mano nueva.

        Returns:
            Un mensaje de error para el usuario si no se pudo repartir, o `None`.
        """
        side = self.side
        try:
            await self.cog.economy.place_bet(
                self.guild_id, self.owner.id, game=GAME, stake=self.stake + side
            )
        except InsufficientFundsError as error:
            return insufficient_text(error.balance, self.stake + side)
        # Para las porras: una mano repartida antes del cierre no cuenta.
        self.started_at = int(time.time())
        self.game = WarGame(stake=self.stake, side=side, shoe=self.cog.new_shoe())
        self._paid = False
        self.headline = None
        self.game.deal()
        if self.game.awaiting_choice:
            await self._show(first_edit)
        else:
            await self._finish(first_edit)
        return None

    async def _decide(self, interaction: discord.Interaction, *, war: bool) -> None:
        """Resuelve el empate: rendirse o ir a la guerra (con su segunda apuesta)."""
        game = self.game
        if self._busy or game is None or not game.awaiting_choice:
            await ack(interaction)
            return
        self._busy = True
        try:
            # Cobrar, pagar y dibujar la mesa tardan: se acepta el clic antes.
            await ack(interaction)
            if war:
                extra = game.extra_stake
                try:
                    await self.cog.economy.place_bet(
                        self.guild_id, self.owner.id, game=GAME, stake=extra
                    )
                except InsufficientFundsError as error:
                    await notify(
                        interaction,
                        f"Para ir a la guerra necesitas {format_amount(extra)} más y tienes "
                        f"{format_amount(error.balance)}. Siempre puedes rendirte.",
                    )
                    return
                game.go_to_war()
                self._last_interaction = interaction
                await self._show(interaction.edit_original_response, locked=True, burning=True)
                await asyncio.sleep(WAR_PAUSE_SECONDS)
            else:
                game.surrender()
                self._last_interaction = interaction
            await self._finish(interaction.edit_original_response)
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)

    async def _surrender(self, interaction: discord.Interaction) -> None:
        await self._decide(interaction, war=False)

    async def _war(self, interaction: discord.Interaction) -> None:
        await self._decide(interaction, war=True)

    async def _finish(self, edit_fn: EditFn) -> None:
        """Paga la mano, enseña el resultado y cuenta los logros."""
        game = self.game
        assert game is not None
        balance = await self._settle(game)
        self._update_buttons()
        png = await self.cog.render(game)
        await edit_fn(
            embed=await self.current_embed(balance),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )
        await self._track()

    async def _settle(self, game: WarGame) -> int:
        """Paga la mano y actualiza las rachas. Devuelve el saldo final.

        Si la mano ya se pagó (p. ej. el apagado la cerró mientras la banca
        quemaba cartas en pantalla) no se vuelve a pagar.
        """
        if self._paid:
            return await self.balance()
        self._paid = True
        tax_note: str | None = None
        tax_delta = 0
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.total_return
            )
            balance = settlement.balance
            tax_note = gambling_tax_line(settlement)
            tax_delta = settlement.tax_delta
        except BalanceLimitError:
            logger.warning("Premio de la guerra por encima del saldo máximo; no se paga.")
            balance = await self.balance()
        self._to_track = (game, balance, tax_delta)
        if game.net > 0:
            self.streak += 1
        elif game.net < 0:
            self.streak = 0
        self._update_runs(game)
        self.headline = result_headline(game)
        if tax_note:
            self.headline += f"\n{tax_note}"
        if renta_hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=game.total_stake, net=game.net, balance_after=balance),
        ):
            self.headline += f"\n{renta_hint}"
        return balance

    def _update_runs(self, game: WarGame) -> None:
        """Manos seguidas ganadas, perdidas y empatadas en la primera carta."""
        if game.won:
            self.win_run, self.lose_run = self.win_run + 1, 0
        elif game.result in (Result.LOSE, Result.WAR_LOSE):
            self.win_run, self.lose_run = 0, self.lose_run + 1
        else:
            self.win_run = self.lose_run = 0
        self.tie_run = self.tie_run + 1 if game.tied else 0

    async def _track(self) -> None:
        """Cuenta la última mano pagada para los logros (una sola vez).

        Se llama después de enseñar el resultado, para que el aviso de un logro
        no se adelante a las cartas.
        """
        if self._to_track is None:
            return
        game, balance, tax_delta = self._to_track
        self._to_track = None
        delta = war_stats(game, win_run=self.win_run, lose_run=self.lose_run, tie_run=self.tie_run)
        delta.merge(
            casino_stats(
                stake=game.total_stake, net=game.net, balance_after=balance, tax_delta=tax_delta
            )
        )
        await logros.casino_play(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            delta,
            net=game.net,
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=game.total_stake,
            net=game.net,
            balance_after=balance,
            tax=tax_delta,
            details=(
                ("war", int(game.went_to_war)),
                ("started", self.started_at),
            ),
        )

    # -- Fichas y repartir ----------------------------------------------------------

    def _fits(self, balance: int) -> int:
        """Ficha más alta que cabe en `balance` contando la lateral, si está activada."""
        return max(1, balance // (2 if self.side_on else 1))

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        # El embed lee el saldo de la base de datos: se acepta el clic antes.
        await ack(interaction)
        self._update_buttons()
        await edit(interaction, embed=await self.current_embed(balance), view=self)
        self._last_interaction = interaction

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        self.stake = max(1, min(self.stake * 2, self._fits(balance)))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        if balance == 0:
            await notify(interaction, insufficient_text(0))
            return
        self.stake = self._fits(balance)
        await self._refresh(interaction, balance)

    async def _toggle_side(self, interaction: discord.Interaction) -> None:
        self.side_on = not self.side_on
        await self._refresh(interaction)

    async def _deal_again(self, interaction: discord.Interaction) -> None:
        if self._busy or (self.game is not None and not self._paid):
            await ack(interaction)
            return
        self._busy = True
        try:
            await ack(interaction)
            self._last_interaction = interaction
            error = await self.start(interaction.edit_original_response)
            if error is not None:
                await notify(interaction, error)
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)


# -- Cog ----------------------------------------------------------------------------


class War(commands.Cog, name="Guerra"):
    """Guerra (Casino War) con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: CardRenderer | None = None,
        shoe_factory: Callable[[], list[Card]] = new_shoe,
        casino_channel_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.renderer = renderer or CardRenderer()
        self.new_shoe = shoe_factory
        self.casino_channel_ids = casino_channel_ids
        # Mesas abiertas: para resolver sus manos si el bot se apaga. Cada
        # mesa sale de aquí al caducar, así que el tamaño está acotado.
        self.tables: set[WarTable] = set()

    async def cog_unload(self) -> None:
        """Al apagar, resuelve los empates a medias para no quedarse con el dinero."""
        for table in list(self.tables):
            try:
                await table.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una mano de la guerra al apagar")
            table.stop()
        self.tables.clear()

    async def render(self, game: WarGame, *, burning: bool = False) -> bytes:
        """PNG de la mesa, dibujado fuera del event loop."""
        return await asyncio.to_thread(lambda: render_table(self.renderer, game, burning=burning))

    async def _guerra_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/guerra` y `.guerra`: abre la mesa y reparte ya."""
        if guild is None:
            await send_error("La guerra solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "La guerra"):
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
        if stake > balance:
            await send_error(insufficient_text(balance))
            return

        table = WarTable(self, guild_id=guild.id, owner=user, stake=stake)

        async def first_edit(**kwargs: Any) -> None:
            attachments = kwargs.pop("attachments")
            table.message = await send(files=attachments, **kwargs)

        error = await table.start(first_edit)
        if error is not None:
            await send_error(error)
            return
        self.tables.add(table)

    @app_commands.command(
        name="guerra",
        description="Guerra: tu carta contra la de la banca, y a la guerra si empatas.",
    )
    @app_commands.describe(cantidad="Apuesta: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def guerra(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Reparte una mano de la Guerra con botones para jugarla.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado.
        Cobra la apuesta al repartir y paga al terminar la mano.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._guerra_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="guerra")
    @commands.guild_only()
    async def guerra_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.guerra`, `.guerra 500`, `.guerra all`."""

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._guerra_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(War(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
