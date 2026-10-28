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
