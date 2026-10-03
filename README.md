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
4. `CASINO_CHANNEL_IDS` (opcional) limita la ruleta a esos canales: IDs
   separados por comas. En nuestro servidor, `#casino` es
   `1384280704539562054`. Vacío = se puede jugar en cualquier canal.

El bot carga automáticamente el archivo `.env` (si existe) al arrancar,
mediante `python-dotenv`. En producción no es necesario un archivo
`.env`: basta con exportar las variables en el entorno de despliegue.

## Ejecución

```bash
python -m bot
```

## Funcionalidad actual

- Cada comando tiene **un único nombre corto, igual con `/` y con `.`**
  (`/play despacito` = `.play despacito`), salvo los de imagen, que solo
  existen con `.` para dejar los slash commands al resto del bot. Escribe
  `/help` o `.help` para ver la lista:

  | Categoría | Comandos |
  |---|---|
  | ⚙️ General | `help` · `ping` |
  | 🎵 Música | `clear` · `pause` · `play <consulta>` · `queue` · `remove <posicion>` · `resume` · `skip` · `stop` · `volume <1-200>` |
  | 📊 Niveles | `level [miembro]` · `top [pagina]` |
  | 🎰 Casino | `daily` · `ruleta [cantidad] [apuesta]` · `saldo [miembro]` |
  | 🔔 Entradas | `entrada [archivo] [volumen] [borrar]` |
  | 🗼 Diversión | `babel <texto \| @miembros #canales>` |
  | 🎨 Imagen (solo `.`) | `magik [miembro]` · `memes [efecto]` · 108 efectos (`.memes`) |
  | 🛡️ Admin | `ban` · `kick` · `lock` · `mute` · `nick` · `purge` · `role` · `say` · `slow` · `unban` · `unlock` · `unmute` |

  La ayuda cabe en un solo embed: categorías con los nombres en orden
  alfabético, sin descripciones. La categoría Admin solo la ve quien es
  administrador.
- Los **comandos de administración** solo los pueden usar miembros con el
  permiso Administrador (se comprueba en cada uso, no solo ocultándolos del
  menú). Respetan la jerarquía de roles, dejan el motivo y el autor en el
  registro de auditoría y avisan si al bot le falta un permiso. Detalle de
  cada uno en [FUNCIONALIDADES.md](./FUNCIONALIDADES.md#6-quater-administración).

- `magik` deforma una imagen con *seam carving* (reescalado consciente del
  contenido, el mismo efecto que Dank Memer): en vez de estirar o recortar,
  elimina primero los caminos de píxeles menos importantes y vuelve a
  ampliar, así que las formas se "derriten". Usa, por orden: la imagen
  adjunta (también la del mensaje al que respondes), el avatar del miembro
  indicado o el tuyo. Ejemplos: `.magik` con una foto adjunta,
  `.magik @alguien`, o responde a una foto con `.magik`. Solo acepta
  adjuntos de Discord y avatares (nunca enlaces externos), hasta 8 MB, con
  5 segundos de espera entre usos por usuario. Respeta la rotación de las
  fotos de móvil. El bot necesita el permiso **Adjuntar archivos** en el
  canal; si falta, `magik` lo explica en lugar de quedarse colgado.
- Los **108 efectos de imagen de Dank Memer** (`trigger`, `slap`, `wanted`,
  `changemymind`, `brain`, `tweet`, `crab`...) funcionan como comandos de
  texto con su nombre original. `.help` y `.memes` los listan por tipo y
  `.memes <efecto>` explica uno. Reglas comunes:
  - `@alguien` (o responder a su mensaje) usa su avatar; una imagen adjunta
    lo sustituye. En los de dos personas (`slap`, `spank`, `bed`...) tú eres
    la primera y el mencionado la segunda.
  - Los textos múltiples se separan con `|`: `.brain agua | zumo | café | café a las 3`.
    En los efectos de solo texto, `@alguien` se escribe con su nombre.
  - Comparten con `magik` el límite de 8 MB, los 5 s de espera por usuario
    y un máximo de 2 trabajos a la vez en todo el bot.
  - `crab`, `letmein` y `scaryabove` devuelven MP4 (los genera `ffmpeg`,
    ya incluido en la imagen Docker); `trigger`, `dank`, `salty`, `airpods`,
    `america`, `communism` y `kowalski` devuelven GIF.
  - Diferencias con Dank Memer: los emojis del texto no se dibujan (los
    personalizados salen como `:nombre:`), `dream` es una imitación ligera
    sin TensorFlow, y `radialblur` y `warp` están reimplementados con numpy.
    No se incluyen `profile` (la ficha de la economía de Dank Memer) ni
    `yomomma` (solo devuelve un chiste de texto).
  - Las plantillas (~27 MB) están en `src/bot/assets/memes`, con la licencia
    MIT de [imgen](https://github.com/DankMemer/imgen).
- **Economía (yapdollars):** una sola moneda, ficticia y no comprable,
  para todo el bot. Cada miembro empieza con 1.000 Y$ por servidor y
  `daily` paga 500 Y$ más 100 por cada día seguido (tope 1.500 Y$; se
  cobra cada 20 h y la racha se pierde tras 48 h). Todo movimiento queda en
  un libro (`economy_ledger`) y se aplica de forma atómica: dos clics a la
  vez no pueden gastar dos veces el mismo dinero.
- **Ruleta americana** (0 y 00, la casa gana el 5,26 %): `ruleta` abre una
  mesa con botones que solo puede usar quien la abre. Cada clic en una
  apuesta cobra, gira (GIF de 2 s) y paga. Botones: rojo/negro, par/impar,
  1-18/19-36, docenas, columnas, 0 y 00; 🎯 **Números** abre un formulario
  para plenos, caballos, transversales, cuadros, seisenas y la línea
  0-00-1-2-3. Con ½, ×2 y 💰 All-in se cambia la ficha; 🔁 Repetir y
  ⏫ Doblar repiten la última apuesta. Atajos de texto:
  `.ruleta 500`, `.ruleta all rojo`, `.ruleta 50 17-20`, `.ruleta rojo`.
  Las 38 animaciones (~50 KB cada una) se precalculan al arrancar (~5 s de
  CPU), así que una tirada no dibuja nada.
- `babel` es un teléfono escacharrado con traductores: pasa el texto por 99
  idiomas elegidos al azar y lo devuelve al español, para ver qué queda.
  Responde con el antes, el después y la ruta de idiomas. `.babel` sin texto,
  respondiendo a un mensaje, traduce ese mensaje. Máximo 300 caracteres y una
  tirada a la vez en todo el bot (son 100 peticiones seguidas y tardan unos
  segundos). Usa el endpoint público de Google Translate, sin clave ni coste,
  pero sin garantías: si Google limita la IP, la tirada se corta, vuelve al
  español desde donde iba y lo avisa.
  - Si solo le das menciones (`.babel @Ana @Luis #general`, hasta 10), en
    vez de enseñar el resultado **cambia de verdad** el apodo de esos
    miembros y el nombre de esos canales. Conserva emojis y separadores del
    principio del nombre (`🎮・juegos` → `🎮・<traducción>`). Todos los nombres
    viajan juntos en la misma tirada de 100 traducciones.
  - Exige los mismos permisos que Discord para hacerlo a mano: tu apodo,
    **Cambiar apodo**; el de otro, **Gestionar apodos** y un rol por encima
    del suyo; un canal, **Gestionar canales**. El bot necesita **Gestionar
    apodos** y **Gestionar canales** y estar por encima de los roles de
    quienes renombra. Al dueño del servidor no se le puede cambiar el apodo.
  - Discord solo deja renombrar un canal 2 veces cada 10 minutos; el bot lo
    avisa en vez de quedarse esperando. No hay comando para deshacer: el
    apodo se quita desde Discord y el canal se renombra a mano.
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

La economía usa el mismo archivo SQLite (tablas `economy_*`). Si el bot sale
de un servidor, se borran sus saldos y su libro de movimientos.

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
y **Hablar** en los canales de voz donde se vaya a usar la música. Para los
comandos de administración necesita además Gestionar mensajes, Aislar
temporalmente a miembros, Expulsar, Banear, Gestionar canales, Gestionar
apodos y Gestionar roles, y su rol debe estar por encima de los roles que
vaya a moderar.

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
│   ├── admin.py         # Moderación solo para administradores
│   ├── errors.py        # Mensajes claros ante errores de comandos (/ y .)
│   ├── message_stats.py # Recuento de mensajes y niveles
│   ├── welcome.py      # Bienvenidas y despedidas
│   ├── images.py        # Comandos de imagen: magik, memes y los 108 efectos
│   ├── casino.py        # Ruleta con botones, saldo y daily
│   └── music.py         # Comandos de música y control por servidor
├── utils/
│   └── responder.py     # Adaptador común: misma lógica para / y .
├── services/            # Lógica de negocio pura, sin discord.py
│   ├── levels.py        # Cálculo de niveles y progreso
│   ├── economy.py       # Yapdollars: única puerta al dinero del bot
│   ├── roulette.py      # Reglas de la ruleta americana (apuestas y pagos)
│   ├── roulette_render.py # GIF y PNG de la rueda, precalculados
│   ├── moderation.py    # Duraciones, IDs y jerarquía de roles de los comandos de admin
│   ├── image_input.py   # Lectura validada de imágenes de usuario (límites, EXIF)
│   ├── magik.py         # Seam carving con Pillow y numpy (testable sin Discord)
│   ├── memes/           # Efectos de Dank Memer: registro, utilidades y efectos
│   ├── music.py          # Pistas, cola y límites (testable sin red)
│   └── music_source.py   # Extracción de audio con yt-dlp (bloqueante)
└── assets/
    ├── bienvenida.mp4  # Vídeo adjunto al mensaje de bienvenida
    ├── despedidas.txt  # Frases de despedida, editables sin tocar código
    └── memes/          # Plantillas y fuentes de los efectos (de imgen, MIT)
```

En la raíz: `Dockerfile`, `docker-compose.yml` y `.env.example` para el despliegue.

Consulta [Biblia.txt](./Biblia.txt) para el detalle completo de la
estructura de referencia, los límites entre capas y las normas de
seguridad, rendimiento y documentación.
