"""Reglas de Allá tú: 22 cajas, un gordo sorpresa y la banca de Perro Sanxe al teléfono.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.allatu`) cobra al
empezar, paga al acabar y pinta el plató (`bot.services.allatu_scene`).

Cómo se juega:

1. **El gordo.** Al empezar se sortea el premio gordo de la partida (`Table.gordo`):
   casi siempre ×10, a veces mucho más (hasta ×250, ×500 o ×1.000 según la tabla).
2. **Tu caja.** Se reparten los 22 premios en 22 cajas cerradas y el jugador se
   queda una (`choose`). No se abre hasta el final.
3. **Rondas.** Se abren las demás cajas por rondas de 6, 5, 4, 3 y 2 (`ROUNDS`).
   Cada caja abierta enseña un premio que ya no puede estar en la tuya.
4. **La banca.** Tras cada ronda llama Perro Sanxe con una oferta (`offer`): ✅ Trato
   (cobra la oferta y acaba) o ❌ No trato (sigue). Una vez por partida se puede
   🤝 regatear: la mitad de las veces sube la oferta y la otra mitad cuelga y esa
   ronda no hay oferta. Si se abre el gordo a mitad de ronda, llama en el acto
   con la oferta de esa ronda (el pésame, `Offer.condolence`): quien ya no quiere
   seguir se va sin abrir más cajas.
5. **El final.** Tras la quinta oferta quedan dos cajas, la tuya y otra: 🔒 te
   quedas la tuya o 🔄 la cambias, y cobras lo que haya dentro.

**Escalera de premios.** Debajo del gordo hay cajas rojas (de ×1 o más: cinco en
Normal hasta ×3, tres en Picante hasta ×4 y dos en Infierno hasta ×5), como en el
concurso de la tele. Hasta la 8.7 el gordo era el 72-90 % del valor de la tabla y,
al salir, en Picante e Infierno no quedaba ninguna caja que ganara: se seguían
abriendo cajas para perder. Ahora es el 54-66 % y, sin él, sigue habiendo premios
por los que jugar (`docs/allatu_tablas.py` lo mide).

**Ventaja de la casa.** Los 21 premios de serie de cada tabla y la media del gordo
suman exactamente 22 × 0,99: de media, una caja vale el 99 % de lo apostado
(`test_cada_tabla_paga_de_media_el_99`). La oferta es la media de las cajas que
quedan cerradas (la tuya incluida) por un factor que sube de ronda en ronda
(`OFFER_FACTORS`, del 75 % al 100 %, «tacaña al principio»). Como esa media no
cambia de media al abrir una caja (es una martingala) y el factor nunca pasa de
1, **ninguna forma de jugar devuelve más del 99 %**: quien llega hasta el final
cobra el 99 % exacto y quien acepta pronto, menos. Regatear tampoco pasa de 1.
La oferta no mira lo que hay en tu caja: si lo mirara, delataría su contenido.

**Todo se sortea al empezar** (`AllatuGame.new`): el gordo, el reparto, la suerte
de cada regateo (`haggle_luck`) y la caja de 🎲 (`dice`). Así el cog pinta antes
del clic la animación de cada caja (no depende de cuándo se abra) y la de cada
botón de la banca.

Premios en «veces la apuesta» y con fracciones exactas; lo que se cobra se
redondea hacia abajo.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

#: Cajas del plató.
BOXES = 22
#: Cajas que se abren en cada ronda; tras cada ronda llama la banca.
ROUNDS: tuple[int, ...] = (6, 5, 4, 3, 2)
#: Retorno al jugador que juega hasta el final: 99 %.
RTP = Fraction(99, 100)
#: Factor de la oferta de cada ronda sobre la media de lo que queda.
OFFER_FACTORS: tuple[Fraction, ...] = (
    Fraction(75, 100),
    Fraction(82, 100),
    Fraction(89, 100),
    Fraction(95, 100),
    Fraction(1),
)
#: Lo que sube el factor un regateo con suerte (nunca por encima de 1).
HAGGLE_RAISE = Fraction(10, 100)
#: Probabilidad de que Perro Sanxe suba la oferta al regatear (si no, cuelga).
HAGGLE_CHANCE = Fraction(1, 2)
#: Cajas abiertas al acabar cada ronda: (6, 11, 15, 18, 20).
ROUND_ENDS: tuple[int, ...] = tuple(sum(ROUNDS[: i + 1]) for i in range(len(ROUNDS)))

assert len(OFFER_FACTORS) == len(ROUNDS)
assert ROUND_ENDS[-1] == BOXES - 2  # al final quedan la tuya y una más


def _fr(*values: str) -> tuple[Fraction, ...]:
    return tuple(Fraction(v) for v in values)


class Table(Enum):
    """Una tabla de premios: los 21 de serie y cómo se sortea el gordo.

    El valor del enum es la clave estable (la del comando y la de los logros).

    Attributes:
        label: Nombre visible.
        emoji: Forma que la distingue.
        base: Los 21 premios de serie (veces la apuesta), de menor a mayor.
        gordo: Cómo se sortea el gordo: (multiplicador, peso en tantos por mil).
    """

    NORMAL = (
        "normal",
        "Normal",
        "🎁",
        _fr(*"0 0 0 0 0 0 0 0 0.01 0.02 0.04".split(),
            *"0.05 0.05 0.1 0.1 0.2 1 1.5 2 2 3".split()),
        ((10, 945), (20, 38), (50, 10), (100, 5), (250, 2)),
    )  # fmt: skip
    PICANTE = (
        "picante",
        "Picante",
        "🌶️",
        _fr(*"0 0 0 0 0 0 0 0 0 0 0.01".split(),
            *"0.02 0.03 0.03 0.05 0.05 0.1 0.15 1.5 2.5 4".split()),
        ((10, 934), (25, 44), (60, 15), (200, 5), (500, 2)),
    )  # fmt: skip
    INFIERNO = (
        "infierno",
        "Infierno",
        "🔥",
        _fr(*"0 0 0 0 0 0 0 0 0 0 0".split(),
            *"0 0.01 0.02 0.03 0.05 0.08 0.1 0.2 2 5".split()),
        ((10, 919), (20, 60), (60, 15), (250, 4), (1000, 2)),
    )  # fmt: skip

    label: str
    emoji: str
    base: tuple[Fraction, ...]
    gordo: tuple[tuple[int, int], ...]

    def __new__(
        cls,
        key: str,
        label: str,
        emoji: str,
        base: tuple[Fraction, ...],
        gordo: tuple[tuple[int, int], ...],
    ) -> Table:
        obj = object.__new__(cls)
        obj._value_ = key
        obj.label = label
        obj.emoji = emoji
        obj.base = base
        obj.gordo = gordo
        return obj

    @property
    def key(self) -> str:
        """Clave estable de la tabla (`normal`, `picante`, `infierno`)."""
        return str(self.value)

    @property
    def max_gordo(self) -> int:
        """El gordo más grande que puede salir."""
        return max(m for m, _ in self.gordo)

    @property
    def gordo_mean(self) -> Fraction:
        """Lo que vale de media el gordo."""
        total = sum(w for _, w in self.gordo)
        return Fraction(sum(m * w for m, w in self.gordo), total)

    def draw_gordo(self, rng: random.Random) -> int:
        """Sortea el gordo de una partida según sus pesos."""
        roll = rng.randrange(sum(w for _, w in self.gordo))
        for mult, weight in self.gordo:
            if roll < weight:
                return mult
            roll -= weight
        raise AssertionError("pesos mal sumados")  # pragma: no cover

    def gordo_chance(self, mult: int) -> Fraction:
        """Probabilidad de que el gordo sea `mult`."""
        total = sum(w for _, w in self.gordo)
        return Fraction(sum(w for m, w in self.gordo if m == mult), total)


TABLES: tuple[Table, ...] = tuple(Table)
TABLE_BY_KEY: dict[str, Table] = {t.key: t for t in TABLES}
DEFAULT_TABLE = Table.NORMAL
_ALIASES = {"n": "normal", "p": "picante", "i": "infierno", "infernal": "infierno"}


def parse_table(text: str) -> Table:
    """`picante`, `Infierno`, `p`… → la tabla correspondiente.

    Raises:
        ValueError: Con un mensaje mostrable si no es ninguna.
    """
    key = text.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in TABLE_BY_KEY:
        names = ", ".join(f"`{t.key}`" for t in TABLES)
        raise ValueError(f"La tabla es una de estas: {names}.")
    return TABLE_BY_KEY[key]


def format_multiplier(value: Fraction | int) -> str:
    """`Fraction(3, 4)` → `×0,75`; `250` → `×250`; `1000` → `×1.000`. Hacia abajo."""
    cents = math.floor(Fraction(value) * 100)
    whole, frac = divmod(cents, 100)
    text = "×" + f"{whole:,}".replace(",", ".")
    if frac:
        text += f",{frac:02d}".rstrip("0")
    return text


def format_chance(p: Fraction) -> str:
    """`Fraction(3, 1000)` → `0,3 %`; `Fraction(76, 100)` → `76 %`."""
    value = float(p) * 100
    if value < 10:
        return f"{value:.1f}".replace(".", ",").replace(",0", "") + " %"
    return f"{round(value)} %"


class Phase(Enum):
    """En qué punto está una partida."""

    PICK = "pick"  # elegir tu caja
    OPEN = "open"  # abrir cajas
    OFFER = "offer"  # la banca ha hecho una oferta
    FINAL = "final"  # quedan dos: quedarse o cambiar
    DONE = "done"


class Ending(Enum):
    """Cómo acabó la partida."""

    DEAL = "deal"  # aceptó una oferta
    KEPT = "kept"  # se quedó su caja al final
    SWAPPED = "swapped"  # la cambió por la otra


class AllatuError(Exception):
    """Acción no válida en la partida; el mensaje se puede enseñar al usuario."""


@dataclass(slots=True)
class Offer:
    """Una oferta de la banca.

    Attributes:
        round: Ronda tras la que llama (0-4).
        factor: Factor sobre la media de lo que queda (`OFFER_FACTORS`, o más si
            se regateó con suerte).
        amount: Lo que se cobraría (Y$, hacia abajo).
        mean: Media de las cajas cerradas cuando llamó (veces la apuesta).
        haggled: Si esta oferta es la que salió de un regateo con suerte.
        hung_up: Si Perro Sanxe colgó al regatear (y no hubo oferta).
        accepted: Si se aceptó.
        condolence: Si es el pésame: la llamada fuera de ronda al abrir el gordo.
    """

    round: int
    factor: Fraction
    amount: int
    mean: Fraction
    haggled: bool = False
    hung_up: bool = False
    accepted: bool = False
    condolence: bool = False


@dataclass(slots=True)
class AllatuGame:
    """Una partida de Allá tú.

    Attributes:
        stake: Lo apostado (ya cobrado por la economía).
        table: La tabla de premios.
        gordo: El gordo sorteado (veces la apuesta).
        values: Lo que hay en cada caja, de la 1 a la 22 (índices 0-21).
        haggle_luck: Si regatear tras cada ronda sube la oferta (`True`) o cuelga.
        dice: La caja que abriría 🎲 ahora (se vuelve a sortear tras usarla).
        own: Tu caja (índice), cuando ya la has elegido.
        opened: Cajas abiertas, en orden.
        offers: Las ofertas de la banca, en orden.
        random_opens: Cajas abiertas con 🎲 (para los logros).
    """

    stake: int
    table: Table
    gordo: int
    values: tuple[Fraction, ...]
    haggle_luck: tuple[bool, ...]
    dice: int = 0
    own: int | None = None
    opened: list[int] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)
    phase: Phase = Phase.PICK
    ending: Ending | None = None
    payout: int = 0
    random_opens: int = 0

    @classmethod
    def new(cls, stake: int, table: Table, rng: random.Random) -> AllatuGame:
        """Empieza una partida: sortea el gordo, el reparto y la suerte de los regateos.

        Raises:
            ValueError: Si la apuesta no es positiva.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        gordo = table.draw_gordo(rng)
        values = [*table.base, Fraction(gordo)]
        rng.shuffle(values)
        luck = tuple(rng.random() < HAGGLE_CHANCE for _ in ROUNDS)
        game = cls(stake=stake, table=table, gordo=gordo, values=tuple(values), haggle_luck=luck)
        game.dice = rng.randrange(BOXES)
        return game

    # -- Estado -----------------------------------------------------------------------

    @property
    def playing(self) -> bool:
        """Si la partida sigue."""
        return self.phase is not Phase.DONE

    @property
    def closed(self) -> list[int]:
        """Cajas sin abrir (la tuya incluida), de menor a mayor."""
        shut = set(self.opened)
        return [i for i in range(BOXES) if i not in shut]

    @property
    def openable(self) -> list[int]:
        """Cajas que se pueden abrir ahora (las cerradas que no son la tuya)."""
        return [i for i in self.closed if i != self.own]

    @property
    def remaining_values(self) -> list[Fraction]:
        """Premios que siguen en juego (en alguna caja cerrada), de menor a mayor."""
        return sorted(self.values[i] for i in self.closed)

    @property
    def mean(self) -> Fraction:
        """Lo que vale de media una caja cerrada (veces la apuesta)."""
        left = self.closed
        return sum((self.values[i] for i in left), Fraction(0)) / len(left)

    @property
    def round(self) -> int:
        """La ronda en juego (0-4) mientras se abren cajas; 5 al final."""
        done = len(self.opened)
        for i, end in enumerate(ROUND_ENDS):
            if done < end:
                return i
        return len(ROUNDS)

    @property
    def left_in_round(self) -> int:
        """Cajas que faltan por abrir antes de que llame la banca."""
        r = self.round
        return ROUND_ENDS[r] - len(self.opened) if r < len(ROUNDS) else 0

    @property
    def offer(self) -> Offer | None:
        """La oferta que espera respuesta, si la hay."""
        return self.offers[-1] if self.phase is Phase.OFFER else None

    @property
    def gordo_alive(self) -> bool:
        """Si el gordo sigue en alguna caja cerrada."""
        return any(self.values[i] == self.gordo for i in self.closed)

    @property
    def gordo_lost(self) -> bool:
        """Si el gordo salió en una caja que abrió el jugador (no en la otra del final)."""
        return any(self.values[i] == self.gordo for i in self.opened[: ROUND_ENDS[-1]])

    @property
    def own_value(self) -> Fraction | None:
        """Lo que hay en tu caja (secreto hasta el final)."""
        return self.values[self.own] if self.own is not None else None

    @property
    def other(self) -> int | None:
        """En el final, la otra caja que queda cerrada."""
        if self.phase is not Phase.FINAL:
            return None
        rest = self.openable
        return rest[0] if rest else None

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la partida terminada."""
        return self.payout - self.stake

    @property
    def can_haggle(self) -> bool:
        """Si se puede regatear la oferta en juego: una vez por partida y si aún sube."""
        offer = self.offer
        return (
            offer is not None
            and not any(o.haggled or o.hung_up for o in self.offers)
            and offer.factor < 1
        )

    def value(self, box: int) -> Fraction:
        """Lo que hay en la caja `box` (0-21)."""
        return self.values[box]

    def amount_of(self, mult: Fraction) -> int:
        """Lo que se cobra por `mult` veces la apuesta, hacia abajo."""
        return math.floor(self.stake * mult)

    def best_offer(self) -> Offer | None:
        """La mayor oferta de la partida (sin contar los cuelgues)."""
        real = [o for o in self.offers if not o.hung_up]
        return max(real, key=lambda o: o.amount) if real else None

    # -- Acciones ---------------------------------------------------------------------

    def choose(self, box: int) -> None:
        """Elige tu caja.

        Raises:
            AllatuError: Si ya la eligió o la caja no existe.
        """
        if self.phase is not Phase.PICK:
            raise AllatuError("Ya tienes tu caja.")
        if not 0 <= box < BOXES:
            raise AllatuError("Esa caja no existe.")
        self.own = box
        self.phase = Phase.OPEN
        if self.dice == box:
            self.dice = self.openable[0]

    def reroll_dice(self, rng: random.Random) -> int:
        """Sortea la caja de 🎲 entre las que se pueden abrir (o elegir, al empezar)."""
        pool = list(range(BOXES)) if self.phase is Phase.PICK else self.openable
        self.dice = rng.choice(pool) if pool else 0
        return self.dice

    def open(self, box: int, *, random_pick: bool = False) -> Fraction:
        """Abre una caja. Si cierra la ronda, la banca hace su oferta (`offer`).

        Si es el gordo y no cierra la ronda, llama igual con la oferta de la ronda
        (el pésame): misma fórmula, así que no cambia el 99 %.

        Returns:
            Lo que había dentro.

        Raises:
            AllatuError: Si no toca abrir o la caja no se puede abrir.
        """
        if self.phase is not Phase.OPEN:
            raise AllatuError("Ahora no toca abrir cajas.")
        if box == self.own:
            raise AllatuError("Esa es tu caja: se abre al final.")
        if box not in self.openable:
            raise AllatuError("Esa caja ya está abierta.")
        self.opened.append(box)
        if random_pick:
            self.random_opens += 1
        if len(self.opened) in ROUND_ENDS:
            self._call(ROUND_ENDS.index(len(self.opened)))
        elif self.values[box] == self.gordo:
            self._call(self.round, condolence=True)
        return self.values[box]

    def _call(
        self,
        round_: int,
        factor: Fraction | None = None,
        *,
        haggled: bool = False,
        condolence: bool = False,
    ) -> None:
        mean = self.mean
        f = OFFER_FACTORS[round_] if factor is None else factor
        amount = math.floor(self.stake * f * mean)
        self.offers.append(Offer(round_, f, amount, mean, haggled=haggled, condolence=condolence))
        self.phase = Phase.OFFER

    def deal(self) -> int:
        """✅ Trato: cobra la oferta y acaba.

        Raises:
            AllatuError: Si no hay oferta.
        """
        offer = self.offer
        if offer is None:
            raise AllatuError("No hay ninguna oferta.")
        offer.accepted = True
        self.ending = Ending.DEAL
        self.payout = offer.amount
        self.phase = Phase.DONE
        return self.payout

    def no_deal(self) -> None:
        """❌ No trato: sigue abriendo (o pasa al final tras la última oferta).

        Raises:
            AllatuError: Si no hay oferta.
        """
        if self.offer is None:
            raise AllatuError("No hay ninguna oferta.")
        self._next()

    def _next(self) -> None:
        self.phase = Phase.FINAL if len(self.opened) >= ROUND_ENDS[-1] else Phase.OPEN

    def haggle_result(self) -> bool:
        """Lo que pasaría al regatear ahora: `True` si sube, `False` si cuelga."""
        offer = self.offer
        return offer is not None and self.haggle_luck[offer.round]

    def haggle(self) -> bool:
        """🤝 Regatear: con suerte sube la oferta; si no, cuelga y se sigue sin oferta.

        Returns:
            `True` si subió (hay oferta nueva que contestar), `False` si colgó.

        Raises:
            AllatuError: Si no se puede regatear.
        """
        if not self.can_haggle:
            raise AllatuError("Perro Sanxe ya no admite regateos.")
        offer = self.offer
        assert offer is not None
        if self.haggle_luck[offer.round]:
            factor = min(Fraction(1), offer.factor + HAGGLE_RAISE)
            # Regatear el pésame sigue siendo el pésame (para el texto y los logros).
            self._call(offer.round, factor, haggled=True, condolence=offer.condolence)
            return True
        offer.hung_up = True
        self._next()
        return False

    def finish(self, *, swap: bool) -> int:
        """Final: 🔒 quedarse la caja o 🔄 cambiarla por la otra, y cobrar lo que haya.

        Raises:
            AllatuError: Si no es el final.
        """
        if self.phase is not Phase.FINAL or self.own is None:
            raise AllatuError("Aún no es el final.")
        other = self.other
        assert other is not None
        if swap:
            self.own, other = other, self.own
        self.opened.append(other)
        self.ending = Ending.SWAPPED if swap else Ending.KEPT
        self.payout = self.amount_of(self.values[self.own])
        self.phase = Phase.DONE
        return self.payout

    def settle_now(self) -> int:
        """Cierra a la fuerza (mesa caducada o bot apagándose) sin que nadie pierda.

        Con oferta en juego, la acepta; si no, cobra la oferta que tocaría
        ahora con el factor de la ronda (al final, el 100 %), como si llamara
        la banca. Antes de elegir caja no se llama: el cog devuelve la apuesta.
        """
        if self.phase is Phase.OFFER:
            return self.deal()
        r = min(self.round, len(ROUNDS) - 1)
        factor = OFFER_FACTORS[-1] if self.phase is Phase.FINAL else OFFER_FACTORS[r]
        self._call(r, factor)
        return self.deal()


def offer_mood(game: AllatuGame) -> str:
    """`rata`, `normal` o `generosa`: cómo se presenta la oferta en juego.

    Rata por debajo de la mitad de la apuesta; generosa desde el doble.
    """
    offer = game.offer
    if offer is None:
        return "normal"
    if offer.amount >= game.stake * 2:
        return "generosa"
    if offer.amount < game.stake // 2:
        return "rata"
    return "normal"


def tier(value: Fraction, gordo: int) -> str:
    """Color de un premio en el plató: `gordo`, `rojo` (paga), `azul` (pierde) o `cero`."""
    if value == gordo:
        return "gordo"
    if value >= 1:
        return "rojo"
    if value > 0:
        return "azul"
    return "cero"
