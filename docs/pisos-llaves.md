# Pisos: duración del contrato y recuperar el piso (9.23.0)

Diseño acordado el 25/10/2026. Hasta la 9.22 todo contrato de vivienda duraba 5 años (60 días)
y al inquilino que pagaba no había forma de sacarlo: el ⚖️ Desahuciar solo vale contra el
impago y los okupas. Ahora el casero elige cuánto firma y tiene tres caminos para recuperar
el piso, del gratis y lento al caro y rápido.

## La ley de verdad (a 25/10/2026) y lo que hace el juego

- **Vigente:** la LAU (art. 9.1, cinco años; art. 9.3, necesidad del arrendador; art. 10.1,
  no renovar avisando con 4 meses, sin pagar nada) y la prórroga de la Ley 12/2023 en zona
  tensionada (art. 10.2 LAU: anual, a petición del inquilino).
- **Tumbados por el Congreso:** RDL 8/2026 (28/04/2026) y RDL 26/2026 (2/10/2026).
- **Pendientes de la Diputación Permanente:** RDL 29/2026 (vigente desde el 8/10: prórroga
  extraordinaria de hasta 2 años) y RDL 28/2026 (entraría el 15/11: 12 mensualidades por no
  renovar). El juego no los aplica hasta que se convaliden.
- **Lo que el juego cambia a propósito:** en la ley, un contrato de menos de 5 años se
  prorroga cada año a voluntad del inquilino hasta los 5 (art. 9.1 LAU). Aquí, la duración
  que se firma es la de verdad: al acabar, el casero puede no renovar. Si no, elegir la
  duración no cambiaría nada.

## Duración del contrato (🧰 Gestionar)

- De 1 a 5 años (12, 24, 36, 48 o 60 días; `housing.TERMS`). Vale para el próximo contrato,
  como la renta (`Lease.term`; 0 = la de serie, 60). Los nómadas (temporada) y el vacacional
  tienen la suya, y una habitación no pasa de 12 (`ROOM_MONTHS`).
- **Lo que cuesta firmar corto** (`term_factors`), en proporción a lo que le falta para
  los 5 años: menos gente lo quiere (encontrar inquilino, hasta un `TERM_FIND_CUT` menos con
  1 año) y quien lo firma paga peor (impago, hasta un `TERM_RISK_UP` más).
- **Lo que gana:** cada vez que acaba, puedes no renovar gratis y volver a alquilar con el
  índice de ese día. Recuperarlo antes sale más barato, porque le queda menos.
- Al acabar, si no se ha dicho nada, renueva como siempre (`RENEWAL`) con la misma renta y la
  misma duración (la que esté elegida ese día).

## Los tres caminos (🔑 Recuperar el piso, en la ficha)

1. **🚪 No renovar**: gratis; el inquilino se va el día que acaba el contrato. En zona
   tensionada pide la prórroga de la Ley de Vivienda una vez (`TENSE_EXTENSION`; un miembro,
   siempre): +12 días y luego se va.
2. **🏠 Lo necesito para vivir** (art. 9.3 LAU): con 12 días de contrato cumplidos, siendo
   todo tuyo y sin vivir en otro sitio. Se va en `NEED_NOTICE_DAYS` días y te mudas tú solo
   esa noche. Tienes que vivir ahí `NEED_STAY_DAYS` días; si te mudas antes, pagas al
   inquilino 1 mes de renta por cada 12 días que le quedaban (`need_fine`). Mientras tanto no
   puedes venderlo ni irte a vivir a otro sitio.
3. **🤝 Comprar las llaves**: le pagas para que se vaya en `KEYS_MOVE_DAYS` días (sigue
   pagando la renta hasta entonces).
   - **Precio justo** (`keys_fair`): medio mes de renta por cada 12 días que le quedan
     (mínimo un mes), por su apego (el inverso de su `stay`, de 0,7 a 1,5), su humor
     (contento, hasta ×1,3; enfadado no abarata: si no, dejarle las averías sin arreglar
     sería la estrategia) y tu fama. Si los alquileres han subido desde que firmó, pide además
     la mitad (`KEYS_MARKET_SHARE`) de lo que le costaría de más alquilar uno igual hasta que
     acabe su contrato: es lo que de verdad pierde al irse.
   - **Tres botones con la probabilidad en cada uno**: 🙏 Por las buenas (gratis,
     `free_odds`), 🤝 Justa (×1) y 💰 Generosa (×1,5). Su precio de verdad es el justo por
     un factor lognormal fijo para cada contrato (`keys_reserve`): con la justa acepta un 60 %
     de las veces y con la generosa un 90 %.
   - **Si dice que no**, su humor baja `KEYS_MOOD_HIT` y contraoferta con su precio
     (`keys_ask`), que puedes aceptar o dejar. No vuelve a hablar del tema en
     `KEYS_WAIT_DAYS` días. Un inquilino de riesgo alto al que le ofreces poco (por las
     buenas o la justa) se atrinchera con una probabilidad de `TRENCH_CHANCE`: deja de pagar
     y toca el ⚖️ de siempre.
   - **Fama de casero** (`fame`): cada piso que recuperas así en 30 días encarece el siguiente
     un `FAME_STEP`, apaga el «por las buenas» y espanta a los candidatos (encontrar
     inquilino, un 10 % menos por punto, hasta la mitad). Se olvida sola: son las noticias
     `llaves` de los últimos 30 días.
   - **A un miembro de alquiler** no se le impone nada: le llega la oferta en el canal con
     Aceptar, Contraoferta y Rechazar, y el dinero se lo queda él.

## Dinero y fiscalidad

- **Llaves a un inquilino del catálogo:** lo pagado desaparece (el inquilino no existe). Sin
  IGIC: quien cobra es un particular, no un empresario (art. 4 de la Ley 20/1991). Para el
  casero es una mejora, no un gasto deducible (DGT V3259-23, según su resumen: el texto
  íntegro no se pudo leer): suma a lo pagado y al coste de su parte, como una reforma.
- **Llaves a un miembro:** va del casero al miembro. Para el miembro es una ganancia
  patrimonial de la base del ahorro (arts. 33.1 y 49 LIRPF, DGT V3259-23), sin retención en
  la ley; aquí se le descuenta el 19 % al cobrarla, como la plusvalía del Rastro (`plusvalia`).
- **Indemnización del art. 9.3** (te mudas antes de tiempo): la misma cuenta que unas llaves,
  pero no es una mejora (no compra nada).
- No renovar, la duración y la prórroga no mueven dinero.

## Calibrar

`python docs/pisos_sim.py --duraciones`: renta cobrada con cada duración (sin hacer nada y no
renovando cuando el índice ha subido) y lo que gana o pierde comprar las llaves según cuánto
haya subido el índice. La meta: firmar corto no rinde más por sí solo, y echar a alguien para
subirle la renta solo compensa cuando el barrio se ha disparado.

Resultado del 25/10/2026 (`--seeds 40`, un piso2 de referencia; renta cobrada al día entre la
de referencia de ese día, menos abogados y llaves):

| Duración | Quieto, sin hacer nada | Olas, sin hacer nada | Olas, no renovando si sube un 15 % |
|---|---|---|---|
| 1 año | 0,857 | 0,892 | 0,917 |
| 2 años | 0,883 | 0,920 | 0,958 |
| 3 años | 0,913 | 0,937 | 0,981 |
| 4 años | 0,895 | 0,920 | 0,970 |
| 5 años | 0,922 | 0,947 | 0,938 |

Firmar corto sin hacer nada pierde hasta un 7 %; quien vigila el barrio y no renueva a tiempo
gana hasta un 3-4 % sobre el contrato de 5 años: premia jugar, no es un grifo.

Comprar las llaves (generosa) a un inquilino con 48 días por delante cuando el barrio ha
subido, en meses de su renta: sin la parte de lo que le costaría alquilar otro, con un 20 % se
ganaban 5 meses (el abuso); con ella, −3,0 con un 10 %, −2,0 con un 20 % y −0,2 con un 40 %.
Con la justa (si la acepta, 6 de cada 10): −0,7 con un 10 %, a la par con un 15 %, +1,5 con un 20 % y +5,7 con un 40 %.
