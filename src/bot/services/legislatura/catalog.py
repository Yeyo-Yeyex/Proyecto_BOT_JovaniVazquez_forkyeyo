"""Registro de todo lo que existe en una partida de Legislatura.

Una cosa nueva es una fila aquí (y, si hace algo nuevo, su efecto en
`advisors` o `consumables`). Las claves se guardan en las partidas y en los
logros: no se cambian nunca. Lo retirado se marca `visible=False` en vez de
borrarlo, para que las partidas guardadas lo sigan entendiendo.

Contiene:

- `ITEMS`: asesores, encuestas, normativas, cloacas, tratados y sobres.
- `BOSSES`: las mociones de censura (jefe de la tercera sesión de cada periodo).
- `GOVERNMENTS`: con qué gobierno se empieza (las barajas de Balatro).
- `STAKES`: las primas de riesgo (dificultad), acumulativas.
- `TAGS`: lo que se gana al abstenerse en una sesión.

Las cifras (precios, rarezas, objetivos) son de partida y se recalibran con un
simulador antes de abrir el juego, igual que el resto de la economía.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.services.legislatura import advisors as adv
from bot.services.legislatura import consumables as con
from bot.services.legislatura.cards import Card, Suit
from bot.services.legislatura.effects import Effect, ScoreContext
from bot.services.legislatura.hands import HandType
from bot.services.legislatura.items import (
    Bloc,
    Category,
    ItemDef,
    PackSpec,
    Rarity,
    Usable,
)

if TYPE_CHECKING:
    from bot.services.legislatura.run import AdvisorSlot, Run


def _advisor(
    key: str,
    name: str,
    emoji: str,
    bloc: Bloc,
    rarity: Rarity,
    cost: int,
    effect: Effect,
    text: str,
    *,
    visible: bool = True,
) -> ItemDef:
    return ItemDef(
        key, Category.ASESOR, name, emoji, bloc, cost, text, rarity, effect, visible=visible
    )


def _poll(key: str, name: str, emoji: str, bloc: Bloc, hand: HandType, text: str) -> ItemDef:
    def apply(run: Run, cards: list[Card]) -> str:
        # Cruce: el CIS sale mejor con el presidente en la mesa.
        levels = 2 if key == "cis" and run.has_advisor("perro_sanxe") else 1
        run.level_up(hand, levels)
        return f"📊 {name}: {hand.value.replace('_', ' ')} sube {levels} nivel(es)."

    return ItemDef(
        key, Category.ENCUESTA, name, emoji, bloc, 3, text, usable=Usable(0, 0, apply), hand=hand
    )


def _rule(
    key: str,
    category: Category,
    name: str,
    emoji: str,
    bloc: Bloc,
    cost: int,
    targets: tuple[int, int],
    apply: Callable[[Run, list[Card]], str],
    text: str,
) -> ItemDef:
    return ItemDef(key, category, name, emoji, bloc, cost, text, usable=Usable(*targets, apply))


def _treaty(
    key: str, name: str, emoji: str, bloc: Bloc, redeem: Callable[[Run], None], text: str
) -> ItemDef:
    return ItemDef(key, Category.TRATADO, name, emoji, bloc, 10, text, redeem=redeem)


def _pack(key: str, name: str, emoji: str, cost: int, spec: PackSpec, text: str) -> ItemDef:
    return ItemDef(key, Category.SOBRE, name, emoji, Bloc.NEUTRO, cost, text, pack=spec)


_ITEMS: tuple[ItemDef, ...] = (
    # -- Asesores -------------------------------------------------------------------------
    _advisor(
        "perro_sanxe",
        "Perro Sanxe",
        "🐕",
        Bloc.ESPANA,
        Rarity.LEGENDARIO,
        20,
        adv.PerroSanxe(),
        "Cada oro que puntúa da ×1,5 mult. Al acabar la sesión se queda el 21 % de tus fondos.",
        visible=False,
    ),
    _advisor(
        "puerta_giratoria",
        "Puerta giratoria",
        "🚪",
        Bloc.ESPANA,
        Rarity.POCO_COMUN,
        6,
        adv.PuertaGiratoria(),
        "Vender cualquier asesor da el doble.",
    ),
    _advisor(
        "boicot_eurovision",
        "Boicot a Eurovisión",
        "📺",
        Bloc.ESPANA,
        Rarity.COMUN,
        5,
        adv.BoicotEurovision(),
        "+5 mult. Echa a los asesores de Israel y no deja comprar más.",
    ),
    _advisor(
        "aforado",
        "Aforado",
        "⚖️",
        Bloc.ESPANA,
        Rarity.RARO,
        8,
        adv.Aforado(),
        "Si cae el Gobierno, lo salva una vez y desaparece.",
    ),
    _advisor(
        "veto_hungria",
        "Veto de Hungría",
        "🇭🇺",
        Bloc.UE,
        Rarity.COMUN,
        5,
        adv.VetoHungria(),
        "+10 mult. 1 de cada 8 jugadas Hungría veta y no suma.",
    ),
    _advisor(
        "multa_competencia",
        "Multa de Competencia",
        "🧾",
        Bloc.UE,
        Rarity.POCO_COMUN,
        6,
        adv.MultaCompetencia(),
        "Al acabar la sesión, +3 fondos por cada asesor de EE. UU.",
    ),
    _advisor(
        "aranceles",
        "Aranceles",
        "🧱",
        Bloc.EEUU,
        Rarity.COMUN,
        6,
        adv.Aranceles(),
        "Lo chino cuesta un 50 % más. Sin asesores chinos, ×1,5 mult.",
    ),
    _advisor(
        "cumbre_otan",
        "Cumbre de la OTAN",
        "🛡️",
        Bloc.EEUU,
        Rarity.POCO_COMUN,
        7,
        adv.CumbreOtan(),
        "+2 mult por cada otro asesor. Cobra el 5 % de los fondos (con Perro Sanxe, el 2,1 %).",
    ),
    _advisor(
        "fabrica_shenzhen",
        "Fábrica de Shenzhen",
        "🏭",
        Bloc.CHINA,
        Rarity.RARO,
        8,
        adv.FabricaShenzhen(),
        "La primera carta que puntúa se activa otra vez.",
    ),
    _advisor(
        "credito_social",
        "Crédito social",
        "📱",
        Bloc.CHINA,
        Rarity.COMUN,
        5,
        adv.CreditoSocial(),
        "+1 mult acumulado por cada jugada. Cada descarte resta 2 para siempre.",
    ),
    _advisor(
        "asentamiento",
        "Asentamiento",
        "🏗️",
        Bloc.ISRAEL,
        Rarity.POCO_COMUN,
        6,
        adv.Asentamiento(),
        "Cada sesión se anexiona una carta de tu baraja. +3 mult por carta anexionada.",
    ),
    _advisor(
        "paquete_ayuda",
        "Paquete de ayuda",
        "📦",
        Bloc.ISRAEL,
        Rarity.COMUN,
        4,
        adv.PaqueteAyuda(),
        "+4 fondos al acabar la sesión si hay un asesor de EE. UU.",
    ),
    # -- Encuestas (una por combinación) ------------------------------------------------
    _poll(
        "sondeo_calle",
        "Sondeo a pie de calle",
        "🎤",
        Bloc.NEUTRO,
        HandType.CARTA_ALTA,
        "Sube Carta alta.",
    ),
    _poll(
        "cis",
        "CIS",
        "📊",
        Bloc.ESPANA,
        HandType.PAREJA,
        "Sube Pareja. Con Perro Sanxe en la mesa, dos niveles.",
    ),
    _poll(
        "elecciones_anticipadas",
        "Elecciones anticipadas",
        "🗳️",
        Bloc.ISRAEL,
        HandType.DOBLE_PAREJA,
        "Sube Doble pareja. Cinco elecciones en cuatro años.",
    ),
    _poll(
        "barometro_bar",
        "Barómetro del bar",
        "🍺",
        Bloc.ESPANA,
        HandType.LAS_CUARENTA,
        "Sube Las cuarenta.",
    ),
    _poll("eurobarometro", "Eurobarómetro", "🇪🇺", Bloc.UE, HandType.TRIO, "Sube Trío."),
    _poll("gallup", "Gallup", "📈", Bloc.EEUU, HandType.ESCALERA, "Sube Escalera."),
    _poll("caucus_iowa", "Caucus de Iowa", "🌽", Bloc.EEUU, HandType.COLOR, "Sube Color."),
    _poll(
        "xinhua",
        "Agencia Xinhua",
        "📰",
        Bloc.CHINA,
        HandType.FULL,
        "Sube Full, con un 99,8 % de aprobación.",
    ),
    _poll("canal_12", "Sondeo del Canal 12", "📡", Bloc.ISRAEL, HandType.POKER, "Sube Póker."),
    _poll(
        "europeas",
        "Elecciones europeas",
        "🗳️",
        Bloc.UE,
        HandType.ESCALERA_COLOR,
        "Sube Escalera de color.",
    ),
    _poll(
        "congreso_partido",
        "Congreso del Partido",
        "☭",
        Bloc.CHINA,
        HandType.REPOKER,
        "Sube Repóker.",
    ),
    _poll(
        "supermartes", "Supermartes", "🦅", Bloc.EEUU, HandType.FULL_COLOR, "Sube Full de color."
    ),
    _poll(
        "referendum_otan",
        "Referéndum de la OTAN",
        "✅",
        Bloc.ESPANA,
        HandType.REPOKER_COLOR,
        "Sube Repóker de color. «OTAN, de entrada no».",
    ),
    # -- Normativas -----------------------------------------------------------------------
    _rule(
        "decreto_ley",
        Category.NORMATIVA,
        "Decreto ley",
        "📜",
        Bloc.ESPANA,
        3,
        (1, 3),
        con.decreto_ley,
        "Hasta 3 cartas pasan a oros. 1 de cada 3 no se convalida.",
    ),
    _rule(
        "directiva_europea",
        Category.NORMATIVA,
        "Directiva europea",
        "🇪🇺",
        Bloc.UE,
        3,
        (1, 3),
        con.directiva_europea,
        "Hasta 3 cartas pasan a Funcionario dentro de 2 sesiones.",
    ),
    _rule(
        "orden_ejecutiva",
        Category.NORMATIVA,
        "Orden ejecutiva",
        "🖊️",
        Bloc.EEUU,
        3,
        (1, 2),
        con.orden_ejecutiva,
        "Hasta 2 cartas pasan a Promesa. 1 de cada 4 la tumba un juez.",
    ),
    _rule(
        "plan_quinquenal",
        Category.NORMATIVA,
        "Plan quinquenal",
        "🏭",
        Bloc.CHINA,
        3,
        (1, 5),
        con.plan_quinquenal,
        "Hasta 5 cartas pasan a Enchufado dentro de 5 jugadas.",
    ),
    _rule(
        "nacion_startup",
        Category.NORMATIVA,
        "Nación startup",
        "🚀",
        Bloc.ISRAEL,
        3,
        (1, 1),
        con.nacion_startup,
        "Copia una carta a tu baraja.",
    ),
    # -- Cloacas ---------------------------------------------------------------------------
    _rule(
        "rescate_troika",
        Category.CLOACA,
        "Rescate de la troika",
        "🏦",
        Bloc.UE,
        4,
        (0, 0),
        con.rescate_troika,
        "+20 fondos y una jugada menos por sesión para siempre.",
    ),
    _rule(
        "guerra_arancelaria",
        Category.CLOACA,
        "Guerra arancelaria total",
        "💥",
        Bloc.EEUU,
        4,
        (0, 0),
        con.guerra_arancelaria,
        "Duplica tus asesores de EE. UU. y destruye los chinos.",
    ),
    _rule(
        "fondo_reptiles",
        Category.CLOACA,
        "Fondo de reptiles",
        "🐍",
        Bloc.ESPANA,
        4,
        (0, 0),
        con.fondo_reptiles,
        "Un asesor legendario y te quedas sin fondos.",
    ),
    # -- Tratados ---------------------------------------------------------------------------
    _treaty("schengen", "Schengen", "🛂", Bloc.UE, con.schengen, "Un hueco más en la tienda."),
    _treaty(
        "acuerdo_paris",
        "Acuerdo de París",
        "🌍",
        Bloc.NEUTRO,
        con.acuerdo_paris,
        "Un descarte más por sesión.",
    ),
    _treaty(
        "mercado_unico",
        "Mercado único",
        "🇪🇺",
        Bloc.UE,
        con.mercado_unico,
        "Todo un 25 % más barato.",
    ),
    _treaty(
        "ruta_seda",
        "Ruta de la Seda",
        "🚢",
        Bloc.CHINA,
        con.ruta_seda,
        "Un hueco más para consumibles.",
    ),
    # -- Sobres -------------------------------------------------------------------------------
    _pack(
        "sobre_encuestas",
        "Sobre de encuestas",
        "📊",
        4,
        PackSpec(Category.ENCUESTA, 3, 1),
        "3 encuestas, te quedas 1.",
    ),
    _pack(
        "sobre_normativas",
        "Sobre de normativas",
        "📜",
        4,
        PackSpec(Category.NORMATIVA, 3, 1),
        "3 normativas, te quedas 1.",
    ),
    _pack(
        "sobre_asesores",
        "Sobre de asesores",
        "💼",
        6,
        PackSpec(Category.ASESOR, 2, 1),
        "2 asesores, te quedas 1.",
    ),
    _pack(
        "sobre_cloacas",
        "Sobre de las cloacas",
        "🐀",
        6,
        PackSpec(Category.CLOACA, 2, 1),
        "2 cloacas, te quedas 1.",
    ),
    _pack(
        "sobre_militantes",
        "Sobre de militantes",
        "🪪",
        4,
        PackSpec(None, 3, 1),
        "3 cartas para tu baraja, te quedas 1.",
    ),
)

ITEMS: dict[str, ItemDef] = {item.key: item for item in _ITEMS}


def items_of(category: Category, *, visible_only: bool = True) -> list[ItemDef]:
    """Cosas de una categoría, en el orden del catálogo."""
    return [i for i in _ITEMS if i.category is category and (i.visible or not visible_only)]


# -- Mociones de censura (jefes) -------------------------------------------------------------


class HuelgaOros(Effect):
    """Los oros no puntúan."""

    def debuffs(self, run: Run, card: Card) -> bool:
        return card.suit is Suit.OROS and not card.is_stone


class Auditoria(Effect):
    """Cada descarte cuesta 1 fondo."""

    def on_discard(self, run: Run, slot: AdvisorSlot | None, cards: list[Card]) -> None:
        run.fondos -= 1
        run.say("🔎 Auditoría del Tribunal de Cuentas: −1 fondo por descartar.")


class GranCortafuegos(Effect):
    """No se ven los palos de las cartas."""

    @property
    def hides_suits(self) -> bool:
        return True


class Tamayazo(Effect):
    """Al empezar, dos cartas de la baraja se fugan y no puntúan en la sesión."""

    def on_round_start(self, run: Run, slot: AdvisorSlot | None) -> None:
        stream = run.rng.stream(f"jefe:tamayazo:{run.period}")
        fugados = stream.sample(sorted(run.cards), k=min(2, len(run.cards)))
        run.boss_state["fugados"] = fugados
        run.say("🏃 Tamayazo: dos de los tuyos no aparecen a votar.")

    def debuffs(self, run: Run, card: Card) -> bool:
        return card.id in run.boss_state.get("fugados", [])


class ResolucionOnu(Effect):
    """El mult se divide entre 2, salvo que un asesor de EE. UU. la vete."""

    def on_hand(self, ctx: ScoreContext, slot: AdvisorSlot | None) -> None:
        if ctx.run.advisors_of(Bloc.EEUU):
            ctx.note("jefe", "🇺🇳 Resolución de la ONU vetada por EE. UU.")
            return
        ctx.times_mult("jefe", 0.5)


@dataclass(frozen=True, slots=True)
class BossDef:
    """Una moción de censura.

    Attributes:
        min_period: Periodo desde el que puede salir.
        target_factor: Votos que pide frente a la sesión ordinaria.
    """

    key: str
    name: str
    emoji: str
    bloc: Bloc
    text: str
    effect: Effect
    min_period: int = 1
    target_factor: float = 2.0


BOSSES: dict[str, BossDef] = {
    b.key: b
    for b in (
        BossDef(
            "huelga_oros", "Huelga de oros", "🪧", Bloc.ESPANA, "Los oros no puntúan.", HuelgaOros()
        ),
        BossDef(
            "auditoria",
            "Auditoría",
            "🔎",
            Bloc.ESPANA,
            "Cada descarte cuesta 1 fondo.",
            Auditoria(),
        ),
        BossDef(
            "gran_cortafuegos",
            "Gran Cortafuegos",
            "🧱",
            Bloc.CHINA,
            "No se ven los palos de las cartas.",
            GranCortafuegos(),
            min_period=2,
        ),
        BossDef(
            "tamayazo",
            "Tamayazo",
            "🏃",
            Bloc.ESPANA,
            "Dos cartas de tu baraja no puntúan en esta sesión.",
            Tamayazo(),
        ),
        BossDef(
            "resolucion_onu",
            "Resolución de la ONU",
            "🇺🇳",
            Bloc.NEUTRO,
            "El mult se divide entre 2, salvo que un asesor de EE. UU. la vete.",
            ResolucionOnu(),
            min_period=3,
        ),
    )
}


# -- Gobiernos (barajas iniciales) ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GovernmentDef:
    """Con qué se empieza la partida. `setup` se aplica una vez al crearla."""

    key: str
    name: str
    emoji: str
    text: str
    setup: Callable[[Run], None]
    unlocked_by: str | None = None


def _bipartidismo(run: Run) -> None:
    """Las reglas de serie."""


def _partido_unico(run: Run) -> None:
    run.base_discards = 0
    run.base_hands += 1


def _tecnocracia(run: Run) -> None:
    run.redeem("schengen")
    run.redeem("mercado_unico")


def _superpotencia(run: Run) -> None:
    run.fondos += 10
    run.add_advisor("aranceles")


GOVERNMENTS: dict[str, GovernmentDef] = {
    g.key: g
    for g in (
        GovernmentDef("bipartidismo", "Bipartidismo", "🏛️", "Las reglas de siempre.", _bipartidismo),
        GovernmentDef(
            "partido_unico",
            "Partido único",
            "☭",
            "Sin descartes y una jugada más por sesión.",
            _partido_unico,
            unlocked_by="ganar_bipartidismo",
        ),
        GovernmentDef(
            "tecnocracia",
            "Tecnocracia",
            "🇪🇺",
            "Empiezas con Schengen y el Mercado único.",
            _tecnocracia,
            unlocked_by="ganar_bipartidismo",
        ),
        GovernmentDef(
            "superpotencia",
            "Superpotencia",
            "🦅",
            "+10 fondos y empiezas con Aranceles.",
            _superpotencia,
            unlocked_by="ganar_bipartidismo",
        ),
    )
}


# -- Primas de riesgo (dificultad) ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StakeDef:
    """Un nivel de prima de riesgo. Cada nivel incluye todo lo de los anteriores.

    Attributes:
        level: De 1 a 8; se desbloquea ganando el anterior.
        target_factor: Multiplica los votos de todas las sesiones.
        ordinary_reward: Si la sesión ordinaria da fondos.
        discards_delta: Descartes de más o de menos.
        advisor_surcharge: Fondos de más por asesor en la tienda.
        interest_cap: Máximo de intereses por sesión.
    """

    level: int
    name: str
    text: str
    target_factor: float = 1.0
    ordinary_reward: bool = True
    discards_delta: int = 0
    advisor_surcharge: int = 0
    interest_cap: int = 5


STAKES: dict[int, StakeDef] = {
    s.level: s
    for s in (
        StakeDef(1, "Prima a 50", "Las reglas de serie."),
        StakeDef(2, "Prima a 100", "La sesión ordinaria no da fondos.", ordinary_reward=False),
        StakeDef(3, "Prima a 200", "Las sesiones piden un 20 % más.", 1.2, False),
        StakeDef(4, "Prima a 300", "Un descarte menos.", 1.2, False, -1),
        StakeDef(5, "Prima a 400", "Las sesiones piden un 45 % más.", 1.45, False, -1),
        StakeDef(
            6, "Intervención de Bruselas", "Los asesores cuestan 1 fondo más.", 1.45, False, -1, 1
        ),
        StakeDef(7, "Rebaja de Moody's", "Las sesiones piden un 75 % más.", 1.75, False, -1, 1),
        StakeDef(8, "Rescate", "Los intereses no pasan de 2 fondos.", 1.75, False, -1, 1, 2),
    )
}


# -- Abstenciones (premios por saltarse una sesión) ----------------------------------------


@dataclass(frozen=True, slots=True)
class TagDef:
    """Lo que se gana al abstenerse.

    Attributes:
        instant: Si se aplica nada más ganarlo (si no, espera a la próxima tienda).
        apply: Lo que hace al aplicarse.
    """

    key: str
    name: str
    emoji: str
    text: str
    instant: bool
    apply: Callable[[Run], None]


def _subvencion(run: Run) -> None:
    run.fondos += 10


def _store_tag(run: Run) -> None:
    """Las de tienda las mira `market` mientras estén en `run.tags`."""


TAGS: dict[str, TagDef] = {
    t.key: t
    for t in (
        TagDef("subvencion", "Subvención", "💶", "+10 fondos.", True, _subvencion),
        TagDef(
            "indulto",
            "Indulto",
            "🕊️",
            "El primer asesor de la próxima tienda es gratis.",
            False,
            _store_tag,
        ),
        TagDef(
            "rebajas",
            "Rebajas de enero",
            "🏷️",
            "La próxima tienda, todo a mitad de precio.",
            False,
            _store_tag,
        ),
    )
}
