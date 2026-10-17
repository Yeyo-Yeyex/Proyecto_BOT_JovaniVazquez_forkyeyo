"""Pruebas del panel `admin` (bot.cogs.moderation) y de sus repositorios."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs.moderation import AdminPanel, Moderation, last_active
from bot.repositories.activity import ActivityRepository, LastSeen
from bot.repositories.moderation import ModerationRepository

GUILD_ID = 7


@pytest.fixture
async def activity(tmp_path: Path) -> ActivityRepository:
    repo = ActivityRepository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    return repo


@pytest.fixture
async def moderation(tmp_path: Path) -> ModerationRepository:
    repo = ModerationRepository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    return repo


async def test_la_actividad_guardada_no_borra_lo_que_no_viene_en_el_cambio(
    activity: ActivityRepository,
) -> None:
    await activity.save({(GUILD_ID, 1): {"message_at": 100.0, "message_channel": 55}})
    await activity.save({(GUILD_ID, 1): {"voice_at": 200.0, "voice_channel": 66}})

    seen = await activity.guild(GUILD_ID)

    assert seen == {1: LastSeen(100.0, 55, 200.0, 66, None)}
    assert await activity.guild(GUILD_ID + 1) == {}


async def test_la_actividad_rechaza_columnas_desconocidas(activity: ActivityRepository) -> None:
    with pytest.raises(KeyError):
        await activity.save({(GUILD_ID, 1): {"contenido": 1}})


async def test_el_historial_cuenta_sanciones_y_las_da_de_la_mas_nueva_a_la_vieja(
    moderation: ModerationRepository,
) -> None:
    await moderation.record(GUILD_ID, 1, 9, "callar", "spam", 600)
    await moderation.record(GUILD_ID, 1, 9, "echar")
    await moderation.record(GUILD_ID, 2, 9, "banear")

    mine = await moderation.sanctions(GUILD_ID, 1)
    everyone = await moderation.sanctions(GUILD_ID)

    assert [s.kind for s in mine] == ["echar", "callar"]
    assert mine[1].reason == "spam" and mine[1].seconds == 600
    assert [s.kind for s in everyone] == ["banear", "echar", "callar"]
    assert await moderation.sanction_counts(GUILD_ID, 1) == {"callar": 1, "echar": 1}


async def test_las_notas_se_apuntan_y_se_borran_solo_en_su_servidor(
    moderation: ModerationRepository,
) -> None:
    await moderation.add_note(GUILD_ID, 1, 9, "Sospechoso de votar a Junts")
    [note] = await moderation.notes(GUILD_ID, 1)

    assert note.text == "Sospechoso de votar a Junts" and note.author_id == 9
    assert not await moderation.delete_note(GUILD_ID + 1, note.id)
    assert await moderation.delete_note(GUILD_ID, note.id)
    assert await moderation.notes(GUILD_ID, 1) == []


def offline_member(*, voice: object | None = None) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.status = discord.Status.offline
    member.voice = voice
    return member


def test_ultima_actividad_es_ahora_si_esta_conectado_o_en_voz() -> None:
    seen = LastSeen(message_at=10.0)
    online = offline_member()
    online.status = discord.Status.idle

    assert last_active(online, seen, now=99.0) == 99.0
    assert last_active(offline_member(voice=object()), seen, now=99.0) == 99.0


def test_ultima_actividad_es_la_senal_mas_reciente_o_ninguna() -> None:
    seen = LastSeen(message_at=10.0, voice_at=30.0, online_at=20.0)

    assert last_active(offline_member(), seen, now=99.0) == 30.0
    assert last_active(offline_member(), LastSeen(), now=99.0) is None


async def test_los_mensajes_voz_y_presencias_se_juntan_y_se_vuelcan(
    activity: ActivityRepository,
) -> None:
    bot = MagicMock()
    bot.last_seen = activity
    cog = Moderation(bot)
    guild = SimpleNamespace(id=GUILD_ID)
    author = SimpleNamespace(id=1, bot=False)
    message = SimpleNamespace(
        guild=guild, author=author, webhook_id=None, channel=SimpleNamespace(id=55)
    )
    await cog.on_message(message)  # type: ignore[arg-type]
    member = SimpleNamespace(id=1, bot=False, guild=guild)
    room = SimpleNamespace(id=66)
    await cog.on_voice_state_update(
        member,  # type: ignore[arg-type]
        SimpleNamespace(channel=None),  # type: ignore[arg-type]
        SimpleNamespace(channel=room),  # type: ignore[arg-type]
    )
    before = SimpleNamespace(status=discord.Status.online)
    after = SimpleNamespace(status=discord.Status.offline, bot=False, guild=guild, id=1)
    await cog.on_presence_update(before, after)  # type: ignore[arg-type]

    # Antes de volcar ya se ve (memoria encima de la base).
    pending = (await cog.last_seen(GUILD_ID))[1]
    await cog._save()
    saved = (await activity.guild(GUILD_ID))[1]

    assert pending == saved
    assert saved.message_channel == 55 and saved.voice_channel == 66
    assert saved.online_at is not None
    assert cog._pending == {}


async def test_los_bots_no_cuentan_como_actividad() -> None:
    cog = Moderation(MagicMock())
    guild = SimpleNamespace(id=GUILD_ID)
    message = SimpleNamespace(
        guild=guild,
        author=SimpleNamespace(id=1, bot=True),
        webhook_id=None,
        channel=SimpleNamespace(id=55),
    )

    await cog.on_message(message)  # type: ignore[arg-type]

    assert cog._pending == {}


def admin_user(user_id: int, *, admin: bool) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.guild_permissions = discord.Permissions(administrator=admin)
    return user


async def test_el_panel_solo_lo_maneja_quien_lo_abrio_y_si_sigue_siendo_admin() -> None:
    owner = admin_user(1, admin=True)
    panel = AdminPanel(Moderation(MagicMock()), MagicMock(), owner, None)

    assert await panel.interaction_check(fake_interaction(owner))
    assert not await panel.interaction_check(fake_interaction(admin_user(2, admin=True)))
    assert not await panel.interaction_check(fake_interaction(admin_user(1, admin=False)))


def test_el_panel_cabe_en_discord_con_y_sin_miembro() -> None:
    """Cinco filas como mucho y cinco botones por fila, en las dos vistas."""
    member = MagicMock(spec=discord.Member)
    panel = AdminPanel(Moderation(MagicMock()), MagicMock(), admin_user(1, admin=True), member)
    for chosen in (member, None):
        panel.member = chosen
        panel.rebuild()
        rows: dict[int, int] = {}
        for item in panel.children:
            rows[item.row] = rows.get(item.row, 0) + 1  # type: ignore[index]
        assert max(rows) <= 4
        assert all(count <= 5 for count in rows.values())
        assert len({item.custom_id for item in panel.children}) == len(panel.children)  # type: ignore[attr-defined]


async def test_un_boton_de_sancion_acepta_antes_de_llamar_al_cog_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.cogs import moderation as module

    events: list[str] = []
    admin = MagicMock()

    async def unmute(responder: object, member: object) -> None:
        events.append("hablar")

    admin._unmute_impl = unmute
    monkeypatch.setattr(module, "find_cog", lambda bot, cls: admin)
    owner = admin_user(1, admin=True)
    member = MagicMock(spec=discord.Member)
    guild = MagicMock()
    guild.get_member.return_value = member
    cog = Moderation(MagicMock())
    cog.embed = AsyncMock(return_value=discord.Embed())  # type: ignore[method-assign]
    panel = AdminPanel(cog, guild, owner, member)
    interaction = fake_interaction(owner, events=events)

    await panel._unmute(interaction)

    assert events[0] == "response.defer"
    assert "hablar" in events
    assert events[-1] == "edit_original_response"


def fake_member(member_id: int, *, bot: bool = False, online: bool = False) -> MagicMock:
    from datetime import UTC, datetime, timedelta

    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.bot = bot
    member.mention = f"<@{member_id}>"
    member.display_name = f"m{member_id}"
    member.status = discord.Status.online if online else discord.Status.offline
    member.voice = None
    member.nick = None
    member.color = discord.Color.default()
    member.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    member.joined_at = datetime(2026, 1, member_id, tzinfo=UTC)
    member.is_timed_out.return_value = member_id == 2
    member.timed_out_until = datetime.now(UTC) + timedelta(hours=1)
    member.roles = []
    member.display_avatar.url = "https://example.com/a.png"
    return member


async def test_todas_las_vistas_del_panel_se_pintan_dentro_de_los_limites(
    activity: ActivityRepository, moderation: ModerationRepository, tmp_path: Path
) -> None:
    from bot.cogs.moderation import SERVER_VIEWS
    from bot.repositories.message_stats import MessageStatsRepository

    stats = MessageStatsRepository(tmp_path / "bot.sqlite3")
    await stats.initialize()
    bot = SimpleNamespace(last_seen=activity, moderation=moderation, message_stats=stats)
    cog = Moderation(bot)  # type: ignore[arg-type]
    members = [fake_member(1, online=True), fake_member(2), fake_member(3, bot=True)]
    guild = SimpleNamespace(id=GUILD_ID, name="Servidor", members=members)
    for member in members:
        member.guild = guild
    await activity.save({(GUILD_ID, 2): {"message_at": 50.0, "message_channel": 5}})
    await moderation.record(GUILD_ID, 2, 1, "callar", "spam", 600)
    await moderation.add_note(GUILD_ID, 2, 1, "ojo")
    panel = AdminPanel(cog, guild, members[0], None)  # type: ignore[arg-type]

    for key, *_ in SERVER_VIEWS:
        panel.key = key
        embed = await cog.embed(panel)
        assert len(embed) <= 6000, key
    panel.member = members[1]
    ficha = (await cog.embed(panel)).to_dict()

    text = str(ficha)
    assert "<#5>" in text and "ojo" in text and "Aislamiento" in text


async def test_las_listas_incluyen_a_todos_los_miembros_en_paginas(
    activity: ActivityRepository, moderation: ModerationRepository
) -> None:
    from bot.cogs.moderation import PAGE_SIZE, SERVER_VIEWS

    bot = SimpleNamespace(last_seen=activity, moderation=moderation)
    cog = Moderation(bot)  # type: ignore[arg-type]
    members = [fake_member(i % 28 + 1) for i in range(PAGE_SIZE * 2 + 5)]
    for index, member in enumerate(members):
        member.id = 100 + index
        member.mention = f"<@{member.id}>"
    guild = SimpleNamespace(
        id=GUILD_ID, name="Servidor", members=[*members, fake_member(1, bot=True)]
    )
    # Solo uno tiene registro: los demás también salen, detrás.
    await activity.save({(GUILD_ID, 100): {"message_at": 50.0, "message_channel": 5}})
    panel = AdminPanel(cog, guild, members[0], None)  # type: ignore[arg-type]

    for key in ("inactivos", "escritos", "voz", "nuevos"):
        lines = await cog.server_lines(guild, key)  # type: ignore[arg-type]
        assert lines is not None and len(lines) == len(members), key
    assert (await cog.server_lines(guild, "escritos"))[0].startswith("<@100>")  # type: ignore[arg-type,index]

    panel.key = "escritos"
    seen_ids: set[str] = set()
    for page in range(3):
        panel.page = page
        embed = await cog.embed(panel)
        seen_ids.update(line.split(" ")[0] for line in (embed.description or "").splitlines())
    panel.rebuild()

    assert panel.pages == 3
    assert seen_ids == {m.mention for m in members}
    assert any(getattr(item, "custom_id", "") == "admin:siguiente" for item in panel.children)
    assert {key for key, *_ in SERVER_VIEWS} >= {"escritos"}
