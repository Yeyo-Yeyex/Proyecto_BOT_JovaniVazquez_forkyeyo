# Auditoría de las ONGs (`donar`, 15/10/2026)

Repaso de `bot.services.donations`, `bot.cogs.donations` y la deducción de
`bot.services.taxes.donation_deduction`, con lo que salió y lo que se cambió.

## Lo que está bien

- **Fiscalidad real.** La deducción es la del art. 19.1 de la Ley 49/2002 tras la
  reforma de 2024: el 80 % de los primeros 250 € al año (desde la 8.0.0, unos
  1.202 Y$ a la semana con la vara del IRPF; antes, 2.500 Y$) y el 40 % del resto,
  con el límite del 10 % de la base (art. 69.1 LIRPF). La deducción rebaja la cuota
  de la declaración del lunes y nunca la deja en negativo. El donativo no paga IGIC
  (no hay contraprestación, art. 4 de la Ley 20/1991) ni Donaciones (la ONG no es persona física, art. 3 de la Ley 29/1987). No
  se modela el 45 % de los donativos recurrentes (tres años a la misma entidad): con
  la renta semanal del bot no tendría sentido.
- **Es un sumidero limpio.** Lo donado se queda en el monedero de la ONG (ids
  negativos) y no vuelve a nadie; lo que se recupera sale del Estado y como mucho es
  el 80 % de lo donado, así que donar siempre cuesta. Los monederos de las ONGs no
  salen en `fortunas`, ni en el Patrimonio, ni en las estadísticas de miembros (todas
  esas consultas piden `user_id > 0`).
- No se puede donar 0 ni en negativo (`EconomyService.donate`) ni más de lo que se
  tiene, y `/donar` lleva el aviso de la Renta.

## Lo que se ha cambiado

- **Logros:** había 8 (lo donado en cinco escalones y las cuatro ONGs). La Biblia pide
  al menos una docena por funcionalidad y todas sus familias. Ahora son 19: además,
  donativos por número (10, 100 y 1.000), 10.000 Y$ a cada ONG (uno por ONG),
  Navidad, y secretos con guasa (1 Y$, 666 Y$ y donar todo el saldo). Salen de
  `donation_stats`.
- **Frases:** cada ONG tenía cuatro maneras de gastarse tu dinero; ahora nueve, con
  Waterloo, Koldo, los puros de Bibi, Davos, el tapón pegado y la motosierra de Milei.
- **Mascota:** al donar hablaba de dinero en general (`Moment()`); ahora tiene su
  momento (`Event.DONATE`) con frases propias.

## Segunda auditoría (26/10/2026): más juego

La tarea de la lista pedía que donar fuera más interesante, no más fiscal. Donar solo
servía para desgravar, y con cuatro ONGs y un embed fijo no había nada que mirar ni
perseguir. Lo que se ha hecho:

- **Ocho ONGs.** Cuatro nuevas con temas que no tenían las de antes: videojuegos
  (🎮 Gamers Sin Fronteras), redes (🤳 Protectora de Influencers Abandonados), Canarias
  (🍌 Salvemos el Plátano Canario) y el propio bot (🤖 Amigos del Bot Jovani). Monederos
  `-5` a `-8`.
- **Panel.** `donar` sin cantidad abre un `LayoutView` con la portada (regla, tus niveles
  y el muro de donantes) y la ficha de cada ONG en un menú, con botones para donar. Los
  botones aceptan el clic antes de leer la base de datos (`test_button_speed`).
- **Mecenas.** Lo donado a cada ONG da un nivel con ella (Bronce 5.000, Plata 25.000, Oro
  100.000 y Patrono 500.000 Y$). Es el objetivo a largo plazo que faltaba: cada nivel
  regala algo y da más boletos en el sorteo.
- **Sorteo de los viernes.** Cada ONG sortea un regalo entre quienes le donaron en los
  siete días anteriores. Va los viernes a las 20:00 para no amontonarse con la Renta y el
  Patrimonio de los lunes. Se apunta en `economy_ong_raffles` (una vez por ONG y
  sorteo), así que un reinicio no lo repite.
- **Regalos.** El premio suele ser algo de la tienda de hasta 500 Y$ (los mismos que da
  la caja botín, `shop.giftable`). Un 15 % de las veces, y siempre al llegar a Oro y a
  Patrono, es uno de los exclusivos de la ONG: artículos ocultos del surtido, como las
  piezas de las cajas, que no se venden. Los regalos no mueven dinero.
- **Logros:** 15 más (42 en total): las 8 ONGs, los cuatro niveles de mecenas, los
  sorteos ganados, los regalos y los exclusivos.

Se quedó fuera una deducción canaria por donativos (art. 4 ter del DL 1/2009, un 20 %
más de los primeros 150 €). Es real, pero haría que donar el primer tramo saliera gratis.

### Tercera tanda (9.27.0)

- **Diez ONGs.** Dos más picantes, con sátira de instituciones y figuras públicas (Biblia,
  «Personalidad»): 👑 Fundación Ejemplaridad Borbónica (el emérito, Abu Dabi, Botsuana) y
  🦅 Fundación Techo Para Todos (un fondo buitre y la vivienda). Monederos `-9` y `-10`.
- **100 exclusivos**, diez por ONG.
- **91 logros de ONGs** (antes 42): Oro y Patrono de cada ONG, mecenas de varias ONGs,
  colecciones de exclusivos (por ONG y en total), más sorteos y regalos, el mayor
  donativo, fechas (madrugada, Día de Canarias, la hora antes del sorteo) y secretos (69,
  420, justo lo que faltaba para subir de nivel, la Corona un 14 de abril).

### Entrada al sorteo y rarezas calculadas (9.28.0)

- **Entrada:** hasta la 9.27.0 bastaba con donar 1 Y$ para entrar en el sorteo de una ONG.
  Donar 1 Y$ a cada una costaba 10 Y$ a la semana y daba hasta diez regalos de hasta 500
  Y$, que se podían vender. Ahora hay que donarle en la semana al menos el tramo que
  desgrava al 80 % (`RAFFLE_MIN` = `DONATION_FULL_LIMIT`, 1.202 Y$).
- **Sorpresa:** los exclusivos ya no salen en la ficha de la ONG. Se sabe que existen,
  pero no cuáles son.
- **Rarezas:** `docs/auditoria_ongs.py` juega diez años de sorteos con las reglas reales y
  saca la rareza de la escala de la Biblia. Se calibra con quien dona la entrada a las 10
  ONGs cada semana, que es quien persigue estos logros, con 2 rivales por sorteo (el
  informe del 17/10 tenía 3 personas usando `donar` en un mes). Cambiaron: 5 sorteos (de
  Épico a Raro), 10 (de Legendario a Épico), 25 (de Mítico a Épico), el primer regalo (de
  Común a Raro: el primer sorteo llega el viernes), 25 regalos (de Legendario a Épico), 50
  regalos (de Mítico a Legendario) y 5 exclusivos de una ONG (de Legendario a Mítico).
  Se añadieron 50 y 150 sorteos y 200 regalos. Con más rivales todo tarda más: se vuelve a
  pasar si cambia mucho el número de donantes.
  `tests/unit/test_patrimonio_donativos.py` comprueba que el catálogo cuadra con la
  simulación.

Entrada: 1202 Y$ por ONG y semana · 2 rivales por sorteo

## Donando a 10 ONGs (12020 Y$ a la semana)

| Logro | Meta | Días (mediana) | Pide | Tiene |
|---|---|---|---|---|
| ong_raffle_1 | 1 | 7 | Raro | Raro |
| ong_raffle_5 | 5 | 14 | Raro | Raro |
| ong_raffle_10 | 10 | 21 | Épico | Épico |
| ong_raffle_25 | 25 | 56 | Épico | Épico |
| ong_raffle_50 | 50 | 105 | Legendario | Legendario |
| ong_raffle_150 | 150 | 315 | Mítico | Mítico |
| ong_gift_1 | 1 | 7 | Raro | Raro |
| ong_gift_10 | 10 | 21 | Épico | Épico |
| ong_gift_25 | 25 | 35 | Épico | Épico |
| ong_gift_50 | 50 | 84 | Legendario | Legendario |
| ong_gift_200 | 200 | 378 | Mítico | Mítico |
| ong_exclusive_1 | 1 | 14 | Raro | Raro |
| ong_exclusive_5 | 5 | 42 | Épico | Épico |
| ong_exclusive_15 | 15 | 161 | Legendario | Legendario |
| ong_owned_3 | 3 | 35 | Épico | Épico |
| ong_owned_10 | 10 | 112 | Legendario | Legendario |
| ong_owned_25 | 25 | 301 | Mítico | Mítico |
| ong_owned_100 | 100 | 1866 | Mítico | Mítico |
| ong_set_5 | 5 | 270 | Mítico | Mítico |
| ong_set_10 | 10 | 672 | Mítico | Mítico |

## Donando a 3 ONGs (3606 Y$ a la semana)

| Logro | Meta | Días (mediana) | Pide | Tiene |
|---|---|---|---|---|
| ong_raffle_1 | 1 | 7 | Raro | Raro |
| ong_raffle_5 | 5 | 35 | Épico | Raro ⚠️ |
| ong_raffle_10 | 10 | 70 | Legendario | Épico ⚠️ |
| ong_raffle_25 | 25 | 175 | Legendario | Épico ⚠️ |
| ong_raffle_50 | 50 | 350 | Mítico | Legendario ⚠️ |
| ong_raffle_150 | 150 | 1050 | Mítico | Mítico |
| ong_gift_1 | 1 | 7 | Raro | Raro |
| ong_gift_10 | 10 | 49 | Épico | Épico |
| ong_gift_25 | 25 | 147 | Legendario | Épico ⚠️ |
| ong_gift_50 | 50 | 308 | Mítico | Legendario ⚠️ |
| ong_gift_200 | 200 | 1337 | Mítico | Mítico |
| ong_exclusive_1 | 1 | 35 | Épico | Raro ⚠️ |
| ong_exclusive_5 | 5 | 168 | Legendario | Épico ⚠️ |
| ong_exclusive_15 | 15 | 588 | Mítico | Legendario ⚠️ |
| ong_owned_3 | 3 | 98 | Legendario | Épico ⚠️ |
| ong_owned_10 | 10 | 410 | Mítico | Legendario ⚠️ |
| ong_owned_25 | 25 | 1043 | Mítico | Mítico |
| ong_owned_100 | 100 | nunca (10 años) | Mítico | Mítico |
| ong_set_5 | 5 | 399 | Mítico | Mítico |
| ong_set_10 | 10 | 875 | Mítico | Mítico |

### Más logros (9.29.0)

- **Sorteos jugados, perdidos y ganados el mismo viernes:** la simulación cuenta ahora
  también `ong_raffle_entered`, `ong_raffle_lost` y `ong_raffle_week_max`. Con 10 ONGs y 2
  rivales: entrar 1 vez, 7 días (Raro); 50, 35 (Épico); 200, 140 (Legendario); 1.000, 700
  (Mítico). Perder 10, 14 días (Raro); 50, 56 (Épico); 200, 210 (Legendario); 1.000, más
  de 1.000 (Mítico). Ganar 2 el mismo viernes, 7 días (Raro); 5, 25 (Épico); 7, 228
  (Legendario); 8, unos 1.300 (Mítico).
- **Sin simular** (dependen de cuántos donan en el servidor): ganar sin rivales (Raro,
  secreto), ganar con un boleto contra alguien con 5 o más (Épico, secreto), ser quien más
  ha donado a una ONG (Raro) o encabezar el muro (Épico).
- **Fechas** (Comunes, como pide la Biblia): viernes 13, Halloween, Nochevieja, Reyes, el
  Pino y los Inocentes (secreto). **Cifras con guasa** (secretos): 1.202 Y$ justos, 65.000
  a la Corona, 1.200 al fondo buitre, 1.337 a los gamers y Cubiertos de Plata a la hora de
  comer. **Colección:** mirar la ficha de las 10 ONGs.

### La Fundación Familia Primero (9.30.0)

- Sustituye a la Fundación Ejemplaridad Borbónica. Es una ONG del cuñado de Perro Sanxe,
  el presidente de broma del bot: lee el BOE la víspera, pide las ayudas para sus empresas
  y se las adjudica él mismo. Es un familiar inventado de un personaje inventado; no se
  nombra a ningún familiar real.
- Mantiene la clave `corona` y el monedero `-9`, así que lo donado, el nivel de mecenas y
  los logros siguen valiendo. Los logros conservan su clave: cambian nombre, descripción
  y, en los dos secretos, la condición (14.999 Y$, el contrato menor troceado, y 5.300 Y$,
  el rescate de 53 millones a escala).
- Sus diez regalos llevan claves nuevas. La tienda no le cambia el nombre a un artículo
  ya sembrado, así que los de la Corona se quedan como estaban en los servidores donde
  ya estuvieran, ocultos, y no vuelven a salir en ningún sorteo.
- Techo Para Todos pierde el «(patrocina un fondo buitre)» del nombre.
- 9.30.1: Techo Para Todos pasa a ser la Fundación Ningún Inversor Sin Casa (misma clave `buitre`).

### La Fundación Acogida Cinco Estrellas (9.31.0)

- Once ONGs. La nueva (`acogida`, monedero `-11`) se ríe de la gestión y la comunicación
  de la acogida en Canarias: Madrid y Canarias pasándose la pelota, contratos de
  emergencia sin concurso, hoteles pagados a precio de suite porque nadie negoció,
  ruedas de prensa vacías y la foto del ministro en el muelle. La broma es siempre el
  Gobierno y sus contratistas, nunca las personas migrantes (Biblia, «Personalidad»).
- Diez regalos exclusivos (110 en total) y sus logros de 10.000, 100.000 y 500.000 Y$.
  «Dona a las 11 ONGs» es un escalón nuevo; los que decían «las 10» pasan a «10
  distintas» y la colección de exclusivos, a «100 distintos».
- 9.31.1: la Fundación Acogida Cinco Estrellas pasa a llamarse Eso Es Cosa de Madrid, con textos y regalos más burros (misma clave `acogida`; el blanco sigue siendo la gestión, no quien llega).
