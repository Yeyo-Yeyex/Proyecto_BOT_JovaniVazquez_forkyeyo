"""Torre del dragón: `torre`, una torre de nueve pisos con huevos y dragones.

Cada jugador sube su propia torre, que solo él puede pulsar. `torre 500 dificil`
cobra 500 Y$ y juega en Difícil. En cada piso hay varias puertas y unas
esconden un dragón 🐉; las demás, un huevo 🥚. Con huevo subes de piso y sube el
multiplicador; con dragón lo pierdes todo. 💰 **Cobrar** se lleva apuesta ×
multiplicador (tras al menos un piso) y coronar el piso 9 cobra solo. 🎲 **Al
azar** elige una puerta cualquiera del piso.

Hay cinco dificultades (Fácil, Normal, Difícil, Experto y Maestro: más puertas
o menos dragones, más o menos riesgo y premio) y se cambian con un menú
mientras no se haya subido el primer piso y entre partidas.

Mientras se juega, el texto dibuja la torre entera, de arriba abajo y con el
multiplicador de cada piso, y lleva la cuenta de pisos (🏰 3/9), lo que
sumaría el siguiente y su probabilidad, frases al pasar por ciertos pisos y el
aviso de récord personal. Al acabar enseña dónde estaban todos los dragones y
deja jugar otra con 🔁, cambiar la apuesta (½, ×2, 💰 All-in) y la dificultad.

El mensaje usa los componentes nuevos de Discord (`LayoutView`): un bloque con
el texto y una sola fila de puertas (las del piso actual), una fila de
controles y el menú de dificultad. No hay imágenes: cada clic es una edición
de texto y botones, así que es instantáneo.

Reglas en `bot.services.tower`. Dinero: la apuesta se cobra al empezar
(`place_bet`) y se paga al cobrar o al quemarse (`pay_winnings`, con 0 si te
quema), que es cuando se ajusta el IRPF del día (juego, art. 33.1 y 33.5.d
LIRPF). Si la torre caduca (3 min sin tocarla) o el bot se apaga de forma
ordenada con una partida a medias, se cobra sola; si no se había subido ningún
piso, se devuelve la apuesta.

Si `CASINO_CHANNEL_IDS` está configurado, solo se juega en esos canales.
Permisos del bot en el canal: enviar mensajes.
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import casino_stats, tower_stats
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.pets import bet_moment
from bot.services.tower import (
    DEFAULT_DIFFICULTY,
    DIFFICULTIES,
    DIFFICULTY_BY_KEY,
    FLOORS,
    Difficulty,
    Status,
    TowerError,
    TowerGame,
    format_multiplier,
    milestone,
    multiplier_cents,
    parse_difficulty,
    plural_dragons,
    risk_summary,
)
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Clave del juego: motivo del libro de la economía y de `casino_stats.GAMES`.
GAME = "torre"
DEFAULT_STAKE = 100
BOARD_TIMEOUT = 180
#: Desde este multiplicador (en centésimas) se anuncia el premio en el canal.
SHOUT_CENTS = 2_500

EMPTY = "⬛"
EGG = "🥚"
DRAGON = "🐉"
BOOM = "💥"
DOOR = "🚪"

COLOR_PLAYING = discord.Color.from_rgb(88, 101, 242)
COLOR_HOT = discord.Color.from_rgb(255, 196, 0)
COLOR_BURNED = discord.Color.from_rgb(180, 60, 40)
COLOR_CASHED = discord.Color.from_rgb(255, 196, 0)

# Textos en el tono de Jovani Vázquez.
CASH_LINES = (
    "¡Wepa!",
    "¡Cobras, mi amor!",
    "¡Bajas de la torre con la cartera llena!",
    "¡Huevo cobrado y dragón con hambre!",
    "¡Ni Daenerys se escapó tan limpia de Poniente!",
    "¡Escalada más fina que la de un asesor de Moncloa!",
    "¿Con pulpa? ¡Sin pulpa! ¡Con huevos!",
    "¡Plata de dragón, y sin pasar por la aduana de Xi!",
    "¡Te llevas el tesoro antes de que lo declare Hacienda!",
    "¡Aterrizaje más suave que el del Falcon!",
    "¡Huevos fritos con plata, qué buen desayuno!",
    "¡Toma mangó!",
    "¡Cobras antes de que el dragón rojo de Gales se despierte!",
)
BURN_LINES = (
    "¡Chamuscado!",
    "¡Ay, bendito!",
    "¡El dragón te ha hecho a la brasa!",
    "¡Torre de Babel: aquí nadie se entiende y todo arde!",
    "¡Daenerys te manda recuerdos, en llamas!",
    "¡Dracarys! Te han frito como a un bono basura.",
    "¡Quemado más rápido que un contenedor con Macron mirando!",
    "¡El Dragón de Xi te ha dejado a cero en el crédito social!",
    "¡Dragón 1, cartera 0!",
    "¡Torre Picasso en llamas y sin bomberos de la Comunidad!",
    "¡Más tostado que el Teide en agosto!",
    "¡Huevo duro, bolsillo vacío!",
    "¡Quiero café, y tú quieres un extintor!",
)
TOP_LINES = (
    "¡CIMA! ¡Eres el rey de la Torre!",
    "¡Coronada! Hasta el Teide se ha quitado el sombrero.",
    "¡Has domado al dragón, Daenerys en versión low cost!",
    "¡Torre conquistada! Te esperan en Juego de Tronos, sin tronos.",
    "¡La Torre de Babel sigue en obras y tú ya estás arriba!",
    "¡Dragón dormido y cartera despierta!",
    "¡Wepa! ¡Arriba del todo, mi amor!",
    "¡Más alto que la Torre Picasso y con menos burocracia!",
    "¡El Dragón de Xi pide tu receta y la censura la borra!",
    "¡Toma mangó! ¡Cima!",
)
#: Estadística de logros con el récord de pisos en una partida.
RECORD_STAT = "tower_floor_max"


def percent(chance: float) -> str:
    """`0,75` → `75 %` (sin decimales, al estilo español)."""
    return f"{round(chance * 100)} %"


class TowerBoard(ui.LayoutView):
    """La torre de un jugador: el texto con los pisos, las puertas y los controles.

    Guarda la partida en curso (o la última), la apuesta y la dificultad. No
    guarda dinero: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Tower,
        *,
        guild_id: int,
        owner: discord.abc.User,
        stake: int,
        difficulty: Difficulty,
    ) -> None:
        super().__init__(timeout=BOARD_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.difficulty = difficulty
        self.game: TowerGame | None = None
        self.balance = 0
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.channel: object = None
        #: Récord de pisos del dueño al empezar la partida en curso.
        self.record_before = 0
        #: Cuándo empezó la partida en curso (para las porras).
        self.started_at = 0
        self._lock = asyncio.Lock()
        self._last_interaction: discord.Interaction | None = None

    # -- Dibujo -----------------------------------------------------------------------

    def tower_text(self, game: TowerGame) -> str:
        """La torre de arriba abajo: una línea por piso con su multiplicador.

        `⬛` es una puerta sin jugar, `🥚` la elegida; al acabar salen todos los
        dragones (`🐉`) y el que quemó la partida, como `💥`.
        """
        over = not game.playing
        rows = []
        for floor in range(FLOORS - 1, -1, -1):
            cells = []
            for door in range(game.difficulty.width):
                if floor < game.floor and door == game.picks[floor]:
                    cells.append(EGG)
                elif over and door in game.dragons[floor]:
                    boom = floor == game.floor and door == game.burned
                    cells.append(BOOM if boom else DRAGON)
                else:
                    cells.append(EMPTY)
            mult = format_multiplier(multiplier_cents(game.difficulty, floor + 1))
            if floor + 1 == game.floor:
                mult = f"**{mult}**"
            marker = "👉" if game.playing and floor == game.floor else "▫️"
            rows.append(f"{marker}`{floor + 1}` {''.join(cells)} {mult}")
        return "\n".join(rows)

    def header(self) -> str:
        """Texto de arriba: estado de la partida, multiplicadores, torre y apuesta."""
        game = self.game
        name = self.owner.display_name
        lines = [f"### 🐉 Torre del dragón · {name}"]
        if game is None:
            lines.append("Pulsa 🔁 para jugar.")
        elif game.playing:
            if game.floor:
                lines.append(
                    f"# {format_multiplier(game.cents)} · {format_amount(game.cashout_value)}"
                )
                progress = f"🏰 **{game.floor}/{FLOORS}**"
                if cheer := milestone(game.floor):
                    progress += f" · {cheer}"
                lines.append(progress)
                if self.record_before and game.floor > self.record_before:
                    lines.append(
                        f"🏅 ¡Récord personal! Antes llegabas al piso {self.record_before}."
                    )
            else:
                lines.append("# Elige una puerta")
                lines.append(
                    f"-# Cada piso esconde {plural_dragons(game.difficulty.dragons)} "
                    f"{DRAGON} entre {game.difficulty.width} puertas."
                )
            if game.next_value is not None:
                extra = game.next_value - game.cashout_value
                lines.append(
                    f"-# Siguiente: {format_multiplier(game.next_cents or 0)} "
                    f"(+{format_amount(extra)}) · {percent(float(game.safe_chance))} "
                    f"de que sea {EGG}"
                )
        elif game.status is Status.CASHED:
            sign = "+" if game.net >= 0 else "-"
            phrase = random.choice(TOP_LINES if game.crowned else CASH_LINES)
            lines.append(
                f"# {'👑' if game.crowned else EGG} {phrase} {sign}{format_amount(abs(game.net))}\n"
                f"Cobras **{format_amount(game.payout)}** en {format_multiplier(game.cents)} "
                f"con 🏰 {game.floor}/{FLOORS}"
            )
            if self.record_before and game.floor > self.record_before:
                lines.append(f"🏅 ¡Récord personal: piso {game.floor}!")
        else:
            lines.append(f"# {BOOM} {random.choice(BURN_LINES)} -{format_amount(game.stake)}")
            if game.floor:
                lines.append(
                    f"Te quemó el dragón del piso {game.floor + 1}, con 🏰 {game.floor}/{FLOORS}. "
                    f"Te ibas a llevar {format_amount(game.cashout_value)}."
                )
            else:
                lines.append("Te quemó el dragón del primer piso. Qué manera de empezar.")
            if self.record_before and game.floor > self.record_before:
                lines.append(f"🏅 Aun así, récord personal: piso {game.floor}.")
        if game is not None:
            lines.append(self.tower_text(game))
        if self.note:
            lines.append(self.note)
        playing = game is not None and game.playing
        difficulty = game.difficulty if game is not None and playing else self.difficulty
        stake = game.stake if game is not None and playing else self.stake
        lines.append(
            f"-# Apuesta {format_amount(stake)} · {difficulty.emoji} {difficulty.label} · "
            f"Saldo {format_amount(self.balance)}"
        )
        if self.balance == 0 and (game is None or not game.playing):
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def color(self) -> discord.Color:
        """Color del borde según cómo va la partida."""
        game = self.game
        if game is None or game.playing:
            return COLOR_HOT if game is not None and game.cents >= 200 else COLOR_PLAYING
        return COLOR_CASHED if game.status is Status.CASHED else COLOR_BURNED

    def door_button(self, door: int) -> ui.Button:
        """Una puerta del piso actual (apagada si no hay partida en marcha)."""
        game = self.game
        button: ui.Button = ui.Button(
            label=str(door + 1),
            emoji=DOOR,
            style=discord.ButtonStyle.secondary,
            disabled=game is None or not game.playing,
            custom_id=f"{GAME}:pick:{door}",
        )
        button.callback = self._door_callback(door)  # type: ignore[method-assign]
        return button

    def control_button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> ui.Button:
        """Un botón de la fila de controles."""
        button: ui.Button = ui.Button(
            label=label, style=style, disabled=disabled, custom_id=f"{GAME}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        return button

    def rebuild(self) -> None:
        """Vuelve a montar los componentes con el estado actual."""
        self.clear_items()
        game = self.game
        shown = game.difficulty if game is not None else self.difficulty
        container = ui.Container(accent_colour=self.color())
        container.add_item(ui.TextDisplay(self.header()))
        container.add_item(ui.Separator())
        doors: ui.ActionRow = ui.ActionRow()
        for door in range(shown.width):
            doors.add_item(self.door_button(door))
        container.add_item(doors)
        self.add_item(container)

        controls: ui.ActionRow = ui.ActionRow()
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        if game is not None and game.playing:
            label = f"💰 Cobrar {format_amount(game.cashout_value)}" if game.floor else "💰 Cobrar"
            controls.add_item(
                self.control_button(
                    label, "cashout", self._cash_out, style=green, disabled=not game.floor
                )
            )
            controls.add_item(self.control_button("🎲 Al azar", "random", self._random, style=blue))
        else:
            controls.add_item(
                self.control_button(
                    f"🔁 Jugar · {format_amount(self.stake)}", "again", self._again, style=green
                )
            )
            controls.add_item(self.control_button("½", "half", self._halve))
            controls.add_item(self.control_button("×2", "x2", self._double))
            controls.add_item(self.control_button("💰 All-in", "allin", self._all_in))
        self.add_item(controls)
        if self._level_editable():
            level_row: ui.ActionRow = ui.ActionRow()
            level_row.add_item(self.level_select())
            self.add_item(level_row)

    def level_select(self) -> ui.Select:
        """Menú de dificultad, con puertas, dragones y el premio de coronar en cada opción."""
        options = [
            discord.SelectOption(
                label=f"{d.emoji} {d.label}",
                value=d.key,
                description=risk_summary(d),
                default=d is self.difficulty,
            )
            for d in DIFFICULTIES
        ]
        select: ui.Select = ui.Select(
            custom_id=f"{GAME}:nivel", options=options, placeholder="Elige la dificultad"
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self._choose_level(interaction, select.values[0])

        select.callback = callback  # type: ignore[method-assign]
        return select

    def disable_all(self) -> None:
        """Apaga todos los botones y el menú (torre caducada)."""
        for item in self.walk_children():
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su torre."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta torre es de {self.owner.display_name}. Sube la tuya con `torre`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había partida a medias, se cobra sola."""
        self.cog.boards.discard(self)
        await self.force_settle()
        self.rebuild()
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la torre del dragón", exc_info=True)

    async def force_settle(self) -> None:
        """Cierra la partida a medias sin que nadie pierda: cobra o devuelve."""
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                return
            if game.floor:
                game.cash_out()
                await self._settle(game)
                return
            # Ni un piso subido: se devuelve la apuesta (neto 0).
            try:
                await self.cog.economy.pay_winnings(
                    self.guild_id, self.owner.id, game=GAME, amount=game.stake
                )
            except BalanceLimitError:
                logger.warning("No se pudo devolver una apuesta de la Torre del dragón.")
            self.game = None

    # -- Juego ------------------------------------------------------------------------

    async def start(self) -> str | None:
        """Cobra la apuesta y sortea los dragones.

        Returns:
            Un mensaje de error para el usuario si no se pudo, o `None`.
        """
        try:
            settlement = await self.cog.economy.place_bet(
                self.guild_id, self.owner.id, game=GAME, stake=self.stake
            )
        except InsufficientFundsError as error:
            return insufficient_text(error.balance, self.stake)
        self.balance = settlement.balance
        # Para las porras: una partida abierta antes del cierre no cuenta.
        self.started_at = int(time.time())
        self.game = TowerGame.new(self.stake, self.difficulty, self.cog.rng)
        self.record_before = await self.cog.record(self.guild_id, self.owner.id)
        self.note = None
        self.rebuild()
        return None

    async def _settle(self, game: TowerGame) -> BetSettlement | None:
        """Paga la partida terminada (0 si te quemó) y deja preparado el resultado."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.payout
            )
        except BalanceLimitError:
            logger.warning("Premio de la Torre del dragón por encima del saldo máximo; no se paga.")
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
            bet_moment(stake=game.stake, net=game.net, balance_after=settlement.balance),
        ):
            notes.append(hint)
        self.note = "\n".join(notes) or None
        return settlement

    async def _after_game(
        self, interaction: discord.Interaction, game: TowerGame, settlement: BetSettlement | None
    ) -> None:
        """Lo que va después de enseñar el final: renta, récord, logros y anuncio."""
        await renta.remind(self.cog.bot, interaction)
        self.cog.note_record(self.guild_id, self.owner.id, game.floor)
        delta = tower_stats(game)
        delta.merge(
            casino_stats(
                stake=game.stake,
                net=game.net,
                balance_after=settlement.balance if settlement else self.balance,
                tax_delta=settlement.tax_delta if settlement else 0,
            )
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self.channel, delta, net=game.net
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=game.stake,
            net=game.net,
            balance_after=settlement.balance if settlement else self.balance,
            tax=settlement.tax_delta if settlement else 0,
            details=(
                ("boom", int(game.status is Status.BURNED)),
                ("started", self.started_at),
            ),
        )
        await self.cog.shout(game, self.owner, self.channel)

    async def _climb(self, interaction: discord.Interaction, door: int | None) -> None:
        """Elige `door` (o una al azar) en el piso actual y enseña el resultado."""
        if self._lock.locked():
            # Doble clic mientras se procesa el anterior: se ignora.
            await ack(interaction)
            return
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                await ack(interaction)
                return
            random_pick = door is None
            try:
                target = game.random_door(self.cog.rng) if door is None else door
                safe = game.climb(target, random_pick=random_pick)
            except TowerError:
                await ack(interaction)
                return
            settlement = None
            if not safe or game.crowned:
                # Pagar va a la base de datos: se acepta el clic antes. Un piso
                # superado sin más se pinta directamente, que es lo más rápido.
                await ack(interaction)
            if not safe:
                settlement = await self._settle(game)
            elif game.crowned:
                game.cash_out()
                settlement = await self._settle(game)
            else:
                self.note = None
            self._last_interaction = interaction
            self.rebuild()
            await edit(interaction, view=self)
        if not game.playing:
            await self._after_game(interaction, game, settlement)

    def _door_callback(self, door: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._climb(interaction, door)

        return callback

    async def _random(self, interaction: discord.Interaction) -> None:
        await self._climb(interaction, None)

    async def _cash_out(self, interaction: discord.Interaction) -> None:
        """💰 Cobrar: se retira con el multiplicador actual."""
        if self._lock.locked():
            await ack(interaction)
            return
        async with self._lock:
            game = self.game
            if game is None or not game.playing or not game.floor:
                await ack(interaction)
                return
            await ack(interaction)
            game.cash_out()
            settlement = await self._settle(game)
            self._last_interaction = interaction
            self.rebuild()
            await edit(interaction, view=self)
        await self._after_game(interaction, game, settlement)

    async def _again(self, interaction: discord.Interaction) -> None:
        """🔁 Jugar: cobra otra vez y sortea dragones nuevos."""
        if self._lock.locked():
            await ack(interaction)
            return
        async with self._lock:
            if self.game is not None and self.game.playing:
                await ack(interaction)
                return
            await ack(interaction)
            error = await self.start()
            if error is not None:
                await notify(interaction, error)
                return
            self._last_interaction = interaction
            await edit(interaction, view=self)
        await renta.remind(self.cog.bot, interaction)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """Pinta la torre tras cambiar apuesta o dificultad (sin dinero de por medio).

        Lee el saldo de la base de datos: se acepta el clic antes.
        """
        await ack(interaction)
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        self.note = None
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, view=self)

    def _idle(self) -> bool:
        return self.game is None or not self.game.playing

    def _level_editable(self) -> bool:
        """Si el menú de dificultad se puede usar: sin partida o antes del primer piso.

        El comando ya cobra la apuesta y empieza la partida, así que sin esto el
        menú no salía la primera vez. Los dragones no se han tocado hasta el
        primer clic, de modo que rehacer la torre antes no altera nada jugado.
        """
        game = self.game
        return game is None or not game.playing or not game.picks

    async def _halve(self, interaction: discord.Interaction) -> None:
        if not self._idle():
            await ack(interaction)
            return
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double(self, interaction: discord.Interaction) -> None:
        if not self._idle():
            await ack(interaction)
            return
        await ack(interaction)
        balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        # Si el doble no cabe, se queda en todo el saldo.
        self.stake = max(1, min(self.stake * 2, balance))
        await self._refresh(interaction)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        if not self._idle():
            await ack(interaction)
            return
        await ack(interaction)
        balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        if balance <= 0:
            await notify(interaction, insufficient_text(0))
            return
        self.stake = balance
        await self._refresh(interaction)

    async def _choose_level(self, interaction: discord.Interaction, key: str) -> None:
        """Menú de dificultad: cambia la torre recién empezada o la de la siguiente partida.

        Con la partida empezada y ningún piso subido, se rehace con la
        dificultad nueva y la misma apuesta, que ya está cobrada: no se mueve dinero.
        """
        if self._lock.locked() or not self._level_editable() or key not in DIFFICULTY_BY_KEY:
            await ack(interaction)
            return
        difficulty = DIFFICULTY_BY_KEY[key]
        async with self._lock:
            game = self.game
            if game is not None and game.playing:
                if game.picks:
                    await ack(interaction)
                    return
                self.game = TowerGame.new(game.stake, difficulty, self.cog.rng)
            self.difficulty = difficulty
            self.cog.set_difficulty(self.guild_id, self.owner.id, difficulty)
            await self._refresh(interaction)


# -- Cog -------------------------------------------------------------------------------


class Tower(commands.Cog, name="Torre"):
    """Torre del dragón con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        casino_channel_ids: frozenset[int] = frozenset(),
        rng: random.Random | None = None,
        load_record: Callable[[int, int], Awaitable[int]] | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        # Torres abiertas: para cerrar sus partidas si el bot se apaga.
        # Cada torre sale de aquí al caducar, así que el tamaño está acotado.
        self.boards: set[TowerBoard] = set()
        # Dificultad elegida por (servidor, miembro); se pierde al reiniciar.
        self._levels: dict[tuple[int, int], Difficulty] = {}
        # Récord de pisos por (servidor, miembro). Se lee una vez de los logros
        # (`tower_floor_max`, que sobrevive a reinicios) y luego se lleva en
        # memoria. Crece como mucho hasta los miembros que han jugado.
        self._records: dict[tuple[int, int], int] = {}
        self._load_record = load_record

    async def record(self, guild_id: int, user_id: int) -> int:
        """Récord de pisos de un miembro en una partida (0 si no se sabe)."""
        key = (guild_id, user_id)
        if key not in self._records:
            value = 0
            if self._load_record is not None:
                try:
                    value = await self._load_record(guild_id, user_id)
                except Exception:
                    logger.exception("No se pudo leer el récord de la Torre de %s", user_id)
            self._records[key] = value
        return self._records[key]

    def note_record(self, guild_id: int, user_id: int, floors: int) -> None:
        """Apunta los pisos de una partida terminada si baten el récord."""
        key = (guild_id, user_id)
        self._records[key] = max(self._records.get(key, 0), floors)

    def difficulty_for(self, guild_id: int, user_id: int) -> Difficulty:
        """Dificultad que eligió un miembro la última vez (Fácil si no eligió)."""
        return self._levels.get((guild_id, user_id), DEFAULT_DIFFICULTY)

    def set_difficulty(self, guild_id: int, user_id: int, difficulty: Difficulty) -> None:
        """Recuerda la dificultad de un miembro."""
        self._levels[(guild_id, user_id)] = difficulty

    async def cog_unload(self) -> None:
        """Al apagar, cobra o devuelve las partidas a medias."""
        for board in list(self.boards):
            try:
                await board.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una partida de la Torre al apagar")
            board.stop()
        self.boards.clear()

    async def shout(self, game: TowerGame, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los cobros enormes, para que se vea."""
        if game.status is not Status.CASHED or game.cents < SHOUT_CENTS:
            return
        if not isinstance(channel, discord.abc.Messageable):
            return
        text = (
            f"📣 {DRAGON} ¡{user.mention} ha cobrado **{format_multiplier(game.cents)}** en la "
            f"Torre del dragón ({game.difficulty.label}) tras {game.floor} pisos! "
            f"**+{format_amount(game.net)}**"
        )
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio de la Torre", exc_info=True)

    # -- torre -----------------------------------------------------------------------

    async def _torre_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        difficulty: Difficulty | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/torre` y `.torre`: abre la torre y empieza ya."""
        if guild is None:
            await send_error("La Torre del dragón solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "La Torre del dragón"):
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
        if difficulty is not None:
            self.set_difficulty(guild.id, user.id, difficulty)

        board = TowerBoard(
            self,
            guild_id=guild.id,
            owner=user,
            stake=stake,
            difficulty=self.difficulty_for(guild.id, user.id),
        )
        board.channel = channel
        error = await board.start()
        if error is not None:
            await send_error(error)
            return
        board.message = await send(view=board)
        self.boards.add(board)

    @app_commands.command(
        name="torre",
        description="Torre del dragón: sube pisos esquivando dragones y cobra a tiempo.",
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto 100)",
        nivel="Dificultad (por defecto, la última vez o Fácil)",
    )
    @app_commands.choices(
        nivel=[
            app_commands.Choice(name=f"{d.emoji} {d.label} · {risk_summary(d)}", value=d.key)
            for d in DIFFICULTIES
        ]
    )
    @app_commands.guild_only()
    async def torre(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        nivel: str | None = None,
    ) -> None:
        """Abre una Torre del dragón y empieza la partida.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. Cobra
        la apuesta al empezar y paga al cobrar.
        """
        responder = InteractionResponder(interaction)
        try:
            difficulty = parse_difficulty(nivel) if nivel else None
        except ValueError as error:
            await responder.send_error(str(error))
            return

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._torre_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            difficulty=difficulty,
            send=send,
            send_error=responder.send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="torre")
    @commands.guild_only()
    async def torre_text(
        self, ctx: commands.Context, cantidad: str | None = None, nivel: str | None = None
    ) -> None:
        """Versión de texto: `.torre`, `.torre 500` o `.torre 500 dificil`."""
        responder = ContextResponder(ctx)
        try:
            difficulty = parse_difficulty(nivel) if nivel else None
        except ValueError as error:
            await responder.send_error(str(error))
            return

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._torre_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            difficulty=difficulty,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""

    async def load_record(guild_id: int, user_id: int) -> int:
        profile = await bot.achievements.profile(guild_id, user_id)
        return profile.stats.get(RECORD_STAT, 0)

    await bot.add_cog(
        Tower(
            bot,
            economy=bot.economy,
            casino_channel_ids=bot.casino_channel_ids,
            load_record=load_record,
        )
    )
