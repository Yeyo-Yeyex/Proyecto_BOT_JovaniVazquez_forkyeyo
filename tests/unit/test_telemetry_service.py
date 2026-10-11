"""Pruebas del servicio de telemetría: contadores, latencias, volcado e informe.

El servicio no toca Discord ni SQLite directamente; el repositorio se sustituye
por dobles y el reloj se inyecta para que nada dependa de la hora real.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from unittest.mock import AsyncMock

from bot.services.levels import TIMEZONE
from bot.services.telemetry import (
    MAX_NAME,
    MAX_PENDING,
    PENDING_SECONDS,
    Batch,
    Counter,
    CounterKey,
    ReportData,
    SystemSample,
    TelemetryService,
    build_report,
    gini,
    local_moment,
)

GUILD = 1
# Martes 6 de octubre de 2026, 21:30 en Canarias.
NOW = datetime(2026, 10, 6, 21, 30, tzinfo=TIMEZONE).timestamp()


class FakeClock:
    """Reloj controlable."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeRepository:
    """Repositorio de mentira que guarda los lotes que recibe."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.batches: list[Batch] = []
        self.pruned_at: float | None = None
        self.report_args: dict | None = None
        self.data = ReportData()

    async def write(self, batch: Batch) -> None:
        if self.fail:
            raise RuntimeError("disco lleno")
        self.batches.append(batch)

    async def prune(self, *, now: float) -> None:
        self.pruned_at = now

    async def report_data(self, **kwargs) -> ReportData:
        self.report_args = kwargs
        return self.data


def only_counter(service: TelemetryService) -> Counter:
    batch = service.drain()
    assert len(batch.counters) == 1
    return next(iter(batch.counters.values()))


def test_local_moment_usa_la_hora_canaria() -> None:
    assert local_moment(NOW) == ("2026-10-06", 21)
    # Tres horas después de las 21:30 ya es el día siguiente, a las 00:30.
    assert local_moment(NOW + 3 * 3600) == ("2026-10-07", 0)


def test_cubos_de_latencia_en_sus_bordes() -> None:
    service = TelemetryService(None, clock=FakeClock())
    for ms in (499, 500, 999, 1000, 2999, 3000):
        service.count("slash", "x", guild_id=GUILD, ms=ms)
    counter = only_counter(service)
    assert counter.ms_lt500 == 1
    assert counter.ms_lt1000 == 2
    assert counter.ms_lt3000 == 2
    assert counter.ms_ge3000 == 1
    assert counter.ms_count == 6
    assert counter.ms_max == 3000
    assert counter.ms_total == 499 + 500 + 999 + 1000 + 2999 + 3000


def test_sin_latencia_no_toca_los_cubos_y_los_errores_se_cuentan() -> None:
    service = TelemetryService(None, clock=FakeClock())
    service.count("button", "b", guild_id=GUILD, events=3)
    service.count("button", "b", guild_id=GUILD, error=True)
    counter = only_counter(service)
    assert (counter.events, counter.errors, counter.ms_count) == (4, 1, 0)


def test_counter_merge_suma_y_se_queda_con_el_maximo() -> None:
    a, b = Counter(), Counter()
    a.add(events=1, error=False, ms=100)
    b.add(events=2, error=True, ms=2000)
    a.merge(b)
    assert (a.events, a.errors, a.ms_count, a.ms_max, a.ms_lt500, a.ms_lt3000) == (
        3,
        1,
        2,
        2000,
        1,
        1,
    )


def test_guild_none_se_guarda_como_cero_y_el_nombre_se_corta() -> None:
    service = TelemetryService(None, clock=FakeClock())
    service.count("message", "x" * (MAX_NAME + 50), guild_id=None, user_id=7)
    batch = service.drain()
    (key,) = batch.counters
    assert key.guild_id == 0
    assert key.name == "x" * MAX_NAME
    (user,) = batch.users
    assert (user.user_id, user.name, user.kind) == (7, key.name, "message")


def test_count_con_at_usa_ese_momento_y_no_el_reloj() -> None:
    service = TelemetryService(None, clock=FakeClock())
    service.count("slash", "x", guild_id=GUILD, at=NOW + 3 * 3600)
    (key,) = service.drain().counters
    assert key == CounterKey("2026-10-07", 0, GUILD, "slash", "x")


def test_sin_user_id_no_se_apunta_usuario() -> None:
    service = TelemetryService(None, clock=FakeClock())
    service.count("slash", "x", guild_id=GUILD)
    assert service.drain().users == set()


def test_answered_no_pisa_la_primera_y_take_answer_la_borra() -> None:
    service = TelemetryService(None, clock=FakeClock())
    service.answered(1, 120.0)
    service.answered(1, 999.0)
    assert service.take_answer(1) == 120.0
    assert service.take_answer(1) is None


def test_answered_tira_las_mas_antiguas_al_pasarse() -> None:
    service = TelemetryService(None, clock=FakeClock())
    for interaction_id in range(MAX_PENDING + 3):
        service.answered(interaction_id, 1.0)
    assert service.take_answer(0) is None
    assert service.take_answer(2) is None
    assert service.take_answer(3) == 1.0
    assert service.take_answer(MAX_PENDING + 2) == 1.0


def test_answered_caduca_pasado_el_plazo_al_hacer_drain() -> None:
    clock = FakeClock()
    service = TelemetryService(None, clock=clock)
    service.answered(1, 10.0)
    clock.now += PENDING_SECONDS - 1
    service.answered(2, 20.0)
    clock.now += 2  # la 1 lleva PENDING_SECONDS + 1; la 2 solo 2.
    service.drain()
    assert service.take_answer(1) is None
    assert service.take_answer(2) == 20.0


def test_drain_vacia_los_buffers() -> None:
    service = TelemetryService(None, clock=FakeClock())
    service.count("slash", "x", guild_id=GUILD, user_id=1)
    service.sample(SystemSample(NOW, "2026-10-06", None, 100.0, 1.0, None, 0, 0))
    first = service.drain()
    assert not first.is_empty()
    assert len(first.samples) == 1
    assert service.drain().is_empty()


def test_flush_sin_repositorio_no_hace_nada_y_vacia() -> None:
    service = TelemetryService(None, clock=FakeClock())
    service.count("slash", "x", guild_id=GUILD)
    asyncio.run(service.flush())
    assert service.drain().is_empty()


def test_flush_escribe_el_lote_y_no_escribe_si_esta_vacio() -> None:
    repository = FakeRepository()
    service = TelemetryService(repository, clock=FakeClock())  # type: ignore[arg-type]

    async def scenario() -> None:
        await service.flush()
        service.count("slash", "x", guild_id=GUILD)
        await service.flush()
        await service.flush()

    asyncio.run(scenario())
    assert len(repository.batches) == 1


def test_flush_con_write_que_falla_no_lanza_y_lo_registra(caplog) -> None:
    repository = FakeRepository(fail=True)
    service = TelemetryService(repository, clock=FakeClock())  # type: ignore[arg-type]
    service.count("slash", "x", guild_id=GUILD)
    service.count("slash", "y", guild_id=GUILD)
    with caplog.at_level(logging.ERROR, logger="bot.services.telemetry"):
        asyncio.run(service.flush())
    assert "se pierden 2 contadores" in caplog.text
    assert service.drain().is_empty()


def test_prune_pasa_la_hora_del_reloj_al_repositorio() -> None:
    repository = FakeRepository()
    service = TelemetryService(repository, clock=FakeClock())  # type: ignore[arg-type]
    asyncio.run(service.prune())
    assert repository.pruned_at == NOW
    asyncio.run(TelemetryService(None).prune())  # sin repositorio no falla


def test_report_vuelca_antes_y_pide_desde_las_cero_horas_locales() -> None:
    repository = FakeRepository()
    service = TelemetryService(repository, clock=FakeClock())  # type: ignore[arg-type]
    service.count("slash", "x", guild_id=GUILD)
    report = asyncio.run(
        service.report(guild_id=GUILD, days=7, commands=["x", "y"], channel_names={})
    )
    assert len(repository.batches) == 1
    assert repository.report_args == {
        "guild_id": GUILD,
        "since_day": "2026-09-30",
        "since_ts": datetime(2026, 9, 30, 0, 0, tzinfo=TIMEZONE).timestamp(),
    }
    assert (report["desde"], report["hasta"], report["dias"]) == ("2026-09-30", "2026-10-06", 7)


def test_report_sin_repositorio_devuelve_informe_vacio() -> None:
    report = asyncio.run(
        TelemetryService(None, clock=FakeClock()).report(
            guild_id=GUILD, days=1, commands=["a"], channel_names={}
        )
    )
    assert report["uso"] == []
    assert report["comandos_sin_uso"] == ["a"]
    json.dumps(report)


def test_gini_de_casos_conocidos() -> None:
    assert gini([]) == 0
    assert gini([0, 0]) == 0
    assert gini([1, 1]) == 0
    assert gini([0, 10]) == 0.5


def usage_row(kind: str, name: str, events: int, **extra) -> dict:
    row = {
        "kind": kind,
        "name": name,
        "events": events,
        "errors": 0,
        "ms_count": 0,
        "ms_total": 0,
        "ms_max": 0,
        "lt500": 0,
        "lt1000": 0,
        "lt3000": 0,
        "ge3000": 0,
    }
    row.update(extra)
    return row


def sample_data() -> ReportData:
    return ReportData(
        usage=[
            usage_row("slash", "poner", 5, ms_count=2, ms_total=300, ms_max=200, lt500=2),
            usage_row("slash", "tiempo", 9),
            usage_row("text", "ayuda", 1),
            usage_row("message", "555", 40),
            usage_row("message", "777", 3),
        ],
        daily=[("2026-10-05", "slash", 6, 0), ("2026-10-06", "slash", 8, 1)],
        hourly=[(21, "slash", 8), (3, "slash", 6)],
        users=[
            ("2026-10-06", "slash", "poner", 4242),
            ("2026-10-05", "slash", "poner", 99),
            ("2026-10-06", "slash", "poner", 99),
            ("2026-10-06", "message", "555", 99),
        ],
        system=[
            {
                "day": "2026-10-06",
                "memory_max": 512.04,
                "memory_avg": 400.0,
                "rss_max": 300.0,
                "lag_max": 12.0,
                "gateway_avg": None,
                "chromium_max": 1,
                "node_max": 2,
            }
        ],
        casino=[
            {"game": "ruleta", "plays": 10, "free": 1, "players": 3, "wagered": 1000, "paid": 900},
            {"game": "dados", "plays": 0, "free": 2, "players": 1, "wagered": 0, "paid": 0},
        ],
        balances=[0, 0, 100, 300],
        system_accounts={0: 5000, -100: 40, -200: 7, -300: 1, -999: 2},
        ledger=[
            {"reason": "a", "moves": 1, "inflow": 10, "outflow": 0},
            {"reason": "b", "moves": 2, "inflow": 5, "outflow": 50},
        ],
    )


def test_informe_es_json_y_no_contiene_user_ids() -> None:
    report = build_report(
        sample_data(), generated_at=NOW, days=7, commands=[], channel_names={}, guild_id=GUILD
    )
    text = json.dumps(report)
    assert "4242" not in text
    assert '"99"' not in text and ": 99" not in text
    assert report["servidor"] == GUILD
    assert report["version"] == 1
    assert report["generado"] == "2026-10-06T21:30:00+01:00"


def test_informe_seudonimiza_por_primer_dia_visto_y_luego_user_id() -> None:
    activity = build_report(sample_data(), generated_at=NOW, days=7, commands=[], channel_names={})[
        "actividad"
    ]
    # El 99 aparece el día 5 (desfase 5 desde el 30/09); el 4242 solo el 6.
    assert list(activity) == ["u1", "u2"]
    assert activity["u1"] == {"message:555": [6], "slash:poner": [5, 6]}
    assert activity["u2"] == {"slash:poner": [6]}


def test_informe_comandos_sin_uso_cuenta_slash_y_texto() -> None:
    report = build_report(
        sample_data(),
        generated_at=NOW,
        days=7,
        commands=["poner", "ayuda", "tiempo", "nunca", "jamas"],
        channel_names={},
    )
    assert report["comandos_sin_uso"] == ["jamas", "nunca"]


def test_informe_traduce_canales_y_deja_el_id_si_no_se_conoce() -> None:
    report = build_report(
        sample_data(), generated_at=NOW, days=7, commands=[], channel_names={555: "general"}
    )
    names = [row["nombre"] for row in report["uso"] if row["tipo"] == "message"]
    assert names == ["#general", "777"]
    assert "message:#general" in report["actividad"]["u1"]


def test_informe_uso_ordenado_y_con_medias_y_usuarios() -> None:
    uso = build_report(sample_data(), generated_at=NOW, days=7, commands=[], channel_names={})[
        "uso"
    ]
    assert [(r["tipo"], r["nombre"]) for r in uso if r["tipo"] == "slash"] == [
        ("slash", "tiempo"),
        ("slash", "poner"),
    ]
    poner = next(r for r in uso if r["nombre"] == "poner")
    assert poner["ms_medio"] == 150.0
    assert poner["usuarios"] == 2
    assert poner["ms"]["<500"] == 2
    assert next(r for r in uso if r["nombre"] == "tiempo")["ms_medio"] is None


def test_informe_por_dia_por_hora_sistema_y_casino() -> None:
    report = build_report(sample_data(), generated_at=NOW, days=7, commands=[], channel_names={})
    assert report["por_dia"]["2026-10-06"]["slash"] == {"eventos": 8, "errores": 1, "usuarios": 2}
    assert report["por_dia"]["2026-10-06"]["message"]["usuarios"] == 1
    assert report["por_hora"]["slash"][21] == 8
    assert report["por_hora"]["slash"][3] == 6
    assert len(report["por_hora"]["slash"]) == 24
    assert report["sistema"]["2026-10-06"]["memoria_mb_max"] == 512.0
    assert report["sistema"]["2026-10-06"]["gateway_ms_media"] is None
    juegos = {row["juego"]: row for row in report["casino"]}
    assert juegos["ruleta"]["rtp"] == 0.9
    assert juegos["dados"]["rtp"] is None


def test_informe_economia() -> None:
    economia = build_report(sample_data(), generated_at=NOW, days=7, commands=[], channel_names={})[
        "economia"
    ]
    assert economia["masa_monetaria"] == 400
    assert economia["miembros_con_saldo"] == 2
    # 4 miembros: gini = (sum (2i-n-1) x) / (n * total) = (-3*0 -1*0 +1*100 +3*300)/(4*400).
    assert economia["gini"] == 0.625
    # ceil(2 * 0,1) = 1 miembro: 300 de 400.
    assert economia["top10_pct"] == 0.75
    assert economia["cuentas_sistema"]["estado"] == 5000
    assert economia["cuentas_sistema"]["bote_tragaperras"] == 40
    assert economia["cuentas_sistema"]["tienda"] == 7
    assert economia["cuentas_sistema"]["hong_kong"] == 1
    assert economia["cuentas_sistema"]["cuenta_-999"] == 2
    assert [m["motivo"] for m in economia["motivos"]] == ["b", "a"]


def test_informe_vacio_no_divide_por_cero() -> None:
    report = build_report(ReportData(), generated_at=NOW, days=1, commands=[], channel_names={})
    assert report["economia"]["gini"] == 0
    assert report["economia"]["top10_pct"] == 0
    assert report["desde"] == report["hasta"] == "2026-10-06"
    json.dumps(report)


def test_flush_concurrente_no_pierde_ni_duplica() -> None:
    repository = FakeRepository()
    repository.write = AsyncMock()  # type: ignore[method-assign]
    service = TelemetryService(repository, clock=FakeClock())  # type: ignore[arg-type]
    service.count("slash", "x", guild_id=GUILD)

    async def scenario() -> None:
        await asyncio.gather(service.flush(), service.flush())

    asyncio.run(scenario())
    assert repository.write.await_count == 1
