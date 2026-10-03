"""Pruebas de bot.cogs.fun: el comando `babel`."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.fun import MAX_TEXT_LENGTH, Fun, _format_route, build_result_embed
from bot.services.babel import TOTAL_HOPS, BabelResult
from bot.utils.responder import CommandResponder


class FakeResponder(CommandResponder):
    """Responder que registra cada envío, sin tocar Discord."""

    def __init__(self) -> None:
        self.guild = SimpleNamespace(id=1)
        self.member = None
        self.channel = MagicMock()
        self.errors: list[str] = []
        self.progress: list[str] = []
        self.finished: list[dict[str, object]] = []

    async def send(self, content=None, **kwargs) -> None:  # noqa: ANN001
        raise AssertionError("babel no debe usar send")

    async def send_error(self, content: str) -> None:
        self.errors.append(content)

    async def start_progress(self, placeholder: str = "", **kwargs) -> None:  # noqa: ANN003
        self.progress.append(placeholder)

    async def update_progress(self, content: str) -> None:
        self.progress.append(content)

    async def finish(self, content=None, **kwargs) -> None:  # noqa: ANN001
        self.finished.append({"content": content, **kwargs})


async def echo_translator(text: str, source: str, target: str) -> str:
    return f"{text}·{target}"


@pytest.mark.asyncio
async def test_babel_da_100_vueltas_y_muestra_antes_y_despues() -> None:
    cog = Fun(MagicMock(), translator=echo_translator)
    responder = FakeResponder()

    await cog._babel_impl(responder, "  el gato come pescado  ")

    assert not responder.errors
    embed = responder.finished[0]["embed"]
    assert isinstance(embed, discord.Embed)
    assert embed.fields[0].value == "el gato come pescado"
    assert embed.title.startswith(f"🗼 {TOTAL_HOPS} traducciones")
    allowed = responder.finished[0]["allowed_mentions"]
    assert isinstance(allowed, discord.AllowedMentions)
    assert not allowed.everyone and not allowed.users
    # Aviso inicial + una actualización cada 10 vueltas (sin la última).
    assert len(responder.progress) == 1 + (TOTAL_HOPS // 10 - 1)


@pytest.mark.asyncio
async def test_babel_rechaza_texto_vacio_y_demasiado_largo() -> None:
    translator = AsyncMock()
    cog = Fun(MagicMock(), translator=translator)
    responder = FakeResponder()

    await cog._babel_impl(responder, "   ")
    await cog._babel_impl(responder, "a" * (MAX_TEXT_LENGTH + 1))

    assert len(responder.errors) == 2
    translator.assert_not_awaited()


@pytest.mark.asyncio
async def test_babel_no_admite_dos_tiradas_a_la_vez() -> None:
    release = asyncio.Event()

    async def slow_translator(text: str, source: str, target: str) -> str:
        await release.wait()
        return text

    cog = Fun(MagicMock(), translator=slow_translator)
    first, second = FakeResponder(), FakeResponder()

    running = asyncio.create_task(cog._babel_impl(first, "hola"))
    await asyncio.sleep(0)
    await cog._babel_impl(second, "adiós")
    release.set()
    await running

    assert second.errors and not second.finished
    assert first.finished


@pytest.mark.asyncio
async def test_babel_text_sin_texto_usa_el_mensaje_al_que_responde() -> None:
    cog = Fun(MagicMock(), translator=echo_translator)
    replied = MagicMock(spec=discord.Message)
    replied.clean_content = "texto citado"
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=1)
    ctx.author = None
    ctx.message.reference.resolved = replied
    progress = MagicMock()
    progress.edit = AsyncMock()
    ctx.send = AsyncMock(return_value=progress)

    await cog.babel_text.callback(cog, ctx)

    embed = progress.edit.await_args_list[-1].kwargs["embed"]
    assert embed.fields[0].value == "texto citado"


def test_embed_avisa_si_la_cadena_se_corto_lejos_del_espanol() -> None:
    result = BabelResult("hola", "こんにちは", "ja", ("es", "ja"), stopped_early=True)
    embed = build_result_embed(result)

    assert "japonés" in embed.fields[1].name
    assert embed.footer.text


def test_ruta_larga_se_recorta_conservando_principio_y_final() -> None:
    route = ("es", *(["mni-Mtei"] * 150), "zu", "es")
    text = _format_route(route)

    assert len(text) <= 1024
    assert text.startswith("es → ")
    assert text.endswith("zu → es")
    assert " → … → " in text


# --- Modo nombres --------------------------------------------------------------

OWNER_ID = 999
BOT_ID = 500


def make_member(
    member_id: int, name: str, *, role: int = 1, perms: discord.Permissions | None = None
) -> MagicMock:
    """Miembro simulado: `top_role` es un número para comparar jerarquías."""
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.display_name = name
    member.top_role = role
    member.guild_permissions = perms or discord.Permissions.none()
    member.edit = AsyncMock()
    member.__str__ = lambda self: name
    return member


def make_channel(channel_id: int, name: str, *, invoker_can: bool = True) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.edit = AsyncMock()
    channel.permissions_for = lambda who: (
        discord.Permissions(manage_channels=True)
        if invoker_can or who.id == BOT_ID
        else discord.Permissions.none()
    )
    return channel


class GuildResponder(FakeResponder):
    """Responder dentro de un servidor con miembros y canales simulados."""

    def __init__(self, invoker: MagicMock, members: list[MagicMock], channels: list[MagicMock]):
        super().__init__()
        bot_member = make_member(
            BOT_ID,
            "Bot",
            role=50,
            perms=discord.Permissions(manage_nicknames=True, change_nickname=True),
        )
        by_id = {m.id: m for m in [invoker, bot_member, *members]}
        guild = MagicMock()
        guild.owner_id = OWNER_ID
        guild.me = bot_member
        guild.get_member = by_id.get
        guild.get_channel = {c.id: c for c in channels}.get
        guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(), "x"))
        for member in by_id.values():
            member.guild = guild
        self.guild = guild
        self.member = invoker


async def upper_translator(text: str, source: str, target: str) -> str:
    """Traductor que conserva las líneas y deja huella reconocible al volver."""
    return text.upper() if target == "es" else text


MOD = discord.Permissions(manage_nicknames=True, change_nickname=True)


@pytest.mark.asyncio
async def test_babel_con_menciones_renombra_miembros_y_canales() -> None:
    invoker = make_member(1, "Diego", role=10, perms=MOD)
    ana = make_member(2, "Ana", role=5)
    channel = make_channel(30, "🎮・chat-general")
    responder = GuildResponder(invoker, [ana], [channel])
    cog = Fun(MagicMock(), translator=upper_translator)

    await cog._babel_impl(responder, "<@2> <#30> <@2>")

    assert not responder.errors
    ana.edit.assert_awaited_once()
    assert ana.edit.await_args.kwargs["nick"] == "ANA"
    # El adorno se conserva y los guiones se traducen como espacios.
    assert channel.edit.await_args.kwargs["name"] == "🎮・CHAT GENERAL"
    embed = responder.finished[0]["embed"]
    assert embed.fields[0].name == "Bautizos"


@pytest.mark.asyncio
async def test_sin_gestionar_apodos_solo_puedes_babelizarte_a_ti() -> None:
    invoker = make_member(1, "Diego", role=10, perms=discord.Permissions(change_nickname=True))
    ana = make_member(2, "Ana", role=5)
    responder = GuildResponder(invoker, [ana], [])
    cog = Fun(MagicMock(), translator=upper_translator)

    await cog._babel_impl(responder, "<@1> <@2>")

    invoker.edit.assert_awaited_once()
    ana.edit.assert_not_awaited()
    failures = responder.finished[0]["embed"].fields[1].value
    assert "Gestionar apodos" in failures


@pytest.mark.asyncio
async def test_no_se_toca_a_roles_iguales_o_superiores_ni_al_dueno() -> None:
    invoker = make_member(1, "Diego", role=10, perms=MOD)
    jefe = make_member(2, "Jefe", role=10)
    owner = make_member(OWNER_ID, "Dueño", role=1)
    responder = GuildResponder(invoker, [jefe, owner], [])
    cog = Fun(MagicMock(), translator=AsyncMock())

    await cog._babel_impl(responder, "<@2> <@999>")

    assert responder.errors and not responder.finished
    assert "rol igual o superior" in responder.errors[0]
    assert "dueño" in responder.errors[0]


@pytest.mark.asyncio
async def test_canal_sin_gestionar_canales_no_se_renombra() -> None:
    invoker = make_member(1, "Diego", role=10)
    channel = make_channel(30, "general", invoker_can=False)
    responder = GuildResponder(invoker, [], [channel])
    cog = Fun(MagicMock(), translator=AsyncMock())

    await cog._babel_impl(responder, "<#30>")

    assert "Gestionar canales" in responder.errors[0]
    channel.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_un_canal_no_se_renombra_mas_de_dos_veces_en_diez_minutos() -> None:
    invoker = make_member(1, "Diego", role=10)
    channel = make_channel(30, "general")
    cog = Fun(MagicMock(), translator=upper_translator)

    for _ in range(3):
        await cog._babel_impl(GuildResponder(invoker, [], [channel]), "<#30>")

    assert channel.edit.await_count == 2


@pytest.mark.asyncio
async def test_si_la_cadena_no_vuelve_al_espanol_no_se_renombra_nada() -> None:
    from bot.services.babel import RateLimitedError

    async def blocked(text: str, source: str, target: str) -> str:
        raise RateLimitedError("429")

    invoker = make_member(1, "Diego", role=10, perms=MOD)
    responder = GuildResponder(invoker, [], [])
    cog = Fun(MagicMock(), translator=blocked)

    await cog._babel_impl(responder, "<@1>")

    invoker.edit.assert_not_awaited()
    assert "no he tocado" in responder.finished[0]["embed"].fields[0].value


@pytest.mark.asyncio
async def test_frase_con_mencion_y_texto_traduce_el_nombre_como_palabra() -> None:
    invoker = make_member(1, "Diego", role=10)
    ana = make_member(2, "Ana")
    responder = GuildResponder(invoker, [ana], [])
    cog = Fun(MagicMock(), translator=echo_translator)

    await cog._babel_impl(responder, "<@2> come queso")

    ana.edit.assert_not_awaited()
    assert responder.finished[0]["embed"].fields[0].value == "Ana come queso"


@pytest.mark.asyncio
async def test_babel_fuera_de_un_servidor_no_renombra() -> None:
    responder = FakeResponder()
    responder.guild = None
    cog = Fun(MagicMock(), translator=AsyncMock())

    await cog._babel_impl(responder, "<@2>")

    assert "servidor" in responder.errors[0]
