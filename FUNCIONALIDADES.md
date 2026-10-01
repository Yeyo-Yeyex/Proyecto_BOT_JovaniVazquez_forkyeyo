# Especificación funcional del bot

Este documento describe las funcionalidades actuales y futuras y cómo debe comportarse el bot desde el punto de vista de sus usuarios y administradores. Complementa la [Biblia del proyecto](./Biblia.txt), que define las normas técnicas y de calidad.

La bienvenida/despedida descrita en la sección 3 y los comandos `/ping`, `/nivel` y `/ranking` están implementados. Las demás funciones son objetivos futuros salvo que se indique lo contrario.

## 1. Objetivo

Crear un bot de Discord en español que aporte a los servidores:

- Mensajes de bienvenida y despedida.
- Progresión de niveles basada en la participación mediante mensajes.
- Reproducción y control de música en canales de voz.

La interfaz usa comandos de aplicación (`/`) y sus equivalentes de texto con el prefijo `º` (además del prefijo configurable), junto con botones o menús de Discord cuando corresponda. Los mensajes automáticos y avisos se enviarán en los canales configurados.

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

### 4.2. Comandos previstos

- `/nivel [miembro]` y `ºnivel [miembro]`: muestran el nivel, experiencia actual y progreso al siguiente nivel del miembro indicado o de quien ejecuta el comando.
- `/ranking [página]` y `ºranking [página]`: muestran los miembros con más experiencia del servidor, ordenados de forma estable y con paginación.
- `/ping` y `ºping`: comprueban la latencia del bot.
- `/ayuda` y `ºayuda` (alias `ºhelp`): listan dinámicamente los comandos slash y de texto, sus argumentos y alias.

Los comandos slash y de texto comparten la misma lógica de negocio; solamente cambia el adaptador usado para responder a Discord. El prefijo `º` está siempre activo y se combina con `COMMAND_PREFIX` (por defecto `!`). Para que Discord entregue mensajes a los comandos de texto, también debe habilitarse **Message Content Intent** en el portal de desarrolladores.

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

Cada comando existe en tres formas equivalentes que comparten exactamente la misma lógica interna (no hay duplicación de reglas de negocio): el comando de aplicación completo (`/reproducir`), su alias corto de aplicación registrado como un segundo slash command independiente (`/p`), y un comando de texto clásico con el prefijo `º` (o el configurado en `COMMAND_PREFIX`, `!` por defecto) que admite tanto el nombre completo como los alias corto e inglés (`ºreproducir`, `ºp`, `ºplay`). Los tres puntos de entrada delegan en el mismo método `_*_impl`, que recibe un `MusicResponder` — una abstracción (`InteractionResponder`/`ContextResponder` en `bot.cogs.music`) que oculta si la petición vino de una `discord.Interaction` o de un mensaje de texto.

- `/reproducir consulta` (`/p`; texto `ºreproducir`/`ºp`/`ºplay`): busca en YouTube (o resuelve un enlace directo) mediante `yt-dlp` y reproduce el resultado; conecta al bot al canal de voz del miembro si aún no estaba conectado. Si ya hay una pista sonando, la añade al final de la cola.
- `/pausar` (`/pausa`; texto `ºpausar`/`ºpausa`/`ºpause`) y `/reanudar` (`/rs`; texto `ºreanudar`/`ºrs`/`ºresume`): controlan la reproducción actual.
- `/saltar` (`/s`; texto `ºsaltar`/`ºs`/`ºskip`): detiene la pista en curso; la cola continúa automáticamente con la siguiente.
- `/cola` (`/q`; texto `ºcola`/`ºq`/`ºqueue`): muestra la pista actual y hasta diez pistas siguientes (con el resto resumido en un contador).
- `/quitar posición` (`/rm`; texto `ºquitar`/`ºrm`/`ºremove`): elimina una pista de la cola por su posición (1 = la siguiente).
- `/limpiar` (`/cl`; texto `ºlimpiar`/`ºcl`/`ºclear`): vacía la cola sin afectar a la pista en curso.
- `/parar` (`/stop`; texto `ºparar`/`ºstop`): detiene la reproducción, vacía la cola y desconecta al bot del canal de voz.
- `/volumen valor` (`/vol`; texto `ºvolumen`/`ºvol`/`ºvolume`): cambia el volumen (1-200 %), incluso con una pista ya sonando.

La cola y el reproductor son independientes por servidor (`GuildMusicState` en `bot.cogs.music`). Las acciones de control (todas salvo `/reproducir`, que además puede conectar al bot, y `/cola`, de solo lectura) exigen que quien las use esté conectado al mismo canal de voz que el bot.

Los comandos de texto con prefijo requieren el intent privilegiado **Message Content** habilitado en el portal de desarrolladores de Discord (además del ya requerido **Server Members**); sin él, el bot no puede leer el contenido de los mensajes y esos comandos no se dispararán (los comandos de aplicación `/` no se ven afectados).

### 5.2. Comportamiento y límites

- Se informa con un mensaje claro si la consulta no se puede resolver, la pista dura demasiado, la cola está llena o el miembro no está en el canal de voz adecuado.
- Al terminar una pista (o al fallar su reproducción), se continúa automáticamente con la siguiente de la cola; `/parar` es la única acción que corta ese encadenamiento.
- Duración máxima por pista: 30 minutos (`MAX_TRACK_DURATION_SECONDS`); se rechazan también los directos, al no tener duración conocida. Tamaño máximo de cola: 50 pistas por servidor (`MAX_QUEUE_SIZE`).
- El bot abandona el canal de voz automáticamente si se queda sin oyentes humanos, o tras 5 minutos de inactividad sin pistas en cola (`IDLE_DISCONNECT_SECONDS`).
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

## 6. Persistencia y aislamiento por servidor

La bienvenida/despedida usa actualmente el canal `#chat-general` y textos definidos por el bot, por lo que no necesita configuración persistida. Si se añade personalización, sus preferencias deberán persistir por servidor. Los datos del sistema de niveles ya se guardan en SQLite.

Toda configuración de servidor debe estar asociada al ID de ese servidor. El bot no debe usar valores de un servidor como valores implícitos para otro. Los datos de progresión se limitarán a lo necesario para operar las funciones descritas y deben poder eliminarse si el bot deja de prestar servicio en un servidor.

## 7. Orden de implementación

1. **Bienvenida y despedida:** eventos y mensajes básicos en `#chat-general` (implementado); configuración por servidor y mensajes personalizables pendientes.
2. **Preparación de niveles:** importar y guardar agregados de mensajes históricos y contar actividad nueva. (Implementado.)
3. **Niveles:** conversión de historial en XP, XP por mensajes nuevos, cooldown, comandos de nivel/ranking y configuración. (Implementado.)
4. **Música:** reproducción y controles de cola con `yt-dlp` y `ffmpeg`. (Implementado.)

Cada fase debe incluir pruebas, permisos mínimos, documentación de uso y los cambios pertinentes a la configuración. Una función se considera terminada únicamente cuando cumple sus criterios de aceptación; aparecer en esta lista no significa que ya esté implementada.

## 8. Decisiones pendientes

Estas decisiones no impiden documentar el alcance, pero deben resolverse antes de cerrar la implementación correspondiente:

- Configuración por servidor y plantillas personalizables para bienvenida/despedida.
- Si se ofrecerá una herramienta administrativa para reiniciar la progresión.
- Si moderadores podrán controlar música iniciada por otros miembros, o si el control seguirá limitado a "cualquiera en el mismo canal de voz" (comportamiento actual).

Las decisiones pendientes no bloquean las funciones ya implementadas, pero deben resolverse antes de cerrar la funcionalidad correspondiente.
