"""Catálogo de los negocios de `pala`: sectores, productos, paletas, emblemas y habituales.

Solo datos. Las reglas que los usan están en `bot.services.business` y los minijuegos, en
`bot.services.business_games`. Las claves (`Sector.key`, `Product.key`, los índices de
`PALETTES` y `EMBLEMS`, `REGULAR_NAMES`) se guardan en la base de datos y las lee la escena
(`assets/negocio/escena.html`): no se cambian nunca. Lo nuevo se añade al final.

Las cifras van a la escala de las nóminas (`WAGE_YAPDOLLARS_PER_EURO`, 100 Y$ = 1 €): un
café solo de 1,50 € son 150 Y$. Son cifras de juego, calibradas con `docs/economia_sim.py`
(ver `docs/negocios.md`, «Equilibrio»): un turno a la par con un P3 de la `pala`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Product:
    """Un producto que se compra al mayorista y se vende (o un recambio del taller).

    Attributes:
        key: Clave estable (la dibuja la escena).
        name: Cómo se enseña.
        cost: Base del mayorista por unidad, sin IGIC.
        price: Precio de venta con el nivel de precios normal (en el taller, el
            presupuesto justo: recambio y mano de obra).
        igic: Clave de `taxes.IGIC_BY_KEY` de la compra al mayorista (pan y leche, tipo
            cero; el resto, el general del 7 %, art. 32.1 del Decreto Legislativo 1/2025).
        pack: Unidades de un lote del mayorista.
        spoil: Parte del stock que se pierde cada noche (0 = no caduca; 1 = se tira entero).
        weight: Lo que se pide frente a los demás productos del sector.
    """

    key: str
    name: str
    cost: int
    price: int
    igic: str = "general"
    pack: int = 10
    spoil: float = 0.0
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class Sector:
    """Un sector: su local, sus productos, su minijuego y su clientela.

    Attributes:
        key: Clave estable.
        name: Nombre corto.
        emoji: Emoji del menú.
        game: Minijuego (`comandas`, `caja` o `diagnostico`).
        blurb: Una línea para elegirlo.
        m2: Metros del local (el alquiler sale de ahí).
        kind: Tipo de inmueble de `pisos` cuyo `rent_factor` se usa (`local` o `nave`).
        products: Lo que se vende.
        clients: Clientes de un turno con todo a la media (barrio neutro, 3 estrellas,
            precios normales, hora de media).
        patience: Segundos que espera cada cliente.
        setup: Acondicionamiento del local (base, sin IGIC), al montarlo.
        license: Tasa municipal de la apertura.
        utilities: Suministros de cada noche (base, sin IGIC).
        order: `(mínimo, máximo)` de cosas que pide un cliente (en el taller, 1).
        curve: Cuánta gente viene a cada hora canaria (24 valores; 1 = la media).
        fit: Lo que multiplica la demanda el tema del barrio (`THEMES` de `pisos`).
    """

    key: str
    name: str
    emoji: str
    game: str
    blurb: str
    m2: int
    kind: str
    products: tuple[Product, ...]
    clients: int
    patience: int
    setup: int
    license: int
    utilities: int
    order: tuple[int, int]
    curve: tuple[float, ...]
    fit: dict[str, float]

    def product(self, key: str) -> Product | None:
        """El producto con esa clave, o `None`."""
        return next((p for p in self.products if p.key == key), None)


CAFETERIA = Sector(
    key="cafeteria",
    name="Cafetería",
    emoji="☕",
    game="comandas",
    blurb="Barraquitos, bocadillos y la tertulia de siempre. Cada cliente es una mesa.",
    m2=50,
    kind="local",
    products=(
        Product("cafe", "Café solo", 40, 150, pack=40, weight=1.4),
        Product("cortado", "Cortado", 50, 180, pack=40, weight=1.6),
        Product("barraquito", "Barraquito", 85, 280, pack=30, weight=1.0),
        Product("zumo", "Zumo natural", 110, 350, pack=20, spoil=0.4, weight=0.7),
        Product("bocadillo", "Bocadillo", 160, 550, pack=20, spoil=0.5, weight=1.0),
        Product("dulce", "Dulce", 70, 250, pack=20, spoil=0.5, weight=0.8),
    ),
    clients=8,
    patience=25,
    setup=120_000,
    license=15_000,
    utilities=1_200,
    order=(2, 5),
    # Desayunos y meriendas; de madrugada, los que salen de fiesta.
    curve=(
        0.25, 0.15, 0.15, 0.15, 0.15, 0.3, 0.6, 1.2, 1.4, 1.3, 1.2, 1.0,
        0.9, 1.0, 0.9, 0.8, 1.1, 1.3, 1.2, 1.0, 0.8, 0.6, 0.4, 0.3,
    ),
    fit={
        "marinero": 1.05, "historico": 1.15, "centro": 1.2, "obrero": 0.95,
        "playa": 1.15, "lujo": 1.0, "rural": 0.8,
    },
)  # fmt: skip

TIENDA = Sector(
    key="tienda",
    name="Tienda de barrio",
    emoji="🛒",
    game="caja",
    blurb="El ultramarinos de la esquina: se cobra, se da el cambio y se vigila el bolsillo.",
    m2=60,
    kind="local",
    products=(
        Product("pan", "Pan", 90, 150, igic="cero", pack=30, spoil=1.0, weight=1.5),
        Product("leche", "Leche", 120, 190, igic="cero", pack=24, spoil=0.1, weight=1.3),
        Product("fruta", "Fruta", 260, 420, pack=20, spoil=0.3, weight=1.0),
        Product("conservas", "Conservas", 320, 520, pack=24, weight=0.9),
        Product("limpieza", "Limpieza", 500, 800, pack=12, weight=0.6),
        Product("bebidas", "Bebidas", 430, 700, pack=24, weight=1.0),
    ),
    clients=7,
    patience=30,
    setup=100_000,
    license=10_000,
    utilities=1_000,
    order=(3, 6),
    # Media mañana y la vuelta del trabajo.
    curve=(
        0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2, 0.4, 0.7, 0.9, 1.2, 1.3,
        1.2, 1.0, 0.7, 0.7, 0.9, 1.1, 1.3, 1.4, 1.2, 0.8, 0.4, 0.2,
    ),
    fit={
        "marinero": 1.05, "historico": 0.9, "centro": 0.95, "obrero": 1.2,
        "playa": 1.0, "lujo": 0.85, "rural": 1.15,
    },
)  # fmt: skip

TALLER = Sector(
    key="taller",
    name="Taller",
    emoji="🔧",
    game="diagnostico",
    blurb="Coches que hacen ruidos raros. Se diagnostica, se presupuesta y que no vuelvan.",
    m2=90,
    kind="nave",
    products=(
        Product("pastillas", "Pastillas de freno", 900, 2_400, pack=4, weight=1.3),
        Product("bateria", "Batería", 1_400, 3_200, pack=2, weight=1.0),
        Product("aceite", "Aceite y filtro", 500, 1_600, pack=6, weight=1.5),
        Product("correa", "Correa", 1_800, 4_600, pack=2, weight=0.6),
        Product("bujias", "Bujías", 400, 1_400, pack=6, weight=0.9),
        Product("neumatico", "Neumático", 1_100, 2_600, pack=4, weight=1.1),
    ),
    clients=4,
    patience=45,
    setup=150_000,
    license=12_000,
    utilities=900,
    order=(1, 1),
    # Horario de taller: mañana fuerte, tarde floja.
    curve=(
        0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.5, 1.1, 1.3, 1.3, 1.2,
        1.1, 0.9, 0.6, 0.9, 1.1, 1.0, 0.8, 0.5, 0.3, 0.15, 0.15, 0.1,
    ),
    fit={
        "marinero": 0.95, "historico": 0.8, "centro": 0.9, "obrero": 1.25,
        "playa": 0.85, "lujo": 0.95, "rural": 1.15,
    },
)  # fmt: skip

SECTORS: tuple[Sector, ...] = (CAFETERIA, TIENDA, TALLER)
SECTOR_BY_KEY: dict[str, Sector] = {s.key: s for s in SECTORS}


@dataclass(frozen=True, slots=True)
class PriceLevel:
    """Un nivel de precios de todo el negocio.

    Attributes:
        key: Clave estable.
        name: Cómo se enseña.
        ticket: Lo que multiplica el precio de cada cosa.
        clients: Lo que multiplica los clientes de un turno.
        luxury_clients: Lo mismo en un barrio de lujo (allí lo caro espanta menos).
        reputation: Lo que suma a la reputación cada cliente bien atendido.
    """

    key: str
    name: str
    ticket: float
    clients: float
    luxury_clients: float
    reputation: float


PRICE_LEVELS: tuple[PriceLevel, ...] = (
    PriceLevel("barato", "Barato (−15 %)", 0.85, 1.15, 1.05, 0.1),
    PriceLevel("normal", "Normal", 1.0, 1.0, 1.0, 0.0),
    PriceLevel("caro", "Caro (+15 %)", 1.15, 0.85, 0.95, 0.0),
    PriceLevel("carisimo", "Carísimo (+30 %)", 1.3, 0.68, 0.85, -0.1),
)
PRICE_BY_KEY: dict[str, PriceLevel] = {p.key: p for p in PRICE_LEVELS}

#: Paletas del rótulo y el toldo: `(nombre, color principal, color de acento)`. La escena
#: recibe los colores; el índice es lo que se guarda.
PALETTES: tuple[tuple[str, str, str], ...] = (
    ("Atlántico", "#0f4c81", "#f6bd2d"),
    ("Turquesa", "#13867f", "#fbf3e0"),
    ("Buganvilla", "#a81f5c", "#fbf3e0"),
    ("Atardecer", "#d4600c", "#1b2a35"),
    ("Laurisilva", "#1f5a37", "#f0dfb2"),
    ("Picón", "#2a242d", "#f58220"),
    ("Gofio", "#cfb676", "#1b2a35"),
    ("Mojo", "#b3261e", "#f6bd2d"),
)

#: Emblemas del rótulo (los dibuja la escena con trazos, por su clave).
EMBLEMS: tuple[tuple[str, str], ...] = (
    ("taza", "Taza"),
    ("estrella", "Estrella"),
    ("ancla", "Ancla"),
    ("sol", "Sol"),
    ("pez", "Pez"),
    ("drago", "Drago"),
    ("corona", "Corona"),
    ("rayo", "Rayo"),
)

#: Nombres de los clientes habituales. Cada negocio saca cinco por su semilla.
REGULAR_NAMES: tuple[str, ...] = (
    "Doña Carmen",
    "Don Agustín",
    "Yeray",
    "Nayra",
    "Pino",
    "Candelaria",
    "Don Fefo",
    "Chano",
    "Mari Luz",
    "Ayoze",
    "Tinerfe",
    "Doña Lola",
    "Iballa",
    "Don Paco",
    "Guaci",
    "Cristo",
    "Nauzet",
    "Doña Fela",
    "Jonay",
    "Dácil",
)
#: Habituales por negocio.
REGULARS_PER_BUSINESS = 5

#: Avería de cada recambio del taller: el síntoma que cuenta el cliente y la pista que se
#: dibuja en el coche (`cue`, la lee la escena).
FAULTS: dict[str, tuple[tuple[str, ...], str]] = {
    "pastillas": (
        ("Chirría al frenar y cada vez frena menos.", "Hace un ruido de metal cuando freno."),
        "frenos",
    ),
    "bateria": (
        (
            "Por las mañanas no arranca: hace clic y nada.",
            "Se me quedó tirado con las luces puestas.",
        ),
        "bateria",
    ),
    "aceite": (
        ("Se me encendió la lucecita roja de la aceitera.", "Echa humo azulado por el tubo."),
        "aceite",
    ),
    "correa": (
        ("Al arrancar suena un chirrido largo bajo el capó.", "Tiene un silbido raro en el motor."),
        "correa",
    ),
    "bujias": (
        ("Da tirones al acelerar y gasta más que nunca.", "En frío falla y tiembla todo."),
        "bujias",
    ),
    "neumatico": (
        ("Vibra el volante y tira hacia un lado.", "Se me baja la rueda cada dos días."),
        "rueda",
    ),
}

#: Lo que dice un cliente al llegar (`{name}` es su nombre si es habitual). Al menos la
#: mitad son frases normales, como pide la Biblia.
GREETINGS: dict[str, tuple[str, ...]] = {
    "comandas": (
        "Buenas, ¿nos pone esto?",
        "Hola, cuando pueda.",
        "Buenos días. Para la mesa del fondo.",
        "¿Me cobra luego? Primero esto.",
        "Venimos del Teide y traemos un hambre…",
        "Rapidito, que se me va la guagua.",
        "Lo de siempre no, que hoy estoy de dieta. Bueno, sí.",
    ),
    "caja": (
        "Hola, ¿me cobra?",
        "Buenas tardes.",
        "Esto es todo, gracias.",
        "¿Tiene cambio? Solo llevo este billete.",
        "Me llevo esto y me voy, que tengo el coche mal aparcado.",
        "¿A cuánto está el gofio hoy? Da igual, cóbrame.",
    ),
    "diagnostico": (
        "Buenas, el coche me hace una cosa rara.",
        "Hola, ¿me lo puede mirar hoy?",
        "Me lo dejó así mi cuñado, que entiende de motores.",
        "Necesito el coche para el lunes, ¿puede ser?",
        "No sé lo que es, pero suena a caro.",
    ),
}
