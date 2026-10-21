# Garantías del banco: rehipotecar, anticipo de rentas y avalista

Pedido el 21/10/2026. Decisiones del proyecto: **empeñar va en `vender`** (el Monte de Piedad,
9.0.0) y **pignorar va en `banco`**, en un mostrador nuevo, 🔐 Garantías. Se pignoran tres
cosas: los pisos (rehipotecar), las rentas de un alquiler (anticipo) y la firma de otro miembro
(avalista). Los objetos de la mochila no: para eso está el Monte.

Este documento es el contrato de las tres partes. Las cifras son de partida y se calibran con
`docs/economia_sim.py` (el Falcon con pisos tiene que seguir entre los días 350 y 390).

## Lo que ya había

- El banco no existe: lo que presta se crea y lo que cobra desaparece. Un día del bot es un mes.
- Todas las cuotas juntas (hipotecas y préstamos) no pasan del 35 % de lo que ingresas
  (`housing.affordable` con `Market.installments_of`), con las nóminas de 30 días y el 70 % de
  las rentas.
- Hipoteca al comprar: el 80 % del precio a 360 meses, al Euríbor + 1 %. Ejecución con 12 cuotas
  o el 3 % del capital sin pagar (Ley 5/2019), subasta judicial de un día y, sin pujas, el banco
  al 70 %; lo que no cubre queda como deuda con el banco (sin dación en pago).
- Préstamos personales: `bot.services.loans` y `housing_loans`; ASNEF con 2 cuotas, vencimiento
  con 3, embargo del art. 607 LEC.
- El Patrimonio **ya resta las deudas** (`repositories.housing.assets_in`, art. 25 de la Ley
  19/1991): hipotecas, préstamos y deuda con el banco; la hipoteca de la vivienda habitual no,
  porque el bien está exento. Lo que no las resta es lo que se enseña: `perfil` (🏰 Patrimonio) y
  `fortunas`.

## 1. Rehipotecar (9.1.0)

Una hipoteca nueva sobre participaciones que ya tienes, para sacar dinero de lo que vale tu
inmueble («equity release»). Se pide en 🔐 Garantías de `banco` o con 🏦 Rehipotecar en la
ficha del inmueble de `pisos`, que abre el banco con ese inmueble elegido.

- **Tasación:** lo que valen hoy tus participaciones en el mercado (`Market.value`, con el índice
  del barrio). La paga quien pide (art. 14.1.e de la Ley 5/2019): el 0,2 % de lo tasado, con un
  mínimo de 2.500 Y$ (250 €), más el IGIC general del 7 % (servicio profesional), que va al
  Estado. Se cobra al firmar.
- **Notaría, registro, gestoría y AJD:** los paga el banco (Ley 5/2019 y RDL 17/2018). El banco
  no existe: no se mueve dinero.
- **Cuánto:** hasta el **80 % de lo tasado si es vivienda** (`HOME_KINDS`) y el **60 % si no**
  (locales, garajes, trasteros, naves), los límites del mercado hipotecario (RD 716/2009),
  **menos lo que ya debes de ese inmueble** (todas sus hipotecas). Mínimo 10.000 Y$.
- **Tipo y plazo:** fijo, Euríbor + 1,5 % (medio punto más que la de compra: va detrás en el
  rango). Plazos de 120, 240 o 360 meses. Comisión de apertura del 0,5 %, que desaparece.
- **El 35 %:** su cuota cuenta como cualquier otra y tiene que caber.
- **No se puede:** en el fichero de morosos, con deuda con Hacienda (lo prestado se embargaría),
  con el inmueble en subasta o en una acción de división, ni sobre una VPO (precio tasado:
  decisión del proyecto).
- **Rango y ejecución:** si cualquiera de tus hipotecas de un inmueble llega a la ejecución, sale
  a subasta lo tuyo de ese inmueble una sola vez y lo que dé paga **todas** tus hipotecas de ese
  inmueble, de la más antigua a la más nueva (prioridad registral); lo que no cubra queda como
  deuda con el banco. Es el mismo banco, así que el vencimiento de una arrastra a las demás.
  *Arreglo de paso:* hasta ahora, con dos hipotecas sobre el mismo inmueble (comprar más
  participaciones con hipoteca), ejecutar una dejaba la otra viva sin inmueble detrás, y podían
  salir dos subastas por lo mismo.
- **Vender:** como hasta ahora, lo que se debe de todas las hipotecas del inmueble se cancela con
  lo cobrado; si no llega (el barrio bajó), pones la diferencia.
- **IRPF:** sus intereses **no** restan de la renta del inmueble (art. 23.1.a.1.º LIRPF: solo los
  del dinero invertido en comprarlo o mejorarlo), y su cuota no cuenta para la deducción canaria
  por inversión en vivienda habitual (art. 14 del DL 1/2009). Se guarda como `kind = 'rehipoteca'`
  en `housing_mortgages`.
- **Patrimonio:** resta siempre, también la de la vivienda habitual: no se pidió para comprar un
  bien exento. Y `perfil` y `fortunas` enseñan las deudas y el patrimonio neto (activos menos
  deudas), para que pedir prestado no suba a nadie en la lista.
- **Amortizar:** como cualquier hipoteca (`pisos` y el mostrador), sin comisión (decisión del
  proyecto, como las demás).

## 2. Anticipo de rentas (9.2.0)

El banco te adelanta hoy varios meses de lo que te paga el inquilino de un inmueble y se cobra
él esa renta hasta saldarlo (cesión de rentas futuras en garantía).

- **Qué inmueble:** uno tuyo (también participaciones) **alquilado** (no vacío, ni okupado, ni
  vacacional, ni tu vivienda habitual), sin rentas atrasadas y con al menos tantos meses de
  contrato por delante como pidas.
- **Cuánto:** 3, 6 o 12 meses de **tu parte de la renta de hoy** (`cuota`). El banco te da hoy
  el valor actual de esas cuotas al Euríbor + 3 %, menos una comisión de apertura del 1 %.
- **Cada noche:** la cuota se cobra como la de un préstamo, después de cobrar la renta. Si el
  inquilino se va o no paga, la pones tú (con recurso): sin pagar, como un préstamo (ASNEF con
  2, vence con 3 y pasa a deuda con el banco).
- **El 35 %:** no hace falta que quepa (lo paga el inquilino), pero su cuota cuenta en el 35 %
  para lo siguiente que pidas. Uno por inmueble y no cuenta en el tope de 3 préstamos.
- **Vender:** con un anticipo vivo no se vende el inmueble (el banco no consiente): se cancela
  antes. Si lo pierdes por la fuerza (ejecución, división), el anticipo sigue y lo pagas tú.
- **Desistir y amortizar:** como un préstamo personal (Ley 16/2011).
- **IRPF:** la renta tributa cada noche como siempre, cuando se devenga (art. 14.1.a LIRPF), no
  al adelantarla; el coste del anticipo no deduce. Se guarda en `housing_loans` con
  `kind = 'rentas'` y `listing`.

## 3. Avalista (9.3.0)

Al pedir un préstamo personal o una rehipoteca, puedes poner a otro miembro de avalista.

- **La petición:** sale en el canal mencionándolo, con ✍️ Avalar y ✖️ No solo para él (y ↩️
  Retirar para ti), y caduca a los 10 minutos. Al aceptar se firma, volviendo a comprobarlo todo.
- **Lo que aporta:** el banco suma a lo que te cabe en tu 35 % lo que le cabe al avalista en el
  suyo, y el tipo baja medio punto («Con avalista»).
- **Riesgo indirecto (CIRBE):** mientras dure, la cuota avalada cuenta en el 35 % del avalista
  para lo que pida él, y la ve en `banco` y en `saldo`. Cada miembro avala como mucho 2 cosas a
  la vez. Ni en el fichero de morosos ni con deuda con Hacienda.
- **Si no pagas** (fianza solidaria, sin beneficio de excusión, como en los bancos: arts. 1822,
  1831 y 1144 CC): cuando el préstamo vence, o la ejecución de la rehipoteca no cubre, lo que
  queda pasa a **deuda con el banco del avalista** (se cobra cada noche, embargo del 607, ASNEF).
  Tú quedas debiéndole eso al avalista (**acción de reembolso**, art. 1838 CC): se cobra cada
  noche de tu monedero y va al suyo. El impago cuenta en tu historial.
- **Patrimonio:** lo avalado no resta al avalista hasta que se lo reclaman (art. 25 de la Ley
  19/1991); entonces resta su deuda con el banco. Lo que le debes es deuda tuya y un derecho de
  cobro suyo.

## Logros (🏦 Banco)

Cada parte con los suyos, de todas las familias de la Biblia: primeras veces, escalones de lo
sacado, lo más grande y lo más pequeño, mala suerte (te ejecutan la rehipoteca, el inquilino se
va con el anticipo vivo, te reclaman un aval), fechas, social (avalar, que te avalen, pagar el
reembolso entero) y secretos.

## Por comprobar en fuente oficial

Lo verifica un subagente contra el BOE; el resultado va en la sección siguiente.
