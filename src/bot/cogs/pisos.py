"""`pisos`: el mercado inmobiliario de Canarias, en un panel que es solo imágenes.

`/pisos` (o `.pisos`) abre en el canal el panel de quien lo pide. Todo se ve en una
imagen (`bot.services.housing_view` decide qué sale y `bot.services.housing_scene`
lo pinta) y se navega con el desplegable de barrios y los botones de cada pantalla:
el mapa de Gran Canaria y Tenerife, cada barrio con sus diez inmuebles (nueve, y la obra
nueva o el solar donde irá, en una rejilla de 5 × 2 con un botón por tarjeta), la ficha de
cada uno, tus pisos, las VPO y la caja de cada compra o venta. Solo lo toca quien lo
abrió; a los demás se les dice que abran el suyo.

Fases 2 a 4: en la caja se elige contado o hipoteca; desde la ficha, el dueño amortiza,
se muda, alquila el piso a otro miembro (que acepta o rechaza en un mensaje aparte),
pide la licencia vacacional o vende al fondo buitre; desde la cartera monta su SOCIMI
o deja su casa, y en la pantalla 🏛️ VPO se apunta al sorteo, compra la que le toca o
la devuelve. Lo que cuesta dinero pide un segundo clic (⚠️ ¿Seguro?).

Dinero (todo por `EconomyService.housing`, impuestos en su docstring): comprar
participaciones (ITP del 6,5 % al Estado, notaría que desaparece), venderlas al
mercado (agencia, deudas del piso e IRPF de la ganancia) y, cada noche, la renta de
cada inmueble alquilado con su IRPF, el IBI y la comunidad. Las fases 2 a 4 (hipoteca,
VPO, bono, licencia, SOCIMI) también pasan por ahí; sus impuestos, en la Biblia. Lo de
cada noche lo cierra una tarea cada 10 minutos (`HousingService.close_days`), que
recupera hasta 7 días si el bot ha estado apagado.

Rapidez: cada clic contesta antes de leer la base de datos o pintar (`ack`), una
imagen que ya se ve no se vuelve a subir, las imágenes se guardan por su contenido
(`Painter`) y, mientras miras una pantalla, se pintan por adelantado las que se abren
desde sus botones (los 20 barrios desde el mapa, los diez inmuebles desde un barrio,
la caja de comprar entero o vender todo desde una ficha, el mapa y la cartera desde
casi todas, la ficha desde la caja…). Van de una en una, la más probable primero, y
cada una comprueba antes de empezar que el panel sigue donde estaba.

Funciones puente para otros cogs: `fiscal_of` (valor fiscal de los pisos de alguien,
para el aviso del Patrimonio de `saldo`). No lanzan y devuelven 0 sin el cog.

Logros: categoría 🏠 Pisos (`housing_*_stats` de `bot.services.achievements`).

Permisos: enviar mensajes y adjuntar archivos en el canal del panel.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.cogs import work as curro
from bot.repositories.economy import BalanceLimitError, InsufficientFundsError
from bot.repositories.housing import (
    DayRun,
    HousingError,
    HousingRepository,
    Market,
    PriceChanged,
    Purchase,
    Sale,
    TenantDeal,
)
from bot.repositories.housing_finance import HOME_OWN, HOME_RENTED, vpo_ref
from bot.services import housing as rules
from bot.services import housing_view as view
from bot.services.achievements import (
    StatDelta,
    housing_action_stats,
    housing_day_stats,
    housing_panel_stats,
    housing_purchase_stats,
    housing_sale_stats,
    housing_tax_stats,
)
from bot.services.economy import EconomyService, format_amount
from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIOS,
    ISLANDS,
    KINDS,
    RENT_LEVELS,
    TENANT_BY_KEY,
    Listing,
    listings_of,
)
from bot.services.housing_market import HousingService
from bot.services.housing_scene import HousingScene
from bot.services.levels import TIMEZONE
from bot.services.pets import Event, Moment
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "pisos"
PNG_NAME = "pisos.png"
#: El panel caduca tras este rato sin tocarlo.
VIEW_TIMEOUT = 15 * 60
#: Imágenes pintadas que se guardan (unos 200-400 KB cada una, ~25 MB en total). Desde el mapa se
#: pintan por adelantado los 20 barrios (más la cartera y las VPO) y desde un barrio sus 10
#: fichas, el mapa y la cartera (13 con el propio barrio): con 80 caben a la vez, y los barrios
#: ya vistos aguantan al ir y volver del mapa, sin que unas echen a otras.
CACHE_SIZE = 80
#: Botones de inmuebles por fila de la pantalla de un barrio (el máximo de Discord).
BARRIO_COLS = 5
#: Inmuebles de tu cartera que salen en su desplegable (el máximo de Discord).
MAX_OPTIONS = 25

MAPA, BARRIO, FICHA, CARTERA, CAJA, VPO = "mapa", "barrio", "ficha", "cartera", "caja", "vpo"
#: La oferta de alquiler a un miembro caduca tras este rato.
OFFER_TIMEOUT = 30 * 60
#: Lo que le pasa a un miembro y no al inmueble: sus logros van solo a él, no a los dueños.
PERSONAL_EVENTS = frozenset(
    {
        "ejecucion",
        "sorteo_vpo",
        "vpo_caducada",
        "partida_agotada",
        "oferta_fondo",
        "fianza_devuelta",
        "fianza_incautada",
    }
)
#: Canal del anuncio del BCE de los lunes (el mismo que el del Patrimonio).
ANNOUNCE_CHANNEL_NAME = "chat-general"


def _pct(value: float) -> str:
    """`0.0225` → `2,25 %`."""
    return f"{value * 100:.2f}".replace(".", ",") + " %"


def effort_text(rent: int, income: int) -> str:
    """«Destinarías el 58 % de tu sueldo al alquiler», con la vara del Banco de España."""
    rate = rules.effort(rent, income)
    if rate is None:
        return (
            "📉 Sin nómina de `pala` en 30 días: la renta te comería el 100 % de nada. El "
            "Banco de España no tiene palabra para eso."
        )
    pct = round(rate * 100)
    if rate > 1:
        return (
            f"📉 La renta se lleva el {pct} % de tu sueldo: trabajas para pagar el piso y "
            "el piso para pagar a tu casero."
        )
    if rate > rules.OVERBURDEN:
        return (
            f"📉 Destinarías el {pct} % de tu sueldo al alquiler. El Banco de España llama "
            f"sobreesfuerzo a pasar del {round(rules.OVERBURDEN * 100)} %."
        )
    return f"📈 Destinarías el {pct} % de tu sueldo al alquiler. Eres un unicornio."


def requirements_text(rent: int, income: int) -> str:
    """Lo que pide el casero: fianza y, sin nómina de 3 veces la renta, garantía extra."""
    deposit = format_amount(rules.deposit_for(rent, guarantee=False))
    if not rules.needs_guarantee(rent, income, aval=False):
        return f"🧾 Fianza de un mes: {deposit}. Tu nómina pasa de 3 veces la renta: ni aval."
    extra = format_amount(rules.deposit_for(rent, guarantee=True))
    return (
        f"🧾 Tu nómina no llega a 3 veces la renta: fianza y garantía adicional de dos "
        f"meses ({extra} en total) salvo que te avalen tus padres (favor de `pala`), y "
        f"entonces solo la fianza ({deposit}). Vuelve cuando te vayas."
    )


def signed_text(deal: TenantDeal) -> str:
    """Lo que dejó en el depósito de fianzas al firmar."""
    if deal.aval:
        return f"👴 Sus padres le avalan: solo deja la fianza ({format_amount(deal.deposit)})."
    if deal.guarantee:
        return f"🧾 Deja fianza y garantía adicional: {format_amount(deal.deposit)}."
    return f"🧾 Deja la fianza: {format_amount(deal.deposit)}."


def promotion_embed(listing: Listing, index: float, builder: str) -> discord.Embed:
    """Anuncio de la obra nueva del lunes: qué se ha levantado, dónde y quién se lo lleva."""
    barrio = BARRIO_BY_KEY[listing.barrio]
    embed = discord.Embed(
        title=f"🏗️ {listing.title.removeprefix('Obra nueva: ')}",
        description=(
            f"**{builder}** levanta en **{barrio.name}** ({barrio.town}) un "
            f"{KINDS[listing.kind].name.lower()} de {listing.m2} m²: "
            f"**{format_amount(rules.price(listing, index))}**.\n*{listing.blurb}*\n\n"
            f"Se compra en `pisos`, en su barrio. De cada participación que se venda, el "
            f"{round(rules.PROMOTER_FEE * 100)} % va al promotor."
        ),
        color=discord.Color.orange(),
    )
    embed.set_footer(text="Obra nueva de la rama Ladrillo de pala: una por barrio, cada lunes.")
    return embed


def ecb_embed(before: float, after: float, week: date) -> discord.Embed:
    """Anuncio de los lunes: lo que hace el Euríbor y la frase del BCE."""
    if after > before:
        move = "sube"
    elif after < before:
        move = "baja"
    else:
        move = "se queda"
    embed = discord.Embed(
        title="🏦 Fráncfort ha hablado",
        description=(
            f"*«{rules.ecb_phrase(before, after, week)}»*\n\n"
            f"El Euríbor {move}: **{_pct(before)} → {_pct(after)}**. Las hipotecas que se "
            f"firmen esta semana van al **{_pct(after + rules.MORTGAGE_SPREAD)} fijo**; las "
            "que ya tienes no cambian."
        ),
        color=discord.Color.dark_blue(),
    )
    embed.set_footer(text="Euríbor simulado: se mueve un cuarto de punto cada lunes, del 0 al 5 %.")
    return embed


def _name(guild: discord.Guild | None, user_id: int) -> str:
    member = guild.get_member(user_id) if guild is not None else None
    return member.display_name if member is not None else "Alguien"


class Painter:
    """Pinta pantallas y las guarda por su contenido, para no pintar dos veces lo mismo.

    Los dibujos por adelantado (`prefetch`) van de uno en uno y por orden (el primero
    es el que más probablemente se pulse): antes de empezar cada uno se mira si ya
    está guardado o se está pintando y si sigue valiendo (el panel no ha cambiado de
    pantalla). Si llegan otros mientras se pinta uno, los anteriores se sueltan: ya no
    se van a ver. Si se pulsa algo que se está pintando por adelantado, se espera a
    ese dibujo en vez de pintarlo otra vez.

    Args:
        scene: Lo que pinta (Node, Chromium o Pillow).
    """

    def __init__(self, scene: HousingScene) -> None:
        self.scene = scene
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self._next: list[tuple[dict[str, Any], Callable[[], bool] | None]] = []
        self._worker: asyncio.Task[None] | None = None
        self._painting: dict[str, asyncio.Future[None]] = {}

    @staticmethod
    def key(state: dict[str, Any]) -> str:
        """Huella de una pantalla: dos pantallas iguales se pintan igual."""
        text = json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha1(text.encode()).hexdigest()

    def _store(self, key: str, png: bytes) -> None:
        self.cache[key] = png
        self.cache.move_to_end(key)
        while len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes]:
        """PNG de cada pantalla, pintando solo las que no estén guardadas ni en marcha."""
        keys = [self.key(s) for s in states]
        while True:
            missing: dict[str, dict[str, Any]] = {}
            waiting: list[asyncio.Future[None]] = []
            for key, state in zip(keys, states, strict=True):
                if key in self.cache:
                    self.cache.move_to_end(key)
                elif key in self._painting:
                    waiting.append(self._painting[key])
                else:
                    missing.setdefault(key, state)
            if not missing:
                if not waiting:
                    return [self.cache[k] for k in keys]
                await asyncio.wait(waiting)  # un dibujo por adelantado: se espera a ese
                continue  # (si falló o se guardó, se mira otra vez)
            marks = {key: asyncio.get_running_loop().create_future() for key in missing}
            self._painting.update(marks)
            try:
                pngs = await self.scene.paint(list(missing.values()))
                for key, png in zip(missing, pngs, strict=True):
                    self._store(key, png)
            finally:
                for key, mark in marks.items():
                    self._painting.pop(key, None)
                    mark.set_result(None)
            if waiting:
                await asyncio.wait(waiting)
            return [self.cache[k] for k in keys]

    def prefetch(
        self, states: list[dict[str, Any]], still: Callable[[], bool] | None = None
    ) -> None:
        """Pinta en segundo plano las pantallas que pueden venir después, de una en una.

        Args:
            states: Las pantallas, de la más a la menos probable.
            still: Si ya no vale (el panel ha cambiado de pantalla), devuelve `False` y
                lo que queda de esta tanda se suelta.
        """
        self._next = [(s, still) for s in states if self.key(s) not in self.cache]
        if self._next and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._work(), name="pisos-adelanto")

    async def _work(self) -> None:
        while self._next:
            state, still = self._next.pop(0)
            if (still is not None and not still()) or self.key(state) in self.cache:
                continue
            try:
                await self.paint([state])
            except Exception:
                logger.exception("No se pudo pintar por adelantado una pantalla de pisos")

    async def close(self) -> None:
        """Para lo pendiente y cierra el pintor."""
        self._next = []
        if self._worker is not None:
            self._worker.cancel()
        await self.scene.close()


class SharesModal(ui.Modal):
    """Cuántas participaciones comprar o vender."""

    def __init__(self, panel: Panel, *, selling: bool, most: int) -> None:
        super().__init__(title="Vender participaciones" if selling else "Comprar participaciones")
        self.panel = panel
        self.selling = selling
        self.most = most
        self.amount: ui.TextInput = ui.TextInput(
            # Discord corta la etiqueta en 45 caracteres: la explicación va en el ejemplo.
            label=f"Participaciones (1 a {most})",
            placeholder="100 participaciones son el inmueble entero",
            default=str(most),
            min_length=1,
            max_length=3,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida el número y enseña la caja."""
        text = self.amount.value.strip()
        if not text.isdigit() or not 1 <= int(text) <= self.most:
            await notify(interaction, f"Pon un número de 1 a {self.most}.")
            return
        await ack(interaction)
        await self.panel.open_checkout(interaction, selling=self.selling, shares=int(text))


class Panel(ui.View):
    """El panel de `pisos` de un miembro: una pantalla a la vez, todas en imagen."""

    def __init__(self, cog: Pisos, *, guild: discord.Guild, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.screen = MAPA
        self.barrio = BARRIOS[0].key
        self.listing = listings_of(self.barrio)[0].key
        #: Caja abierta: `(vende, participaciones)`.
        self.pending: tuple[bool, int] | None = None
        #: Si la compra de la caja va con hipoteca.
        self.financed = False
        #: Botón que pide un segundo clic para hacerse (`amortizar`, `devolver`…).
        self.confirm: str | None = None
        #: Sello de la caja tras pagar (`PAGADO`, `VENDIDO`) y su resultado.
        self.done: tuple[str, Purchase | Sale] | None = None
        self.market = Market()
        self.cash = 0
        self.message: discord.Message | None = None
        self._shown: str | None = None
        self._last: discord.Interaction | None = None

    # -- Pantallas ------------------------------------------------------------------

    def _at(self, key: str) -> Listing:
        """El inmueble de una clave del mercado de este servidor (la obra nueva incluida)."""
        listing = self.market.listing(key)
        if listing is None:
            raise KeyError(key)
        return listing

    def _names(self, market: Market) -> dict[int, str]:
        members = {m for holders in market.holdings.values() for m in holders}
        members |= {owner.owner_id for owner in market.vpo.values()}
        members |= {member for member, _until in market.reservations.values()}
        members |= set(market.homes)
        return {m: _name(self.guild, m) for m in members}

    async def refresh(self) -> None:
        """Lee el mercado y el saldo (una consulta cada uno)."""
        service = self.cog.service
        self.market = await service.market(self.guild.id)
        self.cash = await self.cog.economy.balance(self.guild.id, self.owner.id)

    async def _state(
        self, screen: str, *, barrio: str | None = None, listing: str | None = None
    ) -> dict[str, Any]:
        market, me = self.market, self.owner.id
        names = self._names(market)
        if screen == BARRIO:
            return view.barrio_state(market, barrio or self.barrio, me, names)
        if screen == FICHA:
            key = listing or self.listing
            news = await self.cog.service.news(self.guild.id, (key,), 4)
            return view.listing_state(market, key, me, names, news)
        if screen == CARTERA:
            mine = tuple(h.listing for h in market.of_member(me))
            news = await self.cog.service.news(self.guild.id, mine, 6)
            week = await self.cog.service.week_income(self.guild.id, me)
            effort = None
            home = market.homes.get(me)
            if home is not None and home[0] == HOME_RENTED and home[1] in market.leases:
                income = await self.cog.service.work_income(self.guild.id, me)
                effort = rules.effort(market.leases[home[1]].rent, income)
            return view.portfolio_state(
                market, me, self.owner.display_name, self.cash, week, news, names, effort
            )
        if screen == CAJA:
            return self._checkout_state()
        if screen == VPO:
            return await self._vpo_state()
        return view.map_state(market, me, self.owner.display_name, self.cash)

    async def _vpo_state(self) -> dict[str, Any]:
        market, me = self.market, self.owner.id
        income = await self.cog.service.work_income(self.guild.id, me)
        home = market.homes.get(me)
        reason = ""
        if market.vpo_of(me) is not None:
            reason = "Ya tienes tu VPO"
        elif market.owns_home(me) or (home is not None and home[0] != HOME_RENTED):
            reason = "Ya tienes casa: la VPO es para quien no tiene"
        elif income > rules.vpo_income_cap():
            reason = "Ganas más de 3,5 veces el IPREM: eres clase media"
        today = self.cog.service.today()
        # El sorteo sale al cerrar el lunes (la noche del lunes al martes).
        monday = today + timedelta(days=-today.weekday() % 7)
        return view.vpo_state(
            market, me, self._names(market), eligible=not reason, reason=reason,
            next_draw=f"lunes {monday:%d/%m}", income=income,
        )  # fmt: skip

    def _checkout_state(self, stamp: str | None = None) -> dict[str, Any]:
        listing = self._at(self.listing)
        index = self.market.index(listing.barrio)
        selling, shares = self.pending or (False, 1)
        if self.done is not None:
            stamp, deal = self.done
            index = deal.index_before if isinstance(deal, Purchase) else index
        if selling:
            if self.done is not None and isinstance(self.done[1], Sale):
                quote = self.done[1].quote
            else:
                held = self.market.holdings.get(self.listing, {}).get(self.owner.id)
                lease = self.market.leases.get(self.listing, rules.Lease())
                owned = held.shares if held else shares
                quote = rules.sale_quote(
                    listing,
                    min(shares, owned),
                    owned,
                    held.cost if held else 0,
                    index=index,
                    arrears=lease.arrears,
                    arrears_ibi=lease.arrears_ibi,
                    pool=sum(self.market.shares_of(self.listing).values()),
                )
            lines = [
                (f"Precio ({quote.shares} participaciones)", quote.price, "normal"),
                ("Agencia 3 %", -quote.fee, "normal"),
                ("IGIC de la agencia 7 %", -quote.fee_igic, "tax"),
            ]
            if quote.arrears:
                lines.append(("Deudas del piso (comunidad e IBI)", -quote.arrears, "normal"))
            if quote.gain_tax:
                lines.append(("IRPF de la ganancia 19 %", -quote.gain_tax, "tax"))
            lines.append(("Cobras", quote.received, "total"))
            after = self.cash if self.done else self.cash + quote.received
            before = self.cash - quote.received if self.done else self.cash
            if quote.gain >= 0:
                note = f"Ganas {format_amount(quote.gain)} sobre lo que te costó"
            else:
                note = f"Pierdes {format_amount(-quote.gain)}: el ladrillo nunca baja, dijeron"
            return view.checkout_state(
                listing,
                selling=True,
                lines=lines,
                balance=before,
                after=after,
                note=note,
                stamp=stamp,
            )
        rate = self.market.euribor + rules.MORTGAGE_SPREAD
        if self.done is not None and isinstance(self.done[1], Purchase):
            quote_b, loan, payment = self.done[1].quote, self.done[1].loan, self.done[1].payment
        else:
            quote_b = rules.purchase_quote(listing, shares, index)
            loan = round(quote_b.price * rules.MORTGAGE_LTV) if self.financed else 0
            payment = rules.installment(loan, rate) if loan else 0
        whole = quote_b.shares == rules.SHARES
        lines = [
            (
                "Precio (el inmueble entero)"
                if whole
                else f"Precio ({quote_b.shares} participaciones)",
                quote_b.price,
                "normal",
            ),
            ("ITP 6,5 %", quote_b.itp, "tax"),
            ("Notaría, registro y gestoría", quote_b.notary, "normal"),
            ("IGIC de la notaría 7 %", quote_b.notary_igic, "tax"),
            ("Total", quote_b.total, "normal" if loan else "total"),
        ]
        if loan:
            lines.append(("Hipoteca del banco (80 % del precio)", -loan, "normal"))
            lines.append(("Sale de tu bolsillo", quote_b.total - loan, "total"))
        pocket = quote_b.total - loan
        after = self.cash if self.done else self.cash - pocket
        before = self.cash + pocket if self.done else self.cash
        rise = rules.index_after(index, listing, quote_b.shares, bought=True) / index - 1
        barrio = BARRIO_BY_KEY[listing.barrio].name
        note = f"Comprar sube el precio de {barrio} un {rise * 100:.2f} %".replace(".", ",")
        if loan:
            note = (
                f"Cuota: {format_amount(payment)} al mes durante 30 años (360 días) al "
                f"{rate * 100:.2f} % fijo".replace(".", ",")
                + ". El banco no presta si pasa del 35 % de lo que ganas"
            )
        return view.checkout_state(
            listing,
            selling=False,
            lines=lines,
            balance=before,
            after=after,
            note=note,
            stamp=stamp,
        )

    def _neighbours(self) -> list[tuple[str, dict[str, str]]]:
        """Pantallas que se abren desde la de ahora, de la más a la menos probable.

        Salen con los datos que ya hay en memoria (volver al mapa, la cartera, la ficha
        de un inmueble desde su barrio…) y se pintan en este orden, de una en una.
        """
        me = self.owner.id
        if self.screen == MAPA:
            mine = {self._at(h.listing).barrio for h in self.market.of_member(me)}
            # Los barrios donde ya tienes algo van primero (`sorted` es estable).
            barrios = sorted(BARRIOS, key=lambda b: b.key not in mine)
            return [(CARTERA, {}), (VPO, {})] + [(BARRIO, {"barrio": b.key}) for b in barrios]
        if self.screen == BARRIO:
            fichas = [(FICHA, {"listing": i.key}) for i in self.market.listings_of(self.barrio)]
            return [*fichas, (MAPA, {}), (CARTERA, {})]
        if self.screen == FICHA:
            barrio = self._at(self.listing).barrio
            return [(BARRIO, {"barrio": barrio}), (CARTERA, {}), (MAPA, {})]
        if self.screen == CARTERA:
            mine = self.market.of_member(me)[:6]
            return [(FICHA, {"listing": h.listing}) for h in mine] + [(MAPA, {}), (VPO, {})]
        if self.screen == VPO:
            return [(MAPA, {}), (CARTERA, {})]
        if self.screen == CAJA:  # cancelar o «ver la ficha», y los atajos de siempre
            return [(FICHA, {"listing": self.listing}), (CARTERA, {}), (MAPA, {})]
        return []

    async def _prefetch(self) -> None:
        """Deja pintando en segundo plano lo que se puede pulsar desde esta pantalla."""
        here = self._where()
        checkouts: list[dict[str, Any]] = []
        saved = self.pending, self.financed
        if self.screen == FICHA and self.done is None:
            # La caja de comprar todo lo libre (al contado y con hipoteca) o vender todo
            # lo tuyo: los botones que más se pulsan desde una ficha.
            for selling, shares in self._default_checkouts():
                for financed in (False, True) if not selling else (False,):
                    self.pending, self.financed = (selling, shares), financed
                    checkouts.append(self._checkout_state())
        elif self.screen == CAJA and self.done is None and self.pending and not self.pending[0]:
            # La misma caja con el otro modo de pago (el botón de al lado).
            self.financed = not self.financed
            checkouts.append(self._checkout_state())
        self.pending, self.financed = saved
        states = list(checkouts)
        for screen, where in self._neighbours():
            states.append(await self._state(screen, **where))
        if self._where() == here:  # si ya han cambiado de pantalla, lo de esta ya no vale
            self.cog.painter.prefetch(states, still=lambda: self._where() == here)

    def _where(self) -> tuple[Any, ...]:
        """Dónde está el panel: si cambia, lo que se pintaba por adelantado ya no vale."""
        return (
            self.screen,
            self.barrio,
            self.listing,
            self.pending,
            self.financed,
            self.done is None,
        )

    def _default_checkouts(self) -> list[tuple[bool, int]]:
        out = []
        if free := self.market.free_shares(self.listing):
            out.append((False, free))
        if held := self.market.holdings.get(self.listing, {}).get(self.owner.id):
            out.append((True, held.shares))
        return out

    async def show(
        self,
        interaction: discord.Interaction | None,
        *,
        content: str | None = None,
        send: Callable[..., Awaitable[discord.Message]] | None = None,
    ) -> None:
        """Pinta la pantalla de ahora y la pone en el mensaje (o lo manda, la primera vez)."""
        await self.refresh()
        state = self._checkout_state() if self.screen == CAJA else await self._state(self.screen)
        key = Painter.key(state)
        self.rebuild()
        if send is not None:
            png = (await self.cog.painter.paint([state]))[0]
            self.message = await send(
                content=content, file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=self
            )
        elif interaction is not None and key == self._shown:
            # Ya se ve esta imagen: subirla otra vez cuesta ~1 s y no cambia nada.
            self._last = interaction
            await edit(interaction, content=content, view=self)
        elif interaction is not None:
            png = (await self.cog.painter.paint([state]))[0]
            self._last = interaction
            await edit(
                interaction,
                content=content,
                embed=None,
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
        self._shown = key
        await self._prefetch()

    # -- Componentes ----------------------------------------------------------------

    def rebuild(self) -> None:
        """Monta el desplegable y los botones de la pantalla de ahora."""
        self.clear_items()
        if self.screen == CAJA:
            self._caja_buttons()
            return
        select: ui.Select = ui.Select(
            placeholder="Elige un barrio",
            custom_id=f"{GAME}:barrio",
            row=0,
            options=[
                discord.SelectOption(
                    label=b.name,
                    value=b.key,
                    description=f"{ISLANDS[b.island]} · {b.town}"[:100],
                    emoji="🏝️" if b.island == "gc" else "🌋",
                    default=self.screen in (BARRIO, FICHA) and b.key == self.barrio,
                )
                for b in BARRIOS
            ],
        )
        select.callback = self._pick_barrio  # type: ignore[method-assign]
        self.add_item(select)
        if self.screen == BARRIO:
            # Una rejilla de 5 × 2 que cuadra con las tarjetas de la imagen: el botón N abre la
            # tarjeta N (el precio ya sale en la imagen, aquí solo el número y el tipo).
            items = self.market.listings_of(self.barrio)
            for slot, item in enumerate(items):
                new = "🏗️ " if item.key in self.market.promotions else ""
                self._button(
                    f"{new}{slot + 1} · {KINDS[item.kind].name}",
                    f"ver:{slot}",
                    self._open_listing,
                    row=1 + slot // BARRIO_COLS,
                    style=discord.ButtonStyle.primary,
                )
            if len(items) < view.BARRIO_SLOTS:  # el solar de la imagen: sin nada que abrir
                slot = len(items)
                self._button(
                    f"🚧 {slot + 1} · Solar",
                    f"ver:{slot}",
                    self._open_listing,
                    row=1 + slot // BARRIO_COLS,
                    disabled=True,
                )
            self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
            self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=3)
        elif self.screen == FICHA:
            self._ficha_buttons()
        elif self.screen == CARTERA:
            mine = self.market.of_member(self.owner.id)[:MAX_OPTIONS]
            if mine:
                pick: ui.Select = ui.Select(
                    placeholder="Abre uno de tus pisos",
                    custom_id=f"{GAME}:mio",
                    row=1,
                    options=[
                        discord.SelectOption(
                            label=self._at(h.listing).title[:100],
                            value=h.listing,
                            description=f"{BARRIO_BY_KEY[self._at(h.listing).barrio].name}"
                            f" · {h.shares} %",
                        )
                        for h in mine
                    ],
                )
                pick.callback = self._pick_mine  # type: ignore[method-assign]
                self.add_item(pick)
            self._button("🗺️ Mapa", "mapa", self._go_map, row=2)
            self._button("🏛️ VPO", "vpo", self._go_vpo, row=2)
            self._portfolio_buttons()
        elif self.screen == VPO:
            self._vpo_buttons()
        else:
            self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=1)
            self._button("🏛️ VPO", "vpo", self._go_vpo, row=1)

    def _confirmable(
        self,
        label: str,
        action: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        row: int,
    ) -> None:
        """Un botón que cuesta dinero: el primer clic pide confirmar y el segundo lo hace."""
        if self.confirm == action:
            self._button(
                f"⚠️ ¿Seguro? {label}", f"{action}:si", callback, row=row,
                style=discord.ButtonStyle.danger,
            )  # fmt: skip
        else:
            self._button(label, action, self._arm, row=row)

    def _portfolio_buttons(self) -> None:
        me, market = self.owner.id, self.market
        if me not in market.socimi and market.whole_homes(me) >= rules.SOCIMI_HOMES:
            fee = format_amount(rules.SOCIMI_FEE)
            self._confirmable(f"📈 Montar SOCIMI ({fee})", "socimi", self._found_socimi, row=3)
        home = market.homes.get(me)
        if home is not None and home[0] in (HOME_OWN, HOME_RENTED):
            self._confirmable("🚪 Dejar mi casa", "dejar", self._leave_home, row=3)

    def _vpo_buttons(self) -> None:
        me, market = self.owner.id, self.market
        unit = market.vpo_of(me)
        if unit is not None:
            if market.mortgages_of(me, vpo_ref(unit)):
                self._confirmable("🏦 Amortizar", "amortizar", self._amortize, row=1)
            self._confirmable("↩️ Devolverla al Estado", "devolver", self._vpo_return, row=1)
        elif market.reservation_of(me) is not None:
            self._confirmable(
                "🔑 Comprar mi VPO", "comprar_vpo", self._vpo_buy, row=1
            )  # fmt: skip
        elif me in market.signups:
            self._button("✖️ Borrarme del sorteo", "sorteo", self._vpo_signup, row=1)
        else:
            self._button(
                "✍️ Apuntarme al sorteo", "sorteo", self._vpo_signup, row=1,
                style=discord.ButtonStyle.success,
            )  # fmt: skip
        self._button("🗺️ Mapa", "mapa", self._go_map, row=2)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=2)

    def _owner_buttons(self) -> None:
        """Lo que puede hacer el dueño con su inmueble (fila de abajo de la ficha)."""
        key, me, market = self.listing, self.owner.id, self.market
        listing = self._at(key)
        holders = market.shares_of(key)
        lease = market.leases.get(key)
        if not holders.get(me) or lease is None:
            return
        if self.confirm == "miembro":
            pick: ui.UserSelect = ui.UserSelect(
                placeholder="¿A qué miembro se lo alquilas?", custom_id=f"{GAME}:inquilino", row=4
            )
            pick.callback = self._pick_tenant  # type: ignore[method-assign]
            self.add_item(pick)
            return
        is_home = listing.kind in rules.HOME_KINDS
        boss = rules.manager(holders) == me
        if market.mortgages_of(me, key):
            self._confirmable("🏦 Amortizar", "amortizar", self._amortize, row=4)
        if market.homes.get(me) == (HOME_OWN, key):
            self._button("🚪 Mudarme", "mudarme", self._move_out, row=4)
        elif (
            is_home and holders[me] == rules.SHARES and lease.status == rules.VACANT
            and me not in market.homes
        ):  # fmt: skip
            self._button("🏠 Vivir aquí", "vivir", self._set_home, row=4)
        if boss and is_home and lease.status == rules.VACANT:
            self._button("👤 Alquilar a un miembro", "miembro", self._arm, row=4)
        if (
            boss and is_home and not lease.license and lease.status != rules.HABITUAL
            and lease.member is None
        ):  # fmt: skip
            fee = format_amount(rules.LICENSE_FEE)
            self._confirmable(f"🏖️ Licencia ({fee})", "licencia", self._license, row=4)
        if (key, me) in market.offers:
            self._confirmable("🦅 Vender al fondo", "fondo", self._vulture, row=4)

    def _ficha_buttons(self) -> None:
        key = self.listing
        holders = self.market.shares_of(key)
        mine = holders.get(self.owner.id, 0)
        free = self.market.free_shares(key)
        lease = self.market.leases.get(key)
        if free:
            label = "🛒 Comprar entero" if free == rules.SHARES else f"🛒 Comprar (quedan {free})"
            self._button(label, "comprar", self._buy, row=1, style=discord.ButtonStyle.success)
        if mine:
            self._button("💰 Vender", "vender", self._sell, row=1, style=discord.ButtonStyle.danger)
        boss = rules.manager(holders)
        can_decide = mine and (boss is None or boss == self.owner.id)
        if can_decide and lease is not None and lease.status in (rules.DEFAULTING, rules.SQUATTED):
            self._button("⚖️ Desahuciar", "desahuciar", self._evict, row=1)
        if boss == self.owner.id and lease is not None:
            listing = self._at(key)
            index = self.market.index(listing.barrio)
            levels: ui.Select = ui.Select(
                placeholder="Renta que pides",
                custom_id=f"{GAME}:renta",
                row=2,
                options=[
                    discord.SelectOption(
                        label=(
                            f"{level.name} · "
                            f"{format_amount(rules.asking_rent(listing, level, index))}"
                        ),
                        value=level.key,
                        emoji=level.emoji,
                        description="Vale para el próximo contrato (art. 18 LAU)",
                        default=level.key == lease.level,
                    )
                    for level in RENT_LEVELS.values()
                ],
            )
            levels.callback = self._pick_level  # type: ignore[method-assign]
            self.add_item(levels)
        barrio = BARRIO_BY_KEY[self._at(key).barrio].name
        self._button(f"◀ {barrio}"[:80], "volver", self._go_barrio, row=3)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=3)
        self._owner_buttons()

    def _caja_buttons(self) -> None:
        if self.done is not None:
            self._button(
                "🏠 Ver la ficha",
                "ficha",
                self._back_to_listing,
                row=0,
                style=discord.ButtonStyle.primary,
            )
            self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=0)
            self._button("🗺️ Mapa", "mapa", self._go_map, row=0)
            return
        selling = self.pending[0] if self.pending else False
        self._button(
            "🤝 Vender" if selling else "💳 Pagar",
            "pagar",
            self._pay,
            row=0,
            style=discord.ButtonStyle.success,
        )
        if not selling:
            self._button(
                "💵 Al contado" if self.financed else "🏦 Con hipoteca",
                "financiar",
                self._toggle_mortgage,
                row=0,
            )
        self._button("✖️ Cancelar", "cancelar", self._back_to_listing, row=0)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        row: int,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label[:80],
            style=style,
            custom_id=f"{GAME}:{custom_id}",
            row=row,
            disabled=disabled,
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño mueve su panel."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Este panel es de {self.owner.display_name}. Abre el tuyo con `/pisos`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: quita los botones (la imagen se queda)."""
        self.cog.panels.discard(self)
        try:
            if self._last is not None:
                await self._last.edit_original_response(view=None)
            elif self.message is not None:
                await self.message.edit(view=None)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el panel de pisos", exc_info=True)

    # -- Navegación -----------------------------------------------------------------

    async def _go(self, interaction: discord.Interaction, screen: str) -> None:
        await ack(interaction)
        self.screen, self.pending, self.done = screen, None, None
        self.financed, self.confirm = False, None
        await self.show(interaction)
        logros.note(
            self.cog.bot,
            self.guild.id,
            self.owner.id,
            housing_panel_stats(screen=screen),
            getattr(interaction.channel, "id", None),
        )

    async def _go_map(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, MAPA)

    async def _go_portfolio(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CARTERA)

    async def _go_vpo(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, VPO)

    async def _arm(self, interaction: discord.Interaction) -> None:
        """Primer clic de un botón que pide confirmar: solo cambia los botones (memoria)."""
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        self.confirm = custom_id.removeprefix(f"{GAME}:")
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def _go_barrio(self, interaction: discord.Interaction) -> None:
        self.barrio = self._at(self.listing).barrio
        await self._go(interaction, BARRIO)

    async def _back_to_listing(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, FICHA)

    async def _pick_barrio(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if not values or values[0] not in BARRIO_BY_KEY:
            await ack(interaction)
            return
        self.barrio = values[0]
        await self._go(interaction, BARRIO)

    async def _open_listing(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        slot = custom_id.rsplit(":", 1)[-1]
        items = self.market.listings_of(self.barrio)
        if not slot.isdigit() or int(slot) >= len(items):
            await ack(interaction)
            return
        self.listing = items[int(slot)].key
        await self._go(interaction, FICHA)

    async def _pick_mine(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if not values or self.market.listing(values[0]) is None:
            await ack(interaction)
            return
        self.listing = values[0]
        self.barrio = self._at(self.listing).barrio
        await self._go(interaction, FICHA)

    # -- Dinero ---------------------------------------------------------------------

    async def _buy(self, interaction: discord.Interaction) -> None:
        """Abre el formulario (o la caja, si solo cabe comprarlo entero). Solo memoria."""
        free = self.market.free_shares(self.listing)
        if free <= 0:
            await notify(interaction, "Ya no queda nada libre de este inmueble.")
            return
        await interaction.response.send_modal(SharesModal(self, selling=False, most=free))

    async def _sell(self, interaction: discord.Interaction) -> None:
        held = self.market.holdings.get(self.listing, {}).get(self.owner.id)
        if held is None:
            await notify(interaction, "No tienes nada en este inmueble.")
            return
        await interaction.response.send_modal(SharesModal(self, selling=True, most=held.shares))

    async def open_checkout(
        self, interaction: discord.Interaction, *, selling: bool, shares: int
    ) -> None:
        """Enseña la caja con las cuentas de hoy (aún no se mueve dinero)."""
        self.screen, self.pending, self.done = CAJA, (selling, shares), None
        self.financed = False
        await self.show(interaction)

    async def _toggle_mortgage(self, interaction: discord.Interaction) -> None:
        """Cambia la caja entre contado e hipoteca (la otra ya está pintada)."""
        await ack(interaction)
        if self.pending is None or self.done is not None:
            return
        self.financed = not self.financed
        await self.show(interaction)

    def _expected(self) -> int:
        """Lo que dice la caja que se ve ahora: el total de la compra o lo que se cobra."""
        listing = self._at(self.listing)
        index = self.market.index(listing.barrio)
        selling, shares = self.pending or (False, 1)
        if not selling:
            quote = rules.purchase_quote(listing, shares, index)
            loan = round(quote.price * rules.MORTGAGE_LTV) if self.financed else 0
            return quote.total - loan
        held = self.market.holdings.get(self.listing, {}).get(self.owner.id)
        lease = self.market.leases.get(self.listing, rules.Lease())
        owned = held.shares if held else shares
        return rules.sale_quote(
            listing, min(shares, owned), owned, held.cost if held else 0, index=index,
            arrears=lease.arrears, arrears_ibi=lease.arrears_ibi,
            pool=sum(self.market.shares_of(self.listing).values()),
        ).received  # fmt: skip

    async def _pay(self, interaction: discord.Interaction) -> None:
        # Un segundo clic en Pagar (o uno que llega tarde) no vuelve a cobrar.
        if self.pending is None or self.done is not None:
            await ack(interaction)
            return
        await ack(interaction)
        slot = (self.guild.id, self.owner.id)
        if slot in self.cog.working:
            await notify(interaction, "Espera a que termine la operación de antes.")
            return
        self.cog.working.add(slot)
        selling, shares = self.pending
        try:
            expected = self._expected()
            if selling:
                trade = await self.cog.service.sell(
                    self.guild.id, self.owner.id, self.listing, shares, expected=expected
                )
            else:
                aval = self.financed and await curro.parents_aval(
                    self.cog.bot, self.guild.id, self.owner.id
                )
                trade = await self.cog.service.buy(
                    self.guild.id, self.owner.id, self.listing, shares, expected=expected,
                    financed=self.financed, aval=aval,
                )  # fmt: skip
        except BalanceLimitError:
            await notify(interaction, "No te cabe más dinero en el monedero. Ni a Amancio.")
            return
        except InsufficientFundsError as error:
            tip = (
                ""
                if selling or self.financed
                else " Prueba 🏦 Con hipoteca: el banco pone el 80 %."
            )
            await notify(interaction, f"No te llega: tienes {format_amount(error.balance)}.{tip}")
            return
        except PriceChanged as error:
            self.cog.working.discard(slot)
            await self.show(interaction)
            await notify(
                interaction,
                "El precio ha cambiado mientras mirabas (alguien ha comprado o vendido en el "
                f"barrio): ahora son {format_amount(error.amount)}. No se ha cobrado nada; "
                "mira la caja y vuelve a pulsar.",
            )
            return
        except HousingError as error:
            await notify(interaction, str(error))
            return
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("No se pudo cerrar una operación de pisos")
            await notify(interaction, "Se ha caído el notario. Prueba otra vez en un momento.")
            return
        finally:
            self.cog.working.discard(slot)
        self.done = ("VENDIDO" if selling else "PAGADO", trade.deal)
        moment = Moment(Event.TRADE if selling else Event.BUY)
        hint = await renta.hint(self.cog.bot, self.guild.id, self.owner.id, moment)
        await self.show(interaction, content=hint)
        if isinstance(trade.deal, Purchase) and trade.deal.aval:
            await curro.spend_parents_aval(self.cog.bot, self.guild.id, self.owner.id)
            await notify(
                interaction,
                "👴 El banco solo firma con el aval de tus padres: el piso del pueblo queda de "
                "garantía. Si dejas de pagar, van a por ellos (y tu familia de `pala` lo notará).",
            )
        await renta.remind(self.cog.bot, interaction)
        await self.cog.track_trade(
            self.guild.id, self.owner, interaction.channel, trade.deal, trade.balance
        )
        deal = trade.deal
        if isinstance(deal, Purchase) and deal.promoter is not None and deal.promoter_fee:
            logros.note(
                self.cog.bot,
                self.guild.id,
                deal.promoter,
                StatDelta(add={"pisos_promoter_fees": deal.promoter_fee}),
            )

    async def _money(
        self,
        interaction: discord.Interaction,
        operation: Callable[[], Awaitable[Any]],
        *,
        broke: str | None = None,
    ) -> Any | None:
        """Hace una operación con dinero, una a la vez, con los avisos de siempre.

        Args:
            broke: Aviso si falta dinero (por defecto, que no le llega).

        Returns:
            Lo que devuelve la operación, o `None` si no se ha hecho (ya avisado).
        """
        await ack(interaction)
        self.confirm = None
        slot = (self.guild.id, self.owner.id)
        if slot in self.cog.working:
            await notify(interaction, "Espera a que termine la operación de antes.")
            return None
        self.cog.working.add(slot)
        try:
            return await operation()
        except BalanceLimitError:
            text = "No te cabe más dinero en el monedero. Ni a Amancio."
        except InsufficientFundsError as error:
            text = broke or f"No te llega: tienes {format_amount(error.balance)}."
        except HousingError as error:
            text = str(error)
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("No se pudo cerrar una operación de pisos")
            text = "Se ha caído el notario. Prueba otra vez en un momento."
        finally:
            self.cog.working.discard(slot)
        await notify(interaction, text)
        await self.show(interaction)
        return None

    async def _done(
        self, interaction: discord.Interaction, event: Event, delta: StatDelta | None
    ) -> None:
        """Tras una operación con dinero: la pantalla nueva, la mascota, la Renta y los logros."""
        hint = await renta.hint(self.cog.bot, self.guild.id, self.owner.id, Moment(event))
        await self.show(interaction, content=hint)
        await renta.remind(self.cog.bot, interaction)
        if delta is not None:
            await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel, delta)

    async def _amortize(self, interaction: discord.Interaction) -> None:
        me = self.owner.id
        unit = self.market.vpo_of(me)
        ref = vpo_ref(unit) if self.screen == VPO and unit is not None else self.listing
        mortgages = self.market.mortgages_of(me, ref)

        async def run() -> int:
            total = 0
            for mortgage in mortgages:
                paid, _balance = await self.cog.service.amortize(self.guild.id, me, mortgage.id)
                total += paid
            return total

        if await self._money(interaction, run) is None:
            return
        await self._done(interaction, Event.TRADE, housing_action_stats("amortiza"))

    async def _license(self, interaction: discord.Interaction) -> None:
        async def run() -> tuple[int, int]:
            return await self.cog.service.buy_license(self.guild.id, self.owner.id, self.listing)

        if await self._money(interaction, run) is None:
            return
        await self._done(interaction, Event.BUY, housing_action_stats("licencia"))

    async def _vulture(self, interaction: discord.Interaction) -> None:
        async def run() -> Any:
            return await self.cog.service.accept_offer(self.guild.id, self.owner.id, self.listing)

        trade = await self._money(interaction, run)
        if trade is None:
            return
        await self._done(interaction, Event.TRADE, None)
        await self.cog.track_trade(
            self.guild.id, self.owner, interaction.channel, trade.deal, trade.balance
        )

    async def _found_socimi(self, interaction: discord.Interaction) -> None:
        async def run() -> tuple[int, int]:
            return await self.cog.service.found_socimi(self.guild.id, self.owner.id)

        if await self._money(interaction, run) is None:
            return
        await self._done(interaction, Event.TRADE, housing_action_stats("socimi"))

    async def _vpo_buy(self, interaction: discord.Interaction) -> None:
        unit = self.market.reservation_of(self.owner.id)

        async def run() -> Any:
            if unit is None:
                raise HousingError("Ya no tienes ninguna VPO reservada.")
            return await self.cog.service.vpo_buy(self.guild.id, self.owner.id, unit)

        if await self._money(interaction, run) is None:
            return
        await self._done(interaction, Event.BUY, housing_action_stats("compra_vpo"))

    async def _vpo_return(self, interaction: discord.Interaction) -> None:
        async def run() -> Any:
            return await self.cog.service.vpo_return(self.guild.id, self.owner.id)

        broke = "El Estado no tiene partida para recomprarla. Vuelve cuando recaude."
        if await self._money(interaction, run, broke=broke) is None:
            return
        await self._done(interaction, Event.TRADE, housing_action_stats("devuelve_vpo"))

    # -- Sin dinero -----------------------------------------------------------------

    async def _vpo_signup(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        join = self.owner.id not in self.market.signups
        try:
            await self.cog.service.vpo_signup(self.guild.id, self.owner.id, join=join)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        if join:
            await logros.track(
                self.cog.bot, self.guild.id, self.owner, interaction.channel,
                housing_action_stats("vpo_signup"),
            )  # fmt: skip

    async def _set_home(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            await self.cog.service.set_home(self.guild.id, self.owner.id, self.listing)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        await logros.track(
            self.cog.bot, self.guild.id, self.owner, interaction.channel,
            housing_action_stats("se_muda"),
        )  # fmt: skip

    async def _move_out(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        self.confirm = None
        try:
            await self.cog.service.leave_home(self.guild.id, self.owner.id)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)

    async def _leave_home(self, interaction: discord.Interaction) -> None:
        await self._move_out(interaction)

    async def _pick_tenant(self, interaction: discord.Interaction) -> None:
        """Ofrece el piso a un miembro: él acepta o rechaza en un mensaje aparte."""
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        self.confirm = None
        member = None
        if values and str(values[0]).isdigit():
            member = self.guild.get_member(int(values[0]))
            if member is None:
                try:
                    member = await self.guild.fetch_member(int(values[0]))
                except discord.HTTPException:
                    member = None
        await self.show(interaction)
        market, key = self.market, self.listing
        if member is None:
            await notify(interaction, "No encuentro a ese miembro.")
            return
        if member.bot:
            await notify(interaction, "Los bots no alquilan: viven en la nube.")
            return
        if member.id in market.shares_of(key):
            await notify(interaction, "No puedes alquilarle el piso a uno de sus dueños.")
            return
        if member.id in market.homes:
            await notify(interaction, f"{member.display_name} ya vive en otro sitio.")
            return
        listing = self._at(key)
        lease = market.leases.get(key, rules.Lease())
        level = RENT_LEVELS["referencia" if lease.level == rules.VACATION_LEVEL else lease.level]
        rent = rules.asking_rent(listing, level, market.index(listing.barrio))
        income = await self.cog.service.work_income(self.guild.id, member.id)
        offer = TenantOffer(self.cog, self.guild, self.owner, member, listing, rent, income)
        offer.message = await interaction.followup.send(
            content=member.mention,
            embed=offer.embed(),
            view=offer,
            wait=True,
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )

    async def _pick_level(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        if not values or values[0] not in RENT_LEVELS:
            return
        try:
            await self.cog.service.set_level(self.guild.id, self.owner.id, self.listing, values[0])
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        logros.note(
            self.cog.bot,
            self.guild.id,
            self.owner.id,
            housing_panel_stats(screen="renta", level=values[0]),
            getattr(interaction.channel, "id", None),
        )

    async def _evict(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            lease = await self.cog.service.evict(self.guild.id, self.owner.id, self.listing)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        await logros.track(
            self.cog.bot,
            self.guild.id,
            self.owner,
            interaction.channel,
            housing_panel_stats(screen="desahucio", squatters=lease.unpaid == 0),
        )


class TenantOffer(ui.View):
    """Oferta pública de alquiler de un piso a otro miembro: él acepta o rechaza.

    Al aceptar firma 5 años (60 días) con la renta que pide el piso ese día, deja la
    fianza (y la garantía adicional si su nómina no llega a 3 veces la renta y no le
    avalan sus padres) y la paga cada noche de su monedero. Si gana poco, cobra el Bono
    Alquiler Joven. La oferta enseña cuánto de su sueldo se le iría en la renta.
    """

    def __init__(
        self,
        cog: Pisos,
        guild: discord.Guild,
        owner: discord.abc.User,
        tenant: discord.abc.User,
        listing: Listing,
        rent: int,
        income: int = 0,
    ) -> None:
        super().__init__(timeout=OFFER_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.tenant = tenant
        self.listing = listing
        self.key = listing.key
        self.rent = rent
        #: Neto medio de nómina al día de quien alquila (para la tasa de esfuerzo).
        self.income = income
        self.message: discord.Message | None = None
        #: Hay una firma en marcha: un segundo clic no firma dos veces.
        self.busy = False

    def embed(self, text: str | None = None) -> discord.Embed:
        listing = self.listing
        barrio = BARRIO_BY_KEY[listing.barrio].name
        return discord.Embed(
            title="🔑 Se alquila (a un colega)",
            description=text
            or (
                f"**{self.owner.display_name}** le ofrece a **{self.tenant.display_name}** "
                f"«{listing.title}» ({barrio}) por unos **{format_amount(self.rent)} al mes**, "
                "5 años (60 días del bot). La renta se paga cada noche de tu monedero; si "
                "ganas poco, el Estado te pone el Bono Alquiler Joven. Vivir en casa propia "
                "(aunque sea alquilada) recarga más rápido la batería de `pala`.\n\n"
                + effort_text(self.rent, self.income)
                + "\n"
                + requirements_text(self.rent, self.income)
            ),
            color=discord.Color.teal(),
        )

    async def _close(self, interaction: discord.Interaction, text: str) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=None, embed=self.embed(text), view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )  # fmt: skip

    @ui.button(
        label="Aceptar", emoji="🤝", style=discord.ButtonStyle.success,
        custom_id="pisos:alquiler:si",
    )  # fmt: skip
    async def accept(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.tenant.id:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)
            return
        if self.busy:
            await interaction.response.send_message("Un momento, que lo está firmando.",
                                                    ephemeral=True)  # fmt: skip
            return
        self.busy = True
        await ack(interaction)
        try:
            aval = await curro.parents_aval(self.cog.bot, self.guild.id, self.tenant.id)
            deal = await self.cog.service.rent_to_member(
                self.guild.id, self.owner.id, self.key, self.tenant.id, aval=aval
            )
        except HousingError as error:
            await notify(interaction, str(error))
            return
        except InsufficientFundsError as error:
            await notify(
                interaction,
                f"No te llega para la fianza: tienes {format_amount(error.balance)}. Sin "
                "fianza no hay llaves, ni en Canarias.",
            )
            return
        except BalanceLimitError:
            await notify(interaction, "Se ha atascado el depósito de fianzas. Prueba otra vez.")
            return
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo firmar un alquiler entre miembros")
            await notify(interaction, "Se ha caído el notario. Prueba otra vez en un momento.")
            return
        finally:
            self.busy = False
        self.stop()
        lease = deal.lease
        bono = " Cobra el Bono Alquiler Joven." if lease.bono else ""
        title = self.listing.title
        await edit(
            interaction, content=None, view=None,
            embed=self.embed(
                f"🤝 **{self.tenant.display_name}** firma con **{self.owner.display_name}**: "
                f"«{title}» por {format_amount(lease.rent)} al mes.{bono} "
                + signed_text(deal)
                + " Bienvenido a la generación inquilina."
            ),
        )  # fmt: skip
        if deal.aval:
            await curro.spend_parents_aval(self.cog.bot, self.guild.id, self.tenant.id)
        rate = rules.effort(lease.rent, self.income)
        await logros.track(
            self.cog.bot, self.guild.id, self.tenant, interaction.channel,
            housing_action_stats(
                "inquilino_miembro", effort=rate, guarantee=deal.guarantee, aval=deal.aval
            ),
        )  # fmt: skip
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel,
                           housing_action_stats("casero_miembro"))  # fmt: skip

    @ui.button(
        label="Rechazar", emoji="✖️", style=discord.ButtonStyle.secondary,
        custom_id="pisos:alquiler:no",
    )  # fmt: skip
    async def reject(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.tenant.id:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)
            return
        await self._close(
            interaction,
            f"✖️ {self.tenant.display_name} prefiere seguir en casa de sus padres. Normal.",
        )

    @ui.button(
        label="Retirar", emoji="↩️", style=discord.ButtonStyle.secondary,
        custom_id="pisos:alquiler:retirar",
    )  # fmt: skip
    async def withdraw(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("Esa oferta no es tuya.", ephemeral=True)
            return
        await self._close(interaction, f"↩️ {self.owner.display_name} se lo ha pensado mejor.")

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(
                content=None, embed=self.embed("⌛ La oferta de alquiler ha caducado."),
                view=None, allowed_mentions=discord.AllowedMentions.none(),
            )  # fmt: skip
        except discord.HTTPException:
            pass


class Pisos(commands.Cog):
    """El mercado inmobiliario: el panel, la tarea de cada noche y los puentes."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: HousingRepository,
        service: HousingService | None = None,
        renderer: HousingScene | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.service = service or HousingService(repository, economy)
        self.painter = Painter(renderer or HousingScene())
        self.panels: set[Panel] = set()
        #: `(servidor, miembro)` con una compra o venta en marcha: una a la vez.
        self.working: set[tuple[int, int]] = set()

    async def cog_load(self) -> None:
        self._nightly.start()

    async def cog_unload(self) -> None:
        self._nightly.cancel()
        for panel in list(self.panels):
            panel.stop()
        self.panels.clear()
        await self.painter.close()

    # -- Cada noche -----------------------------------------------------------------

    @tasks.loop(minutes=10)
    async def _nightly(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self.close_guild(guild.id)
            except Exception:
                # Un servidor roto no para la tarea: si saliera la excepción, `tasks.loop`
                # se pararía para siempre y nadie cobraría más alquileres.
                logger.exception("Fallo inesperado cerrando los días de pisos en %s", guild.id)

    @_nightly.before_loop
    async def _before_nightly(self) -> None:
        await self.bot.wait_until_ready()

    async def close_guild(self, guild_id: int) -> list[DayRun]:
        """Cierra los días que falten en un servidor y apunta sus logros. Nunca lanza."""
        try:
            runs = await self.service.close_days(
                guild_id, lambda: curro.ladrillo_builders(self.bot, guild_id)
            )
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron cerrar los días de pisos en %s", guild_id)
            return []
        if runs:
            for member, delta in (await self.tax_stats(guild_id)).items():
                logros.note(self.bot, guild_id, member, delta)
        for run in runs:
            if run.euribor is not None:
                await self._announce_ecb(guild_id, run.day, *run.euribor)
            for builder, key in run.promotions:
                await self._announce_promotion(guild_id, builder, key)
                logros.note(self.bot, guild_id, builder, StatDelta(add={"pisos_promotions": 1}))
            events: dict[int, list[str]] = {}
            for item in run.news:
                if item.kind in PERSONAL_EVENTS:
                    who: tuple[int, ...] = (item.user_id,) if item.user_id is not None else ()
                else:
                    who = run.owners.get(item.listing, ())
                tenant = TENANT_BY_KEY.get(item.tenant or "")
                business = item.kind == "firma" and tenant is not None and bool(tenant.kinds)
                for member in who:
                    events.setdefault(member, []).append(item.kind)
                    if business:
                        events[member].append("firma_negocio")
            for member in set(run.mortgage_unpaid):
                events.setdefault(member, []).append("cuota_impagada")
            for member in run.aval_executions:
                await curro.parents_lose_home(self.bot, guild_id, member)
                events.setdefault(member, []).append("ejecucion_aval")
            for member in set(run.members) | set(events) | set(run.bono) | set(run.installments):
                day = run.members.get(member)
                logros.note(
                    self.bot,
                    guild_id,
                    member,
                    housing_day_stats(
                        rent=day.rent if day else 0,
                        ibi=day.ibi if day else 0,
                        events=tuple(events.get(member, ())),
                        bono=run.bono.get(member, 0),
                        installments=run.installments.get(member, 0),
                    ),
                )
        return runs

    async def _announce_ecb(self, guild_id: int, week: date, before: float, after: float) -> None:
        """Publica la frase del BCE del lunes si en el servidor alguien tiene pisos. Nunca lanza.

        Sin nadie en el mercado sería un mensaje semanal que no le importa a nadie.
        """
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        try:
            market = await self.service.market(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer el mercado para el anuncio del BCE")
            return
        if not market.holdings and not market.mortgages:
            return
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        if channel is None:
            return
        try:
            await channel.send(
                embed=ecb_embed(before, after, week),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo anunciar el Euríbor en %s", guild_id, exc_info=True)

    async def _announce_promotion(self, guild_id: int, builder: int, key: str) -> None:
        """Publica la obra nueva del lunes con su promotor. Nunca lanza."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        try:
            market = await self.service.market(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer el mercado para la obra nueva")
            return
        listing = market.listing(key)
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        if listing is None or channel is None:
            return
        try:
            await channel.send(
                embed=promotion_embed(listing, market.index(listing.barrio), _name(guild, builder)),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo anunciar la obra nueva en %s", guild_id, exc_info=True)

    # -- Logros de compras y ventas ------------------------------------------------

    async def tax_stats(self, guild_id: int) -> dict[int, StatDelta]:
        """Logros de Hacienda de cada casero: lo que lleva pagado por sus pisos."""
        try:
            taxes = await self.economy.housing_tax_breakdown(guild_id)
            rents = await self.economy.housing_rent_totals(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron leer los impuestos de los pisos")
            return {}
        return {
            member: housing_tax_stats(taxes=amounts, rent=rents.get(member, 0))
            for member, amounts in taxes.items()
        }

    async def track_trade(
        self,
        guild_id: int,
        member: discord.abc.User,
        channel: object,
        deal: Purchase | Sale,
        balance: int,
    ) -> None:
        """Logros de una compra o una venta. Nunca lanza."""
        when = datetime.now(TIMEZONE)
        market = await self.service.market(guild_id)
        mine = market.of_member(member.id)
        listing = market.listing(deal.listing)
        if listing is None:
            return
        if isinstance(deal, Purchase):
            delta = housing_purchase_stats(
                kind=listing.kind,
                barrio=listing.barrio,
                total=deal.quote.total,
                itp=deal.quote.itp,
                shares=deal.quote.shares,
                shares_now=deal.shares,
                holdings=len(mine),
                whole=sum(h.shares == rules.SHARES for h in mine),
                islands=len({BARRIO_BY_KEY[market.listing(h.listing).barrio].island for h in mine}),  # type: ignore[union-attr]
                balance_after=balance,
                when=when,
                tensed=rules.tense(deal.index_after) and not rules.tense(deal.index_before),
                barrio_whole=all(
                    market.shares_of(item.key).get(member.id) == rules.SHARES
                    for item in listings_of(listing.barrio)
                ),
            )
        else:
            delta = housing_sale_stats(
                shares=deal.quote.shares,
                received=deal.quote.received,
                gain=deal.quote.gain,
                arrears=deal.quote.arrears,
                held_days=deal.held_days,
                holdings_left=len(mine),
            )
        if isinstance(deal, Purchase) and deal.loan:
            euribor = deal.rate - rules.MORTGAGE_SPREAD
            delta.merge(
                housing_action_stats(
                    "hipoteca",
                    loan=deal.loan,
                    floor=euribor <= rules.EURIBOR_MIN + 1e-9,
                    ceiling=euribor >= rules.EURIBOR_MAX - 1e-9,
                    aval=deal.aval,
                )
            )
        if isinstance(deal, Sale) and deal.vulture:
            delta.merge(housing_action_stats("venta_fondo"))
        if isinstance(deal, Purchase) and deal.promoter is not None:
            own = deal.promoter == member.id
            delta.merge(
                StatDelta(add={"pisos_new_build": 1, **({"pisos_own_build": 1} if own else {})})
            )
        if (taxes := (await self.tax_stats(guild_id)).get(member.id)) is not None:
            delta.merge(taxes)
        await logros.track(self.bot, guild_id, member, channel, delta)

    # -- Comando --------------------------------------------------------------------

    async def _open(
        self,
        responder: CommandResponder,
        send: Callable[..., Awaitable[discord.Message]],
    ) -> None:
        guild, member = responder.guild, responder.member
        if guild is None or member is None:
            await responder.send_error("Los pisos solo se compran dentro de un servidor.")
            return
        # Un panel vivo por persona: cada uno pinta pantallas por adelantado, y abrir
        # veinte seguidos dejaría sin hilos al resto del bot. El viejo se queda quieto.
        for old in [p for p in self.panels if p.owner.id == member.id and p.guild.id == guild.id]:
            old.stop()
            self.panels.discard(old)
        panel = Panel(self, guild=guild, owner=member)
        self.panels.add(panel)
        try:
            await panel.show(None, send=send)
        except (OSError, sqlite3.Error, discord.HTTPException):
            self.panels.discard(panel)
            logger.exception("No se pudo abrir el panel de pisos en %s", guild.id)
            await responder.send_error(
                "La inmobiliaria está cerrada ahora mismo. Prueba en un rato."
            )
            return
        logros.note_for(self.bot, responder, housing_panel_stats(screen="abrir"))

    @app_commands.command(name="pisos", description="El mercado inmobiliario de Canarias.")
    @app_commands.guild_only()
    async def pisos(self, interaction: discord.Interaction) -> None:
        """Abre tu panel de pisos en el canal.

        Dibujar la primera pantalla puede pasar de 3 s (el pintor recién abierto), así
        que se acepta antes y el panel llega como seguimiento.
        """
        await interaction.response.defer(thinking=True)

        async def send(**kwargs: Any) -> discord.Message:
            return await interaction.followup.send(wait=True, **kwargs)

        await self._open(InteractionResponder(interaction), send)

    @commands.command(name="pisos")
    @commands.guild_only()
    async def pisos_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.pisos`)."""
        await self._open(ContextResponder(ctx), ctx.send)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los pisos del servidor cuando el bot deja de pertenecer a él."""
        await self.repository.delete_guild_data(guild.id)
        logger.info("Se eliminaron los pisos del servidor %s", guild.id)


# -- Funciones puente ---------------------------------------------------------------


async def fiscal_of(bot: commands.Bot, guild_id: int, user_id: int) -> int:
    """Valor fiscal de los pisos de alguien (lo que suma al Patrimonio), o 0."""
    cog = find_cog(bot, Pisos)
    if cog is None:
        return 0
    try:
        _values, fiscal = await cog.repository.member_values(guild_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer el valor fiscal de los pisos")
        return 0
    return fiscal.get(user_id, 0)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y los pisos del bot."""
    await bot.add_cog(Pisos(bot, economy=bot.economy, repository=bot.housing))
