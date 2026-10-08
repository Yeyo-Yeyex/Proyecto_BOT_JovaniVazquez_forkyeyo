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
  terminada sin los suyos, y cuantos más, mejor (con gracia y sátira de la España
  actual). Se alimentan con `casino_play`, `track`, `note` o `note_for` de
  `bot.cogs.achievements`. La rareza sigue la escala por esfuerzo de la Biblia; los de
  suerte del casino se calibran con `docs/auditoria_logros.py`. Sección "Logros" de
  `Biblia.txt`, que tiene la lista de familias de logros que hay que cubrir.
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
- **Comprobar antes de entregar:** `ruff check src tests`, `ruff format --check src tests`
  y `python -m pytest -q`.

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

El título de cada PR acaba en Discord: al desplegar, `actualizar.sh` publica los
títulos de los PR nuevos como «📜 Novedades del bot» (README, «Novedades»). Lo leen
los miembros del servidor, no programadores.

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
- **La PR del fork a godzilin** se salta en el aviso si trae otros PR dentro, pero si
  lo que trae llegó al `main` del fork sin PR, su título es la única línea que saldrá:
  resume todo lo nuevo («Caballos y una sola semana para el IRPF»), nunca «Caballos»
  ni «Merge main».
- **Asuntos de commit** en español y legibles: si un PR queda con el título
  automático, el aviso usa los asuntos de sus commits.
- **Descripción** (no sale en Discord, es para quien revisa): qué cambia y por qué,
  en párrafos cortos; cómo se ha probado; y lo que hay que vigilar al desplegar
  (logros retroactivos que se cobran de golpe, cambios de cifras de la economía,
  pasos a mano en el NAS). Sin listas de archivos tocados: eso ya lo enseña el diff.
