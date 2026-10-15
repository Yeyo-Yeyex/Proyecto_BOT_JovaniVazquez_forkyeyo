"""Las funciones puente entre cogs encuentran su cog con el bot cargado de verdad.

`load_extension` vuelve a ejecutar el módulo de cada extensión, así que los
cogs que importaron `bot.cogs.achievements`, `bot.cogs.renta` o
`bot.cogs.shop` antes de que se cargaran tienen una copia antigua del módulo.
Las pruebas unitarias crean los cogs a mano y no lo ven; aquí se carga todo
con `INITIAL_EXTENSIONS`, en el mismo orden que en producción.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from coin_fakes import Scripted
from craps_fakes import Scripted as DiceScripted
from interaction_fakes import fake_interaction
from render_fakes import use_fake_drawings

from bot.app import INITIAL_EXTENSIONS, BotClient
from bot.repositories.economy import LedgerEntry
from bot.services.blackjack import Card
from bot.services.coin import Outcome
from bot.services.enraya import Board, Disc
from bot.services.skins import SKIN_BY_KEY

GUILD_ID = 1
GUILD = GUILD_ID
OWNER_ID = 10
GAMES = (
    "Casino",
    "Blackjack",
    "Tragaperras",
    "Botes",
    "Crash",
    "Minas",
    "Torre",
    "Guerra",
    "Pollo",
    "Moneda",
    "Dados",
    "Pachinko",
    "Caballos",
    "Bacará",
    "Gallos",
    "Enraya",
    "Backgammon",
    "Porras",
    "Loteria",
)


@pytest.fixture(autouse=True)
def fake_drawings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin dibujar las máquinas del casino: ninguna aserción de aquí mira una imagen.

    Estas pruebas comprueban que lo jugado llega a logros, apuestas y Renta. El GIF de
    la tragaperras, la ruleta o el pachinko (y su precalentamiento al cargar el cog) no
    cambia nada de eso y costaba 1-3 s por prueba. Cogs, bot y base de datos siguen siendo
    los reales; solo se sustituye el dibujo (ver `render_fakes`).
    """
    use_fake_drawings(monkeypatch)


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "message_stats.sqlite3")
    await client.message_stats.initialize()
    await client.economy.repository.initialize()
    await client.casino_stats.initialize()
    await client.birthdays.initialize()
    await client.achievements.initialize()
    await client.welcome.initialize()
    await client.shop.initialize()
    await client.housing.initialize()
    await client.work.repository.initialize()
    await client.horses.initialize()
    await client.gallos.initialize()
    await client.enraya.initialize()
    await client.backgammon.initialize()
    await client.poker.initialize()
    await client.porras.initialize()
    await client.slots_repository.initialize()
    for extension in INITIAL_EXTENSIONS:
        await client.load_extension(extension)
    return client


def importer(cog_name: str, client: BotClient):  # noqa: ANN201
    """Módulo (el que quedó registrado) del cog `cog_name`."""
    cog = client.get_cog(cog_name)
    assert cog is not None
    return sys.modules[type(cog).__module__]


async def test_los_juegos_que_cargan_antes_encuentran_logros_y_renta(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        for game in GAMES:
            module = importer(game, client)
            assert module.logros._cog(client) is client.get_cog("Achievements"), game
        renta_cog = client.get_cog("Renta")
        renta_cog.hint_for = AsyncMock(return_value="📬 Tienes la renta pendiente")
        for game in GAMES:
            module = importer(game, client)
            hint = await module.renta.hint(client, GUILD_ID, OWNER_ID)
            assert hint == "📬 Tienes la renta pendiente", game
    finally:
        await client.close()


async def test_los_juegos_que_cargan_antes_apuntan_sus_jugadas_en_apuestas(
    tmp_path: Path,
) -> None:
    """`apuestas` carga después que los juegos: su función puente tiene que verlo."""
    client = await load_bot(tmp_path)
    try:
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        for game in GAMES[:-1]:  # la lotería no es una jugada del casino
            module = importer(game, client)
            await module.apuestas.record(
                client, GUILD_ID, owner, game="ruleta", stake=100, net=-100, balance_after=0, tax=0
            )
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.total.plays == len(GAMES) - 1
    finally:
        await client.close()


async def test_los_niveles_ven_los_potenciadores_de_la_tienda(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        client.get_cog("Tienda").xp_multiplier = MagicMock(return_value=2.0)
        tienda = importer("MessageStats", client).tienda
        assert tienda.xp_multiplier(client, GUILD_ID, OWNER_ID, 0.0) == 2.0
    finally:
        await client.close()


async def test_rafaga_de_la_tragaperras_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """El fallo de producción: diez tiradas de la Ráfaga jugadas y ninguna en los logros."""
    client = await load_bot(tmp_path)
    try:
        slots = importer("Tragaperras", client)
        slots.REVEAL_MARGIN_SECONDS = 0
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        view = slots.SlotMachineView(
            client.get_cog("Tragaperras"), guild_id=GUILD_ID, owner=owner, stake=1
        )
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        view.message = MagicMock()
        view.message.channel = channel
        view.cog.renderer = MagicMock()
        view.cog.renderer.still_png = MagicMock(return_value=b"PNG")
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._burst(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["slots_spins"] == view.session_spins == slots.BURST_SPINS
        assert profile.stats["slots_auto"] == 1
        assert {"slots_1", "auto_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["tragaperras"].plays + plays.by_game["tragaperras"].free_plays == (
            slots.BURST_SPINS
        )
    finally:
        await client.close()


async def test_auto_de_la_tragaperras_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """▶️ Auto con el bot cargado: cada tirada y la sesión llegan a los logros."""
    client = await load_bot(tmp_path)
    try:
        slots = importer("Tragaperras", client)
        slots.REVEAL_MARGIN_SECONDS = 0
        slots.AUTOPLAY_MIN_GAP = 0
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        cog = client.get_cog("Tragaperras")
        view = slots.SlotMachineView(cog, guild_id=GUILD_ID, owner=owner, stake=1)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        view.message = MagicMock()
        view.message.channel = channel
        view.message.edit = AsyncMock()
        cog.renderer = MagicMock()
        cog.renderer.render = MagicMock(
            return_value=slots.SlotsMedia(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._autoplay_click(interaction)
        await view.autoplay.task

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert view.session_spins >= 1
        assert profile.stats["slots_spins"] == profile.stats["slots_autoplay_spins"]
        assert profile.stats["slots_autoplay_spins"] == view.session_spins
        assert profile.stats["slots_autoplay_sessions"] == 1
        assert {"slots_1", "autoplay_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        played = plays.by_game["tragaperras"]
        assert played.plays + played.free_plays == view.session_spins
    finally:
        await client.close()


async def test_auto_del_pachinko_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """▶️ Auto del pachinko con el bot cargado, con precarga: cada tanda y la sesión llegan."""
    client = await load_bot(tmp_path)
    try:
        pachinko = importer("Pachinko", client)
        pachinko.REVEAL_MARGIN_SECONDS = 0
        pachinko.AUTOPLAY_MIN_GAP = 0
        # Cuatro tandas bastan para probar la precarga (se prepara la siguiente mientras se
        # juega la actual): las 25 de un Auto completo cuestan ~75 ms de física cada una.
        pachinko.AUTOPLAY_MAX = 4
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        cog = client.get_cog("Pachinko")
        view = pachinko.PachinkoView(cog, guild_id=GUILD_ID, owner=owner, stake=10)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        view.message = MagicMock()
        view.message.channel = channel
        view.message.edit = AsyncMock()
        cog.machines.add(view)
        cog.renderer = MagicMock()
        cog.renderer.render = MagicMock(
            return_value=pachinko.PachinkoMedia(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._autoplay_click(interaction)
        await view.autoplay.task

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert view.session_volleys >= 1
        assert profile.stats["pachinko_volleys"] == view.session_volleys
        assert profile.stats["pachinko_autoplay_volleys"] == view.session_volleys
        assert profile.stats["pachinko_autoplay_sessions"] == 1
        assert {"pachi_1", "pachi_auto_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["pachinko"].plays == view.session_volleys
    finally:
        await client.close()


async def test_el_pollo_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """El Pollo carga antes que los logros: sus partidas deben llegar a `logros`."""
    client = await load_bot(tmp_path)
    try:
        pollo = importer("Pollo", client)
        pollo.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Pollo")
        cog.renderer = MagicMock()
        cog.renderer.hops = AsyncMock(return_value=pollo.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        cog.renderer.close = AsyncMock()
        # Con un dibujo falso, pintar por adelantado contaría dibujos de más.
        cog.ahead = False
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._pollo_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            difficulty=None,
            auto=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (view,) = cog.views
        view.game.hit_lane = 1
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._cross(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["chicken_games"] == 1
        assert profile.stats["chicken_splats"] == 1
        assert {"pollo_1", "pollos_1", "pollo_ni_acera"} <= set(profile.unlocked)
    finally:
        await client.close()


async def test_la_moneda_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """La moneda carga antes que los logros: sus partidas deben llegar a `logros`."""
    client = await load_bot(tmp_path)
    try:
        moneda = importer("Moneda", client)
        moneda.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Moneda")
        cog.renderer = MagicMock()
        cog.renderer.toss = AsyncMock(
            return_value=moneda.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        # Con un dibujo falso, pintar por adelantado gastaría el azar de guion.
        cog.ahead = False
        cog.rng = Scripted(Outcome.CRUZ, Outcome.CARA)  # pide cara, sale cruz
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._moneda_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            pick=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (view,) = cog.views
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._flip_cara(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["coin_games"] == 1
        assert profile.stats["coin_losses"] == 1
        assert "moneda_1" in profile.unlocked
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["moneda"].plays == 1
    finally:
        await client.close()


async def test_los_dados_apuntan_sus_logros_y_jugadas_con_el_bot_real(tmp_path: Path) -> None:
    """Los dados cargan antes que los logros: sus partidas deben llegar a `logros`."""
    client = await load_bot(tmp_path)
    try:
        dados = importer("Dados", client)
        dados.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Dados")
        cog.renderer = MagicMock()
        cog.renderer.throw = AsyncMock(
            return_value=dados.Media(gif=b"GIF", png=b"PNG", seconds=0.0, rest=dados.OPENING_REST)
        )
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        cog.rng = DiceScripted((1, 1))  # pide pase, sale pifia
        # Con un dibujo falso, pintar por adelantado gastaría el azar de guion.
        cog.ahead = False
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._dados_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            bet=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (view,) = cog.views
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._pass(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["dice_games"] == 1
        assert profile.stats["dice_losses"] == 1
        assert profile.stats["dice_snake_eyes"] == 1
        assert {"dados_1", "dados_pegasus", "dados_pifia"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["dados"].plays == 1
    finally:
        await client.close()


async def test_las_carreras_apuntan_sus_logros_y_jugadas_con_el_bot_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los caballos cargan antes que los logros y `apuestas`: sus boletos deben llegar."""
    from bot.services.horses import SEGMENTS, RaceResult, estimate

    client = await load_bot(tmp_path)
    try:
        caballos = importer("Caballos", client)
        # Las cuotas salen de simular la carrera `ODDS_TRIALS` (80.000) veces, ~0,7 s. Aquí
        # basta con unas pocas: la prueba apunta una apuesta acertada, no mira la cuota.
        monkeypatch.setattr(caballos, "estimate", lambda card, rng: estimate(card, rng, 2_000))
        cog = client.get_cog("Caballos")
        cog.renderer = MagicMock()
        cog.renderer.card = AsyncMock(return_value=b"PNG")
        cog.renderer.ticket = AsyncMock(return_value=b"PNG")
        cog.renderer.race = AsyncMock(
            return_value=caballos.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        cog.sleep = AsyncMock()
        cog.start = MagicMock()
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 77
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        await cog._caballo_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=channel,
            user=owner,
            amount_text="100",
            pick_text="1",
            kind_text=None,
            confirm=AsyncMock(),
            send_error=AsyncMock(),
        )
        race = cog.races[77]
        times = tuple(100.0 + i for i in range(race.card.size))
        splits = tuple(tuple(t * s / SEGMENTS for s in range(SEGMENTS + 1)) for t in times)
        result = RaceResult(order=tuple(range(race.card.size)), times=times, splits=splits)
        monkeypatch.setattr(caballos, "run_race", lambda card, rng: result)

        await race.race()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["horse_bets"] == 1
        assert profile.stats["horse_hits"] == 1
        assert {"caballo_1", "caballo_hit_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["caballos"].plays == 1
    finally:
        await client.close()


async def test_el_bacara_apunta_sus_logros_y_jugadas_con_el_bot_real(tmp_path: Path) -> None:
    """El bacará carga antes que los logros y `apuestas`: cada mano jugada debe llegar."""
    from bot.services.bacara import Coup
    from bot.services.blackjack import Card

    client = await load_bot(tmp_path)
    try:
        bacara = importer("Bacará", client)
        cog = client.get_cog("Bacará")
        cog.ahead = False
        cog.renderer = MagicMock()
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        cog.renderer.coup = AsyncMock(
            return_value=bacara.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        cog.sleep = AsyncMock()
        cog.start = MagicMock()
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 78
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        await cog._bacara_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=channel,
            user=owner,
            amount_text="100",
            side_text="banca",
            confirm=AsyncMock(),
            send_error=AsyncMock(),
        )
        table = cog.tables[78]
        # Banca 9 de salida contra Jugador 3: natural de 9 para la Banca.
        table.state.pending = Coup((Card(1, 0), Card(2, 1)), (Card(4, 2), Card(5, 3)))

        await table.deal()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["bac_hands"] == 1
        assert profile.stats["bac_banker_wins"] == 1
        assert profile.stats["bac_nine_wins"] == 1
        assert {"bac_1", "bac_natural", "bac_nueve", "bac_estreno"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["bacara"].plays == 1
    finally:
        await client.close()


async def test_auto_de_los_botes_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """Las máquinas de Botes cargan antes que los logros: sus tiradas deben llegar."""
    client = await load_bot(tmp_path)
    await client.hold_win.initialize()
    try:
        botes = importer("Botes", client)
        botes.REVEAL_MARGIN_SECONDS = 0
        botes.BONUS_INTRO_PAUSE = 0
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        cog = client.get_cog("Botes")
        view = botes.HoldWinView(
            cog, theme=botes.THEMES["volcan"], guild_id=GUILD_ID, owner=owner, stake=10
        )
        view.message = None
        cog.renderer = MagicMock()
        cog.renderer.base_still = MagicMock(return_value=b"PNG")
        cog.renderer.bonus_still = MagicMock(return_value=b"PNG")
        interaction = fake_interaction()
        interaction.user = owner

        await view._auto(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["botes_spins"] == view.session_spins
        assert profile.stats["botes_spins_volcan"] == view.session_spins
        assert profile.stats["botes_auto"] == 1
        assert "botes_1" in profile.unlocked
    finally:
        await client.close()


async def test_bizum_apunta_a_espaldas_de_sanchez_con_el_bot_real(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        bizum = importer("Bizum", client)
        assert bizum.logros._cog(client) is client.get_cog("Achievements")
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(50_000, "test")])
        sender = MagicMock(spec=discord.Member)
        sender.id, sender.bot, sender.display_name = OWNER_ID, False, "Diego"
        receiver = MagicMock(spec=discord.Member)
        receiver.id, receiver.bot, receiver.display_name = 20, False, "Ana"
        receiver.mention = "<@20>"
        ctx = MagicMock()
        ctx.guild = MagicMock(id=GUILD)
        ctx.author = sender
        ctx.channel = None
        ctx.send = AsyncMock()

        cog = client.get_cog("Bizum")
        await cog.bizum_text.callback(cog, ctx, receiver, "10001")

        mine = await client.achievements.profile(GUILD, OWNER_ID)
        theirs = await client.achievements.profile(GUILD, 20)
        assert "bizum_espaldas" in mine.unlocked
        assert theirs.stats["bizum_received"] == 10_001
    finally:
        await client.close()


async def test_la_lista_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """`lista` carga antes que los logros: apuntar y tachar deben llegar a ellos."""
    client = await load_bot(tmp_path)
    await client.todo.initialize()
    try:
        lista = client.get_cog("Lista")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        owner.guild_permissions = discord.Permissions.none()
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        guild.get_member = MagicMock(return_value=owner)
        owner.guild = guild
        ctx = MagicMock()
        ctx.guild = guild
        ctx.author = owner
        ctx.message.delete = AsyncMock()
        ctx.channel = MagicMock(spec=discord.TextChannel)
        ctx.channel.send = AsyncMock(return_value=MagicMock(id=5, channel=MagicMock(id=6)))

        await lista.lista_text.callback(lista, ctx, tarea="probar la lista prioridad alta")
        (task,) = await client.todo.list_tasks(GUILD_ID)
        interaction = fake_interaction()
        interaction.guild = guild
        interaction.user = owner
        interaction.channel = ctx.channel
        await lista.complete_from_menu(interaction, [task.id])

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["todo_added"] == 1
        assert profile.stats["todo_done"] == 1
        assert {"todo_add_1", "todo_done_1"} <= set(profile.unlocked)
    finally:
        await client.close()


async def test_cambios_configura_el_aviso_que_publica_el_despliegue_con_el_bot_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cambios` (cog Admin) guarda los ajustes y el cog Despliegue los usa al publicar."""
    client = await load_bot(tmp_path)
    await client.news.initialize()
    try:
        elegido = MagicMock(spec=discord.TextChannel)
        elegido.id = 77
        elegido.send = AsyncMock()
        elegido.mention = "#anuncios"
        general = MagicMock(spec=discord.TextChannel)
        general.name = "chat-general"
        general.send = AsyncMock()
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        guild.text_channels = [general, elegido]
        guild.get_channel_or_thread = lambda channel_id: elegido if channel_id == 77 else None
        responder = MagicMock()
        responder.guild = guild
        responder.send = AsyncMock()
        responder.send_error = AsyncMock()
        admin = client.get_cog("Admin")

        await admin._cambios_impl(responder, "resumen", 77)

        responder.send_error.assert_not_awaited()
        deploy = client.get_cog("Despliegue")
        buzon = tmp_path / "buzon"
        buzon.mkdir()
        (buzon / "novedades.txt").write_text(
            "#5\tyeyo/pollo\ttitulo\tPollo más rápido\n", encoding="utf-8"
        )
        deploy.mailbox = importer("Despliegue", client).Mailbox(buzon)
        deploy._fetch = AsyncMock(return_value=None)
        monkeypatch.setattr(type(client), "guilds", property(lambda self: [guild]))

        assert await deploy.announce_news() is True

        general.send.assert_not_awaited()
        embeds = elegido.send.await_args.kwargs["embeds"]
        assert len(embeds) == 1, "en resumen no van fichas"
        assert "Pollo más rápido" in embeds[0].description
    finally:
        await client.close()


async def test_un_turno_de_pala_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """`pala` carga antes que los logros: el turno y la nómina deben llegar a ellos."""
    client = await load_bot(tmp_path)
    try:
        work = importer("Trabajo", client)
        assert work.logros._cog(client) is client.get_cog("Achievements")
        cog = client.get_cog("Trabajo")
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        ctx = MagicMock()
        ctx.guild = MagicMock(id=GUILD)
        ctx.author = owner
        ctx.channel = None
        ctx.send = AsyncMock(return_value=MagicMock())
        await cog.pala_text.callback(cog, ctx)
        (panel,) = cog.panels

        def interaction() -> MagicMock:
            fake = fake_interaction()
            fake.user = owner
            fake.message = MagicMock()
            fake.message.edit = AsyncMock()
            return fake

        await panel._hire(interaction(), "obra")
        await panel._clock_in(interaction())
        await panel._finish(None)

        profile = await client.achievements.profile(GUILD, OWNER_ID)
        assert profile.stats["work_shifts"] == 1
        assert profile.stats["work_payslips"] == 1
        assert "pala_1" in profile.unlocked and "primera_nomina" in profile.unlocked
    finally:
        await client.close()


async def test_los_intereses_encuentran_los_logros_con_el_bot_real(tmp_path: Path) -> None:
    """`Intereses` carga antes que los logros: sus avisos deben llegar al cog real."""
    client = await load_bot(tmp_path)
    try:
        module = importer("Intereses", client)
        assert module.logros._cog(client) is client.get_cog("Achievements")
    finally:
        await client.close()


async def test_el_aviso_de_intereses_llega_a_los_juegos_por_la_renta(tmp_path: Path) -> None:
    """`renta.hint` es el hueco por el que todos los juegos cuentan los intereses."""
    client = await load_bot(tmp_path)
    try:
        client.get_cog("Intereses").hint_for = AsyncMock(return_value="🏦 Ayer cobraste")
        for game in GAMES:
            module = importer(game, client)
            assert await module.renta.hint(client, GUILD_ID, OWNER_ID) == "🏦 Ayer cobraste", game
        renta_cog = client.get_cog("Renta")
        renta_cog.hint_for = AsyncMock(return_value="📬 Renta")
        hint = await importer("Casino", client).renta.hint(client, GUILD_ID, OWNER_ID)
        assert hint == "📬 Renta\n🏦 Ayer cobraste"
    finally:
        await client.close()


async def test_usar_un_objeto_apunta_logros_a_los_dos_con_el_bot_real(tmp_path: Path) -> None:
    """Tirar un huevo desde la mochila llega a los logros de quien tira y de quien lo recibe."""
    from bot.services.shop import quote

    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        egg = next(i for i in await client.shop.items(GUILD_ID) if i.catalog_key == "huevo")
        price = quote(egg, tienda.clock())
        await client.economy.purchase(
            GUILD_ID, OWNER_ID, base=price.base, tax=price.tax, concept="objeto",
            reserve=client.shop.reserve(
                GUILD_ID, OWNER_ID, egg.id, expected=price, level=0, now=tienda.clock()
            ),
        )  # fmt: skip

        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        victim = MagicMock(spec=discord.Member)
        victim.id, victim.bot, victim.mention = 11, False, "<@11>"
        backpack = await tienda.backpack_view(guild, owner, owner)
        (entry,) = backpack.entries
        interaction = fake_interaction(owner)
        interaction.channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.send = AsyncMock()
        await tienda.perform_use(interaction, backpack, entry, tienda_use("huevo"), target=victim)

        thrower = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert thrower.stats["shop_uses"] == 1 and thrower.stats["shop_used_huevo"] == 1
        assert {"use_1", "target_1"} <= set(thrower.unlocked)
        target = await client.achievements.profile(GUILD_ID, 11)
        assert target.stats["shop_got_hit"] == 1 and "hit_1" in target.unlocked
    finally:
        await client.close()


def tienda_use(key: str):  # noqa: ANN201
    from bot.services.shop_uses import USES

    return USES[key]


async def test_las_mascotas_llegan_a_todos_con_el_bot_real(tmp_path: Path) -> None:
    """Niveles, Renta, tienda, IMV, logros y cumpleaños cargan antes o después de
    `bot.cogs.pets`: todos tienen que encontrar la mascota activa."""
    from bot.services.pets import BOND_LEVELS, Event, Moment, PetState

    client = await load_bot(tmp_path)
    try:
        pets = client.get_cog("Mascotas")
        assert pets is not None
        pets.active[(GUILD_ID, OWNER_ID)] = PetState(
            id=1, guild_id=GUILD_ID, user_id=OWNER_ID, species="perro", name="Toby",
            bond=BOND_LEVELS[4],
        )  # fmt: skip
        loud = Moment(Event.BIG_WIN)  # sonado y sin apariciones
        for cog_name in ("MessageStats", "Renta", "Tienda", "Casino", "Achievements",
                         "Birthdays"):  # fmt: skip
            module = importer(cog_name, client)
            assert module.mascotas.xp_bonus(client, GUILD_ID, OWNER_ID, voice=False) > 1.0
            line = await module.mascotas.cameo(client, GUILD_ID, OWNER_ID, loud)
            assert line is not None and "**Toby**" in line, cog_name
        # Los juegos llegan a la mascota a través de la Renta.
        client.get_cog("Renta").hint_for = AsyncMock(return_value=None)
        for game in GAMES:
            module = importer(game, client)
            hint = await module.renta.hint(client, GUILD_ID, OWNER_ID, loud)
            assert hint is not None and "**Toby**" in hint, game
    finally:
        await client.close()


async def test_la_pala_ve_las_herramientas_de_la_mochila_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`pala` carga antes que la tienda: su puente tiene que encontrar la mochila."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        items = await client.shop.items(GUILD_ID)
        casco = next(item for item in items if item.catalog_key == "casco_linterna")
        await client.shop.grant(GUILD_ID, OWNER_ID, casco, 0.0)
        work = importer("Trabajo", client)
        owned = await work.tienda.owned_keys(client, GUILD_ID, OWNER_ID)
        assert "casco_linterna" in owned
    finally:
        await client.close()


async def test_la_pala_da_los_regalos_de_la_mochila_con_el_bot_real(tmp_path: Path) -> None:
    """El regalo se compra en la tienda y `pala` lo gasta por su puente."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        items = await client.shop.items(GUILD_ID)
        flores = next(item for item in items if item.catalog_key == "flores_mama")
        await client.shop.grant(GUILD_ID, OWNER_ID, flores, 0.0)
        work = importer("Trabajo", client)
        cog = client.get_cog("Trabajo")
        await cog.service.hire(GUILD_ID, OWNER_ID, "obra")

        async def consume() -> bool:
            return await work.tienda.consume_key(client, GUILD_ID, OWNER_ID, "flores_mama")

        await cog.service.give_gift(GUILD_ID, OWNER_ID, "flores_mama", consume)
        assert "flores_mama" not in await work.tienda.owned_keys(client, GUILD_ID, OWNER_ID)
        assert not await work.tienda.consume_key(client, GUILD_ID, OWNER_ID, "flores_mama")
    finally:
        await client.close()


async def test_la_pala_compra_y_da_un_regalo_en_el_panel_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Un regalo que no tienes se compra y se da desde el desplegable, sin salir del panel."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Trabajo")
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        ctx = MagicMock()
        ctx.guild = MagicMock(id=GUILD)
        ctx.author = owner
        ctx.channel = None
        ctx.send = AsyncMock(return_value=MagicMock())
        await cog.pala_text.callback(cog, ctx)
        (panel,) = cog.panels

        def interaction() -> MagicMock:
            fake = fake_interaction(owner)
            fake.guild = MagicMock(spec=discord.Guild)
            fake.guild.id = GUILD_ID
            fake.message = MagicMock()
            fake.message.edit = AsyncMock()
            return fake

        await panel._hire(interaction(), "obra")
        money = await client.economy.grant(GUILD_ID, OWNER_ID, amount=10_000, reason="prueba")
        await panel._buy_gift(interaction(), "flores_mama")
        assert panel.checkout is not None
        await panel._pay_gift(interaction())

        status = await cog.service.status(GUILD_ID, OWNER_ID)
        assert "flores_mama" in status.contract.family_used  # dado
        after = await client.economy.balance(GUILD_ID, OWNER_ID)
        assert after < money  # cobrado (los logros de la compra devuelven algún premio)
        assert "flores_mama" not in await importer("Trabajo", client).tienda.owned_keys(
            client, GUILD_ID, OWNER_ID
        )
        await panel._pay_gift(interaction())  # el segundo clic no cobra otra vez
        assert await client.economy.balance(GUILD_ID, OWNER_ID) == after
    finally:
        await client.close()


async def test_perfil_fortunas_y_ranking_ven_el_titulo_con_el_bot_real(tmp_path: Path) -> None:
    """Los tres cogs cargan antes que la tienda: su puente tiene que ver el título puesto."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "verificado")
        assert item is not None
        await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
        await tienda.wear_title(GUILD_ID, OWNER_ID, "verificado")
        for cog_name in ("Perfil", "Patrimonio", "MessageStats"):
            module = importer(cog_name, client)
            assert module.tienda.title_of(client, GUILD_ID, OWNER_ID) == "☑️ Cuenta verificada"
    finally:
        await client.close()


async def test_vender_en_el_rastro_quita_el_titulo_y_apunta_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El Rastro carga después de la tienda y antes de los logros: los dos puentes valen."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        rastro = client.get_cog("Rastro")
        module = importer("Rastro", client)
        assert module.logros._cog(client) is client.get_cog("Achievements")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "verificado")
        assert item is not None
        entry, _dupe = await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
        await tienda.wear_title(GUILD_ID, OWNER_ID, "verificado")
        await client.economy.repository.apply(GUILD, 20, [LedgerEntry(100_000, "test")])
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        seller = MagicMock(spec=discord.Member)
        seller.id, seller.bot, seller.display_name = OWNER_ID, False, "Diego"
        buyer = MagicMock(spec=discord.Member)
        buyer.id, buyer.bot, buyer.display_name, buyer.mention = 20, False, "Ana", "<@20>"
        guild.get_member = MagicMock(side_effect={OWNER_ID: seller, 20: buyer}.get)
        client.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
        entries = await client.shop.inventory(GUILD_ID, OWNER_ID, rastro.clock())
        view = module.OfferView(
            rastro, GUILD_ID, seller, buyer, next(e for e in entries if e.id == entry.id), 500
        )
        interaction = fake_interaction(buyer)
        interaction.client = client
        interaction.guild = guild
        interaction.channel = None

        await view.accept.callback(interaction)

        assert tienda.title(GUILD_ID, OWNER_ID) is None
        mine = await client.achievements.profile(GUILD, OWNER_ID)
        theirs = await client.achievements.profile(GUILD, 20)
        assert mine.stats["market_sold"] == 1
        assert theirs.stats["market_bought"] == 1
        assert "rastro_itp_1" in theirs.unlocked
    finally:
        await client.close()


async def test_la_ruleta_pinta_con_la_skin_de_la_tienda_con_el_bot_real(tmp_path: Path) -> None:
    """El casino carga antes que la tienda: su puente tiene que ver la skin puesta."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "skin_ruleta_granate")
        assert item is not None
        await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
        casino = client.get_cog("Casino")
        assert casino.skin(GUILD_ID, OWNER_ID)["key"] == "serie"
        await tienda.wear_skin(GUILD_ID, OWNER_ID, "ruleta", SKIN_BY_KEY["ruleta_granate"])
        assert casino.skin(GUILD_ID, OWNER_ID)["key"] == "ruleta_granate"
        # Los logros piden las skins de regalo a la tienda del bot cargado.
        shop_module = importer("Tienda", client)
        assert await shop_module.grant_reward_skins(client, GUILD_ID, OWNER_ID, ["x"]) == []
    finally:
        await client.close()


async def test_la_imagen_de_pieles_llega_a_las_mesas_y_a_los_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`pieles` carga después de la tienda y los juegos antes: todos ven la misma imagen."""
    import io

    from PIL import Image

    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "skin_tapete_propio")
        assert item is not None
        await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
        await tienda.wear_skin(GUILD_ID, OWNER_ID, "ruleta", SKIN_BY_KEY["tapete_propio"])
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        buffer = io.BytesIO()
        Image.new("RGB", (64, 36), "purple").save(buffer, format="PNG")
        upload = MagicMock(spec=discord.Attachment)
        upload.size = len(buffer.getvalue())
        upload.read = AsyncMock(return_value=buffer.getvalue())

        text = await client.get_cog("Pieles").upload(GUILD_ID, owner, owner, upload, None)

        assert "🖼️" in text
        skin = client.get_cog("Casino").skin(GUILD_ID, OWNER_ID)
        assert str(skin["key"]).startswith("tapete_propio:")
        assert str(skin["image"]).startswith("data:image/jpeg;base64,")
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["shop_skin_image_uploads"] == 1
        assert "skin_image_1" in profile.unlocked
    finally:
        await client.close()


async def test_los_gallos_apuntan_logros_y_jugadas_y_reciben_sus_skins_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Los gallos cargan antes que la tienda y los logros: sus puentes tienen que verlos."""
    client = await load_bot(tmp_path)
    try:
        gallos = importer("Gallos", client)
        gallos.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Gallos")
        cog.renderer = MagicMock()
        cog.renderer.still = AsyncMock(return_value=b"PNG")
        cog.renderer.fight = AsyncMock(
            return_value=gallos.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._gallos_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            rival=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        # Al abrir el reñidero, la tienda le da los gallos gratis.
        mochila = {e.catalog_key for e in await client.shop.inventory(GUILD_ID, OWNER_ID, 0.0)}
        assert {"skin_gallo_blanco", "skin_gallo_negro", "skin_gallo_pinto"} <= mochila
        tienda = client.get_cog("Tienda")
        await tienda.wear_skin(GUILD_ID, OWNER_ID, "gallos", SKIN_BY_KEY["gallo_negro"])
        (panel,) = cog.panels
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await panel._fight(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["gallos_fights"] == 1
        assert profile.stats["gallos_skin_gallo_negro"] == 1
        assert "gallos_1" in profile.unlocked
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["gallos"].plays == 1
    finally:
        await client.close()


RIVAL_ID = 11
#: Las rojas (que salen) ganan en horizontal con su cuarta ficha.
ENRAYA_WIN = [0, 0, 1, 1, 2, 2, 3]


def enraya_member(user_id: int, name: str) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = name
    member.mention = f"<@{user_id}>"
    member.bot = False
    return member


def enraya_cog_with_fake_drawing(client: BotClient):  # noqa: ANN201
    """El 4 en raya del bot real con dibujo falso, sin esperas y sin pintar por adelantado."""
    module = importer("Enraya", client)
    module.REVEAL_MARGIN_SECONDS = 0
    cog = client.get_cog("Enraya")
    cog.ahead = False
    cog.renderer = MagicMock()
    cog.renderer.still = AsyncMock(return_value=b"PNG")
    cog.renderer.animate = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    cog.renderer.close = AsyncMock()
    return cog


def enraya_interaction(member: MagicMock) -> MagicMock:
    interaction = fake_interaction()
    interaction.user = member
    interaction.guild = None
    return interaction


async def test_un_duelo_de_4_en_raya_apunta_logros_y_jugadas_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El 4 en raya carga antes que los logros y `apuestas`: sus puentes tienen que verlos."""
    client = await load_bot(tmp_path)
    try:
        cog = enraya_cog_with_fake_drawing(client)
        owner = enraya_member(OWNER_ID, "Diego")
        rival = enraya_member(RIVAL_ID, "Ana")
        duel = await cog._enraya_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            rival=rival,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        assert duel is not None
        await duel.accept(enraya_interaction(rival))
        for column in ENRAYA_WIN:
            member = duel.member(duel.board.turn)
            await duel.drop(enraya_interaction(member), column)
        assert duel.board.winner is not None

        winner, loser = duel.member(duel.board.winner), duel.member(duel.board.winner.other)
        for member in (winner, loser):
            profile = await client.achievements.profile(GUILD_ID, member.id)
            assert profile.stats["enraya_games"] == 1
            assert profile.stats["enraya_duels"] == 1
            assert profile.stats["enraya_casino_duels"] == 1
            assert "enraya_1" in profile.unlocked
            report = await client.casino_stats.report(
                GUILD_ID, member.id, since=None, today=date(2026, 10, 6)
            )
            assert report.by_game["enraya"].plays == 1
        won = await client.achievements.profile(GUILD_ID, winner.id)
        lost = await client.achievements.profile(GUILD_ID, loser.id)
        assert won.stats["enraya_wins"] == 1 and "enraya_win_1" in won.unlocked
        assert lost.stats["enraya_losses"] == 1 and "enraya_wins" not in lost.stats
        assert won.stats["enraya_win_h"] == 1
    finally:
        await client.close()


async def test_una_partida_contra_jovani_apunta_logros_y_la_ficha_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Sin dinero de por medio: los logros y la ficha cuentan igual, `apuestas` no."""
    client = await load_bot(tmp_path)
    try:
        cog = enraya_cog_with_fake_drawing(client)
        owner = enraya_member(OWNER_ID, "Diego")
        await cog._enraya_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text=None,
            rival=None,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        (panel,) = cog.panels
        panel.board = Board()
        for column in ENRAYA_WIN[:-1]:
            panel.board = panel.board.play(column)
        panel.rebuild()
        interaction = enraya_interaction(owner)

        button = next(i for i in panel.children if i.custom_id == "enraya:col3")
        await button.callback(interaction)

        assert panel.over and panel.board.winner is Disc.RED
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["enraya_games"] == 1 and profile.stats["enraya_wins"] == 1
        assert "enraya_1" in profile.unlocked and "enraya_win_1" in profile.unlocked
        assert (await client.enraya.get(GUILD_ID, OWNER_ID)).wins == 1
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert "enraya" not in report.by_game
    finally:
        await client.close()


def backgammon_cog_with_fake_drawing(client: BotClient):  # noqa: ANN201
    """El backgammon del bot real con dibujo falso, sin esperas y sin pintar por adelantado."""
    module = importer("Backgammon", client)
    module.REVEAL_MARGIN_SECONDS = 0
    cog = client.get_cog("Backgammon")
    cog.ahead = False
    cog.renderer = MagicMock()
    cog.renderer.still = AsyncMock(return_value=b"PNG")
    cog.renderer.animate = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    cog.renderer.close = AsyncMock()
    return cog, module


async def test_un_duelo_de_backgammon_apunta_logros_y_jugadas_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El backgammon carga antes que los logros y `apuestas`: sus puentes tienen que verlos."""
    client = await load_bot(tmp_path)
    try:
        cog, module = backgammon_cog_with_fake_drawing(client)
        owner = enraya_member(OWNER_ID, "Diego")
        rival = enraya_member(RIVAL_ID, "Ana")
        duel = await cog._backgammon_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            rival=rival,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        assert duel is not None
        duel.swap, duel.opening = False, (6, 1)
        await duel.accept(enraya_interaction(rival))
        # Las negras doblan y las blancas pasan: gana quien dobló.
        hand = duel.hand
        while not hand.done:
            step = hand.legal()[0]
            interaction = enraya_interaction(owner)
            interaction.data = {"values": [f"{step.source}:{step.target}"]}
            await duel.step(interaction, step.die)
        await duel.confirm(enraya_interaction(owner))
        await duel.double(enraya_interaction(rival))
        await duel.drop(enraya_interaction(owner))
        assert duel.phase is module.Phase.DONE

        for member in (owner, rival):
            profile = await client.achievements.profile(GUILD_ID, member.id)
            assert profile.stats["backgammon_games"] == 1
            assert profile.stats["backgammon_casino_duels"] == 1
            assert "bg_1" in profile.unlocked
            report = await client.casino_stats.report(
                GUILD_ID, member.id, since=None, today=date(2026, 10, 6)
            )
            assert report.by_game["backgammon"].plays == 1
        won = await client.achievements.profile(GUILD_ID, RIVAL_ID)
        lost = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert won.stats["backgammon_opp_drops"] == 1 and "bg_le_asustas" in won.unlocked
        assert won.stats["backgammon_doubles_offered"] == 1
        assert lost.stats["backgammon_drops"] == 1 and "backgammon_wins" not in lost.stats
        assert (await client.backgammon.get(GUILD_ID, RIVAL_ID)).wins == 1
    finally:
        await client.close()


async def test_una_partida_de_backgammon_contra_jovani_apunta_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Sin dinero de por medio: los logros y la ficha cuentan igual, `apuestas` no."""
    client = await load_bot(tmp_path)
    try:
        cog, module = backgammon_cog_with_fake_drawing(client)
        owner = enraya_member(OWNER_ID, "Diego")
        await cog._backgammon_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text=None,
            rival=None,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        (panel,) = cog.panels
        points = [0] * 24
        points[0], points[20] = 1, -14
        start = module.Position(tuple(points), (0, 0), (14, 1))
        panel.phase, panel.position, panel.origin = "play", start, start
        panel.hand = module.Hand(start, module.Side.WHITE, (1, 2))
        panel.hand.play(panel.hand.legal()[0])
        panel.rebuild()

        button = next(i for i in panel.children if i.custom_id == "backgammon:confirm")
        await button.callback(enraya_interaction(owner))

        assert panel.phase == "over"
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["backgammon_games"] == 1 and profile.stats["backgammon_wins"] == 1
        assert "bg_1" in profile.unlocked and "bg_win_1" in profile.unlocked
        assert (await client.backgammon.get(GUILD_ID, OWNER_ID)).wins == 1
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert "backgammon" not in report.by_game
    finally:
        await client.close()


async def test_la_torre_apunta_sus_logros_y_su_libro_con_el_bot_real(tmp_path: Path) -> None:
    """La Torre del dragón carga antes que los logros: sus partidas deben llegar a `logros`."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Torre")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._torre_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            difficulty=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (board,) = cog.boards
        board.game.dragons = tuple(frozenset({0}) for _ in board.game.dragons)
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await board._climb(interaction, 0)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["tower_games"] == 1
        assert profile.stats["tower_burns"] == 1
        assert {"tower_1", "tower_burn_1", "tower_burn_first", "tower_easy_burn"} <= set(
            profile.unlocked
        )
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["torre"].plays == 1
    finally:
        await client.close()


async def test_la_guerra_apunta_sus_logros_y_su_libro_con_el_bot_real(tmp_path: Path) -> None:
    """La Guerra carga antes que los logros: sus manos deben llegar a `logros` y al casino."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Guerra")
        cog.new_shoe = lambda: [Card(7, 0)] * 10 + [Card(1, 0), Card(13, 0)]
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._guerra_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["war_hands"] == 1
        # Rey contra as: la banca gana y salta el secreto del rey sin corona.
        assert {"war_1", "war_king_lost"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["guerra"].plays == 1
    finally:
        await client.close()


async def test_una_porra_cuenta_las_jugadas_y_apunta_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """La jugada de minas llega a la porra por `apuestas.record` y el reparto, a los logros."""
    import asyncio
    from types import SimpleNamespace

    from bot.services.porras import Status

    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Porras")

        async def never(_seconds: float) -> None:
            await asyncio.Event().wait()

        cog.sleep = never
        people = {}
        for user_id, name in ((10, "Luis"), (20, "Ana"), (30, "Pepe"), (40, "Mari")):
            user = MagicMock(spec=discord.Member)
            user.id, user.bot, user.display_name, user.mention = (
                user_id,
                False,
                name,
                f"<@{user_id}>",
            )
            people[user_id] = user
        cog._member = lambda _guild, user_id: people.get(user_id)
        message = MagicMock()
        message.id = 99
        message.edit = AsyncMock()
        message.channel.send = AsyncMock()
        porra = await cog.open(
            guild=SimpleNamespace(id=GUILD_ID), channel=None, opener=people[10],
            subject=people[20], game_text="minas", prop_key="signo", plays=1,
            stake_text="100", send=AsyncMock(return_value=message), send_error=AsyncMock(),
        )  # fmt: skip
        table = cog.tables[porra.id]

        def press(user_id: int) -> MagicMock:
            inter = fake_interaction()
            inter.user, inter.channel, inter.guild_id = people[user_id], None, GUILD_ID
            inter.guild = SimpleNamespace(id=GUILD_ID)
            return inter

        await cog.answer(press(20), table, accepted=True)
        await cog.bet(press(30), table, 0, "200")
        await cog.bet(press(40), table, 1, "200")
        await cog.lock(table)

        mines = importer("Minas", client)
        await mines.apuestas.record(
            client, GUILD_ID, people[20], game="minas", stake=100, net=150,
            balance_after=1_150, tax=0, details=(("boom", 0),),
        )  # fmt: skip
        # El reparto va en otra tarea (el juego no lo espera): se espera a que acabe.
        for _ in range(300):
            stats = (await client.achievements.profile(GUILD_ID, 20)).stats
            if porra.status is Status.RESOLVED and "porra_pool_max" in stats:
                break
            await asyncio.sleep(0.01)
        assert porra.status is Status.RESOLVED

        winner = await client.achievements.profile(GUILD_ID, 30)
        loser = await client.achievements.profile(GUILD_ID, 40)
        subject = await client.achievements.profile(GUILD_ID, 20)
        assert winner.stats.get("porra_wins") == 1
        assert loser.stats.get("porra_losses") == 1
        assert subject.stats.get("porra_image", 0) > 0
        assert "porra_image_1" in subject.unlocked
        report = await client.casino_stats.report(GUILD_ID, 30, since=None, today=date(2026, 10, 6))
        assert report.total.plays == 1
    finally:
        for task in list(client.get_cog("Porras")._tasks):
            task.cancel()
        await client.close()


async def test_la_beernight_apunta_sus_logros_y_saca_la_mascota_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`beernight` carga antes que los logros: confesar y cerrar deben llegar a ellos, y la
    mascota activa tiene que poder brindar en sus mensajes."""
    from bot.services.beernight import Settings
    from bot.services.pets import BOND_LEVELS, Event, Moment, PetState

    client = await load_bot(tmp_path)
    await client.beernight.initialize()
    await client.beernight.save_settings(GUILD_ID, Settings(sound=False))
    try:
        beernight = client.get_cog("Beernight")
        module = importer("Beernight", client)
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        owner.voice = None
        owner.guild_permissions = discord.Permissions.none()
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        guild.get_member = MagicMock(return_value=owner)
        guild.voice_client = None
        owner.guild = guild
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 5
        channel.send = AsyncMock(return_value=MagicMock(id=6, channel=channel))
        channel.get_partial_message = MagicMock(return_value=MagicMock(edit=AsyncMock()))
        client.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
        client.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]

        def click() -> MagicMock:
            """Una pulsación nueva: cada botón trae su propia interacción."""
            interaction = fake_interaction(owner)
            interaction.guild = guild
            interaction.channel = channel
            interaction.client = client
            interaction.message = MagicMock(id=7, channel=channel)
            return interaction

        await beernight.start(click())
        state = beernight.nights[GUILD_ID]
        state.task.cancel()
        mandate = state.active[0].mandate
        await beernight.confess(click(), state.night.id, mandate.key)
        await beernight.finish(state, interaction=click())

        # Los sorbos van por `note` (se escriben cada minuto): se fuerza la escritura.
        await client.get_cog("Achievements").flush()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["beer_sips"] == mandate.sips
        assert profile.stats["beer_nights"] == 1
        assert profile.stats["beer_hosted"] == 1
        assert {"beer_sip_1", "beer_night_1", "beer_host_1"} <= set(profile.unlocked)

        pets = client.get_cog("Mascotas")
        pets.active[(GUILD_ID, OWNER_ID)] = PetState(
            id=1, guild_id=GUILD_ID, user_id=OWNER_ID, species="perro", name="Toby",
            bond=BOND_LEVELS[4],
        )  # fmt: skip
        line = await module.mascotas.cameo(client, GUILD_ID, OWNER_ID, Moment(Event.BEER, True))
        assert line is not None and "**Toby**" in line
    finally:
        await client.close()


async def test_el_perfil_junta_todas_las_secciones_con_el_bot_real(tmp_path: Path) -> None:
    """`perfil` pide cada sección a su cog: con el bot real, ninguna sale «no disponible»."""
    client = await load_bot(tmp_path)
    await client.lottery.initialize()
    await client.pets.initialize()
    try:
        perfil = client.get_cog("Perfil")
        module = importer("Perfil", client)
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        owner.guild = guild
        guild.get_member = MagicMock(return_value=owner)
        guild.get_role = MagicMock(return_value=None)

        result = await perfil.open(guild=guild, author=owner, target=None, channel=None)

        assert not isinstance(result, str)
        summary, view = result
        assert "Diego" in summary.title
        for marker in ("📊", "🏰", "🪏", "🏆", "🔥", "🎒"):
            assert marker in summary.description, marker
        for key in module.SECTION_KEYS:
            embed = await perfil.section(view, key)
            assert "no está disponible" not in (embed.description or ""), key
        # Abrir el perfil propio cuenta para sus logros, en el cog de logros de verdad.
        profile = await client.get_cog("Achievements").fresh_profile(GUILD_ID, OWNER_ID)
        assert profile.stats["perfil_views"] == 1
        assert profile.stats["perfil_seen_resumen"] == 1
    finally:
        await client.close()


async def test_el_autobus_apunta_sus_logros_y_su_jugada_con_el_bot_real(tmp_path: Path) -> None:
    """El autobús carga antes que los logros: sus partidas llegan a `logros` y a `apuestas`."""
    client = await load_bot(tmp_path)
    try:
        autobus = importer("Autobús", client)
        autobus.REVEAL_MARGIN_SECONDS = 0
        media = autobus.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        cog = client.get_cog("Autobús")
        cog.renderer = MagicMock()
        cog.renderer.reveal = AsyncMock(return_value=autobus.Reveal(win=media, lose=media))
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._guagua_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (view,) = cog.views
        view.deck = (Card(2, 0),) * 5  # negra: pedir rojo falla
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._pick(interaction, autobus.Pick.RED)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["bus_games"] == 1
        assert profile.stats["bus_losses"] == 1
        assert {"bus_1", "busp_1"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["autobus"].plays == 1
    finally:
        await client.close()


async def test_el_poker_apunta_sus_logros_su_jugada_y_su_club_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El póker carga antes que los logros: sus manos llegan a `logros`, `apuestas` y al club."""
    client = await load_bot(tmp_path)
    try:
        poker = importer("Póker", client)
        poker.REVEAL_MARGIN_SECONDS = 0
        media = poker.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        cog = client.get_cog("Póker")
        cog.start = MagicMock()
        cog.sleep = AsyncMock()
        cog.renderer = MagicMock()
        cog.renderer.deal = AsyncMock(return_value=media)
        cog.renderer.showdown = AsyncMock(return_value=media)
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 5
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        table, _ = await cog.ensure_table(GUILD_ID, channel, OWNER_ID)
        await table.sit(owner)
        await table.deal()
        fold = fake_interaction()
        fold.user = owner
        fold.guild = None
        await table.decide(fold, False)
        await table.finish()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["poker_games"] == 1
        assert profile.stats["poker_folds"] == 1
        assert profile.stats["poker_rank"] == 1
        assert {"poker_1", "pokerr_1"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["poker"].plays == 1
        assert (await client.poker.player(GUILD_ID, OWNER_ID)).xp == 1
    finally:
        await client.close()


async def test_la_timba_apunta_logros_y_jugadas_de_los_dos_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """La timba carga antes que los logros: sus manos llegan a `logros` y a `apuestas`."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Timba")
        cog.start = MagicMock()
        cog.renderer = MagicMock()
        cog.renderer.timba = AsyncMock(return_value=b"PNG")
        players = []
        for user_id, name in ((OWNER_ID, "Diego"), (OWNER_ID + 1, "Ana")):
            user = MagicMock(spec=discord.Member)
            user.id = user_id
            user.display_name = name
            user.mention = f"<@{user_id}>"
            user.bot = False
            players.append(user)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 6
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        table, _ = await cog.ensure_table(GUILD_ID, channel, OWNER_ID, 20)

        async def fold_first(_seconds: float) -> None:
            hand = table.hand
            if hand is not None and hand.current is not None:
                interaction = fake_interaction()
                interaction.user = next(p for p in players if p.id == hand.current.user_id)
                interaction.guild = None
                await table.fold_click(interaction)

        cog.sleep = fold_first
        for user in players:
            await table.sit(user)
        await table.play_hand()

        for user in players:
            profile = await client.achievements.profile(GUILD_ID, user.id)
            assert profile.stats["timba_hands"] == 1
            assert "timba_1" in profile.unlocked
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["timba"].plays == 1
    finally:
        await client.close()


async def test_poker_en_modo_timba_llega_al_cog_de_la_timba_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`/poker timba` busca el cog `Timba` con `find_cog`: con el bot real tiene que encontrarlo."""
    client = await load_bot(tmp_path)
    try:
        timba = client.get_cog("Timba")
        timba.start = MagicMock()
        timba.renderer = MagicMock()
        timba.renderer.timba = AsyncMock(return_value=b"PNG")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 8
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        confirm, errors = AsyncMock(), AsyncMock()
        await client.get_cog("Póker").play_mode(
            "timba",
            guild=MagicMock(id=GUILD_ID),
            channel=channel,
            user=owner,
            amount_text="50",
            confirm=confirm,
            send_error=errors,
        )
        errors.assert_not_awaited()
        table = timba.tables[8]
        assert table.big_blind == 50 and [c.user.id for c in table.chairs] == [OWNER_ID]
    finally:
        await client.close()


async def test_las_mesas_con_fieltro_llevan_el_boton_de_tapete_antes_y_durante_la_partida(
    tmp_path: Path,
) -> None:
    """El 🎨 sale en moneda, dados y pollo también a mitad de partida, y en la ruleta."""
    import random

    from bot.services.chicken import DIFFICULTY_BY_KEY, ChickenGame
    from bot.services.coin import CoinGame
    from bot.services.craps import Bet, Status

    client = await load_bot(tmp_path)
    owner = MagicMock(id=OWNER_ID)

    def has_paint(view: discord.ui.View) -> bool:
        return any(
            isinstance(item, discord.ui.Button) and str(item.emoji) == "🎨"
            for item in view.children
        )

    try:
        coin = importer("Moneda", client).CoinView(
            client.get_cog("Moneda"), guild_id=GUILD_ID, owner=owner, stake=100
        )
        coin.rebuild()
        assert has_paint(coin), "moneda sin partida"
        coin.game = CoinGame.new(100, random.Random(1))
        coin.rebuild()
        assert has_paint(coin), "moneda a mitad de partida"

        dice = importer("Dados", client).CrapsView(
            client.get_cog("Dados"), guild_id=GUILD_ID, owner=owner, stake=100, bet=Bet.PASS
        )
        dice.rebuild()
        assert has_paint(dice), "dados sin punto"
        dice.game = MagicMock(status=Status.POINT, point=6, odds_room=0, stake=100, bet=Bet.PASS)
        dice.rebuild()
        assert has_paint(dice), "dados con el punto puesto"

        chicken = importer("Pollo", client).ChickenView(
            client.get_cog("Pollo"),
            guild_id=GUILD_ID,
            owner=owner,
            stake=100,
            difficulty=DIFFICULTY_BY_KEY["facil"],
            auto=None,
        )
        chicken.rebuild()
        assert has_paint(chicken), "pollo sin partida"
        chicken.game = ChickenGame(stake=100, difficulty=DIFFICULTY_BY_KEY["facil"], hit_lane=5)
        chicken.rebuild()
        assert has_paint(chicken), "pollo cruzando"

        # La ruleta tiene las 25 casillas llenas: el 🎨 va donde estaban las columnas.
        roulette = importer("Casino", client).RouletteTable(
            client.get_cog("Casino"), guild_id=GUILD_ID, owner=owner, stake=100
        )
        assert has_paint(roulette)
        assert len(roulette.children) <= 25
        labels = [getattr(item, "label", "") for item in roulette.children]
        assert not any("col" in (label or "") for label in labels)
    finally:
        await client.close()


async def test_saldo_patrimonio_y_hacienda_ven_los_pisos_con_el_bot_real(tmp_path: Path) -> None:
    """`pisos` carga después que el casino y el Patrimonio: sus puentes deben llegar."""
    client = await load_bot(tmp_path)
    try:
        pisos_cog = client.get_cog("Pisos")
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(30_000_000, "test")])
        trade = await pisos_cog.service.buy(GUILD, OWNER_ID, "vegueta_edificio", 100)

        fiscal = await importer("Casino", client).pisos.fiscal_of(client, GUILD, OWNER_ID)
        assert fiscal >= trade.deal.quote.price
        patrimonio = client.get_cog("Patrimonio")
        assert patrimonio.housing is client.housing
        worth, _rank, _people = await patrimonio.net_worth(GUILD, OWNER_ID)
        assert worth.homes > 0 and worth.homes_fiscal == fiscal
        assert importer("Pisos", client).logros._cog(client) is client.get_cog("Achievements")

        member = MagicMock(spec=discord.Member)
        member.id, member.bot, member.display_name = OWNER_ID, False, "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.get_member = MagicMock(return_value=member)
        responder = MagicMock()
        responder.guild, responder.member, responder.channel = guild, member, MagicMock()
        responder.send = AsyncMock()
        await client.get_cog("Casino")._hacienda_impl(responder)
        embed = responder.send.await_args.kwargs["embed"]
        housing = [f for f in embed.fields if f.name.startswith("🏠 Lo que pagan los pisos")]
        assert housing and "ITP" in housing[0].value
        assert len(embed) <= 6_000
    finally:
        await client.close()
