# Auditoría de las loterías (15/10/2026)

Hecha con `docs/auditoria_loteria.py`, que juega los sorteos con el código real del
bot (`server_odds`, `draw_result`, `settle_pool`, `guarantee_for`) durante diez años
simulados, en un servidor de 20 y otro de 60 personas donde cada una echa una
apuesta automática en cada sorteo. `tests/unit/test_auditoria_loteria.py` la pasa
corta en cada prueba.

## Retorno y botes

| Juego | Normas | 20 personas | 60 personas | Bote, veces al mes (20 / 60) |
|---|---:|---:|---:|---:|
| La Primitiva | 55 % | 56,4 % | 55,3 % | 1,04 / 1,05 |
| Bonoloto | 55 % | 56,3 % | 54,8 % | 1,12 / 0,93 |
| El Gordo de la Primitiva | 55 % (44 % sin la reserva) | 47,1 % | 46,1 % | 0,71 / 0,90 |
| Euromillones | 50 % (47,6 % sin la reserva) | 47,4 % | 47,7 % | 1,06 / 1,06 |
| Nacional (jueves, sábado, Navidad, Niño) | 70 % | 69,5-70 % | | |
| Rasca X10 / 7 y Media | 64 % / 59 % | 64,0 % / 59,0 % | | |

- **Todo cuadra con las normas.** El Gordo y Euromillones devuelven menos que su
  `prize_rate` porque la reserva del bote garantizado (la mitad de la 1ª del Gordo y
  el 4,8 % de Euromillones) se queda en el Estado, que es quien garantiza; lo que pone
  para llegar al mínimo (0,2-1,2 % de lo vendido) se lo devuelve en parte.
- **El bote cae una vez al mes**, como pide `JACKPOT_PERIOD_DAYS`. El Gordo se queda
  algo por debajo con 20 personas, como ya avisa `server_odds`: tiene tantas
  categorías que el bombo no puede regalarlas todas.
- **Ninguna categoría cobra más que una superior** en ningún sorteo de los diez años
  (`_no_inversion` funciona) y el bote que pasa de un sorteo a otro nunca se pierde.

## Lo único raro: los premios fijos en servidores pequeños

Con 20 personas, la Primitiva y la Bonoloto reparten un 3 % más de lo que les toca
(56,4 % en vez de 55 %). Con 60 desaparece (100,5 % y 99,8 % de su parte). No es un
fallo de cálculo, es el tamaño del sorteo:

- Los 3 aciertos cobran un premio fijo (8 € en la Primitiva, 80 Y$) que sale del
  fondo de categorías, el 45 % de lo vendido. Con 20 apuestas de 10 Y$ ese fondo es
  de 90 Y$: con dos acertantes de 3 (pasa en 1 de cada 20 sorteos) ya no llega.
- Lo que falta lo pone el Estado, como en la vida real cuando el fondo no alcanza: en
  ~40 % de los sorteos se paga algo más que la parte de premios de ese sorteo (los
  reintegros también varían), y en el conjunto queda ese 3 %.

No crea dinero (lo paga el Estado, que se lo cobra con los demás impuestos) y es
pequeño, así que **no se ha cambiado**. Si el Estado anduviera justo, el arreglo sería
el de algunas loterías reales: si los fijos pasan del fondo, se reparte el fondo entre
los acertantes en vez de pagar el fijo entero.

## También revisado, sin cambios

- Compras, rascas y premios pasan por `EconomyService.lottery`, sin IGIC y con el
  gravamen del 20 % por décimo o apuesta por encima de 40.000 € (DA 33ª LIRPF).
- Los botones de comprar y rascar hacen `ack` antes de la base de datos y avisan de
  la Renta después de contestar.
- El rasca se sortea antes de cobrar, pero si no hay saldo no se enseña nada: no se
  puede mirar el premio sin pagar.
- 52 logros (9 comunes, 17 raros, 13 épicos, 9 legendarios, 4 míticos; 8 secretos):
  pasa de los cuarenta que pide la Biblia para las loterías.
