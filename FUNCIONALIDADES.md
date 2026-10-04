# Especificación funcional del bot

Este documento describe las funcionalidades actuales y futuras y cómo debe comportarse el bot desde el punto de vista de sus usuarios y administradores. Complementa la [Biblia del proyecto](./Biblia.txt), que define las normas técnicas y de calidad.

La bienvenida/despedida descrita en la sección 3, los comandos `ping`, `help`, `level`, `top`, `magik`, `memes` y los 108 efectos de imagen, los comandos de música, los sonidos de entrada, la economía con la ruleta, el blackjack, la tragaperras, el Crash, Minas y el pachinko (`ruleta`, `blackjack`/`.bj`, `slots`, `crash`, `minas`, `pachinko`, `saldo`, `imv`, `hacienda`, `renta`), los cumpleaños (`cumple`, `cumples`) los comandos de administración y `babel` están implementados. Las demás funciones son objetivos futuros salvo que se indique lo contrario.

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

- Al subir de nivel, el bot envía un aviso en el canal donde se ganó la XP (el chat del canal de voz si fue por voz). Solo se muestra el nombre visible, sin activar menciones.
- Subir al nivel N paga 100 × N Y$ brutos (el doble en múltiplos de 5), con retención de IRPF.
- Fuentes de XP además de los mensajes: +50 XP por el primer mensaje del día, 4–6 XP por minuto en voz (sin mute, fuera del AFK y con alguien más sin mutear), +5 XP por reacción recibida de otro (tope 100 al día), racha de +2 % por día seguido escribiendo (tope +20 %) y una hora feliz diaria con XP ×2.
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
- `imv` (Ingreso Mínimo Vital, antes `daily`): exento de IRPF como el real (art. 7.y LIRPF). Paga 500 Y$ más 100 por cada día seguido, con tope de 1.500 Y$. Se puede cobrar cada 20 h; la racha se pierde si pasan más de 48 h.
- IRPF: los premios por nivel y la ganancia neta diaria del casino llevan retención. En el casino se retiene sobre premios menos apuestas del día (hora canaria) y la retención se recalcula en cada tirada o mano: si las pérdidas posteriores bajan la ganancia, la cuenta del Estado devuelve la diferencia. Es la regla del art. 33.5.d LIRPF (las pérdidas de juego solo compensan ganancias de juego) aplicada por día en vez de por año. Se proyecta la renta anual con los ingresos de los últimos 30 días (10 Y$ = 1 €) y se aplican la escala estatal (art. 63.1 LIRPF) y la de Canarias (Ley 9/2025), descontando los mínimos personales (art. 57 LIRPF y art. 18 quater del D. Leg. 1/2009 de Canarias). El tipo es cuota / base con dos decimales (art. 86 RIRPF). Cada cobro muestra lo que se lleva Perro Sanxe. Cada ingreso queda en `economy_tax_records` para poder hacer más adelante la declaración anual.
- Cuenta del Estado: todo lo retenido entra en un monedero propio del servidor (`user_id = 0`), que empieza a 0 y tiene su propio libro de movimientos. Qué se hace con ese dinero está por decidir.
- Campaña de la Renta (`renta`): al cerrar cada semana (lunes 00:00, hora canaria) se recalcula la retención del casino sobre el neto de la semana, con las pérdidas de un día compensando las ganancias de otro, y lo retenido de más sale a devolver. Nunca sale a pagar. Para cobrarlo hay que presentar la declaración: con `renta`, o con el mensaje efímero que aparece una vez por campaña al jugar con botones o slash en el casino (después solo queda una línea pequeña en el resultado). Al presentar, el bot publica una línea en el canal. Se guardan como máximo las 2 últimas semanas pendientes, sin fecha de caducidad; cuando aparece una tercera, la más antigua caduca y el dinero se queda en el Estado. El bot mantiene un evento de Discord por campaña, que va de la apertura al domingo siguiente; sin el permiso **Gestionar eventos** todo funciona igual, sin evento.
- Impuesto sobre el Patrimonio: cada lunes (cuando la tarea de 10 minutos ve la semana cerrada) se cobra a todos los monederos lo que pase de **70.000 Y$**, con los tipos reales de la escala estatal (art. 30 de la Ley 19/1991, del 0,2 % al 3,5 %). Canarias no tiene tarifa propia ni bonificación, y su mínimo exento es de 700.000 € (art. 29 del D. Leg. 1/2009); en el bot ese mínimo son 70.000 Y$ y los tramos se escalan igual (1 Y$ = 10 € solo en este impuesto). El ejercicio dura una semana, como la renta: cada lunes se paga la cuota anual entera sobre el saldo de ese momento. Ejemplos: 100.000 Y$ pagan 73; 200.000 pagan 819; 1.000.000 pagan 15.435. La primera semana tras desplegarlo no se cobra, para que nadie pague por sorpresa. El bot anuncia en `#chat-general` (o el canal del sistema) quién ha pagado, `saldo` avisa de lo que te tocaría y lo cobrado va al Estado y cuenta en `hacienda`. Las cuentas del Estado y de las ONGs no pagan. No se aplica el límite conjunto con el IRPF (art. 31).
- Impuesto al consumo: el **IGIC**, no el IVA, porque el servidor es canario. Tipo general del 7 % (art. 27 de la Ley 4/2012 de Canarias). Ya está la regla (`taxes.igic`); se cobrará en cuanto haya tienda o cualquier compra.
- `donar [ong] [cantidad]`: donativos a cuatro ONGs de broma que dicen servir a una causa y hacen lo contrario (🧨 Fundación Desmontando España, 🤝 Asociación Ayuda al Ayudante, 🛥️ Mares Limpios Sociedad Limitada y 🍽️ Fundación Cubiertos de Plata contra el Hambre). Sin argumentos enseña lo que dicen, lo que hacen y cuánto llevan recaudado. El dinero se queda en la cuenta de la ONG (`user_id` negativo) y no vuelve. A cambio desgrava como el art. 19.1 de la Ley 49/2002: 80 % de los primeros 2.500 Y$ donados en la semana (250 €) y 40 % del resto, con la base limitada al 10 % de la renta de la semana (art. 69.1 LIRPF) y sin pasar del IRPF pagado esa semana. La deducción sale a devolver en la renta del lunes junto con lo del casino; si esa semana no pagaste IRPF, no recuperas nada. Los donativos no pagan IGIC (art. 4 de la Ley 20/1991) ni Donaciones (quien recibe es una entidad). El resultado es público y en `/donar` lleva el aviso de la Renta.
- `hacienda`: muestra a cualquiera el saldo del Estado, lo recaudado este año y desde siempre (IRPF y Patrimonio), los 5 que más han pagado y qué impuestos hay.
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

- `slots [cantidad]` abre una máquina propia con botones que solo pulsa su dueño: 🎰 Tirar, 🔁 Auto ×10, ⚡ Turbo, ½, ×2, 💰 All-in y 📋 Premios (en privado). Apuesta por defecto, 100 Y$.
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

### 6 ter.5. Crash

- `crash [cantidad] [auto]` abre la mesa de Crash del canal o entra en la ronda que esté embarcando. La mesa es compartida: una ronda por canal y cualquiera se sube. `.crash 500 2x` entra con 500 Y$ y auto-retiro en 2x; el orden de los dos argumentos da igual y `no` quita el auto-retiro. Si el cohete está en el aire, quien escribe `crash` entra en la ronda siguiente.
- Embarque: 10 s la primera ronda y 7 s las siguientes, con cuenta atrás que Discord mueve solo (marca de tiempo relativa, sin editar el mensaje). Botones: 🚀 Entrar (con tu ficha), ½, ×2, 💰 All-in y 🎯 Auto (formulario). La ficha y el auto-retiro de cada miembro se recuerdan entre rondas, en memoria. Si un embarque termina sin nadie, la mesa se cierra.
- Vuelo: el multiplicador es e^(k·t^1,4): empieza lento y acelera, para que la tensión dure donde se decide casi todo (1,5x a los 5,5 s, 2x a los 8 s, 5x a los 15 s, 10x a los 19 s, 100x a los 31 s) y tiene tope en 1.000x. 💸 Retirar cobra apuesta × multiplicador del instante en que llega la pulsación. El auto-retiro cobra justo su objetivo aunque el bot edite a saltos. Si ya no queda nadie dentro, el cohete salta directo a su punto de explosión.
- Punto de explosión: se sortea al abrir el embarque con `secrets` y P(llegar a x) = 0,99 / x. Retirarse siempre en el mismo multiplicador devuelve el 99 % de media, sea cual sea; el 1 % de las rondas explota en 1,00x. Una prueba lo comprueba con 200.000 puntos.
- Mensaje: durante el vuelo, texto con el multiplicador en grande, una estela que sube (un bloque por segundo) y la lista de pasajeros. Una edición por segundo como mucho, sin encadenar una sobre otra. Al explotar, una gráfica PNG (~15 KB, ~60 ms de CPU) con la curva, un punto por cada retirada con su nombre y una estrella en la explosión, más la tira de las últimas 10 rondas. Si otros mensajes han enterrado la mesa, la ronda siguiente se manda abajo y la vieja pierde los botones.
- Dinero: la apuesta se cobra al entrar (`place_bet`) y se paga al retirarse o al explotar (`pay_winnings`, con 0 si explota). Fiscalmente es juego, como el resto del casino (art. 33.1 y 33.5.d LIRPF). El resultado de la ronda dice lo que Perro Sanxe retiene o devuelve a cada uno y quien se retira a mano lo ve además en privado. Si el bot se apaga de forma ordenada, en embarque devuelve lo apostado y en vuelo retira a todos en el multiplicador del momento.
- Logros: 24 en la categoría 🚀 Crash (rondas, retiradas, 2x a 1.000x, auto-retiro, por los pelos, el último en saltar, ni despegó, la avaricia rompe el saco, rondas de 100x, rondas con 3 y 6 personas…).

### 6 ter.6. Minas

- `minas [cantidad] [minas]` cobra y abre un tablero de 5×5 propio que solo pulsa su dueño. Minas: de 1 a 23 (por defecto, las de la última vez o 2). Apuesta por defecto, 100 Y$.
- La primera casilla siempre es buena, como en el Buscaminas de Windows: las minas se colocan con `secrets` entre las otras 24 justo después del primer clic y desde ahí no se mueven. Como no tiene riesgo, paga ×1 (devuelve la apuesta). Mediana de casillas buenas antes de explotar: 13 con 1 mina, 7 con 2, 5 con 3 y 3 con 5.
- Cada casilla buena después sube el multiplicador; 💰 Cobrar se lleva apuesta × multiplicador y una 💣 lo pierde todo. 🎲 Al azar destapa una casilla cualquiera.
- Multiplicador tras k casillas (contando la segura) con m minas: 0,99 × C(24, k − 1) / C(24 − m, k − 1), con fracciones exactas y tope en ×10.000. Cobrar en cualquier momento desde la segunda casilla devuelve el 99 % de media, y para la misma casilla más minas pagan más (con 2 minas la segunda paga ×1,08; con 10, ×1,69; con 23, ×23,76). Una prueba lo verifica para cada número de minas y cada k.
- Al terminar, un menú elige de 1 a 23 minas y enseña lo que paga cada opción (segunda casilla, quinta y tablero entero), además de 🔁 Jugar, ½, ×2 y 💰 All-in.
- Progreso: el texto cuenta las casillas (💎 7/23), dice el multiplicador y lo que sumaría la siguiente casilla con su probabilidad, celebra las casillas 3, 5, 7, 10, 15 y 20, el medio tablero y la última buena, y avisa al batir el récord personal de casillas en una partida. El récord sale de la estadística de logros `mines_streak_max`, así que sobrevive a los reinicios. Al explotar dice hasta dónde llegaste y cuánto te ibas a llevar.
- Mensaje: componentes nuevos de Discord (`LayoutView`), un bloque con el texto y las 25 casillas como botones, una fila de botones y el menú de minas: los 40 componentes que admite un mensaje. Sin imágenes: cada clic es una edición instantánea.
- Dinero: la apuesta se cobra al empezar (`place_bet`) y se paga al cobrar o al explotar (`pay_winnings`, con 0 si explota). Fiscalidad del juego, con la línea de Perro Sanxe en el resultado. Si el tablero caduca (3 min) o el bot se apaga con una partida a medias, se cobra sola; sin casillas destapadas, se devuelve la apuesta.
- Los cobros de ×25 o más se anuncian en el canal.
- Logros: 25 en la categoría 💣 Minas (partidas, diamantes, cobros, minas pisadas, pisar justo después de la segura, tan cerca, ×5 a ×1.000, 10, 15 y 20 casillas en una partida, ganar con 23 minas, limpiar el tablero, 🎲…) y *Todoterreno* en 💰 Casino por jugar a los cinco juegos.

### 6 ter.7. Pachinko

- `pachinko [cantidad]` abre una máquina propia con botones que solo pulsa su dueño: 🎯 Lanzar, 🔁 Ráfaga ×5, ⚡ Turbo, ½, ×2, 💰 All-in y 📋 Premios (en privado). Apuesta por defecto, 100 Y$; mínimo, 10 Y$ (una bola tiene que valer al menos 1 Y$).
- Cada tanda lanza 10 bolas. Cada una rebota en 10 filas de clavos a cara o cruz y cae en uno de 11 bolsillos (tablero de Galton): ×10 bolas en las esquinas (1 de cada 1.024), ×3 en los siguientes, ×1, OUT y la ranura START en el centro (24,6 %). Una bola vale la apuesta entre 10 y el pago se redondea una vez por tanda.
- Cada bola en START gana una tirada del sorteo de la pantalla, con reserva de 4 como en las máquinas reales (保留); las que entran con la reserva llena se pierden. Atari (tres iguales): 1 de cada 40 tiradas, +30 bolas por premio gordo. Con número par paga uno; con impar es un **RUSH** que encadena premios con 3/5 de seguir (hasta 10); con el 7, **SUPER RUSH** con 4/5 (hasta 15).
- Reach: cuando no toca, 1 de cada 6 tiradas enseña dos números iguales y el del centro frena y para al lado. Se decide después del resultado y no cambia la probabilidad; la tabla de premios lo dice.
- Números (pruebas con fracciones exactas y una simulación): devuelve el 94,6 % (57,6 % los bolsillos y 37,0 % los ataris), un atari cada ~17 tandas y 2,1 premios gordos de media por atari. Casi todas las tandas devuelven algo, pero solo una de cada diez llega a lo apostado.
- Animación: GIF por tanda (130-330 KB, ~0,5 s de CPU fuera del event loop) con bombillas que persiguen por el borde y el marco de la pantalla, rótulo de neón, molinillos, bolas rebotando y la pantalla jugando la reserva mientras siguen cayendo. En el reach las bombillas corren y sale el cartel; en el atari la pantalla se pone dorada, las bombillas hacen arcoíris, llueven bolas y sube el contador del rush. El último fotograma es el PNG final con las bolas contadas en cada bolsillo. Turbo y Ráfaga mandan solo el PNG. Bolsillos distinguibles por forma (estrella, rombo, círculo, aspa, tulipán) y texto.
- Dinero: `settle_bet` cobra, paga y ajusta el IRPF en una transacción. Fiscalidad del juego (art. 33.1 y 33.5.d LIRPF), línea de Perro Sanxe en el resultado, aviso de la Renta en cada botón.
- Los SUPER RUSH, los rush de 5 premios o más y las ganancias de ×20 la apuesta se anuncian en el canal.
- Logros: 30 en la categoría 🌸 Pachinko (tandas, bolas por START, reach, reach perdidos, ataris, rush, 7️⃣7️⃣7️⃣, renchan de 3 a 15, esquinas, reserva llena, bolas al limbo, tanda en blanco, premios grandes, sesión larga, Ráfaga, turbo y de madrugada) y *Ludópata integral* en 💰 Casino por jugar a los seis juegos.

### 6 ter.8. Criterios de aceptación

- Ningún saldo puede quedar negativo ni gastarse dos veces, aunque se pulsen botones a la vez.
- Solo el dueño de una mesa puede apostar en ella.
- Una apuesta ilegal en el tapete se rechaza con un ejemplo de formato válido.
- Al salir el bot de un servidor se borra su economía.

### 6 ter.9. Logros

- `logros [miembro]`: resumen (logros conseguidos, puntos, por categoría, los 5 últimos, los 3 más cercanos y el más raro del servidor), un menú con cada categoría y un botón 🏆 Ranking por puntos. Solo quien abre la vista puede cambiar de página.
- 344 logros en 14 categorías: 💬 Chat, 🗓️ Horarios y fechas, 🎙️ Voz, ❤️ Social, 📈 Niveles, 🎡 Ruleta, 🃏 Blackjack, 💰 Casino, 🎰 Tragaperras, 🚀 Crash, 💣 Minas, 🌸 Pachinko, 🏛️ Economía y Hacienda y 🏆 Coleccionista. Una categoría puede marcarse `upcoming` ("próximamente") mientras su juego no exista: sus logros se ven pero no se pueden conseguir ni cuentan para el total.
- Rarezas y premio bruto: ▫️ Común 50 Y$ (10 puntos), 🔹 Raro 200 Y$ (25), 💠 Épico 750 Y$ (50), 🌟 Legendario 2.500 Y$ (100), 👑 Mítico 10.000 Y$ (250). Los emojis tienen formas distintas para que se distingan sin depender del color.
- Fiscalidad: el premio es una ganancia patrimonial por un concurso del servidor (art. 33.1 LIRPF), sujeta a retención como los premios (art. 75.2.c RIRPF). Se cobra con `pay_income`: retención de IRPF que va a la cuenta del Estado y línea de Perro Sanxe en el aviso.
- 35 logros son secretos: se ven como `???` (con el porcentaje del servidor que lo tiene) hasta conseguirlos.
- Qué cuenta: mensajes (y propiedades sin guardar el texto: hora, largo, mayúsculas, enlaces, adjuntos, respuestas, risas…), minutos en voz con al menos otra persona sin ensordecer (fuera del canal AFK; también minutos silenciado, compartiendo pantalla, con cámara, de madrugada, solo en el canal y la sesión seguida más larga), reacciones dadas y recibidas (una por persona y mensaje), felicitaciones de cumpleaños, bienvenidas dadas con el botón 👋, Patrimonio pagado, donativos, nivel y racha de días, cada tirada de ruleta y tragaperras y mano de blackjack, lo apostado, el mayor premio y la mayor pérdida, all-in, rachas de casino entre juegos, IMV, IRPF pagado, renta presentada y saldo máximo.
- Escrituras: mensajes, reacciones y voz se acumulan en memoria y se guardan una vez por minuto, una transacción por servidor. Los juegos, el IMV, la renta y las subidas de nivel se guardan en el momento. Las rachas de casino y las sesiones de voz viven en memoria y se cortan con un reinicio.
- La primera vez que el bot ve a alguien tras arrancar, recupera como máximos sus mensajes del historial importado y su nivel actual. Por eso, en el primer mensaje tras desplegar, cada veterano desbloquea y cobra lo que ya tenía.
- Se anuncia en el canal donde se consiguió (en voz, el chat del canal de voz; si no se sabe, el canal del sistema). Con más de 8 a la vez, el aviso los resume.
- Un logro puede llevar un texto propio (`story`) que sale en su aviso. Como cada logro se desbloquea una sola vez, sirve de gancho de "la primera vez que…". Lo usa *Bienvenido a España* (secreto): la primera vez que a alguien le retienen IRPF, venga de donde venga el dinero, Perro Sanxe le explica que ha pasado del mínimo personal (55.500 Y$ al año, unos 4.561 Y$ cada 30 días), que desde ahora se lleva parte de cada ganancia y que lo retenido de más en el casino vuelve con la `renta`. Al entrar al servidor no se dice nada de esto. Quien ya había pagado IRPF antes de este cambio lo recibe con su siguiente acción.
- Al salir el bot de un servidor se borran sus logros.
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
| `bienv [gif] [canal]` | GIF y canal de la bienvenida; sin argumentos, enseña los actuales. Responde con una vista previa (en `/`, solo la ves tú). Ver sección 3.3. |

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

El canal y el GIF de bienvenida se guardan por servidor en SQLite, igual que los datos del sistema de niveles.

Toda configuración de servidor debe estar asociada al ID de ese servidor. El bot no debe usar valores de un servidor como valores implícitos para otro. Los datos de progresión se limitarán a lo necesario para operar las funciones descritas y deben poder eliminarse si el bot deja de prestar servicio en un servidor.

## 8. Orden de implementación

1. **Bienvenida y despedida:** frases editables, GIF y canal configurables con `bienv`, vuelta de antiguos miembros y botón de saludo con logros. (Implementado.) Pendiente: activar o desactivar cada mensaje y separar el canal de despedida.
2. **Preparación de niveles:** importar y guardar agregados de mensajes históricos y contar actividad nueva. (Implementado.)
3. **Niveles:** conversión de historial en XP, XP por mensajes nuevos, cooldown, comandos `level`/`top` y configuración. (Implementado.)
4. **Música:** reproducción y controles de cola con `yt-dlp` y `ffmpeg`. (Implementado.)
5. **Imagen:** comando `magik` con seam carving y los 108 efectos de Dank Memer. (Implementado.)
6. **Sonidos de entrada:** clip personal de hasta 3 s al entrar a voz. (Implementado.)
7. **Economía y casino:** yapdollars, `daily`, `saldo`, ruleta americana, blackjack, tragaperras, Crash, Minas y pachinko. (Implementado.) Siguientes juegos y usos de la moneda pendientes.
8. **Diversión:** `babel`, traducción en cadena por 99 idiomas de frases, apodos y nombres de canal. (Implementado.)
9. **Logros:** 313 logros con premios en yapdollars, `logros` y ranking. (Implementado.)

Cada fase debe incluir pruebas, permisos mínimos, documentación de uso y los cambios pertinentes a la configuración. Una función se considera terminada únicamente cuando cumple sus criterios de aceptación; aparecer en esta lista no significa que ya esté implementada.

## 9. Decisiones pendientes

Estas decisiones no impiden documentar el alcance, pero deben resolverse antes de cerrar la implementación correspondiente:

- Si la bienvenida y la despedida podrán desactivarse o ir a canales distintos.
- Si se ofrecerá una herramienta administrativa para reiniciar la progresión (los comandos de administración actuales no tocan los niveles).
- Si moderadores podrán controlar música iniciada por otros miembros, o si el control seguirá limitado a "cualquiera en el mismo canal de voz" (comportamiento actual).

Las decisiones pendientes no bloquean las funciones ya implementadas, pero deben resolverse antes de cerrar la funcionalidad correspondiente.
