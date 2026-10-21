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
| 2 | Crecer el local: mejoras que se amortizan, traslado a un local mayor o a uno tuyo de `pisos`, campañas de publicidad, reseñas, dos sectores más, precio por producto, traspasar o liquidar al cerrar | 9.5.0 |
| 3 | Sociedad limitada: caja propia, Impuesto sobre Sociedades, sueldo de administrador o dividendos, quiebra limitada, compatibilidades por oficio, pluriactividad | 9.6.0 |
| 4 | Empleados artificiales estilo inquilinos (rasgos, habilidades, formación, humor, contrato y despido), turnos pasivos, nómina completa con la cotización de la empresa (`compute_payslip`), ZEC y RIC | 8.x |
| 5 | Cadenas: varios locales, importar con nave, gerentes que automatizan, vender la empresa | 8.x |

Dónde engancha cada fase con lo de la 1:

- **Fase 2 (hecha).** `Business.size`, `equipment`, `campaign` y las reseñas multiplican los clientes de
  un turno (`turn_clients`); el traslado cambia `barrio`, `size` y `own_listing`. Detalle en «Fase 2 al
  detalle».
- **Fase 3 (hecha).** `Business.form` pasa a `"sl"` y `Business.company` lleva la sociedad. El dinero va a su
  cuenta (`economy.company_account`) y tributa en Sociedades, no en `INCOME_BUSINESS`. Detalle en «Fase 3 al
  detalle».
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

- Cada turno gasta 1 franja y batería (`SHIFT_COST`), tenga curro o no: la batería es de la persona.
  - Sin contrato se guarda aparte (`work_people`) y el inicio de `pala` la enseña.
  - Al firmar el primer oficio pasa al contrato: quien llega reventado del negocio no estrena batería.
  - Con la batería en `BATTERY_MIN` no se abre; por debajo de `TIRED`, los clientes esperan menos.
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
  - Son rendimiento de actividad económica (art. 27 LIRPF). El autónomo del juego opta por el criterio de
    cobros y pagos (art. 7.2 RIRPF, que es una opción y no la regla): ingreso al cobrar y gasto al pagar.
  - Retienen a cuenta con el tipo estable sobre el margen del turno (ventas menos el coste de lo vendido), en
    lugar del 20 % del pago fraccionado (art. 110.1.a RIRPF): una sola vara para toda la renta (Biblia).
  - Se integran con el resto de la base general (art. 48.a LIRPF): la Renta las suma a las nóminas.
  - El embargo del 30 % de quien debe a Hacienda alcanza a las ventas.
- **Compras al mayorista:** la base desaparece y el IGIC va al Estado.
  - El autónomo está en el régimen especial del pequeño empresario (arts. 89-91 del Decreto Legislativo
    1/2025 de Canarias): sus ventas están exentas (art. 90.1), así que no repercute IGIC y no deduce el
    soportado, que pasa a ser coste. El tipo general es el 7 % (art. 32.1).
  - Simplificación: no se mira el tope del régimen (30.000 € de facturación al año; 50.000 € desde 2027, y en
    julio de 2026 pudo optar quien no pasó de 50.000 € en 2025, DA 7.ª del Decreto-ley 3/2026).
  - Base más IGIC se apuntan como gasto de la actividad (`INCOME_BUSINESS` en negativo).
- **Montar el negocio:**
  - la tasa municipal de la apertura va al Estado, porque no hay ayuntamientos. Es la de la comunicación
    previa (art. 20.4.i TRLRHL): la Ley 7/2011 de Canarias para la cafetería y el taller, y la Ley 12/2012
    para la tienda (pequeño comercio);
  - la fianza del local son 2 mensualidades (art. 36.1 LAU) y va al depósito de fianzas
    (`HOUSING_DEPOSIT_ACCOUNT_ID`, como la de los pisos); vuelve al cerrar, menos lo que se deba;
  - el acondicionamiento lleva IGIC y se apunta entero como gasto de esa semana. Es una simplificación: la
    libertad de amortización (art. 12.3.e LIS, por el art. 28.1 LIRPF) solo vale para elementos nuevos de
    hasta 300 € cada uno y 25.000 € al año, y unas obras se amortizarían (fase 2).
- **Cada noche, el alquiler y los suministros:**
  - el alquiler del local lleva IGIC y la retención del 19 % del arrendador (arts. 100 RIRPF y 101.8 LIRPF), que el inquilino
    ingresa en el Estado; el resto desaparece (el casero no existe);
  - los suministros llevan IGIC;
  - se cobra lo que llegue del monedero y el resto queda como deuda del local;
  - con `EVICTION_NIGHTS` (3) noches sin pagar, el casero rescinde: el negocio cierra y la fianza cubre lo
    que pueda.
- **Cuota de autónomo:** cada lunes, `SELF_EMPLOYED_FEE` (la tarifa plana `FLAT_RATE_FEE` la primera semana,
  art. 38 ter de la Ley 20/2007) por `charge_self_employed_fee`.
  - La tarifa plana real fue de 80 € al mes de 2023 a 2025 (DT 5.ª del RDL 13/2022). Desde 2026 la fija cada
    Ley de Presupuestos; con los de 2026 prorrogados (RDL 3/2026), no hay cifra oficial nueva.
  - Si su puesto de la `pala` ya es de autónomo (reparto, influencer), no paga dos.
  - Con curro por cuenta ajena es pluriactividad: paga las dos cotizaciones. El reintegro del art. 313 LGSS
    se deja para la fase 3.
- **Mostrar la mordida:**
  - cada resultado con dinero lleva su línea de impuestos (🐶 IRPF a cuenta, IGIC);
  - el aviso de la Renta (`renta.remind`) y la mascota (`renta.hint` con el momento
    `Event.BUSINESS`: contenta con una S o una A, de bajón con una D);
  - `hacienda` cuenta el IGIC, las tasas y la retención del alquiler (`economy_business_tax`);
  - `saldo` enseña el alquiler de esta noche, la deuda del local y la cuota de la semana (`pending`).
- **El IMV y el banco ven el negocio** (`EconomyRepository.work_net`):
  - a las nóminas se suma lo que deja el negocio: ventas menos su retención y menos los gastos de la
    actividad (compras, local, apertura) de la misma ventana, 7 días para el IMV y 30 para el banco;
  - el IMV real mira todos los ingresos, también los de actividades económicas (art. 18 de la Ley 19/2021),
    y el banco los ve en la declaración;
  - nunca resta: una semana en pérdidas, o de montar y comprar stock, cuenta como cero. Así comprar no sube
    el IMV. La cuota de autónomo sí resta, como siempre;
  - con eso, quien solo tiene negocio ya no es «sin nómina» para los préstamos, y el VPO, el Bono Alquiler
    Joven y la garantía del alquiler lo cuentan como ingresos.

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
  - El primer cliente de un turno también: en la portada y en el tique, el panel sortea ya el azar del
    próximo turno (`TurnPlan`, `BusinessService.plan`) y pinta su primer cliente (`preview`). Al pulsar
    🔑 Abrir, `start_turn` gasta ese mismo azar: si nada ha cambiado (la hora, el stock, la batería), la
    imagen ya está pintada.

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
- colecciones: tres sectores y los cinco, todos los temas de barrio, las dos islas, las tres
  campañas de publicidad;
- fase 2: escalones de turnos en la heladería y la ferretería; reseñas (en total, de cinco
  estrellas y, ocultas las altas, de una), el distintivo de «recomendado»; mejoras instaladas,
  tres a la vez y lo gastado en ellas; campañas y lo gastado en publicidad; precio por
  producto; traslados, el local propio, el mediano y el grande; traspasos, el traspaso gordo
  y lo cobrado traspasando;
- fase 3, la sociedad: la primera y tres constituidas (oculto), el capital de 3.000 € y el de
  10.000 €; lo aportado, lo prestado y lo devuelto; los sueldos de administrador (cuántos y
  cuánto) y los dividendos (cuántos, oculto el alto, y cuánto); semanas declaradas en
  Sociedades y lo pagado; las semanas en pérdidas (ocultas); el mejor beneficio de una semana;
  liquidarla, hundirse y pagar sus deudas (oculto) y la gran cuota de liquidación; lo devuelto
  por la pluriactividad;
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
  - la batería (cada turno del negocio la gasta como un turno de la `pala`, con curro o sin él);
  - la competencia en el barrio;
  - los niveles de precio distintos del normal;
  - el Falcon. En la fase 1 el negocio da lo de un P3-P5; los puestos altos de la `pala`
    siguen mandando al final.
- **El cupo de franjas** cambia la `pala` sola: quien no tiene negocio puede hacer como
  mucho 4 horas extra al día (8 turnos en total). Antes solo lo frenaban la batería y la
  familia, y quien ficha todo lo que puede sacaba unos 5 turnos al día (`docs/familia_pala.py`),
  así que el tope casi nunca se nota.

### Medido (21/10/2026)

- **Dibujo** (`docs/medir_dibujos.py`, 4 hilos de este PC, dos pasadas, ms con 1 proceso abierto / en caliente):
  inicio 61 / 59, portada 64 / 63, cliente de la cafetería 61 / 63, cliente del taller 56 / 57 (la ficha de
  `pisos`, 75 / 75). En el NAS, ~1,5 veces más.
- **Node contra Chromium** (Chrome 155 de este PC, las 32 muestras): diferencia media de 0,9 a 2,2 sobre 255,
  por debajo de 4 en todas. La prueba de `tests/unit/test_business_scene.py` sigue saltándose aquí porque busca
  Chromium en `/opt/pw-browsers`.
- **Memoria:** el proceso de Node que siempre está abierto pasa de 78 a 131 MB con la escena cargada (~53 MB),
  lo que cuenta `docker-compose.yml` por escena. No sube el tope: cada proceso carga 3 escenas como mucho.

### Normas comprobadas en fuente oficial (21/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Estado |
|---|---|---|
| Art. 36.1 LAU | Fianza de dos mensualidades en el uso distinto de vivienda | Confirmado |
| Art. 110.1.a RIRPF | Pago fraccionado del 20 % del rendimiento neto en estimación directa | Confirmado |
| Art. 7.2 RIRPF | Criterio de cobros y pagos: es una opción, que dura 3 años | Corregido (antes se daba como regla) |
| Art. 48.a LIRPF | Los rendimientos se integran y compensan sin límite en la base general | Confirmado |
| Ley 7/2011 de Canarias, arts. 5.1, 34 y 35; Ley 12/2012, arts. 3-4; art. 20.4.i TRLRHL | Comunicación previa ante el ayuntamiento y su tasa municipal | Confirmado |
| Decreto Legislativo 1/2025 de Canarias, arts. 32.1 y 89-91 | IGIC general del 7 %; pequeño empresario exento, tope de 30.000 € (50.000 € desde 2027) | Corregido (antes, 50.000 € desde julio de 2026) |
| Ley 19/1994, arts. 29, 31, 43 y 44 | ZEC: IS al 4 %, 5 empleos en Gran Canaria y Tenerife (3 en el resto), inversión de 100.000 o 50.000 €, 1,8 M€ de base más 0,5 M€ por empleo de más | Confirmado. La inscripción acaba con el reglamento europeo de ayudas (31/12/2026, prorrogable); sus incentivos duran 6 años más |
| Art. 38 ter Ley 20/2007 | Tarifa plana 12 meses (y 12 más con rendimientos bajo el SMI) | Confirmado. El importe de 2026 no tiene cifra oficial |
| Arts. 100 RIRPF y 101.8 LIRPF | Retención del 19 % sobre el alquiler de un local | Confirmado (el 101.8 es de la ley, no del reglamento) |
| Art. 12.3.e LIS, por el art. 28.1 LIRPF | Libertad de amortización hasta 300 € por elemento y 25.000 € al año | Corregida la remisión (no es el art. 30 LIRPF) |

Para la fase 3: el RDL 3/2026 fija en 2026 el reintegro del 50 % de lo cotizado en pluriactividad por encima
de 17.323,68 €. Para la fase 4: el anexo de la ZEC no admite el comercio minorista ni la hostelería, pero sí la
reparación de vehículos (CNAE 45), así que solo el taller podría acogerse.

Sin comprobar: el articulado del Decreto 86/2013 y la cuota reducida de 2026.

## Fase 2 al detalle: crecer el local (9.5.0)

Lo que trae, en `business.py` (reglas), `business_catalog.py` (cifras), `negocio.py` (casos de uso),
`business_view.py` (pantallas) y `cogs/business.py` (panel). Decidido por Claude, como la fase 1; todo se
puede cambiar.

### Dos sectores más

- 🍦 **Heladería** (minijuego «Comandas»): helados de vainilla, chocolate y gofio, sorbete de mango,
  granizado de limón y batido. Es un local de 40 m². Las tardes y la playa mandan.
- 🔩 **Ferretería** (minijuego «La caja»): tornillería, bombillas, pintura, herramientas, fontanería y
  cerraduras. Es un local de 70 m², con pocos clientes y tiques caros. Mandan los barrios obreros y rurales.

### El tamaño del local (`SIZES`)

| Tamaño | Metros | Clientes por turno | Cupo del barrio | Suministros y obra |
|---|---|---|---|---|
| Pequeño | ×1 | ×1 | ×1 | ×1 |
| Mediano | ×1,6 | ×1,3 | ×1,25 | ×1,4 |
| Grande | ×2,4 | ×1,6 | ×1,5 | ×1,8 |

- El alquiler sale de los metros, como en la fase 1.
- Un local mayor llega a más clientes y saca más del cupo del barrio, que siguen compartiendo los negocios
  del mismo sector.

### Mejoras (`EQUIPMENT`) y amortización

- **Lo que hacen:** dos o tres por sector. Suben los clientes por turno, la paciencia o la propina, o bajan
  la merma.
- **La terraza** (cafetería y heladería) paga además una tasa municipal cada noche por ocupar la vía pública
  (art. 20.3.l TRLRHL). Va al Estado.
- **Comprarlas no es gasto:** base más IGIC (el pequeño empresario no lo deduce y forma parte del precio de
  adquisición, norma 2.ª del PGC) se amortizan cada noche (`amortize`).
  - Se usa la tabla simplificada (Orden de 27 de marzo de 1998, art. 30.1.ª RIRPF): instalaciones y
    mobiliario 10 %, maquinaria 12 %, equipos informáticos 26 %, útiles y herramientas 30 %.
  - El año fiscal es la semana: cada noche se amortiza la séptima parte del coeficiente.
  - La amortización es gasto de la actividad sin pago (`economy_tax_records`, `negocio:amortizacion`).
  - Interpretación del proyecto: con el criterio de cobros y pagos se sigue amortizando por la dotación,
    porque la amortización no es un pago (el art. 7.2 RIRPF no lo dice expresamente).
- **El acondicionamiento** de montar o trasladarse tampoco es ya gasto entero de la semana: es una
  instalación al 10 %.
  - Los negocios abiertos antes de la 9.5.0 ya lo apuntaron entero y no cambian.
  - La obra del local que se deja al trasladarse se queda allí, y su pérdida no se apunta (simplificación).

### Campañas de publicidad (`CAMPAIGNS`)

| Campaña | Coste (sin IGIC) | Días | Clientes y cupo |
|---|---|---|---|
| Buzoneo y carteles | 4.000 Y$ | 3 | +10 % |
| Anuncios en redes | 15.000 Y$ | 5 | +20 % |
| Cuña en la radio local | 45.000 Y$ | 7 | +30 % |

- Una a la vez.
- Es gasto entero de la actividad (art. 15.e LIS, por el art. 28.1 LIRPF: sin límite).
- Lleva el IGIC general.

### Precio por producto

Cada producto puede tener su propio nivel de precios (`Business.prices`), y los que no lo tienen siguen el
general:

- en el almacén, el desplegable «🏷️ Precio de este producto»;
- los clientes de un turno y lo que suma a la reputación son la media de los niveles, pesada por lo que se
  pide de cada producto (`price_clients`, `price_reputation`).

### Reseñas (`review_of`)

- **Quién escribe:** al acabar cada turno, un 25 % de los clientes atendidos y la mitad de los que se fueron
  sin atender (el enfadado escribe más).
- **Las estrellas** salen de la nota del cliente, con algo de azar, y un habitual es un poco más generoso.
- **El azar es propio del turno** (la semilla es la del negocio y la hora del turno): no gasta el de las
  colas.
- **Se guardan las 20 últimas.** Con 4,5 de media y 10 reseñas o más, el negocio sale **recomendado**: un
  10 % más de clientes.
- **Las frases** (`REVIEW_LINES`) son al menos la mitad normales.
- Inventar o encargar reseñas sería práctica desleal (art. 27.8 de la Ley 3/1991): el juego no deja.

### Trasladarse (`move`, `move_to_own`)

- **A un local alquilado:** de otro barrio, de otro tamaño o los dos.
  - Se paga de nuevo la tasa (el traslado necesita su comunicación previa, art. 34.2 de la Ley 7/2011 de
    Canarias), el acondicionamiento del local nuevo y su fianza.
  - Vuelve la fianza de ahora, menos lo que se deba al casero; lo que no cubra se paga antes de irse.
- **Cambiar de barrio** cuesta clientela: la reputación se acerca a la de partida a la mitad y los
  habituales pierden la mitad de su fidelidad.
- **A un local propio de `pisos`:** un local o una nave entera tuya (local para la cafetería, la tienda, la
  heladería y la ferretería; nave para el taller), vacía y sin anticipo de rentas.
  - El tamaño sale de sus metros (`size_for`): desde 0,75 veces los del sector cabe un pequeño, desde 1,3
    un mediano y desde 2 un grande.
  - Sin alquiler y sin fianza. Sus recibos (IBI, comunidad, seguro) los sigue pagando `pisos`.
    Simplificación: en la vida real serían gasto de la actividad, y la construcción se amortizaría al 3 %.
  - El inmueble pasa al estado `negocio` (`housing.OWN_BUSINESS`): no se alquila, no se vende (la venta
    voluntaria se para) y no imputa renta (art. 85.1 LIRPF, porque está afecto a la actividad, art.
    29.1.a).
  - Si deja de ser entero tuyo (una ejecución, una división), esa noche el negocio cierra («sin local») y se
    liquida.

### Cerrar: traspasar o liquidar (`closing_offer`)

- **Traspasar:** hace falta llevar 7 días abierto, 3 estrellas y estar en un local alquilado (quien se lo
  queda se subroga en el alquiler, art. 32 LAU).
  - Paga el stock al coste, las mejoras y la obra por lo que queda por amortizar (su valor neto contable,
    art. 40 RIRPF) y un **fondo de comercio**: tres días de lo que dejó de media la semana pasada, por las
    estrellas sobre cinco.
  - El stock es venta de la actividad (art. 27 LIRPF).
  - Lo demás es ganancia patrimonial de elementos afectos (art. 28.2 LIRPF), en la base del ahorro (art.
    46.b). La ganancia es el fondo de comercio (lo demás se cobra por su valor contable) y paga el 19 % en
    el momento, como el Rastro.
  - Sin IGIC: es una unidad económica autónoma (art. 9.1.º de la Ley 20/1991).
- **Liquidar** (cerrar sin traspaso, o por desahucio o sin local): el mayorista recompra el stock al 50 % y
  las mejoras se venden de segunda mano al 40 % de lo que queda por amortizar. La obra se pierde.
- En los dos casos vuelve la fianza, menos lo que se deba al casero.

### El IGIC de lo que se compra al mayorista

Comprobado en el Decreto Legislativo 1/2025 de Canarias (21/10/2026). La 9.5.0 corrige los productos de la
fase 1 que iban al 7 %:

- **Tipo cero (art. 33.Uno):** el pan, la leche, la fruta fresca, el café tostado (café solo y cortado) y el
  agua.
- **3 % (art. 34.Uno.e y a):** los demás alimentos (zumo, dulce, conservas, helados, sorbete y batido), los
  detergentes (limpieza) y la pintura.
- **7 % (art. 32.1):** la comida preparada (el bocadillo), los refrescos con azúcar (bebidas, granizado) y
  el resto (ferretería, salvo la pintura).
- **Interpretaciones del proyecto:**
  - el barraquito (café, leche condensada y licor, que iría al 15 %) va al general;
  - el zumo y el batido, al 3 %;
  - el granizado, como un refresco con azúcar.

### Pantallas nuevas

| `screen` | Para qué |
|---|---|
| `mejoras` | Las mejoras del sector (instaladas o no, con su amortización) y las tres campañas, con la que está en marcha |
| `resenas` | La media, el distintivo de recomendado y las seis últimas reseñas |

- **La portada** gana la media de las reseñas, la campaña en marcha, los iconos de las mejoras (la terraza se
  ve en la fachada) y el tamaño del local.
- **El almacén** gana el nivel de precio de cada producto.
- **`montar`** enseña los cinco sectores.
- **El traslado** se enseña con la vista previa de `negocio` en el barrio nuevo (`traslado_state`).
- **El panel:** 🛠️ **Mejoras** y ⭐ **Reseñas** en la fila de arriba, 🚚 **Traslado** en la de abajo, y en
  🚪 **Cerrar**, 🤝 **Traspasar** (si hay quien lo compre) o 🚪 **Cerrar y liquidar**.
- **Pintar antes del clic:** desde la portada y el tique se pintan las mejoras y las reseñas, además del
  almacén, las cuentas y el primer cliente.
- **Medido** (21/10/2026, este PC atado a 4 hilos): mejoras 61/65 ms y reseñas 59/62 ms (1 abierto /
  caliente); `montar`, con cinco fachadas, 98 ms. Node contra Chrome, peor diferencia 2,47/255. Con todas
  las pantallas, la escena suma ~74 MB al proceso de Node (eran ~53 en la fase 1).

### Equilibrio de la fase 2

`python docs/negocio_sim.py --fase2`: un jugador bueno en el barrio del medio de cada sector, con el cupo del
barrio y la amortización, media de 6 semillas y días 15 a 28. Neto al día y en cuántos días se paga lo
invertido (las campañas ya restan lo que cuestan):

| Sector | Sin nada (4 / 8 turnos) | Mejoras (4 turnos) | Local mediano | Local grande | Campañas (al día) |
|---|---|---|---|---|---|
| Cafetería | 16.800 / 23.900 | 11-18 días | 15-17 días | 37-39 días | +1.100 a +3.800 |
| Tienda | 14.000 / 18.300 | 12-32 días | 24-27 días | 28-33 días | +700 a +2.700 |
| Taller | 5.900 / 16.700 | 13-19 días | 26-82 días | 33-98 días | −1.000 a +1.700 |
| Heladería | 12.800 / 16.200 | 14-19 días | 13-15 días | 19 días | +800 a +2.100 |
| Ferretería | 17.100 / 23.800 | 13-20 días | 17-27 días | 18-30 días | +900 a +4.000 |

- **Las mejoras que suben los clientes** rinden a quien abre pocos turnos (4 al día, con curro). A 8 turnos el
  cupo del barrio ya se agota y suman poco: ahorran franjas, no dan más dinero.
- **El local mayor** sube el techo, porque le toca más cupo. Es la mejora de quien abre 8 turnos.
- **Las mejoras de paciencia** (TPV, diagnosis) cuestan menos porque al jugador rápido le sirven poco. A quien
  tarda en atender, le sirven más.
- **El taller con 4 turnos** gana poco y las campañas no le compensan: tiene pocos clientes y caros.
- Las cifras de los sectores de la fase 1 cambian un poco por el IGIC corregido y por el cupo del barrio, que
  la tabla de la fase 1 no miraba.

### Normas de la fase 2 comprobadas (21/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Uso |
|---|---|---|
| Orden de 27-3-1998; art. 30.1.ª RIRPF | Tabla simplificada: 10 / 12 / 26 / 30 % (y 3 % los edificios) | Amortización de mejoras y obra |
| Norma 2.ª del PGC (RD 1514/2007); art. 35.1.b LIRPF | El impuesto indirecto no recuperable va al precio de adquisición | El IGIC del pequeño empresario se amortiza |
| Art. 7.2 RIRPF | El criterio de cobros y pagos es para ingresos y gastos; no dice nada de amortizar | Interpretación: se amortiza igual |
| Art. 20.3.l TRLRHL | Tasa por mesas y sillas en la vía pública | La terraza |
| Art. 15.e LIS (art. 28.1 LIRPF) | Lo que promociona las ventas es deducible | Publicidad, entera |
| Art. 32 LAU | Cesión del local al transmitir el negocio, sin consentimiento, con un 20 % más de renta y aviso en un mes | El traspaso (la renta del comprador no nos toca) |
| Arts. 27, 28.2, 33.1, 35.1, 46.b LIRPF; art. 40 RIRPF | Las existencias son rendimiento; los elementos afectos, ganancia del ahorro sobre el valor contable | Tributación del traspaso |
| Art. 9.1.º Ley 20/1991 | No sujeta la transmisión de una unidad económica autónoma | Traspaso sin IGIC |
| Arts. 29.1.a y 85.1 LIRPF; art. 22 RIRPF | El inmueble donde se ejerce la actividad está afecto y no imputa renta | Local propio |
| Art. 27.8 Ley 3/1991 | Encargar o inventar reseñas es práctica engañosa | Las reseñas no se compran |
| Art. 34.2 Ley 7/2011 de Canarias | El traslado necesita nueva comunicación previa | La tasa del traslado |
| DL 1/2025 de Canarias, arts. 32-36 | Tipos del IGIC | Tabla de arriba |

Sin comprobar:

- si una tienda o una heladería entran en la Ley 7/2011 o solo en la declaración responsable municipal;
- qué es «modificación sustancial» al ampliar el local;
- las consultas de la DGT y de la Agencia Tributaria Canaria sobre helados y bocadillos.

## Fase 3 al detalle: la sociedad limitada (9.6.0)

Reglas en `business_sl.py` (puras), casos de uso en `negocio.py`, el dinero en `EconomyService.business` (con
`BusinessMove.account`), la pantalla `sociedad` en `business_view.py` y el panel 🏢 **Sociedad** en
`cogs/business.py`. Verificado en el BOE consolidado el 21/10/2026 (tabla al final).

### Constituirla

- **Solo desde un negocio de autónomo:** se convierte el suyo, no se monta una nueva desde cero.
- **Qué hace falta:**
  - el negocio tiene que estar en un local alquilado: una sociedad en un local del socio sería una
    operación vinculada (art. 18 LIS), que el juego no lleva;
  - su curro tiene que dejarle administrarla (abajo, «Incompatibilidades»).
- **El capital**, a escoger: 1 €, 3.000 € o 10.000 € (100, 300.000 o 1.000.000 Y$).
  - Desde la Ley 18/2022, el mínimo es 1 € (art. 4.1 LSC).
  - Por debajo de 3.000 €, el 20 % del beneficio va a la reserva legal hasta que capital y reserva sumen
    3.000 €, y al liquidar el socio responde de lo que falte hasta 3.000 € (art. 4.1 LSC).
- **Lo que cuesta:**
  - notaría y registro con estatutos tipo: 60 € y 40 € con hasta 3.100 € de capital, o 150 € y 100 €
    (art. 5 del RDL 13/2010), con su IGIC;
  - la constitución está exenta del ITP-AJD (art. 45.I.B.11 TRLITPAJD).
  - El dueño mete en la caja el capital y lo de la notaría (aportación de socio), y la sociedad lo paga:
    es su primer gasto.
- **El negocio pasa entero a la sociedad** por lo que valía en sus cuentas (stock, mejoras, obra, fianza).
  - Es la aportación de una rama de actividad, neutral en el IRPF (art. 87 LIS) y no sujeta al IGIC (art.
    9.1.º de la Ley 20/1991).
  - Lo de la semana anterior a constituirla es del autónomo: no entra en Sociedades
    (`company["offset"]`).
- **Sin el 15 % de las entidades de nueva creación:** la actividad ya la ejercía como autónomo su socio
  único (art. 29.1.b LIS). Tributa como microempresa desde el primer día.
- **La cuota de autónomo sigue igual:** el dueño pasa a autónomo societario (art. 305.2.b LGSS). Su base
  mínima en 2026, 1.424,40 € al mes por el 30,6 %, es lo que ya cobra el juego. Al convertirlo no hay tarifa
  plana: no es un alta inicial (art. 38 ter de la Ley 20/2007).

### La caja es suya

- **Las ventas** van a la caja de la sociedad, sin retención de IRPF y sin los embargos de las deudas del
  dueño.
- **De la caja salen** las compras, las mejoras, la publicidad, el local, el traslado y, cada noche, la
  gestoría (1.000 Y$ con su IGIC).
- **Si la caja no llega**, el dueño tiene que meter dinero: el negocio no tira de su monedero.
- **`saldo`** ya no le enseña el local como algo que pague él.

### Del socio a su sociedad y al revés

- **💶 Aportar:** aportación de socio (cuenta 118 del PGC). No es ingreso de la sociedad ni se devuelve: se
  recupera con dividendos o al liquidarla. Exenta como operación societaria.
- **🏦 Prestar:** sin intereses. Simplificación: entre socio y sociedad, el art. 18 LIS pediría valor de
  mercado. Se devuelve cuando hay caja (↩️ **Devolverme**). Al liquidar, cobra el último: es subordinado
  (arts. 281 y 283 TRLC).
- **💼 Sueldo de administrador:**
  - es rendimiento del trabajo del dueño (art. 17.2.e LIRPF);
  - retiene el 35 %, o el 19 % si la sociedad factura menos de 100.000 € al año (art. 101.2 LIRPF, art.
    80.1.3.º RIRPF). La facturación se mide con lo vendido la semana pasada, pasado al año con la vara del
    IRPF;
  - entra en la Renta del lunes y, como una nómina, en el IMV y en el banco;
  - en Sociedades es gasto deducible: no es una liberalidad (art. 15.e LIS), porque los estatutos tipo
    prevén la retribución (art. 217 LSC).
- **💰 Dividendos:**
  - solo de lo disponible tras Sociedades y la reserva legal, y sin dejar el patrimonio neto por debajo del
    capital (art. 273 LSC);
  - con deuda con Hacienda, nada;
  - es rendimiento del capital mobiliario (art. 25.1.a LIRPF) con retención del 19 % (art. 101.4).
    Simplificación: como en el Rastro, ese 19 % es lo que paga, sin la escala del ahorro (19/21/23/27/30 %).

### El Impuesto sobre Sociedades

Cada domingo por la noche, la última de la semana fiscal (el año del juego).

- **La base** son las partidas de la semana (`TAX_ITEMS`):
  - ventas, compras, local, publicidad, amortización, tasas, sueldo, gestoría y notaría;
  - por devengo: el local cuenta la noche que toca, aunque no llegue para pagarlo;
  - no entran la cuota de autónomo (es del dueño), los dividendos ni el propio impuesto.
- **Las pérdidas** se compensan con las semanas siguientes sin límite (art. 26 LIS: el 70 % empieza en el
  millón de euros).
- **Microempresa** (factura menos de un millón al año): el 19 % hasta 50.000 € de base y el 21 % el resto
  (art. 29.1 LIS y disposición transitoria 44.ª de la Ley 7/2024 para 2026). Si factura más, el general,
  el 25 %. Los tramos en euros se pasan a la semana con la vara del IRPF.
- **Se paga de la caja.** Lo que no llegue queda como deuda con Hacienda: se cobra antes que nada las
  noches siguientes.
- Simplificación: no hay pagos fraccionados (art. 40 LIS); se liquida entero cada semana.
- **Del beneficio tras el impuesto**, primero la reserva legal: el 10 % hasta el 20 % del capital (art. 274
  LSC), o el 20 % del art. 4.1. Lo demás queda disponible para dividendos.

### Quiebra y responsabilidades

- **Causa de disolución:** si las pérdidas dejan el patrimonio neto por debajo de la mitad del capital (art.
  363.1.e LSC), el panel avisa.
  - El patrimonio neto es caja, stock y lo que queda por amortizar, menos deudas y el préstamo del socio.
  - Con dos noches (los dos meses del art. 367 LSC) sin remediarlo, el socio responde de todas sus deudas
    al liquidarla.
- **Liquidarla** (al cerrar, traspasarlo, en un desahucio o sin local):
  1. lo cobrado entra en la caja;
  2. se paga el último Sociedades, con el traspaso o la liquidación de las mejoras y la obra en la base;
  3. luego Hacienda, el casero y el préstamo del socio, por ese orden;
  4. lo que quede es la cuota de liquidación del dueño, con el 19 % de lo que pase de lo que metió.
- **Lo que la sociedad no llegue a pagar:**
  - a Hacienda se lo paga el administrador (derivación, art. 43.1.b LGT): lo que no tenga, a su deuda;
  - al casero, el socio lo cubre hasta lo que falte para 3.000 € de capital, o entero si dejó la causa de
    disolución sin remediar;
  - si no, se pierde: es una sociedad de responsabilidad limitada (art. 1 LSC).

### Incompatibilidades (`INCOMPATIBILITIES`)

Si el curro de la `pala` no deja administrar una sociedad, no se puede constituir ni cobrar sueldo de
administrador. Los dividendos sí: ser socio no está prohibido. De excedencia no hay incompatibilidad.

| Curro | Por qué | Norma |
|---|---|---|
| Política (todo el oficio) | Dedicación exclusiva de los altos cargos y los diputados | art. 13 Ley 3/2015; art. 157 LOREG |
| Funcionariado, Alta Administración | El complemento específico lleva el factor de incompatibilidad | art. 16.1 Ley 53/1984 |
| Sanidad pública | Personal estatutario con complemento por incompatibilidad | art. 76 Ley 55/2003; art. 16 Ley 53/1984 |
| Oficina, unicornio | Pacto de plena dedicación | art. 21.1 ET |
| Hostelería, con cafetería o heladería | Concurrencia desleal con tu empresa | art. 21.1 ET |

- Simplificación: el resto del funcionariado necesitaría el reconocimiento previo de compatibilidad (art. 14
  Ley 53/1984); el juego lo da por concedido.
- Las incompatibilidades miran solo la sociedad: el negocio de autónomo no cambia.

### Pluriactividad (art. 313 LGSS)

- **Quién:** quien cotiza a la vez por cuenta ajena (sus nóminas de la `pala`) y en el RETA (la cuota del
  negocio).
- **Cuánto:** cada domingo recupera el 50 % de lo que pase de 17.323,68 € al año (RDL 3/2026, art. 3.5),
  con el tope del 50 % de lo pagado al RETA. El límite se pasa a la semana con la vara.
- **Lo paga el Estado.** Simplificación: no tributa (en la vida real reduce el gasto de la cuota).
- Vale también para el autónomo, no solo para la sociedad.

### El Patrimonio

- Las participaciones cuentan a su valor teórico (art. 16.Uno de la Ley 19/1991): caja, stock y lo que
  queda por amortizar, menos lo que debe la sociedad (`repositories.business.companies_in`).
- Lo que le debe al socio no se resta: es un crédito del propio dueño.
- Salen en `perfil`, en `fortunas` y en el Impuesto sobre el Patrimonio.
- Simplificación: no se aplica la exención de la empresa familiar (art. 4.Ocho.Dos).

### Equilibrio

`python docs/negocio_sim.py --sociedad`: lo que se lleva el dueño al bolsillo cada semana, según el beneficio
del negocio antes de sus impuestos, con el mejor reparto entre sueldo y dividendos. La cuota del RETA es igual
en los dos casos.

| Beneficio por semana | Autónomo | Sociedad | Mejor sueldo | Diferencia |
|---|---|---|---|---|
| 50.000 | 37.561 | 32.506 | 42.500 | −5.055 |
| 100.000 | 76.959 | 75.154 | 72.150 | −1.805 |
| 250.000 | 178.252 | 174.911 | 184.300 | −3.341 |
| 400.000 | 263.029 | 273.343 | 180.550 | +10.314 |
| 700.000 | 420.454 | 465.756 | 180.050 | +45.302 |
| 1.200.000 | 677.954 | 785.703 | 190.800 | +107.749 |

- En impuestos, la sociedad compensa a partir de unos 300.000 Y$ de beneficio a la semana (unos 60.000 € al
  año con la vara), como en la vida real.
- Un negocio de la fase 2 deja 100.000-210.000 Y$ a la semana: solo los grandes (local grande, 8 turnos)
  llegan.
- Por debajo, la sociedad sirve para limitar lo que se arriesga, no para pagar menos.
- El mejor sueldo ronda los 180.000 Y$ a la semana: hasta ahí el IRPF del trabajo pesa menos que Sociedades
  más el 19 % de los dividendos.

### Normas de la fase 3 comprobadas (21/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Uso |
|---|---|---|
| Art. 4.1 LSC (Ley 18/2022) | Capital mínimo de 1 €; por debajo de 3.000 €, reserva del 20 % y responsabilidad hasta 3.000 € | Capital y liquidación |
| Arts. 273 y 274 LSC | Dividendos solo si el patrimonio neto no baja del capital; reserva legal del 10 % hasta el 20 % del capital | Dividendos y reserva |
| Arts. 363.1.e y 367 LSC | Causa de disolución por pérdidas; el administrador responde si no convoca en dos meses | El aviso y responder de todo |
| Art. 45.I.B.11 TRLITPAJD; RDL 13/2010, art. 5 | Constitución exenta; aranceles de 60 + 40 € o 150 + 100 € | Lo que cuesta |
| Art. 29.1 LIS y DT 44.ª (Ley 7/2024) | 25 % general; microempresa en 2026, 19 % hasta 50.000 € y 21 % el resto; el 15 % de nueva creación no vale si el socio ya ejercía la actividad | Los tipos |
| Art. 26 LIS | Compensación de bases negativas | Las pérdidas |
| Art. 15.e LIS; art. 217 LSC | La retribución del administrador no es liberalidad si los estatutos la prevén | El sueldo es gasto |
| Arts. 17.2.e y 101.2 LIRPF; art. 80.1.3.º RIRPF | El sueldo del administrador es trabajo; retención del 35 %, o del 19 % con menos de 100.000 € de facturación | La retención |
| Arts. 25.1.a, 46, 66, 76 y 101.4 LIRPF | Dividendos en la base del ahorro, retención del 19 %; escala 19/21/23/27/30 % | Los dividendos |
| Art. 305.2.b LGSS; Orden PJC/297/2026 | El administrador con control es autónomo societario; base mínima de 1.424,40 € en 2026 | La cuota |
| Art. 38 ter Ley 20/2007 | Tarifa plana solo con alta inicial | Sin tarifa al convertir |
| Art. 87 LIS; art. 9.1.º Ley 20/1991 | Aportación de rama de actividad neutral; no sujeta al IGIC | Constituirla |
| Art. 18 LIS | Operaciones vinculadas a valor de mercado | No al local propio; el préstamo sin intereses es simplificación |
| Arts. 281 y 283 TRLC | El préstamo del socio es subordinado | Cobra el último |
| Art. 313 LGSS; RDL 3/2026, art. 3.5 | Reintegro del 50 % de lo cotizado por encima de 17.323,68 €, con tope | La pluriactividad |
| Art. 21.1 ET; arts. 14 y 16 Ley 53/1984; Ley 55/2003; arts. 13 y 15 Ley 3/2015; art. 157 LOREG | Concurrencia, plena dedicación y las incompatibilidades públicas | Incompatibilidades |
| Art. 1 LSC; art. 43.1.b LGT | Responsabilidad limitada; derivación al administrador de los impuestos que queden | La quiebra |

Sin comprobar:

- la deducibilidad del sueldo de un administrador único sin estatutos (doctrina);
- el efecto fiscal concreto del préstamo gratuito socio-sociedad;
- el importe de la tarifa plana de 2026.

