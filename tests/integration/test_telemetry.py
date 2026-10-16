"""La telemetría cuenta lo que pasa en el bot real y el informe lo devuelve.

Se carga el bot como en producción (`BotClient` + `INITIAL_EXTENSIONS`): los
enganches de `bot.cogs.telemetry` envuelven clases de discord.py y la telemetría
vive en el cliente, así que un cog montado a mano no prueba que lleguen.

Los clics pasan por `BaseView._scheduled_task`, el método interno de discord.py
que se envuelve; si una versión nueva lo cambia, estas pruebas lo dicen.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.state import ConnectionState
from interaction_fakes import fake_interaction

import bot.repositories.sqlite as sqlite_module
from bot.app import INITIAL_EXTENSIONS, BotClient
from bot.cogs.telemetry import (
    ARRIVAL,
    FIRST_RESPONSES,
    IN_BOT,
    TRIP,
    _arrival,
    _timed_response,
    item_name,
)
from bot.services.telemetry import report_render
from bot.utils.interactions import ack
from bot.utils.responder import CommandResponder

GUILD_ID = 1
USER_ID = 284019384756019283
CHANNEL_ID = 55


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
    await client.telemetry_repository.initialize()
    for extension in INITIAL_EXTENSIONS:
        await client.load_extension(extension)
    return client


class Panel(discord.ui.View):
    """Vista de prueba con un botón que contesta y otro que se olvida."""

    @discord.ui.button(label="Sí")
    async def answers(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await ack(interaction)

    @discord.ui.button(label="No")
    async def forgets(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        return None


def click(client: BotClient, interaction_id: int) -> MagicMock:
    interaction = fake_interaction(MagicMock(id=USER_ID))
    interaction.client = client
    interaction.id = interaction_id
    interaction.guild_id = GUILD_ID
    return interaction


class Responder(CommandResponder):
    """Responder que se queda con el archivo del informe."""

    def __init__(self, guild: MagicMock) -> None:
        self.guild = guild
        self.member = None
        self.channel = None
        self.file: discord.File | None = None
        self.errors: list[str] = []

    async def send(self, content: str | None = None, **kwargs: object) -> None:
        return None

    async def send_error(self, content: str) -> None:
        self.errors.append(content)

    async def start_progress(self, placeholder: str = "", *, ephemeral: bool = False) -> None:
        return None

    async def update_progress(self, content: str) -> None:
        return None

    async def finish(self, content: str | None = None, **kwargs: object) -> None:
        file = kwargs.get("file")
        assert isinstance(file, discord.File)
        self.file = file


def usage(report: dict, kind: str) -> dict[str, dict]:
    return {row["nombre"]: row for row in report["uso"] if row["tipo"] == kind}


def test_los_clics_se_cuentan_por_vista_y_metodo_y_sin_respuesta_es_error(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        client = await load_bot(tmp_path)
        try:
            panel = Panel()
            answers, forgets = panel.children
            assert item_name(panel, answers) == "Panel.answers"
            await panel._scheduled_task(answers, click(client, 1))
            await panel._scheduled_task(answers, click(client, 2))
            await panel._scheduled_task(forgets, click(client, 3))

            report = await client.telemetry.report(
                guild_id=GUILD_ID, days=1, commands=[], channel_names={}
            )
            buttons = usage(report, "button")
            assert buttons["Panel.answers"]["eventos"] == 2
            assert buttons["Panel.answers"]["errores"] == 0
            assert buttons["Panel.answers"]["usuarios"] == 1
            assert buttons["Panel.forgets"]["errores"] == 1
        finally:
            await client.close()

    asyncio.run(scenario())


def test_contar_un_clic_no_abre_la_base_de_datos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La telemetría suma en memoria: un clic no puede esperar a SQLite."""

    async def scenario() -> None:
        client = await load_bot(tmp_path)
        try:
            panel = Panel()
            opened: list[Path] = []
            real_connect = sqlite_module.connect

            def spy(path: Path, **kwargs: object) -> object:
                opened.append(path)
                return real_connect(path, **kwargs)

            monkeypatch.setattr(sqlite_module, "connect", spy)
            await panel._scheduled_task(panel.children[0], click(client, 1))
            assert opened == []
            await client.telemetry.flush()
            assert opened, "el volcado sí escribe"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_la_primera_respuesta_apunta_la_latencia_desde_que_discord_creo_el_clic(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        client = await load_bot(tmp_path)
        try:
            # Los métodos reales de discord.py quedan envueltos al cargar el cog.
            for name in FIRST_RESPONSES:
                assert getattr(discord.InteractionResponse, name)._telemetry_wrapped

            class Response:
                """Lo mínimo de `InteractionResponse` que usa el envoltorio."""

                def __init__(self, parent: MagicMock) -> None:
                    self._parent = parent
                    self.done = False

                def is_done(self) -> bool:
                    return self.done

                async def defer(self) -> None:
                    self.done = True

            parent = MagicMock(id=77, client=client)
            parent.created_at = discord.utils.utcnow() - timedelta(seconds=2)
            response = Response(parent)
            timed = _timed_response(Response.defer)
            await timed(response)
            await timed(response)  # la segunda ya no es primera respuesta

            latency = client.telemetry.take_answer(77)
            assert latency is not None
            assert 2000 <= latency < 3000
            assert client.telemetry.take_answer(77) is None
        finally:
            await client.close()

    asyncio.run(scenario())


def test_la_latencia_se_parte_en_llegada_bot_y_viaje(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = await load_bot(tmp_path)
        try:
            assert ConnectionState.parse_interaction_create._telemetry_wrapped

            class Response:
                def __init__(self, parent: MagicMock) -> None:
                    self._parent = parent
                    self.done = False

                def is_done(self) -> bool:
                    return self.done

                async def defer(self) -> None:
                    await asyncio.sleep(0.05)
                    self.done = True

            parent = MagicMock(id=78, client=client, guild_id=GUILD_ID)
            parent.created_at = discord.utils.utcnow() - timedelta(seconds=1)
            _arrival(lambda _state, _data: None)(MagicMock(), {"id": "78"})
            await _timed_response(Response.defer)(Response(parent))

            tramos = {
                key.name: counter.ms_total
                for key, counter in client.telemetry._counters.items()
                if key.kind == "respuesta"
            }
            assert set(tramos) == {ARRIVAL, IN_BOT, TRIP}
            assert 900 <= tramos[ARRIVAL] < 2000
            assert tramos[TRIP] >= 50
        finally:
            await client.close()

    asyncio.run(scenario())


def test_mensajes_dibujos_y_avisos_del_log_llegan_al_informe_sin_su_contenido(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        client = await load_bot(tmp_path)
        try:
            message = MagicMock(spec=discord.Message)
            message.guild = MagicMock(id=GUILD_ID)
            message.author = MagicMock(id=USER_ID, bot=False)
            message.channel = MagicMock(id=CHANNEL_ID)
            message.content = "mira https://ejemplo.es contraseña123"
            message.attachments = []
            message.stickers = []
            message.reference = None
            # Sin conexión no hay `dispatch`: se llama al oyente del cog cargado.
            await client.get_cog("Telemetry").on_message(message)  # type: ignore[union-attr]

            report_render("El pintor de la moneda", 120.0)
            logging.getLogger("bot.prueba").warning("Falló %s", "dato-privado")

            guild = MagicMock(id=GUILD_ID, threads=[])
            channel = MagicMock(id=CHANNEL_ID)
            channel.name = "chat-general"
            guild.channels = [channel]
            responder = Responder(guild)
            await client.get_cog("Admin")._informe_impl(responder, 7)  # type: ignore[union-attr]

            assert responder.errors == []
            assert responder.file is not None
            raw = responder.file.fp.read().decode()
            report = json.loads(raw)
            assert usage(report, "message")["#chat-general"]["eventos"] == 1
            assert {"enlace", "medio"} <= set(usage(report, "message_type"))
            assert usage(report, "render")["El pintor de la moneda"]["ms_max"] == 120
            assert "WARNING bot.prueba: Falló %s" in usage(report, "log")
            for secret in ("contraseña123", "ejemplo.es", "dato-privado", str(USER_ID)):
                assert secret not in raw
            assert report["actividad"] == {"u1": {"message:#chat-general": [6]}}
        finally:
            await client.close()

    asyncio.run(scenario())


class ChannelResponder(Responder):
    """Responder que apunta lo que contesta `informe` al elegir canal."""

    def __init__(self, guild: MagicMock) -> None:
        super().__init__(guild)
        self.said: list[str] = []

    async def send(self, content: str | None = None, **kwargs: object) -> None:
        self.said.append(content or "")

    async def finish(self, content: str | None = None, **kwargs: object) -> None:
        self.said.append(content or "")


def test_informe_elige_el_canal_diario_publica_el_de_hoy_y_se_quita(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = await load_bot(tmp_path)
        try:
            channel = MagicMock(spec=discord.TextChannel, id=77, mention="<#77>")
            channel.send = AsyncMock()

            async def history(limit: int):  # noqa: ANN202, ARG001
                return
                yield

            channel.history = history
            guild = MagicMock(id=GUILD_ID, threads=[], channels=[channel])
            guild.get_channel_or_thread.return_value = channel
            admin = client.get_cog("Admin")
            responder = ChannelResponder(guild)

            await admin._informe_impl(responder, 30, 77)  # type: ignore[union-attr]
            assert responder.errors == []
            assert await client.telemetry.report_channel(GUILD_ID) == 77
            published = channel.send.await_args.kwargs["file"]
            assert published.filename.startswith("informe") and published.filename.endswith(".json")
            assert "Ya está el de hoy" in responder.said[-1]

            await admin._informe_impl(responder, 30, remove=True)  # type: ignore[union-attr]
            assert await client.telemetry.report_channel(GUILD_ID) is None
        finally:
            await client.close()

    asyncio.run(scenario())


def test_al_cerrar_el_bot_se_guarda_lo_pendiente_y_se_sueltan_los_enganches(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        client = await load_bot(tmp_path)
        client.telemetry.count("slash", "ruleta", guild_id=GUILD_ID, user_id=USER_ID)
        await client.close()

        report_render("Tras cerrar", 1.0)
        assert not client.telemetry.drain().counters
        data = await client.telemetry_repository.report_data(
            guild_id=GUILD_ID, since_day="2000-01-01", since_ts=0
        )
        assert [row["name"] for row in data.usage if row["kind"] == "slash"] == ["ruleta"]

    asyncio.run(scenario())
