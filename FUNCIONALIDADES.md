# Especificación funcional del bot

Este documento describe las funcionalidades actuales y futuras y cómo debe comportarse el bot desde el punto de vista de sus usuarios y administradores. Complementa la [Biblia del proyecto](./Biblia.txt), que define las normas técnicas y de calidad.

La bienvenida/despedida descrita en la sección 3, los comandos `ping`, `help`, `level`, `top`, `magik`, `memes` y los 108 efectos de imagen, los comandos de música, los sonidos de entrada, la economía con la ruleta (`ruleta`, `saldo`, `daily`) los comandos de administración y `babel` están implementados. Las demás funciones son objetivos futuros salvo que se indique lo contrario.

## 1. Objetivo

Crear un bot de Discord en español que aporte a los servidores:

- Mensajes de bienvenida y despedida.
- Progresión de niveles basada en la participación mediante mensajes.
- Reproducción y control de música en canales de voz.
- Efectos de imagen divertidos: la deformación `magik` y los 108 efectos de Dank Memer.

La interfaz usa comandos de aplicación (`/`) y sus equivalentes de texto con el prefijo `.` (configurable con `COMMAND_PREFIX`), junto con botones o menús de Discord cuando corresponda. Los mensajes automáticos y avisos se enviarán en los canales configurados.

## 2. Usuarios y permisos

- **Miembro:** puede usar los comandos de nivel y música disponibles para cualquier usuario del servidor.
- **Moderador o administrador:** puede configurar la bienvenida/despedida y modificar ajustes del sistema de niveles.
- **Bot:** solo necesita permisos e intents relacionados con las funciones activadas en cada servidor.

Las comprobaciones de permisos deben hacerse en el servidor en cada operación protegida; ocultar un comando o botón no cuenta como autorización.

## 3. Bienvenida y despedida

### 3.1. Bienvenida

Implementación actual:

- Cuando una persona se una, el bot envía a `#chat-general` un mensaje con formato que pregunta **¿QUIÉN ERES?**.
- El mensaje menciona intencionalmente al nuevo miembro y adjunta `src/bot/assets/bienvenida.mp4`.
- Si el canal o el vídeo no están disponibles, o el bot no puede enviar el mensaje, registra el problema y no detiene el bot.

### 3.2. Despedida

Cuando una persona abandone el servidor, el bot publica en `#chat-general` una despedida con su nombre visible y una frase humorística elegida aleatoriamente de `src/bot/assets/despedidas.txt`. Ese archivo es texto plano editable (una frase por línea; las líneas vacías o que empiezan por `#` se ignoran) y se relee en cada despedida, sin reiniciar el bot. Las menciones están desactivadas para evitar notificaciones accidentales.

### 3.3. Configuración

La configuración por servidor aún no está disponible. Como siguiente mejora, los administradores podrán:

- Elegir por separado el canal de bienvenida y el de despedida.
- Activar o desactivar cada tipo de mensaje independientemente.
- Cambiar las plantillas de texto.
- Consultar la configuración actual.

Los mensajes deben limitar menciones accidentales a usuarios o roles. Si una plantilla incluye variables desconocidas o supera límites de Discord, la configuración debe rechazarse con un error comprensible, no fallar al procesar un evento.

### 3.4. Criterios de aceptación

- Un ingreso produce como máximo una bienvenida y una salida produce como máximo una despedida.
- Cada mensaje se envía únicamente al canal `#chat-general` del servidor donde ocurrió el evento.
- Los eventos de miembros requieren habilitar el intent privilegiado `Server Members Intent` en el portal de Discord y en el código.
- La función tolera canales eliminados y permisos revocados sin detener el bot.

## 4. Sistema de niveles por mensajes

### 4.0. Preparación: importación del historial

La progresión se preparó con una importación histórica puntual, ya completada en el servidor inicial. Sus comandos de mantenimiento se retiraron después de activar los niveles:

- La importación se inició manualmente y se ejecutó en segundo plano; no se ejecuta automáticamente al arrancar el bot.
- El bot recorre canales de texto, hilos activos y hilos archivados que pueda enumerar y leer. En hilos privados depende de que el bot esté unido al hilo o tenga permiso para administrar hilos. El resultado no puede incluir canales eliminados ni canales con permisos insuficientes.
- Se requieren `Ver canal` y `Leer el historial de mensajes` en cada canal o hilo. Leer mensajes de un hilo privado puede requerir, además, que el bot sea miembro del hilo o tenga permisos de administración de hilos.
- Se cuentan mensajes escritos por usuarios; se excluyen bots, webhooks y mensajes de sistema. Se cuentan mensajes aunque solo contengan un adjunto, porque el cálculo no debe depender del intent privilegiado `MESSAGE_CONTENT`.
- Se guarda únicamente un total agregado por ID de servidor y usuario. No se persisten contenidos, adjuntos ni IDs individuales de mensajes.
- La base SQLite local vive en `.data/message_stats.sqlite3`, está excluida de Git y persiste entre reinicios en el mismo entorno. Debe incluirse en las copias de seguridad del despliegue si se quiere conservar el progreso.
- El proceso guarda cada canal de forma atómica, evita sumar dos veces canales ya completados y puede reanudarse después de un reinicio. Los canales inaccesibles se reportan como fallidos; una importación parcial no debe presentarse como completa.
- `/niveles importacion` muestra los mensajes revisados y los mensajes de usuarios contados. El contador se persiste cada 100 mensajes y al cerrar cada canal; por ello el valor puede retrasarse hasta el siguiente lote. Los mensajes de bots/sistema cuentan como revisados, pero no como mensajes de usuario elegibles.
- Se fija un instante de corte al iniciar: los mensajes anteriores se agregan como históricos y los mensajes posteriores se cuentan en vivo, para evitar duplicarlos mientras avanza el recorrido. Mensajes enviados mientras el bot está apagado después del corte no pueden contabilizarse en vivo.

La importación no concede experiencia por sí sola. Al activar el sistema, el recuento importado más los mensajes nuevos registrados en vivo hasta ese momento se convirtieron una sola vez en **20 XP por mensaje**; los mensajes posteriores reciben XP en vivo solo cuando el sistema está activo. El recuento actual no es un libro de auditoría: no puede corregir cambios retroactivos del historial una vez importado.

### 4.1. Progresión

- El sistema asignará experiencia por mensajes elegibles y avanzará el nivel al alcanzar los umbrales definidos.
- La experiencia y el nivel serán independientes por servidor y por miembro.
- Solo los mensajes de personas reales contarán; se excluirán bots y webhooks.
- Se cuentan también los mensajes de usuario que solo tengan adjuntos; el bot no inspecciona su texto.
- Se otorgan **15–25 XP aleatorios** por mensaje de usuario elegible y se aplica un enfriamiento de **60 segundos por miembro y servidor**. Los mensajes creados durante el enfriamiento no dan XP.
- El contador en vivo y la progresión de XP excluyen bots, webhooks y eventos de sistema; no dependen del texto del mensaje. Por ello, no es necesario habilitar el intent privilegiado `MESSAGE_CONTENT`.
- No se vuelve a puntuar un mensaje editado. Editarlo no genera un evento de mensaje nuevo.
- Los umbrales y la cantidad de experiencia deben centralizarse en la lógica del sistema, documentarse y poder ajustarse sin duplicar reglas en los comandos.

**Fórmula MVP implementada:** nivel 1 requiere 100 XP acumulados; el nivel 2 requiere 300 XP totales, el nivel 3 requiere 600 XP, y cada nivel sucesivo añade 100 XP más que el umbral del anterior.

### 4.2. Comandos implementados

- `/level [miembro]` y `.level [miembro]`: muestran el nivel, experiencia actual y progreso al siguiente nivel del miembro indicado o de quien ejecuta el comando.
- `/top [pagina]` y `.top [pagina]`: muestran los miembros con más experiencia del servidor, ordenados de forma estable y con paginación.
- `/ping` y `.ping`: comprueban la latencia del bot.
- `/help` y `.help`: listan todos los comandos en un solo embed, por categorías (General, Música, Niveles, Casino, Entradas, Imagen y sus cuatro tipos de efecto), solo por nombre, sin descripción ni argumentos y en orden alfabético dentro de cada categoría. La categoría Admin solo aparece si quien pide la ayuda es administrador.

**Convención de nombres de comandos (norma del proyecto, ver Biblia):** cada comando tiene un único nombre corto de una palabra (máximo 8 caracteres), idéntico en `/` y en `.`. No hay alias, grupos ni subcomandos, para que el menú de `/` quede limpio. El prefijo de texto es `.` por defecto y se puede cambiar con `COMMAND_PREFIX`; los comandos `/` funcionan siempre, sea cual sea el prefijo. Para que Discord entregue mensajes a los comandos de texto, también debe habilitarse **Message Content Intent** en el portal de desarrolladores.

Los comandos temporales `/niveles importar`, `/niveles importacion`, `/niveles mensajes` y `/niveles activar` se retiraron del menú una vez completada la preparación del servidor inicial. La activación del sistema no se ofrece como comando público permanente.

### 4.3. Avisos y persistencia

- Al subir de nivel, el bot envía un aviso en el mismo canal donde se publicó el mensaje que otorgó la XP. Solo se muestra el nombre visible, sin activar menciones.
- No hay comandos públicos para consultar el estado ni para cambiar el canal de avisos, el enfriamiento o la activación del sistema.
- La experiencia, el nivel y los ajustes por servidor deben conservarse después de reiniciar el bot.
- La XP inicial se calcula una sola vez al activar el sistema, usando 20 XP por mensaje contado hasta ese momento; desactivar y reactivar no vuelve a conceder esa XP.
- El ranking no debe mostrar datos de otros servidores.
- No guardar contenido de mensajes para calcular experiencia; almacenar solo los identificadores y los datos de progresión que sean necesarios.
- Los cambios de nombre no deben crear perfiles duplicados: la identidad se basa en los ID de Discord.

### 4.4. Criterios de aceptación

- Un mensaje elegible otorga experiencia como máximo una vez durante el periodo de enfriamiento.
- Mensajes de bots, webhooks y mensajes de sistema no generan experiencia.
- La XP histórica no se inicializa antes de una importación completa ni se duplica al reactivar.
- El nivel y el progreso mostrado coinciden con los valores persistidos.
- Reiniciar el bot no borra niveles ni configuración.
- El ranking y las consultas siempre están limitados al servidor de la interacción.

## 5. Música

### 5.1. Comandos implementados

Cada acción tiene un único nombre, igual en `/` y en `.` (por ejemplo `/play` y `.play`). Ambas interfaces reutilizan la misma lógica interna; el adaptador compartido `CommandResponder` (`InteractionResponder`/`ContextResponder` en `bot.utils.responder`) oculta si la petición vino de una `discord.Interaction` o de un mensaje de texto.

- `play consulta`: busca en YouTube (o resuelve un enlace directo) mediante `yt-dlp` y reproduce el resultado; conecta al bot al canal de voz del miembro si aún no estaba conectado. Si ya hay una pista sonando, la añade al final de la cola.
- `pause` y `resume`: controlan la reproducción actual.
- `skip`: detiene la pista en curso; la cola continúa automáticamente con la siguiente.
- `queue`: muestra la pista actual y hasta diez pistas siguientes (con el resto resumido en un contador).
- `remove posicion`: elimina una pista de la cola por su posición (1 = la siguiente).
- `clear`: vacía la cola sin afectar a la pista en curso.
- `stop`: detiene la reproducción, vacía la cola y desconecta al bot del canal de voz.
- `volume valor`: cambia el volumen (1-200 %), incluso con una pista ya sonando.

La cola y el reproductor son independientes por servidor (`GuildMusicState` en `bot.cogs.music`). Las acciones de control (todas salvo `play`, que además puede conectar al bot, y `queue`, de solo lectura) exigen que quien las use esté conectado al mismo canal de voz que el bot.

Los comandos de texto con prefijo requieren el intent privilegiado **Message Content** habilitado en el portal de desarrolladores de Discord (además del ya requerido **Server Members**); sin él, el bot no puede leer el contenido de los mensajes y esos comandos no se dispararán (los comandos de aplicación `/` no se ven afectados).

### 5.2. Comportamiento y límites

- Se informa con un mensaje claro si la consulta no se puede resolver, la pista dura demasiado, la cola está llena o el miembro no está en el canal de voz adecuado.
- Al terminar una pista (o al fallar su reproducción), se continúa automáticamente con la siguiente de la cola; `stop` es la única acción que corta ese encadenamiento.
- Duración máxima por pista: 30 minutos (`MAX_TRACK_DURATION_SECONDS`); se rechazan también los directos, al no tener duración conocida. Tamaño máximo de cola: 50 pistas por servidor (`MAX_QUEUE_SIZE`).
- El bot abandona el canal de voz automáticamente si se queda sin oyentes humanos, o tras 5 minutos de inactividad sin pistas en cola (`IDLE_DISCONNECT_SECONDS`).
- El bot se conecta ensordecido (`self_deaf=True`): Discord deja de reenviarle el audio de los participantes, que no usa, y el tráfico de bajada en voz cae a casi cero.
- Si al pedir música el bot está en voz por un sonido de entrada (sección 6 bis), lo corta y conecta la música: la música tiene prioridad.
- Todas las conexiones de voz, procesos de `ffmpeg` y temporizadores de inactividad se cierran al detener, saltar, desconectar o descargar el cog (`cog_unload`).
- La cola vive en memoria y se pierde al reiniciar el bot; no se persiste audio ni historial de reproducción.
- Solo se admiten fuentes resueltas por `yt-dlp` (típicamente YouTube); no se intenta eludir restricciones de acceso, DRM ni condiciones de uso de los proveedores.
- Para evitar el `403 Forbidden` que YouTube devuelve si `ffmpeg` solicita el flujo sin las cabeceras HTTP originales, `yt-dlp` se configura para resolver con el cliente `android` y las cabeceras (`http_headers`) que devuelve se reenvían a `ffmpeg` mediante `-headers` al reproducir.

### 5.3. Criterios de aceptación

- Dos servidores pueden reproducir música y mantener colas simultáneas sin interferirse (estado indexado por ID de servidor).
- La reproducción y la cola avanzan correctamente al terminar, saltar, detener o fallar una pista.
- Los errores de conexión o de fuente no bloquean otros comandos ni dejan al bot en un estado irrecuperable.
- Usuarios no conectados al canal de voz adecuado reciben una respuesta clara y no alteran la reproducción.
- Los límites de cola y duración se aplican antes de aceptar trabajo adicional.

## 6. Imagen: `magik` y efectos de Dank Memer

Todos los comandos de imagen son **solo de texto** (prefijo `.`). Los slash commands se reservan para el resto del bot, y Discord limita a 100 los comandos de `/` de un bot: no cabrían.

### 6.1. Comportamiento de `magik`

`.magik [miembro]` deforma una imagen con **seam carving** (reescalado consciente del contenido), la técnica del comando `magik` de Dank Memer. A diferencia de estirar o recortar, el algoritmo calcula la "energía" de cada píxel (el contraste con sus vecinos) y elimina repetidamente la **costura** —un camino continuo de píxeles de arriba abajo— de menor energía acumulada. Se pierden primero las zonas planas y se conservan los bordes y las formas; al eliminar la mitad del ancho y del alto y volver a ampliar la imagen, los objetos se deforman y "derriten".

La imagen se elige, por orden de prioridad:

1. Un archivo de imagen adjunto al mensaje o al mensaje al que se responde.
2. El avatar del miembro indicado.
3. El avatar de quien ejecuta el comando.

La respuesta es un PNG (`magik.png`). Los GIF animados se reducen a su primer fotograma y se respeta la rotación EXIF de las fotos de móvil. El resultado se envía como un mensaje nuevo y el aviso «Distorsionando…» se borra.

### 6.2. Efectos de Dank Memer

Port de [imgen](https://github.com/DankMemer/imgen), el generador de imágenes de Dank Memer (licencia MIT). Cada uno de sus 108 efectos es un comando de texto con el nombre original (`.trigger`, `.slap`, `.changemymind`...). Esos nombres conservan la longitud de Dank Memer, aunque algunos superan las 8 letras, porque así los reconoce quien ya los usaba; `.help` los lista en subcategorías de Imagen según su tipo; `.memes` muestra la misma lista y `.memes <efecto>` explica el uso de uno.

Lectura de argumentos:

- **Avatares.** Una mención (`@alguien`) o responder a su mensaje elige el avatar; sin ninguna, se usa el de quien escribe. Una imagen adjunta (al mensaje o al respondido) sustituye al avatar del objetivo. En los efectos de dos personas, la primera es quien escribe y la segunda el objetivo; mencionando a dos, se usan esas dos. Sin objetivo, el bot explica el uso.
- **Textos.** El texto que queda tras quitar las menciones. Los efectos de varios campos los separan con `|` (`.brain agua | zumo | café | café a las 3`). Si faltan o sobran, el bot responde con el uso y un ejemplo. Máximo 300 caracteres por campo. En los efectos sin avatar, una mención se escribe con el nombre visible de esa persona (`.changemymind @Ana tiene razón`).
- **Nombres.** Los efectos que muestran un nombre (`tweet`, `quote`, `youtube`, `obama`, `byemom`, `sword`) usan el nombre visible y el de usuario del objetivo, o de quien escribe.

Formatos: PNG o JPEG según la plantilla; GIF en `trigger`, `dank`, `salty`, `airpods`, `america`, `communism` y `kowalski`; MP4 en `crab`, `letmein` y `scaryabove`, que se componen con `ffmpeg` (ya presente en la imagen Docker por la música), con 90 s de tiempo máximo y 2 hilos.

Diferencias conscientes con el original:

- Los emojis del texto no se dibujan como imágenes (exigía ~60 MB de PNG): los personalizados de Discord se escriben como `:nombre:` y los Unicode se quitan, porque las fuentes no tienen esos glifos.
- `dream` usaba DeepDream con TensorFlow; aquí es una imitación con Pillow y numpy (detalle amplificado a varias escalas, rotación de tono y saturación). `radialblur` y `warp` usaban ImageMagick y GraphicsMagick; se reimplementan con numpy.
- No se incluyen `profile` (la ficha del sistema de economía de Dank Memer, que este bot aún no tiene) ni `yomomma` (solo devuelve un chiste en inglés, no una imagen).

Las plantillas y fuentes viven en `src/bot/assets/memes` (~27 MB): solo las que usa algún efecto, con los BMP pasados a PNG sin pérdida y las fotos opacas grandes a JPEG de calidad 92.

### 6.3. Límites y seguridad comunes

- Solo se aceptan adjuntos de Discord y avatares; **nunca URLs arbitrarias**, para que el bot no pueda usarse para hacer peticiones a destinos elegidos por el usuario.
- Se comprueba el tipo declarado (`image/*`) y el tamaño (máximo 8 MB) antes de descargar, y las dimensiones de la cabecera (máximo 25 megapíxeles) antes de decodificar, para evitar "bombas de descompresión".
- `magik` trabaja sobre una versión reducida (lado mayor de 320 px). Todo el procesado se ejecuta fuera del event loop (`asyncio.to_thread`) y se limita a 2 trabajos simultáneos en todo el bot.
- Enfriamiento de 5 segundos por usuario, compartido entre `magik` y todos los efectos.
- Los efectos trabajan las fotos de usuario a 1024 px de lado como máximo.
- El bot necesita el permiso **Adjuntar archivos** en el canal. Si falta, el comando lo indica con un mensaje claro.
- Los errores (archivo ilegible, demasiado grande, fallo de descarga o de envío, fallo inesperado) se comunican con un mensaje claro, sin trazas; el aviso de progreso nunca se queda colgado.

### 6.4. Criterios de aceptación

- El resultado conserva los objetos de bordes marcados y sacrifica antes el fondo plano.
- La misma imagen produce siempre el mismo resultado.
- Una imagen inválida, truncada, demasiado pequeña o demasiado grande nunca provoca una excepción sin controlar.
- Ningún comando de imagen ocupa un slash command.
- Cada uno de los 108 efectos genera un archivo válido de menos de 8 MB con su ejemplo (prueba automática que los ejecuta todos).
- Una prueba de integración con objetos reales de discord.py (servidor, canal y mensaje con adjunto) recorre todo el camino de `.magik` y de los efectos: adjunto, menciones, textos con `|`, procesado, envío, enfriamiento compartido y errores.

### 6.5. Errores en comandos de texto y de aplicación

Cuando un comando falla, el bot responde con un mensaje breve y seguro en lugar de quedarse en silencio: argumento que falta o no válido, uso fuera de un servidor, o un fallo inesperado (que además se registra en el log con su traza). Escribir el prefijo seguido de algo que no es un comando no provoca ninguna respuesta.

## 6 bis. Sonidos de entrada

### 6 bis.1. Comportamiento

- Cada miembro configura su sonido con `entrada` (`/entrada` o `.entrada`). Sin argumentos muestra su estado; `archivo` sube un audio; `volumen` lo ajusta (10-200 %); `borrar` lo elimina. En texto, el audio va adjunto al mensaje y el argumento es el volumen o `borrar`.
- Al subirlo se responde con una vista previa del clip ya procesado (efímera en `/entrada`).
- Cuando el miembro entra o se mueve a un canal de voz (salvo el canal AFK), el bot se conecta ensordecido, reproduce el clip y sale. Si entran varias personas seguidas, los sonidos se encolan (máximo 5 por servidor) y se reutiliza la conexión.
- Si el bot ya está en voz por la música, el sonido de entrada no suena.
- Cada persona dispara su sonido como mucho una vez cada 30 s (`COOLDOWN_SECONDS`).

### 6 bis.2. Límites, rendimiento y seguridad

- Duración máxima: 3 s (`MAX_CLIP_SECONDS`, con 0,1 s de tolerancia por redondeo). Tamaño máximo del adjunto: 8 MB.
- Todo el trabajo pesado se hace al subir: `ffmpeg` convierte el audio una vez a Ogg/Opus 48 kHz estéreo con paquetes de 20 ms, normaliza la sonoridad (-16 LUFS) y aplica el volumen con un limitador para que el 200 % no sature. Al reproducir no se lanza ningún proceso: `OpusPacketSource` entrega los paquetes Opus del archivo directamente a `discord.py`.
- `ffmpeg` y `ffprobe` se ejecutan sin shell, con tiempo máximo y con `-protocol_whitelist file`, para que un adjunto manipulado no pueda hacerles abrir URLs.
- Se guardan solo la fuente normalizada, el clip final y el volumen, en `.data/entradas/<servidor>/<miembro>.*`. `borrar` elimina los tres archivos. No se guarda el adjunto original ni su nombre.
- Requiere los permisos **Conectar** y **Hablar** en los canales de voz; si faltan, o el canal está lleno, el sonido se omite.

### 6 bis.3. Criterios de aceptación

- Un audio de más de 3 s o que no sea audio se rechaza con un mensaje claro y sin guardar nada.
- Cambiar el volumen regenera el clip sin volver a subir el audio.
- Silenciarse, ensordecerse o emitir pantalla dentro del mismo canal no dispara el sonido.
- El bot no interrumpe ni se cruza con la música.

## 6 ter. Economía y casino

### 6 ter.1. Economía: yapdollars

- Una única moneda para todo el bot, los **yapdollars** (`Y$`). Es ficticia: no se compra con dinero real ni se canjea por nada con valor económico. Mantenerlo así es una condición de diseño, porque una moneda comprable o canjeable convertiría el casino en juego con premio en el sentido legal (art. 3.a de la Ley 13/2011, de regulación del juego).
- Saldo por servidor y usuario. El primer uso abre el monedero con 1.000 Y$.
- `daily`: 500 Y$ más 100 por cada día seguido, con tope de 1.500 Y$. Se puede cobrar cada 20 h; la racha se pierde si pasan más de 48 h.
- `saldo [miembro]`: muestra el saldo.
- Cualquier función que dé o quite dinero (juegos, tienda, premios futuros) debe hacerlo a través de `EconomyService`. Cada movimiento se guarda en un libro con su motivo y el saldo resultante, y se aplica en una transacción que impide saldos negativos y dobles gastos.

### 6 ter.2. Ruleta americana

- Rueda con 0 y 00. Pagos estándar: pleno 35:1, caballo 17:1, transversal y trío 11:1, cuadro 8:1, línea de cinco (0-00-1-2-3) 6:1, seisena 5:1, docena y columna 2:1, rojo/negro, par/impar y 1-18/19-36 1:1. La ventaja de la casa es del 5,26 % (7,89 % en la línea de cinco) y una prueba lo verifica para cada apuesta.
- Individual: `ruleta [cantidad] [apuesta]` abre una mesa propia con botones. En el modo rápido (por defecto), cada apuesta pulsada cobra, gira y paga en el acto. Cantidades: `500`, `2k`, `all`/`todo` (all-in), `mitad`.
- Varias apuestas en la misma tirada: con 🧩 **Varias**, cada botón pone una ficha en la mesa sin cobrar nada y 🎰 **Girar** las juega todas con un solo número. Hasta 10 apuestas distintas; las fichas en la misma apuesta se apilan. No se pueden poner más fichas que saldo. El total se cobra y los premios se pagan en una sola transacción. Por texto: `ruleta 100 rojo + 17 + d2` (ficha por apuesta; `all` reparte el saldo a partes iguales).
- El resultado sale de `secrets` (no predecible) y se decide antes de cobrar, pero solo se muestra si el cobro sale bien.
- Animación: GIF de ~2 s donde la bola gira y cae en su casilla, seguido del resultado. Los 38 GIF se precalculan al arrancar y se reutilizan.
- Si `CASINO_CHANNEL_IDS` está configurado, la ruleta solo se abre en esos canales.

### 6 ter.3. Criterios de aceptación

- Ningún saldo puede quedar negativo ni gastarse dos veces, aunque se pulsen botones a la vez.
- Solo el dueño de una mesa puede apostar en ella.
- Una apuesta ilegal en el tapete se rechaza con un ejemplo de formato válido.
- Al salir el bot de un servidor se borra su economía.
## 6 quater. Administración

### 6 quater.1. Comandos

Todos funcionan con `/` y con `.`, con el mismo nombre:

| Comando | Qué hace |
|---|---|
| `purge <cantidad> [miembro]` | Borra hasta 100 mensajes del canal (solo los de ese miembro, si se indica). Ignora los de más de 14 días. |
| `mute <miembro> <duración> [motivo]` | Aislamiento temporal de Discord. Duración: `10m`, `2h`, `1d`, `1h30m`; sin unidad, minutos; máximo 28 días. |
| `unmute <miembro>` | Quita el aislamiento. |
| `kick <miembro> [motivo]` | Expulsa del servidor. |
| `ban <miembro> [motivo]` | Banea sin borrar mensajes anteriores. |
| `unban <id>` | Levanta un baneo (ID numérico o mención). |
| `lock` / `unlock` | Quita o devuelve a `@everyone` el permiso de escribir y crear hilos en el canal actual. |
| `slow <segundos>` | Modo lento del canal (0-21600; 0 lo quita). |
| `say <texto>` | El bot escribe el texto. En `.say` se borra tu mensaje; `/say` admite otro canal. Nunca menciona a `@everyone`, `@here` ni roles. |
| `nick <miembro> [apodo]` | Cambia el apodo; sin apodo, lo quita. |
| `role <miembro> <rol>` | Da el rol si no lo tiene; si lo tiene, se lo quita. |

### 6 quater.2. Autorización y seguridad

- Solo los miembros con el permiso **Administrador** pueden usarlos. Se comprueba en el servidor en cada invocación (`cog_check` y `interaction_check`); que Discord oculte los `/` a los demás (`default_permissions`) es solo estética.
- Se replica la jerarquía de Discord antes de llamar a la API: nadie actúa sobre sí mismo (salvo `nick`), sobre el bot ni sobre el dueño, ni sobre alguien con un rol igual o superior al suyo o al del bot. El dueño del servidor está por encima de esa regla.
- Cada acción queda en el registro de auditoría con el motivo y quién la pidió.
- El bot necesita, según el comando: Gestionar mensajes, Aislar temporalmente a miembros, Expulsar, Banear, Gestionar canales, Gestionar apodos y Gestionar roles. Si le falta alguno, responde que no tiene permiso en vez de fallar en silencio.

## 6 quinquies. Diversión: `babel`

### 6 quinquies.1. Comportamiento

- `/babel texto` (o `.babel texto`) traduce el texto en cadena por 99 idiomas distintos elegidos al azar en cada tirada y termina en español: 100 traducciones. Cada salto parte del idioma anterior, no del español, para que el sentido se degrade.
- `.babel` sin texto, respondiendo a un mensaje, usa el texto de ese mensaje. En `.babel`, las menciones se traducen como el nombre visible.
- Mientras trabaja, edita el aviso cada 10 vueltas con el idioma por el que va. Al acabar muestra un embed con el texto original, el resultado y la ruta de códigos de idioma.
- **Modo nombres:** si el texto son solo menciones de miembros y canales (`.babel @Ana #general`, máximo 10), se cambia el apodo de esos miembros y el nombre de esos canales por su versión tras las 100 traducciones. Los nombres se traducen juntos, uno por línea, en una sola tirada; una vuelta que junta o parte líneas se descarta. El adorno inicial (emojis, separadores) no se traduce y se conserva. En los canales, los guiones se traducen como espacios.
- Permisos del modo nombres, iguales a los de Discord: el propio apodo requiere **Cambiar apodo**; el de otro, **Gestionar apodos** y un rol superior (el dueño del servidor está exento de la jerarquía, pero su apodo no se puede cambiar); un canal, **Gestionar canales** en ese canal. El bot necesita esos mismos permisos y un rol superior al de los miembros que renombra. Los objetivos no permitidos se listan con su motivo y el resto se renombra.
- Discord solo permite renombrar un canal 2 veces cada 10 minutos: el bot lleva la cuenta en memoria y lo avisa antes de empezar. No hay comando para deshacer. Si la tirada no consigue volver al español, no se renombra nada.
- Las traducciones se piden en el momento a Google Translate (endpoint público `translate_a/single`, `client=gtx`), sin clave. No hay frases predefinidas.

### 6 quinquies.2. Límites y fallos

- Máximo 300 caracteres de texto y una sola tirada a la vez en todo el bot; si hay otra en curso, se avisa y no se encola.
- Un idioma que falla se salta. Si Google limita la tasa (HTTP 429 o redirección a su captcha) o fallan 5 idiomas seguidos, la cadena se corta, se intenta una última traducción al español y el embed avisa de que dio menos vueltas.
- Las respuestas se envían con las menciones desactivadas.

## 7. Persistencia y aislamiento por servidor

La bienvenida/despedida usa actualmente el canal `#chat-general` y textos definidos por el bot, por lo que no necesita configuración persistida. Si se añade personalización, sus preferencias deberán persistir por servidor. Los datos del sistema de niveles ya se guardan en SQLite.

Toda configuración de servidor debe estar asociada al ID de ese servidor. El bot no debe usar valores de un servidor como valores implícitos para otro. Los datos de progresión se limitarán a lo necesario para operar las funciones descritas y deben poder eliminarse si el bot deja de prestar servicio en un servidor.

## 8. Orden de implementación

1. **Bienvenida y despedida:** eventos y mensajes básicos en `#chat-general` (implementado); configuración por servidor y mensajes personalizables pendientes.
2. **Preparación de niveles:** importar y guardar agregados de mensajes históricos y contar actividad nueva. (Implementado.)
3. **Niveles:** conversión de historial en XP, XP por mensajes nuevos, cooldown, comandos `level`/`top` y configuración. (Implementado.)
4. **Música:** reproducción y controles de cola con `yt-dlp` y `ffmpeg`. (Implementado.)
5. **Imagen:** comando `magik` con seam carving y los 108 efectos de Dank Memer. (Implementado.)
6. **Sonidos de entrada:** clip personal de hasta 3 s al entrar a voz. (Implementado.)
7. **Economía y casino:** yapdollars, `daily`, `saldo` y ruleta americana. (Implementado.) Siguientes juegos y usos de la moneda pendientes.
8. **Diversión:** `babel`, traducción en cadena por 99 idiomas de frases, apodos y nombres de canal. (Implementado.)

Cada fase debe incluir pruebas, permisos mínimos, documentación de uso y los cambios pertinentes a la configuración. Una función se considera terminada únicamente cuando cumple sus criterios de aceptación; aparecer en esta lista no significa que ya esté implementada.

## 9. Decisiones pendientes

Estas decisiones no impiden documentar el alcance, pero deben resolverse antes de cerrar la implementación correspondiente:

- Configuración por servidor y plantillas personalizables para bienvenida/despedida.
- Si se ofrecerá una herramienta administrativa para reiniciar la progresión (los comandos de administración actuales no tocan los niveles).
- Si moderadores podrán controlar música iniciada por otros miembros, o si el control seguirá limitado a "cualquiera en el mismo canal de voz" (comportamiento actual).

Las decisiones pendientes no bloquean las funciones ya implementadas, pero deben resolverse antes de cerrar la funcionalidad correspondiente.
