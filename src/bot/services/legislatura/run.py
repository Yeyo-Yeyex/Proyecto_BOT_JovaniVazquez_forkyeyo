"""Una partida de Legislatura: estado, acciones del jugador y guardado en JSON.

`Run` es la única pieza con estado. El cog la carga del repositorio, llama a
una acción (`play`, `discard`, `buy`…), enseña `run.log` y lo que devuelva la
acción, y la guarda. Cada acción comprueba que se puede hacer en la fase
actual y, si no, lanza `GameError` con el texto para el usuario y no cambia
nada (lo comprueba todo antes de tocar el estado).

Fases (`Phase`):

    ELEGIR ──start_session──▶ SESION ──(llega a los votos)──▶ MERCADO ──leave──▶ ELEGIR
      │  ▲                       │                            │   ▲
      │  └──skip_session─────────┘ (sin manos: PERDIDA)        └─▶ SOBRE ─┘
      └── tras la moción del último periodo: GANADA ──extend──▶ MERCADO (prórroga)

Se guarda tras cada acción (el cog lo hace) y la semilla la pone el servidor:
abandonar y volver a empezar no cambia la baraja de la partida diaria, y no se
puede mirar una mano y deshacerla.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from bot.services.legislatura import market as mk
from bot.services.legislatura.cards import Card, DeckSpec, Edition, Enhancement, Seal, build_deck
from bot.services.legislatura.catalog import BOSSES, GOVERNMENTS, ITEMS, STAKES, TAGS, items_of
from bot.services.legislatura.consumables import DELAYED
from bot.services.legislatura.effects import Effect, ScoreContext
from bot.services.legislatura.hands import MAX_PLAY, HandType
from bot.services.legislatura.items import Bloc, Category, GameError, ItemDef, Rarity
from bot.services.legislatura.rng import Rng
from bot.services.legislatura.scoring import score

__all__ = ["AdvisorSlot", "GameError", "Phase", "PlayOutcome", "Run"]

#: Versión del formato guardado. Si cambia, `Run.from_dict` tiene que saber leer la anterior.
SAVE_VERSION = 1
#: Periodos de sesiones de una legislatura (4 años × 2 periodos, art. 73.1 CE).
PERIODS = 8
SESSIONS_PER_PERIOD = 3
SESSION_NAMES = ("Sesión ordinaria", "Sesión extraordinaria", "Moción de censura")
#: Votos de la sesión ordinaria de cada periodo (los de Balatro por apuesta).
BASE_TARGETS = (300, 800, 2_000, 5_000, 11_000, 20_000, 35_000, 50_000)
#: Periodos de prórroga como máximo, para que el premio tenga techo.
MAX_EXTENSION = 2
#: Cuánto crece el objetivo en cada periodo de prórroga.
EXTENSION_GROWTH = 4
#: Votos de la extraordinaria frente a la ordinaria (la moción la fija su jefe).
EXTRA_FACTOR = 1.5
#: Fondos por ganar cada sesión (ordinaria, extraordinaria, moción).
SESSION_REWARDS = (3, 4, 5)
#: Fondos con los que se empieza.
STARTING_FONDOS = 4
#: Intereses: 1 fondo por cada tantos fondos guardados.
INTEREST_STEP = 5
#: Fondos por cada jugada que sobra al ganar una sesión.
FONDOS_PER_HAND_LEFT = 1


class Phase(StrEnum):
    """En qué punto está la partida. El valor se guarda."""

    ELEGIR = "elegir"
    SESION = "sesion"
    MERCADO = "mercado"
    SOBRE = "sobre"
    PERDIDA = "perdida"
    GANADA = "ganada"


@dataclass(slots=True)
class AdvisorSlot:
    """Un asesor en la mesa.

    Attributes:
        uid: Único en la partida (dos copias del mismo asesor son dos `uid`).
        key: Clave del catálogo.
        edition: Edición, si tiene.
        state: Estado propio del efecto (contadores); se guarda con la partida.
    """

    uid: int
    key: str
    edition: Edition | None = None
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Forma JSON."""
        return {
            "u": self.uid,
            "k": self.key,
            "e": self.edition.value if self.edition else None,
            "s": self.state,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdvisorSlot:
        """Inversa de `to_dict`."""
        return cls(
            int(data["u"]), str(data["k"]), Edition(data["e"]) if data["e"] else None, data["s"]
        )


@dataclass(frozen=True, slots=True)
class PlayOutcome:
    """Lo que devuelve una jugada para enseñarla.

    Attributes:
        context: La cuenta paso a paso y el total.
        cleared: Si con esta jugada se gana la sesión.
        lost: Si con esta jugada cae el Gobierno.
    """

    context: ScoreContext
    cleared: bool
    lost: bool


@dataclass(slots=True)
class Run:
    """Estado completo de una partida. Ver la docstring del módulo."""

    seed: str
    stake: int = 1
    government: str = "bipartidismo"
    mode: str = "libre"
    phase: Phase = Phase.ELEGIR
    period: int = 1
    session: int = 0
    boss: str = ""
    period_voucher: str | None = None
    extended: bool = False
    cleared_periods: int = 0
    fondos: int = STARTING_FONDOS
    cards: dict[int, Card] = field(default_factory=dict)
    draw: list[int] = field(default_factory=list)
    hand: list[int] = field(default_factory=list)
    hands_left: int = 0
    discards_left: int = 0
    round_score: int = 0
    advisors: list[AdvisorSlot] = field(default_factory=list)
    consumables: list[str] = field(default_factory=list)
    vouchers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    hand_levels: dict[str, int] = field(default_factory=dict)
    hand_plays: dict[str, int] = field(default_factory=dict)
    last_hand: str | None = None
    pending: list[dict[str, Any]] = field(default_factory=list)
    boss_state: dict[str, Any] = field(default_factory=dict)
    market: mk.Market | None = None
    pack: mk.PackState | None = None
    max_advisors: int = 5
    max_consumables: int = 2
    hand_size: int = 8
    base_hands: int = 4
    base_discards: int = 3
    shop_slots: int = 2
    stats: dict[str, int] = field(default_factory=dict)
    next_id: int = 1
    rng: Rng = field(default_factory=lambda: Rng(""))
    log: list[str] = field(default_factory=list)

    # -- Crear -------------------------------------------------------------------------------

    @classmethod
    def new(
        cls,
        seed: str,
        *,
        stake: int = 1,
        government: str = "bipartidismo",
        mode: str = "libre",
        deck: DeckSpec | None = None,
    ) -> Run:
        """Partida nueva con la baraja, el gobierno y la prima dados.

        Raises:
            GameError: Si el gobierno o la prima no existen.
        """
        if government not in GOVERNMENTS:
            raise GameError("Ese gobierno no existe.")
        if stake not in STAKES:
            raise GameError("Esa prima de riesgo no existe.")
        run = cls(seed=seed, stake=stake, government=government, mode=mode, rng=Rng(seed))
        for card in build_deck(deck):
            run.cards[card.id] = card
        run.next_id = max(run.cards) + 1
        run.base_discards = max(0, run.base_discards + STAKES[stake].discards_delta)
        GOVERNMENTS[government].setup(run)
        run._start_period()
        return run

    # -- Consultas para efectos y cog -----------------------------------------------------------

    @staticmethod
    def item(key: str) -> ItemDef:
        """Ficha del catálogo de una clave."""
        return ITEMS[key]

    def effect_of(self, slot: AdvisorSlot) -> Effect:
        """Efecto de un asesor de la mesa."""
        effect = ITEMS[slot.key].effect
        assert effect is not None, f"{slot.key} no es un asesor"
        return effect

    @property
    def boss_effect(self) -> Effect | None:
        """El efecto del jefe si se está jugando la moción de censura."""
        if self.phase is Phase.SESION and self.session == SESSIONS_PER_PERIOD - 1:
            return BOSSES[self.boss].effect
        return None

    def has_advisor(self, key: str) -> bool:
        """Si hay al menos un asesor con esa clave en la mesa."""
        return any(slot.key == key for slot in self.advisors)

    def advisors_of(self, bloc: Bloc) -> list[AdvisorSlot]:
        """Asesores de la mesa de un bloque."""
        return [slot for slot in self.advisors if ITEMS[slot.key].bloc is bloc]

    def legendary_keys(self) -> list[str]:
        """Asesores legendarios que no están ya en la mesa."""
        return [
            i.key
            for i in items_of(Category.ASESOR, visible_only=False)
            if i.rarity is Rarity.LEGENDARIO and not self.has_advisor(i.key)
        ]

    def blocked_reason(self, item: ItemDef) -> str | None:
        """Por qué no se puede tener `item` ahora (lo dice el primer asesor que lo impida)."""
        for slot in self.advisors:
            reason = self.effect_of(slot).blocks(self, slot, item)
            if reason:
                return reason
        return None

    @property
    def advisor_room(self) -> int:
        """Huecos libres para asesores (los Offshore no ocupan)."""
        used = sum(1 for slot in self.advisors if slot.edition is not Edition.OFFSHORE)
        return self.max_advisors - used

    @property
    def target(self) -> int:
        """Votos que pide la sesión actual."""
        if self.period <= PERIODS:
            base = BASE_TARGETS[self.period - 1]
        else:
            base = BASE_TARGETS[-1] * EXTENSION_GROWTH ** (self.period - PERIODS)
        factor = (1.0, EXTRA_FACTOR, BOSSES[self.boss].target_factor)[self.session]
        value = base * factor * STAKES[self.stake].target_factor
        return int(round(value, -1)) if value >= 100 else int(value)

    @property
    def session_name(self) -> str:
        """Nombre de la sesión actual."""
        return SESSION_NAMES[self.session]

    # -- Cambios que usan los efectos -------------------------------------------------------------

    def say(self, text: str) -> None:
        """Apunta un aviso para enseñar tras la acción."""
        self.log.append(text)

    def add_advisor(self, key: str, edition: Edition | None = None) -> AdvisorSlot | None:
        """Sienta un asesor en la mesa. `None` si no cabe."""
        if edition is not Edition.OFFSHORE and self.advisor_room <= 0:
            return None
        slot = AdvisorSlot(self.next_id, key, edition)
        self.next_id += 1
        self.advisors.append(slot)
        self.effect_of(slot).on_acquire(self, slot)
        return slot

    def remove_advisor(self, uid: int) -> None:
        """Quita un asesor de la mesa sin cobrar nada."""
        self.advisors = [slot for slot in self.advisors if slot.uid != uid]

    def add_card(self, rank: int, suit: Any, enhancement: Enhancement | None = None) -> Card:
        """Añade una carta a la baraja (y al mazo de robo si hay sesión en curso)."""
        card = Card(self.next_id, rank, suit, enhancement)
        self.next_id += 1
        self.cards[card.id] = card
        if self.phase is Phase.SESION:
            self.draw.append(card.id)
        return card

    def destroy_card(self, card_id: int) -> None:
        """Quita una carta de la baraja, de la mano y del mazo."""
        self.cards.pop(card_id, None)
        self.hand = [i for i in self.hand if i != card_id]
        self.draw = [i for i in self.draw if i != card_id]

    def add_consumable(self, key: str) -> bool:
        """Guarda un consumible si cabe."""
        if len(self.consumables) >= self.max_consumables:
            return False
        self.consumables.append(key)
        return True

    def level_up(self, hand: HandType, levels: int = 1) -> None:
        """Sube de nivel una combinación."""
        self.hand_levels[hand.value] = self.hand_levels.get(hand.value, 1) + levels

    def redeem(self, key: str) -> None:
        """Aplica un tratado (una sola vez por partida)."""
        if key in self.vouchers:
            return
        self.vouchers.append(key)
        redeem = ITEMS[key].redeem
        if redeem:
            redeem(self)

    def defer(self, key: str, cards: list[Card], *, rounds: int = 0, hands: int = 0) -> None:
        """Programa un efecto diferido de `DELAYED` (en sesiones o en jugadas)."""
        self.pending.append(
            {"key": key, "cards": [c.id for c in cards], "rounds": rounds, "hands": hands}
        )

    def bump(self, stat: str, amount: int = 1) -> None:
        """Suma a una estadística de la partida (para logros al acabar)."""
        self.stats[stat] = self.stats.get(stat, 0) + amount

    def record_max(self, stat: str, value: int) -> None:
        """Guarda el máximo de una estadística."""
        self.stats[stat] = max(self.stats.get(stat, 0), value)

    # -- Ciclo de periodos y sesiones -----------------------------------------------------------

    def _start_period(self) -> None:
        candidates = sorted(k for k, b in BOSSES.items() if b.min_period <= self.period)
        self.boss = self.rng.choice(f"jefe:{self.period}", candidates)
        treaties = [i.key for i in items_of(Category.TRATADO) if i.key not in self.vouchers]
        self.period_voucher = (
            self.rng.choice(f"tratado:{self.period}", treaties) if treaties else None
        )

    def _require(self, *phases: Phase) -> None:
        if self.phase not in phases:
            raise GameError("Eso no se puede hacer ahora.")

    def _fill_hand(self) -> None:
        while len(self.hand) < self.hand_size and self.draw:
            self.hand.append(self.draw.pop(0))

    def start_session(self) -> None:
        """Se presenta a la sesión: baraja, reparte y empieza a jugar."""
        self._require(Phase.ELEGIR)
        self.log.clear()
        self.phase = Phase.SESION
        self.round_score = 0
        self.hands_left = self.base_hands
        self.discards_left = self.base_discards
        self.boss_state = {}
        order = sorted(self.cards)
        self.rng.shuffle(f"mazo:{self.period}:{self.session}", order)
        self.draw, self.hand = order, []
        boss = self.boss_effect
        if boss:
            boss.on_round_start(self, None)
        for slot in list(self.advisors):
            self.effect_of(slot).on_round_start(self, slot)
        self._fill_hand()

    def skip_session(self) -> str:
        """Se abstiene en la sesión ordinaria o extraordinaria a cambio de una abstención.

        Returns:
            La clave de la abstención ganada.
        """
        self._require(Phase.ELEGIR)
        if self.session == SESSIONS_PER_PERIOD - 1:
            raise GameError("A una moción de censura no te puedes abstener.")
        self.log.clear()
        key = self.rng.choice(f"abstencion:{self.period}:{self.session}", sorted(TAGS))
        tag = TAGS[key]
        if tag.instant:
            tag.apply(self)
        else:
            self.tags.append(key)
        self.say(f"{tag.emoji} Te abstienes y te llevas: {tag.name}. {tag.text}")
        self.bump("abstenciones")
        self.session += 1
        return key

    def _check_hand(self, card_ids: list[int]) -> list[Card]:
        if not 1 <= len(card_ids) <= MAX_PLAY:
            raise GameError(f"Elige de 1 a {MAX_PLAY} cartas.")
        if len(set(card_ids)) != len(card_ids) or any(i not in self.hand for i in card_ids):
            raise GameError("Esas cartas no están en tu mano.")
        return [self.cards[i] for i in card_ids]

    def play(self, card_ids: list[int]) -> PlayOutcome:
        """Juega de 1 a 5 cartas de la mano.

        Raises:
            GameError: Si no hay sesión, no quedan jugadas o las cartas no valen.
        """
        self._require(Phase.SESION)
        if self.hands_left <= 0:
            raise GameError("No te quedan jugadas.")
        played = self._check_hand(card_ids)
        self.log.clear()
        ctx = score(self, played)
        total = ctx.total
        self.round_score += total
        self.hands_left -= 1
        hand = ctx.evaluation.hand.value
        self.last_hand = hand
        self.hand_plays[hand] = self.hand_plays.get(hand, 0) + 1
        self.bump("jugadas")
        self.record_max("mejor_jugada", total)
        self.hand = [i for i in self.hand if i not in card_ids]
        for card_id in ctx.destroyed:
            self.destroy_card(card_id)
            self.bump("promesas_rotas")
        self._tick_pending(hands=True)
        self._fill_hand()

        cleared = self.round_score >= self.target
        lost = False
        if cleared:
            self._win_session()
        elif self.hands_left <= 0:
            cleared = self._try_save()
            lost = not cleared
        return PlayOutcome(ctx, cleared, lost)

    def discard(self, card_ids: list[int]) -> None:
        """Descarta de 1 a 5 cartas y roba otras tantas."""
        self._require(Phase.SESION)
        if self.discards_left <= 0:
            raise GameError("No te quedan descartes.")
        cards = self._check_hand(card_ids)
        self.log.clear()
        self.discards_left -= 1
        self.bump("descartes")
        boss = self.boss_effect
        if boss:
            boss.on_discard(self, None, cards)
        for slot in list(self.advisors):
            self.effect_of(slot).on_discard(self, slot, cards)
        for card in cards:
            if card.seal is Seal.PARTIDO:
                key = self.rng.choice(
                    "sello:partido", [i.key for i in items_of(Category.NORMATIVA)]
                )
                if self.add_consumable(key):
                    self.say(f"🟣 El Partido te manda una normativa: {ITEMS[key].name}.")
        self.hand = [i for i in self.hand if i not in card_ids]
        self._fill_hand()

    def _try_save(self) -> bool:
        for slot in list(self.advisors):
            if self.effect_of(slot).saves(self, slot):
                self._win_session(saved=True)
                return True
        self.phase = Phase.PERDIDA
        self.say(f"💥 Cae el Gobierno en la {self.session_name.lower()} del periodo {self.period}.")
        return False

    def _tick_pending(self, *, hands: bool = False, rounds: bool = False) -> None:
        keep = []
        for entry in self.pending:
            if hands and entry["hands"]:
                entry["hands"] -= 1
            if rounds and entry["rounds"]:
                entry["rounds"] -= 1
            if entry["hands"] or entry["rounds"]:
                keep.append(entry)
                continue
            cards = [self.cards[i] for i in entry["cards"] if i in self.cards]
            self.say(f"⏰ {ITEMS[entry['key']].name}: {DELAYED[entry['key']](self, cards)}")
        self.pending = keep

    def _win_session(self, *, saved: bool = False) -> None:
        """Cierra una sesión ganada: efectos de fin, cobro y avance."""
        held = [self.cards[i] for i in self.hand]
        for card in held:
            if card.enhancement is Enhancement.LINGOTE:
                self.fondos += 3
            if card.seal is Seal.BRUSELAS and self.last_hand:
                polls = [i.key for i in items_of(Category.ENCUESTA) if i.hand == self.last_hand]
                if polls and self.add_consumable(polls[0]):
                    self.say(f"🔵 Bruselas encarga la encuesta {ITEMS[polls[0]].name}.")
        for slot in list(self.advisors):
            self.effect_of(slot).on_round_end(self, slot)
        self._tick_pending(rounds=True)

        stake = STAKES[self.stake]
        reward = SESSION_REWARDS[self.session]
        if self.session == 0 and not stake.ordinary_reward:
            reward = 0
        leftover = self.hands_left * FONDOS_PER_HAND_LEFT
        interest = min(max(self.fondos, 0) // INTEREST_STEP, stake.interest_cap)
        self.fondos += reward + leftover + interest
        self.say(
            f"✅ {self.session_name} superada{' (por los pelos)' if saved else ''}: "
            f"+{reward} por ganar, +{leftover} por jugadas sobrantes, +{interest} de intereses."
        )
        self.record_max("fondos_max", self.fondos)
        self.bump("sesiones")
        self.hand, self.draw = [], []

        if self.session == SESSIONS_PER_PERIOD - 1:
            self.cleared_periods += 1
            self.bump("mociones")
            self.period += 1
            self.session = 0
            if self.period > PERIODS:
                self.phase = Phase.GANADA
                if self.can_extend:
                    self.say("🏆 Legislatura completa. Cobra o prorroga los presupuestos.")
                else:
                    self.say("🏆 Presupuestos prorrogados hasta el final. Toca cobrar.")
                return
            self._start_period()
        else:
            self.session += 1
        self.market = mk.open_market(self)
        self.phase = Phase.MERCADO

    @property
    def can_extend(self) -> bool:
        """Si aún se puede prorrogar otro periodo (hay tope: ver `MAX_EXTENSION`)."""
        return self.period <= PERIODS + MAX_EXTENSION

    def extend(self) -> None:
        """Tras ganar, juega un periodo más en prórroga (Presupuestos prorrogados).

        Si cae el Gobierno en la prórroga se pierde todo el premio
        (`bot.services.legislatura.prizes.payout`), y no se puede prorrogar
        más de `MAX_EXTENSION` periodos.
        """
        self._require(Phase.GANADA)
        if not self.can_extend:
            raise GameError("Ya no se puede prorrogar más: toca cobrar.")
        self.log.clear()
        self.extended = True
        self._start_period()
        self.market = mk.open_market(self)
        self.phase = Phase.MERCADO

    # -- Mercado ----------------------------------------------------------------------------------

    def _pay(self, price: int) -> None:
        if self.fondos < price:
            raise GameError(f"No te llega: cuesta {price} y tienes {self.fondos}.")
        self.fondos -= price

    def _market(self) -> mk.Market:
        self._require(Phase.MERCADO)
        assert self.market is not None
        return self.market

    def offer_price(self, offer: mk.Offer) -> int:
        """Precio actual de una oferta del escaparate."""
        if offer.free:
            return 0
        return mk.price_of(self, ITEMS[offer.key], offer.edition)  # type: ignore[index]

    def _take(self, key: str, edition: Edition | None) -> None:
        """Mete en la partida algo comprado o elegido (sin cobrar). Comprueba antes si cabe."""
        item = ITEMS[key]
        if item.category is Category.ASESOR:
            reason = self.blocked_reason(item)
            if reason:
                raise GameError(reason)
            if self.add_advisor(key, edition) is None:
                raise GameError("No te cabe otro asesor: vende uno antes.")
            self.bump("asesores_fichados")
        elif item.category is Category.ENCUESTA and self.phase is Phase.SOBRE:
            assert item.usable is not None
            self.say(item.usable.apply(self, []))
        elif item.category.consumable:
            if not self.add_consumable(key):
                raise GameError("No te caben más consumibles: usa o vende uno.")
        else:
            raise GameError("Eso no se coge así.")

    def buy(self, index: int) -> None:
        """Compra una oferta suelta del escaparate."""
        market = self._market()
        if not 0 <= index < len(market.offers) or market.offers[index].sold:
            raise GameError("Esa oferta ya no está.")
        offer = market.offers[index]
        price = self.offer_price(offer)
        if self.fondos < price:
            raise GameError(f"No te llega: cuesta {price} y tienes {self.fondos}.")
        self.log.clear()
        self._take(offer.key, offer.edition)  # type: ignore[arg-type]
        self.fondos -= price
        offer.sold = True
        self.bump("compras")

    def buy_voucher(self) -> None:
        """Compra el tratado del periodo."""
        market = self._market()
        if not market.voucher:
            raise GameError("No hay tratado a la venta.")
        self._pay(mk.price_of(self, ITEMS[market.voucher]))
        self.log.clear()
        self.redeem(market.voucher)
        self.say(f"🖋️ Firmas {ITEMS[market.voucher].name}.")
        market.voucher = None
        self.bump("tratados")

    def buy_pack(self, index: int) -> None:
        """Compra y abre un sobre."""
        market = self._market()
        if not 0 <= index < len(market.packs) or market.packs[index].sold:
            raise GameError("Ese sobre ya no está.")
        offer = market.packs[index]
        item = ITEMS[offer.key]  # type: ignore[index]
        assert item.pack is not None
        self._pay(mk.price_of(self, item))
        self.log.clear()
        offer.sold = True
        options = mk.roll_pack(self, item.key, item.pack)
        self.pack = mk.PackState(item.key, options, item.pack.picks)
        self.phase = Phase.SOBRE
        self.bump("sobres")

    def pick(self, index: int) -> None:
        """Se queda una opción del sobre abierto."""
        self._require(Phase.SOBRE)
        assert self.pack is not None
        if not 0 <= index < len(self.pack.options) or self.pack.options[index].sold:
            raise GameError("Esa opción ya no está.")
        option = self.pack.options[index]
        self.log.clear()
        if option.card is not None:
            template = Card.from_dict({**option.card, "i": 0})
            card = self.add_card(template.rank, template.suit, template.enhancement)
            self.say(f"🪪 Se afilia a tu partido el {card.label}.")
        else:
            self._take(option.key, option.edition)  # type: ignore[arg-type]
        option.sold = True
        self.pack.picks_left -= 1
        if self.pack.picks_left <= 0:
            self.close_pack()

    def close_pack(self) -> None:
        """Cierra el sobre (con o sin elegir) y vuelve al mercado."""
        self._require(Phase.SOBRE)
        self.pack = None
        self.phase = Phase.MERCADO

    def reroll(self) -> None:
        """Da otra vuelta al escaparate (las ofertas sueltas)."""
        market = self._market()
        self._pay(market.reroll_cost)
        self.log.clear()
        self.bump("vueltas")
        market.offers = mk.roll_offers(self, f"{self.period}:{self.session}:vuelta")
        market.reroll_cost += 1

    def sell(self, uid: int) -> int:
        """Vende un asesor. Se puede en cualquier fase menos al acabar la partida.

        Returns:
            Lo cobrado.
        """
        if self.phase in (Phase.PERDIDA, Phase.GANADA):
            raise GameError("La partida ya ha terminado.")
        slot = next((s for s in self.advisors if s.uid == uid), None)
        if slot is None:
            raise GameError("Ese asesor no está en tu mesa.")
        self.log.clear()
        value = mk.sell_value(self, slot)
        self.effect_of(slot).on_sell(self, slot)
        self.remove_advisor(uid)
        self.fondos += value
        self.bump("ventas")
        return value

    def move_advisor(self, uid: int, position: int) -> None:
        """Cambia de sitio un asesor (el orden cambia la cuenta)."""
        slot = next((s for s in self.advisors if s.uid == uid), None)
        if slot is None:
            raise GameError("Ese asesor no está en tu mesa.")
        self.advisors.remove(slot)
        self.advisors.insert(max(0, min(position, len(self.advisors))), slot)

    def use(self, index: int, card_ids: list[int] | None = None) -> str:
        """Usa un consumible guardado sobre las cartas elegidas.

        Returns:
            El texto de lo que ha pasado.
        """
        if self.phase in (Phase.PERDIDA, Phase.GANADA):
            raise GameError("La partida ya ha terminado.")
        if not 0 <= index < len(self.consumables):
            raise GameError("Ese consumible no está.")
        item = ITEMS[self.consumables[index]]
        assert item.usable is not None
        card_ids = card_ids or []
        usable = item.usable
        if usable.max_targets:
            if self.phase is not Phase.SESION:
                raise GameError("Esto se usa con cartas en la mano: espera a la sesión.")
            if not usable.min_targets <= len(card_ids) <= usable.max_targets:
                raise GameError(f"Elige de {usable.min_targets} a {usable.max_targets} cartas.")
            cards = self._check_hand(card_ids)
        else:
            cards = []
        self.log.clear()
        text = usable.apply(self, cards)
        self.consumables.pop(index)
        self.bump(f"usado_{item.category.value}")
        return text

    def leave_market(self) -> None:
        """Sale del mercado hacia la siguiente sesión."""
        self._market()
        self.log.clear()
        self.market = None
        self.phase = Phase.ELEGIR

    # -- Guardar --------------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Todo el estado en tipos de JSON (sin `log`, que es de una sola acción)."""
        return {
            "v": SAVE_VERSION,
            "seed": self.seed,
            "stake": self.stake,
            "government": self.government,
            "mode": self.mode,
            "phase": self.phase.value,
            "period": self.period,
            "session": self.session,
            "boss": self.boss,
            "period_voucher": self.period_voucher,
            "extended": self.extended,
            "cleared_periods": self.cleared_periods,
            "fondos": self.fondos,
            "cards": [c.to_dict() for c in self.cards.values()],
            "draw": self.draw,
            "hand": self.hand,
            "hands_left": self.hands_left,
            "discards_left": self.discards_left,
            "round_score": self.round_score,
            "advisors": [s.to_dict() for s in self.advisors],
            "consumables": self.consumables,
            "vouchers": self.vouchers,
            "tags": self.tags,
            "hand_levels": self.hand_levels,
            "hand_plays": self.hand_plays,
            "last_hand": self.last_hand,
            "pending": self.pending,
            "boss_state": self.boss_state,
            "market": self.market.to_dict() if self.market else None,
            "pack": self.pack.to_dict() if self.pack else None,
            "limits": [
                self.max_advisors,
                self.max_consumables,
                self.hand_size,
                self.base_hands,
                self.base_discards,
                self.shop_slots,
            ],
            "stats": self.stats,
            "next_id": self.next_id,
            "rng": self.rng.counters,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Run:
        """Inversa de `to_dict`.

        Raises:
            ValueError: Si el formato es de una versión que no se sabe leer.
        """
        if data.get("v") != SAVE_VERSION:
            raise ValueError(f"Partida guardada con un formato desconocido: {data.get('v')}")
        run = cls(seed=data["seed"], rng=Rng(data["seed"], data["rng"]))
        for name in (
            "stake", "government", "mode", "period", "session", "boss", "period_voucher",
            "extended", "cleared_periods", "fondos", "draw", "hand", "hands_left",
            "discards_left", "round_score", "consumables", "vouchers", "tags", "hand_levels",
            "hand_plays", "last_hand", "pending", "boss_state", "stats", "next_id",
        ):  # fmt: skip
            setattr(run, name, data[name])
        run.phase = Phase(data["phase"])
        run.cards = {c["i"]: Card.from_dict(c) for c in data["cards"]}
        run.advisors = [AdvisorSlot.from_dict(s) for s in data["advisors"]]
        run.market = mk.Market.from_dict(data["market"]) if data["market"] else None
        run.pack = mk.PackState.from_dict(data["pack"]) if data["pack"] else None
        (
            run.max_advisors,
            run.max_consumables,
            run.hand_size,
            run.base_hands,
            run.base_discards,
            run.shop_slots,
        ) = data["limits"]
        return run

    def dumps(self) -> str:
        """JSON compacto para la base de datos."""
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def loads(cls, text: str) -> Run:
        """Inversa de `dumps`."""
        return cls.from_dict(json.loads(text))
