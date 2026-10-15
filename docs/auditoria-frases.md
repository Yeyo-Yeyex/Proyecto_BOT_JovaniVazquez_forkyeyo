# Auditoría de las frases de Jovani (15/10/2026)

Resultado de `docs/auditoria_frases.py`, que junta las 226 listas de frases del bot
(las tuplas de textos que se eligen al azar) y mira tres cosas: si las coletillas de
Jovani salen «de vez en cuando» como pide la Biblia, si hay bromas copiadas de un juego
a otro y cuánto sale cada fuente de ironía. `tests/unit/test_auditoria_frases.py`
la pasa en cada prueba.

## Lo que salió

- **Coletillas de más.** Once listas pasaban de un tercio de frases con coletilla. La
  peor, el aviso de importar mensajes (4 de 5); luego las frases de Jovani contra
  quien le usa un objeto, las del backgammon y el 4 en raya, el casi del volcán y los
  premios gordos de las tragaperras. Se bajaron metiendo bromas nuevas sin coletilla,
  no quitando las que había.
- **Bromas copiadas.** El 4 en raya se hizo copiando las listas del backgammon: las
  cuatro rendiciones, las cuatro siestas y media charla de Jovani eran idénticas. Las
  tragaperras, el volcán y el pachinko compartían «Como el AVE a Extremadura», «La
  máquina se hace la difícil» y «Quiero café.»; la ruleta y las tragaperras, «💥 ¡TOMA
  MANGÓ!». Se reescribieron las del juego más nuevo. Las exclamaciones cortas («¡Eso
  es!», «¡Toma mangó!») pueden repetirse entre juegos: no son una broma, son Jovani.
- **Fuentes.** Netanyahu salía 18 veces y los groypers ninguna. Ahora Bibi sale en
  casi todas las mesas (el juicio que se aplaza, los puros y el champán, el cartel de la
  bomba en la ONU, el indulto, las coaliciones, la Cúpula de Hierro) y Nick Fuentes con
  sus groypers también (el sótano de su madre, el ejército de ranas, la rana con la
  barbilla en la mano, los baneos, las preguntas groyper a Charlie Kirk). Las que siguen
  más flojas son Epstein, Kirk, Vox, África y Musk.

| Fuente | Antes | Después |
|---|---:|---:|
| Netanyahu (Bibi) | 18 | 67 |
| Groypers (Nick Fuentes) | 0 | 28 |

## Lo que no se toca

La sátira sigue la Biblia: contra Netanyahu, su Gobierno y Nick Fuentes, nunca contra
los judíos ni los israelíes; nada de víctimas ni de guerra, y
nada de repetir los lemas del movimiento groyper.
