# Historial de versiones

Cada cambio que entra en `main` sube la versión del bot (`src/bot/__init__.py`)
y añade aquí su entrada, la más nueva arriba. Cómo se decide el número está en
la sección «Versiones» de `Biblia.txt`. Las versiones anteriores a la 5.22.0
se asignaron a posteriori, revisando cada PR y commit de `main` con el mismo
criterio.

## 7.x

### 7.18.0 · 2026-10-16

- Blackjack: mesa animada con tapetes, zapato para contar y rachas

### 7.17.0 · 2026-10-16

- Allá tú: `allatu`, 22 cajas, un gordo sorpresa y la banca al teléfono

### 7.16.0 · 2026-10-16

- Pisos: obra nueva de la rama Ladrillo, aval de los padres, fianzas y frase del BCE
- Patrimonio: cuentan los objetos de la tienda, límite con el IRPF y Grandes Fortunas

### 7.15.0 · 2026-10-16

- `.met`: atajo de texto de `reinicio` (sin versión `/`)

### 7.14.0 · 2026-10-16

- Informe de uso: llamadas a Discord por ruta, tareas programadas, tiempo de arranque, memoria de Chromium y Node, disco leído y escrito, tareas vivas, primeros pasos de los miembros nuevos, reacciones a los mensajes del bot, y por día los impuestos, el Gini y el saldo del Estado
- El informe del canal llega con un resumen de lo que hay que vigilar del día anterior

### 7.13.0 · 2026-10-16

- Informe de uso: tiempo de cada consulta a la base de datos por repositorio, esperas en candados, procesos de Node y dibujos con plan B, peso de las imágenes, interacciones perdidas (10062), partidas y paneles abiertos, cerrados o abandonados, opciones de los menús y páginas de la ayuda, comandos mal escritos o rechazados, errores por tipo y función, música, minutos sin datos (bot caído), logros por rareza, ventas de la tienda, mascotas y economía por día

### 7.12.0 · 2026-10-16

- Informe de uso: mide la CPU del contenedor, lo que ocupa la base de datos y el disco libre, la espera del clic a la imagen, las vistas que caducan a medias, cada arranque con su versión, los cortes con Discord, sus respuestas 429 y los procesos matados por memoria
- `docs/analisis_metricas.py` junta varios informes en una visión global y compara versiones del bot; `docs/descargar_informe.py` baja todos los del canal

### 7.11.0 · 2026-10-16

- `informe`: con `canal`, el informe de uso se publica cada día en ese canal privado; con `quitar`, deja de publicarse

### 7.10.1 · 2026-10-16

- Torre del dragón: al acabar cada jugada ya no se repite un trozo del GIF anterior con fotogramas mezclados (el mensaje pasa a embed, como los demás juegos)

### 7.10.0 · 2026-10-16

- Torre del dragón: subida entre pisos más larga por una escalera de caracol, el dragón sale del hueco sin saltos y al acabar la cámara se aleja hasta la torre entera

### 7.9.2 · 2026-10-16

- Torre del dragón: al subir de piso, la cámara entra por la puerta del huevo en vez de enseñar dos pisos a la vez

### 7.9.1 · 2026-10-16

- Torre del dragón: al subir de piso ya no se mezclan los dos pisos, con un forjado de piedra entre ellos

### 7.9.0 · 2026-10-15

- Torre del dragón: dibujo rehecho con sombreado, luces y un dragón nuevo

### 7.8.0 · 2026-10-15

- Torre del dragón: `torre` animada, con un tema por nivel y todo pintado antes del clic

### 7.7.1 · 2026-10-15

- Logros de colección: las cajas y las skins ya no cuentan como coleccionables

### 7.7.0 · 2026-10-15

- Pisos: rediseño ilustrado, con las islas de verdad y mucha más guasa

### 7.6.0 · 2026-10-15

- Guerra: mesa animada con tapete propio, pintada antes del clic

### 7.5.3 · 2026-10-15

- Arreglos: impuestos de pisos en bases antiguas y cobros que se quedaban colgados

### 7.5.2 · 2026-10-15

- Tragaperras: rótulos y fondos de la Mina y el Olimpo a la altura del Volcán

### 7.5.1 · 2026-10-15

- Tragaperras: la Mina y el Olimpo, con el mismo arte que el Volcán

### 7.5.0 · 2026-10-15

- Tienda: cajas por niveles, de temporada y ganadas jugando, con colecciones

### 7.4.0 · 2026-10-15

- Dados: mesa compartida del canal, con tirador y tiradas para todos

### 7.3.0 · 2026-10-15

- Tragaperras: la Mina y el Olimpo, dos máquinas nuevas en `tragas`

### 7.2.0 · 2026-10-15

- Tienda: tapete del 20-N, la tele de 1975 con la caricatura de Franco

### 7.1.1 · 2026-10-15

- Tragaperras: el bote crece el doble de rápido, con el 6 % de cada tirada

### 7.1.0 · 2026-10-15

- Gallos: stats, entrenamiento y muertes en el reñidero

### 7.0.0 · 2026-10-15

- Tragaperras: `tragas` junta todas las máquinas, la clásica y el Volcán
- Rompe: el comando `volcan` desaparece; el Volcán se abre con `tragas volcan` (o la opción máquina de `/tragas`)

## 6.x

### 6.17.0 · 2026-10-15

- Guerra: `guerra`, carta contra carta y a la guerra si empatas

### 6.16.0 · 2026-10-15

- ONGs: once logros nuevos, más frases y la mascota comenta el donativo

### 6.15.1 · 2026-10-15

- Babel: `babel` ya no se corta a mitad cuando Google pide un respiro

### 6.15.0 · 2026-10-15

- Rastro: `trueque`, cambia un objeto por otro con otro miembro

### 6.14.2 · 2026-10-15

- Tragaperras y todo el bot: ya no van más lentos cuanto más se juega

### 6.14.1 · 2026-10-15

- Interno: auditoría de las loterías con el código real

### 6.14.0 · 2026-10-15

- Logros: todo lo del trabajo, en una sola entrada 🪏 Trabajo del menú

### 6.13.0 · 2026-10-15

- Mascotas: atajos a adoptar y a la comida desde el panel

### 6.12.0 · 2026-10-15

- Blackjack: el zapato dura entre manos y se pueden contar cartas

### 6.11.0 · 2026-10-15

- Torre del dragón: `torre`, sube pisos esquivando dragones

### 6.10.0 · 2026-10-15

- Tienda: cada artículo dice si ya lo tienes y cuántos

### 6.9.0 · 2026-10-15

- Aniversario: el 7 de octubre, fiesta del servidor con XP doble

### 6.8.0 · 2026-10-15

- Caballos: el bote del Gran Premio crece con el 10 % de lo que se pierde en cada carrera, hasta 50.000 Y$

### 6.7.5 · 2026-10-15

- Interno: la prueba de caducidad de la música ya no falla al azar

### 6.7.4 · 2026-10-15

- Jovani: frases repasadas, sin bromas copiadas entre juegos y con más Bibi y groypers

### 6.7.3 · 2026-10-15

- Tienda: el tapete de los archivos Epstein pasa a 3.000.000 Y$, con tres copias numeradas
- Tienda: los cambios de precio llegan también a las tiendas que ya tenían el artículo (el de Israel ya cuesta 271.000 Y$)

### 6.7.2 · 2026-10-15

- Pala: los marrones salen el doble, uno de cada tres turnos

### 6.7.1 · 2026-10-15

- Mascotas: el panel ya no deja de responder tras pedir un respiro

### 6.7.0 · 2026-10-15

- Pisos: hipoteca, VPO por sorteo, alquilar a miembros con Bono Alquiler Joven, vacacional, fondo buitre y SOCIMI

### 6.6.0 · 2026-10-15

- Pisos: `pisos`, el mercado inmobiliario de Canarias, y el Patrimonio cuenta el ladrillo

### 6.5.0 · 2026-10-14

- Tienda: nueve tapetes nuevos (PSOE, Falcon, NO-DO, Modelo 100, Prove Me Wrong, archivos Epstein, Corea del Norte, diamantes y el Gordo) y el de Israel baja a 271.000 Y$

### 6.4.1 · 2026-10-14

- Timba: al empezar la mano ya no se quedan los botones de entre manos y nadie se retira sin poder jugar

### 6.4.0 · 2026-10-14
- Rastro: `vender` y `rastro`, segunda mano entre miembros con subastas e ITP

### 6.3.1 · 2026-10-14

- Botones: se acabó «Esta interacción ha fallado» al pulsar rápido o en botones viejos, y contestan antes tras un rato sin uso
- Pollo: dos clics seguidos en 🔁 Jugar ya no cobran dos partidas

### 6.3.0 · 2026-10-14

- Backgammon: `backgammon`, contra Jovani o en duelo con cubo de doblar

### 6.2.5 · 2026-10-14

- Interno: receta para que los juegos nuevos pinten igual de rápido

### 6.2.4 · 2026-10-14

- Juegos: GIF más rápidos con los 4 pintores a la vez y Python 3.13

### 6.2.3 · 2026-10-14

- Interno: los pintores de Node ya no pasan de 4 aunque se pidan varios a la vez

### 6.2.2 · 2026-10-14

- Juegos: los GIF se dibujan y se montan bastante más rápido (los gallos y el bacará, en menos de la mitad de tiempo) y el primero tras un rato ya no arranca en frío
- Interno: el informe cuenta lo que tarda cada imagen o GIF en subir a Discord

### 6.2.1 · 2026-10-14

- Interno: las pruebas pasan también en Windows (textos en UTF-8, la hora del meme del tuit y las piezas del pachinko)

### 6.2.0 · 2026-10-14

- 4 en raya: `4enraya`, contra Jovani con retos del día o en duelo con apuesta y grada

### 6.1.2 · 2026-10-14

- Timba: la mesa de cada botón de quien habla (pasar o igualar, retirarse, cada subida y todo) se pinta mientras corre su turno, y la primera de cada mano durante la espera entre manos

### 6.1.1 · 2026-10-14

- Timba: mientras corre el turno se pinta ya la mesa de quien se duerme, y sale al momento al acabarse el tiempo

### 6.1.0 · 2026-10-14

- Timba: botón ✅ Listo entre manos (si lo pulsan todos, empieza ya) y dos minutos de espera si no
- Timba: el turno se agota cuando marca el reloj de la mesa, no unos segundos después

### 6.0.1 · 2026-10-14

- Interno: reglas para que Claude navegue el código con el grafo de graphify

### 6.0.0 · 2026-10-14

- Póker: mesa del canal para seis, 💎 Bote progresivo y el club (rango, retos y racha)
- Póker: `poker` con dos modos, contra la banca y la timba entre miembros (Texas Hold'em sin límite)
- Interno: póker, comprobación rápida de la ventaja de la casa
- Rompe: `poker` pide el modo (o lo pregunta con botones) y ya no abre una mesa propia: contra la banca es la mesa del canal y se juega sentándose con 🪑; el ante máximo depende del rango del club (10.000 Y$ al empezar).

## 5.x

### 5.25.0 · 2026-10-14

- Póker: `poker`, Casino Hold'em contra la banca

### 5.24.0 · 2026-10-13

- Pala: los ocho oficios llegan a 10 puestos con dos ramas cada uno

### 5.23.0 · 2026-10-13

- Ayuda: `ayuda` va por menús, con lo que hace cada comando

### 5.22.1 · 2026-10-13

- Novedades: se anuncian los commits directos a main que se quedaron sin aviso desde el #99

### 5.22.0 · 2026-10-13

- Versiones: el bot tiene número de versión y el aviso de novedades lo dice

### 5.21.1 · 2026-10-13

- Novedades: el changelog cuenta todo lo que entra en main, no solo los PR (5322531)

### 5.21.0 · 2026-10-13

- Tienda: tapete propio con tu imagen y `pieles` para manejar las skins (7fb2285)

### 5.20.1 · 2026-10-13

- Pachinko: la Ráfaga ×5 se anima con las 50 bolas a la vez (c2db2a8)

### 5.20.0 · 2026-10-13

- Casino: botón 🎨 del tapete en todas las mesas, también en la ruleta (#99)

### 5.19.0 · 2026-10-13

- Casino: tapetes de oro y de bandera, uno por mesa y se compran en la mesa (#98)

### 5.18.2 · 2026-10-13

- Pala: comprar y dar regalos a la familia sin salir del panel (#97)

### 5.18.1 · 2026-10-13

- Pala: los regalos para la familia se compran desde el desplegable (#96)

### 5.18.0 · 2026-10-13

- Pala: la sanidad llega a 10 puestos con ramas pública y privada (#95)

### 5.17.0 · 2026-10-12

- Pala: la obra llega a 10 puestos y la política estrena bromas (#94)

### 5.16.0 · 2026-10-12

- Jovani con más frases, coletillas y logros con sátira en todo el bot (#93)

### 5.15.0 · 2026-10-12

- Gallos: `gallos`, peleas contra la casa y duelos con apuestas (#92)

### 5.14.1 · 2026-10-12

- Novedades: salen todos los cambios, enteros y sin recortar (#91)

### 5.14.0 · 2026-10-12

- Pala: la familia frena el grind, con regalos y favores (#90)

### 5.13.1 · 2026-10-12

- Música: el reproductor ya no se queda colgado (#89)

### 5.13.0 · 2026-10-12

- Lista: páginas de 25, `/lista` arreglado y prioridad con botones (#88)

### 5.12.0 · 2026-10-12

- Pala: carrera larga con ramas y jubilación; la política llega a 10 (#87)

### 5.11.1 · 2026-10-12

- Interno: la prueba de la porra completa ya no falla a ratos (#86)

### 5.11.0 · 2026-10-12

- Pala: tres oficios nuevos (reparto, funcionariado e influencer) (#85)

### 5.10.0 · 2026-10-12

- Renta: el IRPF se reinicia cada lunes y devuelve lo cobrado de más (#84)

### 5.9.0 · 2026-10-12

- Skins: cambia el tapete de la ruleta, y pronto más mesas (#83)

### 5.8.0 · 2026-10-12

- Tienda: títulos para lucir junto al nombre y caprichos caros (#82)

### 5.7.0 · 2026-10-11

- Bacará: `bacara`, Punto y Banca con zapato compartido y apretón (#81)

### 5.6.1 · 2026-10-11

- Pala: el minijuego ya no cuenta como fallo y fichar contesta antes (#80)

### 5.6.0 · 2026-10-11

- IMV por día natural y `pala` dice cuándo vuelves a fichar (#79)

### 5.5.0 · 2026-10-11

- Blackjack: seguro también con blackjack (dinero par) y tres logros (#78)

### 5.4.0 · 2026-10-11

- Interno: el bot cuenta cuánto se usa cada cosa y `informe` lo descarga (#77)

### 5.3.0 · 2026-10-11

- Pollo: nueva mesa al estilo del casino y el paso sale al momento (#76)

### 5.2.4 · 2026-10-10

- Autobús y caballos: más rápidos y sin navegador (#75)

### 5.2.3 · 2026-10-10

- Dados y ruleta: más rápidos y sin navegador (#74)

### 5.2.2 · 2026-10-10

- Interno: receta para acelerar los juegos como la moneda (#73)

### 5.2.1 · 2026-10-10

- Cara o cruz: la moneda sale nada más pulsar (#72)

### 5.2.0 · 2026-10-10

- Novedades: el aviso se puede publicar en un hilo con `cambios` (#71)

### 5.1.0 · 2026-10-10

- Novedades: aviso detallado como en GitHub y comando `cambios` (#70)

### 5.0.2 · 2026-10-10

- Cara o cruz: el botón responde al momento y no chiva el resultado (#69)

### 5.0.1 · 2026-10-10

- Cara o cruz: la moneda se dibuja sin navegador (#68)

### 5.0.0 · 2026-10-10

- Autobús: el comando pasa a ser guagua (#67)
- Rompe: `autobus` pasa a llamarse `guagua`.


## 4.x

### 4.6.0 · 2026-10-10

- Blackjack: seguro y sin tope (#66)

### 4.5.0 · 2026-10-10

- Autobús: `autobus`, el Ride the Bus con las probabilidades a la vista (#65)

### 4.4.2 · 2026-10-10

- Optimizar la Rule (#64)

### 4.4.1 · 2026-10-10

- Arreglo Rule (#63)

### 4.4.0 · 2026-10-10

- Ruleta: rayos de ×500, rueda nueva y números calientes y fríos (#62)

### 4.3.0 · 2026-10-10

- Dados: `dados`, craps con Pase, No pase y Odds sin comisión (#61)

### 4.2.0 · 2026-10-10

- Cara o cruz: `moneda`, doble o nada con monedas que caen de canto (#60)

### 4.1.1 · 2026-10-10

- Tragaperras: los giros gratis dentro de giros gratis se suman (#59)

### 4.1.0 · 2026-10-10

- Caballos: parrilla de 2 minutos y botón ✅ Listo para salir antes (#58)

### 4.0.0 · 2026-10-09

- Perfil: un solo menú, lotería a medida del servidor y blackjack con empate (#57)
- Rompe: `nivel`, `patrimonio`, `logros` y `mochila` desaparecen; ahora es `perfil`.


## 3.x

### 3.19.1 · 2026-10-09

- Interno: los agentes de IA solo usan Haiku y Sonnet, de uno en uno (#56)

### 3.19.0 · 2026-10-09

- Tragaperras: re-giro, doble o nada, giro del día y bote con tope (#55)

### 3.18.0 · 2026-10-09

- Pachinko: autoplay y la siguiente tanda lista al pulsar (#54)

### 3.17.0 · 2026-10-09

- Pachinko: las bolas chocan entre sí; Tragaperras: autoplay animado (#53)

### 3.16.0 · 2026-10-09

- Pachinko: máquina con neón, bolas metálicas y bolsillos con relieve (#52)

### 3.15.0 · 2026-10-09

- Pachinko: las bolas caen con física real (#51)

### 3.14.2 · 2026-10-09

- Casino: las animaciones de los juegos se preparan mucho más rápido (#50)

### 3.14.1 · 2026-10-08

- Botones: responden al momento y ya no fallan (#49)

### 3.14.0 · 2026-10-08

- Beernight: `beernight`, mandamientos, eventos, chivatazos e histórico (#48)

### 3.13.0 · 2026-10-08

- Novedades: el bot publica en Discord los PR de cada despliegue (#47)

### 3.12.0 · 2026-10-08

- Caballos (#46)

### 3.11.0 · 2026-10-07

- Claude/adoring tesla ybmnco (#45)

### 3.10.0 · 2026-10-07

- Porras: apuestas entre miembros sobre las jugadas de otro (#44)

### 3.9.0 · 2026-10-07

- Pala: clics que no se pierden, minijuegos más claros y herramientas (#43)

### 3.8.0 · 2026-10-06

- Patrimonio hacienda (#42)

### 3.7.0 · 2026-10-06

- Estadísticas del casino: comando apuestas con siete páginas (#41)

### 3.6.0 · 2026-10-06

- Comando reinicio: actualizar y reiniciar el bot desde Discord (#40)

### 3.5.0 · 2026-10-06

- Tienda: surtido de serie con 155 artículos, pasillos y objetos que se usan (#39)

### 3.4.1 · 2026-10-06

- Actualizar sin git (#38)

### 3.4.0 · 2026-10-06

- Logros compactos (#37)

### 3.3.0 · 2026-10-06

- Autoactualización del bot en el NAS (actualizar.sh) (#36)

### 3.2.0 · 2026-10-06

- Logros risas (#35)

### 3.1.0 · 2026-10-06

- Pollo (Chicken Road) con GIF de tensión y autocobro; casino unificado (#34)

### 3.0.0 · 2026-10-06

- Limpieza: fuera la hora feliz, lotería en positivo y /hongkong (#33)
- Rompe: se quita la hora feliz (XP ×2) y sus logros.


## 2.x

### 2.5.0 · 2026-10-06

- Intereses: cuenta remunerada diaria por tramos, con liquidación (#32)

### 2.4.1 · 2026-10-06

- pala: sueldos calibrados frente al IMV (escala de nóminas a 100 Y$/€) (#31)

### 2.4.0 · 2026-10-06

- Pala (#30)

### 2.3.0 · 2026-10-06

- Trabajo: `pala`, tres oficios con minijuegos, nóminas y ascensos (#29)

### 2.2.0 · 2026-10-05

- Botes: el Volcán, tragaperras de monedas, recogida y bonus (`volcan`) (#28)

### 2.1.0 · 2026-10-05

- Lista: tareas compartidas del servidor ordenadas por prioridad (#27)

### 2.0.0 · 2026-10-05

- Nombres de comandos en español y norma en la Biblia (#26)
- Rompe: 27 comandos cambian de nombre al español (`play`→`poner`, `help`→`ayuda`…).


## 1.x

### 1.19.0 · 2026-10-05

- Bizum entre miembros, exento de Donaciones, con sus logros (#25)

### 1.18.1 · 2026-10-05

- Purge: borra los últimos mensajes, no los de hace casi 14 días (#24)

### 1.18.0 · 2026-10-05

- Loterías del Estado: Nacional, Primitiva, Bonoloto, Gordo y Euromillones (#23)

### 1.17.2 · 2026-10-05

- Minas: de 1 a 12 minas, sin tope y el menú enseña el premio gordo (#22)

### 1.17.1 · 2026-10-05

- Logros cogs puente (#21)

### 1.17.0 · 2026-10-05

- Tienda: escaparate con botones, caja con IGIC, mochila y trastienda (#20)

### 1.16.0 · 2026-10-05

- Pachinko: cuatro tableros a elegir, del más tranquilo al más loco (#19)

### 1.15.0 · 2026-10-04

- Pachinko: tablero de clavos, reach, atari y rush con botones (#18)

### 1.14.0 · 2026-10-04

- Comando niveles para importar el historial y encender los niveles (#17)

### 1.13.1 · 2026-10-04

- Crash minas tension (#16)

### 1.13.0 · 2026-10-04

- Crash y Minas: cohete compartido por canal y tablero de 5×5 (#15)

### 1.12.0 · 2026-10-04

- Tragaperras (#14)

### 1.11.0 · 2026-10-04

- Bienvenidas (#13)

### 1.10.0 · 2026-10-04

- Logros (#12)

### 1.9.0 · 2026-10-04

- Cumpleaños: cumple, cumples, anuncio con regalo y felicitaciones pagadas (#11)

### 1.8.0 · 2026-10-04

- Niveles con voz, IMV, IRPF a la española, Hacienda y Campaña de la Renta (#10)

### 1.7.0 · 2026-10-03

- Blackjack con botones (/blackjack, .blackjack, .bj) (#9)

### 1.6.0 · 2026-10-03

- Ruleta: varias apuestas en la misma tirada (#8)

### 1.5.0 · 2026-10-03

- Comando babel: frases, apodos y canales por 99 idiomas (#7)

### 1.4.0 · 2026-10-03

- Ayuda unificada en un embed y comandos de administración (#6)

### 1.3.0 · 2026-10-03

- Economía de yapdollars y ruleta americana con botones (#5)

### 1.2.1 · 2026-10-03

- Listar los efectos de imagen en .help (#4)

### 1.2.0 · 2026-10-03

- Efectos de imagen de Dank Memer como comandos de texto (#3)

### 1.1.0 · 2026-10-03

- Sonidos de entrada al canal de voz y voz ensordecida (#2)

### 1.0.0 · 2026-10-03

- Unificar el prefijo de texto en "." y alinear tests y docs (#1)
- Primera versión estable: el prefijo `.` queda fijo.


## 0.x: desarrollo inicial

### 0.7.0 · 2026-10-01

- Cambios en los prefijos del bot (eb5010c)

### 0.6.0 · 2026-10-01

- Despliegue con Docker en el NAS (db11421)

### 0.5.0 · 2026-10-01

- Un solo nombre por comando, `magik` y ayuda más simple (5b2b4eb)

### 0.4.0 · 2026-10-01

- Todos los comandos con `/` también como texto, y ayuda (ec66e98)

### 0.3.0 · 2026-09-29

- Bienvenida y despedida, reproductor de música y comandos con prefijo º (3f1e9aa)

### 0.2.0 · 2026-09-28

- Estadísticas y niveles (f577287)

### 0.1.0 · 2026-09-28

- Estructura inicial del bot y comando `ping` (2343cd6)
