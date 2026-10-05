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
  terminada sin los suyos. Se alimentan con `casino_play`, `track` o `note` de
  `bot.cogs.achievements`. Sección "Logros" de `Biblia.txt`.
- **Comandos:** un solo nombre en español de 8 caracteres como máximo, idéntico con `/`
  y con `.`, sin alias ni subcomandos (`/poner`, no `/play`). Sección "Cogs y comandos"
  de `Biblia.txt`.
- **Comprobar antes de entregar:** `ruff check src tests`, `ruff format --check src tests`
  y `python -m pytest -q`.
