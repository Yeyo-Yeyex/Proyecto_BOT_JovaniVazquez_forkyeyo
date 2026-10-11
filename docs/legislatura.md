# Legislatura: arquitectura

Roguelike de cartas con baraja española, inspirado en Balatro. Junto con la beernight es la
pieza grande del bot y la fuente de dinero de habilidad: el IMV y `pala` premian la
constancia, el casino es azar y Legislatura premia jugar bien.

Este documento fija la arquitectura. El motor ya existe y tiene pruebas; el cog, el dibujo,
los logros y el alta en la tienda son las fases siguientes.

## Reglas en una página

- Una partida es una legislatura: 4 años con dos periodos de sesiones cada uno (septiembre a
  diciembre y febrero a junio, art. 73.1 CE), así que 8 periodos. Son las 8 «apuestas» de
  Balatro.
- Cada periodo tiene tres sesiones: ordinaria, extraordinaria (×1,5 votos) y moción de
  censura (×2, con una regla mala: el jefe). Las dos primeras se pueden saltar
  («abstenerse») a cambio de un premio.
- En cada sesión hay que reunir votos con 4 jugadas y 3 descartes, con una mano de 8 cartas.
  Una jugada es de 1 a 5 cartas y puntúa `votos × mult`.
- Combinaciones de Balatro con dos cambios: escaleras de 10 números con la sota pegada al 7, y
  **las cuarenta** (caballo y rey del mismo palo), que valen más que la doble pareja.
- Entre sesiones, el mercado: asesores (comodines), encuestas (planetas), normativas
  (tarots), cloacas (espectrales), tratados (vales) y sobres. Se paga con **fondos** de la
  partida, que no son yapdollars.
- Tras ganar la moción del periodo 8: cobrar o prorrogar los presupuestos (hasta 2
  periodos más, premio ×2 y ×4, y si cae el Gobierno no se cobra nada).

## Capas

```text
cogs/legislatura.py            (fase 2) Discord: comando, botones, candado por jugador
  │   carga y guarda             cobra y paga
  ▼                              ▼
repositories/legislatura.py    services/economy.py (place_bet / pay_winnings)
  │  JSON de Run
  ▼
services/legislatura/
  run.py        estado y acciones; única pieza con estado
  market.py     mercado de dentro de la partida
  scoring.py    cadena de puntuación
  catalog.py    registro: cosas, jefes, gobiernos, primas, abstenciones
  advisors.py   efectos de asesores        consumables.py  normativas, cloacas, tratados
  effects.py    contrato de efectos (ganchos) y ScoreContext (la cuenta paso a paso)
  items.py      tipos comunes: Bloc, Category, Rarity, ItemDef, GameError
  hands.py      combinaciones               cards.py   baraja, etiquetas, ediciones, sellos
  rng.py        azar con semilla por flujos
  prizes.py     entradas y premios en Y$    cosmetics.py  tapetes, dorsos, arte de asesores
```

Cada capa importa solo las de debajo. Nada de `services/legislatura` toca Discord, la base de
datos ni el dinero.

### Decisiones y por qué

**Efectos con ganchos, no condiciones en el motor.** Un asesor es una clase con métodos
(`on_card_scored`, `on_hand`, `on_round_end`, `price`, `blocks`…) que por defecto no hacen
nada. El motor los llama en orden fijo y de izquierda a derecha, como Balatro, así que el
orden de los asesores importa. Un asesor nuevo es una clase y una fila en `catalog`; el motor
no cambia. El estado propio de cada asesor (contadores) va en `slot.state`, que se guarda con
la partida.

**Los bloques son mecánica.** Cada cosa del catálogo tiene un bloque (España, UE, EE. UU.,
China, Israel o neutro) y los ganchos ven toda la mesa. De ahí salen los cruces: los Aranceles
encarecen lo chino en el escaparate al momento, el Boicot a Eurovisión echa a los asesores
israelíes y bloquea comprarlos, la Cumbre de la OTAN cobra el 2,1 % si está Perro Sanxe, el
Paquete de ayuda solo llega con un asesor de EE. UU., el CIS sube dos niveles con Perro
Sanxe, la Resolución de la ONU la veta EE. UU. Una prueba exige que todos los bloques tengan
asesores.

**Azar por flujos con nombre** (`rng.py`). Cada sorteo pide un flujo («mazo:3:1»,
«tienda:3:1:0») sembrado con la semilla, el nombre y un contador. Comprar o dar vueltas en la
tienda no gasta el azar de barajar, así que en la partida diaria todos ven la misma baraja en
cada sesión aunque jueguen distinto. Se guarda solo la semilla y los contadores.

**Todo el estado en un JSON** (`Run.to_dict`, ~2-5 KB). Se guarda tras cada acción. Con la
semilla puesta por el servidor y el guardado continuo, abandonar y volver a empezar no
cambia nada y no hay forma de deshacer una jugada. El formato lleva versión (`SAVE_VERSION`).

**`GameError` antes de tocar nada.** Cada acción comprueba fase, fondos, huecos y cartas, y
si algo no vale lanza `GameError` con el texto para el usuario sin haber cambiado el estado
(hay prueba de ello). El cog solo tiene que enseñar el mensaje.

**La cuenta paso a paso.** `ScoreContext` apunta cada suma y multiplicación con su origen
(`ScoreStep`). El cog edita el mensaje en 3 o 4 tramos (base, cartas, asesores, total): ese
conteo es la mitad de la gracia del juego original.

## Dinero

- **Fondos de la partida:** se ganan al superar sesiones (3/4/5), por jugadas sobrantes (+1) e
  intereses (1 por cada 5, tope 5). No se compran con yapdollars, para que el juego premie
  jugar bien y no ser rico.
- **Entrada y premio en Y$** (`prizes.py`): por prima de riesgo, de 300/2.000 en la prima 1 a
  15.000/100.000 en la 8. El premio es convexo según los periodos superados (`PRIZE_SHARE`).
- **Fiscalidad: juego.** Ley 13/2011, art. 3.a: es juego aunque predomine la destreza. Entrada
  con `place_bet` y premio con `pay_winnings` (`game="legislatura"`): retención diaria y
  declaración semanal del casino, como el blackjack.
- **Partida diaria:** gratis, misma semilla para todo el servidor (`rng.daily_seed`), una al
  día, no paga Y$. Da clasificación (`legislatura_daily`) y logros.
- **Aspectos** (`cosmetics.py`): tapetes, dorsos y arte de asesores. Se compran en la `tienda`
  del bot con Y$ y no cambian las reglas. Son el sumidero que compensa los premios.

### Calibrar (antes de abrir)

Las cifras son provisionales. Hay que escribir `docs/legislatura_simulador.py`, que juegue miles
de partidas con el motor real y un jugador razonable (elige la mejor jugada, compra asesores
por valor) para medir:

1. Qué periodo alcanza un jugador medio y uno bueno en cada prima.
2. Con eso, entradas y premios para que ganar en la prima 8 rinda algo así como dos o tres
   turnos de `pala` por hora jugada, y que un buen jugador llegue a los objetos de millones
   en semanas, no en años.
3. El Falcon cuesta 50.000.000 Y$: son 500 victorias en la prima 8 sin prórroga. Decidir si es
   el techo inalcanzable a propósito o si hacen falta escalones intermedios (1-5 millones).

## Fase 2: el cog

- **Comando:** `legislar` (8 letras, en español). Un panel: elegir modo (diaria, libre,
  continuar), gobierno y prima. Sin subcomandos.
- **Mano:** 8 botones de carta que se marcan al pulsarlos (2 filas de 4) y una fila de acciones
  (Jugar, Descartar, Ordenar, Consumibles, Mesa). Marcar cartas solo cambia memoria y contesta
  con `response.edit_message`; Jugar, Descartar y el mercado hacen `ack` antes de cargar,
  guardar o dibujar (CLAUDE.md, «Botones rápidos»).
- **Un candado por jugador** y una acción en curso, como `pala`.
- **Dibujo:** Pillow, como `cards_render.py` del blackjack (~10 ms por imagen): las 40 caras y
  los dorsos se pintan una vez y se cachean, y cada estado solo pega recortes sobre el tapete
  elegido. Sin Chromium ni Node: no suma memoria al `mem_limit` de 2 GB.
- **Al cerrar** (perder, cobrar o abandonar): `pay_winnings`, `repository.finish`, desbloqueo
  de la siguiente prima y gobiernos, logros (`track`), `apuestas.record`, `renta.remind` y
  `renta.hint` con el `Moment` de la mascota.
- **Porras:** decidir si entra (las partidas duran; habría que mandar `started`) o va en
  `EXCLUDED_GAMES`.

## Fase 3: logros

Categoría propia en 🎰 Casino o entrada aparte (mejor aparte: es habilidad). Más de cuarenta,
con todas las familias de la Biblia. Ideas: «Gobierno en funciones» (perder en la primera
sesión), «Mayoría absoluta» (176.000 votos en una jugada), «Equidistante» (un asesor de cada
bloque a la vez), «Autonomía estratégica» (ganar sin asesores de EE. UU.), «Las cuarenta en
bastos», «Promesa incumplida» (romper 10 Promesas), «Moción a la primera», «Presupuestos
prorrogados» (cobrar ×4), una por jefe superado, una por prima. Las estadísticas ya se cuentan
en `run.stats` y `hand_plays`.

## Fase 4: tienda y contenido

- Dar de alta los aspectos de `cosmetics.SKINS` en `shop_catalog` generados solos (como las
  mascotas), con la ficha de alta de la Biblia.
- Ampliar el catálogo por tandas hasta ~40 asesores, 13 encuestas, 12 normativas, 6 cloacas
  y 12 tratados, con los cinco bloques y sus cruces. Cada tanda es una novedad del bot.
- Pendiente del motor para tandas futuras: Big Tech y Pegasus (ven cartas: piden apoyo en el
  dibujo), Banner de cookies (botón propio), duelos `pacto` con la misma semilla.

## Pendiente de decidir

1. Cifras de entradas y premios tras el simulador.
2. Si el Falcon y los demás lujos de millones se quedan como techo o se añaden escalones.
3. Si Legislatura admite porras.
4. Elecciones generales semanales (bote del Estado para los tres mejores de una semilla
   semanal): encaja con el resto de la economía, pero mueve dinero del Estado y se decide
   aparte.
