"""Pruebas de bot.services.babel: la cadena de traducciones y el parseo de Google."""

from __future__ import annotations

import random

import pytest

from bot.services.babel import (
    HOME_LANGUAGE,
    LANGUAGES,
    MAX_CONSECUTIVE_FAILURES,
    TOTAL_HOPS,
    ChannelRenameLimiter,
    RateLimitedError,
    TranslationError,
    keep_lines,
    parse_google_response,
    pick_route,
    readable_channel_name,
    run_chain,
    split_decoration,
)


class FakeTranslator:
    """Traductor que marca cada salto (`texto|ja`) y registra las llamadas."""

    def __init__(self, fail: dict[str, Exception] | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = fail or {}

    async def __call__(self, text: str, source: str, target: str) -> str:
        self.calls.append((text, source, target))
        if target in self.fail:
            raise self.fail[target]
        return f"{text}|{target}"


def test_pick_route_da_99_idiomas_distintos_sin_espanol() -> None:
    route = pick_route(random.Random(1))
    assert len(route) == TOTAL_HOPS - 1
    assert len(set(route)) == len(route)
    assert HOME_LANGUAGE not in route
    assert set(route) <= set(LANGUAGES)


@pytest.mark.asyncio
async def test_cadena_completa_encadena_idiomas_y_vuelve_al_espanol() -> None:
    translator = FakeTranslator()
    result = await run_chain("hola", ["ja", "zu"], translator)

    # Cada salto parte del idioma anterior, no del español.
    assert [(s, t) for _, s, t in translator.calls] == [("es", "ja"), ("ja", "zu"), ("zu", "es")]
    assert result.final == "hola|ja|zu|es"
    assert result.route == ("es", "ja", "zu", "es")
    assert result.hops == 3
    assert result.final_language == HOME_LANGUAGE
    assert not result.stopped_early


@pytest.mark.asyncio
async def test_un_idioma_que_falla_se_salta_y_la_cadena_sigue() -> None:
    translator = FakeTranslator(fail={"ja": TranslationError("400")})
    result = await run_chain("hola", ["ja", "zu"], translator)

    assert result.route == ("es", "zu", "es")
    assert ("hola", "es", "zu") in translator.calls
    assert not result.stopped_early


@pytest.mark.asyncio
async def test_limite_de_tasa_corta_la_cadena_e_intenta_volver_al_espanol() -> None:
    translator = FakeTranslator(fail={"zu": RateLimitedError("429")})
    result = await run_chain("hola", ["ja", "zu", "fr"], translator)

    targets = [t for _, _, t in translator.calls]
    assert "fr" not in targets  # no se insiste tras el 429
    assert result.route == ("es", "ja", "es")
    assert result.stopped_early


class FlakyTranslator(FakeTranslator):
    """Google limita la tasa las `limited` primeras veces que se pide un idioma."""

    def __init__(self, limited: int) -> None:
        super().__init__()
        self.limited = limited

    async def __call__(self, text: str, source: str, target: str) -> str:
        if target == "zu" and self.limited:
            self.limited -= 1
            self.calls.append((text, source, target))
            raise RateLimitedError("429")
        return await super().__call__(text, source, target)


@pytest.mark.asyncio
async def test_la_cadena_se_corta_al_agotar_su_tiempo_y_vuelve_al_espanol() -> None:
    """Con el plazo agotado no se sigue saltando: el token de Discord caduca a los 15 min."""
    now = [0.0]
    translator = FakeTranslator()

    async def slow(text: str, source: str, target: str) -> str:
        now[0] += 4.0
        return await FakeTranslator.__call__(translator, text, source, target)

    result = await run_chain(
        "hola", ["ja", "zu", "fr", "de"], slow, budget=10.0, clock=lambda: now[0]
    )

    assert result.route == ("es", "ja", "zu", "fr", "es")
    assert result.stopped_early


@pytest.mark.asyncio
async def test_sin_tiempo_para_la_espera_no_se_reintenta_el_limite() -> None:
    translator = FlakyTranslator(limited=1)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    result = await run_chain(
        "hola",
        ["ja", "zu"],
        translator,
        backoff=(20.0,),
        sleep=sleep,
        budget=5.0,
        clock=lambda: 0.0,
    )

    assert waits == []
    assert result.stopped_early


@pytest.mark.asyncio
async def test_con_esperas_un_limite_pasajero_no_corta_la_cadena() -> None:
    translator = FlakyTranslator(limited=2)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    result = await run_chain(
        "hola", ["ja", "zu", "fr"], translator, pause=0.3, backoff=(3, 8, 20), sleep=sleep
    )

    assert result.route == ("es", "ja", "zu", "fr", "es")
    assert not result.stopped_early
    # Un respiro entre saltos y, en el idioma limitado, dos esperas antes de reintentar.
    assert waits == [0.3, 3, 8, 0.3]


@pytest.mark.asyncio
async def test_si_el_limite_no_se_pasa_tras_las_esperas_se_corta() -> None:
    translator = FlakyTranslator(limited=10)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    result = await run_chain("hola", ["ja", "zu", "fr"], translator, backoff=(1, 2), sleep=sleep)

    assert result.stopped_early and result.route == ("es", "ja", "es")
    assert waits == [1, 2]


@pytest.mark.asyncio
async def test_si_no_puede_volver_al_espanol_devuelve_el_texto_donde_estaba() -> None:
    translator = FakeTranslator(fail={"es": TranslationError("caído")})
    result = await run_chain("hola", ["ja"], translator)

    assert result.final == "hola|ja"
    assert result.final_language == "ja"
    assert result.stopped_early


@pytest.mark.asyncio
async def test_demasiados_fallos_seguidos_cortan_la_cadena() -> None:
    langs = ["af", "sq", "de", "am", "ar", "hy", "as"]
    translator = FakeTranslator(fail={code: TranslationError("x") for code in langs})
    result = await run_chain("hola", langs, translator)

    attempted = [t for _, _, t in translator.calls]
    assert attempted == langs[:MAX_CONSECUTIVE_FAILURES]
    assert result.route == ("es",)
    assert result.final == "hola"
    assert result.stopped_early


@pytest.mark.asyncio
async def test_progreso_informa_de_cada_salto() -> None:
    seen: list[tuple[int, int, str]] = []

    async def on_progress(step: int, total: int, language: str) -> None:
        seen.append((step, total, language))

    await run_chain("hola", ["ja", "zu"], FakeTranslator(), on_progress)
    assert seen == [(1, 3, "ja"), (2, 3, "zu")]


def test_parse_google_response_une_los_trozos_de_cada_frase() -> None:
    data = [
        [["Hello world. ", "Hola mundo. ", None, None, 10], ["I like cheese.", "x"]],
        None,
        "es",
    ]
    assert parse_google_response(data) == "Hello world. I like cheese."


@pytest.mark.parametrize("data", [None, [], "html", [None]])
def test_parse_google_response_rechaza_formatos_raros(data: object) -> None:
    with pytest.raises(TranslationError):
        parse_google_response(data)


@pytest.mark.asyncio
async def test_keep_lines_rechaza_traducciones_que_juntan_lineas() -> None:
    async def merges(text: str, source: str, target: str) -> str:
        return text.replace("\n", " ")

    async def keeps(text: str, source: str, target: str) -> str:
        return text.replace("\n", " \n ")

    with pytest.raises(TranslationError):
        await keep_lines(merges, 2)("Ana\nLuis", "es", "ja")
    assert await keep_lines(keeps, 2)("Ana\nLuis", "es", "ja") == "Ana\nLuis"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("🎮・juegos", ("🎮・", "juegos")),
        ("general", ("", "general")),
        ("💬-chat-general", ("💬-", "chat-general")),
        ("★★★", ("★★★", "")),
    ],
)
def test_split_decoration_separa_el_adorno_inicial(name: str, expected: tuple[str, str]) -> None:
    assert split_decoration(name) == expected


def test_readable_channel_name_cambia_guiones_por_espacios() -> None:
    assert readable_channel_name("chat_de-voz") == "chat de voz"


def test_limiter_permite_dos_renombrados_cada_diez_minutos() -> None:
    now = [0.0]
    limiter = ChannelRenameLimiter(clock=lambda: now[0])

    limiter.record(1)
    limiter.record(1)
    assert not limiter.can_rename(1)
    assert limiter.can_rename(2)
    now[0] = ChannelRenameLimiter.WINDOW_SECONDS
    assert limiter.can_rename(1)
