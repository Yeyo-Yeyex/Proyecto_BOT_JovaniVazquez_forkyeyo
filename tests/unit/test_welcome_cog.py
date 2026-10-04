"""Pruebas de la bienvenida y la despedida: mensaje, GIF, vuelta y botón 👋."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import welcome
from bot.cogs.welcome import GreetButton, Welcome, load_farewell_insults
from bot.repositories.welcome import WelcomeRepository, WelcomeSettings

GUILD = 456
NEWCOMER = 789
JOINED = 1_000_000.0


def make_member() -> tuple[SimpleNamespace, MagicMock]:
    """Crea un miembro de prueba y el canal #chat-general de su servidor."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 123
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = SimpleNamespace(id=GUILD, text_channels=[channel], get_channel=lambda _id: None)
    member = SimpleNamespace(
        id=NEWCOMER,
        bot=False,
        guild=guild,
        mention=f"<@{NEWCOMER}>",
        display_name="Miembro de prueba",
    )
    return member, channel


@pytest.fixture
def phrases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Archivo de frases con una sola frase por sección, para saber qué sale."""
    path = tmp_path / "bienvenidas.txt"
    path.write_text("# prueba\nHola {usuario}, pasa.\n[vuelta]\n{usuario} ha vuelto.\n", "utf-8")
    monkeypatch.setattr(welcome, "WELCOME_PHRASES_PATH", path)
    return path


@pytest.fixture
def video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "bienvenida.mp4"
    path.write_bytes(b"video de prueba")
    monkeypatch.setattr(welcome, "WELCOME_VIDEO_PATH", path)
    return path


async def make_repository(tmp_path: Path) -> WelcomeRepository:
    repository = WelcomeRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


# -- Entrada ------------------------------------------------------------------------


async def test_sin_ajustes_manda_frase_video_y_mencion_controlada(
    phrases: Path, video: Path
) -> None:
    """Sin base de datos ni GIF se comporta como siempre: vídeo y sin botón."""
    member, channel = make_member()
    cog = Welcome(MagicMock())

    await cog.on_member_join(member)

    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs["content"] == f"Hola {member.mention}, pasa."
    assert kwargs["file"].filename == "bienvenida.mp4"
    assert "view" not in kwargs
    assert kwargs["allowed_mentions"].to_dict() == {"parse": [], "users": [member.id]}


async def test_sin_video_ni_gif_manda_al_menos_la_frase(
    phrases: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member, channel = make_member()
    monkeypatch.setattr(welcome, "WELCOME_VIDEO_PATH", tmp_path / "no-existe.mp4")

    await Welcome(MagicMock()).on_member_join(member)

    kwargs = channel.send.await_args.kwargs
    assert "file" not in kwargs
    assert kwargs["content"] == f"Hola {member.mention}, pasa."


async def test_gif_directo_va_en_el_embed_con_boton(
    phrases: Path, video: Path, tmp_path: Path
) -> None:
    repository = await make_repository(tmp_path)
    gif = "https://media.tenor.com/x/kratos.gif"
    await repository.save_settings(GUILD, WelcomeSettings(gif_url=gif))
    member, channel = make_member()
    cog = Welcome(MagicMock(), repository, clock=lambda: JOINED)

    await cog.on_member_join(member)

    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].image.url == gif
    assert "file" not in kwargs
    (button,) = kwargs["view"].children
    assert button.custom_id == f"bienv:saludar:{NEWCOMER}:{int(JOINED)}"


async def test_pagina_de_tenor_va_como_enlace_al_final(phrases: Path, tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    gif = "https://tenor.com/view/kratos-12345"
    await repository.save_settings(GUILD, WelcomeSettings(gif_url=gif))
    member, channel = make_member()

    await Welcome(MagicMock(), repository).on_member_join(member)

    kwargs = channel.send.await_args.kwargs
    assert kwargs["content"].endswith(f"\n{gif}")
    assert "embed" not in kwargs


async def test_quien_vuelve_recibe_la_frase_de_vuelta(phrases: Path, tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await repository.save_settings(GUILD, WelcomeSettings(gif_url="https://x.com/a.gif"))
    member, channel = make_member()
    cog = Welcome(MagicMock(), repository)

    await cog.on_member_join(member)
    await cog.on_member_join(member)

    assert channel.send.await_args.kwargs["content"] == f"{member.mention} ha vuelto."


async def test_usa_el_canal_configurado(phrases: Path, video: Path, tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    member, chat_general = make_member()
    other = MagicMock(spec=discord.TextChannel)
    other.send = AsyncMock()
    member.guild.get_channel = lambda channel_id: other if channel_id == 77 else None
    await repository.save_settings(GUILD, WelcomeSettings(channel_id=77))

    await Welcome(MagicMock(), repository).on_member_join(member)

    other.send.assert_awaited_once()
    chat_general.send.assert_not_awaited()


async def test_canal_configurado_borrado_vuelve_a_chat_general(
    phrases: Path, video: Path, tmp_path: Path
) -> None:
    repository = await make_repository(tmp_path)
    await repository.save_settings(GUILD, WelcomeSettings(channel_id=77))
    member, chat_general = make_member()

    await Welcome(MagicMock(), repository).on_member_join(member)

    chat_general.send.assert_awaited_once()


# -- Botón 👋 -----------------------------------------------------------------------


def make_interaction(user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.guild = SimpleNamespace(id=GUILD)
    interaction.user = SimpleNamespace(id=user_id, bot=False)
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    return interaction


@pytest.fixture
def tracked(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Sustituye el envío a logros para ver qué se suma."""
    track = AsyncMock()
    monkeypatch.setattr(welcome.logros, "track", track)
    return track


async def greet_cog(tmp_path: Path, now: float) -> Welcome:
    repository = await make_repository(tmp_path)
    await repository.record_join(GUILD, NEWCOMER, now=JOINED)
    return Welcome(MagicMock(), repository, clock=lambda: now)


async def test_saludar_suma_al_contador_y_a_los_logros(tmp_path: Path, tracked: AsyncMock) -> None:
    cog = await greet_cog(tmp_path, now=JOINED + 3600)

    await cog.greet_from_button(make_interaction(1), NEWCOMER, int(JOINED))
    second = make_interaction(2)
    await cog.greet_from_button(second, NEWCOMER, int(JOINED))

    view = second.response.edit_message.await_args.kwargs["view"]
    assert view.children[0].item.label == "Dar la bienvenida · 2"
    delta = tracked.await_args.args[4]
    assert delta.add == {"welcomes_given": 1}


async def test_saludo_en_el_primer_minuto_cuenta_como_rapido(
    tmp_path: Path, tracked: AsyncMock
) -> None:
    cog = await greet_cog(tmp_path, now=JOINED + 30)

    await cog.greet_from_button(make_interaction(1), NEWCOMER, int(JOINED))

    assert tracked.await_args.args[4].add == {"welcomes_given": 1, "welcomes_fast": 1}


@pytest.mark.parametrize(
    ("greeter", "elapsed", "texto"),
    [
        (NEWCOMER, 10, "ti mismo"),
        (1, 2 * 24 * 3600, "un día"),
    ],
)
async def test_saludos_no_validos_se_explican_y_no_cuentan(
    tmp_path: Path, tracked: AsyncMock, greeter: int, elapsed: int, texto: str
) -> None:
    cog = await greet_cog(tmp_path, now=JOINED + elapsed)
    interaction = make_interaction(greeter)

    await cog.greet_from_button(interaction, NEWCOMER, int(JOINED))

    assert texto in interaction.response.send_message.await_args.args[0]
    interaction.response.edit_message.assert_not_awaited()
    tracked.assert_not_awaited()


async def test_no_se_saluda_dos_veces_a_la_misma_persona(
    tmp_path: Path, tracked: AsyncMock
) -> None:
    cog = await greet_cog(tmp_path, now=JOINED + 60)
    await cog.greet_from_button(make_interaction(1), NEWCOMER, int(JOINED))
    again = make_interaction(1)

    await cog.greet_from_button(again, NEWCOMER, int(JOINED))

    assert "Ya le diste" in again.response.send_message.await_args.args[0]
    assert tracked.await_count == 1


async def test_el_boton_se_reconstruye_desde_su_custom_id() -> None:
    match = re.fullmatch(GreetButton.__discord_ui_compiled_template__, "bienv:saludar:5:42")
    assert match is not None
    button = await GreetButton.from_custom_id(MagicMock(), MagicMock(), match)
    assert (button.user_id, button.joined_at) == (5, 42)


# -- Repositorio --------------------------------------------------------------------


async def test_volver_a_entrar_cuenta_y_permite_saludar_de_nuevo(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    first = await repository.record_join(GUILD, NEWCOMER, now=1.0)
    assert await repository.add_greeting(GUILD, NEWCOMER, 1) == 1
    assert await repository.add_greeting(GUILD, NEWCOMER, 1) is None

    again = await repository.record_join(GUILD, NEWCOMER, now=2.0)

    assert (first.joins, again.joins, again.joined_at) == (1, 2, 2.0)
    assert await repository.add_greeting(GUILD, NEWCOMER, 1) == 1


async def test_borrar_servidor_elimina_ajustes_y_entradas(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await repository.save_settings(GUILD, WelcomeSettings(gif_url="https://x.com/a.gif"))
    await repository.record_join(GUILD, NEWCOMER, now=1.0)

    await Welcome(MagicMock(), repository).on_guild_remove(SimpleNamespace(id=GUILD))

    assert await repository.settings(GUILD) == WelcomeSettings()
    assert await repository.last_join(GUILD, NEWCOMER) is None


# -- Despedida ----------------------------------------------------------------------


async def test_member_remove_envia_insulto_aleatorio_sin_menciones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La despedida usa una frase del archivo editable y no activa menciones."""
    member, channel = make_member()
    member.display_name = "@everyone Miembro"
    insults_path = tmp_path / "despedidas.txt"
    insults_path.write_text(
        "# comentario ignorado\n\nprimera frase de prueba.\nsegunda frase de prueba.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(welcome, "FAREWELL_INSULTS_PATH", insults_path)
    monkeypatch.setattr(welcome.random, "choice", lambda choices: choices[0])
    cog = Welcome(MagicMock())

    await cog.on_member_remove(member)

    channel.send.assert_awaited_once()
    args, kwargs = channel.send.await_args
    assert "primera frase de prueba." in args[0]
    assert "@everyone" in args[0]
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


def test_load_farewell_insults_usa_reserva_si_falta_el_archivo(tmp_path: Path) -> None:
    """Si el archivo de frases no existe, se usa la lista de reserva."""
    insults = load_farewell_insults(tmp_path / "no-existe.txt")

    assert insults == welcome.FALLBACK_FAREWELL_INSULTS


def test_load_farewell_insults_usa_reserva_si_el_archivo_esta_vacio(tmp_path: Path) -> None:
    """Un archivo solo con comentarios o líneas vacías no deja la lista sin frases."""
    empty_path = tmp_path / "vacio.txt"
    empty_path.write_text("# solo comentarios\n\n   \n", encoding="utf-8")

    insults = load_farewell_insults(empty_path)

    assert insults == welcome.FALLBACK_FAREWELL_INSULTS


async def test_evento_sin_chat_general_no_intenta_enviar(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """La ausencia del canal se registra y no provoca un error de evento."""
    member, _ = make_member()
    member.guild.text_channels = []
    cog = Welcome(MagicMock())

    await cog.on_member_remove(member)

    assert "No existe el canal #chat-general" in caplog.text


def test_el_archivo_real_de_frases_tiene_entrada_y_vuelta() -> None:
    phrases = welcome.load_welcome_phrases(welcome.ASSETS_PATH / "bienvenidas.txt")
    assert len(phrases.welcomes) >= 5
    assert len(phrases.returns) >= 2
