"""Skins: el aspecto de las mesas del casino (tapetes, barajas, monedas…).

Una skin cambia **cómo se ve** un juego, nunca cómo se juega: no toca
probabilidades, pagos ni tiempos (como las supersticiones de la tienda).

**Dos registros:**

- `GAMES`: los juegos que admiten skins y su aspecto de serie. Cada juego
  dice qué parámetros entiende su escena (`Game.defaults`): colores,
  degradados, textos. Es el contrato con la escena: lo que no esté aquí, la
  escena no lo lee.
- `SKINS`: las skins. Cada una pertenece a un juego y solo cambia los
  parámetros que quiere; el resto sale de los de serie (`params`).

**Cómo se consiguen** (se pueden combinar):

- *En la tienda* (`price` y `aisle`): un artículo del colmado, con su IGIC,
  que genera `shop_catalog` igual que las mascotas (clave `skin_<clave>`).
- *Por logro* (`achievement`): no se vende (artículo oculto); la tienda la
  mete en la mochila al desbloquear el logro, o al abrir la mochila si el
  logro ya estaba (`Tienda.sync_reward_skins`).
- *Por temporada* (`season`): solo se vende en esos meses; fuera de ellos no
  sale en el escaparate ni se puede pagar. Quien ya la tiene la conserva.
- *Gratis* (`free`): no se vende (artículo oculto; el precio es lo que vale
  para el patrimonio, como en las de logro); la tienda la mete en la mochila
  al abrir el 🎨 de su juego o cuando el juego la pide
  (`Tienda.grant_free_skins`), y no antes: así no llena la mochila de quien
  nunca ha jugado. Sirven para que un juego tenga donde elegir desde el
  primer día (los gallos).

**El dibujo de reserva.** Si Node y Chromium fallan, el juego se dibuja con
Pillow, que no conoce las skins: la mesa sale como siempre. Es raro y no
rompe nada; por eso un juego que admita skins no necesita tocar su Pillow.

**Cuál se ve.** Cada miembro lleva una por juego (o ninguna: la de serie),
elegida en la mochila o con el botón 🎨 de la mesa (`bot.cogs.shop`). En las
mesas individuales se ve la de su dueño; en las compartidas (bacará, crash,
caballos), la de quien abre la mesa, y el mensaje lo dice (`Game.shared`).

**Tapetes.** Las mesas con fieltro (ruleta, autobús, bacará, dados, moneda y
pollo) son un juego cada una, del grupo `TAPETE` (`Game.group`). Una skin del
grupo se compra una vez y se lleva en las mesas que quieras, cada una con la
suya; una skin con la clave de una mesa (`moneda`) es un tapete solo de esa
mesa (`fits`). Lo puesto va por mesa en `shop_skin_worn`. En el 🎨 de la mesa
salen también los tapetes que se venden y no tienes: se compran desde ahí y se
ponen en esa mesa.

**Añadir una skin** a un juego que ya las admite: una fila en `SKINS` con los
parámetros que cambia. **Añadir un juego**: una fila en `GAMES` con sus
parámetros de serie (los colores que hoy están escritos en su escena), que la
escena lea `meta.skin` en vez de esos colores, que el cog pida la skin con
`shop.skin_of` y la pase al dibujo, y que la skin entre en la clave de lo que
se pinta antes del clic. Ver la Biblia, «Tienda», y la ruleta como ejemplo.

Los gallos son el otro caso: cada skin es un gallo entero y en un duelo se
ven las dos, la de cada dueño (la escena recibe una por esquina). Los gallos
de la casa usan los mismos parámetros (`bot.services.gallos.HOUSE`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

#: Prefijo de la clave de artículo de una skin en el surtido de la tienda.
CATALOG_PREFIX = "skin_"
#: El grupo de los tapetes: sus skins valen en todas las mesas con fieltro.
TAPETE = "tapete"


def _frozen(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class Game:
    """Un juego que admite skins.

    Attributes:
        key: Clave estable (la misma que usa el juego en `apuestas`).
        name: Nombre para la interfaz.
        emoji: Icono del juego.
        thing: Qué cambia la skin, en minúscula y singular («tapete»).
        defaults: Parámetros de serie de la escena. Toda skin del juego parte
            de aquí, y la escena no lee nada que no esté en este diccionario.
        shared: Si la mesa es de todo el canal: entonces se ve la skin de
            quien la abre.
        group: Grupo cuyas skins también valen aquí (las mesas con fieltro,
            `TAPETE`): se compran una vez y se llevan en cada mesa por separado.
        wearable: Si es una mesa donde se lleva una skin. El grupo `TAPETE` no lo
            es: solo agrupa las skins que valen en todas sus mesas.
    """

    key: str
    name: str
    emoji: str
    thing: str
    defaults: Mapping[str, object] = field(default_factory=dict)
    shared: bool = False
    group: str | None = None
    wearable: bool = True

    @property
    def label(self) -> str:
        """`🎡 Ruleta`."""
        return f"{self.emoji} {self.name}"


@dataclass(frozen=True, slots=True)
class Season:
    """Meses en los que se vende una skin de temporada.

    Attributes:
        name: Cómo se llama la temporada («Carnaval»).
        months: Meses (1-12) en los que está a la venta.
    """

    name: str
    months: frozenset[int]

    def open(self, when: datetime) -> bool:
        """Si la temporada está abierta en `when` (hora local del servidor)."""
        return when.month in self.months


@dataclass(frozen=True, slots=True)
class Skin:
    """Una skin de un juego.

    Attributes:
        key: Clave estable; no se cambia nunca (la mochila guarda
            `skin_<clave>` y la cuentan los logros).
        game: Clave del juego (`GAMES`).
        emoji: Icono.
        name: Nombre para la interfaz.
        description: Texto del escaparate.
        params: Los parámetros de la escena que cambia respecto a los de serie.
        price: Precio en la tienda. Si no se vende (`achievement`), es lo que
            vale para el patrimonio.
        aisle: Pasillo del colmado por su tema (como cualquier artículo).
        igic: Tipo de IGIC (`taxes.IGIC_RATES`).
        min_level: Nivel mínimo para comprarla.
        stock: Unidades numeradas, si es limitada.
        achievement: Id del logro que la regala. Si lo tiene, no se vende.
        season: Temporada en la que se vende, si es de temporada.
        free: La tiene todo el mundo sin pagar: no se vende y la tienda la
            regala (ver la docstring del módulo).
    """

    key: str
    game: str
    emoji: str
    name: str
    description: str
    params: Mapping[str, object]
    price: int
    aisle: str
    igic: str = "general"
    min_level: int = 0
    stock: int | None = None
    achievement: str | None = None
    season: Season | None = None
    free: bool = False

    @property
    def catalog_key(self) -> str:
        """Clave del artículo de la tienda: `skin_<clave>`."""
        return f"{CATALOG_PREFIX}{self.key}"

    @property
    def for_sale(self) -> bool:
        """Si se vende en la tienda (las de logro y las gratis no)."""
        return self.achievement is None and not self.free

    @property
    def label(self) -> str:
        """`🟥 Tapete granate`."""
        return f"{self.emoji} {self.name}"


# fmt: off
#: Lo que entiende el tapete de una mesa con fieltro. Cada escena carga
#: `assets/tapete.js`, que pinta `felt` y `pattern`; con la de serie, cada mesa usa
#: su verde de siempre. Los de serie de aquí son los de la ruleta.
FELT: Mapping[str, object] = _frozen({
    # Degradado radial del fieltro, de la luz al borde.
    "felt": ("#2f9b62", "#1b7345", "#0b3f26"),
    # Las fibras largas del fieltro: una clara y una oscura.
    "fiber": ("rgba(190,255,210,0.045)", "rgba(0,20,8,0.07)"),
    # Filete del borde del tapete.
    "trim": "rgba(240,199,90,0.22)",
    # Contorno del título, sobre el fieltro.
    "ink": "#10190f",
    # Dibujo encima del fieltro (`Tapete.PATTERNS` en `assets/tapete.js`); vacío, liso.
    "pattern": "",
})

GAMES: dict[str, Game] = {g.key: g for g in (
    # Las skins de `tapete` valen en todas las mesas con fieltro; cada mesa lleva
    # la suya. Una skin con la clave de una mesa (`moneda`) solo vale en esa.
    Game(TAPETE, "Mesas con fieltro", "🎰", "tapete", FELT, wearable=False),
    Game("ruleta", "Ruleta", "🎡", "tapete", FELT, group=TAPETE),
    Game("autobus", "Autobús", "🚌", "tapete", FELT, group=TAPETE),
    Game("bacara", "Bacará", "🍸", "tapete", FELT, shared=True, group=TAPETE),
    Game("dados", "Dados", "🎲", "tapete", FELT, group=TAPETE),
    Game("moneda", "Cara o cruz", "🪙", "tapete", FELT, group=TAPETE),
    Game("pollo", "Pollo", "🐔", "tapete", FELT, group=TAPETE),
    # El gallo de serie es un giro colorado: pecho rojizo, esclavina naranja y
    # cola negra con reflejos verdes. Cada skin es un gallo entero.
    Game("gallos", "Gallos", "🐓", "gallo", _frozen({
        # Plumas del cuerpo, de la esclavina (cuello) y del ala.
        "body": "#b5341f",
        "hackle": "#e8862a",
        "wing": "#7a2416",
        # Las tres hoces de la cola, de dentro afuera.
        "tail": ("#123a2a", "#1f5a3a", "#0e2a1e"),
        # Cresta y barbillas, pico y patas (con los espolones).
        "comb": "#e0242a",
        "beak": "#e8b04a",
        "legs": "#e8b04a",
        # Dibujo de las plumas: `liso`, `moteado` o `barrado`.
        "pattern": "liso",
        # Lo que lleva puesto (la escena sabe pintar cada clave; vacío, nada).
        "accessory": "",
    })),
)}

SKINS: tuple[Skin, ...] = (
    # -- 🎰 Tapetes: uno para todas las mesas con fieltro. La clave del granate
    # es de cuando solo lo tenía la ruleta y no se cambia.
    Skin("ruleta_granate", "tapete", "🟥", "Tapete granate", (
        "El fieltro de los casinos de los ochenta, de cuando se fumaba dentro. No "
        "cambia ni una carta ni un número: solo el color de la mesa."
    ), _frozen({
        "felt": ("#a3243a", "#741628", "#3a0812"),
        "fiber": ("rgba(255,200,205,0.045)", "rgba(30,0,6,0.08)"),
        "ink": "#1c0508",
    }), price=15_000, aisle="lujo"),
    Skin("tapete_oro", "tapete", "🪙", "Tapete de oro", (
        "Fieltro de pan de oro con celosía art déco y chispas, como la sala privada de "
        "Montecarlo. Hacienda pregunta de dónde ha salido; tú dices que de la moneda."
    ), _frozen({
        "felt": ("#ffe9a3", "#c99a35", "#5c3b07"),
        "fiber": ("rgba(255,250,215,0.06)", "rgba(60,35,0,0.09)"),
        "trim": "rgba(255,240,190,0.5)",
        "ink": "#2a1a02",
        "pattern": "oro",
    }), price=250_000, aisle="lujo", min_level=10),
    Skin("tapete_espana", "tapete", "🇪🇸", "Tapete rojigualda", (
        "Rojo y gualda con el escudo bordado, las columnas y el PLVS VLTRA. Para "
        "jugarse los ahorros con el himno de fondo, que no tiene letra."
    ), _frozen({
        "felt": ("#f1bf00", "#c21d24", "#7e0f13"),
        "fiber": ("rgba(255,240,190,0.05)", "rgba(60,0,0,0.08)"),
        "trim": "rgba(255,215,90,0.4)",
        "ink": "#4a0608",
        "pattern": "espana",
    }), price=30_000, aisle="espana"),
    Skin("tapete_israel", "tapete", "🇮🇱", "Tapete de Israel", (
        "Blanco satinado, las dos franjas azules y la estrella de David bordada, con "
        "un damasco de estrellas que solo se ve de cerca."
    ), _frozen({
        "felt": ("#ffffff", "#e6ebf3", "#9aa8bf"),
        "fiber": ("rgba(255,255,255,0.06)", "rgba(0,20,70,0.07)"),
        "trim": "rgba(0,56,184,0.45)",
        "ink": "#001a5c",
        "pattern": "israel",
    }), price=400_000, aisle="lujo", min_level=15),
    Skin("tapete_canarias", "tapete", "🌋", "Tapete del Teide", (
        "Azul atlántico con olas, el Teide nevado asomando por encima del mar de "
        "nubes y la tricolor de las islas en el borde. Huele a gofio."
    ), _frozen({
        "felt": ("#1b8fd6", "#0a62a8", "#03294f"),
        "fiber": ("rgba(200,235,255,0.05)", "rgba(0,10,30,0.08)"),
        "ink": "#021a33",
        "pattern": "canarias",
    }), price=25_000, aisle="canarias"),
    Skin("tapete_boricua", "tapete", "🇵🇷", "Tapete monoestrellado", (
        "Las cinco franjas, el triángulo y la estrella de Puerto Rico. Jovani juega "
        "aquí descalzo y con la bocina a todo volumen. ¡Wepa!"
    ), _frozen({
        "felt": ("#f2163f", "#c90a33", "#5e0418"),
        "fiber": ("rgba(255,255,255,0.05)", "rgba(40,0,10,0.08)"),
        "ink": "#06204f",
        "pattern": "boricua",
    }), price=25_000, aisle="boricua"),
    # -- 🐓 Gallos: tres gratis, el resto en la tienda, uno de logro y dos de temporada.
    Skin("gallo_blanco", "gallos", "🤍", "Gallo blanco", (
        "Leghorn de granja, blanco nuclear. Lo tiene todo el mundo y aun así impone."
    ), _frozen({
        "body": "#f2efe6", "hackle": "#ffffff", "wing": "#d8d3c4",
        "tail": ("#e8e4d8", "#ffffff", "#cfc9b8"),
    }), price=2_000, aisle="espana", free=True),
    Skin("gallo_negro", "gallos", "🖤", "Gallo azabache", (
        "Negro con reflejos verdes, como el de las peleas de toda la vida. Gratis."
    ), _frozen({
        "body": "#1c1f1c", "hackle": "#2a3a2a", "wing": "#121612",
        "tail": ("#0f2a1c", "#1d4a30", "#0a1a12"),
    }), price=2_000, aisle="espana", free=True),
    Skin("gallo_pinto", "gallos", "🤎", "Gallo pinto", (
        "Moteado en blanco y negro, como un dálmata con espolones. Gratis."
    ), _frozen({
        "body": "#3a3530", "hackle": "#e8e2d6", "wing": "#2a2622",
        "tail": ("#1a1714", "#e8e2d6", "#1a1714"), "pattern": "moteado",
    }), price=2_000, aisle="espana", free=True),
    Skin("gallo_corral", "gallos", "🧺", "Gallo del corral de la abuela", (
        "Barrado gris, criado a base de sobras del cocido. No cambia ni un "
        "picotazo: solo cómo luce en el reñidero."
    ), _frozen({
        "body": "#7a7670", "hackle": "#bdb7ad", "wing": "#5a5650",
        "tail": ("#3a3834", "#8a857c", "#3a3834"), "pattern": "barrado",
        "accessory": "pañuelo",
    }), price=6_000, aisle="espana"),
    Skin("gallo_canario", "gallos", "🌴", "Gallo de la Fiesta del Pino", (
        "Amarillo canario, con sombrero de mago y olor a gofio. De Teror de toda "
        "la vida."
    ), _frozen({
        "body": "#f2c81a", "hackle": "#ffe066", "wing": "#d4a40e",
        "tail": ("#2a6ab8", "#f2f2f2", "#f2c81a"), "accessory": "sombrero",
    }), price=9_000, aisle="canarias"),
    Skin("gallo_moncloa", "gallos", "🌹", "Gallo de Moncloa", (
        "Rojo PSOE con la rosa en la cresta. Resiste a todo: a las encuestas, a la "
        "UCO y a los picotazos."
    ), _frozen({
        "body": "#d81e2a", "hackle": "#ff4a50", "wing": "#a01018",
        "tail": ("#1a1a1a", "#d81e2a", "#f2f2f2"), "accessory": "rosa",
    }), price=20_000, aisle="moncloa"),
    Skin("gallo_eurocrata", "gallos", "🇪🇺", "Gallo eurócrata", (
        "Azul Bruselas con doce estrellas y el tapón pegado. Viene con su propia "
        "directiva de 400 páginas."
    ), _frozen({
        "body": "#1f3fa0", "hackle": "#3c64d0", "wing": "#162e78",
        "tail": ("#f2c42a", "#1f3fa0", "#f2c42a"), "comb": "#f2c42a",
        "accessory": "estrellas",
    }), price=12_000, aisle="ventanilla"),
    Skin("gallo_boricua", "gallos", "🇵🇷", "Gallo boricua", (
        "Con pañuelo de la monoestrellada, como los de las galleras de la isla. "
        "¡Wepa!"
    ), _frozen({
        "body": "#c8202e", "hackle": "#f2f2f2", "wing": "#1a4ab0",
        "tail": ("#1a4ab0", "#f2f2f2", "#c8202e"), "accessory": "pañuelo",
    }), price=15_000, aisle="boricua"),
    Skin("gallo_dragon", "gallos", "🐉", "Gallo del dragón", (
        "Rojo y oro, de importación directa. Robuso jura que en Hong Kong pelea "
        "solo."
    ), _frozen({
        "body": "#b80c1e", "hackle": "#ffcc33", "wing": "#8a0816",
        "tail": ("#ffcc33", "#b80c1e", "#ffcc33"), "comb": "#ffcc33",
        "pattern": "barrado",
    }), price=18_000, aisle="hongkong"),
    Skin("gallo_maga", "gallos", "🧢", "Gallo MAGA", (
        "Tupé naranja y gorra roja. Make Gallinero Great Again. Con arancel "
        "incluido."
    ), _frozen({
        "body": "#f0a030", "hackle": "#ffcc55", "wing": "#c87818",
        "tail": ("#c8102e", "#f2f2f2", "#1a3a8a"), "accessory": "gorra",
    }), price=16_000, aisle="shitpost"),
    Skin("gallo_motosierra", "gallos", "🪚", "Gallo motosierra", (
        "Celeste y blanco, patillas de león y una motosierra para el gasto público "
        "del gallinero."
    ), _frozen({
        "body": "#74acdf", "hackle": "#f2f2f2", "wing": "#4a8ac0",
        "tail": ("#74acdf", "#f2f2f2", "#f6b40e"), "accessory": "patillas",
    }), price=16_000, aisle="shitpost"),
    Skin("gallo_oro", "gallos", "🏆", "Gallo de oro", (
        "Solo para quien gana 100 peleas. Oro de verdad, o eso dice el certificado."
    ), _frozen({
        "body": "#d4a017", "hackle": "#ffe27a", "wing": "#a87c0c",
        "tail": ("#ffe27a", "#d4a017", "#fff4c2"), "comb": "#ff3a3a",
        "accessory": "corona",
    }), price=50_000, aisle="lujo", achievement="gallos_wins_100"),
    Skin("gallo_calavera", "gallos", "💀", "Gallo calavera", (
        "Huesos pintados y velas del Día de Muertos. Solo en octubre y noviembre."
    ), _frozen({
        "body": "#1a1a1a", "hackle": "#f2f2f2", "wing": "#2a2a2a",
        "tail": ("#ff8a00", "#7a2ab8", "#ff8a00"), "comb": "#7a2ab8",
        "accessory": "calavera",
    }), price=10_000, aisle="cotillon", season=Season("Halloween", frozenset({10, 11}))),
    Skin("gallo_navidad", "gallos", "🎄", "Gallo navideño", (
        "Gorro de Papá Noel y bufanda. El único gallo que canta villancicos en "
        "vez de al amanecer."
    ), _frozen({
        "body": "#c41e3a", "hackle": "#f2f2f2", "wing": "#1e6a32",
        "tail": ("#1e6a32", "#c41e3a", "#f2f2f2"), "accessory": "gorro",
    }), price=10_000, aisle="cotillon", season=Season("Navidad", frozenset({12, 1}))),
)
# fmt: on

SKIN_BY_KEY: dict[str, Skin] = {s.key: s for s in SKINS}
SKIN_BY_CATALOG_KEY: dict[str, Skin] = {s.catalog_key: s for s in SKINS}


def fits(skin: Skin, game: str) -> bool:
    """Si `skin` se puede llevar en `game`: es suya o de su grupo (un tapete)."""
    return skin.game == game or (game in GAMES and skin.game == GAMES[game].group)


def skins_of(game: str, skins: tuple[Skin, ...] = SKINS) -> tuple[Skin, ...]:
    """Las skins que se pueden llevar en un juego (o las de un grupo), en orden."""
    return tuple(s for s in skins if fits(s, game))


def wearable_games(skin: Skin) -> tuple[Game, ...]:
    """Las mesas donde se puede llevar `skin` (todas las de fieltro, si es un tapete)."""
    return tuple(g for g in GAMES.values() if g.wearable and fits(skin, g.key))


def where(skin: Skin) -> str:
    """Dónde vale, para la tienda: `🎡 Ruleta` o `🎰 Mesas con fieltro`."""
    return GAMES[skin.game].label


def skin_of_item(catalog_key: str | None) -> Skin | None:
    """La skin de un artículo de la tienda, o `None` si no es una skin."""
    return SKIN_BY_CATALOG_KEY.get(catalog_key or "")


def params(game: str, skin: Skin | None = None) -> dict[str, object]:
    """Parámetros de la escena de `game` con `skin` encima de los de serie.

    Lleva también `key` (la de la skin, o `serie`): la escena la usa para saber
    si tiene que repintar lo que guarda de una partida a otra.

    Raises:
        KeyError: Si el juego no admite skins.
        ValueError: Si la skin es de otro juego o trae un parámetro que el
            juego no tiene (la escena no lo leería).
    """
    base = GAMES[game]
    out = dict(base.defaults)
    if skin is not None:
        if not fits(skin, game):
            raise ValueError(f"La skin {skin.key} es de {skin.game}, no de {game}")
        unknown = set(skin.params) - set(base.defaults)
        if unknown:
            raise ValueError(f"La skin {skin.key} trae parámetros que {game} no tiene: {unknown}")
        out.update(skin.params)
    out["key"] = skin.key if skin is not None else "serie"
    return out


def in_season(skin: Skin, when: datetime) -> bool:
    """Si la skin se puede comprar en `when` (las que no son de temporada, siempre)."""
    return skin.season is None or skin.season.open(when)


def rewards_for(achievement_ids: list[str] | set[str] | frozenset[str]) -> tuple[Skin, ...]:
    """Skins que regalan estos logros."""
    wanted = set(achievement_ids)
    return tuple(s for s in SKINS if s.achievement in wanted)


def free_skins(game: str) -> tuple[Skin, ...]:
    """Las skins gratis de un juego (las que la tienda regala a todos)."""
    return tuple(s for s in skins_of(game) if s.free)
