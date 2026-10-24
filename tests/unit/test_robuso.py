"""Pruebas de 🎸 Robuso: qué cuenta de cada mensaje, la categoría secreta y el cog que escucha."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import robuso as robuso_cog
from bot.cogs.achievements import Achievements
from bot.services.achievements import (
    APART,
    BY_ID,
    CATEGORY_BY_KEY,
    COUNTED,
    UNLOCKED_STAT,
    StatDelta,
    menu_entries,
    meta_stats,
    newly_unlocked,
)
from bot.services.hong_kong import HONG_KONG
from bot.services.robuso import (
    DAY_SECONDS,
    PROMISE_SECONDS,
    ROBUSO_ID,
    author_stats,
    echoes,
    fan_stats,
    game_stats,
    names_robuso,
    promises_to_play,
    says_goodbye,
)

GUILD = 1
FRIEND = 2
CHANNEL = 5
#: Martes 13 de octubre de 2026 a las 12:00 en Hong Kong (6:00 en Madrid).
NOON_HK = datetime(2026, 10, 13, 12, tzinfo=HONG_KONG).timestamp()


def stats_of(content: str, *, in_hk: bool = False, images: int = 0, at: float = NOON_HK) -> set:
    return set(author_stats(content, now=at, in_hk=in_hk, images=images))


# -- Lo que se detecta ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "stat"),
    [
        ("Los indios del curro otra vez con lo mismo", "robuso_coworkers"),
        ("mis compañeros de la oficina no saben hacer nada", "robuso_coworkers"),
        ("Otra reunión de dos horas, odio mi trabajo", "robuso_work_rant"),
        ("Escuchad esto, es shoegaze del bueno", "robuso_altmusic"),
        ("https://open.spotify.com/track/abc", "robuso_altmusic"),
        ("Hoy estuve tocando el bajo tres horas", "robuso_bass"),
        ("Me he pillado una pedalera nueva para el bajo", "robuso_bass"),
        ("¿Alguien ha jugado al Balatro?", "robuso_indie_talk"),
        ("Un roguelike pixelado que no conoce nadie", "robuso_indie_talk"),
        ("*suspira dramáticamente*", "robuso_roleplay"),
        ("a" * 500, "robuso_essays"),
    ],
)
def test_lo_que_cuenta_de_un_mensaje_de_robuso(text: str, stat: str) -> None:
    assert stat in stats_of(text)


@pytest.mark.parametrize(
    "text",
    [
        "Me voy de viaje a la India en verano",
        "Está bajo la mesa",
        "Voy a bajar a por pan",
        "El precio está muy bajo",
        "Busca una alternativa, que ese plan alternativo no vale",
        "Qué emocionante",
        "El cielo está celeste y luego gris",
        "**negrita**",
        "Hola, ¿qué tal?",
    ],
)
def test_las_palabras_normales_no_cuentan(text: str) -> None:
    assert stats_of(text) == set()


def test_lo_de_hong_kong_solo_cuenta_mientras_esta_alli() -> None:
    night = datetime(2026, 10, 13, 3, tzinfo=HONG_KONG).timestamp()
    there = stats_of("Comiendo dim sum en Kowloon", in_hk=True, images=2, at=night)
    assert {"robuso_hk_photos", "robuso_hk_talk", "robuso_hk_night"} <= there
    here = stats_of("Comiendo dim sum en Kowloon", images=2, at=night)
    assert not {s for s in here if s.startswith("robuso_hk_")}
    # En Madrid son las 21:00 del día anterior: tampoco es su madrugada.
    assert "robuso_madrid_night" not in here
    madrid_night = datetime(2026, 10, 13, 10, tzinfo=HONG_KONG).timestamp()  # 4:00 en Madrid
    assert "robuso_madrid_night" in stats_of("no puedo dormir", at=madrid_night)
    office = stats_of("en la oficina", in_hk=True)
    assert "robuso_hk_office" in office


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ahora me conecto", True),
        ("¿Jugamos?", True),
        ("en 5 entro", True),
        ("luego juego un rato", True),
        ("voy a comer", False),
        ("me voy", False),
    ],
)
def test_promete_jugar(text: str, expected: bool) -> None:
    assert promises_to_play(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("me voy a dormir", True), ("Buenas noches", True), ("a mimir", True), ("voy ahora", False)],
)
def test_se_despide(text: str, expected: bool) -> None:
    assert says_goodbye(text) is expected


def test_nombrar_a_robuso_no_es_mencionarlo() -> None:
    assert names_robuso("¿Dónde anda Robuso?")
    assert not names_robuso(f"<@{ROBUSO_ID}>")


def test_lo_que_suma_a_quien_le_escribe() -> None:
    night = datetime(2026, 10, 13, 4, tzinfo=HONG_KONG).timestamp()
    stats = fan_stats(
        "jajaja amigos de discord",
        now=night,
        pinged=True,
        replied=False,
        reply_images=False,
        laughed=True,
        in_hk=True,
        silent_for=8 * DAY_SECONDS,
    )
    assert stats == {
        "robuso_pings": 1,
        "robuso_wakeups": 1,
        "robuso_missed": 1,
        "robuso_friends_said": 1,
    }
    reply = fan_stats(
        "jajaja qué envidia",
        now=night,
        pinged=False,
        replied=True,
        reply_images=True,
        laughed=True,
        in_hk=False,
        silent_for=60,
    )
    # La envidia es de las fotos de Hong Kong: desde Madrid no cuenta.
    assert reply == {"robuso_replies": 1, "robuso_laughs": 1}


def test_juegos_tipo_robuso() -> None:
    assert game_stats("Minecraft", others_playing=False) == {}
    assert game_stats("Noita", others_playing=False) == {
        "robuso_indie_play": 1,
        "robuso_solo_game": 1,
    }
    assert game_stats("Balatro", others_playing=True) == {"robuso_indie_play": 1}
    assert game_stats("Un juego rarísimo", others_playing=False) == {"robuso_solo_game": 1}


def test_el_eco_suma_una_vez_por_estadistica() -> None:
    assert echoes({"messages": 1, "casino_wagered": 500, "otra": 3}) == {
        "robuso_msgs": 1,
        "robuso_casino": 1,
    }
    assert echoes({"casino_wagered": 0}) == {}


# -- La categoría secreta ---------------------------------------------------------------


def test_la_categoria_no_sale_en_el_menu_hasta_conseguir_uno_suyo() -> None:
    assert CATEGORY_BY_KEY["robuso"].secret
    assert "robuso" not in dict(menu_entries())
    assert "robuso" not in dict(menu_entries(["completionist"]))
    assert "robuso" in dict(menu_entries(["robuso_hola"]))


def test_va_aparte_del_total_y_de_coleccionista() -> None:
    assert APART and all(a.category == "robuso" for a in APART)
    assert not {a.id for a in APART} & {a.id for a in COUNTED}
    assert meta_stats(["robuso_hola", "robuso_viaje_1"])[UNLOCKED_STAT] == 0
    # Pero se consiguen como cualquier otro.
    assert "robuso_hola" in newly_unlocked({"robuso_msgs": 1}, [])


def test_la_categoria_es_grande_y_con_secretos() -> None:
    assert len(APART) >= 100
    secrets = sum(a.secret for a in APART)
    assert len(APART) / 10 <= secrets <= len(APART) / 4
    # Ambiguo: ninguna descripción dice que es solo de Robuso.
    assert not any("solo robuso" in a.description.lower() for a in APART)


def test_solo_lo_de_robuso_suma_su_eco() -> None:
    cog = Achievements(MagicMock(), MagicMock())
    cog.note(GUILD, ROBUSO_ID, StatDelta(add={"messages": 1}))
    cog.note(GUILD, FRIEND, StatDelta(add={"messages": 1}))
    assert cog._pending[GUILD][ROBUSO_ID].add == {"messages": 1, "robuso_msgs": 1}
    assert cog._pending[GUILD][FRIEND].add == {"messages": 1}


# -- El cog -----------------------------------------------------------------------------


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def noted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, dict, dict]]:
    """Lo que el cog manda a los logros: `(miembro, add, peak)`."""
    calls: list[tuple[int, dict, dict]] = []

    def note(_bot, _guild_id, user_id, delta, _channel_id=None) -> None:  # noqa: ANN001
        calls.append((user_id, dict(delta.add), dict(delta.peak)))

    monkeypatch.setattr(robuso_cog.achievements, "note", note)
    return calls


def make_cog(clock: Clock, *, last_message: float | None = None) -> robuso_cog.Robuso:
    bot = MagicMock()
    bot.get_cog.return_value = None
    seen = SimpleNamespace(message_at=last_message, voice_at=None)
    bot.last_seen.guild = AsyncMock(return_value={ROBUSO_ID: seen})
    return robuso_cog.Robuso(bot, clock=clock)


def message(
    author_id: int,
    content: str,
    at: float,
    *,
    mentions: tuple[int, ...] = (),
    reply_to: object = None,
    message_id: int = 100,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=GUILD),
        author=SimpleNamespace(id=author_id, bot=False, voice=None),
        webhook_id=None,
        is_system=lambda: False,
        channel=SimpleNamespace(id=CHANNEL),
        created_at=datetime.fromtimestamp(at, HONG_KONG),
        content=content,
        attachments=[],
        mentions=[SimpleNamespace(id=i) for i in mentions],
        reference=SimpleNamespace(resolved=reply_to) if reply_to is not None else None,
        type=discord.MessageType.reply if reply_to is not None else discord.MessageType.default,
    )


def added(calls: list, user_id: int, stat: str) -> int:
    return sum(add.get(stat, 0) for uid, add, _peak in calls if uid == user_id)


async def test_dice_que_viene_y_no_viene_y_planta_a_quien_espera(noted: list) -> None:
    clock = Clock(NOON_HK)
    cog = make_cog(clock)
    await cog.on_message(message(ROBUSO_ID, "ahora me conecto", clock.now))
    waiting = SimpleNamespace(id=FRIEND, bot=False)
    voice = SimpleNamespace(id=7, members=[waiting], guild=SimpleNamespace(afk_channel=None))
    cog.bot.get_guild.return_value = SimpleNamespace(voice_channels=[voice])
    cog.bot.guilds = []
    cog.check(clock.now + PROMISE_SECONDS - 60)
    assert added(noted, ROBUSO_ID, "robuso_flakes") == 0
    cog.check(clock.now + PROMISE_SECONDS)
    assert added(noted, ROBUSO_ID, "robuso_flakes") == 1
    assert added(noted, FRIEND, "robuso_waited") == 1


async def test_cumple_su_palabra_si_entra_antes_de_dos_horas(noted: list) -> None:
    clock = Clock(NOON_HK)
    cog = make_cog(clock)
    await cog.on_message(message(ROBUSO_ID, "¿jugamos?", clock.now))
    clock.now += 600
    member = SimpleNamespace(id=ROBUSO_ID, guild=SimpleNamespace(id=GUILD, afk_channel=None))
    channel = SimpleNamespace(id=7, members=[])
    await cog.on_voice_state_update(
        member, SimpleNamespace(channel=None), SimpleNamespace(channel=channel)
    )
    assert added(noted, ROBUSO_ID, "robuso_kept_word") == 1
    cog.bot.guilds = []
    cog.check(clock.now + PROMISE_SECONDS)
    assert added(noted, ROBUSO_ID, "robuso_flakes") == 0


async def test_volver_tras_una_semana_y_la_bienvenida(noted: list) -> None:
    clock = Clock(NOON_HK)
    cog = make_cog(clock, last_message=NOON_HK - 8 * DAY_SECONDS)
    await cog.on_message(message(ROBUSO_ID, "holi", clock.now))
    assert (ROBUSO_ID, {}, {"robuso_silence_max": 8}) in noted
    await cog.on_message(message(FRIEND, "¡¡Ha vuelto!!", clock.now + 60))
    await cog.on_message(message(3, "ey", clock.now + 120))
    assert added(noted, FRIEND, "robuso_welcomes") == 1
    assert added(noted, 3, "robuso_welcomes") == 0


async def test_echarlo_de_menos_cuenta_una_vez_por_ausencia(noted: list) -> None:
    clock = Clock(NOON_HK)
    cog = make_cog(clock, last_message=NOON_HK - 10 * DAY_SECONDS)
    await cog.on_message(message(FRIEND, "¿Y Robuso?", clock.now))
    await cog.on_message(
        message(FRIEND, f"<@{ROBUSO_ID}> vuelve", clock.now, mentions=(ROBUSO_ID,))
    )
    assert added(noted, FRIEND, "robuso_missed") == 1


async def test_despedirse_y_seguir_escribiendo(noted: list) -> None:
    clock = Clock(NOON_HK)
    cog = make_cog(clock)
    await cog.on_message(message(ROBUSO_ID, "me voy a dormir", clock.now))
    await cog.on_message(message(ROBUSO_ID, "buenas noches", clock.now + 30))
    assert added(noted, ROBUSO_ID, "robuso_fake_goodbye") == 0
    await cog.on_message(message(ROBUSO_ID, "por cierto, una cosa", clock.now + 300))
    assert added(noted, ROBUSO_ID, "robuso_fake_goodbye") == 1


async def test_los_temas_cuentan_una_vez_al_dia(noted: list) -> None:
    clock = Clock(NOON_HK)
    cog = make_cog(clock)
    await cog.on_message(message(ROBUSO_ID, "los indios del curro", clock.now))
    await cog.on_message(message(ROBUSO_ID, "otra vez los indios", clock.now + 60))
    assert added(noted, ROBUSO_ID, "robuso_coworkers") == 1
    clock.now += DAY_SECONDS
    await cog.on_message(message(ROBUSO_ID, "los indios, de nuevo", clock.now))
    assert added(noted, ROBUSO_ID, "robuso_coworkers") == 2


async def test_responder_dos_veces_al_mismo_mensaje_cuenta_una(noted: list) -> None:
    clock = Clock(NOON_HK)
    cog = make_cog(clock)
    original = SimpleNamespace(id=55, author=SimpleNamespace(id=ROBUSO_ID), attachments=[])
    for text in ("jajaja", "jajajaja"):
        reply = message(FRIEND, text, clock.now, reply_to=original, mentions=(ROBUSO_ID,))
        await cog.on_message(reply)
    assert added(noted, FRIEND, "robuso_replies") == 1
    assert added(noted, FRIEND, "robuso_laughs") == 1
    # Responder no es mencionar, aunque Discord meta a Robuso en las menciones.
    assert added(noted, FRIEND, "robuso_pings") == 0


class FakeAchievements:
    """Lo justo del cog de logros para `set_place`: leer y apuntar las estadísticas."""

    def __init__(self) -> None:
        self.stats: dict[str, int] = {}

    async def fresh_profile(self, _guild_id: int, _user_id: int) -> SimpleNamespace:
        return SimpleNamespace(stats=dict(self.stats))

    async def apply(self, _guild_id: int, _user_id: int, delta: StatDelta, _channel) -> list:  # noqa: ANN001
        for stat, value in delta.add.items():
            self.stats[stat] = self.stats.get(stat, 0) + value
        for stat, value in delta.peak.items():
            self.stats[stat] = max(self.stats.get(stat, 0), value)
        return []


async def test_viajes_y_vueltas_dicen_donde_esta(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAchievements()
    monkeypatch.setattr(robuso_cog, "find_cog", lambda _bot, _cls: fake)
    clock = Clock(NOON_HK)
    cog = make_cog(clock)
    assert await cog.in_hong_kong(GUILD) is False
    assert await cog.set_place(GUILD, hong_kong=True, channel=None) is True
    assert await cog.set_place(GUILD, hong_kong=True, channel=None) is False
    assert await cog.in_hong_kong(GUILD) is True
    clock.now += 21 * DAY_SECONDS
    assert await cog.set_place(GUILD, hong_kong=False, channel=None) is True
    assert fake.stats["robuso_trips"] == fake.stats["robuso_returns"] == 1
    assert fake.stats["robuso_trip_days_max"] == 21
    assert await cog.in_hong_kong(GUILD) is False
    assert BY_ID["robuso_estancia_21"].conditions == (("robuso_trip_days_max", 21),)
