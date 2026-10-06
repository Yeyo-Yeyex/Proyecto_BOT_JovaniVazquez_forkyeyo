# Auditoría de logros: rarezas y cantidad

Octubre de 2026. Cifras calculadas con `docs/auditoria_logros.py` (15 jugadores simulados por juego, hasta 400 días cada uno). Se vuelve a ejecutar al tocar las probabilidades de un juego o al añadir logros de suerte.

## Escala

La rareza dice cuánto le cuesta a un miembro activo que hace esa cosa:

| Rareza | Esfuerzo | Premio | Puntos |
|---|---|---|---|
| ▫️ Común | 3 días o menos | 50 Y$ | 10 |
| 🔹 Raro | hasta 2 semanas | 200 Y$ | 25 |
| 💠 Épico | hasta 2 meses | 750 Y$ | 50 |
| 🌟 Legendario | hasta 8 meses | 2.500 Y$ | 100 |
| 👑 Mítico | más de 8 meses, o suerte de 1 entre decenas de miles | 10.000 Y$ | 250 |

Ritmos supuestos de un jugador habitual de cada juego, al día: ruleta 40 tiradas, blackjack 40 manos, tragaperras 200 (con Auto y turbo), Botes 150, Crash 30 rondas, Minas 40, Pollo 60 y pachinko 60 tandas. Los contadores que no son del casino (mensajes, voz, reacciones, IMV…) usan los ritmos de `RITMO` del script.

## Método

1. **Casino, simulado.** Cada jugador juega con el código de verdad (`SlotMachine`, `spin_base` y `BonusGame`, `PachinkoMachine`, `MinesGame`, `ChickenGame`, `crash_point`, `Wheel`, `BlackjackGame`) y la misma función de estadísticas que usa el cog. La estrategia imita a un jugador normal: apuestas a color, a número y a docenas en la ruleta; estrategia básica simplificada en el blackjack; minas, dificultades y objetivos de cobro variados en Minas, Pollo y Crash. Se toma la mediana del día en que salta cada logro.
2. **Contadores, por ritmo.** Meta entre ritmo diario. Es orientativo: sirve para ver saltos de dos rarezas, no para afinar.
3. **Fechas y decisiones, a mano.** Los logros de un día del año (Halloween, Reyes…) se quedan en Común: basta con estar ese día. Lo que depende de una decisión del jugador (apostar 1 Y$, plantarse con 11, cobrar a 1,10x) se puede forzar a propósito y va en Común o Raro.

## Lo que salió y se cambió

Estructura: ninguna estadística tenía un escalón más alto con menos rareza. Sí había nombres repetidos: 14 en el catálogo de antes, como «Por los pelos» (tres veces) o «Ballena» y «Bote» (dos), y otros tantos que se colaron en la tanda de chat y voz. Se han renombrado 35 logros (los `id` no cambian). Ahora una prueba impide repetir nombres y otra, que la rareza baje al subir la meta.

Rarezas cambiadas: 130.

- **Pachinko estaba inflado.** El SUPER RUSH era Legendario y sale en unos 2 días (ahora Raro). Encadenar 15 era Mítico y sale en unos 2 meses (ahora Épico). Atari, rush y sus escalones, una rareza menos.
- **Tragaperras, al revés.** 💎💎💎 (1 de cada 847 tiradas) y 7️⃣7️⃣7️⃣ (1 de cada 1.309) salen en 3 a 6 días: de Épico y Legendario pasan a Raro. El premio gordo (1 de cada 14.400) tarda unos 84 días: de Épico a Legendario. Cinco premios gordos, a Mítico.
- **Botes.** MINI, MAJOR, bonus rojo y gran bonus salían en 1 a 5 días con rareza de semanas: bajan uno. Los multiplicadores inmediatos y los misteriosos tardan 18 y 24 días: suben de Común a Épico.
- **Pollo.** Llegar a la meta en Fácil y en Media (1 de cada 2,7 y 1 de cada 17 intentos), Por los pelos y la colección de matrículas bajaban de sobra. ×50, ×1.000, 50 cobros en Hardcore y 10 carriles en Hardcore suben.
- **Minas.** ×100 tarda unos 110 días y ×1.000 más de un año: suben a Legendario y Mítico. Limpiar el tablero con una mina (1 de cada 24 partidas con 1 mina, pero casi nadie juega con una) sube a Épico.
- **Crash.** Una ronda que llega a 100x pasa en 1 de cada 101: baja a Raro. 1.000 retiradas suben a Legendario.
- **Ruleta y blackjack.** Acertar un pleno, que salga el mismo número dos veces, separar y ganar las dos o acabar con 5 cartas bajan a Común. 12 tiradas seguidas ganadas (14.600 tiradas a color de media) sube a Mítico.
- **Casino general.** Probar los ocho juegos costaba Legendario y se hace en diez minutos: baja a Común o Raro. Las rachas de victorias se fuerzan cobrando a 1,01x en el Crash (98 % de acierto): 5 seguidas a Común, 10 a Raro.
- **Cosas que dependen del calendario.** Treinta días seguidos (hablar, el IMV, los intereses, la pala) es Épico; cien, Legendario; un año, Mítico. Antes había rachas de un año en Legendario y de 30 días en Común.
- **Loterías.** 4 aciertos en la Primitiva o la Bonoloto (1 de cada 1.032 apuestas) pasa de Raro a Épico; 5 aciertos (1 de cada 55.491), de Legendario a Mítico.

## Excepciones que la simulación marca y se dejan

Tras los cambios, 320 de los 370 logros simulados caen en su banda y 27 están a una de distancia, casi siempre en el límite de tres días. Los 23 restantes no son fallos de rareza:

- **Dependen de la hora:** los de madrugada (`slots_night`, `botes_night`, `pachi_night`). La simulación juega a las 18:00.
- **Dependen de la apuesta, no de la suerte:** ganar 10.000 o 100.000 Y$ de golpe (`crash_fuel`, `mines_rich`, `pollo_rich`, `pachi_rich`, `botes_win_10k`). Con 100 Y$ por jugada son imposibles; con apuestas grandes, no.
- **Dependen de una decisión:** cobrar tras un diamante o a 1,10x, plantarse con 11, ser gallina en el Pollo, cubrir toda la ruleta, jugar con las 12 cantidades de minas, el pleno repetido al número de siempre. El jugador simulado no lo hace; uno de verdad lo hace cuando quiere.
- **Dependen de la mesa o de la sesión:** 10 apuestas a la vez en la ruleta, 500 tiradas sin cerrar la tragaperras, la racha de 8 en la ruleta (con apuestas solo a color sale en unos 19 días, Épico).

## Cantidad

| | Antes | Después |
|---|---|---|
| Logros | 710 | 1.212 |
| Secretos | 127 | 213 |
| Míticos | 17 | 65 |

Primera tanda (chat y voz): 262 logros. Segunda tanda (el resto de funcionalidades): 240. Ninguna categoría baja de 12 logros. Ruleta, blackjack, tragaperras, Botes, Pollo, pachinko, loterías, tienda, banco, trabajo, oficios y Hong Kong pasan de 40; Crash, Minas, Sanidad y Oficina rondan los 35. Las que no caben en un embed se parten en hojas con ◀ y ▶ en `logros`, así que el tamaño de una categoría ya no pone techo.
