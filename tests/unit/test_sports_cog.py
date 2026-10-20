"""Pruebas de bot.cogs.sports: el panel, apostar, resolver, el bucle y lo pendiente.

Se usa la economía real y el repositorio de partidos real sobre un SQLite temporal, un
reloj falso y los partidos puestos a mano en memoria (`Deportes.matches`). La API de
cuotas se sustituye por un cliente falso. El bucle no se arranca solo: cada prueba llama
a `tick()` o `settle()`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

import bot.repositories.sqlite as sqlite_module
from bot.cogs import sports as cog_module
from bot.cogs.sports import (
    NOT_CONFIGURED,
    RESULT_LINES,
    Deportes,
    Resolved,
    SportsView,
    StakeModal,
    panel_embed,
    pending,
    result_message,
    slip_embed,
    win_streak,
)
from bot.repositories.economy import EconomyRepository
from bot.repositories.sports import SportsRepository
from bot.services.economy import STARTING_BALANCE, EconomyService, SportsBet, format_amount
from bot.services.pending import Side
from bot.services.sports import (
    MAX_STAKE,
    MIN_STAKE,
    RESULT_DELAY,
    SPORTS,
    VOID_AFTER,
    FeedError,
    FeedReply,
    Match,
    Pick,
    Score,
    payout,
)

GUILD_ID = 1
CHANNEL_ID = 555
OTHER_CHANNEL_ID = 556
ANA, LEO = 10, 11
START = 1_800_000_000.0
LIGA = SPORTS[0].key
CHAMPIONS = SPORTS[1].key
ODDS = {Pick.HOME: 185, Pick.DRAW: 360, Pick.AWAY: 410}


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


def make_match(
    event_id: str = "e1", *, sport: str = LIGA, commence: float = START + 3_600
) -> Match:
    return Match(event_id, sport, "Real Madrid", "Barcelona", commence, dict(ODDS))


def make_user(user_id: int, name: str = "Ana") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_channel(channel_id: int = CHANNEL_ID) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.send = AsyncMock()
    return channel


def make_interaction(user_id: int = ANA, *, guild_id: int | None = GUILD_ID) -> MagicMock:
    interaction = fake_interaction(make_user(user_id))
    interaction.guild_id = guild_id
    interaction.channel_id = CHANNEL_ID
    interaction.channel = make_channel()
    interaction.original_response = AsyncMock()
    return interaction


def said(interaction: MagicMock) -> str:
    """Lo último que se avisó con `notify` después de aceptar el clic."""
    return interaction.followup.send.await_args.args[0]


@pytest.fixture(autouse=True)
def hooks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Logros, apuestas y Renta se apuntan sin ir a los cogs: aquí solo se miran las llamadas."""
    found = SimpleNamespace(
        track=AsyncMock(),
        casino_play=AsyncMock(),
        record=AsyncMock(),
        remind=AsyncMock(),
        hint=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(cog_module.logros, "track", found.track)
    monkeypatch.setattr(cog_module.logros, "casino_play", found.casino_play)
    monkeypatch.setattr(cog_module.apuestas, "record", found.record)
    monkeypatch.setattr(cog_module.renta, "remind", found.remind)
    monkeypatch.setattr(cog_module.renta, "hint", found.hint)
    return found


async def make_cog(tmp_path: Path) -> tuple[Deportes, FakeClock]:
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    sports = SportsRepository(tmp_path / "bot.db")
    await sports.initialize()
    clock = FakeClock()
    bot = MagicMock()
    channel = make_channels()
    bot.get_channel = MagicMock(side_effect=lambda channel_id: channel.get(channel_id))
    bot.channels = channel
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda user_id: make_user(user_id))
    bot.get_guild = MagicMock(return_value=guild)
    cog = Deportes(bot, EconomyService(economy_repository), sports, clock=clock)
    cog.matches[LIGA] = [make_match()]
    return cog, clock


def make_channels() -> dict[int, MagicMock]:
    return {CHANNEL_ID: make_channel(CHANNEL_ID), OTHER_CHANNEL_ID: make_channel(OTHER_CHANNEL_ID)}


async def stake_on(
    cog: Deportes,
    user_id: int,
    pick: Pick,
    *,
    stake: int = 100,
    match: Match | None = None,
    channel_id: int = CHANNEL_ID,
) -> SportsBet:
    """Apuesta directamente en la economía (sin pasar por el formulario)."""
    match = match or cog.matches[LIGA][0]
    ticket = SportsBet(
        id=0,
        guild_id=GUILD_ID,
        user_id=user_id,
        channel_id=channel_id,
        event_id=match.event_id,
        sport=match.sport,
        home=match.home,
        away=match.away,
        commence=match.commence,
        pick=pick.value,
        odds=match.odds[pick],
        stake=stake,
        created_at=START,
    )
    bet, _ = await cog.economy.sports_bet(ticket, game="deportes")
    return bet


async def store(cog: Deportes, match: Match, score: Score | None = None) -> None:
    """Guarda el partido (y su marcador) en el repositorio, como si llegaran de la API."""
    await cog.repository.save_odds(match.sport, [match], now=START, remaining=None)
    if score is not None:
        await cog.repository.save_scores(
            match.sport, {match.event_id: score}, now=START, remaining=None
        )


# -- El panel -----------------------------------------------------------------------------


async def test_abrir_deportes_no_toca_la_base_de_datos_y_responde_directo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    connections: list[str] = []
    monkeypatch.setattr(sqlite_module, "connect", lambda *a, **k: connections.append("db"))
    interaction = make_interaction()

    await cog.deportes.callback(cog, interaction)

    assert connections == []
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], SportsView)
    assert "Real Madrid – Barcelona" in kwargs["embed"].description
    interaction.response.defer.assert_not_awaited()


async def test_el_panel_se_abre_en_la_primera_competicion_con_partidos(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.matches[LIGA] = []
    cog.matches[CHAMPIONS] = [make_match("c1", sport=CHAMPIONS)]
    interaction = make_interaction()

    await cog.deportes.callback(cog, interaction)

    assert interaction.response.send_message.await_args.kwargs["view"].sport == CHAMPIONS


async def test_los_botones_del_panel_llevan_custom_id_fijo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    view.event_id = "e1"
    view.rebuild()

    ids = [child.custom_id for child in view.children]

    assert ids == [
        f"deportes:liga:{LIGA}",
        f"deportes:liga:{CHAMPIONS}",
        "deportes:partido",
        "deportes:signo:1",
        "deportes:signo:X",
        "deportes:signo:2",
        "deportes:mis",
    ]


async def test_cambiar_de_competicion_rehace_la_vista_sin_acudir_a_la_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.matches[CHAMPIONS] = [make_match("c1", sport=CHAMPIONS)]
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    view.event_id = "e1"
    monkeypatch.setattr(sqlite_module, "connect", lambda *a, **k: pytest.fail("toca la base"))
    interaction = make_interaction()
    button = next(c for c in view.children if c.custom_id == f"deportes:liga:{CHAMPIONS}")

    await button.callback(interaction)

    assert view.sport == CHAMPIONS
    assert view.event_id is None
    interaction.response.edit_message.assert_awaited_once()
    assert "Champions" in interaction.response.edit_message.await_args.kwargs["embed"].title
    assert "deportes:partido" in [c.custom_id for c in view.children]
    assert not any(c.custom_id.startswith("deportes:signo") for c in view.children)


async def test_elegir_partido_enseña_los_tres_signos_con_su_cuota(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()  # con la clave de la API puesta
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    select = next(c for c in view.children if c.custom_id == "deportes:partido")
    select._values = ["e1"]  # type: ignore[attr-defined]  # noqa: SLF001
    interaction = make_interaction()

    await select.callback(interaction)

    assert view.event_id == "e1"
    interaction.response.edit_message.assert_awaited_once()
    signs = [c for c in view.children if c.custom_id.startswith("deportes:signo")]
    assert [c.custom_id for c in signs] == [
        "deportes:signo:1",
        "deportes:signo:X",
        "deportes:signo:2",
    ]
    assert "×1,85" in signs[0].label
    description = interaction.response.edit_message.await_args.kwargs["embed"].description
    assert "Pulsa un signo para apostar." in description


async def test_un_signo_abre_el_formulario_como_primera_respuesta(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    view.event_id = "e1"
    view.rebuild()
    interaction = make_interaction()
    button = next(c for c in view.children if c.custom_id == "deportes:signo:X")

    await button.callback(interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, StakeModal)
    assert modal.odds == 360


async def test_un_signo_de_un_partido_que_ya_empezo_avisa_sin_formulario(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    view.event_id = "e1"
    view.rebuild()
    clock.now = START + 7_200
    interaction = make_interaction()
    button = next(c for c in view.children if c.custom_id == "deportes:signo:1")

    await button.callback(interaction)

    interaction.response.send_modal.assert_not_awaited()
    assert "ya ha empezado" in interaction.response.send_message.await_args.args[0]


async def test_solo_el_dueño_maneja_el_panel(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    stranger = make_interaction(LEO)
    owner = make_interaction(ANA)

    assert await view.interaction_check(stranger) is False
    assert stranger.response.send_message.await_args.kwargs["ephemeral"] is True
    assert await view.interaction_check(owner) is True
    owner.response.send_message.assert_not_awaited()


async def test_sin_clave_de_la_api_el_panel_dice_que_no_esta_configurado(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    assert cog.client is None
    view = SportsView(cog, owner_id=ANA, sport=LIGA)

    assert NOT_CONFIGURED in view.embed().description

    cog.client = MagicMock()
    assert NOT_CONFIGURED not in view.embed().description


async def test_el_comando_de_texto_manda_el_panel_al_canal(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    ctx = MagicMock()
    ctx.author = make_user(ANA)
    ctx.channel = make_channel()
    ctx.send = AsyncMock()

    await cog.deportes_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    assert isinstance(ctx.send.await_args.kwargs["view"], SportsView)


async def test_fuera_del_canal_del_casino_no_se_abre(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({999})
    interaction = make_interaction()

    await cog.deportes.callback(cog, interaction)

    assert "se juega en <#999>" in interaction.response.send_message.await_args.args[0]


# -- Apostar ------------------------------------------------------------------------------


async def test_apostar_cobra_y_confirma_con_la_cuota_y_lo_que_cobraria(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], Pick.HOME, seen=185, text="100")

    # Aceptado antes de tocar la base de datos.
    interaction.response.defer.assert_awaited_once()
    message = said(interaction)
    assert "×1,85" in message
    assert format_amount(100) in message
    assert format_amount(payout(100, 185)) in message
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100
    [bet] = await cog.economy.open_sports_bets()
    assert (bet.user_id, bet.pick, bet.odds, bet.stake, bet.channel_id) == (
        ANA,
        "1",
        185,
        100,
        CHANNEL_ID,
    )
    hooks.remind.assert_awaited_once()
    hooks.track.assert_awaited_once()


async def test_apostar_a_un_partido_que_ya_empezo_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    clock.now = match.commence + 1
    interaction = make_interaction()

    await cog.place(interaction, match, Pick.HOME, seen=185, text="100")

    assert "ya ha empezado" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    assert await cog.economy.open_sports_bets() == []


async def test_si_la_cuota_cambio_desde_que_se_vio_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    cog.matches[LIGA] = [Match(match.event_id, LIGA, match.home, match.away, match.commence,
                               {**ODDS, Pick.HOME: 170})]  # fmt: skip
    interaction = make_interaction()

    await cog.place(interaction, match, Pick.HOME, seen=185, text="100")

    message = said(interaction)
    assert "La cuota ha cambiado" in message
    assert "×1,70" in message
    assert "×1,85" in message
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_un_signo_que_se_quedo_sin_cuota_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    cog.matches[LIGA] = [Match(match.event_id, LIGA, match.home, match.away, match.commence,
                               {**ODDS, Pick.DRAW: 0})]  # fmt: skip
    interaction = make_interaction()

    await cog.place(interaction, match, Pick.DRAW, seen=360, text="100")

    assert "ya no tiene cuota" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


@pytest.mark.parametrize("text", [str(MIN_STAKE - 1), str(MAX_STAKE + 1), "0", "1m"])
async def test_una_cantidad_fuera_del_rango_avisa_sin_cobrar(tmp_path: Path, text: str) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=2_000_000, reason="prueba")
    before = await cog.economy.balance(GUILD_ID, ANA)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], Pick.HOME, seen=185, text=text)

    assert interaction.followup.send.await_count == 1
    assert await cog.economy.balance(GUILD_ID, ANA) == before
    assert await cog.economy.open_sports_bets() == []


async def test_el_rango_avisa_con_el_minimo_y_el_maximo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], Pick.HOME, seen=185, text="5")

    assert format_amount(MIN_STAKE) in said(interaction)
    assert format_amount(MAX_STAKE) in said(interaction)


async def test_la_apuesta_minima_y_la_maxima_se_cobran(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=100_000, reason="prueba")
    before = await cog.economy.balance(GUILD_ID, ANA)
    cog.matches[LIGA] = [make_match("e1"), make_match("e2")]

    await cog.place(
        make_interaction(), cog.matches[LIGA][0], Pick.HOME, seen=185, text=str(MIN_STAKE)
    )
    await cog.place(
        make_interaction(), cog.matches[LIGA][1], Pick.HOME, seen=185, text=str(MAX_STAKE)
    )

    assert await cog.economy.balance(GUILD_ID, ANA) == before - MIN_STAKE - MAX_STAKE


async def test_all_se_recorta_al_maximo_por_partido(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    before = await cog.economy.balance(GUILD_ID, ANA)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], Pick.HOME, seen=185, text="all")

    [bet] = await cog.economy.open_sports_bets()
    assert bet.stake == MAX_STAKE
    assert await cog.economy.balance(GUILD_ID, ANA) == before - MAX_STAKE


async def test_all_con_poco_saldo_apuesta_todo_lo_que_hay(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)

    await cog.place(make_interaction(), cog.matches[LIGA][0], Pick.HOME, seen=185, text="all")

    [bet] = await cog.economy.open_sports_bets()
    assert bet.stake == STARTING_BALANCE
    assert await cog.economy.balance(GUILD_ID, ANA) == 0


async def test_una_segunda_apuesta_al_mismo_partido_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await cog.place(make_interaction(), match, Pick.HOME, seen=185, text="100")
    interaction = make_interaction()

    await cog.place(interaction, match, Pick.AWAY, seen=410, text="100")

    assert "Ya tienes una apuesta" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100
    assert len(await cog.economy.open_sports_bets()) == 1


async def test_si_no_le_llega_avisa_con_su_saldo_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], Pick.HOME, seen=185, text="5000")

    assert "No te llega" in said(interaction)
    assert format_amount(STARTING_BALANCE) in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_una_cantidad_que_no_se_entiende_avisa(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], Pick.HOME, seen=185, text="mucho")

    assert "No entiendo" in said(interaction)


async def test_no_se_apuesta_fuera_de_un_servidor(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction(guild_id=None)

    await cog.place(interaction, cog.matches[LIGA][0], Pick.HOME, seen=185, text="100")

    assert "dentro de un servidor" in said(interaction)
    assert await cog.economy.open_sports_bets() == []


async def test_el_formulario_apuesta_a_la_cuota_que_vio(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    modal = StakeModal(view, cog.matches[LIGA][0], Pick.AWAY)
    modal.amount._value = "250"  # noqa: SLF001
    interaction = make_interaction()

    await modal.on_submit(interaction)

    [bet] = await cog.economy.open_sports_bets()
    assert (bet.pick, bet.odds, bet.stake) == ("2", 410, 250)


async def test_mis_apuestas_enseña_las_abiertas_en_un_mensaje_privado(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    await stake_on(cog, ANA, Pick.HOME)
    interaction = make_interaction()

    await cog.slip(interaction)

    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs["ephemeral"] is True
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert embed.fields[0].name == "Esperando resultado"
    assert "Real Madrid – Barcelona" in embed.fields[0].value
    hooks.track.assert_awaited_once()


# -- Resolver -----------------------------------------------------------------------------


async def test_settle_paga_a_quien_acierta_y_no_a_quien_falla(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    await stake_on(cog, LEO, Pick.AWAY)
    clock.now = match.commence + RESULT_DELAY

    resolved = await cog.settle(clock.now)

    assert {r.bet.user_id: r.bet.status for r in resolved} == {ANA: "won", LEO: "lost"}
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + payout(100, 185)
    assert await cog.economy.balance(GUILD_ID, LEO) == STARTING_BALANCE - 100
    assert await cog.economy.open_sports_bets() == []


async def test_settle_manda_un_solo_mensaje_por_partido_y_canal_y_menciona_a_quien_cobra(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    await stake_on(cog, LEO, Pick.AWAY)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    channel = cog.bot.channels[CHANNEL_ID]
    channel.send.assert_awaited_once()
    text = channel.send.await_args.args[0]
    assert "Final: Real Madrid 2–1 Barcelona" in text
    assert f"✅ <@{ANA}>" in text
    assert f"❌ <@{LEO}>" in text
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert [user.id for user in mentions.users] == [ANA]
    assert mentions.everyone is False
    assert mentions.roles is False


async def test_settle_manda_un_mensaje_por_cada_canal_donde_se_aposto(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(0, 0))
    await stake_on(cog, ANA, Pick.DRAW, channel_id=CHANNEL_ID)
    await stake_on(cog, LEO, Pick.DRAW, channel_id=OTHER_CHANNEL_ID)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    for channel_id, user_id in ((CHANNEL_ID, ANA), (OTHER_CHANNEL_ID, LEO)):
        channel = cog.bot.channels[channel_id]
        channel.send.assert_awaited_once()
        assert f"<@{user_id}>" in channel.send.await_args.args[0]


async def test_settle_cuenta_el_casino_las_estadisticas_y_los_logros(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    hooks.casino_play.assert_awaited_once()
    assert hooks.casino_play.await_args.kwargs["net"] == payout(100, 185) - 100
    hooks.record.assert_awaited_once()
    record = hooks.record.await_args.kwargs
    assert (record["game"], record["stake"], record["net"]) == ("deportes", 100, 85)


async def test_settle_sin_resultado_antes_de_void_after_no_hace_nada(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + VOID_AFTER - 1

    assert await cog.settle(clock.now) == []

    cog.bot.channels[CHANNEL_ID].send.assert_not_awaited()
    assert len(await cog.economy.open_sports_bets()) == 1
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100


async def test_settle_no_hace_nada_con_un_partido_que_no_ha_empezado(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence - 1

    assert await cog.settle(clock.now) == []

    assert len(await cog.economy.open_sports_bets()) == 1


async def test_settle_anula_y_devuelve_lo_que_pasa_void_after_sin_resultado(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + VOID_AFTER

    resolved = await cog.settle(clock.now)

    assert [r.bet.status for r in resolved] == ["void"]
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    channel = cog.bot.channels[CHANNEL_ID]
    channel.send.assert_awaited_once()
    assert "no tiene resultado" in channel.send.await_args.args[0]
    assert f"<@{ANA}> recupera" in channel.send.await_args.args[0]
    assert [u.id for u in channel.send.await_args.kwargs["allowed_mentions"].users] == [ANA]
    # Anular no es una jugada del casino.
    hooks.casino_play.assert_not_awaited()
    hooks.record.assert_not_awaited()
    hooks.track.assert_awaited_once()


async def test_settle_dos_veces_no_paga_dos_veces(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await cog.settle(clock.now)
    balance = await cog.economy.balance(GUILD_ID, ANA)

    assert await cog.settle(clock.now) == []

    assert await cog.economy.balance(GUILD_ID, ANA) == balance
    cog.bot.channels[CHANNEL_ID].send.assert_awaited_once()


async def test_settle_sigue_si_no_puede_anunciar_en_el_canal(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    cog.bot.channels[CHANNEL_ID].send.side_effect = discord.HTTPException(
        MagicMock(status=500), "caído"
    )
    clock.now = match.commence + RESULT_DELAY

    resolved = await cog.settle(clock.now)

    assert len(resolved) == 1
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + payout(100, 185)


# -- El bucle -----------------------------------------------------------------------------


class FakeOdds:
    """Sustituye a `OddsClient`: apunta qué se pidió y contesta lo que se le dé."""

    def __init__(self) -> None:
        self.odds_calls: list[str] = []
        self.scores_calls: list[tuple[str, int]] = []
        self.odds_reply: FeedReply | FeedError = FeedReply([], 400)
        self.scores_reply: FeedReply | FeedError = FeedReply([], 398)

    async def odds(self, sport: str, *, now: float) -> FeedReply:
        self.odds_calls.append(sport)
        if isinstance(self.odds_reply, FeedError):
            raise self.odds_reply
        return self.odds_reply

    async def scores(self, sport: str, *, days: int) -> FeedReply:
        self.scores_calls.append((sport, days))
        if isinstance(self.scores_reply, FeedError):
            raise self.scores_reply
        return self.scores_reply


@pytest.fixture
def parsed_matches(monkeypatch: pytest.MonkeyPatch) -> list[Match]:
    """Lo que `parse_odds` saca de la respuesta de cuotas (la respuesta no se mira)."""
    found: list[Match] = [make_match("nuevo", commence=START + 7_200)]
    monkeypatch.setattr(cog_module, "parse_odds", lambda events, *, now: list(found))
    return found


async def test_tick_pide_cuotas_la_primera_vez_y_no_en_la_siguiente_vuelta(
    tmp_path: Path, parsed_matches: list[Match]
) -> None:
    cog, clock = await make_cog(tmp_path)
    cog.matches.clear()
    feed = cog.client = FakeOdds()  # type: ignore[assignment]

    await cog.tick()

    assert feed.odds_calls == [LIGA, CHAMPIONS]
    assert [m.event_id for m in cog.matches[LIGA]] == ["nuevo"]
    assert cog.odds_at[LIGA] == START
    clock.now += 300

    await cog.tick()

    assert feed.odds_calls == [LIGA, CHAMPIONS]
    assert feed.scores_calls == []


async def test_tick_vuelve_a_pedir_cuotas_cuando_toca(
    tmp_path: Path, parsed_matches: list[Match]
) -> None:
    from bot.services.sports import ODDS_EVERY

    cog, clock = await make_cog(tmp_path)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    await cog.tick()
    clock.now += ODDS_EVERY

    await cog.tick()

    assert feed.odds_calls == [LIGA, CHAMPIONS] * 2


async def test_tick_sin_cliente_no_pide_nada_pero_resuelve(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + VOID_AFTER

    await cog.tick()

    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def _fresh_odds(cog: Deportes, now: float) -> None:
    """Las cuotas de las dos competiciones acaban de pedirse: no tocan."""
    for sport in SPORTS:
        await cog.repository.mark_checked(sport.key, "odds_at", now=now)


def _final(match: Match, home: int, away: int) -> dict:
    return {
        "id": match.event_id,
        "completed": True,
        "home_team": match.home,
        "away_team": match.away,
        "scores": [
            {"name": match.home, "score": str(home)},
            {"name": match.away, "score": str(away)},
        ],
    }


async def test_tick_pide_resultados_si_hay_apuestas_de_un_partido_que_ya_acabo(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match)
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    feed.scores_reply = FeedReply([_final(match, 2, 1)], 300)

    await cog.tick()

    # Solo la competición de la apuesta, y la apuesta se resuelve en la misma vuelta.
    assert feed.scores_calls == [(LIGA, 1)]
    assert feed.odds_calls == []
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + payout(100, 185)


async def test_tick_no_pide_resultados_antes_de_result_delay(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY - 1
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]

    await cog.tick()

    assert feed.scores_calls == []


async def test_tick_no_pide_resultados_sin_apuestas(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    clock.now = cog.matches[LIGA][0].commence + RESULT_DELAY + 600
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]

    await cog.tick()

    assert feed.scores_calls == []


async def test_tick_no_repite_la_consulta_de_resultados_en_la_vuelta_siguiente(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match)
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    await cog.tick()
    clock.now += 300

    await cog.tick()

    assert feed.scores_calls == [(LIGA, 1)]


async def test_un_error_de_la_api_de_cuotas_no_rompe_la_vuelta_y_se_apunta_como_hecha(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    feed.odds_reply = FeedError("La API de cuotas contesta 500")

    await cog.tick()

    assert feed.odds_calls == [LIGA, CHAMPIONS]
    for sport in SPORTS:
        assert (await cog.repository.feed(sport.key)).odds_at == START
    clock.now += 300
    await cog.tick()
    assert feed.odds_calls == [LIGA, CHAMPIONS]


async def test_un_error_de_la_api_de_resultados_no_rompe_la_vuelta_y_se_apunta_como_hecha(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match)
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    feed.scores_reply = FeedError("La API de cuotas no responde (TimeoutError)")

    await cog.tick()

    assert (await cog.repository.feed(LIGA)).scores_at == clock.now
    assert len(await cog.economy.open_sports_bets()) == 1
    clock.now += 300
    await cog.tick()
    assert feed.scores_calls == [(LIGA, 1)]


async def test_el_bucle_se_traga_los_errores_de_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    monkeypatch.setattr(cog, "tick", AsyncMock(side_effect=RuntimeError("boom")))

    await cog._feed.coro(cog)  # noqa: SLF001

    cog.tick.assert_awaited_once()


# -- saldo --------------------------------------------------------------------------------


async def test_pending_da_una_linea_retenida_por_apuesta_abierta(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    cog.matches[LIGA] = [match, make_match("e2", commence=START + 9_000)]
    await stake_on(cog, ANA, Pick.HOME, stake=100)
    await stake_on(cog, ANA, Pick.DRAW, stake=250, match=cog.matches[LIGA][1])
    await stake_on(cog, LEO, Pick.AWAY)
    cog.bot.get_cog = MagicMock(return_value=cog)

    items = await pending(cog.bot, GUILD_ID, ANA)

    assert sorted((i.amount, i.when) for i in items) == [
        (100, match.commence),
        (250, START + 9_000),
    ]
    assert {(i.side, i.kind) for i in items} == {(Side.HELD, "sports_bet")}
    first = next(i for i in items if i.amount == 100)
    assert "1 (Real Madrid)" in first.text
    assert format_amount(payout(100, 185)) in first.text
    assert f"<t:{int(match.commence)}:R>" in first.line()


async def test_pending_no_cuenta_las_apuestas_resueltas(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await cog.settle(clock.now)
    cog.bot.get_cog = MagicMock(return_value=cog)

    assert await pending(cog.bot, GUILD_ID, ANA) == []


async def test_pending_sin_el_cog_cargado_da_una_lista_vacia() -> None:
    bot = MagicMock()
    bot.get_cog = MagicMock(return_value=None)

    assert await pending(bot, GUILD_ID, ANA) == []


# -- Funciones puras ----------------------------------------------------------------------


def make_bet(
    user_id: int = ANA,
    *,
    pick: str = "1",
    status: str = "won",
    stake: int = 100,
    odds: int = 185,
    bet_id: int = 1,
    settled_at: float | None = START,
    sport: str = LIGA,
) -> SportsBet:
    won = status == "won"
    return SportsBet(
        id=bet_id,
        guild_id=GUILD_ID,
        user_id=user_id,
        channel_id=CHANNEL_ID,
        event_id="e1",
        sport=sport,
        home="Real Madrid",
        away="Barcelona",
        commence=START,
        pick=pick,
        odds=odds,
        stake=stake,
        created_at=START,
        status=status,
        payout=payout(stake, odds) if won else (stake if status == "void" else 0),
        settled_at=settled_at,
    )


def test_result_message_cuenta_el_marcador_quien_cobra_y_quien_pierde() -> None:
    settlement = SimpleNamespace(tax_delta=12)
    text = result_message(
        [
            Resolved(make_bet(ANA), Score(2, 1), settlement, 0),  # type: ignore[arg-type]
            Resolved(make_bet(LEO, pick="2", status="lost"), Score(2, 1), settlement, 0),  # type: ignore[arg-type]
        ]
    )

    lines = text.splitlines()
    assert lines[0] == "⚽ **Final: Real Madrid 2–1 Barcelona** · 🇪🇸 LaLiga"
    assert f"✅ <@{ANA}> 1 (Real Madrid) a ×1,85: cobra **{format_amount(185)}**" in lines[1]
    assert f"(+{format_amount(85)})" in lines[1]
    assert f"IRPF −{format_amount(12)}" in lines[1]
    assert lines[2] == f"❌ <@{LEO}> 2 (Barcelona) a ×1,85: pierde {format_amount(100)}"


def test_result_message_sin_irpf_no_pone_la_retencion() -> None:
    settlement = SimpleNamespace(tax_delta=0)

    text = result_message([Resolved(make_bet(), Score(1, 0), settlement, 0)])  # type: ignore[arg-type]

    assert "IRPF" not in text


def test_result_message_de_una_anulada_dice_que_se_devuelve() -> None:
    text = result_message([Resolved(make_bet(status="void"), None, None, 0)])

    assert "no tiene resultado" in text
    assert f"<@{ANA}> recupera {format_amount(100)}" in text


def test_result_message_corta_la_lista_y_cuenta_el_resto() -> None:
    resolved = [
        Resolved(make_bet(user_id, status="lost", pick="2"), Score(1, 0), None, 0)
        for user_id in range(100, 100 + RESULT_LINES + 3)
    ]

    text = result_message(resolved)

    assert text.count("❌") == RESULT_LINES
    assert text.splitlines()[-1] == "-# Y 3 apuestas más."


def test_result_message_con_una_competicion_desconocida_no_falla() -> None:
    text = result_message([Resolved(make_bet(sport="otra"), Score(1, 0), None, 0)])

    assert "· otra" in text


def test_slip_embed_separa_esperando_resultado_y_resueltas() -> None:
    bets = [
        make_bet(bet_id=1, status="open", settled_at=None),
        make_bet(bet_id=2, status="won"),
        make_bet(bet_id=3, status="lost"),
        make_bet(bet_id=4, status="void"),
    ]

    embed = slip_embed(bets, "Ana")

    assert "Ana" in embed.title
    assert [f.name for f in embed.fields] == ["Esperando resultado", "Últimas resueltas"]
    assert f"cobrarías {format_amount(185)}" in embed.fields[0].value
    done = embed.fields[1].value.splitlines()
    assert f"cobraste {format_amount(185)}" in done[0] and done[0].startswith("✅")
    assert done[1].startswith("❌")
    assert "anulada y devuelta" in done[2] and done[2].startswith("↩️")


def test_slip_embed_sin_apuestas_invita_a_apostar() -> None:
    embed = slip_embed([], "Ana")

    assert not embed.fields
    assert "Aún no has apostado" in embed.description


def test_win_streak_cuenta_las_acertadas_seguidas_desde_la_ultima() -> None:
    bets = [
        make_bet(bet_id=1, status="won", settled_at=1),
        make_bet(bet_id=2, status="lost", settled_at=2),
        make_bet(bet_id=3, status="won", settled_at=3),
        make_bet(bet_id=4, status="won", settled_at=4),
    ]

    assert win_streak(bets) == 2
    assert win_streak(list(reversed(bets))) == 2


def test_win_streak_ignora_las_anuladas_y_las_abiertas_y_se_corta_con_una_perdida() -> None:
    bets = [
        make_bet(bet_id=1, status="won", settled_at=1),
        make_bet(bet_id=2, status="void", settled_at=2),
        make_bet(bet_id=3, status="won", settled_at=3),
        make_bet(bet_id=4, status="open", settled_at=None),
        make_bet(bet_id=5, status="lost", settled_at=0),
    ]

    assert win_streak(bets) == 2
    assert win_streak([make_bet(status="lost")]) == 0
    assert win_streak([]) == 0


def test_panel_embed_sin_configurar_solo_dice_que_falta_la_clave() -> None:
    embed = panel_embed(LIGA, [make_match()], None, configured=False, odds_at=None)

    assert NOT_CONFIGURED in embed.description
    assert "Real Madrid" not in embed.description


def test_panel_embed_sin_partidos_lo_dice() -> None:
    embed = panel_embed(CHAMPIONS, [], None, configured=True, odds_at=None)

    assert "No hay partidos con cuotas" in embed.description
    assert "Champions" in embed.title


def test_panel_embed_lista_los_partidos_con_sus_cuotas_y_cuando_llegaron() -> None:
    matches = [make_match(f"e{n}") for n in range(cog_module.LISTED + 2)]

    embed = panel_embed(LIGA, matches, None, configured=True, odds_at=START)

    assert "1 ×1,85 · X ×3,60 · 2 ×4,10" in embed.description
    assert "Y 2 partidos más en el desplegable." in embed.description
    assert f"<t:{int(START)}:R>" in embed.description


def test_panel_embed_de_un_partido_enseña_lo_que_cobrarian_100_y_los_signos_sin_cuota() -> None:
    match = Match("e1", LIGA, "Real Madrid", "Barcelona", START + 3_600, {**ODDS, Pick.DRAW: 0})

    embed = panel_embed(LIGA, [match], match, configured=True, odds_at=None)

    assert f"100 Y$ cobran {format_amount(185)}" in embed.description
    assert "`X` Empate · sin cuota" in embed.description
    assert "Pulsa un signo para apostar." in embed.description
    assert f"<t:{int(match.commence)}:F>" in embed.description
