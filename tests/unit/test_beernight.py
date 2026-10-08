"""Pruebas de la beernight: catálogo, reglas, histórico, audios y el cog `beernight`."""

from __future__ import annotations

import random
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.beernight import (
    LIVE_ACTIONS,
    Beernight,
    NightState,
    PanelButton,
    live_embed,
    mandate_text,
    parse_sound_args,
    summary_embed,
)
from bot.repositories.beernight import BeernightRepository
from bot.repositories.beernight_sounds import BeernightSoundStore
from bot.services.achievements import (
    BEER_KIND_PREFIX,
    BEER_TEETOTAL_MINUTES,
    BY_ID,
    CATALOG,
    beernight_close_stats,
    beernight_drink_stats,
    beernight_streak,
)
from bot.services.beernight import (
    EVENT_BOUNDS,
    MAX_CUSTOM,
    MAX_SOUNDS_PER_SLOT,
    BeernightError,
    Reason,
    Settings,
    SipRecord,
    SoundSlot,
    apply_cap,
    cast_event,
    clean_custom_text,
    duration_text,
    lie_votes_needed,
    liters,
    mandate_pool,
    parse_number,
    pick_event,
    pick_mandates,
    rotation_size,
    summarize,
)
from bot.services.beernight_catalog import (
    EVENT_BY_KEY,
    EVENTS,
    FAMILIES,
    MANDATE_BY_KEY,
    MANDATES,
    EventKind,
    NightEvent,
    Target,
)
from bot.services.levels import TIMEZONE

GUILD_ID = 1
CHANNEL_ID = 50
VOICE_ID = 60
HOST, ANA, BEA, CARLOS = 10, 11, 12, 13

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg no está instalado",
)

# -- Catálogo ---------------------------------------------------------------------------


def test_el_catalogo_trae_un_monton_de_mandamientos_y_eventos_sin_claves_repetidas() -> None:
    assert len(MANDATES) >= 150
    assert len(MANDATE_BY_KEY) == len(MANDATES)
    assert len(EVENTS) >= 60
    assert len(EVENT_BY_KEY) == len(EVENTS)


def test_cada_familia_tiene_mandamientos_de_sobra_para_rotar() -> None:
    for family in FAMILIES:
        assert sum(1 for m in MANDATES if m.family == family.key) >= 15, family.key
    assert {m.family for m in MANDATES} == {f.key for f in FAMILIES}


def test_los_mandamientos_caben_en_un_desplegable_y_piden_pocos_sorbos() -> None:
    for mandate in MANDATES:
        assert 0 < len(mandate.text) <= 100, mandate.key
        assert 1 <= mandate.sips <= 3, mandate.key


@pytest.mark.parametrize("event", EVENTS, ids=lambda e: e.key)
def test_cada_evento_arma_su_texto_sin_huecos(event: NightEvent) -> None:
    text = event.text.format(who="<@1>", a="<@2>", b="<@3>", n=event.sips, m=event.minutes)
    assert not re.search(r"\{\w*\}", text)
    if event.kind is EventKind.DECREE:
        assert event.rule and event.minutes > 0
    if event.kind is EventKind.DUEL:
        assert "{a}" in event.text and "{b}" in event.text
    if event.kind in (EventKind.CHALLENGE, EventKind.GIFT):
        assert "{who}" in event.text


def test_hay_eventos_de_cada_tipo_y_para_cada_objetivo() -> None:
    assert {e.kind for e in EVENTS} == set(EventKind)
    drink_targets = {e.target for e in EVENTS if e.kind is EventKind.DRINK}
    assert drink_targets == set(Target)


# -- Ajustes ----------------------------------------------------------------------------


def test_los_ajustes_se_guardan_y_se_leen_igual() -> None:
    settings = Settings(event_min=3, event_max=5, active=7, rotation=30, cap=12, sound=False,
                        disabled_families=frozenset({"canarias"}))  # fmt: skip
    assert Settings.from_json(settings.to_json()) == settings


def test_unos_ajustes_rotos_vuelven_a_los_de_por_defecto() -> None:
    assert Settings.from_json({"event_min": 50, "event_max": 3}) == Settings()
    assert Settings.from_json({"active": "cinco", "disabled_families": ["nada"]}) == Settings()


def test_los_ajustes_fuera_de_rango_se_rechazan() -> None:
    with pytest.raises(BeernightError):
        Settings(active=1).validated()
    with pytest.raises(BeernightError):
        Settings(event_min=10, event_max=5).validated()
    with pytest.raises(BeernightError):
        parse_number("abc", EVENT_BOUNDS)
    with pytest.raises(BeernightError):
        parse_number("999", EVENT_BOUNDS)
    assert parse_number(" 12 ", EVENT_BOUNDS) == 12


def test_un_mandamiento_propio_se_limpia_y_tiene_tope_de_largo() -> None:
    assert clean_custom_text("  quien   diga\nwepa ") == "quien diga wepa"
    with pytest.raises(BeernightError):
        clean_custom_text("   ")
    with pytest.raises(BeernightError):
        clean_custom_text("x" * 121)


# -- Mandamientos y eventos -------------------------------------------------------------


def test_los_mandamientos_elegidos_varian_de_familia_y_no_se_repiten() -> None:
    pool = mandate_pool(Settings())
    chosen = pick_mandates(pool, 5, random.Random(1))
    assert len(chosen) == 5
    assert len({m.family for m in chosen}) == 5
    again = pick_mandates(pool, 5, random.Random(1), exclude={m.key for m in chosen})
    assert not {m.key for m in again} & {m.key for m in chosen}


def test_las_familias_apagadas_no_salen() -> None:
    settings = Settings(disabled_families=frozenset(f.key for f in FAMILIES[1:]))
    pool = mandate_pool(settings)
    assert {m.family for m in pool} == {FAMILIES[0].key}
    chosen = pick_mandates(pool, 8, random.Random(2))
    assert len(chosen) == 8


def test_en_cada_rotacion_cambia_un_tercio() -> None:
    assert rotation_size(2) == 1
    assert rotation_size(5) == 2
    assert rotation_size(9) == 3


def test_un_evento_de_dos_no_sale_con_una_sola_persona() -> None:
    rng = random.Random(3)
    for _ in range(200):
        event = pick_event(1, rng)
        assert event is not None and event.players_needed == 1
    assert pick_event(0, rng) is None


def test_los_eventos_recientes_no_se_repiten_mientras_haya_otros() -> None:
    recent = [e.key for e in EVENTS[:-1]]
    assert pick_event(5, random.Random(4), recent=recent) == EVENTS[-1]


def _event(target: Target, kind: EventKind = EventKind.DRINK) -> NightEvent:
    return NightEvent("x", kind, "{who} {a} {b} {n}", 2, target)


def test_reparto_de_papeles_de_cada_evento() -> None:
    players = [ANA, BEA, CARLOS]
    rng = random.Random(5)
    sips = {ANA: 9, BEA: 1, CARLOS: 4}
    assert cast_event(_event(Target.ALL), players, rng).drinkers == tuple(players)
    assert cast_event(_event(Target.MOST), players, rng, sips=sips).drinkers == (ANA,)
    assert cast_event(_event(Target.LEAST), players, rng, sips=sips).drinkers == (BEA,)
    assert cast_event(_event(Target.HOST), players, rng, host=CARLOS).drinkers == (CARLOS,)
    assert cast_event(_event(Target.NEWEST), players, rng, newest=BEA).drinkers == (BEA,)
    spared = cast_event(_event(Target.ALL_BUT_ONE), players, rng)
    assert spared.who not in spared.drinkers and len(spared.drinkers) == 2
    pair = cast_event(_event(Target.PAIR), players, rng)
    assert pair.a != pair.b and set(pair.drinkers) == {pair.a, pair.b}
    duel = cast_event(_event(Target.ONE, EventKind.DUEL), players, rng)
    assert duel.a != duel.b and not duel.drinkers
    with pytest.raises(BeernightError):
        cast_event(_event(Target.ONE, EventKind.DUEL), [ANA], rng)


def test_el_anfitrion_ausente_no_bebe_por_ser_anfitrion() -> None:
    casting = cast_event(_event(Target.HOST), [ANA], random.Random(6), host=None)
    assert casting.drinkers == (ANA,)


# -- Sorbos, tope y resumen ---------------------------------------------------------------


def test_el_tope_por_hora_perdona_lo_que_sobra() -> None:
    assert apply_cap(3, 0, 0) == (3, 0)
    assert apply_cap(3, 8, 10) == (2, 1)
    assert apply_cap(3, 12, 10) == (0, 3)


def test_con_poca_gente_basta_un_voto_para_tumbar_un_chivatazo() -> None:
    assert lie_votes_needed(3) == 1
    assert lie_votes_needed(4) == 2


def test_el_resumen_saca_mvp_chivato_bulero_y_el_mandamiento_mas_incumplido() -> None:
    records = [
        SipRecord(ANA, 2, Reason.REPORT, "literal", BEA),
        SipRecord(ANA, 1, Reason.CONFESSION, "literal"),
        SipRecord(CARLOS, 1, Reason.LIE, "lag"),
        SipRecord(BEA, 2, Reason.GIFT, "reparte", CARLOS),
        SipRecord(ANA, 0, Reason.EVENT, "diana", forgiven=2),
    ]
    summary = summarize(records)
    assert summary.total == 6
    assert summary.mvp() == ANA
    assert summary.snitch() == BEA
    assert summary.liar() == CARLOS
    assert summary.most_broken() == "literal"
    assert summary.given == {BEA: 2, CARLOS: 2}
    assert summary.forgiven[ANA] == 2
    assert summary.kinds[ANA] == {Reason.REPORT, Reason.CONFESSION, Reason.EVENT}


def test_litros_y_duraciones_legibles() -> None:
    assert liters(40) == "1,0"
    assert duration_text(59) == "0 min"
    assert duration_text(3600 * 2 + 60 * 15) == "2 h 15 min"
    assert duration_text(3600) == "1 h"


# -- Logros -----------------------------------------------------------------------------


def test_beber_suma_sorbos_motivo_y_mayor_trago() -> None:
    delta = beernight_drink_stats(Reason.CONFESSION.value, 3, forgiven=1)
    assert delta.add == {
        "beer_sips": 3,
        f"{BEER_KIND_PREFIX}confesion": 1,
        "beer_confessions": 1,
        "beer_forgiven": 1,
    }
    assert delta.peak == {"beer_drink_max": 3}


def test_cerrar_la_noche_cuenta_fechas_rachas_y_secretos() -> None:
    started = datetime(2026, 12, 31, 22, 0, tzinfo=TIMEZONE)  # jueves, Nochevieja
    ended = datetime(2027, 1, 1, 6, 30, tzinfo=TIMEZONE)
    delta = beernight_close_stats(
        sips=69, minutes=510, crowd=5, mvp=True, host=True,
        started=started, ended=ended, streak=2,
    )  # fmt: skip
    assert delta.add == {
        "beer_nights": 1,
        "beer_hosted": 1,
        "beer_mvp": 1,
        "beer_nice": 1,
        "beer_dawn": 1,
        "beer_thursday": 1,
        "beer_nochevieja": 1,
    }
    assert delta.peak["beer_streak_max"] == 2
    sober = beernight_close_stats(
        sips=0, minutes=BEER_TEETOTAL_MINUTES, crowd=2, mvp=False, host=False,
        started=started, ended=started, streak=1,
    )  # fmt: skip
    assert sober.add["beer_zero_night"] == 1


def test_racha_de_dias_seguidos_con_beernight() -> None:
    days = [date(2026, 5, 1), date(2026, 5, 3), date(2026, 5, 4), date(2026, 5, 4)]
    assert beernight_streak(days) == 2
    assert beernight_streak([]) == 0


def test_la_beernight_tiene_su_categoria_de_logros_grande_y_con_secretos() -> None:
    mine = [a for a in CATALOG if a.category == "beernight"]
    assert len(mine) >= 40
    assert sum(a.secret for a in mine) >= len(mine) // 10
    collection = BY_ID["beer_all_reasons"]
    assert {stat for stat, _goal in collection.conditions} == {
        f"{BEER_KIND_PREFIX}{reason.value}" for reason in Reason
    }


# -- Repositorio --------------------------------------------------------------------------


async def _repo(tmp_path: Path) -> BeernightRepository:
    repo = BeernightRepository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    return repo


async def test_solo_una_noche_abierta_por_servidor(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    night = await repo.open_night(GUILD_ID, HOST, CHANNEL_ID, VOICE_ID, 100.0)
    with pytest.raises(BeernightError):
        await repo.open_night(GUILD_ID, ANA, CHANNEL_ID, None, 101.0)
    assert [n.id for n in await repo.open_nights()] == [night.id]
    assert await repo.close_night(night.id, 200.0)
    assert not await repo.close_night(night.id, 300.0)
    assert (await repo.get_night(GUILD_ID, night.id)).ended_at == 200.0  # type: ignore[union-attr]
    await repo.open_night(GUILD_ID, ANA, CHANNEL_ID, None, 400.0)


async def test_el_historico_guarda_sorbos_y_saca_el_ranking_de_siempre(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    for start, mvp in ((100.0, ANA), (1000.0, BEA)):
        night = await repo.open_night(GUILD_ID, HOST, CHANNEL_ID, None, start)
        for user in (ANA, BEA):
            assert await repo.add_participant(night.id, user, start)
        assert not await repo.add_participant(night.id, ANA, start + 1)
        await repo.add_sips(
            night.id,
            GUILD_ID,
            [
                SipRecord(mvp, 5, Reason.EVENT, "diana", created_at=start + 10),
                SipRecord(ANA if mvp == BEA else BEA, 1, Reason.REPORT, "lag", HOST, start + 20),
            ],
        )
        assert await repo.sips_since(night.id, mvp, start) == 5
        assert await repo.sips_since(night.id, mvp, start + 11) == 0
        await repo.close_night(night.id, start + 100)
    all_time = await repo.all_time(GUILD_ID)
    assert all_time.total_nights == 2
    assert all_time.sips == {ANA: 6, BEA: 6}
    assert all_time.mvps == {ANA: 1, BEA: 1}
    assert all_time.nights == {ANA: 2, BEA: 2}
    assert all_time.reports_ok == {HOST: 2}
    assert len(await repo.recent_nights(GUILD_ID)) == 2
    assert await repo.night_dates(GUILD_ID, ANA) == [100.0, 1000.0]


async def test_los_mandamientos_de_la_casa_tienen_tope(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    first = await repo.add_custom(GUILD_ID, ANA, "Quien diga wepa", 2, 1.0)
    for i in range(MAX_CUSTOM - 1):
        await repo.add_custom(GUILD_ID, ANA, f"m{i}", 1, 1.0)
    with pytest.raises(BeernightError):
        await repo.add_custom(GUILD_ID, ANA, "uno de más", 1, 1.0)
    assert (await repo.get_custom(GUILD_ID, first.id)) == first
    assert await repo.delete_custom(GUILD_ID, first.id)
    assert not await repo.delete_custom(GUILD_ID, first.id)


async def test_ajustes_por_servidor_y_borrado_al_salir(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    assert await repo.get_settings(GUILD_ID) == Settings()
    await repo.save_settings(GUILD_ID, Settings(cap=10))
    assert (await repo.get_settings(GUILD_ID)).cap == 10
    night = await repo.open_night(GUILD_ID, HOST, CHANNEL_ID, None, 1.0)
    await repo.add_participant(night.id, ANA, 1.0)
    await repo.add_sips(night.id, GUILD_ID, [SipRecord(ANA, 1, Reason.TOAST)])
    await repo.delete_guild_data(GUILD_ID)
    assert await repo.get_settings(GUILD_ID) == Settings()
    assert await repo.open_nights() == []
    assert await repo.participants(night.id) == {}


# -- Audios -------------------------------------------------------------------------------


async def test_los_audios_se_guardan_por_momento_con_tope(tmp_path: Path) -> None:
    store = BeernightSoundStore(tmp_path / "beernight")
    for i in range(MAX_SOUNDS_PER_SLOT):
        clip = tmp_path / f"clip{i}.ogg"
        clip.write_bytes(b"ogg" + bytes([i]))
        await store.save(GUILD_ID, SoundSlot.DRINK, ANA, clip, 1000.0)
    sounds = await store.list_sounds(GUILD_ID, SoundSlot.DRINK)
    assert len(sounds) == MAX_SOUNDS_PER_SLOT
    assert {s.uploader_id for s in sounds} == {ANA}
    extra = tmp_path / "extra.ogg"
    extra.write_bytes(b"x")
    with pytest.raises(BeernightError):
        await store.save(GUILD_ID, SoundSlot.DRINK, ANA, extra, 1000.0)
    assert (await store.counts(GUILD_ID))[SoundSlot.DRINK] == MAX_SOUNDS_PER_SLOT
    assert (await store.read_random(GUILD_ID, SoundSlot.DRINK, random.Random(1))).startswith(b"ogg")  # type: ignore[union-attr]  # noqa: E501
    assert await store.read_random(GUILD_ID, SoundSlot.END) is None
    assert not await store.delete(GUILD_ID, SoundSlot.DRINK, "../../etc/passwd")
    assert await store.delete(GUILD_ID, SoundSlot.DRINK, sounds[0].name)
    await store.delete_guild(GUILD_ID)
    assert await store.list_sounds(GUILD_ID, SoundSlot.DRINK) == []


def test_el_comando_de_texto_entiende_el_momento_del_audio() -> None:
    assert parse_sound_args(("sonido", "Beber")) is SoundSlot.DRINK
    assert parse_sound_args(("sonido", "nada")) is None
    assert parse_sound_args(("beber",)) is None


# -- Cog ----------------------------------------------------------------------------------


def _member(user_id: int, *, admin: bool = False, bot: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = bot
    member.display_name = f"m{user_id}"
    member.mention = f"<@{user_id}>"
    member.guild_permissions = (
        discord.Permissions(administrator=True) if admin else discord.Permissions.none()
    )
    member.voice = None
    return member


class World:
    """Un servidor de mentira con su canal, su llamada y un reloj que se mueve a mano."""

    def __init__(self, tmp_path: Path) -> None:
        self.now = 1_000_000.0
        self.members = {uid: _member(uid) for uid in (HOST, ANA, BEA, CARLOS)}
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = CHANNEL_ID
        self.sent: list[str] = []
        self.channel.send = AsyncMock(side_effect=self._send)
        self.channel.get_partial_message = MagicMock(
            return_value=MagicMock(edit=AsyncMock(), delete=AsyncMock())
        )
        self.voice = MagicMock(spec=discord.VoiceChannel)
        self.voice.id = VOICE_ID
        self.voice.members = [self.members[HOST], self.members[ANA], self.members[BEA]]
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = GUILD_ID
        self.guild.get_member = MagicMock(side_effect=self.members.get)
        self.guild.voice_client = None
        for member in self.members.values():
            member.guild = self.guild
        self.bot = MagicMock()
        self.bot.get_guild = MagicMock(return_value=self.guild)
        self.bot.get_channel = MagicMock(return_value=self.channel)
        self.bot.get_cog = MagicMock(return_value=None)
        self.repo = BeernightRepository(tmp_path / "bot.sqlite3")
        self.sounds = BeernightSoundStore(tmp_path / "beernight")
        self.cog = Beernight(
            self.bot, self.repo, self.sounds, rng=random.Random(7), clock=lambda: self.now
        )

    async def _send(self, content: str | None = None, **_: object) -> MagicMock:
        self.sent.append(content or "")
        message = MagicMock()
        message.id = 900 + len(self.sent)
        message.channel = self.channel
        return message

    def interaction(self, user_id: int) -> MagicMock:
        interaction = MagicMock()
        interaction.guild = self.guild
        interaction.user = self.members[user_id]
        interaction.channel = self.channel
        interaction.client = self.bot
        interaction.response.send_message = AsyncMock()
        interaction.response.edit_message = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.response.send_modal = AsyncMock()
        interaction.response.is_done = MagicMock(return_value=False)
        interaction.followup.send = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.message = MagicMock(id=777, channel=self.channel, edit=AsyncMock())
        return interaction

    async def start(self, *, settings: Settings | None = None) -> NightState:
        await self.repo.initialize()
        await self.repo.save_settings(GUILD_ID, settings or Settings(sound=False))
        self.members[HOST].voice = MagicMock(channel=self.voice)
        await self.cog.start(self.interaction(HOST))
        state = self.cog.nights[GUILD_ID]
        assert state.task is not None
        state.task.cancel()
        return state


async def test_empezar_apunta_a_la_llamada_y_saca_los_mandamientos(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    assert set(state.joined) == {HOST, ANA, BEA}
    assert state.present == {HOST, ANA, BEA}
    assert len(state.active) == Settings().active
    assert await world.repo.participants(state.night.id) == {
        HOST: world.now,
        ANA: world.now,
        BEA: world.now,
    }
    second = world.interaction(ANA)
    await world.cog.start(second)
    assert "Ya hay una beernight" in second.response.send_message.call_args.args[0]
    embed = live_embed(world.guild, state, world.now)
    assert len(embed) <= 6000
    assert all(len(f.value) <= 1024 for f in embed.fields)


async def test_confesar_apunta_los_sorbos_del_mandamiento(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    mandate = state.active[0].mandate
    await world.cog.confess(world.interaction(ANA), state.night.id, mandate.key)
    assert state.sips[ANA] == mandate.sips
    (record,) = await world.repo.night_records(state.night.id)
    assert (record.user_id, record.reason, record.mandate) == (ANA, Reason.CONFESSION, mandate.key)
    assert any("confiesa" in text for text in world.sent)


async def test_un_chivatazo_confirmado_lo_bebe_el_acusado(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    mandate = state.active[0].mandate
    await world.cog.report(world.interaction(ANA), state.night.id, BEA, mandate.key)
    (pending,) = world.cog.pending.values()
    # Ni el chivato ni el acusado pueden confirmarlo.
    for voter in (ANA, BEA):
        interaction = world.interaction(voter)
        await world.cog.vote(interaction, pending.id, "si")
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral")
    assert state.sips[BEA] == 0
    await world.cog.vote(world.interaction(HOST), pending.id, "si")
    assert state.sips[BEA] == mandate.sips
    assert pending.id not in world.cog.pending
    (record,) = await world.repo.night_records(state.night.id)
    assert (record.reason, record.by_user_id) == (Reason.REPORT, ANA)


async def test_un_chivatazo_falso_lo_bebe_el_chivato(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    mandate = state.active[0].mandate
    await world.cog.report(world.interaction(ANA), state.night.id, BEA, mandate.key)
    (pending,) = world.cog.pending.values()
    # Tres personas en la noche: basta el voto del acusado.
    await world.cog.vote(world.interaction(BEA), pending.id, "no")
    assert state.sips[ANA] == mandate.sips
    assert state.sips[BEA] == 0


async def test_con_mas_gente_hacen_falta_dos_votos_de_mentira(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    await world.cog._join(state, CARLOS, world.now)
    mandate = state.active[0].mandate
    await world.cog.report(world.interaction(ANA), state.night.id, BEA, mandate.key)
    (pending,) = world.cog.pending.values()
    await world.cog.vote(world.interaction(BEA), pending.id, "no")
    await world.cog.vote(world.interaction(BEA), pending.id, "no")
    assert state.sips[ANA] == 0
    await world.cog.vote(world.interaction(CARLOS), pending.id, "no")
    assert state.sips[ANA] == mandate.sips


async def test_un_duelo_lo_resuelve_otro_o_el_que_se_rinde(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    duel = next(e for e in EVENTS if e.kind is EventKind.DUEL)
    await world.cog.fire_event(state, duel)
    (pending,) = world.cog.pending.values()
    winner_interaction = world.interaction(pending.b)  # type: ignore[arg-type]
    await world.cog.vote(winner_interaction, pending.id, "a")
    assert "No vale" in winner_interaction.response.send_message.call_args.args[0]
    await world.cog.vote(world.interaction(pending.a), pending.id, "a")  # type: ignore[arg-type]
    assert state.sips[pending.a] == duel.sips  # type: ignore[index]


async def test_un_reto_cumplido_no_bebe_y_uno_fallado_si(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    challenge = next(e for e in EVENTS if e.kind is EventKind.CHALLENGE)
    await world.cog.fire_event(state, challenge)
    (pending,) = world.cog.pending.values()
    who = pending.who
    assert who is not None
    other = next(p for p in state.players() if p != who)
    own = world.interaction(who)
    await world.cog.vote(own, pending.id, "ok")
    assert pending.id in world.cog.pending
    await world.cog.vote(world.interaction(other), pending.id, "ok")
    assert state.sips[who] == 0
    await world.cog.fire_event(state, challenge)
    (pending,) = world.cog.pending.values()
    await world.cog.vote(world.interaction(pending.who), pending.id, "ko")  # type: ignore[arg-type]
    assert state.sips[pending.who] == challenge.sips  # type: ignore[index]


async def test_solo_reparte_quien_le_toca(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    gift = next(e for e in EVENTS if e.kind is EventKind.GIFT)
    await world.cog.fire_event(state, gift)
    (pending,) = world.cog.pending.values()
    who = pending.who
    assert who is not None
    other = next(p for p in state.players() if p != who)
    await world.cog.gift(world.interaction(other), pending.id, world.members[CARLOS])
    assert state.sips[CARLOS] == 0
    await world.cog.gift(world.interaction(who), pending.id, world.members[CARLOS])
    assert state.sips[CARLOS] == gift.sips
    (record,) = await world.repo.night_records(state.night.id)
    assert (record.reason, record.by_user_id) == (Reason.GIFT, who)


async def test_un_evento_de_todos_y_el_tope_por_hora(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start(settings=Settings(sound=False, cap=2))
    everyone = next(e for e in EVENTS if e.target is Target.ALL and e.sips == 2)
    await world.cog.fire_event(state, everyone)
    await world.cog.fire_event(state, everyone)
    assert state.sips == {HOST: 2, ANA: 2, BEA: 2}
    records = await world.repo.night_records(state.night.id)
    assert sum(r.forgiven for r in records) == 6


async def test_los_que_salen_de_la_llamada_o_se_retiran_no_juegan(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    left = MagicMock(channel=world.voice)
    gone = MagicMock(channel=None)
    await world.cog.on_voice_state_update(world.members[BEA], left, gone)
    await world.cog.toggle_retire(world.interaction(ANA), state)
    assert state.players() == [HOST]
    joined = MagicMock(channel=world.voice)
    await world.cog.on_voice_state_update(world.members[CARLOS], gone, joined)
    assert CARLOS in state.joined and state.players() == [HOST, CARLOS]


async def test_un_decreto_entra_y_caduca(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    decree = next(e for e in EVENTS if e.kind is EventKind.DECREE)
    await world.cog.fire_event(state, decree)
    assert any(a.mandate.key == f"ev:{decree.key}" for a in state.active)
    world.now += decree.minutes * 60 + 1
    state.next_event_at = world.now + 999
    await world.cog.tick(state)
    assert not any(a.mandate.key == f"ev:{decree.key}" for a in state.active)


async def test_la_rotacion_cambia_mandamientos_con_el_tiempo(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    before = {a.mandate.key for a in state.active}
    world.now = state.next_rotation_at + 1
    state.next_event_at = world.now + 999
    await world.cog.tick(state)
    after = {a.mandate.key for a in state.active}
    assert len(after) == Settings().active
    assert len(before - after) == rotation_size(Settings().active)


async def test_terminar_cierra_la_noche_y_publica_el_resumen(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    mandate = state.active[0].mandate
    await world.cog.confess(world.interaction(ANA), state.night.id, mandate.key)
    world.now += 3600
    host = world.interaction(HOST)
    await world.cog.panel_action(host, "fin", state.night.id)
    assert GUILD_ID not in world.cog.nights
    embed = host.followup.send.call_args.kwargs["embed"]
    assert "Se acabó" in embed.title
    assert f"<@{ANA}>" in embed.description
    (night,) = await world.repo.recent_nights(GUILD_ID)
    assert night.ended_at == world.now


async def test_solo_el_anfitrion_o_un_admin_gestiona_la_noche(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    interaction = world.interaction(ANA)
    await world.cog.panel_action(interaction, "fin", state.night.id)
    assert GUILD_ID in world.cog.nights
    world.members[ANA].guild_permissions = discord.Permissions(administrator=True)
    await world.cog.panel_action(world.interaction(ANA), "rotar", state.night.id)
    assert world.cog.nights[GUILD_ID].log[-1].startswith("🔄")


async def test_un_reinicio_cierra_la_noche_abierta_con_lo_apuntado(tmp_path: Path) -> None:
    world = World(tmp_path)
    await world.repo.initialize()
    night = await world.repo.open_night(GUILD_ID, HOST, CHANNEL_ID, None, 100.0)
    await world.repo.add_participant(night.id, ANA, 100.0)
    await world.repo.add_sips(
        night.id, GUILD_ID, [SipRecord(ANA, 2, Reason.TOAST, created_at=500.0)]
    )
    await world.cog.recover()
    closed = await world.repo.get_night(GUILD_ID, night.id)
    assert closed is not None and closed.ended_at == 500.0
    world.channel.send.assert_awaited()


async def test_ajustes_del_formulario_y_mandamientos_de_la_casa(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = await world.start()
    host = world.interaction(HOST)
    await world.cog.save_rhythm(host, GUILD_ID, events="3-4", active="3", rotation="10", cap="5")
    settings = await world.repo.get_settings(GUILD_ID)
    assert (settings.event_min, settings.event_max, settings.active, settings.cap) == (3, 4, 3, 5)
    assert sum(1 for a in state.active if a.until is None) == 3
    bad = world.interaction(HOST)
    await world.cog.save_rhythm(bad, GUILD_ID, events="9-2", active="3", rotation="10", cap="0")
    assert "❌" in bad.response.send_message.call_args.args[0]
    stranger = world.interaction(ANA)
    await world.cog.save_rhythm(stranger, GUILD_ID, events="5", active="4", rotation="10", cap="0")
    assert (await world.repo.get_settings(GUILD_ID)).active == 3
    await world.cog.add_custom(world.interaction(ANA), GUILD_ID, "Quien diga wepa", "2")
    assert [c.text for c in state.custom] == ["Quien diga wepa"]
    assert any(m.key == f"c{state.custom[0].id}" for m in state.pool())
    assert mandate_text(f"c{state.custom[0].id}", {f"c{state.custom[0].id}": "x"}) == "x"


def test_los_botones_del_panel_caben_en_dos_filas() -> None:
    view = discord.ui.View(timeout=None)
    for action in LIVE_ACTIONS:
        view.add_item(PanelButton(action, 3))
    rows: dict[int, int] = {}
    for item in view.children:
        rows[item.row or 0] = rows.get(item.row or 0, 0) + 1  # type: ignore[attr-defined]
    assert all(count <= 5 for count in rows.values())


def test_el_resumen_cabe_en_un_embed() -> None:
    from bot.repositories.beernight import Night

    night = Night(1, GUILD_ID, HOST, CHANNEL_ID, VOICE_ID, 0.0, 7200.0)
    records = [SipRecord(uid, 3, Reason.EVENT, "diana") for uid in range(100, 140)]
    embed = summary_embed(None, night, summarize(records), 40, names=lambda _k: "x" * 120)
    assert len(embed) <= 6000
    assert all(len(f.value) <= 1024 for f in embed.fields)


@requires_ffmpeg
async def test_subir_un_audio_lo_convierte_y_lo_guarda(tmp_path: Path) -> None:
    world = World(tmp_path)
    wav = tmp_path / "pitido.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         str(wav)],
        check=True,
    )  # fmt: skip
    attachment = MagicMock(spec=discord.Attachment)
    attachment.content_type = "audio/wav"
    attachment.size = wav.stat().st_size
    attachment.read = AsyncMock(return_value=wav.read_bytes())
    text = await world.cog.upload_sound(GUILD_ID, world.members[ANA], SoundSlot.START, attachment)
    assert text.startswith("🔊"), text
    (sound,) = await world.sounds.list_sounds(GUILD_ID, SoundSlot.START)
    assert sound.uploader_id == ANA
