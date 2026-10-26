# Auditoría de las ONGs (`donar`, 15/10/2026)

Repaso de `bot.services.donations`, `bot.cogs.donations` y la deducción de
`bot.services.taxes.donation_deduction`, con lo que salió y lo que se cambió.

## Lo que está bien

- **Fiscalidad real.** La deducción es la del art. 19.1 de la Ley 49/2002 tras la
  reforma de 2024: el 80 % de los primeros 250 € al año (desde la 8.0.0, unos
  1.202 Y$ a la semana con la vara del IRPF; antes, 2.500 Y$) y el 40 % del resto,
  con el límite del 10 % de la base (art. 69.1 LIRPF). La deducción rebaja la cuota
  de la declaración del lunes y nunca la deja en negativo. El donativo no paga IGIC
  (no hay contraprestación, art. 4 de la Ley 20/1991) ni Donaciones (la ONG no es persona física, art. 3 de la Ley 29/1987). No
  se modela el 45 % de los donativos recurrentes (tres años a la misma entidad): con
  la renta semanal del bot no tendría sentido.
- **Es un sumidero limpio.** Lo donado se queda en el monedero de la ONG (ids
  negativos) y no vuelve a nadie; lo que se recupera sale del Estado y como mucho es
  el 80 % de lo donado, así que donar siempre cuesta. Los monederos de las ONGs no
  salen en `fortunas`, ni en el Patrimonio, ni en las estadísticas de miembros (todas
  esas consultas piden `user_id > 0`).
- No se puede donar 0 ni en negativo (`EconomyService.donate`) ni más de lo que se
  tiene, y `/donar` lleva el aviso de la Renta.

## Lo que se ha cambiado

- **Logros:** había 8 (lo donado en cinco escalones y las cuatro ONGs). La Biblia pide
  al menos una docena por funcionalidad y todas sus familias. Ahora son 19: además,
  donativos por número (10, 100 y 1.000), 10.000 Y$ a cada ONG (uno por ONG),
  Navidad, y secretos con guasa (1 Y$, 666 Y$ y donar todo el saldo). Salen de
  `donation_stats`.
- **Frases:** cada ONG tenía cuatro maneras de gastarse tu dinero; ahora nueve, con
  Waterloo, Koldo, los puros de Bibi, Davos, el tapón pegado y la motosierra de Milei.
- **Mascota:** al donar hablaba de dinero en general (`Moment()`); ahora tiene su
  momento (`Event.DONATE`) con frases propias.

## Segunda auditoría (26/10/2026): más juego

La tarea de la lista pedía que donar fuera más interesante, no más fiscal. Donar solo
servía para desgravar, y con cuatro ONGs y un embed fijo no había nada que mirar ni
perseguir. Lo que se ha hecho:

- **Ocho ONGs.** Cuatro nuevas con temas que no tenían las de antes: videojuegos
  (🎮 Gamers Sin Fronteras), redes (🤳 Protectora de Influencers Abandonados), Canarias
  (🍌 Salvemos el Plátano Canario) y el propio bot (🤖 Amigos del Bot Jovani). Monederos
  `-5` a `-8`.
- **Panel.** `donar` sin cantidad abre un `LayoutView` con la portada (regla, tus niveles
  y el muro de donantes) y la ficha de cada ONG en un menú, con botones para donar. Los
  botones aceptan el clic antes de leer la base de datos (`test_button_speed`).
- **Mecenas.** Lo donado a cada ONG da un nivel con ella (Bronce 5.000, Plata 25.000, Oro
  100.000 y Patrono 500.000 Y$). Es el objetivo a largo plazo que faltaba: cada nivel
  regala algo y da más boletos en el sorteo.
- **Sorteo de los viernes.** Cada ONG sortea un regalo entre quienes le donaron en los
  siete días anteriores. Va los viernes a las 20:00 para no amontonarse con la Renta y el
  Patrimonio de los lunes. Se apunta en `economy_ong_raffles` (una vez por ONG y
  sorteo), así que un reinicio no lo repite.
- **Regalos.** El premio suele ser algo de la tienda de hasta 500 Y$ (los mismos que da
  la caja botín, `shop.giftable`). Un 15 % de las veces, y siempre al llegar a Oro y a
  Patrono, es uno de los 40 exclusivos de la ONG: artículos ocultos del surtido, como las
  piezas de las cajas, que no se venden. Los regalos no mueven dinero.
- **Logros:** 15 más (42 en total): las 8 ONGs, los cuatro niveles de mecenas, los
  sorteos ganados, los regalos y los exclusivos.

Se quedó fuera una deducción canaria por donativos (art. 4 ter del DL 1/2009, un 20 %
más de los primeros 150 €). Es real, pero haría que donar el primer tramo saliera gratis.
