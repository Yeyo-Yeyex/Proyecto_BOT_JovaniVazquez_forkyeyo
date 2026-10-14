"""Resume en texto el informe JSON de uso del bot, pensado para que un agente decida mejoras.

Uso, desde la raíz del repo:

    python docs/analisis_metricas.py docs/metricas/informe-2026-10-11.json
    python docs/analisis_metricas.py informe.json --top 5 --limite-mb 2048

El informe lo genera el comando `informe` del bot (`build_report` en
`bot/services/telemetry.py`). Entradas: la ruta del JSON, `--top N` (filas de cada
ranking, 10 por defecto) y `--limite-mb` (memoria del contenedor, 2048 por defecto,
para marcar los días cerca del límite). Salida: Markdown sencillo por pantalla, con
una sección `## ...` por tema. No escribe archivos y solo usa la biblioteca estándar:
no importa nada de `bot.*`, así que funciona con el JSON solo.

Cualquier sección cuyo dato falte o esté vacío se omite o dice «sin datos»; el
script no debe reventar con un informe casi vacío.

El signo ⚠️ marca lo que merece mirarse: latencias de 3 s o más, memoria por
encima del 85 % del límite, bucle de eventos con más de 250 ms de retraso, RTP del
casino por encima de 1 o juegos sin jugadas.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

Report = dict[str, Any]

DEFAULT_TOP = 10
DEFAULT_LIMIT_MB = 2048.0
MEMORY_WARN = 0.85
LAG_WARN_MS = 250.0
NEW_USER_DAYS = 7
RETURN_FROM_DAY = 14
MIN_RETENTION_WINDOW = 21
MIN_RETENTION_USERS = 3
TREND_KINDS = ("message", "slash", "button", "voice")
FAIL_KINDS = ("button", "modal", "slash", "text")
WARN = "⚠️"


# --------------------------------------------------------------------------- utilidades


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _num(value: Any, default: float = 0) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _entries(report: Report, *kinds: str) -> list[dict[str, Any]]:
    """Entradas de `uso` de los tipos dados (todas si no se indica ninguno)."""
    return [
        e
        for e in _list(report.get("uso"))
        if isinstance(e, dict) and (not kinds or e.get("tipo") in kinds)
    ]


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "-"


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def _window(report: Report) -> tuple[date, date] | None:
    try:
        return date.fromisoformat(report["desde"]), date.fromisoformat(report["hasta"])
    except (KeyError, TypeError, ValueError):
        return None


def _window_days(report: Report) -> int:
    """Días de la ventana: por las fechas si son válidas, si no por `dias`."""
    window = _window(report)
    if window:
        return (window[1] - window[0]).days + 1
    return int(_num(report.get("dias")))


def _feature(label: str) -> str | None:
    """Funcionalidad de una etiqueta «tipo:nombre»: el comando (slash+text) o la vista del botón."""
    kind, _, name = label.partition(":")
    if kind in ("slash", "text"):
        return f"comando {name}"
    if kind == "button":
        return f"vista {name.split('.', 1)[0]}"
    return None


def _feature_users(report: Report) -> dict[str, set[str]]:
    """Usuarios distintos por funcionalidad, a partir de `actividad`."""
    found: dict[str, set[str]] = {}
    for who, labels in _dict(report.get("actividad")).items():
        for label in _dict(labels):
            feature = _feature(label)
            if feature:
                found.setdefault(feature, set()).add(who)
    return found


def _section(title: str, body: list[str]) -> list[str]:
    return [f"## {title}", "", *(body or ["Sin datos."]), ""]


# --------------------------------------------------------------------------- secciones


def section_summary(report: Report, top: int) -> list[str]:
    """Ventana del informe, eventos por tipo y usuarios distintos totales."""
    lines = [
        f"- Ventana: {report.get('desde', '?')} a {report.get('hasta', '?')} "
        f"({report.get('dias', '?')} días)",
    ]
    if report.get("generado"):
        lines.append(f"- Generado: {report['generado']}")
    per_kind: dict[str, int] = {}
    for entry in _entries(report):
        kind = str(entry.get("tipo"))
        per_kind[kind] = per_kind.get(kind, 0) + int(_num(entry.get("eventos")))
    if per_kind:
        lines.append(
            "- Eventos por tipo: " + ", ".join(f"{k} {v}" for k, v in sorted(per_kind.items()))
        )
    else:
        lines.append("- Eventos por tipo: sin datos")
    lines.append(f"- Usuarios distintos con actividad: {len(_dict(report.get('actividad')))}")
    return _section("Resumen", lines)


def section_slow(report: Report, top: int) -> list[str]:
    """Entradas con latencia medida, de más a menos lentas por proporción de ≥3 s y luego ≥1 s."""
    rows = []
    for entry in _entries(report):
        if entry.get("ms_medio") is None:
            continue
        ms = _dict(entry.get("ms"))
        slow = int(_num(ms.get("<3000")))
        very_slow = int(_num(ms.get(">=3000")))
        measured = sum(int(_num(ms.get(k))) for k in ("<500", "<1000", "<3000", ">=3000"))
        if not measured:
            continue
        rows.append((very_slow / measured, slow / measured, measured, very_slow, slow, entry))
    rows.sort(key=lambda r: (-r[0], -r[1], -r[2], str(r[5].get("nombre"))))
    body: list[str] = []
    if rows:
        table = [
            (
                WARN if very_slow else "",
                f"{e.get('tipo')}:{e.get('nombre')}",
                measured,
                _pct(slow + very_slow, measured),
                _pct(very_slow, measured),
                f"{_num(e.get('ms_medio')):.0f}",
                f"{_num(e.get('ms_max')):.0f}",
            )
            for _a, _b, measured, very_slow, slow, e in rows[:top]
        ]
        body = ["Latencia hasta la primera respuesta o duración medida.", ""]
        body += _table(["", "Nombre", "Medidos", "≥1 s", "≥3 s", "ms medio", "ms máx"], table)
        if len(rows) > top:
            body += ["", f"(+{len(rows) - top} más con medición; usa --top para ver más)"]
    return _section("Lentos", body)


def section_failures(report: Report, top: int) -> list[str]:
    """Botones, formularios y comandos con errores: una interacción sin respuesta o fallida."""
    rows = sorted(
        (e for e in _entries(report, *FAIL_KINDS) if _num(e.get("errores")) > 0),
        key=lambda e: (-_num(e.get("errores")), str(e.get("nombre"))),
    )
    if not rows:
        return _section("Fallos", ["Sin errores registrados."])
    body = [
        "En `button`, `modal` y `slash` un error es una interacción sin responder a Discord"
        " o fallida.",
        "",
    ]
    body += _table(
        ["Nombre", "Errores", "Eventos", "Tasa"],
        [
            (
                f"{e.get('tipo')}:{e.get('nombre')}",
                int(_num(e.get("errores"))),
                int(_num(e.get("eventos"))),
                _pct(_num(e.get("errores")), _num(e.get("eventos"))),
            )
            for e in rows[:top]
        ],
    )
    return _section("Fallos", body)


def _usage_groups(report: Report) -> dict[str, tuple[int, int]]:
    """Eventos y usuarios por funcionalidad (comando o vista de botones)."""
    distinct = _feature_users(report)
    groups: dict[str, list[int]] = {}
    for entry in _entries(report, "slash", "text", "button"):
        feature = _feature(f"{entry.get('tipo')}:{entry.get('nombre')}")
        if feature is None:
            continue
        slot = groups.setdefault(feature, [0, 0])
        slot[0] += int(_num(entry.get("eventos")))
        slot[1] += int(_num(entry.get("usuarios")))  # solo se usa si no hay `actividad`
    return {
        f: (events, len(distinct[f]) if f in distinct else users)
        for f, (events, users) in groups.items()
    }


def section_usage(report: Report, top: int) -> list[str]:
    """Comandos (slash y text sumados) y vistas de botones más y menos usados."""
    groups = _usage_groups(report)
    body: list[str] = []
    for prefix, title in (("comando ", "Comandos"), ("vista ", "Botones por vista")):
        items = sorted(
            ((f[len(prefix) :], ev, us) for f, (ev, us) in groups.items() if f.startswith(prefix)),
            key=lambda i: (-i[1], i[0]),
        )
        if not items:
            continue
        head = items[:top]
        tail = [i for i in sorted(items, key=lambda i: (i[1], i[0]))[:top] if i not in head]
        if body:
            body.append("")
        body += [f"### {title}: más usados", ""]
        body += _table(["Nombre", "Eventos", "Usuarios"], head)
        if tail:
            body += ["", f"### {title}: menos usados", ""]
            body += _table(["Nombre", "Eventos", "Usuarios"], tail)
    return _section("Lo más y lo menos usado", body)


def section_unused(report: Report, top: int) -> list[str]:
    """Comandos del bot que nadie usó en la ventana."""
    names = [str(n) for n in _list(report.get("comandos_sin_uso"))]
    if not names:
        return _section("Comandos sin uso", ["Ninguno: todos se han usado (o no hay datos)."])
    return _section("Comandos sin uso", [f"{len(names)}: " + ", ".join(names)])


def _split_halves(report: Report) -> tuple[list[str], list[str]] | None:
    window = _window(report)
    if window is None:
        return None
    start, end = window
    total = (end - start).days + 1
    if total < 2:
        return None
    days = [(start + timedelta(days=i)).isoformat() for i in range(total)]
    half = total // 2
    return days[:half], days[total - half :]


def _change(before: float, after: float) -> str:
    if before == 0:
        return "nuevo" if after else "-"
    return f"{100 * (after - before) / before:+.0f} %"


def section_trend(report: Report, top: int) -> list[str]:
    """Eventos y usuarios medios por día: primera mitad de la ventana frente a la segunda."""
    halves = _split_halves(report)
    per_day = _dict(report.get("por_dia"))
    if halves is None or not per_day:
        return _section("Tendencia", [])
    first, second = halves
    rows = []
    for kind in TREND_KINDS:
        if not any(kind in _dict(per_day.get(d)) for d in per_day):
            continue
        cells: list[float] = []
        for days in (first, second):
            for field in ("eventos", "usuarios"):
                total = sum(_num(_dict(_dict(per_day.get(d)).get(kind)).get(field)) for d in days)
                cells.append(total / len(days))
        rows.append(
            (
                kind,
                f"{cells[0]:.1f}",
                f"{cells[2]:.1f}",
                _change(cells[0], cells[2]),
                f"{cells[1]:.1f}",
                f"{cells[3]:.1f}",
                _change(cells[1], cells[3]),
            )
        )
    if not rows:
        return _section("Tendencia", [])
    body = [
        f"Primera mitad: {first[0]} a {first[-1]}; segunda: {second[0]} a {second[-1]}."
        " Medias por día.",
        "",
    ]
    body += _table(
        ["Tipo", "Eventos 1ª", "Eventos 2ª", "Cambio", "Usuarios 1ª", "Usuarios 2ª", "Cambio"],
        rows,
    )
    return _section("Tendencia", body)


def section_peak_hours(report: Report, top: int) -> list[str]:
    """Las 3 horas con más mensajes y las 3 con más comandos y clics."""
    hourly = _dict(report.get("por_hora"))
    if not hourly:
        return _section("Horas punta", [])

    def series(kind: str) -> list[float]:
        raw = _list(hourly.get(kind))
        return [_num(raw[h]) if h < len(raw) else 0 for h in range(24)]

    def best(values: list[float]) -> str:
        ranked = [
            (h, v) for h, v in sorted(enumerate(values), key=lambda i: (-i[1], i[0])) if v > 0
        ]
        return ", ".join(f"{h:02d}h ({int(v)})" for h, v in ranked[:3]) or "sin datos"

    interactions = [
        a + b + c for a, b, c in zip(series("slash"), series("button"), series("text"), strict=True)
    ]
    return _section(
        "Horas punta",
        [
            f"- Mensajes: {best(series('message'))}",
            f"- Comandos y botones: {best(interactions)}",
            "- Horas locales de Canarias.",
        ],
    )


def section_retention(report: Report, top: int) -> list[str]:
    """Retención por funcionalidad y usuarios activos por semana."""
    title = "Retención por funcionalidad"
    activity = _dict(report.get("actividad"))
    days = _window_days(report)
    if not activity:
        return _section(title, [])
    if days < MIN_RETENTION_WINDOW:
        return _section(
            title,
            [
                f"Ventana de {days} días: hacen falta al menos {MIN_RETENTION_WINDOW}"
                " para medir la retención."
            ],
        )
    offsets: dict[str, dict[str, set[int]]] = {}
    for who, labels in activity.items():
        for label, shifts in _dict(labels).items():
            feature = _feature(label)
            if feature:
                bucket = offsets.setdefault(feature, {}).setdefault(who, set())
                bucket.update(int(s) for s in _list(shifts) if isinstance(s, int | float))
    rows = []
    for feature, users in offsets.items():
        if len(users) < MIN_RETENTION_USERS:
            continue
        new = [u for u, s in users.items() if s and min(s) < NEW_USER_DAYS]
        back = [u for u in new if any(d >= RETURN_FROM_DAY for d in users[u])]
        rows.append((feature, len(users), len(new), len(back), _pct(len(back), len(new))))
    rows.sort(key=lambda r: (-r[1], r[0]))
    body = [
        f"Nuevo: primer uso en los primeros {NEW_USER_DAYS} días. Vuelve: lo usa de nuevo a"
        f" partir del día {RETURN_FROM_DAY}.",
        "",
    ]
    if rows:
        body += _table(["Funcionalidad", "Usuarios", "Nuevos", "Vuelven", "Retención"], rows[:top])
    else:
        body.append(f"Ninguna funcionalidad llega a {MIN_RETENTION_USERS} usuarios.")
    weeks = math.ceil(days / 7)
    active = [0] * weeks
    for labels in activity.values():
        seen = {
            int(s) // 7
            for shifts in _dict(labels).values()
            for s in _list(shifts)
            if isinstance(s, int | float) and 0 <= s < weeks * 7
        }
        for week in seen:
            active[week] += 1
    body += ["", "Usuarios activos (cualquier cosa) por semana:", ""]
    body += _table(["Semana", "Usuarios"], [(f"semana {i + 1}", n) for i, n in enumerate(active)])
    return _section(title, body)


def section_channels(report: Report, top: int) -> list[str]:
    """Canales con más mensajes y con más minutos de voz."""
    body: list[str] = []
    for kind, title, unit in (("message", "Mensajes", "Mensajes"), ("voice", "Voz", "Minutos")):
        rows = sorted(
            _entries(report, kind), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
        )
        if not rows:
            continue
        if body:
            body.append("")
        body += [f"### {title}", ""]
        body += _table(
            ["Canal", unit, "Usuarios"],
            [
                (e.get("nombre"), int(_num(e.get("eventos"))), int(_num(e.get("usuarios"))))
                for e in rows[:top]
            ],
        )
    return _section("Canales", body)


def section_system(report: Report, top: int, limit_mb: float = DEFAULT_LIMIT_MB) -> list[str]:
    """Memoria, retraso del bucle, procesos de dibujo y latencia con Discord por día."""
    rows = []
    for day, raw in sorted(_dict(report.get("sistema")).items()):
        row = _dict(raw)
        memory = row.get("memoria_mb_max")
        lag = row.get("lag_ms_max")
        gateway = row.get("gateway_ms_media")
        flags = []
        if isinstance(memory, int | float) and limit_mb > 0 and memory > MEMORY_WARN * limit_mb:
            flags.append(f"{WARN} memoria")
        if isinstance(lag, int | float) and lag > LAG_WARN_MS:
            flags.append(f"{WARN} bucle")
        rows.append(
            (
                day,
                "-" if memory is None else f"{memory:.0f} ({_pct(memory, limit_mb)})",
                "-" if lag is None else f"{lag:.0f}",
                row.get("chromium_max", "-"),
                row.get("node_max", "-"),
                "-" if gateway is None else f"{gateway:.0f}",
                " ".join(flags),
            )
        )
    body: list[str] = []
    if rows:
        body = [
            f"Límite de memoria: {limit_mb:.0f} MB; aviso por encima del {MEMORY_WARN:.0%}.",
            "",
        ]
        body += _table(
            [
                "Día",
                "Memoria máx MB",
                "Retraso bucle ms",
                "Chromium",
                "Node",
                "Discord ms",
                "Avisos",
            ],
            rows,
        )
    return _section("Sistema", body)


def section_render(report: Report, top: int) -> list[str]:
    """Tiempo medio y máximo de cada dibujo (escena)."""
    rows = sorted(
        _entries(report, "render"),
        key=lambda e: (-_num(e.get("ms_medio")), str(e.get("nombre"))),
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["Escena", "Dibujos", "ms medio", "ms máx"],
            [
                (
                    e.get("nombre"),
                    int(_num(e.get("eventos"))),
                    "-" if e.get("ms_medio") is None else f"{e['ms_medio']:.0f}",
                    f"{_num(e.get('ms_max')):.0f}",
                )
                for e in rows[:top]
            ],
        )
    return _section("Dibujos", body)


def section_upload(report: Report, top: int) -> list[str]:
    """Tiempo medio y máximo de subir cada archivo a Discord, por nombre y tamaño."""
    rows = sorted(
        _entries(report, "upload"),
        key=lambda e: (-_num(e.get("ms_medio")), str(e.get("nombre"))),
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["Archivo", "Subidas", "Fallos", "ms medio", "ms máx"],
            [
                (
                    e.get("nombre"),
                    int(_num(e.get("eventos"))),
                    int(_num(e.get("errores"))),
                    "-" if e.get("ms_medio") is None else f"{e['ms_medio']:.0f}",
                    f"{_num(e.get('ms_max')):.0f}",
                )
                for e in rows[:top]
            ],
        )
    return _section("Subidas a Discord", body)


def section_log(report: Report, top: int) -> list[str]:
    """Avisos y errores registrados por el bot, de más a menos frecuentes."""
    rows = sorted(
        _entries(report, "log"), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["Registro", "Eventos"],
            [(e.get("nombre"), int(_num(e.get("eventos")))) for e in rows[:top]],
        )
    return _section("Log", body)


def section_casino(report: Report, top: int) -> list[str]:
    """Jugadas, jugadores, apostado, pagado y RTP por juego."""
    rows = []
    for raw in _list(report.get("casino")):
        game = _dict(raw)
        plays = int(_num(game.get("jugadas")))
        rtp = game.get("rtp")
        flags = []
        if plays == 0:
            flags.append(f"{WARN} sin jugadas")
        if isinstance(rtp, int | float) and rtp > 1:
            flags.append(f"{WARN} la casa pierde")
        rows.append(
            (
                game.get("juego"),
                plays,
                int(_num(game.get("jugadores"))),
                int(_num(game.get("apostado"))),
                int(_num(game.get("pagado"))),
                "-" if rtp is None else f"{rtp:.1%}",
                " ".join(flags),
            )
        )
    body: list[str] = []
    if rows:
        body = _table(
            ["Juego", "Jugadas", "Jugadores", "Apostado", "Pagado", "RTP", "Avisos"], rows
        )
    return _section("Casino", body)


def section_economy(report: Report, top: int) -> list[str]:
    """Masa monetaria, desigualdad, cuentas del sistema y motivos con más movimiento."""
    eco = _dict(report.get("economia"))
    if not eco:
        return _section("Economía", [])
    body = [
        f"- Masa monetaria: {int(_num(eco.get('masa_monetaria')))}",
        f"- Miembros con saldo: {int(_num(eco.get('miembros_con_saldo')))}",
        f"- Gini: {_num(eco.get('gini')):.3f}",
        f"- Parte del 10 % más rico: {100 * _num(eco.get('top10_pct')):.1f} %",
    ]
    accounts = _dict(eco.get("cuentas_sistema"))
    if accounts:
        body.append("- Cuentas del sistema: " + ", ".join(f"{k} {v}" for k, v in accounts.items()))
    reasons = [r for r in _list(eco.get("motivos")) if isinstance(r, dict)]
    if reasons:
        body += ["", f"Motivos con más movimiento (top {top}):", ""]
        body += _table(
            ["Motivo", "Movimientos", "Entradas", "Salidas"],
            [
                (
                    r.get("motivo"),
                    int(_num(r.get("movimientos"))),
                    int(_num(r.get("entradas"))),
                    int(_num(r.get("salidas"))),
                )
                for r in reasons[:top]
            ],
        )
    return _section("Economía", body)


# --------------------------------------------------------------------------- principal


def build_text(
    report: Report, top: int = DEFAULT_TOP, limit_mb: float = DEFAULT_LIMIT_MB
) -> list[str]:
    """Une todas las secciones, en orden, en las líneas del resumen completo."""
    lines = ["# Análisis de métricas del bot", ""]
    for section in (
        section_summary,
        section_slow,
        section_failures,
        section_usage,
        section_unused,
        section_trend,
        section_peak_hours,
        section_retention,
        section_channels,
    ):
        lines += section(report, top)
    lines += section_system(report, top, limit_mb)
    for section in (
        section_render,
        section_upload,
        section_log,
        section_casino,
        section_economy,
    ):
        lines += section(report, top)
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """Lee el informe indicado y escribe el resumen por la salida estándar."""
    parser = argparse.ArgumentParser(description="Resume el informe JSON de uso del bot.")
    parser.add_argument("informe", help="ruta del JSON que genera el comando `informe`")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="filas por ranking")
    parser.add_argument(
        "--limite-mb", type=float, default=DEFAULT_LIMIT_MB, help="límite de memoria en MB"
    )
    args = parser.parse_args(argv)
    try:
        with open(args.informe, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"No se pudo leer el informe: {error}", file=sys.stderr)
        return 1
    if not isinstance(report, dict):
        print("El informe no es un objeto JSON.", file=sys.stderr)
        return 1
    print("\n".join(build_text(report, max(args.top, 1), args.limite_mb)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
