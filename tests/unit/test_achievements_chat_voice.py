"""Pruebas de los logros de chat, risas, voz, música, imágenes, babel y Coleccionista."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from bot.cogs.achievements import Achievements, group_embed
from bot.repositories.achievements import AchievementRepository, Profile
from bot.services.achievements import (
    AVAILABLE,
    BY_ID,
    CATALOG,
    CATEGORIES,
    CHAT_GROUP,
    IMG_EFFECTS_STAT,
    VOICE_GROUP,
    Achievement,
    ChatTracker,
    Rarity,
    analyze_laugh,
    babel_stats,
    group_sections,
    image_stats,
    is_keyboard_smash,
    is_laugh,
    is_laugh_emoji,
    laugh_reply_stats,
    message_delta,
    meta_stats,
    music_queue_stats,
    music_volume_stats,
    newly_unlocked,
    voice_move_stats,
    with_derived,
)
from bot.services.levels import TIMEZONE

GUILD = 1


def at(hour: int, minute: int = 0, *, month: int = 3, day: int = 10) -> datetime:
    """10 de marzo de 2026 (martes) a la hora indicada, en hora canaria."""
    return datetime(2026, month, day, hour, minute, tzinfo=TIMEZONE)


# -- Risas -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("jajaja", "es"),
        ("JAJAJAJ", "es"),
        ("jsjsjs", "es"),
        ("ajajajaj", "es"),
        ("jajsjajsj", "es"),
        ("jejeje", "es"),
        ("jijiji", "es"),
        ("jajá", "es"),
        ("ja ja ja", "es"),
        ("hahaha", "en"),
        ("ahahah", "en"),
        ("huehue", "en"),
        ("lol", "en"),
        ("loool", "en"),
        ("lmaooo", "en"),
        ("rofl", "en"),
        ("kekw", "en"),
        ("ha ha", "en"),
        ("xD", "xd"),
        ("xddd", "xd"),
        ("😂", "emoji"),
        ("🤣🤣", "emoji"),
        ("<:kekw:123456>", "emoji"),
        ("<a:pepelaugh:42>", "emoji"),
        ("💀", "skull"),
        ("ajsjsjsjd", "smash"),
        ("asdfghjklñ", "smash"),
        ("kkkkk", "intl"),
        ("rsrsrs", "intl"),
        ("mdr", "intl"),
        ("ptdr", "intl"),
        ("wwwww", "intl"),
        ("ㅋㅋㅋ", "intl"),
        ("哈哈哈", "intl"),
        ("хахаха", "intl"),
        ("me meo", "phrase"),
        ("me parto contigo", "phrase"),
        ("qué risa", "phrase"),
        ("me cago de risa", "phrase"),
        ("lloroooo", "phrase"),
    ],
)
def test_cada_forma_de_reirse_cuenta_con_su_tipo(text: str, kind: str) -> None:
    laugh = analyze_laugh(text)
    assert kind in laugh.kinds
    assert is_laugh(text)


@pytest.mark.parametrize(
    "text",
    [
        "ja",
        "jamón",
        "jeans",
        "hola",
        "José",
        "ajá",
        "jauja",
        "alaska",
        "falsas",
        "salsa",
        "www.google.com",
        "mira https://x.com/jajaja",
        "he comido",
        "me muero de hambre",
    ],
)
def test_palabras_que_no_son_risas(text: str) -> None:
    assert not is_laugh(text)


@pytest.mark.parametrize("text", ["ja", "ja.", "jaja.", "ja, ja.", "JE."])
def test_la_risa_seca_de_funcionario_no_es_risa(text: str) -> None:
    laugh = analyze_laugh(text)
    assert laugh.dry
    assert not laugh.laughed


def test_risa_larga_gritada_y_mezclada() -> None:
    laugh = analyze_laugh("JAJAJAJAJAJAJAJAJAJAJA lol 😂")
    assert laugh.kinds == {"es", "en", "emoji"}
    assert laugh.longest == 22
    assert laugh.shouted


def test_aporreo_del_teclado() -> None:
    assert is_keyboard_smash("ajsjsjs")
    assert is_keyboard_smash("aksjdhaksjd")
    assert not is_keyboard_smash("shhhhh")  # dos letras distintas
    assert not is_keyboard_smash("dallas")  # demasiadas aes


@pytest.mark.parametrize("emoji", ["😂", "🤣", "💀", "☠️", "kekw", "pepelaugh", "OMEGALUL"])
def test_reacciones_de_risa(emoji: str) -> None:
    assert is_laugh_emoji(emoji)


@pytest.mark.parametrize("emoji", ["👍", "❤️", "pepehands"])
def test_reacciones_que_no_son_de_risa(emoji: str) -> None:
    assert not is_laugh_emoji(emoji)


def test_un_mensaje_con_risa_suma_sus_tipos_y_maximos() -> None:
    delta = message_delta("jajajaja xd 💀", when=at(3))
    assert delta.add["msg_laughs"] == 1
    assert delta.add["laugh_es"] == 1
    assert delta.add["laugh_xd"] == 1
    assert delta.add["laugh_skull"] == 1
    assert delta.add["msg_xd"] == 1
    assert delta.add["laugh_night"] == 1
    assert delta.peak == {"laugh_len_max": 8, "laugh_kinds_max": 3}


def test_reirse_de_hacienda() -> None:
    assert message_delta("jajaja Hacienda otra vez", when=at(12)).add["laugh_sanxe"] == 1
    assert "laugh_sanxe" not in message_delta("Hacienda otra vez", when=at(12)).add


def test_politiglota_de_la_risa_pide_todos_los_tipos() -> None:
    kinds = {f"laugh_{kind}": 1 for kind in ("es", "en", "emoji", "skull", "smash", "intl")}
    assert "laugh_all_kinds" not in newly_unlocked(kinds, [])
    kinds |= {"laugh_phrase": 1, "laugh_xd": 1}
    assert "laugh_all_kinds" in newly_unlocked(kinds, [])


# -- Reírse de alguien -----------------------------------------------------------------


def test_reirse_respondiendo_da_credito_al_gracioso() -> None:
    out = laugh_reply_stats(author_id=1, replied_author_id=2, replied_is_bot=False)
    assert out[1].add == {"laugh_replies": 1}
    assert out[2].add == {"laughs_caused": 1}


def test_reirse_de_uno_mismo_y_del_bot() -> None:
    own = laugh_reply_stats(author_id=1, replied_author_id=1, replied_is_bot=False)
    assert own[1].add["laugh_self"] == 1
    assert list(own) == [1]
    bot = laugh_reply_stats(author_id=1, replied_author_id=999, replied_is_bot=True)
    assert bot[1].add["laugh_at_bot"] == 1
    assert 999 not in bot


# -- Estilo y lengua -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "stat"),
    [
        ("chacho, ¿cogemos la guagua?", "msg_canario"),
        ("ños, qué calor", "msg_canario"),
        ("papas arrugadas con mojo", "msg_canario"),
        ("wepa, bendito", "msg_boricua"),
        ("me cago en todo", "msg_swear"),
        ("joder", "msg_swear"),
        ("ostras, jolín", "msg_mild_swear"),
        ("muchas gracias", "msg_thanks"),
        ("perdón, me equivoqué", "msg_sorry"),
        ("buenos días", "msg_good_morning"),
        ("buenas nocheees", "msg_good_night"),
        ("el PSOE y el Congreso", "msg_politics"),
        ("ya despega el Falcon", "msg_falcon"),
        ("eso es un bulo", "msg_fango"),
        ("¿y mi paguita?", "msg_paguita"),
        ("léete el Manual de resistencia", "msg_manual"),
        ("Hacienda somos todos", "msg_hacienda"),
        ("eso lo arreglaba yo en dos días", "msg_cuñado"),
        ("ola k ase", "msg_ola_k_ase"),
        ("hazme un bizum", "msg_bizum_ask"),
        ("¿vienes?", "msg_rae"),
        ("¡¡¡vamos!!!", "msg_exclaim"),
        ("el final es ||que muere||", "msg_spoiler"),
        ("usa `print()`", "msg_code"),
        ("😀😃😄😁😆", "msg_emoji_heavy"),
        ("🗿", "msg_only_emoji"),
        ("👍🏽", "msg_only_emoji"),
        ("holaaaaaaa", "msg_stretch"),
        ("esternocleidomastoideos", "msg_long_word"),
        ("Dábale arroz a la zorra el abad", "msg_palindrome"),
        ("tengo 69 Y$", "msg_nice"),
    ],
)
def test_lo_que_se_dice_cuenta(text: str, stat: str) -> None:
    assert message_delta(text, when=at(12)).add.get(stat) == 1


@pytest.mark.parametrize(
    ("text", "stat"),
    [
        ("nos vemos", "msg_canario"),  # «nos» no es «ños»
        ("podemos quedar", "msg_politics"),  # el verbo, no el partido
        ("tengo 690 Y$", "msg_nice"),
        ("jajajaja", "msg_palindrome"),  # palíndromo de dos letras: no vale
        ("hola 🗿", "msg_only_emoji"),
    ],
)
def test_lo_que_no_cuenta(text: str, stat: str) -> None:
    assert stat not in message_delta(text, when=at(12)).add


def test_menciones_masivas_y_everyone() -> None:
    delta = message_delta("venid", when=at(12), mention_everyone=True, people_mentioned=5)
    assert delta.add["msg_everyone"] == 1
    assert delta.add["msg_mass_ping"] == 1
    assert "msg_mass_ping" not in message_delta("hola", when=at(12), people_mentioned=4).add


@pytest.mark.parametrize(
    ("when", "stat"),
    [
        (at(16), "msg_siesta"),
        (at(10), "msg_office"),  # martes por la mañana
        (datetime(2026, 3, 14, 12, tzinfo=TIMEZONE), "msg_weekend"),  # sábado
        (at(0, 0), "msg_cinderella"),
        (at(12, month=1, day=6), "msg_reyes"),
        (at(12, month=2, day=14), "msg_valentin"),
        (at(12, month=9, day=8), "msg_pino"),
        (at(12, month=10, day=12), "msg_hispanidad"),
        (at(12, month=12, day=28), "msg_inocentes"),
        (datetime(2026, 2, 13, 12, tzinfo=TIMEZONE), "msg_friday13"),
    ],
)
def test_fechas_nuevas(when: datetime, stat: str) -> None:
    assert message_delta("hola", when=when).add.get(stat) == 1


# -- Conversación ----------------------------------------------------------------------


def observe(tracker: ChatTracker, author: int, *, t: float, text: str = "hola", **kw: object):
    local = datetime.fromtimestamp(t, TIMEZONE)
    return tracker.observe(
        GUILD,
        kw.pop("channel", 100),  # type: ignore[arg-type]
        author,
        at=t,
        local=local,
        content=text,
        laughed=bool(kw.pop("laughed", False)),
    )


T0 = at(10).timestamp()


def test_monologo_y_mensajes_del_dia() -> None:
    tracker = ChatTracker()
    for i in range(4):
        out = observe(tracker, 1, t=T0 + i)
    assert out[1].peak["msg_monologue_max"] == 4
    assert out[1].peak["msg_day_max"] == 4
    out = observe(tracker, 2, t=T0 + 10)
    assert "msg_monologue_max" not in out[2].peak


def test_abre_la_persiana_una_vez_al_dia() -> None:
    tracker = ChatTracker()
    assert observe(tracker, 1, t=T0)[1].add["msg_first_of_day"] == 1
    assert "msg_first_of_day" not in observe(tracker, 2, t=T0 + 5)[2].add
    # Antes de las 6:00 del día siguiente aún no abre nadie.
    early = at(5, month=3, day=11).timestamp()
    assert "msg_first_of_day" not in observe(tracker, 2, t=early)[2].add
    later = at(7, month=3, day=11).timestamp()
    assert observe(tracker, 3, t=later)[3].add["msg_first_of_day"] == 1


def test_eco_de_bancada_y_nigromante() -> None:
    tracker = ChatTracker()
    observe(tracker, 1, t=T0, text="Viva Canarias")
    assert observe(tracker, 2, t=T0 + 1, text="viva canarias ")[2].add["msg_echo"] == 1
    later = T0 + 25 * 3600
    assert observe(tracker, 3, t=later)[3].add["msg_necro"] == 1


def test_cadena_de_risas_suma_a_todos_los_que_se_rien() -> None:
    tracker = ChatTracker()
    observe(tracker, 1, t=T0, laughed=True)
    observe(tracker, 2, t=T0 + 1, laughed=True)
    out = observe(tracker, 3, t=T0 + 2, laughed=True)
    assert {user: d.peak["laugh_chain_max"] for user, d in out.items()} == {1: 3, 2: 3, 3: 3}
    # Un mensaje sin risa corta la cadena.
    observe(tracker, 4, t=T0 + 3)
    out = observe(tracker, 1, t=T0 + 4, laughed=True)
    assert "laugh_chain_max" not in out[1].peak


def test_la_memoria_de_canales_esta_acotada() -> None:
    tracker = ChatTracker()
    for channel in range(600):
        observe(tracker, 1, t=T0 + channel, channel=channel)
    assert len(tracker._channels) == 500


# -- Voz -------------------------------------------------------------------------------


def test_entrar_cambiar_irse_rapido_y_compartir_pantalla() -> None:
    def move(before: int | None, after: int | None, **kw: object) -> dict[str, int]:
        return voice_move_stats(
            before_channel=before,
            after_channel=after,
            started_stream=bool(kw.get("stream", False)),
            joined_at=kw.get("joined_at"),  # type: ignore[arg-type]
            now=100.0,
        ).add

    assert move(None, 1) == {"voice_joins": 1}
    assert move(1, 2) == {"voice_hops": 1}
    assert move(1, 1, stream=True) == {"voice_stream_starts": 1}
    assert move(1, None, joined_at=90.0) == {"voice_ghost": 1}
    assert move(1, None, joined_at=10.0) == {}


def voice_member(user_id: int, **flags: bool) -> SimpleNamespace:
    state = {
        "self_mute": False,
        "self_deaf": False,
        "mute": False,
        "deaf": False,
        "self_stream": False,
        "self_video": False,
        **flags,
    }
    return SimpleNamespace(id=user_id, bot=False, voice=SimpleNamespace(**state))


async def make_cog(tmp_path: Path, *guilds: object) -> Achievements:
    repository = AchievementRepository(tmp_path / "bot.db")
    await repository.initialize()
    bot = MagicMock()
    bot.guilds = list(guilds)
    by_id = {g.id: g for g in guilds}  # type: ignore[attr-defined]
    bot.get_guild = lambda guild_id: by_id.get(guild_id)
    bot.get_cog = lambda _name: None
    bot.user = SimpleNamespace(id=999)
    return Achievements(bot, repository)


async def test_voz_cuenta_multitarea_cine_y_mordaza(tmp_path: Path) -> None:
    channel = SimpleNamespace(
        id=70,
        members=[
            voice_member(1, self_stream=True, self_video=True),
            voice_member(2, mute=True),
            *(voice_member(i) for i in range(3, 6)),
        ],
    )
    guild = SimpleNamespace(id=GUILD, voice_channels=[channel], afk_channel=None)
    cog = await make_cog(tmp_path, guild)
    sunday_siesta = datetime(2026, 3, 15, 16, tzinfo=TIMEZONE).timestamp()
    cog.collect_voice(sunday_siesta)

    streamer = cog._pending[GUILD][1].add
    assert streamer["voice_multitask"] == 1
    assert streamer["voice_stream_crowd"] == 1
    assert streamer["voice_siesta"] == 1
    assert streamer["voice_weekend"] == 1
    assert "voice_duo" not in streamer
    assert cog._pending[GUILD][2].add["voice_server_muted"] == 1


async def test_la_racha_de_silencio_se_corta_al_hablar(tmp_path: Path) -> None:
    quiet = voice_member(1, self_mute=True)
    channel = SimpleNamespace(id=70, members=[quiet, voice_member(2)])
    guild = SimpleNamespace(id=GUILD, voice_channels=[channel], afk_channel=None)
    cog = await make_cog(tmp_path, guild)
    noon = at(12).timestamp()
    cog.collect_voice(noon)
    cog.collect_voice(noon + 60)
    assert cog._mute_streaks[(GUILD, 1)] == 2
    quiet.voice.self_mute = False
    cog.collect_voice(noon + 120)
    assert (GUILD, 1) not in cog._mute_streaks


async def test_voz_suma_entradas_y_huidas(tmp_path: Path) -> None:
    guild = SimpleNamespace(id=GUILD)
    cog = await make_cog(tmp_path, guild)
    clock = iter([0.0, 0.0, 5.0])
    cog._clock = lambda: next(clock)
    member = SimpleNamespace(id=7, bot=False, guild=guild)
    out = SimpleNamespace(channel=None, self_stream=False)
    inside = SimpleNamespace(channel=SimpleNamespace(id=70), self_stream=False)

    await cog.on_voice_state_update(member, out, inside)  # type: ignore[arg-type]
    await cog.on_voice_state_update(member, inside, out)  # type: ignore[arg-type]

    assert cog._pending[GUILD][7].add == {"voice_joins": 1, "voice_ghost": 1}


# -- Mensajes y reacciones en el cog --------------------------------------------------


def fake_message(
    author_id: int,
    content: str,
    *,
    channel_id: int = 100,
    replied: object = None,
) -> SimpleNamespace:
    reference = SimpleNamespace(resolved=replied) if replied is not None else None
    return SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        author=SimpleNamespace(id=author_id, bot=False),
        webhook_id=None,
        is_system=lambda: False,
        created_at=at(12),
        reference=reference,
        type=discord.MessageType.reply if replied is not None else discord.MessageType.default,
        mentions=[],
        mention_everyone=False,
        attachments=[],
        stickers=[],
        content=content,
        channel=SimpleNamespace(id=channel_id),
    )


async def test_reirse_respondiendo_suma_al_gracioso(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    joke = SimpleNamespace(author=SimpleNamespace(id=2, bot=False))
    await cog.on_message(fake_message(1, "JAJAJAJA", replied=joke))  # type: ignore[arg-type]
    assert cog._pending[GUILD][1].add["laugh_replies"] == 1
    assert cog._pending[GUILD][2].add["laughs_caused"] == 1


async def test_reir_con_racha_de_derrotas_es_reir_por_no_llorar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog._casino_streaks[(GUILD, 1)] = -5
    await cog.on_message(fake_message(1, "jajaja me arruiné"))  # type: ignore[arg-type]
    assert cog._pending[GUILD][1].add["laugh_losing"] == 1


async def test_editar_un_mensaje_cuenta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    before = fake_message(1, "hola")
    after = fake_message(1, "hola!!")
    await cog.on_message_edit(before, after)  # type: ignore[arg-type]
    await cog.on_message_edit(after, after)  # type: ignore[arg-type]
    assert cog._pending[GUILD][1].add == {"msg_edits": 1}


async def test_reacciones_de_risa_una_por_persona(tmp_path: Path) -> None:
    guild = SimpleNamespace(id=GUILD, get_member=lambda uid: SimpleNamespace(id=uid, bot=False))
    cog = await make_cog(tmp_path, guild)

    def react(user_id: int, emoji: str) -> SimpleNamespace:
        return SimpleNamespace(
            guild_id=GUILD,
            message_id=7,
            user_id=user_id,
            message_author_id=1,
            channel_id=50,
            member=SimpleNamespace(id=user_id, bot=False),
            emoji=SimpleNamespace(name=emoji),
        )

    for user_id, emoji in ((2, "😂"), (2, "💀"), (3, "kekw"), (4, "👍")):
        await cog.on_raw_reaction_add(react(user_id, emoji))  # type: ignore[arg-type]

    author = cog._pending[GUILD][1]
    assert author.add["laugh_reacts_received"] == 2
    assert author.peak["laugh_reacts_on_message_max"] == 2
    assert cog._pending[GUILD][2].add["laugh_reacts_given"] == 1
    assert "laugh_reacts_given" not in cog._pending[GUILD][4].add


# -- Música, imágenes y babel ---------------------------------------------------------


def test_poner_musica_y_sus_secretos() -> None:
    delta = music_queue_stats(
        query="pedro pedro pedro", title="Pedro (Remix)", duration_seconds=1_500, position=11
    )
    assert delta.add == {"music_queued": 1, "music_pedro": 1}
    assert delta.peak == {"music_track_max": 25, "music_queue_max": 11}
    assert (
        "music_jovani"
        in music_queue_stats(query="jovani vázquez", title="x", duration_seconds=60, position=1).add
    )


def test_volumen_de_la_musica() -> None:
    assert music_volume_stats(200).peak["music_volume_max"] == 200
    assert music_volume_stats(5).add["music_whisper"] == 1
    assert "music_whisper" not in music_volume_stats(50).add


def test_imagenes_cuentan_efectos_distintos() -> None:
    first = image_stats("magik", "png", subject_is_author=True)
    assert first.add == {"img_made": 1, "img_fx_magik": 1, "img_magik": 1, "img_self": 1}
    other = image_stats("ataud", "mp4", subject_is_author=False)
    assert other.add["img_video"] == 1
    assert other.add["img_on_others"] == 1
    stats = {"img_fx_magik": 3, "img_fx_ataud": 1, "img_made": 4}
    assert with_derived(stats)[IMG_EFFECTS_STAT] == 2


def test_babel_frase_nombres_y_vueltas() -> None:
    assert babel_stats(completed=True, lost=False).add == {"babel_phrases": 1, "babel_full": 1}
    names = babel_stats(renamed_members=2, renamed_channels=1, completed=False, lost=True)
    assert names.add == {"babel_renames": 2, "babel_channels": 1, "babel_lost": 1}


# -- Coleccionista ---------------------------------------------------------------------


def test_meta_stats_cuenta_rarezas_secretos_y_categorias() -> None:
    ids = ["chat_1", "chat_100", "leet", "laugh_1"]
    stats = meta_stats(ids)
    assert stats["achievements"] == 4
    assert stats["achievements_secret"] == 1
    assert stats["achievements_rare"] == 1  # «1337»
    assert stats["achievements_categories"] == 3  # chat, time y laughs
    assert stats["achievement_points"] == sum(BY_ID[i].rarity.points for i in ids)


def test_completar_una_categoria() -> None:
    todo = [a.id for a in AVAILABLE if a.category == "todo"]
    assert meta_stats(todo)["achievements_categories_done"] == 1
    assert meta_stats(todo[:-1])["achievements_categories_done"] == 0


def test_combo_al_desbloquear_varios_de_golpe() -> None:
    new = newly_unlocked({"messages": 100, "msg_laughs": 10}, [])
    assert {"chat_1", "chat_100", "laugh_1", "laugh_10"} <= set(new)
    assert "meta_combo_3" in new


def test_logro_de_logros_de_logros_salta_con_el_ultimo_de_coleccionista() -> None:
    metas = [a.id for a in AVAILABLE if a.category == "meta" and a.id != "metacompletionist"]
    assert "metacompletionist" in newly_unlocked({}, metas)
    assert "metacompletionist" not in newly_unlocked({}, metas[:-1])


# -- Menú ------------------------------------------------------------------------------


def test_chat_y_voz_son_grupos_con_secciones() -> None:
    chat = {c.key for c in group_sections(CHAT_GROUP.key)}
    voice = {c.key for c in group_sections(VOICE_GROUP.key)}
    assert {"chat", "style", "laughs", "funny", "lengua", "convo", "time", "memes"} == chat
    assert {"voice", "voice_mic", "voice_moves", "music"} == voice
    for group in (CHAT_GROUP, VOICE_GROUP):
        embed = group_embed(group.key, "Diego", Profile(stats={}, unlocked={}))
        assert embed.description is not None
        assert len(embed.description) <= 4096


def test_no_hay_categorias_vacias() -> None:
    for category in CATEGORIES:
        assert any(a.category == category.key for a in AVAILABLE), category.key


def test_la_rareza_no_baja_al_subir_la_meta() -> None:
    """Dentro de una estadística, un escalón más alto nunca es más común."""
    order = list(Rarity)
    by_stat: dict[str, list[Achievement]] = {}
    for achievement in AVAILABLE:
        if len(achievement.conditions) == 1:
            by_stat.setdefault(achievement.stat, []).append(achievement)
    for tiers in by_stat.values():
        ranks = [order.index(a.rarity) for a in tiers]
        assert ranks == sorted(ranks), [a.id for a in tiers]


def test_las_categorias_largas_se_parten_en_paginas_sin_perder_logros() -> None:
    from bot.cogs.achievements import category_embed, category_page_count

    profile = Profile(stats={}, unlocked={})
    category = next(c for c in CATEGORIES if c.key == "slots")
    pages = category_page_count(category, profile, {}, 10)
    assert pages >= 2
    text = "".join(
        category_embed(category, "Diego", profile, {}, 10, page).description or ""
        for page in range(pages)
    )
    visible = [a for a in AVAILABLE if a.category == "slots" and not a.secret]
    assert all(a.name in text for a in visible)
    last = category_embed(category, "Diego", profile, {}, 10, 99)
    assert f"Página {pages}/{pages}" in (last.footer.text or "")


# -- Segunda tanda: más logros del casino y del resto ----------------------------------


def _roulette(pocket: int, *bets: str):
    from bot.services import roulette

    wheel = roulette.Wheel(lambda _n: roulette.POCKETS.index(pocket), lightning=False)
    wagers = [roulette.Wager(roulette.parse_bet(bet), 100) for bet in bets]
    return roulette.play_round(wheel, wagers)


def test_ruleta_cuenta_plenos_por_numero_y_victorias_pirricas() -> None:
    from bot.services.achievements import roulette_stats

    outcome = _roulette(13, "13", "rojo", "negro", "1-18", "docena2")
    delta = roulette_stats(outcome, table_streak=1, previous_pocket=None)
    assert delta.add["roulette_hit_13"] == 1
    assert delta.add["roulette_half_wins"] == 1  # 1-18
    assert delta.add["roulette_dozen_wins"] == 1
    assert delta.peak["roulette_cover_max"] == 36
    stats = with_derived({"roulette_hit_13": 2, "roulette_hit_7": 1})
    assert stats["roulette_numbers_hit"] == 2
    assert stats["roulette_hit_max"] == 2

    pyrrhic = roulette_stats(
        _roulette(1, "rojo", "19-36", "docena3"), table_streak=0, previous_pocket=None
    )
    assert pyrrhic.add["roulette_pyrrhic"] == 1


def test_ruleta_el_cero_barre_la_mesa() -> None:
    from bot.services.achievements import roulette_stats

    delta = roulette_stats(_roulette(0, "rojo", "negro"), table_streak=0, previous_pocket=None)
    assert delta.add["roulette_zero_sweep"] == 1


def _blackjack(player: list[tuple[int, int]], dealer: list[tuple[int, int]], hits: int = 0):
    from bot.services import blackjack as bj

    # Reparto: jugador, banca, jugador, banca; luego las cartas que se pidan
    # y al final las de la banca. El mazo se roba desde el final.
    order = [player[0], dealer[0], player[1], dealer[1], *player[2:], *dealer[2:]]
    shoe = [bj.Card(rank, suit) for rank, suit in reversed(order)]
    game = bj.BlackjackGame(100, shoe=shoe)
    game.deal()
    for _ in range(hits):
        if game.player_turn:
            game.act(bj.Action.HIT)
    while game.player_turn:
        game.act(bj.Action.STAND)
    game.reveal_hole()
    while game.dealer_should_draw():
        game.dealer_draw()
    game.settle()
    return game


def test_blackjack_tres_sietes_y_blackjack_del_mismo_palo() -> None:
    from bot.services.achievements import blackjack_stats

    sevens = blackjack_stats(_blackjack([(7, 0), (7, 1), (7, 2)], [(10, 0), (7, 0)], hits=1))
    assert sevens.add["bj_triple_seven"] == 1
    suited = blackjack_stats(_blackjack([(1, 2), (13, 2)], [(9, 0), (8, 0)]))
    assert suited.add["bj_suited_natural"] == 1


def test_blackjack_plantarse_con_once_y_la_banca_que_se_lo_curra() -> None:
    from bot.services.achievements import blackjack_stats

    game = _blackjack([(5, 0), (6, 0)], [(2, 0), (2, 1), (2, 2), (3, 0), (10, 0)])
    delta = blackjack_stats(game)
    assert delta.add["bj_stand_low"] == 1
    assert delta.add["bj_dealer_five"] == 1


def test_minas_cuenta_niveles_cobrar_con_uno_y_la_avaricia() -> None:
    import random

    from bot.services import mines
    from bot.services.achievements import mines_stats

    game = mines.MinesGame.new(100, 3, random.Random(1))
    game.reveal(game.random_hidden(random.Random(2)))
    game.cash_out()
    delta = mines_stats(game)
    assert delta.add["mines_level_3"] == 1
    assert delta.add["mines_cash_one"] == 1


def test_crash_cobarde_y_cohete_perdido() -> None:
    from bot.services.achievements import crash_stats

    seat = SimpleNamespace(cashed_cents=105, by_auto=False, net=5)
    delta = crash_stats(seat, crash_cents=12_000, players=1, last_out=False)  # type: ignore[arg-type]
    assert delta.add["crash_cash_low"] == 1
    assert delta.add["crash_missed_moon"] == 1


def test_apuestas_secretas_del_casino() -> None:
    from bot.services.achievements import casino_stats

    for stake in (1, 69, 777):
        assert (
            casino_stats(stake=stake, net=-stake, balance_after=1_000).add[f"casino_bet_{stake}"]
            == 1
        )


def test_loterias_cuentan_por_juego() -> None:
    from bot.services.achievements import lottery_buy_stats

    delta = lottery_buy_stats(game="bonoloto", units=3, cost=15, owned_in_draw=3, balance_after=100)
    assert delta.add["lottery_game_bonoloto"] == 3


def test_no_hay_dos_logros_con_el_mismo_nombre() -> None:
    names = [a.name for a in CATALOG]
    assert len(names) == len(set(names))
