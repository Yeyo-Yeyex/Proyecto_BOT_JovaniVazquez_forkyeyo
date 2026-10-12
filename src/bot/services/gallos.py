"""Reglas de las peleas de gallos (`gallos`): rivales, cuotas, la pelea y los repartos.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.gallos`) cobra, paga y
pinta (`bot.services.gallos_scene`); la ficha de cada gallo se guarda en
`bot.repositories.gallos`.

**Dos maneras de pelear:**

- *Contra la casa*: tu gallo contra uno de los rivales de `HOUSE`, cada uno con
  su probabilidad de que ganes (`Rival.chance`) y una cuota fija que devuelve
  de media el `RTP` (95 %) de lo apostado, como las cuotas de los caballos.
- *Contra otra persona*: los dos ponen lo mismo y el ganador se lleva el bote
  menos la comisión de la casa (`DUEL_CUT`). Mientras se calientan los gallos,
  el público apuesta por uno u otro a la porra: todo lo apostado se reparte
  entre los que acierten, menos la misma comisión (`pool_payouts`).

**La skin no pelea.** El aspecto del gallo (`bot.services.skins`, juego
`gallos`), su nombre y su historial no tocan ninguna probabilidad: entre dos
personas cada gallo gana el 50 %. Lo mismo los rivales de la casa: su aspecto
y su golpe estrella (`Rival.signature`) son solo la gracia.

**Cómo se decide y cómo se cuenta.** Primero se sortea quién gana (con la
probabilidad de arriba) y luego `simulate` cuenta una pelea que acabe así. La
pelea se simula simétrica (cada intercambio lo empieza cualquiera de los dos al
50 %) y, si el ganador que sale no es el sorteado, se le da la vuelta entera:
como las dos esquinas son intercambiables, el resultado tiene exactamente la
probabilidad sorteada y la pelea parece igual de natural en los dos sentidos.

**El dinero que sale de la nada.** Los premios de la casa salen de la nada,
como en cualquier juego del casino; los compensa el margen de las cuotas. En
el duelo y en la porra del público no se crea dinero: la comisión se destruye
(es el margen de la casa, no un impuesto, así que no va al Estado). La única
excepción es la cuota mínima de la porra (`MIN_POOL_PAYOUT`): quien acierta
nunca cobra menos de lo que apostó, aunque casi todos apostaran lo mismo.
"""

from __future__ import annotations

import random
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from fractions import Fraction
from types import MappingProxyType

#: Clave del juego: motivo del libro, `casino_stats.GAMES` y juego de las skins.
GAME = "gallos"
#: Lo que devuelve de media una pelea contra la casa (el resto es su margen).
RTP = Fraction(95, 100)
#: Comisión de la casa en el bote de un duelo y en la porra del público.
DUEL_CUT = Fraction(5, 100)
POOL_CUT = DUEL_CUT
#: Quien acierta en la porra cobra al menos esto por cada Y$ apostado.
MIN_POOL_PAYOUT = Fraction(1)

#: Vida de cada gallo al empezar.
MAX_HP = 100
#: Asaltos de una pelea y cuántos intercambios lleva cada uno (como mucho).
ROUNDS = 3
EXCHANGES_PER_ROUND = (3, 4)
#: Probabilidad de que el que recibe esquive el golpe.
DODGE_CHANCE = 0.18
#: Probabilidad de golpe crítico (daño doble).
CRIT_CHANCE = 0.10
#: Por debajo de esta vida, un gallo puede salir corriendo tras un golpe.
FLEE_HP = 25
FLEE_CHANCE = 0.15

#: Largo máximo del nombre de un gallo.
NAME_MAX = 24
#: Nombres de serie hasta que el dueño le pone uno.
DEFAULT_NAMES = (
    "Kikirikí",
    "Espolón",
    "Pechuga de Acero",
    "Cresta Roja",
    "Gallito Inglés",
    "El Madrugador",
    "Plumas",
    "Caldo de Gallina",
    "Sin Miedo",
    "Pollo Pera",
)


class Corner(Enum):
    """Las dos esquinas del reñidero. En un duelo, el que reta es el rojo."""

    RED = "rojo"
    BLUE = "azul"

    @property
    def other(self) -> Corner:
        """La otra esquina."""
        return Corner.BLUE if self is Corner.RED else Corner.RED

    @property
    def emoji(self) -> str:
        """🔴 o 🔵."""
        return "🔴" if self is Corner.RED else "🔵"


class Finish(Enum):
    """Cómo acaba la pelea."""

    KO = "ko"
    FLED = "huida"
    POINTS = "puntos"

    @property
    def label(self) -> str:
        """Cómo se dice en el resultado."""
        return {
            Finish.KO: "por KO",
            Finish.FLED: "porque el otro salió por patas",
            Finish.POINTS: "a los puntos",
        }[self]


# -- Golpes ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Move:
    """Un golpe.

    Attributes:
        key: Clave estable (la escena la usa para elegir la animación).
        name: Nombre para la narración.
        damage: Daño mínimo y máximo, sin crítico.
        weight: Lo a menudo que sale.
        sound: Onomatopeya que pinta la escena al impactar.
    """

    key: str
    name: str
    damage: tuple[int, int]
    weight: int
    sound: str


MOVES: tuple[Move, ...] = (
    Move("picotazo", "Picotazo", (11, 22), 5, "¡TOC!"),
    Move("espolonazo", "Espolonazo", (18, 32), 3, "¡ZAS!"),
    Move("aletazo", "Aletazo", (7, 16), 4, "¡FLAP!"),
    Move("crestazo", "Crestazo", (14, 25), 3, "¡BONK!"),
    Move("patada", "Patada voladora", (22, 36), 2, "¡PAF!"),
)
MOVE_BY_KEY: dict[str, Move] = {m.key: m for m in MOVES}
#: El golpe que cambia de nombre por el golpe estrella de cada rival.
SIGNATURE_MOVE = "patada"


# -- Rivales de la casa ------------------------------------------------------------------


def _look(**params: object) -> Mapping[str, object]:
    return MappingProxyType(dict(params))


@dataclass(frozen=True, slots=True)
class Rival:
    """Un gallo de la casa.

    Attributes:
        key: Clave estable (la cuentan los logros: `gallos_beat_<clave>`).
        name: Nombre del gallo.
        emoji: Bandera o icono de su sitio.
        origin: De dónde viene, para el texto.
        blurb: Su ficha en una frase.
        chance: Probabilidad de que tu gallo le gane.
        signature: Nombre de su golpe estrella (su patada voladora).
        look: Parámetros de la escena que cambia respecto al gallo de serie
            (los mismos que una skin: `skins.GAMES["gallos"].defaults`).
    """

    key: str
    name: str
    emoji: str
    origin: str
    blurb: str
    chance: Fraction
    signature: str
    look: Mapping[str, object] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """`🇺🇸 Arancel`."""
        return f"{self.emoji} {self.name}"

    @property
    def multiplier(self) -> Fraction:
        """Cuota a la que paga ganarle (apuesta incluida)."""
        return house_multiplier(self.chance)


# fmt: off
HOUSE: tuple[Rival, ...] = (
    Rival("temu", "Gallo de Temu", "📦", "un almacén de Shenzhen",
          "Lo pediste por 2,99 y llegó en tres cajas. Falta una pata.",
          Fraction(85, 100), "Pieza de recambio",
          _look(body="#d9c25a", hackle="#f0dc78", wing="#b49a3a",
                tail=("#7a6a2a", "#a08a3a", "#c9b45a"), comb="#e86a6a", accessory="caja")),
    Rival("teror", "El Pollo de Teror", "🌋", "Gran Canaria",
          "Gallo viejo de la Fiesta del Pino. Ya no pica, pero huele a gofio.",
          Fraction(80, 100), "Gofiazo",
          _look(body="#e8d27a", hackle="#fff0a8", wing="#c9ad4a",
                tail=("#3a6b3a", "#2f5a8a", "#e8d27a"), accessory="sombrero")),
    Rival("directiva", "Directiva 2019/904", "🇪🇺", "Bruselas",
          "Lleva el tapón pegado a la cresta y pide tres formularios antes de cada picotazo.",
          Fraction(65, 100), "Tapón solidario",
          _look(body="#2a4aa8", hackle="#3c64d0", wing="#1f3a86",
                tail=("#f2c42a", "#2a4aa8", "#f2c42a"), comb="#f2c42a", accessory="estrellas")),
    Rival("falcon", "El Gallo del Falcon", "🇪🇸", "la Moncloa",
          "Vuela a todas partes en avión oficial y no dimite ni con el pico roto.",
          Fraction(50, 100), "Manual de resistencia",
          _look(body="#c8102e", hackle="#e8344a", wing="#9a0c22",
                tail=("#1a1a1a", "#c8102e", "#f2f2f2"), accessory="rosa")),
    Rival("motosierra", "La Motosierra", "🇦🇷", "la Casa Rosada",
          "Le grita «¡afuera!» a cada pluma que le sobra. Pelado a lo león.",
          Fraction(45, 100), "¡Afuera!",
          _look(body="#6ab0e8", hackle="#f2f2f2", wing="#4a90c8",
                tail=("#6ab0e8", "#f2f2f2", "#6ab0e8"), accessory="patillas")),
    Rival("sahel", "El Coronel del Sahel", "🌍", "un cuartel del Sahel",
          "Llegó al reñidero con un golpe de Estado y promete elecciones «pronto».",
          Fraction(40, 100), "Golpe de Estado",
          _look(body="#5a6a3a", hackle="#7a8a4a", wing="#4a5a2a",
                tail=("#3a4a2a", "#5a6a3a", "#8a7a4a"), accessory="boina")),
    Rival("arancel", "Arancel", "🇺🇸", "el Despacho Oval",
          "Tupé naranja, gorra roja y un 200 % de recargo a cada pluma que entra.",
          Fraction(35, 100), "Arancel del 200 %",
          _look(body="#f2a03a", hackle="#ffc85a", wing="#d0802a",
                tail=("#c8102e", "#f2f2f2", "#1a3a8a"), accessory="gorra")),
    Rival("credito", "Crédito Social", "🇨🇳", "Zhongnanhai",
          "Te quita puntos por cada picotazo que le das. Y por cada uno que no le das.",
          Fraction(30, 100), "Bajada de crédito social",
          _look(body="#c8102e", hackle="#ffd200", wing="#a00c24",
                tail=("#ffd200", "#c8102e", "#ffd200"), comb="#ffd200", accessory="camara")),
    Rival("cupula", "Cúpula de Hierro", "🇮🇱", "la Knesset",
          "Intercepta casi todo y lleva treinta años con el juicio aplazado.",
          Fraction(25, 100), "Intercepción",
          _look(body="#e8eef8", hackle="#ffffff", wing="#b8c8e8",
                tail=("#1a4ab8", "#e8eef8", "#1a4ab8"), accessory="escudo")),
    Rival("actas", "El Gallo de las Actas", "🇻🇪", "Miraflores",
          "Gana siempre según sus actas. Las actas no aparecen.",
          Fraction(15, 100), "Recuento propio",
          _look(body="#ffcc00", hackle="#1a3a8a", wing="#cf142b",
                tail=("#ffcc00", "#1a3a8a", "#cf142b"), accessory="bigote")),
    Rival("marte", "Gallo de Marte", "🚀", "una nave que explotó en el despegue",
          "Despide a medio gallinero a las 3 de la mañana y lo anuncia en X.",
          Fraction(10, 100), "Despido masivo",
          _look(body="#2a2a2a", hackle="#4a4a4a", wing="#1a1a1a",
                tail=("#e8e8e8", "#8a8a8a", "#e8e8e8"), comb="#e8e8e8", accessory="casco")),
)
# fmt: on
RIVAL_BY_KEY: dict[str, Rival] = {r.key: r for r in HOUSE}


def house_multiplier(chance: Fraction) -> Fraction:
    """Cuota de ganar con probabilidad `chance`: devuelve de media el `RTP`.

    Raises:
        ValueError: Si la probabilidad no está entre 0 y 1 (sin incluirlos).
    """
    if not 0 < chance < 1:
        raise ValueError("La probabilidad tiene que estar entre 0 y 1.")
    return RTP / chance


def house_payout(stake: int, rival: Rival) -> int:
    """Lo que cobra quien le gana a `rival` con `stake` (apuesta incluida, redondeado abajo)."""
    return int(stake * rival.multiplier)


def format_odds(multiplier: Fraction | float) -> str:
    """`Fraction(19, 10)` → `×1,90`."""
    return "×" + f"{float(multiplier):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def duel_prize(stake: int) -> int:
    """Lo que se lleva el ganador de un duelo en el que cada uno puso `stake`."""
    return int(2 * stake * (1 - DUEL_CUT))


# -- La pelea ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Exchange:
    """Un intercambio: uno ataca y el otro recibe (o esquiva).

    Attributes:
        round: Asalto (desde 1).
        attacker: Quién ataca.
        move: Clave del golpe (`MOVES`).
        damage: Daño hecho (0 si esquiva).
        crit: Si fue crítico.
        dodged: Si el que recibe lo esquivó.
        hp: Vida de rojo y azul después del intercambio.
    """

    round: int
    attacker: Corner
    move: str
    damage: int
    crit: bool
    dodged: bool
    hp: tuple[int, int]

    def mirrored(self) -> Exchange:
        """El mismo intercambio con las esquinas cambiadas."""
        return replace(self, attacker=self.attacker.other, hp=(self.hp[1], self.hp[0]))


@dataclass(frozen=True, slots=True)
class Fight:
    """Una pelea ya decidida y contada.

    Attributes:
        winner: Quién gana.
        finish: Cómo acaba.
        exchanges: Los intercambios, en orden.
    """

    winner: Corner
    finish: Finish
    exchanges: tuple[Exchange, ...]

    @property
    def loser(self) -> Corner:
        """Quién pierde."""
        return self.winner.other

    @property
    def rounds(self) -> int:
        """Asaltos que duró."""
        return self.exchanges[-1].round if self.exchanges else 1

    def hp(self, corner: Corner) -> int:
        """Vida con la que acaba `corner`."""
        last = self.exchanges[-1].hp if self.exchanges else (MAX_HP, MAX_HP)
        return last[0] if corner is Corner.RED else last[1]

    def hits(self, corner: Corner) -> list[Exchange]:
        """Golpes que `corner` acertó."""
        return [e for e in self.exchanges if e.attacker is corner and not e.dodged]

    def crits(self, corner: Corner) -> int:
        """Críticos que dio `corner`."""
        return sum(1 for e in self.hits(corner) if e.crit)

    def damage_taken(self, corner: Corner) -> int:
        """Vida que perdió `corner`."""
        return MAX_HP - self.hp(corner)

    def dodges(self, corner: Corner) -> int:
        """Golpes que esquivó `corner`."""
        return sum(1 for e in self.exchanges if e.attacker is corner.other and e.dodged)

    def mirrored(self) -> Fight:
        """La misma pelea con las esquinas cambiadas."""
        return Fight(
            winner=self.winner.other,
            finish=self.finish,
            exchanges=tuple(e.mirrored() for e in self.exchanges),
        )


def _pick_move(rng: random.Random) -> Move:
    return rng.choices(MOVES, weights=[m.weight for m in MOVES])[0]


def _natural(rng: random.Random) -> Fight:
    """Una pelea simétrica: cada intercambio lo empieza cualquiera de los dos al 50 %."""
    hp = {Corner.RED: MAX_HP, Corner.BLUE: MAX_HP}
    exchanges: list[Exchange] = []
    for round_number in range(1, ROUNDS + 1):
        for _ in range(rng.randint(*EXCHANGES_PER_ROUND)):
            attacker = Corner.RED if rng.random() < 0.5 else Corner.BLUE
            target = attacker.other
            move = _pick_move(rng)
            dodged = rng.random() < DODGE_CHANCE
            crit = not dodged and rng.random() < CRIT_CHANCE
            damage = 0 if dodged else rng.randint(*move.damage) * (2 if crit else 1)
            hp[target] = max(0, hp[target] - damage)
            exchanges.append(
                Exchange(
                    round_number,
                    attacker,
                    move.key,
                    damage,
                    crit,
                    dodged,
                    (hp[Corner.RED], hp[Corner.BLUE]),
                )
            )
            if hp[target] == 0:
                return Fight(attacker, Finish.KO, tuple(exchanges))
            if not dodged and hp[target] <= FLEE_HP and rng.random() < FLEE_CHANCE:
                return Fight(attacker, Finish.FLED, tuple(exchanges))
    if hp[Corner.RED] != hp[Corner.BLUE]:
        winner = Corner.RED if hp[Corner.RED] > hp[Corner.BLUE] else Corner.BLUE
    else:
        # Empate a vida: los jueces se lo dan al último que acertó.
        landed = [e for e in exchanges if not e.dodged]
        winner = landed[-1].attacker if landed else Corner.RED
    return Fight(winner, Finish.POINTS, tuple(exchanges))


def simulate(winner: Corner, rng: random.Random) -> Fight:
    """Cuenta una pelea que gana `winner` (ver la docstring del módulo)."""
    fight = _natural(rng)
    return fight if fight.winner is winner else fight.mirrored()


def draw_winner(chance: Fraction | float, rng: random.Random) -> Corner:
    """Sortea el ganador: el rojo gana con probabilidad `chance`."""
    return Corner.RED if rng.random() < chance else Corner.BLUE


# -- Porra del público -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PoolBet:
    """Una apuesta del público: por qué esquina y cuánto."""

    corner: Corner
    stake: int


def pool_totals(bets: Mapping[int, PoolBet]) -> dict[Corner, int]:
    """Lo apostado a cada esquina."""
    totals = {Corner.RED: 0, Corner.BLUE: 0}
    for bet in bets.values():
        totals[bet.corner] += bet.stake
    return totals


def pool_odds(bets: Mapping[int, PoolBet]) -> dict[Corner, Fraction | None]:
    """Cuota de cada esquina si ganara ahora mismo; `None` si nadie le ha apostado.

    Es la cuota «a la porra»: lo que hay en total, menos la comisión, entre lo
    que hay a esa esquina, y nunca menos de `MIN_POOL_PAYOUT`.
    """
    totals = pool_totals(bets)
    pot = sum(totals.values()) * (1 - POOL_CUT)
    return {
        corner: max(MIN_POOL_PAYOUT, pot / total) if total else None
        for corner, total in totals.items()
    }


def pool_payouts(bets: Mapping[int, PoolBet], winner: Corner) -> dict[int, int]:
    """Lo que cobra cada apostante del público (apuesta incluida; 0 si falla).

    Si una de las dos esquinas no tiene ninguna apuesta, no hay porra: se
    devuelve a cada uno lo suyo.
    """
    totals = pool_totals(bets)
    if not totals[Corner.RED] or not totals[Corner.BLUE]:
        return {user: bet.stake for user, bet in bets.items()}
    odds = pool_odds(bets)[winner]
    assert odds is not None  # las dos esquinas tienen apuestas
    return {
        user: int(bet.stake * odds) if bet.corner is winner else 0 for user, bet in bets.items()
    }


# -- La ficha del gallo ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoosterRecord:
    """El historial de un gallo (no toca ninguna probabilidad).

    Attributes:
        name: Su nombre.
        fights: Peleas.
        wins: Victorias.
        kos: Victorias por KO.
        streak: Victorias seguidas ahora mismo.
        best_streak: La racha más larga.
        day_streak: Días seguidos peleando.
        last_day: Último día que peleó (`None` si nunca).
    """

    name: str
    fights: int = 0
    wins: int = 0
    kos: int = 0
    streak: int = 0
    best_streak: int = 0
    day_streak: int = 0
    last_day: date | None = None

    @property
    def losses(self) -> int:
        """Derrotas."""
        return self.fights - self.wins


def after_fight(record: RoosterRecord, *, won: bool, finish: Finish, day: date) -> RoosterRecord:
    """La ficha tras una pelea que acaba `finish` el día `day`."""
    streak = record.streak + 1 if won else 0
    if record.last_day == day:
        day_streak = record.day_streak
    elif record.last_day is not None and (day - record.last_day).days == 1:
        day_streak = record.day_streak + 1
    else:
        day_streak = 1
    return replace(
        record,
        fights=record.fights + 1,
        wins=record.wins + int(won),
        kos=record.kos + int(won and finish is Finish.KO),
        streak=streak,
        best_streak=max(record.best_streak, streak),
        day_streak=day_streak,
        last_day=day,
    )


def default_name(user_id: int) -> str:
    """Nombre de serie de un gallo: siempre el mismo para el mismo dueño."""
    return DEFAULT_NAMES[user_id % len(DEFAULT_NAMES)]


_MARKUP = re.compile(r"[`*_~|<>@#\\]")


def clean_name(text: str) -> str:
    """Valida el nombre que pone el dueño: sin menciones ni formato, como mucho `NAME_MAX`.

    Raises:
        ValueError: Con un mensaje mostrable si queda vacío o es demasiado largo.
    """
    name = " ".join(_MARKUP.sub("", text).split())
    if not name:
        raise ValueError("Ponle un nombre con letras de verdad.")
    if len(name) > NAME_MAX:
        raise ValueError(f"Como mucho {NAME_MAX} caracteres: es un gallo, no un decreto.")
    return name


# -- Narración ---------------------------------------------------------------------------


def move_name(exchange: Exchange, signature: Mapping[Corner, str]) -> str:
    """Nombre del golpe de un intercambio (el estrella si es la patada de un rival)."""
    if exchange.move == SIGNATURE_MOVE and exchange.attacker in signature:
        return signature[exchange.attacker]
    return MOVE_BY_KEY[exchange.move].name


def narrate(
    fight: Fight, names: Mapping[Corner, str], signature: Mapping[Corner, str] | None = None
) -> list[str]:
    """Las frases de la crónica: el golpe más gordo, el final y cómo acabó."""
    signature = signature or {}
    lines: list[str] = []
    landed = [e for e in fight.exchanges if not e.dodged]
    if landed:
        best = max(landed, key=lambda e: e.damage)
        crit = " **crítico**" if best.crit else ""
        lines.append(
            f"💥 El golpe de la noche: {move_name(best, signature)}{crit} de "
            f"**{names[best.attacker]}** (−{best.damage})."
        )
    dodges = {c: fight.dodges(c) for c in Corner}
    agile = max(Corner, key=lambda c: dodges[c])
    if dodges[agile] >= 3:
        lines.append(f"🌀 **{names[agile]}** esquivó {dodges[agile]} golpes. Ni el Falcon.")
    last = fight.exchanges[-1] if fight.exchanges else None
    winner, loser = names[fight.winner], names[fight.loser]
    if fight.finish is Finish.KO and last is not None:
        lines.append(
            f"🥊 {move_name(last, signature)} de **{winner}** y **{loser}** ve estrellitas. "
            f"KO en el asalto {fight.rounds}."
        )
    elif fight.finish is Finish.FLED:
        lines.append(f"🏃 **{loser}** sale por patas del reñidero. **{winner}** canta victoria.")
    else:
        lines.append(
            f"⚖️ Tres asaltos y a los jueces: **{winner}** gana a los puntos "
            f"({fight.hp(fight.winner)} a {fight.hp(fight.loser)} de vida)."
        )
    return lines
