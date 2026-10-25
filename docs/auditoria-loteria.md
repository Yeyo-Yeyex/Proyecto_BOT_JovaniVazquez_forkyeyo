# Auditoría de las loterías (25/10/2026)

Hecha con el informe de uso del 26/09 al 25/10/2026 (versión 9.21.0) y con
`docs/auditoria_loteria.py`, que juega un año de sorteos con el código real del bot
(`server_odds`, `draw_result`, `settle_pool`, `guarantee_for`).
`tests/unit/test_auditoria_loteria.py` la pasa corta en cada prueba. La anterior, del
15/10/2026, solo miraba que el reparto cuadrara con las normas; esta mira además si
alguien juega.

## Lo que dice el informe

| Juego | Movimientos | Jugado | Cobrado |
|---|---:|---:|---:|
| Rasca X10 | 509 | 8.120 Y$ | 4.420 Y$ |
| Nacional del jueves | 59 | 15.930 Y$ | 12.330 Y$ |
| Nacional del sábado | 22 | 12.000 Y$ | 3.600 Y$ |
| Bonoloto | 38 | 1.500 Y$ | 275 Y$ |
| Rasca 7 y Media | 24 | 200 Y$ | 70 Y$ |
| Euromillones, Gordo y Primitiva | 6 | 267 Y$ | 87 Y$ |

- Cuatro personas usan la lotería: 552 clics en el panel y 16 aperturas en 30 días.
  Casi todo son rascas X10 en ráfagas (unos 400, de una o dos personas) y décimos
  del jueves y el sábado.
- Los cuatro juegos de bote están muertos: 44 apuestas en 60 días, la mitad en un
  solo día de septiembre.
- Para comparar: en el mismo mes se apostaron 6 millones en las minas y 1,1 millones
  en la tragaperras. La lotería entera movió 38.000 Y$.

## Por qué nadie jugaba a los botes

Las probabilidades se ajustaban al servidor (el bote caía una vez al mes si todos
jugaban), pero el dinero seguía las normas de SELAE: cada categoría se lleva una
parte del fondo de lo recaudado. Con cinco apuestas de 10 Y$ ese fondo es de unos
pocos Y$. Simulando cinco jugadores por sorteo en un servidor de 20 personas:

| | Mediana de lo que cobraba cada acertante |
|---|---:|
| Bote de la Primitiva (6 + reintegro) | 6 Y$ (la apuesta cuesta 10) |
| 5 + complementario de la Primitiva | 1 Y$ |
| 3 aciertos de la Primitiva (premio fijo) | 80 Y$ |
| Bote de Euromillones | 208 Y$ |
| 5 + 1 estrella de Euromillones | 2 Y$ |

Y el bote que se anunciaba: 0 Y$ en la Primitiva y la Bonoloto (no tienen mínimo) y
de 70 a 800 Y$ en el Gordo y Euromillones, porque el mínimo real (4,5 y 17 millones
de euros) se encogía en la misma proporción que las probabilidades.

## Lo que se ha cambiado (9.25.0)

- **Premios fijos por debajo del bote** (`category_prizes`). Cada categoría devuelve
  de media la misma parte de la apuesta, y entre todas lo mismo que con las normas
  reales (`real_shares`): el premio crece con la dificultad. Con 20 personas, la
  Primitiva paga 40 Y$ por 3 aciertos, 55 por 4, 82 por 5, 120 por 5 + C y 150 por 6.
  Con las probabilidades reales salen cifras de verdad (740 Y$ por 4 aciertos, 40.000
  por 5, 1,7 millones por 5 + C). Ninguno baja del precio de la apuesta.
- **Bote que crece** (`guarantee_for`). El bote es lo que le toca de cada venta,
  acumulado, o el mínimo que garantiza el Estado si es mayor. Ese mínimo empieza en
  `jackpot_floor` y sube `jackpot_daily` cada día sin acertante, hasta 90 días:

  | Juego | Empieza en | Sube al día | A los 30 días | Tope (90 días) |
  |---|---:|---:|---:|---:|
  | Bonoloto | 2.000 Y$ | 150 Y$ | 6.500 Y$ | 15.500 Y$ |
  | La Primitiva | 5.000 Y$ | 400 Y$ | 17.000 Y$ | 41.000 Y$ |
  | El Gordo | 8.000 Y$ | 450 Y$ | 21.500 Y$ | 48.500 Y$ |
  | Euromillones | 15.000 Y$ | 1.000 Y$ | 45.000 Y$ | 105.000 Y$ |

  El Estado solo paga cuando alguien acierta; entonces el mínimo vuelve a empezar.
  Nunca compromete más del 25 % de su saldo.
- **Diez apuestas por persona y sorteo** en estos cuatro juegos (`POOL_MAX_PER_DRAW`).
  Con cien, una sola persona se compraba el bote. La Nacional sigue en cien décimos.

## Cuánto cuesta (un año simulado)

«Estado/mes» es lo vendido menos lo pagado: negativo, lo que pone el Estado.

| Juego | Escenario (20 personas) | Devuelve | Estado/mes | Bote al mes | Bote mediano |
|---|---|---:|---:|---:|---:|
| Primitiva | todos, 1 apuesta | 634 % | −13.706 Y$ | 1,15 | 12.200 Y$ |
| Primitiva | 1 de cada 4, 3 apuestas | 906 % | −15.504 Y$ | 1,07 | 11.400 Y$ |
| Primitiva | 1 persona al tope | 1.109 % | −12.938 Y$ | 0,74 | 16.200 Y$ |
| Bonoloto | todos, 1 apuesta | 249 % | −3.821 Y$ | 0,74 | 6.950 Y$ |
| Gordo | todos, 1 apuesta | 1.525 % | −18.272 Y$ | 0,90 | 17.450 Y$ |
| Euromillones | todos, 1 apuesta | 1.235 % | −42.701 Y$ | 1,15 | 36.000 Y$ |
| Euromillones | 1 persona al tope | 1.298 % | −22.530 Y$ | 0,33 | 92.000 Y$ |

Con 60 personas el Estado pone algo menos (el bote es más difícil y cae menos):
Primitiva −13.020, Bonoloto −873, Gordo −14.566 y Euromillones −38.003 Y$ al mes con
todo el servidor jugando.

- **El peor caso son unos 78.500 Y$ al mes** entre los cuatro juegos (20 personas,
  todas jugando cada sorteo). Es el 1,5 % de lo que recaudó el Estado en el mes del
  informe (5,1 millones) y un 19 % de la masa monetaria de los miembros (418.000 Y$).
- **Es dinero nuevo a propósito.** La Biblia pide que cada fuente tenga su sumidero:
  aquí la fuente es el propio Estado devolviendo parte de lo recaudado, con tope
  diario (unos 2.000 Y$ entre los cuatro mínimos) y el 25 % de su saldo como límite.
- **Una sola persona al tope en los cuatro juegos** gasta unos 5.100 Y$ al mes y, de
  media, se lleva unos 52.000 si nadie más juega: 1,2 veces el IMV máximo. Si juegan
  más, se reparte. Es un premio de lotería: llega de golpe cada uno o dos meses.

## Lo que no cambia

- La Nacional (jueves, sábado, Navidad y Niño) devuelve su 70 % con las tablas
  reales (69,5-70 %), y los rascas su 64 % (X10) y 59 % (7 y Media). Son lo que se
  juega: el reintegro y los premios pequeños caen a menudo y el gordo existe.
- Ninguna categoría cobra más que una superior en ningún sorteo del año simulado.
- Compras, rascas y premios pasan por `EconomyService.lottery`, sin IGIC y con el
  gravamen del 20 % por décimo o apuesta por encima de 40.000 € (DA 33ª LIRPF). Si el
  Estado no llega a un premio, emite deuda pública.

## Las imágenes (9.25.0)

Una sola escena (`assets/loteria/escena.html`) con un modo por imagen, pintada con Node.
Medido con `docs/medir_dibujos.py` a 4 hilos («1 abierto» / «caliente», ms) y con
`docs/muestras_loteria.py` (peso):

| Imagen | Tiempo | Peso | Cuándo se ve |
|---|---:|---:|---|
| Rasca (GIF de ~2,5 s) | 322 / 246 | 280-380 KB | Al rascar; se pinta antes del clic, así que del clic al GIF no se espera nada |
| Sorteo de bote (GIF de ~5 s) | 654 / 540 | 0,9-1,2 MB | En el canal, al celebrarse |
| Sorteo de la Nacional (GIF de ~4-5,5 s) | 628 / 494 | 0,9-1,1 MB | En el canal, al celebrarse |
| Décimo | 77 / 81 | ~200 KB | Al comprar y en 🎟️ Mis boletos |
| Resguardo | 38 / 37 | 45-90 KB | Al comprar y en 🎟️ Mis boletos |
| Cartel | 64 / 68 | ~190 KB | En 🏠 Inicio; solo se repinta si cambia |

La escena suma ~50 MB a cada proceso de Node que la carga (como mucho 4). Si Node falla,
pasa a Chromium; si tampoco hay, el panel sigue en texto como antes.
