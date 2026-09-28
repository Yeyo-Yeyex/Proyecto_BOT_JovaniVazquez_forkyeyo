# Especificación funcional del bot

Este documento describe las funcionalidades que se quieren construir y cómo debe comportarse el bot desde el punto de vista de sus usuarios y administradores. Complementa la [Biblia del proyecto](./Biblia.txt), que define las normas técnicas y de calidad.

Las funciones de este documento son objetivos, no funcionalidades ya disponibles, salvo la preparación del recuento de mensajes descrita en la sección 4.0. También está disponible el comando `/ping`.

## 1. Objetivo

Crear un bot de Discord en español que aporte a los servidores:

- Mensajes de bienvenida y despedida.
- Progresión de niveles basada en la participación mediante mensajes.
- Reproducción y control de música en canales de voz.

La interfaz principal serán comandos de aplicación (`/`) y, cuando corresponda, botones o menús de Discord. Los mensajes automáticos y avisos se enviarán en los canales configurados.

## 2. Usuarios y permisos

- **Miembro:** puede usar los comandos de nivel y música disponibles para cualquier usuario del servidor.
- **Moderador o administrador:** puede configurar la bienvenida/despedida y modificar ajustes del sistema de niveles.
- **Bot:** solo necesita permisos e intents relacionados con las funciones activadas en cada servidor.

Las comprobaciones de permisos deben hacerse en el servidor en cada operación protegida; ocultar un comando o botón no cuenta como autorización.

## 3. Bienvenida y despedida

### 3.1. Bienvenida

Cuando una persona se una a un servidor que tenga la función habilitada, el bot deberá:

1. Enviar un mensaje al canal de bienvenida configurado.
2. Mencionar al nuevo miembro de forma intencional y segura.
3. Permitir que el servidor personalice el texto con, como mínimo, el nombre visible del miembro y del servidor.
4. No enviar nada si la función no está configurada, el canal ya no existe o el bot no tiene permisos para escribir en él; registrar el problema sin detener el bot.

### 3.2. Despedida

Cuando una persona abandone un servidor que tenga la función habilitada, el bot deberá enviar un mensaje al canal de despedidas configurado. El texto podrá personalizarse con el nombre disponible del miembro y del servidor. No se debe asumir que Discord seguirá proporcionando todos los datos del miembro después de que se haya ido.

### 3.3. Configuración

Los administradores podrán:

- Elegir por separado el canal de bienvenida y el de despedida.
- Activar o desactivar cada tipo de mensaje independientemente.
- Cambiar las plantillas de texto.
- Consultar la configuración actual.

Los mensajes deben limitar menciones accidentales a usuarios o roles. Si una plantilla incluye variables desconocidas o supera límites de Discord, la configuración debe rechazarse con un error comprensible, no fallar al procesar un evento.

### 3.4. Criterios de aceptación

- Un ingreso produce como máximo una bienvenida y una salida produce como máximo una despedida.
- Cada mensaje se envía únicamente al canal configurado para ese servidor.
- La configuración de un servidor no afecta a otros servidores.
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

- `/nivel [miembro]`: muestra el nivel, experiencia actual y progreso al siguiente nivel del miembro indicado o de quien ejecuta el comando.
- `/ranking [página]`: muestra los miembros con más experiencia del servidor, ordenados de forma estable y con paginación.

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

### 5.1. Comandos previstos

- `/reproducir consulta`: reproduce una búsqueda o fuente admitida por el proveedor que se elija.
- `/pausar` y `/reanudar`: controlan la reproducción actual.
- `/saltar`: pasa a la siguiente pista.
- `/cola`: muestra la pista actual y las siguientes, con paginación si hace falta.
- `/quitar posición`: elimina una pista de la cola, con autorización adecuada.
- `/limpiar`: vacía la cola con autorización adecuada.
- `/parar`: detiene la reproducción y desconecta al bot.
- `/volumen valor`: cambia el volumen dentro de un rango acotado.

La cola y el reproductor serán independientes por servidor. Como regla inicial, las acciones de música solo se permitirán a miembros conectados al mismo canal de voz que el bot. Los comandos que inician reproducción requerirán conexión a un canal de voz.

### 5.2. Comportamiento y límites

- Informar con claridad si la entrada no se puede reproducir, el bot no puede conectarse o el servidor no dispone de permisos suficientes.
- Al terminar una pista, reproducir la siguiente de la cola; si no quedan pistas, desconectarse tras un periodo de inactividad configurable o permanecer conectado según la decisión de implementación.
- Limitar duración y tamaño de cola para evitar consumo ilimitado de recursos.
- Validar URL, consulta y metadatos antes de mostrarlos o procesarlos; escapar contenido no confiable.
- Cerrar procesos, conexiones y tareas de reproducción al saltar, parar, desconectar o cerrar el bot.
- La cola en memoria puede perderse al reiniciar durante el MVP; no persistir audio ni historiales salvo que se defina un requisito separado.
- La fuente, librería de extracción/reproducción y requisitos de despliegue quedan pendientes de decisión técnica. Solo se admitirán fuentes compatibles con sus condiciones de uso y con las políticas de Discord; no se intentará eludir DRM, controles de acceso ni restricciones de proveedores.

### 5.3. Criterios de aceptación

- Dos servidores pueden reproducir música y mantener colas simultáneas sin interferirse.
- La reproducción y la cola avanzan correctamente al terminar, saltar, detener o fallar una pista.
- Los errores de conexión o de fuente no bloquean otros comandos ni dejan al bot en un estado irrecuperable.
- Usuarios no conectados al canal de voz adecuado reciben una respuesta clara y no alteran la reproducción.
- Límites de cola y duración se aplican antes de aceptar trabajo adicional.

## 6. Persistencia y aislamiento por servidor

Las preferencias de bienvenida/despedida y los datos del sistema de niveles deben persistir. La tecnología de almacenamiento se decidirá al implementar estas funciones y deberá seguir la separación de repositorios definida en `Biblia.txt`.

Toda configuración de servidor debe estar asociada al ID de ese servidor. El bot no debe usar valores de un servidor como valores implícitos para otro. Los datos de progresión se limitarán a lo necesario para operar las funciones descritas y deben poder eliminarse si el bot deja de prestar servicio en un servidor.

## 7. Orden de implementación

1. **Bienvenida y despedida:** eventos, configuración por servidor y mensajes personalizables.
2. **Preparación de niveles:** importar y guardar agregados de mensajes históricos y contar actividad nueva. (Implementado.)
3. **Niveles:** conversión de historial en XP, XP por mensajes nuevos, cooldown, comandos de nivel/ranking y configuración. (Implementado.)
4. **Música:** seleccionar fuente/proveedor y estrategia de despliegue antes de implementar el reproductor; después, reproducción y controles de cola.

Cada fase debe incluir pruebas, permisos mínimos, documentación de uso y los cambios pertinentes a la configuración. Una función se considera terminada únicamente cuando cumple sus criterios de aceptación; aparecer en esta lista no significa que ya esté implementada.

## 8. Decisiones pendientes

Estas decisiones no impiden documentar el alcance, pero deben resolverse antes de cerrar la implementación correspondiente:

- Plantillas y formato visual definitivos para bienvenida/despedida.
- Si se ofrecerá una herramienta administrativa para reiniciar la progresión.
- Fuente de audio admitida, comportamiento de búsqueda, límites de pista/cola y estrategia de despliegue.
- Si moderadores podrán controlar música iniciada por otros miembros o solo el miembro solicitante.

Las decisiones pendientes no bloquean las funciones ya implementadas, pero deben resolverse antes de cerrar la funcionalidad correspondiente.
