"""Catálogo del trabajo (`pala`): oficios, puestos, contenido de minijuegos y eventos.

Solo datos. Añadir un oficio es añadir un `Job` a `JOBS` con sus puestos (hoy
cinco; el código admite los que haya), su contenido de minijuego (si usa uno
nuevo) y, si se quiere, sus eventos. Los `key` (oficios, formaciones, tareas, eventos) no se cambian
nunca: son lo que se guarda en la base de datos.

Sueldos, en Y$ de nómina (100 Y$ por euro, ver
`taxes.WAGE_YAPDOLLARS_PER_EURO`): el puesto 1 de cada oficio paga entre 1.600 y
1.800 Y$ por turno con nota media, un poco más que un IMV entero (1.500 Y$), y
el puesto 5 entre 13.000 y 25.000. Un turno son unas dos horas: la jornada
completa (4 turnos) de un celador equivale a unos 25.000 € al año y la de un
consejero de eléctrica, a 365.000 €, con la retención que toca a cada uno. Las
formaciones cuestan de 2 a 4 turnos del puesto al que dan acceso. El análisis
que fija estas cifras está en `docs/economia-trabajo.md`.

La política es una sátira del PSOE, el partido del Gobierno, y de sus
tópicos (sobres, enchufes, comisiones, puertas giratorias). El reparto se ríe
de las apps de reparto y de los falsos autónomos; el funcionariado, de la
burocracia (con las preguntas del temario de verdad); el influencer, de la
fama a golpes. No se nombra a ninguna persona ni marca real: los personajes
son genéricos.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.work import FLAT_RATE_FEE, Job, Mechanic, Position, Task, Training

# -- Tareas comunes --------------------------------------------------------------------
#
# Contadores de `Contract.progress` (se ponen a cero al cambiar de puesto):
# `good` turnos de 90 o más, `extras` turnos extra, `events` eventos
# resueltos, y los del minijuego: `clean` (turnos de cavar sin romper nada),
# `caught` (aciertos de detectar), `perfect_rounds` (rondas de memoria sin
# fallo), `smooth` (respuestas buenas en un diálogo), `sorted` (aciertos de
# clasificar), `best` (aciertos de calcular) y `temario` (preguntas del
# temario acertadas estudiando la oposición).


def _good(goal: int) -> Task:
    return Task("good", f"Haz {goal} turnos de 90 o más", "good", goal)


def _extras(goal: int) -> Task:
    return Task("extras", f"Echa {goal} turno{'s' if goal > 1 else ''} extra", "extras", goal)


def _guards(goal: int) -> Task:
    return Task("guards", f"Haz {goal} guardias", "guards", goal)


def _study(goal: int) -> Task:
    return Task("temario", f"Acierta {goal} preguntas del temario", "temario", goal)


def _events(goal: int) -> Task:
    return Task("events", f"Sal de {goal} marrones (eventos)", "events", goal)


# -- Oficios ---------------------------------------------------------------------------

OBRA = Job(
    key="obra",
    name="Obra",
    emoji="🦺",
    blurb="El oficio de la pala. Estable, duro y con jubilados mirando.",
    rgb=(240, 160, 30),
    positions=(
        Position(
            1,
            "Peón",
            1_800,
            Mechanic.DIG,
            30,
            (_good(3), Task("clean", "Haz 2 turnos sin romper nada", "clean", 2)),
            days=2,
            content="cavar:3",
        ),
        Position(
            2,
            "Oficial de segunda",
            2_800,
            Mechanic.DIG,
            30,
            (_good(4), _extras(1)),
            days=3,
            content="cavar:3",
            training=Training("prl20", "el curso de PRL de 20 horas", 6_000),
        ),
        Position(
            3,
            "Oficial de primera",
            4_500,
            Mechanic.DIG,
            40,
            (_good(5), Task("clean", "Haz 4 turnos sin romper nada", "clean", 4)),
            days=4,
            content="cavar:4",
        ),
        Position(
            4,
            "Encargado",
            7_500,
            Mechanic.SPOT,
            60,
            (Task("caught", "Pilla a 25 escaqueados", "caught", 25), _good(5), _events(2)),
            days=5,
            content="vagos",
            training=Training("recurso", "el curso de recurso preventivo de 60 horas", 15_000),
        ),
        Position(
            5,
            "Constructor",
            13_000,
            Mechanic.SPOT,
            60,
            (),
            days=0,
            content="sobrecostes",
            self_employed=True,
            training=Training("rea", "el alta en el Registro de Empresas Acreditadas", 50_000),
        ),
    ),
)

HOSTELERIA = Job(
    key="hosteleria",
    name="Hostelería",
    emoji="🍽️",
    blurb="Precaria abajo, chiringuito propio arriba. Propinas en negro.",
    rgb=(46, 204, 113),
    positions=(
        Position(
            1,
            "Friegaplatos",
            1_700,
            Mechanic.MEMORY,
            30,
            (_good(3), Task("perfect", "Saca 6 tandas perfectas", "perfect_rounds", 6)),
            days=2,
            content="platos:3",
        ),
        Position(
            2,
            "Camarero",
            2_500,
            Mechanic.MEMORY,
            30,
            (_good(4), _extras(1)),
            days=3,
            content="comandas:3",
            training=Training("manipulador", "el carnet de manipulador de alimentos", 4_000),
        ),
        Position(
            3,
            "Jefe de rango",
            4_000,
            Mechanic.MEMORY,
            40,
            (Task("perfect", "Saca 15 comandas perfectas", "perfect_rounds", 15), _good(5)),
            days=4,
            content="comandas:4",
        ),
        Position(
            4,
            "Jefe de cocina",
            7_000,
            Mechanic.MEMORY,
            60,
            (
                Task("perfect", "Saca 20 comandas perfectas", "perfect_rounds", 20),
                _good(6),
                _events(2),
            ),
            days=5,
            content="cocina:5",
            training=Training("alergenos", "el curso de alérgenos y APPCC", 12_000),
        ),
        Position(
            5,
            "Dueño del chiringuito",
            13_000,
            Mechanic.DIALOGUE,
            60,
            (),
            days=0,
            content="chiringuito",
            self_employed=True,
            training=Training("apertura", "la licencia de apertura del chiringuito", 60_000),
        ),
    ),
)

POLITICA = Job(
    key="politica",
    name="Política (PSOE)",
    emoji="🌹",
    blurb="Del pegacarteles al consejo de una eléctrica. Sobres incluidos.",
    rgb=(225, 6, 19),
    positions=(
        Position(
            1,
            "Pegacarteles",
            1_700,
            Mechanic.MEMORY,
            30,
            (_good(3), Task("perfect", "Haz 6 rutas perfectas", "perfect_rounds", 6)),
            days=2,
            content="carteles:3",
        ),
        Position(
            2,
            "Concejal",
            2_700,
            Mechanic.MEMORY,
            30,
            (
                Task("perfect", "Vota 10 veces lo que diga el partido", "perfect_rounds", 10),
                _good(4),
            ),
            days=3,
            content="votos:3",
            training=Training("listas", "un puesto de salida en las listas", 8_000),
        ),
        Position(
            3,
            "Diputado autonómico",
            5_000,
            Mechanic.DIALOGUE,
            40,
            (Task("smooth", "Esquiva 15 preguntas en rueda de prensa", "smooth", 15), _good(5)),
            days=4,
            content="prensa",
        ),
        Position(
            4,
            "Ministro",
            10_000,
            Mechanic.DIALOGUE,
            60,
            (
                Task("smooth", "Sal vivo de 20 preguntas en comisión", "smooth", 20),
                _good(5),
                _events(3),
            ),
            days=5,
            content="comision",
            training=Training("cartera", "la cartera ministerial (cena en Ferraz)", 30_000),
        ),
        Position(
            5,
            "Consejero de una eléctrica",
            25_000,
            Mechanic.DIALOGUE,
            60,
            (),
            days=0,
            content="consejo",
            training=Training("giratoria", "el paso por la puerta giratoria", 100_000),
        ),
    ),
)

SANIDAD = Job(
    key="sanidad",
    name="Sanidad",
    emoji="🏥",
    blurb="Aquí no se rinde trabajando lo normal: se rinde haciendo guardias.",
    rgb=(26, 188, 156),
    guards_from=2,
    ordinary_cap=5,
    guards_required=((4, 2),),
    positions=(
        Position(
            1, "Celador", 1_750, Mechanic.MEMORY, 30,
            (_good(3), Task("perfect", "Haz 6 traslados perfectos", "perfect_rounds", 6)),
            days=2, content="camilla:3",
        ),
        Position(
            2, "TCAE", 2_800, Mechanic.MEMORY, 30,
            (_good(3), _guards(2)),
            days=3, content="ronda:3",
            training=Training("tcae", "el título de TCAE (FP de grado medio)", 6_000),
        ),
        Position(
            3, "Enfermero", 4_800, Mechanic.SPOT, 40,
            (Task("caught", "Acierta 20 triajes", "caught", 20), _guards(4)),
            days=4, content="triaje",
            training=Training("enfermeria", "el grado en Enfermería", 20_000),
        ),
        Position(
            4, "Médico residente (MIR)", 8_000, Mechanic.DIALOGUE, 60,
            (Task("smooth", "Acierta 25 preguntas del MIR", "smooth", 25), _guards(6),
             _events(2)),
            days=5, content="mir",
            training=Training("mir", "la academia del MIR (y aprobar el examen)", 30_000),
        ),
        Position(
            5, "Médico adjunto", 14_000, Mechanic.DIALOGUE, 60, (),
            days=0, content="consulta",
            training=Training("ope", "la plaza de adjunto (oposición, OPE)", 60_000),
        ),
    ),
)  # fmt: skip

OFICINA = Job(
    key="oficina",
    name="Oficina (tecnología)",
    emoji="💻",
    blurb="Del café del becario a CTO con stock options. Teletrabajo y Hong Kong.",
    rgb=(52, 152, 219),
    remote=True,
    abroad_from=3,
    options_level=5,
    positions=(
        Position(
            1, "Becario", 1_700, Mechanic.MEMORY, 30,
            (_good(3), Task("perfect", "Acierta 6 rondas de cafés", "perfect_rounds", 6)),
            days=2, content="cafes:3",
        ),
        Position(
            2, "Programador junior", 2_800, Mechanic.SPOT, 30,
            (Task("caught", "Encuentra 15 bugs", "caught", 15), _good(4)),
            days=3, content="bugs",
            training=Training("bootcamp", "el bootcamp de programación", 10_000),
        ),
        Position(
            3, "Programador senior", 5_000, Mechanic.SPOT, 40,
            (Task("caught", "Para 20 cambios que rompían producción", "caught", 20), _good(5)),
            days=4, content="revisiones",
        ),
        Position(
            4, "Tech lead", 9_500, Mechanic.DIALOGUE, 60,
            (Task("smooth", "Acorta 20 reuniones", "smooth", 20), _good(5), _events(2)),
            days=5, content="reuniones",
            training=Training("scrum", "la certificación de Scrum Master", 12_000),
        ),
        Position(
            5, "CTO de startup", 18_000, Mechanic.DIALOGUE, 60, (),
            days=0, content="inversores",
            training=Training("mba", "el MBA de una escuela de negocios", 80_000),
        ),
    ),
)  # fmt: skip

REPARTO = Job(
    key="reparto",
    name="Reparto (rider)",
    emoji="🛵",
    blurb="Pedidos, estrellas en la app y lluvia que paga más. De la bici a fundar la app.",
    rgb=(0, 204, 188),
    weather=True,
    rating=True,
    positions=(
        Position(
            1, "Rider en bici", 1_650, Mechanic.CALC, 30,
            (_good(3), Task("best", "Elige bien 15 pedidos", "best", 15)),
            days=2, content="pedidos:3", self_employed=True, fee=FLAT_RATE_FEE,
        ),
        Position(
            2, "Rider en moto", 2_700, Mechanic.CALC, 30,
            (Task("best", "Elige bien 25 pedidos", "best", 25), _good(4)),
            days=3, content="pedidos:4", self_employed=True, fee=FLAT_RATE_FEE,
            training=Training("carnet_a2", "el carnet de moto (A2) y una moto de segunda mano",
                              8_000),
        ),
        Position(
            3, "Rider con contrato", 4_500, Mechanic.MEMORY, 40,
            (Task("perfect", "Haz 15 rutas perfectas", "perfect_rounds", 15), _good(5)),
            days=4, content="entregas:4",
            training=Training("ley_rider", "el abogado laboralista que te gana la laboralidad "
                              "(Ley 12/2021, la «Ley Rider»)", 15_000),
        ),
        Position(
            4, "Jefe de flota", 7_500, Mechanic.SPOT, 60,
            (Task("caught", "Pilla a 25 riders comiéndose el pedido", "caught", 25), _good(5),
             _events(2)),
            days=5, content="flota",
            training=Training("flotas", "el curso de gestión de flotas", 20_000),
        ),
        Position(
            5, "Fundador de app de reparto", 14_000, Mechanic.DIALOGUE, 60, (),
            days=0, content="fundador", self_employed=True,
            training=Training("semilla", "la ronda semilla (familia, amigos y algún incauto)",
                              60_000),
        ),
    ),
)  # fmt: skip

FUNCIONARIADO = Job(
    key="funcionariado",
    name="Funcionariado",
    emoji="🗂️",
    blurb="Oposita, saca la plaza y que no te mueva nadie. Trienios y desayuno sagrado.",
    rgb=(149, 165, 166),
    study="temario",
    tenure_from=2,
    seniority=True,
    positions=(
        Position(
            1, "Interino", 1_700, Mechanic.SORT, 30,
            (_good(3), _study(20)),
            days=2, content="ventanilla",
        ),
        Position(
            2, "Auxiliar administrativo (C2)", 2_700, Mechanic.SORT, 30,
            (_study(40), Task("sorted", "Tramita 40 papeles al sitio correcto", "sorted", 40)),
            days=3, content="registro",
            training=Training("ope_c2", "las tasas y la academia de la oposición de auxiliar "
                              "(C2)", 6_000),
        ),
        Position(
            3, "Administrativo (C1)", 4_400, Mechanic.MEMORY, 40,
            (_study(60), Task("perfect", "Haz 15 circuitos de firmas perfectos",
                              "perfect_rounds", 15), _good(4)),
            days=4, content="sellos:4",
            training=Training("ope_c1", "la promoción interna a administrativo (C1)", 15_000),
        ),
        Position(
            4, "Técnico de gestión (A2)", 7_500, Mechanic.DIALOGUE, 60,
            (_study(80), Task("smooth", "Atiende bien a 25 ciudadanos", "smooth", 25),
             _events(2)),
            days=5, content="ciudadano",
            training=Training("ope_a2", "la oposición de técnico de gestión (A2)", 30_000),
        ),
        Position(
            5, "Inspector de Hacienda (A1)", 14_000, Mechanic.SPOT, 60, (),
            days=0, content="inspeccion",
            training=Training("ope_a1", "la oposición a Inspector de Hacienda (cinco años de "
                              "academia)", 60_000),
        ),
    ),
)  # fmt: skip

INFLUENCER = Job(
    key="influencer",
    name="Influencer",
    emoji="🤳",
    blurb="Unos días no te ve ni tu madre y otros te haces viral. Funas incluidas.",
    rgb=(233, 30, 99),
    viral=True,
    positions=(
        Position(
            1, "Tiktoker de barrio", 1_700, Mechanic.MEMORY, 30,
            (_good(3), Task("perfect", "Clava 6 coreografías", "perfect_rounds", 6)),
            days=2, content="baile:3",
        ),
        Position(
            2, "Microinfluencer", 2_800, Mechanic.CALC, 30,
            (Task("best", "Acierta 20 métricas", "best", 20), _good(4)),
            days=3, content="metricas",
            training=Training("aro_luz", "el aro de luz y el curso de marca personal", 6_000),
        ),
        Position(
            3, "Streamer", 4_800, Mechanic.DIALOGUE, 40,
            (Task("smooth", "Sobrevive a 25 momentos del directo", "smooth", 25), _good(5)),
            days=4, content="directo", self_employed=True,
            training=Training("setup", "el setup de streamer (silla gamer incluida)", 15_000),
        ),
        Position(
            4, "Influencer con agencia", 9_000, Mechanic.SPOT, 60,
            (Task("caught", "Rechaza 20 colaboraciones turbias", "caught", 20), _good(5),
             _events(2)),
            days=5, content="colaboraciones", self_employed=True,
            training=Training("agencia", "el contrato con una agencia de representación (se "
                              "queda el 20 %)", 25_000),
        ),
        Position(
            5, "Celebrity de reality", 16_000, Mechanic.DIALOGUE, 60, (),
            days=0, content="plato", self_employed=True,
            training=Training("reality", "el casting de un reality en una isla desierta",
                              70_000),
        ),
    ),
)  # fmt: skip

JOBS: tuple[Job, ...] = (
    OBRA,
    HOSTELERIA,
    POLITICA,
    SANIDAD,
    OFICINA,
    REPARTO,
    FUNCIONARIADO,
    INFLUENCER,
)
JOB_BY_KEY: dict[str, Job] = {job.key: job for job in JOBS}

# -- Contenido: cavar --------------------------------------------------------------------
#
# Cada turno trae su plano: unas marcas del suelo son seguras, otras esconden
# algo que romper y otras son roca (no rompes nada, pero no avanzas). El plano
# cambia en cada turno, así que hay que leerlo cada vez.

DIG_MARKS: tuple[str, ...] = ("🟫", "🌱", "💧", "⚡", "🪨", "🟨", "🧱", "🍂")
DIG_DANGERS: tuple[str, ...] = (
    "la tubería del agua",
    "el cable de la luz",
    "la fibra de todo el barrio",
    "la tubería del gas",
    "el alcantarillado",
)
DIG_HINTS: tuple[str, ...] = (
    "👴 Un jubilado señala {mark}: «Ahí, chacho, ahí, que yo lo sé».",
    "👷 El encargado grita desde la caseta: «¡Dale en {mark}!».",
    "🐕 Un perro está escarbando justo en {mark}.",
    "🧓 Otro jubilado discrepa: «Yo cavaría en {mark}».",
)

# -- Contenido: detectar -----------------------------------------------------------------

WORKER_NAMES: tuple[str, ...] = (
    "Yeray",
    "Acoidán",
    "Cristo",
    "Juanma",
    "Airam",
    "Kevin",
    "Tino",
    "Echedey",
    "Dailos",
    "Nauzet",
    "Ayoze",
    "Pepe",
    "Jonathan",
    "Óliver",
    "Guayarmina",
    "Iballa",
)
WORKING: tuple[str, ...] = (
    "⛏️ cavando la zanja del fondo",
    "🧱 subiendo bloques al segundo",
    "📏 midiendo la pared con la cinta",
    "🚚 descargando el camión de arena",
    "🪣 haciendo la mezcla",
    "🔨 montando el encofrado",
    "💧 bebiendo agua (hay 34 °C)",
    "🦺 ajustándose el arnés antes de subir",
    "🪚 cortando tablones",
    "🧹 recogiendo escombro",
    "🚜 moviendo la retro",
    "📋 firmando el albarán del cemento",
    "🔩 apretando la ferralla",
    "🧰 cambiando el disco de la radial",
)
SLACKING: tuple[str, ...] = (
    "📱 lleva 40 minutos «buscando una llave del 13»",
    "🥖 en el bocadillo de las 10 (son las 12:30)",
    "🚬 cuarto cigarro «para pensar la estructura»",
    "📞 hablando con su primo de un coche que vende",
    "😴 «vigilando» la hormigonera con los ojos cerrados",
    "👀 mirando cómo trabaja el de al lado",
    "🪑 sentado en un palé «por la espalda»",
    "🔩 ha ido a la ferretería a por tornillos… hace dos horas",
    "📺 viendo el partido en el móvil detrás de la caseta",
    "🍺 ha bajado al bar «a por hielo»",
    "🪞 peinándose en el retrovisor de la furgoneta",
)
#: Partidas de un presupuesto de obra pública: (concepto, precio normal en Y$).
BUDGET_ITEMS: tuple[tuple[str, int], ...] = (
    ("Saco de cemento", 70),
    ("Palé de bloques", 900),
    ("Hora de retroexcavadora", 450),
    ("Ferralla (100 kg)", 1_100),
    ("Ventana de aluminio", 2_400),
    ("Puerta de entrada", 3_500),
    ("Camión de arena", 1_600),
    ("Andamio (una semana)", 800),
    ("Grifo del baño", 400),
    ("Cuba de escombros", 1_500),
    ("Azulejo (m²)", 250),
    ("Farola", 3_000),
    ("Banco del parque", 1_200),
    ("Placa conmemorativa", 600),
)

# -- Contenido: memoria ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemoryPack:
    """Contenido de un minijuego de memoria.

    Attributes:
        intro: Qué tienes que memorizar (se enseña con la secuencia).
        ask: Qué hacer después (con la secuencia oculta).
        items: `(emoji, nombre)` posibles.
    """

    intro: str
    ask: str
    items: tuple[tuple[str, str], ...]


DISHES: tuple[tuple[str, str], ...] = (
    ("🍺", "Caña"),
    ("🍷", "Vino"),
    ("☕", "Barraquito"),
    ("🥔", "Papas arrugadas"),
    ("🐟", "Vieja sancochada"),
    ("🧀", "Queso asado"),
    ("🥗", "Ensalada"),
    ("🍤", "Gambas"),
    ("🦑", "Calamares"),
    ("🍮", "Quesillo"),
)

MEMORY_PACKS: dict[str, MemoryPack] = {
    "platos": MemoryPack(
        "🧽 Llega una montaña de platos. Lávalos en este orden o el jefe se enfada:",
        "Lávalos en el mismo orden.",
        (
            ("🍽️", "Plato llano"),
            ("🥣", "Bol"),
            ("🍴", "Cubiertos"),
            ("🥛", "Vaso"),
            ("🍳", "Sartén"),
            ("🫖", "Tetera"),
            ("🍲", "Olla"),
        ),
    ),
    "comandas": MemoryPack("🧾 Mesa {n} pide:", "Sírvelo en el mismo orden.", DISHES),
    "cocina": MemoryPack(
        "👨‍🍳 Comanda para la mesa {n}. Emplata en este orden:", "¡Emplata!", DISHES
    ),
    "carteles": MemoryPack(
        "🗺️ Noche de pegada de carteles. El coordinador de campaña te dicta la ruta "
        "(y que no te pille la policía local):",
        "Pega el cartel del candidato en cada sitio, en el mismo orden de la ruta.",
        (
            ("💡", "Farola"),
            ("🚏", "Marquesina de la guagua"),
            ("🗳️", "Colegio electoral"),
            ("🏛️", "Ayuntamiento"),
            ("🌹", "Casa del Pueblo"),
            ("🧱", "Muro del solar"),
            ("🏪", "Persiana del bazar"),
            ("📮", "Buzón de Correos"),
        ),
    ),
    "votos": MemoryPack(
        "🗳️ Pleno. El portavoz te hace señas desde su escaño. Vota en este orden:",
        "Vota lo que te ha dicho. Disciplina de partido.",
        (
            ("👍", "Sí"),
            ("👎", "No"),
            ("✋", "Abstención"),
            ("🚪", "Salir del pleno"),
            ("📱", "Voto telemático"),
        ),
    ),
}

# -- Contenido: diálogos -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dialogue:
    """Una situación con una respuesta buena y dos o tres malas (se barajan al jugar)."""

    situation: str
    good: str
    bad: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DialoguePack:
    """Contenido de un minijuego de diálogo.

    Attributes:
        rule: La regla de oro que se enseña al empezar.
        items: Situaciones.
    """

    rule: str
    items: tuple[Dialogue, ...]


DIALOGUE_PACKS: dict[str, DialoguePack] = {
    "prensa": DialoguePack(
        "🎤 Rueda de prensa. Regla de oro: **no contestes nunca a lo que te preguntan.**",
        (
            Dialogue(
                "¿Conocía las adjudicaciones a la empresa de su cuñado?",
                "Le voy a hablar de lo que importa a la gente: el paro está en mínimos.",
                ("Sí, pero mi cuñado es un gran profesional.", "Me enteré por el BOE, como todos."),
            ),
            Dialogue(
                "¿Va a dimitir?",
                "Lo que este país necesita es estabilidad, y eso es lo que damos.",
                ("Sí, mañana a primera hora.", "Si sale algo más en la prensa, sí."),
            ),
            Dialogue(
                "¿Por qué se reunió de madrugada con un empresario investigado?",
                "Fue un encuentro casual, de esos que pasan en las gasolineras.",
                ("Para hablar del contrato del puerto.", "Porque me invitó a cenar."),
            ),
            Dialogue(
                "¿Cuánto ha costado el viaje oficial en el avión del Gobierno?",
                "Lo relevante es la agenda internacional que hemos cerrado.",
                ("Unos 40.000 euros.", "Fui al concierto, pero era una agenda de trabajo."),
            ),
            Dialogue(
                "¿Va a pactar con quien prometió no pactar nunca?",
                "Hemos cambiado de opinión porque ha cambiado la realidad.",
                ("Sí, porque necesitamos los votos.", "No, nunca. Bueno, ya veremos."),
            ),
            Dialogue(
                "¿Ha leído usted la sentencia?",
                "Respeto profundamente a la justicia y no voy a valorar resoluciones.",
                ("No, ni pienso.", "Sí, y los jueces se han equivocado."),
            ),
            Dialogue(
                "¿Por qué ha subido un 30 % el presupuesto de la obra?",
                "Estamos ante la mayor inversión de la historia de esta isla.",
                ("Hubo comisiones.", "No sé sumar, soy de letras."),
            ),
            Dialogue(
                "¿Qué opina del informe de la UCO?",
                "Es un bulo de la fachosfera y de los medios de siempre.",
                ("Que me pilla de lleno.", "Que es muy completo y muy bien redactado."),
            ),
            Dialogue(
                "¿Su asesor cobraba sin ir a trabajar?",
                "Es un servidor público con una trayectoria impecable.",
                ("Venía los jueves.", "Iba al despacho a cargar el móvil."),
            ),
            Dialogue(
                "¿Cuántos asesores tiene su consejería?",
                "Los necesarios para servir a la ciudadanía.",
                ("Ciento doce.", "No lo sé, nunca los he visto a todos juntos."),
            ),
            Dialogue(
                "¿Por qué no se han presentado los presupuestos?",
                "Seguimos gobernando con unos presupuestos que funcionan.",
                ("Porque no tenemos apoyos.", "Se nos olvidó."),
            ),
            Dialogue(
                "¿Cobró usted dietas por un viaje en el que no estuvo?",
                "Toda mi actividad está publicada en el portal de transparencia.",
                ("Sí, pero poquito.", "Estuve en espíritu."),
            ),
        ),
    ),
    "comision": DialoguePack(
        "🏛️ Comisión de investigación. Regla de oro: **no te consta, no lo recuerdas y "
        "la culpa es del Gobierno anterior.**",
        (
            Dialogue(
                "¿Firmó usted el contrato de las mascarillas?",
                "No me consta. Eso se gestionaba a otro nivel.",
                ("Sí, aquí está mi firma.", "Lo firmé sin leer, como todo."),
            ),
            Dialogue(
                "¿Cómo explica los 400.000 euros en efectivo?",
                "No lo recuerdo, señoría. Han pasado muchos meses.",
                ("Ahorros del Bizum.", "Me tocó la lotería tres veces."),
            ),
            Dialogue(
                "¿Por qué el déficit se ha disparado bajo su mandato?",
                "Heredamos una situación desastrosa del Gobierno anterior.",
                ("Gastamos más de lo que teníamos.", "No sé qué es el déficit."),
            ),
            Dialogue(
                "¿Conoce a este señor de la foto?",
                "Me hago muchas fotos al día, señoría. No puedo conocer a todo el mundo.",
                ("Sí, es mi socio.", "Es mi chófer, mi testaferro y mi amigo."),
            ),
            Dialogue(
                "¿Dónde estaba la noche del 14?",
                "En un acto del partido, rodeado de compañeros que lo pueden confirmar.",
                ("En un bar con el empresario.", "En Andorra, de compras."),
            ),
            Dialogue(
                "¿Ha borrado los mensajes de su móvil?",
                "Mi teléfono se actualizó solo y se perdió todo, una pena.",
                ("Sí, todos, esa misma noche.", "Lo tiré al mar en Puerto Rico."),
            ),
            Dialogue(
                "¿Quién decidió adjudicar sin concurso?",
                "Fue una decisión técnica, colegiada y ajustada a derecho.",
                ("Yo, por teléfono.", "Lo echamos a piedra, papel o tijera."),
            ),
            Dialogue(
                "¿Por qué su exmujer trabaja en una empresa pública?",
                "Accedió por sus méritos, como cualquier otra ciudadana.",
                ("Para que no me pidiera pensión.", "Porque se lo prometí en el divorcio."),
            ),
            Dialogue(
                "¿Va a entregar los documentos que se le pidieron?",
                "Colaboraremos con esta comisión en todo lo que permita la ley.",
                ("No, se mojaron.", "Sí, los que no he quemado."),
            ),
        ),
    ),
    "consejo": DialoguePack(
        "💼 Consejo de administración de la eléctrica. Regla de oro: **asiente, no "
        "preguntes y cobra.**",
        (
            Dialogue(
                "El presidente propone subir la tarifa un 15 % en invierno.",
                "Me parece una decisión valiente y necesaria.",
                ("¿Y la gente que no pueda pagar?", "Propongo bajarla, que es Navidad."),
            ),
            Dialogue(
                "Hay que pedirle al Gobierno una ayuda de 200 millones.",
                "Yo me encargo: tengo el móvil de medio Consejo de Ministros.",
                ("¿No dimos beneficios récord?", "Mejor no, que queda feo."),
            ),
            Dialogue(
                "Toca votar los bonus de la dirección.",
                "A favor. Y propongo que el mío lleve decimales.",
                ("En contra, eso es mucho.", "Me abstengo, que tengo conciencia."),
            ),
            Dialogue(
                "¿Alguien tiene alguna pregunta sobre las cuentas?",
                "Ninguna. Están clarísimas.",
                ("Yo: ¿por qué hay una filial en las Caimán?", "¿Qué es un balance?"),
            ),
            Dialogue(
                "Un periodista pregunta por qué cobra usted 300.000 euros por ir una vez al mes.",
                "Por mi dilatada experiencia en el sector, adquirida en el ministerio.",
                ("Porque fui ministro, básicamente.", "Por no hacer preguntas."),
            ),
            Dialogue(
                "Se propone cortar la luz a los morosos en ola de frío.",
                "Hay que ser responsables con los accionistas.",
                ("Me parece una salvajada.", "¿Y si les mandamos una manta?"),
            ),
            Dialogue(
                "Hay que elegir dónde se celebra la junta: en Madrid o en Maldivas.",
                "En Maldivas, por la sostenibilidad del equipo.",
                ("En Madrid, que es más barato.", "Por Zoom, que ahorramos."),
            ),
            Dialogue(
                "El presidente se duerme a mitad de su propio discurso.",
                "Aplaudes fuerte para que se despierte con dignidad.",
                ("Le haces una foto para el grupo.", "Te vas a casa, que ya has cobrado."),
            ),
        ),
    ),
    "chiringuito": DialoguePack(
        "🏖️ Tu chiringuito. Regla de oro: **el cliente siempre tiene razón… hasta que paga.**",
        (
            Dialogue(
                "Un guiri pide paella a las seis de la tarde.",
                "«Of course, my friend!». Y le cobras la paella a precio de langosta.",
                ("«Aquí no se come paella a esa hora».", "Le das la del mediodía recalentada."),
            ),
            Dialogue(
                "Llega la inspectora de Sanidad sin avisar.",
                "La invitas a un barraquito y le enseñas el registro de temperaturas al día.",
                ("Escondes los calamares en el baño.", "Le dices que vuelva el lunes."),
            ),
            Dialogue(
                "Una mesa de doce se quiere ir sin pagar.",
                "Te pones en la puerta con el datáfono y una sonrisa.",
                ("Les tiras un plato.", "Llamas a tu primo el de la moto."),
            ),
            Dialogue(
                "Un cliente pide la hoja de reclamaciones.",
                "Se la das con un chupito de ron miel «para que escriba más tranquilo».",
                ("Le dices que se ha acabado.", "Te la comes delante de él."),
            ),
            Dialogue(
                "El camarero nuevo quiere cobrar las horas extra.",
                "Se las pagas y lo apuntas todo: Inspección de Trabajo ronda la playa.",
                ("«Aquí somos una familia».", "Le pagas en croquetas."),
            ),
            Dialogue(
                "Un influencer pide comer gratis a cambio de una historia en Instagram.",
                "Le ofreces un 10 % si te etiqueta. La caja no se paga con likes.",
                ("Le das el menú entero gratis.", "Lo echas a gritos."),
            ),
            Dialogue(
                "Se acaba el queso asado un sábado a las dos.",
                "Recomiendas el especial del día con mucho arte.",
                ("Dices que es tendencia que no haya.", "Asas queso de sándwich."),
            ),
            Dialogue(
                "Viene un concejal a pedirte «una ayuda» para la licencia de la terraza.",
                "Le dices que lo tramitas todo por la sede electrónica, gracias.",
                ("Le das un sobre.", "Le das un sobre más grande."),
            ),
        ),
    ),
}

# -- Contenido de sanidad y oficina ------------------------------------------------------

MEMORY_PACKS.update(
    {
        "camilla": MemoryPack(
            "🛏️ Te dictan el traslado del paciente. Ruta:",
            "Lleva la camilla por la misma ruta, en orden.",
            (("🚑", "Urgencias"), ("🩻", "Rayos"), ("🧪", "Laboratorio"), ("🛏️", "Planta 3"),
             ("🔪", "Quirófano"), ("🛗", "Ascensor de servicio"), ("☕", "Cafetería")),
        ),
        "ronda": MemoryPack(
            "📋 Ronda de la planta. La supervisora te dice el orden:",
            "Haz la ronda en orden.",
            (("🍪", "Merienda"), ("🌡️", "Temperatura"), ("💊", "Pastillero"),
             ("🛏️", "Cambio de sábanas"), ("🚽", "Cuña"), ("🩸", "Glucemia"),
             ("📺", "Mando de la tele")),
        ),
        "cafes": MemoryPack(
            "☕ El equipo te pide los cafés. Apunta (de memoria, claro):",
            "Reparte los cafés en el mismo orden.",
            (("☕", "Solo"), ("🥛", "Con leche"), ("🍵", "Té verde"), ("🧋", "Té con perlas"),
             ("🥃", "Barraquito"), ("🧊", "Con hielo"),
             ("🌱", "Avena templada, sin lactosa, descafeinado")),
        ),
    }
)  # fmt: skip

#: Triaje: pacientes que tienen que pasar YA y pacientes que pueden esperar (mucho).
TRIAGE_URGENT: tuple[str, ...] = (
    "Dolor en el pecho que se le va al brazo izquierdo; está sudando",
    "No puede respirar y tiene los labios morados",
    "Se ha caído de un andamio y no mueve las piernas",
    "Habla raro y tiene media cara caída desde hace 20 minutos",
    "Sangra mucho por un corte en el muslo",
    "Fiebre de 40 y manchas que no se borran al apretarlas",
    "Se le hincha la cara después de comer gambas y le cuesta tragar",
    "Está convulsionando en la sala de espera",
)
TRIAGE_TRIVIAL: tuple[str, ...] = (
    "Catarro de hace tres semanas; ha venido a las 4 de la mañana",
    "Le duele un dedo desde 2019",
    "Viene a por la receta de siempre",
    "Quiere un justificante para el trabajo",
    "Ha leído en Google que lo suyo es muy grave",
    "Una picadura de mosquito «muy fea»",
    "Que le miren un lunar que tiene desde niño",
    "Resaca de las fiestas del pueblo",
    "Le pica la garganta desde esta tarde",
    "Quiere un análisis «de todo», por si acaso",
    "Acompaña a su madre, pero aprovecha para preguntar lo suyo",
    "Se ha cortado con un folio",
)

#: Código con un bug en una línea: (líneas, índice de la línea con el bug).
CODE_BUGS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("def media(numeros):", "    if not numeros:", "        return 0",
      "    total = sum(numeros)", "    return total / len(numeros) + 1"), 4),
    (("precio = 100", "igic = 0.07", "total = precio * igic", "print(total)",
      "guardar_factura(total)"), 2),
    (("usuarios = cargar_usuarios()", "for u in usuarios:", "    if u.activo = True:",
      "        avisar(u)", "print('hecho')"), 2),
    (("PASSWORD = 'admin123'", "def entrar(clave):", "    return clave == PASSWORD",
      "if entrar(input()):", "    abrir_panel()"), 0),
    (("nombres = ['Ana', 'Yeray']", "print(nombres[0])", "print(nombres[1])",
      "print(nombres[2])", "print('fin')"), 3),
    (("total = 0", "for pedido in pedidos:", "    total = pedido.importe",
      "print(total)", "enviar_informe(total)"), 2),
    (("import time", "def pausa_corta():", "    time.sleep(3600)  # un segundo",
      "pausa_corta()", "seguir()"), 2),
    (("def es_admin(usuario):", "    if usuario.rol == 'admin' or True:",
      "        return True", "    return False", "borrar_todo() if es_admin(yo) else None"), 1),
    (("edad = input('¿Edad? ')", "if edad >= 18:", "    entrar()", "else:",
      "    print('Vuelve en unos años')"), 0),
    (("dias = 7", "horas_por_dia = 24", "horas = dias + horas_por_dia",
      "print(f'{horas} horas')", "programar_backup(horas)"), 2),
    (("for i in range(1, len(lista)):", "    procesar(lista[i])", "print('procesados')",
      "guardar(lista)", "avisar_al_jefe()"), 0),
)  # fmt: skip

#: Revisiones de código: lo que rompe producción y lo que se puede aprobar.
PR_DANGER: tuple[str, ...] = (
    "Quitar el WHERE del UPDATE de precios",
    "Subir el .env con las contraseñas al repositorio",
    "Actualizar las 300 dependencias a la vez",
    "Desactivar los tests «que fallaban a veces»",
    "Poner el IVA al 21 % (estamos en Canarias)",
    "Borrar la carpeta /backups para ganar espacio",
    "Desplegar en producción sin revisión, que es viernes",
    "Renombrar la tabla de usuarios «porque sí»",
    "Poner a 0 el timeout de la base de datos",
    "Guardar las contraseñas en texto plano «para depurar»",
)
PR_SAFE: tuple[str, ...] = (
    "Corregir una errata en el README",
    "Añadir pruebas al cálculo del IGIC",
    "Mejorar el mensaje de error del login",
    "Subir la versión de una librería, con su changelog leído",
    "Documentar la API de pedidos",
    "Quitar código muerto que nadie usa",
    "Añadir un índice a la tabla de pedidos",
    "Traducir los textos del panel al español",
    "Arreglar el formato de las fechas a dd/mm",
)

DIALOGUE_PACKS.update(
    {
        "mir": DialoguePack(
            "📚 Pregunta tipo MIR. Regla de oro: **la respuesta prudente suele ser la buena.**",
            (
                Dialogue(
                    "Paciente de 25 años, a las 4 de la mañana en Urgencias por un dolor de "
                    "garganta de hace tres días, sin fiebre. ¿Actitud?",
                    "Explorarle, darle un analgésico y que lo siga su médico de cabecera.",
                    ("TAC de cuerpo entero.", "Ingreso en la UCI, por si acaso.",
                     "Antibiótico «por si es una bacteria»."),
                ),
                Dialogue(
                    "¿Cuál es la causa más frecuente de que un residente no haya dormido?",
                    "La guardia.",
                    ("El café.", "Las series.", "Los vecinos."),
                ),
                Dialogue(
                    "Un paciente exige antibiótico para un catarro. ¿Qué haces?",
                    "Le explicas que los antibióticos no sirven contra los virus.",
                    ("Se lo das para que se vaya.", "Le das dos, por si uno falla.",
                     "Le recetas mojo picón."),
                ),
                Dialogue(
                    "Llega un paciente con el diagnóstico impreso de internet. ¿Primer paso?",
                    "Escucharle, explorarle y luego comentar lo que ha leído.",
                    ("Tirar el papel a la papelera.", "Darle la razón en todo.",
                     "Buscarlo tú también, delante de él."),
                ),
                Dialogue(
                    "Paciente de 70 años que se cae en casa, no recuerda el golpe y toma "
                    "anticoagulantes. ¿Actitud?",
                    "Valorar un TAC craneal: con anticoagulantes, un golpe en la cabeza no se "
                    "ignora.",
                    ("Paracetamol y a casa.", "Que vuelva si le duele.", "Una tirita."),
                ),
                Dialogue(
                    "Te piden una baja de tres meses por «estrés postvacacional». ¿Qué haces?",
                    "Valoras si hay un motivo médico; si no lo hay, no la firmas.",
                    ("La firmas sin mirar.", "Firmas seis meses, ya puestos.",
                     "Te la coges tú."),
                ),
                Dialogue(
                    "Un adjunto te dice «esto lo has hecho fatal» delante del paciente. Esto es…",
                    "Un mal ejemplo de docencia.",
                    ("Docencia de calidad.", "Tradición.", "Motivación."),
                ),
                Dialogue(
                    "Ante una parada cardiorrespiratoria, ¿qué es lo primero?",
                    "Pedir ayuda y empezar las compresiones torácicas.",
                    ("Buscar su historia clínica.", "Hacerle una foto para el grupo.",
                     "Esperar al adjunto."),
                ),
                Dialogue(
                    "Paciente diabético, sudoroso y con una glucemia de 40. ¿Qué haces?",
                    "Darle azúcar o glucosa de inmediato.",
                    ("Ponerle insulina.", "Que siga en ayunas.",
                     "Esperar al análisis de mañana."),
                ),
                Dialogue(
                    "¿Cuánto dura una guardia de residente, según la leyenda del hospital?",
                    "24 horas, y luego te vas (en teoría).",
                    ("8 horas.", "Las que hagan falta.", "Hasta que te desmayes."),
                ),
            ),
        ),
        "consulta": DialoguePack(
            "🩺 Consulta de cinco minutos. Regla de oro: **claro, amable y breve (fuera "
            "esperan 40).**",
            (
                Dialogue(
                    "«Doctor, he leído en internet que lo mío es gravísimo».",
                    "Vamos a ver qué tiene usted, no lo que dice internet.",
                    ("Internet sabe más que yo.", "Pues sí, gravísimo."),
                ),
                Dialogue(
                    "Un familiar exige un TAC «porque él paga sus impuestos».",
                    "Le explicas qué pruebas hacen falta y por qué.",
                    ("Le haces tres TAC.", "Le dices que Perro Sanxe no paga TACs."),
                ),
                Dialogue(
                    "La gerencia quiere que veas 60 pacientes al día.",
                    "Pides refuerzos por escrito.",
                    ("Cinco minutos por paciente, y si no, tres.", "Atiendes a dos a la vez."),
                ),
                Dialogue(
                    "Un visitador médico te deja bolis y te ofrece un congreso.",
                    "Le das las gracias y te quedas con la evidencia científica.",
                    ("Recetas su fármaco a todo el mundo.", "Le pides el crucero."),
                ),
                Dialogue(
                    "«No me tomé las pastillas porque ya me encontraba bien».",
                    "Le explicas por qué hay que terminar el tratamiento.",
                    ("¡Bien hecho!", "Le riñes y le echas."),
                ),
                Dialogue(
                    "Un paciente te trae un queso de regalo.",
                    "Lo aceptas con una sonrisa: un queso no es un soborno.",
                    ("Le pides también un jamón.", "Llamas a la UCO."),
                ),
                Dialogue(
                    "Llevas 40 pacientes y entra uno «solo para una preguntita».",
                    "La pregunta, y si hay más, otra cita.",
                    ("Le cuentas tu vida.", "Le cobras la pregunta."),
                ),
                Dialogue(
                    "El jefe de servicio te llama un domingo.",
                    "Contestas si estás de guardia; si no, el lunes.",
                    ("Vas al hospital en pijama.", "Cambias de número."),
                ),
            ),
        ),
        "reuniones": DialoguePack(
            "📅 Reunión. Regla de oro: **gana la frase que acaba antes la reunión.**",
            (
                Dialogue(
                    "El de producto quiere «darle una vuelta» a lo que aprobasteis ayer.",
                    "Lo hablamos offline y te mando un resumen.",
                    ("Démosle esa vuelta ahora, que tenemos dos horas.",
                     "¿Y si hacemos un taller de ideación?"),
                ),
                Dialogue(
                    "Alguien comparte pantalla y no se ve nada.",
                    "Mándalo por el chat y seguimos.",
                    ("Espera, que reinicio el ordenador.", "Probemos con otra plataforma."),
                ),
                Dialogue(
                    "Faltan las dos personas que tienen que decidir.",
                    "La movemos a cuando estén y aprovechamos el rato.",
                    ("Les esperamos 20 minutos.", "Decidimos sin ellos y ya se enterarán."),
                ),
                Dialogue(
                    "Te piden una estimación «aproximada».",
                    "Mañana te digo algo cerrado, cuando lo haya mirado.",
                    ("Dos semanas (serán seis).", "Lo que tarde, tarde."),
                ),
                Dialogue(
                    "A las dos menos cinco, alguien dice «solo una cosita rápida».",
                    "Escríbela en el canal y la vemos después de comer.",
                    ("Claro, dime.", "Pedimos comida y seguimos."),
                ),
                Dialogue(
                    "Empieza la guerra de tabuladores contra espacios.",
                    "Lo decide el formateador y seguimos.",
                    ("Votación a mano alzada.", "Os dejo pelear, avisadme."),
                ),
                Dialogue(
                    "Te convocan a una reunión para preparar otra reunión.",
                    "Os paso un documento y hacemos corta la reunión de verdad.",
                    ("Acepto y propongo una tercera.", "Voy, con la cámara apagada y dormido."),
                ),
                Dialogue(
                    "El jefe pregunta quién se encarga.",
                    "Lo asigno ahora mismo y lo apunto en el ticket.",
                    ("Todos y ninguno.", "El becario."),
                ),
            ),
        ),
        "inversores": DialoguePack(
            "🦄 Ronda de inversión. Regla de oro: **cuanto más humo, más dinero.**",
            (
                Dialogue(
                    "«¿Cuál es vuestro modelo de negocio?»",
                    "Somos la plataforma de IA generativa para la economía circular.",
                    ("Vender cosas y ganar más de lo que gastamos.", "Todavía no tenemos."),
                ),
                Dialogue(
                    "«¿Cuántos usuarios tenéis?»",
                    "Crecemos de forma exponencial, con una comunidad muy comprometida.",
                    ("Catorce, y nueve son mi familia.", "Activos, ninguno."),
                ),
                Dialogue(
                    "«¿Por qué perdéis tanto dinero?»",
                    "Estamos invirtiendo en crecimiento antes de monetizar.",
                    ("Porque gastamos más de lo que entra.", "Por el futbolín de la oficina."),
                ),
                Dialogue(
                    "«¿Cuál es vuestra ventaja competitiva?»",
                    "Un algoritmo propio y un equipo de primer nivel.",
                    ("Que somos majos.", "Que copiamos muy rápido."),
                ),
                Dialogue(
                    "«¿Cuándo seréis rentables?»",
                    "Tenemos un camino claro a la rentabilidad en 18 meses.",
                    ("Nunca.", "Cuando nos compre alguien."),
                ),
                Dialogue(
                    "«¿Qué haréis con el dinero?»",
                    "Escalar el equipo y entrar en nuevos mercados.",
                    ("Una oficina con tobogán.", "Pagar las deudas."),
                ),
                Dialogue(
                    "«¿Y la competencia?»",
                    "No tenemos competencia directa: hemos creado una categoría nueva.",
                    ("Hay doscientas empresas iguales.", "Google lo hace gratis."),
                ),
                Dialogue(
                    "«¿Habéis pensado en blockchain?»",
                    "Está en nuestra hoja de ruta para la fase dos.",
                    ("¿Eso sigue existiendo?", "No, gracias."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: clasificar ---------------------------------------------------------------
#
# Cada ronda trae un papel o una persona y hay que mandarla al sitio correcto.
# Los sitios (botones) son los mismos toda la partida.


@dataclass(frozen=True, slots=True)
class SortPack:
    """Contenido de un minijuego de clasificar.

    Attributes:
        rule: Lo que se enseña arriba toda la partida.
        ask: Encabezado de cada ronda (`{n}` y `{total}`).
        bins: Sitios donde clasificar (los botones), de 2 a 5.
        items: `(texto, índice del sitio correcto)`.
    """

    rule: str
    ask: str
    bins: tuple[str, ...]
    items: tuple[tuple[str, int], ...]


SORT_PACKS: dict[str, SortPack] = {
    "ventanilla": SortPack(
        "🪟 Eres el interino de la ventanilla única. Manda a cada vecino a su negociado.",
        "🙋 Vecino {n}/{total}:",
        ("🏠 Padrón", "💶 Recaudación", "🏗️ Urbanismo", "🤝 Servicios sociales"),
        (
            ("Necesita un certificado de empadronamiento para la beca", 0),
            ("Se ha mudado al municipio y quiere darse de alta", 0),
            ("Ha cambiado de piso dentro del mismo pueblo", 0),
            ("Quiere pagar el IBI, que le venció ayer", 1),
            ("Le ha llegado el recibo de la basura dos veces", 1),
            ("Pide fraccionar el impuesto de circulación", 1),
            ("Quiere licencia para cerrar la azotea", 2),
            ("Denuncia que el vecino ha hecho una piscina sin permiso", 2),
            ("Pide licencia de obra menor para cambiar el baño", 2),
            ("Solicita ayuda a domicilio para su madre", 3),
            ("Pregunta por la ayuda de emergencia social", 3),
            ("Quiere pedir la valoración de la dependencia", 3),
        ),
    ),
    "registro": SortPack(
        "📮 Eres auxiliar en el registro. Cada papel va a su sitio (y el spam, a la papelera).",
        "📄 Papel {n}/{total}:",
        ("📨 Entrada", "📤 Salida", "🗄️ Archivo", "🗑️ Papelera"),
        (
            ("Una instancia que acaba de traer un vecino", 0),
            ("Un recurso de alzada presentado hoy", 0),
            ("La factura de un proveedor del ayuntamiento", 0),
            ("La notificación de una multa que manda el ayuntamiento", 1),
            ("Un requerimiento de documentación para un vecino", 1),
            ("La resolución de una subvención que hay que notificar", 1),
            ("Un expediente cerrado de 1994", 2),
            ("Las actas del pleno del año pasado", 2),
            ("Un expediente ya resuelto y notificado", 2),
            ("Un correo de un príncipe extranjero para el alcalde", 3),
            ("Publicidad de una academia de oposiciones", 3),
            ("Una cadena de WhatsApp impresa «para que la vea el concejal»", 3),
        ),
    ),
}

# -- Contenido: calcular -----------------------------------------------------------------
#
# Cada ronda trae varias ofertas con números y hay que quedarse con la mejor
# según una cuenta (euros por kilómetro, me gusta por visita). La mejor saca
# siempre un margen claro a la segunda (`work_games.CALC_MARGIN`).

#: Restaurantes de los pedidos del reparto.
RESTAURANTS: tuple[str, ...] = (
    "🍔 Burger del polígono",
    "🍕 Pizzería Vesubio",
    "🥙 Kebab de la esquina",
    "🍣 Sushi de Mesa y López",
    "🥡 Chino del barrio",
    "🍗 Pollos asados Chacho",
    "🥗 Poke «healthy»",
    "🌮 Tacos de Triana",
    "🧁 Dulcería de Vegueta",
    "🍝 Italiano de Las Canteras",
)
#: Temas de los vídeos del influencer.
VIDEO_TOPICS: tuple[str, ...] = (
    "📹 Probando el gofio por primera vez",
    "📹 Mi rutina de mañana (a las 13:00)",
    "📹 Reacciono a los precios de Maspalomas",
    "📹 Un día en mi vida de influencer",
    "📹 Le pongo nota a los barraquitos",
    "📹 Sigo la receta de mi abuela",
    "📹 Abro el paquete que me mandó una marca",
    "📹 Bailo el trend con mi madre",
    "📹 Cuento mi ruptura (con música triste)",
    "📹 Pruebo la comida del aeropuerto",
)

# -- Contenido: memoria, detectar y diálogos de los oficios nuevos ---------------------------

MEMORY_PACKS.update(
    {
        "entregas": MemoryPack(
            "📱 La app te encadena pedidos. Ruta {n}:",
            "Entrega en el mismo orden (el cliente valora con estrellas).",
            (("🍕", "Pizzería"), ("🏢", "Oficina del 7.º"), ("🏠", "Chalé con perro"),
             ("🏫", "Residencia de estudiantes"), ("🏥", "Hospital (turno de noche)"),
             ("⛱️", "Chiringuito de Las Canteras"), ("🅿️", "Parking del centro comercial")),
        ),
        "sellos": MemoryPack(
            "📋 Expediente {n}. El jefe de negociado te dicta el circuito de firmas:",
            "Pasa el expediente por las mismas mesas, en orden.",
            (("📥", "Registro de entrada"), ("🖋️", "Firma"), ("🔖", "Sello"),
             ("📑", "Compulsa"), ("🗂️", "Archivo"), ("📎", "Grapa"), ("☕", "Desayuno")),
        ),
        "baile": MemoryPack(
            "🎵 Trend nuevo. La coreografía va así (toma {n}):",
            "Repite la coreografía en orden. Grabando…",
            (("👈", "Izquierda"), ("👉", "Derecha"), ("🙌", "Manos arriba"), ("💃", "Giro"),
             ("🦵", "Patada"), ("🫶", "Corazón"), ("🤸", "Voltereta")),
        ),
    }
)  # fmt: skip

#: Jefe de flota: lo que hacen los riders currando y lo que hace el que se come el pedido.
RIDER_WORKING: tuple[str, ...] = (
    "🛵 entregando en un 4.º sin ascensor",
    "📍 esperando en la puerta del restaurante",
    "🌧️ pedaleando bajo la lluvia",
    "🧾 confirmando la entrega en la app",
    "🔋 cambiando la batería de la bici",
    "🗺️ buscando el portal B (no existe)",
    "📦 recogiendo tres pedidos a la vez",
    "🧊 metiendo los helados en la mochila térmica",
    "🚦 parado en el semáforo, como manda la ley",
    "📞 llamando al cliente, que no abre",
)
RIDER_SLACKING: tuple[str, ...] = (
    "🍟 se está comiendo las papas del pedido",
    "🥤 se ha bebido el refresco «porque se iba a derramar»",
    "🍕 ha abierto la pizza «para ver si estaba bien»",
    "📵 tiene la app en «ocupado» y está en la playa",
    "😴 lleva una hora «en un atasco» en su sofá",
    "🍦 se ha comido el helado «porque se derretía»",
    "🔄 acepta y cancela pedidos hasta que le sale uno bueno",
)

#: Inspector de Hacienda: declaraciones con fraude y declaraciones normales.
TAX_FRAUD: tuple[str, ...] = (
    "Declara 9.000 € al año y tiene tres deportivos a su nombre",
    "Dice vivir en Andorra, pero sube stories desde Maspalomas cada día",
    "Restaurante que solo cobra en efectivo y declara pérdidas desde 2009",
    "Desgrava como gasto de empresa un yate «para reuniones»",
    "Sociedad con sede en las Islas Caimán y oficina en Telde",
    "Factura 40.000 € de «asesoramiento verbal» a la empresa del cuñado",
    "Tiene a sus cinco gatos dados de alta como empleados",
)
TAX_CLEAN: tuple[str, ...] = (
    "Asalariado con una nómina y la deducción por alquiler antiguo",
    "Pensionista con su pensión y un plan de pensiones pequeño",
    "Autónoma que declara todo lo que factura, con sus facturas",
    "Familia numerosa con la deducción que le toca",
    "Estudiante con una beca y un verano de camarero",
    "Funcionario con su nómina y nada más",
    "Jubilado que vendió el piso del pueblo y lo declara",
    "Rider que declara sus ingresos de autónomo al céntimo",
    "Profesora que dona a una ONG y lo desgrava",
)

#: Influencer con agencia: colaboraciones turbias y colaboraciones normales.
COLLAB_SHADY: tuple[str, ...] = (
    "Una cripto que «se va a multiplicar por cien» seguro",
    "Unas pastillas que adelgazan 10 kilos en una semana",
    "Un curso para hacerte millonario vendiendo cursos",
    "Una casa de apuestas que te paga por cada seguidor que pierda",
    "Un «préstamo rápido» al 3.000 % TAE",
    "Una clínica de retoques sin título sanitario",
)
COLLAB_FINE: tuple[str, ...] = (
    "Una marca de gofio de la isla",
    "Una tienda de ropa del barrio",
    "Un festival de música en el sur",
    "Unas zapatillas de correr",
    "Una app para aprender idiomas",
    "Un restaurante de Vegueta",
    "Una marca de protector solar",
    "Un hotel rural en la cumbre",
    "Una protectora de animales",
)

DIALOGUE_PACKS.update(
    {
        "temario": DialoguePack(
            "📚 Test de la oposición. Regla de oro: **la respuesta es la de la ley**, no la "
            "de la vida.",
            (
                Dialogue("¿Cuántos artículos tiene la Constitución de 1978?", "169",
                         ("155", "200")),
                Dialogue("¿Qué artículo de la Constitución recoge el derecho a una vivienda "
                         "digna?", "El 47", ("El 14", "El 155")),
                Dialogue("¿Qué artículo de la Constitución recoge la igualdad ante la ley?",
                         "El 14", ("El 1", "El 47")),
                Dialogue("¿Qué artículo de la Constitución permite al Gobierno obligar a una "
                         "comunidad autónoma a cumplir la ley?", "El 155", ("El 47", "El 2")),
                Dialogue("¿Qué día se aprobó la Constitución en referéndum?",
                         "El 6 de diciembre de 1978",
                         ("El 29 de diciembre de 1978", "El 20 de noviembre de 1975")),
                Dialogue("¿Cuántos diputados tiene el Congreso?", "350", ("300", "400")),
                Dialogue("¿Cuántos senadores elige cada provincia peninsular?", "Cuatro",
                         ("Dos", "Seis")),
                Dialogue("¿Quién sanciona y promulga las leyes?", "El Rey",
                         ("El presidente del Gobierno", "La presidencia del Congreso")),
                Dialogue("Ley 39/2015: si la norma no fija plazo para resolver, ¿cuál es?",
                         "Tres meses", ("Diez días", "Un año")),
                Dialogue("Ley 39/2015: en un procedimiento a solicitud del interesado, ¿qué "
                         "es, por regla general, el silencio administrativo?", "Estimatorio",
                         ("Desestimatorio", "Lo que diga el jefe de negociado")),
                Dialogue("¿Qué plazo hay para el recurso de alzada contra un acto expreso?",
                         "Un mes", ("Tres meses", "Quince días")),
                Dialogue("Ley 39/2015: ¿qué días no son hábiles?",
                         "Sábados, domingos y festivos",
                         ("Solo domingos y festivos", "Los viernes por la tarde")),
                Dialogue("¿Qué título se pide para el subgrupo C2?",
                         "Graduado en Educación Secundaria Obligatoria",
                         ("Grado universitario", "Bachillerato")),
                Dialogue("¿Qué norma regula el empleo público?",
                         "El Estatuto Básico del Empleado Público",
                         ("El Estatuto de los Trabajadores", "El Reglamento del Congreso")),
                Dialogue("Si a tu solicitud le falta algo, ¿cuánto plazo te dan para "
                         "subsanarla?", "Diez días", ("Un mes", "Veinticuatro horas")),
            ),
        ),
        "ciudadano": DialoguePack(
            "🪟 Atención al ciudadano. Regla de oro: **sigue el procedimiento**, aunque se caiga "
            "el mundo.",
            (
                Dialogue(
                    "Un señor quiere que le atiendas sin cita «porque solo es una pregunta».",
                    "Le doy cita para el primer hueco y le resuelvo la duda rápida.",
                    ("Le atiendo y que esperen los de la cola.", "«Vuelva usted mañana»."),
                ),
                Dialogue(
                    "Le falta la fotocopia del DNI para el trámite.",
                    "La Administración no puede pedirle lo que ya tiene: lo consulto yo.",
                    ("Que vuelva con la fotocopia.",
                     "Que vaya a la papelería de enfrente, que es de mi cuñado."),
                ),
                Dialogue(
                    "Te traen un recurso sin firmar.",
                    "Le pido que lo firme o que lo subsane en diez días.",
                    ("Lo tiro a la papelera.", "Lo firmo yo, que tengo buena letra."),
                ),
                Dialogue(
                    "Te pide que le adelantes si le van a dar la subvención.",
                    "Se resolverá en plazo y se le notificará.",
                    ("Sí, que el concejal es su primo.", "No, se la dan a los de siempre."),
                ),
                Dialogue(
                    "Un vecino grita que tu sueldo lo paga él con sus impuestos.",
                    "Le agradezco su contribución y le ofrezco la hoja de reclamaciones.",
                    ("Entonces súbamelo.", "Le cierro la ventanilla en la cara."),
                ),
                Dialogue(
                    "Te piden un certificado que se saca en la sede electrónica.",
                    "Le enseño a sacarlo con su certificado digital.",
                    ("Que pida cita para dentro de un mes.", "Le digo que eso no existe."),
                ),
                Dialogue(
                    "Alguien llega justo cuando sales a desayunar.",
                    "Le atiendo y luego desayuno.",
                    ("Cuelgo «Vuelvo en 5 minutos» y vuelvo en 45.",
                     "Le digo que se ha caído el sistema."),
                ),
                Dialogue(
                    "Presenta la solicitud un minuto antes de cerrar el registro.",
                    "La registro con su hora y le doy el resguardo.",
                    ("Le digo que ya está cerrado.", "La dejo en la mesa para mañana."),
                ),
            ),
        ),
        "fundador": DialoguePack(
            "🚀 Fundas una app de reparto. Regla de oro: **los riders no son trabajadores, son "
            "«colaboradores»**.",
            (
                Dialogue(
                    "La Inspección de Trabajo pregunta por qué tus riders no tienen contrato.",
                    "Son emprendedores independientes que eligen su horario.",
                    ("Porque contratar es caro.", "¿Qué riders?"),
                ),
                Dialogue(
                    "Un rider pregunta por qué ha bajado la tarifa por kilómetro.",
                    "Hemos optimizado el algoritmo para darte más oportunidades.",
                    ("Porque podemos.", "Porque el inversor quiere beneficios."),
                ),
                Dialogue(
                    "Un periodista pregunta cuánto gana un rider a la hora.",
                    "Nuestros colaboradores pueden llegar a ganar hasta 20 € a la hora.",
                    ("Unos 4 €, si no llueve.", "Ni idea, nunca lo he probado."),
                ),
                Dialogue(
                    "Los inversores preguntan cuándo daréis beneficios.",
                    "Ahora toca crecer: primero, conquistar el mercado.",
                    ("Nunca.", "Cuando dejemos de pagar a los riders."),
                ),
                Dialogue(
                    "Un juez dice que tus riders son falsos autónomos.",
                    "Respetamos la sentencia y la recurriremos hasta el Supremo.",
                    ("Vale, mañana los contrato a todos.", "¿Falsos qué?"),
                ),
                Dialogue(
                    "Te preguntan por la sostenibilidad.",
                    "Repartimos en bici: somos la empresa más verde del mercado.",
                    ("Repartimos un rollito en una furgoneta diésel.",
                     "La sostenibilidad no da dinero."),
                ),
                Dialogue(
                    "Un cliente se queja de que la comida llegó fría.",
                    "Le pedimos disculpas y le damos un cupón de 3 €.",
                    ("Que se la caliente.", "Le bajamos la valoración al rider y listo."),
                ),
                Dialogue(
                    "El Gobierno aprueba la Ley Rider.",
                    "Cumpliremos la ley con un modelo innovador de subcontratas.",
                    ("Cerramos y nos vamos del país.", "Contratamos a todos mañana."),
                ),
            ),
        ),
        "directo": DialoguePack(
            "🎮 Directo. Regla de oro: **el chat siempre entretenido** (y la marca, contenta).",
            (
                Dialogue(
                    "Un hater escribe en el chat «eres malísimo».",
                    "«Gracias por el engagement, rey». Y sigues.",
                    ("Le contestas durante 40 minutos.", "Cierras el directo llorando."),
                ),
                Dialogue(
                    "Te donan 50 € con un mensaje que no se puede leer en voz alta.",
                    "Das las gracias sin leerlo y avisas al moderador.",
                    ("Lo lees entero.", "Devuelves los 50 €."),
                ),
                Dialogue(
                    "Se ve en pantalla una factura con tu dirección.",
                    "Cambias de escena al momento.",
                    ("Haces zoom para que se vea mejor.", "Dices que no es tuya."),
                ),
                Dialogue(
                    "Te llega la publi de una casa de apuestas.",
                    "La rechazas: la ley casi no deja anunciarlas con caras conocidas.",
                    ("La metes sin decir que es publi.", "Apuestas el sueldo en directo."),
                ),
                Dialogue(
                    "Llevas 8 horas de directo y el chat pide «una más».",
                    "Anuncias la última y quedas para mañana.",
                    ("Sigues hasta las 6 de la mañana.", "Te duermes en directo."),
                ),
                Dialogue(
                    "Otro streamer te manda 3.000 personas de golpe.",
                    "Les das la bienvenida y otro día le devuelves el favor.",
                    ("Sigues sin decir nada.", "Les pides que se suscriban o se vayan."),
                ),
                Dialogue(
                    "El chat pregunta por qué no vives en Andorra «como todos».",
                    "«Yo pago aquí, que la sanidad la uso».",
                    ("«Me voy la semana que viene».", "«Es por el clima»."),
                ),
                Dialogue(
                    "Se cae internet en la partida decisiva.",
                    "Avisas por redes y vuelves con el vídeo resumen.",
                    ("Culpas a tu madre en directo.", "Subes 40 stories llorando."),
                ),
            ),
        ),
        "plato": DialoguePack(
            "📺 Plató de televisión. Regla de oro: **ni confirmo ni desmiento**: el misterio paga "
            "la hipoteca.",
            (
                Dialogue(
                    "¿Es verdad que estás con tu compañero del reality?",
                    "Ni confirmo ni desmiento: lo contaré en exclusiva.",
                    ("Sí, desde el martes.", "No, es mi primo."),
                ),
                Dialogue(
                    "Otra cadena te ofrece 20.000 € por una entrevista.",
                    "Lo hablo con mi agencia, a ver si suben.",
                    ("Acepto sin leer el contrato.", "La hago gratis, por amor al arte."),
                ),
                Dialogue(
                    "Un paparazzi te pilla saliendo del gimnasio sin maquillar.",
                    "Sonríes y saludas: mañana, portada amable.",
                    ("Le tiras el batido.", "Te escondes detrás de un contenedor."),
                ),
                Dialogue(
                    "Te preguntan en directo por tu residencia en Andorra.",
                    "Eso lo lleva mi gestor; aquí he venido a hablar de mi libro.",
                    ("«Vivo en Maspalomas, pero no se lo digan a nadie».",
                     "Enseñas el piso de Andorra vacío en un story."),
                ),
                Dialogue(
                    "Tu ex sale en el programa de enfrente contando cosas.",
                    "No entras al trapo: tu silencio cotiza más.",
                    ("Llamas en directo al otro programa.", "Subes 40 stories llorando."),
                ),
                Dialogue(
                    "Te preguntan por tu libro, que no has escrito tú.",
                    "Es un proyecto muy personal en el que he volcado mi alma.",
                    ("Lo escribió un escritor fantasma.", "No sé de qué va, no lo he leído."),
                ),
                Dialogue(
                    "La marca de tu perfume quiere que digas que lo creaste tú.",
                    "Es mi esencia hecha fragancia.",
                    ("No sé ni a qué huele.", "Me pagan por decirlo."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Eventos ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    """Consecuencias de elegir una opción de un evento.

    Attributes:
        text: Lo que se cuenta al elegirla.
        pay: Parte de la base del puesto que se cobra en nómina (con impuestos).
        black: Parte de la base que se cobra en negro.
        risk: Probabilidad de que te pillen si hay dinero en negro.
        caught_by: Quién te pilla (`"inspeccion"` o `"uco"`).
        performance: Cambio de la barra de rendimiento.
        family: Cambio de la familia.
        battery: Cambio de la batería.
        stat: Estadística de logros que suma 1 al elegirla.
        demote: Si te bajan un puesto (p. ej., dimitir).
        fine: Sin dinero en negro, la multa (parte de la base) si sale `risk`.
        rating: Cambio de la valoración de la app, en décimas de estrella (reparto).
        followers: Cambio de los seguidores, en tanto por uno (influencer).
    """

    text: str
    pay: float = 0.0
    black: float = 0.0
    risk: float = 0.0
    caught_by: str = "inspeccion"
    performance: int = 0
    family: int = 0
    battery: int = 0
    stat: str | None = None
    demote: bool = False
    fine: float = 0.0
    rating: int = 0
    followers: float = 0.0


@dataclass(frozen=True, slots=True)
class Event:
    """Algo que pasa al terminar un turno, con dos opciones.

    Attributes:
        key: Identificador estable.
        text: La situación.
        options: `(etiqueta del botón, consecuencias)`; siempre dos.
        jobs: Oficios donde sale (vacío = todos).
        min_level: Puesto mínimo.
        family_below: Solo sale con la familia por debajo de esto (0 = siempre).
        abroad: `True`, solo trabajando fuera; `False`, solo en casa; `None`, siempre.
        phase: Solo con esa situación fiscal (`work.residence_phase`).
        special: No sale al azar; lo lanza una regla (p. ej., el teletrabajo).
    """

    key: str
    text: str
    options: tuple[tuple[str, Outcome], tuple[str, Outcome]]
    jobs: tuple[str, ...] = ()
    min_level: int = 1
    family_below: int = 0
    abroad: bool | None = None
    phase: str | None = None
    special: bool = False


EVENTS: tuple[Event, ...] = (
    # -- Para todos ------------------------------------------------------------------
    Event(
        "quedarse",
        "🕖 Son las siete y tu jefe asoma la cabeza: «Oye, ¿te puedes quedar un ratito más?».",
        (
            (
                "Me quedo",
                Outcome(
                    "Te quedas. Te lo pagan como extra, pero en casa ya han cenado.",
                    pay=0.5,
                    battery=-15,
                    family=-8,
                    stat="work_stayed",
                ),
            ),
            (
                "Tengo cosas",
                Outcome(
                    "«Tengo cosas que hacer». Tu jefe apunta algo en una libreta.",
                    performance=-10,
                    family=5,
                ),
            ),
        ),
    ),
    Event(
        "cena_empresa",
        "🥂 Cena de empresa en un restaurante de Las Canteras. Va todo el mundo.",
        (
            (
                "Voy",
                Outcome(
                    "Fuiste, bailaste con el jefe y él lo recuerda con cariño.",
                    performance=10,
                    battery=-10,
                    family=-3,
                ),
            ),
            (
                "No voy",
                Outcome(
                    "No fuiste. Al día siguiente todos tienen una anécdota menos tú.",
                    performance=-5,
                    family=5,
                ),
            ),
        ),
    ),
    Event(
        "en_b",
        "💶 Tu jefe, bajito: «Este mes te pago una parte en B, ¿vale? Así no te quitan el IMV».",
        (
            (
                "Vale",
                Outcome(
                    "Te llevas un sobre. Sin IRPF, sin cotizar… y sin cubrirte si te pillan.",
                    black=0.6,
                    risk=0.15,
                    stat="work_black_offer",
                ),
            ),
            (
                "Todo en nómina",
                Outcome(
                    "Le dices que todo en nómina. Te mira como si fueras de Hacienda.",
                    pay=0.3,
                    performance=-5,
                ),
            ),
        ),
    ),
    # -- Familia ----------------------------------------------------------------------
    Event(
        "madre",
        "📱 Tu madre te escribe por Discord: «¿Sigues vivo? Hace días que no te veo el pelo».",
        (
            (
                "La llamo",
                Outcome(
                    "La llamas. Te cuenta tres dramas del barrio y se queda feliz.",
                    family=12,
                    battery=-5,
                ),
            ),
            (
                "Le dejo el visto",
                Outcome("Visto a las 23:47. Ella lo sabe.", family=-6, stat="work_mom_ghosted"),
            ),
        ),
        family_below=40,
    ),
    Event(
        "comunion",
        "⛪ Es la comunión de tu sobrino y coincide con tu turno de mañana.",
        (
            (
                "Voy",
                Outcome(
                    "Fuiste. Comiste tarta y saliste en las fotos.", family=15, performance=-10
                ),
            ),
            (
                "Le mando un Bizum",
                Outcome(
                    "Le mandas un Bizum de 50 € con un emoji de cura. Tu hermana no lo olvidará.",
                    family=-5,
                    stat="work_communion_bizum",
                ),
            ),
        ),
        family_below=60,
    ),
    Event(
        "pareja",
        "📝 Al llegar a casa encuentras una nota en la nevera: «La cena está en el micro. "
        "Tú y yo tenemos que hablar».",
        (
            (
                "Hablamos",
                Outcome(
                    "Habláis hasta las dos. Mañana vas hecho polvo, pero en paz.",
                    family=20,
                    battery=-20,
                ),
            ),
            (
                "Mañana",
                Outcome(
                    "«Mañana hablamos». Mañana también trabajas.",
                    family=-10,
                    stat="work_note_ignored",
                ),
            ),
        ),
        family_below=30,
    ),
    # -- Obra -------------------------------------------------------------------------
    Event(
        "jubilado",
        "👴 Un jubilado se apoya en la valla: «Eso así no se hace, chacho. En mis tiempos…».",
        (
            (
                "Le explico",
                Outcome(
                    "Le explicas la obra entera. Se va feliz y vuelve mañana con otro.",
                    performance=5,
                    battery=-5,
                    stat="work_retiree",
                ),
            ),
            ("Sigo a lo mío", Outcome("Sigues cavando. Él sigue mirando. Para siempre.")),
        ),
        jobs=("obra",),
    ),
    Event(
        "calima",
        "🌫️ Entra calima fuerte y Protección Civil activa la alerta. El encargado duda.",
        (
            (
                "Paramos",
                Outcome(
                    "Se para la obra y se paga igual: permiso retribuido por fenómenos "
                    "meteorológicos adversos (art. 37.3.g ET).",
                    pay=0.5,
                    family=5,
                    stat="work_calima",
                ),
            ),
            (
                "Seguimos",
                Outcome(
                    "Seguís con mascarilla. Toses polvo del Sáhara hasta el jueves.",
                    performance=10,
                    battery=-15,
                ),
            ),
        ),
        jobs=("obra",),
    ),
    # -- Hostelería ---------------------------------------------------------------------
    Event(
        "propina",
        "🪙 Una pareja de alemanes te deja una propina de 20 € en efectivo.",
        (
            (
                "A la caja común",
                Outcome(
                    "A la caja común. Tus compañeros te quieren un poco más.",
                    performance=5,
                    family=0,
                ),
            ),
            (
                "Al bolsillo",
                Outcome(
                    "Al bolsillo. Dinero en negro, pero en pequeñito.",
                    black=0.4,
                    risk=0.03,
                    stat="work_tip",
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "sinpa",
        "🏃 Una mesa de cuatro se levanta sin pagar y sale corriendo hacia el paseo.",
        (
            (
                "Los persigo",
                Outcome(
                    "Los alcanzas en la avenida y pagan avergonzados.",
                    performance=12,
                    battery=-12,
                    stat="work_dine_dash",
                ),
            ),
            (
                "Que se vayan",
                Outcome("Que se vayan. El jefe te lo descuenta de la moral.", performance=-8),
            ),
        ),
        jobs=("hosteleria",),
    ),
    # -- Política: corrupción ---------------------------------------------------------
    Event(
        "sobre",
        "✉️ Un empresario muy simpático te deja un sobre gordito «para la campaña».",
        (
            (
                "Lo cojo",
                Outcome(
                    "Te lo guardas en la gabardina. Chistorras de 500, nada menos.",
                    black=2.0,
                    risk=0.25,
                    caught_by="uco",
                    stat="work_envelope",
                ),
            ),
            (
                "No, gracias",
                Outcome(
                    "Lo rechazas. En el partido empiezan a desconfiar de ti.",
                    performance=-10,
                    stat="work_envelope_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=2,
    ),
    Event(
        "enchufe",
        "🔌 Un dirigente de Ferraz te llama: «Necesito colocar a mi sobrino. ¿Lo ves?».",
        (
            (
                "Lo coloco",
                Outcome(
                    "Lo colocas de asesor. Ferraz no olvida a quien hace favores.",
                    performance=35,
                    stat="work_cronyism",
                ),
            ),
            (
                "No hay plaza",
                Outcome(
                    "Le dices que no hay plaza. Te mandan a pegar carteles a Fuerteventura.",
                    performance=-15,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=2,
    ),
    Event(
        "mordida",
        "🏗️ La constructora de la nueva rotonda ofrece «un 3 % para la fundación».",
        (
            (
                "Para la fundación",
                Outcome(
                    "La fundación crece. Tú también, un poquito.",
                    black=3.0,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_kickback",
                ),
            ),
            (
                "Concurso público",
                Outcome(
                    "Haces un concurso público de verdad. Nadie entiende nada.",
                    performance=-5,
                    stat="work_clean_tender",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=3,
    ),
    Event(
        "falcon",
        "✈️ Hay un concierto en Benicàssim y el avión oficial está libre.",
        (
            (
                "Agenda oficial",
                Outcome(
                    "Lo apuntas como agenda oficial. Pagan los de siempre.",
                    performance=5,
                    battery=10,
                    family=5,
                    stat="work_falcon",
                ),
            ),
            ("Voy en guagua", Outcome("Vas en guagua. Llegas tarde y sin batería.", battery=-15)),
        ),
        jobs=("politica",),
        min_level=4,
    ),
    # -- Sanidad ------------------------------------------------------------------------
    Event(
        "cancun",
        "🏝️ Un visitador médico te invita a un «congreso» en Cancún. Hotel con pulsera y "
        "una charla de veinte minutos.",
        (
            (
                "Me apunto",
                Outcome(
                    "Vuelves moreno y con dos fotos de la charla. El viaje lo pagó otro.",
                    black=0.8,
                    risk=0.1,
                    caught_by="expediente",
                    battery=20,
                    family=5,
                    stat="work_cancun",
                ),
            ),
            (
                "Ética ante todo",
                Outcome("Lo rechazas. El visitador te mira raro.", stat="work_cancun_refused"),
            ),
        ),
        jobs=("sanidad",),
        min_level=3,
    ),
    Event(
        "agresivo",
        "😤 Un familiar se pone a gritar en la sala de espera porque «lleva tres horas».",
        (
            (
                "Seguridad",
                Outcome(
                    "Llamas a seguridad. Se calma de golpe.", performance=5, stat="work_aggressive"
                ),
            ),
            (
                "Lo calmo yo",
                Outcome(
                    "Le explicas el triaje con paciencia infinita. Funciona.",
                    performance=10,
                    battery=-10,
                    stat="work_aggressive",
                ),
            ),
        ),
        jobs=("sanidad",),
    ),
    Event(
        "lista_espera",
        "📉 Gerencia te pide «optimizar» la lista de espera antes de que salga en el periódico.",
        (
            (
                "La optimizo",
                Outcome(
                    "Mueves a medio paciente a «pendiente de cita». La lista baja, la espera no.",
                    performance=15,
                    stat="work_waitlist",
                ),
            ),
            (
                "Me niego",
                Outcome("Le dices a gerencia que la lista es la que es.", performance=-10),
            ),
        ),
        jobs=("sanidad",),
        min_level=4,
    ),
    Event(
        "huelga",
        "🪧 Los residentes convocan huelga por las guardias de 24 horas.",
        (
            (
                "Me sumo",
                Outcome(
                    "Te sumas. Pierdes el día, pero ganas compañeros.",
                    performance=-10,
                    family=10,
                    stat="work_strike",
                ),
            ),
            (
                "Voy a trabajar",
                Outcome(
                    "Vas a trabajar. Te pagan, pero en el grupo ya no te hablan.",
                    pay=0.5,
                    stat="work_scab",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=2,
    ),
    Event(
        "aplausos",
        "👏 Son las ocho y un vecino aplaude desde el balcón. Nadie sabe si va en serio.",
        (
            (
                "Saludo",
                Outcome("Saludas con la mano. Él sigue aplaudiendo.", family=5, stat="work_clap"),
            ),
            ("Sigo andando", Outcome("Sigues andando. Ya no estamos para eso.")),
        ),
        jobs=("sanidad",),
    ),
    Event(
        "cambio_turno",
        "📆 Te cambian el turno del domingo con un día de antelación.",
        (
            (
                "Vale",
                Outcome(
                    "Aceptas. Tu familia aplaza la paella otra vez.",
                    family=-10,
                    performance=10,
                    stat="work_shift_swap",
                ),
            ),
            ("No puedo", Outcome("Dices que no. La supervisora toma nota.", performance=-5)),
        ),
        jobs=("sanidad",),
    ),
    # -- Oficina ------------------------------------------------------------------------
    Event(
        "viernes",
        "🚀 Son las 17:40 de un viernes y hay que desplegar «una cosita».",
        (
            (
                "Despliego",
                Outcome(
                    "Despliegas. Funciona. Esta vez.",
                    performance=10,
                    battery=-15,
                    stat="work_deploy_friday",
                ),
            ),
            ("El lunes", Outcome("Lo dejas para el lunes. El cliente se enfada.", performance=-5)),
        ),
        jobs=("oficina",),
        min_level=2,
    ),
    Event(
        "reunion_correo",
        "📨 Te invitan a una reunión de una hora para algo que cabía en un correo.",
        (
            (
                "Voy",
                Outcome(
                    "Vas. Una hora de tu vida que no vuelve.",
                    battery=-5,
                    stat="work_useless_meeting",
                ),
            ),
            (
                "Contesto por correo",
                Outcome(
                    "Contestas por correo y te cancelan la reunión. Héroe.",
                    performance=-5,
                    stat="work_meeting_killed",
                ),
            ),
        ),
        jobs=("oficina",),
    ),
    Event(
        "irlanda",
        "🇮🇪 Un reclutador de LinkedIn te ofrece irte a Dublín por el doble.",
        (
            (
                "Lo pienso",
                Outcome(
                    "Se lo dices a tu jefe para que te suba el sueldo. No cuela.",
                    performance=-5,
                    stat="work_ireland",
                ),
            ),
            ("Me quedo", Outcome("Te quedas. La lealtad cotiza poco.", performance=5)),
        ),
        jobs=("oficina",),
        min_level=2,
    ),
    Event(
        "linkedin",
        "💼 Te tienta escribir en LinkedIn: «Agradecido y emocionado de anunciar…».",
        (
            (
                "Publico",
                Outcome(
                    "Publicas. Tres reacciones: tu madre, tu jefe y un bot.",
                    family=-2,
                    stat="work_linkedin",
                ),
            ),
            ("Ni de broma", Outcome("Cierras LinkedIn. Tu dignidad lo agradece.")),
        ),
        jobs=("oficina",),
    ),
    Event(
        "paintball",
        "🔫 Team building: paintball un sábado por la mañana.",
        (
            (
                "Voy",
                Outcome(
                    "Le das al jefe en la espalda «sin querer».",
                    battery=-15,
                    performance=5,
                    stat="work_paintball",
                ),
            ),
            (
                "Tengo cosas",
                Outcome("No vas. El lunes todos tienen moratones menos tú.", performance=-5),
            ),
        ),
        jobs=("oficina",),
    ),
    Event(
        "ninja",
        "🥷 Contratan a un «ninja de la IA» que cobra el triple que tú.",
        (
            (
                "Le enseño el código",
                Outcome(
                    "Le enseñas el código. Él lo pega en un chatbot.",
                    performance=5,
                    stat="work_ai_ninja",
                ),
            ),
            (
                "Que se apañe",
                Outcome(
                    "Rompe producción en dos días. Te llaman a ti.", performance=-10, battery=-10
                ),
            ),
        ),
        jobs=("oficina",),
    ),
    Event(
        "desconexion",
        "📱 Son las 23:12. Tu jefe: «Oye, ¿tienes un momentito?».",
        (
            (
                "Contesto",
                Outcome(
                    "Contestas. El momentito dura hasta la una.",
                    performance=10,
                    family=-8,
                    stat="work_always_online",
                ),
            ),
            (
                "Desconexión digital",
                Outcome(
                    "Ni lo abres: tienes derecho a la desconexión digital (art. 88 de la Ley "
                    "Orgánica 3/2018).",
                    performance=-5,
                    family=5,
                    stat="work_disconnect",
                ),
            ),
        ),
        jobs=("oficina",),
        special=True,
    ),
    # -- Hong Kong ----------------------------------------------------------------------
    Event(
        "t8",
        "🌀 El Observatorio de Hong Kong iza la señal de tifón n.º 8. La ciudad se para.",
        (
            (
                "Me quedo en casa",
                Outcome(
                    "Te quedas en casa y cobras igual, como recomienda el Labour Department.",
                    pay=1.0,
                    family=5,
                    stat="work_t8",
                ),
            ),
            (
                "Voy a la oficina",
                Outcome(
                    "Llegas empapado. Eres el único. Tu jefe te ve.",
                    performance=10,
                    battery=-15,
                    stat="work_t8_hero",
                ),
            ),
        ),
        jobs=("oficina",),
        abroad=True,
    ),
    Event(
        "dimsum",
        "🥟 Robuso, que vive en Hong Kong, te invita a dim sum el domingo.",
        (
            (
                "Voy",
                Outcome(
                    "Har gow, siu mai y Robuso poniéndote al día del servidor. Vuelves nuevo.",
                    battery=10,
                    family=5,
                    stat="work_dimsum",
                ),
            ),
            (
                "Tengo que currar",
                Outcome(
                    "Te quedas trabajando. Robuso te manda fotos de los bollos.", performance=5
                ),
            ),
        ),
        jobs=("oficina",),
        abroad=True,
    ),
    Event(
        "lkf",
        "🍸 Afterwork en Lan Kwai Fong con los del equipo.",
        (
            (
                "Salgo",
                Outcome(
                    "Sales. Acabas cantando en un karaoke de Wan Chai.",
                    battery=-20,
                    performance=-5,
                    stat="work_lkf",
                ),
            ),
            (
                "Me retiro pronto",
                Outcome("Te vas a las diez. El lunes no entiendes los chistes.", battery=5),
            ),
        ),
        jobs=("oficina",),
        abroad=True,
    ),
    Event(
        "videollamada",
        "📞 Tu madre te hace una videollamada. En Hong Kong son las tres de la mañana.",
        (
            (
                "Contesto",
                Outcome(
                    "Contestas medio dormido. Ella está feliz de verte la cara.",
                    family=15,
                    battery=-10,
                    stat="work_videocall",
                ),
            ),
            ("Mañana la llamo", Outcome("Mañana la llamas. Ella lo sabe.", family=-5)),
        ),
        jobs=("oficina",),
        abroad=True,
    ),
    Event(
        "carta_hacienda",
        "📨 Te llega una carta de la Agencia Tributaria: «¿De verdad vive usted en Hong Kong? "
        "Su familia y sus intereses siguen en España».",
        (
            (
                "Demuestro que vivo aquí",
                Outcome(
                    "Mandas el alquiler, el certificado de residencia y la factura de la luz. "
                    "Te dejan en paz.",
                    performance=-5,
                    stat="work_proved_residence",
                ),
            ),
            (
                "La ignoro",
                Outcome(
                    "Ignoras la carta. A ver qué pasa.",
                    risk=0.4,
                    fine=3.0,
                    caught_by="hacienda",
                    stat="work_ignored_letter",
                ),
            ),
        ),
        jobs=("oficina",),
        abroad=True,
        phase="no_residente",
    ),
    # -- Reparto ------------------------------------------------------------------------
    Event(
        "sexto_sin_ascensor",
        "🏢 El cliente deja 0,50 € de propina y te pide que se lo subas a un 6.º sin ascensor.",
        (
            (
                "Subo",
                Outcome(
                    "Subes. Llegas sin aire, pero el cliente te pone cinco estrellas.",
                    battery=-10,
                    rating=2,
                    stat="work_sixth_floor",
                ),
            ),
            (
                "Que baje él",
                Outcome(
                    "«Te espero en el portal». El cliente baja en pijama y te pone una estrella.",
                    rating=-4,
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "diluvio",
        "🌧️ Diluvia en Las Palmas (tres gotas) y la app ofrece 2 € más por pedido. ¿Sales?",
        (
            (
                "Salgo",
                Outcome(
                    "Sales con el chubasquero del chino. Cobras más y llegas empapado.",
                    pay=0.5,
                    battery=-15,
                    stat="work_storm",
                ),
            ),
            ("Me quedo en casa", Outcome("Te quedas en casa viendo llover.", family=6)),
        ),
        jobs=("reparto",),
    ),
    Event(
        "perro_entrega",
        "🐕 En la entrega te espera un perro sin atar. El dueño grita: «¡No hace nada!».",
        (
            (
                "Corro",
                Outcome(
                    "Corres como nunca. El perro también. Entregas el pedido desde la valla.",
                    battery=-10,
                    stat="work_dog_chase",
                ),
            ),
            (
                "Le doy una papa",
                Outcome(
                    "Le das una papa del pedido. El perro, feliz; el cliente, no tanto.",
                    rating=-2,
                    stat="work_dog_bribe",
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "huelga_riders",
        "✊ Los riders convocan una desconexión masiva contra la bajada de tarifas.",
        (
            (
                "Me desconecto",
                Outcome(
                    "Te desconectas con los compañeros. La app toma nota.",
                    performance=-10,
                    family=5,
                    stat="work_rider_strike",
                ),
            ),
            (
                "Reparto igual",
                Outcome(
                    "Repartes tú solo media ciudad. Pedidos a montones, compañeros mirando mal.",
                    pay=0.4,
                    battery=-10,
                    stat="work_rider_scab",
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "un_hielo",
        "🧊 Un cliente pide a las 4 de la mañana un solo cubito de hielo. Gastos de envío: 3 €.",
        (
            (
                "Lo llevo",
                Outcome(
                    "Llevas un hielo en una mochila térmica de 40 litros. Cinco estrellas.",
                    pay=0.1,
                    rating=2,
                    stat="work_ice_cube",
                ),
            ),
            ("Lo rechazo", Outcome("Lo rechazas. La app te lo apunta.", rating=-2)),
        ),
        jobs=("reparto",),
    ),
    # -- Funcionariado ---------------------------------------------------------------------
    Event(
        "desayuno",
        "🥐 Son las 10:00: la hora sagrada del desayuno. Hay cola en la ventanilla.",
        (
            (
                "Me voy a desayunar",
                Outcome(
                    "Cuelgas «Vuelvo en 5 minutos» y vuelves en 40, con el barraquito dentro.",
                    battery=15,
                    performance=-5,
                    stat="work_breakfast",
                ),
            ),
            (
                "Atiendo la cola",
                Outcome(
                    "Atiendes la cola en ayunas. Nadie te lo agradece.", performance=5, battery=-5
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "expediente_perdido",
        "📁 Aparece detrás del archivador un expediente de 2011 sin resolver.",
        (
            (
                "Lo tramito",
                Outcome(
                    "Lo tramitas. El vecino lleva trece años esperando y llora al teléfono.",
                    performance=10,
                    battery=-10,
                    stat="work_lost_file",
                ),
            ),
            (
                "Lo vuelvo a esconder",
                Outcome(
                    "Lo vuelves a dejar donde estaba. Ojos que no ven…",
                    risk=0.15,
                    fine=0.5,
                    caught_by="defensor",
                    stat="work_hidden_file",
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "sistema_caido",
        "💻 Se ha caído el sistema informático. Otra vez.",
        (
            (
                "Lo apunto a mano",
                Outcome("Lo apuntas todo a boli, como en 1985.", performance=5, battery=-10),
            ),
            (
                "Cartel de «sin sistema»",
                Outcome(
                    "Cuelgas el cartel y te vas a casa antes. Mañana, el doble de cola.",
                    family=5,
                    stat="work_no_system",
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "sobrino_concejal",
        "🤝 Un concejal te pide que «mires con cariño» el expediente de su sobrino.",
        (
            (
                "Lo miro con cariño",
                Outcome(
                    "Lo pones el primero de la pila. El concejal te deja un sobre «para el café».",
                    black=0.5,
                    risk=0.15,
                    caught_by="uco",
                    stat="work_favor",
                ),
            ),
            (
                "Por orden de entrada",
                Outcome(
                    "«Va por orden de entrada, como todos». El concejal ya no te saluda.",
                    performance=-5,
                    stat="work_by_the_book",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=2,
    ),
    Event(
        "moscosos",
        "🏖️ Te quedan días de asuntos propios (los moscosos) sin gastar y acaba el año.",
        (
            (
                "Me los cojo",
                Outcome(
                    "Te los coges todos seguidos. Tu familia ya no se acordaba de ti.",
                    family=15,
                    performance=-5,
                    stat="work_moscosos",
                ),
            ),
            (
                "Los regalo",
                Outcome("Los pierdes. Tu jefe de negociado te mira raro.", performance=5),
            ),
        ),
        jobs=("funcionariado",),
        min_level=2,
    ),
    # -- Influencer -----------------------------------------------------------------------
    Event(
        "funa",
        "🔥 Te están funando: alguien ha encontrado un tuit tuyo de 2014.",
        (
            (
                "Vídeo pidiendo perdón",
                Outcome(
                    "Subes un vídeo llorando con sudadera gris. Pierdes seguidores, pero menos.",
                    followers=-0.1,
                    performance=-5,
                    stat="work_apology",
                ),
            ),
            (
                "Doblo la apuesta",
                Outcome(
                    "Dices que es la «cultura de la cancelación». Te funan más fuerte.",
                    followers=-0.3,
                    performance=5,
                    stat="work_cancelled",
                ),
            ),
        ),
        jobs=("influencer",),
    ),
    Event(
        "andorra",
        "🏔️ Tu agencia te propone irte a vivir a Andorra «por el clima».",
        (
            (
                "Me voy",
                Outcome(
                    "Te vas una semana. Hace frío, no hay playa y echas de menos el gofio. "
                    "Vuelves sin decir nada a nadie.",
                    family=-10,
                    battery=-10,
                    stat="work_andorra",
                ),
            ),
            (
                "Me quedo",
                Outcome(
                    "Te quedas en Canarias pagando lo tuyo. Hacienda te manda un corazón.",
                    family=5,
                    stat="work_andorra_no",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=3,
    ),
    Event(
        "publi_encubierta",
        "📦 Una marca te paga por enseñar su crema, pero sin poner que es publicidad.",
        (
            (
                "Pongo #publi",
                Outcome(
                    "Pones #publi. Cobras y duermes tranquilo.",
                    pay=0.3,
                    stat="work_ad_tagged",
                ),
            ),
            (
                "Sin #publi",
                Outcome(
                    "Lo cuelas como si te encantara de verdad.",
                    black=0.5,
                    risk=0.2,
                    caught_by="consumo",
                    stat="work_hidden_ad",
                ),
            ),
        ),
        jobs=("influencer",),
    ),
    Event(
        "regalo_marca",
        "🎁 Te llega a casa una caja enorme de una marca: un móvil nuevo «de regalo».",
        (
            (
                "Lo declaro",
                Outcome(
                    "Lo declaras: un regalo por publicidad es renta en especie y tributa.",
                    pay=0.2,
                    stat="work_gift_declared",
                ),
            ),
            (
                "Calladito",
                Outcome(
                    "Lo estrenas en un story y no le dices nada a nadie (salvo a tus seguidores).",
                    black=0.4,
                    risk=0.15,
                    caught_by="regalos",
                    stat="work_gift_hidden",
                ),
            ),
        ),
        jobs=("influencer",),
    ),
    Event(
        "colaboracion_amiga",
        "🤝 Otro influencer te propone un vídeo juntos para «ayudarnos a crecer».",
        (
            (
                "Hecho",
                Outcome(
                    "Hacéis un baile juntos. Sus seguidores te descubren.",
                    followers=0.1,
                    battery=-5,
                    stat="work_collab",
                ),
            ),
            ("Paso", Outcome("Pasas. Él lo hace con otro y se hace viral.", performance=-5)),
        ),
        jobs=("influencer",),
    ),
)

EVENT_BY_KEY: dict[str, Event] = {event.key: event for event in EVENTS}

#: Qué pasa cuando la UCO o la Inspección te pillan con dinero en negro.
CAUGHT_TEXT: dict[str, str] = {
    "inspeccion": (
        "🕵️ **Inspección de Trabajo.** Te pillan cobrando en negro: devuelves lo cobrado con "
        "un recargo del 20 % y te suspenden el IMV 3 días."
    ),
    "uco": (
        "🚔 **La UCO llama a tu puerta a las seis de la mañana.** Estás imputado: devuelves "
        "lo del sobre con un 20 % de recargo y te quedas sin IMV 3 días."
    ),
    "expediente": (
        "📋 **Expediente disciplinario.** El hospital se entera del «congreso»: devuelves el "
        "viaje con un 20 % de recargo."
    ),
    "hacienda": (
        "📨 **Hacienda te regulariza.** Dice que tu centro de intereses económicos sigue en "
        "España (art. 9.1.b LIRPF) y te cobra la diferencia con recargo."
    ),
    "defensor": (
        "📮 **Queja al Diputado del Común.** El vecino del expediente escondido se ha quejado "
        "y te abren un expediente a ti: multa."
    ),
    "consumo": (
        "📢 **Consumo te expedienta por publicidad encubierta** (art. 26 de la Ley de "
        "Competencia Desleal): devuelves lo de la marca con un 20 % de recargo."
    ),
    "regalos": (
        "📨 **Hacienda cruza datos con la marca.** El regalo era renta en especie: lo "
        "regularizas con un 20 % de recargo."
    ),
}
#: Pillados que además dejan sin IMV unos días.
CAUGHT_SUSPENDS_IMV = frozenset({"inspeccion", "uco"})

#: Probabilidad de que te indulten tras pillarte la UCO (no pagas la multa).
PARDON_CHANCE = 0.2

#: Pregunta después de que te pille la UCO.
RESIGN_EVENT = Event(
    "dimision",
    "🎙️ Te esperan los periodistas en la puerta de casa. «¿Va a dimitir?».",
    (
        (
            "Dimito",
            Outcome(
                "Dimites. Bajas un puesto, pero duermes tranquilo.",
                demote=True,
                family=10,
                stat="work_resigned",
            ),
        ),
        (
            "Ni de broma",
            Outcome(
                "«Dimitir no está en mi vocabulario». El sillón es tuyo.",
                performance=-20,
                stat="work_clinging",
            ),
        ),
    ),
    jobs=("politica",),
)


def events_for(
    job: str, level: int, family: int, *, abroad: bool = False, phase: str = "casa"
) -> list[Event]:
    """Eventos que pueden salir al azar con este oficio, puesto, familia y país."""
    return [
        event
        for event in EVENTS
        if not event.special
        and (not event.jobs or job in event.jobs)
        and level >= event.min_level
        and (not event.family_below or family < event.family_below)
        and (event.abroad is None or event.abroad == abroad)
        and (event.phase is None or event.phase == phase)
    ]


# -- Café ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Coffee:
    """Algo de la máquina de café: cuánto recarga y cuánto cuesta (base, sin IGIC)."""

    key: str
    name: str
    emoji: str
    battery: int
    price: int


COFFEES: tuple[Coffee, ...] = (
    Coffee("cortado", "Cortado", "☕", 10, 14),
    Coffee("barraquito", "Barraquito", "🥃", 20, 25),
)
COFFEE_BY_KEY: dict[str, Coffee] = {coffee.key: coffee for coffee in COFFEES}
