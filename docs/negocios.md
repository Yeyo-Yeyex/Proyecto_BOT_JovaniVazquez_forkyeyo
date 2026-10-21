# Negocios: tu propia empresa dentro de `pala`

Diseño del sistema de negocios. Se acordó con el usuario el 20/10/2026, inspirado en *Big Ambitions*.
Este documento recoge el sistema entero, sus cinco fases y la fase 1 al detalle; la fase 1 es la versión 8.14.0.

## La idea

- **El tiempo es la moneda.** El día tiene `FRANJAS_DIA` (8) franjas. La jornada de la `pala` reserva las suyas
  (4, o menos con la reducción) aunque no fiches. Cada hora extra, guardia o turno de estudio gasta franjas
  libres, y cada turno en tu negocio también. Lo que haces de más en el curro se lo quitas al negocio.
- **Respondes con lo tuyo.** En la fase 1 el negocio es de un autónomo: no tiene caja propia y todo sale y
  entra del monedero de su dueño. Los gastos fijos (alquiler del local, suministros y cuota) corren aunque no
  abras, y un turno malo puede perder dinero.
- **Pesa lo que le dedicas.** Sin empleados, cerrado es cerrado. Lo pasivo solo llega con empleados (fase 4).
- **Visual y personal.** Todo el panel son escenas de Node, como `pisos`. Cada negocio lleva su nombre, sus
  colores, su emblema y sus clientes habituales, que te recuerdan.
- **La demanda es artificial y limitada.** Cada barrio tiene cada día un cupo de clientes por sector, y lo
  reparten los negocios del mismo sector que hay en él. El dinero nuevo que entra está topado.

## Las fases

| Fase | Qué trae | Versión |
|---|---|---|
| 1 | Franjas, reducción de jornada, excedencia, local en un barrio, 3 sectores con su minijuego, stock y precios, gastos fijos, cierre de cada noche y renta de actividad | 8.14.0 |
| 2 | Crecer el local: equipamiento (capacidad, amortización), traslado a un local mayor o a uno tuyo de `pisos`, marketing, reseñas, más sectores, precio por producto | 8.x |
| 3 | Sociedad limitada: caja propia, Impuesto de Sociedades, sueldo de administrador o dividendos, quiebra limitada, compatibilidades por oficio | 8.x |
| 4 | Empleados artificiales estilo inquilinos (rasgos, habilidades, formación, humor, contrato y despido), turnos pasivos, nómina completa con la cotización de la empresa (`compute_payslip`), ZEC y RIC | 8.x |
| 5 | Cadenas: varios locales, importar con nave, gerentes que automatizan, vender la empresa | 8.x |

Dónde engancha cada fase con lo de la 1:

- **Fase 2.** `Business.size` y `Business.equipment` se suman a la capacidad (`clients_per_turn`).
  El traslado cambia `barrio` y `size`, y las reseñas salen de `Business.reputation`.
- **Fase 3.** `Business.form` pasa de `"autonomo"` a `"sl"`. Con `"sl"`, el dinero va a una cuenta propia del
  negocio y no al monedero. Las rentas se apuntan en el Impuesto de Sociedades, no en `INCOME_BUSINESS`.
- **Fase 4.** Un turno pasivo es `settle_turn` con la nota que sale de las habilidades del empleado. Su
  sueldo pasa por `compute_payslip`: la cotización de la empresa, por fin, la paga alguien.
- **Fase 5.** La clave primaria del negocio ya es `(guild_id, user_id, id)`, así que un miembro puede tener
  varios cambiando solo la comprobación de «uno por persona».

## Fase 1 al detalle

### El día en franjas (`services/agenda.py`)

- `FRANJAS_DIA = 8`: unas 16 horas despierto en bloques de dos.
- **Jornada:** 4 franjas (`ORDINARY_SHIFTS`), 3 con la reducción de 1/4 o 2 con la de 1/2. Queda reservada
  todo el día aunque no se fiche.
- **Lo que gasta cada cosa:**
  - turno ordinario, extra, en B, de estudio o en teletrabajo: 1 franja;
  - guardia: 2 franjas;
  - turno en el negocio: 1 franja.
- **Cuentas del día:**
  - en el curro se usa lo que sea mayor entre la jornada y lo fichado de jornada;
  - lo libre es `FRANJAS_DIA` menos lo usado en el curro, menos las extras, menos el negocio.
- **Qué se bloquea sin franjas libres:** las horas extra, el B, las guardias y el estudio de la `pala`, y los
  turnos del negocio. Los turnos de jornada nunca se bloquean, porque ya están reservados.
- **El día** es el local de Canarias (`local_day`), como en el resto de la `pala`.
- **Datos que se guardan:**
  - en `Contract`: `jornada` (4, 3 o 2), `jornada_next` y su día (`jornada_from`), `excedencia_since` y
    `franjas_day`/`franjas_work` (franjas gastadas hoy en el curro);
  - en el negocio: `franjas_day`/`franjas_used`.

### Reducción de jornada

Es el art. 37.6 ET: de 1/8 a 1/2 de la jornada, con el sueldo reducido en proporción. En el juego:

- **Tramos:** completa (4 franjas), 3/4 (3) o media (2). Cada turno paga lo mismo, así que el sueldo baja
  porque hay menos turnos de jornada.
- **Preaviso** (art. 37.7 ET, 15 días): el cambio vale desde el día siguiente.
- **Sin horas extra, B ni guardias.** Es decisión del proyecto, por analogía con el tiempo parcial
  (art. 12.4.c ET). Las guardias mínimas del residente no se exigen mientras dure.
- **La pensión** sale de la media diaria cotizada en la rama, así que con reducción cotizas menos. En la vida
  real se protege la de cuidado de menores (art. 237 LGSS); aquí no, y se apunta como posible añadido.

### Excedencia voluntaria

Es el art. 46.2 ET; en la escala de la `pala`, un año son 7 días (los trienios son de 21).

- **Requisitos para pedirla:**
  - al menos 7 días en el oficio (`job_since`), el «año» de antigüedad;
  - que hayan pasado 28 días (los «4 años») desde la anterior.
- **Mientras dura:**
  - no reserva franjas y no se ficha;
  - el contrato se conserva;
  - para la pensión se sigue contando como trabajando: no hay pensión entera por pedirla.
- **Volver:**
  - pasados 2 días como mínimo (los «4 meses»);
  - vuelves al mismo puesto, con la barra a la mitad, como al volver de 7 días sin fichar;
  - en la vida real solo hay derecho preferente al reingreso (art. 46.5); es una simplificación del proyecto.
- **Cambiar de oficio** estando de excedencia la termina.

### El negocio (`services/business.py`, `business_catalog.py`)

- **Uno por miembro y servidor** en la fase 1. Forma: autónomo (`form = "autonomo"`).
- **Sectores** (`SECTORS`), cada uno con su minijuego:
  - ☕ **Cafetería** · minijuego «Comandas». Productos: café solo, cortado, barraquito, zumo, bocadillo y dulce.
    Cada cliente es una mesa que pide de 2 a 5 cosas.
  - 🛒 **Tienda de barrio** · minijuego «La caja». Productos: pan, leche, fruta, conservas, limpieza y bebidas.
    El cliente trae una cesta; se cobra y se da el cambio.
  - 🔧 **Taller** · minijuego «Diagnóstico». Recambios: pastillas de freno, batería, aceite, correa, bujías y
    neumático. Primero se diagnostica la avería y luego se elige el presupuesto.
- **Barrio:** uno de los 20 de `pisos`.
  - El tema del barrio (`THEMES`) multiplica la demanda de cada sector (`THEME_FIT`).
  - En lujo se aguantan mejor los precios altos.
  - El alquiler del local sale de `rent_m2` del barrio, los metros del sector y el `rent_factor` de los
    locales de `pisos`, a la misma escala que la renta de un local en `pisos`. Así un local cuesta lo mismo
    si te lo alquilan que si lo alquilas tú.
- **Personalización:**
  - nombre (hasta 24 caracteres; se valida y se limpia de menciones y saltos de línea);
  - paleta del rótulo (`PALETTES`, 8) y emblema (`EMBLEMS`, 8);
  - **clientes habituales** (`REGULARS`): cinco por negocio, elegidos por semilla. Cada uno tiene su nombre,
    su pedido de siempre y su fidelidad (0-10). Con buena fidelidad dejan más propina y piden
    «lo de siempre» sin decirlo: hay que recordarlo.
- **Precio:** un nivel para todo el negocio (`PRICE_LEVELS`): barato (−15 %), normal, caro (+15 %) o
  carísimo (+30 %).
  - Más caro, menos clientes y más margen.
  - Barato sube la reputación un poco.
  - En los barrios de lujo, lo caro espanta menos.
- **Reputación:** de 0 a 100, en estrellas de 1 a 5 (`stars`). Arranca en 50. Sube con los clientes bien
  atendidos y baja con los errores, las esperas y las acusaciones falsas.
- **Stock:** unidades por producto, que se compran al mayorista por lotes. Las perecederas se pierden en parte
  cada noche (`SPOIL`). Sin stock, ese producto no se vende: el cliente se va o pide otra cosa.

### La demanda (artificial y limitada)

- **Clientes de un turno:**
  `base del sector × THEME_FIT × curva horaria (hora canaria) × reputación (0,5 + estrellas / 6) × precio`.
- **Cupo diario por barrio y sector** (`DAILY_POOL`, en turnos de clientes base): un servidor no puede sacar
  de un barrio más que eso al día. Lo comparten los negocios del mismo sector en ese barrio, por orden de
  llegada, y se guarda en `business_demand`.
- **Curva horaria por sector:**
  - cafetería: mañana y merienda;
  - tienda: media mañana y tarde;
  - taller: horario de taller;
  - de madrugada, poco.

### Un turno (los minijuegos, `services/business_games.py`)

- Cada turno gasta 1 franja y, si el dueño tiene curro, batería (`SHIFT_COST`).
- Llega una cola de clientes (los de la demanda). A cada uno se le atiende en su pantalla (una imagen por
  cliente) y tiene paciencia; si se agota, se va. El turno acaba al vaciarse la cola o a los `TURN_SECONDS`.
- **Comandas** (cafetería):
  - en el bocadillo del cliente sale su pedido; se pulsan los productos y luego ✅ **Servir**;
  - acertar cobra la mesa; servir rápido deja propina;
  - fallar deja una queja y lo servido se pierde;
  - el habitual con fidelidad pide «lo de siempre» y no dice qué.
- **La caja** (tienda):
  - la cesta, el total y el billete con que paga; se da el cambio con monedas y billetes y se pulsa ✅ **Cobrar**;
  - si das de menos, queja; si das de más, lo pierdes;
  - a veces alguien esconde algo: 👀 **Bolsillo** lo pilla, pero acusar a quien no es baja mucho la reputación.
- **Diagnóstico** (taller):
  - el coche llega con un síntoma (texto y una pista en el dibujo); se elige la avería entre cuatro;
  - luego el presupuesto: ajustado, justo o caro; cuanto más caro, más fácil que no lo acepte;
  - si fallas el diagnóstico, gastas el recambio, no cobras (garantía) y baja la reputación.
- **El resultado** (`settle_turn`): ventas, propinas, coste de lo vendido, mermas, reputación, fidelidad de
  los habituales y la nota del turno (para los logros).
- **Velocidad:**
  - el siguiente cliente se conoce de antemano (semilla del turno), así que su imagen se pinta mientras
    atiendes al de ahora;
  - los clics dentro de un cliente solo cambian el texto: contestan sin subir imagen.

### Dinero e impuestos

Todo por `EconomyService.business`, en una transacción con lo que guarda el negocio, como `pisos`.

- **Ventas:** las crea la demanda, que no existe; son el grifo.
  - Son rendimiento de actividad económica (art. 27 LIRPF), con criterio de caja (art. 7.2 RIRPF): ingreso
    al cobrar y gasto al pagar.
  - Retienen a cuenta con el tipo estable sobre el margen del turno (ventas menos el coste de lo vendido), en
    lugar del 20 % del pago fraccionado (art. 110 RIRPF): una sola vara para toda la renta (Biblia).
  - El embargo del 30 % de quien debe a Hacienda alcanza a las ventas.
- **Compras al mayorista:** la base desaparece y el IGIC va al Estado.
  - El autónomo está en el régimen especial del pequeño empresario (Canarias, hasta 50.000 € al año desde
    julio de 2026): no repercute IGIC en sus ventas y no deduce el soportado, que pasa a ser coste.
  - Base más IGIC se apuntan como gasto de la actividad (`INCOME_BUSINESS` en negativo).
- **Montar el negocio:**
  - la tasa municipal de la apertura (comunicación previa) va al Estado, porque no hay ayuntamientos;
  - la fianza del local son 2 mensualidades (art. 36.1 LAU) y va al depósito de fianzas
    (`HOUSING_DEPOSIT_ACCOUNT_ID`, como la de los pisos); vuelve al cerrar, menos lo que se deba;
  - el acondicionamiento lleva IGIC y se apunta entero como gasto de esa semana (elementos de poco valor,
    art. 12.3.e LIS por remisión del art. 30 LIRPF; simplificación).
- **Cada noche, el alquiler y los suministros:**
  - el alquiler del local lleva IGIC y la retención del 19 % del arrendador (art. 100 RIRPF), que el inquilino
    ingresa en el Estado; el resto desaparece (el casero no existe);
  - los suministros llevan IGIC;
  - se cobra lo que llegue del monedero y el resto queda como deuda del local;
  - con `EVICTION_NIGHTS` (3) noches sin pagar, el casero rescinde: el negocio cierra y la fianza cubre lo
    que pueda.
- **Cuota de autónomo:** cada lunes, `SELF_EMPLOYED_FEE` (la tarifa plana `FLAT_RATE_FEE` la primera semana,
  art. 38 ter de la Ley 20/2007) por `charge_self_employed_fee`.
  - Si su puesto de la `pala` ya es de autónomo (reparto, influencer), no paga dos.
  - Con curro por cuenta ajena es pluriactividad: paga las dos cotizaciones. El reintegro del art. 313 LGSS
    se deja para la fase 3.
- **Mostrar la mordida:**
  - cada resultado con dinero lleva su línea de impuestos (🐶 IRPF a cuenta, IGIC);
  - el aviso de la Renta (`renta.remind`) y la mascota (`renta.hint` con el momento
    `Event.BUSINESS`: contenta con una S o una A, de bajón con una D);
  - `hacienda` cuenta el IGIC, las tasas y la retención del alquiler (`economy_business_tax`);
  - `saldo` enseña el alquiler de esta noche, la deuda del local y la cuota de la semana (`pending`).

### Las pantallas (escena de Node, `assets/negocio/escena.html`)

1000 × 640, al doble (S = 2), con las fuentes de `pisos`. Sin emojis: los iconos son trazos de canvas. Python
decide todo (`services/business_view.py`) y la escena solo pinta. Si no hay Node ni Chromium, el panel sale en
texto (`ui.TextDisplay`), como la `pala`.

| `screen` | Para qué |
|---|---|
| `dia` | El inicio de `pala`: las 8 franjas del día, la tarjeta del curro y la del negocio (o la de montar uno) |
| `montar` | Elegir sector: los tres locales con su fachada y sus cifras |
| `negocio` | La portada: la fachada con su rótulo, el barrio de fondo y las cifras del día. También es la vista previa al montarlo |
| `almacen` | El stock: cada producto con sus unidades, coste, precio y si caduca |
| `cuentas` | La cuenta de resultados de esta semana y la anterior |
| `cliente` | El minijuego: el mostrador con el cliente, su pedido, cesta o coche, y la cola |
| `cierre` | El resumen del turno: atendidos, perdidos, caja, propinas, estrellas y nota |

El contrato de cada pantalla está en la docstring de `services/business_view.py`.

### Panel dentro de `pala`

- **`pala` abre el inicio** (`dia`), un `LayoutView` con la imagen en un `MediaGallery` (PNG; nunca GIF, por
  lo de la Torre).
  - Con curro y negocio: 🪏 **Trabajo** y 🏪 **Negocio**.
  - Con uno solo: su botón y otro pequeño para lo que falta (🏪 **Montar negocio** o 💼 **Buscar curro**).
  - Sin nada: los dos botones de empezar.
- **🪏 Trabajo** es el panel de siempre (texto), con 🏠 **Inicio** para volver. Su menú de la jornada:
  - reducción de jornada;
  - excedencia y volver de ella.
- **🏪 Negocio** es la portada, con:
  - 🔑 **Abrir** (un turno, 1 franja);
  - 📦 **Almacén**, 🏷️ **Precios**, 📊 **Cuentas**;
  - 🚪 **Cerrar el negocio** (con confirmación);
  - 🏠 **Inicio**.
- **Montar:**
  - el sector en la escena `montar`, con un desplegable;
  - el barrio en un desplegable con el alquiler y la demanda de cada uno;
  - nombre en un formulario, paleta y emblema en desplegables, con la vista previa en la escena;
  - una confirmación con lo que cuesta (tasa, fianza, acondicionamiento y la cuota de la semana).
- **Pintar antes del clic:** las pantallas vecinas (almacén, cuentas, portada) se pintan en segundo plano
  (`Painter` de `pisos`) y el siguiente cliente también, mientras atiendes al de ahora.

### Logros (categoría 🏪 Negocio, en el grupo 🪏 Trabajo)

Salen de las estadísticas de `business_stats` y de las de la jornada. Familias:

- escalones de turnos (también por sector), clientes, ventas y propinas;
- lo gastado en el mayorista, las compras, las noches con el negocio y los negocios cerrados;
- los clientes servidos rápido y los que se van sin atender;
- el mejor turno;
- el turno perfecto;
- la caja exacta y el ladrón pillado;
- el diagnóstico certero;
- los presupuestos caros aceptados;
- las 5 estrellas;
- habituales fieles y «lo de siempre» acertado;
- fracasos: la acusación falsa, el cambio de más o de menos, el presupuesto rechazado, el
  desahucio, cerrar en números rojos;
- colecciones: los tres sectores, todos los temas de barrio, las dos islas;
- fechas: madrugada, domingo, Día de Canarias;
- rachas de días abriendo;
- las 8 franjas usadas;
- el día de pluriactividad;
- la reducción y la excedencia;
- Hacienda: primera retención de la actividad, primer IGIC soportado;
- secretos.

### Equilibrio

Se calibra con `docs/negocio_sim.py`, que juega días enteros con los minijuegos y las reglas
reales. Cifras del 20/10/2026, con 28 días y la media desde el día 15, ya con las estrellas
asentadas. Neto al día, tras IRPF y cuota:

| | 4 turnos al día | 8 turnos al día |
|---|---|---|
| `pala` en obra (4 turnos de jornada) | P1 6.700 · P3 14.300 · P5 29.800 | — |
| Buen jugador (falla 1 de cada 8) | 7.500-18.000 | 18.000-38.000 |
| Jugador perfecto | 12.000-22.000 | 29.000-46.000 |
| Jugador flojo (falla 1 de cada 3) | pierde en la cafetería y el taller; 5.500-9.600 en la tienda | casi nada |

- **Montarlo** cuesta de 129.000 a 195.000 Y$ y se recupera entre el día 4 y el 28: lo que
  ahorra en una o dos semanas alguien con un P3.
- **Las 5 estrellas** llegan en dos a cuatro semanas a 4 turnos al día.
  - `REPUTATION` y `REPUTATION_CLIENTS` de `business.py` igualan el ritmo de los sectores:
    el taller, con 4 clientes por turno, sube por turno lo mismo que la cafetería.
  - Un error pesa como unos cinco aciertos.
- **La tienda** es el sector fácil y de poco margen: el flojo no se hunde (se queda en 3★).
- **Barrio y alquiler:** un barrio caro solo compensa con su demanda o subiendo precios.
  La cafetería de Santa Cruz (centro) gana más que la de Puerto de Mogán (lujo), que paga
  casi el doble de local.
- **Lo que no mira el simulador:**
  - la batería (con curro, cada turno del negocio la gasta como un turno de la `pala`);
  - la competencia en el barrio;
  - los niveles de precio distintos del normal;
  - el Falcon. En la fase 1 el negocio da lo de un P3-P5; los puestos altos de la `pala`
    siguen mandando al final.
- **El cupo de franjas** cambia la `pala` sola: quien no tiene negocio puede hacer como
  mucho 4 horas extra al día (8 turnos en total). Antes solo lo frenaban la batería y la
  familia, y quien ficha todo lo que puede sacaba unos 5 turnos al día (`docs/familia_pala.py`),
  así que el tope casi nunca se nota.
