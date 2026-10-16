# Informes de uso

Aquí se guardan los informes que saca un administrador con `/informe` (un JSON por
informe, con el nombre que trae: `informe-AAAA-MM-DD.json`). Sirven para que quien
trabaje en el bot decida mejoras con datos y no a ojo.

Llevan cantidades y seudónimos (u1, u2…), nunca mensajes ni IDs de miembros. Los
seudónimos cambian de un informe a otro: u1 de un informe no es u1 del siguiente.

Si un administrador eligió canal con `/informe canal:`, el último se baja aquí con
`python docs/descargar_informe.py` (lee `INFORME_TOKEN` e `INFORME_CANAL` del `.env`).

Para resumir uno:

```bash
python docs/analisis_metricas.py docs/metricas/informe-AAAA-MM-DD.json
```

El formato lo define `build_report` en `src/bot/services/telemetry.py`.
