# Plan del juego del pico (`picar`)

Juego nuevo del casino inspirado en los anuncios de picos de Instagram. Este documento
recoge el plan de partida y lo que se decidió al hablarlo, para que cada fase se pueda
retomar en otro chat sin depender de la conversación. Lo que cambie por el camino se
apunta aquí.

## El juego

El jugador elige un pico y paga su precio. El pico cae por un mundo de bloques de 9
columnas con físicas de pachinko: rebota, gira y rompe bloques. Cada golpe le quita vida
según la dureza del bloque. Los minerales rotos suman multiplicadores. Cuando la vida
llega a 0, el premio es la suma de multiplicadores por el precio del pico. Si baja lo
bastante pasa al Nether, y más abajo al End. En el fondo del End está el dragón:
llegar a él paga el premio máximo de ese pico.

- RTP del 99,5 % exacto en todos los picos. Ningún pico rinde más que otro: cambia la
  volatilidad y la profundidad.
- La partida se ve entera en GIF.
- El resultado se decide antes de dibujar y el dinero se mueve al simular, no al acabar
  la animación. Si el bot se cae a mitad del GIF, el jugador ya ha cobrado o pagado.
- Se guarda la semilla y el resultado de cada tirada para poder reproducirla.

## Picos

Coste fijo por pico. Las cifras son de partida y se pueden cambiar más adelante sin
recalibrar, porque los premios son múltiplos del coste. La economía del servidor tenía
298.286 Y$ de masa monetaria el 17/10/2026; por eso son la décima parte de las del
primer borrador.

| Pico | Coste | Dragón | Premio del dragón | Notas |
|---|---|---|---|---|
| Madera | 10 | ×50 | 500 | no suele salir de las cuevas |
| Piedra | 50 | ×150 | 7.500 | |
| Hierro | 200 | ×500 | 100.000 | llega a la zona de diamantes |
| Oro | 200 | ×1.500 | 300.000 | se rompe enseguida, minerales ×3 (kamikaze) |
| Diamante | 1.000 | ×2.500 | 2,5 M | llega al Nether a menudo |
| Netherite | 5.000 | ×5.000 | 25 M | Nether profundo y a veces el End |

El tope de netherite se revisa con la biblioteca: si el dragón sale demasiado a menudo,
un solo premio mete varias veces la masa monetaria. El ×3 del oro es de ambientación: con
un factor de escala por pico, la calibración lo absorbe.

Metas de profundidad iniciales (se revisan jugando):

| Pico | Llega al Nether | Llega al End |
|---|---|---|
| Madera | ~0,1 % | casi imposible |
| Piedra | ~0,5 % | |
| Hierro | ~2 % | |
| Oro | ~1 % | |
| Diamante | ~25 % | ~1 % |
| Netherite | ~60 % | ~5 % |

## Reglas que no se negocian

1. **La física no depende del valor de los minerales.** La trayectoria y los bloques rotos
   dependen solo de la semilla, del pico y del mundo. Los valores se aplican después. Si
   esto se rompe, la calibración deja de valer.
2. **Física solo con `+ - * /` y `sqrt`.** La calibración se hace en un PC con Windows y el
   juego corre en Linux. `sin`, `cos`, `atan2` o `exp` vienen de la libm de cada sistema y
   pueden diferir en el último bit; en un sistema caótico, un bit en el tick 200 es otra
   partida en el tick 2.000. Por lo mismo, nada de numba ni numpy dentro del bucle. Es la
   misma regla del pachinko (`pachinko_physics`).
3. **Todo el azar sale de la semilla.** El mundo, de un generador por trozo de filas; la
   física, de un generador propio de la partida. Nada de `random` global ni de la hora.
4. **Cualquier cambio en física, mundo o vida de los picos obliga a recalibrar.** La
   calibración guarda el hash de los parámetros (`picar_physics.params_hash`) y una huella
   de un puñado de semillas. Al arrancar, el bot comprueba las dos cosas; si no cuadran,
   el juego se apaga y lo dice en el log. El hash detecta que alguien tocó un parámetro; la
   huella, que el NAS calcula distinto que el PC o que cambió el código del motor.
5. **Cada tirada guardada lleva el hash con el que se jugó.** Tras recalibrar, la misma
   semilla da otra partida.

## Biblioteca de semillas

En vez de simular partidas nuevas y fiarse de un RTP medido por Monte Carlo, el juego
elige una semilla de una lista fija: las semillas de 0 a 999.999 de cada pico.

- El RTP de una lista finita se calcula exacto con `Fraction`, sin error de muestreo. A lo
  bruto no se podría comprobar: con un dragón de ×5.000 en el 0,1 % de las partidas, un
  millón de partidas da el RTP con ±16 puntos de error típico.
- No hay nada que guardar en el repo salvo «un millón»: al pulsar, el bot elige un número,
  simula esa semilla (lo tiene que hacer igual para dibujarla) y sale el resultado.
- `calibracion.json` guarda los valores de los minerales, el factor `k` de cada pico, los
  topes, el RTP exacto como fracción, el hash y la huella. Unos pocos KB.
- En el PC se guarda aparte (fuera del repo) la tabla de cuántos minerales de cada tipo
  rompe cada semilla, para cambiar valores o topes sin volver a simular.
- El dragón entra en la cuenta cerrada: `RTP = k · E[suma · (sin dragón)] + P(dragón) · tope`.
  `k` se despeja de una vez.
- Si un evento raro no sale ni una vez en el millón (el dragón con madera), se busca una
  semilla que lo tenga y se añade con un peso pequeño. El RTP sigue siendo exacto.
- Mientras se ajustan las físicas se calibra con 100.000 semillas; la definitiva, con el
  millón.

## Dibujo

- Escena de canvas pintada con `NodeScene` y GIF con `bot.utils.gif`, como el resto del
  casino (Biblia, sección 5, y «Juego nuevo con dibujo» de CLAUDE.md). No se usan ffmpeg
  ni un `ProcessPoolExecutor`: el NAS ya roza los 2 GB de memoria.
- **Un GIF por dimensión.** Cada uno puede llegar al 80 % de `guild.filesize_limit`. El
  cambio de dimensión es una animación repartida entre los dos GIF: el que acaba termina
  con el pico cayendo en la oscuridad y el rótulo «ENTRANDO AL NETHER…», y ese suspense
  tapa la subida del siguiente, que empieza con el pico saliendo al Nether. La edición sale
  un poco antes de que acabe el GIF (`reveal_delay`) para que no vuelva a empezar.
- Si una dimensión sola pasa del tope de peso, se corta dentro de ella en un momento
  natural (una caída por una cueva, el fogonazo de una TNT) o se bajan los fps de ese
  tramo. Nunca se quitan partes de la partida.
- Al acabar, el último GIF se cambia por un PNG del fotograma final.
- Montar un GIF guarda hoy todos los fotogramas a la vez (medio MB cada uno a 270×480). Con
  segmentos de cientos de fotogramas puede hacer falta montarlo por tramos. Se mide en la
  fase 3.
- Texturas: un pack de estilo vanilla con licencia que permita tenerlo en un repo público
  (los dos repos lo son), con su licencia y su atribución en `assets/picar/`.

## Dónde va cada cosa

- `src/bot/services/picar_world.py`: bloques, capas y generación del mundo.
- `src/bot/services/picar_physics.py`: el motor y `simulate(semilla, pico)`.
- `src/bot/services/picar.py` (fase 2): valores, premios, biblioteca y calibración.
- `src/bot/services/picar_scene.py` y `src/bot/assets/picar/` (fase 3): escena, texturas y
  `calibracion.json`, todo en `package-data`.
- `src/bot/cogs/picar.py` (fase 4).
- `docs/calibrar_picar.py` (fase 2).
- `tests/unit/test_picar_*.py`.

## Fases

0. **Reconocimiento.** Hecho en el chat; las conclusiones están en este documento.
1. **Mundo y motor de físicas.** Código puro, sin Discord ni dibujo. Pruebas: misma semilla
   y mismo pico dan el mismo registro, el pico nunca atraviesa un bloque sin romperlo y la
   partida siempre termina.
2. **Prototipo del dibujo.** Antes de calibrar: lo que pese un segundo de partida decide
   cuánto mide cada dimensión, y cambiar el mundo obliga a recalibrar.
3. **Calibración** con la biblioteca de semillas y el informe por pico (RTP exacto, % de
   tiradas con premio mayor que la apuesta, percentiles del premio, frecuencia del dragón,
   profundidad y duración medias).
4. **Dibujo completo**: segmentos por dimensión, transiciones, pintar antes del clic.
5. **Discord y economía**: comando `picar`, botón por pico, `settle_bet`, renta, estadísticas
   del casino (clave en `casino_stats.GAMES`), tirada guardada con semilla y hash, y una
   herramienta de administración (permiso de Administrador) para reproducir una tirada.
6. **Progresión y logros**: récord de profundidad por pico, ranking, más de 40 logros. El
   menú 🎰 Casino de los logros ya tiene las 25 opciones: hay que paginarlo o juntar
   secciones.
7. **Prueba de carga en el NAS y ajuste final** con el informe de uso.

Hasta que se pueda jugar (fase 5), el trabajo se acumula en el fork y no se abre PR a
godzilin: cada PR allí sale en «📜 Novedades» y sube versión.

## Estado

### Fase 1 (28/10/2026)

Mundo (`picar_world`) y motor (`picar_physics`) hechos, con sus pruebas: misma semilla,
misma partida (también sin fotogramas); el pico nunca se mete en un bloque, ni en el tick
en que muere; la partida siempre termina; las cuentas de vida y de bloques cuadran con
los eventos; el hash cambia con cualquier parámetro; y la física no usa funciones de la
libm.

Con las cifras de partida, 1.000 partidas por pico en este contenedor:

| Pico | ms por partida | Ticks de media | Fila media | p90 | Máxima | Nether | End |
|---|---|---|---|---|---|---|---|
| Madera | 0,5 | 186 | 4 | 5 | 9 | 0 % | 0 % |
| Piedra | 0,5 | 223 | 11 | 14 | 25 | 0 % | 0 % |
| Hierro | 1,2 | 401 | 22 | 30 | 60 | 0 % | 0 % |
| Oro | 0,4 | 102 | 10 | 15 | 28 | 0 % | 0 % |
| Diamante | 2,9 | 891 | 105 | 135 | 180 | 0 % | 0 % |
| Netherite | 5,9 | 1.849 | 174 | 216 | 537 | 3,9 % | 1,6 % |

- **Simular es barato.** Un millón de partidas de los seis picos son ~11.000 s de CPU: unos
  10 minutos en los 24 hilos del PC. Calibrar con el millón no es un problema.
- **Las profundidades salen muy concentradas.** La madera nunca pasa de la fila 9 y el
  diamante nunca llega al Nether. Las metas piden colas largas (madera al Nether en el
  0,1 %), y eso no se consigue tocando la vida: con una distribución tan estrecha, subir la
  vida mueve toda la campana, no alarga la cola. Hace falta algo en el mundo que dé saltos
  grandes de vez en cuando: cofres que den más vida, cuevas o barrancos de muchas filas,
  cadenas de TNT más largas. Se decide antes de calibrar.
- Hubo un fallo de redondeo que dejaba al pico rozando los bloques y disparaba la red de
  seguridad unas 50 veces por partida (y los empujones por atasco). Se arregló con `SLOP` y
  ahora no salta en 18.000 partidas.

### Colas largas (28/10/2026)

Para que de vez en cuando un pico baje mucho más de lo normal se añadieron tres cosas:

- **Mesas de crafteo** (como en el juego original): devuelven toda la vida inicial. Están en
  las cuevas y en la profundidad, no en el Nether ni en el End.
- **Cofres del tesoro**: dan entre el 80 y el 120 % de la vida inicial (los normales, del 15
  al 40 %).
- **Barrancos**: grietas de 1 o 2 columnas y de 20 a 60 filas por las que el pico cae libre.

Además, el Nether lleva piedra negra (dureza 3) entre el netherrack, para que cruzarlo
cueste. Con 2.000 partidas por pico y la vida de partida:

| Pico | Vida | p50 | p99 | Máxima | Nether | End | Dragón |
|---|---|---|---|---|---|---|---|
| Madera | 40 | 4 | 10 | 31 | 0 % | 0 % | 0 |
| Hierro | 300 | 88 | 225 | 512 | 0,55 % | 0,05 % | 0 |
| Diamante | 350 | 171 | 536 | 605 | 20 % | 6 % | 0 |
| Netherite | 600 | 516 | 700 | 700 | 75 % | 57 % | 37 |

Lo que se aprende para calibrar:

- **La vida decide cuántos llegan al Nether; lo que cuesta cruzarlo decide cuántos de esos
  llegan al End.** Con netherite, las metas piden un 60 % al Nether y un 5 % al End: de cada
  12 que entran, uno sale por abajo. Hoy sale más de la mitad. Hace falta un Nether bastante
  más caro de cruzar (más piedra negra, más filas o menos cofres) y se ajusta al calibrar.
- **La madera no llega al Nether** ni con 8 veces su vida (p99 en la fila 113 con 320 de
  vida). La meta del 0,1 % pide otra cosa o se rebaja; el dragón con madera, si se quiere,
  entra en la biblioteca con una semilla buscada a propósito y un peso pequeño.
- La partida se alarga: el netherite con 600 de vida dura 5.000 ticks de media, 83 s de
  simulación. Cuánto se ve de eso en el GIF lo dice el prototipo del dibujo.

### Fases 2 a 6 (28/10/2026)

- **Texturas**: Pixel Perfection CE (CC BY-SA 4.0), recortadas a un atlas con
  `docs/picar_texturas.py`; lo que el pack no trae (pizarra, minerales hondos, cristal del
  End, cofres) sale de sus texturas, y el dragón es dibujo propio. Licencia y cambios en
  `src/bot/assets/picar/LICENCIA.md`.
- **Dibujo** (`picar_scene`, `assets/picar/escena.html`): 288 × 512 (bloques de 32 px, el
  doble exacto de las texturas), 20 fotogramas por segundo en tiempo real.
  - La cámara se queda quieta mientras el pico está en una franja y se desplaza deprisa
    cuando sale: con la cámara siguiendo al pico cada fotograma, el GIF pesaba ~800 KB por
    segundo; así, ~200-300 KB.
  - Un GIF por dimensión, con el rótulo y una pausa al cambiar; si no cabe en el 80 % del
    límite de subida, se parte con una pausa corta. El primero dura ~4 s para que el clic
    responda enseguida. Se monta por tramos con `GifStream` (memoria del proceso: ~365 MB
    frente a ~800 MB montándolo entero).
  - Medido: una partida de netherite que llega al End son 6 GIF de 1-8 MB y unos 3 min;
    cada parte se pinta en 2-4 s, mucho menos de lo que dura la anterior.
- **Bloques de multiplicador** (oro ×2, diamante ×3, netherite ×5, baliza ×10): sin ellos
  el juego no tenía riesgo (el netherite pagaba como mucho ×4, mediana ×0,99). Con ellos,
  el diamante llega a ×237 y el netherite a ×622 en 4.000 tiradas.
- **Vidas**: los picos pequeños por duración (madera ~8 s, oro ~10, piedra ~13, hierro
  ~18) y los grandes por llegar al Nether (diamante ~26 %, netherite ~61 %). El Nether
  lleva piedra negra y no tiene mesas ni cofres, y el End es una muralla: el dragón tiene
  que salir muy poco para caber en el 99,5 %.
- **Valores** en diezmilésimas del coste (`picar.SCALE`): con céntimos, un céntimo más
  movía el RTP varios puntos.
- **Discord** (`cogs/picar.py`): `picar`, un botón por pico, 📋 Premios y 🏆 Récords;
  `settle_bet` y la tirada apuntada (`picar_plays`) antes de dibujar; `repicar` en Admin.
- **Logros**: 74 en ⛏️ Pico. El desplegable de 🎰 Casino se parte en dos.

### Pendiente

- Calibración definitiva con un millón de semillas (la del repo puede ser la de prueba
  hasta que acabe). Con `--reusar` se retoma si se corta.
- **El netherite llega al End en el ~14 % de las tiradas** (meta inicial: 5 %). Bajarlo
  pide un Nether que cueste más al netherite que al diamante; se decide jugando.
- Las rarezas de los logros salen de 3.000 tiradas por pico, no de
  `docs/auditoria_logros.py`; meter el pico allí es lo siguiente para afinarlas.
- Fase 7, en el NAS: 20 partidas seguidas con varias de netherite, mirando memoria, CPU,
  «Bloqueos del bucle» y «Subidas a Discord» en el informe.

### Metas de profundidad (28/10/2026)

Acordadas en el chat y conseguidas con el millón de semillas (`docs/picar-calibracion.md`):

| Pico | Nether | End |
|---|---|---|
| Madera | nunca (su techo son las cuevas) | nunca |
| Piedra | ~1 de cada 20.000 | nunca |
| Hierro | ~1 de cada 2.000 | nunca |
| Oro | ~1 de cada 2.500 | nunca |
| Diamante | ~21 % | ~0,25 % (meta, 1 %) |
| Netherite | ~53 % | ~5 % |

- La cola de los picos pequeños sale de **barrancos hondos** (de 30 a 128 filas, uno por
  cada región de 128 filas en las cuevas y la profundidad), no de más vida: así no se
  alargan las partidas. Diamante y netherite bajan de vida (365 y 495).
- El Nether lleva más piedra negra (500 de cada 1.000) y algún cofre y barranco. El
  netherite cruza el Nether unas cinco veces mejor que el diamante porque llega con más
  vida; por eso el End del diamante se queda en el 0,25 % y no en el 1 %: subirlo dispara
  el del netherite.
- **Motor en C para calibrar** (`docs/picar_rapido.c`): mismos resultados bit a bit que el
  de Python y 15-28 veces más rápido. Una calibración completa con un millón de semillas
  de los seis picos tarda ~9 minutos en 4 núcleos (antes, ~4 horas). Cada barrido de
  parámetros de 300.000 partidas, ~20 s.
- **Pendiente: el dragón.** El End es hoy una muralla y el dragón no sale en el millón.
  Su frecuencia y los topes están por decidir.
- **Fiscalidad de los premios gordos.** El pico es juego de casino (operador privado), no
  una lotería del Estado: el gravamen especial del 20 % de la DA 33ª LIRPF no aplica. El
  premio es ganancia patrimonial de la base general (art. 33.1 y 46 LIRPF): retención
  diaria del 19 % y la declaración de los lunes, con la escala progresiva (hasta el 50,5 %
  marginal, estatal 24,5 % y canaria 26 %).
