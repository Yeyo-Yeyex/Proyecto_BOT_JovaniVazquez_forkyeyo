"""Loterías del Estado: reglas, probabilidades y reparto de premios, sin Discord.

Imita los juegos reales con sus normas oficiales, pasados a yapdollars con
`YAPDOLLARS_PER_EURO` (10 Y$ = 1 €). La cuenta del Estado hace de banca:
cobra los boletos, paga los premios y garantiza el bote mínimo donde el juego
real lo tiene.

Juegos de bote (reparto a partes iguales entre acertantes de cada categoría):

- **La Primitiva** (lunes, jueves y sábado, 1 €): 6 de 49, complementario y
  reintegro. Normas de SELAE de junio de 2022: el 55 % de la recaudación va a
  premios (7ª), el 10 % al reintegro y el 45 % a las categorías; la 5ª (3
  aciertos) cobra 8 € fijos y el resto se reparte 30/37/6/11/16 % entre
  especial (6 + reintegro), 1ª, 2ª, 3ª y 4ª (8ª.1). Si la especial o la 1ª
  quedan desiertas, su fondo pasa a la especial del sorteo siguiente (8ª.1.1
  y 1.2); la 2ª baja a la 3ª, la 3ª a la 4ª y la 4ª al bote (8ª.1.3).
- **Bonoloto** (lunes a sábado, 0,50 €): igual pero sin categoría especial;
  45/24/12/19 % y 4 € fijos para 3 aciertos (normas de marzo de 2015).
- **El Gordo de la Primitiva** (domingo, 1,50 €): 5 de 54 y número clave. El
  22 % de la recaudación va a la 1ª y el 23 %, menos los 3 € fijos de la 8ª,
  se reparte 33/6/7/8/26/20 % entre la 2ª y la 7ª (normas de enero de 2024,
  8ª). La mitad de lo destinado a la 1ª va al fondo de reserva, que paga el
  mínimo garantizado de 4,5 M€ (9ª): aquí esa mitad se queda en el Estado,
  que es quien garantiza.
- **Euromillones** (martes y viernes, 2,20 €): 5 de 50 y 2 estrellas de 12.
  El 50 % va a premios en 13 categorías; el 4,80 % del fondo va a la reserva
  que garantiza el bote mínimo de 17 M€ (aquí, al Estado). Una categoría
  desierta pasa a la siguiente inferior con acertantes. No se vende El Millón
  (los 0,30 € de España): es un sorteo entre todos los boletos españoles que
  siempre toca a alguien y en un servidor pequeño regalaría el dinero.

En los cuatro, ningún premio de una categoría puede ser mayor que el de una
superior: si pasa, se juntan los fondos y se reparte entre todos (8ª.2 de la
Primitiva y equivalentes). Eso es una regresión isotónica, `_no_inversion`.

Lotería Nacional (premios fijos por décimo, el 70 % de la emisión):

- **Jueves** (3 €) y **sábado** (6 €): 1º y 2º premio, aproximaciones,
  centenas, últimas cifras del 1º, extracciones de 4, 3 y 2 cifras y tres
  reintegros. Programas sacados de los sorteos de septiembre y octubre de 2026.
- **Navidad** (22 de diciembre, 20 €): Gordo, 2º, 3º, dos 4º, ocho 5º, 1.794
  pedreas, aproximaciones, centenas, dos últimas cifras y reintegro.
- **El Niño** (6 de enero, 20 €): tres premios, extracciones y tres reintegros.

Rascas de la ONCE (instantáneos, con la tabla de premios de su emisión):
**X10** (2 €, devuelve el 64 %) y **7 y Media** (1 €, devuelve el 59 %).

Las probabilidades son las reales. El gordo de cualquier juego es, en la
práctica, inalcanzable; lo que toca a menudo son reintegros y premios bajos.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from bot.services.taxes import YAPDOLLARS_PER_EURO, lottery_tax

#: Los sorteos se celebran en Madrid; Discord enseña la hora local de cada uno.
DRAW_TIMEZONE = ZoneInfo("Europe/Madrid")
#: Parte del saldo del Estado que puede comprometer en un bote garantizado.
GUARANTEE_SHARE = 0.25
#: Apuestas (o décimos) como máximo por persona y sorteo. Evita filas sin fin
#: en la base de datos del NAS; nadie juega 100 apuestas por gusto.
MAX_PER_DRAW = 100
#: Números de la Lotería Nacional: del 00000 al 99999.
NACIONAL_NUMBERS = 100_000


def eur(amount: float) -> int:
    """Euros reales a yapdollars."""
    return round(amount * YAPDOLLARS_PER_EURO)


class Kind(StrEnum):
    """Familia de juego: decide cómo se apuesta, se sortea y se paga."""

    LOTTO = "lotto"  # 6 de 49 + complementario + reintegro
    GORDO = "gordo"  # 5 de 54 + número clave
    EURO = "euro"  # 5 de 50 + 2 estrellas de 12
    NACIONAL = "nacional"
    SCRATCH = "rasca"


class LotteryError(Exception):
    """Algo que el jugador ha hecho mal; el mensaje se le enseña tal cual."""


# -- Categorías de los juegos de bote ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Category:
    """Una categoría de premio de un juego de bote.

    Attributes:
        key: Identificador corto (`"1"`, `"especial"`…).
        label: Nombre que se enseña.
        rule: Qué hay que acertar, en pocas palabras.
        ways: Combinaciones de las `Game.combinations` que caen aquí (para la
            probabilidad exacta).
        share: Parte del fondo variable, o `None` si el premio es fijo.
        fixed: Premio fijo en Y$, si lo tiene.
        jackpot: Si es la categoría del bote (desierta pasa al sorteo siguiente).
    """

    key: str
    label: str
    rule: str
    ways: int
    share: float | None = None
    fixed: int | None = None
    jackpot: bool = False

    def odds(self, combinations: int) -> int:
        """`N` de "1 entre N"."""
        return round(combinations / self.ways)


# -- Juegos -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Prize:
    """Premio fijo de la Lotería Nacional o de un rasca.

    Attributes:
        key: Identificador estable.
        label: Nombre que se enseña.
        amount: Y$ por décimo o boleto.
        count: Cuántos hay (números premiados, extracciones o boletos de la
            emisión de un rasca).
    """

    key: str
    label: str
    amount: int
    count: int = 1


@dataclass(frozen=True, slots=True)
class MainPrize:
    """Un premio "con nombre" de la Lotería Nacional y sus premios derivados.

    Attributes:
        approx: Y$ por décimo para el número anterior y el posterior.
        centena: Y$ por décimo para los otros 99 números de su centena.
        endings: `(cifras, Y$)` para los números que acaban igual; solo se
            cobra la terminación más larga.
    """

    key: str
    label: str
    amount: int
    count: int = 1
    approx: int = 0
    centena: int = 0
    endings: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class NacionalProgram:
    """Programa de premios de un sorteo de la Lotería Nacional (por décimo).

    Attributes:
        extractions: `(cifras, cuántas, Y$)`: extracciones especiales.
        pedrea: Premios sueltos de la Navidad (`Prize` con su número).
        reintegros: Cuántas cifras dan reintegro (la del 1º y extracciones).
        reintegro_excludes_first: En Navidad el Gordo no cobra además el
            reintegro (son 9.999 números, no 10.000).
    """

    main: tuple[MainPrize, ...]
    extractions: tuple[tuple[int, int, int], ...] = ()
    pedrea: Prize | None = None
    reintegros: int = 1
    reintegro_excludes_first: bool = False


@dataclass(frozen=True, slots=True)
class Game:
    """Un juego a la venta.

    Attributes:
        key: Identificador estable (se guarda en la base de datos).
        price: Precio de una apuesta, décimo o rasca, en Y$.
        prize_rate: Parte de la recaudación que vuelve en premios.
        weekly: `(día de la semana, hora, minuto)` de los sorteos, hora de
            Madrid (lunes = 0). Vacío para los sorteos anuales y los rascas.
        yearly: `(mes, día, hora, minuto)` de un sorteo anual.
        variable_share: Parte de la recaudación para el fondo de categorías.
        reserve: Parte de ese fondo (o de la 1ª, en el Gordo) que va al Estado
            como fondo de reserva del bote garantizado.
        guarantee: Bote mínimo real, en Y$ (0 si no tiene).
        reintegro: Si tiene reintegro (devuelve el precio de la apuesta).
        categories: De mayor a menor premio.
        program: Programa de la Lotería Nacional.
        scratch: Tabla de premios de un rasca: `(premios, boletos de la emisión)`.
    """

    key: str
    name: str
    emoji: str
    kind: Kind
    price: int
    prize_rate: float
    weekly: tuple[tuple[int, int, int], ...] = ()
    yearly: tuple[int, int, int, int] | None = None
    variable_share: float = 0.0
    reserve: float = 0.0
    guarantee: int = 0
    reintegro: bool = False
    categories: tuple[Category, ...] = ()
    program: NacionalProgram | None = None
    scratch: tuple[tuple[Prize, ...], int] | None = None
    blurb: str = ""

    @property
    def combinations(self) -> int:
        """Combinaciones posibles de una apuesta (para las probabilidades)."""
        if self.kind is Kind.LOTTO:
            return math.comb(49, 6) * 10
        if self.kind is Kind.GORDO:
            return math.comb(54, 5) * 10
        if self.kind is Kind.EURO:
            return math.comb(50, 5) * math.comb(12, 2)
        return NACIONAL_NUMBERS

    @property
    def scheduled(self) -> bool:
        """Si tiene sorteos (los rascas se resuelven al momento)."""
        return self.kind is not Kind.SCRATCH

    def category(self, key: str) -> Category:
        """La categoría `key` del juego."""
        return next(c for c in self.categories if c.key == key)


def _lotto_categories(*, especial: bool, shares: Sequence[float], fixed: int) -> tuple:
    """Categorías de los juegos 6/49. `ways` cuenta sobre 49C6 × 10 reintegros."""
    five = math.comb(6, 5)
    cats = []
    if especial:
        cats.append(Category("especial", "Especial", "6 + reintegro", 1, shares[0], jackpot=True))
        shares = shares[1:]
    cats += [
        Category("1", "1ª", "6 aciertos", 9 if especial else 10, shares[0], jackpot=not especial),
        Category("2", "2ª", "5 + complementario", five * 10, shares[1]),
        Category("3", "3ª", "5 aciertos", five * 42 * 10, shares[2]),
        Category("4", "4ª", "4 aciertos", math.comb(6, 4) * math.comb(43, 2) * 10, shares[3]),
        Category("5", "5ª", "3 aciertos", math.comb(6, 3) * math.comb(43, 3) * 10, fixed=fixed),
    ]
    return tuple(cats)


def _gordo_categories() -> tuple[Category, ...]:
    def ways(hits: int, clave: bool) -> int:
        return math.comb(5, hits) * math.comb(49, 5 - hits) * (1 if clave else 9)

    rows = [
        ("1", "5 + clave", 5, True, None),
        ("2", "5 aciertos", 5, False, 0.33),
        ("3", "4 + clave", 4, True, 0.06),
        ("4", "4 aciertos", 4, False, 0.07),
        ("5", "3 + clave", 3, True, 0.08),
        ("6", "3 aciertos", 3, False, 0.26),
        ("7", "2 + clave", 2, True, 0.20),
    ]
    cats = [
        Category(key, f"{key}ª", rule, ways(hits, clave), share, jackpot=key == "1")
        for key, rule, hits, clave, share in rows
    ]
    cats.append(Category("8", "8ª", "2 aciertos", ways(2, False), fixed=eur(3)))
    return tuple(cats)


#: Euromillones: (números, estrellas, % del fondo). Normas vigentes en 2026.
_EURO_TABLE = (
    (5, 2, 0.4320),
    (5, 1, 0.0395),
    (5, 0, 0.0092),
    (4, 2, 0.0045),
    (4, 1, 0.0048),
    (4, 0, 0.0067),
    (3, 2, 0.0038),
    (2, 2, 0.0175),
    (3, 1, 0.0185),
    (3, 0, 0.0350),
    (1, 2, 0.0495),
    (2, 1, 0.1485),
    (2, 0, 0.1825),
)
#: Lo que queda del fondo (4,80 %) va a la reserva del bote garantizado.
_EURO_RESERVE = 0.0480


def _euro_categories() -> tuple[Category, ...]:
    cats = []
    for index, (hits, stars, share) in enumerate(_EURO_TABLE, start=1):
        ways = (
            math.comb(5, hits)
            * math.comb(45, 5 - hits)
            * math.comb(2, stars)
            * math.comb(10, 2 - stars)
        )
        rule = f"{hits} + {stars} ★"
        cats.append(Category(str(index), f"{index}ª", rule, ways, share, jackpot=index == 1))
    return tuple(cats)


_JUEVES = NacionalProgram(
    main=(
        MainPrize(
            "1", "1er premio", eur(30_000), approx=eur(1_200), centena=eur(30),
            endings=((4, eur(75)), (3, eur(15)), (2, eur(6))),
        ),
        MainPrize("2", "2º premio", eur(6_000), approx=eur(747), centena=eur(15)),
    ),
    extractions=((4, 4, eur(75)), (3, 7, eur(15)), (2, 9, eur(6))),
    reintegros=3,
)  # fmt: skip
_SABADO = NacionalProgram(
    main=(
        MainPrize(
            "1", "1er premio", eur(60_000), approx=eur(1_000), centena=eur(30),
            endings=((4, eur(150)), (3, eur(30)), (2, eur(12))),
        ),
        MainPrize("2", "2º premio", eur(12_000), approx=eur(554), centena=eur(30)),
    ),
    extractions=((4, 4, eur(150)), (3, 10, eur(30)), (2, 9, eur(12))),
    reintegros=3,
)  # fmt: skip
_NAVIDAD = NacionalProgram(
    main=(
        MainPrize(
            "1", "EL GORDO", eur(400_000), approx=eur(2_000), centena=eur(100),
            endings=((2, eur(100)),),
        ),
        MainPrize(
            "2", "2º premio", eur(125_000), approx=eur(1_250), centena=eur(100),
            endings=((2, eur(100)),),
        ),
        MainPrize(
            "3", "3er premio", eur(50_000), approx=eur(960), centena=eur(100),
            endings=((2, eur(100)),),
        ),
        MainPrize("4", "4º premio", eur(20_000), count=2, centena=eur(100)),
        MainPrize("5", "5º premio", eur(6_000), count=8),
    ),
    pedrea=Prize("pedrea", "Pedrea", eur(100), 1_794),
    reintegros=1,
    reintegro_excludes_first=True,
)  # fmt: skip
_NINO = NacionalProgram(
    main=(
        MainPrize(
            "1", "1er premio", eur(200_000), approx=eur(1_200), centena=eur(100),
            endings=((3, eur(100)), (2, eur(100))),
        ),
        MainPrize(
            "2", "2º premio", eur(75_000), approx=eur(610), centena=eur(100),
            endings=((3, eur(100)),),
        ),
        MainPrize("3", "3er premio", eur(25_000), centena=eur(100)),
    ),
    extractions=((4, 2, eur(350)), (3, 14, eur(100)), (2, 5, eur(40))),
    reintegros=3,
)  # fmt: skip

#: Rasca X10 de la ONCE: 9.000.000 de boletos por emisión.
_X10 = (
    tuple(
        Prize(f"x10_{euros}", f"{euros:,} €".replace(",", "."), eur(euros), count)
        for euros, count in (
            (150_000, 3), (10_000, 5), (2_000, 10), (1_000, 100), (200, 500), (100, 1_000),
            (40, 10_450), (20, 75_000), (10, 435_000), (4, 533_000), (2, 1_150_000),
        )
    ),
    9_000_000,
)  # fmt: skip
#: Rasca 7 y Media de la ONCE: 6.000.000 de boletos por emisión.
_SIETE_Y_MEDIA = (
    tuple(
        Prize(f"7m_{int(euros * 100)}", f"{euros:g} €".replace(".", ","), eur(euros), count)
        for euros, count in (
            (7_500, 12), (750, 200), (150, 1_000), (75, 2_000), (30, 4_000), (15, 14_969),
            (7.5, 66_000), (4, 171_000), (2, 370_800), (1, 735_000),
        )
    ),
    6_000_000,
)  # fmt: skip

_MON, _TUE, _WED, _THU, _FRI, _SAT, _SUN = range(7)

GAMES: tuple[Game, ...] = (
    Game(
        "jueves", "Lotería Nacional del jueves", "🎫", Kind.NACIONAL, eur(3), 0.70,
        weekly=((_THU, 21, 30),), program=_JUEVES,
        blurb="Décimo de 3 €. 1er premio de 30.000 € al décimo.",
    ),
    Game(
        "sabado", "Lotería Nacional del sábado", "🎫", Kind.NACIONAL, eur(6), 0.70,
        weekly=((_SAT, 13, 0),), program=_SABADO,
        blurb="Décimo de 6 €. 1er premio de 60.000 € al décimo.",
    ),
    Game(
        "navidad", "Lotería de Navidad", "🎄", Kind.NACIONAL, eur(20), 0.70,
        yearly=(12, 22, 9, 0), program=_NAVIDAD,
        blurb="Décimo de 20 €. El Gordo: 400.000 € al décimo. Sorteo el 22 de diciembre.",
    ),
    Game(
        "nino", "Lotería del Niño", "👶", Kind.NACIONAL, eur(20), 0.70,
        yearly=(1, 6, 12, 0), program=_NINO,
        blurb="Décimo de 20 €. 1er premio de 200.000 € al décimo. Sorteo el 6 de enero.",
    ),
    Game(
        "primitiva", "La Primitiva", "🔵", Kind.LOTTO, eur(1), 0.55,
        weekly=((_MON, 21, 30), (_THU, 21, 30), (_SAT, 21, 30)),
        variable_share=0.45, reintegro=True,
        categories=_lotto_categories(
            especial=True, shares=(0.30, 0.37, 0.06, 0.11, 0.16), fixed=eur(8)
        ),
        blurb="6 de 49. El bote es de la categoría especial: 6 aciertos + reintegro.",
    ),
    Game(
        "bonoloto", "Bonoloto", "🟢", Kind.LOTTO, eur(0.5), 0.55,
        weekly=tuple((day, 21, 30) for day in (_MON, _TUE, _WED, _THU, _FRI, _SAT)),
        variable_share=0.45, reintegro=True,
        categories=_lotto_categories(especial=False, shares=(0.45, 0.24, 0.12, 0.19), fixed=eur(4)),
        blurb="6 de 49, de lunes a sábado. La más barata.",
    ),
    Game(
        "gordo", "El Gordo de la Primitiva", "🟡", Kind.GORDO, eur(1.5), 0.55,
        weekly=((_SUN, 21, 30),), variable_share=0.45, reserve=0.5,
        guarantee=eur(4_500_000), reintegro=True, categories=_gordo_categories(),
        blurb="5 de 54 y número clave, los domingos. Bote mínimo garantizado.",
    ),
    Game(
        "euromillones", "Euromillones", "⭐", Kind.EURO, eur(2.2), 0.50,
        weekly=((_TUE, 21, 0), (_FRI, 21, 0)), variable_share=0.50, reserve=_EURO_RESERVE,
        guarantee=eur(17_000_000), categories=_euro_categories(),
        blurb="5 de 50 y 2 estrellas de 12. Sin El Millón. Bote mínimo garantizado.",
    ),
    Game(
        "x10", "Rasca X10", "🟣", Kind.SCRATCH, eur(2), 0.64, scratch=_X10,
        blurb="Rasca de la ONCE de 2 €. Premio máximo: 150.000 €.",
    ),
    Game(
        "7ymedia", "Rasca 7 y Media", "🃏", Kind.SCRATCH, eur(1), 0.59, scratch=_SIETE_Y_MEDIA,
        blurb="Rasca de la ONCE de 1 €. Premio máximo: 7.500 €.",
    ),
)  # fmt: skip
GAME_BY_KEY: dict[str, Game] = {g.key: g for g in GAMES}


# -- Calendario -------------------------------------------------------------------------


def next_draw(game: Game, now: float) -> float:
    """Momento (epoch) del próximo sorteo de `game` estrictamente después de `now`."""
    current = datetime.fromtimestamp(now, DRAW_TIMEZONE)
    if game.yearly is not None:
        month, day, hour, minute = game.yearly
        for year in (current.year, current.year + 1):
            moment = datetime(year, month, day, hour, minute, tzinfo=DRAW_TIMEZONE)
            if moment.timestamp() > now:
                return moment.timestamp()
    if not game.weekly:
        raise ValueError(f"{game.key} no tiene sorteos")
    best: float | None = None
    for weekday, hour, minute in game.weekly:
        days = (weekday - current.weekday()) % 7
        for extra in (days, days + 7):
            day = current.date() + timedelta(days=extra)
            moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=DRAW_TIMEZONE)
            if moment.timestamp() > now:
                best = moment.timestamp() if best is None else min(best, moment.timestamp())
                break
    assert best is not None  # siempre hay un sorteo en los próximos 8 días
    return best


def draw_label(game: Game, draw_at: float) -> str:
    """`Primitiva · jueves 08/10` para un sorteo."""
    days = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    moment = datetime.fromtimestamp(draw_at, DRAW_TIMEZONE)
    return f"{game.name} · {days[moment.weekday()]} {moment:%d/%m}"


# -- Apuestas ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pick:
    """Una apuesta de un juego de bote o un número de la Nacional.

    Attributes:
        numbers: Números elegidos, ordenados (el número de la Nacional va solo).
        extra: Reintegro, número clave o estrellas (ordenadas).
    """

    numbers: tuple[int, ...]
    extra: tuple[int, ...] = ()

    def encode(self) -> str:
        """Forma compacta para la base de datos: `3,14,22|7`."""
        return ",".join(map(str, self.numbers)) + "|" + ",".join(map(str, self.extra))

    @classmethod
    def decode(cls, text: str) -> Pick:
        """Inversa de `encode`."""
        numbers, _, extra = text.partition("|")
        return cls(
            tuple(int(n) for n in numbers.split(",") if n),
            tuple(int(n) for n in extra.split(",") if n),
        )


def _shape(game: Game) -> tuple[int, int, int, int]:
    """`(cuántos números, máximo, cuántos extra, máximo extra)` de un juego."""
    if game.kind is Kind.LOTTO:
        return 6, 49, 1, 9
    if game.kind is Kind.GORDO:
        return 5, 54, 1, 9
    if game.kind is Kind.EURO:
        return 5, 50, 2, 12
    raise ValueError(f"{game.key} no se juega con números")


def random_pick(game: Game, rng: random.Random) -> Pick:
    """Apuesta al azar (la "automática" de las administraciones)."""
    if game.kind is Kind.NACIONAL:
        return Pick((rng.randrange(NACIONAL_NUMBERS),))
    count, top, extra_count, extra_top = _shape(game)
    numbers = tuple(sorted(rng.sample(range(1, top + 1), count)))
    if game.kind is Kind.EURO:
        extra = tuple(sorted(rng.sample(range(1, extra_top + 1), extra_count)))
    else:
        extra = (rng.randint(0, extra_top),)
    return Pick(numbers, extra)


def parse_pick(game: Game, text: str, rng: random.Random) -> Pick:
    """Lee una apuesta escrita por el jugador.

    Primitiva y Bonoloto: `3 14 22 30 41 49` y, opcional, `R7`. Gordo:
    `3 14 22 30 41` y opcional `C7`. Euromillones: `3 14 22 30 41 * 2 9`
    (las estrellas tras `*` o `★`). Nacional: un número de hasta 5 cifras. Lo
    que no se marca (reintegro, clave, estrellas) se pone al azar, como en
    la administración.

    Raises:
        LotteryError: Si no es una apuesta válida; el texto explica por qué.
    """
    cleaned = text.strip().upper().replace(",", " ").replace("-", " ").replace("★", " * ")
    if game.kind is Kind.NACIONAL:
        if not cleaned.isdigit() or len(cleaned) > 5:
            raise LotteryError("El número de la Nacional tiene 5 cifras, del 00000 al 99999.")
        return Pick((int(cleaned),))
    count, top, extra_count, extra_top = _shape(game)
    main_text, _, star_text = cleaned.partition("*")
    numbers: list[int] = []
    extra: list[int] = []
    for token in main_text.split():
        if token[0] in "RC" and token[1:].isdigit() and game.kind is not Kind.EURO:
            extra.append(int(token[1:]))
        elif token.isdigit():
            numbers.append(int(token))
        else:
            raise LotteryError(f"No entiendo `{token}`.")
    if game.kind is Kind.EURO:
        extra = [int(t) for t in star_text.split() if t.isdigit()]
    if len(numbers) != count or len(set(numbers)) != count:
        raise LotteryError(f"Hacen falta {count} números distintos.")
    if any(not 1 <= n <= top for n in numbers):
        raise LotteryError(f"Los números van del 1 al {top}.")
    if game.kind is Kind.EURO:
        if extra and (len(extra) != 2 or len(set(extra)) != 2):
            raise LotteryError("Las estrellas son 2, distintas.")
        if any(not 1 <= s <= extra_top for s in extra):
            raise LotteryError("Las estrellas van del 1 al 12.")
        if not extra:
            extra = rng.sample(range(1, extra_top + 1), 2)
        return Pick(tuple(sorted(numbers)), tuple(sorted(extra)))
    if len(extra) > 1 or any(not 0 <= e <= 9 for e in extra):
        name = "reintegro" if game.kind is Kind.LOTTO else "número clave"
        raise LotteryError(f"El {name} es una cifra del 0 al 9.")
    return Pick(tuple(sorted(numbers)), tuple(extra) or (rng.randint(0, 9),))


def format_pick(game: Game, pick: Pick) -> str:
    """Una apuesta como se enseña: `03 14 22 30 41 49 · R7`."""
    if game.kind is Kind.NACIONAL:
        return f"{pick.numbers[0]:05d}"
    numbers = " ".join(f"{n:02d}" for n in pick.numbers)
    if game.kind is Kind.EURO:
        return f"{numbers} · ★ {' '.join(f'{s:02d}' for s in pick.extra)}"
    letter = "R" if game.kind is Kind.LOTTO else "C"
    return f"{numbers} · {letter}{pick.extra[0]}"


# -- Sorteos ----------------------------------------------------------------------------


def draw_result(game: Game, rng: random.Random) -> dict:
    """Saca las bolas de un sorteo. El resultado se guarda tal cual (JSON)."""
    if game.kind is Kind.LOTTO:
        balls = rng.sample(range(1, 50), 7)
        return {"numbers": sorted(balls[:6]), "comp": balls[6], "reintegro": rng.randint(0, 9)}
    if game.kind is Kind.GORDO:
        return {"numbers": sorted(rng.sample(range(1, 55), 5)), "clave": rng.randint(0, 9)}
    if game.kind is Kind.EURO:
        return {
            "numbers": sorted(rng.sample(range(1, 51), 5)),
            "stars": sorted(rng.sample(range(1, 13), 2)),
        }
    if game.kind is Kind.NACIONAL:
        return _draw_nacional(game, rng)
    raise ValueError(f"{game.key} no tiene sorteos")


def _draw_nacional(game: Game, rng: random.Random) -> dict:
    program = game.program
    assert program is not None
    needed = sum(p.count for p in program.main) + (program.pedrea.count if program.pedrea else 0)
    # Los premios principales y la pedrea salen de bombos distintos en la
    # realidad, pero nunca repiten número: se sacan sin reemplazo.
    numbers = rng.sample(range(NACIONAL_NUMBERS), needed)
    main: list[list] = []
    position = 0
    for prize in program.main:
        for _ in range(prize.count):
            main.append([prize.key, numbers[position]])
            position += 1
    pedrea = sorted(numbers[position:])
    # Las extracciones son independientes: pueden repetir terminación.
    extractions = {
        str(digits): [rng.randrange(10**digits) for _ in range(count)]
        for digits, count, _ in program.extractions
    }
    first_digit = main[0][1] % 10
    others = rng.sample([d for d in range(10) if d != first_digit], program.reintegros - 1)
    return {
        "main": main,
        "pedrea": pedrea,
        "extractions": extractions,
        "reintegros": [first_digit, *others],
    }


def format_result(game: Game, result: Mapping) -> str:
    """Combinación ganadora en una línea."""
    if game.kind is Kind.LOTTO:
        numbers = " ".join(f"**{n:02d}**" for n in result["numbers"])
        return f"{numbers} · C {result['comp']:02d} · R {result['reintegro']}"
    if game.kind is Kind.GORDO:
        numbers = " ".join(f"**{n:02d}**" for n in result["numbers"])
        return f"{numbers} · clave {result['clave']}"
    if game.kind is Kind.EURO:
        numbers = " ".join(f"**{n:02d}**" for n in result["numbers"])
        return f"{numbers} · ★ {' '.join(f'**{s:02d}**' for s in result['stars'])}"
    program = game.program
    assert program is not None
    labels = {p.key: p.label for p in program.main}
    lines = [f"{labels[key]}: **{number:05d}**" for key, number in result["main"]]
    for digits, numbers in result["extractions"].items():
        shown = " ".join(f"{n:0{digits}d}" for n in numbers)
        lines.append(f"Extracciones de {digits} cifras: {shown}")
    lines.append("Reintegros: " + ", ".join(str(d) for d in result["reintegros"]))
    return "\n".join(lines)


def classify(game: Game, pick: Pick, result: Mapping) -> tuple[str | None, bool]:
    """Categoría que gana una apuesta de un juego de bote y si cobra reintegro."""
    hits = len(set(pick.numbers) & set(result["numbers"]))
    if game.kind is Kind.LOTTO:
        reintegro = pick.extra[0] == result["reintegro"]
        if hits == 6:
            especial = any(c.key == "especial" for c in game.categories)
            return ("especial" if especial and reintegro else "1"), reintegro
        if hits == 5:
            return ("2" if result["comp"] in pick.numbers else "3"), reintegro
        return {4: "4", 3: "5"}.get(hits), reintegro
    if game.kind is Kind.GORDO:
        clave = pick.extra[0] == result["clave"]
        order = {(5, True): "1", (5, False): "2", (4, True): "3", (4, False): "4",
                 (3, True): "5", (3, False): "6", (2, True): "7", (2, False): "8"}  # fmt: skip
        return order.get((hits, clave)), clave
    if game.kind is Kind.EURO:
        stars = len(set(pick.extra) & set(result["stars"]))
        for index, (need_hits, need_stars, _) in enumerate(_EURO_TABLE, start=1):
            if (hits, stars) == (need_hits, need_stars):
                return str(index), False
        return None, False
    raise ValueError(f"{game.key} no es un juego de bote")


# -- Reparto de los juegos de bote -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CategoryResult:
    """Cómo ha quedado una categoría en un sorteo."""

    key: str
    winners: int
    prize: int


@dataclass(slots=True)
class Settlement:
    """Resultado de repartir un sorteo.

    Attributes:
        prizes: Premio bruto de cada boleto premiado (`id` → Y$).
        labels: Qué ha ganado cada boleto premiado, en texto.
        categories: Cómo ha quedado cada categoría (juegos de bote).
        carry: Bote que pasa al sorteo siguiente.
        topup: Lo que pone el Estado para llegar al bote garantizado.
    """

    prizes: dict[int, int] = field(default_factory=dict)
    labels: dict[int, list[str]] = field(default_factory=dict)
    categories: list[CategoryResult] = field(default_factory=list)
    carry: int = 0
    topup: int = 0

    @property
    def paid(self) -> int:
        """Total repartido en premios."""
        return sum(self.prizes.values())

    def add(self, ticket_id: int, amount: int, label: str) -> None:
        """Suma un premio a un boleto."""
        if amount <= 0:
            return
        self.prizes[ticket_id] = self.prizes.get(ticket_id, 0) + amount
        self.labels.setdefault(ticket_id, []).append(label)


def _no_inversion(funds: list[float], winners: list[int]) -> list[float]:
    """Premio por acertante sin que una categoría inferior cobre más que una superior.

    Norma 8ª.2 de la Primitiva: si una categoría inferior saldría mejor
    pagada, se juntan sus fondos y se reparten por igual entre todos sus
    acertantes, y así hasta que no quede ninguna inversión. Es el algoritmo
    "pool adjacent violators" con los acertantes como peso.

    Args:
        funds: Fondo de cada categoría con acertantes, de mayor a menor.
        winners: Acertantes de cada una (todos > 0).

    Returns:
        Premio por acertante de cada categoría, en el mismo orden.
    """
    blocks: list[list[float]] = []  # [fondo, acertantes, cuántas categorías]
    for fund, count in zip(funds, winners, strict=True):
        blocks.append([fund, count, 1])
        while len(blocks) > 1 and (blocks[-1][0] / blocks[-1][1] > blocks[-2][0] / blocks[-2][1]):
            fund_b, count_b, size_b = blocks.pop()
            blocks[-1][0] += fund_b
            blocks[-1][1] += count_b
            blocks[-1][2] += size_b
    prizes: list[float] = []
    for fund, count, size in blocks:
        prizes += [fund / count] * int(size)
    return prizes


def settle_pool(
    game: Game,
    *,
    sales: int,
    carry: int,
    guarantee: int,
    tickets: Iterable[tuple[int, Pick]],
    result: Mapping,
) -> Settlement:
    """Reparte un sorteo de Primitiva, Bonoloto, Gordo o Euromillones.

    Args:
        sales: Recaudación del sorteo en Y$.
        carry: Bote que viene de sorteos anteriores.
        guarantee: Bote mínimo que garantiza el Estado ahora (0 si ninguno).
        tickets: `(id, apuesta)` de cada apuesta vendida.
        result: Combinación ganadora (`draw_result`).
    """
    settlement = Settlement()
    by_category: dict[str, list[int]] = {c.key: [] for c in game.categories}
    for ticket_id, pick in tickets:
        key, reintegro = classify(game, pick, result)
        if key is not None:
            by_category[key].append(ticket_id)
        if reintegro and game.reintegro:
            settlement.add(ticket_id, game.price, "Reintegro")

    fixed_total = sum(
        len(by_category[c.key]) * (c.fixed or 0) for c in game.categories if c.fixed is not None
    )
    pool = sales * game.variable_share
    funds: dict[str, float] = {}
    if game.kind is Kind.GORDO:
        # Gordo: el 22 % de la recaudación es de la 1ª (la mitad va a la
        # reserva, que aquí es el Estado) y el 23 % menos la 8ª, del resto.
        first = sales * 0.22 * (1 - game.reserve)
        rest = max(0.0, sales * 0.23 - fixed_total)
        for cat in game.categories:
            if cat.jackpot:
                funds[cat.key] = first
            elif cat.share is not None:
                funds[cat.key] = rest * cat.share
    else:
        variable = max(0.0, pool - fixed_total)
        for cat in game.categories:
            if cat.share is not None:
                funds[cat.key] = variable * cat.share
    jackpot = next(c for c in game.categories if c.jackpot)
    funds[jackpot.key] += carry

    # Categorías desiertas: el bote pasa al sorteo siguiente; las demás bajan
    # a la siguiente categoría variable y la última también va al bote.
    variable_keys = [c.key for c in game.categories if c.share is not None or c.jackpot]
    next_carry = 0.0
    for index, key in enumerate(variable_keys):
        if by_category[key]:
            continue
        moved, funds[key] = funds[key], 0.0
        cat = game.category(key)
        if cat.jackpot or game.key == "primitiva" and key == "1":
            next_carry += moved
        elif index + 1 < len(variable_keys):
            funds[variable_keys[index + 1]] += moved
        else:
            next_carry += moved

    if by_category[jackpot.key] and funds[jackpot.key] < guarantee:
        settlement.topup = round(guarantee - funds[jackpot.key])
        funds[jackpot.key] = float(guarantee)

    won = [c for c in game.categories if by_category[c.key]]
    totals = [
        float(len(by_category[c.key]) * c.fixed) if c.fixed is not None else funds[c.key]
        for c in won
    ]
    per_winner = _no_inversion(totals, [len(by_category[c.key]) for c in won])
    paid = {c.key: math.floor(p) for c, p in zip(won, per_winner, strict=True)}
    for cat in game.categories:
        prize = paid.get(cat.key, 0)
        settlement.categories.append(CategoryResult(cat.key, len(by_category[cat.key]), prize))
        for ticket_id in by_category[cat.key]:
            settlement.add(ticket_id, prize, f"{cat.label} ({cat.rule})")
    settlement.carry = math.floor(next_carry)
    return settlement


def jackpot_estimate(game: Game, *, sales: int, carry: int, guarantee: int) -> int:
    """Bote que se anuncia para el próximo sorteo: acumulado más lo de este, o el mínimo."""
    jackpot = next(c for c in game.categories if c.jackpot)
    if game.kind is Kind.GORDO:
        share = sales * 0.22 * (1 - game.reserve)
    else:
        share = sales * game.variable_share * (jackpot.share or 0)
    return max(math.floor(carry + share), guarantee)


def guarantee_for(game: Game, state_balance: int) -> int:
    """Bote mínimo que garantiza ahora el Estado: el real, si le llega el dinero."""
    if not game.guarantee:
        return 0
    return min(game.guarantee, max(0, int(state_balance * GUARANTEE_SHARE)))


# -- Lotería Nacional --------------------------------------------------------------------


def settle_nacional(
    game: Game, *, tickets: Iterable[tuple[int, int, int]], result: Mapping
) -> Settlement:
    """Paga un sorteo de la Lotería Nacional: premios fijos por décimo.

    Los premios son compatibles entre sí (un décimo puede cobrar una centena,
    una extracción y el reintegro), salvo las terminaciones de un mismo
    premio, donde solo vale la más larga.

    Args:
        tickets: `(id, número, décimos)` de cada compra.
    """
    settlement = Settlement()
    for ticket_id, number, decimos in tickets:
        for amount, label in nacional_prizes(game, number, result):
            settlement.add(ticket_id, amount * decimos, label)
    return settlement


def nacional_prizes(game: Game, number: int, result: Mapping) -> list[tuple[int, str]]:
    """Premios por décimo de `number` en un sorteo de la Nacional: `(Y$, motivo)`."""
    program = game.program
    assert program is not None
    by_key = {p.key: p for p in program.main}
    won: list[tuple[int, str]] = []
    for key, winner in result["main"]:
        prize = by_key[key]
        if number == winner:
            won.append((prize.amount, prize.label))
            continue
        neighbours = ((winner - 1) % NACIONAL_NUMBERS, (winner + 1) % NACIONAL_NUMBERS)
        if prize.approx and number in neighbours:
            won.append((prize.approx, f"Aproximación al {prize.label}"))
        if prize.centena and number // 100 == winner // 100:
            won.append((prize.centena, f"Centena del {prize.label}"))
        for digits, amount in prize.endings:
            if number % 10**digits == winner % 10**digits:
                won.append((amount, f"{digits} últimas cifras del {prize.label}"))
                break
    if program.pedrea and number in set(result["pedrea"]):
        won.append((program.pedrea.amount, program.pedrea.label))
    amounts = {digits: amount for digits, _, amount in program.extractions}
    for digits_text, numbers in result["extractions"].items():
        digits = int(digits_text)
        hits = sum(1 for n in numbers if number % 10**digits == n)
        if hits:
            won.append((amounts[digits] * hits, f"Extracción de {digits} cifras"))
    first = result["main"][0][1]
    if number % 10 in result["reintegros"] and not (
        program.reintegro_excludes_first and number == first
    ):
        won.append((game.price, "Reintegro"))
    return won


def nacional_expected_return(game: Game, rng: random.Random) -> float:
    """Parte de la emisión que vuelve en premios en un sorteo concreto (≈ 0,70).

    Recorre los 100.000 números; solo para pruebas y comprobaciones.
    """
    result = _draw_nacional(game, rng)
    total = sum(
        amount
        for number in range(NACIONAL_NUMBERS)
        for amount, _ in nacional_prizes(game, number, result)
    )
    return total / (game.price * NACIONAL_NUMBERS)


# -- Rascas -------------------------------------------------------------------------------


def scratch(game: Game, rng: random.Random) -> Prize | None:
    """Rasca un boleto: saca su premio con las probabilidades de la emisión real."""
    assert game.scratch is not None
    prizes, total = game.scratch
    ticket = rng.randrange(total)
    for prize in prizes:
        if ticket < prize.count:
            return prize
        ticket -= prize.count
    return None


def scratch_odds(game: Game) -> float:
    """`N` de "1 de cada N boletos tiene premio", con un decimal."""
    assert game.scratch is not None
    prizes, total = game.scratch
    return round(total / sum(p.count for p in prizes), 1)


#: Importes que se pintan en las casillas de un rasca sin premio.
_DECOYS = (2, 4, 10, 20, 40, 100, 200, 1_000, 2_000, 10_000)


def scratch_grid(game: Game, prize: Prize | None, rng: random.Random) -> list[int]:
    """Las 9 casillas del rasca: tres iguales si hay premio, ninguna terna si no.

    Es la mecánica clásica de "3 importes iguales"; los reales tienen cada
    uno la suya, pero el premio sale de su tabla.
    """
    decoys = [eur(e) for e in _DECOYS]
    cells: list[int] = []
    if prize is not None:
        cells += [prize.amount] * 3
        decoys = [d for d in decoys if d != prize.amount]
    counts: Counter[int] = Counter()
    while len(cells) < 9:
        value = rng.choice(decoys)
        if counts[value] < 2:
            counts[value] += 1
            cells.append(value)
    rng.shuffle(cells)
    return cells


# -- Impuestos ----------------------------------------------------------------------------


def gravamen(prize_per_unit: int, units: int = 1) -> int:
    """Gravamen especial de `units` décimos o apuestas que cobran lo mismo cada uno."""
    return lottery_tax(prize_per_unit) * units
