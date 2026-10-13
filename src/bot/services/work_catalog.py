"""Catálogo del trabajo (`pala`): oficios, puestos, contenido de minijuegos y eventos.

Solo datos. Añadir un oficio es añadir un `Job` a `JOBS` con sus puestos (el
código admite los que haya), su contenido de minijuego (si usa uno nuevo) y, si
se quiere, sus eventos. Para la carrera larga, un oficio lleva un tronco de
puestos y sus ramas (`Branch`): todas con los mismos puestos, la primera sin
formación y cada una con su pensión; los puestos pueden dar una ventaja
(`Perk`) y los eventos pueden ser de una rama (`Event.branches`). Los `key`
(oficios, ramas, formaciones, tareas, eventos) no se cambian nunca: son lo que
se guarda en la base de datos.

Sueldos, en Y$ de nómina (100 Y$ por euro, ver
`taxes.WAGE_YAPDOLLARS_PER_EURO`): el puesto 1 de cada oficio paga entre 1.600 y
1.800 Y$ por turno con nota media, un poco más que un IMV entero (1.500 Y$), y
el puesto 5 entre 13.000 y 18.000 y lo más alto de una rama, entre 18.000 y
45.000. Un turno son unas dos horas: la jornada completa (4 turnos) de un
celador equivale a unos 25.000 € al año y la de un comisario europeo, a
438.000 €, con la retención que toca a cada uno. Las
formaciones cuestan de 2 a 4 turnos del puesto al que dan acceso. El análisis
que fija estas cifras está en `docs/economia-trabajo.md`.

La política es una sátira del PSOE, el partido del Gobierno, y de sus
tópicos (sobres, enchufes, comisiones, puertas giratorias, el Falcon, la
máquina del fango). La obra se ríe del ladrillo (recalificaciones, la
burbuja, los pisos turísticos) y de la obra pública (modificados, aeropuertos
sin aviones, el AVE a ninguna parte). La sanidad, de la precariedad con bata
(guardias, contratos de un día, camas en el pasillo) y, en sus ramas, de la
gestión pública (listas de espera, mascarillas a precio de oro, hospitales
inaugurados sin personal) y del negocio privado (letra pequeña, conciertos con
la Comunidad, fondos de inversión). La hostelería, de los contratos de cuatro horas
que son diez, el turismo de pulsera y la patronal (hotel) y de la alta cocina de
espumas, estrellas y Dubái. El reparto se ríe de las apps de reparto
y de los falsos autónomos; el funcionariado, de la burocracia (con las
preguntas del temario de verdad); el influencer, de la fama a golpes. Los
personajes del juego son genéricos; las figuras públicas (Feijóo, Puigdemont,
Trump, Erdoğan…) salen como en el resto del bot, con las fuentes de ironía de
la Biblia. No se nombra a ninguna persona que no sea figura pública ni a una
marca.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.work import (
    FLAT_RATE_FEE,
    Branch,
    Job,
    Mechanic,
    Perk,
    Position,
    Task,
    Training,
)

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
    blurb="De la pala al palco o al IBEX. Estable, duro y con jubilados mirando.",
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
            perk=Perk("Sabes leer el plano: +5 % de tiempo en el minijuego.", extra_time=0.05),
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
            perk=Perk("Bocadillo de las diez: cada turno cansa un 10 % menos.", battery=0.10),
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
            perk=Perk("Ojo de encargado: un fallo gratis por turno.", retry=True),
        ),
        Position(
            5,
            "Constructor",
            13_000,
            Mechanic.SPOT,
            60,
            (
                Task("caught", "Encuentra 25 partidas con sobrecoste", "caught", 25),
                _good(5),
                _events(3),
            ),
            days=4,
            content="sobrecostes",
            self_employed=True,
            training=Training("rea", "el alta en el Registro de Empresas Acreditadas", 50_000),
            perk=Perk(
                "Gestoría de confianza: una respuesta mala tachada en los diálogos.", fifty=True
            ),
        ),
    ),
    branches=(
        Branch(
            "ladrillo",
            "Ladrillo",
            "🏖️",
            "Promotora en la costa: pisos sobre plano, recalificaciones y la burbuja "
            "que siempre vuelve. Autónomo. Cobra más, se jubila peor.",
            (
                Position(
                    6,
                    "Promotor inmobiliario",
                    15_000,
                    Mechanic.MEMORY,
                    60,
                    (
                        Task(
                            "perfect",
                            "Enseña 15 pisos piloto tal como los pidió el cliente",
                            "perfect_rounds",
                            15,
                        ),
                        _good(5),
                        _events(2),
                    ),
                    days=5,
                    content="pisos:4",
                    self_employed=True,
                    perk=Perk(
                        "Oficina de ventas en primera línea: +5 % de tiempo.", extra_time=0.05
                    ),
                ),
                Position(
                    7,
                    "Urbanizador de la costa",
                    18_000,
                    Mechanic.SORT,
                    60,
                    (
                        Task("sorted", "Manda 30 terrenos a su sitio", "sorted", 30),
                        _good(6),
                        _events(3),
                    ),
                    days=5,
                    content="terrenos",
                    self_employed=True,
                    training=Training(
                        "costas", "el informe favorable de Costas (con mariscada)", 50_000
                    ),
                    perk=Perk(
                        "Todoterreno de promotor: cada turno cansa un 5 % menos.", battery=0.05
                    ),
                ),
                Position(
                    8,
                    "Rey del ladrillo",
                    21_500,
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Refinancia 25 deudas sin asustar al banco", "smooth", 25),
                        _good(6),
                        _events(3),
                    ),
                    days=6,
                    content="banco",
                    self_employed=True,
                    perk=Perk("Asesor fiscal en Andorra: un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9,
                    "Magnate inmobiliario",
                    26_000,
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Cierra 30 convenios con el alcalde", "smooth", 30),
                        _good(6),
                        _events(4),
                    ),
                    days=6,
                    content="alcalde",
                    self_employed=True,
                    training=Training(
                        "convenio", "un convenio urbanístico con el ayuntamiento", 75_000
                    ),
                    perk=Perk(
                        "El alcalde en favoritos: cada turno cansa un 5 % menos.", battery=0.05
                    ),
                ),
                Position(
                    10,
                    "Presidente de un club de fútbol",
                    32_000,
                    Mechanic.DIALOGUE,
                    60,
                    (),
                    days=0,
                    content="palco",
                    self_employed=True,
                    perk=Perk("Palco VIP con catering: +10 % de tiempo.", extra_time=0.10),
                ),
            ),
            pension=2_500,
            retired_as="promotor jubilado con el chalet embargado",
        ),
        Branch(
            "publica",
            "Obra pública",
            "🚄",
            "Gran constructora: túneles, licitaciones, modificados y ese AVE que para en "
            "mitad del campo. En nómina. Cobra menos, se jubila mejor.",
            (
                Position(
                    6,
                    "Jefe de obra de una gran constructora",
                    14_000,
                    Mechanic.DIG,
                    60,
                    (
                        _good(5),
                        Task("clean", "Haz 6 turnos de tuneladora sin romper nada", "clean", 6),
                        _events(2),
                    ),
                    days=5,
                    content="cavar:5",
                    perk=Perk(
                        "Caseta de obra con aire acondicionado: cada turno cansa un 5 % menos.",
                        battery=0.05,
                    ),
                ),
                Position(
                    7,
                    "Director de licitaciones",
                    15_500,
                    Mechanic.SORT,
                    60,
                    (
                        Task("sorted", "Manda 30 concursos a su sitio", "sorted", 30),
                        _good(6),
                        _events(3),
                    ),
                    days=5,
                    content="licitaciones",
                    training=Training(
                        "clasificacion", "la clasificación de contratista del Estado", 45_000
                    ),
                    perk=Perk(
                        "Los pliegos, de memoria: +5 % de tiempo en el minijuego.", extra_time=0.05
                    ),
                ),
                Position(
                    8,
                    "Director de concesiones",
                    17_000,
                    Mechanic.MEMORY,
                    60,
                    (
                        Task(
                            "perfect",
                            "Renueva 15 tandas de concesiones sin olvidar ninguna",
                            "perfect_rounds",
                            15,
                        ),
                        _good(6),
                        _events(3),
                    ),
                    days=6,
                    content="concesiones:4",
                    perk=Perk("Jefe de gabinete: un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9,
                    "Consejero delegado",
                    18_800,
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Saca 30 modificados en el ministerio", "smooth", 30),
                        _good(6),
                        _events(4),
                    ),
                    days=6,
                    content="fomento",
                    training=Training("ute", "la UTE con las otras cinco grandes", 55_000),
                    perk=Perk("Chófer de la empresa: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10,
                    "Presidente de una constructora del IBEX",
                    21_000,
                    Mechanic.DIALOGUE,
                    60,
                    (),
                    days=0,
                    content="internacional",
                    perk=Perk("Avión privado del consejo: +10 % de tiempo.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="expresidente de constructora con medalla al mérito civil",
        ),
    ),
)

HOSTELERIA = Job(
    key="hosteleria",
    name="Hostelería",
    emoji="🍽️",
    blurb="Del friegaplatos al hotel o a la estrella. Propinas en negro y reseñas de una estrella.",
    rgb=(46, 204, 113),
    positions=(
        Position(
            1, "Friegaplatos", 1_700, Mechanic.MEMORY, 30,
            (_good(3), Task("perfect", "Saca 6 tandas perfectas", "perfect_rounds", 6)),
            days=2, content="platos:3",
        ),
        Position(
            2, "Camarero", 2_500, Mechanic.MEMORY, 30, (_good(4), _extras(1)),
            days=3, content="comandas:3",
            training=Training("manipulador", "el carnet de manipulador de alimentos", 4_000),
            perk=Perk("Bandeja de tres pisos (y un pulso de cirujano): +5 % de tiempo en el "
                      "minijuego.", extra_time=0.05),
        ),
        Position(
            3, "Jefe de rango", 4_000, Mechanic.MEMORY, 40,
            (Task("perfect", "Saca 15 comandas perfectas", "perfect_rounds", 15), _good(5)),
            days=4, content="comandas:4",
            perk=Perk("Zapatillas antideslizantes (de las que usan en el hospital): cada turno "
                      "cansa un 10 % menos.", battery=0.10),
        ),
        Position(
            4, "Jefe de cocina", 7_000, Mechanic.MEMORY, 60,
            (Task("perfect", "Saca 20 comandas perfectas", "perfect_rounds", 20), _good(6),
             _events(2)),
            days=5, content="cocina:5",
            training=Training("alergenos", "el curso de alérgenos y APPCC", 12_000),
            perk=Perk("El pinche que te avisa antes de que se queme: un fallo gratis por turno.",
                      retry=True),
        ),
        Position(
            5, "Dueño del chiringuito", 13_000, Mechanic.DIALOGUE, 60,
            (Task("smooth", "Sobrevive a 25 clientes en el chiringuito", "smooth", 25),
             _good(6), _events(3)),
            days=4, content="chiringuito", self_employed=True,
            training=Training("apertura", "la licencia de apertura del chiringuito", 60_000),
            perk=Perk("Tu cuñado en la barra, que te sopla: una respuesta mala tachada en los "
                      "diálogos.", fifty=True),
        ),
    ),
    branches=(
        Branch(
            "hotel",
            "Hotelera",
            "🏨",
            "Benidorm, pulseras del todo incluido, balconing y tasa turística: de director de "
            "hotel a presidente de la patronal. En nómina. Cobra menos, se jubila mejor. "
            "«Canarias tiene un límite», tu cuenta de resultados no.",
            (
                Position(
                    6, "Director de hotel en primera línea", 14_000, Mechanic.MEMORY, 60,
                    (Task("perfect", "Reparte las pulseras de 15 huéspedes sin liarla",
                          "perfect_rounds", 15), _good(5), _events(2)),
                    days=5, content="pulseras:4",
                    perk=Perk("Carrito de golf para ir de la recepción al buffet: cada turno "
                              "cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7, "Director regional de una cadena hotelera", 15_500, Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 reseñas a su departamento", "sorted", 30),
                     _good(6), _events(3)),
                    days=5, content="quejas",
                    training=Training(
                        "master_hotelero",
                        "el máster en Dirección Hotelera (en inglés, en Suiza y con tu "
                        "madre pagando)", 40_000,
                    ),
                    perk=Perk("Tarjeta de socio de la cadena, con sala VIP en el aeropuerto: "
                              "+5 % de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    8, "Director de resorts en el Caribe", 17_000, Mechanic.SPOT, 60,
                    (Task("caught", "Pilla a 25 colados en el buffet", "caught", 25),
                     _good(6), _events(3)),
                    days=6, content="colados",
                    perk=Perk("Un jefe de recepción que te cubre las espaldas (y los check-in): "
                              "un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9, "Consejero delegado de una cadena hotelera", 19_000, Mechanic.DIALOGUE,
                    60,
                    (Task("smooth", "Sobrevive a 30 juntas con los inversores", "smooth", 30),
                     _good(6), _events(4)),
                    days=6, content="cadena_hotelera",
                    training=Training(
                        "familia_fundadora",
                        "la confianza de la familia fundadora (que te cuesta una cena de "
                        "Nochebuena con el patriarca y un anillo de compromiso)", 50_000,
                    ),
                    perk=Perk("Helicóptero para llegar a la junta sin pisar la autovía: cada "
                              "turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10, "Presidente de la patronal turística", 21_000, Mechanic.DIALOGUE, 60, (),
                    days=0, content="patronal",
                    perk=Perk("El Falcon del presidente, «en comisión de servicio»: +10 % de "
                              "tiempo en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="expresidente de la patronal con suite vitalicia en primera línea",
        ),
        Branch(
            "estrella",
            "Alta cocina",
            "⭐",
            "Espumas, deconstrucciones y un menú degustación de 300 €: del restaurante propio "
            "al gurú con franquicias en Dubái. Autónomo. Cobra más, se jubila peor.",
            (
                Position(
                    6, "Chef con restaurante propio", 15_000, Mechanic.MEMORY, 60,
                    (Task("perfect", "Emplata 15 platos tal como mandaba la carta",
                          "perfect_rounds", 15), _good(5), _events(2)),
                    days=5, content="emplatado:4", self_employed=True,
                    perk=Perk("Pinzas de emplatar de titanio (y un pulso que no tiembla): +5 % "
                              "de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    7, "Chef con estrella", 19_000, Mechanic.SPOT, 60,
                    (Task("caught", "Pilla a 25 inspectores de la Guía", "caught", 25),
                     _good(6), _events(3)),
                    days=5, content="inspector", self_employed=True,
                    training=Training(
                        "stage_tres_estrellas",
                        "un stage gratis de seis meses en un tres estrellas (fregando "
                        "cacerolas, pero con el apellido del chef en el CV)", 55_000,
                    ),
                    perk=Perk("Un sumiller que te susurra por el pinganillo: un fallo gratis "
                              "por turno.", retry=True),
                ),
                Position(
                    8, "Chef de la tele", 23_000, Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Aguanta 25 galas del talent de cocina", "smooth", 25),
                     _good(6), _events(3)),
                    days=6, content="plato_tv", self_employed=True,
                    perk=Perk("Maquilladora de plató (te quita las ojeras de las grabaciones): "
                              "cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    9, "Dueño de un grupo de restaurantes", 28_000, Mechanic.SORT, 60,
                    (Task("sorted", "Manda a 30 cocineros a su partida", "sorted", 30),
                     _good(6), _events(4)),
                    days=6, content="brigada", self_employed=True,
                    training=Training(
                        "socio_inversor",
                        "la entrada de un fondo y de un socio con dinero de procedencia "
                        "«digamos, dudosa»", 70_000,
                    ),
                    perk=Perk("Un maître con cara de póquer que gestiona las crisis: +5 % de "
                              "tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    10, "Gurú gastronómico con franquicias en Dubái", 34_000, Mechanic.DIALOGUE,
                    60, (),
                    days=0, content="dubai", self_employed=True,
                    perk=Perk("Jet privado con cocina de inducción: +10 % de tiempo en el "
                              "minijuego.", extra_time=0.10),
                ),
            ),
            pension=2_500,
            retired_as="gurú jubilado que cobra en pan de oro (y la pensión no da ni para el pan)",
        ),
    ),
)  # fmt: skip

POLITICA = Job(
    key="politica",
    name="Política (PSOE)",
    emoji="🌹",
    blurb="Del pegacarteles a la Moncloa o a Bruselas. Sobres incluidos.",
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
            perk=Perk("Te sabes el argumentario: +5 % de tiempo en el minijuego.", extra_time=0.05),
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
            perk=Perk("Coche oficial: cada turno cansa un 10 % menos.", battery=0.10),
        ),
        Position(
            4,
            "Diputado en el Congreso",
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
            # Clave heredada de cuando el puesto 4 era ministro: quien ya la
            # compró no paga dos veces.
            training=Training("cartera", "el escaño en el Congreso (cena en Ferraz)", 30_000),
            perk=Perk("Asesores de confianza: un fallo gratis por turno.", retry=True),
        ),
        Position(
            5,
            "Portavoz del grupo parlamentario",
            13_000,
            Mechanic.DIALOGUE,
            60,
            (
                Task("smooth", "Contesta 25 preguntas sin contestar nada", "smooth", 25),
                _good(5),
                _events(3),
            ),
            days=4,
            content="portavoz",
            training=Training("portavoz", "el móvil de portavoz (nunca se apaga)", 40_000),
            perk=Perk(
                "Argumentario de Ferraz memorizado: una respuesta mala tachada en los diálogos.",
                fifty=True,
            ),
        ),
    ),
    branches=(
        Branch(
            "moncloa",
            "Moncloa",
            "🏛️",
            "Consejos de ministros, socios que chantajean y, al final, la puerta "
            "giratoria. Cobra menos, se jubila mejor.",
            (
                Position(
                    6,
                    "Secretario de Estado",
                    14_200,
                    Mechanic.SORT,
                    60,
                    (
                        Task("sorted", "Despacha 30 papeles al cajón correcto", "sorted", 30),
                        _good(5),
                        _events(2),
                    ),
                    days=5,
                    content="decretos",
                    perk=Perk("Chófer oficial: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7,
                    "Ministro",
                    15_500,
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Sal airoso de 25 marrones del ministerio", "smooth", 25),
                        _good(6),
                        _events(3),
                    ),
                    days=5,
                    content="ministerio",
                    training=Training(
                        "ministerio", "la cartera ministerial (otra cena en Ferraz)", 45_000
                    ),
                    perk=Perk(
                        "Gabinete de prensa: +5 % de tiempo en el minijuego.", extra_time=0.05
                    ),
                ),
                Position(
                    8,
                    "Vicepresidente del Gobierno",
                    16_900,
                    Mechanic.MEMORY,
                    60,
                    (
                        Task(
                            "perfect",
                            "Recuerda 15 veces lo que le prometiste a cada socio",
                            "perfect_rounds",
                            15,
                        ),
                        _good(6),
                        _events(3),
                    ),
                    days=6,
                    content="socios:4",
                    perk=Perk("Falcon a demanda: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    9,
                    "Presidente del Gobierno",
                    18_400,
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task(
                            "smooth",
                            "Sobrevive a 30 preguntas en la sesión de control",
                            "smooth",
                            30,
                        ),
                        _good(6),
                        _events(4),
                    ),
                    days=6,
                    content="control",
                    training=Training("investidura", "una investidura (pactos incluidos)", 55_000),
                    perk=Perk("Manual de resistencia: otro fallo gratis por turno.", retry=True),
                ),
                Position(
                    10,
                    "Consejero de una eléctrica",
                    20_000,
                    Mechanic.DIALOGUE,
                    60,
                    (),
                    days=0,
                    content="consejo",
                    # Clave heredada del antiguo puesto 5: quien ya la compró la conserva.
                    training=Training("giratoria", "el paso por la puerta giratoria", 70_000),
                    perk=Perk("El sillón ya está caliente: +10 % de tiempo.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="expresidente con sillón vitalicio en la eléctrica",
        ),
        Branch(
            "bruselas",
            "Bruselas",
            "🇪🇺",
            "Eurocámara: dietas, lobbies, trílogos y Estrasburgo una vez al mes. Lejos de "
            "la UCO, cerca de la OLAF. Cobra más, se jubila peor.",
            (
                Position(
                    6,
                    "Eurodiputado",
                    15_400,
                    Mechanic.MEMORY,
                    60,
                    (
                        Task("perfect", "Vota bien 15 tandas del pleno", "perfect_rounds", 15),
                        _good(5),
                        _events(2),
                    ),
                    days=5,
                    content="plenos:4",
                    perk=Perk(
                        "Dietas de Estrasburgo: cada turno cansa un 5 % menos.", battery=0.05
                    ),
                ),
                Position(
                    7,
                    "Ponente de un reglamento",
                    18_200,
                    Mechanic.SORT,
                    60,
                    (
                        Task("sorted", "Manda 30 enmiendas a su sitio", "sorted", 30),
                        _good(6),
                        _events(3),
                    ),
                    days=5,
                    content="enmiendas",
                    training=Training(
                        "ponencia", "la ponencia de un reglamento (te la cede tu grupo)", 50_000
                    ),
                    perk=Perk(
                        "Intérpretes en cabina: +5 % de tiempo en el minijuego.", extra_time=0.05
                    ),
                ),
                Position(
                    8,
                    "Jefe de delegación",
                    21_500,
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Torea a 25 lobistas sin firmar nada", "smooth", 25),
                        _good(6),
                        _events(3),
                    ),
                    days=6,
                    content="lobbies",
                    perk=Perk("Asistente acreditado: un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9,
                    "Vicepresidente del Parlamento Europeo",
                    25_400,
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Cierra 30 puntos de un trílogo", "smooth", 30),
                        _good(6),
                        _events(4),
                    ),
                    days=6,
                    content="trilogo",
                    training=Training(
                        "vicepresidencia",
                        "el apoyo de los grandes grupos para la vicepresidencia",
                        75_000,
                    ),
                    perk=Perk(
                        "Coche con chófer en Bruselas: cada turno cansa un 5 % menos.",
                        battery=0.05,
                    ),
                ),
                Position(
                    10,
                    "Comisario europeo",
                    30_000,
                    Mechanic.DIALOGUE,
                    60,
                    (),
                    days=0,
                    content="comisario",
                    perk=Perk("Gabinete de veinte personas: +10 % de tiempo.", extra_time=0.10),
                ),
            ),
            pension=3_000,
            retired_as="eurócrata jubilado con pensión comunitaria",
        ),
    ),
)

SANIDAD = Job(
    key="sanidad",
    name="Sanidad",
    emoji="🏥",
    blurb="Del celador a la Consejería o a la aseguradora. Aquí se rinde haciendo guardias.",
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
            perk=Perk("Zuecos de goma: +5 % de tiempo en el minijuego.", extra_time=0.05),
        ),
        Position(
            3, "Enfermero", 4_800, Mechanic.SPOT, 40,
            (Task("caught", "Acierta 20 triajes", "caught", 20), _guards(4)),
            days=4, content="triaje",
            training=Training("enfermeria", "el grado en Enfermería", 20_000),
            perk=Perk(
                "Café de la máquina del control: cada turno cansa un 10 % menos.", battery=0.10
            ),
        ),
        Position(
            4, "Médico residente (MIR)", 8_000, Mechanic.DIALOGUE, 60,
            (Task("smooth", "Acierta 25 preguntas del MIR", "smooth", 25), _guards(6),
             _events(2)),
            days=5, content="mir",
            training=Training("mir", "la academia del MIR (y aprobar el examen)", 30_000),
            perk=Perk("El Harrison, subrayado entero: un fallo gratis por turno.", retry=True),
        ),
        Position(
            5, "Médico adjunto", 14_000, Mechanic.DIALOGUE, 60,
            (Task("smooth", "Pasa 25 consultas sin una sola reclamación", "smooth", 25),
             _guards(6), _events(3)),
            days=4, content="consulta",
            training=Training("ope", "la plaza de adjunto (oposición, OPE)", 60_000),
            perk=Perk(
                "El residente te sopla: una respuesta mala tachada en los diálogos.", fifty=True
            ),
        ),
    ),
    branches=(
        Branch(
            "publica",
            "Sanidad pública",
            "🏛️",
            "Del jefe de sección a la Consejería: listas de espera, camas en el pasillo y "
            "hospitales inaugurados sin personal. En nómina. Cobra menos, se jubila mejor.",
            (
                Position(
                    6, "Jefe de sección", 15_000, Mechanic.MEMORY, 60,
                    (Task("perfect", "Cuadra 15 partes de quirófano sin tachones",
                          "perfect_rounds", 15), _guards(6), _events(2)),
                    days=5, content="quirofano:4",
                    perk=Perk(
                        "Despacho con ventana (al patio de luces): cada turno cansa un 5 % "
                        "menos.", battery=0.05,
                    ),
                ),
                Position(
                    7, "Jefe de servicio", 16_500, Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 pacientes a su sitio", "sorted", 30),
                     _guards(6), _events(3)),
                    days=5, content="derivaciones",
                    training=Training(
                        "jefatura",
                        "la jefatura de servicio por concurso de méritos (y un café con el "
                        "gerente)", 45_000,
                    ),
                    perk=Perk(
                        "Secretaria que te guarda la agenda: +5 % de tiempo en el minijuego.",
                        extra_time=0.05,
                    ),
                ),
                Position(
                    8, "Director médico", 18_000, Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Apaga 25 incendios en la junta técnico-asistencial",
                          "smooth", 25), _good(6), _events(3)),
                    days=6, content="direccion",
                    perk=Perk("Un jefe de guardia que no te llama: un fallo gratis por turno.",
                              retry=True),
                ),
                Position(
                    9, "Gerente del hospital", 20_000, Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Sobrevive a 30 reuniones con la Consejería", "smooth", 30),
                     _good(6), _events(4)),
                    days=6, content="gerencia",
                    training=Training(
                        "gerencia",
                        "el nombramiento de gerente (libre designación, del partido que toque)",
                        55_000,
                    ),
                    perk=Perk("Plaza de garaje en el hospital: cada turno cansa un 5 % menos.",
                              battery=0.05),
                ),
                Position(
                    10, "Consejero de Sanidad", 22_000, Mechanic.DIALOGUE, 60, (),
                    days=0, content="consejeria",
                    perk=Perk("Coche oficial con sirena (de adorno): +10 % de tiempo.",
                              extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="exconsejero de Sanidad con sillón en una aseguradora",
        ),
        Branch(
            "privada",
            "Privada",
            "💉",
            "Mutuas, retoques y conciertos con la Comunidad: la salud como modelo de negocio. "
            "Autónomo. Cobra más, se jubila peor.",
            (
                Position(
                    6, "Médico de mutua", 15_500, Mechanic.SPOT, 60,
                    (Task("caught", "Encuentra 25 cláusulas de letra pequeña", "caught", 25),
                     _good(5), _events(2)),
                    days=5, content="letra_pequena", self_employed=True,
                    perk=Perk(
                        "Consultas de diez minutos (el doble que en la pública): cada turno "
                        "cansa un 5 % menos.", battery=0.05,
                    ),
                ),
                Position(
                    7, "Cirujano estético", 19_000, Mechanic.MEMORY, 60,
                    (Task("perfect", "Haz 15 retoques tal como los pidió el cliente",
                          "perfect_rounds", 15), _good(6), _events(3)),
                    days=5, content="retoques:4", self_employed=True,
                    training=Training(
                        "estetica",
                        "el máster en Medicina Estética (un fin de semana en Marbella)",
                        50_000,
                    ),
                    perk=Perk("Luz de quirófano favorecedora: +5 % de tiempo en el minijuego.",
                              extra_time=0.05),
                ),
                Position(
                    8, "Director de una clínica privada", 23_000, Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Atiende a 25 clientes VIP sin perder ninguno", "smooth",
                          25), _good(6), _events(3)),
                    days=6, content="clinica", self_employed=True,
                    perk=Perk("Recepcionista que lo filtra todo: un fallo gratis por turno.",
                              retry=True),
                ),
                Position(
                    9, "Consejero delegado de un grupo hospitalario", 28_000, Mechanic.DIALOGUE,
                    60,
                    (Task("smooth", "Cierra 30 conciertos con la Comunidad", "smooth", 30),
                     _good(6), _events(4)),
                    days=6, content="conciertos", self_employed=True,
                    training=Training(
                        "fondo_salud",
                        "la entrada de un fondo de inversión en el accionariado", 75_000,
                    ),
                    perk=Perk("Helipuerto en la azotea: cada turno cansa un 5 % menos.",
                              battery=0.05),
                ),
                Position(
                    10, "Dueño de una aseguradora de salud", 35_000, Mechanic.DIALOGUE, 60, (),
                    days=0, content="aseguradora", self_employed=True,
                    perk=Perk("Póliza de oro para ti (sin carencias): +10 % de tiempo.",
                              extra_time=0.10),
                ),
            ),
            pension=2_500,
            retired_as="dueño jubilado de aseguradora, con su propia póliza denegada",
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
            ("🗿", "Pedestal sin estatua (Ley de Memoria)"),
            ("🚌", "Guagua del IMSERSO"),
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
            Dialogue(
                "¿Le ha llamado Puigdemont desde Waterloo?",
                "Hablamos con todas las fuerzas democráticas por la convivencia.",
                ("Sí, desde el maletero de un coche.", "Cada noche, para darme las buenas noches."),
            ),
            Dialogue(
                "Ayuso dice que su ley es comunismo. ¿Qué responde?",
                "Nosotros, a lo nuestro: gobernar para la mayoría social.",
                ("Que tiene razón: la copiamos de Cuba.", "Que a mí también me gusta la fruta."),
            ),
            Dialogue(
                "¿Es verdad que tenía Pegasus en el móvil?",
                "La seguridad del Estado no se comenta en una rueda de prensa.",
                (
                    "Sí, y me escuchan cantar en la ducha.",
                    "Lo tenía, pero me he pasado a un Nokia.",
                ),
            ),
            Dialogue(
                "Feijóo dice que usted miente más que habla.",
                "La oposición vive instalada en el ruido y en la máquina del fango.",
                ("Es que hablo poco.", "Un vaso es un vaso, y una mentira es una mentira."),
            ),
            Dialogue(
                "¿Qué opina de los aranceles de Trump al aceite?",
                "Defenderemos a nuestros sectores productivos de la mano de Europa.",
                ("Que nos hunde el olivar.", "Que le mando papas arrugadas a ver si se ablanda."),
            ),
            Dialogue(
                "¿En qué se han gastado los fondos europeos?",
                "Es la mayor transformación de nuestra economía en décadas.",
                ("¿Qué? ¿Qué? ¡Toma mangó!", "En un PERTE de cosas variadas."),
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
            Dialogue(
                "¿Le mandó usted mensajes de ánimo al tesorero?",
                "No me consta haber enviado mensajes de esa naturaleza.",
                ("Sí: «Sé fuerte».", "Le mandé un sticker de un gatito con casco."),
            ),
            Dialogue(
                "¿Qué hacía de madrugada en el aeropuerto con una vicepresidenta venezolana?",
                "Fue un encuentro fortuito en una zona de tránsito, señoría.",
                ("Que no pisara suelo español, técnicamente.", "Traía unas maletas; no pregunté."),
            ),
            Dialogue(
                "¿Sabe usted lo que es una caja B?",
                "Conozco la contabilidad del partido, que está auditada y es ejemplar.",
                ("La que va debajo de la caja A.", "La llevaba en el maletero, como todo."),
            ),
            Dialogue(
                "¿Qué recuerda de aquella cena con el comisionista?",
                "Señoría, recuerdo que se habló de fútbol. Nada más.",
                ("Que pidió café… ¡Quiero café!", "Que pagó él, en chistorras."),
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
            Dialogue(
                "Un consejero pregunta si las placas solares nos quitan el negocio.",
                "Diversificamos: cobramos el sol al precio del gas.",
                ("Pues sí, el sol no se puede facturar.", "Propongo bajar la luz por solidaridad."),
            ),
            Dialogue(
                "Trump amenaza con aranceles al gas europeo.",
                "Una gran oportunidad para revisar las tarifas al alza.",
                ("Habrá que bajar márgenes.", "Le llamamos y le pedimos perdón en inglés."),
            ),
            Dialogue(
                "Llega al consejo un exministro, recién salido del Gobierno.",
                "Bienvenido: aquí tu experiencia vale oro, y tu agenda, más.",
                (
                    "¿Y tú qué sabes de electricidad?",
                    "Pedimos antes un informe de incompatibilidades.",
                ),
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
    "decretos": SortPack(
        "📜 Eres secretario de Estado. Cada papel de la mesa va a su sitio: decreto, "
        "moneda de cambio, filtración o cajón.",
        "🗂️ Papel {n}/{total}:",
        (
            "📜 Real Decreto-ley",
            "🤝 Moneda de cambio con socios",
            "📰 Filtrar a la prensa",
            "🗄️ Al cajón",
        ),
        (
            ("Una medida urgente que hay que aprobar el martes sin pasar por comisión", 0),
            ("Una prórroga de ayudas que caduca el día 31", 0),
            ("Un cambio fiscal que no puede esperar al trámite parlamentario", 0),
            ("Un traspaso de competencias que pide un partido de la investidura", 1),
            ("Una partida para las infraestructuras de un socio", 1),
            ("Una comisión bilateral que un grupo exige para apoyar los presupuestos", 1),
            ("Un indulto que sirve para asegurar siete votos", 1),
            ("El borrador de una subida de impuestos, para ver cómo reacciona la gente", 2),
            ("La lista de nombres de la próxima remodelación del Gobierno", 2),
            ("Un informe incómodo sobre el ministro de al lado", 2),
            ("Un dato favorable del paro que conviene que salga hoy", 2),
            ("Una propuesta de reforma de hace diez legislaturas que nadie ha vuelto a abrir", 3),
            ("El informe que dice que el plan estrella no funciona", 3),
            ("Una iniciativa popular con medio millón de firmas", 3),
            ("Una promesa electoral de 2019 que ya nadie recuerda", 3),
            ("Una queja del Tribunal de Cuentas que «ya se verá»", 3),
            ("Una ley de transparencia que nunca llegó a la mesa", 3),
            ("Un informe de la UCO que salpica a tu jefe de gabinete", 3),
            ("La respuesta a los aranceles de Trump, antes de que abran las bolsas", 0),
            ("La amnistía que piden siete votos desde Waterloo", 1),
            ("La fecha de una exhumación, justo antes de las elecciones", 2),
        ),
    ),
    "enmiendas": SortPack(
        "🇪🇺 Eres ponente de un reglamento. Cada enmienda va a su sitio: aceptar, "
        "transaccional, rechazar o inadmisible.",
        "✏️ Enmienda {n}/{total}:",
        ("✅ Aceptar", "🔀 Transaccional", "❌ Rechazar", "🗑️ Inadmisible"),
        (
            ("Corrige una errata de numeración en el artículo 12", 0),
            ("Añade un «a más tardar» que todos los grupos piden", 0),
            ("Precisa una definición que ya nadie discute", 0),
            ("Aclara que la fecha del artículo 3 se refiere a días hábiles", 0),
            ("Pide fusionar dos propuestas de redacción casi idénticas", 1),
            ("Quiere cambiar «obligatorio» por «recomendado», pero con matices", 1),
            ("Propone una cifra intermedia entre las dos enmiendas enfrentadas", 1),
            ("Combina la idea de dos grupos en un único texto de compromiso", 1),
            ("Elimina todo el reglamento y propone no hacer nada", 2),
            ("Exige multiplicar por diez las sanciones", 2),
            ("Pide que el reglamento no se aplique a ningún país", 2),
            ("Duplica los costes de aplicación sin explicar de dónde sale el dinero", 2),
            ("Introduce un tema que no tiene nada que ver con el reglamento", 3),
            ("Modifica un artículo de otro reglamento ya aprobado", 3),
            ("Contradice un artículo del Tratado de Funcionamiento de la UE", 3),
            ("Llega fuera de plazo, entregada en mano a las tres de la mañana", 3),
            ("Propone legislar sobre una competencia que es solo de los Estados miembros", 3),
            ("Obliga a aceptar las cookies dos veces, por si acaso", 2),
            ("Rebautiza el reglamento de pesca como «Ley Eurovisión»", 3),
            ("Une la propuesta del tapón pegado y la de los bricks en un solo artículo", 1),
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
        "socios": MemoryPack(
            "🤝 Cena con los socios de investidura. Esto es lo que les prometiste, por "
            "orden (ronda {n}):",
            "Recuerda en el mismo orden lo que prometiste a cada socio.",
            (
                ("🚆", "Un tren"),
                ("💶", "Transferencias"),
                ("🏛️", "Una comisión"),
                ("📜", "Un indulto"),
                ("🌉", "Un puente"),
                ("🏥", "Un hospital"),
                ("🗣️", "Un traspaso de competencias"),
                ("🚧", "Una autovía"),
                ("🎫", "Una cartera"),
                ("🧳", "Un viaje a Waterloo"),
                ("🕊️", "Una amnistía"),
                ("🚂", "El Cercanías transferido"),
            ),
        ),
        "plenos": MemoryPack(
            "🇪🇺 Pleno en Estrasburgo. Esta es la secuencia de votaciones de hoy "
            "(tanda {n}):",
            "Vota en el mismo orden que la lista de votación.",
            (
                ("👍", "A favor"),
                ("👎", "En contra"),
                ("✋", "Abstención"),
                ("✏️", "Enmienda"),
                ("✂️", "Votación por partes"),
                ("🔒", "Votación secreta"),
                ("🙋", "Votación nominal"),
            ),
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
        "portavoz": DialoguePack(
            "📣 Portavoz en el Congreso. Regla de oro: **contesta sin contestar nada.**",
            (
                Dialogue(
                    "Señoría, ¿va a explicar por fin las cuentas del partido?",
                    "Lo que importa es que este Gobierno cumple y la oposición solo grita.",
                    ("Sí, pero tengo que buscar el Excel.", "Esas cuentas las lleva el tesorero."),
                ),
                Dialogue(
                    "¿Piensa su grupo apoyar la ley que ayer criticaba?",
                    "Evolucionamos con los tiempos; la rigidez es cosa de otros.",
                    ("No, qué va, ya está bien.", "Si me dan algo a cambio, quizá."),
                ),
                Dialogue(
                    "¿Por qué no comparece el presidente para dar explicaciones?",
                    "El presidente comparece cuando toca, y la agenda la marca España.",
                    ("Porque le da miedo.", "Está de viaje, como siempre."),
                ),
                Dialogue(
                    "¿Qué le parece el último escándalo de su partido?",
                    "Mi grupo trabaja por la gente mientras otros viven de la bronca.",
                    ("Un escándalo, sí.", "Yo no sabía nada, lo leí en el periódico."),
                ),
                Dialogue(
                    "¿Se compromete a bajar los impuestos?",
                    "Nos comprometemos con la justicia fiscal y con quien más lo necesita.",
                    ("No, los vamos a subir.", "Sí, a ver si cuela."),
                ),
                Dialogue(
                    "¿Admite que el Gobierno ha maquillado las cifras de empleo?",
                    "Las cifras son las mejores de nuestra historia reciente.",
                    ("Maquillar es una palabra fuerte, pero sí.", "Eso lo dirá la oposición."),
                ),
                Dialogue(
                    "¿Va a pedir perdón a las víctimas del desastre?",
                    "Nuestra prioridad es la reconstrucción y mirar hacia adelante.",
                    ("Sí, pero tarde.", "No, ya pidieron perdón los de antes."),
                ),
                Dialogue(
                    "¿Votará su grupo a favor de la enmienda de los socios?",
                    "Estamos dialogando con todos los grupos desde la responsabilidad.",
                    ("Sí, nos tienen cogidos.", "No sé qué enmienda es."),
                ),
                Dialogue(
                    "¿Quién ha filtrado la conversación privada del ministro?",
                    "No vamos a entrar en cuestiones que están en manos de los tribunales.",
                    ("Fue un asesor, seguro.", "Yo, para ver qué pasaba."),
                ),
                Dialogue(
                    "¿Cuántos cargos de confianza ha colocado su partido este año?",
                    "Los que exige una administración moderna y comprometida.",
                    ("Unos trescientos, aproximadamente.", "Todos los primos que había libres."),
                ),
                Dialogue(
                    "¿Por qué ha cambiado de postura sobre el referéndum?",
                    "Porque la realidad es dinámica y la política, también.",
                    ("Por los votos.", "No he cambiado: la que cambió fue mi memoria."),
                ),
                Dialogue(
                    "¿Han cambiado la amnistía por la investidura?",
                    "Hemos apostado por la convivencia y por pasar página.",
                    (
                        "Sí, y ahora me toca explicarlo.",
                        "La amnistía se pagó en Waterloo, en efectivo.",
                    ),
                ),
                Dialogue(
                    "Vox ha presentado una moción montado a caballo. ¿Qué opina?",
                    "Con todo respeto, la derecha y la ultraderecha cada día se parecen más.",
                    (
                        "Si viene a caballo, por lo menos la leo.",
                        "Que se traigan también la Reconquista.",
                    ),
                ),
                Dialogue(
                    "¿Qué le parece lo último que ha dicho Yolanda Díaz?",
                    "En el Gobierno de coalición hay diversidad y una sola dirección.",
                    (
                        "No lo he entendido, pero sonreía mucho.",
                        "Que monte otro partido, que ya toca.",
                    ),
                ),
                Dialogue(
                    "¿Qué opina de Milei y su motosierra?",
                    "Respetamos a todos los gobiernos elegidos democráticamente.",
                    ("Que me la preste para los ministerios.", "Que se corte antes el pelo."),
                ),
            ),
        ),
        "ministerio": DialoguePack(
            "🗂️ Despacho del ministro. Regla de oro: **todo es una oportunidad, nada es "
            "culpa tuya y las filtraciones vienen de fuera.**",
            (
                Dialogue(
                    "Se ha filtrado el borrador de la ley a un periódico.",
                    "Pedir una investigación interna... que no acabe nunca.",
                    ("Admitir que lo filtraste tú.", "Echarle la culpa al becario por escrito."),
                ),
                Dialogue(
                    "El presupuesto de la obra se ha disparado un 60 %.",
                    "Hablar de «ajuste técnico» y de «mayor inversión en seguridad».",
                    ("Decir que nadie sabe sumar.", "Cancelar la obra y no decir nada."),
                ),
                Dialogue(
                    "Un socio de coalición amenaza con romper si no tiene su cartera.",
                    "Un café largo y una promesa vaga para después de las elecciones.",
                    ("Dárselo todo a la primera.", "Mandarlo a freír espárragos por Telegram."),
                ),
                Dialogue(
                    "La prensa pregunta por el sobrecoste del coche oficial.",
                    "Remitir al portal de transparencia, que nadie ha abierto nunca.",
                    ("Reconocer que fue un capricho.", "Decir que se compró barato."),
                ),
                Dialogue(
                    "Estalla una crisis con un país vecino por un comentario tuyo.",
                    "Emitir un comunicado de «máximo respeto y relaciones históricas».",
                    ("Repetir el comentario más alto.", "Desaparecer una semana."),
                ),
                Dialogue(
                    "Un alto cargo del ministerio ha cobrado dos sueldos.",
                    "Abrir un expediente informativo y pasar al siguiente tema.",
                    ("Aplaudir su iniciativa.", "Pedirle que devuelva el dinero por Bizum."),
                ),
                Dialogue(
                    "Los sindicatos convocan huelga del sector.",
                    "Convocar una mesa de diálogo con fecha para dentro de seis meses.",
                    ("Subir el sueldo de golpe.", "Reírte en directo."),
                ),
                Dialogue(
                    "Te piden cuentas por un contrato a dedo.",
                    "Aclarar que se ajustó al procedimiento de emergencia.",
                    ("Decir que era un amigo.", "Asegurar que fue un error de la fotocopiadora."),
                ),
                Dialogue(
                    "Un informe interno dice que el plan estrella no funciona.",
                    "Encargar otro informe que diga lo contrario.",
                    ("Publicarlo entero.", "Quemar el único ejemplar delante de testigos."),
                ),
                Dialogue(
                    "Toca presentar un recorte y todos los grupos se te echan encima.",
                    "Llamarlo «optimización del gasto» y repetirlo cinco veces.",
                    ("Pedir perdón a todo el mundo.", "Culpar a la herencia de Felipe II."),
                ),
                Dialogue(
                    "Toca inaugurar un pantano y no hay pantano.",
                    "Inauguras la primera piedra; el agua ya vendrá.",
                    ("Cancelas el acto.", "Inauguras un charco y lo graba el NO-DO."),
                ),
                Dialogue(
                    "Bruselas pide justificar los fondos Next Generation.",
                    "Mandas un Excel de 4.000 filas con la palabra «resiliencia».",
                    ("Reconoces que se fueron en asesores.", "Mandas una postal de Canarias."),
                ),
                Dialogue(
                    "Tu jefe de gabinete sale en un audio con un comisionista.",
                    "Lo cesas «a petición propia» y agradeces los servicios prestados.",
                    ("Lo ascienes a secretario de Estado.", "Pides que te pasen el audio entero."),
                ),
            ),
        ),
        "control": DialoguePack(
            "🏛️ Sesión de control, miércoles. Regla de oro: **nunca contestes a la "
            "pregunta; devuelve la pelota con un «¿y usted más?»**",
            (
                Dialogue(
                    "Presidente, ¿cuándo convocará elecciones?",
                    "Cuando toque, y mientras tanto usted gobernó con corrupción.",
                    ("Pronto, si me deja.", "El mes que viene, creo."),
                ),
                Dialogue(
                    "¿Sabía usted lo de las comisiones de su exsecretario?",
                    "Y usted, ¿sabía lo de su tesorero, señoría?",
                    ("Algo me sonaba.", "Me enteré el mismo día que usted."),
                ),
                Dialogue(
                    "¿Por qué sube el precio de la luz?",
                    "Gobernaron ocho años y dejaron la factura más cara de Europa.",
                    ("Por el mercado marginalista, que lo explico.", "No tengo ni idea."),
                ),
                Dialogue(
                    "¿Ha pagado usted ese viaje de su bolsillo?",
                    "Hablemos de los viajes que pagaron ustedes en su día.",
                    ("Pagó el partido.", "Sí, todo en metálico."),
                ),
                Dialogue(
                    "¿Va a rectificar la política de vivienda?",
                    "Su partido vendió pisos a fondos buitre; nosotros construimos.",
                    ("Sí, rectifico hoy mismo.", "La vivienda no es mi competencia."),
                ),
                Dialogue(
                    "¿Respeta usted las sentencias del Supremo?",
                    "Respeto las sentencias, y ustedes, ¿respetan las urnas?",
                    ("Depende de la sentencia.", "No las leo, pero me las resumen."),
                ),
                Dialogue(
                    "Su ministro ha mentido al Congreso.",
                    "Más mintió su líder prometiendo bajar impuestos.",
                    ("Es verdad, y me da vergüenza.", "Lo he sabido siempre."),
                ),
                Dialogue(
                    "¿Por qué no hay presupuestos?",
                    "Porque ustedes bloquean todo, como siempre.",
                    ("Porque no los he hecho.", "Los hice, pero se me perdieron."),
                ),
                Dialogue(
                    "¿Qué piensa de la última encuesta?",
                    "Las encuestas las ganan ustedes; las elecciones, nosotros.",
                    ("Pinta muy mal, la verdad.", "La encuesta la hice yo, ya sabe."),
                ),
                Dialogue(
                    "¿Cuántos asesores hay en la Moncloa?",
                    "Los mismos que cuando gobernaban ustedes, pero más baratos.",
                    ("Unos mil trescientos.", "No me los sé, los contrato por grupos."),
                ),
                Dialogue(
                    "¿Se arrepiente de haber pactado con quien dijo que no pactaría?",
                    "Y usted, ¿se arrepiente de haber hecho lo mismo en tres autonomías?",
                    ("Un poco, sí.", "Qué va, me lo recomendaron."),
                ),
                Dialogue(
                    "Presidente, ¿es cierto que va a indultar a sus socios?",
                    "Aquí lo que importa es la concordia, señoría, y sus tribunales ya veremos.",
                    ("Claro, ya está firmado.", "Solo a mis amigos."),
                ),
                Dialogue(
                    "¿Por qué no condena usted a Maduro?",
                    "Y ustedes, ¿por qué no condenaron los sobres de su tesorero?",
                    ("Porque nunca enseña las actas.", "Lo condenaré cuando publique las actas."),
                ),
                Dialogue(
                    "¿Va a escribir otra carta a la ciudadanía?",
                    "Escribiré cuando la máquina del fango deje de funcionar, señoría.",
                    (
                        "Sí, y me iré cinco días a reflexionar.",
                        "Ya la tengo escrita para el jueves.",
                    ),
                ),
                Dialogue(
                    "¿Le parece normal un Gobierno con veintidós ministerios?",
                    "Más asesores tenían ustedes en Génova.",
                    ("Son pocos: quiero treinta.", "Uno por socio, más o menos."),
                ),
                Dialogue(
                    "Feijóo le pregunta cuándo va a dimitir.",
                    "Usted ganó las elecciones y no gobierna: dimita usted de la oposición.",
                    ("Mañana a primera hora.", "Le dejo el sillón un rato, si quiere."),
                ),
            ),
        ),
        "lobbies": DialoguePack(
            "🤝 Pasillos del Parlamento Europeo. Regla de oro: **escucha, sonríe y no firmes "
            "nada.**",
            (
                Dialogue(
                    "Un lobista de una gran tecnológica te invita a un desayuno «informal».",
                    "Vas, escuchas, te llevas la tarjeta y no prometes nada.",
                    ("Aceptas todo lo que te dice.", "Le firmas un papel «de buena voluntad»."),
                ),
                Dialogue(
                    "Una petrolera pide que el reglamento «reconozca la transición gradual».",
                    "«Muy interesante, lo estudiaremos con el máximo rigor».",
                    ("Le copias su texto tal cual.", "Le dices que se vaya al diablo en directo."),
                ),
                Dialogue(
                    "La agroindustria quiere una exención para los pesticidas.",
                    "Sonríes, tomas notas y lo remites a la comisión competente.",
                    ("Le das la exención ahora mismo.", "Le haces un discurso sobre las abejas."),
                ),
                Dialogue(
                    "Un lobista te regala un maletín de «material informativo».",
                    "Aceptas el folleto, rechazas el maletín.",
                    ("Aceptas el maletín entero.", "Lo abres delante de las cámaras."),
                ),
                Dialogue(
                    "Una asociación farmacéutica te pide una reunión urgente.",
                    "Que la pidan por el registro de transparencia, con cita el mes que viene.",
                    ("Quedas a cenar esta misma noche.", "Le das tu número personal."),
                ),
                Dialogue(
                    "Una empresa de datos te ofrece un «estudio independiente» ya escrito.",
                    "Lo recibes, lo guardas y lo dejas junto a los demás estudios.",
                    ("Lo presentas como propio.", "Firmas el prólogo."),
                ),
                Dialogue(
                    "Un lobby te propone un viaje a una conferencia en una isla.",
                    "Lo declinas con cortesía y lo apuntas en el registro.",
                    ("Aceptas e invitas a un amigo.", "Dices que sí, pero que pagas tú... luego."),
                ),
                Dialogue(
                    "Te piden que enmiendes un párrafo para «clarificar» el texto.",
                    "«Lo valoraremos en el momento procesal oportuno».",
                    ("Lo cambias en el acto.", "Le preguntas cuánto vale la clarificación."),
                ),
                Dialogue(
                    "Un lobista te asegura que su cliente genera cien mil empleos.",
                    "«Fascinante cifra». Sonríes y pides que la envíen por escrito.",
                    ("Le preguntas dónde están.", "Le ofreces un puesto a un primo."),
                ),
                Dialogue(
                    "Una alianza de empresas energéticas te invita a su cóctel anual.",
                    "Pasas un rato, saludas y te vas antes del brindis.",
                    ("Brindas con ellos por el reglamento.", "Te quedas a dormir en el hotel."),
                ),
                Dialogue(
                    "Un enviado de Musk pide que el reglamento proteja «la libertad de "
                    "expresión» de su dueño.",
                    "Lo escuchas y le pides que lo mande por el registro de transparencia.",
                    (
                        "Le pones un check azul al reglamento.",
                        "Le prometes una exención para Marte.",
                    ),
                ),
                Dialogue(
                    "Un lobby de Pekín te regala un móvil de última generación.",
                    "Lo devuelves con una nota amable; tu móvil ya te espía bastante.",
                    ("Lo usas para el trabajo.", "Te haces un selfi con él y lo subes a TikTok."),
                ),
            ),
        ),
        "trilogo": DialoguePack(
            "🇪🇺 Trílogo con el Consejo y la Comisión. Regla de oro: **cierra un acuerdo tan "
            "ambiguo que todos puedan venderlo como victoria.**",
            (
                Dialogue(
                    "El Consejo quiere 2030 y el Parlamento, 2025, para el objetivo.",
                    "Propones «ambición progresiva con hitos intermedios flexibles».",
                    ("Aceptas 2030 sin más.", "Exiges 2025 o te levantas de la mesa."),
                ),
                Dialogue(
                    "La Comisión pide mantener su propuesta original intacta.",
                    "Se mantiene el espíritu y se «adapta» el articulado.",
                    ("Aceptas el texto original.", "Lo reescribes entero en la servilleta."),
                ),
                Dialogue(
                    "Un Estado miembro exige una excepción para su país.",
                    "Una cláusula de «circunstancias nacionales específicas».",
                    ("Le das la excepción completa.", "Le dices que no, bajo ningún concepto."),
                ),
                Dialogue(
                    "Son las tres de la mañana y nadie cede en el artículo 7.",
                    "Lo dejas en «cuando proceda y de conformidad con la legislación».",
                    ("Te duermes en la silla.", "Sacas el artículo y lo borras."),
                ),
                Dialogue(
                    "Hay que decidir quién fija la fecha de aplicación.",
                    "«La fecha será determinada por los Estados miembros en coordinación».",
                    ("Pones una fecha y la defiendes.", "Lo pospones a 2040."),
                ),
                Dialogue(
                    "El Parlamento quiere sanciones; el Consejo, recomendaciones.",
                    "Sanciones «proporcionadas, disuasorias y, en su caso, recomendadas».",
                    ("Sanciones duras o nada.", "Solo recomendaciones, y gracias."),
                ),
                Dialogue(
                    "La prensa espera un titular claro de la reunión.",
                    "Sales con tus socios y dices que ha sido «un paso decisivo».",
                    ("Dices que no hay acuerdo.", "Explicas el acuerdo con detalle."),
                ),
                Dialogue(
                    "Quedan cinco minutos antes de que cierre la presidencia semestral.",
                    "Aprobáis un texto con tres interpretaciones posibles.",
                    ("Pedís otra reunión para después.", "Lo votáis sin leerlo."),
                ),
                Dialogue(
                    "Un jurista advierte de que el texto no se entiende.",
                    "«Se aclarará en los actos delegados». Y todos felices.",
                    ("Le das la razón y repites la ronda.", "Despides al jurista."),
                ),
                Dialogue(
                    "Hay que repartir la financiación entre las partes.",
                    "Cifras entre corchetes y un «sin perjuicio del marco financiero».",
                    ("Fijas cantidades exactas.", "Dices que cada uno pague lo suyo."),
                ),
                Dialogue(
                    "La presidencia húngara amenaza con vetar el texto.",
                    "Una «declaración de la presidencia» anexa que no obliga a nadie.",
                    ("Aceptas el veto.", "Llamas a Orbán a las tres de la mañana."),
                ),
                Dialogue(
                    "Macron quiere dar un discurso de veinte minutos antes de firmar.",
                    "Le das la palabra y adelantas el café para todos.",
                    ("Le cortas el micrófono.", "Le pides que lo resuma en un tuit."),
                ),
                Dialogue(
                    "Los fabricantes piden una prórroga para el tapón pegado a la botella.",
                    "Prórroga «técnica», con cláusula de revisión en 2030.",
                    (
                        "Despegar los tapones de toda Europa.",
                        "Que cada uno lleve el tapón en la mano.",
                    ),
                ),
            ),
        ),
        "comisario": DialoguePack(
            "🏢 Despacho en el Berlaymont. Regla de oro: **la respuesta buena es la más "
            "comunitaria, la más burocrática y la que no compromete a nadie.**",
            (
                Dialogue(
                    "Una gran tecnológica recurre la multa de miles de millones.",
                    "«La Comisión ha actuado con arreglo a los Tratados y confía en el control "
                    "jurisdiccional».",
                    ("Le subes la multa por insolente.", "Le dices que no pasa nada."),
                ),
                Dialogue(
                    "Un Estado miembro se queja de que el reglamento le cuesta mucho.",
                    "Remites a la «evaluación de impacto» y a una consulta pública.",
                    ("Le das dinero ahora mismo.", "Le dices que se aguante."),
                ),
                Dialogue(
                    "Un eurodiputado te pregunta por escrito cuántos funcionarios hay.",
                    "«La Comisión remite a su respuesta a la pregunta E-004321/2024».",
                    ("Contestas con la cifra exacta.", "No contestas nunca."),
                ),
                Dialogue(
                    "Una audiencia en el Parlamento te interroga sobre tu programa.",
                    "«Trabajaremos con los colegisladores en un espíritu constructivo».",
                    ("Explicas tu plan en detalle.", "Les cuentas tus vacaciones."),
                ),
                Dialogue(
                    "La prensa pregunta si habrá nuevo reglamento sobre el tema.",
                    "«Se estudiará en el marco del programa de trabajo de la Comisión».",
                    ("Anuncias el reglamento en directo.", "Dices que no habrá nada."),
                ),
                Dialogue(
                    "Un país te pide flexibilidad con el déficit.",
                    "«Se aplicarán las cláusulas de salvaguardia conforme al marco fiscal».",
                    ("Le dices que sí a todo.", "Le abres un procedimiento por si acaso."),
                ),
                Dialogue(
                    "Dos direcciones generales discuten por quién redacta el informe.",
                    "Creas un grupo de trabajo interservicios.",
                    ("Lo redactas tú esa noche.", "Las mandas a la cantina a resolverlo."),
                ),
                Dialogue(
                    "Un Estado miembro incumple una directiva desde hace diez años.",
                    "Una carta de emplazamiento, con plazo de dos meses ampliable.",
                    ("Le impones una multa inmediata.", "Mirar a otro lado, que ya se acordará."),
                ),
                Dialogue(
                    "La gente en redes pregunta qué hace Bruselas por ellos.",
                    "Un comunicado con la palabra «ciudadanos» cinco veces.",
                    ("Les respondes con memes.", "Les mandas el informe de 800 páginas."),
                ),
                Dialogue(
                    "Una asociación pide transparencia sobre una reunión a puerta cerrada.",
                    "«El documento solicitado queda fuera del ámbito del Reglamento 1049/2001».",
                    ("Les envías las actas completas.", "Les dices que se las inventen."),
                ),
                Dialogue(
                    "Trump anuncia aranceles del 50 % a todo lo europeo.",
                    "«La Unión responderá de manera firme, proporcionada y unida».",
                    (
                        "Le mandas una tarta con un 50 % menos.",
                        "Te rindes y compras gas a precio Trump.",
                    ),
                ),
                Dialogue(
                    "Erdoğan te invita a su palacio de mil habitaciones a hablar de la adhesión.",
                    "«El diálogo con Turquía sigue abierto en el marco de las negociaciones».",
                    ("Vas y te pierdes en el ala oeste.", "Le dices que entra en la UE el lunes."),
                ),
                Dialogue(
                    "Boris Johnson quiere volver a la UE, pero sin pagar.",
                    "«La puerta está abierta conforme al artículo 49 del Tratado».",
                    (
                        "Le dices que sí y que traiga el té.",
                        "Le pides el cheque británico de vuelta.",
                    ),
                ),
                Dialogue(
                    "Un país pide que las cookies no haya que aceptarlas.",
                    "«La Comisión evaluará la experiencia del usuario al revisar la directiva».",
                    ("Aceptas todas las cookies.", "Prohíbes las galletas."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas de la obra (Ladrillo y Obra pública) --------------------------

MEMORY_PACKS.update(
    {
        "pisos": MemoryPack(
            "🏖️ Venta sobre plano. El cliente {n} quiere su piso con esto, por orden:",
            "Enséñale el piso piloto en el mismo orden (y no menciones la depuradora).",
            (
                ("🏊", "Piscina comunitaria"),
                ("🅿️", "Plaza de garaje"),
                ("🌊", "Vistas al mar (de lado)"),
                ("⛳", "Campo de golf"),
                ("🧱", "Trastero"),
                ("🛗", "Ascensor"),
                ("🌴", "Palmera"),
                ("☀️", "Solárium"),
                ("🛋️", "Cocina americana"),
            ),
        ),
        "concesiones": MemoryPack(
            "📋 Consejo de concesiones. Renueva en este orden (tanda {n}):",
            "Renueva las concesiones en el mismo orden, antes de que caduquen.",
            (
                ("🛣️", "Autopista radial (rescatada)"),
                ("✈️", "Aeropuerto sin aviones"),
                ("🚄", "Estación del AVE en mitad del campo"),
                ("🚇", "Línea de metro"),
                ("💧", "Desaladora"),
                ("🅿️", "Parking municipal"),
                ("🏟️", "Estadio"),
                ("🏥", "Hospital de gestión privada"),
                ("⚓", "Puerto deportivo"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "terrenos": SortPack(
            "🗺️ Eres urbanizador. Cada terreno va a su sitio: construir ya, recalificar "
            "(con mariscada), dejarlo protegido o devolverlo, que es de Costas.",
            "🌄 Terreno {n}/{total}:",
            ("🏗️ Construir ya", "🦐 Recalificar", "🦎 Protegido", "🌊 Es de Costas"),
            (
                ("Un solar con licencia en el centro del pueblo", 0),
                ("Una parcela urbanizable al lado del polígono", 0),
                ("El hueco de una gasolinera cerrada", 0),
                ("Un pinar justo detrás de tu última urbanización", 1),
                ("Una finca de plataneras que «ya no da»", 1),
                ("El campo de fútbol del pueblo", 1),
                ("Un barranco «seco desde hace años»", 1),
                ("El hábitat del lagarto gigante de El Hierro", 2),
                ("Las dunas de Maspalomas", 2),
                ("Un yacimiento con grabados guanches", 2),
                ("La primera línea de una playa virgen", 3),
                ("Un hotel a medio hacer en plena arena desde 2008", 3),
                ("Un paseo marítimo «que total, nadie usa»", 3),
            ),
        ),
        "licitaciones": SortPack(
            "📑 Eres director de licitaciones. Cada concurso va a su sitio: nos "
            "presentamos, en UTE con la competencia, baja temeraria (y luego "
            "modificado) o pasamos.",
            "📂 Concurso {n}/{total}:",
            (
                "✅ Nos presentamos",
                "🤝 En UTE con la competencia",
                "📉 Baja y luego modificado",
                "🚪 Pasamos",
            ),
            (
                ("Un tramo de autovía normal, con presupuesto razonable", 0),
                ("La reforma de un colegio, con plazos claros", 0),
                ("Un hospital con el proyecto ya redactado", 0),
                ("Un AVE de 4.000 millones que ninguna empresa puede hacer sola", 1),
                ("Un túnel bajo el mar que asusta hasta al banco", 1),
                ("Ocho estadios para un Mundial con un plazo imposible", 1),
                ("Un tramo con un presupuesto que no da ni para el asfalto", 2),
                ("Una obra que el ministro quiere inaugurar antes de las elecciones", 2),
                ("Un puerto que «seguro que se amplía después»", 2),
                ("Un concurso a medida de otra empresa (su logo sale en el pliego)", 3),
                ("Una obra en un país del Sahel que va por su tercer golpe de Estado", 3),
                ("Un pliego que exige haber construido antes un muro de 3.000 km", 3),
            ),
        ),
    }
)  # fmt: skip

DIALOGUE_PACKS.update(
    {
        "banco": DialoguePack(
            "🏦 Reunión con el banco para refinanciar. Regla de oro: **nunca digas que no "
            "puedes pagar: el mercado está «en un momento de oportunidad».**",
            (
                Dialogue(
                    "¿Cómo piensa devolver los 40 millones?",
                    "Con la venta de la fase dos, que tiene una demanda espectacular.",
                    ("No pienso.", "Con un décimo de Navidad que llevo en la cartera."),
                ),
                Dialogue(
                    "Los pisos de la fase uno siguen vacíos.",
                    "Están en fase de comercialización estratégica.",
                    ("Sí, solo vive un gato.", "Es que están en mitad del desierto."),
                ),
                Dialogue(
                    "El tasador dice que valen la mitad.",
                    "Buscamos otro tasador con más visión de futuro.",
                    ("Tiene razón el hombre.", "La mitad de la mitad, si le soy sincero."),
                ),
                Dialogue(
                    "¿Qué avales tiene?",
                    "Veinte años en el sector y la confianza del ayuntamiento.",
                    ("La bici de mi hijo.", "Una foto con Florentino, firmada."),
                ),
                Dialogue(
                    "En la tele dicen que estalla la burbuja.",
                    "El ladrillo nunca baja: como mucho, se toma un respiro.",
                    ("Ya ha estallado; por eso vengo.", "¿Qué burbuja? ¿La del jacuzzi?"),
                ),
                Dialogue(
                    "Necesitamos ver las cuentas de la sociedad.",
                    "Se las mando el lunes, auditadas y en tres idiomas.",
                    ("Están en una servilleta.", "¿De cuál de las cuarenta sociedades?"),
                ),
                Dialogue(
                    "El banco se quiere quedar los pisos como pago.",
                    "Dación en pago no: refinanciamos a treinta años y todos contentos.",
                    ("Vale, quedáoslos.", "Os doy también el chalet, que me ahoga."),
                ),
                Dialogue(
                    "¿Por qué hay un yate a nombre de la empresa?",
                    "Es la oficina de ventas flotante para el mercado internacional.",
                    ("Para escaparme.", "Es de mi cuñado, que vive en Andorra."),
                ),
                Dialogue(
                    "¿Le suena la palabra «concurso de acreedores»?",
                    "Como concepto académico, sí.",
                    (
                        "La estoy rellenando ahora mismo.",
                        "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
                    ),
                ),
            ),
        ),
        "alcalde": DialoguePack(
            "🏛️ Comisión de urbanismo con el alcalde. Regla de oro: **todo se arregla con un "
            "convenio urbanístico y una foto con casco.**",
            (
                Dialogue(
                    "El alcalde dice que el pinar es suelo protegido.",
                    "Proponemos un convenio: el pinar se urbaniza y el pueblo gana un "
                    "polideportivo.",
                    ("Lo talamos y ya se verá.", "Le invito a una mariscada y lo hablamos."),
                ),
                Dialogue(
                    "La oposición pide un informe medioambiental.",
                    "Encantados: lo encargamos nosotros, que conocemos a un técnico estupendo.",
                    ("¿Medioambiental? ¿Eso qué es?", "Que lo haga mi primo, que es fontanero."),
                ),
                Dialogue(
                    "Los vecinos protestan por el rascacielos en primera línea.",
                    "Es un hito arquitectónico que pondrá el pueblo en el mapa.",
                    ("Que se muden.", "Le quito una planta y se queda en veintinueve."),
                ),
                Dialogue(
                    "El alcalde pregunta qué gana el pueblo con todo esto.",
                    "Una rotonda con una escultura de dos millones.",
                    ("Atascos.", "El pueblo, nada; usted, ya hablaremos."),
                ),
                Dialogue(
                    "El secretario municipal no quiere firmar.",
                    "Pedimos un informe jurídico externo que diga que sí.",
                    (
                        "Lo cambiamos por otro secretario.",
                        "Firmo yo por él, que tengo buena letra.",
                    ),
                ),
                Dialogue(
                    "Te piden viviendas protegidas en la urbanización.",
                    "Las hacemos, en la parcela que da justo a la autopista.",
                    ("Ni una.", "Todas protegidas, ¡wepa!"),
                ),
                Dialogue(
                    "La prensa local habla de «un nuevo Marbella».",
                    "Marbella es un modelo de éxito turístico, no hay más que verla.",
                    (
                        "Ojalá: allí sí se recalificaba bien.",
                        "Sin comentarios, que me escucha el juez.",
                    ),
                ),
                Dialogue(
                    "Bruselas avisa de que la Ley de Costas prohíbe construir en la arena.",
                    "Construimos a 101 metros y llamamos «paseo marítimo» a la terraza.",
                    ("Pues construimos en el mar.", "Que vengan a echarnos de la playa."),
                ),
            ),
        ),
        "palco": DialoguePack(
            "⚽ Palco del estadio. Regla de oro: **en el palco no se habla de fútbol; se habla "
            "de obra pública y recalificaciones.**",
            (
                Dialogue(
                    "El alcalde te pregunta qué tal va el equipo.",
                    "Muy bien, igual que la recalificación de la ciudad deportiva.",
                    ("Fatal, bajamos.", "Ni idea: no he visto un partido en mi vida."),
                ),
                Dialogue(
                    "Un ministro quiere saber si apoyarás el Mundial en España.",
                    "Si hay estadio nuevo, mis máquinas están listas.",
                    ("No, que sale carísimo.", "Solo si juega Messi."),
                ),
                Dialogue(
                    "La afición pide fichajes.",
                    "Primero el estadio nuevo: los fichajes vienen con los palcos VIP.",
                    ("Fichamos al portero de mi cuñado.", "Que los pague la afición."),
                ),
                Dialogue(
                    "Un jeque quiere comprar el club.",
                    "Hablamos, pero el suelo de la ciudad deportiva se queda en casa.",
                    ("Vendido, con todos dentro.", "Solo si me regala un Mundial."),
                ),
                Dialogue(
                    "Un periodista pregunta quién construye el estadio nuevo.",
                    "La mejor empresa del país, adjudicada con todas las garantías.",
                    ("La mía, casualmente.", "Lo firmé en una servilleta en el descanso."),
                ),
                Dialogue(
                    "Te sientas al lado del presidente del Gobierno.",
                    "Le hablas del tiempo y, en el descanso, del AVE a tu pueblo.",
                    ("Le pides que dimita.", "Le cantas el himno del rival."),
                ),
                Dialogue(
                    "El árbitro pita un penalti en contra.",
                    "Pides calma y un comunicado oficial contra el sistema.",
                    ("Bajas al campo a por él.", "Le mandas un Bizum al árbitro."),
                ),
                Dialogue(
                    "El equipo baja a segunda.",
                    "Es un año de transición hacia un proyecto ganador (y un estadio nuevo).",
                    ("Lo pago con dinero del ayuntamiento.", "Me compro otro club."),
                ),
            ),
        ),
        "fomento": DialoguePack(
            "🚄 Despacho del Ministerio de Transportes. Regla de oro: **pide el modificado y "
            "ofrece la inauguración.**",
            (
                Dialogue(
                    "El ministro pregunta por qué el AVE va con tres años de retraso.",
                    "Por las mejoras de seguridad que exige la nueva normativa europea.",
                    ("Por la siesta.", "Porque se nos olvidó el túnel."),
                ),
                Dialogue(
                    "El presupuesto del túnel se ha duplicado.",
                    "Es un modificado técnico por la geología, imprevisible como la política.",
                    ("Es que cavamos dos veces.", "Pusimos mármol en el túnel."),
                ),
                Dialogue(
                    "Te piden que el tren pare en el pueblo del ministro.",
                    "Una estación nueva, con aparcamiento, en mitad del campo.",
                    ("Imposible.", "Que vaya andando, que es sano."),
                ),
                Dialogue(
                    "La UCO investiga al asesor del ministerio que os adjudicaba obras.",
                    "Colaboraremos con la justicia; nuestras adjudicaciones son impecables.",
                    ("Nos cobraba en chistorras.", "Ya le dije que no usara WhatsApp."),
                ),
                Dialogue(
                    "Los trenes nuevos no caben por los túneles.",
                    "Se adaptarán los túneles con un nuevo contrato.",
                    ("Los trenes los hicieron mal.", "Que pasen de lado."),
                ),
                Dialogue(
                    "Hay que poner la primera piedra antes de las elecciones.",
                    "La ponemos el viernes; el resto de piedras ya irán llegando.",
                    ("No hay ni proyecto.", "La ponemos de cartón piedra."),
                ),
                Dialogue(
                    "El Tribunal de Cuentas pregunta por el aeropuerto sin aviones.",
                    "Es una infraestructura estratégica a la espera de demanda.",
                    ("Tiene estatua, ¿no le vale?", "Lo usan los conejos para correr."),
                ),
                Dialogue(
                    "El ministerio quiere licitar sin la ampliación que pedisteis.",
                    "Sin ampliación no hay oferta: el mercado está muy tensionado.",
                    ("Vale, la hacemos gratis.", "La hacemos y la cobramos en B."),
                ),
            ),
        ),
        "internacional": DialoguePack(
            "🌍 Contratos por el mundo. Regla de oro: **firma todo, cobra por adelantado y que "
            "el sobrecoste lo pague el país.**",
            (
                Dialogue(
                    "Trump quiere un muro en la frontera y dice que lo paga México.",
                    "Encantados: nuestra tarifa, en dólares y por adelantado.",
                    ("Que lo pague México, pues.", "Le hacemos una valla de cañas."),
                ),
                Dialogue(
                    "Erdoğan quiere ampliar su palacio de mil habitaciones.",
                    "Mil cien, con helipuerto, y la factura en euros.",
                    ("¿No le bastan mil?", "Le recomendamos un piso en Vallecas."),
                ),
                Dialogue(
                    "Arabia quiere un tren de alta velocidad en mitad del desierto.",
                    "Firmamos, y el sobrecoste de la arena lo pactamos después.",
                    (
                        "La arena estropea las vías; mejor no.",
                        "Mejor un camello de alta velocidad.",
                    ),
                ),
                Dialogue(
                    "Panamá dice que la ampliación del canal cuesta más de lo pactado.",
                    "Abrimos un arbitraje internacional que dure diez años.",
                    ("Pagamos la diferencia.", "Lo dejamos a medias y nos vamos."),
                ),
                Dialogue(
                    "Kim Jong-un quiere un hotel de 105 plantas para su cumpleaños.",
                    "Lo acabamos para la fecha y cobramos antes del aplauso obligatorio.",
                    ("Ya tiene uno sin terminar desde 1987.", "Le regalamos un misil de cartón."),
                ),
                Dialogue(
                    "Milei cancela toda la obra pública con una motosierra.",
                    "Le ofrecemos una concesión privada con peaje: ¡libertad, carajo!",
                    ("Le pedimos que no corte.", "Le mandamos una motosierra más grande."),
                ),
                Dialogue(
                    "Qatar necesita ocho estadios con aire acondicionado para un Mundial.",
                    "Con aire, con césped y con calendario de pagos.",
                    ("Que jueguen en invierno.", "Mejor un estadio en Las Palmas."),
                ),
                Dialogue(
                    "Xi te invita a entrar en la Ruta de la Seda.",
                    "Un memorando de entendimiento con las cláusulas en chino y en inglés.",
                    ("Sí, pero que me den TikTok.", "Me lo pienso, que me baja el crédito social."),
                ),
                Dialogue(
                    "Maduro quiere un metro nuevo en Caracas y paga en bolívares.",
                    "Por adelantado, en euros y depositado en Suiza.",
                    ("Aceptamos bolívares.", "Primero, que enseñe las actas."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas de la sanidad (Pública y Privada) ------------------------------

MEMORY_PACKS.update(
    {
        "quirofano": MemoryPack(
            "🗓️ Parte de quirófano del lunes {n}. El orden de las intervenciones:",
            "Programa las operaciones en el mismo orden (y que no se cuele el sobrino del "
            "gerente).",
            (
                ("🦴", "Prótesis de cadera"),
                ("🫀", "Bypass coronario"),
                ("🧠", "Hernia discal"),
                ("👁️", "Cataratas"),
                ("🦵", "Menisco del concejal"),
                ("🩻", "Apendicitis"),
                ("🫁", "Biopsia de pulmón"),
                ("🧽", "Contar las gasas"),
                ("🧤", "Cambio de guantes"),
            ),
        ),
        "retoques": MemoryPack(
            "💋 El cliente {n} lo quiere todo, en este orden (y en efectivo):",
            "Haz los retoques en el mismo orden, que luego sale sin filtro en las fotos.",
            (
                ("💉", "Bótox"),
                ("👄", "Relleno de labios"),
                ("👃", "Rinoplastia"),
                ("🧑‍🦲", "Injerto capilar (mejor que el de Turquía)"),
                ("🦷", "Carillas de blanco nuclear"),
                ("🍑", "Aumento de glúteos"),
                ("👀", "Párpados"),
                ("🪒", "Láser"),
                ("😐", "Lifting (sin expresión)"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "derivaciones": SortPack(
            "🗂️ Eres jefe de servicio. Cada paciente va a su sitio: quirófano ya, lista de "
            "espera, concierto con la privada o de vuelta a su médico de cabecera.",
            "🧑‍⚕️ Paciente {n}/{total}:",
            ("🔪 Quirófano ya", "⏳ Lista de espera", "🤝 A la privada", "🏠 A su médico"),
            (
                ("Apendicitis con fiebre y la tripa como una tabla", 0),
                ("Fractura de cadera de una señora de 85 años", 0),
                ("Hernia estrangulada desde esta mañana", 0),
                ("Embarazo ectópico con dolor fuerte", 0),
                ("Juanete que le molesta «desde la boda de su hija»", 1),
                ("Cataratas que aún le dejan ver el telediario", 1),
                ("Rodilla de pádel del concejal (sin prisa, aunque él diga que sí)", 1),
                ("Varices de toda la vida, sin dolor", 1),
                ("Prótesis de rodilla con dos años de espera (y elecciones en mayo)", 2),
                ("Operación de hombro que la Consejería quiere sacar de la lista ya", 2),
                ("Doscientas cataratas para bajar la lista antes del telediario", 2),
                ("Resonancia que en la pública tarda nueve meses", 2),
                ("Catarro derivado a Cirugía «por si acaso»", 3),
                ("Viene a por la receta de las pastillas de la tensión", 3),
                ("Pide un justificante para el gimnasio", 3),
                ("Dolor de espalda de la mudanza, sin nada más", 3),
            ),
        ),
    }
)  # fmt: skip

#: Pólizas de la mutua: la cláusula que deja tirado al asegurado y las que son normales.
POLICY_TRAPS: tuple[str, ...] = (
    "Excluye cualquier enfermedad que empiece por vocal",
    "No cubre accidentes en días laborables ni en festivos",
    "Carencia de 18 meses para las urgencias",
    "Las prótesis solo se cubren si son de segunda mano",
    "No cubre lesiones jugando al pádel (el 90 % de los asegurados)",
    "La ambulancia se cubre si la empuja el asegurado",
    "Excluye las enfermedades preexistentes, incluida la de haber nacido",
    "Copago del 100 % en todo lo que no sea una tirita",
    "Los partos solo se cubren los martes y si son de gemelos",
    "Habitación individual, compartida con otros tres pacientes",
)
POLICY_FINE: tuple[str, ...] = (
    "Consultas de medicina general sin copago",
    "Urgencias 24 horas en los centros concertados",
    "Análisis de sangre con volante del especialista",
    "Hospitalización en habitación individual",
    "Pediatría para los hijos del titular",
    "Revisión ginecológica una vez al año",
    "Diez sesiones de fisioterapia al año",
    "Limpieza dental anual incluida",
    "Segunda opinión médica por videollamada",
)

DIALOGUE_PACKS.update(
    {
        "direccion": DialoguePack(
            "🏥 Junta técnico-asistencial. Regla de oro: **nunca digas «no hay dinero»; di "
            "«estamos priorizando».**",
            (
                Dialogue(
                    "Los cirujanos amenazan con dimitir en bloque por las peonadas.",
                    "Te sientas con ellos y pagas las tardes como se pactó.",
                    ("Que dimitan, ya vendrán los de la privada.",
                     "Les pones un reloj de fichar dentro del quirófano."),
                ),
                Dialogue(
                    "Urgencias tiene 40 pacientes en el pasillo.",
                    "Abres la planta que lleva cerrada «por obras» desde 2019.",
                    ("Mueves las camillas al parking, que hay más sitio.",
                     "Comunicado de prensa: «situación puntual»."),
                ),
                Dialogue(
                    "El jefe de Traumatología quiere un robot quirúrgico de 2 millones.",
                    "Pides un estudio de coste y de uso antes de firmar.",
                    ("Lo compras: queda muy bien en la foto.", "Le regalas un destornillador."),
                ),
                Dialogue(
                    "La supervisora te avisa: no hay sustitutos para agosto.",
                    "Abres la bolsa de empleo y haces contratos de verano completos.",
                    ("Contratos de un día, renovables cada mañana.",
                     "Cerráis el hospital en agosto, como los bares."),
                ),
                Dialogue(
                    "Un periodista pregunta por la lista de espera.",
                    "Das la cifra real y el plan para bajarla.",
                    ("«No es una lista: es una cola de esperanza».",
                     "Le dices que la culpa es de Perro Sanxe."),
                ),
                Dialogue(
                    "El sindicato pide descanso después de las guardias de 24 horas.",
                    "Lo garantizas por escrito: lo dice la ley.",
                    ("Les regalas un cojín cervical.",
                     "Que duerman en la cama de guardias, de dos en dos."),
                ),
                Dialogue(
                    "Un presentador famoso quiere saltarse la lista de espera.",
                    "Entra por la lista, como todo el mundo.",
                    ("Le abres un quirófano el domingo.", "Le pides un selfie a cambio."),
                ),
                Dialogue(
                    "Se cae el sistema informático a las nueve de la mañana.",
                    "Activas el plan de contingencia: papel y bolígrafo.",
                    ("Reinicias el servidor a patadas.", "Mandas a todo el mundo a casa."),
                ),
                Dialogue(
                    "Una consultora te ofrece «optimizar» el hospital por 3 millones.",
                    "Pides que expliquen qué van a hacer antes de pagar un euro.",
                    ("Firmas: el PowerPoint tenía unas gráficas preciosas.",
                     "Le encargas también la cafetería."),
                ),
                Dialogue(
                    "La Consejería te pide recortar un 10 % «sin tocar la calidad».",
                    "Les mandas por escrito qué se recorta y qué se pierde con ello.",
                    ("Recortas el papel higiénico.",
                     "Apagas la luz del pasillo, que total ya hay pacientes."),
                ),
            ),
        ),
        "gerencia": DialoguePack(
            "📊 Reunión con la Consejería. Regla de oro: **todo es «un reto» y nada es «un "
            "problema».**",
            (
                Dialogue(
                    "El consejero quiere inaugurar el ala nueva antes de las elecciones. No "
                    "hay personal.",
                    "Propones abrirla por fases, a medida que haya plantilla.",
                    ("Cinta, tijeras y la luz apagada al día siguiente.",
                     "Contratas figurantes con bata para la foto."),
                ),
                Dialogue(
                    "Te piden explicar por qué el hospital tiene déficit.",
                    "Enseñas qué está infradotado y cuánto cuesta arreglarlo.",
                    ("La culpa es de los pacientes, por ponerse malos.",
                     "Escondes las facturas en un cajón, como en 2008."),
                ),
                Dialogue(
                    "Hay que comprar mascarillas urgentes y te llama un intermediario.",
                    "Compras por el procedimiento de emergencia, con todo por escrito.",
                    ("Compras al amigo de un asesor de un ministro, el del Rolls.",
                     "Pagas por adelantado a una empresa de colchones."),
                ),
                Dialogue(
                    "El hospital sale el último en un ranking.",
                    "Miras qué mide el ranking y mejoras lo que sí importa.",
                    ("Compras el ranking.", "Dices que el ranking lo ha hecho el Kremlin."),
                ),
                Dialogue(
                    "Los médicos se van a Francia y a Portugal porque pagan el doble.",
                    "Pides a la Consejería un plan de retención: sueldos y plazas fijas.",
                    ("Les cantas «¡Azul, azul, como el mar azul!» para que vuelvan.",
                     "Fichas médicos por TikTok."),
                ),
                Dialogue(
                    "La cafetería sube el café a 2,50 €.",
                    "Renegocias la concesión: el personal no puede pagar eso.",
                    ("«¡Quiero café!» y te lo pagas con las dietas.", "Prohíbes el café."),
                ),
                Dialogue(
                    "Un fondo de inversión quiere gestionar el hospital «con eficiencia "
                    "privada».",
                    "Le pides los números de los otros hospitales que gestiona.",
                    ("Le das las llaves y te vas de vacaciones.",
                     "Le vendes también el tanatorio."),
                ),
                Dialogue(
                    "Hay goteras en el quirófano 3.",
                    "Lo cierras y pides la obra urgente.",
                    ("Pones un cubo y a operar.", "Dices que es humedad ambiental, muy sana."),
                ),
                Dialogue(
                    "En el Parlamento autonómico te preguntan por las camas en el pasillo.",
                    "Reconoces el problema y das plazos.",
                    ("«Es el vecino el que elige la cama», como diría Rajoy.",
                     "Las camas en el pasillo dan ambiente."),
                ),
                Dialogue(
                    "El consejero te pide que la memoria anual quede «bonita».",
                    "La haces exacta y bien explicada.",
                    ("Gráficas con el eje empezando en 99.",
                     "La presentas en un NO-DO con música de fanfarria."),
                ),
            ),
        ),
        "consejeria": DialoguePack(
            "🏛️ Pleno del Parlamento autonómico. Regla de oro: **la culpa siempre es del "
            "Gobierno central (o del anterior).**",
            (
                Dialogue(
                    "La oposición te pregunta por la lista de espera.",
                    "Es herencia del Gobierno anterior y, aun así, la estamos bajando.",
                    ("Es culpa mía: dimito.", "¿Qué lista?"),
                ),
                Dialogue(
                    "Llega una pandemia nueva.",
                    "Un comité de expertos de verdad y datos públicos cada día.",
                    ("Un comité de expertos que no existe, como en 2020.",
                     "Recomiendas gárgaras de gofio."),
                ),
                Dialogue(
                    "Te piden explicar el contrato de mascarillas a precio de oro.",
                    "Publicas el expediente entero en el portal de transparencia.",
                    ("«Fue una urgencia y no me acuerdo».",
                     "Dices que las trajo Puigdemont en el maletero."),
                ),
                Dialogue(
                    "Toca repartir los fondos Next Generation.",
                    "Presentas proyectos concretos: centros de salud y equipos.",
                    ("Pides el dinero para una rotonda con estatua.",
                     "Te lo gastas en consultoras."),
                ),
                Dialogue(
                    "Ayuso dice que en Madrid se opera más y mejor.",
                    "Comparas los datos públicos, comunidad por comunidad.",
                    ("Le contestas con una frase sobre la fruta.",
                     "Le pides que te preste quirófanos."),
                ),
                Dialogue(
                    "Los médicos de familia convocan huelga: ven 60 pacientes al día.",
                    "Negocias cupos máximos y más plazas de residente.",
                    ("Les recomiendas consultas de un minuto.", "Contratas a Doctor Google."),
                ),
                Dialogue(
                    "Un periodista te pregunta si te has vacunado.",
                    "Sí, y enseñas el certificado.",
                    ("Como Bolsonaro: «yo no me vacuno».",
                     "Le pinchas a él para demostrarlo."),
                ),
                Dialogue(
                    "Trump dice que la sanidad de EE. UU. es «the best, believe me».",
                    "Explicas lo que cuesta allí una ambulancia.",
                    ("Le das la razón y copias su modelo.",
                     "Le mandas la factura de tu última guardia."),
                ),
                Dialogue(
                    "Te preguntan si vas a fichar por una aseguradora al dejar el cargo.",
                    "Respetarás el periodo de incompatibilidad, como manda la ley.",
                    ("Ya tengo el despacho elegido.",
                     "¿Puerta giratoria? Es una puerta automática."),
                ),
                Dialogue(
                    "Hay que nombrar al nuevo gerente del hospital.",
                    "Concurso de méritos y una comparecencia pública.",
                    ("Nombras al cuñado del alcalde.",
                     "A dedo, pero con un dedo muy profesional."),
                ),
            ),
        ),
        "clinica": DialoguePack(
            "💳 Recepción de la clínica. Regla de oro: **el paciente es «cliente», y el cliente "
            "siempre trae tarjeta.**",
            (
                Dialogue(
                    "Un futbolista quiere entrar por detrás para que no le vean.",
                    "Garaje privado, ascensor directo y confidencialidad.",
                    ("Le haces una foto para las redes.",
                     "Le cobras aparte la puerta de atrás."),
                ),
                Dialogue(
                    "Un cliente pregunta por qué la resonancia cuesta 400 €.",
                    "Le das el presupuesto detallado y le explicas qué incluye.",
                    ("Porque la hacemos con más cariño.",
                     "Porque la máquina es alemana y tiene sentimientos."),
                ),
                Dialogue(
                    "Una influencer quiere un chequeo «gratis a cambio de visibilidad».",
                    "Le ofreces la tarifa normal o un acuerdo por escrito.",
                    ("Se lo haces todo gratis por diez stories.",
                     "Le cobras por cada seguidor."),
                ),
                Dialogue(
                    "Un cliente VIP se queja de que el café de la sala de espera es de "
                    "máquina.",
                    "Barista y cruasán: esto es la privada.",
                    ("«¿Con pulpa? ¡Sin pulpa!» y le das un zumo de bote.",
                     "Que vaya a la pública, que allí ni café."),
                ),
                Dialogue(
                    "Un paciente de la mutua pide una prueba que su póliza no cubre.",
                    "Le explicas qué cubre y le das presupuesto del resto.",
                    ("Se la haces y que pague la mutua sin enterarse.",
                     "Le dices que lo suyo no existe."),
                ),
                Dialogue(
                    "Un médico quiere trabajar por la mañana en la pública y por la tarde aquí.",
                    "Si tiene la compatibilidad en regla, adelante.",
                    ("Que deje la pública, que aquí no hay listas.",
                     "Que se traiga a sus pacientes de la pública."),
                ),
                Dialogue(
                    "Un turista de EE. UU. no se cree lo barata que es la consulta.",
                    "Le atiendes con la tarifa de siempre.",
                    ("Le cobras la tarifa de Ohio.",
                     "Le dices que en España todo es gratis menos la luz."),
                ),
                Dialogue(
                    "Reseña de una estrella: «tardaron 7 minutos en atenderme».",
                    "Contestas con amabilidad y revisas qué pasó.",
                    ("Contestas: «en la pública tardan 7 meses».",
                     "Compras cien reseñas de cinco estrellas."),
                ),
                Dialogue(
                    "Un jeque quiere la planta entera para él y su séquito.",
                    "Le reservas la suite y mantienes las camas para el resto.",
                    ("Echas a todos los pacientes.", "Le alquilas la UCI como trastero."),
                ),
                Dialogue(
                    "Llega un cliente con un diagnóstico que le ha hecho un chatbot.",
                    "Le exploras y luego comparas con lo que dice la máquina.",
                    ("Le das la razón al chatbot.", "Le cobras la consulta al chatbot."),
                ),
            ),
        ),
        "conciertos": DialoguePack(
            "🤝 Despacho de la Consejería. Regla de oro: **la pública no da abasto y tú tienes "
            "quirófanos libres (qué casualidad).**",
            (
                Dialogue(
                    "La Comunidad quiere derivarte 5.000 cataratas.",
                    "Precio cerrado por proceso y resultados auditados.",
                    ("Las operas a toda prisa, que se paga por ojo.",
                     "Operas el mismo ojo dos veces."),
                ),
                Dialogue(
                    "Te ofrecen gestionar un hospital nuevo durante 30 años.",
                    "Contrato con indicadores de calidad y penalizaciones claras.",
                    ("Firmas con una cláusula: si pierdes dinero, paga la Comunidad.",
                     "Le pones tu nombre al hospital."),
                ),
                Dialogue(
                    "Un fondo de Luxemburgo quiere el 40 % del grupo.",
                    "Pides garantías de que no recortará plantilla.",
                    ("Vendes y fusionas Pediatría con la cafetería.", "Le vendes el 140 %."),
                ),
                Dialogue(
                    "La oposición dice que «estáis privatizando la sanidad».",
                    "Publicas cuánto cobráis por proceso y cuánto costaría en la pública.",
                    ("Les invitas a una revisión gratis en la suite.",
                     "Dices que es colaboración público-privada, como los sobres."),
                ),
                Dialogue(
                    "Hay que renovar el concierto de diálisis.",
                    "Mantienes precio y calidad: esos pacientes no pueden esperar.",
                    ("Subes el precio un 30 %: ¿adónde van a ir?",
                     "Ofreces diálisis a domicilio por Temu."),
                ),
                Dialogue(
                    "Un consejero deja el cargo y quiere trabajar con vosotros.",
                    "Esperas a que acabe su periodo de incompatibilidad.",
                    ("Le das un despacho al día siguiente.",
                     "Le pagas «asesorías» desde antes de dejarlo."),
                ),
                Dialogue(
                    "Los médicos piden más sueldo.",
                    "Revisas las tablas salariales: si se van, no hay hospital.",
                    ("Les ofreces una gorra con el logo.",
                     "Contratas médicos por horas, como los riders."),
                ),
                Dialogue(
                    "Un auditor pregunta por las facturas a la Comunidad.",
                    "Le das acceso a todo.",
                    ("Le invitas a un congreso en Cancún.", "Le facturas también la auditoría."),
                ),
                Dialogue(
                    "Milei te propone llevar tu modelo a Argentina.",
                    "Estudias el mercado con calma.",
                    ("Llevas una motosierra a la reunión.", "Le facturas en dólar blue."),
                ),
                Dialogue(
                    "Te piden publicar las listas de espera de la privada.",
                    "Las publicas, igual que la pública.",
                    ("En la privada no hay listas: hay «agendas premium».",
                     "«Lista de espera» te suena a comunismo."),
                ),
            ),
        ),
        "aseguradora": DialoguePack(
            "📈 Junta de accionistas. Regla de oro: **lo que se cobra es «prima»; lo que se "
            "paga es «siniestralidad».**",
            (
                Dialogue(
                    "Los accionistas preguntan por qué el beneficio ha subido un 40 %.",
                    "Más asegurados por las listas de la pública y una buena gestión.",
                    ("Porque denegamos el 40 % de los partes.",
                     "Porque cobramos también a los difuntos."),
                ),
                Dialogue(
                    "Un asegurado de 80 años quiere renovar.",
                    "Le renuevas: lleva treinta años pagando.",
                    ("Le subes la prima un 300 % «por la edad».",
                     "Le ofreces un seguro de decesos en su lugar."),
                ),
                Dialogue(
                    "El regulador revisa tus cláusulas.",
                    "Simplificas la póliza y la publicas en lenguaje claro.",
                    ("Las imprimes en cuerpo 3 para que no se lean.",
                     "Las escribes en latín, como las recetas de antes."),
                ),
                Dialogue(
                    "Trump te propone entrar en el mercado de EE. UU.",
                    "Estudias la regulación y los riesgos antes de entrar.",
                    ("Entras cobrando 40.000 dólares por una ambulancia.",
                     "Le regalas un seguro dental a cambio de quitar los aranceles."),
                ),
                Dialogue(
                    "Hacienda revisa cómo vendes la exención de los seguros de empresa.",
                    "Cumples la norma y se la explicas bien a tus clientes.",
                    ("Le ofreces a Perro Sanxe una póliza gratis.",
                     "Te llevas la sede a Andorra."),
                ),
                Dialogue(
                    "Una periodista pregunta por los partes denegados.",
                    "Das los datos y el procedimiento de reclamación.",
                    ("«Solo denegamos lo que habría que pagar».",
                     "«No me consta, no me consta, no me consta»."),
                ),
                Dialogue(
                    "La competencia lanza pólizas a 9,99 €.",
                    "Compites con servicio y con buen cuadro médico.",
                    ("Lanzas una a 9,98 € que no cubre nada.",
                     "Copias su anuncio con un perro más bonito."),
                ),
                Dialogue(
                    "Los médicos del cuadro no quieren seguir cobrando 12 € por consulta.",
                    "Subes las tarifas: sin médicos no hay seguro.",
                    ("Les pagas en vales de gasolina.",
                     "Les dices que «antes se trabajaba por vocación»."),
                ),
                Dialogue(
                    "Llega una ola de gripe y se disparan las consultas.",
                    "Refuerzas el cuadro médico y la teleconsulta.",
                    ("Subes las primas a mitad de año.", "Cierras por vacaciones hasta marzo."),
                ),
                Dialogue(
                    "Erdoğan te invita a abrir clínicas de injerto capilar en Estambul.",
                    "Le das las gracias y sigues centrado en tu mercado.",
                    ("Aceptas y pones su cara en el logo.",
                     "Te haces un injerto en directo en la reunión."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas de la hostelería (Hotelera y Alta cocina) ----------------------------

MEMORY_PACKS.update(
    {
        "pulseras": MemoryPack(
            "🛬 El vuelo de Mánchester ha aterrizado. El huésped {n} lo pide todo, en este orden:",
            "Cúmplele las peticiones en el mismo orden, que luego te las reclama en inglés.",
            (
                ("🏖️", "Hamaca en primera fila"),
                ("🧺", "Toalla en la tumbona a las 7 de la mañana"),
                ("🟢", "Pulsera del todo incluido"),
                ("🌊", "Cambio a una habitación con vistas al mar"),
                ("🍺", "Cerveza a las diez de la mañana"),
                ("🥞", "Desayuno hasta las dos de la tarde"),
                ("🧊", "Aire acondicionado a 16 °C"),
                ("🫖", "Té con leche (hirviendo)"),
                ("🍳", "Huevos fritos en el buffet"),
            ),
        ),
        "emplatado": MemoryPack(
            "🍽️ Plato {n} del menú degustación. Se emplata con pinzas, en este orden:",
            "Emplata con las pinzas en el mismo orden, que si se mueve algo se hunde la estrella.",
            (
                ("🥔", "Aire de papa arrugada"),
                ("🧪", "Esferificación de mojo"),
                ("🌱", "Tierra de almogrote"),
                ("☁️", "Espuma de gofio"),
                ("🧊", "Gel de barraquito"),
                ("🌸", "Flor comestible (sin sabor)"),
                ("🪙", "Hoja de pan de oro"),
                ("💧", "Una gota de aceite en el centro"),
                ("🫧", "Humo de nogal bajo una campana"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "quejas": SortPack(
            "📝 Eres director regional. Las reseñas llegan a tu mesa: cada una a su "
            "departamento, y a las absurdas, un «lamentamos su experiencia».",
            "⭐ Reseña {n}/{total}:",
            ("🧹 Limpieza", "🍳 Buffet", "🛎️ Recepción", "🙄 «Lamentamos su experiencia»"),
            (
                ("Encontré un pelo en la ducha. No era mío. Ni de nadie que conozca", 0),
                ("La toalla olía a humedad del siglo pasado", 0),
                ("Las sábanas tenían una mancha con forma de Galicia", 0),
                ("La habitación 214 no se limpió en cuatro días", 0),
                ("Los huevos revueltos llevaban un par de días en el buffet", 1),
                ("Se acabó el jamón a las 8:05 de la mañana", 1),
                ("La máquina de zumo hacía un ruido de moto", 1),
                ("La fuente de fruta estaba en la otra punta del comedor", 1),
                ("Esperé 40 minutos para el check-in con las maletas en la mano", 2),
                ("Me dieron una habitación con vistas a un muro y yo pedí el mar", 2),
                ("Me cobraron dos veces la tasa turística", 2),
                ("Me cambiaron de habitación a las dos de la madrugada sin avisar", 2),
                ("Llovió el día que fui a la playa. Exijo la devolución", 3),
                ("Nadie me avisó de que en la playa hay arena", 3),
                ("El mar estaba demasiado mojado", 3),
                ("Mi marido roncaba y no me dieron otro marido", 3),
            ),
        ),
        "brigada": SortPack(
            "🔪 Eres el dueño del grupo. Manda a cada uno de tu brigada a su partida (y que "
            "nadie se entere de quién cobra qué).",
            "🧑‍🍳 Cocinero {n}/{total}:",
            ("🔥 Cuarto caliente", "🥗 Cuarto frío", "🍰 Pastelería", "🧽 Office"),
            (
                ("Hace una salsa que no se corta ni a cuchilladas", 0),
                ("Controla el punto de la carne a ojo, con la plancha a 300 grados", 0),
                ("Se pasa la vida entre fogones y le encanta el humo", 0),
                ("Es el único que no se achicharra con la sartén en llamas", 0),
                ("Hace ensaladas con pinzas y un pulso de cirujano", 1),
                ("Se le dan de maravilla los ceviches y los carpaccios", 1),
                ("Corta verdura en brunoise sin mirar", 1),
                ("Monta los entrantes fríos y le tiene pánico al fuego", 1),
                ("Pesa el azúcar al gramo y no se fía de nadie", 2),
                ("Se levanta a las cuatro de la mañana a amasar", 2),
                ("Hace merengues que se aguantan solos", 2),
                ("Sueña con una tarta con forma de Falcon", 2),
                ("Es el stagier danés que vino a aprender gratis", 3),
                ("Se presentó para chef y le han dado un estropajo", 3),
                ("El becario que dijo que sabía freír un huevo", 3),
                ("El sobrino del socio, que no sabe ni encender una vitro", 3),
            ),
        ),
    }
)  # fmt: skip

#: Colados del buffet del resort: el que no lleva pulsera del todo incluido.
GATECRASHERS: tuple[str, ...] = (
    "Lleva una pulsera dibujada con rotulador verde",
    "Dice «soy de la 304», pero en tu hotel no hay habitación 304",
    "Luce la pulsera del todo incluido… de la Feria de Abril de 2016",
    "Repite por cuarta vez el plato de gambas y no tiene pulsera",
    "Su pulsera es de papel de aluminio, pegada con celo",
    "Entra por la cocina con una bandeja y una camisa de camarero (que no lo es)",
    "Dice que es primo del director, y es lo único que dice",
    "Cena en el buffet con la pulsera de la piscina de otro hotel",
    "Se llena los bolsillos de bocadillos con la servilleta al cuello",
)
GUESTS_LEGIT: tuple[str, ...] = (
    "Pulsera verde del todo incluido, bañador y un plato de huevos",
    "Familia de cuatro con pulseras a juego y los niños con zumo",
    "Pareja de jubilados con pulsera y su propio salero",
    "Despedida de soltero con pulsera y resaca",
    "Huésped con pulsera y la tarjeta de la habitación en la mano",
    "Un señor con pulsera, chancletas y dos platos (con pulsera, no pasa nada)",
    "Mujer con pulsera y un periódico de hace tres días",
    "Matrimonio de Mánchester con pulsera y cerveza a las nueve de la mañana",
    "Niño con pulsera y un plato de patatas fritas, y nada más",
    "Huésped con pulsera dorada de suite y tostada con aguacate",
    "Recién casados con pulsera y cara de no haber dormido",
    "Abuela con pulsera que guarda una naranja en el bolso",
)

#: Los inspectores de la Guía tienen sus manías; los demás comensales, las suyas.
INSPECTOR_TELLS: tuple[str, ...] = (
    "Come solo, apunta en una libreta y pide el menú largo",
    "Hace fotos de cada plato con una cámara de verdad, no con el móvil",
    "Pregunta de dónde viene la sal y habla en voz baja",
    "Pide agua del grifo, mira el mantel a contraluz y cuenta los cubiertos",
    "Reserva a nombre de «Sr. Fernández» y paga siempre con tarjeta de empresa",
    "Mastica cada bocado como si le dictaran sentencia",
    "Va al baño tres veces y vuelve con la libreta más gorda",
    "Se pasa la servilleta por la copa y la mira a contraluz",
    "Pide una copa de vino y no la bebe: la huele y la apunta",
)
DINERS_NORMAL: tuple[str, ...] = (
    "Pareja que se hace un selfie con la tarta",
    "Grupo de ocho celebrando una comunión a gritos",
    "Influencer que graba la carta con el móvil mientras pide",
    "Un cuñado que dice que él lo haría mejor",
    "Pareja de aniversario que se mira y no habla",
    "Familia de turistas que pide ketchup para el solomillo",
    "Un abuelo que pregunta si hay papas arrugadas",
    "Ejecutivo que habla por teléfono a gritos",
    "Dos amigos que se pelean por pagar la cuenta",
    "Un niño que pide macarrones en un restaurante de tres tenedores",
    "Señora que pide la salsa aparte, el pan aparte y el plato aparte",
    "Pareja que pide «el vino más caro» y lo mezcla con cola",
)

DIALOGUE_PACKS.update(
    {
        "cadena_hotelera": DialoguePack(
            "🏢 Junta con los inversores. Regla de oro: **lo que cuesta dinero es «inversión» "
            "y lo que se ahorra, «eficiencia».**",
            (
                Dialogue(
                    "Un inversor pregunta qué piensas hacer con los pisos turísticos de la "
                    "competencia.",
                    "Pides que se regule por igual y compites con calidad.",
                    ("Compras cien pisos y los llamas «hoteles boutique».",
                     "Les mandas una paella con chorizo de bienvenida."),
                ),
                Dialogue(
                    "La Ley de Costas amenaza el hotel de la playa.",
                    "Pagas un informe jurídico y presentas alegaciones en plazo.",
                    ("Mueves la playa dos metros, que es más barato.",
                     "Dices que la arena es tuya por usucapión."),
                ),
                Dialogue(
                    "Las kellys piden un convenio digno.",
                    "Te sientas con ellas y negocias de verdad.",
                    ("Las externalizas a una subcontrata fantasma.",
                     "Les subes la limpieza a veinte habitaciones por hora."),
                ),
                Dialogue(
                    "Los accionistas quieren más margen este trimestre.",
                    "Les enseñas el plan de inversión a cinco años con números.",
                    ("Cobras la toalla por separado.",
                     "Quitas el jabón y lo vendes como «ducha ecológica»."),
                ),
                Dialogue(
                    "El Gobierno canario anuncia una ecotasa.",
                    "Estudias su impacto y la trasladas con transparencia.",
                    ("Dices que Canarias «tiene un límite» y que ese límite es tu cuenta.",
                     "Llevas el hotel a Andorra."),
                ),
                Dialogue(
                    "Una agencia online te exige el 22 % de comisión.",
                    "Negocias y pones tu propia web de reservas.",
                    ("Cobras un 22 % más a los clientes.",
                     "Te inventas una web que solo funciona los martes."),
                ),
                Dialogue(
                    "Se descubre que el hotel sale con 300 reseñas falsas de cinco estrellas.",
                    "Las retiras y pides perdón públicamente.",
                    ("Compras 3.000 más para diluir.", "Culpas a la competencia (o a Rusia)."),
                ),
                Dialogue(
                    "Un fondo quiere comprar el 30 % de la cadena.",
                    "Pides condiciones por escrito: plantilla, marca y plazos.",
                    ("Le vendes el 130 % y sales corriendo.",
                     "Lo celebras con un brindis y una firma en una servilleta."),
                ),
                Dialogue(
                    "Hay que cambiar todas las camas, pero el presupuesto no da.",
                    "Cambias por fases y empiezas por las peores habitaciones.",
                    ("Le das la vuelta al colchón y lo llamas «renovación».",
                     "Pones camas de campaña y un cartel de «glamping»."),
                ),
                Dialogue(
                    "Un periodista pregunta por los salarios de los camareros de temporada.",
                    "Das los datos reales y el plan de subidas.",
                    ("«La hostelería es una vocación, no un trabajo».",
                     "«Nadie quiere trabajar» y cuelgas el teléfono."),
                ),
            ),
        ),
        "patronal": DialoguePack(
            "🏛️ Mesa con el Gobierno. Regla de oro: **siempre hay crisis… salvo en el "
            "balance.**",
            (
                Dialogue(
                    "El Gobierno propone subir la tasa turística.",
                    "Propones destinarla a infraestructuras y que se publique el gasto.",
                    ("Dices que el turista se irá a Marruecos de golpe y porrazo.",
                     "Pides un abrazo con el ministro delante de las cámaras."),
                ),
                Dialogue(
                    "El ministro quiere la foto con la patronal para presumir de acuerdo.",
                    "Pones tus condiciones antes de posar.",
                    ("Sales sonriendo y firmas lo que sea.", "Te haces un selfie con él."),
                ),
                Dialogue(
                    "Los sindicatos piden subir el convenio un 12 %.",
                    "Contraoferta razonable, con revisión según la inflación.",
                    ("«No encontramos camareros»… pagando 900 euros.",
                     "Les ofreces una cesta de Navidad con turrón de Alicante."),
                ),
                Dialogue(
                    "Una oleada de turismofobia llena las calles de pancartas.",
                    "Abres una mesa con vecinos para reparar el impacto del turismo.",
                    ("Dices que quien protesta no sabe dónde está su pan.",
                     "Pones un cartel de «Welcome» que dice «Go home»."),
                ),
                Dialogue(
                    "El Gobierno plantea limitar los pisos turísticos.",
                    "Apoyas la regulación a condición de que sea clara y para todos.",
                    ("Dices que es el fin del turismo, de la paella y de España.",
                     "Compras diez edificios enteros esa misma tarde."),
                ),
                Dialogue(
                    "Te piden que expliques por qué suben los precios de las habitaciones.",
                    "Enseñas los costes: energía, personal y suministros.",
                    ("«Es que la gente quiere pagar más».",
                     "Cobras la vista al mar en euros por mirada."),
                ),
                Dialogue(
                    "Llega un ministro de otro país a visitar el sector.",
                    "Le enseñas las buenas prácticas y los retos pendientes.",
                    ("Le regalas un jamón y una mordida.",
                     "Le dices que Benidorm es el modelo de Dubái."),
                ),
                Dialogue(
                    "Hay que repartir los fondos europeos para modernizar hoteles.",
                    "Proyectos de eficiencia energética con control del gasto.",
                    ("Pides la piscina climatizada que lleva años en tu chalet.",
                     "Los gastas en consultoras que hagan un informe sobre el informe."),
                ),
                Dialogue(
                    "Te preguntan por el Falcon, por si te sumas.",
                    "Dices que tú viajas en vuelo regular, como todo el mundo.",
                    ("Aseguras que «el Falcon es un transporte de interés turístico».",
                     "Pides un Falcon para el sector."),
                ),
                Dialogue(
                    "Aparece un informe: la hostelería gana mucho y paga poco.",
                    "Lo estudias, lo reconoces y pones una subida a la mesa.",
                    ("Dices que el informe lo ha hecho un camarero resentido.",
                     "Demuestras con un gráfico que el eje vertical está al revés."),
                ),
            ),
        ),
        "plato_tv": DialoguePack(
            "📺 Gran final del talent de cocina. Regla de oro: **humilla con gracia: el "
            "público quiere sangre, pero en cámara lenta.**",
            (
                Dialogue(
                    "Un concursante te presenta un plato crudo por dentro.",
                    "Le explicas qué falla y cómo arreglarlo, con respeto.",
                    ("Lo tiras al suelo en directo.", "«Esto lo cocinaría yo… en la cárcel»."),
                ),
                Dialogue(
                    "Una concursante llora porque se le ha quemado el flan.",
                    "La consuelas y le das una última oportunidad.",
                    ("Subes la música dramática y pones primer plano.",
                     "Le dices que llorar no sube la nota (y sí la audiencia)."),
                ),
                Dialogue(
                    "El productor te pide que grites más: la audiencia baja.",
                    "Propones un formato mejor sin gritos.",
                    ("Gritas al jurado.", "Gritas a la cámara, que no tiene la culpa."),
                ),
                Dialogue(
                    "Un concursante recita una receta de su abuela con lágrimas.",
                    "La pruebas y la valoras por lo que sabe.",
                    ("«La receta de tu abuela era mejor».",
                     "Le preguntas por el nombre de su abuela para el titular."),
                ),
                Dialogue(
                    "La marca patrocinadora quiere que uses su aceite en todos los platos.",
                    "Lo dices en pantalla: es publicidad.",
                    ("Lo usas hasta en el postre y sonríes como un lagarto.",
                     "Dices que «es el mejor aceite del mundo» sin catarlo."),
                ),
                Dialogue(
                    "Un concursante cocina con un ingrediente que no está en la lista.",
                    "Lo penalizas, como en el reglamento.",
                    ("Lo expulsas y le quitas los cuchillos con ceremonia.",
                     "Lo premias, que da más morbo."),
                ),
                Dialogue(
                    "Llega un chef invitado y te corrige un plato en directo.",
                    "Le das las gracias y aprendes algo.",
                    ("Le tiras una espátula.",
                     "Le enseñas tu estrella como quien enseña un carnet."),
                ),
                Dialogue(
                    "El público vota por el concursante que cocina peor.",
                    "Respetas el voto y lo animas a mejorar.",
                    ("Anulas el voto «por irregularidades», como Maduro con las actas.",
                     "Dices que la audiencia no sabe de cocina."),
                ),
                Dialogue(
                    "Tu antiguo jefe de cocina aparece en el programa a reclamarte un sueldo.",
                    "Hablas con él después de grabar, con calma.",
                    ("Dices que no le conoces.", "Lo presentas como concursante y lo eliminas."),
                ),
            ),
        ),
        "dubai": DialoguePack(
            "🏜️ Tu restaurante de Dubái. Regla de oro: **si no lleva pan de oro, todavía no "
            "está terminado.**",
            (
                Dialogue(
                    "Un jeque quiere pan de oro hasta en el agua.",
                    "Se lo pones donde queda bien: en el postre y en el cóctel.",
                    ("Le sirves un lingote con una gota de vinagre.",
                     "Le pones oro y a los camareros también."),
                ),
                Dialogue(
                    "Un emir pide papas arrugadas con caviar.",
                    "Las haces con sal de verdad y caviar al lado, que no se mezcla.",
                    ("Le dices que las papas son un símbolo nacional y las cobras al peso.",
                     "Le preparas papas con mojo de oro."),
                ),
                Dialogue(
                    "Un millonario quiere que le vendas el plato como NFT.",
                    "Le explicas que un plato se come.",
                    ("Le vendes el plato, el NFT y el aire que lo rodea.",
                     "Se lo vendes en dos tokens, uno por mitad."),
                ),
                Dialogue(
                    "Elon Musk tuitea que va a abrir una cadena de espumas en Marte.",
                    "Le deseas suerte y sigues cocinando.",
                    ("Le pides el 20 % del mercado marciano.",
                     "Le propones comprar tu espuma de gofio en cohete."),
                ),
                Dialogue(
                    "Un influencer pide el menú de 1.500 € «para hacer un unboxing».",
                    "Le cobras el menú, con factura y todo.",
                    ("Se lo regalas a cambio de una story.", "Le cobras también la cámara."),
                ),
                Dialogue(
                    "Un socio quiere franquiciar tu plato estrella en un centro comercial.",
                    "Le pones controles de calidad y una cláusula clara.",
                    ("Aceptas: «un centro comercial es un restaurante con más suelo».",
                     "Lo pones en la carta de comida rápida."),
                ),
                Dialogue(
                    "Un crítico escribe que tu espuma «sabe a jabón».",
                    "Reconoces el error y revisas la receta.",
                    ("Le vas a pagar la reseña con pan de oro.",
                     "Demuestras que el jabón es tendencia."),
                ),
                Dialogue(
                    "Te invitan a cocinar para una cumbre donde no cabe una cuchara.",
                    "Preparas un menú sencillo y acorde con el evento.",
                    ("Lo haces con pan de oro y una pirámide de caviar.",
                     "Sirves comida de avión para que se sientan en casa."),
                ),
                Dialogue(
                    "Hacienda de tu país pregunta dónde tributa tu franquicia.",
                    "Presentas la documentación y tributas donde toca.",
                    ("Pones la sede en una isla desierta con wifi.",
                     "Dices que vives en un avión sin domicilio fiscal."),
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
        branches: Ramas donde sale (vacío = en cualquiera y en el tronco).
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
    branches: tuple[str, ...] = ()


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
    Event(
        "sin_factura",
        "🧾 Un cliente te pide la reforma del baño «sin factura, que sale más barato».",
        (
            (
                "Sin factura",
                Outcome(
                    "Sin factura, sin IGIC y sin garantía. Cobras en un sobre de la panadería.",
                    black=0.8,
                    risk=0.2,
                    stat="work_no_invoice",
                ),
            ),
            (
                "Con factura",
                Outcome(
                    "Factura con IGIC. El cliente te mira como si fueras Hacienda.",
                    pay=0.4,
                    performance=-5,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=5,
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
    # -- Hostelería: más marrones del tronco ---------------------------------------------
    Event(
        "contrato_4h",
        "📑 Te ofrecen un contrato de cuatro horas. Trabajando, serán diez. «Es lo que hay».",
        (
            (
                "Firmo y curro diez",
                Outcome(
                    "Firmas. Cuatro horas en la nómina y seis en un sobre. Inspección de "
                    "Trabajo, de momento, está de bocadillo.",
                    pay=0.3,
                    black=0.7,
                    risk=0.2,
                    battery=-15,
                    family=-5,
                    stat="work_four_hours",
                ),
            ),
            (
                "Contrato de verdad",
                Outcome(
                    "Exiges el contrato de jornada completa. Te miran como si hubieras "
                    "pedido caviar en el menú del día.",
                    performance=-8,
                    family=5,
                    stat="work_full_contract",
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "paella_chorizo",
        "🥘 A las once de la mañana, un guiri pide paella… con chorizo. «Like in London».",
        (
            (
                "Se la hago",
                Outcome(
                    "Le haces la paella con chorizo. Lo publica en redes y media Valencia "
                    "te manda amenazas con buena ortografía.",
                    pay=0.4,
                    performance=-5,
                    stat="work_chorizo_paella",
                ),
            ),
            (
                "Ni hablar",
                Outcome(
                    "Le dices que la paella no se toca. Se va a un sitio con paella "
                    "congelada y cinco estrellas.",
                    performance=5,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "terraza_metro",
        "📏 La policía local aparece con un metro y mide la terraza: tienes tres mesas de más.",
        (
            (
                "Mesas de más",
                Outcome(
                    "Dices que «son decorativas» y las dejas. El agente anota algo en la "
                    "libreta y no es tu teléfono.",
                    pay=0.6,
                    risk=0.3,
                    fine=1.0,
                    caught_by="terraza",
                    stat="work_extra_tables",
                ),
            ),
            (
                "Recoger mesas",
                Outcome(
                    "Recoges las tres mesas. El metro se va, pero los clientes se sientan "
                    "en el suelo.",
                    performance=-5,
                    battery=-10,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=3,
    ),
    Event(
        "resena_una",
        "⭐ Un cliente amenaza: «Si no me haces descuento, os pongo una estrella en las reseñas».",
        (
            (
                "Postre invitado",
                Outcome(
                    "Le invitas al postre. La reseña sale de cinco estrellas y habla de "
                    "«un trato familiar» (el de una familia mafiosa).",
                    performance=8,
                    stat="work_review_bribe",
                ),
            ),
            (
                "Que la ponga",
                Outcome(
                    "«Pon lo que quieras». Pone una estrella, una queja de la sal y una foto "
                    "de otro restaurante.",
                    performance=-8,
                    family=3,
                    stat="work_one_star",
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "influencer_gratis",
        "🤳 Una influencer quiere comer gratis: «Os doy visibilidad, que tengo doce mil "
        "seguidores (nueve mil son bots)».",
        (
            (
                "Come gratis",
                Outcome(
                    "Le pones el menú entero. Sube una story de dos segundos con el plato "
                    "borroso y tu local sale como «un sitio».",
                    performance=-5,
                    stat="work_free_dinner",
                ),
            ),
            (
                "Pasa por caja",
                Outcome(
                    "Le pasas la cuenta. Te deja de seguir y 40 bots te dan un me gusta de "
                    "consolación.",
                    pay=0.3,
                    performance=5,
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "crucero",
        "🚢 Atraca un crucero con 4.000 pasajeros. Traen el desayuno, el almuerzo y la cena "
        "pagados en el barco.",
        (
            (
                "Menú para cruceristas",
                Outcome(
                    "Montas un menú a 4,99 €. Venden 4.000 cafés solos y el que paga la "
                    "luz es tu bolsillo.",
                    pay=0.5,
                    battery=-15,
                    stat="work_cruise_menu",
                ),
            ),
            (
                "Cierro y me voy",
                Outcome(
                    "Cierras el chiringuito y te vas a la playa. Cuatro mil personas pasan "
                    "de largo, con su bolsa de plástico y su botella de agua del barco.",
                    performance=-8,
                    family=8,
                    stat="work_cruise_closed",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=2,
    ),
    Event(
        "ola_calor",
        "🔥 Ola de calor: 48 °C dentro de la cocina. El extractor se ha rendido y los "
        "cocineros también.",
        (
            (
                "Cocino igual",
                Outcome(
                    "Cocinas con una toalla en la cabeza. Sirves 80 menús y pierdes dos "
                    "kilos de agua.",
                    pay=0.5,
                    battery=-25,
                    stat="work_heatwave_cook",
                ),
            ),
            (
                "Cierro la cocina",
                Outcome(
                    "Cierras la cocina y abres solo bebidas frías. Todo el mundo está más "
                    "contento menos tu jefe.",
                    performance=-8,
                    family=8,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=4,
    ),
    Event(
        "no_camareros",
        "🪧 Un cliente dice a su tertulia por radio: «No encuentro camareros». Tienes un "
        "cartel en la puerta que ofrece 900 € al mes.",
        (
            (
                "Subo el sueldo",
                Outcome(
                    "Subes el sueldo a 1.400 € y aparecen veinte currículums en una tarde. "
                    "Casualidades de la vida.",
                    performance=10,
                    family=5,
                    stat="work_waiters_paid",
                ),
            ),
            (
                "Mantengo el cartel",
                Outcome(
                    "Mantienes los 900 € y el cartel. Sigues sin camareros y te quejas "
                    "en la tele de la juventud.",
                    performance=-8,
                    stat="work_no_waiters",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=5,
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
                    stat="work_cronyism_refused",
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
            (
                "Voy en guagua",
                Outcome(
                    "Vas en guagua. Llegas tarde y sin batería.",
                    battery=-15,
                    stat="work_falcon_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=4,
    ),
    Event(
        "fango",
        "📰 Un digital publica que tienes un yate en Marbella. No tienes ni bici.",
        (
            (
                "Máquina del fango",
                Outcome(
                    "Denuncias la máquina del fango en un vídeo de cinco minutos. El bulo se "
                    "hace el doble de famoso, pero los tuyos te aplauden.",
                    performance=10,
                    stat="work_fango",
                ),
            ),
            (
                "Lo desmiento con papeles",
                Outcome(
                    "Publicas tu declaración de bienes. Nadie la lee; todos comparten el bulo.",
                    performance=-5,
                    battery=-5,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=2,
    ),
    Event(
        "pegasus",
        "📱 Tu móvil se calienta solo y pita de madrugada. Los de informática susurran: «Pegasus».",
        (
            (
                "Móvil nuevo",
                Outcome(
                    "Te compras otro móvil y metes el viejo en la nevera. Por si acaso.",
                    performance=5,
                    battery=-5,
                    stat="work_pegasus",
                ),
            ),
            (
                "Que escuchen",
                Outcome(
                    "Que escuchen lo aburrida que es tu agenda: plenos, comidas y la siesta. "
                    "En algún despacho, alguien bosteza.",
                    performance=-5,
                    family=-3,
                    stat="work_pegasus_listened",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=3,
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
    # -- Sanidad: más marrones del tronco ------------------------------------------------
    Event(
        "contrato_dia",
        "📞 Son las siete de la mañana y te ofrecen un contrato de un día. El undécimo este mes.",
        (
            (
                "Lo cojo",
                Outcome(
                    "Lo coges. Te firman el contrato, el finiquito y el siguiente contrato en "
                    "la misma hoja.",
                    pay=0.5,
                    battery=-10,
                    family=-5,
                    stat="work_one_day_contract",
                ),
            ),
            (
                "Paso",
                Outcome("Pasas. Te bajan en la bolsa de empleo hasta 2031.", performance=-5),
            ),
        ),
        jobs=("sanidad",),
        min_level=2,
    ),
    Event(
        "selfie_quirofano",
        "🤳 Un compañero quiere grabar un baile en el quirófano para las redes.",
        (
            (
                "Bailo",
                Outcome(
                    "Bailáis con las mascarillas puestas. Dos millones de visitas y una "
                    "reunión con la supervisora.",
                    performance=-5,
                    family=5,
                    stat="work_or_dance",
                ),
            ),
            (
                "Ni de broma",
                Outcome(
                    "Le recuerdas que el paciente sigue dormido. Graba él solo.", performance=5
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=3,
    ),
    Event(
        "cama_guardias",
        "🛏️ La cama de la sala de guardias huele a 1987 y cruje como un barco.",
        (
            (
                "Dormir igual",
                Outcome(
                    "Duermes dos horas con la bata de almohada. Hueles a 1987.",
                    battery=15,
                    stat="work_guard_bed",
                ),
            ),
            (
                "Silla del control",
                Outcome(
                    "Cabeceas en la silla del control. Te despierta el timbre de la 214.",
                    battery=-5,
                    performance=5,
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=2,
    ),
    # -- Sanidad: la pública -------------------------------------------------------------
    Event(
        "pasillo",
        "🛏️ Urgencias, colapsada: hay camas en el pasillo hasta la máquina de café.",
        (
            (
                "Abrir la planta cerrada",
                Outcome(
                    "Abres la planta cerrada «por obras» desde 2019. Las obras eran una "
                    "cinta de plástico.",
                    performance=15,
                    battery=-15,
                    stat="work_ward_opened",
                ),
            ),
            (
                "«Situación puntual»",
                Outcome(
                    "Sale tu comunicado: «situación puntual». Es la duodécima puntual del año.",
                    performance=5,
                    stat="work_hallway_beds",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=6,
        branches=("publica",),
    ),
    Event(
        "derivar_privada",
        "📋 La Consejería te pide derivar 500 operaciones a la privada «para bajar la lista».",
        (
            (
                "Derivar",
                Outcome(
                    "Las derivas. La lista baja, el gasto sube y tus quirófanos siguen "
                    "cerrados por la tarde.",
                    pay=0.5,
                    performance=10,
                    stat="work_referred_private",
                ),
            ),
            (
                "Abrir por la tarde",
                Outcome(
                    "Abres tus quirófanos por la tarde. La lista baja y en casa ya no te ven "
                    "el pelo.",
                    performance=15,
                    battery=-15,
                    family=-5,
                    stat="work_afternoon_ops",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=7,
        branches=("publica",),
    ),
    Event(
        "mascarillas",
        "😷 Un «empresario» que ayer vendía colchones te ofrece mascarillas a precio de oro, "
        "con comisión para el que lo presenta.",
        (
            (
                "Comprar",
                Outcome(
                    "Compras. Las mascarillas llegan tarde y el intermediario se compra un "
                    "Rolls. Te toca un sobre.",
                    black=2.0,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_masks",
                ),
            ),
            (
                "Concurso público",
                Outcome(
                    "Concurso público con tres ofertas. Llegan una semana más tarde y a la "
                    "cuarta parte de precio.",
                    performance=-5,
                    stat="work_masks_refused",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=8,
        branches=("publica",),
    ),
    Event(
        "hospital_vacio",
        "✂️ Toca inaugurar el hospital nuevo antes de las elecciones. Faltan el personal, el "
        "material y la mitad de los ascensores.",
        (
            (
                "Cortar la cinta",
                Outcome(
                    "Cinta, tijeras y foto en la entrada. Al día siguiente lo cerráis «para "
                    "ajustes».",
                    pay=0.4,
                    performance=10,
                    stat="work_empty_hospital",
                ),
            ),
            (
                "Cuando haya personal",
                Outcome(
                    "Esperas a tener plantilla. El consejero inaugura otro hospital vacío en "
                    "la isla de al lado.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=9,
        branches=("publica",),
    ),
    Event(
        "giratoria_salud",
        "💼 Una aseguradora te ofrece un sillón en su consejo… para cuando dejes la "
        "Consejería. Que firmes ya, por si acaso.",
        (
            (
                "Firmo el precontrato",
                Outcome(
                    "Firmas con el cargo todavía caliente. Te mandan una cesta de Navidad del "
                    "tamaño de un coche.",
                    black=1.5,
                    risk=0.2,
                    caught_by="incompatibilidades",
                    stat="work_revolving_health",
                ),
            ),
            (
                "Ni hablar",
                Outcome(
                    "Les dices que hay periodo de incompatibilidad. Te miran como si "
                    "hablaras en latín.",
                    performance=-5,
                    stat="work_revolving_refused",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=10,
        branches=("publica",),
    ),
    # -- Sanidad: la privada -------------------------------------------------------------
    Event(
        "poliza_denegada",
        "📄 Un asegurado de 30 años necesita una prueba cara. La póliza tiene una cláusula "
        "en letra de cuerpo 4.",
        (
            (
                "Denegar por la cláusula",
                Outcome(
                    "Se la deniegas por la cláusula 47.3.b. La aseguradora te manda un jamón.",
                    pay=0.6,
                    performance=10,
                    risk=0.2,
                    fine=1.0,
                    caught_by="abusivas",
                    stat="work_claim_denied",
                ),
            ),
            (
                "Cubrirla",
                Outcome(
                    "La autorizas. El paciente se cura y la aseguradora te manda una circular.",
                    performance=-10,
                    family=5,
                    stat="work_claim_paid",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=6,
        branches=("privada",),
    ),
    Event(
        "canje",
        "🤳 Una influencer quiere un retoque gratis «a cambio de visibilidad».",
        (
            (
                "Canje por stories",
                Outcome(
                    "Diez stories con tu clínica de fondo. Te llegan doscientos mensajes "
                    "pidiendo lo mismo gratis.",
                    performance=10,
                    stat="work_canje",
                ),
            ),
            (
                "Con factura",
                Outcome(
                    "Le pasas la tarifa. Te deja de seguir y te pone una estrella en las reseñas.",
                    pay=0.5,
                    performance=-5,
                    stat="work_canje_refused",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=7,
        branches=("privada",),
    ),
    Event(
        "turquia",
        "🧑‍🦲 Medio pueblo vuelve de Estambul con un injerto capilar torcido y te piden "
        "arreglarlo.",
        (
            (
                "Arreglarlos",
                Outcome(
                    "Arreglas doce cabezas a precio de urgencia. Erdoğan, sin saberlo, es tu "
                    "mejor comercial.",
                    pay=0.8,
                    battery=-10,
                    stat="work_turkey_fix",
                ),
            ),
            (
                "Que reclamen allí",
                Outcome(
                    "Les das el teléfono de la clínica de Estambul. Contesta un contestador en "
                    "turco.",
                    family=5,
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=7,
        branches=("privada",),
    ),
    Event(
        "ohio",
        "🇺🇸 Un turista de EE. UU. ve tu factura y se echa a llorar: «En Ohio esto me costaría "
        "40.000 dólares».",
        (
            (
                "Tarifa de Ohio",
                Outcome(
                    "Le cobras «la tarifa internacional». Te da las gracias y 25 % de propina.",
                    pay=0.8,
                    risk=0.2,
                    fine=1.0,
                    caught_by="abusivas",
                    stat="work_ohio_rate",
                ),
            ),
            (
                "Tarifa normal",
                Outcome(
                    "Le cobras lo normal. Te abraza y dice que se muda a Canarias.",
                    family=5,
                    stat="work_fair_rate",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=8,
        branches=("privada",),
    ),
    Event(
        "concierto_sobre",
        "🤝 Un alto cargo de la Comunidad te insinúa que el concierto se renueva antes «con "
        "un detalle».",
        (
            (
                "Un detalle",
                Outcome(
                    "Un detalle en un sobre y el concierto renovado por diez años. La UCO "
                    "también tiene agenda.",
                    black=2.5,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_concert_envelope",
                ),
            ),
            (
                "Por concurso",
                Outcome(
                    "Te presentas al concurso como todo el mundo. Lo ganas por los pelos y "
                    "sin sobre.",
                    performance=-10,
                    stat="work_concert_clean",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=9,
        branches=("privada",),
    ),
    Event(
        "fondo_salud",
        "🦅 Un fondo de inversión quiere comprar tu grupo hospitalario y «optimizar» la plantilla.",
        (
            (
                "Vender al fondo",
                Outcome(
                    "Vendes. Al mes, una enfermera atiende tres plantas y el fondo reparte "
                    "dividendos.",
                    pay=1.0,
                    performance=-5,
                    family=-5,
                    stat="work_health_fund_sale",
                ),
            ),
            (
                "La plantilla primero",
                Outcome(
                    "Le dices que tu plantilla no se toca. El fondo compra al de enfrente.",
                    performance=-5,
                    family=5,
                    stat="work_staff_first",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=9,
        branches=("privada",),
    ),
    # -- Hostelería: la rama Hotelera ----------------------------------------------------
    Event(
        "balconing",
        "🩴 Dos huéspedes de la 405 quieren saltar del balcón a la piscina, que está tres "
        "pisos más abajo. Es su primera noche.",
        (
            (
                "Sellar los balcones",
                Outcome(
                    "Mandas soldar las barandillas. Los de la 405 se quejan: «¡Nos habéis "
                    "quitado la mitad de las vistas!».",
                    performance=10,
                    battery=-10,
                    stat="work_balconing_sealed",
                ),
            ),
            (
                "Cartel en cinco idiomas",
                Outcome(
                    "Pones un cartel en cinco idiomas. Lo leen del revés y siguen con el plan.",
                    performance=-5,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=6,
        branches=("hotel",),
    ),
    Event(
        "overbooking",
        "🛏️ Overbooking: has vendido 40 habitaciones de más y llegan tres autobuses a la vez.",
        (
            (
                "Al hotel de enfrente",
                Outcome(
                    "Los mandas al hotel de enfrente, con un cheque de disculpa. Al día "
                    "siguiente es tu competencia la que te manda un cheque.",
                    pay=0.3,
                    performance=8,
                    stat="work_overbook_across",
                ),
            ),
            (
                "Al de dos estrellas",
                Outcome(
                    "Los mandas a un hotel de dos estrellas «de 5 estrellas, pero de la "
                    "carretera». Cuatro reseñas hablan de una cucaracha con nombre propio.",
                    performance=-10,
                    stat="work_overbook_two_star",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=6,
        branches=("hotel",),
    ),
    Event(
        "tasa_turistica",
        "💶 El Gobierno autonómico aprueba la tasa turística: dos euros por noche y persona.",
        (
            (
                "Se la cobro al cliente",
                Outcome(
                    "La repercutes en la factura con un recargo de «gastos de gestión». "
                    "Un cliente te dice que en Barcelona es peor, y no lo es.",
                    pay=0.4,
                    performance=-5,
                    stat="work_tourist_tax_passed",
                ),
            ),
            (
                "La pago yo",
                Outcome(
                    "La pagas tú, a cambio de un titular: «El hotel que no cobra la tasa». "
                    "El contable te manda un correo en mayúsculas.",
                    performance=10,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=7,
        branches=("hotel",),
    ),
    Event(
        "costas",
        "🏖️ La Ley de Costas manda demoler la terraza de tu hotel de primera línea: «está "
        "en dominio público».",
        (
            (
                "Demoler",
                Outcome(
                    "Demueles la terraza. La arena vuelve donde estaba y los clientes de "
                    "pensión completa, también.",
                    performance=-10,
                    battery=-15,
                    stat="work_costas_demolish",
                ),
            ),
            (
                "Recurrir veinte años",
                Outcome(
                    "Recurres. Veinte años de juzgados, tres abogados y una terraza "
                    "inmortal. Costas, mientras tanto, te manda una carta de las suyas.",
                    pay=0.5,
                    risk=0.25,
                    fine=1.2,
                    caught_by="costas",
                    stat="work_costas_appeal",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=8,
        branches=("hotel",),
    ),
    Event(
        "kellys",
        "🧹 Las kellys piden dejar de limpiar veinte habitaciones por turno. «Con dignidad», "
        "dicen.",
        (
            (
                "Externalizar",
                Outcome(
                    "Externalizas el servicio a una empresa multiservicios. Limpian lo "
                    "mismo, cobran menos y nadie sabe quién es su jefe.",
                    pay=0.8,
                    performance=-5,
                    family=-5,
                    stat="work_kellys_outsourced",
                ),
            ),
            (
                "Contratarlas",
                Outcome(
                    "Las contratas con el convenio. Los números se resienten, el hotel "
                    "huele a limpio y en la plantilla hay buen ambiente.",
                    performance=10,
                    family=5,
                    stat="work_kellys_hired",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=8,
        branches=("hotel",),
    ),
    Event(
        "falcon_huesped",
        "✈️ El presidente del Gobierno quiere pasar agosto en tu hotel. Llega en el Falcon "
        "y pide la suite presidencial «de cortesía».",
        (
            (
                "Suite gratis",
                Outcome(
                    "Suite, pensión completa y spa, todo de cortesía. Sales en una foto "
                    "con él (de espaldas, como siempre).",
                    pay=0.5,
                    performance=12,
                    family=-5,
                    stat="work_falcon_guest",
                ),
            ),
            (
                "Se lo cobro",
                Outcome(
                    "Se lo cobras como a cualquiera. La factura la paga «el partido» y "
                    "al mes te piden «una revisión del modelo de colaboración».",
                    performance=-10,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=9,
        branches=("hotel",),
    ),
    Event(
        "lobby_pisos",
        "🏛️ El Ayuntamiento va a limitar los pisos turísticos. Un concejal te insinúa que "
        "«se podría hablar», con un sobre de por medio.",
        (
            (
                "Un sobre para el concejal",
                Outcome(
                    "Un sobre en una bolsa de paella. La norma se retrasa seis meses y "
                    "la UCO te tiene en la agenda.",
                    black=2.5,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_lobby_envelope",
                ),
            ),
            (
                "Alegaciones como todos",
                Outcome(
                    "Presentas alegaciones por la vía legal. Te las leen, te dan las "
                    "gracias y no cambia nada.",
                    performance=-8,
                    stat="work_lobby_clean",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=9,
        branches=("hotel",),
    ),
    Event(
        "canarias_limite",
        "📢 Una manifestación bajo el lema «Canarias tiene un límite» llega a la puerta de "
        "tu hotel con pancartas y cacerolas.",
        (
            (
                "Pancarta de apoyo",
                Outcome(
                    "Cuelgas una pancarta que dice «Canarias tiene un límite (de "
                    "habitaciones: 1.200)». Los manifestantes no se ríen.",
                    performance=-5,
                    family=5,
                    stat="work_limit_banner",
                ),
            ),
            (
                "Llamar a la policía",
                Outcome(
                    "Llamas a la policía. Salen en las noticias frente a tu lobby y tu "
                    "reputación en las reseñas baja un punto.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=10,
        branches=("hotel",),
    ),
    # -- Hostelería: la rama Alta cocina -----------------------------------------------------
    Event(
        "stagier",
        "🧑‍🍳 Llega un stagier de fuera: quiere trabajar seis meses gratis «por la "
        "experiencia». En el currículum pone «chef en ciernes».",
        (
            (
                "Sin contrato, sin sueldo",
                Outcome(
                    "Seis meses sin sueldo ni contrato, pero con muchas ganas. Inspección "
                    "de Trabajo tiene un olfato muy fino.",
                    black=1.0,
                    risk=0.25,
                    caught_by="inspeccion",
                    stat="work_stagier_free",
                ),
            ),
            (
                "Con contrato de prácticas",
                Outcome(
                    "Le haces contrato de prácticas, con sueldo. El stagier cree que es "
                    "una broma y te lleva a casa un tupper de agradecimiento.",
                    performance=-5,
                    family=5,
                    stat="work_stagier_paid",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=6,
        branches=("estrella",),
    ),
    Event(
        "inspector_guia",
        "🔎 Sospechas que la mesa 4 es el inspector de la Guía. Pide el menú largo y lo "
        "apunta todo en una libreta.",
        (
            (
                "Todo el trato de gala",
                Outcome(
                    "Pones un cubierto de más, una trufa de más y una sonrisa de más. "
                    "Al final era un jubilado con diabetes que apuntaba su medicación.",
                    performance=12,
                    battery=-20,
                    stat="work_guide_pampered",
                ),
            ),
            (
                "Como a cualquiera",
                Outcome(
                    "Le tratas igual que a los demás. Resulta que era el inspector y tu "
                    "estrella tiembla como una gelatina.",
                    performance=-8,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=7,
        branches=("estrella",),
    ),
    Event(
        "alergico",
        "🤧 Un cliente es alérgico al gluten, la lactosa, los frutos secos, el marisco y «a "
        "algunos ruidos». Reserva el menú degustación.",
        (
            (
                "Menú a medida",
                Outcome(
                    "Le cocinas un menú a medida con aire, agua y pimienta. Dice que es "
                    "lo mejor que ha comido y deja de propina un abrazo.",
                    performance=8,
                    battery=-15,
                    stat="work_allergic_menu",
                ),
            ),
            (
                "A otro restaurante",
                Outcome(
                    "Le recomiendas un restaurante «más adecuado a sus necesidades». Es "
                    "una farmacia de guardia.",
                    performance=-5,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=7,
        branches=("estrella",),
    ),
    Event(
        "tortilla",
        "🥔 Un periodista te pregunta en directo: «La tortilla de patatas, ¿con cebolla o "
        "sin cebolla?». Media España contiene la respiración.",
        (
            (
                "Con cebolla",
                Outcome(
                    "«Con cebolla.» Tu móvil explota y tu abuela te manda un audio de "
                    "once minutos. Eres tendencia dos horas.",
                    performance=8,
                    family=-5,
                    stat="work_onion_with",
                ),
            ),
            (
                "Sin cebolla",
                Outcome(
                    "«Sin cebolla.» Sale un tertuliano a gritar «¡herejía!» y la "
                    "polémica nacional te da otra estrella en visibilidad.",
                    performance=8,
                    family=-5,
                    stat="work_onion_without",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=8,
        branches=("estrella",),
    ),
    Event(
        "presentador_gritos",
        "📺 Un presentador famoso por gritar «¡esto es un desastre!» llega con cámaras a tu "
        "restaurante: «Vengo a salvarte».",
        (
            (
                "Que grabe",
                Outcome(
                    "Le dejas grabar. Grita tres veces, llora dos y al final la audiencia "
                    "se come tu plato estrella por la tele.",
                    pay=0.8,
                    performance=-5,
                    family=-5,
                    stat="work_tv_crew",
                ),
            ),
            (
                "A la calle",
                Outcome(
                    "Le echas con una bandeja. Sale en las noticias: «El chef que echó "
                    "a un presentador». Te llaman de tres cadenas.",
                    performance=10,
                    stat="work_tv_kicked",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=8,
        branches=("estrella",),
    ),
    Event(
        "jeque_oro",
        "🏜️ Un jeque de visita quiere pan de oro en todo: el entrante, el principal, el "
        "postre y el agua.",
        (
            (
                "Oro en todo",
                Outcome(
                    "Pan de oro hasta en la ensalada. Pagó con una tarjeta negra que "
                    "parecía un lingote.",
                    pay=1.0,
                    performance=-5,
                    stat="work_gold_all",
                ),
            ),
            (
                "Solo en el postre",
                Outcome(
                    "Le pones oro en el postre y le explicas que lo demás es de sabor. "
                    "El jeque te regala un reloj. Falso, pero con mucho brillo.",
                    pay=0.4,
                    performance=8,
                    stat="work_gold_dessert",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=9,
        branches=("estrella",),
    ),
    Event(
        "espumas",
        "💥 La máquina de espumas explota en pleno servicio y cubre la sala de espuma de "
        "gofio hasta las lámparas.",
        (
            (
                "Limpiar y seguir",
                Outcome(
                    "Limpias entre plato y plato. Los clientes dicen que el ambiente "
                    "estaba «muy aireado».",
                    performance=5,
                    battery=-20,
                ),
            ),
            (
                "Venderlo como técnica",
                Outcome(
                    "Lo vendes como «la nueva técnica del baño de espuma». Cobras un "
                    "suplemento y alguien lo pone en una guía.",
                    pay=0.5,
                    performance=5,
                    stat="work_foam_sold",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=9,
        branches=("estrella",),
    ),
    Event(
        "inspeccion_sanidad",
        "🩺 La Inspección de Sanidad llega sin avisar a la cocina del grupo: en la cámara "
        "hay huevos de la semana pasada, y «de otra semana».",
        (
            (
                "Esconder los huevos",
                Outcome(
                    "Metes los huevos en un armario y la inspectora abre el armario "
                    "primero. Lleva años haciendo esto.",
                    pay=0.6,
                    risk=0.3,
                    fine=1.5,
                    caught_by="sanidad_alimentaria",
                    stat="work_health_hidden",
                ),
            ),
            (
                "Cocina abierta",
                Outcome(
                    "Le abres todas las puertas y la invitas a probar la tarta. Sale "
                    "con un informe impecable y un trozo para la oficina.",
                    performance=10,
                    battery=-10,
                    stat="work_health_open",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=10,
        branches=("estrella",),
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
    # -- Política: Moncloa --------------------------------------------------------------
    Event(
        "mocion",
        "🗳️ Registran una moción de censura contra el Gobierno. Esta tarde cuentan los votos.",
        (
            (
                "Aguantar",
                Outcome(
                    "Aguantas con tres cafés, dos llamadas a los socios y una promesa de tren. "
                    "La moción decae y Ferraz te debe una.",
                    pay=0.5,
                    performance=20,
                    battery=-20,
                    stat="work_censure_survived",
                ),
            ),
            (
                "Elecciones por sorpresa",
                Outcome(
                    "Convocas elecciones a las seis de la mañana. Nadie lo vio venir, ni tú.",
                    pay=1.0,
                    performance=10,
                    battery=-25,
                    family=-5,
                    stat="work_snap_election",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    Event(
        "socio",
        "🤝 Un socio de la investidura pide algo a cambio de sus votos. «Es una cuestión "
        "de Estado», dice.",
        (
            (
                "Ceder",
                Outcome(
                    "Cedes. Los votos llegan, la factura la pagará otro Gobierno.",
                    pay=0.5,
                    performance=15,
                    stat="work_partner_deal",
                ),
            ),
            (
                "Plantarse",
                Outcome(
                    "Te plantas. El socio vota con la oposición y en Ferraz te miran fatal.",
                    performance=-20,
                    battery=-10,
                    stat="work_partner_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    Event(
        "cis",
        "📊 Te sugieren «afinar» la encuesta del CIS antes de que salga publicada.",
        (
            (
                "La cocino",
                Outcome(
                    "Cocinas la encuesta a fuego lento. Sale una ventaja de diez puntos y "
                    "ningún sociólogo la entiende.",
                    pay=0.6,
                    performance=20,
                    stat="work_cis",
                ),
            ),
            (
                "Sin tocar",
                Outcome(
                    "La publicas tal cual. Sale fatal y el titular es tuyo.",
                    performance=-10,
                    stat="work_cis_honest",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    Event(
        "omnibus",
        "📦 Hay veinte medidas pendientes y poco tiempo. Alguien susurra: «Un ómnibus y a correr».",
        (
            (
                "Todo al ómnibus",
                Outcome(
                    "Metes pensiones, vivienda, una ley de pesca y un indulto en un solo "
                    "decreto. Nadie lo ha leído entero.",
                    pay=0.7,
                    performance=15,
                    battery=-10,
                    stat="work_omnibus",
                ),
            ),
            (
                "Por separado",
                Outcome(
                    "Tramitas cada ley por su cuenta. Es correcto, lento y nadie te lo agradece.",
                    performance=-5,
                    battery=-15,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    # -- Política: Bruselas -------------------------------------------------------------
    Event(
        "qatargate",
        "💼 Un enviado de un emirato te deja un maletín «para estrechar lazos entre pueblos».",
        (
            (
                "Lo cojo",
                Outcome(
                    "Lo cargas en el coche. Pesa lo que pesan las buenas relaciones "
                    "internacionales.",
                    black=3.0,
                    risk=0.3,
                    caught_by="olaf",
                    stat="work_suitcase",
                ),
            ),
            (
                "Lo rechazo",
                Outcome(
                    "Lo devuelves y lo cuentas en el registro de transparencia. Parece que "
                    "has perdido el norte.",
                    performance=-10,
                    stat="work_suitcase_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    Event(
        "estrasburgo",
        "🚆 Toca el traslado mensual a Estrasburgo: cuatro mil funcionarios, ciento "
        "ochenta cajas de documentos y un tren.",
        (
            (
                "Voy con todos",
                Outcome(
                    "Viajas con la comitiva y las cajas. Cuando llegas, ya es el pleno siguiente.",
                    battery=-25,
                    family=-5,
                    stat="work_strasbourg",
                ),
            ),
            (
                "Me quedo en Bruselas",
                Outcome(
                    "Te quedas en Bruselas y lo sigues por pantalla. En casa respiran.",
                    performance=-10,
                    battery=-10,
                    family=5,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    Event(
        "dietas",
        "✍️ El pleno es largo y las dietas se cobran con solo firmar en el registro de la entrada.",
        (
            (
                "Firmo y me voy",
                Outcome(
                    "Firmas, cobras la dieta y desapareces entre los tulipanes. Nadie "
                    "lo comprueba, casi nadie.",
                    black=0.5,
                    risk=0.25,
                    caught_by="olaf",
                    performance=-5,
                    stat="work_per_diem",
                ),
            ),
            (
                "Me quedo al pleno",
                Outcome(
                    "Te quedas. Aprendes lo que es una votación por partes y no cobras extra.",
                    performance=15,
                    battery=-15,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    Event(
        "cena_lobby",
        "🍽️ Un lobby te invita a una cena de cuatro platos en un restaurante con "
        "estrella. «Solo para conocernos».",
        (
            (
                "Acepto",
                Outcome(
                    "Cenas, brindas y sales sin haber firmado nada. La cuenta la paga "
                    "un sector entero.",
                    performance=10,
                    battery=-10,
                    family=-3,
                    stat="work_lobby_dinner",
                ),
            ),
            (
                "Declino",
                Outcome(
                    "Te vas a casa con un bocadillo. Tu asistente te mira con tristeza.",
                    performance=-5,
                    family=5,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    Event(
        "exhumacion",
        "⚰️ Faltan tres meses para las elecciones y alguien propone otra exhumación «histórica».",
        (
            (
                "Con helicóptero",
                Outcome(
                    "Helicóptero, cámaras y directo como en el NO-DO. Franco sigue muerto y tú "
                    "subes dos puntos en el CIS.",
                    pay=0.5,
                    performance=15,
                    stat="work_exhumation",
                ),
            ),
            (
                "No toca",
                Outcome(
                    "Lo dejas para después de las elecciones. Tu jefe de campaña llora en el baño.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    Event(
        "waterloo",
        "🧳 Para sacar la próxima ley te faltan siete votos, y se deciden en Waterloo.",
        (
            (
                "Voy a Waterloo",
                Outcome(
                    "Viajas a Bélgica, te hacen esperar en la puerta y vuelves con los votos "
                    "y una lista de deberes más larga que el Estatut.",
                    pay=0.5,
                    performance=15,
                    battery=-15,
                    family=-5,
                    stat="work_waterloo",
                ),
            ),
            (
                "Que vengan ellos",
                Outcome(
                    "Esperas sentado. Los votos no llegan y la ley, tampoco.",
                    performance=-15,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=7,
        branches=("moncloa",),
    ),
    Event(
        "carta",
        "✉️ Te sientes acosado por la prensa. Tu equipo propone una carta a la ciudadanía "
        "y cinco días de reflexión.",
        (
            (
                "Escribo la carta",
                Outcome(
                    "Escribes la carta, desapareces cinco días y vuelves más fuerte. El país, "
                    "en vilo; el resultado, el de siempre.",
                    pay=0.5,
                    performance=20,
                    family=10,
                    stat="work_letter",
                ),
            ),
            (
                "Sigo trabajando",
                Outcome(
                    "Sigues al pie del cañón. Ni una portada te dedican.",
                    performance=-5,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=9,
        branches=("moncloa",),
    ),
    Event(
        "aranceles",
        "🇺🇸 Trump anuncia aranceles del 200 % al aceite, al vino y a los quesos europeos.",
        (
            (
                "Respuesta firme",
                Outcome(
                    "Aranceles espejo a las motos y al bourbon. Trump te llama «tremendous» y "
                    "sube otro 100 %.",
                    pay=0.5,
                    performance=10,
                    battery=-10,
                    stat="work_tariffs",
                ),
            ),
            (
                "Llamar al Despacho Oval",
                Outcome(
                    "Llamas, te ponen en espera con el himno de fondo y cuelgas sin hablar con "
                    "nadie.",
                    performance=-5,
                    battery=-5,
                    stat="work_oval_office",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=7,
        branches=("bruselas",),
    ),
    Event(
        "tapon",
        "🍶 Toca votar si el tapón pegado a la botella llega también a los bricks de leche.",
        (
            (
                "Tapón para todos",
                Outcome(
                    "Aprobado. Medio continente se mancha la nariz al beber. Bruselas en estado "
                    "puro.",
                    performance=10,
                    stat="work_cap",
                ),
            ),
            (
                "Libertad de tapón",
                Outcome(
                    "Votas en contra. En tu grupo te llaman euroescéptico y te quitan el turno "
                    "de palabra.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    # -- Obra: Ladrillo -----------------------------------------------------------------
    Event(
        "burbuja",
        "📉 En la tele dicen que estalla la burbuja. Tienes trescientos pisos sin vender.",
        (
            (
                "Vender a un fondo",
                Outcome(
                    "Se los vendes todos a un fondo buitre a mitad de precio. Al mes los "
                    "alquila al doble.",
                    pay=0.6,
                    performance=-5,
                    stat="work_vulture",
                ),
            ),
            (
                "Aguantar",
                Outcome(
                    "«El ladrillo nunca baja». Aguantas con los pisos vacíos y el banco "
                    "llamando a diario.",
                    performance=15,
                    battery=-15,
                    family=-5,
                    stat="work_bubble_held",
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("ladrillo",),
    ),
    Event(
        "recalificacion",
        "🌲 El concejal de urbanismo te ofrece recalificar un pinar «a cambio de un detalle».",
        (
            (
                "Un detalle",
                Outcome(
                    "Un detalle en un sobre y el pinar ya es urbanizable. Las ardillas, "
                    "desahuciadas.",
                    black=2.5,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_rezoning",
                ),
            ),
            (
                "Por la vía legal",
                Outcome(
                    "Pides la licencia por la vía legal. Te la darán en 2041.",
                    performance=-10,
                    stat="work_rezoning_refused",
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("ladrillo",),
    ),
    Event(
        "oligarca",
        "🛥️ Un oligarca quiere diez áticos al contado, en billetes de 500 y sin preguntas.",
        (
            (
                "Vendo",
                Outcome(
                    "Cuentas los billetes con una máquina que se calienta. Venta cerrada.",
                    pay=1.0,
                    risk=0.25,
                    fine=1.5,
                    caught_by="sepblac",
                    stat="work_oligarch",
                ),
            ),
            (
                "Transferencia o nada",
                Outcome(
                    "Le pides una transferencia con su origen. Se va a la promoción de al lado.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=7,
        branches=("ladrillo",),
    ),
    Event(
        "turisticos",
        "🧳 Un fondo quiere tu bloque nuevo entero para pisos turísticos.",
        (
            (
                "Todo turístico",
                Outcome(
                    "Cuarenta pisos con caja de llaves en el portal. Los vecinos de toda la "
                    "vida, a la periferia.",
                    pay=0.7,
                    family=-3,
                    stat="work_tourist_flats",
                ),
            ),
            (
                "Para el barrio",
                Outcome(
                    "Lo alquilas a gente del barrio. Ganas menos y te saludan por la calle.",
                    performance=-5,
                    family=5,
                    stat="work_social_rent",
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("ladrillo",),
    ),
    # -- Obra: Obra pública -------------------------------------------------------------
    Event(
        "modificado",
        "📈 La obra va por la mitad y el presupuesto ya está gastado.",
        (
            (
                "Modificado al canto",
                Outcome(
                    "Pides un modificado «por la geología». Te lo aprueban en una semana: el "
                    "ministerio también quiere inaugurar.",
                    pay=0.7,
                    performance=15,
                    stat="work_change_order",
                ),
            ),
            (
                "Acabar con lo que hay",
                Outcome(
                    "Terminas con lo que queda. La carretera acaba en un descampado.",
                    performance=-10,
                    battery=-15,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("publica",),
    ),
    Event(
        "inauguracion",
        "✂️ El ministro quiere inaugurar la autovía antes de las elecciones. Falta la mitad.",
        (
            (
                "Cortar la cinta",
                Outcome(
                    "Cinta, tijeras y foto en el kilómetro 3. El kilómetro 4 sigue siendo tierra.",
                    pay=0.5,
                    performance=15,
                    stat="work_ribbon",
                ),
            ),
            (
                "Cuando esté acabada",
                Outcome(
                    "Le dices que cuando esté acabada. El ministro deja de contestarte.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("publica",),
    ),
    Event(
        "aeropuerto",
        "✈️ Te encargan un aeropuerto en una provincia sin vuelos. Con estatua en la rotonda.",
        (
            (
                "Lo construyo",
                Outcome(
                    "Lo levantas en tiempo récord. La estatua mide veinticuatro metros; los "
                    "aviones, de momento, cero.",
                    pay=0.8,
                    performance=10,
                    stat="work_ghost_airport",
                ),
            ),
            (
                "Mejor un tren",
                Outcome(
                    "Propones un tren. Te miran como si hubieras pedido un concurso limpio.",
                    performance=-5,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=7,
        branches=("publica",),
    ),
    Event(
        "muro",
        "🧱 Trump te llama: quiere un muro en la frontera y jura que lo paga México.",
        (
            (
                "Por adelantado",
                Outcome(
                    "Presupuesto en dólares, cobrado por adelantado. Quién lo pague al final "
                    "ya no es cosa tuya.",
                    pay=1.0,
                    performance=10,
                    battery=-10,
                    stat="work_wall",
                ),
            ),
            (
                "Mejor un puente",
                Outcome(
                    "Le propones un puente. Te cuelga y te pone un arancel.",
                    performance=-5,
                    family=5,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=8,
        branches=("publica",),
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
    "sepblac": (
        "🏦 **El Sepblac (prevención del blanqueo) pregunta por los áticos.** Los billetes "
        "de 500 dejan rastro: multa por no identificar el origen del dinero."
    ),
    "olaf": (
        "🇪🇺 **La OLAF (Oficina Europea de Lucha contra el Fraude) abre una investigación.** "
        "Tras tres años de papeleo bilingüe, devuelves lo cobrado con un 20 % de recargo."
    ),
    "incompatibilidades": (
        "🏛️ **La Oficina de Conflictos de Intereses te abre expediente.** El precontrato con "
        "la aseguradora se firmó con el cargo aún caliente: devuelves lo cobrado con un 20 % "
        "de recargo."
    ),
    "abusivas": (
        "⚖️ **Un juzgado declara abusiva la cláusula.** El asegurado reclamó con abogado: "
        "la clínica paga lo que cobró de más y una multa por cláusulas abusivas."
    ),
    "terraza": (
        "📏 **La policía local te multa por ocupación de vía pública.** Las tres mesas "
        "«decorativas» eran muy reales: pagas la sanción y ya conoces al agente por su nombre."
    ),
    "costas": (
        "🏖️ **Costas ejecuta la demolición subsidiaria.** Después de veinte años de recursos, "
        "la terraza cae en una tarde y la factura de la excavadora te llega a ti."
    ),
    "sanidad_alimentaria": (
        "🩺 **La Inspección de Sanidad cierra la cámara.** Los huevos tenían más historia que "
        "tu local: multa por no cumplir la cadena de frío y un acta con tu nombre."
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
    job: str,
    level: int,
    family: int,
    *,
    abroad: bool = False,
    phase: str = "casa",
    branch: str = "",
) -> list[Event]:
    """Eventos que pueden salir al azar con este oficio, puesto, rama, familia y país."""
    return [
        event
        for event in EVENTS
        if not event.special
        and (not event.jobs or job in event.jobs)
        and (not event.branches or branch in event.branches)
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


# -- Familia: regalos y favores ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gift:
    """Un regalo de la tienda (pasillo 🏠 La familia) que sube la familia en `pala`.

    Se compra en `tienda` como cualquier objeto y se da desde el panel de `pala`
    (se gasta). La clave es la del artículo en `bot.services.shop_catalog`.

    Attributes:
        family: Lo que sube la familia.
        cooldown: Segundos hasta poder dar otro igual (tu madre no quiere
            veinte ramos el mismo día).
    """

    key: str
    emoji: str
    name: str
    family: int
    cooldown: int


HOUR = 3600
DAY = 24 * HOUR

GIFTS: tuple[Gift, ...] = (
    Gift("bombones_mama", "🍫", "Caja de bombones", 6, 8 * HOUR),
    Gift("flores_mama", "💐", "Ramo para tu madre", 10, 12 * HOUR),
    Gift("pizza_familiar", "🍕", "Pizza familiar", 12, 12 * HOUR),
    Gift("tarta_abuela", "🎂", "Tarta de la abuela", 15, DAY),
    Gift("juguete_sobrino", "🧸", "Juguete para el sobrino", 20, 3 * DAY),
    Gift("entradas_concierto", "🎟️", "Entradas para un concierto", 20, 3 * DAY),
    Gift("cena_guachinche", "🍷", "Cena en un guachinche", 25, 4 * DAY),
    Gift("finde_sur", "🏖️", "Finde en el sur", 50, 7 * DAY),
)
GIFT_BY_KEY: dict[str, Gift] = {gift.key: gift for gift in GIFTS}


@dataclass(frozen=True, slots=True)
class Favor:
    """Algo que le pides a la familia desde `pala`: cuesta familia, no dinero.

    No se piden si dejarían la familia por debajo de `work.FAVOR_FLOOR`: quien
    ya la tiene en crisis no tiene a quién pedirle nada.

    Attributes:
        cost: Lo que baja la familia.
        cooldown: Segundos hasta poder pedirlo otra vez.
        text: Qué hace, para el desplegable.
        done: Lo que se cuenta al pedirlo.
    """

    key: str
    emoji: str
    name: str
    cost: int
    cooldown: int
    text: str
    done: str


FAVORS: tuple[Favor, ...] = (
    Favor(
        "tupper",
        "🍲",
        "El tupper de tu madre",
        15,
        20 * HOUR,
        "+30 de batería, sin temblores",
        "🍲 Tu madre te da un tupper de lentejas «para que comas algo caliente». +30 de batería.",
    ),
    Favor(
        "ninos",
        "💪",
        "Lo hago por los niños",
        15,
        12 * HOUR,
        "+15 % de tiempo en el próximo turno",
        "💪 Miras la foto de los niños en el móvil. El próximo turno va con +15 % de tiempo.",
    ),
    Favor(
        "cubre",
        "👵",
        "Te cubre tu madre",
        25,
        3 * DAY,
        "Quita el saliente de guardia",
        "👵 Tu madre llama al hospital: «Mi niño no puede, que tiene fiebre». Se acabó el "
        "saliente.",
    ),
    Favor(
        "cambio",
        "👯",
        "Tu hermana te cambia el turno",
        20,
        7 * DAY,
        "Un turno ordinario más hoy",
        "👯 Tu hermana se queda con los niños y te cambia el turno. Hoy tienes un turno "
        "ordinario más, sin extras ni B.",
    ),
    Favor(
        "excusa",
        "📞",
        "Excusa familiar",
        10,
        DAY,
        "Te libras del marrón que tengas delante",
        "📞 «Perdona, jefe, me llama mi madre». Te libras del marrón.",
    ),
    Favor(
        "bizum",
        "💸",
        "Bizum de mamá",
        20,
        7 * DAY,
        "Te adelanta un sueldo base; se devuelve con media nómina",
        "💸 Tu madre te manda un Bizum con el concepto «para que no pases fatigas».",
    ),
)
FAVOR_BY_KEY: dict[str, Favor] = {favor.key: favor for favor in FAVORS}
#: Lo que sube la batería el tupper.
TUPPER_BATTERY = 30
#: Lo que da de tiempo «Lo hago por los niños», en tanto por uno.
MOTIVATED_TIME = 0.15
