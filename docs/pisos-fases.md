# Pisos: fases 2 a 6 de la ampliación (diseño)

Diseño de lo que falta del plan de ampliación de `pisos` (la fase 1 salió en la 7.22: 180
inmuebles, barrios con carácter e índice vivo). Va encima de la 8.0.0 (rama `economia-8`),
porque usa su IRPF: la reducción del 70 % por inquilino joven, el impuesto de primas de los
seguros y las deducciones canarias del casero. Una versión por fase: 8.1.0 a 8.5.0.

Las normas están comprobadas el 18/10/2026 en el BOE y la AEAT (al final, las fuentes y lo
que no se pudo confirmar). Las cifras de juego se calibran con `docs/pisos_sim.py` y
`docs/economia_sim.py` antes de fijarlas: lo de abajo es el punto de partida.

## Principios

- **Lo de hoy no cambia para quien no toca nada.** La gestora sigue eligiendo inquilino al
  azar, los imprevistos se arreglan solos y los alquileres siguen como hasta ahora: quien
  gestiona bien gana algo más, quien no hace nada cobra como hoy. Una prueba comprueba que
  la media de los inquilinos que elige la gestora deja el impago, las marchas y las averías
  donde están.
- **Ningún bucle de dinero.** Cada cosa nueva se mide contra la ida y vuelta de comprar y
  vender (~11 % con ITP, notaría, agencia y su IGIC): reformar y vender, comprar en subasta y
  revender o encadenar deducciones nunca sale a cuenta. El seguro de impago cuesta algo más de
  lo que paga de media (la aseguradora gana), como en la vida real.
- **Toda pantalla nueva es imagen**, como el resto del panel, y se pinta por adelantado. La
  ficha ya tiene los cinco renglones de Discord llenos: lo nuevo del dueño va en una pantalla
  🧰 Gestionar que se abre desde la ficha.

## Fase 2 (8.1.0): inquilinos con personalidad, elegir inquilino, seguro de impago y noticias de barrio

### Inquilinos

- Cada inquilino tiene **edad**, **riesgo de impago** (multiplica la probabilidad diaria de
  dejar de pagar), **estabilidad** (multiplica la de irse antes y la de no renovar),
  **desgaste** (multiplica averías y humedades: niños, perro, despedidas), dos o tres
  **rasgos** que se ven en su tarjeta («Plaza fija», «Contrato temporal», «Con perro», «Paga
  por adelantado», «Pagan sus padres») y frases para cada momento: firma, renueva, se va,
  deja de pagar y se queja.
- De 16 personas y 9 negocios a unas 40 personas y 16 negocios: la abuela con su pensión, la
  cajera, el guagüero, los estudiantes de la ULL, el camarero fijo discontinuo, el piloto de
  Binter, la ingeniera de una startup, el directivo trasladado de Madrid, los ciclistas que
  entrenan en invierno, el bar de toda la vida, la peluquería, la academia de inglés, la
  escuela de vela…
- Las frases que tiran de la nacionalidad (el inglés, el alemán, el Erasmus, los británicos, la
  despedida alemana) se reescriben: los personajes se quedan, el chiste pasa a la situación.

### Elegir inquilino

- Cada inmueble tiene modo: **🤖 La gestora elige** (el de siempre y el de serie) o **✋ Elijo
  yo**. Lo cambia quien decide la renta.
- Con «Elijo yo», cada noche que el piso está vacío puede llegar un candidato (con la misma
  probabilidad que hoy de encontrar inquilino), hasta 3 a la vez. Cada uno espera 3 días. La
  pantalla 👥 Candidatos enseña su tarjeta (edad, rasgos, riesgo bajo, medio o alto,
  estabilidad, si la aseguradora lo cubre, su frase) y se firma con uno o se descarta.
- **Reducción del 70 %** (art. 23.2.b.1.º LIRPF, Ley 12/2023): vivienda que se alquila por
  primera vez, en zona tensionada, a un inquilino de 18 a 35 años. «Por primera vez» se mira en
  el propio inmueble: sin contrato anterior apuntado. Vale también si la gestora acierta.
  Elegir permite buscarla.
- **Deducción canaria del art. 16** (DL 1/2009): 1.000 € (a la vara `W`) una vez por inmueble
  por alquilar como vivienda habitual uno que imputó renta (estuvo vacío) la semana anterior,
  con contrato de 3 años o más (todos los de vivienda son de 5). Máximo 5 inmuebles por
  persona, sin garajes ni trasteros. Solo una deducción canaria por inmueble (art. 18): la más
  alta entre 15 ter, 15 quater y 16. Es un premio único y con tope: no se puede repetir.

### Seguro de impago

- El seguro obligatorio de hoy («del hogar con impago», 3 % de la renta) no paga nada. Pasa
  a ser **seguro del hogar** (1,5 % de la renta de referencia) y aparte se puede contratar el
  **🛡️ seguro de impago**. Los dos llevan el impuesto de primas del 8 % (Ley 13/1996).
- El de impago paga la renta desde el segundo mes sin pagar (el primero es de franquicia),
  hasta 12 meses por impago, y el abogado y el procurador del desahucio. Prima de partida: un
  4 % de la renta al mes con renta de referencia, más con la de mercado (donde se deja de
  pagar más), menos con la asequible. Se calibra para que la aseguradora gane un 20 %.
- Como en la vida real, solo cubre a quien pasa el estudio de solvencia (riesgo bajo o
  medio) y solo vivienda habitual: ni nómadas (temporada), ni vacacional, ni locales.
- **Desahucio con coste:** abogado y procurador son obligatorios (art. 23 LEC). Tres meses de
  renta, entre 3.000 y 15.000 Y$, con su IGIC. Sin seguro, lo paga el dueño.
- **Deducción canaria del art. 15 quater:** el 75 % de la prima del seguro de impago, hasta
  150 € (a `W`), si la renta no pasa de 800 € al mes. Incompatible con la 15 ter en el mismo
  inmueble: se aplica la más alta.

### Noticias de barrio

- Cada lunes, entre 0 y 3 barrios reciben una noticia de un catálogo de unas 40, según su
  carácter: abre un Mercadona, obras de la MetroGuagua, rodaje de una serie, llega la fibra de
  1 Gb, cierra la fábrica, festival de música, botellón en la plaza, nuevo centro de salud,
  avistan un tiburón, Carnaval (en febrero), calima…
- Dura 7 días y cambia la demanda (encontrar inquilino, nómadas, turistas), el impago, las
  averías o las quejas del vecino. Alguna mueve el índice un 1-2 % de una vez, siempre muy por
  debajo de la ida y vuelta: no se puede comprar antes de saberla ni sale a cuenta especular.
- Se ve en el mapa (📰 en la chincheta), en el barrio y en la ficha. Los lunes sale el
  resumen en `#chat-general`, en el mismo mensaje que el BCE, si alguien tiene pisos.

## Fase 3 (8.2.0): compraventa entre miembros y subastas

### Vender a un miembro

- Desde 🧰 Gestionar: participaciones, precio y a quién. Le llega una oferta pública con
  Aceptar, Rechazar y Retirar, como el alquiler a un miembro. Puede pagar al contado o con
  hipoteca (el banco presta el 80 % del menor entre el precio y el valor).
- **Impuestos:**
  - El comprador paga el **ITP del 6,5 % sobre el mayor entre el precio y el valor de
    referencia** (art. 10.2 del RDL 1/1993 tras la Ley 11/2021). El valor de referencia es
    el 90 % del de mercado de hoy (factor 0,9 de la Orden HFP/1104/2021). Paga también la
    notaría y su IGIC.
  - El vendedor no paga agencia. Su ganancia se calcula con el mayor entre el precio y el
    valor de mercado (art. 35.2 LIRPF: si vendes barato, Hacienda mira lo que valía) y paga
    el 19 %.
  - La parte regalada (precio por debajo del valor) va exenta de Donaciones por decisión del
    proyecto, como en el Rastro y el Bizum.
- **No mueve el índice** del barrio: no entra ni sale nada del mercado.
- El contrato del inquilino sigue (art. 14 LAU) y las deudas del piso también (art. 9.1.e
  LPH). La oferta las enseña. La hipoteca del vendedor se cancela en la parte vendida con lo
  que cobra.
- **Tanteo del inquilino** (art. 25 LAU): si vendes entera una vivienda en la que vive un
  miembro de alquiler, él recibe aviso y puede quedársela al mismo precio mientras dure la
  oferta. Desde el RDL 29/2026 no se puede renunciar a él.
- **Retracto de comuneros** (art. 1522 CC): si vendes participaciones a quien no es
  copropietario, los otros dueños pueden quedárselas al mismo precio.

### Subastas entre miembros

- Desde 🧰 Gestionar: participaciones y puja mínima. Duran 24 horas y se anuncian en
  `#chat-general`.
- Se puja desde la pantalla 🔨 Subastas del panel, con un formulario. Cada puja sube al menos
  un 1 %. Lo pujado espera en un depósito y se devuelve entero a quien superan. Si alguien
  puja en la última hora, la subasta se alarga una hora, para que no gane quien puja en el
  último segundo.
- Sin pujas no se vende nada. Pagan los mismos impuestos que una venta a un miembro.

### Subastas judiciales

- Cuando el banco ejecuta una hipoteca, lo hipotecado no se lo queda al momento: sale a
  subasta 24 horas (en la vida real, 20 días en el Portal de Subastas del BOE). Se anuncia y
  puja cualquiera.
- Reglas (art. 670 LEC, simplificado):
  - Puja mínima, el 50 % del valor.
  - Gana la mejor puja si llega al 70 %.
  - Por debajo del 70 % solo gana si no es la vivienda habitual del ejecutado, o si cubre toda
    la deuda y llega al 60 %.
- Lo cobrado paga las deudas del piso y al banco. Si sobra, es para el ejecutado; si falta,
  se le sigue debiendo (no hay dación en pago), como hoy.
- El comprador paga el **ITP de subastas, 7 %** (art. 31.f del DL 1/2009) sobre el mayor
  entre la puja y el valor de referencia (TSJ de Galicia, 2026).
- **Lo adjudicado no se puede vender al mercado en 30 días** (a otro miembro, sí). Sin eso,
  comprar al 70 % y revender al 100 % sería un pelotazo seguro.
- **Subasta sin pujas:** el banco se la queda al 70 % del valor, como hoy (decidido; ver abajo).

## Fase 4 (8.3.0): reformas e imprevistos que se deciden

### Reformas

- Las hace quien decide, con el inmueble vacío o siendo su casa. Mientras duran está «En
  obras»: no se alquila y sigue pagando IBI y comunidad.

  | Reforma | Coste de partida | Días | Efecto |
  |---|---|---|---|
  | Pintura y arreglos | 20 €/m² | 2 | renta +3 % |
  | Cocina nueva | 9.000 € | 5 | renta +5 % |
  | Baño nuevo | 6.000 € | 4 | renta +4 %, humedades −50 % |
  | Eficiencia energética | 120 €/m² | 7 | certificado a B o A, renta +4 % |
  | Reforma integral | 700 €/m² | 20 | renta +12 %, averías y humedades −60 % |
  | Acondicionar el local o la nave | 150 €/m² | 7 | renta +8 % |

- **Valor:** cada reforma suma al precio del inmueble el 60 % de lo que costó (con un
  máximo de +25 % entre todas). Reformar para vender pierde dinero; se gana con la renta. Las
  mejoras se quedan en el inmueble aunque cambie de dueño o vuelva al mercado.
- **Impuestos:**
  - La obra lleva **IGIC del 7 %**: la reforma de una vivienda libre va al tipo general,
    art. 32 del DL 1/2025, el texto refundido del IGIC.
  - Lleva también el **ICIO del 4 %** (art. 102 del RDL 2/2004), que va al Estado porque no
    hay ayuntamientos, más una tasa de licencia fija.
  - Para el IRPF es una mejora, no un gasto: suma a lo que costó el inmueble (art. 35.1
    LIRPF), se amortiza y suma al valor del Patrimonio.
- **Reducción del 60 %** (art. 23.2.c LIRPF y art. 41.1 RIRPF): si la reforma integral cuesta
  más del 25 % de lo que se pagó, sin contar el suelo, es rehabilitación. Los contratos que se
  firmen en los 24 días siguientes (dos años) tienen el 60 %, si no les toca el 90 o el 70.

### Imprevistos que se deciden

- Cuando hay una avería, humedades, una cerradura o una queja del vecino, el dueño tiene 2
  días para elegir qué hacer (en la ficha y en «Mis pisos»). Si nadie elige, la gestora lo
  arregla como hoy.

  | Imprevisto | Opciones |
  |---|---|
  | Avería | Técnico oficial (100 %); la chapuza del cuñado (35 %, 40 % de que vuelva); que espere |
  | Humedades | Repararlas bien; pintar encima (20 %, 60 % de que vuelvan); dar parte al seguro del hogar (franquicia de 150 €) |
  | Cerradura | Cerrajero de urgencia; mañana por la mañana (50 %, el inquilino se molesta) |
  | Vecino del 3.º | Hablar con él (gratis, se arregla la mitad de las veces); mediación (cuesta); ignorarlo |
  | Derrama e ITE | Son obligatorias (art. 10 LPH): se pagan como siempre, de golpe |

- Si la avería espera, el inquilino puede repararla él y descontarla de la renta, con un 20 %
  más (art. 21.5 LAU desde el RDL 29/2026: el casero tiene 15 días para contestar).
- **Humor del inquilino** (de 0 a 100, se ve en la ficha): baja con lo que no se arregla o se
  arregla mal y sube con lo que se arregla bien. Cambia lo que renueva y lo que se va antes.
- Las reparaciones son gasto deducible (intereses y reparaciones con tope, desde la 8.0.0) y
  llevan IGIC del 7 %.

## Fase 5 (8.4.0): vacacional con reseñas

### Reseñas y nota

- Tras cada estancia, el huésped deja nota (de 1 a 5 estrellas) y una frase, 7 de cada 10
  veces. La ficha enseña la media de las últimas 20 y las tres últimas reseñas.
- La nota sale de:
  - el huésped: la despedida es exigente y ruidosa; los nórdicos, tranquilos;
  - los muebles que tiene el dueño;
  - la limpieza;
  - los imprevistos sin arreglar y las quejas del vecino;
  - si el barrio le pega (surfistas en la playa, senderistas en el campo).
- **Efecto:**
  - La nota mueve la ocupación de ×0,5 (1,5 estrellas o menos) a ×1,3 (4,8 o más).
  - Con 4,8 o más y 10 reseñas eres **Superanfitrión**: +10 % de precio.

### Muebles «con tenerlos basta»

- Pasillo nuevo de la tienda, 🛋️ Muebles del vacacional, con 10 artículos: Smart TV,
  aire acondicionado, cafetera de cápsulas, colchón viscoelástico, wifi de 1 Gb (también
  atrae nómadas), kit de playa, cuna y trona, barbacoa, cerradura inteligente y juegos de mesa.
- Llevan IGIC del 7 % y valen en todos los vacacionales de quien los tiene, como las
  herramientas de `pala`. Algunos suman más en su tipo de barrio: el kit de playa en la playa,
  la barbacoa en el campo.

### Gastos y ley

- **Gastos de cada estancia:** limpieza (30 € con su IGIC) y la comisión de la plataforma
  (15 % con su IGIC).
- **Ley 6/2025 de Canarias, de 10 de diciembre:**
  - La licencia dura 5 años (60 días) y se renueva si el barrio no está tensionado.
  - En zona tensionada no hay licencias nuevas (art. 5.1).
  - Obra nueva no puede ser vacacional: pide 10 años de antigüedad.
  - En un bloque hace falta que lo aprueben 3/5 de la comunidad (arts. 7.3 y 17.12 LPH, desde
    abril de 2025). Votan los vecinos ficticios o, en un edificio de miembros, la junta.
  - Sigue habiendo una plaza por barrio.

## Fase 6 (8.5.0): junta de copropietarios

- **Dónde:** en cualquier inmueble con varios dueños miembros (decidido).
- **Propuestas:** cualquier copropietario puede proponer:
  - la renta que se pide;
  - el seguro de impago y el modo de elegir inquilino;
  - una reforma o la licencia vacacional;
  - quién administra (el que elige inquilino y decide los imprevistos);
  - una derrama al fondo de reserva;
  - vender el inmueble entero.
- **Votación:** 24 horas, por participaciones.
  - Mayoría de cuotas para administrar (art. 398 CC).
  - 3/5 para obras de mejora y vivienda vacacional (arts. 17.4 y 17.12 LPH).
  - Unanimidad para vender todo o alterarlo (art. 397 CC).
  - Quien no vota cuenta a favor (art. 17.8 LPH: el silencio de 30 días vale como sí).
- **Fondo de reserva** (art. 9.1.f LPH): el 10 % de la comunidad del edificio se guarda en un
  fondo que paga las derramas y la ITE.
- **Acción de división** (arts. 400 y 404 CC), en cualquier inmueble con varios dueños:
  - Un copropietario puede forzar la venta del inmueble entero.
  - Primero va a subasta de 24 horas entre miembros, con el valor de mercado como mínimo.
  - Si nadie puja, se vende al mercado y se reparte.

## En todas las fases

- **Logros:** entre 25 y 40 por fase, en 🏠 Pisos: escalones, mala suerte, secretos, fechas,
  fiscalidad con `story`.
- **El resto, como cualquier funcionalidad:**
  - la mascota en cada resultado y el aviso de la Renta en lo que mueve dinero;
  - botones que contestan antes de tocar la base de datos;
  - pruebas: el libro cuadra y el Estado recibe exactamente lo retenido;
  - documentación (FUNCIONALIDADES, Biblia, README);
  - versión y CHANGELOG.
- **Antes de cerrar cada fase**, el simulador vuelve a medir el día del Falcon con pisos (hoy,
  obra 366, oficina 359 y hostelería 388). Gestionar bien puede adelantarlo un poco, pero no
  debe bajar de los 330.

## Decidido por el usuario (18/10/2026)

1. **Subasta judicial sin pujas:** el banco se la queda al 70 %, como hace hoy el bot,
   aunque la LO 1/2025 cambió el art. 671 LEC (decisión del proyecto: se documenta como
   simplificación).
2. **Junta:** en cualquier inmueble con varios dueños miembros (copropiedad del Código Civil),
   no solo en los edificios enteros.
3. **Desahucio con coste,** en Y$: tres meses de renta, entre 3.000 y 15.000 Y$, más su IGIC.
   Lo paga el seguro de impago si lo hay.

## Normas comprobadas (18/10/2026) y avisos

- **Comprobado en el BOE:**
  - LAU, arts. 14, 19, 21, 21 bis, 22, 25 y 36 (con el RDL 29/2026);
  - Código Civil, arts. 392-404 y 1522;
  - LPH, arts. 7.3, 9, 10, 16 y 17 (17.12 desde la LO 1/2025);
  - LEC, arts. 647 (el depósito para pujar es el 10 %), 649 (20 días), 670 y 671 (LO 1/2025);
  - LIRPF, arts. 23.2 (Ley 12/2023) y 35.2, y RIRPF, art. 41.1;
  - ITP, art. 10 del RDL 1/1993 y arts. 31.a y 31.f del DL 1/2009;
  - DL 1/2009, arts. 15 ter, 15 quater, 16 y 18;
  - ICIO, arts. 100-103 del RDL 2/2004;
  - IGIC: DL 1/2025, el texto refundido de octubre de 2025, que renumera la Ley 4/2012;
  - Ley 6/2025 de Canarias, de uso turístico de viviendas.
- **Sin confirmar:**
  - si el Congreso convalida el RDL 29/2026, que cambia la escala del 23.2 y la definición de
    gran tenedor: se sigue con la Ley 12/2023, como se decidió el 17/10;
  - el texto literal del art. 1524 CC (plazo del retracto, 9 días);
  - que el factor 0,9 siga en 2026;
  - criterio del Supremo sobre el ITP en subastas (solo hay una sentencia del TSJ de Galicia);
  - que el IPS al 8 % se aplique en Canarias (no hay fuente oficial directa; la Ley 11/2020
    lo subió al 8 % y no excluye Canarias);
  - lo de alargar una hora la subasta si se puja al final: es regla del juego, no de la LEC.
- **Aviso:** el Registro Único estatal de alquileres (RD 1312/2024) lo anuló en parte el
  Supremo (STS 620/2026, de 19/5/2026): no se usa.
- **Aviso:** los textos del bot citan el IGIC como «art. 51 de la Ley 4/2012». Desde el
  21/10/2025 es el art. 32 del DL 1/2025. Hay que actualizar las citas.
