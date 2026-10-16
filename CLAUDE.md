# Instrucciones para agentes

Antes de tocar código, lee `Biblia.txt`: son las normas del proyecto (estructura,
nombres de comandos, documentación, pruebas). Están en español y mandan sobre
cualquier costumbre propia.

Lo que más se olvida:

- **Dinero:** todo lo que gane o gaste yapdollars pasa por `EconomyService`, tiene un
  tratamiento fiscal decidido y documentado (IRPF, juego, exento u otro impuesto),
  manda lo recaudado a la cuenta del Estado y, si es una interacción (botón o
  slash), llama a `renta.remind(bot, interaction)` para el aviso de la Renta. Detalle
  en la sección "Dinero, impuestos y la Renta" de `Biblia.txt`.
- **Logros:** cada funcionalidad del bot tiene logros asociados; una nueva no está
  terminada sin los suyos, y cuantos más, mejor. Se alimentan con `casino_play`, `track`, `note` o `note_for` de
  `bot.cogs.achievements`. La rareza sigue la escala por esfuerzo de la Biblia; los de
  suerte del casino se calibran con `docs/auditoria_logros.py`. Sección "Logros" de
  `Biblia.txt`, que tiene la lista de familias de logros que hay que cubrir. Al
  rebautizar uno se cambian nombre y descripción, nunca la `key`.
- **Personalidad:** todo lo que dice el bot (frases de Jovani, anuncios, logros) es
  variado y jocoso, sin cupo: tantas bromas como cosas de las que reírse haya. Fuentes:
  la España de Sánchez, la oposición (PP, Vox, Podemos y Sumar, Junts), la España de
  Franco (el régimen, nunca apología ni víctimas), Europa (Macron, Boris, Erdoğan),
  EE. UU. (Trump, los Clinton, Obama, Biden, Kamala, Musk), Putin, Kim Jong-un, África,
  Sudamérica (Milei, Maduro, Bolsonaro), Xi y Netanyahu; siempre contra gobiernos y
  figuras públicas, nunca contra pueblos. Las coletillas de Jovani («¡Toma mangó!»,
  «¡Quiero café!», «¿Con pulpa? ¡Sin pulpa!», la motora de Víctor y Joel…; lista en la
  Biblia) salen de vez en cuando, no en cada frase. Sección
  «Personalidad: frases y fuentes de ironía» de `Biblia.txt`.
- **Tienda y mascotas:** un artículo nuevo sigue la ficha de alta de la Biblia (tipo por lo
  que hace, pasillo por su tema). Todo resultado nuevo deja hablar a la mascota activa:
  pasa un `Moment` a `renta.hint` o llama a `mascotas.cameo`. Sección «Mascotas y cameos»
  de `Biblia.txt`.
- **Comandos:** un solo nombre en español de 8 caracteres como máximo, idéntico con `/`
  y con `.`, sin alias ni subcomandos (`/poner`, no `/play`). Sección "Cogs y comandos"
  de `Biblia.txt`.
- **Entre cogs:** las funciones puente buscan el cog con `bot.utils.cogs.find_cog`, nunca
  con `isinstance`. Lo que cruza de un cog a otro se prueba también con el bot real
  (`BotClient` + `INITIAL_EXTENSIONS`, ver `tests/integration/test_cog_bridges.py`): los
  cogs montados a mano no reproducen producción. Si algo falla solo en el bot desplegado,
  reprodúcelo así antes de culpar a Docker o a la base de datos. Sección "Pruebas y
  calidad" de `Biblia.txt`.
- **Botones:** contestan a Discord antes de tocar la base de datos. Sección siguiente.
- **Subagentes:** solo Haiku (lo mecánico) o Sonnet (programar), en su última versión, uno a
  la vez y para una tarea bien acotada. Si pueden hacerlo, se les delega y se espera, revisando
  siempre su trabajo antes de fusionar. Sección «Agentes y subagentes» de `Biblia.txt`.
- **Decidir con datos:** antes de optimizar, quitar o rehacer algo, mira si hay
  informes de uso en `docs/metricas/` y pásalos por `docs/analisis_metricas.py`
  (botones lentos, fallos, retención, memoria del NAS, bloqueos del bucle). El bot deja
  cada día el informe en `informes/` del NAS (README, «Telemetría interna») y lo sube
  al canal que eligió un administrador con `/informe canal:`; bájalo de ahí con
  `docs/descargar_informe.py` (necesita `INFORME_TOKEN` e `INFORME_CANAL` en `.env`) y
  mira la visión global con `docs/analisis_metricas.py docs/metricas/` («Versiones y
  reinicios» compara cada versión con las anteriores). Si no hay
  uno reciente, pídeselo: lo saca un administrador con `/informe`. La telemetría no lleva logros.
- **Versión:** todo cambio que entra en `main` sube `__version__` (`src/bot/__init__.py`)
  y añade su entrada arriba en `CHANGELOG.md`. MAYOR si algo que la gente ya hacía deja
  de funcionar igual (comando quitado o renombrado, datos perdidos), MENOR si hay algo
  nuevo que hacer o ver, PARCHE para arreglos, equilibrio, velocidad y lo interno. En
  la duda, el escalón más alto. Sección «Versiones» de `Biblia.txt`.
- **Comprobar antes de entregar:** `ruff check src tests`, `ruff format --check src tests`
  y `python -m pytest -q`.

## Botones rápidos

Discord da 3 segundos para contestar a un clic (botón, menú o formulario). Si no
llega nada, el botón se queda «pensando» y acaba en «Esta interacción ha fallado»,
aunque el bot termine el trabajo después. Python casi nunca es lo lento. Lo lento
es la base de datos (sobre todo cuando espera a otra escritura), dibujar una
imagen, llamar a la API de Discord (roles, apodos) y esperar un `asyncio.Lock`.

Todo botón nuevo, o que se toque, sigue estas reglas. Las funciones están en
`bot.utils.interactions`.

1. **Si antes de enseñar el resultado hay que esperar algo de lo de arriba, la
   primera línea útil del callback es `await ack(interaction)`.** Las
   comprobaciones que solo miran memoria (¿es el dueño?, ¿está ocupado?) pueden ir
   antes y contestar directamente. El `ack` va también antes de coger un candado.
2. **Después de `ack`, se contesta con `edit(interaction, ...)` para cambiar el
   mensaje del botón y con `notify(interaction, "...")` para un aviso aparte**
   (privado por defecto, `ephemeral=False` para uno público). Nunca con
   `interaction.response.*`, que ya está gastado y lanza `InteractionResponded`.
   `edit` y `notify` valen igual sin `ack`, así que una función compartida puede
   usarlas sin saber de dónde la llaman.
3. **Si la respuesta es un mensaje privado nuevo** (abrir tu propio panel, una
   ficha, unas estadísticas), se usa `ack(interaction, new_message=True)`:
   Discord enseña «pensando…» en privado y `edit(interaction, ...)` lo rellena,
   también con los errores (`edit(interaction, content="...")`). Así no queda un
   «pensando…» colgado.
4. **Si el botón solo cambia memoria** (pasar de página con los datos ya
   cargados, el minijuego de `pala`), se contesta directamente con
   `interaction.response.edit_message`: es lo más rápido y no hace falta `ack`.
5. **Un botón que abre un formulario no puede hacer `ack`**: `send_modal` tiene
   que ser la primera respuesta. Las comprobaciones previas a abrirlo solo pueden
   mirar memoria. El `ack` va en el `on_submit` del formulario.
6. **Lo que no hace falta para enseñar el resultado va después de contestar:**
   logros (`casino_play`, `track`), `apuestas.record`, `renta.remind`, anuncios en
   el canal.
7. **Clics en ráfaga contra un mismo candado** (como el minijuego de `pala`): el
   clic que llega tarde se acepta fuera del candado. Si se acepta dentro, cada
   clic espera a que el anterior termine de hablar con Discord y los últimos pasan
   de 3 segundos.
8. **La base de datos se abre siempre con `bot.repositories.sqlite.connect`**, que
   activa el modo WAL. Una escritura no sincroniza el disco del NAS y una lectura
   no espera a una escritura. Un repositorio nuevo no llama a `sqlite3.connect` a
   mano.

9. **Los botones que se rehacen llevan `custom_id` fijo** (`minas:tile:5`, no el
   que pone discord.py al azar). Entre `clear_items()` y que Discord tenga el
   mensaje nuevo, un clic llega a un botón que ya no está en la vista; el rescate
   de clics del cog `Errors` lo pasa al botón que lo sustituye por su `custom_id`.
   Con uno al azar no lo encuentra y el clic se pierde (se acepta sin hacer nada).
   El mismo rescate contesta con un aviso a los clics en vistas caducadas o de
   antes de un reinicio. Un listener que atienda botones por su cuenta lo dice con
   `claim_custom_ids(prefijo)` (`pala`).

Para probarlo, el doble de `tests/interaction_fakes.py` (`fake_interaction`) se porta
como Discord: una sola respuesta y `is_done()` de verdad. Con un `MagicMock` sin más,
`is_done()` siempre es falso y la prueba no ve si el botón contesta tarde o dos veces.
Un botón nuevo que toque la base de datos se añade a
`tests/integration/test_button_speed.py`, que pulsa botones del bot real y falla si
la primera conexión a SQLite llega antes que la respuesta a Discord.

## Rendimiento en el NAS (4 hilos)

El bot corre en un NAS con 4 hilos. Lo lento de un juego casi nunca es la lógica:
es dibujar, comprimir y mover imágenes. Antes de optimizar, se mide por tramos
(navegador, montar fotogramas, codificar el GIF) con el navegador ya abierto: el
primer dibujo tras arrancar Chromium siempre es más lento. Lo aprendido con la
ruleta, que pasó de 4-7 s a ~2 s por tirada:

- **Escenas de Chromium: `BrowserScene.render`.** Reparte los fotogramas entre
  `TABS` (2) pestañas que pintan a la vez, cada una en su proceso: el navegador
  tarda la mitad. Cada pestaña hace `setup` y su primer fotograma sale entero, y
  `renderFrames(states, first)` recibe el índice absoluto, así que una escena
  nueva no puede guardar estado entre lotes que no se reinicie en `setup`. Se
  comprueba que dos pestañas dan los mismos fotogramas que una. Más de 2
  pestañas no compensa: deja sin hilos al bot y a la codificación del GIF.
- **Sin navegador: `NodeScene`.** Una escena que solo usa canvas 2D se pinta
  en Node con `@napi-rs/canvas` (Skia, el mismo motor que Chrome) cargando la
  misma `escena.html` (`assets/escena_node.cjs`). La escena devuelve cada
  recuadro con `window.exportFrame(lienzo)` y no con `toDataURL`, y así Node lo
  manda en RGBA crudo, sin PNG. Con la moneda: arranca en 0,35 s (Chromium,
  2,9 s), ocupa 177 MB frente a 580 y la tirada baja de 1,3 a 1,05 s. Pinta
  lo mismo salvo algún borde de letra. Lo que queda es el raster de Skia, igual
  en los dos: para bajar más hay que pintar menos, no cambiar de motor. En
  Node, usar un lienzo como origen de `drawImage` lo copia entero cada vez.
- **Los procesos de Node son de todos los juegos** (`node_scene._Pool`). Cada
  uno carga la escena que le pidan en su propio contexto `vm` (hasta 3; suelta
  la más vieja). Hay uno siempre abierto desde que arranca el bot (`warm`); en
  cuanto empieza un dibujo se abren los que faltan hasta 4, y los de más se
  cierran tras 10 minutos sin uso. Todos colaboran en cada dibujo: empieza con
  los libres (a partes iguales con los dibujos en marcha) y cada proceso que se
  abre o se queda libre a mitad se une y se lleva la mitad final del tramo con
  más trabajo pendiente (`_steal`). Cada tramo empieza con `setup` y un
  fotograma entero, así que una escena no puede guardar estado entre
  fotogramas salvo que lo recree con los de antes (`warmup`, el polvo de los
  caballos). Frente a 2 procesos fijos por juego, el dibujo bajó un 40-55 %
  (gallos, 2,4 a 1,1 s; bacará, 1,1 a 0,5 s; moneda, 0,40 a 0,24 s).
- **El GIF se monta mientras Node pinta.** Los fotogramas llegan en RGBA crudo,
  se abren y se montan en un hilo (no en el bucle de eventos) y cada uno empieza
  a codificarse ya (`gif.precode`): `local_palette_gif` solo espera lo que falte.
  `render` devuelve los fotogramas enteros.
- **En el navegador, lo caro es el PNG.** `toDataURL` comprime en un solo hilo
  (`toBlob` no ayuda sin GPU) y cada fotograma viaja en base64 a Python. Hay
  que devolver solo el recuadro que cambia y no marcar `full` sin necesidad: un
  marcador que cambia en cada fotograma obliga a mandarlos enteros. WebP sin
  pérdida pesa menos pero tarda más en comprimirse.
- **En Python, lo que no depende del orden va en varios hilos.** Pillow suelta el
  GIL al cuantizar, al comprimir y al descomprimir: `bot.utils.gif` codifica
  cada fotograma (paleta y LZW) en `QUANTIZE_THREADS` (4) hilos y escribe el GIF
  a mano, y `browser_scene.assemble` descomprime los PNG igual. Pasarle la lista
  a `Image.save` era 2-3 veces más lento: comprime en un hilo y vuelve a comparar
  cada fotograma entero con el anterior.
  Lo que sí depende del fotograma anterior (comparar, pegar recuadros) va en orden.
  Los pools se crean dentro de la función, que ya corre en `asyncio.to_thread`.
- **Trabajar solo sobre lo que cambia.** Cuantizar la caja que cambia y no el
  fotograma entero: sin tramado, el resultado es idéntico. Antes de tocar
  `bot.utils.gif` se comprueba con GIF de juegos de verdad que se ve igual píxel
  a píxel, con las mismas duraciones, al abrirlo con Pillow y con Chromium
  (`ImageDecoder`, que es lo que usa Discord de escritorio).
- **Menos fotogramas donde no se notan.** Una fase rápida (la bola en la pista) va
  a 20 fps sin que se vea; la cámara lenta se hace con fotogramas más largos, no
  con más fotogramas.
- **Comprobar que llega al NAS.** La imagen instala el paquete y borra `src/`:
  un archivo nuevo de `assets/` tiene que entrar en `package-data` de
  `pyproject.toml` (lo vigila `tests/unit/test_packaging.py`). Si una escena no
  llega, el juego se dibuja con Pillow sin dar error: así estuvieron la moneda,
  los dados y la ruleta. Lo mismo con la memoria: cada Chromium abierto suma
  ~170 MB (el autobús, ~370 con sus dos pestañas) y cada proceso de Node ~80 MB
  más ~50 MB por escena cargada contra el `mem_limit` de `docker-compose.yml`;
  un juego nuevo con dibujo se suma a la cuenta de ahí.
  Si Node falla en el NAS, el juego vuelve a Chromium sin avisar a nadie: el log
  dice «El pintor de … no está disponible».
- **Tapar la espera.** Si el dibujo tarda más de un segundo, la jugada se enseña
  ya («🎲 No va más…» con los botones apagados) y el GIF llega después. El cambio
  va en paralelo al dibujo, nunca antes.
- **Mejor aún, pintar antes del clic.** Si el resultado de la próxima jugada se
  puede sortear por adelantado sin enseñarlo (la moneda: `CoinGame.upcoming`),
  la mesa pinta en segundo plano los GIF posibles (uno por botón) mientras se ve
  el GIF actual, y al pulsar solo queda subirlo (`CoinView.prepare`). En la
  moneda, del clic al GIF pasó de ~1,1 s de dibujo a 0; en los dados, de ~0,9 s
  a 0 (`CrapsView.prepare`). La ruleta ya precargaba la jugada más probable
  (repetir la última apuesta) y con Node el giro bajó de 1,8 a 1,1 s. La timba pinta
  durante cada turno la mesa de cada botón y la de dormirse, y durante la espera
  entre manos reparte y pinta la siguiente (`Table.prepare_turn`, `Table.plan_deal`). Los dibujos por adelantado no se cancelan a medias:
  van de uno en uno y cada uno comprueba antes de empezar si sigue valiendo. Si
  aun así se corta uno, `NodeScene` mata los procesos de ese dibujo y el grupo
  abre otros (~0,2 s), porque quedarían píxeles sin leer en la tubería.

## Acelerar un juego: receta de la moneda

La moneda pasó de sentirse lenta («clic, nada, GIF») a salir nada más pulsar. Lo
que más se nota no es el motor de dibujo sino lo que ve el usuario entre el clic y
el GIF. Por orden de ganancia:

1. **Medir del clic al GIF con el bot real**, no solo el dibujo: `load_bot` de
   `tests/integration/test_button_speed.py`, la escena de verdad (no el doble de
   `render_fakes`) y un `fake_interaction` cuyo `edit_original_response` apunta
   el momento de la primera edición con un `.gif`. Pulsar varias veces con pausas
   realistas (0,3 s y 1,5 s). Un cuelgue tampoco se ve sin esto: un clic sin
   respuesta suele ser que no cambia nada en pantalla, no que el bot se pare.
   En el NAS, lo que tarda subir cada archivo a Discord sale en el informe
   (tipo `upload`, sección «Subidas a Discord» de `docs/analisis_metricas.py`):
   con el GIF ya pintado antes del clic, es lo único que queda por esperar.
2. **Pintar antes del clic** («Rendimiento en el NAS»). Es la que convierte ~1 s de
   espera en 0. Hace falta que el resultado se pueda sortear antes sin enseñarlo y
   pocos botones distintos (en la moneda, dos GIF por tirada; en los dados, uno
   con el punto puesto y dos en la salida). Todo lo que cambie lo que se ve antes
   de tirar (las Odds de los dados, la apuesta) entra en la clave del plan
   (`_plan_key`) y lo vuelve a pintar. Se empieza en
   cuanto sale el GIF actual, no al enseñar el resultado: si no, quien pulsa rápido
   sigue esperando. Con un `renderer` de prueba se apaga (`ahead=False`), y las
   pruebas de integración que cambian el dibujo por un doble también
   (`cog.ahead = False`): cada dibujo de más gasta el azar de guion.
3. **Tapar la espera**, para cuando no hay nada pintado: apagar los botones en
   paralelo al dibujo. Se apagan los que hay, sin `rebuild()`: las etiquetas nuevas
   (×8, la apuesta) delatan el resultado antes de ver el GIF.
4. **Pasar la escena de Chromium a Node** (`NodeScene`). Da poca velocidad (en la
   moneda, la tirada bajó un 20 %) pero mucha memoria (177 MB frente a 580) y
   arranque (0,35 s frente a 2,9). Pasos:
   - En la escena, `u: window.exportFrame(out)` en lugar de `toDataURL`, con
     `window.exportFrame ??= (c) => c.toDataURL("image/png")` para que Chromium
     siga valiendo.
   - Solo canvas: el `document` de `escena_node.cjs` solo crea lienzos. Lo que use
     `<div>` o SVG (el cartel de los caballos) se pasa a canvas o se queda en
     Chromium. Las fuentes de los `@font-face` se registran solas.
   - La clase de la escena prueba Node, luego Chromium y luego Pillow
     (`CoinScene._frames`). Las pruebas que quieren Chromium o Pillow pasan
     `node="/no/existe/node"`.
   - Una prueba compara Node con Chromium fotograma a fotograma (diferencia media
     por debajo de 4/255; solo cambia algún borde de letra).
5. **Recortar lo que no se ve.** Mirar qué tramo pesa con `perf_counter` en
   Python y, en Node, forzando el raster: Skia dibuja en diferido y el coste
   aparece al leer, así que un `ctx.getImageData(0, 0, 1, 1)` tras cada tramo dice
   lo que cuesta de verdad. Con la moneda: el PNG final tardaba 227 ms con
   `optimize=True` y 34 con `compress_level=6`.

Cuándo Node no acelera: en el autobús, pintar una mano baja solo de 1,35 a 1,31
s, porque lo caro es montar sus dos GIF (acierto y fallo) en Python. Ahí se gana
memoria, no tiempo. En cambio, la carrera de caballos (130 fotogramas enteros)
bajó de 5,6 a 3,6 s: cuanto más PNG mandaba Chromium, más se gana. Antes de
prometer velocidad, medir qué tramo pesa.

Escenas que no siguen `setup`/`renderFrames` usan `NodeScene.sequence`: varias
llamadas en orden y en un solo proceso (el podio de los caballos, a la vez que la
carrera). Una escena que arrastra estado de un fotograma al siguiente no usa
`sequence`: lo hace repetible por fotograma y lo recrea con `warmup` (los caballos,
`DUST_FRAMES`), con una prueba que compare repartida contra de un tirón. Cualquier función de la escena puede devolver
imágenes de `exportFrame`. Lo que sea HTML con CSS (la parrilla y el boleto de
los caballos, capturas de pantalla) se queda en Chromium.

Rehacer un juego con escena nueva (el Pollo): la animación se queda en Python
(`chicken_render.timeline`, que ya usaba Pillow) y la escena solo pinta lo que
le pasa `chicken_scene.frame_state`. Así Pillow sigue de reserva con los mismos
estados, y la refactorización se comprueba sacando el mismo GIF byte a byte. Una
escena más rica puede pintar más lento que el Pillow de antes (el paso del Pollo,
442 ms frente a 259): pintar antes del clic lo tapa entero (del clic al GIF, de
~440 ms a 0).

Lo que no compensó, para no repetirlo:

- **PNG o GIF con menos colores.** La PNG de 256 colores pesa la cuarta parte,
  pero el plateado de la moneda viraba a verde. Los visuales mandan.
- **Congelar en `Image` los lienzos fijos** (caras, marcador) para esquivar la
  copia de `drawImage` en Node: un 10 % y más código.
- **Cambiar `imageSmoothingQuality`**: casi no cambia el tiempo.
- **Bajar `REVEAL_MARGIN_SECONDS` sin probarlo en Discord**: cubre lo que tarda el
  cliente en descargar el GIF, y si se queda corto el resultado sale antes de que
  acabe la animación.

Al tocar las dependencias de Node: versiones publicadas hace al menos dos
semanas, fijadas en `package.json` con su `package-lock.json`. Aquí `docker
build` no tiene red; se comprueba al menos que el `node` de `node:22-slim` y el
`node_modules` pintan la escena dentro de `python:3.13-slim` con el usuario `bot`.

## Juego nuevo con dibujo: de cero a rápido

Orden para que un juego nuevo con animación salga como los del casino (moneda,
dados, caballos). Cada paso trae su comprobación; la Biblia (sección 5) lo exige.

1. **Escena** (`assets/<juego>/escena.html`), solo canvas 2D:
   - API: `setup(meta)` reinicia todo lo que pasa de un fotograma a otro, así que
     el primero sale entero; `renderFrames(states, first, prior)` devuelve un
     recuadro por estado con `window.exportFrame(lienzo)`, y
     `window.exportFrame ??= (c) => c.toDataURL("image/png")` deja que Chromium
     siga valiendo. Solo el recuadro que cambia; entero si no hay anterior.
   - **Nada de `Math.random`.** Azar repetible por fotograma (`rng(semilla, frame)`,
     como `dustRng` de los caballos). Con `Math.random`, cada proceso pinta otra
     cosa y la prueba de repartido contra un proceso falla.
   - **Estado que pasa de un fotograma a otro** (partículas, estelas): se le pone
     una vida máxima en fotogramas y se recrea desde `prior` sin pintar, con un
     lienzo que no hace nada (`NOOP` y `replayDust` en `caballos/escena.html`). En
     Python, `render(..., warmup=N)` con N esa vida (`DUST_FRAMES`).
   - **Nombres:** en el contexto `vm` de Node las funciones de primer nivel son
     propiedades de `window`. `function warmDust(){}` más
     `window.warmDust = () => warmDust()` se llama a sí misma hasta «Maximum call
     stack size exceeded». La función interna lleva otro nombre.
   - La escena entra en `package-data` de `pyproject.toml`.
2. **Python** (`services/<juego>_scene.py`): Node, luego Chromium, luego Pillow,
   como `CoinScene`. `painter.render(meta, states)` ya devuelve los fotogramas
   enteros y los deja codificándose para el GIF: se pasan tal cual a
   `local_palette_gif(frames, ms)` dentro de `asyncio.to_thread`, los mismos
   objetos y en el mismo orden. Si se copian, convierten o reordenan, el GIF sale
   igual pero sin lo adelantado. Las duraciones salen de los estados, antes de
   pintar. Una imagen quieta: `await asyncio.to_thread(patch_png, patches[0])`,
   nunca en el bucle de eventos.
3. **El reparto es automático:** `render` usa los 4 procesos y los que se abren a
   mitad. `procs=1` solo en pruebas. `sequence` es para llamadas sueltas (el podio
   de los caballos) y puede ir a la vez que `render` con `asyncio.gather`.
4. **Cog:** pintar antes del clic (`prepare`, `_plan_key`), tapar la espera y no
   volver a subir una imagen que no ha cambiado: se guarda la clave de lo que se ve
   (`HousePanel._poster` en gallos) y se edita sin `attachments`. Cada mensaje con
   archivo cuesta ~1,1 s fijos en Discord aunque pese poco, y el tamaño solo suma
   ~0,4 s a partir de 1 MB (informe del 14/10/2026): mejor no subir que subir menos.
5. **Pruebas**, copiando las de los juegos que ya están:
   - Node contra Chromium, diferencia media por debajo de 4/255
     (`test_node_y_chromium_pintan_lo_mismo*`).
   - Repartido contra un solo proceso, byte a byte, comprobando que de verdad se ha
     repartido (contar los `setup`):
     `test_la_carrera_repartida_pinta_lo_mismo_que_de_un_tiron`,
     `test_los_que_se_unen_a_mitad_pintan_lo_mismo_que_uno_solo`.
   - Que no se resube lo que no cambia
     (`test_repintar_la_misma_presentacion_no_vuelve_a_subir_la_imagen`).
   - Al reescribir algo del camino de dibujo (`node_scene`, `gif`, `assemble`): el
     GIF y el PNG finales de cada juego con el mismo `sha256` que en `upstream/main`.
6. **Medir** con `docs/medir_dibujos.py`: se añade el juego a `GAMES` y se mide
   antes y después (`--repo ../viejo`), dos veces cada uno. «1 abierto» es la
   primera jugada tras 10 minutos sin jugar; «caliente», las siguientes.
   Referencia a 4 hilos (14/10/2026, ms): moneda 410/275, dados 470/280, autobús
   500/340, ruleta 620/380, bacará 820/630, caballos 900/750, backgammon (un turno
   entero con la respuesta de Jovani) 830/500.
7. **Memoria:** cada escena cargada suma ~50 MB a cada proceso de Node que la pinte
   (hasta 4 procesos con 3 escenas cada uno). Se mira en «Sistema» del informe.
8. **Tras desplegar**, en el informe: «Dibujos» (ms del pintor), «Subidas a
   Discord», «Bloqueos del bucle» y memoria. «Bloqueos del bucle» tiene que estar
   vacío: si sale `bot/...:función`, esa función hace trabajo pesado en el bucle y
   va a `asyncio.to_thread`. `selectors.py:select` quiere decir que faltó CPU, no
   que algo bloquee.

## Notas para el agente en este repo

- Python del proyecto: `.venv/Scripts/python` (3.13, el mismo que la imagen del
  NAS). En la consola de Windows, `docs/analisis_metricas.py` necesita
  `PYTHONIOENCODING=utf-8` o revienta con `UnicodeEncodeError`.
- En este PC no hay Docker y la imagen no se puede construir. Si un cambio la
  rompe, `actualizar.sh` vuelve sola a la anterior en el NAS; se avisa en la PR.
- Sin atar a 4 hilos, este PC (24 hilos) da tiempos que el NAS nunca verá;
  `docs/medir_dibujos.py` se ata solo. El N100 va ~1,5 veces más lento por hilo,
  pero las comparaciones de antes y después sí valen.
- Para comparar con lo desplegado: `git worktree add ../viejo upstream/main`. Allí
  Node necesita `NODE_PATH` apuntando al `node_modules` de aquí (el script lo pone).
- Lo que no se repite en `node_scene`: repartir solo entre los procesos libres al
  empezar, porque casi siempre hay uno solo y los que se abren después no ayudan;
  y dar trabajo a un proceso antes de que cargue la escena, porque el que ya
  pintaba se queda esperándolo (los dados empeoraban 100 ms).

## Dos repositorios

- `godzilin/Proyecto_BOT_JovaniVazquez` es el del bot: su `main` es lo que se despliega.
- `Yeyo-Yeyex/Proyecto_BOT_JovaniVazquez_forkyeyo` es un fork que se usa para dar
  contexto en cada chat. Se trabaja aquí.
- Al terminar un cambio, los dos `main` tienen que quedar con la versión más
  actualizada. Primero se trae el `main` de godzilin (`git remote add upstream
  https://github.com/godzilin/Proyecto_BOT_JovaniVazquez`, `git fetch upstream main`) y
  se fusiona con el del fork, resolviendo conflictos y pasando las comprobaciones. El
  resultado se sube al `main` del fork.
- El agente no tiene permiso de escritura en el repo de godzilin. Para llevar el `main`
  del fork allí, se le da a la persona el enlace para abrir la PR desde la web:
  https://github.com/godzilin/Proyecto_BOT_JovaniVazquez/compare/main...Yeyo-Yeyex:Proyecto_BOT_JovaniVazquez_forkyeyo:main
  Junto al enlace va el título y la descripción listos para pegar (ver abajo).

## Títulos y descripciones de PR

El título y la descripción de cada PR acaban en Discord: al desplegar, el bot publica
los PR nuevos como «📜 Novedades del bot», con la lista de títulos y, en modo
detallado, la descripción de cada uno (README, «Novedades»). Lo leen los miembros del
servidor, no programadores.

- **Título:** qué cambia para quien usa el bot, en español y sin jerga, con la forma
  «Funcionalidad: qué cambia» y el comando entre comillas invertidas si lo hay. 70
  caracteres como mucho: GitHub corta el resto con «…» en el commit de fusión.
  - Bien: «Caballos: `caballo`, carreras con cuotas de verdad», «Pala: los clics ya
    no se pierden».
  - Mal: «Claude/adoring tesla ybmnco» (el que propone GitHub con la rama: cámbialo
    siempre), «fix», «Add pets system», «Caballos», «Refactor de deploy.py».
  - Un arreglo interno que nadie nota se dice igual, en cristiano: «Interno: pruebas
    del casino con el bot real».
- **Un tema por PR.** Cada PR es una línea del aviso; si un PR junta tres cosas, el
  título las nombra todas o se parte en tres.
- **La PR del fork a godzilin** es lo único que sale en el aviso: los PR del fork que
  trae dentro no se anuncian. Su título resume todo lo nuevo («Caballos y una sola
  semana para el IRPF»), nunca «Caballos» ni «Merge main», y su descripción cuenta
  cada cambio, porque sale entera en Discord.
- **Asuntos de commit** en español y legibles: si un PR queda con el título
  automático, el aviso usa los asuntos de sus commits.
- **Descripción** (sale en Discord en el aviso detallado y la lee también quien
  revisa): primero qué cambia para quien usa el bot, en párrafos cortos; luego por
  qué, cómo se ha probado y lo que hay que vigilar al desplegar (logros retroactivos
  que se cobran de golpe, cambios de cifras de la economía, pasos a mano en el NAS).
  Sin listas de archivos tocados: eso ya lo enseña el diff. Nada que no deba leer el
  servidor (rutas del NAS con datos, IDs privados). La firma de Claude Code se quita
  sola al publicarla.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
