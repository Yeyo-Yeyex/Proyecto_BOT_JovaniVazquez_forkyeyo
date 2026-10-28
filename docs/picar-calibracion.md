# Calibración del juego del pico (`picar`)

Sacada con `docs/calibrar_picar.py` el 28/10/2026, con una biblioteca de 1.000.000 semillas por pico (9 min). El RTP es exacto: es la media de los premios de todas las semillas de la biblioteca, que es de donde sale cada tirada. Los multiplicadores son el premio entre el coste, ya redondeado a Y$.

| Pico | Coste | Tope | RTP | Gana algo | ×10 o más | Nada | p50 | p90 | p99 | p99,9 | Máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Madera | 10 | ×50 | 99,4972% | 42,5% | 0,03% | 11,4% | ×0,80 | ×2,20 | ×4,2 | ×7,4 | ×34 |
| Piedra | 50 | ×150 | 99,5000% | 29,0% | 0,14% | 0,0% | ×0,74 | ×1,80 | ×4,8 | ×11,3 | ×126 |
| Hierro | 200 | ×500 | 99,5000% | 27,7% | 0,33% | 0,0% | ×0,65 | ×1,83 | ×6,0 | ×17,0 | ×500 |
| Oro | 200 | ×1.500 | 99,5000% | 29,6% | 0,23% | 0,0% | ×0,69 | ×1,78 | ×5,3 | ×14,2 | ×241 |
| Diamante | 1.000 | ×2.500 | 99,5000% | 15,7% | 1,12% | 0,0% | ×0,27 | ×1,55 | ×10,9 | ×60,3 | ×2500 |
| Netherite | 5.000 | ×5.000 | 99,4959% | 9,6% | 1,11% | 0,0% | ×0,10 | ×0,95 | ×11,1 | ×90,2 | ×5000 |

| Pico | Fila media | Nether | End | Dragón | Del RTP, el dragón | Duración media | p90 |
|---|---|---|---|---|---|---|---|
| Madera | 9 | 0,00% | 0,000% | nunca | 0,00% | 8 s | 10 s |
| Piedra | 41 | 0,00% | 0,000% | nunca | 0,00% | 15 s | 25 s |
| Hierro | 55 | 0,05% | 0,000% | nunca | 0,00% | 18 s | 32 s |
| Oro | 75 | 0,04% | 0,000% | nunca | 0,00% | 11 s | 18 s |
| Diamante | 199 | 21,29% | 0,247% | nunca | 0,00% | 38 s | 66 s |
| Netherite | 280 | 52,80% | 5,079% | nunca | 0,00% | 59 s | 97 s |

Lo que suma cada mineral, en diezmilésimas del coste (10.000 es ×1):

| Pico | coal | iron | gold | redstone | lapis | diamond | emerald | quartz | nether_gold | ancient_debris | end_crystal |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Madera | 2857 | 5714 | 11431 | 5717 | 8574 | 28572 | 34287 | 5714 | 8572 | 114290 | 342870 |
| Piedra | 392 | 784 | 1567 | 784 | 1175 | 4098 | 4704 | 785 | 1175 | 15673 | 47020 |
| Hierro | 210 | 421 | 843 | 421 | 633 | 2113 | 2526 | 423 | 632 | 8421 | 25260 |
| Oro | 275 | 550 | 1099 | 550 | 824 | 2829 | 3301 | 554 | 824 | 10993 | 32980 |
| Diamante | 22 | 43 | 87 | 43 | 65 | 218 | 262 | 44 | 65 | 874 | 2704 |
| Netherite | 3 | 7 | 13 | 7 | 10 | 33 | 39 | 7 | 10 | 132 | 395 |
