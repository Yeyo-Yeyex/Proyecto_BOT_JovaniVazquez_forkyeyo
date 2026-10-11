"""Pruebas de `docs/analisis_metricas.py` con informes sintéticos pequeños.

El script vive en `docs/` (no es paquete), así que se carga por ruta. Las secciones
son funciones puras, de modo que se prueban sin tocar el disco salvo en `main`.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "analisis_metricas.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analisis_metricas", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


am = _load()


def _entry(kind, name, events, *, errors=0, users=1, ms=None, buckets=(0, 0, 0, 0), ms_max=0):
    return {
        "tipo": kind,
        "nombre": name,
        "eventos": events,
        "errores": errors,
        "usuarios": users,
        "ms_medio": ms,
        "ms_max": ms_max,
        "ms": dict(zip(["<500", "<1000", "<3000", ">=3000"], buckets, strict=True)),
    }


def _report() -> dict:
    per_day = {}
    for i in range(30):
        day = f"2026-09-{i + 1:02d}"
        late = i >= 15
        per_day[day] = {
            "message": {"eventos": 20 if late else 10, "errores": 0, "usuarios": 4},
            "slash": {"eventos": 5, "errores": 0, "usuarios": 2},
        }
    activity = {
        # u1..u4 estrenan `moneda` en la primera semana; u1 y u2 vuelven tras el día 14.
        "u1": {"slash:moneda": [0, 20], "button:CoinView.heads": [1]},
        "u2": {"text:moneda": [2, 15], "button:CoinView.tails": [3]},
        "u3": {"slash:moneda": [4], "message:#chat": [25]},
        "u4": {"slash:moneda": [5]},
        "u5": {"slash:ruleta": [10]},
    }
    return {
        "version": 1,
        "generado": "2026-09-30T12:00:00",
        "desde": "2026-09-01",
        "hasta": "2026-09-30",
        "dias": 30,
        "uso": [
            _entry(
                "slash", "moneda", 40, errors=2, users=3, ms=900, buckets=(3, 2, 1, 1), ms_max=4000
            ),
            _entry("text", "moneda", 10, users=2),
            _entry("slash", "ruleta", 3, users=1, ms=100, buckets=(3, 0, 0, 0), ms_max=150),
            _entry("button", "CoinView.heads", 25, errors=1, users=3, ms=300, buckets=(5, 0, 0, 0)),
            _entry("button", "CoinView.tails", 15, users=2),
            _entry("message", "#chat", 500, users=5),
            _entry("voice", "#sala", 90, users=2),
            _entry("render", "coin", 12, ms=1200.0, ms_max=2500),
            _entry("log", "WARNING:bot.cogs.x", 7),
        ],
        "por_dia": per_day,
        "por_hora": {"message": [0] * 20 + [5, 9, 1, 0], "slash": [0, 4] + [0] * 22},
        "comandos_sin_uso": ["pala", "caballo"],
        "actividad": activity,
        "sistema": {
            "2026-09-29": {
                "memoria_mb_max": 1900.0,
                "lag_ms_max": 300.0,
                "chromium_max": 2,
                "node_max": 1,
                "gateway_ms_media": 80.0,
            },
            "2026-09-30": {"memoria_mb_max": 800.0, "lag_ms_max": 10.0, "gateway_ms_media": None},
        },
        "casino": [
            {
                "juego": "moneda",
                "jugadas": 10,
                "jugadores": 3,
                "apostado": 100,
                "pagado": 120,
                "rtp": 1.2,
            },
            {
                "juego": "dados",
                "jugadas": 0,
                "jugadores": 0,
                "apostado": 0,
                "pagado": 0,
                "rtp": None,
            },
            {
                "juego": "ruleta",
                "jugadas": 5,
                "jugadores": 2,
                "apostado": 100,
                "pagado": 90,
                "rtp": 0.9,
            },
        ],
        "economia": {
            "masa_monetaria": 5000,
            "miembros_con_saldo": 4,
            "gini": 0.4321,
            "top10_pct": 0.35,
            "cuentas_sistema": {"estado": 777},
            "motivos": [
                {"motivo": "casino", "movimientos": 10, "entradas": 50, "salidas": 70},
                {"motivo": "sueldo", "movimientos": 2, "entradas": 500, "salidas": 0},
            ],
        },
    }


def _empty() -> dict:
    return {
        "version": 1,
        "desde": "2026-09-01",
        "hasta": "2026-09-07",
        "dias": 7,
        "uso": [],
        "por_dia": {},
        "por_hora": {},
        "comandos_sin_uso": [],
        "actividad": {},
        "sistema": {},
        "casino": [],
        "economia": {},
    }


def _text(lines: list[str]) -> str:
    return "\n".join(lines)


def test_informe_casi_vacio_no_revienta_y_dice_sin_datos():
    text = _text(
        am.build_text({"version": 1, "desde": "2026-09-01", "hasta": "2026-09-07", "dias": 7})
    )
    assert "## Resumen" in text
    assert "Sin datos." in text


def test_informe_vacio_con_todas_las_claves_no_revienta():
    text = _text(am.build_text(_empty()))
    assert "## Casino" in text and "Sin datos." in text


def test_informe_sin_fechas_validas_no_revienta():
    text = _text(am.build_text({"desde": None, "hasta": "x", "uso": None, "actividad": []}))
    assert "## Tendencia" in text


def test_secciones_salen_en_el_orden_pedido():
    text = _text(am.build_text(_report()))
    titles = [line for line in text.splitlines() if line.startswith("## ")]
    assert titles == [
        "## Resumen",
        "## Lentos",
        "## Fallos",
        "## Lo más y lo menos usado",
        "## Comandos sin uso",
        "## Tendencia",
        "## Horas punta",
        "## Retención por funcionalidad",
        "## Canales",
        "## Sistema",
        "## Dibujos",
        "## Log",
        "## Casino",
        "## Economía",
    ]


def test_resumen_cuenta_eventos_por_tipo_y_usuarios_distintos():
    text = _text(am.section_summary(_report(), 10))
    assert "2026-09-01 a 2026-09-30 (30 días)" in text
    assert "slash 43" in text and "button 40" in text
    assert "Usuarios distintos con actividad: 5" in text


def test_lentos_ordena_por_proporcion_y_marca_los_de_tres_segundos():
    lines = am.section_slow(_report(), 10)
    rows = [
        line for line in lines if line.startswith("| ") and ("slash:" in line or "button:" in line)
    ]
    assert "slash:moneda" in rows[0] and "⚠️" in rows[0]
    # 7 medidos: 2 son >=1 s (2/7) y 1 es >=3 s (1/7).
    assert "28.6 %" in rows[0] and "14.3 %" in rows[0]
    assert all("⚠️" not in row for row in rows[1:])


def test_lentos_ignora_entradas_sin_latencia():
    text = _text(am.section_slow(_report(), 10))
    assert "text:moneda" not in text and "message:" not in text


def test_fallos_calcula_la_tasa_y_solo_incluye_interacciones():
    text = _text(am.section_failures(_report(), 10))
    assert "slash:moneda | 2 | 40 | 5.0 %" in text
    assert "button:CoinView.heads | 1 | 25 | 4.0 %" in text
    assert "ruleta" not in text


def test_fallos_sin_errores_lo_dice():
    assert "Sin errores" in _text(am.section_failures(_empty(), 10))


def test_uso_suma_slash_y_text_y_agrupa_botones_por_vista():
    text = _text(am.section_usage(_report(), 10))
    # slash 40 + text 10; usuarios distintos por actividad (u1, u2, u3, u4).
    assert "| moneda | 50 | 4 |" in text
    assert "| CoinView | 40 | 2 |" in text


def test_uso_menos_usados_no_repite_los_del_top():
    text = _text(am.section_usage(_report(), 1))
    assert "Comandos: más usados" in text and "Comandos: menos usados" in text
    assert "| ruleta | 3 | 1 |" in text


def test_uso_sin_actividad_suma_usuarios_de_las_entradas():
    report = _report()
    report["actividad"] = {}
    assert "| moneda | 50 | 5 |" in _text(am.section_usage(report, 10))


def test_sin_uso_lista_los_comandos():
    assert "2: pala, caballo" in _text(am.section_unused(_report(), 10))


def test_tendencia_compara_mitades_con_porcentaje():
    text = _text(am.section_trend(_report(), 10))
    # message: 10/día la primera mitad, 20/día la segunda; slash no cambia.
    assert "| message | 10.0 | 20.0 | +100 % |" in text
    assert "| slash | 5.0 | 5.0 | +0 % |" in text
    assert "voice" not in text


def test_tendencia_con_ventana_de_un_dia_se_omite():
    report = _report()
    report["desde"] = report["hasta"] = "2026-09-30"
    assert "Sin datos." in _text(am.section_trend(report, 10))


def test_horas_punta_ordena_y_separa_mensajes_de_interacciones():
    text = _text(am.section_peak_hours(_report(), 10))
    assert "Mensajes: 21h (9), 20h (5), 22h (1)" in text
    assert "Comandos y botones: 01h (4)" in text


def test_retencion_cuenta_nuevos_y_los_que_vuelven():
    text = _text(am.section_retention(_report(), 10))
    # moneda: u1..u4 (4 usuarios), todos nuevos; vuelven u1 (20) y u2 (15).
    assert "| comando moneda | 4 | 4 | 2 | 50.0 % |" in text
    # CoinView solo tiene 2 usuarios: queda fuera por debajo del mínimo.
    assert "vista CoinView" not in text
    assert "semana 1 | 4" in text


def test_retencion_en_ventana_corta_lo_dice_y_salta_la_tabla():
    report = _report()
    report["desde"], report["hasta"] = "2026-09-01", "2026-09-14"
    text = _text(am.section_retention(report, 10))
    assert "hacen falta al menos 21" in text
    assert "Usuarios activos" not in text


def test_canales_ordena_mensajes_y_voz():
    text = _text(am.section_channels(_report(), 10))
    assert "| #chat | 500 | 5 |" in text and "| #sala | 90 | 2 |" in text


def test_sistema_marca_memoria_alta_y_bucle_lento():
    text = _text(am.section_system(_report(), 10, 2048))
    day29 = next(line for line in text.splitlines() if line.startswith("| 2026-09-29"))
    day30 = next(line for line in text.splitlines() if line.startswith("| 2026-09-30"))
    assert "⚠️ memoria" in day29 and "⚠️ bucle" in day29
    assert "⚠️" not in day30


def test_sistema_respeta_el_limite_indicado():
    text = _text(am.section_system(_report(), 10, 8000))
    assert "⚠️ memoria" not in text


def test_dibujos_y_log_salen_ordenados():
    assert "| coin | 12 | 1200 | 2500 |" in _text(am.section_render(_report(), 10))
    assert "WARNING:bot.cogs.x | 7" in _text(am.section_log(_report(), 10))


def test_casino_avisa_si_la_casa_pierde_o_no_hay_jugadas():
    lines = am.section_casino(_report(), 10)
    by_game = {
        line.split("|")[1].strip(): line
        for line in lines
        if line.startswith("| ") and "---" not in line
    }
    assert "la casa pierde" in by_game["moneda"]
    assert "sin jugadas" in by_game["dados"]
    assert "⚠️" not in by_game["ruleta"]


def test_economia_muestra_cifras_cuentas_y_motivos_por_movimiento():
    text = _text(am.section_economy(_report(), 1))
    assert "Masa monetaria: 5000" in text and "Gini: 0.432" in text
    assert "35.0 %" in text and "estado 777" in text
    assert "| casino | 10 | 50 | 70 |" in text
    assert "sueldo" not in text  # con --top 1 solo entra el de más movimiento


def test_main_lee_el_archivo_y_respeta_top(tmp_path, capsys):
    path = tmp_path / "informe.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    assert am.main([str(path), "--top", "1", "--limite-mb", "1000"]) == 0
    out = capsys.readouterr().out
    assert "## Economía" in out and "Límite de memoria: 1000 MB" in out


def test_main_con_archivo_inexistente_o_roto_devuelve_error(tmp_path, capsys):
    assert am.main([str(tmp_path / "no-existe.json")]) == 1
    broken = tmp_path / "roto.json"
    broken.write_text("{", encoding="utf-8")
    assert am.main([str(broken)]) == 1
    listing = tmp_path / "lista.json"
    listing.write_text("[]", encoding="utf-8")
    assert am.main([str(listing)]) == 1
    assert "No se pudo leer" in capsys.readouterr().err


@pytest.mark.parametrize("section", [name for name in dir(am) if name.startswith("section_")])
def test_cada_seccion_acepta_un_informe_vacio(section):
    func = getattr(am, section)
    lines = func({}, 5)
    assert lines[0].startswith("## ")
