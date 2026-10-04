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
4. `CASINO_CHANNEL_IDS` (opcional) limita los juegos del casino (ruleta,
   blackjack, tragaperras y pachinko) a esos canales: IDs
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
  | 🎂 Cumpleaños | `cumple [dd/mm] [miembro]` · `cumples` |
  | 🏆 Logros | `logros [miembro]` |
  | 🎰 Casino | `donar [ong] [cantidad]` · `imv` · `hacienda` · `renta` · `ruleta [cantidad] [apuesta]` · `blackjack [cantidad]` (atajo `.bj`) · `crash [cantidad] [auto]` · `minas [cantidad] [minas]` · `pachinko [cantidad]` · `saldo [miembro]` · `slots [cantidad]` |
  | 🔔 Entradas | `entrada [archivo] [volumen] [borrar]` |
  | 🗼 Diversión | `babel <texto \| @miembros #canales>` |
  | 🎨 Imagen (solo `.`) | `magik [miembro]` · `memes [efecto]` · 108 efectos (`.memes`) |
  | 🛡️ Admin | `ban` · `bienv` · `kick` · `lock` · `mute` · `nick` · `purge` · `role` · `say` · `slow` · `unban` · `unlock` · `unmute` |

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
  `imv` (Ingreso Mínimo Vital, antes `daily`, exento de IRPF) paga 500 Y$
  más 100 por cada día seguido (tope 1.500 Y$; se cobra cada 20 h y la racha se
  pierde tras 48 h).
- **Cumpleaños:** `cumple 14/02` guarda el tuyo (solo día y mes; una vez
  puesto, solo un administrador lo cambia) y `cumples` lista los próximos.
  Ese día (hora canaria) el bot lo anuncia en `#chat-general` y el
  cumpleañero recibe 3.000 Y$. Felicitarle, con el botón 🎉 o con un mensaje
  que le mencione o le responda y suene a felicitación, da 500 Y$ a quien
  felicita y 100 Y$ más al cumpleañero, una vez por persona. Si existe un rol
  llamado `🎂 Cumpleañero`, el bot se lo pone durante el día (necesita
  **Gestionar roles**). Los regalos pagan IRPF con retención (ganancia
  patrimonial, art. 33.1 LIRPF), aunque solo muerde a quien ya pasa del mínimo.
- **IRPF y Hacienda:** los premios por subir de nivel y la ganancia neta
  diaria del casino tributan; el IMV está exento, como el real (art. 7.y
  LIRPF). En el casino, las pérdidas del día compensan las ganancias del
  mismo día: si pierdes después de ganar, Hacienda te devuelve lo retenido
  de más.
- **Campaña de la Renta:** cada lunes se cierra la semana anterior y las
  pérdidas de unos días compensan las ganancias de otros. Lo retenido de más
  sale a devolver, y se cobra presentando la declaración: con `renta` o con
  el aviso (solo lo ves tú) que sale la primera vez que juegas en la semana.
  Al presentar, el bot lo anuncia en el canal. Se guardan sin caducidad las 2
  últimas semanas pendientes; si se acumula otra, la más antigua se pierde.
  El bot crea un evento de Discord por campaña (necesita **Gestionar
  eventos**). La retención
  proyecta la renta anual con lo cobrado en los últimos 30 días y le aplica
  la escala estatal y la de Canarias con sus mínimos personales, a 10 Y$
  por euro (detalle y fuentes en `src/bot/services/taxes.py`). Cada cobro
  muestra una línea pequeña con lo que se lleva Perro Sanxe. Todo lo
  retenido entra en la cuenta del Estado (`user_id = 0` en
  `economy_wallets`), y `hacienda` muestra a cualquiera su saldo, lo
  recaudado este año y desde siempre, y quién más ha pagado. Todo movimiento queda en
  un libro (`economy_ledger`) y se aplica de forma atómica: dos clics a la
  vez no pueden gastar dos veces el mismo dinero.
- **Impuesto sobre el Patrimonio:** cada lunes se cobra lo que pase de
  70.000 Y$ con los tipos reales (0,2 % a 3,5 %, art. 30 de la Ley 19/1991),
  tramos escalados al mismo factor que el mínimo. `saldo` avisa de lo que te
  tocaría y el bot anuncia quién ha pagado. La primera semana tras desplegarlo
  no cobra.
- **IGIC:** el impuesto al consumo es el canario, al 7 %, para cuando haya
  tienda (`taxes.igic`).
- **Donativos** (`donar`): cuatro ONGs de broma que hacen lo contrario de lo
  que dicen. Donar es gastar (el dinero se queda en la ONG), pero desgrava en
  la renta del lunes: 80 % de los primeros 2.500 Y$ y 40 % del resto, hasta el
  10 % de lo que ganes esa semana y sin pasar del IRPF pagado.
- **Ruleta americana** (0 y 00, la casa gana el 5,26 %): `ruleta` abre una
  mesa con botones que solo puede usar quien la abre. Cada clic en una
  apuesta cobra, gira (GIF de 2 s) y paga. Botones: rojo/negro, par/impar,
  1-18/19-36, docenas, columnas, 0 y 00; 🎯 **Números** abre un formulario
  para plenos, caballos, transversales, cuadros, seisenas y la línea
  0-00-1-2-3. Con ½, ×2 y 💰 All-in se cambia la ficha; 🔁 Repetir y
  ⏫ Doblar repiten la última tirada.
  🧩 **Varias** cambia al modo de varias apuestas: cada botón pone una
  ficha (pulsar dos veces la misma apila fichas), 🎰 **Girar** las juega
  todas en la misma tirada y 🗑️ las quita. Hasta 10 apuestas distintas;
  todo se cobra y se paga en una sola operación y el resultado marca qué
  entró. Atajos de texto: `.ruleta 500`, `.ruleta all rojo`,
  `.ruleta 50 17-20`, `.ruleta rojo`, `.ruleta 100 rojo + 17 + d2` (varias
  con `+`, la cantidad es por apuesta; con `all` se reparte el saldo).
  Las 38 animaciones (~50 KB cada una) se precalculan al arrancar (~5 s de
  CPU), así que una tirada no dibuja nada.
- **Blackjack** (`/blackjack`, `.blackjack` o `.bj`): reparte al momento con la apuesta indicada
  (`.bj 500`, `.bj all`) y se juega con botones: 🃏 Pedir, ✋ Plantarse,
  ⏫ Doblar y ✂️ Separar. Al terminar, 🃏 Repartir juega otra mano en el mismo
  mensaje y ½ / ×2 / 💰 All-in cambian la apuesta. La mesa es una imagen
  (tapete y cartas, ~5-10 KB por paso) y la banca roba carta a carta en
  pantalla. Reglas: 6 barajas rebarajadas en cada mano, la banca se planta
  en 17 (también blando) y mira si tiene blackjack, blackjack paga 3:2,
  doblar con dos cartas (también tras separar), separar una vez; sin seguro
  ni rendición. La apuesta se cobra al repartir (y al doblar o separar) y
  el premio se paga al acabar. Si la mesa caduca o el bot se apaga con una
  mano a medias, se planta y se paga.
- **Tragaperras** (`/slots`, `.slots [cantidad]`): una máquina de 3 rodillos
  con botones que solo usa quien la abre. Paga la fila del medio; las filas
  de arriba y abajo se ven para que se note cuándo has estado cerca.
  Botones: 🎰 Tirar, 🔁 Auto ×10 (diez tiradas con un solo resumen),
  ⚡ Turbo (sin animación), ½ / ×2 / 💰 All-in y 📋 Premios. Premios:
  🍒 al principio devuelve la mitad, 🍒 🍒 ×2, tríos de ×4 a ×200, 🃏 comodín
  y 🃏 🃏 🃏 se lleva el **bote común** del servidor, que crece con el 3 % de
  cada apuesta y vuelve a 5.000 Y$ al vaciarse. Tres 🎟️ en cualquier fila dan
  5 giros gratis, y cada 5 tiradas con premio la máquina se calienta y la
  siguiente paga ×2. Devuelve ~94 % de lo apostado contando el bote. El GIF
  (~150 KB, ~0,1 s de CPU) se monta en cada tirada con piezas precalculadas:
  los rodillos paran uno a uno y, si los dos primeros prometen algo gordo, el
  tercero frena despacio. Los premios tributan como el resto del casino, el
  bote incluido, y los de más de ×50 y los botes se anuncian en el canal.
- **Crash** (`/crash`, `.crash [cantidad] [auto]`): un cohete compartido
  por canal. En el embarque (7-10 s) se entra con 🚀 o con `.crash 500 2x`
  (500 Y$ y auto-retiro en 2x); ½, ×2, 💰 All-in y 🎯 Auto cambian tu ficha.
  Luego el multiplicador sube, lento al principio y cada vez más rápido
  (2x a los 8 s, 10x a los 19 s, tope en 1.000x) y 💸 Retirar cobra
  apuesta × multiplicador; quien sigue dentro cuando explota, lo pierde. Devuelve el 99 % de media y el 1 % de las
  rondas explota en 1,00x. Al explotar sale una gráfica con la curva y quién
  saltó dónde, y el mismo mensaje abre la ronda siguiente. Una edición por
  segundo durante el vuelo y una imagen por ronda.
- **Minas** (`/minas`, `.minas [cantidad] [minas]`): un tablero de 5×5 propio
  con 1 a 23 minas (2 por defecto). La primera casilla siempre es buena y
  devuelve la apuesta; desde ahí cada 💎 sube el multiplicador, 💰 Cobrar se
  lo lleva y una 💣 lo pierde todo. Más minas, más pago por casilla: un menú
  enseña lo que paga cada opción. El texto cuenta las casillas (💎 7/23), lo
  que sumaría la siguiente y su probabilidad, celebra rachas y avisa al batir
  tu récord. Devuelve el 99 % de media desde la segunda casilla, sin
  imágenes, y los cobros de ×25 o más se anuncian en el canal.
- **Pachinko** (`/pachinko`, `.pachinko [cantidad]`): una máquina japonesa
  propia con botones. Cada 🎯 Lanzar cobra la apuesta y suelta 10 bolas que
  rebotan por 10 filas de clavos hasta 11 bolsillos (×10 en las esquinas, ×3,
  ×1, OUT y START en el centro). Cada bola en START gana una tirada de la
  pantalla (reserva de 4): tres iguales es **ATARI** (+30 bolas), los impares
  encadenan premios en un **RUSH** y el 7 es el **SUPER RUSH**. El reach
  (dos iguales y el centro frenando) es espectáculo y no cambia nada.
  Botones: 🎯 Lanzar, 🔁 Ráfaga ×5, ⚡ Turbo, ½ / ×2 / 💰 All-in y 📋 Premios.
  Devuelve el 94,6 % (calculado con fracciones exactas en las pruebas) y hay
  un atari cada ~17 tandas. El GIF (130-330 KB, ~0,5 s de CPU) tiene
  bombillas que persiguen, molinillos, rótulo de neón y la pantalla jugando
  la reserva mientras siguen cayendo bolas. Tributa como el resto del casino
  y los SUPER RUSH, los rush de 5 o más y los premios de ×20 se anuncian.
- **Logros** (`logros [miembro]`): 344 logros en 14 categorías (chat,
  horarios y fechas, voz, social, niveles, ruleta, blackjack, casino,
  tragaperras, Crash, Minas, pachinko, economía y coleccionista), con cinco rarezas: ▫️ común,
  🔹 raro, 💠 épico, 🌟 legendario y 👑 mítico. Van desde escribir el primer
  mensaje hasta pasar 1.000 horas en llamada, acertar 50 plenos o pagar un
  millón de IRPF; 35 son secretos y se ven como `???` hasta conseguirlos.
  Cada logro paga yapdollars según su rareza (50, 200, 750,
  2.500 o 10.000 Y$ brutos) con retención de IRPF, y se anuncia en el canal
  donde se consiguió. `logros` enseña un resumen (total, puntos, últimos
  conseguidos, los más cercanos y el más raro), un menú por categorías con
  el progreso de cada uno y el porcentaje del servidor que lo tiene, y un
  botón 🏆 Ranking por puntos.
  - Los mensajes y reacciones se cuentan en memoria y se guardan una vez por
    minuto en una sola escritura por servidor. Del mensaje solo se miran
    propiedades (largo, hora, enlace, mayúsculas…), nunca se guarda el texto.
  - La voz cuenta un minuto cada minuto a quien está en llamada (aunque
    tenga el micro silenciado) con al menos otra persona que no esté
    ensordecida. El canal AFK no cuenta.
  - La primera vez que el bot ve a alguien recupera sus mensajes del
    historial importado y su nivel, así que los veteranos cobran de golpe
    lo que ya tenían.
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
- Los avisos de subida de nivel se publican en el canal donde se ganó el XP
  (el chat del canal de voz si fue hablando) e incluyen el premio cobrado.
- Al entrar alguien, el bot le saluda con una frase de
  `src/bot/assets/bienvenidas.txt` y el GIF del servidor (sin GIF, el vídeo
  `src/bot/assets/bienvenida.mp4`) en el canal de bienvenida, que por
  defecto es `#chat-general`. Si ya había estado, usa las frases de vuelta.
  Los demás pueden pulsar **👋 Dar la bienvenida** durante su primer día:
  200 Y$ para quien saluda y 100 Y$ para el nuevo, sin IRPF (como los
  regalos de cumpleaños), y cuenta para logros. Un administrador cambia el GIF y el canal con
  `bienv`. Al salir, publica una despedida con una frase aleatoria tomada de
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
recuentos y niveles existentes permanecen guardados. Fuentes de XP:

- Mensajes: 15–25 XP, como máximo una vez cada 60 segundos por miembro.
- Primer mensaje del día (hora canaria): +50 XP.
- Voz: 4–6 XP por minuto sin mute ni ensordecido, fuera del canal AFK y con
  al menos otra persona sin mutear en el canal.
- Reacciones recibidas de otros: +5 XP, con tope de 100 XP al día.
- Racha: cada día seguido escribiendo suma un 2 % al XP de mensajes y voz,
  hasta +20 %.
- Hora feliz: una hora al día (entre las 12:00 y las 23:00, distinta cada
  día) con XP ×2 en mensajes y voz. Se anuncia en el canal del sistema del
  servidor.

Subir al nivel N paga 100 × N Y$ brutos, el doble en los múltiplos de 5. Los
niveles que ya tenía cada uno antes de este cambio no se pagan.

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

Las frases de bienvenida se editan en `src/bot/assets/bienvenidas.txt` con
las mismas reglas; `{usuario}` se cambia por la mención y lo que va tras la
línea `[vuelta]` es para quien vuelve al servidor.

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
│   ├── welcome.py       # Bienvenidas (GIF, frases, botón 👋) y despedidas
│   ├── images.py        # Comandos de imagen: magik, memes y los 108 efectos
│   ├── casino.py        # Ruleta con botones, saldo y daily
│   ├── patrimonio.py    # Impuesto sobre el Patrimonio de cada lunes
│   ├── donations.py     # donar: ONGs de broma y donativos deducibles
│   ├── blackjack.py     # Blackjack con botones (bj)
│   ├── slots.py         # Tragaperras con botones, Auto, turbo y bote común
│   ├── crash.py         # Crash: cohete compartido por canal, rondas seguidas
│   ├── mines.py         # Minas: tablero de 5×5 con botones (componentes v2)
│   ├── pachinko.py      # Pachinko con botones, Ráfaga y turbo
│   ├── achievements.py  # Logros: seguimiento, premios, avisos y `logros`
│   └── music.py         # Comandos de música y control por servidor
├── utils/
│   └── responder.py     # Adaptador común: misma lógica para / y .
├── services/            # Lógica de negocio pura, sin discord.py
│   ├── levels.py        # Cálculo de niveles y progreso
│   ├── achievements.py  # Catálogo de logros y qué cuenta cada jugada o mensaje
│   ├── economy.py       # Yapdollars: única puerta al dinero del bot
│   ├── taxes.py         # IRPF, Patrimonio, IGIC y deducción por donativos
│   ├── donations.py     # Catálogo de ONGs y texto de la deducción
│   ├── roulette.py      # Reglas de la ruleta americana (apuestas y pagos)
│   ├── blackjack.py     # Reglas del blackjack (zapato, manos, banca, pagos)
│   ├── cards_render.py  # Imagen de la mesa de blackjack
│   ├── roulette_render.py # GIF y PNG de la rueda, precalculados
│   ├── slots.py         # Rodillos, premios, giros gratis y máquina caliente
│   ├── slots_render.py  # GIF de cada tirada con piezas precalculadas
│   ├── crash.py         # Punto de explosión, curva del cohete y ronda
│   ├── crash_render.py  # Gráfica PNG de cada ronda de Crash
│   ├── mines.py         # Multiplicadores exactos y partida de Minas
│   ├── pachinko.py      # Clavos, bolsillos, sorteo, rush y retorno exacto
│   ├── pachinko_render.py # GIF neón de cada tanda del pachinko
│   ├── moderation.py    # Duraciones, IDs y jerarquía de roles de los comandos de admin
│   ├── welcome.py       # GIF de bienvenida, frases y reglas del botón 👋
│   ├── image_input.py   # Lectura validada de imágenes de usuario (límites, EXIF)
│   ├── magik.py         # Seam carving con Pillow y numpy (testable sin Discord)
│   ├── memes/           # Efectos de Dank Memer: registro, utilidades y efectos
│   ├── music.py          # Pistas, cola y límites (testable sin red)
│   └── music_source.py   # Extracción de audio con yt-dlp (bloqueante)
└── assets/
    ├── bienvenida.mp4  # Vídeo adjunto al mensaje de bienvenida
    ├── bienvenidas.txt # Frases de bienvenida y de vuelta, editables sin tocar código
    ├── despedidas.txt  # Frases de despedida, editables sin tocar código
    ├── memes/          # Plantillas y fuentes de los efectos (de imgen, MIT)
    └── slots/          # Símbolos de la tragaperras (Noto Emoji, ver LICENSE.txt)
```

En la raíz: `Dockerfile`, `docker-compose.yml` y `.env.example` para el despliegue.

Consulta [Biblia.txt](./Biblia.txt) para el detalle completo de la
estructura de referencia, los límites entre capas y las normas de
seguridad, rendimiento y documentación.
