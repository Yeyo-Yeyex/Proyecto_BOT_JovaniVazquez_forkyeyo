"""Pruebas del cog de niveles: XP por voz, por reacciones y premio al subir."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.message_stats import MessageStats, is_voice_active
from bot.repositories.economy import EconomyRepository
from bot.repositories.message_stats import MessageStatsRepository
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import REACTION_XP, MemberActivity

GUILD = 10


def voice_member(user_id: int, *, bot: bool = False, **flags: bool) -> SimpleNamespace:
    state = {"self_mute": False, "self_deaf": False, "mute": False, "deaf": False, **flags}
    return SimpleNamespace(
        id=user_id, bot=bot, display_name=f"m{user_id}", voice=SimpleNamespace(**state)
    )


async def make_cog(tmp_path: Path) -> tuple[MessageStats, MessageStatsRepository, EconomyService]:
    repository = MessageStatsRepository(tmp_path / "bot.db")
    await repository.initialize()
    assert await repository.start_import(GUILD, [20], cutoff_id=500)
    await repository.save_channel_counts(GUILD, 20, {99: 1})
    await repository.finish_import(GUILD)
    await repository.enable_levels(GUILD, historical_xp_per_message=20)
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    economy = EconomyService(economy_repository)
    return MessageStats(MagicMock(), repository, economy), repository, economy


def voice_channel(*members: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(id=500, members=list(members), send=AsyncMock())


GUILD_OBJ = SimpleNamespace(id=GUILD, afk_channel=None)


def test_mute_o_ensordecido_no_cuentan_para_voz() -> None:
    assert is_voice_active(voice_member(1))  # type: ignore[arg-type]
    assert not is_voice_active(voice_member(1, self_mute=True))  # type: ignore[arg-type]
    assert not is_voice_active(voice_member(1, self_deaf=True))  # type: ignore[arg-type]
    assert not is_voice_active(voice_member(1, mute=True))  # type: ignore[arg-type]
    assert not is_voice_active(voice_member(1, bot=True))  # type: ignore[arg-type]


async def test_voz_da_xp_solo_a_quien_habla_con_alguien_mas(tmp_path: Path) -> None:
    cog, repository, _ = await make_cog(tmp_path)
    channel = voice_channel(
        voice_member(1), voice_member(2), voice_member(3, self_mute=True), voice_member(4, bot=True)
    )

    await cog._award_voice_channel(GUILD_OBJ, channel, now=1_000_000)  # type: ignore[arg-type]

    assert await repository.member_xp(GUILD, 1) > 0
    assert await repository.member_xp(GUILD, 2) > 0
    assert await repository.member_xp(GUILD, 3) == 0
    assert await repository.member_xp(GUILD, 4) == 0


async def test_solo_en_voz_no_da_xp(tmp_path: Path) -> None:
    cog, repository, _ = await make_cog(tmp_path)
    channel = voice_channel(voice_member(1), voice_member(2, self_deaf=True))

    await cog._award_voice_channel(GUILD_OBJ, channel, now=1_000_000)  # type: ignore[arg-type]

    assert await repository.member_xp(GUILD, 1) == 0


async def test_canal_afk_no_da_xp(tmp_path: Path) -> None:
    cog, repository, _ = await make_cog(tmp_path)
    channel = voice_channel(voice_member(1), voice_member(2))
    guild = SimpleNamespace(id=GUILD, afk_channel=channel)

    await cog._award_voice_channel(guild, channel, now=1_000_000)  # type: ignore[arg-type]

    assert await repository.member_xp(GUILD, 1) == 0


async def test_subir_de_nivel_paga_yapdollars_y_lo_anuncia(tmp_path: Path) -> None:
    cog, repository, economy = await make_cog(tmp_path)

    def almost_level_one(_u: int, state: MemberActivity, _c: int) -> MemberActivity:
        return replace(state, total_xp=99)

    await repository.grant_activity(GUILD, [1, 2], almost_level_one)
    channel = voice_channel(voice_member(1), voice_member(2))

    await cog._award_voice_channel(GUILD_OBJ, channel, now=1_000_000)  # type: ignore[arg-type]

    assert await economy.balance(GUILD, 1) == STARTING_BALANCE + 100
    assert channel.send.await_count == 2
    text = channel.send.await_args.args[0]
    assert "nivel **1**" in text
    assert "+100 Y$" in text
    assert "Perro Sanxe" in text


def reaction(
    *, message_id: int = 7, user_id: int = 2, author_id: int = 1, bot: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        guild_id=GUILD,
        message_id=message_id,
        user_id=user_id,
        message_author_id=author_id,
        channel_id=500,
        member=SimpleNamespace(bot=bot),
    )


@pytest.fixture
def guild_with_members() -> SimpleNamespace:
    members = {1: SimpleNamespace(id=1, bot=False), 9: SimpleNamespace(id=9, bot=True)}
    return SimpleNamespace(
        id=GUILD,
        get_member=members.get,
        get_channel_or_thread=lambda _id: None,
    )


async def test_reaccion_de_otro_da_xp_una_sola_vez(
    tmp_path: Path, guild_with_members: SimpleNamespace
) -> None:
    cog, repository, _ = await make_cog(tmp_path)
    cog.bot.get_guild = lambda _id: guild_with_members

    await cog.on_raw_reaction_add(reaction())  # type: ignore[arg-type]
    await cog.on_raw_reaction_add(reaction())  # type: ignore[arg-type]

    assert await repository.member_xp(GUILD, 1) == REACTION_XP


async def test_reacciones_propias_o_a_bots_no_dan_xp(
    tmp_path: Path, guild_with_members: SimpleNamespace
) -> None:
    cog, repository, _ = await make_cog(tmp_path)
    cog.bot.get_guild = lambda _id: guild_with_members

    await cog.on_raw_reaction_add(reaction(user_id=1))  # type: ignore[arg-type]
    await cog.on_raw_reaction_add(reaction(author_id=9))  # type: ignore[arg-type]
    await cog.on_raw_reaction_add(reaction(bot=True))  # type: ignore[arg-type]

    assert await repository.member_xp(GUILD, 1) == 0
