"""Negocios: tu propia empresa dentro de `pala` (cog `Negocio`, sin comando propio).

Se entra desde el inicio de `pala` (botón 🏪 **Negocio**, ver `bot.cogs.work`). El panel
es un `LayoutView` con una imagen de la escena de los negocios (`BusinessScene`) en un
`MediaGallery` (siempre PNG: nunca un GIF que luego cambie, por lo de la Torre) y el texto
debajo, que también sirve de reserva si no hay Node ni Chromium. Pantallas:

- **Montar** (sin negocio): el sector, luego el barrio, el nombre, la paleta y el emblema
  con la vista previa de la fachada, y la confirmación con lo que cuesta.
- **Portada:** la fachada con las cifras de hoy. 🔑 **Abrir** (un turno, una franja),
  📦 **Almacén**, 📊 **Cuentas**, 🎨 **Rótulo**, 🚪 **Cerrar el negocio**, el desplegable
  de precios y 🏠 **Inicio** (vuelve al inicio de `pala`).
- **Turno:** el minijuego del sector, un cliente por imagen. Sus botones no los atiende la
  vista sino `Negocio.on_interaction`, por su `custom_id` (`negocio:<panel>:<ronda>:<acción>`),
  como el minijuego de `pala`: así no se pierde ningún clic al redibujar. La imagen del
  siguiente cliente se pinta mientras se atiende al de ahora, y una tarea despide al
  cliente al que se le acaba la paciencia.
- **Tique** del turno, con la nómina del negocio: caja, IRPF a cuenta y embargo.
- **Fase 2:** 🛠️ **Mejoras** (instalar mejoras del local y lanzar campañas), ⭐ **Reseñas**,
  🚚 **Traslado** (a otro barrio, a un local mayor o a uno tuyo de `pisos`), el precio de cada
  producto en el almacén y 🚪 cerrar traspasándolo o liquidando el stock y las mejoras.

Cada noche, una tarea cada 10 minutos cobra el local de cada negocio
(`BusinessService.close_nights`) y la cuota de autónomo de la semana.

Dinero: todo por `EconomyService.business` (detalle fiscal allí) y la cuota con
`charge_self_employed_fee`. Toda acción con dinero lleva `renta.remind` y el resultado del
turno, `renta.hint` (habla la mascota). Alimenta los logros de 🏪 Negocio.

Funciones puente: para `pala`, abrir el panel (`open_panel`), pintar el inicio (`paint`) y
saber si alguien está atendiendo su negocio (`busy`); para `saldo`, lo pendiente
(`pending`); para `perfil`, una línea del negocio (`business_line`). No lanzan y no hacen
nada sin el cog.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import random
import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.services.achievements import StatDelta, business_stats
from bot.services.business import (
    Business,
    move_quote,
    nightly_bill,
    opening_quote,
    rating,
    recommended,
    sale_price,
)
from bot.services.business_catalog import (
    CAMPAIGN_BY_KEY,
    CAMPAIGNS,
    EMBLEMS,
    EQUIPMENT_BY_KEY,
    PALETTES,
    PRICE_LEVELS,
    REVIEW_LINES,
    SECTOR_BY_KEY,
    SECTORS,
    SIZES,
    equipment_for,
)
from bot.services.business_games import COINS, QUOTES, TurnGame
from bot.services.business_scene import BusinessScene
from bot.services.business_view import (
    almacen_state,
    campaign_state,
    cierre_state,
    cliente_state,
    cuentas_state,
    mejoras_state,
    montar_state,
    negocio_state,
    preview_state,
    resenas_state,
    signed,
    size_text,
    traslado_state,
    turn_lines,
)
from bot.services.economy import format_amount, next_daily_at
from bot.services.housing_catalog import BARRIO_BY_KEY, BARRIOS, ISLANDS, THEMES
from bot.services.levels import local_day
from bot.services.negocio import (
    MAX_LOTS,
    BusinessError,
    BusinessService,
    BusinessStatus,
    OwnLocal,
    Turn,
    TurnPlan,
    TurnResult,
)
from bot.services.pending import Pending, Side
from bot.services.pets import Event as PetEvent
from bot.services.pets import Moment, Mood
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import LISTENER_ITEM, TimedLock
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, claim_custom_ids, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Segundos sin tocar el panel antes de que caduque.
PANEL_TIMEOUT = 300
#: Nombre del PNG de la escena en el mensaje.
PNG_NAME = "negocio.png"
#: Pantallas pintadas que se guardan (las del adelanto y las de vuelta).
CACHE_SIZE = 48
#: Prefijo de los `custom_id` de los botones del minijuego.
GAME_PREFIX = "negocio:"
#: Segundos de margen tras la paciencia de un cliente antes de despedirlo (lo que tarda
#: Discord en llevar un clic que salió a tiempo).
GRACE_SECONDS = 1.0
COLOR = discord.Color.from_rgb(31, 181, 173)
COLOR_GAME = discord.Color.from_rgb(88, 101, 242)
COLOR_BAD = discord.Color.from_rgb(80, 84, 92)

#: Emoji de cada producto para los botones (la escena los dibuja con trazos).
PRODUCT_EMOJI: dict[str, str] = {
    "cafe": "☕", "cortado": "🥛", "barraquito": "🍮", "zumo": "🍊", "bocadillo": "🥖",
    "dulce": "🥐", "pan": "🍞", "leche": "🧃", "fruta": "🍎", "conservas": "🥫",
    "limpieza": "🧴", "bebidas": "🥤", "pastillas": "🛑", "bateria": "🔋", "aceite": "🛢️",
    "correa": "⛓️", "bujias": "⚡", "neumatico": "🛞",
}  # fmt: skip
#: Cómo se juega cada minijuego, en una línea.
HOW_TO = {
    "comandas": "Pulsa lo que pide la mesa y luego ✅ Servir. Rápido deja propina.",
    "caja": "Da el cambio con las monedas y pulsa ✅ Cobrar. Si alguien esconde algo, 👀.",
    "diagnostico": "Elige la avería por el síntoma y luego el presupuesto: caro, más fácil "
    "que no lo acepte.",
}

PORTADA = "portada"
ALMACEN = "almacen"
CUENTAS = "cuentas"
ROTULO = "rotulo"
CERRAR = "cerrar"
MONTAR = "montar"
PREVIA = "previa"
CONFIRMAR = "confirmar"
TURNO = "turno"
CIERRE = "cierre"
MEJORAS = "mejoras"
RESENAS = "resenas"
TRASLADO = "traslado"


def game_id(token: int, round_index: int, action: str) -> str:
    """`custom_id` de un botón del minijuego: panel, ronda y acción."""
    return f"{GAME_PREFIX}{token}:{round_index}:{action}"


def parse_game_id(custom_id: str) -> tuple[int, int, str] | None:
    """Lo contrario de `game_id`; `None` si no es un botón del minijuego."""
    if not custom_id.startswith(GAME_PREFIX):
        return None
    token, _, rest = custom_id[len(GAME_PREFIX) :].partition(":")
    round_text, _, action = rest.partition(":")
    if not token.isdigit() or not round_text.isdigit() or not action:
        return None
    return int(token), int(round_text), action


def state_key(state: dict[str, Any]) -> str:
    """Huella de una pantalla: dos pantallas iguales se pintan igual."""
    text = json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(text.encode()).hexdigest()


class Painter:
    """Pinta pantallas, las guarda por su huella y pinta por adelantado las que pueden venir.

    Como el de `pisos`, pero `paint` devuelve `None` si no hay con qué pintar (el panel
    sale entonces en texto). Los adelantos van de uno en uno y se sueltan si el panel ya
    no está donde estaba (`still`).
    """

    def __init__(self, scene: BusinessScene) -> None:
        self.scene = scene
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self._painting: dict[str, asyncio.Future[bytes | None]] = {}
        self._next: list[tuple[dict[str, Any], Callable[[], bool] | None]] = []
        self._worker: asyncio.Task[None] | None = None

    def ready(self, state: dict[str, Any]) -> bytes | None:
        """El PNG si ya está pintado (sin esperar)."""
        return self.cache.get(state_key(state))

    async def paint(self, state: dict[str, Any]) -> bytes | None:
        """El PNG de una pantalla (`None` si no se puede pintar)."""
        key = state_key(state)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if key in self._painting:
            return await asyncio.shield(self._painting[key])
        future: asyncio.Future[bytes | None] = asyncio.get_running_loop().create_future()
        self._painting[key] = future
        png: bytes | None = None
        try:
            pngs = await self.scene.paint([state])
            png = pngs[0] if pngs else None
            if png is not None:
                self.cache[key] = png
                while len(self.cache) > CACHE_SIZE:
                    self.cache.popitem(last=False)
        except Exception:
            logger.exception("No se pudo pintar una pantalla del negocio")
        finally:
            self._painting.pop(key, None)
            future.set_result(png)
        return png

    def prefetch(
        self, states: list[dict[str, Any]], still: Callable[[], bool] | None = None
    ) -> None:
        """Pinta en segundo plano `states` (del más al menos probable), de uno en uno."""
        self._next = [(s, still) for s in states if state_key(s) not in self.cache]
        if self._next and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._work(), name="negocio-adelanto")

    async def _work(self) -> None:
        while self._next:
            state, still = self._next.pop(0)
            if still is not None and not still():
                continue
            await self.paint(state)

    async def close(self) -> None:
        """Para el adelanto y cierra Node y el navegador."""
        if self._worker is not None:
            self._worker.cancel()
        await self.scene.close()


def _stars_text(value: float) -> str:
    return f"{value:.1f}".replace(".", ",").replace(",0", "") + " ⭐"


class NegocioPanel(ui.LayoutView):
    """El panel del negocio de un miembro (solo lo toca su dueño).

    Args:
        cog: El cog `Negocio`.
        guild_id: Servidor.
        owner: Dueño del panel.
        channel: Canal del mensaje (para los avisos).
    """

    def __init__(
        self, cog: Negocio, *, guild_id: int, owner: discord.abc.User, channel: object
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.service = cog.service
        self.guild_id = guild_id
        self.owner = owner
        self.channel = channel
        self.message: discord.Message | None = None
        self.screen = PORTADA
        self.status: BusinessStatus | None = None
        self.notes: list[str] = []
        self.product = ""
        self.draft: dict[str, Any] = {
            "sector": "", "barrio": "", "name": "Tu negocio", "palette": 0, "emblem": 0,
        }  # fmt: skip
        self.turn: Turn | None = None
        self.result: TurnResult | None = None
        #: El azar del próximo turno, para pintar su primer cliente antes de 🔑 Abrir.
        self.plan: TurnPlan | None = None
        #: Lo elegido en 🛠️ Mejoras: la mejora y la campaña.
        self.equipment = ""
        self.campaign = ""
        #: El traslado que se mira: barrio y tamaño, o un local propio (`own`).
        self.move: dict[str, Any] = {"barrio": "", "size": 0, "own": ""}
        self.own_locals: list[OwnLocal] = []
        self.token = cog.new_token()
        self._shown = ""
        self._lock = TimedLock("negocio:NegocioPanel._lock")
        self._render_lock = TimedLock("negocio:NegocioPanel._render_lock")
        self._ticket = 0
        self._timer: asyncio.Task[None] | None = None
        self._finishing = False

    # -- Lo que se pinta ------------------------------------------------------------------

    def _foot(self) -> str:
        status = self.status
        if status is None:
            return ""
        blocker = status.blocker
        if blocker:
            return blocker
        free = status.agenda.free
        return f"Abrir gasta 1 franja. Te quedan {free}."

    def scene_state(self) -> dict[str, Any] | None:
        """El estado de la escena de la pantalla de ahora (`None` si no tiene imagen)."""
        business = self.status.business if self.status else None
        draft = self.draft
        if self.screen == MONTAR:
            return montar_state(draft["sector"] or None)
        if self.screen in (PREVIA, CONFIRMAR) or (self.screen == ROTULO and business is None):
            return preview_state(
                draft["sector"], draft["barrio"], draft["name"], draft["palette"], draft["emblem"]
            )
        if business is None:
            return None
        if self.screen == ROTULO:
            return preview_state(
                business.sector, business.barrio, draft["name"], draft["palette"], draft["emblem"]
            )
        if self.screen == ALMACEN:
            return almacen_state(business, "Cada compra paga su IGIC. Lo perecedero se estropea.")
        if self.screen == CUENTAS:
            return cuentas_state(business)
        if self.screen == MEJORAS:
            return mejoras_state(business, self._today())
        if self.screen == RESENAS:
            return resenas_state(business, self._today())
        if self.screen == TRASLADO and (target := self._move_target()) is not None:
            barrio, size, quote, own = target
            return traslado_state(business, barrio, size, quote, own=own)
        if self.screen == TURNO and self.turn is not None and self.turn.game.current is not None:
            return cliente_state(self.turn.business, self.turn.game)
        if self.screen == CIERRE and self.result is not None:
            return cierre_state(self.result.business, self.result)
        return negocio_state(
            business, foot=self._foot(), rivals=self.status.rivals if self.status else 0
        )

    def _neighbours(self) -> list[dict[str, Any]]:
        """Pantallas a las que se puede ir desde esta (para pintarlas antes del clic)."""
        business = self.status.business if self.status else None
        if self.screen == TURNO and self.turn is not None:
            return self._next_clients()
        if business is None:
            return []
        out = []
        if self.screen in (PORTADA, CIERRE):
            out = self._first_client()
            out.append(
                almacen_state(business, "Cada compra paga su IGIC. Lo perecedero se estropea.")
            )
            out.append(mejoras_state(business, self._today()))
            out.append(cuentas_state(business))
            out.append(resenas_state(business, self._today()))
        elif self.screen in (ALMACEN, CUENTAS, ROTULO, CERRAR, MEJORAS, RESENAS, TRASLADO):
            out = [
                negocio_state(
                    business, foot=self._foot(), rivals=self.status.rivals if self.status else 0
                )
            ]
        return out

    def _today(self) -> Any:
        return local_day(self.service.now())

    def _move_target(self) -> tuple[str, int, Any, bool] | None:
        """El local que se mira en 🚚 Traslado: `(barrio, tamaño, presupuesto, propio)`."""
        if self.status is None:
            return None
        business = self.status.business
        own = next((o for o in self.own_locals if o.listing.key == self.move["own"]), None)
        if own is not None and own.size is not None:
            barrio = BARRIO_BY_KEY[own.listing.barrio]
            return barrio.key, own.size, move_quote(business, barrio, own.size, own=True), True
        barrio_key = self.move["barrio"] or business.barrio
        size = self.move["size"]
        return barrio_key, size, self.service.move_quote(business, barrio_key, size), False

    def _first_client(self) -> list[dict[str, Any]]:
        """El primer cliente del próximo turno, ya sorteado: 🔑 Abrir no espera al dibujo."""
        assert self.status is not None
        self.plan = self.plan or self.service.plan()
        game = self.service.preview(self.status, self.plan)
        return [] if game is None else [cliente_state(self.status.business, game)]

    def _next_clients(self) -> list[dict[str, Any]]:
        """El presupuesto del cliente de ahora (taller) y el siguiente cliente."""
        assert self.turn is not None
        game = self.turn.game
        out: list[dict[str, Any]] = []
        if game.current is None:
            return out
        if game.sector.game == "diagnostico" and game.step == "diagnostico":
            out.append({**cliente_state(self.turn.business, game), "step": "presupuesto"})
        if game.index + 1 < len(game.clients):
            ahead = _advanced(game)
            out.append(cliente_state(self.turn.business, ahead))
        return out

    def text(self) -> str:
        """El texto debajo de la imagen (y la reserva si no hay imagen)."""
        status = self.status
        lines: list[str] = []
        if self.screen == MONTAR:
            lines.append("## 🏪 Monta tu negocio")
            for sector in SECTORS:
                lines.append(f"{sector.emoji} **{sector.name}** · {sector.blurb}")
            lines.append(
                "-# Eres autónomo: respondes con lo tuyo y el alquiler corre aunque no abras."
            )
        elif self.screen in (PREVIA, CONFIRMAR):
            draft = self.draft
            sector = SECTOR_BY_KEY[draft["sector"]]
            barrio = BARRIO_BY_KEY[draft["barrio"]]
            quote = opening_quote(sector, barrio)
            lines.append(f"## {sector.emoji} {draft['name']} · {barrio.name}")
            lines.append(
                f"Tema del barrio: {THEMES[barrio.theme]} · demanda ×"
                f"{sector.fit.get(barrio.theme, 1.0):.2f}".replace(".", ",")
            )
            lines.append(
                f"🧾 Tasa de apertura **{format_amount(quote.license)}** · Acondicionamiento "
                f"**{format_amount(quote.setup)}** + IGIC {format_amount(quote.setup_igic)} · "
                f"Fianza (2 meses) **{format_amount(quote.deposit)}**"
            )
            lines.append(
                f"🔑 Alquiler: **{format_amount(quote.rent)}** cada noche, más IGIC y suministros."
                " Se paga aunque no abras."
            )
            lines.append(
                f"Total al montarlo: **{format_amount(quote.total)}** (más la cuota de autónomo)"
            )
            if self.screen == CONFIRMAR:
                lines.append("\n**¿Lo montas?** La fianza vuelve al cerrar; lo demás, no.")
        elif status is not None:
            business = status.business
            sector = business.sector_info
            if self.screen == TURNO and self.turn is not None:
                lines.extend(self._turn_text())
            elif self.screen == CIERRE and self.result is not None:
                lines.extend(self._result_text())
            else:
                lines.append(
                    f"## {sector.emoji} {business.name} · {business.barrio_info.name} · "
                    f"{_stars_text(business.stars)}"
                )
                if self.screen == ALMACEN:
                    lines.extend(self._stock_lines(business))
                elif self.screen == CUENTAS:
                    totals = business.week_totals
                    lines.append(
                        "Esta semana: "
                        + (" · ".join(f"{k} {signed(v)}" for k, v in totals.items()) or "nada aún")
                    )
                elif self.screen == CERRAR:
                    lines.extend(self._close_lines(business))
                elif self.screen == MEJORAS:
                    lines.extend(self._mejoras_lines(business))
                elif self.screen == RESENAS:
                    lines.extend(self._resenas_lines(business))
                elif self.screen == TRASLADO:
                    lines.extend(self._traslado_lines(business))
                else:
                    bill = nightly_bill(business)
                    lines.append(
                        f"Caja de hoy **{signed(business.sales_today)}** · "
                        f"{business.clients_today} clientes · {business.turns_today} turnos · "
                        "franjas libres: "
                        f"{status.agenda.free}"
                    )
                    lines.append(
                        f"-# Esta noche: alquiler y suministros {format_amount(bill.total)}"
                        + (
                            f" · debes {format_amount(business.rent_debt)} del local"
                            if business.rent_debt
                            else ""
                        )
                    )
                    extras = [size_text(business)]
                    average, count = rating(business)
                    if count:
                        extras.append(
                            f"⭐ {average:.1f}".replace(".", ",")
                            + f" en {count} reseñas"
                            + (" · recomendado" if recommended(business) else "")
                        )
                    if campaign := campaign_state(business, self._today()):
                        extras.append(f"📣 {campaign['name']} ({campaign['left']})")
                    lines.append("-# " + " · ".join(extras))
                    if blocker := status.blocker:
                        lines.append(f"-# ⚠️ {blocker}")
        notes, self.notes = self.notes, []
        lines.extend(notes)
        return "\n".join(lines)[:3900]

    @staticmethod
    def _stock_lines(business: Business) -> list[str]:
        out = []
        for product in business.sector_info.products:
            units = business.stock.get(product.key, 0)
            level = business.level(product.key)
            out.append(
                f"{PRODUCT_EMOJI.get(product.key, '📦')} {product.name}: **{units}** · cuesta "
                f"{format_amount(product.cost)} · se vende a "
                f"{format_amount(sale_price(product, level.key))}"
                + (
                    f" ({level.name.split(' (')[0].lower()})"
                    if product.key in business.prices
                    else ""
                )
                + (f" · caduca un {round(product.spoil * 100)} %" if product.spoil else "")
            )
        return out

    def _close_lines(self, business: Business) -> list[str]:
        offer = self.service.offer(business)
        refund = max(0, business.deposit - business.rent_debt)
        lines = [f"🚪 **¿Cierras {business.name}?** Vuelven {format_amount(refund)} de la fianza."]
        if offer.can_traspasar:
            lines.append(
                f"🤝 **Traspaso:** te dan {format_amount(offer.traspaso)}: el stock "
                f"({format_amount(offer.stock)}), las mejoras y la obra "
                f"({format_amount(offer.book)}) y la clientela ({format_amount(offer.goodwill)})."
                " La clientela tributa al 19 % (ganancia patrimonial)."
            )
        else:
            lines.append(
                "-# 🤝 Nadie quiere traspasarlo: hace falta una semana abierto, 3 estrellas y un "
                "local alquilado."
            )
        lines.append(
            f"🧹 **Liquidar:** el mayorista te recompra el stock por "
            f"{format_amount(offer.liquidation_stock)} y vendes las mejoras de segunda mano por "
            f"{format_amount(offer.liquidation_equipment)}."
        )
        return lines

    def _mejoras_lines(self, business: Business) -> list[str]:
        lines = ["🛠️ **Mejoras del local** · se amortizan cada noche"]
        for item in equipment_for(business.sector):
            mark = "✅" if item.key in business.equipment else "▫️"
            lines.append(f"{mark} {item.name}: {item.effect()} · {format_amount(item.cost)} + IGIC")
        active = campaign_state(business, self._today())
        lines.append(
            "📣 **Publicidad** · "
            + (f"en marcha: {active['name']} ({active['left']})" if active else "sin campaña")
        )
        for campaign in CAMPAIGNS:
            lines.append(
                f"- {campaign.name}: +{round(campaign.boost * 100)} % de clientes, "
                f"{campaign.days} días · {format_amount(campaign.cost)} + IGIC"
            )
        return lines

    @staticmethod
    def _resenas_lines(business: Business) -> list[str]:
        average, count = rating(business)
        if not count:
            return ["⭐ Aún no hay reseñas: las dejan algunos clientes al acabar cada turno."]
        lines = [
            f"⭐ **{average:.1f}**".replace(".", ",")
            + f" de media en {count} reseñas"
            + (" · 🏅 Recomendado" if recommended(business) else "")
        ]
        for _day, who, n, line in reversed(business.reviews[-5:]):
            text = REVIEW_LINES[n][line % len(REVIEW_LINES[n])]
            lines.append(f"{'★' * n}{'☆' * (5 - n)} **{who}**: {text}")
        return lines

    def _traslado_lines(self, business: Business) -> list[str]:
        target = self._move_target()
        assert target is not None
        barrio, size, quote, own = target
        place = BARRIO_BY_KEY[barrio]
        lines = [
            f"🚚 **Traslado** a {place.name}, local {SIZES[size].name.lower()}"
            + (" (tuyo)" if own else ""),
            f"🧾 Tasa {format_amount(quote.opening.license)} · acondicionamiento "
            f"{format_amount(quote.opening.setup)} + IGIC · fianza nueva "
            f"{format_amount(quote.opening.deposit)} · vuelve la de ahora: "
            f"{format_amount(quote.refund)}",
            (
                "🔑 Sin alquiler: el local es tuyo (sus recibos siguen en `pisos`)."
                if own
                else f"🔑 Alquiler: {format_amount(quote.opening.rent)} cada noche, más IGIC."
            ),
        ]
        if not quote.same_barrio:
            lines.append("-# Cambias de barrio: la reputación y la fidelidad bajan a la mitad.")
        if not self.own_locals:
            lines.append(
                "-# Si compras un local entero en `pisos`, podrás traerte aquí el negocio."
            )
        return lines

    def _turn_text(self) -> list[str]:
        assert self.turn is not None
        game = self.turn.game
        client = game.current
        lines = [f"-# 🎮 {HOW_TO[game.sector.game]}"]
        if game.last:
            lines.append(f"**{game.last}**")
        if client is None:
            return lines
        who = f"**{client.regular}** (habitual)" if client.regular else "Un cliente"
        if game.sector.game == "comandas":
            if client.usual:
                lines.append(f"{who}: «Lo de siempre». Tienes que acordarte.")
            else:
                order = ", ".join(
                    f"{n} {game.sector.product(k).name.lower()}" for k, n in client.order.items()
                )  # type: ignore[union-attr]
                lines.append(f"{who} pide: {order}")
            tray = game.tray_counts()
            lines.append(
                "🍽️ Bandeja: "
                + (
                    ", ".join(f"{n} {game.sector.product(k).name.lower()}" for k, n in tray.items())
                    or "vacía"
                )  # type: ignore[union-attr]
            )
        elif game.sector.game == "caja":
            items = ", ".join(
                f"{n} {game.sector.product(k).name.lower()}" for k, n in client.order.items()
            )  # type: ignore[union-attr]
            given = sum(game.given)
            lines.append(f"{who} lleva {items}.")
            lines.append(
                f"🧾 Total **{format_amount(game.total())}** · paga con "
                f"**{format_amount(game.paid)}** · "
                f"cambio dado: **{format_amount(given)}**"
            )
        else:
            lines.append(f"{who}: «{client.symptom}»")
            if game.step == "presupuesto":
                product = game.sector.product(game.diagnosis or "")
                lines.append(
                    f"🔧 Diagnóstico: {product.name.lower() if product else '?'}. "
                    "Elige el presupuesto."
                )
        cash = sum(o.revenue + o.tip for o in game.outcomes)
        lines.append(
            f"⏳ Se va <t:{int(game.client_deadline) + 1}:R> · caja del turno {signed(cash)} · "
            f"cierras <t:{int(game.deadline) + 1}:R>"
        )
        return lines

    def _result_text(self) -> list[str]:
        result = self.result
        assert result is not None
        summary = result.summary
        lines = [
            f"## 🧾 {summary.grade} · {summary.served} de {len(summary.outcomes)} clientes · "
            f"{signed(summary.sales)}",
        ]
        if result.withheld:
            lines.append(
                f"-# 🐶 IRPF a cuenta: −{format_amount(result.withheld)} ({TAX_COLLECTOR})"
            )
        if result.garnished:
            lines.append(
                f"-# ⚖️ Embargo por tu deuda con Hacienda: −{format_amount(result.garnished)}"
            )
        if result.bank_garnished:
            lines.append(
                "-# ⚖️ Embargo del juzgado por tu deuda con el banco (art. 607 LEC): "
                f"−{format_amount(result.bank_garnished)}"
            )
        lines.append(
            f"-# Estrellas: {_stars_text(result.stars_before)} → "
            f"{_stars_text(result.business.stars)}"
        )
        lines.extend(f"- {line}" for line in turn_lines(result))
        for who, n, line in result.reviews[:3]:
            text = REVIEW_LINES[n][line % len(REVIEW_LINES[n])]
            lines.append(f"-# {'★' * n}{'☆' * (5 - n)} {who}: «{text}»")
        return lines

    # -- Componentes ------------------------------------------------------------------

    def _button(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        name: str,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> ui.Button:
        button: ui.Button = ui.Button(
            label=label[:80], style=style, disabled=disabled, custom_id=f"negocio-panel:{name}"
        )
        button.callback = callback  # type: ignore[method-assign]
        return button

    def _game_button(
        self,
        label: str,
        action: str,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
        emoji: str | None = None,
    ) -> ui.Button:
        assert self.turn is not None
        button: ui.Button = ui.Button(
            label=label[:80],
            style=style,
            disabled=disabled,
            emoji=emoji,
            custom_id=game_id(self.token, self.turn.game.round, action),
        )
        setattr(button, LISTENER_ITEM, True)
        return button

    def _select(
        self,
        placeholder: str,
        options: list[discord.SelectOption],
        name: str,
        callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> ui.Select:
        select: ui.Select = ui.Select(
            placeholder=placeholder[:150], options=options[:25], custom_id=f"negocio-panel:{name}"
        )

        async def run(interaction: discord.Interaction) -> None:
            await callback(interaction, select.values[0])

        select.callback = run  # type: ignore[method-assign]
        return select

    def rebuild(self, has_image: bool) -> None:
        """Monta la imagen, el texto y los controles de la pantalla de ahora."""
        self.clear_items()
        color = COLOR_GAME if self.screen == TURNO else COLOR
        container = ui.Container(accent_colour=color)
        if has_image:
            container.add_item(
                ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{PNG_NAME}"))
            )
        container.add_item(ui.TextDisplay(self.text() or "…"))
        self.add_item(container)
        for row in self._rows():
            if row.children:
                self.add_item(row)

    def _rows(self) -> list[ui.ActionRow]:
        green, blue, red = (
            discord.ButtonStyle.success,
            discord.ButtonStyle.primary,
            discord.ButtonStyle.danger,
        )
        home = self._button("🏠 Inicio", self._home, "inicio")
        back = self._button("⬅️ Volver", self._portada, "volver")
        if self.screen == MONTAR:
            options = [
                discord.SelectOption(
                    label=s.name, value=s.key, emoji=s.emoji, description=s.blurb[:100]
                )
                for s in SECTORS
            ]
            return [
                ui.ActionRow(
                    self._select("Elige qué negocio montas", options, "sector", self._pick_sector)
                ),
                ui.ActionRow(home),
            ]
        if self.screen == PREVIA:
            return self._draft_rows(home)
        if self.screen == CONFIRMAR:
            total = opening_quote(
                SECTOR_BY_KEY[self.draft["sector"]], BARRIO_BY_KEY[self.draft["barrio"]]
            ).total
            return [
                ui.ActionRow(
                    self._button(
                        f"✅ Montarlo · {format_amount(total)}",
                        self._open,
                        "abrir-negocio",
                        style=green,
                    ),
                    self._button("⬅️ Atrás", self._back_to_draft, "atras"),
                )
            ]
        if self.screen == TURNO:
            return self._game_rows()
        if self.status is None:
            return [ui.ActionRow(home)]
        business = self.status.business
        if self.screen == ALMACEN:
            options = [
                discord.SelectOption(
                    label=f"{p.name} ({business.stock.get(p.key, 0)})",
                    value=p.key,
                    emoji=PRODUCT_EMOJI.get(p.key),
                    description=f"Lote de {p.pack}: {format_amount(p.cost * p.pack)} + IGIC",
                    default=p.key == self.product,
                )
                for p in business.sector_info.products
            ]
            rows = [
                ui.ActionRow(
                    self._select("Elige qué comprar", options, "producto", self._pick_product)
                )
            ]
            if self.product:
                current = business.prices.get(self.product, "")
                levels = [
                    discord.SelectOption(
                        label="Como el resto", value="general", default=not current
                    ),
                    *(
                        discord.SelectOption(label=p.name, value=p.key, default=p.key == current)
                        for p in PRICE_LEVELS
                    ),
                ]
                rows.append(
                    ui.ActionRow(
                        self._select(
                            "🏷️ Precio de este producto", levels, "precio-producto",
                            self._set_product_price,
                        )
                    )
                )  # fmt: skip
                buy = ui.ActionRow()
                for lots in (1, 5, MAX_LOTS // 2):
                    buy.add_item(
                        self._button(
                            f"🛒 {lots} lote{'s' if lots > 1 else ''}",
                            self._buy(lots),
                            f"comprar:{lots}",
                            style=green,
                        )
                    )
                rows.append(buy)
            rows.append(ui.ActionRow(back, home))
            return rows
        if self.screen == CUENTAS:
            return [ui.ActionRow(back, home)]
        if self.screen == RESENAS:
            return [ui.ActionRow(back, home)]
        if self.screen == MEJORAS:
            return self._mejoras_rows(business, back, home)
        if self.screen == TRASLADO:
            return self._traslado_rows(business, back)
        if self.screen == ROTULO:
            return [*self._style_rows(), ui.ActionRow(
                self._button("✏️ Nombre", self._rename, "nombre"),
                self._button("✅ Guardar el rótulo", self._save_style, "guardar", style=green),
                back,
            )]  # fmt: skip
        if self.screen == CERRAR:
            offer = self.service.offer(business)
            row = ui.ActionRow()
            if offer.can_traspasar:
                row.add_item(
                    self._button(
                        f"🤝 Traspasar · {format_amount(offer.traspaso)}",
                        self._close_traspaso,
                        "traspasar",
                        style=green,
                    )
                )
            row.add_item(
                self._button(
                    f"🚪 Cerrar y liquidar · {format_amount(offer.liquidation)}",
                    self._close,
                    "cerrar-si",
                    style=red,
                )
            )
            row.add_item(self._button("⬅️ No", self._portada, "cerrar-no"))
            return [row]
        if self.screen == CIERRE:
            return [
                ui.ActionRow(
                    self._button(
                        "🔑 Otro turno",
                        self._start,
                        "otro",
                        style=green,
                        disabled=self.status.blocker is not None,
                    ),
                    self._button("🏪 Portada", self._portada, "portada"),
                    home,
                )
            ]
        # Portada.
        main = ui.ActionRow(
            self._button(
                "🔑 Abrir · 1 franja",
                self._start,
                "turno",
                style=green,
                disabled=self.status.blocker is not None,
            ),
            self._button("📦 Almacén", self._almacen, "almacen", style=blue),
            self._button("🛠️ Mejoras", self._mejoras, "mejoras", style=blue),
            self._button("📊 Cuentas", self._cuentas, "cuentas"),
            self._button("⭐ Reseñas", self._resenas, "resenas"),
        )
        more = ui.ActionRow(
            self._button("🎨 Rótulo", self._rotulo, "rotulo"),
            self._button("🚚 Traslado", self._traslado, "traslado"),
            self._button("🚪 Cerrar el negocio", self._confirm_close, "cerrar"),
            home,
        )
        prices = [
            discord.SelectOption(label=p.name, value=p.key, default=p.key == business.price)
            for p in PRICE_LEVELS
        ]
        price_row = ui.ActionRow(self._select("🏷️ Precios", prices, "precio", self._set_price))
        return [main, more, price_row]

    def _mejoras_rows(
        self, business: Business, back: ui.Button, home: ui.Button
    ) -> list[ui.ActionRow]:
        green = discord.ButtonStyle.success
        rows = []
        items = [e for e in equipment_for(business.sector) if e.key not in business.equipment]
        if items:
            options = [
                discord.SelectOption(
                    label=e.name, value=e.key, description=e.effect()[:100],
                    default=e.key == self.equipment,
                )
                for e in items
            ]  # fmt: skip
            rows.append(
                ui.ActionRow(
                    self._select("Elige una mejora", options, "mejora", self._pick_equipment)
                )
            )
        active = campaign_state(business, self._today())
        if active is None:
            options = [
                discord.SelectOption(
                    label=c.name, value=c.key, default=c.key == self.campaign,
                    description=f"+{round(c.boost * 100)} % de clientes durante {c.days} días",
                )
                for c in CAMPAIGNS
            ]  # fmt: skip
            rows.append(
                ui.ActionRow(
                    self._select("Elige una campaña", options, "campana", self._pick_campaign)
                )
            )
        last = ui.ActionRow()
        if (item := EQUIPMENT_BY_KEY.get(self.equipment)) and item in items:
            last.add_item(
                self._button(
                    f"🛒 Instalar · {format_amount(item.cost)} + IGIC",
                    self._install, "instalar", style=green,
                )
            )  # fmt: skip
        if active is None and (campaign := CAMPAIGN_BY_KEY.get(self.campaign)):
            last.add_item(
                self._button(
                    f"📣 Lanzar · {format_amount(campaign.cost)} + IGIC",
                    self._launch, "lanzar", style=green,
                )
            )  # fmt: skip
        last.add_item(back)
        last.add_item(home)
        rows.append(last)
        return rows

    def _traslado_rows(self, business: Business, back: ui.Button) -> list[ui.ActionRow]:
        target = self._move_target()
        assert target is not None
        barrio, size, quote, own = target
        barrios = [
            discord.SelectOption(
                label=b.name, value=b.key, description=f"{b.town} · {THEMES[b.theme]}"[:100],
                default=not own and b.key == barrio,
            )
            for b in BARRIOS
        ]  # fmt: skip
        sizes = [
            discord.SelectOption(
                label=f"Local {s.name.lower()}", value=str(i), default=not own and i == size,
                description=f"{round(business.sector_info.m2 * s.m2)} m² · "
                f"×{s.clients:.1f} clientes por turno".replace(".", ","),
            )
            for i, s in enumerate(SIZES)
        ]  # fmt: skip
        pick_barrio = self._select(
            "Elige el barrio", barrios, "traslado-barrio", self._pick_move_barrio
        )
        pick_size = self._select("Elige el tamaño", sizes, "traslado-tamano", self._pick_move_size)
        rows = [ui.ActionRow(pick_barrio), ui.ActionRow(pick_size)]
        fit = [o for o in self.own_locals if o.fits]
        if fit:
            locals_ = [
                discord.SelectOption(
                    label=f"{o.listing.title}"[:100], value=o.listing.key,
                    description=f"{BARRIO_BY_KEY[o.listing.barrio].name} · {o.listing.m2} m² · "
                    f"local {SIZES[o.size or 0].name.lower()}"[:100],
                    default=own and o.listing.key == self.move["own"],
                )
                for o in fit
            ]  # fmt: skip
            title = "🏠 A un local tuyo de pisos"
            rows.append(
                ui.ActionRow(self._select(title, locals_, "traslado-propio", self._pick_own))
            )
        # Donde ya está no se traslada (el servicio también lo comprueba).
        here = (
            business.own_listing == self.move["own"]
            if own
            else quote.same_barrio and size == business.size and not business.own_listing
        )
        go = self._button(
            f"🚚 Trasladarse · {format_amount(quote.total)}",
            self._do_move,
            "trasladar",
            style=discord.ButtonStyle.success,
            disabled=here,
        )
        rows.append(ui.ActionRow(go, back))
        return rows

    def _style_rows(self) -> list[ui.ActionRow]:
        palettes = [
            discord.SelectOption(label=name, value=str(i), default=i == self.draft["palette"])
            for i, (name, _main, _accent) in enumerate(PALETTES)
        ]
        emblems = [
            discord.SelectOption(label=name, value=str(i), default=i == self.draft["emblem"])
            for i, (_key, name) in enumerate(EMBLEMS)
        ]
        return [
            ui.ActionRow(
                self._select("🎨 Colores del rótulo", palettes, "paleta", self._pick_palette)
            ),
            ui.ActionRow(self._select("✨ Emblema", emblems, "emblema", self._pick_emblem)),
        ]

    def _draft_rows(self, home: ui.Button) -> list[ui.ActionRow]:
        sector = SECTOR_BY_KEY[self.draft["sector"]]
        barrios = [
            discord.SelectOption(
                label=f"{b.name} ({ISLANDS[b.island]})"[:100],
                value=b.key,
                description=(
                    f"{THEMES[b.theme]} · demanda ×{sector.fit.get(b.theme, 1.0):.2f} · alquiler "
                    f"{format_amount(opening_quote(sector, b).rent)}/día"
                ).replace(".", ",", 1)[:100],
                default=b.key == self.draft["barrio"],
            )
            for b in BARRIOS
        ]
        return [
            ui.ActionRow(self._select("📍 Barrio", barrios, "barrio", self._pick_barrio)),
            *self._style_rows(),
            ui.ActionRow(
                self._button("✏️ Nombre", self._rename, "nombre"),
                self._button(
                    "➡️ Seguir", self._to_confirm, "seguir", style=discord.ButtonStyle.success
                ),
                self._button("⬅️ Otro sector", self._to_montar, "otro-sector"),
                home,
            ),
        ]

    def _game_rows(self) -> list[ui.ActionRow]:
        assert self.turn is not None
        game = self.turn.game
        client = game.current
        if client is None:
            return []
        rows: list[ui.ActionRow] = []
        row = ui.ActionRow()

        def push(button: ui.Button) -> None:
            nonlocal row
            if len(row.children) == 5:
                rows.append(row)
                row = ui.ActionRow()
            row.add_item(button)

        green = discord.ButtonStyle.success
        if game.sector.game == "comandas":
            for product in game.sector.products:
                left = game.available(product.key)
                push(
                    self._game_button(
                        f"{product.name} ({left})", f"p:{product.key}",
                        emoji=PRODUCT_EMOJI.get(product.key), disabled=left <= 0,
                    )
                )  # fmt: skip
            push(self._game_button("Deshacer", "undo", emoji="↩️"))
            push(self._game_button("Servir", "serve", emoji="✅", style=green))
        elif game.sector.game == "caja":
            for coin in COINS:
                push(self._game_button(format_amount(coin), f"c:{coin}"))
            push(self._game_button("Deshacer", "undo", emoji="↩️"))
            push(self._game_button("Bolsillo", "thief", emoji="👀", disabled=game.accused))
            push(self._game_button("Cobrar", "charge", emoji="✅", style=green))
        elif game.step == "diagnostico":
            for key in client.options:
                product = game.sector.product(key)
                assert product is not None
                push(
                    self._game_button(
                        f"{product.name} ({game.stock.get(key, 0)})", f"d:{key}",
                        emoji=PRODUCT_EMOJI.get(key), disabled=game.stock.get(key, 0) <= 0,
                    )
                )  # fmt: skip
            push(self._game_button("No tengo la pieza", "nopart", emoji="🚫"))
        else:
            for index, (_key, name, _mult, _accept) in enumerate(QUOTES):
                push(
                    self._game_button(
                        f"{name} · {format_amount(game.quote_amount(index))}", f"q:{index}",
                        style=green if index == 1 else discord.ButtonStyle.secondary,
                    )
                )  # fmt: skip
        rows.append(row)
        return rows

    # -- Enseñar ----------------------------------------------------------------------

    async def refresh(self) -> None:
        """Vuelve a leer el negocio."""
        self.status = await self.service.status(self.guild_id, self.owner.id)
        if self.status is None and self.screen not in (MONTAR, PREVIA, CONFIRMAR):
            self.screen = MONTAR

    def _where(self) -> tuple[Any, ...]:
        game = self.turn.game if self.turn else None
        return (self.screen, game.index if game else -1, game.step if game else "")

    async def show(self, interaction: discord.Interaction | None, *, reload: bool = True) -> None:
        """Pinta la pantalla de ahora y la pone en el mensaje."""
        if reload:
            await self.refresh()
        state = self.scene_state()
        png = await self.cog.painter.paint(state) if state is not None else None
        key = state_key(state) if (state is not None and png is not None) else ""
        self.rebuild(has_image=png is not None)
        kwargs: dict[str, Any] = {"view": self}
        if key != self._shown:
            kwargs["attachments"] = (
                [discord.File(io.BytesIO(png), filename=PNG_NAME)] if png is not None else []
            )
        await self._edit(interaction, **kwargs)
        self._shown = key
        self._prefetch()

    def _prefetch(self) -> None:
        here = self._where()
        states = self._neighbours()
        if states:
            self.cog.painter.prefetch(states, still=lambda: self._where() == here)

    async def _edit(self, interaction: discord.Interaction | None, **kwargs: Any) -> None:
        try:
            if interaction is not None:
                await edit(interaction, **kwargs)
                if interaction.message is not None:
                    self.message = interaction.message
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar el panel del negocio", exc_info=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño toca su negocio. Los botones del minijuego van por el listener."""
        custom_id = (interaction.data or {}).get("custom_id")
        if isinstance(custom_id, str) and parse_game_id(custom_id) is not None:
            return False
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Este negocio es de {self.owner.display_name}. El tuyo, en `pala`.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había un turno a medias, se cierra y se cobra."""
        if self.turn is not None:
            await self._finish(None)
        self.cog.panels.discard(self)
        for item in self.walk_children():
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True
        await self._edit(None, view=self)

    # -- Navegar ----------------------------------------------------------------------

    async def _go(self, interaction: discord.Interaction, screen: str) -> None:
        await ack(interaction)
        self.screen = screen
        await self.show(interaction)

    async def _portada(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, PORTADA)

    async def _almacen(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, ALMACEN)

    async def _cuentas(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CUENTAS)

    async def _confirm_close(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CERRAR)

    async def _mejoras(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, MEJORAS)

    async def _resenas(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, RESENAS)

    async def _traslado(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        self.own_locals = await self.service.own_locals(self.guild_id, self.owner.id)
        business = self.status.business if self.status else None
        if business is not None and not self.move["barrio"]:
            self.move = {
                "barrio": business.barrio,
                "size": min(len(SIZES) - 1, business.size + 1),
                "own": "",
            }
        self.screen = TRASLADO
        await self.show(interaction)

    async def _pick_equipment(self, interaction: discord.Interaction, value: str) -> None:
        self.equipment = value if value in EQUIPMENT_BY_KEY else ""
        await self._go(interaction, MEJORAS)

    async def _pick_campaign(self, interaction: discord.Interaction, value: str) -> None:
        self.campaign = value if value in CAMPAIGN_BY_KEY else ""
        await self._go(interaction, MEJORAS)

    async def _pick_move_barrio(self, interaction: discord.Interaction, value: str) -> None:
        if value in BARRIO_BY_KEY:
            self.move.update(barrio=value, own="")
        await self._go(interaction, TRASLADO)

    async def _pick_move_size(self, interaction: discord.Interaction, value: str) -> None:
        if value.isdigit() and int(value) < len(SIZES):
            self.move.update(size=int(value), own="")
        await self._go(interaction, TRASLADO)

    async def _pick_own(self, interaction: discord.Interaction, value: str) -> None:
        if any(o.listing.key == value for o in self.own_locals):
            self.move["own"] = value
        await self._go(interaction, TRASLADO)

    async def _rotulo(self, interaction: discord.Interaction) -> None:
        if self.status is not None:
            business = self.status.business
            self.draft.update(name=business.name, palette=business.palette, emblem=business.emblem)
        await self._go(interaction, ROTULO)

    async def _home(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.turn is not None:
            return
        self.stop()
        self.cog.panels.discard(self)
        from bot.cogs import work as curro  # el inicio es de `pala`

        await curro.home(self.cog.bot, interaction, self.owner, self.channel)

    # -- Montar -----------------------------------------------------------------------

    async def _to_montar(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, MONTAR)

    async def _pick_sector(self, interaction: discord.Interaction, value: str) -> None:
        if value not in SECTOR_BY_KEY:
            await ack(interaction)
            return
        self.draft["sector"] = value
        if not self.draft["barrio"]:
            sector = SECTOR_BY_KEY[value]
            # De entrada, el barrio donde más demanda tiene el sector.
            self.draft["barrio"] = max(BARRIOS, key=lambda b: sector.fit.get(b.theme, 1.0)).key
        await self._go(interaction, PREVIA)

    async def _pick_barrio(self, interaction: discord.Interaction, value: str) -> None:
        if value in BARRIO_BY_KEY:
            self.draft["barrio"] = value
        await self._go(interaction, self.screen)

    async def _pick_palette(self, interaction: discord.Interaction, value: str) -> None:
        if value.isdigit() and int(value) < len(PALETTES):
            self.draft["palette"] = int(value)
        await self._go(interaction, self.screen)

    async def _pick_emblem(self, interaction: discord.Interaction, value: str) -> None:
        if value.isdigit() and int(value) < len(EMBLEMS):
            self.draft["emblem"] = int(value)
        await self._go(interaction, self.screen)

    async def _rename(self, interaction: discord.Interaction) -> None:
        # Un formulario tiene que ser la primera respuesta: nada de `ack` antes.
        await interaction.response.send_modal(NameModal(self))

    async def _to_confirm(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CONFIRMAR)

    async def _back_to_draft(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, PREVIA)

    async def _open(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        draft = self.draft
        try:
            business, fee = await self.service.open(
                self.guild_id, self.owner.id, sector=draft["sector"], barrio=draft["barrio"],
                name=draft["name"], palette=draft["palette"], emblem=draft["emblem"],
            )  # fmt: skip
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        quote = opening_quote(business.sector_info, business.barrio_info)
        self.notes.append(
            f"🎉 **¡{business.name} abre sus puertas!** Has pagado {format_amount(quote.total)} "
            f"(tasa, acondicionamiento con su IGIC y la fianza)"
            + (f" y {format_amount(fee)} de cuota de autónomo (tarifa plana)." if fee else ".")
        )
        self.notes.append("-# Ahora llena el 📦 almacén: sin stock no hay nada que vender.")
        self.screen = PORTADA
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        barrio = business.barrio_info
        opened = {
            "biz_opened": 1,
            f"biz_opened_{business.sector}": 1,
            f"biz_theme_{barrio.theme}": 1,
            f"biz_island_{barrio.island}": 1,
        }
        await self._track(StatDelta(add=opened))

    # -- Rótulo, precios, almacén y cerrar --------------------------------------------

    async def _save_style(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            await self.service.restyle(
                self.guild_id, self.owner.id, name=self.draft["name"],
                palette=self.draft["palette"], emblem=self.draft["emblem"],
            )  # fmt: skip
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append("🎨 Rótulo nuevo colgado.")
        self.screen = PORTADA
        await self.show(interaction)
        await self._track(StatDelta(add={"biz_restyled": 1}))

    async def _set_price(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        try:
            await self.service.restyle(self.guild_id, self.owner.id, price=value)
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        await self._track(StatDelta(add={f"biz_price_{value}": 1}))

    async def _set_product_price(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        level = None if value == "general" else value
        try:
            await self.service.set_price(self.guild_id, self.owner.id, self.product, level)
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        if level is not None:
            await self._track(StatDelta(add={"biz_price_product": 1}))

    async def _install(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        key = self.equipment
        try:
            business, base, tax, _balance = await self.service.buy_equipment(
                self.guild_id, self.owner.id, key
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        item = EQUIPMENT_BY_KEY[key]
        self.equipment = ""
        self.notes.append(
            f"🛠️ **{item.name}** instalada: {format_amount(base)} + IGIC {format_amount(tax)}. "
            "Se amortiza cada noche."
        )
        await self._pet_note()
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(
            StatDelta(
                add={"biz_equipment": 1, "biz_spent_equipment": base + tax},
                peak={"biz_equipment_max": len(business.equipment)},
            )
        )

    async def _launch(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        key = self.campaign
        try:
            _business, base, tax, _balance = await self.service.start_campaign(
                self.guild_id, self.owner.id, key
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        campaign = CAMPAIGN_BY_KEY[key]
        self.campaign = ""
        self.notes.append(
            f"📣 **{campaign.name}** en marcha {campaign.days} días: "
            f"{format_amount(base)} + IGIC {format_amount(tax)}."
        )
        await self._pet_note()
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(
            StatDelta(
                add={
                    "biz_campaigns": 1,
                    f"biz_campaign_{key}": 1,
                    "biz_spent_marketing": base + tax,
                }
            )
        )

    async def _do_move(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.turn is not None:
            await notify(interaction, "Acaba el turno antes de trasladarte.")
            return
        target = self._move_target()
        if target is None:
            return
        barrio, size, _quote, own = target
        try:
            if own:
                business, quote, _balance = await self.service.move_to_own(
                    self.guild_id, self.owner.id, self.move["own"]
                )
            else:
                business, quote, _balance = await self.service.move(
                    self.guild_id, self.owner.id, barrio, size
                )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"🚚 **{business.name}** ya está en {business.barrio_info.name}, en un "
            f"{size_text(business).lower()}. Has pagado {format_amount(quote.total)}"
            f" y te devuelven {format_amount(quote.refund)} de la fianza."
        )
        self.move = {"barrio": "", "size": 0, "own": ""}
        self.screen = PORTADA
        await self._pet_note()
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        place = business.barrio_info
        await self._track(
            StatDelta(
                add={
                    "biz_moves": 1,
                    "biz_own_local": int(own),
                    f"biz_theme_{place.theme}": 1,
                    f"biz_island_{place.island}": 1,
                },
                peak={"biz_size_max": business.size + 1},
            )
        )

    async def _pick_product(self, interaction: discord.Interaction, value: str) -> None:
        self.product = value
        await self._go(interaction, ALMACEN)

    def _buy(self, lots: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await ack(interaction)
            try:
                _business, base, tax, _balance = await self.service.buy(
                    self.guild_id, self.owner.id, self.product, lots
                )
            except BusinessError as error:
                await notify(interaction, str(error))
                return
            self.notes.append(
                f"📦 Comprado: {format_amount(base)}"
                + (f" + IGIC {format_amount(tax)}" if tax else " (IGIC tipo cero)")
                + "."
            )
            await self.show(interaction)
            await renta.remind(self.cog.bot, interaction)
            await self._track(StatDelta(add={"biz_purchases": 1, "biz_spent_stock": base + tax}))

        return callback

    async def _close(self, interaction: discord.Interaction) -> None:
        await self._shut(interaction, traspaso=False)

    async def _close_traspaso(self, interaction: discord.Interaction) -> None:
        await self._shut(interaction, traspaso=True)

    async def _shut(self, interaction: discord.Interaction, *, traspaso: bool) -> None:
        await ack(interaction)
        if self.turn is not None:
            await notify(interaction, "Acaba el turno antes de cerrar.")
            return
        try:
            closing = await self.service.close(self.guild_id, self.owner.id, traspaso=traspaso)
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        verb = "traspasado" if traspaso else "cerrado"
        got = closing.stock + closing.proceeds
        self.notes.append(
            f"🚪 **{closing.business.name} se ha {verb}.** Cobras {format_amount(got)} por el "
            f"stock{', las mejoras y la clientela' if traspaso else ' y las mejoras'} y te "
            f"devuelven {format_amount(closing.refund)} de la fianza."
        )
        if closing.gain_tax:
            self.notes.append(
                f"-# 🐶 IRPF de la ganancia (19 %): −{format_amount(closing.gain_tax)} "
                f"({TAX_COLLECTOR})"
            )
        self.screen = MONTAR
        await self._pet_note(Mood.GOOD if traspaso else None)
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        week = sum(closing.business.week_totals.values())
        await self._track(
            StatDelta(
                add={
                    "biz_closed": 1,
                    "biz_closed_red": int(week < 0),
                    "biz_traspasos": int(traspaso),
                    "biz_traspaso_money": got if traspaso else 0,
                },
                peak={"biz_traspaso_max": got if traspaso else 0},
            )
        )

    # -- Turno ------------------------------------------------------------------------

    async def _start(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        async with self._lock:
            if self.turn is not None:
                return
            key = (self.guild_id, self.owner.id)
            from bot.cogs import work as curro  # la pala y el negocio no se juegan a la vez

            if key in self.cog.working or curro.busy(self.cog.bot, *key):
                await notify(interaction, "Ya estás currando en otro panel. Una cosa cada vez.")
                return
            try:
                turn = await self.service.start_turn(self.guild_id, self.owner.id, self.plan)
            except BusinessError as error:
                await notify(interaction, str(error))
                return
            self.plan = None
            self.cog.working.add(key)
            self.turn, self.result, self._finishing = turn, None, False
            self.screen = TURNO
            await self.show(interaction, reload=False)
            self._timer = asyncio.create_task(self._patience(turn))
            self.cog.timers.add(self._timer)
            self._timer.add_done_callback(self.cog.timers.discard)

    async def _patience(self, turn: Turn) -> None:
        """Despide a los clientes a los que se les acaba la paciencia aunque nadie pulse."""
        game = turn.game
        while self.turn is turn:
            delay = game.client_deadline + GRACE_SECONDS - self.service.now()
            await asyncio.sleep(max(0.2, delay))
            if self.turn is not turn or self._finishing:
                return
            now = self.service.now()
            if game.finished(now) or game.expire(now):
                if game.finished(now):
                    await self._finish(None)
                    return
                async with self._render_lock:
                    if self.turn is turn:
                        await self.show(None, reload=False)

    async def game_click(
        self, interaction: discord.Interaction, round_index: int, action: str
    ) -> None:
        """Un clic en el minijuego (llega por `Negocio.on_interaction`)."""
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                f"Este negocio es de {self.owner.display_name}. El tuyo, en `pala`.", ephemeral=True
            )
            return
        turn = self.turn
        if turn is None or self._finishing or round_index != turn.game.round:
            await ack(interaction)
            return
        game = turn.game
        before = self._where()
        game.press(action, self.service.now())
        if game.finished(self.service.now()):
            await self._finish(interaction)
            return
        self._ticket += 1
        ticket = self._ticket
        async with self._render_lock:
            if ticket != self._ticket or self.turn is not turn:
                await ack(interaction)
                return
            if self._where() == before:
                # Solo cambia el texto (la bandeja, el cambio): sin subir imagen.
                self.rebuild(has_image=bool(self._shown))
                await self._edit(interaction, view=self)
                return
            state = self.scene_state()
            png = self.cog.painter.ready(state) if state is not None else None
            if png is None:
                await ack(interaction)
            await self.show(interaction, reload=False)

    async def _finish(self, interaction: discord.Interaction | None) -> None:
        """Cobra el turno y enseña el tique (una sola vez por turno)."""
        turn = self.turn
        if self._finishing or turn is None:
            if interaction is not None:
                await ack(interaction)
            return
        self._finishing = True
        if interaction is not None:
            await ack(interaction)
        if self._timer is not None and asyncio.current_task() is not self._timer:
            self._timer.cancel()
        try:
            result = await self.service.finish_turn(self.guild_id, self.owner.id, turn)
        except BusinessError as error:
            self.notes.append(str(error))
            self.turn = None
            self.cog.working.discard((self.guild_id, self.owner.id))
            self.screen = PORTADA
            async with self._render_lock:
                await self.show(interaction)
            return
        finally:
            self.cog.working.discard((self.guild_id, self.owner.id))
        self.turn, self.result = None, result
        self.screen = CIERRE
        grade = result.summary.grade
        feel = Mood.GOOD if grade in ("S", "A") else Mood.BAD if grade == "D" else None
        moment = Moment(PetEvent.BUSINESS, feel=feel)
        hint = await renta.hint(self.cog.bot, self.guild_id, self.owner.id, moment)
        if hint:
            self.notes.append(hint)
        async with self._render_lock:
            await self.show(interaction)
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        await self._track(business_stats(result, tired=turn.tired))

    async def _pet_note(self, feel: Mood | None = None) -> None:
        """Lo que dice la mascota de lo que acaba de pasar en el negocio (y la Renta)."""
        moment = Moment(PetEvent.BUSINESS, feel=feel)
        if hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id, moment):
            self.notes.append(hint)

    async def _track(self, delta: StatDelta) -> None:
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)


def _advanced(game: TurnGame) -> TurnGame:
    """Una copia ligera del juego con el siguiente cliente delante (para pintarlo antes)."""
    ahead = object.__new__(TurnGame)
    for name in ("sector", "levels", "luxury", "stars", "loyalty", "clients", "stock",
                 "started", "deadline", "patience", "tip_bonus", "since", "outcomes", "last",
                 "tray", "given", "accused", "diagnosis", "extra", "round"):  # fmt: skip
        setattr(ahead, name, getattr(game, name))
    ahead.index = game.index + 1
    ahead.step = "diagnostico"
    ahead.paid = game.clients[ahead.index].paid
    ahead.extra = 0
    return ahead


class NameModal(ui.Modal, title="Nombre del negocio"):
    """Formulario para el nombre del negocio (hasta 24 caracteres)."""

    name: ui.TextInput = ui.TextInput(label="Nombre", max_length=24, min_length=1)

    def __init__(self, panel: NegocioPanel) -> None:
        super().__init__()
        self.panel = panel
        self.name.default = panel.draft["name"]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guarda el nombre en el borrador y repinta la vista previa."""
        await ack(interaction)
        from bot.services.business import clean_name

        try:
            self.panel.draft["name"] = clean_name(self.name.value)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        await self.panel.show(interaction)


class Negocio(commands.Cog, name="Negocio"):
    """Negocios propios dentro de `pala`: montar, atender, comprar y cerrar."""

    def __init__(
        self, bot: commands.Bot, service: BusinessService, renderer: BusinessScene | None = None
    ) -> None:
        self.bot = bot
        self.service = service
        self.painter = Painter(renderer or BusinessScene())
        self.panels: set[NegocioPanel] = set()
        #: Miembros con un turno en marcha.
        self.working: set[tuple[int, int]] = set()
        self.timers: set[asyncio.Task[None]] = set()
        self._tokens = random.SystemRandom()
        claim_custom_ids(GAME_PREFIX)

    def new_token(self) -> int:
        """Número de panel para los `custom_id` del minijuego (al azar, ver `pala`)."""
        taken = {panel.token for panel in self.panels}
        while (token := self._tokens.randrange(1, 10**9)) in taken:
            pass
        return token

    async def cog_load(self) -> None:
        self._nightly.start()

    async def cog_unload(self) -> None:
        """Cierra los turnos a medias (se cobran), la tarea de las noches y el pintor."""
        self._nightly.cancel()
        for panel in list(self.panels):
            if panel.turn is not None:
                with contextlib.suppress(Exception):
                    await panel._finish(None)
            panel.stop()
        self.panels.clear()
        for timer in list(self.timers):
            timer.cancel()
        await self.painter.close()

    @tasks.loop(minutes=10)
    async def _nightly(self) -> None:
        for guild in self.bot.guilds:
            try:
                runs = await self.service.close_nights(guild.id)
            except Exception:
                # Un servidor roto no para la tarea: si saliera la excepción, `tasks.loop`
                # se pararía para siempre y nadie pagaría el local.
                logger.exception(
                    "Fallo inesperado cerrando las noches de los negocios en %s", guild.id
                )
                continue
            for run in runs:
                delta = StatDelta(add={"biz_nights": 1, "biz_nights_unpaid": int(run.unpaid > 0)})
                if run.evicted:
                    delta.add["biz_evicted"] = 1
                logros.note(self.bot, guild.id, run.user_id, delta)

    @_nightly.before_loop
    async def _before_nightly(self) -> None:
        await self.bot.wait_until_ready()

    async def open_panel(
        self, interaction: discord.Interaction, owner: discord.abc.User, channel: object
    ) -> None:
        """Pone el panel del negocio en el mensaje del botón (ya aceptado con `ack`)."""
        assert interaction.guild_id is not None
        panel = NegocioPanel(self, guild_id=interaction.guild_id, owner=owner, channel=channel)
        panel.message = interaction.message
        await panel.refresh()
        panel.screen = PORTADA if panel.status is not None else MONTAR
        news = await self.service.take_news(interaction.guild_id, owner.id)
        panel.notes.extend(news)
        self.panels.add(panel)
        await panel.show(interaction, reload=False)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Atiende los botones del minijuego (ver la docstring del módulo)."""
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id")
        if not isinstance(custom_id, str) or (parsed := parse_game_id(custom_id)) is None:
            return
        token, round_index, action = parsed
        try:
            panel = next((p for p in self.panels if p.token == token), None)
            if panel is None:
                await interaction.response.send_message(
                    "Este panel del negocio ya caducó. Ábrelo otra vez desde `pala`.",
                    ephemeral=True,
                )
                return
            await panel.game_click(interaction, round_index, action)
        finally:
            telemetry = self.bot.telemetry
            telemetry.count(
                "button",
                "NegocioPanel.game_click",
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                ms=telemetry.take_answer(interaction.id),
                error=not interaction.response.is_done(),
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los negocios del servidor cuando el bot deja de pertenecer a él."""
        await self.service.repository.delete_guild_data(guild.id)


# -- Funciones puente ---------------------------------------------------------------


async def open_panel(
    bot: commands.Bot, interaction: discord.Interaction, owner: discord.abc.User, channel: object
) -> bool:
    """Abre el panel del negocio en el mensaje del botón. `False` sin el cog."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return False
    try:
        await cog.open_panel(interaction, owner, channel)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo abrir el negocio de %s", owner.id)
        await notify(interaction, "No se pudo abrir tu negocio. Prueba en un rato.")
    return True


async def paint(bot: commands.Bot, state: dict[str, Any]) -> bytes | None:
    """PNG de una pantalla de la escena de los negocios (el inicio de `pala`), o `None`."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return None
    return await cog.painter.paint(state)


async def business_of(bot: commands.Bot, guild_id: int, user_id: int) -> Business | None:
    """El negocio de un miembro con los contadores de hoy al día (para el inicio de `pala`)."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return None
    try:
        return await cog.service.business(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer el negocio de %s", user_id)
        return None


def busy(bot: commands.Bot, guild_id: int, user_id: int) -> bool:
    """Si el miembro está atendiendo su negocio ahora mismo (solo memoria)."""
    cog = find_cog(bot, Negocio)
    return cog is not None and (guild_id, user_id) in cog.working


async def business_line(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """El negocio de alguien en un renglón (para `perfil`), o `None` si no tiene."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return None
    try:
        business = await cog.service.business(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer el negocio de %s", user_id)
        return None
    if business is None:
        return None
    sector = business.sector_info
    return (
        f"{sector.emoji} **{business.name}** · {sector.name} en {business.barrio_info.name} · "
        f"{_stars_text(business.stars)}"
    )


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Lo pendiente del negocio (para `saldo`): el local de esta noche, la deuda y la cuota."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return []
    try:
        tonight, debt, fee = await cog.service.dues(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer lo pendiente del negocio de %s", user_id)
        return []
    items = []
    if tonight:
        items.append(
            Pending(
                Side.PAY, "business_rent", "🔑",
                f"Local de tu negocio esta noche: {format_amount(tonight)} "
                "(alquiler y suministros)",
                tonight, next_daily_at(cog.service.now()),
            )
        )  # fmt: skip
    if debt:
        items.append(
            Pending(
                Side.PAY, "business_debt", "🧾",
                f"Le debes {format_amount(debt)} al casero del local · se cobra cada noche",
                debt,
            )
        )  # fmt: skip
    if fee:
        items.append(
            Pending(
                Side.PAY, "business_fee", "🧾",
                f"Cuota de autónomo de esta semana: {format_amount(fee)} · se cobra esta noche",
                fee,
            )
        )  # fmt: skip
    return items


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el servicio de negocios del bot."""
    await bot.add_cog(Negocio(bot, bot.negocio))
