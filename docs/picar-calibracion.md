# Calibración del juego del pico (`picar`)

Sacada con `docs/calibrar_picar.py` el 28/10/2026, con una biblioteca de 4,000 semillas por pico (1 min). El RTP es exacto: es la media de los premios de todas las semillas de la biblioteca, que es de donde sale cada tirada. Los multiplicadores son el premio entre el coste, ya redondeado a Y$.

| Pico | Coste | Tope | RTP | Gana algo | ×10 o más | Nada | p50 | p90 | p99 | p99,9 | Máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Madera | 10 | ×50 | 99.4575% | 42.8% | 0.03% | 10.9% | ×0.80 | ×2.20 | ×4.4 | ×7.2 | ×17 |
| Piedra | 50 | ×150 | 99.5000% | 30.5% | 0.10% | 0.0% | ×0.74 | ×1.74 | ×4.9 | ×9.7 | ×19 |
| Hierro | 200 | ×500 | 99.5000% | 26.4% | 0.35% | 0.0% | ×0.62 | ×1.84 | ×6.4 | ×17.9 | ×36 |
| Oro | 200 | ×1,500 | 99.5000% | 30.3% | 0.35% | 0.0% | ×0.69 | ×1.76 | ×5.8 | ×14.7 | ×24 |
| Diamante | 1,000 | ×2,500 | 99.4998% | 13.9% | 1.38% | 0.0% | ×0.18 | ×1.43 | ×13.8 | ×57.4 | ×237 |
| Netherite | 5,000 | ×5,000 | 99.4941% | 11.8% | 1.45% | 0.0% | ×0.10 | ×1.25 | ×14.6 | ×81.7 | ×622 |

| Pico | Fila media | Nether | End | Dragón | Del RTP, el dragón | Duración media | p90 |
|---|---|---|---|---|---|---|---|
| Madera | 9 | 0.00% | 0.000% | nunca | 0.00% | 8 s | 10 s |
| Piedra | 33 | 0.00% | 0.000% | nunca | 0.00% | 13 s | 21 s |
| Hierro | 50 | 0.00% | 0.000% | nunca | 0.00% | 18 s | 32 s |
| Oro | 71 | 0.00% | 0.000% | nunca | 0.00% | 10 s | 18 s |
| Diamante | 222 | 27.12% | 1.550% | nunca | 0.00% | 48 s | 85 s |
| Netherite | 327 | 62.95% | 14.900% | nunca | 0.00% | 73 s | 112 s |

Lo que suma cada mineral, en diezmilésimas del coste (10.000 es ×1):

| Pico | coal | iron | gold | redstone | lapis | diamond | emerald | quartz | nether_gold | ancient_debris | end_crystal |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Madera | 2800 | 5599 | 11199 | 5599 | 8399 | 27997 | 33597 | 5599 | 8399 | 111990 | 335970 |
| Piedra | 439 | 878 | 1757 | 879 | 1318 | 4392 | 5271 | 878 | 1318 | 17570 | 52710 |
| Hierro | 197 | 392 | 785 | 392 | 589 | 1967 | 2357 | 392 | 589 | 7850 | 23550 |
| Oro | 267 | 534 | 1069 | 534 | 803 | 2680 | 3209 | 534 | 802 | 10690 | 32070 |
| Diamante | 8 | 17 | 34 | 17 | 25 | 87 | 102 | 17 | 25 | 340 | 1036 |
| Netherite | 2 | 3 | 6 | 3 | 5 | 16 | 20 | 3 | 4 | 61 | 180 |
