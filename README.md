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
   opcionales). `COMMAND_PREFIX` es el prefijo de los comandos de texto
   (por defecto `.`); con él se invocan los mismos comandos que con `/`.

El bot carga automáticamente el archivo `.env` (si existe) al arrancar,
mediante `python-dotenv`. En producción no es necesario un archivo
`.env`: basta con exportar las variables en el entorno de despliegue.

## Ejecución

```bash
python -m bot
```

## Funcionalidad actual

- Cada comando tiene **un único nombre corto, igual con `/` y con `.`**
  (`/play despacito` = `.play despacito`). Escribe `/help` o `.help` para ver
  la lista:

  | Categoría | Comandos |
  |---|---|
  | 🎵 Música | `play <consulta>` · `pause` · `resume` · `skip` · `stop` · `queue` · `remove <posicion>` · `clear` · `volume <1-200>` |
  | 🔔 Entradas | `entrada [archivo] [volumen] [borrar]` |
  | 🎨 Imagen | `magik [imagen] [miembro]` |
  | 📊 Niveles | `level [miembro]` · `top [pagina]` |
  | ⚙️ General | `ping` · `help` |

- `magik` deforma una imagen con *seam carving* (reescalado consciente del
  contenido, el mismo efecto que Dank Memer): en vez de estirar o recortar,
  elimina primero los caminos de píxeles menos importantes y vuelve a
  ampliar, así que las formas se "derriten". Usa, por orden: la imagen
  adjunta (en `.magik`, también la del mensaje al que respondes), el avatar
  del miembro indicado o el tuyo. Ejemplos: `/magik imagen:<archivo>`,
  `.magik @alguien`, o responde a una foto con `.magik`. Solo acepta
  adjuntos de Discord y avatares (nunca enlaces externos), hasta 8 MB, con
  5 segundos de espera entre usos por usuario. Respeta la rotación de las
  fotos de móvil. El bot necesita el permiso **Adjuntar archivos** en el
  canal; si falta, `magik` lo explica en lugar de quedarse colgado.
- Si un comando de texto falla (falta un argumento, un error inesperado…) el
  bot responde con un mensaje claro; los errores internos se guardan en el log.
- Los avisos de subida de nivel se publican en el canal donde el mensaje
  concedió el nivel.
- Al entrar alguien, el bot pregunta **¿QUIÉN ERES?** y adjunta el vídeo
  `src/bot/assets/bienvenida.mp4` en `#chat-general`. Al salir, publica una
  despedida con una frase aleatoria tomada de
  `src/bot/assets/despedidas.txt` en ese mismo canal.
- `top` resuelve nombres visibles del servidor incluso para miembros que
  todavía no estén en la caché local del bot, y lo presenta en un embed con
  podio, progreso visual y paginación.
- Música en canales de voz, sencilla y por servidor: `play` busca o resuelve
  un enlace (vía `yt-dlp`) y lo reproduce, o lo añade a la cola si ya suena
  algo; `stop` además vacía la cola y desconecta al bot. Solo se puede
  controlar la reproducción desde el mismo canal de voz en el que está el
  bot. Las pistas están limitadas a 30 minutos y la cola, a 50 elementos por
  servidor. El bot abandona el canal automáticamente si se queda sin oyentes
  humanos o tras 5 minutos de inactividad. Se conecta ensordecido para que
  Discord no le envíe el audio de los demás.
- Sonidos de entrada: cada miembro sube con `entrada` un audio de hasta 3 s
  que suena cuando entra a un canal de voz, con volumen ajustable (10-200 %).
  El bot entra, lo reproduce y se va; no suena si el bot ya está poniendo
  música. Los clips se guardan en `.data/entradas/` (mismo volumen Docker
  que la base de datos).

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
Para que funcionen los comandos de texto con prefijo `.` (o el prefijo
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

## Despliegue con Docker (NAS)

El bot corre bien en cualquier equipo x86_64 con Docker, por ejemplo un NAS
UGREEN DXP2800 (Intel N100): consume poca CPU y memoria y no necesita abrir
puertos, porque solo hace conexiones salientes a Discord y a las fuentes de
audio. La imagen incluye `ffmpeg` y `libopus`.

1. Lleva el proyecto al NAS (`git clone` por SSH, o copia la carpeta).
2. Crea el archivo de configuración y pon tu token:
   ```bash
   cp .env.example .env
   nano .env            # DISCORD_TOKEN=...
   chmod 600 .env
   ```
3. Construye y arranca en segundo plano:
   ```bash
   docker compose up -d --build     # o `docker-compose` si tu NAS solo trae esa versión
   docker compose logs -f           # ver el log; Ctrl+C solo sale del log
   ```
   Si prefieres la interfaz gráfica, la app Docker del NAS puede crear un
   proyecto a partir de este `docker-compose.yml`.

`restart: unless-stopped` hace que el bot arranque con el NAS y se levante
solo si falla; no se reinicia si lo paras a mano (`docker compose stop`). Con
un token inválido el contenedor se reiniciará en bucle: revisa el log.

**Actualizar el bot:** `git pull && docker compose up -d --build`.

**Si la música deja de funcionar**, casi siempre es que `yt-dlp` se ha quedado
anticuado (YouTube cambia a menudo). Reconstruye sin caché para traer la
última versión: `docker compose build --no-cache && docker compose up -d`.

**Datos y copia de seguridad:** niveles y estadísticas viven en el volumen
`bot-jovani-vazquez-data` (sobrevive a reconstrucciones y actualizaciones).
Para copiarlo:
```bash
docker run --rm -v bot-jovani-vazquez-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/bot-data.tgz -C /data .
```
La cola de música está en memoria y se pierde al reiniciar el contenedor.

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
│   ├── general.py       # Comandos generales (ping, help)
│   ├── errors.py        # Mensajes claros ante errores de comandos (/ y .)
│   ├── message_stats.py # Recuento de mensajes y niveles
│   ├── welcome.py      # Bienvenidas y despedidas
│   ├── images.py        # Comandos de imagen (magik)
│   └── music.py         # Comandos de música y control por servidor
├── utils/
│   └── responder.py     # Adaptador común: misma lógica para / y .
├── services/            # Lógica de negocio pura, sin discord.py
│   ├── levels.py        # Cálculo de niveles y progreso
│   ├── magik.py         # Seam carving con Pillow y numpy (testable sin Discord)
│   ├── music.py          # Pistas, cola y límites (testable sin red)
│   └── music_source.py   # Extracción de audio con yt-dlp (bloqueante)
└── assets/
    ├── bienvenida.mp4  # Vídeo adjunto al mensaje de bienvenida
    └── despedidas.txt  # Frases de despedida, editables sin tocar código
```

En la raíz: `Dockerfile`, `docker-compose.yml` y `.env.example` para el despliegue.

Consulta [Biblia.txt](./Biblia.txt) para el detalle completo de la
estructura de referencia, los límites entre capas y las normas de
seguridad, rendimiento y documentación.
