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

from bot.app import INITIAL_EXTENSIONS, BotClient
from bot.repositories.economy import LedgerEntry

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
    "Pollo",
    "Pachinko",
    "Caballos",
    "Loteria",
)


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "message_stats.sqlite3")
    await client.message_stats.initialize()
    await client.economy.repository.initialize()
    await client.casino_stats.initialize()
    await client.birthdays.initialize()
    await client.achievements.initialize()
    await client.welcome.initialize()
    await client.shop.initialize()
    await client.work.repository.initialize()
    await client.horses.initialize()
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


async def test_auto_de_la_tragaperras_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """El fallo de producción: diez tiradas jugadas y ninguna en los logros."""
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
        interaction = MagicMock()
        interaction.user = owner
        interaction.guild = None
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.followup.send = AsyncMock()

        await view._auto(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["slots_spins"] == view.session_spins == slots.AUTO_SPINS
        assert profile.stats["slots_auto"] == 1
        assert {"slots_1", "auto_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["tragaperras"].plays + plays.by_game["tragaperras"].free_plays == (
            slots.AUTO_SPINS
        )
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
        cog.renderer.hops.return_value = pollo.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        cog.renderer.board.return_value = b"PNG"
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
        interaction = MagicMock()
        interaction.user = owner
        interaction.guild = None
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.followup.send = AsyncMock()

        await view._cross(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["chicken_games"] == 1
        assert profile.stats["chicken_splats"] == 1
        assert {"pollo_1", "pollos_1", "pollo_ni_acera"} <= set(profile.unlocked)
    finally:
        await client.close()


async def test_las_carreras_apuntan_sus_logros_y_jugadas_con_el_bot_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los caballos cargan antes que los logros y `apuestas`: sus boletos deben llegar."""
    from bot.services.horses import SEGMENTS, RaceResult

    client = await load_bot(tmp_path)
    try:
        caballos = importer("Caballos", client)
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
        interaction = MagicMock()
        interaction.user = owner
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.followup.send = AsyncMock()

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
        ctx = MagicMock()
        ctx.guild = guild
        ctx.author = owner
        ctx.message.delete = AsyncMock()
        ctx.channel = MagicMock(spec=discord.TextChannel)
        ctx.channel.send = AsyncMock(return_value=MagicMock(id=5, channel=MagicMock(id=6)))

        await lista.lista_text.callback(lista, ctx, tarea="probar la lista prioridad alta")
        (task,) = await client.todo.list_tasks(GUILD_ID)
        interaction = MagicMock()
        interaction.guild = guild
        interaction.user = owner
        interaction.channel = ctx.channel
        interaction.response.edit_message = AsyncMock()
        await lista.complete_from_menu(interaction, [task.id])

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["todo_added"] == 1
        assert profile.stats["todo_done"] == 1
        assert {"todo_add_1", "todo_done_1"} <= set(profile.unlocked)
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
            fake = MagicMock()
            fake.user = owner
            fake.response.edit_message = AsyncMock()
            fake.response.is_done = MagicMock(return_value=False)
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
        interaction = MagicMock()
        interaction.user = owner
        interaction.channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.send = AsyncMock()
        interaction.response.send_message = AsyncMock()
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
