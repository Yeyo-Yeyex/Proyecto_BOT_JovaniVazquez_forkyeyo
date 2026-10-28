# Calibración del juego del pico (`picar`)

Sacada con `docs/calibrar_picar.py` el 28/10/2026, con una biblioteca de 1.000.000 semillas por pico (3 min). El RTP es exacto: es la media de los premios de todas las semillas de la biblioteca, que es de donde sale cada tirada. Los multiplicadores son el premio entre el coste, ya redondeado a Y$.

| Pico | Coste | Tope | RTP | Gana algo | ×10 o más | Nada | p50 | p90 | p99 | p99,9 | Máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Madera | 10 | ×50 | 99,4837% | 42,8% | 0,03% | 11,2% | ×0,80 | ×2,20 | ×4,2 | ×7,3 | ×34 |
| Piedra | 50 | ×150 | 99,5000% | 31,3% | 0,13% | 0,0% | ×0,76 | ×1,78 | ×4,6 | ×10,8 | ×150 |
| Hierro | 200 | ×500 | 99,5000% | 26,7% | 0,36% | 0,0% | ×0,63 | ×1,84 | ×6,2 | ×17,7 | ×319 |
| Oro | 200 | ×1.500 | 99,5000% | 29,6% | 0,24% | 0,0% | ×0,69 | ×1,79 | ×5,4 | ×14,6 | ×174 |
| Diamante | 1.000 | ×2.500 | 99,4998% | 13,3% | 1,21% | 0,0% | ×0,18 | ×1,35 | ×11,8 | ×75,7 | ×2500 |
| Netherite | 5.000 | ×5.000 | 99,4862% | 8,8% | 1,13% | 0,0% | ×0,07 | ×0,85 | ×11,2 | ×99,7 | ×5000 |

| Pico | Fila media | Nether | End | Dragón | Del RTP, el dragón | Duración media | p90 |
|---|---|---|---|---|---|---|---|
| Madera | 9 | 0,00% | 0,000% | nunca | 0,00% | 8 s | 10 s |
| Piedra | 33 | 0,00% | 0,000% | nunca | 0,00% | 13 s | 21 s |
| Hierro | 50 | 0,00% | 0,000% | nunca | 0,00% | 18 s | 31 s |
| Oro | 71 | 0,00% | 0,000% | nunca | 0,00% | 10 s | 18 s |
| Diamante | 222 | 27,35% | 1,659% | nunca | 0,00% | 48 s | 85 s |
| Netherite | 328 | 62,69% | 15,584% | 1 de cada 1.000.000 | 0,50% | 74 s | 114 s |

Lo que suma cada mineral, en diezmilésimas del coste (10.000 es ×1):

| Pico | coal | iron | gold | redstone | lapis | diamond | emerald | quartz | nether_gold | ancient_debris | end_crystal |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Madera | 2833 | 5670 | 11336 | 5667 | 8502 | 28335 | 34002 | 5667 | 8500 | 113340 | 340020 |
| Piedra | 449 | 897 | 1796 | 897 | 1346 | 4688 | 5386 | 897 | 1346 | 17950 | 53850 |
| Hierro | 204 | 410 | 818 | 409 | 614 | 2100 | 2454 | 440 | 618 | 8180 | 24540 |
| Oro | 268 | 536 | 1071 | 536 | 803 | 2690 | 3216 | 537 | 803 | 10713 | 32140 |
| Diamante | 10 | 15 | 31 | 15 | 23 | 78 | 95 | 15 | 23 | 311 | 939 |
| Netherite | 1 | 2 | 5 | 2 | 3 | 13 | 14 | 2 | 3 | 48 | 141 |
