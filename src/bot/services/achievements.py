"""Logros: el catálogo, las rarezas y las reglas para desbloquearlos.

Lógica pura, sin Discord ni base de datos. Un logro se desbloquea cuando las
estadísticas de un miembro llegan a una meta (`Achievement.conditions`). Las
estadísticas son contadores con nombre (`messages`, `voice_minutes`,
`roulette_spins`…) que se guardan por servidor y miembro. Hay dos tipos:

- **Sumas** (`add`): se acumulan. Mensajes, minutos en voz, tiradas…
- **Máximos** (`peak`): se quedan con el mayor valor visto. Nivel, mayor
  premio de una jugada, racha más larga…

Quien juega o habla no toca esto directamente: los cogs calculan qué ha
pasado con las funciones de este módulo (`message_stats`, `roulette_stats`,
`blackjack_stats`, `slots_stats`, `hold_win_stats`, `hold_win_bonus_stats`,
`crash_stats`, `mines_stats`, `chicken_stats`, `coin_stats`, `bus_stats`, `craps_stats`,
`pachinko_stats`, `horses_stats`, `porra_open_stats`, `porra_bet_stats`,
`porra_bettor_stats`, `porra_subject_stats`,
`casino_stats`, `shop_stats`, `bizum_stats`, `message_delta`,
`voice_move_stats`, `music_queue_stats`, `image_stats`, `babel_stats`…) y se
lo pasan al cog de logros. Las risas escritas las reconoce `analyze_laugh`.

Los de 🏆 Coleccionista no tienen contadores propios: salen de los logros ya
conseguidos (`meta_stats`) cada vez que se evalúa.

Para añadir un logro basta con una línea en el catálogo (`_build_catalog`).
Si usa una estadística nueva, el juego que la produce tiene que sumarla.
Los `id` no se cambian nunca: son lo que se guarda en la base de datos.
"""

from __future__ import annotations

import math
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from fractions import Fraction
from typing import TYPE_CHECKING

from bot.services.autoplay import StopReason
from bot.services.bacara import BANKER_MIN_STAKE as BACCARAT_BANKER_MIN
from bot.services.bacara import Coup as BaccaratCoup
from bot.services.bacara import Result as BaccaratResult
from bot.services.bacara import Side as BaccaratSide
from bot.services.bacara import chop_run as baccarat_chop_run
from bot.services.bacara import commission as baccarat_commission
from bot.services.bacara import hand_points as baccarat_points
from bot.services.bacara import road_stats as baccarat_road_stats
from bot.services.beernight import Reason as BeerReason
from bot.services.bizum import MAX_DAILY as BIZUM_MAX_DAILY
from bot.services.bizum import MAX_OPERATION as BIZUM_MAX_OPERATION
from bot.services.bizum import MIN_AMOUNT as BIZUM_MIN_AMOUNT
from bot.services.blackjack import (
    BlackjackGame,
    Result,
    hand_total,
    is_blackjack,
)
from bot.services.bus import HANDS as BUS_HANDS
from bot.services.bus import BusGame
from bot.services.bus import Hand as BusHand
from bot.services.bus import Pick as BusPick
from bot.services.bus import Status as BusStatus
from bot.services.bus import chance as bus_chance
from bot.services.bus import picks_for as bus_picks_for
from bot.services.chicken import ChickenGame
from bot.services.chicken import Status as ChickenStatus
from bot.services.coin import MAX_FLIPS as COIN_MAX_FLIPS
from bot.services.coin import CoinGame
from bot.services.coin import Outcome as CoinOutcome
from bot.services.coin import Side as CoinSide
from bot.services.coin import Status as CoinStatus
from bot.services.craps import CRAPS as CRAPS_CRAPS
from bot.services.craps import NATURALS as CRAPS_NATURALS
from bot.services.craps import POINTS as CRAPS_POINTS
from bot.services.craps import Bet as CrapsBet
from bot.services.craps import CrapsGame, Hand
from bot.services.craps import Status as CrapsStatus
from bot.services.crash import Seat as CrashSeat
from bot.services.gallos import HOUSE as GALLOS_HOUSE
from bot.services.gallos import Corner as GallosCorner
from bot.services.gallos import Fight as GallosFight
from bot.services.gallos import Finish as GallosFinish
from bot.services.gallos import RoosterRecord
from bot.services.hold_win import BaseSpin as HoldWinSpin
from bot.services.hold_win import BonusResult as HoldWinBonusResult
from bot.services.hold_win import BonusStep as HoldWinStep
from bot.services.hold_win import Trigger as HoldWinTrigger
from bot.services.horses import DISTANCES as _HORSE_RACE_DISTANCES
from bot.services.horses import GRAND_PRIX_DISTANCE as _HORSE_GP_DISTANCE
from bot.services.horses import STABLE as _HORSE_STABLE
from bot.services.horses import BetKind as HorseBetKind
from bot.services.horses import Going as HorseGoing
from bot.services.horses import Pick as HorsePick
from bot.services.horses import RaceCard as HorseCard
from bot.services.horses import RaceResult as HorseResult
from bot.services.interest import (
    INTEREST_DAILY_MAX,
    INTEREST_TIERS,
    INTEREST_TOP,
    RESIST_BALANCE,
)
from bot.services.levels import TIMEZONE
from bot.services.lottery import MAX_PER_DRAW as MAX_LOTTERY_PER_DRAW
from bot.services.mines import MAX_MINES as MINES_MAX
from bot.services.mines import MinesGame, multiplier_cents
from bot.services.mines import Status as MinesStatus
from bot.services.pachinko import BOARDS as PACHINKO_BOARDS
from bot.services.pachinko import Kind as PachinkoKind
from bot.services.pachinko import Volley as PachinkoVolley
from bot.services.pachinko_motion import VolleyMotion as PachinkoMotion
from bot.services.pets_catalog import SPAWNING as PET_SPAWNING
from bot.services.pets_catalog import SPECIES as PET_SPECIES
from bot.services.porras import MIN_BET as PORRA_MIN_BET
from bot.services.porras import PROPOSITIONS as PORRA_PROPOSITIONS
from bot.services.porras import allowed_games as porra_allowed_games
from bot.services.roulette import DOUBLE_ZERO, ZEROS, RoundOutcome
from bot.services.shop_catalog import AISLES as SHOP_AISLES
from bot.services.shop_catalog import RETIRED_AISLES as SHOP_RETIRED_AISLES
from bot.services.shop_catalog import TITLE_KEYS as SHOP_TITLE_KEYS
from bot.services.shop_uses import USES as SHOP_USES
from bot.services.slots import HEAT_MAX as SLOT_HEAT_MAX
from bot.services.slots import WILD as SLOT_WILD
from bot.services.slots import Kind as SlotKind
from bot.services.slots import Spin
from bot.services.slots import WinTier as SlotWinTier
from bot.services.slots import pot_share as slots_pot_share
from bot.services.work import (
    FAMILY_TIERS,
    FLOP_BELOW,
    MAX_COFFEES,
    MEGAVIRAL,
    RATING_MAX,
    SELF_EMPLOYED_FEE,
    VIRAL_AT,
    WISHES,
    Mechanic,
)
from bot.services.work_tools import TOOLS as WORK_TOOLS

if TYPE_CHECKING:
    from bot.services.pala import ShiftOutcome

from bot.services.taxes import (
    LOTTERY_EXEMPT,
    STATE_PERSONAL_MINIMUM,
    TAX_COLLECTOR,
    YAPDOLLARS_PER_EURO,
)

# -- Rarezas ---------------------------------------------------------------------------


class Rarity(Enum):
    """Rareza de un logro: decide su premio, sus puntos y cómo se pinta.

    Los emojis son de formas distintas, no solo de colores, para que se
    distingan también con daltonismo.
    """

    COMMON = ("Común", "▫️", 50, 10, (170, 170, 170))
    RARE = ("Raro", "🔹", 200, 25, (70, 140, 255))
    EPIC = ("Épico", "💠", 750, 50, (160, 90, 255))
    LEGENDARY = ("Legendario", "🌟", 2_500, 100, (255, 190, 40))
    MYTHIC = ("Mítico", "👑", 10_000, 250, (255, 255, 255))

    def __init__(
        self, label: str, emoji: str, reward: int, points: int, rgb: tuple[int, int, int]
    ) -> None:
        self.label = label
        self.emoji = emoji
        #: Yapdollars brutos al desbloquearlo (pagan IRPF, ver el cog).
        self.reward = reward
        #: Puntos para el ranking de logros.
        self.points = points
        self.rgb = rgb


# -- Categorías ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Category:
    """Grupo de logros que se muestra junto en el menú de logros.

    Attributes:
        upcoming: El juego aún no existe. Sus logros se enseñan como
            "próximamente", no cuentan para el total ni se pueden desbloquear.
        group: Clave del grupo del menú en el que va (`CASINO_GROUP`), o
            `None` si sale sola en el menú. Las categorías de un grupo son
            secciones: el menú enseña el grupo una vez y, dentro, un segundo
            menú elige la sección.
    """

    key: str
    title: str
    upcoming: bool = False
    group: str | None = None


@dataclass(frozen=True, slots=True)
class Group:
    """Entrada del menú de logros que reúne varias categorías (secciones)."""

    key: str
    title: str


#: Todos los juegos del casino van juntos en una sola entrada del menú.
CASINO_GROUP = Group("casino_group", "🎰 Casino")
#: El chat (mensajes, risas, lengua, imágenes…) también, con una sección por tema.
CHAT_GROUP = Group("chat_group", "💬 Chat")
#: Y la voz: llamada, micro y cámara, entradas y música.
VOICE_GROUP = Group("voice_group", "🎙️ Voz")
GROUPS: dict[str, Group] = {g.key: g for g in (CHAT_GROUP, VOICE_GROUP, CASINO_GROUP)}

_CG = CASINO_GROUP.key
_CH = CHAT_GROUP.key
_VG = VOICE_GROUP.key
CATEGORIES: tuple[Category, ...] = (
    Category("chat", "💬 General", group=_CH),
    Category("style", "✍️ Estilo", group=_CH),
    Category("laughs", "😂 Risas", group=_CH),
    Category("funny", "🤡 Hacer reír", group=_CH),
    Category("lengua", "🗣️ Lengua y temas", group=_CH),
    Category("convo", "🧵 Conversación", group=_CH),
    Category("time", "🗓️ Horarios y fechas", group=_CH),
    Category("memes", "🖼️ Imágenes y Babel", group=_CH),
    Category("voice", "🎙️ Llamada", group=_VG),
    Category("voice_mic", "🎚️ Micro, cámara y AFK", group=_VG),
    Category("voice_moves", "🚪 Entradas y salidas", group=_VG),
    Category("music", "🎵 Música", group=_VG),
    Category("social", "❤️ Social"),
    Category("todo", "📝 Lista"),
    Category("beernight", "🍻 Beernight"),
    Category("levels", "📈 Niveles"),
    # 🎰 Casino: una entrada del menú con una sección por juego. El menú de
    # Discord admite 25 opciones y, con un juego por categoría, se llenaba.
    Category("casino", "💰 General", group=_CG),
    Category("roulette", "🎡 Ruleta", group=_CG),
    Category("blackjack", "🃏 Blackjack", group=_CG),
    Category("slots", "🎰 Tragaperras", group=_CG),
    Category("botes", "🌋 Botes", group=_CG),
    Category("crash", "🚀 Crash", group=_CG),
    Category("mines", "💣 Minas", group=_CG),
    Category("chicken", "🐔 Pollo", group=_CG),
    Category("coin", "🪙 Cara o cruz", group=_CG),
    Category("bus", "🚌 Autobús", group=_CG),
    Category("dice", "🎲 Dados", group=_CG),
    Category("pachinko", "🌸 Pachinko", group=_CG),
    Category("horses", "🏇 Caballos", group=_CG),
    Category("baccarat", "🍸 Bacará", group=_CG),
    Category("gallos", "🐓 Gallos", group=_CG),
    Category("porras", "🎫 Porras", group=_CG),
    Category("apuestas", "📊 Estadísticas", group=_CG),
    Category("lottery", "🎟️ Loterías"),
    Category("shop", "🛍️ Tienda"),
    Category("pets", "🐾 Mascotas"),
    Category("bizum", "🏦 Banco: Bizum y cuenta"),
    Category("economy", "🏛️ Economía y Hacienda"),
    Category("work", "🪏 Trabajo"),
    Category("jobs", "👷 Oficios"),
    Category("sanidad", "🏥 Sanidad"),
    Category("oficina", "💻 Oficina"),
    Category("reparto", "🛵 Reparto"),
    Category("funcionariado", "🗂️ Funcionariado"),
    Category("influencer", "🤳 Influencer"),
    Category("hongkong", "🇭🇰 Hong Kong"),
    Category("meta", "🏆 Coleccionista"),
)
CATEGORY_BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}


def menu_entries() -> list[tuple[str, str]]:
    """Opciones del menú de logros (sin el Resumen): `(clave, título)`.

    Cada categoría suelta sale tal cual; las de un grupo salen una sola vez,
    con la clave y el título del grupo, en la posición de la primera.
    """
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for category in CATEGORIES:
        if category.group is None:
            entries.append((category.key, category.title))
        elif category.group not in seen:
            seen.add(category.group)
            group = GROUPS[category.group]
            entries.append((group.key, group.title))
    return entries


def group_sections(group_key: str) -> list[Category]:
    """Categorías (secciones) de un grupo, en el orden del catálogo."""
    return [c for c in CATEGORIES if c.group == group_key]


# -- Logros ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Achievement:
    """Un logro del catálogo.

    Attributes:
        id: Identificador estable; es lo que se guarda al desbloquearlo.
        conditions: Pares `(estadística, meta)`; hacen falta todos.
        unit: Cómo se muestra el progreso (`"min"` pasa minutos a horas,
            `"money"` formatea yapdollars).
        secret: Se muestra como `???` hasta que alguien lo desbloquea.
        story: Texto largo que acompaña al aviso de desbloqueo. Convierte el
            logro en un gancho de "la primera vez que…": como cada logro se
            desbloquea una sola vez por miembro, el texto sale una sola vez.
    """

    id: str
    name: str
    description: str
    category: str
    rarity: Rarity
    conditions: tuple[tuple[str, int], ...]
    unit: str = ""
    secret: bool = False
    story: str | None = None

    @property
    def stat(self) -> str:
        """Estadística principal (la que mide el progreso)."""
        return self.conditions[0][0]

    @property
    def goal(self) -> int:
        """Meta de la estadística principal."""
        return self.conditions[0][1]

    @property
    def upcoming(self) -> bool:
        """Si pertenece a un juego que aún no existe."""
        return CATEGORY_BY_KEY[self.category].upcoming


#: Estadística virtual con el número de logros normales desbloqueados (los de
#: Coleccionista no cuentan, para que no se desbloqueen unos a otros).
UNLOCKED_STAT = "achievements"
#: Estadística virtual: mensajes contados en vivo más los del historial importado.
MESSAGES_TOTAL_STAT = "messages_total"
#: Prefijo de los contadores de cada efecto de imagen (`img_fx_magik`, `img_fx_gay`…).
IMG_EFFECT_PREFIX = "img_fx_"
#: Estadística virtual: efectos de imagen distintos que ha usado el miembro.
IMG_EFFECTS_STAT = "img_effects_tried"
#: Secciones de `perfil` vistas: un contador por sección con este prefijo
#: (`perfil_seen_nivel`…) y el recuento de distintas, calculado en `with_derived`.
PERFIL_SEEN_PREFIX = "perfil_seen_"
PERFIL_SECTIONS_STAT = "perfil_sections"
#: Secciones de `perfil` (las mismas claves que `bot.cogs.perfil.SECTIONS`).
PERFIL_SECTIONS = (
    "resumen",
    "nivel",
    "patrimonio",
    "trabajo",
    "logros",
    "rachas",
    "objetos",
    "gallo",
)
#: Apuestas exactas con logro secreto propio (`casino_bet_<cifra>`), en cualquier juego.
SECRET_STAKES = (1, 23, 33, 44, 47, 69, 87, 176, 600, 777, 1975, 1978)
#: Prefijo de los plenos acertados en cada número de la ruleta (`roulette_hit_17`).
ROULETTE_HIT_PREFIX = "roulette_hit_"
#: Estadísticas virtuales: números distintos acertados a pleno y el más repetido.
ROULETTE_NUMBERS_STAT = "roulette_numbers_hit"
ROULETTE_FAVOURITE_STAT = "roulette_hit_max"
#: Tipos de risa que distingue `analyze_laugh`. Cada uno suma `laugh_<tipo>`.
LAUGH_KINDS = ("es", "en", "emoji", "skull", "smash", "intl", "phrase", "xd")
#: Prefijo de los usos de cada objeto de la tienda (`shop_used_huevo`).
SHOP_USED_PREFIX = "shop_used_"
#: Estadística virtual: objetos distintos (por tipo de uso) que ha usado el miembro.
SHOP_USE_KINDS_STAT = "shop_use_kinds"
#: Prefijo de las compras por pasillo del colmado (`shop_aisle_moncloa`).
SHOP_AISLE_PREFIX = "shop_aisle_"
#: Estadística virtual: pasillos del surtido de serie en los que ha comprado.
SHOP_AISLES_STAT = "shop_aisles"
_SHOP_AISLE_KEYS = frozenset(a.key for a in SHOP_AISLES)
#: Prefijo de las especies de mascota conseguidas (`pet_species_gato`).
PET_SPECIES_PREFIX = "pet_species_"
#: Estadísticas virtuales: especies distintas y, de ellas, las que aparecen solas.
PET_SPECIES_STAT = "pet_species_count"
PET_SPAWN_KINDS_STAT = "pet_spawn_kinds"
_PET_KEYS = frozenset(s.key for s in PET_SPECIES)
_PET_SPAWN_KEYS = frozenset(s.key for s in PET_SPAWNING)
#: Artículos de serie con logro propio al comprarlos; cada uno suma `shop_key_<clave>`.
#: Solo estos, para no llenar las estadísticas con un contador por artículo.
SHOP_TRACKED_KEYS = frozenset(
    {
        "falcon",
        "aire_moncloa",
        "nada",
        "piedra",
        "manual_resistencia",
        "platano_cinta",
        "nft_mono",
        "escano",
        "asesor",
        "recibo_luz",
        "lingote",
        "patata_espana",
        "caca_oro",
        "yate",
        "verificado",
        "master_express",
        "doctorado",
        "aforamiento",
        "puerta_giratoria",
        "condado_teide",
        "marquesado_mogan",
        "grandeza",
        "palco_bernabeu",
        "porsche",
        "skin_tapete_oro",
        "skin_tapete_espana",
        "skin_tapete_israel",
        "skin_tapete_canarias",
        "skin_tapete_boricua",
    }
)
#: Usos de la tienda que ensucian a quien los recibe, y los que son de cariño.
SHOP_MESSY_USES = frozenset({"huevo", "tomate", "tarta", "globo", "gofio", "mojo"})
SHOP_LOVE_USES = frozenset(
    {"ramo", "abrazo", "carta", "barraquito", "perreo", "dimsum", "sobre_rojo"}
)

#: Prefijos de las porras montadas por juego y por propuesta (`porra_game_minas`).
PORRA_GAME_PREFIX = "porra_game_"
PORRA_PROP_PREFIX = "porra_prop_"
PORRA_GAMES = tuple(porra_allowed_games())
PORRA_PROPS = tuple(prop.key for prop in PORRA_PROPOSITIONS)

_R = Rarity
C, R, E, L, M = _R.COMMON, _R.RARE, _R.EPIC, _R.LEGENDARY, _R.MYTHIC


def _tiers(category: str, stat: str, rows: Iterable[tuple], *, unit: str = "") -> list[Achievement]:
    """Crea los logros escalonados de una estadística.

    Cada fila es `(meta, id, nombre, descripción, rareza)` y, opcionalmente,
    `True` al final si es secreto. Van de menor a mayor meta.
    """
    built = []
    for row in rows:
        goal, achievement_id, name, description, rarity, *rest = row
        built.append(
            Achievement(
                id=achievement_id,
                name=name,
                description=description,
                category=category,
                rarity=rarity,
                conditions=((stat, goal),),
                unit=unit,
                secret=bool(rest and rest[0]),
            )
        )
    return built


#: Renta anual en Y$ a partir de la cual empieza la retención: el mínimo
#: personal estatal (art. 57 LIRPF) al cambio del juego. El autonómico de
#: Canarias es algo mayor, así que la primera mordida siempre es la estatal.
FIRST_TAX_YEARLY = int(STATE_PERSONAL_MINIMUM * YAPDOLLARS_PER_EURO)
#: Lo mismo en la ventana de 7 días que usa la retención (`compute_withholding`).
FIRST_TAX_WEEKLY = FIRST_TAX_YEARLY * 7 // 365


#: Tipos de vehículo del Pollo (`chicken_render.VEHICLES`), uno por logro de atropello.
CHICKEN_VEHICLE_KINDS = ("car", "van", "truck", "bus", "moto", "taxi")

#: Prefijo de los aciertos del Autobús por opción (`bus_win_igual`, `bus_win_picas`…).
BUS_WIN_PREFIX = "bus_win_"
#: Una mano del Autobús es «cuota larga» con esta probabilidad o menos (2 de 13 alturas).
BUS_LONGSHOT = Fraction(2, 13)
#: Fallar con esta probabilidad o más a favor es «lo tenías hecho» (11 de 13 alturas).
BUS_SURE = Fraction(11, 13)
#: Desde este multiplicador en juego, perder es perder a lo grande (×20).
BUS_LOST_BIG = 20

#: Tipos de boleto de las carreras (`horses.BetKind`), uno por condición de «Quiniela completa».
HORSE_BET_KINDS = tuple(kind.key for kind in HorseBetKind)
#: Caballos del establo y distancias de las carreras, para las colecciones.
HORSE_KEYS = frozenset(h.key for h in _HORSE_STABLE)
HORSE_DISTANCES = (*_HORSE_RACE_DISTANCES, _HORSE_GP_DISTANCE)
#: Boletos por caballo (`horse_backed_falcon`…); el recuento se calcula en `with_derived`.
HORSE_BACKED_PREFIX = "horse_backed_"
#: Caballos distintos por los que ha apostado y boletos al caballo más repetido.
HORSE_BACKED_KINDS_STAT = "horse_backed_kinds"
HORSE_BACKED_MAX_STAT = "horse_backed_max"


#: Prefijo de los sorbos por motivo (`beer_kind_chivatazo`), para «He bebido por todo».
BEER_KIND_PREFIX = "beer_kind_"

#: Discurso del primer logro de la beernight: explica cómo se juega.
BEERNIGHT_STORY = (
    "Bienvenido a la beernight. Las normas: los mandamientos dicen quién bebe; quien cae lo "
    "confiesa con 🍺 y, si no, alguien se chiva con 🚨 y otro lo confirma. Si el chivatazo "
    "es mentira, bebe el chivato. Cada pocos minutos el bot lanza un evento y los "
    "mandamientos cambian solos. Nadie está obligado a nada y cada uno bebe lo que tenga. "
    f"{TAX_COLLECTOR} todavía no ha encontrado la forma de cobrar por sorbo. Todavía."
)


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


#: Discurso del logro `tax_first`: el aviso de la primera retención. No se
#: dice al entrar al servidor; salta la primera vez que alguien gana lo
#: bastante para pagar, venga de donde venga el dinero (casino, niveles,
#: premios de logros), porque todos esos caminos suman `tax_paid`.
FIRST_TAX_STORY = (
    f"🐶 **¡Ay, bendito! {TAX_COLLECTOR} te encontró.** Hasta hoy cobrabas limpito "
    f"porque no llegabas al mínimo personal: {_thousands(FIRST_TAX_YEARLY)} Y$ al año, "
    f"unos {_thousands(FIRST_TAX_WEEKLY)} Y$ a la semana, sumando todo lo que cobras. "
    "Te pasaste, mi amor, y desde hoy cada premio, cada nivel, cada nómina y cada "
    "pelotazo del casino pasa antes por su cartera. Cuanto más ganas, más se lleva.\n"
    "Lo que el casino te retenga de más te lo devuelve en la renta del lunes, si te "
    "acuerdas de presentarla (`renta`). Bienvenido a España: aquí hasta el café paga."
)


#: Discurso del logro `pet_1`: la primera mascota.
ADOPTION_STORY = (
    "🐾 **Tu primera mascota.** Si es un perro, un gato o un hurón, el colmado no te lo "
    "ha vendido: desde la Ley 7/2023 de bienestar animal (art. 56) las tiendas no pueden "
    "venderlos, solo tenerlos en adopción con una protectora. Lo que has pagado es la tasa "
    f"de adopción, con su IGIC al tipo general, porque {TAX_COLLECTOR} no perdona ni a los "
    "animales. Cuídala cada día con `mascota`: el vínculo nunca baja, y si la llevas "
    "contigo saldrá en tus jugadas a opinar."
)


#: Discurso del logro `gravamen`: el primer premio de lotería que paga impuestos.
LOTTERY_TAX_STORY = (
    f"🐶 **{TAX_COLLECTOR} también juega a la lotería, pero sin comprar décimo.** Los "
    f"premios hasta {_thousands(LOTTERY_EXEMPT)} Y$ por décimo o apuesta están exentos; "
    "de ahí para arriba se queda el 20 %, y te lo quita antes de pagarte "
    "(disposición adicional 33ª de la Ley del IRPF). No va a la renta ni se "
    "devuelve: es definitivo. Con lo que te queda, mi amor, ya puedes invitar."
)


#: Discurso del logro `bizum_espaldas`: el primer Bizum por encima del máximo.
BIZUM_LIMIT_STORY = (
    f"🤫 **Eso no lo ha visto nadie, ¿verdad, mi amor?** En España un Bizum no pasa de "
    f"1.000 € por operación ({_thousands(BIZUM_MAX_OPERATION)} Y$) ni de 2.000 € al día; "
    "el banco te lo para en seco. Aquí te ha colado porque Perro Sanxe estaba mirando "
    "despegar el Falcon. Si alguien pregunta, era para el cumple de tu prima."
)

#: Discurso del primer logro de los gallos (`gallos_1`).
GALLOS_STORY = (
    "🐓 **Bienvenido al reñidero, mi amor.** En casi toda España las peleas de gallos "
    "están prohibidas, pero la ley canaria de protección animal de 1991 las sigue "
    "permitiendo allí donde son tradición. Aquí son de dibujos animados: ningún gallo "
    "sufre, solo tu cartera. Y lo que ganes, Perro Sanxe lo mira con lupa: tributa "
    "como cualquier juego del casino."
)
#: Rivales de la casa ganados: un contador por rival (`gallos_beat_falcon`).
GALLOS_BEAT_PREFIX = "gallos_beat_"
#: Skins con las que ha peleado: un contador por skin (`gallos_skin_serie`).
GALLOS_SKIN_PREFIX = "gallos_skin_"
#: Las skins gratis de los gallos (claves de `bot.services.skins`).
GALLOS_FREE_SKINS = ("gallo_blanco", "gallo_negro", "gallo_pinto")


#: Discurso del logro `paguita`: rechazar un ascenso.
DECLINE_STORY = (
    "🪑 **Has rechazado un ascenso.** En la vida real hay gente que lo hace a propósito: "
    "si cobrar más te quita una ayuda entera, acabas igual o peor. Es la «trampa de la "
    "pobreza», y por eso existe el incentivo al empleo del IMV (RD 789/2022): lo que "
    "ganas trabajando solo te quita una parte. Aquí, cada Y$ de más te quita medio de "
    "IMV, así que ascender siempre compensa. Pero tú sabrás, mi amor."
)

#: Prefijo de los deseos del día cumplidos por clave (`work_wish_nota_90`…).
WISH_STAT_PREFIX = "work_wish_"
#: Deseos que pueden salir en cualquier curro (para «Lo he deseado todo»).
GENERIC_WISHES = tuple(wish.key for wish in WISHES if wish.fits is None)

#: Discurso del logro `pension_irpf`: la primera pensión con retención.
PENSION_STORY = (
    "🏖️ **Jubilado, pero no de Hacienda.** Las pensiones son rendimientos del trabajo "
    "(art. 17.2.a LIRPF): tributan en el IRPF como un sueldo, con su retención y su "
    "reducción del art. 20. Lo único que te ahorras es cotizar a la Seguridad Social, "
    "que ya cotizaste bastante. Perro Sanxe no se jubila nunca."
)

#: Discurso del logro `rama_1`: elegir rama en un oficio.
BRANCH_STORY = (
    "🔀 **Has elegido rama.** Como en los Sims: el tronco es igual para todos y al final "
    "toca decidir. Cada rama tiene sus puestos, sus minijuegos, sus marrones y su "
    "pensión. Si llegas a lo más alto, te puedes jubilar de ella y empezar la otra "
    "desde abajo, con la pensión de la primera en el bolsillo."
)

#: Discurso del logro `ochenta_horas`: pasar del límite legal de horas extra.
OVERTIME_STORY = (
    "⏰ **El art. 35.2 del Estatuto de los Trabajadores dice que las horas extra no "
    "pueden pasar de 80 al año.** Aquí son dos turnos extra por semana. Tú ya vas por "
    "encima, así que tu jefe te las ofrece en B: sin IRPF, sin cotizar y sin que lo vea "
    "el IMV. Si viene la Inspección, devuelves todo con un 20 % de recargo. Wepa."
)

#: Discurso del logro `primera_nomina`.
FIRST_PAYSLIP_STORY = (
    "📄 **Tu primera nómina.** Arriba, el bruto. Luego te quitan la Seguridad Social "
    "(6,5 %: pensiones, paro, formación y el MEI) y la retención de IRPF, que sale de "
    "proyectar lo que cobras al año. Abajo, lo que te llega. Y en letra pequeña, lo que "
    "paga la empresa por ti: otro 32 % que no ves nunca. Todo eso, para Perro Sanxe."
)

#: Discurso del logro `tramo`.
BRACKET_STORY = (
    "📊 **«Me suben de tramo y gano menos».** Mentira, y de las gordas. El IRPF es "
    "progresivo por tramos: solo lo que pasa de cada escalón paga el tipo nuevo, no todo "
    "el sueldo. Ganar un yapdólar más nunca te deja con menos neto. Lo que sí pasa es que "
    "la retención sube, y eso duele igual. Bienvenido a la clase media alta."
)

#: Discurso del logro `socio_hacienda`.
PARTNER_STORY = (
    "🤝 **Esta semana has pagado más impuestos que lo que te ha llegado.** Cuenta la "
    "Seguridad Social tuya y la de la empresa, el IRPF, el IGIC de lo que compras y el "
    "Patrimonio. Es la cuenta que hace la gente cuando dice que trabaja medio año para "
    "Hacienda, y aquí te ha salido más de medio. Perro Sanxe te considera de la familia."
)


#: Discurso del logro `guardia_1`: la primera guardia.
GUARD_STORY = (
    "🚑 **Tu primera guardia.** Las guardias no son horas extra: en el Estatuto Marco "
    "(Ley 55/2003) son jornada complementaria, así que no tienen tope ni se pagan en B. Te "
    "pagan 1,6 veces la base por el doble de trabajo: la hora de guardia sale más barata "
    "que la normal, como en muchos hospitales de verdad. Pero en sanidad la barra solo sube "
    "con guardias. Bienvenido, y ahora a dormir: estás saliente."
)

#: Discurso del logro `tarifa_plana`: la primera cuota de falso autónomo.
FLAT_FEE_STORY = (
    "🧾 **Tarifa plana.** Los nuevos autónomos pagan 80 € al mes de cuota durante el primer "
    "año (art. 38 ter de la Ley 20/2007, del Trabajo Autónomo). Tú no eres autónomo de "
    "verdad: no eliges precio, ni horario, ni cliente. Eso tiene nombre, falso autónomo, y "
    "desde la Ley 12/2021 (la «Ley Rider») a los repartidores se les presume contrato. Pero "
    "eso, para el puesto 3."
)

#: Discurso del logro `renta_especie`: declarar el regalo de una marca.
GIFT_STORY = (
    "🎁 **Renta en especie.** Lo que te da una marca a cambio de enseñarlo no es un regalo: "
    "es el pago de tu actividad, aunque llegue en una caja, y tributa en el IRPF por su "
    "valor de mercado. Hacienda lleva años mirando a los influencers con lupa. Has hecho bien."
)

#: Discurso del logro `no_residente`: dejar de ser residente fiscal en España.
NONRESIDENT_STORY = (
    "✈️ **Ya no eres residente fiscal en España.** Quien pasa más de 183 días del año "
    "fuera deja de serlo (art. 9.1.a LIRPF), salvo que su familia o sus intereses "
    "económicos sigan aquí (art. 9.1.b). Desde hoy tu sueldo de Hong Kong solo paga allí. "
    "Perro Sanxe mira tu nómina como quien mira un barco que se va. Ojo: si sigues "
    "comprando en el chiringuito, a lo mejor te escribe."
)

#: Discurso del logro `beckham`.
BECKHAM_STORY = (
    "⚽ **Ley Beckham.** El régimen de impatriados (art. 93 LIRPF) es para quien se muda a "
    "España tras 5 años sin residir aquí: durante el año de la llegada y los 5 siguientes, "
    "su sueldo tributa al 24 % fijo hasta 600.000 € en vez de por la escala. Se llama así "
    "porque llegó con Beckham al Real Madrid en 2003. Tú no juegas al fútbol, pero "
    "tributas como si sí."
)


#: Discurso de «Sanxe cobra antes que tú»: la primera retención de los intereses.
INTEREST_TAX_STORY = (
    "El banco te ha pagado intereses y, antes de que los vieras, Perro Sanxe ya se había "
    "quedado el 19 %. Es la retención a cuenta de los rendimientos del capital mobiliario "
    "(art. 101.4 LIRPF): el banco se la quita y se la manda a Hacienda por ti. Cada lunes la "
    "semana se liquida con la escala del ahorro y, si te toca más, te cobra la diferencia. "
    "Devolver, no te devuelve nada: el 19 % es el tramo más bajo."
)

#: Discurso de «Me suben de tramo del ahorro»: el mito del tramo, versión ahorro.
SAVINGS_BRACKET_STORY = (
    "Tus intereses de la semana, proyectados a un año, pasan de 6.000 € y entras en el tramo "
    "del 21 % de la base del ahorro (arts. 66.1 y 76 LIRPF). Tranquilo: el 21 % solo se aplica "
    "a lo que pasa de ese límite, el resto sigue al 19 %. Subir de tramo nunca te deja con "
    "menos dinero. Eso sí, Sanxe te lo cobra el lunes sin preguntar."
)


#: Páginas de `apuestas` (las claves de su menú). Ver `bot.cogs.apuestas`.
#: Discurso del logro `porra_iaj_1`: el primer IAJ pagado en una porra.
IAJ_STORY = (
    f"🎫 **{TAX_COLLECTOR} no apuesta, pero siempre gana.** Las porras son apuestas "
    "cruzadas entre jugadores (art. 3.c de la Ley 13/2011) y el operador paga el "
    "Impuesto sobre Actividades de Juego por lo que se queda: en la vida real, el 20 % "
    "de sus ingresos netos (art. 48). Aquí el operador no existe, así que el 10 % de "
    "cada apuesta va directo al Estado. Hayas acertado o no."
)

#: Discurso del logro `porra_image_1`: los primeros derechos de imagen cobrados.
IMAGE_STORY = (
    "📸 **Tu cara vale dinero, mi amor.** Por prestar tu imagen a una porra cobras el 2 "
    "% del bote. Para Hacienda es un rendimiento del capital mobiliario por ceder el "
    "derecho de imagen (art. 25.4.d de la Ley del IRPF) y la retención es fija: el 24 %, "
    f"como a los futbolistas (art. 101). {TAX_COLLECTOR} ya tiene tu foto."
)

APUESTAS_PAGES = ("resumen", "juegos", "records", "horario", "ranking", "hacienda", "libro")
#: Periodos de `apuestas` (`bot.services.casino_stats.Period`).
APUESTAS_PERIODS = ("hoy", "semana", "mes", "siempre")


#: Tiradas o menos para llenar la barra de bonus «exprés».
SLOTS_BONUS_QUICK = 60


def _build_catalog() -> tuple[Achievement, ...]:
    a: list[Achievement] = []

    # 💬 Chat -------------------------------------------------------------------------
    a += _tiers("chat", MESSAGES_TOTAL_STAT, [
        (1, "chat_1", "Rompiendo el hielo", "Escribe tu primer mensaje.", C),
        (100, "chat_100", "Ya se te oye, como a Ayuso", "Escribe 100 mensajes.", C),
        (500, "chat_500", "Tertuliano", "Escribe 500 mensajes.", C),
        (1_000, "chat_1k", "Cotorra de tertulia de La Sexta", "Escribe 1.000 mensajes.", R),
        (5_000, "chat_5k", "Sin filtro, como Trump en Truth Social", "Escribe 5.000 mensajes.", R),
        (10_000, "chat_10k", "Yapper certificado", "Escribe 10.000 mensajes.", E),
        (25_000, "chat_25k", "Teclado en llamas", "Escribe 25.000 mensajes.", E),
        (50_000, "chat_50k", "¿Tú no trabajas?", "Escribe 50.000 mensajes.", L),
        (100_000, "chat_100k", "Ruido de fondo del Congreso", "Escribe 100.000 mensajes.", L),
        (250_000, "chat_250k", "Yapper supremo", "Escribe 250.000 mensajes.", M),
    ])  # fmt: skip
    a += _tiers("chat", "msg_replies", [
        (50, "reply_50", "Conversador de sobremesa", "Responde a 50 mensajes.", C),
        (500, "reply_500", "Hilo infinito, como el juicio de Netanyahu",
         "Responde a 500 mensajes.", R),
        (5_000, "reply_5k", "Tertulia de bar", "Responde a 5.000 mensajes.", E),
    ])  # fmt: skip
    a += _tiers("chat", "msg_questions", [
        (50, "ask_50", "Curioso",
         "Haz 50 preguntas (mensajes que acaban en ?). Más que la oposición en una sesión de "
         "control.", C),
        (500, "ask_500", "Pregúntale a Google", "Haz 500 preguntas.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_mentions", [
        (50, "ping_50", "Oye, tú, ¿por qué no te callas?",
         "Menciona a alguien en 50 mensajes. El Rey Juan Carlos ya lo hizo antes.", C),
        (500, "ping_500", "Pesado como un comercial de Iberdrola",
         "Menciona a alguien en 500 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_attachments", [
        (10, "pic_10", "Paparazzi del ¡Hola!", "Sube 10 archivos o imágenes.", C),
        (100, "pic_100", "Galería de arte del Prado", "Sube 100 archivos o imágenes.", R),
        (1_000, "pic_1k", "Archivo nacional", "Sube 1.000 archivos o imágenes.", E),
    ])  # fmt: skip
    a += _tiers("chat", "msg_links", [
        (25, "link_25", "Cartero de Correos, que llega cuando quiere", "Comparte 25 enlaces.", C),
        (250, "link_250", "Agregador de noticias de Telegram", "Comparte 250 enlaces.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_stickers", [
        (10, "sticker_10", "Pegatinero de cromos de la Liga", "Manda 10 stickers.", C),
        (100, "sticker_100", "Álbum completo", "Manda 100 stickers.", R),
    ])  # fmt: skip
    a += _tiers("style", "msg_long", [
        (1, "long_1", "Me explayo como Fidel en un discurso",
         "Escribe un mensaje de 600 caracteres o más.", C),
        (25, "long_25", "Escribes biblias", "Escribe 25 mensajes de 600 caracteres o más.", R),
        (100, "long_100", "Premio Planeta", "Escribe 100 mensajes de 600 caracteres o más.", E),
    ])  # fmt: skip
    a += _tiers("style", "msg_short", [
        (100, "short_100", "Monosílabo de Rajoy",
         "Manda 100 mensajes de 3 caracteres o menos. «Sí… no… es el vecino».", C),
        (1_000, "short_1k", "k.", "Manda 1.000 mensajes de 3 caracteres o menos.", R),
    ])  # fmt: skip
    a += _tiers("style", "msg_caps", [
        (10, "caps_10", "NO GRITES, QUE NO ERES TRUMP",
         "Escribe 10 mensajes TODO EN MAYÚSCULAS.", C),
        (100, "caps_100", "BLOQ MAYÚS ROTO", "Escribe 100 mensajes TODO EN MAYÚSCULAS.", R),
    ])  # fmt: skip

    a += _tiers("chat", MESSAGES_TOTAL_STAT, [
        (500_000, "chat_500k", "Más páginas que el BOE", "Escribe 500.000 mensajes.", M, True),
    ])  # fmt: skip
    a += _tiers("style", "msg_caps", [
        (1_000, "caps_1k", "MEGÁFONO DE MANIFESTACIÓN", "Escribe 1.000 mensajes en mayúsculas.", E),
    ])  # fmt: skip
    a += _tiers("chat", "msg_questions", [
        (5_000, "ask_5k", "¿Y tú qué opinas?", "Haz 5.000 preguntas.", E),
    ])  # fmt: skip
    a += _tiers("chat", "msg_links", [
        (2_500, "link_2500", "Hemeroteca digital", "Comparte 2.500 enlaces.", E),
    ])  # fmt: skip
    a += _tiers("chat", "msg_stickers", [
        (1_000, "sticker_1k", "Coleccionista de cromos", "Manda 1.000 stickers.", E),
    ])  # fmt: skip
    a += _tiers("style", "msg_rae", [
        (10, "rae_10", "Abre interrogación",
         "Empieza 10 mensajes con ¿ o ¡, como manda la RAE.", C),
        (100, "rae_100", "Sillón de la RAE", "Empieza 100 mensajes con ¿ o ¡.", R),
        (1_000, "rae_1k", "Académico de número", "Empieza 1.000 mensajes con ¿ o ¡.", E),
    ])  # fmt: skip
    a += _tiers("style", "msg_exclaim", [
        (25, "exclaim_25", "¡¡¡Enfático como Milei!!!", "Escribe «!!!» en 25 mensajes.", C),
        (250, "exclaim_250", "Telepredicador", "Escribe «!!!» en 250 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_everyone", [
        (1, "everyone_1", "Megafonía de Renfe",
         "Menciona a @everyone o @here. El tren de las 8:15 lleva retraso.", C, True),
        (10, "everyone_10", "Presidente de la comunidad de vecinos",
         "Menciona a @everyone o @here 10 veces.", R),
    ])  # fmt: skip
    a += _tiers("chat", "msg_mass_ping", [
        (1, "mass_ping_1", "Convocatoria de huelga general",
         "Menciona a 5 personas o más en un mismo mensaje.", R),
        (10, "mass_ping_10", "Liberado sindical del chat",
         "Convoca a 5 personas o más en 10 mensajes.", E),
    ])  # fmt: skip
    a += _tiers("style", "msg_spoiler", [
        (10, "spoiler_10", "Sin spoilers", "Esconde algo con ||spoiler|| en 10 mensajes.", C),
        (100, "spoiler_100", "Secreto oficial", "Usa ||spoiler|| en 100 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("style", "msg_code", [
        (10, "code_10", "Hackerman de Pegasus", "Escribe código (`así`) en 10 mensajes.", C),
        (100, "code_100", "Copiado de Stack Overflow", "Escribe código en 100 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("style", "msg_emoji_heavy", [
        (10, "emoji_heavy_10", "Jeroglífico de la Agencia Tributaria",
         "Mete 5 emojis o más en 10 mensajes.", C),
        (100, "emoji_heavy_100", "Piedra Rosetta", "Mete 5 emojis o más en 100 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("style", "msg_only_emoji", [
        (50, "only_emoji_50", "Hablo en emoji, como Trump en mayúsculas",
         "Manda 50 mensajes hechos solo de emojis.", C),
        (500, "only_emoji_500", "Generación 🗿", "Manda 500 mensajes solo de emojis.", R),
    ])  # fmt: skip
    a += _tiers("style", "msg_stretch", [
        (10, "stretch_10", "Holaaaaaaa",
         "Repite una letra 6 veces seguidas en 10 mensajes. ¡Azul, azuuul, como el mar azuuul!", C),
        (100, "stretch_100", "Alargador de vocaleeeees", "Estira letras en 100 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("style", "msg_long_word", [
        (1, "long_word", "Esternocleidomastoideo",
         "Escribe una palabra de 20 letras o más. Más larga que el nombre oficial "
         "de Corea del Norte.", C, True),
    ])  # fmt: skip
    a += _tiers("style", "msg_palindrome", [
        (1, "palindrome", "Dábale arroz a la zorra el abad",
         "Escribe un mensaje que se lea igual al revés (9 letras o más).", E, True),
    ])  # fmt: skip
    a += _tiers("style", "msg_nice", [
        (1, "nice", "Nice", "Escribe el número 69. Nice.", C, True),
    ])  # fmt: skip

    # 😂 Risas ----------------------------------------------------------------------------
    a += _tiers("laughs", "msg_laughs", [
        (1, "laugh_1", "Primera sonrisa, como la de Yolanda",
         "Ríete por escrito por primera vez.", C),
        (10, "laugh_10", "Risa floja de Kamala", "Ríete en 10 mensajes.", C),
        (100, "laugh_100", "Jajajaja", "Ríete (jaja, jsjs, lol, 😂…) en 100 mensajes.", C),
        (1_000, "laugh_1k", "Risa enlatada del NO-DO", "Ríete en 1.000 mensajes.", E),
        (5_000, "laugh_5k", "Joker de Gotham y de Galapagar", "Ríete en 5.000 mensajes.", L),
        (10_000, "laugh_10k", "Hiena profesional", "Ríete en 10.000 mensajes.", M),
        (25_000, "laugh_25k", "Hiena alfa", "Ríete en 25.000 mensajes. ¿Estás bien?", M),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_es", [
        (500, "laugh_es_500", "Jajajólogo", "Ríete a la española (jaja, jsjs, ajaj) 500 veces.", R),
        (5_000, "laugh_es_5k", "Catedrático del jajaja", "Ríete a la española 5.000 veces.", E),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_en", [
        (50, "laugh_en_50", "Anglicismo risueño de Ana Botella",
         "Ríete en inglés (haha, lol, lmao) 50 veces. A relaxing laugh in Plaza Mayor.", C),
        (500, "laugh_en_500", "LMAO certificado", "Ríete en inglés 500 veces.", E),
    ])  # fmt: skip
    a += _tiers("laughs", "msg_xd", [
        (100, "xd_100", "xd", "Escribe «xd» en 100 mensajes.", C),
        (1_000, "xd_1k", "XDDDDDD", "Escribe «xd» en 1.000 mensajes.", R),
        (10_000, "xd_10k", "Fósil de 2012", "Escribe «xd» en 10.000 mensajes.", L),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_emoji", [
        (25, "laugh_emoji_25", "Lágrima fácil, como Arias Navarro en la tele",
         "Ríete con 😂, 🤣 o un emoji de risa 25 veces.", C),
        (500, "laugh_emoji_500", "Emoji del año 2015", "Ríete con emojis 500 veces.", R),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_skull", [
        (10, "laugh_skull_10", "Me morí 💀", "Mándale un 💀 a un chiste 10 veces.", C),
        (200, "laugh_skull_200", "Cementerio de chistes", "Usa 💀 o ☠️ en 200 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_smash", [
        (10, "laugh_smash_10", "Teclado aporreado", "Aporrea el teclado (ajsjsjs) 10 veces.", C),
        (100, "laugh_smash_100", "asdfghjklñ", "Aporrea el teclado 100 veces. Cómprate otro.", R),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_intl", [
        (1, "laugh_intl_1", "Risa sin fronteras",
         "Ríete en otro idioma: kkkk, rsrs, mdr, wwww, ㅋㅋ, 哈哈, хаха…", R, True),
        (50, "laugh_intl_50", "Erasmus de la risa", "Ríete en otros idiomas 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_phrase", [
        (10, "laugh_phrase_10", "Me meo",
         "Ríete con palabras (me meo, me parto, lloro) 10 veces.", C),
        (100, "laugh_phrase_100", "Descojonado", "Ríete con palabras 100 veces.", R),
    ])  # fmt: skip
    a += _tiers("laughs", "msg_laugh_caps", [
        (10, "laugh_caps_10", "CARCAJADA DE MITIN", "Ríete EN MAYÚSCULAS en 10 mensajes.", C),
        (100, "laugh_caps_100", "TERREMOTO DE RISA", "Ríete en mayúsculas en 100 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("laughs", "msg_laugh_dry", [
        (10, "laugh_dry_10", "Ja.", "Responde «ja.» 10 veces. Risa de funcionario a las 14:59.", C),
        (100, "laugh_dry_100", "Me río por compromiso", "Ríete en seco («jaja.») 100 veces.", E),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_night", [
        (25, "laugh_night_25", "Hiena nocturna",
         "Ríete en 25 mensajes entre las 2:00 y las 6:00.", R),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_sanxe", [
        (1, "laugh_sanxe", "Reírse de Hacienda (mientras puedas)",
         "Ríete en un mensaje que nombre a Hacienda o a Perro Sanxe.", R, True),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_len_max", [
        (20, "laugh_len_20", "Ataque de risa", "Escribe una risa de 20 letras o más.", C),
        (50, "laugh_len_50", "Me falta el aire", "Escribe una risa de 50 letras o más.", R),
        (100, "laugh_len_100", "Ingresado por risa", "Escribe una risa de 100 letras.", E, True),
    ])  # fmt: skip
    a += _tiers("laughs", "laugh_kinds_max", [
        (3, "laugh_combo_3", "Risa políglota",
         "Mezcla 3 tipos de risa en un mensaje (jaja lol 😂).", R),
        (5, "laugh_combo_5", "Bomba de risas", "Mezcla 5 tipos de risa en un mensaje.", E, True),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="laugh_all_kinds",
            name="Políglota de la risa",
            description=(
                "Ríete de todas las formas: jaja, haha, xd, 😂, 💀, ajsjsjs, kkkk y «me meo»."
            ),
            category="laughs",
            rarity=E,
            conditions=tuple((f"laugh_{kind}", 1) for kind in LAUGH_KINDS),
        )
    )
    # 🤡 Hacer reír --------------------------------------------------------------------
    a += _tiers("funny", "laugh_replies", [
        (50, "laugh_reply_50", "Público fácil, como la claque de un mitin",
         "Ríete respondiendo a 50 mensajes.", C),
        (500, "laugh_reply_500", "Risas en directo", "Ríete respondiendo a 500 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("funny", "laughs_caused", [
        (10, "funny_10", "Gracioso, como Tip y Coll",
         "Que se rían respondiendo a tus mensajes 10 veces.", C),
        (100, "funny_100", "Payaso oficial de la corte", "Haz reír (con respuesta) 100 veces.", R),
        (1_000, "funny_1k", "Especial de Nochevieja", "Haz reír (con respuesta) 1.000 veces.", L),
    ])  # fmt: skip
    a += _tiers("funny", "laugh_self", [
        (1, "laugh_self", "Me río de mis propios chistes",
         "Responde a un mensaje tuyo con una risa.", C, True),
    ])  # fmt: skip
    a += _tiers("funny", "laugh_at_bot", [
        (1, "laugh_bot_1", "Jovani me hace gracia", "Ríete respondiendo a un mensaje del bot.", C),
        (25, "laugh_bot_25", "Fan del bot", "Ríete con el bot 25 veces. Wepa.", R),
    ])  # fmt: skip
    a += _tiers("funny", "laugh_losing", [
        (1, "laugh_losing", "Ríe por no llorar",
         "Ríete en el chat con una racha de 5 derrotas seguidas en el casino.", R, True),
    ])  # fmt: skip
    a += _tiers("funny", "laugh_chain_max", [
        (3, "laugh_chain_3", "Risa contagiosa", "Que 3 personas se rían seguidas en un canal.", C),
        (5, "laugh_chain_5", "Epidemia de risa", "Que 5 personas se rían seguidas en un canal.", R),
        (8, "laugh_chain_8", "Pandemia: confinados de la risa",
         "Que 8 personas se rían seguidas en un canal.", E),
    ])  # fmt: skip
    a += _tiers("funny", "laugh_reacts_given", [
        (50, "laugh_react_50", "Risa de reacción", "Reacciona con 😂, 🤣 o 💀 a 50 mensajes.", C),
        (500, "laugh_react_500", "Claque profesional", "Reacciona con risa a 500 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("funny", "laugh_reacts_received", [
        (25, "laughed_25", "Haces gracia, como Rajoy sin querer",
         "Recibe 25 reacciones de risa.", C),
        (250, "laughed_250", "Cómico de bar, como Tejero en el 23-F (sin querer)",
         "Recibe 250 reacciones de risa.", R),
        (2_500, "laughed_2500", "Monologuista de la tele", "Recibe 2.500 reacciones de risa.", L),
    ])  # fmt: skip
    a += _tiers("funny", "laugh_reacts_on_message_max", [
        (3, "joke_day", "Chiste del día, como un tuit de Óscar Puente",
         "Que 3 personas se rían (con reacción) del mismo mensaje.", C),
        (5, "joke_year", "Chiste del año", "Que 5 personas se rían del mismo mensaje.", R),
        (10, "joke_decade", "Chiste de la década", "Que 10 personas se rían del mismo mensaje.", E),
    ])  # fmt: skip

    # 🗣️ Lengua y temas -----------------------------------------------------------------
    a += _tiers("lengua", "msg_canario", [
        (10, "canario_10", "¡Chacho!",
         "Habla en canario (guagua, ños, cotufas…) en 10 mensajes.", C),
        (100, "canario_100", "Más canario que el gofio", "Habla en canario en 100 mensajes.", R),
        (1_000, "canario_1k", "Ños, qué fleje", "Habla en canario en 1.000 mensajes.", L),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_boricua", [
        (10, "boricua_10", "Wepa",
         "Habla en boricua (wepa, bendito, janguear…) en 10 mensajes.", C),
        (100, "boricua_100", "Boricua honorario",
         "Habla en boricua en 100 mensajes. ¿Qué? ¿Qué? ¡Toma mangó!", R),
        (500, "boricua_500", "Más boricua que Jovani", "Habla en boricua en 500 mensajes.", L),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_swear", [
        (25, "swear_25", "Lenguaje de taberna del Congreso",
         "Suelta una palabrota en 25 mensajes.", C),
        (250, "swear_250", "Marinero de puerto", "Suelta palabrotas en 250 mensajes.", R),
        (2_500, "swear_2500", "Mecagüen la mar", "Suelta palabrotas en 2.500 mensajes.", E),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_mild_swear", [
        (10, "recorcholis", "¡Recórcholis!",
         "Di «jolín», «ostras» o «caramba» en 10 mensajes. Qué fino.", C, True),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_thanks", [
        (10, "thanks_10", "Bien educado, no como en el Congreso",
         "Da las gracias en 10 mensajes.", C),
        (100, "thanks_100", "Tu madre estaría orgullosa", "Da las gracias en 100 mensajes.", R),
        (1_000, "thanks_1k", "Mayordomo inglés", "Da las gracias en 1.000 mensajes.", E),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_sorry", [
        (1, "sorry_1", "Usted perdone",
         "Pide perdón en el chat. Más de lo que ha hecho cualquier ministro.", C),
        (25, "sorry_25", "Lo siento mucho, no volverá a ocurrir", "Pide perdón 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_good_morning", [
        (10, "morning_hi_10", "Buenos días, España", "Da los buenos días 10 veces.", C),
        (100, "morning_hi_100", "Despertador oficial, como el himno en la tele de antes",
         "Da los buenos días 100 veces.", R),
        (365, "morning_hi_365", "Ni un día sin saludar", "Da los buenos días 365 veces.", L),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_good_night", [
        (10, "night_bye_10", "Mañana más, como dice Iñaki", "Da las buenas noches 10 veces.", C),
        (100, "night_bye_100", "Cierre de emisión", "Da las buenas noches 100 veces.", R),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_politics", [
        (10, "politics_10", "Cuñado en Nochebuena", "Habla de política en 10 mensajes.", C),
        (100, "politics_100", "Tertuliano de sobremesa", "Habla de política en 100 mensajes.", R),
        (1_000, "politics_1k", "Todólogo de plató", "Habla de política en 1.000 mensajes.", L),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_falcon", [
        (1, "chat_falcon", "Despegue inmediato",
         "Nombra el Falcon. Ya está calentando motores.", R, True),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_fango", [
        (1, "fango", "Máquina del fango", "Habla de bulos, fango o fake news.", R, True),
    ])  # fmt: skip
    for stat, achievement_id, name, description in (
        (
            "msg_franco",
            "chat_franco",
            "Atado y bien atado",
            "Habla del franquismo en el chat. El NO-DO ya está grabando.",
        ),
        (
            "msg_trump",
            "chat_trump",
            "Make the chat great again",
            "Nombra a Trump. Tremendo, tremendo. El mejor mensaje de la historia.",
        ),
        (
            "msg_putin",
            "chat_putin",
            "Al otro lado de la mesa larga",
            "Nombra a Putin. Aprobado con el 87 % de los votos.",
        ),
        (
            "msg_kim",
            "chat_kim",
            "Aplauso obligatorio",
            "Nombra a Kim Jong-un. Aplaude, por si acaso.",
        ),
        ("msg_xi", "chat_xi", "Crédito social +15", "Nombra a Xi Jinping. El Partido toma nota."),
        (
            "msg_netanyahu",
            "chat_netanyahu",
            "Juicio eterno",
            "Nombra a Netanyahu. La sentencia, para otra legislatura.",
        ),
        (
            "msg_obama",
            "chat_obama",
            "Yes we chat",
            "Nombra a Obama. El Nobel de la Paz, por adelantado.",
        ),
        (
            "msg_clinton",
            "chat_clinton",
            "Los correos de Hillary",
            "Nombra a los Clinton. El mensaje se autodestruye en un servidor privado.",
        ),
        (
            "msg_musk",
            "chat_musk",
            "Rumbo a Marte",
            "Nombra a Musk. Este mensaje llega a Marte en 2016. O en 2030.",
        ),
        ("msg_milei", "chat_milei", "¡Afuera!", "Nombra a Milei. Motosierra al chat."),
    ):
        a += _tiers("lengua", stat, [(1, achievement_id, name, description, C, True)])
    for stat, achievement_id, name, description in (
        (
            "msg_mango",
            "chat_mango",
            "¿Qué? ¿Qué? ¡Toma mangó!",
            "Escribe «toma mangó». Everybody want mango.",
        ),
        (
            "msg_cafe",
            "chat_cafe",
            "Hazme café",
            "Escribe «quiero café». Jovani ya pone la cafetera.",
        ),
        (
            "msg_pulpa",
            "chat_pulpa",
            "¿Con pulpa? ¡Sin pulpa!",
            "Escribe «sin pulpa». Jugo de china de el bueno.",
        ),
        (
            "msg_motora",
            "chat_motora",
            "Y Víctor le dijo a Joel",
            "Escribe «mi motora». Móntate, que nos vamos.",
        ),
    ):
        a += _tiers("lengua", stat, [(1, achievement_id, name, description, R, True)])
    a += _tiers("lengua", "msg_trump", [
        (100, "chat_trump_100", "Corresponsal en la Casa Blanca",
         "Nombra a Trump en 100 mensajes. ¿No te cansas de ganar?", R),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_putin", [
        (100, "chat_putin_100", "Analista del Kremlin",
         "Nombra a Putin en 100 mensajes. Ya te sientas en la mesa larga.", R),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_franco", [
        (50, "chat_franco_50", "Hemeroteca del NO-DO",
         "Habla del franquismo en 50 mensajes. Franco sigue muerto, por cierto.", R),
    ])  # fmt: skip
    a.append(Achievement(
        id="x_g7_chat",
        name="Cumbre del G-7 en el chat",
        description="Nombra a Trump, Putin, Kim Jong-un, Xi Jinping y Netanyahu. "
        "Foto de familia imposible.",
        category="lengua",
        rarity=R,
        conditions=tuple((stat, 1) for stat in (
            "msg_trump", "msg_putin", "msg_kim", "msg_xi", "msg_netanyahu")),
    ))  # fmt: skip
    a.append(Achievement(
        id="x_jovani_fan",
        name="Me sé el repertorio",
        description="Escribe «toma mangó», «quiero café», «sin pulpa» y «mi motora».",
        category="lengua",
        rarity=E,
        conditions=tuple((stat, 1) for stat in (
            "msg_mango", "msg_cafe", "msg_pulpa", "msg_motora")),
        secret=True,
    ))  # fmt: skip
    a += _tiers("lengua", "msg_paguita", [
        (1, "chat_paguita", "¿Y lo mío qué?", "Pregunta por la paguita.", C, True),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_manual", [
        (1, "manual", "Lectura obligatoria", "Cita el «Manual de resistencia».", E, True),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_hacienda", [
        (1, "hacienda_1", "Hacienda nos espía a todos", "Nombra a Hacienda.", C, True),
        (50, "hacienda_50", "Asesor fiscal de barra de bar",
         "Nombra a Hacienda en 50 mensajes.", R),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_cuñado", [
        (1, "cunado", "Eso lo arreglaba yo", "Di que eso lo arreglabas tú en dos días.", R, True),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_ola_k_ase", [
        (1, "ola_k_ase", "Ola k ase", "Escribe «ola k ase». Programa o k ase.", C, True),
    ])  # fmt: skip
    a += _tiers("lengua", "msg_bizum_ask", [
        (1, "bizum_ask_1", "Pásame un Bizum", "Pide un Bizum por el chat.", C),
        (25, "bizum_ask_25", "Que no llevo suelto", "Pide un Bizum 25 veces.", R),
    ])  # fmt: skip

    # 🧵 Conversación -------------------------------------------------------------------
    a += _tiers("convo", "msg_monologue_max", [
        (5, "monologue_5", "Hilo de Twitter de Elon",
         "Escribe 5 mensajes seguidos sin que nadie conteste.", C),
        (10, "monologue_10", "Monólogo de Fidel Castro",
         "Escribe 10 mensajes seguidos en un canal. Él aguantaba siete horas.", R),
        (25, "monologue_25", "Comparecencia en el Congreso", "Escribe 25 mensajes seguidos.", E),
        (50, "monologue_50", "Discurso de investidura",
         "Escribe 50 mensajes seguidos. Nadie te ha interrumpido.", L),
    ])  # fmt: skip
    a += _tiers("convo", "msg_day_max", [
        (100, "day_100", "Día libre, como el de un diputado",
         "Escribe 100 mensajes en un mismo día.", C),
        (300, "day_300", "Jornada intensiva", "Escribe 300 mensajes en un mismo día.", R),
        (1_000, "day_1k", "¿Tú duermes?", "Escribe 1.000 mensajes en un mismo día.", L),
    ])  # fmt: skip
    a += _tiers("convo", "msg_first_of_day", [
        (1, "first_1", "Abre la persiana",
         "Escribe el primer mensaje del día del servidor. Antes que el bar de abajo.", C),
        (30, "first_30", "Primero en fichar", "Abre el chat del día 30 veces.", E),
        (200, "first_200", "Funcionario de ventanilla", "Abre el chat del día 200 veces.", L),
    ])  # fmt: skip
    a += _tiers("convo", "msg_echo", [
        (1, "echo_1", "Eco de tertulia",
         "Repite tal cual el mensaje que acaba de escribir otro.", C),
        (25, "echo_25", "Aplauso de bancada", "Repite lo que dice otro 25 veces.", R),
        (250, "echo_250", "Disciplina de partido", "Repite lo que dice otro 250 veces.", L),
    ])  # fmt: skip
    a += _tiers("convo", "msg_necro", [
        (1, "necro_1", "Nigromante del Valle de Cuelgamuros",
         "Escribe en un canal que llevaba 24 h sin mensajes. Exhumación en directo.", C),
        (10, "necro_10", "Resucitador de canales", "Resucita 10 canales muertos.", R),
    ])  # fmt: skip
    a += _tiers("convo", "msg_edits", [
        (10, "edit_10", "Donde dije digo, digo «cambio de opinión»", "Edita 10 mensajes.", C),
        (100, "edit_100", "No es mentir, es cambiar de opinión", "Edita 100 mensajes.", R),
        (1_000, "edit_1k", "Rectificar es de sabios", "Edita 1.000 mensajes.", E),
    ])  # fmt: skip

    # 🗓️ Horarios y fechas ----------------------------------------------------------------
    a += _tiers("time", "msg_night", [
        (10, "night_10", "Búho de Moncloa", "Escribe 10 mensajes entre las 2:00 y las 6:00.", C),
        (100, "night_100", "Insomne, como Sánchez con Podemos en el Gobierno",
         "Escribe 100 mensajes entre las 2:00 y las 6:00. Él dijo que no dormiría tranquilo.", R),
        (1_000, "night_1k", "Vampiro de Transilvania (Rumanía, que también está en la UE)",
         "Escribe 1.000 mensajes entre las 2:00 y las 6:00.", E),
    ])  # fmt: skip
    a += _tiers("time", "msg_morning", [
        (10, "morning_10", "Madrugador como el NO-DO",
         "Escribe 10 mensajes entre las 6:00 y las 8:00.", C),
        (100, "morning_100", "Al que madruga, Hacienda le ayuda (a pagar)",
         "Escribe 100 mensajes antes de las 8:00.", R),
        (1_000, "morning_1k", "Gallo del corral", "Escribe 1.000 mensajes antes de las 8:00.", E),
    ])  # fmt: skip
    a += _tiers(
        "time", "msg_leet", [(1, "leet", "1337", "Escribe un mensaje a las 13:37.", R, True)]
    )
    a += _tiers("time", "msg_new_year", [
        (1, "new_year", "Campanadas", "Escribe en la primera hora del año.", E, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_halloween", [
        (1, "halloween", "Truco o trato, como en Bruselas", "Escribe el 31 de octubre.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_christmas", [
        (1, "christmas", "Espíritu navideño, discurso del Rey incluido",
         "Escribe en Nochebuena o en Navidad.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_canarias", [
        (1, "canarias", "¡Viva Canarias!", "Escribe el 30 de mayo, Día de Canarias.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_23f", [
        (1, "chat_23f", "Tejerazo en el chat",
         "Escribe el 23 de febrero. Se sienten, que hay mensaje.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_july4", [
        (1, "chat_july4", "God bless the chat",
         "Escribe el 4 de julio. God bless America y sus aranceles.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_20n", [
        (1, "chat_20n", "Españoles, Franco ha muerto",
         "Escribe el 20 de noviembre. Y sigue muerto, lo hemos comprobado.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_own_birthday", [
        (1, "own_bday", "Felicidades a mí", "Escribe el día de tu cumpleaños.", C, True),
    ])  # fmt: skip

    a += _tiers("time", "msg_siesta", [
        (100, "siesta_100", "Siesta, ¿qué siesta?",
         "Escribe 100 mensajes entre las 15:00 y las 17:00.", C),
        (1_000, "siesta_1k", "Antisiesta", "Escribe 1.000 mensajes a la hora de la siesta.", R),
    ])  # fmt: skip
    a += _tiers("time", "msg_office", [
        (100, "office_100", "Teletrabajo (o eso dice tu jefe)",
         "Escribe 100 mensajes entre semana de 9:00 a 14:00.", C),
        (1_000, "office_1k", "Productividad a la española",
         "Escribe 1.000 mensajes en horario de oficina.", R),
        (10_000, "office_10k", "Absentismo digital",
         "Escribe 10.000 mensajes en horario de oficina.", E),
    ])  # fmt: skip
    a += _tiers("time", "msg_weekend", [
        (100, "weekend_100", "Ni el finde descansas", "Escribe 100 mensajes en fin de semana.", C),
        (1_000, "weekend_1k", "Sábado sabadete", "Escribe 1.000 mensajes en fin de semana.", R),
    ])  # fmt: skip
    a += _tiers("time", "msg_cinderella", [
        (1, "cinderella", "Cenicienta", "Escribe un mensaje a las 00:00 en punto.", R, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_reyes", [
        (1, "reyes", "Carbón dulce", "Escribe el 6 de enero. ¿Te has portado bien?", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_valentin", [
        (1, "valentin", "San Valentín en Discord",
         "Escribe el 14 de febrero. Aquí, sin pareja… como Podemos y Sumar.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_pino", [
        (1, "pino", "Romero del Pino", "Escribe el 8 de septiembre, día del Pino.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_hispanidad", [
        (1, "hispanidad", "Desfile del 12 de octubre", "Escribe el Día de la Hispanidad.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_inocentes", [
        (1, "inocentes", "Inocente, inocente", "Escribe el 28 de diciembre.", C, True),
    ])  # fmt: skip
    a += _tiers("time", "msg_friday13", [
        (1, "friday13", "Viernes 13, mal fario de legislatura",
         "Escribe un viernes 13. Mal fario.", R, True),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="calendar",
            name="Calendario zaragozano",
            description="Escribe en todas las fechas señaladas del año.",
            category="time",
            rarity=L,
            conditions=tuple(
                (stat, 1)
                for stat in (
                    "msg_new_year",
                    "msg_reyes",
                    "msg_valentin",
                    "msg_canarias",
                    "msg_pino",
                    "msg_hispanidad",
                    "msg_halloween",
                    "msg_christmas",
                    "msg_inocentes",
                )
            ),  # fmt: skip
            secret=True,
        )
    )

    # 🎙️ Voz ------------------------------------------------------------------------------
    a += _tiers("voice", "voice_minutes", [
        (60, "voice_1h", "¿Se me oye?", "Pasa 1 hora en llamada con más gente.", C),
        (600, "voice_10h", "Tertulia de Al rojo vivo", "Pasa 10 horas en llamada.", C),
        (3_000, "voice_50h", "Locutor de Radio Nacional", "Pasa 50 horas en llamada.", R),
        (6_000, "voice_100h", "Podcaster de Ibai", "Pasa 100 horas en llamada.", R),
        (15_000, "voice_250h", "Vives en la llamada", "Pasa 250 horas en llamada.", E),
        (30_000, "voice_500h", "Okupa del canal", "Pasa 500 horas en llamada.", L),
        (60_000, "voice_1000h", "Mil horas, como el juicio de Netanyahu",
         "Pasa 1.000 horas en llamada.", M),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_session_max", [
        (180, "session_3h", "Maratón de comparecencia", "Aguanta 3 horas seguidas en llamada.", R),
        (480, "session_8h", "Jornada completa, sin reforma laboral",
         "Aguanta 8 horas seguidas en llamada.", E),
        (720, "session_12h", "Ultramaratón de Fidel",
         "Aguanta 12 horas seguidas en llamada. Él lo hacía con un solo discurso.", L),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_night", [
        (60, "vnight_1h", "Trasnochador de La Moncloa",
         "Pasa 1 hora en llamada entre las 2:00 y las 6:00.", C),
        (600, "vnight_10h", "Turno de noche", "Pasa 10 horas en llamada de madrugada.", R),
        (3_000, "vnight_50h", "Guardia nocturna", "Pasa 50 horas en llamada de madrugada.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_stream", [
        (60, "stream_1h", "En directo, como el NO-DO", "Comparte pantalla durante 1 hora.", C),
        (600, "stream_10h", "Streamer", "Comparte pantalla durante 10 horas.", R),
        (3_000, "stream_50h", "Streamer de barrio", "Comparte pantalla durante 50 horas.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_video", [
        (30, "cam_30m", "Dando la cara, no como un ministro tras la UCO",
         "Pon la cámara durante 30 minutos.", C),
        (600, "cam_10h", "Influencer con aro de luz", "Pon la cámara durante 10 horas.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_muted", [
        (120, "mute_2h", "Oyente de la sesión de control",
         "Pasa 2 horas en llamada con el micro silenciado.", C),
        (1_200, "mute_20h", "El mimo", "Pasa 20 horas en llamada sin abrir el micro.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_crowd_max", [
        (5, "crowd_5", "Fiesta en Ferraz", "Coincide en una llamada con 5 personas o más.", C),
        (10, "crowd_10", "Multitud de la Diada",
         "Coincide en una llamada con 10 personas o más.", R),
    ])  # fmt: skip
    a += _tiers("voice", "voice_alone", [
        (60, "alone_1h", "Hablando solo", "Pasa 1 hora solo en un canal de voz.", C, True),
        (600, "alone_10h", "Forever alone, como Puigdemont en Waterloo",
         "Pasa 10 horas solo en un canal de voz.", R, True),
    ], unit="min")  # fmt: skip

    a += _tiers("voice", "voice_minutes", [
        (150_000, "voice_2500h", "Plaza en propiedad",
         "Pasa 2.500 horas en llamada. Ya no te echa nadie.", M, True),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_session_max", [
        (1_440, "session_24h", "Okupa con usucapión",
         "Aguanta 24 horas seguidas en llamada.", M, True),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_crowd_max", [
        (15, "crowd_15", "Mitin", "Coincide en una llamada con 15 personas o más.", E),
        (25, "crowd_25", "Manifestación", "Coincide en una llamada con 25 personas o más.", L),
    ])  # fmt: skip
    a += _tiers("voice", "voice_alone", [
        (3_000, "alone_50h", "Ermitaño", "Pasa 50 horas solo en un canal de voz.", E, True),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_duo", [
        (60, "duo_1h", "Cara a cara electoral", "Pasa 1 hora en llamada con una sola persona.", C),
        (600, "duo_10h", "Pareja de hecho", "Pasa 10 horas en llamada a solas con alguien.", R),
        (3_000, "duo_50h", "Matrimonio de conveniencia", "Pasa 50 horas en llamada de a dos.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_morning", [
        (60, "vmorning_1h", "Café en llamada. ¡Quiero café!",
         "Pasa 1 hora en llamada entre las 6:00 y las 8:00.", C),
        (600, "vmorning_10h", "Tertulia matinal", "Pasa 10 horas en llamada de buena mañana.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_siesta", [
        (120, "vsiesta_2h", "Siesta compartida",
         "Pasa 2 horas en llamada entre las 15:00 y las 17:00.", C),
        (1_200, "vsiesta_20h", "Siesta de pijama y orinal",
         "Pasa 20 horas en llamada a la hora de la siesta.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_weekend", [
        (600, "vweekend_10h", "Plan de finde de diputado",
         "Pasa 10 horas en llamada en fin de semana.", C),
        (6_000, "vweekend_100h", "Sábado sabadete en llamada",
         "Pasa 100 horas en llamada en fin de semana.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice", "voice_new_year", [
        (1, "voice_new_year", "Uvas en llamada",
         "Estate en llamada en la primera hora del año.", E, True),
    ])  # fmt: skip
    a += _tiers("voice", "voice_christmas", [
        (1, "voice_christmas", "Nochebuena en Discord",
         "Estate en llamada en Nochebuena o en Navidad.", R, True),
    ])  # fmt: skip

    # 🎚️ Micro, cámara y AFK ---------------------------------------------------------------
    a += _tiers("voice_mic", "voice_deaf", [
        (60, "deaf_1h", "Estoy pero no estoy",
         "Pasa 1 hora en llamada con el sonido quitado.", C, True),
        (600, "deaf_10h", "Sordo selectivo, como Bruselas",
         "Pasa 10 horas ensordecido en llamada.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_afk", [
        (60, "afk_1h", "Liberado sindical", "Pasa 1 hora en el canal AFK sin dar palo al agua.", C),
        (600, "afk_10h", "Asesor del ministerio", "Pasa 10 horas en el canal AFK.", R),
        (6_000, "afk_100h", "Liberado con dietas",
         "Pasa 100 horas en el canal AFK cobrando igual.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_server_muted", [
        (1, "gagged", "Ley mordaza", "Que un moderador te silencie en llamada.", R, True),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_stream_crowd", [
        (60, "stream_crowd_1h", "Cine de verano",
         "Comparte pantalla 1 hora con 4 personas o más mirando.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_multitask", [
        (30, "multitask", "Multitarea", "Pon cámara y comparte pantalla a la vez 30 minutos.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_mute_streak_max", [
        (120, "mute_streak_2h", "Voto de silencio",
         "Aguanta 2 horas seguidas silenciado en llamada.", R),
        (600, "mute_streak_10h", "Monje cartujo", "Aguanta 10 horas seguidas silenciado.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("voice_mic", "voice_stream_starts", [
        (10, "stream_start_10", "¿Se ve mi pantalla?", "Empieza a compartir pantalla 10 veces.", C),
        (100, "stream_start_100", "Presentador de PowerPoint", "Comparte pantalla 100 veces.", R),
    ])  # fmt: skip

    # 🚪 Entradas y salidas -------------------------------------------------------------
    a += _tiers("voice_moves", "voice_joins", [
        (10, "joins_10", "Llamando a la puerta de Moncloa", "Entra 10 veces a un canal de voz.", C),
        (100, "joins_100", "Como Pedro por su casa", "Entra 100 veces a un canal de voz.", R),
        (1_000, "joins_1k", "Puerta giratoria de voz", "Entra 1.000 veces a un canal de voz.", E),
    ])  # fmt: skip
    a += _tiers("voice_moves", "voice_hops", [
        (10, "hops_10", "Saltimbanqui de partidos", "Cambia de canal de voz 10 veces.", C),
        (100, "hops_100", "Tránsfuga", "Cambia de canal de voz 100 veces.", R),
        (500, "hops_500", "Cambio de chaqueta", "Cambia de canal de voz 500 veces.", E),
    ])  # fmt: skip
    a += _tiers("voice_moves", "voice_ghost", [
        (1, "ghost_1", "Visto y no visto",
         "Entra en un canal de voz y vete en menos de 15 s.", C, True),
        (25, "ghost_25", "Diputado que solo va a votar",
         "Entra y sal de voz en menos de 15 s, 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("voice_moves", "entrance_saved", [
        (1, "entrance_1", "Sintonía propia, como el himno de Riego",
         "Guarda tu sonido de entrada con `entrada`.", C),
        (10, "entrance_10", "Indeciso", "Cambia tu sonido de entrada 10 veces.", R),
    ])  # fmt: skip
    a += _tiers("voice_moves", "entrance_played", [
        (10, "entrance_play_10", "Entrada de torero en Las Ventas",
         "Que suene tu entrada 10 veces.", C),
        (100, "entrance_play_100", "Llegó el que faltaba", "Que suene tu entrada 100 veces.", R),
        (1_000, "entrance_play_1k", "Himno nacional", "Que suene tu entrada 1.000 veces.", E),
    ])  # fmt: skip
    a += _tiers("voice_moves", "entrance_volume_max", [
        (200, "entrance_loud", "Entrada ensordecedora",
         "Pon tu sonido de entrada al 200 %.", R, True),
    ])  # fmt: skip
    a += _tiers("voice_moves", "entrance_deleted", [
        (1, "entrance_gone", "Perfil bajo", "Borra tu sonido de entrada.", C, True),
    ])  # fmt: skip

    # 🎵 Música -------------------------------------------------------------------------
    a += _tiers("music", "music_queued", [
        (1, "music_1", "DJ de verbena", "Pon una canción con `poner`.", C),
        (50, "music_50", "Pinchadiscos de verbena del PP", "Pon 50 canciones.", R),
        (500, "music_500", "La radio del pueblo (y de Kim)", "Pon 500 canciones.", E),
    ])  # fmt: skip
    a += _tiers("music", "voice_music", [
        (60, "vmusic_1h", "Verbena", "Pasa 1 hora en llamada con el bot poniendo música.", C),
        (600, "vmusic_10h", "Discoteca de Ibiza con Bruselas pagando",
         "Pasa 10 horas en llamada con música.", R),
        (3_000, "vmusic_50h", "Tenderete", "Pasa 50 horas en llamada con música.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("music", "music_skips", [
        (10, "skip_10", "Siguiente, como dice Trump a sus ministros", "Salta 10 canciones.", C),
        (100, "skip_100", "Ni la escuchas entera", "Salta 100 canciones.", R),
    ])  # fmt: skip
    a += _tiers("music", "music_stops", [
        (1, "stop_1", "Aguafiestas", "Para la música con `parar`.", C, True),
        (25, "stop_25", "Se acabó la fiesta", "Para la música 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("music", "music_volume_max", [
        (200, "music_loud", "Denuncia del vecino del quinto", "Sube la música al 200 %.", R, True),
    ])  # fmt: skip
    a += _tiers("music", "music_whisper", [
        (1, "music_asmr", "ASMR", "Baja la música al 10 % o menos.", C, True),
    ])  # fmt: skip
    a += _tiers("music", "music_track_max", [
        (20, "music_long", "Sinfonía completa, como un discurso de Castro",
         "Pon una canción de 20 minutos o más.", R),
    ], unit="min")  # fmt: skip
    a += _tiers("music", "music_queue_max", [
        (10, "music_queue_10", "Playlist de boda", "Deja la cola con 10 canciones o más.", R),
    ])  # fmt: skip
    a += _tiers("music", "music_clears", [
        (1, "music_clear", "Tabula rasa", "Vacía la cola con `vaciar`.", C),
    ])  # fmt: skip
    a += _tiers("music", "music_removes", [
        (10, "music_remove_10", "Censura previa", "Quita 10 canciones de la cola.", R),
    ])  # fmt: skip
    a += _tiers("music", "music_jovani", [
        (1, "music_jovani", "Fan de Jovani", "Pon una canción de Jovani Vázquez.", R, True),
    ])  # fmt: skip
    a += _tiers("music", "music_despacito", [
        (1, "music_despacito", "2017 ha llamado", "Pon «Despacito».", C, True),
    ])  # fmt: skip
    a += _tiers("music", "music_macarena", [
        (1, "music_macarena", "Dale a tu cuerpo alegría", "Pon «Macarena».", C, True),
    ])  # fmt: skip
    a += _tiers("music", "music_pedro", [
        (1, "music_pedro", "Pedro, Pedro, Pedro",
         "Pon una canción con «Pedro» en el título.", R, True),
    ])  # fmt: skip

    # 🖼️ Imágenes y Babel ---------------------------------------------------------------
    a += _tiers("memes", "img_made", [
        (1, "img_1", "Fotoshopero", "Genera tu primera imagen con un efecto (`memes`).", C),
        (50, "img_50", "Fábrica de memes de Moncloa", "Genera 50 imágenes.", R),
        (500, "img_500", "Ministerio de Propaganda", "Genera 500 imágenes.", E),
    ])  # fmt: skip
    a += _tiers("memes", "img_magik", [
        (10, "magik_10", "Magia negra de la Agencia Tributaria",
         "Deforma 10 imágenes con `magik`.", C),
        (100, "magik_100", "Brujo de Teror", "Deforma 100 imágenes con `magik`.", R),
    ])  # fmt: skip
    a += _tiers("memes", "img_video", [
        (10, "img_video_10", "Productora audiovisual", "Genera 10 vídeos o GIFs con efectos.", C),
        (100, "img_video_100", "Televisión pública", "Genera 100 vídeos o GIFs.", R),
    ])  # fmt: skip
    a += _tiers("memes", "img_on_others", [
        (10, "img_others_10", "Bullying artístico",
         "Aplica un efecto al avatar de otro 10 veces.", C),
        (100, "img_others_100", "Caricaturista del régimen", "Usa a otros de modelo 100 veces.", R),
    ])  # fmt: skip
    a += _tiers("memes", "img_self", [
        (10, "img_self_10", "Narciso", "Aplica un efecto a tu propio avatar 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("memes", IMG_EFFECTS_STAT, [
        (10, "img_fx_10", "Probador de filtros de TikTok (que es de Xi)",
         "Prueba 10 efectos de imagen distintos.", R),
        (30, "img_fx_30", "Catálogo de Ikea", "Prueba 30 efectos de imagen distintos.", E),
        (60, "img_fx_60", "Instagram de los 2010", "Prueba 60 efectos de imagen distintos.", L),
    ])  # fmt: skip
    a += _tiers("memes", "babel_phrases", [
        (1, "babel_1", "Torre de Babel", "Pasa una frase por `babel`.", C),
        (25, "babel_25", "Traductor de Google, nivel pinganillo", "Pasa 25 frases por `babel`.", R),
        (100, "babel_100", "Intérprete de la ONU", "Pasa 100 frases por `babel`.", E),
    ])  # fmt: skip
    a += _tiers("memes", "babel_renames", [
        (1, "babel_rename_1", "Registro civil", "Cámbiale el apodo a alguien con `babel`.", C),
        (25, "babel_rename_25", "Notario de apodos", "Babeliza 25 apodos.", R),
    ])  # fmt: skip
    a += _tiers("memes", "babel_channels", [
        (1, "babel_channel", "Reforma territorial",
         "Cámbiale el nombre a un canal con `babel`.", R, True),
    ])  # fmt: skip
    a += _tiers("memes", "babel_full", [
        (1, "babel_full", "La vuelta al mundo en 100 idiomas",
         "Que una tirada de `babel` complete los 100 saltos.", R),
    ])  # fmt: skip
    a += _tiers("memes", "babel_lost", [
        (1, "babel_lost", "Lost in translation",
         "Que `babel` se corte lejos del español.", R, True),
    ])  # fmt: skip

    # ❤️ Social ---------------------------------------------------------------------------
    a += _tiers("social", "reactions_given", [
        (50, "react_50", "Me gusta, como el like de Trump a sí mismo",
         "Reacciona a 50 mensajes de otros.", C),
        (500, "react_500", "Dedo rápido de votación telemática",
         "Reacciona a 500 mensajes de otros.", R),
        (5_000, "react_5k", "Reaccionador compulsivo", "Reacciona a 5.000 mensajes.", E),
    ])  # fmt: skip
    a += _tiers("social", "reactions_received", [
        (50, "liked_50", "Gustas, como la fruta a Ayuso",
         "Recibe 50 reacciones de otras personas.", C),
        (500, "liked_500", "Carismático, como Obama", "Recibe 500 reacciones.", R),
        (5_000, "liked_5k", "Ídolo de masas", "Recibe 5.000 reacciones.", E),
    ])  # fmt: skip
    a += _tiers("social", "reactions_on_message_max", [
        (5, "viral_5", "Viral, como un bulo en Telegram",
         "Que 5 personas reaccionen al mismo mensaje tuyo.", R),
        (10, "viral_10", "Trending topic", "Que 10 personas reaccionen al mismo mensaje.", E),
    ])  # fmt: skip
    a += _tiers("social", "greetings_sent", [
        (1, "greet_1", "Buen amigo",
         "Felicita a alguien por su cumpleaños. Con huevo y jugo de china de el bueno.", C),
        (10, "greet_10", "Alma de la fiesta", "Felicita 10 cumpleaños.", R),
        (50, "greet_50", "Tarta para todos", "Felicita 50 cumpleaños.", L),
    ])  # fmt: skip
    a += _tiers("social", "greetings_received", [
        (5, "greeted_5", "Querido, como un rey emérito en Sanxenxo",
         "Recibe 5 felicitaciones de cumpleaños.", C),
        (20, "greeted_20", "Popular, como el PP en Galicia",
         "Recibe 20 felicitaciones de cumpleaños.", R),
    ])  # fmt: skip
    a += _tiers("social", "welcomes_given", [
        (1, "welcome_1", "Comité de bienvenida", "Dale la bienvenida a alguien nuevo.", C),
        (5, "welcome_5", "Relaciones públicas de Moncloa", "Da la bienvenida a 5 personas.", R),
        (20, "welcome_20", "Portero de discoteca", "Da la bienvenida a 20 personas.", E),
    ])  # fmt: skip
    a += _tiers("social", "welcomes_fast", [
        (1, "welcome_fast", "Más rápido que Hacienda",
         "Da la bienvenida a alguien en su primer minuto en el servidor.", R, True),
    ])  # fmt: skip
    a += _tiers("social", "msg_bot_call", [
        (1, "bot_call", "¿Me llamabas?", "Menciona al bot o di su nombre.", C, True),
    ])  # fmt: skip
    a += _tiers("social", "msg_sanxe", [
        (1, "sanxe", "Invocación", "Nombra a Perro Sanxe en el chat.", C, True),
    ])  # fmt: skip

    a += _tiers("social", "reactions_given", [
        (25_000, "react_25k", "Pulgar de acero", "Reacciona a 25.000 mensajes.", M),
    ])  # fmt: skip
    a += _tiers("social", "reactions_received", [
        (25_000, "liked_25k", "Influencer de barrio", "Recibe 25.000 reacciones.", M),
    ])  # fmt: skip
    a += _tiers("social", "reactions_on_message_max", [
        (20, "viral_20", "Meme nacional", "Que 20 personas reaccionen al mismo mensaje tuyo.", L),
    ])  # fmt: skip
    a += _tiers("social", "greetings_received", [
        (50, "greeted_50", "Rey del cumpleaños",
         "Recibe 50 felicitaciones de cumpleaños.", E),
    ])  # fmt: skip
    a += _tiers("social", "welcomes_given", [
        (50, "welcome_50", "Ministerio de Inclusión", "Da la bienvenida a 50 personas.", L),
    ])  # fmt: skip
    a += _tiers("social", "msg_bot_call", [
        (100, "bot_call_100", "Pesado con el bot", "Menciona al bot o di su nombre 100 veces.", R),
        (1_000, "bot_call_1k", "Mejor amigo de Jovani", "Llama al bot 1.000 veces.", E),
    ])  # fmt: skip
    a += _tiers("social", "msg_sanxe", [
        (100, "sanxe_100", "Obsesionado con Sanxe", "Nombra a Perro Sanxe 100 veces.", R),
    ])  # fmt: skip

    # 📝 Lista (cogs/todo.py) ------------------------------------------------------------
    a += _tiers("todo", "todo_added", [
        (1, "todo_add_1", "Apuntado", "Apunta tu primera tarea con `lista`.", C),
        (5, "todo_add_5", "Post-it en la nevera de Moncloa", "Apunta 5 tareas en la lista.", C),
        (25, "todo_add_25", "Agenda andante de ministro", "Apunta 25 tareas en la lista.", R),
        (100, "todo_add_100", "Jefe de proyecto", "Apunta 100 tareas en la lista.", E),
    ])  # fmt: skip
    a += _tiers("todo", "todo_done", [
        (1, "todo_done_1", "Tachado", "Tacha tu primera tarea de la lista.", C),
        (5, "todo_done_5", "Algo es algo, como el programa electoral",
         "Tacha 5 tareas de la lista.", C),
        (25, "todo_done_25", "Productivo, como Bruselas un viernes",
         "Tacha 25 tareas de la lista.", R),
        (100, "todo_done_100", "Máquina de tachar promesas", "Tacha 100 tareas de la lista.", E),
    ])  # fmt: skip

    a += _tiers("todo", "todo_added", [
        (500, "todo_add_500", "Lista de la compra del Mercadona",
         "Apunta 500 tareas en la lista.", L),
    ])  # fmt: skip
    a += _tiers("todo", "todo_done", [
        (500, "todo_done_500", "Productividad alemana", "Tacha 500 tareas de la lista.", L),
    ])  # fmt: skip
    a += _tiers("todo", "todo_done_batch_max", [
        (5, "todo_batch_5", "Limpieza de primavera", "Tacha 5 tareas de golpe.", C),
        (20, "todo_batch_20", "Limpieza general", "Tacha 20 tareas de golpe.", R),
    ])  # fmt: skip

    # 🍻 Beernight (cogs/beernight.py: beernight_drink_stats y beernight_close_stats) ----
    a.append(Achievement(
        id="beer_night_1", name="Primera ronda",
        description="Participa en tu primera beernight.",
        category="beernight", rarity=C, conditions=(("beer_nights", 1),),
        story=BEERNIGHT_STORY,
    ))  # fmt: skip
    a += _tiers("beernight", "beer_nights", [
        (3, "beer_night_3", "Cliente de la casa", "Participa en 3 beernights.", R),
        (10, "beer_night_10", "Tu taburete tiene tu forma", "Participa en 10 beernights.", E),
        (25, "beer_night_25", "Peña cervecera", "Participa en 25 beernights.", L),
        (50, "beer_night_50", "Patrimonio Inmaterial de la Humanidad",
         "Participa en 50 beernights.", M),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_hosted", [
        (1, "beer_host_1", "Anfitrión", "Organiza una beernight.", C),
        (5, "beer_host_5", "Excelentísimo", "Organiza 5 beernights.", R),
        (20, "beer_host_20", "Presidente del Gobierno de la birra",
         "Organiza 20 beernights. Sin Falcon, de momento.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_sips", [
        (1, "beer_sip_1", "El primer sorbo", "Bebe tu primer sorbo en una beernight.", C),
        (25, "beer_sip_25", "Calentando, como Aznar antes de conducir",
         "Bebe 25 sorbos en beernights.", C),
        (100, "beer_sip_100", "Hígado de acero", "Bebe 100 sorbos en beernights.", R),
        (500, "beer_sip_500", "Barril humano", "Bebe 500 sorbos en beernights.", E),
        (1_500, "beer_sip_1500", "Patrocinado por Tropical",
         "Bebe 1.500 sorbos en beernights.", L),
        (5_000, "beer_sip_5000", "Monumento a la cebada", "Bebe 5.000 sorbos en beernights.", M),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_night_sips_max", [
        (15, "beer_night_sips_15", "Noche movida en Ferraz",
         "Bebe 15 sorbos en una sola beernight.", C),
        (30, "beer_night_sips_30", "Noche toledana", "Bebe 30 sorbos en una sola beernight.", R),
        (60, "beer_night_sips_60", "Mañana no existe",
         "Bebe 60 sorbos en una sola beernight.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_drink_max", [
        (3, "beer_gulp_3", "Trago largo", "Bebe 3 sorbos de una sola vez.", C),
        (5, "beer_gulp_5", "Fondo blanco", "Bebe 5 sorbos de una sola vez.", R, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_mvp", [
        (1, "beer_mvp_1", "MVP de la barra de Casa Pepe", "Sé quien más bebe en una beernight.", C),
        (5, "beer_mvp_5", "Récord autonómico", "Sé quien más bebe en 5 beernights.", E),
        (20, "beer_mvp_20", "Leyenda del garito", "Sé quien más bebe en 20 beernights.", L),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_reports_ok", [
        (1, "beer_snitch_1", "Chivato", "Que te confirmen un chivatazo.", C),
        (10, "beer_snitch_10", "Confidente de la UCO", "Que te confirmen 10 chivatazos.", R),
        (50, "beer_snitch_50", "Pegasus con patas", "Que te confirmen 50 chivatazos.", E),
        (200, "beer_snitch_200", "Gran Hermano", "Que te confirmen 200 chivatazos.", L),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_reports_false", [
        (1, "beer_lie_1", "Bulo", "Que te tumben un chivatazo por falso y bebas tú.", C, True),
        (10, "beer_lie_10", "Pseudomedio digital",
         "Que te tumben 10 chivatazos por falsos.", R, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_confirms", [
        (1, "beer_confirm_1", "Notario", "Confirma un chivatazo ajeno.", C),
        (25, "beer_confirm_25", "Fe pública", "Confirma 25 chivatazos ajenos.", R),
        (100, "beer_confirm_100", "Tribunal Supremo de la birra",
         "Confirma 100 chivatazos ajenos.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_denies", [
        (1, "beer_deny_1", "Fact-checker", "Vota «mentira» en un chivatazo.", C),
        (25, "beer_deny_25", "Maldita birra", "Vota «mentira» en 25 chivatazos.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_confessions", [
        (1, "beer_confess_1", "Confieso que he bebido", "Confiesa que has caído.", C),
        (10, "beer_confess_10", "Arrepentido", "Confiesa 10 veces que has caído.", R),
        (50, "beer_confess_50", "Colaborador con la justicia",
         "Confiesa 50 veces que has caído.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_accused", [
        (1, "beer_caught_1", "Pillado", "Bebe por un chivatazo confirmado.", C),
        (25, "beer_caught_25", "Investigado por la birra",
         "Bebe por 25 chivatazos confirmados.", R),
        (100, "beer_caught_100", "Ficha policial", "Bebe por 100 chivatazos confirmados.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_event_hits", [
        (1, "beer_event_1", "Le tocó", "Bebe por un evento aleatorio.", C),
        (25, "beer_event_25", "Imán de desgracias", "Bebe por 25 eventos aleatorios.", R),
        (100, "beer_event_100", "El bombo te odia", "Bebe por 100 eventos aleatorios.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_duels_won", [
        (1, "beer_duel_win_1", "En guardia", "Gana un duelo de la beernight.", C),
        (10, "beer_duel_win_10", "Gladiador de la Puerta del Sol",
         "Gana 10 duelos de la beernight.", R),
        (50, "beer_duel_win_50", "Invicto del salón", "Gana 50 duelos de la beernight.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_duels_lost", [
        (1, "beer_duel_lose_1", "Tocado", "Pierde un duelo de la beernight.", C),
        (10, "beer_duel_lose_10", "Sparring", "Pierde 10 duelos de la beernight.", R, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_challenges_ok", [
        (1, "beer_challenge_ok_1", "Reto superado", "Cumple un reto de la beernight.", C),
        (10, "beer_challenge_ok_10", "Showman de Eurovisión",
         "Cumple 10 retos de la beernight.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_challenges_failed", [
        (1, "beer_challenge_ko_1", "Pánico escénico", "Falla un reto de la beernight.", C),
        (10, "beer_challenge_ko_10", "Abucheado", "Falla 10 retos de la beernight.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_given", [
        (10, "beer_give_10", "Generoso con lo ajeno",
         "Haz beber 10 sorbos a otros con repartos o chivatazos.", C),
        (100, "beer_give_100", "Recaudador", "Haz beber 100 sorbos a otros.", R),
        (500, "beer_give_500", "Agencia Tributaria de la birra",
         "Haz beber 500 sorbos a otros.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_toasts", [
        (1, "beer_toast_1", "Por gusto", "Brinda por voluntad propia.", C),
        (50, "beer_toast_50", "Nadie te obliga", "Brinda 50 veces por voluntad propia.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_custom_added", [
        (1, "beer_law_1", "Legislador", "Propón un mandamiento de la casa.", C),
        (10, "beer_law_10", "Padre de la Constitución",
         "Propón 10 mandamientos de la casa.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_custom_broken", [
        (1, "beer_law_used_1", "Tu ley se cumple",
         "Que alguien caiga en un mandamiento que propusiste.", C),
        (25, "beer_law_used_25", "Hecha la ley…",
         "Que caigan 25 veces en tus mandamientos.", R),
        (100, "beer_law_used_100", "BOE andante",
         "Que caigan 100 veces en tus mandamientos.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_sound_saved", [
        (1, "beer_dj_1", "DJ de la beernight", "Sube un audio para la beernight.", C),
        (5, "beer_dj_5", "Productor musical", "Sube 5 audios para la beernight.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_retired", [
        (1, "beer_retire_1", "Me retiro a mis aposentos", "Retírate de una beernight.", C),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_events_forced", [
        (1, "beer_force_1", "Dedo nervioso", "Lanza un evento a mano como anfitrión.", C),
        (25, "beer_force_25", "Gobernar por decreto",
         "Lanza 25 eventos a mano como anfitrión.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_night_minutes_max", [
        (120, "beer_long_2h", "Una y nos vamos", "Aguanta 2 horas en una beernight.", C),
        (240, "beer_long_4h", "Hasta que cierre el bar",
         "Aguanta 4 horas en una beernight.", R),
        (360, "beer_long_6h", "Afterhours", "Aguanta 6 horas en una beernight.", E),
    ], unit="min")  # fmt: skip
    a += _tiers("beernight", "beer_crowd_max", [
        (4, "beer_crowd_4", "Mesa para cuatro, como un pacto de Gobierno",
         "Participa en una beernight de 4 personas.", C),
        (8, "beer_crowd_8", "Botellón", "Participa en una beernight de 8 personas.", R),
        (12, "beer_crowd_12", "Esto parece una boda",
         "Participa en una beernight de 12 personas.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_streak_max", [
        (2, "beer_streak_2", "Ayer también", "Participa en beernights 2 días seguidos.", C),
        (3, "beer_streak_3", "Puente largo", "Participa en beernights 3 días seguidos.", R),
        (7, "beer_streak_7", "Feria de abril", "Participa en beernights 7 días seguidos.", E),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_forgiven", [
        (1, "beer_forgiven_1", "Indulto parcial",
         "Que el tope por hora te perdone algún sorbo.", C, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_snitch_host", [
        (1, "beer_coup", "Golpe de Estado",
         "Que te confirmen un chivatazo contra el anfitrión.", R, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_zero_night", [
        (1, "beer_teetotal", "Abstemio de facto",
         "Pasa una hora o más en una beernight sin beber ni un sorbo.", R, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_nice", [
        (1, "beer_nice", "Sesenta y nueve sorbos",
         "Bebe exactamente 69 sorbos en una beernight.", R, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_dawn", [
        (1, "beer_dawn", "Churros con chocolate",
         "Sigue en una beernight cuando cierra entre las 5 y las 9 de la mañana.", C, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_thursday", [
        (1, "beer_juernes", "Juernes", "Participa en una beernight que empieza en jueves.", C),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_monday", [
        (1, "beer_monday", "Lunes de resaca",
         "Participa en una beernight que empieza en lunes. ¿Quién hace eso?", C, True),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_nochevieja", [
        (1, "beer_nochevieja", "Uvas con espuma",
         "Participa en una beernight en Nochevieja.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_dia_canarias", [
        (1, "beer_dia_canarias", "Arrorró con cerveza",
         "Participa en una beernight el Día de Canarias (30 de mayo).", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_pino", [
        (1, "beer_pino", "Pino con espuma",
         "Participa en una beernight el 7 u 8 de septiembre.", R),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_halloween", [
        (1, "beer_halloween", "Truco o birra", "Participa en una beernight en Halloween.", C),
    ])  # fmt: skip
    a += _tiers("beernight", "beer_san_juan", [
        (1, "beer_san_juan", "Noche de San Juan",
         "Participa en una beernight la noche del 23 de junio.", C),
    ])  # fmt: skip
    a.append(Achievement(
        id="beer_all_reasons", name="He bebido por todo",
        description="Bebe por cada motivo: confesión, chivatazo, chivatazo falso, evento, "
        "duelo, reto, reparto y brindis.",
        category="beernight", rarity=R,
        conditions=tuple((f"{BEER_KIND_PREFIX}{reason.value}", 1) for reason in BeerReason),
    ))  # fmt: skip

    # 📈 Niveles --------------------------------------------------------------------------
    a += _tiers("levels", "level_max", [
        (5, "level_5", "Novato, como un ministro recién nombrado", "Llega al nivel 5.", C),
        (10, "level_10", "De la casa, como un asesor de toda la vida", "Llega al nivel 10.", C),
        (15, "level_15", "Ya no eres nuevo, eres tertuliano", "Llega al nivel 15.", C),
        (20, "level_20", "Veterano de la Transición", "Llega al nivel 20.", R),
        (30, "level_30", "Pilar del servidor", "Llega al nivel 30.", R),
        (40, "level_40", "Crisis de los cuarenta", "Llega al nivel 40.", E),
        (50, "level_50", "Leyenda local, como Jesús Gil", "Llega al nivel 50.", E),
        (60, "level_60", "Jubilación anticipada", "Llega al nivel 60.", L),
        (75, "level_75", "Semidiós, como se cree Trump", "Llega al nivel 75.", L),
        (90, "level_90", "Casi centenario", "Llega al nivel 90.", M),
        (100, "level_100", "Nivel 100, como el arancel de Trump", "Llega al nivel 100.", M),
    ])  # fmt: skip
    a += _tiers("levels", "activity_streak_max", [
        (3, "streak_3", "Fin de semana largo de puente", "Habla 3 días seguidos.", C),
        (7, "streak_7", "Una semana sin faltar", "Habla 7 días seguidos.", R),
        (14, "streak_14", "Quincena completa sin dimitir", "Habla 14 días seguidos.", R),
        (30, "streak_30", "Un mes sin faltar", "Habla 30 días seguidos.", E),
        (60, "streak_60", "Dos meses sin vacaciones", "Habla 60 días seguidos.", E),
        (100, "streak_100", "Cien días de gracia",
         "Habla 100 días seguidos. Ningún Gobierno los respeta.", L),
        (200, "streak_200", "Ni en agosto", "Habla 200 días seguidos.", L),
        (365, "streak_365", "Un año entero", "Habla 365 días seguidos.", M),
    ])  # fmt: skip

    a += _tiers("levels", "level_max", [
        (150, "level_150", "Más allá del cien",
         "Llega al nivel 150. ¿Qué haces aún aquí?", M, True),
    ])  # fmt: skip
    a += _tiers("levels", "activity_streak_max", [
        (730, "streak_730", "Funcionario del chat",
         "Habla 730 días seguidos. Ni una baja en dos años.", M, True),
    ])  # fmt: skip

    # 🎡 Ruleta ---------------------------------------------------------------------------
    a += _tiers("roulette", "roulette_spins", [
        (1, "rl_1", "Hagan juego", "Juega tu primera tirada de ruleta.", C),
        (50, "rl_50", "Cliente del NO-DO",
         "Juega 50 tiradas de ruleta. Ya sales en el noticiario.", C),
        (250, "rl_250", "Crupier honorario", "Juega 250 tiradas de ruleta.", R),
        (1_000, "rl_1k", "La bola es mi amiga", "Juega 1.000 tiradas de ruleta.", E),
        (5_000, "rl_5k", "Vives en la mesa", "Juega 5.000 tiradas de ruleta.", L),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_wins", [
        (10, "rlw_10", "Diez escaños", "Gana 10 tiradas de ruleta.", C),
        (100, "rlw_100", "Grupo parlamentario propio", "Gana 100 tiradas de ruleta.", R),
        (1_000, "rlw_1k", "Rey de la ruleta", "Gana 1.000 tiradas de ruleta.", L),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_straight_wins", [
        (1, "pleno_1", "¡Pleno!", "Acierta un número suelto.", C),
        (10, "pleno_10", "Francotirador del CIS",
         "Acierta 10 plenos. Tezanos quiere tu método.", E),
        (50, "pleno_50", "Bruja Lola", "Acierta 50 plenos.", L),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_green_wins", [
        (1, "green", "Verde esperanza", "Gana apostando al 0 o al 00.", E),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_double_zero_wins", [
        (1, "double_zero", "Doble cero", "Acierta un pleno al 00.", E, True),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_color_wins", [
        (50, "color_50", "Rojos y azules",
         "Gana 50 apuestas a color. El bipartidismo te agradece la fidelidad.", C),
        (500, "color_500", "Ajedrecista", "Gana 500 apuestas a color.", E),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_wagers_max", [
        (5, "wagers_5", "Pintor de mesa", "Juega 5 apuestas en una sola tirada.", C),
        (10, "wagers_10", "Alfombra de fichas", "Juega 10 apuestas en una sola tirada.", R),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_streak_max", [
        (3, "rlstreak_3", "Tripartito de la ruleta",
         "Gana 3 tiradas seguidas en la misma mesa.", C),
        (5, "rlstreak_5", "Racha de Gobierno de coalición",
         "Gana 5 tiradas seguidas en la misma mesa.", R),
        (8, "rlstreak_8", "Ocho años de Rajoy",
         "Gana 8 tiradas seguidas en la misma mesa. Y sin leerte el periódico.", E),
        (12, "rlstreak_12", "¿Trampas? Que lo investigue la UCO",
         "Gana 12 tiradas seguidas en la misma mesa.", M),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_repeat_pocket", [
        (1, "deja_vu", "Déjà vu", "Que salga el mismo número dos veces seguidas.", C, True),
    ])  # fmt: skip

    a += _tiers("roulette", "roulette_spins", [
        (10_000, "rl_10k", "Crupier jubilado", "Juega 10.000 tiradas de ruleta.", M),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_wins", [
        (2_500, "rlw_2500", "Casino de Montecarlo", "Gana 2.500 tiradas de ruleta.", M),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_dozen_wins", [
        (25, "dozen_25", "Docena de huevos",
         "Gana 25 apuestas a docena o columna. Los que le echas tú.", C),
        (250, "dozen_250", "Mayorista de docenas", "Gana 250 apuestas a docena o columna.", E),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_half_wins", [
        (50, "half_50", "Par o impar, qué más da",
         "Gana 50 apuestas a par, impar, 1-18 o 19-36.", C),
        (500, "half_500", "Cara o cruz profesional",
         "Gana 500 apuestas a par, impar o mitades.", E),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_pyrrhic", [
        (1, "pyrrhic_1", "Victoria pírrica",
         "Gana una apuesta y aun así pierde dinero en la tirada.", C, True),
        (100, "pyrrhic_100", "Victorias de Pirro", "Gana perdiendo dinero 100 veces.", E),
    ])  # fmt: skip
    a += _tiers("roulette", ROULETTE_NUMBERS_STAT, [
        (10, "numbers_10", "Ruleta rusa",
         "Acierta a pleno 10 números distintos. Putin juega con otra.", E),
        (25, "numbers_25", "Medio paño", "Acierta a pleno 25 números distintos.", L),
        (38, "numbers_38", "Bingo de la ruleta",
         "Acierta a pleno los 38 números, el 0 y el 00 incluidos.", M),
    ])  # fmt: skip
    a += _tiers("roulette", ROULETTE_FAVOURITE_STAT, [
        (3, "lucky_3", "Número de la suerte", "Acierta 3 plenos al mismo número.", E),
        (10, "lucky_10", "Siempre el mismo, como el Partido de Xi",
         "Acierta 10 plenos al mismo número.", M),
    ])  # fmt: skip
    a += _tiers("roulette", f"{ROULETTE_HIT_PREFIX}13", [
        (1, "martes_13", "Martes y 13", "Acierta un pleno al 13.", R, True),
    ])  # fmt: skip
    a += _tiers("roulette", f"{ROULETTE_HIT_PREFIX}7", [
        (1, "siete_suerte", "El siete de la suerte", "Acierta un pleno al 7.", R, True),
    ])  # fmt: skip
    for number, achievement_id, name, description in (
        (
            1,
            "pleno_1_trump",
            "Number one, believe me",
            "Acierta un pleno al 1. Trump dice que ese número lo inventó él.",
        ),
        (
            14,
            "pleno_14",
            "14 de abril",
            "Acierta un pleno al 14. La bola se proclama República en la casilla.",
        ),
        (
            15,
            "pleno_15m",
            "Indignado del 15-M",
            "Acierta un pleno al 15. Acampada en Sol con la ganancia.",
        ),
        (
            20,
            "pleno_20n",
            "Españoles, el 20 ha salido",
            "Acierta un pleno al 20. Arias Navarro lo anuncia con lágrimas en los ojos.",
        ),
        (
            23,
            "pleno_23f",
            "Tejerazo en la mesa",
            "Acierta un pleno al 23. ¡Quieto todo el mundo, que cobro!",
        ),
    ):
        a += _tiers("roulette", f"{ROULETTE_HIT_PREFIX}{number}", [
            (1, achievement_id, name, description, R, True),
        ])  # fmt: skip
    a += _tiers("roulette", "roulette_cover_max", [
        (30, "cover_30", "Cobertura de Movistar", "Cubre 30 números o más en una tirada.", C),
        (38, "cover_38", "Apostar a todo (y perder igual)",
         "Cubre los 38 números en una tirada.", R, True),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_zero_sweep", [
        (10, "zero_10", "El cero, que es de Hacienda",
         "Pierde todo 10 veces porque sale el 0 o el 00.", R),
        (100, "zero_100", "Gafe del cero", "Que el cero te barra la mesa 100 veces.", E),
    ])  # fmt: skip
    # Rayos: la ruleta relámpago de los casinos en línea, con Perro Sanxe de Zeus.
    a += _tiers("roulette", "roulette_lucky_wins", [
        (1, "rayo_1", "Le ha caído un rayo",
         "Acierta un pleno con rayo. Hacienda ya ha visto el resplandor.", E),
        (5, "rayo_5", "Pararrayos humano", "Acierta 5 plenos con rayo.", L),
        (20, "rayo_20", "Zeus en el Falcon", "Acierta 20 plenos con rayo.", M),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_lucky_max", [
        (200, "rayo_x200", "Tormenta de verano", "Cobra un rayo de ×200 o más.", L),
        (500, "rayo_x500", "El rayo que no cesa",
         "Cobra un rayo de ×500. Miguel Hernández lo escribió para ti.", L, True),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_lucky_missed", [
        (1, "rayo_fallo_1", "Rayo sin trueno",
         "Ten un pleno con rayo y que la bola caiga en otro sitio.", C),
        (25, "rayo_fallo_25", "La AEMET no avisó",
         "Ten rayo en tu número 25 veces sin que salga.", E),
        (100, "rayo_fallo_100", "Me cae el rayo al lado",
         "Ten rayo en tu número 100 veces sin que salga.", L),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_lucky_zero", [
        (1, "rayo_verde", "Rayo verde",
         "Acierta un pleno con rayo al 0 o al 00. Ni el Gobierno lo vio venir.", M, True),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_storm_max", [
        (4, "storm_4", "Ciclogénesis explosiva", "Juega una tirada con 4 rayos.", C),
        (5, "storm_5", "Gota fría", "Juega una tirada con 5 rayos a la vez.", C, True),
    ])  # fmt: skip
    # El casi: la bola pasa por tu número y se va al de al lado.
    a += _tiers("roulette", "roulette_near_miss", [
        (1, "casi_1", "Por una casilla", "Que la bola caiga a una o dos casillas de tu pleno.", C),
        (25, "casi_25", "Casi, casi", "Quédate a una o dos casillas de tu pleno 25 veces.", R),
        (250, "casi_250", "El casi es mi apellido",
         "Quédate a una o dos casillas de tu pleno 250 veces.", L),
        (1_000, "casi_1k", "Resiliencia, dijo el ministro",
         "Quédate a una o dos casillas de tu pleno 1.000 veces y sigue jugando.", M),
    ])  # fmt: skip
    # Calientes y fríos: la falacia del jugador con botón propio.
    a += _tiers("roulette", "roulette_hot_bets", [
        (1, "rl_hot_1", "Está que arde", "Apuesta al número caliente con 🔥.", C),
        (50, "rl_hot_50", "Sondeo del CIS",
         "Apuesta 50 veces al número caliente. Lo que sale, sale.", R),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_cold_bets", [
        (1, "rl_cold_1", "Le toca, como a Feijóo", "Apuesta al número frío con ❄️.", C),
        (50, "rl_cold_50", "Falacia del jugador",
         "Apuesta 50 veces al número frío. La bola no tiene memoria; tú tampoco.", R),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_hot_hits", [
        (1, "rl_hot_hit", "Seguía caliente", "Acierta el número caliente con 🔥.", E),
    ])  # fmt: skip
    a += _tiers("roulette", "roulette_cold_hits", [
        (1, "rl_cold_hit", "Ya tocaba",
         "Acierta el número frío con ❄️. Tenías razón (por casualidad).", E, True),
    ])  # fmt: skip

    # 🃏 Blackjack ------------------------------------------------------------------------
    a += _tiers("blackjack", "bj_hands", [
        (1, "bj_1", "Primera mano", "Juega tu primera mano de blackjack.", C),
        (50, "bj_50", "Jugador de mus sin pareja", "Juega 50 manos de blackjack.", C),
        (250, "bj_250", "Tahúr", "Juega 250 manos de blackjack.", R),
        (1_000, "bj_1k", "Mesa reservada", "Juega 1.000 manos de blackjack.", E),
        (5_000, "bj_5k", "Dueño del casino", "Juega 5.000 manos de blackjack.", L),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_wins", [
        (10, "bjw_10", "Le pillas el truco", "Gana 10 manos de blackjack.", C),
        (100, "bjw_100", "Ganador nato, dijo Trump de sí mismo", "Gana 100 manos de blackjack.", R),
        (1_000, "bjw_1k", "La banca te teme, como Bruselas a Orbán",
         "Gana 1.000 manos de blackjack.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_naturals", [
        (1, "natural_1", "¡Blackjack!", "Saca un blackjack (as y figura de salida).", C),
        (10, "natural_10", "As en la manga de Bill Clinton",
         "Saca 10 blackjacks. Él tenía otra cosa en la manga.", R),
        (50, "natural_50", "Contador de cartas del KGB", "Saca 50 blackjacks.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_double_wins", [
        (1, "dbl_1", "Doble o nada", "Gana una mano después de doblar.", C),
        (25, "dbl_25", "Sangre fría de Putin", "Gana 25 manos doblando.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_splits", [
        (1, "split_1", "Divide y vencerás",
         "Separa una pareja. Como el PSOE con Podemos y Sumar.", C),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_split_sweeps", [
        (1, "split_sweep", "Doble victoria", "Gana las dos manos después de separar.", C),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_busts", [
        (10, "bust_10", "Me pasé, como Ayuso con la fruta", "Pásate de 21 diez veces.", C),
        (100, "bust_100", "Avaricioso",
         "Pásate de 21 cien veces. Bárcenas te habría avisado de que es mejor en Suiza.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_21_multi", [
        (1, "21_1", "Veintiuno", "Suma 21 con tres cartas o más.", C),
        (25, "21_25", "Matemático", "Suma 21 con tres cartas o más 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_cards_max", [
        (5, "cards_5", "Cinco cartas, cinco ministerios",
         "Acaba una mano con 5 cartas sin pasarte.", C),
        (6, "cards_6", "Castillo de naipes", "Acaba una mano con 6 cartas sin pasarte.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_pushes", [
        (10, "push_10", "Empate en el Congreso",
         "Empata 10 manos. Como la reforma laboral, decidida por un voto equivocado.", C),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_dealer_busts", [
        (25, "dbust_25", "La banca revienta", "Gana 25 manos porque la banca se pasa.", C),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_dealer_naturals", [
        (5, "dnat_5", "La banca siempre gana, como el PRI",
         "Que la banca saque blackjack 5 veces.", R),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_bad_beat", [
        (1, "bad_beat", "Por los pelos", "Pierde con 20 contra 21 de la banca.", C, True),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_kamikaze", [
        (1, "kamikaze", "Kamikaze con suerte", "Pide carta con 17 o más y no te pases.", E, True),
    ])  # fmt: skip

    a += _tiers("blackjack", "bj_hands", [
        (10_000, "bj_10k", "Crupier de Las Vegas de Trump",
         "Juega 10.000 manos de blackjack. Él quebró tres casinos; tú sigues aquí.", M),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_wins", [
        (2_500, "bjw_2500", "Banca rota", "Gana 2.500 manos de blackjack.", L),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_naturals", [
        (200, "natural_200", "Veintiuno de oro", "Saca 200 blackjacks.", L),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_busts", [
        (1_000, "bust_1k", "Pasado de vueltas", "Pásate de 21 mil veces.", L),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_pushes", [
        (100, "push_100", "Tablas de ajedrez", "Empata 100 manos.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_dealer_busts", [
        (250, "dbust_250", "Banca en quiebra", "Gana 250 manos porque la banca se pasa.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_suited_natural", [
        (1, "suited_1", "Blackjack de etiqueta",
         "Saca blackjack con el as y la figura del mismo palo.", C),
        (10, "suited_10", "Traje a medida", "Saca 10 blackjacks del mismo palo.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_triple_seven", [
        (1, "triple_seven", "Siete, siete, siete", "Suma 21 con tres sietes.", L, True),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_five_21", [
        (1, "five_21", "Escalera al cielo", "Suma 21 con cinco cartas o más.", R),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_double_loss", [
        (10, "dloss_10", "Doblar y llorar", "Pierde 10 manos después de doblar.", R),
        (100, "dloss_100", "Doble o nada (nada)", "Pierde 100 manos dobladas.", L),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_stand_low", [
        (1, "stand_low", "Me planto, que hace frío", "Plántate con 11 o menos.", C, True),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_split_aces", [
        (1, "split_aces", "Dos ases en la manga", "Separa una pareja de ases.", R),
        (10, "split_aces_10", "Mago de los ases", "Separa ases 10 veces.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_both_bj", [
        (1, "both_bj", "Empate de titanes",
         "Saca blackjack cuando la banca también lo tiene.", R, True),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_dealer_five", [
        (10, "dealer_five", "La banca se lo curra",
         "Que la banca robe 5 cartas o más 10 veces.", R),
    ])  # fmt: skip
    # Hasta que llegó el seguro, el blackjack de la banca era empate y esto lo
    # contaba. Ahora cuenta los seguros cobrados: menos de uno de cada 40
    # manos aunque se asegure siempre, de ahí las metas más bajas. Rarezas de
    # `docs/auditoria_logros.py` (un jugador que asegura una vez de cada cuatro).
    a += _tiers("blackjack", "bj_dealer_bj_saved", [
        (1, "bj_rescate", "Rescate de Bankia",
         "Cobra el seguro: la banca tenía blackjack.", C),
        (10, "bj_rescate_25", "El FROB te quiere",
         "Cobra el seguro 10 veces.", E),
        (50, "bj_rescate_250", "Demasiado grande para caer",
         "Cobra el seguro 50 veces.", L),
        (200, "bj_rescate_1k", "Rescatado con dinero de todos",
         "Cobra el seguro 200 veces.", M),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_insured", [
        (1, "bj_seguro", "Seguro a todo riesgo",
         "Paga el seguro cuando la banca enseña un as.", C),
        (25, "bj_seguro_25", "Cliente de la Mutua",
         "Paga el seguro 25 veces.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_insurance_wasted", [
        (1, "bj_letra_pequena", "La letra pequeña",
         "Paga el seguro y que la banca no tenga blackjack.", C),
        (25, "bj_franquicia", "Con franquicia de la cuantía total",
         "Pierde el seguro 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_insured_bust", [
        (1, "bj_siniestro_total", "Parte amistoso con la banca",
         "Paga el seguro, la banca no tiene blackjack y encima te pasas.", C, True),
    ])  # fmt: skip
    # Con blackjack propio y un as de la banca también se ofrece el seguro: cobra
    # algo seguro, pero menos de media que arriesgar (ver `bot.services.blackjack`).
    a += _tiers("blackjack", "bj_natural_insured", [
        (1, "bj_letras_tesoro", "Te pasas a las Letras del Tesoro",
         "Coge el dinero par con tu blackjack: rentabilidad garantizada, aunque menor.", E),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_natural_insured_saved", [
        (1, "bj_cinturon_tirantes", "Cinturón y tirantes",
         "Asegura tu blackjack y que la banca también lo tenga.", L, True),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_natural_greed", [
        (1, "bj_lechera", "El cuento de la lechera",
         "Rechaza el seguro con blackjack y que la banca también lo tenga.", E, True),
    ])  # fmt: skip
    a += _tiers("blackjack", "bj_max_stake", [
        (1, "bj_tope", "Aquí ya no hay techo",
         "Juega una mano de 5.000 Y$ o más.", C),
        (100, "bj_tope_100", "Techo de gasto, ¿qué techo?",
         "Juega 100 manos de 5.000 Y$ o más.", R),
        (1_000, "bj_tope_1k", "Regla de gasto de Bruselas",
         "Juega 1.000 manos de 5.000 Y$ o más.", R),
    ])  # fmt: skip

    # 💰 Casino ---------------------------------------------------------------------------
    a += _tiers("casino", "casino_wagered", [
        (10_000, "wager_10k", "Apostador de barrio", "Apuesta 10.000 Y$ en total.", C),
        (100_000, "wager_100k", "Fondos Next Generation ejecutados",
         "Apuesta 100.000 Y$ en total. Ejecutado al 100 %, no como los de verdad.", R),
        (1_000_000, "wager_1m", "Ballena del Falcon", "Apuesta 1.000.000 Y$ en total.", E),
        (10_000_000, "wager_10m", "Jeque del casino",
         "Apuesta 10.000.000 Y$ en total. Rubiales ya te ha pedido la Supercopa.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("casino", "casino_win_max", [
        (1_000, "bigwin_1k", "Buen golpe (de los legales)",
         "Gana 1.000 Y$ netos en una jugada.", C),
        (10_000, "bigwin_10k", "Pelotazo", "Gana 10.000 Y$ netos en una jugada.", R),
        (100_000, "bigwin_100k", "Bote de Fabra",
         "Gana 100.000 Y$ netos en una jugada. A él le tocaba la lotería cada año.", E),
        (1_000_000, "bigwin_1m", "Rompebancas", "Gana 1.000.000 Y$ netos en una jugada.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("casino", "casino_loss_max", [
        (1_000, "bigloss_1k", "Duele como la factura de la luz",
         "Pierde 1.000 Y$ en una jugada.", C),
        (10_000, "bigloss_10k", "Eso ha dolido más que el IVA",
         "Pierde 10.000 Y$ en una jugada.", R),
        (100_000, "bigloss_100k", "Ruina a la argentina",
         "Pierde 100.000 Y$ en una jugada. Milei te presta la motosierra.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("casino", "casino_all_in", [
        (1, "allin_1", "All-in", "Apuesta todo tu saldo.", C),
        (10, "allin_10", "Sin miedo, como Abascal a caballo", "Ve all-in 10 veces.", R),
        (50, "allin_50", "YOLO, Elon", "Ve all-in 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("casino", "casino_all_in_wins", [
        (1, "allinw_1", "Todo o nada", "Gana un all-in.", R),
        (10, "allinw_10", "Nervios de acero de Merkel", "Gana 10 all-in.", E),
    ])  # fmt: skip
    a += _tiers("casino", "casino_broke", [
        (1, "broke_1", "Arruinado",
         "Quédate a cero en el casino. Bienvenido a la cartilla de racionamiento.", C),
        (10, "broke_10", "Cliente del IMV", "Quédate a cero 10 veces.", R),
    ])  # fmt: skip
    a += _tiers("casino", "casino_win_streak_max", [
        (5, "wstreak_5", "Viento a favor (no como a la Armada Invencible)",
         "Gana 5 jugadas seguidas en el casino.", C),
        (10, "wstreak_10", "Elegido por Dios, como se cree Trump", "Gana 10 jugadas seguidas.", R),
    ])  # fmt: skip
    a += _tiers("casino", "casino_loss_streak_max", [
        (5, "lstreak_5", "Mala racha de Feijóo", "Pierde 5 jugadas seguidas.", C),
        (10, "lstreak_10", "Gafe", "Pierde 10 jugadas seguidas. Ni Hillary con los correos.", C),
        (20, "lstreak_20", "Maldito, como el Brexit", "Pierde 20 jugadas seguidas.", R),
    ])  # fmt: skip
    a += _tiers("casino", "casino_bet_666", [
        (1, "bet_666", "Apuesta diabólica", "Juega exactamente 666 Y$ de una vez.", R, True),
    ])  # fmt: skip
    a += _tiers("casino", "casino_bet_42", [
        (1, "bet_42", "La respuesta", "Juega exactamente 42 Y$ de una vez.", R, True),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="versatile",
            name="Polivalente como un ministro sin cartera",
            description="Juega a la ruleta y al blackjack.",
            category="casino",
            rarity=C,
            conditions=(("roulette_spins", 1), ("bj_hands", 1)),
        )
    )

    a += _tiers("casino", "casino_wagered", [
        (100_000_000, "wager_100m", "Ballena del casino", "Apuesta 100.000.000 Y$ en total.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("casino", "casino_loss_max", [
        (1_000_000, "bigloss_1m", "Arruinado con estilo", "Pierde 1.000.000 Y$ en una jugada.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("casino", "casino_broke", [
        (50, "broke_50", "Cliente fiel del IMV", "Quédate a cero en el casino 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("casino", "casino_all_in", [
        (200, "allin_200", "Todo o nada (siempre)", "Ve all-in 200 veces.", L),
    ])  # fmt: skip
    a += _tiers("casino", "casino_win_streak_max", [
        (25, "wstreak_25", "Racha de leyenda", "Gana 25 jugadas seguidas en el casino.", R),
        (50, "wstreak_50", "Imbatible, como Putin en las urnas", "Gana 50 jugadas seguidas.", E),
    ])  # fmt: skip
    a += _tiers("casino", "casino_loss_streak_max", [
        (50, "lstreak_50", "Gafe certificado por el BOE", "Pierde 50 jugadas seguidas.", E),
    ])  # fmt: skip
    a += _tiers("casino", "casino_bet_69", [
        (1, "bet_69", "Apuesta nice", "Juega exactamente 69 Y$ de una vez.", C, True),
    ])  # fmt: skip
    a += _tiers("casino", "casino_bet_777", [
        (1, "bet_777", "Apuesta celestial", "Juega exactamente 777 Y$ de una vez.", C, True),
    ])  # fmt: skip
    a += _tiers("casino", "casino_bet_1", [
        (1, "bet_1", "Apuesta de jubilado",
         "Juega 1 Y$. El casino te agradece la visita.", C, True),
    ])  # fmt: skip
    for stake, achievement_id, name, description in (
        (
            23,
            "bet_23f",
            "¡Quieto todo el mundo!",
            "Juega exactamente 23 Y$ de una vez. Se sienten, coño.",
        ),
        (
            33,
            "bet_33",
            "Los 33 de Abascal",
            "Juega exactamente 33 Y$ de una vez. Uno por escaño, a caballo.",
        ),
        (
            44,
            "bet_44",
            "Yes we bet",
            "Juega exactamente 44 Y$ de una vez, como el presidente 44. Obama aprueba el cambio.",
        ),
        (
            47,
            "bet_47",
            "Make Casino Great Again",
            "Juega exactamente 47 Y$ de una vez, como el presidente 47. Trump lo llama la mejor "
            "apuesta de la historia.",
        ),
        (
            87,
            "bet_87",
            "Elecciones a la rusa",
            "Juega exactamente 87 Y$ de una vez: el porcentaje con el que gana Putin, "
            "cuente quien cuente.",
        ),
        (
            176,
            "bet_176",
            "Mayoría absoluta de bolsillo",
            "Juega exactamente 176 Y$ de una vez. Sin Junts, sin PNV y sin llamar a Waterloo.",
        ),
        (
            600,
            "bet_600",
            "El Seiscientos",
            "Juega exactamente 600 Y$ de una vez. Con baca, la suegra y rumbo a Benidorm.",
        ),
        (
            1975,
            "bet_1975",
            "Hecho biológico",
            "Juega exactamente 1.975 Y$ de una vez. Españoles: Franco ha muerto.",
        ),
        (
            1978,
            "bet_1978",
            "La Constitución del 78",
            "Juega exactamente 1.978 Y$ de una vez. Constitución, autonomías y cafetera nueva.",
        ),
    ):
        row = (1, achievement_id, name, description, C, True)
        a += _tiers("casino", f"casino_bet_{stake}", [row])

    # 🎰 Tragaperras ---------------------------------------------------------------------
    a += _tiers("slots", "slots_spins", [
        (1, "slots_1", "Tirar de la palanca", "Juega tu primera tirada en la tragaperras.", C),
        (100, "slots_100", "Enganchado como Puigdemont a Waterloo",
         "Juega 100 tiradas en la tragaperras.", C),
        (1_000, "slots_1k", "Zombi de la máquina", "Juega 1.000 tiradas.", R),
        (10_000, "slots_10k", "La máquina te conoce", "Juega 10.000 tiradas.", E),
        (50_000, "slots_50k", "Parte del mobiliario", "Juega 50.000 tiradas.", M),
    ])  # fmt: skip
    a += _tiers("slots", "slots_wins", [
        (10, "slotsw_10", "Tilín tilín", "Gana 10 tiradas en la tragaperras.", C),
        (100, "slotsw_100", "Luces y campanas de Navidad de Vigo",
         "Gana 100 tiradas en la tragaperras. Abel Caballero ya te ve desde el espacio.", R),
        (1_000, "slotsw_1k", "Máquina de premios", "Gana 1.000 tiradas en la tragaperras.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_jackpots", [
        (1, "jackpot_1", "¡JACKPOT!", "Saca el premio gordo de la tragaperras.", R),
        (5, "jackpot_5", "Rey del jackpot (emérito)",
         "Saca el premio gordo 5 veces. Las comisiones, en Abu Dabi.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_jackpot_max", [
        # El bote cae solo antes de 50.000 (`POT_CAP`): la meta queda por debajo del tope.
        (40_000, "jackpot_50k", "Bote gordo", "Llévate un bote de 40.000 Y$ o más.", E),
        (250_000, "jackpot_250k", "Bote histórico", "Llévate un bote de 250.000 Y$ o más.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_win_max", [
        (10_000, "slots_rain", "Lluvia de monedas", "Gana 10.000 Y$ en una tirada.", R),
        (100_000, "slots_storm", "Tormenta de monedas", "Gana 100.000 Y$ en una tirada.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_three_C", [
        (1, "slots_cherries", "Fruta prohibida", "Saca 🍒 🍒 🍒 en la línea.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_L", [
        (1, "slots_lemons", "Limonada de Kamala", "Saca 🍋 🍋 🍋. Y ríete como ella.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_G", [
        (1, "slots_grapes", "Vendimia", "Saca 🍇 🍇 🍇 en la línea.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_B", [
        (1, "slots_bells", "Ding, dong, ding", "Saca 🔔 🔔 🔔 en la línea.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_D", [
        (1, "slots_diamonds", "Diamantes en bruto", "Saca 💎 💎 💎 en la línea.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_7", [
        (1, "slots_777", "Siete vidas, como Sánchez",
         "Saca 7️⃣ 7️⃣ 7️⃣ en la línea. Lo dieron por muerto siete veces.", L),
        (5, "slots_777_5", "Lucky seven de Junts",
         "Saca 7️⃣ 7️⃣ 7️⃣ cinco veces. Siete votos que valen un Gobierno.", M),
    ])  # fmt: skip
    a.append(Achievement(
        id="slots_fruit_shop",
        name="Frutería completa",
        description="Saca un trío de cada: 🍒, 🍋, 🍇, 🔔, 💎 y 7️⃣.",
        category="slots",
        rarity=L,
        conditions=tuple((f"slots_three_{s}", 1) for s in "CLGBD7"),
    ))  # fmt: skip
    a += _tiers("slots", "slots_ldw", [
        (1, "ldw_1", "Ganar perdiendo", "Cobra un premio más pequeño que tu apuesta.", C, True),
        (100, "ldw_100", "Me sale a cuenta", "Cobra 100 premios más pequeños que tu apuesta.", C),
        (1_000, "ldw_1k", "Contabilidad creativa",
         "Cobra 1.000 premios más pequeños que tu apuesta.", E),
    ])  # fmt: skip
    # Por un pelo: desde el revamp la máquina enseña un casi-premio en un 20-25 % de
    # las tiradas (antes, un 0,8 %). A 200 tiradas al día son unos 45 al día, así que
    # 25 y 100 salían en horas. Se multiplican las metas (los `id` se quedan) para
    # mantener la rareza: 1.000 son unos 22 días (Épico), 5.000 unos 110 (Legendario)
    # y 25.000 (abajo) más de un año (Mítico). Si cambia la frecuencia, se ajustan.
    a += _tiers("slots", "slots_near_miss", [
        (1, "nearmiss_1", "Por un pelo", "Quédate a un símbolo de un premio gordo.", C),
        (1_000, "nearmiss_25", "¡Ay, bendito!",
         "Quédate 1.000 veces a un símbolo del premio gordo.", E),
        (5_000, "nearmiss_100", "La próxima sí", "Quédate 5.000 veces a un símbolo.", L),
    ])  # fmt: skip
    # El tercer rodillo frena despacio cuando los dos primeros prometen premio gordo,
    # que es la mitad de cada casi-premio de la línea: con el revamp pasa en más del
    # 10 % de las tiradas, y 100 salían en tres o cuatro días. 1.000 vuelve a ser Épico.
    a += _tiers("slots", "slots_anticipation", [
        (10, "antic_10", "Corazón en un puño", "Ve frenar despacio el tercer rodillo 10 veces.", R),
        (1_000, "antic_100", "Taquicardia",
         "Ve frenar despacio el tercer rodillo 1.000 veces.", M),
    ])  # fmt: skip
    a += _tiers("slots", "slots_scatter_tease", [
        (10, "tease_10", "Te faltó una entrada", "Saca dos 🎟️ y no el tercero 10 veces.", C, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_free_triggers", [
        (1, "free_1", "Entrada VIP", "Consigue giros gratis.", C),
        (10, "free_10", "Pase de temporada del Bernabéu de Florentino",
         "Consigue giros gratis 10 veces.", R),
        (50, "free_50", "Abono vitalicio", "Consigue giros gratis 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_free_spins", [
        (100, "freespin_100", "Barra libre", "Juega 100 giros gratis.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_free_retriggers", [
        (1, "free_again", "Presupuestos prorrogados",
         "Saca giros gratis en un giro gratis: lo de antes sigue valiendo un año más.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_hot_spins", [
        (1, "hot_1", "Al rojo vivo", "Juega una tirada con la máquina caliente.", C),
        (50, "hot_50", "Quemado como un ministro de Vivienda",
         "Juega 50 tiradas con la máquina caliente.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_hot_big", [
        (1, "hot_big", "Fuego real", "Gana ×20 o más con la máquina caliente.", C, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_wild_wins", [
        (10, "wild_10", "Comodín al rescate, como Junts", "Gana 10 tiradas gracias al 🃏.", C),
        (100, "wild_100", "Amigo del comodín", "Gana 100 tiradas gracias al 🃏.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_turbo", [
        (100, "turbo_100", "Sin frenos, como el déficit", "Juega 100 tiradas en modo turbo.", C),
        (1_000, "turbo_1k", "Turbodiésel", "Juega 1.000 tiradas en modo turbo.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_auto", [
        (1, "auto_1", "Piloto automático", "Usa Ráfaga ×10.", C),
        (50, "auto_50", "Ni lo miro, como Rajoy el Marca", "Usa Ráfaga ×10 50 veces.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_session_max", [
        (100, "session_100", "Una más y lo dejo", "Juega 100 tiradas sin cerrar la máquina.", C,
         True),
        (500, "session_500", "Sin pestañear, como Putin en la mesa larga",
         "Juega 500 tiradas sin cerrar la máquina.", E, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_pot_fed", [
        (1_000, "pot_1k", "Alimentador del bote", "Aporta 1.000 Y$ al bote.", C),
        (10_000, "pot_10k", "Mecenas del bote", "Aporta 10.000 Y$ al bote.", E),
        (100_000, "pot_100k", "El bote es tuyo (o casi)", "Aporta 100.000 Y$ al bote.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_night", [
        (1, "slots_night", "Ludopatía nocturna", "Juega a la tragaperras entre las 3 y las 6.", C,
         True),
    ])  # fmt: skip
    a.append(Achievement(
        id="casino_trilero",
        name="Trilero",
        description="Juega a la ruleta, al blackjack y a la tragaperras.",
        category="casino",
        rarity=C,
        conditions=(("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1)),
    ))  # fmt: skip

    a += _tiers("slots", "slots_wins", [
        (10_000, "slotsw_10k", "Máquina domada, como el Senado por el PP",
         "Gana 10.000 tiradas en la tragaperras.", M),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_C", [
        (100, "cherries_100", "Mercado de abastos", "Saca 100 tríos de 🍒.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_three_7", [
        (25, "slots_777_25", "Sietes de ensueño", "Saca 25 tríos de 7️⃣.", M),
    ])  # fmt: skip
    a += _tiers("slots", "slots_free_triggers", [
        (200, "free_200", "Giros infinitos", "Consigue giros gratis 200 veces.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_wild_wins", [
        (1_000, "wild_1k", "Comodín de oro", "Gana 1.000 tiradas gracias al 🃏.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_turbo", [
        (10_000, "turbo_10k", "Sin animaciones, sin alma", "Juega 10.000 tiradas en turbo.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_auto", [
        (500, "auto_500", "Manos libres", "Usa Ráfaga ×10 500 veces.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_ldw", [
        (5_000, "ldw_5k", "Pierdo ganando", "Cobra 5.000 premios más pequeños que tu apuesta.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_near_miss", [
        (25_000, "nearmiss_500", "Casi, casi, casi",
         "Quédate 25.000 veces a un símbolo del premio gordo.", M, True),
    ])  # fmt: skip

    # ▶️ Auto con animación: sesiones de hasta 25 tiradas, una a una. Las rarezas salen de
    # simular sesiones con las reglas reales (apuesta de 100 Y$, 5 sesiones al día): el 31 %
    # llega a las 25, el 65 % topa con el límite de pérdidas, el 3,7 % para por un premio
    # gordo (jackpot o ×50) y el bote solo en 1 de cada ~600 sesiones.
    a += _tiers("slots", "slots_autoplay_spins", [
        (1, "autoplay_1", "Gobierno en funciones",
         "Juega una tirada con ▶️ Auto: la máquina gobierna sola.", C),
        (100, "autoplay_100", "Decreto ley", "Juega 100 tiradas con ▶️ Auto, sin consultar.", C),
        (1_000, "autoplay_1k", "Falcon en piloto automático",
         "Juega 1.000 tiradas con ▶️ Auto.", R),
        (10_000, "autoplay_10k", "Legislatura infinita", "Juega 10.000 tiradas con ▶️ Auto.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_sessions", [
        (10, "autoplay_ses_10", "Consejo de Ministros", "Empieza 10 sesiones de ▶️ Auto.", C),
        (100, "autoplay_ses_100", "Mayoría absoluta", "Empieza 100 sesiones de ▶️ Auto.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_full", [
        (1, "autoplay_full_1", "Agotar la legislatura",
         "Completa una sesión de ▶️ Auto hasta el tope de 25 tiradas.", C),
        (10, "autoplay_full_10", "Sin moción de censura",
         "Completa 10 sesiones de ▶️ Auto hasta el tope.", R),
        (50, "autoplay_full_50", "Cuatro años sin dimitir",
         "Completa 50 sesiones de ▶️ Auto hasta el tope.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_loss_limit", [
        (1, "autoplay_loss_1", "Techo de gasto",
         "Que ▶️ Auto pare al perder 10 veces la apuesta.", C),
        (10, "autoplay_loss_10", "Regla de gasto",
         "Que ▶️ Auto pare por el límite de pérdidas 10 veces.", C),
        (100, "autoplay_loss_100", "Recortes por decreto",
         "Que ▶️ Auto pare por el límite de pérdidas 100 veces.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_bigwin", [
        (1, "autoplay_big_1", "Aterrizaje forzoso",
         "Que ▶️ Auto pare porque ha salido un premio gordo.", R),
        (10, "autoplay_big_10", "Gordo de Navidad en diferido",
         "Que ▶️ Auto pare por un premio gordo 10 veces.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_jackpot", [
        (1, "autoplay_jackpot", "El Gordo con el piloto puesto",
         "Llévate el bote mientras ▶️ Auto juega por ti.", L, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_broke", [
        (1, "autoplay_broke", "Prórroga de los presupuestos",
         "Que ▶️ Auto pare porque no te llega para otra tirada.", C, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_manual", [
        (1, "autoplay_manual_1", "Cese en funciones", "Para ▶️ Auto a mano con ⏹️ Parar.", C),
        (10, "autoplay_manual_10", "Cuestión de confianza", "Para ▶️ Auto a mano 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_quick_quit", [
        (1, "autoplay_quit", "Dimisión fulminante",
         "Para ▶️ Auto después de una sola tirada.", C, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_exit_ahead", [
        (1, "autoplay_ahead_1", "Dimitir a tiempo",
         "Para ▶️ Auto a mano con 10 tiradas o más y la sesión en positivo.", C),
        (10, "autoplay_ahead_10", "Retirada con honores",
         "Retírate a tiempo, con ganancias, 10 veces. Sin puertas giratorias.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_free", [
        (10, "autoplay_free_10", "Barra libre automática",
         "Juega 10 giros gratis con ▶️ Auto.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_autoplay_even", [
        (1, "autoplay_even", "Déficit cero",
         "Termina una sesión completa de ▶️ Auto con el neto en 0 Y$ exactos.", E, True),
    ])  # fmt: skip

    # 🎰 Tragaperras: re-giro, doble o nada, bote misterioso, giro diario, celebraciones,
    # calor que se enfría y ticket de sesión. Rarezas estimadas a 200 tiradas al día con
    # estas probabilidades (ajustarlas si cambian las tablas de `services/slots.py` y
    # volver a pasar `docs/auditoria_logros.py`): re-giro ofrecido en un 20 % de las
    # tiradas, de las que un jugador normal acepta unas 10 al día, y acertado en un
    # 10 %; volver a quedarse a uno tras fallar, un 20 %; doble o nada al 50 %, unas 10
    # series al día; GRAN PREMIO 1 de cada 75 tiradas, MEGA 1 de cada 500 y ÉPICO 1 de
    # cada 2.000 (los contadores de cada nivel no se solapan).
    a += _tiers("slots", "slots_respins", [
        (1, "respin_1", "Rescate exprés", "Re-gira el tercer rodillo tras un casi-premio.", C),
        (50, "respin_50", "Inyección de liquidez", "Re-gira el tercer rodillo 50 veces.", C),
        (500, "respin_500", "Banco malo",
         "Re-gira el tercer rodillo 500 veces. La Sareb te manda recuerdos.", E),
        (2_500, "respin_2500", "Rescate a la banca",
         "Re-gira 2.500 veces. 60.000 millones que no volverán.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_respin_saved", [
        (1, "respin_saved_1", "Rescatado", "Acierta un re-giro y cobra el premio.", C),
        (10, "respin_saved_10", "Too big to fail", "Acierta 10 re-giros.", C),
        (50, "respin_saved_50", "El rescate que sí se devolvió", "Acierta 50 re-giros.", R),
        (250, "respin_saved_250", "Ingeniería financiera", "Acierta 250 re-giros.", E),
    ])  # fmt: skip
    # Fallar k seguidos en la misma tirada: 0,2^(k-1) × 0,9^k por re-giro empezado.
    # 3 seguidos, unos 3 días; 4, unos 20; 5, unos 100; 7, años.
    a += _tiers("slots", "slots_respin_fail_chain", [
        (3, "respin_fail_3", "Esto no es un rescate",
         "Falla 3 re-giros seguidos en la misma tirada. Es una línea de crédito.", C),
        (4, "respin_fail_4", "Bankia sale a bolsa",
         "Falla 4 re-giros seguidos en la misma tirada.", R),
        (5, "respin_fail_5", "El FROB al rescate",
         "Falla 5 re-giros seguidos en la misma tirada.", R),
        (7, "respin_fail_7", "Rescate a fondo perdido",
         "Falla 7 re-giros seguidos en la misma tirada. Ni el Banco de España lo vio venir.",
         E, True),
    ])  # fmt: skip
    # Acertar tras fallar dos: 0,2² × 0,9² × 0,1 por re-giro empezado, unos 30 días.
    a += _tiers("slots", "slots_respin_bailout", [
        (1, "respin_bailout", "A la tercera va la vencida",
         "Acierta un re-giro después de fallar dos o más en la misma tirada.", C, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_respin_jackpots", [
        (1, "respin_jackpot", "Rescate con premio gordo",
         "Llévate el bote en un re-giro.", M, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_respin_price_max", [
        (1_000, "respin_price_1k", "Prima de riesgo",
         "Paga 1.000 Y$ o más por un solo re-giro.", R),
        (10_000, "respin_price_10k", "Prima de riesgo por las nubes",
         "Paga 10.000 Y$ o más por un solo re-giro.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_respin_spent", [
        (10_000, "respin_spent_10k", "Fondo de rescate", "Gasta 10.000 Y$ en re-giros.", C),
        (100_000, "respin_spent_100k", "Socializar las pérdidas",
         "Gasta 100.000 Y$ en re-giros.", E),
    ], unit="money")  # fmt: skip

    a += _tiers("slots", "slots_doubles", [
        (1, "double_1", "¿Rojo o negro?", "Juega tu primer doble o nada tras un premio.", C),
        (100, "double_100", "Doble o nada, nada o doble", "Juega 100 dobles.", C),
        (1_000, "double_1k", "Tahúr de feria", "Juega 1.000 dobles.", E),
        (10_000, "double_10k", "El rojo y el negro", "Juega 10.000 dobles. Stendhal lo sabía.",
         L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_double_wins", [
        (1, "double_win_1", "Lo doblo", "Gana un doble o nada.", C),
        (50, "double_win_50", "Ojo de halcón", "Gana 50 dobles.", C),
        (500, "double_win_500", "Pacto con el diablo", "Gana 500 dobles.", E),
        (2_000, "double_win_2k", "La banca soy yo", "Gana 2.000 dobles.", L),
    ])  # fmt: skip
    # Ganar los cinco seguidos: 1 de cada 32 series, unos 3 días a 10 series al día.
    a += _tiers("slots", "slots_double_chain", [
        (3, "double_chain_3", "Triple salto mortal", "Gana 3 dobles seguidos.", C),
        (5, "double_chain_5", "Pleno al color",
         "Gana los 5 dobles seguidos que deja la máquina.", R),
    ])  # fmt: skip
    a += _tiers("slots", "slots_double_fives", [
        (10, "double_fives_10", "Martingala a la española", "Gana 10 series de 5 dobles.", L),
        (50, "double_fives_50", "Trato con la ruina", "Gana 50 series de 5 dobles.", M),
    ])  # fmt: skip
    a += _tiers("slots", "slots_double_nada", [
        (1, "double_nada", "Fue nada", "Pierde el primer doble nada más empezar.", C),
    ])  # fmt: skip
    a += _tiers("slots", "slots_double_heartbreak", [
        (1, "double_heartbreak", "Moción de censura en el descuento",
         "Gana 4 dobles seguidos y pierde el quinto.", E, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_double_win_max", [
        (10_000, "double_rich", "Doblar la paguita", "Gana 10.000 Y$ o más en un solo doble.", E),
        (50_000, "double_rich_50k", "Doble de oro", "Gana 50.000 Y$ o más en un solo doble.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_double_loss_max", [
        (10_000, "double_ouch", "Lo que el viento se llevó",
         "Pierde 10.000 Y$ o más en un solo doble.", R),
        (50_000, "double_ouch_50k", "Y se fue en un rojo",
         "Pierde 50.000 Y$ o más en un solo doble.", E, True),
    ], unit="money")  # fmt: skip

    # Bote misterioso: cada jugador se lo lleva más o menos cada 15.000 tiradas suyas
    # (depende del tope y de cuánto juegue el servidor): unos 75 días, como el 🃏🃏🃏.
    a += _tiers("slots", "slots_mystery_pots", [
        (1, "mystery_pot_1", "Tenía que caer",
         "Llévate el bote misterioso al cruzar su tope oculto.", R),
        (3, "mystery_pot_3", "Vidente del bote", "Llévate el bote misterioso 3 veces.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_pot_drought", [
        (10_000, "drought_10k", "Le tocaba",
         "Llévate el bote cuando el servidor llevaba 10.000 tiradas sin él.", E),
        (50_000, "drought_50k", "Sequía de la España vaciada",
         "Llévate el bote cuando el servidor llevaba 50.000 tiradas sin él.", M),
    ])  # fmt: skip
    a += _tiers("slots", "slots_pot_quick", [
        (1, "pot_quick", "Ni lo calentaste",
         "Llévate el bote cuando el servidor llevaba 100 tiradas o menos sin bote.", M, True),
    ])  # fmt: skip

    # Giro diario: uno al día como mucho, así que se mide en días de calendario.
    a += _tiers("slots", "slots_daily", [
        (1, "daily_1", "La paguita de la máquina", "Juega tu giro diario gratis.", C),
        (30, "daily_30", "Funcionario de la tragaperras", "Juega el giro diario 30 días.", E),
        (100, "daily_100", "Fijo discontinuo", "Juega el giro diario 100 días.", L),
        (365, "daily_365", "Ayuda a fondo perdido", "Juega el giro diario 365 días.", M),
    ])  # fmt: skip
    a += _tiers("slots", "slots_daily_streak", [
        (7, "daily_streak_7", "Siete días a la semana",
         "Juega el giro diario 7 días seguidos y llévalo a su apuesta máxima.", R),
    ])  # fmt: skip
    # Un GRAN PREMIO o más en el giro diario: 1 de cada 60 días, unos 42 de mediana.
    a += _tiers("slots", "slots_daily_big", [
        (1, "daily_big", "Café para todos",
         "Saca un GRAN PREMIO o más en el giro diario gratis.", E, True),
    ])  # fmt: skip

    a += _tiers("slots", "slots_win_big", [
        (1, "win_big_1", "¡GRAN PREMIO!", "Gana de ×5 a ×15 lo apostado en una tirada.", C),
        (25, "win_big_25", "Fanfarria de barrio", "Saca 25 GRAN PREMIO.", C),
        (100, "win_big_100", "Vecino de la orquesta", "Saca 100 GRAN PREMIO.", R),
        (500, "win_big_500", "Ya ni miro las luces", "Saca 500 GRAN PREMIO.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_win_mega", [
        (1, "win_mega_1", "¡MEGAPREMIO!", "Gana de ×15 a ×50 lo apostado en una tirada.", C),
        (10, "win_mega_10", "Megaconstructora", "Saca 10 MEGAPREMIO.", R),
        (50, "win_mega_50", "Mega, mega, mega", "Saca 50 MEGAPREMIO.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_win_epic", [
        (1, "win_epic_1", "¡PREMIO ÉPICO!", "Gana ×50 o más lo apostado en una tirada.", R),
        (5, "win_epic_5", "Epopeya", "Saca 5 PREMIO ÉPICO.", E),
        (25, "win_epic_25", "El cantar de mío Sanxe", "Saca 25 PREMIO ÉPICO.", L),
    ])  # fmt: skip
    a.append(Achievement(
        id="slots_fanfare",
        name="Fanfarria completa",
        description="Saca un GRAN PREMIO, un MEGAPREMIO y un PREMIO ÉPICO.",
        category="slots",
        rarity=R,
        conditions=(("slots_win_big", 1), ("slots_win_mega", 1), ("slots_win_epic", 1)),
    ))  # fmt: skip
    a += _tiers("slots", "slots_tiny_big", [
        (1, "tiny_big", "Gran premio de jubilado", "Saca un GRAN PREMIO apostando 1 Y$.", C,
         True),
    ])  # fmt: skip

    # Barra de bonus: se llena cada ~80 tiradas pagadas (unas 2,5 veces al día a
    # 200 tiradas) y es regular: el 98 % de las barras tardan entre 60 y 103.
    # Tardar 100 o más pasa en ~2 % (unos 20 días, Épico); 110 o más, en ~0,1 %
    # (unos seis meses, Legendario); 60 o menos, en ~1 % (unos 40 días, Épico).
    # Simulado con `bot.services.slots.add_bonus`; si cambian sus números,
    # hay que volver a medirlo.
    a += _tiers("slots", "slots_bonus_fills", [
        (1, "bonus_1", "Tarjeta de cliente", "Llena la barra de bonus.", C),
        (10, "bonus_10", "Cliente preferente", "Llena la barra de bonus 10 veces.", R),
        (100, "bonus_100", "Socio de honor", "Llena la barra de bonus 100 veces.", E),
        (500, "bonus_500", "Abonado vitalicio", "Llena la barra de bonus 500 veces.", L),
    ])  # fmt: skip
    a += _tiers("slots", "slots_bonus_slowest", [
        (100, "bonus_slow_100", "Las obras de la M-30",
         "Llena una barra de bonus que te ha costado 100 tiradas o más.", E, True),
        (110, "bonus_slow_110", "El AVE a Extremadura",
         "Llena una barra de bonus que te ha costado 110 tiradas o más.", L, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_bonus_quick", [
        (1, "bonus_quick", "Licencia exprés",
         f"Llena la barra de bonus en {SLOTS_BONUS_QUICK} tiradas o menos.", E, True),
    ])  # fmt: skip

    a += _tiers("slots", "slots_cooled", [
        (1, "cooled_1", "Se enfrió el café", "Pierde calor de la máquina por no jugar.", C),
        (25, "cooled_25", "Me fui a por tabaco", "Pierde 25 puntos de calor por no jugar.", R),
        (100, "cooled_100", "Corriente de aire", "Pierde 100 puntos de calor por no jugar.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_cooled_max", [
        (SLOT_HEAT_MAX, "cooled_hot", "Apagar el horno",
         "Deja que la máquina caliente se enfríe del todo por no jugar.", C, True),
    ])  # fmt: skip

    a += _tiers("slots", "slots_tickets", [
        (1, "ticket_1", "Pase por caja", "Cierra la máquina y llévate el ticket.", C),
        (25, "ticket_25", "Tickets en la cartera", "Llévate 25 tickets de la tragaperras.", R),
        (250, "ticket_250", "Archivo de Simancas", "Llévate 250 tickets de la tragaperras.", E),
    ])  # fmt: skip
    a += _tiers("slots", "slots_ticket_gross_max", [
        (10_000, "ticket_gross_10k", "Premios en bruto",
         "Cierra un ticket con 10.000 Y$ en premios cobrados.", C),
        (100_000, "ticket_gross_100k", "Facturación récord",
         "Cierra un ticket con 100.000 Y$ en premios cobrados.", R),
        (500_000, "ticket_gross_500k", "Lluvia de millones (en bruto)",
         "Cierra un ticket con 500.000 Y$ en premios cobrados.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_ticket_creative", [
        (10_000, "ticket_creative_10k", "Maquillaje contable",
         "Cierra con 10.000 Y$ en premios cobrados y aun así en negativo.", C),
        (100_000, "ticket_creative_100k", "La contabilidad en B",
         "Cierra con 100.000 Y$ en premios cobrados y aun así en negativo.", R),
        (1_000_000, "ticket_creative_1m", "Las cuentas del Gran Capitán",
         "Cierra con 1.000.000 Y$ en premios cobrados y aun así en negativo.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_ticket_best", [
        (10_000, "ticket_best_10k", "Salir ganando", "Cierra un ticket con 10.000 Y$ netos o más.",
         R),
        (100_000, "ticket_best_100k", "Pelotazo inmobiliario",
         "Cierra un ticket con 100.000 Y$ netos o más.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_ticket_loss_max", [
        (10_000, "ticket_loss_10k", "Agujero contable",
         "Cierra un ticket perdiendo 10.000 Y$ o más.", C),
        (100_000, "ticket_loss_100k", "El agujero de las pensiones",
         "Cierra un ticket perdiendo 100.000 Y$ o más.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("slots", "slots_ticket_taxed", [
        (1, "ticket_taxed", "Ganas a la máquina, pierdes con Hacienda",
         "Cierra con más premios que apuestas y aun así en negativo.", R, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_ticket_zero", [
        (1, "ticket_zero", "Equilibrio presupuestario",
         "Cierra tras 10 tiradas o más con el neto en 0 justo. Estabilidad presupuestaria.", R,
         True),
    ])  # fmt: skip
    # Diez tiradas seguidas sin premio, con un 40 % de tiradas premiadas: 1 de cada 165
    # sesiones de 10 o más; unos 30 días a 5 sesiones al día.
    a += _tiers("slots", "slots_ticket_dry", [
        (1, "ticket_dry", "Ni para pipas",
         "Cierra tras 10 tiradas o más sin haber cobrado ni un premio.", E, True),
    ])  # fmt: skip
    a += _tiers("slots", "slots_ticket_quick", [
        (1, "ticket_quick", "Visita de médico", "Cierra la máquina tras una sola tirada.", C,
         True),
    ])  # fmt: skip
    a.append(Achievement(
        id="slots_feria",
        name="La feria entera",
        description="Re-gira, juega un doble, usa el giro diario, deja enfriar la máquina "
        "y llévate un ticket.",
        category="slots",
        rarity=C,
        conditions=(
            ("slots_respins", 1), ("slots_doubles", 1), ("slots_daily", 1),
            ("slots_cooled", 1), ("slots_tickets", 1),
        ),
    ))  # fmt: skip

    # 🌋 Botes (de momento, volcan) ---------------------------------------------------------
    a += _tiers("botes", "botes_spins", [
        (1, "botes_1", "Hold & win", "Juega tu primera tirada en el Volcán.",
         C),
        (100, "botes_100", "Coleccionista de monedas de Bárcenas",
         "Juega 100 tiradas en los botes.", C),
        (1_000, "botes_1k", "Maletín al hombro", "Juega 1.000 tiradas en los botes.",
         R),
        (10_000, "botes_10k", "Socio de la casa", "Juega 10.000 tiradas en los botes.",
         L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_spins_volcan", [
        (100, "botes_timanfaya", "Turista en Timanfaya", "Juega 100 tiradas en el Volcán.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_collects", [
        (1, "botes_collect", "Recogida", "Recoge las monedas con el recogedor.", C),
        (50, "botes_collect_50", "Barrendero de monedas", "Haz 50 recogidas.", C),
        (500, "botes_collect_500", "Aspiradora de comisiones", "Haz 500 recogidas.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_double_collect", [
        (1, "botes_double", "Por las dos puntas", "Recoge con recogedor en el rodillo 1 y el 5.",
         C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_near_miss", [
        (25, "botes_near", "Monedas al viento",
         "Deja 25 veces un buen puñado de monedas sin recoger.", C, True),
    ])  # fmt: skip
    a += _tiers("botes", "botes_ways_5", [
        (1, "botes_five", "De punta a punta", "Gana con un símbolo en los cinco rodillos.", C),
        (25, "botes_five_25", "Ways a mansalva", "Gana 25 veces con los cinco rodillos.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_wild_wins", [
        (50, "botes_wild", "Comodín de confianza, como un asesor de Moncloa",
         "Gana 50 tiradas con ayuda del comodín.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_chips", [
        (25, "botes_chips", "Fichas al bote", "Saca 25 fichas de bote en el juego base.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonuses", [
        (1, "botes_bonus", "¡Maletín lleno!", "Llena un maletín y juega su bonus.", C),
        (25, "botes_bonus_25", "Abonado al bonus de Abengoa", "Juega 25 bonus.", R),
        (100, "botes_bonus_100", "El bonus me conoce", "Juega 100 bonus.", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_green", [
        (1, "botes_green", "Verde que te quiero Vox", "Juega un bonus verde.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_blue", [
        (1, "botes_blue", "Azul gaviota", "Juega un bonus azul. Génova te saluda.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_red", [
        (1, "botes_red", "Rojo pasión socialista", "Juega un bonus rojo.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_grand", [
        (1, "botes_grand_bonus", "Fin del mundo, según Kim",
         "Juega el gran bonus de los tres colores.", C),
        (10, "botes_grand_bonus_10", "Apocalipsis en bucle", "Juega 10 grandes bonus.", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_mini", [
        (1, "botes_mini", "MINI", "Gana el bote MINI (10 monedas en un bonus).", C),
        (10, "botes_mini_10", "Minis en serie, como los partidos de la izquierda",
         "Gana 10 botes MINI.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_major", [
        (1, "botes_major", "MAJOR", "Gana el bote MAJOR (15 monedas en un bonus).", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_grand", [
        (1, "botes_grand", "GRAND", "Llena la pantalla de monedas y llévate el GRAND.", M),
    ])  # fmt: skip
    a += _tiers("botes", "botes_almost_grand", [
        (1, "botes_19", "Por una moneda", "Acaba un bonus con 19 monedas.", L, True),
    ])  # fmt: skip
    a += _tiers("botes", "botes_mult_max", [
        (5, "botes_mult_5", "Multiplicador de asesores ×5",
         "Lleva el multiplicador de un bonus a ×5.", R),
        (10, "botes_mult_10", "Multiplicador de ministerios ×10",
         "Lleva el multiplicador de un bonus a ×10. Veintidós ministerios se quedan cortos.",
         L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_instant", [
        (10, "botes_instant", "¡Pum, por dos!", "Saca 10 multiplicadores inmediatos.", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_maximizer", [
        (1, "botes_max", "Maximalista de Puigdemont", "Saca un maximizador de botes.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_mystery", [
        (25, "botes_mystery", "Misterio resuelto", "Destapa 25 símbolos misteriosos.", E),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_max", [
        (10_000, "botes_bonus_10k", "Maletín de billetes", "Gana 10.000 Y$ en un bonus.", R),
        (100_000, "botes_bonus_100k", "Maletín de lingotes", "Gana 100.000 Y$ en un bonus.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("botes", "botes_win_max", [
        (10_000, "botes_win_10k", "Lluvia de lava", "Gana 10.000 Y$ en una tirada base.", R),
    ], unit="money")  # fmt: skip
    a += _tiers("botes", "botes_turbo", [
        (100, "botes_turbo", "Turbo en la mina de litio", "Juega 100 tiradas en turbo.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_auto", [
        (10, "botes_auto", "Que trabaje la máquina", "Usa Auto ×10 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("botes", "botes_night", [
        (1, "botes_night", "Erupción de madrugada", "Juega a los botes entre las 3 y las 6.", C,
         True),
    ])  # fmt: skip

    a += _tiers("botes", "botes_spins", [
        (50_000, "botes_50k", "Vulcanólogo", "Juega 50.000 tiradas en los botes.", M),
    ])  # fmt: skip
    a += _tiers("botes", "botes_collects", [
        (5_000, "botes_collect_5k", "Recogepelotas del volcán", "Haz 5.000 recogidas.", L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_double_collect", [
        (25, "botes_double_25", "Pinza doble PP-Vox", "Recoge con dos recogedores 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_near_miss", [
        (250, "botes_near_250", "Monedas que se escapan",
         "Deja 250 puñados de monedas sin recoger.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_ways_5", [
        (250, "botes_five_250", "Cinco de cinco", "Gana 250 veces con los cinco rodillos.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_wild_wins", [
        (500, "botes_wild_500", "Comodín volcánico", "Gana 500 tiradas con ayuda del comodín.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_chips", [
        (250, "botes_chips_250", "Saco de fichas", "Saca 250 fichas de bote.", R),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonuses", [
        (500, "botes_bonus_500", "Adicto al maletín", "Juega 500 bonus.", L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_bonus_grand", [
        (50, "botes_grand_bonus_50", "Tricolor", "Juega 50 grandes bonus.", L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_mini", [
        (100, "botes_mini_100", "Coleccionista de MINI", "Gana 100 botes MINI.", L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_major", [
        (10, "botes_major_10", "MAJOR de MAJORES", "Gana 10 botes MAJOR.", L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_instant", [
        (100, "botes_instant_100", "Lluvia de multiplicadores",
         "Saca 100 multiplicadores inmediatos.", L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_mystery", [
        (250, "botes_mystery_250", "Cuarto milenio del volcán",
         "Destapa 250 símbolos misteriosos.", L),
    ])  # fmt: skip
    a += _tiers("botes", "botes_maximizer", [
        (25, "botes_max_25", "Botes al máximo, como el precio del alquiler",
         "Saca 25 maximizadores de botes.", L),
    ])  # fmt: skip

    # 🚀 Crash --------------------------------------------------------------------------
    a += _tiers("crash", "crash_rounds", [
        (1, "crash_1", "Despegue",
         "Juega tu primera ronda de Crash. Elon ya te ha cobrado el billete.", C),
        (100, "crash_100", "Piloto del Falcon", "Juega 100 rondas de Crash.", R),
        (1_000, "crash_1k", "Astronauta de Musk",
         "Juega 1.000 rondas de Crash. Marte, en 2016. O en 2030. O ya veremos.", E),
        (5_000, "crash_5k", "Vives en órbita, como Pedro Duque", "Juega 5.000 rondas de Crash.", L),
    ])  # fmt: skip
    a += _tiers("crash", "crash_cashouts", [
        (10, "crashc_10", "Paracaidista de Bruselas", "Retírate a tiempo 10 veces.", C),
        (100, "crashc_100", "Saltador profesional de partidos",
         "Retírate a tiempo 100 veces. Ni un tránsfuga cambia tanto de sitio.", R),
        (1_000, "crashc_1k", "Siempre a tiempo, como Biden no",
         "Retírate a tiempo 1.000 veces.", L),
    ])  # fmt: skip
    a += _tiers("crash", "crash_cashout_max", [
        (200, "crash_x2", "Duplicado, como el IPC del aceite", "Retírate en 2x o más.", C),
        (1_000, "crash_x10", "Diez veces más que el SMI", "Retírate en 10x o más.", R),
        (5_000, "crash_x50", "Estratosfera de Elon", "Retírate en 50x o más.", E),
        (10_000, "crash_x100", "Centenario del Seiscientos",
         "Retírate en 100x o más. Ni el Seiscientos subía tanto el puerto.", L),
        (100_000, "crash_x1000", "Hasta la Luna (y sin Starship)",
         "Retírate en 1.000x. Musk te pide el truco.", M, True),
    ])  # fmt: skip
    a += _tiers("crash", "crash_win_max", [
        (10_000, "crash_fuel", "Combustible a precio de Putin",
         "Gana 10.000 Y$ en una ronda de Crash.", R),
        (100_000, "crash_gold", "Cohete de oro, como el inodoro de Trump",
         "Gana 100.000 Y$ en una ronda de Crash.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("crash", "crash_auto", [
        (10, "crasha_10", "Control de crucero de Tesla", "Cobra 10 veces con el auto-retiro.", C),
        (100, "crasha_100", "Sin manos, como un Tesla en la autopista",
         "Cobra 100 veces con el auto-retiro.", E),
    ])  # fmt: skip
    a += _tiers("crash", "crash_close", [
        (1, "crash_close", "Salto in extremis", "Retírate a menos de un 5 % de la explosión.", C),
        (10, "crash_close_10", "Nervios de titanio, los de Netanyahu en el juicio",
         "Retírate por los pelos 10 veces.", R),
    ])  # fmt: skip
    a += _tiers("crash", "crash_last_out", [
        (1, "crash_last", "El último en saltar del Titanic",
         "Sé el último en retirarse con más gente aún dentro.", R),
    ])  # fmt: skip
    a += _tiers("crash", "crash_instant", [
        (1, "crash_ramp", "Ni despegó", "Pierde en una ronda que explota en 1,00x.", C, True),
    ])  # fmt: skip
    a += _tiers("crash", "crash_greedy", [
        (1, "crash_greedy", "La avaricia rompe el saco",
         "Pierde en una ronda que llegó a 10x. Que se lo digan a Lehman Brothers.", C, True),
    ])  # fmt: skip
    a += _tiers("crash", "crash_moon", [
        (1, "crash_moon", "Testigo lunar, como Kubrick",
         "Juega una ronda que llega a 100x.", R, True),
    ])  # fmt: skip
    a += _tiers("crash", "crash_party_max", [
        (3, "crash_crew", "Tripulación del Consejo de Ministros",
         "Juega una ronda con 3 personas.", C),
        (6, "crash_charter", "Vuelo chárter a Waterloo", "Juega una ronda con 6 personas.", R),
    ])  # fmt: skip

    a += _tiers("crash", "crash_rounds", [
        (10_000, "crash_10k", "Controlador aéreo en huelga", "Juega 10.000 rondas de Crash.", M),
    ])  # fmt: skip
    a += _tiers("crash", "crash_cashouts", [
        (2_500, "crashc_2500", "Paracaidista profesional", "Retírate a tiempo 2.500 veces.", L),
    ])  # fmt: skip
    a += _tiers("crash", "crash_auto", [
        (1_000, "crasha_1k", "Piloto automático del Falcon",
         "Cobra 1.000 veces con el auto-retiro.", L),
    ])  # fmt: skip
    a += _tiers("crash", "crash_close", [
        (50, "crash_close_50", "Saltar en marcha", "Retírate por los pelos 50 veces.", L),
    ])  # fmt: skip
    a += _tiers("crash", "crash_instant", [
        (10, "crash_ramp_10", "Despegue abortado", "Pierde en 10 rondas que explotan en 1,00x.", E),
    ])  # fmt: skip
    a += _tiers("crash", "crash_moon", [
        (10, "crash_moon_10", "Turista espacial", "Juega 10 rondas que llegan a 100x.", E),
    ])  # fmt: skip
    a += _tiers("crash", "crash_cash_low", [
        (25, "crash_low_25", "Cobarde profesional", "Retírate en 1,10x o menos 25 veces.", C),
        (250, "crash_low_250", "Funcionario del Crash", "Retírate en 1,10x o menos 250 veces.", R),
    ])  # fmt: skip
    a += _tiers("crash", "crash_missed_moon", [
        (1, "missed_moon", "El cohete se fue sin ti",
         "Retírate antes del 2x en una ronda que pasa de 100x.", R, True),
    ])  # fmt: skip

    # 💣 Minas --------------------------------------------------------------------------
    a += _tiers("mines", "mines_games", [
        (1, "mines_1", "Zapador", "Juega tu primera partida de Minas. Sin ayuda de la OTAN.", C),
        (100, "mines_100", "Artificiero", "Juega 100 partidas de Minas.", C),
        (1_000, "mines_1k", "Campo minado de la política española",
         "Juega 1.000 partidas de Minas.", E),
    ])  # fmt: skip
    a += _tiers("mines", "mines_gems", [
        (100, "gems_100", "Buscador de litio", "Destapa 100 diamantes.", C),
        (1_000, "gems_1k", "Minero asturiano",
         "Destapa 1.000 diamantes. El carbón ya lo cerró Bruselas.", R),
        (10_000, "gems_10k", "Mina de diamantes sin multinacional", "Destapa 10.000 diamantes.", E),
    ])  # fmt: skip
    a += _tiers("mines", "mines_cashouts", [
        (10, "minesc_10", "Retirada a tiempo, como Iglesias de la política",
         "Cobra 10 partidas de Minas.", C),
        (100, "minesc_100", "Pulso firme de cirujano de la sanidad pública",
         "Cobra 100 partidas de Minas.", R),
    ])  # fmt: skip
    a += _tiers("mines", "mines_booms", [
        (1, "boom_1", "Boom", "Pisa una mina. Como un ministro con un micro abierto.", C),
        (100, "boom_100", "Saltaminas de la oposición", "Pisa 100 minas.", R),
    ])  # fmt: skip
    a += _tiers("mines", "mines_first_boom", [
        (1, "boom_first", "A la primera",
         "Pisa una mina justo después de la casilla segura.", C, True),
    ])  # fmt: skip
    a += _tiers("mines", "mines_almost", [
        (1, "boom_almost", "Tan cerca", "Pisa una mina cuando solo quedaba una casilla buena.",
         E, True),
    ])  # fmt: skip
    a += _tiers("mines", "mines_mult_max", [
        (500, "mines_x5", "Cinco veces más que la paguita", "Cobra en ×5 o más.", C),
        (2_000, "mines_x20", "Veinte veces, como los asesores de un ministro",
         "Cobra en ×20 o más.", R),
        (10_000, "mines_x100", "Cien veces, como la deuda pública", "Cobra en ×100 o más.", L),
        (100_000, "mines_x1000", "Mil veces", "Cobra en ×1.000 o más.", M),
        (1_000_000, "mines_x10k", "Diez mil veces, como un caso de corrupción",
         "Cobra en ×10.000 o más.", M),
    ])  # fmt: skip
    a += _tiers("mines", "mines_win_max", [
        (10_000, "mines_rich", "Veta de oro del Banco de España",
         "Gana 10.000 Y$ en una partida de Minas.", R),
        (100_000, "mines_richer", "Filón de las mascarillas",
         "Gana 100.000 Y$ en una partida de Minas. Koldo quiere ser tu socio.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("mines", "mines_24", [
        (1, "mines_24", "Ruleta rusa al revés", "Gana con 12 minas, el máximo.", C),
    ])  # fmt: skip
    a += _tiers("mines", "mines_clear", [
        (1, "mines_clear", "Desminado", "Destapa todas las casillas buenas.", E),
    ])  # fmt: skip
    a += _tiers("mines", "mines_clear_hard", [
        (1, "mines_clear_5", "Artificiero de élite",
         "Destapa todas las casillas buenas con 5 minas o más.", M, True),
    ])  # fmt: skip
    a += _tiers("mines", "mines_streak_max", [
        (10, "mines_streak_10", "Pisando firme, como Ayuso",
         "Destapa 10 casillas en una partida.", C),
        (15, "mines_streak_15", "Detector humano de micrófonos",
         "Destapa 15 casillas en una partida.", C),
        (20, "mines_streak_20", "Pies de plomo de Feijóo",
         "Destapa 20 casillas en una partida.", R),
    ])  # fmt: skip
    a += _tiers("mines", "mines_random", [
        (50, "mines_dice", "Que decida el destino", "Destapa 50 casillas con 🎲.", C),
    ])  # fmt: skip
    a += _tiers("mines", "mines_games", [
        (5_000, "mines_5k", "Campo de minas de Moncloa", "Juega 5.000 partidas de Minas.", L),
    ])  # fmt: skip
    a += _tiers("mines", "mines_gems", [
        (50_000, "gems_50k", "Joyería de la Gran Vía", "Destapa 50.000 diamantes.", M),
    ])  # fmt: skip
    a += _tiers("mines", "mines_cashouts", [
        (1_000, "minesc_1k", "Artificiero jefe del CNI", "Cobra 1.000 partidas de Minas.", L),
    ])  # fmt: skip
    a += _tiers("mines", "mines_booms", [
        (1_000, "boom_1k", "Fuegos artificiales de Kim",
         "Pisa 1.000 minas. Feliz cumpleaños, líder.", E),
    ])  # fmt: skip
    a += _tiers("mines", "mines_clear", [
        (10, "mines_clear_10", "Campo despejado", "Destapa todas las casillas buenas 10 veces.", M),
    ])  # fmt: skip
    a += _tiers("mines", "mines_random", [
        (500, "mines_dice_500", "Que decida el dado", "Destapa 500 casillas con 🎲.", E),
    ])  # fmt: skip
    a += _tiers("mines", "mines_cash_one", [
        (25, "cash_one_25", "Con uno me vale", "Cobra 25 veces tras un solo diamante.", C),
        (250, "cash_one_250", "Diamante y a casa", "Cobra 250 veces tras un solo diamante.", R),
    ])  # fmt: skip
    a += _tiers("mines", "mines_greedy", [
        (1, "mines_greedy", "El que mucho abarca",
         "Pisa una mina con ×10 o más ya ganado.", R, True),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="mines_all_levels",
            name="Todos los niveles de riesgo",
            description="Juega con cada número de minas, de 1 a 12.",
            category="mines",
            rarity=R,
            conditions=tuple((f"mines_level_{n}", 1) for n in range(1, MINES_MAX + 1)),
        )
    )
    a += _tiers("mines", f"mines_level_{MINES_MAX}", [
        (100, "mines_12_100", "Kamikaze del 12", "Juega 100 partidas con 12 minas.", E),
    ])  # fmt: skip

    # 🪙 Cara o cruz -----------------------------------------------------------------------
    a += _tiers("coin", "coin_games", [
        (1, "moneda_1", "Echarlo a suertes", "Juega tu primera partida a cara o cruz.", C),
        (100, "moneda_100", "Decisiones de Estado", "Juega 100 partidas a cara o cruz.", C),
        (1_000, "moneda_1k", "Así se decide en Moncloa", "Juega 1.000 partidas a cara o cruz.", E),
        (10_000, "moneda_10k", "Política monetaria",
         "Juega 10.000 partidas a cara o cruz. El Banco de España pregunta por ti.", L),
    ])  # fmt: skip
    a += _tiers("coin", "coin_flips", [
        (100, "monedal_100", "Pulgar de oro", "Lanza la moneda 100 veces.", C),
        (1_000, "monedal_1k", "Tendinitis del pulgar", "Lanza la moneda 1.000 veces.", R),
        (5_000, "monedal_5k", "Fábrica Nacional de Moneda y Timbre",
         "Lanza la moneda 5.000 veces.", E),
        (20_000, "monedal_20k", "La máquina de hacer euros", "Lanza la moneda 20.000 veces.", L),
    ])  # fmt: skip
    a += _tiers("coin", "coin_wins", [
        (50, "monedaw_50", "Echador de cartas", "Acierta 50 lanzamientos.", C),
        (500, "monedaw_500", "Mejor que el CIS", "Acierta 500 lanzamientos.", R),
        (2_500, "monedaw_2500", "Tezanos te pide consejo", "Acierta 2.500 lanzamientos.", E),
        (10_000, "monedaw_10k", "Oráculo de Moncloa", "Acierta 10.000 lanzamientos.", L),
    ])  # fmt: skip
    a += _tiers("coin", "coin_cashouts", [
        (10, "monedac_10", "Más vale pájaro en mano", "Cobra 10 partidas a cara o cruz.", C),
        (100, "monedac_100", "Hucha de cerdito", "Cobra 100 partidas a cara o cruz.", R),
        (800, "monedac_800", "Plan de pensiones", "Cobra 800 partidas a cara o cruz.", E),
        (3_000, "monedac_3k", "Banca Sanxe", "Cobra 3.000 partidas a cara o cruz.", L),
    ])  # fmt: skip
    a += _tiers("coin", "coin_losses", [
        (1, "monedap_1", "Salió la otra", "Falla tu primer lanzamiento.", C),
        (10, "monedap_10", "Mal fario de Hillary", "Falla 10 lanzamientos.", C),
        (100, "monedap_100", "Ni a cara ni a cruz, como Sumar", "Falla 100 lanzamientos.", C),
        (1_000, "monedap_1k", "Manual de resistencia numismático",
         "Falla 1.000 lanzamientos y sigue jugando como si nada.", E),
    ])  # fmt: skip
    a += _tiers("coin", "coin_edges", [
        (1, "moneda_canto", "De canto", "Que la moneda caiga de canto. Para Hacienda.", C),
        (10, "moneda_canto_10", "Inspección rutinaria",
         "Que la moneda caiga de canto 10 veces.", R),
        (50, "moneda_canto_50", "Expediente abierto en la Agencia Tributaria",
         "Que la moneda caiga de canto 50 veces.", E),
        (150, "moneda_canto_150", "Perro Sanxe te tiene en favoritos",
         "Que la moneda caiga de canto 150 veces.", L),
    ])  # fmt: skip
    a += _tiers("coin", "coin_streak_max", [
        (3, "moneda_r3", "Racha de tres, como un tripartito",
         "Acierta 3 seguidas en una partida.", C),
        (5, "moneda_r5", "Repóker de caras (o cruces)", "Acierta 5 seguidas en una partida.", R),
        (7, "moneda_r7", "Siete y sin despeinarse", "Acierta 7 seguidas en una partida.", E),
        (9, "moneda_r9", "A una de la gloria", "Acierta 9 seguidas en una partida.", M),
    ])  # fmt: skip
    a += _tiers("coin", "coin_cash_mult_max", [
        (4, "moneda_x4", "Doble de doble, como el sueldo de un ministro", "Cobra en ×4 o más.", C),
        (16, "moneda_x16", "La paguita", "Cobra en ×16 o más.", C),
        (64, "moneda_x64", "Fondos europeos", "Cobra en ×64 o más.", E),
        (256, "moneda_x256", "La banca siempre gana (hoy tú)", "Cobra en ×256 o más.", M),
    ])  # fmt: skip
    a += _tiers("coin", "coin_finish", [
        (1, "moneda_oro", "La moneda de oro",
         f"Acierta {COIN_MAX_FLIPS} seguidas y cobra ×1.024. Sale en el BOE.", M),
    ])  # fmt: skip
    a += _tiers("coin", "coin_win_max", [
        (10_000, "moneda_rich", "Lluvia de euros", "Gana 10.000 Y$ en una partida a cara o cruz.",
         E),
        (100_000, "moneda_richer", "Hacienda somos todos (tú más)",
         f"Gana 100.000 Y$ en una partida a cara o cruz. {TAX_COLLECTOR} ya ha hecho números.",
         M),
    ])  # fmt: skip
    a += _tiers("coin", "coin_lost_big", [
        (1, "moneda_rescate", "Del Falcon al autobús",
         "Pierde con ×16 o más en juego.", C, True),
        (10, "moneda_rescate_10", "Burbuja inmobiliaria",
         "Pierde con ×16 o más en juego 10 veces.", E),
    ])  # fmt: skip
    a += _tiers("coin", "coin_edge_big", [
        (1, "moneda_canto_gordo", "Embargo preventivo",
         "Que caiga de canto con ×8 o más en juego.", E, True),
    ])  # fmt: skip
    a += _tiers("coin", "coin_gallina", [
        (25, "moneda_gallina", "Prudencia fiscal", "Cobra 25 veces tras un solo acierto.", C),
        (250, "moneda_gallina_250", "Oposición aprobada a la prudencia",
         "Cobra 250 veces tras un solo acierto. Nada de riesgos.", E),
    ])  # fmt: skip
    a += _tiers("coin", "coin_first_fail", [
        (10, "moneda_ni_una", "Ni una, como Feijóo en la investidura",
         "Falla el primer lanzamiento de 10 partidas.", C),
        (250, "moneda_ni_una_250", "Mal fario con sello oficial",
         "Falla el primer lanzamiento de 250 partidas.", R),
    ])  # fmt: skip
    a += _tiers("coin", "coin_next_edge", [
        (1, "moneda_librado", "Te has librado de Hacienda",
         "Cobra justo cuando la siguiente iba a caer de canto.", R, True),
    ])  # fmt: skip
    a += _tiers("coin", "coin_loyal_cara", [
        (1, "moneda_monarquico", "Monárquico de toda la vida",
         "Acierta 5 o más seguidas pidiendo siempre cara.", R, True),
    ])  # fmt: skip
    a += _tiers("coin", "coin_loyal_cruz", [
        (1, "moneda_falcon", "Tarjeta de embarque del Falcon",
         "Acierta 5 o más seguidas pidiendo siempre cruz.", R, True),
    ])  # fmt: skip
    a += _tiers("coin", "coin_flipflop", [
        (1, "moneda_chaquetero", "No es un cambio de opinión",
         "Acierta 4 o más seguidas cambiando de lado en cada lanzamiento.", R, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="moneda_equidistante",
        name="Equidistante",
        description="Acierta 100 lanzamientos pidiendo cara y 100 pidiendo cruz.",
        category="coin",
        rarity=R,
        conditions=(("coin_wins_cara", 100), ("coin_wins_cruz", 100)),
    ))  # fmt: skip
    a += _tiers("coin", "coin_edge_lost", [
        (5_000, "moneda_contribuyente", "Contribuyente de pie",
         "Pierde 5.000 Y$ en monedas de canto.", E),
        (100_000, "moneda_mecenas", "Mecenas involuntario del Estado",
         "Pierde 100.000 Y$ en monedas de canto.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("coin", "coin_night", [
        (1, "moneda_madrugada", "Insomnio de Moncloa",
         "Juega a cara o cruz de madrugada (de 2 a 6).", C),
    ])  # fmt: skip
    a += _tiers("coin", "coin_hispanidad", [
        (1, "moneda_hispanidad", "Cara de la Hispanidad",
         "Juega a cara o cruz el 12 de octubre.", C),
    ])  # fmt: skip
    a += _tiers("coin", "coin_nochevieja", [
        (1, "moneda_uvas", "Cara o cruz y las uvas", "Juega a cara o cruz en Nochevieja.", C),
    ])  # fmt: skip
    a += _tiers("coin", "coin_friday13", [
        (1, "moneda_viernes13", "Moneda negra", "Pierde a cara o cruz un viernes 13.", C, True),
    ])  # fmt: skip
    a += _tiers("coin", "coin_cash_666", [
        (1, "moneda_666", "La moneda de la bestia", "Cobra exactamente 666 Y$.", C, True),
    ])  # fmt: skip
    a += _tiers("coin", "coin_first_edge", [
        (1, "moneda_canto_primera", "Ni empezar",
         "Que la moneda caiga de canto en el primer lanzamiento de la partida.", C, True),
    ])  # fmt: skip

    # 🚌 Autobús --------------------------------------------------------------------------
    a += _tiers("bus", "bus_games", [
        (1, "bus_1", "Sube, que no muerde", "Juega tu primera partida al autobús.", C),
        (100, "bus_100", "Abono transporte de Óscar Puente", "Juega 100 partidas al autobús.", C),
        (1_000, "bus_1k", "Abono gratis de Renfe",
         "Juega 1.000 partidas al autobús. Gratis no es, pero lo parece.", E),
        (10_000, "bus_10k", "Conductor de la EMT", "Juega 10.000 partidas al autobús.", L),
    ])  # fmt: skip
    a += _tiers("bus", "bus_hands", [
        (100, "busm_100", "Billete sencillo de Rodalies", "Juega 100 manos al autobús.", C),
        (1_000, "busm_1k", "Bonobús de diez viajes", "Juega 1.000 manos al autobús.", R),
        (10_000, "busm_10k", "La Global de punta a punta", "Juega 10.000 manos al autobús.", L),
        (50_000, "busm_50k", "Más kilómetros que el Falcon",
         "Juega 50.000 manos al autobús.", M),
    ])  # fmt: skip
    a += _tiers("bus", "bus_wins", [
        (50, "busw_50", "Cartomante de parada", "Acierta 50 manos al autobús.", C),
        (500, "busw_500", "Echadora de cartas del Puerto", "Acierta 500 manos al autobús.", R),
        (5_000, "busw_5k", "Tezanos al volante",
         "Acierta 5.000 manos al autobús. El CIS quiere tu método.", L),
        (20_000, "busw_20k", "Vidente de la DGT", "Acierta 20.000 manos al autobús.", M),
    ])  # fmt: skip
    a += _tiers("bus", "bus_cashouts", [
        (10, "busc_10", "Próxima parada: mi casa", "Cobra 10 partidas al autobús.", C),
        (100, "busc_100", "Picar el billete", "Cobra 100 partidas al autobús.", R),
        (1_000, "busc_1k", "Concesión de la línea", "Cobra 1.000 partidas al autobús.", L),
    ])  # fmt: skip
    a += _tiers("bus", "bus_losses", [
        (1, "busp_1", "Se te escapó la guagua", "Pierde tu primera partida al autobús.", C),
        (10, "busp_10", "Huelga de transporte",
         "Pierde 10 partidas al autobús. Servicios mínimos del 0 %.", C),
        (100, "busp_100", "Servicios mínimos",
         "Pierde 100 partidas al autobús. Óscar Puente lo achaca a un robo de cable.", C),
        (1_000, "busp_1k", "Manual de resistencia en la marquesina",
         "Pierde 1.000 partidas al autobús y sigue esperando la guagua.", E),
    ])  # fmt: skip
    a += _tiers("bus", "bus_complete", [
        (1, "bus_completo", "Fin de trayecto", "Acierta las cuatro manos del autobús.", C),
        (10, "bus_completo_10", "Abono anual del Imserso", "Acierta las cuatro manos 10 veces.", R),
        (100, "bus_completo_100", "Jefe de cocheras", "Acierta las cuatro manos 100 veces.", L),
    ])  # fmt: skip
    a += _tiers("bus", "bus_turned", [
        (1, "bus_vuelta", "Billete de ida y vuelta",
         "Completa el autobús y acierta también la vuelta.", R),
        (10, "bus_vuelta_10", "Línea circular", "Acierta la vuelta 10 veces.", L),
    ])  # fmt: skip
    a += _tiers("bus", "bus_turn_lost", [
        (1, "bus_vuelta_perdida", "Doble o nada: nada",
         "Completa el autobús, juégatelo a la vuelta y piérdelo todo.", R, True),
    ])  # fmt: skip
    a += _tiers("bus", "bus_mult_max", [
        (500, "bus_x5", "Suplemento de equipaje de Ryanair", "Cobra al autobús en ×5 o más.", C),
        (2_000, "bus_x20", "Primera clase en la guagua", "Cobra al autobús en ×20 o más.", C),
        (10_000, "bus_x100", "El autobús de los fondos europeos",
         "Cobra al autobús en ×100 o más.", E),
        (50_000, "bus_x500", "Autobús oficial de Moncloa", "Cobra al autobús en ×500 o más.", M),
        (200_000, "bus_x2000", "La guagua dorada", "Cobra al autobús en ×2.000 o más.", M),
    ])  # fmt: skip
    a += _tiers("bus", "bus_win_max", [
        (10_000, "bus_rich", "Billete premiado de la ONCE",
         "Gana 10.000 Y$ en una partida al autobús.", E),
        (100_000, "bus_richer", "Concesión pública a dedo",
         "Gana 100.000 Y$ en una partida al autobús. La UCO toma nota.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("bus", "bus_win_igual", [
        (1, "bus_igual", "Clavado, como el Rey a su padre", "Acierta «igual» al autobús.", R),
        (10, "bus_igual_10", "Gemelos del Congreso", "Acierta «igual» 10 veces.", E),
        (50, "bus_igual_50", "Fotocopiadora del BOE", "Acierta «igual» 50 veces.", M),
    ])  # fmt: skip
    a += _tiers("bus", "bus_win_poste", [
        (1, "bus_poste", "Al palo", "Acierta «poste» al autobús.", R),
        (10, "bus_poste_10", "Parada de la marquesina", "Acierta «poste» 10 veces.", L),
        (50, "bus_poste_50", "Francotirador de Pegasus", "Acierta «poste» 50 veces.", M),
    ])  # fmt: skip
    a += _tiers("bus", "bus_suit_wins", [
        (10, "buss_10", "Echar las cartas, como Rappel", "Acierta el palo 10 veces.", R),
        (100, "buss_100", "Tarotista de madrugada", "Acierta el palo 100 veces.", L),
        (1_000, "buss_1k", "Bruja con licencia de Hacienda", "Acierta el palo 1.000 veces.", M),
    ])  # fmt: skip
    a.append(Achievement(
        id="bus_baraja",
        name="Baraja completa",
        description="Acierta el palo con picas, corazones, diamantes y tréboles.",
        category="bus",
        rarity=R,
        conditions=tuple((f"{BUS_WIN_PREFIX}{p.key}", 1) for p in bus_picks_for(BusHand.SUIT)),
    ))  # fmt: skip
    a.append(Achievement(
        id="bus_todas",
        name="Me sé todas las paradas",
        description="Acierta al autobús con cada una de las opciones, la vuelta incluida.",
        category="bus",
        rarity=E,
        conditions=tuple((f"{BUS_WIN_PREFIX}{p.key}", 1) for p in BusPick),
    ))  # fmt: skip
    a.append(Achievement(
        id="bus_transversal",
        name="Transversal",
        description="Acierta 100 veces rojo y 100 veces negro en el color del autobús.",
        category="bus",
        rarity=R,
        conditions=((f"{BUS_WIN_PREFIX}rojo", 100), (f"{BUS_WIN_PREFIX}negro", 100)),
    ))  # fmt: skip
    a += _tiers("bus", "bus_longshots", [
        (25, "bus_largas", "Amante de las cuotas largas",
         "Acierta 25 manos con 2 de 13 cartas o menos a favor.", E),
    ])  # fmt: skip
    a += _tiers("bus", "bus_contrarian", [
        (10, "bus_contra_10", "Llevar la contraria",
         "Acierta 10 manos eligiendo una opción menos probable que otra.", R),
        (100, "bus_contra_100", "Oposición de manual",
         "Acierta 100 manos eligiendo una opción menos probable que otra.", R),
    ])  # fmt: skip
    a += _tiers("bus", "bus_sure_fail", [
        (1, "bus_lo_tenias", "Lo tenías hecho",
         "Falla una mano con 11 de cada 13 cartas a favor.", C, True),
    ])  # fmt: skip
    a += _tiers("bus", "bus_first_fail", [
        (10, "bus_ni_subir", "Ni subirse, como Puigdemont a un avión",
         "Falla el color en 10 partidas.", C),
        (250, "bus_ni_subir_250", "Vecino de la marquesina", "Falla el color en 250 partidas.", R),
    ])  # fmt: skip
    a += _tiers("bus", "bus_last_fail", [
        (1, "bus_casi", "A una parada de casa", "Falla el palo con tres manos acertadas.", C),
        (25, "bus_casi_25", "Siempre te bajas en la penúltima", "Falla el palo 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("bus", "bus_gallina", [
        (25, "bus_gallina", "Me bajo aquí mismo, como Boris del Gobierno",
         "Cobra 25 veces tras acertar solo el color.", R),
        (250, "bus_gallina_250", "Una parada y a casa",
         "Cobra 250 veces tras acertar solo el color.", R),
    ])  # fmt: skip
    a += _tiers("bus", "bus_missed_longshot", [
        (1, "bus_te_bajaste", "Te bajaste antes del premio",
         "Cobra justo cuando la carta siguiente pagaba ×6,5 o más.", C, True),
    ])  # fmt: skip
    a += _tiers("bus", "bus_lost_big", [
        (1, "bus_rescate", "Del Falcon a la guagua", "Pierde con ×20 o más en juego.", R, True),
        (10, "bus_rescate_10", "Rescate de aerolínea",
         "Pierde con ×20 o más en juego 10 veces.", L),
    ])  # fmt: skip
    a += _tiers("bus", "bus_all_red", [
        (1, "bus_todo_rojo", "Ni el PSOE es tan rojo",
         "Descubre cuatro cartas rojas seguidas en una partida.", R, True),
    ])  # fmt: skip
    a += _tiers("bus", "bus_trio", [
        (1, "bus_trio", "Trío en la marquesina",
         "Acierta «igual» y luego «poste» en la misma partida.", L, True),
    ])  # fmt: skip
    a += _tiers("bus", "bus_cash_666", [
        (1, "bus_666", "La línea de la bestia", "Cobra exactamente 666 Y$ al autobús.", C, True),
    ])  # fmt: skip
    a += _tiers("bus", "bus_cash_69", [
        (1, "bus_69", "Línea 69", "Cobra exactamente 69 Y$ al autobús.", C, True),
    ])  # fmt: skip
    a += _tiers("bus", "bus_min_stake", [
        (1, "bus_1y", "Tarifa reducida", "Juega al autobús apostando 1 Y$.", C, True),
    ])  # fmt: skip
    a += _tiers("bus", "bus_night", [
        (1, "bus_buho", "Búho nocturno", "Juega al autobús de madrugada (de 2 a 6).", C),
    ])  # fmt: skip
    a += _tiers("bus", "bus_rush", [
        (1, "bus_hora_punta", "Hora punta",
         "Juega al autobús un día laborable entre las 7 y las 9 de la mañana.", C),
    ])  # fmt: skip
    a += _tiers("bus", "bus_canarias", [
        (1, "bus_guagua", "¡Que es guagua, no autobús!",
         "Juega al autobús el 30 de mayo, Día de Canarias.", C),
    ])  # fmt: skip
    a += _tiers("bus", "bus_friday13", [
        (1, "bus_viernes13", "Autobús de la mala suerte",
         "Pierde una partida al autobús un viernes 13.", C, True),
    ])  # fmt: skip

    # 🎲 Dados ----------------------------------------------------------------------------
    a += _tiers("dice", "dice_games", [
        (1, "dados_1", "Alea iacta est", "Juega tu primera partida de dados.", C),
        (100, "dados_100", "Habitual de la mesa de craps", "Juega 100 partidas de dados.", C),
        (1_000, "dados_1k", "Tirador de plantilla", "Juega 1.000 partidas de dados.", E),
        (10_000, "dados_10k", "Funcionario de carrera del cubilete",
         "Juega 10.000 partidas de dados. Plaza fija y trienios.", L),
    ])  # fmt: skip
    a += _tiers("dice", "dice_rolls", [
        (100, "dadost_100", "Muñeca suelta", "Tira los dados 100 veces.", C),
        (1_000, "dadost_1k", "Codo de tirador", "Tira los dados 1.000 veces.", R),
        (10_000, "dadost_10k", "Ruido de fondo en Moncloa", "Tira los dados 10.000 veces.", E),
        (50_000, "dadost_50k", "El cubilete no descansa", "Tira los dados 50.000 veces.", M),
    ])  # fmt: skip
    a += _tiers("dice", "dice_wins", [
        (50, "dadosw_50", "Mayoría simple", "Gana 50 partidas de dados.", C),
        (500, "dadosw_500", "Mayoría absoluta en el cubilete", "Gana 500 partidas de dados.", E),
        (2_500, "dadosw_2500", "Decreto ley tras decreto ley", "Gana 2.500 partidas de dados.", L),
        (10_000, "dadosw_10k", "Legislatura completa, sin adelanto electoral",
         "Gana 10.000 partidas de dados.", M),
    ])  # fmt: skip
    a += _tiers("dice", "dice_naturals", [
        (1, "dados_natural", "¡Natural!", "Saca un 7 o un 11 en la tirada de salida.", C),
        (100, "dados_natural_100", "Naturalidad institucional",
         "Saca 100 sietes u onces en la salida.", R),
        (1_000, "dados_natural_1k", "Más natural que el gofio",
         "Saca 1.000 sietes u onces en la salida.", L),
    ])  # fmt: skip
    a += _tiers("dice", "dice_craps_rolls", [
        (1, "dados_pifia", "Pifia de salida", "Saca un 2, un 3 o un 12 en la tirada de salida.", C),
        (100, "dados_pifia_100", "Gafe de Estado", "Saca 100 pifias en la salida.", E),
        (1_000, "dados_pifia_1k", "Crisis diplomática permanente",
         "Saca 1.000 pifias en la salida.", L),
    ])  # fmt: skip
    a += _tiers("dice", "dice_points_set", [
        (10, "dados_punto_10", "Punto de partida", "Pon el punto 10 veces.", C),
        (300, "dados_punto_300", "Punto y seguido", "Pon el punto 300 veces.", R),
        (2_000, "dados_punto_2k", "Punto de inflexión", "Pon el punto 2.000 veces.", E),
    ])  # fmt: skip
    a += _tiers("dice", "dice_points_made", [
        (1, "dados_hecho", "Promesa cumplida",
         "Repite el punto antes que el siete. No es tan habitual como parece.", C),
        (50, "dados_hecho_50", "Cumplir el programa electoral", "Haz 50 puntos.", R),
        (500, "dados_hecho_500", "Hemeroteca impecable", "Haz 500 puntos.", E),
        (2_500, "dados_hecho_2500", "Esto no lo ha hecho ni un Gobierno", "Haz 2.500 puntos.", L),
    ])  # fmt: skip
    a += _tiers("dice", "dice_seven_outs", [
        (1, "dados_siete_fuera", "Siete fuera",
         "Saca un 7 con el punto puesto y pierde los dados.", C),
        (100, "dados_siete_fuera_100", "Moción de censura con dados", "Saca 100 sietes fuera.", R),
        (1_000, "dados_siete_fuera_1k", "Disolución de las Cortes", "Saca 1.000 sietes fuera.", E),
    ])  # fmt: skip
    a += _tiers("dice", "dice_hand_points_max", [
        (2, "dados_mano_2", "Mano tibia de Feijóo", "Haz 2 puntos en la misma mano.", C),
        (4, "dados_mano_4", "Mano caliente de Ábalos", "Haz 4 puntos en la misma mano.", C),
        (6, "dados_mano_6", "Llamad a los bomberos", "Haz 6 puntos en la misma mano.", R),
        (8, "dados_mano_8", "Sale en el telediario", "Haz 8 puntos en la misma mano.", E),
        (10, "dados_mano_10", "Leyenda del Casino del Estado",
         "Haz 10 puntos en la misma mano.", M, True),
    ])  # fmt: skip
    a += _tiers("dice", "dice_hand_rolls_max", [
        (20, "dados_ronda_20", "Turno de palabra", "Aguanta 20 tiradas en la misma mano.", C),
        (40, "dados_ronda_40", "Filibusterismo", "Aguanta 40 tiradas en la misma mano.", R),
        (60, "dados_ronda_60", "Sesión de investidura",
         "Aguanta 60 tiradas en la misma mano.", L),
        (100, "dados_ronda_100", "Debate del estado de la nación",
         "Aguanta 100 tiradas en la misma mano.", M),
    ])  # fmt: skip
    a += _tiers("dice", "dice_fire_max", [
        (4, "dados_fuego_4", "Fuego cruzado en el Congreso",
         "Haz 4 puntos distintos en la misma mano.", R),
        (5, "dados_fuego_5", "Incendio de verano", "Haz 5 puntos distintos en la misma mano.",
         R),
        (6, "dados_fuego_6", "Los seis puntos en una mano",
         "Haz el 4, el 5, el 6, el 8, el 9 y el 10 en la misma mano.", L),
    ])  # fmt: skip
    a += _tiers("dice", "dice_game_rolls_max", [
        (10, "dados_larga_10", "Partida maratoniana", "Una partida de dados de 10 tiradas.", C),
        (20, "dados_larga_20", "Comisión de investigación del craps",
         "Una partida de dados de 20 tiradas.", R),
        (30, "dados_larga_30", "Más larga que unos Presupuestos prorrogados",
         "Una partida de dados de 30 tiradas.", L, True),
    ])  # fmt: skip
    a += _tiers("dice", "dice_snake_eyes", [
        (1, "dados_pegasus", "Ojos de Pegasus",
         "Saca dos unos. Alguien te está leyendo el móvil.", C, True),
        (50, "dados_pegasus_50", "Pinchazo telefónico", "Saca dos unos 50 veces.", R),
        (300, "dados_pegasus_300", "El CNI ya ni disimula", "Saca dos unos 300 veces.", L),
    ])  # fmt: skip
    a += _tiers("dice", "dice_boxcars", [
        (1, "dados_doble_seis", "Doble seis", "Saca dos seises. Mayoría absoluta de puntos.", C),
        (50, "dados_doble_seis_50", "Puente aéreo del Falcon", "Saca dos seises 50 veces.", R),
    ])  # fmt: skip
    a += _tiers("dice", "dice_elevens", [
        (1, "dados_once", "Como la ONCE", "Saca un 11 en la tirada de salida.", C, True),
        (100, "dados_once_100", "Cupón diario", "Saca 100 onces en la salida.", E),
    ])  # fmt: skip
    a += _tiers("dice", "dice_hard", [
        (1, "dados_malas", "Por las malas", "Haz un punto con dobles (2-2, 3-3, 4-4 o 5-5).", C),
        (100, "dados_malas_100", "Siempre por las malas", "Haz 100 puntos con dobles.", E),
    ])  # fmt: skip
    a.append(Achievement(
        id="dados_malas_todas",
        name="Las cuatro por las malas",
        description="Haz el 4, el 6, el 8 y el 10 con dobles alguna vez.",
        category="dice",
        rarity=R,
        conditions=tuple((f"dice_hard_{p}", 1) for p in (4, 6, 8, 10)),
    ))  # fmt: skip
    a.append(Achievement(
        id="dados_seis_puntos",
        name="Programa electoral completo",
        description="Haz alguna vez cada uno de los seis puntos: 4, 5, 6, 8, 9 y 10.",
        category="dice",
        rarity=C,
        conditions=tuple((f"dice_point_made_{p}", 1) for p in CRAPS_POINTS),
    ))  # fmt: skip
    a.append(Achievement(
        id="dados_2_al_12",
        name="Del 2 al 12, como el BOE",
        description="Saca alguna vez cada total, del 2 al 12.",
        category="dice",
        rarity=C,
        conditions=tuple((f"dice_total_{t}", 1) for t in range(2, 13)),
    ))  # fmt: skip
    a += _tiers("dice", "dice_odds_games", [
        (1, "dados_odds", "Odds de verdad",
         "Pon Odds por primera vez: la única apuesta del casino que no lleva trampa.", C),
        (100, "dados_odds_100", "El que sabe, sabe", "Pon Odds en 100 partidas.", R),
        (1_000, "dados_odds_1k", "Catedrático de probabilidad", "Pon Odds en 1.000 partidas.",
         E),
    ])  # fmt: skip
    a += _tiers("dice", "dice_odds_full", [
        (1, "dados_odds_tope", "Hasta el fondo", "Pon las Odds al tope.", C),
        (100, "dados_odds_tope_100", "Apalancamiento de manual",
         "Pon las Odds al tope en 100 partidas.", R),
    ])  # fmt: skip
    a += _tiers("dice", "dice_odds_full_lost", [
        (1, "dados_tezanos", "Te fiaste de Tezanos",
         "Pierde con las Odds al tope.", C, True),
        (25, "dados_tezanos_25", "Cocina de Tezanos", "Pierde 25 veces con las Odds al tope.", R),
    ])  # fmt: skip
    a += _tiers("dice", "dice_odds_full_won", [
        (1, "dados_lo_vio", "Lo vio venir", "Gana con las Odds al tope.", C),
        (50, "dados_lo_vio_50", "Soplo de la UCO",
         "Gana 50 veces con las Odds al tope.", R),
    ])  # fmt: skip
    a += _tiers("dice", "dice_dont_wins", [
        (1, "dados_en_contra", "Votar en contra", "Gana apostando a No pase.", C),
        (100, "dados_en_contra_100", "Oposición útil", "Gana 100 veces apostando a No pase.", E),
        (500, "dados_en_contra_500", "Oposición de Estado",
         "Gana 500 veces apostando a No pase.", L),
    ])  # fmt: skip
    a += _tiers("dice", "dice_dont_bar", [
        (1, "dados_barra", "Empate técnico", "Saca un 12 en la salida apostando a No pase.", C,
         True),
        (10, "dados_barra_10", "Ni vencedores ni vencidos", "Empata 10 veces en la barra.", E),
    ])  # fmt: skip
    a += _tiers("dice", "dice_dont_seven", [
        (1, "dados_ajena", "Alegrarse de la desgracia ajena",
         "Gana con No pase gracias a un siete fuera.", C, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="dados_equidistante",
        name="Ni sí ni no, sino todo lo contrario",
        description="Gana 50 partidas con Pase y 50 con No pase.",
        category="dice",
        rarity=R,
        conditions=(("dice_pass_wins", 50), ("dice_dont_wins", 50)),
    ))  # fmt: skip
    a += _tiers("dice", "dice_seven_first", [
        (1, "dados_expres", "Cesado antes de jurar el cargo",
         "Saca el siete justo después de poner el punto.", C, True),
        (50, "dados_expres_50", "Legislatura exprés",
         "Saca el siete justo después de poner el punto 50 veces.", R),
    ])  # fmt: skip
    a += _tiers("dice", "dice_long_lost", [
        (1, "dados_para_nada", "Tanto para nada",
         "Pierde una partida de dados de 10 tiradas o más.", C, True),
    ])  # fmt: skip
    a += _tiers("dice", "dice_boxcars_lost", [
        (1, "dados_doble_seis_casa", "Doble seis y para casa",
         "Pierde con Pase por un 12 en la salida.", C, True),
    ])  # fmt: skip
    a += _tiers("dice", "dice_win_max", [
        (10_000, "dados_rich", "Pelotazo en el tapete",
         "Gana 10.000 Y$ en una partida de dados.", E),
        (100_000, "dados_richer", "Recalificación del tapete",
         f"Gana 100.000 Y$ en una partida de dados. {TAX_COLLECTOR} ya ha hecho números.", M),
    ])  # fmt: skip
    a += _tiers("dice", "dice_profit", [
        (10_000, "dados_plusvalia", "Plusvalía", "Gana 10.000 Y$ en total con los dados.", C),
        (250_000, "dados_plusvalia_250k", "Patrimonio a declarar",
         "Gana 250.000 Y$ en total con los dados.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("dice", "dice_repeat_max", [
        (3, "dados_deja_vu", "Repetición de la jugada",
         "Saca el mismo total 3 veces seguidas en una mano.", C, True),
        (4, "dados_bucle", "Bucle temporal", "Saca el mismo total 4 veces seguidas en una mano.",
         C),
        (5, "dados_marmota", "El día de la marmota",
         "Saca el mismo total 5 veces seguidas en una mano.", E, True),
    ])  # fmt: skip
    a += _tiers("dice", "dice_night", [
        (1, "dados_madrugada", "Timba de madrugada", "Juega a los dados de madrugada (de 2 a 6).",
         C),
    ])  # fmt: skip
    a += _tiers("dice", "dice_canarias", [
        (1, "dados_canarias", "Dados y papas arrugadas", "Juega a los dados el Día de Canarias.",
         C),
    ])  # fmt: skip
    a += _tiers("dice", "dice_nochevieja", [
        (1, "dados_uvas", "Doce uvas, dos dados", "Juega a los dados en Nochevieja.", C),
    ])  # fmt: skip
    a += _tiers("dice", "dice_inocentes", [
        (1, "dados_trucados", "Dados trucados", "Juega a los dados el día de los Inocentes.",
         C, True),
    ])  # fmt: skip
    a += _tiers("dice", "dice_friday13", [
        (1, "dados_viernes13", "Dados negros", "Pierde a los dados un viernes 13.", C, True),
    ])  # fmt: skip
    a += _tiers("dice", "dice_cash_777", [
        (1, "dados_777", "El 777 del cubilete", "Cobra exactamente 777 Y$ en los dados.", C,
         True),
    ])  # fmt: skip

    # 🐔 Pollo ---------------------------------------------------------------------------
    a += _tiers("chicken", "chicken_games", [
        (1, "pollo_1", "¿Por qué cruzó el pollo la carretera?",
         "Juega tu primera partida del Pollo.", C),
        (100, "pollo_100", "Carnet por puntos", "Juega 100 partidas del Pollo.", C),
        (1_000, "pollo_1k", "La DGT te tiene fichado", "Juega 1.000 partidas del Pollo.", E),
        (10_000, "pollo_10k", "Más viajes que el Falcon", "Juega 10.000 partidas del Pollo.", L),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_lanes", [
        (100, "pollol_100", "Peatón de fondo de la Castellana", "Cruza 100 carriles.", C),
        (1_000, "pollol_1k", "Paso de cebra del Ministerio", "Cruza 1.000 carriles.", R),
        (10_000, "pollol_10k", "Red de Carreteras del Estado", "Cruza 10.000 carriles.", E),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_cashouts", [
        (10, "polloc_10", "Pollo listo, como un asesor", "Cobra 10 partidas del Pollo.", C),
        (100, "polloc_100", "Gallina vieja hace buen caldo", "Cobra 100 partidas del Pollo.", R),
        (1_000, "polloc_1k", "Granja rentable con la PAC", "Cobra 1.000 partidas del Pollo.", E),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_splats", [
        (1, "pollos_1", "Pollo a la plancha", "Que te atropellen por primera vez.", C),
        (10, "pollos_10", "Pechuga fileteada de Mercadona", "Que te atropellen 10 veces.", C),
        (100, "pollos_100", "Nuggets de Temu", "Que te atropellen 100 veces.", C),
        (500, "pollos_500", "Pollo sin cabeza, como el Gobierno en funciones",
         "Que te atropellen 500 veces.", R),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_first_splat", [
        (1, "pollo_ni_acera", "Ni a la otra acera", "Que te atropellen en el primer carril.",
         C, True),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_last_lane_splat", [
        (1, "pollo_casi", "A un carril de la gloria",
         "Que te atropellen en el último carril antes de la meta.", R, True),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_mult_max", [
        (300, "pollo_x3", "Pollo de corral ecológico de la Agenda 2030", "Cobra en ×3 o más.", C),
        (1_000, "pollo_x10", "Pollo de Bresse", "Cobra en ×10 o más.", C),
        (5_000, "pollo_x50", "La gallina de los huevos de oro", "Cobra en ×50 o más.", L),
        (100_000, "pollo_x1000", "Pollo en el Falcon",
         "Cobra en ×1.000 o más: ya viajas como un presidente.", M),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_win_max", [
        (10_000, "pollo_rich", "Huevo de oro de la Fundación Franco",
         "Gana 10.000 Y$ en una partida del Pollo.", R),
        (100_000, "pollo_richer", "Granja de Hacienda",
         f"Gana 100.000 Y$ en una partida del Pollo. {TAX_COLLECTOR} ya afila el cuchillo.", L),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_finish_facil", [
        (1, "pollo_meta_facil", "Cruzar en verde", "Llega a la meta en Fácil.", C),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_finish_media", [
        (1, "pollo_meta_media", "Nacional sin rasguños", "Llega a la meta en Media.", R),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_finish_dificil", [
        (1, "pollo_meta_dificil", "Autovía conquistada", "Llega a la meta en Difícil.", L),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_finish_hardcore", [
        (1, "pollo_meta_hardcore", "Operación salida superada",
         "Llega a la meta en Hardcore (×2.105). Sale en el BOE.", M),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_hardcore_cashouts", [
        (1, "pollo_hc_1", "Kamikaze de Kim", "Cobra una partida en Hardcore.", C),
        (50, "pollo_hc_50", "Sin miedo a la DGT", "Cobra 50 partidas en Hardcore.", E),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_lanes_max_hardcore", [
        (5, "pollo_hc_r5", "Valiente o inconsciente, como Ayuso en el Metro",
         "Cruza 5 carriles en Hardcore.", R),
        (10, "pollo_hc_r10", "Ni el Constitucional te para",
         "Cruza 10 carriles en Hardcore.", M),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_lanes_max_dificil", [
        (12, "pollo_dif_r12", "Autovía de peaje", "Cruza 12 carriles en Difícil.", R),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_lanes_max_media", [
        (15, "pollo_med_r15", "Nacional de primera", "Cruza 15 carriles en Media.", C),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_lanes_max_facil", [
        (20, "pollo_fac_r20", "Paseo dominical", "Cruza 20 carriles en Fácil.", C),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_auto_runs", [
        (10, "polloa_10", "Pollo autónomo con Estatuto propio", "Usa el autocobro 10 veces.", C),
        (100, "polloa_100", "Conducción autónoma de Elon", "Usa el autocobro 100 veces.", R),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_auto_lanes", [
        (1_000, "polloal_1k", "Sin manos, mamá", "Cruza 1.000 carriles con el autocobro.", R),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_gallina", [
        (25, "pollo_gallina", "Gallina",
         "Cobra 25 veces tras un solo carril. Cobarde, pero cobras. "
         "Como un diputado en una votación secreta.", C),
        (250, "pollo_gallina_250", "Gallina clueca", "Cobra 250 veces tras un solo carril.", R),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_close", [
        (1, "pollo_pelos", "Pollo con suerte", "Cobra justo un carril antes del coche.", C),
        (10, "pollo_pelos_10", "Resistencia en el arcén",
         "Cobra 10 veces justo un carril antes del coche.", R),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_left_on_table", [
        (1, "pollo_mesa", "Te lo dejaste en la mesa",
         "Cobra con 5 carriles libres o más por delante.", C),
        (10, "pollo_mesa_10", "Dinero que no volverá",
         "Cobra 10 veces con 5 carriles libres o más por delante.", C),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_road_free", [
        (1, "pollo_libre", "Y la carretera, vacía",
         "Cobra cuando no venía ningún coche hasta la meta.", C, True),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_lost_big", [
        (1, "pollo_rescate", "Rescate denegado", "Que te atropellen con ×10 o más en juego.", R),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_hit_car", [
        (1, "pollo_hit_car", "Siniestro total", "Que te atropelle un utilitario o un SUV.",
         C, True),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_hit_van", [
        (1, "pollo_hit_van", "Pedido entregado (tú)",
         "Que te atropelle una furgoneta de reparto.", C, True),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_hit_truck", [
        (1, "pollo_hit_truck", "Mudanza al más allá", "Que te atropelle un camión de mudanzas.",
         C, True),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_hit_bus", [
        (1, "pollo_hit_bus", "Final de trayecto", "Que te atropelle un autobús de línea.",
         C, True),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_hit_moto", [
        (1, "pollo_hit_moto", "Rider sin contrato", "Que te atropelle un repartidor en moto.",
         C, True),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_hit_taxi", [
        (1, "pollo_hit_taxi", "Bajada de bandera", "Que te atropelle un taxi con prisa.",
         C, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="pollo_matriculas",
        name="Colección de matrículas",
        description="Que te atropellen los seis tipos de vehículo.",
        category="chicken",
        rarity=C,
        conditions=tuple((f"chicken_hit_{kind}", 1) for kind in CHICKEN_VEHICLE_KINDS),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_all_games",
        name="Todoterreno",
        description="Juega a la ruleta, al blackjack, a la tragaperras, al Crash y a Minas.",
        category="casino",
        rarity=C,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1),
        ),
    ))  # fmt: skip

    a += _tiers("chicken", "chicken_lanes", [
        (30_000, "pollol_30k", "Kilómetro cero", "Cruza 30.000 carriles.", L),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_cashouts", [
        (5_000, "polloc_5k", "Pollo del año", "Cobra 5.000 partidas del Pollo.", L),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_splats", [
        (2_500, "pollos_2500", "Pollo asado de carretera", "Que te atropellen 2.500 veces.", L),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_first_splat", [
        (25, "pollo_ni_acera_25", "Atropellado en la acera",
         "Que te atropellen en el primer carril 25 veces.", C),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_close", [
        (50, "pollo_pelos_50", "Cruzar en ámbar",
         "Cobra 50 veces justo un carril antes del coche.", E),
    ])  # fmt: skip
    a += _tiers("chicken", "chicken_lost_big", [
        (10, "pollo_rescate_10", "Rescate denegado otra vez",
         "Que te atropellen con ×10 o más en juego 10 veces.", E),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="pollo_all_roads",
            name="Todas las carreteras llevan a Waterloo",
            description="Juega una partida en cada dificultad.",
            category="chicken",
            rarity=C,
            conditions=tuple(
                (f"chicken_games_{key}", 1) for key in ("facil", "media", "dificil", "hardcore")
            ),
        )
    )
    a += _tiers("chicken", "chicken_games_hardcore", [
        (500, "pollo_hc_500", "Kamikaze de autovía", "Juega 500 partidas en Hardcore.", L),
    ])  # fmt: skip

    # 🌸 Pachinko -----------------------------------------------------------------------
    a += _tiers("pachinko", "pachinko_volleys", [
        (1, "pachi_1", "Primera bola", "Lanza tu primera tanda en el pachinko.", C),
        (100, "pachi_100", "Salón de Akihabara", "Lanza 100 tandas en el pachinko.", C),
        (1_000, "pachi_1k", "Ojos de neón, como Las Vegas de Trump",
         "Lanza 1.000 tandas en el pachinko.", E),
        (10_000, "pachi_10k", "Vives en el salón, como Kim en su búnker",
         "Lanza 10.000 tandas en el pachinko.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_starts", [
        (100, "pachi_start_100", "Por la ranura", "Mete 100 bolas por START.", C),
        (1_000, "pachi_start_1k", "Tulipán abierto", "Mete 1.000 bolas por START.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_reach", [
        (1, "pachi_reach", "¡REACH!", "Mira un reach en la pantalla.", C),
        (100, "pachi_reach_100", "Corazón de pachinko", "Mira 100 reach.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_fake_reach", [
        (25, "pachi_fake_25", "Me la volvió a hacer", "Pierde 25 reach por un número.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_atari", [
        (1, "pachi_atari", "¡ATARI!", "Saca tres iguales en la pantalla.", C),
        (25, "pachi_atari_25", "Bendecido por Jovani", "Saca 25 ataris.", R),
        (100, "pachi_atari_100", "La compuerta te quiere", "Saca 100 ataris.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_rush", [
        (1, "pachi_rush", "Kakuhen", "Saca un rush (atari con número impar).", C),
        (10, "pachi_rush_10", "Adicto al rush, como Trump a los aranceles", "Saca 10 rush.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_super", [
        (1, "pachi_777", "7️⃣7️⃣7️⃣", "Saca el SUPER RUSH.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_renchan_max", [
        (3, "pachi_ren_3", "Renchan", "Encadena 3 premios gordos en un rush.", C),
        (5, "pachi_ren_5", "Racha imparable de Xi",
         "Encadena 5 premios gordos. El Partido lo planificó.", R),
        (10, "pachi_ren_10", "Lluvia de bolas y de fondos europeos",
         "Encadena 10 premios gordos.", E),
        (15, "pachi_ren_15", "Fiebre total", "Encadena 15 premios gordos.", E, True),
        (25, "pachi_ren_25", "El oni sonríe", "Encadena 25 premios gordos, el máximo (Oni).",
         M, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_board_sakura", [
        (100, "pachi_hanami", "Hanami", "Lanza 100 tandas en el tablero Sakura.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_board_oni", [
        (100, "pachi_infierno", "Bajada a los infiernos", "Lanza 100 tandas en el tablero Oni.",
         R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_atari_dragon", [
        (1, "pachi_dragon", "Perla del dragón", "Saca un atari en el tablero Dragón.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_atari_oni", [
        (1, "pachi_oni", "Domador de onis", "Saca un atari en el tablero Oni.", R),
        (10, "pachi_oni_10", "Amigo de los demonios", "Saca 10 ataris en el tablero Oni.", E),
    ])  # fmt: skip
    a.append(Achievement(
        id="pachi_tourist",
        name="Turista de salones",
        description="Juega en los cuatro tableros del pachinko.",
        category="pachinko",
        rarity=C,
        conditions=tuple((f"pachinko_board_{key}", 1) for key in PACHINKO_BOARDS),
    ))  # fmt: skip
    a += _tiers("pachinko", "pachinko_corners", [
        (1, "pachi_corner", "Esquinita, como Puigdemont en el Parlamento Europeo",
         "Mete una bola en un bolsillo de esquina.", C),
        (25, "pachi_corner_25", "Tirador de esquinas", "Mete 25 bolas en las esquinas.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_full_hold", [
        (1, "pachi_hold", "Reserva llena, como los embalses en campaña",
         "Llena las 4 reservas en una tanda.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_wasted", [
        (1, "pachi_limbo", "Bolas al limbo", "Mete una bola en START con la reserva llena.", C,
         True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_blank", [
        (1, "pachi_blank", "Todas por el desagüe", "Pierde las 10 bolas de una tanda.", C, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_win_max", [
        (10_000, "pachi_rich", "Bandeja llena", "Gana 10.000 Y$ en una tanda.", R),
        (100_000, "pachi_richer", "Rey del salón", "Gana 100.000 Y$ en una tanda.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("pachinko", "pachinko_session_max", [
        (50, "pachi_session", "Sin levantarse del taburete, como un tertuliano",
         "Lanza 50 tandas en una máquina.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_burst", [
        (10, "pachi_burst", "Mano en el gatillo, como Kim en el botón",
         "Usa la Ráfaga 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_turbo", [
        (100, "pachi_turbo", "Prisa japonesa, ritmo de tramitación española",
         "Lanza 100 tandas en turbo.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_night", [
        (1, "pachi_night", "Salón 24 horas", "Juega al pachinko entre las 3 y las 6.", C, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="casino_six_games",
        name="Ludópata integral",
        description="Juega a los seis juegos del casino, pachinko incluido.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
        ),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_seven_games",
        name="Los siete pecados",
        description="Juega a los siete juegos del casino, botes incluidos.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
            ("botes_spins", 1),
        ),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_eight_games",
        name="Ocho apellidos ludópatas",
        description="Juega a los ocho juegos del casino, el Pollo incluido.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
            ("botes_spins", 1), ("chicken_games", 1),
        ),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_eleven_games",
        name="Once apellidos ludópatas",
        description="Juega a los once juegos del casino, de la ruleta a los dados.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
            ("botes_spins", 1), ("chicken_games", 1), ("coin_games", 1),
            ("horse_bets", 1), ("dice_games", 1),
        ),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_twelve_games",
        name="Doce apellidos ludópatas",
        description="Juega a los doce juegos del casino: de la ruleta al autobús.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
            ("botes_spins", 1), ("chicken_games", 1), ("coin_games", 1),
            ("horse_bets", 1), ("dice_games", 1), ("bus_games", 1),
        ),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_thirteen_games",
        name="Trece apellidos ludópatas",
        description="Juega a los trece juegos del casino, de la ruleta al bacará. Y sin gafe.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
            ("botes_spins", 1), ("chicken_games", 1), ("coin_games", 1),
            ("horse_bets", 1), ("dice_games", 1), ("bus_games", 1), ("bac_hands", 1),
        ),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_fourteen_games",
        name="Catorce apellidos y un gallo",
        description="Juega a los catorce juegos del casino, reñidero de gallos incluido.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
            ("botes_spins", 1), ("chicken_games", 1), ("coin_games", 1),
            ("horse_bets", 1), ("dice_games", 1), ("bus_games", 1), ("bac_hands", 1),
            ("gallos_fights", 1),
        ),
    ))  # fmt: skip

    a += _tiers("pachinko", "pachinko_volleys", [
        (50_000, "pachi_50k", "Pachinko de por vida", "Lanza 50.000 tandas en el pachinko.", M),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_atari", [
        (500, "pachi_atari_500", "Fiebre del atari", "Saca 500 ataris.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_rush", [
        (100, "pachi_rush_100", "Hora punta en Shibuya", "Saca 100 rush.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_super", [
        (10, "pachi_super_10", "Siete del Imperio", "Saca 10 SUPER RUSH.", E),
        (50, "pachi_super_50", "Emperador del pachinko", "Saca 50 SUPER RUSH.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_corners", [
        (100, "pachi_corner_100", "Esquinero", "Mete 100 bolas en las esquinas.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_full_hold", [
        (50, "pachi_hold_50", "Reserva de por vida", "Llena las 4 reservas en 50 tandas.", R),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_atari_dragon", [
        (25, "pachi_dragon_25", "Domador de dragones", "Saca 25 ataris en el tablero Dragón.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_board_clasica", [
        (1_000, "pachi_clasica_1k", "De toda la vida",
         "Lanza 1.000 tandas en el tablero Clásico.", E),
    ])  # fmt: skip

    # 🌸 Pachinko: lo que cuentan las caídas simuladas (`pachinko_physics`) ---------------
    # Rarezas: meta / (60 tandas al día × rebotes medios por tanda); las de suerte,
    # con la biblioteca de caídas y la probabilidad de cada bolsillo (ver
    # `docs/auditoria-logros.md`, «Pachinko: la física»).
    a += _tiers("pachinko", "pachinko_bounces", [
        (1_000, "pachi_bounce_1k", "Pelotazo urbanístico",
         "Acumula 1.000 rebotes de bolas en los clavos.", C),
        (10_000, "pachi_bounce_10k", "Rebote técnico del Ibex",
         "Acumula 10.000 rebotes en los clavos.", C),
        (50_000, "pachi_bounce_50k", "Fichaje de exministro",
         "Acumula 50.000 rebotes: entran, salen y vuelven a entrar.", R),
        (250_000, "pachi_bounce_250k", "Vuelo en Falcon",
         "Acumula 250.000 rebotes, de clavo en clavo.", E),
        (1_000_000, "pachi_bounce_1m", "Resistencia numantina",
         "Acumula 1.000.000 de rebotes: la bola aguanta lo que le echen.", L),
        (5_000_000, "pachi_bounce_5m", "Rebote del gato muerto",
         "Acumula 5.000.000 de rebotes.", M, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_bounce_max", [
        (15, "pachi_hop_15", "Pinball parlamentario",
         "Haz que una bola rebote 15 veces en los clavos antes de entrar.", C),
        (18, "pachi_hop_18", "Tránsfuga de clavos",
         "Haz que una bola rebote 18 veces: no se queda en ningún sitio.", C),
        (20, "pachi_hop_20", "Moción de censura",
         "Haz que una bola rebote 20 veces antes de caer en su bolsillo.", C),
        (21, "pachi_hop_21", "Récord del Congreso",
         "Haz que una bola rebote 21 veces: casi lo máximo que da un tablero.", C, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_bounce_volley_max", [
        (150, "pachi_tanda_150", "Sesión de control",
         "Suma 150 rebotes de las diez bolas en una sola tanda.", C),
        (160, "pachi_tanda_160", "Pleno sin descanso",
         "Suma 160 rebotes en una sola tanda.", R),
        (165, "pachi_tanda_165", "Crispación máxima",
         "Suma 165 rebotes en una sola tanda.", E),
        (170, "pachi_tanda_170", "Barra libre de rebotes",
         "Suma 170 rebotes en una sola tanda: una entre decenas de miles.", M, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_slow_balls", [
        (1, "pachi_slow_1", "Retraso de Renfe", "Mira una bola tardar casi 2 segundos en caer.", C),
        (100, "pachi_slow_100", "Cercanías en hora punta",
         "Mira 100 bolas tardar casi 2 segundos en caer.", C),
        (1_000, "pachi_slow_1k", "Obras en la M-30",
         "Mira 1.000 bolas tardar casi 2 segundos en caer.", R),
        (10_000, "pachi_slow_10k", "Variante de Pajares",
         "Mira 10.000 bolas tardar casi 2 segundos en caer.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_swift_balls", [
        (1, "pachi_swift_1", "Puntual como el AVE",
         "Mira una bola caer en 1,1 segundos o menos.", C),
        (100, "pachi_swift_100", "Maquinista con prisa",
         "Mira 100 bolas caer en 1,1 segundos o menos.", R),
        (1_000, "pachi_swift_1k", "Alta velocidad, baja fiabilidad",
         "Mira 1.000 bolas caer en 1,1 segundos o menos.", E),
        (5_000, "pachi_swift_5k", "Hyperloop de Teruel",
         "Mira 5.000 bolas caer en 1,1 segundos o menos.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_clean_balls", [
        (1, "pachi_clean_1", "Bola Pegasus",
         "Haz que una bola baje rebotando 5 veces o menos, sin que nadie se entere.", C),
        (25, "pachi_clean_25", "Mensajes borrados",
         "Mira 25 bolas bajar rebotando 5 veces o menos.", C),
        (250, "pachi_clean_250", "Disco duro formateado",
         "Mira 250 bolas bajar rebotando 5 veces o menos.", E, True),
    ])  # fmt: skip

    # 🌸 Pachinko: choques entre bolas (`pachinko_motion`) ----------------------------------
    # Rarezas: meta / (60 tandas al día × lo que da una tanda), con las probabilidades
    # medidas simulando 42.000 tandas con el código real (0,62 choques por tanda, el 61 %
    # sin ninguno; ver `docs/auditoria-logros.md`, «Pachinko: los choques»).
    a += _tiers("pachinko", "pachinko_hits", [
        (1, "pachi_hit_1", "Cara a cara en el Congreso",
         "Haz que dos bolas choquen entre sí por primera vez.", C),
        (100, "pachi_hit_100", "Rifirrafe de cafetería", "Provoca 100 choques entre bolas.", C),
        (500, "pachi_hit_500", "Junta de vecinos",
         "Provoca 500 choques entre bolas: siempre las mismas de siempre.", R),
        (2_000, "pachi_hit_2k", "Cacerolada en Ferraz", "Provoca 2.000 choques entre bolas.", E),
        (5_000, "pachi_hit_5k", "Investidura fallida",
         "Provoca 5.000 choques entre bolas sin que se pongan de acuerdo.", L),
        (20_000, "pachi_hit_20k", "Colisionador de hadrones",
         "Provoca 20.000 choques entre bolas.", M, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_hits_max", [
        (3, "pachi_hits_3", "Empujones en el Metro",
         "Consigue 3 choques entre bolas en una sola tanda.", C),
        (5, "pachi_hits_5", "Manifestación en Colón",
         "Consigue 5 choques entre bolas en una sola tanda.", C),
        (6, "pachi_hits_6", "Pelea en el hemiciclo",
         "Consigue 6 choques entre bolas en una sola tanda.", R),
        (8, "pachi_hits_8", "Choque de trenes",
         "Consigue 8 choques entre bolas en una sola tanda.", E),
        (10, "pachi_hits_10", "Colisión en cadena en la A-6",
         "Consigue 10 choques entre bolas en una sola tanda.", L, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_hit_ball_max", [
        (2, "pachi_ball_2", "Doble tropiezo",
         "Haz que una misma bola choque 2 veces con otras en su caída.", C),
        (3, "pachi_ball_3", "Carambola a tres bandas",
         "Haz que una misma bola choque 3 veces con otras en su caída.", C),
        (5, "pachi_ball_5", "Partida de billar en el bar de Paco",
         "Haz que una misma bola choque 5 veces con otras en su caída.", R),
        (7, "pachi_ball_7", "Tertulia de Telecinco",
         "Haz que una misma bola choque 7 veces: nadie deja hablar a nadie.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_balls_hit_max", [
        (5, "pachi_dominos_5", "Efecto dominó",
         "Haz que 5 bolas distintas choquen con otra en una misma tanda.", C),
        (7, "pachi_dominos_7", "Coalición de siete partidos",
         "Haz que 7 bolas distintas choquen con otra en una misma tanda.", E),
        (8, "pachi_dominos_8", "Todos contra todos",
         "Haz que 8 de las 10 bolas choquen con otra en una misma tanda.", L, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_hit_corner", [
        (1, "pachi_waterloo", "Fuga a Waterloo",
         "Haz que una bola choque con otra y salga despedida a un bolsillo de esquina.", E, True),
        (5, "pachi_andorra", "Cuenta en Andorra",
         "Haz que 5 bolas acaben en una esquina después de chocar con otra.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_no_hits", [
        (1, "pachi_calm_1", "Distancia de seguridad",
         "Lanza una tanda en la que ninguna bola choque con otra.", C),
        (100, "pachi_calm_100", "Aforo limitado",
         "Lanza 100 tandas sin ningún choque entre bolas.", C),
        (1_000, "pachi_calm_1k", "Burbuja de convivientes",
         "Lanza 1.000 tandas sin ningún choque entre bolas.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_delayed", [
        (1, "pachi_wait_1", "Cita previa en Hacienda",
         "Mira una bola esperar turno para salir: no había hueco libre.", C),
        (100, "pachi_wait_100", "Lista de espera de la Sanidad",
         "Mira 100 bolas esperar turno para salir.", E),
        (1_000, "pachi_wait_1k", "Plaza de funcionario",
         "Mira 1.000 bolas esperar turno para salir.", L),
    ])  # fmt: skip

    # 🌸 Pachinko: ▶️ Auto (sesiones de hasta 25 tandas, una a una, con animación) --------
    # Las rarezas salen de simular sesiones con las reglas reales (apuesta de 100 Y$, tableros
    # Clásica 40 %, Sakura 30 %, Dragón 15 % y Oni 15 %, 5 sesiones al día, 30.000 sesiones):
    # el Auto del pachinko casi siempre para antes de las 25 tandas, porque un atari llega en
    # 1 de cada ~13 tandas y lo corta. El 79,7 % de las sesiones acaba en atari (4 al día), el
    # 19,1 % en el techo de pérdidas (1 al día) y solo el 1,3 % llega a las 25 tandas (1 cada
    # ~16 días). Una sesión dura 10,1 tandas de media (~50 al día); el 9,1 % acaba en un SUPER
    # RUSH (0,46 al día); el 0,97 % pasa por un momento con 10 tandas o más y neto positivo
    # donde retirarse a tiempo (1 cada ~20 días); 1 de cada ~15.000 sesiones llega a las 25 con
    # el neto en 0 exacto; salen 19 reach perdidos al día y un atari encadena 3 premios gordos o
    # más 1 de cada 5 veces. Parar a mano, quedarse sin saldo o parar tras una tanda se decide.
    # El primer SUPER RUSH del Auto es Raro como el de 🎯 Lanzar (`pachi_777`): mismo ritmo.
    a += _tiers("pachinko", "pachinko_autoplay_volleys", [
        (1, "pachi_auto_1", "Bolas de oficio",
         "Juega una tanda con ▶️ Auto: las bolas caen solas, como los expedientes.", C),
        (100, "pachi_auto_100", "Silencio administrativo",
         "Juega 100 tandas con ▶️ Auto. Quien calla, otorga.", C),
        (1_000, "pachi_auto_1k", "Fondos Next Generation",
         "Juega 1.000 tandas con ▶️ Auto: el dinero sale solo y nadie sabe adónde va.", E),
        (10_000, "pachi_auto_10k", "Funcionario de carrera",
         "Juega 10.000 tandas con ▶️ Auto. Plaza fija y sin mirar el reloj.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_sessions", [
        (10, "pachi_auto_ses_10", "Pleno extraordinario",
         "Empieza 10 sesiones de ▶️ Auto en el pachinko.", C),
        (100, "pachi_auto_ses_100", "Subcomisión de bolas",
         "Empieza 100 sesiones de ▶️ Auto: más sesiones que conclusiones.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_full", [
        (1, "pachi_auto_full_1", "Maratón de tramitación",
         "Completa una sesión de ▶️ Auto hasta el tope de 25 tandas, sin atari ni techo de gasto.",
         E),
        (5, "pachi_auto_full_5", "Gobernar sin Presupuestos",
         "Completa 5 sesiones de ▶️ Auto hasta el tope de 25 tandas.", L),
        (25, "pachi_auto_full_25", "Inmunidad parlamentaria",
         "Completa 25 sesiones de ▶️ Auto hasta el tope de 25 tandas.", M),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_loss_limit", [
        (1, "pachi_auto_loss_1", "Banco malo de bolas",
         "Que ▶️ Auto pare en el pachinko al perder 10 veces la apuesta.", C),
        (10, "pachi_auto_loss_10", "Corralito",
         "Que ▶️ Auto pare por el límite de pérdidas del pachinko 10 veces.", R),
        (100, "pachi_auto_loss_100", "Intervención de Bruselas",
         "Que ▶️ Auto pare por el límite de pérdidas del pachinko 100 veces.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_bigwin", [
        (1, "pachi_auto_atari_1", "Aterrizaje en Torrejón",
         "Que ▶️ Auto pare porque ha salido un atari: el Falcon toca pista.", C),
        (10, "pachi_auto_atari_10", "Hoja de ruta del Falcon",
         "Que ▶️ Auto pare por un atari 10 veces.", C),
        (50, "pachi_auto_atari_50", "Puerta giratoria de Akihabara",
         "Que ▶️ Auto pare por un atari 50 veces: entras, cobras y sales.", R),
        (250, "pachi_auto_atari_250", "Fichaje de expresidente",
         "Que ▶️ Auto pare por un atari 250 veces.", E),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_super", [
        (1, "pachi_auto_777", "Sobresueldo en 7️⃣7️⃣7️⃣",
         "Saca un SUPER RUSH mientras ▶️ Auto juega por ti.", R),
        (10, "pachi_auto_777_10", "Caja B de la máquina",
         "Saca 10 SUPER RUSH con ▶️ Auto.", E),
        (50, "pachi_auto_777_50", "La Gürtel del pachinko",
         "Saca 50 SUPER RUSH con ▶️ Auto. Alguien tiene que estar cobrando comisiones.", L, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_broke", [
        (1, "pachi_auto_broke", "Cuenta intervenida",
         "Que ▶️ Auto pare porque no te llega para otra tanda.", C, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_manual", [
        (1, "pachi_auto_manual_1", "Botón de pánico",
         "Para ▶️ Auto a mano con ⏹️ Parar en el pachinko.", C),
        (10, "pachi_auto_manual_10", "Moción de orden",
         "Para ▶️ Auto a mano en el pachinko 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_quick_quit", [
        (1, "pachi_auto_quit", "Marcha atrás en caliente",
         "Para ▶️ Auto en el pachinko después de una sola tanda.", C, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_exit_ahead", [
        (1, "pachi_auto_ahead_1", "Salir de rositas",
         "Para ▶️ Auto a mano con 10 tandas o más y la sesión en positivo.", E),
        (10, "pachi_auto_ahead_10", "Pelotazo y a Dubái",
         "Retírate a tiempo, con ganancias, 10 veces en el pachinko.", L),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_even", [
        (1, "pachi_auto_even", "Cuadrar con Bruselas",
         "Termina una sesión completa de ▶️ Auto en el pachinko con el neto en 0 Y$ exactos.",
         M, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_fake_reach", [
        (50, "pachi_auto_fake_50", "Promesas electorales",
         "Pierde 50 reach por un número mientras ▶️ Auto juega por ti.", C),
        (200, "pachi_auto_fake_200", "Letra pequeña del programa",
         "Pierde 200 reach por un número con ▶️ Auto.", R),
        (1_000, "pachi_auto_fake_1k", "Programa electoral cumplido",
         "Pierde 1.000 reach por un número con ▶️ Auto. Lo prometido no era deuda.", E, True),
    ])  # fmt: skip
    a += _tiers("pachinko", "pachinko_autoplay_renchan_max", [
        (3, "pachi_auto_ren_3", "Cadena de favores",
         "Encadena 3 premios gordos en una tanda de ▶️ Auto.", C),
        (5, "pachi_auto_ren_5", "Red clientelar",
         "Encadena 5 premios gordos en una tanda de ▶️ Auto.", R),
        (10, "pachi_auto_ren_10", "Contratos a dedo",
         "Encadena 10 premios gordos en una tanda de ▶️ Auto, sin concurso público.", E, True),
    ])  # fmt: skip

    # 🏦 Banco: Bizum ----------------------------------------------------------------------
    a += _tiers("bizum", "bizum_sent_count", [
        (1, "bizum_1", "Te hago un Bizum", "Manda tu primer Bizum.", C),
        (25, "bizum_25", "Cuentas claras, no como las de Bárcenas", "Manda 25 Bizums.", R),
        (100, "bizum_100", "Tesorero del grupo, como Bárcenas", "Manda 100 Bizums.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_sent", [
        (10_000, "bizumy_10k", "Invito yo, como Rubiales en Arabia",
         "Manda 10.000 Y$ en Bizums.", C),
        (100_000, "bizumy_100k", "Banco de los colegas", "Manda 100.000 Y$ en Bizums.", R),
        (1_000_000, "bizumy_1m", "Herencia en vida", "Manda 1.000.000 Y$ en Bizums.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "bizum_received_count", [
        (1, "bizumr_1", "Te ha llegado un Bizum", "Recibe tu primer Bizum.", C),
        (25, "bizumr_25", "Con amigos así, quién necesita Next Generation", "Recibe 25 Bizums.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_received", [
        (100_000, "bizumr_100k", "Vivo de los colegas", "Recibe 100.000 Y$ en Bizums.", R),
    ], unit="money")  # fmt: skip
    a.append(Achievement(
        id="bizum_espaldas",
        name="A espaldas de Sánchez",
        description=(
            f"Manda un Bizum de más de {_thousands(BIZUM_MAX_OPERATION)} Y$, el máximo "
            "por operación en España (1.000 €)."
        ),
        category="bizum",
        rarity=E,
        conditions=(("bizum_max", BIZUM_MAX_OPERATION + 1),),
        unit="money",
        story=BIZUM_LIMIT_STORY,
    ))  # fmt: skip
    a += _tiers("bizum", "bizum_day_max", [
        (BIZUM_MAX_DAILY + 1, "bizum_daily", "Límite diario",
         f"Manda más de {_thousands(BIZUM_MAX_DAILY)} Y$ en Bizums en un día (2.000 €).", R,
         True),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "bizum_full", [
        (5, "bizum_pitufeo", "Pitufeo",
         f"Manda 5 Bizums de justo {_thousands(BIZUM_MAX_OPERATION)} Y$, sin pasarte ni un "
         "yapdólar.", R, True),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_min", [
        (1, "bizum_cents", "Te debo 50 céntimos",
         f"Manda un Bizum de {BIZUM_MIN_AMOUNT} Y$, el mínimo.", C, True),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_broke", [
        (1, "bizum_broke", "Todo por un amigo", "Quédate a cero mandando un Bizum.", R, True),
    ])  # fmt: skip

    a += _tiers("bizum", "bizum_sent_count", [
        (500, "bizum_500", "Banco de los amigos (y de Koldo)", "Manda 500 Bizums.", L),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_sent", [
        (10_000_000, "bizumy_10m", "Transferencia a las islas Caimán",
         "Manda 10.000.000 Y$ en Bizums.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "bizum_received_count", [
        (100, "bizumr_100", "Me lo debes", "Recibe 100 Bizums.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_received", [
        (1_000_000, "bizumr_1m", "Vivir de los colegas", "Recibe 1.000.000 Y$ en Bizums.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "bizum_min", [
        (25, "bizum_cents_25", "Bizum de la vergüenza", "Manda 25 Bizums de 5 Y$.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "bizum_full", [
        (25, "bizum_pitufeo_25", "Pitufo profesional",
         "Manda 25 Bizums de justo 10.000 Y$.", E, True),
    ])  # fmt: skip
    # 🏛️ Economía y Hacienda -------------------------------------------------------------
    a += _tiers("economy", "balance_max", [
        (10_000, "rich_10k", "Clase media, especie en extinción", "Ten 10.000 Y$ a la vez.", C),
        (100_000, "rich_100k", "Acomodado de Pozuelo", "Ten 100.000 Y$ a la vez.", R),
        (1_000_000, "rich_1m", "Millonario",
         "Ten 1.000.000 Y$ a la vez. Trump te llamaría pobre.", E),
        (10_000_000, "rich_10m", "Multimillonario, como un exministro consejero",
         "Ten 10.000.000 Y$ a la vez.", L),
        (100_000_000, "rich_100m", "Paraíso fiscal", "Ten 100.000.000 Y$ a la vez.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "imv_claims", [
        (1, "imv_1", "Paguita", "Cobra el IMV por primera vez.", C),
        (30, "imv_30", "Subsidiado", "Cobra el IMV 30 veces. Milei ya ha sacado la motosierra.", E),
        (100, "imv_100", "Abonado al IMV", "Cobra el IMV 100 veces.", L),
        (365, "imv_365", "Un año de paguita", "Cobra el IMV 365 veces.", M),
    ])  # fmt: skip
    a += _tiers("economy", "imv_streak_max", [
        (7, "imvs_7", "Constancia de funcionario", "Cobra el IMV 7 días seguidos.", R),
        (30, "imvs_30", "Disciplina de cartilla de racionamiento",
         "Cobra el IMV 30 días seguidos.", E),
        (100, "imvs_100", "Inquebrantable, como el «no es no»",
         "Cobra el IMV 100 días seguidos.", L),
    ])  # fmt: skip
    a += _tiers("economy", "imv_midnight", [
        (1, "imv_midnight_1", "Cola del SEPE a las 00:00",
         "Cobra el IMV en los primeros 5 minutos del día, hora canaria.", C),
        (30, "imv_midnight_30", "La paguita no duerme",
         "Cobra el IMV 30 veces en los primeros 5 minutos del día.", E),
    ])  # fmt: skip
    a.append(Achievement(
        id="tax_first",
        name="Bienvenido a España",
        description="Paga IRPF por primera vez.",
        category="economy",
        rarity=C,
        conditions=(("tax_paid", 1),),
        secret=True,
        story=FIRST_TAX_STORY,
    ))  # fmt: skip
    a += _tiers("economy", "tax_paid", [
        (1_000, "tax_1k", "Contribuyente de a pie", "Paga 1.000 Y$ de IRPF.", C),
        (10_000, "tax_10k", "Patriota fiscal", "Paga 10.000 Y$ de IRPF.", R),
        (100_000, "tax_100k", "Favorito de Perro Sanxe", "Paga 100.000 Y$ de IRPF.", E),
        (1_000_000, "tax_1m", "Mecenas del Estado", "Paga 1.000.000 Y$ de IRPF.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "wealth_tax_paid", [
        (1, "wealth_1", "Grande de España", "Paga el Impuesto sobre el Patrimonio.", R),
        (10_000, "wealth_10k", "Fortuna amenazada", "Paga 10.000 Y$ de Patrimonio.", E),
        (100_000, "wealth_100k", "Perro Sanxe te pone velas",
         "Paga 100.000 Y$ de Patrimonio.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "wealth_tax_weeks", [
        (10, "wealth_10w", "Rico de toda la vida", "Paga Patrimonio 10 semanas.", L),
    ])  # fmt: skip
    # Intereses de la cuenta (cogs/intereses.py: day_stats, savings_stats y hint_for)
    a += _tiers("bizum", "interest_earned", [
        (1, "interest_1", "La octava maravilla del mundo",
         "Cobra intereses por primera vez. Einstein lo flipaba con esto.", C),
        (1_000, "interest_1k", "El dinero trabaja por ti", "Cobra 1.000 Y$ netos de intereses.", R),
        (10_000, "interest_10k", "Vivir de las rentas", "Cobra 10.000 Y$ netos de intereses.", E),
        (100_000, "interest_100k", "Rentista de toda la vida",
         "Cobra 100.000 Y$ netos de intereses.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "interest_days", [
        (30, "interest_30d", "Cliente fiel, como Ana Botín a sí misma",
         "Cobra intereses 30 días.", E),
        (365, "interest_365d", "El BCE me sigue en Instagram", "Cobra intereses 365 días.", M),
    ])  # fmt: skip
    a.append(Achievement(
        id="interest_tax_1", name="Sanxe cobra antes que tú",
        description="Que te retengan el 19 % de tus intereses.", category="bizum",
        rarity=C, conditions=(("interest_tax", 1),), unit="money", story=INTEREST_TAX_STORY,
    ))  # fmt: skip
    a += _tiers("bizum", "interest_tax", [
        (10_000, "interest_tax_10k", "Perro Sanxe se fuma un puro con tus ahorros",
         "Paga 10.000 Y$ de IRPF por tus intereses.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "interest_avg_max", [
        (INTEREST_TIERS[0][0], "interest_tier_2", "He leído la letra pequeña",
         f"Pasa de {_thousands(INTEREST_TIERS[0][0])} Y$ de saldo medio y que el banco te baje "
         "el tipo.", C),
        (100_000, "interest_mattress", "Para eso lo dejo en el colchón",
         f"Ten 100.000 Y$ de saldo medio: el banco no paga nada por encima de "
         f"{_thousands(INTEREST_TOP)}.", R),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "interest_capped", [
        (1, "interest_falcon", "Ahorro en Falcon",
         f"Cobra el máximo de la cuenta: {_thousands(INTEREST_DAILY_MAX)} Y$ brutos en un día.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_capped_streak", [
        (30, "interest_capped_30", "Rentista de barrio",
         "Cobra el máximo diario 30 días seguidos.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_floor_streak", [
        (90, "interest_grandma", "El plazo fijo de la abuela",
         f"Pasa 90 días seguidos sin bajar de {_thousands(INTEREST_TIERS[0][0])} Y$.", L),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_resist_streak", [
        (30, "interest_resist", "Manual de resistencia",
         f"Pasa 30 días seguidos sin bajar de {_thousands(RESIST_BALANCE)} Y$.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_still_streak", [
        (5, "interest_still_5", "Cinco días de reflexión",
         "Cobra intereses 5 días seguidos sin mover ni un Y$.", R),
        (7, "interest_still_7", "Esto lo pagamos entre todos",
         "Cobra intereses 7 días seguidos sin hacer absolutamente nada.", E),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_ant_streak", [
        (7, "interest_ant", "Hormiguita", "Cobra intereses 7 días seguidos sin gastar nada.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_grasshopper", [
        (1, "interest_grasshopper", "La cigarra",
         "Cobra una nómina y acaba el día sin para una tirada.", C),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_gambled", [
        (1, "interest_gambled", "Me lo fundo en intereses",
         "Pierde en el casino, el mismo día, lo que te acaba de pagar el banco.", C),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_beats_imv", [
        (1, "interest_beats_imv", "Paguita de rentista",
         "Cobra en un día más de intereses que de IMV.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_comeback", [
        (1, "interest_comeback", "Volví solo a por los intereses",
         "Vuelve tras una semana sin aparecer y encuéntrate los intereses cobrados.", R),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_zero", [
        (1, "interest_zero", "Cero patatero",
         "Pasa un día activo con el monedero a cero: ni un Y$ de intereses.", C, True),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_rounding", [
        (1, "interest_rounding", "Redondeo a favor de Hacienda",
         "Cobra tan pocos intereses que el redondeo deja a Sanxe con más del 25 %.", C, True),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_bizum_trick", [
        (1, "interest_bizum_trick", "Ingeniería fiscal de barrio",
         "Haz un Bizum que deje tu saldo justo por debajo de un tramo de la cuenta.", R, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="savings_bracket", name="Me suben de tramo del ahorro",
        description="Que la liquidación semanal de tus intereses llegue al tramo del 21 %.",
        category="bizum", rarity=R, conditions=(("savings_rate_max", 21),),
        story=SAVINGS_BRACKET_STORY,
    ))  # fmt: skip
    a += _tiers("economy", "donated", [
        (1, "donate_1", "Alma caritativa",
         "Dona a una ONG. Más que la Fundación Clinton, en proporción.", C),
        (10_000, "donate_10k", "Filántropo de postureo", "Dona 10.000 Y$ a ONGs.", R),
        (100_000, "donate_100k", "Mecenas del chiringuito", "Dona 100.000 Y$ a ONGs.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "ongs_supported", [
        (4, "donate_all", "Accionista del tercer sector", "Dona a las 4 ONGs.", R),
    ])  # fmt: skip
    a += _tiers("economy", "tax_refunds", [
        (1, "refund_day", "Desgravación", "Recupera IRPF del casino perdiendo el mismo día.", C),
    ])  # fmt: skip
    a += _tiers("economy", "renta_filed", [
        (1, "renta_1", "Declarante, como Shakira (al final)", "Presenta la renta.", C),
        (10, "renta_10", "Asesor fiscal", "Presenta la renta 10 veces.", L),
    ])  # fmt: skip
    a += _tiers("economy", "renta_refunded", [
        (10_000, "renta_10k", "Devolución gorda", "Recupera 10.000 Y$ con la renta.", R),
        (100_000, "renta_100k", "Hacienda somos todos", "Recupera 100.000 Y$ con la renta.", E),
    ], unit="money")  # fmt: skip

    a += _tiers("economy", "tax_paid", [
        (10_000_000, "tax_10m", "Contribuyente del año", "Paga 10.000.000 Y$ de IRPF.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "wealth_tax_paid", [
        (1_000_000, "wealth_1m", "Grandes fortunas", "Paga 1.000.000 Y$ de Patrimonio.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "wealth_tax_weeks", [
        (52, "wealth_52w", "Rico de cuna", "Paga Patrimonio 52 semanas.", M),
    ])  # fmt: skip
    a += _tiers("economy", "donated", [
        (1_000_000, "donate_1m", "Filántropo de chiringuito", "Dona 1.000.000 Y$ a ONGs.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "tax_refunds", [
        (10, "refund_10", "Sanxe me devuelve lo mío",
         "Recupera IRPF del casino perdiendo el mismo día 10 veces.", R),
        (100, "refund_100", "Ingeniero fiscal", "Recupera IRPF del casino 100 veces.", E),
    ])  # fmt: skip
    a += _tiers("economy", "renta_filed", [
        (52, "renta_52", "Un año entero declarando", "Presenta la renta 52 veces.", M),
    ])  # fmt: skip
    a += _tiers("economy", "renta_refunded", [
        (1_000_000, "renta_1m", "Hacienda me debe la vida",
         "Recupera 1.000.000 Y$ con la renta.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "imv_streak_max", [
        (365, "imvs_365", "Paguita vitalicia", "Cobra el IMV 365 días seguidos.", M, True),
    ])  # fmt: skip

    a += _tiers("bizum", "interest_earned", [
        (1_000_000, "interest_1m", "Rentista de Mónaco",
         "Cobra 1.000.000 Y$ netos de intereses.", M, True),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "interest_tax", [
        (100_000, "interest_tax_100k", "Sanxe se fuma tus ahorros",
         "Paga 100.000 Y$ de IRPF por tus intereses.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("bizum", "interest_capped", [
        (100, "interest_capped_100", "Tope de la casa", "Cobra el máximo diario 100 días.", L),
    ])  # fmt: skip
    a += _tiers("bizum", "interest_still_streak", [
        (30, "interest_still_30", "Momia financiera",
         "Cobra intereses 30 días seguidos sin mover ni un Y$.", L, True),
    ])  # fmt: skip

    # 🎟️ Loterías -------------------------------------------------------------------------
    a += _tiers("lottery", "lottery_bets", [
        (1, "lotto_1", "Hoy me toca, como a Fabra", "Compra un décimo o una apuesta.", C),
        (50, "lotto_50", "Fijo en la administración de Sort", "Compra 50 décimos o apuestas.", R),
        (500, "lotto_500", "Cliente de Doña Manolita", "Compra 500 décimos o apuestas.", E),
        (5_000, "lotto_5k", "La suerte al por mayor", "Compra 5.000 décimos o apuestas.", L),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_spent", [
        (10_000, "lspend_10k", "Impuesto a la ilusión", "Juega 10.000 Y$ a la lotería.", C),
        (100_000, "lspend_100k", "El Estado te lo agradece", "Juega 100.000 Y$.", R),
        (1_000_000, "lspend_1m", "Mecenas de Hacienda", "Juega 1.000.000 Y$.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("lottery", "lottery_prizes", [
        (1, "lwin_1", "¡Me ha tocado!", "Cobra un premio de lotería, aunque sea el reintegro.", C),
        (25, "lwin_25", "Tocado por la suerte de Fabra", "Cobra 25 premios de lotería.", R),
        (250, "lwin_250", "Cuestión de estadística, según Fabra",
         "Cobra 250 premios de lotería. Él decía que era suerte.", E),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_won", [
        (10_000, "lwon_10k", "Para gastos (de representación)", "Gana 10.000 Y$ en loterías.", C),
        (100_000, "lwon_100k", "Pellizco de Bruselas", "Gana 100.000 Y$ en loterías.", R),
        (1_000_000, "lwon_1m", "Pelotazo de lotería", "Gana 1.000.000 Y$ en loterías.", E),
        (10_000_000, "lwon_10m", "Me jubilo", "Gana 10.000.000 Y$ en loterías.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("lottery", "lottery_win_max", [
        (100_000, "lbig_100k", "Un buen pellizco", "Cobra 100.000 Y$ con un solo boleto.", E),
        (4_000_000, "lbig_4m", "Hoy no se trabaja", "Cobra 4.000.000 Y$ con un solo boleto.",
         M, True),
    ], unit="money")  # fmt: skip
    a += _tiers("lottery", "lottery_reintegros", [
        (1, "reint_1", "Al menos lo recupero, como el rescate bancario (no)",
         "Cobra un reintegro.", C),
        (50, "reint_50", "Vuelta a empezar", "Cobra 50 reintegros.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_navidad", [
        (1, "xmas_1", "Décimo de la suerte", "Compra un décimo de Navidad.", C),
        (20, "xmas_20", "La peña de la oficina", "Compra 20 décimos de Navidad.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_nino", [
        (1, "nino_1", "Los Reyes también juegan", "Compra un décimo del Niño.", C),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_pedrea", [
        (1, "pedrea", "Pedrea", "Cobra una pedrea de la Lotería de Navidad.", E, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_gordo_navidad", [
        (1, "el_gordo", "EL GORDO", "Te toca el Gordo de Navidad.", M, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_euro_bets", [
        (1, "euro_1", "Europeísta, como Macron en un mitin",
         "Juega una apuesta de Euromillones.", C),
        (100, "euro_100", "Soñando en euros, viviendo en pesetas",
         "Juega 100 apuestas de Euromillones.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_lotto4", [
        (1, "lotto4", "Cuatro de seis", "Acierta 4 números en la Primitiva o la Bonoloto.", E),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_lotto5", [
        (1, "lotto5", "Rozando el cielo", "Acierta 5 en la Primitiva o la Bonoloto.", L, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_jackpot", [
        (1, "lotto_jackpot", "El bote de la Primitiva",
         "Llévate la 1ª categoría de un juego de bote.", M, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_scratches", [
        (1, "rasca_1", "Rasca y gana", "Rasca un boleto de la ONCE.", C),
        (100, "rasca_100", "Uña de oro de la ONCE", "Rasca 100 boletos.", R),
        (1_000, "rasca_1k", "Sin uñas", "Rasca 1.000 boletos.", E),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_scratch_top", [
        (1, "rasca_top", "Premio máximo", "Saca el premio más alto de un rasca.", L, True),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_draw_bets_max", [
        (MAX_LOTTERY_PER_DRAW, "brute_force", "Fuerza bruta",
         f"Juega {MAX_LOTTERY_PER_DRAW} apuestas o décimos en un mismo sorteo.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_broke_buy", [
        (1, "lotto_broke", "Todo al número", "Gasta todo tu saldo en lotería.", L, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="gravamen",
        name="Hacienda también juega",
        description="Paga el gravamen especial de un premio de lotería.",
        category="lottery",
        rarity=E,
        conditions=(("lottery_gravamen", 1),),
        secret=True,
        story=LOTTERY_TAX_STORY,
    ))  # fmt: skip

    a += _tiers("lottery", "lottery_bets", [
        (25_000, "lotto_25k", "Administración de loterías", "Compra 25.000 décimos o apuestas.", M),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_spent", [
        (10_000_000, "lspend_10m", "Contribuyente voluntario",
         "Juega 10.000.000 Y$ a la lotería.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("lottery", "lottery_prizes", [
        (1_000, "lwin_1k", "Afortunado crónico", "Cobra 1.000 premios de lotería.", L),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_reintegros", [
        (500, "reint_500", "Me quedo como estaba", "Cobra 500 reintegros.", E),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_scratches", [
        (10_000, "rasca_10k", "Uñas de acero", "Rasca 10.000 boletos.", L),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_navidad", [
        (100, "xmas_100", "Peña de la oficina", "Compra 100 décimos de Navidad.", E),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_nino", [
        (20, "nino_20", "Los Reyes ya pasaron", "Compra 20 décimos del Niño.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_euro_bets", [
        (1_000, "euro_1k", "Ciudadano europeo", "Juega 1.000 apuestas de Euromillones.", E),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_game_primitiva", [
        (100, "primitiva_100", "Primitivo, como un discurso de Abascal a caballo",
         "Juega 100 apuestas de La Primitiva.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_game_bonoloto", [
        (250, "bonoloto_250", "Bonoloto de cada día", "Juega 250 apuestas de Bonoloto.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_game_gordo", [
        (50, "gordo_50", "El Gordo de los domingos",
         "Juega 50 apuestas de El Gordo de la Primitiva.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_game_jueves", [
        (50, "jueves_50", "Jueves de lotería", "Compra 50 décimos del jueves.", R),
    ])  # fmt: skip
    a += _tiers("lottery", "lottery_game_sabado", [
        (50, "sabado_50", "Sábado de lotería", "Compra 50 décimos del sábado.", R),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="lottery_all_games",
            name="Ludopatía de Estado",
            description="Juega a todas las loterías: las nacionales, las de bote y un rasca.",
            category="lottery",
            rarity=R,
            conditions=(
                *(
                    (f"lottery_game_{game}", 1)
                    for game in (
                        "jueves",
                        "sabado",
                        "navidad",
                        "nino",
                        "primitiva",
                        "bonoloto",
                        "gordo",
                        "euromillones",
                    )
                ),
                ("lottery_scratches", 1),
            ),  # fmt: skip
        )
    )

    # 🛍️ Tienda ---------------------------------------------------------------------------
    a += _tiers("shop", "shop_purchases", [
        (1, "shop_1", "Estrenando cartera", "Compra algo en la tienda.", C),
        (10, "shop_10", "Cliente fijo de Temu", "Haz 10 compras en la tienda.", C),
        (50, "shop_50", "Comprador compulsivo de AliExpress", "Haz 50 compras en la tienda.", R),
        (200, "shop_200", "Tarjeta de socio", "Haz 200 compras en la tienda.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_spent", [
        (10_000, "spend_10k", "Consumista, como un ministro con tarjeta",
         "Gasta 10.000 Y$ en la tienda.", C),
        (100_000, "spend_100k", "Motor de la economía", "Gasta 100.000 Y$ en la tienda.", R),
        (1_000_000, "spend_1m", "El PIB eres tú", "Gasta 1.000.000 Y$ en la tienda.", E),
        (10_000_000, "spend_10m", "Ballena de la tienda", "Gasta 10.000.000 Y$ en la tienda.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_igic", [
        (1_000, "igic_1k", "Aquí hasta el café paga", "Paga 1.000 Y$ de IGIC.", C),
        (25_000, "igic_25k", "Sostén del Cabildo", "Paga 25.000 Y$ de IGIC.", R),
        (250_000, "igic_250k", "Contribuyente canario de honor", "Paga 250.000 Y$ de IGIC.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_roles", [
        (1, "shoprole_1", "Con estilo, como Macron", "Cómprate un rol.", C),
        (5, "shoprole_5", "Armario lleno, como el de Imelda Marcos", "Compra 5 roles.", R),
        (20, "shoprole_20", "Camaleón", "Compra 20 roles.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_renewals", [
        (3, "renew_3", "Inquilino fiel", "Renueva un alquiler de rol 3 veces.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_boosts", [
        (1, "boost_1", "Turbo, como un decreto ley", "Compra un potenciador de XP.", C),
        (10, "boost_10", "Dopado, como un ciclista de los noventa",
         "Compra 10 potenciadores de XP.", R),
        (50, "boost_50", "Nitro humano", "Compra 50 potenciadores de XP.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_boost_queue_max", [
        (3, "boost_queue", "Turbo en cola", "Ten 3 potenciadores esperando turno a la vez.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_collection_max", [
        (1, "collect_1", "Primera pieza del Prado", "Consigue un coleccionable.", C),
        (5, "collect_5", "Vitrina, como la de los regalos de Trump",
         "Ten 5 coleccionables distintos.", R),
        (15, "collect_15", "Museo privado", "Ten 15 coleccionables distintos.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_sale_buys", [
        (1, "sale_1", "Cazador de rebajas de El Corte Inglés", "Compra algo rebajado.", C),
        (10, "sale_10", "Black Friday", "Compra 10 cosas rebajadas.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_discount_max", [
        (50, "sale_half", "A mitad de precio", "Compra algo con un 50 % de rebaja o más.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_luxury", [
        (1, "luxury_1", "Nuevo rico de Marbella",
         "Compra algo que paga el IGIC de lujo (15 %).", R),
        (10, "luxury_10", "Clase alta", "Compra 10 cosas con IGIC de lujo.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_big_buy_max", [
        (50_000, "bigbuy_50k", "Capricho caro", "Paga 50.000 Y$ de una sola vez.", R),
        (500_000, "bigbuy_500k", "Tarjeta negra", "Paga 500.000 Y$ de una sola vez.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_limited", [
        (1, "limited_1", "Edición limitada", "Compra una unidad de una edición limitada.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_first_serial", [
        (1, "serial_1", "Unidad nº 1", "Llévate la primera unidad de una edición limitada.", E,
         True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_last_unit", [
        (1, "last_unit", "El último mohicano", "Llévate la última unidad de algo.", E, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_broke_buy", [
        (1, "broke_buy", "Lo quiero, lo tengo", "Gástate todo tu saldo en una compra.", L, True),
    ])  # fmt: skip

    a += _tiers("shop", "shop_purchases", [
        (1_000, "shop_1k", "Cliente VIP", "Haz 1.000 compras en la tienda.", L),
    ])  # fmt: skip
    a += _tiers("shop", "shop_spent", [
        (100_000_000, "spend_100m", "Consumismo de Estado",
         "Gasta 100.000.000 Y$ en la tienda.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_igic", [
        (1_000_000, "igic_1m", "Canarias te lo agradece", "Paga 1.000.000 Y$ de IGIC.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_roles", [
        (50, "shoprole_50", "Coleccionista de títulos", "Compra 50 roles.", L),
    ])  # fmt: skip
    a += _tiers("shop", "shop_renewals", [
        (25, "renew_25", "Alquiler indefinido", "Renueva un alquiler de rol 25 veces.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_boosts", [
        (200, "boost_200", "Esteroides de XP", "Compra 200 potenciadores de XP.", L),
    ])  # fmt: skip
    a += _tiers("shop", "shop_sale_buys", [
        (100, "sale_100", "Black Friday permanente", "Compra 100 cosas rebajadas.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_luxury", [
        (50, "luxury_50", "Lujo asiático", "Compra 50 cosas con IGIC de lujo.", L),
    ])  # fmt: skip
    a += _tiers("shop", "shop_limited", [
        (10, "limited_10", "Edición de coleccionista",
         "Compra 10 unidades de ediciones limitadas.", R),
        (50, "limited_50", "Revendedor de zapatillas", "Compra 50 unidades limitadas.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_first_serial", [
        (5, "serial_5", "Número uno siempre",
         "Llévate la unidad nº 1 de 5 ediciones limitadas.", L, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_last_unit", [
        (5, "last_unit_5", "Te lo quito de las manos", "Llévate la última unidad 5 veces.", L),
    ])  # fmt: skip
    a += _tiers("shop", "shop_boost_queue_max", [
        (6, "boost_queue_6", "Atasco de potenciadores", "Ten 6 potenciadores esperando turno.", E),
    ])  # fmt: skip

    # 🛍️ Tienda: el surtido de serie y los objetos que se usan --------------------------
    a += _tiers("shop", "shop_uses", [
        (1, "use_1", "Pa' eso lo compré", "Usa un objeto de la mochila.", C),
        (25, "use_25", "Gamberro de barrio", "Usa 25 objetos.", C),
        (100, "use_100", "Altercado público", "Usa 100 objetos.", R),
        (1_000, "use_1k", "Terror del servidor", "Usa 1.000 objetos.", E),
        (5_000, "use_5k", "Vandalismo de Estado", "Usa 5.000 objetos.", L),
    ])  # fmt: skip
    a += _tiers("shop", SHOP_USE_KINDS_STAT, [
        (5, "use_kinds_5", "Probador", "Usa 5 objetos distintos.", R),
        (20, "use_kinds_20", "Catador oficial del colmado", "Usa 20 objetos distintos.", E),
        (len(SHOP_USES), "use_kinds_all", "Lo he probado todo",
         f"Usa los {len(SHOP_USES)} tipos de objeto del colmado.", L),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_targeted", [
        (1, "target_1", "Con cariño, como Putin con sus vecinos",
         "Usa un objeto contra alguien.", C),
        (50, "target_50", "Francotirador de tupper", "Usa 50 objetos contra alguien.", R),
        (500, "target_500", "Enemigo público nº 1", "Usa 500 objetos contra alguien.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_got_hit", [
        (1, "hit_1", "Diana",
         "Que alguien use un objeto contra ti. Bienvenido a la sesión de control.", C),
        (25, "hit_25", "Saco de boxeo", "Que usen 25 objetos contra ti.", R),
        (250, "hit_250", "Pim, pam, pum del servidor", "Que usen 250 objetos contra ti.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_got_messy", [
        (10, "messy_10", "Pringado (literalmente)",
         "Recibe 10 huevos, tomates, tartazos, globos, pellas de gofio o mojo.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_got_love", [
        (10, "love_10", "Querido por el pueblo",
         "Recibe 10 ramos, abrazos, cartas, barraquitos, perreos, dim sum o sobres rojos.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_got_raided", [
        (1, "raided_1", "Diligencias abiertas", "Que te registre la UCO por un chivatazo.", C),
        (10, "raided_10", "Registro de los martes", "Que te registre la UCO 10 veces.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_self", [
        (1, "use_self", "Me lo merezco", "Úsate un objeto a ti mismo.", C, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_bot", [
        (1, "use_bot", "Muerde la mano que te da de comer",
         "Intenta usar un objeto contra el bot.", R, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_backfires", [
        (1, "backfire_1", "Karma instantáneo", "Que te salga el tiro por la culata.", C),
        (25, "backfire_25", "Tu peor enemigo eres tú",
         "Que te salga el tiro por la culata 25 veces.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_hits", [
        (100, "hits_100", "Puntería de chancla", "Acierta 100 lanzamientos.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_egg_collector", [
        (1, "egg_sanxe", "Huevo a Hacienda",
         "Dale con un huevo a Perro Sanxe sin querer.", R, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_caught", [
        (1, "caught_1", "Reflejos de portero",
         "Que tu objetivo cace al vuelo lo que le lanzas.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_d20_nat20", [
        (1, "nat20_1", "Crítico natural", "Saca un 20 con el dado de 20 caras.", R),
        (10, "nat20_10", "Bendecido por los dados", "Saca 10 veces un 20 natural.", L),
    ])  # fmt: skip
    a += _tiers("shop", "shop_d20_nat1", [
        (1, "nat1_1", "Pifia", "Saca un 1 con el dado de 20 caras.", R, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_padron_hot", [
        (1, "padron_1", "Unos pican y otros no", "Cómete un pimiento de Padrón que pica.", C),
        (25, "padron_25", "Lengua de amianto", "Cómete 25 pimientos de Padrón que pican.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_robuso_asleep", [
        (1, "robuso_asleep", "Despertador internacional",
         "Llama a Robuso cuando en Hong Kong es de madrugada.", C, True),
    ])  # fmt: skip
    a += _tiers("shop", f"{SHOP_USED_PREFIX}robuso", [
        (10, "robuso_10", "Tarifa plana con Hong Kong", "Llama a Robuso 10 veces.", R),
    ])  # fmt: skip
    a += _tiers("shop", f"{SHOP_USED_PREFIX}huevo", [
        (50, "eggs_50", "Huevina", "Lanza 50 huevos.", R),
    ])  # fmt: skip
    a += _tiers("shop", f"{SHOP_USED_PREFIX}uco", [
        (10, "uco_10", "Colaborador habitual de la UCO", "Da 10 chivatazos a la UCO.", E),
    ])  # fmt: skip
    a += _tiers("shop", f"{SHOP_USED_PREFIX}indulto", [
        (3, "indulto_3", "El BOE es mío", "Concede 3 indultos.", E, True),
    ])  # fmt: skip
    a += _tiers("shop", f"{SHOP_USED_PREFIX}bulo", [
        (25, "bulo_25", "Director de la máquina del fango", "Publica 25 bulos.", E),
    ])  # fmt: skip
    a += _tiers("shop", f"{SHOP_USED_PREFIX}cis", [
        (10, "cis_10", "Cocinero del CIS", "Publica 10 encuestas del CIS.", R),
    ])  # fmt: skip
    a += _tiers("shop", f"{SHOP_USED_PREFIX}megafono", [
        (5, "megafono_5", "Pregonero de las fiestas", "Grita 5 veces por el megáfono.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_nickname", [
        (1, "dni_1", "Identidad falsa", "Cámbiate el apodo con un DNI falso.", R),
    ])  # fmt: skip
    a += _tiers("shop", "shop_mystery", [
        (1, "mystery_1", "Caja de Pandora", "Abre una caja botín.", C),
        (25, "mystery_25", "Ludopatía de cajas botín", "Abre 25 cajas botín.", R),
        (250, "mystery_250", "Ley de cajas botín, ¿para cuándo?", "Abre 250 cajas botín.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_mystery_dupe", [
        (1, "mystery_dupe", "Repe", "Que la caja botín te dé algo que ya tenías.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_mystery_best", [
        (10_000, "mystery_10k", "Por una vez sale a cuenta",
         "Que la caja botín te dé algo de 10.000 Y$ o más.", R),
    ], unit="money")  # fmt: skip
    a += _tiers("shop", "shop_mystery_jackpot", [
        (1, "mystery_jackpot", "Me tocó el gordo de la caja",
         "Que la caja botín te dé algo de 100.000 Y$ o más.", L, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_consumed", [
        (100, "consumed_100", "Usar y tirar", "Gasta 100 objetos de un solo uso.", R),
        (1_000, "consumed_1k", "Sociedad de consumo", "Gasta 1.000 objetos de un solo uso.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_night", [
        (1, "use_night", "Gamberro de madrugada", "Usa un objeto entre las 3 y las 6.", C, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_newyear", [
        (1, "use_newyear", "Campanadas con petardo", "Usa un objeto en Nochevieja o Año Nuevo.",
         C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_halloween", [
        (1, "use_halloween", "Truco, trato o tomatazo", "Usa un objeto en Halloween.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_canarias", [
        (1, "use_canarias", "Día de Canarias en el colmado",
         "Usa un objeto el 30 de mayo.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_use_pino", [
        (1, "use_pino", "Romería del Pino", "Usa un objeto el 8 de septiembre.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_clover_broken", [
        (1, "clover_broken", "Trébol de tres hojas",
         "Frota tan fuerte el trébol que se le cae una hoja.", C, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_no_parsley", [
        (1, "no_parsley", "Sin perejil no hay milagro",
         "Pídele algo a San Pancracio sin perejil.", C, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_flash", [
        (1, "shop_flash", "Ropa interior a la vista",
         "Enseña al canal la ropa interior roja de Nochevieja.", C, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_fake_champagne", [
        (1, "fake_champagne", "Era cava", "Descorcha el champán francés y que resulte ser cava.",
         R, True),
    ])  # fmt: skip
    a += _tiers("shop", "shop_ball_dogs", [
        (1, "ball_dogs", "Suelta de perros",
         "Lanza la pelota y que salga detrás medio barrio de perros.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_titles", [
        (1, "title_1", "Con tratamiento", "Compra un título para lucirlo junto a tu nombre.", C),
        (3, "title_3", "Currículum de alto standing", "Ten 3 títulos.", R),
        (6, "title_6", "Más títulos que la casa de Alba", "Ten 6 títulos.", E),
        (len(SHOP_TITLE_KEYS), "title_all", "Todos los honores",
         f"Ten los {len(SHOP_TITLE_KEYS)} títulos del colmado.", M),
    ])  # fmt: skip
    a += _tiers("shop", "shop_title_swaps", [
        (10, "title_swap_10", "Tratamiento a la carta", "Cambia de título 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_skins", [
        (1, "skin_1", "Mesa a mi gusto", "Compra una skin para una mesa del casino.", C),
        (3, "skin_3", "Decorador de interiores", "Compra 3 skins para el casino.", R),
        (6, "skin_6", "Escaparatista de Las Vegas", "Compra 6 skins para el casino.", E),
    ])  # fmt: skip
    a += _tiers("shop", "shop_skin_swaps", [
        (10, "skin_swap_10", "Interiorista de casino", "Cambia de skin 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("shop", "shop_title_off", [
        (1, "title_off", "Llámame de tú", "Quítate el título y quédate sin ninguno.", C, True),
    ])  # fmt: skip
    a += _tiers("shop", SHOP_AISLES_STAT, [
        (3, "aisles_3", "De paseo por el colmado", "Compra en 3 pasillos distintos.", C),
        (len(SHOP_AISLES), "aisles_all", "Me conozco todos los pasillos",
         f"Compra en los {len(SHOP_AISLES)} pasillos del colmado.", E),
    ])  # fmt: skip
    for key, achievement_id, name, description, rarity, secret in (
        ("piedra", "buy_piedra", "La compra más honesta", "Compra la piedra.", C, False),
        ("manual_resistencia", "buy_manual", "Libro de cabecera",
         "Compra el Manual de resistencia.", C, False),
        ("aire_moncloa", "buy_aire", "Pagar por nada", "Compra aire de La Moncloa.", C, True),
        ("nada", "buy_nada", "Nada de nada", "Compra nada. Literalmente.", R, True),
        ("recibo_luz", "buy_luz", "Pagar la luz por gusto",
         "Compra el recibo de la luz de enero.", R, True),
        ("nft_mono", "buy_nft", "Web3 en 2026", "Compra el NFT del mono aburrido.", R, True),
        ("escano", "buy_escano", "Diputado de cartón", "Compra un escaño del Congreso.", R,
         False),
        ("asesor", "buy_asesor", "Asesor de nada", "Compra una plaza de asesor sin funciones.", E,
         True),
        ("caca_oro", "buy_caca", "Lujo escatológico", "Compra la caca bañada en oro.", E, True),
        ("lingote", "buy_lingote", "Reserva de oro", "Compra un lingote de oro.", E, False),
        ("yate", "buy_yate", "Puerto Rico, pero el de aquí", "Compra un yate.", L, False),
        ("patata_espana", "buy_patata", "Sin Canarias, como en el telediario",
         "Llévate la única patata con forma de España.", L, True),
        ("platano_cinta", "buy_platano", "Arte contemporáneo",
         "Compra el plátano pegado a la pared con cinta.", L, True),
        ("falcon", "buy_falcon", "Yo también tengo Falcon", "Compra el Falcon de Moncloa.", M,
         True),
        ("verificado", "buy_verificado", "Check azul", "Compra la cuenta verificada.", R, False),
        ("master_express", "buy_master", "Máster exprés", "Compra el máster convalidado.", R,
         False),
        ("doctorado", "buy_doctorado", "Cum laude", "Compra el doctorado cum laude.", E, False),
        ("aforamiento", "buy_aforamiento", "Intocable", "Compra el aforamiento.", E, False),
        ("palco_bernabeu", "buy_palco", "Los negocios, en el palco",
         "Compra un asiento en el palco del Bernabéu.", L, False),
        ("puerta_giratoria", "buy_puerta", "Del Consejo de Ministros al de administración",
         "Compra un sillón en el consejo de administración.", L, False),
        ("porsche", "buy_porsche", "Concejal de urbanismo", "Compra el Porsche 911.", L, True),
        ("condado_teide", "buy_condado", "Señorío del volcán", "Compra el Condado del Teide.", L,
         False),
        ("marquesado_mogan", "buy_marquesado", "Con vistas al puerto",
         "Compra el Marquesado de Mogán.", L, False),
        ("grandeza", "buy_grandeza", "Grande entre los grandes",
         "Compra la Grandeza de España.", M, False),
        ("skin_tapete_oro", "buy_tapete_oro", "Rey Midas del casino",
         "Compra el tapete de oro. Hacienda ya está tomando nota.", E, False),
        ("skin_tapete_espana", "buy_tapete_espana", "Con el escudo bien puesto",
         "Compra el tapete rojigualda.", R, False),
        ("skin_tapete_israel", "buy_tapete_israel", "Shalom, crupier",
         "Compra el tapete de Israel.", R, False),
        ("skin_tapete_canarias", "buy_tapete_canarias", "Apostando a la sombra del Teide",
         "Compra el tapete del Teide.", R, False),
        ("skin_tapete_boricua", "buy_tapete_boricua", "¡Wepa en la mesa!",
         "Compra el tapete monoestrellado.", R, False),
    ):  # fmt: skip
        a.append(Achievement(
            id=achievement_id, name=name, description=description, category="shop",
            rarity=rarity, conditions=((f"shop_key_{key}", 1),), secret=secret,
        ))  # fmt: skip
    a.append(Achievement(
        id="tapetes_banderas", name="Asamblea General de la ONU",
        description="Ten los cuatro tapetes de bandera: rojigualda, Israel, Teide y boricua.",
        category="shop", rarity=E,
        conditions=tuple((f"shop_key_skin_tapete_{k}", 1)
                         for k in ("espana", "israel", "canarias", "boricua")),
    ))  # fmt: skip

    # 🐾 Mascotas ------------------------------------------------------------------------
    a.append(Achievement(
        id="pet_1", name="Familia monoparental",
        description="Consigue tu primera mascota. Lagartijo, iguana, lagarto…",
        category="pets", rarity=C, conditions=((PET_SPECIES_STAT, 1),), story=ADOPTION_STORY,
    ))  # fmt: skip
    a += _tiers("pets", "pet_adopted", [
        (3, "pet_adopt_3", "Casa de acogida", "Adopta 3 mascotas en el colmado.", R),
        (10, "pet_adopt_10", "Arca de Noé", "Adopta 10 mascotas en el colmado.", E),
    ])  # fmt: skip
    a += _tiers("pets", PET_SPECIES_STAT, [
        (5, "pet_species_5", "Zoológico de barrio", "Ten 5 especies distintas.", R),
        (15, "pet_species_15", "Bioparc", "Ten 15 especies distintas.", E),
        (sum(1 for sp in PET_SPECIES if sp.adoptable), "pet_species_shop",
         "Todo el escaparate", "Ten todas las especies que se adoptan en el colmado.", L),
        (len(PET_SPECIES), "pet_species_all", "Ni Félix Rodríguez de la Fuente",
         "Ten todas las especies, también las que aparecen solas.", M),
    ])  # fmt: skip
    a += _tiers("pets", "pet_protectora", [
        (1, "pet_protectora", "Adopta, no compres",
         "Adopta un perro, un gato o un hurón, que no se venden desde la Ley 7/2023.", C),
    ])  # fmt: skip
    a += _tiers("pets", "pet_spawned", [
        (1, "pet_spawn_1", "Okupa", "Que una mascota aparezca sola en tu casa.", R),
    ])  # fmt: skip
    a += _tiers("pets", PET_SPAWN_KINDS_STAT, [
        (len(PET_SPAWNING), "pet_spawn_all", "Ley de okupación animal",
         f"Que te aparezcan solas las {len(PET_SPAWNING)} especies que no se venden.", L),
    ])  # fmt: skip
    a += _tiers("pets", "pet_owned_max", [
        (5, "pet_owned_5", "Casa llena, como el Consejo de Ministros",
         "Ten 5 mascotas a la vez.", R),
        (10, "pet_owned_10", "Esto es un zoo", "Ten 10 mascotas a la vez.", E),
    ])  # fmt: skip
    for key, achievement_id, name, description, rarity, secret in (
        ("cucaracha", "pet_cucaracha", "Compañera de piso",
         "Que se te meta en casa la cucaracha superviviente.", R, True),
        ("gato_callejero", "pet_callejero", "Undécima vida",
         "Que te elija el gato callejero tuerto.", E, False),
        ("paloma", "pet_paloma", "Plaza Mayor", "Que se te pose la paloma al salir de currar.",
         R, False),
        ("cotorra", "pet_cotorra", "Invasión argentina",
         "Que se te mude la cotorra al subir de nivel.", R, False),
        ("lagarto", "pet_lagarto", "Especie protegida",
         "Que te aparezca el lagarto gigante de El Hierro el Día de Canarias.", L, False),
        ("mosquito", "pet_mosquito", "Verano en España", "Que se te pegue el mosquito tigre.",
         R, True),
        ("pedrusco", "pet_pedrusco", "Moda de 1975", "Adopta la piedra mascota.", C, True),
        ("presa", "pet_presa", "Corazón de pan", "Adopta un presa canario.", R, False),
        ("koi", "pet_koi", "Prosperidad de Mong Kok", "Adopta la carpa koi.", E, False),
        ("caballo", "pet_caballo", "Feria de Abril en casa",
         "Adopta el caballo de pura raza española.", E, False),
        ("panda", "pet_panda", "Diplomacia del panda", "Consigue un panda en préstamo.", L,
         False),
        ("perro_sanxe", "pet_sanxe", "El perro es mío",
         "Adopta a Perro Sanxe. Solo hay uno en todo el servidor.", M, False),
    ):  # fmt: skip
        a.append(Achievement(
            id=achievement_id, name=name, description=description, category="pets",
            rarity=rarity, conditions=((f"{PET_SPECIES_PREFIX}{key}", 1),), secret=secret,
        ))  # fmt: skip
    for achievement_id, name, description, rarity, keys in (
        ("pet_perro_gato", "Como el perro y el gato", "Ten un perro y un gato a la vez.", R,
         ("perro", "gato")),
        ("pet_islas", "Fauna de las islas",
         "Ten el canario, el bardino, la cabra majorera y el presa canario.", E,
         ("canario", "bardino", "cabra", "presa")),
        ("pet_boricua", "Patio boricua", "Ten el coquí y el gallo de patio.", R,
         ("coqui", "gallo")),
        ("pet_pijos", "Pijerío animal",
         "Ten el caniche de Serrano, el pavo real y el caballo de pura raza.", L,
         ("caniche", "pavo_real", "caballo")),
    ):  # fmt: skip
        a.append(Achievement(
            id=achievement_id, name=name, description=description, category="pets",
            rarity=rarity,
            conditions=tuple((f"{PET_SPECIES_PREFIX}{key}", 1) for key in keys),
        ))  # fmt: skip
    a += _tiers("pets", "pet_cares", [
        (1, "pet_care_1", "Primera caricia", "Cuida a una mascota.", C),
        (50, "pet_care_50", "Cuidador del Imserso", "Cuida 50 veces a tus mascotas.", R),
        (500, "pet_care_500", "Veterinario sin título", "Cuida 500 veces a tus mascotas.", E),
        (2_000, "pet_care_2k", "Protectora con patas", "Cuida 2.000 veces a tus mascotas.", L),
    ])  # fmt: skip
    a += _tiers("pets", "pet_petted", [
        (100, "pet_petted_100", "Mano de santo", "Acaricia 100 veces a tus mascotas.", E),
    ])  # fmt: skip
    a += _tiers("pets", "pet_played", [
        (100, "pet_played_100", "Compañero de juegos", "Juega 100 veces con tus mascotas.", E),
    ])  # fmt: skip
    a += _tiers("pets", "pet_fed", [
        (1, "pet_fed_1", "Hora de comer",
         "Dale de comer a una mascota. ¿Con pulpa? ¡Sin pulpa!", C),
        (100, "pet_fed_100", "Comedor social", "Dale de comer 100 veces a tus mascotas.", E),
    ])  # fmt: skip
    a += _tiers("pets", "pet_favourite", [
        (1, "pet_fav_1", "Eso le encanta", "Dale a una mascota su comida favorita.", C),
        (50, "pet_fav_50", "Chef de pienso", "Dales 50 veces su comida favorita.", E),
    ])  # fmt: skip
    a += _tiers("pets", "pet_bond_max", [
        (1, "pet_bond_1", "Nos vamos conociendo, como Sánchez y Puigdemont",
         "Sube a nivel 1 el vínculo con una mascota.",
         C),
        (5, "pet_bond_5", "Uña y carne, como Trump y Musk (al principio)",
         "Sube a nivel 5 el vínculo con una mascota.", R),
        (10, "pet_bond_10", "Inseparables", "Sube a nivel 10 el vínculo con una mascota.", E),
    ])  # fmt: skip
    a += _tiers("pets", "pet_tricks_max", [
        (1, "pet_tricks_1", "Sabe hacer cosas, más que un ministro de Cultura",
         "Que una mascota aprenda su primer truco.", R),
        (3, "pet_tricks_3", "Circo del Sol", "Que una mascota se sepa sus tres trucos.", E),
    ])  # fmt: skip
    a += _tiers("pets", "pet_streak_max", [
        (7, "pet_streak_7", "Una semana sin fallar", "Cuida a tus mascotas 7 días seguidos.", R),
        (30, "pet_streak_30", "Responsabilidad afectiva",
         "Cuida a tus mascotas 30 días seguidos.", E),
        (100, "pet_streak_100", "Ni un día sin paseo", "Cuida a tus mascotas 100 días seguidos.",
         L),
        (365, "pet_streak_365", "Un año dándole de comer",
         "Cuida a tus mascotas 365 días seguidos.", M),
    ])  # fmt: skip
    a += _tiers("pets", "pet_gifts", [
        (1, "pet_gift_1", "Un regalito, como los relojes de Camps",
         "Que una mascota te traiga un regalo.", C),
        (10, "pet_gift_10", "Lo que trae el gato", "Que tus mascotas te traigan 10 regalos.", E),
        (50, "pet_gift_50", "Mantenido por la mascota",
         "Que tus mascotas te traigan 50 regalos.", L),
    ])  # fmt: skip
    a += _tiers("pets", "pet_cameos", [
        (1, "pet_cameo_1", "Robaescenas", "Que tu mascota salga en un mensaje del bot.", C),
        (100, "pet_cameo_100", "Estrella invitada", "Que tu mascota salga 100 veces.", R),
        (1_000, "pet_cameo_1k", "Más pantalla que Jovani", "Que tu mascota salga 1.000 veces.",
         E),
    ])  # fmt: skip
    a += _tiers("pets", "pet_cameo_bust", [
        (1, "pet_cameo_bust", "Hasta la mascota se va",
         "Que tu mascota te vea quedarte a cero.", C, True),
    ])  # fmt: skip
    a += _tiers("pets", "pet_cameo_big", [
        (1, "pet_cameo_big", "Testigo del pelotazo", "Que tu mascota te vea dar un pelotazo.", R),
    ])  # fmt: skip
    a += _tiers("pets", "pet_cameo_night", [
        (1, "pet_cameo_night", "Fiesta de madrugada",
         "Que tu mascota salga en un mensaje entre las 0:00 y las 6:00.", C, True),
    ])  # fmt: skip
    a += _tiers("pets", "pet_renamed", [
        (1, "pet_name_1", "Bautizo", "Ponle nombre a una mascota.", C),
        (10, "pet_name_10", "Crisis de identidad", "Cambia 10 veces el nombre de tus mascotas.",
         R, True),
    ])  # fmt: skip
    a += _tiers("pets", "pet_named_sanxe", [
        (1, "pet_named_sanxe", "Perro Sanxe, el de verdad",
         "Ponle a una mascota el nombre del presidente.", R, True),
    ])  # fmt: skip
    a += _tiers("pets", "pet_named_beast", [
        (1, "pet_named_beast", "La bestia", "Ponle un 666 en el nombre a una mascota.", C, True),
    ])  # fmt: skip
    a += _tiers("pets", "pet_switches", [
        (10, "pet_switch_10", "Poliamor animal, como Bill Clinton",
         "Cambia 10 veces de mascota activa.", R),
    ])  # fmt: skip
    a += _tiers("pets", "pet_fed_nothing", [
        (1, "pet_fed_rock", "Dar de comer a una piedra",
         "Intenta darle de comer a una mascota que no come.", C, True),
    ])  # fmt: skip
    a += _tiers("pets", "pet_goat_tax", [
        (1, "pet_goat_tax", "La cabra se comió la declaración",
         "Dale el Modelo 100 a la cabra majorera.", R, True),
    ])  # fmt: skip
    a += _tiers("pets", "pet_goat_odd", [
        (10, "pet_goat_10", "Estómago de cabra",
         "Dale a la cabra 10 cosas que no son su favorita.", R),
    ])  # fmt: skip
    a += _tiers("pets", "pet_night", [
        (1, "pet_night", "Insomnio compartido", "Cuida a una mascota entre las 3 y las 6.", C,
         True),
    ])  # fmt: skip
    a += _tiers("pets", "pet_san_anton", [
        (1, "pet_san_anton", "Bendición de San Antón",
         "Cuida a una mascota el 17 de enero, día de San Antón.", C),
    ])  # fmt: skip
    a += _tiers("pets", "pet_christmas", [
        (1, "pet_christmas", "Navidad con pelos", "Cuida a una mascota en Nochebuena o Navidad.",
         C),
    ])  # fmt: skip
    a += _tiers("pets", "pet_halloween", [
        (1, "pet_halloween", "Gato negro en Halloween",
         "Cuida a una mascota en Halloween.", C),
    ])  # fmt: skip
    a += _tiers("pets", "pet_canarias", [
        (1, "pet_canarias", "Día de Canarias con mascota",
         "Cuida a una mascota el 30 de mayo.", C),
    ])  # fmt: skip

    # 🪏 Trabajo ------------------------------------------------------------------------
    a += _tiers("work", "work_shifts", [
        (1, "pala_1", "Coge la pala", "Ficha tu primer turno.", C),
        (10, "pala_10", "Currante, como un autónomo en agosto", "Ficha 10 turnos.", C),
        (100, "pala_100", "Obrero del mes, como en un cartel soviético", "Ficha 100 turnos.", R),
        (500, "pala_500", "Mula de carga de Amazon", "Ficha 500 turnos.", E),
        (1_000, "pala_1k", "Toda una vida con la pala", "Ficha 1.000 turnos.", L),
    ])  # fmt: skip
    a += _tiers("work", "work_promotions", [
        (1, "ascenso_1", "Me han subido el sueldo (en bruto)", "Asciende por primera vez.", C),
    ])  # fmt: skip
    a += _tiers("work", "work_jobs_top", [
        (1, "top_1", "Lo más alto del escalafón", "Llega a lo más alto de un oficio.", E),
        (3, "top_3", "Currículum de Pokédex", "Llega a lo más alto de 3 oficios.", L),
    ])  # fmt: skip
    a.append(Achievement(
        id="paguita", name="Me quedo con la paguita",
        description="Rechaza un ascenso.", category="work", rarity=R,
        conditions=(("work_declined", 1),), story=DECLINE_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_demoted", [
        (1, "degradado", "De vuelta a la pala", "Que te bajen de puesto.", C, True),
    ])  # fmt: skip
    a += _tiers("work", "work_job_changes", [
        (5, "culo_inquieto", "Culo inquieto", "Cambia de oficio 5 veces.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_perfect", [
        (1, "turno_100", "Turno perfecto", "Saca un 100 en un turno.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_good", [
        (20, "empleado_mes", "Empleado del mes", "Haz 20 turnos de 90 o más.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_shifts_day_max", [
        (8, "doble_jornada", "Jornada partida… en dos jornadas", "Ficha 8 turnos en un día.", E),
        (12, "que_es_dormir", "¿Qué es dormir?", "Ficha 12 turnos en un día.", L, True),
    ])  # fmt: skip
    a += _tiers("work", "work_streak_max", [
        (7, "domingo_debiles", "El domingo es para los débiles", "Ficha 7 días seguidos.", R),
        (30, "senor_pala", "Tus hijos te llaman «el señor de la pala»",
         "Ficha 30 días seguidos.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_night", [
        (1, "turno_noche", "Sereno", "Ficha entre las 0:00 y las 6:00.", C),
        (25, "vampiro", "Vampiro laboral", "Ficha 25 turnos de madrugada.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_sunday", [
        (10, "misa_doce", "Misa de doce en la obra", "Ficha 10 turnos en domingo.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_birthday", [
        (1, "cumple_pala", "Feliz cumpleaños, a currar", "Ficha el día de tu cumpleaños.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_christmas", [
        (1, "nochebuena", "Nochebuena en la oficina", "Ficha el 24 o el 25 de diciembre.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_reyes", [
        (1, "reyes_extra", "Los Reyes me trajeron horas extra", "Ficha el 6 de enero.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_mayday", [
        (1, "ironia", "Ironía", "Ficha el 1 de mayo, Día del Trabajador.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_family_zero", [
        (1, "madre_discord", "Tu madre se enteró de que existes por Discord",
         "Deja la familia a 0.", R, True),
        (3, "intervencion", "Intervención familiar", "Deja la familia a 0 tres veces.", E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_happy_shifts", [
        (25, "conciliacion", "Conciliación", "Ficha 25 turnos con la familia en casa feliz.", R),
        (200, "conciliacion_real", "Conciliación de la de verdad",
         "Ficha 200 turnos con la familia en casa feliz.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_gifts", [
        (1, "un_detalle", "Un detalle", "Dale un regalo a tu familia desde `pala`.", C),
        (10, "floristeria", "Cliente fijo de la floristería", "Dale 10 regalos a tu familia.", R),
        (50, "paz_a_plazos", "La paz en casa, a plazos", "Dale 50 regalos a tu familia.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_gift_finde_sur", [
        (1, "todo_incluido", "Pulserita de todo incluido",
         "Llévate a la familia un finde al sur.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_favors", [
        (1, "mama_favor", "Mamá, ¿me haces un favor?", "Pídele un favor a tu familia.", C),
        (25, "familia_primero", "La familia es lo primero (para pedir)",
         "Pídele 25 favores a tu familia.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_favor_bizum", [
        (1, "bizum_mama", "Bizum de mamá", "Pídele a tu madre que te adelante un sueldo.", C),
        (5, "banco_mama", "El banco de mamá, sin comisiones",
         "Pídele el Bizum a tu madre 5 veces.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_favor_excusa", [
        (1, "me_llama", "Me llama mi madre", "Líbrate de un marrón con la excusa familiar.", C),
        (10, "excusas_manual", "Excusas de manual", "Líbrate de 10 marrones con la excusa.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_favor_cubre", [
        (1, "fiebre", "El niño tiene fiebre", "Que tu madre te cubra el saliente de guardia.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_favor_tupper", [
        (10, "lentejas", "Lentejas de los lunes", "Cómete 10 tuppers de tu madre.", R),
    ])  # fmt: skip
    a.append(Achievement(
        id="ochenta_horas", name="80 horas al año, ja",
        description="Pasa del límite legal de horas extra.", category="work", rarity=R,
        conditions=(("work_past_limit", 1),), secret=True, story=OVERTIME_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_zombie", [
        (1, "zombi", "Zombi asalariado", "Ficha con la batería por debajo de 0.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_coffees_day_max", [
        (3, "barraquito_iv", "Barraquito intravenoso", "Tómate 3 cafés en un día.", C),
        (4, "temblores", "Temblores de oficina", "Tómate el cuarto café del día.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_accidents", [
        (1, "parte", "Parte de accidente", "Ten un accidente laboral.", C, True),
        (5, "mutua", "La mutua ya te conoce", "Ten 5 accidentes laborales.", E, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="primera_nomina", name="Mi primera nómina (y mi primer disgusto)",
        description="Cobra tu primera nómina.", category="work", rarity=C,
        conditions=(("work_payslips", 1),), story=FIRST_PAYSLIP_STORY,
    ))  # fmt: skip
    a.append(Achievement(
        id="tramo", name="Me suben de tramo",
        description="Que te retengan un 30 % o más de IRPF en una nómina.",
        category="work", rarity=R, conditions=(("work_irpf_pct_max", 30),),
        story=BRACKET_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_half_salary", [
        (1, "medio_sueldo", "Medio sueldo para Sanxe",
         "Cobra una nómina cuyos impuestos pasen del 80 % del neto.", R),
    ])  # fmt: skip
    a.append(Achievement(
        id="socio_hacienda", name="Socio de Hacienda",
        description="En 7 días, paga en impuestos (nóminas, IGIC y Patrimonio) más que tu neto.",
        category="work", rarity=L, conditions=(("work_partner", 1),), secret=True,
        story=PARTNER_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_max_base", [
        (1, "tope", "Tope de cotización", "Pasa de la base máxima de cotización.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_taxes", [
        (1_000_000, "sanxe_pala", "Perro Sanxe come de tu pala",
         "Paga 1.000.000 Y$ entre IRPF y Seguridad Social trabajando.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("work", "imv_with_salary", [
        (1, "compatible", "Compatibilidad total", "Cobra el IMV con nómina esa semana.", C),
    ])  # fmt: skip
    a += _tiers("work", "imv_floor", [
        (1, "rico_paguita", "Demasiado rico para la paguita",
         "Cobra el IMV mínimo por lo que ganas trabajando.", R),
    ])  # fmt: skip
    a += _tiers("work", "work_black", [
        (1, "negro_1", "En B", "Cobra un turno en negro.", C, True),
    ])  # fmt: skip
    a += _tiers("work", "work_caught_inspeccion", [
        (1, "inspeccion", "Inspección de Trabajo llama dos veces",
         "Que te pille la Inspección.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_fee", [
        (1, "autonomo", "Autónomo y sin vacaciones", "Paga la cuota de autónomos.", R),
    ])  # fmt: skip

    a += _tiers("work", "work_shifts", [
        (5_000, "pala_5k", "Pala de por vida", "Ficha 5.000 turnos.", M),
    ])  # fmt: skip
    a += _tiers("work", "work_promotions", [
        (5, "ascenso_5", "Escalador corporativo, como un cuñado en Moncloa",
         "Asciende 5 veces.", R),
        (15, "ascenso_15", "Techo de cristal roto (y la factura, a Bruselas)",
         "Asciende 15 veces.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_jobs_top", [
        (5, "top_5", "Pluriempleado de élite", "Llega a lo más alto de 5 oficios.", M),
    ])  # fmt: skip
    a += _tiers("work", "work_perfect", [
        (10, "turno_100_10", "Perfeccionista de la pala", "Saca un 100 en 10 turnos.", E),
        (50, "turno_100_50", "Trabajador del mes, del año y del siglo",
         "Saca 50 turnos de 100.", L),
    ])  # fmt: skip
    a += _tiers("work", "work_good", [
        (100, "empleado_100", "Empleado del trimestre", "Haz 100 turnos de 90 o más.", E),
        (500, "empleado_500", "Ejemplo para la plantilla", "Haz 500 turnos de 90 o más.", L),
    ])  # fmt: skip
    a += _tiers("work", "work_night", [
        (100, "vampiro_100", "Turno de noche fijo", "Ficha 100 turnos de madrugada.", L),
    ])  # fmt: skip
    a += _tiers("work", "work_sunday", [
        (50, "domingo_50", "Los domingos, a la obra", "Ficha 50 turnos en domingo.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_streak_max", [
        (100, "pala_100d", "Cien días sin vacaciones", "Ficha 100 días seguidos.", L),
        (365, "pala_365d", "Ni el Estatuto te frena", "Ficha 365 días seguidos.", M, True),
    ])  # fmt: skip
    a += _tiers("work", "work_accidents", [
        (25, "mutua_25", "Siniestralidad laboral", "Ten 25 accidentes laborales.", L, True),
    ])  # fmt: skip
    a += _tiers("work", "work_job_changes", [
        (25, "job_changes_25", "Currículum de Infojobs", "Cambia de oficio 25 veces.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_taxes", [
        (10_000_000, "sanxe_pala_10m", "Pilar del Estado del bienestar",
         "Paga 10.000.000 Y$ entre IRPF y Seguridad Social trabajando.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("work", "work_half_salary", [
        (25, "medio_sueldo_25", "Trabajo para Hacienda",
         "Cobra 25 nóminas cuyos impuestos pasen del 80 % del neto.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_zombie", [
        (25, "zombi_25", "The Walking Pala", "Ficha 25 veces con la batería por debajo de 0.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_fee", [
        (12, "autonomo_12", "Autónomo de verdad", "Paga 12 cuotas de autónomos.", E),
    ])  # fmt: skip

    # Minijuego: herramientas de curro, velocidad y despistes.
    a += _tiers("work", "work_tools_owned", [
        (1, "herramienta_1", "Herramienta propia",
         "Ficha con una herramienta de curro de la tienda.", C),
        (5, "herramienta_5", "Caja de herramientas", "Ficha con 5 herramientas de curro.", R),
        (len(WORK_TOOLS), "herramienta_all", "Ferretería ambulante",
         f"Ficha con las {len(WORK_TOOLS)} herramientas de curro.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_saves", [
        (1, "rectificar", "Donde dije digo, digo Diego",
         "Que una herramienta te perdone un fallo.", C),
        (50, "rectificar_50", "No es mentira, es un cambio de opinión",
         "Que las herramientas te perdonen 50 fallos.", R),
        (500, "rectificar_500", "Manual de rectificación",
         "Que las herramientas te perdonen 500 fallos.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_insured", [
        (1, "seguro_paga", "Para eso pago el seguro",
         "Rompe algo cavando con el seguro de responsabilidad civil.", C, True),
        (25, "prima_sube", "La aseguradora te sube la prima",
         "Rompe 25 cosas con el seguro puesto.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_fifty", [
        (10, "chuleta", "Con chuleta", "Acierta 10 preguntas con una respuesta tachada.", C),
        (500, "chuleta_500", "Opositor con chuleta",
         "Acierta 500 preguntas con una respuesta tachada.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_fast", [
        (1, "rayo", "Rayo de la pala", "Acaba un turno con más de la mitad del tiempo.", R),
        (50, "decreto_ley", "Por la vía del decreto ley",
         "Acaba 50 turnos con más de la mitad del tiempo.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_last_second", [
        (1, "ultimo_segundo", "Como la Renta, el último día",
         "Acaba la última ronda con menos de un segundo en el reloj.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_first_miss", [
        (1, "empezamos_bien", "Empezamos bien", "Falla la primera jugada de un turno.", C, True),
        (25, "lunes_eterno", "Lunes eterno", "Falla la primera jugada en 25 turnos.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_stale_clicks", [
        (25, "doble_clic", "Doble clic de boomer",
         "Pulsa 25 veces un botón de una ronda que ya ha pasado.", C, True),
    ])  # fmt: skip
    a += _tiers("work", "work_tremor_perfect", [
        (1, "pulso_cirujano", "Pulso de cirujano",
         "Saca un 100 con los temblores del cuarto café.", R, True),
    ])  # fmt: skip
    a += _tiers("work", "work_memory_flawless", [
        (1, "memoria_elefante", "Memoria de elefante",
         "Haz perfectas todas las rondas de memoria de un turno.", C),
        (50, "memoria_50", "Ni un «no me consta»",
         "Haz perfectas todas las rondas de memoria en 50 turnos.", R),
    ])  # fmt: skip

    # 👷 Oficios ------------------------------------------------------------------------
    a += _tiers("jobs", "work_pipes", [
        (1, "tuberia", "Tubería rota", "Rompe algo cavando.", C),
        (25, "barrio_sin_agua", "El barrio sin agua", "Rompe 25 cosas cavando.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_slackers", [
        (20, "cinco_miran", "Cinco miran, uno cava", "Pilla a 20 escaqueados.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_overruns", [
        (20, "listo_uco", "Más listo que la UCO", "Encuentra 20 sobrecostes.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_retiree", [
        (1, "jubilado", "Jubilado inspector", "Explícale la obra a un jubilado.", C),
    ])  # fmt: skip
    a += _tiers("jobs", "work_calima", [
        (1, "calima", "Parada por calima", "Para la obra por calima.", C),
    ])  # fmt: skip
    a += _tiers("jobs", "work_top_obra_publica", [
        (1, "constructor", "Constructor de aeropuertos sin aviones",
         "Llega a presidente de una constructora del IBEX (rama 🚄 Obra pública).", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_perfect_orders", [
        (100, "una_cana", "Ponme una caña", "Saca 100 comandas perfectas.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_tip", [
        (1, "propina", "Propina de guiri", "Guárdate una propina en el bolsillo.", C),
    ])  # fmt: skip
    a += _tiers("jobs", "work_dine_dash", [
        (1, "sinpa", "Ni un sinpa", "Persigue a una mesa que se iba sin pagar.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_happy_clients", [
        (30, "cliente_razon", "El cliente siempre tiene razón", "Atiende bien 30 marrones.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_top_hosteleria", [
        (1, "chiringuito", "Chiringuito propio", "Llega a dueño del chiringuito.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_perfect_votes", [
        (1, "voto_1", "Primer botonazo", "Vota una vez lo que diga el partido.", C),
        (50, "disciplina", "Disciplina de voto, como en la Duma de Putin",
         "Vota 50 veces lo que diga el partido.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_dodged", [
        (1, "plasma", "Rueda de prensa por plasma",
         "Esquiva tu primera pregunta en rueda de prensa.", R),
        (20, "mi_libro", "No he venido a hablar de mi libro",
         "Esquiva 20 preguntas en rueda de prensa.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_no_recuerdo", [
        (1, "no_recuerdo_1", "Señoría, no recuerdo", "Sal vivo de tu primera pregunta en comisión.",
         R),
        (20, "no_me_consta", "No me consta (ni a Rajoy)", "Sal vivo de 20 preguntas en comisión.",
         E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_envelope", [
        (1, "sobre", "Sobre en la gabardina", "Acepta un sobre.", R, True),
        (10, "sobres", "Coleccionista de sobres, como un tesorero de Génova",
         "Acepta 10 sobres.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_envelope_refused", [
        (1, "honrado", "Honrado (de momento)", "Rechaza un sobre.", C, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_kickback", [
        (1, "fundacion", "Para la fundación", "Acepta una comisión de obra pública.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_cronyism", [
        (1, "enchufe", "Enchufado en la Diputación", "Coloca a un sobrino.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_falcon", [
        (1, "falcon", "Agenda oficial en el Falcon", "Vete a un concierto en el avión oficial.",
         E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_caught_uco", [
        (1, "imputado", "Imputado, sé fuerte", "Que te pille la UCO.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_pardoned", [
        (1, "indultado", "Indultado, amnistiado y a Waterloo",
         "Que te indulten después de pillarte.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_clinging", [
        (1, "sillon", "Pegado al sillón, como Maduro", "Niégate a dimitir.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_resigned", [
        (1, "dimision", "Dimisión, especie en peligro de extinción", "Dimite. Algo insólito.",
         E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_top_politica_moncloa", [
        (1, "giratoria", "Puerta giratoria",
         "Llega a consejero de una eléctrica (rama 🏛️ Moncloa).", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_communion_bizum", [
        (1, "comunion", "La comunión del sobrino, en diferido",
         "Manda un Bizum en vez de ir a la comunión.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_mom_ghosted", [
        (1, "visto", "Visto a las 23:47", "Déjale el visto a tu madre.", C, True),
    ])  # fmt: skip

    a += _tiers("jobs", "work_pipes", [
        (100, "pipes_100", "Fontanero de guardia", "Rompe 100 cosas cavando.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_slackers", [
        (100, "slackers_100", "Capataz implacable", "Pilla a 100 escaqueados.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_overruns", [
        (100, "overruns_100", "Tribunal de Cuentas", "Encuentra 100 sobrecostes.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_perfect_orders", [
        (500, "orders_500", "Camarero de la Estrella Michelin", "Saca 500 comandas perfectas.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_happy_clients", [
        (150, "clients_150", "El cliente siempre tiene razón (no)",
         "Atiende bien 150 marrones.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_perfect_votes", [
        (250, "votes_250", "Diputado de pulsar el botón",
         "Vota 250 veces lo que diga el partido.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_dodged", [
        (100, "dodged_100", "Escapista de rueda de prensa",
         "Esquiva 100 preguntas en rueda de prensa.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_no_recuerdo", [
        (100, "no_recuerdo_100", "Amnesia selectiva", "Sal vivo de 100 preguntas en comisión.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_envelope", [
        (50, "sobres_50", "Caja B con papeles de Bárcenas", "Acepta 50 sobres.", M, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_kickback", [
        (10, "kickback_10", "Mordida del 3 %", "Acepta 10 comisiones de obra pública.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_cronyism", [
        (10, "enchufe_10", "Agencia de colocación familiar", "Coloca a 10 sobrinos.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_falcon", [
        (10, "falcon_10", "Pasajero frecuente del Falcon",
         "Vete a 10 conciertos en el avión oficial.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_caught_uco", [
        (5, "uco_5", "Cliente habitual de la UCO", "Que te pille la UCO 5 veces.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_tip", [
        (50, "propinas_50", "Bote de propinas", "Guárdate 50 propinas en el bolsillo.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_dine_dash", [
        (10, "sinpa_10", "Velocista de terraza", "Persigue a 10 mesas que se iban sin pagar.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_posters", [
        (1, "cartel_1", "Engrudo en los dedos", "Pega tu primera ruta de carteles perfecta.", C),
        (10, "carteles_10", "Empapelando el barrio", "Pega 10 rutas de carteles perfectas.", C),
        (100, "carteles_100", "Las farolas son del partido",
         "Pega 100 rutas de carteles perfectas.", R),
        (500, "carteles_500", "Brigada del engrudo", "Pega 500 rutas de carteles perfectas.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_clean_digs", [
        (10, "sin_averias", "Ni una avería", "Haz 10 turnos de cavar sin romper nada.", C),
        (100, "zahori", "Zahorí", "Haz 100 turnos de cavar sin romper nada.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_board", [
        (20, "consejero_20", "Consejero de nada",
         "Acierta 20 respuestas en el consejo de administración.", L),
        (200, "consejero_200", "Dietas por asistir",
         "Acierta 200 respuestas en el consejo de administración.", L),
    ])  # fmt: skip

    # 🏥 Sanidad ------------------------------------------------------------------------
    a.append(Achievement(
        id="guardia_1", name="Primera guardia (sin plus)",
        description="Haz tu primera guardia.", category="sanidad", rarity=C,
        conditions=(("work_guards", 1),), story=GUARD_STORY,
    ))  # fmt: skip
    a += _tiers("sanidad", "work_guards", [
        (10, "guardia_10", "De guardia", "Haz 10 guardias.", R),
        (50, "guardia_50", "Vives en el hospital", "Haz 50 guardias.", E),
        (100, "guardia_100", "La cama de guardias es tuya", "Haz 100 guardias.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_zombie_guard", [
        (1, "treinta_seis", "36 horas despierto", "Haz una guardia con la batería en negativo.",
         E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_off_duty_tries", [
        (1, "saliente", "Saliente, pero con ganas", "Intenta fichar estando saliente.", C, True),
        (10, "adicto_hospital", "Adicto al hospital", "Intenta fichar saliente 10 veces.",
         R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_missed_guards", [
        (1, "tutor", "El tutor te busca", "Sáltate las guardias mínimas del MIR.", C, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_stretcher", [
        (50, "celador_pro", "Celador todoterreno", "Haz 50 traslados perfectos.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_rounds", [
        (50, "ronda_seis", "La ronda de las seis", "Haz 50 rondas perfectas en planta.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_triage", [
        (30, "ojo_clinico", "Ojo clínico", "Acierta 30 triajes.", R),
        (200, "manchester", "Triaje de Manchester", "Acierta 200 triajes.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_mir", [
        (50, "numero_uno", "Número uno del MIR", "Acierta 50 preguntas del MIR.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_google", [
        (30, "doctor_google", "Doctor Google", "Gana 30 consultas.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_cancun", [
        (1, "cancun", "Congreso en Cancún", "Acepta el «congreso» del visitador médico.", R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_cancun_refused", [
        (1, "etica", "Ética de manual", "Rechaza el congreso en Cancún.", C, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_caught_expediente", [
        (1, "expediente", "Expediente disciplinario", "Que te pillen el congreso.", E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_strike", [
        (1, "huelguista", "Huelguista", "Súmate a la huelga de residentes.", R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_scab", [
        (1, "esquirol", "Esquirol", "Trabaja durante la huelga.", R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_waitlist", [
        (1, "lista_infinita", "Lista de espera infinita", "«Optimiza» la lista de espera.",
         R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_aggressive", [
        (5, "seguridad_sala", "Seguridad, a la sala 3", "Sobrevive a 5 familiares alterados.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_clap", [
        (1, "aplausos", "Aplausos de las ocho", "Saluda al vecino que aún aplaude.", C, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_shift_swap", [
        (3, "cambio_turno", "Comodín de la supervisora", "Acepta 3 cambios de turno.", R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_top_sanidad", [
        (1, "adjunto", "Del celador a lo más alto",
         "Llega a lo más alto de la sanidad: consejero o dueño de aseguradora.", L),
    ])  # fmt: skip

    a += _tiers("sanidad", "work_stretcher", [
        (250, "stretcher_250", "Celador del año", "Haz 250 traslados perfectos.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_rounds", [
        (250, "rounds_250", "Planta controlada", "Haz 250 rondas perfectas en planta.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_triage", [
        (1_000, "triage_1k", "Ojo de rayos X", "Acierta 1.000 triajes.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_mir", [
        (250, "mir_250", "Plaza en Dermatología", "Acierta 250 preguntas del MIR.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_google", [
        (150, "google_150", "Más listo que el Dr. Google", "Gana 150 consultas.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_aggressive", [
        (25, "aggressive_25", "Chaleco antibalas", "Sobrevive a 25 familiares alterados.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_shift_swap", [
        (25, "swap_25", "El compañero que todos quieren", "Acepta 25 cambios de turno.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_zombie_guard", [
        (10, "zombie_guard_10", "Guardia de 72 horas",
         "Haz 10 guardias con la batería en negativo.", L, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_off_duty_tries", [
        (50, "saliente_50", "Vivo en el hospital", "Intenta fichar saliente 50 veces.", E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_cancun", [
        (5, "cancun_5", "Congresista profesional",
         "Acepta 5 congresos del visitador médico.", E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_waitlist", [
        (10, "waitlist_10", "Lista de espera eterna",
         "«Optimiza» la lista de espera 10 veces.", E, True),
    ])  # fmt: skip

    # 💻 Oficina ------------------------------------------------------------------------
    a += _tiers("oficina", "work_coffee_orders", [
        (50, "becario_cafe", "Becario del café", "Acierta 50 rondas de cafés.", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_bugs", [
        (1, "mi_maquina", "Funciona en mi máquina", "Encuentra tu primer bug.", C),
        (100, "cazabugs", "Cazabugs", "Encuentra 100 bugs.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_reviews", [
        (30, "guardian", "Guardián de producción", "Para 30 cambios peligrosos.", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_meetings", [
        (30, "podia_correo", "Esta reunión podía ser un correo", "Acorta 30 reuniones.", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_pitches", [
        (30, "humo", "Humo de calidad", "Convence a 30 inversores.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_remote", [
        (1, "sofa", "Desde el sofá", "Teletrabaja por primera vez.", C),
        (50, "nomada_salon", "Nómada digital del salón", "Teletrabaja 50 turnos.", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_office", [
        (50, "presentismo", "Presentismo", "Ve a la oficina 50 turnos (que te vean).", R),
    ])  # fmt: skip
    a += _tiers("oficina", "work_always_online", [
        (1, "siempre_linea", "Siempre en línea", "Contesta al jefe a las once de la noche.", C),
        (10, "esclavo_slack", "Esclavo de la mensajería", "Contesta fuera de hora 10 veces.",
         E, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_disconnect", [
        (1, "desconexion", "Desconexión digital", "No contestes fuera de hora.", C),
    ])  # fmt: skip
    a += _tiers("oficina", "work_deploy_friday", [
        (1, "viernes", "Viernes de despliegue", "Despliega un viernes por la tarde.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_useless_meeting", [
        (5, "reunionitis", "Reunionitis", "Ve a 5 reuniones inútiles.", C),
    ])  # fmt: skip
    a += _tiers("oficina", "work_meeting_killed", [
        (1, "mata_reuniones", "Matarreuniones", "Cancela una reunión con un correo.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_linkedin", [
        (1, "agradecido", "Agradecido y emocionado de anunciar", "Publica en LinkedIn.", C, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_paintball", [
        (1, "paintball", "Fuego amigo", "Ve al paintball de empresa.", C, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_ai_ninja", [
        (1, "ninja", "Formador de ninjas", "Enséñale el código al ninja de la IA.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_ireland", [
        (1, "dublin", "Farol irlandés", "Usa la oferta de Dublín para pedir aumento.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_options_max", [
        (1_000_000, "rico_papel", "Rico en papel", "Acumula 1.000.000 Y$ en stock options.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("oficina", "work_exit", [
        (1, "unicornio", "Unicornio de Temu", "Vive un exit.", L, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_bankrupt", [
        (1, "quiebra", "Quiebra, como los casinos de Trump",
         "Que tu startup quiebre con tus opciones dentro.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_top_oficina", [
        (1, "cto", "CTO (hasta que llegue Elon)", "Llega a CTO de startup.", L),
    ])  # fmt: skip

    a += _tiers("oficina", "work_coffee_orders", [
        (250, "coffee_250", "Barista corporativo", "Acierta 250 rondas de cafés.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_bugs", [
        (500, "bugs_500", "Entomólogo del código", "Encuentra 500 bugs.", L),
    ])  # fmt: skip
    a += _tiers("oficina", "work_reviews", [
        (150, "reviews_150", "Portero de producción", "Para 150 cambios peligrosos.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_meetings", [
        (150, "meetings_150", "Esto podía ser un correo", "Acorta 150 reuniones.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_pitches", [
        (100, "pitches_100", "Vendehúmos certificado", "Convence a 100 inversores.", L),
    ])  # fmt: skip
    a += _tiers("oficina", "work_remote", [
        (250, "remote_250", "Nómada del sofá", "Teletrabaja 250 turnos.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_office", [
        (250, "office_250", "Calientasillas", "Ve a la oficina 250 turnos.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_always_online", [
        (50, "online_50", "Esclavo del Slack", "Contesta fuera de hora 50 veces.", L, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_useless_meeting", [
        (50, "useless_50", "Reunionitis crónica", "Ve a 50 reuniones inútiles.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_linkedin", [
        (10, "linkedin_10", "Thought leader", "Publica 10 veces en LinkedIn.", R, True),
    ])  # fmt: skip
    a += _tiers("oficina", "work_disconnect", [
        (25, "disconnect_25", "Derecho a la desconexión",
         "No contestes fuera de hora 25 veces.", E),
    ])  # fmt: skip
    a += _tiers("oficina", "work_exit", [
        (3, "exit_3", "Emprendedor en serie", "Vive 3 exits.", M, True),
    ])  # fmt: skip

    # 🇭🇰 Hong Kong ---------------------------------------------------------------------
    a += _tiers("hongkong", "work_abroad", [
        (1, "expat", "Néih hóu, Hong Kong", "Vete a trabajar a Hong Kong.", R),
        (3, "expat_3", "Ida y vuelta", "Vete a Hong Kong 3 veces.", E),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_hk_shifts", [
        (10, "hk_10", "Expat de manual", "Haz 10 turnos desde Hong Kong.", R),
        (100, "hk_100", "Ya no vuelves", "Haz 100 turnos desde Hong Kong.", E),
        (500, "hk_500", "Más de aquí que de allí", "Haz 500 turnos desde Hong Kong.", L),
    ])  # fmt: skip
    a.append(Achievement(
        id="no_residente", name="183 días",
        description="Deja de ser residente fiscal en España.", category="hongkong", rarity=E,
        conditions=(("work_nonresident", 1),), story=NONRESIDENT_STORY,
    ))  # fmt: skip
    a += _tiers("hongkong", "work_7p", [
        (1, "siete_p", "Exento por el 7.p", "Cobra con la exención por trabajos en el extranjero.",
         R),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_double_tax", [
        (1, "doble_imposicion", "Sin doble imposición",
         "Descuenta lo pagado en Hong Kong de tu IRPF.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_hk_tax", [
        (200_000, "hk_tax", "Contribuyente en Hong Kong",
         "Deja 200.000 Y$ entre salaries tax y MPF.", R),
    ], unit="money")  # fmt: skip
    a += _tiers("hongkong", "work_jetlag", [
        (1, "jetlag", "Jet lag", "Ficha desde Hong Kong cuando en Canarias es de madrugada.", C),
        (25, "reloj_roto", "Reloj biológico roto", "25 turnos con jet lag.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_t8", [
        (1, "t8", "Señal 8", "Quédate en casa con el tifón.", R, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_t8_hero", [
        (1, "t8_heroe", "Ni el tifón te para", "Ve a la oficina con señal 8.", E, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_dimsum", [
        (1, "dimsum", "Dim sum con Robuso", "Desayuna dim sum con Robuso en Hong Kong.",
         R, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_lkf", [
        (1, "lkf", "Lan Kwai Fong", "Sal de afterwork y acaba en un karaoke.", C, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_videocall", [
        (1, "videollamada", "Videollamada a las tres", "Contesta a tu madre de madrugada.",
         C, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_proved_residence", [
        (1, "vivo_aqui", "Vivo aquí, lo juro", "Demuestra a Hacienda que vives fuera.", R, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_caught_hacienda", [
        (1, "residencia_ficticia", "Residencia fiscal ficticia",
         "Que Hacienda te regularice por vivir «fuera».", E, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "imv_abroad", [
        (1, "paguita_hk", "Paguita desde Hong Kong", "Intenta cobrar el IMV viviendo fuera.",
         C, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_return", [
        (1, "vuelta", "Vuelta a casa", "Vuelve de Hong Kong.", C),
    ])  # fmt: skip
    # `hongkong`: mirar la hora de allí (ver `hong_kong_clock_stats`).
    a += _tiers("hongkong", "hk_clock", [
        (1, "hkclock_1", "¿Qué hora es allí?", "Mira la hora de Hong Kong.", C),
        (25, "hkclock_25", "Reloj de Robuso", "Mira la hora de Hong Kong 25 veces.", R),
        (100, "hkclock_100", "Doble zona horaria", "Mira la hora de Hong Kong 100 veces.", E),
        (500, "hkclock_500", "Viaje oficial en Falcon",
         "Mira la hora de Hong Kong 500 veces. Con tanto interés, ya habrías ido en Falcon.", L),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_tomorrow", [
        (1, "hk_manana", "Viajero del futuro", "Mira la hora cuando en Hong Kong ya es mañana.",
         C),
        (50, "hk_diferido", "Vives en diferido", "50 veces mirando el mañana de Hong Kong.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_sleeping", [
        (1, "hk_no_despiertes", "No despiertes a Robuso",
         "Mira la hora cuando en Hong Kong son entre las 2:00 y las 6:00.", C),
        (25, "hk_insomne", "Insomne transoceánico",
         "25 veces mirando la hora de madrugada en Hong Kong.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_lunch", [
        (1, "hk_cha_chaan", "Hora del cha chaan teng",
         "Mira la hora cuando Robuso está almorzando (12:00–14:00 allí).", C),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_tour", [
        (1, "hk_gira", "Gira asiática",
         "Mira la hora de Hong Kong de madrugada en Canarias, como en una gira oficial por "
         "China.", R, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_new_year", [
        (1, "hk_ano_nuevo", "Año nuevo por adelantado",
         "Mira la hora en el primer minuto del año en Hong Kong, horas antes que en Canarias.",
         E, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="beckham", name="Ley Beckham",
        description="Vuelve tras 5 «años» fuera y tributa al 24 %.", category="hongkong",
        rarity=L, conditions=(("work_beckham", 1),), secret=True, story=BECKHAM_STORY,
    ))  # fmt: skip
    a += _tiers("hongkong", "work_abroad_days_max", [
        (7, "semana_fuera", "Una semana fuera", "Pasa 7 días seguidos en Hong Kong.", R),
        (35, "cinco_anos", "Cinco «años» fuera", "Pasa 35 días seguidos en Hong Kong.", L),
    ])  # fmt: skip

    a += _tiers("hongkong", "work_abroad", [
        (10, "expat_10", "Más viajes que Robuso", "Vete a Hong Kong 10 veces.", L),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_hk_shifts", [
        (2_000, "hk_2000", "Hongkonés de adopción", "Haz 2.000 turnos desde Hong Kong.", M),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_jetlag", [
        (100, "jetlag_100", "Reloj biológico en huelga", "Haz 100 turnos con jet lag.", E),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_hk_tax", [
        (2_000_000, "hk_tax_2m", "Contribuyente de Hong Kong",
         "Deja 2.000.000 Y$ en impuestos de Hong Kong.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("hongkong", "hk_clock_lunch", [
        (25, "hk_lunch_25", "Almorzando con Robuso",
         "Mira la hora 25 veces cuando Robuso almuerza.", R),
    ])  # fmt: skip
    a += _tiers("hongkong", "hk_clock_tour", [
        (10, "hk_tour_10", "Gira asiática del presidente",
         "Mira la hora de Hong Kong de madrugada en Canarias 10 veces.", E, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_dimsum", [
        (10, "dimsum_10", "Har gow para desayunar",
         "Desayuna dim sum con Robuso 10 veces.", E, True),
    ])  # fmt: skip
    a += _tiers("hongkong", "work_t8", [
        (5, "t8_5", "Temporada de tifones", "Quédate en casa con el tifón 5 veces.", E, True),
    ])  # fmt: skip

    # 🪏 Trabajo (más) --------------------------------------------------------------------
    a += _tiers("jobs", "work_jobs_tried", [
        (3, "probador", "Probando oficios", "Trabaja en 3 oficios distintos.", R),
        (5, "curriculum_infinito", "Currículum infinito", "Trabaja en 5 oficios distintos.",
         E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_zero", [
        (1, "cero", "¿Has venido a trabajar?", "Saca un 0 en un turno.", C, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_black_total", [
        (10, "sumergida", "Economía sumergida", "Cobra 10 turnos en B.", R, True),
    ])  # fmt: skip

    # ➕ Segunda tanda: más escalones y logros cruzados -------------------------------
    # Todos salen de estadísticas que ya se suman. Los escalones nuevos van aquí,
    # detrás de los de siempre, y heredan la unidad (minutos, Y$) del primero.

    def more(category: str, stat: str, rows: list[tuple]) -> list[Achievement]:
        unit = next(x.unit for x in a if x.stat == stat and len(x.conditions) == 1)
        return _tiers(category, stat, rows, unit=unit)

    def combo(
        achievement_id: str,
        name: str,
        description: str,
        category: str,
        rarity: Rarity,
        conditions: tuple[tuple[str, int], ...],
        *,
        secret: bool = False,
    ) -> Achievement:
        return Achievement(
            id=achievement_id,
            name=name,
            description=description,
            category=category,
            rarity=rarity,
            conditions=conditions,
            secret=secret,
        )

    # 💬 Chat, ✍️ Estilo y 🧵 Conversación
    a += more("chat", "msg_replies", [
        (25_000, "reply_25k", "Réplica y contrarréplica", "Responde a 25.000 mensajes.", L),
    ])  # fmt: skip
    a += more("chat", "msg_mentions", [
        (5_000, "ping_5k", "Acoso y derribo", "Menciona a alguien en 5.000 mensajes.", E),
    ])  # fmt: skip
    a += more("chat", "msg_attachments", [
        (5_000, "pic_5k", "Filtración masiva", "Sube 5.000 archivos o imágenes.", L),
    ])  # fmt: skip
    a += more("chat", "msg_everyone", [
        (50, "everyone_50", "Cadena de televisión pública",
         "Menciona a @everyone o @here 50 veces. Nadie te ha pedido el informativo.", E, True),
    ])  # fmt: skip
    a += more("chat", "msg_mass_ping", [
        (50, "mass_ping_50", "Manifestación convocada por WhatsApp",
         "Menciona a 5 personas o más en un mismo mensaje 50 veces.", L, True),
    ])  # fmt: skip
    a += more("style", "msg_long", [
        (1_000, "long_1k", "Tesis doctoral (con autoría dudosa)",
         "Escribe 1.000 mensajes de 600 caracteres o más.", L),
    ])  # fmt: skip
    a += more("style", "msg_short", [
        (10_000, "short_10k", "Telegrama", "Manda 10.000 mensajes de 3 caracteres o menos.", E),
    ])  # fmt: skip
    a += more("style", "msg_exclaim", [
        (2_500, "exclaim_2500", "Mitin de campaña", "Escribe «!!!» en 2.500 mensajes.", E),
    ])  # fmt: skip
    a += more("style", "msg_spoiler", [
        (1_000, "spoiler_1k", "Ley de Secretos Oficiales de 1968",
         "Esconde 1.000 mensajes tras un spoiler.", E),
    ])  # fmt: skip
    a += more("style", "msg_code", [
        (1_000, "code_1k", "Programador del SEPE",
         "Escribe código (`así`) en 1.000 mensajes. En COBOL, a ser posible.", E),
    ])  # fmt: skip
    a += more("style", "msg_emoji_heavy", [
        (1_000, "emoji_heavy_1k", "Tía en el grupo de la familia",
         "Mete 5 emojis o más en 1.000 mensajes.", E),
    ])  # fmt: skip
    a += more("style", "msg_only_emoji", [
        (5_000, "only_emoji_5k", "Piedra de Rosetta",
         "Manda 5.000 mensajes hechos solo de emojis.", E),
    ])  # fmt: skip
    a += more("style", "msg_stretch", [
        (1_000, "stretch_1k", "Holaaaaaaaaaa",
         "Repite una letra 6 veces seguidas en 1.000 mensajes.", E),
    ])  # fmt: skip
    a += more("convo", "msg_monologue_max", [
        (100, "monologue_100", "Sesión de investidura fallida",
         "Escribe 100 mensajes seguidos sin que nadie te conteste.", M, True),
    ])  # fmt: skip
    a += more("convo", "msg_day_max", [
        (2_000, "day_2k", "Filibusterismo parlamentario",
         "Escribe 2.000 mensajes en un solo día.", M, True),
    ])  # fmt: skip
    a += more("convo", "msg_first_of_day", [
        (365, "first_365", "El gallo del corral",
         "Sé el primero en escribir del día 365 veces.", M),
    ])  # fmt: skip
    a += more("convo", "msg_echo", [
        (1_000, "echo_1k", "Argumentario de Ferraz",
         "Repite tal cual lo que acaba de escribir otro 1.000 veces.", L),
    ])  # fmt: skip
    a += more("convo", "msg_necro", [
        (50, "necro_50", "Exhumación",
         "Escribe 50 veces en un canal que llevaba 24 h muerto.", E),
    ])  # fmt: skip
    a += more("convo", "msg_edits", [
        (10_000, "edits_10k", "Corrección de errores del BOE", "Edita 10.000 mensajes.", L),
    ])  # fmt: skip

    # 😂 Risas, 🤡 Hacer reír, 🗣️ Lengua
    a += more("laughs", "laugh_en", [
        (5_000, "laugh_en_5k", "Spanglish de Miami", "Ríete en inglés 5.000 veces.", L),
    ])  # fmt: skip
    a += more("laughs", "msg_xd", [
        (50_000, "xd_50k", "xDDDDDDDDD", "Escribe xd en 50.000 mensajes.", M, True),
    ])  # fmt: skip
    a += more("laughs", "laugh_emoji", [
        (5_000, "laugh_emoji_5k", "😂😂😂😂😂", "Ríete con emojis 5.000 veces.", E),
    ])  # fmt: skip
    a += more("laughs", "laugh_skull", [
        (2_000, "laugh_skull_2k", "Cementerio de memes",
         "Mándale un 💀 a un chiste 2.000 veces.", E),
    ])  # fmt: skip
    a += more("laughs", "laugh_smash", [
        (1_000, "laugh_smash_1k", "Teclado sin garantía",
         "Aporrea el teclado (ajsjsjs) 1.000 veces.", E),
    ])  # fmt: skip
    a += more("laughs", "laugh_phrase", [
        (1_000, "laugh_phrase_1k", "Me meo (de verdad)",
         "Ríete con palabras (me meo, me parto, lloro) 1.000 veces.", E),
    ])  # fmt: skip
    a += more("laughs", "msg_laugh_caps", [
        (1_000, "laugh_caps_1k", "JAJAJAJA A GRITOS",
         "Ríete EN MAYÚSCULAS en 1.000 mensajes.", E),
    ])  # fmt: skip
    a += more("laughs", "msg_laugh_dry", [
        (1_000, "laugh_dry_1k", "Ja. Ja. Ja.",
         "Responde «ja.» 1.000 veces.", L),
    ])  # fmt: skip
    a += more("laughs", "laugh_night", [
        (250, "laugh_night_250", "Risa de madrugada en el Congreso",
         "Ríete en 250 mensajes entre las 2:00 y las 6:00.", E),
    ])  # fmt: skip
    a += more("laughs", "laugh_intl", [
        (500, "laugh_intl_500", "Cumbre de la OTAN",
         "Ríete en otro idioma (kkkk, mdr, wwww, ㅋㅋ…) 500 veces.", L),
    ])  # fmt: skip
    a += more("funny", "laugh_replies", [
        (5_000, "laugh_replies_5k", "Público de plató",
         "Ríete respondiendo a 5.000 mensajes.", E),
    ])  # fmt: skip
    a += more("funny", "laughs_caused", [
        (5_000, "laughs_caused_5k", "Club de la Comedia",
         "Que se rían respondiendo a tus mensajes 5.000 veces.", M),
    ])  # fmt: skip
    a += more("funny", "laugh_at_bot", [
        (250, "laugh_bot_250", "Te ríes de una máquina",
         "Ríete respondiendo al bot 250 veces. Él no se ríe de ti. Todavía.", E),
    ])  # fmt: skip
    a += more("funny", "laugh_chain_max", [
        (12, "laugh_chain_12", "Ataque de risa colectivo",
         "Que 12 personas se rían seguidas en un canal.", L, True),
    ])  # fmt: skip
    a += more("funny", "laugh_reacts_given", [
        (5_000, "laugh_given_5k", "Risas enlatadas",
         "Reacciona con 😂, 🤣 o 💀 a 5.000 mensajes.", E),
    ])  # fmt: skip
    a += more("funny", "laugh_reacts_received", [
        (10_000, "laugh_received_10k", "Humorista de Estado",
         "Recibe 10.000 reacciones de risa.", M),
    ])  # fmt: skip
    a += more("funny", "laugh_reacts_on_message_max", [
        (15, "laugh_message_15", "Meme de Estado",
         "Que 15 personas se rían (con reacción) del mismo mensaje.", L),
    ])  # fmt: skip
    a += more("lengua", "msg_canario", [
        (5_000, "canario_5k", "Más canario que un barraquito",
         "Habla en canario en 5.000 mensajes, mi niño.", M),
    ])  # fmt: skip
    a += more("lengua", "msg_swear", [
        (25_000, "swear_25k", "Camionero de la GC-1",
         "Suelta una palabrota en 25.000 mensajes.", L),
    ])  # fmt: skip
    a += more("lengua", "msg_thanks", [
        (10_000, "thanks_10k", "Educación de colegio de monjas",
         "Da las gracias en 10.000 mensajes.", L),
    ])  # fmt: skip
    a += more("lengua", "msg_sorry", [
        (250, "sorry_250", "Lo siento mucho, me he equivocado, no volverá a ocurrir",
         "Pide perdón en 250 mensajes.", E),
    ])  # fmt: skip
    a += more("lengua", "msg_good_night", [
        (1_000, "good_night_1k", "Ya me voy, que mañana madrugo",
         "Da las buenas noches 1.000 veces.", E),
    ])  # fmt: skip
    a += more("lengua", "msg_hacienda", [
        (500, "hacienda_500", "Hacienda somos todos (menos tú)",
         "Nombra a Hacienda 500 veces.", E),
    ])  # fmt: skip
    a += more("lengua", "msg_bizum_ask", [
        (250, "bizum_ask_250", "Sablista",
         "Pide un Bizum por el chat 250 veces.", E),
    ])  # fmt: skip

    # 🗓️ Horarios y ❤️ Social
    a += more("time", "msg_night", [
        (10_000, "night_10k", "Insomnio en la Moncloa",
         "Escribe 10.000 mensajes entre las 2:00 y las 6:00.", L),
    ])  # fmt: skip
    a += more("time", "msg_morning", [
        (10_000, "morning_10k", "Panadero digital",
         "Escribe 10.000 mensajes entre las 6:00 y las 8:00.", L),
    ])  # fmt: skip
    a += more("time", "msg_siesta", [
        (10_000, "siesta_10k", "La siesta es para los débiles",
         "Escribe 10.000 mensajes entre las 15:00 y las 17:00.", E),
    ])  # fmt: skip
    a += more("time", "msg_office", [
        (50_000, "office_50k", "Funcionario en su salsa",
         "Escribe 50.000 mensajes entre semana de 9:00 a 14:00. ¿Quién atiende la ventanilla?", L),
    ])  # fmt: skip
    a += more("time", "msg_weekend", [
        (10_000, "weekend_10k", "Sin vida los findes",
         "Escribe 10.000 mensajes en fin de semana.", E),
    ])  # fmt: skip
    a += more("social", "greetings_sent", [
        (100, "greetings_100", "Felicitador de la Casa Real",
         "Felicita 100 cumpleaños.", M),
    ])  # fmt: skip
    a += more("social", "welcomes_given", [
        (100, "welcomes_100", "Delegación del Gobierno de bienvenida",
         "Da la bienvenida a 100 recién llegados.", M),
    ])  # fmt: skip
    a += more("social", "msg_bot_call", [
        (10_000, "bot_call_10k", "Amigo íntimo del bot",
         "Menciona al bot o di su nombre 10.000 veces.", L),
    ])  # fmt: skip
    a += more("social", "msg_sanxe", [
        (1_000, "sanxe_1k", "Monotema",
         "Nombra a Perro Sanxe 1.000 veces. Seguro que te pagan por ello.", E),
    ])  # fmt: skip

    # 🎙️ Llamada, 🎚️ Micro, 🚪 Entradas, 🎵 Música, 🖼️ Imágenes, 📝 Lista
    a += more("voice", "voice_night", [
        (12_000, "voice_night_200h", "Tertulia de madrugada en la radio",
         "Pasa 200 horas en llamada entre las 2:00 y las 6:00.", L),
    ])  # fmt: skip
    a += more("voice", "voice_duo", [
        (12_000, "duo_200h", "Matrimonio por la llamada",
         "Pasa 200 horas en llamada con una sola persona.", L),
    ])  # fmt: skip
    a += more("voice", "voice_morning", [
        (3_000, "voice_morning_3k", "Tertuliano matinal",
         "Pasa 50 horas en llamada entre las 6:00 y las 8:00.", E),
    ])  # fmt: skip
    a += more("voice", "voice_siesta", [
        (6_000, "voice_siesta_6k", "Siesta parlamentaria",
         "Pasa 100 horas en llamada entre las 15:00 y las 17:00.", E),
    ])  # fmt: skip
    a += more("voice", "voice_weekend", [
        (30_000, "voice_weekend_30k", "El finde es para la llamada",
         "Pasa 500 horas en llamada en fin de semana.", L),
    ])  # fmt: skip
    a += more("voice_mic", "voice_stream", [
        (12_000, "stream_200h", "Retransmisión de La 1",
         "Comparte pantalla durante 200 horas.", L),
    ])  # fmt: skip
    a += more("voice_mic", "voice_video", [
        (3_000, "video_3k", "Presentador del telediario",
         "Pon la cámara durante 50 horas.", E),
    ])  # fmt: skip
    a += more("voice_mic", "voice_muted", [
        (6_000, "muted_6k", "Rueda de prensa sin preguntas",
         "Pasa 100 horas en llamada con el micro silenciado.", E),
    ])  # fmt: skip
    a += more("voice_mic", "voice_deaf", [
        (6_000, "deaf_6k", "Oídos sordos a la oposición",
         "Pasa 100 horas en llamada con el sonido quitado.", E, True),
    ])  # fmt: skip
    a += more("voice_mic", "voice_afk", [
        (30_000, "afk_30k", "Vuelva usted mañana",
         "Pasa 500 horas en el canal AFK sin dar palo al agua.", L),
    ])  # fmt: skip
    a += more("voice_mic", "voice_stream_crowd", [
        (600, "stream_crowd_600", "Audiencia de Eurovisión",
         "Comparte pantalla 10 horas con 4 personas o más mirando.", E),
    ])  # fmt: skip
    a += more("voice_mic", "voice_multitask", [
        (300, "multitask_300", "Ministro de varias carteras",
         "Pon cámara y comparte pantalla a la vez 5 horas.", E),
    ])  # fmt: skip
    a += more("voice_mic", "voice_stream_starts", [
        (1_000, "stream_starts_1k", "Zapeador", "Empieza a compartir pantalla 1.000 veces.", E),
    ])  # fmt: skip
    a += more("voice_mic", "voice_mute_streak_max", [
        (1_440, "mute_streak_1440", "Monje de Montserrat",
         "Aguanta 24 horas seguidas silenciado en llamada.", L, True),
    ])  # fmt: skip
    a += more("voice_moves", "voice_joins", [
        (5_000, "joins_5k", "Puerta del Sol en Nochevieja",
         "Entra 5.000 veces a un canal de voz.", L),
    ])  # fmt: skip
    a += more("voice_moves", "voice_hops", [
        (2_000, "hops_2k", "Tránsfuga profesional",
         "Cambia de canal de voz 2.000 veces.", L),
    ])  # fmt: skip
    a += more("voice_moves", "voice_ghost", [
        (100, "ghost_100", "Fantasma de la Moncloa",
         "Entra en un canal de voz y vete en menos de 15 s, 100 veces.", E, True),
    ])  # fmt: skip
    a += more("voice_moves", "entrance_saved", [
        (50, "entrance_saved_50", "Votante indeciso",
         "Guarda tu sonido de entrada 50 veces. Decídete ya.", E),
    ])  # fmt: skip
    a += more("voice_moves", "entrance_played", [
        (5_000, "entrance_played_5k", "Himno sin letra",
         "Que suene tu entrada 5.000 veces.", L),
    ])  # fmt: skip
    a += more("music", "music_queued", [
        (2_000, "queued_2k", "Los 40 Principales",
         "Pon 2.000 canciones con `poner`.", L),
    ])  # fmt: skip
    a += more("music", "voice_music", [
        (12_000, "voice_music_200h", "Verbena de pueblo",
         "Pasa 200 horas en llamada con el bot poniendo música.", L),
    ])  # fmt: skip
    a += more("music", "music_skips", [
        (1_000, "skips_1k", "Siguiente, por favor",
         "Salta 1.000 canciones.", E),
    ])  # fmt: skip
    a += more("music", "music_stops", [
        (100, "stops_100", "El gran apagón",
         "Para la música con `parar` 100 veces.", E),
    ])  # fmt: skip
    a += more("music", "music_removes", [
        (100, "removes_100", "Lápiz rojo del censor", "Quita 100 canciones de la cola.", E),
    ])  # fmt: skip
    a += more("music", "music_clears", [
        (25, "clears_25", "Borrón y cuenta nueva",
         "Vacía la cola con `vaciar` 25 veces.", R),
    ])  # fmt: skip
    a += more("music", "music_queue_max", [
        (25, "queue_25", "Lista de espera de la Seguridad Social",
         "Deja la cola con 25 canciones o más.", E),
    ])  # fmt: skip
    a += more("memes", "img_made", [
        (2_000, "img_2k", "Fábrica de bulos",
         "Genera 2.000 imágenes con efectos.", L),
    ])  # fmt: skip
    a += more("memes", "img_magik", [
        (1_000, "magik_1k", "Brujería de Estado",
         "Deforma 1.000 imágenes con `magik`.", E),
    ])  # fmt: skip
    a += more("memes", "img_video", [
        (1_000, "img_video_1k", "Productora de NO-DO",
         "Genera 1.000 vídeos o GIFs con efectos.", E),
    ])  # fmt: skip
    a += more("memes", "img_on_others", [
        (1_000, "img_others_1k", "Dossier de la máquina del fango",
         "Aplica un efecto al avatar de otro 1.000 veces.", E),
    ])  # fmt: skip
    a += more("memes", "img_self", [
        (100, "img_self_100", "Selfie presidencial",
         "Aplica un efecto a tu propio avatar 100 veces.", R),
    ])  # fmt: skip
    a += more("memes", "babel_phrases", [
        (1_000, "babel_1k", "Pinganillo del Senado",
         "Pasa 1.000 frases por `babel`.", L),
    ])  # fmt: skip
    a += more("memes", "babel_renames", [
        (250, "babel_renames_250", "Nombre en las cuatro lenguas cooficiales",
         "Cámbiale el apodo a alguien con `babel` 250 veces.", E),
    ])  # fmt: skip
    a += more("todo", "todo_done_batch_max", [
        (50, "todo_batch_50", "Ley ómnibus", "Tacha 50 tareas de la lista de golpe.", E, True),
    ])  # fmt: skip

    # 🎰 Casino
    a += more("casino", "casino_all_in_wins", [
        (50, "all_in_wins_50", "Manual de supervivencia",
         "Gana 50 all-in.", L),
    ])  # fmt: skip
    a += more("casino", "casino_broke", [
        (200, "broke_200", "Rescate bancario",
         "Quédate a cero en el casino 200 veces.", L),
    ])  # fmt: skip
    a += more("roulette", "roulette_color_wins", [
        (5_000, "color_5k", "Rojo o negro, como el CIS",
         "Gana 5.000 apuestas a color.", L),
    ])  # fmt: skip
    a += more("roulette", "roulette_dozen_wins", [
        (2_500, "dozen_2500", "La docena del fraile",
         "Gana 2.500 apuestas a docena o columna.", L),
    ])  # fmt: skip
    a += more("roulette", "roulette_half_wins", [
        (5_000, "half_5k", "Mitad y mitad, como el Congreso",
         "Gana 5.000 apuestas a par, impar, 1-18 o 19-36.", L),
    ])  # fmt: skip
    a += more("roulette", "roulette_zero_sweep", [
        (1_000, "zero_sweep_1k", "La casa siempre gana",
         "Pierde todo 1.000 veces porque sale el 0 o el 00.", L),
    ])  # fmt: skip
    a += more("blackjack", "bj_splits", [
        (100, "bj_splits_100", "Federalismo asimétrico",
         "Separa 100 parejas.", E),
    ])  # fmt: skip
    a += more("blackjack", "bj_double_wins", [
        (250, "bj_double_250", "Doble o nada (y siempre doble)",
         "Gana 250 manos después de doblar.", L),
    ])  # fmt: skip
    a += more("blackjack", "bj_pushes", [
        (1_000, "bj_pushes_1k", "Empate técnico en las encuestas", "Empata 1.000 manos.", L),
    ])  # fmt: skip
    a += more("blackjack", "bj_dealer_busts", [
        (2_500, "bj_dealer_busts_2500", "La banca se pasa de frenada",
         "Gana 2.500 manos porque la banca se pasa.", L),
    ])  # fmt: skip
    a += more("blackjack", "bj_21_multi", [
        (250, "bj_21_multi_250", "Veintiuno a plazos",
         "Suma 21 con tres cartas o más 250 veces.", E),
    ])  # fmt: skip
    a += more("crash", "crash_party_max", [
        (8, "crash_party_8", "Viaje oficial en el Falcon",
         "Juega una ronda de crash con 8 personas.", E),
    ])  # fmt: skip
    a += more("crash", "crash_cash_low", [
        (2_500, "crash_low_2500", "Inversor de letras del Tesoro",
         "Retírate en 1,10x o menos 2.500 veces.", E),
    ])  # fmt: skip
    a += more("crash", "crash_last_out", [
        (10, "crash_last_10", "El último que apague la luz",
         "Sé el último en retirarse, con más gente aún dentro, 10 veces.", E),
    ])  # fmt: skip
    a += more("crash", "crash_missed_moon", [
        (10, "crash_missed_10", "Me bajé antes de la Luna",
         "Retírate antes del 2x en 10 rondas que pasan de 100x.", E, True),
    ])  # fmt: skip
    a += more("crash", "crash_greedy", [
        (25, "crash_greedy_25", "Codicia de consejo de administración",
         "Pierde en 25 rondas que llegaron a 10x.", E, True),
    ])  # fmt: skip
    a += more("crash", "crash_auto", [
        (5_000, "crash_auto_5k", "Piloto automático en el Congreso",
         "Cobra 5.000 veces con el auto-retiro.", L),
    ])  # fmt: skip
    a += more("crash", "crash_close", [
        (100, "crash_close_100", "Por los pelos, otra vez",
         "Retírate a menos de un 5 % de la explosión 100 veces.", L),
    ])  # fmt: skip

    # 🛍️ Tienda, 🏦 Banco, 🎟️ Loterías y 🏛️ Hacienda
    a += more("shop", "shop_collection_max", [
        (30, "collect_30", "Trastero lleno", "Ten 30 objetos distintos.", E),
        (60, "collect_60", "Síndrome de Diógenes", "Ten 60 objetos distintos.", L),
        (100, "collect_100", "El colmado en casa", "Ten 100 objetos distintos.", M),
    ])  # fmt: skip
    a += more("shop", "shop_renewals", [
        (100, "renewals_100", "Suscriptor de por vida",
         "Renueva alquileres de rol 100 veces.", L),
    ])  # fmt: skip
    a += more("shop", "shop_sale_buys", [
        (500, "sale_500", "Cazador del Black Friday", "Compra 500 cosas rebajadas.", L),
    ])  # fmt: skip
    a += more("bizum", "bizum_received_count", [
        (500, "bizum_recv_500", "Mantenido",
         "Recibe 500 Bizums.", L),
    ])  # fmt: skip
    a += more("bizum", "bizum_min", [
        (250, "bizum_min_250", "Bizum de céntimos",
         "Manda 250 Bizums de 5 Y$, el mínimo.", E, True),
    ])  # fmt: skip
    a += more("lottery", "lottery_reintegros", [
        (5_000, "reintegros_5k", "Me ha tocado (lo mismo que jugué)", "Cobra 5.000 reintegros.", L),
    ])  # fmt: skip
    a += more("lottery", "lottery_nino", [
        (100, "nino_100", "Carta a los Reyes Magos",
         "Compra 100 décimos del Niño.", E),
    ])  # fmt: skip
    a += more("economy", "tax_refunds", [
        (500, "refunds_500", "Devolución por domiciliación",
         "Recupera IRPF del casino 500 veces perdiendo el mismo día.", L),
    ])  # fmt: skip
    a += more("economy", "renta_filed", [
        (104, "renta_104", "Dos años sin faltar a la cita",
         "Presenta la renta 104 semanas. Perro Sanxe te manda una felicitación de Navidad.", M),
    ])  # fmt: skip
    a += more("economy", "donated", [
        (10_000_000, "donated_10m", "Mecenas de chiringuitos", "Dona 10.000.000 Y$ a ONGs.", M),
    ])  # fmt: skip
    a += more("economy", "imv_claims", [
        (730, "imv_730", "Pensión vitalicia de expresidente", "Cobra el IMV 730 veces.", M, True),
    ])  # fmt: skip

    # 🪏 Trabajo y oficios
    a += more("work", "work_promotions", [
        (50, "promotions_50", "Ascenso por enchufe", "Consigue 50 ascensos.", L),
    ])  # fmt: skip
    a += more("work", "work_job_changes", [
        (100, "job_changes_100", "Vida laboral de 40 páginas",
         "Cambia de oficio 100 veces.", L),
    ])  # fmt: skip
    a += more("work", "work_night", [
        (500, "work_night_500", "Turno de noche perpetuo",
         "Ficha 500 veces entre las 0:00 y las 6:00.", M),
    ])  # fmt: skip
    a += more("jobs", "work_tip", [
        (500, "tip_500", "Propina sin declarar",
         "Guárdate 500 propinas en el bolsillo.", E),
    ])  # fmt: skip
    a += more("jobs", "work_happy_clients", [
        (1_000, "happy_1k", "Trato de cinco estrellas",
         "Atiende bien 1.000 marrones.", L),
    ])  # fmt: skip
    a += more("jobs", "work_slackers", [
        (500, "slackers_500", "Capataz de la obra", "Pilla a 500 escaqueados.", L),
    ])  # fmt: skip
    a += more("jobs", "work_dodged", [
        (500, "dodged_500", "Esquiva preguntas como en el Senado",
         "Esquiva 500 preguntas en rueda de prensa.", L),
    ])  # fmt: skip
    a += more("jobs", "work_perfect_votes", [
        (1_000, "votes_1k", "Rodillo parlamentario",
         "Vota 1.000 veces lo que diga el partido.", L),
    ])  # fmt: skip
    a += more("sanidad", "work_guards", [
        (500, "guards_500", "Guardia eterna", "Haz 500 guardias.", M),
    ])  # fmt: skip
    a += more("sanidad", "work_stretcher", [
        (1_000, "stretcher_1k", "Celador de oro",
         "Haz 1.000 traslados perfectos.", L),
    ])  # fmt: skip
    a += more("sanidad", "work_rounds", [
        (1_000, "rounds_1k", "Pase de planta infinito",
         "Haz 1.000 rondas perfectas en planta.", L),
    ])  # fmt: skip
    a += more("sanidad", "work_google", [
        (500, "google_500", "Doctor Google, colegiado",
         "Gana 500 consultas.", L),
    ])  # fmt: skip
    a += more("sanidad", "work_aggressive", [
        (100, "aggressive_100", "Chaleco antibalas en urgencias",
         "Sobrevive a 100 familiares alterados.", L),
    ])  # fmt: skip
    a += more("sanidad", "work_shift_swap", [
        (100, "swap_100", "Cuadrante imposible",
         "Acepta 100 cambios de turno.", L),
    ])  # fmt: skip
    a += more("sanidad", "work_waitlist", [
        (50, "waitlist_50", "Lista de espera quirúrgica",
         "«Optimiza» la lista de espera 50 veces.", L, True),
    ])  # fmt: skip
    a += more("oficina", "work_coffee_orders", [
        (1_000, "coffee_1k", "Becario eterno",
         "Acierta 1.000 rondas de cafés.", L),
    ])  # fmt: skip
    a += more("oficina", "work_reviews", [
        (500, "reviews_500", "Revisor implacable",
         "Para 500 cambios peligrosos.", L),
    ])  # fmt: skip
    a += more("oficina", "work_meetings", [
        (500, "meetings_500", "Podría haber sido un correo",
         "Acorta 500 reuniones.", L),
    ])  # fmt: skip
    a += more("oficina", "work_remote", [
        (1_000, "remote_1k", "Teletrabajo desde Fuerteventura",
         "Teletrabaja 1.000 turnos.", L),
    ])  # fmt: skip
    a += more("oficina", "work_office", [
        (1_000, "office_shifts_1k", "Mueble de la oficina",
         "Ve a la oficina 1.000 turnos (que te vean).", L),
    ])  # fmt: skip
    a += more("oficina", "work_useless_meeting", [
        (250, "useless_250", "Comisión de investigación",
         "Ve a 250 reuniones inútiles.", L),
    ])  # fmt: skip
    a += more("oficina", "work_disconnect", [
        (100, "disconnect_100", "Derecho a la desconexión (de verdad)",
         "No contestes fuera de hora 100 veces.", L),
    ])  # fmt: skip
    a += more("hongkong", "work_jetlag", [
        (500, "jetlag_500", "Reloj biológico en Kowloon",
         "Ficha 500 veces desde Hong Kong cuando en Canarias es de madrugada.", L),
    ])  # fmt: skip
    a += more("hongkong", "hk_clock_tomorrow", [
        (500, "hk_tomorrow_500", "Viviendo en el futuro",
         "Mira 500 veces la hora cuando en Hong Kong ya es mañana.", E),
    ])  # fmt: skip
    a += more("hongkong", "hk_clock_sleeping", [
        (250, "hk_sleeping_250", "Robuso tiene el móvil en silencio",
         "Mira 250 veces la hora cuando en Hong Kong son entre las 2:00 y las 6:00.", E),
    ])  # fmt: skip
    a += more("hongkong", "hk_clock_lunch", [
        (250, "hk_lunch_250", "Dim sum a distancia",
         "Mira 250 veces la hora cuando Robuso está almorzando.", E),
    ])  # fmt: skip

    # 🔀 Logros cruzados: piden cosas de varias funcionalidades a la vez
    a += [
        combo("x_funcionario", "Funcionario modelo",
              "Escribe 1.000 mensajes en horario de oficina y trabaja 100 turnos. "
              "¿Cuándo trabajas?",
              "work", E, (("msg_office", 1_000), ("work_shifts", 100))),
        combo("x_paguita_casino", "La paguita al casino",
              "Cobra el IMV 100 veces y apuesta 1.000.000 Y$. El dinero público, bien invertido.",
              "casino", E, (("imv_claims", 100), ("casino_wagered", 1_000_000))),
        combo("x_ludopata_nomina", "Trabajo para el casino",
              "Trabaja 100 turnos y quédate a cero 10 veces en el casino.",
              "casino", E, (("work_shifts", 100), ("casino_broke", 10)), secret=True),
        combo("x_contribuyente", "Contribuyente ejemplar",
              "Paga 100.000 Y$ de IRPF, presenta la renta 10 veces y dona 10.000 Y$.",
              "economy", L, (("tax_paid", 100_000), ("renta_filed", 10), ("donated", 10_000))),
        combo("x_noctambulo", "Vampiro de la Moncloa",
              "Escribe 1.000 mensajes de madrugada, pasa 50 h en llamada de madrugada "
              "y trabaja 25 turnos de noche.",
              "time", L, (("msg_night", 1_000), ("voice_night", 3_000), ("work_night", 25))),
        combo("x_canario", "Canario de pura cepa",
              "Habla en canario 100 veces y escribe el Día de Canarias y el día del Pino.",
              "lengua", R, (("msg_canario", 100), ("msg_canarias", 1), ("msg_pino", 1))),
        combo("x_hombre_orquesta", "Hombre orquesta",
              "Pon 50 canciones, haz 50 imágenes, pasa 25 frases por babel y apunta 25 tareas.",
              "memes", R, (("music_queued", 50), ("img_made", 50), ("babel_phrases", 25),
                           ("todo_added", 25))),
        combo("x_tombola", "Tómbola nacional",
              "Haz 50 apuestas de lotería y rasca 100 rascas.",
              "lottery", R, (("lottery_bets", 50), ("lottery_scratches", 100))),
        combo("x_rey_llamada", "Presidente de la llamada",
              "Pasa 500 h en llamada, coincide con 10 personas y comparte pantalla 10 h.",
              "voice", L, (("voice_minutes", 30_000), ("voice_crowd_max", 10),
                           ("voice_stream", 600))),
        combo("x_influencer", "Influencer del servidor",
              "Recibe 5.000 reacciones, haz reír 100 veces y crea 50 imágenes.",
              "social", E, (("reactions_received", 5_000), ("laughs_caused", 100),
                            ("img_made", 50))),
        combo("x_hormiga_cigarra", "Hormiga y cigarra a la vez",
              "Cobra 10.000 Y$ de intereses y apuesta 1.000.000 Y$ en el casino.",
              "bizum", E, (("interest_earned", 10_000), ("casino_wagered", 1_000_000))),
        combo("x_ruleta_fiscal", "Ingeniería fiscal",
              "Juégatelo todo 10 veces y cobra 10 devoluciones de Hacienda.",
              "economy", E, (("casino_all_in", 10), ("tax_refunds", 10)), secret=True),
        combo("x_bienestar", "Estado del bienestar",
              "Cobra el IMV 30 veces, trabaja 100 turnos, manda 25 Bizums y dona 10.000 Y$.",
              "economy", E, (("imv_claims", 30), ("work_shifts", 100), ("bizum_sent_count", 25),
                             ("donated", 10_000))),
        combo("x_comisionista", "Comisionista",
              "Mueve 100.000 Y$ en Bizum y cobra una mordida en política.",
              "jobs", L, (("bizum_sent", 100_000), ("work_kickback", 1)), secret=True),
        combo("x_puerta_giratoria", "Consejero de una eléctrica",
              "Cambia de trabajo 5 veces y de canal de voz 100. Siempre caes de pie.",
              "work", R, (("work_job_changes", 5), ("voice_hops", 100))),
        combo("x_casino_royale", "Casino Royale",
              "Juega 1.000 partidas a ruleta, blackjack, tragaperras, "
              "crash, minas, pollo y pachinko.",
              "casino", L, tuple((stat, 1_000) for stat in (
                  "roulette_spins", "bj_hands", "slots_spins", "crash_rounds", "mines_games",
                  "chicken_games", "pachinko_volleys"))),
        combo("x_hongkones", "Pasaporte de Hong Kong",
              "Trabaja 100 turnos en Hong Kong y mira su hora 100 veces.",
              "hongkong", E, (("work_hk_shifts", 100), ("hk_clock", 100))),
        combo("x_rueda_prensa", "Comparecencia sin preguntas",
              "Haz 500 preguntas y pasa 20 h muteado. Preguntas, pero no escuchas.",
              "convo", R, (("msg_questions", 500), ("voice_muted", 1_200)), secret=True),
        combo("x_todoterreno", "Navaja suiza",
              "Escribe 10.000 mensajes, pasa 100 h en llamada, trabaja 100 turnos "
              "y apuesta 100.000 Y$ en el casino.",
              "chat", L, (("messages_total", 10_000), ("voice_minutes", 6_000),
                          ("work_shifts", 100), ("casino_wagered", 100_000))),
        combo("x_gestor", "Gestor de lo pendiente",
              "Apunta 100 tareas y tacha 100.",
              "todo", E, (("todo_added", 100), ("todo_done", 100))),
        combo("x_madrugador_total", "Gallo y panadero",
              "Sé el primero del día 30 veces y escribe 1.000 mensajes por la mañana.",
              "convo", E, (("msg_first_of_day", 30), ("msg_morning", 1_000))),
        combo("x_dj_entrada", "Entrada triunfal con banda sonora",
              "Que tu entrada suene 100 veces y pon 50 canciones.",
              "voice_moves", R, (("entrance_played", 100), ("music_queued", 50))),
    ]  # fmt: skip

    # 🏆 Coleccionista --------------------------------------------------------------------
    # Se miden con estadísticas virtuales que salen de los logros ya conseguidos
    # (`meta_stats`), nunca de contadores guardados.
    a += _tiers("meta", UNLOCKED_STAT, [
        (10, "meta_10", "Cazador de logros, como Sánchez de titulares", "Desbloquea 10 logros.", C),
        (25, "meta_25", "Coleccionista de relojes de Camps", "Desbloquea 25 logros.", R),
        (50, "meta_50", "Vitrina llena", "Desbloquea 50 logros.", E),
        (100, "meta_100", "Museo", "Desbloquea 100 logros.", L),
        (150, "meta_150", "Hemeroteca", "Desbloquea 150 logros.", L),
        (250, "meta_250", "Patrimonio de la Humanidad", "Desbloquea 250 logros.", L),
        (400, "meta_400", "BOE de logros", "Desbloquea 400 logros.", M),
        (600, "meta_600", "Ministerio de Logros",
         "Desbloquea 600 logros. Con su propio Falcon.", M),
    ])  # fmt: skip
    a += _tiers("meta", "achievements_rare", [
        (25, "meta_rare_25", "Raro, raro", "Desbloquea 25 logros raros.", R),
        (100, "meta_rare_100", "Bicho raro", "Desbloquea 100 logros raros.", E),
    ])  # fmt: skip
    a += _tiers("meta", "achievements_epic", [
        (10, "meta_epic_10", "Épica del NO-DO", "Desbloquea 10 logros épicos.", E),
        (50, "meta_epic_50", "Cantar de gesta", "Desbloquea 50 logros épicos.", L),
    ])  # fmt: skip
    a += _tiers("meta", "achievements_legendary", [
        (3, "meta_legend_3", "Leyenda de barrio", "Desbloquea 3 logros legendarios.", E),
        (15, "meta_legend_15", "Leyenda viva", "Desbloquea 15 logros legendarios.", L),
    ])  # fmt: skip
    a += _tiers("meta", "achievements_mythic", [
        (1, "meta_mythic_1", "Divinidad, como Kim según Kim", "Desbloquea un logro mítico.", L),
        (5, "meta_mythic_5", "Olimpo", "Desbloquea 5 logros míticos.", M),
    ])  # fmt: skip
    a += _tiers("meta", "achievements_secret", [
        (5, "meta_secret_5", "Agente del CNI", "Desbloquea 5 logros secretos.", R),
        (20, "meta_secret_20", "Pegasus",
         "Desbloquea 20 logros secretos. Ya sabes hasta lo que hay en el móvil del presidente.", E),
        (50, "meta_secret_50", "Fontanero de Ferraz", "Desbloquea 50 logros secretos.", L),
    ])  # fmt: skip
    normal_categories = [c for c in CATEGORIES if c.key != "meta" and not c.upcoming]
    a += _tiers("meta", "achievements_categories", [
        (10, "meta_cats_10", "Tocando todos los palos", "Consigue logros en 10 categorías.", R),
        (len(normal_categories), "meta_cats_all", "Hombre del Renacimiento",
         "Consigue al menos un logro en cada categoría.", E),
    ])  # fmt: skip
    a += _tiers("meta", "achievements_categories_done", [
        (1, "meta_done_1", "Perfeccionista", "Completa una categoría entera.", E),
        (5, "meta_done_5", "TOC", "Completa 5 categorías enteras.", L),
    ])  # fmt: skip
    a += _tiers("meta", "achievement_points", [
        (1_000, "meta_pts_1k", "Mil puntos", "Suma 1.000 puntos de logros.", R),
        (5_000, "meta_pts_5k", "Carné por puntos (sin perder ninguno)", "Suma 5.000 puntos.", E),
        (15_000, "meta_pts_15k", "Matrícula de honor", "Suma 15.000 puntos de logros.", L),
    ])  # fmt: skip
    a += _tiers("meta", "achievements_earned", [
        (25_000, "meta_money_25k", "Subvencionado",
         "Cobra 25.000 Y$ brutos en premios de logros.", R),
        (100_000, "meta_money_100k", "Chiringuito de logros", "Cobra 100.000 Y$ en premios.", E),
        (500_000, "meta_money_500k", "Vivir del cuento", "Cobra 500.000 Y$ en premios.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("meta", "achievements_batch", [
        (3, "meta_combo_3", "Combo", "Desbloquea 3 logros de golpe.", R, True),
        (5, "meta_combo_5", "Pleno al quince", "Desbloquea 5 logros de golpe.", E, True),
    ])  # fmt: skip
    a += _tiers("meta", "logros_views", [
        (10, "views_10", "Mírate al espejo, como Trump",
         "Abre tus logros en `perfil` 10 veces.", C),
        (100, "views_100", "Narciso de vitrina", "Abre tus logros en `perfil` 100 veces.", R),
    ])  # fmt: skip
    a += _tiers("meta", "logros_others", [
        (10, "others_10", "Cotilla", "Mira los logros de otra persona 10 veces.", C),
        (100, "others_100", "Portera del edificio", "Mira los logros de otros 100 veces.", R),
    ])  # fmt: skip
    # 👤 Perfil: mirarse y mirar a otros es una decisión, nada pasa de Épico.
    a += _tiers("meta", "perfil_views", [
        (1, "perfil_1", "¿Quién soy yo? Yo soy yo",
         "Abre tu `perfil`. Y así te tienen que soportar.", C),
        (50, "perfil_50", "Selfie diario de Ayuso", "Abre tu `perfil` 50 veces.", R),
        (500, "perfil_500", "Ego de ministro", "Abre tu `perfil` 500 veces.", E),
    ])  # fmt: skip
    a += _tiers("meta", "perfil_others", [
        (1, "perfil_otro_1", "Fisgón de rellano", "Mira el `perfil` de otra persona.", C),
        (50, "perfil_otro_50", "Informe de la UCO", "Mira perfiles ajenos 50 veces.", R),
        (500, "perfil_otro_500", "Pegasus de barrio", "Mira perfiles ajenos 500 veces.", E),
    ])  # fmt: skip
    a += _tiers("meta", PERFIL_SECTIONS_STAT, [
        (len(PERFIL_SECTIONS), "perfil_todo", "Expediente completo",
         "Mira todas las secciones de tu `perfil`.", C),
    ])  # fmt: skip
    a += _tiers("meta", "perfil_bot", [
        (1, "perfil_bot", "Funcionario sin alma",
         "Intenta mirar el perfil de un bot.", C, True),
    ])  # fmt: skip
    a += _tiers("meta", "perfil_night", [
        (1, "perfil_noche", "Crisis existencial de madrugada",
         "Mírate el `perfil` entre las 3 y las 5 de la mañana.", C, True),
    ])  # fmt: skip
    a += _tiers("meta", "logros_ranking", [
        (25, "ranking_25", "Obsesionado con el ranking del CIS",
         "Mira el ranking de logros 25 veces.", R),
    ])  # fmt: skip
    # 📊 Estadísticas del casino (`apuestas`) ------------------------------------------
    # Mirar las cuentas es una decisión, no suerte: nada pasa de Raro
    # (Biblia, «Rarezas»). Los secretos dependen de cómo te vaya a ti.
    a += _tiers("apuestas", "apuestas_views", [
        (1, "apuestas_1", "Mirar el extracto", "Consulta tus `apuestas` por primera vez.", C),
        (10, "apuestas_10", "Contable de la ruina", "Consulta las estadísticas 10 veces.", C),
        (50, "apuestas_50", "Excel de la desgracia", "Consulta las estadísticas 50 veces.", C),
        (250, "apuestas_250", "Asesor fiscal de ti mismo",
         "Consulta las estadísticas 250 veces. Sanxe ya te ofrece trabajo.", R),
        (1_000, "apuestas_1k", "Fiscalizado por la UCO",
         "Consulta las estadísticas 1.000 veces. Ni la UCO revisa tanto.", R),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_snoop", [
        (1, "apuestas_snoop_1", "Cotilla de la UCO", "Mira las apuestas de otra persona.", C),
        (25, "apuestas_snoop_25", "Pegasus en el móvil del vecino",
         "Mira las apuestas de otros 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_page_ranking", [
        (1, "apuestas_rank_1", "¿Quién va primero?", "Abre el ranking del casino.", C),
        (50, "apuestas_rank_50", "Obsesionado con la tabla",
         "Abre el ranking del casino 50 veces.", R),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_page_hacienda", [
        (10, "apuestas_hacienda_10", "Inspector vocacional",
         "Mira 10 veces lo que te ha quitado Hacienda en el casino.", C),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_page_libro", [
        (1, "apuestas_libro_1", "Arqueólogo del libro mayor",
         "Consulta el libro del casino desde el primer día.", C),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_page_horario", [
        (1, "apuestas_horario_1", "Hora feliz (para la casa)",
         "Mira a qué hora pierde más dinero el servidor.", C),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_page_records", [
        (1, "apuestas_records_1", "Salón de la fama y de la vergüenza",
         "Mira los récords del casino.", C),
    ])  # fmt: skip
    a.append(
        combo(
            "apuestas_periods",
            "Hoy, ayer y siempre",
            "Mira las estadísticas en los cuatro periodos (hoy, 7 días, 30 días y siempre).",
            "apuestas",
            C,
            tuple((f"apuestas_period_{p}", 1) for p in APUESTAS_PERIODS),
        )
    )
    a.append(
        combo(
            "apuestas_all_pages",
            "Me lo he leído todo, Sanxe",
            "Abre todas las páginas de `apuestas`.",
            "apuestas",
            C,
            tuple((f"apuestas_page_{page}", 1) for page in APUESTAS_PAGES),
        )
    )
    a += _tiers("apuestas", "apuestas_denial", [
        (1, "apuestas_denial", "Negacionista de la estadística",
         "Mira tus cuentas con más de 100 jugadas y menos de la mitad devuelta. Y sigue.",
         R, True),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_ruin", [
        (1, "apuestas_ruin", "La máquina del fango",
         "Mira tus cuentas yendo 100.000 Y$ abajo en el casino.", R, True),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_rich", [
        (1, "apuestas_rich", "Contando billetes delante de Sanxe",
         "Mira tus cuentas yendo 100.000 Y$ arriba en el casino.", R, True),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_even", [
        (1, "apuestas_even", "Ni pa ti ni pa mí",
         "Mira tus cuentas con 100 jugadas o más y el casino devolviéndote entre el 99 y "
         "el 101 %.", R, True),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_insomnia", [
        (1, "apuestas_insomnia", "Insomnio contable",
         "Repasa las cuentas del casino entre las 2 y las 6 de la madrugada.", C, True),
    ])  # fmt: skip
    a += _tiers("apuestas", "apuestas_virgin", [
        (1, "apuestas_virgin", "Mirar sin tocar",
         "Consulta tus estadísticas del casino sin haber apostado nunca.", C, True),
    ])  # fmt: skip

    # 🎫 Porras ---------------------------------------------------------------------------
    # Montar, aceptar y apostar son decisiones (Común o Raro); lo que pide muchas porras o
    # que el resto del servidor se equivoque sube de rareza. Las cuotas las pone la gente,
    # no un dado: no salen de `auditoria_logros.py`, sino de cuántas porras hacen falta.
    a += _tiers("porras", "porra_opened", [
        (1, "porra_open_1", "Montador de porras", "Monta tu primera `porra` a alguien.", C),
        (10, "porra_open_10", "Peñista", "Monta 10 porras.", C),
        (50, "porra_open_50", "Corredor de apuestas de barra",
         "Monta 50 porras. El bar ya te guarda el taburete.", R),
        (250, "porra_open_250", "Casa de apuestas clandestina", "Monta 250 porras.", E),
        (1_000, "porra_open_1k", "Loterías y Apuestas del Barrio",
         "Monta 1.000 porras. La SELAE quiere hablar contigo.", L),
    ])  # fmt: skip
    a += _tiers("porras", "porra_rejected", [
        (1, "porra_rejected_1", "Calabazas", "Que te rechacen una porra.", C, True),
        (10, "porra_rejected_10", "Ni con un palo", "Que te rechacen 10 porras.", R),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="porra_all_games",
            name="Peña polideportiva",
            description="Monta porras en todos los juegos que las admiten.",
            category="porras",
            rarity=R,
            conditions=tuple((f"{PORRA_GAME_PREFIX}{game}", 1) for game in PORRA_GAMES),
        )
    )
    a.append(
        Achievement(
            id="porra_all_props",
            name="Me sé todas las preguntas",
            description="Monta una porra de cada tipo de propuesta.",
            category="porras",
            rarity=R,
            conditions=tuple((f"{PORRA_PROP_PREFIX}{prop}", 1) for prop in PORRA_PROPS),
        )
    )
    a += _tiers("porras", "porra_long", [
        (1, "porra_long", "Ley de Presupuestos",
         "Monta una porra de más de 5 jugadas (con la libreta de la porra).", R),
    ])  # fmt: skip
    a += _tiers("porras", "porra_opener_against", [
        (1, "porra_fontaneria", "Fontanería de Ferraz",
         "Monta una porra a alguien y apuesta en su contra.", C, True),
    ])  # fmt: skip

    a += _tiers("porras", "porra_accepted", [
        (1, "porra_star_1", "Protagonista", "Acepta una porra sobre ti.", C),
        (10, "porra_star_10", "Carne de porra", "Acepta 10 porras sobre ti.", C),
        (50, "porra_star_50", "Personaje público", "Acepta 50 porras sobre ti.", R),
        (250, "porra_star_250", "Famoseo de tertulia", "Acepta 250 porras sobre ti.", E),
    ])  # fmt: skip
    a += _tiers("porras", "porra_declined", [
        (1, "porra_declined_1", "No, gracias", "Rechaza una porra sobre ti.", C),
        (25, "porra_declined_25", "Escaqueo profesional", "Rechaza 25 porras sobre ti.", R),
    ])  # fmt: skip
    a.append(Achievement(
        id="porra_image_1", name="Derechos de imagen",
        description="Cobra tus primeros derechos de imagen como protagonista de una porra.",
        category="porras", rarity=C, conditions=(("porra_image", 1),), unit="money",
        story=IMAGE_STORY,
    ))  # fmt: skip
    a += _tiers("porras", "porra_image", [
        (10_000, "porra_image_10k", "Imagen de marca",
         "Cobra 10.000 Y$ en derechos de imagen.", R),
        (100_000, "porra_image_100k", "Contrato con una plataforma",
         "Cobra 100.000 Y$ en derechos de imagen.", E),
        (1_000_000, "porra_image_1m", "Influencer de Marbella",
         "Cobra 1.000.000 Y$ en derechos de imagen.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("porras", "porra_star_wins", [
        (1, "porra_star_win_1", "Cumplir las expectativas",
         "Como protagonista, que salga lo bueno para ti.", C),
        (25, "porra_star_win_25", "Fiable como el BOE",
         "Como protagonista, que salga lo bueno para ti 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("porras", "porra_heroic", [
        (1, "porra_heroic", "Les has callado la boca",
         "Como protagonista, que salga lo bueno para ti con al menos 3 personas y todo "
         "el dinero apostado en tu contra.", R, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_no_show", [
        (1, "porra_no_show_1", "Espantada", "Acepta una porra y no juegues a tiempo.", C, True),
        (10, "porra_no_show_10", "Ni está ni se le espera",
         "Deja 10 porras tiradas por no jugar.", R),
    ])  # fmt: skip
    a += _tiers("porras", "porra_broke", [
        (1, "porra_broke", "Arruinado en directo",
         "Quédate sin saldo para seguir en mitad de una porra sobre ti.", C, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_pool_max", [
        (1_000, "porra_pool_1k", "Hay expectación",
         "Protagoniza una porra con 1.000 Y$ en el bote.", C),
        (10_000, "porra_pool_10k", "Prime time",
         "Protagoniza una porra con 10.000 Y$ en el bote.", R),
        (100_000, "porra_pool_100k", "Final de Champions",
         "Protagoniza una porra con 100.000 Y$ en el bote.", E),
    ], unit="money")  # fmt: skip

    a += _tiers("porras", "porra_bets", [
        (1, "porra_bet_1", "Un euro a la porra", "Apuesta en una porra.", C),
        (10, "porra_bet_10", "Habitual del bar", "Apuesta 10 veces en porras.", C),
        (100, "porra_bet_100", "Quinielista", "Apuesta 100 veces en porras.", R),
        (1_000, "porra_bet_1k", "El del boleto en la cartera", "Apuesta 1.000 veces.", E),
        (5_000, "porra_bet_5k", "Abonado de la peña", "Apuesta 5.000 veces en porras.", L),
    ])  # fmt: skip
    a += _tiers("porras", "porra_wins", [
        (1, "porra_win_1", "Lo sabía", "Acierta una porra.", C),
        (10, "porra_win_10", "Ojo de tasador", "Acierta 10 porras.", R),
        (100, "porra_win_100", "Pitoniso", "Acierta 100 porras.", E),
        (500, "porra_win_500", "Oráculo de Teror", "Acierta 500 porras.", L),
    ])  # fmt: skip
    a += _tiers("porras", "porra_losses", [
        (10, "porra_loss_10", "Así es el fútbol", "Falla 10 porras.", C),
        (100, "porra_loss_100", "Siempre al caballo cojo", "Falla 100 porras.", R),
    ])  # fmt: skip
    a += _tiers("porras", "porra_win_streak_max", [
        (3, "porra_streak_3", "Racha de bar", "Acierta 3 porras seguidas.", R),
        (5, "porra_streak_5", "Información privilegiada",
         "Acierta 5 porras seguidas. La UCO toma nota.", E),
        (10, "porra_streak_10", "Cuñado con razón", "Acierta 10 porras seguidas.", L),
    ])  # fmt: skip
    a += _tiers("porras", "porra_win_max", [
        (1_000, "porra_big_1k", "Para cañas", "Gana 1.000 Y$ netos en una porra.", C),
        (10_000, "porra_big_10k", "Pelotazo de barra", "Gana 10.000 Y$ netos en una porra.", R),
        (100_000, "porra_big_100k", "Golpe de mano", "Gana 100.000 Y$ netos en una porra.", E),
        (1_000_000, "porra_big_1m", "El millón del Falcon",
         "Gana 1.000.000 Y$ netos en una porra.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("porras", "porra_odds_max", [
        (3, "porra_odds_3", "Cuota decente", "Acierta una porra que te paga ×3 o más.", C),
        (10, "porra_odds_10", "La sorpresa del Mundial",
         "Acierta una porra que te paga ×10 o más.", R),
        (50, "porra_odds_50", "Leicester campeón",
         "Acierta una porra que te paga ×50 o más.", E, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_lone_wolf", [
        (1, "porra_lone_1", "Contra todo pronóstico",
         "Acierta siendo el único, con al menos 3 personas en contra.", R),
        (10, "porra_lone_10", "Francotirador de la peña",
         "Acierta en solitario contra 3 o más 10 veces.", E),
    ])  # fmt: skip
    a += _tiers("porras", "porra_favourite_flop", [
        (1, "porra_flop_1", "El favorito siempre pierde",
         "Falla una porra en la que tu opción tenía el 75 % del bote o más.", C),
        (10, "porra_flop_10", "Encuesta del CIS",
         "Falla 10 porras siendo el gran favorito.", R),
    ])  # fmt: skip
    a += _tiers("porras", "porra_loyal_losses", [
        (10, "porra_loyal_10", "Fe ciega",
         "Apuesta a favor del protagonista y pierde 10 veces.", R),
        (50, "porra_loyal_50", "Manual de resistencia (del apostante)",
         "Apuesta a favor del protagonista y pierde 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("porras", "porra_tamayazo", [
        (1, "porra_tamayazo", "Tamayazo",
         "Acierta una porra que dio la vuelta en la última jugada.", R, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_refunds", [
        (1, "porra_refund_1", "Devolución de la entrada", "Que te devuelvan una apuesta.", C),
        (25, "porra_refund_25", "Porras de papel mojado",
         "Que te devuelvan 25 apuestas de porras anuladas.", R),
    ])  # fmt: skip
    a += _tiers("porras", "porra_nobody", [
        (1, "porra_nobody", "Nadie lo vio venir",
         "Apuesta en una porra en la que no acierta nadie.", C, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="porra_iaj_1", name="Impuesto sobre Actividades de Juego",
        description="Paga el IAJ de una porra por primera vez.",
        category="porras", rarity=C, conditions=(("porra_iaj", 1),), unit="money",
        story=IAJ_STORY,
    ))  # fmt: skip
    a += _tiers("porras", "porra_iaj", [
        (10_000, "porra_iaj_10k", "La peña paga a Sanxe", "Paga 10.000 Y$ de IAJ.", R),
        (100_000, "porra_iaj_100k", "Mecenas del Falcon", "Paga 100.000 Y$ de IAJ.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("porras", "porra_crowd_max", [
        (5, "porra_crowd_5", "Corrillo", "Participa en una porra con 5 apostantes.", C),
        (10, "porra_crowd_10", "Peña completa",
         "Participa en una porra con 10 apostantes.", R),
        (20, "porra_crowd_20", "Bar lleno en el Clásico",
         "Participa en una porra con 20 apostantes.", E),
    ])  # fmt: skip
    a += _tiers("porras", "porra_bet_min", [
        (1, "porra_bet_min", "Lo mínimo para presumir",
         f"Apuesta {PORRA_MIN_BET} Y$ justos en una porra.", C, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_bet_666", [
        (1, "porra_bet_666", "La porra del diablo", "Apuesta 666 Y$ en una porra.", C, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_bet_69", [
        (1, "porra_bet_69", "Porra picante", "Apuesta 69 Y$ en una porra.", C, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_all_in", [
        (1, "porra_all_in_1", "Todo a la porra", "Apuesta todo tu saldo en una porra.", C),
        (10, "porra_all_in_10", "Me lo juego todo a que no", "Apuesta todo 10 veces.", R),
    ])  # fmt: skip
    a += _tiers("porras", "porra_snoops", [
        (1, "porra_snoop_1", "Vigilancia aduanera",
         "Mira quién apuesta qué con los prismáticos de la UCO.", C),
        (50, "porra_snoop_50", "Pegasus de bolsillo",
         "Usa los prismáticos en 50 porras.", R),
    ])  # fmt: skip
    a += _tiers("porras", "porra_night", [
        (1, "porra_night", "Porra de after",
         "Apuesta en una porra entre las 2:00 y las 6:00.", C, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_carrusel", [
        (1, "porra_carrusel", "Carrusel deportivo",
         "Apuesta en una porra un domingo entre las 16:00 y las 20:00.", C),
    ])  # fmt: skip
    a += _tiers("porras", "porra_nochevieja", [
        (1, "porra_nochevieja", "Porra de las uvas",
         "Apuesta en una porra en Nochevieja.", C, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_canarias", [
        (1, "porra_canarias", "Porra del 30 de mayo",
         "Apuesta en una porra el Día de Canarias.", C, True),
    ])  # fmt: skip
    a += _tiers("porras", "porra_friday13", [
        (1, "porra_friday13", "Gafe profesional",
         "Apuesta en una porra un viernes 13.", R, True),
    ])  # fmt: skip
    a += _tiers("porras", f"{SHOP_USED_PREFIX}bufanda", [
        (10, "porra_bufanda_10", "Grada de animación",
         "Ondea la bufanda de la peña 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("porras", f"{SHOP_USED_PREFIX}silbato", [
        (1, "porra_silbato", "¡Al VAR!",
         "Pita a alguien con el silbato de árbitro.", C, True),
    ])  # fmt: skip

    # 🏰 patrimonio (`perfil`), 💎 `fortunas` y 🧾 la factura de `hacienda` ------------------------
    # Mirar es una decisión: nada pasa de Raro. El patrimonio neto sí cuesta.
    a += _tiers("economy", "patrimonio_views", [
        (1, "patrimonio_1", "Hacer inventario",
         "Mira tu patrimonio en `perfil` (o el de alguien).", C),
        (25, "patrimonio_25", "Contando los duros, como en la peseta",
         "Mira el patrimonio 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("economy", "patrimonio_snoop", [
        (1, "patrimonio_snoop_1", "Registro de la propiedad",
         "Mira el patrimonio de otra persona.", C),
        (25, "patrimonio_snoop_25", "Inspector del catastro",
         "Mira el patrimonio de otros 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("economy", "fortunas_views", [
        (1, "fortunas_1", "Lista Forbes del barrio", "Abre `fortunas`.", C),
        (25, "fortunas_25", "Envidia sana", "Abre `fortunas` 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("economy", "net_worth_max", [
        (50_000, "networth_50k", "Propietario, con hipoteca a 40 años",
         "Llega a 50.000 Y$ de patrimonio.", C),
        (500_000, "networth_500k", "Rentista de pisos turísticos",
         "Llega a 500.000 Y$ de patrimonio.", R),
        (5_000_000, "networth_5m", "Gran fortuna, como la de los Pujol",
         "Llega a 5.000.000 Y$ de patrimonio.", E),
        (50_000_000, "networth_50m", "Lista Forbes de verdad",
         "Llega a 50.000.000 Y$ de patrimonio.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("economy", "fortunas_first", [
        (1, "fortunas_first", "El Amancio Ortega del servidor",
         "Mira `fortunas` siendo el más rico.", R, True),
    ])  # fmt: skip
    a += _tiers("economy", "fortunas_last", [
        (1, "fortunas_last", "La base de la pirámide",
         "Mira `fortunas` siendo el último de la lista (con al menos 3 personas).", C, True),
    ])  # fmt: skip
    a += _tiers("economy", "patrimonio_illiquid", [
        (1, "patrimonio_illiquid", "Rico en ladrillo, pobre en liquidez",
         "Mira tu patrimonio con más de la mitad en cosas y no en efectivo.", R, True),
    ])  # fmt: skip
    a += _tiers("economy", "hacienda_views", [
        (1, "hacienda_open_1", "¿Y esto adónde va?", "Abre `hacienda`.", C),
        (25, "hacienda_open_25", "Votante informado, especie protegida",
         "Abre `hacienda` 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("economy", "hacienda_self", [
        (1, "hacienda_self_1", "Mi factura con Sanxe", "Mira tu propia factura fiscal.", C),
        (25, "hacienda_self_25", "Masoquista fiscal", "Mira tu factura fiscal 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("economy", "hacienda_snoop", [
        (1, "hacienda_snoop_1", "Chivato de Hacienda",
         "Mira la factura fiscal de otra persona.", C),
    ])  # fmt: skip
    a += _tiers("economy", "hacienda_pillar", [
        (1, "hacienda_pillar", "Tú solo sostienes el Estado",
         "Mira `hacienda` habiendo pagado tú la mitad de todo lo recaudado.", R, True),
    ])  # fmt: skip
    a += _tiers("economy", "hacienda_hidden", [
        (1, "hacienda_hidden", "Te sangran sin que lo notes",
         "Mira `hacienda` pagando más en impuestos indirectos que directos.", C, True),
    ])  # fmt: skip

    # 🏇 Caballos ------------------------------------------------------------------------
    a += _tiers("horses", "horse_bets", [
        (1, "caballo_1", "Día de carreras en la Zarzuela",
         "Haz tu primer boleto en las carreras.", C),
        (10, "caballo_10", "Socio del hipódromo", "Haz 10 boletos en las carreras.", C),
        (100, "caballo_100", "Tribuna de honor", "Haz 100 boletos en las carreras.", R),
        (1_000, "caballo_1k", "Palco presidencial", "Haz 1.000 boletos en las carreras.", E),
        (5_000, "caballo_5k", "Patrimonio Nacional", "Haz 5.000 boletos en las carreras.", L),
    ])  # fmt: skip
    a += _tiers("horses", "horse_hits", [
        (1, "caballo_hit_1", "¡Ha entrado!", "Cobra tu primer boleto.", C),
        (25, "caballo_hit_25", "Ojo de tratante", "Cobra 25 boletos.", R),
        (250, "caballo_hit_250", "Pronóstico del BOE", "Cobra 250 boletos.", E),
        (1_000, "caballo_hit_1k", "Más aciertos que el CIS", "Cobra 1.000 boletos.", L),
    ])  # fmt: skip
    a += _tiers("horses", "horse_hits_colocado", [
        (50, "caballo_coloc_50", "Colocado, como un enchufado",
         "Cobra 50 boletos a colocado.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_hits_gemela", [
        (1, "caballo_gemela_1", "Gemelos univitelinos",
         "Acierta una gemela (1º y 2º en orden).", C),
        (25, "caballo_gemela_25", "Coalición de gobierno",
         "Acierta 25 gemelas. Dos que llegan juntos y en orden.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_hits_trio", [
        (1, "caballo_trio_1", "Trío de ases", "Acierta un trío (el podio entero, en orden).", R),
        (10, "caballo_trio_10", "Tripartito", "Acierta 10 tríos.", L),
    ])  # fmt: skip
    a.append(Achievement(
        id="caballo_quiniela",
        name="Quiniela completa",
        description="Haz un boleto de cada tipo: ganador, colocado, gemela y trío.",
        category="horses",
        rarity=C,
        conditions=tuple((f"horse_kind_{kind}", 1) for kind in HORSE_BET_KINDS),
    ))  # fmt: skip
    a += _tiers("horses", "horse_odds_max", [
        (500, "caballo_x5", "Caballo de segunda fila", "Cobra un boleto a 5x o más.", C),
        (2_000, "caballo_x20", "Tapado", "Cobra un boleto a 20x o más.", R),
        (10_000, "caballo_x100", "Pelotazo del ladrillo", "Cobra un boleto a 100x o más.", E),
        (100_000, "caballo_x1000", "Indulto a la banca", "Cobra un boleto a 1.000x o más.", M),
    ])  # fmt: skip
    a += _tiers("horses", "horse_win_max", [
        (10_000, "caballo_rich", "Premio gordo en la Zarzuela",
         "Gana 10.000 Y$ con un boleto.", E),
        (100_000, "caballo_richer", "Cuadra en Marbella",
         f"Gana 100.000 Y$ con un boleto. {TAX_COLLECTOR} ya ha cogido sitio en la tribuna.", L),
    ])  # fmt: skip
    a += _tiers("horses", "horse_long_shots", [
        (1, "caballo_tapado_1", "Tapado de manual",
         "Gana a ganador con un caballo a 10x o más.", R),
        (10, "caballo_tapado_10", "Especialista en tapados",
         "Gana 10 veces a ganador con caballos a 10x o más.", L),
    ])  # fmt: skip
    a += _tiers("horses", "horse_photo_wins", [
        (1, "caballo_foto_1", "Sale bien en la foto",
         "Cobra un boleto decidido por foto-finish.", C),
        (10, "caballo_foto_10", "Fotogénico", "Cobra 10 boletos decididos por foto-finish.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_nose_losses", [
        (1, "caballo_cabeza", "Por una cabeza",
         "Pierde un boleto a ganador porque tu caballo entra 2º por una nariz o menos.", R),
        (10, "caballo_cabeza_10", "Gardel lloraría",
         "Pierde 10 boletos a ganador por una nariz o menos.", L),
    ])  # fmt: skip
    a += _tiers("horses", "horse_seconds", [
        (10, "caballo_segundo_10", "Eterno segundón",
         "Tu caballo a ganador entra 2º 10 veces.", C),
        (100, "caballo_segundo_100", "Subcampeón del Estado",
         "Tu caballo a ganador entra 2º 100 veces.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_lasts", [
        (1, "caballo_farolillo", "Farolillo rojo", "Tu caballo entra el último.", C),
        (50, "caballo_farolillo_50", "Abonado al farolillo",
         "Tu caballo entra el último 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_bolted", [
        (1, "caballo_desbocado", "Se fue en el Falcon",
         "Tu caballo se desboca y se va hacia la grada.", C, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_stumble_wins", [
        (1, "caballo_tropiezo", "Tropezar y ganar",
         "Gana a ganador con un caballo que ha tropezado en la carrera.", L),
    ])  # fmt: skip
    a += _tiers("horses", "horse_comebacks", [
        (1, "caballo_remontada", "Del último al primero",
         "Gana a ganador con un caballo que iba último a mitad de carrera.", E),
        (10, "caballo_remontada_10", "Especialista en remontadas",
         "Gana 10 veces con caballos que iban últimos a mitad de carrera.", M),
    ])  # fmt: skip
    a += _tiers("horses", "horse_manual_comeback", [
        (1, "caballo_resistencia", "Resistir es vencer",
         "Gana con Manual de Resistencia remontando desde el último puesto.", L, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_rain_wins", [
        (1, "caballo_lluvia", "Cantando bajo la lluvia",
         "Cobra un boleto en una carrera en la que se puso a llover.", C),
    ])  # fmt: skip
    a += _tiers("horses", "horse_mud_wins", [
        (10, "caballo_barro_10", "Máquina del barro",
         "Cobra 10 boletos con la pista embarrada.", R),
    ])  # fmt: skip
    a += _tiers("horses", "horse_tired_wins", [
        (1, "caballo_cansado", "Sin vacaciones",
         "Gana a ganador con un caballo que salió cansado.", R, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_sanxe_hits", [
        (1, "caballo_cis", "Lo dijo el CIS",
         f"Gana a ganador con el caballo que pronosticó {TAX_COLLECTOR}.", C),
        (25, "caballo_cis_25", "Tertuliano de cabecera",
         f"Gana 25 veces con el pronóstico de {TAX_COLLECTOR}.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_contra_sanxe", [
        (10, "caballo_oposicion", "Ni caso a Perro Sanxe",
         f"Gana 10 veces a ganador con otro caballo cuando {TAX_COLLECTOR} falla.", R),
        (100, "caballo_oposicion_100", "Oposición frontal",
         f"Gana 100 veces llevándole la contraria a {TAX_COLLECTOR}, y con razón.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_via_sanxe", [
        (10, "caballo_via_sanxe", "Voto cautivo", "Pulsa 🐶 Lo de Sanxe 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("horses", "horse_via_pueblo", [
        (10, "caballo_via_pueblo", "Donde va Vicente", "Pulsa 🐑 Con el pueblo 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("horses", "horse_via_azar", [
        (10, "caballo_via_azar", "Dios proveerá", "Pulsa 🎲 Al azar 10 veces.", C),
    ])  # fmt: skip
    a += _tiers("horses", "horse_ready", [
        (1, "caballo_listo", "Ni un minuto más", "Pulsa ✅ Listo en una carrera.", C),
        (25, "caballo_listo_25", "Agenda de ministro",
         "Pulsa ✅ Listo en 25 carreras. Tienes un Falcon esperando.", R),
        (200, "caballo_listo_200", "Prisa de fin de legislatura",
         "Pulsa ✅ Listo en 200 carreras.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_starter", [
        (1, "caballo_pistoletazo", "Pistoletazo de salida",
         "Sé el último en pulsar ✅ Listo y que los caballos salgan por ti.", C),
        (20, "caballo_decreto", "Real Decreto-ley",
         "Arranca 20 carreras con tu ✅ Listo. Sin pasar por el Congreso.", R),
        (100, "caballo_rodillo", "Mayoría absoluta de prisas",
         "Arranca 100 carreras con tu ✅ Listo.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_flash", [
        (1, "caballo_visto_no_visto", "Ni el BOE va tan rápido",
         "Corre una carrera que sale con ✅ Listo en menos de 15 segundos.", C, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_lone_wins", [
        (1, "caballo_verso_suelto", "Verso suelto",
         "Gana con un caballo al que no apostaba nadie más, con 3 boletos o más en la carrera.",
         C),
    ])  # fmt: skip
    a += _tiers("horses", "horse_party_max", [
        (3, "caballo_pena", "Peña hípica", "Corre una carrera con 3 boletos o más.", C),
        (6, "caballo_tribuna", "Tribuna llena", "Corre una carrera con 6 boletos o más.", R),
        (10, "caballo_derbi", "Derbi de Epsom", "Corre una carrera con 10 boletos o más.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_gp_bets", [
        (1, "caballo_gp_1", "Gran Premio", "Haz un boleto en el Gran Premio.", C),
        (10, "caballo_gp_10", "Habitual del Gran Premio", "Haz 10 boletos en el Gran Premio.", R),
        (50, "caballo_gp_50", "Pamela y chaqué", "Haz 50 boletos en el Gran Premio.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_pot_wins", [
        (1, "caballo_bote_1", "El bote de Perro Sanxe",
         "Llévate el bote del Gran Premio, aunque sea a medias.", R),
        (5, "caballo_bote_5", "Bote, bote, bote", "Llévate el bote del Gran Premio 5 veces.", E),
    ])  # fmt: skip
    a += _tiers("horses", "horse_pot_max", [
        (10_000, "caballo_bote_gordo", "Bote acumulado",
         "Cobra 10.000 Y$ o más de un bote del Gran Premio.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("horses", "horse_fav_flops", [
        (1, "caballo_cis_falla", "Encuesta de Tezanos",
         "Tu favorito a 2x o menos se queda fuera del podio.", C, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_reversed", [
        (1, "caballo_reves", "Del revés",
         "Falla una gemela por haber puesto el 1º y el 2º al revés.", C),
    ])  # fmt: skip
    a += _tiers("horses", "horse_jumbled", [
        (1, "caballo_trio_lio", "Trío desordenado",
         "Falla un trío con los tres caballos del podio, pero en otro orden.", C),
    ])  # fmt: skip
    a += _tiers("horses", "horse_night", [
        (1, "caballo_noche", "Hípica de madrugada", "Haz un boleto entre las 3 y las 6.", C, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_uco_wins", [
        (1, "caballo_uco", "La UCO siempre llega", "Gana a ganador con La UCO.", R, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_puerta_long", [
        (1, "caballo_puerta", "Puertas giratorias",
         "Gana a ganador con Puerta Giratoria a 5x o más.", R, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_falcon_bets", [
        (10, "caballo_falcon", "Viaje oficial",
         "Apuesta 10 veces por Falcon Presidencial.", C, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_paguita_imv", [
        (1, "caballo_paguita_imv", "Invertir la paguita",
         "Apuesta 1.500 Y$ justos a La Paguita. El IMV entero, a un caballo.", C, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_colchon_night", [
        (1, "caballo_colchon", "Colchón de madrugada",
         "Apuesta por Colchón de la Moncloa entre las 3 y las 6.", C, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_wepa_gp", [
        (1, "caballo_wepa_gp", "¡Wepa en el Gran Premio!",
         "Gana el Gran Premio a ganador con Wepa Boricua.", R, True),
    ])  # fmt: skip
    a += _tiers("horses", "horse_taxed", [
        (1, "caballo_irpf", "Hacienda también apuesta",
         "Cobra un boleto y que te retengan IRPF por él.", C),
    ])  # fmt: skip
    a.append(Achievement(
        id="caballo_establo",
        name="Todo el establo",
        description="Apuesta por los 16 caballos del establo.",
        category="horses",
        rarity=R,
        conditions=((HORSE_BACKED_KINDS_STAT, len(HORSE_KEYS)),),
    ))  # fmt: skip
    a += _tiers("horses", HORSE_BACKED_MAX_STAT, [
        (25, "caballo_hincha", "Hincha", "Apuesta 25 veces por el mismo caballo.", C),
        (100, "caballo_socio", "Socio de número", "Apuesta 100 veces por el mismo caballo.", R),
        (500, "caballo_pena_vida", "Peña de toda la vida",
         "Apuesta 500 veces por el mismo caballo.", E),
    ])  # fmt: skip
    a.append(Achievement(
        id="caballo_distancias",
        name="Kilómetro hípico",
        description="Apuesta en las cuatro distancias: 1.200, 1.600, 2.000 y 2.400 m.",
        category="horses",
        rarity=R,
        conditions=tuple((f"horse_dist_{d}", 1) for d in HORSE_DISTANCES),
    ))  # fmt: skip
    a.append(Achievement(
        id="casino_nine_games",
        name="La novena",
        description="Juega a los nueve juegos del casino, las carreras incluidas.",
        category="casino",
        rarity=R,
        conditions=(
            ("roulette_spins", 1), ("bj_hands", 1), ("slots_spins", 1),
            ("crash_rounds", 1), ("mines_games", 1), ("pachinko_volleys", 1),
            ("botes_spins", 1), ("chicken_games", 1), ("horse_bets", 1),
        ),
    ))  # fmt: skip
    a += [
        combo("x_marshall", "Bienvenido, Mister Marshall",
              "Quédate a cero 10 veces y gana 10 all-in. Os debemos una explicación.",
              "casino", E, (("casino_broke", 10), ("casino_all_in_wins", 10))),
        combo("x_taj_mahal", "Trump Taj Mahal",
              "Apuesta 10.000.000 Y$ y quédate a cero 50 veces. Trump quebró seis veces "
              "y llegó a la Casa Blanca: hay esperanza.",
              "casino", L, (("casino_wagered", 10_000_000), ("casino_broke", 50))),
        combo("x_yes_we_can", "Yes we can (y luego no)",
              "Quédate a cero en el casino y gana un all-in. Hope and change.",
              "casino", R, (("casino_broke", 1), ("casino_all_in_wins", 1))),
        combo("x_mesa_putin", "La mesa larguísima de Putin",
              "Juega bacará con 6 en la mesa, un Crash con 8 y una carrera con 10 boletos. "
              "Aun así, la mesa del Kremlin era más larga.",
              "casino", E, (("bac_table_max", 6), ("crash_party_max", 8),
                            ("horse_party_max", 10))),
        combo("x_g20_espolones", "Cumbre del G-20 con espolones",
              "Gánales en el reñidero a Arancel, Crédito Social, Cúpula de Hierro, el Gallo "
              "de las Actas y el Gallo de Marte.",
              "casino", R, tuple((f"gallos_beat_{key}", 1) for key in (
                  "arancel", "credito", "cupula", "actas", "marte"))),
        combo("x_norcorea", "Resultados a la norcoreana",
              "Acierta 7 seguidas a cara o cruz y gana 10 peleas seguidas con tu gallo. "
              "Sin oposición, como en Pyongyang.",
              "casino", E, (("coin_streak_max", 7), ("gallos_streak_max", 10))),
        combo("x_cloacas", "Las cloacas del Estado",
              "Saca ojos de Pegasus, gana con La UCO y mira las apuestas de otros 25 veces.",
              "casino", R, (("dice_snake_eyes", 1), ("horse_uco_wins", 1),
                            ("apuestas_snoop", 25)), secret=True),
        combo("x_elon_marte", "Elon, llévame contigo",
              "Juega un Crash que llegue a 100x y gánale al Gallo de Marte. Marte, por fin.",
              "casino", R, (("crash_moon", 1), ("gallos_beat_marte", 1))),
    ]  # fmt: skip
    a += _tiers("casino", "casino_broke", [
        (1_000, "broke_1k", "Más quiebras que Trump",
         "Quédate a cero en el casino 1.000 veces. Él solo lleva seis; tú juegas en otra liga.",
         M),
    ])  # fmt: skip
    a += _tiers("casino", "casino_all_in", [
        (1_000, "allin_1k", "La motosierra de Milei",
         "Ve all-in 1.000 veces. ¡Afuera el saldo!", M),
    ])  # fmt: skip
    a += _tiers("casino", "casino_loss_streak_max", [
        (100, "lstreak_100", "Gafe de Estado, con escolta",
         "Pierde 100 jugadas seguidas. Hillary te manda un correo (que luego borra).", L),
    ])  # fmt: skip
    a += _tiers("casino", "casino_win_streak_max", [
        (100, "wstreak_100", "Cien de cien, como Kim en las urnas",
         "Gana 100 jugadas seguidas en el casino.", E),
    ])  # fmt: skip

    # 🍸 Bacará ---------------------------------------------------------------------------
    a += _tiers("baccarat", "bac_hands", [
        (1, "bac_1", "Bond, James Bond", "Juega tu primera mano de bacará.", C),
        (100, "bac_100", "Habitual de Punto y Banca", "Juega 100 manos de bacará.", C),
        (1_000, "bac_1k", "Socio del casino de Torrelodones", "Juega 1.000 manos de bacará.", E),
        (10_000, "bac_10k", "Ballena de Macao", "Juega 10.000 manos de bacará.", L),
        (50_000, "bac_50k", "Dueño del zapato",
         "Juega 50.000 manos de bacará. El crupier ya te llama por tu nombre.", M),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_wins", [
        (50, "bacw_50", "Punto a favor, como en un debate de Feijóo",
         "Gana 50 manos de bacará.", C),
        (500, "bacw_500", "Mayoría en la mesa verde", "Gana 500 manos de bacará.", E),
        (5_000, "bacw_5k", "Sanchismo de casino",
         "Gana 5.000 manos de bacará. Pierdes elecciones, pero gobiernas.", L),
        (20_000, "bacw_20k", "Manual de resistencia en Macao", "Gana 20.000 manos de bacará.", M),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_losses", [
        (1, "bacp_1", "Shaken, not stirred", "Pierde tu primera mano de bacará.", C),
        (50, "bacp_50", "Rescate a la Banca", "Pierde 50 manos de bacará.", C),
        (500, "bacp_500", "Contribuyente neto del casino", "Pierde 500 manos de bacará.", E),
        (5_000, "bacp_5k", "Déficit estructural", "Pierde 5.000 manos de bacará.", L),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_banker_wins", [
        (100, "bac_banca_100", "La Banca siempre gana, que se lo digan a Botín",
         "Gana 100 manos apostando a la Banca.", R),
        (1_000, "bac_banca_1k", "Consejero del Banco de España",
         "Gana 1.000 manos apostando a la Banca.", L),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_player_wins", [
        (100, "bac_jugador_100", "Del lado del pueblo, como dice Podemos desde Galapagar",
         "Gana 100 manos apostando al Jugador.", R),
        (1_000, "bac_jugador_1k", "Sindicalista de la mesa verde",
         "Gana 1.000 manos apostando al Jugador.", L),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_tie_wins", [
        (1, "bac_empate", "Empate a todo", "Acierta un empate en el bacará (paga 8 a 1).", C),
        (10, "bac_empate_10", "Equidistante profesional", "Acierta 10 empates.", R),
        (50, "bac_empate_50", "Tablas en el Congreso", "Acierta 50 empates.", E),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_pushes", [
        (1, "bac_devuelto", "Que me quede como estaba",
         "Te devuelven lo apostado a Jugador o a Banca porque empatan.", C),
        (100, "bac_devuelto_100", "Ni sube ni baja, como el IPC oficial",
         "Te devuelven lo apostado por empate 100 veces.", E),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_naturals_won", [
        (1, "bac_natural", "Natural como la vida misma",
         "Gana una mano con un natural (8 o 9 en dos cartas).", C),
        (100, "bac_natural_100", "Naturalidad ministerial", "Gana 100 manos con natural.", R),
        (1_000, "bac_natural_1k", "Más natural que el agua de Firgas",
         "Gana 1.000 manos con natural.", L),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_nine_wins", [
        (1, "bac_nueve", "El nueve de toda la vida, como Bernie Sanders de joven",
         "Gana con un natural de 9.", C),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_comebacks", [
        (1, "bac_remontada", "Remontada de la tercera carta",
         "Gana una mano que ibas perdiendo con las dos primeras cartas.", C),
        (100, "bac_remontada_100", "Moción de censura constructiva",
         "Gana 100 manos remontando con la tercera carta.", E),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_squeeze_nine", [
        (1, "bac_apreton", "Apretar hasta el nueve",
         "Gana una mano en la que tu lado llega a 9 con la tercera carta.", C),
        (50, "bac_apreton_50", "Dedos de crupier de Macao",
         "Llega a 9 con la tercera carta y gana 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_lost_by_one", [
        (1, "bac_por_uno", "A un punto del escaño",
         "Pierde una mano de bacará por un solo punto.", C),
        (100, "bac_por_uno_100", "Siempre a un escaño", "Pierde 100 manos por un solo punto.", E),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_six_cards", [
        (100, "bac_seis", "Mano larga",
         "Juega 100 manos en las que salen las seis cartas.", R),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_natural_beaten", [
        (1, "bac_natural_pero", "Natural, pero menos",
         "Pierde con un natural de 8 contra un natural de 9.", C, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_tie_zero", [
        (1, "bac_cero", "Cero a cero en el Bernabéu",
         "Juega una mano que acaba en empate a cero.", C, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_natural_tie", [
        (1, "bac_empate_natural", "Empate técnico en el CIS",
         "Juega una mano en la que los dos sacan el mismo natural.", C, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_win_streak_max", [
        (3, "bac_racha_3", "Tres de tres, como Trump con los jueces del Supremo",
         "Gana 3 manos de bacará seguidas.", C),
        (6, "bac_racha_6", "Legislatura sin sobresaltos", "Gana 6 manos de bacará seguidas.", C),
        (9, "bac_racha_9", "Mayoría absoluta en la mesa", "Gana 9 manos de bacará seguidas.", E),
        (12, "bac_racha_12", "Ni una moción de censura",
         "Gana 12 manos de bacará seguidas.", M),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_win_max", [
        (10_000, "bac_rico", "Martini con aceituna de oro",
         "Gana 10.000 Y$ en una mano de bacará.", E),
        (100_000, "bac_millonario", "Al servicio de Su Majestad",
         "Gana 100.000 Y$ en una mano de bacará.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("baccarat", "bac_road_longest", [
        (8, "bac_dragon", "Ver al dragón de Xi",
         "Juega en un zapato en el que un lado gana 8 manos seguidas.", R),
        (12, "bac_dragon_12", "El dragón de Komodo",
         "Juega en un zapato en el que un lado gana 12 manos seguidas.", E),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_dragon_ride", [
        (1, "bac_montar", "Montar al dragón, como Putin a caballo",
         "Apuesta a favor de una racha de 5 o más y gana.", C),
        (50, "bac_montar_50", "Jinete del dragón", "Monta al dragón y gana 50 veces.", E),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_dragon_slay", [
        (1, "bac_cortar", "Cortarle la cola al dragón",
         "Apuesta contra una racha de 5 o más y gana.", C),
        (50, "bac_cortar_50", "San Jorge de Moncloa", "Corta al dragón 50 veces.", L),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_pingpong", [
        (1, "bac_pingpong", "Ping-pong parlamentario",
         "Gana una mano tras 6 o más que alternan Banca y Jugador.", C, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_table_max", [
        (3, "bac_mus", "Partida de mus en el Senado",
         "Juega una mano de bacará con 3 personas en la mesa.", C),
        (6, "bac_consejo", "Mesa del Congreso",
         "Juega una mano de bacará con 6 personas en la mesa.", R),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_alone_win", [
        (1, "bac_verso_suelto", "Voto en contra de mi grupo",
         "Gana siendo el único en tu lado, con al menos dos en el contrario.", R, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_herd_loss", [
        (1, "bac_rebano", "Efecto rebaño",
         "Pierde una mano en la que los 3 o más de la mesa iban al mismo lado.", C, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_starter", [
        (25, "bac_crupier", "El crupier eres tú",
         "Sé quien pulsa el ¡Cartas! que reparte 25 veces.", C),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_hedge", [
        (1, "bac_pacto", "Pacto de Gobierno",
         "Apuesta a la Banca y al Jugador en la misma mano.", C, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_all_three", [
        (1, "bac_cafe", "Geometría variable",
         "Apuesta a Jugador, Banca y Empate en la misma mano.", C, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_shoe_first", [
        (1, "bac_estreno", "Estreno de zapato de Imelda Marcos",
         "Juega la primera mano de un zapato nuevo.", C),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_shoe_last", [
        (1, "bac_cierre", "Cierre de zapato", "Juega la última mano de un zapato.", C),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_shoe_hands_max", [
        (30, "bac_zapato_30", "Zapato gastado, como el discurso de Yolanda",
         "Juega 30 manos del mismo zapato.", C),
        (70, "bac_zapato_70", "Del corte a la quema", "Juega 70 manos del mismo zapato.", R),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_commission", [
        (1_000, "bac_comision", "El 5 % de toda la vida",
         "Paga 1.000 Y$ de comisión a la Banca.", R),
        (10_000, "bac_comision_10k", "Comisionista de mascarillas",
         "Paga 10.000 Y$ de comisión a la Banca.", L),
        (100_000, "bac_comision_100k", "La UCO pregunta por el 5 %",
         "Paga 100.000 Y$ de comisión a la Banca.", M),
    ], unit="money")  # fmt: skip
    a += _tiers("baccarat", "bac_banker_min", [
        (1, "bac_justo", "Lo justo para la comisión",
         "Apuesta a la Banca el mínimo, 20 Y$.", C, True),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_night", [
        (1, "bac_madrugada", "Ludópata de madrugada",
         "Juega al bacará de madrugada (de 2 a 6).", C),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_newyear", [
        (1, "bac_uvas", "Uvas y bacará", "Juega al bacará en Nochevieja.", C),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_pino", [
        (1, "bac_pino", "Romería al casino",
         "Juega al bacará el 8 de septiembre, día del Pino.", C),
    ])  # fmt: skip
    a += _tiers("baccarat", "bac_friday13", [
        (1, "bac_viernes13", "Gafe de mesa verde",
         "Pierde una mano de bacará un viernes 13.", C, True),
    ])  # fmt: skip

    # 🐓 Gallos ---------------------------------------------------------------------------
    fights = _tiers("gallos", "gallos_fights", [
        (1, "gallos_1", "Al reñidero", "Pelea con tu gallo por primera vez.", C),
        (10, "gallos_10", "Gallo de corral", "Pelea 10 veces con tu gallo.", C),
        (100, "gallos_100", "Gallera de barrio", "Pelea 100 veces con tu gallo.", R),
        (1_000, "gallos_1k", "Gallero de toda la vida", "Pelea 1.000 veces con tu gallo.", E),
        (5_000, "gallos_5k", "Federación Gallística Internacional",
         "Pelea 5.000 veces. La FIFA de los gallos ya te manda la acreditación.", L),
    ])  # fmt: skip
    fights[0] = replace(fights[0], story=GALLOS_STORY)
    a += fights
    a += _tiers("gallos", "gallos_wins", [
        (1, "gallos_wins_1", "Kikirikí", "Gana tu primera pelea de gallos.", C),
        (25, "gallos_wins_25", "Rey del corral", "Gana 25 peleas de gallos.", C),
        (100, "gallos_wins_100", "Gallo de oro",
         "Gana 100 peleas. Te regalan el Gallo de oro, con certificado y todo.", R),
        (500, "gallos_wins_500", "Imperio del espolón", "Gana 500 peleas de gallos.", E),
        (2_500, "gallos_wins_2500", "Manual de resistencia gallístico",
         "Gana 2.500 peleas de gallos. Ni las mociones de censura pueden contigo.", L),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_losses", [
        (1, "gallos_loss_1", "Caldo de gallina", "Pierde tu primera pelea de gallos.", C),
        (10, "gallos_loss_10", "Pollo asado", "Pierde 10 peleas de gallos.", C),
        (100, "gallos_loss_100", "Gallina en pepitoria", "Pierde 100 peleas de gallos.", R),
        (1_000, "gallos_loss_1k", "Más palos que una piñata",
         "Pierde 1.000 peleas y sigue llevando al gallo al reñidero.", E),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_kos", [
        (1, "gallos_ko_1", "KO técnico", "Gana una pelea por KO.", C),
        (25, "gallos_ko_25", "Mike Tyson con plumas", "Gana 25 peleas por KO.", R),
        (250, "gallos_ko_250", "La UCO del reñidero", "Gana 250 peleas por KO.", E),
        (1_000, "gallos_ko_1k", "Pegasus te tiene fichado",
         "Gana 1.000 peleas por KO. Hasta los servicios secretos quieren tu gallo.", L),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_ko_taken", [
        (1, "gallos_estrellitas", "Viendo estrellitas", "Que tumben a tu gallo por KO.", C),
        (50, "gallos_sparring", "Sparring profesional",
         "Que tumben a tu gallo 50 veces. Ya cobra por dejarse.", R, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_fled", [
        (1, "gallos_maletero", "Huida en el maletero",
         "Que tu gallo salga por patas del reñidero. Waterloo queda a dos horas.", C, True),
        (25, "gallos_waterloo", "Exilio en Waterloo", "Que tu gallo huya 25 veces.", R),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_made_flee", [
        (1, "gallos_que_se_vaya", "Que se vaya, que se vaya",
         "Haz que el gallo rival salga corriendo.", C),
        (50, "gallos_devolucion", "Devolución en caliente",
         "Haz que 50 gallos salgan corriendo del reñidero.", R),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_points_wins", [
        (1, "gallos_jueces", "Decisión de los jueces", "Gana una pelea a los puntos.", C),
        (50, "gallos_constitucional", "El Constitucional te da la razón",
         "Gana 50 peleas a los puntos.", R),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_crits", [
        (10, "gallos_crit_10", "Golpe crítico", "Da 10 golpes críticos.", C),
        (100, "gallos_crit_100", "Recorte con motosierra", "Da 100 golpes críticos.", R),
        (1_000, "gallos_crit_1k", "Arancel del 200 %", "Da 1.000 golpes críticos.", E),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_dodges", [
        (50, "gallos_cintura", "Cintura de torero", "Esquiva 50 golpes.", C),
        (500, "gallos_ministro", "Respuesta de ministro",
         "Esquiva 500 golpes. Ni una pregunta de control te ha tocado.", R),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_damage_max", [
        (40, "gallos_dmg_40", "Pegada seca", "Quita 40 de vida de un solo golpe.", C),
        (60, "gallos_dmg_60", "Golpe de efecto",
         "Quita 60 de vida de un solo golpe. Un crítico de patada voladora, mínimo.", R, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_flawless", [
        (1, "gallos_ni_un_rasguno", "Ni un rasguño",
         "Gana una pelea sin que te toquen ni una pluma.", R, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_close_win", [
        (1, "gallos_por_los_pelos", "Con la última pluma",
         "Gana una pelea con 10 de vida o menos.", C, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_blitz", [
        (1, "gallos_relampago", "Guerra relámpago",
         "Gana por KO en el primer asalto.", C),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_streak_max", [
        (3, "gallos_racha_3", "Hat-trick de espolones", "Gana 3 peleas seguidas con tu gallo.", C),
        (5, "gallos_racha_5", "Pentacampeón", "Gana 5 peleas seguidas con tu gallo.", C),
        (10, "gallos_racha_10", "Mandato eterno", "Gana 10 peleas seguidas con tu gallo.", R),
        (20, "gallos_racha_20", "Presidente vitalicio del gallinero",
         "Gana 20 peleas seguidas. Las elecciones, ya si eso, otro año.", E),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_days_max", [
        (7, "gallos_dias_7", "Gallo madrugador", "Pelea 7 días seguidos.", R),
        (30, "gallos_dias_30", "Kikirikí diario", "Pelea 30 días seguidos.", E),
        (100, "gallos_dias_100", "El despertador de todo el barrio",
         "Pelea 100 días seguidos.", L),
    ])  # fmt: skip
    for key, achievement_id, name, description, rarity, secret in (
        (
            "temu",
            "gallos_beat_temu",
            "Devolución gratuita",
            "Gánale al Gallo de Temu. Tampoco tenía mucho mérito.",
            C,
            False,
        ),
        ("teror", "gallos_beat_teror", "Bajada del Pino", "Gánale al Pollo de Teror.", C, False),
        (
            "directiva",
            "gallos_beat_directiva",
            "Recurso estimado",
            "Gánale a la Directiva 2019/904. Bruselas lo apela.",
            C,
            False,
        ),
        (
            "falcon",
            "gallos_beat_falcon",
            "Moción de censura con espolones",
            "Gánale al Gallo del Falcon. Y aun así no dimite.",
            C,
            False,
        ),
        (
            "motosierra",
            "gallos_beat_motosierra",
            "¡Afuera tú!",
            "Gánale a La Motosierra.",
            C,
            False,
        ),
        (
            "sahel",
            "gallos_beat_sahel",
            "Contragolpe",
            "Gánale al Coronel del Sahel. Promete elecciones para la revancha.",
            C,
            False,
        ),
        (
            "arancel",
            "gallos_beat_arancel",
            "Liberation Day",
            "Gánale a Arancel. Mañana te pone un 300 %.",
            C,
            False,
        ),
        (
            "credito",
            "gallos_beat_credito",
            "Crédito social: −1.000",
            "Gánale a Crédito Social. Ya no te dejan coger el tren.",
            C,
            False,
        ),
        (
            "cupula",
            "gallos_beat_cupula",
            "Se coló por la Cúpula",
            "Gánale a Cúpula de Hierro.",
            C,
            False,
        ),
        (
            "actas",
            "gallos_beat_actas",
            "Ahora sí aparecen las actas",
            "Gánale al Gallo de las Actas. Él dice que ganó.",
            R,
            False,
        ),
        (
            "marte",
            "gallos_beat_marte",
            "Cohete que no despega",
            "Gánale al Gallo de Marte. Ya lo está anunciando en X como una victoria.",
            R,
            False,
        ),
    ):
        a += _tiers("gallos", f"{GALLOS_BEAT_PREFIX}{key}", [
            (1, achievement_id, name, description, rarity, secret),
        ])  # fmt: skip
    a.append(
        Achievement(
            id="gallos_gira_mundial",
            name="Gira mundial",
            description="Gánale a todos los gallos de la casa.",
            category="gallos",
            rarity=E,
            conditions=tuple((f"{GALLOS_BEAT_PREFIX}{r.key}", 1) for r in GALLOS_HOUSE),
        )
    )
    a += _tiers("gallos", "gallos_temu_loss", [
        (1, "gallos_pieza_recambio", "Pieza de recambio",
         "Pierde contra el Gallo de Temu. Llegó en tres cajas y aun así.", C, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_favourite_loss", [
        (1, "gallos_cis", "Encuesta fallida",
         "Pierde siendo el gran favorito (un 80 % o más). El CIS lo daba por hecho.", C, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_win_max", [
        (10_000, "gallos_bolsa", "La bolsa de la pelea", "Gana 10.000 Y$ en una pelea.", R),
        (100_000, "gallos_velada", "Velada del año", "Gana 100.000 Y$ en una pelea.", E),
        (1_000_000, "gallos_millonario", "Combate del siglo",
         "Gana 1.000.000 Y$ en una pelea. Ni Las Vegas.", L),
    ], unit="money")  # fmt: skip
    a += _tiers("gallos", "gallos_duels", [
        (1, "gallos_duelo_1", "Duelo al amanecer", "Pelea contra el gallo de otra persona.", C),
        (10, "gallos_duelo_10", "Pique de barrio", "Pelea 10 duelos contra otras personas.", C),
        (100, "gallos_duelo_100", "Rivalidad histórica", "Pelea 100 duelos.", R),
        (500, "gallos_duelo_500", "El clásico del gallinero", "Pelea 500 duelos.", E),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_duel_wins", [
        (1, "gallos_cuarenta", "Le has cantado las cuarenta", "Gana un duelo.", C),
        (25, "gallos_terror", "El terror del barrio", "Gana 25 duelos.", R),
        (250, "gallos_maton", "Matón del gallinero", "Gana 250 duelos.", E),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_duel_big", [
        (1, "gallos_ppv", "Pago por visión",
         "Pelea un duelo de 10.000 Y$ o más por cabeza.", R),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_crowd_max", [
        (1_000, "gallos_publico", "Con público",
         "Que el público apueste 1.000 Y$ en uno de tus duelos.", C),
        (10_000, "gallos_lleno", "Lleno en el reñidero",
         "Que el público apueste 10.000 Y$ en uno de tus duelos.", R),
        (100_000, "gallos_pantalla", "Pantalla gigante en Sol",
         "Que el público apueste 100.000 Y$ en uno de tus duelos.", E),
    ], unit="money")  # fmt: skip
    a += _tiers("gallos", "gallos_bets", [
        (1, "gallos_grada_1", "Apuesta de grada", "Apuesta en el duelo de otros.", C),
        (10, "gallos_grada_10", "Abonado de grada", "Apuesta en 10 duelos de otros.", C),
        (100, "gallos_grada_100", "Gallero de sofá", "Apuesta en 100 duelos de otros.", R),
        (1_000, "gallos_grada_1k", "Palco VIP", "Apuesta en 1.000 duelos de otros.", E),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_bets_won", [
        (1, "gallos_ojo", "Ojo de gallero", "Acierta una apuesta de grada.", C),
        (50, "gallos_tipster", "Tipster del reñidero", "Acierta 50 apuestas de grada.", R),
        (500, "gallos_vidente", "Vidente con espolones", "Acierta 500 apuestas de grada.", E),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_bet_odds_max", [
        (300, "gallos_contra_el_pueblo", "Contra el pueblo",
         "Acierta una apuesta de grada que pagaba ×3 o más.", C),
        (1_000, "gallos_tapado", "Todo al tapado",
         "Acierta una apuesta de grada que pagaba ×10 o más.", R),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_bet_refund", [
        (1, "gallos_porra_desierta", "Porra desierta",
         "Apuesta en un duelo en el que nadie apuesta por el otro gallo. "
         "Te lo devuelven, qué remedio.", C, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_challenges", [
        (1, "gallos_te_reto", "Te reto", "Reta a alguien a una pelea de gallos.", C),
        (50, "gallos_buscabroncas", "Buscabroncas", "Reta a 50 personas.", R),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_rejects", [
        (1, "gallos_gallina", "Co-co-co", "Rechaza un reto de pelea de gallos.", C, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_rejected", [
        (1, "gallos_cobra", "Te han hecho la cobra",
         "Que alguien rechace tu reto. Como Bisbal en la tele.", C, True),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_renamed", [
        (1, "gallos_bautizo", "Bautizo en el corral", "Ponle nombre a tu gallo.", C),
    ])  # fmt: skip
    a += _tiers("gallos", "gallos_rake", [
        (1_000, "gallos_comision", "El reñidero también cobra",
         "Deja 1.000 Y$ de comisión en duelos y apuestas de grada.", C),
        (100_000, "gallos_comisionista", "Comisionista del reñidero",
         "Deja 100.000 Y$ de comisión en el reñidero.", E, True),
    ], unit="money")  # fmt: skip
    a += _tiers("gallos", "gallos_paid_skin_win", [
        (1, "gallos_pasarela", "Gallo de pasarela", "Gana una pelea con un gallo de la tienda.", C),
    ])  # fmt: skip
    a.append(
        Achievement(
            id="gallos_vestuario",
            name="Prueba de vestuario",
            description="Pelea con el gallo de serie y con cada uno de los gratis.",
            category="gallos",
            rarity=C,
            conditions=tuple(
                (f"{GALLOS_SKIN_PREFIX}{key}", 1) for key in ("serie", *GALLOS_FREE_SKINS)
            ),
        )
    )
    a += _tiers("gallos", "gallos_stake_155", [
        (1, "gallos_155", "Aplicar el 155", "Apuesta exactamente 155 Y$ en una pelea.", C, True),
    ])  # fmt: skip
    for stat, achievement_id, name, description, secret in (
        (
            "gallos_hispanidad",
            "gallos_hispanidad",
            "Pelea de la Hispanidad",
            "Pelea un 12 de octubre.",
            False,
        ),
        (
            "gallos_canarias",
            "gallos_dia_canarias",
            "Gallo del Día de Canarias",
            "Pelea un 30 de mayo.",
            False,
        ),
        (
            "gallos_july4",
            "gallos_july4",
            "Independence Day",
            "Pelea un 4 de julio. God bless America y sus aranceles.",
            False,
        ),
        (
            "gallos_china_day",
            "gallos_china",
            "Fiesta Nacional china",
            "Pelea un 1 de octubre. El Partido te observa con orgullo.",
            True,
        ),
        (
            "gallos_europe_day",
            "gallos_europa",
            "Día de Europa",
            "Pelea un 9 de mayo, con el tapón bien pegado.",
            False,
        ),
        ("gallos_africa_day", "gallos_africa", "Día de África", "Pelea un 25 de mayo.", False),
        (
            "gallos_night",
            "gallos_deshoras",
            "Kikirikí a deshoras",
            "Pelea de madrugada (de 2 a 6).",
            True,
        ),
        (
            "gallos_dawn",
            "gallos_amanecer",
            "Al amanecer, como Dios manda",
            "Pelea entre las 6 y las 8 de la mañana.",
            False,
        ),
        ("gallos_nochevieja", "gallos_uvas", "Uvas y espolones", "Pelea en Nochevieja.", True),
        (
            "gallos_friday13",
            "gallos_viernes13",
            "Gallo negro, viernes 13",
            "Pierde una pelea un viernes 13.",
            True,
        ),
    ):
        a += _tiers("gallos", stat, [(1, achievement_id, name, description, C, secret)])

    # 🛵 Reparto ------------------------------------------------------------------------
    a += _tiers("reparto", "work_orders", [
        (1, "primer_pedido", "Su pedido está en camino", "Elige bien tu primer pedido.", C),
        (100, "pedidos_100", "Cuentas de cabeza", "Elige bien 100 pedidos.", R),
    ])  # fmt: skip
    a += _tiers("reparto", "work_routes", [
        (50, "rutas_50", "GPS humano", "Haz 50 rutas de entregas perfectas.", R),
    ])  # fmt: skip
    a += _tiers("reparto", "work_fleet", [
        (30, "flota_30", "Ni una papa menos", "Pilla a 30 riders comiéndose el pedido.", R),
    ])  # fmt: skip
    a += _tiers("reparto", "work_founder", [
        (30, "colaboradores", "Colaboradores, no trabajadores",
         "Sal airoso de 30 preguntas como fundador de la app.", E),
    ])  # fmt: skip
    a += _tiers("reparto", "work_rain", [
        (1, "mojado", "Mojado y contento", "Reparte con lluvia.", C),
        (25, "lluvia_25", "Rider anfibio", "Reparte 25 turnos con lluvia.", E),
    ])  # fmt: skip
    a += _tiers("reparto", "work_calima", [
        (1, "calima_reparto", "Repartiendo polvo del Sáhara", "Reparte con calima.", C),
    ])  # fmt: skip
    a += _tiers("reparto", "work_rain_fall", [
        (1, "charco", "El charco traidor", "Cáete con la moto en un día de lluvia.", R, True),
    ])  # fmt: skip
    a += _tiers("reparto", "work_app_blocked", [
        (1, "desconectado", "Desconectado por el algoritmo",
         "Que la app te desconecte por mala valoración.", C, True),
    ])  # fmt: skip
    a += _tiers("reparto", "work_five_stars", [
        (1, "cinco_estrellas_app", "Rider de cinco estrellas",
         "Llega a 5,0 de valoración en la app.", R),
    ])  # fmt: skip
    a.append(Achievement(
        id="tarifa_plana", name="Tarifa plana",
        description="Paga la cuota de autónomos con la tarifa plana.", category="reparto",
        rarity=C, conditions=(("work_flat_fee", 1),), story=FLAT_FEE_STORY,
    ))  # fmt: skip
    a += _tiers("reparto", "work_sixth_floor", [
        (1, "sexto_sin_ascensor", "Sexto sin ascensor", "Sube un pedido a un 6.º a pie.", C),
    ])  # fmt: skip
    a += _tiers("reparto", "work_storm", [
        (1, "tres_gotas", "Cuatro gotas en Las Palmas", "Sal a repartir con el diluvio.", C),
    ])  # fmt: skip
    a += _tiers("reparto", "work_dog_chase", [
        (1, "no_hace_nada", "«¡No hace nada!»", "Huye de un perro en una entrega.", C, True),
    ])  # fmt: skip
    a += _tiers("reparto", "work_dog_bribe", [
        (1, "soborno_canino", "Soborno canino", "Compra al perro con una papa del pedido.",
         C, True),
    ])  # fmt: skip
    a += _tiers("reparto", "work_rider_strike", [
        (1, "desconexion_masiva", "Desconexión masiva", "Súmate a la huelga de riders.",
         R, True),
    ])  # fmt: skip
    a += _tiers("reparto", "work_rider_scab", [
        (1, "media_ciudad", "Media ciudad para mí solo", "Reparte durante la huelga de riders.",
         R, True),
    ])  # fmt: skip
    a += _tiers("reparto", "work_ice_cube", [
        (1, "un_hielo", "Un hielo, por favor", "Lleva un cubito de hielo a domicilio.", R, True),
    ])  # fmt: skip
    a += _tiers("reparto", "work_top_reparto", [
        (1, "fundador_app", "Unicornio del reparto", "Llega a fundador de la app de reparto.", L),
    ])  # fmt: skip
    a += _tiers("reparto", "work_orders", [
        (1_000, "pedidos_1k", "Algoritmo con patas", "Elige bien 1.000 pedidos.", E),
    ])  # fmt: skip

    # 🗂️ Funcionariado ----------------------------------------------------------------------
    a += _tiers("funcionariado", "work_study", [
        (1, "codos", "Hincar codos", "Estudia la oposición por primera vez.", C),
        (10, "biblioteca", "Opositor de biblioteca", "Estudia 10 turnos.", R),
        (50, "opositor_pro", "Opositor profesional", "Estudia 50 turnos.", E),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_temario", [
        (100, "temario_100", "Me sé la Constitución", "Acierta 100 preguntas del temario.", R),
        (500, "temario_500", "Temario cantado", "Acierta 500 preguntas del temario.", E),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_ventanilla", [
        (50, "ventanilla_unica", "Ventanilla única",
         "Manda a 50 vecinos al negociado correcto.", R),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_registro", [
        (50, "sello_entrada", "Sello de entrada", "Registra bien 50 papeles.", R),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_stamps", [
        (50, "circuito_firmas", "Circuito de firmas", "Haz 50 circuitos de firmas perfectos.", R),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_citizens", [
        (30, "vuelva_manana", "Vuelva usted mañana (no)", "Atiende bien a 30 ciudadanos.", R),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_frauds", [
        (30, "olfato_fiscal", "Olfato fiscal", "Destapa 30 declaraciones fraudulentas.", E),
        (200, "perro_te_quiere", "Perro Sanxe te quiere",
         "Destapa 200 declaraciones fraudulentas.", L),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_triennia_max", [
        (1, "primer_trienio", "Primer trienio", "Cumple un trienio en el funcionariado.", R),
        (4, "cuatro_trienios", "Funcionario de toda la vida", "Cumple 4 trienios.", L),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_breakfast", [
        (1, "desayuno_sagrado", "Desayuno sagrado", "Vete a desayunar con cola en ventanilla.",
         C),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_lost_file", [
        (1, "expediente_2011", "Expediente de 2011", "Tramita un expediente olvidado.", C),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_hidden_file", [
        (1, "ojos_no_ven", "Ojos que no ven", "Vuelve a esconder un expediente olvidado.",
         R, True),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_caught_defensor", [
        (1, "diputado_comun", "Queja al Diputado del Común",
         "Que te pillen escondiendo un expediente.", E, True),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_no_system", [
        (1, "sin_sistema", "Se ha caído el sistema", "Cuelga el cartel de «sin sistema».",
         C, True),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_favor", [
        (1, "con_carino", "Mirar con cariño", "Adelanta el expediente del sobrino del concejal.",
         R, True),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_by_the_book", [
        (1, "orden_entrada", "Por orden de entrada", "Dile que no a un concejal.", C),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_moscosos", [
        (1, "moscosos", "Moscosos", "Gasta tus días de asuntos propios.", C),
    ])  # fmt: skip
    a += _tiers("funcionariado", "work_top_funcionariado", [
        (1, "inspector_hacienda", "Inspector de Hacienda", "Llega a Inspector de Hacienda.", L),
    ])  # fmt: skip

    # 🤳 Influencer ------------------------------------------------------------------------
    a += _tiers("influencer", "work_dances", [
        (1, "primer_trend", "Primer trend", "Clava tu primera coreografía.", C),
        (30, "coreografo", "Coreógrafo del barrio", "Clava 30 coreografías.", R),
    ])  # fmt: skip
    a += _tiers("influencer", "work_metrics", [
        (50, "engagement", "Engagement", "Acierta 50 métricas.", R),
    ])  # fmt: skip
    a += _tiers("influencer", "work_stream", [
        (30, "directo_limpio", "Directo sin incidentes", "Sobrevive a 30 momentos del directo.",
         R),
    ])  # fmt: skip
    a += _tiers("influencer", "work_collabs", [
        (30, "con_verguenza", "Con vergüenza", "Rechaza 30 colaboraciones turbias.", E),
    ])  # fmt: skip
    a += _tiers("influencer", "work_tv", [
        (30, "ni_confirmo", "Ni confirmo ni desmiento", "Sal airoso de 30 preguntas en plató.",
         E),
    ])  # fmt: skip
    a += _tiers("influencer", "work_viral", [
        (1, "viral", "Me he hecho viral", "Hazte viral en un turno.", R),
        (10, "viral_pala_10", "El algoritmo te quiere", "Hazte viral 10 veces.", E),
    ])  # fmt: skip
    a += _tiers("influencer", "work_megaviral", [
        (1, "megaviral", "Megaviral", "Multiplica tu sueldo por 15 en un turno.", L, True),
    ])  # fmt: skip
    a += _tiers("influencer", "work_flop", [
        (1, "algoritmo_odia", "El algoritmo me odia", "Que un turno te lo vea solo tu madre.",
         C, True),
    ])  # fmt: skip
    a += _tiers("influencer", "work_followers_max", [
        (10_000, "seguidores_10k", "Diez mil seguidores (la mitad, bots rusos)",
         "Llega a 10.000 seguidores.", R),
        (100_000, "seguidores_100k", "Cien mil seguidores, más votos que Sumar en Galicia",
         "Llega a 100.000 seguidores.", E),
        (1_000_000, "seguidores_1m", "Un millón de seguidores, como Milei en TikTok",
         "Llega a 1.000.000 de seguidores.", L),
    ])  # fmt: skip
    a += _tiers("influencer", "work_apology", [
        (1, "sudadera_gris", "Sudadera gris", "Pide perdón en vídeo tras una funa.", C),
    ])  # fmt: skip
    a += _tiers("influencer", "work_cancelled", [
        (1, "funado", "Funado", "Dobla la apuesta en plena funa.", R, True),
    ])  # fmt: skip
    a += _tiers("influencer", "work_andorra", [
        (1, "por_el_clima", "Por el clima", "Múdate a Andorra (una semana).", R, True),
    ])  # fmt: skip
    a += _tiers("influencer", "work_andorra_no", [
        (1, "yo_pago_aqui", "Yo pago aquí", "Rechaza irte a Andorra.", C),
    ])  # fmt: skip
    a += _tiers("influencer", "work_ad_tagged", [
        (1, "hashtag_publi", "#publi", "Avisa de que es publicidad.", C),
    ])  # fmt: skip
    a += _tiers("influencer", "work_hidden_ad", [
        (1, "publi_encubierta", "Me encanta de verdad", "Cuela publicidad encubierta.",
         R, True),
    ])  # fmt: skip
    a += _tiers("influencer", "work_caught_consumo", [
        (1, "expedientado_consumo", "Expedientado por Consumo",
         "Que te pillen la publicidad encubierta.", E, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="renta_especie", name="Renta en especie",
        description="Declara un regalo de una marca.", category="influencer", rarity=R,
        conditions=(("work_gift_declared", 1),), story=GIFT_STORY,
    ))  # fmt: skip
    a += _tiers("influencer", "work_gift_hidden", [
        (1, "unboxing_calladito", "Unboxing calladito", "No declares el regalo de una marca.",
         R, True),
    ])  # fmt: skip
    a += _tiers("influencer", "work_caught_regalos", [
        (1, "hacienda_cruza", "Hacienda cruza datos", "Que te pillen el regalo sin declarar.",
         E, True),
    ])  # fmt: skip
    a += _tiers("influencer", "work_collab", [
        (1, "colab", "Colab", "Haz un vídeo con otro influencer.", C),
    ])  # fmt: skip
    a += _tiers("influencer", "work_top_influencer", [
        (1, "celebrity", "Celebrity", "Llega a celebrity de reality.", L),
    ])  # fmt: skip

    # 🪏 Carrera larga: deseo del día, ramas, jubilación y pensión --------------------------
    a += _tiers("work", "work_wishes", [
        (1, "deseo_1", "Pide un deseo", "Cumple tu primer deseo del día.", C),
        (10, "deseo_10", "Hada madrina sindical", "Cumple 10 deseos del día.", R),
        (50, "deseo_50", "Frotando la lámpara", "Cumple 50 deseos del día.", E),
        (200, "deseo_200", "Los Reyes Magos te tienen en nómina",
         "Cumple 200 deseos del día.", L),
    ])  # fmt: skip
    a += _tiers("work", "work_wish_streak_max", [
        (7, "deseo_racha_7", "Siete deseos, siete días", "Cumple el deseo 7 días seguidos.", R),
        (30, "deseo_racha_30", "Un mes de estrellas fugaces",
         "Cumple el deseo 30 días seguidos.", E),
        (100, "deseo_racha_100", "Cien días pidiendo", "Cumple el deseo 100 días seguidos.", L),
        (365, "deseo_racha_365", "Un año de deseos", "Cumple el deseo 365 días seguidos.", M),
    ])  # fmt: skip
    a.append(Achievement(
        id="deseos_todos", name="Lo he deseado todo",
        description="Cumple cada uno de los deseos que salen en cualquier curro.",
        category="work", rarity=E,
        conditions=tuple((f"{WISH_STAT_PREFIX}{key}", 1) for key in GENERIC_WISHES),
    ))  # fmt: skip
    a += _tiers("work", "work_wish_perfecto", [
        (5, "deseo_perfecto_5", "Un 100 por encargo", "Cumple 5 veces el deseo de sacar un 100.",
         E, True),
    ])  # fmt: skip
    a += _tiers("work", "work_wish_nocturno", [
        (10, "deseo_nocturno_10", "Lechuza sindical",
         "Cumple 10 veces el deseo de fichar de noche.", E, True),
    ])  # fmt: skip
    a.append(Achievement(
        id="rama_1", name="Encrucijada", description="Elige rama en un oficio.",
        category="work", rarity=R, conditions=(("work_branch_chosen", 1),), story=BRANCH_STORY,
    ))  # fmt: skip
    a += _tiers("work", "work_branch_chosen", [
        (5, "rama_5", "Indeciso con carrera", "Elige rama 5 veces.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_retired", [
        (1, "jubilado_1", "Jubilación anticipada (a los dos meses)",
         "Jubílate de una rama.", E),
        (3, "jubilado_3", "Pensionista profesional", "Jubílate de 3 ramas.", L),
        (8, "jubilado_8", "El sistema de pensiones no aguanta", "Jubílate de 8 ramas.", M),
    ])  # fmt: skip
    a += _tiers("work", "work_pensions", [
        (1, "pension_1", "Pensión cobrada", "Cobra tu pensión por primera vez.", E),
        (30, "pension_30", "Pensión vitalicia", "Cobra la pensión 30 días.", L),
        (365, "pension_365", "Más años cobrando que cotizando",
         "Cobra la pensión 365 días.", M),
    ])  # fmt: skip
    a.append(Achievement(
        id="pension_irpf", name="Hacienda no se jubila",
        description="Paga IRPF por tu pensión.", category="work", rarity=E,
        conditions=(("work_pension_irpf", 1),), story=PENSION_STORY,
    ))  # fmt: skip

    # 🌹 Política: las ramas Moncloa y Bruselas -------------------------------------------
    a += _tiers("jobs", "work_top_politica_bruselas", [
        (1, "comisario", "Bruselas me espera",
         "Llega a comisario europeo (rama 🇪🇺 Bruselas).", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_retired_politica", [
        (2, "doble_militancia", "Doble militancia",
         "Jubílate de la Moncloa y de Bruselas.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_spokes", [
        (25, "portavoz_25", "Lo que diga el argumentario",
         "Contesta 25 preguntas como portavoz sin contestar nada.", E),
        (250, "portavoz_250", "Máquina de titulares vacíos",
         "Contesta 250 preguntas como portavoz.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_decrees", [
        (30, "decretos_30", "Al BOE", "Despacha 30 papeles como secretario de Estado.", E),
        (300, "decretos_300", "Gobernar por decreto, como Trump con el rotulador",
         "Despacha 300 papeles.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_ministry", [
        (25, "ministerio_25", "Ministro de lo suyo", "Sal airoso de 25 marrones del ministerio.",
         E),
        (250, "ministerio_250", "Superministro de veintidós ministerios",
         "Sal airoso de 250 marrones del ministerio.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_partners", [
        (15, "socios_15", "Agenda de socios con prefijo de Bélgica",
         "Recuerda 15 veces lo que le prometiste a cada socio.", E),
        (150, "socios_150", "Geometría variable, con siete socios",
         "Recuerda 150 veces lo que le prometiste a cada socio.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_control", [
        (30, "control_30", "Y tú más", "Sobrevive a 30 preguntas de la sesión de control.", E),
        (300, "control_300", "Los miércoles son míos",
         "Sobrevive a 300 preguntas de la sesión de control.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_plenary", [
        (15, "plenos_15", "Botón verde, botón rojo", "Vota bien 15 tandas del pleno europeo.", E),
        (150, "plenos_150", "Eurodiputado de manual", "Vota bien 150 tandas del pleno.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_amendments", [
        (30, "enmiendas_30", "Enmienda a la totalidad", "Manda 30 enmiendas a su sitio.", E),
        (300, "enmiendas_300", "Ponente estrella", "Manda 300 enmiendas a su sitio.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_lobbies", [
        (25, "lobbies_25", "Puertas abiertas (al lobby)", "Torea a 25 lobistas.", E),
        (250, "lobbies_250", "Registro de transparencia", "Torea a 250 lobistas.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_trilogues", [
        (30, "trilogo_30", "Trílogo a las cuatro de la mañana", "Cierra 30 puntos de un trílogo.",
         E),
        (300, "trilogo_300", "Acuerdo provisional", "Cierra 300 puntos de un trílogo.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_commission", [
        (20, "comision_20", "Multa a la red social de Musk",
         "Acierta 20 respuestas como comisario europeo.", L),
        (200, "comision_200", "Bruselas manda, como el tapón pegado a la botella",
         "Acierta 200 respuestas como comisario.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_censure_survived", [
        (1, "mocion", "Manual de resistencia, capítulo uno", "Aguanta una moción de censura.", E),
        (3, "resistencia", "Resistiré (en el sillón)", "Aguanta 3 mociones de censura.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_snap_election", [
        (1, "adelanto", "Elecciones en pleno julio", "Convoca elecciones por sorpresa.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_partner_deal", [
        (1, "socio_cede", "Socio preferente", "Cede ante un socio a cambio de sus votos.",
         E, True),
        (10, "frankenstein", "Gobierno Frankenstein", "Cede 10 veces ante los socios.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_partner_refused", [
        (1, "plantado", "Ni un paso atrás", "Planta a un socio que te chantajea.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_cis", [
        (1, "cis", "La cocina de Tezanos", "Cocina el CIS.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_omnibus", [
        (1, "omnibus", "Decreto ómnibus, con indulto de regalo",
         "Mételo todo en el mismo decreto.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_suitcase", [
        (1, "maletin", "Maletín del Golfo", "Acepta un maletín en Bruselas.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_suitcase_refused", [
        (1, "maletin_no", "Manos limpias en Bruselas", "Rechaza un maletín.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_caught_olaf", [
        (1, "olaf", "La OLAF toca el timbre",
         "Que te pille la Oficina Europea de Lucha contra el Fraude.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_strasbourg", [
        (1, "estrasburgo", "Circo itinerante", "Múdate a Estrasburgo para el pleno.", E),
        (10, "estrasburgo_10", "Abono Bruselas-Estrasburgo", "Haz 10 traslados a Estrasburgo.",
         L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_per_diem", [
        (1, "dietas", "Firma y vete", "Cobra las dietas de un pleno al que no vas.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_lobby_dinner", [
        (1, "cena_lobby", "Cena de trabajo", "Cena con un lobby (paga él).", E, True),
    ])  # fmt: skip

    # 🌹 Política: lo que faltaba (auditoría de octubre de 2026) --------------------------
    a += _tiers("jobs", "work_clean_tender", [
        (1, "concurso_limpio", "Concurso limpio, como en Suiza",
         "Haz un concurso público de verdad.", C, True),
        (10, "concurso_limpio_10", "Licitador ejemplar (y aburrido)",
         "Haz 10 concursos públicos de verdad.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_cronyism_refused", [
        (1, "sin_plaza", "Desterrado a Fuerteventura", "Niégate a colocar a un sobrino.", C, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_falcon_refused", [
        (1, "guagua", "En guagua, como el pueblo", "Deja el avión oficial y ve en guagua.",
         R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_cis_honest", [
        (1, "cis_limpio", "Encuesta sin cocinar (insólito)", "Publica el CIS tal cual sale.",
         E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_constitution", [
        (1, "constitucion", "Constitucionalista de guardia",
         "Ficha en la política el 6 de diciembre, Día de la Constitución.", R),
    ])  # fmt: skip
    a += _tiers("jobs", "work_fango", [
        (1, "fango_bulo", "Víctima oficial del fango", "Denuncia la máquina del fango por un bulo.",
         R, True),
        (10, "fango_10", "Ingeniero jefe del fango", "Denuncia la máquina del fango 10 veces.",
         E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_pegasus", [
        (1, "pegasus", "Móvil en la nevera", "Cambia de móvil por miedo a Pegasus.", R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_pegasus_listened", [
        (1, "pegasus_escucha", "Que escuchen, que aprendan", "Deja que te espíen con Pegasus.",
         R, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_exhumation", [
        (1, "exhumacion", "Franco sigue muerto",
         "Organiza una exhumación con helicóptero antes de las elecciones.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_waterloo", [
        (1, "waterloo", "Peregrino a Waterloo", "Viaja a Waterloo a por siete votos.", E, True),
        (5, "waterloo_5", "Abono Madrid-Waterloo", "Viaja 5 veces a Waterloo.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_letter", [
        (1, "carta", "Querida ciudadanía",
         "Escribe una carta y desaparece cinco días a reflexionar.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_tariffs", [
        (1, "aranceles", "Aranceles espejo", "Contesta con firmeza a los aranceles de Trump.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_oval_office", [
        (1, "despacho_oval", "En espera con el Despacho Oval",
         "Llama a Trump y que te dejen en espera.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_cap", [
        (1, "tapon", "Tapón pegado hasta en el brick",
         "Vota que el tapón vaya pegado también a los bricks.", E, True),
    ])  # fmt: skip

    # 🦺 Obra: las ramas Ladrillo y Obra pública ------------------------------------------
    a += _tiers("jobs", "work_top_obra_ladrillo", [
        (1, "palco", "Del ladrillo al palco",
         "Llega a presidente de un club de fútbol (rama 🏖️ Ladrillo).", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_retired_obra", [
        (2, "ladrillo_y_boe", "Del pelotazo al BOE",
         "Jubílate del Ladrillo y de la Obra pública.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_no_invoice", [
        (1, "sin_factura", "¿Con factura o sin factura?", "Haz una reforma sin factura.", R, True),
        (10, "sin_factura_10", "Economía sumergida con casco", "Haz 10 reformas sin factura.",
         E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_flats", [
        (15, "pisos_15", "Piso piloto con vistas (de lado)",
         "Enseña 15 pisos piloto como los pidió el cliente.", E),
        (150, "pisos_150", "Burbuja 2.0", "Enseña 150 pisos piloto.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_land", [
        (30, "terrenos_30", "Recalificador de guardia", "Manda 30 terrenos a su sitio.", E),
        (300, "terrenos_300", "Ni un pinar en pie", "Manda 300 terrenos a su sitio.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_bank", [
        (25, "banco_25", "Refinanciado a treinta años", "Refinancia 25 deudas con el banco.", E),
        (250, "banco_250", "Rescate bancario a medida", "Refinancia 250 deudas.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_mayor", [
        (30, "alcalde_30", "Convenio urbanístico", "Cierra 30 convenios con el alcalde.", E),
        (300, "alcalde_300", "Marbella era esto", "Cierra 300 convenios con el alcalde.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_box", [
        (20, "palco_20", "De fútbol, lo justo", "Acierta 20 respuestas en el palco.", L),
        (200, "palco_200", "Presidente vitalicio (como Florentino)",
         "Acierta 200 respuestas en el palco.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_tenders", [
        (30, "licitaciones_30", "Baja temeraria", "Manda 30 concursos a su sitio.", E),
        (300, "licitaciones_300", "Rey de la UTE", "Manda 300 concursos a su sitio.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_concessions", [
        (15, "concesiones_15", "Radial rescatada", "Renueva 15 tandas de concesiones.", E),
        (150, "concesiones_150", "Peaje eterno", "Renueva 150 tandas de concesiones.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_ministry_deals", [
        (30, "modificados_30", "El modificado del modificado",
         "Saca 30 modificados en el ministerio.", E),
        (300, "modificados_300", "Presupuesto por tres", "Saca 300 modificados en el ministerio.",
         L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_abroad_deals", [
        (20, "internacional_20", "Marca España (con sobrecoste)",
         "Acierta 20 respuestas en los contratos por el mundo.", L),
        (200, "internacional_200", "Del canal de Panamá al palacio de Erdoğan",
         "Acierta 200 respuestas en los contratos por el mundo.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_vulture", [
        (1, "fondo_buitre", "Vendido al fondo buitre",
         "Vende trescientos pisos de golpe a un fondo.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_bubble_held", [
        (1, "burbuja", "El ladrillo nunca baja", "Aguanta los pisos cuando estalla la burbuja.",
         E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_rezoning", [
        (1, "recalificacion", "Pinar con vistas", "Acepta un «detalle» por recalificar un pinar.",
         E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_rezoning_refused", [
        (1, "recalificacion_no", "Licencia para 2041", "Pide la licencia por la vía legal.", E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_oligarch", [
        (1, "oligarca", "Áticos al contado", "Vende diez áticos en billetes de 500.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_caught_sepblac", [
        (1, "sepblac", "Blanqueo con vistas al mar", "Que te pille el Sepblac.", L, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_tourist_flats", [
        (1, "turisticos", "Caja de llaves en el portal",
         "Convierte un bloque entero en pisos turísticos.", E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_social_rent", [
        (1, "alquiler_barrio", "Casero de los de antes", "Alquila el bloque a gente del barrio.",
         E),
    ])  # fmt: skip
    a += _tiers("jobs", "work_change_order", [
        (1, "modificado", "Modificado al canto", "Pide un modificado con la obra a medias.", E),
        (10, "modificado_10", "El AVE más caro de Europa", "Pide 10 modificados.", L),
    ])  # fmt: skip
    a += _tiers("jobs", "work_ribbon", [
        (1, "cinta", "Inauguración hasta el kilómetro 3", "Inaugura una autovía sin terminar.",
         E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_ghost_airport", [
        (1, "aeropuerto", "Aeropuerto con estatua", "Construye un aeropuerto sin aviones.",
         E, True),
    ])  # fmt: skip
    a += _tiers("jobs", "work_wall", [
        (1, "muro", "Muro pagado por México (en teoría)",
         "Cobra por adelantado el muro de Trump.", E, True),
    ])  # fmt: skip

    # 🏥 Sanidad: las ramas Sanidad pública y Privada -------------------------------------
    a += _tiers("sanidad", "work_top_sanidad_publica", [
        (1, "san_consejero", "Consejero con mascarilla de oro",
         "Llega a consejero de Sanidad (rama 🏛️ Sanidad pública).", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_top_sanidad_privada", [
        (1, "san_aseguradora", "La salud es un negocio (el tuyo)",
         "Llega a dueño de una aseguradora de salud (rama 💉 Privada).", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_retired_sanidad", [
        (1, "san_jubilado", "Jubilado con el fonendo al cuello",
         "Jubílate de una rama de la sanidad.", L),
        (2, "san_marea_y_poliza", "De la marea blanca a la póliza de oro",
         "Jubílate de la Sanidad pública y de la Privada.", L, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_surgeries", [
        (15, "san_quirofano_15", "Parte de quirófano sin tachones",
         "Cuadra 15 partes de quirófano como jefe de sección.", E),
        (150, "san_quirofano_150", "Quirófano a las ocho en punto (de verdad)",
         "Cuadra 150 partes de quirófano.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_referrals", [
        (30, "san_derivaciones_30", "Derivador oficial",
         "Manda 30 pacientes a su sitio como jefe de servicio.", E),
        (300, "san_derivaciones_300", "Cada paciente en su cajón",
         "Manda 300 pacientes a su sitio.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_medical_director", [
        (25, "san_direccion_25", "Bombero con bata",
         "Apaga 25 incendios como director médico.", E),
        (250, "san_direccion_250", "Estamos priorizando",
         "Apaga 250 incendios como director médico.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_hospital_manager", [
        (30, "san_gerencia_30", "Todo es un reto",
         "Acierta 30 respuestas con la Consejería como gerente.", E),
        (300, "san_gerencia_300", "Gerente a prueba de goteras",
         "Acierta 300 respuestas como gerente del hospital.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_health_minister", [
        (20, "san_consejeria_20", "La culpa es del anterior",
         "Acierta 20 respuestas en el pleno como consejero de Sanidad.", L),
        (200, "san_consejeria_200", "Herencia recibida",
         "Acierta 200 respuestas en el pleno como consejero de Sanidad.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_fine_print", [
        (25, "san_letra_25", "Lupa de mutua", "Encuentra 25 cláusulas de letra pequeña.", E),
        (250, "san_letra_250", "Cuerpo 4", "Encuentra 250 cláusulas de letra pequeña.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_touchups", [
        (15, "san_retoques_15", "Bisturí con filtro",
         "Haz 15 retoques tal como los pidió el cliente.", E),
        (150, "san_retoques_150", "Cara de cera, cuenta de oro", "Haz 150 retoques perfectos.",
         L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_clinic", [
        (25, "san_clinica_25", "El cliente siempre trae tarjeta",
         "Atiende a 25 clientes VIP en tu clínica.", E),
        (250, "san_clinica_250", "Barista en la sala de espera",
         "Atiende a 250 clientes VIP en tu clínica.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_health_deals", [
        (30, "san_conciertos_30", "Colaboración público-privada",
         "Cierra 30 conciertos con la Comunidad.", E),
        (300, "san_conciertos_300", "Quirófanos libres (qué casualidad)",
         "Cierra 300 conciertos con la Comunidad.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_insurer", [
        (20, "san_poliza_20", "Prima, no siniestro",
         "Acierta 20 respuestas en la junta de accionistas de tu aseguradora.", L),
        (200, "san_poliza_200", "No me consta, no me consta",
         "Acierta 200 respuestas en la junta de accionistas.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_one_day_contract", [
        (1, "san_contrato_dia", "Contrato de un día (renovable)",
         "Acepta un contrato de un día.", C),
        (10, "san_contrato_dia_10", "Once contratos al mes", "Acepta 10 contratos de un día.", R),
        (50, "san_contrato_dia_50", "Precariedad con bata", "Acepta 50 contratos de un día.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_or_dance", [
        (1, "san_baile", "Coreografía con mascarilla", "Graba un baile en el quirófano.",
         R, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_guard_bed", [
        (1, "san_cama_1987", "La cama de 1987", "Duerme en la cama de la sala de guardias.", C),
        (25, "san_cama_25", "Hueles a guardia", "Duerme 25 veces en la cama de guardias.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_guard_nye", [
        (1, "san_uvas", "Las uvas en el control de enfermería", "Haz una guardia en Nochevieja.",
         R),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_ward_opened", [
        (1, "san_planta", "La planta cerrada «por obras»",
         "Abre la planta cerrada para vaciar el pasillo de Urgencias.", E),
        (10, "san_planta_10", "Las obras eran una cinta de plástico",
         "Abre la planta cerrada 10 veces.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_hallway_beds", [
        (1, "san_puntual", "Situación puntual",
         "Llama «situación puntual» al colapso de Urgencias.", E, True),
        (10, "san_puntual_10", "Puntual todo el año",
         "Saca 10 comunicados de «situación puntual».", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_referred_private", [
        (1, "san_derivar", "Derivado a la privada", "Deriva 500 operaciones a la privada.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_afternoon_ops", [
        (1, "san_tardes", "Quirófanos por la tarde",
         "Abre tus quirófanos por la tarde para bajar la lista.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_masks", [
        (1, "san_mascarillas", "Mascarillas a precio de oro",
         "Compra mascarillas al intermediario del Rolls.", E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_masks_refused", [
        (1, "san_mascarillas_no", "Tres ofertas y una semana",
         "Compra las mascarillas por concurso público.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_empty_hospital", [
        (1, "san_hospital_vacio", "Inaugurado y cerrado «para ajustes»",
         "Inaugura un hospital sin personal antes de las elecciones.", E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_revolving_health", [
        (1, "san_giratoria", "Puerta giratoria con fonendo",
         "Firma con una aseguradora antes de dejar la Consejería.", L, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_revolving_refused", [
        (1, "san_incompatible", "Periodo de incompatibilidad",
         "Rechaza el sillón de la aseguradora.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_caught_incompatibilidades", [
        (1, "san_conflicto", "Conflicto de intereses",
         "Que te pille la Oficina de Conflictos de Intereses.", L, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_claim_denied", [
        (1, "san_clausula", "Cláusula 47.3.b", "Deniega una prueba por la letra pequeña.",
         E, True),
        (10, "san_clausula_10", "El jamón de la aseguradora",
         "Deniega 10 pruebas por la letra pequeña.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_claim_paid", [
        (1, "san_cubierta", "Antes paciente que cliente",
         "Cubre la prueba aunque la póliza no quiera.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_caught_abusivas", [
        (1, "san_abusiva", "Cláusula abusiva", "Que un juzgado te anule una cláusula.", E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_canje", [
        (1, "san_canje", "Pagado en visibilidad",
         "Haz un retoque gratis a cambio de stories.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_canje_refused", [
        (1, "san_con_factura", "La visibilidad no paga el alquiler",
         "Cóbrale el retoque a la influencer.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_turkey_fix", [
        (1, "san_turquia", "Arreglos de Estambul",
         "Arregla los injertos capilares de medio pueblo.", E),
        (10, "san_turquia_10", "Erdoğan, tu mejor comercial",
         "Arregla injertos de Turquía 10 veces.", L),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_ohio_rate", [
        (1, "san_ohio", "Tarifa de Ohio",
         "Cóbrale a un turista de EE. UU. la «tarifa internacional».", E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_fair_rate", [
        (1, "san_se_muda", "Se muda a Canarias",
         "Cóbrale lo normal a un turista de EE. UU.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_concert_envelope", [
        (1, "san_sobre", "Concierto con sobre", "Renueva el concierto «con un detalle».",
         E, True),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_concert_clean", [
        (1, "san_concurso", "Ganado sin sobre", "Gana el concierto por concurso.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_health_fund_sale", [
        (1, "san_fondo", "Vendido al fondo, con plantilla incluida",
         "Vende tu grupo hospitalario a un fondo de inversión.", E),
    ])  # fmt: skip
    a += _tiers("sanidad", "work_staff_first", [
        (1, "san_plantilla", "Mi plantilla no se toca",
         "Rechaza al fondo para proteger a la plantilla.", E),
    ])  # fmt: skip
    a += [
        combo("san_hipocrates", "Juramento hipocrático de verdad",
              "Cubre la prueba, compra las mascarillas por concurso y rechaza el sillón de "
              "la aseguradora.", "sanidad", L,
              (("work_claim_paid", 1), ("work_masks_refused", 1),
               ("work_revolving_refused", 1))),
        combo("san_sin_escrupulos", "Sin escrúpulos y con bata",
              "Compra las mascarillas del Rolls, deniega una prueba por la letra pequeña y "
              "firma con la aseguradora.", "sanidad", L,
              (("work_masks", 1), ("work_claim_denied", 1), ("work_revolving_health", 1)),
              secret=True),
    ]  # fmt: skip

    # 👷 Los ocho oficios ------------------------------------------------------------------
    a += _tiers("jobs", "work_jobs_tried", [
        (8, "ocho_oficios", "Vida laboral de ocho páginas", "Trabaja en los 8 oficios.", E),
    ])  # fmt: skip
    a += _tiers("work", "work_jobs_top", [
        (8, "top_8", "Lo he sido todo", "Llega a lo más alto de los 8 oficios.", M),
    ])  # fmt: skip

    countable = sum(
        1 for x in a if x.category != "meta" and not CATEGORY_BY_KEY[x.category].upcoming
    )
    a += _tiers("meta", UNLOCKED_STAT, [
        (countable, "completionist", "Completista", "Desbloquea todos los logros.", M),
    ])  # fmt: skip
    metas = sum(1 for x in a if x.category == "meta")
    a += _tiers("meta", "achievements_meta", [
        (metas, "metacompletionist", "Logro de logros de logros",
         "Desbloquea todos los demás logros de Coleccionista. Ya no queda nada.", M, True),
    ])  # fmt: skip
    return tuple(a)


CATALOG: tuple[Achievement, ...] = _build_catalog()
BY_ID: dict[str, Achievement] = {a.id: a for a in CATALOG}
#: Logros que se pueden conseguir hoy (sin los de juegos que aún no existen).
AVAILABLE: tuple[Achievement, ...] = tuple(a for a in CATALOG if not a.upcoming)
_META = tuple(a for a in AVAILABLE if a.category == "meta")
_NORMAL = tuple(a for a in AVAILABLE if a.category != "meta")


# -- Evaluación ------------------------------------------------------------------------


def hong_kong_clock_stats(hong_kong: datetime, canary: datetime) -> dict[str, int]:
    """Contadores que suma mirar la hora de Hong Kong con `hongkong`.

    Args:
        hong_kong: Hora local de Hong Kong en ese momento.
        canary: Hora local de Canarias en ese mismo momento.
    """
    stats = {"hk_clock": 1}
    if hong_kong.date() > canary.date():
        stats["hk_clock_tomorrow"] = 1
    if 2 <= hong_kong.hour < 6:
        stats["hk_clock_sleeping"] = 1
    if 12 <= hong_kong.hour < 14:
        stats["hk_clock_lunch"] = 1
    if is_night(canary.hour):
        stats["hk_clock_tour"] = 1
    if (hong_kong.month, hong_kong.day, hong_kong.hour, hong_kong.minute) == (1, 1, 0, 0):
        stats["hk_clock_new_year"] = 1
    return stats


@dataclass(slots=True)
class StatDelta:
    """Cambios en las estadísticas de un miembro.

    Attributes:
        add: Cuánto sumar a cada contador.
        peak: Valor visto para cada máximo; se guarda si supera al anterior.
    """

    add: dict[str, int] = field(default_factory=dict)
    peak: dict[str, int] = field(default_factory=dict)

    def merge(self, other: StatDelta) -> None:
        """Acumula `other` en este cambio."""
        for stat, value in other.add.items():
            self.add[stat] = self.add.get(stat, 0) + value
        for stat, value in other.peak.items():
            self.peak[stat] = max(self.peak.get(stat, value), value)

    def __bool__(self) -> bool:
        return bool(self.add or self.peak)


def with_derived(stats: Mapping[str, int]) -> dict[str, int]:
    """Añade las estadísticas calculadas a partir de otras.

    `messages_total` suma los mensajes contados por los logros y los que ya
    tenía el miembro en el historial importado antes de que existieran.
    `img_effects_tried` cuenta los efectos de imagen distintos usados;
    `roulette_numbers_hit` y `roulette_hit_max`, los plenos por número;
    `shop_use_kinds`, los objetos distintos usados de la tienda;
    `shop_aisles`, los pasillos del colmado en los que ha comprado;
    `perfil_sections`, las secciones distintas de `perfil` que ha mirado, y
    `horse_backed_kinds` y `horse_backed_max`, los caballos distintos por los
    que ha apostado y los boletos al que más.
    """
    full = dict(stats)
    full[MESSAGES_TOTAL_STAT] = full.get("messages", 0) + full.get("messages_imported", 0)
    full[IMG_EFFECTS_STAT] = sum(
        1 for stat, value in stats.items() if stat.startswith(IMG_EFFECT_PREFIX) and value > 0
    )
    hits = [
        value
        for stat, value in stats.items()
        if stat.startswith(ROULETTE_HIT_PREFIX)
        and stat[len(ROULETTE_HIT_PREFIX) :].isdigit()
        and value > 0
    ]
    full[ROULETTE_NUMBERS_STAT] = len(hits)
    full[ROULETTE_FAVOURITE_STAT] = max(hits, default=0)
    full[SHOP_USE_KINDS_STAT] = sum(
        1
        for stat, value in stats.items()
        if stat.startswith(SHOP_USED_PREFIX)
        and stat[len(SHOP_USED_PREFIX) :] in SHOP_USES
        and value > 0
    )
    full[SHOP_AISLES_STAT] = len(visited_aisles(stats))
    full[PERFIL_SECTIONS_STAT] = sum(
        1 for key in PERFIL_SECTIONS if stats.get(f"{PERFIL_SEEN_PREFIX}{key}", 0) > 0
    )
    pets = {
        stat[len(PET_SPECIES_PREFIX) :]
        for stat, value in stats.items()
        if stat.startswith(PET_SPECIES_PREFIX) and value > 0
    }
    full[PET_SPECIES_STAT] = len(pets & _PET_KEYS)
    full[PET_SPAWN_KINDS_STAT] = len(pets & _PET_SPAWN_KEYS)
    backed = [
        value
        for stat, value in stats.items()
        if stat.startswith(HORSE_BACKED_PREFIX)
        and stat[len(HORSE_BACKED_PREFIX) :] in HORSE_KEYS
        and value > 0
    ]
    full[HORSE_BACKED_KINDS_STAT] = len(backed)
    full[HORSE_BACKED_MAX_STAT] = max(backed, default=0)
    return full


def visited_aisles(stats: Mapping[str, int]) -> set[str]:
    """Pasillos actuales del colmado en los que ha comprado el miembro.

    Las compras de un pasillo retirado cuentan para el que lo hereda
    (`RETIRED_AISLES`): quien compró en Ultramarinos ya conoce la España de
    siempre. Las de un pasillo repartido entre varios (la Farmacia) no
    cuentan para ninguno.
    """
    seen = set()
    for stat, value in stats.items():
        if not stat.startswith(SHOP_AISLE_PREFIX) or value <= 0:
            continue
        key = stat[len(SHOP_AISLE_PREFIX) :]
        key = SHOP_RETIRED_AISLES.get(key, key) or ""
        if key in _SHOP_AISLE_KEYS:
            seen.add(key)
    return seen


#: Logros normales de cada categoría, para saber cuándo se completa una.
_PER_CATEGORY: dict[str, frozenset[str]] = {}
for _achievement in _NORMAL:
    _PER_CATEGORY[_achievement.category] = _PER_CATEGORY.get(_achievement.category, frozenset()) | {
        _achievement.id
    }
_NORMAL_IDS = frozenset(a.id for a in _NORMAL)
_META_IDS = frozenset(a.id for a in _META)


def meta_stats(unlocked: Iterable[str], *, batch: int = 0) -> dict[str, int]:
    """Estadísticas virtuales de Coleccionista a partir de los logros conseguidos.

    Solo cuentan los logros normales (no los de Coleccionista), salvo
    `achievements_meta`, para que no se desbloqueen unos a otros sin fin.

    Args:
        unlocked: Ids conseguidos (los desconocidos se ignoran).
        batch: Logros normales que acaban de saltar a la vez (para «Combo»).
    """
    have = set(unlocked)
    normal = [BY_ID[i] for i in have & _NORMAL_IDS]
    by_rarity = {rarity: 0 for rarity in Rarity}
    for achievement in normal:
        by_rarity[achievement.rarity] += 1
    return {
        UNLOCKED_STAT: len(normal),
        "achievements_rare": by_rarity[Rarity.RARE],
        "achievements_epic": by_rarity[Rarity.EPIC],
        "achievements_legendary": by_rarity[Rarity.LEGENDARY],
        "achievements_mythic": by_rarity[Rarity.MYTHIC],
        "achievements_secret": sum(1 for a in normal if a.secret),
        "achievements_categories": sum(1 for ids in _PER_CATEGORY.values() if ids & have),
        "achievements_categories_done": sum(1 for ids in _PER_CATEGORY.values() if ids <= have),
        "achievement_points": sum(a.rarity.points for a in normal),
        "achievements_earned": sum(a.rarity.reward for a in normal),
        "achievements_batch": batch,
        "achievements_meta": len(have & _META_IDS),
    }


def _met(achievement: Achievement, stats: Mapping[str, int]) -> bool:
    return all(stats.get(stat, 0) >= goal for stat, goal in achievement.conditions)


def newly_unlocked(stats: Mapping[str, int], unlocked: Iterable[str]) -> list[str]:
    """Logros que se cumplen con `stats` y aún no estaban en `unlocked`.

    Primero los normales y después los de Coleccionista, contando ya los que
    se acaban de conseguir. Los de Coleccionista se repasan hasta que no salte
    ninguno más, porque el último («Logro de logros de logros») depende de los
    demás de su categoría.
    """
    have = set(unlocked)
    full = with_derived(stats)
    new = [a.id for a in _NORMAL if a.id not in have and _met(a, full)]
    batch = len(new)
    while True:
        full.update(meta_stats(have | set(new), batch=batch))
        found = [a.id for a in _META if a.id not in have and a.id not in new and _met(a, full)]
        if not found:
            return new
        new += found


def progress(achievement: Achievement, stats: Mapping[str, int]) -> tuple[int, int]:
    """`(actual, meta)` de la estadística principal, con el actual sin pasarse.

    Para los de Coleccionista, `stats` ya trae `meta_stats` (ver el cog).
    """
    full = with_derived(stats)
    return min(full.get(achievement.stat, 0), achievement.goal), achievement.goal


def points(achievement_ids: Iterable[str]) -> int:
    """Puntos de ranking de una colección de logros (ignora ids desconocidos)."""
    return sum(BY_ID[i].rarity.points for i in achievement_ids if i in BY_ID)


def total_reward(achievement_ids: Iterable[str]) -> int:
    """Yapdollars brutos que se pagan por estos logros."""
    return sum(BY_ID[i].rarity.reward for i in achievement_ids if i in BY_ID)


# -- Qué cuenta cada cosa --------------------------------------------------------------

#: Mensaje "largo" a partir de estos caracteres.
LONG_MESSAGE = 600
#: Mensaje "corto" hasta estos caracteres.
SHORT_MESSAGE = 3
#: Letras mínimas para que un mensaje en mayúsculas cuente como grito.
CAPS_MIN_LETTERS = 8
#: Emojis en un mismo mensaje para que cuente como jeroglífico.
EMOJI_HEAVY = 5
#: Menciones a personas distintas en un mensaje para que cuente como convocatoria.
MASS_PING = 5
#: Letras de una palabra para que cuente como palabro.
LONG_WORD = 20
#: Letras mínimas de un palíndromo (con al menos tres letras distintas).
PALINDROME_MIN = 9

_LINK = re.compile(r"https?://\S+", re.IGNORECASE)
_XD = re.compile(r"(?<![a-z])x+d+(?![a-z])", re.IGNORECASE)
#: Palabras de cualquier alfabeto (latino, cirílico, griego…), sin números.
_LETTERS = re.compile(r"[^\W\d_]+")
_CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")
#: Emojis "de verdad": pictogramas, símbolos y banderas. Aproximado a propósito:
#: sin dependencias, y basta para contar emojis en un chat.
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿⌀-⏿]")
#: Lo que acompaña a un emoji sin ser un emoji: selector de variante, unión de
#: secuencias (ZWJ) y tonos de piel. Para saber si un mensaje es "solo emojis".
_EMOJI_GLUE = re.compile("[️‍\U0001f3fb-\U0001f3ff\\s]")
#: Quita tildes y diéresis pero deja la ñ (que distingue «ños» de «nos»).
_ACCENTS = str.maketrans("áéíóúüàèìòùâêîôû", "aeiouuaeiouaeiou")

# Risas ------------------------------------------------------------------------------


#: jaja, jajaj, jejeje, jiji, jojojo, jsjsjs, jajsja, ajajaj… (dos jotas o más).
_LAUGH_ES = re.compile(r"a?(?:j+[aeious]+){2,}j*|a?j+[aeiou]+j+")
#: haha, hehe, hihi, huehue, ahahah… y las siglas de internet.
_LAUGH_EN = re.compile(
    r"a?(?:h+[aeiu]+){2,}h*|lo+l+z?|lolazo|lmf?ao+|rofl(?:mao)?|kekw?|lel|lulz?|omegalul"
)
#: Portugués (kkkk, rsrs), francés (mdr, ptdr), japonés (wwww) y griego latinizado (xaxa).
_LAUGH_INTL_WORD = re.compile(r"k{4,}|(?:rs){2,}r?|p?tdr+|mdr+|w{4,}|(?:xa){2,}x?")
#: Coreano, chino, japonés, ruso y griego, buscados en el texto entero.
_LAUGH_INTL_TEXT = re.compile(
    r"ㅋ{2,}|ㅎ{2,}|哈{2,}|呵{2,}|嘻{2,}|[草笑]|(?:х[ае]){2,}|(?:ах){2,}|(?:χα){2,}"
)
#: Frases de quien se ríe con palabras: «me meo», «me parto», «lloro»…
_LAUGH_PHRASE = re.compile(
    r"\bme (?:meo|meé|parto|parti|troncho|descojono|desternillo|mondo|"
    r"cago de (?:la )?risa|muero de (?:la )?risa)\b|\bque risa\b|\bmuert[oa] de risa\b|"
    r"\blloro+\b|\bllorando\b|\bestoy muert[oa]\b|\bme he meado\b"
)
#: Sílabas sueltas de una risa con espacios («ja ja ja», «ha ha»).
_LAUGH_SYLLABLES_ES = frozenset({"ja", "je", "ji", "jo"})
_LAUGH_SYLLABLES_EN = frozenset({"ha", "he", "hi"})
_LAUGH_EMOJI = frozenset("😂🤣😹😆")
_SKULL_EMOJI = ("💀", "☠")
#: Pistas en el nombre de un emoji personalizado de que es de risa (`:kekw:`, `:pepelaugh:`).
_LAUGH_EMOJI_HINTS = ("kek", "lul", "laugh", "lol", "jaja", "haha", "xd", "risa", "rofl", "lmao")
#: Teclas de la fila central de un teclado español: lo que sale al aporrearlo.
_HOME_ROW = frozenset("asdfghjklñ")
#: «ja.», «jaja.», «ja, ja.»: la risa del funcionario. No cuenta como risa.
_DRY_LAUGH = re.compile(r"j[ae]\.*|(?:j[ae]){2}\.+|j[ae],? j[ae]\.+")


@dataclass(frozen=True, slots=True)
class Laugh:
    """Qué risas lleva un mensaje.

    Attributes:
        kinds: Tipos de `LAUGH_KINDS` que aparecen (vacío si no hay risa).
        longest: Letras de la risa más larga («jajajajaja» son 10).
        shouted: Si alguna risa va EN MAYÚSCULAS.
        dry: Si el mensaje es una risa seca de funcionario («ja.»). No es risa.
    """

    kinds: frozenset[str] = frozenset()
    longest: int = 0
    shouted: bool = False
    dry: bool = False

    @property
    def laughed(self) -> bool:
        """Si el mensaje tiene alguna risa de verdad."""
        return bool(self.kinds)


def is_keyboard_smash(word: str) -> bool:
    """Si una palabra parece un aporreo del teclado (`ajsjsjs`, `asdfghjkl`).

    Hacen falta seis letras o más, todas de la fila central, al menos tres
    distintas y como mucho una «a» de cada cuatro: así «alaska» o «falsas» no cuentan.
    """
    if len(word) < 6 or not set(word) <= _HOME_ROW or len(set(word)) < 3:
        return False
    return word.count("a") * 4 <= len(word)


#: Palabras que encajan con los patrones pero no son risas. Salen de cruzar el
#: detector con las 60.000 palabras más usadas en español, inglés y portugués.
_NOT_LAUGHS = frozenset({"jauja", "jeju", "juju", "jojo", "flasks", "skaggs"})


def _word_laugh(word: str) -> str | None:
    """Tipo de risa de una palabra suelta (ya en minúsculas y sin tildes), o `None`."""
    if word in _NOT_LAUGHS:
        return None
    if _LAUGH_ES.fullmatch(word):
        return "es"
    if _LAUGH_EN.fullmatch(word):
        return "en"
    if re.fullmatch(r"x+d+", word):
        return "xd"
    if _LAUGH_INTL_WORD.fullmatch(word):
        return "intl"
    if is_keyboard_smash(word):
        return "smash"
    return None


def analyze_laugh(text: str) -> Laugh:
    """Busca risas escritas de todas las formas que se nos han ocurrido.

    Reconoce las españolas (jaja, jsjs, ajaj, «ja ja ja»), las inglesas (haha,
    lol, lmao, kek), las de otros idiomas (kkkk, rsrs, mdr, wwww, ㅋㅋ, 哈哈,
    хаха), los aporreos del teclado (ajsjsjs), las frases («me meo», «lloro»),
    xd y los emojis (😂, 🤣, 💀 y los personalizados como `:kekw:`).
    """
    stripped = text.strip()
    lowered = stripped.lower()
    if _DRY_LAUGH.fullmatch(lowered):
        return Laugh(dry=True)

    kinds: set[str] = set()
    longest = 0
    shouted = False

    for name in _CUSTOM_EMOJI.findall(lowered):
        if any(hint in name for hint in _LAUGH_EMOJI_HINTS):
            kinds.add("emoji")
    # Sin los emojis personalizados ni los enlaces, que también tienen letras.
    plain = _LINK.sub(" ", _CUSTOM_EMOJI.sub(" ", stripped))
    if any(ch in _LAUGH_EMOJI for ch in plain):
        kinds.add("emoji")
    if any(skull in plain for skull in _SKULL_EMOJI):
        kinds.add("skull")

    words = _LETTERS.findall(plain)
    previous = ""
    for original in words:
        word = original.lower().translate(_ACCENTS)
        kind = _word_laugh(word)
        # «ja ja ja»: dos sílabas de risa seguidas cuentan como una risa.
        if kind is None and previous:
            if word in _LAUGH_SYLLABLES_ES and previous in _LAUGH_SYLLABLES_ES:
                kind = "es"
            elif word in _LAUGH_SYLLABLES_EN and previous in _LAUGH_SYLLABLES_EN:
                kind = "en"
        previous = word
        if kind is None:
            continue
        kinds.add(kind)
        longest = max(longest, len(word))
        if len(original) >= 4 and original.isupper():
            shouted = True

    normalized = plain.lower().translate(_ACCENTS)
    for match in _LAUGH_INTL_TEXT.finditer(normalized):
        kinds.add("intl")
        longest = max(longest, len(match.group(0)))
    if _LAUGH_PHRASE.search(normalized):
        kinds.add("phrase")
    return Laugh(kinds=frozenset(kinds), longest=longest, shouted=shouted)


def is_laugh(text: str) -> bool:
    """Si el mensaje contiene una risa escrita (de cualquier tipo, ver `analyze_laugh`)."""
    return analyze_laugh(text).laughed


def is_laugh_emoji(emoji: str) -> bool:
    """Si una reacción es de risa: 😂, 🤣, 😹, 😆, 💀 o un emoji propio tipo `kekw`."""
    lowered = emoji.lower()
    return (
        lowered in _LAUGH_EMOJI
        or any(skull in lowered for skull in _SKULL_EMOJI)
        or any(hint in lowered for hint in _LAUGH_EMOJI_HINTS)
    )


def is_night(hour: int) -> bool:
    """De 2:00 a 5:59, hora canaria."""
    return 2 <= hour < 6


# Vocabulario ------------------------------------------------------------------------


def _words(*items: str) -> frozenset[str]:
    return frozenset(items)


#: Canarismos. Palabras sueltas, ya sin tildes y en minúsculas.
_CANARIO = _words(
    "guagua", "guaguas", "chacho", "chacha", "ños", "fos", "cholas", "enyesque", "machango",
    "machanga", "baifo", "gofio", "mojo", "fleje", "tenderete", "cotufas", "millo", "bubango",
    "bubangos", "magua", "jeito", "naife", "pella", "aguaviva", "aguavivas", "arrorro",
    "cambullonero", "sorullo", "majalulo", "tafeña", "gánigo", "ganigo",
)  # fmt: skip
_CANARIO_PHRASES = re.compile(r"\bpapas arrugadas\b|\bmi niñ[oa]\b|\bde fleje\b|\bño+s+\b")
#: Palabras boricuas, para estar a la altura de Jovani.
_BORICUA = _words(
    "wepa", "bendito", "acho", "janguear", "jangueo", "corillo", "chavos", "bregar", "pana",
    "panas", "boricua", "perreo", "perrear", "nitido", "chavienda", "zafacon", "gufear",
    "jevo", "jeva", "guille", "algarete", "bichote", "mamey", "chinchorro",
)  # fmt: skip
_SWEAR = _words(
    "joder", "jodido", "jodida", "coño", "hostia", "hostias", "ostia", "ostias", "mierda",
    "puta", "puto", "cabron", "cabrona", "gilipollas", "cojones", "carajo", "capullo",
    "pollas", "mecaguen", "imbecil", "subnormal",
)  # fmt: skip
_SWEAR_PHRASES = re.compile(r"\bme cago\b|\bmaldita sea\b")
_MILD_SWEAR = _words(
    "jolin", "jolines", "ostras", "recorcholis", "caramba", "caracoles", "corcho", "cachis",
    "rediez", "miercoles", "porras", "cáspita", "caspita",
)  # fmt: skip
_THANKS = re.compile(r"\bgracias\b|\bgrax\b|\bthx\b|\bthanks\b|\bgracia[sz]+\b")
_SORRY = re.compile(r"\bperdon\b|\bperdona(?:me)?\b|\bsorry\b|\blo siento\b|\bmi culpa\b")
_GOOD_MORNING = re.compile(r"\bbuen[oa]?s? d[i]a+s*\b")
_GOOD_NIGHT = re.compile(r"\bbuenas noche+s*\b")
_POLITICS = _words(
    "psoe", "vox", "sanchez", "feijoo", "abascal", "ayuso", "puigdemont", "moncloa", "congreso",
    "senado", "ministro", "ministra", "ministerio", "gobierno", "diputado", "diputada",
    "elecciones", "investidura", "oposicion",
)  # fmt: skip
_FANGO = re.compile(r"\bbulos?\b|\bfango\b|\bfake news\b|\bdesinformacion\b")
#: Fuentes de ironía y coletillas de Jovani: un contador por cada una (texto sin tildes).
#: «franco» o «maduro» a secas no valen: son palabras normales.
IRONY_PATTERNS: dict[str, re.Pattern[str]] = {
    "msg_franco": re.compile(
        r"\bfranquis(?:mo|ta|tas)\b|\bcaudillo\b|\bgeneralisimo\b|\batado y bien atado\b"
    ),
    "msg_trump": re.compile(r"\btrump\b"),
    "msg_putin": re.compile(r"\bputin\b"),
    "msg_kim": re.compile(r"\bkim jong"),
    "msg_xi": re.compile(r"\bjinping\b"),
    "msg_netanyahu": re.compile(r"\bnetanyahu\b"),
    "msg_obama": re.compile(r"\bobama\b"),
    "msg_clinton": re.compile(r"\bclinton\b"),
    "msg_musk": re.compile(r"\bmusk\b"),
    "msg_milei": re.compile(r"\bmilei\b"),
    "msg_mango": re.compile(r"\btoma mango\b"),
    "msg_cafe": re.compile(r"\bquiero cafe\b"),
    "msg_pulpa": re.compile(r"\bsin pulpa\b"),
    "msg_motora": re.compile(r"\bmi motora\b"),
}
_CUÑADO = re.compile(r"\blo arreglaba yo\b|\beso lo arreglo yo\b|\byo de eso se un rato\b")
_BIZUM_ASK = re.compile(r"\b(?:hazme|pasame|mandame|enviame|haceme) (?:un )?bizum\b|\bbizumea")
_NICE = re.compile(r"(?<!\d)69(?!\d)")
_SPOILER = re.compile(r"\|\|.+?\|\|", re.DOTALL)
_CODE = re.compile(r"```|`[^`\n]+`")
_STRETCH = re.compile(r"([^\W\d_])\1{5,}")


def _emoji_count(text: str) -> int:
    return len(_EMOJI.findall(text)) + len(_CUSTOM_EMOJI.findall(text))


def _only_emoji(text: str) -> bool:
    if not text:
        return False
    rest = _EMOJI_GLUE.sub("", _EMOJI.sub("", _CUSTOM_EMOJI.sub("", text)))
    return not rest and _emoji_count(text) > 0


def _is_palindrome(letters: str) -> bool:
    return len(letters) >= PALINDROME_MIN and len(set(letters)) >= 3 and letters == letters[::-1]


def message_delta(
    content: str,
    *,
    when: datetime,
    attachments: int = 0,
    stickers: int = 0,
    is_reply: bool = False,
    mentions_others: bool = False,
    mentions_bot: bool = False,
    own_birthday: bool = False,
    mention_everyone: bool = False,
    people_mentioned: int = 0,
) -> StatDelta:
    """Lo que suma un mensaje a los logros. El contenido se mira y se olvida.

    Args:
        content: Texto del mensaje; no se guarda en ningún sitio.
        when: Hora local (Atlantic/Canary) del mensaje.
        mention_everyone: Si menciona a @everyone o @here.
        people_mentioned: Personas distintas (no bots) mencionadas.
    """
    stats = {"messages": 1}
    text = content.strip()
    lowered = text.lower()
    normalized = _LINK.sub(" ", lowered).translate(_ACCENTS)
    words = set(_LETTERS.findall(normalized))
    laugh = analyze_laugh(text)

    def bump(stat: str, condition: bool) -> None:
        if condition:
            stats[stat] = 1

    letters = [ch for ch in text if ch.isalpha()]
    weekday = when.weekday()
    # Formas de escribir
    bump("msg_long", len(text) >= LONG_MESSAGE)
    bump("msg_short", 0 < len(text) <= SHORT_MESSAGE)
    bump("msg_caps", len(letters) >= CAPS_MIN_LETTERS and all(ch.isupper() for ch in letters))
    bump("msg_questions", text.endswith("?"))
    bump("msg_rae", text.startswith(("¿", "¡")))
    bump("msg_exclaim", "!!!" in text)
    bump("msg_links", bool(_LINK.search(text)))
    bump("msg_attachments", attachments > 0)
    bump("msg_stickers", stickers > 0)
    bump("msg_replies", is_reply)
    bump("msg_mentions", mentions_others)
    bump("msg_everyone", mention_everyone)
    bump("msg_mass_ping", people_mentioned >= MASS_PING)
    bump("msg_spoiler", bool(_SPOILER.search(text)))
    bump("msg_code", bool(_CODE.search(text)))
    bump("msg_emoji_heavy", _emoji_count(text) >= EMOJI_HEAVY)
    bump("msg_only_emoji", _only_emoji(text))
    bump("msg_stretch", bool(_STRETCH.search(lowered)))
    bump("msg_long_word", any(len(w) >= LONG_WORD for w in words))
    bump("msg_palindrome", _is_palindrome("".join(ch for ch in normalized if ch.isalpha())))
    # Risas
    bump("msg_xd", bool(_XD.search(text)))
    bump("msg_laughs", laugh.laughed)
    for kind in laugh.kinds:
        stats[f"laugh_{kind}"] = 1
    bump("msg_laugh_caps", laugh.shouted)
    bump("msg_laugh_dry", laugh.dry)
    bump("laugh_night", laugh.laughed and is_night(when.hour))
    bump("laugh_sanxe", laugh.laughed and ("sanxe" in lowered or "hacienda" in normalized))
    # Lengua y temas
    bump("msg_canario", bool(words & _CANARIO or _CANARIO_PHRASES.search(normalized)))
    bump("msg_boricua", bool(words & _BORICUA))
    bump("msg_swear", bool(words & _SWEAR or _SWEAR_PHRASES.search(normalized)))
    bump("msg_mild_swear", bool(words & _MILD_SWEAR))
    bump("msg_thanks", bool(_THANKS.search(normalized)))
    bump("msg_sorry", bool(_SORRY.search(normalized)))
    bump("msg_good_morning", bool(_GOOD_MORNING.search(normalized)))
    bump("msg_good_night", bool(_GOOD_NIGHT.search(normalized)))
    bump("msg_politics", bool(words & _POLITICS))
    bump("msg_falcon", "falcon" in words)
    bump("msg_fango", bool(_FANGO.search(normalized)))
    for stat, pattern in IRONY_PATTERNS.items():
        bump(stat, bool(pattern.search(normalized)))
    bump("msg_paguita", bool(words & {"paguita", "paguitas"}))
    bump("msg_manual", "manual de resistencia" in normalized)
    bump("msg_hacienda", "hacienda" in words)
    bump("msg_cuñado", bool(_CUÑADO.search(normalized)))
    bump("msg_ola_k_ase", "ola k ase" in normalized)
    bump("msg_bizum_ask", bool(_BIZUM_ASK.search(normalized)))
    bump("msg_nice", bool(_NICE.search(text)))
    bump("msg_bot_call", mentions_bot or "jovani" in lowered)
    bump("msg_sanxe", "sanxe" in lowered)
    # Horarios y fechas
    bump("msg_night", is_night(when.hour))
    bump("msg_morning", 6 <= when.hour < 8)
    bump("msg_siesta", 15 <= when.hour < 17)
    bump("msg_office", weekday < 5 and 9 <= when.hour < 14)
    bump("msg_weekend", weekday >= 5)
    bump("msg_leet", when.hour == 13 and when.minute == 37)
    bump("msg_cinderella", when.hour == 0 and when.minute == 0)
    bump("msg_new_year", when.month == 1 and when.day == 1 and when.hour == 0)
    bump("msg_reyes", when.month == 1 and when.day == 6)
    bump("msg_valentin", when.month == 2 and when.day == 14)
    bump("msg_canarias", when.month == 5 and when.day == 30)
    bump("msg_23f", when.month == 2 and when.day == 23)
    bump("msg_july4", when.month == 7 and when.day == 4)
    bump("msg_20n", when.month == 11 and when.day == 20)
    bump("msg_pino", when.month == 9 and when.day == 8)
    bump("msg_hispanidad", when.month == 10 and when.day == 12)
    bump("msg_halloween", when.month == 10 and when.day == 31)
    bump("msg_christmas", when.month == 12 and when.day in (24, 25))
    bump("msg_inocentes", when.month == 12 and when.day == 28)
    bump("msg_friday13", weekday == 4 and when.day == 13)
    bump("msg_own_birthday", own_birthday)
    # Los adjuntos y stickers cuentan uno por mensaje, no uno por archivo:
    # subir 10 imágenes de golpe no debería valer 10 veces más.
    peak = {}
    if laugh.laughed:
        peak["laugh_len_max"] = laugh.longest
        peak["laugh_kinds_max"] = len(laugh.kinds)
    return StatDelta(add=stats, peak=peak)


def message_stats(content: str, *, when: datetime, **kwargs: object) -> dict[str, int]:
    """Contadores (sumas) que suma un mensaje. Atajo de `message_delta(...).add`."""
    return message_delta(content, when=when, **kwargs).add  # type: ignore[arg-type]


# Conversación: lo que depende de los mensajes anteriores del canal -------------------

#: Mensajes seguidos de la misma persona para hablar de monólogo.
MONOLOGUE_MIN = 2
#: Horas sin mensajes en un canal para que escribir en él sea resucitarlo.
NECRO_HOURS = 24
#: Canales que se recuerdan a la vez (los menos recientes se olvidan).
CHANNEL_MEMORY = 500
#: Hora (canaria) a partir de la cual el primer mensaje abre la persiana del día.
OPENING_HOUR = 6


@dataclass(slots=True)
class _ChannelState:
    author: int
    streak: int
    at: float
    content_hash: int
    laughing: bool
    laughers: set[int]


class ChatTracker:
    """Recuerda lo justo de cada canal para los logros que dependen del contexto.

    Monólogos, risas en cadena, aplausos de bancada (repetir lo que acaba de
    decir otro), resucitar canales y abrir la persiana del día. No guarda el
    texto: solo un hash del último mensaje de cada canal. La memoria está
    acotada a `CHANNEL_MEMORY` canales y a los contadores del día en curso.
    """

    def __init__(self) -> None:
        self._channels: OrderedDict[int, _ChannelState] = OrderedDict()
        #: Último día en que se abrió la persiana de cada servidor.
        self._day: dict[int, date] = {}
        #: Mensajes de hoy por (servidor, miembro); se vacía al cambiar de día.
        self._daily: dict[tuple[int, int], int] = {}
        self._daily_date: date | None = None

    def observe(
        self,
        guild_id: int,
        channel_id: int,
        author_id: int,
        *,
        at: float,
        local: datetime,
        content: str,
        laughed: bool,
    ) -> dict[int, StatDelta]:
        """Apunta un mensaje y devuelve lo que suma a cada miembro implicado.

        Args:
            at: Instante (epoch) del mensaje.
            local: El mismo instante en hora canaria.
            content: Texto del mensaje; solo se guarda su hash.
            laughed: Si el mensaje tiene risa (`analyze_laugh`).

        Returns:
            Cambios por miembro: casi siempre solo el autor, pero una cadena de
            risas suma a todos los que han participado.
        """
        out: dict[int, StatDelta] = {}

        def mine() -> StatDelta:
            return out.setdefault(author_id, StatDelta())

        # Mensajes del día (se vacía al cambiar de fecha).
        today = local.date()
        if self._daily_date != today:
            self._daily_date = today
            self._daily.clear()
        key = (guild_id, author_id)
        self._daily[key] = self._daily.get(key, 0) + 1
        mine().peak["msg_day_max"] = self._daily[key]

        # Primer mensaje del día en el servidor, a partir de las 6:00.
        if local.hour >= OPENING_HOUR and self._day.get(guild_id) != today:
            self._day[guild_id] = today
            mine().add["msg_first_of_day"] = 1

        digest = hash(content.strip().lower()) if content.strip() else 0
        state = self._channels.get(channel_id)
        if state is None:
            state = _ChannelState(author_id, 0, at, 0, False, set())
        else:
            if at - state.at >= NECRO_HOURS * 3600:
                mine().add["msg_necro"] = 1
            if state.author != author_id and digest and digest == state.content_hash:
                mine().add["msg_echo"] = 1
        streak = state.streak + 1 if state.author == author_id else 1
        if streak >= MONOLOGUE_MIN:
            mine().peak["msg_monologue_max"] = streak

        # Cadena de risas: mensajes con risa seguidos en el canal. Cuenta cuántas
        # personas distintas se han reído; con dos o más, suma a todas.
        laughers: set[int] = set()
        if laughed:
            laughers = (state.laughers if state.laughing else set()) | {author_id}
            if len(laughers) >= 2:
                for member in laughers:
                    out.setdefault(member, StatDelta()).peak["laugh_chain_max"] = len(laughers)

        self._channels[channel_id] = _ChannelState(author_id, streak, at, digest, laughed, laughers)
        self._channels.move_to_end(channel_id)
        while len(self._channels) > CHANNEL_MEMORY:
            self._channels.popitem(last=False)
        return out


def laugh_reply_stats(
    *, author_id: int, replied_author_id: int | None, replied_is_bot: bool
) -> dict[int, StatDelta]:
    """Lo que suma reírse respondiendo a un mensaje: al que ríe y al gracioso.

    Args:
        replied_author_id: Autor del mensaje al que se responde (`None` si no se sabe).
        replied_is_bot: Si ese autor es un bot.
    """
    out = {author_id: StatDelta(add={"laugh_replies": 1})}
    if replied_author_id is None:
        return out
    if replied_is_bot:
        out[author_id].add["laugh_at_bot"] = 1
    elif replied_author_id == author_id:
        out[author_id].add["laugh_self"] = 1
    else:
        out[replied_author_id] = StatDelta(add={"laughs_caused": 1})
    return out


#: Segundos en voz por debajo de los cuales salir cuenta como «Visto y no visto».
GHOST_SECONDS = 15


def voice_move_stats(
    *,
    before_channel: int | None,
    after_channel: int | None,
    started_stream: bool,
    joined_at: float | None,
    now: float,
) -> StatDelta:
    """Lo que suma un cambio de estado de voz: entrar, cambiar, irse o emitir.

    El canal AFK cuenta como cualquier otro: irse a él también es cambiar.

    Args:
        before_channel: Canal de antes (`None` si no estaba en voz).
        after_channel: Canal de después (`None` si se ha ido).
        started_stream: Si acaba de empezar a compartir pantalla.
        joined_at: Cuándo entró a voz (epoch), si se sabe.
        now: Instante del cambio.
    """
    add: dict[str, int] = {}
    if before_channel is None and after_channel is not None:
        add["voice_joins"] = 1
    elif before_channel is not None and after_channel is not None:
        if before_channel != after_channel:
            add["voice_hops"] = 1
    elif before_channel is not None and joined_at is not None and now - joined_at < GHOST_SECONDS:
        add["voice_ghost"] = 1
    if started_stream:
        add["voice_stream_starts"] = 1
    return StatDelta(add=add)


#: Volumen de la música (en %) por debajo del cual cuenta como «ASMR».
MUSIC_WHISPER = 10
#: Búsquedas de `poner` con logro secreto: (texto, estadística).
_MUSIC_SECRETS = (
    ("jovani", "music_jovani"),
    ("despacito", "music_despacito"),
    ("macarena", "music_macarena"),
    ("pedro", "music_pedro"),
)


def music_queue_stats(*, query: str, title: str, duration_seconds: int, position: int) -> StatDelta:
    """Lo que suma poner una canción con `poner`.

    Args:
        query: Lo que se buscó; solo se mira y se olvida.
        title: Título de la pista que se encontró.
        duration_seconds: Duración de la pista.
        position: Posición en la cola (1 si suena ya).
    """
    add = {"music_queued": 1}
    text = f"{query} {title}".lower().translate(_ACCENTS)
    for needle, stat in _MUSIC_SECRETS:
        if needle in text:
            add[stat] = 1
    return StatDelta(
        add=add,
        peak={"music_track_max": duration_seconds // 60, "music_queue_max": position},
    )


def music_volume_stats(percent: int) -> StatDelta:
    """Lo que suma cambiar el volumen de la música."""
    delta = StatDelta(peak={"music_volume_max": percent})
    if percent <= MUSIC_WHISPER:
        delta.add["music_whisper"] = 1
    return delta


def beernight_drink_stats(reason: str, sips: int, *, forgiven: int = 0) -> StatDelta:
    """Lo que suma beber en una beernight (para quien bebe).

    Args:
        reason: Motivo (`beernight.Reason`), por su valor.
        sips: Sorbos que bebe de verdad (ya con el tope aplicado).
        forgiven: Sorbos que le ha perdonado el tope.
    """
    add = {"beer_sips": sips, f"{BEER_KIND_PREFIX}{reason}": 1}
    per_reason = {
        BeerReason.CONFESSION.value: "beer_confessions",
        BeerReason.REPORT.value: "beer_accused",
        BeerReason.LIE.value: "beer_reports_false",
        BeerReason.EVENT.value: "beer_event_hits",
        BeerReason.TOAST.value: "beer_toasts",
    }
    if reason in per_reason:
        add[per_reason[reason]] = 1
    if forgiven:
        add["beer_forgiven"] = forgiven
    return StatDelta(add=add, peak={"beer_drink_max": sips})


#: Días con logro propio de la beernight: (mes, día) -> estadística.
_BEER_DATES = {
    (12, 31): "beer_nochevieja",
    (5, 30): "beer_dia_canarias",
    (9, 7): "beer_pino",
    (9, 8): "beer_pino",
    (10, 31): "beer_halloween",
    (6, 23): "beer_san_juan",
}
#: Minutos en una noche sin beber para «Abstemio de facto».
BEER_TEETOTAL_MINUTES = 60


def beernight_close_stats(
    *,
    sips: int,
    minutes: int,
    crowd: int,
    mvp: bool,
    host: bool,
    started: datetime,
    ended: datetime,
    streak: int,
) -> StatDelta:
    """Lo que suma a cada participante el cierre de una beernight.

    Args:
        sips: Sorbos que bebió esa noche.
        minutes: Minutos que estuvo (desde que llegó hasta el cierre).
        crowd: Gente que participó.
        mvp: Si fue quien más bebió.
        host: Si la organizó.
        started: Inicio de la noche, en hora de Canarias.
        ended: Cierre de la noche, en hora de Canarias.
        streak: Días seguidos con beernight, contando esta.
    """
    add = {"beer_nights": 1}
    if host:
        add["beer_hosted"] = 1
    if mvp:
        add["beer_mvp"] = 1
    if sips == 0 and minutes >= BEER_TEETOTAL_MINUTES:
        add["beer_zero_night"] = 1
    if sips == 69:
        add["beer_nice"] = 1
    if 5 <= ended.hour < 9:
        add["beer_dawn"] = 1
    if started.weekday() == 3:
        add["beer_thursday"] = 1
    if started.weekday() == 0:
        add["beer_monday"] = 1
    for day in {(started.month, started.day), (ended.month, ended.day)}:
        if stat := _BEER_DATES.get(day):
            add[stat] = 1
    return StatDelta(
        add=add,
        peak={
            "beer_night_sips_max": sips,
            "beer_night_minutes_max": minutes,
            "beer_crowd_max": crowd,
            "beer_streak_max": streak,
        },
    )


def beernight_streak(days: Iterable[date]) -> int:
    """Días seguidos con beernight que acaban en el más reciente de `days`."""
    ordered = sorted(set(days), reverse=True)
    if not ordered:
        return 0
    streak = 1
    for newer, older in zip(ordered, ordered[1:], strict=False):
        if (newer - older).days != 1:
            break
        streak += 1
    return streak


def image_stats(effect: str, extension: str, *, subject_is_author: bool | None) -> StatDelta:
    """Lo que suma generar una imagen con un efecto (`magik`, `.gay`, `.ataud`…).

    Args:
        effect: Nombre del efecto (el del comando).
        extension: Formato del resultado (`png`, `gif`, `mp4`…).
        subject_is_author: Si el avatar usado es el de quien lo pide; `None`
            si se usó una imagen adjunta y no un avatar.
    """
    add = {"img_made": 1, f"{IMG_EFFECT_PREFIX}{effect}": 1}
    if effect == "magik":
        add["img_magik"] = 1
    if extension in ("gif", "mp4"):
        add["img_video"] = 1
    if subject_is_author is True:
        add["img_self"] = 1
    elif subject_is_author is False:
        add["img_on_others"] = 1
    return StatDelta(add=add)


def babel_stats(
    *, renamed_members: int = 0, renamed_channels: int = 0, completed: bool, lost: bool
) -> StatDelta:
    """Lo que suma una tirada de `babel`.

    Args:
        renamed_members: Apodos cambiados (modo nombres); 0 en modo frase.
        renamed_channels: Canales renombrados.
        completed: Si la cadena hizo todos los saltos.
        lost: Si se cortó lejos del español.
    """
    add: dict[str, int] = {}
    if renamed_members or renamed_channels:
        if renamed_members:
            add["babel_renames"] = renamed_members
        if renamed_channels:
            add["babel_channels"] = renamed_channels
    else:
        add["babel_phrases"] = 1
    if completed:
        add["babel_full"] = 1
    if lost:
        add["babel_lost"] = 1
    return StatDelta(add=add)


def casino_stats(*, stake: int, net: int, balance_after: int, tax_delta: int = 0) -> StatDelta:
    """Lo que cuenta cualquier jugada de casino, sea del juego que sea.

    Args:
        stake: Total apostado en la jugada (dobles y separaciones incluidos).
        net: Ganancia (positiva) o pérdida (negativa) neta.
        balance_after: Saldo tras cobrar el premio.
        tax_delta: IRPF retenido (positivo) o devuelto (negativo) en la jugada.
    """
    balance_before = balance_after - net
    all_in = stake >= balance_before > 0
    delta = StatDelta(
        add={"casino_wagered": stake},
        peak={"balance_max": balance_after},
    )
    if net > 0:
        delta.peak["casino_win_max"] = net
    elif net < 0:
        delta.peak["casino_loss_max"] = -net
    if all_in:
        delta.add["casino_all_in"] = 1
        if net > 0:
            delta.add["casino_all_in_wins"] = 1
    if balance_after == 0:
        delta.add["casino_broke"] = 1
    if stake == 666:
        delta.add["casino_bet_666"] = 1
    if stake == 42:
        delta.add["casino_bet_42"] = 1
    for secret_stake in SECRET_STAKES:
        if stake == secret_stake:
            delta.add[f"casino_bet_{secret_stake}"] = 1
    if tax_delta > 0:
        delta.add["tax_paid"] = tax_delta
    elif tax_delta < 0:
        delta.add["tax_refunds"] = 1
    return delta


def roulette_stats(
    outcome: RoundOutcome,
    *,
    table_streak: int,
    previous_pocket: int | None,
    hunches: Mapping[str, str] | None = None,
) -> StatDelta:
    """Contadores de una tirada de ruleta (sin lo común del casino).

    Args:
        table_streak: Tiradas ganadas seguidas en la mesa, contando esta.
        previous_pocket: Número de la tirada anterior en la misma mesa.
        hunches: Apuestas puestas con 🔥 Caliente o ❄️ Frío, `{clave: "hot"|"cold"}`.
    """
    delta = StatDelta(
        add={"roulette_spins": 1},
        peak={"roulette_wagers_max": len(outcome.wagers), "roulette_streak_max": table_streak},
    )
    won = [w for w, r in zip(outcome.wagers, outcome.returns, strict=True) if r]
    if outcome.won:
        delta.add["roulette_wins"] = 1
    straight = [w for w in won if len(w.bet.numbers) == 1]
    if straight:
        delta.add["roulette_straight_wins"] = len(straight)
    if any(w.bet.numbers <= ZEROS for w in won):
        delta.add["roulette_green_wins"] = 1
    if outcome.pocket == DOUBLE_ZERO and any(w.bet.numbers == {DOUBLE_ZERO} for w in straight):
        delta.add["roulette_double_zero_wins"] = 1
    colors = sum(1 for w in won if w.bet.key in ("red", "black"))
    if colors:
        delta.add["roulette_color_wins"] = colors
    if previous_pocket is not None and previous_pocket == outcome.pocket:
        delta.add["roulette_repeat_pocket"] = 1
    add = delta.add
    dozens = sum(1 for w in won if w.bet.key.startswith(("dozen", "col")))
    if dozens:
        add["roulette_dozen_wins"] = dozens
    halves = sum(1 for w in won if w.bet.key in ("even", "odd", "low", "high"))
    if halves:
        add["roulette_half_wins"] = halves
    # Ganar alguna apuesta y aun así perder dinero en la tirada.
    if any(outcome.returns) and outcome.net < 0:
        add["roulette_pyrrhic"] = 1
    for wager in straight:
        (number,) = wager.bet.numbers
        add[f"{ROULETTE_HIT_PREFIX}{number}"] = add.get(f"{ROULETTE_HIT_PREFIX}{number}", 0) + 1
    covered = set().union(*(w.bet.numbers for w in outcome.wagers))
    delta.peak["roulette_cover_max"] = len(covered)
    if outcome.pocket in ZEROS and not any(outcome.returns):
        add["roulette_zero_sweep"] = 1
    # Los rayos, el casi y las corazonadas (ver `bot.services.roulette`).
    delta.peak["roulette_storm_max"] = len(outcome.lucky)
    if outcome.lucky_hit:
        add["roulette_lucky_wins"] = 1
        delta.peak["roulette_lucky_max"] = outcome.lucky_hit
        if outcome.pocket in ZEROS:
            add["roulette_lucky_zero"] = 1
    elif outcome.lucky_covered:
        add["roulette_lucky_missed"] = 1
    if outcome.near_miss is not None:
        add["roulette_near_miss"] = 1
    for wager, returned in zip(outcome.wagers, outcome.returns, strict=True):
        kind = (hunches or {}).get(wager.bet.key)
        if kind in ("hot", "cold"):
            add[f"roulette_{kind}_bets"] = add.get(f"roulette_{kind}_bets", 0) + 1
            if returned:
                add[f"roulette_{kind}_hits"] = add.get(f"roulette_{kind}_hits", 0) + 1
    return delta


#: Mano «de las gordas» para los logros de `bj_max_stake` (50 tiradas). Era el
#: tope de la mesa hasta que el blackjack dejó de favorecer al jugador.
BJ_HIGH_STAKE = 5_000


def blackjack_stats(game: BlackjackGame) -> StatDelta:
    """Contadores de una mano de blackjack ya pagada (sin lo común del casino)."""
    delta = StatDelta(add={"bj_hands": 1})
    add = delta.add

    def bump(stat: str, amount: int = 1) -> None:
        if amount:
            add[stat] = add.get(stat, 0) + amount

    results = [hand.result for hand in game.hands]
    dealer_total = game.dealer_total
    if game.net > 0:
        bump("bj_wins")
    bump("bj_naturals", results.count(Result.BLACKJACK))
    bump("bj_double_wins", sum(1 for h in game.hands if h.doubled and h.result is Result.WIN))
    if len(game.hands) > 1:
        bump("bj_splits")
        if all(r is Result.WIN for r in results):
            bump("bj_split_sweeps")
    bump("bj_busts", results.count(Result.BUST))
    bump("bj_pushes", results.count(Result.PUSH))
    bump("bj_21_multi", sum(1 for h in game.hands if h.total == 21 and len(h.cards) >= 3))
    if dealer_total > 21 and Result.WIN in results:
        bump("bj_dealer_busts")
    if is_blackjack(game.dealer):
        bump("bj_dealer_naturals")
    if game.insurance:
        bump("bj_insured")
        if game.insurance_paid:
            bump("bj_dealer_bj_saved")
        else:
            bump("bj_insurance_wasted")
            if all(h.busted for h in game.hands):
                bump("bj_insured_bust")
    natural = bool(game.hands) and game.hands[0].natural
    # El seguro se ofreció si la banca enseñaba un as (con ficha de 1 no se puede tomar).
    offered = natural and game.dealer[0].rank == 1 and game.insurance_cost > 0
    if offered and game.insurance:
        bump("bj_natural_insured")
        if game.insurance_paid:
            bump("bj_natural_insured_saved")
    elif offered and is_blackjack(game.dealer):
        bump("bj_natural_greed")
    if game.stake >= BJ_HIGH_STAKE:
        bump("bj_max_stake")
    if any(h.result is Result.LOSE and h.total == 20 and dealer_total == 21 for h in game.hands):
        bump("bj_bad_beat")
    if any(_kamikaze(h.cards, h.doubled, h.busted) for h in game.hands):
        bump("bj_kamikaze")
    standing = [len(h.cards) for h in game.hands if not h.busted]
    if standing:
        delta.peak["bj_cards_max"] = max(standing)
    for hand in game.hands:
        cards = hand.cards
        if hand.result is Result.BLACKJACK and cards[0].suit == cards[1].suit:
            bump("bj_suited_natural")
        if [c.rank for c in cards] == [7, 7, 7]:
            bump("bj_triple_seven")
        if hand.total == 21 and len(cards) >= 5:
            bump("bj_five_21")
        if hand.doubled and hand.result in (Result.LOSE, Result.BUST):
            bump("bj_double_loss")
        if (
            not hand.busted
            and len(cards) == 2
            and hand.total <= 11
            and not is_blackjack(game.dealer)
        ):
            bump("bj_stand_low")
    if len(game.hands) > 1 and game.hands[0].cards[0].rank == 1:
        bump("bj_split_aces")
    if is_blackjack(game.dealer) and Result.PUSH in results and game.hands[0].natural:
        bump("bj_both_bj")
    if len(game.dealer) >= 5:
        bump("bj_dealer_five")
    return delta


def _kamikaze(cards: list, doubled: bool, busted: bool) -> bool:
    """Pidió carta con un 17 duro o más y no se pasó.

    Con el 17 "blando" (as que vale 11) pedir no tiene riesgo, así que no cuenta.
    """
    if doubled or busted or len(cards) < 3:
        return False
    total, soft = hand_total(cards[:-1])
    return total >= 17 and not soft


def slots_stats(
    spin: Spin,
    *,
    stake: int,
    payout: int,
    jackpot: int,
    free: bool,
    hot: bool,
    turbo: bool,
    session_spins: int,
    when: datetime,
    autoplay: bool = False,
    tier: str | None = None,
    mystery: bool = False,
    drought: int = 0,
    daily: bool = False,
    daily_streak: int = 0,
) -> StatDelta:
    """Contadores de una tirada de tragaperras (sin lo común del casino).

    Args:
        stake: Apuesta de la tirada; en un giro gratis, la que lo activó.
        payout: Lo que ha devuelto la línea (con la máquina caliente incluida).
        jackpot: Lo que se ha llevado del bote (0 si nada).
        free: Si era un giro gratis.
        hot: Si la máquina estaba caliente.
        session_spins: Tiradas en esta máquina, contando esta.
        when: Hora local de la tirada.
        autoplay: Si la tirada se jugó con ▶️ Auto (con animación, una a una).
        tier: Celebración de la tirada (`slots.win_tier`): `"big"`, `"mega"`,
            `"epic"` o `None`.
        mystery: Si el bote que se ha llevado cayó por el tope oculto.
        drought: Si se ha llevado el bote, tiradas que llevaba el servidor sin bote.
        daily: Si era el giro diario gratis.
        daily_streak: Días seguidos del giro diario, contando este.
    """
    delta = StatDelta(add={"slots_spins": 1}, peak={"slots_session_max": session_spins})
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    paid_stake = 0 if free else stake
    won = payout + jackpot
    net = won - paid_stake
    bump("slots_wins", net > 0)
    bump("slots_ldw", 0 < won < paid_stake)
    if net > 0:
        delta.peak["slots_win_max"] = net
    if jackpot:
        bump("slots_jackpots")
        delta.peak["slots_jackpot_max"] = jackpot
    if spin.kind == SlotKind.THREE and spin.symbol is not None:
        bump(f"slots_three_{spin.symbol}")
    bump("slots_near_miss", spin.near_miss)
    bump("slots_anticipation", spin.anticipation and not turbo)
    bump("slots_scatter_tease", spin.scatters == 2)
    bump("slots_free_triggers", spin.triggers_free_spins)
    bump("slots_free_spins", free)
    bump("slots_free_retriggers", free and spin.triggers_free_spins)
    bump("slots_hot_spins", hot)
    bump("slots_hot_big", hot and stake > 0 and payout >= 20 * stake)
    bump("slots_wild_wins", payout > 0 and SLOT_WILD in spin.line)
    bump("slots_turbo", turbo)
    bump("slots_pot_fed", amount=0 if free else slots_pot_share(stake))
    bump("slots_night", 3 <= when.hour < 6)
    bump("slots_autoplay_spins", autoplay)
    bump("slots_autoplay_free", autoplay and free)
    bump("slots_autoplay_jackpot", autoplay and jackpot > 0)
    if tier in SLOTS_WIN_TIERS:
        bump(f"slots_win_{tier}")
        bump("slots_tiny_big", stake == 1 and not free and not daily)
        bump("slots_daily_big", daily)
    if jackpot:
        bump("slots_mystery_pots", mystery)
        if drought > 0:
            delta.peak["slots_pot_drought"] = drought
            bump("slots_pot_quick", drought <= SLOTS_QUICK_POT)
    if daily:
        bump("slots_daily")
        if daily_streak > 0:
            delta.peak["slots_daily_streak"] = daily_streak
    return delta


#: Niveles de celebración de `slots.win_tier` (`WinTier.BIG`, `MEGA` y `EPIC`). Cada
#: uno suma su contador (`slots_win_big`…); no se solapan.
SLOTS_WIN_TIERS = (SlotWinTier.BIG, SlotWinTier.MEGA, SlotWinTier.EPIC)
#: Tiradas sin bote del servidor por debajo de las que llevárselo es «Ni lo calentaste».
SLOTS_QUICK_POT = 100
#: Dobles seguidos que deja la máquina tras un premio.
SLOTS_DOUBLE_MAX = 5


def slots_respin_stats(*, price: int, payout: int, jackpot: int, chain: int) -> StatDelta:
    """Contadores de un re-giro del tercer rodillo tras un casi-premio.

    Args:
        price: Lo que ha costado el re-giro.
        payout: Lo que ha pagado la línea tras re-girar (0 si nada).
        jackpot: Lo que se ha llevado del bote (0 si nada).
        chain: Re-giros seguidos sobre la misma tirada, contando este.
    """
    delta = StatDelta(add={"slots_respins": 1})
    if price > 0:
        delta.add["slots_respin_spent"] = price
        delta.peak["slots_respin_price_max"] = price
    if payout + jackpot > 0:
        delta.add["slots_respin_saved"] = 1
        if chain >= 3:
            delta.add["slots_respin_bailout"] = 1
        if jackpot > 0:
            delta.add["slots_respin_jackpots"] = 1
    else:
        delta.peak["slots_respin_fail_chain"] = chain
    return delta


def slots_double_stats(*, amount: int, won: bool, chain: int) -> StatDelta:
    """Contadores de un doble o nada (rojo o negro) tras un premio.

    Args:
        amount: Lo que se jugaba en este doble.
        won: Si ha acertado el color.
        chain: Dobles ganados seguidos tras este si ha ganado; si ha perdido, los
            que llevaba ganados antes de este.
    """
    delta = StatDelta(add={"slots_doubles": 1})
    if won:
        delta.add["slots_double_wins"] = 1
        delta.peak["slots_double_chain"] = chain
        delta.peak["slots_double_win_max"] = amount
        if chain >= SLOTS_DOUBLE_MAX:
            delta.add["slots_double_fives"] = 1
    else:
        delta.peak["slots_double_loss_max"] = amount
        if chain == 0:
            delta.add["slots_double_nada"] = 1
        elif chain == SLOTS_DOUBLE_MAX - 1:
            delta.add["slots_double_heartbreak"] = 1
    return delta


def slots_cooled_stats(*, lost: int) -> StatDelta:
    """Contadores del calor que ha perdido la máquina por no jugar.

    Args:
        lost: Puntos de calor perdidos (al reabrir la máquina o al tirar).
    """
    if lost <= 0:
        return StatDelta()
    return StatDelta(add={"slots_cooled": lost}, peak={"slots_cooled_max": lost})


def slots_bonus_stats(*, fill_spins: int) -> StatDelta:
    """Contadores de una barra de bonus llena.

    Args:
        fill_spins: Tiradas pagadas que ha costado llenarla.
    """
    delta = StatDelta(add={"slots_bonus_fills": 1}, peak={"slots_bonus_slowest": fill_spins})
    if fill_spins <= SLOTS_BONUS_QUICK:
        delta.add["slots_bonus_quick"] = 1
    return delta


def slots_ticket_stats(*, spins: int, gross: int, staked: int, net: int) -> StatDelta:
    """Contadores del ticket que sale al cerrar la máquina.

    Args:
        spins: Tiradas de la sesión.
        gross: Premios cobrados en bruto durante la sesión.
        staked: Lo apostado en la sesión.
        net: Resultado real de la sesión (lo que entra o sale del monedero).
    """
    if spins <= 0:
        return StatDelta()
    delta = StatDelta(add={"slots_tickets": 1})
    add, peak = delta.add, delta.peak
    if gross > 0:
        peak["slots_ticket_gross_max"] = gross
    if net > 0:
        peak["slots_ticket_best"] = net
    elif net < 0:
        peak["slots_ticket_loss_max"] = -net
        if gross > 0:
            peak["slots_ticket_creative"] = gross
        if gross > staked:
            add["slots_ticket_taxed"] = 1
    if spins >= 10:
        if net == 0:
            add["slots_ticket_zero"] = 1
        if gross == 0:
            add["slots_ticket_dry"] = 1
    if spins == 1:
        add["slots_ticket_quick"] = 1
    return delta


#: Tiradas mínimas de una sesión de Auto para que pararla a mano en positivo
#: cuente como retirarse a tiempo (con menos, es suerte de una racha corta).
MANUAL_EXIT_MIN_SPINS = 10


def slots_autoplay_stats(*, spins: int, net: int, reason: StopReason) -> StatDelta:
    """Contadores de una sesión de ▶️ Auto de la tragaperras, al terminar.

    Las tiradas ya se contaron una a una en `slots_stats`; aquí solo cuenta la
    sesión y cómo acabó. Una sesión sin ninguna tirada (no llegaba el saldo) no
    cuenta.

    Args:
        spins: Tiradas jugadas en la sesión.
        net: Neto de la sesión (ganado menos apostado).
        reason: Por qué paró.
    """
    delta = StatDelta()
    if spins <= 0:
        return delta
    add = delta.add
    add["slots_autoplay_sessions"] = 1
    if reason is StopReason.MAX_SPINS:
        add["slots_autoplay_full"] = 1
        if net == 0:
            add["slots_autoplay_even"] = 1
    elif reason is StopReason.LOSS_LIMIT:
        add["slots_autoplay_loss_limit"] = 1
    elif reason is StopReason.BIG_PRIZE:
        add["slots_autoplay_bigwin"] = 1
    elif reason is StopReason.NO_FUNDS:
        add["slots_autoplay_broke"] = 1
    elif reason is StopReason.MANUAL:
        add["slots_autoplay_manual"] = 1
        if spins == 1:
            add["slots_autoplay_quick_quit"] = 1
        if net > 0 and spins >= MANUAL_EXIT_MIN_SPINS:
            add["slots_autoplay_exit_ahead"] = 1
    return delta


def hold_win_stats(
    spin: HoldWinSpin,
    *,
    theme: str,
    stake: int,
    payout: int,
    trigger: HoldWinTrigger | None,
    turbo: bool,
    session_spins: int,
    when: datetime,
) -> StatDelta:
    """Contadores de una tirada base de Botes (sin lo común del casino).

    Args:
        theme: Máquina (de momento, `volcan`).
        payout: Lo cobrado en la tirada (ways más recogida).
        trigger: Bonus que dispara la tirada, si alguno.
        session_spins: Tiradas en esta máquina, contando esta.
        when: Hora local de la tirada.
    """
    del session_spins  # de momento sin logros de sesión larga
    delta = StatDelta(add={"botes_spins": 1, f"botes_spins_{theme}": 1})
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    bump("botes_collects", spin.collectors > 0 and bool(spin.coins))
    bump("botes_double_collect", spin.collectors == 2 and bool(spin.coins))
    bump("botes_near_miss", spin.near_miss)
    bump("botes_ways_5", any(win.reels == 5 for win in spin.wins))
    bump("botes_wild_wins", spin.wild_win)
    bump("botes_chips", amount=sum(spin.chips.values()))
    bump("botes_turbo", turbo)
    bump("botes_night", 3 <= when.hour < 6)
    if payout - stake > 0:
        delta.peak["botes_win_max"] = payout - stake
    if trigger is not None:
        bump("botes_bonuses")
        bump(f"botes_bonus_{trigger.kind}")
    return delta


def hold_win_bonus_stats(
    result: HoldWinBonusResult, steps: Iterable[HoldWinStep], *, amount: int
) -> StatDelta:
    """Contadores de un bonus de Botes terminado.

    El bonus en sí ya se contó al dispararse (`hold_win_stats`); aquí va lo
    que ha pasado dentro.

    Args:
        steps: Tiradas del bonus, en orden.
        amount: Lo cobrado en Y$.
    """
    delta = StatDelta(peak={"botes_mult_max": result.multiplier, "botes_bonus_max": amount})
    for step in steps:
        for landing in step.landings:
            if landing.mystery:
                delta.add["botes_mystery"] = delta.add.get("botes_mystery", 0) + 1
            if landing.cell.kind == "instant":
                delta.add["botes_instant"] = delta.add.get("botes_instant", 0) + 1
        if step.maximized:
            delta.add["botes_maximizer"] = delta.add.get("botes_maximizer", 0) + 1
    for name in result.jackpots:
        delta.add[f"botes_{name}"] = 1
    if result.coins == 19:
        delta.add["botes_almost_grand"] = 1
    return delta


#: Todas las estadísticas que suman las porras (menos las de prefijo por juego y propuesta).
PORRA_STATS = frozenset(
    {
        "porra_opened", "porra_rejected", "porra_long", "porra_opener_against",
        "porra_accepted", "porra_declined", "porra_image", "porra_star_wins",
        "porra_heroic", "porra_no_show", "porra_broke", "porra_pool_max", "porra_bets",
        "porra_wins", "porra_losses", "porra_win_streak_max", "porra_win_max",
        "porra_odds_max", "porra_lone_wolf", "porra_favourite_flop", "porra_loyal_losses",
        "porra_tamayazo", "porra_refunds", "porra_nobody", "porra_iaj", "porra_crowd_max",
        "porra_bet_min", "porra_bet_666", "porra_bet_69", "porra_all_in", "porra_snoops",
        "porra_night", "porra_carrusel", "porra_nochevieja", "porra_canarias",
        "porra_friday13",
    }
)  # fmt: skip


def porra_open_stats(*, game: str, proposition: str, plays: int) -> StatDelta:
    """Lo que cuenta montar una porra (para quien la monta).

    Args:
        game: Juego de la porra (clave de `casino_stats.GAMES`).
        proposition: Clave de la propuesta (`bot.services.porras.PROPOSITIONS`).
        plays: Jugadas pedidas.
    """
    add = {
        "porra_opened": 1,
        f"{PORRA_GAME_PREFIX}{game}": 1,
        f"{PORRA_PROP_PREFIX}{proposition}": 1,
    }
    if plays > 5:
        add["porra_long"] = 1
    return StatDelta(add=add)


def porra_answer_stats(*, accepted: bool) -> tuple[StatDelta, StatDelta]:
    """Lo que cuenta la respuesta del protagonista: `(para él, para quien la montó)`."""
    if accepted:
        return StatDelta(add={"porra_accepted": 1}), StatDelta()
    return StatDelta(add={"porra_declined": 1}), StatDelta(add={"porra_rejected": 1})


def porra_bet_stats(
    *, stake: int, balance_before: int, opener_against: bool, when: datetime
) -> StatDelta:
    """Lo que cuenta una apuesta a una porra, al hacerla.

    Args:
        stake: Lo apostado esta vez.
        balance_before: Saldo antes de apostar.
        opener_against: Si quien apuesta montó la porra y va contra el protagonista.
        when: Hora canaria de la apuesta.
    """
    add = {"porra_bets": 1}

    def bump(stat: str, condition: bool) -> None:
        if condition:
            add[stat] = 1

    bump("porra_bet_min", stake == PORRA_MIN_BET)
    bump("porra_bet_666", stake == 666)
    bump("porra_bet_69", stake == 69)
    bump("porra_all_in", stake >= balance_before > 0)
    bump("porra_opener_against", opener_against)
    bump("porra_night", 2 <= when.hour < 6)
    bump("porra_carrusel", when.weekday() == 6 and 16 <= when.hour < 20)
    bump("porra_nochevieja", (when.month, when.day) == (12, 31))
    bump("porra_canarias", (when.month, when.day) == (5, 30))
    bump("porra_friday13", when.weekday() == 4 and when.day == 13)
    return StatDelta(add=add)


def porra_bettor_stats(
    *,
    stake: int,
    payout: int,
    refund: bool,
    nobody: bool,
    tax: int,
    streak: int,
    lone_wolf: bool,
    favourite_flop: bool,
    loyal_loss: bool,
    flipped: bool,
    crowd: int,
) -> StatDelta:
    """Lo que cuenta el final de una porra para quien apostó.

    Args:
        stake: Lo que llevaba apostado.
        payout: Lo que le ha vuelto (0 si falló; lo apostado si se devolvió).
        refund: Si se le ha devuelto (anulada o sin aciertos).
        nobody: Si se devolvió porque no acertó nadie.
        tax: IAJ que ha pagado.
        streak: Porras acertadas seguidas tras esta (0 si falló).
        lone_wolf: Si ha sido el único en acertar con 3 o más en contra.
        favourite_flop: Si ha fallado con el 75 % del bote o más en su opción.
        loyal_loss: Si ha fallado apostando a lo bueno para el protagonista.
        flipped: Si la última jugada le dio la vuelta al resultado.
        crowd: Apostantes de la porra.
    """
    delta = StatDelta(peak={"porra_crowd_max": crowd})
    add = delta.add
    if refund:
        add["porra_refunds"] = 1
        if nobody:
            add["porra_nobody"] = 1
        return delta
    if tax:
        add["porra_iaj"] = tax
    if payout > 0:
        add["porra_wins"] = 1
        delta.peak["porra_win_streak_max"] = streak
        if payout > stake:
            delta.peak["porra_win_max"] = payout - stake
        if stake:
            delta.peak["porra_odds_max"] = payout // stake
        if lone_wolf:
            add["porra_lone_wolf"] = 1
        if flipped:
            add["porra_tamayazo"] = 1
    else:
        add["porra_losses"] = 1
        if favourite_flop:
            add["porra_favourite_flop"] = 1
        if loyal_loss:
            add["porra_loyal_losses"] = 1
    return delta


def porra_subject_stats(
    *, image: int, pool: int, favourable: bool | None, heroic: bool, broke: bool, crowd: int
) -> StatDelta:
    """Lo que cuenta una porra resuelta para su protagonista.

    Args:
        image: Derechos de imagen brutos cobrados.
        pool: Bote de la porra.
        favourable: Si ha salido la opción buena para él (`None` si la propuesta
            no tiene una buena, como «cuántas gana»).
        heroic: Si ha salido la buena con todo el dinero en contra (3 o más).
        broke: Si se ha quedado sin saldo para seguir.
        crowd: Apostantes de la porra.
    """
    delta = StatDelta(peak={"porra_pool_max": pool, "porra_crowd_max": crowd})
    if image:
        delta.add["porra_image"] = image
    if favourable:
        delta.add["porra_star_wins"] = 1
    if heroic:
        delta.add["porra_heroic"] = 1
    if broke:
        delta.add["porra_broke"] = 1
    return delta


def porra_no_show_stats() -> StatDelta:
    """El protagonista no ha jugado a tiempo."""
    return StatDelta(add={"porra_no_show": 1})


def porra_snoop_stats() -> StatDelta:
    """Ha mirado quién apuesta qué con los prismáticos."""
    return StatDelta(add={"porra_snoops": 1})


def apuestas_stats(
    *,
    page: str,
    period: str | None,
    opened: bool,
    own: bool,
    snooping: bool,
    plays: int,
    rtp: float | None,
    net: int,
    when: datetime,
) -> StatDelta:
    """Contadores de mirar las estadísticas del casino (`apuestas`).

    Args:
        page: Página que se enseña (una de `APUESTAS_PAGES`).
        period: Clave del periodo (`hoy`, `semana`, `mes`, `siempre`), o `None`
            si no ha cambiado.
        opened: Si es el comando (una consulta) y no un cambio de página.
        own: Si mira sus propias cifras de siempre (las de los secretos).
        snooping: Si mira las de otro miembro.
        plays: Jugadas pagadas del miembro de `own` (de siempre).
        rtp: Pagado / apostado del miembro de `own`.
        net: Resultado del miembro de `own` antes de impuestos.
        when: Hora canaria de la consulta.
    """
    add: dict[str, int] = {f"apuestas_page_{page}": 1}
    if period is not None:
        add[f"apuestas_period_{period}"] = 1
    if opened:
        add["apuestas_views"] = 1
        if snooping:
            add["apuestas_snoop"] = 1
        if 2 <= when.hour < 6:
            add["apuestas_insomnia"] = 1
    if own:
        if plays == 0:
            add["apuestas_virgin"] = 1
        if plays >= 100 and rtp is not None:
            if rtp < 0.5:
                add["apuestas_denial"] = 1
            if 0.99 <= rtp <= 1.01:
                add["apuestas_even"] = 1
        if net <= -100_000:
            add["apuestas_ruin"] = 1
        if net >= 100_000:
            add["apuestas_rich"] = 1
    return StatDelta(add=add)


def patrimonio_stats(
    *,
    listing: bool,
    snooping: bool,
    net_worth: int,
    illiquid: bool,
    rank: int,
    people: int,
) -> StatDelta:
    """Contadores de mirar el patrimonio en `perfil` (y de `fortunas` si `listing`).

    Args:
        listing: Si es `fortunas`.
        snooping: Si mira el patrimonio de otro.
        net_worth: Su propio patrimonio neto (0 si no mira el suyo).
        illiquid: Si mira el suyo y más de la mitad no es efectivo.
        rank: Su puesto en la lista (0 si no está o no aplica).
        people: Cuántos salen en la lista.
    """
    add: dict[str, int] = {}
    peak: dict[str, int] = {}
    if listing:
        add["fortunas_views"] = 1
        if rank == 1 and people > 1:
            add["fortunas_first"] = 1
        if people >= 3 and rank == people:
            add["fortunas_last"] = 1
    else:
        add["patrimonio_views"] = 1
        if snooping:
            add["patrimonio_snoop"] = 1
        if illiquid:
            add["patrimonio_illiquid"] = 1
    if net_worth > 0:
        peak["net_worth_max"] = net_worth
    return StatDelta(add=add, peak=peak)


def hacienda_stats(
    *, own: bool, snooping: bool, share: float, indirect_over_direct: bool
) -> StatDelta:
    """Contadores de `hacienda`.

    Args:
        own: Si mira su propia factura.
        snooping: Si mira la de otro.
        share: Parte de lo recaudado que ha pagado quien mira (0–1).
        indirect_over_direct: Si quien mira paga más en indirectos que en directos.
    """
    add = {"hacienda_views": 1}
    if own:
        add["hacienda_self"] = 1
    if snooping:
        add["hacienda_snoop"] = 1
    if share >= 0.5:
        add["hacienda_pillar"] = 1
    if indirect_over_direct:
        add["hacienda_hidden"] = 1
    return StatDelta(add=add)


#: Una bola «tarda casi 2 s» si su caída dura estos fotogramas o más (el máximo es 41).
PACHINKO_SLOW_FRAMES = 40
#: Una bola «cae en 1,1 s» si su caída dura estos fotogramas o menos (el mínimo es 22).
PACHINKO_SWIFT_FRAMES = 23
#: Una bola «baja sin tocar casi nada» si rebota en los clavos esto o menos.
PACHINKO_CLEAN_BOUNCES = 5


def pachinko_stats(
    volley: PachinkoVolley,
    *,
    motion: PachinkoMotion,
    stake: int,
    won: int,
    turbo: bool,
    session_volleys: int,
    when: datetime,
    autoplay: bool = False,
) -> StatDelta:
    """Contadores de una tanda de pachinko (sin lo común del casino).

    Args:
        motion: Cómo se han movido las bolas (`pachinko_motion.motion_for`, con
            sus choques), se dibuje la tanda o no.
        stake: Apuesta de la tanda.
        won: Lo que ha devuelto (apuesta incluida).
        turbo: Si se jugó sin animación (turbo o Ráfaga).
        session_volleys: Tandas en esta máquina, contando esta.
        when: Hora local de la tanda.
        autoplay: Si la tanda se jugó con ▶️ Auto (con animación, una a una).

    Las estadísticas de rebotes, de duración y de choques salen del movimiento
    que se ve, no de lo que paga la máquina, así que no tocan el dinero.
    """
    delta = StatDelta(add={"pachinko_volleys": 1}, peak={"pachinko_session_max": session_volleys})
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    net = won - stake
    if net > 0:
        delta.peak["pachinko_win_max"] = net
    bump("pachinko_starts", amount=volley.starts)
    bump("pachinko_reach", amount=sum(1 for d in volley.draws if d.reach))
    bump("pachinko_fake_reach", amount=sum(1 for d in volley.draws if d.reach and not d.atari))
    bump("pachinko_atari", amount=sum(1 for d in volley.draws if d.atari))
    bump("pachinko_rush", amount=sum(1 for d in volley.draws if d.kind == PachinkoKind.RUSH))
    bump("pachinko_super", amount=sum(1 for d in volley.draws if d.kind == PachinkoKind.SUPER))
    if volley.draws:
        delta.peak["pachinko_renchan_max"] = max(d.jackpots for d in volley.draws)
    bump("pachinko_corners", amount=volley.corners)
    bump("pachinko_full_hold", len(volley.draws) >= 4)
    bump("pachinko_wasted", volley.wasted > 0)
    bump("pachinko_blank", volley.total_balls == 0)
    bump("pachinko_turbo", turbo)
    bump("pachinko_night", 3 <= when.hour < 6)
    key = volley.board.key
    bump(f"pachinko_board_{key}")
    bump(f"pachinko_atari_{key}", amount=sum(1 for d in volley.draws if d.atari))
    drops = motion.balls
    bounces = [drop.bounces for drop in drops]
    bump("pachinko_bounces", amount=sum(bounces))
    if bounces:
        delta.peak["pachinko_bounce_max"] = max(bounces)
        delta.peak["pachinko_bounce_volley_max"] = sum(bounces)
    bump("pachinko_slow_balls", amount=sum(1 for d in drops if d.frames >= PACHINKO_SLOW_FRAMES))
    bump("pachinko_swift_balls", amount=sum(1 for d in drops if d.frames <= PACHINKO_SWIFT_FRAMES))
    bump(
        "pachinko_clean_balls",
        amount=sum(1 for d in drops if d.bounces <= PACHINKO_CLEAN_BOUNCES),
    )
    # Choques entre bolas: salen del movimiento real, no del sorteo. Un choque cuenta
    # una vez en la tanda (`VolleyMotion.collisions`) y una vez por cada bola que lo
    # sufre (`BallMotion.collisions`).
    hits = [drop.collisions for drop in drops]
    bump("pachinko_hits", amount=motion.collisions)
    bump("pachinko_no_hits", not motion.collisions)
    if hits:
        delta.peak["pachinko_hits_max"] = motion.collisions
        delta.peak["pachinko_hit_ball_max"] = max(hits)
    corners = (0, volley.board.rows)
    bump(
        "pachinko_hit_corner",
        amount=sum(1 for drop in drops if drop.collisions and drop.pocket in corners),
    )
    bump("pachinko_delayed", amount=motion.delayed)
    if hits:
        delta.peak["pachinko_balls_hit_max"] = sum(1 for count in hits if count)
    if autoplay:
        bump("pachinko_autoplay_volleys")
        bump(
            "pachinko_autoplay_super",
            amount=sum(1 for d in volley.draws if d.kind == PachinkoKind.SUPER),
        )
        bump(
            "pachinko_autoplay_fake_reach",
            amount=sum(1 for d in volley.draws if d.reach and not d.atari),
        )
        if volley.draws:
            delta.peak["pachinko_autoplay_renchan_max"] = max(d.jackpots for d in volley.draws)
    return delta


def pachinko_autoplay_stats(*, volleys: int, net: int, reason: StopReason) -> StatDelta:
    """Contadores de una sesión de ▶️ Auto del pachinko, al terminar.

    Las tandas ya se contaron una a una en `pachinko_stats`; aquí solo cuenta la
    sesión y cómo acabó. Una sesión sin ninguna tanda (no llegaba el saldo) no
    cuenta. Parar por «premio gordo» es parar por un atari.

    Args:
        volleys: Tandas jugadas en la sesión.
        net: Neto de la sesión (ganado menos apostado).
        reason: Por qué paró.
    """
    delta = StatDelta()
    if volleys <= 0:
        return delta
    add = delta.add
    add["pachinko_autoplay_sessions"] = 1
    if reason is StopReason.MAX_SPINS:
        add["pachinko_autoplay_full"] = 1
        if net == 0:
            add["pachinko_autoplay_even"] = 1
    elif reason is StopReason.LOSS_LIMIT:
        add["pachinko_autoplay_loss_limit"] = 1
    elif reason is StopReason.BIG_PRIZE:
        add["pachinko_autoplay_bigwin"] = 1
    elif reason is StopReason.NO_FUNDS:
        add["pachinko_autoplay_broke"] = 1
    elif reason is StopReason.MANUAL:
        add["pachinko_autoplay_manual"] = 1
        if volleys == 1:
            add["pachinko_autoplay_quick_quit"] = 1
        if net > 0 and volleys >= MANUAL_EXIT_MIN_SPINS:
            add["pachinko_autoplay_exit_ahead"] = 1
    return delta


def crash_stats(seat: CrashSeat, *, crash_cents: int, players: int, last_out: bool) -> StatDelta:
    """Contadores de un jugador en una ronda de Crash ya pagada (sin lo común del casino).

    Args:
        seat: Su asiento, con el multiplicador al que se retiró (si lo hizo).
        crash_cents: Punto de explosión de la ronda.
        players: Jugadores de la ronda.
        last_out: Si fue el último en retirarse quedando gente dentro.
    """
    delta = StatDelta(add={"crash_rounds": 1}, peak={"crash_party_max": players})
    add = delta.add

    def bump(stat: str, condition: bool = True) -> None:
        if condition:
            add[stat] = add.get(stat, 0) + 1

    cashed = seat.cashed_cents
    if cashed is not None:
        bump("crash_cashouts")
        delta.peak["crash_cashout_max"] = cashed
        if seat.net > 0:
            delta.peak["crash_win_max"] = seat.net
        bump("crash_auto", seat.by_auto)
        # A menos de un 5 % de la explosión: 2,00x con explosión en 2,09x.
        bump("crash_close", crash_cents * 100 <= cashed * 105)
        bump("crash_last_out", last_out)
        bump("crash_cash_low", cashed <= 110)
        # Bajarse antes del 2x en una ronda que acabó pasando de 100x.
        bump("crash_missed_moon", cashed < 200 and crash_cents >= 10_000)
    else:
        bump("crash_instant", crash_cents == 100)
        bump("crash_greedy", crash_cents >= 1_000)
    bump("crash_moon", crash_cents >= 10_000)
    return delta


def horses_stats(
    *,
    card: HorseCard,
    result: HorseResult,
    pick: HorsePick,
    stake: int,
    odds_cents: int,
    net: int,
    pot_share: int,
    tip_horse: int,
    alone: bool,
    via: str,
    players: int,
    favourite: int,
    favourite_odds: int,
    photo: bool,
    comeback: bool,
    tax_delta: int,
    when: datetime,
    ready: bool = False,
    starter: bool = False,
    flash: bool = False,
) -> StatDelta:
    """Contadores de un boleto de las carreras ya pagado (sin lo común del casino).

    Args:
        card: La carrera (caballos, terreno, distancia).
        result: Cómo acabó.
        pick: El boleto.
        stake: Lo apostado.
        odds_cents: Su cuota, en centésimas.
        net: Ganado menos apostado (bote incluido).
        pot_share: Parte del bote del Gran Premio que se lleva.
        tip_horse: Caballo del pronóstico de Perro Sanxe.
        alone: Si nadie más apostó por su caballo (con 3 boletos o más).
        via: Cómo apostó (`sanxe`, `pueblo`, `azar`, `panel` o `comando`).
        players: Boletos de la carrera.
        favourite: Caballo con más probabilidad de ganar.
        favourite_odds: Cuota a ganador del favorito.
        photo: Si hubo foto-finish.
        comeback: Si su caballo iba último a mitad de carrera.
        tax_delta: IRPF retenido (positivo) o devuelto en la jugada.
        when: Hora local.
        ready: Si pulsó ✅ Listo.
        starter: Si su ✅ Listo fue el último y los caballos salieron por él.
        flash: Si la carrera salió con ✅ Listo a los pocos segundos de abrirse.
    """
    kind = pick.kind
    first = pick.horses[0]
    first_key = card.horses[first].key
    delta = StatDelta(
        add={
            "horse_bets": 1,
            f"horse_kind_{kind.key}": 1,
            f"horse_dist_{card.distance}": 1,
        },
        peak={"horse_party_max": players},
    )
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    for index in pick.horses:
        bump(f"{HORSE_BACKED_PREFIX}{card.horses[index].key}")
    bump("horse_falcon_bets", any(card.horses[i].key == "falcon" for i in pick.horses))
    bump("horse_gp_bets", card.grand_prix)
    bump(f"horse_via_{via}", via in ("sanxe", "pueblo", "azar"))
    bump("horse_ready", ready)
    bump("horse_starter", starter)
    bump("horse_flash", flash)
    night = 3 <= when.hour < 6
    bump("horse_night", night)
    backed = {card.horses[i].key for i in pick.horses}
    bump("horse_paguita_imv", stake == 1_500 and "paguita" in backed)
    bump("horse_colchon_night", night and "colchon" in backed)
    order = result.order
    single = kind is HorseBetKind.WIN
    position = result.position(first)
    if pick.wins(order):
        bump("horse_hits")
        bump(f"horse_hits_{kind.key}")
        delta.peak["horse_odds_max"] = odds_cents
        if net > 0:
            delta.peak["horse_win_max"] = net
        bump("horse_photo_wins", photo)
        bump("horse_rain_wins", result.rained)
        bump(
            "horse_mud_wins",
            card.going is HorseGoing.BARRO
            or (result.rained and card.going.wetter() is HorseGoing.BARRO),
        )
        bump("horse_lone_wins", alone)
        bump("horse_taxed", tax_delta > 0)
        if pot_share:
            bump("horse_pot_wins")
            delta.peak["horse_pot_max"] = pot_share
        if single:
            bump("horse_long_shots", odds_cents >= 1_000)
            bump("horse_tired_wins", first_key in card.tired)
            bump("horse_stumble_wins", first in result.stumbles)
            bump("horse_comebacks", comeback)
            bump("horse_manual_comeback", comeback and first_key == "manual")
            bump("horse_sanxe_hits", first == tip_horse)
            bump("horse_contra_sanxe", first != tip_horse)
            bump("horse_uco_wins", first_key == "uco")
            bump("horse_puerta_long", first_key == "puerta" and odds_cents >= 500)
            bump("horse_wepa_gp", first_key == "wepa" and card.grand_prix)
    else:
        if single and position == 2:
            bump("horse_seconds")
            bump("horse_nose_losses", result.lengths_behind(first, card.distance) < 0.15)
        real = tuple(order[: kind.picks])
        bump("horse_reversed", kind is HorseBetKind.EXACTA and real == pick.horses[::-1])
        bump(
            "horse_jumbled",
            kind is HorseBetKind.TRIFECTA and set(real) == set(pick.horses),
        )
    bump("horse_lasts", position == card.size)
    bump("horse_bolted", first in result.bolted)
    bump(
        "horse_fav_flops",
        first == favourite and favourite_odds <= 200 and position > 3,
    )
    return delta


def mines_stats(game: MinesGame) -> StatDelta:
    """Contadores de una partida de Minas terminada (sin lo común del casino)."""
    delta = StatDelta(
        add={"mines_games": 1, f"mines_level_{game.mines}": 1},
        peak={"mines_streak_max": game.gems},
    )
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    bump("mines_gems", amount=game.gems)
    bump("mines_random", amount=game.random_picks)
    if game.status is MinesStatus.CASHED:
        bump("mines_cashouts")
        delta.peak["mines_mult_max"] = game.cents
        if game.net > 0:
            delta.peak["mines_win_max"] = game.net
        bump("mines_24", game.mines == MINES_MAX)
        bump("mines_clear", game.cleared)
        bump("mines_clear_hard", game.cleared and game.mines >= 5)
        bump("mines_cash_one", game.gems == 1)
    elif game.status is MinesStatus.BUSTED:
        bump("mines_booms")
        # La primera casilla es segura: "a la primera" es la que va justo después.
        bump("mines_first_boom", game.gems == 1)
        bump("mines_almost", game.gems >= 1 and game.safe_total - game.gems == 1)
        # Explotar con ×10 o más ya ganado: lo que se llevaba se queda en la mesa.
        bump("mines_greedy", game.gems >= 1 and multiplier_cents(game.mines, game.gems) >= 1_000)
    return delta


def chicken_stats(game: ChickenGame, *, vehicle: str | None = None) -> StatDelta:
    """Contadores de una partida del Pollo terminada (sin lo común del casino).

    Args:
        game: La partida terminada.
        vehicle: Tipo de vehículo que atropelló (`CHICKEN_VEHICLE_KINDS`), si
            hubo atropello.
    """
    key = game.difficulty.key
    delta = StatDelta(
        add={"chicken_games": 1, f"chicken_games_{key}": 1},
        peak={f"chicken_lanes_max_{key}": game.crossed},
    )
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    bump("chicken_lanes", amount=game.crossed)
    bump("chicken_auto_lanes", amount=game.auto_lanes)
    bump("chicken_auto_runs", game.auto_target is not None)
    if game.status is ChickenStatus.CASHED:
        bump("chicken_cashouts")
        delta.peak["chicken_mult_max"] = game.cents
        if game.net > 0:
            delta.peak["chicken_win_max"] = game.net
        bump(f"chicken_finish_{key}", game.finished_road)
        bump("chicken_hardcore_cashouts", key == "hardcore")
        bump("chicken_gallina", game.crossed == 1)
        left = game.free_lanes_left
        if not game.finished_road:
            bump("chicken_close", left == 0)
            bump("chicken_left_on_table", left is not None and left >= 5)
            bump("chicken_road_free", left is None)
    elif game.status is ChickenStatus.SPLAT:
        bump("chicken_splats")
        bump("chicken_first_splat", game.crossed == 0)
        bump("chicken_last_lane_splat", game.crossed == game.lanes - 1)
        bump("chicken_lost_big", game.cents >= 1_000)
        if vehicle in CHICKEN_VEHICLE_KINDS:
            bump(f"chicken_hit_{vehicle}")
    return delta


def coin_stats(game: CoinGame, *, when: datetime) -> StatDelta:
    """Contadores de una partida de cara o cruz terminada (sin lo común del casino).

    Args:
        game: La partida terminada.
        when: Hora local.
    """
    delta = StatDelta(
        add={"coin_games": 1, "coin_flips": len(game.flips)},
        peak={"coin_streak_max": game.wins},
    )
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    won = [f for f in game.flips if f.won]
    bump("coin_wins", amount=len(won))
    bump("coin_wins_cara", amount=sum(1 for f in won if f.pick is CoinSide.CARA))
    bump("coin_wins_cruz", amount=sum(1 for f in won if f.pick is CoinSide.CRUZ))
    picks = [f.pick for f in won]
    if len(won) >= 5 and len(set(picks)) == 1:
        bump("coin_loyal_cara" if picks[0] is CoinSide.CARA else "coin_loyal_cruz")
    bump(
        "coin_flipflop",
        len(won) >= 4 and all(a is not b for a, b in zip(picks, picks[1:], strict=False)),
    )
    if game.status is CoinStatus.CASHED:
        bump("coin_cashouts")
        delta.peak["coin_cash_mult_max"] = game.multiplier
        if game.net > 0:
            delta.peak["coin_win_max"] = game.net
        bump("coin_finish", game.maxed)
        bump("coin_gallina", game.wins == 1)
        bump("coin_next_edge", not game.maxed and game.upcoming is CoinOutcome.EDGE)
        bump("coin_cash_666", game.payout == 666)
    elif game.status is CoinStatus.LOST:
        bump("coin_losses")
        bump("coin_first_fail", game.wins == 0)
        bump("coin_lost_big", game.multiplier >= 16)
    elif game.status is CoinStatus.EDGE:
        bump("coin_edges")
        bump("coin_edge_big", game.multiplier >= 8)
        bump("coin_first_edge", len(game.flips) == 1)
        bump("coin_edge_lost", amount=game.pot)
    bump("coin_night", 2 <= when.hour < 6)
    bump("coin_hispanidad", when.month == 10 and when.day == 12)
    bump("coin_nochevieja", when.month == 12 and when.day == 31)
    bump("coin_friday13", game.net < 0 and when.weekday() == 4 and when.day == 13)
    return delta


def bus_stats(game: BusGame, *, when: datetime) -> StatDelta:
    """Contadores de una partida del Autobús terminada (sin lo común del casino).

    Args:
        game: La partida terminada.
        when: Hora local.
    """
    delta = StatDelta(add={"bus_games": 1, "bus_hands": len(game.guesses)})
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    won = [g for g in game.guesses if g.won]
    bump("bus_wins", amount=len(won))
    table = []
    for guess in game.guesses:
        best = max(bus_chance(p, table) for p in bus_picks_for(guess.pick.hand))
        if guess.won:
            bump(f"{BUS_WIN_PREFIX}{guess.pick.key}")
            bump("bus_suit_wins", guess.pick.hand is BusHand.SUIT)
            bump("bus_longshots", guess.chance <= BUS_LONGSHOT)
            bump("bus_contrarian", guess.chance < best)
        else:
            bump("bus_sure_fail", guess.chance >= BUS_SURE)
        table.append(guess.card)
    picks = [g.pick for g in won]
    bump("bus_trio", BusPick.EQUAL in picks and BusPick.POST in picks)
    cards = game.table
    bump("bus_all_red", len(cards) >= BUS_HANDS and all(c.is_red for c in cards[:BUS_HANDS]))
    bump("bus_complete", game.completed)
    bump("bus_turned", game.turned)
    bump("bus_min_stake", game.stake == 1)
    if game.status is BusStatus.CASHED:
        bump("bus_cashouts")
        delta.peak["bus_mult_max"] = math.floor(game.multiplier * 100)
        if game.net > 0:
            delta.peak["bus_win_max"] = game.net
        bump("bus_gallina", len(won) == 1)
        bump("bus_cash_666", game.payout == 666)
        bump("bus_cash_69", game.payout == 69)
        missed = game.missed()
        bump(
            "bus_missed_longshot",
            bool(missed) and bus_chance(missed[0], cards) <= BUS_LONGSHOT,
        )
    elif game.status is BusStatus.LOST:
        bump("bus_losses")
        last = len(game.guesses)
        bump("bus_first_fail", last == 1)
        bump("bus_last_fail", last == BUS_HANDS)
        bump("bus_turn_lost", last == BUS_HANDS + 1)
        bump("bus_lost_big", game.multiplier >= BUS_LOST_BIG)
        bump("bus_friday13", when.weekday() == 4 and when.day == 13)
    bump("bus_night", 2 <= when.hour < 6)
    bump("bus_rush", when.weekday() < 5 and 7 <= when.hour < 9)
    bump("bus_canarias", when.month == 5 and when.day == 30)
    return delta


#: Racha de la carretera (manos seguidas de un lado) a partir de la cual hay «dragón».
BACCARAT_DRAGON = 5
#: Manos alternas (Banca, Jugador, Banca…) para el ping-pong.
BACCARAT_PINGPONG = 6


def gallos_stats(
    *,
    fight: GallosFight,
    corner: GallosCorner,
    rival: str | None,
    chance: float,
    stake: int,
    net: int,
    record: RoosterRecord,
    skin: str,
    paid_skin: bool,
    crowd_pool: int,
    rake: int,
    when: datetime,
) -> StatDelta:
    """Contadores de una pelea de gallos para uno de los dos dueños (sin lo común del casino).

    Args:
        fight: La pelea.
        corner: La esquina de este dueño.
        rival: Clave del gallo de la casa (`gallos.HOUSE`), o `None` en un duelo.
        chance: Probabilidad que tenía de ganar.
        stake: Lo apostado.
        net: Ganado menos apostado.
        record: La ficha de su gallo tras la pelea.
        skin: Clave de la skin con la que peleó (`serie` si ninguna).
        paid_skin: Si esa skin es de las de la tienda (ni de serie ni gratis).
        crowd_pool: Lo que apostó el público en el duelo (0 contra la casa).
        rake: Comisión de la casa que se ha dejado (0 contra la casa).
        when: Hora local.
    """
    won = fight.winner is corner
    mine = corner
    hits = fight.hits(mine)
    delta = StatDelta(
        add={"gallos_fights": 1, f"{GALLOS_SKIN_PREFIX}{skin}": 1},
        peak={
            "gallos_streak_max": record.best_streak,
            "gallos_days_max": record.day_streak,
        },
    )
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    bump("gallos_wins", won)
    bump("gallos_losses", not won)
    bump("gallos_crits", amount=fight.crits(mine))
    bump("gallos_dodges", amount=fight.dodges(mine))
    if hits:
        delta.peak["gallos_damage_max"] = max(e.damage for e in hits)
    if won:
        bump("gallos_kos", fight.finish is GallosFinish.KO)
        bump("gallos_made_flee", fight.finish is GallosFinish.FLED)
        bump("gallos_points_wins", fight.finish is GallosFinish.POINTS)
        bump("gallos_flawless", fight.damage_taken(mine) == 0)
        bump("gallos_close_win", fight.hp(mine) <= 10)
        bump("gallos_blitz", fight.finish is GallosFinish.KO and fight.rounds == 1)
        bump("gallos_paid_skin_win", paid_skin)
        if net > 0:
            delta.peak["gallos_win_max"] = net
    else:
        bump("gallos_ko_taken", fight.finish is GallosFinish.KO)
        bump("gallos_fled", fight.finish is GallosFinish.FLED)
        bump("gallos_favourite_loss", chance >= 0.8)
        bump("gallos_friday13", when.weekday() == 4 and when.day == 13)
    if rival is not None:
        bump(f"{GALLOS_BEAT_PREFIX}{rival}", won)
        bump("gallos_temu_loss", not won and rival == "temu")
    else:
        bump("gallos_duels")
        bump("gallos_duel_wins", won)
        bump("gallos_duel_big", stake >= 10_000)
        if crowd_pool:
            delta.peak["gallos_crowd_max"] = crowd_pool
    bump("gallos_rake", amount=rake)
    bump("gallos_stake_155", stake == 155)
    bump("gallos_hispanidad", when.month == 10 and when.day == 12)
    bump("gallos_canarias", when.month == 5 and when.day == 30)
    bump("gallos_july4", when.month == 7 and when.day == 4)
    bump("gallos_china_day", when.month == 10 and when.day == 1)
    bump("gallos_europe_day", when.month == 5 and when.day == 9)
    bump("gallos_africa_day", when.month == 5 and when.day == 25)
    bump("gallos_night", 2 <= when.hour < 6)
    bump("gallos_dawn", 6 <= when.hour < 8)
    bump("gallos_nochevieja", when.month == 12 and when.day == 31)
    return delta


def gallos_bet_stats(
    *, won: bool, refunded: bool, odds_cents: int, rake: int, when: datetime
) -> StatDelta:
    """Contadores de una apuesta de grada en un duelo de gallos (sin lo común del casino).

    Args:
        won: Si acertó.
        refunded: Si se devolvió porque nadie apostó por el otro gallo.
        odds_cents: Cuota cobrada, en centésimas (0 si no cobró).
        rake: Comisión de la casa que se ha dejado.
        when: Hora local.
    """
    delta = StatDelta(add={"gallos_bets": 1})
    if refunded:
        delta.add["gallos_bet_refund"] = 1
    elif won:
        delta.add["gallos_bets_won"] = 1
        delta.peak["gallos_bet_odds_max"] = odds_cents
    if rake:
        delta.add["gallos_rake"] = rake
    if 2 <= when.hour < 6:
        delta.add["gallos_night"] = 1
    return delta


def baccarat_stats(
    coup: BaccaratCoup,
    *,
    bets: Mapping[BaccaratSide, int],
    prize: int,
    before: Sequence[BaccaratResult],
    players: int = 1,
    alone_win: bool = False,
    herd_loss: bool = False,
    starter: bool = False,
    shoe_hands: int = 1,
    win_streak: int = 0,
    when: datetime,
) -> StatDelta:
    """Contadores de una mano de bacará para un miembro (sin lo común del casino).

    Args:
        coup: La mano repartida.
        bets: Lo que puso en cada lado (solo los lados con apuesta).
        prize: Lo que le devuelve la mano (apuesta incluida).
        before: Resultados del zapato antes de esta mano (para el dragón y el ping-pong).
        players: Personas con apuesta en la mano.
        alone_win: Ganó siendo el único de su lado, con dos o más en el contrario.
        herd_loss: Perdió en una mano en la que los 3 o más de la mesa iban al mismo lado.
        starter: Pulsó el ¡Cartas! que hizo repartir.
        shoe_hands: Manos que lleva en este zapato, contando esta.
        win_streak: Manos de bacará ganadas seguidas, contando esta (0 si no gana).
        when: Hora local.
    """
    stake = sum(bets.values())
    net = prize - stake
    winner = coup.winner
    after = [*before, BaccaratResult.of(coup)]
    road = baccarat_road_stats(after)
    delta = StatDelta(
        add={"bac_hands": 1},
        peak={
            "bac_table_max": players,
            "bac_shoe_hands_max": shoe_hands,
            "bac_road_longest": road.longest,
            "bac_win_streak_max": win_streak,
        },
    )
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    main = [s for s in (BaccaratSide.BANKER, BaccaratSide.PLAYER) if s in bets]
    won_sides = [s for s in bets if s is winner]
    bump("bac_wins", net > 0)
    bump("bac_losses", net < 0)
    if net > 0:
        delta.peak["bac_win_max"] = net
    bump("bac_banker_wins", winner is BaccaratSide.BANKER and BaccaratSide.BANKER in bets)
    bump("bac_player_wins", winner is BaccaratSide.PLAYER and BaccaratSide.PLAYER in bets)
    bump("bac_tie_wins", winner is BaccaratSide.TIE and BaccaratSide.TIE in bets)
    bump("bac_pushes", winner is BaccaratSide.TIE and bool(main))
    for side in won_sides:
        if side is BaccaratSide.TIE:
            continue
        natural = coup.natural_of(side)
        bump("bac_naturals_won", natural is not None)
        bump("bac_nine_wins", natural == 9)
        bump("bac_comebacks", coup.comeback(side))
        cards = coup.player if side is BaccaratSide.PLAYER else coup.banker
        bump("bac_squeeze_nine", len(cards) == 3 and baccarat_points(cards) == 9)
    for side in main:
        if winner not in (side, BaccaratSide.TIE):
            bump("bac_lost_by_one", coup.margin == 1)
            other = BaccaratSide.PLAYER if side is BaccaratSide.BANKER else BaccaratSide.BANKER
            bump("bac_natural_beaten", coup.natural_of(side) == 8 and coup.natural_of(other) == 9)
    bump("bac_six_cards", coup.cards == 6)
    bump("bac_tie_zero", winner is BaccaratSide.TIE and coup.player_total == 0)
    bump(
        "bac_natural_tie",
        winner is BaccaratSide.TIE
        and coup.natural_of(BaccaratSide.PLAYER) is not None
        and coup.natural_of(BaccaratSide.BANKER) is not None,
    )
    # El dragón: la racha que había antes de esta mano.
    previous = baccarat_road_stats(before)
    if previous.streak >= BACCARAT_DRAGON and previous.streak_side is not None:
        streak_side = previous.streak_side
        against = BaccaratSide.PLAYER if streak_side is BaccaratSide.BANKER else BaccaratSide.BANKER
        bump("bac_dragon_ride", streak_side in bets and winner is streak_side)
        bump("bac_dragon_slay", against in bets and winner is against)
    bump(
        "bac_pingpong",
        net > 0 and winner is not BaccaratSide.TIE and baccarat_chop_run(after) > BACCARAT_PINGPONG,
    )
    bump("bac_alone_win", alone_win)
    bump("bac_herd_loss", herd_loss)
    bump("bac_starter", starter)
    bump("bac_hedge", BaccaratSide.BANKER in bets and BaccaratSide.PLAYER in bets)
    bump("bac_all_three", len(bets) == 3)
    bump("bac_shoe_first", coup.number == 1)
    bump("bac_shoe_last", coup.last)
    if winner is BaccaratSide.BANKER and BaccaratSide.BANKER in bets:
        bump("bac_commission", amount=baccarat_commission(bets[BaccaratSide.BANKER]))
    bump("bac_banker_min", bets.get(BaccaratSide.BANKER) == BACCARAT_BANKER_MIN)
    bump("bac_night", 2 <= when.hour < 6)
    bump("bac_newyear", when.month == 12 and when.day == 31)
    bump("bac_pino", when.month == 9 and when.day == 8)
    bump("bac_friday13", net < 0 and when.weekday() == 4 and when.day == 13)
    return delta


def craps_stats(game: CrapsGame, hand: Hand, *, when: datetime) -> StatDelta:
    """Contadores de una partida de dados terminada (sin lo común del casino).

    Args:
        game: La partida terminada.
        hand: La mano del tirador tras la partida (con sus tiradas ya
            apuntadas): da los récords de la mano caliente.
        when: Hora local.
    """
    rolls = game.rolls
    delta = StatDelta(
        add={"dice_games": 1, "dice_rolls": len(rolls)},
        peak={
            "dice_game_rolls_max": len(rolls),
            "dice_hand_points_max": len(hand.points),
            "dice_hand_rolls_max": hand.rolls,
            "dice_fire_max": hand.distinct_points,
            "dice_repeat_max": hand.repeat_max,
        },
    )
    add = delta.add

    def bump(stat: str, condition: bool = True, amount: int = 1) -> None:
        if condition and amount:
            add[stat] = add.get(stat, 0) + amount

    passing = game.bet is CrapsBet.PASS
    bump("dice_pass_games" if passing else "dice_dont_games")
    for roll in rolls:
        bump(f"dice_total_{roll.total}")
        bump("dice_snake_eyes", roll.dice == (1, 1))
        bump("dice_boxcars", roll.dice == (6, 6))
    first, last = rolls[0], rolls[-1]
    bump("dice_naturals", first.total in CRAPS_NATURALS)
    bump("dice_craps_rolls", first.total in CRAPS_CRAPS)
    bump("dice_elevens", first.total == 11)
    bump("dice_points_set", game.point is not None)
    if last.made:
        bump("dice_points_made")
        bump(f"dice_point_made_{last.total}")
        if last.hard:
            bump("dice_hard")
            bump(f"dice_hard_{last.total}")
    if last.seven_out:
        bump("dice_seven_outs")
        bump("dice_seven_first", len(rolls) == 2)
    if game.odds:
        bump("dice_odds_games")
        bump("dice_odds_full", game.odds_full)
    if game.status is CrapsStatus.WON:
        bump("dice_wins")
        bump("dice_pass_wins" if passing else "dice_dont_wins")
        bump("dice_dont_seven", not passing and last.seven_out)
        bump("dice_odds_full_won", game.odds_full)
        bump("dice_profit", amount=game.net)
        bump("dice_cash_777", game.payout == 777)
        delta.peak["dice_win_max"] = game.net
    elif game.status is CrapsStatus.LOST:
        bump("dice_losses")
        bump("dice_long_lost", len(rolls) >= 10)
        bump("dice_odds_full_lost", game.odds_full)
        bump("dice_boxcars_lost", passing and len(rolls) == 1 and first.total == 12)
    elif game.status is CrapsStatus.PUSH:
        bump("dice_dont_bar")
    bump("dice_night", 2 <= when.hour < 6)
    bump("dice_canarias", when.month == 5 and when.day == 30)
    bump("dice_nochevieja", when.month == 12 and when.day == 31)
    bump("dice_inocentes", when.month == 12 and when.day == 28)
    bump("dice_friday13", game.net < 0 and when.weekday() == 4 and when.day == 13)
    return delta


def shop_stats(
    *,
    kind: str,
    total: int,
    tax: int,
    discount_pct: int,
    luxury: bool,
    serial: int | None,
    last_unit: bool,
    renewed: bool,
    collection: int,
    queued_boosts: int,
    balance_after: int,
    catalog_key: str | None = None,
    aisle: str | None = None,
    title: bool = False,
    skin: bool = False,
) -> StatDelta:
    """Estadísticas de una compra de la tienda.

    Args:
        kind: Tipo de artículo (`"rol"`, `"xp"` u `"objeto"`).
        total: Lo pagado, IGIC incluido.
        tax: IGIC pagado.
        discount_pct: Rebaja aplicada en %.
        luxury: Si pagó el IGIC de lujo.
        serial: Número de serie de la unidad, si era limitada.
        last_unit: Si se llevó la última unidad.
        renewed: Si alargó un alquiler de rol.
        collection: Coleccionables distintos que tiene tras comprar.
        queued_boosts: Potenciadores suyos sin acabar (en marcha o en cola).
        balance_after: Saldo tras pagar.
        catalog_key: Clave del surtido de serie, si viene de ahí.
        aisle: Pasillo del colmado del artículo.
        title: Si es un título. Hay uno de cada por persona, así que contar
            compras es contar títulos distintos.
        skin: Si es una skin de una mesa del casino (también una por persona).
    """
    delta = StatDelta(
        add={"shop_purchases": 1, "shop_spent": total, "shop_igic": tax},
        peak={"shop_big_buy_max": total},
    )

    def bump(stat: str, condition: bool = True) -> None:
        if condition:
            delta.add[stat] = delta.add.get(stat, 0) + 1

    bump("shop_roles", kind == "rol")
    bump("shop_renewals", renewed)
    bump("shop_boosts", kind == "xp")
    bump("shop_sale_buys", discount_pct > 0)
    bump("shop_luxury", luxury)
    bump("shop_limited", serial is not None)
    bump("shop_first_serial", serial == 1)
    bump("shop_last_unit", last_unit)
    bump("shop_broke_buy", balance_after == 0)
    bump("shop_titles", title)
    bump("shop_skins", skin)
    if discount_pct:
        delta.peak["shop_discount_max"] = discount_pct
    if kind == "objeto":
        delta.peak["shop_collection_max"] = collection
    if kind == "xp":
        delta.peak["shop_boost_queue_max"] = queued_boosts
    if aisle in _SHOP_AISLE_KEYS:
        bump(f"{SHOP_AISLE_PREFIX}{aisle}")
    if catalog_key in SHOP_TRACKED_KEYS:
        bump(f"shop_key_{catalog_key}")
    return delta


def shop_use_stats(
    *,
    use: str,
    flags: Iterable[str],
    consumed: bool,
    targeted: bool,
    at_self: bool,
    at_bot: bool,
    prize_price: int | None,
    collection: int,
    when: float,
) -> StatDelta:
    """Estadísticas de quien usa un objeto de la tienda.

    Args:
        use: Clave del uso (`bot.services.shop_uses.USES`).
        flags: Marcas del desenlace (`"hit"`, `"backfire"`, `"nat20"`…).
        consumed: Si el objeto se ha gastado.
        targeted: Si se ha usado contra otro miembro (ni uno mismo ni un bot).
        at_self: Si se lo ha aplicado a sí mismo.
        at_bot: Si ha ido contra un bot.
        prize_price: Precio del premio, si era una caja botín.
        collection: Objetos distintos que tiene tras usarlo.
        when: Momento del uso (epoch).
    """
    delta = StatDelta(add={"shop_uses": 1, f"{SHOP_USED_PREFIX}{use}": 1})

    def bump(stat: str, condition: bool = True) -> None:
        if condition:
            delta.add[stat] = delta.add.get(stat, 0) + 1

    marks = set(flags)
    bump("shop_consumed", consumed)
    bump("shop_use_targeted", targeted)
    bump("shop_use_self", at_self)
    bump("shop_use_bot", at_bot)
    bump("shop_use_hits", "hit" in marks)
    bump("shop_use_backfires", "backfire" in marks)
    bump("shop_egg_collector", "collector" in marks)
    bump("shop_caught", "caught" in marks)
    bump("shop_d20_nat20", "nat20" in marks)
    bump("shop_d20_nat1", "nat1" in marks)
    bump("shop_padron_hot", "hot" in marks)
    bump("shop_robuso_asleep", "asleep" in marks)
    bump("shop_nickname", "nickname" in marks)
    bump("shop_clover_broken", "broken" in marks)
    bump("shop_no_parsley", "no_parsley" in marks)
    bump("shop_flash", "flash" in marks)
    bump("shop_fake_champagne", "fake" in marks)
    bump("shop_ball_dogs", "dogs" in marks)
    if prize_price is not None:
        bump("shop_mystery")
        bump("shop_mystery_jackpot", "jackpot" in marks)
        bump("shop_mystery_dupe", "dupe" in marks)
        delta.peak["shop_mystery_best"] = prize_price
        delta.peak["shop_collection_max"] = collection
    moment = datetime.fromtimestamp(when, TIMEZONE)
    bump("shop_use_night", 3 <= moment.hour < 6)
    bump("shop_use_newyear", (moment.month, moment.day) in {(12, 31), (1, 1)})
    bump("shop_use_halloween", (moment.month, moment.day) == (10, 31))
    bump("shop_use_canarias", (moment.month, moment.day) == (5, 30))
    bump("shop_use_pino", (moment.month, moment.day) == (9, 8))
    return delta


# -- Mascotas -------------------------------------------------------------------------


def _pet_name_marks(name: str) -> set[str]:
    """Nombres con guasa: ponerle «Perro Sanxe» o «Pedro» a la mascota, o un 666."""
    plain = re.sub(r"[^a-z0-9]", "", name.lower().replace("á", "a").replace("é", "e"))
    marks = set()
    if "sanxe" in plain or "sanchez" in plain or plain == "pedro":
        marks.add("pet_named_sanxe")
    if "666" in plain:
        marks.add("pet_named_beast")
    return marks


def pet_adopt_stats(
    *, species: str, spawned: bool, owned: int, adoption: bool = False
) -> StatDelta:
    """Estadísticas de quien consigue una mascota.

    Args:
        species: Clave de la especie (`bot.services.pets_catalog.SPECIES`).
        spawned: Si ha aparecido sola (si no, la ha adoptado en la tienda).
        owned: Mascotas que tiene tras conseguirla.
        adoption: Si era de las de tasa de adopción (perros, gatos, hurones).
    """
    delta = StatDelta(add={f"{PET_SPECIES_PREFIX}{species}": 1}, peak={"pet_owned_max": owned})
    delta.add["pet_spawned" if spawned else "pet_adopted"] = 1
    if adoption:
        delta.add["pet_protectora"] = 1
    return delta


def pet_care_stats(
    *,
    action: str,
    points: int,
    favourite: bool,
    level: int,
    tricks: int,
    streak: int,
    gift: bool,
    when: float,
    food_key: str | None = None,
    species: str = "",
    eats: bool = True,
) -> StatDelta:
    """Estadísticas de un cuidado (acariciar, jugar o dar de comer).

    Solo cuentan para los escalones los cuidados que suman vínculo: el resto
    (pasado el cupo del día) se puede hacer sin límite y no debe dar logros.

    Args:
        action: `bot.services.pets.Care` (`"acariciar"`, `"jugar"`, `"comer"`).
        points: Vínculo que ha sumado.
        favourite: Si le ha dado su comida favorita.
        level: Nivel de vínculo tras el cuidado.
        tricks: Trucos que sabe tras el cuidado.
        streak: Días seguidos cuidando alguna mascota.
        gift: Si ha traído un regalo.
        food_key: Clave de lo que se ha comido, si ha comido.
        species: Especie cuidada.
        eats: Si la especie come (darle de comer a una piedra tiene logro).
    """
    delta = StatDelta(peak={"pet_bond_max": level, "pet_tricks_max": tricks,
                            "pet_streak_max": streak})  # fmt: skip

    def bump(stat: str, condition: bool = True) -> None:
        if condition:
            delta.add[stat] = delta.add.get(stat, 0) + 1

    if points > 0:
        bump("pet_cares")
        bump({"acariciar": "pet_petted", "jugar": "pet_played", "comer": "pet_fed"}[action])
    bump("pet_favourite", favourite)
    bump("pet_gifts", gift)
    bump("pet_fed_nothing", action == "comer" and not eats)
    bump("pet_goat_tax", species == "cabra" and food_key == "modelo_100")
    bump("pet_goat_odd", species == "cabra" and food_key is not None and food_key != "modelo_100")
    moment = datetime.fromtimestamp(when, TIMEZONE)
    bump("pet_night", 3 <= moment.hour < 6)
    bump("pet_san_anton", (moment.month, moment.day) == (1, 17))
    bump("pet_christmas", (moment.month, moment.day) in {(12, 24), (12, 25)})
    bump("pet_halloween", (moment.month, moment.day) == (10, 31))
    bump("pet_canarias", (moment.month, moment.day) == (5, 30))
    return delta


def pet_cameo_stats(*, event: str, when: float) -> StatDelta:
    """Estadísticas de que la mascota activa salga en un mensaje del bot.

    Args:
        event: `bot.services.pets.Event` del momento.
    """
    delta = StatDelta(add={"pet_cameos": 1})
    if event == "bust":
        delta.add["pet_cameo_bust"] = 1
    if event == "big_win":
        delta.add["pet_cameo_big"] = 1
    if datetime.fromtimestamp(when, TIMEZONE).hour < 6:
        delta.add["pet_cameo_night"] = 1
    return delta


def pet_switch_stats() -> StatDelta:
    """Estadísticas de cambiar de mascota activa."""
    return StatDelta(add={"pet_switches": 1})


def pet_name_stats(*, name: str) -> StatDelta:
    """Estadísticas de ponerle nombre a una mascota."""
    delta = StatDelta(add={"pet_renamed": 1})
    for mark in _pet_name_marks(name):
        delta.add[mark] = 1
    return delta


def shop_title_stats(*, worn: bool) -> StatDelta:
    """Estadísticas de quien cambia el título que luce en la mochila.

    Args:
        worn: Si se ha puesto uno (falso si se ha quedado sin título).
    """
    delta = StatDelta(add={"shop_title_swaps": 1})
    if not worn:
        delta.add["shop_title_off"] = 1
    return delta


def shop_skin_stats(*, swapped: bool) -> StatDelta:
    """Estadísticas de quien cambia la skin de una mesa (mochila o botón 🎨).

    Args:
        swapped: Si ha cambiado de verdad (elegir la que ya llevaba no cuenta).
    """
    return StatDelta(add={"shop_skin_swaps": 1} if swapped else {})


def shop_hit_stats(*, use: str) -> StatDelta:
    """Estadísticas de quien recibe un objeto de la tienda (un huevo, un ramo…)."""
    delta = StatDelta(add={"shop_got_hit": 1})
    if use in SHOP_MESSY_USES:
        delta.add["shop_got_messy"] = 1
    if use in SHOP_LOVE_USES:
        delta.add["shop_got_love"] = 1
    if use == "uco":
        delta.add["shop_got_raided"] = 1
    return delta


# -- Bizum ----------------------------------------------------------------------------


def bizum_stats(*, amount: int, sent_today: int, balance_after: int) -> StatDelta:
    """Estadísticas de quien manda un Bizum.

    Args:
        amount: Lo enviado.
        sent_today: Lo enviado hoy, este Bizum incluido.
        balance_after: Saldo de quien envía tras enviar.
    """
    delta = StatDelta(
        add={"bizum_sent_count": 1, "bizum_sent": amount},
        peak={"bizum_max": amount, "bizum_day_max": sent_today},
    )
    if amount == BIZUM_MAX_OPERATION:
        delta.add["bizum_full"] = 1
    if amount == BIZUM_MIN_AMOUNT:
        delta.add["bizum_min"] = 1
    if balance_after == 0:
        delta.add["bizum_broke"] = 1
    return delta


def bizum_received_stats(*, amount: int, balance_after: int) -> StatDelta:
    """Estadísticas de quien recibe un Bizum."""
    return StatDelta(
        add={"bizum_received_count": 1, "bizum_received": amount},
        peak={"balance_max": balance_after},
    )


# -- Loterías --------------------------------------------------------------------------

#: Etiquetas de un boleto premiado que cuentan para logros concretos.
LOTTERY_TAGS = frozenset({"reintegro", "pedrea", "gordo_navidad", "jackpot", "lotto4", "lotto5"})


def lottery_buy_stats(
    *, game: str, units: int, cost: int, owned_in_draw: int, balance_after: int
) -> StatDelta:
    """Estadísticas de una compra de décimos o apuestas.

    Args:
        game: Clave del juego (`bot.services.lottery.GAMES`).
        units: Décimos o apuestas comprados.
        cost: Lo pagado.
        owned_in_draw: Décimos o apuestas del miembro en ese sorteo tras comprar.
        balance_after: Saldo tras pagar.
    """
    delta = StatDelta(
        add={"lottery_bets": units, "lottery_spent": cost},
        peak={"lottery_draw_bets_max": owned_in_draw},
    )
    per_game = {
        "navidad": "lottery_navidad",
        "nino": "lottery_nino",
        "euromillones": "lottery_euro_bets",
    }
    if game in per_game:
        delta.add[per_game[game]] = units
    delta.add[f"lottery_game_{game}"] = units
    if balance_after == 0:
        delta.add["lottery_broke_buy"] = 1
    return delta


def lottery_prize_stats(prizes: Iterable[tuple[int, int, frozenset[str]]]) -> StatDelta:
    """Estadísticas de los boletos premiados de un miembro en un sorteo.

    Args:
        prizes: `(premio bruto, gravamen, etiquetas)` de cada boleto premiado.
            Las etiquetas salen de `LOTTERY_TAGS`.
    """
    delta = StatDelta()
    for gross, tax, tags in prizes:
        delta.merge(
            StatDelta(
                add={"lottery_prizes": 1, "lottery_won": gross},
                peak={"lottery_win_max": gross},
            )
        )
        for tag in tags & LOTTERY_TAGS:
            stat = "lottery_reintegros" if tag == "reintegro" else f"lottery_{tag}"
            delta.merge(StatDelta(add={stat: 1}))
        if tax:
            delta.merge(StatDelta(add={"lottery_gravamen": tax, "tax_paid": tax}))
    return delta


def scratch_stats(*, cost: int, prize: int, tax: int, top: bool, balance_after: int) -> StatDelta:
    """Estadísticas de un rasca.

    Args:
        cost: Precio del rasca.
        prize: Premio bruto (0 si no toca).
        tax: Gravamen especial pagado.
        top: Si es el premio más alto de su tabla.
        balance_after: Saldo tras cobrar el premio.
    """
    delta = StatDelta(add={"lottery_scratches": 1, "lottery_spent": cost})
    if balance_after == 0:
        delta.add["lottery_broke_buy"] = 1
    if prize:
        delta.merge(lottery_prize_stats([(prize, tax, frozenset())]))
    if top:
        delta.add["lottery_scratch_top"] = 1
    return delta


# -- Trabajo (`pala`) --------------------------------------------------------------------

#: Contadores de logros por contenido de minijuego: `contenido → estadística`.
_WORK_CONTENT_STATS = {
    "camilla": "work_stretcher",
    "ronda": "work_rounds",
    "triaje": "work_triage",
    "mir": "work_mir",
    "consulta": "work_google",
    "cafes": "work_coffee_orders",
    "bugs": "work_bugs",
    "revisiones": "work_reviews",
    "reuniones": "work_meetings",
    "inversores": "work_pitches",
    "vagos": "work_slackers",
    "sobrecostes": "work_overruns",
    "platos": "work_perfect_orders",
    "comandas": "work_perfect_orders",
    "cocina": "work_perfect_orders",
    "votos": "work_perfect_votes",
    "carteles": "work_posters",
    "chiringuito": "work_happy_clients",
    "prensa": "work_dodged",
    "comision": "work_no_recuerdo",
    "consejo": "work_board",
    "pedidos": "work_orders",
    "entregas": "work_routes",
    "flota": "work_fleet",
    "fundador": "work_founder",
    "ventanilla": "work_ventanilla",
    "registro": "work_registro",
    "sellos": "work_stamps",
    "ciudadano": "work_citizens",
    "inspeccion": "work_frauds",
    "baile": "work_dances",
    "metricas": "work_metrics",
    "directo": "work_stream",
    "colaboraciones": "work_collabs",
    "plato": "work_tv",
    "portavoz": "work_spokes",
    "decretos": "work_decrees",
    "ministerio": "work_ministry",
    "socios": "work_partners",
    "control": "work_control",
    "plenos": "work_plenary",
    "enmiendas": "work_amendments",
    "lobbies": "work_lobbies",
    "trilogo": "work_trilogues",
    "comisario": "work_commission",
    "pisos": "work_flats",
    "terrenos": "work_land",
    "banco": "work_bank",
    "alcalde": "work_mayor",
    "palco": "work_box",
    "licitaciones": "work_tenders",
    "concesiones": "work_concessions",
    "fomento": "work_ministry_deals",
    "internacional": "work_abroad_deals",
    "quirofano": "work_surgeries",
    "derivaciones": "work_referrals",
    "direccion": "work_medical_director",
    "gerencia": "work_hospital_manager",
    "consejeria": "work_health_minister",
    "letra_pequena": "work_fine_print",
    "retoques": "work_touchups",
    "clinica": "work_clinic",
    "conciertos": "work_health_deals",
    "aseguradora": "work_insurer",
}


def work_stats(outcome: ShiftOutcome, *, birthday: bool = False) -> StatDelta:
    """Estadísticas de un turno de `pala`.

    Args:
        outcome: Lo que ha pasado en el turno.
        birthday: Si el miembro ha fichado el día de su cumpleaños.
    """
    if outcome.kind.value == "estudio":
        # Estudiar no es fichar: no cuenta como turno ni para las rachas.
        study = StatDelta(add={"work_study": 1})
        if outcome.temario:
            study.add["work_temario"] = outcome.temario
        return study
    delta = StatDelta(
        add={"work_shifts": 1},
        peak={
            "work_shifts_day_max": outcome.shifts_today,
            "work_streak_max": outcome.streak_days,
            "balance_max": outcome.balance,
        },
    )
    add = delta.add
    if outcome.score >= 100:
        add["work_perfect"] = 1
    if outcome.score >= 90:
        add["work_good"] = 1
    if outcome.night:
        add["work_night"] = 1
    if outcome.sunday:
        add["work_sunday"] = 1
    if birthday:
        add["work_birthday"] = 1
    if outcome.holiday in ("Nochebuena", "Navidad"):
        add["work_christmas"] = 1
    elif outcome.holiday == "Reyes":
        add["work_reyes"] = 1
    elif outcome.holiday == "el Día del Trabajador":
        add["work_mayday"] = 1
    elif outcome.holiday == "el Día de la Constitución" and outcome.job.key == "politica":
        add["work_constitution"] = 1
    if outcome.kind.value == "negro":
        add["work_black"] = 1
        add["work_past_limit"] = 1
    if outcome.caught:
        add["work_caught_inspeccion"] = 1
    if outcome.battery_before < 0:
        add["work_zombie"] = 1
    if outcome.accident:
        add["work_accidents"] = 1
    if outcome.intervention:
        add["work_family_zero"] = 1
    if outcome.family_before >= FAMILY_TIERS[0].low:
        add["work_happy_shifts"] = 1
    if outcome.fee:
        add["work_fee"] = 1
    if outcome.game.broken:
        add["work_pipes"] = outcome.game.broken
    content = outcome.position.content.partition(":")[0]
    if (stat := _WORK_CONTENT_STATS.get(content)) is not None:
        hits = outcome.game.perfect_rounds if content in MEMORY_CONTENT else outcome.game.correct
        if hits:
            add[stat] = hits
    slip = outcome.payslip
    if slip is not None:
        add["work_payslips"] = 1
        if slip.irpf:
            add["tax_paid"] = slip.irpf
        if slip.total_taxes:
            add["work_taxes"] = slip.ss_worker + slip.irpf
        delta.peak["work_irpf_pct_max"] = round(slip.rates.irpf * 100)
        if slip.net > 0 and slip.total_taxes >= 0.8 * slip.net:
            add["work_half_salary"] = 1
        if slip.rates.over_max_base:
            add["work_max_base"] = 1
    taxes, net = outcome.week_taxes
    if net > 0 and taxes >= net:
        add["work_partner"] = 1
    if outcome.score == 0:
        add["work_zero"] = 1
    if outcome.kind.value == "negro":
        add["work_black_total"] = 1
    if outcome.kind.value == "guardia":
        add["work_guards"] = 1
        if outcome.battery_before < 0:
            add["work_zombie_guard"] = 1
        if outcome.holiday == "Nochevieja":
            add["work_guard_nye"] = 1
    if outcome.missed_guards:
        add["work_missed_guards"] = 1
    if outcome.remote:
        add["work_remote"] = 1
    elif outcome.job.key == "oficina" and not outcome.abroad:
        add["work_office"] = 1
    if outcome.abroad:
        add["work_hk_shifts"] = 1
        if outcome.canary_night:
            add["work_jetlag"] = 1
        if outcome.phase == "no_residente":
            add["work_nonresident"] = 1
    foreign = outcome.foreign
    if foreign is not None:
        add["work_hk_tax"] = foreign.foreign
        if foreign.exempt:
            add["work_7p"] = 1
        if foreign.double_tax_relief:
            add["work_double_tax"] = 1
        if foreign.irpf:
            add["tax_paid"] = foreign.irpf
    if outcome.options_total:
        delta.peak["work_options_max"] = outcome.options_total
    if outcome.exit_payout:
        add["work_exit"] = 1
    if outcome.bankrupt:
        add["work_bankrupt"] = 1
    if outcome.wish is not None:
        add["work_wishes"] = 1
        add[f"{WISH_STAT_PREFIX}{outcome.wish.key}"] = 1
        delta.peak["work_wish_streak_max"] = outcome.wish_streak
    _work_job_stats(outcome, delta)
    _work_game_stats(outcome, add)
    return delta


def _work_job_stats(outcome: ShiftOutcome, delta: StatDelta) -> None:
    """Lo propio de cada oficio: el tiempo, la app, los trienios y la viralidad."""
    add = delta.add
    if outcome.weather == "lluvia":
        add["work_rain"] = 1
        if outcome.accident:
            add["work_rain_fall"] = 1
    elif outcome.weather == "calima":
        add["work_calima"] = 1
    if outcome.blocked:
        add["work_app_blocked"] = 1
    if outcome.rating >= RATING_MAX:
        add["work_five_stars"] = 1
    if outcome.fee and outcome.position.fee < SELF_EMPLOYED_FEE:
        add["work_flat_fee"] = 1
    if outcome.viral >= MEGAVIRAL:
        add["work_megaviral"] = 1
    if outcome.viral >= VIRAL_AT:
        add["work_viral"] = 1
    elif 0 < outcome.viral < FLOP_BELOW:
        add["work_flop"] = 1
    if outcome.followers:
        delta.peak["work_followers_max"] = outcome.followers
    if outcome.triennia:
        delta.peak["work_triennia_max"] = outcome.triennia


def _work_game_stats(outcome: ShiftOutcome, add: dict[str, int]) -> None:
    """Lo del minijuego: velocidad, herramientas, despistes y rondas limpias."""
    game = outcome.game
    if game.saved:
        add["work_saves"] = game.saved
    if game.insured_breaks:
        add["work_insured"] = game.insured_breaks
    if game.fifty and game.correct:
        add["work_fifty"] = game.correct
    if game.completed and game.time_left >= game.seconds / 2:
        add["work_fast"] = 1
    if game.completed and game.time_left < 1:
        add["work_last_second"] = 1
    if game.first_miss:
        add["work_first_miss"] = 1
    if game.stale:
        add["work_stale_clicks"] = game.stale
    if outcome.score >= 100 and outcome.coffees > MAX_COFFEES:
        add["work_tremor_perfect"] = 1
    if game.mechanic is Mechanic.DIG and not game.broken and game.correct:
        add["work_clean_digs"] = 1
    if (
        game.mechanic is Mechanic.MEMORY
        and game.completed
        and game.perfect_rounds == len(game.rounds)
    ):
        add["work_memory_flawless"] = 1


#: Contenidos de memoria: cuentan las rondas perfectas, no los aciertos sueltos.
MEMORY_CONTENT = frozenset(
    {
        "platos",
        "comandas",
        "cocina",
        "carteles",
        "votos",
        "camilla",
        "ronda",
        "cafes",
        "entregas",
        "sellos",
        "baile",
        "socios",
        "plenos",
        "pisos",
        "concesiones",
        "quirofano",
        "retoques",
    }
)
