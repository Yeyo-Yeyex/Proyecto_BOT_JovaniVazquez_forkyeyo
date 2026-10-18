# Auditoría de tono de las frases (18/10/2026)

Resultado de `docs/auditoria_frases.py` tras la auditoría de tono de la Biblia
(«Personalidad», «Auditoría de tono»). El script junta las listas de frases del bot (las
tuplas de textos que se eligen al azar) y mira coletillas de Jovani, bromas copiadas de un
juego a otro, fuentes de ironía y, por partes (`--parte`), qué parte de cada lista es
normal. `tests/unit/test_auditoria_frases.py` lo pasa en cada prueba.

## La norma

- No todo es una broma: al menos la mitad de cada lista, frases normales; lo que informa
  (reglas, cifras, errores) va en claro.
- El bot es Jovani Vázquez, el cantante: sus frases («¡Toma mangó!», «¿Con pulpa? ¡Sin
  pulpa!», «Hazme café… ¡Quiero café!»…) salen a menudo, hasta la mitad de una lista. Lo
  típico de Puerto Rico («¡Wepa!», «mi amor», «¡Ay, bendito!», «papi») no es suyo y no sale.
- Todas las fuentes de bromas al mismo nivel: la política es una más; lo que cansa es
  repetir el mismo tema o la misma figura.
- Nada de la muleta «X, como [referencia]».
- El contenido se queda: solo cambian los textos que lo rodean.

## Antes y después

| | Antes (15/10) | Después (18/10) |
|---|---:|---:|
| Frases normales en todo el bot | ~25 % | 73 % (1.923 frases, 301 listas) |
| Casino | ~20-25 % | 69 % (960 frases) |
| Casino con política | 42 % | ~9 % |
| Comunidad (cumpleaños, niveles, importación) | 5 % | 48 % |
| Tienda, Rastro y mascotas | 64 % | 76 % |
| Bibi / Netanyahu | 67 | 18 |
| Groypers | 28 | 0 |

«Sánchez y el PSOE» sigue arriba (429), pero casi todo es Perro Sanxe, el nombre del
recaudador (`TAX_COLLECTOR`): es un personaje del juego, no una broma.

## Lo que queda

- Los marrones y minijuegos de los oficios (`services/work_catalog.py`, ~3.900 textos): 9 %
  de política, casi toda en las ramas de política y funcionariado, donde es el tema. Son
  contenido; se revisan si se tocan.
- Los carteles fijos de las escenas del casino y los textos sueltos que no son listas.
- Los nombres de los artículos y sus descripciones en `services/shop_catalog.py`.
