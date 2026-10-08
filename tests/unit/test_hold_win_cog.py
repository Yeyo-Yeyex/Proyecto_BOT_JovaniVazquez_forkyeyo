"""Pruebas de bot.cogs.hold_win: las máquinas de Botes con botones y su dinero.

Se usa la economía y el repositorio reales sobre un SQLite temporal (para
comprobar que el dinero y los maletines se mueven de verdad), tiradas trucadas
y un renderizador falso.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

import bot.cogs.hold_win as module
from bot.cogs.hold_win import (
    GIF_NAME,
    PNG_NAME,
    BasePlay,
    HoldWin,
    HoldWinView,
    base_text,
    bonus_text,
    parse_stake,
    paytable_embed,
)
from bot.repositories.economy import EconomyRepository
from bot.repositories.hold_win import HoldWinRepository
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService
from bot.services.hold_win import (
    CASE_SIZE,
    CELLS,
    MAJOR_BASE,
    MIN_STAKE,
    MINI_BASE,
    MINI_CHIP,
    MINI_MAX,
    ROWS,
    THEMES,
    BonusGame,
    BonusKind,
    Cell,
    Kind,
    Meters,
    Tier,
    Trigger,
    evaluate_base,
)
from bot.services.hold_win_render import Media

GUILD_ID = 1
OWNER_ID = 10
VOLCAN = THEMES["volcan"]


def coin(value: int, tier: Tier = Tier.GREEN) -> Cell:
    return Cell(Kind.COIN, tier=tier, value=value)


def grid(cells: dict[tuple[int, int], Cell]) -> list[Cell]:
    filler = Cell(Kind.CHIP, symbol="mini")
    return [cells.get(divmod(i, ROWS), filler) for i in range(CELLS)]


#: Recogedor en el rodillo 1 y monedas por ×0,5 + ×2: paga ×2,5 la apuesta.
COLLECT = evaluate_base(
    grid({(0, 0): Cell(Kind.COLLECT), (2, 1): coin(50), (3, 2): coin(200, Tier.RED)})
)
#: Nada de nada: ni monedas ni ways (solo fichas de bote).
NOTHING = evaluate_base(grid({}))
#: Una moneda azul sin recoger.
BLUE_COIN = evaluate_base(grid({(1, 1): coin(80, Tier.BLUE)}))


class FakeRenderer:
    """Devuelve bytes fijos: las pruebas no necesitan dibujar."""

    def render_base(self, theme, spin, *, stake, panel, banner=None, turbo=False, rng=None):  # noqa: ANN001, ANN201
        return Media(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    def base_still(self, theme, spin, *, stake, panel, highlight=False, banner=None):  # noqa: ANN001, ANN201
        return b"STILL"

    def bonus_still(self, theme, board, *, stake, panel, banner=None):  # noqa: ANN001, ANN201
        return b"BONUS"

    def render_bonus_step(self, theme, before, step, **kwargs):  # noqa: ANN001, ANN003, ANN201
        return Media(gif=b"" if kwargs.get("turbo") else b"GIF", png=b"PNG", seconds=0.0)


class NeverLands(random.Random):
    """Azar en el que nunca cae nada en el bonus y no hay gran bonus por sorpresa."""

    def random(self) -> float:
        return 0.999

    def randrange(self, start, stop=None, step=1):  # noqa: ANN001, ANN201
        return start - 1 if stop is None else start


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las animaciones y pausas no hace falta esperarlas."""
    monkeypatch.setattr(module, "REVEAL_MARGIN_SECONDS", 0)
    monkeypatch.setattr(module, "BONUS_AUTO_PAUSE", 0)
    monkeypatch.setattr(module, "BONUS_INTRO_PAUSE", 0)


def rig(monkeypatch: pytest.MonkeyPatch, *spins) -> None:  # noqa: ANN002
    """Las siguientes tiradas salen en este orden; después, siempre `NOTHING`."""
    queue = list(spins)
    monkeypatch.setattr(module, "spin_base", lambda _rng: queue.pop(0) if queue else NOTHING)


async def make_cog(tmp_path: Path, rng: random.Random | None = None) -> HoldWin:
    database = tmp_path / "bot.db"
    economy_repository = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    repository = HoldWinRepository(database)
    await repository.initialize()
    return HoldWin(
        MagicMock(),
        economy=EconomyService(economy_repository),
        repository=repository,
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        rng=rng or NeverLands(),
    )


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


def make_view(cog: HoldWin, stake: int = 100) -> HoldWinView:
    return HoldWinView(cog, theme=VOLCAN, guild_id=GUILD_ID, owner=make_user(), stake=stake)


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


def names(call) -> list[str]:  # noqa: ANN001
    return [file.filename for file in call.kwargs["attachments"]]


# -- Presentación -------------------------------------------------------------------


def test_apuesta_por_defecto_minimo_y_formatos() -> None:
    assert parse_stake(None, 1_000) == 100
    assert parse_stake(None, 40) == 40
    assert parse_stake("2k", 5_000) == 2_000
    with pytest.raises(ValueError, match="mínima"):
        parse_stake(str(MIN_STAKE - 1), 1_000)


def test_la_tabla_explica_ways_monedas_maletines_y_botes() -> None:
    text = paytable_embed(VOLCAN).description or ""
    for word in ("Ways", "Monedas", "Maletines", "Bonus", "GRAND", "🌋"):
        assert word in text


def test_el_texto_de_la_recogida_dice_cuanto_y_con_que() -> None:
    play = BasePlay(
        spin=COLLECT,
        stake=100,
        payout=250,
        settlement=MagicMock(balance=1_150),
        meters=Meters(),
        trigger=None,
        bonus=None,
        media=Media(b"", b"", 0.0),
        session_spins=1,
    )
    text = base_text(VOLCAN, play, random.Random(0))
    assert "+150 Y$" in text.splitlines()[0]
    assert "2 monedas: +250 Y$" in text


def test_el_texto_del_bonus_cuenta_lo_que_falta_para_el_mini() -> None:
    game = BonusGame(kind=BonusKind.RED, stake=100, mini=MINI_BASE, major=MAJOR_BASE)
    game.board[0] = coin(200, Tier.RED)
    text = bonus_text(VOLCAN, game, None)
    assert "Bonus Lava" in text
    assert "9 para el MINI" in text


# -- Juego base ---------------------------------------------------------------------


async def test_tirar_cobra_gira_y_paga_la_recogida(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    interaction = make_interaction()

    await view._spin(interaction)

    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance == STARTING_BALANCE - 100 + 250
    assert ledger_sum(tmp_path, OWNER_ID) == balance
    interaction.response.defer.assert_awaited_once()
    first, final = interaction.edit_original_response.await_args_list
    assert names(first) == [GIF_NAME]
    assert names(final) == [PNG_NAME]


async def test_las_monedas_entran_en_el_maletin_y_se_guardan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    await make_view(cog, stake=40)._spin(make_interaction())

    meters = await cog.meters(GUILD_ID, OWNER_ID, "volcan")
    assert meters.cases[Tier.GREEN].coins == 1
    assert meters.cases[Tier.RED].coins == 1
    assert meters.cases[Tier.RED].stake_sum == 40
    # Las fichas de relleno de la rejilla también suben el MINI.
    assert meters.mini == MINI_BASE + MINI_CHIP * COLLECT.chips["mini"]
    # Cada máquina guarda lo suyo.
    assert (await cog.meters(GUILD_ID, OWNER_ID, "otra")).cases[Tier.RED].coins == 0


async def test_en_turbo_solo_se_edita_una_vez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch)
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.turbo = True
    interaction = make_interaction()

    await view._spin(interaction)

    assert names(interaction.edit_original_response.await_args) == [PNG_NAME]
    interaction.edit_original_response.assert_awaited_once()


async def test_sin_saldo_no_gira_ni_toca_los_maletines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=STARTING_BALANCE + 1)
    interaction = make_interaction()

    await view._spin(interaction)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True

    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    assert (await cog.meters(GUILD_ID, OWNER_ID, "volcan")).cases[Tier.RED].coins == 0


async def test_solo_juega_el_dueno(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    interaction = make_interaction(user_id=99)
    assert not await make_view(cog).interaction_check(interaction)
    assert "volcan" in interaction.response.send_message.await_args.args[0]


async def test_auto_juega_diez_y_un_solo_resumen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch)
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    interaction = make_interaction()

    await view._auto(interaction)

    assert view.session_spins == module.AUTO_SPINS
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    assert "10 tiradas" in interaction.edit_original_response.await_args.kwargs["embed"].description


# -- Bonus --------------------------------------------------------------------------


async def fill_blue(cog: HoldWin) -> None:
    meters = Meters()
    meters.cases[Tier.BLUE].add(CASE_SIZE[Tier.BLUE] - 1, 100)
    await cog.repository.save(GUILD_ID, OWNER_ID, "volcan", meters)


async def test_llenar_un_maletin_abre_el_bonus_sin_cobrar_nada_mas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, BLUE_COIN)
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    view = make_view(cog)
    interaction = make_interaction()

    await view._spin(interaction)

    assert view.bonus is not None
    assert view.bonus.kind == BonusKind.BLUE
    assert view.bonus.stake == 100
    assert view.spin_button.label.startswith("🎰 Girar")
    assert view.half_button.disabled
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    assert (await cog.meters(GUILD_ID, OWNER_ID, "volcan")).cases[Tier.BLUE].coins == 0
    # La entrada al bonus se enseña con su propia imagen.
    assert names(interaction.edit_original_response.await_args) == [PNG_NAME]


async def test_el_bonus_se_paga_al_acabar_y_cuadra_con_hacienda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, BLUE_COIN)
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    view = make_view(cog)
    await view._spin(make_interaction())
    assert view.bonus is not None
    expected = view.bonus.coin_points * 100 // 100

    for _ in range(3):
        await view._spin(make_interaction())

    assert view.bonus is None
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    assert balance + state == STARTING_BALANCE - 100 + expected
    assert ledger_sum(tmp_path, OWNER_ID) == balance
    assert view.spin_button.label.startswith("🎰 Tirar")


async def test_auto_del_bonus_lo_juega_entero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.GREEN, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )
    coins = view.bonus.coin_points

    await view._auto(make_interaction())

    assert view.bonus is None
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance + ledger_sum(tmp_path, STATE_ACCOUNT_ID) == STARTING_BALANCE + coins


async def test_ganar_un_bote_lo_devuelve_a_su_base(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.repository.save(GUILD_ID, OWNER_ID, "volcan", Meters(mini=MINI_MAX))
    game = BonusGame(kind=BonusKind.GREEN, stake=100, mini=MINI_MAX, major=MAJOR_BASE)
    game.board = [coin(10) for _ in range(10)] + [None] * 10
    game.respins_left = 0

    payout = await cog.pay_bonus(GUILD_ID, OWNER_ID, VOLCAN, game)

    assert payout.result.jackpots == ("mini",)
    assert payout.amount == 100 + MINI_MAX
    assert (await cog.meters(GUILD_ID, OWNER_ID, "volcan")).mini == MINI_BASE


async def test_cerrar_la_maquina_con_bonus_a_medias_lo_juega_y_lo_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.RED, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )
    coins = view.bonus.coin_points
    view.message = MagicMock()
    view.message.edit = AsyncMock()

    await view.on_timeout()

    assert view.bonus is None
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance + ledger_sum(tmp_path, STATE_ACCOUNT_ID) == STARTING_BALANCE + coins
    assert "cerrar" in view.message.edit.await_args.kwargs["embed"].description


async def test_al_apagar_se_pagan_los_bonus_pendientes(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.GREEN, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )
    cog.machines.add(view)

    await cog.cog_unload()

    assert view.bonus is None
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) > STARTING_BALANCE - 1
    assert not cog.machines


async def test_salir_del_servidor_borra_los_maletines(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    await cog.on_guild_remove(guild)
    assert (await cog.meters(GUILD_ID, OWNER_ID, "volcan")).cases[Tier.BLUE].coins == 0


# -- Comandos -----------------------------------------------------------------------


async def test_abrir_la_maquina_manda_imagen_y_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    ctx = MagicMock()
    ctx.guild = MagicMock(id=GUILD_ID)
    ctx.author = make_user()
    ctx.channel = MagicMock(id=5)
    ctx.send = AsyncMock(return_value=MagicMock())

    await cog.volcan_text.callback(cog, ctx, None)

    kwargs = ctx.send.await_args.kwargs
    assert kwargs["file"].filename == PNG_NAME
    assert isinstance(kwargs["view"], HoldWinView)
    assert kwargs["view"].theme.key == "volcan"
    assert len(cog.machines) == 1


async def test_fuera_del_canal_de_casino_no_se_abre(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({555})
    ctx = MagicMock()
    ctx.guild = MagicMock(id=GUILD_ID)
    ctx.author = make_user()
    ctx.channel = MagicMock(id=5)
    ctx.send = AsyncMock()
    ctx.reply = AsyncMock()

    await cog.volcan_text.callback(cog, ctx, None)

    assert not cog.machines
