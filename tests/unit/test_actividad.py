"""Pruebas de bot.cogs.actividad: el servidor web de la Activity de Discord.

Se prueba de punta a punta con el servidor de `aiohttp` de verdad (sin abrir un
puerto: `TestServer`), la mesa real de la moneda y la economía real sobre un SQLite
temporal. Lo único sustituido es Discord: cambiar el código por el token, quién es
el usuario y la API de instancias (los tres métodos `_exchange`, `_whoami` e
`_instance`), y el bot, que es un doble con un servidor y un miembro.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer
from coin_fakes import Scripted

from bot.cogs import actividad as actividad_cog
from bot.cogs import coin as coin_cog
from bot.cogs.actividad import Actividad
from bot.cogs.coin import BUSY_TEXT, Coin
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import EconomyRepository
from bot.services.coin import Outcome, Side, Status
from bot.services.coin_render import Media
from bot.services.economy import STARTING_BALANCE, EconomyService

CARA, CRUZ = Outcome.CARA, Outcome.CRUZ
GUILD_ID = 1
OTHER_GUILD_ID = 2
OWNER_ID = 10
STRANGER_ID = 11
INSTANCE = "i-1-gc-1-20"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lo de después de la partida no espera a la animación (no hay navegador)."""
    monkeypatch.setattr(actividad_cog, "toss_seconds", lambda states: 0.0)
    monkeypatch.setattr(actividad_cog, "FINISH_MARGIN_SECONDS", 0)


def make_member(user_id: int = OWNER_ID) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = "Diego"
    member.mention = f"<@{user_id}>"
    member.bot = False
    return member


def make_bot(coin: list[Coin]) -> MagicMock:
    """Un bot con un servidor (`GUILD_ID`) del que solo es miembro `OWNER_ID`."""
    owner = make_member()
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    guild.name = "El servidor"
    guild.get_member = lambda user_id: owner if user_id == OWNER_ID else None
    bot = MagicMock()
    bot.application_id = 123
    bot.get_guild = lambda guild_id: guild if guild_id == GUILD_ID else None
    bot.get_channel = lambda channel_id: None
    bot.get_cog = lambda name: coin[0] if coin and name == "Moneda" else None
    return bot


async def make_cogs(
    tmp_path: Path, *results: Outcome, guild_id: int | None = None
) -> tuple[Actividad, Coin]:
    """La Activity y la moneda con economía real y azar de guion (como `test_coin_cog`)."""
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    holder: list[Coin] = []
    bot = make_bot(holder)
    renderer = MagicMock()
    renderer.toss = AsyncMock(return_value=Media(gif=b"GIF89a", png=b"png", seconds=0.0))
    renderer.board = AsyncMock(return_value=b"png")
    renderer.close = AsyncMock()
    coin = Coin(
        bot,
        economy=EconomyService(repository),
        rng=Scripted(*results, CARA),
        renderer=renderer,
    )
    holder.append(coin)
    cog = Actividad(bot, secret="secreto", guild_id=guild_id)
    cog._exchange = AsyncMock(return_value="token-de-diego")  # type: ignore[method-assign]
    cog._whoami = AsyncMock(return_value=OWNER_ID)  # type: ignore[method-assign]
    cog._instance = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "instance_id": INSTANCE,
            "location": {"kind": "gc", "guild_id": str(GUILD_ID), "channel_id": "20"},
            "users": [str(OWNER_ID)],
        }
    )
    return cog, coin


async def login(http: TestClient) -> dict[str, str]:
    response = await http.post("/api/token", json={"code": "abc", "instance_id": INSTANCE})
    assert response.status == 200, await response.text()
    token = (await response.json())["access_token"]
    return {"Authorization": f"Bearer {token}"}


def ledger(tmp_path: Path) -> list[tuple[str, int]]:
    connection = sqlite_module.connect(tmp_path / "bot.db")
    try:
        rows = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id",
            (GUILD_ID, OWNER_ID),
        ).fetchall()
    finally:
        connection.close()
    return [(row[0], row[1]) for row in rows if row[0] != "bienvenida"]


async def test_sin_secreto_no_abre_el_servidor(tmp_path: Path) -> None:
    cog, _ = await make_cogs(tmp_path)
    cog.secret = ""

    await cog.cog_load()

    assert cog._runner is None
    await cog.cog_unload()


async def test_con_el_puerto_cogido_el_bot_sigue_sin_activity(tmp_path: Path) -> None:
    """Si otro programa del NAS tiene el puerto, el cog carga igual: el bot no se cae."""
    cog, _ = await make_cogs(tmp_path)
    with socket.socket() as busy:
        busy.bind(("0.0.0.0", 0))
        busy.listen()
        cog.port = busy.getsockname()[1]

        await cog.cog_load()

        assert cog._runner is None
    await cog.cog_unload()


async def test_la_pagina_lleva_el_id_de_la_app(tmp_path: Path) -> None:
    cog, _ = await make_cogs(tmp_path)
    cog._page = actividad_cog.PAGE.read_text(encoding="utf-8")
    async with TestClient(TestServer(cog.build_app())) as http:
        page = await (await http.get("/")).text()
        script = await http.get("/assets/actividad/actividad.js")
        scene = await http.get("/assets/moneda/escena.html")

    assert 'content="123"' in page and "%CLIENT_ID%" not in page
    assert script.status == 200 and "javascript" in script.content_type
    assert scene.status == 200


async def test_el_token_abre_una_sesion_con_el_servidor_y_el_canal(tmp_path: Path) -> None:
    cog, _ = await make_cogs(tmp_path)
    async with TestClient(TestServer(cog.build_app())) as http:
        await login(http)

    session = cog.sessions["token-de-diego"]
    assert (session.user_id, session.guild_id) == (OWNER_ID, GUILD_ID)
    # El canal de la Activity no es del bot (el doble no conoce ninguno): sin anuncios.
    assert session.channel_id is None
    cog._instance.assert_awaited_once_with(INSTANCE)


@pytest.mark.parametrize(
    ("instance", "member_id", "status"),
    [
        # Con el token de otro: no está dentro de esa Activity.
        ({"location": {"guild_id": str(GUILD_ID)}, "users": [str(STRANGER_ID)]}, OWNER_ID, 403),
        # Está en la Activity pero no es del servidor.
        ({"location": {"guild_id": str(GUILD_ID)}, "users": [str(STRANGER_ID)]}, STRANGER_ID, 403),
        # Un mensaje directo sin `ACTIVITY_GUILD_ID`: no hay de qué dinero jugar.
        ({"location": {"kind": "pc"}, "users": [str(OWNER_ID)]}, OWNER_ID, 403),
    ],
)
async def test_el_token_rechaza_lo_que_no_cuadra(
    tmp_path: Path, instance: dict, member_id: int, status: int
) -> None:
    cog, _ = await make_cogs(tmp_path)
    cog._instance.return_value = instance
    cog._whoami.return_value = member_id
    async with TestClient(TestServer(cog.build_app())) as http:
        response = await http.post("/api/token", json={"code": "abc", "instance_id": INSTANCE})
        data = await response.json()

    assert response.status == status
    assert data["error"]
    assert cog.sessions == {}


async def test_fuera_de_los_servidores_del_bot_juega_con_activity_guild_id(
    tmp_path: Path,
) -> None:
    """Un servidor pequeño sin el bot (para no verificar la app): dinero de `GUILD_ID`."""
    cog, _ = await make_cogs(tmp_path, guild_id=GUILD_ID)
    cog._instance.return_value = {
        "location": {"kind": "gc", "guild_id": str(OTHER_GUILD_ID), "channel_id": "30"},
        "users": [str(OWNER_ID)],
    }
    async with TestClient(TestServer(cog.build_app())) as http:
        await login(http)

    assert cog.sessions["token-de-diego"].guild_id == GUILD_ID


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": "abc"},
        {"code": 5, "instance_id": INSTANCE},
        {"code": "x" * 201, "instance_id": "i"},
    ],
)
async def test_el_token_rechaza_peticiones_mal_hechas(tmp_path: Path, body: dict) -> None:
    cog, _ = await make_cogs(tmp_path)
    async with TestClient(TestServer(cog.build_app())) as http:
        response = await http.post("/api/token", json=body)

    assert response.status == 400
    cog._exchange.assert_not_awaited()


async def test_sin_sesion_la_api_pide_permiso_otra_vez(tmp_path: Path) -> None:
    cog, _ = await make_cogs(tmp_path)
    async with TestClient(TestServer(cog.build_app())) as http:
        missing = await http.get("/api/moneda")
        wrong = await http.get("/api/moneda", headers={"Authorization": "Bearer otro"})

    assert missing.status == wrong.status == 401


async def test_una_partida_entera_cobra_y_paga_por_la_economia(tmp_path: Path) -> None:
    """Abrir, acertar cara, cobrar: el dinero va por `place_bet` y `pay_winnings`."""
    cog, coin = await make_cogs(tmp_path, CARA, CARA)
    async with TestClient(TestServer(cog.build_app())) as http:
        headers = await login(http)
        opened = await (await http.get("/api/moneda", headers=headers)).json()
        thrown = await (
            await http.post("/api/moneda", headers=headers, json={"accion": "cara"})
        ).json()
        cashed_response = await http.post("/api/moneda", headers=headers, json={"accion": "cobrar"})
        cashed = await cashed_response.json()
        await asyncio.gather(*cog._finishing)

    assert opened["saldo"] == STARTING_BALANCE
    assert len(opened["states"]) == 1 and opened["botones"]["cobrar"] is None
    # El lanzamiento trae su animación entera y el texto de mientras vuela.
    assert len(thrown["states"]) > 10 and thrown["volando"]
    assert thrown["botones"]["cobrar"] == "💰 Cobrar 200 Y$"
    assert cashed_response.status == 200 and cashed["botones"]["cobrar"] is None
    (view,) = coin.web_tables.values()
    assert view.game.status is Status.CASHED
    balance = await coin.economy.balance(GUILD_ID, OWNER_ID)
    assert cashed["saldo"] == balance
    moves = ledger(tmp_path)
    assert moves[0] == ("moneda:apuesta", -100)
    assert balance == STARTING_BALANCE + sum(delta for _, delta in moves)


async def test_la_apuesta_se_valida_como_en_moneda(tmp_path: Path) -> None:
    cog, coin = await make_cogs(tmp_path)
    async with TestClient(TestServer(cog.build_app())) as http:
        headers = await login(http)
        ok = await http.post(
            "/api/moneda", headers=headers, json={"accion": "apuesta", "cantidad": "50"}
        )
        too_much = await http.post(
            "/api/moneda",
            headers=headers,
            json={"accion": "apuesta", "cantidad": str(STARTING_BALANCE + 1)},
        )
        nonsense = await http.post(
            "/api/moneda", headers=headers, json={"accion": "apuesta", "cantidad": "mucho"}
        )
        unknown = await http.post("/api/moneda", headers=headers, json={"accion": "trampa"})
        stake = (await ok.json())["apuesta"]

    assert ok.status == 200 and stake == 50
    assert too_much.status == nonsense.status == 409
    assert unknown.status == 400
    (view,) = coin.web_tables.values()
    assert view.stake == 50


async def test_un_clic_con_la_moneda_en_el_aire_no_lanza_otra(tmp_path: Path) -> None:
    cog, coin = await make_cogs(tmp_path)
    async with TestClient(TestServer(cog.build_app())) as http:
        headers = await login(http)
        await http.get("/api/moneda", headers=headers)
        (view,) = coin.web_tables.values()
        view._busy = True
        response = await http.post("/api/moneda", headers=headers, json={"accion": "cruz"})
        error = (await response.json())["error"]

    assert response.status == 409
    assert error == BUSY_TEXT
    assert ledger(tmp_path) == []


async def test_la_mesa_de_la_activity_se_cobra_sola_al_caducar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Como la de Discord a los 3 minutos: una racha abandonada se cobra, no se pierde."""
    monkeypatch.setattr(coin_cog, "VIEW_TIMEOUT", 0.05)
    _, coin = await make_cogs(tmp_path, CARA, CARA)
    view = await coin.web_table(GUILD_ID, make_member(), None)
    state, finish = await view.web_flip(Side.CARA)
    assert finish is None and state["botones"]["cobrar"]

    await asyncio.wait_for(coin._web_watchers[(GUILD_ID, OWNER_ID)], timeout=2)

    assert coin.web_tables == {} and view not in coin.views
    assert view.game.status is Status.CASHED
    assert await coin.economy.balance(GUILD_ID, OWNER_ID) > STARTING_BALANCE - 100
