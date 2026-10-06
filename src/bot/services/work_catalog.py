"""Catálogo del trabajo (`pala`): oficios, puestos, contenido de minijuegos y eventos.

Solo datos. Añadir un oficio es añadir un `Job` a `JOBS` con sus cinco
puestos, su contenido de minijuego (si usa uno nuevo) y, si se quiere, sus
eventos. Los `key` (oficios, formaciones, tareas, eventos) no se cambian
nunca: son lo que se guarda en la base de datos.

Sueldos: la base común es 150 / 250 / 400 / 650 / 1.000 Y$ por turno y cada
oficio la ajusta (la hostelería empieza por debajo, la política se dispara
arriba). Con jornada completa todos los días, la retención de IRPF queda
entre el 0 y el 11 % en el puesto 1 y entre el 37 y el 44 % en el 5 (ver la
nómina en `bot.services.taxes`).

La política es una sátira del PSOE, el partido del Gobierno, y de sus
tópicos (sobres, enchufes, comisiones, puertas giratorias). No se nombra a
ninguna persona real: los personajes son genéricos.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.work import Job, Mechanic, Position, Task, Training

# -- Tareas comunes --------------------------------------------------------------------
#
# Contadores de `Contract.progress` (se ponen a cero al cambiar de puesto):
# `good` turnos de 90 o más, `extras` turnos extra, `events` eventos
# resueltos, y los del minijuego: `clean` (turnos de cavar sin romper nada),
# `caught` (aciertos de detectar), `perfect_rounds` (rondas de memoria sin
# fallo) y `smooth` (respuestas buenas en un diálogo).


def _good(goal: int) -> Task:
    return Task("good", f"Haz {goal} turnos de 90 o más", "good", goal)


def _extras(goal: int) -> Task:
    return Task("extras", f"Echa {goal} turno{'s' if goal > 1 else ''} extra", "extras", goal)


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
            150,
            Mechanic.DIG,
            30,
            (_good(3), Task("clean", "Haz 2 turnos sin romper nada", "clean", 2)),
            days=2,
            content="cavar:3",
        ),
        Position(
            2,
            "Oficial de segunda",
            250,
            Mechanic.DIG,
            30,
            (_good(4), _extras(1)),
            days=3,
            content="cavar:3",
            training=Training("prl20", "el curso de PRL de 20 horas", 1_500),
        ),
        Position(
            3,
            "Oficial de primera",
            400,
            Mechanic.DIG,
            40,
            (_good(5), Task("clean", "Haz 4 turnos sin romper nada", "clean", 4)),
            days=4,
            content="cavar:4",
        ),
        Position(
            4,
            "Encargado",
            715,
            Mechanic.SPOT,
            60,
            (Task("caught", "Pilla a 25 escaqueados", "caught", 25), _good(5), _events(2)),
            days=5,
            content="vagos",
            training=Training("recurso", "el curso de recurso preventivo de 60 horas", 4_000),
        ),
        Position(
            5,
            "Constructor",
            1_300,
            Mechanic.SPOT,
            60,
            (),
            days=0,
            content="sobrecostes",
            self_employed=True,
            training=Training("rea", "el alta en el Registro de Empresas Acreditadas", 12_000),
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
            120,
            Mechanic.MEMORY,
            30,
            (_good(3), Task("perfect", "Saca 6 tandas perfectas", "perfect_rounds", 6)),
            days=2,
            content="platos:3",
        ),
        Position(
            2,
            "Camarero",
            210,
            Mechanic.MEMORY,
            30,
            (_good(4), _extras(1)),
            days=3,
            content="comandas:3",
            training=Training("manipulador", "el carnet de manipulador de alimentos", 800),
        ),
        Position(
            3,
            "Jefe de rango",
            360,
            Mechanic.MEMORY,
            40,
            (Task("perfect", "Saca 15 comandas perfectas", "perfect_rounds", 15), _good(5)),
            days=4,
            content="comandas:4",
        ),
        Position(
            4,
            "Jefe de cocina",
            650,
            Mechanic.MEMORY,
            60,
            (
                Task("perfect", "Saca 20 comandas perfectas", "perfect_rounds", 20),
                _good(6),
                _events(2),
            ),
            days=5,
            content="cocina:5",
            training=Training("alergenos", "el curso de alérgenos y APPCC", 2_500),
        ),
        Position(
            5,
            "Dueño del chiringuito",
            1_300,
            Mechanic.DIALOGUE,
            60,
            (),
            days=0,
            content="chiringuito",
            self_employed=True,
            training=Training("apertura", "la licencia de apertura del chiringuito", 15_000),
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
            45,
            Mechanic.MEMORY,
            30,
            (_good(3), Task("perfect", "Haz 6 rutas perfectas", "perfect_rounds", 6)),
            days=2,
            content="carteles:3",
        ),
        Position(
            2,
            "Concejal",
            225,
            Mechanic.MEMORY,
            30,
            (
                Task("perfect", "Vota 10 veces lo que diga el partido", "perfect_rounds", 10),
                _good(4),
            ),
            days=3,
            content="votos:3",
            training=Training("listas", "un puesto de salida en las listas", 2_000),
        ),
        Position(
            3,
            "Diputado autonómico",
            480,
            Mechanic.DIALOGUE,
            40,
            (Task("smooth", "Esquiva 15 preguntas en rueda de prensa", "smooth", 15), _good(5)),
            days=4,
            content="prensa",
        ),
        Position(
            4,
            "Ministro",
            975,
            Mechanic.DIALOGUE,
            60,
            (
                Task("smooth", "Sal vivo de 20 preguntas en comisión", "smooth", 20),
                _good(5),
                _events(3),
            ),
            days=5,
            content="comision",
            training=Training("cartera", "la cartera ministerial (cena en Ferraz)", 8_000),
        ),
        Position(
            5,
            "Consejero de una eléctrica",
            2_500,
            Mechanic.DIALOGUE,
            60,
            (),
            days=0,
            content="consejo",
            training=Training("giratoria", "el paso por la puerta giratoria", 25_000),
        ),
    ),
)

JOBS: tuple[Job, ...] = (OBRA, HOSTELERIA, POLITICA)
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
        "¿En qué orden iban?",
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
        "🗺️ El coordinador te dicta la ruta de hoy para pegar carteles:",
        "¿Por dónde ibas?",
        (
            ("🏫", "Colegio"),
            ("🏥", "Centro de salud"),
            ("⛪", "Iglesia"),
            ("🏟️", "Estadio"),
            ("🚏", "Parada de guagua"),
            ("🏪", "Bazar"),
            ("🌳", "Parque"),
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
    """Una situación con tres respuestas; la primera es la buena (se barajan al jugar)."""

    situation: str
    good: str
    bad: tuple[str, str]


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
    """

    key: str
    text: str
    options: tuple[tuple[str, Outcome], tuple[str, Outcome]]
    jobs: tuple[str, ...] = ()
    min_level: int = 1
    family_below: int = 0


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
}
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


def events_for(job: str, level: int, family: int) -> list[Event]:
    """Eventos que pueden salir con este oficio, puesto y familia."""
    return [
        event
        for event in EVENTS
        if (not event.jobs or job in event.jobs)
        and level >= event.min_level
        and (not event.family_below or family < event.family_below)
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
