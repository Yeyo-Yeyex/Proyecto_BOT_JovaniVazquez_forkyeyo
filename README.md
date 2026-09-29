# Bot Jovani Vázquez

Bot de Discord escrito en Python, con arquitectura modular basada en
`discord.py`. Las normas de estructura, límites y documentación del
proyecto están definidas en [Biblia.txt](./Biblia.txt); léelo antes de
añadir funcionalidades nuevas. El alcance funcional previsto se describe
en [FUNCIONALIDADES.md](./FUNCIONALIDADES.md).

## Requisitos

- Python 3.12 o superior.
- Una aplicación y un token de bot creados en el
  [portal de desarrolladores de Discord](https://discord.com/developers/applications).
- `ffmpeg` instalado en el sistema y accesible en el `PATH`, necesario para
  reproducir audio en canales de voz (comandos de música). No se instala
  con `pip`; en Debian/Ubuntu basta con `sudo apt install ffmpeg`.
- Las dependencias de Python `PyNaCl` y `davey` (cifrado de voz) se
  instalan automáticamente con `pip install -e .`; si el bot lanza
  `RuntimeError: davey library needed in order to use voice` al intentar
  reproducir música, reinstala las dependencias (`pip install -e ".[dev]"`).

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

> Si tu proyecto vive en un sistema de archivos montado con la opción
> `noexec` (por ejemplo, algunas particiones NTFS montadas manualmente),
> la creación del entorno virtual y la ejecución de dependencias
> compiladas fallarán. En ese caso, crea el entorno virtual fuera de esa
> ruta, por ejemplo en `~/.venvs/bot-jovani-vazquez`.

## Configuración

1. Copia `.env.example` a `.env`.
2. Completa `DISCORD_TOKEN` con el token de tu bot. Nunca lo subas al
   repositorio.
3. Ajusta `LOG_LEVEL` y `COMMAND_PREFIX` si lo necesitas (ambos son
   opcionales). `COMMAND_PREFIX` afecta solo a los comandos de texto
   clásicos (música); el prefijo `º` está siempre disponible además del
   configurado, no lo sustituye.

El bot carga automáticamente el archivo `.env` (si existe) al arrancar,
mediante `python-dotenv`. En producción no es necesario un archivo
`.env`: basta con exportar las variables en el entorno de despliegue.

## Ejecución

```bash
python -m bot
```

## Funcionalidad actual

- Comando de aplicación `/ping`: responde con la latencia de la conexión
  con Discord, de forma efímera.
- `/nivel [miembro]`: consulta el nivel y el progreso de un miembro.
- `/ranking [página]`: muestra la clasificación del servidor.
- Los avisos de subida de nivel se publican en el canal donde el mensaje
  concedió el nivel.
- Al entrar alguien, el bot pregunta **¿QUIÉN ERES?** y adjunta el vídeo
  `src/bot/assets/bienvenida.mp4` en `#chat-general`. Al salir, publica una
  despedida con una frase aleatoria tomada de
  `src/bot/assets/despedidas.txt` en ese mismo canal.
- El ranking resuelve nombres visibles del servidor incluso para miembros
  que todavía no estén en la caché local del bot, y lo presenta en un embed
  con podio, progreso visual y paginación.
- Música en canales de voz, sencilla y por servidor. Cada comando funciona
  de tres formas equivalentes: como comando de aplicación completo
  (`/reproducir`), como su alias corto de aplicación (`/p`) y como comando
  de texto clásico con el prefijo `º` (o el prefijo configurado, `!` por
  defecto), aceptando también el nombre completo y el alias en inglés
  (`ºreproducir`, `ºp`, `ºplay`):
  - `/reproducir <consulta>` (`/p`, `ºreproducir`/`ºp`/`ºplay`): busca o
    resuelve un enlace (vía `yt-dlp`) y lo reproduce; si ya hay algo
    sonando, lo añade a la cola.
  - `/pausar` (`/pausa`, `ºpausar`/`ºpausa`/`ºpause`), `/reanudar` (`/rs`,
    `ºreanudar`/`ºrs`/`ºresume`), `/saltar` (`/s`, `ºsaltar`/`ºs`/`ºskip`),
    `/parar` (`/stop`, `ºparar`/`ºstop`): controlan la reproducción.
    `/parar` además vacía la cola y desconecta al bot del canal de voz.
  - `/cola` (`/q`, `ºcola`/`ºq`/`ºqueue`): muestra la pista actual y hasta
    diez pistas pendientes.
  - `/quitar <posición>` (`/rm`, `ºquitar`/`ºrm`/`ºremove`): elimina una
    pista concreta de la cola.
  - `/limpiar` (`/cl`, `ºlimpiar`/`ºcl`/`ºclear`): vacía la cola sin afectar
    a la pista en curso.
  - `/volumen <1-200>` (`/vol`, `ºvolumen`/`ºvol`/`ºvolume`): ajusta el
    volumen, incluso con música en marcha.
  - Solo se puede controlar la reproducción desde el mismo canal de voz en
    el que está el bot. Las pistas están limitadas a 30 minutos y la cola,
    a 50 elementos por servidor. El bot abandona el canal automáticamente
    si se queda sin oyentes humanos o tras 5 minutos de inactividad.

La importación histórica de este servidor ya se completó y la activación de
niveles ya se ejecutó. Los comandos temporales de importación/activación y los
comandos de configuración y estado de niveles no están disponibles. Los
recuentos y niveles existentes permanecen guardados. Los mensajes nuevos dan
15–25 XP aleatorios como máximo una vez cada 60 segundos por miembro y servidor.

Los datos persistentes viven en `.data/message_stats.sqlite3`, localmente en
la máquina de ejecución y excluidos de Git; inclúyelos en las copias de
seguridad del despliegue.

Tras actualizar el código, reinicia el bot para que sincronice y retire los
comandos antiguos de Discord.

Para recibir eventos de entrada y salida, habilita **Server Members Intent**
en la sección *Bot* del [portal de desarrolladores de Discord](https://discord.com/developers/applications).
Para que funcionen los comandos de texto con prefijo `º` (o el prefijo
configurado), habilita también **Message Content Intent** en esa misma
sección: sin él, el bot no puede leer el contenido de mensajes normales y
esos comandos simplemente no se dispararán (los comandos de aplicación `/`
no lo necesitan). El bot también necesita ver `#chat-general` y tener permiso
para enviar mensajes y adjuntar archivos, además de permisos de **Conectar**
y **Hablar** en los canales de voz donde se vaya a usar la música.

Las frases de despedida se editan directamente en
`src/bot/assets/despedidas.txt`, una por línea (las líneas vacías y las que
empiezan por `#` se ignoran). El archivo se relee en cada despedida, así que
los cambios se aplican sin reiniciar el bot. Si el archivo falta o queda
vacío, se usa una frase de reserva para no dejar la despedida sin texto.

## Pruebas y calidad

```bash
pytest
ruff check .
```

Las pruebas no requieren un token real ni conexión a Discord: se aíslan
mediante dobles de prueba (`unittest.mock`).

## Estructura del proyecto

```text
src/bot/
├── __main__.py        # Punto de entrada: python -m bot
├── app.py              # Construcción y ciclo de vida del cliente
├── config.py           # Lectura y validación de configuración
├── logging_config.py   # Configuración centralizada de logging
├── cogs/                # Comandos y eventos agrupados por dominio
│   ├── general.py       # Comandos generales (p. ej. /ping)
│   ├── message_stats.py # Recuento de mensajes y niveles
│   ├── welcome.py      # Bienvenidas y despedidas
│   └── music.py         # Comandos de música y control por servidor
├── services/            # Lógica de negocio pura, sin discord.py
│   ├── levels.py        # Cálculo de niveles y progreso
│   ├── music.py          # Pistas, cola y límites (testable sin red)
│   └── music_source.py   # Extracción de audio con yt-dlp (bloqueante)
└── assets/
    ├── bienvenida.mp4  # Vídeo adjunto al mensaje de bienvenida
    └── despedidas.txt  # Frases de despedida, editables sin tocar código
```

Consulta [Biblia.txt](./Biblia.txt) para el detalle completo de la
estructura de referencia, los límites entre capas y las normas de
seguridad, rendimiento y documentación.
