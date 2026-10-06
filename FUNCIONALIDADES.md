# Especificación funcional del bot

Este documento describe las funcionalidades actuales y futuras y cómo debe comportarse el bot desde el punto de vista de sus usuarios y administradores. Complementa la [Biblia del proyecto](./Biblia.txt), que define las normas técnicas y de calidad.

La bienvenida/despedida descrita en la sección 3, los comandos `latencia`, `ayuda`, `nivel`, `ranking`, `magik`, `memes` y los 108 efectos de imagen, los comandos de música, los sonidos de entrada, la economía con la ruleta, el blackjack, la tragaperras, los Botes, el Crash, Minas y el pachinko (`ruleta`, `blackjack`/`.bj`, `tragas`, `volcan`, `cohete`, `minas`, `pachinko`, `saldo`, `imv`, `hacienda`, `renta`), los cumpleaños (`cumple`, `cumples`), la lista de tareas (`lista`), los comandos de administración y `babel` están implementados. Las demás funciones son objetivos futuros salvo que se indique lo contrario.

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

- Cuando una persona se une, el bot publica en el canal de bienvenida (el configurado con `bienv`; si no hay o se ha borrado, `#chat-general`) una frase al azar de `src/bot/assets/bienvenidas.txt` que menciona al recién llegado.
- Con un GIF configurado, va debajo de la frase: los enlaces directos (`.gif`, `media.tenor.com`, `i.giphy.com`) dentro de un embed; las páginas de Tenor o Giphy (lo que da "Copiar enlace" en el selector de GIF de Discord), como enlace suelto que Discord despliega. Sin GIF se adjunta `src/bot/assets/bienvenida.mp4`, como antes.
- Las frases van en el tono de Jovani Vázquez (alegre, cantarín, con guiños al café y al mango). `bienvenidas.txt` es texto editable, se relee en cada entrada y admite `{usuario}` (la mención). Las frases tras la línea `[vuelta]` son para quien ya había estado en el servidor y vuelve.
- El mensaje lleva un botón **👋 Dar la bienvenida**. Cada miembro puede pulsarlo una vez durante el primer día del recién llegado; el botón muestra cuántos le han saludado. Da un regalo simbólico, como felicitar un cumpleaños: 200 Y$ a quien saluda (dos tiradas de ruleta a la apuesta por defecto) y 100 Y$ al recién llegado por cada saludo. Paga IRPF con retención, igual que los regalos de cumpleaños (ganancia patrimonial, art. 33.1 LIRPF), aunque con estas cantidades solo retiene a quien ya pasa del mínimo; quien saluda ve la línea de Perro Sanxe y lleva el aviso de la Renta como cualquier acción con dinero. También cuenta para los logros de Social (*Comité de bienvenida*, *Relaciones públicas*, *Portero de discoteca* y el secreto *Más rápido que Hacienda*, por saludar en el primer minuto). El botón sigue funcionando tras reiniciar el bot.
- Las menciones se limitan al recién llegado. Si el canal, el vídeo o el GIF no están disponibles, o el bot no puede enviar el mensaje, registra el problema y no detiene el bot.

### 3.2. Despedida

Cuando una persona abandone el servidor, el bot publica en el canal de bienvenida una despedida con su nombre visible y una frase humorística elegida aleatoriamente de `src/bot/assets/despedidas.txt`. Ese archivo es texto plano editable (una frase por línea; las líneas vacías o que empiezan por `#` se ignoran) y se relee en cada despedida, sin reiniciar el bot. Las menciones están desactivadas para evitar notificaciones accidentales.

### 3.3. Configuración

Comando de administración `bienv` (ver sección 6 quater):

- `bienv` sin argumentos enseña el canal y el GIF actuales y una vista previa.
- `bienv gif:<enlace>` (o `.bienv <enlace>`) cambia el GIF; `quitar` vuelve al vídeo. Se rechazan los enlaces que no son `https`, los que no parecen un GIF y los adjuntos de Discord, porque caducan a las 24 horas.
- `bienv canal:#canal` (o `.bienv #canal`) cambia el canal de bienvenida y despedida.

Los ajustes se guardan por servidor en SQLite (`welcome_settings`), junto a la última entrada de cada miembro (`welcome_joins`, para saber si vuelve y para el plazo del botón) y quién ha saludado a quién (`welcome_greetings`). Solo se guardan IDs y marcas de tiempo, y todo se borra si el bot sale del servidor.

### 3.4. Criterios de aceptación

- Un ingreso produce como máximo una bienvenida y una salida produce como máximo una despedida.
- Cada mensaje se envía únicamente al canal de bienvenida del servidor donde ocurrió el evento.
- Los eventos de miembros requieren habilitar el intent privilegiado `Server Members Intent` en el portal de Discord y en el código.
- La función tolera canales eliminados y permisos revocados sin detener el bot.

## 4. Sistema de niveles por mensajes

### 4.0. Preparación: importación del historial

Los niveles vienen apagados en cada servidor. Un administrador los pone en marcha con el comando `niveles` (sección 6 quater): `/niveles accion:importar` o `.niveles importar` recorre el historial y, la primera vez que termina, enciende los niveles solo y lo anuncia en el canal donde se pidió. Mientras están apagados nadie gana XP y `nivel`/`ranking` responden que están apagados.

- La importación se inicia a mano y corre en segundo plano; no se ejecuta sola al arrancar el bot. Si el bot se reinicia a medias, `importar` la reanuda desde los canales que faltaban.
- El bot recorre canales de texto, hilos activos y hilos archivados que pueda enumerar y leer. En hilos privados depende de que el bot esté unido al hilo o tenga permiso para administrar hilos. El resultado no puede incluir canales eliminados ni canales con permisos insuficientes.
- Se requieren `Ver canal` y `Leer el historial de mensajes` en cada canal o hilo. Leer mensajes de un hilo privado puede requerir, además, que el bot sea miembro del hilo o tenga permisos de administración de hilos.
- Se cuentan mensajes escritos por usuarios; se excluyen bots, webhooks y mensajes de sistema. Se cuentan mensajes aunque solo contengan un adjunto, porque el cálculo no debe depender del intent privilegiado `MESSAGE_CONTENT`.
- Se guarda únicamente un total agregado por ID de servidor y usuario. No se persisten contenidos, adjuntos ni IDs individuales de mensajes.
- La base SQLite local vive en `.data/message_stats.sqlite3`, está excluida de Git y persiste entre reinicios en el mismo entorno. Debe incluirse en las copias de seguridad del despliegue si se quiere conservar el progreso.
- El proceso guarda cada canal de forma atómica, evita sumar dos veces canales ya completados y puede reanudarse después de un reinicio. Los canales inaccesibles se reportan como fallidos y la importación queda como parcial. Una importación parcial también enciende los niveles (si no, bastaría un canal sin permiso para dejarlos apagados para siempre); el estado dice cuántos canales faltan. Si luego se da el permiso y se repite `importar`, esos canales suman su XP al momento.
- `niveles` sin acción muestra el estado: canales revisados y mensajes de usuarios contados. El contador se persiste cada 100 mensajes y al cerrar cada canal; por ello el valor puede retrasarse hasta el siguiente lote. Los mensajes de bots/sistema cuentan como revisados, pero no como mensajes de usuario elegibles.
- Se fija un instante de corte al iniciar: los mensajes anteriores se agregan como históricos y los mensajes posteriores se cuentan en vivo, para evitar duplicarlos mientras avanza el recorrido. Los mensajes escritos entre el corte y el final de la importación no cuentan: el historial ya no los ve y los niveles aún no están encendidos.

Al encender los niveles por primera vez, el recuento importado se convierte una sola vez en **20 XP por mensaje**. Esa XP no paga premios por nivel: los yapdollars solo llegan con las subidas que vengan después; los mensajes posteriores reciben XP en vivo solo cuando el sistema está activo. El recuento actual no es un libro de auditoría: no puede corregir cambios retroactivos del historial una vez importado.

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

- `/nivel [miembro]` y `.nivel [miembro]`: muestran el nivel, experiencia actual y progreso al siguiente nivel del miembro indicado o de quien ejecuta el comando.
- `/ranking [pagina]` y `.ranking [pagina]`: muestran los miembros con más experiencia del servidor, ordenados de forma estable y con paginación.
- `/latencia` y `.latencia`: comprueban la latencia del bot.
- `/ayuda` y `.ayuda`: listan todos los comandos en un solo embed, por categorías (General, Música, Niveles, Casino, Entradas, Imagen y sus cuatro tipos de efecto), solo por nombre, sin descripción ni argumentos y en orden alfabético dentro de cada categoría. La categoría Admin solo aparece si quien pide la ayuda es administrador.

**Convención de nombres de comandos (norma del proyecto, ver Biblia):** cada comando tiene un único nombre corto de una palabra (máximo 8 caracteres), idéntico en `/` y en `.`. No hay alias, grupos ni subcomandos, para que el menú de `/` quede limpio. El prefijo de texto es `.` por defecto y se puede cambiar con `COMMAND_PREFIX`; los comandos `/` funcionan siempre, sea cual sea el prefijo. Para que Discord entregue mensajes a los comandos de texto, también debe habilitarse **Message Content Intent** en el portal de desarrolladores.

La puesta en marcha y los ajustes (importar, encender, apagar, canal de avisos y enfriamiento) están en el comando de administración `niveles`, que solo ven y usan los administradores.

### 4.3. Avisos y persistencia

- Al subir de nivel, el bot envía un aviso en el canal de avisos elegido con `niveles`; si no hay ninguno o se ha borrado, en el canal donde se ganó la XP (el chat del canal de voz si fue por voz). Solo se muestra el nombre visible, sin activar menciones.
- Subir al nivel N paga 100 × N Y$ brutos (el doble en múltiplos de 5), con retención de IRPF.
- Fuentes de XP además de los mensajes: +50 XP por el primer mensaje del día, 4–6 XP por minuto en voz (sin mute, fuera del AFK y con alguien más sin mutear), +5 XP por reacción recibida de otro (tope 100 al día), racha de +2 % por día seguido escribiendo (tope +20 %) y una hora feliz diaria con XP ×2.
- El estado, el canal de avisos, el enfriamiento y la activación solo se tocan con el comando de administración `niveles`.
- La experiencia, el nivel y los ajustes por servidor deben conservarse después de reiniciar el bot.
- La XP inicial se calcula una sola vez al activar el sistema, usando 20 XP por mensaje contado hasta ese momento; desactivar y reactivar no vuelve a conceder esa XP.
- El ranking no debe mostrar datos de otros servidores.
- No guardar contenido de mensajes para calcular experiencia; almacenar solo los identificadores y los datos de progresión que sean necesarios.
- Los cambios de nombre no deben crear perfiles duplicados: la identidad se basa en los ID de Discord.

### 4.4. Criterios de aceptación

- Un mensaje elegible otorga experiencia como máximo una vez durante el periodo de enfriamiento.
- Mensajes de bots, webhooks y mensajes de sistema no generan experiencia.
- La XP histórica no se inicializa antes de que termine una importación (completa o parcial) ni se duplica al reactivar.
- El nivel y el progreso mostrado coinciden con los valores persistidos.
- Reiniciar el bot no borra niveles ni configuración.
- El ranking y las consultas siempre están limitados al servidor de la interacción.

## 5. Música

### 5.1. Comandos implementados

Cada acción tiene un único nombre, igual en `/` y en `.` (por ejemplo `/poner` y `.poner`). Ambas interfaces reutilizan la misma lógica interna; el adaptador compartido `CommandResponder` (`InteractionResponder`/`ContextResponder` en `bot.utils.responder`) oculta si la petición vino de una `discord.Interaction` o de un mensaje de texto.

- `poner consulta`: busca en YouTube (o resuelve un enlace directo) mediante `yt-dlp` y reproduce el resultado; conecta al bot al canal de voz del miembro si aún no estaba conectado. Si ya hay una pista sonando, la añade al final de la cola.
- `pausar` y `seguir`: controlan la reproducción actual.
- `saltar`: detiene la pista en curso; la cola continúa automáticamente con la siguiente.
- `cola`: muestra la pista actual y hasta diez pistas siguientes (con el resto resumido en un contador).
- `quitar posicion`: elimina una pista de la cola por su posición (1 = la siguiente).
- `vaciar`: vacía la cola sin afectar a la pista en curso.
- `parar`: detiene la reproducción, vacía la cola y desconecta al bot del canal de voz.
- `volumen valor`: cambia el volumen (1-200 %), incluso con una pista ya sonando.

La cola y el reproductor son independientes por servidor (`GuildMusicState` en `bot.cogs.music`). Las acciones de control (todas salvo `poner`, que además puede conectar al bot, y `cola`, de solo lectura) exigen que quien las use esté conectado al mismo canal de voz que el bot.

Los comandos de texto con prefijo requieren el intent privilegiado **Message Content** habilitado en el portal de desarrolladores de Discord (además del ya requerido **Server Members**); sin él, el bot no puede leer el contenido de los mensajes y esos comandos no se dispararán (los comandos de aplicación `/` no se ven afectados).

### 5.2. Comportamiento y límites

- Se informa con un mensaje claro si la consulta no se puede resolver, la pista dura demasiado, la cola está llena o el miembro no está en el canal de voz adecuado.
- Al terminar una pista (o al fallar su reproducción), se continúa automáticamente con la siguiente de la cola; `parar` es la única acción que corta ese encadenamiento.
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

Port de [imgen](https://github.com/DankMemer/imgen), el generador de imágenes de Dank Memer (licencia MIT). Cada uno de sus 108 efectos es un comando de texto con el nombre original (`.trigger`, `.slap`, `.changemymind`...). Esos nombres conservan la longitud de Dank Memer, aunque algunos superan las 8 letras, porque así los reconoce quien ya los usaba; `.ayuda` los lista en subcategorías de Imagen según su tipo; `.memes` muestra la misma lista y `.memes <efecto>` explica el uso de uno.

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
- `imv` (Ingreso Mínimo Vital, antes `daily`): exento de IRPF como el real (art. 7.y LIRPF). Paga 500 Y$ más 100 por cada día seguido, con tope de 1.500 Y$. Se puede cobrar cada 20 h; la racha se pierde si pasan más de 48 h. Es compatible con trabajar (`pala`, sección 6 ter.7 quater), pero se reduce: no cuentan los primeros 11.506 Y$ netos de nómina de los últimos 7 días (6.000 € al año pasados a una semana, a la escala de las nóminas) y cada 20 Y$ de nómina de más quitan 1 Y$ de IMV (la mitad, pasada a la escala general), repartido entre los 7 días; nunca baja del 20 % de lo que toca por la racha. Se inspira en el incentivo al empleo de los arts. 3 y 4 del RD 789/2022, en la redacción del RD 240/2026 (100 % exento hasta 6.000 € de aumento anual y 50 % de lo que pase); la ley compara con el año anterior y el bot, con la última semana. Si la Inspección de Trabajo pilla un turno en negro, el IMV queda suspendido 3 días.
- IRPF: los premios por nivel y la ganancia neta diaria del casino llevan retención. En el casino se retiene sobre premios menos apuestas del día (hora canaria) y la retención se recalcula en cada tirada o mano: si las pérdidas posteriores bajan la ganancia, la cuenta del Estado devuelve la diferencia. Es la regla del art. 33.5.d LIRPF (las pérdidas de juego solo compensan ganancias de juego) aplicada por día en vez de por año. Se proyecta la renta anual con los ingresos de los últimos 30 días (10 Y$ = 1 €) y se aplican la escala estatal (art. 63.1 LIRPF) y la de Canarias (Ley 9/2025), descontando los mínimos personales (art. 57 LIRPF y art. 18 quater del D. Leg. 1/2009 de Canarias). El tipo es cuota / base con dos decimales (art. 86 RIRPF). Cada cobro muestra lo que se lleva Perro Sanxe. Cada ingreso queda en `economy_tax_records` para poder hacer más adelante la declaración anual.
- Cuenta del Estado: todo lo retenido entra en un monedero propio del servidor (`user_id = 0`), que empieza a 0 y tiene su propio libro de movimientos. Qué se hace con ese dinero está por decidir.
- Campaña de la Renta (`renta`): al cerrar cada semana (lunes 00:00, hora canaria) se recalcula la retención del casino sobre el neto de la semana, con las pérdidas de un día compensando las ganancias de otro, y lo retenido de más sale a devolver. Nunca sale a pagar. Para cobrarlo hay que presentar la declaración: con `renta`, o con el mensaje efímero que aparece una vez por campaña al jugar con botones o slash en el casino (después solo queda una línea pequeña en el resultado). Al presentar, el bot publica una línea en el canal. Se guardan como máximo las 2 últimas semanas pendientes, sin fecha de caducidad; cuando aparece una tercera, la más antigua caduca y el dinero se queda en el Estado. El bot mantiene un evento de Discord por campaña, que va de la apertura al domingo siguiente; sin el permiso **Gestionar eventos** todo funciona igual, sin evento.
- Impuesto sobre el Patrimonio: cada lunes (cuando la tarea de 10 minutos ve la semana cerrada) se cobra a todos los monederos lo que pase de **70.000 Y$**, con los tipos reales de la escala estatal (art. 30 de la Ley 19/1991, del 0,2 % al 3,5 %). Canarias no tiene tarifa propia ni bonificación, y su mínimo exento es de 700.000 € (art. 29 del D. Leg. 1/2009); en el bot ese mínimo son 70.000 Y$ y los tramos se escalan igual (1 Y$ = 10 € solo en este impuesto). El ejercicio dura una semana, como la renta: cada lunes se paga la cuota anual entera sobre el saldo de ese momento. Ejemplos: 100.000 Y$ pagan 73; 200.000 pagan 819; 1.000.000 pagan 15.435. La primera semana tras desplegarlo no se cobra, para que nadie pague por sorpresa. El bot anuncia en `#chat-general` (o el canal del sistema) quién ha pagado, `saldo` avisa de lo que te tocaría y lo cobrado va al Estado y cuenta en `hacienda`. Las cuentas del Estado y de las ONGs no pagan. No se aplica el límite conjunto con el IRPF (art. 31).
- Impuesto al consumo: el **IGIC**, no el IVA, porque el servidor es canario. Lo pagan las compras de la tienda (sección 6 ter.7 bis) al tipo de cada artículo, con los tipos de la Ley 4/2012 de Canarias: cero (art. 52), reducido del 3 % (art. 54), general del 7 % (art. 51, el de por defecto), incrementado del 9,5 % (art. 59.3) y de lujo del 15 % (arts. 56.1 y 59.4). Antes este documento citaba el art. 27 de la Ley 4/2012: era un error, el art. 27 es el de la Ley 20/1991, de donde la Ley 4/2012 sacó los tipos.
- `donar [ong] [cantidad]`: donativos a cuatro ONGs de broma que dicen servir a una causa y hacen lo contrario (🧨 Fundación Desmontando España, 🤝 Asociación Ayuda al Ayudante, 🛥️ Mares Limpios Sociedad Limitada y 🍽️ Fundación Cubiertos de Plata contra el Hambre). Sin argumentos enseña lo que dicen, lo que hacen y cuánto llevan recaudado. El dinero se queda en la cuenta de la ONG (`user_id` negativo) y no vuelve. A cambio desgrava como el art. 19.1 de la Ley 49/2002: 80 % de los primeros 2.500 Y$ donados en la semana (250 €) y 40 % del resto, con la base limitada al 10 % de la renta de la semana (art. 69.1 LIRPF) y sin pasar del IRPF pagado esa semana. La deducción sale a devolver en la renta del lunes junto con lo del casino; si esa semana no pagaste IRPF, no recuperas nada. Los donativos no pagan IGIC (art. 4 de la Ley 20/1991) ni Donaciones (quien recibe es una entidad). El resultado es público y en `/donar` lleva el aviso de la Renta.
- `bizum <miembro> <cantidad> [concepto]`: pasa yapdollars de un miembro a otro al momento, en una sola transacción. Exento del Impuesto sobre Sucesiones y Donaciones por decisión del proyecto y sin IRPF para quien recibe (art. 6.4 LIRPF). En la vida real sería una donación sujeta (art. 3.1.b de la Ley 29/1987) y Canarias solo la bonifica al 99,9 % entre familia de los grupos I y II (art. 26 sexies del Decreto Legislativo 1/2009); el bot trata a todo el servidor como grupo II y redondea el 0,1 % a cero. Mínimo 5 Y$ (0,50 €, el mínimo de Bizum). Los máximos de Bizum, 10.000 Y$ por operación (1.000 €) y 20.000 Y$ al día (2.000 €), no se aplican: el resultado dice que te has pasado y los logros lo premian. No se puede mandar a uno mismo ni a un bot. El resultado es público, menciona a quien recibe (es la única mención permitida) y enseña el concepto recortado a 60 caracteres; el concepto no se guarda. En `/bizum` lleva el aviso de la Renta. `economy_bizums` guarda quién, a quién, cuánto y cuándo, para sumar lo enviado en el día.
- `hacienda`: muestra a cualquiera el saldo del Estado, lo recaudado este año y desde siempre (IRPF, Patrimonio, IGIC, gravamen de loterías, Seguridad Social de las nóminas y multas), los 5 que más han pagado y qué impuestos hay. La Seguridad Social de la empresa de cada nómina se apunta a quien la cobra.
- `saldo [miembro]`: muestra el saldo.
- Cualquier función que dé o quite dinero (juegos, tienda, premios futuros) debe hacerlo a través de `EconomyService`. Cada movimiento se guarda en un libro con su motivo y el saldo resultante, y se aplica en una transacción que impide saldos negativos y dobles gastos.

### 6 ter.1 bis. Cumpleaños

- `cumple [dd/mm] [miembro]`: sin argumentos muestra el tuyo; con fecha lo guarda; con miembro, consulta el suyo. Solo se guarda día y mes. Una vez puesto, solo un administrador puede cambiarlo (o ponérselo a otro), para que nadie cobre el regalo varias veces.
- `cumples`: próximos 10 cumpleaños del servidor.
- El día del cumpleaños (hora canaria; el 29/02 se celebra el 28/02 en años no bisiestos) el bot lo anuncia en `#chat-general` (o en el canal del sistema) con un botón 🎉 Felicitar y le da 3.000 Y$ al cumpleañero. Se comprueba cada 5 minutos y se guarda qué cumpleaños ya se celebraron, así que un reinicio no paga dos veces.
- Felicitar (botón o mensaje que mencione o responda al cumpleañero y suene a felicitación) da 500 Y$ a quien felicita y 100 Y$ al cumpleañero, una vez por persona y año. El bot reacciona 🎉 a los mensajes que cuentan.
- Si el servidor tiene un rol llamado `🎂 Cumpleañero`, el bot se lo pone durante el día y lo quita al siguiente. No crea el rol.
- Los regalos pagan IRPF con retención: el dinero lo pone el bot, no otro miembro, así que no es una donación entre particulares sujeta a Sucesiones y Donaciones (art. 3.1.b de la Ley 29/1987) sino una ganancia patrimonial (art. 33.1 LIRPF). Con estas cantidades casi nunca se retiene nada, salvo a quien ya pasa del mínimo personal. El Impuesto de Donaciones queda para cuando haya transferencias entre miembros.

### 6 ter.2. Ruleta americana

- Rueda con 0 y 00. Pagos estándar: pleno 35:1, caballo 17:1, transversal y trío 11:1, cuadro 8:1, línea de cinco (0-00-1-2-3) 6:1, seisena 5:1, docena y columna 2:1, rojo/negro, par/impar y 1-18/19-36 1:1. La ventaja de la casa es del 5,26 % (7,89 % en la línea de cinco) y una prueba lo verifica para cada apuesta.
- Individual: `ruleta [cantidad] [apuesta]` abre una mesa propia con botones. En el modo rápido (por defecto), cada apuesta pulsada cobra, gira y paga en el acto. Cantidades: `500`, `2k`, `all`/`todo` (all-in), `mitad`.
- Varias apuestas en la misma tirada: con 🧩 **Varias**, cada botón pone una ficha en la mesa sin cobrar nada y 🎰 **Girar** las juega todas con un solo número. Hasta 10 apuestas distintas; las fichas en la misma apuesta se apilan. No se pueden poner más fichas que saldo. El total se cobra y los premios se pagan en una sola transacción. Por texto: `ruleta 100 rojo + 17 + d2` (ficha por apuesta; `all` reparte el saldo a partes iguales).
- El resultado sale de `secrets` (no predecible) y se decide antes de cobrar, pero solo se muestra si el cobro sale bien.
- Animación: GIF de ~2 s donde la bola gira y cae en su casilla, seguido del resultado. Los 38 GIF se precalculan al arrancar y se reutilizan.
- Si `CASINO_CHANNEL_IDS` está configurado, la ruleta solo se abre en esos canales.

### 6 ter.3. Blackjack

- `blackjack [cantidad]` (atajo `.bj`) reparte al momento en una mesa propia (imagen con las cartas y botones) que solo pulsa su dueño. Acciones: pedir, plantarse, doblar y separar; al acabar, repartir otra mano en el mismo mensaje y cambiar la apuesta con ½, ×2 y all-in.
- Reglas: zapato de 6 barajas, barajado con `secrets` en cada mano (no se pueden contar cartas). La banca mira si tiene blackjack con un as o un 10 a la vista y se planta en 17, también blando. Blackjack paga 3:2 (redondeando hacia abajo). Doblar con cualquier par de cartas, también tras separar. Separar una vez dos cartas del mismo valor; los ases separados reciben una carta y su 21 no es blackjack. Sin seguro ni rendición. Ventaja de la casa ≈ 0,5 % con estrategia básica.
- Dinero: la apuesta se cobra al repartir y el extra al doblar o separar (`place_bet`); el premio se paga al acabar la mano (`pay_winnings`). Si la mesa caduca (3 min sin uso) o el bot se apaga de forma ordenada con una mano a medias, la mano se planta, juega la banca y se paga. Si el proceso muere de golpe, lo apostado en esa mano se pierde y queda en el libro de movimientos.

### 6 ter.4. Tragaperras

- `tragas [cantidad]` abre una máquina propia con botones que solo pulsa su dueño: 🎰 Tirar, 🔁 Auto ×10, ⚡ Turbo, ½, ×2, 💰 All-in y 📋 Premios (en privado). Apuesta por defecto, 100 Y$.
- 3 rodillos y 3 filas a la vista; solo paga la del medio. Cada rodillo es una tira fija de símbolos y la parada de cada uno sale del azar del sistema operativo (`random.SystemRandom`, como `secrets`). Las tiras son distintas: el primer rodillo lleva más 🃏 y 7️⃣ que el tercero, así que quedarse a uno del premio gordo pasa a menudo y el premio, poco.
- Premios (veces la apuesta, con la apuesta incluida): 🍒 en el primer rodillo ×0,5; 🍒 🍒 ×2; 🍒🍒🍒 y 🍋🍋🍋 ×4; 🍇 ×10; 🔔 ×20; 💎 ×60; 7️⃣ ×200. 🃏 es comodín (menos para 🎟️) y 🃏 🃏 🃏 se lleva el bote.
- Bote común por servidor: el 3 % de cada apuesta pagada va al bote (monedero `SLOTS_POT_ACCOUNT_ID`). Quien saca 🃏 🃏 🃏 se lo lleva entero y la casa pone 5.000 Y$ para empezar otro. La máquina enseña el bote y el último ganador.
- Giros gratis: 3 🎟️ en cualquier fila dan 5 giros con la apuesta que los activó. Durante los giros gratis los 🎟️ no cuentan. Si la máquina se cierra o el bot se apaga con giros pendientes, se juegan solos y se pagan.
- Máquina caliente: cada tirada con premio (también el medio premio de la 🍒) llena una barra de 5; llena, la siguiente tirada paga la línea ×2. La barra se guarda por miembro en memoria.
- Retorno: la línea devuelve ~83 %; con giros gratis y máquina caliente, ~91 %; con el bote, ~94 % (una prueba lo calcula). El 31 % de las tiradas paga algo, pero dos de cada tres de esos premios son menores que la apuesta, y la máquina los celebra igual. Jackpot: 1 de cada 14.400 tiradas; giros gratis: 1 de cada 133.
- Dinero: cada tirada cobra, paga y mueve el bote en una sola transacción (`EconomyService.play_slots`). Fiscalmente es juego, como la ruleta: ganancia patrimonial en la base general (art. 33.1 LIRPF) con las pérdidas del día compensando (art. 33.5.d LIRPF). El jackpot también: el gravamen especial del 20 % (disposición adicional 33ª LIRPF) solo es para loterías del Estado, ONCE y Cruz Roja.
- Animación: un GIF de 2-3 s por tirada (~150 KB, ~0,1 s de CPU), montado con piezas precalculadas. Los rodillos paran de izquierda a derecha con rebote; si los dos primeros prometen algo gordo, el tercero gira más y su ventana se ilumina; si la línea paga, parpadea. Al acabar se cambia por el PNG final. ⚡ Turbo y Auto solo mandan el PNG (~5 KB).
- Los botes y los premios de ×50 o más se anuncian en el canal.
- Logros: 50 en la categoría 🎰 Tragaperras (tríos de cada símbolo, botes, medio premio, por un pelo, giros gratis, máquina caliente, turbo, Auto, sesiones largas, aportes al bote, de madrugada…) y *Trilero* en 💰 Casino por jugar a los tres juegos.

### 6 ter.4 bis. Botes: el Volcán

- `volcan [cantidad]` abre una máquina propia de 5 rodillos y 4 filas con botones que solo pulsa su dueño: 🎰 Tirar, 🔁 Auto ×10, ⚡ Turbo, ½, ×2, 💰 All-in y 📋 Premios (en privado). Apuesta por defecto, 100 Y$; mínima, 10 Y$. Estilo de tragaperras de casino ilustrada: cerezas, campana, BAR, siete, máscara tiki dorada, máscara de fuego, diamante WILD y el volcán de recogedor, sobre un fondo magenta con un volcán morado echando lava. Las reglas y los números (`bot.services.hold_win`) sirven para más máquinas con otros dibujos (`THEMES`); quedan pendientes un Olimpo y una mina.
- Juego base, dos capas que pagan por separado. **Ways**: seis símbolos pagan si salen en los rodillos 1, 2 y 3 seguidos (o más), en cualquier fila, por cada combinación posible; el comodín sale en los rodillos 2 a 4. **Monedas y recogida**: las monedas llevan su premio escrito (verde ×0,1-×0,5, azul ×0,6-×1,5 y roja ×2-×5 la apuesta) y se distinguen por la forma (círculo, hexágono y estrella); el recogedor en el rodillo 1 o el 5 cobra todas las de la pantalla, y en los dos, el doble.
- Maletines: cada moneda que cae entra en el maletín de su color, se cobre o no (verde 108, azul 50, roja 22). Se guardan por miembro y máquina en la base de datos (`hold_win_meters`), así que un maletín a medias sigue ahí otro día. Lleno, dispara su bonus a la apuesta media de las monedas que lo llenaron (llenarlo a 10 Y$ y dispararlo a 10.000 no sirve). Uno de cada seis bonus, o si se llenan dos maletines a la vez, es el gran bonus con monedas de los tres colores.
- Bonus: empieza con 3-8 monedas y 3 tiradas. En cada tirada puede caer algo en cada casilla vacía; si cae, el contador vuelve a 3. Las monedas se quedan; los modificadores se aplican y se van: 🎟️ suma +×1 a +×5 al multiplicador final, 🔁 sube el contador de reinicio en 1 (hasta 6), 💥 multiplica al momento las monedas de la mesa (×1,5 a ×5), 🏆 sube el MINI y el MAJOR a su máximo y los ❓ (rojo en círculo, azul en rombo) se revelan en una de esas cosas o en una moneda. 🎰 Girar juega una tirada y ⏩ Auto bonus, todas las que queden. Si la máquina se cierra o el bot se apaga de forma ordenada con un bonus a medias, se juega solo y se paga.
- Botes: MINI con 10 monedas (×1 a ×2,5), MAJOR con 15 (×5 a ×10) y GRAND con la pantalla llena (×1.000, fijo, distinto del bote común de `tragas`). Las fichas de bote del juego base suben el MINI y el MAJOR de cada jugador en cada máquina; al ganarlos vuelven a su base. El multiplicador del bonus no se aplica a los botes.
- Retorno: ~94 % (~42 % de los ways, ~22 % de la recogida y ~30 % de los bonus), con un bonus cada ~58 tiradas y un GRAND cada ~200.000. Lo calcula `simulate` y una prueba comprueba una versión corta.
- Dinero: cada tirada base es una apuesta del casino (`settle_bet`) y el bonus se paga al acabar (`pay_winnings`), sin cobrar apuesta: ya se cobró en las tiradas que llenaron el maletín. Fiscalmente es juego, como la tragaperras: ganancia patrimonial en la base general (art. 33.1 LIRPF) con las pérdidas del día compensando (art. 33.5.d LIRPF), también los botes.
- Animación: un GIF por tirada (300-450 KB, ~0,7 s de CPU fuera del event loop): los rodillos caen y paran de izquierda a derecha con rebote; con recogedor en el rodillo 1 y monedas a la vista, el quinto gira más y su ventana parpadea. Después se marcan los premios, los chorros de lava de la recogida y un cartel con lo ganado (grande ×10, mega ×25 y épico ×50). En el bonus, cada tirada es un GIF corto (~200 KB) con lo que cae dando un golpe y los misteriosos revelándose. ⚡ Turbo y Auto solo mandan el PNG final a color completo (~120 KB). En el GIF cada fotograma lleva su paleta y solo guarda los píxeles que cambian.
- Las tiradas de ×50 o más, los MAJOR, los GRAND y los bonus de ×50 o más se anuncian en el canal.
- Logros: 38 en la categoría 🌋 Botes (tiradas, recogidas, doble recogida, ways de cinco rodillos, bonus de cada color, MINI, MAJOR, GRAND, por una moneda, multiplicadores, misteriosos, turbo, Auto, de madrugada…) y *Los siete pecados* en 💰 Casino por jugar a los siete juegos.

### 6 ter.5. Crash

- `cohete [cantidad] [auto]` abre la mesa de Crash del canal o entra en la ronda que esté embarcando. La mesa es compartida: una ronda por canal y cualquiera se sube. `.cohete 500 2x` entra con 500 Y$ y auto-retiro en 2x; el orden de los dos argumentos da igual y `no` quita el auto-retiro. Si el cohete está en el aire, quien escribe `cohete` entra en la ronda siguiente.
- Embarque: 10 s la primera ronda y 7 s las siguientes, con cuenta atrás que Discord mueve solo (marca de tiempo relativa, sin editar el mensaje). Botones: 🚀 Entrar (con tu ficha), ½, ×2, 💰 All-in y 🎯 Auto (formulario). La ficha y el auto-retiro de cada miembro se recuerdan entre rondas, en memoria. Si un embarque termina sin nadie, la mesa se cierra.
- Vuelo: el multiplicador es e^(k·t^1,4): empieza lento y acelera, para que la tensión dure donde se decide casi todo (1,5x a los 5,5 s, 2x a los 8 s, 5x a los 15 s, 10x a los 19 s, 100x a los 31 s) y tiene tope en 1.000x. 💸 Retirar cobra apuesta × multiplicador del instante en que llega la pulsación. El auto-retiro cobra justo su objetivo aunque el bot edite a saltos. Si ya no queda nadie dentro, el cohete salta directo a su punto de explosión.
- Punto de explosión: se sortea al abrir el embarque con `secrets` y P(llegar a x) = 0,99 / x. Retirarse siempre en el mismo multiplicador devuelve el 99 % de media, sea cual sea; el 1 % de las rondas explota en 1,00x. Una prueba lo comprueba con 200.000 puntos.
- Mensaje: durante el vuelo, texto con el multiplicador en grande, una estela que sube (un bloque por segundo) y la lista de pasajeros. Una edición por segundo como mucho, sin encadenar una sobre otra. Al explotar, una gráfica PNG (~15 KB, ~60 ms de CPU) con la curva, un punto por cada retirada con su nombre y una estrella en la explosión, más la tira de las últimas 10 rondas. Si otros mensajes han enterrado la mesa, la ronda siguiente se manda abajo y la vieja pierde los botones.
- Dinero: la apuesta se cobra al entrar (`place_bet`) y se paga al retirarse o al explotar (`pay_winnings`, con 0 si explota). Fiscalmente es juego, como el resto del casino (art. 33.1 y 33.5.d LIRPF). El resultado de la ronda dice lo que Perro Sanxe retiene o devuelve a cada uno y quien se retira a mano lo ve además en privado. Si el bot se apaga de forma ordenada, en embarque devuelve lo apostado y en vuelo retira a todos en el multiplicador del momento.
- Logros: 24 en la categoría 🚀 Crash (rondas, retiradas, 2x a 1.000x, auto-retiro, por los pelos, el último en saltar, ni despegó, la avaricia rompe el saco, rondas de 100x, rondas con 3 y 6 personas…).

### 6 ter.6. Minas

- `minas [cantidad] [minas]` cobra y abre un tablero de 5×5 propio que solo pulsa su dueño. Minas: de 1 a 12 (por defecto, las de la última vez o 2). Apuesta por defecto, 100 Y$.
- La primera casilla siempre es buena, como en el Buscaminas de Windows: las minas se colocan con `secrets` entre las otras 24 justo después del primer clic y desde ahí no se mueven. Como no tiene riesgo, paga ×1 (devuelve la apuesta). Mediana de casillas buenas antes de explotar: 13 con 1 mina, 7 con 2, 5 con 3 y 3 con 5.
- Cada casilla buena después sube el multiplicador; 💰 Cobrar se lleva apuesta × multiplicador y una 💣 lo pierde todo. 🎲 Al azar destapa una casilla cualquiera.
- Multiplicador tras k casillas (contando la segura) con m minas: 0,99 × C(24, k − 1) / C(24 − m, k − 1), con fracciones exactas y sin tope. Cobrar en cualquier momento desde la segunda casilla devuelve el 99 % de media, y para la misma casilla más minas pagan más (con 2 minas la segunda paga ×1,08; con 12, ×1,98). Una prueba lo verifica para cada número de minas y cada k.
- Al terminar, un menú elige de 1 a 12 minas y enseña solo lo que paga limpiar el tablero con cada opción, además de 🔁 Jugar, ½, ×2 y 💰 All-in.
- Por qué 12 y sin tope: limpiar el tablero tiene una posibilidad entre C(24, m) y paga 0,99 × C(24, m). Es simétrico (4 y 20 minas dan lo mismo) y el máximo está en 12 (×2.677.114, 1 entre 2.704.156), así que con más de 12 el premio gordo baja. El antiguo tope de ×10.000 igualaba ese premio de 4 a 20 minas y hacía que no compensara arriesgar más de 4. Hasta 12, cada mina sube el premio gordo.
- Progreso: el texto cuenta las casillas (💎 7/23), dice el multiplicador y lo que sumaría la siguiente casilla con su probabilidad, celebra las casillas 3, 5, 7, 10, 15 y 20, el medio tablero y la última buena, y avisa al batir el récord personal de casillas en una partida. El récord sale de la estadística de logros `mines_streak_max`, así que sobrevive a los reinicios. Al explotar dice hasta dónde llegaste y cuánto te ibas a llevar.
- Mensaje: componentes nuevos de Discord (`LayoutView`), un bloque con el texto y las 25 casillas como botones, una fila de botones y el menú de minas: los 40 componentes que admite un mensaje. Sin imágenes: cada clic es una edición instantánea.
- Dinero: la apuesta se cobra al empezar (`place_bet`) y se paga al cobrar o al explotar (`pay_winnings`, con 0 si explota). Fiscalidad del juego, con la línea de Perro Sanxe en el resultado. Si el tablero caduca (3 min) o el bot se apaga con una partida a medias, se cobra sola; sin casillas destapadas, se devuelve la apuesta.
- Los cobros de ×25 o más se anuncian en el canal.
- Logros: 26 en la categoría 💣 Minas (partidas, diamantes, cobros, minas pisadas, pisar justo después de la segura, tan cerca, ×5 a ×10.000, 10, 15 y 20 casillas en una partida, ganar con 12 minas, limpiar el tablero, 🎲…) y *Todoterreno* en 💰 Casino por jugar a los cinco juegos.

### 6 ter.7. Pachinko

- `pachinko [cantidad] [mapa]` abre una máquina propia con botones que solo pulsa su dueño: 🎯 Lanzar, 🔁 Ráfaga ×5, ⚡ Turbo, ½, ×2, 💰 All-in, 📋 Premios (en privado) y un menú de tablero. Cantidad y tablero van en cualquier orden (`.pachinko oni 500`); en `/pachinko`, `mapa` es una lista. Apuesta por defecto, 100 Y$; mínimo, 10 Y$ (una bola tiene que valer al menos 1 Y$).
- Cada tanda lanza 10 bolas. Cada una rebota a cara o cruz en cada fila de clavos y cae en el bolsillo que corresponde a sus rebotes a la derecha (tablero de Galton). El del centro es la ranura START. Una bola vale la apuesta entre 10 y el pago se redondea una vez por tanda.
- Cada bola en START gana una tirada del sorteo de la pantalla, con reserva de 4 como en las máquinas reales (保留); las que entran con la reserva llena se pierden. Tres iguales es atari: con número par paga un premio gordo; con impar es un **RUSH** que encadena premios; con el 7, **SUPER RUSH**, con más probabilidad de seguir y más tope.
- Reach: cuando no toca, 1 de cada 6 tiradas enseña dos números iguales y el del centro frena y para al lado. Se decide después del resultado y no cambia la probabilidad; la tabla de premios lo dice.
- Tableros (`bot.services.pachinko.BOARDS`). Todos devuelven lo mismo de media y cambia el riesgo. Números con fracciones exactas en las pruebas, que también comprueban que el riesgo sube en orden:

  | Tablero | Filas | Bolsillos (de la esquina al centro) | Atari | Bolas por premio | Rush / super rush | Retorno | Atari cada | Atari medio |
  |---|---|---|---|---|---|---|---|---|
  | 🌸 Sakura | 8 | ×5, ×3, ×2, OUT | 1/16 | 11 | 1/2 hasta 5 · 2/3 hasta 7 | 95,2 % | 7 tandas | ×1,8 |
  | 🏮 Clásica | 10 | ×10, ×3, ×1, OUT, ×1 | 1/40 | 30 | 3/5 hasta 10 · 4/5 hasta 15 | 94,6 % | 17 tandas | ×6,3 |
  | 🐉 Dragón | 10 | ×50, ×4, ×1, OUT | 1/50 | 69 | 3/5 hasta 10 · 4/5 hasta 15 | 94,4 % | 22 tandas | ×14,4 |
  | 👹 Oni | 12 | ×100, ×10, ×2, OUT | 1/84 | 100 | 3/4 hasta 15 · 6/7 hasta 25 | 94,3 % | 39 tandas | ×29,6 |

- Por qué se elige y no sale solo al azar: el tablero decide el riesgo, y que te cambien el riesgo sin elegirlo sería injusto. Quien quiera sorpresa tiene 🎲 Al azar en el menú (cada tanda en un tablero distinto, y el resultado dice cuál tocó). El tablero elegido se recuerda por miembro hasta reiniciar el bot; por defecto, la Clásica.
- Animación: GIF por tanda (130-370 KB, ~0,8 s de CPU fuera del event loop) con bombillas que persiguen por el borde y el marco de la pantalla, rótulo de neón, adornos que se mueven, bolas rebotando y la pantalla jugando la reserva mientras siguen cayendo. Cada tablero tiene su tema: Sakura rosa con flores de cerezo, Clásica morada con molinillos, Dragón turquesa con perlas de tres comas y Oni rojo con llamas. Con más filas, los clavos se juntan para que quepa todo. En el reach las bombillas corren y sale el cartel; en el atari la pantalla se pone dorada, las bombillas hacen arcoíris, llueven bolas y sube el contador del rush. El último fotograma es el PNG final con las bolas contadas en cada bolsillo. Turbo y Ráfaga mandan solo el PNG. Bolsillos distinguibles por forma (estrella, rombo, círculo, aspa y tulipán) y texto.
- Dinero: `settle_bet` cobra, paga y ajusta el IRPF en una transacción. Fiscalidad del juego (art. 33.1 y 33.5.d LIRPF), línea de Perro Sanxe en el resultado, aviso de la Renta en cada botón.
- Los SUPER RUSH, los rush de 5 premios o más y las ganancias de ×20 la apuesta se anuncian en el canal, con el tablero.
- Logros: 37 en la categoría 🌸 Pachinko (tandas, bolas por START, reach, reach perdidos, ataris, rush, 7️⃣7️⃣7️⃣, renchan de 3 a 25, esquinas, reserva llena, bolas al limbo, tanda en blanco, premios grandes, sesión larga, Ráfaga, turbo, de madrugada, 100 tandas en Sakura y en Oni, ataris en Dragón y en Oni y jugar en los cuatro tableros) y *Ludópata integral* en 💰 Casino por jugar a los seis juegos.

### 6 ter.7 ter. Loterías del Estado

- `loteria` abre un panel con pestañas (🏠 Inicio, 🎫 Nacional, 🔵 Primitiva, 🟢 Bonoloto, 🟡 Gordo, ⭐ Euromillones, 🟣 Rascas, 🎟️ Mis boletos). Con `/` es efímero; con `.loteria` es público y a quien pulse sin ser el dueño se le abre su propio panel. Se juega en los canales de casino.
- Juegos, precios a 10 Y$ por euro y reparto según sus normas oficiales (detalle y artículos en `bot.services.lottery`):

  | Juego | Sorteos (hora de Madrid) | Precio | A premios | Reparto |
  |---|---|---|---|---|
  | Nacional del jueves | jueves 21:30 | 30 Y$ el décimo | 70 % | Premios fijos: 1º 300.000 Y$, 2º, aproximaciones, centenas, terminaciones, 4+7+9 extracciones, 3 reintegros |
  | Nacional del sábado | sábado 13:00 | 60 Y$ | 70 % | 1º 600.000 Y$ y el mismo esquema |
  | Navidad | 22 de diciembre 9:00 | 200 Y$ | 70 % | Gordo 4.000.000 Y$, 2º, 3º, dos 4º, ocho 5º, 1.794 pedreas, aproximaciones, centenas, dos últimas cifras y reintegro |
  | Niño | 6 de enero 12:00 | 200 Y$ | 70 % | 1º 2.000.000 Y$, 2º, 3º, extracciones y 3 reintegros |
  | La Primitiva | lunes, jueves y sábado 21:30 | 10 Y$ | 55 % | 10 % reintegro; 45 % menos 80 Y$ por cada 3 aciertos, 30/37/6/11/16 % a especial, 1ª, 2ª, 3ª y 4ª |
  | Bonoloto | lunes a sábado 21:30 | 5 Y$ | 55 % | 10 % reintegro; 45 % menos 40 Y$ por cada 3 aciertos, 45/24/12/19 % |
  | El Gordo de la Primitiva | domingo 21:30 | 15 Y$ | 55 % | 10 % reintegro; 22 % a la 1ª (la mitad a la reserva del Estado); 23 % menos 30 Y$ por cada 2 aciertos, a la 2ª-7ª |
  | Euromillones | martes y viernes 21:00 | 22 Y$ | 50 % | 13 categorías (43,20 % la 1ª); 4,80 % a la reserva del Estado. Sin El Millón |
  | Rasca X10 | al momento | 20 Y$ | 64 % | Tabla de la emisión de 9.000.000 de boletos de la ONCE |
  | Rasca 7 y Media | al momento | 10 Y$ | 59 % | Tabla de la emisión de 6.000.000 de boletos |

- Probabilidades reales: se calculan con combinatoria y salen en la tabla de cada juego (pleno de la Primitiva 1 entre 139.838.160, 4 aciertos 1 entre 1.032; Euromillones 1 entre 139.838.160). En la práctica, lo que toca son reintegros y premios bajos.
- Juegos de bote: los premios se reparten a partes iguales entre los acertantes de cada categoría del servidor. Una categoría desierta pasa a la siguiente inferior y la última al bote; la del bote se acumula para el sorteo siguiente. Ninguna categoría inferior puede cobrar más que una superior (se juntan fondos, norma 8ª.2 de la Primitiva). Los premios fijos (3 aciertos, 2 aciertos del Gordo) se pagan siempre, aunque la venta del servidor no llegue: lo pone el Estado.
- Bote garantizado: El Gordo (4,5 M€ = 45.000.000 Y$) y Euromillones (17 M€ = 170.000.000 Y$) garantizan el mínimo real o el 25 % del saldo del Estado, lo que sea menor. Solo se paga si hay acertante. La Primitiva y la Bonoloto no tienen mínimo, como las reales.
- Compra: apuestas automáticas (1, 5 o 10), o hasta 10 escritas a mano (reintegro, clave y estrellas opcionales: si no se marcan, al azar). Nacional: décimo al azar, billete de 10 décimos o número elegido con 1-10 décimos. Máximo 100 apuestas o décimos por persona y sorteo. Las ventas cierran a la hora del sorteo.
- Sorteos: cada minuto el bot celebra los que ya tocan y tienen apuestas en el servidor (las bolas salen de `secrets.SystemRandom`), paga y anuncia en el canal donde se compró por última vez: combinación, premiados (un renglón por persona con su total y su mejor boleto), bote que pasa y gravamen. Un sorteo no se puede cerrar dos veces ni dejar boletos sin repartir.
- Rascas: se cobran y resuelven en una transacción. El panel enseña las 9 casillas tapadas con spoilers de Discord (se rascan pulsando) y el veredicto también tapado. Con premio salen tres importes iguales.
- Dinero y fiscalidad (`EconomyService.lottery`): la compra va entera al Estado sin IGIC (exenta, art. 10.1.19º de la Ley 20/1991). Los premios salen de la cuenta del Estado y pagan el gravamen especial del 20 % sobre lo que pase de 400.000 Y$ por décimo o apuesta (disposición adicional 33ª LIRPF), que vuelve al Estado y cuenta en `hacienda`. No entra en la retención del casino ni en la renta semanal. Si el Estado no tiene saldo para un premio, emite deuda pública por la diferencia; `hacienda` la enseña.
- Logros: 35 en la categoría 🎟️ Loterías (décimos y apuestas, gasto, premios, ganancias, mayor premio, reintegros, Navidad, Niño, pedrea, el Gordo, Euromillones, 4 y 5 aciertos, bote, rascas, premio máximo de un rasca, 100 apuestas en un sorteo, quedarse a cero y *Hacienda también juega*, con su historia, al pagar el primer gravamen).

### 6 ter.7 bis. Tienda: El Colmado de Jovani

- `tienda`: escaparate con pestañas (🛍️ Todo, 🎭 Roles, ⚡ Potenciadores, 💎 Coleccionables), 5 artículos por página, cada uno con su botón **Comprar · precio**. Cada ficha enseña el precio (tachado y rebajado si hay rebaja, con su fin), la descripción y etiquetas: qué es, 🔥 lo más vendido (si ha vendido al menos 3), 🆕 nuevo (3 primeros días), 📦 quedan X de Y, 🔒 nivel mínimo y 👤 máximo por persona. Es público; las pestañas y páginas solo las mueve quien lo abrió (a los demás se les abre su propio escaparate), pero **Comprar** lo puede pulsar cualquiera.
- Caja (solo la ve quien compra): ticket con precio, rebaja, base imponible, IGIC y total; línea de Perro Sanxe con el tipo y el artículo de la ley; saldo antes y después o lo que falta. Botones 💳 Pagar y Cancelar. Si ya tienes un potenciador, avisa de que el nuevo va a la cola; si ya tienes el rol alquilado, de que se suma.
- Al pagar, todo en una transacción: se vuelve a comprobar el artículo (si el precio ha cambiado o se ha agotado mientras mirabas, no se cobra), se cobra la base a la caja de la tienda (`user_id = -200`) y el IGIC al Estado, se gasta la unidad y se apunta la factura. Sale una **factura simplificada** numerada por servidor (`T2026-000042`), que dice que no hay derecho de desistimiento por ser contenido digital entregado al momento (art. 103.m del Real Decreto Legislativo 1/2007), y un aviso público en el canal con lo pagado y el IGIC. Pagar lleva el aviso de la Renta.
- Tratamiento fiscal: IGIC (art. 4 de la Ley 20/1991: entrega a título oneroso en Canarias) al tipo del artículo, sobre la base ya rebajada (art. 22 de la Ley 20/1991). Sin IRPF: gastar no es renta. `hacienda` suma el IGIC a lo recaudado.
- Roles: para siempre o alquilados. Un rol para siempre solo se compra una vez; uno alquilado se puede volver a comprar y alarga el alquiler. Cada 5 minutos el bot quita los alquileres vencidos, salvo que el miembro tenga el mismo rol por otra compra en vigor. No se vende un rol que ya llevas sin haberlo comprado. Si al pagar no se puede dar el rol, se devuelve todo (base e IGIC, que deja de contar como recaudado).
- Potenciadores: multiplican el XP base de mensajes y voz (de ×1,1 a ×3) durante un tiempo. Si se juntan, el nuevo empieza cuando acaba el último. El multiplicador se lee de memoria, sin consultas por mensaje.
- Coleccionables: no hacen nada; se tienen. Con existencias limitadas, cada unidad lleva número de serie.
- `mochila [miembro]`: roles (puestos, guardados o con su vencimiento), potenciadores (activos o en cola) y coleccionables agrupados con sus números de serie. Su dueño tiene un menú para ponerse y quitarse los roles que compró para siempre.
- `catalogo` (Admin): ver sección 6 quater.1.

### 6 ter.7 quater. Trabajo: `pala`

- `pala` abre el panel del curro de quien lo pide (público, pero solo lo toca su dueño). Sin contrato, un menú con los oficios; con contrato, el puesto (1 a 5), el sueldo base, la batería, la barra de rendimiento, la familia, los turnos de hoy, las extras legales de la semana, el tipo del próximo turno y lo que falta para ascender. Botones ⛏️ Fichar, 📈 Ascender (cuando toca), 🎓 Formación (cuando el puesto siguiente pide un curso) y 📜 Vida laboral; menús para la máquina de café y para cambiar de oficio. Si un administrador ha elegido canales con `tajo`, solo se abre en ellos (o en sus hilos).
- Escala de las nóminas: todo lo del trabajo va a **100 Y$ por euro**, no a 10 como el resto de la economía. Un turno son unas 2 horas: el puesto 1 cobra unos 1.700 Y$ (17 €, el SMI por hora) y la jornada completa de un celador sale a unos 25.000 € al año. Así un turno cunde lo mismo que un IMV a racha máxima (≈ 15 tiradas de 100 Y$) sin que el IRPF y la Seguridad Social se disparen: a 10 Y$/€ esos sueldos serían de 250.000 € al año y tributarían al 45 %. La nómina, la cotización y el IRPF se calculan en euros a esa escala; a la renta semanal y al IMV llega el bruto pasado a la escala general (÷10). Fórmulas y gráficas en `docs/economia-trabajo.md`.
- Oficios (sueldo bruto por turno con nota media; sanidad y oficina, más abajo): 🦺 **Obra**: peón 1.800, oficial de segunda 2.800 (curso de PRL de 20 h), oficial de primera 4.500, encargado 7.500 (curso de recurso preventivo), constructor 13.000 (autónomo, alta en el REA). 🍽️ **Hostelería**: friegaplatos 1.600, camarero 2.500 (carnet de manipulador), jefe de rango 4.000, jefe de cocina 7.000 (curso de alérgenos), dueño del chiringuito 13.000 (autónomo, licencia de apertura). 🌹 **Política (PSOE)**: pegacarteles 1.600, concejal 2.700 (puesto en las listas), diputado autonómico 5.000, ministro 10.000 (cartera ministerial), consejero de una eléctrica 25.000 (puerta giratoria). Las formaciones se compran una vez, con IGIC general, y valen para siempre. La política es sátira del partido del Gobierno y de sus tópicos; no se nombra a ninguna persona real.
- Turno: un minijuego de decidir, no de reflejos (Discord tarda de 100 a 500 ms por pulsación). Dura 30 s en los puestos 1 y 2, 40 s en el 3 y 60 s en el 4 y 5. Motores: **cavar** (cada turno trae un plano distinto: qué marca del suelo es segura, cuál es tubería o cable y cuál roca; romper algo resta), **detectar** (el obrero que se escaquea; la partida con sobrecoste del presupuesto), **memoria** (platos, comandas, rutas de carteles, votaciones: se enseña la secuencia y hay que repetirla en orden) y **diálogo** (rueda de prensa, comisión de investigación, consejo de administración, clientes del chiringuito: tres respuestas, una buena). La nota es aciertos sobre el máximo, más hasta 10 puntos si se acaba antes; el sueldo va del 70 % (nota 0) al 130 % (nota 100). Si se acaba el tiempo sin terminar, el turno se cobra con lo hecho.
- Jornada: 4 turnos ordinarios al día (hora canaria). Los siguientes son horas extra, que pagan 1,25 veces la base (el art. 35.1 ET exige pagarlas al menos como la ordinaria) y cansan más. Límite legal: 2 turnos extra por semana (el art. 35.2 ET las limita a 80 horas al año). Pasado el límite, el jefe ofrece pagarlas en B: se cobra la base sin IRPF ni cotización y sin que lo vea el IMV, pero con un 10 % de inspección que obliga a devolverlo con un 20 % de recargo (al Estado) y suspende el IMV 3 días.
- Batería: de 100 a −30; se recarga 5 puntos por hora. Un turno ordinario gasta 18 y uno extra 27. Por debajo de 20 estás reventado (el minijuego da un 30 % menos de tiempo y hay un 8 % de accidente laboral); por debajo de 0, zombi (20 % de accidente); en −30 no se ficha. El accidente da 24 h de baja y una prestación del 75 % del sueldo diario medio de los últimos 30 días (el porcentaje real de la incapacidad temporal por accidente de trabajo), que tributa como rendimiento del trabajo. Máquina de café: cortado (+10, 14 Y$) y barraquito (+20, 25 Y$), con IGIC; desde el cuarto del día, temblores (los botones del minijuego cambian de sitio).
- Familia: de 0 a 100. Baja 8 por turno extra, 5 por turno de madrugada (0:00 a 6:00) y 3 por turno en domingo o festivo; sube 10 por cada día entero sin fichar. No da dinero: activa eventos familiares por debajo de ciertos umbrales y, al llegar a 0, la intervención familiar (al día siguiente, nada de extras).
- Nómina de cada turno: bruto; Seguridad Social del trabajador con los tipos de 2026 (contingencias comunes 4,70 %, desempleo 1,55 %, formación 0,10 %, MEI 0,15 %) hasta la base máxima (5.101,20 € al mes) más su parte de la cotización de solidaridad (art. 19 bis LGSS; 1,15/1,25/1,46 % en 2026, el 16,6 % a cargo del trabajador); IRPF con la escala estatal y la canaria sobre el rendimiento neto (bruto − cotizaciones − 2.000 € de otros gastos, art. 19.2.f LIRPF − reducción del art. 20 LIRPF), proyectando la renta con los últimos 30 días; y neto. La empresa paga además el 30,65 % (23,60 + 5,50 + 0,20 + 0,60 + 0,75) y un 1,5 % de accidentes de trabajo. La empresa no existe: esa cotización se crea y entra igualmente en el Estado, a propósito, para engordar las arcas y los botes de la lotería. Los autónomos (constructor y dueño del chiringuito) no cotizan por turno: pagan una cuota fija de 10.000 Y$ (100 €) al fichar el primer turno de cada semana (valor de juego; la cuota real va por tramos de rendimientos desde el RDL 13/2022) y su IRPF va por la escala, sin la reducción del art. 20. Cada nómina queda en `economy_payroll` y su bruto en `economy_tax_records`.
- Ascensos, al estilo de los Sims: barra de rendimiento llena (cada turno suma o resta (nota − 50) / 2), un mínimo de días distintos fichando en el puesto (2 a 5), dos o tres tareas del puesto (turnos de 90 o más, turnos sin romper nada, escaqueados pillados, comandas perfectas, preguntas esquivadas, extras, marrones) y, si el puesto siguiente la pide, su formación. Entonces se ofrece el ascenso, que se puede rechazar (vuelve a ofrecerse al día siguiente). Sin despidos: con la barra a 0 hay un aviso y, si se repite, se baja un puesto. Tras 7 días sin fichar, excedencia: no se pierde nada y la barra vuelve a la mitad. Al cambiar de oficio se conserva el puesto alcanzado en cada uno.
- Eventos: uno de cada 6 turnos trae un marrón con dos opciones. Para todos: quedarse más tiempo (media base en nómina), cena de empresa, cobrar parte en B. Familiares (con la familia baja): tu madre por Discord, la comunión del sobrino (o un Bizum), la nota en la nevera. Obra: el jubilado que mira, la calima (permiso retribuido por fenómenos meteorológicos adversos, art. 37.3.g ET). Hostelería: la propina al bolsillo, la mesa que se va sin pagar. Política: el sobre, el enchufe del sobrino, la comisión de la rotonda y el avión oficial para un concierto. Si la UCO te pilla con un sobre, devuelves lo cobrado con un 20 % de recargo y te quedas sin IMV 3 días (salvo indulto, un 20 % de las veces), y te preguntan si dimites: dimitir baja un puesto.
- Logros: categorías 🪏 Trabajo (turnos, ascensos, horas, familia, cansancio, nómina e IMV), 👷 Oficios (obra, hostelería, política y la corrupción), 🏥 Sanidad, 💻 Oficina y 🇭🇰 Hong Kong. Las historias de *Primera guardia*, *183 días* y *Ley Beckham* explican la norma. Varios llevan texto propio: la primera nómina, el mito de «me suben de tramo», rechazar un ascenso (la trampa de la pobreza), pasar del límite de horas extra y «Socio de Hacienda» (una semana en la que IRPF, Seguridad Social de las dos partes, IGIC y Patrimonio superan el neto cobrado).
- 🏥 **Sanidad**: celador 1.700 (memoria: traslados en camilla), TCAE 2.800 (memoria: la ronda de planta; título de TCAE), enfermero 4.800 (detectar: el triaje, quién pasa primero; grado en Enfermería), médico residente 8.000 (test MIR de 4 respuestas; academia del MIR) y médico adjunto 14.000 (diálogo: la consulta de cinco minutos; plaza por OPE). Para rendir hay que hacer **guardias**, desde TCAE: los turnos que no son guardia suben la barra como mucho 5 puntos. La guardia es un botón aparte: el doble de rondas y de tiempo, 45 de batería, −15 de familia, paga 1,6 veces la base (la hora de guardia sale más barata que la ordinaria, como en muchos servicios de salud) y mueve la barra el doble. No son horas extra (en el Estatuto Marco, Ley 55/2003, son jornada complementaria): no cuentan para el límite ni hay B. Después quedas **saliente** 12 h sin poder fichar (valor de juego; el Supremo reconoció en 2019 al personal sanitario 36 h seguidas de descanso semanal). El residente tiene 2 guardias mínimas por semana: por cada una que falte, la barra baja 20 al empezar la semana siguiente. Eventos: el «congreso» en Cancún del visitador médico (riesgo de expediente), el familiar que grita, la lista de espera, la huelga de residentes, los aplausos de las ocho y el cambio de turno.
- 💻 **Oficina (tecnología)**: becario 1.600 (memoria: los cafés del equipo), programador junior 2.800 (detectar: la línea con el bug; bootcamp), senior 5.000 (detectar: la pull request que rompe producción), tech lead 9.500 (diálogo: acortar reuniones; Scrum Master) y CTO de startup 18.000 (diálogo: inversores; MBA). **Teletrabajo** (Ley 10/2021): botón aparte que gasta la mitad de batería y suma familia, pero mueve la barra la mitad; un 30 % de las veces te escriben a las once de la noche y eliges entre contestar o la desconexión digital (art. 88 de la Ley Orgánica 3/2018). **Stock options**: el CTO cobra el 70 % del bruto y el 30 % se queda en opciones; cada turno hay un 2 % de exit (las opciones se multiplican de 2 a 10 y se cobran: exentas hasta 5.000.000 Y$, los 50.000 € de la Ley 28/2022 para empleados de empresas emergentes, y el resto con IRPF) y un 2,5 % de quiebra (se pierden). Eventos: despliegue en viernes, la reunión que podía ser un correo, la oferta de Dublín, LinkedIn, el paintball y el «ninja de la IA».
- 🇭🇰 **Hong Kong**: desde programador senior, el botón 🌏 compra el billete (25.000 Y$ más IGIC; simplificación: la parte del vuelo fuera de Canarias no lo pagaría), te da jet lag (−30 de batería) y te muda. Allí cobras el doble (paquete de expatriado) y la nómina es la de Hong Kong: MPF del 5 % del trabajador y otro 5 % de la empresa (con el tope de HK$18.000 al año) y salaries tax de 2025/26 (escala del 2 al 17 % sobre lo que pasa de la deducción personal de HK$132.000, o el 15 % estándar si sale menos), a 9 HK$ por euro (y 100 Y$ por euro). Eso va a una cuenta de Hong Kong, no al Estado. En España: mientras sigues siendo residente fiscal, los primeros 60.100 € al año (16.466 Y$ de nómina al día) están exentos por el art. 7.p LIRPF (Hong Kong no es jurisdicción no cooperativa según la Orden HFP/115/2023) y lo que pase paga IRPF descontando lo pagado allí (art. 80 LIRPF). A los 4 días dejas de ser residente (los 183 días del art. 9.1.a LIRPF, a la escala del juego de una semana por año) y España ya no cobra nada. Mientras vives fuera no hay IMV (arts. 10 y 36.e de la Ley 19/2021) ni se puede cambiar de oficio. Al volver (otro billete), si has pasado fuera 5 semanas («5 años»), la **Ley Beckham** (art. 93 LIRPF): durante 6 semanas tu IRPF es un 24 % fijo (47 % por encima de 600.000 €). Eventos: la señal de tifón n.º 8 (te quedas en casa cobrando, como recomienda el Labour Department), dim sum con Robuso, el colega del servidor que vive allí, Lan Kwai Fong, la videollamada de tu madre a las tres de la mañana y, ya como no residente, la carta de Hacienda que duda de que vivas fuera (art. 9.1.b LIRPF): ignorarla puede acabar en regularización.
- Persistencia: `work_contracts` (contrato en curso, en JSON), `work_history` (puesto actual y máximo en cada oficio), `work_training`, `work_shifts` (turnos de los últimos 35 días) y `work_settings` (canales). La batería, la familia, las excedencias, las bajas y la cuota de autónomos se calculan al fichar, sin tareas en segundo plano; el único temporizador es el que cierra un turno cuando se acaba su tiempo.

### 6 ter.8. Criterios de aceptación

- Ningún saldo puede quedar negativo ni gastarse dos veces, aunque se pulsen botones a la vez.
- Solo el dueño de una mesa puede apostar en ella.
- Una apuesta ilegal en el tapete se rechaza con un ejemplo de formato válido.
- Al salir el bot de un servidor se borra su economía.

### 6 ter.9. Logros

- `logros [miembro]`: resumen (logros conseguidos, puntos, por categoría, los 5 últimos, los 3 más cercanos y el más raro del servidor), un menú con cada categoría y un botón 🏆 Ranking por puntos. Solo quien abre la vista puede cambiar de página.
- 624 logros en 24 categorías: 💬 Chat, 🗓️ Horarios y fechas, 🎙️ Voz, ❤️ Social, 📝 Lista, 📈 Niveles, 🎡 Ruleta, 🃏 Blackjack, 💰 Casino, 🎰 Tragaperras, 🌋 Botes, 🚀 Crash, 💣 Minas, 🌸 Pachinko, 🎟️ Loterías, 🛍️ Tienda, 💸 Bizum, 🏛️ Economía y Hacienda, 🪏 Trabajo, 👷 Oficios, 🏥 Sanidad, 💻 Oficina, 🇭🇰 Hong Kong y 🏆 Coleccionista. Una categoría puede marcarse `upcoming` ("próximamente") mientras su juego no exista: sus logros se ven pero no se pueden conseguir ni cuentan para el total.
- Rarezas y premio bruto: ▫️ Común 50 Y$ (10 puntos), 🔹 Raro 200 Y$ (25), 💠 Épico 750 Y$ (50), 🌟 Legendario 2.500 Y$ (100), 👑 Mítico 10.000 Y$ (250). Los emojis tienen formas distintas para que se distingan sin depender del color.
- Fiscalidad: el premio es una ganancia patrimonial por un concurso del servidor (art. 33.1 LIRPF), sujeta a retención como los premios (art. 75.2.c RIRPF). Se cobra con `pay_income`: retención de IRPF que va a la cuenta del Estado y línea de Perro Sanxe en el aviso.
- 113 logros son secretos: se ven como `???` (con el porcentaje del servidor que lo tiene) hasta conseguirlos.
- Qué cuenta: mensajes (y propiedades sin guardar el texto: hora, largo, mayúsculas, enlaces, adjuntos, respuestas, risas…), minutos en voz con al menos otra persona sin ensordecer (fuera del canal AFK; también minutos silenciado, compartiendo pantalla, con cámara, de madrugada, solo en el canal y la sesión seguida más larga), reacciones dadas y recibidas (una por persona y mensaje), felicitaciones de cumpleaños, bienvenidas dadas con el botón 👋, Patrimonio pagado, donativos, nivel y racha de días, cada tirada de ruleta y tragaperras y mano de blackjack, lo apostado, el mayor premio y la mayor pérdida, all-in, rachas de casino entre juegos, IMV, IRPF pagado, renta presentada, saldo máximo y compras de la tienda (número, gasto, IGIC, roles, renovaciones, potenciadores y su cola, coleccionables, rebajas, IGIC de lujo, mayor compra, ediciones limitadas, unidad nº 1, última unidad y quedarse a cero) y Bizums (enviados y recibidos, en número y en dinero, el mayor, lo enviado en un día, los de justo el máximo o el mínimo y quedarse a cero).
- Escrituras: mensajes, reacciones y voz se acumulan en memoria y se guardan una vez por minuto, una transacción por servidor. Los juegos, el IMV, la renta y las subidas de nivel se guardan en el momento. Las rachas de casino y las sesiones de voz viven en memoria y se cortan con un reinicio.
- La primera vez que el bot ve a alguien tras arrancar, recupera como máximos sus mensajes del historial importado y su nivel actual. Por eso, en el primer mensaje tras desplegar, cada veterano desbloquea y cobra lo que ya tenía.
- Se anuncia en el canal donde se consiguió (en voz, el chat del canal de voz; si no se sabe, el canal del sistema). Con más de 8 a la vez, el aviso los resume.
- Un logro puede llevar un texto propio (`story`) que sale en su aviso. Como cada logro se desbloquea una sola vez, sirve de gancho de "la primera vez que…". Lo usa *Bienvenido a España* (secreto): la primera vez que a alguien le retienen IRPF, venga de donde venga el dinero, Perro Sanxe le explica que ha pasado del mínimo personal (55.500 Y$ al año, unos 4.561 Y$ cada 30 días), que desde ahora se lleva parte de cada ganancia y que lo retenido de más en el casino vuelve con la `renta`. Al entrar al servidor no se dice nada de esto. Quien ya había pagado IRPF antes de este cambio lo recibe con su siguiente acción. También lo usa *A espaldas de Sánchez* (💸 Bizum): el primer Bizum de más de 10.000 Y$, el máximo por operación en España.
- Al salir el bot de un servidor se borran sus logros.
## 6 quater. Administración

### 6 quater.1. Comandos

Todos funcionan con `/` y con `.`, con el mismo nombre:

| Comando | Qué hace |
|---|---|
| `borrar <cantidad> [miembro]` | Borra hasta 100 mensajes del canal (solo los de ese miembro, si se indica). Ignora los de más de 14 días. |
| `callar <miembro> <duración> [motivo]` | Aislamiento temporal de Discord. Duración: `10m`, `2h`, `1d`, `1h30m`; sin unidad, minutos; máximo 28 días. |
| `hablar <miembro>` | Quita el aislamiento. |
| `echar <miembro> [motivo]` | Expulsa del servidor. |
| `banear <miembro> [motivo]` | Banea sin borrar mensajes anteriores. |
| `indultar <id>` | Levanta un baneo (ID numérico o mención). |
| `cerrar` / `abrir` | Quita o devuelve a `@everyone` el permiso de escribir y crear hilos en el canal actual. |
| `lento <segundos>` | Modo lento del canal (0-21600; 0 lo quita). |
| `decir <texto>` | El bot escribe el texto. En `.decir` se borra tu mensaje; `/decir` admite otro canal. Nunca menciona a `@everyone`, `@here` ni roles. |
| `apodo <miembro> [apodo]` | Cambia el apodo; sin apodo, lo quita. |
| `rol <miembro> <rol>` | Da el rol si no lo tiene; si lo tiene, se lo quita. |
| `bienv [gif] [canal]` | GIF y canal de la bienvenida; sin argumentos, enseña los actuales. Responde con una vista previa (en `/`, solo la ves tú). Ver sección 3.3. |
| `catalogo` | Abre la trastienda (en `/`, solo la ves tú; en `.`, queda en el canal pero solo la toca quien la abrió). Botones ➕ Rol (eliges el rol y rellenas nombre, precio, duración del alquiler o vacío para siempre, y descripción), ➕ Potenciador (multiplicador y duración) y ➕ Coleccionable (existencias). ✏️ Editar abre la ficha de cada artículo: datos, 📦 límites (existencias, máximo por persona, nivel mínimo), 🏷️ rebaja (porcentaje hasta el 90 % y duración), tipo de IGIC, ocultar/mostrar y retirar (con confirmación; lo vendido se queda en las mochilas). El nombre puede empezar por un emoji, que pasa a ser el icono. Rechaza roles por encima del del bot, gestionados por integraciones o con permisos de moderación o administración. Ver sección 6 ter.7 bis. |
| `tajo [canal] [todos]` | Canales donde se puede usar `pala`. Un canal lo añade o, si ya estaba, lo quita; `todos` (en `.tajo`, también `cualquiera`) vuelve a permitirla en cualquier canal. Sin argumentos enseña los actuales. Responde solo a ti en `/`. Ver sección 6 ter.7 quater. |
| `niveles [acción] [canal] [segundos]` | Sin argumentos, enseña el estado de los niveles. Acciones: `importar` (lee el historial y, al acabar la primera vez, enciende los niveles), `activar`, `desactivar` (no borra XP) y `mismo` (avisos donde se sube). Un canal fija dónde se anuncian las subidas; unos segundos (10–3600), el enfriamiento del XP por mensaje. En `.niveles` van en cualquier orden. Ver sección 4.0. |

### 6 quater.2. Autorización y seguridad

- Solo los miembros con el permiso **Administrador** pueden usarlos. Se comprueba en el servidor en cada invocación (`cog_check` y `interaction_check`); que Discord oculte los `/` a los demás (`default_permissions`) es solo estética.
- Se replica la jerarquía de Discord antes de llamar a la API: nadie actúa sobre sí mismo (salvo `apodo`), sobre el bot ni sobre el dueño, ni sobre alguien con un rol igual o superior al suyo o al del bot. El dueño del servidor está por encima de esa regla.
- Cada acción queda en el registro de auditoría con el motivo y quién la pidió.
- El bot necesita, según el comando: Gestionar mensajes, Aislar temporalmente a miembros, Expulsar, Banear, Gestionar canales, Gestionar apodos y Gestionar roles (también para dar los roles de la tienda). Si le falta alguno, responde que no tiene permiso en vez de fallar en silencio.

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

## 6 sexies. Lista de cosas que hacer: `lista`

Lista de tareas compartida del servidor, para no apuntar los pendientes en mensajes sueltos que se pierden en el chat.

- `lista`: vuelve a publicar la lista en el canal donde se escribe.
- `lista <tarea> [prioridad alta|media|baja]`: la apunta y vuelve a publicar la lista. Sin prioridad, es media. La prioridad se escribe con la palabra «prioridad» (o «prio») delante y en cualquier punto del texto: `.lista arreglar el purge prioridad alta`; «comprar una mesa alta» no cuenta como prioridad. Con `/lista`, la prioridad también se elige en un desplegable, que manda sobre la del texto.
- La lista sale ordenada: 🔥 alta, 📌 media y 💤 baja (iconos distintos, no solo colores) y, dentro de cada prioridad, de la más antigua a la más nueva. Cada tarea muestra quién la apuntó.
- Solo hay una lista a la vista: al publicar otra, el bot borra el mensaje de la anterior. Con `.lista <tarea>` borra también el mensaje de la orden, porque la tarea ya queda en la lista; necesita **Gestionar mensajes** y, si no lo tiene, lo deja y sigue.
- Las tareas se tachan con el menú ✅ de la lista (varias a la vez). Puede tachar una tarea quien la apuntó o un administrador; si se eligen ajenas, se tachan solo las propias y la lista lo dice. Al tachar, la tarea se borra y la lista se actualiza en el mismo mensaje. El menú sigue funcionando tras reiniciar el bot.
- Límites: 25 tareas pendientes por servidor (lo que cabe en un menú de Discord) y 100 caracteres por tarea.
- Logros (📝 Lista): apuntar 1, 25 y 100 tareas, y tachar 1, 25 y 100.

## 7. Persistencia y aislamiento por servidor

El canal y el GIF de bienvenida se guardan por servidor en SQLite, igual que los datos del sistema de niveles.

Toda configuración de servidor debe estar asociada al ID de ese servidor. El bot no debe usar valores de un servidor como valores implícitos para otro. Los datos de progresión se limitarán a lo necesario para operar las funciones descritas y deben poder eliminarse si el bot deja de prestar servicio en un servidor.

## 8. Orden de implementación

1. **Bienvenida y despedida:** frases editables, GIF y canal configurables con `bienv`, vuelta de antiguos miembros y botón de saludo con logros. (Implementado.) Pendiente: activar o desactivar cada mensaje y separar el canal de despedida.
2. **Preparación de niveles:** importar y guardar agregados de mensajes históricos y contar actividad nueva. (Implementado.)
3. **Niveles:** conversión de historial en XP, XP por mensajes nuevos, cooldown, comandos `nivel`/`ranking` y configuración. (Implementado.)
4. **Música:** reproducción y controles de cola con `yt-dlp` y `ffmpeg`. (Implementado.)
5. **Imagen:** comando `magik` con seam carving y los 108 efectos de Dank Memer. (Implementado.)
6. **Sonidos de entrada:** clip personal de hasta 3 s al entrar a voz. (Implementado.)
7. **Economía y casino:** yapdollars, `daily`, `saldo`, ruleta americana, blackjack, tragaperras, Crash, Minas y pachinko. (Implementado.) Siguientes juegos y usos de la moneda pendientes.
8. **Diversión:** `babel`, traducción en cadena por 99 idiomas de frases, apodos y nombres de canal. (Implementado.)
9. **Logros:** 624 logros con premios en yapdollars, `logros` y ranking. (Implementado.)
10. **Tienda:** `tienda`, `mochila` y `catalogo`, con IGIC por tipos, rebajas, alquileres, potenciadores y coleccionables numerados. (Implementado.)
11. **Trabajo:** `pala` con cinco oficios de 5 puestos (obra, hostelería, política, sanidad con guardias y oficina con teletrabajo, stock options y Hong Kong), minijuegos, nóminas con Seguridad Social, IMV reducido, ascensos y eventos; `tajo` para los canales. (Implementado.) Pendiente: más oficios.

Cada fase debe incluir pruebas, permisos mínimos, documentación de uso y los cambios pertinentes a la configuración. Una función se considera terminada únicamente cuando cumple sus criterios de aceptación; aparecer en esta lista no significa que ya esté implementada.

## 9. Decisiones pendientes

Estas decisiones no impiden documentar el alcance, pero deben resolverse antes de cerrar la implementación correspondiente:

- Si la bienvenida y la despedida podrán desactivarse o ir a canales distintos.
- Si se ofrecerá una herramienta administrativa para reiniciar la progresión (`niveles` enciende, apaga y configura, pero no borra progreso).
- Si moderadores podrán controlar música iniciada por otros miembros, o si el control seguirá limitado a "cualquiera en el mismo canal de voz" (comportamiento actual).

Las decisiones pendientes no bloquean las funciones ya implementadas, pero deben resolverse antes de cerrar la funcionalidad correspondiente.
