# Pisos por calles: calles, edificios y unidades

Plan acordado el 24/10/2026. El barrio de siempre (su ambiente y sus diez tarjetas) se
queda, y un botón 🗺️ **Calles** abre el plano del barrio: 3 calles con 3 edificios cada una.
Al elegir un edificio se ve **en corte**, partido por la mitad: el sótano con sus garajes y
trasteros, el bajo con sus locales, las plantas con sus viviendas, el ático y la azotea, y en
cada hueco qué es, quién lo usa y de quién es.

Decisiones del usuario (no se vuelven a preguntar):

- **Tamaño:** 3 calles × 3 edificios por barrio.
- **El edificio:** las dos cosas. Cada unidad tiene sus dueños, y además el edificio (el vuelo
  y la azotea) es una propiedad aparte. Quien junta todas las unidades y el vuelo tiene el
  edificio entero, con sus ventajas.
- **Negocios en casas:** con un cambio de uso (obra y licencia de actividad).
- **Barrio:** se queda como está y añade 🗺️ Calles.

## Compatibilidad: ninguna clave cambia

Todo lo que se guarda (participaciones, hipotecas, contratos, candidatos, noticias, ingresos,
reformas, logros) va por la clave del inmueble. Por eso:

- Los 180 inmuebles del catálogo conservan su clave, sus metros, su planta y su precio.
- **Cada edificio es el de uno de los 9 destacados de su barrio**: 3 × 3 = 9. La tarjeta N del
  barrio vive en el edificio N del plano.
- Las unidades nuevas tienen claves nuevas (`<edificio>_<hueco>`, p. ej. `triana_e3_2b`) y salen
  sin dueño, a la venta.
- Las plantas y habitaciones de la 9.12-9.13 (`clave~n`, `clave~hn`) siguen igual: son las
  unidades de su edificio cuando se dividió.
- El catálogo de calles se genera con una regla fija a partir del destacado y del tema del
  barrio; una prueba fija su huella (claves, tipos, metros) para que no cambie sin querer.

## Qué edificio sale de cada destacado

| Destacado | Edificio | Unidades |
|---|---|---|
| estudio, bajo, piso2, piso3, ático, loft, dúplex, local | **Bloque** con el destacado en su planta | sótano (garajes y trasteros), bajo (locales o vivienda), plantas (viviendas A y B), ático |
| garaje | **Aparcamiento** | plazas de garaje |
| trastero | **Guardamuebles** | trasteros |
| chalé, terrera, cueva, finca, casona | **Casa** | ella sola |
| bungalow | **Complejo de bungalows** | varios bungalows |
| amarre | **Pantalán** del puerto | varios amarres de distinta eslora |
| nave | **Polígono** | dos naves |
| edificio | **Edificio** entero (propiedad vertical) | él solo; al dividirlo por plantas, sus plantas |

La planta del destacado manda: un «5.º sin ascensor» vive en un bloque de al menos seis plantas.
Las plantas, las viviendas por planta y el sótano dependen del tema del barrio (el centro, alto
y con garajes; el barrio marinero, bajo y sin sótano; el rural, casas).

Juego por etapas: **early** trasteros y garajes (~100.000-300.000 Y$), **mid** pisos y locales,
**late** juntar edificios, su vuelo y las obras del edificio.

## Fases

1. **Calles y edificios** (9.14.0): el catálogo de calles con nombres reales, los edificios y
   sus unidades; las pantallas 🗺️ Calles y 🏢 Edificio (en corte); cada unidad se compra,
   alquila, divide y vende como cualquier inmueble; el índice del barrio pesa todas sus
   unidades; logros; simulación del equilibrio (early game y Falcon).
2. **El vuelo y la azotea** (derecho de vuelo, art. 16 del Reglamento Hipotecario): en cada
   bloque, una propiedad aparte. Su dueño alquila la azotea (antena, publicidad en la fachada) y
   puede levantar una planta (obra y licencia: unidades nuevas). Edificio entero = todas las
   unidades más el vuelo.
3. **La comunidad del edificio** (Ley de Propiedad Horizontal): junta de todos los dueños de las
   unidades con su cuota, presidente, obras del edificio (ascensor, rehabilitar la fachada,
   placas solares) y derramas repartidas.
4. **Negocios en casas**: cambio de uso de terreras, casonas, chalés, fincas y cuevas (obra y
   licencia de actividad, con IGIC, ICIO y tasa) para montar ahí un negocio de `pala`.

## Contrato de la fase 1

- `bot.services.housing_streets`: `Street`, `Building` y el catálogo generado (`STREETS`,
  `BUILDINGS`, `BUILDING_BY_KEY`, `STREET_UNITS`, `STREET_UNIT_BY_KEY`, `building_of(clave)`).
- `Market.base` resuelve las unidades de las calles; `Market.keys` las incluye; el índice
  (`barrio_weight`, `index_level`) pesa todas las unidades del barrio.
- Pantallas: `calles` (`housing_view.streets_state`) y `edificio`
  (`housing_view.building_state`), con un botón por edificio y un desplegable de unidades.

## Lo que salió de la fase 1 (9.14.0)

- 866 unidades nuevas (221 pisos de 2, 178 de 3, 179 plazas, 135 trasteros, 64 locales, 39
  áticos, 24 amarres, 18 bungalows, 4 bajos y 4 naves): de ~10 en Tafira a ~80 en Santa Cruz.
- **Índice:** los nueve destacados pesan como antes (juntos, 1) y las unidades de las calles,
  juntas, `STREETS_WEIGHT` = 1. Repartir el peso entre todo el barrio dejaba a los nueve en un
  11 %: los barrios tensionados de hoy se habrían destensado solos.
- **Falcon** (`docs/economia_sim.py --calles`): con el generador primero salía el día 342,
  porque los bloques de un estudio eran todos estudios (lo que más renta por lo que cuesta). Con
  vecinos del tamaño del barrio y plazas y trasteros nuevos un 25 % más baratos de alquilar,
  sale el día 356-361 (sin calles, 363-367): dentro de los 350-390 de `CLAUDE.md`.

## 9.15.0: calles mezcladas y más densas

El usuario, al probarlo: el plano tiene que salir al elegir el barrio, tiene que haber casas
normales entre los bloques y el centro tiene que tener más edificios que el campo. Así que:

- Elegir un barrio abre `calles` (con el índice, el precio y la noticia del barrio); la pantalla
  de siempre queda en ⭐ Destacados.
- `EXTRA` y `EXTRA_HOUSES` en `housing_streets.py`: bloques y casas de más, sin destacado, con
  claves `e10` en adelante. 15 edificios en el centro y los obreros, 12 en los demás urbanos, 9
  en los rurales; repartidos por las calles con una regla fija.
- Las 866 unidades de la 9.14.0 siguen idénticas (prueba con su huella, `UNITS_9_14`).
- Falcon (`--calles`): día 356-360.

## 9.16.0: áticos caros y edificios variados

- `Theme.penthouse`: en la mitad de los bloques (por `_seed(clave, "atico")`) de los barrios
  con vistas, una planta nueva arriba con un ático caro: sobreático (`_sa`) sobre un ático, o
  ático con vistas (`_va`) donde no lo había. `PENTHOUSE_RENT` 25 % y `PENTHOUSE_VALUE` 40 %;
  1,3 veces los metros del bloque. 54 en total. Las 1.271 unidades de la 9.15 no cambian
  (`UNITS_9_15`). Falcon: día 356-360.
- La escena del corte varía por tema, edificio y unidad (subagente).
