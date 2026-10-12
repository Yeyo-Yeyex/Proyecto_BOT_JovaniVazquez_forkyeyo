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

**El dibujo de reserva.** Si Node y Chromium fallan, el juego se dibuja con
Pillow, que no conoce las skins: la mesa sale como siempre. Es raro y no
rompe nada; por eso un juego que admita skins no necesita tocar su Pillow.

**Cuál se ve.** Cada miembro lleva una por juego (o ninguna: la de serie),
elegida en la mochila o con el botón 🎨 de la mesa (`bot.cogs.shop`). En las
mesas individuales se ve la de su dueño; en las compartidas (bacará, crash,
caballos), la de quien abre la mesa, y el mensaje lo dice (`Game.shared`).

**Añadir una skin** a un juego que ya las admite: una fila en `SKINS` con los
parámetros que cambia. **Añadir un juego**: una fila en `GAMES` con sus
parámetros de serie (los colores que hoy están escritos en su escena), que la
escena lea `meta.skin` en vez de esos colores, que el cog pida la skin con
`shop.skin_of` y la pase al dibujo, y que la skin entre en la clave de lo que
se pinta antes del clic. Ver la Biblia, «Tienda», y la ruleta como ejemplo.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

#: Prefijo de la clave de artículo de una skin en el surtido de la tienda.
CATALOG_PREFIX = "skin_"


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
    """

    key: str
    name: str
    emoji: str
    thing: str
    defaults: Mapping[str, object] = field(default_factory=dict)
    shared: bool = False

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

    @property
    def catalog_key(self) -> str:
        """Clave del artículo de la tienda: `skin_<clave>`."""
        return f"{CATALOG_PREFIX}{self.key}"

    @property
    def for_sale(self) -> bool:
        """Si se vende en la tienda (las de logro no)."""
        return self.achievement is None

    @property
    def label(self) -> str:
        """`🟥 Tapete granate`."""
        return f"{self.emoji} {self.name}"


# fmt: off
GAMES: dict[str, Game] = {g.key: g for g in (
    Game("ruleta", "Ruleta", "🎡", "tapete", _frozen({
        # Degradado radial del fieltro, de la luz al borde.
        "felt": ("#2f9b62", "#1b7345", "#0b3f26"),
        # Las fibras largas del fieltro: una clara y una oscura.
        "fiber": ("rgba(190,255,210,0.045)", "rgba(0,20,8,0.07)"),
        # Filete del borde del tapete.
        "trim": "rgba(240,199,90,0.22)",
        # Contorno del título, sobre el fieltro.
        "ink": "#10190f",
    })),
)}

SKINS: tuple[Skin, ...] = (
    Skin("ruleta_granate", "ruleta", "🟥", "Tapete granate", (
        "El fieltro de los casinos de los ochenta, de cuando se fumaba dentro. No "
        "cambia ni un número de la rueda: solo el color de la mesa."
    ), _frozen({
        "felt": ("#a3243a", "#741628", "#3a0812"),
        "fiber": ("rgba(255,200,205,0.045)", "rgba(30,0,6,0.08)"),
        "ink": "#1c0508",
    }), price=15_000, aisle="lujo"),
)
# fmt: on

SKIN_BY_KEY: dict[str, Skin] = {s.key: s for s in SKINS}
SKIN_BY_CATALOG_KEY: dict[str, Skin] = {s.catalog_key: s for s in SKINS}


def skins_of(game: str, skins: tuple[Skin, ...] = SKINS) -> tuple[Skin, ...]:
    """Las skins de un juego, en el orden del registro."""
    return tuple(s for s in skins if s.game == game)


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
        if skin.game != game:
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
