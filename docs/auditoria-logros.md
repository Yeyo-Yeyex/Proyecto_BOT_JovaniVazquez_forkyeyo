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

Ritmos supuestos de un jugador habitual de cada juego, al día: ruleta 40 tiradas, blackjack 40 manos, tragaperras 200 (con Auto y turbo), Botes 150, Crash 30 rondas, Minas 40, Pollo 60, pachinko 60 tandas y caballos 30 boletos. Los contadores que no son del casino (mensajes, voz, reacciones, IMV…) usan los ritmos de `RITMO` del script.

## Método

1. **Casino, simulado.** Cada jugador juega con el código de verdad (`SlotMachine`, `spin_base` y `BonusGame`, `PachinkoMachine`, `MinesGame`, `ChickenGame`, `crash_point`, `Wheel`, `BlackjackGame`, `run_race`) y la misma función de estadísticas que usa el cog. La estrategia imita a un jugador normal: apuestas a color, a número y a docenas en la ruleta; estrategia básica simplificada en el blackjack; minas, dificultades y objetivos de cobro variados en Minas, Pollo y Crash. Se toma la mediana del día en que salta cada logro.
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

## Carreras de caballos

Se añadieron después, con 72 logros (11 secretos). La simulación (`_jugar_caballos`) prepara 60 parrillas con sus cuotas, una de cada diez de Gran Premio, y en cada carrera elige un tipo de boleto como un jugador normal (ganador 55 %, colocado 15 %, gemela 18 %, trío 12 %) y caballos tirando hacia los favoritos. 52 de los 72 caen en su banda; 9 a una de distancia. Lo que se calibró con ella:

- **Suben:** cobrar a 20x (unos 4 días) y a 100x (unos 40), ganar con un tapado de 10x (5 días; diez veces, unos 70), perder 10 por una nariz (63), remontar desde el último (40; diez veces, más de un año), ganar tras tropezar (unos 200 días), 50 colocados y 25 aciertos con el pronóstico de Perro Sanxe (unos 20 días).
- **Bajan:** que tu caballo se desboque (1 de cada 250 boletos, día y medio) y el secreto de Puerta Giratoria, que pide 5x en vez de 10x (a 10x tardaba nueve meses).
- **Se dejan por encima de lo que dice la simulación:** el bote del Gran Premio (Raro, y cinco botes Épico) y la colección de distancias (Raro), porque la simulación corre un Gran Premio de cada diez carreras y en un servidor de verdad sale como mucho uno cada 4 horas y hacen falta ocho carreras normales antes. Igual con «Tribuna llena» y «Derbi de Epsom» (6 y 10 boletos en una carrera), que dependen de cuánta gente juegue en el servidor.
- **Fuera de la simulación:** los de madrugada, apostar 1.500 Y$ justos, ganar 100.000 Y$ de golpe, la retención de IRPF (el jugador simulado no paga impuestos), el caballo cansado (la simulación no guarda historial) y los que se derivan de los contadores por caballo (todo el establo, hincha, socio y peña), que se cuentan en `with_derived`.

## Pachinko: la física

Las bolas del pachinko caen con física real (`bot.services.pachinko_physics`) y de ahí salen 25 logros nuevos: rebotes en los clavos, caídas lentas y rápidas y bolas que bajan sin tocar casi nada. Se alimentan en `pachinko_stats` con las caídas de la biblioteca guardada (`trayectorias.json`), no con lo que paga la máquina. Cuatro son secretos.

La rareza sale de la biblioteca y de la probabilidad real de cada bolsillo (`pocket_probability`): cada bola elige al azar una de las 24 caídas de su bolsillo, así que la probabilidad de una propiedad de la caída es la suma, sobre bolsillos, de la probabilidad del bolsillo por la parte de sus caídas que la cumple. Con el jugador de la simulación (60 tandas al día, tableros 40 % Clásica, 30 % Sakura, 15 % Dragón y 15 % Oni) y 10 bolas por tanda salen unos 116 rebotes por tanda (6.950 al día), 0,14 bolas lentas por bola, 0,05 rápidas y 0,025 que bajan casi sin tocar clavos:

| Logro | Cuenta | Días (mediana) | Rareza |
|---|---|---|---|
| 1.000 y 10.000 rebotes | meta / 6.950 al día | 0,15 y 1,5 | Común |
| 50.000 / 250.000 / 1.000.000 / 5.000.000 rebotes | idem | 7 / 36 / 144 / 720 | Raro / Épico / Legendario / Mítico |
| Una bola con 15, 18, 20 y 21 rebotes | 66 %, 11 %, 2,9 % y 1,2 % de las tandas lo traen | 0,01 / 0,1 / 0,4 / 1 | Común (el máximo que da un tablero es 21, solo en Oni) |
| Una tanda con 150 rebotes | 2,4 % de las tandas | 0,5 | Común |
| Una tanda con 160, 165 y 170 rebotes | 0,16 %, 0,025 % y 0,003 % | 7 / 46 / 450 | Raro / Épico / Mítico |
| 1, 100, 1.000 y 10.000 bolas lentas (casi 2 s) | 85 al día | 0,02 / 1,2 / 12 / 118 | Común / Común / Raro / Legendario |
| 1, 100, 1.000 y 5.000 bolas rápidas (1,1 s o menos) | 30 al día | 0,03 / 3,3 / 33 / 165 | Común / Raro / Épico / Legendario |
| 1, 25 y 250 bolas con 5 rebotes o menos | 15 al día | 0,06 / 1,7 / 17 | Común / Común / Épico |

Las cifras de las tandas salen de convolucionar la distribución de rebotes de una bola 10 veces por tablero; la simulación de `docs/auditoria_logros.py` (con `PachinkoMachine(rng.randrange, rng.randrange)`) las confirma y no marca ninguna discrepancia. Si se regenera la biblioteca (`docs/pachinko_trayectorias.py`) cambian estas distribuciones: hay que volver a calcular las tablas y las metas de `pachinko_bounce_volley_max`, que son las más sensibles.

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
