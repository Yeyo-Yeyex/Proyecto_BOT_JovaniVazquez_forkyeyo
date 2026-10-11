"""Legislatura: roguelike de cartas con baraja española (motor puro, sin Discord).

Inspirado en Balatro. Una partida es una legislatura de 4 años con dos periodos
de sesiones cada uno (el Congreso los tiene de septiembre a diciembre y de
febrero a junio, art. 73.1 de la Constitución): 8 periodos, y en cada periodo
tres sesiones (ordinaria, extraordinaria y moción de censura). En cada sesión
hay que reunir los votos que pide con unas pocas manos y descartes; si no se
llega, cae el Gobierno y se acaba la partida.

Capas, de abajo arriba (cada una solo importa las de debajo):

- `cards`: la baraja de 40, con etiquetas (mejoras), ediciones y sellos.
- `hands`: qué combinación forma una jugada y qué cartas puntúan.
- `rng`: azar con semilla y por flujos, para que la partida diaria sea igual
  para todos y se pueda reproducir.
- `effects`: el contrato de los efectos (ganchos) y el contexto de la
  puntuación, que apunta cada paso para poder enseñarlo en Discord.
- `catalog`: todo lo que existe en la partida (asesores, encuestas,
  normativas, cloacas, tratados, sobres, jefes, gobiernos, primas y
  abstenciones). Cada cosa es una fila con su efecto.
- `scoring`: la cadena de puntuación de una jugada.
- `run`: el estado de la partida (serializable a JSON) y las acciones del
  jugador, que validan la fase y lanzan `GameError` con un texto para el usuario.
- `market`: la tienda de dentro de la partida (no confundir con la `tienda` del bot).
- `prizes`: entradas y premios en yapdollars por prima de riesgo.
- `cosmetics`: tapetes, dorsos y aspectos que se compran en la `tienda` del bot.

Nada de aquí toca la base de datos ni el dinero: el cog guarda el estado con
`bot.repositories.legislatura` y cobra y paga con `EconomyService`. El detalle
de la arquitectura y las decisiones pendientes están en `docs/legislatura.md`.
"""
