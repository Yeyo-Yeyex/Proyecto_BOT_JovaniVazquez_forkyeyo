# Calibración del juego del pico (`picar`)

Sacada con `docs/calibrar_picar.py` el 28/10/2026, con una biblioteca de 1.000.000 semillas por pico (1 min). El RTP es exacto: es la media de los premios de todas las semillas de la biblioteca, que es de donde sale cada tirada. Los multiplicadores son el premio entre el coste, ya redondeado a Y$.

| Pico | Coste | Tope | RTP | Gana algo | ×10 o más | Nada | p50 | p90 | p99 | p99,9 | Máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Madera | 10 | ×50 | 99,4998% | 35,2% | 0,05% | 26,3% | ×0,80 | ×1,70 | ×3,4 | ×5,2 | ×50 |
| Piedra | 50 | ×150 | 99,5000% | 31,6% | 0,20% | 0,6% | ×0,88 | ×1,64 | ×3,8 | ×13,9 | ×150 |
| Hierro | 200 | ×500 | 99,5000% | 27,2% | 0,30% | 0,1% | ×0,79 | ×1,57 | ×5,1 | ×17,1 | ×500 |
| Oro | 200 | ×1.500 | 99,5000% | 27,7% | 0,30% | 0,0% | ×0,77 | ×1,54 | ×5,1 | ×16,9 | ×380 |
| Diamante | 1.000 | ×2.500 | 99,5000% | 15,8% | 0,97% | 0,0% | ×0,36 | ×1,52 | ×9,8 | ×54,4 | ×2500 |
| Netherite | 5.000 | ×5.000 | 99,4999% | 11,8% | 0,95% | 0,0% | ×0,20 | ×1,18 | ×9,5 | ×63,7 | ×5000 |

| Pico | Fila media | Nether | End | Dragón | Del RTP, el dragón | Duración media | p90 |
|---|---|---|---|---|---|---|---|
| Madera | 2 | 0,00% | 0,000% | nunca | 0,00% | 9 s | 11 s |
| Piedra | 11 | 0,00% | 0,000% | nunca | 0,00% | 21 s | 27 s |
| Hierro | 18 | 0,03% | 0,000% | nunca | 0,00% | 22 s | 31 s |
| Oro | 15 | 0,01% | 0,000% | nunca | 0,00% | 17 s | 24 s |
| Diamante | 64 | 21,40% | 0,471% | nunca | 0,00% | 52 s | 81 s |
| Netherite | 90 | 50,80% | 5,360% | 1 de cada 52.632 | 9,50% | 72 s | 113 s |

Lo que suma cada mineral, en diezmilésimas del coste (10.000 es ×1):

| Pico | coal | iron | gold | redstone | lapis | diamond | emerald | quartz | nether_gold | ancient_debris | end_crystal |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Madera | 8666 | 17333 | 94999 | 17634 | 26268 | 86665 | 103998 | 17333 | 26000 | 346660 | 1039980 |
| Piedra | 1266 | 2535 | 5066 | 2537 | 3800 | 12793 | 15227 | 2555 | 3800 | 50660 | 151980 |
| Hierro | 714 | 1428 | 2857 | 1428 | 2143 | 7168 | 8572 | 1428 | 2144 | 28595 | 85710 |
| Oro | 704 | 1409 | 2817 | 1409 | 2114 | 7046 | 8452 | 1415 | 2117 | 28175 | 84525 |
| Diamante | 78 | 156 | 314 | 156 | 234 | 783 | 939 | 156 | 234 | 3130 | 9422 |
| Netherite | 26 | 51 | 102 | 51 | 76 | 256 | 308 | 51 | 76 | 1021 | 3064 |
