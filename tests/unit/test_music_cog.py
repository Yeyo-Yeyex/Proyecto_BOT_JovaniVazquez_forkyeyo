"""Pruebas de bot.cogs.music: comandos y bucle del reproductor sin Discord ni red.

`FakeVoice` imita un `discord.VoiceClient`: `play` guarda el callback `after`
y `finish` (o `stop`) lo llama como haría el hilo de audio al acabar la pista.
Así se prueba el ciclo completo (sonar, acabar, siguiente, inactividad,
reconexión) que con mocks sueltos no se veía.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.music import GuildMusicState, Music
from bot.services.music import STREAM_TTL_SECONDS, Track


class Silence(discord.AudioSource):
    """Fuente de audio vacía en lugar de `FFmpegPCMAudio`."""

    def __init__(self, url: str, **_kwargs: object) -> None:
        self.url = url

    def read(self) -> bytes:
        return b""


class FakeVoice:
    """Doble de `discord.VoiceClient` con reproducción controlada por la prueba."""

    def __init__(self, channel: object) -> None:
        self.channel = channel
        self.source: discord.AudioSource | None = None
        self.played: list[str] = []
        self.connected = True
        self.paused = False
        self.disconnects = 0
        self._after = None

    def is_connected(self) -> bool:
        return self.connected

    def is_playing(self) -> bool:
        return self._after is not None and not self.paused

    def is_paused(self) -> bool:
        return self._after is not None and self.paused

    def play(self, source: discord.AudioSource, *, after) -> None:
        self.source = source
        self.played.append(source.original.url)
        self._after = after

    def finish(self, error: Exception | None = None) -> None:
        """La pista llega a su fin (o ffmpeg se cierra)."""
        after, self._after = self._after, None
        if after is not None:
            after(error)

    def stop(self) -> None:
        self.finish()

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    async def disconnect(self, *, force: bool = False) -> None:
        self.connected = False
        self.disconnects += 1


def make_track(title: str = "Canción de prueba", duration: int = 120) -> Track:
    """Crea una pista de prueba lista para encolarse."""
    return Track(
        title=title,
        webpage_url=f"https://example.com/{title}",
        stream_url=f"https://example.com/stream/{title}",
        duration_seconds=duration,
        requested_by="Alguien",
    )


def make_interaction(*, guild_id: int = 1, member: object | None = None) -> MagicMock:
    """Crea una interacción de prueba con respuesta y edición simuladas."""
    interaction = MagicMock()
    interaction.guild = SimpleNamespace(id=guild_id, voice_client=None)
    interaction.user = member
    interaction.channel = MagicMock()
    interaction.channel.send = AsyncMock()
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
    member.bot = False
    return member


def make_context(*, guild_id: int = 1, member: object | None = None) -> MagicMock:
    """Crea un `commands.Context` de prueba para invocar comandos de texto (`....`)."""
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=guild_id, voice_client=None)
    ctx.author = member
    ctx.channel = MagicMock()
    progress_message = MagicMock()
    progress_message.edit = AsyncMock()
    ctx.send = AsyncMock(return_value=progress_message)
    return ctx


def connected(cog: Music, channel: object, guild_id: int = 1) -> GuildMusicState:
    """Estado de un servidor con el bot ya en `channel` (sin bucle de reproducción)."""
    state = cog._get_state(guild_id)
    state.voice_client = FakeVoice(channel)
    return state


async def settle() -> None:
    """Deja correr las tareas pendientes (bucle del reproductor, avisos)."""
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture
async def player(monkeypatch: pytest.MonkeyPatch):
    """Cog con yt-dlp y ffmpeg sustituidos y un canal de voz donde conectar.

    Se parchea el espacio de nombres real del módulo (`__globals__`): si otra
    prueba cargó la extensión con `load_extension`, `sys.modules` puede tener
    otra copia distinta de la que usa `Music`.
    """
    cog = Music(MagicMock())
    module = cog.play.callback.__globals__
    searches: list[str] = []

    def fake_extract(query: str) -> dict:
        searches.append(query)
        return {
            "url": f"https://example.com/stream/{query}",
            "webpage_url": f"https://example.com/{query}",
            "title": query,
            "duration": 120,
        }

    monkeypatch.setitem(module, "extract_track_info", fake_extract)
    monkeypatch.setitem(module, "FAILED_START_SECONDS", 0)
    monkeypatch.setattr(discord, "FFmpegPCMAudio", Silence)
    channel = MagicMock(name="canal-de-voz")
    channel.members = []
    voices: list[FakeVoice] = []

    async def connect(**_kwargs: object) -> FakeVoice:
        voice = FakeVoice(channel)
        voices.append(voice)
        return voice

    channel.connect = AsyncMock(side_effect=connect)
    member = make_voice_member(channel)
    channel.members = [member]
    yield SimpleNamespace(
        cog=cog, module=module, channel=channel, member=member, voices=voices, searches=searches
    )
    for state in cog._states.values():
        if state.player is not None:
            state.player.cancel()
    await settle()


async def poner(env: SimpleNamespace, query: str) -> MagicMock:
    """Lanza `/poner query` como el miembro del canal y deja correr el reproductor."""
    interaction = make_interaction(member=env.member)
    await env.cog.play.callback(env.cog, interaction, query)
    await settle()
    return interaction


async def test_poner_conecta_y_reproduce_la_pista(player: SimpleNamespace) -> None:
    interaction = await poner(player, "uno")

    assert len(player.voices) == 1
    assert player.voices[0].played == ["https://example.com/stream/uno"]
    final = interaction.edit_original_response.await_args.kwargs["content"]
    assert "Reproduciendo ahora" in final


async def test_al_acabar_una_pista_suena_la_siguiente_de_la_cola(
    player: SimpleNamespace,
) -> None:
    await poner(player, "uno")
    interaction = await poner(player, "dos")
    voice = player.voices[0]

    assert "posición 1" in interaction.edit_original_response.await_args.kwargs["content"]
    voice.finish()
    await settle()

    assert voice.played == ["https://example.com/stream/uno", "https://example.com/stream/dos"]


async def test_si_la_busqueda_falla_el_bot_no_entra_al_canal(
    player: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_dlp

    def broken(_query: str) -> dict:
        raise yt_dlp.utils.DownloadError("sin resultados")

    monkeypatch.setitem(player.module, "extract_track_info", broken)

    interaction = await poner(player, "nada")

    player.channel.connect.assert_not_awaited()
    assert "No se encontró" in interaction.edit_original_response.await_args.kwargs["content"]


async def test_tras_la_inactividad_sale_del_canal_y_vuelve_a_funcionar(
    player: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresión: la desconexión por inactividad se cancelaba a sí misma y la
    siguiente sesión dejaba la cola congelada tras la primera canción."""
    monkeypatch.setitem(player.module, "IDLE_DISCONNECT_SECONDS", 0.01)
    await poner(player, "uno")
    player.voices[0].finish()
    await asyncio.sleep(0.05)
    await settle()

    state = player.cog._get_state(1)
    assert player.voices[0].disconnects == 1
    assert state.voice_client is None
    assert state.player is None

    monkeypatch.setitem(player.module, "IDLE_DISCONNECT_SECONDS", 60)
    await poner(player, "dos")
    await poner(player, "tres")
    voice = player.voices[1]
    voice.finish()
    await settle()

    assert voice.played[-1] == "https://example.com/stream/tres"


async def test_parar_sin_nada_sonando_no_congela_la_siguiente_sesion(
    player: SimpleNamespace,
) -> None:
    """Regresión: `parar` en vacío dejaba una bandera que frenaba la cola después."""
    await poner(player, "uno")
    player.voices[0].finish()
    await settle()
    await player.cog.stop.callback(player.cog, make_interaction(member=player.member))
    await settle()

    await poner(player, "dos")
    await poner(player, "tres")
    player.voices[1].finish()
    await settle()

    assert player.voices[1].played[-1] == "https://example.com/stream/tres"


async def test_parar_vacia_la_cola_y_desconecta(player: SimpleNamespace) -> None:
    await poner(player, "uno")
    await poner(player, "dos")
    interaction = make_interaction(member=player.member)

    await player.cog.stop.callback(player.cog, interaction)

    state = player.cog._get_state(1)
    assert player.voices[0].disconnects == 1
    assert state.voice_client is None
    assert state.current is None
    assert len(state.queue) == 0
    assert state.player is None


async def test_una_pista_que_no_llega_a_sonar_se_avisa_y_pasa_a_la_siguiente(
    player: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(player.module, "FAILED_START_SECONDS", 60)
    await poner(player, "rota")
    last = await poner(player, "buena")  # se avisa en el canal del último `poner`
    voice = player.voices[0]

    voice.finish()  # ffmpeg se cierra nada más empezar (403, URL caducada…)
    await settle()

    last.channel.send.assert_awaited_once()
    assert "No se pudo reproducir **rota**" in last.channel.send.await_args.args[0]
    assert voice.played[-1] == "https://example.com/stream/buena"


async def test_saltar_no_se_avisa_como_fallo(
    player: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(player.module, "FAILED_START_SECONDS", 60)
    await poner(player, "uno")
    interaction = await poner(player, "dos")

    await player.cog.skip.callback(player.cog, make_interaction(member=player.member))
    await settle()

    interaction.channel.send.assert_not_awaited()
    assert player.voices[0].played[-1] == "https://example.com/stream/dos"


async def test_una_pista_caducada_se_vuelve_a_resolver_antes_de_sonar(
    player: SimpleNamespace,
) -> None:
    await poner(player, "uno")
    state = player.cog._get_state(1)
    old = make_track("vieja")
    state.queue.add(
        Track(
            title=old.title,
            webpage_url=old.webpage_url,
            stream_url="https://example.com/caducada",
            duration_seconds=old.duration_seconds,
            requested_by=old.requested_by,
            resolved_at=old.resolved_at - STREAM_TTL_SECONDS - 1,
        )
    )

    player.voices[0].finish()
    await settle()

    assert player.searches[-1] == "https://example.com/vieja"
    assert player.voices[0].played[-1] != "https://example.com/caducada"


async def test_si_echan_al_bot_del_canal_se_para_la_musica(player: SimpleNamespace) -> None:
    await poner(player, "uno")
    await poner(player, "dos")
    bot_member = MagicMock()
    bot_member.id = player.cog.bot.user.id
    bot_member.guild.id = 1

    await player.cog.on_voice_state_update(
        bot_member, SimpleNamespace(channel=player.channel), SimpleNamespace(channel=None)
    )

    state = player.cog._get_state(1)
    assert state.player is None
    assert len(state.queue) == 0


async def test_si_se_quedan_sin_humanos_el_bot_sale(player: SimpleNamespace) -> None:
    await poner(player, "uno")
    leaving = player.member
    leaving.guild.id = 1
    player.channel.members = []

    await player.cog.on_voice_state_update(
        leaving, SimpleNamespace(channel=player.channel), SimpleNamespace(channel=None)
    )

    assert player.voices[0].disconnects == 1
    assert player.cog._get_state(1).voice_client is None


async def test_volumen_cambia_la_pista_que_ya_suena(player: SimpleNamespace) -> None:
    await poner(player, "uno")

    await player.cog.volume.callback(player.cog, make_interaction(member=player.member), 50)

    assert player.cog._get_state(1).volume_percent == 50
    assert player.voices[0].source.volume == pytest.approx(0.5)


async def test_pausar_sin_conexion_de_voz_responde_error() -> None:
    """No se puede pausar si el bot no está conectado a ningún canal."""
    cog = Music(MagicMock())
    interaction = make_interaction(member=make_voice_member(MagicMock()))

    await cog.pause.callback(cog, interaction)

    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "no está conectado" in message


async def test_pausar_con_miembro_en_otro_canal_responde_error() -> None:
    """El control de música exige estar en el mismo canal que el bot."""
    cog = Music(MagicMock())
    state = connected(cog, MagicMock(name="canal-del-bot"))
    interaction = make_interaction(member=make_voice_member(MagicMock(name="otro-canal")))

    await cog.pause.callback(cog, interaction)

    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "mismo canal de voz" in message
    assert not state.voice_client.paused


async def test_pausar_y_seguir(player: SimpleNamespace) -> None:
    await poner(player, "uno")
    voice = player.voices[0]

    await player.cog.pause.callback(player.cog, make_interaction(member=player.member))
    assert voice.is_paused()
    await player.cog.resume.callback(player.cog, make_interaction(member=player.member))
    assert voice.is_playing()


async def test_saltar_sin_reproduccion_responde_error() -> None:
    """/saltar avisa si no hay nada que saltar."""
    cog = Music(MagicMock())
    channel = MagicMock()
    connected(cog, channel)
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.skip.callback(cog, interaction)

    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "no hay ninguna pista" in message.lower()


async def test_vaciar_vacia_la_cola_sin_tocar_la_pista_actual() -> None:
    """/vaciar deja la pista en curso intacta y vacía solo lo pendiente."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = connected(cog, channel)
    state.current = make_track("En curso")
    state.queue.add(make_track("Pendiente"))

    await cog.clear.callback(cog, make_interaction(member=make_voice_member(channel)))

    assert len(state.queue) == 0
    assert state.current is not None


async def test_quitar_posicion_invalida_responde_error() -> None:
    """/quitar informa si la posición pedida no existe en la cola."""
    cog = Music(MagicMock())
    channel = MagicMock()
    connected(cog, channel)
    interaction = make_interaction(member=make_voice_member(channel))

    await cog.remove.callback(cog, interaction, 3)

    message = interaction.response.send_message.await_args.kwargs["content"]
    assert "posición 3" in message


async def test_quitar_elimina_la_pista_pedida() -> None:
    """/quitar elimina exactamente la pista de la posición indicada."""
    cog = Music(MagicMock())
    channel = MagicMock()
    state = connected(cog, channel)
    state.queue.add(make_track("Primera"))
    state.queue.add(make_track("Segunda"))

    await cog.remove.callback(cog, make_interaction(member=make_voice_member(channel)), 1)

    assert [track.title for track in state.queue.snapshot()] == ["Segunda"]


async def test_cola_lista_lo_pendiente_aunque_aun_no_suene_nada() -> None:
    """Entre dos pistas `current` está vacío, pero la cola sí tiene pistas."""
    cog = Music(MagicMock())
    state = cog._get_state(1)
    state.queue.add(make_track("Pendiente"))
    interaction = make_interaction(member=make_voice_member(MagicMock()))

    await cog.queue_.callback(cog, interaction)

    assert "1. Pendiente" in interaction.response.send_message.await_args.kwargs["content"]


# Contrato de nombres: cada acción tiene UN solo nombre corto, idéntico en
# el comando de aplicación (`/poner`) y en el de texto (`.poner`), sin alias.
MUSIC_COMMAND_NAMES = {
    "poner",
    "pausar",
    "seguir",
    "saltar",
    "parar",
    "cola",
    "quitar",
    "vaciar",
    "volumen",
}


def test_cada_comando_de_musica_tiene_el_mismo_nombre_en_slash_y_texto() -> None:
    """El menú `/` y el prefijo `.` exponen exactamente los mismos nombres."""
    cog = Music(MagicMock())

    slash_names = {command.name for command in cog.get_app_commands()}
    text_names = {command.name for command in cog.get_commands()}

    assert slash_names == MUSIC_COMMAND_NAMES
    assert text_names == MUSIC_COMMAND_NAMES


def test_los_comandos_de_texto_de_musica_no_definen_alias() -> None:
    """Sin alias: un nombre distinto por interfaz era justo lo que se quería evitar."""
    cog = Music(MagicMock())

    assert all(not command.aliases for command in cog.get_commands())


async def test_comando_de_texto_poner_reproduce_igual_que_el_slash(
    player: SimpleNamespace,
) -> None:
    """`.poner` reproduce una pista igual que `/poner`."""
    ctx = make_context(member=player.member)

    await player.cog.play_text.callback(player.cog, ctx, consulta="prefijo")
    await settle()

    ctx.send.assert_awaited_once_with("🔎 Buscando...")
    final_content = ctx.send.return_value.edit.await_args.kwargs["content"]
    assert "prefijo" in final_content
    assert player.voices[0].played == ["https://example.com/stream/prefijo"]


async def test_comando_de_texto_volumen_valida_rango_antes_de_delegar() -> None:
    """`.volumen` rechaza valores fuera de rango sin llegar a `_volume_impl`."""
    cog = Music(MagicMock())
    ctx = make_context(member=make_voice_member(MagicMock()))

    await cog.volume_text.callback(cog, ctx, 999)

    ctx.send.assert_awaited_once()
    assert "entre" in ctx.send.await_args.args[0]
