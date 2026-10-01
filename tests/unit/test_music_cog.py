"""Pruebas de bot.cogs.music: control del reproductor con mocks de discord.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.music import GuildMusicState, Music
from bot.services.music import Track


def make_track(title: str = "Canción de prueba", duration: int = 120) -> Track:
    """Crea una pista de prueba lista para encolarse."""
    return Track(
        title=title,
        webpage_url="https://example.com/video",
        stream_url="https://example.com/stream",
        duration_seconds=duration,
        requested_by="Alguien",
    )


def make_interaction(*, guild_id: int = 1, member: object | None = None) -> MagicMock:
    """Crea una interacción de prueba con respuesta y edición simuladas."""
    interaction = MagicMock()
    interaction.guild = SimpleNamespace(id=guild_id)
    interaction.user = member
    interaction.channel = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


def make_voice_member(channel: object) -> SimpleNamespace:
    """Crea un `discord.Member` de prueba conectado a un canal de voz dado."""
    member = MagicMock(spec=discord.Member)
    member.voice = SimpleNamespace(channel=channel)
    member.display_name = "Miembro de prueba"
    return member


def make_context(*, guild_id: int = 1, member: object | None = None) -> MagicMock:
    """Crea un `commands.Context` de prueba para invocar comandos de texto (`º...`)."""
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=guild_id)
    ctx.author = member
    ctx.channel = MagicMock()
    progress_message = MagicMock()
    progress_message.edit = AsyncMock()
    ctx.send = AsyncMock(return_value=progress_message)
    return ctx


@pytest.mark.asyncio
async def test_pausar_sin_conexion_de_voz_responde_error() -> None:
    """No se puede pausar si el bot no está conectado a ningún canal."""
    cog = Music(MagicMock())
    channel = MagicMock()
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.pause.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "no está conectado" in message


@pytest.mark.asyncio
async def test_pausar_con_miembro_en_otro_canal_responde_error() -> None:
    """El control de música exige estar en el mismo canal que el bot."""
    cog = Music(MagicMock())
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = MagicMock(name="canal-del-bot")
    state.voice_client.is_playing.return_value = True
    otro_canal = MagicMock(name="otro-canal")
    interaction = make_interaction(member=make_voice_member(otro_canal))

    await cog.pause.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "mismo canal de voz" in message
    state.voice_client.pause.assert_not_called()


@pytest.mark.asyncio
async def test_pausar_detiene_la_reproduccion_en_curso() -> None:
    """/pausar pausa el reproductor cuando hay una pista en curso."""
    cog = Music(MagicMock())
    channel = MagicMock(name="canal-compartido")
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.voice_client.is_playing.return_value = True
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.pause.callback(cog, interaction)

    state.voice_client.pause.assert_called_once()
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_saltar_sin_reproduccion_responde_error() -> None:
    """/saltar avisa si no hay nada que saltar."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.voice_client.is_playing.return_value = False
    state.voice_client.is_paused.return_value = False
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.skip.callback(cog, interaction)

    state.voice_client.stop.assert_not_called()
    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "no hay ninguna pista" in message.lower()


@pytest.mark.asyncio
async def test_saltar_detiene_la_pista_actual_para_encadenar_la_siguiente() -> None:
    """/saltar deja que el callback `after` continúe con la siguiente pista."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.voice_client.is_playing.return_value = True
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.skip.callback(cog, interaction)

    state.voice_client.stop.assert_called_once()
    assert state.stopping is False  # a diferencia de /parar, no se frena la cola


@pytest.mark.asyncio
async def test_limpiar_vacia_la_cola_sin_tocar_la_pista_actual() -> None:
    """/limpiar deja la pista en curso intacta y vacía solo lo pendiente."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.current = make_track("En curso")
    state.queue.add(make_track("Pendiente"))
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.clear.callback(cog, interaction)

    assert len(state.queue) == 0
    assert state.current is not None


@pytest.mark.asyncio
async def test_quitar_posicion_invalida_responde_error() -> None:
    """/quitar informa si la posición pedida no existe en la cola."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.remove.callback(cog, interaction, 3)

    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "posición 3" in message


@pytest.mark.asyncio
async def test_quitar_elimina_la_pista_pedida() -> None:
    """/quitar elimina exactamente la pista de la posición indicada."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.queue.add(make_track("Primera"))
    state.queue.add(make_track("Segunda"))
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.remove.callback(cog, interaction, 1)

    assert len(state.queue) == 1
    assert state.queue.snapshot()[0].title == "Segunda"


@pytest.mark.asyncio
async def test_volumen_ajusta_la_fuente_en_reproduccion() -> None:
    """/volumen actualiza el estado guardado y la fuente activa si existe."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    fake_source = MagicMock(spec=discord.PCMVolumeTransformer)
    state.voice_client.source = fake_source
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.volume.callback(cog, interaction, 50)

    assert state.volume_percent == 50
    assert fake_source.volume == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_parar_marca_stopping_y_desconecta() -> None:
    """/parar vacía la cola, detiene la reproducción y desconecta del canal."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    voice_client = MagicMock()
    voice_client.channel = channel
    voice_client.is_playing.return_value = True
    voice_client.disconnect = AsyncMock()
    state.voice_client = voice_client
    state.queue.add(make_track())
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.stop.callback(cog, interaction)

    voice_client.stop.assert_called_once()
    voice_client.disconnect.assert_awaited_once_with(force=True)
    assert state.voice_client is None
    assert len(state.queue) == 0
    assert state.current is None


@pytest.mark.asyncio
async def test_advance_no_reproduce_siguiente_si_estaba_parando() -> None:
    """El callback de fin de pista respeta un /parar en curso y no reanuda la cola."""
    cog = Music(MagicMock())
    state = cog._get_state(1)
    state.stopping = True
    state.voice_client = None  # ya desconectado por /parar

    await cog._advance(1, None)

    assert state.stopping is False


@pytest.mark.asyncio
async def test_advance_reproduce_la_siguiente_pista_tras_un_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tras un error de reproducción normal (sin /parar), se continúa la cola."""
    import bot.cogs.music as music_module

    monkeypatch.setattr(music_module.discord, "FFmpegPCMAudio", MagicMock())
    monkeypatch.setattr(music_module.discord, "PCMVolumeTransformer", MagicMock())
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.queue.add(make_track("Siguiente"))

    await cog._advance(1, RuntimeError("fallo de red"))

    state.voice_client.play.assert_called_once()
    assert state.current is not None
    assert state.current.title == "Siguiente"


def test_guild_music_state_valores_por_defecto() -> None:
    """El estado nuevo de un servidor no tiene conexión, cola vacía y volumen base."""
    state = GuildMusicState()

    assert state.voice_client is None
    assert state.current is None
    assert len(state.queue) == 0
    assert state.stopping is False


# Cada alias corto debe registrarse con su propio nombre de comando pero
# delegar en exactamente la misma lógica que el comando completo, para no
# duplicar reglas de negocio ni comportarse de forma distinta.
ALIAS_PAIRS = [
    ("reproducir", "p"),
    ("pausar", "pausa"),
    ("reanudar", "rs"),
    ("saltar", "s"),
    ("parar", "stop"),
    ("cola", "q"),
    ("quitar", "rm"),
    ("limpiar", "cl"),
    ("volumen", "vol"),
]


@pytest.mark.parametrize(("full_name", "alias_name"), ALIAS_PAIRS)
def test_cada_alias_corto_esta_registrado_junto_a_su_comando_completo(
    full_name: str, alias_name: str
) -> None:
    """Cada alias corto existe como comando de aplicación independiente."""
    cog = Music(MagicMock())
    command_names = {command.name for command in cog.get_app_commands()}

    assert full_name in command_names
    assert alias_name in command_names


@pytest.mark.asyncio
async def test_alias_pausa_produce_el_mismo_efecto_que_pausar() -> None:
    """El alias `/pausa` pausa la reproducción igual que `/pausar`."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.voice_client.is_playing.return_value = True
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.pause_short.callback(cog, interaction)

    state.voice_client.pause.assert_called_once()


@pytest.mark.asyncio
async def test_alias_q_muestra_la_misma_cola_que_el_comando_cola() -> None:
    """El alias `/q` lista la cola igual que `/cola`."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.current = make_track("En curso")
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.queue_short.callback(cog, interaction)

    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "En curso" in message


@pytest.mark.asyncio
async def test_alias_vol_ajusta_el_mismo_volumen_que_volumen() -> None:
    """El alias `/vol` ajusta el volumen igual que `/volumen`."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.volume_short.callback(cog, interaction, 42)

    assert state.volume_percent == 42


# Cada comando de texto con prefijo (invocado como "ºreproducir", "ºp", etc.)
# debe registrar exactamente los mismos alias cortos que su comando slash y
# delegar en la misma lógica compartida (`_*_impl`), para no duplicar reglas.
TEXT_COMMAND_ALIASES = {
    "reproducir": {"p", "play"},
    "pausar": {"pausa", "pause"},
    "reanudar": {"rs", "resume"},
    "saltar": {"s", "skip"},
    "parar": {"stop"},
    "cola": {"q", "queue"},
    "quitar": {"rm", "remove"},
    "limpiar": {"cl", "clear"},
    "volumen": {"vol", "volume"},
}


@pytest.mark.parametrize(("name", "aliases"), TEXT_COMMAND_ALIASES.items())
def test_cada_comando_de_texto_registra_sus_alias_cortos(name: str, aliases: set[str]) -> None:
    """Cada comando de texto (`º...`) expone los mismos alias que su slash command."""
    cog = Music(MagicMock())
    text_commands = {command.name: set(command.aliases) for command in cog.get_commands()}

    assert name in text_commands
    assert text_commands[name] == aliases


@pytest.mark.asyncio
async def test_comando_de_texto_reproducir_encola_la_pista_igual_que_el_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ºreproducir`/`ºp`/`ºplay` reproducen una pista igual que `/reproducir`."""
    cog = Music(MagicMock())
    channel = MagicMock(name="canal-de-voz")
    voice_client = MagicMock()
    voice_client.is_playing.return_value = False
    member = make_voice_member(channel)
    member.voice.channel.connect = AsyncMock(return_value=voice_client)
    ctx = make_context(member=member)

    # Se parchea el espacio de nombres real que usa la función en tiempo de
    # ejecución (`__globals__`), en vez de reimportar el módulo por nombre:
    # si otra prueba de la suite carga esta misma extensión vía
    # `Bot.load_extension`, discord.py puede reejecutar el módulo y dejar
    # una segunda instancia en `sys.modules` distinta de la que usa `Music`.
    music_module_globals = cog.play_text.callback.__globals__
    monkeypatch.setitem(music_module_globals, "extract_track_info", lambda _query: {})
    monkeypatch.setitem(
        music_module_globals,
        "build_track_from_info",
        lambda *_a, **_k: make_track("Pista de prefijo"),
    )
    monkeypatch.setattr(cog, "_play_next", AsyncMock())

    await cog.play_text.callback(cog, ctx, consulta="cancion de prueba")

    ctx.send.assert_awaited_once_with("🔎 Buscando...")
    # El mensaje de progreso enviado por `ctx.send` se edita con el resultado.
    sent_message = ctx.send.return_value
    sent_message.edit.assert_awaited_once()
    final_content = sent_message.edit.await_args.kwargs["content"]
    assert "Pista de prefijo" in final_content


@pytest.mark.asyncio
async def test_comando_de_texto_pausar_pausa_igual_que_el_slash() -> None:
    """`ºpausar` pausa la reproducción igual que `/pausar`."""
    cog = Music(MagicMock())
    channel = MagicMock(name="canal-compartido")
    state = cog._get_state(1)
    state.voice_client = MagicMock()
    state.voice_client.channel = channel
    state.voice_client.is_playing.return_value = True
    ctx = make_context(member=make_voice_member(channel))

    await cog.pause_text.callback(cog, ctx)

    state.voice_client.pause.assert_called_once()
    ctx.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_comando_de_texto_volumen_valida_rango_antes_de_delegar() -> None:
    """`ºvolumen` rechaza valores fuera de rango sin llegar a `_volume_impl`."""
    cog = Music(MagicMock())
    ctx = make_context(member=make_voice_member(MagicMock()))

    await cog.volume_text.callback(cog, ctx, 999)

    ctx.send.assert_awaited_once()
    message = ctx.send.await_args.args[0]
    assert "entre" in message
